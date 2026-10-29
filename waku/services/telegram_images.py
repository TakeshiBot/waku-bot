"""Telegram anime-image policy shared by commands, links and AI tools."""

from waku import database
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
    # Private chats have no group policy. Narrow mixed mode only when the
    # current request explicitly asks for Safe/R18 (resolve_image_search).
    return True, 2


async def telegram_image_context(chat_id: int) -> str:
    """Refresh the actual conversation and image policy on every AI turn."""
    enabled, mode = await telegram_image_settings(chat_id)
    if chat_id > 0:
        return (
            "Current Telegram conversation: private DM, not a group. "
            "Catalog image mode: mixed (Safe and R18 allowed). An ordinary image "
            "request uses random mixed images; an explicit R18/Safe request or "
            "tag must be passed to send_anime_photo so it selects that rating/tag. "
            "Do not apply group image settings or repeat an old group-lock claim. "
            "Use the image tool before reporting availability or success."
        )
    policy = {0: "Safe only", 1: "R18 only", 2: "mixed (Safe and R18)"}[mode]
    return (
        f"Current Telegram conversation: group. Catalog images: "
        f"{policy if enabled else 'disabled by group settings'}. "
        "Use the image tool to check the current request; do not infer policy "
        "from old replies in conversation history."
    )


async def generated_image_allowed(chat_id: int, prompt: str) -> tuple[bool, str | None]:
    enabled, mode = await telegram_image_settings(chat_id)
    if not enabled:
        return False, "image_disabled"
    if not image_allowed(contains_r18_request(prompt), mode):
        return False, "image_mode_mismatch"
    return True, None
