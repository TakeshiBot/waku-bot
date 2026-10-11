"""Pause Telegram interaction, retaining only the admin DM settings menu."""

from pyrogram import Client, enums, filters

from waku.common.telegram_authority import is_bot_admin
from waku.config import app_config

_config_command = filters.command("config")


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
        or not await is_bot_admin(actor.id)
    ):
        return False
    if callback:
        data = update.data
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        # The settings handler still checks session ownership, expiry and view.
        return isinstance(data, str) and data.startswith("pcfg:")
    if await _config_command(client, message):
        return True
    from waku.plugins.private_config import has_pending_config_input

    return has_pending_config_input(message)


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
