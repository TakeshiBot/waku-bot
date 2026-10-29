# Waku API

Kho ảnh riêng chạy trên Cloudflare Workers + R2 + D1. Bot Telegram nhận ảnh hoặc link ảnh, lưu vào R2, trả link trực tiếp. Source nằm trong thư mục `api/` trên nhánh chính `v2` của repo Waku. API ảnh deploy độc lập lên Cloudflare, không chạy trong container bot; không cần VPS. Có thể thêm API vision để tự gắn tag, hoặc dùng hoàn toàn thủ công.

## Dùng bot

Mở DM của bot upload bằng tài khoản đã cấp quyền. Gửi một hoặc nhiều ảnh; mỗi ảnh được xử lý riêng. Gửi dạng **file ảnh** nếu muốn giữ nguyên chất lượng; ảnh gửi dạng Photo được lưu theo bản Telegram đã nén.

Có thể chọn cả đợt nhiều ảnh/file, kể cả khi Telegram tách thành nhiều album. Bot lưu từng update vào hàng đợi D1; toàn hệ thống giới hạn **2 upload đồng thời**, còn AI giới hạn **2 ảnh/API đang bật**, không phải 2 lượt cho mỗi Worker. Ví dụ bật 3 API thì xử lý tối đa 6 ảnh AI song song. Phản hồi mới trong cùng chat được giãn ít nhất khoảng 1,1 giây để tránh gửi dồn. Mỗi file được lưu độc lập, file lỗi không chặn cả đợt. Caption chỉ đi cùng file mà Telegram gắn caption đó; muốn áp dụng tag chung cho cả đợt, dùng trang web bên dưới.

## Trang quản lý trên web

Mở URL gốc của Worker, ví dụ `https://waku-api.your-account.workers.dev/`. Trang được phục vụ ngay trên Worker hiện có, không cần thêm hosting, domain hoặc dịch vụ khác.

Đăng nhập bằng `API_KEY` trong file local `api/.secrets.json` (không phải key AI hoặc token Telegram). Key không được ghi vào HTML, URL hoặc localStorage. Sau khi xác thực, trình duyệt dùng cookie HttpOnly/Secure, hiệu lực 8 giờ. Đăng xuất để xóa phiên trên trình duyệt.

**API Keys** ở sidebar tạo key dịch vụ để nối bot hoặc ứng dụng lấy ảnh. Key đầy đủ chỉ hiện lúc tạo; lưu lại trước khi đóng bảng. Có thể đặt tên, đổi tên, bật/tắt và xóa để thu hồi. D1 chỉ lưu SHA-256 của key, không lưu lại key gốc. Key dịch vụ chỉ được GET `/v1/images/random`, không đăng nhập quản trị, đọc cấu hình AI, upload hoặc xóa dữ liệu. URL `/media/UUID` vẫn là link chia sẻ ảnh để Telegram/Discord tải được file.

Gọi ảnh: `GET https://waku-api.takeshi.dev/v1/images/random?rating=safe&count=3&tags=maid` với header `Authorization: Bearer <key dịch vụ>`. Hỗ trợ `rating=safe|r18|mixed`, `count=1..10`, nhiều tag ngăn bằng dấu phẩy (khớp tất cả), `exclude` chứa ID cần tránh. Chỉ ảnh đủ tên, tag, phân loại và kiểu ảnh mới vào API random.

Trong bot Waku, điền `waku_api_url = "https://waku-api.takeshi.dev/v1"`, `waku_api_key` bằng key dịch vụ, bật `waku_api_enabled`. `/config → Service` có công tắc riêng ManyACG/Waku API, mỗi công tắc nằm trên hàng URL/Key tương ứng. Bật cả hai để chọn nguồn ngẫu nhiên, bật nguồn nào chỉ dùng nguồn đó; tắt cả hai không gọi nguồn dự phòng ngầm. Lệnh ảnh báo chưa cấu hình dịch vụ; tool AI trả trạng thái cho model tự diễn đạt. Telegram và Discord cùng dùng bộ chọn nguồn, bộ lọc Safe/R18, metadata caption và chống lặp hiện có.

