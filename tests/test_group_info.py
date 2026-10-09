"""Privileged group overview works without Telegram or the production database."""

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import ChannelPrivate
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import waku
from waku.i18n import i18n

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def info(monkeypatch):
    database = ModuleType("waku.database")
    database.get_user_by_id = AsyncMock(
        return_value=SimpleNamespace(
            is_bot_global_admin=False, user_config=SimpleNamespace(lang="vi")
        )
    )
    database.get_known_groups_page = AsyncMock(
        return_value=SimpleNamespace(items=[], total=0, page=1, size=10)
    )
    monkeypatch.setitem(sys.modules, "waku.database", database)
    monkeypatch.setattr(waku, "database", database, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "waku.config",
        SimpleNamespace(app_config=SimpleNamespace(owners=[1])),
    )
    monkeypatch.setitem(
        sys.modules,
        "waku.logger",
        SimpleNamespace(
            logger=SimpleNamespace(debug=lambda *a: None, warning=lambda *a: None)
        ),
    )
    return _load("_info_plugin_test", "waku/plugins/group_info.py", monkeypatch)


def _message(user_id=1, chat_type=ChatType.PRIVATE):
    message = Message(
        id=7,
        chat=Chat(id=user_id, type=chat_type),
        from_user=User(id=user_id, first_name="Tester"),
        text="/info",
    )
    message.reply_text = AsyncMock()
    message.edit_text = AsyncMock()
    message.delete = AsyncMock()
    return message


def _query(message, user_id=1, action="p", page=1, owner_id=1):
    return SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=user_id),
        data=f"group_info:{owner_id}:{action}:{page}",
        answer=AsyncMock(),
    )


def _api_mock(method, return_value):
    # Kurigram wraps async APIs for synchronous use, so create_autospec alone
    # mistakes the wrapper for a synchronous method. Validate its real signature.
    signature = inspect.signature(method)

    async def invoke(*args, **kwargs):
        signature.bind(*args, **kwargs)
        return return_value

    return AsyncMock(side_effect=invoke)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/info", "/info@waku_test_bot"])
async def test_registered_command_uses_real_message_methods_and_current_api(
    info, command
):
    client = Client("info-unit", api_id=1, api_hash="0" * 32, in_memory=True)
    client.me = User(id=999, first_name="Waku", username="waku_test_bot", is_bot=True)
    chat = Chat(id=1, type=ChatType.PRIVATE)
    panel = Message(client=client, id=17, chat=chat, from_user=client.me)
    incoming = Message(
        client=client,
        id=16,
        chat=chat,
        from_user=User(id=1, first_name="Owner"),
        text=command,
    )
    # Autospec the actual installed methods: obsolete quote/preview kwargs fail.
    client.send_message = _api_mock(client.send_message, panel)
    client.edit_message_text = _api_mock(client.edit_message_text, panel)
    handler, group = info.info_command.handlers[0]
    assert isinstance(handler, MessageHandler)
    assert group == 0
    assert await handler.check(client, incoming)
    await handler.callback(client, incoming)
    client.send_message.assert_awaited_once_with(1, i18n.t("bot.info.loading", "vi"))
    edit = client.edit_message_text.await_args
    assert edit.kwargs["link_preview_options"].is_disabled
    assert "📚 <b>Nhóm Telegram</b>" in edit.kwargs["text"]
    assert (1, 17) in info._panels
    assert isinstance(edit.kwargs["reply_markup"].inline_keyboard[0][0].text, str)
    assert (
        edit.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        == "group_info:1:x:1"
    )


@pytest.mark.asyncio
async def test_registered_callback_uses_real_query_and_message_methods(info):
    client = Client("info-callback-unit", api_id=1, api_hash="0" * 32, in_memory=True)
    client.me = User(id=999, first_name="Waku", username="waku_test_bot", is_bot=True)
    panel = Message(client=client, id=17, chat=Chat(id=1, type=ChatType.PRIVATE))
    client.answer_callback_query = _api_mock(client.answer_callback_query, True)
    client.edit_message_text = _api_mock(client.edit_message_text, panel)
    client.delete_messages = _api_mock(client.delete_messages, 1)
    info._remember_panel(panel, 1)
    query = CallbackQuery(
        client=client,
        id="callback-test",
        from_user=User(id=1, first_name="Owner"),
        message=panel,
        data="group_info:1:r:1",
    )
    handler, group = info.info_callback.handlers[0]
    assert isinstance(handler, CallbackQueryHandler)
    assert group == 0
    assert await handler.check(client, query)
    await handler.callback(client, query)
    client.answer_callback_query.assert_awaited_once()
    assert client.edit_message_text.await_args.kwargs[
        "link_preview_options"
    ].is_disabled
    query.data = "group_info:1:x:1"
    await handler.callback(client, query)
    client.delete_messages.assert_awaited_once()
    assert (1, 17) not in info._panels


