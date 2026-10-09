<div align="center">
<img src="docs/docs/images/miyaneko.jpg" alt="Linh vật mèo của waku" width="240">

# waku bot

Bot Telegram đa chức năng, có trợ lý AI và bảng quản trị Mini App.
</div>

Tiếng Việt là ngôn ngữ mặc định. Bot hỗ trợ quản lý nhóm, xác minh thành viên,
RSS, quote, quà tặng, meme và AI agent.

Tên nhánh `v2` chỉ thể hiện thế hệ thiết kế thứ hai, không mang ý nghĩa phiên bản
major theo Semantic Versioning. Dự án có thể có thay đổi không tương thích;
hãy đọc lịch sử commit và sao lưu cấu hình, cơ sở dữ liệu, dữ liệu phiên trước khi cập nhật.

## Tài liệu

- [Giới thiệu](docs/docs/index.md)
- [Triển khai](docs/docs/self-host.md)
- [Bảng quản trị Mini App](docs/docs/webapp.md)
- [Quốc tế hóa và tiếng Việt](docs/docs/i18n.md)
- [Đổi tên dự án và múi giờ](docs/docs/branding-timezone.md)

## Chạy bot

Điền `token` và `owners` trong [settings.toml](settings.toml), rồi chạy từ thư mục dự án:

```bash
docker compose up -d --build
docker compose logs -f waku
```

Docker Compose build image `waku-bot:local` từ source hiện tại. Hướng dẫn chạy trực
tiếp bằng Python 3.13 và các điều kiện triển khai nằm trong tài liệu triển khai.

Múi giờ mặc định là `Asia/Ho_Chi_Minh` (UTC+7). Ngôn ngữ mặc định là `vi`; dữ liệu
ngôn ngữ đã lưu của người dùng và nhóm được giữ lại, có thể đổi bằng `/lang`.
Tên hiển thị của bot là `waku`; tên repository và phân phối Python là `waku-bot`.
Namespace Python `kmua` và lệnh `python -m kmua` được giữ để các import và
entrypoint tiếp tục hoạt động.

## Giấy phép và nguồn gốc

Source được phát hành theo [GNU AGPL v3](LICENSE). Dự án phát triển từ
[source upstream của Krau](https://github.com/krau/kmua-bot); giữ nguyên ghi nhận
tác giả và những người đóng góp bên dưới.

## Người đóng góp

<!-- readme: contributors -start -->
<table>
	<tbody>
		<tr>
            <td align="center">
                <a href="https://github.com/krau">
                    <img src="https://avatars.githubusercontent.com/u/71133316?v=4" width="100;" alt="krau"/>
                    <br />
                    <sub><b>Krau</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/mokurin000">
                    <img src="https://avatars.githubusercontent.com/u/34085039?v=4" width="100;" alt="mokurin000"/>
                    <br />
                    <sub><b>mokurin000</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/tjsky">
                    <img src="https://avatars.githubusercontent.com/u/7272911?v=4" width="100;" alt="tjsky"/>
                    <br />
                    <sub><b>tjsky</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/NyanWhite">
                    <img src="https://avatars.githubusercontent.com/u/51278093?v=4" width="100;" alt="NyanWhite"/>
                    <br />
                    <sub><b>NyanWhite</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/ames0k0">
                    <img src="https://avatars.githubusercontent.com/u/26835631?v=4" width="100;" alt="ames0k0"/>
                    <br />
                    <sub><b>YóUnǎi</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/ImgBotApp">
                    <img src="https://avatars.githubusercontent.com/u/31427850?v=4" width="100;" alt="ImgBotApp"/>
                    <br />
                    <sub><b>Imgbot</b></sub>
                </a>
            </td>
		</tr>
		<tr>
            <td align="center">
                <a href="https://github.com/real-LiHua">
                    <img src="https://avatars.githubusercontent.com/u/65490624?v=4" width="100;" alt="real-LiHua"/>
                    <br />
                    <sub><b>Li Hua</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/Mufanc">
                    <img src="https://avatars.githubusercontent.com/u/47652878?v=4" width="100;" alt="Mufanc"/>
                    <br />
                    <sub><b>Mufanc</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/ricky8955555">
                    <img src="https://avatars.githubusercontent.com/u/24487646?v=4" width="100;" alt="ricky8955555"/>
                    <br />
                    <sub><b>Phrinky</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/leafmoes">
                    <img src="https://avatars.githubusercontent.com/u/44945631?v=4" width="100;" alt="leafmoes"/>
                    <br />
                    <sub><b>leafmoes</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/AHCorn">
                    <img src="https://avatars.githubusercontent.com/u/42889600?v=4" width="100;" alt="AHCorn"/>
                    <br />
                    <sub><b>AHCorn</b></sub>
                </a>
            </td>
		</tr>
	</tbody>
</table>
<!-- readme: contributors -end -->
