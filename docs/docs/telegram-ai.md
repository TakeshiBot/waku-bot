# AI Telegram: quản trị nhóm và cách gửi phản hồi

Phần này dùng agent, provider, lịch sử, quota và database của base Waku hiện tại.
Các chức năng quản trị được triển khai lại với kiểm tra quyền Telegram; không
dùng bộ xử lý quản trị của bản mod cũ.

## Yêu cầu và cách gọi

Bật AI của nhóm và **AI quản trị** trong `/config`, rồi bấm **Lưu**.
AI quản trị mặc định tắt; khi tắt, bot vẫn trò chuyện nhưng không thực hiện thao tác quản trị.
Cho Waku làm admin và cấp đúng quyền cần dùng.
Người yêu cầu cũng phải có quyền quản trị tương ứng trong chính nhóm đó.
Người thường vẫn được gửi yêu cầu. AI gọi tool để kiểm tra quyền thực tế,
rồi giải thích lý do từ chối theo prompt nếu người gọi hoặc bot thiếu quyền;
không suy diễn rằng bot chưa hỗ trợ chức năng đó.
Quyền admin toàn bot không thay thế quyền quản trị nhóm; admin ẩn danh không
được thực thi khi không xác minh được danh tính người ra lệnh.

Gọi tên/tag Waku, hoặc dùng `/chat`, rồi yêu cầu bằng ngôn ngữ tự nhiên:

- Reply tin của thành viên: `waku, mute người này 10m vì spam`.
- `waku, ban @username 1d vì spam`.
- Reply tin cần ghim: `waku, ghim tin này, không gửi thông báo`.
- Reply tin cần xóa: `waku, xóa tin này`.
- `waku, đổi tên nhóm thành Hội bạn Waku`.

Mục tiêu ưu tiên @username/ID được nêu rõ trong tin ra lệnh; nếu không có thì
dùng người gửi tin đang reply. Username có thể viết không có `@`, ví dụ
`waku cho takeshi7502 admin`; bot tra đúng username Telegram, kiểm tra thành viên
và quyền trước khi thực hiện. Không dùng tên gần giống hoặc tên hiển thị để đoán.
Reply tin của Waku kèm `@bot_khac` vẫn tác động
đến `@bot_khac`, không chọn Waku. Bot khác là đối tượng quản trị hợp lệ,
bao gồm mute, ban/kick và bổ nhiệm admin nhóm, theo quyền/trạng thái Telegram.
Bot không đoán từ tên hiển thị hoặc tác giả gốc của tin chuyển tiếp.
Khi reply câu trả lời gần nhất của Waku cho chính mình trong 5 phút, câu tiếp
nối như `cho lên admin` có thể dùng mục tiêu duy nhất đã ghi trong yêu cầu gốc.
Mục tiêu được giữ qua chuỗi reply của cùng người gọi, chẳng hạn
`cho @username admin` → `thôi demote đi` → `giờ mute 36s`, cả với phản hồi
thông thường, streaming hoặc tin do tool gửi. Thời hạn như `36 giây` không
được coi là ID thành viên.
Backend đối chiếu người gọi, nhóm, ID tin reply và thời gian; không lấy mục tiêu
từ nội dung AI tự tạo, reply của người khác hoặc lịch sử tùy ý.
Khi yêu cầu chưa rõ, AI phải hỏi lại trước khi thực hiện.
Khi thao tác và đối tượng đã rõ, bot gọi tool ngay; không yêu cầu xác nhận thêm.
Tool kiểm tra quyền thực tế ngay trước khi gửi thao tác.

## Menu, quyền và các lệnh Telegram

`/config` trong nhóm, `/sett`, `/lang`, thay đổi lời chào `/greet`, `/qp` và
`/rss digest|broadcast on|off` dùng bản nháp:
bấm **Lưu** mới áp dụng và đóng menu. Menu cùng tin gọi lệnh tự xóa sau 5 giây
(tin gọi lệnh chỉ xóa được khi bot có quyền). Mở lại lệnh để bỏ bản nháp.
Menu gắn với người mở và tin nhắn, hết hạn sau 15 phút, kiểm tra lại quyền khi lưu. Thay đổi cấu hình nhóm có hiệu
lực ngay và không cần khởi động lại.

