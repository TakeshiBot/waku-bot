"""Owner/global-admin settings menu, separate from /config in groups."""

from __future__ import annotations

import asyncio
import html
import os
import secrets
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from pyrogram import enums, filters
from pyrogram.client import Client
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from waku import common, database
from waku.config import ProviderConfig, _AppConfig, _resolve_settings_files, app_config
from waku.i18n import t
from waku.logger import logger
from waku.services.settings_editor import (
    SettingsEditError,
    SettingsEditor,
    display_value,
)
from waku.timezone import BOT_TIMEZONE

_PREFIX = "pcfg"
_SESSION_TTL = 15 * 60
_EDIT_TTL = 120
_PAGE_SIZE = 8
_MAX_SESSIONS = 100
_GROUPS = ("base", "agent", "webapp", "rss", "services", "cache", "economy")
_SESSIONS: dict[str, ConfigSession] = {}


@dataclass
class ConfigSession:
    user_id: int
    chat_id: int
    message_id: int
    locale: str
    editor: SettingsEditor
    expires: float = field(default_factory=lambda: time.monotonic() + _SESSION_TTL)
    entries: list[str] = field(default_factory=list)
    revision: str = ""
    pending: str | None = None
    pending_until: float = 0
    dirty: set[str] = field(default_factory=set)
    view: int = 0
    confirmation: str | None = None
    location: str = "home"
    back: str = "home"
    entry_parent: str = "groups:0"
    provider_name: str | None = None
    provider_parent: str = "providers:0"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _tr(translation_key: str, session: ConfigSession | None = None, **values) -> str:
    return t(
        "bot.private_config." + translation_key,
        locale=session.locale if session else "",
    ).format(**values)


async def _is_admin(user_id: int) -> bool:
    if user_id in app_config.owners:
        return True
    user = await database.get_user_by_id(user_id)
    return bool(user and user.is_bot_global_admin and not user.is_blocked)


def _prune() -> None:
    now = time.monotonic()
    for token, session in list(_SESSIONS.items()):
        if session.expires <= now:
            _SESSIONS.pop(token, None)
    while len(_SESSIONS) > _MAX_SESSIONS:
        _SESSIONS.pop(next(iter(_SESSIONS)))


def _button(label: str, token: str, action: str) -> InlineKeyboardButton:
    session = _SESSIONS[token]
    return InlineKeyboardButton(
        label, callback_data=f"{_PREFIX}:{token}:{session.view}:{action}"
    )


def _footer(
    token: str,
    session: ConfigSession,
    *,
    previous: str | None = None,
    following: str | None = None,
):
    row = []
    if previous:
        row.append(_button("◀️", token, previous))
    row.append(_button(_tr("back", session), token, session.back))
    if session.dirty:
        row.append(_button("🔄", token, "restart"))
    row.append(_button(_tr("close", session), token, "close"))
    if following:
        row.append(_button("▶️", token, following))
    return row


def _rows(buttons: list[InlineKeyboardButton], columns: int = 2):
    return [
        buttons[index : index + columns] for index in range(0, len(buttons), columns)
    ]


def _entry_value(values: dict, key: str):
    if key.startswith("agent_providers."):
        name, field_name = key.removeprefix("agent_providers.").rsplit(".", 1)
        return (
            values.get("agent_providers", {})
            .get(name, {})
            .get(field_name, ProviderConfig.model_fields[field_name].default)
        )
    return values[key] if key in values else getattr(app_config, key)


def _label(key: str, value, session: ConfigSession) -> str:
    status = "✅ " if value is True else "❌ " if value is False else ""
    changed = "🔄 " if key in session.dirty else ""
    return changed + status + key.rsplit(".", 1)[-1]


