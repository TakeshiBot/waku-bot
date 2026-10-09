"""Package rename must preserve existing scheduled jobs and cached objects."""

import pickle
import sys
from datetime import datetime
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import create_engine, text

from waku.compat import (
    CompatiblePickleSerializer,
    PersistedJobMigrationError,
    loads_legacy_pickle,
    migrate_legacy_job_references,
)


@pytest.fixture
def renamed_modules(monkeypatch):
    for package_name in ("kmua", "waku"):
        if package_name not in sys.modules:
            package = ModuleType(package_name)
            package.__path__ = []
            monkeypatch.setitem(sys.modules, package_name, package)
    old = ModuleType("kmua.compat_fixture")
    new = ModuleType("waku.compat_fixture")
    old.Payload = type("Payload", (), {"__module__": old.__name__})
    new.Payload = type("Payload", (), {"__module__": new.__name__})
    new.scheduled = lambda: None
    monkeypatch.setitem(sys.modules, old.__name__, old)
    monkeypatch.setitem(sys.modules, new.__name__, new)
    return old, new


@pytest.fixture
def job_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE apscheduler_jobs "
                "(id TEXT PRIMARY KEY, next_run_time FLOAT, job_state BLOB NOT NULL)"
            )
        )
    yield engine
    engine.dispose()


def _insert(engine, job_id, payload):
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO apscheduler_jobs (id, next_run_time, job_state) "
                "VALUES (:id, 123.0, :state)"
            ),
            {"id": job_id, "state": payload},
        )


def _read(engine, job_id):
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT job_state FROM apscheduler_jobs WHERE id = :id"),
            {"id": job_id},
        ).scalar_one()


def test_cached_class_maps_to_current_module(renamed_modules, monkeypatch):
    old, new = renamed_modules
    original = old.Payload()
    original.value = {"message": "xin chào", "ids": [1, 2]}
    payload = pickle.dumps(original)
    monkeypatch.delitem(sys.modules, old.__name__)

    restored = loads_legacy_pickle(payload)
    assert type(restored) is new.Payload
    assert restored.value == original.value
    serializer = CompatiblePickleSerializer()
    assert serializer.loads(payload).value == original.value
    assert serializer.loads(None) is None
    assert serializer.loads(serializer.dumps(restored)).value == original.value


def test_jobs_migrate_once_without_changing_trigger_or_unrelated_rows(
    job_engine, renamed_modules, monkeypatch
):
    old, new = renamed_modules
    argument = old.Payload()
    argument.value = "stored argument"
    due = datetime(2026, 10, 10, 9, 30, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    state = {
        "func": "kmua.compat_fixture:scheduled",
        "args": (argument,),
        "kwargs": {"chat_id": 123},
        "trigger": DateTrigger(run_date=due),
        "next_run_time": due,
    }
    unrelated = pickle.dumps({"func": "builtins:print", "args": ("existing",)})
    _insert(job_engine, "renamed", pickle.dumps(state))
    _insert(job_engine, "unrelated", unrelated)
    monkeypatch.delitem(sys.modules, old.__name__)

    assert migrate_legacy_job_references(job_engine) == 1
    migrated = pickle.loads(_read(job_engine, "renamed"))
    assert migrated["func"] == "waku.compat_fixture:scheduled"
    assert type(migrated["args"][0]) is new.Payload
    assert migrated["args"][0].value == argument.value
    assert migrated["kwargs"] == state["kwargs"]
    assert migrated["trigger"].run_date == due
    assert migrated["trigger"].run_date.tzinfo.key == "Asia/Ho_Chi_Minh"
    assert migrated["next_run_time"] == due
    assert _read(job_engine, "unrelated") == unrelated
    assert migrate_legacy_job_references(job_engine) == 0


def test_failure_rolls_back_prior_rows(job_engine, renamed_modules):
    valid = pickle.dumps({"func": "kmua.compat_fixture:scheduled", "args": ()})
    _insert(job_engine, "first", valid)
    _insert(job_engine, "broken", b"invalid pickle")

    with pytest.raises(PersistedJobMigrationError, match="broken"):
        migrate_legacy_job_references(job_engine)
    assert _read(job_engine, "first") == valid
    assert _read(job_engine, "broken") == b"invalid pickle"


def test_unresolved_renamed_callable_is_not_discarded(job_engine, renamed_modules):
    payload = pickle.dumps({"func": "kmua.compat_fixture:missing", "args": ()})
    _insert(job_engine, "missing", payload)

    with pytest.raises(PersistedJobMigrationError, match="missing"):
        migrate_legacy_job_references(job_engine)
    assert _read(job_engine, "missing") == payload


def test_fresh_database_needs_no_migration():
    engine = create_engine("sqlite:///:memory:")
    try:
        assert migrate_legacy_job_references(engine) == 0
    finally:
        engine.dispose()
