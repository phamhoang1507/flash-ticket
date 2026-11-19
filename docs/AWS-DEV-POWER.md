# Bật, dừng và xóa AWS dev của Flash-Ticket

Hai file thao tác chính là `scripts/aws-dev-up.sh` và `scripts/aws-dev-down.sh`. Chạy chúng từ repository `flash-ticket` trên WSL, với AWS profile `flash-ticket-terraform` và region `us-east-1` đã cấu hình.

**`down stop` chỉ dừng bốn ECS services. RDS, Redis và EC2 MongoDB vẫn chạy và tiếp tục dùng credits.** Muốn xóa các tài nguyên compute/data của môi trường dev, chọn `down runtime`. Muốn dọn cả runtime, foundation và Terraform backend của dự án, chọn `down all`.

Các lệnh dưới đây chỉ tác động phạm vi Flash-Ticket mà script/Terraform quản lý. Chúng không quét và xóa toàn bộ AWS account.

## 1. Cập nhật hai script

Trong repository hiện tại:

```bash
cd ~/chatgpt/flash-ticket

git status --short
git pull --ff-only origin feat/aws-dev-deploy
```

Nếu Git báo có thay đổi local gây xung đột, xử lý các thay đổi đó trước khi pull. Không dùng `git reset --hard` để bỏ qua thông báo.

Code script được commit sẵn trên branch. Bạn không cần tạo thêm commit chỉ để chạy lệnh bật hoặc tắt. Docker images hiện có cũng không cần build lại chỉ vì cập nhật script vận hành.

Có thể xem hướng dẫn của từng file:

```bash
bash scripts/aws-dev-up.sh --help
bash scripts/aws-dev-down.sh
```

Lệnh `down` không có mode chỉ hiển thị cách dùng, không tự xóa tài nguyên.

Các công cụ local cần sẵn sàng gồm AWS CLI v2, Terraform, Python 3, Git, Docker đang chạy và OpenSSH client. Dùng AWS credentials/profile đã thiết lập cho dự án; không dùng root access keys. Nếu phiên đăng nhập AWS hết hạn, đăng nhập lại bằng cách bạn đã cấu hình trước khi chạy script.

## 2. Đang có hạ tầng: bật ứng dụng

Nếu bạn đang chạy AWS hoặc vừa dùng `down stop`, dùng cùng một lệnh:

```bash
bash scripts/aws-dev-up.sh
```

Script đọc cấu hình và trạng thái hiện có rồi chọn phần cần thực hiện. Nó sử dụng image tag đã lưu khi đủ cả năm ECR images: Booking, Catalog, Relay, Worker và MySQL Bootstrap. Nếu thiếu image cần thiết, script sẽ thực hiện nhánh build/push tương ứng thay vì tham chiếu một tag chưa tồn tại.

Khi data layer đã tồn tại và có ECS service đang chạy, script tiếp tục nhánh start, publish frontend và kiểm tra trạng thái. Khi data layer tồn tại nhưng các services đều đang dừng, script thực hiện các bước bootstrap có kiểm tra rồi bật lại ứng dụng. Bootstrap sử dụng migrations và kiểm tra dữ liệu hiện có; đây không phải thao tác đặt inventory về số vé ban đầu.

Nếu RDS hoặc EC2 MongoDB đã bị stop thủ công, script bật lại đúng instance lấy từ Terraform output và chờ nó sẵn sàng. Nó giữ nguyên database/EBS hiện có. Các trạng thái không an toàn như deleting, terminated hoặc hạ tầng tạo dở sẽ làm script dừng để bạn kiểm tra.

Nếu script phát hiện trạng thái compute chưa đủ thông tin để xử lý an toàn, nó dừng với thông báo. Không tự tắt thêm services hoặc destroy tài nguyên để vượt qua thông báo đó.

## 3. Đã xóa toàn bộ AWS: dựng lại dự án

