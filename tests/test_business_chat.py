"""Business handler/connection regressions using real SDK objects and offline AI."""

import ast
import asyncio
import sys
import time
import weakref
from collections import OrderedDict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits
from pyrogram import Client, enums, filters
from pyrogram.handlers import (
    BusinessConnectionHandler,
    BusinessMessageHandler,
    MessageHandler,
)
from pyrogram.types import BusinessBotRights, BusinessConnection, Chat, Message, User

ROOT = Path(__file__).resolve().parents[1]


def message(
    connection_id="connection-a",
    *,
    chat_id=123,
    message_id=1,
    sender_id=123,
    outgoing=False,
    text="Chào Waku",
):
    return Message(
        id=message_id,
        chat=Chat(id=chat_id, type=enums.ChatType.PRIVATE),
        from_user=User(id=sender_id, first_name="Customer", is_bot=False),
        text=text,
        outgoing=outgoing,
        business_connection_id=connection_id,
    )


def connection(
    connection_id="connection-a", *, can_reply=True, enabled=True, owner_id=456
):
    return BusinessConnection(
        id=connection_id,
        user=User(id=owner_id, first_name="Owner"),
        dc_id=2,
        date=datetime.now(UTC),
        is_enabled=enabled,
        rights=BusinessBotRights(can_reply=can_reply),
    )


@pytest.fixture
def business():
    cache = {}

    async def get(key, default=None):
        return cache.get(key, default)

    async def set_value(key, value, ttl=0):
        cache[key] = value

    config = SimpleNamespace(
        business_chat_enabled=True,
        agent=True,
        agent_model="test/model",
        agent_streaming=False,
        agent_run_timeout=1,
        cachettl_agent_history=60,
        agent_model_options={"temperature": 0.4},
        agent_prompt="Be helpful",
        agent_secret_masking=False,
        lang="vi",
    )
    namespace = {
        "__name__": "__main__",
        "asyncio": asyncio,
        "time": time,
        "weakref": weakref,
        "OrderedDict": OrderedDict,
        "replace": replace,
        "Client": Client,
        "enums": enums,
        "filters": filters,
        "app_config": config,
        "logger": Mock(),
        "memttlcache": SimpleNamespace(
            get=AsyncMock(side_effect=get), set=AsyncMock(side_effect=set_value)
        ),
    }
    tree = ast.parse(
        (ROOT / "waku/plugins/business_chat.py").read_text(encoding="utf-8")
    )
    definitions = [
        node
        for node in tree.body
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign, ast.AnnAssign)
        )
    ]
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[future, *definitions], type_ignores=[])
            ),
            "business-chat",
            "exec",
        ),
        namespace,
    )

    async def run(prompt, *, message_history, usage_limits, usage):
        assert usage_limits.request_limit == 2
        return SimpleNamespace(
            output="Ừm, chào nha.",
            all_messages=lambda: [
                *message_history,
                ModelRequest(parts=[UserPromptPart(prompt)]),
                ModelResponse(parts=[TextPart("Ừm, chào nha.")]),
            ],
        )

    async def compact(history, _agent, _usage):
        return list(history)

    agent = SimpleNamespace(run=AsyncMock(side_effect=run))
    namespace["_make_business_agent"] = Mock(return_value=agent)
    namespace["_compact_business_history"] = AsyncMock(side_effect=compact)
    namespace["_usage_limits"] = Mock(return_value=UsageLimits(request_limit=2))
    namespace["_send_business_reply"] = AsyncMock(return_value=True)
    outputs = []

    class Output:
        def __init__(self, client, incoming, revision):
            self.current_text = ""
            self.appended = []
            self.should_send = lambda: namespace["_still_authorized"](
                incoming, revision
            )
            self.finalize = AsyncMock(return_value=True)
            self.abort = AsyncMock()
            outputs.append(self)

        async def append_delta(self, delta):
            self.appended.append(delta)
            self.current_text += delta

    namespace["_new_streaming_output"] = Output
    client = SimpleNamespace(
        get_business_connection=AsyncMock(return_value=connection())
    )
    return SimpleNamespace(
        ns=namespace,
        config=config,
        cache=cache,
        client=client,
        agent=agent,
        outputs=outputs,
        source_tree=tree,
    )


