# 06. Troubleshooting và recovery

[← 05. Docker và vận hành](05-docker-and-operations.md) · [07. Thực hành bảo trì →](07-maintenance-practice.md)

Mục tiêu: tìm đúng tầng gây lỗi trước khi sửa. Một API trả `202` chưa chứng minh vé đã phát hành; ECS `RUNNING` chưa chứng minh email đã đến inbox. Ghi lại thời điểm UTC, HTTP status, booking UUID, outbox ID, SQS message ID và task ID liên quan. Không gửi access token, password hoặc presigned URL vào log/chat.

## Chuẩn bị bộ lệnh đọc trạng thái

Chạy từ root repository. Các lệnh sau đọc state/AWS; không apply, destroy hoặc tạo booking. Những request đọc vẫn có thể phát sinh phí theo dịch vụ.

```bash
aws_ft() {
  aws --profile flash-ticket-terraform --region us-east-1 --no-cli-pager "$@"
}

python3 scripts/deploy_dev.py status

TASK_API_URL="$(terraform -chdir=infra/environments/dev output -raw api_url)"
TASK_QUEUE_URL="$(terraform -chdir=infra/environments/dev-foundation output -raw booking_queue_url)"
TASK_DLQ_URL="$(terraform -chdir=infra/environments/dev-foundation output -raw booking_dlq_url)"
TASK_QUEUE_ARN="$(terraform -chdir=infra/environments/dev-foundation output -raw booking_queue_arn)"
TASK_DLQ_ARN="$(terraform -chdir=infra/environments/dev-foundation output -raw booking_dlq_arn)"
```

Nếu outputs chưa tồn tại sau destroy, dừng ở đó; dùng [runbook bật/tắt](../AWS-DEV-POWER.md) để chọn dựng lại. Không xem lỗi thiếu state là lý do chạy destroy tiếp.

| Điểm kiểm tra | Câu hỏi cần trả lời |
|---|---|
| Browser → API Gateway | Network tab gọi đúng URL/method? Lỗi CORS, timeout hay HTTP response? |
| Cognito → Booking | Có access token, đúng issuer/client/scope và chưa hết hạn? |
| Booking → MySQL | Booking được commit chưa? Inventory và outbox có cùng transaction? |
| Relay → Redis/SQS | Có `Published outbox_id=...`? Projection chỉ chậm hay Redis không truy cập được? |
| SQS → Worker | Jobs visible, in-flight hay đã vào DLQ? Worker có đang chạy? |
| Worker → S3/SES | PDF tồn tại? SES chấp nhận email hay request bị từ chối? |

Log groups hiện tại:

```bash
aws_ft logs tail /apigateway/flash-ticket-dev --since 15m --format short
aws_ft logs tail /ecs/flash-ticket-dev/catalog --since 15m --format short
aws_ft logs tail /ecs/flash-ticket-dev/booking --since 15m --format short
aws_ft logs tail /ecs/flash-ticket-dev/relay --since 15m --format short
aws_ft logs tail /ecs/flash-ticket-dev/worker --since 15m --format short
aws_ft logs tail /ecs/flash-ticket-dev/mysql-bootstrap --since 15m --format short
```

Không có HTTP port cho Relay/Worker. Xem log và ECS task events, không thử `curl` vào hai workers. `/healthz` và `/readyz` thuộc FastAPI/container checks; HTTP API hiện không công khai các routes đó.

## 1. `422 uuid_parsing`, rồi `KeyError: 'url'` và malformed URL

### (1) Nguyên nhân gốc rễ

`GET /bookings/{booking_id}` yêu cầu UUID. Số `1` không phải booking ID. Lỗi tiếp theo xuất hiện vì script cố lấy `url` từ JSON báo lỗi của API. Endpoint ticket cũng có thể trả `409` khi vé chưa sẵn sàng.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Dùng UUID trong response đặt vé hoặc trên giao diện. Nhập access token mới vào terminal riêng của bạn:

```bash
read -r -s -p "Access token: " TOKEN
printf '\n'
read -r -p "Booking UUID: " BOOKING_ID

if python3 -c 'import sys; from uuid import UUID; UUID(sys.argv[1])' "$BOOKING_ID"; then
  curl --fail-with-body --silent --show-error \
    "$TASK_API_URL/bookings/$BOOKING_ID" \
    -H "Authorization: Bearer $TOKEN"
fi
```

