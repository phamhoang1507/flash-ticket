# AWS Operations Runbook — Flash-Ticket Dev

Region: `us-east-1`.

Chạy tất cả lệnh từ root repository:

```bash
cd ~/chatgpt/flash-ticket
```

Tài liệu phản ánh cấu hình đến bước RDS MySQL. Chưa có ECS services
được triển khai trong phạm vi runbook hiện tại.

## 1. Chọn thao tác phù hợp

| Nhu cầu | Thao tác | Dữ liệu RDS | Chi phí còn lại |
|---|---|---|---|
| Nghỉ vài giờ, giữ database | Stop RDS | Giữ | Storage, backup và tài nguyên khác |
| Học tiếp sau khi stop | Start RDS | Giữ | Compute chạy trở lại |
| Nghỉ lâu, chấp nhận reset database | Destroy dev | Xóa theo cấu hình dev | Foundation và state bucket |
| Tạo lại sau destroy dev | Apply dev | Database mới | Runtime được tính phí trở lại |
| Kết thúc lab, xóa assets/images/queues | Destroy foundation sau dev | Runtime phải xóa trước | State bucket và SSM nếu còn |
| Dọn toàn bộ phần đã cấu hình | Xóa dev, foundation, SSM, bootstrap | Xóa | Kiểm tra tài nguyên ngoài state |

Terraform không có lệnh `stop` chung cho cả môi trường.

`apply` tạo hoặc cập nhật cấu hình.
`destroy` xóa tài nguyên.
Stop/start RDS là thao tác qua RDS API.

## 2. Các Terraform state

| Root directory | State | Quản lý |
|---|---|---|
| `infra/bootstrap` | Local state | S3 bucket chứa remote state |
| `infra/environments/dev-foundation` | S3 remote state | ECR, IAM, assets, SQS, SES |
| `infra/environments/dev` | S3 remote state | VPC, Security Groups, RDS |

Remote state keys:

```text
environments/dev-foundation/terraform.tfstate
environments/dev/terraform.tfstate
```

SSM parameters được quản lý bằng `scripts/aws_dev_secrets.py`,
không thuộc các Terraform state trên.

### Các file phải giữ

- `infra/bootstrap/terraform.tfstate`.
- Các file `terraform.tfvars`.
- Các file `backend.hcl`.
- `infra/environments/dev-foundation/ses.auto.tfvars`.
- `.secrets/aws-dev-secrets.json`.

Các file này không được commit.

Đặc biệt, bootstrap dùng local state. Khi chuyển máy, cần giữ bản sao
an toàn của bootstrap state. Chỉ clone Git không khôi phục file này.

Các `.terraform.lock.hcl` phải được commit.

## 3. Đăng nhập AWS

Đăng nhập bằng profile dành cho người dùng:

```bash
aws login --profile flash-ticket-dev
```

Cấu hình profile bridge cho Terraform:

```bash
aws configure set credential_process \
  "aws configure export-credentials --profile flash-ticket-dev --format process" \
  --profile flash-ticket-terraform

aws configure set region us-east-1 \
  --profile flash-ticket-terraform
```

Kiểm tra:

```bash
aws sts get-caller-identity \
  --profile flash-ticket-terraform \
  --region us-east-1
```

Đối chiếu `Account` với account Flash-Ticket trước khi tiếp tục.

Nếu session hết hạn, chạy lại `aws login`.

Không tạo root access keys để sử dụng với dự án.

## 4. Chuẩn bị cấu hình trên máy mới

Bỏ qua phần tạo file nếu các file cấu hình đã có.

### Account ID

Lấy account hiện tại:

```bash
TASK_AWS_ACCOUNT_ID="$(
  aws sts get-caller-identity \
    --profile flash-ticket-terraform \
    --region us-east-1 \
    --query Account \
    --output text
)"
```

Tạo file account configuration nếu chưa có:

```bash
if [[ "$TASK_AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
  for TASK_TF_ROOT in \
    infra/bootstrap \
    infra/environments/dev-foundation \
    infra/environments/dev
  do
    if [[ ! -f "$TASK_TF_ROOT/terraform.tfvars" && ! -f "$TASK_TF_ROOT/terraform.tfvars.json" ]]; then
      printf 'aws_account_id = "%s"\n' "$TASK_AWS_ACCOUNT_ID" \
        > "$TASK_TF_ROOT/terraform.tfvars"

      chmod 600 "$TASK_TF_ROOT/terraform.tfvars"
    fi
  done
else
  printf '%s\n' "Invalid AWS account ID. Check the AWS login."
fi
```

