import pyrogram
from pyrogram.client import Client

from kmua import common
from kmua.common.locale import message_locale
from kmua.common.utils import is_explicit_reply
from kmua.i18n import t


@Client.on_message(pyrogram.filters.command("id"), group=0)
async def getchatid(client: Client, message: pyrogram.types.Message):
    chat = message.chat
    user = message.sender_chat or message.from_user
    if not chat or not user:
        return
    lang = await message_locale(message)
    if is_explicit_reply(message) and message.reply_to_message:
        target_msg = message.reply_to_message
        target = common.get_message_origin(target_msg)
        await message.reply_text(
            t("bot.hardcoded.id.reply", locale=lang).format(
                chat_id=chat.id,
                user_id=user.id,
                target_id=target.id
                if target
                else t("bot.hardcoded.unknown", locale=lang),
            )
        )
    else:
        await message.reply_text(
            t("bot.hardcoded.id.chat_user", locale=lang).format(
                chat_id=chat.id, user_id=user.id
            )
        )
