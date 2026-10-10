"""Save/cancel confirmation for single Telegram preference commands."""

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from pyrogram import enums, filters, types
from pyrogram.client import Client

from waku import database, i18n
from waku.common.telegram_authority import (
    can_manage_bot_settings,
    can_manage_group_settings,
)
from waku.logger import logger
from waku.plugins.menu_cleanup import schedule_saved_menu_cleanup

_TTL = 15 * 60


@dataclass
class PreferenceDraft:
    owner_id: int
    chat_id: int
    lang: str
    values: dict[str, object]
    private: bool = False
    baseline: dict[str, object] | None = None
    chat_config_private: bool = False
    message_id: int | None = None
    expires: float = field(default_factory=lambda: time.monotonic() + _TTL)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    command_message_id: int | None = None


_drafts: dict[str, PreferenceDraft] = {}


def _prune() -> None:
    for token, draft in tuple(_drafts.items()):
        if time.monotonic() >= draft.expires:
            _drafts.pop(token, None)
    while len(_drafts) >= 500:
        _drafts.pop(next(iter(_drafts)))


def create_draft(
    owner_id: int,
    chat_id: int,
    lang: str,
    values: dict[str, object],
    *,
    private=False,
    baseline: dict[str, object] | None = None,
    chat_config_private: bool = False,
    command_message_id: int | None = None,
) -> str:
    """Only internal callers choose fields; callback data carries no config values."""
    allowed = (
        {"lang"}
        if private
        else {
            "lang",
            "greeting",
            "rss_agent_summary",
            "rss_agent_broadcast",
            "quote_probability",
        }
    )
    if not values or values.keys() - allowed:
        raise ValueError("Unsupported preference fields")
    if chat_config_private and (
        private
        or chat_id != owner_id
        or type(owner_id) is not int
        or owner_id <= 0
        or values.keys() - {"rss_agent_summary", "rss_agent_broadcast"}
    ):
        raise ValueError("Invalid private RSS preference scope")
    _prune()
    token = secrets.token_hex(8)
    _drafts[token] = PreferenceDraft(
        owner_id,
        chat_id,
        lang,
        dict(values),
        private,
        dict(baseline) if baseline is not None else None,
        chat_config_private,
        command_message_id=command_message_id,
    )
    return token


def save_markup(token: str, lang: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    i18n.t("bot.preference_save.save", locale=lang),
                    callback_data=f"prefs:{token}:save",
                ),
            ]
        ]
    )


def bind_draft(token: str, message_id: int) -> None:
    _drafts[token].message_id = message_id


def bound_draft(
    token: str, actor_id: int, chat_id: int, message_id: int
) -> PreferenceDraft:
    draft = _drafts.get(token)
    if (
        not draft
        or time.monotonic() >= draft.expires
        or draft.owner_id != actor_id
        or draft.chat_id != chat_id
        or draft.message_id != message_id
    ):
        raise PermissionError("Menu expired or belongs to another user")
    return draft


async def preview_preference(
    message: types.Message,
    values: dict[str, object],
    text: str,
    lang: str,
    *,
    chat_config_private=False,
) -> None:
    if not message.from_user or not message.chat:
        return
    private = message.chat.type == enums.ChatType.PRIVATE and not chat_config_private
    current = (
        await database.get_user_config(message.from_user.id)
        if private
        else await database.get_chat_config(message.chat)
    )
    token = create_draft(
        message.from_user.id,
        message.chat.id,
        lang,
        values,
        private=private,
        baseline={name: getattr(current, name) for name in values},
        chat_config_private=chat_config_private,
        command_message_id=message.id,
    )
    draft = _drafts[token]
    try:
        sent = await message.reply_text(
            text + "\n\n" + i18n.t("bot.preference_save.notice", locale=lang),
            parse_mode=enums.ParseMode.DISABLED,
            reply_markup=save_markup(token, lang),
        )
        draft.message_id = sent.id
    except BaseException:
        _drafts.pop(token, None)
        raise


async def apply_draft(
    client: Client,
    token: str,
    actor_id: int,
    chat_id: int,
    message_id: int,
    action: str,
) -> tuple[str, str]:
    draft = bound_draft(token, actor_id, chat_id, message_id)
    async with draft.lock:
        if _drafts.get(token) is not draft or time.monotonic() >= draft.expires:
            raise PermissionError("Menu expired")
        if action == "cancel":
            _drafts.pop(token, None)
            return "cancelled", draft.lang
        if action != "save":
            raise ValueError("Unknown preference action")
        if not draft.private and not draft.chat_config_private:
            check = (
                can_manage_group_settings
                if draft.values.keys() & {"lang", "greeting"}
                else can_manage_bot_settings
            )
            if not await check(client, actor_id, chat_id):
                raise PermissionError("Administrator rights changed")
        if draft.private:
            await database.patch_user_preferences(actor_id, **draft.values)
        else:
            kwargs = (
                {"expected_fields": draft.baseline}
                if draft.baseline is not None
                else {}
            )
            await database.update_chat_config_fields(chat_id, draft.values, **kwargs)
        _drafts.pop(token, None)
        return "saved", str(draft.values.get("lang", draft.lang))


@Client.on_callback_query(filters.regex(r"^prefs:"), group=0)
async def save_preference_callback(client: Client, query: types.CallbackQuery):
    if not query.from_user or not query.message or not query.message.chat:
        return
    data = (
        query.data.decode("utf-8", errors="replace")
        if isinstance(query.data, bytes)
        else str(query.data)
    )
    parts = data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    draft = _drafts.get(parts[1])
    lang = draft.lang if draft else "vi"
    try:
        outcome, lang = await apply_draft(
            client,
            parts[1],
            query.from_user.id,
            query.message.chat.id,
            query.message.id,
            parts[2],
        )
    except ValueError as error:
        key = "conflict" if str(error) == "config_conflict" else "denied"
        await query.answer(
            i18n.t(f"bot.preference_save.{key}", locale=lang), show_alert=True
        )
        return
    except PermissionError:
        await query.answer(
            i18n.t("bot.preference_save.denied", locale=lang), show_alert=True
        )
        return
    except Exception as error:
        logger.warning("Telegram preference save failed: {}", type(error).__name__)
        await query.answer(
            i18n.t("bot.preference_save.failed", locale=lang), show_alert=True
        )
        return
    try:
        await query.answer()
        await query.edit_message_text(
            i18n.t(f"bot.preference_save.{outcome}", locale=lang), reply_markup=None
        )
    finally:
        schedule_saved_menu_cleanup(
            client, query.message, draft.command_message_id if draft else None
        )
