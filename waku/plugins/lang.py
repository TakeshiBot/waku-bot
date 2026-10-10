"""Language selection is a draft until the caller presses Save."""

from pyrogram import enums, filters, types
from pyrogram.client import Client

from waku import database, i18n
from waku.common.telegram_authority import can_manage_group_settings
from waku.common.utils import GROUP_CHAT_TYPES
from waku.plugins.preference_save import (
    bind_draft,
    bound_draft,
    create_draft,
    save_markup,
)


def _markup(token: str, lang: str) -> types.InlineKeyboardMarkup:
    locales = i18n.i18n.get_available_locales()
    rows = [
        [
            types.InlineKeyboardButton(
                f"{'✓ ' if locale == lang else ''}{locale}",
                callback_data=f"lprefs:{token}:{locale}",
            )
            for locale in locales[index : index + 4]
        ]
        for index in range(0, len(locales), 4)
    ]
    rows.extend(save_markup(token, lang).inline_keyboard)
    return types.InlineKeyboardMarkup(rows)


@Client.on_message(filters.command("lang") & (filters.private | filters.group), group=0)
async def change_lang_command(client: Client, message: types.Message):
    if not message.from_user or not message.chat:
        return
    private = message.chat.type == enums.ChatType.PRIVATE
    if not private and message.chat.type not in GROUP_CHAT_TYPES:
        return
    config = (
        await database.get_user_config(message.from_user.id)
        if private
        else await database.get_chat_config(message.chat)
    )
    if not private and not await can_manage_group_settings(
        client, message.from_user.id, message.chat.id
    ):
        await message.reply_text(
            i18n.t("bot.msg.no_permission_group", locale=config.lang)
        )
        return
    token = create_draft(
        message.from_user.id,
        message.chat.id,
        config.lang,
        {"lang": config.lang},
        private=private,
        baseline={"lang": config.lang},
        command_message_id=message.id,
    )
    text = i18n.t(
        "bot.hardcoded.lang.choose_user"
        if private
        else "bot.hardcoded.lang.choose_chat",
        locale=config.lang,
    )
    sent = await message.reply_text(
        text + "\n\n" + i18n.t("bot.preference_save.notice", locale=config.lang),
        reply_markup=_markup(token, config.lang),
    )
    bind_draft(token, sent.id)


@Client.on_callback_query(filters.regex(r"^lprefs:"), group=0)
async def choose_lang(client: Client, query: types.CallbackQuery):
    if not query.from_user or not query.message or not query.message.chat:
        return
    parts = str(query.data).split(":")
    if len(parts) != 3:
        await query.answer()
        return
    try:
        draft = bound_draft(
            parts[1], query.from_user.id, query.message.chat.id, query.message.id
        )
        if parts[2] not in i18n.i18n.get_available_locales():
            raise ValueError("Unknown locale")
        async with draft.lock:
            bound_draft(
                parts[1], query.from_user.id, query.message.chat.id, query.message.id
            )
            if not draft.private and not await can_manage_group_settings(
                client, draft.owner_id, draft.chat_id
            ):
                raise PermissionError("Administrator rights changed")
            draft.values["lang"] = parts[2]
            draft.lang = parts[2]
            await query.edit_message_text(
                i18n.t("bot.preference_save.language", locale=draft.lang).format(
                    lang=draft.lang
                )
                + "\n\n"
                + i18n.t("bot.preference_save.notice", locale=draft.lang),
                reply_markup=_markup(parts[1], draft.lang),
            )
        await query.answer()
    except (PermissionError, ValueError):
        await query.answer(
            i18n.t("bot.preference_save.denied", locale="vi"), show_alert=True
        )
