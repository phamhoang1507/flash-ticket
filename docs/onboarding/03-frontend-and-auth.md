# 03. Frontend và Authentication

[← Backend và data](02-backend-and-data.md) · [Tiếp: Terraform và AWS →](04-terraform-and-aws.md)

Chương này giúp bạn lần từ một nút trên giao diện đến HTTP request, token và booking. Đọc cùng [hướng dẫn local](../LOCAL-DEVELOPMENT.md) để chạy thử hoặc [AWS quickstart](../DEPLOY-DEV-QUICKSTART.md) để triển khai. Frontend hiện là React một trang, cố ý giữ ít thành phần để phần học chính nằm ở backend và hạ tầng.

## 1. Mỗi file chịu trách nhiệm gì?

Các link dưới đây đi thẳng đến source. Đọc theo thứ tự `main.jsx` → `App.jsx` → `api.js` → `auth.js` trước khi đọc Docker.

| File | Vai trò và khi nào cần sửa |
|---|---|
| [index.html](../../frontend/index.html) | Khung HTML, ngôn ngữ, title và phần tử `root`. Sửa metadata tại đây; không chèn token hoặc AWS credentials vào HTML. |
| [main.jsx](../../frontend/src/main.jsx) | `createRoot` gắn `App` vào `root`, import CSS. Thường giữ nguyên. |
| [App.jsx](../../frontend/src/App.jsx) | State, form, polling, retry booking và render. Sửa khi đổi hành vi người dùng. |
| [api.js](../../frontend/src/api.js) | Ghép URL, headers, timeout, JSON và `ApiError`. Thêm quy tắc HTTP dùng chung tại đây. |
| [auth.js](../../frontend/src/auth.js) | Đọc token local hoặc giao tiếp Cognito bằng `oidc-client-ts`. Sửa khi đổi luồng đăng nhập. |
| [style.css](../../frontend/src/style.css) | Màu, layout, responsive. Đổi giao diện thường chỉ cần file này và JSX. |
| [vite.config.js](../../frontend/vite.config.js) | React plugin và proxy khi chạy Vite dev server. Proxy này không chạy trong bản build AWS. |
| [package.json](../../frontend/package.json) | Dependencies và các script `dev`, `build`, `preview`. |
| [package-lock.json](../../frontend/package-lock.json) | Ghi dependency tree cụ thể để `npm ci` cài lặp lại được. Không sửa tay hoặc xóa để né lỗi cài đặt. |
| [Dockerfile](../../frontend/Dockerfile) | Node build ra `dist`, rồi Nginx phục vụ bằng user `nginx`, port `8080`. |
| [nginx.conf](../../frontend/nginx.conf) | Static hosting, SPA fallback, security headers và proxy local vào Booking/Catalog. AWS S3 hosting không dùng file này. |

Dependencies frontend không phải nơi chứa business rule chống bán lố. Disable nút giúp tránh thao tác lặp trong một tab; MySQL transaction mới quyết định có đủ vé hay không.

## 2. State, effects và quyền sở hữu trong giao diện

`useState` giữ dữ liệu của lần render hiện tại. Trong `App`, `events/loading/eventsError` dành cho Catalog; `selected/quantity/email` dành cho form; `attempt` là yêu cầu chưa xác nhận kết quả; `booking` là đơn gần nhất lưu trong tab. `token` giữ access token đang dùng. `busy` đổi trạng thái nút, còn `busyRef` chặn hai lần click liên tiếp trước khi React kịp render lại.

Ba `useEffect` xử lý công việc bên ngoài render:

1. Đăng ký sự kiện token rồi gọi `initialAccessToken()`. Cleanup hủy đăng ký khi component bị tháo.
2. Tải `/events`, sau đó đặt timer cho lần tiếp theo. `revision` tăng khi bấm làm mới hoặc đặt vé thành công, khiến effect chạy lại.
3. Theo dõi booking cho đến khi vé `ISSUED` và email `SENT`, hoặc đạt giới hạn polling.

