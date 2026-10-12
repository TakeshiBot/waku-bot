"""Private, verified-owner controls for a connected Business account."""

from __future__ import annotations

import asyncio
import html
import secrets
import time
from dataclasses import dataclass, field

from pyrogram import Client, enums, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from waku import common, database
from waku.database import business as repository
from waku.i18n import t
from waku.logger import logger
from waku.plugins.menu_cleanup import schedule_saved_menu_cleanup
from waku.services import business_context as context
from waku.services import business_ops as operations

_SESSIONS = {}
_GROUPS = {
    "chat": (
        "send_message",
        "send_media",
        "edit_message",
        "mark_read",
        "delete_sent",
        "delete_all",
    ),
    "profile": ("set_name", "set_bio", "set_username", "set_photo", "remove_photo"),
    "gifts": (
        "get_gifts",
        "get_stars",
        "convert_gift",
        "upgrade_gift",
        "transfer_gift",
        "transfer_stars",
        "gift_settings",
    ),
    "stories": ("post_story", "edit_story", "delete_story"),
}


@dataclass
class BusinessSession:
    owner_id: int
    chat_id: int
    message_id: int
    connection_id: str
    locale: str
    settings: dict
    command_id: int | None = None
    expires: float = field(default_factory=lambda: time.monotonic() + 900)
    view: int = 0
    location: str = "home"
    pending: str | None = None
    pending_until: float = 0
    action_token: str | None = None
    review: dict = field(default_factory=dict)
    gift_settings: dict = field(default_factory=dict)
    gift_offsets: list[str] = field(default_factory=lambda: [""])
    gift_page: int = 0
    gift_next: str = ""
    note_ids: list[str] = field(default_factory=list)
    cancel_note_id: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _tr(key, session=None, **values):
    return t("bot.business." + key, locale=session.locale if session else "").format(
        **values
    )


def _prune():
    for token, session in list(_SESSIONS.items()):
        if session.expires < time.monotonic():
            _SESSIONS.pop(token, None)
    while len(_SESSIONS) > 100:
        _SESSIONS.pop(next(iter(_SESSIONS)))


def _error_text(error, session=None):
    text = _tr("errors." + error.code, session)
    if error.code == "api_rejected" and error.detail:
        text += "\n" + html.escape(error.detail)
    elif error.code == "missing_right" and error.detail:
        text += "\n" + _tr("rights_labels." + error.detail, session)
    return text


def _button(token, label, action):
    return InlineKeyboardButton(
        label, callback_data=f"busi:{token}:{_SESSIONS[token].view}:{action}"
    )


def _footer(token):
    session = _SESSIONS[token]
    buttons = []
    if session.location != "home":
        buttons.append(_button(token, "↩️", "home"))
    if session.location not in {"input", "gift_settings"}:
        buttons.append(_button(token, _tr("save", session), "save"))
    buttons.append(_button(token, _tr("close", session), "close"))
    return buttons


def _rows(buttons):
    return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]


async def _owner_connection(client, session):
    connection = await context.ensure_connection(client, session.connection_id)
    if connection.user.id != session.owner_id or session.chat_id != session.owner_id:
        raise operations.BusinessOperationError("owner_only")
    if not connection.is_enabled:
        raise operations.BusinessOperationError("connection_disabled")
    return connection