- Chọn/kéo thả nhiều ảnh, hoặc dán nhiều URL ảnh trực tiếp (mỗi dòng một link).
- Chọn tag chung, phân loại và bật/tắt AI cho đợt upload; mặc định dùng AI. Các lựa chọn này được chốt khi bấm bắt đầu.
- Trình duyệt tải tối đa **2 file mỗi lượt**. Có tiến độ từng file, tạm dừng sau các file đang chạy, bỏ file chờ và thử lại mục lỗi. Khi máy chủ bận, file sẽ chờ rồi thử lại.
- Giữ tab mở trong lúc upload. Sau khi ảnh được lưu, AI chạy nền và tiếp tục dù đóng trang; các file vẫn còn chờ ở trình duyệt chưa được gửi lên máy chủ.
- Thư viện có tìm theo tên/tag, lọc An toàn/R18/chưa phân loại, phân trang, sửa tên/tag/nguồn, đổi phân loại, chạy lại AI, sao chép link và xóa ảnh.
- Các mục Tất cả / An toàn / R18 / Chờ phân loại nằm dưới Kho ảnh ở sidebar, kèm tổng số ảnh của từng mục. Số đếm dùng cùng điều kiện với danh sách, kể cả ảnh đang chạy lại AI, và được gộp trong request trạng thái sẵn có. Mỗi lần mở mục hoặc bấm Xem thêm tải 5 hàng: 30 ảnh ở bố cục 6 cột, 15 ảnh ở bố cục 3 cột, 10 ảnh ở bố cục 2 cột. Nút biểu tượng upload cạnh tiêu đề thư viện mở phần tải ảnh trên điện thoại.
- Ảnh R18 được làm mờ trong thư viện, có tùy chọn hiện ảnh; mở chi tiết để xem và chỉnh sửa.
- Web và bot Telegram dùng chung dữ liệu, không có hai kho ảnh riêng.

Trên PC, lưới ảnh hiển thị 6 cột; màn hình nhỏ tự giảm cột. Mỗi ảnh có checkbox, **Chọn tất cả** lấy toàn bộ kết quả trong tab/bộ lọc hiện tại, kể cả trang chưa tải. **Bỏ chọn** xóa lựa chọn; **Xóa** hỏi lại tổng số ảnh trước khi xóa hàng loạt.

Nút **Sửa** cạnh Xóa mở popup sửa hàng loạt **Phân loại** và **Kiểu ảnh**. Mỗi mục mặc định **Giữ nguyên**, nên có thể chỉ sửa kiểu ảnh bị chọn nhầm lúc upload. Chọn giá trị rồi bấm Lưu thay đổi. Thao tác áp dụng cho các ảnh đã chọn, kể cả trang chưa tải, gửi tối đa 100 ID/request; giữ tên, tag và nguồn. Có thể thử lại các ảnh còn lỗi nếu một đợt chưa hoàn tất.

**Lọc** nằm cùng hàng với Chọn tất cả. Trên máy tính rê chuột vào để mở, giữ bảng khi di chuyển bên trong và thu lại khi rời ra; trên điện thoại bấm để mở/đóng. Có thể dùng bàn phím và Escape. Logo Waku được dùng ở sidebar, trang đăng nhập và biểu tượng tab.

Nút **Hiện R18 / Ẩn R18** nằm ngay cạnh Lọc, thay cho checkbox ở tiêu đề thư viện. Khung lọc cố định 252 × 336 px, giãn dòng gọn và không cuộn; tag chia thành trang 5 mục, có ô tìm kiếm. Chuyển trang tag không gọi API thêm và giữ các tag đã tích chọn. Khi thiếu chỗ ở dưới, bảng lọc mở lên trên.

**API & Model** nằm trên nút Đăng xuất ở sidebar (biểu tượng bánh răng trên điện thoại). Thêm/sửa/xóa kết nối bằng tên, Base URL OpenAI-compatible và key; mỗi API lưu được nhiều Model ID. Checkbox bật/tắt từng API độc lập; bấm tên API để xem model mà không đổi trạng thái bật. Mỗi API có một model vision được chọn bằng radio và được ghi nhớ riêng, kể cả khi tắt. Có thể bật nhiều API để chia hàng đợi song song; chọn model của API đang tắt không tự bật API đó. Thêm tối đa 30 API và 100 model/API. Cấu hình hiện có được nhập thành **API Waku** lần đầu mở mục này.

Bảng API/Model giữ chiều cao cố định theo màn hình; danh sách model cuộn khi dài hơn 4 dòng. Bấm nền ngoài bảng hoặc Escape để đóng. Trên điện thoại, nội dung bên trong có thể cuộn để vẫn dùng được các form ở màn hình thấp.

Lựa chọn được lưu trong D1 và dùng chung cho web/bot upload Telegram, không cần deploy. Lượt AI đang chạy tiếp tục với cấu hình đã lấy; các lượt kế tiếp đọc lựa chọn mới. Xóa model đang dùng sẽ chọn model còn lại của API nếu có; xóa/tắt một API không tắt các API khác. Nếu không còn API có model đang bật, upload thủ công vẫn hoạt động và AI chờ đến khi có kết nối. Migration từ cấu hình cũ chỉ bật API đã được chọn trước đó, giữ nguyên các API khác ở trạng thái tắt.