Nếu đã dùng `down all`, chạy lệnh có SES sender:

```bash
read -r -p "SES sender email: " TASK_SES_SENDER

bash scripts/aws-dev-up.sh --sender "$TASK_SES_SENDER"
```

Chọn địa chỉ email bạn sở hữu và có thể nhận thư xác minh từ Amazon SES. Nếu sender đã được lưu và còn phù hợp, script có thể dùng lại; truyền `--sender` giúp chỉ rõ giá trị cho lần dựng mới.

Script lần lượt xử lý các phần cần thiết:

1. Khôi phục Terraform backend/foundation, các cấu hình SSM và kiểm tra SES sender.
2. Kiểm tra ECR images; tái sử dụng đủ bộ images đã lưu hoặc build/push khi cần.
3. Tái sử dụng MongoDB AMI thuộc dự án nếu còn hợp lệ; nếu không còn, tạo builder tạm để bake AMI.
4. Dựng data layer, bootstrap MySQL/MongoDB và cấu hình kết nối.
5. Bật ECS services, publish frontend và in kết quả kiểm tra cùng các URLs.

Nếu SES sender chưa được xác minh, script dừng ở bước kiểm tra sớm, trước khi dựng data layer. Mở email xác minh của Amazon SES, bấm liên kết xác nhận rồi chạy lại cùng lệnh. Một số tài nguyên foundation đã tạo trước bước kiểm tra vẫn có thể tồn tại; cần dọn chúng thì dùng `down all`.

Dựng từ đầu cần chờ CloudFront, RDS, EC2/AMI và ECS hoàn tất. Giữ terminal để đọc tiến trình; không mở một lần `up` thứ hai khi lần đầu còn chạy.

## 4. Sau `down runtime`: dựng lại data và ứng dụng

```bash
bash scripts/aws-dev-up.sh
```

Foundation vẫn còn nên script có thể tái sử dụng S3, ECR, Cognito, CloudFront và MongoDB AMI. Tuy nhiên, MySQL và MongoDB đã bị xóa ở lần `down runtime`: các bookings và dữ liệu trong DB cũ không tự xuất hiện trở lại. Đây là tạo data layer mới, không phải restore backup.

SQS và DLQ được giữ cùng foundation. Nếu còn message của DB cũ, script từ chối bootstrap data mới để tránh xử lý các booking không còn tồn tại.

Các bộ đếm SQS là approximate, nên không bảo đảm phản ánh ngay mọi message. Khi đã quyết định bỏ dữ liệu của lượt test cũ sau `down runtime`, dùng `--purge-queues` để xóa job cũ trước khi tạo database mới, kể cả khi bộ đếm đang hiện 0.

Nếu bạn chủ động chấp nhận bỏ các jobs cũ, chạy:

```bash
bash scripts/aws-dev-up.sh --purge-queues
```

**`--purge-queues` xóa messages đang nằm trong booking queue và DLQ.** Script chỉ cho phép nhánh này khi tạo data layer mới và ứng dụng đang dừng; sau purge nó chờ 60 giây cho thao tác SQS có hiệu lực. Không dùng cờ này để chữa lỗi email hoặc xóa backlog của hệ thống đang hoạt động.

## 5. Ba mức tắt/xóa

| Lệnh | Thao tác | Dữ liệu và tài nguyên còn lại |
|---|---|---|
| `down stop` | Đưa cả bốn ECS services về `desired_count = 0`, chờ services ổn định | Giữ DB và toàn bộ hạ tầng. RDS, Redis, MongoDB vẫn chạy |
| `down runtime` | Destroy Terraform runtime dev, gồm VPC, ECS và data layer | Giữ foundation: ECR, S3, SQS/DLQ, Cognito, CloudFront và cấu hình liên quan; giữ AMI, SSM và backend state |
| `down all` | Xóa runtime, foundation, các SSM/AMI/snapshot thuộc phạm vi cleanup của dự án; dọn Terraform backend sau cùng | Giữ AWS account, IAM đăng nhập quản trị, Budget và dữ liệu local |

