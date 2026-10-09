import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pyrogram
from pyrogram.client import Client

from kmua import database, enums
from kmua.common.memory_store import memttlcache
from kmua.common.utils import GROUP_CHAT_TYPES
from kmua.config import app_config
from kmua.logger import logger
from kmua.plugins.agent import quota, state
from kmua.plugins.agent.localization import localized_message, tr
from kmua.plugins.agent.user_memory import update_user_memory

from . import powermem_usage
from .agent import memory_agent, powermemory
from .myfilter import (
    base_filter,
    mention_me_filter,
    not_bottle_reply_filter,
    reply_me_filter,
)
from .whitelist import is_chat_allowed

_AGENT_MEMORY_TRIGGER_COUNT = 100
# Pyrogram stops dispatching within a group after the first matching handler.
# These paths overlap on group messages, so each memory path needs its own group.
_MEMORY_HANDLER_GROUP = 100
_AGENT_MEMORY_HANDLER_GROUP = 101
_GROUP_MEMORY_HANDLER_GROUP = 102

# Chats whose group memory update is currently running. Dispatcher workers run
# handlers concurrently, and the hourly quota key is only written once the update
# finishes, so it alone cannot stop a second worker from starting the same update.
_group_memory_inflight_chats: set[int] = set()


_GROUP_MEMORY_MAX_CHARS = 2000
_GROUP_MEMORY_PREFIX_KEY = "group_memory_heading"


@dataclass
class UserMessageGlobal:
    chat_id: int
    message_id: int
    text: str


@dataclass
class AgentMessage:
    chat_id: int
    chat_name: str
    is_group: bool
    date: datetime
    text: str


@dataclass
class GroupMessage:
    chat_id: int
    message_id: int
    text: str
    sender_name: str
    sender_id: int
    date: datetime


def _group_memory_chunks(messages: list[GroupMessage]) -> list[str]:
    """Split a group batch into provider-safe, lossless text chunks."""
    prefix = tr(_GROUP_MEMORY_PREFIX_KEY)
    budget = _GROUP_MEMORY_MAX_CHARS - len(prefix)
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for message in messages:
        line = tr(
            "memory_message",
            p0=message.sender_name,
            p1=message.sender_id,
            p2=message.text,
        )
        while line:
            separator = 1 if current else 0
            capacity = budget - current_size - separator
            if capacity <= 0:
                chunks.append(prefix + "\n".join(current))
                current = []
                current_size = 0
                continue
            piece, line = line[:capacity], line[capacity:]
            current.append(piece)
            current_size += separator + len(piece)
            if line:
                chunks.append(prefix + "\n".join(current))
                current = []
                current_size = 0
    if current:
        chunks.append(prefix + "\n".join(current))
    return chunks


async def _base_memory_filter_func(
    _, client: Client, message: pyrogram.types.Message
) -> bool:
    if not app_config.agent:
        return False
    if not message:
        return False
    if not message.from_user or not message.from_user.id:
        return False
    if not message.chat or not message.chat.id:
        return False
    user = message.from_user
    chat = message.chat
    if not chat.id or not user.id:
        return False
    if not is_chat_allowed(chat.id):
        return False
    if (
        user.is_bot
        or message.outgoing
        or message.service
        or message.automatic_forward
        or message.forward_from_chat
        or message.forward_from
        or message.forward_sender_name
        or message.via_bot
        or (
            user.id
            in (
                enums.ChatID.ANONYMOUS_ADMIN,
                enums.ChatID.SERVICE_CHAT,
                enums.ChatID.FAKE_CHANNEL,
            )
        )
    ):
        return False
    text = message.caption or message.text
    if not text or len(text) < 2 or len(text) > 2048:
        return False
    return True


base_memory_filter = pyrogram.filters.create(_base_memory_filter_func)

agent_memory_filter = (
    base_memory_filter
    & base_filter
    & (reply_me_filter | pyrogram.filters.private | mention_me_filter)
    & not_bottle_reply_filter
)

group_memory_filter = base_memory_filter & pyrogram.filters.group


