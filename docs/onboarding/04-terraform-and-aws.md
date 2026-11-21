# 04 — Terraform và kiến trúc AWS

[← 03 — Frontend và authentication](03-frontend-and-auth.md) · [Tiếp: 05 — Docker và vận hành →](05-docker-and-operations.md)

Tài liệu này giải thích hạ tầng **đang có trong source** để một người mới đọc được plan, biết resource nào sẽ đổi và giữ an toàn cho state/dữ liệu. Các lệnh kiểm tra bên dưới không áp dụng cấu hình. Những lệnh tạo, thay đổi hoặc xóa AWS được ghi rõ trước khi sử dụng; việc đọc tài liệu không yêu cầu chạy chúng.

## 1. Phạm vi triển khai hiện tại

Region mặc định là `us-east-1`, với hai AZ `us-east-1a` và `us-east-1b`. Ba Terraform root phục vụ một môi trường dev; project chưa có root prod, pipeline GitHub Actions, autoscaling, CloudWatch Alarms hoặc SNS. CloudWatch Logs đã có.

Mạng có cấu trúc hai AZ để học cách chia tầng. Cấu hình dev vẫn tiết kiệm compute: mỗi ECS service có một task khi bật, RDS/Redis một primary, MongoDB một EC2. **`desired_count=1` là một task cho mỗi service trên toàn cluster, không phải một task trong mỗi AZ.** Có bốn service nên trạng thái ổn định thường có tổng cộng bốn app tasks.

Các cờ NAT và Multi-AZ tạo nền để nâng cấp từng phần. Chúng chưa biến bản dev thành hệ thống prod HA hoặc chứng minh khả năng chịu tải 50.000 users.

## 2. Đọc cấu trúc Terraform trước khi đọc từng resource

Terraform nạp tất cả file `.tf` trong cùng thư mục thành một module. Tách `main.tf`, `variables.tf` và `outputs.tf` giúp người đọc; tên file không tạo ra thứ tự chạy. Dependency graph được tạo từ references và `depends_on`.

| File/thư mục | Vai trò trong repo |
|---|---|
| `main.tf`, `rds.tf`, `redis.tf`… | Khai báo resource/module theo nhóm chức năng |
| `variables.tf` | Contract đầu vào: kiểu dữ liệu, mặc định, validation |
| `outputs.tf` | Contract đầu ra cho module cha, root khác và script |
| `versions.tf` | Terraform minimum, provider requirements, backend của root |
| `.terraform.lock.hcl` | Khóa provider version và checksum; được commit |
| `*.tfvars`, `*.auto.tfvars` | Giá trị local của account, flags, SES sender; không commit |
| `backend.hcl` | Bucket/key/profile cho remote state; script tạo, không commit |
| `.terraform/` | Cache provider/modules và cấu hình backend local; không commit |

Trong [dev/main.tf](../../infra/environments/dev/main.tf), `module.vpc.vpc_id` là output của child module VPC. Giá trị đó được truyền vào `module.data_layer`. Đây là dependency thực: Terraform biết VPC phải tồn tại trước các resource cần VPC ID. Đổi tên một file `.tf` thường không đổi resource address; đổi tên resource/module có thể đổi address và dẫn tới replacement nếu không có migration state.

## 3. Ba root, ba state và thứ tự phụ thuộc

| Root | Backend/state | Sở hữu phần nào |
|---|---|---|
| [`infra/bootstrap`](../../infra/bootstrap) | Local: `infra/bootstrap/terraform.tfstate` | S3 bucket lưu remote state, versioning, encryption, public-access block và HTTPS policy |
| [`infra/environments/dev-foundation`](../../infra/environments/dev-foundation) | S3 key `environments/dev-foundation/terraform.tfstate` | ECR, IAM roles, S3 assets/frontend, SQS/DLQ, SES identity, Cognito, CloudFront |
| [`infra/environments/dev`](../../infra/environments/dev) | S3 key `environments/dev/terraform.tfstate` | VPC, data layer, builder tạm, ECS, Cloud Map, VPC Link, HTTP API và log groups |

