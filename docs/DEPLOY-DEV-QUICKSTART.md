# Flash-Ticket: đưa bản dev lên AWS để test toàn bộ luồng

Hướng dẫn này dành cho trường hợp **đã xóa tài nguyên AWS, còn nguyên source code trên máy**. Chạy các lệnh trong WSL tại thư mục `flash-ticket`. Region sử dụng là `us-east-1`; IAM profile là `flash-ticket-terraform`.

Mục tiêu của lượt triển khai này: mở CloudFront URL, đăng ký/đăng nhập qua Cognito, xem sự kiện, đặt vé và nhận PDF qua email. Chưa chuyển sang Phase 4 CI/CD hay Phase 5 load testing.

## 0. Lấy code từ GitHub

Branch `feat/aws-dev-deploy` đã chứa đầy đủ thay đổi; không cần tải ZIP hay chạy installer. Branch được tạo từ `test/mysql-bootstrap-tls` để giữ nguyên các phần đã hoàn thành.

```bash
cd ~/chatgpt/flash-ticket
git status --short
git fetch origin feat/aws-dev-deploy
```

Nếu working tree có thay đổi, commit các file source của bạn trước khi chuyển branch; giữ nguyên `.secrets`, tfvars và state local. Với working tree sạch, tạo branch local riêng để tránh trùng branch bạn đã tạo trước đây:

```bash
git switch -c feat/aws-dev-deploy-local --track origin/feat/aws-dev-deploy
```

Nếu branch local trên đã tồn tại, chuyển sang nó rồi cập nhật bằng fast-forward:

```bash
git switch feat/aws-dev-deploy-local
git pull --ff-only
```

Sau đó làm từ mục 2. Source đã được commit trên remote; mục commit chỉ cần dùng khi bạn sửa code thêm.

## 1. Những gì thay đổi

`scripts/deploy_dev.py` điều phối các lệnh AWS CLI, Terraform và Docker đã có trong dự án. Script dùng Python standard library, không yêu cầu cài thêm Python package. **Mỗi lần gọi chỉ chạy một stage**; nếu một lệnh lỗi, stage dừng ngay.

| Stage | Tài nguyên / tác vụ chính | Kết quả |
| --- | --- | --- |
| `prepare` | State bucket; ECR; IAM; S3; SQS/DLQ; SES; Cognito; CloudFront; khôi phục SSM | Foundation sẵn sàng |
| `images` | Build/push 4 app images và MySQL bootstrap image lên ECR | Cùng một tag gắn Git SHA |
| `bake` | VPC 2 AZ; EC2 public tạm thời để cài MongoDB và tạo private AMI | AMI MongoDB; xóa EC2 builder sau khi thành công |
| `data` | RDS MySQL, ElastiCache Redis, EC2 MongoDB private, Instance Connect Endpoint | Database đang chạy, chưa có app |
| `bootstrap` | Tạo ECS/API Gateway/Cloud Map; giữ 4 service ở `desired_count=0`; chạy các initialization task | Schema, app users, demo event, Redis inventory sẵn sàng |
| `start` | Kiểm tra SES; nâng 4 service lên `desired_count=1`; chờ stable; test `GET /events` | API hoạt động |
| `frontend` | Build React với Cognito/API URL thật; upload S3; invalidate CloudFront | Giao diện có thể mở trong browser |
| `status` | Đọc output và trạng thái ECS | Biết service nào đang chạy |
| `stop` | Đưa 4 ECS service về 0 | Dừng Fargate; data layer vẫn tính phí |
| `destroy` | Xóa runtime trước; tùy chọn xóa toàn bộ foundation và state bucket | Chủ động dọn tài nguyên |

Terraform vẫn hiển thị plan và yêu cầu nhập `yes`. Tùy chọn `--yes` chủ động bật `-auto-approve` **cho stage đang gọi**. Lượt đầu nên đọc plan rồi xác nhận, đặc biệt khi xóa database.

## 2. Chuẩn bị và lưu code trước khi build

Kiểm tra công cụ và IAM identity:

```bash
aws --version
terraform version
docker version
python3 --version
git status --short

aws login --profile flash-ticket-dev

aws sts get-caller-identity \
  --profile flash-ticket-terraform \
  --region us-east-1
```

ARN cần thuộc IAM user/role sử dụng cho lab. Không dùng access key của root.

