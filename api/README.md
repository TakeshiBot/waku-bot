# Waku API

## Video

Web và bot upload nhận video **MP4, MOV, WebM tối đa 20 MiB/file**, dùng chung
kho R2, chống trùng và hạn mức Data 10 GB với ảnh. Trên web, AI gắn tag video
bằng **một khung hình** (khoảng 25% thời lượng nếu đọc được), không xem toàn bộ
video hoặc nghe âm thanh. Web trích JPEG tối đa 768 px rồi lưu trên R2 để phiên
AI và các lượt chạy lại tiếp tục trên máy chủ dù đóng trình duyệt. Bật AI khi
upload hoặc dùng nút AI riêng/chọn nhiều file. Phân loại chỉ dựa vào khung đã
xem; cần tự kiểm tra Safe/R18 cho toàn video. Codec không được trình duyệt hỗ
trợ thì vẫn giữ video đã upload và nhập thông tin thủ công. Video upload qua
Telegram hoặc API chưa có khung hình cũng có thể mở trên web để gắn tag.
Trình xem web phát video và hỗ trợ tua bằng HTTP byte ranges. Việc phát MOV
còn phụ thuộc codec và trình duyệt; MP4 H.264/AAC phù hợp nhất để gửi Telegram.

Danh sách nhận `media_types=image`, `media_types=video` hoặc cả hai (mặc định).
API random mặc định chỉ lấy ảnh; thêm `media_type=video` để lấy **một video**,
kể cả khi `count` lớn hơn 1. Ví dụ:
`/v1/images/random?media_type=video&rating=mixed&tags=maid`.
Tag kết hợp AND, giữ nguyên phân loại và kiểu ảnh; không trả video ngẫu nhiên
khác tag khi tìm không có. Kết quả có `media_type`, `mime_type`, `size` và URL.

Bot Telegram/Discord: `/vseg`, `/vseg maid`, `/vseg r18, maid` (hoặc `r18 maid`).
Discord còn nhận `!vseg maid`. Chat AI có tool gửi video riêng và trả lời tự
nhiên trước khi gửi file. Video R18 gửi với spoiler, giới hạn phân loại vẫn
theo cấu hình cuộc chat. Nếu giới hạn đính kèm của kênh Discord thấp hơn 20 MiB,
bot báo rõ thay vì giả vờ đã gửi thành công.
Video catalog gửi Telegram dùng khung tối đa 240 × 426 px, giữ nguyên tỷ lệ;
caption rút gọn bằng `…` để không kéo rộng khung. Đọc metadata bằng `ffprobe`, không
mã hoá lại hay giảm chất lượng file.
Bot gửi tin chờ trước khi tải video, rồi thay chính tin đó bằng video; nếu lỗi,
tin chờ được xoá để không lưu trạng thái đang tải mãi.
Caption ảnh lẻ cũng rút gọn; caption album giữ đầy đủ, không ngắt dòng thủ công.

Kho ảnh riêng chạy trên Cloudflare Workers + R2 + D1. Bot Telegram nhận ảnh hoặc link ảnh, lưu vào R2, trả link trực tiếp. Source nằm trong thư mục `api/` trên nhánh chính `v2` của repo Waku. API ảnh deploy độc lập lên Cloudflare, không chạy trong container bot; không cần VPS. Có thể thêm API vision để tự gắn tag, hoặc dùng hoàn toàn thủ công.

## LibSQL trên VPS

Bản đang chạy dùng một bộ Compose riêng trong `/home/ubuntu/waku-api-db`: `libsql` lưu dữ liệu và `tunnel` nối Worker đến database qua Cloudflare Tunnel. Cổng database chỉ lắng nghe ở `127.0.0.1:18783`; kết nối qua Tunnel cần JWT. Hai container có giới hạn tài nguyên riêng, không thuộc Compose của bot Waku. Không cần sửa hoặc restart bot, Nginx hay các dịch vụ khác khi quản lý bộ này.

```bash
cd /home/ubuntu/waku-api-db
docker compose -p waku-api-db ps
docker compose -p waku-api-db logs --tail 100
systemctl status waku-api-db-backup.timer
sudo ls -lh /home/ubuntu/waku-api-db/backups
```

Timer tạo bản sao SQLite nhất quán lúc **00:00 và 12:00 giờ Việt Nam** mỗi ngày,
kiểm tra tính toàn vẹn rồi gửi thẳng bản nén `.sqlite3.gz` qua bot upload vào DM
của chủ bot. Sau khi Telegram xác nhận đúng file và dung lượng, VPS giữ **2 bản
gần nhất** tại `backups/`. Gửi lỗi thì giữ bản chờ cùng các bản cũ, thử lại sau
5 phút và dùng lại bản chờ để tránh sinh thêm file. VPS có thể tạm có 3 bản khi
gửi lỗi. Service giới hạn 256 MiB RAM, 25% CPU và tối đa 15 phút mỗi lượt;
riêng truyền Telegram tối đa 10 phút để chịu được đường truyền chậm.
Ảnh/video gốc vẫn ở R2, không nằm trong bản sao database. D1 cũ không đồng bộ
thay đổi phát sinh sau khi chuyển.

`backup-telegram.json` trong `/home/ubuntu/waku-api-db/` có quyền `0600`, chứa
`bot_token` của bot upload và `chat_id` nhận backup.
Cần `curl` và Python 3. Copy script
`vps/backup.py` vào thư mục DB, cài hai unit trong `vps/` vào `/etc/systemd/system/`,
rồi chạy `sudo systemctl daemon-reload` và
`sudo systemctl enable --now waku-api-db-backup.timer`. Khi sửa lịch đã tồn tại,
restart timer. Lịch dùng múi giờ rõ ràng, không phụ thuộc timezone của VPS.
`backup-last-sent.json` lưu xác nhận Telegram; `backup-pending.json` ghi bản chờ.
Backup mới không mã hoá, không cần khóa: tải file `.sqlite3.gz` từ Telegram rồi
giải nén trực tiếp hoặc khôi phục vào **file SQLite mới**:

```bash
python3 api/vps/restore-backup.py backup.sqlite3.gz restored.sqlite3
```

Lệnh kiểm tra SQLite trước khi tạo file đích và từ chối ghi đè file đã có.
Riêng các bản `.fernet.bin` đã gửi trước đây vẫn cần thêm
`--key-file api/.backups/backup-telegram.key` và thư viện `cryptography` để khôi phục.
Kiểm tra luồng backup bằng `python3 api/vps/check_backup.py` trên Linux; chỉ dùng
database tạm và Telegram giả, không chạm dữ liệu thật hoặc gửi tin nhắn.

`api/.secrets.json` chứa `LIBSQL_URL`, `LIBSQL_AUTH_TOKEN` và `API_KEY`; giữ bí mật và sao lưu an toàn, vì `API_KEY` còn dùng để giải mã key AI đã lưu. `private.pem` và `tunnel-token` trên VPS cũng là thông tin nhạy cảm. Không mở cổng database ra Internet và không lưu các file này trong Git.

Khi không dùng domain riêng, nối DB qua Workers VPC: tạo Tunnel trên cùng account với Worker, tạo HTTP VPC Service trỏ đến `127.0.0.1:18783` trên VPS, rồi thêm binding `LIBSQL_SERVICE` trong `vpc_services` của `wrangler.local.json`. Đặt `LIBSQL_URL=http://127.0.0.1:18783`; Worker dùng binding riêng để gửi yêu cầu, không gọi địa chỉ loopback của Worker. Script deploy giữ lại binding này. Web và API vẫn dùng địa chỉ `workers.dev`; không cần mở cổng DB hoặc chuyển zone DNS.

Các công cụ sao lưu/chuyển D1 chạy bằng Node ngoài Worker không có VPC binding. Chạy chúng ngay trên VPS, hoặc mở SSH forwarding `ssh -N -L 18783:127.0.0.1:18783 ubuntu@VPS` rồi chạy công cụ trên máy local trong lúc giữ phiên SSH. Không đổi `LIBSQL_URL` sang endpoint công khai chỉ để chạy công cụ.

