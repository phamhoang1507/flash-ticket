# Flash-Ticket — hướng dẫn tiếp quản và bảo trì

Tài liệu này dành cho người vừa tham gia dự án: đã chạy được ứng dụng nhưng chưa biết nên đọc file nào, sửa ở đâu và kiểm tra thế nào. Mục tiêu là bạn có thể tự lần theo một request, thay đổi có kiểm soát, deploy và xử lý lỗi mà không phải hỏi lại người viết ban đầu.

Các chương được đối chiếu với source ở commit `c3c18e77c6029e0ee21e997b7231f3253792339e` trên branch `feat/aws-dev-deploy`. Đây là mốc source để giải thích; tài liệu được thêm bằng commit sau đó. Khi code thay đổi, hãy cập nhật chương tương ứng trong cùng thay đổi.

## 1. Đọc theo thứ tự nào?

| Thứ tự | Chương | Sau khi đọc, bạn cần làm được |
|---|---|---|
| 1 | [Repository và cách tư duy về hệ thống](01-repository-and-mental-model.md) | Chỉ đúng nơi lưu dữ liệu, phân biệt các process và kể lại vòng đời một booking |
| 2 | [Backend và dữ liệu](02-backend-and-data.md) | Giải thích chống over-selling, idempotency, Outbox, retry và email |
| 3 | [Frontend và authentication](03-frontend-and-auth.md) | Lần từ nút bấm đến API, hiểu PKCE/access token và cách UI giữ trạng thái |
| 4 | [Terraform và kiến trúc AWS](04-terraform-and-aws.md) | Đọc ba Terraform roots, hiểu hai AZ, network, IAM, secrets và giới hạn dev |
| 5 | [Docker và vận hành](05-docker-and-operations.md) | Biết image nào chạy ở đâu; bật, dừng, xóa và triển khai lại đúng phạm vi |
| 6 | [Chẩn đoán và phục hồi](06-troubleshooting-and-recovery.md) | Tìm đúng bằng chứng trước khi sửa; xử lý các lỗi thường gặp |
| 7 | [Thực hành bảo trì](07-maintenance-practice.md) | Tự làm một thay đổi từ branch, test, image tag đến kiểm tra và rollback |

Hãy đọc chương 1 trước, sau đó mở code song song với chương 2. Khi đã hiểu một booking đi qua các process thế nào, chuyển sang chương 4 và 5 để dành phần lớn thời gian cho DevOps. Chương 6 dùng như sổ tra cứu; chương 7 là phần tự kiểm tra năng lực tiếp quản.

Không cần học thuộc tên mọi file. Cần nắm được **đường đi từ hành vi → entry point → dữ liệu → tác động ra ngoài → cách kiểm tra**.

## 2. Phân biệt tài liệu học với runbook

Các chương onboarding giải thích **vì sao** code và hạ tầng được tổ chức như vậy. Các runbook dưới đây giữ hướng dẫn thao tác chi tiết hiện hành:

| Việc cần làm | Runbook chính |
|---|---|
| Cài từ clone mới, tạo local secrets, chạy Compose | [LOCAL-DEVELOPMENT.md](../LOCAL-DEVELOPMENT.md) |
| Bật/dừng/xóa AWS và hiểu chi phí còn lại | [AWS-DEV-POWER.md](../AWS-DEV-POWER.md) |
| Triển khai riêng từng stage, xem outputs và logs | [DEPLOY-DEV-QUICKSTART.md](../DEPLOY-DEV-QUICKSTART.md) |
| MySQL migrations, TLS và application users | [mysql-bootstrap.md](../mysql-bootstrap.md) |
| MongoDB AMI, users, SSH provisioning và kiểm tra | [mongodb-bootstrap.md](../mongodb-bootstrap.md) |

Không chạy lại tất cả lệnh cài đặt chỉ vì đang đọc onboarding. Khi ứng dụng đã hoạt động, hãy bắt đầu bằng việc đọc source và kiểm tra trạng thái. Lệnh tạo, sửa hoặc xóa AWS chỉ chạy khi bạn chủ động muốn thực hiện thao tác đó.

## 3. Bài học đầu tiên: một lần bấm “Đặt vé”

Giả sử người dùng đặt một vé:

1. React gửi `POST /bookings` kèm **access token**, `Idempotency-Key`, event ID, số lượng và email nhận vé.
2. Booking xác thực token và dùng subject trong token làm chủ sở hữu booking. Backend không lấy `user_id` từ form.
3. Trong **một MySQL transaction**, Booking trừ tồn kho có điều kiện, tạo booking `PENDING`, rồi ghi `BookingCreated` vào outbox. Commit xong mới trả kết quả.
4. Relay đang chạy nền nhìn thấy outbox đã commit, cập nhật Redis theo inventory version rồi publish SQS. Relay tự poll database; không đợi một request HTTP gọi tới.
5. Worker đang long poll SQS nhận message, lấy lease của booking, tạo PDF/QR, upload S3, cập nhật `ISSUED`, gọi SES và lưu `email_status=SENT` khi SES chấp nhận yêu cầu.
6. React gọi API lấy trạng thái. Khi vé sẵn sàng, API kiểm tra quyền sở hữu rồi cấp presigned URL để browser tải PDF từ S3.