Biến `active` và `clearTimeout` ngăn response cũ tiếp tục cập nhật state sau khi effect đã bị thay thế. Khi sửa dependency array, phải xem cả cleanup; bỏ cleanup có thể tạo nhiều vòng polling song song.

`identityFromToken()` tạo chuỗi từ cặp `iss` và `sub`: issuer xác định bên phát hành, subject xác định user ở bên đó. Booking lưu dạng `{ owner, data }`; `ownBooking` chỉ hiện khi owner khớp identity đang đăng nhập. Pending request cũng kiểm tra owner trước khi retry. Vì vậy, đăng xuất rồi đăng nhập tài khoản khác không làm giao diện trình bày đơn của tài khoản trước như đơn của người mới.

Đây là bảo vệ ở giao diện. Browser có thể sửa state hoặc `sessionStorage`; backend vẫn phải kiểm tra chữ ký token, scope và điều kiện `user_id` trên từng truy vấn booking.

## 3. Pending request và Idempotency-Key

`submitBooking()` tạo một UUID bằng `crypto.randomUUID()` cho **một lần đặt vé có chủ đích**. Trước khi gọi mạng, nó ghi `{ key, owner, payload }` vào `sessionStorage` dưới key `flash-ticket-pending`. Payload gồm `event_id`, `quantity`, `email`.

Ví dụ request đã tới Booking và commit thành công, nhưng Wi-Fi đứt trước khi browser nhận response. Frontend chưa thể biết đơn đã tồn tại. Nút **Thử lại yêu cầu** gửi lại cùng key và cùng payload, để backend trả đơn cũ thay vì tiếp tục trừ inventory. Không tạo key mới cho thao tác retry và không sửa payload của pending request.

Chỉ khi response có booking `id` hợp lệ, frontend mới lưu `flash-ticket-last-booking` và xóa pending. Các lỗi `400/403/404/409/422` được coi là lỗi kết thúc của lần gửi đó. `401`, `429`, lỗi server, timeout hoặc lỗi mạng giữ pending để người dùng có thể đăng nhập lại hoặc retry. Với `409`, đọc `detail`: có thể hết vé hoặc key từng được dùng cho payload khác.

`api()` dùng timeout **15 giây**. Browser timeout không hủy transaction đã chạy trên server. HTTP `2xx` nhưng body không phải JSON, hoặc JSON là `null`/scalar, trở thành `ApiError(502)`. `App` còn kiểm tra danh sách events là array và booking response có `id`. Những kiểm tra này ngăn proxy trả HTML thành công giả khiến frontend xóa mất pending.

`sessionStorage` giữ dữ liệu qua refresh trong cùng tab, tách theo origin và tab, không phải lịch sử đơn hàng lâu dài. UI chỉ theo dõi đơn gần nhất. Đừng xóa pending khi kết quả giao dịch còn chưa rõ chỉ để làm mất thông báo; cần retry hoặc kiểm tra booking ở backend trước.

## 4. API và polling thực sự hoạt động thế nào?

| Route browser gọi | Token và ý nghĩa |
|---|---|
| `GET /events` | Public; đọc metadata và số vé đang được Redis projection cập nhật. |
| `POST /bookings` | Access token, scope `bookings:write`, header `Idempotency-Key`; trả booking `PENDING` với HTTP `202`. |
| `GET /bookings/{booking_id}` | Cùng scope; backend chỉ trả đơn của user hiện tại. ID phải là UUID. |
| `GET /bookings/{booking_id}/ticket` | Cùng scope; trả presigned URL khi PDF đã sẵn sàng. |

`api.js` thêm `Accept`, `Authorization: Bearer ...`, `Content-Type` khi có body và `Idempotency-Key` khi được truyền. Không rải các bản sao của `fetch()` trong component vì chúng dễ bỏ sót timeout hoặc xử lý lỗi.

Catalog được refresh sau mỗi lần hoàn tất request, cộng khoảng **15–17 giây** chờ, và bỏ qua việc gọi API khi tab bị ẩn. Số vé hiển thị có thể trễ; kết quả POST Booking mới là kết quả đặt vé.

