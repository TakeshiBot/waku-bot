"""Run the real Telegram tool and output flow without Telegram or model requests."""

import ast
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pydantic_ai
import pyrogram
import pytest
import telegramify_markdown
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

ROOT = Path(__file__).resolve().parents[1]


def load_definitions(relative_path, names, namespace):
    """Exclude module imports that initialize the bot, DB, and model providers."""
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    definitions = []
    for node in tree.body:
        node_names = {getattr(node, "name", None)}
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            node_names |= {
                target.id for target in targets if isinstance(target, ast.Name)
            }
        if node_names & names:
            definitions.append(node)
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[future, *definitions], type_ignores=[])
    )
    exec(compile(module, relative_path, "exec"), namespace)


@pytest.fixture
def delivery():
    def convert_md(text):
        return telegramify_markdown.convert(text)[0], []

    namespace = {
        "asyncio": asyncio,
        "datetime": datetime,
        "pyrogram": pyrogram,
        "pydantic_ai": pydantic_ai,
        "dataclass": dataclass,
        "field": field,
        "convert_md": convert_md,
        "convert_md_chunks": lambda text: [convert_md(text)],
        "logger": MagicMock(),
        "memttlcache": SimpleNamespace(
            get=AsyncMock(return_value=None), set=AsyncMock()
        ),
        "GROUP_CHAT_TYPES": {
            pyrogram.enums.ChatType.SUPERGROUP,
            pyrogram.enums.ChatType.GROUP,
        },
        "_rich_output_enabled": AsyncMock(return_value=False),
        "app_config": SimpleNamespace(
            agent_streaming_max_time=300,
            agent_multimodal_mode="native",
            agent_model_options={},
            cachettl_agent_history=300,
        ),
    }
    load_definitions("waku/plugins/agent/datatype.py", {"ContextDeps"}, namespace)
    namespace["datatype"] = SimpleNamespace(
        ContextDeps=namespace["ContextDeps"], BotLastReply=SimpleNamespace
    )
    namespace["message_plain_text"] = lambda message: message.text
    namespace["state"] = SimpleNamespace(
        bot_last_reply_key=lambda chat: f"reply:{chat}"
    )
    load_definitions(
        "waku/plugins/agent/output.py",
        {
            "_text_key",
            "text_already_sent",
            "record_sent_text",
            "record_tool_reply",
            "_send_plain_reply",
            "reply_output",
            "StreamingOutput",
        },
        namespace,
    )
    namespace.update(tr=MagicMock(side_effect=lambda key, **kwargs: key))
    load_definitions(
        "waku/plugins/agent/tools/tg_ops.py",
        {"_METHODS", "_MEDIA_FIELDS", "_WAKU_EXTENSIONS", "_convert_params", "tg"},
        namespace,
    )
    client = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(id=10, text="Hello"))
    )
    message = SimpleNamespace(
        id=1,
        chat=SimpleNamespace(id=2, type=pyrogram.enums.ChatType.PRIVATE),
        from_user=SimpleNamespace(id=3),
        sender_chat=None,
        reply_text=AsyncMock(return_value=SimpleNamespace(id=11)),
    )
    deps = namespace["ContextDeps"](
        client=client, user_id=3, chat_id=2, message=message
    )
    return SimpleNamespace(
        namespace=namespace,
        client=client,
        message=message,
        deps=deps,
        ctx=SimpleNamespace(deps=deps),
    )


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize(
    "output,failed,expected_replies",
    [
        ("Hello", False, 0),
        ("A different follow-up", False, 1),
        ("Hello", True, 1),
        ("**Hello**", False, 0),
    ],
)
async def test_tool_send_and_final_output_deliver_only_once(
    delivery,
    streaming,
    output,
    failed,
    expected_replies,
    use_tool=True,
    tool_text="Hello",
):
    """A successful tool send suppresses its echo; new text and failures survive."""
    namespace = delivery.namespace
    if failed:
        delivery.client.send_message.side_effect = RuntimeError("offline")

    class ModelNode:
        def __init__(self, text=""):
            self.text = text

        @asynccontextmanager
        async def stream(self, _ctx):
            async def events():
                if self.text:
                    yield PartStartEvent(index=0, part=TextPart(self.text[:2]))
                    yield PartDeltaEvent(index=0, delta=TextPartDelta(self.text[2:]))

            yield events()

    class ToolsNode:
        def __init__(self, tool=False):
            self.tool = tool
            self.model_response = SimpleNamespace(
                parts=[SimpleNamespace(part_kind="tool-call")]
                if tool
                else [TextPart(output)]
            )

    end = object()
    nodes = (
        [ModelNode(), ToolsNode(tool=True), ModelNode(output), ToolsNode(), end]
        if use_tool
        else [ModelNode(output), ToolsNode(), end]
    )

    class AgentRun:
        def __init__(self):
            self.next_node = nodes[0]
            self.ctx = None
            self.result = None
            self.usage = SimpleNamespace()

        async def next(self, node):
            if isinstance(node, ToolsNode) and node.tool:
                await namespace["tg"](delivery.ctx, "sendMessage", {"text": tool_text})
            next_node = nodes[nodes.index(node) + 1]
            if next_node is end:
                self.result = SimpleNamespace(output=output)
            return next_node

        def all_messages(self):
            return []

    @asynccontextmanager
    async def fake_iter(*_args, **_kwargs):
        yield AgentRun()

    namespace["app_config"].agent_streaming = streaming
    namespace.update(
        Agent=SimpleNamespace(
            is_end_node=lambda node: node is end,
            is_model_request_node=lambda node: isinstance(node, ModelNode),
            is_call_tools_node=lambda node: isinstance(node, ToolsNode),
        ),
        PartStartEvent=PartStartEvent,
        PartDeltaEvent=PartDeltaEvent,
        TextPart=TextPart,
        TextPartDelta=TextPartDelta,
        EndTurn=type("EndTurn", (), {}),
        AskUserOutput=type("AskUserOutput", (), {}),
        is_chat_allowed=lambda _chat: True,
        check_needs_multimodal=lambda *_args: False,
        get_chat_model_override=AsyncMock(return_value=None),
        provider=SimpleNamespace(make_model_settings=lambda _options: None),
        safety=SimpleNamespace(build_usage_limits=lambda: None),
        quota=SimpleNamespace(settle=AsyncMock()),
        trace=SimpleNamespace(mark_trace=MagicMock()),
        state=SimpleNamespace(history_key=lambda *_args: "history"),
        log_run_cache_stats=MagicMock(),
        _iter_with_spill_session=fake_iter,
    )
    load_definitions("waku/plugins/agent/runner.py", {"_run_agent_impl"}, namespace)
    await namespace["_run_agent_impl"](
        agi=None,
        client=delivery.client,
        message=delivery.message,
        user_id=3,
        chat_id=2,
        user_prompt=["Hi"],
        history=[],
        deps=delivery.deps,
        multimodal_model=None,
        model=SimpleNamespace(model_name="fake"),
        lang="vi",
        subject=None,
        typing_keepalive=SimpleNamespace(),
    )
    assert delivery.client.send_message.await_count == int(use_tool)
    assert delivery.message.reply_text.await_count == expected_replies
    assert all(
        "error" not in call.kwargs
        for call in namespace["trace"].mark_trace.call_args_list
    )
    namespace["quota"].settle.assert_awaited_once()