async def _render(client, token, location="home"):
    session = _SESSIONS[token]
    connection = await _owner_connection(client, session)
    rights = context.rights_snapshot(connection.rights)
    session.location = location
    profile = context.profile(connection.user)
    text = _tr("title", session, name=html.escape(profile["name"]))
    rows = []
    if location == "home":
        text += "\n" + _tr("notice", session)
        buttons = [
            _button(
                token,
                ("✅ " if value else "❌ ") + _tr("settings." + key, session),
                "toggle:" + key,
            )
            for key, value in session.settings.items()
        ]
        rows.extend(_rows(buttons))
        rows.extend(
            _rows(
                [
                    _button(token, _tr("groups." + group, session), "group:" + group)
                    for group in _GROUPS
                ]
            )
        )
        rows.append(
            [
                _button(token, _tr("rights", session), "rights"),
                _button(token, _tr("dialogs", session), "dialogs"),
            ]
        )
        rows.append([_button(token, _tr("notes", session), "notes")])
    elif location == "rights":
        text += "\n" + "\n".join(
            ("✅ " if granted else "❌ ") + _tr("rights_labels." + key, session)
            for key, granted in rights.items()
        )
    elif location == "dialogs":
        dialogs = await repository.list_dialogs(session.owner_id)
        text += "\n" + _tr("dialogs_notice", session)
        for peer, state in dialogs[:15]:
            messages = state.get("events", [])[-5:]
            text += (
                "\n"
                + html.escape(state.get("contact", {}).get("name", "")[:60])
                + f" · <code>{peer}</code>"
            )
            text += "\n" + " · ".join(
                f"{_tr('roles.' + message['role'], session)}: <code>{message['id']}</code>"
                for message in messages
            )
        if not dialogs:
            text += "\n" + _tr("no_dialogs", session)
    elif location == "notes":
        from datetime import datetime

        from waku.database import business_notes
        from waku.timezone import BOT_TIMEZONE

        notes = await business_notes.list_notes(session.owner_id)
        session.note_ids = [note["id"] for note in notes[:10]]
        for index, note in enumerate(notes[:10]):
            text += (
                f"\n\n{index + 1}. "
                + html.escape(
                    (note["contact"].get("name") or str(note["peer_id"]))[:60]
                )
                + "\n"
                + html.escape(note["content"][:120])
            )
            if note["remind_at"]:
                text += "\n" + datetime.fromtimestamp(
                    note["remind_at"], BOT_TIMEZONE
                ).strftime("%H:%M %d/%m/%Y")
                text += " · " + _tr("delivery." + note["reminder_status"], session)
                if note["reminder_status"] in {"pending", "failed"}:
                    rows.append(
                        [
                            _button(
                                token,
                                _tr("cancel_appointment", session, number=index + 1),
                                "cancel_note:" + str(index),
                            )
                        ]
                    )
        if not notes:
            text += "\n" + _tr("no_notes", session)
    elif location.startswith("group:"):
        group = location.split(":", 1)[1]
        text += (
            "\n"
            + _tr("groups." + group, session)
            + "\n"
            + _tr("management_notice", session)
        )
        rows.extend(
            _rows(
                [
                    _button(
                        token,
                        (
                            "✅ "
                            if rights[operations.OPERATIONS[operation][0]]
                            else "❌ "
                        )
                        + _tr("operations." + operation, session),
                        "op:" + operation,
                    )
                    for operation in _GROUPS[group]
                ]
            )
        )
    elif location == "gift_settings":
        text += "\n" + _tr("gift_settings_notice", session)
        rows.extend(
            _rows(
                [
                    _button(
                        token,
                        ("✅ " if value else "❌ ") + _tr("gift_types." + key, session),
                        "gift:" + key,
                    )
                    for key, value in {
                        "show_gift_button": session.gift_settings["show_gift_button"],
                        **session.gift_settings["accepted_gift_types"],
                    }.items()
                ]
            )
        )
        rows.append([_button(token, _tr("review", session), "gift_review")])
    rows.append(_footer(token))
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


async def _show_gifts(client, token):
    session = _SESSIONS[token]
    result = await operations.execute(
        client,
        session.owner_id,
        session.connection_id,
        "get_gifts",
        {"offset": session.gift_offsets[session.gift_page]},
    )
    text = (
        _tr("operations.get_gifts", session)
        + f" · {session.gift_page + 1}\n{_tr('total', session)}: {int(result['total_count'])}"
    )
    for gift in result.get("gifts", []):
        item = gift.get("gift", {})
        text += (
            "\n"
            + html.escape(str(item.get("name", item.get("id", "Gift")))[:40])
            + " · <code>"
            + html.escape(gift.get("owned_gift_id") or "—")
            + "</code>"
        )
        costs = []
        for key, label in (
            ("convert_star_count", "convert_gift"),
            ("transfer_star_count", "transfer_gift"),
        ):
            if key in gift:
                costs.append(
                    _tr("operations." + label, session) + f": {int(gift[key])}⭐"
                )
        if "upgrade_star_count" in item:
            costs.append(
                _tr("operations.upgrade_gift", session)
                + f": {int(item['upgrade_star_count'])}⭐"
            )
        if costs:
            text += "\n" + " · ".join(costs)
    session.gift_next = result.get("next_offset") or ""
    session.location = "gift_list"
    paging = []
    if session.gift_page:
        paging.append(_button(token, "◀️", "gifts_prev"))
    if session.gift_next:
        paging.append(_button(token, "▶️", "gifts_next"))
    await _edit(
        client,
        session,
        text,
        InlineKeyboardMarkup(([paging] if paging else []) + [_footer(token)]),
    )


