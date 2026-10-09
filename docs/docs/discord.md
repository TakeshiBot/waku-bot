# Bot Discord

Discord chạy cùng tiến trình `python -m waku`, dùng schema cấu hình, database,
scheduler và AI provider của base hiện tại. Bot giữ prefix `!`, menu, embed và
cách tương tác của bản Discord đã mod. Discord không cần URL/key AI riêng:
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

`discord_admin_users` chứa ID tài khoản Discord được quản trị Waku và duyệt server.
Danh sách `owners` của Telegram không cấp quyền Discord. Allowlist rỗng cho phép
các kênh trong server đã được cấp quyền; khi có ID, bot chỉ hoạt động trong những
kênh đó. Bỏ `discord_keywords` để dùng nickname mặc định.

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

- `!waku`, `!unwaku`: cấu hình việc Waku hoạt động trong server, theo quyền hiện có.
- `!config`: mở menu server hoặc DM; giữ các lựa chọn AI, ảnh, bộ nhớ và ngôn ngữ.
- `!server`: danh sách server dành cho quản trị viên Waku.
- `!forget`: xoá ngữ cảnh AI hiện tại.
- `!help`, `!invite`: trợ giúp và liên kết mời.
- `!clean`: xoá tin của chính Waku khi đủ quyền, theo giới hạn của bản mod.
- `/seg`: gửi ảnh anime/Pixiv.
- `/bc`: phát thông báo; có lựa chọn server hiện tại, đã/chưa cấp quyền, tất cả
  hoặc ID server, theo quyền quản trị Discord.

Server chưa được cấp quyền dùng menu yêu cầu cấp quyền; quản trị viên Discord
trong `discord_admin_users` nhận menu duyệt/từ chối. Các nút cấp quyền và danh
sách server được đăng ký lại khi bot khởi động để tiếp tục dùng sau restart.

AI có các tool Discord để tìm kênh/người dùng, đọc tin có quyền truy cập, nhớ thông
tin server, reaction, ảnh và lịch gửi tin/ảnh. Lịch dùng scheduler của base hiện
tại, múi giờ `Asia/Ho_Chi_Minh` (UTC+7), có namespace riêng.

Settings server/DM được lưu trong bảng `discord_chat_data`, không dùng bảng nhóm
Telegram. Bot không tự chép dữ liệu chạy hoặc credentials từ source cũ.
