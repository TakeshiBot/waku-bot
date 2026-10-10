"""Slash commands exercised through native Discord Interaction HTTP methods."""

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands
from discord.webhook.async_ import async_context


class Adapter:
    def __init__(self):
        self.acks = []
        self.edits = []

    async def create_interaction_response(
        self, interaction_id, token, *, session, proxy=None, proxy_auth=None, params
    ):
        self.acks.append(params.payload)
        return {
            "interaction": {
                "id": str(interaction_id),
                "response_message_loading": True,
                "response_message_ephemeral": True,
            }
        }

    async def edit_original_interaction_response(
        self,
        application_id,
        token,
        *,
        session,
        proxy=None,
        proxy_auth=None,
        payload=None,
        multipart=None,
        files=None,
    ):
        assert self.acks, "IO must follow the private ACK"
        self.edits.append(payload)
        return {
            "id": "999",
            "channel_id": "20",
            "author": user(900, bot=True),
            "timestamp": datetime.now(UTC).isoformat(),
            "type": 0,
            "content": "",
            "attachments": [],
            "embeds": payload.get("embeds", []),
            "pinned": False,
            "mention_everyone": False,
            "tts": False,
            "flags": 64,
        }


def user(user_id, *, bot=False):
    return {
        "id": str(user_id),
        "username": "User",
        "discriminator": "0",
        "avatar": None,
        "bot": bot,
    }


def message_http(env, monkeypatch):
    send = AsyncMock(
        return_value={
            "id": "999",
            "channel_id": "20",
            "author": user(900, bot=True),
            "timestamp": datetime.now(UTC).isoformat(),
            "type": 0,
            "content": "",
            "attachments": [],
            "embeds": [],
            "pinned": False,
            "mention_everyone": False,
            "tts": False,
            "flags": 0,
        }
    )
    monkeypatch.setattr(env.client._connection.http, "send_message", send)
    return send


