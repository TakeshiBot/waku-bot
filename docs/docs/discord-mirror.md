# Mirror từ Discord

Admin bot trong `discord_admin_users` có thể yêu cầu Waku mirror bằng chat ở
server hoặc DM. Bot Telegram và Discord của cùng instance Waku cần đang hoạt động.
Dùng chung `mirror_group_id` và `mirror_bot_username` đã cấu hình cho Telegram;
không cần sửa source bot Mirror hoặc thêm biến cấu hình.

Ví dụ: “Waku mirror link này rồi giải nén”, “leech hai link này”, “qm link này
rồi nén lại”, hoặc reply file đính kèm Discord và nói “Waku mirror file này”.
Hỗ trợ các chế độ và options của luồng Telegram, tối đa 20 nguồn mỗi lượt.
File Discord dùng URL CDN thật của attachment, không tải qua workspace hay vision.
Link CDN có thể hết hạn; file gốc cần còn truy cập được khi bot Mirror tải.

Waku gửi lời xác nhận do AI viết trước khi chuyển yêu cầu sang nhóm Mirror Telegram.
Khi task được nhận và khi kết thúc, AI gửi cập nhật về đúng kênh/thread/DM Discord
ban đầu, kèm nút tải từ kết quả đã xác minh. Leech vẫn gửi file lên Telegram;
Discord nhận các link Telegram có trong kết quả, không tự upload lại file.
Lỗi model khi viết cập nhật sẽ được chờ thử lại, không thay bằng câu trả lời cố định.

Chat “huỷ task đó” khi reply một cập nhật để chọn task chính xác. Nếu nhiều task
đang chạy, AI sẽ hỏi lại; yêu cầu huỷ tất cả có thể huỷ từng task của chính người đó.
Quyền admin server riêng lẻ không cấp quyền mirror. Task, quyền huỷ và thông báo
được tách theo nền tảng; dữ liệu task vẫn giữ trong database khi restart.