Chỉ tải file khi endpoint ticket thành công. Đây là ghi file local, không tạo tài nguyên AWS:

```bash
if TASK_TICKET_RESPONSE="$(
  curl --fail-with-body --silent --show-error \
    "$TASK_API_URL/bookings/$BOOKING_ID/ticket" \
    -H "Authorization: Bearer $TOKEN"
)"; then
  TASK_DOWNLOAD_URL="$(printf '%s' "$TASK_TICKET_RESPONSE" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])')" && {
    mkdir -p output
    curl --fail --silent --show-error "$TASK_DOWNLOAD_URL" --output output/ticket.pdf
  }
fi
```

### (3) Lệnh CLI kiểm tra kết quả

```bash
file output/ticket.pdf
```

Booking API cấp link hết hạn sau 120 giây; nếu quá hạn, xin link mới từ endpoint ticket. Không đổi S3 bucket thành public.

## 2. API root `404`, frontend mở được nhưng API không hoạt động

### (1) Nguyên nhân gốc rễ

API không khai báo `/`; kiểm tra bằng `/events`. CloudFront còn hoạt động sau `down stop`, trong khi bốn ECS services đã có desired count 0. `404`, `503` và timeout là các dấu hiệu khác nhau, cần đọc response và ECS events trước khi kết luận.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Đọc trước:

```bash
curl --include --silent --show-error "$TASK_API_URL/events"
python3 scripts/deploy_dev.py status
```

Nếu bạn chủ động muốn bật lại môi trường, chạy **lệnh thay đổi AWS, có thể dùng credits**:

```bash
bash scripts/aws-dev-up.sh
```

Nếu ECS đang chạy nhưng Catalog không healthy, xem Catalog log, Mongo readiness và Cloud Map/task events. Không tạo ALB hoặc mở inbound Internet để thử sửa định tuyến.

### (3) Lệnh CLI kiểm tra kết quả

```bash
curl --fail-with-body --silent --show-error "$TASK_API_URL/events"
python3 scripts/deploy_dev.py status
```

Kỳ vọng JSON danh sách sự kiện và services có running count phù hợp desired count. Browser còn lỗi trong khi curl thành công: kiểm tra URL/CORS trong Network tab; publish lại frontend bằng `python3 scripts/deploy_dev.py frontend` chỉ sau khi xác nhận cấu hình hiện tại đúng.

## 3. JWT `401` hoặc `403`

### (1) Nguyên nhân gốc rễ

`401` thường liên quan token thiếu/hết hạn, sai issuer/client hoặc dùng ID token thay access token. `403` có thể là thiếu scope tại authorizer/backend; cần đối chiếu response và log, không suy ra chỉ từ mã. Booking cần `flash-ticket/bookings:write` ở Cognito; adapter chuẩn hóa thành `bookings:write`. Public client không được cấp quyền quản trị events.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Đăng xuất/đăng nhập lại trên frontend CloudFront hiện tại để chạy Authorization Code + PKCE, sau đó lấy **access token** mới. Có thể xem một số claims local; thao tác decode này không xác minh chữ ký:

```bash
printf '%s' "$TOKEN" | python3 -c '
import base64,json,sys
part=sys.stdin.read().split(".")[1]
claims=json.loads(base64.urlsafe_b64decode(part+"="*(-len(part)%4)))
print(json.dumps({k:claims.get(k) for k in ("iss","client_id","token_use","scope","exp")},indent=2))
'
```

Đối chiếu issuer/client với `terraform -chdir=infra/environments/dev-foundation output -json web`. Không tắt JWT authorizer, đổi `AUTH_PROVIDER` hoặc nới scopes chỉ để vượt lỗi đăng nhập.

### (3) Lệnh CLI kiểm tra kết quả

```bash
curl --fail-with-body --silent --show-error \
  "$TASK_API_URL/bookings/$BOOKING_ID" \
  -H "Authorization: Bearer $TOKEN"
```

Token đúng nhưng booking thuộc người khác có thể trả `404`, vì truy vấn được giới hạn theo `user_id`; không bỏ điều kiện này.

## 4. Booking `409`: inventory, idempotency hay ticket chưa có?

### (1) Nguyên nhân gốc rễ