Nếu file credentials có nhiều account, các script chọn token khớp `account_id` trong `wrangler.local.json`; có thể chỉ định rõ bằng `--account` hoặc `CLOUDFLARE_ACCOUNT_ID`. Deploy dừng khi account khác bản đang cấu hình, tránh vô tình nối Worker mới với tài nguyên của account cũ. Chuyển account cần sao chép R2, giữ khóa giải mã/API key và kiểm tra DB trước khi đổi cấu hình, webhook và URL dịch vụ trong bot.

Khi cần khôi phục, trước tiên tạm dừng ghi và giữ nguyên bản hiện tại. Giải nén bản sao vào một nơi riêng, kiểm tra SQLite, rồi dùng `sqlite3` xuất SQL hoặc Python `sqlite3.Connection.iterdump()`. Script `scripts/import-libsql.mjs` chỉ nhập SQL vào một libSQL **trống**, yêu cầu `LIBSQL_URL` và `LIBSQL_AUTH_TOKEN` qua biến môi trường, và kiểm tra khóa ngoại. Đối chiếu nội dung trước khi chuyển Worker sang server khôi phục; không ghi đè trực tiếp file `data` đang chạy hoặc quay về D1 cũ mà không đồng bộ dữ liệu.

### Chuẩn bị hoặc chuyển về D1

Chạy trong thư mục `api`. Lệnh mặc định chỉ xuất một bản chụp nhất quán từ libSQL và lập danh sách kiểm tra nội dung; không đổi Worker và không tạo hay ghi vào D1:

```bash
npm run db:d1 -- --credentials ../api.txt
```

Khi thực sự muốn chuyển về D1:

```bash
npm run db:d1 -- --credentials ../api.txt --apply
```

Chế độ `--apply` kiểm tra hạn mức đọc/ghi D1 trước, tạo một database D1 mới, đưa riêng kho ảnh vào bảo trì và đợi các tác vụ đang chạy hoàn tất. Sau đó công cụ xuất bản chụp mới, nhập dữ liệu, đối chiếu SHA-256 nội dung từng bảng cùng khóa ngoại, rồi kiểm tra API D1 trong lúc vẫn khóa ghi. Chỉ khi các bước đạt mới mở API với D1. URL ảnh, R2, API key, key AI đã mã hóa và UUID được giữ nguyên. Bot Waku và các dịch vụ khác trên VPS không bị khởi động lại.

Nếu D1 đang được cấu hình là bản cũ không có ứng dụng nào khác ghi vào, có thể thêm `--reuse-existing` để sao lưu D1 đó rồi đồng bộ các dòng thay đổi, giảm lượt ghi. Tùy chọn này sửa database D1 cũ; không dùng khi database đó còn phục vụ ứng dụng khác. Cả bản chụp libSQL và bản sao D1 được lưu riêng trong `api/.backups` và chứa dữ liệu nhạy cảm.

D1 cũ không tự đồng bộ với VPS. Công cụ không chuyển về một bản D1 cũ khi libSQL mất kết nối. Hạn mức D1 Free áp dụng cho cả account, nên tạo database mới không vượt qua được quota; import cũng dùng rows written và đối chiếu dùng rows read. Nếu hết quota trước khi kích hoạt, công cụ dừng và mở lại backend VPS. Nếu kết nối bị gián đoạn ngay lúc kích hoạt, cấu hình local giữ D1 đã đối chiếu và công cụ yêu cầu kiểm tra/triển khai lại cấu hình đó, thay vì quay lại dữ liệu VPS có thể đã cũ.

Khi đang dùng D1 và muốn chuyển lại libSQL trên VPS đã cấu hình, khởi động database và Tunnel riêng trước, rồi chạy `npm run db:vps -- --credentials ../api.txt --apply`. Công cụ sao lưu bản VPS cũ, tạm ngừng ghi, sao lưu và đồng bộ D1, đối chiếu các bảng trước khi kích hoạt VPS. D1 được giữ lại nhưng sau khi chuyển sẽ không còn đồng bộ; không đổi backend thủ công để quay lại D1.

Bản SQL, manifest, cấu hình cũ và secrets được lưu riêng trong `api/.backups/` và bị Git bỏ qua. Các file này chứa dữ liệu riêng và cần bảo quản an toàn. File `.db-move.lock` ngăn hai lần chuyển chạy cùng lúc; chỉ xóa lock còn sót sau khi xác nhận tiến trình chuyển trước đã dừng.

`DATABASE_BACKEND` trong cấu hình triển khai xác định backend đang dùng. Khi chọn D1, credentials libSQL được giữ để phục hồi có kiểm soát và không được gọi trong luồng bình thường. Script deploy luôn giữ đúng database D1 đã chọn, không tự nối lại database tên `waku-api` cũ. Thay đổi riêng biến backend không phải quy trình đồng bộ dữ liệu; hãy dùng công cụ chuyển ở trên.

## Dùng bot

Mở DM của bot upload bằng tài khoản đã cấp quyền. Gửi một hoặc nhiều ảnh; mỗi ảnh được xử lý riêng. Gửi dạng **file ảnh** nếu muốn giữ nguyên chất lượng; ảnh gửi dạng Photo được lưu theo bản Telegram đã nén.

Có thể chọn cả đợt nhiều ảnh/file, kể cả khi Telegram tách thành nhiều album. Bot lưu từng update vào hàng đợi D1; toàn hệ thống giới hạn **2 upload đồng thời**, còn AI giới hạn **2 ảnh/API đang bật**, không phải 2 lượt cho mỗi Worker. Ví dụ bật 3 API thì xử lý tối đa 6 ảnh AI song song. Phản hồi mới trong cùng chat được giãn ít nhất khoảng 1,1 giây để tránh gửi dồn. Mỗi file được lưu độc lập, file lỗi không chặn cả đợt. Caption chỉ đi cùng file mà Telegram gắn caption đó; muốn áp dụng tag chung cho cả đợt, dùng trang web bên dưới.

## Trang quản lý trên web

Mở URL gốc của Worker, ví dụ `https://waku-api.your-account.workers.dev/`. Trang được phục vụ ngay trên Worker hiện có, không cần thêm hosting, domain hoặc dịch vụ khác.

Đăng nhập bằng `API_KEY` trong file local `api/.secrets.json` (không phải key AI hoặc token Telegram). Key không được ghi vào HTML, URL hoặc localStorage. Sau khi xác thực, trình duyệt dùng cookie HttpOnly/Secure, hiệu lực 8 giờ. Đăng xuất để xóa phiên trên trình duyệt.

**API Keys** ở sidebar tạo key dịch vụ để nối bot hoặc ứng dụng lấy ảnh. Key đầy đủ chỉ hiện lúc tạo; lưu lại trước khi đóng bảng. Có thể đặt tên, đổi tên, bật/tắt và xóa để thu hồi. D1 chỉ lưu SHA-256 của key, không lưu lại key gốc. Key dịch vụ chỉ được GET `/v1/images/random`, không đăng nhập quản trị, đọc cấu hình AI, upload hoặc xóa dữ liệu. URL `/media/UUID` vẫn là link chia sẻ ảnh để Telegram/Discord tải được file.

Với ảnh đã phân loại, API còn trả `telegram_file_url`: mở link này trong Telegram để bot upload gửi file gốc trong DM. Người nhận không có quyền upload hay quản lý thư viện. Script deploy tự lấy username từ bot Telegram; không cần thêm config thủ công. Telegram không hỗ trợ spoiler cho `sendDocument`, vì vậy file gốc không có bảo đảm làm mờ như ảnh gửi bằng `sendPhoto`.

Gọi ảnh: `GET https://waku-api.takeshi7502.workers.dev/v1/images/random?rating=safe&count=3&tags=maid` với header `Authorization: Bearer <key dịch vụ>`. Hỗ trợ `rating=safe|r18|mixed`, `image_type=anime|real` (tùy chọn), `count=1..10`, nhiều tag ngăn bằng dấu phẩy (khớp tất cả), `exclude` chứa ID cần tránh. Các bộ lọc kết hợp AND; chỉ ảnh đủ tên, tag, phân loại và kiểu ảnh mới vào API random.

