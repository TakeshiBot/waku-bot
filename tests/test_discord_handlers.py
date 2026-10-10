import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from waku.discordbot import handlers, state
from waku.discordbot.agent import DiscordPostRunError


@pytest.fixture(autouse=True)
def global_ai_enabled(monkeypatch):
    monkeypatch.setattr(
        handlers, "_discord_global_ai_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(handlers.app_config, "discord_periodic_reaction_interval", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_error", "expected_reply"),
    [
        (TimeoutError("model timed out"), "Thử lại sau nhé"),
        (DiscordPostRunError("reply failed"), None),
    ],
)
async def test_discord_does_not_retry_failed_or_completed_model_turns(
    monkeypatch, run_error, expected_reply
):
    channel = SimpleNamespace(id=123, name="test", send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(id=987),
        guild=SimpleNamespace(id=10, name="test"),
        channel=channel,
    )
    monkeypatch.setattr(state, "discord_agent", object())
    monkeypatch.setattr(handlers.app_config, "agent_periodic_reaction_interval", 0)
    monkeypatch.setattr(
        handlers, "_history_key", AsyncMock(return_value="discord:test:987")
    )
    monkeypatch.setattr(
        handlers, "_build_prompt", AsyncMock(return_value=(["prompt"], False))
    )
    monkeypatch.setattr(handlers.common.memttlcache, "get", AsyncMock(return_value=[]))
    monkeypatch.setattr(handlers, "_is_discord_history_error", lambda error: False)
    run_once = AsyncMock(side_effect=run_error)
    send_reply = AsyncMock()
    monkeypatch.setattr(handlers, "_send_reply", send_reply)
    recovery = AsyncMock(return_value="Thử lại sau nhé")
    monkeypatch.setattr(handlers, "_run_discord_agent_once", run_once)
    monkeypatch.setattr(handlers, "_discord_recovery_reply", recovery)
    waiting_key = handlers._waiting_key(message.author.id)

    try:
        await handlers._handle_message(message, "hello")
    finally:
        await handlers.common.memstore.delete(waiting_key)

    run_once.assert_awaited_once()
    if expected_reply is None:
        recovery.assert_not_awaited()
        channel.send.assert_not_awaited()
        send_reply.assert_not_awaited()
    else:
        recovery.assert_awaited_once()
        send_reply.assert_awaited_once_with(message, expected_reply)
        channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_discord_serializes_same_user_before_prompt_preparation(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    channel = SimpleNamespace(id=123, name="test", send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(id=988),
        guild=SimpleNamespace(id=10, name="test"),
        channel=channel,
    )

    async def slow_prompt(*args):
        entered.set()
        await release.wait()
        return (["prompt"], False)

    monkeypatch.setattr(state, "discord_agent", object())
    monkeypatch.setattr(handlers.app_config, "agent_periodic_reaction_interval", 0)
    monkeypatch.setattr(
        handlers, "_history_key", AsyncMock(return_value="discord:test:988")
    )
    monkeypatch.setattr(handlers, "_build_prompt", slow_prompt)
    monkeypatch.setattr(handlers.common.memttlcache, "get", AsyncMock(return_value=[]))
    run_once = AsyncMock()
    send_reply = AsyncMock()
    monkeypatch.setattr(handlers, "_send_reply", send_reply)
    monkeypatch.setattr(handlers, "_run_discord_agent_once", run_once)
    waiting_key = handlers._waiting_key(message.author.id)

    first_turn = asyncio.create_task(handlers._handle_message(message, "first"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await handlers._handle_message(message, "second")
        send_reply.assert_not_awaited()
        channel.send.assert_not_awaited()
        assert run_once.await_count == 0
        release.set()
        await asyncio.wait_for(first_turn, timeout=1)
        run_once.assert_awaited_once()
    finally:
        release.set()
        if not first_turn.done():
            first_turn.cancel()
            try:
                await first_turn
            except asyncio.CancelledError:
                pass
        await handlers.common.memstore.delete(waiting_key)


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", ["flag", "finished_task"])
async def test_stale_busy_state_does_not_block_a_new_turn(monkeypatch, stale):
    message = SimpleNamespace(
        author=SimpleNamespace(id=989, bot=False),
        guild=SimpleNamespace(id=10, name="test"),
        channel=SimpleNamespace(id=123, name="test", send=AsyncMock()),
    )
    monkeypatch.setattr(state, "discord_agent", object())
    turn = AsyncMock()
    monkeypatch.setattr(handlers, "_handle_discord_message_turn", turn)
    monkeypatch.setattr(handlers, "_send_reply", AsyncMock())
    waiting_key = handlers._waiting_key(message.author.id)
    owner = True
    if stale == "finished_task":
        owner = asyncio.create_task(asyncio.sleep(0))
        await owner
    await handlers.common.memstore.set(waiting_key, owner)
    await handlers._handle_message(message, "hello")
    turn.assert_awaited_once_with(message, "hello")
    handlers._send_reply.assert_not_awaited()
    assert await handlers.common.memstore.get(waiting_key) is None


@pytest.mark.asyncio
async def test_cancelling_a_turn_releases_its_busy_owner(monkeypatch):
    message = SimpleNamespace(
        author=SimpleNamespace(id=990, bot=False),
        guild=SimpleNamespace(id=10, name="test"),
        channel=SimpleNamespace(id=123, name="test", send=AsyncMock()),
    )
    entered = asyncio.Event()

    async def hold(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(state, "discord_agent", object())
    monkeypatch.setattr(handlers, "_handle_discord_message_turn", hold)
    waiting_key = handlers._waiting_key(message.author.id)
    task = asyncio.create_task(handlers._handle_message(message, "hello"))
    await asyncio.wait_for(entered.wait(), 1)
    assert await handlers.common.memstore.get(waiting_key) is task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await handlers.common.memstore.get(waiting_key) is None
    assert task not in state.discord_ai_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override,legacy,expected",
    [
        (None, 5, [False] * 5 + [True]),
        (0, 5, [False] * 6),
        (2, 0, [False, False, True, False, True, False]),
    ],
)
async def test_discord_reaction_interval_override_zero_and_legacy_inheritance(
    monkeypatch, override, legacy, expected
):
    config = SimpleNamespace(
        discord_periodic_reaction_interval=override,
        agent_periodic_reaction_interval=legacy,
        agent_model_multimodal=None,
    )
    monkeypatch.setattr(handlers, "app_config", config)
    channel = SimpleNamespace(id=123, name="test", send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(id=700),
        guild=SimpleNamespace(id=10, name="test"),
        channel=channel,
    )
    counter = {}

    async def get(key, default=None):
        return counter.get(key, default)

    async def set_value(key, value):
        counter[key] = value

    monkeypatch.setattr(handlers.common.memstore, "get", get)
    monkeypatch.setattr(handlers.common.memstore, "set", set_value)
    monkeypatch.setattr(handlers.common.memttlcache, "get", AsyncMock(return_value=[]))
    monkeypatch.setattr(handlers, "_history_key", AsyncMock(return_value="history"))
    prompts = []

    async def build(_message, prompt):
        prompts.append(prompt)
        return ([prompt], False)

    monkeypatch.setattr(handlers, "_build_prompt", build)
    monkeypatch.setattr(handlers, "_run_discord_agent_once", AsyncMock())
    for _ in expected:
        await handlers._handle_discord_message_turn(message, "hello")
    assert ["Discord reaction nudge" in prompt for prompt in prompts] == expected
    assert handlers._run_discord_agent_once.await_count == len(expected)


@pytest.mark.asyncio
async def test_busy_timeout_uses_shared_reply_helper_before_any_model_run(monkeypatch):
    channel = SimpleNamespace(id=123, name="test", send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(id=701),
        guild=SimpleNamespace(id=10, name="test"),
        channel=channel,
    )
    monkeypatch.setattr(handlers.app_config, "agent_periodic_reaction_interval", 0)
    monkeypatch.setattr(handlers, "_history_key", AsyncMock(return_value="history"))
    monkeypatch.setattr(handlers.common.memttlcache, "get", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        handlers, "_build_prompt", AsyncMock(return_value=(["prompt"], False))
    )

    async def blocked():
        await asyncio.Event().wait()

    gate = SimpleNamespace(acquire=blocked, release=Mock())
    monkeypatch.setattr(handlers, "_discord_agent_gate", lambda: gate)
    monkeypatch.setattr(handlers, "_discord_agent_busy_timeout", lambda: 0.001)
    monkeypatch.setattr(handlers, "_run_discord_agent_once", AsyncMock())
    monkeypatch.setattr(handlers, "_send_reply", AsyncMock())
    await handlers._handle_discord_message_turn(message, "hello")
    handlers._send_reply.assert_awaited_once()
    assert handlers._send_reply.await_args.args[0] is message
    assert handlers._send_reply.await_args.args[1] in handlers._DISCORD_BUSY_REPLIES
    handlers._run_discord_agent_once.assert_not_awaited()
    channel.send.assert_not_awaited()
    gate.release.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_after_one_clean_history_retry_uses_shared_reply_helper(
    monkeypatch,
):
    message = SimpleNamespace(
        author=SimpleNamespace(id=702),
        guild=SimpleNamespace(id=10, name="test"),
        channel=SimpleNamespace(id=123, name="test", send=AsyncMock()),
    )
    monkeypatch.setattr(handlers.app_config, "agent_periodic_reaction_interval", 0)
    monkeypatch.setattr(handlers, "_history_key", AsyncMock(return_value="history"))
    monkeypatch.setattr(handlers.common.memttlcache, "get", AsyncMock(return_value=[]))
    monkeypatch.setattr(handlers.common.memttlcache, "delete", AsyncMock())
    monkeypatch.setattr(
        handlers, "_build_prompt", AsyncMock(return_value=(["prompt"], False))
    )
    first = TypeError("message history broken")
    second = TimeoutError("model timed out")
    monkeypatch.setattr(
        handlers, "_is_discord_history_error", lambda error: error is first
    )
    monkeypatch.setattr(
        handlers, "_run_discord_agent_once", AsyncMock(side_effect=[first, second])
    )
    monkeypatch.setattr(
        handlers, "_discord_recovery_reply", AsyncMock(return_value="Try again")
    )
    monkeypatch.setattr(handlers, "_send_reply", AsyncMock())
    await handlers._handle_discord_message_turn(message, "hello")
    assert handlers._run_discord_agent_once.await_count == 2
    assert handlers._run_discord_agent_once.await_args.args[3] == []
    handlers.common.memttlcache.delete.assert_awaited_once_with("history")
    handlers._send_reply.assert_awaited_once_with(message, "Try again")
    message.channel.send.assert_not_awaited()
