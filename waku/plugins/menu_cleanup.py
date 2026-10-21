"""Expire bot-owned menus without deleting the user's command."""

import asyncio
import time

from waku import common
from waku.logger import logger

SAVE_AUTO_DELETE_SECONDS = 5
MENU_TIMEOUT_SECONDS = 15 * 60
_timers = {}


def watch_menu(client, message, *, session=None, ttl=MENU_TIMEOUT_SECONDS):
    """Watch only the outgoing bot menu; never its replied-to command."""
    chat = getattr(message, "chat", None)
    message_id = getattr(message, "id", None)
    if chat is None or type(chat.id) is not int or type(message_id) is not int:
        return
    watch_menu_id(client, chat.id, message_id, session=session, ttl=ttl)


def watch_menu_id(
    client, chat_id, message_id, *, session=None, ttl=MENU_TIMEOUT_SECONDS
):
    if (
        client is None
        or type(chat_id) is not int
        or type(message_id) is not int
        or message_id <= 0
    ):
        return
    key = (id(client), chat_id, message_id)
    previous = _timers.pop(key, None)
    if previous is not None:
        previous.cancel()
    deadline = time.monotonic() + ttl

    async def expire():
        try:
            while True:
                remaining = getattr(session, "expires", deadline) - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)
            await client.delete_messages(chat_id, message_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.debug("Expired-menu cleanup failed: {}", type(error).__name__)
        finally:
            if _timers.get(key) is asyncio.current_task():
                _timers.pop(key, None)

    _timers[key] = common.spawn(expire(), name=f"menu-expiry:{chat_id}:{message_id}")


async def delete_saved_menu(client, chat_id, menu_id, command_id=None):
    await asyncio.sleep(SAVE_AUTO_DELETE_SECONDS)
    for message_id in (menu_id,):
        if type(message_id) is not int or message_id <= 0:
            continue
        try:
            await client.delete_messages(chat_id, message_id)
        except Exception as error:
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