Bucket state có dạng `flash-ticket-tfstate-<account-id>-us-east-1`. Bootstrap dùng local state vì bucket chưa tồn tại ở lần tạo đầu. Hai root còn lại dùng cùng bucket nhưng **khác key**; chúng không chia sẻ một state file.

`deploy_dev.initialize()` tạo `backend.hcl` với `encrypt=true`, `use_lockfile=true`, bucket, key, region và profile. S3 backend dùng native lock file `.tflock`; repo không tạo DynamoDB lock table. Lock bảo vệ một state khỏi các writer đồng thời, không tạo transaction xuyên cả ba roots. Bucket versioning giữ các phiên bản state trước đó, nhưng không phải backup dữ liệu MySQL/MongoDB. Tránh chạy hai phiên `up`/`down` cùng lúc.

Thứ tự dựng là bootstrap → foundation → runtime. Khi `enable_compute=true`, [dev/deployment.tf](../../infra/environments/dev/deployment.tf) dùng `terraform_remote_state.foundation` để đọc ECR URLs, IAM role ARNs, queue, bucket và cấu hình web. Nó chỉ đọc outputs; lifecycle của foundation vẫn thuộc state riêng.

Thứ tự xóa là runtime → foundation → state bucket. Xóa bucket state trước khiến Terraform mất đường đọc các state còn cần để destroy. Local bootstrap state và backup phải được giữ cho tới khi cleanup hoàn tất. Mất state không tự xóa resource AWS; tạo state mới cùng cấu hình có thể gây xung đột tên hoặc dựng một bộ resource khác.

Project dùng **workspace `default`** và tách môi trường bằng root directories. Không tự tạo workspace `dev`/`prod` trong các root này: S3 backend sẽ chọn vị trí state khác, trong khi tên resource vẫn có thể là `flash-ticket-dev`.

## 4. Provider lock, account và phiên đăng nhập

Runtime/foundation cần Terraform `>=1.11,<2.0`; bootstrap hiện khai báo `>=1.10,<2.0`. Terraform 1.11 trở lên đáp ứng toàn bộ repo và hỗ trợ write-only arguments đang dùng. Các lockfile hiện khóa AWS provider `6.73.0`; foundation còn nhận constraint `<7.0` từ module `web_auth`.

`required_providers` xác định phiên bản được phép; lockfile xác định phiên bản được chọn và checksum của package. Giữ lockfile khi review/commit. Đổi constraint có thể làm `init` cập nhật metadata của lockfile, vì vậy luôn kiểm tra Git diff trước build image. Không dùng `init -upgrade` như một cách chữa lỗi state: đó là thao tác chọn lại provider, cần review riêng.

| Danh tính | Được dùng ở đâu |
|---|---|
| `flash-ticket-dev` | AWS CLI login session của người vận hành |
| `flash-ticket-terraform` | Profile hạ tầng, lấy credentials tạm qua `credential_process` đã cấu hình |
| `allowed_account_ids` | Provider từ chối chạy nếu credentials thuộc account khác `aws_account_id` |
| `default_tags` | Gắn `Project`, `Environment`, `ManagedBy` để nhận diện tài nguyên |

Credentials không nằm trong Terraform source. Khi session hết hạn, cần đăng nhập lại; không tạo lại hạ tầng để sửa lỗi authentication. Các lệnh sau phục vụ đăng nhập/kiểm tra danh tính, không tạo resource:

```bash
aws login --profile flash-ticket-dev

aws sts get-caller-identity \
  --profile flash-ticket-terraform \
  --region us-east-1

terraform version
git diff -- infra
```

`credential_process` là cấu hình trên máy người vận hành, không phải IAM role của container. Không chép AWS access keys vào task environment hoặc Git.

## 5. SSM, biến ephemeral và password write-only

