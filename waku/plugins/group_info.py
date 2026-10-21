"""Operator-only overview of groups already known to the bot."""

import asyncio
import html
import re
import time
from collections import OrderedDict
from dataclasses import dataclass

from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle, ChatMemberStatus, ChatType, ParseMode
from pyrogram.errors import MessageNotModified, RPCError
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
)

from waku import database
from waku.config import app_config
from waku.i18n import t
from waku.logger import logger
from waku.plugins.menu_cleanup import watch_menu

_PAGE_SIZE = 15
_ACCESS_TTL = 60
_ACCESS_LIMIT = 512
_PANEL_TTL = 15 * 60
_PANEL_LIMIT = 128
_ACCESS_TIMEOUT = 4
_access_cache: OrderedDict[tuple[int, int], tuple[float, str]] = OrderedDict()
_panels: OrderedDict = OrderedDict()
_access_slots = asyncio.Semaphore(3)
_sync_lock = asyncio.Lock()


@dataclass
class InfoPanel:
    user_id: int
    expires: float
    scope: str = "groups"


async def _actor(user):
    if user is None or getattr(user, "is_bot", False):
        return False, "vi"
    record = await database.get_user_by_id(user.id)
    locale = record.user_config.lang if record else "vi"
    allowed = user.id in app_config.owners or bool(
        record
        and record.is_bot_global_admin is True
        and not getattr(record, "is_blocked", False)
    )
    return allowed, locale


def _remember_panel(message, user_id, scope="groups", client=None):
    key = (message.chat.id, message.id)
    _panels[key] = InfoPanel(user_id, time.monotonic() + _PANEL_TTL, scope)
    if client is not None:
        watch_menu(client, message, session=_panels[key])
    _panels.move_to_end(key)
    while len(_panels) > _PANEL_LIMIT:
        _panels.popitem(last=False)


async def _access_status(client, chat_id, refresh=False):
    bot_id = getattr(getattr(client, "me", None), "id", 0)
    key = (bot_id, chat_id)
    cached = _access_cache.get(key)
    if not refresh and cached and cached[0] > time.monotonic():
        _access_cache.move_to_end(key)
        return cached[1]

    async def membership():
        async with _access_slots:
            return await client.get_chat_member(chat_id, "me")

    try:
        member = await asyncio.wait_for(membership(), timeout=_ACCESS_TIMEOUT)
        inaccessible = member.status in {
            ChatMemberStatus.LEFT,
            ChatMemberStatus.BANNED,
        } or (
            member.status == ChatMemberStatus.RESTRICTED
            and getattr(member, "is_member", None) is False
        )
        if inaccessible:
            status = "unavailable"
        elif member.status in {
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        } or (
            member.status == ChatMemberStatus.RESTRICTED
            and getattr(member, "is_member", None) is True
        ):
            status = "accessible"
        else:
            status = "unknown"
    except RPCError as exc:
        status = (
            "unavailable"
            if getattr(exc, "ID", "")
            in {
                "CHANNEL_PRIVATE",
                "CHANNEL_INVALID",
                "CHAT_ID_INVALID",
                "USER_NOT_PARTICIPANT",
                "PEER_ID_INVALID",
            }
            else "unknown"
        )
    except Exception as exc:
        logger.debug("Group access check failed: {}", type(exc).__name__)
        status = "unknown"
    _access_cache[key] = (time.monotonic() + _ACCESS_TTL, status)
    _access_cache.move_to_end(key)
    while len(_access_cache) > _ACCESS_LIMIT:
        _access_cache.popitem(last=False)
    return status


def _keyboard(page, pages, user_id, locale, scope="groups"):
    navigation = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                t("bot.info.previous", locale=locale),
                callback_data=f"group_info:{user_id}:{scope}:p:{page - 1}",
            )
        )
    navigation.append(
        InlineKeyboardButton(
            t("bot.info.refresh", locale=locale),
            callback_data=f"group_info:{user_id}:{scope}:r:{page}",
        )
    )
    navigation.append(
        InlineKeyboardButton(
            t("bot.info.close", locale=locale),
            callback_data=f"group_info:{user_id}:{scope}:x:{page}",
            style=ButtonStyle.DANGER,
        )
    )
    if page < pages:
        navigation.append(
            InlineKeyboardButton(
                t("bot.info.next", locale=locale),
                callback_data=f"group_info:{user_id}:{scope}:p:{page + 1}",
            )
        )
    tabs = [
        InlineKeyboardButton(
            ("✅ " if scope == selected else "")
            + t("bot.info.tab_" + selected, locale=locale),
            callback_data=f"group_info:{user_id}:{selected}:s:1",
        )
        for selected in ("groups", "guests")
    ]
    return InlineKeyboardMarkup([tabs, navigation])


