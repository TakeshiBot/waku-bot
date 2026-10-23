"""Transactional mirror submission reservations and notification outbox."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import with_session, with_tx
from .models import MirrorTask

TERMINAL = frozenset({"completed", "failed", "cancelled", "send_failed"})


def _cancel_state_matches(details):
    # JSON-path scalar comparison works on PostgreSQL JSON (whole-document
    # JSON equality does not), MySQL and SQLite alike.
    column = MirrorTask.details["cancel_status"].as_string()
    expected = (details or {}).get("cancel_status")
    return column.is_(None) if expected is None else column == expected


def _data(row):
    if row is None:
        return None
    result = {key: getattr(row, key) for key in MirrorTask.__table__.columns.keys()}
    result["request_id"] = result["id"]
    result.update(result.pop("details") or {})
    return result


@with_tx
async def reserve(values, session=None):
    """Only the creator of the durable reservation may send a command."""
    values = dict(
        values,
        status="reserved",
        accepted_notification="pending",
        terminal_notification="pending",
    )
    values.setdefault("details", {})
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        stmt = sqlite_insert(MirrorTask).values(**values).on_conflict_do_nothing()
    elif dialect == "postgresql":
        stmt = postgres_insert(MirrorTask).values(**values).on_conflict_do_nothing()
    elif dialect == "mysql":
        stmt = mysql_insert(MirrorTask).values(**values)
        stmt = stmt.on_duplicate_key_update(id=stmt.inserted.id)
    else:
        raise ValueError("Unsupported mirror database")
    await session.execute(stmt)
    # A compare-and-set works even when MySQL reports matched rowcounts for
    # duplicate-key no-op inserts. A reserved row is claimed only once.
    claimed = await session.execute(
        update(MirrorTask)
        .where(MirrorTask.id == values["id"], MirrorTask.status == "reserved")
        .values(status="sending")
    )
    return _data(
        await session.get(MirrorTask, values["id"], populate_existing=True)
    ), claimed.rowcount == 1


@with_session
async def get(request_id, session=None):
    return _data(await session.get(MirrorTask, request_id))


@with_session
async def cancellation_candidates(
    requester_id,
    origin_chat_id,
    *,
    request_id=None,
    reply_message_id=None,
    origin_platform="telegram",
    session=None,
):
    statement = select(MirrorTask).where(MirrorTask.requester_id == requester_id)
    platform = MirrorTask.details["origin_platform"].as_string()
    statement = statement.where(
        or_(platform.is_(None), platform == "telegram")
        if origin_platform == "telegram"
        else platform == origin_platform
    )
    if request_id is not None:
        statement = statement.where(MirrorTask.id == request_id)
    else:
        statement = statement.where(MirrorTask.origin_chat_id == origin_chat_id)
        if reply_message_id is not None:
            statement = statement.where(
                or_(
                    MirrorTask.origin_message_id == reply_message_id,
                    MirrorTask.accepted_message_id == reply_message_id,
                    MirrorTask.terminal_message_id == reply_message_id,
                )
            )
        else:
            statement = statement.where(MirrorTask.status.not_in(TERMINAL))
    rows = (
        (
            await session.execute(
                statement.order_by(MirrorTask.created_at.desc()).limit(21)
            )
        )
        .scalars()
        .all()
    )
    return [_data(row) for row in rows]


@with_tx
async def claim_cancellation(request_id, requester_id, session=None):
    row = (
        await session.execute(
            select(MirrorTask)
            .where(MirrorTask.id == request_id, MirrorTask.requester_id == requester_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    previous = dict(row.details or {})
    if (
        row.status in TERMINAL
        or previous.get("cancel_status")
        or not row.command_message_id
    ):
        return {"task": _data(row), "claimed": False}
    details = dict(
        previous,
        cancel_status="sending",
        cancel_requested_by=requester_id,
        cancel_requested_at=time.time(),
    )
    changed = await session.execute(
        update(MirrorTask)
        .where(
            MirrorTask.id == row.id,
            MirrorTask.status.not_in(TERMINAL),
            _cancel_state_matches(previous),
        )
        .values(details=details)
    )
    return {
        "task": _data(await session.get(MirrorTask, row.id, populate_existing=True)),
        "claimed": changed.rowcount == 1,
    }


@with_tx
async def finish_cancellation(
    request_id,
    requester_id,
    status,
    *,
    command_message_id=None,
    error=None,
    _attempt=0,
    session=None,
):
    if status not in {"sent", "failed", "uncertain"}:
        raise ValueError("Invalid mirror cancellation outcome")
    row = (
        await session.execute(
            select(MirrorTask)
            .where(MirrorTask.id == request_id, MirrorTask.requester_id == requester_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    previous = dict(row.details or {})
    if previous.get("cancel_status") != "sending":
        return _data(row)
    details = dict(previous, cancel_status=status)
    if command_message_id is not None:
        details["cancel_command_message_id"] = command_message_id
    if error:
        details["cancel_error"] = error
    changed = await session.execute(
        update(MirrorTask)
        .where(
            MirrorTask.id == row.id,
            MirrorTask.status == row.status,
            _cancel_state_matches(previous),
        )
        .values(details=details)
    )
    if changed.rowcount != 1:
        if _attempt < 2:
            return await finish_cancellation(
                request_id,
                requester_id,
                status,
                command_message_id=command_message_id,
                error=error,
                _attempt=_attempt + 1,
                session=session,
            )
        # A simultaneous lifecycle update must be retained; the claim stays
        # durable even if the final send outcome cannot be merged this instant.
        return _data(await session.get(MirrorTask, row.id, populate_existing=True))
    return _data(await session.get(MirrorTask, row.id, populate_existing=True))


@with_session
async def find_cancel_command(chat_id, bot_id, command_message_id, session=None):
    row = (
        await session.execute(
            select(MirrorTask).where(
                MirrorTask.mirror_chat_id == chat_id,
                MirrorTask.mirror_bot_id == bot_id,
                MirrorTask.details["cancel_command_message_id"].as_integer()
                == command_message_id,
            )
        )
    ).scalar_one_or_none()
    return _data(row)


@with_session
async def find_mirror_command(chat_id, bot_id, command_message_id, session=None):
    """Native Mirror messages may only address a command sent to that bot."""
    row = (
        await session.execute(
            select(MirrorTask).where(
                MirrorTask.mirror_chat_id == chat_id,
                MirrorTask.mirror_bot_id == bot_id,
                MirrorTask.command_message_id == command_message_id,
            )
        )
    ).scalar_one_or_none()
    return _data(row)


@with_session
async def find_unbound_mirror_request(request_id, chat_id, bot_id, session=None):
    """Only a verified own-bot command can bind a fast native Mirror reply."""
    row = (
        await session.execute(
            select(MirrorTask).where(
                MirrorTask.id == request_id,
                MirrorTask.mirror_chat_id == chat_id,
                MirrorTask.mirror_bot_id == bot_id,
                MirrorTask.command_message_id.is_(None),
                MirrorTask.status.in_(["sending", "send_uncertain"]),
            )
        )
    ).scalar_one_or_none()
    return _data(row)


@with_tx
async def record_source(request_id, url, staged_message_id, session=None):
    """Persist the actual staged attachment before its Mirror command is sent."""
    row = await session.get(MirrorTask, request_id)
    if row is None or row.status != "sending":
        raise ValueError("Mirror source can only be recorded for a new submission")
    details = dict(row.details or {}, staged_message_id=staged_message_id)
    changed = await session.execute(
        update(MirrorTask)
        .where(MirrorTask.id == request_id, MirrorTask.status == "sending")
        .values(url=url, details=details)
    )
    if changed.rowcount != 1:
        raise ValueError("Mirror source reservation was lost")
    return _data(await session.get(MirrorTask, request_id, populate_existing=True))


@with_tx
async def mark_sent(
    request_id, message_id=None, *, status="sent", error=None, session=None
):
    changes = {"status": status}
    if message_id is not None:
        # A fast Mirror acknowledgement can arrive before send_message returns.
        await session.execute(
            update(MirrorTask)
            .where(MirrorTask.id == request_id, MirrorTask.command_message_id.is_(None))
            .values(command_message_id=message_id)
        )
    if error:
        current = await session.get(MirrorTask, request_id)
        changes["details"] = dict(current.details or {}, error=error)
    await session.execute(
        update(MirrorTask)
        .where(MirrorTask.id == request_id, MirrorTask.status == "sending")
        .values(**changes)
    )
    return _data(await session.get(MirrorTask, request_id, populate_existing=True))


@with_tx
async def apply_event(payload, *, sender_id, chat_id, _attempt=0, session=None):
    row = (
        await session.execute(
            select(MirrorTask)
            .where(MirrorTask.id == payload["request_id"])
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None or row.mirror_bot_id != sender_id or row.mirror_chat_id != chat_id:
        return None
    command_id = payload["command_message_id"]
    if row.command_message_id is not None and row.command_message_id != command_id:
        return None
    event = payload["event"]
    if row.status in TERMINAL:
        return _data(row)  # Late accepted / duplicate terminal cannot regress.
    details = dict(row.details or {})
    details.update(
        {
            key: payload[key]
            for key in ("gid", "name", "size", "cancel_command", "links", "error")
            if key in payload
        }
    )
    if payload.get("bridge_protocol") is True:
        details["bridge_protocol"] = True
    elif (
        payload.get("bridge_protocol") is False
        and details.get("bridge_protocol") is not True
    ):
        details["bridge_protocol"] = False
    if event in {"accepted", "received"}:
        details.pop("error", None)
    if row.status in {"accepted", "unconfirmed"} and event == "received":
        return _data(row)
    changes = dict(status=event, details=details, command_message_id=command_id)
    if (
        row.status == "unconfirmed"
        and event == "accepted"
        and row.accepted_notification != "sending"
    ):
        changes["accepted_notification"] = "pending"
    # Compare-and-set also protects SQLite, where SELECT FOR UPDATE is ignored.
    changed = await session.execute(
        update(MirrorTask)
        .where(
            MirrorTask.id == row.id,
            MirrorTask.status == row.status,
            _cancel_state_matches(row.details),
        )
        .values(**changes)
    )
    if changed.rowcount != 1:
        if _attempt < 2:
            return await apply_event(
                payload,
                sender_id=sender_id,
                chat_id=chat_id,
                _attempt=_attempt + 1,
                session=session,
            )
        return None
    return _data(await session.get(MirrorTask, row.id, populate_existing=True))


def _stage_eligible(stage):
    if stage == "accepted":
        return MirrorTask.status.in_(["accepted", "unconfirmed"])
    if stage == "terminal":
        return MirrorTask.status.in_(TERMINAL)
    raise ValueError("Invalid mirror notification stage")


@with_session
async def pending_notifications(session=None):
    rows = (
        (
            await session.execute(
                select(MirrorTask)
                .where(
                    or_(
                        and_(
                            MirrorTask.status.in_(["accepted", "unconfirmed"]),
                            MirrorTask.accepted_notification.in_(["pending", "failed"]),
                        ),
                        and_(
                            MirrorTask.status.in_(TERMINAL),
                            MirrorTask.terminal_notification.in_(["pending", "failed"]),
                        ),
                    )
                )
                .order_by(MirrorTask.created_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "task": _data(row),
            "stage": "terminal" if row.status in TERMINAL else "accepted",
        }
        for row in rows
    ]


@with_tx
async def claim_notification(request_id, stage, session=None):
    column = getattr(MirrorTask, stage + "_notification", None)
    eligible = _stage_eligible(stage)
    changed = await session.execute(
        update(MirrorTask)
        .where(MirrorTask.id == request_id, eligible, column.in_(["pending", "failed"]))
        .values(
            {stage + "_notification": "sending", stage + "_claimed_at": time.time()}
        )
    )
    if changed.rowcount != 1:
        return None
    return _data(await session.get(MirrorTask, request_id, populate_existing=True))


@with_tx
async def finish_notification(request_id, stage, status, message_id=None, session=None):
    _stage_eligible(stage)
    if status not in {"sent", "failed", "uncertain"}:
        raise ValueError("Invalid mirror delivery status")
    column = getattr(MirrorTask, stage + "_notification")
    changes = {stage + "_notification": status}
    if message_id is not None:
        changes[stage + "_message_id"] = message_id
    await session.execute(
        update(MirrorTask)
        .where(MirrorTask.id == request_id, column == "sending")
        .values(changes)
    )


@with_tx
async def recover_delivery_claims(session=None):
    """A crashed send has an unknown outcome; never send it twice automatically."""
    for stage in ("accepted", "terminal"):
        column = getattr(MirrorTask, stage + "_notification")
        await session.execute(
            update(MirrorTask)
            .where(column == "sending")
            .values({stage + "_notification": "uncertain"})
        )


@with_tx
async def expire_acknowledgements(session=None):
    await session.execute(
        update(MirrorTask)
        .where(
            MirrorTask.status.in_(["sending", "sent", "received", "send_uncertain"]),
            MirrorTask.created_at < datetime.now(UTC) - timedelta(seconds=90),
        )
        .values(status="unconfirmed")
    )


@with_tx
async def claim_status_queries(session=None):
    now = time.time()
    conditions = (
        MirrorTask.status.not_in(TERMINAL),
        or_(
            MirrorTask.last_status_query_at.is_(None),
            MirrorTask.last_status_query_at < now - 60,
        ),
        MirrorTask.created_at < datetime.now(UTC) - timedelta(seconds=15),
    )
    rows = (
        (
            await session.execute(
                select(MirrorTask)
                .where(*conditions)
                .order_by(MirrorTask.created_at)
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    claimed = []
    for row in rows:
        changed = await session.execute(
            update(MirrorTask)
            .where(MirrorTask.id == row.id, *conditions)
            .values(last_status_query_at=now)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount == 1:
            claimed.append(_data(row))
    return claimed