Profile `flash-ticket-dev` giữ phiên đăng nhập AWS CLI; `flash-ticket-terraform` là profile dùng cho các lệnh hạ tầng đã cấu hình trước đó. Khi phiên đăng nhập hết hạn, chạy lại `aws login --profile flash-ticket-dev`, rồi kiểm tra `get-caller-identity` trước khi deploy.

Nếu đang ở shell từng chạy LocalStack:

```bash
unset AWS_ENDPOINT_URL AWS_ENDPOINT_URL_S3 AWS_ENDPOINT_URL_SQS AWS_ENDPOINT_URL_SES
```

Script cũng từ chối mọi biến `AWS_ENDPOINT_URL*` còn giá trị để tránh gọi nhầm LocalStack.

Code kéo từ branch GitHub đã được commit sẵn. Nếu bạn sửa thêm source trước khi build, lưu thay đổi vào Git theo các lệnh dưới đây; nếu đang có branch làm việc phù hợp, bỏ qua lệnh tạo branch:

```bash
git switch -c feat/aws-dev-deploy

git add \
  .gitignore \
  shared \
  services \
  scripts \
  infra/modules \
  infra/environments \
  docs \
  README.md

git diff --cached --check
git diff --cached --name-only
```

Kiểm tra danh sách staged: không có `.env`, `.secrets`, `*.tfvars`, `*.tfstate`, `*.tfplan`, `backend.hcl` hay private key. Giữ các file này trong `.gitignore`; nếu trước đây đã track nhầm secret thì phải xử lý riêng, `.gitignore` không tự bỏ file đang được track.

```bash
git commit -m "feat: deploy Flash-Ticket dev stack with Cognito and ECS"
git push -u origin HEAD
```

Stage `images` yêu cầu working tree sạch để tag trỏ đúng source đã commit. Các file vận hành được tạo sau đó là file local được ignore:

```text
infra/bootstrap/account.auto.tfvars
infra/environments/dev-foundation/account.auto.tfvars
infra/environments/dev-foundation/ses.auto.tfvars
infra/environments/dev-foundation/backend.hcl
infra/environments/dev/account.auto.tfvars
infra/environments/dev/backend.hcl
infra/environments/dev/launch.auto.tfvars
infra/environments/dev/mongodb-ami.auto.tfvars
.secrets/aws-dev-secrets.json
output/mongo-ami.json
```

## 3. Tạo foundation và xác minh SES

Nhập email của bạn; dùng email này cho cả SES sender và tài khoản Cognito test đầu tiên:

```bash
read -r -p "SES sender email: " TASK_SES_SENDER
python3 scripts/deploy_dev.py prepare \
  --sender "$TASK_SES_SENDER"
```

Script thực hiện theo thứ tự:

1. Đọc source helper hiện có để kiểm tra static contract trước khi tạo tài nguyên: `aws_dev_secrets.py`, `provision_mongodb.py` và `tf-dev.sh`. Không chạy helper để dò `--help`; chỉ truyền optional flag được khai báo trong source. Sau đó đọc account từ IAM profile, kiểm tra account trong các file cấu hình; không tự ghi đè khi account khác nhau.
2. Khôi phục S3 state bucket từ root `infra/bootstrap`. Root này dùng local Terraform state; giữ lại file state và backup trên máy.
3. Khởi tạo S3 backend cho hai root `dev-foundation` và `dev` bằng key riêng.
4. Apply foundation, gồm Cognito và CloudFront.
5. Gọi `scripts/aws_dev_secrets.py` để khôi phục các SSM SecureString từ `.secrets/aws-dev-secrets.json`, hoặc tạo lần đầu khi chưa có.

**Mở email AWS SES và bấm verification link.** Thời gian CloudFront tạo distribution có thể vài phút. Bước này chưa có API hay database.

Không xóa `.secrets/aws-dev-secrets.json` khi dọn AWS: file này giúp khôi phục cùng password khi dựng lại. Không commit hoặc gửi file đó cho người khác.

## 4. Build image trước, rồi tạo AMI MongoDB và data layer

```bash
python3 scripts/deploy_dev.py images
```

Nếu `images` báo thiếu `ecr_repository_urls` hoặc các foundation outputs khác, Terraform state mà script đang đọc chưa có đủ output. Source đã khai báo các output này; kiểm tra state trước khi sửa Terraform hoặc tạo ECR thủ công:

```bash
terraform -chdir=infra/environments/dev-foundation workspace show

terraform -chdir=infra/environments/dev-foundation output -json \\
  | python3 -c 'import json,sys; print("\\n".join(sorted(json.load(sys.stdin))))'
```

