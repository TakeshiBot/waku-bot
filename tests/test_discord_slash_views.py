"""Native DM Message/View methods; only Discord HTTP and storage are mocked."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
import pytest_asyncio
from test_discord_config_views import interaction

from waku.discordbot.embeds import discord_command_embed
from waku.discordbot.models import DiscordGuildSettings
from waku.discordbot.views import server_list as menus


def joined_guilds(count):
    return [
        SimpleNamespace(
            id=1000 + i,
            name=f"Server {i:04} " + "Long name " * 20,
            member_count=50,
            me=SimpleNamespace(
                joined_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i)
            ),
        )
        for i in range(count)
    ]


@pytest.fixture
def storage(monkeypatch):
    config = DiscordGuildSettings(setu_enabled=False)
    monkeypatch.setattr(menus, "_is_discord_user_bot_admin", lambda user: user.id == 9)
    monkeypatch.setattr(menus, "_discord_dm_settings", AsyncMock(return_value=config))
    return config


@pytest_asyncio.fixture
async def transport(monkeypatch, storage):
    client = discord.Client(intents=discord.Intents.none())
    state = client._connection
    bot = {
        "id": "11",
        "username": "Waku",
        "discriminator": "0",
        "avatar": None,
        "bot": True,
    }
    admin = {"id": "9", "username": "Admin", "discriminator": "0", "avatar": None}
    me = discord.ClientUser(state=state, data=bot)
    channel = discord.DMChannel(
        me=me, state=state, data={"id": "777", "recipients": [admin]}
    )
    payload = {"id": "900", "type": 0, "content": "", "author": bot}
    monkeypatch.setattr(client.http, "send_message", AsyncMock(return_value=payload))
    monkeypatch.setattr(client.http, "edit_message", AsyncMock(return_value=payload))
    monkeypatch.setattr(menus.state, "discord_client", client)

    def prefix(user_id=9, guild=None):
        data = {
            "id": "800",
            "type": 0,
            "content": "!server",
            "author": dict(admin, id=str(user_id)),
        }
        message = discord.Message(state=state, channel=channel, data=data)
        message.guild = guild
        return message

    sent = discord.Message(state=state, channel=channel, data=payload)

    def component(user_id=9, guild=None):
        request = interaction(user_id, guild, client=client)
        request.message = sent
        return request

    def set_guilds(count):
        state._guilds = {guild.id: guild for guild in joined_guilds(count)}

    yield SimpleNamespace(
        client=client,
        prefix=prefix,
        sent=sent,
        component=component,
        set_guilds=set_guilds,
    )
    await client.close()


def menu_view(transport):
    params = transport.client.http.edit_message.await_args.kwargs["params"]
    assert params.payload["components"]
    # Native Message.edit registers the exact view in Discord's ViewStore.
    dispatch = transport.client._connection._view_store._views[900]
    return next(iter(dispatch.values())).view


@pytest.mark.asyncio
async def test_server_inventory_live_joined_guilds_not_saved_approval_rows(storage):
    client = SimpleNamespace(guilds=joined_guilds(120))
    rows = await menus._joined_server_rows(client)
    assert len(rows) == 120
    assert [guild.id for guild in rows] == list(range(1119, 999, -1))
    pages = menus._server_pages(rows)
    assert len(pages) == 3
    assert [len(page.description.splitlines()) - 1 for page in pages] == [50, 50, 20]
    assert all(
        len(page) <= 6000 and len(page.description) <= 4096 and not page.fields
        for page in pages
    )
    assert pages[0].description.splitlines()[1] == "1. `1119` · 50"
    assert pages[1].description.splitlines()[1] == "51. `1069` · 50"
    assert pages[2].description.splitlines()[1] == "101. `1019` · 50"
    assert "120 server" in pages[0].footer.text
    assert "Server 0000" not in pages[0].description


@pytest.mark.asyncio
async def test_dm_prefix_sends_actual_message_then_edits_and_registers_native_view(
    transport,
):
    transport.set_guilds(1)
    source = transport.prefix()
    assert isinstance(source, discord.Message)
    await menus.open_discord_server_list_message(source)
    transport.client.http.send_message.assert_awaited_once()
    assert transport.client.http.send_message.await_args.args[0] == 777
    view = menu_view(transport)
    assert isinstance(view.message, discord.Message)
    assert view.message.id == 900
    assert isinstance(view.message.channel, discord.DMChannel)
    assert view.user_id == 9
    assert view.timeout == 900
    assert not view.is_persistent()
    assert view.previous_page.disabled and view.next_page.disabled
    view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["nonadmin", "guild", "group"])
async def test_nonadmin_or_server_or_group_invocation_silently_ignored(transport, case):
    source = transport.prefix(
        1 if case == "nonadmin" else 9,
        SimpleNamespace(id=123) if case == "guild" else None,
    )
    if case == "group":
        source.channel = SimpleNamespace(send=AsyncMock())
    await menus.open_discord_server_list_message(source)
    transport.client.http.send_message.assert_not_awaited()
    transport.client.http.edit_message.assert_not_awaited()
    menus._discord_dm_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_buttons_edit_bound_dm_paginate_reload_and_close(transport):
    transport.set_guilds(51)
    await menus.open_discord_server_list_message(transport.prefix())
    view = menu_view(transport)
    request = transport.component()
    await view.next_page.callback(request)
    assert view.page == 1 and not view.previous_page.disabled
    request.response.defer.assert_awaited_once_with(ephemeral=True)
    request.edit_original_response.assert_not_awaited()
    args = transport.client.http.edit_message.await_args
    assert args.args[:2] == (777, 900)
    assert args.kwargs["params"].payload["embeds"][0] == view.pages[1].to_dict()
    await view.previous_page.callback(transport.component())
    assert view.page == 0
    transport.set_guilds(1)
    await view.reload_servers.callback(transport.component())
    assert len(view.pages) == 1 and len(view.pages[0].description.splitlines()) == 2
    assert view.next_page.disabled
    await view.close_menu.callback(transport.component())
    assert view.is_finished()
    assert (
        transport.client.http.edit_message.await_args.kwargs["params"].payload[
            "components"
        ]
        == []
    )


@pytest.mark.asyncio
async def test_component_bound_to_admin_dm_and_original_message(transport, monkeypatch):
    await menus.open_discord_server_list_message(transport.prefix())
    view = menu_view(transport)
    assert not await view.interaction_check(transport.component(1))
    assert not await view.interaction_check(
        transport.component(9, SimpleNamespace(id=99))
    )
    wrong = transport.component()
    wrong.message = SimpleNamespace(id=901, channel=transport.sent.channel)
    assert not await view.interaction_check(wrong)
    before = transport.client.http.edit_message.await_count
    monkeypatch.setattr(menus, "_is_discord_user_bot_admin", lambda user: False)
    request = transport.component()
    await view.reload_servers.callback(request)
    assert request.response.send_message.await_args.kwargs["ephemeral"]
    assert transport.client.http.edit_message.await_count == before
    view.stop()


@pytest.mark.asyncio
async def test_timeout_edits_actual_dm_message_and_disables_components(transport):
    await menus.open_discord_server_list_message(transport.prefix())
    view = menu_view(transport)
    await view.on_timeout()
    assert view.is_finished()
    assert all(button.disabled for button in view.children)
    args = transport.client.http.edit_message.await_args
    assert args.args[:2] == (777, 900)
    components = args.kwargs["params"].payload["components"][0]["components"]
    assert all(button["disabled"] for button in components)
    assert not hasattr(menus, "_remember_discord_server_menu")
    assert not hasattr(menus, "open_discord_server_list")


@pytest.mark.asyncio
async def test_component_errors_are_ephemeral(transport):
    await menus.open_discord_server_list_message(transport.prefix())
    view = menu_view(transport)
    request = transport.component()
    await view.on_error(request, RuntimeError("offline"), view.reload_servers)
    assert request.response.send_message.await_args.kwargs["ephemeral"]
    view.stop()


@pytest.mark.asyncio
async def test_access_revoked_during_initial_load_never_discloses_server_inventory(
    transport, monkeypatch, storage
):
    transport.set_guilds(1)

    async def load(user):
        monkeypatch.setattr(menus, "_is_discord_user_bot_admin", lambda user: False)
        return storage

    menus._discord_dm_settings.side_effect = load
    await menus.open_discord_server_list_message(transport.prefix())
    payload = transport.client.http.edit_message.await_args.kwargs["params"].payload
    assert "fields" not in payload["embeds"][0]
    assert payload["components"] == []


@pytest.mark.asyncio
async def test_join_order_normalizes_timezones_and_unknown_dates_deterministically():
    def guild(guild_id, joined_at):
        return SimpleNamespace(
            id=guild_id, member_count=0, me=SimpleNamespace(joined_at=joined_at)
        )

    rows = [
        guild(5, None),
        guild(4, datetime(2026, 10, 1, 9, tzinfo=timezone(timedelta(hours=7)))),
        guild(3, datetime(2026, 10, 1, 3, tzinfo=UTC)),
        guild(2, datetime(2026, 10, 1, 2)),
        SimpleNamespace(id=1, member_count=None, me=None),
    ]
    ordered = await menus._joined_server_rows(SimpleNamespace(guilds=rows))
    assert [guild.id for guild in ordered] == [3, 2, 4, 1, 5]
    page = menus._server_pages(ordered)[0]
    assert "1. `3` · 0" in page.description
    assert "4. `1` · —" in page.description


def test_fifty_full_snowflake_ids_and_member_counts_fit_one_embed_page():
    rows = [
        SimpleNamespace(id=18446744073709551615 - i, member_count=2147483647)
        for i in range(50)
    ]
    pages = menus._server_pages(rows, "en")
    assert len(pages) == 1
    assert len(pages[0].description.splitlines()) == 51
    assert len(pages[0].description) <= 4096
    assert len(pages[0]) <= 6000
    assert str(rows[0].id) in pages[0].description
    assert str(rows[-1].id) in pages[0].description


def test_empty_server_inventory_and_english_locale():
    pages = menus._server_pages([], "en")
    assert len(pages) == 1 and "not joined" in pages[0].description
    assert "Page 1/1" in pages[0].footer.text


def test_shared_command_embed_obeys_title_and_description_limits():
    embed = discord_command_embed("x" * 5000, title="y" * 300)
    assert (
        len(embed.title) == 256 and len(embed.description) == 4096 and len(embed) < 6000
    )
