"""Permission-scoped message deletion, separate from member moderation."""

import asyncio
import json

from pydantic import StrictInt
from pydantic_ai import RunContext
from pyrogram import enums
from pyrogram.errors import RPCError

from waku.common.telegram_authority import is_bot_admin
from waku.common.utils import get_reply_target
from waku.logger import logger

from ..datatype import ContextDeps

__all__ = ["delete_chat_messages", "message_deletion_instructions"]
_locks: dict[int, asyncio.Lock] = {}
_GROUPS = (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.FORUM)


def _topic_id(message):
    return (
        getattr(message, "message_thread_id", None)
        or getattr(message, "reply_to_top_message_id", None)
        or (
            getattr(message, "reply_to_message_id", None)
            if getattr(message, "topic_message", False)
            else None
        )
    )


def _has_delete_right(member, *, basic=False):
    return member.status == enums.ChatMemberStatus.OWNER or (
        member.status == enums.ChatMemberStatus.ADMINISTRATOR
        and (
            getattr(member.privileges, "can_delete_messages", False) is True
            or basic
            and member.privileges is None
        )
    )


async def _operator(deps):
    if await is_bot_admin(deps.user_id):
        return True
    if deps.message.chat.type not in _GROUPS:
        return False
    member = await asyncio.wait_for(
        deps.client.get_chat_member(deps.chat_id, deps.user_id), timeout=10
    )
    return bool(
        member.user
        and member.user.id == deps.user_id
        and not member.user.is_bot
        and _has_delete_right(
            member, basic=deps.message.chat.type == enums.ChatType.GROUP
        )
    )


def message_deletion_instructions():
    return (
        "Message deletion: use delete_chat_messages for an explicit request to delete a chat "
        "message. With count omitted it deletes exactly the directly replied message; resolve "
        "that reply via Telegram, never guess message IDs. Ordinary users may delete only "
        "their own or this bot's message, still subject to Telegram's actual bot rights. "
        "Bot owners/global admins or group admins with Delete Messages may delete any directly "
        "replied message. When an authorized admin asks to delete N messages, pass count=N: "
        "this means N recent messages SENT BY THIS BOT, never a purge of other users' messages. "
        "Batch limit 100; do not split larger requests or silently clamp. The current request "
        "is preserved. This utility does not require AI member-moderation to be enabled. "
        "Do not use file delete, shell or other tools to bypass these checks. Report only actual "
        "results, partial counts and missing rights in your own conversational language. "
        "No extra confirmation is needed for a clear authorized request."
    )


