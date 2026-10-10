"""Grant/revoke bot-wide administrator access by numeric Telegram ID in DM."""

import asyncio

import pyrogram
from pyrogram.client import Client

from waku import database, i18n
from waku.common.ops import RESERVED_USER_IDS
from waku.config import app_config

_ROLE_LOCK = asyncio.Lock()


@Client.on_message(
    pyrogram.filters.command(["botpromote", "botdemote"]) & pyrogram.filters.private,
    group=0,
)
async def set_user_bot_admin(client: Client, message: pyrogram.types.Message):
    actor, chat = message.from_user, message.chat
    if (
        not actor
        or actor.is_bot
        or not chat
        or chat.type != pyrogram.enums.ChatType.PRIVATE
        or chat.id != actor.id
        or getattr(message, "business_connection_id", None)
    ):
        return
    async with _ROLE_LOCK:
        # Recheck within the lock: a queued command cannot retain a revoked role.
        current_actor = await database.get_user_by_id(actor.id)
        if actor.id not in app_config.owners and not (
            current_actor
            and current_actor.is_bot_global_admin
            and not getattr(current_actor, "is_blocked", False)
        ):
            return
        lang = (await database.get_user_config(actor.id)).lang

        async def reply(key, **values):
            await message.reply_text(
                i18n.t("bot.botadmin_private." + key, locale=lang).format(**values)
            )

        args = message.command or []
        if (
            len(args) != 2
            or not args[1].isascii()
            or not args[1].isdigit()
            or len(args[1]) > 19
        ):
            await reply("usage")
            return
        target_id = int(args[1])
        if not 0 < target_id < 2**63 or target_id in RESERVED_USER_IDS:
            await reply("invalid")
            return
        if target_id == actor.id or target_id in app_config.owners:
            await reply("protected")
            return
        target = await database.get_user_by_id(target_id)
        if target is None:
            await reply("unregistered")
            return
        if target.is_bot:
            await reply("bot")
            return
        promote = args[0].split("@", 1)[0].lower() == "botpromote"
        if target.is_bot_global_admin == promote:
            await reply("already")
            return
        await database.set_user_global_admin(target_id, promote)
        await reply("promoted" if promote else "demoted", id=target_id)
