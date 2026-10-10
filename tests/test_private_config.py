"""Admin menu permissions, stale callbacks and private input isolation."""

import ast
import asyncio
import html
import inspect
import os
import secrets
import signal
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pyrogram
import pytest
from pyrogram import enums
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    User,
)
from test_settings_editor import schemas  # noqa: F401

from waku.i18n import t
from waku.services.settings_editor import (
    SettingsEditError,
    SettingsEditor,
    display_value,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def panel(tmp_path, schemas, monkeypatch):  # noqa: F811
    path = tmp_path / "settings.toml"
    path.write_text(
        'token="test"\nowners=[1]\nagent_prompt="before"\n'
        'business_chat_enabled=false\n'
    )
    schema, provider = schemas
    runtime = schema(token="test", owners=[1])
    editor = SettingsEditor([path], schema, provider, runtime.model_dump)
    runtime_module = ModuleType("waku.services.settings_runtime")

    async def prepare(candidate, changed):
        roots = {key.split(".", 1)[0] for key in changed}
        restart = roots & {"debug", "token", "db_url"}

        def apply():
            for key in roots - restart:
                setattr(runtime, key, getattr(candidate, key))

        return SimpleNamespace(
            apply=apply,
            activate=AsyncMock(side_effect=apply),
            discord_status=None,
            restart_fields=restart,
            live_fields=roots - restart,
            discard=AsyncMock(),
        )

    runtime_module.prepare_settings_application = AsyncMock(side_effect=prepare)
    runtime_module.SETTINGS_APPLICATION_LOCK = asyncio.Lock()
    runtime_module.settings_restart_fields = lambda candidate, changed: (
        changed & {"debug", "token", "db_url"}
    )
    monkeypatch.setitem(sys.modules, runtime_module.__name__, runtime_module)
    namespace = {
        "__name__": "__main__",
        "asyncio": asyncio,
        "SETTINGS_APPLICATION_LOCK": runtime_module.SETTINGS_APPLICATION_LOCK,
        "html": html,
        "os": os,
        "secrets": secrets,
        "signal": signal,
        "time": time,
        "dataclass": dataclass,
        "field": field,
        "Path": Path,
        "enums": enums,
        "t": t,
        "MessageNotModified": MessageNotModified,
        "InlineKeyboardButton": InlineKeyboardButton,
        "InlineKeyboardMarkup": InlineKeyboardMarkup,
        "LinkPreviewOptions": LinkPreviewOptions,
        "SettingsEditor": SettingsEditor,
        "SettingsEditError": SettingsEditError,
        "display_value": display_value,
        "_AppConfig": schema,
        "ProviderConfig": provider,
        "app_config": runtime,
        "database": SimpleNamespace(
            get_user_by_id=AsyncMock(return_value=None),
            get_user_config=AsyncMock(return_value=SimpleNamespace(lang="vi")),
        ),
        "common": SimpleNamespace(
            spawn=MagicMock(), message_plain_text=lambda message: message.text
        ),
        "logger": MagicMock(),
        "schedule_saved_menu_cleanup": MagicMock(),
        "BOT_TIMEZONE": ZoneInfo("Asia/Ho_Chi_Minh"),
        "_resolve_settings_files": lambda: [str(path)],
    }
    tree = ast.parse(
        (ROOT / "waku/plugins/private_config.py").read_text(encoding="utf-8")
    )
    nodes = []
    for node in tree.body:
        if isinstance(
            node,
            (
                ast.ClassDef,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.Assign,
                ast.AnnAssign,
            ),
        ):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.decorator_list = []
            nodes.append(node)
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[future, *nodes], type_ignores=[])
            ),
            "private-config",
            "exec",
        ),
        namespace,
    )
    session = namespace["ConfigSession"](1, 1, 99, "vi", editor)
    namespace["_SESSIONS"]["test"] = session
    message = SimpleNamespace(
        chat=SimpleNamespace(id=1, type=enums.ChatType.PRIVATE),
        id=99,
        edit_text=AsyncMock(),
        reply_text=AsyncMock(return_value=SimpleNamespace(id=99)),
        from_user=SimpleNamespace(id=1),
        text="",
        delete=AsyncMock(),
        stop_propagation=MagicMock(side_effect=pyrogram.StopPropagation),
    )
    client = SimpleNamespace(edit_message_text=AsyncMock())
    return SimpleNamespace(
        ns=namespace, editor=editor, session=session, message=message, client=client
    )


