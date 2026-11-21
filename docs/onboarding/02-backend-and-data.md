# 02 — Backend và dữ liệu: lần theo một booking

[← Chương 01](01-repository-and-mental-model.md) · [Chương 03: Frontend và Auth →](03-frontend-and-auth.md)

Chương này lần theo một booking đến PDF/email và chỉ ra nơi được quyền quyết định dữ liệu. Các con số mô tả source hiện tại; dev chưa chứng minh khả năng phục vụ 50.000 người dùng.

## 1. Bốn process, ba nơi lưu dữ liệu

| Thành phần | Trách nhiệm | Dữ liệu quyết định |
| --- | --- | --- |
| Catalog — FastAPI | Liệt kê metadata, ghép tồn kho để hiển thị | MongoDB giữ metadata; Redis giữ bản đọc nhanh |
| Booking — FastAPI | Nhận yêu cầu, giữ số vé, tạo booking/outbox | MySQL quyết định còn vé và booking có tồn tại |
| Relay — background process | Đọc outbox đã commit, cập nhật Redis, gửi SQS | MySQL giữ tiến độ publication |
| Worker — background process | Nhận SQS, tạo PDF, upload S3, gửi SES | MySQL giữ trạng thái ticket/email; S3 giữ file |

Chỉ MySQL được trừ vé. Không sửa tồn kho Redis hoặc đánh dấu email `SENT` để che lỗi trên UI.

Hai API có HTTP port `8000`; Relay và Worker không có endpoint để gọi. Relay tự polling MySQL; Worker tự long polling SQS ngay sau khi process chạy.

## 2. Thứ tự đọc source

1. [001_schema.sql](../../database/001_schema.sql): ba bảng và các constraint.
2. [shared/database.py](../../shared/database.py): engine, transaction isolation, pool và TLS.
3. [Booking main.py](../../services/booking/app/main.py): `BookingInput` → `create_booking()` → `replay()` → `get_booking()`/`get_ticket()`.
4. [Relay main.py](../../services/relay/app/main.py): `claim_event()` → `publish()` → `mark_published()`; sau đó đọc `schedule_retry()`.
5. [shared/inventory.py](../../shared/inventory.py) và [Catalog main.py](../../services/catalog/app/main.py): bản projection được ghi và đọc như thế nào.
6. [Worker main.py](../../services/worker/app/main.py): `claim_booking()` → `Heartbeat` → `process_message()`; rồi [ticket.py](../../services/worker/app/ticket.py).
7. [shared/aws.py](../../shared/aws.py), [shared/errors.py](../../shared/errors.py): AWS clients và cách đọc log an toàn.

`main()` quản lý vòng đời background process; claim/publish/process xử lý từng công việc.

Các `requirements.txt` thuộc từng service. Dockerfiles build hai stage Python, copy `shared/` và app riêng vào image, chạy non-root UID `10001`. API dùng một Uvicorn worker; Relay/Worker dùng `python -m app.main`. Vì mỗi image có package `app` riêng, cùng tên `main.py` không gây xung đột. `__init__.py` đánh dấu package, không chứa nghiệp vụ. Helper được Dockerfile COPY có thể phục vụ local; đừng xóa chỉ vì AWS không gọi nó.

## 3. Booking: giữ đúng vé trong một transaction

Ví dụ demo dùng event UUID `11111111-1111-4111-8111-111111111111`, được migration tạo với `total=100`, `remaining=100`, `version=0`. Catalog seed dùng cùng ID để nối metadata với inventory.

Request tạo booking có access token, header `Idempotency-Key` và body:

```json
{
  "event_id": "11111111-1111-4111-8111-111111111111",
  "quantity": 2,
  "email": "buyer@example.com"
}
```

Khi test SES sandbox, thay email minh họa bằng địa chỉ đã verified. `BookingInput` kiểm tra UUID/email, integer quantity 1–5, cấm field thừa. `Buyer` yêu cầu scope `bookings:write`; `user_id` lấy từ token.

`with engine.begin()` mở transaction; ra khỏi block bình thường thì commit, có exception thì rollback. Trong block đó code thực hiện:

1. Tìm booking theo `(user_id, idempotency_key)` để trả lại yêu cầu đã xử lý.
2. Chạy statement tham số hóa của SQLAlchemy:

```sql
UPDATE inventory
SET remaining = remaining - :quantity,
    version = version + 1
WHERE event_id = :event_id
  AND remaining >= :quantity
```

