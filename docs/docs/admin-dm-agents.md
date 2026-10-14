# File md cho DM của admin bot

Mở `/config` trong DM Telegram của admin bot, chọn **File md** bên dưới
**Chức Năng / Cấu Hình**. Khi có file đang bật, nút này hiển thị tên file đó.
Danh sách file có 2 cột, tên nút giữ nguyên tên file tải lên. Dấu ✅ đánh dấu
file đang dùng. Chỉ chọn được một file: bấm file khác để chuyển, bấm lại file
đang chọn để tắt. Các thao tác có hiệu lực ngay, không có nút Lưu.

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

File dùng chung cho owners và bot global admins. Đây là **lớp ngữ cảnh riêng**
chứa thông tin, kiến thức, kỹ năng hoặc tính cách bổ sung: bot đọc file đang chọn
trước, rồi đến prompt chính của cuộc chat (kể cả prompt riêng đã đặt cho DM).
Nội dung được gửi thành message ngữ cảnh riêng, không nối vào chuỗi prompt chính
và không sửa `agent_prompt`. Hai lớp nằm trong cùng request tới model. Các hướng
dẫn ngôn ngữ và ngữ cảnh hiện có vẫn được giữ. File không áp dụng cho nhóm, DM của người dùng
thường, Telegram Business, guest chat hoặc Discord. Nó không cấp thêm quyền
cho công cụ và không thay đổi prompt chung.

Có thể dùng [file mẫu](examples/admin-dm-agents.md), chỉnh nội dung rồi tải lên.
Nếu admin khác vừa đổi lựa chọn, nút xoá trong menu cũ sẽ không xoá nhầm file mới.
