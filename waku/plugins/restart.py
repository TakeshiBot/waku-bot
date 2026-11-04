"""Restart the whole bot process after an authenticated operator request."""

import asyncio
import secrets
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path

import pyrogram
from pyrogram.client import Client

from waku import common, database
from waku.config import ProviderConfig, _AppConfig, _resolve_settings_files, app_config
from waku.i18n import t
from waku.logger import logger
from waku.plugins.menu_cleanup import stop_watching_menu, watch_menu
from waku.services import maintenance
from waku.services.process_restart import request_restart
from waku.services.settings_editor import SettingsEditError, SettingsEditor
from waku.version import check_updates

_MENUS = {}


@dataclass
class RestartMenu:
    user_id: int
    chat_id: int
    message_id: int
    locale: str
    expires: float
    text: str = ""
    has_update: bool = False
    pending_action: str = ""


def _text(key, locale):
    return t("bot.restart." + key, locale=locale)


def _buttons(token, lang, *, has_update=False, confirm=False):
    button = pyrogram.types.InlineKeyboardButton
    if confirm:
        rows = [[
            button("Yes", callback_data=f"rst:{token}:yes", style=pyrogram.enums.ButtonStyle.SUCCESS),
            button("No", callback_data=f"rst:{token}:no", style=pyrogram.enums.ButtonStyle.DANGER),
        ]]
    else:
        rows = [[
            button(_text("restart_button", lang), callback_data=f"rst:{token}:restart"),
            button(("⚠️ " if has_update else "") + _text("update_button", lang), callback_data=f"rst:{token}:update"),
        ], [
            button(_text("close", lang), callback_data=f"rst:{token}:close", style=pyrogram.enums.ButtonStyle.DANGER),
        ]]
    return pyrogram.types.InlineKeyboardMarkup(rows)


async def _operator(user_id):
    db_user = await database.get_user_by_id(user_id)
    locale = db_user.user_config.lang if db_user else app_config.lang
    allowed = user_id in app_config.owners or bool(
        db_user and db_user.is_bot_global_admin and not db_user.is_blocked
    )
    return allowed, locale


def _valid_actor(message):
    user = message.from_user
    return bool(
        user
        and not user.is_bot
        and not getattr(message, "sender_chat", None)
        and not getattr(message, "business_connection_id", None)
        and not getattr(message, "guest_query_id", None)
    )


async def _restart() -> None:
    # Let the command handler and its acknowledgement finish before shutdown.
    await asyncio.sleep(2)
    request_restart()


@Client.on_message(pyrogram.filters.command("restart"), group=0)
async def restart_command(client: Client, message: pyrogram.types.Message):
    user = message.from_user
    if not _valid_actor(message):
        return
    allowed, lang = await _operator(user.id)
    if not allowed:
        await message.reply_text(_text("denied", lang))
        return
    for token, menu in list(_MENUS.items()):
        if menu.expires < time.monotonic():
            _MENUS.pop(token, None)
    if len(_MENUS) >= 100:
        _MENUS.pop(next(iter(_MENUS)))
    token = secrets.token_hex(8)
    update = await asyncio.to_thread(check_updates)
    status_key = {
        "ahead": "update_available", "identical": "up_to_date",
        "behind": "local_ahead", "diverged": "diverged",
    }.get(update["status"], "check_unavailable")
    text = _text("menu", lang) + "\n\n" + _text(status_key, lang).format(
        current=escape(update["current"]), latest=escape(update["latest"]), count=update["count"]
    )
    has_update = update["status"] == "ahead"
    reply = await message.reply_text(
        text,
        parse_mode=pyrogram.enums.ParseMode.HTML,
        reply_markup=_buttons(token, lang, has_update=has_update),
    )
    _MENUS[token] = RestartMenu(
        user.id, reply.chat.id, reply.id, lang, time.monotonic() + 600, text, has_update
    )
    watch_menu(client, reply, session=_MENUS[token])


@Client.on_callback_query(
    pyrogram.filters.regex(r"^rst:[0-9a-f]{16}:(restart|update|close|yes|no)$"), group=0
)
async def restart_callback(client: Client, query: pyrogram.types.CallbackQuery):
    data = query.data.decode() if isinstance(query.data, bytes) else str(query.data)
    parts = data.split(":")
    if len(parts) != 3 or not query.message or not query.from_user:
        return
    _, token, action = parts
    menu = _MENUS.get(token)
    if (
        menu is None
        or menu.expires < time.monotonic()
        or (query.from_user.id, query.message.chat.id, query.message.id)
        != (menu.user_id, menu.chat_id, menu.message_id)
    ):
        await query.answer(_text("expired", app_config.lang), show_alert=True)
        return
    allowed, lang = await _operator(query.from_user.id)
    if not allowed:
        await query.answer(_text("denied", lang), show_alert=True)
        return
    async with maintenance.LOCK:
        if _MENUS.get(token) is not menu:
            await query.answer(_text("expired", lang), show_alert=True)
            return
        if action == "close":
            await query.answer()
            await query.message.delete()
            _MENUS.pop(token, None)
            return
        if action in {"restart", "update"}:
            await query.answer()
            if menu.pending_action == action:
                return
            await query.message.edit_text(
                _text("confirm_" + action, lang),
                parse_mode=pyrogram.enums.ParseMode.HTML,
                reply_markup=_buttons(token, lang, confirm=True),
            )
            menu.pending_action = action
            return
        if action == "no":
            await query.answer()
            if menu.pending_action:
                await query.message.edit_text(
                    menu.text, parse_mode=pyrogram.enums.ParseMode.HTML,
                    reply_markup=_buttons(token, lang, has_update=menu.has_update),
                )
                menu.pending_action = ""
            return
        if action != "yes" or not menu.pending_action:
            await query.answer(_text("expired", lang), show_alert=True)
            return
        action = menu.pending_action
        if action == "update" and not await asyncio.to_thread(maintenance.worker_ready):
            await query.answer(_text("worker_unavailable", lang), show_alert=True)
            return
        await query.answer()
        try:
            editor = SettingsEditor(
                [Path(path) for path in _resolve_settings_files()],
                _AppConfig,
                ProviderConfig,
                lambda: app_config.model_dump(),
            )
            await asyncio.to_thread(editor.validate_for_restart)
        except (SettingsEditError, OSError):
            await query.message.edit_text(
                _text("invalid_config", lang), reply_markup=None
            )
            _MENUS.pop(token, None)
            return
        try:
            await query.message.edit_text(
                maintenance.pending_text(action, lang, menu.user_id, user=query.from_user),
                parse_mode=pyrogram.enums.ParseMode.HTML,
                reply_markup=None,
            )
            await asyncio.to_thread(
                maintenance.save_request, action, query.message, menu.user_id, lang, user=query.from_user
            )
        except (OSError, ValueError) as error:
            key = "busy" if str(error) == "busy" else "storage_error"
            await query.message.edit_text(_text(key, lang), reply_markup=None)
            _MENUS.pop(token, None)
            return
        stop_watching_menu(client, query.message)
        _MENUS.pop(token, None)
        logger.info("Bot maintenance {} requested by {}", action, menu.user_id)
        if action == "restart":
            common.spawn(_restart(), name="telegram-command-restart")
