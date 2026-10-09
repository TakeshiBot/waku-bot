"""Quota for each agent call, billed by token usage.

Fixed billing order: speaker's free tokens, group's free tokens, speaker's credits, group's credits.

Timing: usage is known only after completion. can_start performs preflight, allowing a run if any payer has
quota left; settle charges actual usage after success. A run may therefore consume more than
the remaining quota. Record excess as debt and reject the next preflight until an operator grants credit.
Preflight approval cannot guarantee full affordability, but accounting now reflects the actual cost.

Accounts represent the speaker and conversation. Anonymous admins and channel identities have no personal
account (sender_chat is a channel or the group itself); they must use allocated group quota or be rejected.
Operators control this in the panel through group allocations, credits or exemption.

This covers conversational agents (user-triggered wake, ask callbacks and follow-up); bot-initiated helper
agents have no billable speaker and are outside this quota.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyrogram
import pyrogram.enums
import pyrogram.types
from pydantic_ai.usage import RunUsage

from kmua import database
from kmua.common.memory_store import memttlcache
from kmua.config import app_config
from kmua.enums import ChatID
from kmua.i18n import i18n
from kmua.logger import logger
from kmua.plugins.agent import state as agent_state

SCOPE_USER = database.SCOPE_USER
SCOPE_CHAT = database.SCOPE_CHAT

# Anonymous admins/service accounts have no personal account (see kmua/enums.py::ChatID); charge the group.
PSEUDO_USER_IDS = frozenset(
    (int(ChatID.ANONYMOUS_ADMIN), int(ChatID.SERVICE_CHAT), int(ChatID.FAKE_CHANNEL))
)

# Minimum interval between group quota notices, also used as the exhausted-marker TTL.
_NOTICE_TTL_SECONDS = 60


@dataclass(frozen=True)
class Subject:
    """The payer accounts for one call.

    user_id=None means the speaker has no personal account (anonymous admin, channel or service account).
    In private chats chat_id is the user, so accounts() does not create a separate conversation account.
    """

    user_id: int | None
    chat_id: int | None
    in_group: bool

    def accounts(self) -> list[tuple[str, int]]:
        """Accounts in billing order: speaker first, then the group."""
        accounts: list[tuple[str, int]] = []
        if self.user_id is not None:
            accounts.append((SCOPE_USER, self.user_id))
        if self.in_group and self.chat_id is not None:
            accounts.append((SCOPE_CHAT, self.chat_id))
        return accounts

    @property
    def cache_key(self) -> str:
        """Stable key for exhausted markers and notice throttling."""
        return f"{self.chat_id or 0}:{self.user_id or 0}"


@dataclass(frozen=True)
class AccountState:
    """A read-only account snapshot measured in tokens."""

    scope: str
    scope_id: int
    requests_today: int
    free_used_tokens: int
    free_limit_tokens: int | None
    credits: int
    input_tokens_today: int
    output_tokens_today: int

    @property
    def free_left(self) -> int | None:
        if self.free_limit_tokens is None:
            return None
        return max(0, self.free_limit_tokens - self.free_used_tokens)

    @property
    def exhausted(self) -> bool:
        """Nonpositive credits and exhausted free tokens cause the next call to be rejected."""
        return self.free_left == 0 and self.credits <= 0


@dataclass(frozen=True)
class QuotaState:
    """A call's quota snapshot, with account order matching Subject.accounts()."""

    accounts: list[AccountState]
    exempt: bool

    @property
    def user(self) -> AccountState | None:
        return next((a for a in self.accounts if a.scope == SCOPE_USER), None)

    @property
    def chat(self) -> AccountState | None:
        return next((a for a in self.accounts if a.scope == SCOPE_CHAT), None)

    @property
    def exhausted(self) -> bool:
        return not self.exempt and (
            not self.accounts or all(a.exhausted for a in self.accounts)
        )