def query(panel, action, *, user=1, chat=1, view=None):
    panel.message.chat.id = chat
    return SimpleNamespace(
        data=f"pcfg:test:{panel.session.view if view is None else view}:{action}",
        from_user=SimpleNamespace(id=user),
        message=panel.message,
        answer=AsyncMock(),
    )


@pytest.mark.parametrize("user,chat", [(2, 1), (1, 2)])
async def test_callback_owner_and_chat_binding(panel, user, chat):
    request = query(panel, "add", user=user, chat=chat)
    await panel.ns["private_config_callback"](panel.client, request)
    assert panel.session.pending is None
    request.answer.assert_awaited_once()


async def test_callback_rechecks_revoked_admin(panel):
    panel.ns["app_config"].owners = []
    request = query(panel, "add")
    await panel.ns["private_config_callback"](panel.client, request)
    assert "test" not in panel.ns["_SESSIONS"]


async def test_callback_old_view_cannot_edit_different_entry(panel):
    with panel.editor.path.open("a") as target:
        target.write("\nbtts=false\n")
    panel.ns["_menu"]("test", panel.session, "group:agent:0")
    old_view = panel.session.view
    panel.ns["_menu"]("test", panel.session, "group:services:0")
    request = query(panel, "edit:0", view=old_view)
    await panel.ns["private_config_callback"](panel.client, request)
    assert panel.session.pending is None


async def test_toggle_stages_until_save_and_answers_callback_once(panel):
    panel.session.entries = ["debug"]
    values, panel.session.revision = panel.editor.snapshot()
    request = query(panel, "toggle:0")
    await panel.ns["private_config_callback"](panel.client, request)
    assert panel.editor.snapshot()[0]["debug"] is values["debug"]
    assert panel.session.values["debug"] is not values["debug"]
    request.answer.assert_awaited_once()
    save_request = query(panel, "save")
    prepare = sys.modules["waku.services.settings_runtime"].prepare_settings_application
    original_prepare = prepare.side_effect

    async def prepare_after_ack(candidate, changed):
        # A slow Discord reconnect must not leave an unanswered callback.
        save_request.answer.assert_awaited_once()
        assert panel.editor.snapshot()[0]["debug"] is values["debug"]
        return await original_prepare(candidate, changed)

    prepare.side_effect = prepare_after_ack
    await panel.ns["private_config_callback"](panel.client, save_request)
    assert panel.editor.snapshot()[0]["debug"] is not values["debug"]
    assert panel.session.dirty == {"debug"}
    assert not panel.session.changes


async def test_failed_edit_instructs_reopen_and_has_no_redundant_buttons(panel):
    panel.session.entries = ["debug"]
    panel.session.revision = "stale"
    request = query(panel, "toggle:0")
    await panel.ns["private_config_callback"](panel.client, request)
    markup = panel.message.edit_text.call_args.kwargs["reply_markup"]
    actions = [
        b.callback_data.rsplit(":", 1)[-1]
        for row in markup.inline_keyboard
        for b in row
    ]
    assert "save" in actions
    assert not set(actions) & {"reload", "close", "discard"}
    assert "/config" in panel.message.edit_text.call_args.args[0]


async def test_input_staged_and_stopped_before_ai_even_with_slash(panel):
    panel.session.pending = "agent_prompt"
    panel.session.pending_until = time.monotonic() + 120
    panel.session.revision = panel.editor.snapshot()[1]
    panel.message.text = "/root/absolute/path\nnew prompt"
    with pytest.raises(pyrogram.StopPropagation):
        await panel.ns["private_config_input"](panel.client, panel.message)
    assert panel.editor.snapshot()[0]["agent_prompt"] == "before"
    assert panel.session.values["agent_prompt"] == panel.message.text
    panel.message.delete.assert_awaited_once()
    assert panel.session.pending is None
    await panel.ns["private_config_callback"](panel.client, query(panel, "save"))
    assert panel.editor.snapshot()[0]["agent_prompt"] == panel.message.text
    assert panel.ns["app_config"].agent_prompt == panel.message.text


