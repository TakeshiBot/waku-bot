"""Consume authenticated Mirror receipts before ordinary AI bot-chat handling."""

import json
import re

from pyrogram import Client, filters

from waku.common.rich_message import message_plain_text
from waku.config import app_config
from waku.logger import logger
from waku.services import mirror_bridge, mirror_native, mirror_notifications

_PREFIX = "WAKU_MIRROR_EVENT "


@Client.on_message(filters.bot & filters.group, group=-1100)
@Client.on_edited_message(filters.bot & filters.group, group=-1100)
async def mirror_receipt(client, message):
    actor = message.from_user
    if (
        not actor
        or not actor.is_bot
        or not message.chat
        or message.sender_chat
        or getattr(message, "business_connection_id", None)
        or getattr(message, "guest_query_id", None)
        or mirror_native.is_forwarded(message)
    ):
        return
    text = message_plain_text(message)
    if len(text) > 64 * 1024:
        return
    line = next((line for line in text.splitlines() if line.startswith(_PREFIX)), None)
    if line is None:
        try:
            consumed = await mirror_native.receive_native(message)
        except Exception as error:
            logger.warning("Native Mirror receipt failed: {}", type(error).__name__)
            return
        if consumed:
            mirror_notifications.wake()
            message.stop_propagation()
        if (
            message.chat.id == app_config.mirror_group_id
            and (getattr(actor, "username", None) or "").lower()
            == app_config.mirror_bot_username.lstrip("@").lower()
            and re.match(r"^\W*No Active Bot Tasks!", text)
        ):
            # This stock status response is data, not a request to chat with AI.
            message.stop_propagation()
        return
    try:
        event = json.loads(line[len(_PREFIX) :])
        if not isinstance(event, dict):
            return
        # The wire ID must agree with an actual reply, when Telegram supplies it.
        reply_id = message.reply_to_message_id
        if reply_id and reply_id != event.get("command_message_id"):
            return
        task = await mirror_bridge.receive_event(
            event, sender_id=actor.id, chat_id=message.chat.id
        )
        if task is not None:
            mirror_notifications.wake()
    except (TypeError, ValueError):
        return
    except Exception as error:
        logger.warning("Mirror receipt persistence failed: {}", type(error).__name__)
        return
    finally:
        # Protocol messages are backend data, never conversational input.
        # Stale, invalid or temporarily unrecordable receipts cannot start loops.
        message.stop_propagation()
