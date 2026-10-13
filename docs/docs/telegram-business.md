# Telegram Business

Waku là trợ lý AI của tài khoản Telegram đã kết nối: nhận hội thoại trong phạm vi
chủ tài khoản cho phép, giữ ngữ cảnh và trả lời thay mặt tài khoản đó. AI biết mình
là trợ lý, không giả làm chủ tài khoản. Phần Business dùng provider, streaming và
rich message của base hiện tại.

## Kết nối và bật

1. Trong **BotFather**, bật **Secretary Mode** cho bot (một số giao diện ghi
   **Business Mode**).
2. Trong cài đặt tài khoản Telegram, kết nối Waku ở phần bot Business, chọn các
   cuộc chat cho bot truy cập và cấp quyền trả lời.
3. Thêm cấu hình ở **cấp gốc** của `settings.toml`, trước `[agent_providers.*]`:

   ```toml
   agent = true
   agent_chat_model = "default/ten-model"
   ```

Business dùng `agent_chat_model`,
`agent_streaming`, `agent_rich_output`, timeout và giới hạn AI của bot hiện tại;
không cần token hoặc URL/key AI khác.

Sau khi cập nhật source, khởi động lại `python -m waku` một lần để nạp handler.
Chủ bot hoặc quản trị viên toàn cục có thể vào **DM với bot → /config → Chức Năng →
Chat Business** để bật/tắt.
Công tắc được lưu và áp dụng ngay, không cần khởi động lại. Những thay đổi model,
provider và prompt vẫn theo cơ chế cấu hình của base hiện tại.

Trong **Cấu Hình → Business**, sửa biến **business_chat_prompt** và bật/tắt
**Business Prompt**, rồi bấm **Lưu**. Khi bật và nội dung không trống,
Business chỉ dùng prompt riêng này, không ghép thêm `agent_prompt`. Khi tắt
hoặc để trống, Business dùng prompt chính. Tắt không xoá nội dung đã lưu;
thay đổi áp dụng cho lượt tiếp theo, không cần khởi động lại.
Nút **Đóng** trong menu admin bỏ các thay đổi chưa lưu.

File cấu hình chỉ chứa nội dung prompt; công tắc lưu trong database:

```toml
business_chat_prompt = '''Bạn là trợ lý trả lời khách hàng của tôi.'''
```