Booking bắt đầu poll sau **1 giây**. Sau mỗi request, thời gian chờ là:

```text
min(10000, 2000 × 2^floor(polls / 5)) + random(0, 1000) milliseconds
```

Nghĩa là khoảng chờ tăng từ 2 lên 4, 8 rồi tối đa 10 giây, có thêm jitter tối đa 1 giây để các browser không cùng gọi vào một thời điểm. Tab ẩn chỉ kiểm tra lại sau 5 giây và không tăng bộ đếm request. Giới hạn là **60 lần gọi trong một vòng effect**, không phải đúng hai phút: còn tùy độ trễ mạng, thời gian chờ và thời gian tab bị ẩn. Token thay đổi hoặc tracked booking thay đổi có thể tạo vòng effect mới.

Polling dừng khi `ISSUED` và `SENT`, khi gặp `401/403/404`, hoặc hết lượt. Nút **Cập nhật trạng thái** vẫn gọi lại được. Tăng tần suất polling làm tăng tải API/MySQL; trước khi đổi, phải tính số browser đồng thời, không chỉ nhìn độ mượt của một tab.

## 5. Cognito: lần từ login đến access token

`AUTH_PROVIDER` là cấu hình backend; `VITE_AUTH_PROVIDER` là cấu hình frontend lúc build. `auth.js` dùng `jwt` mặc định cho local và `cognito` cho AWS.

Với Cognito, luồng Authorization Code + PKCE đi như sau:

1. `signIn()` gọi `UserManager.signinRedirect()`.
2. `oidc-client-ts` tạo state và PKCE verifier/challenge, lưu dữ liệu giao dịch đăng nhập trong `sessionStorage`, rồi chuyển browser đến Cognito `/oauth2/authorize`.
3. User đăng nhập trên Cognito. Public app client không có client secret.
4. Cognito chuyển về đúng `window.location.origin + /auth/callback`, mang theo authorization code và state.
5. `initialAccessToken()` gọi `signinRedirectCallback()`. Thư viện kiểm tra state và đổi code cùng verifier tại `/oauth2/token`.
6. Browser nhận token. Code dùng **access token** để gọi API, rồi bỏ code khỏi address bar bằng `history.replaceState`.

PKCE ràng buộc code với browser đã bắt đầu login; state giúp đối chiếu response với lần login đang chờ. `callbackPromise` tránh xử lý cùng callback lặp lại trong một phiên module. Không tự viết lại bước đổi code bằng `fetch()` hoặc đưa client secret vào React.

Access token dành cho API; ID token mô tả phiên đăng nhập theo OIDC. Dán ID token vào API không thay cho access token: backend yêu cầu `token_use=access`. Scope là quyền được cấp, mặc định gồm `openid email profile flash-ticket/bookings:write`; các booking GET cũng dùng quyền này trong implementation hiện tại.

`automaticSilentRenew` cho phép thư viện gia hạn phiên, thường dùng refresh token. Khi mở trang với user hết hạn còn refresh token, `initialAccessToken()` gọi `signinSilent()`. `onTokenChanged()` cập nhật React khi user được nạp, bị xóa hoặc access token hết hạn. Local JWT không có refresh flow; hết hạn thì tạo token local mới theo [local guide](../LOCAL-DEVELOPMENT.md).

Logout local xóa token local. Logout Cognito xóa user trong browser rồi chuyển tới Cognito `/logout` với `client_id` và `logout_uri` là origin cộng `/`. Callback và logout URLs phải khớp danh sách đã cấu hình ở Cognito, kể cả protocol, port và dấu `/` cuối.

## 6. Backend Adapter xác minh token ở đâu?

[shared/auth.py](../../shared/auth.py) là nơi cần đọc khi thay nhà cung cấp Auth. Frontend decode claims chỉ để hiển thị và kiểm tra hạn dùng; thao tác decode đó không chứng minh chữ ký hợp lệ.

