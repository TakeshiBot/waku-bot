"""Submit one tracked cancellation; verified admin tools may manage all tasks."""

from __future__ import annotations

import asyncio
import re

from pyrogram import enums, types
from pyrogram.errors import BadRequest, Forbidden, Unauthorized

from waku.database import mirror_tasks as repository


def _selection_error(task):
    if task["status"] in repository.TERMINAL:
        return {"status": "already_terminal", "task": task}
    if task.get("cancel_status"):
        return {
            "status": "already_requested",
            "cancellation_status": task["cancel_status"],
            "task": task,
        }
    if (
        type(task.get("command_message_id")) is not int
        or task["command_message_id"] <= 0
    ):
        return {
            "status": "not_ready",
            "task": task,
            "error": "Original Mirror command message ID is not known.",
        }
    return None


def _outcome(status, task, error=None):
    result = {
        "status": "already_terminal"
        if task and task["status"] in repository.TERMINAL
        else status,
        "task": task,
    }
    if task and task.get("cancel_status"):
        result["cancellation_status"] = task["cancel_status"]
    if error:
        result["error"] = error
    return result


async def cancel(
    client,
    *,
    requester_id,
    origin_chat_id,
    request_id=None,
    reply_message_id=None,
    origin_platform="telegram",
    admin_scope=False,
):
    if (
        type(requester_id) is not int
        or requester_id <= 0
        or type(origin_chat_id) is not int
        or origin_chat_id == 0
        or (
            request_id is not None
            and (
                not isinstance(request_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) is None
            )
        )
        or (
            reply_message_id is not None
            and (type(reply_message_id) is not int or reply_message_id <= 0)
        )
    ):
        return {
            "status": "invalid_request",
            "error": "Invalid cancellation routing identity.",
        }
    if origin_platform not in {"telegram", "discord"}:
        return {"status": "invalid_request", "error": "Invalid platform."}
    platform_args = (
        {"origin_platform": origin_platform} if origin_platform != "telegram" else {}
    )
    if admin_scope:
        platform_args["admin_scope"] = True
    candidates = await repository.cancellation_candidates(
        requester_id,
        origin_chat_id,
        request_id=request_id,
        reply_message_id=reply_message_id,
        **platform_args,
    )
    if not candidates and request_id is None and reply_message_id is not None:
        # An unrelated chat reply must not hide tasks started elsewhere. Actual
        # task replies above always take precedence over the global selection.
        candidates = await repository.cancellation_candidates(
            requester_id,
            origin_chat_id,
            **platform_args,
        )
    if not candidates:
        return {
            "status": "not_found",
            "error": "No tracked Mirror task matches the permitted scope and supplied context.",
        }
    if len(candidates) != 1:
        return {
            "status": "ambiguous",
            "candidates": [
                {
                    key: item.get(key)
                    for key in (
                        "request_id",
                        "name",
                        "url",
                        "status",
                        "origin_chat_id",
                        "origin_message_id",
                        "requester_id",
                        "gid",
                    )
                }
                for item in candidates[:20]
            ],
            "more_candidates": len(candidates) > 20,
        }
    task = candidates[0]
    early = _selection_error(task)
    if early:
        return early
    username = task.get("mirror_bot_username")
    if (
        not isinstance(username, str)
        or re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) is None
    ):
        return {
            "status": "identity_mismatch",
            "task": task,
            "error": "Stored Mirror username is invalid.",
        }
    # Resolve the numerical identity, not a potentially reassigned username.
    # Refuse a rename until the stored route is deliberately reconciled.
    try:
        bot = await asyncio.wait_for(
            client.get_users(task["mirror_bot_id"]), timeout=10
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        return {
            "status": "identity_unavailable",
            "task": task,
            "error": type(error).__name__,
        }
    if (
        getattr(bot, "id", None) != task["mirror_bot_id"]
        or not getattr(bot, "is_bot", False)
        or str(getattr(bot, "username", "")).lower() != username.lower()
    ):
        return {
            "status": "identity_mismatch",
            "task": task,
            "error": "Mirror bot identity/username no longer matches the stored task route.",
        }
    claim_args = {"admin_scope": True} if admin_scope else {}
    claimed = await repository.claim_cancellation(
        task["id"], requester_id, **claim_args
    )
    if claimed is None:
        return {"status": "not_found"}
    task = claimed["task"]
    if not claimed["claimed"]:
        return _selection_error(task) or {"status": "already_requested", "task": task}
    # A terminal event arriving during identity lookup or reservation wins.
    current = await repository.get(task["id"])
    if current is None or current["status"] in repository.TERMINAL:
        return {"status": "already_terminal", "task": current or task}
    gid = current.get("gid")
    # Stock Mirrorz splits the entire command at '_'; an addressed /cancel
    # breaks when a username contains '_'. Its actual /c_GID@Bot form works.
    # Before GID is known, a bare /cancel uses the exact original task reply.
    command = (
        f"/c_{gid}@{username}"
        if isinstance(gid, str)
        and len(gid) != 6
        and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", gid)
        else "/cancel"
    )
    try:
        sent = await asyncio.wait_for(
            client.send_message(
                task["mirror_chat_id"],
                command,
                parse_mode=enums.ParseMode.DISABLED,
                reply_parameters=types.ReplyParameters(
                    message_id=task["command_message_id"]
                ),
                link_preview_options=types.LinkPreviewOptions(is_disabled=True),
            ),
            timeout=25,
        )
        message_id = getattr(sent, "id", None)
        if type(message_id) is not int or message_id <= 0:
            result = await repository.finish_cancellation(
                task["id"],
                requester_id,
                "uncertain",
                error="Telegram returned no cancellation command message ID.",
            )
            return _outcome("cancel_uncertain", result)
        result = await repository.finish_cancellation(
            task["id"], requester_id, "sent", command_message_id=message_id
        )
        return _outcome("cancel_requested", result)
    except (BadRequest, Forbidden, Unauthorized) as error:
        result = await repository.finish_cancellation(
            task["id"], requester_id, "failed", error=type(error).__name__
        )
        return _outcome("cancel_failed", result, type(error).__name__)
    except asyncio.CancelledError:
        await asyncio.shield(
            repository.finish_cancellation(
                task["id"],
                requester_id,
                "uncertain",
                error="Cancellation command send interrupted.",
            )
        )
        raise
    except Exception as error:
        result = await repository.finish_cancellation(
            task["id"], requester_id, "uncertain", error=type(error).__name__
        )
        return _outcome("cancel_uncertain", result, type(error).__name__)
