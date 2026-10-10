"""Other bots can address Waku; self messages and automated loops are bounded."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from waku.discordbot import client, handlers, messages, state
from waku.discordbot.models import DiscordGuildSettings


def incoming(content="waku hello", *, author_id=42, bot=True, dm=False):
    channel = Mock(spec=discord.DMChannel if dm else discord.TextChannel)
    channel.id = 20
    return SimpleNamespace(
        id=100, content=content, clean_content=content, channel=channel,
        guild=None if dm else SimpleNamespace(id=10, name="Server"),
        author=SimpleNamespace(id=author_id, bot=bot, name="OtherBot"),
        mentions=[], reference=None,
    )


@pytest.fixture
def routing(monkeypatch):
    monkeypatch.setattr(messages.app_config, "nickname", "waku")
    monkeypatch.setattr(messages.app_config, "discord_keywords", None)
    monkeypatch.setattr(messages, "_discord_global_ai_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(messages, "_discord_guild_settings", AsyncMock(return_value=DiscordGuildSettings(reply_to_bots=True)))
    monkeypatch.setattr(messages, "_channel_allowed", AsyncMock(return_value=True))
    bot_user = SimpleNamespace(id=900)
    return bot_user


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["nickname", "mention", "reply"])
async def test_other_bot_can_address_waku(routing, trigger):
    msg = incoming("waku hello" if trigger == "nickname" else "hello")
    if trigger == "mention":
        msg.content = "<@900> hello"
        msg.mentions = [routing]
    elif trigger == "reply":
        replied = Mock(spec=discord.Message)
        replied.author = routing
        msg.reference = SimpleNamespace(resolved=replied)
    assert await messages._should_wake(msg, routing) == (True, "hello")


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", [
    incoming(author_id=900), incoming(dm=True), incoming("hello"),
    incoming("!seg"), incoming("https://www.pixiv.net/artworks/123"),
])
async def test_self_dm_and_unaddressed_bot_messages_do_not_wake(routing, msg):
    assert await messages._should_wake(msg, routing) == (False, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["global", "guild", "channel"])
async def test_other_bot_respects_existing_ai_and_channel_settings(routing, monkeypatch, blocked):
    if blocked == "global":
        monkeypatch.setattr(messages, "_discord_global_ai_enabled", AsyncMock(return_value=False))
    elif blocked == "guild":
        monkeypatch.setattr(messages, "_discord_guild_settings", AsyncMock(return_value=DiscordGuildSettings(ai_reply=False, reply_to_bots=True)))
    else:
        monkeypatch.setattr(messages, "_channel_allowed", AsyncMock(return_value=False))
    assert await messages._should_wake(incoming(), routing) == (False, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("bot,enabled", [(True, True), (True, False), (False, True), (False, False)])
async def test_bot_reply_switch_controls_bots_without_disabling_human_chat(routing, monkeypatch, bot, enabled):
    current = DiscordGuildSettings(reply_to_bots=enabled)
    monkeypatch.setattr(messages, "_discord_guild_settings", AsyncMock(return_value=current))
    wake, prompt = await messages._should_wake(incoming(bot=bot), routing)
    assert wake is (enabled or not bot)
    assert prompt == ("hello" if wake else "")


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["waku !seg", "waku https://www.pixiv.net/artworks/123", "<@900> hello"])
async def test_disabled_bot_reply_switch_also_blocks_media_and_mentions(routing, monkeypatch, content):
    monkeypatch.setattr(messages, "_discord_guild_settings", AsyncMock(return_value=DiscordGuildSettings(reply_to_bots=False)))
    msg = incoming(content)
    msg.mentions = [routing]
    assert await messages._should_wake(msg, routing) == (False, "")


@pytest.mark.asyncio
async def test_ai_entry_rechecks_bot_reply_switch_before_starting_turn(monkeypatch):
    monkeypatch.setattr(state, "discord_agent", object())
    monkeypatch.setattr(handlers, "_discord_global_ai_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(handlers, "_discord_guild_settings", AsyncMock(return_value=DiscordGuildSettings(reply_to_bots=False)))
    turn = AsyncMock()
    monkeypatch.setattr(handlers, "_handle_discord_message_turn", turn)
    await handlers._handle_message(incoming(), "hello")
    turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_keeps_mentioned_other_bot_as_a_conversation_target(monkeypatch):
    own = SimpleNamespace(id=900, name="Waku", display_name="Waku", bot=True)
    other = SimpleNamespace(id=43, name="OtherBot", display_name="OtherBot", bot=True)
    monkeypatch.setattr(state, "discord_client", SimpleNamespace(user=own))
    for name in ("_reply_context", "_discord_emoji_hint", "_discord_reaction_hint"):
        monkeypatch.setattr(messages, name, AsyncMock(return_value=None))
    for name in ("_discord_attachment_contents", "_discord_sticker_contents"):
        monkeypatch.setattr(messages, name, AsyncMock(return_value=([], [])))
    msg = incoming("<@900> answer <@43>")
    msg.mentions = [own, other]
    prompt, multimodal = await messages._build_prompt_impl(msg, "answer")
    assert "id=43 mention=<@43> name=OtherBot" in prompt[0]
    assert "id=900 mention=<@900>" not in prompt[0]
    assert not multimodal


@pytest.fixture
def limiter(monkeypatch):
    values = {}
    clock = SimpleNamespace(now=100.0)

    async def get(key, default):
        return values.get(key, default)

    async def set(key, value, ttl):
        assert ttl == 60
        values[key] = value

    monkeypatch.setattr(client.common.memttlcache, "get", AsyncMock(side_effect=get))
    monkeypatch.setattr(client.common.memttlcache, "set", AsyncMock(side_effect=set))
    monkeypatch.setattr(client.time, "monotonic", lambda: clock.now)
    return clock


@pytest.mark.asyncio
async def test_automated_wakes_are_atomic_scoped_and_expire(limiter):
    msg = incoming()
    results = await asyncio.gather(*(client._claim_bot_wake(msg) for _ in range(12)))
    assert sum(results) == 6
    assert not await client._claim_bot_wake(msg)
    assert await client._claim_bot_wake(incoming(author_id=43))
    other_channel = incoming()
    other_channel.channel.id = 21
    assert await client._claim_bot_wake(other_channel)
    limiter.now += 60
    assert await client._claim_bot_wake(msg)


@pytest.mark.asyncio
@pytest.mark.parametrize("author_id,bot,dm,permitted", [
    (42, True, False, True), (900, True, False, False),
    (42, True, True, False), (42, False, False, True),
])
async def test_native_dispatch_accepts_other_bots_and_keeps_people_unlimited(
    monkeypatch, routing, limiter, author_id, bot, dm, permitted,
):
    monkeypatch.setattr(state, "discord_stopping", False)
    monkeypatch.setattr(state, "discord_message_tasks", set())
    monkeypatch.setattr(state, "discord_ai_tasks", set())
    monkeypatch.setattr(client, "_discord_global_ai_enabled", AsyncMock(return_value=True))
    for name in ("_record_discord_group_memory", "_remember_discord_emojis", "_remember_discord_reaction_style"):
        monkeypatch.setattr(client, name, AsyncMock())
    monkeypatch.setattr(client, "_maybe_handle_discord_media_request", AsyncMock(return_value=False))
    ai = AsyncMock()
    claim = AsyncMock(wraps=client._claim_bot_wake)
    monkeypatch.setattr(client, "_handle_message", ai)
    monkeypatch.setattr(client, "_claim_bot_wake", claim)
    native = client._create_client()
    native._connection.user = routing
    msg = incoming(author_id=author_id, bot=bot, dm=dm)
    try:
        for _ in range(8):
            await native.on_message(msg)
        expected = 6 if bot and permitted else 8 if permitted else 0
        assert ai.await_count == expected
        assert claim.await_count == (8 if bot and permitted else 0)
        assert not state.discord_message_tasks
        assert not state.discord_ai_tasks
    finally:
        await native.close()
