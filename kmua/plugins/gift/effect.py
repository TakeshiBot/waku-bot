from pyrogram import filters, types
from pyrogram.client import Client

from kmua import database, gift
from kmua.gift.service import send_gift_to_bot
from kmua.i18n import t


@Client.on_callback_query(filters.regex(r"^gift:.+:.+$"), group=1)
async def handle_send_gift_callback(
    client: Client, callback_query: types.CallbackQuery
):
    lang = (await database.get_user_config(callback_query.from_user.id)).lang
    data = callback_query.data
    if data is None:
        return
    data = str(data)
    parts = data.split(":")
    if len(parts) != 3:
        return
    user_id = callback_query.from_user.id
    gift_db_id_str = parts[1]
    try:
        gift_db_id = int(gift_db_id_str)
    except ValueError:
        return
    offset_str = parts[2]
    try:
        offset = int(offset_str)
    except ValueError:
        offset = 0
    try:
        result = await send_gift_to_bot(user_id, gift_db_id, locale=lang)
    except ValueError as e:
        messages = {
            "Gift not found": t("bot.hardcoded.gift.gift_missing", locale=lang),
            "This is not your gift": t("bot.hardcoded.gift.not_your_gift", locale=lang),
            "Gift was already sent": t("bot.hardcoded.gift.already_sent", locale=lang),
            "Unknown gift": t("bot.hardcoded.gift.unknown_gift", locale=lang),
        }
        await callback_query.answer(
            messages.get(str(e), t("bot.hardcoded.gift.send_failed", locale=lang)),
            show_alert=True,
        )
        return
    if result.detail and callback_query.message:
        await callback_query.message.reply_text(result.detail)
    await callback_query.answer(
        t("bot.hardcoded.gift.send_success", locale=lang).format(
            rarity=result.rarity_name, name=result.display_name
        ),
        show_alert=True,
    )
    user_gifts = await database.get_user_gifts(user_id, False, offset, 5)
    user_gifts_total = await database.count_user_gifts(user_id, False)
    if not user_gifts:
        if user_gifts_total == 0:
            await callback_query.edit_message_text(
                t("bot.hardcoded.gift.sent_all", locale=lang)
            )
            return
        else:
            offset = max(0, offset - 5)
            user_gifts = await database.get_user_gifts(user_id, False, offset, 5)
    text = t("bot.hardcoded.gift.choose_more", locale=lang)
    for i, g in enumerate(user_gifts, start=1 + offset):
        text += t("bot.hardcoded.gift.inventory_row", locale=lang).format(
            index=i,
            rarity=gift.get_rarity_display_name(g.rarity, lang),
            name=gift.get_display_name(gift.GiftID(g.gift_id), lang),
        )
    # Five buttons per row; pagination is on the second row.
    buttons = [
        [
            types.InlineKeyboardButton(
                str(i + 1 + offset),
                callback_data=f"gift:{g.id}:{offset}",  # sendgift:DB_GIFT_ID:OFFSET
            )
            for i, g in enumerate(user_gifts)
        ]
    ]
    if user_gifts_total >= 5:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    t("bot.hardcoded.gift.previous", locale=lang),
                    callback_data=f"sendgift_page:{offset - 5}",  # sendgift_page:OFFSET
                ),
                types.InlineKeyboardButton(
                    t("bot.hardcoded.gift.next", locale=lang),
                    callback_data=f"sendgift_page:{offset + 5}",  # sendgift_page:OFFSET
                ),
            ]
        )
    await callback_query.edit_message_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(buttons),
    )