Nút **Ảnh** trong `/config` nhóm chuyển **An toàn → R18 → Cả hai → Tắt**.
Mặc định là **An toàn**. Bấm **Lưu** để áp dụng cho `/seg`, `/randavatar`
và ảnh anime do AI gửi; R18 vẫn được che bằng spoiler. Ảnh lấy từ liên kết
cũng được kiểm tra theo chế độ đã chọn. Trong DM/Business, `manyacg_r18_mode`
chọn `0` (An toàn, mặc định), `1` (R18) hoặc `2` (Cả hai), có hiệu lực ngay
sau khi lưu trong `/config` của quản trị bot.

`/sett` chỉnh **mẫu quyền bổ nhiệm admin** cho AI, không đổi quyền của thành viên
hay admin ngay lúc lưu. Chỉ dùng mẫu này khi yêu cầu rõ, ví dụ reply thành viên:
`waku, bổ nhiệm người này theo mẫu quyền đã lưu trong /sett`.
Quyền được cấp vẫn phải nằm trong quyền hiện tại của người ra lệnh và bot.
Promote không nêu quyền cụ thể dùng phần quyền chung của người gọi và bot,
trừ quyền thêm admin và ẩn danh. Không cần cấu hình `/sett` trước.

`/tag Nội dung` và `/xtag` đặt/xóa tag cho người đang reply; không reply thì áp
dụng cho chính người gọi. Với thành viên thường, bot dùng quyền quản lý tag;
với admin, bot chỉ đổi danh hiệu và giữ nguyên toàn bộ quyền. Bot không bổ nhiệm
hoặc hạ admin để đổi tag. Chủ nhóm, quyền bổ nhiệm, chuỗi người bổ nhiệm và
khả năng bot sửa đối tượng được kiểm tra theo Telegram; nhiều cờ quyền hơn
không tự tạo một cấp admin cao hơn.

Các lệnh giải trí hiện là `/nemchai` (ném chai), `/nhatchai` (nhặt chai) và
`/seg` (ảnh), đồng bộ với menu lệnh Telegram và `/help`.

`/config` trong DM chỉ dành cho owner/admin toàn bot, hiển thị các khóa cấu hình
được hỗ trợ. Giá trị sửa cũng chờ **Lưu**. Các giá trị đọc theo từng lượt và thay
đổi model/provider chat có thể áp dụng ngay được cập nhật sau khi lưu thành công.
Discord bật/tắt và thay token Discord được áp dụng ngay khi lưu; Telegram vẫn chạy.
Thay đổi token Telegram, database, listener, lịch khởi tạo hoặc danh tính embedding/index
cần khởi động lại được liệt kê riêng. Lưu không tự khởi động lại bot; nút khởi động lại
chỉ xuất hiện cho các thay đổi còn chờ áp dụng. Bot tự chạy lại sau khi đóng các
kết nối hiện tại, kể cả khi chạy Python trực tiếp. Biến môi trường vẫn có ưu tiên cao hơn
file TOML, và khóa bí mật được che trong menu.

`/botpromote <ID Telegram>` cấp quyền quản trị **toàn bot**;
`/botdemote <ID Telegram>` gỡ quyền đó. Chỉ owner/admin toàn bot dùng được,
trong DM cá nhân với bot. Người được cấp quyền phải đã tương tác với bot;
owner và chính người gọi được bảo vệ. Hai lệnh này không hoạt động trong nhóm
và không thay đổi quyền admin Telegram của nhóm.

## Đối chiếu hành vi quản trị

Bảng dưới dùng tên lệnh Rose để đối chiếu ý nghĩa; đây là các **tool của AI**,
không đăng ký thêm bộ slash command Rose. `/chat ban ...` hoặc lời gọi Waku
sẽ đi qua AI và cùng một bộ kiểm tra quyền.

