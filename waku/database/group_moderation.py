"""Group-scoped warning counters and permission snapshots in the main database."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import with_session, with_tx
from .models import GroupModerationState


def _check_scope(chat_id: int, user_id: int) -> None:
    if (
        isinstance(chat_id, bool)
        or not isinstance(chat_id, int)
        or not -(2**63) < chat_id < 0
        or isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or not 0 <= user_id < 2**63
    ):
        raise ValueError("Moderation state requires a group ID and nonnegative user ID")


@with_session
async def get_state(
    chat_id: int, user_id: int, session: AsyncSession | None = None
) -> dict:
    _check_scope(chat_id, user_id)
    assert session is not None
    row = await session.get(GroupModerationState, (chat_id, user_id))
    return deepcopy(row.state or {}) if row is not None else {}


@with_tx
async def patch_state(
    chat_id: int,
    user_id: int,
    updates: dict,
    session: AsyncSession | None = None,
) -> dict:
    """Merge selected keys; None clears a key. Callers serialize SDK mutations."""
    _check_scope(chat_id, user_id)
    if not isinstance(updates, dict) or not all(isinstance(key, str) for key in updates):
        raise ValueError("Moderation updates must be a dictionary with string keys")
    assert session is not None
    def merge(data: dict) -> dict:
        for key, value in updates.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = deepcopy(value)
        return data

    return await _mutate_state(chat_id, user_id, merge, session=session)


async def _mutate_state(
    chat_id: int, user_id: int, mutator: Callable[[dict], dict], *, session: AsyncSession
) -> dict:
    """Acquire a database write/row lock before reading, including a new scope.

    SQLite's insert acquires its writer lock before the read. PostgreSQL/MySQL
    upserts lock the conflicting row. The revision guards against stale writes.
    """
    dialect = session.get_bind().dialect.name
    values = dict(chat_id=chat_id, user_id=user_id, state={}, state_version=0)
    if dialect == "sqlite":
        seed = sqlite_insert(GroupModerationState).values(**values).on_conflict_do_nothing()
    elif dialect == "postgresql":
        seed = postgres_insert(GroupModerationState).values(**values).on_conflict_do_nothing()
    elif dialect == "mysql":
        seed = mysql_insert(GroupModerationState).values(**values)
        seed = seed.on_duplicate_key_update(chat_id=seed.inserted.chat_id)
    else:
        raise ValueError(f"Unsupported moderation database: {dialect}")
    await session.execute(seed)
    row = (
        await session.execute(
            select(GroupModerationState)
            .where(
                GroupModerationState.chat_id == chat_id,
                GroupModerationState.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    assert row is not None
    data = mutator(deepcopy(row.state or {}))
    result = await session.execute(
        update(GroupModerationState)
        .where(
            GroupModerationState.chat_id == chat_id,
            GroupModerationState.user_id == user_id,
            GroupModerationState.state_version == row.state_version,
        )
        .values(state=data, state_version=row.state_version + 1, updated_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise RuntimeError("Moderation state changed concurrently; retry the operation")
    session.expire(row)
    return deepcopy(data)


@with_tx
async def increment_warning(
    chat_id: int,
    user_id: int,
    reason: str,
    expires_at: str,
    session: AsyncSession | None = None,
) -> dict:
    _check_scope(chat_id, user_id)
    if user_id == 0 or not isinstance(reason, str):
        raise ValueError("Warnings require a member and text reason")
    expiry = datetime.fromisoformat(expires_at)
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise ValueError("Warning expiry must be an aware future date")
    assert session is not None

    def increment(data: dict) -> dict:
        previous_expiry = data.get("warning_expires_at")
        if previous_expiry and datetime.fromisoformat(previous_expiry) <= datetime.now(UTC):
            data["warning_count"] = 0
            data["warning_reasons"] = []
        data["warning_count"] = int(data.get("warning_count", 0)) + 1
        data["warning_reasons"] = [*data.get("warning_reasons", []), reason[:1000]][-10:]
        data["warning_expires_at"] = expiry.isoformat()
        data["warning_generation"] = uuid4().hex
        return data

    return await _mutate_state(chat_id, user_id, increment, session=session)


@with_tx
async def reset_warnings(
    chat_id: int,
    user_id: int,
    expected_generation: str | None = None,
    session: AsyncSession | None = None,
) -> bool:
    _check_scope(chat_id, user_id)
    if user_id == 0:
        raise ValueError("Warnings require a member")
    assert session is not None
    reset = False

    def clear(data: dict) -> dict:
        nonlocal reset
        if expected_generation is not None and data.get("warning_generation") != expected_generation:
            return data
        for key in ("warning_count", "warning_reasons", "warning_expires_at", "warning_generation"):
            data.pop(key, None)
        reset = True
        return data

    await _mutate_state(chat_id, user_id, clear, session=session)
    return reset
