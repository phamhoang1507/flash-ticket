# Local development từ clone mới

Chạy tại root repository, trong Bash/WSL. Cần Git, Docker Engine/Desktop đang chạy, Docker Compose v2, Python 3 và OpenSSL. Node trên host không bắt buộc vì frontend build trong Docker. LocalStack cố ý pin Community `4.14.0` để lab không cần tài khoản; đây không phải release LocalStack mới nhất.

Với máy chưa có repository, clone đúng branch triển khai hiện tại:

```bash
git clone --branch feat/aws-dev-deploy https://github.com/phamhoang1507/flash-ticket.git
cd flash-ticket
```

Nếu đã có repository và đang ở branch local theo dõi bản deploy, giữ nguyên thư mục hiện tại rồi cập nhật bằng `git pull --ff-only origin feat/aws-dev-deploy`.

## 1. Tạo secrets và khóa JWT

Tạo `.env` một lần từ template, không ghi đè cấu hình đang có:

```bash
python3 - <<'PYENV'
import os
from pathlib import Path
import secrets

path = Path('.env')
if path.exists():
    raise SystemExit('.env already exists; keep it or edit it deliberately.')
content = Path('.env.example').read_text()
for name in ('MYSQL_ROOT_PASSWORD', 'MYSQL_PASSWORD', 'MONGO_PASSWORD',
             'REDIS_PASSWORD', 'TICKET_SIGNING_SECRET'):
    content = content.replace(f'{name}=GENERATE_LOCALLY', f'{name}={secrets.token_hex(32)}')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(content)
PYENV

mkdir -p .secrets
chmod 700 .secrets
if [ ! -f .secrets/private.pem ]; then
  (umask 077; openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out .secrets/private.pem)
fi
openssl pkey -in .secrets/private.pem -pubout -out .secrets/public.pem
chmod 600 .secrets/private.pem
chmod 644 .secrets/public.pem
```

`.env` được Compose đọc tự động; không `source` file này vào shell dùng AWS thật. Compose chỉ bind-mount file public key có quyền `0644`; API UID 10001 không cần truy cập thư mục `.secrets` trên host. Private key không mount vào API. Giữ các file này ở local và không commit.

## 2. Build và chạy data services

Compose tự merge `compose.yaml` với `compose.override.yaml`. Không thêm `-f compose.yaml` đơn lẻ nếu muốn chạy cả ứng dụng.

```bash
docker compose config --quiet
docker compose build
docker compose up -d mysql mongo redis localstack
docker compose ps
```

Đợi cả bốn data services healthy; xem lỗi nếu chưa sẵn sàng:

```bash
docker compose logs --tail=100 mysql mongo redis localstack
```

Không khởi động Relay/Worker trước khi SQS và schema đã được tạo.

## 3. Khởi tạo schema, assets, queue và event mẫu

MySQL local dùng user `flash` do official image tạo, có quyền trên database local. Trên AWS, bootstrap tạo application user hạn chế quyền và migration ledger riêng; xem [MySQL bootstrap](mysql-bootstrap.md).

```bash
docker compose exec -T mysql sh -c   'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql -u flash flash_ticket'   < database/001_schema.sql

docker compose run --rm --no-deps worker python -m scripts.init_assets
docker compose run --rm --no-deps relay python -m scripts.init_queue
docker compose run --rm --no-deps catalog python -m scripts.seed_catalog
docker compose run --rm --no-deps relay python -m scripts.reconcile_inventory
```

Migration local tạo event inventory 100 vé; seed metadata dùng cùng UUID `11111111-1111-4111-8111-111111111111`. Chạy lại với dữ liệu hiện có không reset tồn kho. Với schema đã phát triển qua nhiều migration, phải chạy các migration còn thiếu theo thứ tự, không sửa file cũ rồi giả định bootstrap sẽ cập nhật schema.