def subject_for_chat(user_id: int | None, chat: pyrogram.types.Chat | None) -> Subject:
    """Construct from (speaker ID, conversation) when the caller already knows the speaker."""
    if chat is None or chat.id is None:
        return Subject(user_id=user_id, chat_id=None, in_group=False)
    in_group = chat.type not in (
        pyrogram.enums.ChatType.PRIVATE,
        pyrogram.enums.ChatType.BOT,
    )
    return Subject(user_id=user_id, chat_id=chat.id, in_group=in_group)


def subject_of(message: pyrogram.types.Message) -> Subject:
    """Derive payer accounts from a message.

    Match middlewares/before.py: sender_chat takes priority for anonymous admins and channel
    messages, which have no personal account and are recorded only against the group.
    """
    chat = message.chat
    user_id: int | None = None
    if message.sender_chat is None and message.from_user is not None:
        candidate = message.from_user.id
        if candidate not in PSEUDO_USER_IDS:
            user_id = candidate
    return subject_for_chat(user_id, chat)


async def _plan(subject: Subject) -> tuple[list[database.ChargeAccount], bool]:
    """Payer accounts with their free-token limits and the call's exemption status."""
    exempt = False
    accounts: list[database.ChargeAccount] = []
    if subject.user_id is not None:
        if subject.user_id in app_config.owners:
            exempt = True
        else:
            user = await database.get_user_by_id(subject.user_id)
            exempt = user is not None and user.is_bot_global_admin
        limit = app_config.agent_quota_free_daily_tokens
        accounts.append(
            database.ChargeAccount(
                SCOPE_USER, subject.user_id, None if limit <= 0 else limit
            )
        )
    if subject.in_group and subject.chat_id is not None:
        policy = await database.get_chat_policy(subject.chat_id)
        exempt = exempt or policy.agent_quota_exempt
        accounts.append(
            database.ChargeAccount(
                SCOPE_CHAT,
                subject.chat_id,
                database.agent_free_daily_tokens(policy),
            )
        )
    return accounts, exempt


async def get_state(subject: Subject) -> QuotaState:
    """Full read-only usage/quota/credits and exemption state for /quota and rejection notices."""
    accounts, exempt = await _plan(subject)
    day = database.utc_day()
    states: list[AccountState] = []
    for account in accounts:
        requests, free_used, input_tokens, output_tokens = await database.get_usage(
            account.scope, account.scope_id, day
        )
        states.append(
            AccountState(
                scope=account.scope,
                scope_id=account.scope_id,
                requests_today=requests,
                free_used_tokens=free_used,
                free_limit_tokens=account.free_limit,
                credits=await database.get_credit(account.scope, account.scope_id),
                input_tokens_today=input_tokens,
                output_tokens_today=output_tokens,
            )
        )
    return QuotaState(accounts=states, exempt=exempt)


async def can_start(subject: Subject) -> bool:
    """Preflight permits the call if any payer has free tokens or a positive credit balance.

    Do not charge during preflight; actual cost is known after completion. This checks current affordability;
    if this call exhausts quota or incurs debt, subsequent preflight checks reject calls.

    Read the DB every time instead of caching rejection, which could block newly credited users for a full
    TTL. One lookup per account is cheap compared with fetching history, downloading media and
    calling the model. notify_exhausted throttles notices without masking account state.
    """
    accounts, exempt = await _plan(subject)
    return exempt or await _has_capacity(accounts)


async def _has_capacity(accounts: list[database.ChargeAccount]) -> bool:
    day = database.utc_day()
    for account in accounts:
        if account.free_limit is None:
            # This account has unlimited free usage (agent_quota_free_daily_tokens = 0).
            return True
        _, free_used, _, _ = await database.get_usage(
            account.scope, account.scope_id, day
        )
        if free_used < account.free_limit:
            return True
        if await database.get_credit(account.scope, account.scope_id) > 0:
            return True
    return False


async def settle(subject: Subject, usage: RunUsage | None) -> None:
    """Settle the run's actual token usage.

    Record usage for every account involved, including calls paid by a group pool or another balance, so the panel
    shows actual group consumption. database.charge_tokens applies deductions in the fixed order within
    the same transaction as accounting.
    Exemption skips billing, not usage statistics; /quota and the panel must still show token usage for owners,
    global administrators and exempt groups.
    """
    accounts, exempt = await _plan(subject)
    day = database.utc_day()
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    await database.record_and_charge(
        subject.accounts(),
        () if exempt else accounts,
        day,
        input_tokens,
        output_tokens,
    )