Hàng đợi đặt chỗ API và ảnh trong cùng transaction D1 để tránh một ảnh chạy đồng thời ở nhiều API. Mỗi API có 2 slot riêng, chọn slot ít dùng trước; không còn trần 2 lượt AI cho toàn hệ thống. Mỗi ảnh chạy trong invocation riêng, chia fan-out thành nhóm để giữ giới hạn Worker/D1. Nếu ACK gọi Worker bị thất lạc, giữ lease thay vì lập tức gửi lại; Cron khôi phục khi lease hết hạn. Kết quả chỉ được ghi nếu lượt chạy vẫn sở hữu lease và metadata chưa bị sửa tay.

Ảnh lỗi chuyển sang failed ngay, không tự gọi lại hoặc chuyển sang API khác. API chỉ tạm chờ khi bị 429 (60 giây) hoặc lỗi cấu hình 400/401/403/404 (5 phút); lỗi output/timeout của một ảnh không làm đứng các ảnh còn lại. Sửa kết nối/model hoặc tắt/bật API xóa khoảng chờ; phản hồi từ cấu hình cũ không ghi đè trạng thái cấu hình mới.

Key nhà cung cấp được mã hóa AES-GCM trước khi lưu D1, không trả lại qua API và không lưu ở localStorage. Khi sửa API, để trống key để giữ key cũ. Khóa mã hóa được dẫn xuất từ `API_KEY` quản trị: nếu thay `API_KEY`, cần nhập lại các key nhà cung cấp đã lưu. Sau khi quản lý API trên web, danh sách D1 là nguồn cấu hình chính; deploy lại secret AI không ghi đè lựa chọn này.

Khi bắt đầu upload, web chuyển sang **Chờ phân loại** và hiện trước các file đang chờ/gửi, sau đó thay bằng ảnh đã lưu. Chỉ vào An toàn/R18 khi ảnh có đủ **tên không trống, ít nhất một tag, phân loại safe/r18, kiểu anime/real** và không còn lượt AI đang chờ/chạy. Thiếu bất kỳ mục nào thì vẫn ở Chờ phân loại, kể cả đã chọn sẵn safe/r18. Link nguồn không bắt buộc. Nhập tay đủ bốn mục cũng được chấp nhận. Danh sách, số đếm tab và trường `classified` trong API dùng cùng điều kiện. Có thể mở ảnh thiếu thông tin để chạy AI hoặc chọn nhiều ảnh ở tab này rồi bấm **AI gắn tag**.

Thanh upload đếm **ảnh được API xác nhận đã lưu / tổng ảnh của đợt**, không cộng file lỗi vào số đã lưu. Thanh AI phía trên danh sách dùng bộ đếm D1 riêng theo đợt: đang chờ, đang chạy, thành công, lỗi, bỏ qua. Đây là hai tiến trình khác nhau; upload xong không đồng nghĩa AI đã hoàn tất. ID đợt AI được giữ trong sessionStorage để tiếp tục theo dõi sau khi tải lại trang; API key không được lưu ở đó.

Thanh AI có **Tạm dừng / Tiếp tục**, **Huỷ**, **Chạy lại lỗi**. Chạy lại lỗi chỉ xuất hiện khi không còn ảnh chờ/chạy trong đợt hoặc sau khi Huỷ; API cũng từ chối chạy lại khi đợt chưa kết thúc. Khi đang xem một đợt, nút tác động vào đợt đó; nếu không theo dõi đợt riêng thì tác động toàn hàng đợi. Tạm dừng được lưu ở D1, không mất khi tải lại trang; ảnh đang gọi API có thể hoàn tất nhưng không nhận ảnh tiếp theo. Huỷ không xoá ảnh/tag đã lưu, vô hiệu hoá kết quả đến muộn. Chạy lại chỉ lấy ảnh lỗi/đã huỷ, không chạy lại ảnh thành công. Tạm dừng toàn hàng đợi còn giữ cả ảnh mới tải lên, đến khi bấm Tiếp tục.

