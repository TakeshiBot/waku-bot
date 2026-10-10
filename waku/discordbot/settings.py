from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime

import discord

from waku import common
from waku.config import app_config
from waku.database import discord as repository
from waku.i18n import normalize_locale
from waku.logger import logger

from . import state
from .constants import (
    _DISCORD_GUILD_SETTINGS_CACHE_PREFIX,
    _DISCORD_GUILD_SETTINGS_CACHE_TTL,
    _R18_KEYWORDS,
)
from .models import DiscordGuildSettings


async def _discord_global_ai_enabled() -> bool:
    async with state._discord_settings_lock(0):
        if state.discord_global_ai_enabled is None:
            try:
                state.discord_global_ai_enabled = await repository.get_discord_global_ai_enabled()
            except Exception as error:
                logger.warning(f"Discord global AI setting unavailable: {type(error).__name__}")
                return False
        return state.discord_global_ai_enabled


async def _set_discord_global_ai_enabled(enabled: bool) -> None:
    if not isinstance(enabled, bool):
        raise ValueError("Discord global AI switch must be boolean")
    async with state._discord_settings_lock(0):
        await repository.set_discord_global_ai_enabled(enabled)
        state.discord_global_ai_enabled = enabled
        pending = (
            tuple(task for task in state.discord_ai_tasks if task is not asyncio.current_task())
            if not enabled else ()
        )
        for task in pending:
            task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _discord_settings_cache_key(guild_id: int) -> str:
    return f"{_DISCORD_GUILD_SETTINGS_CACHE_PREFIX}{guild_id}"


def _history_epoch_key(guild_id: int) -> str:
    return f"discord_history_epoch:{guild_id}"


async def _discord_history_epoch(guild: discord.Guild | None) -> str:
    if guild is None:
        return "dm"
    return str(await common.memttlcache.get(_history_epoch_key(guild.id), "0"))


async def _rotate_discord_history_epoch(guild: discord.Guild) -> None:
    epoch = str(datetime.now(UTC).timestamp())
    await common.memttlcache.set(
        _history_epoch_key(guild.id), epoch, ttl=app_config.cachettl_agent_history
    )
    logger.info(f"Discord AI history reset: guild={guild.name!r}({guild.id}) epoch={epoch}")


async def _history_key(message: discord.Message) -> str:
    epoch = await _discord_history_epoch(message.guild)
    return f"discord_message_history:{epoch}:{message.channel.id}:{message.author.id}"


def _waiting_key(user_id: int) -> str:
    return f"discord_agent_waiting:{user_id}"


def _discord_dm_config_id(user_id: int) -> int:
    return -abs(int(user_id))


def _settings_from_config(config, *, dm: bool = False) -> DiscordGuildSettings:
    return DiscordGuildSettings(
        enabled=True,
        r18_mode=0 if dm else max(0, min(2, int(config.discord_r18_mode))),
        ai_reply=config.discord_ai_reply,
        reply_to_bots=False if dm else config.discord_reply_to_bots,
        group_memory_enabled=False if dm else config.group_memory_enabled,
        setu_enabled=config.setu_enabled,
        lang=normalize_locale(config.lang),
    )


async def _discord_guild_settings(guild: discord.Guild | None) -> DiscordGuildSettings:
    if guild is None:
        return DiscordGuildSettings(enabled=True, group_memory_enabled=False, reply_to_bots=False)
    cached = await common.memttlcache.get(_discord_settings_cache_key(guild.id))
    if isinstance(cached, DiscordGuildSettings):
        settings = copy.copy(cached)
        settings.enabled = True
        return settings
    try:
        config = await repository.get_discord_chat_config(guild.id)
        settings = _settings_from_config(config)
        await common.memttlcache.set(
            _discord_settings_cache_key(guild.id), settings,
            ttl=_DISCORD_GUILD_SETTINGS_CACHE_TTL,
        )
        return copy.copy(settings)
    except Exception as exc:
        logger.error(f"Failed to load Discord guild settings from DB: {type(exc).__name__}")
        return DiscordGuildSettings(
            ai_reply=False, group_memory_enabled=False, setu_enabled=False, reply_to_bots=False
        )


async def _set_discord_guild_settings(guild, settings) -> None:
    await _set_discord_guild_settings_by_id(guild.id, guild.name, settings)


async def _set_discord_guild_settings_by_id(guild_id, guild_name, settings) -> None:
    updates = {
        "discord_enabled": True,
        "discord_allow_r18": settings.r18_mode != 0,
        "discord_r18_mode": max(0, min(2, int(settings.r18_mode))),
        "discord_ai_reply": settings.ai_reply,
        "discord_reply_to_bots": settings.reply_to_bots,
        "group_memory_enabled": settings.group_memory_enabled,
        "setu_enabled": settings.setu_enabled,
        "lang": normalize_locale(settings.lang),
    }
    async with state._discord_settings_lock(guild_id):
        await repository.patch_discord_chat_config(guild_id, updates, title=guild_name)
        await common.memttlcache.delete(_discord_settings_cache_key(guild_id))


async def _discord_dm_settings(user) -> DiscordGuildSettings:
    try:
        return _settings_from_config(
            await repository.get_discord_chat_config(_discord_dm_config_id(user.id)), dm=True
        )
    except Exception as exc:
        logger.error(f"Failed to load Discord DM settings from DB: user={user.id} error={type(exc).__name__}")
        return DiscordGuildSettings(enabled=True, r18_mode=0, ai_reply=False, group_memory_enabled=False, reply_to_bots=False)


async def _set_discord_dm_settings(user, settings) -> None:
    dm_id = _discord_dm_config_id(user.id)
    async with state._discord_settings_lock(dm_id):
        await repository.patch_discord_chat_config(
            dm_id,
            {
                "discord_enabled": True,
                "discord_muted": False,
                "discord_allow_r18": False,
                "discord_r18_mode": 0,
                "discord_ai_reply": settings.ai_reply,
                "setu_enabled": settings.setu_enabled,
                "lang": normalize_locale(settings.lang),
            },
            title=f"Discord DM {user}", username=str(user),
        )


async def _discord_r18_mode(guild) -> int:
    settings = await _discord_guild_settings(guild)
    return settings.r18_mode if settings.setu_enabled else 0


def _r18_mode_label(arg1, arg2=None) -> str:
    if arg2 is None:
        r18_mode, setu_enabled = int(arg1), True
    elif isinstance(arg1, bool):
        setu_enabled, r18_mode = arg1, int(arg2)
    elif isinstance(arg2, bool):
        r18_mode, setu_enabled = int(arg1), arg2
    else:
        setu_enabled, r18_mode = bool(arg1), int(arg2)
    if not setu_enabled:
        return "OFF"
    return {0: "Safe Only", 1: "R18 Only", 2: "Mixed"}.get(r18_mode, "Safe Only")


async def _discord_ai_reply_enabled(guild) -> bool:
    return (await _discord_guild_settings(guild)).ai_reply


def _contains_r18_keyword(text: str) -> bool:
    lowered = text.casefold()
    return any(keyword in lowered for keyword in _R18_KEYWORDS)