async def delete_chat_messages(
    ctx: RunContext[ContextDeps], count: StrictInt | None = None
) -> dict:
    """Delete the directly replied message, or N recent bot-only messages for admins.

    Omit count for one direct reply. Ordinary users can delete only their own
    messages or this bot's messages; admins with deletion authority can delete
    any replied message. count=1..100 is admin-only and deletes only messages
    authored by this bot in the current chat/topic, before the current request.
    Never accepts an arbitrary chat ID or purges other people's recent messages.
    """
    deps, message = ctx.deps, ctx.deps.message
    actor, chat = getattr(message, "from_user", None), getattr(message, "chat", None)
    if (
        not actor
        or actor.id != deps.user_id
        or actor.is_bot
        or not chat
        or chat.id != deps.chat_id
        or chat.type not in (enums.ChatType.PRIVATE, *_GROUPS)
        or getattr(message, "sender_chat", None)
        or any(
            getattr(message, name, None)
            for name in (
                "business_connection_id",
                "guest_query_id",
                "guest_bot_caller_user",
                "guest_bot_caller_chat",
            )
        )
    ):
        return {
            "status": "denied",
            "error": "An identified human in an ordinary Telegram chat is required.",
        }
    if count is not None and (type(count) is not int or not 1 <= count <= 100):
        return {
            "status": "invalid_count",
            "error": "Choose an integer count from 1 to 100.",
        }
    async with _locks.setdefault(deps.chat_id, asyncio.Lock()):
        key = "delete_chat_messages"
        if key in deps.moderation_results:
            return json.loads(deps.moderation_results[key])
        try:
            me = deps.client.me or await asyncio.wait_for(deps.client.get_me(), 10)
            if not me or not me.is_bot:
                return {
                    "status": "denied",
                    "error": "Could not verify the executing bot identity.",
                }
            needs_operator = False
            if count is None:
                reply = get_reply_target(message)
                if not reply or not reply.chat or reply.chat.id != deps.chat_id:
                    return {
                        "status": "missing_reply",
                        "error": "Reply directly to the message to delete.",
                    }
                target = await asyncio.wait_for(
                    deps.client.get_messages(deps.chat_id, reply.id), 10
                )
                if (
                    not target
                    or target.empty
                    or not target.chat
                    or target.chat.id != deps.chat_id
                    or target.id != reply.id
                    or target.id == message.id
                ):
                    return {
                        "status": "not_found",
                        "error": "The replied message is unavailable in this chat.",
                    }
                author_id = getattr(getattr(target, "from_user", None), "id", None)
                if getattr(target, "sender_chat", None):
                    author_id = None
                needs_operator = author_id not in (deps.user_id, me.id)
                if needs_operator and not await _operator(deps):
                    return {
                        "status": "denied",
                        "error": "Only bot admins or group admins with Delete Messages may delete another user's message.",
                    }
                if chat.type in _GROUPS and author_id != me.id:
                    bot = await asyncio.wait_for(
                        deps.client.get_chat_member(deps.chat_id, me.id), 10
                    )
                    if chat.type == enums.ChatType.GROUP and bot.privileges is None:
                        from .moderation import _basic_bot_privileges

                        await _basic_bot_privileges(deps.client, deps.chat_id, bot)
                    if not _has_delete_right(bot):
                        return {
                            "status": "bot_permission_missing",
                            "error": "The bot needs Telegram administrator permission Delete Messages to delete other accounts' messages, including yours.",
                        }
                ids, scanned = [target.id], 1
            else:
                if not await _operator(deps):
                    return {
                        "status": "denied",
                        "error": "Batch deletion requires bot admin or group admin with Delete Messages.",
                    }
                ids, scanned = [], 0
                cursor = message.id - 1
                topic = _topic_id(message)
                async with asyncio.timeout(20):
                    while cursor > 0 and scanned < 2000 and len(ids) < count:
                        chunk = list(range(cursor, max(0, cursor - 200), -1))
                        rows = await deps.client.get_messages(deps.chat_id, chunk)
                        for row in sorted(
                            rows or [],
                            key=lambda item: getattr(item, "id", 0) or 0,
                            reverse=True,
                        ):
                            if (
                                row
                                and not row.empty
                                and row.id in chunk
                                and row.chat
                                and row.chat.id == deps.chat_id
                                and getattr(getattr(row, "from_user", None), "id", None)
                                == me.id
                                and not getattr(row, "sender_chat", None)
                                and (topic is None or _topic_id(row) == topic)
                                and row.id not in ids
                            ):
                                ids.append(row.id)
                                if len(ids) == count:
                                    break
                        scanned += len(chunk)
                        cursor = chunk[-1] - 1
                # Authority may change during the bounded history scan.
                if not await _operator(deps):
                    return {
                        "status": "denied",
                        "error": "Deletion authority was revoked before dispatch.",
                    }
            if needs_operator and not await _operator(deps):
                return {
                    "status": "denied",
                    "error": "Deletion authority was revoked before dispatch.",
                }
            if not ids:
                return {
                    "status": "not_found",
                    "deleted_count": 0,
                    "error": "No matching bot messages found in the recent 2000 message IDs.",
                }
        except (RPCError, TimeoutError) as error:
            return {
                "status": "lookup_failed",
                "error": f"Could not verify messages/permissions ({type(error).__name__}); nothing was deleted.",
            }

        result = {
            "status": "uncertain",
            "requested_count": count or 1,
            "message_ids": ids,
            "error": "Deletion outcome is unknown; do not retry automatically.",
        }
        # Record before dispatch: cancellation/timeout must not cause a second deletion.
        deps.moderation_results[key] = json.dumps(result)
        deps.side_effects_started = True
        try:
            deleted = await asyncio.wait_for(
                deps.client.delete_messages(deps.chat_id, ids), 15
            )
        except RPCError as error:
            result.update(
                status="failed",
                error=f"Telegram rejected deletion ({type(error).__name__}); check the bot's Delete Messages permission and Telegram message restrictions.",
            )
        except (TimeoutError, OSError):
            pass
        else:
            if type(deleted) is int:
                result = {
                    "status": "deleted" if deleted == (count or 1) else "partial",
                    "requested_count": count or 1,
                    "deleted_count": deleted,
                    "message_ids": ids,
                    "scanned_message_ids": scanned,
                }
        deps.moderation_results[key] = json.dumps(result)
        logger.info(
            "AI message deletion: actor={} chat={} status={} count={}",
            deps.user_id,
            deps.chat_id,
            result["status"],
            result.get("deleted_count"),
        )
        return result