Đọc `detail`: `Sold out or event unavailable` khác `Idempotency-Key was used with a different request`, và khác `Ticket is not ready`. MySQL là nguồn inventory chính; số trên Redis có thể cập nhật chậm. Cùng key phải giữ nguyên event, quantity và email của cùng một ý định đặt vé.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Đọc booking bằng lệnh ở mục 1. Khi retry request bị timeout, giữ nguyên key/body ban đầu. Chỉ tạo key mới cho một đơn mới có chủ đích. Nếu hết vé, dừng tạo booking; nếu ticket chưa sẵn sàng, kiểm tra luồng async ở mục 7. Không `UPDATE inventory SET remaining=total`, không xóa booking/outbox để thử lại.

Trong một DB session quản trị đã được cấp phép, các truy vấn sau **chỉ đọc**:

```sql
SELECT event_id, total, remaining, version FROM flash_ticket.inventory;
SELECT status, email_status, COUNT(*) AS count
FROM flash_ticket.bookings GROUP BY status, email_status;
SELECT id, aggregate_id, published_at, attempts, available_at, lease_until
FROM flash_ticket.outbox ORDER BY created_at DESC LIMIT 20;
```

RDS ở private subnet. Các câu SQL này dành cho session/task có network access phù hợp; không phải hướng dẫn kết nối RDS trực tiếp từ Internet hoặc mở SG port 3306.

### (3) Lệnh CLI kiểm tra kết quả

```bash
curl --fail-with-body --silent --show-error "$TASK_API_URL/events"
```

Kết hợp response booking, SQL inventory/outbox và log theo cùng UUID; không dùng một giá trị Redis để kết luận đã over-selling.

## 5. MySQL bootstrap hoặc TLS thất bại

### (1) Nguyên nhân gốc rễ

Bootstrap log cố ý giới hạn thông tin: `BOOTSTRAP_FAILED stage=... mysql_code=...` hoặc `configuration or file error`. `1045` hướng tới xác thực/quyền; `2003` cần kiểm tra cả đường mạng và lỗi kết nối/TLS, không đủ để kết luận SG. `configuration` có thể là thiếu biến, migration files hoặc CA. Migration checksum mismatch khác lỗi password.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Đọc log `mysql-bootstrap` trước. Đối chiếu [MySQL Bootstrap](../mysql-bootstrap.md), [quickstart](../DEPLOY-DEV-QUICKSTART.md) và source [bootstrap.py](../../tools/mysql-bootstrap/bootstrap.py). Image chỉ copy `database/NNN_*.sql`; CA phải đọc được ở `/app/certs/global-bundle.pem`. Không sửa migration đã áp dụng; tạo migration mới. Không tắt certificate/hostname verification hoặc đổi password SSM riêng lẻ khi chưa có kế hoạch rotation đồng bộ.

Nếu cần kiểm tra files trên **image local đã build**, lệnh không nối DB/AWS:

```bash
read -r -p "Local mysql-bootstrap image: " TASK_BOOTSTRAP_IMAGE
docker run --rm --network none "$TASK_BOOTSTRAP_IMAGE" --check-files
```

Sau khi sửa đúng nguyên nhân và đã có image phù hợp, muốn retry bootstrap thì **dừng xử lý trước; các bước sau thay đổi AWS/DB và dùng credits**:

```bash
bash scripts/aws-dev-down.sh stop
python3 scripts/deploy_dev.py bootstrap
python3 scripts/deploy_dev.py start
```

Nếu đã sửa Dockerfile/code image, commit rồi dùng luồng `--rebuild-images` trong chương 05; retry bằng tag cũ sẽ vẫn chạy code cũ. MySQL DDL có implicit commit: migration lỗi giữa chừng cần xem schema thực tế trước khi retry, không xóa ledger để ép chạy lại.

### (3) Lệnh CLI kiểm tra kết quả

```bash
aws_ft logs tail /ecs/flash-ticket-dev/mysql-bootstrap --since 15m --format short
python3 scripts/deploy_dev.py status
```

One-off task phải có `exitCode=0`; `STOPPED` đơn thuần không phải thành công. Helper [deploy_dev.task](../../scripts/deploy_dev.py) kiểm tra exit code thay vì chỉ chờ task kết thúc.

## 6. MongoDB cloud-init lỗi `/run/sshd`

### (1) Nguyên nhân gốc rễ