Hoàn tất hoặc chạy lại `prepare` với cùng SES sender đã cấu hình, đọc Terraform plan rồi mới chạy `images`:

```bash
read -r -p "SES sender email: " TASK_SES_SENDER
python3 scripts/deploy_dev.py prepare --sender "$TASK_SES_SENDER"
python3 scripts/deploy_dev.py images
```

Nếu `prepare` đã thành công mà output vẫn thiếu, kiểm tra `infra/environments/dev-foundation/backend.hcl` và Terraform workspace đang chọn; script cần đọc đúng state foundation đó. Stage `images` chỉ kiểm tra rồi dừng khi thiếu output, không tự apply foundation.

Build/push lúc này chỉ cần foundation. Làm trước khi tạo RDS/Redis để database không phải chạy chờ bạn tải dependency và build Docker. Chi tiết tag/build nằm ở mục 5.

```bash
python3 scripts/deploy_dev.py bake
```

VPC luôn có Public, Private App, Private Data subnet ở cả AZ A và AZ B. Hiện tại NAT Gateway không được tạo. EC2 builder tạm thời ở public subnet để tải package, tạo AMI rồi được xóa sau khi stage thành công. MongoDB thật sẽ chạy private, không có public IP.

MongoDB dev hiện dùng SCRAM authentication và Security Group trong private subnet; kết nối app → MongoDB **chưa bật TLS**. Tunnel quản trị SSH được mã hóa, nhưng không thay thế TLS của kết nối ứng dụng. Khi lên prod cần cấu hình certificate/TLS cho MongoDB hoặc chuyển sang managed MongoDB có TLS.

AMI giữ MongoDB đã cài sẵn, không giữ password ứng dụng. Controller kiểm tra build qua SSM trước khi tạo AMI; trạng thái EC2 `stopped` riêng lẻ không đủ để kết luận build thành công.

`bake` từ chối chạy khi state đã có data layer hoặc ECS runtime để tránh vô tình thay đổi flag và xóa database đang dùng. Nếu bake lỗi giữa chừng, builder có thể còn tồn tại: sửa lỗi và chạy lại đúng stage, hoặc chạy `destroy` để dọn runtime/builder.

```bash
python3 scripts/deploy_dev.py data
```

Stage này bật `enable_data_layer`, `enable_redis`, `enable_mongodb`; RDS/Redis/MongoDB hiện chỉ có một node chính, không bật Multi-AZ data. Chờ Terraform báo apply thành công rồi sang bước tiếp theo.

**Từ đây database/EC2 đang tiêu thụ credit dù chưa có người dùng.** Nếu cần rời máy lâu, dọn bằng `destroy`.

## 5. Hiểu tag và các lệnh build/push ECR

Bạn đã chạy stage `images` ở đầu mục 4; không cần chạy lại ở đây.

Script build cho `linux/amd64` với root build context, theo 5 Dockerfile:

```text
services/booking/Dockerfile
services/catalog/Dockerfile
services/relay/Dockerfile
services/worker/Dockerfile
tools/mysql-bootstrap/Dockerfile
```

Mọi image có tag `dev-1.0.0-<12-character-git-sha>`. Không dùng `latest`. RDS TLS CA bundle được đưa vào image cho các service truy cập MySQL; kết nối Redis dùng `rediss://` với xác thực certificate/hostname.

ECR login dùng temporary Docker config để tránh lỗi Windows credential helper `The stub received bad data` đã gặp trong WSL. ECR token không được ghi vào source và temporary config bị xóa khi stage kết thúc. Nếu cùng tag đã tồn tại trong ECR, script giữ nguyên image đã publish.

Để thấy rõ lệnh build/push chuẩn tương đương cho một service:

```bash
TASK_ACCOUNT_ID="$(aws sts get-caller-identity \
  --profile flash-ticket-terraform --query Account --output text)"
TASK_REGISTRY="${TASK_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com"
TASK_SHA="$(git rev-parse --short=12 HEAD)"
TASK_TAG="dev-1.0.0-${TASK_SHA}"
TASK_DOCKER_CONFIG="$(mktemp -d)"
TASK_BOOKING_REPOSITORY="$(terraform \
  -chdir=infra/environments/dev-foundation \
  output -json ecr_repository_urls \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["booking"])')"

printf '{"auths":{},"cliPluginsExtraDirs":["%s/.docker/cli-plugins"]}\n' \
  "$HOME" > "$TASK_DOCKER_CONFIG/config.json"

aws ecr get-login-password \
  --profile flash-ticket-terraform --region us-east-1 \
  | docker --config "$TASK_DOCKER_CONFIG" login \
      --username AWS --password-stdin "$TASK_REGISTRY"

docker --config "$TASK_DOCKER_CONFIG" build --platform linux/amd64 \
  --file services/booking/Dockerfile \
  --tag "$TASK_BOOKING_REPOSITORY:$TASK_TAG" .

docker --config "$TASK_DOCKER_CONFIG" push "$TASK_BOOKING_REPOSITORY:$TASK_TAG"

rm -rf -- "$TASK_DOCKER_CONFIG"
```

