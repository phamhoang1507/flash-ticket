# 07 — Tự bảo trì và thực hành thay đổi

[← Chẩn đoán và phục hồi](06-troubleshooting-and-recovery.md) · [Mục lục onboarding →](README.md)

Mục tiêu là tự đi từ yêu cầu đến bằng chứng kiểm chứng, không cần đoán lệnh tiếp theo. Chương này bám source baseline `c3c18e7`; những thay đổi bên dưới là **bài tập trên branch/local**, chưa có sẵn trong code và không phải yêu cầu triển khai AWS. Không chương nào chứng minh hệ thống đạt 50.000 users.

## 1. Chọn đúng loại công việc

| Loại | Câu hỏi đầu tiên | Cách bắt đầu |
|---|---|---|
| Incident | Hành vi nào sai, bắt đầu lúc nào, ảnh hưởng dữ liệu nào? | Thu log/status an toàn, tái hiện nhỏ, dùng [chương 6](06-troubleshooting-and-recovery.md); chưa đổi schema hay purge queue |
| Feature | Contract và tiêu chí chấp nhận thay đổi thế nào? | Viết ví dụ input/output, xác định owner/quyền và tương thích dữ liệu |
| Infrastructure change | Resource, state, chi phí và khả năng replace nào bị ảnh hưởng? | Đọc Terraform/outputs, xác định environment và state; không suy từ tên biến rằng thay đổi không gây gián đoạn |

Một vòng làm việc: **ghi mục tiêu → tìm contract → chọn phạm vi → sửa nhỏ → kiểm tra có ý nghĩa → review diff → commit → build đúng source → triển khai có chủ đích → kiểm tra hành vi → ghi cách phục hồi**. Incident đang cần phục hồi không phải lúc tranh thủ thêm feature.

Trước khi sửa, viết ba câu: “Hiện tại…”, “Sau thay đổi…”, “Tôi sẽ chứng minh bằng…”. Nếu chưa chỉ ra được request, row, message hoặc plan cần quan sát, hãy thu hẹp thay đổi.

## 2. Tìm file theo nhiệm vụ

Đi theo thứ tự **code → contract → database → Terraform → Docker → tests → deploy**, nhưng chỉ sửa những tầng thực sự bị tác động.

| Nhiệm vụ | Nơi bắt đầu và phần phải đối chiếu |
|---|---|
| Catalog field, cache hoặc lỗi metadata | [Catalog API](../../services/catalog/app/main.py): `EventInput`, `EventView`, `load_metadata`; [metadata tests](../../tests/test_catalog_metadata.py), [Redis projection](../../shared/inventory.py) |
| Booking request/response và ownership | [Booking API](../../services/booking/app/main.py): `BookingInput`, `BookingView`, `BY_ID`, `replay`; [frontend](../../frontend/src/App.jsx), [API client](../../frontend/src/api.js) |
| Token/scopes | [Auth adapter](../../shared/auth.py), [frontend auth](../../frontend/src/auth.js), [web/auth Terraform](../../infra/modules/web_auth/main.tf) |
| Transaction, outbox hoặc schema | [SQL migration](../../database/001_schema.sql), [Relay](../../services/relay/app/main.py), [MySQL bootstrap](../../tools/mysql-bootstrap/bootstrap.py) |
| PDF, email, S3, SQS retry | [Worker](../../services/worker/app/main.py), [ticket generation](../../services/worker/app/ticket.py), [AWS clients](../../shared/aws.py), [safe error details](../../shared/errors.py) |
| Network/task configuration | [VPC module](../../infra/modules/vpc/main.tf), [SGs](../../infra/modules/vpc/security_groups.tf), [ECS module](../../infra/modules/ecs_runtime/main.tf), [dev deployment](../../infra/environments/dev/deployment.tf) |
| MongoDB AMI/bootstrap | [Builder template](../../infra/modules/mongo_ami_builder/templates/bootstrap.sh), [bake controller](../../scripts/bake_mongo_ami.py), [MongoDB user script](../../scripts/mongodb/bootstrap.js), [provisioner](../../scripts/provision_mongodb.py) |
| Image và deployment | Dockerfile/requirements dưới service; [deploy stages](../../scripts/deploy_dev.py), [power controller](../../scripts/aws_dev_power.py) |