async def test_secret_input_error_contains_no_value_and_is_consumed(panel):
    panel.session.pending = "owners"
    panel.session.pending_until = time.monotonic() + 120
    panel.session.revision = panel.editor.snapshot()[1]
    panel.message.text = '["private-secret"]'
    with pytest.raises(pyrogram.StopPropagation):
        await panel.ns["private_config_input"](panel.client, panel.message)
    assert "private-secret" not in str(panel.client.edit_message_text.call_args)
    panel.message.delete.assert_awaited_once()


async def test_pending_expiry_does_not_send_old_value_to_ai(panel):
    panel.session.pending = "agent_prompt"
    panel.session.pending_until = time.monotonic() - 1
    panel.message.text = "old-secret"
    with pytest.raises(pyrogram.StopPropagation):
        await panel.ns["private_config_input"](panel.client, panel.message)
    assert panel.editor.snapshot()[0]["agent_prompt"] == "before"
    panel.message.delete.assert_awaited_once()


async def test_regular_dm_is_not_consumed(panel):
    panel.message.text = "hello"
    await panel.ns["private_config_input"](panel.client, panel.message)
    panel.message.stop_propagation.assert_not_called()


async def test_cancel_is_consumed_without_saving(panel):
    panel.session.pending = "agent_prompt"
    panel.message.text = "/cancel"
    with pytest.raises(pyrogram.StopPropagation):
        await panel.ns["private_config_input"](panel.client, panel.message)
    assert panel.editor.snapshot()[0]["agent_prompt"] == "before"


async def test_restart_requires_current_confirmation(panel):
    request = query(panel, "restart_confirm")
    await panel.ns["private_config_callback"](panel.client, request)
    panel.ns["common"].spawn.assert_not_called()


def test_secrets_are_masked_and_markup_contains_no_values(panel):
    panel.session.entries = ["token"]
    text, markup = panel.ns["_menu"]("test", panel.session, "entry:0")
    assert '"test"' not in text
    assert "••••••" in text
    assert all(
        len(button.callback_data.encode()) <= 64
        for row in markup.inline_keyboard
        for button in row
    )


def test_input_handler_precedes_debug_logging_middleware():
    tree = ast.parse(
        (ROOT / "waku/plugins/private_config.py").read_text(encoding="utf-8")
    )
    handler = next(
        node
        for node in tree.body
        if getattr(node, "name", None) == "private_config_input"
    )
    registration = handler.decorator_list[0]
    group = next(kw.value for kw in registration.keywords if kw.arg == "group")
    assert ast.literal_eval(group) < -100


async def test_restart_requests_normal_sigint_shutdown(panel, monkeypatch):
    from waku.services import process_restart

    monkeypatch.setattr(process_restart, "_requested", False)
    sleep = AsyncMock()
    kill = MagicMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(os, "kill", kill)
    await panel.ns["_restart"]()
    kill.assert_called_once_with(os.getpid(), signal.SIGINT)
    assert process_restart._requested


@pytest.fixture
def native_panel(panel):
    """Run real Kurigram bound methods; validate every client IO signature."""
    client = SimpleNamespace()
    chat = Chat(id=1, type=enums.ChatType.PRIVATE)
    owner = User(id=1, first_name="Owner")
    menu = Message(
        client=client,
        id=99,
        chat=chat,
        from_user=User(id=900, first_name="Waku", is_bot=True),
        outgoing=True,
    )

    def check(method, args, kwargs):
        inspect.signature(getattr(pyrogram.Client, method)).bind(None, *args, **kwargs)

    async def send(*args, **kwargs):
        check("send_message", args, kwargs)
        menu.text = kwargs["text"]
        menu.reply_markup = kwargs["reply_markup"]
        return menu

    async def edit(*args, **kwargs):
        check("edit_message_text", args, kwargs)
        assert kwargs["chat_id"] == chat.id
        assert kwargs["message_id"] == menu.id
        menu.text = kwargs["text"]
        menu.reply_markup = kwargs["reply_markup"]
        return menu

    async def answer(*args, **kwargs):
        check("answer_callback_query", args, kwargs)
        return True

    async def delete(*args, **kwargs):
        check("delete_messages", args, kwargs)
        return 1

    client.send_message = AsyncMock(side_effect=send)
    client.edit_message_text = AsyncMock(side_effect=edit)
    client.answer_callback_query = AsyncMock(side_effect=answer)
    client.delete_messages = AsyncMock(side_effect=delete)
    panel.client = client
    panel.message = menu
    panel.owner = owner
    panel.command = Message(
        client=client, id=7, chat=chat, from_user=owner, text="/config"
    )

    async def click(action, *, raw_bytes=False):
        button = next(
            button
            for row in menu.reply_markup.inline_keyboard
            for button in row
            if button.callback_data.split(":", 3)[3] == action
        )
        request = CallbackQuery(
            client=client,
            id="query",
            from_user=owner,
            message=menu,
            data=button.callback_data.encode() if raw_bytes else button.callback_data,
        )
        await panel.ns["private_config_callback"](client, request)

    panel.click = click
    return panel