Ví dụ này chỉ minh họa cấu trúc lệnh; đường dẫn repository thực tế lấy từ Terraform output, do stage `images` xử lý. Khi đã chạy stage, không cần build/push lại bằng ví dụ. Thay đổi source thì commit mới và chạy `images` để có tag mới.

Nếu muốn lưu Git tag tương ứng bản dev vừa build:

```bash
TASK_TAG="dev-1.0.0-$(git rev-parse --short=12 HEAD)"
git tag -a "$TASK_TAG" -m "Release Flash-Ticket $TASK_TAG"
git push origin "$TASK_TAG"
```

## 6. Bootstrap database và chạy các initialization task

```bash
python3 scripts/deploy_dev.py bootstrap
```

Stage này tạo ECS cluster, service/task definitions, Cloud Map, VPC Link và HTTP API. **Cả 4 service vẫn có `desired_count=0`** để app không khởi động khi dữ liệu chưa chuẩn bị xong.

Các tác vụ chạy lần lượt:

1. Chạy MySQL bootstrap task trên **FARGATE On Demand**, platform `1.4.0`: tạo schema, SQL app user với quyền DML tối thiểu, seed inventory. Task execution role đọc master/app password từ SSM. Các one-off task khác cũng dùng cùng platform.
2. Chờ task `STOPPED` rồi kiểm tra container `exitCode=0`. Task dừng vì lỗi không được tính là thành công.
3. Đọc app password và endpoint vào RAM, tạo SQLAlchemy URL/Redis URL rồi ghi vào SSM. Password và URI không được in ra console hoặc truyền trong command-line arguments.
4. Gọi MongoDB bootstrap qua EC2 Instance Connect Endpoint để tạo database/app user và SSM MongoDB URI.
5. Chạy Catalog seed task tạo demo event ở trạng thái `PUBLISHED`.
6. Chạy Relay reconciliation task để khởi tạo Redis inventory projection từ MySQL.

Demo event và inventory dùng cùng UUID:

```text
11111111-1111-4111-8111-111111111111
```

Các script seed được thiết kế để không reset inventory đã đặt. `bootstrap` dành cho khởi tạo; nếu đang chạy app, dùng `stop` trước khi cần chạy lại để kiểm tra một lỗi bootstrap.

Nếu task lỗi, stage dừng trước tác vụ tiếp theo. Xem ECS task stopped reason và CloudWatch Logs; không bật service bằng cách đổi `desired_count` thủ công khi bootstrap chưa xong.

### AWS CLI báo Invalid JSON khi dùng cli-input-json

Lỗi `ParamValidation: Error parsing parameter 'cli-input-json': Invalid JSON received` sau Terraform apply nằm ở AWS CLI request, không phải cú pháp Terraform. Các script dùng file JSON tạm có quyền `0600` trong thư mục tạm `0700`, giữ file đến khi lệnh AWS kết thúc rồi tự xóa. Không truyền secret JSON bằng command-line argument hoặc `/dev/stdin`.

Lấy bản sửa của branch hiện tại:

```bash
git pull --ff-only origin feat/aws-dev-deploy
```

Chạy lại đúng stage đã lỗi. Nếu đang ở `bootstrap`, giữ image tag đã publish trong `launch.auto.tfvars` và chạy:

```bash
python3 scripts/deploy_dev.py bootstrap
```

Không cần chạy lại `images` khi chỉ cập nhật các script triển khai. Terraform apply trước lỗi có thể đã tạo runtime; chạy lại stage sẽ đọc state đang có. Nếu bootstrap đã thành công và app đang chạy, dừng service theo mục 8 trước khi khởi tạo lại.

## 7. Bật API rồi publish React frontend

```bash
python3 scripts/deploy_dev.py start
```

