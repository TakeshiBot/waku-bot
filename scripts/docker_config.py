"""Check deployment settings offline; optionally enable a configured Mini App."""

import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit


def config_error_message(error: Exception) -> str:
    """Render known error codes without printing configuration input or secrets."""
    from waku.services.settings_editor import SettingsEditError

    if isinstance(error, SettingsEditError):
        messages = {
            "provider_in_use": "Có model tham chiếu provider chưa được khai báo. Kiểm tra các mục agent_*model và [agent_providers.*].",
            "invalid_value": "Có giá trị cấu hình sai kiểu hoặc không hợp lệ. Kiểm tra TOML, loại provider và tên model.",
            "invalid_file": "Không đọc được cấu hình hoặc cú pháp TOML sai. Kiểm tra config/settings.toml và settings.dev.toml nếu có.",
            "missing_file": "Không tìm thấy file cấu hình trong thư mục config/.",
            "write_failed": "Không ghi được cấu hình. Kiểm tra quyền ghi thư mục config/.",
            "stale_file": "File cấu hình vừa thay đổi trong lúc kiểm tra; chạy lại lệnh.",
        }
        message = messages.get(str(error))
        if message:
            return message
    if isinstance(error, PermissionError):
        return "Không có quyền đọc/ghi cấu hình. Kiểm tra quyền thư mục config/."
    return (
        "Không kiểm tra/lưu được cấu hình. Kiểm tra cú pháp TOML, kiểu dữ liệu, "
        "provider/model, file phụ trợ và quyền ghi thư mục config/."
    )


def image_config_error(config) -> str | None:
    """Image SDKs require a key at construction, before any bot connection."""
    if not config.agent:
        return None
    for field in ("agent_image_gen_model", "agent_image_edit_model"):
        spec = getattr(config, field)
        if not spec:
            continue
        name, separator, _ = spec.partition("/")
        name = name.strip() if separator else "default"
        provider = config.agent_providers.get(name)
        if provider is None:
            return (
                f"{field} dùng provider chưa được khai báo trong [agent_providers.*]."
            )
        if not provider.key.strip():
            return f"Provider của {field} thiếu key. Điền key hoặc để model trống nếu không sử dụng."
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--miniapp-url")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        # Import only the configuration/editor, never the bot clients or plugins.
        from waku.config import (
            ProviderConfig,
            _AppConfig,
            _resolve_settings_files,
            app_config,
        )
        from waku.services.settings_editor import SettingsEditor

        paths = [Path(path) for path in _resolve_settings_files()]
        if not paths:
            raise ValueError("missing_settings")
        editor = SettingsEditor(
            paths, _AppConfig, ProviderConfig, app_config.model_dump
        )
        editor.validate_for_restart()
        image_error = image_config_error(app_config)
        if image_error:
            print(image_error, file=sys.stderr)
            return 1
        if not app_config.token.strip() or ":" not in app_config.token:
            print(
                "Cần điền token Telegram hợp lệ trong settings.toml.", file=sys.stderr
            )
            return 1
        if not app_config.owners or any(owner <= 0 for owner in app_config.owners):
            print("Cần điền owners bằng ID Telegram của admin bot.", file=sys.stderr)
            return 1
        if app_config.discord_enabled and not app_config.discord_token.strip():
            print(
                "discord_enabled đang bật nhưng thiếu discord_token.", file=sys.stderr
            )
            return 1
        if args.miniapp_url:
            url = urlsplit(args.miniapp_url)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in {"", "/"}
            ):
                raise ValueError("invalid_miniapp_url")
            _, revision = editor.snapshot()
            editor.commit_batch(
                {"webapp": "true", "webapp_url": args.miniapp_url}, revision
            )
            print("Đã lưu URL HTTPS và bật Mini App.")
        print("Cấu hình hợp lệ. Không kết nối Telegram/Discord.")
        return 0
    except Exception as error:
        # Pydantic/Dynaconf exception text can include tokens, keys and URLs.
        print(config_error_message(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
