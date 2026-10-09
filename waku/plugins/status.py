import pyrogram
from pyrogram.client import Client

from waku import database
from waku.common import ops
from waku.config import app_config
from waku.i18n import t


@Client.on_message(pyrogram.filters.command("status"), group=0)
async def status_command(client: Client, message: pyrogram.types.Message):
    user = message.from_user
    if user is None:
        return
    db_user = await database.get_user_by_id(user.id)
    if not db_user:
        return
    if not db_user.is_bot_global_admin and user.id not in app_config.owners:
        return
    stats = await ops.collect_stats()
    await message.reply_text(
        t("bot.hardcoded.status.summary", locale=db_user.user_config.lang).format(
            **stats
        ),
    )
