import random
import re

import pyrogram
import zhconv
from pyrogram import filters
from pyrogram.client import Client

from waku import database, i18n, resources
from waku.common.memory_store import memttlcache
from waku.common.rich_message import message_plain_text
from waku.common.utils import GROUP_CHAT_TYPES, is_explicit_reply
from waku.config import app_config
from waku.services import manyacg

_BOTTLE_MSG_PREFIX = "bottle_msg:"
_REPLY_INTENT_PREFIX = "bottle_reply_intent:"


async def word_reply(client: Client, message: pyrogram.types.Message):
    user = message.from_user
    if not user:
        return
    chat = message.chat
    if chat and chat.type in GROUP_CHAT_TYPES:
        config = await database.get_chat_config(chat)
        if not config.bot_reply:
            return
    else:
        config = await database.get_user_config(user)
    text = message_plain_text(message)
    if not text or not client.me:
        return
    if client.me.username:
        text = re.sub(re.escape(client.me.username), "", text, flags=re.IGNORECASE)
    text = zhconv.convert(text.strip().casefold(), "zh-cn")
    await message.reply_chat_action(pyrogram.enums.ChatAction.TYPING)
    all_replies = []
    word_dict = resources.get_word_dict(config.lang)
    for keyword, replies in word_dict.items():
        if keyword in text:
            all_replies.extend(replies)
    if all_replies:
        await message.reply_text(
            text=random.choice(all_replies),
        )
    else:
        await message.reply_text(
            text=i18n.trl("bot.msg.reply.default", locale=config.lang)
        )


async def _base_filter_func(_, __, message: pyrogram.types.Message) -> bool:
    if not message:
        return False
    text = message_plain_text(message)
    if (
        message.entities
        and message.entities[0].type == pyrogram.enums.MessageEntityType.BOT_COMMAND
        and message.entities[0].offset == 0
    ):
        return False
    if text.startswith("/") or text.startswith("\\"):
        return False
    return True


async def _reply_me_filter_func(
    _, client: Client, message: pyrogram.types.Message
) -> bool:
    if not is_explicit_reply(message):
        return False
    if not message.reply_to_message:
        return False
    if not message.reply_to_message.from_user:
        return False
    if not client.me:
        return False
    if message.reply_to_message.from_user.username != client.me.username:
        return False
    return True


async def _mention_me_filter_func(
    _, client: Client, message: pyrogram.types.Message
) -> bool:
    text = message_plain_text(message)
    if not text:
        return False
    folded_text = text.casefold()
    if app_config.nickname and app_config.nickname.casefold() in folded_text:
        return True
    if not client.me:
        return False
    username = client.me.username
    if not username:
        return False
    if username.casefold() in folded_text:
        return True
    if message.caption and username.casefold() in message.caption.casefold():
        return True
    return False


async def _not_bottle_reply_filter_func(
    _, client: Client, message: pyrogram.types.Message
) -> bool:
    if not is_explicit_reply(message):
        return True
    if not message.reply_to_message:
        return True
    reply_to = message.reply_to_message
    if not reply_to.from_user or not client.me:
        return True
    if reply_to.from_user.id != client.me.id:
        return True
    if not reply_to.chat:
        return True
    reply_to_key = f"{_BOTTLE_MSG_PREFIX}{reply_to.chat.id}:{reply_to.id}"
    bottle_data = await memttlcache.get(reply_to_key)
    if not bottle_data:
        return True
    bottle_id = bottle_data.get("bottle_id")
    if not bottle_id:
        return True
    user_id = message.from_user.id if message.from_user else None
    if not user_id:
        return True
    intent_data = await memttlcache.get(f"{_REPLY_INTENT_PREFIX}{user_id}")
    if intent_data and intent_data.get("bottle_id") == bottle_id:
        return False
    return True


_base_filter = filters.create(_base_filter_func)
_reply_me_filter = filters.create(_reply_me_filter_func)
_mention_me_filter = filters.create(_mention_me_filter_func)
_not_bottle_reply_filter = filters.create(_not_bottle_reply_filter_func)


async def _agent_disabled_filter_func(_, __, ___) -> bool:
    return not app_config.agent


_agent_disabled_filter = filters.create(_agent_disabled_filter_func)

_filter = (
    _base_filter
    & (_reply_me_filter | filters.private | _mention_me_filter)
    & _not_bottle_reply_filter
    & ~pyrogram.filters.regex("|".join([r.pattern for r in manyacg.ARTWORK_ALL_REGEX]))
    # WeChat article links are handled by the wechat parser (group -1); the
    # keyword reply must not double-respond to them.
    & ~pyrogram.filters.regex(r"https?://mp\.weixin\.qq\.com/s/[A-Za-z0-9_-]+")
    # Twitter/X links are handled by the native tweet parser (group -1); the
    # keyword reply must not double-respond to them.
    & ~pyrogram.filters.regex(r"(?:twitter|x)\.com/[^/]+/status/\d+")
)

_chat_command_filter = filters.command("chat") & _not_bottle_reply_filter


@Client.on_message((_filter | _chat_command_filter) & _agent_disabled_filter, group=0)
async def wake_simple_reply(client: Client, message: pyrogram.types.Message):
    return await word_reply(client, message)


__all__ = ["word_reply", "wake_simple_reply"]
