"""Scope the Telegram pause switch to native chats, retaining admin DMs."""

from pyrogram.enums import ChatType

from waku.common.telegram_authority import is_bot_admin
from waku.config import app_config


def telegram_output_allowed(message, actor=None) -> bool:
    # Guest chat IDs can alias ordinary private chats. Never grant the DM
    # exception based on a Guest caller's identity or an unrelated chat ID.
    if getattr(message, "guest_query_id", None):
        return app_config.telegram_enabled
    if getattr(message, "business_connection_id", None):
        return True  # Business has its own live switch and connection checks.
    if app_config.telegram_enabled:
        return True
    actor = actor or getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    return bool(
        chat
        and chat.type == ChatType.PRIVATE
        and actor
        and not getattr(actor, "is_bot", False)
        and getattr(message, "_telegram_admin_dm_actor_id", None) == actor.id
    )


async def prepare_telegram_output(message, actor=None) -> bool:
    """Refresh operator authority before a native DM response or tool write."""
    actor = actor or getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    if (
        not getattr(message, "guest_query_id", None)
        and not getattr(message, "business_connection_id", None)
        and chat
        and chat.type == ChatType.PRIVATE
    ):
        if app_config.telegram_enabled and getattr(
            message, "_telegram_admin_dm_checked_actor_id", None
        ) == getattr(actor, "id", None):
            return True
        message._telegram_admin_dm_actor_id = (
            actor.id
            if actor
            and not getattr(actor, "is_bot", False)
            and await is_bot_admin(actor.id)
            else None
        )
        message._telegram_admin_dm_checked_actor_id = getattr(actor, "id", None)
    return telegram_output_allowed(message, actor)


async def telegram_chat_allowed(chat_id: int) -> bool:
    """Scheduled native messages may still reach a bot admin's private chat."""
    return app_config.telegram_enabled or (chat_id > 0 and await is_bot_admin(chat_id))
