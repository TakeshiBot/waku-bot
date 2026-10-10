"""Real Discord views with mocked interaction HTTP/storage boundaries."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.webhook.async_ import async_context
from test_discord_commands import Adapter, user

from waku.discordbot.models import DiscordGuildSettings
from waku.discordbot.views import config as menus


def interaction(user_id=1, guild=None, *, admin=False, client=None):
    response = SimpleNamespace(done=False)
    response.is_done = lambda: response.done

    async def acknowledge(**kwargs):
        response.done = True

    response.defer = AsyncMock(side_effect=acknowledge)
    response.send_message = AsyncMock(side_effect=acknowledge)
    return SimpleNamespace(
        user=SimpleNamespace(
            id=user_id, guild_permissions=SimpleNamespace(administrator=admin)
        ),
        guild=guild,
        client=client,
        response=response,
        edit_original_response=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def guild():
    return SimpleNamespace(id=123, owner_id=1, name="Server", member_count=50)


def button(view, callback):
    return next(
        item for item in view.children if item.callback == getattr(view, callback)
    )


def has_button(view, callback):
    return any(item.callback == getattr(view, callback) for item in view.children)


@pytest.fixture
def storage(monkeypatch):
    current = DiscordGuildSettings(enabled=True)
    monkeypatch.setattr(
        menus, "_discord_guild_settings", AsyncMock(return_value=current)
    )
    monkeypatch.setattr(menus, "_discord_dm_settings", AsyncMock(return_value=current))
    monkeypatch.setattr(menus, "_set_discord_guild_settings", AsyncMock())
    monkeypatch.setattr(menus, "_set_discord_dm_settings", AsyncMock())
    monkeypatch.setattr(
        menus, "_discord_global_ai_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(menus, "_set_discord_global_ai_enabled", AsyncMock())
    monkeypatch.setattr(menus, "_rotate_discord_history_epoch", AsyncMock())
    monkeypatch.setattr(menus, "_is_discord_user_bot_admin", lambda user: user.id == 9)
    return current


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_id,admin,allowed",
    [(1, False, True), (2, True, True), (9, False, True), (2, False, False)],
)
async def test_config_opens_privately_for_owner_admin_or_bot_admin(
    storage, user_id, admin, allowed
):
    request = interaction(user_id, guild(), admin=admin)
    await menus.open_discord_config(request)
    if allowed:
        request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        view = request.edit_original_response.await_args.kwargs["view"]
        assert isinstance(view, discord.ui.View)
        assert view.user_id == user_id
        assert view.timeout == 900
        assert not view.is_persistent()
        assert len(view.embed()) <= 6000
        assert button(view, "toggle_ai_reply").label == "✅ AI trả lời"
        assert all(isinstance(item, discord.ui.Button) for item in view.children)
        view.stop()
    else:
        assert request.response.send_message.await_args.kwargs["ephemeral"] is True
        menus._discord_guild_settings.assert_not_awaited()
        request.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_menu_bound_to_initiator_guild_and_current_permissions(storage):
    server = guild()
    view = menus.DiscordConfigView(interaction(2, server, admin=True), storage)
    assert not await view.interaction_check(interaction(1, server))
    assert not await view.interaction_check(interaction(2, None, admin=True))
    assert not await view.interaction_check(interaction(2, server, admin=False))
    assert await view.interaction_check(interaction(2, server, admin=True))
    view.stop()


@pytest.mark.asyncio
async def test_disabled_legacy_row_does_not_gate_admin_configuration(storage):
    storage.enabled = False
    request = interaction(1, guild())
    await menus.open_discord_config(request)
    view = request.edit_original_response.await_args.kwargs["view"]
    assert isinstance(view, menus.DiscordConfigView)
    assert await view.interaction_check(interaction(1, request.guild))
    view.stop()


@pytest.mark.asyncio
async def test_real_button_updates_draft_save_cancel_reload_and_close(storage):
    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    await button(view, "toggle_ai_reply").callback(interaction(1, server))
    assert not view.pending_settings.ai_reply
    menus._set_discord_guild_settings.assert_not_awaited()
    assert storage.ai_reply
    save = next(
        button for button in view.children if getattr(button, "label", "") == "Lưu"
    )
    assert not save.disabled
    await save.callback(interaction(1, server))
    saved = menus._set_discord_guild_settings.await_args.args[1]
    assert not saved.ai_reply
    assert saved is not view.pending_settings
    assert view.pending_settings == view.saved_settings
    await view.toggle_ai_reply(interaction(1, server))
    await view.cancel_changes(interaction(1, server))
    assert not view.pending_settings.ai_reply
    await view.reload_settings(interaction(1, server))
    assert view.pending_settings.ai_reply
    request = interaction(1, server)
    await view.close_menu(request)
    assert request.edit_original_response.await_args.kwargs["view"] is None
    assert view.is_finished()


@pytest.mark.asyncio
async def test_overview_buttons_and_language_change_native_controls(storage):
    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    await button(view, "toggle_lang").callback(interaction(1, server))
    assert view.pending_settings.lang == "en"
    assert view.embed().title == "Waku settings"
    assert button(view, "toggle_ai_reply").label == "✅ AI replies"
    assert button(view, "toggle_lang").label == "✅ Language: English"
    assert any(getattr(button, "label", "") == "Save" for button in view.children)
    view.stop()


@pytest.mark.asyncio
async def test_memory_change_only_resets_history_after_successful_save(storage):
    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    await view.toggle_group_memory(interaction(1, server))
    menus._rotate_discord_history_epoch.assert_not_awaited()
    await view.save_config(interaction(1, server))
    menus._rotate_discord_history_epoch.assert_awaited_once_with(server)
    view.stop()


@pytest.mark.asyncio
async def test_guild_images_cycle_off_safe_r18_mixed(storage):
    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    observed = []
    for _ in range(4):
        await view.toggle_r18(interaction(1, server))
        observed.append(
            (view.pending_settings.setu_enabled, view.pending_settings.r18_mode)
        )
    assert observed == [(True, 1), (True, 2), (False, 0), (True, 0)]
    view.stop()


@pytest.mark.asyncio
async def test_dm_menu_only_owner_and_safe_images(storage):
    view = menus.DiscordConfigView(interaction(9), storage)
    assert not await view.interaction_check(interaction(456))
    assert not await view.interaction_check(interaction(9, guild()))
    await view.toggle_r18(interaction(9))
    assert not view.pending_settings.setu_enabled
    assert view.pending_settings.r18_mode == 0
    await view.save_config(interaction(9))
    menus._set_discord_dm_settings.assert_awaited_once()
    menus._set_discord_guild_settings.assert_not_awaited()
    menus._rotate_discord_history_epoch.assert_not_awaited()
    view.stop()


@pytest.mark.asyncio
async def test_save_error_preserves_draft_and_errors_only_ephemeral(storage):
    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    await view.toggle_ai_reply(interaction(1, server))
    menus._set_discord_guild_settings.side_effect = RuntimeError("offline")
    request = interaction(1, server)
    await view.save_config(request)
    assert not view.pending_settings.ai_reply
    assert view.saved_settings.ai_reply
    assert request.followup.send.await_args.kwargs["ephemeral"]
    view.stop()


@pytest.mark.asyncio
async def test_timeout_disables_original_ephemeral_menu_and_stops(storage):
    request = interaction(1, guild())
    view = menus.DiscordConfigView(request, storage)
    await view.on_timeout()
    assert view.is_finished()
    assert all(child.disabled for child in view.children)
    request.edit_original_response.assert_awaited_once_with(view=view)


@pytest.mark.asyncio
async def test_reload_and_open_defer_before_storage_io(storage):
    request = interaction(1, guild())

    async def load(server):
        assert request.response.done
        return storage

    menus._discord_guild_settings.side_effect = load
    await menus.open_discord_config(request)
    view = request.edit_original_response.await_args.kwargs["view"]
    view.stop()


@pytest.mark.asyncio
async def test_compact_embed_only_scope_note_notice_and_private_footer(storage):
    server = guild()
    server.name = "*Server* _test_"
    view = menus.DiscordConfigView(interaction(1, server), storage)
    view.section = "status"
    embed = view.embed()
    assert not embed.fields
    assert (
        embed.description
        == "**\\*Server\\* \\_test\\_**\nChỉ áp dụng thay đổi khi bấm **Lưu**."
    )
    assert "15 phút" in embed.footer.text and "Chỉ bạn thấy" in embed.footer.text
    await view.toggle_reply_to_bots(interaction(1, server))
    view.notice = "Đã tải lại."
    assert view.embed().description.endswith("\nĐã tải lại.")
    assert "chưa lưu" in view.embed().footer.text
    view.stop()


@pytest.mark.asyncio
async def test_concurrent_click_acknowledged_while_save_waits_and_timeout_stays_closed(
    storage,
):
    server = guild()
    origin = interaction(1, server)
    view = menus.DiscordConfigView(origin, storage)
    await view.toggle_ai_reply(interaction(1, server))
    started = asyncio.Event()
    release = asyncio.Event()

    async def save(guild, snapshot):
        started.set()
        await release.wait()

    menus._set_discord_guild_settings.side_effect = save
    saving = asyncio.create_task(view.save_config(interaction(1, server)))
    await started.wait()
    request = interaction(1, server)
    queued = asyncio.create_task(view.cancel_changes(request))
    await asyncio.sleep(0)
    assert request.response.done
    assert not queued.done()
    await view.on_timeout()
    before = origin.edit_original_response.await_count
    release.set()
    await asyncio.gather(saving, queued)
    assert view.is_finished()
    assert all(child.disabled for child in view.children)
    assert origin.edit_original_response.await_count == before
    assert request.followup.send.await_args.kwargs["ephemeral"]


@pytest.mark.asyncio
async def test_component_task_registered_for_shutdown_and_removed_when_done(storage):
    from waku.discordbot import state

    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    started = asyncio.Event()
    release = asyncio.Event()

    async def callback():
        assert await view.interaction_check(interaction(1, server))
        started.set()
        await release.wait()

    task = asyncio.create_task(callback())
    await started.wait()
    assert task in state.discord_message_tasks
    release.set()
    await task
    await asyncio.sleep(0)
    assert task not in state.discord_message_tasks
    view.stop()


@pytest.mark.asyncio
async def test_resolved_interaction_permissions_override_incomplete_or_stale_role_cache(
    storage,
):
    server = guild()
    request = interaction(2, server)
    request.permissions = discord.Permissions(administrator=True)
    assert menus._can_manage(request)
    view = menus.DiscordConfigView(request, storage)
    current = interaction(2, server, admin=True)
    current.permissions = discord.Permissions.none()
    assert not await view.interaction_check(current)
    view.stop()


@pytest.mark.asyncio
async def test_nonadmin_dm_config_is_silent_without_ack_or_storage(storage):
    request = interaction(1)
    await menus.open_discord_config(request)
    request.response.defer.assert_not_awaited()
    request.response.send_message.assert_not_awaited()
    request.followup.send.assert_not_awaited()
    request.edit_original_response.assert_not_awaited()
    menus._discord_dm_settings.assert_not_awaited()
    menus._discord_global_ai_enabled.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,global_visible", [(1, False), (9, True)])
async def test_only_botadmin_loads_and_sees_global_discord_ai_section(
    storage, user_id, global_visible
):
    request = interaction(user_id, guild())
    await menus.open_discord_config(request)
    view = request.edit_original_response.await_args.kwargs["view"]
    assert has_button(view, "toggle_global_ai") is global_visible
    assert view.pending_global_ai is (True if global_visible else None)
    assert menus._discord_global_ai_enabled.await_count == int(global_visible)
    view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("dm", [False, True])
async def test_global_ai_draft_cancel_save_and_reload_are_separate_from_local_ai(
    storage, dm
):
    server = None if dm else guild()
    request = interaction(9, server)
    view = menus.DiscordConfigView(request, storage, global_ai_enabled=True)
    await view._change(interaction(9, server), section="global_ai")
    assert button(view, "toggle_global_ai").label == "✅ AI Discord toàn bot"
    await button(view, "toggle_global_ai").callback(interaction(9, server))
    assert view.pending_global_ai is False and view.saved_global_ai is True
    assert view.pending_settings.ai_reply
    menus._set_discord_global_ai_enabled.assert_not_awaited()
    assert not next(
        button for button in view.children if getattr(button, "label", "") == "Lưu"
    ).disabled
    await view.cancel_changes(interaction(9, server))
    assert view.pending_global_ai is True and not view._dirty()
    await view.toggle_global_ai(interaction(9, server))
    await view.save_config(interaction(9, server))
    menus._set_discord_global_ai_enabled.assert_awaited_once_with(False)
    menus._set_discord_guild_settings.assert_not_awaited()
    menus._set_discord_dm_settings.assert_not_awaited()
    assert view.saved_global_ai is False and not view._dirty()
    menus._discord_global_ai_enabled.return_value = True
    await view.reload_settings(interaction(9, server))
    assert view.saved_global_ai is True and view.pending_global_ai is True
    assert view.pending_settings.ai_reply
    view.stop()


@pytest.mark.asyncio
async def test_admin_personal_dm_ai_toggle_does_not_change_global_switch(storage):
    view = menus.DiscordConfigView(interaction(9), storage, global_ai_enabled=True)
    await view.toggle_ai_reply(interaction(9))
    await view.save_config(interaction(9))
    saved = menus._set_discord_dm_settings.await_args.args[1]
    assert saved.ai_reply is False
    assert view.pending_global_ai is True
    menus._set_discord_global_ai_enabled.assert_not_awaited()
    view.stop()


@pytest.mark.asyncio
async def test_global_draft_cannot_save_after_botadmin_revocation_even_for_guild_admin(
    storage, monkeypatch
):
    server = guild()
    view = menus.DiscordConfigView(
        interaction(9, server, admin=True), storage, global_ai_enabled=True
    )
    await view.toggle_global_ai(interaction(9, server, admin=True))
    await view.toggle_ai_reply(interaction(9, server, admin=True))
    monkeypatch.setattr(menus, "_is_discord_user_bot_admin", lambda user: False)
    request = interaction(9, server, admin=True)
    await view.save_config(request)
    menus._set_discord_global_ai_enabled.assert_not_awaited()
    menus._set_discord_guild_settings.assert_not_awaited()
    assert view.saved_global_ai is True and view.pending_global_ai is False
    assert request.followup.send.await_args.kwargs["ephemeral"]
    await view.reload_settings(interaction(9, server, admin=True))
    menus._discord_global_ai_enabled.assert_not_awaited()
    assert view.pending_global_ai is None
    assert not has_button(view, "toggle_global_ai")
    view.stop()


@pytest.mark.asyncio
async def test_dm_admin_revocation_silently_blocks_existing_menu(storage, monkeypatch):
    view = menus.DiscordConfigView(interaction(9), storage, global_ai_enabled=True)
    monkeypatch.setattr(menus, "_is_discord_user_bot_admin", lambda user: False)
    request = interaction(9)
    await view.toggle_ai_reply(request)
    await view.save_config(request)
    assert view.pending_settings.ai_reply
    request.response.defer.assert_not_awaited()
    request.response.send_message.assert_not_awaited()
    request.followup.send.assert_not_awaited()
    menus._set_discord_dm_settings.assert_not_awaited()
    menus._set_discord_global_ai_enabled.assert_not_awaited()
    view.stop()


@pytest.mark.asyncio
async def test_local_commit_survives_global_save_failure_and_retry_only_writes_global(
    storage,
):
    server = guild()
    view = menus.DiscordConfigView(
        interaction(9, server), storage, global_ai_enabled=True
    )
    await view.toggle_ai_reply(interaction(9, server))
    await view.toggle_global_ai(interaction(9, server))
    menus._set_discord_global_ai_enabled.side_effect = RuntimeError(
        "global storage unavailable"
    )
    await view.save_config(interaction(9, server))
    assert view.saved_settings.ai_reply is False
    assert view.saved_global_ai is True and view.pending_global_ai is False
    assert view._dirty()
    menus._set_discord_global_ai_enabled.side_effect = None
    await view.save_config(interaction(9, server))
    menus._set_discord_guild_settings.assert_awaited_once()
    assert menus._set_discord_global_ai_enabled.await_count == 2
    assert view.saved_global_ai is False and not view._dirty()
    view.stop()


@pytest.mark.asyncio
async def test_normal_guild_admin_cannot_inject_global_section_or_toggle(storage):
    server = guild()
    view = menus.DiscordConfigView(
        interaction(1, server), storage, global_ai_enabled=True
    )
    assert view.pending_global_ai is None
    await view._change(interaction(1, server), section="global_ai")
    assert view.section == "chat"
    await view.toggle_global_ai(interaction(1, server))
    assert view.pending_global_ai is None
    menus._set_discord_global_ai_enabled.assert_not_awaited()
    view.stop()


@pytest.mark.asyncio
async def test_global_permission_rechecked_after_local_storage_await(
    storage, monkeypatch
):
    server = guild()
    view = menus.DiscordConfigView(
        interaction(9, server, admin=True), storage, global_ai_enabled=True
    )
    await view.toggle_ai_reply(interaction(9, server, admin=True))
    await view.toggle_global_ai(interaction(9, server, admin=True))

    async def save_local(guild, snapshot):
        monkeypatch.setattr(menus, "_is_discord_user_bot_admin", lambda user: False)

    menus._set_discord_guild_settings.side_effect = save_local
    await view.save_config(interaction(9, server, admin=True))
    menus._set_discord_global_ai_enabled.assert_not_awaited()
    assert view.saved_settings.ai_reply is False
    assert view.saved_global_ai is True
    assert not has_button(view, "toggle_global_ai")
    view.stop()


@pytest.mark.asyncio
async def test_expired_menu_does_not_start_global_write_after_local_save(storage):
    server = guild()
    view = menus.DiscordConfigView(
        interaction(9, server), storage, global_ai_enabled=True
    )
    await view.toggle_ai_reply(interaction(9, server))
    await view.toggle_global_ai(interaction(9, server))

    async def save_local(guild, snapshot):
        await view.on_timeout()

    menus._set_discord_guild_settings.side_effect = save_local
    await view.save_config(interaction(9, server))
    menus._set_discord_global_ai_enabled.assert_not_awaited()
    assert view.is_finished()
    assert view.saved_global_ai is True


@pytest.mark.asyncio
@pytest.mark.parametrize("dm,bot_admin", [(False, False), (False, True), (True, True)])
async def test_overview_only_native_buttons_fit_sdk_payload_rows_and_ids(
    storage, dm, bot_admin
):
    server = None if dm else guild()
    view = menus.DiscordConfigView(
        interaction(9 if bot_admin else 1, server), storage, global_ai_enabled=True
    )
    rows = view.to_components()
    assert 1 <= len(rows) <= 5
    assert sum(len(row["components"]) for row in rows) == len(view.children)
    custom_ids = []
    for row in rows:
        assert row["type"] == 1 and 1 <= len(row["components"]) <= 5
        for component in row["components"]:
            assert component["type"] == 2
            assert 1 <= len(component["label"]) <= 80
            assert 1 <= len(component["custom_id"]) <= 100
            custom_ids.append(component["custom_id"])
    assert len(custom_ids) == len(set(custom_ids))
    assert has_button(view, "toggle_reply_to_bots") is not dm
    assert has_button(view, "toggle_group_memory") is not dm
    assert has_button(view, "toggle_r18")
    assert not hasattr(view, "toggle_images") and not hasattr(view, "cycle_r18_mode")
    assert has_button(view, "toggle_global_ai") is bot_admin
    expected_controls = (4 if bot_admin else 3) if dm else (6 if bot_admin else 5)
    assert len(view.children) == expected_controls + 4
    for control in view.children:
        if control.row != 4:
            assert control.label.startswith(("✅ ", "❌ "))
            expected = (
                discord.ButtonStyle.success
                if control.label.startswith("✅ ")
                else discord.ButtonStyle.secondary
            )
            assert control.style == expected
    assert len(view.embed()) < 6000
    assert not view.embed().fields
    view.stop()


@pytest.mark.asyncio
async def test_bot_reply_button_stages_then_cancel_save_reload(storage):
    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    await button(view, "toggle_reply_to_bots").callback(interaction(1, server))
    assert view.pending_settings.reply_to_bots is True
    assert storage.reply_to_bots is False and view.saved_settings.reply_to_bots is False
    assert button(view, "toggle_reply_to_bots").label == "✅ Trả lời bot khác"
    assert button(view, "toggle_reply_to_bots").style == discord.ButtonStyle.success
    menus._set_discord_guild_settings.assert_not_awaited()
    await view.cancel_changes(interaction(1, server))
    assert view.pending_settings.reply_to_bots is False
    await view.toggle_reply_to_bots(interaction(1, server))
    await view.save_config(interaction(1, server))
    assert menus._set_discord_guild_settings.await_args.args[1].reply_to_bots is True
    assert view.saved_settings.reply_to_bots is True and not view._dirty()
    menus._rotate_discord_history_epoch.assert_not_awaited()
    await view.reload_settings(interaction(1, server))
    assert view.pending_settings.reply_to_bots is False
    assert button(view, "toggle_reply_to_bots").style == discord.ButtonStyle.secondary
    view.stop()


@pytest.mark.asyncio
async def test_bot_reply_mutation_rechecks_guild_permissions_and_is_hidden_in_dm(
    storage,
):
    server = guild()
    view = menus.DiscordConfigView(interaction(2, server, admin=True), storage)
    denied = interaction(2, server)
    await view.toggle_reply_to_bots(denied)
    assert view.pending_settings.reply_to_bots is False
    menus._set_discord_guild_settings.assert_not_awaited()
    view.stop()
    dm = menus.DiscordConfigView(interaction(9), storage)
    assert not has_button(dm, "toggle_reply_to_bots")
    request = interaction(9)
    await dm.toggle_reply_to_bots(request)
    assert dm.pending_settings == dm.saved_settings
    request.response.defer.assert_not_awaited()
    dm.stop()


@pytest.mark.asyncio
async def test_single_image_button_cycles_all_modes_then_saves_draft(storage):
    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    assert button(view, "toggle_r18").label == "✅ Ảnh: An toàn"
    observed = []
    labels = []
    for _ in range(4):
        await button(view, "toggle_r18").callback(interaction(1, server))
        observed.append(
            (view.pending_settings.setu_enabled, view.pending_settings.r18_mode)
        )
        labels.append(button(view, "toggle_r18").label)
        assert not button(view, "toggle_r18").disabled
    assert observed == [(True, 1), (True, 2), (False, 0), (True, 0)]
    assert labels == [
        "✅ Ảnh: Chỉ R18",
        "✅ Ảnh: Cả hai",
        "❌ Ảnh: Tắt",
        "✅ Ảnh: An toàn",
    ]
    menus._set_discord_guild_settings.assert_not_awaited()
    await button(view, "toggle_r18").callback(interaction(1, server))
    await view.save_config(interaction(1, server))
    saved = menus._set_discord_guild_settings.await_args.args[1]
    assert saved.setu_enabled and saved.r18_mode == 1 and not view._dirty()
    view.stop()


@pytest.mark.asyncio
async def test_dm_single_image_button_cycles_only_safe_and_off(storage):
    view = menus.DiscordConfigView(interaction(9), storage)
    assert button(view, "toggle_r18").label == "✅ Ảnh: An toàn"
    await button(view, "toggle_r18").callback(interaction(9))
    assert button(view, "toggle_r18").label == "❌ Ảnh: Tắt"
    assert view.pending_settings.r18_mode == 0
    await button(view, "toggle_r18").callback(interaction(9))
    assert button(view, "toggle_r18").label == "✅ Ảnh: An toàn"
    assert view.pending_settings.r18_mode == 0
    menus._set_discord_dm_settings.assert_not_awaited()
    view.stop()


@pytest.mark.asyncio
async def test_compact_menu_preserves_previously_saved_enabled_bot_reply(storage):
    storage.reply_to_bots = True
    storage.setu_enabled = False
    storage.r18_mode = 2
    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    assert button(view, "toggle_reply_to_bots").label == "✅ Trả lời bot khác"
    assert button(view, "toggle_r18").label == "❌ Ảnh: Tắt"
    assert not view._dirty()
    await view.toggle_lang(interaction(1, server))
    await view.save_config(interaction(1, server))
    saved = menus._set_discord_guild_settings.await_args.args[1]
    assert (
        saved.reply_to_bots is True
        and saved.setu_enabled is False
        and saved.r18_mode == 2
    )
    view.stop()


@pytest.fixture
async def native_menu_transport(storage):
    client = discord.Client(intents=discord.Intents.none())
    sdk_state = client._connection
    sdk_state.user = discord.ClientUser(state=sdk_state, data=user(900, bot=True))
    sdk_state._add_guild_from_data(
        {
            "id": "10",
            "name": "Server",
            "owner_id": "1",
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
    adapter = Adapter()
    context_token = async_context.set(adapter)

    def request(*, component=False):
        data = {
            "id": str(discord.utils.time_snowflake(datetime.now(UTC))),
            "type": 3 if component else 2,
            "token": "offline-menu",
            "version": 1,
            "application_id": "900",
            "attachment_size_limit": 8388608,
            "guild_id": "10",
            "channel": {"id": "20", "type": 0, "name": "general"},
            "member": {
                "user": user(9),
                "roles": [],
                "permissions": "0",
                "joined_at": datetime.now(UTC).isoformat(),
                "flags": 0,
            },
            "data": {"custom_id": "test-button", "component_type": 2}
            if component
            else {"id": "1", "name": "config", "type": 1},
        }
        if component:
            data["message"] = {
                "id": "999",
                "type": 0,
                "content": "",
                "flags": 64,
                "author": user(900, bot=True),
            }
        return discord.Interaction(data=data, state=sdk_state)

    yield SimpleNamespace(client=client, request=request, adapter=adapter)
    views = {
        item.view
        for dispatch in sdk_state._view_store._views.values()
        for item in dispatch.values()
    }
    for view in views:
        view.stop()
    async_context.reset(context_token)
    await client.close()


@pytest.mark.asyncio
async def test_native_interaction_button_payload_draft_save_cancel_reload(
    native_menu_transport, storage
):
    transport = native_menu_transport
    await menus.open_discord_config(transport.request())
    assert transport.adapter.acks[0] == {"type": 5, "data": {"flags": 64}}
    dispatch = transport.client._connection._view_store._views[999]
    view = next(iter(dispatch.values())).view
    assert isinstance(view, menus.DiscordConfigView)
    payload = transport.adapter.edits[-1]
    assert len(payload["components"]) == 4
    assert not payload["embeds"][0].get("fields")
    assert all(
        component["type"] == 2
        for row in payload["components"]
        for component in row["components"]
    )
    await button(view, "toggle_reply_to_bots").callback(
        transport.request(component=True)
    )
    assert view.pending_settings.reply_to_bots is True
    menus._set_discord_guild_settings.assert_not_awaited()
    await button(view, "cancel_changes").callback(transport.request(component=True))
    assert view.pending_settings.reply_to_bots is False
    await button(view, "toggle_reply_to_bots").callback(
        transport.request(component=True)
    )
    await button(view, "save_config").callback(transport.request(component=True))
    saved = menus._set_discord_guild_settings.await_args.args[1]
    assert saved.reply_to_bots is True and saved is not view.pending_settings
    assert not view._dirty()
    await button(view, "reload_settings").callback(transport.request(component=True))
    assert view.pending_settings.reply_to_bots is False
    assert all(ack["type"] == 6 for ack in transport.adapter.acks[1:])
    assert all(payload.get("content") is None for payload in transport.adapter.edits)
    await button(view, "close_menu").callback(transport.request(component=True))
    assert view.is_finished() and not transport.adapter.edits[-1]["components"]
