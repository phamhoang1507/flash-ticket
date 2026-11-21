# 05. Docker, Compose và vận hành dev

[← 04. Terraform và AWS](04-terraform-and-aws.md) · [Danh mục onboarding](README.md) · [06. Troubleshooting và recovery →](06-troubleshooting-and-recovery.md)

Chương này nối source code với thứ thực sự chạy: Docker image chứa gì, Compose tạo lab local như thế nào, và các script quyết định dựng lại hay tái sử dụng AWS resources ra sao. Các lệnh bên dưới dành cho Bash/WSL, chạy tại root repository. Quy trình thao tác đầy đủ nằm trong [Local development](../LOCAL-DEVELOPMENT.md), [AWS up/down](../AWS-DEV-POWER.md) và [deployment theo stage](../DEPLOY-DEV-QUICKSTART.md).

## 1. Từ source đến container

Dockerfile là công thức tạo image; image chứa filesystem và lệnh mặc định; container là một lần chạy image với configuration, network và storage riêng. Sửa file Python trên host không tự sửa container đang chạy vì app source được `COPY` vào image lúc build. Với Compose hiện tại, chỉ public key được bind-mount vào API, không có bind-mount toàn bộ source để hot reload.

| Nơi đọc | Vai trò và điểm cần hiểu |
|---|---|
| [Booking Dockerfile](../../services/booking/Dockerfile), [Catalog Dockerfile](../../services/catalog/Dockerfile) | FastAPI chạy Uvicorn, container port `8000`, user `10001:10001` |
| [Relay Dockerfile](../../services/relay/Dockerfile), [Worker Dockerfile](../../services/worker/Dockerfile) | Chạy `python -m app.main`; tự poll DB/SQS, không mở HTTP port |
| [MySQL bootstrap Dockerfile](../../tools/mysql-bootstrap/Dockerfile) | Tool chạy migrations/users rồi thoát; kết quả cần `exitCode=0` |
| [Frontend Dockerfile](../../frontend/Dockerfile), [nginx.conf](../../frontend/nginx.conf) | Node build React, Nginx phục vụ bản build trong lab local |
| [.dockerignore](../../.dockerignore) | Loại `.env`, `.secrets`, Git metadata, Terraform state/config sinh ra và output khỏi root build context |

Python images dùng official `python:3.12-slim` với hai stage. Stage `builder` tạo virtual environment tại `/opt/venv` và cài từ `requirements.txt`; stage `runtime` copy virtual environment cùng đúng source cần chạy. Tách dependency installation khỏi source giúp Docker dùng lại layer khi chỉ sửa Python code. Runtime bắt đầu từ base image mới nên không copy cả filesystem của builder.

`USER 10001:10001` được đặt sau các thao tác tạo thư mục/copy/chmod cần root. Booking, Relay, Worker và MySQL bootstrap có RDS CA tại `/app/certs/global-bundle.pem`. File certificate có quyền đọc `0444`; các thư mục cha phải có quyền traverse, như `0755`, để non-root thực sự mở được file. Chỉ chmod certificate mà để thư mục cha không truy cập được vẫn gây lỗi TLS/file check. Catalog không truy cập MySQL nên không cần RDS CA này.

MySQL TLS còn cần application configuration: `MYSQL_SSL_CA` trỏ tới bundle và [shared/database.py](../../shared/database.py) tạo SSL context xác minh certificate/hostname. Copy CA vào image chưa tự bật TLS cho mọi connection. Redis trên AWS dùng `rediss://`; MongoDB dev hiện dùng SCRAM trong private network, chưa có TLS cho app connection.

`EXPOSE 8000` là metadata của image. Port được truy cập từ host nhờ cấu hình `ports` của Compose hoặc network/integration trong ECS, không phải chỉ nhờ dòng `EXPOSE`. Relay/Worker không cần fake HTTP endpoint để được “gọi”: process đã bắt đầu poll khi container chạy.

### Build context quyết định ý nghĩa của COPY

Các backend Dockerfiles cần **root context** vì chúng lấy cả `shared/`, `services/...` và một số `scripts/`. Đường dẫn nguồn của `COPY` được tính từ context, không phải từ thư mục chứa Dockerfile. Sau khi source đã commit và `git status --short` trống, ví dụ build Booking:

```bash
docker build --platform linux/amd64 --file services/booking/Dockerfile --tag "flash-ticket/booking:dev-1.0.0-$(git rev-parse --short=12 HEAD)" .
```

