import random

from pyrogram import enums, filters, types
from pyrogram.client import Client

from kmua import database, gift
from kmua.i18n import t


@Client.on_message(filters.command("buygift") & filters.private, group=1)
async def buy_gift(client: Client, message: types.Message):
    user = message.from_user
    if user is None:
        return
    user_data = await database.get_user_by_id(user.id)
    if not user_data:
        return
    lang = user_data.user_config.lang
    user_coin = user_data.user_config.coins
    affordable_gifts = gift.list_affordable_gifts(user_coin)
    if not affordable_gifts:
        await message.reply_text(t("bot.hardcoded.gift.cannot_buy", locale=lang))
        return
    gift_list_text = t("bot.hardcoded.gift.choose_buy", locale=lang)
    await message.reply_text(
        gift_list_text,
        reply_markup=types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton(
                        gift.get_display_name(g.id, lang),
                        callback_data=f"buygift:{user.id}:{g.id}:req",
                    )
                    for g in affordable_gifts  # [TODO] Add pagination when more gift types are introduced.
                ],
                [
                    types.InlineKeyboardButton(
                        t("bot.hardcoded.gift.leave", locale=lang),
                        callback_data="delete_callback_query_message",
                    )
                ],
            ]
        ),
    )


@Client.on_callback_query(filters.regex(r"^buygift:(\d+):(.+):(.+)$"), group=0)
async def handle_buy_gift_callback(client: Client, callback_query: types.CallbackQuery):
    lang = (await database.get_user_config(callback_query.from_user.id)).lang
    data = callback_query.data
    if data is None:
        return
    data = str(data)
    parts = data.split(":")
    if len(parts) != 4:
        return
    user_id_str, gift_id_str, status = parts[1], parts[2], parts[3]
    try:
        user_id = int(user_id_str)
    except ValueError:
        return
    if callback_query.from_user.id != user_id:
        await callback_query.answer(
            t("bot.hardcoded.gift.not_your_purchase", locale=lang), show_alert=True
        )
        return
    gift_id = gift.GiftID(gift_id_str)
    gift_item = gift.get_gift_by_id(gift_id)
    user_data = await database.get_user_by_id(user_id)
    if not user_data:
        await callback_query.answer(
            t("bot.hardcoded.gift.user_missing", locale=lang), show_alert=True
        )
        return
    user_coins = user_data.user_config.coins
    match status:
        case "yes":
            if user_data.user_config.coins < gift_item.price:
                await callback_query.answer(
                    t("bot.hardcoded.gift.insufficient", locale=lang), show_alert=True
                )
                return
            rarity = random.randint(1, 5)
            await database.buy_gift_for_user(user_id, gift_id, rarity=rarity)
            await callback_query.answer(
                t("bot.hardcoded.gift.bought", locale=lang).format(
                    rarity=gift.get_rarity_display_name(rarity, lang),
                    name=gift.get_display_name(gift_item.id, lang),
                ),
                show_alert=True,
            )
            user_coins_after = (await database.get_user_config(user_id)).coins
            percent_now = gift_item.price * 100 // user_coins_after
            if percent_now > 100 or user_coins <= 0:
                percent_now = 100
            await callback_query.edit_message_text(
                t("bot.hardcoded.gift.repeat", locale=lang).format(
                    name=gift.get_display_name(gift_item.id, lang), percent=percent_now
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    [
                        [
                            types.InlineKeyboardButton(
                                t("bot.hardcoded.gift.buy_again", locale=lang),
                                callback_data=f"buygift:{user_id}:{gift_id_str}:yes",
                            ),
                            types.InlineKeyboardButton(
                                t("bot.hardcoded.gift.leave", locale=lang),
                                callback_data="delete_callback_query_message",
                            ),
                        ]
                    ]
                ),
            )
        case "no":
            affordable_gifts = gift.list_affordable_gifts(user_coins)
            if not affordable_gifts:
                await callback_query.edit_message_text(
                    t("bot.hardcoded.gift.cancelled_empty", locale=lang),
                    reply_markup=None,  # type: ignore
                )
                return
            gift_list_text = t("bot.hardcoded.gift.cancelled_choose", locale=lang)
            await callback_query.edit_message_text(
                gift_list_text,
                reply_markup=types.InlineKeyboardMarkup(
                    [
                        [
                            types.InlineKeyboardButton(
                                gift.get_display_name(g.id, lang),
                                callback_data=f"buygift:{user_data.id}:{g.id}:req",
                            )
                            for g in affordable_gifts
                        ],
                        [
                            types.InlineKeyboardButton(
                                t("bot.hardcoded.gift.leave", locale=lang),
                                callback_data="delete_callback_query_message",
                            )
                        ],
                    ]
                ),
            )
        case "req":
            price_percent = (
                int((gift_item.price / user_coins) * 100) if user_coins > 0 else 0
            )
            if price_percent > 100:
                price_percent = 100
            display_name = gift.get_display_name(gift_item.id, lang)
            text = t("bot.hardcoded.gift.confirm_purchase", locale=lang).format(
                name=display_name,
                description=gift_item.get_description(lang),
                comment=gift_item.get_comment(lang),
                percent=price_percent,
            )
            message = callback_query.message
            if message is None:
                return
            await message.edit_text(
                text=text,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=types.InlineKeyboardMarkup(
                    [
                        [
                            types.InlineKeyboardButton(
                                t("bot.hardcoded.gift.confirm", locale=lang),
                                callback_data=f"buygift:{user_id}:{gift_id_str}:yes",
                            ),
                            types.InlineKeyboardButton(
                                t("bot.hardcoded.gift.cancel", locale=lang),
                                callback_data=f"buygift:{user_id}:{gift_id_str}:no",
                            ),
                        ]
                    ]
                ),
            )
        case _:
            await callback_query.answer(
                t("bot.hardcoded.gift.unknown_action", locale=lang), show_alert=True
            )
            return


