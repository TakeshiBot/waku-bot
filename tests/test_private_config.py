"""Admin menu permissions, stale callbacks and private input isolation."""

import ast
import asyncio
import html
import inspect
import os
import secrets
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
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
def panel(tmp_path, schemas):  # noqa: F811
    path = tmp_path / "settings.toml"
    path.write_text('token="test"\nowners=[1]\nagent_prompt="before"\n')
    schema, provider = schemas
    runtime = schema(token="test", owners=[1])
    editor = SettingsEditor([path], schema, provider, runtime.model_dump)
    namespace = {
        "__name__": "__main__",
        "asyncio": asyncio,
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
        "common": SimpleNamespace(spawn=MagicMock()),
        "logger": MagicMock(),
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
    panel.ns["_menu"]("test", panel.session, "group:agent:0")
    old_view = panel.session.view
    panel.ns["_menu"]("test", panel.session, "group:services:0")
    request = query(panel, "edit:0", view=old_view)
    await panel.ns["private_config_callback"](panel.client, request)
    assert panel.session.pending is None


async def test_toggle_saves_and_answers_callback_once(panel):
    panel.session.entries = ["debug"]
    values, panel.session.revision = panel.editor.snapshot()
    request = query(panel, "toggle:0")
    await panel.ns["private_config_callback"](panel.client, request)
    assert panel.editor.snapshot()[0]["debug"] is not values["debug"]
    request.answer.assert_awaited_once()


async def test_failed_edit_has_usable_back_button(panel):
    panel.session.entries = ["debug"]
    panel.session.revision = "stale"
    request = query(panel, "toggle:0")
    await panel.ns["private_config_callback"](panel.client, request)
    markup = panel.message.edit_text.call_args.kwargs["reply_markup"]
    back = markup.inline_keyboard[0][0]
    assert back.callback_data == f"pcfg:test:{panel.session.view}:home"
    request.data = back.callback_data
    await panel.ns["private_config_callback"](panel.client, request)
    assert "stale" not in str(panel.message.edit_text.call_args)
    assert panel.session.revision == panel.editor.snapshot()[1]


async def test_input_saved_and_stopped_before_ai_even_with_slash(panel):
    panel.session.pending = "agent_prompt"
    panel.session.pending_until = time.monotonic() + 120
    panel.session.revision = panel.editor.snapshot()[1]
    panel.message.text = "/root/absolute/path\nnew prompt"
    with pytest.raises(pyrogram.StopPropagation):
        await panel.ns["private_config_input"](panel.client, panel.message)
    assert panel.editor.snapshot()[0]["agent_prompt"] == panel.message.text
    panel.message.delete.assert_awaited_once()
    assert panel.session.pending is None


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
    sleep = AsyncMock()
    kill = MagicMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(os, "kill", kill)
    await panel.ns["_restart"]()
    kill.assert_called_once_with(os.getpid(), signal.SIGINT)


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
    assert len(root_rows) == 1 and len(root_rows[0]) == 2
    assert root_rows[0][0].text == "⚙️ Cài đặt"
    await panel.click("groups:0", raw_bytes=True)
    assert len(panel.message.reply_markup.inline_keyboard) == 9
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
    await open_native_panel(panel)
    keys = sorted(
        key
        for key in panel.ns["_AppConfig"].model_fields
        if panel.ns["_group"](key) == "base"
    )
    page = keys.index("debug") // panel.ns["_PAGE_SIZE"]
    text, markup = panel.ns["_menu"](panel.token, panel.session, f"group:base:{page}")
    panel.message.text, panel.message.reply_markup = text, markup
    prior = panel.editor.snapshot()[0]["debug"]
    await panel.click(f"toggle:{keys.index('debug')}")
    assert panel.editor.snapshot()[0]["debug"] is not prior
    assert panel.session.location == f"group:base:{page}"
    assert "🔄" in panel.message.text
    assert any(
        button.callback_data.endswith(":restart")
        for button in panel.message.reply_markup.inline_keyboard[-1]
    )
    panel.ns["logger"].error.assert_not_called()


@pytest.mark.parametrize("delete_fails", [False, True])
async def test_native_close_deletes_menu_or_falls_back_to_closed_notice(
    native_panel, delete_fails
):
    panel = native_panel
    await open_native_panel(panel)
    if delete_fails:
        panel.client.delete_messages.side_effect = RuntimeError("cannot delete")
    await panel.click("close")
    assert panel.token not in panel.ns["_SESSIONS"]
    panel.client.delete_messages.assert_awaited_once()
    if delete_fails:
        assert panel.message.reply_markup is None
        assert panel.message.text == panel.ns["_tr"]("closed", panel.session)
    else:
        panel.client.edit_message_text.assert_not_awaited()
    panel.client.answer_callback_query.assert_awaited_once()