Incident đã gặp: `Missing privilege separation directory: /run/sshd` làm `sshd -t` thất bại trước khi user-data bật `mongod`; cloud-init ghi lỗi `scripts_user`, MongoDB disabled/inactive. Dòng SSH `Permanently added ...` chỉ là warning về known-hosts, không chứng minh SSH thất bại. Không áp nguyên nhân này cho mọi lỗi MongoDB khi chưa có log.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Đọc trước qua EICE/SSH helper, không cần public IP:

```bash
bash scripts/ssh-mongodb.sh \
  'sudo -n cloud-init status --long; sudo -n systemctl status mongod --no-pager --full; sudo -n tail -n 80 /var/log/flash-ticket-mongodb-node.log'
```

Khi xác nhận đúng incident, **lệnh sau sửa hệ điều hành và chạy lại bootstrap trên node hiện có**:

```bash
python3 scripts/repair_mongodb_bootstrap.py
python3 scripts/provision_mongodb.py --execute
```

[Repair helper](../../scripts/repair_mongodb_bootstrap.py) dùng lock, từ chối khi cloud-init còn chạy/lỗi ở module khác, tạo tmpfiles rule cho `/run/sshd`, backup status/result dưới `/root/flash-ticket-mongo-repair/`, rồi reset riêng `modules-final` để rerun `scripts_user`. Backup giữ mode 0700/0600. Helper yêu cầu status sạch, `mongod` active và readiness marker; không tự coi rc2/degraded là thành công.

Không chạy `cloud-init clean`, không xóa cả `/var/lib/cloud`, không sửa user-data của EC2 đang chạy để chữa incident này: Terraform có `user_data_replace_on_change=true`, nên apply thay đổi đó có thể thay instance/root volume. Không xóa backup repair sau một lần sửa chưa được xác nhận.

### (3) Lệnh CLI kiểm tra kết quả

```bash
bash scripts/ssh-mongodb.sh \
  'sudo -n cloud-init status --long && sudo -n systemctl is-active mongod'
curl --fail-with-body --silent --show-error "$TASK_API_URL/events"
```

Xem thêm [MongoDB Bootstrap](../mongodb-bootstrap.md). Nếu khác lỗi privilege-separation, giữ nguyên trạng thái lỗi và thu thập log thay vì ép helper bỏ guard.

## 7. Booking `PENDING`, queue tăng hoặc Worker báo lỗi

### (1) Nguyên nhân gốc rễ

Theo thứ tự [Booking](../../services/booking/app/main.py) → [Relay](../../services/relay/app/main.py) → [Worker](../../services/worker/app/main.py):

| Dấu hiệu | Tầng cần kiểm tra tiếp |
|---|---|
| Chưa có booking | HTTP response, JWT, transaction MySQL |
| Booking/outbox có, `published_at` trống | Relay/Redis/SQS permission và lease/retry |
| Relay báo `Published outbox_id=...` | SQS visible/in-flight, Worker events/log |
| `PENDING` | Worker chưa hoàn tất upload PDF + cập nhật ticket state |
| `ISSUED` + email `PENDING` | PDF/state đã có; nhánh email còn chưa hoàn tất |
| `ISSUED` + email `SENT` | SES đã được ứng dụng ghi nhận chấp nhận request; chưa bảo đảm inbox |

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Đọc queue attributes trước, không `receive-message` chỉ để xem thử: receive làm tăng receive count và ẩn message tạm thời.

```bash
aws_ft sqs get-queue-attributes --queue-url "$TASK_QUEUE_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible VisibilityTimeout RedrivePolicy
aws_ft sqs get-queue-attributes --queue-url "$TASK_DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
aws_ft logs tail /ecs/flash-ticket-dev/relay --since 30m --format short
aws_ft logs tail /ecs/flash-ticket-dev/worker --since 30m --format short
```

Các counters là xấp xỉ. Cấu hình hiện tại visibility/lease 120 giây, long polling 20 giây; heartbeat gia hạn lease và visibility. Redrive mặc định `maxReceiveCount=5`, nhưng đọc `RedrivePolicy` thực tế vì đây là biến Terraform. Không suy ra đúng 5×120 giây sẽ vào DLQ: heartbeat, thời gian xử lý, retry và thời điểm receive đều ảnh hưởng.

Worker ghi `Message failed message_id=...`, `Heartbeat failed booking_id=...`; Relay ghi `Relay failure outbox_id=...`. `failure_details()` ghi AWS operation, `aws_error_code`, request ID/status khi có; không ghi nguyên AWS error message/payload. Phân biệt SQS `message_id` với SES `ses_message_id` trong `Email accepted`.

