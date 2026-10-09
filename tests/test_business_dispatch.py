"""Offline integration through native plugin discovery, parsing and dispatch."""

import asyncio
import builtins
import importlib.util
import weakref
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pyrogram import Client, enums, raw
from pyrogram.handlers import (
    BusinessConnectionHandler,
    BusinessMessageHandler,
    MessageHandler,
)
from pyrogram.types import Message

from waku.plugins import business_chat


@pytest.mark.asyncio
async def test_native_business_plugin_parses_and_dispatches_only_business_updates(
    monkeypatch,
):
    config = SimpleNamespace(
        business_chat_enabled=True,
        agent=True,
        agent_model="offline/model",
        agent_streaming=False,
        agent_run_timeout=1,
        cachettl_agent_history=60,
    )
    monkeypatch.setattr(business_chat, "app_config", config)
    for name, value in (
        ("_connection_permissions", {}),
        ("_connection_revisions", {}),
        ("_chat_locks", weakref.WeakValueDictionary()),
        ("_active_tasks", {}),
        ("_active_outputs", {}),
        ("_processed_messages", OrderedDict()),
    ):
        monkeypatch.setattr(business_chat, name, value)
    cache = {}

    async def cache_get(key, default=None):
        return cache.get(key, default)

    async def cache_set(key, value, ttl=0):
        cache[key] = value

    monkeypatch.setattr(
        business_chat,
        "memttlcache",
        SimpleNamespace(
            get=AsyncMock(side_effect=cache_get), set=AsyncMock(side_effect=cache_set)
        ),
    )
    result = SimpleNamespace(
        output="Offline answer",
        all_messages=lambda: [
            ModelRequest(parts=[UserPromptPart("Incoming")]),
            ModelResponse(parts=[TextPart("Offline answer")]),
        ],
    )
    model_run = AsyncMock(return_value=result)
    monkeypatch.setattr(
        business_chat,
        "_make_business_agent",
        Mock(return_value=SimpleNamespace(run=model_run)),
    )
    monkeypatch.setattr(
        business_chat, "_compact_business_history", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(business_chat, "_usage_limits", Mock(return_value=object()))
    delivery = AsyncMock(return_value=True)
    monkeypatch.setattr(business_chat, "_send_business_reply", delivery)

    client = Client(
        "business-dispatch-offline",
        in_memory=True,
        plugins={"root": "waku.plugins", "include": ["business_chat"]},
    )
    # Unexpected peer fetches, connection lookups or chat sends must fail the test.
    monkeypatch.setattr(
        client,
        "invoke",
        AsyncMock(side_effect=AssertionError("No network transport expected")),
    )
    lookup = AsyncMock(side_effect=AssertionError("Connection update already received"))
    monkeypatch.setattr(client, "get_business_connection", lookup)
    ordinary_messages, business_messages = [], []

    async def ordinary(_client, message):
        ordinary_messages.append(message)

    async def observe_business(_client, message):
        business_messages.append(message)

    client.add_handler(MessageHandler(ordinary), group=-100)
    client.add_handler(BusinessMessageHandler(observe_business), group=-50)
    client.load_plugins()
    # Native add_handler schedules registration on the client's event loop.
    await asyncio.sleep(0)
    registered = client.dispatcher.groups[0]
    assert any(
        isinstance(handler, BusinessMessageHandler)
        and handler.callback is business_chat.business_chat_message
        for handler in registered
    )
    assert any(isinstance(handler, BusinessConnectionHandler) for handler in registered)

    owner_id, customer_id = 456, 123
    users = {
        owner_id: raw.types.User(id=owner_id, first_name="Owner"),
        customer_id: raw.types.User(id=customer_id, first_name="Customer"),
    }
    connection = raw.types.UpdateBotBusinessConnect(
        connection=raw.types.BotBusinessConnection(
            connection_id="native-account",
            user_id=owner_id,
            dc_id=2,
            date=1,
            rights=raw.types.BusinessBotRights(reply=True),
        ),
        qts=1,
    )

    def raw_message(message_id, text, outgoing=False):
        return raw.types.Message(
            id=message_id,
            from_id=raw.types.PeerUser(user_id=owner_id if outgoing else customer_id),
            # MTProto peer_id identifies the dialog in the connected account.
            peer_id=raw.types.PeerUser(user_id=customer_id),
            date=1,
            message=text,
            out=outgoing,
            restriction_reason=[],
        )

    incoming = raw.types.UpdateBotNewBusinessMessage(
        connection_id="native-account", message=raw_message(10, "Incoming"), qts=2
    )
    outgoing = raw.types.UpdateBotNewBusinessMessage(
        connection_id="native-account",
        message=raw_message(11, "Already answered", True),
        qts=3,
    )
    ordinary_control = raw.types.UpdateNewMessage(
        message=raw_message(12, "Ordinary bot DM"), pts=1, pts_count=1
    )
    for update in (connection, incoming, outgoing, ordinary_control):
        await client.dispatcher.updates_queue.put((update, users, {}))
    await client.dispatcher.updates_queue.put(None)
    # Run the SDK worker itself: no copied dispatcher, parser or filter logic.
    await asyncio.wait_for(client.dispatcher.handler_worker(asyncio.Lock()), timeout=2)

    assert len(business_messages) == 2
    parsed_incoming, parsed_outgoing = business_messages
    assert all(isinstance(message, Message) for message in business_messages)
    assert [message.business_connection_id for message in business_messages] == [
        "native-account"
    ] * 2
    assert [message.id for message in business_messages] == [10, 11]
    assert [message.outgoing for message in business_messages] == [False, True]
    assert all(message.chat.id == customer_id for message in business_messages)
    assert all(
        message.chat.type == enums.ChatType.PRIVATE for message in business_messages
    )
    assert parsed_incoming.from_user.id == customer_id
    assert parsed_outgoing.from_user.id == owner_id
    assert parsed_incoming.text == "Incoming"
    model_run.assert_awaited_once()
    assert model_run.await_args.args == ("Incoming",)
    delivery.assert_awaited_once()
    assert delivery.await_args.args[:3] == (client, parsed_incoming, "Offline answer")
    lookup.assert_not_awaited()
    client.invoke.assert_not_awaited()
    assert [message.id for message in ordinary_messages] == [12]
    assert ordinary_messages[0].business_connection_id is None
    assert set(cache) == {"business_chat_history:native-account:123"}
    assert not business_chat._active_tasks


def test_disabled_agent_plugin_import_does_not_load_agent_stack(monkeypatch):
    """Execute the whole plugin module with AI imports explicitly unavailable."""
    monkeypatch.setattr(business_chat.app_config, "agent", False)
    original_import = builtins.__import__

    def forbid_agent_import(name, *args, **kwargs):
        if name.startswith(("waku.plugins.agent", "pydantic_ai")):
            raise AssertionError(f"Disabled agent imported {name}")
        return original_import(name, *args, **kwargs)

    source = Path(business_chat.__file__)
    spec = importlib.util.spec_from_file_location("business_lazy_smoke", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(builtins, "__import__", forbid_agent_import)
    spec.loader.exec_module(module)
    assert module.app_config.agent is False
    assert isinstance(
        module.business_chat_message.handlers[0][0], BusinessMessageHandler
    )
    assert isinstance(
        module.business_connection_changed.handlers[0][0], BusinessConnectionHandler
    )
