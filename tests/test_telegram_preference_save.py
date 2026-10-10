"""Preference drafts must never write before Save or reuse stale authority."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

from waku.plugins import lang
from waku.plugins import preference_save as menus


@pytest.fixture(autouse=True)
def isolated_drafts(monkeypatch):
    menus._drafts.clear()
    monkeypatch.setattr(menus, "schedule_saved_menu_cleanup", Mock())
    monkeypatch.setattr(
        menus, "can_manage_group_settings", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(menus, "can_manage_bot_settings", AsyncMock(return_value=True))
    monkeypatch.setattr(menus.database, "update_chat_config_fields", AsyncMock())
    monkeypatch.setattr(menus.database, "patch_user_preferences", AsyncMock())
    yield
    menus._drafts.clear()


def draft(*, private=False, values=None):
    token = menus.create_draft(
        12, 12 if private else -100123, "vi", values or {"lang": "en"}, private=private
    )
    menus.bind_draft(token, 88)
    return token


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_rss_toggle_waits_for_save_and_preserves_config_scope(
    monkeypatch, private
):
    from waku.plugins import rss

    chat_id = 12 if private else -100123
    message = SimpleNamespace(
        id=77,
        from_user=SimpleNamespace(id=12),
        chat=SimpleNamespace(
            id=chat_id,
            type=enums.ChatType.PRIVATE if private else enums.ChatType.SUPERGROUP,
        ),
        reply_text=AsyncMock(return_value=SimpleNamespace(id=88)),
    )
    monkeypatch.setattr(
        menus.database,
        "get_chat_config",
        AsyncMock(return_value=SimpleNamespace(rss_agent_summary=False)),
    )
    await rss._rss_agent_toggle(
        message, "vi", ["rss", "digest", "on"], "rss_agent_summary"
    )
    menus.database.update_chat_config_fields.assert_not_awaited()
    token = next(iter(menus._drafts))
    await menus.apply_draft(None, token, 12, chat_id, 88, "save")
    menus.database.update_chat_config_fields.assert_awaited_once_with(
        chat_id,
        {"rss_agent_summary": True},
        expected_fields={"rss_agent_summary": False},
    )
    menus.database.patch_user_preferences.assert_not_awaited()
    if private:
        menus.can_manage_bot_settings.assert_not_awaited()
    else:
        menus.can_manage_bot_settings.assert_awaited_once()


def test_private_rss_scope_cannot_target_someone_elses_chat_or_unrelated_fields():
    with pytest.raises(ValueError):
        menus.create_draft(
            12, 13, "vi", {"rss_agent_summary": True}, chat_config_private=True
        )
    with pytest.raises(ValueError):
        menus.create_draft(12, 12, "vi", {"lang": "en"}, chat_config_private=True)


@pytest.mark.asyncio
async def test_quote_probability_requires_save(monkeypatch):
    from importlib import import_module

    quote = import_module("waku.plugins.quote.quote")
    config = SimpleNamespace(lang="vi", quote_probability=0.1)
    monkeypatch.setattr(
        menus.database, "get_chat_config", AsyncMock(return_value=config)
    )
    monkeypatch.setattr(
        quote.common, "can_user_manage_bot_in_chat", AsyncMock(return_value=True)
    )
    message = SimpleNamespace(
        id=77,
        from_user=SimpleNamespace(id=12),
        sender_chat=None,
        chat=SimpleNamespace(id=-100123, type=enums.ChatType.SUPERGROUP),
        command=["qp", "0.7"],
        reply_text=AsyncMock(return_value=SimpleNamespace(id=88)),
    )
    await quote.set_quote_probability(None, message)
    assert config.quote_probability == 0.1
    menus.database.update_chat_config_fields.assert_not_awaited()
    token = next(iter(menus._drafts))
    await menus.apply_draft(None, token, 12, -100123, 88, "save")
    menus.database.update_chat_config_fields.assert_awaited_once_with(
        -100123, {"quote_probability": 0.7}, expected_fields={"quote_probability": 0.1}
    )


@pytest.mark.asyncio
async def test_save_uses_only_pending_fields_and_only_once():
    token = draft(values={"greeting": "hello"})
    menus.database.update_chat_config_fields.assert_not_called()
    outcome, _ = await menus.apply_draft(None, token, 12, -100123, 88, "save")
    assert outcome == "saved"
    menus.database.update_chat_config_fields.assert_awaited_once_with(
        -100123, {"greeting": "hello"}
    )
    with pytest.raises(PermissionError):
        await menus.apply_draft(None, token, 12, -100123, 88, "save")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,chat,message", [(13, -100123, 88), (12, -100124, 88), (12, -100123, 89)]
)
async def test_menu_cannot_be_saved_by_another_actor_or_message(actor, chat, message):
    token = draft()
    with pytest.raises(PermissionError):
        await menus.apply_draft(None, token, actor, chat, message, "save")
    menus.database.update_chat_config_fields.assert_not_called()
    assert token in menus._drafts


@pytest.mark.asyncio
async def test_expiry_and_revoked_rights_do_not_save():
    token = draft()
    menus.can_manage_group_settings.return_value = False
    with pytest.raises(PermissionError):
        await menus.apply_draft(None, token, 12, -100123, 88, "save")
    menus._drafts[token].expires = time.monotonic() - 1
    with pytest.raises(PermissionError):
        await menus.apply_draft(None, token, 12, -100123, 88, "save")
    menus.database.update_chat_config_fields.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_discards_pending_without_database_or_rights_calls():
    token = draft()
    result, _ = await menus.apply_draft(None, token, 12, -100123, 88, "cancel")
    assert result == "cancelled" and token not in menus._drafts
    menus.database.update_chat_config_fields.assert_not_called()
    menus.can_manage_group_settings.assert_not_called()


@pytest.mark.asyncio
async def test_private_language_save_updates_owner_only():
    token = draft(private=True)
    await menus.apply_draft(None, token, 12, 12, 88, "save")
    menus.database.patch_user_preferences.assert_awaited_once_with(12, lang="en")
    menus.can_manage_group_settings.assert_not_called()
    menus.database.update_chat_config_fields.assert_not_called()


@pytest.mark.asyncio
async def test_repeated_save_callbacks_are_serialized():
    token = draft()
    result = await asyncio.gather(
        *[menus.apply_draft(None, token, 12, -100123, 88, "save") for _ in range(2)],
        return_exceptions=True,
    )
    assert sum(isinstance(item, PermissionError) for item in result) == 1
    menus.database.update_chat_config_fields.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_database_write_keeps_draft_for_retry():
    token = draft()
    menus.database.update_chat_config_fields.side_effect = RuntimeError(
        "database unavailable"
    )
    with pytest.raises(RuntimeError):
        await menus.apply_draft(None, token, 12, -100123, 88, "save")
    assert token in menus._drafts
    menus.database.update_chat_config_fields.side_effect = None
    await menus.apply_draft(None, token, 12, -100123, 88, "save")


@pytest.mark.asyncio
async def test_choose_language_is_pending_until_save(monkeypatch):
    token = draft(private=True, values={"lang": "vi"})
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=12),
        message=SimpleNamespace(id=88, chat=SimpleNamespace(id=12)),
        data=f"lprefs:{token}:en",
        edit_message_text=AsyncMock(),
        answer=AsyncMock(),
    )
    await lang.choose_lang(None, query)
    assert menus._drafts[token].values == {"lang": "en"}
    menus.database.patch_user_preferences.assert_not_called()
    await menus.apply_draft(None, token, 12, 12, 88, "save")
    menus.database.patch_user_preferences.assert_awaited_once_with(12, lang="en")


def test_internal_previews_cannot_write_unrelated_or_admin_fields():
    for fields in ({"owners": [12]}, {"title_permissions": {}}, {"greeting": "hi"}):
        with pytest.raises(ValueError):
            menus.create_draft(12, 12, "vi", fields, private=True)


@pytest.mark.asyncio
async def test_group_draft_keeps_baseline_and_declines_concurrent_changes():
    token = menus.create_draft(
        12, -100123, "vi", {"lang": "en"}, baseline={"lang": "vi"}
    )
    menus.bind_draft(token, 88)
    menus.database.update_chat_config_fields.side_effect = ValueError("config_conflict")
    with pytest.raises(ValueError, match="config_conflict"):
        await menus.apply_draft(None, token, 12, -100123, 88, "save")
    menus.database.update_chat_config_fields.assert_awaited_once_with(
        -100123,
        {"lang": "en"},
        expected_fields={"lang": "vi"},
    )
    assert token in menus._drafts
