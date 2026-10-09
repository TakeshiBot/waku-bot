"""Resolve the stored language for Telegram message presentation."""

from waku import database
from waku.common.utils import GROUP_CHAT_TYPES


async def message_locale(message) -> str:
    if message.chat and message.chat.type in GROUP_CHAT_TYPES:
        return (await database.get_chat_config(message.chat.id)).lang
    if message.from_user:
        return (await database.get_user_config(message.from_user.id)).lang
    return ""
