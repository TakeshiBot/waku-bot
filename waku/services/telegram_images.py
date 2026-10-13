"""Telegram anime-image policy shared by commands, links and AI tools."""

from waku import database
from waku.config import app_config
from waku.services.image_requests import contains_r18_request


def normalize_image_mode(value) -> int:
    return value if type(value) is int and value in (0, 1, 2) else 0


def image_allowed(r18, mode: int) -> bool:
    # Unknown API classifications must never be treated as safe images.
    if type(r18) is not bool:
        return False
    mode = normalize_image_mode(mode)
    return mode == 2 or r18 == (mode == 1)


async def telegram_image_settings(chat_id: int) -> tuple[bool, int]:
    if chat_id < 0:
        config = await database.get_chat_config(chat_id)
        return config.setu_enabled, normalize_image_mode(config.telegram_r18_mode)
    return True, normalize_image_mode(app_config.manyacg_r18_mode)


async def generated_image_allowed(chat_id: int, prompt: str) -> tuple[bool, str | None]:
    enabled, mode = await telegram_image_settings(chat_id)
    if not enabled:
        return False, "image_disabled"
    if not image_allowed(contains_r18_request(prompt), mode):
        return False, "image_mode_mismatch"
    return True, None
