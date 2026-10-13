"""Persistent global bot feature values."""

from __future__ import annotations

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from .db import with_session, with_tx
from .models import BotRuntimeSetting


@with_session
async def get_bot_runtime_setting(
    key: str, session: AsyncSession | None = None
) -> object | None:
    assert session is not None
    row = await session.get(BotRuntimeSetting, key)
    return None if row is None else row.value


@with_session
async def read_bot_runtime_settings(
    keys: set[str] | frozenset[str] | None = None,
    session: AsyncSession | None = None,
) -> dict[str, object]:
    assert session is not None
    statement = sqlalchemy.select(BotRuntimeSetting)
    if keys is not None:
        if not keys:
            return {}
        statement = statement.where(BotRuntimeSetting.key.in_(keys))
    rows = (await session.execute(statement)).scalars().all()
    return {row.key: row.value for row in rows}


@with_tx
async def save_bot_runtime_settings(
    values: dict[str, object],
    *,
    expected_values: dict[str, object] | None = None,
    session: AsyncSession | None = None,
) -> bool:
    """Compare and atomically upsert a batch in one transaction.

    ``expected_values`` provides optimistic concurrency control for menus whose
    draft may have gone stale while another administrator saved a change.
    """
    assert session is not None
    if not values and not expected_values:
        return True
    selected_keys = set(values)
    if expected_values:
        selected_keys.update(expected_values)
    rows = (
        await session.execute(
            sqlalchemy.select(BotRuntimeSetting)
            .where(BotRuntimeSetting.key.in_(selected_keys))
            .with_for_update()
        )
    ).scalars().all()
    existing = {row.key: row for row in rows}
    if expected_values is not None and any(
        key not in existing or existing[key].value != expected
        for key, expected in expected_values.items()
    ):
        return False
    for key, value in values.items():
        row = existing.get(key)
        if row is None:
            session.add(BotRuntimeSetting(key=key, value=value))
        else:
            row.value = value
    await session.flush()
    return True


__all__ = [
    "get_bot_runtime_setting",
    "read_bot_runtime_settings",
    "save_bot_runtime_settings",
]