async def open_native_panel(panel):
    await panel.ns["private_config_command"](panel.client, panel.command)
    panel.token, panel.session = next(iter(panel.ns["_SESSIONS"].items()))
    assert panel.session.message_id == 99  # Bind outgoing menu, not command ID 7.


async def test_native_command_first_and_second_tap_and_entry_edit(native_panel):
    panel = native_panel
    await open_native_panel(panel)
    root_rows = panel.message.reply_markup.inline_keyboard
    assert [
        button.callback_data.split(":", 3)[3] for row in root_rows for button in row
    ] == ["groups:0", "save"]
    assert root_rows[0][0].text == "⚙️ Cài đặt"
    await panel.click("groups:0", raw_bytes=True)
    assert (
        len(panel.message.reply_markup.inline_keyboard)
        == len(panel.ns["_visible_groups"](panel.session)) + 1
    )
    await panel.click("group:base:0")
    assert all(len(row) <= 2 for row in panel.message.reply_markup.inline_keyboard[:-1])
    non_boolean = next(
        button.callback_data.split(":", 3)[3]
        for row in panel.message.reply_markup.inline_keyboard[:-1]
        for button in row
        if button.callback_data.split(":", 3)[3].startswith("entry:")
    )
    index = int(non_boolean.split(":")[1])
    key = panel.session.entries[index]
    await panel.click(non_boolean)
    assert key in panel.message.text
    await panel.click(f"edit:{index}")
    assert panel.session.pending == key
    assert key in panel.message.text
    assert panel.client.edit_message_text.await_count == 4
    assert all(
        call.kwargs["link_preview_options"].is_disabled
        for call in panel.client.edit_message_text.await_args_list
    )
    panel.ns["logger"].error.assert_not_called()


@pytest.mark.parametrize("page", [0, 1])
async def test_native_provider_field_back_returns_to_provider_then_list(
    native_panel, page
):
    panel = native_panel
    if page:
        for index in range(8):
            panel.editor.add_provider(f"provider_{index}", panel.editor.snapshot()[1])
    await open_native_panel(panel)
    await panel.click("groups:0")
    await panel.click("providers:0")
    if page:
        await panel.click("providers:1")
    await panel.click(f"provider:{page * panel.ns['_PAGE_SIZE']}")
    assert panel.session.back == f"providers:{page}"
    await panel.click("entry:0")
    assert panel.session.back == "provider_view"
    await panel.click("provider_view")
    assert panel.session.back == f"providers:{page}"
    await panel.click(f"providers:{page}")
    assert len(panel.session.entries) == (9 if page else 1)
    assert panel.session.back == "groups:0"
    panel.ns["logger"].error.assert_not_called()


async def test_native_navigation_failure_keeps_visible_keyboard_usable(native_panel):
    panel = native_panel
    await open_native_panel(panel)
    view = panel.session.view
    edit = panel.client.edit_message_text.side_effect
    panel.client.edit_message_text.side_effect = TimeoutError("network unavailable")
    await panel.click("groups:0")
    assert panel.session.view == view
    assert panel.session.location == "home"
    assert panel.session.entries == []
    panel.client.edit_message_text.side_effect = edit
    await panel.click("groups:0")
    assert panel.session.location == "groups:0"
    assert panel.session.view > view
    assert all(
        call.kwargs["text"] is None
        for call in panel.client.answer_callback_query.await_args_list
    )