[aws_dev_secrets.py](../../scripts/aws_dev_secrets.py) tạo/khôi phục Standard SecureString dưới `/flash-ticket/dev`, với backup local trong `.secrets/aws-dev-secrets.json`:

- `data/MYSQL_MASTER_PASSWORD`, `data/MYSQL_APP_PASSWORD`.
- `data/MONGODB_ROOT_PASSWORD`, `data/MONGODB_APP_PASSWORD`.
- `data/REDIS_AUTH_TOKEN`, `worker/TICKET_SIGNING_SECRET`.

Bootstrap ứng dụng tạo tiếp `booking/DATABASE_URL`, `relay/DATABASE_URL`, `worker/DATABASE_URL`, `catalog/MONGODB_URI` và Redis URLs theo service. Terraform task definitions lưu **SSM parameter ARN**, rồi ECS execution role đọc giá trị khi khởi động task.

Tên parameter SSM và tên environment variable không hoàn toàn giống nhau: `catalog/MONGODB_URI` là path được `provision_mongodb.py` ghi, còn `ecs_runtime` đưa giá trị đó vào biến **`MONGODB_URL`** của container Catalog. Docker Compose local cũng dùng `MONGODB_URL`. Khi sửa contract này, phải đối chiếu cả provisioner, task definition và app; chỉ đổi tên ở một chỗ sẽ làm Catalog không kết nối được.

Luồng password ở Terraform runtime nằm trong [tf-dev.sh](../../scripts/tf-dev.sh): đọc SSM bằng `--with-decryption` → đặt biến trong process `TF_VAR_rds_master_password` và `TF_VAR_redis_auth_token` → chạy Terraform của root dev. Script tắt shell tracing, bỏ `TF_LOG` và không in secret.

Root và child module đánh dấu các input này `sensitive=true`, `ephemeral=true`. Resource MySQL nhận `password_wo`; Redis nhận `auth_token_wo`. `_wo` là write-only: giá trị được chuyển cho provider trong lần chạy mà không lưu dưới thuộc tính password/token thông thường trong state. Các input `*_version` là số phiên bản lưu được trong state; tăng chúng khi **chủ động rotate** password/token để provider biết phải gửi giá trị mới.

Wrapper vẫn cần đọc hai SSM secrets khi chạy root dev, kể cả lúc data layer đang tắt, vì root variables là input bắt buộc. Vì vậy bước `prepare` phải khôi phục SSM trước các stage runtime; cleanup phải destroy runtime trước khi xóa SSM. Gặp `ParameterNotFound` ở đây là lỗi thứ tự/state secrets, không phải lý do để thay password ngẫu nhiên.

`sensitive` chỉ che cách hiển thị; nó không thay thế encryption. State và saved plan vẫn phải được bảo vệ vì có thể chứa thông tin nhạy cảm khác. Secret cũng tồn tại trong RAM/process environment lúc cần dùng. Không dump environment, `cat` backup secrets hoặc bật debug logging để chia sẻ log. Không đưa secret vào `terraform.tfvars`, Docker build args hoặc biến `VITE_*`.

## 6. Hai AZ và placement thực tế của dev

CIDR mặc định từ [VPC source](../../infra/modules/vpc/main.tf) là `10.42.0.0/16`. `cidrsubnet(vpc_cidr,8,n)` chia thành `/24`; các số subnet là 0/1 cho Public, 10/11 cho Private App và 20/21 cho Private Data.

| Tầng | AZ A — `us-east-1a` | AZ B — `us-east-1b` | Placement hiện tại |
|---|---|---|---|
| Public | `10.42.0.0/24` | `10.42.1.0/24` | Bốn ECS services chọn subnet A hoặc B khi NAT tắt; builder tạm ở A |
| Private App | `10.42.10.0/24` | `10.42.11.0/24` | VPC Link sử dụng hai subnet; Mongo management endpoint ở A |
| Private Data | `10.42.20.0/24` | `10.42.21.0/24` | RDS/Redis primary ở A; MongoDB EC2 ở A; subnet B dành cho placement dự phòng khi cấu hình hỗ trợ |