HTTP contract không chỉ là Pydantic schema: còn status code, scope, owner, retry và ý nghĩa dữ liệu. Message contract nằm ở payload Booking tạo, Relay publish và Worker đọc. Tên environment variable phải đồng bộ giữa code, Compose và ECS; tạo một biến trong `.env` chưa khiến ECS nhận nó.

## 3. Git, kiểm tra và image phải cùng một thay đổi

Chạy ở root repository. Trước tiên xem branch/upstream hiện tại; không ép chuyển về một branch tên cố định và không dùng reset để làm mất công việc:

```bash
git status --short
git branch --show-current
git branch -vv
git remote -v
git fetch origin
```

Nếu working tree sạch, tạo branch từ commit đã chọn của branch đang làm việc:

```bash
git switch -c feat/catalog-cache-ttl
```

Dùng `fix/...` cho lỗi, `feat/...` cho hành vi mới, `chore/...` cho bảo trì. Nếu upstream có commit mới, đọc diff rồi fast-forward/rebase theo workflow nhóm; `fetch` tự nó không cập nhật source. Không chạy `pull` mù khi có thay đổi local.

Sau khi sửa bài tập cache, stage đúng file đã review; nếu chưa tạo test mới thì không thêm tên file tưởng tượng:

```bash
git diff -- services/catalog/app/main.py tests/test_catalog_metadata.py compose.override.yaml
git add services/catalog/app/main.py tests/test_catalog_metadata.py compose.override.yaml
git diff --cached --check
git diff --cached --stat
git commit -m "feat: configure catalog metadata cache TTL"
git push -u origin HEAD
```

`HEAD` đẩy branch hiện tại; không ghi đè một branch remote lịch sử bằng refspec nhầm tên. Review thêm tài liệu contract khi hành vi/config đổi. Không stage `.env`, `.secrets`, tfvars, state, plan hay token; `.gitignore` không bảo vệ secret đã từng được track.

### Hiểu 50 offline unit tests hiện tại

Sau khi chuẩn bị Python dependencies theo [Docker và vận hành](05-docker-and-operations.md), lệnh discovery là:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Baseline có **50 test methods**; số này sẽ thay đổi khi thêm coverage, không phải mục tiêu để giữ cố định.

| File | Chúng kiểm tra gì? |
|---|---|
| `tests/test_catalog_metadata.py` | Metadata hợp lệ và response lỗi có kiểm soát với source document hỏng, bằng doubles |
| `tests/test_error_details.py` | Phân biệt mã lỗi AWS nhưng không log email, token, payload hoặc metadata chèn dòng |
| `tests/test_mysql_bootstrap.py` | Migration ledger/checksum và bộ kiểm tra quyền app user bằng fixtures |
| `tests/test_mongodb_provisioner.py` | URI encoding, secret/provisioning và guard recovery/AMI bằng mocks/source checks |
| `tests/test_cleanup_preparation.py` | Chỉ chấp nhận cleanup-plan đúng phạm vi và các nhánh controller được mock |

Đây **không phải** 50 integration/load tests. Chúng không chạy database thật, xác nhận AWS IAM thực tế, Terraform validate/plan, ECS rollout, SES delivery, race MySQL hay tải flash sale. “Unit tests pass” chỉ kết luận những hành vi được test. Các integration scripts MySQL/MongoDB chạy container riêng; đọc [MySQL](../mysql-bootstrap.md) và [MongoDB](../mongodb-bootstrap.md) trước khi chủ động chạy, không coi chúng là discovery offline.

Test mới nên bắt một lỗi có thể xảy ra: cùng key khác payload, mất cache, crash sau publish, hoặc metadata chứa secret. Tránh test chỉ lặp lại một hằng số hoặc mock mọi thứ đến mức không còn hành vi thật để kiểm tra.

## 4. Sáu bài tập có bằng chứng

Các bước dưới đây mặc định chỉ đọc hoặc chạy trên local lab. Hoàn thành setup theo [local guide](../LOCAL-DEVELOPMENT.md); đừng dùng database/event AWS đang giữ dữ liệu để luyện tập.

### Bài 1 — Cấu hình TTL metadata mà không đổi inventory

**Mục tiêu:** thay `60 + random.randint(0, 15)` trong `load_metadata` bằng TTL có cấu hình, vẫn giữ jitter và cache miss fallback MongoDB.