def format_user_messages(messages: list[AgentMessage]) -> str:
    """Compact timestamped format grouped by chat for the memory agent."""
    if not messages:
        return ""
    grouped: dict[int, list[AgentMessage]] = {}
    for msg in messages:
        grouped.setdefault(msg.chat_id, []).append(msg)
    parts: list[str] = [tr("memory_history_heading")]
    for msgs in grouped.values():
        first = msgs[0]
        header = (
            tr("private_chat")
            if not first.is_group
            else tr("group_chat", p0=first.chat_name)
        )
        parts.append(f"[{header}]")
        for msg in msgs:
            text = msg.text.replace("\n", " ")
            parts.append(f"  {msg.date:%Y-%m-%d %H:%M} {text}")
    return "\n".join(parts)


@Client.on_message(base_memory_filter, group=_MEMORY_HANDLER_GROUP)
@localized_message(prefer_chat=True)
async def record_memory(client: Client, message: pyrogram.types.Message):
    if not app_config.agent or not app_config.agent_cross_group_memory:
        return
    user = message.from_user
    chat = message.chat
    text = message.caption or message.text
    assert (
        user is not None
        and chat is not None
        and text is not None
        and user.id is not None
        and chat.id is not None
    ), "Invalid message state in record_memory"
    if not is_chat_allowed(chat.id):
        return
    in_group = chat.type in GROUP_CHAT_TYPES
    if in_group:
        chat_config = await database.get_chat_config(chat.id)
        if not chat_config.ai_reply:
            return

    user_messages: list[UserMessageGlobal] = await memttlcache.get(
        state.user_messages_global_key(user.id), []
    )
    user_messages.append(
        UserMessageGlobal(
            chat_id=chat.id,
            message_id=message.id,
            text=text,
        )
    )
    if len(user_messages) > 100:
        user_messages = user_messages[-100:]
        # Allow at most one memory update per user per hour through this function.
        last_update_key = state.user_memory_update_key(user.id)
        last_updated = await memttlcache.get(last_update_key)
        if not last_updated:
            await memttlcache.set(last_update_key, True, ttl=3600)
            texts = "\n".join([um.text for um in user_messages])
            if memory_agent is not None:
                await update_user_memory(
                    memory_agent, texts, user.id, quota.subject_of(message)
                )
        user_messages = []
    await memttlcache.set(
        state.user_messages_global_key(user.id), user_messages, ttl=86400 * 7
    )


@Client.on_message(agent_memory_filter, group=_AGENT_MEMORY_HANDLER_GROUP)
@localized_message(prefer_chat=True)
async def record_agent_memory(client: Client, message: pyrogram.types.Message):
    if not app_config.agent:
        return
    user = message.from_user
    chat = message.chat
    text = message.caption or message.text
    assert (
        user is not None
        and chat is not None
        and text is not None
        and user.id is not None
        and chat.id is not None
    ), "Invalid message state in record_agent_memory"
    if not is_chat_allowed(chat.id):
        return
    in_group = chat.type in GROUP_CHAT_TYPES
    if in_group:
        chat_config = await database.get_chat_config(chat.id)
        if not chat_config.ai_reply:
            return
        chat_name = chat.title or f"chat {chat.id}"
    else:
        chat_name = ""

    agent_messages: list[AgentMessage] = await memttlcache.get(
        state.agent_messages_key(user.id), []
    )
    agent_messages.append(
        AgentMessage(
            chat_id=chat.id,
            chat_name=chat_name,
            is_group=in_group,
            date=message.date or datetime.now(UTC),
            text=text,
        )
    )
    if len(agent_messages) >= _AGENT_MEMORY_TRIGGER_COUNT:
        agent_messages = agent_messages[-_AGENT_MEMORY_TRIGGER_COUNT:]
        # Allow at most one memory update per user per hour through this function.
        last_update_key = state.agent_memory_update_key(user.id)
        last_updated = await memttlcache.get(last_update_key)
        if not last_updated:
            await memttlcache.set(last_update_key, True, ttl=3600)
            if memory_agent is not None:
                await update_user_memory(
                    memory_agent,
                    format_user_messages(agent_messages),
                    user.id,
                    quota.subject_of(message),
                )
            agent_messages = []
        # Hourly limit reached: retain the latest 100 messages for the next trigger instead of discarding them.
    await memttlcache.set(
        state.agent_messages_key(user.id), agent_messages, ttl=86400 * 7
    )


