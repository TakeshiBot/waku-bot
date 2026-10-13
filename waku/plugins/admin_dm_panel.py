"""Upload, replace and remove AGENTS.md through the bot-admin DM menu."""

from __future__ import annotations

import asyncio
import io
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from pyrogram import Client, enums, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from waku import common
from waku.i18n import t
from waku.logger import logger
from waku.services import admin_dm_instructions as store

_SESSIONS = {}


@dataclass
class InstructionsSession:
    user_id: int
    chat_id: int
    message_id: int
    locale: str
    content: str | None
    baseline: str | None
    revision: str
    view: int = 0
    pending: bool = False
    pending_until: float = 0
    expires: float = field(default_factory=lambda: time.monotonic() + 900)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _tr(key, session=None):
    return t("bot.admin_dm_agents." + key, locale=session.locale if session else "")


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

    def button(key, action):
        return InlineKeyboardButton(
            _tr(key, session),
            callback_data=f"admd:{token}:{session.view}:{action}",
            style={
                "save": enums.ButtonStyle.SUCCESS,
                "close": enums.ButtonStyle.DANGER,
            }.get(action, enums.ButtonStyle.DEFAULT),
        )

    text = _tr("title", session) + "\n" + _tr("scope", session)
    text += "\n\n" + _tr(
        "present" if session.content is not None else "absent", session
    )
    if session.content != session.baseline:
        text += "\n" + _tr("unsaved", session)
    if session.pending:
        text += "\n\n" + _tr("upload_help", session)
    if notice:
        text += "\n\n" + notice
    rows = [[button("upload", "upload")]]
    if session.content is not None:
        rows.append([button("download", "download"), button("delete", "delete")])
    rows.append([button("save", "save"), button("close", "close")])
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


async def open_panel(client, message, user_id, locale):
    if (
        message.chat is None
        or message.chat.type != enums.ChatType.PRIVATE
        or message.chat.id != user_id
        or not await store.is_admin(user_id)
    ):
        raise store.InstructionsError("denied")
    snapshot = await store.read_snapshot()
    clear_user_sessions(user_id)
    _prune()
    token = secrets.token_hex(5)
    session = InstructionsSession(
        user_id,
        message.chat.id,
        message.id,
        locale,
        snapshot.content,
        snapshot.content,
        snapshot.revision,
    )
    _SESSIONS[token] = session
    try:
        text, markup = _menu(token, session)
        await _edit(client, session, text, markup)
    except BaseException:
        _SESSIONS.pop(token, None)
        raise


@Client.on_callback_query(filters.regex(r"^admd:"), group=0)
async def instructions_callback(client, query):
    _prune()
    data = query.data.decode() if isinstance(query.data, bytes) else query.data
    parts = str(data).split(":")
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
        if int(view) != session.view:
            await query.answer(_tr("expired", session), show_alert=True)
            return
        session.pending = False
        session.expires = time.monotonic() + 900
        try:
            if action in {"close", "save"}:
                if action == "save":
                    await store.save(session.content, session.revision)
                _SESSIONS.pop(token, None)
                await query.answer(
                    _tr("saved" if action == "save" else "closed", session)
                )
                try:
                    await message.delete()
                except Exception:
                    await _edit(
                        client,
                        session,
                        _tr("saved" if action == "save" else "closed", session),
                    )
                return
            elif action == "upload":
                session.pending = True
                session.pending_until = time.monotonic() + 180
            elif action == "delete":
                session.content = None
            elif action == "download":
                if session.content is None:
                    raise store.InstructionsError("invalid_file")
                document = io.BytesIO(session.content.encode("utf-8"))
                document.name = "AGENTS.md"
                await client.send_document(session.chat_id, document)
            else:
                raise store.InstructionsError("expired")
            text, markup = _menu(token, session)
            await _edit(client, session, text, markup)
            await query.answer()
        except (OSError, store.InstructionsError) as error:
            key = (
                str(error) if isinstance(error, store.InstructionsError) else "storage"
            )
            await query.answer(_tr(key, session), show_alert=True)
        except Exception as error:
            logger.warning(
                "Admin DM instructions menu failed: {}", type(error).__name__
            )
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
    # Opening /config again cancels this upload and lets its command handler run.
    text = common.message_plain_text(message)
    command = (text.split(maxsplit=1) or [""])[0].split("@", 1)[0]
    if command == "/config":
        clear_user_sessions(message.from_user.id)
        return
    async with session.lock:
        try:
            if (
                not session.pending
                or _SESSIONS.get(token) is not session
                or not await store.is_admin(message.from_user.id)
            ):
                return
            if text == "/cancel":
                session.pending = False
                notice = _tr("cancelled", session)
            else:
                if session.pending_until <= time.monotonic():
                    session.pending = False
                    raise store.InstructionsError("expired")
                document = message.document
                if (
                    document is None
                    or Path(document.file_name or "").suffix.lower() != ".md"
                ):
                    raise store.InstructionsError("invalid_file")
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
                    # Kurigram leaves in-memory downloads at EOF after writing.
                    file.seek(0)
                    content = store.decode_markdown(file.read(store.MAX_FILE_BYTES + 1))
                finally:
                    file.close()
                if _SESSIONS.get(token) is not session or not await store.is_admin(
                    message.from_user.id
                ):
                    return
                session.content = content
                session.pending = False
                notice = _tr("staged", session)
            text, markup = _menu(token, session, notice)
            await _edit(client, session, text, markup)
        except (OSError, store.InstructionsError, TimeoutError) as error:
            key = (
                str(error) if isinstance(error, store.InstructionsError) else "storage"
            )
            text, markup = _menu(token, session, _tr(key, session))
            await _edit(client, session, text, markup)
        except Exception as error:
            logger.warning(
                "Admin DM instructions upload failed: {}", type(error).__name__
            )
            text, markup = _menu(token, session, _tr("storage", session))
            await _edit(client, session, text, markup)
        finally:
            # An uploaded instruction file must never also become an AI prompt.
            message.stop_propagation()