def test_legacy_style_layout_and_telegram_message_size(info):
    keyboard = info._keyboard(2, 3, 1, "vi")
    assert [button.text for button in keyboard.inline_keyboard[0]] == ["◀️", "✖️", "▶️"]
    chat = SimpleNamespace(id=-1001234567890, title='"<&>' * 20, username="a" * 32)
    line = info._format_group_line(10, chat, "accessible", "en")
    assert line.startswith("<blockquote><b>10. ")
    assert "<b>ID:</b>" in line
    assert "<b>Link:</b> @" in line
    assert line.endswith("| 🟢")
    assert len(line * info._PAGE_SIZE) + 200 < 4096


@pytest.mark.asyncio
async def test_command_requires_owner_or_global_admin_in_private_chat(info):
    unauthorized = _message(2)
    client = SimpleNamespace(send_message=AsyncMock())
    await info.info_command(client, unauthorized)
    info.database.get_known_groups_page.assert_not_awaited()
    assert client.send_message.await_args.args == (2, i18n.t("bot.info.denied", "vi"))
    group_message = _message(1, ChatType.SUPERGROUP)
    client.send_message.reset_mock()
    await info.info_command(client, group_message)
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_without_db_record_and_global_admin_are_allowed(info):
    info.database.get_user_by_id.return_value = None
    message = _message()
    panel = _message()
    client = SimpleNamespace(send_message=AsyncMock(return_value=panel))
    await info.info_command(client, message)
    assert (1, 7) in info._panels
    assert "Nhóm Telegram" in panel.edit_text.await_args.args[0]
    info.database.get_user_by_id.return_value = SimpleNamespace(
        is_bot_global_admin=True, user_config=SimpleNamespace(lang="en")
    )
    admin = _message(2)
    admin_panel = _message(2)
    client.send_message.return_value = admin_panel
    await info.info_command(client, admin)
    assert (2, 7) in info._panels
    assert "Telegram Groups" in admin_panel.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_callbacks_recheck_roles_and_bind_original_user(info):
    panel = _message()
    info._remember_panel(panel, 1)
    info.app_config.owners = []
    revoked = _query(panel)
    await info.info_callback(SimpleNamespace(), revoked)
    assert revoked.answer.await_args.kwargs["show_alert"]
    panel.edit_text.assert_not_awaited()
    info.database.get_user_by_id.return_value.is_bot_global_admin = True
    other_admin = _query(panel, user_id=2, action="x")
    await info.info_callback(SimpleNamespace(), other_admin)
    panel.delete.assert_not_awaited()
    assert other_admin.answer.await_args.kwargs["show_alert"]


@pytest.mark.asyncio
async def test_expired_or_unregistered_panel_cannot_be_edited(info):
    panel = _message()
    query = _query(panel)
    await info.info_callback(SimpleNamespace(), query)
    assert query.answer.await_args.args[0] == i18n.t("bot.info.expired", "vi")
    info._panels[(1, 7)] = (1, 0)
    await info.info_callback(SimpleNamespace(), _query(panel))
    panel.edit_text.assert_not_awaited()
    assert not info._panels


@pytest.mark.asyncio
async def test_close_deletes_only_authorized_panel(info):
    panel = _message()
    info._remember_panel(panel, 1)
    await info.info_callback(SimpleNamespace(), _query(panel, action="x"))
    panel.delete.assert_awaited_once()
    assert not info._panels


@pytest.mark.asyncio
async def test_refresh_reloads_selected_page_and_forces_live_check(info, monkeypatch):
    panel = _message()
    info._remember_panel(panel, 1)
    render = AsyncMock(return_value=("refreshed", None))
    monkeypatch.setattr(info, "_page", render)
    client = SimpleNamespace()
    await info.info_callback(client, _query(panel, action="r", page=2))
    render.assert_awaited_once_with(client, 1, "vi", 2, refresh=True)
    assert panel.edit_text.await_args.args[0] == "refreshed"