RDS/Redis subnet groups chứa cả hai Private Data subnets dù dev chỉ chạy primary một AZ. Cloud Map namespace, S3, SQS, Cognito, SES và API Gateway là managed services; không xem chúng như EC2 process nằm trong một subnet. CloudFront là dịch vụ edge.

Public route table có `0.0.0.0/0 → Internet Gateway`. Subnet đặt `map_public_ip_on_launch=false`; ECS task tự yêu cầu `assign_public_ip=true`. Public IP cho phép task ra Internet tải image, đọc SSM/JWKS và gọi AWS APIs. **Security Group vẫn chỉ cho VPC Link gọi private IP của hai HTTP services**; không có inbound public vào chúng. Relay/Worker không có inbound và không mở HTTP port.

Khi `enable_nat_gateway=true`, module tạo một NAT Gateway/EIP mỗi AZ, thêm default route cho Private App subnet và chuyển ECS sang Private App với `assign_public_ip=false`. Private Data route tables vẫn chỉ có route nội bộ; chúng không được thêm NAT route. MongoDB dùng AMI cài sẵn package và quản trị qua Instance Connect Endpoint nên không cần tải package lúc boot private.

Các luồng SG được khai báo thành cặp ingress/egress trong [security_groups.tf](../../infra/modules/vpc/security_groups.tf):

| Nguồn → đích | Port | Mục đích |
|---|---:|---|
| VPC Link → Booking/Catalog | TCP 8000 | HTTP API private integration |
| Booking/Relay/Worker → RDS | TCP 3306 | MySQL có TLS và xác minh certificate |
| Catalog/Relay → Redis | TCP 6379 | `rediss://`, auth token và xác minh TLS |
| Catalog → MongoDB | TCP 27017 | MongoDB private có authentication; dev chưa bật MongoDB TLS |
| EIC Endpoint → Mongo management SG | TCP 22 | SSH quản trị private node |
| ECS tasks → AWS/Cognito/Internet APIs | TCP 443 | Egress HTTPS |

SG stateful cho phép response của kết nối hợp lệ quay lại. Public subnet không có nghĩa mọi port được mở. Cũng cần phân biệt NAT Gateway với API Gateway: NAT phục vụ egress, API Gateway phục vụ API ingress.

## 7. Bản đồ toàn bộ child modules

Đọc `variables.tf`/`outputs.tf` của từng module để biết contract; đọc resource files dưới đây để biết nó thực sự tạo gì.

| Module | File chính và trách nhiệm | Root sở hữu |
|---|---|---|
| [`vpc`](../../infra/modules/vpc) | `main.tf`: VPC/subnets/routes/IGW/NAT; `security_groups.tf`: các luồng mạng | dev |
| [`ecr`](../../infra/modules/ecr) | `main.tf`: private repositories, immutable tags, scan on push, lifecycle giữ images gần đây | foundation; được gọi riêng cho 4 app và MySQL bootstrap |
| [`iam`](../../infra/modules/iam) | `main.tf`: execution roles, ECR pull, SSM namespace, CloudWatch log permissions | foundation |
| [`task_roles`](../../infra/modules/task_roles) | `main.tf`: task role trust; `policies.tf`: quyền AWS cho từng ứng dụng | foundation |
| [`assets`](../../infra/modules/assets) | `main.tf`: private S3 assets, encryption, HTTPS/OAC policy, demo banner, multipart lifecycle | foundation |
| [`messaging`](../../infra/modules/messaging) | `main.tf`: SQS Standard, DLQ, redrive policy và allow policy | foundation |
| [`ses`](../../infra/modules/ses) | `main.tf`: SES email identity của sender | foundation |
| [`web_auth`](../../infra/modules/web_auth) | `main.tf`: frontend S3/OAC/CloudFront/Cognito; `spa.js`: SPA route rewrite | foundation |
| [`data_layer`](../../infra/modules/data_layer) | `rds.tf`, `redis.tf`, `mongodb.tf`: databases và Mongo management endpoint; `templates/mongodb-node.sh`: node bootstrap | dev, có `count` |
| [`mongo_ami_builder`](../../infra/modules/mongo_ami_builder) | `main.tf`: EC2 public tạm/IAM SSM; `templates/bootstrap.sh`: cài MongoDB, SSH và chuẩn bị image | dev, bật tạm |
| [`ecs_runtime`](../../infra/modules/ecs_runtime) | `main.tf`: ECS services/task definitions, MySQL one-off task, Cloud Map, VPC Link, HTTP API, logs | dev, có `count` |

