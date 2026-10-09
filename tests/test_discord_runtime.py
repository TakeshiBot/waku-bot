import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from waku.discordbot import runtime, state


def test_discord_dm_uses_personal_prompt_and_guild_uses_group_prompt(monkeypatch):
    monkeypatch.setattr(runtime.app_config, "agent_prompt", "DM persona")
    monkeypatch.setattr(runtime.app_config, "agent_group_prompt", "Group persona")

    assert runtime._discord_persona_prompt(SimpleNamespace(guild=None)) == "DM persona"
    assert runtime._discord_persona_prompt(SimpleNamespace(guild=object())) == "Group persona"


@pytest.mark.asyncio
async def test_discord_start_is_idempotent_and_stop_resets_runtime(monkeypatch):
    stopped = asyncio.Event()
    clients = []

    class FakeClient:
        user = None

        async def start(self, token):
            assert token == "test-token"
            await stopped.wait()

        async def close(self):
            stopped.set()

        def is_ready(self):
            return False

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def instructions(self, callback):
            return callback

    def create_client():
        client = FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr(runtime.app_config, "discord_enabled", True)
    monkeypatch.setattr(runtime.app_config, "discord_token", "test-token")
    monkeypatch.setattr(runtime.app_config, "agent", True)
    monkeypatch.setattr(runtime.app_config, "agent_model", "test/model")
    monkeypatch.setattr(runtime.provider, "make_chat_model", lambda _: "model")
    monkeypatch.setattr(runtime, "Agent", FakeAgent)
    monkeypatch.setattr(runtime, "Tool", lambda func, **kwargs: func)
    monkeypatch.setattr(runtime, "_create_client", create_client)
    monkeypatch.setattr(state, "discord_task", None)
    monkeypatch.setattr(state, "discord_client", None)

    try:
        await runtime.start_discord_bot()
        first_task = state.discord_task
        await runtime.start_discord_bot()
        assert len(clients) == 1
        assert state.discord_task is first_task

        state.server_list_view_registered = True
        await runtime.stop_discord_bot()
        assert state.discord_task is None
        assert state.discord_client is None
        assert state.discord_agent is None
        assert state.discord_recovery_agent is None
        assert state.server_list_view_registered is False
    finally:
        if state.discord_task is not None or state.discord_client is not None:
            await runtime.stop_discord_bot()


@pytest.mark.asyncio
async def test_discord_stop_cleans_up_after_client_close_error(monkeypatch):
    class FakeClient:
        async def close(self):
            raise RuntimeError("connection already closed")

    task = asyncio.create_task(asyncio.sleep(0))
    await task
    monkeypatch.setattr(state, "discord_client", FakeClient())
    monkeypatch.setattr(state, "discord_task", task)
    monkeypatch.setattr(state, "discord_agent", SimpleNamespace())
    monkeypatch.setattr(state, "discord_recovery_agent", SimpleNamespace())

    await runtime.stop_discord_bot()

    assert state.discord_client is None
    assert state.discord_task is None
    assert state.discord_agent is None
    assert state.discord_recovery_agent is None