3. Khi `rowcount == 1`, đọc `remaining/version`, insert booking `PENDING` và outbox `BookingCreated`.
4. Đọc booking để tạo response; chỉ trả HTTP `202` sau khi transaction đã commit.

Giả sử còn 1 vé, A/B cùng mua 1: InnoDB khóa row khi update. Một transaction trừ xuống 0; bên kia chờ rồi kiểm tra điều kiện trên dữ liệu mới, không update được và nhận `409`. Hai request không thể cùng thắng từ một lần đọc cũ.

Insert booking/outbox lỗi thì lần trừ vé cũng rollback. Đưa outbox ra sau commit hoặc gửi SQS trực tiếp trong API sẽ tạo hai lần ghi độc lập: database thành công nhưng message có thể mất.

### Idempotency bảo vệ lần thử lại

Client giữ nguyên key khi chưa biết request trước thành công hay chưa. Backend tạo SHA-256 từ body đã validate, JSON sắp xếp key với separators cố định; gọi là `request_hash`.

- Cùng user, cùng key, cùng hash: trả booking cũ.
- Cùng user/key nhưng body khác: `409`, không âm thầm dùng key cho đơn khác.
- Hai request chạy đồng thời chưa thấy booking cũ: unique constraint `(user_id, idempotency_key)` chọn một bản ghi. Bên thua rollback toàn bộ transaction rồi đọc lại booking để replay.

Key có 1–128 ký tự ASCII hiển thị, nhận diện lần yêu cầu của client. Booking ID là UUID của đơn đã lưu; không nhập `1` vào path cần UUID.

Deadlock/lock timeout `1213`/`1205` được thử tối đa ba attempts, có jitter. Lỗi database còn lại trả `503`, `Retry-After: 2`; client phải giữ key cũ khi retry.

## 4. Schema giữ những invariant nào?

| Bảng | Cột cần hiểu | Cơ chế bảo vệ |
| --- | --- | --- |
| `inventory` | `event_id`, `total`, `remaining`, `version` | PK event, unsigned, `remaining <= total` |
| `bookings` | owner, email, quantity, idempotency/hash, trạng thái, ticket key, lease | FK inventory, unique idempotency, quantity 1–5, status constraint |
| `outbox` | aggregate ID, JSON payload, attempts, available/lease/published timestamps | FK booking, unique `(aggregate_id, event_type)` |

Với request mua 2 vé ở trên, sau commit có `remaining=98`, `version=1`, một booking `PENDING`, một outbox chưa published. Payload chứa booking/event ID và **ảnh chụp** `remaining=98/version=1`. Nó không mang email; Worker đọc email thực tế từ booking trong MySQL.

[smoke_transaction.sql](../../database/smoke_transaction.sql) minh họa rollback, không chứng minh chịu tải. Không sửa migration `001` đã áp dụng: bootstrap kiểm tra checksum. Thêm migration số tiếp theo; xem [MySQL bootstrap](../mysql-bootstrap.md).

## 5. Relay: claim ngắn, network I/O ở ngoài transaction

`claim_event()` chọn một row đủ điều kiện: chưa published, đã tới `available_at`, lease trống hoặc hết hạn. `FOR UPDATE SKIP LOCKED` cho các Relay khác bỏ qua row đang bị khóa để lấy công việc khác. Trong transaction ngắn, Relay gán owner UUID, lease mặc định 120 giây và tăng attempts.

Context manager commit trước khi caller nhận object `Event`; `publish()` không giữ row lock/connection suốt network I/O.

Thứ tự `publish()` hiện tại:

```text
renew lease → project Redis → renew lease → SendMessage SQS → mark_published
```

`renew_lease()`/`mark_published()` yêu cầu đúng owner, lease chưa hết hạn. `schedule_retry()` lùi `available_at` theo exponential backoff tối đa 60 giây cộng jitter, bỏ lease của owner đó; main cũng backoff khi lỗi.

Relay không có heartbeat thread như Worker; nó renew ở các điểm trên. Redis lỗi hiện chặn việc gửi SQS của event đó. Đây là dependency thật của implementation, cần cân nhắc trước khi đổi thứ tự.

SQS nhận message nhưng Relay chết trước `mark_published()` thì event sẽ gửi lại. Outbox lưu bền ý định publish cùng booking; publication vẫn có thể trùng.

## 6. Redis: hai loại dữ liệu, hai cách hết hạn

