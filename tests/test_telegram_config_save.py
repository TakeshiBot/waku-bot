"""Native group menus stage changes and recheck authority at the save boundary."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import Client, enums, raw
from pyrogram.types import (
    CallbackQuery,
    Chat,
    ChatMember,
    ChatPrivileges,
    Message,
    User,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waku.common import telegram_authority as authority
from waku.database import chat as repository
from waku.database.models import ChatConfig, ChatData
from waku.plugins import chatconfig as menu


@pytest.fixture
def panel(monkeypatch):
    config = ChatConfig()
    client = Client(
        "group-config-offline", api_id=1, api_hash="offline", in_memory=True
    )
    client.me = User(id=99, first_name="Bot", is_bot=True)
    chat = Chat(id=-100123, type=enums.ChatType.FORUM, title="Group")
    user = User(id=1, first_name="Admin")
    reply = Message(client=client, id=20, chat=chat, from_user=client.me)
    command = Message(client=client, id=19, chat=chat, from_user=user, text="/config")

    def checked(method, result):
        async def invoke(*args, **kwargs):
            inspect.signature(getattr(Client, method)).bind(client, *args, **kwargs)
            return result

        return AsyncMock(side_effect=invoke)

    client.send_message = checked("send_message", reply)
    client.edit_message_text = checked("edit_message_text", reply)
    client.edit_message_reply_markup = checked("edit_message_reply_markup", reply)
    client.answer_callback_query = checked("answer_callback_query", True)
    monkeypatch.setattr(
        menu.database, "get_chat_config", AsyncMock(return_value=config)
    )
    monkeypatch.setattr(
        menu.database, "update_chat_config_fields", AsyncMock(return_value=config)
    )
    monkeypatch.setattr(menu, "can_manage_bot_settings", AsyncMock(return_value=True))
    monkeypatch.setattr(menu, "can_manage_group_settings", AsyncMock(return_value=True))
    monkeypatch.setattr(menu, "chat_panel_button", lambda *args: None)
    menu._SESSIONS.clear()
    monkeypatch.setattr(menu, "schedule_saved_menu_cleanup", Mock())

    async def click(action, *, actor=None, revision=None):
        token, session = next(iter(menu._SESSIONS.items()))
        query = CallbackQuery(
            client=client,
            id="cb",
            from_user=actor or user,
            message=reply,
            data=f"config_chat:{token}:{session.revision if revision is None else revision}:{action}",
        )
        await menu.config_chat(client, query)

    yield SimpleNamespace(
        client=client,
        config=config,
        chat=chat,
        user=user,
        command=command,
        reply=reply,
        click=click,
    )
    menu._SESSIONS.clear()


@pytest.mark.asyncio
async def test_real_forum_menu_stages_without_mutating_cached_config_then_saves_once(
    panel,
):
    await menu.config_chat_cmd(panel.client, panel.command)
    _, session = next(iter(menu._SESSIONS.items()))
    assert session.message_id == panel.reply.id
    await panel.click("toggle:3")  # ai_reply
    assert panel.config.ai_reply is True
    assert session.draft.ai_reply is False
    menu.database.update_chat_config_fields.assert_not_awaited()
    await panel.click("save")
    menu.database.update_chat_config_fields.assert_awaited_once_with(
        panel.chat, {"ai_reply": False}, expected_fields={"ai_reply": True}
    )
    assert not menu._SESSIONS


@pytest.mark.asyncio
async def test_image_button_cycles_safe_r18_mixed_off_and_saves_only_selected_mode(
    panel,
):
    await menu.config_chat_cmd(panel.client, panel.command)
    _, session = next(iter(menu._SESSIONS.items()))
    assert session.draft.setu_enabled and session.draft.telegram_r18_mode == 0
    for enabled, mode, label in [
        (True, 1, "R18"),
        (True, 2, "Cả hai"),
        (False, 0, "Tắt"),
        (True, 0, "An toàn"),
        (True, 1, "R18"),
    ]:
        await panel.click("toggle:6")
        assert (session.draft.setu_enabled, session.draft.telegram_r18_mode) == (
            enabled,
            mode,
        )
        button = menu._markup(next(iter(menu._SESSIONS)), session).inline_keyboard[3][0]
        assert label in button.text
        assert panel.config.setu_enabled and panel.config.telegram_r18_mode == 0
        menu.database.update_chat_config_fields.assert_not_awaited()
    await panel.click("save")
    menu.database.update_chat_config_fields.assert_awaited_once_with(
        panel.chat, {"telegram_r18_mode": 1}, expected_fields={"telegram_r18_mode": 0}
    )
    assert not menu._SESSIONS


@pytest.mark.asyncio
async def test_disabling_images_saves_enabled_and_mode_in_one_patch(panel):
    panel.config.telegram_r18_mode = 2
    await menu.config_chat_cmd(panel.client, panel.command)
    await panel.click("toggle:6")
    await panel.click("save")
    menu.database.update_chat_config_fields.assert_awaited_once_with(
        panel.chat,
        {"setu_enabled": False, "telegram_r18_mode": 0},
        expected_fields={"setu_enabled": True, "telegram_r18_mode": 2},
    )


@pytest.mark.asyncio
async def test_callback_owner_revision_and_revoked_authority_are_enforced(panel):
    await menu.config_chat_cmd(panel.client, panel.command)
    _, session = next(iter(menu._SESSIONS.items()))
    await panel.click("toggle:3", actor=User(id=2, first_name="Other"))
    assert session.draft.ai_reply
    await panel.click("toggle:3")
    await panel.click("toggle:3", revision=0)
    assert not session.draft.ai_reply
    menu.can_manage_bot_settings.return_value = False
    await panel.click("save")
    menu.database.update_chat_config_fields.assert_not_awaited()
    assert not menu._SESSIONS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "index,field", [(13, "agent_moderation_enabled"), (12, "verify_enabled")]
)
async def test_global_bot_admin_cannot_grant_telegram_moderation_and_rechecks_save(
    panel,
    index,
    field,
):
    await menu.config_chat_cmd(panel.client, panel.command)
    _, session = next(iter(menu._SESSIONS.items()))
    menu.can_manage_group_settings.return_value = False
    await panel.click(f"toggle:{index}")
    assert not getattr(session.draft, field)
    menu.can_manage_group_settings.return_value = True
    await panel.click(f"toggle:{index}")
    assert getattr(session.draft, field)
    assert not getattr(panel.config, field)
    menu.can_manage_group_settings.return_value = False
    await panel.click("save")
    menu.database.update_chat_config_fields.assert_not_awaited()
    assert menu._SESSIONS


@pytest.mark.asyncio
async def test_cancel_and_external_conflict_do_not_persist(panel):
    await menu.config_chat_cmd(panel.client, panel.command)
    await panel.click("toggle:3")
    menu.database.update_chat_config_fields.side_effect = ValueError("config_conflict")
    await panel.click("save")
    assert menu._SESSIONS
    menu.database.update_chat_config_fields.reset_mock()
    await panel.click("cancel")
    menu.database.update_chat_config_fields.assert_not_awaited()
    assert not menu._SESSIONS


@pytest.mark.asyncio
async def test_fresh_sdk_authority_does_not_use_cached_or_anonymous_roles():
    client = SimpleNamespace(get_chat_member=AsyncMock())
    client.get_chat_member.return_value = ChatMember(
        status=enums.ChatMemberStatus.ADMINISTRATOR,
        user=User(id=1, first_name="Admin"),
        privileges=ChatPrivileges(can_change_info=True, can_restrict_members=True),
    )
    assert await authority.can_manage_group_settings(
        client, 1, -100123, require_restrict_members=True
    )
    client.get_chat_member.return_value = ChatMember(
        status=enums.ChatMemberStatus.MEMBER, user=User(id=1, first_name="User")
    )
    assert not await authority.can_manage_group_settings(client, 1, -100123)
    client.get_chat_member.side_effect = RuntimeError("offline")
    assert not await authority.can_manage_group_settings(client, 1, -100123)
    before = client.get_chat_member.await_count
    assert not await authority.can_manage_group_settings(client, 1087968824, -100123)
    assert client.get_chat_member.await_count == before


@pytest.mark.asyncio
async def test_sdk_basic_admin_role_requires_actual_basic_peer_and_owner_auto_grant_is_ignored(
    monkeypatch,
):
    client = SimpleNamespace(
        get_chat_member=AsyncMock(
            return_value=ChatMember(
                status=enums.ChatMemberStatus.ADMINISTRATOR,
                user=User(id=12, first_name="Admin"),
            )
        ),
        resolve_peer=AsyncMock(return_value=raw.types.InputPeerChat(chat_id=123)),
    )
    assert await authority.can_manage_group_settings(
        client, 12, -123, require_restrict_members=True
    )
    client.resolve_peer.return_value = raw.types.InputPeerChannel(
        channel_id=123, access_hash=1
    )
    assert not await authority.can_manage_group_settings(client, 12, -123)
    from waku import database
    from waku.config import app_config

    monkeypatch.setattr(app_config, "owners", [])
    monkeypatch.setattr(database, "get_user_by_id", AsyncMock(return_value=None))
    association = SimpleNamespace(is_bot_admin=True, promoted_by=None)
    monkeypatch.setattr(
        database, "get_association", AsyncMock(return_value=association)
    )
    assert not await authority.can_manage_bot_settings(client, 12, -123)
    association.promoted_by = 2
    assert await authority.can_manage_bot_settings(client, 12, -123)
    assert not await authority.can_manage_group_settings(client, 12, -123)


@pytest.fixture
async def storage(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'config.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ChatData.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "AsyncSessionFactory", factory)
    cache = SimpleNamespace(set=AsyncMock(), delete=AsyncMock())
    monkeypatch.setattr(repository, "memttlcache", cache)
    async with factory() as session:
        async with session.begin():
            session.add(
                ChatData(id=-100123, title="Test", config=ChatConfig().to_dict())
            )
    yield SimpleNamespace(factory=factory, cache=cache)
    await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_concurrent_saves_conflict_and_unrelated_fields_are_preserved(
    storage,
):
    results = await asyncio.gather(
        *(
            repository.update_chat_config_fields(
                -100123, {"ai_reply": False}, expected_fields={"ai_reply": True}
            )
            for _ in range(6)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ChatConfig) for result in results) == 1
    assert all(
        isinstance(result, ChatConfig) or str(result) == "config_conflict"
        for result in results
    )
    await asyncio.gather(
        repository.update_chat_config_fields(-100123, {"greeting": "new greeting"}),
        repository.update_chat_config_fields(
            -100123, {"agent_moderation_enabled": True}
        ),
    )
    async with storage.factory() as session:
        config = (await session.get(ChatData, -100123)).chat_config
    assert not config.ai_reply
    assert config.greeting == "new greeting"
    assert config.agent_moderation_enabled


@pytest.mark.asyncio
async def test_rolled_back_config_never_published_to_cache(storage):
    async with storage.factory() as session:
        with pytest.raises(RuntimeError, match="rollback"):
            async with session.begin():
                await repository.update_chat_config_fields(
                    -100123, {"ai_reply": False}, session=session
                )
                raise RuntimeError("rollback")
    storage.cache.set.assert_not_awaited()
    async with storage.factory() as session:
        assert (await session.get(ChatData, -100123)).chat_config.ai_reply


def test_moderation_default_is_explicit_and_strict():
    assert not ChatConfig().agent_moderation_enabled
    assert not ChatConfig.from_dict(
        {"agent_moderation_enabled": "false"}
    ).agent_moderation_enabled
    assert not ChatConfig.from_dict(
        {"agent_moderation_enabled": 1}
    ).agent_moderation_enabled
    assert ChatConfig.from_dict(
        {"agent_moderation_enabled": True}
    ).agent_moderation_enabled
