# Waku API

Kho ảnh riêng chạy trên Cloudflare Workers + R2 + D1. Bot Telegram nhận ảnh hoặc link ảnh, lưu vào R2, trả link trực tiếp. Source nằm trong thư mục `api/` trên nhánh chính `v2` của repo Waku. API ảnh deploy độc lập lên Cloudflare, không chạy trong container bot; không cần VPS. Có thể thêm API vision để tự gắn tag, hoặc dùng hoàn toàn thủ công.

## Dùng bot

Mở DM của bot upload bằng tài khoản đã cấp quyền. Gửi một hoặc nhiều ảnh; mỗi ảnh được xử lý riêng. Gửi dạng **file ảnh** nếu muốn giữ nguyên chất lượng; ảnh gửi dạng Photo được lưu theo bản Telegram đã nén.

Có thể chọn cả đợt nhiều ảnh/file, kể cả khi Telegram tách thành nhiều album. Bot lưu từng update vào hàng đợi D1; toàn hệ thống giới hạn **2 upload và 2 lượt AI đồng thời**, không phải 2 lượt cho mỗi Worker. Phản hồi mới trong cùng chat được giãn ít nhất khoảng 1,1 giây để tránh gửi dồn. Mỗi file được lưu độc lập, file lỗi không chặn cả đợt. Caption chỉ đi cùng file mà Telegram gắn caption đó; muốn áp dụng tag chung cho cả đợt, dùng trang web bên dưới.

## Trang quản lý trên web

Mở URL gốc của Worker, ví dụ `https://waku-api.your-account.workers.dev/`. Trang được phục vụ ngay trên Worker hiện có, không cần thêm hosting, domain hoặc dịch vụ khác.

Đăng nhập bằng `API_KEY` trong file local `api/.secrets.json` (không phải key AI hoặc token Telegram). Key không được ghi vào HTML, URL hoặc localStorage. Sau khi xác thực, trình duyệt dùng cookie HttpOnly/Secure, hiệu lực 8 giờ. Đăng xuất để xóa phiên trên trình duyệt.

- Chọn/kéo thả nhiều ảnh, hoặc dán nhiều URL ảnh trực tiếp (mỗi dòng một link).
- Chọn tag chung, phân loại và bật/tắt AI cho đợt upload; mặc định dùng AI. Các lựa chọn này được chốt khi bấm bắt đầu.
- Trình duyệt tải tối đa **2 file mỗi lượt**. Có tiến độ từng file, tạm dừng sau các file đang chạy, bỏ file chờ và thử lại mục lỗi. Khi máy chủ bận, file sẽ chờ rồi thử lại.
- Giữ tab mở trong lúc upload. Sau khi ảnh được lưu, AI chạy nền và tiếp tục dù đóng trang; các file vẫn còn chờ ở trình duyệt chưa được gửi lên máy chủ.
- Thư viện có tìm theo tên/tag, lọc An toàn/R18/chưa phân loại, phân trang, sửa tên/tag/nguồn, đổi phân loại, chạy lại AI, sao chép link và xóa ảnh.
- Ảnh R18 được làm mờ trong thư viện, có tùy chọn hiện ảnh; mở chi tiết để xem và chỉnh sửa.
- Web và bot Telegram dùng chung dữ liệu, không có hai kho ảnh riêng.

Trên PC, lưới ảnh hiển thị 6 cột; màn hình nhỏ tự giảm cột. Mỗi ảnh có checkbox, **Chọn tất cả** lấy toàn bộ kết quả trong tab/bộ lọc hiện tại, kể cả trang chưa tải. **Bỏ chọn** xóa lựa chọn; **Xóa** hỏi lại tổng số ảnh trước khi xóa hàng loạt.

Nút **Sửa** cạnh Xóa mở popup sửa hàng loạt **Phân loại** và **Kiểu ảnh**. Mỗi mục mặc định **Giữ nguyên**, nên có thể chỉ sửa kiểu ảnh bị chọn nhầm lúc upload. Chọn giá trị rồi bấm Lưu thay đổi. Thao tác áp dụng cho các ảnh đã chọn, kể cả trang chưa tải, gửi tối đa 100 ID/request; giữ tên, tag và nguồn. Có thể thử lại các ảnh còn lỗi nếu một đợt chưa hoàn tất.

**Lọc** nằm cùng hàng với Chọn tất cả. Trên máy tính rê chuột vào để mở, giữ bảng khi di chuyển bên trong và thu lại khi rời ra; trên điện thoại bấm để mở/đóng. Có thể dùng bàn phím và Escape. Logo Waku được dùng ở sidebar, trang đăng nhập và biểu tượng tab.

**API & Model** nằm trên nút Đăng xuất ở sidebar (biểu tượng bánh răng trên điện thoại). Thêm/sửa/xóa kết nối bằng tên, Base URL OpenAI-compatible và key; mỗi API lưu được nhiều Model ID. Tích chọn một API và một model để dùng, tự nhớ model đã chọn khi chuyển qua lại giữa các API. Thêm tối đa 30 API và 100 model/API. Cấu hình hiện có được nhập thành **API Waku** lần đầu mở mục này.