async def test_replies_keep_history_and_exact_connection_chat_isolation(business):
    handler = business.ns["business_chat_message"]
    await handler(business.client, message())
    await handler(business.client, message(message_id=2))
    await handler(business.client, message(chat_id=124))
    business.client.get_business_connection.return_value = connection("connection-b")
    await handler(business.client, message("connection-b"))
    assert business.ns["_send_business_reply"].await_count == 4
    calls = business.agent.run.await_args_list
    assert calls[0].kwargs["message_history"] == []
    assert len(calls[1].kwargs["message_history"]) == 2
    assert calls[2].kwargs["message_history"] == []
    assert calls[3].kwargs["message_history"] == []
    assert (
        calls[0].kwargs["usage"]
        is business.ns["_compact_business_history"].await_args_list[0].args[2]
    )
    assert set(business.cache) == {
        "business_chat_history:connection-a:123",
        "business_chat_history:connection-a:124",
        "business_chat_history:connection-b:123",
    }
    assert business.client.get_business_connection.await_count == 2


@pytest.mark.parametrize(
    "kind",
    [
        "outgoing",
        "disabled",
        "agent_disabled",
        "owner",
        "bot",
        "command",
        "blank",
        "group",
        "ordinary",
    ],
)
async def test_ignores_messages_outside_authorized_business_scope(business, kind):
    incoming = message()
    if kind == "outgoing":
        incoming.outgoing = True
    elif kind == "disabled":
        business.config.business_chat_enabled = False
    elif kind == "agent_disabled":
        business.config.agent = False
    elif kind == "owner":
        incoming.from_user.id = 456
    elif kind == "bot":
        incoming.from_user.is_bot = True
    elif kind == "command":
        incoming.text = " /config"
    elif kind == "blank":
        incoming.text = "  "
    elif kind == "group":
        incoming.chat.type = enums.ChatType.GROUP
    elif kind == "ordinary":
        incoming.business_connection_id = None
    await business.ns["business_chat_message"](business.client, incoming)
    business.agent.run.assert_not_awaited()
    business.ns["_send_business_reply"].assert_not_awaited()


@pytest.mark.parametrize("enabled,can_reply", [(False, True), (True, False)])
async def test_requires_connection_enable_and_reply_rights(
    business, enabled, can_reply
):
    business.client.get_business_connection.return_value = connection(
        enabled=enabled, can_reply=can_reply
    )
    await business.ns["business_chat_message"](business.client, message())
    business.agent.run.assert_not_awaited()


async def test_duplicate_update_runs_and_delivers_once_even_concurrently(business):
    await asyncio.gather(
        *[
            business.ns["business_chat_message"](business.client, message())
            for _ in range(3)
        ]
    )
    business.agent.run.assert_awaited_once()
    business.ns["_send_business_reply"].assert_awaited_once()


async def test_failed_delivery_is_not_cached_or_retried_for_duplicate(business):
    business.ns["_send_business_reply"].return_value = False
    await business.ns["business_chat_message"](business.client, message())
    await business.ns["business_chat_message"](business.client, message())
    assert not business.cache
    business.agent.run.assert_awaited_once()
    business.ns["_send_business_reply"].assert_awaited_once()


async def test_live_disable_during_model_run_suppresses_send_and_history(business):
    async def disable(*_args, **_kwargs):
        business.config.business_chat_enabled = False
        return SimpleNamespace(output="must not send")

    business.agent.run.side_effect = disable
    await business.ns["business_chat_message"](business.client, message())
    business.ns["_send_business_reply"].assert_not_awaited()
    assert not business.cache


async def test_revocation_cancels_child_but_native_handler_parent_stays_alive(business):
    entered = asyncio.Event()
    child_tasks = []

    async def stalled(*_args, **_kwargs):
        child_tasks.append(asyncio.current_task())
        entered.set()
        await asyncio.Event().wait()

    business.agent.run.side_effect = stalled
    handler = business.ns["business_chat_message"].handlers[0][0]
    progressed = asyncio.Event()

    async def dispatcher_worker():
        await handler.callback(business.client, message())
        progressed.set()  # Native dispatcher can process the next update.

    worker = asyncio.create_task(dispatcher_worker())
    await asyncio.wait_for(entered.wait(), 1)
    assert business.ns["_active_tasks"][("connection-a", 123)] is child_tasks[0]
    assert child_tasks[0] is not worker
    await business.ns["business_connection_changed"](
        business.client, connection(can_reply=False)
    )
    await asyncio.wait_for(worker, 1)
    assert progressed.is_set() and not worker.cancelled()
    assert child_tasks[0].cancelled()
    business.ns["_send_business_reply"].assert_not_awaited()
    assert not business.cache and not business.ns["_active_tasks"]


