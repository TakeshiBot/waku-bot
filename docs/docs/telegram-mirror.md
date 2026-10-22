# Gửi tác vụ đến bot Mirror

Admin bot có thể chat với Waku trong DM hoặc nhóm thường, ví dụ:
“waku mirror link này https://example.org/file.zip”. AI tự hiểu yêu cầu,
trả lời trước rồi gửi lệnh vào nhóm Mirror đã cấu hình. Không dùng trong
Business hoặc Guest; admin nhóm thông thường không được cấp quyền này.

Cũng có thể reply một ảnh/file và yêu cầu “waku mirror ảnh này”. AI gọi cùng
tool với ID tin nhắn nguồn; backend lấy đúng attachment, không cần nhận dạng
nội dung ảnh. Với nhóm công khai, Waku dùng trực tiếp link tin nhắn nguồn.
Với DM hoặc nhóm riêng tư khác, Waku sao chép attachment vào nhóm Mirror rồi gửi
link của bản sao cho Mirrorz. File đã ở nhóm Mirror dùng link tại đó, không sao
chép thêm. Bot Mirror không cần tham gia chat nguồn. Nhóm Mirror cần là supergroup;
nội dung được bảo vệ không
được sao chép. Mỗi yêu cầu hiện xử lý một attachment.

## Loại chuyển và tuỳ chọn

AI có thể chọn các lệnh chuyển file sẵn có của Mirrorz theo yêu cầu:

- Mirror/leech thường: `mirror` (`m`), `leech` (`l`).
- qBittorrent: `qbmirror` (`qm`), `qbleech` (`ql`).
- JDownloader: `jdmirror` (`jm`), `jdleech` (`jl`).
- NZB: `nzbmirror` (`nm`), `nzbleech` (`nl`).
- yt-dlp: `ytdl` (`y`), `ytdlleech` (`yl`).
- Sao chép và upload host: `clone` (`cl`), `uphoster` (`up`).

Ví dụ: “waku leech link này rồi giải nén”, “qm hai link này và nén zip”,
“tải video này bằng yt-dlp, chất lượng 720p”. Có thể yêu cầu các cờ xử lý như
`-e`, `-z` (kèm mật khẩu nếu có), đổi tên `-n`, đích upload `-up`, đích leech
`-ud`, chia file `-sp`, dạng tài liệu/media, seed, chuyển đổi, sample, thumbnail,
metadata, bộ lọc clone và các tuỳ chọn riêng của engine. Backend kiểm tra cờ
theo đúng loại lệnh, không gửi tuỳ chọn không được hỗ trợ. yt-dlp mặc định chọn
chất lượng tốt nhất để không mắc ở menu chọn chất lượng; lựa chọn cụ thể của
người yêu cầu được giữ nguyên.

Một lượt nhận tối đa 20 nguồn (link tải, magnet, link Telegram, remote rclone
đã cấu hình hoặc Drive ID). AI nhận yêu cầu một lần, Waku tạo từng lệnh riêng,
giữ liên kết với chat gốc và theo dõi kết quả/huỷ riêng cho từng file. Cùng một
tin yêu cầu không tạo lại task khi gọi lại. Link Telegram dạng khoảng tin nhắn
được tách thành từng nguồn; `clone` giữ nguyên khoảng để dùng cơ chế sao chép
của Mirrorz. Waku thay cơ chế `-b`/`-i` bằng danh sách nguồn được theo dõi này,
không để các task con do Mirrorz sinh ra bị mất dấu. Cờ xử lý áp dụng riêng
cho từng nguồn, không tự gộp nhiều link thành một archive.

Kết quả leech đọc các link file Telegram thực tế trong danh sách của Mirrorz,
không phụ thuộc việc bot đó có dùng nút tải hay không. Mọi lời nhận yêu cầu,
báo đang chạy và báo kết quả vẫn do AI viết. Quyền sử dụng, engine, dịch vụ,
đích upload và các giới hạn phía Mirrorz vẫn theo cấu hình của bot Mirror.

## Cấu hình hai bot

1. Cập nhật source và build lại **Waku**. Có thể giữ Mirrorz nguyên bản:
   Waku đọc tin trạng thái, phản hồi lỗi và kết quả sẵn có của Mirrorz.
