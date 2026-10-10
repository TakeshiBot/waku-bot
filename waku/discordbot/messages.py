from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime

import discord
from pydantic_ai import UserContent

from waku.config import app_config
from waku.logger import logger
from waku.timezone import BOT_TIMEZONE

from . import state
from .constants import (
    _CUSTOM_EMOJI_RE,
    _EMOJI_RE,
    _KEYWORD_SEPARATORS,
    _SEG_COMMANDS,
    DISCORD_REPLY_DELAY_MAX,
    DISCORD_REPLY_DELAY_MIN,
    DISCORD_REPLY_MAX_MESSAGES,
)
from .history import _discord_emoji_hint, _discord_reaction_hint
from .media import (
    _discord_attachment_contents,
    _discord_sticker_contents,
    _find_artwork_url,
)
from .permissions import (
    _channel_allowed,
    _channel_candidate_ids,
    _is_discord_user_bot_admin,
)
from .settings import (
    _discord_dm_settings,
    _discord_global_ai_enabled,
    _discord_guild_settings,
)
from .utilities import (
    _author_name,
    _channel_name,
    _clean_content,
    _guild_name,
    _message_text,
)


def _normalize_keyword(keyword: str) -> str:
    return keyword.strip().lower()

def _keyword_regex(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword)
    return re.compile(
        rf"(^|[\s,，:：!！?？])({escaped})($|[\s,，:：!！?？])",
        re.IGNORECASE,
    )

def _discord_wake_keywords() -> list[str]:
    keywords = app_config.discord_keywords
    if keywords is None:
        keywords = [app_config.nickname]
    return [keyword for keyword in keywords if keyword.strip()]

def _strip_bot_mention(content: str, bot_user_id: int) -> str:
    mention_forms = (f"<@{bot_user_id}>", f"<@!{bot_user_id}>")
    text = content
    for mention in mention_forms:
        text = text.replace(mention, "")
    return text.strip()

def _strip_keyword(content: str) -> str:
    text = content.strip()
    lowered = text.lower()
    for keyword in _discord_wake_keywords():
        normalized = _normalize_keyword(keyword)
        if not normalized:
            continue
        if lowered == normalized:
            return ""
        for sep in _KEYWORD_SEPARATORS:
            prefix = normalized + sep
            if lowered.startswith(prefix):
                return text[len(prefix) :].strip()
        match = _keyword_regex(normalized).search(text)
        if match:
            start, end = match.span(2)
            return (text[:start] + text[end:]).strip(" \n\t,，:：!！?？")
    return text

def _matches_keyword(content: str) -> bool:
    text = content.strip()
    lowered = text.lower()
    if not text:
        return False
    for keyword in _discord_wake_keywords():
        normalized = _normalize_keyword(keyword)
        if not normalized:
            continue
        if lowered == normalized:
            return True
        if any(lowered.startswith(normalized + sep) for sep in _KEYWORD_SEPARATORS):
            return True
        if _keyword_regex(normalized).search(text):
            return True
    return False

def _is_seg_command(content: str) -> bool:
    lowered = content.strip().lower()
    if not lowered:
        return False
    return any(
        lowered == command or lowered.startswith(command + " ")
        for command in _SEG_COMMANDS
    )


def _seg_command_keyword(content: str) -> str:
    """Return the optional search keyword from a recognised text SEG command."""
    text = content.strip()
    lowered = text.lower()
    for command in _SEG_COMMANDS:
        if lowered == command:
            return ""
        if lowered.startswith(command + " "):
            return text[len(command) :].strip()
    return ""

def _is_reply_to_bot(message: discord.Message, bot_user: discord.ClientUser) -> bool:
    ref = message.reference
    if ref is None:
        return False
    resolved = ref.resolved
    if isinstance(resolved, discord.Message):
        return resolved.author.id == bot_user.id
    cached = getattr(ref, "cached_message", None)
    if isinstance(cached, discord.Message):
        return cached.author.id == bot_user.id
    return False


def _discord_current_time() -> str:
    return datetime.now(BOT_TIMEZONE).isoformat(timespec="seconds")