def _notice_key(subject: Subject) -> str:
    return agent_state.quota_notice_key(subject.cache_key)


async def notify_exhausted(
    message: pyrogram.types.Message, subject: Subject, state: QuotaState, lang: str
) -> None:
    """Notify exhaustion: at most once per account every 60 seconds in groups, on every call in private chats."""
    if subject.in_group:
        key = _notice_key(subject)
        if await memttlcache.get(key):
            return
        await memttlcache.set(key, True, ttl=_NOTICE_TTL_SECONDS)
    try:
        await message.reply_text(exhausted_text(state, lang))
    except Exception as e:
        logger.warning(f"Failed to send quota notice: {e.__class__.__name__} - {e}")


_TOKEN_UNITS: tuple[tuple[int, str], ...] = (
    (1_000, "k"),
    (1_000_000, "M"),
    (1_000_000_000, "B"),
    (1_000_000_000_000, "T"),
)


def fmt_tokens(value: int) -> str:
    """Format tokens compactly and consistently for panel and bot messages.

    Round to one decimal before choosing the unit so boundary values roll up. Show numbers below one thousand
    in full so small quotas remain precise.
    """
    if abs(value) < _TOKEN_UNITS[0][0]:
        return str(value)
    scale, suffix = _TOKEN_UNITS[-1]
    for candidate, candidate_suffix in _TOKEN_UNITS:
        if abs(float(f"{value / candidate:.1f}")) < 1_000:
            scale, suffix = candidate, candidate_suffix
            break
    return f"{value / scale:.1f}{suffix}"


def exhausted_text(state: QuotaState, lang: str) -> str:
    user = state.user
    if user is None or user.free_limit_tokens is None:
        return i18n.t("bot.msg.agent.quota.exhausted_group", locale=lang)
    return i18n.t("bot.msg.agent.quota.exhausted", locale=lang)


def status_text(state: QuotaState, lang: str) -> str:
    """Show unlimited group accounts too: this is where members can see the group's remaining quota."""
    lines: list[str] = []
    user = state.user
    if user is not None:
        used_total = user.input_tokens_today + user.output_tokens_today
        if user.free_limit_tokens is None:
            lines.append(
                i18n.t("bot.msg.agent.quota.status_unlimited", locale=lang).format(
                    used=user.requests_today, tokens=fmt_tokens(used_total)
                )
            )
        else:
            lines.append(
                i18n.t("bot.msg.agent.quota.calls_today", locale=lang).format(
                    used=user.requests_today, tokens=fmt_tokens(used_total)
                )
            )
            lines.append(
                i18n.t("bot.msg.agent.quota.free_line", locale=lang).format(
                    used=fmt_tokens(user.free_used_tokens),
                    limit=fmt_tokens(user.free_limit_tokens),
                )
            )
            lines.append(
                i18n.t("bot.msg.agent.quota.credits_line", locale=lang).format(
                    credits=fmt_tokens(user.credits)
                )
            )
    chat = state.chat
    if chat is not None and (chat.free_limit_tokens or chat.credits):
        lines.append(
            i18n.t("bot.msg.agent.quota.chat_line", locale=lang).format(
                used=fmt_tokens(chat.free_used_tokens),
                limit=fmt_tokens(chat.free_limit_tokens or 0),
                credits=fmt_tokens(chat.credits),
            )
        )
    if not lines:
        return i18n.t("bot.msg.agent.quota.status_none", locale=lang)
    return "\n".join(lines)


__all__ = [
    "PSEUDO_USER_IDS",
    "AccountState",
    "QuotaState",
    "Subject",
    "can_start",
    "exhausted_text",
    "fmt_tokens",
    "get_state",
    "notify_exhausted",
    "settle",
    "status_text",
    "subject_for_chat",
    "subject_of",
]