Trong bot Waku, điền `waku_api_url = "https://waku-api.takeshi7502.workers.dev/v1"`, `waku_api_key` bằng key dịch vụ, bật `waku_api_enabled`. `/config → Service` có công tắc riêng ManyACG/Waku API, mỗi công tắc nằm trên hàng URL/Key tương ứng. Bật cả hai để chọn nguồn ngẫu nhiên, bật nguồn nào chỉ dùng nguồn đó; tắt cả hai không gọi nguồn dự phòng ngầm. Lệnh ảnh báo chưa cấu hình dịch vụ; tool AI trả trạng thái cho model tự diễn đạt. Telegram và Discord cùng dùng bộ chọn nguồn, bộ lọc Safe/R18, metadata caption và chống lặp hiện có.

- Chọn/kéo thả nhiều ảnh, hoặc dán nhiều URL ảnh trực tiếp (mỗi dòng một link).
- Chọn tag chung, phân loại và bật/tắt AI cho đợt upload; mặc định dùng AI. Các lựa chọn này được chốt khi bấm bắt đầu.
- Trình duyệt tải tối đa **2 file mỗi lượt**. Có tiến độ từng file, tạm dừng sau các file đang chạy, bỏ file chờ và thử lại mục lỗi. Khi máy chủ bận, file sẽ chờ rồi thử lại.
- Giữ tab mở trong lúc upload. Sau khi ảnh được lưu, AI chạy nền và tiếp tục dù đóng trang; các file vẫn còn chờ ở trình duyệt chưa được gửi lên máy chủ.
- Thư viện có tìm theo tên/tag, lọc An toàn/R18/chưa phân loại, phân trang, sửa tên/tag/nguồn, đổi phân loại, chạy lại AI, sao chép link và xóa ảnh.

Khi mở nhiều trang ảnh, cập nhật nền dùng `GET /v1/images/changes?since=<revision>` để lấy riêng ảnh thay đổi, thay vì đọc lại toàn bộ danh sách. Mỗi lượt tối đa 100 ID; `more` cho biết còn trang tiếp theo và `revision` là mốc tiếp tục. `ids` còn chứa ảnh đã xoá/không còn khớp bộ lọc để trình duyệt loại khỏi danh sách. Bộ lọc giống API danh sách; `window` có thể dùng cursor cuối trang đã mở để không chèn ảnh cũ chưa tải. Thay đổi được ghi cùng transaction với ảnh/trạng thái AI, dùng chung trên D1 và libSQL. Ảnh upload hoàn tất chỉ chèn ngay khi khớp tìm kiếm, tab, tag và kiểu ảnh hiện tại.

Metadata trả thêm `metadata_version`. Web gửi lại phiên bản khi PATCH; nếu ảnh đã đổi ở thiết bị khác hoặc bởi AI, API trả HTTP 409 `metadata_conflict` và giữ nguyên dữ liệu đã lưu. Form vẫn mở và giữ nội dung chưa lưu để người dùng đối chiếu, không tự ghi đè. Ứng dụng quản trị tự viết cũng nên gửi `metadata_version` đã đọc khi sửa ảnh.
- Kho ảnh, Dung lượng và Đang xử lý nằm đầu sidebar; một đường phân cách tách chúng khỏi bốn mục phân loại kèm số ảnh. Thumbnail nhỏ, cao bằng nhau, nhóm theo ngày tải lên (UTC+7); desktop đổi chiều rộng theo tỷ lệ ảnh, mobile dùng ba cột. Giữ tên/tag dưới mỗi ảnh. Mỗi lần mở hoặc bấm Xem thêm chỉ tải một đợt nhỏ (45 ảnh desktop lớn, 30 tablet, 15 mobile).
- Ngày tải lên hiển thị cạnh thanh cuộn gốc bên phải và cập nhật theo vị trí cuộn, không có thanh timeline riêng hay request timeline khi mở thư viện. Các ảnh trong hàng căn trái, cách đều. Ba ô Kho ảnh/Dung lượng/Đang xử lý chỉ hiển thị thông tin, không có thao tác bấm. Nút upload mở bảng nổi ngay dưới nó trên cả desktop/mobile; thu bảng vẫn tiếp tục tải file.
- Bảng xem ảnh hỗ trợ phím ←/→ theo thứ tự của tab hiện tại; chỉ tải trang tiếp theo khi đến cuối danh sách đã mở. Phím mũi tên trong ô nhập vẫn dùng để sửa nội dung. Desktop có mũi tên lùi/tiếp ngoài bảng, chiều cao theo nội dung chi tiết (cuộn khi vượt màn hình), Phân loại/Kiểu ảnh cùng hàng và thao tác sao chép/xóa bằng icon. Mobile ẩn mũi tên, mặc định chỉ xem ảnh với ba icon sao chép/sửa/xóa; nút sửa mở bảng trượt từ dưới đè lên ảnh, không đổi khung ảnh. Có thể vuốt ngang trên ảnh để lùi/tiếp. Nhãn kiểu `real` thống nhất là **Người thật** ở bộ lọc, upload và các bảng sửa.
- API trả thêm `width`/`height`, lưu trong bảng `image_dimensions` độc lập với AI/phân loại. Ảnh mới ghi kích thước khi upload; ảnh cũ đọc header có giới hạn theo trang đang mở (tối đa hai lượt đồng thời), sau đó lưu để không đọc lại. Bố cục được tính từ metadata trước khi tải thumbnail; file thiếu/hỏng không chặn cả thư viện và dùng kích thước dự phòng nếu chưa xác định được.
- Ảnh R18 được làm mờ trong thư viện, có tùy chọn hiện ảnh; mở chi tiết để xem và chỉnh sửa.
- Web và bot Telegram dùng chung dữ liệu, không có hai kho ảnh riêng.

Trên PC, lưới ảnh hiển thị 6 cột; màn hình nhỏ tự giảm cột. Mỗi ảnh có checkbox, **Chọn tất cả** lấy toàn bộ kết quả trong tab/bộ lọc hiện tại, kể cả trang chưa tải. **Bỏ chọn** xóa lựa chọn; **Xóa** hỏi lại tổng số ảnh trước khi xóa hàng loạt.

Nút **Sửa** cạnh Xóa mở popup sửa hàng loạt **Phân loại** và **Kiểu ảnh**. Mỗi mục mặc định **Giữ nguyên**, nên có thể chỉ sửa kiểu ảnh bị chọn nhầm lúc upload. Chọn giá trị rồi bấm Lưu thay đổi. Thao tác áp dụng cho các ảnh đã chọn, kể cả trang chưa tải, gửi tối đa 100 ID/request; giữ tên, tag và nguồn. Có thể thử lại các ảnh còn lỗi nếu một đợt chưa hoàn tất.

**Lọc** nằm cùng hàng với Chọn tất cả. Trên máy tính rê chuột vào để mở, giữ bảng khi di chuyển bên trong và thu lại khi rời ra; trên điện thoại bấm để mở/đóng. Có thể dùng bàn phím và Escape. Logo Waku được dùng ở sidebar, trang đăng nhập và biểu tượng tab.

Nút **Hiện R18 / Ẩn R18** nằm ngay cạnh Lọc, thay cho checkbox ở tiêu đề thư viện. Khung lọc cố định 252 × 336 px, giãn dòng gọn và không cuộn; tag chia thành trang 5 mục, có ô tìm kiếm. Chuyển trang tag không gọi API thêm và giữ các tag đã tích chọn. Khi thiếu chỗ ở dưới, bảng lọc mở lên trên.

**API & Model** nằm trên nút Đăng xuất ở sidebar (biểu tượng bánh răng trên điện thoại). Thêm/sửa/xóa kết nối bằng tên, Base URL OpenAI-compatible và key; mỗi API lưu được nhiều Model ID. Checkbox bật/tắt từng API độc lập; bấm tên API để xem model mà không đổi trạng thái bật. Mỗi API có một model vision được chọn bằng radio và được ghi nhớ riêng, kể cả khi tắt. Có thể bật nhiều API để chia hàng đợi song song; chọn model của API đang tắt không tự bật API đó. Thêm tối đa 30 API và 100 model/API. Cấu hình hiện có được nhập thành **API Waku** lần đầu mở mục này.

