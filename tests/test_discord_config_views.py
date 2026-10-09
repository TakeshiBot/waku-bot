"""Real Discord views with mocked interaction HTTP/storage boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

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
        assert view.children[0].options[0].label == "AI / trò chuyện"
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
    await view.children[1].callback(interaction(1, server))
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
async def test_select_sections_and_language_change_native_controls(storage):
    server = guild()
    view = menus.DiscordConfigView(interaction(1, server), storage)
    select = view.children[0]
    select._values = ["language"]
    await select.callback(interaction(1, server))
    assert view.section == "language"
    await view.children[1].callback(interaction(1, server))
    assert view.pending_settings.lang == "en"
    assert view.embed().title == "Waku settings"
    assert view.children[0].options[0].label == "AI / chat"
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
async def test_status_section_uses_actual_current_config(storage):
    view = menus.DiscordConfigView(interaction(1, guild()), storage)
    view.section = "status"
    assert "Trạng thái" in view.embed().fields[1].name
    assert len(view.embed()) < 6000
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
    options = [option.value for option in view.children[0].options]
    assert ("global_ai" in options) is global_visible
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
    assert view.embed().fields[1].name == "AI Discord toàn bot"
    await view.children[1].callback(interaction(9, server))
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
    assert "global_ai" not in [option.value for option in view.children[0].options]
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
    assert "global_ai" not in [option.value for option in view.children[0].options]
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
