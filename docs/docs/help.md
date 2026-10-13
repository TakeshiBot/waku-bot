# Trợ giúp chi tiết

Phần trợ giúp chi tiết này cần được bổ sung khi chức năng thay đổi. Khi chạy bot,
dùng `/help` để xem hướng dẫn theo ngôn ngữ đã chọn. Đổi ngôn ngữ nhóm bằng
`/sett` → Ngôn Ngữ; đổi ngôn ngữ cá nhân trong bảng quản trị.

Trong chat riêng, mở `/start` và dùng nút **AI trong tin nhắn riêng** để bật/tắt AI
cho tài khoản của bạn. Mặc định tắt; lựa chọn được lưu trong cơ sở dữ liệu. Bạn cũng
có thể đổi lựa chọn trong phần hồ sơ của Mini App. Các giới hạn quota, whitelist và
điều kiện tham gia kênh vẫn được áp dụng khi bật AI.

Chủ bot và quản trị viên toàn cục có thể dùng các lệnh sau trong chat riêng:

- `/config`: mở trang tổng quan, bấm **Cấu Hình** để chọn nhóm cấu hình. Các biến
  được xếp hai cột; bấm biến boolean để bật/tắt trực tiếp, bấm biến khác để xem
  và sửa. **Chức Năng** có các công tắc Telegram, Discord, Chat Business và Tải
  Sticker DM, lưu riêng trong database. Các nút quay lại, đổi trang và đóng nằm dưới menu. Chuỗi/prompt nhập nguyên
  văn; danh sách/object nhập JSON hoặc TOML; `null` xoá giá trị tuỳ chọn nếu không
  kế thừa từ file trước. `/cancel` huỷ bước nhập. Danh sách biến cốt lõi cố định;
  biến chưa khai báo trong TOML vẫn có nút, hiện giá trị `""`; chỉnh và lưu sẽ thêm biến vào file.
  **Lưu** áp dụng bản nháp và giữ menu mở; **Đóng** bỏ thay đổi chưa lưu rồi đóng.
  Menu che key/token và thử xoá tin nhập giá trị.
- `/info`: xem các nhóm bot đã ghi nhận trong DB, 10 nhóm mỗi trang, kèm ID, liên
  kết và trạng thái truy cập. Dùng ◀️ / ▶️ để đổi trang, ✖️ để đóng; nút làm mới
  kiểm tra lại trang hiện tại.

`/config` trong nhóm tiếp tục dùng menu cấu hình nhóm hiện có. Menu riêng tư dùng
schema của base hiện tại, giữ comment TOML, kiểm tra dữ liệu trước khi ghi và từ
chối ghi đè nếu file đã đổi. Nếu có `settings.dev.toml`, menu sửa lớp override đó;
biến môi trường vẫn được ưu tiên sau khi khởi động lại.

Model, prompt và công tắc có thể đổi trực tiếp sẽ được áp dụng khi lưu. Chỉ khi
menu báo có cấu hình cần khởi động lại mới dùng nút **Khởi động lại**.
Đổi cấu hình provider không cần lặp lại URL/key trong các
field model: dùng tên provider như `default/ten-model`.

Xem thêm [hướng dẫn triển khai](self-host.md) và [bảng quản trị Mini App](webapp.md).
Đóng góp tài liệu trong thư mục
[`docs/` của repository](https://github.com/TakeshiBot/waku-bot/tree/v2/docs).