@pytest.fixture
async def env():
    client = discord.Client(intents=discord.Intents.none())
    sdk_state = client._connection
    sdk_state.user = discord.ClientUser(state=sdk_state, data=user(900, bot=True))
    sdk_state._add_guild_from_data(
        {
            "id": "10",
            "name": "Server",
            "owner_id": "42",
            "member_count": 5,
            "roles": [
                {
                    "id": "10",
                    "name": "@everyone",
                    "permissions": "0",
                    "position": 0,
                    "color": 0,
                    "hoist": False,
                    "managed": False,
                    "mentionable": False,
                }
            ],
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
    adapter = Adapter()
    token = async_context.set(adapter)
    storage = {}
    turn_lock = asyncio.Lock()

    async def get(key):
        return storage.get(key)

    async def set_value(key, value):
        storage[key] = value

    async def delete(key):
        storage.pop(key, None)

    async def settings(_context):
        assert adapter.acks
        return SimpleNamespace(
            ai_reply=True,
            setu_enabled=True,
            lang="vi",
            group_memory_enabled=True,
            r18_mode=0,
        )

    async def menu(interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.edit_original_response(
            embed=discord.Embed(description="menu")
        )

    ns = dict(
        __name__="__main__",
        discord=discord,
        asyncio=asyncio,
        app_commands=app_commands,
        SimpleNamespace=SimpleNamespace,
        logger=Mock(),
        common=SimpleNamespace(
            memstore=SimpleNamespace(get=get, set=set_value, delete=delete),
            memttlcache=SimpleNamespace(delete=AsyncMock()),
        ),
        app_config=SimpleNamespace(agent=True),
        state=SimpleNamespace(
            discord_agent=object(), _discord_turn_lock=lambda _id: turn_lock
        ),
        discord_command_embed=lambda description, title: discord.Embed(
            title=title, description=description
        ),
        _discord_wake_keywords=lambda: ["waku"],
        _discord_guild_settings=AsyncMock(side_effect=settings),
        _discord_global_ai_enabled=AsyncMock(return_value=True),
        _discord_dm_settings=AsyncMock(side_effect=settings),
        _is_discord_user_bot_admin=lambda requester: requester.id == 99,
        _can_clean_discord_messages=lambda context: (
            context.guild.owner_id == context.author.id
        ),
        _waiting_key=lambda author_id: f"waiting:{author_id}",
        _history_key=AsyncMock(
            side_effect=lambda context: (
                f"history:{context.channel.id}:{context.author.id}"
            )
        ),
        open_discord_config=AsyncMock(side_effect=menu),
        open_discord_server_list=AsyncMock(side_effect=menu),
    )
    path = Path(__file__).resolve().parents[1] / "waku/discordbot/commands.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [
        node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    tree.body.insert(0, future)
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), ns)

    def request(*, guild=True, user_id=42, permissions=0):
        data = {
            "id": str(discord.utils.time_snowflake(datetime.now(UTC))),
            "type": 2,
            "token": "offline-test",
            "version": 1,
            "application_id": "900",
            "attachment_size_limit": 8388608,
            "data": {"id": "1", "name": "help", "type": 1},
            "channel": {
                "id": "20",
                "type": 0 if guild else 1,
                "name": "general",
                "recipients": [user(user_id)],
            },
        }
        if guild:
            data.update(
                guild_id="10",
                member={
                    "user": user(user_id),
                    "roles": [],
                    "permissions": str(permissions),
                    "joined_at": datetime.now(UTC).isoformat(),
                    "deaf": False,
                    "mute": False,
                    "flags": 0,
                },
            )
        else:
            data["user"] = user(user_id)
        return discord.Interaction(data=data, state=sdk_state)

    yield SimpleNamespace(
        ns=ns, client=client, request=request, adapter=adapter, storage=storage
    )
    async_context.reset(token)
    await client.close()


def assert_private_embed(env):
    assert env.adapter.acks == [{"type": 5, "data": {"flags": 64}}]
    payload = env.adapter.edits[-1]
    assert payload["embeds"]
    assert not payload.get("content")
    return payload


async def test_native_registration_and_bounded_clean_option(env):
    tree = app_commands.CommandTree(env.client)
    env.ns["register_discord_commands"](tree)
    assert {command.name for command in tree.get_commands()} == {
        "config",
        "help",
        "forget",
        "invite",
        "clean",
    }
    for command in tree.get_commands():
        payload = command.to_dict(tree)
        assert not command.guild_only
        assert payload["dm_permission"] is True
        assert "info" not in payload["name"] and "server" not in payload["name"]
    option = tree.get_command("clean").to_dict(tree)["options"][0]
    assert option["min_value"] == 1 and option["max_value"] == 50


@pytest.mark.parametrize("command", ["help", "invite", "forget"])
@pytest.mark.parametrize("guild", [False, True])
async def test_utilities_use_native_private_ack_then_embed(env, command, guild):
    await env.ns[f"{command}_command"](env.request(guild=guild, user_id=99))
    payload = assert_private_embed(env)
    if command == "invite":
        assert "client_id=900" in payload["components"][0]["components"][0]["url"]
    if command == "forget":
        env.ns["common"].memttlcache.delete.assert_awaited_once_with("history:20:99")
        assert not env.storage


@pytest.mark.parametrize(
    "guild,user_id,permissions,allowed",
    [
        (True, 42, 0, True),
        (True, 99, 0, True),
        (True, 43, 8, True),
        (True, 43, 0, False),
        (False, 99, 0, True),
    ],
)
async def test_config_access_then_native_menu_delegate(
    env, guild, user_id, permissions, allowed
):
    await env.ns["config_command"](
        env.request(guild=guild, user_id=user_id, permissions=permissions)
    )
    assert_private_embed(env)
    assert env.ns["open_discord_config"].await_count == int(allowed)


@pytest.mark.parametrize("command", ["config", "help", "forget", "invite", "clean"])
async def test_utility_slash_callbacks_are_silent_for_nonadmin_dm(env, command):
    await env.ns[f"{command}_command"](env.request(guild=False, user_id=43))
    assert not env.adapter.acks and not env.adapter.edits
    env.ns["open_discord_config"].assert_not_awaited()
    env.ns["common"].memttlcache.delete.assert_not_awaited()


async def test_forget_busy_and_exception_release_lock(env):
    env.storage["waiting:42"] = asyncio.current_task()
    await env.ns["forget_command"](env.request())
    env.ns["common"].memttlcache.delete.assert_not_awaited()
    assert env.storage["waiting:42"]
    env.storage.clear()
    env.ns["common"].memttlcache.delete.side_effect = RuntimeError("cache unavailable")
    with pytest.raises(RuntimeError):
        await env.ns["forget_command"](env.request())
    assert not env.storage


@pytest.mark.parametrize("guild", [False, True])
async def test_clean_ack_before_history_and_only_bot_messages(env, monkeypatch, guild):
    candidates = [
        SimpleNamespace(author=SimpleNamespace(id=author), delete=AsyncMock())
        for author in [43, 900, 900, 43, 900]
    ]

    async def history(_channel, *, limit):
        assert env.adapter.acks and limit == 1000
        for candidate in candidates:
            yield candidate

    monkeypatch.setattr(
        discord.TextChannel if guild else discord.DMChannel, "history", history
    )
    await env.ns["clean_command"](
        env.request(guild=guild, user_id=42 if guild else 99), amount=2
    )
    assert_private_embed(env)
    assert [item.delete.await_count for item in candidates] == [0, 1, 1, 0, 0]


@pytest.mark.parametrize("guild,user_id,amount", [(True, 43, 1), (True, 42, 51)])
async def test_clean_denied_never_reads_history(
    env, monkeypatch, guild, user_id, amount
):
    history = Mock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr(discord.TextChannel, "history", history)
    await env.ns["clean_command"](
        env.request(guild=guild, user_id=user_id), amount=amount
    )
    assert_private_embed(env)
    history.assert_not_called()


@pytest.mark.parametrize("permissions", [8, 8192])
async def test_native_interaction_permissions_allow_clean_without_cached_roles(
    env, monkeypatch, permissions
):
    async def history(_channel, *, limit):
        assert env.adapter.acks
        if False:
            yield None

    monkeypatch.setattr(discord.TextChannel, "history", history)
    await env.ns["clean_command"](
        env.request(user_id=43, permissions=permissions), amount=1
    )
    assert "Đã dọn" in assert_private_embed(env)["embeds"][0]["title"]


@pytest.mark.parametrize("user_id", [43, 99])
async def test_help_matches_guild_slash_and_bot_admin_dm_commands(env, user_id):
    await env.ns["help_command"](env.request(user_id=user_id, permissions=8))
    description = assert_private_embed(env)["embeds"][0]["description"]
    assert "`/bc`" not in description and "`/server`" not in description
    assert "`/info`" not in description
    assert ("`!info [server_id]`" in description) is (user_id == 99)
    assert ("`!server`" in description) is (user_id == 99)
    assert ("`!bc`" in description) is (user_id == 99)


@pytest.mark.parametrize(
    "guild,user_id,is_bot,allowed",
    [
        (False, 99, False, True),
        (False, 43, False, False),
        (True, 99, False, False),
        (False, 99, True, False),
    ],
)
async def test_prefix_info_only_configured_admin_dm_with_native_send(
    env, monkeypatch, guild, user_id, is_bot, allowed
):
    request = env.request(guild=guild, user_id=user_id)
    sender = request.user if not is_bot else SimpleNamespace(id=user_id, bot=True)
    message = SimpleNamespace(
        guild=request.guild, author=sender, channel=request.channel
    )
    env.ns["state"].discord_client = SimpleNamespace(
        user=SimpleNamespace(id=900, name="Waku"),
        guilds=[SimpleNamespace(member_count=5), SimpleNamespace(member_count=None)],
        latency=0.125,
        is_ready=lambda: True,
    )
    http = env.client._connection.http
    send = AsyncMock(
        return_value={
            "id": "999",
            "channel_id": "20",
            "author": user(900, bot=True),
            "timestamp": datetime.now(UTC).isoformat(),
            "type": 0,
            "content": "",
            "attachments": [],
            "embeds": [],
            "pinned": False,
            "mention_everyone": False,
            "tts": False,
            "flags": 0,
        }
    )
    monkeypatch.setattr(http, "send_message", send)
    assert await env.ns["send_discord_info_message"](message) is allowed
    assert send.await_count == int(allowed)
    if allowed:
        payload = send.await_args.kwargs["params"].payload
        description = payload["embeds"][0]["description"]
        assert (
            "**2**" in description
            and "**5**" in description
            and "125 ms" in description
        )
        assert "provider" not in description and "token" not in description
        assert not payload.get("content") and payload["allowed_mentions"]["parse"] == []


async def test_prefix_info_does_not_accept_unbound_or_group_channel(env):
    channel = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        guild=None, author=SimpleNamespace(id=99, bot=False), channel=channel
    )
    assert not await env.ns["send_discord_info_message"](message)
    channel.send.assert_not_awaited()


