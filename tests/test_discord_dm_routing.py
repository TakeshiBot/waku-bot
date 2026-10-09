"""DM features are restricted to bot admins; chat respects both AI switches."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from waku.discordbot import client, handlers, messages, state
from waku.discordbot.models import DiscordGuildSettings


def message(content="hello", *, admin=False, guild=False):
    channel = Mock(spec=discord.TextChannel if guild else discord.DMChannel)
    channel.id = 20
    channel.send = AsyncMock()
    return SimpleNamespace(
        content=content, clean_content=content, channel=channel,
        guild=SimpleNamespace(id=10, name="Server") if guild else None,
        author=SimpleNamespace(id=1 if admin else 2, bot=False),
        mentions=[], reference=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["hello waku", "<@900> help", "!seg", "https://www.pixiv.net/artworks/123", "!config"])
async def test_native_message_entry_silently_ignores_nonadmin_dm_chat(monkeypatch, content):
    monkeypatch.setattr(handlers.app_config, "discord_admin_users", [1])
    monkeypatch.setattr(state, "discord_stopping", False)
    monkeypatch.setattr(state, "discord_message_tasks", set())
    background = AsyncMock()
    wake = AsyncMock()
    ai = AsyncMock()
    media = AsyncMock()
    monkeypatch.setattr(client, "_record_discord_group_memory", background)
    monkeypatch.setattr(client, "_should_wake", wake)
    monkeypatch.setattr(client, "_handle_message", ai)
    monkeypatch.setattr(client, "_maybe_handle_discord_media_request", media)
    native_client = client._create_client()
    native_client._connection.user = SimpleNamespace(id=900)
    incoming = message(content, admin=False)
    try:
        await native_client.on_message(incoming)
        incoming.channel.send.assert_not_awaited()
        background.assert_not_awaited()
        wake.assert_not_awaited()
        ai.assert_not_awaited()
        media.assert_not_awaited()
        assert not state.discord_message_tasks
    finally:
        await native_client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("command,callback", [
    ("bc", "send_discord_broadcast_message"),
    ("info", "send_discord_info_message"),
    ("server", "open_discord_server_list_message"),
])
@pytest.mark.parametrize("admin,guild", [(True, False), (False, False), (True, True), (False, True)])
async def test_management_prefix_dispatch_requires_bot_admin_and_dm(monkeypatch, command, callback, admin, guild):
    monkeypatch.setattr(handlers.app_config, "discord_admin_users", [1])
    callbacks = {
        name: AsyncMock() for name in (
            "send_discord_broadcast_message", "send_discord_info_message", "open_discord_server_list_message"
        )
    }
    for name, function in callbacks.items():
        monkeypatch.setattr(handlers, name, function)
    incoming = message(f"!{command} all hello", admin=admin, guild=guild)
    assert await handlers._handle_discord_admin_command(incoming)
    for name, function in callbacks.items():
        if admin and not guild and name == callback:
            if command in {"bc", "info"}:
                function.assert_awaited_once_with(incoming, "all hello")
            else:
                function.assert_awaited_once_with(incoming)
        else:
            function.assert_not_awaited()
    incoming.channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_secondary_ai_entry_points_cannot_wake_or_answer_in_dm(monkeypatch):
    monkeypatch.setattr(handlers.app_config, "discord_admin_users", [1])
    incoming = message("waku hello", admin=False)
    monkeypatch.setattr(state, "discord_agent", object())
    prepare = AsyncMock()
    monkeypatch.setattr(handlers, "_handle_discord_message_turn", prepare)
    assert await messages._should_wake(incoming, SimpleNamespace(id=900)) == (False, "")
    await handlers._handle_message(incoming, "hello")
    prepare.assert_not_awaited()
    incoming.channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_slash_interactions_and_stale_slash_errors_are_silent_in_dm(monkeypatch):
    monkeypatch.setattr(state, "discord_stopping", False)
    request = SimpleNamespace(
        user=SimpleNamespace(id=2),
        guild=None, response=SimpleNamespace(send_message=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    native_client = client._create_client()
    try:
        tree = native_client._connection._command_tree
        assert not await tree.interaction_check(request)
        await tree.on_error(request, discord.app_commands.AppCommandError("stale DM command"))
        request.response.send_message.assert_not_awaited()
        request.edit_original_response.assert_not_awaited()
        assert {item.name for item in tree.get_commands()} == {"config", "help", "forget", "invite", "clean", "seg"}
        assert not any(item.guild_only for item in tree.get_commands())
    finally:
        await native_client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("global_on,personal_on", [(True, True), (True, False), (False, True), (False, False)])
async def test_admin_dm_ai_respects_global_and_personal_toggles(monkeypatch, global_on, personal_on):
    monkeypatch.setattr(handlers.app_config, "discord_admin_users", [1])
    for module in (handlers, messages):
        monkeypatch.setattr(module, "_discord_global_ai_enabled", AsyncMock(return_value=global_on))
        monkeypatch.setattr(module, "_discord_dm_settings", AsyncMock(return_value=DiscordGuildSettings(ai_reply=personal_on)))
    incoming = message("hello", admin=True)
    prepare = AsyncMock()
    monkeypatch.setattr(state, "discord_agent", object())
    monkeypatch.setattr(handlers, "_handle_discord_message_turn", prepare)
    should_wake, prompt = await messages._should_wake(incoming, SimpleNamespace(id=900))
    assert should_wake is (global_on and personal_on)
    if should_wake:
        assert prompt == "hello"
    await handlers._handle_message(incoming, "hello")
    assert prepare.await_count == int(global_on and personal_on)
    incoming.channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_slash_command_and_info_arguments_are_routed_in_dm(monkeypatch):
    monkeypatch.setattr(handlers.app_config, "discord_admin_users", [1])
    monkeypatch.setattr(state, "discord_stopping", False)
    incoming = message("!info 123456789012345678", admin=True)
    info = AsyncMock()
    monkeypatch.setattr(handlers, "send_discord_info_message", info)
    assert await handlers._handle_discord_admin_command(incoming)
    info.assert_awaited_once_with(incoming, "123456789012345678")
    request = SimpleNamespace(guild=None, user=incoming.author)
    native_client = client._create_client()
    try:
        assert await native_client._connection._command_tree.interaction_check(request)
    finally:
        state.discord_message_tasks.discard(asyncio.current_task())
        await native_client.close()
