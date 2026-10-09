"""Agent quota accounts, daily usage, balances and ledger entries, measured in tokens (input + output).

Two independent atomic gates avoid read-then-write operations:
- Free daily quota: compare agent_usage_daily.free_used_tokens with the limit and charge up to that limit.
- Credits: agent_credits.balance has no lower bound because a run's usage is known only after completion.
  Record overspending as a negative balance (debt), blocked by the next call's preflight.

Accounts use (scope, scope_id): SCOPE_USER is the speaker; SCOPE_CHAT is the conversation/group.
Create rows on demand: usage on the first charge; balances on the first grant or charge.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import NamedTuple

import sqlalchemy
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .db import with_session, with_tx
from .models import AgentCredit, AgentCreditLedger, AgentUsageDaily

SCOPE_USER = "user"
SCOPE_CHAT = "chat"

CREDIT_REASON_ADMIN = "admin"
CREDIT_REASON_USAGE = "usage"

Account = tuple[str, int]


class ChargeAccount(NamedTuple):
    """A payer: account and its free-token limit; None = unlimited free usage."""

    scope: str
    scope_id: int
    free_limit: int | None


def utc_day() -> date:
    """Quota accounting's "today" is always UTC, independent of the deployment timezone."""
    return datetime.now(UTC).date()


async def _ensure_usage_row(
    session: AsyncSession, scope: str, scope_id: int, day: date
) -> None:
    """Ensure a usage row exists for subsequent reads and updates.

    Usually the row exists, so return after a lookup. Otherwise insert within a SAVEPOINT for races:
    a PostgreSQL unique-constraint violation invalidates the transaction and needs more than an outer except.

    On SQLite a SAVEPOINT may survive the outer rollback as a zero row; this does not affect accounting.
    """
    existing = await session.get(
        AgentUsageDaily, {"scope": scope, "scope_id": scope_id, "day": day}
    )
    if existing is not None:
        return
    try:
        async with session.begin_nested():
            session.add(AgentUsageDaily(scope=scope, scope_id=scope_id, day=day))
    except IntegrityError:
        pass


async def _ensure_credit_row(session: AsyncSession, scope: str, scope_id: int) -> None:
    """Ensure a balance row exists using the same approach."""
    existing = await session.get(AgentCredit, {"scope": scope, "scope_id": scope_id})
    if existing is not None:
        return
    try:
        async with session.begin_nested():
            session.add(AgentCredit(scope=scope, scope_id=scope_id, balance=0))
    except IntegrityError:
        pass


@with_session
async def get_usage(
    scope: str, scope_id: int, day: date, session: AsyncSession | None = None
) -> tuple[int, int, int, int]:
    """(requests, free_used_tokens, input_tokens, output_tokens); All values are zero when no row exists."""
    assert session is not None
    row = await session.get(
        AgentUsageDaily, {"scope": scope, "scope_id": scope_id, "day": day}
    )
    if row is None:
        return 0, 0, 0, 0
    return row.requests, row.free_used_tokens, row.input_tokens, row.output_tokens