Bucket S3 giữ private cả banner và ticket; Catalog local sinh presigned banner URL. Giữ `ASSETS_PUBLIC_BASE_URL` trống để tránh đổi sang URL public không được bucket cho phép. S3 public endpoint ký cho Browser là `localhost:4566`, khác endpoint nội bộ `localstack:4566` của container.

## 4. Bật ứng dụng và đặt vé

```bash
docker compose up -d booking catalog relay worker frontend
docker compose ps
curl --fail http://localhost:8001/readyz
curl --fail http://localhost:8002/events
```

Mở **http://localhost:8080**. Nếu port bị chiếm, thay `BOOKING_PORT`/`CATALOG_PORT` trong `.env`; frontend vẫn gọi container qua Compose network. Lấy port thực tế bằng `docker compose port booking 8000` hoặc `docker compose port catalog 8000`.

Tạo token một giờ; private key truyền qua stdin, không xuất hiện trong command arguments hoặc image:

```bash
TOKEN="$(docker compose run --rm -T --no-deps booking   python scripts/local_token.py --subject local-user < .secrets/private.pem)"
printf '%s\n' "$TOKEN"
```

Dán token vào giao diện, chọn event và email nhận vé. SES local chỉ giả lập API, không gửi thư ra Internet. Theo dõi booking chuyển `PENDING` → `ISSUED`, sau đó tải PDF. Thêm `--admin` vào lệnh cấp token khi cần scope `events:write`; `POST /events` chỉ tạo DRAFT, chưa mở bán.

```bash
docker compose logs --tail=100 -f booking catalog relay worker
```

## 5. Dừng, khôi phục và reset

```bash
docker compose stop
docker compose start
```

**LocalStack dùng `PERSISTENCE=0`**: sau khi process LocalStack restart hoặc container bị recreate, phải tạo lại S3/SQS/SES. Giữ MySQL/MongoDB/Redis volumes không giữ được objects/message LocalStack.

Để khởi tạo lại an toàn: dừng ứng dụng, bật data services, chạy lại bước 3 rồi bật ứng dụng. Dữ liệu ticket S3 hoặc message đã mất không tự khôi phục; booking `ISSUED` cũ có thể trỏ tới PDF không còn tồn tại. Với lượt test mới không cần dữ liệu cũ, reset toàn bộ lab:

```bash
docker compose down --volumes
docker compose up -d mysql mongo redis localstack
```

Lệnh đầu **xóa database/cache volumes**, sau đó chạy lại bước 3 và 4. Nếu muốn giữ database, dùng `docker compose down` không có `--volumes`. Không đổi password `.env` rồi dùng lại volume cũ: biến khởi tạo database chỉ có tác dụng với data directory mới.

## 6. Git và Docker Hub

Sau khi review source, commit thay đổi trên branch làm việc trước khi gắn image tag Git SHA. Không stage `.env`/`.secrets`.

```bash
git switch -c feat/update-local-stack
git add services shared scripts database frontend assets tools compose.yaml compose.override.yaml
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: update local ticket stack"
git push -u origin HEAD

read -r -p 'Docker Hub username: ' TASK_DOCKERHUB_USER
TASK_TAG="dev-1.0.0-$(git rev-parse --short=12 HEAD)"
docker login --username "$TASK_DOCKERHUB_USER"
for TASK_SERVICE in booking catalog relay worker; do
  docker build --file "services/$TASK_SERVICE/Dockerfile"     --tag "$TASK_DOCKERHUB_USER/flash-ticket-$TASK_SERVICE:$TASK_TAG" .
  docker push "$TASK_DOCKERHUB_USER/flash-ticket-$TASK_SERVICE:$TASK_TAG"
done
docker build --tag "$TASK_DOCKERHUB_USER/flash-ticket-frontend:$TASK_TAG" ./frontend
docker push "$TASK_DOCKERHUB_USER/flash-ticket-frontend:$TASK_TAG"
```

Các lệnh push cần repository/credentials Docker Hub của bạn. Chúng không cập nhật ECR/ECS; dùng AWS deployment guide cho việc đó. Không dùng tag `latest` làm đầu vào triển khai.
