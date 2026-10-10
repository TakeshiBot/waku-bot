"""Real transactions verify durable counters and isolated permission snapshots."""

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waku.database import group_moderation as repository
from waku.database.models import GroupModerationState

patch_with_session = repository.patch_state

@pytest.fixture
async def storage(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'moderation.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(GroupModerationState.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for name in ("get_state", "patch_state", "increment_warning", "reset_warnings"):
        original = getattr(repository, name)

        def wrap(function, read_only):
            async def call(*args, **kwargs):
                async with factory() as session:
                    if read_only:
                        return await function(*args, session=session, **kwargs)
                    async with session.begin():
                        return await function(*args, session=session, **kwargs)
            return call

        monkeypatch.setattr(repository, name, wrap(original, name == "get_state"))
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_state_survives_new_session_preserves_keys_and_scopes(storage):
    await repository.patch_state(-1001, 0, {"lock_permissions": {"send_stickers": False}, "unknown": 7})
    await repository.patch_state(-1001, 4, {"warning_count": 1})
    await repository.patch_state(-1002, 4, {"warning_count": 2})
    returned = await repository.patch_state(-1001, 0, {"lock_generation": "abc"})
    returned["lock_permissions"]["send_stickers"] = True
    assert await repository.get_state(-1001, 0) == {
        "lock_permissions": {"send_stickers": False}, "unknown": 7, "lock_generation": "abc"
    }
    await repository.patch_state(-1001, 0, {"lock_generation": None})
    assert "lock_generation" not in await repository.get_state(-1001, 0)
    assert (await repository.get_state(-1001, 4))["warning_count"] == 1
    assert (await repository.get_state(-1002, 4))["warning_count"] == 2
    assert await repository.get_state(-1003, 4) == {}


@pytest.mark.asyncio
async def test_concurrent_warning_updates_are_not_lost_and_reset_is_conditional(storage):
    expiry = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    states = await asyncio.gather(*(
        repository.increment_warning(-1001, 4, f"reason {index}", expiry)
        for index in range(20)
    ))
    assert sorted(state["warning_count"] for state in states) == list(range(1, 21))
    current = await repository.get_state(-1001, 4)
    assert current["warning_count"] == 20
    assert len(current["warning_reasons"]) == 10
    oldest = min(states, key=lambda state: state["warning_count"])
    assert not await repository.reset_warnings(-1001, 4, oldest["warning_generation"])
    assert (await repository.get_state(-1001, 4))["warning_count"] == 20
    await repository.patch_state(-1001, 4, {"mute_permissions": {"send_gifs": False}})
    assert await repository.reset_warnings(-1001, 4, current["warning_generation"])
    assert await repository.get_state(-1001, 4) == {"mute_permissions": {"send_gifs": False}}


@pytest.mark.asyncio
async def test_warning_expiry_and_transaction_rollback(storage):
    await repository.patch_state(-1001, 4, {
        "warning_count": 2,
        "warning_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "warning_reasons": ["expired"],
    })
    result = await repository.increment_warning(-1001, 4, "new", (datetime.now(UTC) + timedelta(days=1)).isoformat())
    assert result["warning_count"] == 1
    assert result["warning_reasons"] == ["new"]
    async with storage() as session:
        async with session.begin():
            session.add(GroupModerationState(chat_id=-1009, user_id=7, state={"before": True}))
        try:
            async with session.begin():
                await patch_with_session(-1009, 7, {"before": False}, session=session)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
    assert await repository.get_state(-1009, 7) == {"before": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("chat,user", [(1, 4), (0, 4), (-1, -1), (-1, True), (False, 4)])
async def test_invalid_scope_is_rejected(storage, chat, user):
    with pytest.raises(ValueError):
        await repository.patch_state(chat, user, {"x": 1})


def test_moderation_migration_is_idempotent_and_matches_model(tmp_path, monkeypatch):
    filename = Path(__file__).parents[1] / "alembic/versions/a6b7c8d9e0f1_add_group_moderation_state.py"
    spec = importlib.util.spec_from_file_location("moderation_migration", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.upgrade()
        columns = {column["name"] for column in sa.inspect(connection).get_columns("group_moderation_state")}
        assert columns == set(GroupModerationState.__table__.columns.keys())
        migration.downgrade()
        migration.downgrade()
        assert not sa.inspect(connection).has_table("group_moderation_state")
    engine.dispose()
