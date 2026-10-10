"""Run the real Telegram tool and output flow without Telegram or model requests."""

import ast
import asyncio
import secrets
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

from waku.plugins.agent import generation, rich_output

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
def delivery(monkeypatch):
    monkeypatch.setattr(generation, "_generations", {})

    def convert_md(text):
        return telegramify_markdown.convert(text)[0], []

    namespace = {
        "asyncio": asyncio,
        "secrets": secrets,
        "generation": generation,
        "OfficialRichDraftStreamer": rich_output.OfficialRichDraftStreamer,
        "send_generation_message": rich_output.send_generation_message,
        "datetime": datetime,
        "pyrogram": pyrogram,
        "pydantic_ai": pydantic_ai,
        "dataclass": dataclass,
        "field": field,
        "convert_md": convert_md,
        "convert_md_chunks": lambda text, *_args: [convert_md(text)],
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
            "DeliveryUncertain",
            "_FORMAT_REJECTIONS",
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
        send_message=AsyncMock(return_value=SimpleNamespace(id=10, text="Hello")),
        resolve_peer=AsyncMock(
            return_value=pyrogram.raw.types.InputPeerUser(user_id=2, access_hash=0)
        ),
        invoke=AsyncMock(
            return_value=pyrogram.raw.types.UpdateShortSentMessage(
                id=11, pts=1, pts_count=1, date=1
            )
        ),
        parser=pyrogram.Client("agent-delivery-parser", in_memory=True).parser,
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
    pretool_text="",
    cancel_mid_stream=False,
    uncertain_delivery=False,
):
    """A successful tool send suppresses its echo; new text and failures survive."""
    namespace = delivery.namespace
    if failed:
        delivery.client.send_message.side_effect = RuntimeError("offline")
    if uncertain_delivery:

        async def invoke(query, **kwargs):
            if isinstance(query, pyrogram.raw.functions.messages.SendMessage):
                raise OSError("Lost delivery ACK")
            return True

        delivery.client.invoke.side_effect = invoke
        if not streaming:
            delivery.message.reply_text.side_effect = [
                OSError("Lost delivery ACK"),
                SimpleNamespace(id=12),
            ]

    class ModelNode:
        def __init__(self, text=""):
            self.text = text

        @asynccontextmanager
        async def stream(self, _ctx):
            async def events():
                if self.text:
                    yield PartStartEvent(index=0, part=TextPart(self.text[:2]))
                    if cancel_mid_stream:
                        raise asyncio.CancelledError
                    yield PartDeltaEvent(index=0, delta=TextPartDelta(self.text[2:]))

            yield events()

    class ToolsNode:
        def __init__(self, tool=False):
            self.tool = tool
            self.model_response = SimpleNamespace(
                parts=[
                    *([TextPart(pretool_text)] if pretool_text else []),
                    SimpleNamespace(part_kind="tool-call"),
                ]
                if tool
                else [TextPart(output)]
            )

    end = object()
    nodes = (
        [
            ModelNode(pretool_text),
            ToolsNode(tool=True),
            ModelNode(output),
            ToolsNode(),
            end,
        ]
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

    # Error delivery uses the separately tested persona status composer. This
    # runner fixture has no real model, so exercise its API-unavailable fallback.
    async def reply_agent_status(client, message, **kwargs):
        await message.reply_text(kwargs["fallback"])

    namespace.update(
        reply_agent_status=reply_agent_status,
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
        _stop_typing_keepalive=AsyncMock(),
        i18n=SimpleNamespace(t=lambda key, **kwargs: key),
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
    assert delivery.client.send_message.await_count == int(
        use_tool and pretool_text != tool_text and not uncertain_delivery
    )
    native_finals = sum(
        isinstance(call.args[0], pyrogram.raw.functions.messages.SendMessage)
        for call in delivery.client.invoke.await_args_list
    )
    assert delivery.message.reply_text.await_count + native_finals == expected_replies
    if uncertain_delivery:
        namespace["quota"].settle.assert_not_awaited()
        assert (
            delivery.message.reply_text.await_args.args[0]
            == "bot.msg.agent.errors.delivery_unverified"
        )
    else:
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


async def test_pretool_native_final_suppresses_tool_echo_and_final_echo(delivery):
    await test_tool_send_and_final_output_deliver_only_once(
        delivery, True, "Hello", False, 1, pretool_text="Hello"
    )
    assert not generation._generations


async def test_pretool_text_and_distinct_tool_reply_both_preserved(delivery):
    await test_tool_send_and_final_output_deliver_only_once(
        delivery, True, "Hello", False, 1, pretool_text="Starting"
    )
    assert not generation._generations


@pytest.mark.parametrize("streaming", [True, False])
async def test_uncertain_pretool_send_halts_turn_without_final_echo(
    delivery, streaming
):
    await test_tool_send_and_final_output_deliver_only_once(
        delivery,
        streaming,
        "Hello",
        False,
        2,
        pretool_text="Starting",
        uncertain_delivery=True,
    )
    assert not generation._generations


async def test_runner_cancellation_aborts_preview_and_never_finalizes(delivery):
    with pytest.raises(asyncio.CancelledError):
        await test_tool_send_and_final_output_deliver_only_once(
            delivery,
            True,
            "Hello",
            False,
            0,
            use_tool=False,
            cancel_mid_stream=True,
        )
    assert not generation._generations
    assert not any(
        isinstance(call.args[0], pyrogram.raw.functions.messages.SendMessage)
        for call in delivery.client.invoke.await_args_list
    )
    delivery.message.reply_text.assert_not_awaited()


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


@pytest.mark.parametrize("streaming", [False, True])
async def test_group_reply_carries_verified_reference_in_every_output_mode(
    delivery, streaming
):
    delivery.message.chat.type = pyrogram.enums.ChatType.SUPERGROUP
    delivery.message.text = "thôi demote đi"
    reference = SimpleNamespace(target="3", timestamp=1000)
    delivery.deps.moderation_reference = reference
    if streaming:
        stream = delivery.namespace["StreamingOutput"](
            delivery.client, delivery.message, deps=delivery.deps
        )
        await stream.append_delta("AI reply")
        assert await stream.finalize()
    else:
        assert await delivery.namespace["reply_output"](
            delivery.client, delivery.message, "AI reply", deps=delivery.deps
        )
    replies = [
        call.args[1]
        for call in delivery.namespace["memttlcache"].set.await_args_list
        if call.args[0] == "reply:2"
    ]
    assert len(replies) == 1
    assert replies[0].moderation_reference is reference
    assert replies[0].original_user_message == "thôi demote đi"
