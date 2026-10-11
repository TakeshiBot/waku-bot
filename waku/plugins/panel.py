"""Entry points into the Mini App panel.

Group links open the registered Mini App directly in the current chat. The group
hint is navigation only; API permissions remain checked. Legacy private /start
links remain supported for previously sent menus.
"""

import asyncio
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pyrogram
from pyrogram.client import Client
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from waku import common, database
from waku.config import app_config
from waku.i18n import i18n
from waku.webapp.auth import build_chat_start_param

_registration_checked: tuple[int, float] | None = None


async def refresh_panel_registration(client) -> None:
    """Refresh Telegram's Main Mini App flag after BotFather changes, at most every minute."""
    global _registration_checked
    if not panel_available() or getattr(client, "is_connected", False) is not True:
        return
    now = time.monotonic()
    if (
        _registration_checked
        and _registration_checked[0] == id(client)
        and now - _registration_checked[1] < 60
    ):
        return
    _registration_checked = (id(client), now)
    try:
        me = await asyncio.wait_for(client.get_me(), 5)
        if me and me.id == client.me.id:
            client.me = me
    except Exception:
        # A transient Telegram error must not disable a previously valid menu.
        pass


def panel_available() -> bool:
    """Whether the configured panel has a usable HTTPS launch URL."""
    from waku.bot.client import client

    if not (app_config.webapp and app_config.webapp_url):
        return False
    try:
        url = urlsplit(app_config.webapp_url)
    except ValueError:
        return False
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        return False
    bot_username = client.me.username if client.me else None
    return bool(bot_username)


def chat_panel_url(chat_id: int) -> str | None:
    """A deep link to `chat_id`'s page in the panel, or None if unavailable."""
    from waku.bot.client import client

    if not panel_available():
        return None
    bot_username = client.me.username if client.me else None
    start_param = build_chat_start_param(chat_id)
    # Main Mini App is registered on the bot itself. An old default "panel"
    # short name must not send users to an unregistered /newapp instead.
    short_name = (
        ""
        if getattr(client.me, "has_main_web_app", False)
        else app_config.webapp_short_name.strip()
    )
    if not short_name and getattr(client.me, "has_main_web_app", None) is False:
        return None
    if short_name and re.fullmatch(r"[A-Za-z0-9_]+", short_name) is None:
        return None
    app_path = f"/{short_name}" if short_name else ""
    return f"https://t.me/{bot_username}{app_path}?startapp={start_param}"


def private_chat_panel_button(chat_id: int, lang: str) -> InlineKeyboardButton | None:
    """Launch in DM with a group navigation hint, without changing initData."""
    if not panel_available():
        return None
    url = urlsplit(app_config.webapp_url)
    query = [
        (key, value)
        for key, value in parse_qsl(url.query, keep_blank_values=True)
        if key != "waku_chat"
    ]
    query.append(("waku_chat", build_chat_start_param(chat_id)))
    launch_url = urlunsplit(
        (url.scheme, url.netloc, url.path, urlencode(query), url.fragment)
    )
    return InlineKeyboardButton(
        i18n.t("bot.button.chat_panel", locale=lang), web_app=WebAppInfo(url=launch_url)
    )


def chat_panel_button(chat_id: int, lang: str) -> InlineKeyboardButton | None:
    """The "configure in the panel" button for a group, or None if unavailable."""
    url = chat_panel_url(chat_id)
    if url is None:
        return None
    return InlineKeyboardButton(
        i18n.t("bot.button.chat_panel", locale=lang),
        url=url,
    )


@Client.on_message(pyrogram.filters.command("panel") & pyrogram.filters.group, group=0)
async def panel_group_cmd(client: Client, message: pyrogram.types.Message):
    """Open this group's page in the panel.

    Same permission check as /config, since it leads to the same settings. The reply
    carries the link rather than opening anything directly: a bot cannot open a Mini
    App on the user's behalf.
    """
    user = message.sender_chat or message.from_user
    chat = message.chat
    if not chat or chat.id is None or not user:
        return
    chat_config = await database.get_chat_config(chat)
    lang = chat_config.lang

    if not await common.can_user_manage_bot_in_chat(user, chat):
        await message.reply(text=i18n.t("bot.msg.no_permission_group", locale=lang))
        return

    await refresh_panel_registration(client)
    button = chat_panel_button(chat.id, lang)
    if button is None:
        await message.reply(
            text=i18n.t(
                "bot.msg.panel_app_unregistered"
                if panel_available()
                else "bot.msg.panel_unavailable",
                locale=lang,
            )
        )
        return

    await message.reply(
        text=i18n.t("bot.msg.chat_panel", locale=lang),
        reply_markup=InlineKeyboardMarkup([[button]]),
    )
