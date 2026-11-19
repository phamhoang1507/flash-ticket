# Flash-Ticket

Hệ thống đặt vé sự kiện phục vụ việc học Microservices, AWS, Docker,
Terraform và CI/CD.

Mục tiêu thiết kế là xử lý flash sale có spike traffic, chống bán lố vé
và phát hành vé bất đồng bộ. Khả năng chịu tải cần được kiểm chứng bằng
load testing; cấu hình dev không đại diện cho năng lực production.

## 1. Các thành phần

| Thành phần | Công nghệ | Trách nhiệm |
|---|---|---|
| Frontend | React + Vite | Hiển thị sự kiện, đặt vé, theo dõi và tải vé |
| Booking | FastAPI + MySQL | Quản lý inventory, booking và transactional outbox |
| Catalog | FastAPI + MongoDB + Redis | Metadata sự kiện và inventory projection |
| Outbox Relay | Python worker | Đọc committed outbox, cập nhật Redis, gửi SQS |
| Ticket Worker | Python worker | Nhận SQS, tạo PDF/QR, upload S3, cập nhật booking, gửi SES |

Booking và Catalog mở HTTP.

Outbox Relay và Ticket Worker chạy background, không mở HTTP port.

## 2. Luồng đặt vé

1. Người dùng chọn sự kiện trên Frontend.
2. Frontend gửi request tạo booking cùng access token và Idempotency-Key.
3. Booking mở một MySQL transaction.
4. Conditional update trừ inventory nếu còn đủ vé.
5. Booking tạo đơn PENDING và ghi outbox trong cùng transaction.
6. Transaction commit.
7. Relay đọc outbox đã commit.
8. Relay cập nhật versioned inventory projection trên Redis.
9. Relay gửi message tới SQS.
10. Worker nhận message và tạo PDF/QR.
11. Worker upload file vào S3 private.
12. Worker cập nhật ticket state và gửi email qua SES.
13. Worker xóa SQS message sau khi xử lý thành công.

MySQL là nguồn dữ liệu chính xác của inventory.

Redis là projection có độ trễ; số vé hiển thị trên giao diện không phải
cam kết đặt vé thành công.

SQS Standard và Outbox Relay có thể tạo duplicate delivery. Các bước
xử lý sử dụng idempotency và database state để hạn chế xử lý trùng.

SES vẫn có khả năng gửi email trùng nếu gửi thành công nhưng tiến trình
dừng trước khi ghi nhận kết quả vào database.

## 3. API chính

| Service | Endpoint | Chức năng |
|---|---|---|
| Catalog | GET /events | Danh sách sự kiện |
| Catalog | POST /events | Tạo sự kiện dạng draft, yêu cầu quyền quản trị |
| Booking | POST /bookings | Đặt vé |
| Booking | GET /bookings/{booking_id} | Xem booking thuộc người dùng hiện tại |
| Booking | GET /bookings/{booking_id}/ticket | Nhận presigned download URL |

booking_id có định dạng UUID, không phải số nguyên tăng dần.

## 4. Cấu trúc thư mục

```text
flash-ticket/
├── frontend/
├── services/
│   ├── booking/
│   ├── catalog/
│   ├── relay/
│   └── worker/
├── shared/
├── database/
├── scripts/
├── assets/
├── infra/
│   ├── bootstrap/
│   ├── modules/
│   │   ├── mongo_ami_builder//
│   │   ├── vpc/
│   │   ├── ecr/
│   │   ├── iam/
│   │   ├── task_roles/
│   │   ├── assets/
│   │   ├── messaging/
│   │   ├── ses/
│   │   └── data_layer/
│   └── environments/
│       ├── dev-foundation/
│       └── dev/
├── docs/
│   └── aws-operations.md
├── .env
└── .secrets/
```

`.env`, `.secrets/`, Terraform state, plan files và các file cấu hình
riêng của môi trường không được commit.

`.terraform.lock.hcl` phải được commit.

## 5. Công cụ cần có

- Git.
- Docker Engine hoặc Docker Desktop có WSL integration.
- Docker Compose v2.
- Python 3 để chạy các script quản trị.
- AWS CLI v2 hỗ trợ `aws login`.
- Terraform từ 1.11 trở lên.
- OpenSSL cho bộ khóa JWT Local.

Kiểm tra:

```bash
git --version
docker version
docker compose version
python3 --version
aws --version
terraform version
openssl version
```

## 6. Vận hành Local

Các lệnh trong phần này áp dụng cho bản Local đã có `.env`, RSA keys
và các tài nguyên bootstrap của MySQL, MongoDB, Redis và LocalStack.

Chạy lệnh từ root repository.

### Kiểm tra Compose

Lệnh này kiểm tra cấu hình mà không in toàn bộ environment:

```bash
docker compose config --quiet
```

### Build và khởi động

```bash
docker compose up -d --build
```

Kiểm tra:

```bash
docker compose ps
```

Xem port được publish:

```bash
docker compose port frontend 8080
docker compose port booking 8000
docker compose port catalog 8000
```

Khi mở trình duyệt trên cùng máy, sử dụng `localhost` cùng host port
được hiển thị.

### Xem logs

```bash
docker compose logs --tail=100 -f booking catalog relay worker
```

Nhấn Ctrl+C để thoát xem logs. Thao tác này không dừng container.

