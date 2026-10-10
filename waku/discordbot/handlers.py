from __future__ import annotations

import asyncio
import random

import discord
from pydantic_ai.messages import ModelMessage

from waku import common
from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent import provider

from . import state
from .agent import (
    DiscordPostRunError,
    _discord_recovery_reply,
    _is_discord_history_error,
    _run_discord_agent_once,
)
from .broadcast import send_discord_broadcast_message
from .commands import send_discord_info_message
from .constants import (
    _DISCORD_BUSY_REPLIES,
    DISCORD_COMMAND_PREFIX,
    DISCORD_MESSAGE_HISTORY_LIMIT,
)
from .history import (
    _reaction_counter_key,
    _sanitize_discord_history,
    _strip_multimodal_history_for_text_model,
)
from .media import _find_artwork_url, _send_discord_artwork, _send_discord_seg
from .messages import (
    _build_prompt,
    _is_seg_command,
    _seg_command_keyword,
    _send_reply,
)
from .permissions import _is_discord_user_bot_admin
from .settings import (
    _discord_dm_settings,
    _discord_global_ai_enabled,
    _discord_guild_settings,
    _history_key,
    _waiting_key,
)
from .state import (
    _discord_agent_busy_timeout,
    _discord_agent_gate,
    _discord_agent_limit,
)
from .utilities import _channel_name, _clean_content, _guild_name, _message_text
from .views.server_list import open_discord_server_list_message


async def _handle_discord_admin_command(message: discord.Message) -> bool:
    """Route bot-admin DM commands and consume retired management prefixes."""
    content = _message_text(message)
    if not content.startswith(DISCORD_COMMAND_PREFIX):
        return False
    parts = content[len(DISCORD_COMMAND_PREFIX):].strip().split(maxsplit=1)
    command = parts[0].casefold() if parts else ""
    if command in {"bc", "info", "server"}:
        if (
            message.guild is None
            and isinstance(message.channel, discord.DMChannel)
            and _is_discord_user_bot_admin(message.author)
        ):
            if command == "bc":
                await send_discord_broadcast_message(message, parts[1] if len(parts) > 1 else "")
            elif command == "info":
                await send_discord_info_message(message, parts[1] if len(parts) > 1 else "")
            else:
                await open_discord_server_list_message(message)
        return True
    return command in {
        "waku", "unwaku", "config", "server", "forget", "help", "invite", "clean", "info"
    }


async def _maybe_handle_discord_media_request(message: discord.Message) -> bool:
    content = _clean_content(message) or _message_text(message)
    artwork_url = _find_artwork_url(content)
    if artwork_url:
        logger.info(
            f"Discord artwork request: guild={_guild_name(message)!r} "
            f"channel={_channel_name(message)!r} user={message.author.id} url={artwork_url}"
        )
        return await _send_discord_artwork(message, artwork_url)
    if _is_seg_command(content):
        logger.info(
            f"Discord command seg request: guild={_guild_name(message)!r} "
            f"channel={_channel_name(message)!r} user={message.author.id}"
        )
        return await _send_discord_seg(message, _seg_command_keyword(content))
    return False

async def _handle_message(message: discord.Message, user_prompt: str) -> None:
    if state.discord_agent is None:
        return
    if message.guild is None and (
        not isinstance(message.channel, discord.DMChannel)
        or not _is_discord_user_bot_admin(message.author)
        or not (await _discord_dm_settings(message.author)).ai_reply
    ):
        return
    if not await _discord_global_ai_enabled():
        return
    if message.guild is not None and getattr(message.author, "bot", False):
        settings = await _discord_guild_settings(message.guild)
        if not settings.reply_to_bots or not settings.ai_reply:
            return

    task = asyncio.current_task()
    if task is not None:
        state.discord_ai_tasks.add(task)
    waiting_key = _waiting_key(message.author.id)
    try:
        async with state._discord_turn_lock(message.author.id):
            owner = await common.memstore.get(waiting_key)
            already_waiting = isinstance(owner, asyncio.Task) and not owner.done()
            if not already_waiting:
                await common.memstore.set(waiting_key, task)
        if already_waiting:
            # Keep the active reply/typing indicator, without sending permanent
            # placeholders for every subsequent message from the same user.
            return
        try:
            await _handle_discord_message_turn(message, user_prompt)
        finally:
            if await common.memstore.get(waiting_key) is task:
                await common.memstore.delete(waiting_key)
    finally:
        if task is not None:
            state.discord_ai_tasks.discard(task)