Thanh tiến trình chỉ hiện khi còn ảnh chờ/chạy hoặc đang chuẩn bị/xếp hàng AI. Đợt tạm dừng còn ảnh chưa xử lý vẫn giữ nút Tiếp tục/Huỷ. Khi hoàn tất hoặc huỷ, thanh tự ẩn; nếu đợt vừa chạy có lỗi/ảnh bị huỷ thì hiện thông báo gọn cùng nút Chạy lại lỗi, giữ đúng ID đợt đó. Thông báo này không được khôi phục khi tải lại trang. ID đợt đã kết thúc được xoá khỏi sessionStorage; mở web lúc rảnh không hiện lịch sử AI cũ. Khi theo dõi hàng đợi chung, số thành công/lỗi chỉ tính từ lúc quan sát lượt đang hoạt động, không lấy tổng cả kho. Thanh upload cũng chỉ hiện khi thực sự đang chuẩn bị/gửi file.

Mỗi ảnh có timeout **10 giây**, gồm đọc ảnh từ R2, gọi API và đọc nội dung trả về. Ảnh được gửi trực tiếp dạng data URI để hỗ trợ gateway không nhận URL ảnh từ xa, không phụ thuộc gateway tải được CDN hay không. Mỗi ảnh chỉ gọi API một lần: lỗi/timeout chuyển sang failed, không tự thử lại. Worker mất phản hồi được Cron đánh dấu lỗi sau khi lease 20 giây hết hạn, không phát lại tác vụ. Chỉ bấm Chạy lại lỗi mới tạo lượt mới. Không đặt timeout cứng cho toàn đợt vì đợt lớn cần được xếp hàng tuần tự.

Hàng đợi D1 có khóa lease theo từng slot, tiếp tục xử lý theo chuỗi ngắn để không vượt giới hạn gọi Worker lồng nhau. Cron mỗi phút khôi phục các tác vụ bị gián đoạn; khi đang mở web, trang cũng đánh thức hàng đợi. Không cam kết tốc độ cố định cho đợt lớn vì còn phụ thuộc Telegram, provider AI và hạn mức Cloudflare.

Caption ví dụ:

```text
Mèo ngủ #cat #cute #safe https://example.com/source
```

Hoặc gửi link ảnh trực tiếp:

```text
https://cdn.example.com/photo.webp #cat #safe
```

- `#safe` / `#sfw`: ảnh an toàn; `#r18` / `#nsfw`: ảnh R18.
- Khi cấu hình AI vision, ảnh được lưu ngay rồi AI bổ sung tag, tên ảnh còn trống và phân loại. Phản hồi Telegram được edit ngay trên tin nhắn cũ khi có kết quả.
- Tên, phân loại và tag bạn nhập được giữ lại; AI bổ sung các tag còn thiếu. Tên nhân vật chỉ được thêm khi model nhận diện đủ chắc chắn, vẫn có thể nhận sai.
- AI không chắc phân loại: giữ `unknown`, chưa vào API random. Bấm **An toàn** hoặc **R18** để sửa.
- Thêm `#manual` vào caption để bỏ qua AI cho ảnh đó, chỉ dùng dữ liệu bạn nhập. Không có API vision thì bot tự dùng chế độ thủ công.
- Nút **AI gắn tag** chạy lại phân tích, dùng được cả với ảnh cũ hoặc khi lần trước lỗi.
- Nút **Sửa tag** mở tin nhắn để bạn reply `#tag1 #tag2`; hoặc reply thẳng ảnh/link đã lưu bằng `/tags #tag1 #tag2`. Đây là thay toàn bộ tag. Gửi `-` hoặc `/tags -` để xóa hết tag. Sửa tay trong lúc AI chạy sẽ được ưu tiên.
- Nút **Xóa ảnh** xóa metadata và ảnh gốc trong R2.
- `/stats` xem số ảnh, dung lượng và ảnh chờ phân loại.
- Chỉ nhận trong DM của người thuộc `OWNER_IDS`; người khác không upload hoặc sửa ảnh được.
- Giới hạn mặc định **10 MiB/ảnh**; JPEG, PNG, WebP, GIF, AVIF. Link phải là file ảnh công khai, không phải trang gallery cần đăng nhập/JavaScript. Máy chủ nguồn có thể chặn tải ảnh.
- Chống trùng theo SHA-256 của nội dung thật. Upload lại không làm mất metadata của ảnh cũ; dùng PATCH để sửa.

## Deploy

Cần Node.js 22 trở lên và account Cloudflare đã kích hoạt R2. Script chỉ tạo Worker `waku-api`, bucket `waku-api-images`, database `waku-api`; không đổi subscription hoặc bật gói trả phí. Free tier có hạn mức, không có nghĩa mọi mức sử dụng đều miễn phí.

Tạo Cloudflare API token giới hạn đúng account, quyền **Workers Scripts: Edit**, **D1: Edit**, **Workers R2 Storage: Edit**, **Account Settings: Read**. Ghi file credentials ở ngoài Git:

```text
token bot TELEGRAM_BOT_TOKEN
admin id YOUR_TELEGRAM_ID
token cloudflare CLOUDFLARE_API_TOKEN
Account ID YOUR_CLOUDFLARE_ACCOUNT_ID
```

Các nhãn `cloudflare_token = ...`, `account_id = ...` cũng được hỗ trợ. Có thể dùng biến môi trường `TELEGRAM_BOT_TOKEN`, `OWNER_IDS` (nhiều ID cách dấu phẩy), `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` thay cho file.

```powershell
cd D:\project2026\waku-bot\api
npm.cmd ci
npm.cmd run check
npm.cmd test
npm.cmd run deploy -- --credentials D:\project2026\waku-bot\api.txt
```

Linux/macOS dùng `npm` thay `npm.cmd`. Chạy lại deploy để cập nhật source; tài nguyên và key được giữ nguyên. Script áp dụng migrations, deploy Worker, cài secrets rồi đăng ký webhook cho **bot upload đã cấu hình**. Không dùng token bot Waku đang chạy cho uploader này.

File tự sinh, không commit:

- `wrangler.local.json`: Account ID, database ID, danh sách người upload, URL triển khai.
- `.secrets.json`: API key, webhook secret và cấu hình AI đã cài (nếu có); lưu an toàn để kết nối Waku sau này. Không gửi nội dung lên chat.
- `api.txt`, `.dev.vars*`, `.wrangler/`, `node_modules/` cũng được ignore.

Đổi owner hoặc biến cấu hình trong `wrangler.jsonc`/credentials rồi chạy deploy. URL workers.dev được tạo từ account. Custom domain có thể cấu hình sau trong Cloudflare.

Chỉ đăng ký lại webhook, không build:

```text
npm run webhook -- --credentials /path/to/api.txt
```

Chạy local: copy `.dev.vars.example` thành `.dev.vars`, điền giá trị, `npm run db:local`, `npm run dev`. Telegram webhook cần URL HTTPS công khai nên phần đó dùng bản đã deploy hoặc tunnel riêng.

## Tự gắn tag bằng AI

Dùng provider **OpenAI-compatible Chat Completions có vision**, điền thêm vào file credentials (hoặc biến môi trường cùng tên viết HOA):

```text
ai_base_url = https://your-provider.example.com/v1
ai_api_key = YOUR_VISION_KEY
ai_model = YOUR_VISION_MODEL
```

Chạy lại deploy. Có thể dùng lại URL/key/model vision trong `settings.toml` của Waku; prefix provider trong Waku không thuộc model ID gửi đến API. Ví dụ `9router/ag/example-model` có model ID là `ag/example-model`, provider lấy từ `[agent_providers.9router]`. Cấu hình đã lưu trong `.secrets.json` được tái sử dụng ở các lần deploy tiếp theo; thay đổi Waku không tự đồng bộ sang đây.

Model nhận dữ liệu ảnh đã lưu và metadata thủ công. Tag tự sinh dùng tiếng Anh, chữ thường và dấu `_` cho dễ tìm (`cat_ears`, `maid`, `blue_hair`, `cosplay`…). Một kết quả AI thành công phải có đủ tên, tag, phân loại `safe`/`r18` và kiểu `anime`/`real`. Phân loại dựa trên nội dung model nhìn thấy; backend không tự gán theo tag hoặc đổi kết luận của model thành `unknown` bằng ngưỡng confidence. Nếu model không thể xác định, tác vụ báo `classification_unresolved`; thiếu trường bắt buộc báo `incomplete_response`, không lưu kết quả AI dở dang hoặc tính là thành công. Ảnh và metadata cũ vẫn giữ nguyên, không lọt vào tab đã phân loại/random khi còn thiếu thông tin. Giao diện và API vẫn cho bạn sửa thủ công.

Ảnh luôn lưu trước; lỗi AI không làm mất ảnh hay báo upload thất bại. Tác vụ phân tích lưu trong D1. Mọi lỗi AI đều kết thúc lượt xử lý, không tự thử lại; cần sửa cấu hình nếu có lỗi key/model rồi bấm Chạy lại lỗi. Upload trùng ảnh đã có tác vụ không tự gọi AI thêm lần nữa. Nhấn **AI gắn tag** sẽ chủ động gọi lại.

API phân biệt model từ chối nội dung (`ai_error=provider_content_filtered`) với lỗi định dạng, timeout hay kết nối. Nhận diện được cả OpenAI `finish_reason=content_filter` và Gemini `promptFeedback.blockReason` trong envelope của gateway. Web hiện nhãn **AI từ chối**; mở chi tiết ảnh để xem giải thích. Không tự đổi phân loại sang An toàn/R18 khi model chưa phân tích được và không tự thử model khác. Mã lỗi trả qua API không chứa nội dung phản hồi hay thông tin bí mật của nhà cung cấp.