1. Đọc `CACHE_KEY`, `load_metadata`, `EventMetadata` và `EventView` trong Catalog. Cache metadata hiện không phải cache toàn bộ response chứa số vé.
2. Thiết kế biến mới, ví dụ `CATALOG_CACHE_TTL_SECONDS`, với default 60, phạm vi hợp lệ và lỗi rõ khi cấu hình sai. Biến này là đề xuất bài tập, chưa tồn tại ở baseline.
3. Sửa code và truyền biến qua Compose cho Catalog. Nếu đưa lên AWS sau này, phải bổ sung mapping tương ứng ở ECS module; không giả định Compose áp dụng cho AWS.
4. Thêm test TTL hợp lệ/không hợp lệ, cache miss trả metadata và inventory vẫn đọc projection mới. Giữ lỗi Redis có thể fallback theo contract hiện có.

**Bằng chứng:** diff cho thấy không đổi `project_inventory`; test quan sát TTL nằm trong phạm vi đã quy định; đọc event hai lần sau một booking thấy inventory có thể cập nhật dù metadata cache chưa hết hạn. **Tự kiểm:** tăng TTL metadata không được kéo dài thời gian lưu một số vé cũ trong response cache.

### Bài 2 — Thêm response field mà vẫn giữ ranh giới email

**Mục tiêu:** thử thêm field dẫn xuất `ticket_ready` vào response booking trên branch local; không thêm business flow mới.

1. Định nghĩa `ticket_ready = (status == "ISSUED")`. Đây là khả năng đã phát hành file, không phải email đã tới inbox.
2. Đọc `BookingView`, `view`, `BY_ID` và `BY_KEY`. Hai đường create/replay/read phải trả contract nhất quán; frontend xử lý field mới mà không làm hỏng booking đang lưu từ bản cũ.
3. Không lấy email trong URL/query string hoặc log. API hiện trả email cho owner; không chuyển nó sang public Catalog. Giữ `user_id` từ principal và owner predicate trong SQL.
4. Kiểm tra local: chủ đơn đọc được; token người khác không đọc/tải được; input email không hợp lệ bị từ chối; response `PENDING`/`ISSUED` cho giá trị dẫn xuất đúng.

**Bằng chứng:** ví dụ JSON đã che email/token, contract và negative checks. **Tự kiểm:** `ISSUED` và `email_status=SENT` là hai trạng thái khác nhau; SENT chỉ nói SES accepted, không chứng minh inbox. Thêm field nullable vào DB để tính một boolean đã có thể suy ra là không cần thiết.

### Bài 3 — Chứng minh idempotency và cạnh tranh inventory

**Mục tiêu:** thiết kế rồi chạy một scenario nhỏ với MySQL local thật; không sửa thuật toán chỉ vì chưa có test.

1. Đọc conditional `UPDATE`, unique `(user_id, idempotency_key)`, request hash, transaction và nhánh bắt `IntegrityError` trong Booking. Unique constraint là hàng rào cuối cùng khi hai request vượt qua lần đọc trước cùng lúc.
2. Tạo event/inventory test riêng và ghi snapshot ban đầu. Dùng cùng principal, cùng key, cùng payload cho hai request đồng thời; so với hai key khác nhau. Giữ key khi retry request chưa biết kết quả.
3. Đối chiếu database: cùng key chỉ có một booking và một outbox event, inventory giảm một lần. Cùng key khác quantity/email trả 409, không trừ thêm.
4. Cho nhiều key cạnh tranh số vé nhỏ: tổng quantity thành công không vượt số ban đầu, remaining không âm. Không dùng số người gửi request làm số booking thành công.

**Bằng chứng:** request outcomes, số row và tổng quantity theo event test; không lưu token/email thật trong report. **Tự kiểm:** mock `UPDATE` thành công không chứng minh MySQL lock/rollback. Nếu lỗi, giữ dữ liệu test để phân tích trước khi reset; không purge queue AWS.

### Bài 4 — Migration mới, không sửa lịch sử

**Mục tiêu:** hiểu expand migration và checksum ledger. SQL dưới là **minh họa hoàn chỉnh cho một bài tập**, chưa được tạo thành file trong repo, chưa được áp dụng lên local/AWS:

```sql
USE flash_ticket;

ALTER TABLE bookings
    ADD COLUMN client_reference VARCHAR(64) NULL;
```

