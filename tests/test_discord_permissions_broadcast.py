from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from waku.discordbot import broadcast, permissions, state


def _message(user_id=1, *, guild=None, channel=None, bot=False):
    client = discord.Client(intents=discord.Intents.none())
    if channel is None:
        channel = Mock(spec=discord.DMChannel)
        channel.id = 99
        channel.send = AsyncMock()
    message = discord.Message(
        state=client._connection,
        channel=channel,
        data={
            "id": "12345",
            "type": 0,
            "content": "!bc all Announcement",
            "author": {
                "id": str(user_id),
                "username": "Caller",
                "discriminator": "0",
                "avatar": None,
                "bot": bot,
            },
            "timestamp": "2026-10-10T00:00:00+00:00",
            "edited_timestamp": None,
            "tts": False,
            "mention_everyone": False,
            "mentions": [],
            "mention_roles": [],
            "attachments": [],
            "embeds": [],
            "pinned": False,
            "flags": 0,
        },
    )
    message.guild = guild
    return message


def test_telegram_owners_are_not_discord_admins(monkeypatch):
    monkeypatch.setattr(permissions.app_config, "owners", [123])
    monkeypatch.setattr(permissions.app_config, "discord_admin_users", [456])
    assert not permissions._is_discord_user_bot_admin(SimpleNamespace(id=123))
    assert permissions._is_discord_user_bot_admin(SimpleNamespace(id=456))


def test_requester_and_bot_must_both_have_channel_access(monkeypatch):
    bot = SimpleNamespace(id=9)
    requester = SimpleNamespace(id=1)
    bot_permissions = discord.Permissions(view_channel=True, read_message_history=True)
    caller_permissions = discord.Permissions(
        view_channel=False, read_message_history=True
    )
    guild = SimpleNamespace(
        me=bot, get_member=lambda user_id: requester if user_id == 1 else bot
    )
    channel = SimpleNamespace(
        permissions_for=lambda member: (
            bot_permissions if member.id == 9 else caller_permissions
        )
    )
    assert permissions._can_view_channel(channel, guild)
    assert not permissions._can_view_channel(channel, guild, requester=requester)
    assert not permissions._can_read_message_history(
        channel, guild, requester=requester
    )
    caller_permissions.view_channel = True
    assert permissions._can_read_message_history(channel, guild, requester=requester)
    caller_permissions.read_message_history = False
    assert not permissions._can_read_message_history(
        channel, guild, requester=requester
    )


def test_private_threads_need_membership_or_manage_threads():
    bot = SimpleNamespace(id=9)
    requester = SimpleNamespace(id=1)
    role_permissions = discord.Permissions(view_channel=True, read_message_history=True)
    guild = SimpleNamespace(me=bot, get_member=lambda user_id: requester)
    thread = SimpleNamespace(
        is_private=lambda: True,
        me=SimpleNamespace(id=9),
        members=[],
        permissions_for=lambda member: role_permissions,
    )
    assert not permissions._can_view_channel(thread, guild, requester=requester)
    thread.members = [SimpleNamespace(id=1)]
    assert permissions._can_read_message_history(thread, guild, requester=requester)
    thread.members = []
    role_permissions.manage_threads = True
    assert permissions._can_view_channel(thread, guild, requester=requester)


@pytest.mark.parametrize(
    "missing_permission", ["view_channel", "send_messages", "embed_links"]
)
def test_broadcast_requires_bot_view_send_and_embed_permissions(missing_permission):
    allowed = discord.Permissions(
        view_channel=True, send_messages=True, embed_links=True
    )
    channel = SimpleNamespace(permissions_for=lambda member: allowed)
    member = SimpleNamespace(id=9)
    assert broadcast._can_send_broadcast(channel, member)
    setattr(allowed, missing_permission, False)
    assert not broadcast._can_send_broadcast(channel, member)
    assert not broadcast._can_send_broadcast(channel, None)