Lựa chọn được lưu trong D1 và dùng chung cho web/bot upload Telegram, không cần deploy. Lượt AI đang chạy tiếp tục với cấu hình đã lấy; các lượt kế tiếp đọc lựa chọn mới. Xóa model đang dùng sẽ chọn model còn lại của API nếu có; xóa API đang dùng sẽ ngừng AI đến khi chọn kết nối khác, không âm thầm quay về cấu hình cũ. Upload thủ công vẫn hoạt động.

Key nhà cung cấp được mã hóa AES-GCM trước khi lưu D1, không trả lại qua API và không lưu ở localStorage. Khi sửa API, để trống key để giữ key cũ. Khóa mã hóa được dẫn xuất từ `API_KEY` quản trị: nếu thay `API_KEY`, cần nhập lại các key nhà cung cấp đã lưu. Sau khi quản lý API trên web, danh sách D1 là nguồn cấu hình chính; deploy lại secret AI không ghi đè lựa chọn này.

Khi bắt đầu upload, web chuyển sang **Chờ phân loại** và hiện trước các file đang chờ/gửi, sau đó thay bằng ảnh đã lưu. Tab này gồm ảnh `unknown` và ảnh còn chờ/chạy AI, kể cả ảnh đã được nhập phân loại thủ công. Khi AI xong, ảnh đã có phân loại chuyển sang tab tương ứng; ảnh vẫn chưa rõ phân loại ở lại. Có thể chọn ảnh trong tab này rồi bấm **AI gắn tag** để xử lý hàng loạt.

Thanh upload đếm **ảnh được API xác nhận đã lưu / tổng ảnh của đợt**, không cộng file lỗi vào số đã lưu. Thanh AI phía trên danh sách dùng bộ đếm D1 riêng theo đợt: đang chờ, đang chạy, thành công, lỗi, bỏ qua. Đây là hai tiến trình khác nhau; upload xong không đồng nghĩa AI đã hoàn tất. ID đợt AI được giữ trong sessionStorage để tiếp tục theo dõi sau khi tải lại trang; API key không được lưu ở đó.

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

Model nhận URL ảnh đã lưu và metadata thủ công. Tag tự sinh dùng tiếng Anh, chữ thường và dấu `_` cho dễ tìm (`cat_ears`, `maid`, `blue_hair`, `cosplay`…). Chỉ tự gán `safe` khi độ chắc chắn cao; ảnh chưa xác định không bị trộn vào random an toàn. Giao diện và API vẫn cho bạn sửa lại.

Ảnh luôn lưu trước; lỗi AI không làm mất ảnh hay báo upload thất bại. Tác vụ phân tích lưu trong D1 và được Cron khôi phục. Lỗi mạng/429/5xx được thử lại tối đa 3 lần; lỗi key/model/output cần sửa cấu hình hoặc bấm chạy lại. Upload trùng ảnh đã có tác vụ không tự gọi AI thêm lần nữa. Nhấn **AI gắn tag** sẽ chủ động gọi lại.

API upload trả ảnh ngay, có thêm `ai_status`: `pending`, `running`, `done`, `failed`, `skipped` hoặc `off` (ảnh cũ chưa có tác vụ). Khi cần chờ tag, đọc lại `/v1/images/ID`. Thêm `manual=true` vào multipart, hoặc `"manual":true` vào JSON upload để bỏ qua AI. Phí phân tích tính theo provider AI bạn chọn, tách khỏi hạn mức miễn phí của Cloudflare.

## REST API

Các đường dẫn `/v1/*` cần header `Authorization: Bearer API_KEY`. Lấy key từ `.secrets.json`. Không truyền key qua query URL. Ví dụ dưới dùng biến `$WAKU_API_KEY` đã được đặt trên shell.

| Method | Đường dẫn | Chức năng |
| --- | --- | --- |
| GET | `/health` | Kiểm tra Worker đang phản hồi |
| POST/GET/DELETE | `/v1/session` | Đăng nhập, kiểm tra hoặc xóa cookie phiên web |
| GET | `/v1/stats` | Dung lượng, số ảnh và tác vụ đang chờ |
| GET | `/v1/state?batch=ID` | Gộp thống kê, phiên bản thư viện và tiến trình AI trong một request |
| GET | `/v1/ai/settings` | Danh sách API/model và lựa chọn đang dùng, không trả key |
| POST | `/v1/ai/providers` | Lưu API: `name`, `base_url`, `api_key` |
| PATCH/DELETE | `/v1/ai/providers/ID` | Sửa/xóa API; key trống giữ nguyên |
| POST | `/v1/ai/providers/ID/models` | Thêm `{"model":"model-id"}` |
| PATCH/DELETE | `/v1/ai/models/ID` | Sửa/xóa model |
| PUT | `/v1/ai/selection` | Chọn `provider_id`, `model_id`; không gửi model để nhớ lại model gần nhất |
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
