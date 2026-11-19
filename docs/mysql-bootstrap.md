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

```bash
docker run --rm \
  --network none \
  flash-ticket/mysql-bootstrap:dev-1.0.0-check \
  --check-files
```

Đây là kiểm tra files và CA bundle, không kết nối database.

## Lifecycle

Bootstrap task chạy xong phải thoát.

Các container kiểm tra dùng `--rm`, không tạo tài nguyên AWS.
Image Local giữ lại chỉ sử dụng ổ đĩa máy phát triển.