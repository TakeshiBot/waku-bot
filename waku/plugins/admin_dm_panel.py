"""Manage a named Markdown library with one immediately active admin-DM file."""

from __future__ import annotations

import asyncio
import html
import secrets
import time
from dataclasses import dataclass, field

from pyrogram import Client, enums, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from waku import common
from waku.i18n import t
from waku.logger import logger
from waku.services import admin_dm_instructions as store

_SESSIONS = {}
_PAGE_SIZE = 20


@dataclass
class InstructionsSession:
    user_id: int
    chat_id: int
    message_id: int
    locale: str
    library: store.LibrarySnapshot
    view: int = 0
    page: int = 0
    pending: bool = False
    pending_until: float = 0
    expires: float = field(default_factory=lambda: time.monotonic() + 900)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _tr(key, session=None, **values):
    return t(
        "bot.admin_dm_agents." + key, locale=session.locale if session else ""
    ).format(**values)


def clear_user_sessions(user_id):
    for token, session in list(_SESSIONS.items()):
        if session.user_id == user_id:
            _SESSIONS.pop(token, None)


def _prune():
    for token, session in list(_SESSIONS.items()):
        if session.expires <= time.monotonic():
            _SESSIONS.pop(token, None)
    while len(_SESSIONS) > 100:
        _SESSIONS.pop(next(iter(_SESSIONS)))


