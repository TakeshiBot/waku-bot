"""Discord emoji learning and real reaction methods with HTTP/cache boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

from waku.discordbot import constants, history
from waku.discordbot.models import DiscordContextDeps, DiscordGuildSettings
from waku.discordbot.tools import reactions


@pytest.fixture
def cache(monkeypatch):
    values = {}
    ttls = {}

    async def get(key, default=None):
        value = values.get(key, default)
        return list(value) if isinstance(value, list) else value

    async def set(key, value, ttl):
        values[key] = list(value) if isinstance(value, list) else value
        ttls[key] = ttl

    monkeypatch.setattr(history.common.memttlcache, "get", AsyncMock(side_effect=get))
    monkeypatch.setattr(history.common.memttlcache, "set", AsyncMock(side_effect=set))
    monkeypatch.setattr(
        history, "_discord_global_ai_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        history,
        "_discord_guild_settings",
        AsyncMock(return_value=DiscordGuildSettings()),
    )
    monkeypatch.setattr(
        history, "_discord_dm_settings", AsyncMock(return_value=DiscordGuildSettings())
    )
    monkeypatch.setattr(
        history, "_is_discord_user_bot_admin", lambda user: user.id == 9
    )
    monkeypatch.setattr(history, "_channel_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(history, "_can_read_message_history", Mock(return_value=True))
    monkeypatch.setattr(reactions, "_can_read_message_history", Mock(return_value=True))
    return SimpleNamespace(values=values, ttls=ttls)


@pytest_asyncio.fixture
async def transport(monkeypatch, cache):
    client = discord.Client(intents=discord.Intents.none())
    state = client._connection
    bot = {
        "id": "11",
        "username": "Waku",
        "discriminator": "0",
        "avatar": None,
        "bot": True,
    }
    admin = {"id": "9", "username": "User", "discriminator": "0", "avatar": None}
    dm = discord.DMChannel(
        me=discord.ClientUser(state=state, data=bot),
        state=state,
        data={"id": "777", "recipients": [admin]},
    )
    guild = discord.Guild(
        state=state,
        data={
            "id": "123",
            "name": "Server",
            "owner_id": "9",
            "roles": [],
            "emojis": [],
            "stickers": [],
            "features": [],
        },
    )
    channel = discord.TextChannel(
        state=state,
        guild=guild,
        data={
            "id": "888",
            "name": "general",
            "type": 0,
            "position": 0,
            "permission_overwrites": [],
        },
    )
    monkeypatch.setattr(client.http, "add_reaction", AsyncMock(return_value=None))

    def payload(message_id=800, user_id=9, content="hello"):
        return {
            "id": str(message_id),
            "type": 0,
            "content": content,
            "author": dict(admin, id=str(user_id)),
        }

    monkeypatch.setattr(
        client.http, "get_message", AsyncMock(return_value=payload(801, 7))
    )

    def message(content="hello", *, user_id=9, server=False, message_id=800):
        return discord.Message(
            state=state,
            channel=channel if server else dm,
            data=payload(message_id, user_id, content),
        )

    yield SimpleNamespace(client=client, message=message, cache=cache)
    await client.close()


@pytest.mark.parametrize(
    "emoji",
    [
        "\u2764\ufe0f",
        "\U0001f44d\U0001f3fd",
        "\U0001f469\u200d\U0001f4bb",
        "\U0001f1fb\U0001f1f3",
        "1\ufe0f\u20e3",
        "#\u20e3",
        "\U0001f468\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466",
        "\U0001f469\U0001f3fd\u200d\u2764\ufe0f\u200d\U0001f48b\u200d\U0001f468\U0001f3fb",
        "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    ],
)
def test_unicode_emoji_sequences_stay_whole(emoji):
    assert constants._EMOJI_RE.findall("hello " + emoji + " world") == [emoji]
    assert constants._EMOJI_RE.fullmatch(emoji)


def test_adjacent_emoji_and_regional_flags_are_separate_complete_reactions():
    samples = [
        "\u2764\ufe0f",
        "\U0001f44d\U0001f3fd",
        "\U0001f1fb\U0001f1f3",
        "\U0001f1fa\U0001f1f8",
        "1\ufe0f\u20e3",
    ]
    assert constants._EMOJI_RE.findall("".join(samples)) == samples
    assert not constants._EMOJI_RE.findall("123 plain text !server")
    assert not constants._EMOJI_RE.findall("\u2318 \u2325 \u2b00")


@pytest.mark.asyncio
async def test_collectors_keep_legacy_windows_ttl_and_frequency_hints(transport):
    custom = "<:cute:123456789012345678>"
    source = transport.message(
        "\u2764\ufe0f \U0001f44d\U0001f3fd " + custom, server=True
    )
    cache = transport.cache
    emoji_key = history._emoji_key(source)
    reaction_key = history._reaction_key(source)
    cache.values[emoji_key] = ["\U0001f525"] * 39
    cache.values[reaction_key] = ["\U0001f525"] * 59
    await history._remember_discord_emojis(source)
    await history._remember_discord_reaction_style(source)
    assert len(cache.values[emoji_key]) == 40
    assert len(cache.values[reaction_key]) == 60
    assert cache.values[emoji_key][-3:] == [
        custom,
        "\u2764\ufe0f",
        "\U0001f44d\U0001f3fd",
    ]
    assert cache.ttls[emoji_key] == cache.ttls[reaction_key] == 7 * 24 * 60 * 60
    assert (await history._discord_emoji_hint(source)).startswith(
        "User/server emoji style: \U0001f525"
    )
    assert (await history._discord_reaction_hint(source)).startswith(
        "Discord reaction style: \U0001f525"
    )


@pytest.mark.asyncio
async def test_hint_caps_remain_eight_and_ten(transport):
    source = transport.message(server=True)
    values = [str(number) for number in range(20)]
    transport.cache.values[history._emoji_key(source)] = values
    transport.cache.values[history._reaction_key(source)] = values
    assert (
        len(
            (await history._discord_emoji_hint(source))
            .removeprefix("User/server emoji style: ")
            .split()
        )
        == 8
    )
    assert (
        len(
            (await history._discord_reaction_hint(source))
            .removeprefix("Discord reaction style: ")
            .split()
        )
        == 10
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate", ["global", "guild_ai", "allowlist", "read_scope", "dm_ai", "dm_admin"]
)
async def test_style_learning_obeys_current_security_and_ai_gates(transport, gate):
    source = transport.message("\U0001f525", server=gate not in {"dm_ai", "dm_admin"})
    if gate == "global":
        history._discord_global_ai_enabled.return_value = False
    elif gate == "guild_ai":
        history._discord_guild_settings.return_value.ai_reply = False
    elif gate == "allowlist":
        history._channel_allowed.return_value = False
    elif gate == "read_scope":
        history._can_read_message_history.return_value = False
    elif gate == "dm_ai":
        history._discord_dm_settings.return_value.ai_reply = False
    else:
        source = transport.message("\U0001f525", user_id=7)
    await history._remember_discord_emojis(source)
    await history._remember_discord_reaction_style(source, sent_emoji="\U0001f44d")
    assert transport.cache.values == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "emoji", ["\u2764\ufe0f", "\U0001f44d\U0001f3fd", "<a:cute:123456789012345678>"]
)
async def test_native_reaction_records_acknowledged_emoji_despite_stale_message(
    transport, emoji
):
    source = transport.message(server=True)
    assert source.reactions == []
    deps = DiscordContextDeps(source)
    result = await reactions.send_discord_reaction(SimpleNamespace(deps=deps), emoji)
    assert result.success and deps.side_effects_started
    assert source.reactions == []
    assert transport.cache.values[history._reaction_key(source)] == [emoji]
    transport.client.http.add_reaction.assert_awaited_once()
    assert transport.client.http.add_reaction.await_args.args[:2] == (888, 800)


@pytest.mark.asyncio
async def test_reaction_to_other_message_learns_actual_target_author(transport):
    source = transport.message(server=True)
    deps = DiscordContextDeps(source)
    result = await reactions.send_discord_reaction(
        SimpleNamespace(deps=deps), "\U0001f44d", 801
    )
    assert result.success and result.target_message_id == 801
    assert transport.cache.values == {"discord_reaction_style:123:7": ["\U0001f44d"]}
    transport.client.http.get_message.assert_awaited_once_with(888, 801)
    transport.client.http.add_reaction.assert_awaited_once_with(888, 801, "\U0001f44d")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "set"])
async def test_successful_reaction_reports_success_when_optional_cache_fails(
    transport, monkeypatch, operation
):
    source = transport.message()
    deps = DiscordContextDeps(source)

    async def fail(*args, **kwargs):
        assert deps.side_effects_started
        raise RuntimeError("cache offline")

    monkeypatch.setattr(
        history.common.memttlcache, operation, AsyncMock(side_effect=fail)
    )
    result = await reactions.send_discord_reaction(
        SimpleNamespace(deps=deps), "\U0001f525"
    )
    assert result.success and deps.side_effects_started
    transport.client.http.add_reaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_reaction_failure_sets_no_success_marker_or_style(transport):
    transport.client.http.add_reaction.side_effect = RuntimeError("denied")
    deps = DiscordContextDeps(transport.message())
    result = await reactions.send_discord_reaction(
        SimpleNamespace(deps=deps), "\U0001f525"
    )
    assert not result.success and not deps.side_effects_started
    assert transport.cache.values == {}


@pytest.mark.asyncio
async def test_target_fetch_cannot_bypass_requester_history_permission(transport):
    reactions._can_read_message_history.return_value = False
    deps = DiscordContextDeps(transport.message(server=True))
    result = await reactions.send_discord_reaction(
        SimpleNamespace(deps=deps), "\U0001f525", 801
    )
    assert not result.success and not deps.side_effects_started
    transport.client.http.get_message.assert_not_awaited()
    transport.client.http.add_reaction.assert_not_awaited()
