"""Durable Business dialogs and exactly-once owner action claims."""

from __future__ import annotations

import time
from copy import deepcopy

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import with_session, with_tx
from .models import BusinessAction, BusinessState


def _scope(owner_id, peer_id):
    if (
        type(owner_id) is not int
        or not 0 < owner_id < 2**63
        or type(peer_id) is not int
        or not 0 <= peer_id < 2**63
    ):
        raise ValueError("Business state requires an owner and a private peer")


async def _mutate(owner_id, peer_id, mutation, session):
    _scope(owner_id, peer_id)
    values = dict(owner_id=owner_id, peer_id=peer_id, state={}, state_version=0)
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        seed = sqlite_insert(BusinessState).values(**values).on_conflict_do_nothing()
    elif dialect == "postgresql":
        seed = postgres_insert(BusinessState).values(**values).on_conflict_do_nothing()
    elif dialect == "mysql":
        seed = mysql_insert(BusinessState).values(**values)
        seed = seed.on_duplicate_key_update(owner_id=seed.inserted.owner_id)
    else:
        raise ValueError("Unsupported Business database")
    await session.execute(seed)
    row = (
        await session.execute(
            select(BusinessState)
            .where(BusinessState.owner_id == owner_id, BusinessState.peer_id == peer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    state = mutation(deepcopy(row.state or {}))
    result = await session.execute(
        update(BusinessState)
        .where(
            BusinessState.owner_id == owner_id,
            BusinessState.peer_id == peer_id,
            BusinessState.state_version == row.state_version,
        )
        .values(state=state, state_version=row.state_version + 1)
    )
    if result.rowcount != 1:
        raise RuntimeError("Business state changed concurrently")
    return deepcopy(state)


@with_session
async def get_state(
    owner_id: int, peer_id: int = 0, session: AsyncSession | None = None
) -> dict:
    _scope(owner_id, peer_id)
    row = await session.get(BusinessState, (owner_id, peer_id))
    return deepcopy(row.state or {}) if row else {}


@with_tx
async def patch_state(
    owner_id: int, peer_id: int, changes: dict, session: AsyncSession | None = None
) -> dict:
    def merge(state):
        state.update(deepcopy(changes))
        return state

    return await _mutate(owner_id, peer_id, merge, session)


@with_tx
async def record_message(
    owner_id: int,
    peer_id: int,
    event: dict,
    *,
    remember=True,
    session: AsyncSession | None = None,
) -> dict:
    if type(event.get("id")) is not int or event["id"] <= 0:
        raise ValueError("Business event requires a real message ID")
    duplicate = False

    def merge(state):
        nonlocal duplicate
        state.setdefault("first_observed_at", time.time())
        if event["id"] in state.get("deleted_ids", []):
            return state
        events = {item["id"]: item for item in state.get("events", [])}
        duplicate = event["id"] in events
        item = {**events.get(event["id"], {}), **deepcopy(event)}
        if remember:
            text = str(item.get("text") or "")
            item["text"] = text[:4000]
            if len(text) > 4000:
                item["truncated"] = True
        else:
            item = {
                key: item[key]
                for key in (
                    "id",
                    "role",
                    "timestamp",
                    "answered_message_id",
                    "forwarded",
                )
                if key in item
            }
        events[item["id"]] = item
        # Keep the latest responder even when the bounded dialog drops older
        # messages or text memory is disabled. Contact messages do not change
        # who currently holds the conversation; only the owner/assistant do.
        responder = state.get("last_responder")
        if responder is None:
            responder = max(
                (
                    row
                    for row in events.values()
                    if row.get("role") in {"owner", "assistant"}
                ),
                key=lambda row: row["id"],
                default=None,
            )
        if item.get("role") in {"owner", "assistant"} and (
            responder is None or item["id"] >= responder["id"]
        ):
            responder = item
        if responder is not None:
            state["last_responder"] = {"id": responder["id"], "role": responder["role"]}
        state["events"] = sorted(events.values(), key=lambda row: row["id"])[-100:]
        state["last_observed_at"] = time.time()
        return state

    state = await _mutate(owner_id, peer_id, merge, session)
    return {**state, "_duplicate_event": duplicate}


@with_tx
async def delete_messages(
    owner_id: int,
    peer_id: int,
    message_ids: list[int],
    session: AsyncSession | None = None,
) -> dict:
    deleted = set(message_ids)

    def merge(state):
        state["events"] = [
            item for item in state.get("events", []) if item["id"] not in deleted
        ]
        state["deleted_ids"] = sorted(set(state.get("deleted_ids", [])) | deleted)[
            -1000:
        ]
        return state

    return await _mutate(owner_id, peer_id, merge, session)


@with_session
async def list_dialogs(
    owner_id: int, session: AsyncSession | None = None
) -> list[tuple[int, dict]]:
    _scope(owner_id, 0)
    rows = (
        await session.execute(
            select(BusinessState)
            .where(BusinessState.owner_id == owner_id, BusinessState.peer_id > 0)
            .order_by(BusinessState.updated_at.desc())
            .limit(100)
        )
    ).scalars()
    return [(row.peer_id, deepcopy(row.state or {})) for row in rows]


@with_tx
async def create_action(
    token: str,
    owner_id: int,
    connection_id: str,
    operation: str,
    arguments: dict,
    session: AsyncSession | None = None,
):
    _scope(owner_id, 0)
    await session.execute(
        delete(BusinessAction).where(
            BusinessAction.expires_at < time.time() - 30 * 86400
        )
    )
    session.add(
        BusinessAction(
            token=token,
            owner_id=owner_id,
            connection_id=connection_id,
            operation=operation,
            arguments=deepcopy(arguments),
            status="pending",
            expires_at=time.time() + 900,
        )
    )


@with_tx
async def claim_action(
    token: str, owner_id: int, session: AsyncSession | None = None
) -> dict | None:
    result = await session.execute(
        update(BusinessAction)
        .where(
            BusinessAction.token == token,
            BusinessAction.owner_id == owner_id,
            BusinessAction.status == "pending",
            BusinessAction.expires_at > time.time(),
        )
        .values(status="executing")
    )
    if result.rowcount != 1:
        return None
    row = await session.get(BusinessAction, token, populate_existing=True)
    return {
        "connection_id": row.connection_id,
        "operation": row.operation,
        "arguments": deepcopy(row.arguments),
    }


@with_tx
async def finish_action(
    token: str, owner_id: int, status: str, session: AsyncSession | None = None
):
    if status not in {"done", "failed", "uncertain"}:
        raise ValueError("Invalid Business action outcome")
    await session.execute(
        update(BusinessAction)
        .where(
            BusinessAction.token == token,
            BusinessAction.owner_id == owner_id,
            BusinessAction.status == "executing",
        )
        .values(status=status)
    )
