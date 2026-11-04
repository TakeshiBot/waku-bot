"""Telegram /vseg: one catalog video, with optional exact tags and rating."""

import pyrogram
from pyrogram.client import Client

from waku import common, database, i18n
from waku.common.utils import GROUP_CHAT_TYPES
from waku.config import app_config
from waku.logger import logger
from waku.services.catalog_videos import (
    VideoError,
    catalog_notice,
    fetch_catalog_video,
    prepare_video,
    send_telegram_video,
)
from waku.services.telegram_images import telegram_image_settings


@Client.on_message(pyrogram.filters.command("vseg"), group=0)
async def video_command(client, message):
    if not message.chat or not (actor := message.from_user or message.sender_chat):
        return
    config = await (
        database.get_chat_config(message.chat.id)
        if message.chat.type in GROUP_CHAT_TYPES
        else database.get_user_config(actor.id)
    )
    enabled, mode = await telegram_image_settings(message.chat.id)
    if not enabled:
        await message.reply(
            i18n.t("bot.msg.manyacg.chat_setu_disabled", locale=config.lang)
        )
        return
    keyword = " ".join((message.command or [])[1:])
    key = f"setu_cd:{actor.id}"
    if await common.memttlcache.get(key, False):
        await message.reply(i18n.t("bot.msg.manyacg.setu_cd", locale=config.lang))
        return
    await common.memttlcache.set(key, True, ttl=app_config.manyacg_setu_cd)
    file = None
    try:
        row = await fetch_catalog_video(keyword, mode)
        file = await prepare_video(row)
        await send_telegram_video(message, row, file)
    except VideoError as error:
        await message.reply(
            catalog_notice(keyword=keyword, lang=config.lang, code=error.code),
            parse_mode=pyrogram.enums.ParseMode.DISABLED,
        )
    except Exception as error:
        logger.warning("Catalog video failed: {}", type(error).__name__)
        await message.reply(catalog_notice(lang=config.lang, code="unavailable"))
    finally:
        if file:
            file.close()