Sửa nguyên nhân cụ thể trước: service desired0 thì bật chủ động; `AccessDenied` đối chiếu task role/resource ARN; quota/throttling đối chiếu dịch vụ/operation; `Unknown booking` kiểm tra có vừa xóa DB nhưng giữ queue cũ không. Không tự mark `SENT`, đổi `published_at` hoặc reset lease/inventory trong DB.

### (3) Lệnh CLI kiểm tra kết quả

```bash
curl --fail-with-body --silent --show-error \
  "$TASK_API_URL/bookings/$BOOKING_ID" -H "Authorization: Bearer $TOKEN"
aws_ft logs tail /ecs/flash-ticket-dev/worker --since 10m --format short
```

Theo dõi cùng booking UUID tới `Ticket completed booking_id=...`; nếu message đã vào DLQ, sửa xong vẫn cần quyết định replay ở mục 9.

## 8. PDF đã có nhưng chưa nhận email; S3 `403`

### (1) Nguyên nhân gốc rễ

Worker gửi tới `booking.email` từ request đặt vé, không tự lấy email Cognito. SES sandbox cần sender và recipient hợp lệ/được xác minh theo quy định SES. `MessageRejected`, throttling/quota, suppression/bounce hoặc spam cần bằng chứng riêng. Case chưa nhận thư trước đây chưa có đủ Worker logs để kết luận đã sửa xong.

API cấp link PDF 120 giây; email chứa link 900 giây. S3 private không phục vụ ticket bằng URL unsigned. `ISSUED` không đồng nghĩa `SENT`; `SENT` không đồng nghĩa delivered.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Đối chiếu `email` trong booking response/giao diện. Đọc SES cùng region `us-east-1`:

```bash
TASK_SES_SENDER="$(terraform -chdir=infra/environments/dev-foundation output -raw ses_sender_email)"
read -r -p "Actual booking recipient email: " TASK_RECIPIENT
aws_ft ses get-identity-verification-attributes --identities "$TASK_SES_SENDER" "$TASK_RECIPIENT"
aws_ft ses get-send-quota
aws_ft ses get-send-statistics
```

Nếu cần xác minh recipient, **operator chủ động chạy lệnh sau; AWS sẽ gửi verification email**:

```bash
aws_ft ses verify-email-identity --email-address "$TASK_RECIPIENT"
```

Người sở hữu email phải xác nhận link. Không tự gửi email thử hàng loạt. Khi SES đã nhận request, tìm `Email accepted booking_id=... ses_message_id=...`; sau đó kiểm tra inbox/spam và delivery/bounce events nếu account đã cấu hình chúng. Chưa cấu hình events thì không giả định dự án có SNS delivery tracking.

Nếu link S3 hết hạn, gọi lại endpoint ticket như mục 1. Nếu link mới vẫn lỗi, operator đọc object metadata bằng credentials, không mở bucket public:

```bash
TASK_ASSETS_BUCKET="$(terraform -chdir=infra/environments/dev-foundation output -raw assets_bucket_name)"
aws_ft s3api head-object --bucket "$TASK_ASSETS_BUCKET" --key "tickets/$BOOKING_ID.pdf"
```

Không có object: quay lại Worker upload/state logs. Có object nhưng link lỗi: kiểm tra task role `s3:GetObject`, link expiry và clock. SES có thể đã nhận email trước khi Worker chết lúc cập nhật DB; retry có thể gửi trùng, nên luồng này không bảo đảm exactly-once email.

### (3) Lệnh CLI kiểm tra kết quả

```bash
aws_ft logs tail /ecs/flash-ticket-dev/worker --since 15m --format short
curl --fail-with-body --silent --show-error \
  "$TASK_API_URL/bookings/$BOOKING_ID" -H "Authorization: Bearer $TOKEN"
```

Xác nhận riêng ba bằng chứng: ticket state/object, SES acceptance ID và thư thực nhận hoặc delivery event. Không sửa DB thành `SENT` để làm giao diện hết báo pending.

## 9. Replay DLQ sau khi đã sửa nguyên nhân

### (1) Nguyên nhân gốc rễ

DLQ giữ jobs vượt receive threshold, không tự đưa chúng về source queue sau khi sửa cấu hình. Nếu DB đã destroy, jobs cũ có thể không còn booking để xử lý. Purge xóa jobs; redrive chuyển jobs để xử lý lại.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