### Dừng ECS để tiếp tục học sau

```bash
bash scripts/aws-dev-down.sh stop
```

Bật lại:

```bash
bash scripts/aws-dev-up.sh
```

Mode `stop` gọi AWS ECS API để đặt desired count về 0 và lưu cấu hình quản lý tương ứng. Nó không chạy Terraform apply, không dựng thêm tài nguyên và không có bước xác nhận Terraform.

Vì cả hai API services dừng nên frontend có thể còn mở được nhưng API sẽ không xử lý request. Relay/Worker cũng dừng; các jobs trong SQS không được xử lý trong thời gian đó.

### Xóa runtime để ngừng chi phí các DB nodes

```bash
bash scripts/aws-dev-down.sh runtime
```

Lệnh này xóa MySQL, Redis, EC2 MongoDB và các tài nguyên runtime do Terraform dev quản lý. Đọc kỹ Terraform plan trước khi xác nhận. Các bookings và dữ liệu DB sẽ mất nếu không có backup phù hợp.

S3 assets có thể vẫn chứa vé PDF của lần chạy cũ, nhưng DB mới không còn booking tương ứng. S3 objects, ECR images, MongoDB AMI/snapshot, CloudWatch Logs và Terraform state còn lưu trữ vẫn có thể sử dụng credits.

### Xóa toàn bộ phần AWS do dự án quản lý

```bash
bash scripts/aws-dev-down.sh all
```

Mode này tương ứng nhánh cleanup đầy đủ `destroy --all --bootstrap-bucket`: xóa runtime trước, foundation sau, dọn SSM và AMI/snapshots thuộc dự án, rồi mới dọn backend state bucket. Không xóa state bucket hoặc file Terraform state bằng tay trước khi quá trình này hoàn tất.

Đây là lựa chọn để dọn môi trường học hiện tại, gồm dữ liệu DB, files trong các buckets bị cleanup, ECR images và các tài nguyên khác nằm trong state/phạm vi cleanup được nêu. Các S3 URLs, API URL và Cognito cấu hình cũ không còn dùng được. Khi dựng lại, sử dụng URLs do lần `up` mới in ra.

Lệnh không xóa Docker Hub images, Git repository, Docker images/volumes local, các file `.secrets` local, AWS Budget hoặc IAM user/profile đang dùng để đăng nhập. Tài nguyên bạn tạo thủ công ngoài Terraform state và ngoài bộ lọc cleanup của dự án cũng không tự bị xóa.

Nếu lệnh cleanup báo lỗi, không coi đó là đã xóa hết. Đọc lỗi, xử lý nguyên nhân và chạy lại cùng mode; giữ Terraform state để tiếp tục quản lý những tài nguyên còn lại.

### Cleanup dừng vì CloudFront hoặc resource không liên quan

Nếu thấy thông báo cleanup preparation bị chặn tại CloudFront, runtime có thể đã được xóa nhưng foundation chưa được dọn hết. Bước chuẩn bị phải lưu `force_destroy`/`force_delete` vào Terraform state trước khi xóa các bucket/repository đang có dữ liệu.

Script mới chỉ tạo targeted plan cho đúng các S3 bucket/ECR repository còn trong state, tắt refresh riêng bước này, rồi kiểm tra plan chỉ đổi cờ xóa. Nó từ chối mọi create/delete/replacement, thay đổi CloudFront, thay đổi field khác và giá trị chưa xác định. Bước Terraform destroy phía sau vẫn dùng dependency graph đầy đủ.

Lấy bản sửa rồi tiếp tục cleanup:

```bash
git pull --ff-only origin feat/aws-dev-deploy
bash scripts/aws-dev-down.sh all
```