`get_verifier()` chọn Adapter và cache instance. Booking/Catalog gọi verifier lúc startup để phát hiện cấu hình thiếu. Cả hai Adapter trả `Principal(subject, scopes)` nên route dùng chung `authenticate()` và `require_scope()`.

| Adapter | Các kiểm tra chính |
|---|---|
| `CustomJWTVerifier` | Đọc RSA public key từ `JWT_PUBLIC_KEY_PATH`, tối thiểu 2048 bit; chỉ cho `RS256`; kiểm tra issuer, audience, thời gian và các claims bắt buộc. Private key dùng để cấp token không nằm trong API image. |
| `CognitoVerifier` | Lấy public key từ JWKS của user pool, cache JWKS 300 giây, timeout tải key 3 giây; kiểm tra `RS256`, issuer, thời gian, `client_id` và `token_use=access`. Cognito access token dùng `client_id`; code không áp kiểm tra audience như ID token. |

Với Cognito, `principal_from_claims()` bỏ prefix `flash-ticket/`, nên scope `flash-ticket/bookings:write` thành `bookings:write` cho route. Local token do [local_token.py](../../scripts/local_token.py) cấp đã có scope ngắn này. `require_scope("events:write")` là quyền quản trị Catalog; public Cognito client hiện không được cấp quyền đó.

`authenticate()` trả `401` khi thiếu hoặc sai token; `require_scope()` trả `403` khi thiếu quyền; lỗi kết nối JWKS trả `503` và `Retry-After: 2`. Trên AWS, API Gateway còn có JWT authorizer trước backend. Chuyển backend sang Custom JWT mà giữ authorizer Cognito sẽ vẫn bị chặn ở Gateway; cần thiết kế lại issuer/JWKS của Gateway cùng với frontend và backend, không chỉ đổi một biến.

## 7. Local và AWS khác nhau ở đường đi, không ở component

| Cách phục vụ | Đường API và static files |
|---|---|
| Compose, `localhost:8080` | Nginx phục vụ `dist`; `/api/events` vào `catalog:8000`, `/api/bookings` vào `booking:8000`, bỏ prefix `/api`. Tên service là DNS trong Compose network. |
| Vite, thường `localhost:5173` | Dev proxy trong `vite.config.js` vào host `127.0.0.1:8002/8001`; biến shell `CATALOG_PORT`/`BOOKING_PORT` đổi các port này. |
| AWS | CloudFront đọc frontend S3 private bằng OAC. Browser gọi HTTP API URL trực tiếp; Gateway đi qua VPC Link/Cloud Map đến ECS private IP. |

AWS dùng `VITE_API_BASE_URL` là API URL ở `$default` stage, không tự thêm `/api`. HTTP API CORS phải cho phép frontend origin, `GET/POST/OPTIONS` và các headers API dùng. Lỗi CORS không sửa bằng cách mở S3 public hoặc cho ECS inbound từ Internet.

CloudFront phải trả `index.html` cho `/auth/callback`; [spa.js](../../infra/modules/web_auth/spa.js) thực hiện rewrite frontend route và giữ query callback. Banner đi behavior `/banners/*` riêng, PDF đi presigned S3 URL riêng. `downloadTicket()` dùng browser navigation, không fetch PDF qua React.

Các biến sau được Vite đưa vào JavaScript **lúc build**:

| Biến | Nội dung |
|---|---|
| `VITE_AUTH_PROVIDER` | `jwt` hoặc `cognito`. |
| `VITE_API_BASE_URL` | `/api` ở local; HTTP API URL ở AWS. |
| `VITE_COGNITO_AUTHORITY` | Issuer user pool, dạng `https://cognito-idp.us-east-1.amazonaws.com/` cộng pool ID. |
| `VITE_COGNITO_CLIENT_ID` | Public app client ID. |
| `VITE_COGNITO_DOMAIN` | Cognito Hosted UI URL có `https://`. |
| `VITE_OIDC_SCOPE` | Chuỗi scope phân cách bằng khoảng trắng. |