2. Thêm cả hai bot vào nhóm Mirror và cho phép gửi tin nhắn. Bật chế độ
   [bot-to-bot trong BotFather](https://core.telegram.org/api/bots/bot-to-bot)
   cho cả hai bot.
3. Trong Waku, đặt hai biến sau trong `settings.toml`, hoặc sửa ở
   `/config → Cấu Hình → Dịch vụ và kết nối` rồi Lưu:

   ```toml
   mirror_group_id = -1003835947769
   mirror_bot_username = "jinmups_bot"
   ```

   Hai biến này áp dụng ngay khi lưu trong menu. Mẫu để `0` và `""` nhằm tắt
   tích hợp khi chưa cấu hình. AI của Waku cần được bật và có provider hợp lệ.

4. Mirrorz dùng cấu hình tải/upload và quyền sử dụng nhóm hiện có. Không cần
   biến `WAKU_BRIDGE_BOT_ID`. Waku phải được Mirrorz cho phép sử dụng lệnh
   như bình thường. Nếu bật kiểm tra yêu cầu mở DM, xác minh token hoặc tham
   gia kênh, các kiểm tra đó vẫn có thể từ chối bot; Waku báo lại lỗi thực tế,
   không tự cấp SUDO hoặc vượt qua kiểm tra của Mirrorz.

## Cách hoạt động

- Lời nhận yêu cầu, báo đang xử lý và báo kết quả đều do AI viết ngắn gọn,
  không dùng câu trả lời mẫu. Khi hoàn tất có link, Waku gắn các nút tải thực tế
  nhận từ Mirrorz vào lời báo kết quả.
- Waku đối chiếu tin trạng thái bằng liên kết tới đúng lệnh gốc, đọc GID,
  tên file và lệnh huỷ. Không ghép task theo tên file hay đoán người yêu cầu.
  Khi muốn huỷ, admin chat tự nhiên với Waku hoặc reply tin cập nhật của task.
  Waku tự gửi yêu cầu huỷ đúng task; chỉ báo đã huỷ sau khi Mirrorz xác nhận.
  Nếu có nhiều task đang chạy, AI hỏi rõ file cần huỷ. GID và lệnh huỷ chỉ dùng
  nội bộ, không hiển thị trong lời trả lời.
- Kết quả và lỗi được đối chiếu qua tin trả lời đúng lệnh gốc; link thực tế
  trong nút của Mirrorz được gắn vào nút tải bên Waku. AI viết lời báo kết quả.
- Nhận sự kiện chỉ từ đúng bot, đúng nhóm và đúng lệnh nguồn. Các sự kiện kỹ
  thuật không được đưa vào chat AI để tránh hai bot trả lời lặp nhau.
- Cùng tin nhắn nguồn chỉ tạo một yêu cầu. Nếu gửi lệnh bị timeout, Waku
  kiểm tra bằng `/status@MirrorBot <ID Waku>`, không tự tạo lại task. Sau 90 giây
  chưa nhận xác nhận, trạng thái là chưa xác nhận, không khẳng định thất bại.
- Ảnh/file cũng được giữ chỗ trong database trước khi sao chép, tránh tạo lại
  bản sao hoặc task khi AI gọi lại tool. Nếu chuẩn bị attachment lỗi, không gửi
  lệnh mirror. Bản sao giữ caption gốc, bỏ các nút của tin nhắn nguồn.
- Khi vision lỗi trước khi trả nội dung, bot dùng model chat dự phòng với phần
  văn bản và metadata attachment; không gửi dữ liệu ảnh cho model chỉ đọc text,
  không giả vờ đã nhìn thấy ảnh. Thời gian chờ model chính được giới hạn tới
  sự kiện nội dung đầu tiên, kể cả khi API đã gửi heartbeat. Sau khi đã trả nội
  dung hoặc tool call, không chạy lại cả lượt để tránh lặp thao tác.
- Nếu API AI lỗi, thông báo được giữ để thử lại; không gửi câu trả lời mẫu.
  Nếu Telegram đã nhận tin nhưng kết quả gửi không rõ, không tự gửi lại để
  tránh lặp thông báo.

## Addon Mirrorz tuỳ chọn

Nếu cần biên nhận trực tiếp theo request ID và phục hồi kết quả cuối sau khi
mất kết nối, có thể áp dụng [patch tuỳ chọn](mirrorz-notifications.patch) vào
source Mirrorz. Source Mirrorz trong workspace được giữ nguyên bản. Phần xử lý chỉ thêm
helper thông báo, vài hook vòng đời task và handler `/wakustatus`; không sửa
engine tải/upload, quyền, config hoặc kiểm tra trước task. Không bắt buộc cài
addon cho luồng gửi và nhận tin thông thường.

Khi nhận được sự kiện addon, Waku tự dùng `/wakustatus` để phục hồi biên nhận.
Patch thêm volume Docker `mirror_bridge_data`; giữ volume này khi cập nhật.
Patch cũng thêm hướng dẫn `docs/WAKU_BRIDGE.md` vào Mirrorz. Nếu không cài addon và Waku bỏ
lỡ tin kết quả cuối hoặc tin đó đã bị xoá, `/status` không thể khôi phục kết
quả của task đã kết thúc; Waku không tự khẳng định task thành công hay gửi lại.
Các mẫu tin không nhận diện được, hoặc kết quả bị ẩn link, được giữ ở trạng
thái chưa rõ thay vì đoán. Addon giúp tránh phụ thuộc vào định dạng tin nhắn.

Nếu chọn cài addon, kiểm tra rồi áp dụng từ máy có cả hai source:

```bash
git -C /path/to/mirrorz apply --check /path/to/waku-bot/docs/docs/mirrorz-notifications.patch
git -C /path/to/mirrorz apply /path/to/waku-bot/docs/docs/mirrorz-notifications.patch
```

Sau đó build lại Mirrorz. Bỏ qua bước này nếu dùng Mirrorz nguyên bản.