Dấu `.` cuối lệnh là root context. Đổi context thành `services/booking` sẽ làm `COPY shared ...` hoặc `COPY services/booking/...` không tìm thấy source. Frontend có context riêng `./frontend`, nên Dockerfile của nó dùng `COPY package.json ...` thay vì `COPY frontend/package.json ...`.

`requirements.txt` là input thực tế của Docker build; `requirements.in` nếu có là khai báo để tạo/cập nhật lock. Frontend dùng `package-lock.json` với `npm ci`; Terraform dùng `.terraform.lock.hcl` để chọn provider đã lock. Đây là các lớp khóa version khác nhau. Tag base image như `python:3.12-slim` vẫn có thể trỏ tới digest mới; một lần nâng dependency/base image cần được review và build thành image tag mới, không tự đổi toàn bộ version khi debug.

## 2. Frontend local và frontend AWS

Trong local, [frontend/Dockerfile](../../frontend/Dockerfile) dùng official `node:22-alpine` để chạy `npm ci` rồi `npm run build`. Stage runtime dùng `nginx:stable-alpine`, chạy bằng user `nginx` và listen `8080`. Nginx ghi PID/temp files trong `/tmp` để hoạt động với Compose `read_only: true` và tmpfs.

Browser local gọi `/api/events` hoặc `/api/bookings` cùng origin `http://localhost:8080`. Nginx bỏ prefix `/api`, chuyển request tới `catalog:8000` hoặc `booking:8000`, đồng thời giữ header `Authorization`. Docker DNS `127.0.0.11` giúp Nginx resolve lại container endpoint. SPA fallback trả `index.html` cho route giao diện; route `/assets/` thiếu file trả 404 thay vì trả HTML giả làm JavaScript.

Trên AWS, `deploy_dev.frontend()` chạy Node build trong Docker bằng UID/GID của người dùng rồi upload `frontend/dist` lên **private S3 frontend bucket**. CloudFront đọc bằng OAC. Không có frontend ECS task và không chạy Nginx để host giao diện AWS. Năm ECR images là Booking, Catalog, Relay, Worker và MySQL bootstrap; Docker Hub local có thể chứa thêm frontend image.

`VITE_*` được nhúng vào JavaScript tại **build time**. Thay API URL/Cognito client configuration rồi chỉ restart Nginx sẽ giữ bundle cũ. Phải build/publish lại. Các giá trị này công khai với browser; không đặt database password, AWS secret key hoặc ticket signing secret vào `VITE_*`.

AWS frontend dùng API Gateway root URL, không thêm `/api`; prefix đó thuộc reverse proxy local. Hashed assets được cache immutable, còn `index.html` được upload với no-cache. Script giữ hashed assets cũ để tab đang mở bản frontend trước vẫn tải được; CloudFront invalidation giúp browser nhận index mới.

### Browser storage khác database và AWS identity

Source hiện tại trong [auth.js](../../frontend/src/auth.js) và [App.jsx](../../frontend/src/App.jsx) dùng **sessionStorage**, không dùng localStorage để lưu token/booking. Token local, OIDC state, yêu cầu đang chờ và booking gần nhất thuộc phiên/tab của origin hiện tại. Refresh tab có thể giữ chúng; dữ liệu đó không phải danh sách booking lấy lại từ database.

Booking/pending request được gắn với identity gồm issuer và subject. Sau khi đổi account hoặc dựng Cognito User Pool mới, UI không được nhận booking cũ là của người đang đăng nhập. Claims đọc trong browser phục vụ UX; API Gateway/FastAPI vẫn thực hiện xác minh ở server.

Nếu đã chạy `down all` và Cognito User Pool đã bị xóa, lần tạo User Pool mới cần đăng ký/xác nhận tài khoản Cognito mới và dùng URLs/configuration mà controller vừa in ra. SES identity cũng cần xác minh lại khi được tạo lại. `stop` hoặc chỉ `down runtime` giữ foundation nên không tự xóa Cognito users. IAM user đăng nhập AWS được giữ bởi các mode cleanup; đó là identity quản trị riêng, không phải tài khoản khách mua vé.

## 3. Compose là lab có thứ tự khởi tạo