def test_disabled_package_never_imports_discord_runtime_or_agents():
    code = """
import asyncio, sys
from types import SimpleNamespace
sys.modules['waku.config'] = SimpleNamespace(app_config=SimpleNamespace(discord_enabled=False))
from waku.discordbot import start_discord_bot, stop_discord_bot, get_discord_runtime_status
asyncio.run(start_discord_bot())
asyncio.run(stop_discord_bot())
assert get_discord_runtime_status() == 'offline'
assert 'discord' not in sys.modules
assert 'waku.discordbot.runtime' not in sys.modules
assert 'waku.plugins.agent.agent' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_discord_model_setup_failure_is_contained_without_secrets(monkeypatch):
    errors = []
    monkeypatch.setattr(runtime.app_config, "discord_enabled", True)
    monkeypatch.setattr(runtime.app_config, "discord_token", "private-test-token")
    monkeypatch.setattr(runtime.app_config, "agent", True)
    monkeypatch.setattr(runtime.app_config, "agent_model", "default/model")
    monkeypatch.setattr(runtime.logger, "error", errors.append)
    monkeypatch.setattr(state, "discord_client", None)
    monkeypatch.setattr(state, "discord_task", None)

    def fail_model(_):
        raise RuntimeError("private-test-token should never reach a log")

    monkeypatch.setattr(runtime.provider, "make_chat_model", fail_model)
    await runtime.start_discord_bot()
    assert state.discord_client is None
    assert state.discord_task is None
    assert errors == ["Discord startup failed: RuntimeError"]


@pytest.mark.asyncio
async def test_async_connection_failure_closes_client_without_secrets(monkeypatch):
    errors = []
    closed = asyncio.Event()

    class FakeClient:
        async def start(self, token):
            raise RuntimeError("private-test-token")

        async def close(self):
            closed.set()

    monkeypatch.setattr(runtime.logger, "error", errors.append)
    task = asyncio.create_task(runtime._run_discord_client(FakeClient(), "private-test-token"))
    await asyncio.gather(task, return_exceptions=True)
    runtime._log_discord_task_result(task)
    assert closed.is_set()
    assert errors == ["Discord client stopped unexpectedly: RuntimeError"]


@pytest.mark.asyncio
async def test_shutdown_cancels_message_handlers_before_closing_client(monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_message():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    class FakeClient:
        async def close(self):
            assert cancelled.is_set()

    task = asyncio.create_task(pending_message())
    await entered.wait()
    monkeypatch.setattr(state, "discord_message_tasks", {task})
    monkeypatch.setattr(state, "discord_task", None)
    monkeypatch.setattr(state, "discord_client", FakeClient())
    await runtime.stop_discord_bot()
    assert task.cancelled()
    assert not state.discord_message_tasks


@pytest.mark.asyncio
async def test_real_discord_client_agent_schema_and_persistent_views_start_offline(monkeypatch):
    """Exercise native SDK setup while replacing only the model and network entry."""
    import discord
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from waku.discordbot.views.authorization import (
        DiscordAuthorizationRequestView,
        DiscordAuthorizationReviewView,
    )
    from waku.discordbot.views.server_list import DiscordServerListView

    connected = asyncio.Event()
    release = asyncio.Event()

    async def offline_start(self, token, **kwargs):
        assert token == "offline-discord-token"
        connected.set()
        await release.wait()

    monkeypatch.setattr(runtime.app_config, "discord_enabled", True)
    monkeypatch.setattr(runtime.app_config, "discord_token", "offline-discord-token")
    monkeypatch.setattr(runtime.app_config, "agent", True)
    monkeypatch.setattr(runtime.app_config, "agent_model", "default/offline-model")
    monkeypatch.setattr(runtime.provider, "make_chat_model", lambda _: TestModel())
    monkeypatch.setattr(discord.Client, "start", offline_start)
    monkeypatch.setattr(state, "discord_client", None)
    monkeypatch.setattr(state, "discord_task", None)
    monkeypatch.setattr(state, "discord_message_tasks", set())

    try:
        await runtime.start_discord_bot()
        await asyncio.wait_for(connected.wait(), timeout=1)
        native_client = state.discord_client
        assert isinstance(native_client, discord.Client)
        assert isinstance(state.discord_agent, Agent)
        assert isinstance(state.discord_recovery_agent, Agent)
        assert set(state.discord_agent._function_toolset.tools) == {
            "get_discord_server_info", "find_discord_channel", "find_discord_user",
            "mention_discord_user", "search_discord_messages", "search_discord_group_memory",
            "update_discord_group_memory", "send_discord_reaction", "send_discord_web_image",
            "send_discord_anime_photo", "schedule_discord_message", "schedule_discord_image_action",
            "list_discord_scheduled_messages", "cancel_discord_scheduled_message",
        }
        command_tree = native_client._connection._command_tree
        assert isinstance(command_tree, discord.app_commands.CommandTree)
        assert {command.name for command in command_tree.get_commands()} == {"seg", "bc"}
        for view_type in (
            DiscordServerListView,
            DiscordAuthorizationRequestView,
            DiscordAuthorizationReviewView,
        ):
            view = view_type()
            assert view.is_persistent()
            native_client.add_view(view)
        assert len(native_client.persistent_views) == 3
        release.set()
        await runtime.stop_discord_bot()
        assert native_client.is_closed()
        assert state.discord_client is None
        assert state.discord_task is None
        assert state.discord_agent is None
        assert state.discord_recovery_agent is None
    finally:
        release.set()
        await runtime.stop_discord_bot()
