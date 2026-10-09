"""Joined guilds need no approval; Discord preferences remain isolated."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waku.database import discord as repository
from waku.database.models import ChatConfig, ChatData, DiscordChatData
from waku.discordbot import handlers, settings, state
from waku.discordbot.models import DiscordGuildSettings


@pytest.fixture
async def storage(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(ChatData.__table__.create)
        await connection.run_sync(DiscordChatData.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    get_config = repository.get_discord_chat_config
    patch_config = repository.patch_discord_chat_config
    list_servers = repository.list_discord_guilds
    read_global = repository.get_discord_global_ai_enabled
    write_global = repository.set_discord_global_ai_enabled

    async def get(chat_id):
        async with factory() as session:
            return await get_config(chat_id, session=session)

    async def patch(chat_id, updates, **kwargs):
        async with factory.begin() as session:
            return await patch_config(chat_id, updates, session=session, **kwargs)

    async def guilds():
        async with factory() as session:
            return await list_servers(session=session)

    async def global_enabled():
        async with factory() as session:
            return await read_global(session=session)

    async def set_global(enabled):
        async with factory.begin() as session:
            await write_global(enabled, session=session)

    monkeypatch.setattr(repository, "get_discord_chat_config", get)
    monkeypatch.setattr(repository, "patch_discord_chat_config", patch)
    monkeypatch.setattr(repository, "list_discord_guilds", guilds)
    monkeypatch.setattr(repository, "get_discord_global_ai_enabled", global_enabled)
    monkeypatch.setattr(repository, "set_discord_global_ai_enabled", set_global)
    monkeypatch.setattr(settings.common.memttlcache, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(settings.common.memttlcache, "set", AsyncMock())
    monkeypatch.setattr(settings.common.memttlcache, "delete", AsyncMock())
    monkeypatch.setattr(state, "discord_settings_locks", {})
    monkeypatch.setattr(state, "discord_global_ai_enabled", None)
    monkeypatch.setattr(state, "discord_ai_tasks", set())
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_global_ai_switch_survives_restart_and_isolates_platform_and_scope(storage):
    assert await settings._discord_global_ai_enabled()
    async with storage.begin() as session:
        assert await session.get(DiscordChatData, 0) is None
        session.add(ChatData(id=0, title="Telegram", config={"ai_reply": True}))
    await repository.patch_discord_chat_config(123, {"discord_ai_reply": True})
    await repository.patch_discord_chat_config(-123, {"discord_ai_reply": False})
    await settings._set_discord_global_ai_enabled(False)
    state.discord_global_ai_enabled = None  # A process restart drops the cache.
    assert not await settings._discord_global_ai_enabled()
    assert (await repository.get_discord_chat_config(123)).discord_ai_reply
    assert not (await repository.get_discord_chat_config(-123)).discord_ai_reply
    assert [guild_id for guild_id, _ in await repository.list_discord_guilds()] == [123]
    async with storage() as session:
        assert (await session.get(ChatData, 0)).config == {"ai_reply": True}
    await settings._set_discord_global_ai_enabled(True)
    state.discord_global_ai_enabled = None
    assert await settings._discord_global_ai_enabled()


@pytest.mark.asyncio
async def test_global_settings_preserve_unknown_json_and_validate_boolean(storage):
    async with storage.begin() as session:
        session.add(DiscordChatData(id=0, title="Global", config={"keep": "value"}))
    await settings._set_discord_global_ai_enabled(False)
    async with storage() as session:
        assert (await session.get(DiscordChatData, 0)).config == {"keep": "value", "discord_global_ai_enabled": False}
    for invalid in (1, "false", None):
        with pytest.raises(ValueError):
            await settings._set_discord_global_ai_enabled(invalid)


@pytest.mark.asyncio
async def test_disabling_global_ai_cancels_active_turn_and_releases_busy_flag_but_keeps_admin_tasks(storage, monkeypatch):
    state.discord_global_ai_enabled = True
    monkeypatch.setattr(state, "discord_agent", object())
    entered = asyncio.Event()
    keep_admin = asyncio.Event()

    async def pending_turn(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(handlers, "_handle_discord_message_turn", pending_turn)
    message = SimpleNamespace(guild=SimpleNamespace(id=123), author=SimpleNamespace(id=456))
    turn = asyncio.create_task(handlers._handle_message(message, "hello"))
    admin_task = asyncio.create_task(keep_admin.wait())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert turn in state.discord_ai_tasks
        await settings._set_discord_global_ai_enabled(False)
        assert turn.cancelled()
        assert not state.discord_ai_tasks
        assert not await handlers.common.memstore.get(handlers._waiting_key(456))
        assert not admin_task.done()
        assert not await settings._discord_global_ai_enabled()
    finally:
        turn.cancel()
        keep_admin.set()
        await asyncio.gather(turn, admin_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_global_save_does_not_change_active_runtime_state(storage, monkeypatch):
    state.discord_global_ai_enabled = True
    monkeypatch.setattr(repository, "set_discord_global_ai_enabled", AsyncMock(side_effect=RuntimeError("write failed")))
    with pytest.raises(RuntimeError):
        await settings._set_discord_global_ai_enabled(False)
    assert state.discord_global_ai_enabled is True


@pytest.mark.asyncio
async def test_global_read_failure_pauses_ai_without_caching_failure(storage, monkeypatch):
    read = AsyncMock(side_effect=[RuntimeError("unavailable"), True])
    monkeypatch.setattr(repository, "get_discord_global_ai_enabled", read)
    assert not await settings._discord_global_ai_enabled()
    assert state.discord_global_ai_enabled is None
    assert await settings._discord_global_ai_enabled()


@pytest.mark.asyncio
async def test_guild_and_dm_settings_are_isolated_from_telegram(storage):
    async with storage.begin() as session:
        session.add_all([
            ChatData(id=123, title="Telegram same ID", config={"greeting": "keep"}),
            ChatData(id=-123, title="Telegram group", config={"ai_reply": False}),
        ])
    guild = SimpleNamespace(id=123, name="Discord guild")
    user = SimpleNamespace(id=123)
    await settings._set_discord_guild_settings(
        guild, DiscordGuildSettings(enabled=True, ai_reply=False, r18_mode=2, lang="vi-VN")
    )
    await settings._set_discord_dm_settings(user, DiscordGuildSettings(ai_reply=True))
    assert not (await settings._discord_guild_settings(guild)).ai_reply
    assert (await settings._discord_guild_settings(guild)).lang == "vi"
    assert (await settings._discord_dm_settings(user)).ai_reply
    assert [guild_id for guild_id, _ in await repository.list_discord_guilds()] == [123]
    async with storage() as session:
        assert (await session.get(ChatData, 123)).config == {"greeting": "keep"}
        assert (await session.get(ChatData, -123)).config == {"ai_reply": False}
        assert len((await session.execute(select(DiscordChatData))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_new_joined_guild_works_without_creating_approval_or_database_row(storage):
    loaded = await settings._discord_guild_settings(SimpleNamespace(id=123))
    assert loaded.enabled and loaded.ai_reply
    assert loaded.lang == "vi"
    async with storage() as session:
        assert await session.get(DiscordChatData, 123) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["none", "pending", "rejected"])
@pytest.mark.parametrize("ai_reply", [False, True])
async def test_legacy_disabled_approval_rows_work_and_preserve_explicit_ai_preferences(storage, status, ai_reply):
    saved = {
        "discord_enabled": False, "discord_auth_status": status,
        "discord_ai_reply": ai_reply, "lang": "en", "greeting": "keep",
    }
    await repository.patch_discord_chat_config(123, saved)
    loaded = await settings._discord_guild_settings(SimpleNamespace(id=123))
    assert loaded.enabled and loaded.ai_reply is ai_reply and loaded.lang == "en"
    assert (await repository.get_discord_chat_config(123)).discord_enabled
    async with storage() as session:
        assert (await session.get(DiscordChatData, 123)).config == saved


@pytest.mark.asyncio
async def test_saving_legacy_disabled_guild_preserves_old_json_and_selected_preferences(storage):
    guild = SimpleNamespace(id=123, name="guild")
    await repository.patch_discord_chat_config(123, {
        "discord_enabled": False, "discord_auth_status": "rejected",
        "greeting": "keep", "discord_muted": True,
    })
    await settings._set_discord_guild_settings(
        guild, DiscordGuildSettings(enabled=False, ai_reply=False, lang="en"),
    )
    loaded = await settings._discord_guild_settings(guild)
    assert loaded.enabled and not loaded.ai_reply and loaded.lang == "en"
    config = await repository.get_discord_chat_config(123)
    assert config.discord_auth_status == "rejected"
    assert config.greeting == "keep" and config.discord_muted


@pytest.mark.asyncio
async def test_legacy_disabled_cache_cannot_gate_guild_but_keeps_ai_off(storage, monkeypatch):
    cached = DiscordGuildSettings(enabled=False, ai_reply=False, lang="en")
    monkeypatch.setattr(settings.common.memttlcache, "get", AsyncMock(return_value=cached))
    loaded = await settings._discord_guild_settings(SimpleNamespace(id=123))
    assert loaded.enabled and not loaded.ai_reply and loaded.lang == "en"
    assert not cached.enabled  # Do not mutate a shared cached instance.


@pytest.mark.asyncio
async def test_known_guild_listing_includes_old_rejected_disabled_rows_and_excludes_dms(storage):
    await repository.patch_discord_chat_config(20, {"discord_enabled": True})
    await repository.patch_discord_chat_config(10, {"discord_enabled": False, "discord_auth_status": "rejected"})
    await repository.patch_discord_chat_config(-10, {"discord_enabled": True})
    assert [guild_id for guild_id, _ in await repository.list_discord_guilds()] == [10, 20]


@pytest.mark.asyncio
async def test_storage_error_does_not_masquerade_as_missing_approval(storage, monkeypatch):
    monkeypatch.setattr(repository, "get_discord_chat_config", AsyncMock(side_effect=RuntimeError("storage unavailable")))
    loaded = await settings._discord_guild_settings(SimpleNamespace(id=123))
    assert loaded.enabled and not loaded.ai_reply and not loaded.setu_enabled


@pytest.mark.asyncio
async def test_invalid_ids_and_unknown_fields_fail_without_rows(storage):
    for invalid in [0, True, 2**63, -2**63, "123"]:
        with pytest.raises(ValueError):
            await repository.get_discord_chat_config(invalid)
    with pytest.raises(ValueError):
        await repository.patch_discord_chat_config(123, {"invented_field": True})
    assert not await repository.list_discord_guilds()


def test_chat_config_discord_roundtrip_retains_telegram_fields():
    config = ChatConfig.from_dict({
        "greeting": "hello", "ai_reply_other_bots_enabled": True,
        "discord_allow_r18": True, "discord_auth_status": "pending",
        "discord_auth_review_messages": [{"message_id": 1, "channel_id": 2}],
    })
    assert config.discord_r18_mode == 2
    restored = ChatConfig.from_dict(config.to_dict())
    assert restored == config
    assert restored.greeting == "hello" and restored.ai_reply_other_bots_enabled
