from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime

import discord
from pydantic_ai.messages import (
    MULTI_MODAL_CONTENT_TYPES,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from waku import common
from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent.history import compact_history, truncate_multimodal

from .constants import (
    _CUSTOM_EMOJI_RE,
    _DISCORD_GROUP_MEMORY_BATCH_SIZE,
    _DISCORD_GROUP_MEMORY_TTL,
    _EMOJI_RE,
    DISCORD_COMMAND_PREFIX,
)
from .models import DiscordGroupMemoryMessage
from .permissions import (
    _can_read_message_history,
    _channel_allowed,
    _is_discord_user_bot_admin,
)
from .settings import (
    _discord_dm_settings,
    _discord_global_ai_enabled,
    _discord_guild_settings,
)
from .utilities import _author_name, _clean_content, _message_text


def _discord_group_memory_user_id(guild_id: int, channel_id: int) -> str:
    return f"discord_group_{guild_id}_channel_{channel_id}"


def _discord_group_messages_key(guild_id: int, channel_id: int) -> str:
    return f"discord_group_messages:{guild_id}:{channel_id}"


def _discord_group_memory_update_key(guild_id: int, channel_id: int) -> str:
    return f"discord_group_memory_last_update:{guild_id}:{channel_id}"


def _get_powermemory():
    try:
        from waku.plugins.agent.agent import powermemory
    except Exception as e:
        logger.debug(f"Discord powermemory unavailable: {e.__class__.__name__}: {e}")
        return None
    return powermemory


def _emoji_key(message: discord.Message) -> str:
    guild_part = message.guild.id if message.guild else "dm"
    return f"discord_emoji_style:{guild_part}:{message.author.id}"


def _reaction_key(message: discord.Message) -> str:
    guild_part = message.guild.id if message.guild else "dm"
    return f"discord_reaction_style:{guild_part}:{message.author.id}"


def _reaction_counter_key(message: discord.Message) -> str:
    channel_id = getattr(message.channel, "id", "dm")
    return f"discord_periodic_reaction_counter:{channel_id}:{message.author.id}"


def _discord_reaction_candidates(message: discord.Message) -> list[str]:
    content = _message_text(message)
    candidates = _CUSTOM_EMOJI_RE.findall(content) + _EMOJI_RE.findall(content)
    for reaction in getattr(message, "reactions", []):
        emoji = getattr(reaction, "emoji", None)
        if isinstance(emoji, str):
            candidates.append(emoji)
        elif isinstance(emoji, discord.PartialEmoji | discord.Emoji):
            candidates.append(str(emoji))
    return candidates


async def _can_learn_discord_style(message: discord.Message) -> bool:
    if message.author.bot or not await _discord_global_ai_enabled():
        return False
    if message.guild is None:
        if not isinstance(
            message.channel, discord.DMChannel
        ) or not _is_discord_user_bot_admin(message.author):
            return False
        settings = await _discord_dm_settings(message.author)
    else:
        if not await _channel_allowed(message):
            return False
        settings = await _discord_guild_settings(message.guild)
    return bool(settings.ai_reply) and _can_read_message_history(
        message.channel, message.guild, requester=message.author
    )


async def _remember_discord_emojis(message: discord.Message) -> None:
    if not await _can_learn_discord_style(message):
        return
    content = _message_text(message)
    emojis = _CUSTOM_EMOJI_RE.findall(content) + _EMOJI_RE.findall(content)
    if not emojis:
        return
    key = _emoji_key(message)
    existing: list[str] = await common.memttlcache.get(key, [])
    merged = (existing + emojis)[-40:]
    await common.memttlcache.set(key, merged, ttl=7 * 24 * 60 * 60)


async def _remember_discord_reaction_style(
    message: discord.Message, *, sent_emoji: str | None = None
) -> None:
    if not await _can_learn_discord_style(message):
        return
    # add_reaction does not update Message.reactions locally. Explicit sends
    # record just that acknowledged emoji under the actual target's author.
    reactions = [sent_emoji] if sent_emoji else _discord_reaction_candidates(message)
    if not reactions:
        return
    key = _reaction_key(message)
    existing: list[str] = await common.memttlcache.get(key, [])
    merged = (existing + reactions)[-60:]
    await common.memttlcache.set(key, merged, ttl=7 * 24 * 60 * 60)


async def _discord_emoji_hint(message: discord.Message) -> str | None:
    emojis: list[str] = await common.memttlcache.get(_emoji_key(message), [])
    if not emojis:
        return None
    common_emojis = [emoji for emoji, _ in Counter(emojis).most_common(8)]
    if not common_emojis:
        return None
    return "User/server emoji style: " + " ".join(common_emojis)


async def _discord_reaction_hint(message: discord.Message) -> str | None:
    reactions: list[str] = await common.memttlcache.get(_reaction_key(message), [])
    if not reactions:
        return None
    common_reactions = [emoji for emoji, _ in Counter(reactions).most_common(10)]
    if not common_reactions:
        return None
    return "Discord reaction style: " + " ".join(common_reactions)


async def _record_discord_group_memory(message: discord.Message) -> None:
    guild = message.guild
    if guild is None or message.author.bot:
        return
    if not await _discord_global_ai_enabled():
        return
    content = _clean_content(message)
    if not content or len(content) < 2 or len(content) > 2048:
        return
    if content.startswith(DISCORD_COMMAND_PREFIX):
        return
    settings = await _discord_guild_settings(guild)
    if not settings.ai_reply or not settings.group_memory_enabled:
        return
    if not await _channel_allowed(message) or not _can_read_message_history(
        message.channel, guild, requester=message.author
    ):
        return
    powermemory = _get_powermemory()
    if powermemory is None or not app_config.agent_group_memory:
        return

    key = _discord_group_messages_key(guild.id, message.channel.id)
    group_messages: list[DiscordGroupMemoryMessage] = await common.memttlcache.get(
        key, []
    )
    group_messages.append(
        DiscordGroupMemoryMessage(
            guild_id=guild.id,
            channel_id=message.channel.id,
            message_id=message.id,
            text=content,
            sender_name=_author_name(message),
            sender_id=message.author.id,
            created_at=message.created_at or datetime.now(UTC),
        )
    )
    if len(group_messages) >= _DISCORD_GROUP_MEMORY_BATCH_SIZE:
        group_messages = group_messages[-_DISCORD_GROUP_MEMORY_BATCH_SIZE:]
        update_key = _discord_group_memory_update_key(guild.id, message.channel.id)
        retry_key = f"{update_key}:retry"
        if not await common.memttlcache.get(
            update_key
        ) and not await common.memttlcache.get(retry_key):
            memory_text = "Discord server message log:\n" + "\n".join(
                f"{item.sender_name}({item.sender_id}) in #{item.channel_id}: {item.text}"
                for item in group_messages
            )
            try:
                result = await asyncio.wait_for(
                    powermemory.add(
                        memory_text,
                        infer=True,
                        user_id=_discord_group_memory_user_id(
                            guild.id, message.channel.id
                        ),
                        prompt=(
                            "You are Waku's Discord server memory. Extract useful facts, "
                            "member preferences, relationships, recurring topics, jokes, "
                            "or notable events worth remembering for this Discord server."
                        ),
                    ),
                    timeout=30,
                )
                logger.debug(
                    "Discord group memory updated: "
                    f"guild={guild.id} messages={len(group_messages)} result={result}"
                )
            except Exception as e:
                await common.memttlcache.set(retry_key, True, ttl=60)
                logger.error(
                    "Discord group memory update failed: "
                    f"guild={guild.id} error={e.__class__.__name__}: {e}"
                )
            else:
                await common.memttlcache.set(update_key, True, ttl=3600)
                group_messages = []
    await common.memttlcache.set(
        key,
        group_messages,
        ttl=_DISCORD_GROUP_MEMORY_TTL,
    )


def _sanitize_discord_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Remove previous tool messages before reusing Discord history.

    DeepSeek's OpenAI-compatible endpoint is strict about historical `tool`
    messages: each one must immediately follow its matching assistant
    `tool_calls` message. Discord does not need old tool internals for memory, so
    we keep user/text assistant history and drop old tool call/return messages.
    Current-turn tool calls still work normally because this only sanitizes
    cached history before the next request.
    """
    cleaned: list[ModelMessage] = []
    removed = 0
    for msg in messages:
        if isinstance(msg, ModelResponse):
            # Preserve final text even when the same response contains tool calls.
            parts = [
                part
                for part in msg.parts
                if isinstance(part, TextPart) and part.content.strip()
            ]
        else:
            parts = [
                part
                for part in msg.parts
                if getattr(part, "part_kind", "")
                not in {"tool-call", "tool-return", "retry-prompt"}
            ]
        if len(parts) != len(msg.parts):
            removed += 1
        if parts:
            cleaned.append(
                msg if len(parts) == len(msg.parts) else replace(msg, parts=parts)
            )
    if removed:
        logger.debug(f"Discord history sanitized: removed {removed} tool messages")
    return cleaned


async def _prepare_discord_history(messages, model, deps, agent):
    """Apply the new base's context limits without reusing stale tool internals."""
    cleaned = _sanitize_discord_history(messages)
    limit = app_config.agent_multimodal_max_items
    if limit > 0:
        cleaned = truncate_multimodal(cleaned, limit)
    effective_model = model if model is not None else getattr(agent, "model", None)
    compacted = await compact_history(cleaned, effective_model, deps=deps, agent=agent)
    return _sanitize_discord_history(compacted)


def _strip_multimodal_history_for_text_model(
    history: list[ModelMessage],
) -> list[ModelMessage]:
    """Return Discord history safe for text-only providers.

    After a Discord image/sticker turn, pydantic-ai stores multimodal parts in
    history. If the next turn goes back to a text-only model, providers like
    DeepSeek reject old `image_url` payloads. Keep the dialog shape but replace
    binary/image parts with a short text marker, matching Telegram's behavior.
    """
    sanitized: list[ModelMessage] = []
    replaced = 0
    for msg in history:
        if not isinstance(msg, ModelRequest):
            sanitized.append(msg)
            continue

        changed = False
        parts = []
        for part in msg.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, list):
                content = []
                for item in part.content:
                    if isinstance(item, MULTI_MODAL_CONTENT_TYPES):
                        content.append(
                            "[multimodal content omitted from text-model history]"
                        )
                        changed = True
                        replaced += 1
                    else:
                        content.append(item)
                parts.append(replace(part, content=content))
            elif isinstance(part, ToolReturnPart) and isinstance(
                part.content,
                MULTI_MODAL_CONTENT_TYPES,
            ):
                changed = True
                replaced += 1
                parts.append(
                    replace(
                        part,
                        content="[multimodal tool content omitted from text-model history]",
                    )
                )
            else:
                parts.append(part)
        sanitized.append(replace(msg, parts=parts) if changed else msg)
    if replaced:
        logger.debug(f"Discord history sanitized: replaced {replaced} multimodal items")
    return sanitized
