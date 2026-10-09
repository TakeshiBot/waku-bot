# Đổi tên dự án và múi giờ

## Tên dự án

Tên hiển thị của bot và dịch vụ Compose là `waku`. Tên phân phối Python và
container là `waku-bot`.
Repository hiện tại là
[`TakeshiBot/waku-bot`](https://github.com/TakeshiBot/waku-bot).
Docker Compose build image `waku-bot:local` từ source trong repository.

Thư mục source và namespace Python đã đổi thành `waku`. Lệnh chạy là
`python -m waku`; các import, cấu hình build và migration dùng namespace mới.
Thông tin tác giả, giấy phép và nguồn upstream được giữ để ghi nhận nguồn gốc.

Khi khởi động, bot tự chuyển các tham chiếu module `kmua` trong lịch chạy đã lưu
sang `waku` để tiếp tục thực thi các job cũ. Giao thức tham chiếu source dùng
`waku://`; `kmua://` vẫn được chấp nhận như alias để lịch sử hội thoại và đường
dẫn đã lưu tiếp tục hoạt động. Namespace cache và các giá trị nhận diện JWT cũ
được giữ để tương thích với dữ liệu và phiên đăng nhập đã có.

Khi nâng cấp bản đang chạy, sao lưu `settings.toml`, cơ sở dữ liệu, file phiên
Telegram và các thư mục `data/`, `logs/` trước khi thay container. Đối chiếu
đường dẫn database và tên phiên trong cấu hình với dữ liệu có sẵn để tiếp tục
sử dụng đúng dữ liệu của bản cũ. Bản mẫu mới dùng `data/waku.db` và tên phiên
`waku`. Nếu đã có database/phiên mang tên cũ, giữ các giá trị cũ trong cấu hình
riêng để tiếp tục dùng dữ liệu đó. `settings.toml` và các file env được Git bỏ qua;
repository chỉ chứa cấu hình mẫu `settings.ex.toml` không có token/API key riêng.
Dừng project Compose cũ bằng
`docker compose down --remove-orphans` trước khi chạy bản mới; xem
[hướng dẫn nâng cấp](self-host.md).

## Múi giờ UTC+7

Bot dùng `Asia/Ho_Chi_Minh` (UTC+7) làm múi giờ mặc định cho lịch chạy và các
giá trị thời gian địa phương. Giá trị này được định nghĩa tập trung trong
`waku/timezone.py`; không có khóa `timezone` trong `settings.toml`.
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
