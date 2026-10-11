"""Pause native Telegram interaction, retaining all bot-admin DM commands."""

from pyrogram import Client, enums

from waku.common.telegram_runtime import prepare_telegram_output
from waku.config import app_config


async def _recovery_allowed(client, update, *, callback=False):
    message = update.message if callback else update
    actor = update.from_user
    if (
        message is None
        or message.chat is None
        or message.chat.type != enums.ChatType.PRIVATE
        or actor is None
        or actor.is_bot
        or getattr(message, "business_connection_id", None)
        or getattr(message, "guest_query_id", None)
    ):
        return False
    return await prepare_telegram_output(message, actor)


@Client.on_message(group=-1000)
@Client.on_edited_message(group=-1000)
async def paused_message(client, message):
    if not app_config.telegram_enabled and not await _recovery_allowed(client, message):
        message.stop_propagation()


@Client.on_callback_query(group=-1000)
async def paused_callback(client, query):
    if not app_config.telegram_enabled and not await _recovery_allowed(
        client, query, callback=True
    ):
        query.stop_propagation()


@Client.on_inline_query(group=-1000)
@Client.on_chosen_inline_result(group=-1000)
@Client.on_chat_member_updated(group=-1000)
async def paused_interaction(client, update):
    if not app_config.telegram_enabled:
        update.stop_propagation()