Không có module `apigw_cloudmap` riêng trong source hiện tại; chức năng này nằm trong `ecs_runtime`. [mysql_bootstrap.tf](../../infra/environments/dev-foundation/mysql_bootstrap.tf) ở foundation tạo repository/execution role riêng cho task khởi tạo SQL. [dev/deployment.tf](../../infra/environments/dev/deployment.tf) nối các outputs của foundation và data layer vào runtime.

Data layer mặc định dùng RDS MySQL `8.4`, `db.t3.micro`, storage encrypted gp3 20 GiB, backup 1 ngày; Redis `7.1`, `cache.t3.micro`, TLS bắt buộc, snapshot retention 0; MongoDB EC2 `t3.micro`, encrypted gp3 12 GiB, không public IP. Mongo AMI phải thuộc account hiện tại và có project/role tags theo source. Đây là cấu hình cụ thể, không phải cam kết Free Tier cho mọi account.

## 8. Theo một request từ browser đến task

1. Browser tải React và static assets từ CloudFront. Cognito public client dùng Authorization Code + PKCE, không có client secret trong frontend.
2. Browser gửi access token tới HTTP API. JWT authorizer kiểm issuer/client audience và scope trên các route yêu cầu auth.
3. Private integration đi qua VPC Link ENIs, gọi Cloud Map `DiscoverInstances` để tìm private IP/port của task khỏe.
4. Booking/Catalog lắng nghe port `8000`. ECS đăng ký service discovery bằng SRV record, container name/port; health check gọi `/readyz`, có `startPeriod=60` giây.
5. Backend còn xác minh Cognito token và ownership/scope ở FastAPI. Booking ghi transaction/outbox; Relay và Worker chủ động polling, không cần HTTP request gọi vào worker.

| Route HTTP API | Service | Authorization |
|---|---|---|
| `GET /events` | Catalog | Public |
| `POST /bookings` | Booking | JWT + `flash-ticket/bookings:write` |
| `GET /bookings/{booking_id}` | Booking | JWT + cùng scope; backend kiểm chủ booking |
| `GET /bookings/{booking_id}/ticket` | Booking | JWT + cùng scope; backend tạo download URL |

Không có route `/` hoặc public `POST /events` trên API Gateway. Mở API root nhận `404` là phù hợp với cấu hình; dùng `/events` để smoke test. ECS `steady state` với `desired=0` chỉ nói scheduler đã đạt số task yêu cầu bằng 0, không chứng minh API hoạt động.

Integration dùng Cloud Map service ARN và `overwrite:path = $request.path` để backend nhận đúng route. API CORS cho phép CloudFront origin và hai localhost origins hiện có. Cognito callbacks là một cấu hình riêng: `cognito_allow_localhost_callbacks=false` mặc định; chỉ cho phép localhost callback khi bật cờ đó. Một origin được phép qua CORS chưa đủ để Cognito chấp nhận redirect URI.

Task definitions dùng `256` CPU units/`512` MiB RAM, Linux `X86_64`, non-root user `10001:10001`; image tag có dạng `dev-1.0.0-<git-sha>`. Rolling update có minimum healthy 100%, maximum 200%, circuit breaker/rollback. Một Spot task vẫn có thể bị thu hồi hoặc thiếu capacity, nên các tham số rolling này không bảo đảm zero downtime cho bản dev.