Không chạy `up` để sửa lỗi cleanup. Nếu phần runtime đã xóa xong, script bỏ qua state runtime trống và tiếp tục các phần còn lại. Giữ local bootstrap state và backend cho đến khi toàn bộ cleanup hoàn tất.

## 6. Xác nhận tự động bằng `--yes`

Mặc định, các thao tác Terraform cần xác nhận vẫn hiển thị cho bạn xem. Sau khi hiểu rõ mode và dữ liệu sẽ bị xóa, có thể dùng:

```bash
bash scripts/aws-dev-up.sh --yes

bash scripts/aws-dev-down.sh runtime --yes

bash scripts/aws-dev-down.sh all --yes
```

`--yes` cho phép các thao tác tương ứng chạy với Terraform auto-approve. Nó không phải chế độ preview. Không thêm cờ này khi bạn vẫn cần đọc và xác nhận plan. Mode `stop` không cần cờ này vì không chạy Terraform.

## 7. Khi thực sự thay đổi code ứng dụng

Lệnh `up` ưu tiên images đã có để tránh build lại mỗi lần bật dev. Sau khi sửa Booking/Catalog/Relay/Worker hoặc MySQL Bootstrap, cần commit source rồi yêu cầu build mới:

```bash
git status --short

git add services shared database tools/mysql-bootstrap
git diff --cached --check
git commit -m "feat: update ticket processing"
git push origin HEAD:feat/aws-dev-deploy

bash scripts/aws-dev-up.sh --rebuild-images
```

Chỉ stage những thay đổi bạn thực sự muốn đưa vào commit. Không commit `.env`, `.secrets`, Terraform state hoặc file chứa mật khẩu. Build sử dụng Git SHA để xác định source; nếu working tree có thay đổi chưa được xử lý, script có thể từ chối build để tránh gắn tag SHA cho một bộ code khác commit.

Script tự thực hiện các lệnh Docker build/push cần thiết và sử dụng image tags theo phiên bản/commit; không cần đẩy lại thủ công bằng tag `latest`. Thay đổi file hướng dẫn hoặc script vận hành rồi pull bản đã commit không đồng nghĩa phải dùng `--rebuild-images`.

Frontend được build/publish trong luồng `up`, với cấu hình API/Cognito lấy từ hạ tầng hiện tại. Khi dựng lại toàn bộ, bước này cập nhật các URLs mới vào frontend.

## 8. Kiểm tra thành quả sau khi bật

Kết thúc `up`, đọc URLs và trạng thái mà script in ra. Có thể lấy lại API URL và gọi endpoint công khai:

```bash
TASK_API_URL="$(
  terraform -chdir=infra/environments/dev output -raw api_url
)"

curl --fail-with-body --silent --show-error \
  "$TASK_API_URL/events"
```

Request `GET /events` phải trả JSON danh sách sự kiện. Mở trực tiếp API root `/` có thể nhận `404`: hệ thống không khai báo route `/`. Dùng `/events` để kiểm tra Catalog; không tự thêm `/api` vào URL này.

Lấy frontend URL:

```bash
terraform -chdir=infra/environments/dev-foundation output -json web \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["frontend_url"])'
```

Mở URL CloudFront đó, đăng nhập qua Cognito, chọn sự kiện và đặt vé. Dùng email người nhận hợp lệ ở giao diện để thử luồng gửi vé. Booking ID là UUID do API trả về; không nhập số thứ tự như `1` vào endpoint đọc booking.

Xem metadata ECS:

```bash
terraform -chdir=infra/environments/dev output -json ecs
```

Trạng thái ECS ổn định chứng minh tasks đã được scheduler triển khai theo cấu hình. Kiểm tra API, booking và ticket trên giao diện vẫn cần thực hiện để xác nhận luồng ứng dụng.

## 9. Bật ECS thủ công khi cần kiểm tra riêng

