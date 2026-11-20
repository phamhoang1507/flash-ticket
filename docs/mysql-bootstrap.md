# MySQL Bootstrap

## Mục đích

Image `mysql-bootstrap` được dùng cho ECS one-off task.

Task chạy migration, tạo application account, cấp quyền và thoát.
Task không cần chạy liên tục như ECS service.

## Schema

Migration source:

```text
database/001_schema.sql
```

Quy tắc tên:

```text
NNN_description.sql
```

Migration đã áp dụng được ghi trong `_schema_migrations` với checksum.

- Không sửa hoặc xóa migration đã áp dụng.
- Thay đổi tiếp theo dùng file mới.
- File đã áp dụng không được chạy lại khi bootstrap thành công lần sau.
- Không hỗ trợ MySQL CLI directive `DELIMITER`.

MySQL DDL có implicit commit. Một migration lỗi giữa chừng có thể để lại
DDL đã áp dụng; ledger chỉ được ghi sau khi file hoàn tất.

## Credentials

Environment khi chạy thật:

| Biến | Giá trị/mục đích |
|---|---|
| MYSQL_HOST | RDS endpoint |
| MYSQL_PORT | Mặc định 3306 |
| MYSQL_ADMIN_USER | Mặc định flash_admin |
| MYSQL_ADMIN_PASSWORD | Inject từ SSM qua ECS Execution Role |
| MYSQL_APP_USER | Mặc định flash_app |
| MYSQL_APP_PASSWORD | Inject từ SSM qua ECS Execution Role |
| MYSQL_SSL_CA | Mặc định /app/certs/global-bundle.pem |

Không đặt password trong Terraform tfvars hoặc Docker image.

Script không tự rotate password của existing app user.

## Application privileges

App user được cấp SELECT, INSERT, UPDATE, DELETE trên:

- flash_ticket.inventory
- flash_ticket.bookings
- flash_ticket.outbox

App user không được cấp quyền DDL hoặc quyền trên migration ledger.

## Offline checks

Build image trước khi kiểm tra, từ root repository:

```bash
TASK_BOOTSTRAP_TAG="dev-1.0.0-$(git rev-parse --short=12 HEAD)"
docker build --file tools/mysql-bootstrap/Dockerfile \
  --tag "flash-ticket/mysql-bootstrap:$TASK_BOOTSTRAP_TAG" .

docker run --rm \
  --network none \
  "flash-ticket/mysql-bootstrap:$TASK_BOOTSTRAP_TAG" \
  --check-files
```

Build cần network để tải dependencies/CA bundle. Lệnh `--check-files` sau build không kết nối database hoặc AWS. Các lệnh này dành cho bạn chạy khi cần kiểm tra; chúng không phải kết quả của một lần integration test đã chạy.

## Lifecycle

Bootstrap task chạy xong phải thoát.

Các container kiểm tra dùng `--rm`, không tạo tài nguyên AWS.
Image Local giữ lại chỉ sử dụng ổ đĩa máy phát triển.

## Local integration test với MySQL 8.4 và TLS

Script:

```text
scripts/test_mysql_bootstrap_integration.py
```

Yêu cầu Docker, Python 3 và OpenSSL.

Chọn đúng bootstrap image có tag commit:

```bash
read -r -p "Bootstrap image: " TASK_MYSQL_BOOTSTRAP_IMAGE

python3 scripts/test_mysql_bootstrap_integration.py \
  --image "$TASK_MYSQL_BOOTSTRAP_IMAGE"
```

Test sử dụng:

- MySQL 8.4 container riêng.
- Docker internal network, không publish port.
- CA và server certificate tạm.
- Hostname verification với SAN `mysql`.
- Credentials ngẫu nhiên chỉ dành cho test.
- tmpfs cho MySQL data directory.

Các kiểm tra:

1. TLS connection với certificate hợp lệ.
2. Bootstrap schema và app user lần đầu.
3. Chạy lại không chạy migration hoặc seed.
4. Sai app password bị từ chối.
5. Sai admin password bị từ chối.
6. Certificate hostname mismatch bị từ chối.
7. Credentials ban đầu vẫn hoạt động.
8. App user có DML trên business tables, không có DDL hoặc quyền đọc ledger/system users.

Script không gọi AWS APIs hoặc sử dụng database volumes của dự án.

### Cleanup khi bị gián đoạn

```bash
docker ps -aq \
  --filter "label=com.flash-ticket.test=mysql-bootstrap" \
  | xargs -r docker rm --force --volumes

docker network ls -q \
  --filter "label=com.flash-ticket.test=mysql-bootstrap" \
  | xargs -r docker network rm
```