[compose.yaml](../../compose.yaml) định nghĩa MySQL, MongoDB, Redis, LocalStack và named volumes. [compose.override.yaml](../../compose.override.yaml) thêm bốn app services và frontend. `docker compose` tại root tự merge hai file. Chỉ truyền `-f compose.yaml` sẽ bỏ phần override tự động và chỉ thấy data services.

`.env` được Compose đọc để thay `${...}` trong YAML; nó không tự trở thành toàn bộ environment của mọi container. Mỗi service chỉ nhận các key được map trong `environment`. Shell environment có thể override giá trị `.env`; tránh dùng cùng shell đã export credentials/endpoint giả của local khi chuyển sang AWS thật. Không `source .env` vào shell AWS.

Kiểm tra tên services mà không in bản cấu hình chứa password:

```bash
docker compose config --services
```

MySQL/MongoDB/Redis giao tiếp bằng tên service trong Compose network. Host chỉ truy cập các port đã bind, như Booking `127.0.0.1:8001`, Catalog `127.0.0.1:8002`, frontend `127.0.0.1:8080`, LocalStack `127.0.0.1:4566`. Bên trong container, `localhost` là chính container đó; vì thế AWS SDK local gọi `localstack:4566`, còn presigned URL dành cho browser dùng `localhost:4566`.

`depends_on: service_healthy` chỉ xác nhận dependency đã qua healthcheck. Nó không tự tạo schema, SQS queue hoặc S3 bucket. Khởi tạo lab theo thứ tự: data healthy → SQL schema → assets/SES → SQS/DLQ → Catalog metadata/banner → Redis reconciliation → app/frontend. Các lệnh đầy đủ, secrets và RSA key generation nằm trong [Local development, mục 1–4](../LOCAL-DEVELOPMENT.md).

| Script/schema | Tạo hoặc kiểm tra gì | Vì sao không thay bằng một lần `up` |
|---|---|---|
| [database/001_schema.sql](../../database/001_schema.sql) | Tables và inventory demo | MySQL healthy chưa có nghĩa schema ứng dụng đã tồn tại |
| [init_assets.py](../../scripts/init_assets.py) | Private S3 assets bucket, encryption/public-access-block, SES identity local | Worker cần bucket/identity trước khi phát hành vé |
| [init_queue.py](../../scripts/init_queue.py) | Main queue, DLQ, visibility/long-poll/redrive policies | Relay/Worker cần queue đã cấu hình, không chỉ LocalStack healthy |
| [seed_catalog.py](../../scripts/seed_catalog.py) | Event `PUBLISHED`, banner S3, cache invalidation | Event metadata phải cùng UUID với inventory MySQL |
| [reconcile_inventory.py](../../scripts/reconcile_inventory.py) | Đọc MySQL rồi dựng Redis inventory projection theo version | Redis cache không phải nguồn inventory gốc |
| [local_token.py](../../scripts/local_token.py) | Cấp JWT dev từ private key truyền qua stdin | Local default `AUTH_PROVIDER=jwt` cần access token để đặt vé |

`init_assets`, `init_queue` và `seed_catalog` có guard endpoint local-only. Trên AWS, Terraform sở hữu S3/SQS/SES; `bootstrap()` dùng `seed_aws_catalog` và reconciliation one-off tasks. Chạy initializer local trên AWS vừa sai ownership vừa có thể làm drift hoặc đổi policy, nên các script này chủ động từ chối. `reconcile_inventory` dùng được ở cả hai nơi vì nó chỉ rebuild projection từ DB, không tạo hạ tầng AWS.

### Volumes và LocalStack không có cùng vòng đời

`mysql-data`, `mongo-data`, `redis-data` giữ database/cache khi container bị xóa. `docker compose stop` dừng process, `start` chạy container hiện có; `down` xóa container/network nhưng giữ named volumes; `down --volumes` xóa cả volumes và dữ liệu của chúng.

LocalStack hiện pin Community `4.14.0` với `PERSISTENCE=0`. Sau restart/recreate process, S3 objects, SQS messages và SES state có thể mất dù ba DB volumes còn nguyên. Khi bật lại, dừng app, khởi tạo lại local resources theo runbook rồi mới bật Relay/Worker. Queue rỗng không khôi phục được message cũ; PDF đã mất cũng không được tái tạo chỉ bằng việc MongoDB/MySQL còn volume. Booking `ISSUED` cũ có thể trỏ tới object không còn tồn tại.