async def open_business_panel(client, message, owner_id, locale=None, *, replace=False):
    if (
        not message.chat
        or message.chat.type != enums.ChatType.PRIVATE
        or message.chat.id != owner_id
    ):
        raise operations.BusinessOperationError("owner_only")
    state = await repository.get_state(owner_id, 0)
    connection_id = state.get("connection", {}).get("id")
    if not connection_id:
        raise operations.BusinessOperationError("no_connection")
    connection = await context.ensure_connection(client, connection_id)
    if connection.user.id != owner_id:
        raise operations.BusinessOperationError("owner_only")
    if not replace:
        from waku.plugins.private_config import _SESSIONS as config_sessions

        for old_token, old_session in list(config_sessions.items()):
            if old_session.user_id == owner_id:
                config_sessions.pop(old_token, None)
    _prune()
    for token, session in list(_SESSIONS.items()):
        if session.owner_id == owner_id:
            _SESSIONS.pop(token, None)
    session = BusinessSession(
        owner_id,
        message.chat.id,
        message.id if replace else 0,
        connection_id,
        locale or "",
        dict(context.settings_for(owner_id)),
    )
    session.command_id = None if replace else message.id
    token = secrets.token_hex(6)
    _SESSIONS[token] = session
    try:
        text, markup = await _render(client, token)
        if replace:
            await _edit(client, session, text, markup)
        else:
            reply = await message.reply_text(
                text, reply_markup=markup, parse_mode=enums.ParseMode.HTML
            )
            session.message_id = reply.id
        from waku.services.business_notes import drain_notes

        common.spawn(
            drain_notes(client, owner_id=owner_id, retry_failed=True),
            name="business-owner-inbox-recovery",
        )
    except BaseException:
        _SESSIONS.pop(token, None)
        raise


@Client.on_message(filters.private & filters.command("business"), group=-120)
async def business_command(client, message):
    if not message.from_user:
        return
    try:
        config = await database.get_user_config(message.from_user)
        await open_business_panel(client, message, message.from_user.id, config.lang)
    except operations.BusinessOperationError as error:
        await message.reply_text(_tr("errors." + error.code))
    except Exception as error:
        logger.warning("Business command failed: {}", type(error).__name__)
        await message.reply_text(_tr("errors.uncertain"))
    message.stop_propagation()


async def _stage_action(client, token, operation, arguments):
    session = _SESSIONS[token]
    await operations.guard(
        client, session.owner_id, session.connection_id, operation, arguments
    )
    nonce = secrets.token_hex(16)
    await repository.create_action(
        nonce, session.owner_id, session.connection_id, operation, arguments
    )
    session.action_token = nonce
    session.pending = None
    session.review = {"operation": operation, "arguments": arguments}
    session.location = "review"
    preview = {key: value for key, value in arguments.items() if key != "media"}
    if arguments.get("media"):
        preview["media"] = arguments["media"]["kind"]
    text = _tr(
        "review_title",
        session,
        operation=html.escape(_tr("operations." + operation, session)),
    )
    for key, value in preview.items():
        if key == "accepted_gift_types":
            value = (
                ", ".join(
                    _tr("gift_types." + name, session)
                    for name, enabled in value.items()
                    if enabled
                )
                or "—"
            )
        elif type(value) is bool:
            value = "✅" if value else "❌"
        elif isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        escaped = html.escape(str(value))
        if len(escaped) > 2200:
            # Never cut an HTML entity; keep the actual request complete in storage.
            escaped = html.escape(str(value)[:300]) + "…"
        text += "\n" + _tr("fields." + key, session) + ": " + escaped
    if operation == "transfer_stars":
        text += "\n" + _tr("stars_destination", session)
    text += "\n" + _tr("review_notice", session)
    await _edit(client, session, text, InlineKeyboardMarkup([_footer(token)]))