@with_tx
async def spend_free(
    scope: str,
    scope_id: int,
    day: date,
    limit: int,
    tokens: int,
    session: AsyncSession | None = None,
) -> int:
    """Charge free tokens and return the actual amount (0 means this account's free quota is exhausted).

    Check the limit and increment in one UPDATE so concurrent charges cannot both write from stale values.
    If the row no longer has room, the statement changes nothing and returns 0. The caller passes the remainder
    to the next account: free usage may be lower, but the limit is not exceeded and usage remains recorded.
    """
    assert session is not None
    if tokens <= 0 or limit <= 0:
        return 0
    await _ensure_usage_row(session, scope, scope_id, day)
    current = await session.scalar(
        sqlalchemy.select(AgentUsageDaily.free_used_tokens).where(
            AgentUsageDaily.scope == scope,
            AgentUsageDaily.scope_id == scope_id,
            AgentUsageDaily.day == day,
        )
    )
    take = min(tokens, max(0, limit - (current or 0)))
    if take <= 0:
        return 0
    result = await session.execute(
        sqlalchemy.update(AgentUsageDaily)
        .where(
            AgentUsageDaily.scope == scope,
            AgentUsageDaily.scope_id == scope_id,
            AgentUsageDaily.day == day,
            AgentUsageDaily.free_used_tokens + take <= limit,
        )
        .values(free_used_tokens=AgentUsageDaily.free_used_tokens + take)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        return 0
    return take


# Non-final payers use compare-and-swap against the read value, retrying conflicts. After repeated contention,
# pass the remainder to the next payer; charging less is preferable to an incorrect accounting entry.
_CREDIT_CAS_RETRIES = 5


@with_tx
async def spend_credit(
    scope: str,
    scope_id: int,
    tokens: int,
    *,
    allow_debt: bool = False,
    session: AsyncSession | None = None,
) -> int:
    """Charge credits, append the ledger entry and return the actual amount charged.

    allow_debt=False (default): charge at most the available balance. Availability and deduction are separate,
    so compare-and-swap on the read balance retries after concurrent changes instead of using stale balances.
    Personal credits retain first-payer priority and cannot become debt through this path.

    allow_debt=True: charge unconditionally, permitting debt. Only the final payer uses this because usage
    is known after completion; record debt accurately and let the next preflight reject further calls.
    """
    assert session is not None
    if tokens <= 0:
        return 0
    await _ensure_credit_row(session, scope, scope_id)
    if allow_debt:
        balance = await session.scalar(
            sqlalchemy.update(AgentCredit)
            .where(AgentCredit.scope == scope, AgentCredit.scope_id == scope_id)
            .values(balance=AgentCredit.balance - tokens)
            .returning(AgentCredit.balance)
            .execution_options(synchronize_session=False)
        )
        assert balance is not None
        session.add(
            AgentCreditLedger(
                scope=scope,
                scope_id=scope_id,
                delta=-tokens,
                balance_after=balance,
                reason=CREDIT_REASON_USAGE,
            )
        )
        return tokens
    for _ in range(_CREDIT_CAS_RETRIES):
        observed = await session.scalar(
            sqlalchemy.select(AgentCredit.balance).where(
                AgentCredit.scope == scope, AgentCredit.scope_id == scope_id
            )
        )
        if observed is None or observed <= 0:
            return 0
        take = min(tokens, observed)
        result = await session.execute(
            sqlalchemy.update(AgentCredit)
            .where(
                AgentCredit.scope == scope,
                AgentCredit.scope_id == scope_id,
                AgentCredit.balance == observed,
            )
            .values(balance=observed - take)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:  # type: ignore[attr-defined]
            session.add(
                AgentCreditLedger(
                    scope=scope,
                    scope_id=scope_id,
                    delta=-take,
                    balance_after=observed - take,
                    reason=CREDIT_REASON_USAGE,
                )
            )
            return take
    return 0


@with_tx
async def record_usage(
    accounts: Sequence[Account],
    day: date,
    input_tokens: int,
    output_tokens: int,
    session: AsyncSession | None = None,
) -> None:
    """Record the run's request count and tokens for every account involved."""
    assert session is not None
    for scope, scope_id in accounts:
        await _ensure_usage_row(session, scope, scope_id, day)
        await session.execute(
            sqlalchemy.update(AgentUsageDaily)
            .where(
                AgentUsageDaily.scope == scope,
                AgentUsageDaily.scope_id == scope_id,
                AgentUsageDaily.day == day,
            )
            .values(
                requests=AgentUsageDaily.requests + 1,
                input_tokens=AgentUsageDaily.input_tokens + input_tokens,
                output_tokens=AgentUsageDaily.output_tokens + output_tokens,
            )
            .execution_options(synchronize_session=False)
        )


@with_tx
async def charge_tokens(
    accounts: Sequence[ChargeAccount],
    day: date,
    tokens: int,
    session: AsyncSession | None = None,
) -> None:
    """Settle actual token usage, allocating all charges in one transaction.

    Fixed order: free tokens (speaker, then group), followed by credits (speaker, then group).

    - Charge each free allocation only up to its limit and pass the remainder to the next account.
    - An unlimited free account (upper-level setting 0) makes the call free; stop allocation there.
    - For credits, non-final payers are capped at available funds (allow_debt=False); the final payer covers
      the entire remainder and may incur debt because actual run usage is only known afterward.
      Record that debt accurately and let the next preflight reject further calls.
    """
    assert session is not None
    if tokens <= 0:
        return
    remaining = tokens
    for account in accounts:
        if account.free_limit is None:
            return
        if account.free_limit <= 0:
            continue
        remaining -= await spend_free(
            account.scope,
            account.scope_id,
            day,
            account.free_limit,
            remaining,
            session=session,
        )
        if remaining <= 0:
            return
    for index, account in enumerate(accounts):
        if remaining <= 0:
            return
        # The final payer covers the remainder, including debt; earlier payers charge available credits only.
        remaining -= await spend_credit(
            account.scope,
            account.scope_id,
            remaining,
            allow_debt=index == len(accounts) - 1,
            session=session,
        )


@with_tx
async def record_and_charge(
    accounts: Sequence[Account],
    charge_accounts: Sequence[ChargeAccount],
    day: date,
    input_tokens: int,
    output_tokens: int,
    session: AsyncSession | None = None,
) -> None:
    """Record run usage and charge it in the same transaction.

    Empty charge_accounts records usage without billing: exempt accounts must still appear in /quota and panel
    statistics."""
    assert session is not None
    await record_usage(accounts, day, input_tokens, output_tokens, session=session)
    await charge_tokens(
        charge_accounts, day, input_tokens + output_tokens, session=session
    )


@with_session
async def get_credit(
    scope: str, scope_id: int, session: AsyncSession | None = None
) -> int:
    """Account balance; absent rows mean zero."""
    assert session is not None
    value = await session.scalar(
        sqlalchemy.select(AgentCredit.balance).where(
            AgentCredit.scope == scope, AgentCredit.scope_id == scope_id
        )
    )
    return value or 0


@with_tx
async def adjust_credits(
    scope: str,
    scope_id: int,
    delta: int,
    reason: str,
    ref: str | None = None,
    session: AsyncSession | None = None,
) -> int:
    """Adjust credits and record the ledger entry, creating rows if needed; return the new balance.

    One UPDATE + RETURNING prevents lost updates during concurrent adjustments.
    ref supplies an idempotency key for future payment callbacks: record each account/reference only once.
    A unique constraint rejects duplicates. Because debt is allowed, a grant smaller than the debt leaves
    a negative balance, letting the panel show the actual remaining debt.
    """
    assert session is not None
    await _ensure_credit_row(session, scope, scope_id)
    balance = await session.scalar(
        sqlalchemy.update(AgentCredit)
        .where(AgentCredit.scope == scope, AgentCredit.scope_id == scope_id)
        .values(balance=AgentCredit.balance + delta)
        .returning(AgentCredit.balance)
        .execution_options(synchronize_session=False)
    )
    assert balance is not None
    session.add(
        AgentCreditLedger(
            scope=scope,
            scope_id=scope_id,
            delta=delta,
            balance_after=balance,
            reason=reason,
            ref=ref,
        )
    )
    return balance


@with_tx
async def set_credit(
    scope: str,
    scope_id: int,
    balance: int,
    reason: str = CREDIT_REASON_ADMIN,
    session: AsyncSession | None = None,
) -> int:
    """Set an absolute balance and return the prior value for panel fields; record the difference in the ledger.

    Compare-and-swap on the read balance retries concurrent changes so old and final balances are consistent.
    with_for_update() is a no-op on SQLite; compare-and-swap provides the cross-database guarantee.
    Do not append a ledger entry when the target balance is unchanged.
    """
    assert session is not None
    await _ensure_credit_row(session, scope, scope_id)
    for _ in range(_CREDIT_CAS_RETRIES):
        observed = await session.scalar(
            sqlalchemy.select(AgentCredit.balance).where(
                AgentCredit.scope == scope, AgentCredit.scope_id == scope_id
            )
        )
        assert observed is not None
        if observed == balance:
            return observed
        result = await session.execute(
            sqlalchemy.update(AgentCredit)
            .where(
                AgentCredit.scope == scope,
                AgentCredit.scope_id == scope_id,
                AgentCredit.balance == observed,
            )
            .values(balance=balance)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:  # type: ignore[attr-defined]
            session.add(
                AgentCreditLedger(
                    scope=scope,
                    scope_id=scope_id,
                    delta=balance - observed,
                    balance_after=balance,
                    reason=reason,
                )
            )
            return observed
    raise RuntimeError(
        f"set_credit lost {_CREDIT_CAS_RETRIES} races on {scope}:{scope_id}"
    )


__all__ = [
    "CREDIT_REASON_ADMIN",
    "CREDIT_REASON_USAGE",
    "SCOPE_CHAT",
    "SCOPE_USER",
    "ChargeAccount",
    "adjust_credits",
    "charge_tokens",
    "get_credit",
    "get_usage",
    "record_and_charge",
    "record_usage",
    "set_credit",
    "spend_credit",
    "spend_free",
    "utc_day",
]
