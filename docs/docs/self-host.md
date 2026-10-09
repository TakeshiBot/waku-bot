# Hướng dẫn triển khai

Trước khi chạy bot, tắt Privacy Mode để bot nhận tin nhắn trong nhóm.
Bật Inline Mode và Inline Query Feedback. Thực hiện các thiết lập này trong
[@BotFather](https://t.me/BotFather).

## Chạy bằng Docker Compose

Clone toàn bộ repository để build image từ source hiện tại:

```bash
git clone https://github.com/TakeshiBot/waku-bot.git
cd waku-bot
```

Tạo cấu hình riêng bằng `cp settings.ex.toml settings.toml`, rồi sửa
`settings.toml`: điền token của bot vào `token` và ID Telegram của quản trị
viên vào danh sách `owners`. Các tùy chọn AI, RSS và Mini App có thể cấu hình
theo nhu cầu.

```bash
docker compose up -d --build
docker compose logs -f waku
```

Compose build image `waku-bot:local`. Cấu hình, dữ liệu và log được gắn từ
`settings.toml`, `data/` và `logs/` trên máy chủ. Khi cập nhật source, sao lưu
các mục này rồi chạy lại lệnh build.

### Nâng cấp từ bản cũ

Dịch vụ Compose đã đổi tên từ `kmua` sang `waku`. Dừng instance cũ trước
khi khởi động instance mới để hai container không cùng dùng phiên Telegram và
cơ sở dữ liệu. Trong thư mục/project Compose cũ, chạy:

```bash
docker compose down --remove-orphans
```

Sau đó chạy `docker compose up -d --build` với source mới. Lệnh dừng trên không
xóa các thư mục bind mount `data/`, `logs/` hoặc file `settings.toml`. Nếu đổi thư
mục dự án hoặc tên project Compose, cần dừng project cũ riêng và chuyển dữ liệu
đã sao lưu sang đường dẫn bind mount của project mới.

Múi giờ mặc định của bot và container là `Asia/Ho_Chi_Minh` (UTC+7).
Xem [ghi chú múi giờ và đổi tên](branding-timezone.md) nếu triển khai từ bản cũ.

Để dùng bảng quản trị, thiết lập HTTPS theo [hướng dẫn Mini App](webapp.md).

## Chạy trực tiếp từ source

Dự án yêu cầu **Python 3.13** theo `pyproject.toml`. Cài `graphviz` trên hệ
thống để tạo sơ đồ quan hệ. Chạy trực tiếp trên Linux vì dependency `uvloop`
không hỗ trợ Windows. AI agent shell cũng dùng Landlock/landrun trên Linux;
Docker là cách triển khai sẵn có của dự án.

```bash
git clone https://github.com/TakeshiBot/waku-bot.git
cd waku-bot
uv sync --frozen
```

Tạo `settings.toml` từ `settings.ex.toml` nếu chưa có, điền cấu hình như hướng dẫn
ở trên, rồi chạy:

```bash
uv run --no-sync python -m kmua
```

Tên phân phối là `waku-bot`. Thư mục và module Python `kmua` được giữ nguyên
để bảo toàn import, migration và lệnh khởi động.