Metadata key `catalog:events:v1` có TTL 60–75 giây. Cache miss/lỗi/JSON sai thì đọc tối đa 100 event `PUBLISHED` từ MongoDB rồi cache lại. TTL có jitter; chưa có distributed lock chống cache stampede.

Tồn kho nằm ở hash `inventory:<event-id>` với fields `remaining/version`, không đặt TTL. Relay và [reconcile_inventory.py](../../scripts/reconcile_inventory.py) gọi cùng Lua script:

```lua
local current = redis.call('HGET', KEYS[1], 'version')
local incoming = ARGV[2]

if not current
   or #incoming > #current
   or (#incoming == #current and incoming > current)
then
    redis.call(
        'HSET', KEYS[1],
        'remaining', ARGV[1],
        'version', incoming
    )
    return 1
end

return 0
```

Lua chạy atomic: version 2 tới trước 1 thì bỏ 1; cùng version cũng bỏ qua. So sánh chuỗi số theo độ dài rồi thứ tự giúp giữ chính xác BIGINT, tránh floating point Lua.

Catalog pipeline đọc hash rồi ghép metadata. Redis lỗi/mất projection trả `remaining=null`; MySQL vẫn quyết định đặt vé. Reconcile dựng lại từ MySQL khi Redis mất dữ liệu; outbox đã published không tự phát lại.

## 7. Worker: lease database và visibility SQS là hai việc khác nhau

Worker gọi `ReceiveMessage` với long polling 20 giây, tối đa một message mỗi lần. Nhận message chỉ làm nó tạm ẩn, chưa xóa khỏi queue. Standard SQS có thể giao trùng và không bảo đảm thứ tự.

`claim_booking()` conditional update lấy lease. Đã có `ISSUED`, `SENT` và ticket key thì trả `None`, main xóa message trùng. Lease đang thuộc process khác thì để message thử lại.

`Heartbeat` có thread riêng, interval `min(LEASE_SECONDS, SQS_VISIBILITY_SECONDS)/3`: mặc định `120/3=40` giây. `renew()` kéo dài lease MySQL và `ChangeMessageVisibility`; `check()` kiểm tra mất quyền rồi renew DB trước các bước quan trọng. Interval phải có khoảng đệm trước expiry.

Luồng `process_message()`:

1. Kiểm tra `BookingCreated`, parse booking UUID, claim booking.
2. Nếu chưa có ticket: tạo PDF, upload `tickets/<booking-id>.pdf` vào S3 với SSE-S3, lưu `ticket_key` và `status=ISSUED`.
3. Nếu email chưa `SENT`: tạo presigned URL 900 giây, gửi SES đến **booking.email**, ghi log acceptance MessageId, rồi update `email_status=SENT`.
4. Nhả lease; main chỉ `DeleteMessage` khi process thành công. Exception giữ message để retry; redrive policy đưa lỗi lặp lại tới DLQ, dev cấu hình max receive count 5.

| Trạng thái đọc từ MySQL | Kết luận đúng |
| --- | --- |
| `PENDING / PENDING` | Chưa ghi nhận ticket đã phát hành |
| `ISSUED / PENDING` | Đã lưu ticket key; bước gửi/ghi nhận email chưa hoàn tất |
| `ISSUED / SENT` | SES đã nhận yêu cầu gửi; chưa chứng minh email vào Inbox |

SES nhận mail nhưng process chết trước khi lưu `SENT` thì retry có thể gửi trùng. S3 key cố định/trạng thái DB không đóng được cửa sổ này. Không sửa `SENT` thủ công hoặc bulk replay DLQ chưa xác định message.

`ticket.py` tạo QR chứa booking/event ID, quantity, HMAC-SHA256 bằng `TICKET_SIGNING_SECRET`. ReportLab `invariant=1` giúp PDF cùng input ổn định. Chữ ký chống sửa payload; repo chưa có check-in/verifier. Đổi secret ảnh hưởng việc xác minh vé cũ sau này.

## 8. API contract và auth boundary

| API trực tiếp trong service | Auth | Kết quả chính |
| --- | --- | --- |
| `GET /events` | Public | List metadata + inventory projection |
| `POST /events` | `events:write` | `201`, tạo `DRAFT`; chưa publish/tạo inventory |
| `POST /bookings` | `bookings:write` | `202`, trả booking có ID/email/trạng thái |
| `GET /bookings/{id}` | `bookings:write` + owner | Trạng thái hiện tại, không phụ thuộc frontend cache |
| `GET /bookings/{id}/ticket` | `bookings:write` + owner | Presigned URL 120 giây nếu `ISSUED` |
| `GET /healthz`, `/readyz` | Không token | Liveness và readiness |

