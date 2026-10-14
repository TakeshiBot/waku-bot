# File md và phạm vi áp dụng

Mở `/config` trong DM Telegram của admin bot, chọn **File md** bên dưới
**Chức Năng / Cấu Hình**. Khi có file đang bật, nút này hiển thị tên file đó.
Danh sách file có 2 cột, tên nút giữ nguyên tên file tải lên. Dấu ✅ đánh dấu
file đang dùng. Chỉ chọn được một file: bấm file khác để chuyển, bấm lại file
đang chọn để tắt. Các thao tác có hiệu lực ngay, không có nút Lưu.

Nút **Phạm vi** nằm một hàng riêng ngay dưới danh sách file. Mỗi lần bấm chuyển
**Admin → DM → Global → Admin** và lưu ngay:

- **Admin** (mặc định): chỉ DM Telegram của owners và bot global admins, như bản cũ.
- **DM**: các lượt chat AI riêng trên Telegram và Discord; không áp dụng cho
  Telegram Business, nhóm hoặc server.
- **Global**: các lượt chat AI trên toàn bộ Telegram và Discord, gồm nhóm,
  server, Business, Guest và bình luận AI dưới bài đăng kênh.

Phạm vi là thiết lập chung cho file đang chọn, được giữ khi chuyển/thêm/xoá file.
Không có file đang bật thì không thêm lớp Markdown, ở mọi chế độ. Chỉ admin bot
được quản lý danh sách và đổi phạm vi; chế độ này không bật thêm quyền chat hay
cấp quyền thực thi công cụ cho người dùng.

**Thêm file** đổi thành **Huỷ thêm** trong lúc chờ tải lên. Gửi file `.md`
UTF-8 (tối đa 32 KB) trong 3 phút; bot lưu file và xoá tin nhắn upload đã nhận.
File mới chưa tự bật. Gửi file cùng tên sẽ thay nội dung file đó và giữ trạng
thái chọn hiện tại. **Xoá file** xoá ngay file đang chọn; chưa chọn file thì bot
báo bằng popup. **Trở lại** về menu chính, **Đóng** đóng menu. Các thay đổi đã
thực hiện được giữ lại.

Bot lưu danh sách và lựa chọn trong `data/admin_dm/library.json`, nội dung
trong `data/admin_dm/files/`. Tên hiển thị giữ nguyên, tên lưu nội bộ dùng ID.
File `data/admin_dm/AGENTS.md` của bản cũ tự chuyển thành một mục đang bật
trong danh sách. Dữ liệu nằm trong volume `data` của Docker, được giữ khi build
lại và nằm trong bản sao lưu dữ liệu. Không cần thêm biến vào `settings.toml`
hoặc restart để đổi file. Danh sách dài có phân trang; hỗ trợ tối đa 100 file.

Danh sách file được admin bot quản lý chung. Đây là **lớp ngữ cảnh riêng**
chứa thông tin, kiến thức, kỹ năng hoặc tính cách bổ sung: bot đọc file đang chọn
trước, rồi đến prompt chính của cuộc chat (kể cả prompt riêng đã đặt cho DM).
Nội dung được gửi thành message ngữ cảnh riêng, không nối vào chuỗi prompt chính
và không sửa `agent_prompt`. Hai lớp nằm trong cùng request tới model. Các hướng
dẫn ngôn ngữ và ngữ cảnh hiện có vẫn được giữ. Phạm vi được kiểm tra trước mỗi
request; đổi chế độ sẽ gỡ lớp Markdown cũ khỏi các request ngoài phạm vi mới.
Các tác vụ nội bộ như embedding, đánh giá hội thoại, học sticker không dùng lớp này.

Có thể dùng [file mẫu](examples/admin-dm-agents.md), chỉnh nội dung rồi tải lên.
Nếu admin khác vừa đổi lựa chọn, nút xoá trong menu cũ sẽ không xoá nhầm file mới.
