"""Group configuration drafts bound to the requesting administrator."""

import asyncio
import copy
import secrets
import time
from dataclasses import dataclass, field

import pyrogram
from pyrogram.client import Client
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from waku import database
from waku.common.telegram_authority import (
    can_manage_bot_settings,
    can_manage_group_settings,
)
from waku.common.utils import GROUP_CHAT_TYPES
from waku.database.models import ChatConfig
from waku.i18n import i18n
from waku.plugins.menu_cleanup import schedule_saved_menu_cleanup
from waku.plugins.panel import chat_panel_button
from waku.services.telegram_images import normalize_image_mode

_FIELDS = (
    ("waifu", "waifu_enabled"),
    ("delete_events", "delete_events_enabled"),
    ("quote_pin_message", "quote_pin_message"),
    ("ai_reply", "ai_reply"),
    ("ai_comment", "ai_comment"),
    ("group_memory_enabled", "group_memory_enabled"),
    ("setu_enabled", "setu_enabled"),
    ("unpin_channel_pin_enabled", "unpin_channel_pin_enabled"),
    ("convert_b23_enabled", "convert_b23_enabled"),
    ("parse_artwork_enabled", "parse_artwork_enabled"),
    ("pick_bottle_enabled", "pick_bottle_enabled"),
    ("ai_reply_other_bots_enabled", "ai_reply_other_bots_enabled"),
    ("verify", "verify_enabled"),
    ("moderation", "agent_moderation_enabled"),
    ("sticker_memory_enabled", "sticker_memory_enabled"),
)
_TTL = 15 * 60
_SESSIONS = {}


@dataclass
class GroupConfigSession:
    user_id: int
    chat_id: int
    message_id: int
    baseline: ChatConfig
    draft: ChatConfig
    expires: float = field(default_factory=lambda: time.monotonic() + _TTL)
    revision: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    command_message_id: int | None = None

    def changes(self):
        return {
            name: getattr(self.draft, name)
            for name in [*(name for _, name in _FIELDS), "telegram_r18_mode"]
            if getattr(self.draft, name) != getattr(self.baseline, name)
        }


def _tr(key, locale):
    return i18n.t("bot.config." + key, locale=locale)


def _prune():
    for token, session in list(_SESSIONS.items()):
        if session.expires <= time.monotonic():
            _SESSIONS.pop(token, None)
    while len(_SESSIONS) >= 100:
        _SESSIONS.pop(next(iter(_SESSIONS)))


class ChatConfigMarkup:
    def __init__(self, chat_config, lang="", chat_id=None, token="", revision=0):
        self.chat_config, self.lang, self.chat_id = chat_config, lang, chat_id
        self.token, self.revision = token, revision

    def _button(self, label, action):
        return InlineKeyboardButton(
            label, callback_data=f"config_chat:{self.token}:{self.revision}:{action}"
        )

    def build(self):
        buttons = []
        for index, (label, name) in enumerate(_FIELDS):
            if name == "setu_enabled":
                mode = (
                    ("image_safe", "image_r18", "image_mixed")[
                        normalize_image_mode(self.chat_config.telegram_r18_mode)
                    ]
                    if self.chat_config.setu_enabled
                    else "image_off"
                )
                buttons.append(self._button(_tr(mode, self.lang), f"toggle:{index}"))
                continue
            title = (
                _tr("moderation", self.lang)
                if label == "moderation"
                else i18n.t("bot.button.chat_config." + label, locale=self.lang)
            )
            status = "✔️" if getattr(self.chat_config, name) else "❌"
            buttons.append(self._button(f"{title} {status}", f"toggle:{index}"))
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append([self._button(_tr("save", self.lang), "save")])
        if self.chat_id is not None:
            button = chat_panel_button(self.chat_id, self.lang)
            if button:
                rows.append([button])
        return InlineKeyboardMarkup(rows)


def _markup(token, session):
    return ChatConfigMarkup(
        session.draft, session.draft.lang, session.chat_id, token, session.revision
    ).build()


@Client.on_message(pyrogram.filters.command("config") & pyrogram.filters.group, group=0)
async def config_chat_cmd(client, message):
    user, chat = message.from_user, message.chat
    if not user or not chat:
        return
    config = await database.get_chat_config(chat)
    if not await can_manage_bot_settings(client, user.id, chat.id):
        await message.reply_text(
            i18n.t("bot.msg.no_permission_group", locale=config.lang)
        )
        return
    _prune()
    for token, session in list(_SESSIONS.items()):
        if (session.user_id, session.chat_id) == (user.id, chat.id):
            _SESSIONS.pop(token, None)
    token = secrets.token_hex(5)
    session = GroupConfigSession(
        user.id, chat.id, 0, copy.deepcopy(config), copy.deepcopy(config)
    )
    session.command_message_id = message.id
    _SESSIONS[token] = session
    try:
        reply = await message.reply_text(
            _tr("draft", config.lang), reply_markup=_markup(token, session)
        )
        session.message_id = reply.id
    except Exception:
        _SESSIONS.pop(token, None)
        raise


