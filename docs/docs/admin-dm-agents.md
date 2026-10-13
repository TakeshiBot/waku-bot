# AGENTS.md cho DM của admin bot

Mở `/config` trong DM Telegram của admin bot, chọn **AGENTS.md** bên dưới
**Chức Năng / Cấu Hình**. Chọn **Thêm / thay file**, gửi file Markdown UTF-8
(tối đa 32 KB), rồi bấm **Lưu**. Lưu sẽ áp dụng và đóng menu. **Đóng** bỏ thay đổi
chưa lưu. **Xoá file** cũng cần bấm **Lưu** để thực hiện. **Xem file** gửi lại file
đang hiển thị trong menu, kể cả bản thay thế đang chờ lưu.

Bot lưu nội dung thành `data/admin_dm/AGENTS.md`. Tên file tải lên chỉ cần có
đuôi `.md`; không cần thêm biến vào `settings.toml`. Dữ liệu nằm trong volume
`data` của Docker, được giữ khi build lại và nằm trong bản sao lưu dữ liệu.
Thay đổi áp dụng khi bot đọc hướng dẫn cho request tiếp theo, không cần restart.

File dùng chung cho owners và bot global admins. Đây là **lớp ngữ cảnh riêng**
chứa thông tin, kiến thức, kỹ năng hoặc tính cách bổ sung: bot đọc lớp AGENTS.md
trước, rồi đến prompt chính của cuộc chat (kể cả prompt riêng đã đặt cho DM).
Nội dung được gửi thành message ngữ cảnh riêng, không nối vào chuỗi prompt chính
và không sửa `agent_prompt`. Hai lớp nằm trong cùng request tới model. Các hướng
dẫn ngôn ngữ và ngữ cảnh hiện có vẫn được giữ. File không áp dụng cho nhóm, DM của người dùng
thường, Telegram Business, guest chat hoặc Discord. Nó không cấp thêm quyền
cho công cụ và không thay đổi prompt chung.

Có thể dùng [file mẫu](examples/admin-dm-agents.md), chỉnh nội dung rồi tải lên.
Hai admin cùng sửa sẽ không ghi đè âm thầm: phiên giữ bản cũ phải mở lại `/config`.