### Tạo access token Local

Private key được đọc từ stdin, không cần mount vào container:

```bash
TOKEN="$(
  docker compose run --rm -T --no-deps booking \
    python scripts/local_token.py \
    --subject local-user \
    < .secrets/private.pem
)"
```

Tạo token có quyền quản trị sự kiện:

```bash
ADMIN_TOKEN="$(
  docker compose run --rm -T --no-deps booking \
    python scripts/local_token.py \
    --subject local-admin \
    --admin \
    < .secrets/private.pem
)"
```

Token Local có thời hạn. Khi token hết hạn, tạo token mới.

Không commit token hoặc private key.

### Dừng và giữ dữ liệu

```bash
docker compose stop
```

Khởi động lại các container đã tồn tại:

```bash
docker compose start
```

Nếu đã thay đổi source code hoặc cấu hình:

```bash
docker compose up -d --build
```

### Xóa container, giữ named volumes

```bash
docker compose down --remove-orphans
```

Khởi động lại:

```bash
docker compose up -d --build
```

Lệnh `down` không kèm `--volumes` giữ lại named volumes.

### Xóa cả container và dữ liệu trong Compose volumes

Chỉ chạy khi chấp nhận reset dữ liệu Local:

```bash
docker compose down --volumes --remove-orphans
```

Lệnh này không xóa tài nguyên AWS.

LocalStack hiện sử dụng `PERSISTENCE=0`, vì vậy cần chạy lại bước
bootstrap S3/SQS/SES sau khi LocalStack bị khởi tạo lại. Việc giữ
database volumes không có nghĩa là tài nguyên LocalStack cũng được giữ.

## 7. Cấu hình ứng dụng

| Cấu hình | Local | AWS |
|---|---|---|
| AUTH_PROVIDER | jwt | cognito |
| AWS_REGION | us-east-1 | us-east-1 |
| AWS_ENDPOINT_URL | LocalStack endpoint | Không cấu hình |
| S3_PUBLIC_ENDPOINT | Endpoint LocalStack mà trình duyệt truy cập được | Không cấu hình |
| ASSETS_PUBLIC_BASE_URL | Theo cấu hình Local | CloudFront URL khi được triển khai |
| AWS credentials | Credentials giả cho LocalStack | ECS Task Role |
| Database secrets | `.env` phục vụ Local | SSM SecureString |
| Ticket signing secret | Secret riêng của Local | Secret riêng của AWS dev |

Không đưa credentials giả của LocalStack vào ECS.

Không đưa AWS access keys cố định vào Docker image.

## 8. Trạng thái cấu hình AWS

Các phần đã có cấu hình:

- S3 backend lưu Terraform state.
- VPC hai Availability Zones.
- Public, Private App và Private Data subnets.
- Security Groups theo luồng kết nối.
- ECR repositories.
- ECS Execution Roles và Task Roles.
- S3 assets private.
- SQS main queue và DLQ.
- SES email identity.
- SSM Standard SecureString secrets.
- RDS MySQL private, cấu hình Single-AZ cho dev.
- ElastiCache Redis.
- EC2 MongoDB private và EC2 Instance Connect Endpoint.

Các phần triển khai tiếp theo:

- EC2 MongoDB.
- ECS services.
- API Gateway, VPC Link và Cloud Map.
- Cognito.
- S3 frontend và CloudFront.
- GitHub Actions.
- Monitoring và load testing.

Có cấu hình Terraform không có nghĩa là tài nguyên đang chạy.
Kiểm tra bằng Terraform state và AWS CLI.

## 9. Vận hành AWS

Xem hướng dẫn đầy đủ:

[Hướng dẫn AWS và Terraform](docs/aws-operations.md)

Runbook bao gồm:

- Khởi tạo từ đầu.
- Tạo lại runtime sau khi destroy.
- Stop/start RDS.
- Destroy runtime.
- Xóa foundation.
- Xóa SSM secrets.
- Xóa Terraform state bucket.
- Kiểm tra tài nguyên còn lại.

## 10. Quy tắc image và triển khai

Tag dev:

```text
dev-1.0.0-<12-character-git-sha>
```

Tag prod:

```text
prod-1.0.0
```

Không sử dụng `latest` làm deployment reference.

ECR repositories sử dụng immutable tags. Khi code ứng dụng thay đổi,
tạo tag mới thay vì ghi đè tag cũ.

Chỉnh sửa tài liệu không yêu cầu build lại image.

## 11. Những thao tác không tương đương nhau

| Thao tác | Tác động |
|---|---|
| Đóng terminal | Không dừng Docker hoặc tài nguyên AWS |
| AWS logout | Xóa phiên đăng nhập, không dừng tài nguyên |
| Docker Compose stop | Dừng container Local |
| RDS stop | Dừng DB compute tạm thời, vẫn giữ storage |
| Terraform destroy dev | Xóa tài nguyên trong state runtime dev |
| Terraform destroy foundation | Xóa tài nguyên trong state foundation |
| Xóa repository trên máy | Không xóa tài nguyên AWS |

AWS Budget gửi cảnh báo; Budget hiện tại không tự động tắt tài nguyên.

- [MongoDB user bootstrap và Local checks](docs/mongodb-bootstrap.md)

- [MySQL bootstrap image và migration policy](docs/mysql-bootstrap.md)