Các API vision dùng chung hướng dẫn nhận diện và phân loại. Khi nhận ra chắc chắn nhân vật anime hoặc nhân vật được cosplay, AI thêm tag tên chuẩn và tên ngắn hữu ích (ví dụ `tokisaki_kurumi`, `kurumi`), kèm tác phẩm nếu biết chắc (`date_a_live`). Không đủ bằng chứng thì bỏ tag danh tính, vẫn tạo tag ngoại hình và phân loại; không đoán tên chỉ từ màu tóc, tên file hoặc URL. Tag cosplay chỉ nhận diện nhân vật hư cấu, không nhận diện người thật trong ảnh.

An toàn/R18 là bộ lọc nội dung của thư viện: ảnh thường, cosplay, thời trang và đồ bơi không có nội dung tình dục rõ ràng thuộc An toàn. Lộ đùi, chân/bàn chân, tất mỏng, váy ngắn, ngồi trên giường hoặc góc chụp thấp/đưa chân về phía máy ảnh tự chúng chưa đủ để xếp R18; không suy diễn vùng cơ thể bị quần áo che hay nằm ngoài khung ảnh. R18 cần bằng chứng nhìn thấy như khoả thân nhạy cảm, hoạt động tình dục hoặc cách thể hiện khiêu dâm rõ ràng; không chỉ dựa vào trang phục, tác giả, tag hay nhãn cũ. Cùng tiêu chí cho ảnh thật và ảnh vẽ. Nếu không xem được hoặc không đủ bằng chứng để phân loại, lượt AI báo lỗi thay vì đoán. Hướng dẫn áp dụng cho lượt AI tiếp theo; ảnh cũ không tự chạy lại và phân loại đã lưu vẫn được giữ khi chạy lại AI.

Bảng API/Model giữ kích thước cố định theo màn hình, cột model gọn hơn; desktop chỉ danh sách API/model cuộn riêng khi dài. Dưới tên API hiển thị model được chọn, phần trên chỉ tổng hợp số API đang bật. Checkbox bật/tắt độc lập với API đang xem; chỉ API đang xem được tô nổi bật. Ô thêm/sửa model và nút Thêm/Lưu nằm cùng hàng, ghim ở đáy cột. Trên điện thoại dùng hộp chọn API cùng checkbox bật/tắt để dành chỗ cho danh sách model; không phải cuộn toàn bảng để đến ô thêm model. Form sửa kết nối vẫn cuộn riêng nếu màn hình thấp. Bấm nền ngoài bảng hoặc Escape để đóng.

Các thao tác thêm/sửa/xoá dùng icon có chú thích. Mỗi model có icon **Test vision** (bình thí nghiệm) trước Sửa/Xoá. Test gọi đúng API/key/model đã lưu, kể cả API đang tắt hoặc model chưa được chọn; không tự bật API hay đổi model. Hệ thống tạo một ảnh PNG nhỏ có bốn ô màu ngẫu nhiên rồi đối chiếu câu trả lời; chỉ trả thành công khi đọc đúng cả bốn ô. Đây là xác nhận trên ảnh mẫu, không bảo đảm mọi ảnh đều đọc được. Nút có icon xoay trong lúc chờ; kết quả hiện bằng thông báo, màu icon và chú thích khi trỏ vào. Hết 10 giây hoặc lỗi HTTP/định dạng thì báo chưa xác nhận được, không tự thử lại và không đưa ảnh mẫu vào thư viện. Test dùng chung hai slot của API với việc gắn tag; nếu đang kín lượt thì yêu cầu chờ rồi thử lại. API quản trị: `POST /v1/ai/models/<id>/test` với body `{}`.

Lựa chọn được lưu trong D1 và dùng chung cho web/bot upload Telegram, không cần deploy. Lượt AI đang chạy tiếp tục với cấu hình đã lấy; các lượt kế tiếp đọc lựa chọn mới. Xóa model đang dùng sẽ chọn model còn lại của API nếu có; xóa/tắt một API không tắt các API khác. Nếu không còn API có model đang bật, upload thủ công vẫn hoạt động và AI chờ đến khi có kết nối. Migration từ cấu hình cũ chỉ bật API đã được chọn trước đó, giữ nguyên các API khác ở trạng thái tắt.

Hàng đợi đặt chỗ API và ảnh trong cùng transaction D1 để tránh một ảnh chạy đồng thời ở nhiều API. Mỗi API có 2 slot riêng, chọn slot ít dùng trước; không còn trần 2 lượt AI cho toàn hệ thống. Mỗi ảnh chạy trong invocation riêng, chia fan-out thành nhóm để giữ giới hạn Worker/D1. Nếu ACK gọi Worker bị thất lạc, giữ lease thay vì lập tức gửi lại; Cron khôi phục khi lease hết hạn. Kết quả chỉ được ghi nếu lượt chạy vẫn sở hữu lease và metadata chưa bị sửa tay.

Ảnh lỗi chuyển sang failed ngay trong lượt hiện tại. Phiên chung quản lý lịch nghỉ từng API và tạo lượt chỉ gồm ảnh lỗi sau khi lượt trước kết thúc; chi tiết ở dưới. Sửa kết nối/model hoặc tắt/bật API chủ động đặt lại trạng thái nghỉ; phản hồi từ cấu hình cũ không ghi đè trạng thái cấu hình mới.

Key nhà cung cấp được mã hóa AES-GCM trước khi lưu D1, không trả lại qua API và không lưu ở localStorage. Khi sửa API, để trống key để giữ key cũ. Khóa mã hóa được dẫn xuất từ `API_KEY` quản trị: nếu thay `API_KEY`, cần nhập lại các key nhà cung cấp đã lưu. Sau khi quản lý API trên web, danh sách D1 là nguồn cấu hình chính; deploy lại secret AI không ghi đè lựa chọn này.

Khi bắt đầu upload, web chuyển sang **Chờ phân loại**; chỉ ảnh đã lưu thành công mới xuất hiện trong thư viện. Chỉ vào An toàn/R18 khi ảnh có đủ **tên không trống, ít nhất một tag, phân loại safe/r18, kiểu anime/real**. Thiếu bất kỳ mục nào thì vẫn ở Chờ phân loại, kể cả đã chọn sẵn safe/r18. Link nguồn không bắt buộc. Nhập tay đủ bốn mục cũng được chấp nhận; chạy AI lại không làm ảnh đã đủ thông tin quay về tab chờ. Danh sách, số đếm tab và trường `classified` trong API dùng cùng điều kiện. Có thể mở ảnh thiếu thông tin để chạy AI hoặc chọn nhiều ảnh ở tab này rồi bấm **AI gắn tag**.

Thanh upload đếm **ảnh được API xác nhận đã lưu / tổng ảnh của đợt**, không cộng file lỗi vào số đã lưu. Upload vẫn thuộc thiết bị đang chọn file. Phiên AI và bộ đếm từng lượt được lưu trong database, dùng chung giữa các trình duyệt/thiết bị đã đăng nhập. Mở web ở máy khác sẽ thấy đúng phiên đang chạy; đóng toàn bộ trình duyệt không dừng AI. Upload xong không đồng nghĩa AI đã hoàn tất.

Thanh AI có **Tạm dừng / Tiếp tục**, **Huỷ**, **Chạy lại lỗi**. Các nút điều khiển phiên chung trên máy chủ; dòng trạng thái nằm dưới các nút, căn phải trên desktop. Tạm dừng vẫn cho ảnh đang gọi API hoàn tất, nhưng ngừng nhận ảnh mới và giữ lịch thử lại. Huỷ không xoá ảnh/tag đã lưu, vô hiệu hoá kết quả đến muộn và dừng lịch tự chạy lại của phiên. Chạy lại lỗi tạo phiên mới chỉ gồm ảnh lỗi/đã huỷ còn thiếu phân loại, bắt đầu từ 0/N và đặt lại trạng thái nghỉ của API cho phiên mới.

Thanh tiến trình chỉ hiện khi phiên đang chuẩn bị, chạy, tạm dừng hoặc đã hoàn thành và đang đếm ngược để thử lại ảnh lỗi. Desktop đặt nút bên phải; mobile đặt bên dưới. Số đếm lấy từ phiên hiện tại trên máy chủ, không tính lịch sử cả kho. Bảng xem ảnh có thể gắn tag ngay tại chỗ: giữ ảnh mở, hiện hiệu ứng chờ và điền kết quả mà không ghi đè nội dung đang sửa chưa lưu. Tác vụ gắn tag riêng này không tạo thanh tiến trình hàng loạt; nếu đã có phiên hàng loạt thì ảnh được xử lý cùng hàng đợi đó. Trong chế độ chọn ảnh, giữ Ctrl và click để mở xem mà không đổi lựa chọn.