Script kiểm tra SES sender đã verified, kiểm tra các SSM application parameters, nâng `desired_count=1` cho 4 service, chờ ECS stable rồi gọi `GET /events` qua API Gateway. Trong tối đa 180 giây, smoke check chờ đúng demo UUID, với `remaining` và `inventory_version` đều là số nguyên không âm. Lỗi HTTP tạm thời khi Cloud Map/VPC Link cập nhật được retry trong khoảng chờ này.

```bash
python3 scripts/deploy_dev.py frontend
python3 scripts/deploy_dev.py status
```

Frontend dùng `node:22-alpine`, `npm ci` và lockfile hiện có. Container chạy bằng UID/GID của bạn để tránh tạo `node_modules`/`dist` thuộc root. Các biến `VITE_*` chứa API URL và Cognito client configuration công khai; không chứa AWS access key hay database password.

File build được upload vào private S3 frontend bucket, truy cập qua CloudFront OAC. Hashed assets dùng cache immutable; `index.html` dùng `no-cache`. Các hashed assets cũ được giữ để browser đang mở phiên bản trước vẫn tải được; full cleanup sẽ xóa bucket cùng toàn bộ assets.

Mở URL `Frontend:` từ output. Thử theo thứ tự:

1. Danh sách sự kiện có banner và số vé còn lại.
2. Đăng ký bằng **email đã xác minh SES**; xác nhận Cognito email nếu được yêu cầu, rồi đăng nhập.
3. Trong form đặt vé, **đổi email mặc định `buyer@example.com` thành email đã verified SES**. Email đăng ký Cognito không tự điền vào field này. Đặt một vé rồi kiểm tra booking chuyển từ `PENDING` sang trạng thái đã tạo vé theo UI.
4. Mở link tải PDF và kiểm tra email.
5. Refresh trang để xem tồn kho đã giảm.

Trong SES sandbox, sender và recipient đều phải được verified. Cognito xác nhận email không đồng nghĩa SES đã verified recipient; dùng cùng email của sender giúp test đầu tiên đi qua hạn chế này.

Luồng thực tế: Browser → API Gateway JWT authorizer → VPC Link → Cloud Map → Booking/Catalog private IP. Booking commit inventory + booking + outbox trong cùng MySQL transaction. Relay chủ động poll outbox, cập nhật Redis và publish SQS. Worker chủ động long poll SQS, tạo PDF vào private S3, cập nhật booking và gửi SES email. Worker/Relay không cần HTTP request đi vào để bắt đầu chạy.

## 8. Tắt hôm nay, bật lại ngày mai

### Chỉ dừng 4 app service

```bash
python3 scripts/deploy_dev.py stop
```

Lệnh này chỉ scale ECS về 0. **Không phải tắt toàn bộ chi phí:** RDS, Redis và EC2 MongoDB vẫn chạy. Dùng lại app khi dữ liệu còn nguyên:

```bash
python3 scripts/deploy_dev.py start
```

### Dọn runtime để tiết kiệm credit

```bash
python3 scripts/deploy_dev.py destroy
```

Lệnh này xóa VPC và toàn bộ runtime, gồm ECS/API, RDS, Redis, EC2 MongoDB và builder còn sót nếu có. **Dữ liệu database bị xóa** theo cấu hình dev hiện tại, RDS bỏ qua final snapshot. Đọc plan và chỉ xác nhận khi chấp nhận mất dữ liệu.

Foundation, ECR images, S3, Cognito, CloudFront, SSM, MongoDB AMI và state bucket được giữ. Foundation/AMI/S3/ECR storage vẫn có thể tiêu thụ một ít credit. Các SSM password phải tồn tại đến khi runtime destroy xong vì wrapper Terraform cần chúng.

**SQS và DLQ cũng được giữ.** Nếu database bị reset nhưng queue còn job của booking cũ, Worker có thể nhận job không còn bản ghi tương ứng. Khi bạn chủ động bỏ toàn bộ booking/job của lượt test cũ, chỉ purge lúc tất cả service đã về 0 hoặc runtime đã được destroy:

```bash
python3 scripts/deploy_dev.py stop
python3 scripts/deploy_dev.py status

TASK_QUEUE_URL="$(terraform \
  -chdir=infra/environments/dev-foundation output -raw booking_queue_url)"
TASK_DLQ_URL="$(terraform \
  -chdir=infra/environments/dev-foundation output -raw booking_dlq_url)"

aws sqs purge-queue \
  --profile flash-ticket-terraform --region us-east-1 \
  --queue-url "$TASK_QUEUE_URL"

aws sqs purge-queue \
  --profile flash-ticket-terraform --region us-east-1 \
  --queue-url "$TASK_DLQ_URL"
```

