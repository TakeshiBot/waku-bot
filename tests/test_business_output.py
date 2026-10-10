"""Native Kurigram output methods, with only Telegram transport replaced."""

import asyncio
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pyrogram
import pytest
from pyrogram import raw
from pyrogram.types import Chat, Message, User

from waku.common.rich_message import sent_message_id
from waku.plugins.agent import generation, output, rich_output


def business_updates(
    wrapper="updates", message_id=10, *, edit=False, text="Final", entities=None
):
    update_class = (
        raw.types.UpdateBotEditBusinessMessage
        if edit
        else raw.types.UpdateBotNewBusinessMessage
    )
    update = update_class(
        connection_id="business-a",
        message=raw.types.Message(
            id=message_id,
            peer_id=raw.types.PeerUser(user_id=2),
            date=1,
            message=text,
            entities=entities,
            out=True,
            restriction_reason=[],
        ),
        qts=1,
    )
    if wrapper == "direct":
        return update
    if wrapper == "short":
        return raw.types.UpdateShort(update=update, date=1)
    kwargs = dict(
        updates=[update],
        users=[raw.types.User(id=2, first_name="Chat")],
        chats=[],
        date=1,
        seq=1,
    )
    if wrapper == "combined":
        return raw.types.UpdatesCombined(**kwargs, seq_start=1)
    return raw.types.Updates(**kwargs)