Các giá trị còn lại sử dụng defaults của module hoặc cấu hình đã lưu
trong `terraform.tfvars`.

### SES sender

Nếu chưa có `ses.auto.tfvars`, chạy:

```bash
if [[ ! -f infra/environments/dev-foundation/ses.auto.tfvars ]]; then
  read -r -p "SES sender email: " TASK_SES_SENDER
  export TASK_SES_SENDER

  python3 - <<'PY'
import json
import os
from pathlib import Path
import re

email = os.environ["TASK_SES_SENDER"].strip()

if not re.fullmatch(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    email,
):
    raise SystemExit("Invalid email address.")

path = Path("infra/environments/dev-foundation/ses.auto.tfvars")
path.write_text(
    "ses_sender_email = " + json.dumps(email) + "\n",
    encoding="utf-8",
)
path.chmod(0o600)
PY

  unset TASK_SES_SENDER
fi
```

Không commit file này.

## 5. Khởi tạo bootstrap

Chỉ cần tạo bootstrap khi state bucket chưa tồn tại.

Nếu bucket đã tồn tại, giữ nguyên bootstrap state hiện có.
Không chạy như một deployment mới khi mất local state; cần khôi phục
state hoặc import tài nguyên trước.

```bash
terraform -chdir=infra/bootstrap init

terraform -chdir=infra/bootstrap validate

terraform -chdir=infra/bootstrap plan \
  -out=bootstrap-up.tfplan

terraform -chdir=infra/bootstrap apply \
  bootstrap-up.tfplan
```

Lấy tên state bucket:

```bash
TASK_STATE_BUCKET="$(
  terraform -chdir=infra/bootstrap output -raw state_bucket_name
)"
```

Tạo backend configuration nếu chưa có:

```bash
for TASK_ENVIRONMENT in dev-foundation dev; do
  TASK_BACKEND_FILE="infra/environments/$TASK_ENVIRONMENT/backend.hcl"

  if [[ ! -f "$TASK_BACKEND_FILE" ]]; then
    cat > "$TASK_BACKEND_FILE" <<EOF
bucket       = "$TASK_STATE_BUCKET"
key          = "environments/$TASK_ENVIRONMENT/terraform.tfstate"
region       = "us-east-1"
profile      = "flash-ticket-terraform"
encrypt      = true
use_lockfile = true
EOF

    chmod 600 "$TASK_BACKEND_FILE"
  fi
done
```

Hai môi trường sử dụng chung bucket nhưng khác state key.

## 6. Tạo hoặc cập nhật foundation

```bash
terraform -chdir=infra/environments/dev-foundation init \
  -backend-config=backend.hcl

terraform -chdir=infra/environments/dev-foundation validate

terraform -chdir=infra/environments/dev-foundation plan \
  -out=foundation-up.tfplan

terraform -chdir=infra/environments/dev-foundation apply \
  foundation-up.tfplan
```

Foundation bao gồm:

- ECR repositories và lifecycle policies.
- ECS Execution Roles.
- ECS Task Roles.
- S3 assets private.
- SQS main queue và DLQ.
- SES email identity.

Kiểm tra state:

```bash
terraform -chdir=infra/environments/dev-foundation state list
```

Xem outputs:

```bash
terraform -chdir=infra/environments/dev-foundation output
```

### Verify SES

Sau khi identity được tạo, mở email verification do AWS gửi và bấm
liên kết xác nhận.

Kiểm tra:

```bash
TASK_SES_SENDER="$(
  terraform -chdir=infra/environments/dev-foundation \
    output -raw ses_sender_email
)"

aws sesv2 get-email-identity \
  --email-identity "$TASK_SES_SENDER" \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query '{Verified:VerifiedForSendingStatus,Status:VerificationStatus}' \
  --output table
```

Kết quả cần có `Verified = True`.

Khi SES còn sandbox, địa chỉ nhận email thử cũng phải được verify.
Có thể dùng cùng địa chỉ đã verify cho sender và recipient.

## 7. Tạo hoặc khôi phục SSM secrets

