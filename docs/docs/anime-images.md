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

Với ảnh ngẫu nhiên ở chế độ **An Toàn** hoặc **Cả Hai**, bot chọn ngẫu nhiên giữa
hai nguồn. Khi một nguồn lỗi, thử nguồn còn lại. **Chỉ R18** dùng ManyACG,
không lấy ảnh SFW từ nekos.best để thay thế yêu cầu R18.

Tìm từ khóa thông thường ưu tiên ManyACG, rồi thử tìm metadata trên nekos.best;
không đổi sang ảnh ngẫu nhiên khi không tìm thấy. Muốn chọn nekos.best trực tiếp:

```text
/seg nekos:neko
/seg nekos:waifu
/seg nekos:husbando
/seg nekos:kitsune
```

Trên Discord, nhập cùng từ khóa vào lệnh `/seg`. AI cũng dùng được các từ khóa
này khi gọi công cụ gửi ảnh. Nút nguồn dẫn tới tác phẩm gốc, nút ảnh gốc của
nekos.best dẫn tới file ảnh tương ứng; ảnh ManyACG giữ đường dẫn cũ.

Các request nekos.best có User-Agent nhận diện dự án, có giới hạn nhịp tìm kiếm
chung và tạm ngừng khi API báo 429. Việc bật/tắt ảnh và chọn chế độ trong config
Telegram/Discord vẫn áp dụng cho cả hai nguồn.

## Xin ảnh và tạo ảnh AI

Khi trò chuyện trên Telegram hoặc Discord, yêu cầu thông thường như “cho mình
ảnh mèo” dùng nguồn ảnh API có sẵn. Bot chỉ gọi model tạo ảnh khi người gọi
nói rõ muốn tạo/vẽ ảnh, chẳng hạn “waku tạo ảnh mèo”. Sửa ảnh cũng cần yêu cầu
sửa rõ ràng. Việc kiểm tra dựa vào tin nhắn hiện tại của người gọi, không dựa
vào prompt do AI tự viết. Tạo/sửa ảnh cần model tương ứng đã được cấu hình.

Ảnh tạo/sửa được gửi với caption `Tạo bởi waku meow~!`; sau khi gửi thành công,
bot kết thúc lượt đó mà không gửi thêm lời hoàn tất. Ảnh lấy từ API giữ thông
tin tác phẩm và nguồn ảnh như trước.