Mỗi ảnh có timeout **10 giây**, gồm đọc ảnh từ R2, gọi API và đọc nội dung trả về. Ảnh được gửi dạng data URI để gateway không phải tải CDN. Mỗi ảnh gọi API một lần trong một phiên; khi hết việc hoặc toàn bộ API đã dừng, phiên chuyển thành **hoàn thành**. Nếu còn ảnh lỗi, máy chủ đợi ít nhất **5 phút** rồi tạo **phiên mới**, chỉ gồm ảnh lỗi còn thiếu thông tin: ví dụ 10/15 thành công thì phiên sau bắt đầu 0/5. Toàn bộ API đang bật được khởi tạo lại trạng thái trong phiên mới. Ảnh đã đủ thông tin hoặc không còn file không tự chạy lại. Chu kỳ 5 phút tiếp tục khi còn ảnh lỗi, trừ khi người dùng tạm dừng/huỷ; không còn giới hạn số lượt cũ. Lease 20 giây loại bỏ kết quả từ Worker đã mất quyền xử lý.

Mỗi API đếm riêng theo kết quả trả về: **3 lỗi liên tiếp → nghỉ ít nhất 30 giây**; quay lại mà thêm **3 lỗi liên tiếp → nghỉ ít nhất 60 giây**; tiếp tục thêm **3 lỗi liên tiếp → dừng API ngay trong phiên**, không chờ thêm. Bất kỳ ảnh nào thành công đều đặt số lỗi liên tiếp, số đợt lỗi và thời gian nghỉ về 0; đợt 3 lỗi kế tiếp bắt đầu lại ở 30 giây. API khác tiếp tục độc lập. Dừng API nghĩa là không nhận ảnh mới; các request đã chạy vẫn được phép hoàn tất. Khi không còn API nào làm tiếp và các request đã chạy đã kết thúc, ảnh chưa xử lý được tính là lỗi, phiên hoàn thành và hẹn phiên mới sau 5 phút.

