# Bot Discord

Discord chạy cùng tiến trình `python -m waku`, dùng schema cấu hình, database,
scheduler và AI provider của base hiện tại. Thêm bot vào server là dùng được ngay,
không có bước xin hoặc duyệt quyền Waku. Bot dùng slash command và menu embed
riêng tư trong server; lệnh quản trị bot dùng tiền tố `!` trong DM. Discord không cần URL/key AI riêng:
`agent_model` chọn provider trong `[agent_providers.*]` như Telegram.

## Cấu hình và chạy

Thêm các dòng sau **ở cấp gốc** của `settings.toml`, trước các bảng
`[agent_providers.*]`. Không đặt chúng dưới một bảng provider.

```toml
discord_enabled = true
discord_token = "TOKEN_BOT_DISCORD"
discord_admin_users = [123456789012345678]
discord_channel_allowlist = []
discord_keywords = ["waku"]

agent = true
agent_model = "default/ten-model"
```

`discord_admin_users` chứa ID tài khoản Discord được quản trị Waku trên nhiều server.
Danh sách `owners` của Telegram không cấp quyền Discord. Chủ server và thành viên
có quyền Administrator được sửa cấu hình của server mình. Thành viên thường có
thể trò chuyện và dùng các lệnh thông thường ngay.

Allowlist rỗng cho phép các kênh trong mọi server bot đã tham gia; khi chủ bot
cấu hình ID, bot chỉ hoạt động trong phạm vi đó. Bỏ `discord_keywords` để dùng
nickname mặc định.