@pytest.mark.parametrize("wrapper", ["direct", "updates", "combined", "short"])
def test_native_business_sent_id_envelopes(wrapper):
    assert sent_message_id(business_updates(wrapper, message_id=42)) == 42


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(generation, "_generations", {})
    monkeypatch.setattr(rich_output, "_OFFICIAL_DRAFT_UNSUPPORTED_PEERS", set())
    monkeypatch.setattr(rich_output, "_DRAFT_PEER_NEXT_SEND", OrderedDict())
    client = pyrogram.Client("business-output-test", in_memory=True)
    calls = []
    failures = SimpleNamespace(
        rich=False, entity=False, draft=False, edit=False, business_result=None
    )
    peer = raw.types.InputPeerUser(user_id=2, access_hash=0)
    monkeypatch.setattr(client, "resolve_peer", AsyncMock(return_value=peer))

    async def invoke(query, *, business_connection_id=None):
        calls.append((query, business_connection_id))
        if isinstance(query, raw.functions.messages.SetTyping):
            if failures.draft and isinstance(
                query.action,
                (
                    raw.types.InputSendMessageRichMessageDraftAction,
                    raw.types.SendMessageTextDraftAction,
                ),
            ):
                raise pyrogram.errors.TextdraftPeerInvalid()
            return True
        if isinstance(query, raw.functions.messages.EditMessage):
            if failures.edit:
                raise RuntimeError("edit failed")
            if business_connection_id:
                return business_updates(
                    message_id=query.id,
                    edit=True,
                    text=query.message,
                    entities=query.entities,
                )
            return raw.types.Updates(
                updates=[
                    raw.types.UpdateEditMessage(
                        message=raw.types.Message(
                            id=query.id,
                            peer_id=raw.types.PeerUser(user_id=2),
                            date=1,
                            message=query.message,
                            out=True,
                            restriction_reason=[],
                        ),
                        pts=1,
                        pts_count=1,
                    )
                ],
                users=[raw.types.User(id=2, first_name="Chat")],
                chats=[],
                date=1,
                seq=1,
            )
        if getattr(query, "rich_message", None) and failures.rich:
            raise pyrogram.errors.RichMessageUnsupported()
        if getattr(query, "entities", None) and failures.entity:
            failures.entity = False
            raise pyrogram.errors.EntityBoundsInvalid()
        if getattr(query, "rich_message", None) and failures.business_result:
            return business_updates(failures.business_result)
        return raw.types.UpdateShortSentMessage(
            id=10, pts=1, pts_count=1, date=1, out=True
        )

    monkeypatch.setattr(client, "invoke", invoke)
    monkeypatch.setattr(output.app_config, "agent_rich_output", False)

    def message(connection="business-a"):
        return Message(
            id=1,
            text="Hello",
            chat=Chat(id=2, type=pyrogram.enums.ChatType.PRIVATE),
            from_user=User(id=3, first_name="User"),
            business_connection_id=connection,
            client=client,
        )

    return SimpleNamespace(
        client=client, calls=calls, failures=failures, message=message
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", [None, "business-a"])
async def test_native_plain_reply_and_entity_fallback_bind_account(
    transport, connection
):
    message = transport.message(connection)
    transport.failures.entity = True
    result = await output._send_plain_reply(message, "**Hello**")
    assert isinstance(result, Message)
    assert result.business_connection_id == connection
    assert len(transport.calls) == 2
    assert all(account == connection for _, account in transport.calls)
    assert all(
        isinstance(query, raw.functions.messages.SendMessage)
        for query, _ in transport.calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", [None, "business-a"])
async def test_raw_rich_send_and_plain_fallback_stay_on_account(
    transport, monkeypatch, connection
):
    monkeypatch.setattr(output.app_config, "agent_rich_output", True)
    monkeypatch.setattr(output.memttlcache, "get", AsyncMock(return_value=False))
    monkeypatch.setattr(output.memttlcache, "set", AsyncMock())
    monkeypatch.setattr(output.memttlcache, "delete", AsyncMock())
    transport.failures.rich = True
    assert await output.reply_output(
        transport.client, transport.message(connection), "# Hello"
    )
    assert getattr(transport.calls[0][0], "rich_message", None) is not None
    assert transport.calls[-1][0].rich_message is None
    assert all(account == connection for _, account in transport.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper", ["updates", "combined", "short"])
async def test_real_business_rich_delivery_id_can_finalize_stream(
    transport, monkeypatch, wrapper
):

    monkeypatch.setattr(output.app_config, "agent_rich_output", True)
    monkeypatch.setattr(output.memttlcache, "get", AsyncMock(return_value=False))
    monkeypatch.setattr(output.memttlcache, "delete", AsyncMock())
    transport.failures.business_result = wrapper
    stream = output.StreamingOutput(transport.client, transport.message())
    await stream.append_delta("Preview")
    assert not stream.delivered
    stream.current_text = "Final"
    assert await stream.finalize()
    assert stream.reply_message_id == 10
    assert stream.delivered
    assert [type(query).__name__ for query, _ in transport.calls] == [
        "SetTyping",
        "SendMessage",
    ]
    assert all(account == "business-a" for _, account in transport.calls)


@pytest.mark.asyncio
async def test_failed_rich_tail_reports_incomplete_without_resending_head(
    transport, monkeypatch
):
    monkeypatch.setattr(output.app_config, "agent_rich_output", True)
    monkeypatch.setattr(output.memttlcache, "get", AsyncMock(return_value=False))
    monkeypatch.setattr(output.memttlcache, "set", AsyncMock())
    monkeypatch.setattr(output.memttlcache, "delete", AsyncMock())
    monkeypatch.setattr(
        output,
        "convert_rich_md",
        lambda text: [
            pyrogram.types.InputRichMessage(html="<p>Head</p>"),
            pyrogram.types.InputRichMessage(html="<p>Tail</p>"),
        ],
    )
    original = transport.client.invoke
    send_count = 0

    async def invoke(query, *, business_connection_id=None):
        nonlocal send_count
        send_count += 1
        if send_count > 1:
            transport.calls.append((query, business_connection_id))
            raise RuntimeError("tail failed")
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    with pytest.raises(output.DeliveryUncertain):
        await output.reply_output(transport.client, transport.message(), "Head\n\nTail")
    assert len(transport.calls) == 2
    assert all(account == "business-a" for _, account in transport.calls)
    assert [bool(query.rich_message) for query, _ in transport.calls] == [
        True,
        True,
    ]


@pytest.mark.asyncio
async def test_business_draft_and_final_stay_on_bound_account(transport):

    stream = output.StreamingOutput(transport.client, transport.message())
    await stream.append_delta("First")
    await stream.append_delta(" Second")
    assert await stream.finalize()
    assert stream.delivered
    assert [type(query).__name__ for query, _ in transport.calls] == [
        "SetTyping",
        "SendMessage",
    ]
    assert all(account == "business-a" for _, account in transport.calls)
    assert transport.calls[-1][0].random_id == transport.calls[0][0].action.random_id


@pytest.mark.asyncio
async def test_business_edit_guard_rechecks_after_peer_resolution(
    transport, monkeypatch
):
    authorized = True

    async def resolve_peer(chat_id):
        nonlocal authorized
        authorized = False
        return raw.types.InputPeerUser(user_id=chat_id, access_hash=0)

    monkeypatch.setattr(transport.client, "resolve_peer", resolve_peer)
    result = await rich_output.edit_business_message_text(
        transport.client,
        transport.message(),
        10,
        "Final",
        should_send=lambda: authorized,
    )
    assert result is None
    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper", ["updates", "combined", "short"])
async def test_business_edit_native_response_keeps_entities_and_account_without_lookup(
    transport, monkeypatch, wrapper
):
    original = transport.client.invoke

    async def invoke(query, *, business_connection_id=None):
        if isinstance(query, raw.functions.messages.EditMessage):
            transport.calls.append((query, business_connection_id))
            return business_updates(
                wrapper,
                message_id=query.id,
                edit=True,
                text=query.message,
                entities=query.entities,
            )
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    monkeypatch.setattr(transport.client, "get_messages", AsyncMock())
    entities = [
        pyrogram.types.MessageEntity(
            type=pyrogram.enums.MessageEntityType.BOLD, offset=0, length=4
        )
    ]
    result = await rich_output.edit_business_message_text(
        transport.client, transport.message(), 10, "\U0001f642AB", entities=entities
    )
    assert isinstance(result, Message)
    assert result.business_connection_id == "business-a"
    assert result.entities[0].length == 4
    query, connection = transport.calls[0]
    assert query.entities[0].length == 4
    assert connection == "business-a"
    transport.client.get_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_final_send_is_never_retried(transport):

    stream = output.StreamingOutput(transport.client, transport.message())
    await stream.append_delta("Preview")
    original = transport.client.invoke

    async def invoke(query, *, business_connection_id=None):
        if isinstance(query, raw.functions.messages.SendMessage):
            transport.calls.append((query, business_connection_id))
            raise OSError("delivery acknowledgement lost")
        return await original(query, business_connection_id=business_connection_id)

    transport.client.invoke = invoke
    stream.current_text = "Final"
    with pytest.raises(output.DeliveryUncertain):
        await stream.finalize()
    assert not await stream.finalize()
    assert (
        sum(
            isinstance(query, raw.functions.messages.SendMessage)
            for query, _ in transport.calls
        )
        == 1
    )


@pytest.mark.asyncio
async def test_business_draft_uses_native_invoke_and_final_sends_once(transport):
    stream = output.StreamingOutput(transport.client, transport.message())
    await stream.append_delta("Hello")
    assert not stream.delivered
    assert isinstance(
        transport.calls[0][0].action, raw.types.SendMessageTextDraftAction
    )
    assert transport.calls[0][1] == "business-a"
    assert await stream.finalize()
    assert stream.delivered
    assert (
        sum(
            isinstance(query, raw.functions.messages.SendMessage)
            for query, _ in transport.calls
        )
        == 1
    )
    assert all(account == "business-a" for _, account in transport.calls)


@pytest.mark.asyncio
async def test_native_invoke_wraps_draft_in_business_connection(transport, monkeypatch):
    rpc = AsyncMock(return_value=True)
    session = SimpleNamespace(invoke=rpc)
    monkeypatch.setattr(transport.client, "is_connected", True)
    monkeypatch.setattr(
        transport.client, "get_session", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(transport.client, "fetch_peers", AsyncMock())
    monkeypatch.setattr(
        transport.client, "invoke", pyrogram.Client.invoke.__get__(transport.client)
    )
    draft = rich_output.OfficialRichDraftStreamer(transport.client, transport.message())
    draft.update("Preview")
    assert await draft._send_draft()
    wrapped = rpc.await_args.kwargs["query"]
    assert isinstance(wrapped, raw.functions.InvokeWithBusinessConnection)
    assert wrapped.connection_id == "business-a"
    assert isinstance(wrapped.query, raw.functions.messages.SetTyping)
    transport.client.get_session.assert_awaited_once_with(
        business_connection_id="business-a"
    )


@pytest.mark.asyncio
async def test_unsupported_draft_buffers_then_sends_final_once(transport):
    transport.failures.draft = True
    stream = output.StreamingOutput(transport.client, transport.message())
    await stream.append_delta("Preview")
    assert not stream.delivered
    await stream.append_delta(" final")
    assert await stream.finalize()
    assert (
        sum(
            isinstance(query, raw.functions.messages.SendMessage)
            for query, _ in transport.calls
        )
        == 1
    )
    assert all(account == "business-a" for _, account in transport.calls)


@pytest.mark.asyncio
async def test_unsupported_draft_long_plain_answer_delivers_entire_tail_once(transport):
    transport.failures.draft = True
    stream = output.StreamingOutput(transport.client, transport.message())
    await stream.append_delta("Preview")
    assert not stream.delivered
    stream.current_text = "**" + "\U0001f642" * 2600 + "**"
    assert await stream.finalize()
    writes = [
        query
        for query, _ in transport.calls
        if isinstance(
            query,
            (raw.functions.messages.SendMessage, raw.functions.messages.EditMessage),
        )
    ]
    assert [type(query).__name__ for query in writes] == [
        "SendMessage",
        "SendMessage",
    ]
    assert writes[0].message + writes[1].message == "\U0001f642" * 2600
    assert all(len(query.message.encode("utf-16-le")) // 2 <= 4000 for query in writes)
    assert writes[0].entities and writes[1].entities
    assert all(account == "business-a" for _, account in transport.calls)


@pytest.mark.asyncio
async def test_failed_plain_stream_tail_reports_incomplete_without_resending_head(
    transport, monkeypatch
):

    stream = output.StreamingOutput(transport.client, transport.message())
    await stream.append_delta("x" * 5000)
    original = transport.client.invoke
    count = 0

    async def invoke(query, *, business_connection_id=None):
        nonlocal count
        if isinstance(query, raw.functions.messages.SendMessage):
            count += 1
            if count > 1:
                transport.calls.append((query, business_connection_id))
                raise RuntimeError("tail failed")
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    with pytest.raises(output.DeliveryUncertain):
        await stream.finalize()
    assert not await stream.finalize()
    assert stream.delivered
    assert [type(query).__name__ for query, _ in transport.calls] == [
        "SetTyping",
        "SendMessage",
        "SendMessage",
    ]
    assert all(account == "business-a" for _, account in transport.calls)


@pytest.mark.asyncio
async def test_failed_plain_chunk_after_success_reports_incomplete(
    transport, monkeypatch
):
    original = transport.client.invoke

    async def invoke(query, *, business_connection_id=None):
        if transport.calls:
            transport.calls.append((query, business_connection_id))
            raise RuntimeError("tail failed")
        return await original(query, business_connection_id=business_connection_id)

    monkeypatch.setattr(transport.client, "invoke", invoke)
    with pytest.raises(output.DeliveryUncertain):
        await output.reply_output(transport.client, transport.message(), "x" * 5000)
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_background_draft_and_final_are_blocked_after_revocation(transport):
    authorized = True
    stream = output.StreamingOutput(
        transport.client, transport.message(), should_send=lambda: authorized
    )
    await stream.append_delta("Preview")
    draft = stream.official_draft
    authorized = False
    before = len(transport.calls)
    assert not await draft._send_draft()
    assert not await stream.finalize()
    assert len(transport.calls) == before
    assert not stream.delivered


@pytest.mark.asyncio
async def test_plain_chunk_guard_stops_after_first_delivery(transport, monkeypatch):
    authorized = True
    original = transport.client.invoke

    async def invoke(query, *, business_connection_id=None):
        nonlocal authorized
        result = await original(query, business_connection_id=business_connection_id)
        authorized = False
        return result

    monkeypatch.setattr(transport.client, "invoke", invoke)
    assert not await output.reply_output(
        transport.client,
        transport.message(),
        "x" * 5000,
        should_send=lambda: authorized,
    )
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_typing_action_uses_business_account(transport):
    typing = output.TypingKeepAlive(transport.client, transport.message())
    typing.start()
    await asyncio.sleep(0)
    await typing.stop()
    assert isinstance(transport.calls[0][0], raw.functions.messages.SetTyping)
    assert transport.calls[0][1] == "business-a"


def test_unsupported_draft_cache_is_separate_for_each_account(monkeypatch):
    monkeypatch.setattr(
        rich_output, "_OFFICIAL_DRAFT_UNSUPPORTED_PEERS", {(123, "business-a", 2)}
    )
    assert (123, "business-b", 2) not in rich_output._OFFICIAL_DRAFT_UNSUPPORTED_PEERS