1. Nếu thực hiện, tạo file mới `database/002_add_client_reference.sql` trên branch, giữ nguyên `001_schema.sql`. Cột nullable giúp code cũ chưa ghi field vẫn hoạt động; chưa tự thêm API tính năng cho nó.
2. Đọc `load_migrations`, `pending_migrations` và `_schema_migrations` trong MySQL bootstrap. Migration đã áp dụng được đối chiếu tên/checksum; bị sửa hoặc bị xóa khỏi bộ migration là lỗi, không phải cơ hội chạy lại seed.
3. Bổ sung fixture: 001 đã áp dụng thì chỉ 002 pending; checksum 001 thay đổi phải fail; chạy bootstrap lần hai không áp dụng 002 nữa. Dùng unit tests cho ledger và MySQL integration lab cho DDL thật.
4. Đối chiếu cột mới, ledger/checksum và inventory trước/sau. Không cấp DDL cho app user, không sửa ledger để tắt guard.

**Bằng chứng:** diff 001 trống, 002 riêng, ledger chỉ thêm một record và tồn kho không reset. **Tự kiểm:** MySQL DDL implicit commit; crash sau ALTER trước khi ghi ledger có thể để cột đã tồn tại. Khi đó kiểm tra schema/ledger rồi lập kế hoạch sửa tiến, không xóa checksum hoặc chạy lại mù. Nếu quyết định bỏ cột đã release, thêm migration sau; không xóa file 002 khỏi lịch sử.

### Bài 5 — Đưa đúng source thành image và chuẩn bị rollback

**Mục tiêu:** phân biệt “code đã push”, “image đã push” và “task đã chạy image đó”. Không bắt buộc deploy AWS để hoàn thành phần đọc/local.

1. Review diff, chạy checks phù hợp rồi commit/push bằng branch hiện tại. Đọc `images` trong `deploy_dev.py`: nó yêu cầu working tree sạch, build năm images và lưu tag dùng chung.
2. Thử build Booking local bằng tag Git SHA; lệnh này có network/build cost local nhưng không tạo AWS resource:

```bash
TASK_TAG="dev-1.0.0-$(git rev-parse --short=12 HEAD)"
docker build --file services/booking/Dockerfile --tag "flash-ticket/booking:$TASK_TAG" .
```

