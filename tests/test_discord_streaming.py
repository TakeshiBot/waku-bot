"""Native Discord HTTP payloads and PydanticAI streaming, without network IO."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from test_discord_reply_delivery import delivery as delivery
from test_discord_reply_delivery import raw_message

from waku.discordbot import agent, state, streaming
from waku.discordbot.models import DiscordContextDeps


@pytest.fixture
def wire(delivery, monkeypatch):
    edits = []
    updated = asyncio.Event()

    async def edit(channel_id, message_id, *, params):
        assert channel_id == 20 and message_id == 201
        edits.append(params.payload)
        updated.set()
        return raw_message(message_id, bot=True, content=params.payload["content"])

    monkeypatch.setattr(
        delivery.sdk_state.http, "edit_message", AsyncMock(side_effect=edit)
    )
    monkeypatch.setattr(streaming.DiscordReplyStream, "UPDATE_INTERVAL", 0.005)
    monkeypatch.setattr(agent.app_config, "agent_streaming", True)
    monkeypatch.setattr(agent.app_config, "agent_run_timeout", 3)
    monkeypatch.setattr(agent, "_prepare_discord_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent.common.memttlcache, "set", AsyncMock())
    return SimpleNamespace(delivery=delivery, edits=edits, updated=updated)


@pytest.mark.asyncio
async def test_stream_edits_one_reply_before_model_finishes_and_caches_final_history(
    wire, monkeypatch
):
    async def respond(messages, info):
        yield "Xin"
        yield " chào"
        await asyncio.wait_for(wire.updated.wait(), 1)
        assert wire.edits[-1]["content"] == "Xin chào"
        yield "!"

    monkeypatch.setattr(
        state,
        "discord_agent",
        Agent(FunctionModel(stream_function=respond), deps_type=DiscordContextDeps),
    )
    await agent._run_discord_agent_once(
        wire.delivery.source, ["hello"], "history", [], None
    )
    assert len(wire.delivery.payloads) == 1
    assert wire.delivery.payloads[0]["content"] == "Xin"
    assert wire.delivery.payloads[0]["message_reference"]["message_id"] == 101
    assert wire.delivery.payloads[0]["allowed_mentions"]["parse"] == []
    assert wire.edits[-1]["content"] == "Xin chào!"
    assert wire.edits[-1]["allowed_mentions"]["parse"] == ["users"]
    cached = agent.common.memttlcache.set.await_args.args[1]
    assert cached[-1].parts[0].content == "Xin chào!"


@pytest.mark.asyncio
async def test_tool_round_does_not_duplicate_preamble_or_run_tool_twice(
    wire, monkeypatch
):
    calls = []

    async def lookup():
        calls.append("lookup")
        return "done"

    async def respond(messages, info):
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        ):
            yield "Kết quả cuối."
        else:
            yield "Để mình kiểm tra."
            yield {
                0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="lookup-1")
            }

    monkeypatch.setattr(
        state,
        "discord_agent",
        Agent(
            FunctionModel(stream_function=respond),
            tools=[lookup],
            deps_type=DiscordContextDeps,
        ),
    )
    await agent._run_discord_agent_once(
        wire.delivery.source, ["hello"], "history", [], None
    )
    assert calls == ["lookup"]
    assert len(wire.delivery.payloads) == 1
    assert wire.edits[-1]["content"] == "Kết quả cuối."


@pytest.mark.asyncio
async def test_stream_failure_keeps_one_reply_and_prevents_model_retry(
    wire, monkeypatch
):
    async def respond(messages, info):
        yield "Đang trả lời"
        raise TimeoutError("synthetic provider error")

    monkeypatch.setattr(
        state,
        "discord_agent",
        Agent(FunctionModel(stream_function=respond), deps_type=DiscordContextDeps),
    )
    with pytest.raises(agent.DiscordPostRunError):
        await agent._run_discord_agent_once(
            wire.delivery.source, ["hello"], "history", [], None
        )
    assert len(wire.delivery.payloads) == 1
    assert "Đang trả lời" in wire.edits[-1]["content"]
    agent.common.memttlcache.set.assert_not_awaited()
    assert not any(
        task.get_name() == "discord-reply-stream" and not task.done()
        for task in asyncio.all_tasks()
    )


@pytest.mark.asyncio
async def test_long_final_reply_splits_without_resending_the_first_chunk(
    wire, monkeypatch
):
    async def pause(delay):
        pass

    monkeypatch.setattr(
        streaming,
        "asyncio",
        SimpleNamespace(
            create_task=asyncio.create_task,
            sleep=pause,
            CancelledError=asyncio.CancelledError,
        ),
    )
    deps = DiscordContextDeps(wire.delivery.source)
    stream = streaming.DiscordReplyStream(wire.delivery.source, deps)
    text = "x" * 5000
    async with wire.delivery.source.channel.typing():
        await stream.update("x")
    await stream.finalize(text)
    assert len(wire.delivery.payloads) == 3
    assert wire.delivery.payloads[0]["content"] == "x"
    assert (
        wire.edits[-1]["content"]
        + "".join(payload["content"] for payload in wire.delivery.payloads[1:])
        == text
    )
    assert all(len(payload["content"]) <= 1900 for payload in wire.delivery.payloads)
    assert all(
        "message_reference" not in payload for payload in wire.delivery.payloads[1:]
    )
    await stream.finalize(text)
    assert len(wire.delivery.payloads) == 3


@pytest.mark.asyncio
async def test_uncertain_first_preview_send_is_never_retried(wire, monkeypatch):
    wire.delivery.send.side_effect = OSError("synthetic lost send ACK")

    async def respond(messages, info):
        yield "Partial answer"

    monkeypatch.setattr(
        state,
        "discord_agent",
        Agent(FunctionModel(stream_function=respond), deps_type=DiscordContextDeps),
    )
    with pytest.raises(agent.DiscordPostRunError):
        await agent._run_discord_agent_once(
            wire.delivery.source, ["hello"], "history", [], None
        )
    wire.delivery.send.assert_awaited_once()
    agent.common.memttlcache.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_stops_preview_updates(wire, monkeypatch):
    preview_started = asyncio.Event()

    async def respond(messages, info):
        yield "Một phần"
        preview_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        state,
        "discord_agent",
        Agent(FunctionModel(stream_function=respond), deps_type=DiscordContextDeps),
    )
    task = asyncio.create_task(
        agent._run_discord_agent_once(
            wire.delivery.source, ["hello"], "history", [], None
        )
    )
    await asyncio.wait_for(preview_started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(wire.delivery.payloads) == 1
    assert not any(
        task.get_name() == "discord-reply-stream" and not task.done()
        for task in asyncio.all_tasks()
    )


@pytest.mark.asyncio
async def test_first_response_deadline_cancels_a_provider_without_text(
    wire, monkeypatch
):
    cancelled = asyncio.Event()
    monkeypatch.setattr(agent.app_config, "agent_model_timeout", 0.02)

    async def respond(messages, info):
        try:
            await asyncio.Event().wait()
            yield "Unreachable"
        finally:
            cancelled.set()

    monkeypatch.setattr(
        state,
        "discord_agent",
        Agent(FunctionModel(stream_function=respond), deps_type=DiscordContextDeps),
    )
    with pytest.raises(TimeoutError):
        await agent._run_discord_agent_once(
            wire.delivery.source, ["hello"], "history", [], None
        )
    assert cancelled.is_set()
    assert not wire.delivery.payloads and not wire.edits
    agent.common.memttlcache.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_progress_preserves_the_longer_run_budget(wire, monkeypatch):
    monkeypatch.setattr(agent.app_config, "agent_model_timeout", 0.06)
    calls = []

    async def lookup():
        calls.append("lookup")
        await asyncio.sleep(0.12)
        return "done"

    async def respond(messages, info):
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        ):
            yield "Đã xong."
        else:
            yield {
                0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="lookup-1")
            }

    monkeypatch.setattr(
        state,
        "discord_agent",
        Agent(
            FunctionModel(stream_function=respond),
            tools=[lookup],
            deps_type=DiscordContextDeps,
        ),
    )
    await agent._run_discord_agent_once(
        wire.delivery.source, ["hello"], "history", [], None
    )
    assert calls == ["lookup"]
    assert len(wire.delivery.payloads) == 1
    assert wire.edits[-1]["content"] == "Đã xong."