def _format_group_line(index, chat, status, locale):
    raw_title = chat.title or str(chat.id)
    title = html.escape(raw_title[:40] + ("…" if len(raw_title) > 40 else ""))
    username = chat.username or ""
    handle = "@" + username if re.fullmatch(r"[A-Za-z0-9_]{1,32}", username) else "—"
    icon = {"accessible": "🟢", "unavailable": "🔴", "unknown": "⚪"}[status]
    return t("bot.info.group_line", locale=locale).format(
        index=index, title=title, id=chat.id, handle=handle, status=icon
    )


async def _reconcile_groups(client, refresh=False):
    """Backfill ordinary legacy records without promoting Guest deliveries."""
    async with _sync_lock:
        after_id = None
        while True:
            chats = await database.get_unclassified_telegram_groups(
                after_id=after_id, refresh=refresh
            )
            if not chats:
                break
            statuses = await asyncio.gather(
                *(_access_status(client, chat.id, refresh) for chat in chats)
            )
            for chat, status in zip(chats, statuses, strict=True):
                await database.record_telegram_presence(
                    chat,
                    is_member=None if status == "unknown" else status == "accessible",
                )
            after_id = chats[-1].id


async def _page(client, user_id, locale, page=1, refresh=False, scope="groups"):
    if scope == "groups":
        await _reconcile_groups(client, refresh)
    result = await database.get_telegram_presence_page(
        scope=scope, page=page, size=_PAGE_SIZE
    )
    pages = max(1, (result.total + result.size - 1) // result.size)
    statuses = ["accessible" if scope == "groups" else "unknown"] * len(result.items)
    lines = [
        t(
            "bot.info.header" if scope == "groups" else "bot.info.header_guests",
            locale=locale,
        ).format(total=result.total, page=result.page, pages=pages),
        "",
    ]
    if not result.items:
        lines.append(
            t(
                "bot.info.empty" if scope == "groups" else "bot.info.empty_guests",
                locale=locale,
            )
        )
    for index, (chat, status) in enumerate(
        zip(result.items, statuses, strict=True),
        start=(result.page - 1) * result.size + 1,
    ):
        lines.append(_format_group_line(index, chat, status, locale))
    return "\n".join(lines), _keyboard(result.page, pages, user_id, locale, scope)


@Client.on_message(filters.command("info") & (filters.private | filters.group), group=0)
async def info_command(client, message):
    if (
        message.chat is None
        or message.sender_chat
        or getattr(message, "guest_query_id", None)
    ):
        return
    allowed, locale = await _actor(message.from_user)
    if not allowed:
        await client.send_message(message.chat.id, t("bot.info.denied", locale=locale))
        return
    panel = await client.send_message(
        message.chat.id, t("bot.info.loading", locale=locale)
    )
    _remember_panel(panel, message.from_user.id, client=client)
    try:
        body, keyboard = await _page(client, message.from_user.id, locale)
        await panel.edit_text(
            body,
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=keyboard,
        )
        _remember_panel(panel, message.from_user.id, client=client)
    except Exception as exc:
        logger.warning("Group overview failed: {}", type(exc).__name__)
        await panel.edit_text(t("bot.info.error", locale=locale))


@Client.on_callback_query(filters.regex(r"^group_info:"))
async def info_callback(client, query):
    allowed, locale = await _actor(query.from_user)
    message = query.message
    if not allowed or message is None or message.chat is None:
        await query.answer(t("bot.info.denied", locale=locale), show_alert=True)
        return
    match = re.fullmatch(
        r"group_info:(\d+):(?:(groups|guests):)?([prxs]):([1-9]\d{0,8})",
        query.data or "",
    )
    if not match:
        await query.answer(t("bot.info.invalid", locale=locale), show_alert=True)
        return
    user_id, scope, action, page = match.groups()
    scope = scope or "groups"
    if int(user_id) != query.from_user.id or (
        message.chat.type == ChatType.PRIVATE and message.chat.id != query.from_user.id
    ):
        await query.answer(t("bot.info.denied", locale=locale), show_alert=True)
        return
    key = (message.chat.id, message.id)
    panel = _panels.get(key)
    if (
        panel is None
        or panel.user_id != query.from_user.id
        or panel.expires <= time.monotonic()
    ):
        _panels.pop(key, None)
        await query.answer(t("bot.info.expired", locale=locale), show_alert=True)
        return
    await query.answer()
    if action == "x":
        _panels.pop(key, None)
        await message.delete()
        return
    try:
        body, keyboard = await _page(
            client,
            query.from_user.id,
            locale,
            int(page),
            refresh=action == "r",
            scope=scope,
        )
        try:
            await message.edit_text(
                body,
                parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                reply_markup=keyboard,
            )
        except MessageNotModified:
            pass
        _remember_panel(message, query.from_user.id, scope, client=client)
    except Exception as exc:
        logger.warning("Group overview refresh failed: {}", type(exc).__name__)
        await message.edit_text(t("bot.info.error", locale=locale))