```bash
python3 scripts/aws_dev_secrets.py \
  --profile flash-ticket-terraform \
  --region us-east-1
```

Script tạo parameter còn thiếu và kiểm tra giá trị đã tồn tại.
Script không tự rotate secrets.

Kiểm tra metadata:

```bash
aws ssm describe-parameters \
  --parameter-filters \
    "Key=Name,Option=BeginsWith,Values=/flash-ticket/dev/" \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'Parameters[].{Name:Name,Type:Type,Tier:Tier,Version:Version}' \
  --output table
```

Giữ file `.secrets/aws-dev-secrets.json` an toàn.

Nếu cả local copy và SSM master password đều mất trong khi RDS vẫn
tồn tại, cần reset hoặc khôi phục mật khẩu có chủ đích. Tạo một giá trị
mới trên SSM không tự thay đổi mật khẩu của RDS.

## 8. Tạo hoặc cập nhật runtime dev

### Cấu hình tiết kiệm

Các giá trị dùng trong `infra/environments/dev/terraform.tfvars`:

```hcl
enable_nat_gateway   = false
enable_multi_az_data = false

rds_instance_class          = "db.t3.micro"
rds_master_password_version = 1

rds_backup_retention_days     = 1
rds_deletion_protection       = false
rds_skip_final_snapshot       = true
rds_final_snapshot_identifier = null
```

Không đặt `rds_master_password` vào file.

### Init và validate

```bash
terraform -chdir=infra/environments/dev init \
  -backend-config=backend.hcl

terraform -chdir=infra/environments/dev validate
```

### Plan và apply

```bash
bash scripts/tf-dev.sh plan \
  -out=dev-up.tfplan
```

Đọc plan trước khi apply. Lệnh sau có thể tạo RDS tính phí:

```bash
bash scripts/tf-dev.sh apply \
  dev-up.tfplan
```

Script tự lấy master password từ SSM.

Mật khẩu đi qua ephemeral variable và `password_wo`, không được lưu
trong Terraform state hoặc saved plan.

Không chạy script với shell tracing để ghi lại secrets.

### Kiểm tra

```bash
terraform -chdir=infra/environments/dev output rds

terraform -chdir=infra/environments/dev state list
```

Lấy RDS identifier:

```bash
TASK_RDS_ID="$(
  terraform -chdir=infra/environments/dev output -json rds \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["identifier"])'
)"
```

Kiểm tra trên AWS:

```bash
aws rds describe-db-instances \
  --db-instance-identifier "$TASK_RDS_ID" \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'DBInstances[0].{Status:DBInstanceStatus,Class:DBInstanceClass,AZ:AvailabilityZone,MultiAZ:MultiAZ,Public:PubliclyAccessible,Encrypted:StorageEncrypted}' \
  --output table
```

Cấu hình dev cần có:

- Status: available.
- Class: db.t3.micro.
- MultiAZ: False.
- Public: False.
- Encrypted: True.

Database mới có tên `flash_ticket`. App user và application schema
cần được bootstrap khi triển khai ứng dụng.

## 9. Nghỉ ngắn: stop RDS và giữ dữ liệu

Lấy identifier nếu đang ở terminal mới:

```bash
TASK_RDS_ID="$(
  terraform -chdir=infra/environments/dev output -json rds \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["identifier"])'
)"
```

Stop:

```bash
aws rds stop-db-instance \
  --db-instance-identifier "$TASK_RDS_ID" \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'DBInstance.{Identifier:DBInstanceIdentifier,Status:DBInstanceStatus}' \
  --output table
```

Chờ:

```bash
aws rds wait db-instance-stopped \
  --db-instance-identifier "$TASK_RDS_ID" \
  --profile flash-ticket-terraform \
  --region us-east-1
```

Kiểm tra:

```bash
aws rds describe-db-instances \
  --db-instance-identifier "$TASK_RDS_ID" \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'DBInstances[0].DBInstanceStatus' \
  --output text
```

Kết quả: `stopped`.

Lưu ý:

- Storage và backup vẫn có thể tính phí.
- RDS tự khởi động lại sau tối đa 7 ngày.
- Stop không xóa database.
- Chỉ chạy stop khi instance đang ở trạng thái phù hợp, thường là available.

## 10. Học tiếp sau khi stop RDS

