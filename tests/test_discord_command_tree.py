import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord import app_commands

from waku.discordbot import client, state


def interaction(done=False):
    return SimpleNamespace(
        guild=SimpleNamespace(id=10),
        response=SimpleNamespace(
            is_done=lambda: done, send_message=AsyncMock(),
        ),
        edit_original_response=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_restart_rejects_slash_command_with_private_embed(monkeypatch):
    monkeypatch.setattr(state, "discord_stopping", True)
    native_client = client._create_client()
    tree = native_client._connection._command_tree
    request = interaction()
    assert not await tree.interaction_check(request)
    payload = request.response.send_message.await_args.kwargs
    assert payload["ephemeral"]
    assert isinstance(payload["embed"], discord.Embed)
    await native_client.close()


@pytest.mark.asyncio
async def test_slash_tasks_are_tracked_until_done_for_shutdown(monkeypatch):
    monkeypatch.setattr(state, "discord_stopping", False)
    monkeypatch.setattr(state, "discord_message_tasks", set())
    native_client = client._create_client()
    tree = native_client._connection._command_tree
    release = asyncio.Event()
    checked = asyncio.Event()

    async def callback():
        assert await tree.interaction_check(interaction())
        checked.set()
        await release.wait()

    task = asyncio.create_task(callback())
    try:
        await checked.wait()
        assert task in state.discord_message_tasks
        release.set()
        await task
        await asyncio.sleep(0)
        assert task not in state.discord_message_tasks
    finally:
        release.set()
        await task
        await native_client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("done", [True, False])
async def test_slash_errors_preserve_private_response_and_do_not_expose_details(monkeypatch, done):
    warnings = []
    monkeypatch.setattr(client.logger, "warning", lambda *args: warnings.append(args))
    native_client = client._create_client()
    tree = native_client._connection._command_tree
    request = interaction(done)
    error = app_commands.AppCommandError("secret-token-do-not-expose")
    await tree.on_error(request, error)
    if done:
        request.response.send_message.assert_not_awaited()
        payload = request.edit_original_response.await_args.kwargs
        assert payload["content"] is None
        assert payload["view"] is None
    else:
        request.edit_original_response.assert_not_awaited()
        payload = request.response.send_message.await_args.kwargs
        assert payload["ephemeral"]
    assert isinstance(payload["embed"], discord.Embed)
    assert "secret-token" not in str(payload["embed"].to_dict())
    assert "secret-token" not in str(warnings)
    await native_client.close()