@pytest.mark.asyncio
async def test_broadcast_skips_text_channel_without_embed_rights():
    blocked = Mock(spec=discord.TextChannel)
    blocked.id = 10
    blocked.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages=True
    )
    writable = Mock(spec=discord.TextChannel)
    writable.id = 20
    writable.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages=True, embed_links=True
    )
    guild = SimpleNamespace(
        me=SimpleNamespace(id=9),
        system_channel=blocked,
        text_channels=[blocked, writable],
    )
    assert await broadcast._broadcast_channel(guild, blocked) == (writable, True)


@pytest.mark.asyncio
async def test_channel_allowlist_applies_without_any_guild_approval(monkeypatch):
    monkeypatch.setattr(permissions.app_config, "discord_channel_allowlist", [77])
    message = SimpleNamespace(
        guild=SimpleNamespace(id=99),
        channel=SimpleNamespace(id=88, parent_id=77),
    )
    assert await permissions._channel_allowed(message)
    message.channel.parent_id = 66
    assert not await permissions._channel_allowed(message)
    monkeypatch.setattr(permissions.app_config, "discord_channel_allowlist", [99])
    assert await permissions._channel_allowed(message)
    monkeypatch.setattr(permissions.app_config, "discord_channel_allowlist", [])
    assert await permissions._channel_allowed(message)


@pytest.mark.parametrize("role", ["owner", "administrator", "bot_admin", "member"])
@pytest.mark.parametrize("legacy_enabled", [False, True])
def test_config_permissions_depend_on_discord_roles_not_legacy_approval(
    monkeypatch, role, legacy_enabled
):
    monkeypatch.setattr(
        permissions.app_config,
        "discord_admin_users",
        [1] if role == "bot_admin" else [],
    )
    message = SimpleNamespace(
        guild=SimpleNamespace(id=10, owner_id=1 if role == "owner" else 2),
        author=SimpleNamespace(
            id=1,
            guild_permissions=discord.Permissions(
                administrator=role == "administrator"
            ),
        ),
    )
    assert permissions._can_manage_discord_config(
        message, SimpleNamespace(enabled=legacy_enabled)
    ) is (role != "member")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["ordinary_dm", "telegram_owner_dm", "guild", "group_dm", "bot_sender"]
)
async def test_prefix_broadcast_is_silent_outside_configured_admin_dm(
    monkeypatch, kind
):
    monkeypatch.setattr(permissions.app_config, "owners", [1])
    monkeypatch.setattr(
        permissions.app_config,
        "discord_admin_users",
        [] if kind in {"ordinary_dm", "telegram_owner_dm"} else [1],
    )
    channel = Mock(
        spec=discord.GroupChannel if kind == "group_dm" else discord.DMChannel
    )
    channel.id = 99
    channel.send = AsyncMock()
    message = _message(
        channel=channel,
        guild=SimpleNamespace(id=10) if kind == "guild" else None,
        bot=kind == "bot_sender",
    )
    choose = AsyncMock()
    monkeypatch.setattr(broadcast, "_broadcast_channel", choose)
    await broadcast.send_discord_broadcast_message(message, "all Announcement")
    choose.assert_not_awaited()
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,expected_ids", [("10", [10]), ("all", [10, 20]), ("ALL", [10, 20])]
)
async def test_prefix_broadcast_announces_only_to_selected_guilds_and_replies_in_dm(
    monkeypatch, target, expected_ids
):
    monkeypatch.setattr(permissions.app_config, "discord_admin_users", [1])
    guilds = [SimpleNamespace(id=10), SimpleNamespace(id=20)]
    monkeypatch.setattr(
        state,
        "discord_client",
        SimpleNamespace(
            guilds=guilds,
            get_guild=lambda guild_id: next(
                (guild for guild in guilds if guild.id == guild_id), None
            ),
        ),
    )
    channels = {guild.id: SimpleNamespace(send=AsyncMock()) for guild in guilds}

    async def choose(guild):
        return channels[guild.id], True

    choose_mock = AsyncMock(side_effect=choose)
    monkeypatch.setattr(broadcast, "_broadcast_channel", choose_mock)
    message = _message()
    await broadcast.send_discord_broadcast_message(
        message, target + " line1\\nline2\nline3"
    )
    assert [call.args[0].id for call in choose_mock.await_args_list] == expected_ids
    assert all(len(call.args) == 1 for call in choose_mock.await_args_list)
    for guild_id, channel in channels.items():
        if guild_id not in expected_ids:
            channel.send.assert_not_awaited()
            continue
        channel.send.assert_awaited_once()
        payload = channel.send.await_args.kwargs
        assert payload["embed"].description == "line1\nline2\nline3"
        assert payload["allowed_mentions"].to_dict() == {"parse": []}
    message.channel.send.assert_awaited_once()
    result = message.channel.send.await_args.kwargs
    assert result["embed"].title == "Kết quả phát thông báo"
    assert result["embed"].fields[0].value == f"`{len(expected_ids)}` server"
    assert result["allowed_mentions"].to_dict() == {"parse": []}
    assert result["embed"].description != "line1\nline2\nline3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        "",
        "Announcement without target",
        "all",
        "here Announcement",
        "auth Announcement",
        "unauth Announcement",
        "0 Announcement",
        "-1 Announcement",
        str(2**64) + " Announcement",
        "１ Announcement",
        "all \\n",
        "all " + "x" * 4001,
    ],
)
async def test_prefix_invalid_target_or_body_reports_in_admin_dm_without_delivery(
    monkeypatch, arguments
):
    monkeypatch.setattr(permissions.app_config, "discord_admin_users", [1])
    choose = AsyncMock()
    monkeypatch.setattr(broadcast, "_broadcast_channel", choose)
    message = _message()
    await broadcast.send_discord_broadcast_message(message, arguments)
    choose.assert_not_awaited()
    message.channel.send.assert_awaited_once()
    payload = message.channel.send.await_args.kwargs
    assert isinstance(payload["embed"], discord.Embed)
    assert payload["allowed_mentions"].to_dict() == {"parse": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["client_missing", "no_guilds", "guild_missing"])