Hai kết quả phải phân biệt:

- `status=ISSUED`: vé đã được tạo và thông tin vé đã lưu trong MySQL.
- `email_status=SENT`: SES đã chấp nhận yêu cầu gửi; chưa phải bằng chứng thư đã vào Inbox.

Vì vậy, người dùng có thể tải PDF trong lúc email vẫn `PENDING`. Khi đó cần kiểm tra nhánh Worker → SES, không kết luận toàn bộ booking thất bại.

## 4. Cách đọc một function để có thể tự sửa

Với mỗi function quan trọng, ghi lại sáu câu trả lời:

1. **Đầu vào từ đâu?** HTTP body, header, environment, database row hay SQS message?
2. **Điều kiện nào được kiểm tra?** Token, schema, quyền sở hữu, số vé, lease?
3. **Dữ liệu nào thay đổi?** Database table, Redis key, object S3?
4. **Ranh giới transaction ở đâu?** Điều gì rollback cùng nhau, điều gì nằm ngoài transaction?
5. **Nếu process chết ngay sau dòng này?** Request có thể retry an toàn không, có khả năng gửi trùng không?
6. **Làm sao chứng minh đã đúng?** Unit test, integration test, SQL query, HTTP response hay log cụ thể?

Áp dụng trước với `create_booking()` trong [Booking](../../services/booking/app/main.py). Sau đó làm lại với `publish()` của [Relay](../../services/relay/app/main.py) và `process_message()` của [Worker](../../services/worker/app/main.py).

## 5. Những gì hiện có và những gì chưa được chứng minh

Repository hiện có luồng local và AWS dev, authentication adapters, bốn application services, Outbox/SQS, Terraform và scripts điều phối thủ công. Cấu hình này phù hợp học và thử nghiệm.

**Chưa có** GitHub Actions CI/CD, SNS alerts, ECS auto scaling, một Terraform root `prod` hoàn chỉnh hoặc load test chứng minh chịu được 50.000 users. VPC có hai AZ nhưng số task dev mặc định là một cho mỗi service; data dev dùng node đơn. Không xem việc tạo đủ tên dịch vụ AWS là bằng chứng High Availability hay zero downtime.

Các cờ NAT/Multi-AZ/Spot là nền tảng để nâng cấp, không phải nút biến toàn bộ hệ thống thành production HA. MongoDB hiện là standalone và app connection chưa có TLS. Chương 4 mô tả chính xác phạm vi từng cờ và phần cần xây tiếp.

## 6. Phạm vi tác động của lệnh

| Nhóm thao tác | Ví dụ | Cần hiểu trước khi chạy |
|---|---|---|
| Đọc source/trạng thái | `git status`, đọc file, `deploy_dev.py status` | Status AWS vẫn cần credentials hợp lệ |
| Kiểm tra offline | Unit tests, syntax checks | Không chứng minh kết nối AWS hoặc khả năng chịu tải |
| Chạy local | Compose, bootstrap LocalStack | Sử dụng tài nguyên máy; xóa volumes làm mất local data |
| Tạo/sửa AWS | `aws-dev-up.sh`, Terraform apply, stage `start` | Có thể tiêu credit và thay đổi deployment |
| Dừng compute ứng dụng | `aws-dev-down.sh stop` | Chỉ đưa bốn ECS services về 0; data/storage vẫn tồn tại |
| Xóa AWS | `aws-dev-down.sh runtime` / `all` | Mất dữ liệu theo phạm vi; đọc plan và runbook |

Tài liệu này không yêu cầu đưa AWS lên lại chỉ để học code. Không cần rebuild Docker image cho thay đổi Markdown.

## 7. Hoàn thành onboarding khi nào?

Bạn đã tiếp quản được phần hiện tại khi có thể tự làm những việc sau:

- Tìm đúng function xử lý một thao tác UI và giải thích các query/side effects liên quan.
- Giải thích vì sao retry booking không được tạo thêm đơn, còn email vẫn có cửa sổ gửi trùng.
- Đọc plan và biết tài nguyên thuộc foundation hay runtime; biết state nào quản lý chúng.
- Chọn đúng `stop`, `runtime` hoặc `all` và kể được tài nguyên nào vẫn còn sau lệnh.
- Lần theo một UUID từ API → MySQL → outbox → SQS → Worker → S3/SES bằng bằng chứng.
- Tạo branch, sửa nhỏ, kiểm tra, commit, build tag rõ ràng, deploy và kiểm tra kết quả.

**Bắt đầu:** [Chương 1 — Repository và cách tư duy về hệ thống](01-repository-and-mental-model.md).