@Client.on_callback_query(filters.regex(r"^busi:"), group=0)
async def business_callback(client, query):
    _prune()
    parts = str(
        query.data.decode() if isinstance(query.data, bytes) else query.data
    ).split(":", 3)
    session = _SESSIONS.get(parts[1]) if len(parts) == 4 else None
    message = query.message
    if (
        not session
        or not query.from_user
        or not message
        or not message.chat
        or message.chat.type != enums.ChatType.PRIVATE
        or (query.from_user.id, message.chat.id, message.id)
        != (session.owner_id, session.chat_id, session.message_id)
        or not parts[2].isdigit()
    ):
        await query.answer(_tr("errors.expired"), show_alert=True)
        return
    token, _, action = parts[1:]
    async with session.lock:
        if _SESSIONS.get(token) is not session or int(parts[2]) != session.view:
            await query.answer(_tr("errors.expired", session), show_alert=True)
            return
        await query.answer()
        session.view += 1
        session.expires = time.monotonic() + 900
        if action == "save" and session.location in {"input", "gift_settings"}:
            await _edit(
                client,
                session,
                _tr("errors.review_first", session),
                InlineKeyboardMarkup([_footer(token)]),
            )
            return
        session.pending = None
        try:
            if action == "close":
                _SESSIONS.pop(token, None)
                await client.delete_messages(session.chat_id, session.message_id)
                return
            await _owner_connection(client, session)
            if action == "save":
                if session.cancel_note_id and session.location == "cancel_note":
                    from waku.database import business_notes

                    if not await business_notes.cancel_reminder(
                        session.cancel_note_id, session.owner_id
                    ):
                        raise operations.BusinessOperationError("cancel_unavailable")
                    text = _tr("appointment_cancelled", session)
                elif session.action_token and session.location == "review":
                    result = await operations.execute_reviewed_action(
                        client, session.owner_id, session.action_token
                    )
                    text = _tr("completed", session)
                    if isinstance(result, dict):
                        for key in ("id", "message_id"):
                            if result.get(key):
                                text += f"\nID: <code>{int(result[key])}</code>"
                    if result is not True and not isinstance(result, dict):
                        raise operations.BusinessOperationError("uncertain")
                else:
                    await context.set_settings(session.owner_id, session.settings)
                    from waku.plugins.business_chat import account_settings_changed

                    await account_settings_changed(session.owner_id)
                    text = _tr("saved", session)
                _SESSIONS.pop(token, None)
                await _edit(client, session, text)
                schedule_saved_menu_cleanup(client, message, session.command_id)
                return
            if action == "home":
                session.action_token = None
                session.review = {}
                session.cancel_note_id = None
            elif action.startswith("cancel_note:"):
                if session.location != "notes":
                    raise operations.BusinessOperationError("expired")
                index = int(action.split(":", 1)[1])
                if not 0 <= index < len(session.note_ids):
                    raise operations.BusinessOperationError("expired")
                session.cancel_note_id = session.note_ids[index]
                session.location = "cancel_note"
                await _edit(
                    client,
                    session,
                    _tr("confirm_cancel_appointment", session),
                    InlineKeyboardMarkup([_footer(token)]),
                )
                return
            elif action in {"gifts_next", "gifts_prev"}:
                if session.location != "gift_list":
                    raise operations.BusinessOperationError("expired")
                if action == "gifts_next" and session.gift_next:
                    session.gift_page += 1
                    session.gift_offsets = session.gift_offsets[: session.gift_page] + [
                        session.gift_next
                    ]
                elif action == "gifts_prev" and session.gift_page:
                    session.gift_page -= 1
                else:
                    raise operations.BusinessOperationError("expired")
                await _show_gifts(client, token)
                return
            elif action.startswith("toggle:"):
                key = action.split(":", 1)[1]
                if key not in context.DEFAULT_SETTINGS:
                    raise operations.BusinessOperationError("invalid_input")
                session.settings[key] = not session.settings[key]
                action = "home"
            elif action.startswith("op:"):
                operation = action.split(":", 1)[1]
                if session.settings != context.settings_for(session.owner_id):
                    raise operations.BusinessOperationError("save_first")
                # Right checks apply before displaying a form as well as on Save.
                connection = await _owner_connection(client, session)
                if operation not in operations.OPERATIONS:
                    raise operations.BusinessOperationError("invalid_input")
                if not session.settings["manage"]:
                    raise operations.BusinessOperationError("management_disabled")
                if not context.rights_snapshot(connection.rights)[
                    operations.OPERATIONS[operation][0]
                ]:
                    raise operations.BusinessOperationError("missing_right")
                if operation in operations.READ_ONLY:
                    if operation == "get_gifts":
                        session.gift_page = 0
                        session.gift_offsets = [""]
                        await _show_gifts(client, token)
                        return
                    result = await operations.execute(
                        client, session.owner_id, session.connection_id, operation, {}
                    )
                    text = _tr("operations." + operation, session)
                    if operation == "get_stars":
                        text += f"\n{result['amount']}"
                        if result.get("nanostar_amount"):
                            text += f" + {result['nanostar_amount']}/1.000.000.000"
                    else:
                        text += (
                            f"\n{_tr('total', session)}: {int(result['total_count'])}"
                        )
                        for gift in result.get("gifts", []):
                            item = gift.get("gift", {})
                            text += (
                                "\n"
                                + html.escape(
                                    str(item.get("name", item.get("id", "Gift")))
                                )
                                + " · <code>"
                                + html.escape(gift.get("owned_gift_id", "—"))
                                + "</code>"
                            )
                    session.location = "read_only"
                    await _edit(
                        client,
                        session,
                        text[:3900],
                        InlineKeyboardMarkup([_footer(token)]),
                    )
                    return
                if operation == "gift_settings":
                    state = await repository.get_state(session.owner_id, 0)
                    previous = state.get("gift_settings", {})
                    session.gift_settings = {
                        "show_gift_button": bool(
                            previous.get("show_gift_button", False)
                        ),
                        "accepted_gift_types": {
                            key: bool(
                                previous.get("accepted_gift_types", {}).get(key, False)
                            )
                            for key in operations.GIFT_TYPES
                        },
                    }
                    action = "gift_settings"
                elif operation == "remove_photo":
                    await _stage_action(
                        client, token, operation, operations.parse_input(operation, "")
                    )
                    return
                else:
                    session.pending = operation
                    session.pending_until = time.monotonic() + 120
                    session.location = "input"
                    await _edit(
                        client,
                        session,
                        _tr("help." + operation, session),
                        InlineKeyboardMarkup([_footer(token)]),
                    )
                    return
            elif action.startswith("gift:"):
                key = action.split(":", 1)[1]
                if key == "show_gift_button":
                    session.gift_settings[key] = not session.gift_settings[key]
                elif key in operations.GIFT_TYPES:
                    session.gift_settings["accepted_gift_types"][
                        key
                    ] = not session.gift_settings["accepted_gift_types"][key]
                else:
                    raise operations.BusinessOperationError("invalid_input")
                action = "gift_settings"
            elif action == "gift_review":
                await _stage_action(
                    client, token, "gift_settings", session.gift_settings
                )
                return
            if action not in {
                "home",
                "rights",
                "dialogs",
                "notes",
                "gift_settings",
            } and action not in {"group:" + group for group in _GROUPS}:
                raise operations.BusinessOperationError("invalid_input")
            text, markup = await _render(client, token, action)
            await _edit(client, session, text, markup)
        except operations.BusinessOperationError as error:
            session.action_token = None
            session.location = "home"
            text, markup = (
                await _render(client, token)
                if error.code not in {"owner_only", "connection_disabled"}
                else (_tr("errors." + error.code, session), None)
            )
            await _edit(
                client,
                session,
                _error_text(error, session) + "\n" + text,
                markup,
            )
        except Exception as error:
            logger.warning("Business panel failed: {}", type(error).__name__)
            _SESSIONS.pop(token, None)
            await _edit(client, session, _tr("errors.uncertain", session))


