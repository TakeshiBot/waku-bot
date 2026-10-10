"""Owner/global-admin settings menu, separate from /config in groups."""

from __future__ import annotations

import asyncio
import html
import secrets
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
from waku.plugins.menu_cleanup import schedule_saved_menu_cleanup
from waku.services.settings_editor import (
    SettingsEditError,
    SettingsEditor,
    display_value,
)
from waku.services.settings_runtime import SETTINGS_APPLICATION_LOCK

_PREFIX = "pcfg"
_SESSION_TTL = 15 * 60
_EDIT_TTL = 120
_PAGE_SIZE = 8
_MAX_SESSIONS = 100
_SETTINGS_SAVE_LOCK = SETTINGS_APPLICATION_LOCK
_RESTART_PENDING: set[str] = set()
_GROUPS = (
    "base",
    "agent",
    "discord",
    "webapp",
    "rss",
    "services",
    "cache",
    "economy",
    "business",
)
_ESSENTIAL_FIELDS = {
    "agent",
    "agent_model",
    "agent_prompt",
    "agent_group_prompt",
    "agent_streaming",
    "agent_rich_output",
    "business_chat_enabled",
    "manyacg_r18_mode",
}
_MENU_GROUPS = tuple(group for group in _GROUPS if group != "business") + (
    "providers",
    "business",
)
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
    changes: dict[str, str] = field(default_factory=dict)
    additions: set[str] = field(default_factory=set)
    deletions: set[str] = field(default_factory=set)
    values: dict = field(default_factory=dict)
    baseline: dict = field(default_factory=dict)
    field_keys: set[str] = field(default_factory=set)
    view: int = 0
    confirmation: str | None = None
    location: str = "home"
    back: str = "home"
    entry_parent: str = "groups:0"
    provider_name: str | None = None
    provider_parent: str = "providers:0"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    command_message_id: int | None = None


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
    row.append(_button(_tr("save", session), token, "save"))
    if (
        session.dirty
        and not _draft_count(session)
        and session.confirmation != "restart"
    ):
        row.append(_button(_tr("restart", session), token, "restart"))
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
    changed = "📝 " if key in session.changes else "🔄 " if key in session.dirty else ""
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
    if key.startswith("business_chat_"):
        return "business"
    if key.startswith("agent"):
        return "agent"
    if key.startswith("discord"):
        return "discord"
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


def _draft_count(session: ConfigSession) -> int:
    return len(session.changes) + len(session.additions) + len(session.deletions)


def _draft_values(session: ConfigSession) -> dict:
    if not session.values:
        values, revision = session.editor.snapshot()
        if session.revision and session.revision != revision:
            raise SettingsEditError("stale_file")
        session.revision = revision
        session.values = values
        session.baseline = values
        session.field_keys = session.editor.configured_keys() | _ESSENTIAL_FIELDS
    return session.values


async def _stage_setting(session: ConfigSession, key: str, text: str) -> None:
    _draft_values(session)
    changes = {**session.changes, key: text}
    values = await asyncio.to_thread(
        session.editor.preview,
        changes,
        session.revision,
        additions=session.additions,
        deletions=session.deletions,
    )
    # Toggling back to the baseline should leave no pending change.
    if _entry_value(values, key) == _entry_value(session.baseline, key):
        changes.pop(key)
    session.changes, session.values = changes, values


async def _stage_provider(session: ConfigSession, name: str, *, delete: bool = False):
    _draft_values(session)
    additions, deletions = set(session.additions), set(session.deletions)
    changes = dict(session.changes)
    if delete:
        if name in additions:
            additions.remove(name)
        else:
            deletions.add(name)
        changes = {
            key: text
            for key, text in changes.items()
            if not key.startswith(f"agent_providers.{name}.")
        }
    else:
        if name in session.values.get("agent_providers", {}):
            raise SettingsEditError("provider_exists")
        if name in deletions:
            deletions.remove(name)
        else:
            additions.add(name)
    values = await asyncio.to_thread(
        session.editor.preview,
        changes,
        session.revision,
        additions=additions,
        deletions=deletions,
    )
    session.changes, session.additions, session.deletions, session.values = (
        changes,
        additions,
        deletions,
        values,
    )


