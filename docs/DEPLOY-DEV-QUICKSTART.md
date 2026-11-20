# AWS dev deployment theo từng stage

Đây là hướng dẫn giải thích/debug bộ triển khai hiện có. Vận hành thường ngày dùng [AWS dev up/down](AWS-DEV-POWER.md); không trộn thay đổi AWS thủ công với controller khi chưa đối chiếu Terraform state.

## 1. Điều kiện trước khi deploy

- Chạy tại root repository trong Bash/WSL, Git working tree sạch nếu build image.
- Có Python 3, Git, Docker đang chạy, Terraform từ 1.11, AWS CLI v2 và OpenSSH client.
- Profile `flash-ticket-terraform`, region `us-east-1`, workspace `default`; đăng nhập theo phương thức đã cấu hình cho profile. Nếu dùng profile phiên đăng nhập `flash-ticket-dev`, renew phiên đó trước khi profile Terraform tham chiếu tới nó.
- Giữ state/secrets backup theo [runbook](AWS-DEV-POWER.md#state-backup-và-chi-phí). Mất local bootstrap state không được coi là một account trống.

```bash
git status --short
aws sts get-caller-identity --profile flash-ticket-terraform --region us-east-1
```

Dùng shell AWS riêng, không mang credentials/endpoint giả từ lab LocalStack. Controller từ chối các biến `AWS_ENDPOINT_URL*` có giá trị. Kiểm tra và bỏ chúng trước khi dùng AWS thật:

```bash
unset AWS_ENDPOINT_URL AWS_ENDPOINT_URL_S3 AWS_ENDPOINT_URL_SQS AWS_ENDPOINT_URL_SES
```

Không đổi region/profile chỉ bằng một biến ngẫu nhiên: helpers hiện được xây cho cấu hình dev trên. Các thao tác sau tạo tài nguyên thật và tiêu credit. Terraform hỏi xác nhận; `--yes` bật auto-approve cho stage đang gọi.

## 2. Thứ tự triển khai mới

| Stage | Thao tác |
|---|---|
| `prepare --sender ...` | State bucket, foundation, SSM; Cognito/CloudFront/SES |
| `images` | Build/push Booking, Catalog, Relay, Worker, MySQL bootstrap lên ECR |
| `bake` | Public EC2 builder tạm, bake AMI MongoDB, xóa builder sau thành công |
| `data` | RDS, Redis, MongoDB EC2 private và Instance Connect Endpoint |
| `bootstrap` | ECS/API/Cloud Map, giữ services ở 0; migrations/users/seeds/projection |
| `start` | SES/SSM checks, bật bốn services và smoke test `/events` |
| `frontend` | React build với API/Cognito configuration; upload S3/CloudFront |
| `status` | Trạng thái triển khai và URLs |

Thực hiện tuần tự, dừng nếu stage lỗi:

```bash
read -r -p 'SES sender email: ' TASK_SES_SENDER
python3 scripts/deploy_dev.py prepare --sender "$TASK_SES_SENDER"
```

Mở email xác minh SES trước khi đi tiếp. `prepare` tạo account/SES/backend configuration local cần thiết; nó không tự ghi đè cấu hình khác account/sender. `.secrets/aws-dev-secrets.json` giữ secrets để khôi phục SSM sau cleanup, không commit hoặc xóa file này tùy tiện.

```bash
python3 scripts/deploy_dev.py images
python3 scripts/deploy_dev.py bake
python3 scripts/deploy_dev.py data
python3 scripts/deploy_dev.py bootstrap
python3 scripts/deploy_dev.py start
python3 scripts/deploy_dev.py frontend
python3 scripts/deploy_dev.py status
```

Build trước khi tạo data để DB không chạy chờ tải dependency. Nếu stage lỗi, tài nguyên tạo trước đó vẫn có thể còn; không mở thêm một controller để chạy cạnh tranh. Resume đúng stage sau khi đọc lỗi/state, hoặc chọn cleanup rõ ràng.

## 3. Images và Git

`images` yêu cầu source đã commit và working tree sạch. Tag `dev-1.0.0-<12-character-git-sha>` dùng cho cả năm images, build `linux/amd64` từ root context bằng Dockerfiles dưới `services/*/Dockerfile` và `tools/mysql-bootstrap/Dockerfile`.

Stage tự lấy ECR token qua stdin, dùng Docker config tạm để tránh Windows credential helper trong WSL, build rồi push. Tag đã tồn tại được tái sử dụng; không ghi đè image của cùng commit. Không dùng `latest`. Sau sửa backend/bootstrap/shared/migration, review/commit/push trên branch làm việc rồi chạy lại `images`, hoặc `aws-dev-up.sh --rebuild-images`.

Lệnh tương đương để học cách build/push một service, sau khi foundation sẵn sàng:

```bash
TASK_REGISTRY="$(terraform -chdir=infra/environments/dev-foundation output -raw ecr_registry_url)"
TASK_REPOSITORY="$(terraform -chdir=infra/environments/dev-foundation output -json ecr_repository_urls \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["booking"])')"
TASK_TAG="dev-1.0.0-$(git rev-parse --short=12 HEAD)"
aws ecr get-login-password --profile flash-ticket-terraform --region us-east-1 \
  | docker login --username AWS --password-stdin "$TASK_REGISTRY"
docker build --platform linux/amd64 --file services/booking/Dockerfile \
  --tag "$TASK_REPOSITORY:$TASK_TAG" .
docker push "$TASK_REPOSITORY:$TASK_TAG"
```

Đây là thao tác thủ công cho một image; thông thường dùng stage `images` để cập nhật đủ bộ và lưu tag vào runtime flags. Không chạy lại ví dụ sau khi stage đã publish xong. CI/CD tự động và production release pipeline chưa được triển khai.

## 4. Network, AMI và bootstrap

VPC có Public, Private App, Private Data subnets trong hai AZ. Dev không NAT/ALB: ECS task dùng public subnet/public IP cho egress, nhưng API inbound chỉ từ VPC Link SG; workers không inbound. Data nodes private. Catalog/Booking đăng ký Cloud Map; API Gateway khám phá endpoint và kết nối qua VPC Link tới task private IP. Cloud Map không proxy HTTP.

Builder public cài sẵn MongoDB, được controller kiểm tra qua SSM trước khi tạo AMI; `stopped` một mình không chứng minh bake thành công. AMI không chứa application passwords. `bake` từ chối khi runtime/data đã tồn tại để tránh vô tình đổi flags ảnh hưởng dữ liệu. Nếu bake thất bại, builder có thể còn tính phí; xem log/SSM, sửa rồi resume hoặc cleanup runtime. Không dùng script bake console-marker cũ.

`data` bật RDS/Redis/MongoDB đơn node; từ stage này database đang tính phí. MongoDB dùng SCRAM và SG, app connection chưa có TLS; SSH quản trị được mã hóa nhưng không thay thế MongoDB TLS. MySQL dùng CA xác minh hostname; Redis dùng `rediss://`.

`bootstrap` giữ bốn app services ở 0 trong lúc:

1. One-off MySQL task chạy migrations/checksum ledger, tạo app user DML và inventory seed.
2. Controller xác nhận task đã `STOPPED` với `exitCode=0`, không chỉ kiểm tra task kết thúc.
3. Tạo MySQL/Redis application URIs trong SSM, không in password hoặc truyền secret trong command arguments.
4. MongoDB provisioning qua Instance Connect Endpoint/SSH tạo user và publish URI.
5. Catalog seed và Redis reconciliation bằng one-off tasks.

One-off tasks dùng Fargate On Demand platform 1.4.0, không dùng Spot. Demo metadata/inventory cùng UUID `11111111-1111-4111-8111-111111111111`; seed chạy lại không reset vé. Chi tiết: [MySQL bootstrap](mysql-bootstrap.md), [MongoDB bootstrap](mongodb-bootstrap.md).

## 5. Kiểm chứng luồng người dùng

`start` kiểm tra SES sender/SSM, chờ ECS stable và smoke test demo `/events` với inventory projection hợp lệ. Nó không thay thế thử một booking thật.

Mở frontend URL controller in ra, đăng nhập Cognito bằng Authorization Code + PKCE. Trong SES sandbox dùng recipient đã verified; email Cognito được xác nhận chưa chắc đã verified SES. Đổi email mặc định `buyer@example.com` trong form sang địa chỉ phù hợp.

Đặt vé → Booking commit transaction → Relay poll outbox/Redis/SQS → Worker long poll SQS/PDF/S3/SES → `ISSUED` và download. `SENT` chỉ là SES accepted, chưa bảo đảm inbox. Xác minh PDF tải được và tồn kho cập nhật.

Frontend build trong `node:22-alpine` bằng UID/GID local, `npm ci`; `VITE_*` là public API/Cognito config. Hashed assets cache immutable, `index.html` no-cache; assets cũ giữ lại để browser đang mở phiên bản trước vẫn tải được. Frontend S3 private qua CloudFront OAC; assets banner dùng OAC riêng prefix, ticket không public.

## 6. Lỗi và resume

- **Foundation output thiếu:** kiểm tra đúng backend/workspace. Chạy lại `prepare` với cùng sender sau khi đọc plan; không tạo ECR thủ công để bỏ qua state.
- **Initialization task lỗi:** xem ECS stopped reason/CloudWatch Logs; không bật services bằng desired count thủ công khi bootstrap chưa xong. Sửa rồi resume `bootstrap` với image tag đã publish.
- **MongoDB `/run/sshd` trên AMI cũ:** chạy `python3 scripts/repair_mongodb_bootstrap.py`; recovery kiểm tra cloud-init và mongod. Khi thành công chạy provisioning/bootstrap; không thay instance chỉ để bỏ qua lỗi.
- **Credentials hết hạn:** renew AWS profile và xác minh STS identity, rồi resume stage. Không đưa credentials local giả vào ECS.
- **Spot thiếu capacity:** review `use_fargate_spot=false` trong runtime tfvars và chạy `start`; chi phí tăng, không tự tạo HA.
- **Terraform saved plan stale/backend đổi:** đọc state/plan mới; không force-unlock nếu chưa xác minh không có process giữ lock.

Sau thay đổi secret SSM cần task mới để container nhận giá trị inject mới. Giữ cùng passwords khi DB còn tồn tại; provisioners không tự rotate credentials sai.

## 7. Kết thúc buổi học

Dùng [runbook up/down](AWS-DEV-POWER.md) cho ba mode `stop`, `runtime`, `all`, việc purge jobs khi reset DB và state backup. Đừng coi việc đóng terminal hoặc scale ECS về 0 là đã ngừng chi phí data/storage. Production HA, autoscaling, GitHub Actions, SNS alerts và load testing chưa được hoàn tất bởi các stage này.