@pytest.mark.parametrize(
    "global_ai,local_ai", [(False, True), (True, False), (True, True)]
)
async def test_info_joined_guild_details_saved_and_effective_settings(
    env, monkeypatch, global_ai, local_ai
):
    request = env.request(guild=False, user_id=99)
    message = SimpleNamespace(guild=None, author=request.user, channel=request.channel)
    env.ns["state"].discord_client = env.client
    env.ns["_discord_global_ai_enabled"].return_value = global_ai
    env.ns["_discord_guild_settings"] = AsyncMock(
        return_value=SimpleNamespace(
            ai_reply=local_ai,
            reply_to_bots=False,
            group_memory_enabled=False,
            setu_enabled=True,
            r18_mode=2,
            lang="en",
        )
    )
    send = message_http(env, monkeypatch)
    assert await env.ns["send_discord_info_message"](message, " 10 ")
    env.ns["_discord_guild_settings"].assert_awaited_once_with(env.client.get_guild(10))
    description = send.await_args.kwargs["params"].payload["embeds"][0]["description"]
    assert "Guild ID: `10`" in description and "(`42`)" in description
    assert "Kênh: **1**" in description and "Thành viên: **5**" in description
    assert f"AI Reply đã lưu: **{'BẬT' if local_ai else 'TẮT'}**" in description
    assert "Trả lời bot khác: **TẮT**" in description
    assert (
        f"AI Reply hiệu lực: **{'BẬT' if global_ai and local_ai else 'TẮT'}**"
        in description
    )
    assert "Bộ nhớ kênh: **TẮT**" in description and "Ảnh: **BẬT**" in description
    assert "R18: `2`" in description and "Ngôn ngữ: `en`" in description
    assert "token" not in description and "provider" not in description