Các giá trị này công khai. Không đưa AWS secret key, database password, JWT private key hoặc ticket signing secret vào bất kỳ `VITE_*` nào. Đổi SSM/ECS environment không thay bundle đã build; đổi endpoint/pool/client cần rebuild, upload và xử lý CloudFront cache bằng deployment workflow.

## 8. Email đăng nhập và email nhận vé

Form bắt đầu với email trống, bắt buộc người mua nhập recipient. Đăng nhập bằng email A không tự điền recipient A. Email lưu trong booking là giá trị lúc đặt; UI hiển thị lại cả ở pending request và đơn gần nhất. Sửa input sau đó không sửa booking cũ.

Trong SES sandbox, sender và recipient đều cần được SES cho phép, thường bằng verified identity. Cognito xác nhận email không tạo SES verification. Lần test AWS đầu tiên nên nhập chính địa chỉ sender đã verified vào form. LocalStack chỉ giả lập SES, không gửi thư tới inbox thật.

`ISSUED` nghĩa là PDF sẵn sàng; `SENT` nghĩa là SES đã chấp nhận request gửi, không xác nhận thư đến inbox. Nếu tải được PDF mà chưa nhận thư, kiểm tra recipient thực tế, SES sandbox, spam và delivery/bounce nếu đã cấu hình. Không đổi `SENT` thành cam kết “đã đến hộp thư”.

## 9. Sửa và debug theo đúng lớp

Đổi màu hoặc copy: sửa `style.css`/JSX rồi rebuild frontend. Đổi giới hạn quantity: sửa input chỉ là bước đầu; phải sửa validation `BookingInput` và đánh giá tải/transaction ở [Booking](../../services/booking/app/main.py). Đổi route hoặc response field: cập nhật cả caller trong `App`, contract backend và ingress/proxy liên quan. Giữ nguyên quy tắc pending retry.

Khi đổi dependency, cập nhật `package.json` bằng npm để lockfile thay đổi đồng bộ; commit cả hai file. `npm ci` dùng lockfile hiện có và không sửa mismatch thay bạn. `npm run build` tạo `dist`; build Docker dùng context `frontend/`. Giữ non-root user, port `8080`, các đường ghi `/tmp` và cache immutable chỉ cho assets có hash. Lệnh Git/Docker đầy đủ nằm trong [local guide](../LOCAL-DEVELOPMENT.md); triển khai static AWS dùng [quickstart](../DEPLOY-DEV-QUICKSTART.md).

| Hiện tượng | Nơi kiểm tra đầu tiên |
|---|---|
| Login báo `redirect_mismatch` | Origin đang mở, callback `/auth/callback`, allowlist trong [web_auth](../../infra/modules/web_auth/main.tf). |
| Callback 403/404 hoặc hiện XML | CloudFront SPA function, đúng distribution và frontend `index.html`; đây chưa phải lỗi token. |
| `401` | Network request có access token không, `exp`, issuer/client ID và Auth provider backend/Gateway. Không chia sẻ raw token trong log công khai. |
| `403` | Token có `flash-ticket/bookings:write` không; app client, API route scope và `require_scope()`. |
| HTTP `422` ở booking GET | `booking_id` phải là UUID trả từ POST, không phải số thứ tự như `1`. |
| Catalog không tải được | Network URL, CORS/proxy, response là JSON array; sau đó xem Catalog/data layer. |
| Pending sau timeout | Dùng **Thử lại yêu cầu** với key cũ; không kết luận đã thất bại vì browser timeout. |
| UI cũ sau deploy | Bundle build với biến nào, upload `dist`, CloudFront invalidation; đổi ECS env không đổi frontend. |
| Có PDF, chưa có email | Recipient hiển thị trong booking, SES sandbox và trạng thái worker; tách lỗi phát hành vé khỏi lỗi delivery. |

Khi báo lỗi, ghi route, HTTP status, `detail`, booking ID và thời điểm. Phân biệt lỗi trong Browser Network, Gateway và log backend để sửa đúng file. Phần duy trì Terraform, IAM, subnet và teardown tiếp tục ở [chương 04](04-terraform-and-aws.md).