def _variables_text(
    title: str, keys: list[str], values: dict, session: ConfigSession
) -> str:
    lines = [title, "│"]
    for index, key in enumerate(keys):
        branch = "┖" if index == len(keys) - 1 else "┠"
        label = html.escape(_label(key, _entry_value(values, key), session))
        value = html.escape(display_value(key, _entry_value(values, key), limit=140))
        lines.append(f"{branch} <b>{label}</b> → <code>{value}</code>")
    return "\n".join(lines)


def _page(page_text: str, count: int) -> tuple[int, int]:
    pages = max(1, (count + _PAGE_SIZE - 1) // _PAGE_SIZE)
    return max(0, min(int(page_text), pages - 1)), pages


def _group(key: str) -> str:
    if key.startswith("agent"):
        return "agent"
    if key.startswith("webapp") or key.startswith("health_check"):
        return "webapp"
    if key.startswith(("rss", "fxembed")):
        return "rss"
    if key.startswith(("cache", "avatar")):
        return "cache"
    if key.startswith(("coin", "cost")):
        return "economy"
    if key.startswith(("redis", "btts", "manyacg", "aniobjcut", "infographic")):
        return "services"
    return "base"


def _menu(token: str, session: ConfigSession, action: str = "home"):
    session.view += 1
    session.confirmation = None
    values, session.revision = session.editor.snapshot()
    rows = []
    session.location = action
    previous = following = None
    text = _tr("title", session)
    if action == "home":
        session.entries = []
        session.provider_name = None
        text += "\n" + _tr(
            "summary",
            session,
            file=html.escape(session.editor.path.name),
            timezone=html.escape(str(BOT_TIMEZONE)),
            model=html.escape(display_value("agent_model", app_config.agent_model)),
            count=len(session.dirty),
        )
        rows.append(
            [
                _button(_tr("open_settings", session), token, "groups:0"),
                _button(_tr("close", session), token, "close"),
            ]
        )
        return text, InlineKeyboardMarkup(rows)
    elif action.startswith("groups:"):
        page, pages = _page(action.split(":")[1], len(_GROUPS) + 1)
        session.entries = []
        session.provider_name = None
        session.back = "home"
        text = (
            _tr("variables_title", session)
            + "\n"
            + _tr(
                "groups_summary",
                session,
                count=len(_GROUPS) + 1,
                page=page + 1,
                pages=pages,
                changed=len(session.dirty),
            )
        )
        groups = [(group, _tr("groups." + group, session)) for group in _GROUPS]
        groups.append(("providers", _tr("providers", session)))
        for group, label in groups[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]:
            destination = "providers:0" if group == "providers" else f"group:{group}:0"
            rows.append([_button(label, token, destination)])
        previous = f"groups:{page - 1}" if page else None
        following = f"groups:{page + 1}" if page + 1 < pages else None
    elif action.startswith("group:"):
        _, group, page_text = action.split(":")
        if group not in _GROUPS:
            raise SettingsEditError("invalid_value")
        keys = sorted(
            key
            for key in _AppConfig.model_fields
            if key not in {"agent_providers", "agent_powermem_config"}
            and _group(key) == group
        )
        session.entries = keys
        session.provider_name = None
        session.back = "groups:0"
        page, pages = _page(page_text, len(keys))
        session.entry_parent = f"group:{group}:{page}"
        page_keys = keys[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]
        text = _variables_text(
            _tr(
                "section_title",
                session,
                name=html.escape(_tr("groups." + group, session)),
            ),
            page_keys,
            values,
            session,
        )
        text += "\n" + _tr("page", session, page=page + 1, pages=pages)
        buttons = []
        for index in range(page * _PAGE_SIZE, min(len(keys), (page + 1) * _PAGE_SIZE)):
            key = keys[index]
            value = _entry_value(values, key)
            target = "toggle" if isinstance(value, bool) else "entry"
            buttons.append(
                _button(_label(key, value, session), token, f"{target}:{index}")
            )
        rows.extend(_rows(buttons))
        previous = f"group:{group}:{page - 1}" if page else None
        following = f"group:{group}:{page + 1}" if page + 1 < pages else None
    elif action == "providers" or action.startswith("providers:"):
        session.entries = sorted(values.get("agent_providers", {}))
        session.provider_name = None
        session.back = "groups:0"
        page, pages = _page(
            action.split(":")[1] if ":" in action else "0", len(session.entries)
        )
        session.entry_parent = f"providers:{page}"
        text = (
            _tr("providers", session)
            + "\n"
            + _tr(
                "providers_summary",
                session,
                count=len(session.entries),
                default="✅" if "default" in session.entries else "❌",
            )
        )
        text += "\n" + _tr("page", session, page=page + 1, pages=pages)
        buttons = [
            _button(session.entries[index], token, f"provider:{index}")
            for index in range(
                page * _PAGE_SIZE, min(len(session.entries), (page + 1) * _PAGE_SIZE)
            )
        ]
        rows.extend(_rows(buttons))
        rows.append([_button(_tr("add_provider", session), token, "add")])
        previous = f"providers:{page - 1}" if page else None
        following = f"providers:{page + 1}" if page + 1 < pages else None
    elif action.startswith("provider:") or action == "provider_view":
        if action == "provider_view":
            name = session.provider_name
            if name is None or name not in values.get("agent_providers", {}):
                raise SettingsEditError("stale_file")
        else:
            name = session.entries[int(action.split(":")[1])]
            session.provider_parent = session.entry_parent
        session.provider_name = name
        session.back = session.provider_parent
        session.entries = [
            f"agent_providers.{name}.{key}" for key in ProviderConfig.model_fields
        ]
        session.entry_parent = "provider_view"
        text = _variables_text(
            _tr("provider_title", session, name=html.escape(name)),
            session.entries,
            values,
            session,
        )
        rows.extend(
            _rows(
                [
                    _button(
                        _label(key, _entry_value(values, key), session),
                        token,
                        f"entry:{index}",
                    )
                    for index, key in enumerate(session.entries)
                ]
            )
        )
        rows.append([_button(_tr("delete_provider", session), token, "delete")])
    elif action.startswith("entry:"):
        index = int(action.split(":")[1])
        key = session.entries[index]
        value = _entry_value(values, key)
        session.back = session.entry_parent
        text = _tr(
            "entry_summary",
            session,
            key=html.escape(key),
            type=html.escape(type(value).__name__),
            value=html.escape(display_value(key, value)),
        )
        rows.append([_button(_tr("edit", session), token, f"edit:{index}")])
        if isinstance(value, bool):
            rows.append([_button(_tr("toggle", session), token, f"toggle:{index}")])
    else:
        raise SettingsEditError("invalid_value")
    rows.append(_footer(token, session, previous=previous, following=following))
    return text, InlineKeyboardMarkup(rows)


async def _edit(message: Message, text: str, markup=None) -> None:
    try:
        await message.edit_text(
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except MessageNotModified:
        pass


@Client.on_message(filters.command("config") & filters.private, group=0)
async def private_config_command(client: Client, message: Message):
    user = message.from_user
    if user is None or message.chat is None or not await _is_admin(user.id):
        await message.reply_text(_tr("denied"))
        return
    config = await database.get_user_config(user)
    _prune()
    for token, session in list(_SESSIONS.items()):
        if session.user_id == user.id:
            _SESSIONS.pop(token, None)
    try:
        editor = SettingsEditor(
            [Path(p) for p in _resolve_settings_files()],
            _AppConfig,
            ProviderConfig,
            lambda: app_config.model_dump(),
        )
        session = ConfigSession(user.id, message.chat.id, 0, config.lang, editor)
        token = secrets.token_hex(5)
        _SESSIONS[token] = session
        text, markup = _menu(token, session)
        reply = await message.reply_text(
            text, reply_markup=markup, parse_mode=enums.ParseMode.HTML
        )
        session.message_id = reply.id
        _SESSIONS[token] = session
    except SettingsEditError as exc:
        await message.reply_text(
            t("bot.private_config.errors." + str(exc), locale=config.lang)
        )


@Client.on_callback_query(filters.regex(r"^pcfg:"), group=0)
async def private_config_callback(client: Client, query: CallbackQuery):
    _prune()
    data = query.data
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    parts = str(data).split(":", 3)
    session = (
        _SESSIONS.get(parts[1])
        if len(parts) == 4 and parts[0] == _PREFIX and parts[2].isdigit()
        else None
    )
    message = query.message
    user = query.from_user
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
    if not await _is_admin(user.id):
        _SESSIONS.pop(parts[1], None)
        await query.answer(_tr("denied", session), show_alert=True)
        return
    token, view, action = parts[1:]
    async with session.lock:
        if int(view) != session.view:
            await query.answer(_tr("expired", session), show_alert=True)
            return
        previous_view = session.view
        previous_state = (
            list(session.entries),
            session.revision,
            session.location,
            session.back,
            session.entry_parent,
            session.provider_name,
            session.provider_parent,
            session.pending,
            session.confirmation,
        )
        session.view += 1
        session.pending = None
        session.expires = time.monotonic() + _SESSION_TTL
        answered = False
        committed = False
        try:
            if action == "close":
                _SESSIONS.pop(token, None)
                committed = True
                await query.answer()
                answered = True
                try:
                    await message.delete()
                except Exception:
                    await _edit(message, _tr("closed", session))
                return
            if action.startswith(("edit:", "toggle:")):
                index = int(action.split(":")[1])
                key = session.entries[index]
                if action.startswith("toggle:"):
                    values, revision = session.editor.snapshot()
                    value = _entry_value(values, key)
                    if not isinstance(value, bool) or revision != session.revision:
                        raise SettingsEditError("stale_file")
                    await asyncio.to_thread(
                        session.editor.set_value,
                        key,
                        str(not value).lower(),
                        session.revision,
                    )
                    session.dirty.add(key)
                    committed = True
                    await query.answer(_tr("saved", session), show_alert=True)
                    answered = True
                    action = (
                        session.location
                        if session.location.startswith("group:")
                        else f"entry:{index}"
                    )
                else:
                    session.pending = key
                    session.pending_until = time.monotonic() + _EDIT_TTL
                    await query.answer()
                    answered = True
                    await _edit(
                        message,
                        _tr("send_value", session, key=html.escape(key)),
                        InlineKeyboardMarkup([_footer(token, session)]),
                    )
                    return
            elif action == "add":
                session.pending = "+provider"
                session.pending_until = time.monotonic() + _EDIT_TTL
                await query.answer()
                answered = True
                await _edit(
                    message,
                    _tr("send_provider", session),
                    InlineKeyboardMarkup([_footer(token, session)]),
                )
                return
            elif action in {"delete", "delete_confirm"}:
                name = session.provider_name
                if name is None:
                    raise SettingsEditError("stale_file")
                if action == "delete":
                    session.confirmation = "delete"
                    await query.answer()
                    answered = True
                    await _edit(
                        message,
                        _tr("confirm_delete", session, name=html.escape(name)),
                        InlineKeyboardMarkup(
                            [
                                [
                                    _button(
                                        _tr("delete_provider", session),
                                        token,
                                        "delete_confirm",
                                    )
                                ],
                                _footer(token, session),
                            ]
                        ),
                    )
                    return
                if session.confirmation != "delete":
                    raise SettingsEditError("stale_file")
                await asyncio.to_thread(
                    session.editor.delete_provider, name, session.revision
                )
                session.dirty.add("agent_providers." + name)
                committed = True
                await query.answer(_tr("saved", session), show_alert=True)
                answered = True
                action = session.provider_parent
            elif action in {"restart", "restart_confirm"}:
                if action == "restart":
                    session.confirmation = "restart"
                    await query.answer()
                    answered = True
                    await _edit(
                        message,
                        _tr("confirm_restart", session),
                        InlineKeyboardMarkup(
                            [
                                [
                                    _button(
                                        _tr("restart", session),
                                        token,
                                        "restart_confirm",
                                    )
                                ],
                                _footer(token, session),
                            ]
                        ),
                    )
                    return
                if session.confirmation != "restart":
                    raise SettingsEditError("stale_file")
                # Validate the complete on-disk config before requesting a restart.
                session.editor.validate_for_restart()
                await query.answer()
                answered = True
                await _edit(message, _tr("restarting", session))
                _SESSIONS.pop(token, None)
                common.spawn(_restart(), name="private-config-restart")
                return
            text, markup = _menu(token, session, action)
            if not answered:
                await query.answer()
                answered = True
            await _edit(message, text, markup)
        except SettingsEditError as exc:
            error = _tr("errors." + str(exc), session)
            if not answered:
                await query.answer(error, show_alert=True)
            await _edit(message, error, InlineKeyboardMarkup([_footer(token, session)]))
        except (ValueError, IndexError, KeyError):
            if not answered:
                await query.answer(_tr("expired", session), show_alert=True)
            await _edit(
                message,
                _tr("expired", session),
                InlineKeyboardMarkup([_footer(token, session)]),
            )
        except Exception as exc:
            # If navigation could not update Telegram, the old visible keyboard
            # must remain usable. A completed file write is never rolled back.
            if not committed:
                session.view = previous_view
                (
                    session.entries,
                    session.revision,
                    session.location,
                    session.back,
                    session.entry_parent,
                    session.provider_name,
                    session.provider_parent,
                    session.pending,
                    session.confirmation,
                ) = previous_state
            logger.error(f"private config menu failed: {exc.__class__.__name__}")
            if not answered:
                await query.answer(_tr("errors.menu_failed", session), show_alert=True)


@Client.on_message(filters.private & filters.text, group=-110)
async def private_config_input(client: Client, message: Message):
    if message.from_user is None or message.chat is None or not message.text:
        return
    _prune()
    pair = next(
        (
            (token, s)
            for token, s in _SESSIONS.items()
            if s.user_id == message.from_user.id
            and s.chat_id == message.chat.id
            and s.pending
        ),
        None,
    )
    if pair is None:
        return
    token, session = pair
    async with session.lock:
        try:
            if not await _is_admin(message.from_user.id):
                _SESSIONS.pop(token, None)
                return
            key = session.pending
            session.pending = None
            if message.text == "/cancel":
                await client.edit_message_text(
                    session.chat_id,
                    session.message_id,
                    _tr("cancelled", session),
                    reply_markup=InlineKeyboardMarkup([_footer(token, session)]),
                )
                return
            if session.pending_until <= time.monotonic():
                raise SettingsEditError("edit_expired")
            if key == "+provider":
                await asyncio.to_thread(
                    session.editor.add_provider, message.text.strip(), session.revision
                )
                session.dirty.add("agent_providers")
            elif key:
                await asyncio.to_thread(
                    session.editor.set_value, key, message.text, session.revision
                )
                session.dirty.add(key)
            text, markup = _menu(token, session, session.entry_parent)
            await client.edit_message_text(
                session.chat_id,
                session.message_id,
                _tr("saved", session) + "\n\n" + text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )
        except SettingsEditError as exc:
            await client.edit_message_text(
                session.chat_id,
                session.message_id,
                _tr("errors." + str(exc), session),
                reply_markup=InlineKeyboardMarkup([_footer(token, session)]),
            )
        finally:
            try:
                await message.delete()
            except Exception:
                logger.debug("private config: could not delete input message")
            # A settings value is never also a prompt for the AI handler.
            message.stop_propagation()


async def _restart() -> None:
    await asyncio.sleep(2)
    # Matches process supervisors (Docker restart policy/systemd). Direct Python
    # runs must be launched again; the UI states this before confirmation.
    os.kill(os.getpid(), signal.SIGINT)
