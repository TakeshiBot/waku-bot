# Waku API

Kho ảnh riêng chạy trên Cloudflare Workers + R2 + D1. Bot Telegram nhận ảnh hoặc link ảnh, lưu vào R2, trả link trực tiếp. Không cần VPS, Docker hay API AI. Source nằm trên nhánh `waku-api`; bot Waku hiện tại không bị thay đổi.

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
- Không có phân loại: ảnh vẫn lưu nhưng chưa vào API random. Bấm **An toàn** hoặc **R18** dưới phản hồi để phân loại.
- Tag và tiêu đề do người upload nhập, không tự đoán nhân vật bằng AI.
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
cd D:\project2026\waku-api\api
npm.cmd ci
npm.cmd run check
npm.cmd test
npm.cmd run deploy -- --credentials D:\project2026\waku-bot\api.txt
```

Linux/macOS dùng `npm` thay `npm.cmd`. Chạy lại deploy để cập nhật source; tài nguyên và key được giữ nguyên. Script áp dụng migrations, deploy Worker, cài secrets rồi đăng ký webhook cho **bot upload đã cấu hình**. Không dùng token bot Waku đang chạy cho uploader này.

File tự sinh, không commit:

- `wrangler.local.json`: Account ID, database ID, danh sách người upload, URL triển khai.
- `.secrets.json`: API key và webhook secret; lưu an toàn để kết nối Waku sau này. Không gửi nội dung lên chat.
- `api.txt`, `.dev.vars*`, `.wrangler/`, `node_modules/` cũng được ignore.

Đổi owner hoặc biến cấu hình trong `wrangler.jsonc`/credentials rồi chạy deploy. URL workers.dev được tạo từ account. Custom domain có thể cấu hình sau trong Cloudflare.

Chỉ đăng ký lại webhook, không build:

```text
npm run webhook -- --credentials /path/to/api.txt
```

Chạy local: copy `.dev.vars.example` thành `.dev.vars`, điền giá trị, `npm run db:local`, `npm run dev`. Telegram webhook cần URL HTTPS công khai nên phần đó dùng bản đã deploy hoặc tunnel riêng.

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