@pytest.mark.parametrize("arguments", ["abc", "0", "-10", "10 extra", "１２", "999"])
async def test_info_invalid_or_unjoined_guild_never_reads_settings(
    env, monkeypatch, arguments
):
    request = env.request(guild=False, user_id=99)
    message = SimpleNamespace(guild=None, author=request.user, channel=request.channel)
    env.ns["state"].discord_client = env.client
    send = message_http(env, monkeypatch)
    assert await env.ns["send_discord_info_message"](message, arguments)
    assert send.await_count == 1
    env.ns["_discord_guild_settings"].assert_not_awaited()
    env.ns["_discord_global_ai_enabled"].assert_not_awaited()


async def test_info_runtime_global_ai_off_reflected_without_hidden_config(
    env, monkeypatch
):
    request = env.request(guild=False, user_id=99)
    message = SimpleNamespace(guild=None, author=request.user, channel=request.channel)
    env.ns["state"].discord_client = env.client
    env.ns["_discord_global_ai_enabled"].return_value = False
    send = message_http(env, monkeypatch)
    await env.ns["send_discord_info_message"](message)
    description = send.await_args.kwargs["params"].payload["embeds"][0]["description"]
    assert "AI toàn bộ Discord: **TẮT**" in description
    assert "AI đang sẵn sàng: **TẮT**" in description


async def test_forget_serializes_busy_claim_with_ai_handler(env):
    lock = env.ns["state"]._discord_turn_lock(42)
    async with lock:
        pending = asyncio.create_task(env.ns["forget_command"](env.request()))
        await asyncio.sleep(0)
        assert not pending.done()
        env.storage["waiting:42"] = asyncio.current_task()  # Active AI owner.
    await pending
    env.ns["common"].memttlcache.delete.assert_not_awaited()
    assert env.storage["waiting:42"]  # Never clear another run's flag.