async def test_prefix_unavailable_destination_cannot_send_to_calling_dm(
    monkeypatch, kind
):
    monkeypatch.setattr(permissions.app_config, "discord_admin_users", [1])
    client = (
        None
        if kind == "client_missing"
        else SimpleNamespace(guilds=[], get_guild=lambda guild_id: None)
    )
    monkeypatch.setattr(state, "discord_client", client)
    choose = AsyncMock()
    monkeypatch.setattr(broadcast, "_broadcast_channel", choose)
    message = _message()
    await broadcast.send_discord_broadcast_message(
        message, "10 Announcement" if kind == "guild_missing" else "all Announcement"
    )
    choose.assert_not_awaited()
    message.channel.send.assert_awaited_once()
    assert (
        message.channel.send.await_args.kwargs["embed"].title != "📢 Thông báo từ Waku"
    )


@pytest.mark.asyncio
async def test_prefix_delivery_continues_after_http_error_and_counts_unwritable_guilds(
    monkeypatch,
):
    monkeypatch.setattr(permissions.app_config, "discord_admin_users", [1])
    guilds = [SimpleNamespace(id=10), SimpleNamespace(id=20), SimpleNamespace(id=30)]
    monkeypatch.setattr(state, "discord_client", SimpleNamespace(guilds=guilds))
    forbidden = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "missing access"
    )
    failed = SimpleNamespace(send=AsyncMock(side_effect=forbidden))
    success = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(
        broadcast,
        "_broadcast_channel",
        AsyncMock(side_effect=[(failed, True), (None, True), (success, True)]),
    )
    message = _message()
    await broadcast.send_discord_broadcast_message(message, "all Announcement")
    failed.send.assert_awaited_once()
    success.send.assert_awaited_once()
    result = message.channel.send.await_args.kwargs["embed"]
    assert result.fields[0].value == "`1` server"
    assert result.fields[1].value == "`1` server"
    assert result.fields[2].value == "`2` server"


def test_broadcast_exports_only_message_entry_point():
    assert broadcast.__all__ == ["send_discord_broadcast_message"]
    assert not hasattr(broadcast, "send_discord_broadcast")