API upload trả ảnh ngay, có thêm `ai_status`: `pending`, `running`, `done`, `failed`, `skipped` hoặc `off` (ảnh cũ chưa có tác vụ). Khi cần chờ tag, đọc lại `/v1/images/ID`. Thêm `manual=true` vào multipart, hoặc `"manual":true` vào JSON upload để bỏ qua AI. Phí phân tích tính theo provider AI bạn chọn, tách khỏi hạn mức miễn phí của Cloudflare.

## REST API

Các đường dẫn `/v1/*` cần header `Authorization: Bearer API_KEY`. Lấy key từ `.secrets.json`. Không truyền key qua query URL. Ví dụ dưới dùng biến `$WAKU_API_KEY` đã được đặt trên shell.

| Method | Đường dẫn | Chức năng |
| --- | --- | --- |
| GET | `/health` | Kiểm tra Worker đang phản hồi |
| POST/GET/DELETE | `/v1/session` | Đăng nhập, kiểm tra hoặc xóa cookie phiên web |
| GET | `/v1/stats` | Dung lượng, số ảnh và tác vụ đang chờ |
| GET | `/v1/state?batch=ID` | Gộp thống kê, phiên bản thư viện và tiến trình AI trong một request |
| GET / POST | `/v1/keys` | Quản trị: danh sách key (không có key gốc) / tạo key dịch vụ và trả key một lần |
| PATCH / DELETE | `/v1/keys/ID` | Quản trị: đổi tên, bật/tắt / thu hồi key dịch vụ |
| GET | `/v1/ai/settings` | Danh sách API/model và lựa chọn đang dùng, không trả key |
| POST | `/v1/ai/providers` | Lưu API: `name`, `base_url`, `api_key` |
| PATCH/DELETE | `/v1/ai/providers/ID` | Sửa/xóa API; key trống giữ nguyên |
| POST | `/v1/ai/providers/ID/models` | Thêm `{"model":"model-id"}` |
| PATCH/DELETE | `/v1/ai/models/ID` | Sửa/xóa model |
| PUT | `/v1/ai/selection` | Đổi riêng một API: `provider_id`, `enabled` (tùy chọn), `model_id` (tùy chọn); không tắt API khác. `provider_id:null` tắt tất cả |
| GET | `/v1/filters?q=cat` | Các tag hiện có và số ảnh, tối đa 1.000 tag; tìm tiếp bằng `q` |
| POST | `/v1/images/duplicates` | Kiểm tra trước khi upload: `{"hashes":["SHA256"],"urls":["https://…"]}`, tối đa 100 mục/lượt |
| POST | `/v1/images` | Upload multipart `file`, hoặc JSON chứa `url` |
| GET | `/v1/images?limit=20&before=CURSOR&q=maid&rating=safe` | Danh sách/tìm tên và tag/lọc phân loại, tối đa 100/trang; `next` là cursor trang kế |
| GET | `/v1/images/random?count=10&rating=safe&tags=cat,cute` | Lấy tối đa 10 ảnh, các tag kết hợp AND |
| GET | `/v1/images/ID` | Xem metadata |
| PATCH | `/v1/images/ID` | Sửa các trường `title`, `tags`, `rating`, `source_url`, `image_type` |
| PATCH | `/v1/images/bulk` | Sửa phân loại/kiểu ảnh hàng loạt: `ids` (1–100), `rating` và/hoặc `image_type`; trả `updated`, `ids`, `missing` |
| DELETE | `/v1/images/ID` | Xóa ảnh |
| POST | `/v1/images/ID/analyze` | Đưa ảnh vào hàng đợi AI để phân tích lại |
| POST | `/v1/analysis-batches` | Tạo một đợt AI, trả `id` |
| POST | `/v1/analysis-batches/ID` | Xếp hàng AI cho `{"ids":[...]}` (tối đa 100 ID/lượt) |
| GET | `/v1/analysis-batches/ID` | Tiến trình thật của đợt AI |
| POST | `/v1/analysis/control` | `{"action":"pause|resume|cancel|retry","batch_id":"ID tùy chọn"}`; bỏ batch_id để áp dụng toàn hàng đợi |
| GET/HEAD | `/media/ID` | File ảnh gốc, không cần API key |

