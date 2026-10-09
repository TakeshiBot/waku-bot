import io
import random

import httpx
from pyrogram import filters
from pyrogram.client import Client
from pyrogram.types import InputChatPhotoStatic, Message

from kmua import database, i18n
from kmua.config import app_config
from kmua.logger import logger


@Client.on_message(filters.command("randmyavatar"), group=0)
async def randmyavatar_command(client: Client, message: Message):
    """
    Admin command: change the bot avatar immediately.
    """
    user = message.from_user
    if user is None:
        return

    # Check permissions.
    db_user = await database.get_user_by_id(user.id)
    if db_user and not db_user.is_bot_global_admin and user.id not in app_config.owners:
        return
    elif not db_user and user.id not in app_config.owners:
        return

    # Resolve the user's language.
    user_config = await database.get_user_config(user.id)
    lang = user_config.lang

    # Check service availability.
    from kmua.services import aniobjcut, manyacg

    if not manyacg.manyacg_client or not aniobjcut.aniobjcut_client:
        await message.reply_text(
            i18n.t("bot.msg.randmyavatar.service_unavailable", locale=lang)
        )
        return

    # Send the progress message.
    status_msg = await message.reply_text(
        i18n.t("bot.msg.randmyavatar.processing", locale=lang)
    )

    try:
        # Fetch a random image.
        resp = await manyacg.manyacg_client.random_artwork(limit=1, r18=0)
        if resp.status != 200 or not resp.data:
            await status_msg.edit_text(
                i18n.t("bot.msg.randmyavatar.fetch_failed", locale=lang)
            )
            return

        artwork = resp.data[0]
        picture = artwork.pictures[random.randint(0, len(artwork.pictures) - 1)]

        # Download the image.
        async with httpx.AsyncClient(timeout=30) as http_client:
            fileresp = await http_client.get(
                f"{app_config.manyacg_api_url}/picture/file/{picture.id}",
            )
            fileresp.raise_for_status()

        # Crop the avatar.
        avatar = await aniobjcut.aniobjcut_client.cut_avatar(fileresp.content)

        # Update the avatar.
        await client.set_profile_photo(InputChatPhotoStatic(io.BytesIO(avatar)))

        # Success message.
        await status_msg.edit_text(
            i18n.t("bot.msg.randmyavatar.success", locale=lang).format(
                title=artwork.title, url=artwork.source_url
            )
        )

        logger.info(
            f"bot avatar changed by user {user.id}, artwork: {artwork.title} ({artwork.source_url})"
        )

    except Exception as e:
        await status_msg.edit_text(
            i18n.t("bot.msg.randmyavatar.failed", locale=lang).format(error=str(e))
        )
        logger.exception(f"failed to change bot avatar by command: {e}")