3. Docker Hub login/build/push đầy đủ nằm trong [local guide](../LOCAL-DEVELOPMENT.md#6-git-và-docker-hub). AWS ECR dùng `images`; `up` thường tái sử dụng bộ tag đã lưu. Khi chủ động deploy source backend/shared/bootstrap/migration mới, dùng `bash scripts/aws-dev-up.sh --rebuild-images` theo [runbook](../AWS-DEV-POWER.md). Đổi docs không bắt buộc rebuild; frontend có build/publish riêng theo cấu hình thực tế.
4. Ghi commit, tag, digest, task revision và kiểm tra hành vi sau rollout. Không ghi đè tag cũ; không dùng `latest` để gọi là rollback.

**Rollback cần kế hoạch trước:** chọn tag đã tồn tại đủ bộ ECR, biết digest và tương thích schema/message hiện tại. Runtime dùng chung `application_image_tag` cho các images liên quan, không phải một biến chỉ riêng Booking. Review giá trị trong `infra/environments/dev/launch.auto.tfvars`, dự kiến task definition changes và Terraform plan trước apply. Không mong `git checkout` tự đổi ECS hoặc DB.

Nếu đã thêm cột nullable như bài 4, code cũ thường vẫn có thể chạy, nhưng phải kiểm chứng queries và contract. Nếu migration xóa/đổi ý nghĩa cột, rollback image có thể nguy hiểm; dùng forward fix hoặc restore đã lập kế hoạch. **Không downgrade database hoặc xóa migration ledger để đi theo image cũ.** Spot thiếu capacity cũng có thể làm rollout chưa hoàn tất dù plan đúng.

**Bằng chứng:** bảng “commit → image tag/digest → task đang chạy → smoke/booking check”. Nếu chưa deploy, ghi rõ mới build local; không tự điền task/digest AWS giả.

### Bài 6 — Chọn lifecycle và sửa bootstrap/AMI đúng lớp

**Mục tiêu:** tự quyết định phạm vi thay đổi hạ tầng và chi phí, không dùng destroy làm bước debug mặc định.

1. Với tình huống “nghỉ một giờ, cần giữ DB”, chọn `down stop` và nêu rằng RDS/Redis/EC2 vẫn chạy. Với “bỏ DB test, giữ foundation”, chọn `down runtime`. Với “kết thúc lab, chấp nhận bỏ tài nguyên/dữ liệu dự án”, chọn `down all`. Đọc phạm vi và queue reset trong [runbook](../AWS-DEV-POWER.md), không cần thực thi để làm bài.
2. Với lỗi MongoDB user, đọc guard trong `bootstrap.js`: admin database role `root` khác application role `readWrite`; existing roles/credentials sai phải bị từ chối. Không cấp root cho app user để vượt bootstrap. MongoDB role `root`, Linux root và AWS account root là ba khái niệm khác nhau.
3. Với lỗi boot từ AMI, đọc builder template, private-node template và `repair_mongodb_bootstrap.py`. `/run/sshd` là runtime directory; marker/EC2 stopped không thay cho validation qua SSM và exit status. Không sửa app code để che lỗi cloud-init.
4. Builder tắt timer `apt-daily*` để tránh package-lock race; điều đó không phải khuyến nghị tắt security updates trên production host. Đừng chạy upgrade tùy hứng trên private MongoDB node rồi coi source/AMI đã được cập nhật. Muốn nâng package: review builder/dependencies, bake/validate AMI và lập kế hoạch dữ liệu; bake mới không phải thao tác cập nhật an toàn cho node đang giữ DB.

**Bằng chứng:** một bản quyết định gồm dữ liệu giữ/mất, tài nguyên còn tính phí, state cần giữ, guard nào phải pass và cách phục hồi. **Tự kiểm:** không xóa local bootstrap state/remote state để “làm sạch lỗi”; không xóa `.secrets/aws-dev-secrets.json` khi DB cũ cần cùng credentials.

## 5. Tự kiểm tra trước khi hỏi trợ giúp

Trả lời trước, sau đó mở file trong cột cuối để kiểm chứng. Nếu trả lời khác source, sửa hiểu biết hoặc nêu rõ contract muốn thay đổi.

| Câu hỏi | Đáp án ngắn và nơi kiểm chứng |
|---|---|
| 1. Có hai AZ tức là app đã HA? | Không; còn số task, placement, Spot và data replication. Đọc `infra/environments/dev/deployment.tf`, `infra/modules/ecs_runtime/main.tf`, `infra/modules/data_layer/` |
| 2. Redis còn 10 vé có bảo đảm đặt được một vé? | Không; MySQL conditional update quyết định. Xem `shared/inventory.py`, Booking và `database/001_schema.sql` |
| 3. Bốn backend roles khác nhau ra sao? | Catalog đọc metadata/projection; Booking commit giao dịch; Relay chuyển committed outbox; Worker phát hành/email. Đọc bốn `services/*/app/main.py` |
| 4. Vì sao SQS vẫn trùng dù có Outbox? | Publish thành công rồi chết trước mark published; SQS Standard cũng at-least-once. Xem Relay/Worker, lease và persisted state |
| 5. Retry cùng key nhưng email mới có phải đơn mới? | Không; payload hash khác bị 409. Muốn đơn mới dùng key mới có chủ đích. Xem `replay` và request hash của Booking |
| 6. SENT bảo đảm khách nhận được thư? | Không; mới SES accepted. Xem Worker và safe AWS error summary ở `shared/errors.py` |
| 7. Đóng terminal/stop ECS đã hết phí? | Không; DB nodes/storage còn. Xem `scripts/aws_dev_power.py`, `scripts/deploy_dev.py` và power runbook |
| 8. Clone repo có khôi phục state/secrets không? | Không. Giữ local bootstrap state/backup, remote state, tfvars/backend config và `.secrets` an toàn; xem `.gitignore`, `infra/bootstrap/` |
| 9. Vì sao không sửa 001 để thêm cột? | Checksum lịch sử phải bất biến; thêm migration mới. Xem `pending_migrations` và `tests/test_mysql_bootstrap.py` |
| 10. 50 unit tests pass đủ để deploy AWS chịu tải chưa? | Chưa; chưa xác nhận DB concurrency, IAM, Terraform/AWS runtime hoặc load. Đọc tests, integration scripts và giới hạn ở mục 3 |

Bạn có thể chia mỗi buổi học thành một bài tập, không cần chạy AWS cho mọi buổi. Hoàn thành khi có thể chỉ ra **file thay đổi, contract bị tác động, bằng chứng đã có và phần chưa kiểm chứng**, rồi nhờ người khác review đúng các điểm đó.

[← Chương 6](06-troubleshooting-and-recovery.md) · [Quay lại mục lục onboarding →](README.md)