`rating` của random: `safe` (mặc định), `r18`, `mixed`. Ảnh `unknown` không xuất hiện trong random. Kết quả ít hơn `count` nếu kho không đủ ảnh phù hợp; không lặp ID trong cùng lượt. Truyền `exclude=ID1,ID2,...` để tránh lặp giữa các lượt; tổng số tag và ID loại trừ tối đa 96. Bot gọi API cần lưu lịch sử ID đã gửi.

Một POST upload chỉ nhận một file/URL. Muốn upload nhiều ảnh qua API, gọi riêng từng ảnh với tối đa 2 request đồng thời; HTTP 429 có `Retry-After` khi slot upload đang bận. Web đã tự xử lý hàng đợi và thử lại, không cần gộp cả đợt vào một request lớn.

Upload có thể truyền thêm `batch_id` để gắn tác vụ AI vào đợt đã tạo. Danh sách ảnh hỗ trợ `view=pending` (chưa phân loại hoặc AI đang chờ/chạy), hoặc `view=classified` kết hợp `rating=safe|r18` để chỉ lấy ảnh đã xử lý xong.

Kiểu ảnh `image_type`: `anime` (vẽ/anime) hoặc `real` (người thật/ảnh chụp). Có thể chọn lúc upload hoặc sửa chi tiết; AI chỉ điền khi chưa có lựa chọn. Ảnh cũ có tag nhận diện rõ được chuyển kiểu tương ứng khi migrate, còn lại để chưa xác định. Lọc danh sách bằng `types=anime,real` và `tags=maid,cat_ears`; nhiều giá trị trong từng mục kết hợp OR, các mục kết hợp AND. Lọc tag của random vẫn dùng AND như trước.

Web tính SHA-256 tại trình duyệt rồi kiểm tra theo lô trước upload; ảnh trùng nội dung kể cả đổi tên và ảnh lặp trong hàng đợi đều được bỏ qua. File gốc được ghi `original_name`; link import đã gặp cũng được ghi nhớ. Link mới chưa từng gặp phải tải về mới xác định được nội dung trùng. Máy chủ vẫn kiểm tra hash lần cuối để xử lý upload đồng thời.

### Ưu tiên ảnh gốc từ link

Web, REST API và bot upload Telegram dùng chung bộ tải ảnh, tự ưu tiên bản gốc của **link ảnh trực tiếp** từ:

- X/Twitter (`pbs.twimg.com`): `name=orig`, hỗ trợ cả URL `.jpg:small` cũ và avatar có hậu tố kích thước; thử JPG/PNG khi cần.
- Discord: chuyển media proxy về CDN gốc, bỏ tham số thu nhỏ và giữ nguyên `ex`, `is`, `hm` xác thực.
- Pinterest (`i.pinimg.com`): chuyển đường dẫn kích thước sang `originals`, có thử định dạng thay thế.
- Pixiv (`i.pximg.net`): chuyển thumbnail/master sang `img-original`, kèm Referer và thử JPG/PNG.
- Imgur: bỏ hậu tố thumbnail đúng mẫu ID; không cắt nhầm ký tự cuối ID ảnh gốc.
- Reddit: chuyển `preview.redd.it` sang `i.redd.it`; không đoán nguồn từ external-preview.
- Unsplash, Pexels, Shopify CDN và WordPress: gỡ các tham số/đường dẫn thu nhỏ đã biết; giữ thông tin như `ixid`, `v` hoặc tham số xác thực.
- Wikimedia: chuyển thumbnail raster về file gốc; giữ thumbnail PNG của SVG/PDF vì importer không nhận các file gốc đó.

Nguồn không nhận diện hoặc URL có chữ ký không rõ được giữ nguyên, không xóa query tùy tiện. Nếu bản gốc lỗi/không tồn tại/quá giới hạn dung lượng, thử lại URL người dùng gửi. Web ghi rõ khi chỉ lưu được bản từ link; không nhận bản fallback là ảnh gốc. Mỗi ảnh tối đa 4 ứng viên, chỉ gọi ứng viên kế tiếp khi cần, tổng thời gian tải tối đa khoảng 25 giây. Giới hạn dung lượng ảnh vẫn áp dụng cho từng bản tải. Không upscale và không mở khóa ảnh cần đăng nhập.

Kiểm tra trùng chuẩn hóa link ngay trên máy chủ, không gọi CDN lúc preflight. Các kích thước/định dạng của cùng ảnh được nhận diện cùng nguồn; chỉ ghi nhận danh tính bản gốc khi đã tải thành công bản đó. Những link thumbnail lưu trước bản nâng cấp này không ngăn việc lấy lại bản gốc. API upload URL trả thêm `import`: `source`, `preferred_url`, `fetched_url`, `used_original`, `fallback_reason`.