@Client.on_message(filters.command("gift") & filters.private, group=1)
async def send_gift(client: Client, message: types.Message):
    user = message.from_user
    if user is None:
        return
    user_data = await database.get_user_by_id(user.id)
    if not user_data:
        return
    lang = user_data.user_config.lang
    user_gifts = await database.get_user_gifts(user.id, False, 0, 5)
    if not user_gifts:
        await message.reply_text(t("bot.hardcoded.gift.inventory_empty", locale=lang))
        return
    user_gifts_total = await database.count_user_gifts(user.id, False)
    text = t("bot.hardcoded.gift.choose_send", locale=lang)
    for i, g in enumerate(user_gifts, start=1):
        text += t("bot.hardcoded.gift.inventory_row", locale=lang).format(
            index=i,
            rarity=gift.get_rarity_display_name(g.rarity, lang),
            name=gift.get_display_name(gift.GiftID(g.gift_id), lang),
        )
    # Five buttons per row; pagination is on the second row.
    buttons = [
        [
            types.InlineKeyboardButton(
                str(i + 1),
                callback_data=f"gift:{g.id}:0",  # sendgift:DB_GIFT_ID:OFFSET
            )
            for i, g in enumerate(user_gifts)
        ]
    ]
    if user_gifts_total >= 5:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    t("bot.hardcoded.gift.previous", locale=lang),
                    callback_data="sendgift_page:-5",  # sendgift_page:OFFSET
                ),
                types.InlineKeyboardButton(
                    t("bot.hardcoded.gift.next", locale=lang),
                    callback_data="sendgift_page:5",  # sendgift_page:OFFSET
                ),
            ]
        )
    await message.reply_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(buttons),
    )


@Client.on_callback_query(filters.regex(r"^sendgift_page:.+$"), group=0)
async def handle_send_gift_page_callback(
    client: Client, callback_query: types.CallbackQuery
):
    lang = (await database.get_user_config(callback_query.from_user.id)).lang
    data = callback_query.data
    if data is None:
        return
    data = str(data)
    parts = data.split(":")
    if len(parts) != 2:
        return
    offset_str = parts[1]
    try:
        offset = int(offset_str)
    except ValueError:
        return
    if offset < 0:
        await callback_query.answer(
            t("bot.hardcoded.gift.no_more", locale=lang), show_alert=True
        )
    user_id = callback_query.from_user.id
    user_data = await database.get_user_by_id(user_id)
    if not user_data:
        await callback_query.answer(
            t("bot.hardcoded.gift.user_missing", locale=lang), show_alert=True
        )
        return
    user_gifts = await database.get_user_gifts(user_id, False, offset, 5)
    if not user_gifts:
        await callback_query.answer(
            t("bot.hardcoded.gift.no_more_gifts", locale=lang), show_alert=True
        )
        return
    user_gifts_total = await database.count_user_gifts(user_id, False)
    text = t("bot.hardcoded.gift.choose_send", locale=lang)
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
