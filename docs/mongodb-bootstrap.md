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

Ở bước tiếp theo, script quản trị sẽ:

1. Đọc MongoDB passwords từ SSM SecureString.
2. Upload bootstrap.js lên private EC2 qua SSH.
3. Truyền JSON credentials qua SSH stdin.
4. Chạy mongosh trên private EC2.
5. Lưu Catalog connection string vào SSM.

AWS hiện chưa cần được tạo lại để chạy bài kiểm tra Local này.