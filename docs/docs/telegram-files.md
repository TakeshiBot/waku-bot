# File trong hội thoại Telegram

AI nhận tên file, loại MIME, dung lượng và ID tin nhắn của file trong tin hiện tại,
tin được reply và ngữ cảnh nhóm. File không có caption vẫn xuất hiện trong prompt.
File như `.vsix`, `.zip`, `.exe` có metadata để bot nhận diện; bot không thực thi
hoặc coi nội dung các file này là đã đọc.

Văn bản được trích xuất từ tin hiện tại hoặc tin được reply trực tiếp:

- TXT, Markdown, CSV/TSV, JSON, XML, HTML, YAML, TOML và các file mã nguồn dạng chữ.
- DOCX: văn bản trong phần thân tài liệu, gồm đoạn văn và ô bảng.
- PDF có lớp văn bản. PDF khóa bằng mật khẩu hoặc chỉ có ảnh được báo là chưa
  trích xuất được chữ; phần này chưa OCR. Xem [tài liệu pypdf](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).

Áp dụng cho chat nhóm, DM, Guest và Business; không cần bật multimodal để đọc chữ.
Guest chỉ dùng file Telegram đưa vào truy vấn và các tin tham chiếu đi kèm.
Business vẫn chỉ trả lời người dùng trong DM thuộc kết nối, bỏ qua bot khác.

Giới hạn mặc định: 10 MiB/file, tối đa 3 file được tải trong một lần chuẩn bị
ngữ cảnh, 12.000 ký tự/file, tổng 24.000 ký tự và tối đa 30 trang PDF. Nội dung bị
cắt, tải lỗi hoặc định dạng không hỗ trợ có trạng thái riêng; metadata vẫn được giữ.
File từ lịch sử nhóm chỉ có metadata, không tự tải lại toàn bộ lịch sử. Các giới
hạn xử lý đã đặt trong code, không cần thêm biến vào `settings.toml`.