@Client.on_callback_query(pyrogram.filters.regex(r"^config_chat"), group=0)
async def config_chat(client, query):
    _prune()
    data = (
        query.data.decode("utf-8", errors="replace")
        if isinstance(query.data, bytes)
        else str(query.data)
    )
    parts = data.split(":", 3)
    session = (
        _SESSIONS.get(parts[1]) if len(parts) == 4 and parts[2].isdigit() else None
    )
    message, user = query.message, query.from_user
    if (
        session is None
        or message is None
        or message.chat is None
        or user is None
        or message.chat.type not in GROUP_CHAT_TYPES
        or (user.id, message.chat.id, message.id)
        != (session.user_id, session.chat_id, session.message_id)
    ):
        await query.answer(_tr("expired", ""), show_alert=True)
        return
    token, revision, action = parts[1:]
    async with session.lock:
        locale = session.draft.lang
        if _SESSIONS.get(token) is not session or session.expires <= time.monotonic():
            await query.answer(_tr("expired", locale), show_alert=True)
            return
        if int(revision) != session.revision:
            await query.answer(_tr("expired", locale), show_alert=True)
            return
        if not await can_manage_bot_settings(client, user.id, session.chat_id):
            _SESSIONS.pop(token, None)
            await query.answer(
                i18n.t("bot.msg.no_permission_group", locale=locale), show_alert=True
            )
            return
        previous = copy.deepcopy(session.draft)
        previous_baseline = copy.deepcopy(session.baseline)
        session.expires = time.monotonic() + _TTL
        if action == "cancel":
            _SESSIONS.pop(token, None)
            await query.answer()
            await query.edit_message_text(_tr("cancelled", locale), reply_markup=None)
            return
        if action == "reload":
            config = await database.get_chat_config(message.chat)
            session.baseline = copy.deepcopy(config)
            session.draft = copy.deepcopy(config)
        elif action == "save":
            changes = session.changes()
            if changes.keys() & {
                "agent_moderation_enabled",
                "verify_enabled",
            } and not await can_manage_group_settings(
                client, user.id, session.chat_id, require_restrict_members=True
            ):
                await query.answer(_tr("moderation_denied", locale), show_alert=True)
                return
            try:
                if changes:
                    await database.update_chat_config_fields(
                        message.chat,
                        changes,
                        expected_fields={
                            name: getattr(session.baseline, name) for name in changes
                        },
                    )
            except ValueError as exc:
                if str(exc) != "config_conflict":
                    raise
                await query.answer(_tr("conflict", locale), show_alert=True)
                return
            _SESSIONS.pop(token, None)
            try:
                await query.answer()
                await query.edit_message_text(_tr("saved", locale), reply_markup=None)
            finally:
                schedule_saved_menu_cleanup(client, message, session.command_message_id)
            return
        elif action.startswith("toggle:"):
            try:
                index = int(action.split(":")[1])
                if not 0 <= index < len(_FIELDS):
                    raise ValueError
                name = _FIELDS[index][1]
            except (ValueError, IndexError):
                await query.answer(_tr("expired", locale), show_alert=True)
                return
            if name in {
                "agent_moderation_enabled",
                "verify_enabled",
            } and not await can_manage_group_settings(
                client, user.id, session.chat_id, require_restrict_members=True
            ):
                await query.answer(_tr("moderation_denied", locale), show_alert=True)
                return
            if name == "setu_enabled":
                index = (
                    normalize_image_mode(session.draft.telegram_r18_mode) + 1
                    if session.draft.setu_enabled
                    else 0
                )
                index = (index + 1) % 4
                session.draft.setu_enabled = index != 0
                session.draft.telegram_r18_mode = max(0, index - 1)
            else:
                setattr(session.draft, name, not getattr(session.draft, name))
        else:
            await query.answer(_tr("expired", locale), show_alert=True)
            return
        session.revision += 1
        await query.answer()
        try:
            await query.edit_message_reply_markup(_markup(token, session))
        except MessageNotModified:
            pass
        except Exception:
            session.draft, session.baseline = previous, previous_baseline
            session.revision -= 1
            raise
