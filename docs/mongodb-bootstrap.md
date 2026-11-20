# MongoDB User Bootstrap

## Mục đích

Script `scripts/mongodb/bootstrap.js` chạy bằng mongosh trên máy MongoDB.

Script nhận JSON qua stdin với các trường:

| Trường | Ý nghĩa |
|---|---|
| database | Database dành cho Catalog |
| root_username | Admin user trong database admin |
| root_password | Admin password |
| app_username | User dành cho Catalog |
| app_password | Application password |

Không truyền password qua command-line arguments.

## Hành vi

- Database chưa có users: tạo admin bằng localhost exception.
- Admin tồn tại: phải authenticate thành công.
- App user chưa tồn tại: tạo quyền readWrite trên application database.
- App user tồn tại: kiểm tra roles và credentials.
- Credentials không khớp: báo lỗi, không tự rotate password.
- Kiểm tra app user có thể ghi, đọc và xóa một probe document.
- Không in passwords trong bootstrap report.

Script phải chạy trong host/container MongoDB vì kết nối tới localhost.

## Kiểm tra Local

Yêu cầu Docker và Python 3.

```bash
python3 -m py_compile scripts/test_mongodb_bootstrap.py

python3 scripts/test_mongodb_bootstrap.py
```

Test dùng container riêng, không publish port và không sử dụng project volumes.

Các trường hợp kiểm tra:

1. Tạo users lần đầu.
2. Chạy lại với cùng credentials.
3. Từ chối sai admin password.
4. Từ chối sai app password.
5. Credentials cũ vẫn hoạt động.
6. App user không có quyền đọc admin users.

Container tự xóa sau test.

Nếu cần dọn container còn lại:

```bash
docker rm --force flash-ticket-mongo-bootstrap-test
```

## Tích hợp AWS

Wrapper `scripts/provision_mongodb.py` hiện thực hiện:

1. Đọc MongoDB passwords từ SSM SecureString.
2. Upload bootstrap.js lên private EC2 qua SSH.
3. Truyền JSON credentials qua SSH stdin.
4. Chạy mongosh trên private EC2.
5. Lưu Catalog connection string vào SSM.

AWS hiện chưa cần được tạo lại để chạy bài kiểm tra Local này.

## AWS provisioning wrapper

Script:

```text
scripts/provision_mongodb.py
```

### Kiểm tra offline

```bash
python3 scripts/provision_mongodb.py --check-only

python3 tests/test_mongodb_provisioner.py -v
```

Các kiểm tra này không gọi AWS APIs.

### Chạy thật sau khi khôi phục hạ tầng

Chỉ thực hiện khi đã có:

- Terraform backend và runtime state.
- Private MongoDB EC2 đang chạy.
- EC2 Instance Connect Endpoint hoạt động.
- MongoDB service active.
- Hai SecureString parameters:
  - `/flash-ticket/dev/data/MONGODB_ROOT_PASSWORD`
  - `/flash-ticket/dev/data/MONGODB_APP_PASSWORD`

Lệnh:

```bash
python3 scripts/provision_mongodb.py --execute
```

Script không tự apply Terraform hoặc tạo EC2.

Nó đọc secrets từ SSM, bootstrap MongoDB qua SSH rồi ghi URI vào:

```text
/flash-ticket/dev/catalog/MONGODB_URI
```

URI được lưu dưới dạng Standard SecureString.

Script không in URI hoặc passwords.

### Hành vi khi chạy lại

- Credentials đúng, users tồn tại: kiểm tra lại.
- Credentials không khớp: dừng, không tự đổi mật khẩu.
- Bootstrap thất bại: không publish URI.
- URI không đổi: không tạo SSM version mới.
- Node mới có hostname khác: cập nhật URI sau bootstrap thành công.

SSM parameter name không tự trở thành environment variable của ứng dụng.
ECS Task Definition sẽ map parameter này vào cấu hình mà Catalog sử dụng.

Khi secret đã inject vào ECS container thay đổi trên SSM, cần triển khai
task mới để container nhận giá trị mới.

### Cleanup

Parameter nằm trong prefix `/flash-ticket/dev/`, nên được xóa bởi
bước SSM cleanup của runbook.

Các kiểm tra offline không tạo tài nguyên AWS cần destroy.
