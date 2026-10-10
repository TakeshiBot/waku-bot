# Telegram Business

Waku nhận tin nhắn văn bản của tài khoản Telegram đã kết nối và trả lời thay mặt
tài khoản đó, theo quyền được cấp. Phần Business dùng AI provider, cách gửi tin,
streaming và giới hạn của base hiện tại.

## Kết nối và bật

1. Trong **BotFather**, bật **Secretary Mode** cho bot (một số giao diện ghi
   **Business Mode**).
2. Trong cài đặt tài khoản Telegram, kết nối Waku ở phần bot Business, chọn các
   cuộc chat cho bot truy cập và cấp quyền trả lời.
3. Thêm cấu hình ở **cấp gốc** của `settings.toml`, trước `[agent_providers.*]`:

   ```toml
   business_chat_enabled = true
   agent = true
   agent_model = "default/ten-model"
   ```

Business dùng `agent_model`,
`agent_streaming`, `agent_rich_output`, timeout và giới hạn AI của bot hiện tại;
không cần token hoặc URL/key AI khác.

Sau khi cập nhật source, khởi động lại `python -m waku` một lần để nạp handler.
Chủ bot hoặc quản trị viên toàn cục có thể vào **DM với bot → /config → Cài đặt →
trang tiếp theo → Telegram Business**, rồi bật/tắt `business_chat_enabled`.
Công tắc được lưu và áp dụng ngay, không cần khởi động lại. Những thay đổi model,
provider và prompt vẫn theo cơ chế cấu hình của base hiện tại.

Trong mục **Telegram Business**, bấm **Sửa Prompt** để nhập nội dung riêng,
**Prompt Riêng** để bật/tắt rồi bấm **Lưu**. Khi bật và nội dung không trống,
Business chỉ dùng prompt riêng này, không ghép thêm `agent_prompt`. Khi tắt
hoặc để trống, Business dùng prompt chính. Tắt không xoá nội dung đã lưu;
thay đổi áp dụng cho lượt tiếp theo, không cần khởi động lại.
Nút **Đóng** trong menu admin bỏ các thay đổi chưa lưu.

Hai mục tương ứng trong file cấu hình, đặt cùng `business_chat_enabled`:

```toml
business_chat_prompt_enabled = true
business_chat_prompt = '''Bạn là trợ lý trả lời khách hàng của tôi.'''
```

Telegram yêu cầu kết nối còn bật và quyền `can_reply`. Quyền gửi/sửa tin áp dụng
cho cuộc chat riêng có tin đến trong 24 giờ gần nhất. Xem
[hướng dẫn Business Bots chính thức](https://core.telegram.org/bots/features#business-bots).

## Hành vi

- Trả lời tin văn bản đến; bỏ qua lệnh bắt đầu bằng `/`, tin đi và tin từ bot hoặc
  chủ tài khoản đã kết nối, như phần Business của bản mod.
- Lịch sử AI được tách theo kết nối Business và cuộc chat, không dùng lịch sử DM
  thường, nhóm Telegram hoặc Discord.
- Khi bật streaming, gửi phần văn bản đầu tiên ngay khi AI sinh ra và sửa cùng
  tin đó khoảng 2 giây một lần. Kết quả cuối thay thế tin xem trước, giữ rich
  message nếu bật; chỉ gửi tin bổ sung khi nội dung vượt giới hạn Telegram.
  Mọi thao tác gửi/sửa đều đi qua đúng kết nối Business. Cách này không phụ
  thuộc vào khả năng hiển thị native draft của ứng dụng Telegram.
- Tắt công tắc hoặc thu hồi quyền kết nối sẽ ngăn bot tiếp tục trả lời. Chỉ tin mới
  được xử lý; phần này không tự trả lời lại tin đã sửa hoặc đã xoá.

Nếu chưa trả lời, kiểm tra công tắc Business, `agent`, model/provider, Secretary
Mode, phạm vi chat và quyền trả lời trong tài khoản Telegram. Kết nối Business
không dùng công tắc chat DM thường của bot.
