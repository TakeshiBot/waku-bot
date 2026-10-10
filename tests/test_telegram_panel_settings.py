"""Saving the existing panel must preserve Telegram moderation settings."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from waku.database.models import ChatConfig, ChatData
from waku.plugins.title import authority
from waku.webapp.errors import ApiError
from waku.webapp.routers import chats
from waku.webapp.schemas import ChatConfigIn, TitlePermissionsIn


@pytest.fixture
def panel(monkeypatch):
    persisted = ChatConfig(
        agent_moderation_enabled=True,
        discord_enabled=True,
        discord_reply_to_bots=True,
        title_permissions={"can_send_welcome_messages": True},
        verify_questions=[],
    )
    stale = copy.deepcopy(persisted)
    stale.agent_moderation_enabled = False
    stale.discord_enabled = False
    row = ChatData(id=-100123, title="Test", config=stale.to_dict())
    ctx = SimpleNamespace(chat=row, user=SimpleNamespace(id=123, roles=["owner"]))

    async def save(chat_id, fields, **kwargs):
        assert chat_id == -100123
        for key, value in fields.items():
            setattr(persisted, key, value)
        return persisted

    monkeypatch.setattr(
        chats.database, "update_chat_config_fields", AsyncMock(side_effect=save)
    )
    monkeypatch.setattr(chats.write_limiter, "check", Mock())
    monkeypatch.setattr(chats.audit, "record", Mock())
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})
    return persisted, ctx, request


@pytest.mark.asyncio
async def test_bot_role_cannot_enable_verification_without_current_telegram_rights(
    panel, monkeypatch
):
    _, ctx, request = panel
    check = AsyncMock(return_value=False)
    monkeypatch.setattr(chats, "can_manage_group_settings", check)
    values = {
        name: getattr(ctx.chat.chat_config, name) for name in ChatConfigIn.model_fields
    }
    values["verify_enabled"] = True
    with pytest.raises(ApiError) as error:
        await chats.update_chat_config(
            request, ctx, ChatConfigIn.model_validate(values)
        )
    assert error.value.status_code == 403
    check.assert_awaited_once_with(
        chats.client, ctx.user.id, ctx.chat.id, require_restrict_members=True
    )
    chats.database.update_chat_config_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_panel_unchanged_verification_fields_are_never_written(panel):
    _, ctx, request = panel
    values = {
        name: getattr(ctx.chat.chat_config, name) for name in ChatConfigIn.model_fields
    }
    values["greeting"] = "hello"
    await chats.update_chat_config(request, ctx, ChatConfigIn.model_validate(values))
    chats.database.update_chat_config_fields.assert_awaited_once_with(
        ctx.chat.id, {"greeting": "hello"}, expected_fields={"greeting": None}
    )


@pytest.mark.asyncio
async def test_panel_save_conflict_returns_409_without_audit_success(panel):
    _, ctx, request = panel
    values = {
        name: getattr(ctx.chat.chat_config, name) for name in ChatConfigIn.model_fields
    }
    values["greeting"] = "hello"
    chats.database.update_chat_config_fields.side_effect = ValueError("config_conflict")
    with pytest.raises(ApiError) as error:
        await chats.update_chat_config(
            request, ctx, ChatConfigIn.model_validate(values)
        )
    assert error.value.status_code == 409
    chats.audit.record.assert_not_called()


@pytest.mark.asyncio
async def test_panel_save_preserves_new_and_concurrent_settings(panel):
    persisted, ctx, request = panel
    values = {
        name: getattr(ctx.chat.chat_config, name) for name in ChatConfigIn.model_fields
    }
    values.update(greeting="hello", parse_links_enabled=False)
    saved = await chats.update_chat_config(
        request, ctx, ChatConfigIn.model_validate(values)
    )
    assert saved.greeting == "hello" and saved.parse_links_enabled is False
    assert (
        persisted.agent_moderation_enabled
        and persisted.discord_enabled
        and persisted.discord_reply_to_bots
    )
    assert persisted.title_permissions == {"can_send_welcome_messages": True}
    fields = chats.database.update_chat_config_fields.call_args.args[1]
    assert "agent_moderation_enabled" not in fields and "discord_enabled" not in fields


@pytest.mark.asyncio
async def test_global_bot_admin_cannot_change_preset_without_group_rights(
    panel, monkeypatch
):
    _, ctx, request = panel
    monkeypatch.setattr(authority, "can_set_preset", AsyncMock(return_value=False))
    with pytest.raises(ApiError) as error:
        await chats.update_title_permissions(
            request, ctx, TitlePermissionsIn(permissions={"can_manage_tags": True})
        )
    assert error.value.status_code == 403
    chats.database.update_chat_config_fields.assert_not_called()


@pytest.mark.asyncio
async def test_new_preset_rights_round_trip_without_admin_mutation(panel, monkeypatch):
    _, ctx, request = panel
    monkeypatch.setattr(authority, "can_set_preset", AsyncMock(return_value=True))
    saved = await chats.update_title_permissions(
        request,
        ctx,
        TitlePermissionsIn(
            permissions={
                "can_send_welcome_messages": True,
                "is_anonymous": True,
                "can_manage_chat": True,
            }
        ),
    )
    assert saved.title_permissions["can_send_welcome_messages"] is True
    assert saved.title_permissions["is_anonymous"] is True


@pytest.mark.asyncio
async def test_legacy_json_string_preset_can_be_saved_and_audited(panel, monkeypatch):
    _, ctx, request = panel
    ctx.chat.config = {
        **ctx.chat.config,
        "title_permissions": '{"can_manage_tags":true}',
    }
    monkeypatch.setattr(authority, "can_set_preset", AsyncMock(return_value=True))
    saved = await chats.update_title_permissions(
        request, ctx, TitlePermissionsIn(permissions={"can_manage_tags": False})
    )
    assert saved.title_permissions["can_manage_tags"] is False
    changes = chats.audit.record.call_args.kwargs["changes"]
    assert any(
        item.field == "can_manage_tags" and item.old is True and item.new is False
        for item in changes
    )


@pytest.mark.parametrize(
    "name", ["can_post_messages", "can_edit_messages", "can_manage_direct_messages"]
)
def test_channel_only_preset_grants_rejected_for_group(name):
    with pytest.raises(ValidationError):
        TitlePermissionsIn(permissions={name: True})
    assert TitlePermissionsIn(permissions={name: False}).permissions[name] is False