Telegram yêu cầu kết nối còn bật và quyền `can_reply`. Quyền gửi/sửa tin áp dụng
cho cuộc chat riêng có tin đến trong 24 giờ gần nhất. Xem
[hướng dẫn Business Bots chính thức](https://core.telegram.org/bots/features#business-bots).

## Hành vi

- Chỉ trả lời tin văn bản và Rich Message từ người dùng trong DM thuộc kết nối.
  Bỏ qua tài khoản bot, tin gửi qua bot khác hoặc bot Business, lệnh bắt đầu
  bằng `/`, tin đi và tin của chủ kết nối. Bật Bot-to-Bot trong BotFather cũng
  không làm Business tự trả lời bot khác.
- Loại tin trùng theo kết nối, cuộc chat và ID tin nhắn.
- Lịch sử được lưu trong database, tách theo **chủ tài khoản + người liên hệ**,
  giữ qua lần khởi động lại hoặc kết nối lại. Không dùng lịch sử DM thường, nhóm
  Telegram hoặc Discord.
- Ghi nhận tin đến, tin chủ tài khoản tự gửi, câu trả lời của trợ lý và những tin
  được Telegram kèm theo khi reply. Tin sửa được cập nhật, tin xóa bị loại khỏi
  ngữ cảnh. Giữ tối đa 100 tin mỗi chat; tối đa 4.000 ký tự văn bản mỗi tin và
  khoảng 20.000 ký tự ngữ cảnh cho mỗi lượt AI. File có metadata; tài liệu được
  đọc theo giới hạn của bộ đọc file hiện tại.
- Mỗi lượt có tên/username đã xác minh của chủ tài khoản và người liên hệ, thời
  điểm các tin và giờ hiện tại UTC+7. AI phân biệt lượt đầu bot quan sát, chat đang
  tiếp tục, quay lại gần đây và quay lại sau thời gian dài; tiếp nối chủ đề còn
  dang dở khi lịch sử thực sự cho thấy điều đó. Không tự bịa tình trạng, lịch hẹn
  hoặc thông tin cá nhân của chủ tài khoản.
- Khi trợ lý chưa tiếp chuyện, Waku chờ 15 giây yên lặng rồi mới cân nhắc trả
  lời. Các tin đến liên tiếp được gộp vào cùng một lượt. Nếu chủ tài khoản trả lời
  trong thời gian chờ, Waku dừng lượt đó. Hết thời gian chờ, AI chỉ trả lời khi
  ngữ cảnh còn dang dở hoặc cần hỗ trợ; cuộc trò chuyện đã xong, lời cảm ơn hoặc
  câu trả lời đầy đủ của chủ tài khoản sẽ được để yên. Khoảng lặng không được xem
  là bằng chứng chủ tài khoản đang bận hay ngoại tuyến.
- Khi trợ lý đã trả lời, các tin tiếp theo của người liên hệ được xử lý ngay,
  không chờ thêm 15 giây và không bị quyết định giữ im lặng chặn lại. Bot ưu tiên
  trả lời ngắn, thường một hoặc hai câu, tập trung vào yêu cầu cần trợ lý xử lý.
  Chủ tài khoản nhắn vào sẽ dừng lượt trợ lý đang chạy và lấy lại quyền tiếp chuyện;
  Waku không tự trả lời lại tin cũ sau đó. Khi có tin mới từ người liên hệ, Waku
  lại dùng cửa sổ chờ 15 giây trước khi cân nhắc tiếp chuyện.
  Trạng thái này tách theo từng cuộc chat và được giữ qua lần khởi động lại.
- Khi chủ tài khoản tự trả lời, hủy lượt AI đang soạn trong chat đó để tránh nói
  chen. Tin đến cũ được giao muộn sau câu trả lời của chủ tài khoản cũng bị bỏ qua.
- Mặc định câu trả lời có nhãn `Tên bot:`, lấy từ `nickname` trong cấu hình, để
  người liên hệ biết đây là trợ lý. Tắt **Bot ký tên** trong `/business` sẽ bỏ nhãn;
  AI vẫn tự xưng là trợ lý của chủ tài khoản, không tự xưng là Telegram Business.
- Khi bật streaming, thử draft gốc với Thinking trước khi AI sinh ra nội dung,
  sau đó cập nhật draft và gửi câu trả lời cuối. Nếu Telegram không hỗ trợ draft
  cho kết nối/cuộc chat này, gửi phần văn bản đầu tiên và sửa cùng tin đó khoảng
  2 giây một lần. Kết quả cuối giữ rich message nếu bật; chỉ gửi tin bổ sung khi
  nội dung vượt giới hạn Telegram. Mọi thao tác đều đi qua đúng kết nối Business.
- Tắt công tắc hoặc thu hồi quyền kết nối sẽ ngăn bot tiếp tục trả lời.
  Update sửa hoặc xoá tin không tạo thêm lượt AI.

Nếu chưa trả lời, kiểm tra công tắc Business, `agent`, model/provider, Secretary
Mode, phạm vi chat và quyền trả lời trong tài khoản Telegram. Kết nối Business
không dùng công tắc chat DM thường hoặc công tắc `telegram_enabled` của bot.

## Bảng điều khiển của chủ tài khoản

Chủ tài khoản đã kết nối mở **`/business` trong DM với bot**. Admin bot cũng có
nút **Business** trong `/config → Cấu Hình → Business`; nút này chỉ
quản lý tài khoản thực sự thuộc người mở, không cấp quyền cho admin bot quản lý
tài khoản của người khác. Nếu chưa ghi nhận kết nối, gửi một tin trong chat đã
cho phép bot truy cập, rồi mở lại menu.

Các công tắc của **từng tài khoản**, không thêm tham số nâng cao vào `settings.toml`:

| Công tắc | Mặc định | Tác dụng |
| --- | --- | --- |
| Bot trả lời | Bật | Tự trả lời người dùng trong chat được quản lý |
| Nhớ Hội Thoại | Bật | Lưu và dùng nội dung hội thoại; tắt thì tin mới chỉ lưu ID, vai trò, thời điểm |
| Bot đánh dấu đọc | Tắt | Đánh dấu tin người dùng đã đọc nếu Telegram cấp quyền |
| Quyền quản lý | Tắt | Cho chủ tài khoản dùng các thao tác tin nhắn, hồ sơ, quà/Sao và story |
| Bot ký tên | Bật | Thêm `nickname:` trước tin do trợ lý gửi |
| Gửi lời nhắn | Bật | Cho phép nhận lời nhắn hoặc hẹn từ người liên hệ và chuyển riêng cho chủ tài khoản |

**Lưu** áp dụng ngay rồi đóng; **Đóng** bỏ thay đổi. Menu lưu được dọn sau 5 giây.
Trong các mục thao tác, nhập nội dung theo hướng dẫn; xem lại rồi **Lưu** mới thực
hiện. Nút quay lại hoặc Đóng bỏ yêu cầu. Tin nhập không đồng thời gửi sang AI.
Khách đang chat với tài khoản Business không có công cụ sửa tài khoản, chuyển
quà/Sao hoặc xóa tin thông qua nội dung chat hay file họ gửi.

## Lời nhắn và hẹn cho chủ tài khoản

Khi người liên hệ nói rõ muốn nhắn lại, ghi nhớ hoặc hẹn chủ tài khoản, AI có thể
ghi một lời nhắn bền vững theo đúng tin nguồn. Bot gửi lời nhắn đó vào **DM riêng
của đúng chủ tài khoản đã kết nối**; không gửi sang người khác, nhóm hay Discord.
Nếu có ngày giờ rõ ràng, lời nhắc sẽ được gửi vào hoặc sau thời điểm đó. Thiếu hay
mơ hồ ngày giờ thì AI hỏi lại, không tự đặt lịch. Việc gửi chỉ được xác nhận sau
khi Telegram nhận; lỗi không chắc chắn không bị tự gửi lặp lại.

Trong `/business → Xem chat → Xem lời nhắn`, chủ tài khoản xem các lời nhắn gần đây và có thể hủy
lời nhắc còn chờ rồi bấm **Lưu**. Tắt công tắc **Lời Nhắn** ngăn các yêu cầu mới;
những bản đã lưu vẫn chỉ hiện trong bảng của đúng chủ tài khoản.

## Quyền Telegram và thao tác tương ứng

Đối chiếu [BusinessBotRights chính thức](https://core.telegram.org/bots/api#businessbotrights).
Menu **Quyền Telegram** hiển thị đủ 14 quyền thực tế của kết nối. Các nút có dấu
✅/❌ theo quyền Telegram đã cấp; bật Quyền quản lý trong Waku không tự cấp thêm quyền.

| Quyền | Thao tác trong Waku |
| --- | --- |
| `can_reply` | AI trả lời; gửi text/file; sửa tin bot gửi |
| `can_read_messages` | Tự đánh dấu đọc hoặc đánh dấu theo ID tin |
| `can_delete_sent_messages` | Xóa tin chính bot đã gửi |
| `can_delete_all_messages` | Xóa các tin trong cùng chat được quản lý |
| `can_edit_name` | Đổi tên và họ |
| `can_edit_bio` | Đổi/xóa giới thiệu |
| `can_edit_profile_photo` | Đổi/xóa avatar |
| `can_edit_username` | Đổi/xóa username |
| `can_view_gifts_and_stars` | Xem quà sở hữu và số dư Sao |
| `can_convert_gifts_to_stars` | Đổi quà thường thành Sao |
| `can_change_gift_settings` | Nút quà và các loại quà nhận, gồm quà từ kênh |
| `can_transfer_and_upgrade_gifts` | Chuyển hoặc nâng cấp quà |
| `can_transfer_stars` | Chuyển Sao **về số dư của bot**; trả Sao khi chuyển/nâng cấp quà |
| `can_manage_stories` | Đăng, sửa, xóa story |

SDK dùng `can_view_gifts`/`can_sell_gifts` cho hai quyền xem/đổi quà; Waku ánh xạ
về tên Bot API ở bảng trên. Đọc/cập nhật Business vẫn dùng client native. Thao
tác quản lý dùng các phương thức Business chính thức của Bot API, luôn gắn đúng
`business_connection_id`, không mở `getUpdates` song song và không cần user session.

Gửi/sửa tin và đánh dấu đọc chịu giới hạn chat hoạt động 24 giờ của Telegram.
Xóa tin cần ID đã được bot quan sát trong đúng chat; xóa/sửa tin bot chỉ chấp nhận
tin của chính bot. Story sửa/xóa phải do bot đăng; Telegram kiểm tra quyền sở hữu.
Các hạn chế về định dạng, phí, số lần đổi username hay loại quà vẫn do Telegram
quyết định. Không hứa một thao tác đã xong nếu chưa có xác nhận từ API.

Chuyển/nâng cấp quà nhập **đúng số Sao đồng ý trả**; `0` chỉ dùng luồng miễn phí
hoặc đã trả trước. Không tự dò rồi trả phí cao hơn. Quyền dùng Sao được kiểm tra
thêm khi phí dương. Mỗi yêu cầu đã xem lại chỉ được gửi một lần; timeout không
tự gửi lại, kể cả sau restart. Chủ tài khoản kiểm tra kết quả trên Telegram trước
khi tạo yêu cầu mới. Tin, quà và story được thực thi sau khi bấm Lưu, không tự
thực hiện chỉ vì người lạ nhắc đến chúng.

## Giới hạn lịch sử hội thoại

**Quyền `can_read_messages` là đánh dấu đã đọc**, không phải quyền tải toàn bộ
tin cũ. [Danh sách phương thức Business được Telegram cho phép](https://core.telegram.org/api/bots/connected-business-bots)
không có `messages.getHistory`. Waku chỉ giữ tin thực sự được giao cho bot và
reply reference Telegram cung cấp. Hội thoại trước lúc bot bắt đầu quan sát không
được tự động tải về; AI được nhắc không coi thiếu lịch sử là lần đầu hai người
từng nói chuyện.

Phạm vi chat, chat bị loại trừ và việc kết nối bị tắt do chủ tài khoản chọn trong
Telegram. Waku không mở rộng được phạm vi này. Database lưu nội dung Business
như dữ liệu riêng của từng tài khoản; giữ file database và bản sao lưu ở nơi chủ
máy kiểm soát được.
