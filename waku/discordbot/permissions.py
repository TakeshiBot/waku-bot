from __future__ import annotations

from datetime import UTC, datetime

import discord

from waku.config import app_config

from . import state
from .models import DiscordGuildSettings
from .settings import _discord_guild_settings


def _discord_allowed_mentions(allow_everyone: bool = False) -> discord.AllowedMentions:
    return discord.AllowedMentions(
        users=True,
        roles=False,
        everyone=allow_everyone,
    )

def _is_discord_user_bot_admin(user: discord.abc.User) -> bool:
    return user.id in set(app_config.discord_admin_users)

def _is_discord_bot_admin(message: discord.Message) -> bool:
    return _is_discord_user_bot_admin(message.author) or _is_discord_server_admin(message)

def _is_discord_server_admin(message: discord.Message) -> bool:
    guild = message.guild
    if guild is not None and guild.owner_id == message.author.id:
        return True
    permissions = getattr(message.author, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def _can_clean_discord_messages(message: discord.Message) -> bool:
    """Whether the caller may remove Waku's messages in this server channel."""
    guild = message.guild
    if guild is None:
        return False
    if guild.owner_id == message.author.id:
        return True
    permissions = getattr(message.author, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or permissions.manage_messages))

def _can_manage_discord_config(message: discord.Message, settings: DiscordGuildSettings) -> bool:
    if _is_discord_bot_admin(message):
        return settings.enabled
    return settings.enabled and _is_discord_server_admin(message)

async def _send_admin_notice(message: discord.Message, text: str) -> None:
    embed = discord.Embed(
        description=text,
        color=discord.Color.blurple(),
        timestamp=datetime.now(UTC),
    )
    await message.channel.send(embed=embed, reference=message, delete_after=10)

def _channel_candidate_ids(message: discord.Message) -> set[int]:
    ids = {message.channel.id}
    if message.guild is not None:
        ids.add(message.guild.id)
    parent_id = getattr(message.channel, "parent_id", None)
    if parent_id is not None:
        ids.add(parent_id)
    parent = getattr(message.channel, "parent", None)
    if parent is not None:
        ids.add(parent.id)
    return ids

async def _channel_allowed(message: discord.Message) -> bool:
    if message.guild is None:
        return True

    allowlist = set(app_config.discord_channel_allowlist)
    if allowlist and not (_channel_candidate_ids(message) & allowlist):
        return False
    settings = await _discord_guild_settings(message.guild)
    return settings.enabled

def _bot_member(guild: discord.Guild | None) -> discord.Member | None:
    if guild is None:
        return None
    member = guild.me
    if member is not None:
        return member
    if state.discord_client is not None and state.discord_client.user is not None:
        return guild.get_member(state.discord_client.user.id)
    return None

def _channel_permissions(channel: object, guild: discord.Guild | None) -> discord.Permissions | None:
    member = _bot_member(guild)
    permissions_for = getattr(channel, "permissions_for", None)
    if member is None or not callable(permissions_for):
        return None
    try:
        return permissions_for(member)
    except discord.ClientException:
        return None

def _requester_permissions(channel, guild, requester):
    if guild is None or requester is None:
        return None
    member = guild.get_member(requester.id)
    if member is None:
        return None
    permissions_for = getattr(channel, "permissions_for", None)
    if not callable(permissions_for):
        return None
    try:
        return permissions_for(member)
    except discord.ClientException:
        return None


def _private_thread_member(channel, member, permissions) -> bool:
    is_private = getattr(channel, "is_private", None)
    if not callable(is_private) or not is_private():
        return True
    if permissions.manage_threads:
        return True
    member_id = getattr(member, "id", None)
    if member_id is None:
        return False
    if getattr(getattr(channel, "me", None), "id", None) == member_id:
        return True
    return any(getattr(item, "id", None) == member_id for item in channel.members)


def _can_view_channel(channel: object, guild: discord.Guild | None, requester=None) -> bool:
    permissions = _channel_permissions(channel, guild)
    if guild is not None and permissions is None:
        return False
    if permissions is not None and (
        not permissions.view_channel
        or not _private_thread_member(channel, _bot_member(guild), permissions)
    ):
        return False
    if guild is not None and requester is not None:
        requested = _requester_permissions(channel, guild, requester)
        return bool(
            requested and requested.view_channel
            and _private_thread_member(channel, requester, requested)
        )
    return True

def _can_read_message_history(channel: object, guild: discord.Guild | None, requester=None) -> bool:
    if not _can_view_channel(channel, guild, requester=requester):
        return False
    permissions = _channel_permissions(channel, guild)
    if guild is not None and permissions is None:
        return False
    if permissions is not None and not permissions.read_message_history:
        return False
    if guild is not None and requester is not None:
        requested = _requester_permissions(channel, guild, requester)
        return bool(requested and requested.read_message_history)
    return True
