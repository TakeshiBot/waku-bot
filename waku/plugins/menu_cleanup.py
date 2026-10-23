"""Expire menus; also remove group commands when menus are saved or closed."""

import asyncio
import time

from waku import common
from waku.common.utils import GROUP_CHAT_TYPES
from waku.logger import logger

SAVE_AUTO_DELETE_SECONDS = 5
MENU_TIMEOUT_SECONDS = 15 * 60
_timers = {}


def stop_watching_menu(client, message):
    """Keep a menu that has become a durable restart/update receipt."""
    chat = getattr(message, "chat", None)
    message_id = getattr(message, "id", None)
    if chat is None or type(message_id) is not int:
        return
    timer = _timers.pop((id(client), chat.id, message_id), None)
    if timer is not None:
        timer.cancel()


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


async def _delete_menu_messages(client, chat_id, menu_id, command_id=None):
    timer = _timers.pop((id(client), chat_id, menu_id), None)
    if timer is not None:
        timer.cancel()
    message_ids = []
    for message_id in (menu_id, command_id):
        if type(message_id) is not int or message_id <= 0 or message_id in message_ids:
            continue
        message_ids.append(message_id)
    for message_id in message_ids:
        try:
            await client.delete_messages(chat_id, message_id)
        except Exception as error:
            logger.debug("Menu cleanup failed: {}", type(error).__name__)


def _group_command_id(message, command_id):
    chat = getattr(message, "chat", None)
    if getattr(chat, "type", None) not in GROUP_CHAT_TYPES:
        return None
    if command_id is None:
        command_id = getattr(message, "reply_to_message_id", None)
    return command_id


async def delete_menu(client, message, command_id=None):
    """Close a menu immediately, removing its originating command only in groups."""
    chat = getattr(message, "chat", None)
    menu_id = getattr(message, "id", None)
    if chat is None or type(chat.id) is not int or type(menu_id) is not int:
        return
    timer = _timers.pop((id(client), chat.id, menu_id), None)
    if timer is not None:
        timer.cancel()
    try:
        await message.delete()
    except Exception as error:
        logger.debug("Menu cleanup failed: {}", type(error).__name__)

    command_id = _group_command_id(message, command_id)
    if type(command_id) is not int or command_id <= 0 or command_id == menu_id:
        return
    try:
        await client.delete_messages(chat.id, command_id)
    except Exception as error:
        logger.debug("Group command cleanup failed: {}", type(error).__name__)


async def delete_saved_menu(client, chat_id, menu_id, command_id=None):
    """Delete messages already scoped by schedule_saved_menu_cleanup."""
    await asyncio.sleep(SAVE_AUTO_DELETE_SECONDS)
    await _delete_menu_messages(client, chat_id, menu_id, command_id)


def schedule_saved_menu_cleanup(client, message, command_id=None):
    chat = getattr(message, "chat", None)
    menu_id = getattr(message, "id", None)
    if chat is None or type(chat.id) is not int or type(menu_id) is not int:
        return
    common.spawn(
        delete_saved_menu(
            client, chat.id, menu_id, _group_command_id(message, command_id)
        ),
        name=f"saved-menu:{chat.id}:{menu_id}",
    )
