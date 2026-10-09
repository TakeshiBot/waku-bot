from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from waku.discordbot import broadcast, permissions, state


def _interaction(user_id, guild=None, target_channel=None):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id), guild=guild, channel=target_channel,
        response=SimpleNamespace(
            is_done=lambda: False, send_message=AsyncMock(), defer=AsyncMock(),
        ),
        edit_original_response=AsyncMock(),
    )


def test_telegram_owners_are_not_discord_admins(monkeypatch):
    monkeypatch.setattr(permissions.app_config, "owners", [123])
    monkeypatch.setattr(permissions.app_config, "discord_admin_users", [456])
    assert not permissions._is_discord_user_bot_admin(SimpleNamespace(id=123))
    assert permissions._is_discord_user_bot_admin(SimpleNamespace(id=456))


def test_requester_and_bot_must_both_have_channel_access(monkeypatch):
    bot = SimpleNamespace(id=9)
    requester = SimpleNamespace(id=1)
    bot_permissions = discord.Permissions(view_channel=True, read_message_history=True)
    caller_permissions = discord.Permissions(view_channel=False, read_message_history=True)
    guild = SimpleNamespace(me=bot, get_member=lambda user_id: requester if user_id == 1 else bot)
    channel = SimpleNamespace(permissions_for=lambda member: bot_permissions if member.id == 9 else caller_permissions)
    assert permissions._can_view_channel(channel, guild)
    assert not permissions._can_view_channel(channel, guild, requester=requester)
    assert not permissions._can_read_message_history(channel, guild, requester=requester)
    caller_permissions.view_channel = True
    assert permissions._can_read_message_history(channel, guild, requester=requester)
    caller_permissions.read_message_history = False
    assert not permissions._can_read_message_history(channel, guild, requester=requester)


def test_private_threads_need_membership_or_manage_threads():
    bot = SimpleNamespace(id=9)
    requester = SimpleNamespace(id=1)
    role_permissions = discord.Permissions(view_channel=True, read_message_history=True)
    guild = SimpleNamespace(me=bot, get_member=lambda user_id: requester)
    thread = SimpleNamespace(
        is_private=lambda: True, me=SimpleNamespace(id=9), members=[],
        permissions_for=lambda member: role_permissions,
    )
    assert not permissions._can_view_channel(thread, guild, requester=requester)
    thread.members = [SimpleNamespace(id=1)]
    assert permissions._can_read_message_history(thread, guild, requester=requester)
    thread.members = []
    role_permissions.manage_threads = True
    assert permissions._can_view_channel(thread, guild, requester=requester)


@pytest.mark.asyncio
async def test_channel_allowlist_and_authorization_both_apply(monkeypatch):
    monkeypatch.setattr(permissions.app_config, "discord_channel_allowlist", [77])
    monkeypatch.setattr(permissions, "_discord_guild_settings", AsyncMock(return_value=SimpleNamespace(enabled=True)))
    message = SimpleNamespace(
        guild=SimpleNamespace(id=99), channel=SimpleNamespace(id=88, parent_id=77),
    )
    assert await permissions._channel_allowed(message)
    message.channel.parent_id = 66
    assert not await permissions._channel_allowed(message)
    monkeypatch.setattr(permissions.app_config, "discord_channel_allowlist", [99])
    permissions._discord_guild_settings.return_value.enabled = False
    assert not await permissions._channel_allowed(message)


@pytest.mark.asyncio
async def test_broadcast_rejects_unauthorized_without_any_delivery(monkeypatch):
    monkeypatch.setattr(permissions.app_config, "owners", [1])
    monkeypatch.setattr(permissions.app_config, "discord_admin_users", [])
    client = SimpleNamespace(guilds=[SimpleNamespace(id=10)])
    monkeypatch.setattr(state, "discord_client", client)
    choose = AsyncMock()
    monkeypatch.setattr(broadcast, "_broadcast_channel", choose)
    interaction = _interaction(1)
    await broadcast.send_discord_broadcast(interaction, "announcement", target="all")
    choose.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("target", "expected_ids"), [("auth", [10]), ("unauth", [20]), ("all", [10, 20])])
async def test_broadcast_targets_are_filtered_and_mentions_disabled(monkeypatch, target, expected_ids):
    monkeypatch.setattr(permissions.app_config, "discord_admin_users", [1])
    guilds = [SimpleNamespace(id=10), SimpleNamespace(id=20)]
    monkeypatch.setattr(state, "discord_client", SimpleNamespace(guilds=guilds))
    monkeypatch.setattr(broadcast, "_authorized_discord_guild_ids", AsyncMock(return_value={10}))
    sent_ids = []
    channels = []

    async def choose(guild, preferred=None):
        sent_ids.append(guild.id)
        channel = SimpleNamespace(send=AsyncMock())
        channels.append(channel)
        return channel, True

    monkeypatch.setattr(broadcast, "_broadcast_channel", choose)
    interaction = _interaction(1)
    await broadcast.send_discord_broadcast(interaction, "line1\\nline2", target=target)
    assert sent_ids == expected_ids
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    for channel in channels:
        payload = channel.send.await_args.kwargs
        assert payload["embed"].description == "line1\nline2"
        allowed = payload["allowed_mentions"].to_dict()
        assert allowed == {"parse": []}
    result = interaction.edit_original_response.await_args.kwargs["embed"]
    assert result.title == "Kết quả phát thông báo"
    assert result.fields[0].value == f"`{len(expected_ids)}` server"