**Operator-only, thay đổi queue và có thể phát hành vé/gửi lại email.** Chỉ chạy sau khi xác nhận DB/Worker/SES đã sẵn sàng và toàn bộ jobs trong đợt bulk replay được chấp thuận. API redrive không lọc theo một booking ID; nếu queue lẫn jobs chưa được duyệt, không dùng bulk replay.

Đọc ARN/policy trước:

```bash
aws_ft sqs get-queue-attributes --queue-url "$TASK_DLQ_URL" \
  --attribute-names QueueArn ApproximateNumberOfMessages RedriveAllowPolicy
aws_ft sqs get-queue-attributes --queue-url "$TASK_QUEUE_URL" \
  --attribute-names QueueArn RedrivePolicy
```

Khi đã quyết định replay, dùng official SQS API; tốc độ 1 message/giây vẫn cần nằm trong quota xử lý/email thực tế:

```bash
aws_ft sqs start-message-move-task \
  --source-arn "$TASK_DLQ_ARN" \
  --destination-arn "$TASK_QUEUE_ARN" \
  --max-number-of-messages-per-second 1
```

Role operator cần quyền redrive cùng các quyền receive/delete/get attributes/send thích hợp; nếu thiếu quyền, sửa policy có phạm vi thay vì cấp admin cho Worker. Không dùng `--purge-queues` để thay thế replay.

### (3) Lệnh CLI kiểm tra kết quả

```bash
aws_ft sqs list-message-move-tasks --source-arn "$TASK_DLQ_ARN" --max-results 1
aws_ft logs tail /ecs/flash-ticket-dev/worker --since 15m --format short
```

Đọc task status và số message chuyển, rồi đối chiếu từng booking. Nếu phải dừng task đang chạy:

```bash
read -r -p "Active message move task handle: " TASK_MOVE_HANDLE
aws_ft sqs cancel-message-move-task --task-handle "$TASK_MOVE_HANDLE"
```

Cancel chỉ dừng phần còn lại; messages đã chuyển không được tự rollback.

## 10. Redis mất projection, inventory trên giao diện không đúng

### (1) Nguyên nhân gốc rễ

Catalog đọc metadata MongoDB và projection Redis. Redis lỗi/mất keys có thể làm remaining/version không có giá trị; không có nghĩa số vé MySQL bằng 0. Lua projection chỉ nhận version lớn hơn hiện tại để chống sự kiện cũ ghi đè dữ liệu mới.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Sau khi kiểm tra Redis endpoint/TLS và Catalog/Relay logs, có thể chạy reconciliation **ghi Redis và tạo một Fargate task có phí**, từ network/task role của Relay:

```bash
python3 - <<'PY'
from scripts import deploy_dev
runtime = deploy_dev.require_outputs("ecs", "rds", "redis")
deploy_dev.task(runtime, "relay", ["python", "-m", "scripts.reconcile_inventory"])
PY
```

Đây không phải chạy kết nối DB từ host local; helper launch ECS one-off task với env/secrets/network hiện tại. [reconcile_inventory.py](../../scripts/reconcile_inventory.py) SELECT MySQL rồi cập nhật projection. Nó không reset MySQL stock và không ghi đè Redis có version bằng/cao hơn. Nếu ai đã sửa tay cache thành version sai quá cao, dừng và kiểm tra dữ liệu cụ thể; không `FLUSHALL` để thử.

### (3) Lệnh CLI kiểm tra kết quả

```bash
aws_ft logs tail /ecs/flash-ticket-dev/relay --since 10m --format short
curl --fail-with-body --silent --show-error "$TASK_API_URL/events"
```

Task cần exit code 0 và log `Reconciled ... inventory rows`; kiểm tra remaining/version với nguồn MySQL.

## 11. AWS login hết hạn, CLI JSON/stdin hoặc cleanup plan cũ

### (1) Nguyên nhân gốc rễ

`ExpiredToken`, `InvalidClientTokenId` hoặc lỗi refresh session xảy ra trước khi helper làm việc với tài nguyên. Profile bridge `flash-ticket-terraform` sử dụng phiên đăng nhập `flash-ticket-dev`. Các bản helper cũ dùng JSON qua `/dev/stdin` có thể gặp lỗi đọc request; source hiện dùng temporary file mode 0600. Saved Terraform plan phản ánh state/flags tại lúc tạo, không phải trạng thái mới nhất.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Với login profile hiện tại:

