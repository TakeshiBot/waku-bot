"""Native Kurigram draft/final/Stop boundaries, with only Telegram IO mocked."""

import asyncio
from unittest.mock import AsyncMock

import pyrogram
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.models.function import FunctionModel
from pyrogram import raw
from pyrogram.handlers import RawUpdateHandler
from test_business_output import transport as transport

from waku.plugins import message_generation
from waku.plugins.agent import generation, output, rich_output


def writes(transport):
    return [
        query
        for query, _ in transport.calls
        if isinstance(query, raw.functions.messages.SendMessage)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", [None, "business-a"])
@pytest.mark.parametrize("rich", [False, True])
async def test_private_native_preview_and_final_same_id_peer_topic_reply(
    transport, monkeypatch, connection, rich
):
    monkeypatch.setattr(output, "_rich_output_enabled", AsyncMock(return_value=rich))
    message = transport.message(connection)
    message.message_thread_id = 72
    stream = output.StreamingOutput(transport.client, message)
    await stream.append_delta("**Hello**")
    assert not stream.delivered
    preview = transport.calls[0][0]
    assert isinstance(preview, raw.functions.messages.SetTyping)
    assert preview.top_msg_id == 72
    assert preview.action.can_stop
    assert not preview.action.keep_on_stop
    assert isinstance(
        preview.action,
        raw.types.InputSendMessageRichMessageDraftAction
        if rich
        else raw.types.SendMessageTextDraftAction,
    )
    if not rich:
        assert preview.action.text.text == "Hello"
        assert isinstance(preview.action.text.entities[0], raw.types.MessageEntityBold)
    await stream.append_delta(" final")
    assert await stream.finalize()
    assert await stream.finalize()  # Idempotent: no edit and no second final send.
    final = writes(transport)
    assert len(final) == 1
    assert final[0].random_id == preview.action.random_id
    assert final[0].peer == preview.peer
    assert final[0].reply_to.reply_to_msg_id == message.id
    assert final[0].reply_to.top_msg_id == preview.top_msg_id
    assert (
        final[0].rich_message is not None if rich else final[0].message == "Hello final"
    )
    assert stream.reply_message_id == 10
    assert stream.delivered
    assert not generation._generations
    assert stream.official_draft._task.done()
    assert all(account == connection for _, account in transport.calls)
    assert not any(
        isinstance(query, raw.functions.messages.EditMessage)
        for query, _ in transport.calls
    )


@pytest.mark.asyncio
async def test_group_stream_buffers_without_preview_or_edit(transport, monkeypatch):
    monkeypatch.setattr(output.memttlcache, "set", AsyncMock())
    message = transport.message(None)
    message.chat.type = pyrogram.enums.ChatType.SUPERGROUP
    message.chat.id = -100123
    message.message_thread_id = 22
    stream = output.StreamingOutput(transport.client, message)
    await stream.append_delta("First")
    await stream.append_delta(" final")
    assert transport.calls == []
    assert stream.official_draft is None
    assert await stream.finalize()
    assert len(writes(transport)) == 1
    assert writes(transport)[0].reply_to.top_msg_id == 22
    assert writes(transport)[0].message == "First final"
    reply = output.memttlcache.set.await_args.args[1]
    assert reply.reply_to_message_id == message.id
    assert reply.reply_text == "First final"


@pytest.mark.asyncio
async def test_draft_flood_wait_coalesces_instead_of_disabling(transport, monkeypatch):
    original = transport.client.invoke
    attempts = 0

    async def invoke(query, *, business_connection_id=None):
        nonlocal attempts
        if isinstance(query, raw.functions.messages.SetTyping):
            attempts += 1
            if attempts == 1:
                raise pyrogram.errors.FloodWait(2)
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    draft = rich_output.OfficialRichDraftStreamer(transport.client, transport.message())
    draft.update("Initial")
    assert await draft._send_draft()
    assert draft.supported is not False
    assert not rich_output._OFFICIAL_DRAFT_UNSUPPORTED_PEERS
    draft.update("Latest")
    assert await draft._send_draft()
    assert attempts == 1
    draft._next_send = 0
    rich_output._DRAFT_PEER_NEXT_SEND.clear()
    assert await draft._send_draft()
    assert attempts == 2
    assert draft.supported
    assert transport.calls[0][0].action.text.text == "Latest"


@pytest.mark.asyncio
async def test_unchanged_preview_throttled_and_changed_delta_coalesced(transport):
    draft = rich_output.OfficialRichDraftStreamer(transport.client, transport.message())
    draft.update("First")
    assert await draft._send_draft()
    assert await draft._send_draft()
    draft.update("Second")
    assert await draft._send_draft()
    assert len(transport.calls) == 1
    draft._next_send = 0
    rich_output._DRAFT_PEER_NEXT_SEND.clear()
    assert await draft._send_draft()
    assert len(transport.calls) == 2
    assert (
        transport.calls[0][0].action.random_id == transport.calls[1][0].action.random_id
    )
    assert transport.calls[1][0].action.text.text == "Second"


@pytest.mark.asyncio
async def test_rich_draft_rejection_falls_back_to_text_same_draft(
    transport, monkeypatch
):
    original = transport.client.invoke
    rejected = []

    async def invoke(query, *, business_connection_id=None):
        if isinstance(query.action, raw.types.InputSendMessageRichMessageDraftAction):
            rejected.append(query)
            raise pyrogram.errors.RichMessageUnsupported()
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    draft = rich_output.OfficialRichDraftStreamer(
        transport.client, transport.message(), rich=True
    )
    draft.update("**First**")
    assert await draft._send_draft()
    assert draft.supported
    assert not draft.rich
    assert rejected[0].action.random_id == transport.calls[0][0].action.random_id
    assert transport.calls[0][0].action.text.text == "First"


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", [None, "business-a"])
@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [(" ", "visible content"), ("#", " visible content"), ("```", "\nvisible\n```")],
)
async def test_empty_markdown_prefix_thinks_then_streams_later_text(
    transport, monkeypatch, connection, prefix, suffix
):
    monkeypatch.setattr(output, "_rich_output_enabled", AsyncMock(return_value=True))
    stream = output.StreamingOutput(transport.client, transport.message(connection))
    await stream.append_delta(prefix)
    first = transport.calls[0][0]
    assert isinstance(first.action, raw.types.SendMessageTextDraftAction)
    assert first.action.text.text == ""
    assert stream.official_draft._task is not None
    assert generation.has_live_generation(transport.client, stream.message)
    await stream.append_delta(suffix)
    stream.official_draft._next_send = 0
    rich_output._DRAFT_PEER_NEXT_SEND.clear()
    assert await stream.official_draft._send_draft()
    later = transport.calls[-1][0]
    assert isinstance(later.action, raw.types.InputSendMessageRichMessageDraftAction)
    assert later.action.rich_message.markdown == prefix + suffix
    assert later.action.random_id == first.action.random_id
    assert await stream.finalize()
    assert len(writes(transport)) == 1
    assert writes(transport)[0].random_id == first.action.random_id
    assert all(account == connection for _, account in transport.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", [None, "business-a"])
@pytest.mark.parametrize("model_api", ["iter", "run_stream"])
async def test_real_agent_rich_empty_rpc_recovers_background_stream(
    transport, monkeypatch, connection, model_api
):
    monkeypatch.setattr(output, "_rich_output_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(rich_output.OfficialRichDraftStreamer, "UPDATE_INTERVAL", 0.01)
    original = transport.client.invoke
    rejected = []
    visible = asyncio.Event()

    async def invoke(query, *, business_connection_id=None):
        if isinstance(query, raw.functions.messages.SetTyping):
            action = query.action
            if isinstance(action, raw.types.InputSendMessageRichMessageDraftAction):
                if action.rich_message.markdown == "**":
                    rejected.append(query)
                    # Exact native SDK shape for an unknown 400 error; no
                    # is_unknown flag, which would write unknown_errors.txt.
                    raise pyrogram.errors.BadRequest(
                        "[400 RICH_MESSAGE_EMPTY]", rpc_name="messages.SetTyping"
                    )
                visible.set()
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)

    async def model_stream(messages, info):
        yield "**"
        await asyncio.sleep(0)
        yield "Visible**"
        # The actual background updater must recover while the model is
        # still generating, rather than leaving only a final message.
        await asyncio.wait_for(visible.wait(), timeout=1)
        yield " final"

    agent = Agent(FunctionModel(stream_function=model_stream))
    stream = output.StreamingOutput(transport.client, transport.message(connection))
    if model_api == "run_stream":
        async with agent.run_stream("Hi") as result:
            async for delta in result.stream_text(delta=True, debounce_by=None):
                await stream.append_delta(delta)
            stream.current_text = await result.get_output()
    else:
        # Main Telegram runner uses node.stream() events, while Business
        # uses run_stream(). Exercise the installed Agent's two real APIs.
        async with agent.iter("Hi") as agent_run:
            node = agent_run.next_node
            while not Agent.is_end_node(node):
                if Agent.is_model_request_node(node):
                    async with node.stream(agent_run.ctx) as request_stream:
                        async for event in request_stream:
                            if isinstance(event, PartStartEvent) and isinstance(
                                event.part, TextPart
                            ):
                                await stream.append_delta(event.part.content)
                            elif isinstance(event, PartDeltaEvent) and isinstance(
                                event.delta, TextPartDelta
                            ):
                                await stream.append_delta(event.delta.content_delta)
                node = await agent_run.next(node)
            assert agent_run.result.output == stream.current_text
    assert len(rejected) == 1
    previews = [query for query, _ in transport.calls]
    assert isinstance(previews[0].action, raw.types.SendMessageTextDraftAction)
    assert previews[0].action.text.text == ""
    assert visible.is_set()
    assert stream.official_draft.rich
    assert stream.official_draft.supported
    assert await stream.finalize()
    assert len(writes(transport)) == 1
    assert writes(transport)[0].rich_message.markdown == "**Visible** final"
    assert all(
        query.action.random_id == rejected[0].action.random_id for query in previews
    )
    assert writes(transport)[0].random_id == rejected[0].action.random_id
    assert all(account == connection for _, account in transport.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", [None, "business-a"])
async def test_transient_initial_draft_error_retries_same_id_with_latest_delta(
    transport, monkeypatch, connection
):
    original = transport.client.invoke
    attempted = []

    async def invoke(query, *, business_connection_id=None):
        if isinstance(query, raw.functions.messages.SetTyping):
            attempted.append(query)
            if len(attempted) == 1:
                raise OSError("Lost draft ACK")
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    stream = output.StreamingOutput(transport.client, transport.message(connection))
    await stream.append_delta("Initial")
    assert stream.official_draft._task is not None
    assert generation.has_live_generation(transport.client, stream.message)
    await stream.append_delta(" latest")
    assert await stream.official_draft._send_draft()
    assert len(attempted) == 1  # Retry respects its cooldown.
    stream.official_draft._next_send = 0
    rich_output._DRAFT_PEER_NEXT_SEND.clear()
    assert await stream.official_draft._send_draft()
    assert attempted[1].action.text.text == "Initial latest"
    assert attempted[1].action.random_id == attempted[0].action.random_id
    assert await stream.finalize()
    assert len(writes(transport)) == 1


@pytest.mark.asyncio
async def test_draft_transient_retries_bounded_without_poisoning_peer(
    transport, monkeypatch
):
    attempted = []

    async def invoke(query, *, business_connection_id=None):
        attempted.append(query)
        raise OSError("Unavailable transport")

    monkeypatch.setattr(transport.client, "invoke", invoke)
    draft = rich_output.OfficialRichDraftStreamer(transport.client, transport.message())
    draft.update("Preview")
    for _ in range(draft.MAX_TRANSIENT_FAILURES):
        draft._next_send = 0
        rich_output._DRAFT_PEER_NEXT_SEND.clear()
        assert await draft._send_draft()
    draft._next_send = 0
    rich_output._DRAFT_PEER_NEXT_SEND.clear()
    assert not await draft._send_draft()
    assert len(attempted) == draft.MAX_TRANSIENT_FAILURES + 1
    assert not rich_output._OFFICIAL_DRAFT_UNSUPPORTED_PEERS


@pytest.mark.asyncio
async def test_revocation_during_empty_rich_rpc_prevents_fallback(
    transport, monkeypatch
):
    authorized = True

    async def invoke(query, *, business_connection_id=None):
        nonlocal authorized
        transport.calls.append((query, business_connection_id))
        authorized = False
        raise pyrogram.errors.BadRequest("[400 RICH_MESSAGE_EMPTY]")

    monkeypatch.setattr(transport.client, "invoke", invoke)
    stream = output.StreamingOutput(
        transport.client, transport.message(), should_send=lambda: authorized
    )
    monkeypatch.setattr(output, "_rich_output_enabled", AsyncMock(return_value=True))
    await stream.append_delta("**")
    assert len(transport.calls) == 1
    assert not generation._generations
    assert not await stream.finalize()
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_draft_entity_rejection_uses_unformatted_text_only_once(
    transport, monkeypatch
):
    original = transport.client.invoke
    rejected = []

    async def invoke(query, *, business_connection_id=None):
        if query.action.text.entities:
            rejected.append(query)
            raise pyrogram.errors.EntityBoundsInvalid()
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    draft = rich_output.OfficialRichDraftStreamer(transport.client, transport.message())
    draft.update("**Visible**")
    assert await draft._send_draft()
    assert len(rejected) == 1
    assert len(transport.calls) == 1
    assert transport.calls[0][0].action.text.text == "Visible"
    assert not transport.calls[0][0].action.text.entities
    assert transport.calls[0][0].action.random_id == rejected[0].action.random_id


@pytest.mark.asyncio
async def test_final_flood_wait_retries_identical_native_request(
    transport, monkeypatch
):
    original = transport.client.invoke
    attempts = []
    waits = []
    original_sleep = asyncio.sleep

    async def sleep(seconds):
        waits.append(seconds)
        await original_sleep(0)

    async def invoke(query, *, business_connection_id=None):
        attempts.append(query)
        if len(attempts) == 1:
            raise pyrogram.errors.FloodWait(1)
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    monkeypatch.setattr(rich_output.asyncio, "sleep", sleep)
    stream = output.StreamingOutput(transport.client, transport.message())
    stream.current_text = "Final"
    assert await stream.finalize()
    assert len(attempts) == 2
    assert attempts[0] is attempts[1]
    assert attempts[0].random_id == stream.random_id
    assert waits == [1]
    assert len(writes(transport)) == 1


@pytest.mark.asyncio
async def test_rejected_rich_tail_only_falls_back_unsent_chunk(transport, monkeypatch):
    monkeypatch.setattr(output, "_rich_output_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        output,
        "convert_rich_md",
        lambda _text: [
            pyrogram.types.InputRichMessage(markdown="Head"),
            pyrogram.types.InputRichMessage(markdown="**Tail**"),
        ],
    )
    original = transport.client.invoke
    rejected = []

    async def invoke(query, *, business_connection_id=None):
        if query.rich_message and query.rich_message.markdown == "**Tail**":
            rejected.append(query)
            raise pyrogram.errors.RichMessageUnsupported()
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    stream = output.StreamingOutput(transport.client, transport.message())
    stream.current_text = "Head\n\n**Tail**"
    assert await stream.finalize()
    final = writes(transport)
    assert len(final) == 2
    assert final[0].rich_message.markdown == "Head"
    assert final[1].message == "Tail"
    assert final[1].random_id == rejected[0].random_id
    assert isinstance(final[1].entities[0], raw.types.MessageEntityBold)


@pytest.mark.asyncio
async def test_native_stop_exact_draft_cancels_child_not_handler(transport):
    ready = asyncio.Event()
    state = {}
    message = transport.message(None)
    message.message_thread_id = 72

    async def model_child():
        stream = state["stream"] = output.StreamingOutput(transport.client, message)
        try:
            await stream.append_delta("Preview")
            ready.set()
            await asyncio.Event().wait()
            await stream.finalize()
        finally:
            await stream.abort()

    child = asyncio.create_task(model_child())
    await ready.wait()
    stream = state["stream"]
    update = raw.types.UpdateUserTyping(
        user_id=message.chat.id,
        top_msg_id=72,
        action=raw.types.SendMessageStopDraftAction(random_id=stream.random_id),
    )
    parsed = await pyrogram.types.MessageGenerationStopped._parse(
        transport.client,
        update,
        {message.chat.id: raw.types.User(id=message.chat.id, first_name="User")},
        {},
    )
    assert parsed.chat.id == message.chat.id
    assert parsed.draft_id == stream.random_id
    assert parsed.message_thread_id == 72
    assert isinstance(
        message_generation.generation_stopped.handlers[0][0], RawUpdateHandler
    )
    dispatcher = transport.client.dispatcher
    dispatcher.groups = {-99: [message_generation.generation_stopped.handlers[0][0]]}
    # Genuine SDK worker first parses an UpdateShort-style packet without a
    # user dictionary, then dispatches the raw handler and keeps running.
    dispatcher.updates_queue.put_nowait((update, {}, {}))
    dispatcher.updates_queue.put_nowait(None)
    worker = asyncio.create_task(dispatcher.handler_worker(asyncio.Lock()))
    await worker
    assert not worker.cancelled()
    assert not asyncio.current_task().cancelling()
    with pytest.raises(asyncio.CancelledError):
        await child
    assert stream._cancelled
    assert stream.official_draft._task.done()
    assert not await stream.finalize()
    assert not writes(transport)
    assert not generation._generations


@pytest.mark.asyncio
async def test_stop_rejects_wrong_client_peer_topic_id_and_connection(transport):
    ready = asyncio.Event()
    state = {}
    message = transport.message("business-a")
    message.message_thread_id = 72

    async def child_run():
        stream = state["stream"] = output.StreamingOutput(transport.client, message)
        try:
            await stream.append_delta("Preview")
            ready.set()
            await asyncio.Event().wait()
        finally:
            await stream.abort()

    child = asyncio.create_task(child_run())
    await ready.wait()
    draft_id = state["stream"].random_id
    try:
        for args in [
            (object(), 2, 72, draft_id, "business-a"),
            (transport.client, 3, 72, draft_id, "business-a"),
            (transport.client, 2, 73, draft_id, "business-a"),
            (transport.client, 2, 72, draft_id + 1, "business-a"),
            (transport.client, 2, 72, draft_id, "business-b"),
        ]:
            assert not generation.stop_generation(*args)
        assert not child.done()
        assert generation.stop_generation(
            transport.client, 2, 72, draft_id, "business-a"
        )
        with pytest.raises(asyncio.CancelledError):
            await child
    finally:
        if not child.done():
            child.cancel()
            with pytest.raises(asyncio.CancelledError):
                await child


@pytest.mark.asyncio
async def test_typing_does_not_replace_active_native_draft(transport):
    stream = output.StreamingOutput(transport.client, transport.message())
    await stream.append_delta("Preview")
    before = len(transport.calls)
    typing = output.TypingKeepAlive(transport.client, stream.message)
    typing.start()
    await asyncio.sleep(0)
    await typing.stop()
    assert len(transport.calls) == before
    await stream.abort()


@pytest.mark.asyncio
async def test_typing_preserves_private_topic_native_sdk(transport):
    message = transport.message()
    message.message_thread_id = 72
    typing = output.TypingKeepAlive(transport.client, message)
    typing.start()
    await asyncio.sleep(0)
    await typing.stop()
    query, connection = transport.calls[0]
    assert isinstance(query.action, raw.types.SendMessageTypingAction)
    assert query.top_msg_id == 72
    assert connection == "business-a"


@pytest.mark.asyncio
async def test_multiple_private_topics_share_peer_preview_quota(transport):
    first_message = transport.message(None)
    first_message.message_thread_id = 1
    second_message = transport.message(None)
    second_message.message_thread_id = 2
    first = rich_output.OfficialRichDraftStreamer(transport.client, first_message)
    second = rich_output.OfficialRichDraftStreamer(transport.client, second_message)
    first.update("Topic one")
    second.update("Topic two")
    assert await first._send_draft()
    assert await second._send_draft()
    assert len(transport.calls) == 1
    rich_output._DRAFT_PEER_NEXT_SEND.clear()
    assert await second._send_draft()
    assert len(transport.calls) == 2
    assert transport.calls[0][0].top_msg_id == 1
    assert transport.calls[1][0].top_msg_id == 2


@pytest.mark.asyncio
async def test_delta_arriving_during_draft_ack_is_not_marked_as_sent(
    transport, monkeypatch
):
    draft = rich_output.OfficialRichDraftStreamer(transport.client, transport.message())
    original = transport.client.invoke

    async def invoke(query, *, business_connection_id=None):
        result = await original(query, business_connection_id=business_connection_id)
        draft.update("Latest")
        return result

    monkeypatch.setattr(transport.client, "invoke", invoke)
    draft.update("First")
    assert await draft._send_draft()
    assert draft._last_text == "First"
    draft._next_send = 0
    rich_output._DRAFT_PEER_NEXT_SEND.clear()
    assert await draft._send_draft()
    assert transport.calls[1][0].action.text.text == "Latest"


@pytest.mark.asyncio
async def test_preview_guard_rechecked_after_native_entity_parsing(
    transport, monkeypatch
):
    authorized = True
    original = rich_output.utils.parse_text_entities

    async def parse(*args):
        nonlocal authorized
        result = await original(*args)
        authorized = False
        return result

    monkeypatch.setattr(rich_output.utils, "parse_text_entities", parse)
    draft = rich_output.OfficialRichDraftStreamer(
        transport.client,
        transport.message(),
        should_send=lambda: authorized,
    )
    draft.update("Preview")
    assert not await draft._send_draft()
    assert transport.calls == []


@pytest.mark.asyncio
async def test_markdown_rich_tail_fallback_preserves_code_tags(transport):
    payload = pyrogram.types.InputRichMessage(markdown="```html\n<tag>Text</tag>\n```")
    assert await output._send_rich_tail_plain(transport.message(), [payload])
    assert writes(transport)[0].message == "<tag>Text</tag>"


@pytest.mark.asyncio
async def test_offline_real_agent_streams_native_draft_then_one_final(transport):
    async def model_stream(messages, info):
        yield "**Hello**"
        yield " final"

    agent = Agent(FunctionModel(stream_function=model_stream))
    stream = output.StreamingOutput(transport.client, transport.message(None))
    async with agent.run_stream("Hi") as result:
        async for delta in result.stream_text(delta=True):
            await stream.append_delta(delta)
        stream.current_text = await result.get_output()
    assert await stream.finalize()
    assert len(writes(transport)) == 1
    assert writes(transport)[0].message == "Hello final"
    assert writes(transport)[0].random_id == transport.calls[0][0].action.random_id
    assert not generation._generations


@pytest.mark.asyncio
@pytest.mark.parametrize("rich", [False, True])
@pytest.mark.parametrize("fail_at", [1, 2])
@pytest.mark.parametrize("connection", [None, "business-a"])
async def test_nonstream_unknown_ack_stops_no_fallback_or_later_chunks(
    transport, monkeypatch, rich, fail_at, connection
):
    monkeypatch.setattr(output, "_rich_output_enabled", AsyncMock(return_value=rich))
    if rich:
        monkeypatch.setattr(
            output,
            "convert_rich_md",
            lambda _text: [
                pyrogram.types.InputRichMessage(markdown=f"Chunk {index}")
                for index in range(3)
            ],
        )
    original = transport.client.invoke
    count = 0

    async def invoke(query, *, business_connection_id=None):
        nonlocal count
        count += 1
        if count == fail_at:
            transport.calls.append((query, business_connection_id))
            raise OSError("Lost acknowledgement after send")
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    with pytest.raises(output.DeliveryUncertain):
        await output.reply_output(
            transport.client, transport.message(connection), "x" * 9000
        )
    assert len(transport.calls) == fail_at
    assert all(bool(query.rich_message) == rich for query, _ in transport.calls)
    assert all(account == connection for _, account in transport.calls)


@pytest.mark.asyncio
async def test_nonstream_known_permission_rejection_never_format_fallback(
    transport, monkeypatch
):
    monkeypatch.setattr(output, "_rich_output_enabled", AsyncMock(return_value=True))

    async def invoke(query, *, business_connection_id=None):
        transport.calls.append((query, business_connection_id))
        raise pyrogram.errors.ChatWriteForbidden()

    monkeypatch.setattr(transport.client, "invoke", invoke)
    assert not await output.reply_output(
        transport.client, transport.message(None), "Reply"
    )
    assert len(transport.calls) == 1
    assert transport.calls[0][0].rich_message is not None


@pytest.mark.asyncio
async def test_missing_plain_delivery_receipt_stops_turn(transport, monkeypatch):
    message = transport.message(None)
    monkeypatch.setattr(message, "reply_text", AsyncMock(return_value=None))
    with pytest.raises(output.DeliveryUncertain):
        await output._send_plain_reply(message, "x" * 9000)
    message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_followup_cache_failure_never_invalidates_delivered_reply(
    transport, monkeypatch, streaming
):
    message = transport.message(None)
    message.chat.type = pyrogram.enums.ChatType.SUPERGROUP
    monkeypatch.setattr(
        output.memttlcache, "set", AsyncMock(side_effect=OSError("cache offline"))
    )
    if streaming:
        stream = output.StreamingOutput(transport.client, message)
        await stream.append_delta("Reply")
        assert await stream.finalize()
        assert await stream.finalize()
    else:
        assert await output.reply_output(transport.client, message, "Reply")
    assert len(writes(transport)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_missing_native_rich_receipt_stops_before_later_chunks(
    transport, monkeypatch, streaming
):
    monkeypatch.setattr(output, "_rich_output_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        output,
        "convert_rich_md",
        lambda _text: [
            pyrogram.types.InputRichMessage(markdown="One"),
            pyrogram.types.InputRichMessage(markdown="Two"),
        ],
    )

    async def invoke(query, *, business_connection_id=None):
        transport.calls.append((query, business_connection_id))
        return raw.types.Updates(updates=[], users=[], chats=[], date=1, seq=1)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    with pytest.raises(output.DeliveryUncertain):
        if streaming:
            stream = output.StreamingOutput(transport.client, transport.message(None))
            stream.current_text = "One\n\nTwo"
            await stream.finalize()
        else:
            await output.reply_output(
                transport.client, transport.message(None), "One\n\nTwo"
            )
    assert len(writes(transport)) == 1
