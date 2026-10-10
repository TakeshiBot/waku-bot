"""Start menus and group-to-private Mini App launch without Telegram transport."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from pyrogram.enums import ChatType

from waku.common import telegram_authority
from waku.plugins import panel, start


@pytest.fixture
def launch(monkeypatch):
    monkeypatch.setattr(panel.app_config, "webapp", True)
    monkeypatch.setattr(
        panel.app_config, "webapp_url", "https://panel.example.test/app?theme=dark"
    )
    monkeypatch.setattr(panel.app_config, "webapp_short_name", "")
    monkeypatch.setattr(
        importlib.import_module("waku.bot.client"),
        "client",
        SimpleNamespace(me=SimpleNamespace(username="waku_test_bot")),
    )
    monkeypatch.setattr(
        start.database,
        "get_user_config",
        AsyncMock(return_value=SimpleNamespace(lang="vi", dm_ai_enabled=False)),
    )
    check = AsyncMock(return_value=True)
    monkeypatch.setattr(telegram_authority, "can_manage_bot_settings", check)
    return check


def test_group_link_and_private_launch_work_without_registered_app_short_name(launch):
    button = panel.chat_panel_button(-100123, "vi")
    assert button.url == "https://t.me/waku_test_bot?start=panel_c100123"
    assert button.web_app is None
    private_button = panel.private_chat_panel_button(-100123, "vi")
    assert private_button.url is None
    query = parse_qs(urlsplit(private_button.web_app.url).query)
    assert query == {"theme": ["dark"], "waku_chat": ["c100123"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_private_group_launch_checks_current_group_management_rights(
    launch, allowed
):
    launch.return_value = allowed
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        command=["start", "panel_c100123"],
        reply=AsyncMock(),
    )
    client = SimpleNamespace()
    await start.start(client, message)
    launch.assert_awaited_once_with(client, 7, -100123)
    kwargs = message.reply.await_args.kwargs
    if allowed:
        rows = kwargs["reply_markup"].inline_keyboard
        assert "waku_chat=c100123" in rows[0][0].web_app.url
        assert rows[1][0].callback_data == "start_close:7"
    else:
        assert "reply_markup" not in kwargs


@pytest.mark.parametrize("url", ["", "http://panel.example.test", "https://"])
def test_invalid_https_config_does_not_offer_broken_launch(launch, monkeypatch, url):
    monkeypatch.setattr(panel.app_config, "webapp_url", url)
    assert panel.chat_panel_button(-100123, "vi") is None
    assert panel.private_chat_panel_button(-100123, "vi") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", [ChatType.PRIVATE, ChatType.SUPERGROUP])
async def test_start_close_only_deletes_menu_for_requester(chat_type):
    message = SimpleNamespace(
        chat=SimpleNamespace(
            id=7 if chat_type == ChatType.PRIVATE else -100123, type=chat_type
        ),
        delete=AsyncMock(),
    )
    query = SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=8),
        data="start_close:7",
        answer=AsyncMock(),
    )
    await start.close_start_menu(None, query)
    message.delete.assert_not_awaited()
    assert query.answer.await_args.kwargs["show_alert"]
    query.from_user.id = 7
    await start.close_start_menu(None, query)
    message.delete.assert_awaited_once()


def test_private_start_has_close_button():
    rows = start.PrivateStartBotMarkup("vi", user_id=7).build().inline_keyboard
    assert rows[-1][0].text == "✖️ Đóng"
    assert rows[-1][0].callback_data == "start_close:7"


@pytest.mark.asyncio
async def test_group_start_has_close_button_for_requester(monkeypatch):
    monkeypatch.setattr(
        start.database,
        "get_chat_config",
        AsyncMock(return_value=SimpleNamespace(lang="vi")),
    )
    monkeypatch.setattr(start, "chat_panel_button", lambda *args: None)
    monkeypatch.setattr(start.common, "spawn", lambda task, **kwargs: task.close())
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        from_user=SimpleNamespace(id=7),
        reply=AsyncMock(),
    )
    client = SimpleNamespace(me=SimpleNamespace(username="waku_test_bot"))
    await start.start_group(client, message)
    rows = message.reply.await_args.kwargs["reply_markup"].inline_keyboard
    assert rows[-1][0].text == "✖️ Đóng"
    assert rows[-1][0].callback_data == "start_close:7"
