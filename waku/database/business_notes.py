"""Idempotent notes and durable claims for owner-DM notifications."""

from __future__ import annotations

import hashlib
import math
import time

from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .business import _scope
from .db import with_session, with_tx
from .models import BusinessNote


def _data(row):
    return {key: getattr(row, key) for key in BusinessNote.__table__.columns.keys()}


@with_tx
async def create_note(
    owner_id,
    peer_id,
    connection_id,
    source_message_id,
    content,
    contact,
    remind_at=None,
    session: AsyncSession | None = None,
):
    _scope(owner_id, peer_id)
    if (
        peer_id == 0
        or peer_id == owner_id
        or type(source_message_id) is not int
        or not 0 < source_message_id < 2**31
        or not isinstance(content, str)
        or not 1 <= len(content.strip()) <= 1000
        or not isinstance(connection_id, str)
        or not 1 <= len(connection_id) <= 255
        or not isinstance(contact, dict)
        or (
            remind_at is not None
            and (
                type(remind_at) not in {int, float}
                or not math.isfinite(remind_at)
                or remind_at <= 0
            )
        )
    ):
        raise ValueError("Invalid Business note")
    token = hashlib.sha256(
        f"{owner_id}:{peer_id}:{source_message_id}".encode()
    ).hexdigest()
    values = dict(
        id=token,
        owner_id=owner_id,
        peer_id=peer_id,
        connection_id=connection_id,
        source_message_id=source_message_id,
        content=content.strip(),
        contact=contact,
        remind_at=remind_at,
        notification_status="pending",
        reminder_status="pending" if remind_at is not None else "not_required",
        created_at=time.time(),
    )
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        statement = (
            sqlite_insert(BusinessNote).values(**values).on_conflict_do_nothing()
        )
    elif dialect == "postgresql":
        statement = (
            postgres_insert(BusinessNote).values(**values).on_conflict_do_nothing()
        )
    elif dialect == "mysql":
        statement = mysql_insert(BusinessNote).values(**values)
        statement = statement.on_duplicate_key_update(id=statement.inserted.id)
    else:
        raise ValueError("Unsupported Business database")
    await session.execute(statement)
    return _data(await session.get(BusinessNote, token))


@with_session
async def list_notes(
    owner_id=None, *, due=False, peer_id=None, session: AsyncSession | None = None
):
    statement = select(BusinessNote)
    if owner_id is not None:
        _scope(owner_id, 0)
        statement = statement.where(BusinessNote.owner_id == owner_id)
    if peer_id is not None:
        if owner_id is None or type(peer_id) is not int or peer_id <= 0:
            raise ValueError("Note dialog requires an owner and private peer")
        statement = statement.where(BusinessNote.peer_id == peer_id)
    if due:
        statement = statement.where(
            (BusinessNote.notification_status == "pending")
            | (
                (BusinessNote.reminder_status == "pending")
                & (BusinessNote.remind_at <= time.time())
            )
        )
    return [
        _data(row)
        for row in (
            await session.execute(
                statement.order_by(BusinessNote.created_at.desc()).limit(100)
            )
        ).scalars()
    ]


@with_tx
async def claim_delivery(
    token, owner_id, stage, *, retry_failed=False, session: AsyncSession | None = None
):
    _scope(owner_id, 0)
    if stage not in {"notification", "reminder"}:
        raise ValueError("Invalid Business delivery stage")
    column = getattr(BusinessNote, stage + "_status")
    conditions = [
        BusinessNote.id == token,
        BusinessNote.owner_id == owner_id,
        column.in_(["pending", "failed"] if retry_failed else ["pending"]),
    ]
    if stage == "reminder":
        conditions.append(BusinessNote.remind_at <= time.time())
    result = await session.execute(
        update(BusinessNote).where(*conditions).values({stage + "_status": "sending"})
    )
    if result.rowcount != 1:
        return None
    return _data(await session.get(BusinessNote, token, populate_existing=True))


@with_tx
async def finish_delivery(
    token, owner_id, stage, status, session: AsyncSession | None = None
):
    _scope(owner_id, 0)
    if stage not in {"notification", "reminder"} or status not in {
        "sent",
        "failed",
        "uncertain",
    }:
        raise ValueError("Invalid Business delivery outcome")
    column = getattr(BusinessNote, stage + "_status")
    await session.execute(
        update(BusinessNote)
        .where(
            BusinessNote.id == token,
            BusinessNote.owner_id == owner_id,
            column == "sending",
        )
        .values({stage + "_status": status})
    )


@with_tx
async def cancel_reminder(token, owner_id, session: AsyncSession | None = None):
    _scope(owner_id, 0)
    result = await session.execute(
        update(BusinessNote)
        .where(
            BusinessNote.id == token,
            BusinessNote.owner_id == owner_id,
            BusinessNote.reminder_status.in_(["pending", "failed"]),
        )
        .values(reminder_status="cancelled")
    )
    return result.rowcount == 1