async def _should_wake(message: discord.Message, bot_user: discord.ClientUser) -> tuple[bool, str]:
    if message.author.bot:
        return False, ""
    is_dm = message.guild is None
    if is_dm and (
        not isinstance(message.channel, discord.DMChannel)
        or not _is_discord_user_bot_admin(message.author)
    ):
        return False, ""
    if not isinstance(message.channel, discord.abc.Messageable):
        return False, ""

    content = _message_text(message)
    mentioned = bot_user in message.mentions
    replied_to_bot = _is_reply_to_bot(message, bot_user)
    keyword = _matches_keyword(content)
    seg_command = _is_seg_command(content)
    artwork_url = _find_artwork_url(content)

    if not content and not is_dm and not mentioned and not replied_to_bot:
        if not state.warned_empty_content:
            logger.warning(
                "Discord message content is empty; keyword/seg wake requires "
                "Message Content Intent to be enabled in Discord Developer Portal"
            )
            state.warned_empty_content = True

    if (
        not is_dm
        and not mentioned
        and not replied_to_bot
        and not keyword
        and not seg_command
        and not artwork_url
    ):
        return False, ""
    if not is_dm and not await _channel_allowed(message):
        logger.debug(
            "Discord wake ignored because Waku is disabled for server: "
            f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
            f"candidate_ids={sorted(_channel_candidate_ids(message))}"
        )
        return False, ""
    if not seg_command and not artwork_url:
        if not await _discord_global_ai_enabled():
            return False, ""
        settings = (
            await _discord_dm_settings(message.author)
            if is_dm else await _discord_guild_settings(message.guild)
        )
        if not settings.ai_reply:
            logger.debug(
                "Discord AI reply ignored because it is disabled for server: "
                f"guild={_guild_name(message)!r} channel={_channel_name(message)!r}"
            )
            return False, ""

    prompt = _clean_content(message)
    if mentioned:
        prompt = _strip_bot_mention(prompt, bot_user.id)
    if keyword:
        prompt = _strip_keyword(prompt)
    if not prompt:
        prompt = "Please continue the conversation."
    return True, prompt

async def _resolve_discord_channel(
    message: discord.Message, channel_id: int | None
) -> object | None:
    if channel_id is None:
        return message.channel
    guild = message.guild
    if guild is None:
        return message.channel if channel_id == message.channel.id else None
    channel = None
    get_channel_or_thread = getattr(guild, "get_channel_or_thread", None)
    if callable(get_channel_or_thread):
        channel = get_channel_or_thread(channel_id)
    if channel is None:
        channel = guild.get_channel(channel_id) or guild.get_thread(channel_id)
    if channel is None and state.discord_client is not None:
        channel = state.discord_client.get_channel(channel_id)
    if channel is None and state.discord_client is not None:
        try:
            channel = await state.discord_client.fetch_channel(channel_id)
        except Exception as e:
            logger.debug(f"Discord fetch_channel failed for {channel_id}: {e}")
    # A model-supplied channel ID must never let a guild chat inspect or post
    # in another server the bot happens to share with this user.
    channel_guild = getattr(channel, "guild", None)
    return channel if getattr(channel_guild, "id", None) == guild.id else None

async def _reply_context(message: discord.Message) -> str | None:
    ref = message.reference
    if ref is None:
        return None
    replied = ref.resolved
    if isinstance(replied, discord.Message):
        text = _clean_content(replied)
        if text:
            return (
                f"Referenced message to use as source/instructions, not as the requester/target: "
                f"author={_author_name(replied)} text={text[:1000]}"
            )
    return None

async def _build_prompt(message: discord.Message, user_prompt: str) -> tuple[list[UserContent], bool]:
    """Bound media preparation so one stalled attachment cannot hang a chat turn."""
    configured_timeout = app_config.agent_download_timeout
    timeout = configured_timeout if configured_timeout > 0 else 30
    try:
        return await asyncio.wait_for(_build_prompt_impl(message, user_prompt), timeout)
    except TimeoutError:
        logger.warning(
            "Discord prompt media preparation timed out: "
            f"user={message.author.id} channel={message.channel.id} after={timeout}s"
        )
        text = (
            "ContextInfo[Discord chat]\n"
            f"Server: {_guild_name(message)}\n"
            f"Channel: {_channel_name(message)}\n"
            f"User: {_author_name(message)} (id={message.author.id})\n"
            f"Current time: {_discord_current_time()}\n"
            "Media preparation timed out; attachments and stickers are unavailable. "
            "Do not claim to have seen or analyzed them.\n"
            f"User message:\n{user_prompt or '[No text message]'}"
        )
        return [text], False