async def test_native_direct_toggle_preserves_group_page_and_shows_changed(
    native_panel,
):
    panel = native_panel
    with panel.editor.path.open("a") as target:
        target.write("\ndebug=false\n")
    await open_native_panel(panel)
    keys = [
        key for key in panel.session.field_keys if panel.ns["_group"](key) == "base"
    ]
    page = keys.index("debug") // panel.ns["_PAGE_SIZE"]
    text, markup = panel.ns["_menu"](panel.token, panel.session, f"group:base:{page}")
    panel.message.text, panel.message.reply_markup = text, markup
    prior = panel.editor.snapshot()[0]["debug"]
    await panel.click(f"toggle:{keys.index('debug')}")
    assert panel.editor.snapshot()[0]["debug"] is prior
    assert panel.session.values["debug"] is not prior
    assert panel.session.location == f"group:base:{page}"
    assert "📝" in panel.message.text
    assert any(
        button.callback_data.endswith(":save")
        for button in panel.message.reply_markup.inline_keyboard[-1]
    )
    panel.ns["logger"].error.assert_not_called()


@pytest.mark.parametrize("edit_fails", [False, True])
async def test_native_save_closes_session_and_schedules_exact_message_cleanup(
    native_panel, edit_fails
):
    panel = native_panel
    await open_native_panel(panel)
    if edit_fails:
        panel.client.edit_message_text.side_effect = RuntimeError("cannot edit")
    await panel.click("save")
    assert panel.token not in panel.ns["_SESSIONS"]
    panel.client.delete_messages.assert_not_awaited()
    panel.ns["schedule_saved_menu_cleanup"].assert_called_once_with(
        panel.client, panel.message, panel.command.id
    )
    if not edit_fails:
        assert panel.message.reply_markup is None
        assert panel.message.text == panel.ns["_tr"]("saved", panel.session)
    panel.client.answer_callback_query.assert_awaited_once()


async def test_business_group_declared_in_file_preserves_provider_page(
    native_panel,
):
    panel = native_panel
    await open_native_panel(panel)
    await panel.click("groups:0")
    actions = [
        button.callback_data.split(":", 3)[3]
        for row in panel.message.reply_markup.inline_keyboard
        for button in row
    ]
    assert "providers:0" in actions
    await panel.click("group:business:0")
    assert "Telegram Business" in panel.message.text
    assert panel.session.entries == ["business_chat_enabled"]
    assert panel.editor.snapshot()[0]["business_chat_enabled"] is False
    assert panel.session.back == "groups:0"
    await panel.click("groups:0")
    assert any(
        button.callback_data.endswith(":group:business:0")
        for row in panel.message.reply_markup.inline_keyboard
        for button in row
    )


async def test_business_native_toggle_applies_and_persists_without_restart(
    native_panel,
):
    panel = native_panel
    panel.ns["app_config"].agent = True
    panel.ns["app_config"].agent_model = "default/example"
    await open_native_panel(panel)
    text, markup = panel.ns["_menu"](panel.token, panel.session, "group:business:0")
    panel.message.text, panel.message.reply_markup = text, markup
    for expected in (True, False):
        await open_native_panel(panel)
        text, markup = panel.ns["_menu"](panel.token, panel.session, "group:business:0")
        panel.message.text, panel.message.reply_markup = text, markup
        await panel.click("toggle:0")
        assert panel.ns["app_config"].business_chat_enabled is not expected
        await panel.click("save")
        assert panel.ns["app_config"].business_chat_enabled is expected
        assert (
            tomllib.loads(panel.editor.path.read_text())["business_chat_enabled"]
            is expected
        )
        assert "business_chat_enabled" not in panel.session.dirty
        assert panel.token not in panel.ns["_SESSIONS"]
        assert panel.message.reply_markup is None
        assert not panel.session.changes
    panel.ns["logger"].error.assert_not_called()


async def test_business_toggle_reports_missing_agent(panel):
    panel.ns["app_config"].agent = False
    panel.ns["_menu"]("test", panel.session, "group:business:0")
    request = query(panel, "toggle:0")
    await panel.ns["private_config_callback"](panel.client, request)
    assert panel.ns["app_config"].business_chat_enabled is False
    await panel.ns["private_config_callback"](panel.client, query(panel, "save"))
    assert panel.ns["app_config"].business_chat_enabled is True
    assert (
        panel.ns["_tr"]("business_requires_agent", panel.session)
        in panel.message.edit_text.call_args.args[0]
    )