```bash
TASK_RDS_ID="$(
  terraform -chdir=infra/environments/dev output -json rds \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["identifier"])'
)"
```

Start:

```bash
aws rds start-db-instance \
  --db-instance-identifier "$TASK_RDS_ID" \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'DBInstance.{Identifier:DBInstanceIdentifier,Status:DBInstanceStatus}' \
  --output table
```

Chờ:

```bash
aws rds wait db-instance-available \
  --db-instance-identifier "$TASK_RDS_ID" \
  --profile flash-ticket-terraform \
  --region us-east-1
```

Nếu chỉ stop/start và không đổi cấu hình, không cần Terraform apply.

Nếu cần thay đổi RDS bằng Terraform, nên start và chờ instance
available trước khi thực hiện cập nhật.

## 11. Nghỉ lâu: destroy runtime dev

Thao tác này xóa tài nguyên do state `dev` quản lý.

Với cấu hình dev hiện tại, RDS bị xóa và không tạo final snapshot.

```bash
bash scripts/tf-dev.sh plan \
  -destroy \
  -out=dev-destroy.tfplan
```

Đọc danh sách sẽ bị xóa, sau đó:

```bash
bash scripts/tf-dev.sh apply \
  dev-destroy.tfplan
```

Kiểm tra:

```bash
terraform -chdir=infra/environments/dev state list
```

State không còn managed resources.

Các thành phần vẫn giữ lại:

- ECR images.
- IAM Roles.
- S3 assets.
- SQS queues và messages chưa hết retention.
- SES identity.
- SSM secrets.
- Terraform state bucket.

Giữ master password trên SSM cho đến khi destroy hoàn tất vì wrapper
vẫn cần đọc parameter.

Nếu đã bật deletion protection, cần đổi thành false và apply thay đổi
đó trước khi destroy.

Nếu muốn giữ final snapshot, phải cấu hình
`rds_skip_final_snapshot = false` và cung cấp snapshot identifier hợp lệ
trước khi destroy. Snapshot được giữ lại có thể phát sinh storage cost.

## 12. Tạo lại runtime sau destroy

Nếu foundation, backend và SSM vẫn còn:

```bash
aws login --profile flash-ticket-dev
```

```bash
bash scripts/tf-dev.sh plan \
  -out=dev-up.tfplan
```

```bash
bash scripts/tf-dev.sh apply \
  dev-up.tfplan
```

Không cần tạo lại ECR repositories hoặc push lại images nếu chúng vẫn còn.

Không cần init lại mỗi lần nếu backend và module configuration không đổi.
Chạy init khi dùng máy mới, mất `.terraform/`, thêm module hoặc đổi backend.

Apply chỉ tạo lại hạ tầng. Nó không khôi phục dữ liệu database đã xóa.

Luôn tạo plan mới sau khi destroy hoặc đổi cấu hình.

## 13. Reset dữ liệu dev: xử lý queue cũ

Foundation tồn tại lâu hơn database. Sau khi xóa database, messages trong
SQS vẫn có thể tham chiếu các booking đã mất.

Khi chủ đích reset toàn bộ dữ liệu dev, chỉ purge queues sau khi đã dừng
mọi producer và consumer.

Lấy queue URLs:

```bash
TASK_BOOKING_QUEUE_URL="$(
  terraform -chdir=infra/environments/dev-foundation \
    output -raw booking_queue_url
)"

TASK_BOOKING_DLQ_URL="$(
  terraform -chdir=infra/environments/dev-foundation \
    output -raw booking_dlq_url
)"
```

Xóa toàn bộ messages:

```bash
aws sqs purge-queue \
  --queue-url "$TASK_BOOKING_QUEUE_URL" \
  --profile flash-ticket-terraform \
  --region us-east-1
```

```bash
aws sqs purge-queue \
  --queue-url "$TASK_BOOKING_DLQ_URL" \
  --profile flash-ticket-terraform \
  --region us-east-1
```

Purge có thể mất tới 60 giây và không nên gọi lặp lại trong khoảng này.
Không gửi message mới trong lúc purge.

Purge không xóa queue và không thể khôi phục messages đã xóa.

Main queue hiện giữ message tối đa 4 ngày; DLQ giữ tối đa 14 ngày.

## 14. Build và push ECR images khi cần

Chỉ cần thực hiện khi:

