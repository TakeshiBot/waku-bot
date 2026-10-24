"""Group-local, bounded escalation selected by the AI, never by word matching."""

from __future__ import annotations

import asyncio
import time
from weakref import WeakValueDictionary

from waku.common.memory_store import memttlcache
from waku.database import group_moderation as store

WINDOW = 86400
STEPS = (("ignore", 60), ("mute", 36), ("mute", 180), ("mute", 900), ("kick", 0))
_locks = WeakValueDictionary()


def instructions(tool: str, *, platform: str = "telegram") -> str:
    behavior = (
        "The backend only briefly ignores the sender in this server. Repeated incidents "
        "may lead to another brief ignore, never a timeout, mute, kick or ban. "
        "Do not use other tools to restrict or remove someone for personal anger. "
        if platform == "discord"
        else "The backend selects progression per group/user: brief ignore, mute 36s, "
        "mute 3m, mute 15m, then kick with rejoining allowed; reset after 24h without an "
        "applied action. Never jump levels or call other moderation tools for personal anger. "
    )
    return (
        "Group-only personality boundaries: you may judge the current sender's own "
        "direct message as personally insulting, harassing or persistently disrespectful "
        "and choose to use " + tool + ". No fixed words trigger this automatically. "
        "Ordinary disagreement, criticism, friendly teasing, profanity not directed at you, "
        "quoted/forwarded insults, roleplay and asking about this feature are not by themselves "
        "grounds for punishment. Use context and your personality; a brief verbal warning "
        "is often enough. Do not solicit permission from the offending sender. "
        "This is the sole autonomous exception to explicit-request moderation rules. "
        "Use it at most once per actual new offensive message, with your own short factual "
        "reason. "
        + behavior
        + "Never ban anyone autonomously or punish someone mentioned by the sender. "
        "Owners, bot admins and group administrators are protected. Backend checks actual "
        "rights; no permission bypass is allowed. Only describe actions confirmed by the "
        "tool, in your own short natural reply. A denied/uncertain result is not success. "
        "No fixed warning messages; write your own response. This feature is unavailable "
        "in DMs, Business and Guest mode."
    )


def _block_key(platform, user_id):
    return f"{'agent' if platform == 'telegram' else 'discord_agent'}_user_blocked:{user_id}"


async def is_ignored(platform, scope, user_id):
    if scope is None:
        return False
    if platform == "telegram" and await memttlcache.get(
        f"agent_user_block_immune:{user_id}"
    ):
        return False
    values = await memttlcache.get(_block_key(platform, user_id))
    # Old global boolean blocks must not keep suppressing DMs or other groups.
    until = values.get(str(scope), 0) if isinstance(values, dict) else 0
    return isinstance(until, (int, float)) and until > time.time()


async def _ignore(platform, scope, user_id, seconds):
    values = await memttlcache.get(_block_key(platform, user_id))
    now = time.time()
    values = {
        key: until
        for key, until in (values.items() if isinstance(values, dict) else [])
        if isinstance(until, (int, float)) and until > now
    }
    values[str(scope)] = now + seconds
    await memttlcache.set(_block_key(platform, user_id), values, ttl=WINDOW)


async def react(platform, scope, user_id, message_id, apply, *, ignore_seconds=60):
    """Adapters authenticate the actual sender and protect roles before calling.

    A durable reservation precedes external actions. Unknown outcomes do not
    cause a resend or a higher penalty; administrators can resolve them normally.
    """
    if (
        platform not in {"telegram", "discord"}
        or any(
            type(value) is not int or value == 0
            for value in (scope, user_id, message_id)
        )
        or user_id < 0
        or message_id < 0
    ):
        return {"status": "denied", "reason": "Invalid group/sender identity."}
    key = (platform, user_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        db_scope = -abs(scope)
        field = f"personality_{platform}"
        current = (await store.get_state(db_scope, user_id)).get(field, {})
        now = time.time()
        if now - current.get("last_at", 0) >= WINDOW:
            current = {}
        if message_id <= current.get("message_id", 0):
            return {
                "status": "already_processed",
                "result": current.get("result", {"status": "uncertain"}),
            }
        if current.get("pending"):
            return {
                "status": "uncertain",
                "reason": "A previous action has an unknown outcome; do not retry or escalate.",
            }
        if current.get("until", 0) > now:
            return {
                "status": "cooldown",
                "reason": "The last action is still active; do not escalate.",
            }
        steps = (("ignore", 60),) if platform == "discord" else STEPS
        level = min(int(current.get("level", 0)), len(steps) - 1)
        action, seconds = steps[level]
        if action == "ignore":
            seconds = max(60, min(300, int(ignore_seconds)))
        reservation = dict(current, pending=True, last_at=now, message_id=message_id)
        await store.patch_state(db_scope, user_id, {field: reservation})
        try:
            result = await apply(action, seconds)
            if result.get("status") == "applied" and action == "ignore":
                await _ignore(platform, scope, user_id, seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            result = {
                "status": "uncertain",
                "reason": "Action outcome could not be confirmed; do not retry.",
            }
        applied = result.get("status") == "applied"
        result = {**result, "action": action, "seconds": seconds, "level": level + 1}
        saved = dict(
            reservation,
            pending=result.get("status") in {"uncertain", "partial"},
            last_at=time.time() if applied else current.get("last_at", now),
            level=min(level + 1, len(steps)) if applied else level,
            until=time.time() + seconds if applied else 0,
            result=result,
        )
        await store.patch_state(db_scope, user_id, {field: saved})
        return result
