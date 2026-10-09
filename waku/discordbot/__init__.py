"""Optional Discord lifecycle; importing this package does not import either SDK."""

import sys


async def start_discord_bot() -> None:
    from waku.config import app_config

    if not app_config.discord_enabled:
        await stop_discord_bot()
        return
    from .runtime import start_discord_bot as start

    await start()


async def stop_discord_bot() -> None:
    runtime = sys.modules.get(__name__ + ".runtime")
    if runtime is not None:
        await runtime.stop_discord_bot()


def get_discord_runtime_status() -> str:
    runtime = sys.modules.get(__name__ + ".runtime")
    return runtime.get_discord_runtime_status() if runtime is not None else "offline"

__all__ = ["get_discord_runtime_status", "start_discord_bot", "stop_discord_bot"]