Khi nhập ảnh bằng URL, `source_url` luôn lấy URL thực tế tải thành công: bản gốc khi nâng cấp được, bản từ link khi phải fallback. Quy tắc dùng chung cho web/API/bot upload Telegram; upload file vẫn giữ nguồn nhập thủ công. Import lại ảnh trùng nội dung cũng cập nhật riêng link nguồn, giữ nguyên tên/tag/phân loại và tôn trọng chỉnh sửa thủ công đang diễn ra đồng thời.

Tham khảo quy tắc nguồn: [Discord CDN](https://docs.discord.com/developers/reference#signed-attachment-cdn-urls), [Unsplash image URLs](https://unsplash.com/documentation#dynamically-resizable-images), [Pexels photo resource](https://www.pexels.com/api/documentation/#photos), [Shopify image URL](https://shopify.dev/docs/api/liquid/filters/image_url), [WordPress image CDN](https://developer.wordpress.com/docs/developer-tools/site-accelerator-api/). Các mẫu X/Pixiv/Pinterest cũng được đối chiếu với URL và cơ chế tải của [gallery-dl](https://github.com/mikf/gallery-dl/tree/master/gallery_dl/extractor).

Web rảnh chỉ kiểm tra một request mỗi phút; khi có tác vụ chuyển sang mỗi 8 giây, gộp tiến trình và thống kê. Tab trình duyệt bị ẩn ngừng polling. Chỉ tải lại danh sách khi phiên bản thư viện thay đổi; giữ lại các phần tử ảnh để tránh nháy. Hàng đợi chạy độc lập qua continuation/Cron, không phụ thuộc web liên tục gọi drain.

Upload:

```bash
curl "$WAKU_API_URL/v1/images" -H "Authorization: Bearer $WAKU_API_KEY" \
  -F 'file=@cat.png' -F 'title=Mèo ngủ' -F 'tags=cat,cute' -F 'rating=safe'

curl "$WAKU_API_URL/v1/images" -H "Authorization: Bearer $WAKU_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://cdn.example.com/photo.webp","tags":["cat"],"rating":"safe"}'

curl "$WAKU_API_URL/v1/images/random?count=10&tags=cat&rating=safe" \
  -H "Authorization: Bearer $WAKU_API_KEY"
```

Response upload: `{ "image": { "id", "url", "title", "tags", "rating", ... }, "duplicate": false }`.
Response random: `{ "images": [...], "count": 10 }`.
Lỗi có HTTP status và `{ "error": { "code", "message" } }`.

Bucket R2 không public. Link `/media/ID` là **link chia sẻ công khai**: ai có link đều xem được, kể cả ảnh chưa phân loại hoặc R18. Xóa sẽ chặn các lần đọc mới tại Worker; bản tải hoặc cache cũ có thể tồn tại đến 1 giờ. API key bảo vệ danh sách, upload và chỉnh sửa, không làm link ảnh thành riêng tư.

## Độ bền và giới hạn

Webhook chỉ ACK sau khi đã ghi tác vụ vào D1; Cron mỗi phút khôi phục tác vụ nếu Worker bị gián đoạn. Upload lỗi tạm thời được thử lại tối đa 3 lần, có backoff cho Telegram 429. Gửi phản hồi có dấu mốc lưu trước: nếu mất ACK sau khi gửi, bot không spam gửi lại; ảnh đã lưu vẫn có trong API. Ảnh không được báo lưu thành công trước khi R2 và metadata D1 hoàn tất.

Xóa ảnh được ghi vào outbox trước, Cron thử lại xóa R2 nếu cần. Bản sao R2 tạm do upload đồng thời được dọn. Nếu có sự cố ngay giữa R2 put và ghi D1, có thể còn object mồ côi trong bucket; không có tiến trình quét toàn bucket để tránh tốn lượt đọc.

Theo [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/), [R2 pricing](https://developers.cloudflare.com/r2/pricing/) và [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/): hạn mức free hiện tại gồm Workers 100.000 request/ngày; R2 Standard 10 GB-tháng, 1 triệu Class A và 10 triệu Class B/tháng, không tính phí egress; D1 5 triệu row read/ngày, 100.000 row write/ngày và 5 GB tổng lưu trữ. R2 có thể yêu cầu phương thức thanh toán và tính phí vượt mức. Kiểm tra dashboard Usage/Billing khi vận hành.

Đây là kho ảnh/API riêng; source chưa tự thay nguồn ảnh mặc định của bot Waku. Có thể tích hợp REST API vào Waku sau khi kho có dữ liệu.