@pytest.mark.parametrize("streaming", [True, False])
async def test_normal_reply_without_tools_is_delivered(delivery, streaming):
    await test_tool_send_and_final_output_deliver_only_once(
        delivery, streaming, "Hello", False, 1, use_tool=False
    )


@pytest.mark.parametrize("streaming", [True, False])
async def test_html_tool_and_markdown_final_use_returned_plain_text(
    delivery, streaming
):
    await test_tool_send_and_final_output_deliver_only_once(
        delivery, streaming, "**Hello**", False, 0, tool_text="<b>Hello</b>"
    )


async def test_repeated_send_tool_is_deduplicated_but_other_target_is_allowed(delivery):
    tg = delivery.namespace["tg"]
    await tg(delivery.ctx, "sendMessage", {"text": "Hello"})
    delivery.namespace["tr"].assert_any_call(
        "tool_p0_sent_message_id_p1", p0="sendMessage", p1=10
    )
    await tg(delivery.ctx, "sendMessage", {"text": "Hello"})
    assert delivery.client.send_message.await_count == 1
    await tg(delivery.ctx, "sendMessage", {"text": "Hello", "reply_to_message_id": 99})
    assert delivery.client.send_message.await_count == 2


async def test_failed_send_is_not_recorded_and_chat_override_is_rejected(delivery):
    delivery.client.send_message.side_effect = RuntimeError("offline")
    tg = delivery.namespace["tg"]
    await tg(delivery.ctx, "sendMessage", {"text": "Hello"})
    assert not delivery.deps.sent_texts
    await tg(delivery.ctx, "sendMessage", {"chat_id": 99, "text": "Hello"})
    assert delivery.client.send_message.await_count == 1


def test_delivery_tracking_is_per_turn_and_preserves_different_text(delivery):
    record = delivery.namespace["record_sent_text"]
    duplicate = delivery.namespace["text_already_sent"]
    record(delivery.deps, " Hello\r\nthere ")
    assert duplicate(delivery.deps, "Hello\nthere")
    assert not duplicate(delivery.deps, "Hello there!")
    fresh = delivery.namespace["ContextDeps"](
        client=delivery.client, user_id=3, chat_id=2, message=delivery.message
    )
    assert not duplicate(fresh, "Hello\nthere")


async def test_tool_only_reply_preserves_group_follow_up_context(delivery):
    delivery.message.chat.type = pyrogram.enums.ChatType.SUPERGROUP
    delivery.message.text = "Hi"
    await delivery.namespace["tg"](delivery.ctx, "sendMessage", {"text": "Hello"})
    cache = delivery.namespace["memttlcache"].set
    cache.assert_awaited_once()
    key, reply = cache.await_args.args
    assert key == "reply:2"
    assert reply.message_id == 10
    assert reply.reply_text == "Hello"
    assert reply.reply_to_message_id == 1
    assert reply.original_user_message == "Hi"
