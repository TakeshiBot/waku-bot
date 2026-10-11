import mimetypes
from io import BytesIO
from typing import BinaryIO

import pyrogram
from pyrogram.client import Client

from waku import common
from waku.config import app_config


@Client.on_message(pyrogram.filters.sticker & pyrogram.filters.private, group=1)
async def handle_sticker(client: Client, message: pyrogram.types.Message):
    sticker = message.sticker
    if not app_config.dm_sticker_download_enabled or not sticker:
        return
    ext = (
        ".tgs"
        if sticker.is_animated
        else ".webm"
        if sticker.is_video
        else mimetypes.guess_extension(sticker.mime_type or "image/webp") or ".webp"
    )
    file_name = f"{sticker.set_name or 'sticker'}_{sticker.file_unique_id}{ext}"
    if file_id := await common.memttlcache.get(
        f"sticker_file:{sticker.file_unique_id}"
    ):
        if not app_config.dm_sticker_download_enabled:
            return
        await message.reply_document(
            document=file_id,
            force_document=True,
            file_name=file_name,
        )
        return
    await message.reply_chat_action(pyrogram.enums.ChatAction.UPLOAD_DOCUMENT)
    file = await client.download_media(sticker, in_memory=True)
    if (
        not app_config.dm_sticker_download_enabled
        or not file
        or not isinstance(file, (str, BinaryIO, BytesIO))
    ):
        return
    msg = await message.reply_document(
        document=file,
        force_document=True,
        file_name=file_name,
    )
    if msg and msg.document and msg.document.file_id:
        await common.memttlcache.set(
            f"sticker_file:{sticker.file_unique_id}",
            msg.document.file_id,
            ttl=app_config.cachettl_sticker_fileid,  # type: ignore[union-attr]
        )
