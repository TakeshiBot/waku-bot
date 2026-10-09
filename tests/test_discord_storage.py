"""Discord settings and authorization cannot mutate Telegram identities."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waku.database import discord as repository
from waku.database.models import ChatConfig, ChatData, DiscordChatData
from waku.discordbot import settings, state
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
    list_servers = repository.list_authorized_discord_servers

    async def get(chat_id):
        async with factory() as session:
            return await get_config(chat_id, session=session)

    async def patch(chat_id, updates, **kwargs):
        async with factory.begin() as session:
            return await patch_config(chat_id, updates, session=session, **kwargs)

    async def authorized():
        async with factory() as session:
            return await list_servers(session=session)

    monkeypatch.setattr(repository, "get_discord_chat_config", get)
    monkeypatch.setattr(repository, "patch_discord_chat_config", patch)
    monkeypatch.setattr(repository, "list_authorized_discord_servers", authorized)
    monkeypatch.setattr(settings.common.memttlcache, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(settings.common.memttlcache, "set", AsyncMock())
    monkeypatch.setattr(settings.common.memttlcache, "delete", AsyncMock())
    monkeypatch.setattr(state, "discord_auth_locks", {})
    yield factory
    await engine.dispose()


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
    assert [guild_id for guild_id, _ in await repository.list_authorized_discord_servers()] == [123]
    async with storage() as session:
        assert (await session.get(ChatData, 123)).config == {"greeting": "keep"}
        assert (await session.get(ChatData, -123)).config == {"ai_reply": False}
        assert len((await session.execute(select(DiscordChatData))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_authorization_is_idempotent_and_keeps_notification_snapshot(storage):
    pending, created = await settings._submit_discord_auth_request(123, "guild", 45, 67)
    assert created and pending.discord_auth_status == "pending"
    _, duplicate = await settings._submit_discord_auth_request(123, "guild", 99, 101)
    assert not duplicate
    await settings._set_discord_auth_review_messages(123, [{"message_id": 5, "channel_id": 6}])
    first, second = await asyncio.gather(
        settings._approve_discord_auth_request(123), settings._approve_discord_auth_request(123)
    )
    assert first is not None and second is None
    assert first.discord_auth_requester_id == 45
    assert first.discord_auth_review_messages == [{"message_id": 5, "channel_id": 6}]
    current = await repository.get_discord_chat_config(123)
    assert current.discord_enabled and current.discord_auth_status == "none"
    assert current.discord_auth_requester_id is None
    assert current.discord_auth_review_messages is None


@pytest.mark.asyncio
async def test_reject_reapply_and_revoke_preserve_other_fields(storage):
    await repository.patch_discord_chat_config(123, {"greeting": "keep", "lang": "en"})
    await settings._submit_discord_auth_request(123, "guild", 45, 67)
    denied = await settings._reject_discord_auth_request(123, "  no approval  ")
    assert denied.discord_auth_rejection_reason == "no approval"
    assert not denied.discord_enabled
    _, created = await settings._submit_discord_auth_request(123, "guild", 45, 67)
    assert created
    await settings._approve_discord_auth_request(123)
    await settings._delete_discord_guild_settings_by_id(123)
    current = await repository.get_discord_chat_config(123)
    assert not current.discord_enabled
    assert current.greeting == "keep" and current.lang == "en"
    async with storage() as session:
        assert await session.get(DiscordChatData, 123) is not None


@pytest.mark.asyncio
async def test_stale_server_config_cannot_restore_revoked_authorization(storage):
    guild = SimpleNamespace(id=123, name="guild")
    await settings._set_discord_guild_settings(guild, DiscordGuildSettings(enabled=True))
    await settings._delete_discord_guild_settings_by_id(123)
    with pytest.raises(PermissionError):
        await settings._set_discord_guild_settings(
            guild, DiscordGuildSettings(enabled=True), require_enabled=True
        )
    assert not (await repository.get_discord_chat_config(123)).discord_enabled


@pytest.mark.asyncio
async def test_invalid_ids_and_unknown_fields_fail_without_rows(storage):
    for invalid in [0, True, 2**63, -2**63, "123"]:
        with pytest.raises(ValueError):
            await repository.get_discord_chat_config(invalid)
    with pytest.raises(ValueError):
        await repository.patch_discord_chat_config(123, {"invented_field": True})
    assert not await repository.list_authorized_discord_servers()


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