def _menu(token, session, notice=""):
    session.view += 1

    def button(label, action):
        return InlineKeyboardButton(
            label,
            callback_data=f"admd:{token}:{session.view}:{action}",
            style=enums.ButtonStyle.DANGER
            if action in {"delete", "close"}
            else enums.ButtonStyle.DEFAULT,
        )

    text = _tr("title", session) + "\n" + _tr("scope", session)
    name = session.library.active_name
    text += "\n\n" + (
        _tr("active", session, name=html.escape(name))
        if name
        else _tr("inactive", session)
    )
    files = session.library.files
    pages = max(1, (len(files) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    session.page = max(0, min(session.page, pages - 1))
    buttons = [
        button(
            ("✅ " if file.id == session.library.selected else "") + file.name,
            "file:" + file.id,
        )
        for file in files[session.page * _PAGE_SIZE : (session.page + 1) * _PAGE_SIZE]
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    scope_label = {"admin": "Admin", "dm": "DM", "global": "Global"}[
        session.library.scope
    ]
    rows.append([button(_tr("scope_button", session, value=scope_label), "scope")])
    if not files:
        text += "\n" + _tr("absent", session)
    if pages > 1:
        text += "\n" + _tr("page", session, page=session.page + 1, pages=pages)
        navigation = []
        if session.page:
            navigation.append(button("‹", f"page:{session.page - 1}"))
        if session.page + 1 < pages:
            navigation.append(button("›", f"page:{session.page + 1}"))
        rows.append(navigation)
    if session.pending:
        text += "\n\n" + _tr("upload_help", session)
    if notice:
        text += "\n\n" + notice
    rows.append(
        [
            button(
                _tr("cancel_add" if session.pending else "upload", session),
                "cancel_add" if session.pending else "upload",
            ),
            button(_tr("delete", session), "delete"),
        ]
    )
    rows.append(
        [button(_tr("back", session), "back"), button(_tr("close", session), "close")]
    )
    return text, InlineKeyboardMarkup(rows)


async def _edit(client, session, text, markup=None):
    try:
        await client.edit_message_text(
            session.chat_id,
            session.message_id,
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )
    except MessageNotModified:
        pass


async def _render(client, token, session, notice=""):
    previous_library, previous_view = session.library, session.view
    session.library = await store.list_files()
    text, markup = _menu(token, session, notice)
    try:
        await _edit(client, session, text, markup)
    except BaseException:
        session.library, session.view = previous_library, previous_view
        raise


async def open_panel(client, message, user_id, locale):
    if (
        message.chat is None
        or message.chat.type != enums.ChatType.PRIVATE
        or message.chat.id != user_id
        or not await store.is_admin(user_id)
    ):
        raise store.InstructionsError("denied")
    library = await store.list_files()
    clear_user_sessions(user_id)
    token = secrets.token_hex(5)
    session = InstructionsSession(user_id, message.chat.id, message.id, locale, library)
    _SESSIONS[token] = session
    _prune()
    try:
        await _render(client, token, session)
    except BaseException:
        _SESSIONS.pop(token, None)
        raise


@Client.on_callback_query(filters.regex(r"^admd:"), group=0)
async def instructions_callback(client, query):
    _prune()
    data = query.data.decode() if isinstance(query.data, bytes) else query.data
    parts = str(data).split(":", 3)
    session = (
        _SESSIONS.get(parts[1]) if len(parts) == 4 and parts[2].isdigit() else None
    )
    message, user = query.message, query.from_user
    if (
        session is None
        or message is None
        or message.chat is None
        or user is None
        or message.chat.type != enums.ChatType.PRIVATE
        or (user.id, message.chat.id, message.id)
        != (session.user_id, session.chat_id, session.message_id)
    ):
        await query.answer(_tr("expired"), show_alert=True)
        return
    token, view, action = parts[1:]
    async with session.lock:
        if _SESSIONS.get(token) is not session or session.expires <= time.monotonic():
            await query.answer(_tr("expired", session), show_alert=True)
            return
        if not await store.is_admin(user.id):
            _SESSIONS.pop(token, None)
            await query.answer(_tr("denied", session), show_alert=True)
            return
        if int(view) != session.view and action != "close":
            await query.answer(_tr("expired", session), show_alert=True)
            return
        session.expires = time.monotonic() + 900
        try:
            if action == "close":
                _SESSIONS.pop(token, None)
                await query.answer()
                try:
                    await message.delete()
                except Exception:
                    await _edit(client, session, _tr("closed", session))
                return
            if action == "back":
                from waku.plugins.private_config import reopen_private_config

                await reopen_private_config(client, message, user.id, session.locale)
                _SESSIONS.pop(token, None)
                await query.answer()
                return
            if action.startswith("file:"):
                await store.toggle_file(action.split(":", 1)[1])
            elif action == "scope":
                await store.cycle_scope(session.library.scope)
            elif action == "upload":
                session.pending = True
                session.pending_until = time.monotonic() + 180
            elif action == "cancel_add":
                session.pending = False
            elif action == "delete":
                await store.delete_selected(session.library.selected)
            elif action.startswith("page:"):
                session.page = max(0, int(action.split(":", 1)[1]))
            else:
                raise store.InstructionsError("expired")
            await _render(client, token, session)
            await query.answer()
        except (OSError, store.InstructionsError) as error:
            key = (
                str(error) if isinstance(error, store.InstructionsError) else "storage"
            )
            if key in {"stale", "choose_file"}:
                await _render(client, token, session)
            await query.answer(_tr(key, session), show_alert=True)
        except Exception as error:
            logger.warning("Admin DM Markdown menu failed: {}", type(error).__name__)
            await query.answer(_tr("storage", session), show_alert=True)


@Client.on_message(filters.private, group=-115)
async def instructions_input(client, message):
    if (
        message.from_user is None
        or message.chat is None
        or getattr(message, "business_connection_id", None)
        or getattr(message, "guest_query_id", None)
    ):
        return
    _prune()
    pair = next(
        (
            (token, s)
            for token, s in _SESSIONS.items()
            if s.pending
            and s.user_id == message.from_user.id
            and s.chat_id == message.chat.id
        ),
        None,
    )
    if pair is None:
        return
    token, session = pair
    text = common.message_plain_text(message)
    command = (text.split(maxsplit=1) or [""])[0].split("@", 1)[0]
    if command == "/config":
        clear_user_sessions(message.from_user.id)
        return
    accepted = False
    async with session.lock:
        try:
            if not session.pending or _SESSIONS.get(token) is not session:
                return
            if not await store.is_admin(message.from_user.id):
                clear_user_sessions(message.from_user.id)
                return
            if text == "/cancel":
                session.pending = False
                notice = _tr("cancelled", session)
            else:
                if session.pending_until <= time.monotonic():
                    session.pending = False
                    raise store.InstructionsError("expired")
                document = message.document
                if document is None:
                    raise store.InstructionsError("invalid_file")
                name = store.validate_name(document.file_name or "")
                if not isinstance(document.file_size, int) or document.file_size < 0:
                    raise store.InstructionsError("invalid_file")
                if document.file_size == 0:
                    raise store.InstructionsError("empty_file")
                if document.file_size > store.MAX_FILE_BYTES:
                    raise store.InstructionsError("too_large")
                file = await asyncio.wait_for(
                    client.download_media(message, in_memory=True), timeout=30
                )
                if file is None:
                    raise store.InstructionsError("storage")
                try:
                    file.seek(0)
                    content = store.decode_markdown(file.read(store.MAX_FILE_BYTES + 1))
                finally:
                    file.close()
                if _SESSIONS.get(token) is not session or not await store.is_admin(
                    message.from_user.id
                ):
                    return
                _, replaced = await store.add_file(name, content)
                accepted = True
                session.pending = False
                notice = _tr("replaced" if replaced else "added", session)
            session.expires = time.monotonic() + 900
            await _render(client, token, session, notice)
        except (OSError, store.InstructionsError, TimeoutError) as error:
            key = (
                str(error) if isinstance(error, store.InstructionsError) else "storage"
            )
            await _render(client, token, session, _tr(key, session))
        except Exception as error:
            logger.warning("Admin DM Markdown upload failed: {}", type(error).__name__)
            await _render(client, token, session, _tr("storage", session))
        finally:
            if accepted:
                try:
                    await message.delete()
                except Exception as error:
                    logger.warning(
                        "Admin MD upload message cleanup failed: {}",
                        type(error).__name__,
                    )
            message.stop_propagation()
