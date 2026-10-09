"""Discord integration preserves the existing Telegram process lifecycle."""

import ast
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[1]


def lifecycle(events):
    def record(name):
        async def action():
            events.append(name)

        return action

    namespace = {
        "Client": object,
        "client": SimpleNamespace(
            start=AsyncMock(side_effect=record("telegram-start")),
            stop=AsyncMock(side_effect=record("telegram-stop")),
        ),
        "db": SimpleNamespace(
            init_db=AsyncMock(side_effect=record("db-init")),
            close_db=AsyncMock(side_effect=record("db-close")),
        ),
        "app_config": SimpleNamespace(
            lang="vi",
            agent=False,
            loop_monitor_enabled=False,
            webapp=False,
            health_check_enabled=False,
            session_health_enabled=False,
        ),
        "logger": MagicMock(),
        "i18n": SimpleNamespace(t=lambda *args, **kwargs: "lifecycle"),
        "common": SimpleNamespace(
            jobqueue=SimpleNamespace(shutdown=lambda: events.append("jobs-stop"))
        ),
        "webapp_server": SimpleNamespace(
            stop=AsyncMock(side_effect=record("http-stop"))
        ),
        "start_discord_bot": AsyncMock(side_effect=record("discord-start")),
        "stop_discord_bot": AsyncMock(side_effect=record("discord-stop")),
        "idle": AsyncMock(side_effect=record("idle")),
    }
    tree = ast.parse((ROOT / "waku/__main__.py").read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {"main", "stop_bot"}
    ]
    for node in nodes:
        node.decorator_list = []
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), "bot-lifecycle", "exec"),
        namespace,
    )
    return namespace


async def test_discord_starts_after_telegram_and_start_failure_keeps_main_running():
    events = []
    namespace = lifecycle(events)
    await namespace["main"]()
    assert events == [
        "db-init",
        "telegram-start",
        "discord-start",
        "idle",
        "telegram-stop",
        "http-stop",
    ]
    events.clear()
    namespace["start_discord_bot"].side_effect = RuntimeError("private-discord-token")
    namespace["stop_discord_bot"].side_effect = RuntimeError("private-discord-token")
    await namespace["main"]()
    assert events == ["db-init", "telegram-start", "idle", "telegram-stop", "http-stop"]
    assert "private-discord-token" not in str(namespace["logger"].method_calls)


@pytest.mark.parametrize("discord_failure", [False, True])
async def test_discord_stops_before_shared_resources_and_cannot_skip_cleanup(
    monkeypatch, discord_failure
):
    events = []
    namespace = lifecycle(events)

    async def close_http():
        events.append("proxy-close")

    monkeypatch.setitem(
        sys.modules,
        "waku.common.http",
        SimpleNamespace(close_agent_http_clients=close_http),
    )
    if discord_failure:
        namespace["stop_discord_bot"].side_effect = RuntimeError(
            "private-discord-token"
        )
    await namespace["stop_bot"]()
    assert events == (["discord-stop"] if not discord_failure else []) + [
        "proxy-close",
        "jobs-stop",
        "db-close",
    ]
    assert "private-discord-token" not in str(namespace["logger"].method_calls)


def test_discord_migration_keeps_telegram_rows_and_handles_create_all():
    path = ROOT / "alembic/versions/e2f3a4b5c6d7_add_discord_chat_data.py"
    spec = importlib.util.spec_from_file_location("discord_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE chat_data (id BIGINT PRIMARY KEY, title TEXT)")
        )
        connection.execute(sa.text("INSERT INTO chat_data VALUES (123, 'Telegram')"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            migration.upgrade()
            connection.execute(
                sa.text(
                    "INSERT INTO discord_chat_data (id,title,config) VALUES (123,'Discord','{}')"
                )
            )
            assert (
                connection.execute(
                    sa.text("SELECT title FROM chat_data WHERE id=123")
                ).scalar_one()
                == "Telegram"
            )
            assert (
                connection.execute(
                    sa.text("SELECT title FROM discord_chat_data WHERE id=123")
                ).scalar_one()
                == "Discord"
            )
            migration.downgrade()
            assert (
                connection.execute(
                    sa.text("SELECT count(*) FROM chat_data")
                ).scalar_one()
                == 1
            )
    engine.dispose()
