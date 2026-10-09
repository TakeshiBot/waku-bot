# Đổi tên dự án và múi giờ

## Tên dự án

Tên hiển thị của bot và dịch vụ Compose là `waku`. Tên phân phối Python và
container là `waku-bot`.
Repository hiện tại là
[`TakeshiBot/waku-bot`](https://github.com/TakeshiBot/waku-bot).
Docker Compose build image `waku-bot:local` từ source trong repository.

Namespace Python `kmua` được giữ để bảo toàn các import và entrypoint. Lệnh
chạy vẫn là `python -m kmua`; tên module không phải tên hiển thị của bot.
Thông tin tác giả, giấy phép và nguồn upstream được giữ để ghi nhận nguồn gốc.

Khi nâng cấp bản đang chạy, sao lưu `settings.toml`, cơ sở dữ liệu, file phiên
Telegram và các thư mục `data/`, `logs/` trước khi thay container. Đối chiếu
đường dẫn database và tên phiên trong cấu hình với dữ liệu có sẵn để tiếp tục
sử dụng đúng dữ liệu của bản cũ. Dừng project Compose cũ bằng
`docker compose down --remove-orphans` trước khi chạy bản mới; xem
[hướng dẫn nâng cấp](self-host.md).

## Múi giờ UTC+7

Bot dùng `Asia/Ho_Chi_Minh` (UTC+7) làm múi giờ mặc định cho lịch chạy và các
giá trị thời gian địa phương. Giá trị này được định nghĩa tập trung trong
`kmua/timezone.py`; không có khóa `timezone` trong `settings.toml`.
Container dùng cùng múi giờ qua biến `TZ`.

Log của bot và ngày giờ trong bảng quản trị Mini App dùng UTC+7, kể cả khi máy
chủ hoặc trình duyệt dùng múi giờ khác. Log mới được ghi vào `logs/waku.log`.
Các bộ lọc thời gian trong bảng quản trị
hiểu giá trị nhập không có offset theo UTC+7; giá trị có offset giữ đúng thời điểm
được chỉ định. Timestamp database không có offset được đọc theo UTC trước khi
chuyển sang UTC+7 để hiển thị.

Ngày giờ đã có múi giờ hoặc được người dùng chỉ định bằng tên IANA hợp lệ tiếp
tục dùng múi giờ đã chỉ định. Lịch đã lưu trong cơ sở dữ liệu giữ múi giờ trong
trigger cũ; muốn chuyển lịch đó sang UTC+7 thì cần tạo lại lịch.
Dữ liệu UTC phục vụ xác thực, timestamp và giao thức
được giữ nguyên. Bộ đếm quota theo ngày tiếp tục dùng ngày UTC để bảo toàn dữ liệu
đã lưu: mốc đổi ngày là 00:00 UTC, tức 07:00 tại Việt Nam.

Khi chạy trực tiếp trên máy chủ, đặt biến môi trường `TZ=Asia/Ho_Chi_Minh`
để log của môi trường và các tiện ích hệ thống hiển thị nhất quán.