@pytest.mark.parametrize("error", ["stale_file", "write_failed"])
async def test_business_failed_save_does_not_enable_runtime(panel, monkeypatch, error):
    panel.ns["_menu"]("test", panel.session, "group:business:0")
    await panel.ns["private_config_callback"](panel.client, query(panel, "toggle:0"))
    if error == "stale_file":
        with panel.editor.path.open("a") as target:
            target.write("\n# external edit\n")
    else:

        def fail_write(*args, **kwargs):
            raise SettingsEditError("write_failed")

        monkeypatch.setattr(panel.editor, "commit_batch", fail_write)
    request = query(panel, "save")
    await panel.ns["private_config_callback"](panel.client, request)
    assert panel.ns["app_config"].business_chat_enabled is False
    assert panel.editor.snapshot()[0]["business_chat_enabled"] is False
    assert "business_chat_enabled" not in panel.session.dirty


async def test_business_typed_switch_also_applies_live_and_consumes_input(panel):
    panel.session.pending = "business_chat_enabled"
    panel.session.pending_until = time.monotonic() + 120
    panel.session.revision = panel.editor.snapshot()[1]
    panel.message.text = "true"
    with pytest.raises(pyrogram.StopPropagation):
        await panel.ns["private_config_input"](panel.client, panel.message)
    assert panel.ns["app_config"].business_chat_enabled is False
    assert panel.editor.snapshot()[0]["business_chat_enabled"] is False
    await panel.ns["private_config_callback"](panel.client, query(panel, "save"))
    assert panel.ns["app_config"].business_chat_enabled is True
    assert panel.editor.snapshot()[0]["business_chat_enabled"] is True
    assert "business_chat_enabled" not in panel.session.dirty


async def test_discard_and_close_never_commit_staged_values(panel):
    panel.ns["_menu"]("test", panel.session, "group:business:0")
    await panel.ns["private_config_callback"](panel.client, query(panel, "toggle:0"))
    await panel.ns["private_config_callback"](panel.client, query(panel, "discard"))
    assert not panel.session.changes
    assert panel.editor.snapshot()[0]["business_chat_enabled"] is False
    panel.ns["_menu"]("test", panel.session, "group:business:0")
    await panel.ns["private_config_callback"](panel.client, query(panel, "toggle:0"))
    await panel.ns["private_config_callback"](panel.client, query(panel, "close"))
    assert panel.editor.snapshot()[0]["business_chat_enabled"] is False
    assert panel.ns["app_config"].business_chat_enabled is False


async def test_queued_save_rechecks_role_after_lock(panel):
    panel.ns["_menu"]("test", panel.session, "group:business:0")
    await panel.ns["private_config_callback"](panel.client, query(panel, "toggle:0"))
    await panel.session.lock.acquire()
    try:
        task = asyncio.create_task(
            panel.ns["private_config_callback"](panel.client, query(panel, "save"))
        )
        await asyncio.sleep(0)
        panel.ns["app_config"].owners = []
    finally:
        panel.session.lock.release()
    await task
    assert panel.editor.snapshot()[0]["business_chat_enabled"] is False
    assert "test" not in panel.ns["_SESSIONS"]


async def test_queued_save_cannot_resurrect_a_closed_draft(panel):
    panel.ns["_menu"]("test", panel.session, "group:business:0")
    await panel.ns["private_config_callback"](panel.client, query(panel, "toggle:0"))
    await panel.session.lock.acquire()
    try:
        task = asyncio.create_task(
            panel.ns["private_config_callback"](panel.client, query(panel, "save"))
        )
        await asyncio.sleep(0)
        panel.ns["_SESSIONS"].pop("test")
    finally:
        panel.session.lock.release()
    await task
    assert panel.editor.snapshot()[0]["business_chat_enabled"] is False