HTTP API không expose `POST /events`; Cognito client không được cấp scope đó. Seed chưa thay thế luồng quản trị hoàn chỉnh. Xem [shared/auth.py](../../shared/auth.py) và [chương 03](03-frontend-and-auth.md) về scope/JWT Adapter.

`401`: thiếu/token không hợp lệ; `403`: thiếu scope; `404`: booking không tồn tại **hoặc không thuộc user**; `409`: hết vé/event không có inventory, key dùng với body khác, hoặc ticket chưa sẵn sàng; `422`: request/header/path sai kiểu; `503`: dependency chưa dùng được. Readiness Booking kiểm tra table inventory; Catalog cần Mongo ping thành công nhưng chấp nhận Redis degraded. Vì thế “ready” không có nghĩa email/PDF toàn hệ thống đã được test.

## 9. Pool, thread và timeout: scale app cũng scale connection

`shared/database.py` dùng `READ COMMITTED`, `max_overflow=0`, `pool_timeout=3`, recycle 900 giây và pre-ping. SQL connect timeout 3 giây; read/write timeout 10 giây; session dùng UTC và lock wait timeout 3 giây.

Pool tối đa mỗi process: Booking 5, Relay 2, Worker 2. Một task/process mỗi service có trần **9 SQL connections**, mở theo nhu cầu; chưa tính bootstrap/reconcile/admin. Rolling deployment thêm task hoặc thêm Uvicorn worker sẽ nhân ngân sách pool.

FastAPI routes `def` chạy SQL/PyMongo đồng bộ trong thread pool; hết DB pool thì chờ rồi lỗi. Đổi sang `async def` nhưng giữ blocking SQL sẽ chặn event loop. Catalog Mongo pool tối đa 20 riêng.

`MYSQL_SSL_CA` bật SSLContext kiểm tra CA/hostname, minimum TLS 1.2. AWS inject đường dẫn CA trong SQL service Dockerfiles; local có thể bỏ biến. Đừng tắt verify để sửa certificate. AWS client connect/read timeout 3/30 giây, tối đa ba attempts; tổng operation có thể lâu hơn một lần timeout.

## 10. Khi cần bảo trì, bắt đầu sửa ở đâu?

| Thay đổi | File/hàm đầu tiên | Bằng chứng cần có |
| --- | --- | --- |
| Request/response booking | `BookingInput`, `BookingView`, `view()`, BY_KEY/BY_ID | Replay vẫn trả cùng contract; owner khác không đọc được |
| Inventory/schema | Migration mới + `create_booking()` | MySQL concurrent test: không âm vé, không duplicate key/outbox, rollback không trừ vé |
| Message/projection | Relay `publish()`, Worker `process_message()`, Lua | Duplicate/out-of-order không làm tồn kho quay lùi |
| PDF/email | `ticket.py`, Worker | Retry không tạo đơn mới; S3 key ổn định; phân biệt SES accepted/delivered |
| Pool/TLS/dependencies | Shared engine + service Dockerfiles/requirements | Tính lại connections khi scale; rebuild đúng image; kiểm chứng trên môi trường test |

[test_error_details.py](../../tests/test_error_details.py) bảo vệ log; [test_catalog_metadata.py](../../tests/test_catalog_metadata.py) kiểm tra metadata lỗi; [test_mysql_bootstrap.py](../../tests/test_mysql_bootstrap.py) bảo vệ migration/grants. Chúng không thay thế MySQL concurrency/load test; mock không chứng minh chống overselling.

Khi debug, đi theo IDs: booking → outbox aggregate → log `Published outbox_id` → message/Worker → `Email accepted` và `Ticket completed`. `failure_details()` giữ operation/error code/request ID/HTTP status, cố ý bỏ raw AWS message có thể chứa email hoặc URL ký sẵn. Đọc trạng thái mới từ API trước khi suy luận từ UI cũ. Quy trình vận hành nằm ở [AWS dev power](../AWS-DEV-POWER.md); local integration ở [Local development](../LOCAL-DEVELOPMENT.md).

[← Chương 01](01-repository-and-mental-model.md) · [Tiếp: Chương 03 — Frontend và Auth](03-frontend-and-auth.md)