async def _handle_discord_message_turn(message: discord.Message, user_prompt: str) -> None:

    history_key = await _history_key(message)
    history: list[ModelMessage] = await common.memttlcache.get(history_key, [])
    history = _sanitize_discord_history(history)
    periodic_reaction_nudge = ""
    reaction_interval = getattr(app_config, "discord_periodic_reaction_interval", None)
    if reaction_interval is None:
        reaction_interval = app_config.agent_periodic_reaction_interval
    if reaction_interval > 0:
        reaction_ctr: int = await common.memstore.get(_reaction_counter_key(message), 0)
        await common.memstore.set(_reaction_counter_key(message), reaction_ctr + 1)
        if reaction_ctr > 0 and reaction_ctr % reaction_interval == 0:
            periodic_reaction_nudge = (
                "\n\nDiscord reaction nudge: if appropriate, call send_discord_reaction "
                "exactly once this turn with an emoji that matches the user's message."
            )

    prompt, needs_multimodal = await _build_prompt(message, user_prompt + periodic_reaction_nudge)
    model_override = (
        provider.make_chat_model(app_config.agent_model_multimodal)
        if needs_multimodal and app_config.agent_model_multimodal
        else None
    )
    model_history = (
        history
        if model_override is not None
        else _strip_multimodal_history_for_text_model(history)
    )
    model_history = model_history[-DISCORD_MESSAGE_HISTORY_LIMIT :]

    gate = _discord_agent_gate()
    acquired_gate = False
    try:
        try:
            busy_timeout = _discord_agent_busy_timeout()
            if busy_timeout > 0:
                await asyncio.wait_for(gate.acquire(), timeout=busy_timeout)
            else:
                await gate.acquire()
            acquired_gate = True
        except TimeoutError:
            logger.info(
                "Discord agent busy timeout: "
                f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
                f"user={message.author.id} limit={_discord_agent_limit()}"
            )
            await _send_reply(message, random.choice(_DISCORD_BUSY_REPLIES))
            return

        try:
            await _run_discord_agent_once(
                message,
                prompt,
                history_key,
                model_history,
                model_override,
            )
            return
        except DiscordPostRunError as post_run_error:
            logger.error(
                "Discord agent completed but reply/cache failed; not retrying tools: "
                f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
                f"user={message.author.id} error={type(post_run_error).__name__}"
            )
            return
        except Exception as first_error:
            logger.warning(
                "Discord agent first attempt failed: "
                f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
                f"user={message.author.id} error={first_error.__class__.__name__}"
            )
            if not _is_discord_history_error(first_error):
                reply = await _discord_recovery_reply(message, user_prompt, first_error)
                await _send_reply(message, reply)
                return

            await common.memttlcache.delete(history_key)

            retry_prompt = list(prompt)
            retry_prompt.append(
                "\n\n[Discord retry instruction] The previous attempt failed before replying. "
                "Retry with clean context. If the issue appears temporary or overload-related, "
                "answer naturally in Vietnamese that Waku is a bit overloaded and the user should try again soon."
            )
            try:
                await _run_discord_agent_once(
                    message,
                    retry_prompt,
                    history_key,
                    [],
                    model_override,
                )
                logger.info(
                    "Discord agent retry succeeded: "
                    f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
                    f"user={message.author.id}"
                )
                return
            except DiscordPostRunError as post_run_error:
                logger.error(
                    "Discord retry completed but reply/cache failed; not retrying tools: "
                    f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
                    f"user={message.author.id} error={type(post_run_error).__name__}"
                )
                return
            except Exception as retry_error:
                logger.error(
                    "Discord agent retry failed: "
                    f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
                    f"user={message.author.id} error={retry_error.__class__.__name__}"
                )
                if _is_discord_history_error(retry_error):
                    await common.memttlcache.delete(history_key)
                reply = await _discord_recovery_reply(message, user_prompt, retry_error)
                await _send_reply(message, reply)
    finally:
        if acquired_gate:
            gate.release()
