# AWS dev: bật, dừng và xóa

Chạy tại root repository trong Bash/WSL. Hai entrypoints thường dùng là `scripts/aws-dev-up.sh` và `scripts/aws-dev-down.sh`; chúng gọi controller Python hiện có. Cần Git, Python 3, Docker, Terraform từ 1.11, AWS CLI v2 và OpenSSH client. Profile `flash-ticket-terraform` phải truy cập đúng account, region `us-east-1`; Terraform workspace dùng `default`.

Thiết lập lần đầu hoặc cần hiểu/debug từng bước: đọc [deployment quickstart](DEPLOY-DEV-QUICKSTART.md). Không chạy hai controller đồng thời trên cùng environment/state.

## Bật hoặc dựng lại

Nếu hạ tầng/cấu hình còn tồn tại:

```bash
bash scripts/aws-dev-up.sh
```

Nếu dựng mới và chưa lưu SES sender:

```bash
read -r -p 'SES sender email: ' TASK_SES_SENDER
bash scripts/aws-dev-up.sh --sender "$TASK_SES_SENDER"
```

Controller kiểm tra account/state, chuẩn bị foundation/SSM, kiểm tra SES, tái sử dụng hoặc build/push năm ECR images, tái sử dụng/bake AMI khi cần, dựng data, bootstrap lúc app đã dừng, bật ECS và publish frontend. Nó có thể start lại RDS/MongoDB instance hiện có nếu chúng bị stop thủ công; không tự thay instance đang deleting/terminated.

Mở email xác minh SES nếu controller yêu cầu rồi chạy lại cùng lệnh. Foundation đã tạo vẫn có thể tồn tại khi stage dừng. Khi app đang hoạt động, `up` bỏ qua bootstrap/seed để giữ dữ liệu; khi app đã dừng, bootstrap kiểm tra hiện trạng và không reset inventory đã bán.

`up` ưu tiên image tag đã lưu nếu đủ năm images; **không tự coi code mới là đã deploy**. Sau khi sửa backend hoặc bootstrap image, commit source rồi chạy:

```bash
bash scripts/aws-dev-up.sh --rebuild-images
```

Image tag là `dev-1.0.0-<git-sha>`, working tree phải sạch khi build. Frontend luôn được build/publish theo API/Cognito configuration hiện tại. Thay đổi docs thuần túy không yêu cầu rebuild backend.

## Chọn đúng mức dừng/xóa

| Lệnh | Tác động | Phần còn lại |
|---|---|---|
| `bash scripts/aws-dev-down.sh stop` | Scale bốn ECS services về 0 và chờ stable | Database/EC2 vẫn chạy; toàn bộ hạ tầng/storage giữ nguyên |
| `bash scripts/aws-dev-down.sh runtime` | Destroy runtime: VPC, ECS/API và data layer | Foundation, SQS/DLQ, ECR/S3, Cognito/CloudFront, SSM, AMI/snapshots và backend state |
| `bash scripts/aws-dev-down.sh all` | Runtime → project AMI/snapshots → foundation → SSM → state bucket | Local source/secrets/state backup, IAM đăng nhập, Budget, tài nguyên ngoài phạm vi quản lý |

**`runtime` và `all` xóa dữ liệu MySQL/MongoDB của dev; cấu hình hiện tại bỏ qua RDS final snapshot.** `all` còn xóa objects/ECR images trong các tài nguyên bị cleanup. Đây không phải lệnh pause hay backup.

Terraform hiển thị plan và yêu cầu xác nhận cho apply/destroy. `--yes` là lựa chọn bật auto-approve cho lần chạy, không phải preview. `down stop` không gọi Terraform apply và không cần cờ đó.

Nếu cleanup lỗi: giữ state, đọc thông báo và chạy lại cùng mode sau khi sửa nguyên nhân. Không chạy `up` để chữa lỗi cleanup, không xóa state bucket/state bằng tay trước runtime/foundation. Bước cleanup preparation chỉ cho phép cập nhật cờ xóa ở đúng S3/ECR resources trong state; plan có create/delete/replacement hoặc thay đổi không liên quan bị từ chối.

## Dựng lại sau khi xóa runtime

SQS/DLQ ở foundation vẫn có thể chứa message của database cũ. `up` từ chối khởi tạo data mới khi phát hiện jobs cũ. Nếu chủ động bỏ lượt test cũ:

```bash
bash scripts/aws-dev-up.sh --purge-queues
```

Cờ này chỉ dành cho data/runtime đã bị xóa, app không chạy. Nó **xóa message cả booking queue và DLQ**, chờ 60 giây cho SQS purge; không dùng để bỏ backlog đang cần xử lý. SQS counters là approximate: nếu muốn bỏ hoàn toàn lượt test cũ, dùng cờ này kể cả counter đang 0.

Dựng database mới không restore bookings cũ. PDF còn trong S3 không đủ để phục hồi booking. Sau `all`, dùng lại `up --sender ...`; URLs mới do lần triển khai mới in ra thay thế URLs cũ.

## Kiểm tra sau khi bật

```bash
python3 scripts/deploy_dev.py status

TASK_API_URL="$(terraform -chdir=infra/environments/dev output -raw api_url)"
curl --fail-with-body --silent --show-error "$TASK_API_URL/events"

terraform -chdir=infra/environments/dev-foundation output -json web \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["frontend_url"])'
```

API root `/` có thể trả 404 vì không có route; kiểm tra `/events`, không tự thêm `/api`. Trên frontend: đăng nhập Cognito, đổi email nhận vé mặc định thành recipient phù hợp, đặt một vé, xem `ISSUED`, tải PDF và kiểm tra email.

SES sandbox cần sender và recipient verified. Cognito email verification không thay thế SES verification. `email_status=SENT` nghĩa là SES đã chấp nhận request, không bảo đảm vào inbox; kiểm tra spam/bounce/delivery khi cần.

## State, backup và chi phí

| Terraform root | State | Quản lý |
|---|---|---|
| `infra/bootstrap` | Local | S3 state bucket |
| `infra/environments/dev-foundation` | S3: `environments/dev-foundation/terraform.tfstate` | ECR/IAM/S3/SQS/SES/Cognito/CloudFront |
| `infra/environments/dev` | S3: `environments/dev/terraform.tfstate` | VPC, ECS/API và data layer/builder |

SSM và project AMI cleanup được script quản lý thêm ngoài hai runtime/foundation states. Giữ bản sao an toàn của `infra/bootstrap/terraform.tfstate`/backup, các tfvars/backend.hcl local và `.secrets/aws-dev-secrets.json`. Clone Git không phục hồi những file bị ignore. Nếu mất local state nhưng AWS resources còn, khôi phục/import state trước khi dựng mới.

Không có NAT Gateway/ALB không có nghĩa lab miễn phí. Fargate/Spot, public IPv4, RDS, Redis, EC2/EBS, AMI snapshots, ECR/S3, CloudWatch và requests có thể tiêu credit. `stop` giữ DB nodes chạy; `runtime` giữ foundation/storage; sau `all` vẫn kiểm tra cleanup/Billing vì tài nguyên ngoài state không tự bị xóa và Billing có độ trễ.

RDS stop vẫn tính storage và tự khởi động lại sau tối đa bảy ngày; Redis không có stop; EC2 stop vẫn giữ EBS. AWS Budget chỉ cảnh báo. Repo có CloudWatch Logs, **chưa triển khai SNS alerts/CI/CD/load testing production**.