async def test_parent_shutdown_cancellation_propagates_and_cleans_child(business):
    entered = asyncio.Event()

    async def stalled(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    business.agent.run.side_effect = stalled
    worker = asyncio.create_task(
        business.ns["business_chat_message"](business.client, message())
    )
    await asyncio.wait_for(entered.wait(), 1)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert not business.ns["_active_tasks"] and not business.cache


async def test_queued_turn_rechecks_revoked_connection_after_lock(business):
    entered, release = asyncio.Event(), asyncio.Event()

    async def stalled(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(output="must not send")

    business.agent.run.side_effect = stalled
    first = asyncio.create_task(
        business.ns["business_chat_message"](business.client, message())
    )
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(
        business.ns["business_chat_message"](business.client, message(message_id=2))
    )
    await business.ns["business_connection_changed"](
        business.client, connection(can_reply=False)
    )
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    business.agent.run.assert_awaited_once()
    business.ns["_send_business_reply"].assert_not_awaited()


async def test_connection_event_wins_over_stale_connection_fetch(business):
    async def fetch(_connection_id):
        await business.ns["business_connection_changed"](
            business.client, connection(can_reply=False)
        )
        return connection(can_reply=True)

    business.client.get_business_connection.side_effect = fetch
    await business.ns["business_chat_message"](business.client, message())
    business.agent.run.assert_not_awaited()


def stream_agent(business, *, during_delta=None):
    class Result:
        async def stream_text(self, *, delta):
            assert delta
            yield "**Chào"
            if during_delta is not None:
                await during_delta()
            yield " bạn**"

        async def get_output(self):
            return "**Chào bạn**"

        def all_messages(self):
            return [
                ModelRequest(parts=[UserPromptPart("Chào")]),
                ModelResponse(parts=[TextPart("**Chào bạn**")]),
            ]

    class Context:
        async def __aenter__(self):
            return Result()

        async def __aexit__(self, *_args):
            return False

    business.agent.run_stream = Mock(return_value=Context())
    business.config.agent_streaming = True


async def test_stream_deltas_finalize_once_without_second_reply(business):
    stream_agent(business)
    await business.ns["business_chat_message"](business.client, message())
    assert business.outputs[0].appended == ["**Chào", " bạn**"]
    assert business.outputs[0].current_text == "**Chào bạn**"
    business.outputs[0].finalize.assert_awaited_once()
    business.outputs[0].abort.assert_not_awaited()
    business.ns["_send_business_reply"].assert_not_awaited()
    assert len(next(iter(business.cache.values()))) == 2


async def test_stream_live_disable_aborts_and_guard_blocks_background_send(business):
    async def disable():
        business.config.business_chat_enabled = False

    stream_agent(business, during_delta=disable)
    await business.ns["business_chat_message"](business.client, message())
    output = business.outputs[0]
    assert output.appended == ["**Chào"] and not output.should_send()
    output.abort.assert_awaited_once()
    output.finalize.assert_not_awaited()
    business.ns["_send_business_reply"].assert_not_awaited()
    assert not business.cache


async def test_stream_final_delivery_failure_does_not_cache_or_fallback(business):
    stream_agent(business)
    factory = business.ns["_new_streaming_output"]

    def failed(*args):
        output = factory(*args)
        output.finalize.return_value = False
        return output

    business.ns["_new_streaming_output"] = failed
    await business.ns["business_chat_message"](business.client, message())
    business.ns["_send_business_reply"].assert_not_awaited()
    assert not business.cache


async def test_run_deadline_includes_context_compaction(business):
    async def stalled(*_args):
        await asyncio.Event().wait()

    business.config.agent_run_timeout = 0.01
    business.ns["_compact_business_history"].side_effect = stalled
    await business.ns["business_chat_message"](business.client, message())
    business.agent.run.assert_not_awaited()
    assert not business.ns["_active_tasks"]
    assert not business.cache


def test_text_history_drops_tools_media_empty_responses_and_caps_dialog(business):
    original = ModelRequest(
        parts=[
            UserPromptPart(
                ["question", BinaryContent(b"photo", media_type="image/png")]
            )
        ]
    )
    answer = ModelResponse(parts=[TextPart("answer"), ToolCallPart("lookup", {}, "a")])
    cleaned = business.ns["_text_history"](
        [original, answer, ModelResponse(parts=[TextPart(" ")])]
    )
    assert cleaned[0].parts[0].content == "question"
    assert cleaned[1].parts == [answer.parts[0]]
    assert isinstance(original.parts[0].content, list)
    assert len(business.ns["_text_history"]([original, answer] * 30)) == 20


async def test_native_sdk_handlers_are_business_only(business):
    message_handler = business.ns["business_chat_message"].handlers[0][0]
    connection_handler = business.ns["business_connection_changed"].handlers[0][0]
    assert isinstance(message_handler, BusinessMessageHandler)
    assert not isinstance(message_handler, MessageHandler)
    assert isinstance(connection_handler, BusinessConnectionHandler)
    assert await message_handler.check(business.client, message())


def test_no_eager_shared_agent_or_pydantic_imports(business):
    imports = [
        node for node in business.source_tree.body if isinstance(node, ast.ImportFrom)
    ]
    assert not any(
        node.module.startswith(("pydantic_ai", "waku.plugins.agent"))
        for node in imports
    )


async def test_factory_uses_new_provider_options_and_real_offline_agent(
    business, monkeypatch
):
    async def respond(_messages, _info):
        return ModelResponse(parts=[TextPart("offline answer")])

    provider = SimpleNamespace(
        make_chat_model=Mock(return_value=FunctionModel(respond)),
        make_model_settings=Mock(side_effect=lambda options: options),
    )
    monkeypatch.setitem(sys.modules, "waku.plugins.agent.provider", provider)
    monkeypatch.setitem(
        sys.modules,
        "waku.plugins.agent.localization",
        SimpleNamespace(configured_prompt=lambda *_args: "Be helpful"),
    )
    monkeypatch.setitem(
        sys.modules, "waku.plugins.agent.safety", SimpleNamespace(scrub_output=Mock())
    )
    factory_node = next(
        node
        for node in business.source_tree.body
        if getattr(node, "name", None) == "_make_business_agent"
    )
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[factory_node], type_ignores=[])),
            "business-factory",
            "exec",
        ),
        {**business.ns, "Agent": Agent},
        local := {},
    )
    agent = local["_make_business_agent"]()
    business.ns["_make_business_agent"].return_value = agent
    await business.ns["business_chat_message"](business.client, message())
    provider.make_chat_model.assert_called_once_with("test/model")
    provider.make_model_settings.assert_called_once_with({"temperature": 0.4})
    assert business.ns["_send_business_reply"].await_args.args[2] == "offline answer"