## 9. CloudFront OAC và ranh giới public/private của file

CloudFront có hai origins: private frontend bucket và private assets bucket. Default behavior phục vụ frontend; `/banners/*` dùng assets origin. Mỗi origin có OAC ký SigV4. Bucket policy chỉ cho CloudFront service principal đọc đúng prefix với `AWS:SourceArn` của distribution.

Frontend bucket cho phép distribution đọc static build. Assets bucket chỉ cho OAC đọc `banners/*`; không có behavior/OAC permission public cho `tickets/*`. Worker upload PDF vào private prefix, Booking trả presigned S3 URL sau khi kiểm ownership. Ai có URL còn hiệu lực có thể sử dụng nó, nên không ghi URL đầy đủ vào log hoặc công khai nó như một banner.

`spa.js` rewrite đường dẫn frontend không có phần mở rộng, như `/auth/callback`, về `/index.html`; behavior banner không gắn function này. `index.html` được upload với no-cache, hashed assets dùng immutable cache. Cấu hình CDN không thay thế authorization của API hoặc ticket download.

## 10. Ba nhóm IAM cần phân biệt

| Nhóm | Ai dùng | Quyền hiện có và phạm vi |
|---|---|---|
| Operator credentials | AWS CLI/Terraform trên máy dev | Tạo/quản lý resource, đọc SSM để bootstrap, tạo AMI và SSH qua EIC; không phải quyền mặc định của app |
| ECS execution role | ECS agent khi khởi động container | Lấy ECR auth, pull đúng repository, đọc SSM namespace của service, ghi log stream |
| ECS task role | Boto3 trong ứng dụng đang chạy | Booking đọc ticket; Catalog đọc banner; Relay gửi SQS; Worker nhận/xóa/gia hạn message, đọc/ghi ticket và gọi SES |

Các ECS trust policies giới hạn `aws:SourceAccount` và ECS source ARN của account/region. Execution role không được dùng thay task role trong app code. Task role nhận credentials tạm qua ECS credential provider; không cần AWS access key trong environment.

MySQL bootstrap có execution role riêng: pull đúng bootstrap image, ghi đúng log group và đọc hai SSM SQL passwords ở namespace `data/`. App dùng SQL user `flash_app`; không nhận RDS master password. Builder EC2 dùng instance profile với `AmazonSSMManagedInstanceCore` để controller xác minh build; đây là role tạm, không phải một sandbox không có quyền đọc SSM. Builder được dọn sau khi bake thành công; AMI/snapshot vẫn còn cho tới khi cleanup.

## 11. Flags là thay đổi lifecycle, không chỉ nút bật/tắt

Các defaults nằm trong [dev/variables.tf](../../infra/environments/dev/variables.tf) và [dev/deployment.tf](../../infra/environments/dev/deployment.tf). Script lưu trạng thái vận hành vào `launch.auto.tfvars`, không commit file này.

Terraform lấy giá trị theo precedence: default → `TF_VAR_*` → `terraform.tfvars`/`terraform.tfvars.json` → các `*.auto.tfvars` theo tên → `-var`/`-var-file` trên CLI. Vì vậy một giá trị bạn sửa trong `terraform.tfvars` có thể bị `launch.auto.tfvars` ghi đè. Đừng định nghĩa cùng operational flag rải rác ở nhiều file; đọc file do script quản lý và plan cuối cùng để biết giá trị thực sự được áp dụng. Secret inputs được wrapper cấp qua `TF_VAR_*`, không được ghi đè bằng một file chứa password.

`module.vpc` không có `count`, nên một lần apply root dev vẫn quản lý VPC, subnets, routes và SG ngay cả khi cả `enable_data_layer` và `enable_compute` đều false. Muốn xóa runtime phải dùng quy trình destroy; không suy ra rằng đổi hai flags về false đã xóa cả VPC.

