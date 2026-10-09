# Bảng quản trị

waku có bảng quản trị dựa trên
[Telegram Mini Apps](https://core.telegram.org/bots/webapps), cho phép cấu hình
bot ngay trong Telegram.

## Điều kiện triển khai

1. Có tên miền và chứng chỉ HTTPS hợp lệ.
2. Đăng ký Mini App cho bot trong [@BotFather](https://t.me/BotFather).

Khi build bằng Docker, frontend được đóng gói sẵn trong image.

## Đăng ký Mini App

Gửi `/newapp` trong [@BotFather](https://t.me/BotFather), chọn bot, rồi điền:

| Trường | Nội dung |
| --- | --- |
| Title | Tên hiển thị, ví dụ `Bảng quản trị waku` |
| Description | Mô tả bảng quản trị |
| Photo | Ảnh 640 × 360, bắt buộc |
| Web App URL | `https://panel.example.com` |
| Short name | `panel` |

Web App URL phải trùng với `webapp_url` trong cấu hình. Short name phải trùng
với `webapp_short_name`.

## Cấu hình bot

Thêm các dòng sau vào `settings.toml`:

```toml
webapp = true
webapp_url = "https://panel.example.com"
webapp_short_name = "panel"
```

## Khởi động

```bash
docker compose up -d --build
docker compose logs -f waku
```

API và health check dùng chung cổng `8180`. Cấu hình reverse proxy cung cấp
HTTPS cho tên miền của bảng quản trị và chuyển yêu cầu đến cổng này.

## Các tùy chọn cấu hình

| Tùy chọn | Giá trị mặc định | Ý nghĩa |
| --- | --- | --- |
| `webapp` | `false` | Bật bảng quản trị |
| `webapp_host` | `"0.0.0.0"` | Địa chỉ lắng nghe |
| `webapp_port` | `8180` | Cổng lắng nghe |
| `webapp_url` | `""` | URL HTTPS công khai, bắt buộc khi bật bảng quản trị |
| `webapp_short_name` | `"panel"` | Short name của Mini App đã đăng ký trong BotFather |
| `webapp_menu_button` | `true` | Cho nút menu trong cuộc trò chuyện mở bảng quản trị |
| `webapp_jwt_secret` | `""` | Khóa ký token phiên; để trống thì tạo từ token của bot |
| `webapp_jwt_ttl` | `21600` | Thời hạn phiên, tính bằng giây |
| `webapp_initdata_ttl` | `300` | Thời hạn dữ liệu khởi tạo, tính bằng giây |
| `webapp_allow_origins` | `[]` | Danh sách origin CORS được phép, dùng khi phát triển cục bộ |
| `webapp_trusted_proxies` | `["127.0.0.1", "::1"]` | Địa chỉ proxy được tin cậy khi đọc `X-Forwarded-For` |
| `webapp_static_dir` | `""` | Thư mục frontend đã build; để trống thì dùng thư mục mặc định |
| `webapp_admin_edit_user` | `true` | Cho phép sửa thông tin người dùng trong bảng quản trị |