@Client.on_message(filters.private, group=-121)
async def business_panel_input(client, message):
    if not message.from_user or not message.chat or message.business_connection_id:
        return
    _prune()
    pair = next(
        (
            (token, session)
            for token, session in _SESSIONS.items()
            if session.owner_id == message.from_user.id == message.chat.id
            and session.pending
        ),
        None,
    )
    if not pair:
        return
    token, session = pair
    text = common.message_plain_text(message)
    if text.lstrip().startswith("/"):
        session.pending = None
        if text.strip() != "/cancel":
            return
        session.view += 1
        content, markup = await _render(client, token)
        await _edit(client, session, content, markup)
        message.stop_propagation()
    async with session.lock:
        if _SESSIONS.get(token) is not session or not session.pending:
            message.stop_propagation()
        try:
            if session.pending_until < time.monotonic():
                session.pending = None
                raise operations.BusinessOperationError("expired")
            await _owner_connection(client, session)
            operation = session.pending
            arguments = operations.parse_input(
                operation, text, operations.input_media(operation, message)
            )
            session.view += 1
            await _stage_action(client, token, operation, arguments)
        except operations.BusinessOperationError as error:
            await _edit(
                client,
                session,
                _error_text(error, session)
                + "\n"
                + _tr("help." + (session.pending or "send_message"), session),
                InlineKeyboardMarkup([_footer(token)]),
            )
        except Exception as error:
            logger.warning("Business panel input failed: {}", type(error).__name__)
            session.pending = None
            await _edit(
                client,
                session,
                _tr("errors.uncertain", session),
                InlineKeyboardMarkup([_footer(token)]),
            )
    message.stop_propagation()