| Thao tác | Tool Waku | Ý nghĩa/quyền cần có |
| --- | --- | --- |
| `/ban`, `/tban` | `ban_user` | Cấm vào nhóm; `duration` cho cấm có thời hạn. Quyền hạn chế thành viên. |
| `/kick` | `kick_user` | Đuổi thành viên nhưng cho phép vào lại. Quyền hạn chế thành viên. |
| `/mute`, `/tmute` | `mute_user` | Hạn chế gửi tin, có thể đặt thời hạn. Quyền hạn chế thành viên. |
| `/unban`, `/unmute` | `unban_user`, `unmute_user` | Gỡ cấm/mute; không mở rộng hơn quyền mặc định hiện tại. |
| `/warn`, `/resetwarns` | `warn_user`, `reset_user_warnings` | Lưu cảnh cáo theo nhóm/thành viên trong database. |
| Xóa tin đang reply | `delete_replied_message` | Chỉ xóa đúng tin mục tiêu trong nhóm hiện tại. Quyền xóa tin. |
| `/pin`, `/unpin` | `pin_chat_message`, `unpin_chat_message` | Ghim/bỏ ghim. Quyền ghim tin. |
| Cấp/gỡ admin | `promote_user`, `demote_user` | Quyền thêm admin; không tự cấp vượt quyền của người ra lệnh/bot. |
| Slow mode | `set_slow_mode` | Bot giải thích giới hạn API; admin phải chỉnh trong ứng dụng Telegram. |
| Quyền nhóm | `set_chat_permissions` | Thay đổi đúng những quyền được yêu cầu. Quyền hạn chế thành viên. |
| Khóa/mở khóa | `lock_chat`, `unlock_chat` | Lưu và khôi phục quyền trước khi khóa; hỗ trợ thời hạn khóa. |
| Tên/mô tả | `set_chat_title`, `set_chat_description` | Quyền thay đổi thông tin nhóm. |
| Nhãn thành viên | `set_member_tag`, `clear_member_tag` | Quyền quản lý nhãn; chỉ áp dụng đối tượng Telegram cho phép. |

Thời hạn dùng `m`, `h`, `d`, `w`, chẳng hạn `10m`, `2h`, `1d`, `1w`.
Bot từ chối thời hạn quá ngắn/dài mà Telegram có thể hiểu thành vĩnh viễn.
Bot không ban/mute/kick chủ nhóm, admin hoặc chính bot. Thao tác chỉ thành công
khi Telegram xác nhận; lỗi quyền hoặc kết quả không xác định không được báo
thành công hay tự thử lại bằng một công cụ khác.

