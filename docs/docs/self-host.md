# Hướng dẫn triển khai

Trước khi chạy bot, tắt Privacy Mode để bot nhận tin nhắn trong nhóm.
Bật Inline Mode và Inline Query Feedback. Thực hiện các thiết lập này trong
[@BotFather](https://t.me/BotFather).

## Chạy bằng Docker Compose

Cài Docker Engine và Docker Compose plugin trên VPS Linux theo
[hướng dẫn Docker](https://docs.docker.com/engine/install/). User chạy script cần
có quyền sử dụng Docker. Clone toàn bộ repository để build image từ source hiện tại:

```bash
git clone https://github.com/TakeshiBot/waku-bot.git
cd waku-bot
```

Chuẩn bị cấu hình rồi điền `token`, `owners` và các provider AI cần dùng:

```bash
bash run.sh init
nano settings.toml
bash run.sh deploy
bash run.sh logs
```

Gọi `bash run.sh` để mở menu. `deploy` build source đang có trên VPS, không tự
pull Git. Frontend được build trong Docker; không cần Python, Node hoặc pnpm trên
VPS. Image mặc định là `waku-bot:local`, dùng cache Docker cho các lần build tiếp.

Script kiểm tra cấu hình bằng image mới trước khi thay container đang chạy, sao lưu
dữ liệu nếu đã có instance/dữ liệu cũ, rồi chờ health check báo bot sẵn sàng.
Build hoặc kiểm tra cấu hình thất bại sẽ giữ container hiện tại chạy tiếp.
Script không tự prune Docker hay xóa dữ liệu khi thiếu dung lượng.

### Cấu hình và dữ liệu lưu trên VPS

`init` chuyển file riêng hiện có vào `config/` và giữ `settings.toml` ở thư mục gốc
bằng symlink. Có thể tiếp tục sửa bằng `nano settings.toml`. Nếu có `settings.dev.toml`,
file này cũng được chuyển và giữ symlink; nó là lớp cấu hình ghi đè sau cùng.
Không tạo hai bản cấu hình độc lập ở gốc và `config/`.

Compose mount cả thư mục `config/` vào `/app/config`, thay vì mount riêng từng file,
để thao tác Lưu trong `/config` có thể thay file bằng một lần ghi nguyên tử.
Các thư mục được giữ khi tạo lại container:

| Đường dẫn trên VPS | Nội dung |
| --- | --- |
| `config/` | Cấu hình riêng đang chạy |
| `data/` | Database SQLite, phiên Telegram, cache và dữ liệu bot |
| `logs/` | Log ứng dụng |
| `.agentfs/` | Workspace của AI |
| `.backups/` | Cấu hình trước khi chuyển và các bản sao lưu `.tar.gz` |

Các đường dẫn riêng này được Git/Docker build bỏ qua. `backup` tạm dừng container
để sao lưu SQLite/session nhất quán, rồi khởi động lại nếu trước đó đang chạy.
Nếu dùng PostgreSQL/MySQL hoặc đường dẫn dữ liệu tùy chỉnh ngoài các thư mục trên,
cần sao lưu chúng riêng. Khi khôi phục, dừng bot, giải nén bản sao lưu vào thư mục
project, kiểm tra cấu hình và chạy lại; không dùng `docker compose down -v` để cập nhật.

### Các lệnh quản lý

| Lệnh | Thao tác |
| --- | --- |
| `bash run.sh update` | Pull Git fast-forward rồi triển khai; từ chối nếu source có thay đổi local |
| `bash run.sh deploy` | Build và triển khai source hiện tại; dùng cho cả source tải bằng ZIP |
| `bash run.sh start` | Chạy image đã build |
| `bash run.sh restart` | Kiểm tra cấu hình và tạo lại container để nhận cấu hình/env mới |
| `bash run.sh stop` | Dừng bot, giữ dữ liệu |
| `bash run.sh status` | Xem trạng thái và health |
| `bash run.sh logs 200` | Theo dõi 200 dòng log cuối |
| `bash run.sh check` | Kiểm tra cấu hình, không kết nối Telegram/Discord |
| `bash run.sh backup` | Sao lưu cấu hình và dữ liệu local |
| `bash run.sh disk` | Xem dung lượng Docker và ổ đĩa |
| `bash run.sh help` | Xem đầy đủ tùy chọn |

Compose giữ `network_mode: host` cho VPS Linux, hỗ trợ database/proxy ở localhost.
HTTP mặc định chỉ nghe `127.0.0.1:8180`; Nginx trên VPS cung cấp HTTPS. Cổng, địa chỉ
lắng nghe và health server được Compose đặt qua env, ưu tiên hơn giá trị TOML.
Không khai báo mapping `ports` vì mạng host đã dùng trực tiếp cổng trên VPS.

Có thể thay cổng, project hoặc image bằng biến môi trường shell; giữ cùng giá trị
cho mọi lần gọi script, ví dụ thêm các dòng `export` vào profile riêng trên VPS:

```bash
export WAKU_PORT=8280
export WAKU_PROJECT_NAME=waku-bot
export WAKU_IMAGE_NAME=waku-bot:local
bash run.sh deploy
```

`HEALTH_TIMEOUT` mặc định 240 giây, `MIN_FREE_GB` mặc định 5 GiB. Khi chạy qua script,
các biến quản lý trên lấy từ shell, không đọc từ `.env`. Không chạy hai project dùng
cùng token Telegram hoặc cùng `data/`. Dockerfile chọn binary landrun tương ứng
AMD64/ARM64; agent shell vẫn phụ thuộc hỗ trợ Landlock của kernel VPS.

### Mini App, Nginx và HTTPS

Trỏ DNS của domain về VPS và cho phép TCP 80/443. Trên Ubuntu/Debian, cài công cụ
trước khi thiết lập (script không tự cài package hệ thống):

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx
export CERTBOT_EMAIL=you@example.com
bash run.sh miniapp panel.example.com
```

Hoặc dùng `bash run.sh all panel.example.com` để deploy rồi thiết lập HTTPS.
Script tạo virtual host riêng, kiểm tra Nginx, xin chứng chỉ bằng
[Certbot Nginx plugin](https://eff-certbot.readthedocs.io/en/stable/using.html#nginx),
lưu `webapp = true` và URL HTTPS bằng settings editor của base hiện tại, rồi chạy lại
container. Script từ chối ghi đè virtual host không do nó quản lý; bản cấu hình Nginx
trước khi thay được lưu cạnh file gốc. Nếu xin chứng chỉ thất bại, cấu hình bot chưa
bị đổi; kiểm tra DNS/cổng rồi chạy lại. Đăng ký URL và short name trong BotFather theo
[hướng dẫn Mini App](webapp.md).

### Nâng cấp từ bản cũ

Dịch vụ Compose đã đổi tên từ `kmua` sang `waku`. Dừng instance cũ trước
khi khởi động instance mới để hai container không cùng dùng phiên Telegram và
cơ sở dữ liệu. Trong thư mục/project Compose cũ, chạy:

```bash
docker compose down --remove-orphans
```

Sau đó chạy `bash run.sh init` và `bash run.sh deploy` với source mới. Lệnh dừng trên không
xóa các thư mục bind mount `data/`, `logs/` hoặc file `settings.toml`. Nếu đổi thư
mục dự án hoặc tên project Compose, cần dừng project cũ riêng và chuyển dữ liệu
đã sao lưu sang đường dẫn bind mount của project mới.

Múi giờ mặc định của bot và container là `Asia/Ho_Chi_Minh` (UTC+7).
Xem [ghi chú múi giờ và đổi tên](branding-timezone.md) nếu triển khai từ bản cũ.

Để dùng bảng quản trị, thiết lập HTTPS theo [hướng dẫn Mini App](webapp.md).

## Chạy trực tiếp từ source

Dự án yêu cầu **Python 3.13** theo `pyproject.toml`. Cài `graphviz` trên hệ
thống để tạo sơ đồ quan hệ. Chạy trực tiếp trên Linux vì dependency `uvloop`
không hỗ trợ Windows. AI agent shell cũng dùng Landlock/landrun trên Linux;
Docker là cách triển khai sẵn có của dự án.

```bash
git clone https://github.com/TakeshiBot/waku-bot.git
cd waku-bot
uv sync --frozen
```

Tạo `settings.toml` từ `settings.ex.toml` nếu chưa có, điền cấu hình như hướng dẫn
ở trên, rồi chạy:

```bash
mkdir -p data
uv run --no-sync python -m waku
```

Tên phân phối là `waku-bot`; thư mục và module Python là `waku`.
Thư mục `data/` phải tồn tại trước khi mở database SQLite mặc định.
Lịch chạy đã lưu tự chuyển tham chiếu module cũ sang `waku` khi khởi động;
xem [ghi chú nâng cấp](branding-timezone.md) để biết các giá trị tương thích được giữ lại.

## Cấu hình AI provider

Model dùng dạng `tên-provider/tên-model`, ví dụ `default/gpt-4o-mini` sẽ lấy URL
và key từ bảng `[agent_providers.default]`. Tên model có thể chứa thêm dấu `/`;
chỉ phần trước dấu `/` đầu tiên là tên provider.

```toml
agent = true
agent_model = "default/gpt-4o-mini"

# Đặt các cấu hình chung khác ở phía trên các bảng provider.
[agent_providers.default]
url = "https://api.openai.com/v1"
key = "YOUR_API_KEY"
type = "chat_completions"
```

`type` chọn API chat (`chat_completions` hoặc `responses`). `api_type` chọn
họ API (`openai` hoặc `ollama`); mặc định là `openai`. Không cần khai báo URL/key
AI lần nữa ở phần cấu hình chung. Đặt các bảng provider cuối file để những cấu
hình như `manyacg_*` không bị đưa nhầm vào bảng provider.

Khi chạy bot trong WSL và API ở Windows, chế độ mạng NAT cần URL dùng IP của
Windows thay cho `127.0.0.1`. Trong Ubuntu, lấy địa chỉ bằng `ip route show default`
(IP sau từ `via`); dùng IP đó cùng port API hiện tại. Địa chỉ có thể đổi khi môi
trường mạng khởi động lại. Xem [hướng dẫn mạng WSL của Microsoft](https://learn.microsoft.com/en-us/windows/wsl/networking).