Không đổi MySQL/MongoDB password trong `.env` rồi mặc định user trong volume cũ đã được rotate: official image chỉ chạy phần tạo DB/user ban đầu với data directory mới. Chọn giữ dữ liệu và đổi credentials có chủ ý, hoặc reset lab theo runbook khi chấp nhận bỏ dữ liệu. SES local mô phỏng API, không chứng minh email đã gửi ra Internet.

### Môi trường Python cho offline tests

Container runtime tự mang dependency của nó. Python trên host chỉ cần cài thêm packages khi bạn muốn import source và chạy unit tests, không phải để shell up/down controller hoạt động. Tạo virtual environment riêng tại root và cài dependencies đã pin:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r services/booking/requirements.txt -r services/catalog/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Bộ kiểm tra của bản bàn giao có 50 offline/mock tests, cần Git cho các fixture kiểm tra image provenance. Chúng không gọi AWS thật, không khởi động database/service và không thay thế integration test, load test hoặc xác minh deployment. Cài packages lần đầu vẫn cần truy cập package registry/mirror nếu máy chưa cache. `.venv/` được ignore để không commit hoặc đưa vào Docker context.

## 4. Đọc orchestration theo call graph

Hai shell files là entrypoints mỏng: [aws-dev-up.sh](../../scripts/aws-dev-up.sh) kiểm tra Python rồi gọi `aws_dev_power.py up`; [aws-dev-down.sh](../../scripts/aws-dev-down.sh) yêu cầu chọn mode rồi gọi controller `down`. Chúng không chứa một bản Terraform/Docker workflow thứ hai.

| Source/function | Trách nhiệm |
|---|---|
| [aws_dev_power.py](../../scripts/aws_dev_power.py): `preflight`, `up`, `down` | Chọn nhánh theo state thực tế, reuse/build, điều phối stage |
| [deploy_dev.py](../../scripts/deploy_dev.py): các stage | Triển khai một thao tác cụ thể; có thể gọi riêng để resume/debug |
| [aws_dev_secrets.py](../../scripts/aws_dev_secrets.py) | Tạo/khôi phục sáu SSM secrets gốc, đối chiếu local backup trước khi ghi |
| [tf-dev.sh](../../scripts/tf-dev.sh) | Nạp hai ephemeral Terraform secrets từ SSM rồi forward args vào runtime root |
| [bake_mongo_ami.py](../../scripts/bake_mongo_ami.py) | Kiểm chứng builder qua SSM, tạo AMI và lưu AMI ID |
| [ssh-mongodb.sh](../../scripts/ssh-mongodb.sh) | Đọc MongoDB output, gửi key EC2 Instance Connect và mở SSH qua Endpoint |
| [provision_mongodb.py](../../scripts/provision_mongodb.py), [repair helper](../../scripts/repair_mongodb_bootstrap.py) | Provision users/URI hoặc sửa lỗi bootstrap đã nhận diện; giữ dữ liệu/node hiện có |

`up()` luôn gọi `prepare()`. Vì thế `aws-dev-up.sh` có thể chạy Terraform apply cho bootstrap/foundation, build image và thay đổi deployment; đây không chỉ là nút tăng ECS desired count. Đọc plan trước khi nhập `yes`. Cờ `--yes` chủ động bỏ native confirmation cho lần chạy đó, không phải preview. Không chạy hai controller đồng thời trên cùng environment/state.

### Các stage và thứ tự phụ thuộc

| Stage trong deploy_dev.py | Việc thực hiện và điều kiện chuyển tiếp |
|---|---|
| `prepare()` | Kiểm tra helper/account, tạo state bucket, init hai S3 backends, apply foundation, restore SSM |
| `images()` | Build/push năm images từ clean Git tree; lưu `application_image_tag` |
| `bake()` | Tạo VPC/builder tạm khi runtime trống, validate AMI, xóa builder sau thành công |
| `data_stage()` — CLI `data` | Tạo RDS, Redis, EC2 MongoDB private/EICE; chưa bật app |
| `bootstrap()` | Giữ service desired 0, chạy MySQL task, ghi application URIs vào SSM, provision MongoDB, seed Catalog và reconcile Redis |
| `start()` | Kiểm tra SES/SSM, desired 1, chờ ECS stable, poll `/events` tối đa 180 giây |
| `frontend()` | Build static bundle với API/Cognito hiện tại, upload S3 và invalidate CloudFront |
| `status()` | Đọc output/trạng thái ECS và in API/frontend URLs |
| `stop()` | Scale đúng bốn service trong state về 0 qua ECS API; chờ stable/inactive |
| `destroy()` | Xóa runtime trước, tùy mode xóa AMI/foundation/SSM/state bucket sau |