async def test_global_save_lock_rechecks_admin_when_another_menu_revokes_owner(panel):
    panel.ns["_menu"]("test", panel.session, "group:business:0")
    await panel.ns["private_config_callback"](panel.client, query(panel, "toggle:0"))
    lock = panel.ns["_SETTINGS_SAVE_LOCK"]
    await lock.acquire()
    try:
        task = asyncio.create_task(
            panel.ns["private_config_callback"](panel.client, query(panel, "save"))
        )
        await asyncio.sleep(0)
        panel.ns["app_config"].owners = []
    finally:
        lock.release()
    await task
    assert panel.editor.snapshot()[0]["business_chat_enabled"] is False
    assert panel.ns["app_config"].business_chat_enabled is False


async def test_provider_add_edit_delete_are_all_staged_and_saved_together(panel):
    await panel.ns["_stage_provider"](panel.session, "local")
    await panel.ns["_stage_setting"](
        panel.session, "agent_providers.local.url", "https://example.invalid/v1"
    )
    await panel.ns["_stage_setting"](panel.session, "agent_model", "local/example")
    assert "local" not in panel.editor.snapshot()[0]["agent_providers"]
    await panel.ns["private_config_callback"](panel.client, query(panel, "save"))
    assert panel.editor.snapshot()[0]["agent_model"] == "local/example"
    await panel.ns["private_config_command"](panel.client, panel.message)
    token, panel.session = next(iter(panel.ns["_SESSIONS"].items()))
    # This non-native unit fixture uses a fixed token for its callback helper.
    panel.ns["_SESSIONS"]["test"] = panel.ns["_SESSIONS"].pop(token)
    await panel.ns["_stage_setting"](panel.session, "agent_model", "default/example")
    await panel.ns["_stage_provider"](panel.session, "local", delete=True)
    assert "local" in panel.editor.snapshot()[0]["agent_providers"]
    await panel.ns["private_config_callback"](panel.client, query(panel, "save"))
    assert "local" not in panel.editor.snapshot()[0]["agent_providers"]


async def test_runtime_preparation_failure_preserves_file_and_masks_diagnostics(panel):
    panel.ns["_menu"]("test", panel.session, "group:business:0")
    await panel.ns["private_config_callback"](panel.client, query(panel, "toggle:0"))
    runtime = sys.modules["waku.services.settings_runtime"]
    runtime.prepare_settings_application.side_effect = RuntimeError(
        "private-provider-secret"
    )
    before = panel.editor.path.read_bytes()
    await panel.ns["private_config_callback"](panel.client, query(panel, "save"))
    assert panel.editor.path.read_bytes() == before
    assert panel.ns["app_config"].business_chat_enabled is False
    assert panel.session.changes
    assert "private-provider-secret" not in str(panel.message.edit_text.call_args)


def test_menu_only_shows_configured_fields(panel):
    panel.ns["_menu"]("test", panel.session, "group:agent:0")
    assert panel.session.entries == ["agent_prompt"]
    assert "agent_rich_output" not in panel.session.entries
    assert "agent_landrun_path" not in panel.session.entries
    groups = panel.ns["_visible_groups"](panel.session)
    assert groups == ["base", "agent", "business", "providers"]
    with pytest.raises(SettingsEditError):
        panel.ns["_menu"]("test", panel.session, "group:services:0")


def test_removed_business_setting_hides_its_menu_group(panel):
    panel.editor.path.write_text('token="test"\nowners=[1]\nagent_prompt="before"\n')
    panel.ns["_menu"]("test", panel.session, "groups:0")
    assert "business" not in panel.ns["_visible_groups"](panel.session)
    assert "business_chat_enabled" not in panel.session.field_keys


def test_example_settings_menu_matches_file_fields_and_order(panel):
    panel.editor.path.write_text((ROOT / "settings.ex.toml").read_text())
    document = tomllib.loads(panel.editor.path.read_text())
    panel.ns["_menu"]("test", panel.session, "groups:0")
    assert panel.session.field_keys == list(document)
    assert panel.ns["_visible_groups"](panel.session) == [
        "base", "agent", "business", "webapp", "discord", "services", "providers"
    ]
    displayed = []
    for group in panel.ns["_visible_groups"](panel.session):
        if group == "providers":
            continue
        panel.ns["_menu"]("test", panel.session, f"group:{group}:0")
        assert panel.session.entries == [
            key for key in document if key != "agent_providers" and panel.ns["_group"](key) == group
        ]
        displayed.extend(panel.session.entries)
    assert set(displayed) == set(document) - {"agent_providers"}
    assert not {"agent_sticker_search_mode", "agent_small_model_timeout", "webapp_jwt_ttl"} & set(displayed)
    panel.ns["_menu"]("test", panel.session, "providers:0")
    text, markup = panel.ns["_menu"]("test", panel.session, "provider:0")
    assert panel.session.entries == ["agent_providers.default.url", "agent_providers.default.key", "agent_providers.default.type"]
    assert "api_type" not in text and "proxy" not in text
    assert all(len(button.callback_data.encode()) <= 64 for row in markup.inline_keyboard for button in row)