Đây là thao tác xóa message có chủ ý, không dùng khi cần giữ hoặc xử lý tiếp các booking cũ. Đợi ít nhất 60 giây sau purge trước khi bật Worker; không gọi purge lại cùng queue trong khoảng 60 giây. `destroy --all` xóa queue cùng foundation nên không cần bước purge riêng.

Để dựng lại khi còn AMI và foundation:

```bash
python3 scripts/deploy_dev.py data
python3 scripts/deploy_dev.py bootstrap
python3 scripts/deploy_dev.py start
python3 scripts/deploy_dev.py frontend
```

Nếu code đã đổi, commit rồi chạy `images` trước `bootstrap` để cập nhật tag. Nếu AMI đã bị xóa bên ngoài, chạy `bake` trước `data`.

### Xóa toàn bộ tài nguyên thuộc bộ triển khai

```bash
python3 scripts/deploy_dev.py destroy --all
```

Thứ tự: runtime → project MongoDB AMIs/snapshots → foundation, bao gồm ECR images và S3 objects → SSM prefix `/flash-ticket/dev`.

Bucket/repository chứa dữ liệu cần `force_destroy`/`force_delete` được lưu vào state trước khi destroy. Script tạo plan riêng, chỉ cho phép cập nhật bucket/repository; **từ chối plan có create/delete hoặc thay đổi resource loại khác** để không tự dựng lại tài nguyên chỉ để xóa. Khi phát hiện drift, script dừng và giữ nguyên các phần còn lại; đọc thông báo trước khi xử lý tiếp.

Xóa thêm remote state bucket sau cùng:

```bash
python3 scripts/deploy_dev.py destroy --all --bootstrap-bucket
```

State bucket chỉ được xóa sau khi cả runtime và foundation đã destroy. Các file code, `.secrets`, local bootstrap state/backup, IAM login user và AWS Budget được giữ lại.

Khi muốn khôi phục sau full cleanup:

```bash
read -r -p "SES sender email: " TASK_SES_SENDER
python3 scripts/deploy_dev.py prepare --sender "$TASK_SES_SENDER"
python3 scripts/deploy_dev.py images
python3 scripts/deploy_dev.py bake
python3 scripts/deploy_dev.py data
python3 scripts/deploy_dev.py bootstrap
python3 scripts/deploy_dev.py start
python3 scripts/deploy_dev.py frontend
```

Các lệnh chỉ xóa tài nguyên được quản lý bởi hai root Terraform và các AMI/SSM được chỉ rõ. Tài nguyên bạn tự tạo ngoài state hoặc build cũ không mang project tag có thể còn tồn tại; kiểm tra Billing và các dịch vụ đã dùng sau khi dọn.

## 9. Chi phí và giới hạn cấu hình dev

Credit của tài khoản mới thanh toán usage; không mặc định mọi dịch vụ được miễn phí hoặc có quyền lợi `750 hours` của Free Tier cũ. Fargate Spot, public IPv4, EC2/EBS, RDS, Redis, ECR/S3 và snapshot đều cần theo dõi. AWS Budget là cảnh báo, không tự động tắt tài nguyên.

- NAT Gateway và ALB không được tạo trong cấu hình này.
- RDS dừng vẫn tính storage/backup và tự khởi động lại sau tối đa 7 ngày; không dùng cách này thay cho lịch dọn lab.
- ElastiCache Redis không có chức năng stop; muốn ngừng node cost phải xóa.
- EC2 stop giữ EBS nên vẫn có storage cost; AMI cũng giữ EBS snapshots.
- `desired_count=1` với Fargate Spot tiết kiệm compute nhưng **không có cam kết zero downtime**. Spot có thể thiếu capacity hoặc bị thu hồi; rolling update `minimumHealthyPercent=100`, `maximumPercent=200` còn cần capacity cho task mới.
- Nếu Spot không có capacity, đặt `use_fargate_spot = false` trong file `infra/environments/dev/launch.auto.tfvars` rồi chạy `start` để dùng On Demand. Chi phí compute tăng; initialization task vốn đã dùng On Demand.
- Bản dev một node không dùng để chứng minh chịu tải 50.000 người. Kiểm chứng tải, connection budget, autoscaling và HA sẽ làm có kiểm soát ở Phase 5.

Đường dẫn tài liệu vận hành cũ: [AWS operations](aws-operations.md). Khi thông tin bootstrap/runtime cũ khác với bản triển khai này, dùng luồng stage trong tài liệu hiện tại cho lần triển khai mới.
