# Flash-Ticket

Hệ thống đặt vé để thực hành Microservices, Transactional Outbox, Docker và triển khai AWS dev. Repository có ứng dụng chạy local và bộ triển khai AWS bằng Terraform/scripts; **chưa có CI/CD GitHub Actions, SNS alerts hoặc load test chứng minh 50.000 users**.

## Bắt đầu từ đâu

| Mục tiêu | Tài liệu |
|---|---|
| Chạy từ clone mới trên máy local | [Local development](docs/LOCAL-DEVELOPMENT.md) |
| Bật, dừng hoặc xóa AWS dev | [AWS dev up/down](docs/AWS-DEV-POWER.md) |
| Hiểu từng stage triển khai, bootstrap và xử lý lỗi | [AWS deployment quickstart](docs/DEPLOY-DEV-QUICKSTART.md) |
| Migration, TLS và application user MySQL | [MySQL bootstrap](docs/mysql-bootstrap.md) |
| MongoDB users, SSH provisioning và kiểm tra credentials | [MongoDB bootstrap](docs/mongodb-bootstrap.md) |

Local cần Docker + Compose v2, Python 3 và OpenSSL. Làm bước tạo `.env`, RSA keys và bootstrap trong local guide trước khi khởi động ứng dụng; `docker compose up` đơn lẻ chưa khởi tạo schema hay S3/SQS/SES.

AWS dev dùng profile `flash-ticket-terraform`, region `us-east-1` và Terraform từ 1.11. Chạy lệnh tại root repository sau khi đọc phạm vi tạo/xóa tài nguyên:

```bash
bash scripts/aws-dev-up.sh
python3 scripts/deploy_dev.py status
bash scripts/aws-dev-down.sh stop
```

`stop` chỉ dừng bốn ECS services. RDS, Redis, MongoDB EC2 và storage vẫn tính phí. Muốn xóa runtime/database dùng `down runtime`; xóa toàn bộ phần AWS do dự án quản lý dùng `down all`. Hai mode xóa làm mất dữ liệu; xem runbook trước khi chạy.

## Các thành phần và luồng dữ liệu

| Thành phần | Trách nhiệm |
|---|---|
| React + Vite | Danh sách sự kiện, đăng nhập, đặt và tải vé |
| Catalog — FastAPI | MongoDB metadata, Redis cache và inventory projection |
| Booking — FastAPI | Atomic inventory update, booking idempotency và outbox MySQL |
| Outbox Relay — background | Poll committed outbox, cập nhật Redis theo version, publish SQS |
| Ticket Worker — background | Long poll SQS, PDF/QR, S3, trạng thái ticket và SES |

Booking commit **inventory + booking PENDING + outbox trong cùng transaction**. Relay và Worker chạy liên tục, không có HTTP port. MySQL quyết định tồn kho; Redis chỉ là projection có độ trễ. Outbox/SQS có thể giao trùng nên consumer phải idempotent. SES có thể gửi lại email nếu process chết sau khi gửi nhưng trước khi lưu kết quả.

API chính: `GET /events`, `POST /events` (tạo DRAFT, scope quản trị), `POST /bookings`, `GET /bookings/{booking_id}`, `GET /bookings/{booking_id}/ticket`. Booking ID là UUID. Request đặt vé có access token và `Idempotency-Key`; vé được tải bằng presigned URL sau khi kiểm tra chủ sở hữu.

## Ranh giới local và AWS

| Thành phần | Local | AWS dev |
|---|---|---|
| Auth | Custom JWT RS256, CLI cấp token | Cognito Authorization Code + PKCE |
| Ingress | Nginx proxy `/api/*` | HTTP API → VPC Link → Cloud Map → task private IP |
| Storage/messaging/email | LocalStack S3/SQS/SES, credentials giả | AWS services và ECS Task Roles |
| Data | MySQL/MongoDB/Redis containers | RDS, private EC2 MongoDB, ElastiCache |
| Secrets | `.env`, JWT keys local | SSM Standard SecureString |
| Network | Compose network | Hai AZ, public/private-app/private-data subnets |

Cấu hình dev không tạo NAT Gateway/ALB, dùng task public IP với SG giới hạn ingress và data nodes đơn. Fargate Spot/`desired_count=1` tiết kiệm nhưng không bảo đảm HA hoặc zero downtime. MongoDB app connection hiện chưa có TLS; SSH quản trị được mã hóa. Các cờ hạ tầng không tự biến toàn bộ hệ thống thành production HA.

## Kiểm tra offline

Unit tests kiểm tra logic với dữ liệu giả và process mocks, không khởi động database hay gọi AWS. Nếu muốn chạy trên host, dùng môi trường Python riêng:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r services/booking/requirements.txt -r services/catalog/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Các integration scripts trong `scripts/test_*` cần Docker và được hướng dẫn riêng ở tài liệu bootstrap. Kết quả unit tests không thay thế load test hoặc kiểm chứng triển khai AWS.

## Thư mục và file cần giữ

```text
frontend/       React, Vite, Nginx
services/       booking, catalog, relay, worker
shared/         Auth, AWS clients, database, inventory projection
database/       SQL migrations bất biến
scripts/        Local initialization và AWS orchestration
infra/          Bootstrap state bucket, foundation, runtime và modules
tools/          MySQL one-off bootstrap image
tests/          Offline và integration checks
docs/           Các hướng dẫn hiện hành
```

Không commit `.env`, `.secrets/`, Terraform state/plan, `backend.hcl` hoặc tfvars chứa cấu hình riêng. Commit lockfiles và migration mới; không sửa migration đã áp dụng. **Giữ bản sao an toàn của local bootstrap state/backup và `.secrets/aws-dev-secrets.json`**: clone Git không phục hồi được chúng.

Sau khi sửa source, review rồi commit/push trên branch làm việc:

```bash
git switch -c chore/maintain-flash-ticket
git status --short
git add README.md docs .env.example .gitignore .dockerignore compose.yaml compose.override.yaml
git diff --cached --check
git diff --cached --name-only
git commit -m "docs: consolidate local and AWS dev workflows"
git push -u origin HEAD
```

Chỉ stage các thay đổi đã review; lệnh trên dành cho nhóm file hướng dẫn/cấu hình, không tự gom mọi thay đổi source. Quy trình build/push Docker Hub có trong local guide; ECR build/push dùng stage `images` theo Git SHA, không phụ thuộc `latest`.
