"""Read persisted state written before the Python package became ``waku``.

These legacy names are storage identifiers, not branding. Keep the mappings
when changing visible names so scheduled jobs and Redis values survive upgrades.
"""

from __future__ import annotations

import io
import pickle
from typing import Any

from aiocache.serializers import PickleSerializer
from apscheduler.util import ref_to_obj
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

LEGACY_PACKAGE = "kmua"
CURRENT_PACKAGE = "waku"


def _current_module(module: str) -> str:
    if module == LEGACY_PACKAGE or module.startswith(LEGACY_PACKAGE + "."):
        return CURRENT_PACKAGE + module[len(LEGACY_PACKAGE) :]
    return module


class _CompatibleUnpickler(pickle.Unpickler):
    def __init__(self, file: io.BytesIO) -> None:
        super().__init__(file)
        self.remapped = False

    def find_class(self, module: str, name: str) -> Any:
        current = _current_module(module)
        self.remapped |= current != module
        return super().find_class(current, name)


def loads_legacy_pickle(value: bytes) -> Any:
    """Load a trusted existing pickle, resolving old package classes in waku."""
    return _CompatibleUnpickler(io.BytesIO(value)).load()


class CompatiblePickleSerializer(PickleSerializer):
    """Preserve the existing Redis namespace while reading old module names."""

    def loads(self, value: bytes | None) -> Any:
        if value is None:
            return None
        return loads_legacy_pickle(value)


class PersistedJobMigrationError(RuntimeError):
    """A stored job could not be upgraded without losing its state."""


def migrate_legacy_job_references(engine: Engine) -> int:
    """Atomically upgrade APScheduler pickles before the scheduler reads them.

    Both serialized classes and ``module:callable`` references are mapped. Jobs
    without old names retain their original bytes, arguments and trigger zones.
    A malformed or unresolved row aborts the transaction instead of allowing
    APScheduler to delete that job during its normal failed-load handling.
    """
    if not inspect(engine).has_table("apscheduler_jobs"):
        return 0

    migrated = 0
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT id, job_state FROM apscheduler_jobs")
        ).all()
        for job_id, payload in rows:
            try:
                unpickler = _CompatibleUnpickler(io.BytesIO(bytes(payload)))
                state = unpickler.load()
                if not isinstance(state, dict) or not isinstance(
                    state.get("func"), str
                ):
                    raise ValueError("Stored job has no callable reference")
                module, separator, name = state["func"].partition(":")
                current = _current_module(module)
                changed = unpickler.remapped or current != module
                if not changed:
                    continue
                if not separator or not name:
                    raise ValueError("Stored job has an invalid callable reference")
                state["func"] = current + separator + name
                if not callable(ref_to_obj(state["func"])):
                    raise ValueError(
                        "Stored job reference does not resolve to a callable"
                    )
                connection.execute(
                    text(
                        "UPDATE apscheduler_jobs SET job_state = :state WHERE id = :id"
                    ),
                    {
                        "state": pickle.dumps(state, pickle.HIGHEST_PROTOCOL),
                        "id": job_id,
                    },
                )
                migrated += 1
            except Exception as exc:
                raise PersistedJobMigrationError(
                    f"Could not migrate persisted job {job_id!r}; no jobs were changed"
                ) from exc
    return migrated


__all__ = [
    "CompatiblePickleSerializer",
    "PersistedJobMigrationError",
    "loads_legacy_pickle",
    "migrate_legacy_job_references",
]
