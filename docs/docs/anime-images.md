# Nguồn ảnh anime

Telegram `/seg`, công cụ AI gửi ảnh và lệnh ảnh Discord dùng chung ManyACG và
nekos.best. Không cần thêm cấu hình cho nekos.best.

## ManyACG

URL mặc định: `https://api.manyacg.top/v1`.

- Lấy ngẫu nhiên `/artwork/random` và tìm từ khóa `/artwork/list` không gửi API key.
- Lấy tác phẩm từ link qua `/artwork/fetch` cần `manyacg_api_key`.
- Giá trị key trống vẫn dùng được ảnh ngẫu nhiên; việc lấy từ link là chức năng riêng.

## nekos.best

[API v2](https://docs.nekos.best/getting-started/api-endpoints.html) không cần key,
và [chỉ cung cấp nội dung SFW](https://docs.nekos.best/getting-started/introduction).
Bot đọc danh sách category từ `/endpoints`, chỉ chọn ảnh tĩnh được API công bố;
hiện có `neko`, `waifu`, `husbando`, `kitsune`. GIF không được trộn vào luồng gửi ảnh.

Với ảnh ngẫu nhiên ở chế độ **An Toàn** hoặc **Cả Hai**, bot ưu tiên ManyACG
như base cũ; khi nguồn này lỗi có thể thử nekos.best. **Chỉ R18** dùng ManyACG,
không lấy ảnh SFW từ nekos.best để thay thế yêu cầu R18.

Tìm tên nhân vật/tag dùng luồng của base cũ: gửi nguyên từ khóa vào `/artwork/list`
với `hybrid = manyacg_hybrid_search` ngay từ lần tìm đầu tiên. Bot chọn trong tập
kết quả API trả về như base cũ, không kéo thêm các trang kém liên quan. Không tra AniList,
không tự đổi tên nhân vật hoặc loại kết quả vì tag không chứa nguyên tên người dùng nhập.
Không đổi sang ảnh ngẫu nhiên hoặc nhân vật khác khi không tìm thấy. nekos.best
không có chỉ mục tag nhân vật, nên chỉ dùng cho ảnh ngẫu nhiên/category hoặc
khi chọn nguồn này trực tiếp:

```text
/seg nekos:neko
/seg nekos:waifu
/seg nekos:husbando
/seg nekos:kitsune
/seg Hatsune Miku
/seg Rem r18
```

Trên Discord, nhập cùng từ khóa vào lệnh `/seg`. AI cũng dùng được các từ khóa
này khi gọi công cụ gửi ảnh. Nút nguồn dẫn tới tác phẩm gốc, nút ảnh gốc của
nekos.best dẫn tới file ảnh tương ứng; ảnh ManyACG giữ đường dẫn cũ.

Các request nekos.best có User-Agent nhận diện dự án, có giới hạn nhịp tìm kiếm
chung và tạm ngừng khi API báo 429. Việc bật/tắt ảnh và chọn chế độ trong config
Telegram/Discord vẫn áp dụng cho cả hai nguồn.

`r18`/`nsfw` trong từ khóa chọn ảnh R18, kể cả khi menu đang để **Cả Hai**;
`sfw`/`an toàn` chọn ảnh an toàn. Các dấu này không được gửi như tên nhân vật.
Yêu cầu R18 bị từ chối khi đang ở **An Toàn**, và yêu cầu SFW bị từ chối khi
đang ở **Chỉ R18**. Không nới chế độ của nhóm để đáp ứng một yêu cầu riêng.
Tên ngắn có thể trùng nhiều nhân vật; thêm tên đầy đủ hoặc tên bộ anime để thu hẹp.
Độ khớp của kết quả phụ thuộc vào tìm kiếm của ManyACG, bot không nhận diện nhân vật bằng ảnh.

## Tránh gửi lại ảnh

Lệnh và AI dùng chung lịch sử theo từng chat/kênh, kiểm tra ID ảnh, URL và hash ảnh.
Ưu tiên ảnh chưa gửi, sau đó đến ảnh lâu nhất đã gửi; không gửi lại ngay ảnh cuối cùng.
Các trang khác nhau trong cùng tác phẩm vẫn được chọn nếu nội dung không trùng.
Ảnh đang được gửi được giữ chỗ để album và các yêu cầu đồng thời không chọn trùng.
Bot nhớ tối đa 50 ảnh gần nhất, hết hạn sau 24 giờ không cập nhật. Lịch sử nằm trong bộ nhớ khi không dùng
Redis và được giữ qua lần khởi động lại khi có Redis.

Nếu chỉ có ảnh vừa gửi hoặc ảnh đang chờ gửi, bot báo chưa có ảnh khác thay vì gửi
trùng ngay. Mỗi lần tìm đều hỏi API lại, không khóa tập kết quả trong cache 5 phút.
AI lấy từ khóa theo ngữ cảnh hội thoại như base cũ; không có lớp nhớ tên riêng tự
ghi đè từ khóa của công cụ. `/seg` không kèm từ khóa lấy ảnh ngẫu nhiên.

## Xin ảnh và tạo ảnh AI

Khi trò chuyện trên Telegram hoặc Discord, yêu cầu thông thường như “cho mình
ảnh mèo” dùng nguồn ảnh API có sẵn. Bot chỉ gọi model tạo ảnh khi người gọi
nói rõ muốn tạo/vẽ ảnh, chẳng hạn “waku tạo ảnh mèo”. Sửa ảnh cũng cần yêu cầu
sửa rõ ràng. Việc kiểm tra dựa vào tin nhắn hiện tại của người gọi, không dựa
vào prompt do AI tự viết. Tạo/sửa ảnh cần model tương ứng đã được cấu hình.

Ảnh tạo/sửa được gửi với caption `Tạo bởi waku meow~!`; sau khi gửi thành công,
bot kết thúc lượt đó mà không gửi thêm lời hoàn tất. Ảnh lấy từ API giữ thông
tin tác phẩm và nguồn ảnh như trước.

Công cụ tạo/sửa ảnh AI cũng kiểm tra công tắc ảnh và chế độ An Toàn/Chỉ R18/Cả Hai
trước khi gọi model, rồi kiểm tra lại trước khi gửi. Ở chế độ An Toàn, yêu cầu
R18 bị chặn và prompt yêu cầu kết quả SFW. Đây là kiểm tra yêu cầu và ràng buộc
prompt, chưa phải hệ thống phân loại nội dung ảnh sau khi model tạo.