- Có phiên bản ứng dụng mới.
- Foundation và images đã bị xóa.
- Repository chưa có image cần triển khai.

Đăng nhập:

```bash
TASK_REGISTRY="$(
  terraform -chdir=infra/environments/dev-foundation \
    output -raw ecr_registry_url
)"

aws ecr get-login-password \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  | docker login \
      --username AWS \
      --password-stdin "$TASK_REGISTRY"
```

Tạo tag:

```bash
TASK_APP_VERSION="1.0.0"

TASK_GIT_SHA="$(git rev-parse --short=12 HEAD)"

TASK_IMAGE_TAG="dev-${TASK_APP_VERSION}-${TASK_GIT_SHA}"
```

Build và push:

```bash
for TASK_SERVICE in booking catalog relay worker; do
  docker build \
    --platform linux/amd64 \
    --file "services/${TASK_SERVICE}/Dockerfile" \
    --tag "${TASK_REGISTRY}/flash-ticket-dev/${TASK_SERVICE}:${TASK_IMAGE_TAG}" \
    .

  docker push \
    "${TASK_REGISTRY}/flash-ticket-dev/${TASK_SERVICE}:${TASK_IMAGE_TAG}"
done
```

ECR tags là immutable. Không chạy push để ghi đè một tag đã tồn tại.
Dùng image đã có hoặc tạo version/commit tag mới cho bản mới.

Stop/start hoặc tạo lại RDS không yêu cầu rebuild images.

## 15. Kết thúc lab: xóa foundation

Phải destroy runtime dev trước khi xóa foundation.

Các lệnh dưới đây xóa ECR images, S3 assets, SQS messages, IAM Roles
và SES identity thuộc foundation.

Cho phép xóa repositories/bucket có dữ liệu:

```bash
terraform -chdir=infra/environments/dev-foundation apply \
  -var="ecr_force_delete=true" \
  -var="assets_force_destroy=true"
```

Apply trước để cập nhật các force-delete settings vào state.

Tạo destroy plan:

```bash
terraform -chdir=infra/environments/dev-foundation plan \
  -destroy \
  -var="ecr_force_delete=true" \
  -var="assets_force_destroy=true" \
  -out=foundation-destroy.tfplan
```

Xóa:

```bash
terraform -chdir=infra/environments/dev-foundation apply \
  foundation-destroy.tfplan
```

Kiểm tra:

```bash
terraform -chdir=infra/environments/dev-foundation state list
```

Destroy foundation không xóa SSM parameters hoặc bootstrap bucket.

## 16. Xóa SSM secrets

Chỉ thực hiện khi runtime không còn cần secrets.

```bash
aws ssm delete-parameters \
  --names \
    /flash-ticket/dev/data/MYSQL_MASTER_PASSWORD \
    /flash-ticket/dev/data/MYSQL_APP_PASSWORD \
    /flash-ticket/dev/data/MONGODB_ROOT_PASSWORD \
    /flash-ticket/dev/data/MONGODB_APP_PASSWORD \
    /flash-ticket/dev/data/REDIS_AUTH_TOKEN \
    /flash-ticket/dev/worker/TICKET_SIGNING_SECRET \
  --profile flash-ticket-terraform \
  --region us-east-1
```

Lệnh chỉ xóa sáu parameter được liệt kê.

Giữ local secrets file nếu muốn tạo lại cùng giá trị trong lần học sau.
File local không tạo chi phí AWS.

## 17. Xóa Terraform state bucket

Đây là bước cuối cùng.

Chỉ thực hiện sau khi:

1. Runtime dev đã được destroy.
2. Foundation đã được destroy.
3. Không còn Terraform operation đang chạy.
4. Không còn state khác cần giữ trong bucket.

Cho phép xóa bucket có dữ liệu và object versions:

```bash
terraform -chdir=infra/bootstrap apply \
  -var="allow_state_bucket_force_destroy=true"
```

Tạo destroy plan:

```bash
terraform -chdir=infra/bootstrap plan \
  -destroy \
  -var="allow_state_bucket_force_destroy=true" \
  -out=bootstrap-destroy.tfplan
```

Xóa:

```bash
terraform -chdir=infra/bootstrap apply \
  bootstrap-destroy.tfplan
```

Thao tác này xóa cả lịch sử remote state trong bucket.

Không xóa state bucket trước rồi mới destroy các môi trường.