async def _build_prompt_impl(message: discord.Message, user_prompt: str) -> tuple[list[UserContent], bool]:
    parts = [
        "ContextInfo[Discord chat]",
        f"Server: {_guild_name(message)}",
        f"Channel: {_channel_name(message)}",
        f"User: {_author_name(message)} (id={message.author.id})",
        f"Current time: {_discord_current_time()}",
    ]
    reply_ctx = await _reply_context(message)
    replied = message.reference.resolved if message.reference else None
    if reply_ctx:
        parts.append(reply_ctx)
    emoji_hint = await _discord_emoji_hint(message)
    if emoji_hint:
        parts.append(emoji_hint)
    reaction_hint = await _discord_reaction_hint(message)
    if reaction_hint:
        parts.append(reaction_hint)
    mentioned_users = [user for user in message.mentions if not user.bot]
    if mentioned_users:
        parts.append("Mentioned Discord users in the current message:")
        for user in mentioned_users[:10]:
            nick = getattr(user, "nick", None)
            global_name = getattr(user, "global_name", None)
            parts.append(
                f"- id={user.id} mention=<@{user.id}> name={user.name} "
                f"display={user.display_name} global={global_name or ''} nick={nick or ''}"
            )
        parts.append(
            "If the user asks to tag/remind one of these users, use the id above for target_user_ids even if their @name is missing from the cleaned text."
        )
    attachment_summaries, attachment_contents = await _discord_attachment_contents(message)
    sticker_summaries, sticker_contents = await _discord_sticker_contents(message)
    replied_attachment_summaries: list[str] = []
    replied_attachment_contents: list[UserContent] = []
    replied_sticker_summaries: list[str] = []
    replied_sticker_contents: list[UserContent] = []
    if isinstance(replied, discord.Message):
        replied_attachment_summaries, replied_attachment_contents = await _discord_attachment_contents(
            replied, label="replied attachment"
        )
        replied_sticker_summaries, replied_sticker_contents = await _discord_sticker_contents(
            replied, label="replied sticker"
        )
    media_summaries = (
        attachment_summaries
        + sticker_summaries
        + replied_attachment_summaries
        + replied_sticker_summaries
    )
    if media_summaries:
        parts.append("User/replied media:")
        parts.extend(media_summaries)
        if replied_attachment_contents or replied_sticker_contents:
            parts.append(
                "The user is replying to a message that contains media. Treat that replied media as the image/sticker they want analyzed; do not send a new image unless explicitly asked."
            )
    parts.append("User message:")
    parts.append(user_prompt or "[No text message]")
    media = attachment_contents + sticker_contents + replied_attachment_contents + replied_sticker_contents
    positive_limits = [limit for limit in (app_config.agent_multimodal_input_count, app_config.agent_multimodal_max_items) if limit > 0]
    if positive_limits and len(media) > min(positive_limits):
        media = media[:min(positive_limits)]
        parts.append("Some media exceeded the configured image budget and were omitted; do not claim to have seen omitted images.")
    contents: list[UserContent] = ["\n".join(parts), *media]
    needs_multimodal = bool(media)
    return contents, needs_multimodal

def _split_reply(text: str) -> list[str]:
    remaining = text.strip()
    if not remaining:
        return []
    chunks: list[str] = []
    fence_language: str | None = None
    while remaining:
        prefix = f"```{fence_language}\n" if fence_language is not None else ""
        if len(prefix) + len(remaining) <= 1900:
            chunks.append(prefix + remaining)
            break

        # Leave room to close an unfinished code block at the message edge.
        limit = 1900 - len(prefix) - 5
        split_at = remaining.rfind("\n\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at < limit // 2:
            split_at = limit
            # Keep the legacy break priorities; only adjust a forced boundary
            # that would cut Discord markup or an emoji grapheme in two.
            for pattern in (_CUSTOM_EMOJI_RE, _EMOJI_RE):
                for match in pattern.finditer(remaining):
                    if match.start() >= split_at:
                        break
                    if 0 < match.start() < split_at < match.end():
                        split_at = match.start()
                        break

        fragment = remaining[:split_at]
        for match in re.finditer(r"(?m)^```([^\n`]*)", fragment):
            fence_language = match.group(1).strip()[:32] if fence_language is None else None
        suffix = "\n```" if fence_language is not None else ""
        chunks.append(prefix + fragment.rstrip() + suffix)
        remaining = remaining[split_at:]
        remaining = remaining.lstrip("\n") if fence_language is not None else remaining.lstrip()

    if len(chunks) > DISCORD_REPLY_MAX_MESSAGES:
        chunks = chunks[:DISCORD_REPLY_MAX_MESSAGES]
        suffix = "\n\n… (phần còn lại quá dài nên đã lược bớt)"
        closing_fence = "\n```" if chunks[-1].endswith("\n```") else ""
        last_body = chunks[-1][: -len(closing_fence)] if closing_fence else chunks[-1]
        chunks[-1] = (
            last_body[: 1900 - len(suffix) - len(closing_fence)].rstrip()
            + closing_fence
            + suffix
        )
    return chunks

async def _send_reply(message: discord.Message, text: str) -> None:
    chunks = _split_reply(text)
    if not chunks:
        return
    delay_min = DISCORD_REPLY_DELAY_MIN
    delay_max = DISCORD_REPLY_DELAY_MAX
    reference = discord.MessageReference(
        message_id=message.id,
        channel_id=message.channel.id,
        guild_id=getattr(message.guild, "id", None),
        fail_if_not_exists=False,
    )
    async with message.channel.typing():
        for index, chunk in enumerate(chunks):
            await message.channel.send(
                chunk,
                reference=reference if index == 0 else None,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False, replied_user=False
                ),
            )
            if index < len(chunks) - 1:
                await asyncio.sleep(random.uniform(delay_min, delay_max) + len(chunk) / 900)