Trong Discord Developer Portal → Bot, bật **Message Content Intent** và
**Server Members Intent**. Phần Discord cũ sử dụng cả nội dung tin và danh sách
thành viên; các intent này phải được bật trong portal và trong client.
Xem [tài liệu Gateway của Discord](https://github.com/discord/discord-api-docs/blob/main/developers/events/gateway.mdx).

Mời ứng dụng bằng OAuth2 scopes `bot` và `applications.commands`. Bot cần quyền
xem kênh, đọc lịch sử, gửi tin, embed, đính kèm file và thêm reaction để sử dụng đầy
đủ các chức năng. Quyền mention mọi người chỉ cần khi dùng chức năng đó; nội dung
gửi vẫn được kiểm tra quyền ở từng kênh.

Từ thư mục source, cài dependency và chạy như trước:

```bash
uv sync
uv run python -m waku
```

Thay đổi token hoặc bật/tắt nền tảng cần khởi động lại. Khi chưa bật Discord,
Telegram chạy như trước. Không đưa token thật vào repository; settings và env
riêng đã được `.gitignore` loại trừ.

## Menu và lệnh

| Lệnh | Chức năng |
| --- | --- |
| `/config` | Cấu hình server; trong DM là cấu hình chat riêng của bot admin. Bot admin có thêm công tắc AI toàn Discord. |
| `!info [ID server]` | Không có ID: trạng thái Waku. Có ID: cấu hình chi tiết server bot đang tham gia. Chỉ bot admin dùng trong DM. |
| `/help` | Hướng dẫn trò chuyện và danh sách lệnh. |
| `/forget` | Xoá ngữ cảnh AI của người gọi trong kênh hiện tại. |
| `/invite` | Liên kết thêm Waku vào server. |
| `/clean` | Xoá tối đa 50 tin của Waku; cần quyền quản lý tin hoặc quản trị. |
| `!server` | Danh sách đánh số, mỗi dòng gồm ID server và số thành viên; mới tham gia trước, tối đa 50 server/trang, có nút chuyển trang. Chỉ bot admin dùng trong DM. |
| `/seg` | Gửi ảnh anime/Pixiv khi dịch vụ ảnh đã được cấu hình. |
| `!bc all <nội dung>` | Phát thông báo đến mọi server; chỉ quản trị viên bot dùng trong DM. |
| `!bc <ID server> <nội dung>` | Phát thông báo đến server đã chọn; chỉ quản trị viên bot dùng trong DM. |

Menu và phản hồi lệnh slash trong server dùng embed **chỉ người gọi thấy** (ephemeral).
Ba lệnh quản trị `!bc`, `!info`, `!server` phản hồi bằng embed trong DM riêng của
quản trị viên bot. Ảnh được yêu cầu và nội dung phát thông báo được gửi tới kênh
đã chọn. `/bc`, `/info`, `/server` đã bị loại khỏi danh sách slash command.
`!bc` cần chỉ rõ `all` hoặc ID server; hỗ trợ xuống dòng thật và ký tự `\n`.

Bot admin trong `discord_admin_users` dùng được mọi lệnh slash trong DM, kể cả
`/config`, `/help`, `/forget`, `/invite`, `/clean`, `/seg`. User thường nhắn hoặc
gọi lệnh trong DM không được phản hồi. `/clean` trong DM chỉ xoá tin của bot.

`/config` chỉ hiển thị tên server và lưu ý ngắn; các nút ✅/❌ thể hiện trạng thái
AI trả lời, trả lời bot khác, bộ nhớ, chế độ ảnh và ngôn ngữ.
Nút **Ảnh** chuyển An toàn → Chỉ R18 → Cả hai → Tắt; trong DM chuyển An toàn ↔ Tắt.
Cấu hình mới mặc định ảnh an toàn và **Trả lời bot khác** tắt. Các lựa chọn đã lưu
được giữ nguyên. **Trả lời bot khác** áp dụng riêng cho server hiện tại;
tắt mục này không tắt trò chuyện với user. Trong DM của bot admin,
**AI trả lời** bật/tắt chat riêng cho admin đó; không có nút trả lời bot khác.
Bot admin còn có nút **AI Discord toàn bot**, điều khiển trả lời AI tại mọi server
và DM. Tắt công tắc này cũng dừng các lượt AI đang chạy và việc học bộ nhớ;
các lệnh/menu và yêu cầu ảnh trực tiếp vẫn hoạt động. Công tắc được lưu riêng
trong database và giữ nguyên sau khi khởi động lại; không thay đổi AI Telegram.
Thay đổi nằm trong bản nháp riêng cho đến khi bấm **Lưu**; có **Huỷ thay đổi**,
**Tải lại** và **Đóng**. Bot kiểm tra lại quyền ở mỗi lần dùng menu. Menu hết hạn
sau khoảng 15 phút; mở lại `/config` khi cần. Đây là giới hạn của interaction
[theo tài liệu Discord](https://github.com/discord/discord-api-docs/blob/main/developers/interactions/receiving-and-responding.mdx).

Các lệnh quản lý `!config`, `!waku`, `!unwaku`, `!forget`, `!help`,
`!invite`, `!clean` cũ đã ngừng sử dụng. Lệnh ảnh văn bản `!seg` vẫn hỗ trợ.
Trạng thái chờ duyệt/từ chối cũ không chặn server, và các lựa chọn đã lưu như
AI tắt, ngôn ngữ hoặc ảnh vẫn được giữ.

Trong server, tag Waku, trả lời tin của Waku hoặc gọi nickname để trò chuyện;
bot admin có thể chat trực tiếp trong DM khi AI riêng và AI toàn Discord cùng bật.
Tin nhắn DM của user thường không được phản hồi. AI có tool tìm kênh/người dùng, đọc tin có quyền
truy cập, bộ nhớ theo kênh, reaction, ảnh và lịch gửi tin/ảnh. Bộ nhớ từng kênh
được tách riêng để tránh lấy nội dung từ kênh kín sang kênh khác. Lịch dùng
scheduler hiện tại, múi giờ `Asia/Ho_Chi_Minh` (UTC+7), có namespace riêng.

Trong server, bot khác cũng có thể gọi nickname, tag hoặc reply Waku để trò chuyện,
theo công tắc **Trả lời bot khác** trong `/config`, công tắc AI và giới hạn kênh hiện tại.
Waku bỏ qua tin do chính mình gửi và
tin bot khác không gọi Waku. Mỗi bot khác được tối đa 6 lượt gọi trong 60 giây
ở một kênh để hạn chế vòng lặp tự động; user thường không chịu giới hạn này.
DM từ bot khác vẫn được bỏ qua.

Settings server, cấu hình DM của admin và công tắc AI toàn Discord được lưu riêng
trong bảng `discord_chat_data`, không dùng bảng nhóm Telegram. Bot không tự chép
dữ liệu chạy hoặc credentials từ source cũ.

## Cách trả lời và dùng biểu cảm

Bot giữ cách gửi của bản Discord cũ: phản hồi AI là tin nhắn thường, tin dài được
chia theo đoạn/dòng, giữ khối code, tối đa 7 tin và có khoảng nghỉ giữa các tin.
Chỉ tin đầu reply vào tin người gọi, không ping người gọi; các tin sau gửi nối tiếp.
Nếu tin gốc bị xoá, phản hồi vẫn gửi được. Thông báo bot bận/lỗi dùng cùng cách gửi.

AI dùng emoji/biểu cảm tự nhiên theo ngữ cảnh, thường 1–3 trong hội thoại thân mật.
Bot học emoji và reaction của cuộc trò chuyện trong phạm vi AI đang bật, giữ cả
emoji ghép như `❤️`, `👍🏽`, `👩‍💻` và emoji custom. Reaction đã được Discord thêm
thành công vẫn được ghi nhận thành công khi cache học biểu cảm gặp lỗi.

Nhắc AI cân nhắc reaction định kỳ riêng cho Discord bằng:

```toml
discord_periodic_reaction_interval = 5
```

Đặt `0` để tắt; bỏ mục này để dùng `agent_periodic_reaction_interval` chung.
Mục riêng không đổi nhịp reaction Telegram. Đây là nhắc AI chọn reaction phù hợp,
không ép gắn emoji vào mọi tin hoặc nội dung nghiêm túc.
