"""Native Discord send/typing protocol with HTTP replaced, never Telegram/Discord IO."""

import ast
import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from waku.discordbot.constants import _CUSTOM_EMOJI_RE, _EMOJI_RE


def user(user_id, *, bot=False):
    return {
        "id": str(user_id),
        "username": "Waku" if bot else "User",
        "discriminator": "0",
        "avatar": None,
        "bot": bot,
    }


def raw_message(message_id, *, bot=False, content="hello"):
    return {
        "id": str(message_id),
        "channel_id": "20",
        "author": user(900 if bot else 42, bot=bot),
        "timestamp": datetime.now(UTC).isoformat(),
        "type": 0,
        "content": content,
        "attachments": [],
        "embeds": [],
        "pinned": False,
        "mention_everyone": False,
        "tts": False,
        "flags": 0,
    }


@pytest.fixture
async def delivery(monkeypatch):
    client = discord.Client(intents=discord.Intents.none())
    await client._async_setup_hook()
    sdk_state = client._connection
    sdk_state.user = discord.ClientUser(state=sdk_state, data=user(900, bot=True))
    guild = sdk_state._add_guild_from_data(
        {
            "id": "10",
            "name": "Server",
            "owner_id": "42",
            "roles": [],
            "channels": [
                {
                    "id": "20",
                    "type": 0,
                    "name": "general",
                    "position": 0,
                    "permission_overwrites": [],
                }
            ],
        }
    )
    channel = guild.get_channel(20)
    source = discord.Message(state=sdk_state, channel=channel, data=raw_message(101))
    events = []
    contexts = []
    payloads = []
    original_typing = discord.TextChannel.typing

    def typing(channel):
        context = original_typing(channel)
        contexts.append(context)
        return context

    monkeypatch.setattr(discord.TextChannel, "typing", typing)

    async def send_typing(channel_id):
        assert channel_id == 20
        events.append("typing")

    async def send(channel_id, *, params):
        assert channel_id == 20
        assert contexts[-1].task and not contexts[-1].task.done()
        payloads.append(params.payload)
        events.append("send")
        return raw_message(
            200 + len(payloads), bot=True, content=params.payload["content"]
        )

    async def pause(delay):
        assert contexts[-1].task and not contexts[-1].task.done()
        events.append("pause")

    monkeypatch.setattr(sdk_state.http, "send_typing", send_typing)
    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(sdk_state.http, "send_message", send_mock)
    path = Path(__file__).resolve().parents[1] / "waku/discordbot/messages.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"_split_reply", "_send_reply"}
    ]
    ns = {
        "discord": discord,
        "re": re,
        "asyncio": SimpleNamespace(sleep=AsyncMock(side_effect=pause)),
        "random": SimpleNamespace(uniform=lambda minimum, maximum: 1.0),
        "DISCORD_REPLY_MAX_MESSAGES": 7,
        "DISCORD_REPLY_DELAY_MIN": 0.7,
        "DISCORD_REPLY_DELAY_MAX": 3.0,
        "_CUSTOM_EMOJI_RE": _CUSTOM_EMOJI_RE,
        "_EMOJI_RE": _EMOJI_RE,
    }
    exec(compile(tree, str(path), "exec"), ns)
    yield SimpleNamespace(
        ns=ns,
        source=source,
        sdk_state=sdk_state,
        payloads=payloads,
        events=events,
        contexts=contexts,
        send=send_mock,
    )
    await asyncio.sleep(0)
    await client.close()


async def test_native_chunk_reply_mentions_typing_and_exact_legacy_delay(delivery):
    uniform_calls = []

    def uniform(minimum, maximum):
        uniform_calls.append((minimum, maximum))
        return 1.25

    delivery.ns["random"].uniform = uniform
    text = "x" * 5000
    expected = delivery.ns["_split_reply"](text)
    await delivery.ns["_send_reply"](delivery.source, text)
    assert [payload["content"] for payload in delivery.payloads] == expected
    assert all(len(payload["content"]) <= 1900 for payload in delivery.payloads)
    assert delivery.payloads[0]["message_reference"] == {
        "type": 0,
        "message_id": 101,
        "channel_id": 20,
        "guild_id": 10,
        "fail_if_not_exists": False,
    }
    assert all("message_reference" not in payload for payload in delivery.payloads[1:])
    assert all(
        payload["allowed_mentions"] == {"parse": ["users"], "replied_user": False}
        for payload in delivery.payloads
    )
    assert uniform_calls == [(0.7, 3.0)] * 2
    assert [call.args[0] for call in delivery.ns["asyncio"].sleep.await_args_list] == [
        1.25 + len(chunk) / 900 for chunk in expected[:-1]
    ]
    assert delivery.events == ["typing", "send", "pause", "send", "pause", "send"]
    await asyncio.sleep(0)
    assert delivery.contexts[0].task.cancelled()


async def test_deleted_source_is_native_optional_reference_single_send(delivery):
    original_send = delivery.send.side_effect

    async def source_deleted(channel_id, *, params):
        # Discord's optional-reference contract allows the server to deliver
        # without a reply when the source was deleted during generation.
        assert params.payload["message_reference"]["fail_if_not_exists"] is False
        return await original_send(channel_id, params=params)

    delivery.send.side_effect = source_deleted
    await delivery.ns["_send_reply"](
        delivery.source, "hello <@42> @everyone <@&123456789012345678>"
    )
    delivery.send.assert_awaited_once()
    assert delivery.payloads[0]["allowed_mentions"]["parse"] == ["users"]
    assert not delivery.payloads[0]["allowed_mentions"]["replied_user"]


@pytest.mark.parametrize("failed_send", [1, 2])
async def test_native_send_failure_never_retries_or_sends_remaining_chunks(
    delivery, failed_send
):
    original_send = delivery.send.side_effect

    async def fail(channel_id, *, params):
        if delivery.send.await_count == failed_send:
            raise discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"),
                {"message": "Missing Permissions", "code": 50013},
            )
        return await original_send(channel_id, params=params)

    delivery.send.side_effect = fail
    with pytest.raises(discord.Forbidden):
        await delivery.ns["_send_reply"](delivery.source, "x" * 5000)
    assert delivery.send.await_count == failed_send
    assert len(delivery.payloads) == failed_send - 1
    await asyncio.sleep(0)
    assert delivery.contexts[0].task.cancelled()


async def test_legacy_max_seven_chunks_and_empty_output_do_not_send(delivery):
    await delivery.ns["_send_reply"](delivery.source, " \n")
    assert not delivery.events
    await delivery.ns["_send_reply"](delivery.source, "x" * 20000)
    assert len(delivery.payloads) == 7
    assert "đã lược bớt" in delivery.payloads[-1]["content"]


@pytest.mark.parametrize(
    "token",
    ["<:smile:123456789012345678>", "<a:dance:123456789012345678>", "👩🏽‍💻", "🇻🇳"],
)
def test_forced_split_preserves_custom_and_unicode_emoji(delivery, token):
    text = "x" * 1894 + token + "y" * 100
    chunks = delivery.ns["_split_reply"](text)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 1900 for chunk in chunks)
    assert any(token in chunk for chunk in chunks)