Thao tác thông thường vẫn là `bash scripts/aws-dev-up.sh`. Nếu chỉ cần bật lại tasks trong runtime đang tồn tại, có thể dùng AWS CLI sau. Data layer và task definitions phải còn tồn tại; lệnh này không dựng lại hạ tầng đã destroy.

```bash
TASK_ECS_CLUSTER="$(
  terraform -chdir=infra/environments/dev output -json ecs \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["cluster_name"])'
)"

mapfile -t TASK_ECS_SERVICES < <(
  terraform -chdir=infra/environments/dev output -json ecs \
    | python3 -c 'import json, sys; print("\n".join(json.load(sys.stdin)["service_names"].values()))'
)

for TASK_ECS_SERVICE in "${TASK_ECS_SERVICES[@]}"; do
  aws ecs update-service \
    --profile flash-ticket-terraform \
    --region us-east-1 \
    --cluster "$TASK_ECS_CLUSTER" \
    --service "$TASK_ECS_SERVICE" \
    --desired-count 1 \
    --query 'service.{Name:serviceName,Desired:desiredCount,Running:runningCount}' \
    --output table
done

aws ecs wait services-stable \
  --profile flash-ticket-terraform \
  --region us-east-1 \
  --cluster "$TASK_ECS_CLUSTER" \
  --services "${TASK_ECS_SERVICES[@]}"
```

Đây là thay đổi trực tiếp qua AWS API. Terraform vẫn giữ cấu hình desired count của lần thao tác trước; một lần apply sau có thể đưa services về giá trị cấu hình đó. Chạy lại `up` để trở về luồng quản lý thống nhất. Nếu service thiếu capacity Fargate Spot hoặc task không healthy, tăng desired count không tự sửa nguyên nhân; cần đọc ECS events/CloudWatch Logs.

## 10. Email và chi phí cần hiểu đúng

Hai script này không sửa những lỗi gửi email còn tồn tại trong ứng dụng hoặc cấu hình SES. Khi account SES còn ở sandbox, cần xác minh cả sender và recipient theo yêu cầu SES. Email trong Cognito và email nhận vé trên giao diện cũng cần được kiểm tra đúng giá trị.

Trạng thái email `SENT` của ứng dụng có thể chỉ phản ánh SES đã chấp nhận request gửi. Nó không bảo đảm thư đã đến inbox. Nếu chưa nhận thư, kiểm tra recipient thực tế, sandbox/verified identities, spam và SES delivery/bounce events nếu đã cấu hình theo dõi.

Dev giữ NAT Gateway và ALB tắt theo cấu hình tiết kiệm đã chọn. Điều đó không làm toàn bộ môi trường miễn phí: Fargate, public IPv4 của tasks/builder khi sử dụng, RDS/Redis/EC2, storage, requests và các dịch vụ khác vẫn có thể dùng credits theo chính sách account. Sau `stop`, các DB nodes vẫn chạy. Sau `runtime`, foundation và storage còn tồn tại. Sau `all`, kiểm tra cleanup hoàn tất và trang Billing để biết usage thực tế; số liệu Billing có thể cập nhật chậm.

CloudWatch Logs phục vụ xem log của các services hiện tại. SNS alerts và các phần monitoring/alerting nâng cao của Phase 5 chưa được triển khai trong phạm vi hai script này; không có thao tác bật/tắt SNS mới được hứa ở đây.

## 11. Ba lệnh dùng thường xuyên

Đang học và muốn bật/test:

```bash
bash scripts/aws-dev-up.sh
```

Tạm dừng xử lý, giữ nguyên DB đang chạy:

```bash
bash scripts/aws-dev-down.sh stop
```

Kết thúc buổi thực hành và muốn dọn toàn bộ AWS của dự án, chấp nhận xóa dữ liệu:

```bash
bash scripts/aws-dev-down.sh all
```

Lần sau dựng lại bằng `up --sender "$TASK_SES_SENDER"` hoặc nhập lại sender như mục 3. Không cần tạo project mới hoặc viết lại Terraform.