| Flag/default | Khi thay đổi | Điều phải đọc trong plan |
|---|---|---|
| `enable_data_layer=false` | Tạo/bỏ `module.data_layer[0]` | Đổi về false khi đang có DB có thể destroy cả data layer |
| `enable_redis=false`, `enable_mongodb=false` | Tạo/xóa các resource tùy chọn trong data layer | Xóa cache, EC2/EBS và endpoint liên quan; không phải stop |
| `enable_compute=false` | Tạo/bỏ `module.ecs_runtime[0]` | Có thể xóa ECS/API/VPC Link/Cloud Map/log groups |
| `service_desired_count=0` | Scale task count, giữ cấu hình hạ tầng | DB và storage vẫn tồn tại/tính phí |
| `enable_mongo_ami_builder=false` | Tạo/xóa EC2 builder tạm | Không nhầm với EC2 MongoDB đang giữ data |
| `enable_nat_gateway=false` | true tạo NAT/EIP mỗi AZ và đổi task subnet/public IP | Thêm chi phí NAT; task networking thay đổi |
| `enable_multi_az_data=false` | true bật RDS standby và Redis replica/failover | Tăng data-node cost; MongoDB vẫn standalone AZ A |
| `use_fargate_spot=true` | false chọn FARGATE On Demand | Capacity provider thay đổi; không tự tăng task count |
| `application_image_tag=""` | Phải có tag hợp lệ khi enable compute | Task definition revision mới; image phải đã có trong ECR |

Các cờ foundation `ecr_force_delete`, `assets_force_destroy`, `frontend_force_destroy` mặc định false. Bootstrap có `allow_state_bucket_force_destroy=false`. Chúng cho phép delete repository/bucket đang có dữ liệu; không phải cách tắt request hoặc tiết kiệm runtime hằng ngày. Script cleanup chỉ lưu các cờ này bằng targeted plan có guard, rồi destroy bằng dependency graph đầy đủ. Quy trình chi tiết ở [05 — Docker và vận hành](05-docker-and-operations.md).

## 12. Đọc resource address, moved block và plan

Một số address thực tế để luyện đọc:

```text
module.vpc.aws_subnet.public["a"]
module.data_layer[0].aws_db_instance.mysql
module.data_layer[0].aws_instance.mongodb[0]
module.ecs_runtime[0].aws_ecs_service.application["booking"]
module.ecr.aws_ecr_repository.this["worker"]
```

`count` tạo địa chỉ có index số; `for_each` tạo key ổn định theo tên. `main.tf` có `moved { from=module.data_layer; to=module.data_layer[0] }` để nối state cũ với module vừa được thêm count. Giữ migration này: đổi cấu trúc address mà không chuyển state có thể khiến Terraform đề xuất xóa resource cũ và tạo mới.

`create_before_destroy` ở SG/parameter group cho phép tạo bản thay thế trước khi xóa bản cũ; không có nghĩa không thể gián đoạn. MongoDB khai báo `user_data_replace_on_change=true`: chỉnh node user data có thể **replace EC2/EBS**. RDS dev mặc định `skip_final_snapshot=true`, `deletion_protection=false`; destroy có thể mất dữ liệu mà không tạo final snapshot. Không suy diễn những defaults này thành cấu hình prod.

Các lệnh sau chỉ đọc metadata/output của root đã được initialize, nhưng vẫn cần quyền đọc backend tương ứng:

```bash
terraform -chdir=infra/environments/dev workspace show
terraform -chdir=infra/environments/dev state list
terraform -chdir=infra/environments/dev output -json rds
terraform -chdir=infra/environments/dev output -json ecs
terraform -chdir=infra/environments/dev-foundation output -json ecr_repository_urls
```

Nếu chưa dựng hạ tầng, outputs có thể chưa tồn tại. Khai báo output trong source không tự ghi nó vào state; cần root đó apply thành công. `terraform state list` chỉ liệt kê resource Terraform biết, không quét toàn account. Không dùng `state rm`, xóa state file hoặc `force-unlock` để làm lỗi biến mất; cần xác minh owner/process của lock và lý do state lệch trước.

