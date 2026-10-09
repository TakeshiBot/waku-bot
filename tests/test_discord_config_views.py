from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from waku import common
from waku.discordbot import permissions, settings, state
from waku.discordbot.models import DiscordGuildSettings
from waku.discordbot.views import authorization, server_list
from waku.discordbot.views import config as config_views


def _interaction(*, user_id: int, guild=None, administrator: bool = False):
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(administrator=administrator),
        ),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_guild_config_buttons_require_current_admin_permission(monkeypatch):
    guild = SimpleNamespace(id=123, owner_id=1)
    view = config_views.DiscordConfigView(guild, DiscordGuildSettings(enabled=True))
    monkeypatch.setattr(
        config_views, "_discord_guild_settings", AsyncMock(return_value=DiscordGuildSettings(enabled=True))
    )
    monkeypatch.setattr(config_views, "_is_discord_user_bot_admin", lambda user: user.id == 9)

    ordinary = _interaction(user_id=2, guild=guild)
    assert not await view.interaction_check(ordinary)
    ordinary.response.send_message.assert_awaited_once()

    owner = _interaction(user_id=1, guild=guild)
    assert await view.interaction_check(owner)

    administrator = _interaction(user_id=3, guild=guild, administrator=True)
    assert await view.interaction_check(administrator)

    bot_admin = _interaction(user_id=9, guild=guild)
    assert await view.interaction_check(bot_admin)


@pytest.mark.asyncio
async def test_guild_config_rejects_wrong_or_revoked_server(monkeypatch):
    guild = SimpleNamespace(id=123, owner_id=1)
    view = config_views.DiscordConfigView(guild, DiscordGuildSettings(enabled=True))
    monkeypatch.setattr(config_views, "_is_discord_user_bot_admin", lambda user: False)

    wrong_guild = _interaction(user_id=1, guild=SimpleNamespace(id=456, owner_id=1))
    assert not await view.interaction_check(wrong_guild)

    monkeypatch.setattr(
        config_views, "_discord_guild_settings", AsyncMock(return_value=DiscordGuildSettings(enabled=False))
    )
    revoked = _interaction(user_id=1, guild=guild)
    assert not await view.interaction_check(revoked)
    revoked.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_dm_config_buttons_only_accept_owner():
    user = SimpleNamespace(id=123)
    view = config_views.DiscordDMConfigView(user, DiscordGuildSettings(enabled=True))

    intruder = _interaction(user_id=456)
    assert not await view.interaction_check(intruder)
    intruder.response.send_message.assert_awaited_once()

    owner = _interaction(user_id=123)
    assert await view.interaction_check(owner)


@pytest.mark.asyncio
async def test_dm_settings_read_error_does_not_enable_ai(monkeypatch):
    monkeypatch.setattr(settings.repository, "get_discord_chat_config", AsyncMock(side_effect=RuntimeError("offline")))
    result = await settings._discord_dm_settings(SimpleNamespace(id=123))
    assert not result.ai_reply


@pytest.mark.asyncio
async def test_guild_settings_cache_returns_independent_copy(monkeypatch):
    cached = DiscordGuildSettings(enabled=False)
    monkeypatch.setattr(common.memttlcache, "get", AsyncMock(return_value=cached))

    result = await settings._discord_guild_settings(SimpleNamespace(id=123, name="test"))
    result.enabled = True

    assert cached.enabled is False


@pytest.mark.asyncio
async def test_unauthorizing_discord_patches_only_discord_fields(monkeypatch):
    patch = AsyncMock()
    monkeypatch.setattr(settings.repository, "patch_discord_chat_config", patch)
    monkeypatch.setattr(common.memttlcache, "delete", AsyncMock())

    await settings._delete_discord_guild_settings_by_id(123)

    patch.assert_awaited_once()
    assert patch.await_args.args[0] == 123
    changes = patch.await_args.args[1]
    assert changes["discord_enabled"] is False
    assert changes["discord_auth_status"] == "none"
    assert changes["discord_auth_requester_id"] is None
    assert "greeting" not in changes


def test_server_list_embed_stays_within_discord_limit_and_shows_setu_off(monkeypatch):
    client = SimpleNamespace(
        get_guild=lambda guild_id: SimpleNamespace(name="Long server name " * 20)
    )
    monkeypatch.setattr(state, "discord_client", client)
    rows = [
        (10**17 + number, {"discord_r18_mode": "broken", "setu_enabled": False})
        for number in range(30)
    ]

    embed = server_list._build_discord_server_list_embed(rows)

    assert len(embed) <= 6000
    assert len(embed.fields) <= 25
    assert "OFF" in embed.fields[0].value
    assert f"of {len(rows)} servers" in embed.footer.text


def test_unknown_bot_channel_permissions_fail_closed(monkeypatch):
    monkeypatch.setattr(state, "discord_client", None)
    guild = SimpleNamespace(me=None)
    channel = SimpleNamespace(permissions_for=Mock())

    assert not permissions._can_view_channel(channel, guild)
    assert not permissions._can_read_message_history(channel, guild)
    channel.permissions_for.assert_not_called()

    assert permissions._can_view_channel(channel, None)


@pytest.mark.asyncio
async def test_old_server_and_dm_menu_layout_is_preserved_with_canonical_locale():
    config = DiscordGuildSettings(
        enabled=True, r18_mode=2, ai_reply=False, group_memory_enabled=False, lang="vi"
    )
    guild_view = config_views.DiscordConfigView(SimpleNamespace(id=123), config)
    await guild_view._sync_buttons()
    assert [(button.label, button.row) for button in guild_view.children] == [
        ("R18: Mixed", 0), ("AI Reply: OFF", 0), ("Group Memory: OFF", 1),
        ("Language: Tiếng Việt", 1), ("Save", 0),
    ]
    embed = config_views._discord_config_embed(config)
    assert embed.title == "Waku Bot Server config"
    assert embed.description == (
        "Server: `Authorized!`\nAI Reply: `OFF`\nGroup Memory: `OFF`\n"
        "R18/SEG images: `Mixed`\nLanguage: `Tiếng Việt (vi-VN)`\n"
        "\nPress `Save` to apply changes."
    )
    dm_view = config_views.DiscordDMConfigView(SimpleNamespace(id=123), config)
    await dm_view._sync_buttons()
    assert [(button.label, button.row) for button in dm_view.children] == [
        ("AI Reply: OFF", 0), ("Language: Tiếng Việt", 1), ("Save", 0),
    ]


@pytest.mark.asyncio
async def test_persistent_authorization_and_reload_component_ids_stay_compatible():
    request = authorization.DiscordAuthorizationRequestView(pending=True)
    assert request.timeout is None
    assert request.request_access.disabled
    assert request.request_access.label == "Đang chờ duyệt"
    assert request.request_access.custom_id == "waku:discord:auth:request"
    review = authorization.DiscordAuthorizationReviewView()
    assert review.timeout is None
    assert [button.custom_id for button in review.children] == [
        "waku:discord:auth:approve", "waku:discord:auth:reject",
    ]
    menu = server_list.DiscordServerListView()
    assert menu.timeout is None
    assert menu.reload_servers.label == "Reload"
    assert menu.reload_servers.custom_id == "waku:discord_server_list:reload"