def test_explicit_advanced_setting_remains_editable_in_its_declared_order(panel):
    panel.editor.path.write_text(
        'token="test"\nowners=[1]\nagent_prompt="before"\n'
        'agent_small_model_timeout=50\n[agent_providers.default]\n'
        'key="secret-key"\nurl="https://example.test/v1"\nproxy="http://proxy.test"\n'
    )
    panel.ns["_menu"]("test", panel.session, "group:agent:0")
    assert panel.session.entries == ["agent_prompt", "agent_small_model_timeout"]
    panel.ns["_menu"]("test", panel.session, "providers:0")
    text, _ = panel.ns["_menu"]("test", panel.session, "provider:0")
    assert panel.session.entries == ["agent_providers.default.key", "agent_providers.default.url", "agent_providers.default.proxy"]
    assert "secret-key" not in text and "proxy.test" not in text


async def test_rich_only_private_edit_is_consumed_without_saving_or_leaking_to_ai(
    panel,
):
    from pyrogram.types import RichBlockParagraph, RichMessage

    from waku.common.rich_message import message_plain_text

    panel.ns["common"].message_plain_text = message_plain_text
    panel.session.pending = "token"
    panel.session.pending_until = time.monotonic() + 120
    panel.message = Message(
        id=23,
        chat=Chat(id=1, type=enums.ChatType.PRIVATE),
        from_user=User(id=1, first_name="Admin"),
        rich_message=RichMessage(
            blocks=[RichBlockParagraph(text="private-token-value")]
        ),
    )
    panel.message.delete = AsyncMock()
    panel.message.stop_propagation = MagicMock(side_effect=pyrogram.StopPropagation)
    with pytest.raises(pyrogram.StopPropagation):
        await panel.ns["private_config_input"](panel.client, panel.message)
    assert panel.editor.snapshot()[0]["token"] == "test"
    assert panel.session.changes["token"] == "private-token-value"
    assert "private-token-value" not in str(panel.client.edit_message_text.call_args)
    panel.message.delete.assert_awaited_once()


async def test_nontext_message_in_pending_secret_edit_is_consumed_without_ai(panel):
    from waku.common.rich_message import message_plain_text

    panel.ns["common"].message_plain_text = message_plain_text
    panel.session.pending = "token"
    panel.session.pending_until = time.monotonic() + 120
    panel.message = Message(
        id=23,
        chat=Chat(id=1, type=enums.ChatType.PRIVATE),
        from_user=User(id=1, first_name="Admin"),
    )
    panel.message.delete = AsyncMock()
    panel.message.stop_propagation = MagicMock(side_effect=pyrogram.StopPropagation)
    with pytest.raises(pyrogram.StopPropagation):
        await panel.ns["private_config_input"](panel.client, panel.message)
    assert panel.editor.snapshot()[0]["token"] == "test"
    panel.message.delete.assert_awaited_once()


async def test_new_menu_detects_effective_pending_restart_then_reopen_clears_revert(
    native_panel,
):
    panel = native_panel
    panel.editor.path.write_text(
        'token="startup-token"\nowners=[1]\nagent_prompt="before"\n'
    )
    await open_native_panel(panel)
    assert panel.session.dirty == {"token"}
    assert any(
        button.callback_data.endswith(":restart")
        for row in panel.message.reply_markup.inline_keyboard
        for button in row
    )
    panel.editor.path.write_text('token="test"\nowners=[1]\nagent_prompt="before"\n')
    await open_native_panel(panel)
    assert not panel.session.dirty
    assert not any(
        b.callback_data.endswith(":restart")
        for row in panel.message.reply_markup.inline_keyboard
        for b in row
    )