Để review một thay đổi runtime đã chủ động chuẩn bị, dùng wrapper nạp đúng ephemeral variables:

```bash
bash scripts/tf-dev.sh plan -out=dev-review.tfplan
terraform -chdir=infra/environments/dev show dev-review.tfplan
```

Plan không áp dụng cấu hình AWS, nhưng có thể đọc AWS/SSM và tạo lock file trên backend trong thời gian chạy. `+` là create, `~` là update tại chỗ, `-/+` là replacement, `-` là destroy. Đọc từng resource address và attribute trước, rồi mới đọc dòng tổng `Plan: … to add, … to change, … to destroy`.

Ví dụ chỉ muốn đổi log retention nhưng plan lại có `module.data_layer[0].aws_instance.mongodb[0] must be replaced` thì phải dừng để hiểu nguyên nhân. Saved plan cũng không được commit hoặc chia sẻ như một file cấu hình bình thường.

**Thao tác ghi AWS/có thể phát sinh phí:** chỉ sau khi đã review đúng plan và quyết định áp dụng mới chạy `bash scripts/tf-dev.sh apply dev-review.tfplan`. Lệnh này không phải kiểm tra cú pháp. Với sử dụng hằng ngày, đi theo hai entrypoints và runbook trong bài 05 để giữ bootstrap/data/queue đúng thứ tự; không tự gọi apply lẻ khi chưa hiểu flags hiện tại.

## 13. Chi phí, phạm vi cleanup và bài tự kiểm tra

NAT Gateway và ALB được tránh ở dev, nhưng Fargate, public IPv4, RDS/Redis/EC2, EBS, snapshots/AMI, ECR/S3, API requests, Cloud Map/private DNS và CloudWatch Logs vẫn có thể dùng credits. Standard SecureString tránh mô hình phí của Secrets Manager; vẫn cần theo dõi giới hạn và KMS/API usage. Account có credits không đồng nghĩa mọi resource được miễn phí hoặc có quyền lợi Free Tier cũ.

RDS stop vẫn giữ storage/backup và có giới hạn thời gian stop; Redis không có thao tác stop node; EC2 stop giữ EBS; S3/CloudFront/VPC không có cùng nghĩa “stop” với EC2. `down stop` chỉ dừng ECS; `down runtime` xóa runtime/data nhưng giữ foundation/AMI; `down all` dọn toàn bộ phạm vi dự án và state bucket sau cùng. Resource tạo thủ công ngoài state/phạm vi cleanup không tự bị xóa. Các bước thực hiện và kiểm tra nằm trong [05](05-docker-and-operations.md), [runbook bật/tắt](../AWS-DEV-POWER.md) và [runbook triển khai từng stage](../DEPLOY-DEV-QUICKSTART.md).

Trước khi tự bảo trì một thay đổi, hãy trả lời được:

1. Resource thuộc root nào, state key nào và account/region nào?
2. Thay đổi chỉ update, hay replace/destroy database, bucket hoặc identity?
3. App có cần image mới, task definition mới, SSM update hoặc frontend rebuild không?
4. `desired_count=0` có thật sự dừng phần đang dùng credits mà bạn muốn dừng không?
5. Nếu xóa DB nhưng giữ SQS, message cũ sẽ được xử lý thế nào? Nếu xóa Cognito/CloudFront, user và URL nào phải tạo lại?
6. State/lock/backup nào cần giữ để tiếp tục hoặc rollback? Bạn đã đọc dependency graph thay vì dựa riêng vào dòng tổng plan chưa?

Bạn đã nắm phần Terraform khi có thể lần từ root input → child resource → output → script sử dụng, giải thích được placement của task/database và dự đoán đúng tác động của một plan trước khi chạy thao tác ghi AWS.

[← Frontend và authentication](03-frontend-and-auth.md) · [Tiếp: Docker và vận hành →](05-docker-and-operations.md)