Initialization tasks dùng Fargate On Demand platform `1.4.0`, không phải services chạy liên tục. `task()` xác nhận task đã `STOPPED` và container `exitCode=0`; một task bị kill/error cũng dừng nhưng không thành công. Worker/Relay thường trực chỉ được bật sau khi schema/users/queue/projection sẵn sàng.

`start()` cần đúng demo UUID, `remaining` và `inventory_version` là số nguyên không âm. Điều đó chứng minh Catalog đọc được metadata/projection qua HTTP API, chưa chứng minh POST booking, PDF hay SES delivery đã hoàn tất.

### Controller chọn nhánh bằng dữ liệu nào

`service_activity()` đọc **desired/running/pending** từ AWS ECS. Không chỉ tin `service_desired_count` trong file local: file có thể cũ trong lúc AWS vẫn đang chạy task.

| Trạng thái phát hiện | Cách `up()` xử lý |
|---|---|
| SES sender chưa verified | Dừng sau foundation, trước khi tạo builder/data; mở email rồi chạy lại |
| Không có tracked data/compute | Kiểm tra SQS/DLQ cũ; dùng AMI đã lưu còn hợp lệ hoặc bake; tạo data và bootstrap |
| Đủ RDS/Redis/MongoDB, app đều 0 hoặc chưa có | Chờ/repair MongoDB có guard, chạy bootstrap idempotent rồi start |
| Đủ data, bất kỳ ECS desired/running/pending > 0 | Giữ dữ liệu, bỏ bootstrap/seed, thực hiện start/deploy/frontend/status |
| Data/compute tạo hoặc xóa dở, output không đủ | Dừng để kiểm tra plan/state và resume stage; không tự disable compute hay reset DB |

`available_saved_ami()` kiểm tra ID trong `mongodb-ami.auto.tfvars`, owner, trạng thái available, kiến trúc x86_64 và project/environment/role tags. AMI mới hơn không tự thay một MongoDB node đang tồn tại. `ensure_data_nodes_running()` có thể start lại đúng RDS/MongoDB instance đã bị stop thủ công và chờ sẵn sàng; node đang deleting/terminated hoặc trạng thái bất thường cần xử lý trước.

Sau `down runtime`, database bị xóa nhưng SQS/DLQ ở foundation còn. Message booking cũ không còn DB tương ứng. `--purge-queues` chỉ được dùng khi toàn bộ data/runtime đã bị xóa và không có app hoạt động; nó xóa cả hai queue rồi chờ 60 giây. Counters SQS là approximate, nên dùng lựa chọn này khi chủ động bỏ toàn bộ lượt test cũ, kể cả counter đang 0. Không dùng nó để xử lý backlog của database còn giữ.

### Reuse image phải khớp source

`IMAGE_INPUTS` trong controller liệt kê đúng source mà Dockerfiles sử dụng: app/requirements/Dockerfile của bốn service, `shared`, bootstrap tool, SQL migrations, banner và các scripts được copy vào image. `image_inputs_changed()` so sánh commit SHA trong tag cũ với Git working tree và kiểm tra source chưa track.

- Backend/shared/SQL/Docker input đổi hoặc commit cũ không có trong local history: cần build từ source đã commit.
- Chỉ đổi tài liệu hoặc script điều phối không nằm trong image: có thể reuse image cũ.
- `saved_images_exist()` còn yêu cầu đủ năm ECR images hợp lệ. AccessDenied/token expired không được hiểu thành ImageNotFound.