async def test_native_offline_agent_stream_finalizes_once(business):
    async def stream(_messages, _info):
        yield "**Chào"
        yield " bạn**"

    business.config.agent_streaming = True
    business.ns["_make_business_agent"].return_value = Agent(
        FunctionModel(stream_function=stream), output_type=str
    )
    await business.ns["business_chat_message"](business.client, message())
    assert business.outputs[0].current_text == "**Chào bạn**"
    business.outputs[0].finalize.assert_awaited_once()
    business.ns["_send_business_reply"].assert_not_awaited()
    assert len(next(iter(business.cache.values()))) == 2


async def test_output_factories_forward_live_guard_for_stream_and_final_reply(
    business, monkeypatch
):
    streaming = Mock()
    reply = AsyncMock(return_value=True)
    monkeypatch.setitem(
        sys.modules,
        "waku.plugins.agent.output",
        SimpleNamespace(StreamingOutput=streaming, reply_output=reply),
    )
    names = {"_new_streaming_output", "_send_business_reply"}
    helpers = [
        node
        for node in business.source_tree.body
        if getattr(node, "name", None) in names
    ]
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    namespace = {**business.ns}
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[future, *helpers], type_ignores=[])
            ),
            "business-output-contract",
            "exec",
        ),
        namespace,
    )
    namespace["_remember_connection"](connection())
    revision = namespace["_connection_revisions"]["connection-a"]
    incoming = message()
    namespace["_new_streaming_output"](business.client, incoming, revision)
    await namespace["_send_business_reply"](
        business.client, incoming, "answer", revision
    )
    stream_guard = streaming.call_args.kwargs["should_send"]
    reply_guard = reply.await_args.kwargs["should_send"]
    assert stream_guard() and reply_guard()
    business.config.business_chat_enabled = False
    assert not stream_guard() and not reply_guard()
