"""Check deployment settings offline; optionally enable a configured Mini App."""

import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit


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
        if not app_config.token.strip() or ":" not in app_config.token:
            print("Cần điền token Telegram hợp lệ trong settings.toml.", file=sys.stderr)
            return 1
        if not app_config.owners or any(owner <= 0 for owner in app_config.owners):
            print("Cần điền owners bằng ID Telegram của admin bot.", file=sys.stderr)
            return 1
        if app_config.discord_enabled and not app_config.discord_token.strip():
            print("discord_enabled đang bật nhưng thiếu discord_token.", file=sys.stderr)
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
    except Exception:
        # Pydantic/Dynaconf exception text can include tokens, keys and URLs.
        print(
            "Không kiểm tra/lưu được cấu hình. Kiểm tra cú pháp TOML, kiểu dữ liệu, "
            "provider/model, file phụ trợ và quyền ghi thư mục config/.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