def _reset_draft(session: ConfigSession):
    session.changes.clear()
    session.additions.clear()
    session.deletions.clear()
    session.values, session.revision = session.editor.snapshot()
    session.baseline = session.values
    session.field_keys = session.editor.configured_keys() | _ESSENTIAL_FIELDS


def _visible_groups(session: ConfigSession):
    return [
        group
        for group in _MENU_GROUPS
        if group == "providers"
        or any(_group(key) == group for key in session.field_keys)
    ]


async def _refresh_restart_pending(session: ConfigSession):
    from waku.services.settings_runtime import settings_restart_fields

    candidate = session.editor.effective_candidate(_draft_values(session))
    changed = {
        key
        for key in _AppConfig.model_fields
        if getattr(candidate, key) != getattr(app_config, key)
    }
    pending = await asyncio.to_thread(settings_restart_fields, candidate, changed)
    _RESTART_PENDING.clear()
    _RESTART_PENDING.update(pending)
    session.dirty = set(pending)


async def _commit_draft(session: ConfigSession) -> str:
    async with _SETTINGS_SAVE_LOCK:
        if not await _is_admin(session.user_id):
            raise SettingsEditError("permission_changed")
        values = await asyncio.to_thread(
            session.editor.preview,
            session.changes,
            session.revision,
            additions=session.additions,
            deletions=session.deletions,
        )
        # The runtime plan prepares cached clients/models before persistence;
        # Saved bindings and optional Discord lifecycle changes activate after commit.
        from waku.services.settings_runtime import prepare_settings_application

        candidate = session.editor.effective_candidate(values)
        changed = set(session.changes)
        if session.additions or session.deletions:
            changed.add("agent_providers")
        try:
            plan = await prepare_settings_application(candidate, changed)
        except Exception:
            raise SettingsEditError("runtime_prepare_failed") from None
        try:
            if not await _is_admin(session.user_id):
                raise SettingsEditError("permission_changed")
            await asyncio.to_thread(
                session.editor.commit_batch,
                session.changes,
                session.revision,
                additions=session.additions,
                deletions=session.deletions,
            )
        except BaseException:
            await plan.discard()
            raise
        await plan.activate()
        _RESTART_PENDING.update(plan.restart_fields)
        _RESTART_PENDING.difference_update(
            {key.split(".", 1)[0] for key in changed} - plan.restart_fields
        )
        session.dirty = set(_RESTART_PENDING)
        _reset_draft(session)
        notice = (
            _tr("saved_restart", session, count=len(session.dirty))
            if session.dirty
            else _tr("saved", session)
        )
        if any(
            candidate.model_dump()[key.split(".", 1)[0]] != values[key.split(".", 1)[0]]
            for key in changed
        ):
            notice += "\n" + _tr("environment_override", session)
        if (
            "business_chat_enabled" in changed
            and app_config.business_chat_enabled
            and not (app_config.agent and app_config.agent_model)
        ):
            notice += "\n" + _tr("business_requires_agent", session)
        if plan.discord_status is not None:
            notice += "\n" + _tr(
                "discord_failed"
                if plan.discord_status == "error"
                else "discord_stopped"
                if not app_config.discord_enabled
                else "discord_starting",
                session,
            )
        return notice


