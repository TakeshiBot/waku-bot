# Sticker và emoji trên Telegram

Bot học mô tả sticker do người dùng gửi trong từng nhóm, sau đó chọn sticker
phù hợp với cảm xúc hoặc yêu cầu hiện tại. Đây là bộ nhớ sticker, không phải
huấn luyện lại model AI. Nhóm cần bật AI trả lời và bộ nhớ sticker.

Đặt các khóa này trước mọi mục `[agent_providers.…]` trong `settings.toml`:

```toml
agent_sticker_memory = true
# Điền model đọc ảnh đang có trên provider của bạn.
agent_sticker_description_model = "default/gpt-4o-mini"
```

Bot mặc định dùng chế độ `chat`, học theo mẫu 50%, gửi được sau khi có ít nhất
một sticker đã học, xét tối đa 60 sticker gần nhất và chờ model tối đa 45 giây.
Chu kỳ gợi ý sticker/reaction mặc định là 10/5 lượt. Các thông số này đã được đặt
trong code; không cần thêm vào `settings.toml` khi sử dụng bình thường.

Chế độ `chat` dùng model chat để chọn một sticker từ các mô tả đã lưu trong
nhóm. Không cần API embedding và không yêu cầu model tự sinh vector số.
Chỉ cần chỉnh nâng cao khi có nhu cầu riêng: `agent_sticker_search_candidates`
nhận giá trị từ 1 đến 200, `agent_sticker_memory_sample_rate` từ 0 đến 1 và
`agent_periodic_sticker_interval` / `agent_periodic_reaction_interval` nhận 0 để
tắt gợi ý định kỳ. Đặt các khóa bổ sung trước mọi bảng provider.
Chế độ `embedding` giữ cách tìm bằng vector của base mới và yêu cầu một model
embedding thực sự qua `agent_sticker_embed_model`. Không dùng model chat như MiMo
hoặc Claude làm model embedding. Khi đổi phương pháp/model embedding, dùng một
đường dẫn database sticker riêng để tránh trộn các chỉ mục không tương thích.

Model mô tả sticker phải đọc được ảnh. Bot dùng `agent_sticker_description_model`
nếu có, nếu không dùng `agent_model_multimodal`, rồi tới `agent_model`.
Model chọn sticker dùng `agent_model_small` nếu có; nếu không dùng model
mô tả sticker. Có thể giữ model chat chính riêng và dùng model nhanh hơn
cho việc học/chọn sticker.
Khởi động lại sau khi bật bộ nhớ hoặc đổi chế độ tìm kiếm, model embedding,
số chiều hay đường dẫn database. Model chat/mô tả và các chu kỳ có thể áp dụng
ngay qua nút Lưu trong `/config` của admin bot.

Gửi sticker vào nhóm để học tự động. Tỷ lệ `0.5` lấy mẫu khoảng 50%; khi nhóm
chưa có sticker nào, cơ chế khởi động tăng tỷ lệ này để có dữ liệu ban đầu.
Đặt `1.0` nếu muốn học mọi sticker được hỗ trợ. Bot không học sticker từ tài khoản
bot, sticker động `.tgs`, hoặc sticker video khi máy thiếu FFmpeg.

Admin nhóm có thể reply sticker bằng `/addsticker` để thêm chủ động,
`/delsticker` để bỏ một sticker, hoặc `/clearsticker` để xóa bộ nhớ của nhóm.
Sau khi học thành công, thử gọi: `waku gửi sticker vui vẻ đi`.
Sticker của nhóm khác không được đưa vào danh sách chọn. Cơ chế học/gửi sticker
hiện dành cho nhóm, không phải DM hoặc Telegram Business.

Chu kỳ 10/5 gợi ý AI gửi sticker mỗi lượt thứ 10 và thả reaction mỗi lượt thứ 5,
tính riêng cho từng người trong cuộc trò chuyện. Bot không nhắc lại công cụ đã
gửi thành công trong cùng lượt; AI có thể bỏ qua khi không phù hợp. Emoji trong
câu trả lời thông thường được điều khiển qua `agent_prompt`, không cần embedding.
Các lệnh và tin nhắn bị bỏ qua không tính vào chu kỳ.

Mỗi lần mô tả hoặc chọn sticker bằng chế độ `chat` là một lần gọi AI và được
tính vào quota. Bot chỉ lưu sticker sau khi mô tả thành công; học theo mẫu không
đảm bảo mọi sticker gửi vào nhóm đều được lưu.
