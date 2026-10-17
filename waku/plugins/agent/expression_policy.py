"""Expression switches for ordinary Telegram chats, never Business chats."""

from pyrogram.enums import ChatType

from waku import database
from waku.logger import logger


async def expression_settings(message, chat_id=None) -> tuple[bool, bool]:
    if message is None or getattr(message, "business_connection_id", None):
        return False, False
    chat = getattr(message, "chat", None)
    chat_id = chat_id if chat_id is not None else getattr(chat, "id", None)
    if chat_id is None:
        return False, False
    chat_type = getattr(chat, "type", None)
    if chat_type == ChatType.PRIVATE or (chat_type is None and chat_id > 0):
        return True, True
    if chat_type not in (None, ChatType.GROUP, ChatType.SUPERGROUP) or chat_id >= 0:
        return False, False
    try:
        config = await database.get_chat_config(chat_id)
    except Exception as error:
        logger.warning("Expression settings unavailable: {}", type(error).__name__)
        return False, False
    return config.sticker_memory_enabled, getattr(config, "reactions_enabled", True)
