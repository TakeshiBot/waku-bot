# Waku API

Kho ảnh riêng chạy trên Cloudflare Workers + R2 + D1. Bot Telegram nhận ảnh hoặc link ảnh, lưu vào R2, trả link trực tiếp. Source nằm trong thư mục `api/` trên nhánh chính `v2` của repo Waku. API ảnh deploy độc lập lên Cloudflare, không chạy trong container bot; không cần VPS. Có thể thêm API vision để tự gắn tag, hoặc dùng hoàn toàn thủ công.

## Dùng bot

Mở DM của bot upload bằng tài khoản đã cấp quyền. Gửi một hoặc nhiều ảnh; mỗi ảnh được xử lý riêng. Gửi dạng **file ảnh** nếu muốn giữ nguyên chất lượng; ảnh gửi dạng Photo được lưu theo bản Telegram đã nén.

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
| POST | `/v1/images` | Upload multipart `file`, hoặc JSON chứa `url` |
| GET | `/v1/images?limit=20&before=CURSOR` | Danh sách, tối đa 100/trang; `next` là cursor trang kế |
| GET | `/v1/images/random?count=10&rating=safe&tags=cat,cute` | Lấy tối đa 10 ảnh, các tag kết hợp AND |
| GET | `/v1/images/ID` | Xem metadata |
| PATCH | `/v1/images/ID` | Sửa các trường `title`, `tags`, `rating`, `source_url` |
| DELETE | `/v1/images/ID` | Xóa ảnh |
| GET/HEAD | `/media/ID` | File ảnh gốc, không cần API key |

`rating` của random: `safe` (mặc định), `r18`, `mixed`. Ảnh `unknown` không xuất hiện trong random. Kết quả ít hơn `count` nếu kho không đủ ảnh phù hợp; không lặp ID trong cùng lượt. Truyền `exclude=ID1,ID2,...` để tránh lặp giữa các lượt; tổng số tag và ID loại trừ tối đa 96. Bot gọi API cần lưu lịch sử ID đã gửi.

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