def _menu(token: str, session: ConfigSession, action: str = "home"):
    session.dirty = set(_RESTART_PENDING)
    session.view += 1
    session.confirmation = None
    values = _draft_values(session)
    visible_groups = _visible_groups(session)
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
            count=_draft_count(session),
        )
        if session.dirty:
            text += "\n" + _tr("restart_pending", session, count=len(session.dirty))
        rows.append(
            [
                _button(_tr("open_settings", session), token, "groups:0"),
            ]
        )
        session.back = "home"
        rows.append(
            [
                button
                for button in _footer(token, session)
                if not button.callback_data.endswith(":home")
            ]
        )
        return text, InlineKeyboardMarkup(rows)
    elif action.startswith("groups:"):
        page, pages = _page(action.split(":")[1], len(visible_groups))
        session.entries = []
        session.provider_name = None
        session.back = "home"
        text = (
            _tr("variables_title", session)
            + "\n"
            + _tr(
                "groups_summary",
                session,
                count=len(visible_groups),
                page=page + 1,
                pages=pages,
                changed=_draft_count(session),
            )
        )
        groups = [
            (
                group,
                _tr(
                    "providers" if group == "providers" else "groups." + group, session
                ),
            )
            for group in visible_groups
        ]
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
            for key in session.field_keys
            if key not in {"agent_providers", "agent_powermem_config"}
            and _group(key) == group
        )
        session.entries = keys
        session.provider_name = None
        session.back = f"groups:{visible_groups.index(group) // _PAGE_SIZE}"
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
        session.back = f"groups:{visible_groups.index('providers') // _PAGE_SIZE}"
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
        session.command_message_id = message.id
        await _refresh_restart_pending(session)
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
        if _SESSIONS.get(token) is not session or session.expires <= time.monotonic():
            await query.answer(_tr("expired", session), show_alert=True)
            return
        # A queued callback may have lost authority while waiting for the menu.
        if not await _is_admin(user.id):
            _SESSIONS.pop(token, None)
            await query.answer(_tr("denied", session), show_alert=True)
            return
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
            dict(session.changes),
            set(session.additions),
            set(session.deletions),
            session.values,
            session.baseline,
            set(session.field_keys),
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
            if action == "save":
                # Discord connection shutdown can take several seconds. Clear
                # Telegram's button spinner before committing/activating services.
                await query.answer()
                answered = True
                notice = (
                    await _commit_draft(session)
                    if _draft_count(session)
                    else (
                        _tr("saved_restart", session, count=len(session.dirty))
                        if session.dirty
                        else _tr("saved", session)
                    )
                )
                _SESSIONS.pop(token, None)
                committed = True
                try:
                    await _edit(message, notice)
                finally:
                    schedule_saved_menu_cleanup(
                        client, message, session.command_message_id
                    )
                return
            elif action in {"discard", "reload"}:
                _reset_draft(session)
                await _refresh_restart_pending(session)
                action = "home"
            elif action.startswith(("edit:", "toggle:")):
                index = int(action.split(":")[1])
                key = session.entries[index]
                if action.startswith("toggle:"):
                    values = _draft_values(session)
                    value = _entry_value(values, key)
                    if not isinstance(value, bool):
                        raise SettingsEditError("stale_file")
                    await _stage_setting(session, key, str(not value).lower())
                    await query.answer(_tr("staged", session), show_alert=True)
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
                await _stage_provider(session, name, delete=True)
                await query.answer(_tr("staged", session), show_alert=True)
                answered = True
                action = session.provider_parent
            elif action in {"restart", "restart_confirm"}:
                if _draft_count(session):
                    raise SettingsEditError("save_first")
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
                    session.changes,
                    session.additions,
                    session.deletions,
                    session.values,
                    session.baseline,
                    session.field_keys,
                ) = previous_state
            logger.error(f"private config menu failed: {exc.__class__.__name__}")
            if not answered:
                await query.answer(_tr("errors.menu_failed", session), show_alert=True)


@Client.on_message(filters.private, group=-110)
async def private_config_input(client: Client, message: Message):
    if message.from_user is None or message.chat is None:
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
            if _SESSIONS.get(token) is not session:
                return
            if not await _is_admin(message.from_user.id):
                _SESSIONS.pop(token, None)
                return
            value_text = common.message_plain_text(message)
            if not value_text:
                raise SettingsEditError("invalid_value")
            key = session.pending
            session.pending = None
            if value_text == "/cancel":
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
                await _stage_provider(session, value_text.strip())
            elif key:
                await _stage_setting(session, key, value_text)
            text, markup = _menu(token, session, session.entry_parent)
            await client.edit_message_text(
                session.chat_id,
                session.message_id,
                _tr("staged", session) + "\n\n" + text,
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
    from waku.services.process_restart import request_restart

    request_restart()