Hàng đợi dùng cùng schema trên D1 hoặc libSQL, có khóa lease theo từng slot và tiếp tục theo chuỗi Worker ngắn. Cron mỗi phút đánh thức công việc ngay cả khi không có trình duyệt mở. Thời gian nghỉ 30/60 giây và 5 phút là tối thiểu; lượt tiếp tục thực tế có thể chờ thêm đến nhịp Cron tiếp theo. Không giữ request ngủ để tránh vượt [giới hạn thời gian Worker](https://developers.cloudflare.com/workers/runtime-apis/context/).

Caption ví dụ:

```text
Mèo ngủ #cat #cute #safe https://example.com/source
```

Hoặc gửi link ảnh trực tiếp:

```text
https://cdn.example.com/photo.webp #cat #safe
```

- `#safe` / `#sfw`: ảnh an toàn; `#r18` / `#nsfw`: ảnh R18.
- Khi cấu hình AI vision, ảnh được lưu ngay rồi AI bổ sung tag, tên ảnh còn trống và phân loại. Phản hồi Telegram được edit ngay trên tin nhắn cũ khi có kết quả.
- Tên, phân loại và tag bạn nhập được giữ lại; AI bổ sung các tag còn thiếu. Tên nhân vật chỉ được thêm khi model nhận diện đủ chắc chắn, vẫn có thể nhận sai.
- AI không chắc phân loại: giữ `unknown`, chưa vào API random. Bấm **An toàn** hoặc **R18** để sửa.
- Thêm `#manual` vào caption để bỏ qua AI cho ảnh đó, chỉ dùng dữ liệu bạn nhập. Không có API vision thì bot tự dùng chế độ thủ công.
- Nút **AI gắn tag** chạy lại phân tích, dùng được cả với ảnh cũ hoặc khi lần trước lỗi.
- Nút **Sửa tag** mở tin nhắn để bạn reply `#tag1 #tag2`; hoặc reply thẳng ảnh/link đã lưu bằng `/tags #tag1 #tag2`. Đây là thay toàn bộ tag. Gửi `-` hoặc `/tags -` để xóa hết tag. Sửa tay trong lúc AI chạy sẽ được ưu tiên.
- Nút **Xóa ảnh** xóa metadata và ảnh gốc trong R2.
- `/stats` xem số ảnh, dung lượng và ảnh chờ phân loại.
- Chỉ nhận trong DM của người thuộc `OWNER_IDS`; người khác không upload hoặc sửa ảnh được.
- Giới hạn mặc định **10 MiB/ảnh**; JPEG, PNG, WebP, GIF, AVIF. Link phải là file ảnh công khai, không phải trang gallery cần đăng nhập/JavaScript. Máy chủ nguồn có thể chặn tải ảnh.
- Chống trùng theo SHA-256 của nội dung thật. Upload lại không làm mất metadata của ảnh cũ; dùng PATCH để sửa.

## Deploy

Cần Node.js 22 trở lên và account Cloudflare đã kích hoạt R2. Script chỉ tạo Worker `waku-api`, bucket `waku-api-images`, database `waku-api`; không đổi subscription hoặc bật gói trả phí. Free tier có hạn mức, không có nghĩa mọi mức sử dụng đều miễn phí.

Tạo Cloudflare API token giới hạn đúng account, quyền **Workers Scripts: Edit**, **D1: Edit**, **Workers R2 Storage: Edit**, **Account Settings: Read**. Ghi file credentials ở ngoài Git:

```text
token bot TELEGRAM_BOT_TOKEN
admin id YOUR_TELEGRAM_ID
token cloudflare CLOUDFLARE_API_TOKEN
Account ID YOUR_CLOUDFLARE_ACCOUNT_ID
```

Các nhãn `cloudflare_token = ...`, `account_id = ...` cũng được hỗ trợ. Có thể dùng biến môi trường `TELEGRAM_BOT_TOKEN`, `OWNER_IDS` (nhiều ID cách dấu phẩy), `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` thay cho file.

```powershell
cd D:\project2026\waku-bot\api
npm.cmd ci
npm.cmd run check
npm.cmd test
npm.cmd run deploy -- --credentials D:\project2026\waku-bot\api.txt
```

Linux/macOS dùng `npm` thay `npm.cmd`. Chạy lại deploy để cập nhật source; tài nguyên và key được giữ nguyên. Script áp dụng migrations, deploy Worker, cài secrets rồi đăng ký webhook cho **bot upload đã cấu hình**. Không dùng token bot Waku đang chạy cho uploader này.

File tự sinh, không commit:

- `wrangler.local.json`: Account ID, database ID, danh sách người upload, URL triển khai.
- `.secrets.json`: API key, webhook secret và cấu hình AI đã cài (nếu có); lưu an toàn để kết nối Waku sau này. Không gửi nội dung lên chat.
- `api.txt`, `.dev.vars*`, `.wrangler/`, `node_modules/` cũng được ignore.

Đổi owner hoặc biến cấu hình trong `wrangler.jsonc`/credentials rồi chạy deploy. URL workers.dev được tạo từ account. Custom domain có thể cấu hình sau trong Cloudflare.

Chỉ đăng ký lại webhook, không build:

```text
npm run webhook -- --credentials /path/to/api.txt
```

Chạy local: copy `.dev.vars.example` thành `.dev.vars`, điền giá trị, `npm run db:local`, `npm run dev`. Telegram webhook cần URL HTTPS công khai nên phần đó dùng bản đã deploy hoặc tunnel riêng.

## Tự gắn tag bằng AI

Dùng provider **OpenAI-compatible Chat Completions có vision**, điền thêm vào file credentials (hoặc biến môi trường cùng tên viết HOA):

```text
ai_base_url = https://your-provider.example.com/v1
ai_api_key = YOUR_VISION_KEY
ai_model = YOUR_VISION_MODEL
```

Chạy lại deploy. Có thể dùng lại URL/key/model vision trong `settings.toml` của Waku; prefix provider trong Waku không thuộc model ID gửi đến API. Ví dụ `9router/ag/example-model` có model ID là `ag/example-model`, provider lấy từ `[agent_providers.9router]`. Cấu hình đã lưu trong `.secrets.json` được tái sử dụng ở các lần deploy tiếp theo; thay đổi Waku không tự đồng bộ sang đây.

Model nhận dữ liệu ảnh đã lưu và metadata thủ công. Tag tự sinh dùng tiếng Anh, chữ thường và dấu `_` cho dễ tìm (`cat_ears`, `maid`, `blue_hair`, `cosplay`…). Một kết quả AI thành công phải có đủ tên, tag, phân loại `safe`/`r18` và kiểu `anime`/`real`. Phân loại dựa trên nội dung model nhìn thấy; backend không tự gán theo tag hoặc đổi kết luận của model thành `unknown` bằng ngưỡng confidence. Nếu model không thể xác định, tác vụ báo `classification_unresolved`; thiếu trường bắt buộc báo `incomplete_response`, không lưu kết quả AI dở dang hoặc tính là thành công. Ảnh và metadata cũ vẫn giữ nguyên, không lọt vào tab đã phân loại/random khi còn thiếu thông tin. Giao diện và API vẫn cho bạn sửa thủ công.

Ảnh luôn lưu trước; lỗi AI không làm mất ảnh hay báo upload thất bại. Tác vụ phân tích lưu trong D1. Mọi lỗi AI đều kết thúc lượt xử lý, không tự thử lại; cần sửa cấu hình nếu có lỗi key/model rồi bấm Chạy lại lỗi. Upload trùng ảnh đã có tác vụ không tự gọi AI thêm lần nữa. Nhấn **AI gắn tag** sẽ chủ động gọi lại.

API phân biệt model từ chối nội dung (`ai_error=provider_content_filtered`) với lỗi định dạng, timeout hay kết nối. Nhận diện được cả OpenAI `finish_reason=content_filter` và Gemini `promptFeedback.blockReason` trong envelope của gateway. Web hiện nhãn **AI từ chối**; mở chi tiết ảnh để xem giải thích. Không tự đổi phân loại sang An toàn/R18 khi model chưa phân tích được và không tự thử model khác. Mã lỗi trả qua API không chứa nội dung phản hồi hay thông tin bí mật của nhà cung cấp.

API upload trả ảnh ngay, có thêm `ai_status`: `pending`, `running`, `done`, `failed`, `skipped` hoặc `off` (ảnh cũ chưa có tác vụ). Khi cần chờ tag, đọc lại `/v1/images/ID`. Thêm `manual=true` vào multipart, hoặc `"manual":true` vào JSON upload để bỏ qua AI. Phí phân tích tính theo provider AI bạn chọn, tách khỏi hạn mức miễn phí của Cloudflare.

### Hạn mức D1

Hạn mức đọc tính theo số dòng SQLite quét, không phải số request hay dung lượng ảnh. Upload chỉ kiểm tra ID đợt, không đếm lại toàn bộ đợt mỗi ảnh. Migration `0012_incremental_counts.sql` giữ sẵn thống kê thư viện, tiến trình từng đợt và số lượng từng tag bằng trigger trong cùng transaction với dữ liệu nguồn. Thống kê thư viện đọc hai dòng, đếm tiến trình đợt đọc một dòng; bộ lọc tag đọc danh sách tag riêng thay vì tất cả tag gắn trên từng ảnh. Bộ đếm tăng thêm một số rows written khi dữ liệu thay đổi, đổi lại tránh quét lại cả thư viện ở mỗi lần cập nhật giao diện. Cron không chiếm/nhả slot upload khi không có việc.

Migration `0011_read_efficiency.sql` bổ sung index và bộ đếm mà không sửa/xoá ảnh. Worker cũng có thể tự cài migration này trong một transaction khi gặp database cũ: nếu quota hôm nay đã chặn migration, lần truy cập/cron đầu tiên sau reset sẽ cài, không cần giữ terminal local mở.

Khi D1 báo hết quota ngày, API trả `503` với mã `d1_daily_limit` và `Retry-After`. Web dừng polling và tạm dừng upload, giữ các file còn chờ để thử lại sau; tác vụ đã lưu trong D1 vẫn còn. Cờ tạm dừng lưu trên R2 để các Worker khác không tiếp tục gọi D1 liên tục; server tạm ngừng truy vấn 15 phút mỗi lần xác nhận hết quota để giảm các lần thăm dò. Hạn mức miễn phí reset lúc 00:00 UTC (07:00 Việt Nam), theo [Cloudflare](https://developers.cloudflare.com/d1/platform/pricing/).

## REST API

Các đường dẫn `/v1/*` cần header `Authorization: Bearer API_KEY`. Lấy key từ `.secrets.json`. Không truyền key qua query URL. Ví dụ dưới dùng biến `$WAKU_API_KEY` đã được đặt trên shell.

| Method | Đường dẫn | Chức năng |
| --- | --- | --- |
| GET | `/health` | Kiểm tra Worker đang phản hồi |
| POST/GET/DELETE | `/v1/session` | Đăng nhập, kiểm tra hoặc xóa cookie phiên web |
| GET | `/v1/stats` | Dung lượng, số ảnh và tác vụ đang chờ |
| GET | `/v1/state?batch=ID` | Gộp thống kê, phiên bản thư viện và tiến trình AI trong một request |
| GET / POST | `/v1/keys` | Quản trị: danh sách key (không có key gốc) / tạo key dịch vụ và trả key một lần |
| PATCH / DELETE | `/v1/keys/ID` | Quản trị: đổi tên, bật/tắt / thu hồi key dịch vụ |
| GET | `/v1/ai/settings` | Danh sách API/model và lựa chọn đang dùng, không trả key |
| POST | `/v1/ai/providers` | Lưu API: `name`, `base_url`, `api_key` |
| PATCH/DELETE | `/v1/ai/providers/ID` | Sửa/xóa API; key trống giữ nguyên |
| POST | `/v1/ai/providers/ID/models` | Thêm `{"model":"model-id"}` |
| PATCH/DELETE | `/v1/ai/models/ID` | Sửa/xóa model |
| PUT | `/v1/ai/selection` | Đổi riêng một API: `provider_id`, `enabled` (tùy chọn), `model_id` (tùy chọn); không tắt API khác. `provider_id:null` tắt tất cả |
| GET | `/v1/filters?q=cat` | Các tag hiện có và số ảnh, tối đa 1.000 tag; tìm tiếp bằng `q` |
| POST | `/v1/images/duplicates` | Kiểm tra trước khi upload: `{"hashes":["SHA256"],"urls":["https://…"]}`, tối đa 100 mục/lượt |
| POST | `/v1/images` | Upload multipart `file`, hoặc JSON chứa `url` |
| GET | `/v1/images?limit=20&before=CURSOR&q=maid&rating=safe` | Danh sách/tìm tên và tag/lọc phân loại, tối đa 100/trang; `next` là cursor trang kế |
| GET | `/v1/images/timeline?rating=safe&view=classified` | Ngày tải lên và số ảnh theo UTC+7; nhận cùng bộ lọc danh sách, cache một phút. Danh sách nhận thêm `until=UNIX_MS` để nhảy trước mốc ngày, kết hợp được với `before` |
| GET | `/v1/images/random?count=10&rating=safe&tags=cat,cute` | Lấy tối đa 10 ảnh, các tag kết hợp AND |
| GET | `/v1/images/ID` | Xem metadata |
| PATCH | `/v1/images/ID` | Sửa các trường `title`, `tags`, `rating`, `source_url`, `image_type` |
| PATCH | `/v1/images/bulk` | Sửa phân loại/kiểu ảnh hàng loạt: `ids` (1–100), `rating` và/hoặc `image_type`; trả `updated`, `ids`, `missing` |
| DELETE | `/v1/images/ID` | Xóa ảnh |
| POST | `/v1/images/ID/analyze` | Đưa ảnh vào hàng đợi AI để phân tích lại |
| POST | `/v1/analysis-batches` | Tạo một đợt AI, trả `id` |
| POST | `/v1/analysis-batches/ID` | Xếp hàng AI cho `{"ids":[...]}` (tối đa 100 ID/lượt) |
| GET | `/v1/analysis-batches/ID` | Tiến trình thật của đợt AI |
| POST | `/v1/analysis/control` | `{"action":"pause|resume|cancel|retry","batch_id":"ID tùy chọn"}`; bỏ batch_id để áp dụng toàn hàng đợi |
| GET/HEAD | `/media/ID` | File ảnh gốc, không cần API key |

`rating` của random: `safe` (mặc định), `r18`, `mixed`. Ảnh `unknown` không xuất hiện trong random. Kết quả ít hơn `count` nếu kho không đủ ảnh phù hợp; không lặp ID trong cùng lượt. Truyền `exclude=ID1,ID2,...` để tránh lặp giữa các lượt; tổng số tag và ID loại trừ tối đa 96. Bot gọi API cần lưu lịch sử ID đã gửi.

Có thể thêm `q=Kurumi` để tìm theo từ trong tên ảnh hoặc tag, ví dụ khớp `tokisaki_kurumi` nhưng không khớp `kurumiko`. Tối đa 80 ký tự/6 từ; các từ kết hợp AND, có thể nằm ở tên hoặc tag. `tags` vẫn là tag chính xác và kết hợp AND với `q`, `rating`, `image_type`. Không có kết quả thì trả danh sách rỗng, không thay bằng ảnh ngẫu nhiên. Bỏ `q` và `tags` để lấy ảnh ngẫu nhiên bình thường. Đây là đối chiếu metadata, không xác minh danh tính nhân vật trong hình.

Một POST upload chỉ nhận một file/URL. Muốn upload nhiều ảnh qua API, gọi riêng từng ảnh với tối đa 2 request đồng thời; HTTP 429 có `Retry-After` khi slot upload đang bận. Web đã tự xử lý hàng đợi và thử lại, không cần gộp cả đợt vào một request lớn.

Upload có thể truyền thêm `batch_id` để gắn tác vụ AI vào đợt đã tạo. Danh sách ảnh hỗ trợ `view=pending` (chưa phân loại hoặc AI đang chờ/chạy), hoặc `view=classified` kết hợp `rating=safe|r18` để chỉ lấy ảnh đã xử lý xong.

Kiểu ảnh `image_type`: `anime` (vẽ/anime) hoặc `real` (người thật/ảnh chụp). Có thể chọn lúc upload hoặc sửa chi tiết; AI chỉ điền khi chưa có lựa chọn. Ảnh cũ có tag nhận diện rõ được chuyển kiểu tương ứng khi migrate, còn lại để chưa xác định. Lọc danh sách bằng `types=anime,real` và `tags=maid,cat_ears`; nhiều giá trị trong từng mục kết hợp OR, các mục kết hợp AND. Lọc tag của random vẫn dùng AND như trước.

Web tính SHA-256 tại trình duyệt rồi kiểm tra theo lô trước upload; ảnh trùng nội dung kể cả đổi tên và ảnh lặp trong hàng đợi đều được bỏ qua. File gốc được ghi `original_name`; link import đã gặp cũng được ghi nhớ. Link mới chưa từng gặp phải tải về mới xác định được nội dung trùng. Máy chủ vẫn kiểm tra hash lần cuối để xử lý upload đồng thời.

### Ưu tiên ảnh gốc từ link

Web, REST API và bot upload Telegram dùng chung bộ tải ảnh, tự ưu tiên bản gốc của **link ảnh trực tiếp** từ:

- X/Twitter (`pbs.twimg.com`): `name=orig`, hỗ trợ cả URL `.jpg:small` cũ và avatar có hậu tố kích thước; thử JPG/PNG khi cần.
- Discord: chuyển media proxy về CDN gốc, bỏ tham số thu nhỏ và giữ nguyên `ex`, `is`, `hm` xác thực.
- Pinterest (`i.pinimg.com`): chuyển đường dẫn kích thước sang `originals`, có thử định dạng thay thế.
- Pixiv (`i.pximg.net`): chuyển thumbnail/master sang `img-original`, kèm Referer và thử JPG/PNG.
- Imgur: bỏ hậu tố thumbnail đúng mẫu ID; không cắt nhầm ký tự cuối ID ảnh gốc.
- Reddit: chuyển `preview.redd.it` sang `i.redd.it`; không đoán nguồn từ external-preview.
- Unsplash, Pexels, Shopify CDN và WordPress: gỡ các tham số/đường dẫn thu nhỏ đã biết; giữ thông tin như `ixid`, `v` hoặc tham số xác thực.
- Wikimedia: chuyển thumbnail raster về file gốc; giữ thumbnail PNG của SVG/PDF vì importer không nhận các file gốc đó.

Nguồn không nhận diện hoặc URL có chữ ký không rõ được giữ nguyên, không xóa query tùy tiện. Nếu bản gốc lỗi/không tồn tại/quá giới hạn dung lượng, thử lại URL người dùng gửi. Web ghi rõ khi chỉ lưu được bản từ link; không nhận bản fallback là ảnh gốc. Mỗi ảnh tối đa 4 ứng viên, chỉ gọi ứng viên kế tiếp khi cần, tổng thời gian tải tối đa khoảng 25 giây. Giới hạn dung lượng ảnh vẫn áp dụng cho từng bản tải. Không upscale và không mở khóa ảnh cần đăng nhập.

Kiểm tra trùng chuẩn hóa link ngay trên máy chủ, không gọi CDN lúc preflight. Các kích thước/định dạng của cùng ảnh được nhận diện cùng nguồn; chỉ ghi nhận danh tính bản gốc khi đã tải thành công bản đó. Những link thumbnail lưu trước bản nâng cấp này không ngăn việc lấy lại bản gốc. API upload URL trả thêm `import`: `source`, `preferred_url`, `fetched_url`, `used_original`, `fallback_reason`.

Khi nhập ảnh bằng URL, `source_url` luôn lấy URL thực tế tải thành công: bản gốc khi nâng cấp được, bản từ link khi phải fallback. Quy tắc dùng chung cho web/API/bot upload Telegram; upload file vẫn giữ nguồn nhập thủ công. Import lại ảnh trùng nội dung cũng cập nhật riêng link nguồn, giữ nguyên tên/tag/phân loại và tôn trọng chỉnh sửa thủ công đang diễn ra đồng thời.

Tham khảo quy tắc nguồn: [Discord CDN](https://docs.discord.com/developers/reference#signed-attachment-cdn-urls), [Unsplash image URLs](https://unsplash.com/documentation#dynamically-resizable-images), [Pexels photo resource](https://www.pexels.com/api/documentation/#photos), [Shopify image URL](https://shopify.dev/docs/api/liquid/filters/image_url), [WordPress image CDN](https://developer.wordpress.com/docs/developer-tools/site-accelerator-api/). Các mẫu X/Pixiv/Pinterest cũng được đối chiếu với URL và cơ chế tải của [gallery-dl](https://github.com/mikf/gallery-dl/tree/master/gallery_dl/extractor).

Web rảnh chỉ kiểm tra một request mỗi phút; khi có tác vụ chuyển sang mỗi 8 giây, gộp tiến trình và thống kê. Tab trình duyệt bị ẩn ngừng polling. Chỉ tải lại danh sách khi phiên bản thư viện thay đổi; giữ lại các phần tử ảnh để tránh nháy. Hàng đợi chạy độc lập qua continuation/Cron, không phụ thuộc web liên tục gọi drain.

Upload:

```bash
curl "$WAKU_API_URL/v1/images" -H "Authorization: Bearer $WAKU_API_KEY" \
  -F 'file=@cat.png' -F 'title=Mèo ngủ' -F 'tags=cat,cute' -F 'rating=safe'

curl "$WAKU_API_URL/v1/images" -H "Authorization: Bearer $WAKU_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://cdn.example.com/photo.webp","tags":["cat"],"rating":"safe"}'

curl "$WAKU_API_URL/v1/images/random?count=10&tags=cat&rating=safe" \
  -H "Authorization: Bearer $WAKU_API_KEY"
```

Response upload: `{ "image": { "id", "url", "title", "tags", "rating", ... }, "duplicate": false }`.
Response random: `{ "images": [...], "count": 10 }`.
Lỗi có HTTP status và `{ "error": { "code", "message" } }`.

Bucket R2 không public. Link `/media/ID` là **link chia sẻ công khai**: ai có link đều xem được, kể cả ảnh chưa phân loại hoặc R18. Xóa sẽ chặn các lần đọc mới tại Worker; bản tải hoặc cache cũ có thể tồn tại đến 1 giờ. API key bảo vệ danh sách, upload và chỉnh sửa, không làm link ảnh thành riêng tư.

## Nhóm theo nguồn trên web

Tab **Tất cả** gom các file có nguồn thành nhóm theo tài khoản/trang nguồn, gồm cả ảnh và video. Chỉ tạo thẻ nhóm khi có ít nhất 2 file phù hợp bộ lọc; nguồn chỉ có 1 file hiển thị như file lẻ, giữ nguyên link nguồn. Ví dụ link trang cá nhân, media và bài đăng của cùng một tài khoản X/Twitter vào một nhóm. Link không thể suy ra tác giả, như URL ảnh CDN, được giữ theo trang/link đó; không tự đoán tài khoản. File chưa có nguồn hiển thị bên ngoài các nhóm.

Bấm nhóm để xem và lọc file bên trong; nút **Tất cả** quay lại danh sách nhóm. Các tab An toàn, R18 và Chờ phân loại vẫn hiển thị file riêng. Tích checkbox nhóm để chọn mọi file phù hợp bộ lọc trong nhóm, gồm cả các file chưa tải lên màn hình; Sửa/Xóa áp dụng cho các ID đã chọn. Chọn tất cả gồm cả nhóm nguồn và file lẻ. Việc chọn chỉ tải ID, loại file và mã nhóm, không đọc ảnh hay dữ liệu AI.

Khi đã chọn bất kỳ file/nhóm nào, bấm ô **Chọn tất cả** để bỏ chọn hết; khi chưa chọn gì, ô này chọn toàn bộ. Trong chế độ chọn, giữ **Ctrl** và rê chuột qua các thẻ để chọn thêm, không đảo trạng thái thẻ đã chọn. Trên mobile, giữ một thẻ khoảng 0,4 giây rồi vuốt qua các thẻ khác để chọn thêm; vuốt ngay không giữ vẫn cuộn bình thường. Vuốt chuyển tab chỉ hoạt động khi chưa chọn file. **Ctrl + click** vẫn mở trình xem.

Danh sách nhóm phân trang từ toàn bộ thư viện, không chỉ gom các ảnh vừa tải trên trình duyệt. Thống kê nguồn dùng index và cache, chỉ tổng hợp lại tối đa mỗi phút khi thư viện thay đổi; làm mới hoặc sửa nguồn trên web cập nhật trực tiếp. Các màn hình đã mở được giữ tối đa 5 phút để ra/vào nhóm ngay, gồm vị trí cuộn; thay đổi thư viện làm mất hiệu lực cache. Việc gom nhóm không đổi link nguồn đã lưu hay di chuyển file R2.

Nút **Sửa** hỗ trợ giữ nguyên, thay hoặc xóa link nguồn hàng loạt. Facebook chuẩn hóa host và link `profile.php?id=…`/`people/…/ID`; X hỗ trợ `/i/user/ID`. Web tra tối đa 2 hồ sơ công khai mỗi lượt ở nền, phần chưa tra được lưu để Cron tiếp tục 2 hồ sơ/phút. Tên và cặp username/ID đã xác minh được lưu trong 7 ngày; tra thất bại nghỉ 1 ngày, giữ tên/nguồn cũ. Pixiv lấy tên qua hồ sơ công khai. Trang yêu cầu đăng nhập/chặn truy cập sẽ giữ ID, không đoán hai tài khoản là một. Link `pbs.twimg.com/media/…` không chứa chủ tài khoản; muốn gộp các file này hãy chọn chúng và sửa nguồn sang link profile.

Video có ảnh xem trước tạo ở trình duyệt, dùng chung cache thumbnail với ảnh và tối đa 2 lượt xử lý. Không chuyển mã video trên VPS; riêng khung hình dùng cho AI được lưu trên R2, tính vào Data và dọn khi xoá video. Định dạng trình duyệt không giải mã được giữ biểu tượng phát. Bìa nhóm ưu tiên ảnh có sẵn. Trình xem video chỉ cung cấp phát/dừng và bật/tắt âm thanh.

API: gửi JPEG (tối đa 512 KB, 1280 px) tới `POST /v1/images/:id/frame`
trước khi gọi `/analyze` hoặc thêm video vào phiên AI. Khung đã lưu không bị
ghi đè; `analysis_frame` trong thông tin video cho biết đã có khung hay chưa.

## Độ bền và giới hạn

Kho ảnh chặn upload vượt **10.000.000.000 byte (10 GB)** ở máy chủ, dùng chung cho web, API và bot upload Telegram. `Data` hiển thị tổng object R2 cộng dung lượng đã giữ cho các lượt đang ghi, theo GB thập phân; vì vậy số này có thể lớn hơn tổng file trong danh sách ảnh. Lần đầu sau schema 19, hệ thống kiểm kê toàn bộ bucket một lần; khi kiểm kê chưa hoàn tất hoặc DB lỗi, không cho ghi thêm R2. Chỉ một thao tác giữ chỗ nguyên tử được phép tiêu phần dung lượng còn lại, tránh hai upload đồng thời vượt ngưỡng. Chỉ trả dung lượng sau khi R2 xác nhận xoá object; file lỗi chưa dọn vẫn được tính. HTTP `507 / storage_full` dừng hàng đợi web và giữ các file chưa tải, không tự thử lại liên tục. Xoá bớt ảnh rồi tiếp tục khi đã đủ chỗ.

Giới hạn này bảo vệ bucket riêng do Waku API quản lý. Không ghi trực tiếp bằng dashboard/S3 hoặc ứng dụng khác vào bucket sau khi đã kiểm kê; các ghi ngoài hệ thống không đi qua bộ giữ chỗ. Các bucket khác trong account và phí thao tác R2/Workers không thuộc giới hạn dung lượng này. Khi chuyển account/bucket, phải kiểm kê lại `storage_objects` trước khi mở upload; không sao chép nguyên bộ đếm sang một bucket có nội dung khác.

Webhook chỉ ACK sau khi đã ghi tác vụ vào D1; Cron mỗi phút khôi phục tác vụ nếu Worker bị gián đoạn. Upload lỗi tạm thời được thử lại tối đa 3 lần, có backoff cho Telegram 429. Gửi phản hồi có dấu mốc lưu trước: nếu mất ACK sau khi gửi, bot không spam gửi lại; ảnh đã lưu vẫn có trong API. Ảnh không được báo lưu thành công trước khi R2 và metadata D1 hoàn tất.

Xóa ảnh được ghi vào outbox trước, Cron thử lại xóa R2 nếu cần. Upload mới được ghi nhận trong `object_uploads` trước khi ghi R2; transaction lưu ảnh giải phóng bản ghi này khi thành công và đưa bản sao trùng vào outbox. Nếu bị gián đoạn hoặc không biết DB đã commit chưa, không xoá R2 ngay. Sau thời gian bảo vệ một giờ, Cron xử lý tối đa 5 bản ghi hết hạn mỗi lượt và chỉ thu dọn object không được ảnh nào tham chiếu. Transaction lưu ảnh phải còn sở hữu bản ghi upload, tránh tranh chấp với tác vụ thu dọn. Cơ chế này bảo vệ upload từ schema 18; không tự quét/xoá object không được theo dõi từ trước đó.

Theo [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/), [R2 pricing](https://developers.cloudflare.com/r2/pricing/) và [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/): hạn mức free hiện tại gồm Workers 100.000 request/ngày; R2 Standard 10 GB-tháng, 1 triệu Class A và 10 triệu Class B/tháng, không tính phí egress; D1 5 triệu row read/ngày, 100.000 row write/ngày và 5 GB tổng lưu trữ. R2 có thể yêu cầu phương thức thanh toán và tính phí vượt mức. Kiểm tra dashboard Usage/Billing khi vận hành.

Đây là kho ảnh/API riêng; source chưa tự thay nguồn ảnh mặc định của bot Waku. Có thể tích hợp REST API vào Waku sau khi kho có dữ liệu.
