import json

from pydantic_ai import RunContext
from pyrogram.enums import ChatMemberStatus

from waku import common
from waku.affection import get_affection_rank
from waku.common.utils import GROUP_CHAT_TYPES
from waku.config import app_config
from waku.logger import logger
from waku.services import personality

from .. import datatype, state


def _calculate_block_duration(requested_minutes: int, affection_rank: float) -> int:
    """Calculate effective block duration based on affection rank.

    Formula: effective = requested * max(0.1, 1 - affection_rank^2)

    - affection_rank 0.0 (lowest): factor = 1.0, no reduction
    - affection_rank 0.5: factor = 0.75
    - affection_rank 0.8: factor = 0.36
    - affection_rank 1.0 (highest): factor = 0.1, minimum 10%
    """
    factor = max(0.1, 1.0 - affection_rank**2)
    return max(1, round(requested_minutes * factor))


async def block_user(
    ctx: RunContext[datatype.ContextDeps],
    duration_minutes: int,
    user_id: int | None = None,
    reason: str = "",
) -> str:
    """React to the current group sender's direct personal harassment.

    The backend progresses ignore -> 36s mute -> 3m -> 15m -> rejoinable kick.
    Cannot target someone else, run in DM, punish admins, or permanently ban.
    duration_minutes only suggests the initial ignore (1-5m), reduced by affection.
    Give your own short factual reason, never a copied warning or quoted insult.
    """
    deps, message = ctx.deps, ctx.deps.message

    def result(**values):
        return json.dumps(values, ensure_ascii=False)

    if (
        not app_config.telegram_enabled
        or not message
        or not message.chat
        or message.chat.type not in GROUP_CHAT_TYPES
        or message.chat.id != deps.chat_id
        or not message.from_user
        or message.from_user.id != deps.user_id
        or message.from_user.is_bot
        or getattr(message, "sender_chat", None)
        or getattr(message, "business_connection_id", None)
        or getattr(message, "guest_query_id", None)
        or getattr(message, "guest_bot_caller_user", None)
        or getattr(message, "guest_bot_caller_chat", None)
        or user_id is not None
        and user_id != deps.user_id
    ):
        return result(
            status="denied",
            reason="Only the real current human group sender can be selected.",
        )
    if not isinstance(reason, str) or not reason.strip():
        return result(
            status="denied",
            reason="Provide your own brief reason based on this sender's actual direct message.",
        )
    from . import moderation

    async def check():
        member = await deps.client.get_chat_member(deps.chat_id, deps.user_id)
        await moderation._personality_protected(ctx, member)
        if member.status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED}:
            raise moderation.ModerationDenied("member")
        if await common.memttlcache.get(state.user_block_immune_key(deps.user_id)):
            raise moderation.ModerationDenied("protected")

    try:
        await check()
        rank = await get_affection_rank(deps.user_id)
        minutes = _calculate_block_duration(max(1, min(5, duration_minutes)), rank)

        async def apply(action, seconds):
            try:
                await check()
            except Exception:
                return {
                    "status": "denied",
                    "reason": "Current member/roles/immunity could not be verified.",
                }
            if action == "ignore":
                return {"status": "applied"}
            return await moderation.personality_penalty(ctx, action, seconds)

        deps.side_effects_started = True
        facts = await personality.react(
            "telegram",
            deps.chat_id,
            deps.user_id,
            message.id,
            apply,
            ignore_seconds=minutes * 60,
        )
        logger.info(
            "Group personality reaction: chat={}, user={}, status={}, action={}",
            deps.chat_id,
            deps.user_id,
            facts.get("status"),
            facts.get("action"),
        )
        return result(**facts)
    except Exception as error:
        logger.debug("Group personality reaction denied: {}", type(error).__name__)
        return result(
            status="denied",
            reason="Group membership/protected roles/immunity could not be cleared.",
        )


async def is_user_blocked(user_id: int, chat_id: int | None = None) -> bool:
    """A personality ignore applies only in its originating group, never DMs."""
    return await personality.is_ignored(
        "telegram", chat_id if chat_id is not None and chat_id < 0 else None, user_id
    )
