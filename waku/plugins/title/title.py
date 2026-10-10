"""Label-only Telegram commands and staged AI promotion presets."""

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field

import pyrogram
from pyrogram import Client

from waku import database, i18n
from waku.common.utils import get_reply_target
from waku.logger import logger
from waku.plugins.menu_cleanup import schedule_saved_menu_cleanup

from .authority import TitleDenied, can_set_preset, change_title
from .permissions import ADMIN_RIGHTS, CHANNEL_ONLY, supported
from .utils import TitlePermissionsMarkup


@dataclass
class PresetSession:
    chat_id: int
    user_id: int
    lang: str
    baseline: dict | str | None
    draft: dict
    token: str = field(default_factory=lambda: secrets.token_hex(8))
    message_id: int | None = None
    revision: int = 0
    expires: float = field(default_factory=lambda: time.monotonic() + 900)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    command_message_id: int | None = None


_sessions: dict[str, PresetSession] = {}


def _t(key, lang):
    return i18n.t(f"title_menu.{key}", locale=lang)


def _preset(value):
    if isinstance(value, str):
        value = json.loads(value)
    if value is None:
        value = {}
    if not isinstance(value, dict) or any(type(v) is not bool for v in value.values()):
        raise ValueError("invalid_permissions")
    return dict(value)


async def _current_preset(chat):
    row = await database.get_chat_by_id(chat.id)
    config = row.chat_config if row else await database.get_chat_config(chat)
    return config.title_permissions, config.lang


async def _tag(client, message, *, clear):
    chat, actor = message.chat, message.from_user
    if (
        not chat
        or not actor
        or actor.is_bot
        or message.sender_chat
        or getattr(message, "business_connection_id", None)
    ):
        return
    lang = (await database.get_chat_config(chat)).lang
    reply = get_reply_target(message)
    if reply and (
        reply.chat is None or reply.chat.id != chat.id or reply.sender_chat is not None
    ):
        await message.reply_text(_t("denied", lang))
        return
    target = reply.from_user if reply else actor
    if not target or not target.id:
        await message.reply_text(_t("denied", lang))
        return
    title = "" if clear else " ".join((message.command or [])[1:]).strip()
    if not clear and not title:
        title = target.username or target.full_name or ""
    try:
        await change_title(client, chat.id, actor.id, target.id, title)
    except TitleDenied as error:
        await message.reply_text(_t(str(error), lang))
    except Exception as error:
        logger.warning("Telegram label operation failed: {}", type(error).__name__)
        await message.reply_text(_t("error", lang))
    else:
        await message.reply_text(_t("done", lang))


@Client.on_message(pyrogram.filters.command("tag") & pyrogram.filters.group, group=0)
async def set_member_title(client, message):
    await _tag(client, message, clear=False)


@Client.on_message(pyrogram.filters.command("xtag") & pyrogram.filters.group, group=0)
async def delete_member_title(client, message):
    await _tag(client, message, clear=True)


def _markup(session):
    return TitlePermissionsMarkup(
        session.draft, session.lang, session.token, session.revision
    ).build()


@Client.on_message(pyrogram.filters.command("sett") & pyrogram.filters.group, group=0)
async def set_title_permissions(client, message):
    chat, user = message.chat, message.from_user
    if (
        not chat
        or not user
        or user.is_bot
        or message.sender_chat
        or getattr(message, "business_connection_id", None)
    ):
        return
    lang = (await database.get_chat_config(chat)).lang
    try:
        if not await can_set_preset(client, chat.id, user.id):
            await message.reply_text(_t("denied", lang))
            return
        preset, lang = await _current_preset(chat)
        draft = _preset(preset)
    except Exception:
        await message.reply_text(_t("error", lang))
        return
    for token, previous in list(_sessions.items()):
        if previous.expires <= time.monotonic():
            _sessions.pop(token, None)
    session = PresetSession(chat.id, user.id, lang, preset, draft)
    session.command_message_id = message.id
    sent = await message.reply_text(_t("preset", lang), reply_markup=_markup(session))
    session.message_id = sent.id
    _sessions[session.token] = session


@Client.on_callback_query(
    pyrogram.filters.regex(
        r"^sett:[0-9a-f]{16}:\d{1,9}:(?:\d+|save|cancel|reload|close)$"
    ),
    group=0,
)
async def set_title_permissions_callback(client, query):
    message, user = query.message, query.from_user
    if not message or not message.chat or not user or not query.data:
        return
    _, token, revision, action = query.data.split(":")
    session = _sessions.get(token)
    if (
        session is None
        or session.expires <= time.monotonic()
        or (message.chat.id, user.id, message.id)
        != (session.chat_id, session.user_id, session.message_id)
    ):
        if session and session.expires <= time.monotonic():
            _sessions.pop(token, None)
        await query.answer(
            _t("expired", session.lang if session else "vi"), show_alert=True
        )
        return
    try:
        if not await can_set_preset(client, session.chat_id, user.id):
            await query.answer(_t("denied", session.lang), show_alert=True)
            return
    except Exception:
        await query.answer(_t("error", session.lang), show_alert=True)
        return
    await query.answer()
    async with session.lock:
        if (
            _sessions.get(token) is not session
            or session.expires <= time.monotonic()
            or int(revision) != session.revision
        ):
            return
        session.revision += 1
        if action == "close":
            _sessions.pop(token, None)
            await message.edit_reply_markup(None)
            return
        if action in ("reload", "cancel"):
            try:
                preset, session.lang = await _current_preset(message.chat)
                draft = _preset(preset)
            except Exception:
                await message.edit_text(
                    _t("error", session.lang), reply_markup=_markup(session)
                )
                return
            session.baseline, session.draft = preset, draft
        elif action == "save":
            try:
                if not await can_set_preset(client, session.chat_id, user.id):
                    raise TitleDenied("denied")
                await database.update_chat_config_fields(
                    message.chat,
                    {"title_permissions": dict(session.draft)},
                    expected_fields={"title_permissions": session.baseline},
                )
            except TitleDenied:
                await message.edit_text(
                    _t("denied", session.lang), reply_markup=_markup(session)
                )
                return
            except ValueError:
                await message.edit_text(
                    _t("conflict", session.lang), reply_markup=_markup(session)
                )
                return
            except Exception:
                await message.edit_text(
                    _t("error", session.lang), reply_markup=_markup(session)
                )
                return
            _sessions.pop(token, None)
            try:
                await message.edit_text(_t("saved", session.lang), reply_markup=None)
            finally:
                schedule_saved_menu_cleanup(client, message, session.command_message_id)
            return
        else:
            index = int(action)
            names = list(ADMIN_RIGHTS)
            if index >= len(names):
                return
            name = names[index]
            if (
                (name in CHANNEL_ONLY or not supported(name))
                and session.draft.get(name) is not True
            ) or name == "can_manage_chat":
                await message.edit_text(
                    _t("unsupported", session.lang)
                    + "\n\n"
                    + _t("preset", session.lang),
                    reply_markup=_markup(session),
                )
                return
            session.draft[name] = not session.draft.get(name, False)
        await message.edit_text(
            _t("preset", session.lang), reply_markup=_markup(session)
        )
