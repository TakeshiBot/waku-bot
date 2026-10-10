"""Remove saved Telegram menus and their exact command after five seconds."""

import asyncio

from waku import common
from waku.logger import logger

SAVE_AUTO_DELETE_SECONDS = 5


async def delete_saved_menu(client, chat_id, menu_id, command_id=None):
    await asyncio.sleep(SAVE_AUTO_DELETE_SECONDS)
    for message_id in dict.fromkeys((menu_id, command_id)):
        if type(message_id) is not int or message_id <= 0:
            continue
        try:
            await client.delete_messages(chat_id, message_id)
        except Exception as error:
            # Removing our own menu still works when the bot cannot remove
            # the user's command in a group. Never guess from forum reply IDs.
            logger.debug("Saved-menu cleanup failed: {}", type(error).__name__)


def schedule_saved_menu_cleanup(client, message, command_id=None):
    chat = getattr(message, "chat", None)
    menu_id = getattr(message, "id", None)
    if chat is None or type(chat.id) is not int or type(menu_id) is not int:
        return
    common.spawn(
        delete_saved_menu(client, chat.id, menu_id, command_id),
        name=f"saved-menu:{chat.id}:{menu_id}",
    )
