"""Delete only the menu and recorded command, never a topic reply target."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from waku.plugins import menu_cleanup


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_id", [None, 77, 7])
async def test_saved_menu_waits_five_seconds_and_deletes_messages_independently(
    monkeypatch, failed_id
):
    sleep = AsyncMock()
    monkeypatch.setattr(menu_cleanup.asyncio, "sleep", sleep)

    async def delete(chat_id, message_id):
        if message_id == failed_id:
            raise RuntimeError("cannot delete")

    client = SimpleNamespace(delete_messages=AsyncMock(side_effect=delete))
    await menu_cleanup.delete_saved_menu(client, -100, 77, 7)
    sleep.assert_awaited_once_with(5)
    assert client.delete_messages.await_args_list == [call(-100, 77), call(-100, 7)]


@pytest.mark.asyncio
@pytest.mark.parametrize("command_id", [None, 0, -1, "7", True, 77])
async def test_invalid_or_duplicate_command_ids_are_not_deleted(
    monkeypatch, command_id
):
    monkeypatch.setattr(menu_cleanup.asyncio, "sleep", AsyncMock())
    client = SimpleNamespace(delete_messages=AsyncMock())
    await menu_cleanup.delete_saved_menu(client, -100, 77, command_id)
    client.delete_messages.assert_awaited_once_with(-100, 77)


def test_schedule_uses_stored_command_id_instead_of_forum_reply(monkeypatch):
    tasks = []

    def spawn(task, **kwargs):
        tasks.append((task, kwargs))

    monkeypatch.setattr(menu_cleanup.common, "spawn", spawn)
    worker = Mock(return_value=object())
    monkeypatch.setattr(menu_cleanup, "delete_saved_menu", worker)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100), id=77, reply_to_message_id=1
    )
    client = object()
    menu_cleanup.schedule_saved_menu_cleanup(client, message, 7)
    worker.assert_called_once_with(client, -100, 77, 7)
    assert tasks == [(worker.return_value, {"name": "saved-menu:-100:77"})]
