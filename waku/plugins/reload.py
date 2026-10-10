import pyrogram
from pyrogram.client import Client

from waku import database
from waku.config import app_config, reload_config
from waku.i18n import t
from waku.logger import logger


@Client.on_message(pyrogram.filters.command("reload"), group=0)
async def reload_command(client: Client, message: pyrogram.types.Message):
    user = message.from_user
    if user is None:
        return
    db_user = await database.get_user_by_id(user.id)
    if not db_user:
        return
    lang = db_user.user_config.lang
    if not db_user.is_bot_global_admin and user.id not in app_config.owners:
        await message.reply_text(t("bot.hardcoded.reload.denied", locale=lang))
        return

    success, msg, changed = await reload_config(locale=lang)
    if success and changed:
        logger.info(
            f"Config reloaded by {user.id}, changed fields: {', '.join(changed)}"
        )
        msg += "\n\n" + t("bot.hardcoded.reload.changed", locale=lang).format(
            count=len(changed), fields=", ".join(changed)
        )
    await message.reply_text(msg)