@pytest.mark.asyncio
async def test_html_is_escaped_and_live_access_error_is_visible(info):
    info.database.get_known_groups_page.return_value = SimpleNamespace(
        items=[SimpleNamespace(id=-100, title='<b>"A&B"</b>', username='"><script>')],
        total=1,
        page=1,
        size=8,
    )
    client = SimpleNamespace(get_chat_member=AsyncMock(side_effect=ChannelPrivate()))
    body, keyboard = await info._page(client, 1, "vi")
    assert "&lt;b&gt;&quot;A&amp;B&quot;&lt;/b&gt;" in body
    assert "<script>" not in body
    assert "<blockquote><b>1." in body
    assert "<b>Liên kết:</b> — | 🔴" in body
    assert all(
        button.callback_data.startswith("group_info:1:")
        for row in keyboard.inline_keyboard
        for button in row
    )
    client.get_chat_member.assert_awaited_once_with(-100, "me")


@pytest.mark.asyncio
async def test_timeout_is_unknown_and_status_cache_is_bounded(info, monkeypatch):
    monkeypatch.setattr(info, "_ACCESS_TIMEOUT", 0.01)
    monkeypatch.setattr(info, "_ACCESS_LIMIT", 2)

    async def slow(*args):
        await asyncio.sleep(1)

    client = SimpleNamespace(get_chat_member=AsyncMock(side_effect=slow))
    assert await info._access_status(client, -1) == "unknown"
    await info._access_status(client, -1)
    assert client.get_chat_member.await_count == 1
    await info._access_status(client, -2)
    await info._access_status(client, -3)
    assert len(info._access_cache) == 2
    assert (0, -1) not in info._access_cache


@pytest.mark.asyncio
async def test_timeout_also_bounds_wait_for_concurrency_slot(info, monkeypatch):
    monkeypatch.setattr(info, "_ACCESS_TIMEOUT", 0.01)
    monkeypatch.setattr(info, "_access_slots", asyncio.Semaphore(0))
    client = SimpleNamespace(get_chat_member=AsyncMock())
    assert await info._access_status(client, -1) == "unknown"
    client.get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_access_concurrency_is_limited_and_refresh_bypasses_cache(info):
    running = maximum = 0

    async def membership(*args):
        nonlocal running, maximum
        running += 1
        maximum = max(maximum, running)
        await asyncio.sleep(0.01)
        running -= 1
        return SimpleNamespace(status=ChatMemberStatus.MEMBER)

    client = SimpleNamespace(get_chat_member=AsyncMock(side_effect=membership))
    values = await asyncio.gather(
        *(info._access_status(client, -i) for i in range(1, 9))
    )
    assert values == ["accessible"] * 8
    assert maximum <= 3
    await info._access_status(client, -1)
    assert client.get_chat_member.await_count == 8
    await info._access_status(client, -1, refresh=True)
    assert client.get_chat_member.await_count == 9


@pytest.mark.asyncio
async def test_repository_pagination_excludes_private_chats_and_clamps(monkeypatch):
    config = SimpleNamespace(runtime_config=SimpleNamespace(db_is_postgres=False))
    monkeypatch.setitem(sys.modules, "waku.config", config)
    package = ModuleType("_info_repo")
    package.__path__ = [str(ROOT / "waku/database")]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    db = ModuleType("_info_repo.db")
    db.with_session = db.with_tx = lambda function: function
    monkeypatch.setitem(sys.modules, db.__name__, db)
    models = _load("_info_repo.models", "waku/database/models.py", monkeypatch)
    pager = _load("_info_repo.pagination", "waku/database/pagination.py", monkeypatch)
    database = ModuleType("waku.database")
    database.pagination = pager
    monkeypatch.setitem(sys.modules, "waku.database", database)
    monkeypatch.setattr(waku, "database", database, raising=False)
    monkeypatch.setitem(
        sys.modules, "waku.common.memory_store", SimpleNamespace(memttlcache=None)
    )
    repository = _load("_info_repo.chat", "waku/database/chat.py", monkeypatch)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(models.ChatData.__table__.create)
        async with AsyncSession(engine) as session:
            session.add_all(
                [
                    models.ChatData(id=chat_id, title=str(chat_id))
                    for chat_id in [-1, -2, -3, -4, -5, 6]
                ]
            )
            await session.commit()
            first = await repository.get_known_groups_page(
                page=0, size=2, session=session
            )
            second = await repository.get_known_groups_page(
                page=2, size=2, session=session
            )
            last = await repository.get_known_groups_page(
                page=999, size=2, session=session
            )
            assert first.total == second.total == last.total == 5
            assert [chat.id for chat in first.items] == [-1, -2]
            assert [chat.id for chat in second.items] == [-3, -4]
            assert [chat.id for chat in last.items] == [-5]
            assert (first.page, last.page) == (1, 3)
            bounded = await repository.get_known_groups_page(size=999, session=session)
            assert bounded.size == 100
    finally:
        await engine.dispose()
