import time

from pyrogram import filters
from pyrogram.client import Client
from pyrogram.types import Message

from waku.common.locale import message_locale
from waku.i18n import t


@Client.on_message(filters.command("ping"), group=0)
async def ping(client: Client, message: Message):
    lang = await message_locale(message)
    t0 = time.monotonic()
    sent = await message.reply(t("bot.hardcoded.ping.pong", locale=lang))
    ms = (time.monotonic() - t0) * 1000
    await sent.edit(t("bot.hardcoded.ping.latency", locale=lang).format(ms=ms))