@Client.on_message(group_memory_filter, group=_GROUP_MEMORY_HANDLER_GROUP)
@localized_message(prefer_chat=True)
async def record_group_memory(client: Client, message: pyrogram.types.Message):
    if not app_config.agent or not app_config.agent_group_memory:
        return
    if powermemory is None:
        return
    user = message.from_user
    chat = message.chat
    text = message.caption or message.text
    assert (
        user is not None
        and chat is not None
        and text is not None
        and user.id is not None
        and chat.id is not None
    ), "Invalid message state in record_group_memory"
    if not is_chat_allowed(chat.id):
        return
    # Use cached chat configuration to avoid repeated DB lookups.
    chat_config = await database.get_chat_config(chat.id)
    if not chat_config.ai_reply or not chat_config.group_memory_enabled:
        return

    group_messages: list[GroupMessage] = await memttlcache.get(
        state.group_messages_key(chat.id), []
    )
    group_messages.append(
        GroupMessage(
            chat_id=chat.id,
            message_id=message.id,
            text=text,
            sender_name=user.full_name or f"{user.id}",
            sender_id=user.id,
            date=message.date or datetime.now(UTC),
        )
    )
    if len(group_messages) > 100:
        batch_messages = group_messages[-100:]
        # Allow at most one memory update per group per hour through this function.
        last_update_key = state.group_memory_update_key(chat.id)
        last_updated = await memttlcache.get(last_update_key)
        subject = quota.Subject(user_id=None, chat_id=chat.id, in_group=True)
        if last_updated or chat.id in _group_memory_inflight_chats:
            group_messages = []
        elif not await quota.can_start(subject):
            # Without group quota, silently defer the summary and retain the latest 100 messages until quota is available;
            # do not consume the hourly update slot.
            logger.debug(f"Skip group memory update for chat {chat.id}: no quota")
            group_messages = batch_messages
        else:
            _group_memory_inflight_chats.add(chat.id)
            chunks = _group_memory_chunks(batch_messages)
            logger.debug(
                f"Updating group memory for chat {chat.id} with "
                f"{len(batch_messages)} messages in {len(chunks)} chunks"
            )
            # powermem calls the model internally; collect usage through its callback and record the full block on
            # the group account so this cost remains visible.
            with powermem_usage.collect() as memory_calls:
                try:
                    for index, batch_text in enumerate(chunks, start=1):
                        coro = powermemory.add(
                            batch_text, infer=True, user_id=f"group_{chat.id}"
                        )
                        if app_config.agent_model_timeout > 0:
                            result = await asyncio.wait_for(
                                coro, timeout=app_config.agent_model_timeout
                            )
                        else:
                            result = await coro
                        logger.debug(
                            f"Updated group memory chunk {index}/{len(chunks)} for chat "
                            f"{chat.id}, powermem result: {result}"
                        )
                except TimeoutError:
                    logger.warning(f"group memory update timed out for chat {chat.id}")
                    group_messages = batch_messages
                except Exception as e:
                    logger.exception(
                        f"group memory update failed for chat {chat.id}: "
                        f"{e.__class__.__name__}: {e}"
                    )
                    # Keep the attempted batch for a later retry; do not mark the
                    # hourly quota until every chunk has been stored.
                    group_messages = batch_messages
                else:
                    await memttlcache.set(last_update_key, True, ttl=3600)
                    group_messages = []
                finally:
                    _group_memory_inflight_chats.discard(chat.id)
            memory_usage = powermem_usage.usage_of(memory_calls)
            if memory_usage is not None:
                await quota.settle(subject, memory_usage)
    await memttlcache.set(
        state.group_messages_key(chat.id), group_messages, ttl=86400 * 7
    )