Telegram chỉ cho tài khoản người dùng gọi
[API thay đổi slow mode](https://core.telegram.org/method/channels.toggleSlowMode),
không cho bot token. Waku không giả lập chức năng này bằng cách tự xóa tin.
Mute có thời hạn cập nhật hạn chế cá nhân và thời điểm hết hạn như `/tmute`.
Khi hết hạn, Telegram trả thành viên về quyền mặc định của nhóm. Unmute xử lý
cả hạn chế do admin/bot khác đặt; với snapshot của chính Waku còn khớp, bot
khôi phục trạng thái trước mute và giữ giới hạn mặc định hiện tại của nhóm.
Unlock cũng có thể mở quyền gửi tin khi nhóm đã bị khóa từ bên ngoài Waku;
lịch mở khóa tự động vẫn không ghi đè thay đổi quyền mới của admin.

Mỗi thao tác ghi Telegram chỉ gửi một lần. Nếu mất xác nhận, bot đọc lại quyền
thành viên để xác minh promote/demote/mute/ban thay vì gửi lại thao tác.
AI diễn đạt kết quả theo prompt đang cấu hình, kể cả thông báo lỗi nếu API còn
hoạt động; văn bản dự phòng chỉ được dùng khi lượt AI giải thích cũng thất bại.

Cảnh cáo hiện dùng chính sách 3 lần → mute 24 giờ, hết hạn cảnh cáo sau 30 ngày.
Đây là chính sách Waku giữ từ phạm vi chức năng cũ, không phải mặc định của Rose.
Lỗi mute không làm mất số cảnh cáo. Snapshot quyền mute/khóa dùng các cờ native
để giữ riêng quyền sticker/GIF và các quyền gửi khác. Lịch mở khóa có thời hạn
dùng scheduler bền vững; callback kiểm tra thế hệ khóa và quyền hiện tại trước
khi khôi phục, không ghi đè thay đổi quyền do admin khác thực hiện sau đó.

Xác minh thành viên cũng kiểm tra quyền hạn chế hiện tại của bot và người gọi
lệnh quản trị. Snapshot trước khi xác minh giữ nguyên từng cờ quyền; khôi phục
không mở rộng hơn quyền mặc định hiện tại. Nếu quyền bị sửa từ bên ngoài hoặc
kết quả gửi thao tác chưa xác định, bot giữ phiên để quản trị viên xử lý thay vì
tự gửi lại hay báo thành công.
Phiên cũ chỉ lưu quyền tổng hợp, thiếu snapshot native, cần xử lý thủ công vì
không thể suy ra chính xác các hạn chế ban đầu.

Tham khảo [Rose: Restrictions](https://missrose.org/docs/moderation/restrictions/)
và [Rose: Admins](https://missrose.org/docs/moderation/admins/)
và [Telegram Bot API: quản trị nhóm](https://core.telegram.org/bots/api).

## Rich message

`agent_rich_output = true` bật gửi rich message. Nội dung ngắn ưu tiên Markdown
native Telegram để giữ tiêu đề, bảng, checklist, công thức và các tag rich được
Telegram hỗ trợ. Nội dung dài được chia và kiểm tra giới hạn trước khi gửi.
Nếu Telegram từ chối định dạng, bot chuyển phần bị từ chối sang văn bản thường;
không gửi lại phần đã được xác nhận gửi thành công.

Tin rich đầu vào được đọc cho prompt, lịch sử, reply và bộ lọc gọi bot. Liên kết,
caption/credit, bảng và trạng thái checklist được giữ trong bản văn bản. Ảnh,
video, audio và voice native được đưa qua ngân sách, giới hạn kích thước và
timeout của pipeline đa phương tiện hiện có. Đường đọc đầu vào không tải URL
trong rich HTML.

SDK hiện tại chưa parse đủ các loại block mới của Bot API 10.3; media lồng trong
list cũng có giới hạn do parser SDK chưa truyền đầy đủ bảng file vào block con.
Các đường vào được kiểm thử là những block mà SDK đang parse được. Không thể
coi đây là hỗ trợ mọi kiểu tin rich của các phiên bản Telegram tương lai.

## Hiển thị câu trả lời đang sinh

`agent_streaming = true` dùng **native draft** trong chat riêng, kể cả đường
Business khi kết nối hỗ trợ. Draft là bản xem trước tạm thời; khi AI hoàn tất,
bot gửi tin chính thức qua đúng chat/topic/kết nối. Nút Stop chỉ hủy lượt sinh
gắn với chính draft đó; không hoàn tác thao tác quản trị đã được xác nhận.

Telegram chỉ hỗ trợ draft streaming trong chat riêng. Với nhóm hoặc peer
không hỗ trợ, bot hiển thị trạng thái đang nhập rồi gửi kết quả hoàn chỉnh.
Bot không dùng cách liên tục sửa tin chính thức để giả lập streaming.
`agent_streaming = false` vẫn dùng đường gửi kết quả hoàn chỉnh.

Nếu delta đầu tiên chỉ có dấu Markdown như `**`, `#` hay hàng mở code block,
bot giữ draft dạng đang suy nghĩ và tiếp tục cập nhật khi có nội dung hiển thị.
Lỗi định dạng draft không làm mất streaming cho cả lượt; draft văn bản và rich
dùng cùng định danh để tránh tạo nhiều tin. Kết quả gửi chính thức không được
gửi lại khi trạng thái nhận của Telegram chưa xác định.

Xem [Telegram: AI features for bots](https://core.telegram.org/api/bots/ai)
và [rich message/streaming](https://core.telegram.org/bots/features).