Tag hiện là `dev-1.0.0-<git-sha>`; semver prefix không tự tăng. `--rebuild-images` đưa workflow vào stage images của commit hiện tại. Nếu tag đó đã có trong ECR, stage giữ image đã publish để bảo toàn immutable tag; sửa source/lock rồi commit mới để có build mới. Không ghi đè `latest` để che việc source và deployment không cùng phiên bản. Git/Docker Hub commands đầy đủ nằm trong [Local development, mục 6](../LOCAL-DEVELOPMENT.md#6-git-và-docker-hub).

## 5. Profiles, secrets và ba Terraform states

AWS CLI session của profile `flash-ticket-dev` và profile vận hành `flash-ticket-terraform` phục vụ hai vai trò trong cấu hình lab: profile đầu giữ phiên đăng nhập tạm thời; profile sau được CLI/helpers/Terraform sử dụng. Tên profile không tự tạo quyền; máy mới phải cấu hình theo [chương Terraform/AWS](04-terraform-and-aws.md). Dùng IAM identity quản trị phù hợp, không root access keys.

```bash
aws login --profile flash-ticket-dev
aws sts get-caller-identity --profile flash-ticket-terraform --region us-east-1
```

STS xác nhận account/ARN thực tế trước khi tạo hoặc xóa. Helpers dùng `us-east-1` và workspace `default`; environment đã được tách bằng các root directories. Đặt `TF_WORKSPACE` khác hoặc giữ `.terraform/environment` khác default làm controller dừng. `init -reconfigure` không tự chuyển workspace; không đổi workspace tùy tiện để làm mất dấu state đang quản lý resources.

Khi session hết hạn, `auth_error_hint()` hướng dẫn renew login rồi kiểm tra STS và chạy lại lệnh vừa lỗi. Phiên AWS CLI của maintainer không được đóng gói vào image; ECS ứng dụng nhận IAM task role. Token ECR dùng cho Docker login được truyền qua stdin, trong Docker config tạm để tránh Windows credential helper lỗi trên WSL.

| Dữ liệu vận hành | Nơi giữ và lý do |
|---|---|
| `.env`, RSA key local trong `.secrets/` | Local-only; Compose/JWT lab cần, không đưa vào image/Git |
| `.secrets/aws-dev-secrets.json` | Backup sáu secrets gốc theo account/region; giữ để khôi phục SSM và kiểm tra mismatch |
| SSM `/flash-ticket/dev/...` | SecureString secrets và application URIs; task execution role inject vào container khi task khởi động |
| `launch.auto.tfvars`, `mongodb-ami.auto.tfvars`, account/SES tfvars, `backend.hcl` | Configuration sinh trên máy, bị ignore; Git clone không khôi phục các file này |
| `SENSITIVE` trong deploy_dev.py | Set trong RAM để redact giá trị đã biết khi báo lỗi; không phải password vault hoặc backup |

AWS JSON request chứa secret được ghi vào file tạm quyền `0600`, sử dụng file URI và dọn khi kết thúc call. Không truyền URI/password trực tiếp qua arguments hay in ra console. `tf-dev.sh` nạp master password/Redis token thành ephemeral `TF_VAR_*`; chúng phải tồn tại trong SSM đến khi runtime destroy xong. Đổi SSM không tự rotate user DB; container đang chạy cũng cần task mới để nhận secret inject mới.

| Root | State | Phạm vi |
|---|---|---|
| `infra/bootstrap` | Local `terraform.tfstate` | S3 state bucket |
| `infra/environments/dev-foundation` | S3 key `environments/dev-foundation/terraform.tfstate` | ECR/IAM/S3/SQS/SES/Cognito/CloudFront |
| `infra/environments/dev` | S3 key `environments/dev/terraform.tfstate` | VPC, builder, data layer, ECS/API/Cloud Map |

Giữ state/backup local bootstrap, remote states và secrets backup an toàn. State chứa mapping để Terraform biết object nào thuộc dự án; xóa state file không xóa AWS resources và có thể làm lần apply sau cố tạo trùng. Xem thêm ownership/state ở [chương 04](04-terraform-and-aws.md).

## 6. Dừng, destroy và credit

Các entrypoint cốt lõi:

```bash
bash scripts/aws-dev-up.sh
bash scripts/aws-dev-down.sh stop
bash scripts/aws-dev-down.sh runtime
bash scripts/aws-dev-down.sh all
```

Chọn **một** mode down phù hợp, không chạy cả ba theo thói quen. Lần đầu cần sender, dùng lệnh nhập email trong [runbook up/down](../AWS-DEV-POWER.md). Khi SES identity hoặc Cognito User Pool được tạo mới sau khi đã bị xóa, thực hiện verification/signup tương ứng như phần trên; bật lại service với foundation còn nguyên không yêu cầu tạo lại các identity này.

| Mode | Tác động | Điều vẫn còn |
|---|---|---|
| `stop` | ECS API scale bốn services về 0; không Terraform apply | DB nodes vẫn chạy, foundation/storage giữ nguyên |
| `runtime` | Destroy VPC, app/API và RDS/Redis/MongoDB; dữ liệu dev mất, bỏ qua RDS final snapshot | Foundation, queues, SSM, ECR/S3, AMI/snapshots, state backend |
| `all` | Runtime → project AMI/snapshots → foundation → SSM → state bucket cuối cùng | Source, local secrets/backups, IAM login user, Budget và tài nguyên ngoài phạm vi quản lý |

`stop()` chỉ thao tác các service được xác minh từ output, chờ service ACTIVE stable hoặc service DRAINING inactive. Nó không apply toàn bộ cấu hình để “tắt”, nên không dựng lại resources drifted trong lúc người dùng định ngừng compute.

Khi destroy runtime, `destroy_root()` truyền các disable flags qua `-var` cho riêng lần destroy để tránh AMI/data lookups không cần thiết. Chỉ sau runtime destroy thành công, script mới lưu flags off vào `launch.auto.tfvars`. Hủy ở native confirmation không làm file startup bị đổi sang off. Nếu destroy đã chạy một phần rồi lỗi/ngắt, state/resources có thể dở dang: giữ state và resume cleanup, không mặc định mọi thứ đã dừng.

### Vì sao cleanup có một plan chuẩn bị riêng

S3 bucket/ECR repository có objects/images cần `force_destroy`/`force_delete` được lưu trong provider state trước khi xóa. `adopt_cleanup_flags()` chỉ target các S3/ECR addresses đã biết và đang tracked, lập saved plan với `-refresh=false`. `validate_cleanup_plan()` so before/after: chỉ force flag sang boolean true được phép; không cho đổi bucket name, ARN, region, tags, không create/delete/replace, và không nhận unknown thực sự.

Một CloudFront configuration update không được phép chen vào bước chuẩn bị này. Sau khi flags được xác nhận/applied, Terraform destroy bình thường xử lý full dependency graph. `-target`/`-refresh=false` ở đây là ngoại lệ giới hạn cho cleanup flags, không phải cách deploy thường ngày. State bucket phải được giữ đến cuối vì nó chứa state của hai root còn lại; SSM phải được giữ đến khi runtime không cần đọc password nữa.

Full cleanup chỉ xóa objects thuộc các states và SSM/AMI scope mà script quản lý, không xóa mọi resource trong account. Nếu đã tạo tài nguyên thủ công ngoài state hoặc cleanup bị lỗi, kiểm tra Billing và dịch vụ đã dùng. Billing có độ trễ; đóng terminal không thay đổi trạng thái resources.

### Những khoản vẫn tiêu credit khi không có khách

Fargate chạy task, public IPv4 của dev tasks/builder, RDS, Redis, EC2/EBS, AMI snapshots, S3/ECR storage và CloudWatch có thể tiếp tục tính usage. Không NAT Gateway/ALB giúp giảm một phần chi phí, không biến toàn bộ lab thành miễn phí. Credit của account mới không được hiểu thành mọi instance đều có quyền lợi Free Tier cũ.

- Scale ECS về 0 không stop data layer. Redis không có thao tác stop node để giữ nguyên rồi miễn node cost; muốn ngừng node cost phải xóa.
- RDS stop vẫn giữ storage/backup và tự khởi động lại sau tối đa bảy ngày. EC2 stop vẫn giữ EBS; AMI vẫn giữ snapshot.
- `runtime` ngừng các data/compute resources bị destroy nhưng giữ foundation/storage. AWS Budget cảnh báo, không tự dọn lab.
- Dev `desired_count=1` với Spot có thể thiếu capacity/bị thu hồi. Rolling update không đủ để hứa zero downtime; HA/autoscaling/load testing cần công việc riêng.

Sau khi bật, thử một booking thật: nhập email nhận vé, xem `ISSUED`, tải PDF rồi kiểm tra mail. `email_status=SENT` chỉ chứng minh SES accepted; sender/recipient sandbox phải verified, Cognito verification là bước khác. Startup smoke check hoặc email mô phỏng LocalStack không chứng minh thư đã vào inbox. Tiếp tục sang [06. Troubleshooting và recovery](06-troubleshooting-and-recovery.md) để đọc logs, phân biệt failure theo stage và phục hồi mà không xóa nhầm dữ liệu.