```bash
aws login --profile flash-ticket-dev
aws_ft sts get-caller-identity
```

Chỉ dùng `aws sso login --profile ...` nếu profile của bạn thực sự cấu hình SSO. Cập nhật source đã commit, rồi retry đúng lệnh đã lỗi:

```bash
git pull --ff-only origin feat/aws-dev-deploy
python3 scripts/deploy_dev.py status
```

Giữ cơ chế JSON private-file trong [deploy_dev.py](../../scripts/deploy_dev.py) và [provision_mongodb.py](../../scripts/provision_mongodb.py); không nhét mật khẩu vào command line để né lỗi stdin. Không apply một cleanup `.tfplan` cũ sau khi đã bật lại môi trường. Muốn destroy, dùng `aws-dev-down.sh runtime` hoặc `all` để lập kế hoạch hiện tại theo [runbook](../AWS-DEV-POWER.md), đọc plan rồi xác nhận.

### (3) Lệnh CLI kiểm tra kết quả

```bash
aws_ft sts get-caller-identity
python3 scripts/deploy_dev.py status
```

Account/assumed role phải khớp dự án. Không dùng root access keys để vượt qua lỗi IAM/profile.

## 12. Docker WSL: `error saving credentials`, `The stub received bad data`

### (1) Nguyên nhân gốc rễ

Docker CLI có thể được cấu hình Windows credential helper không hoạt động trong phiên WSL. AWS trả ECR password thành công nhưng Docker không lưu được token; đây không chứng minh ECR repository/IAM sai.

### (2) Code/lệnh sửa lỗi hoàn chỉnh

Dùng config tạm riêng, không ghi đè config Docker hiện có. Đây là lưu registry credentials local tạm thời; directory/file cần giữ private:

```bash
TASK_DOCKER_CONFIG="$(mktemp -d)"
chmod 700 "$TASK_DOCKER_CONFIG"
printf '{"auths":{}}\n' > "$TASK_DOCKER_CONFIG/config.json"
chmod 600 "$TASK_DOCKER_CONFIG/config.json"
TASK_ECR_REGISTRY="$(terraform -chdir=infra/environments/dev-foundation output -raw ecr_registry_url)"

aws_ft ecr get-login-password | docker --config "$TASK_DOCKER_CONFIG" login \
  --username AWS --password-stdin "$TASK_ECR_REGISTRY"
```

Giữ `--config "$TASK_DOCKER_CONFIG"` cho lệnh Docker tiếp theo cần credentials đó. Nếu chạy helper build/push trong cùng terminal, truyền `DOCKER_CONFIG="$TASK_DOCKER_CONFIG"` trước lệnh helper. Không in password hoặc copy `config.json` vào Git.

### (3) Lệnh CLI kiểm tra kết quả

Chọn image tag cụ thể đã tồn tại, không `latest`; pull ghi image vào ổ đĩa local:

```bash
read -r -p "Existing ECR image URI with tag: " TASK_ECR_IMAGE
docker --config "$TASK_DOCKER_CONFIG" pull "$TASK_ECR_IMAGE"
docker --config "$TASK_DOCKER_CONFIG" logout "$TASK_ECR_REGISTRY"
rm -rf -- "$TASK_DOCKER_CONFIG"
```

## Bảo toàn dữ liệu khi recovery

`stop` giữ DB nhưng không dừng phí RDS/Redis/EC2. `runtime` và `all` xóa data theo phạm vi runbook; đây không phải backup/restore. Trước thao tác xóa, xác nhận backup/snapshot cần giữ, retention và restore procedure thực tế; đừng mặc định dev đã bật backup đầy đủ. Terraform state, `.secrets` và backup repair chứa thông tin riêng, phải bảo vệ quyền truy cập và không commit.

MySQL `inventory.remaining/version`, booking và outbox phải giữ cùng ngữ nghĩa transaction. Không đánh dấu outbox published, booking issued hoặc email sent chỉ để dashboard trông bình thường. Chỉ retry/reconcile/replay sau khi hiểu tác dụng lên dữ liệu và email. Khi cần hỗ trợ, gửi các IDs, thời điểm, HTTP response đã bỏ token và log lỗi có mã; ghi rõ những lệnh thay đổi bạn đã chạy.

[← 05. Docker và vận hành](05-docker-and-operations.md) · [07. Thực hành bảo trì →](07-maintenance-practice.md)