## 18. Kiểm tra tài nguyên còn lại

Các lệnh sau liệt kê tài nguyên trong account hoặc Region, có thể bao gồm
tài nguyên ngoài dự án.

### RDS

```bash
aws rds describe-db-instances \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'DBInstances[].{Identifier:DBInstanceIdentifier,Status:DBInstanceStatus}' \
  --output table
```

### NAT Gateways

```bash
aws ec2 describe-nat-gateways \
  --filter Name=state,Values=pending,available,deleting \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'NatGateways[].{Id:NatGatewayId,State:State,Vpc:VpcId}' \
  --output table
```

Với cấu hình dev mặc định, dự án không tạo NAT Gateway.

### Elastic IPs

```bash
aws ec2 describe-addresses \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'Addresses[].{PublicIp:PublicIp,AllocationId:AllocationId}' \
  --output table
```

### ECR

```bash
aws ecr describe-repositories \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'repositories[].repositoryName' \
  --output table
```

### S3

```bash
aws s3 ls \
  --profile flash-ticket-terraform \
  --region us-east-1
```

### SSM

```bash
aws ssm describe-parameters \
  --parameter-filters \
    "Key=Name,Option=BeginsWith,Values=/flash-ticket/dev/" \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --query 'Parameters[].Name' \
  --output table
```

Terraform destroy chỉ xóa tài nguyên trong state tương ứng.
Tài nguyên tạo bằng tay, snapshots giữ lại hoặc tài nguyên tại Region khác
cần được kiểm tra riêng.

## 19. Chi phí cần nhớ

| Thành phần | Điểm cần nhớ |
|---|---|
| RDS running | Có phí compute và storage |
| RDS stopped | Storage và backup vẫn có thể tính phí |
| RDS snapshots | Snapshot giữ lại có thể tính phí |
| ECR | Images giữ lại có phí storage |
| S3 | Có phí storage và requests theo mức sử dụng |
| SQS | Tính theo requests và mức sử dụng áp dụng |
| SES | Tính theo lượng gửi và mức sử dụng áp dụng |
| SSM Standard | Không có phí lưu parameter ở standard throughput |
| KMS | Requests có thể tính phí khi vượt allowance áp dụng |
| IAM Roles | Không có phí duy trì role |
| VPC/Subnets/Security Groups | Không có phí duy trì riêng cho các thành phần này |
| NAT Gateway | Có phí theo giờ và xử lý dữ liệu nếu bật |

Free plan và credits không có nghĩa là usage không được tính.
Chi phí sử dụng có thể làm giảm credits.

AWS Budget hiện tại gửi cảnh báo, không tự stop hoặc destroy tài nguyên.

## 20. Lỗi thường gặp

### ParameterNotFound trước Terraform plan

Wrapper chưa đọc được master password:

```bash
python3 scripts/aws_dev_secrets.py \
  --profile flash-ticket-terraform \
  --region us-east-1
```

Sau đó:

```bash
bash scripts/tf-dev.sh plan \
  -out=dev-up.tfplan
```

Kiểm tra đúng account và Region trước khi tạo lại parameter.

### Credentials hết hạn

```bash
aws login --profile flash-ticket-dev
```

Kiểm tra:

```bash
aws sts get-caller-identity \
  --profile flash-ticket-terraform \
  --region us-east-1
```

### Terraform yêu cầu init sau khi thêm module

```bash
terraform -chdir=infra/environments/dev init \
  -backend-config=backend.hcl
```

Không thêm `-upgrade` chỉ để xử lý việc mới thêm module.
Giữ provider version đã khóa trừ khi chủ đích nâng cấp.

### Saved plan đã cũ

Tạo plan mới với cấu hình và state hiện tại:

```bash
bash scripts/tf-dev.sh plan \
  -out=dev-up.tfplan
```

Sau đó apply plan mới:

```bash
bash scripts/tf-dev.sh apply \
  dev-up.tfplan
```

Không sử dụng lại plan từ trước lần destroy.

## 21. Kết thúc phiên làm việc

Nếu đã dừng hoặc xóa đúng tài nguyên theo nhu cầu, có thể logout:

```bash
aws logout --profile flash-ticket-dev
```

Logout không dừng RDS và không xóa tài nguyên AWS.

Đóng terminal cũng không dừng tài nguyên.