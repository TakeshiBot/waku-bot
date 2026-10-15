"""Restart the whole bot process after an authenticated operator request."""

import asyncio
from pathlib import Path

import pyrogram
from pyrogram.client import Client

from waku import common, database
from waku.config import ProviderConfig, _AppConfig, _resolve_settings_files, app_config
from waku.i18n import t
from waku.logger import logger
from waku.services.process_restart import request_restart
from waku.services.settings_editor import SettingsEditError, SettingsEditor


async def _restart() -> None:
    # Let the command handler and its acknowledgement finish before shutdown.
    await asyncio.sleep(2)
    request_restart()


@Client.on_message(pyrogram.filters.command("restart"), group=0)
async def restart_command(client: Client, message: pyrogram.types.Message):
    user = message.from_user
    if (
        user is None
        or user.is_bot
        or getattr(message, "sender_chat", None)
        or getattr(message, "business_connection_id", None)
        or getattr(message, "guest_query_id", None)
    ):
        return
    db_user = await database.get_user_by_id(user.id)
    lang = db_user.user_config.lang if db_user else app_config.lang
    if user.id not in app_config.owners and not (
        db_user and db_user.is_bot_global_admin and not db_user.is_blocked
    ):
        await message.reply_text(t("bot.restart.denied", locale=lang))
        return

    try:
        editor = SettingsEditor(
            [Path(path) for path in _resolve_settings_files()],
            _AppConfig,
            ProviderConfig,
            lambda: app_config.model_dump(),
        )
        await asyncio.to_thread(editor.validate_for_restart)
    except (SettingsEditError, OSError):
        await message.reply_text(t("bot.restart.invalid_config", locale=lang))
        return
    await message.reply_text(t("bot.restart.restarting", locale=lang))
    logger.info("Bot process restart requested by {}", user.id)
    common.spawn(_restart(), name="telegram-command-restart")
