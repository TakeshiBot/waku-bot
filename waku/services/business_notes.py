"""Store explicit contact requests and relay them solely to their account owner."""

from __future__ import annotations

import asyncio
import html
import time
from datetime import datetime

from pyrogram import enums
from pyrogram.errors import BadRequest, FloodWait, Forbidden, RPCError, Unauthorized

from waku.config import app_config
from waku.database import business as dialogs
from waku.database import business_notes as repository
from waku.i18n import t
from waku.logger import logger
from waku.services import business_context as context
from waku.timezone import BOT_TIMEZONE

_DRAIN_LOCK = asyncio.Lock()
_DRAIN_TASKS: set[asyncio.Task] = set()


async def deliver_note(client, note, stage, *, retry_failed=False):
    owner_id = note["owner_id"]
    if (
        not app_config.business_chat_enabled
        or not context.settings_for(owner_id)["notes"]
    ):
        return "pending"
    try:
        connection = await context.ensure_connection(client, note["connection_id"])
    except Exception as error:
        # Nothing was claimed or sent, so a later healthy scheduler run can try
        # again. Never let a temporary connection lookup abort the chat reply.
        logger.warning("Business note connection unavailable: {}", type(error).__name__)
        return "pending"
    if not connection.is_enabled or connection.user.id != owner_id:
        return "pending"
    from waku import database

    try:
        config = await database.get_user_config(connection.user)
    except Exception as error:
        logger.warning("Business note owner lookup failed: {}", type(error).__name__)
        return "pending"
    contact = note["contact"]
    key = "reminder" if stage == "reminder" else "owner_note"
    text = t("bot.business." + key, locale=config.lang).format(
        name=html.escape(contact.get("name") or str(note["peer_id"])),
        content=html.escape(note["content"]),
    )
    text += (
        f'\n<a href="tg://user?id={note["peer_id"]}">'
        + t("bot.business.contact_link", locale=config.lang)
        + "</a>"
    )
    if note["remind_at"]:
        text += (
            "\n"
            + t("bot.business.appointment", locale=config.lang)
            + ": "
            + datetime.fromtimestamp(note["remind_at"], BOT_TIMEZONE).strftime(
                "%H:%M %d/%m/%Y"
            )
        )
    try:
        connection = await context.ensure_connection(client, note["connection_id"])
    except Exception as error:
        logger.warning("Business note connection recheck failed: {}", type(error).__name__)
        return "pending"
    if (
        not connection.is_enabled
        or not app_config.business_chat_enabled
        or not context.settings_for(owner_id)["notes"]
    ):
        return "pending"
    claimed = await repository.claim_delivery(
        note["id"], owner_id, stage, retry_failed=retry_failed
    )
    if claimed is None:
        return note[stage + "_status"]
    try:
        result = await asyncio.wait_for(
            client.send_message(owner_id, text, parse_mode=enums.ParseMode.HTML),
            timeout=25,
        )
        status = (
            "sent"
            if result and type(getattr(result, "id", None)) is int and result.id > 0
            else "uncertain"
        )
    except FloodWait:
        status = "failed"
    except (BadRequest, Forbidden, Unauthorized):
        status = "failed"
    except RPCError:
        status = "uncertain"
    except asyncio.CancelledError:
        await repository.finish_delivery(note["id"], owner_id, stage, "uncertain")
        raise
    except Exception as error:
        logger.warning("Business note delivery unconfirmed: {}", type(error).__name__)
        status = "uncertain"
    await repository.finish_delivery(note["id"], owner_id, stage, status)
    return status


async def accept_requests(client, message, requests):
    connection = await context.ensure_connection(client, message.business_connection_id)
    owner_id, peer_id = connection.user.id, message.chat.id
    if not connection.is_enabled or not context.settings_for(owner_id)["notes"]:
        return []
    state = await dialogs.get_state(owner_id, peer_id)
    events = state.get("events", [])
    boundary = max(
        (item["id"] for item in events if item["role"] in {"owner", "assistant"}),
        default=0,
    )
    eligible = {
        row["id"]: row
        for row in events
        if row["role"] == "contact"
        and row["id"] > boundary
        and row["timestamp"] >= time.time() - 300
        and not row.get("forwarded")
    }
    receipts = []
    for request in requests[:3]:
        if not request.explicit_request or request.source_message_id not in eligible:
            continue
        due = None
        if request.remind_at is not None:
            at = request.remind_at
            if at.tzinfo is None:
                at = at.replace(tzinfo=BOT_TIMEZONE)
            due = at.timestamp()
            if not time.time() < due <= time.time() + 366 * 86400:
                receipts.append(
                    {
                        "saved": False,
                        "reason": "reminder_time_invalid",
                        "needs_time": True,
                    }
                )
                continue
        note = await repository.create_note(
            owner_id,
            peer_id,
            connection.id,
            request.source_message_id,
            request.content,
            context.profile(message.from_user),
            due,
        )
        # A newly revoked connection or changed setting is checked at delivery too.
        try:
            status = await deliver_note(client, note, "notification")
        except Exception as error:
            # The note was durably recorded first. A relay problem must not
            # suppress the useful Business reply that follows this assessment.
            logger.warning("Business note relay unavailable: {}", type(error).__name__)
            status = "pending"
        receipts.append(
            {
                "saved": True,
                "owner_dm_status": status,
                "remind_at": due,
                "needs_time": request.needs_time,
                "content": note["content"],
            }
        )
    return receipts


async def drain_notes(client=None, *, owner_id=None, retry_failed=False):
    task = asyncio.current_task()
    if task is not None:
        _DRAIN_TASKS.add(task)
    try:
        if not app_config.business_chat_enabled or _DRAIN_LOCK.locked():
            return
        if client is None:
            from waku.bot.client import client
        async with _DRAIN_LOCK:
            for note in await repository.list_notes(owner_id, due=owner_id is None):
                try:
                    for stage in ("notification", "reminder"):
                        if note[stage + "_status"] in (
                            ["pending", "failed"] if retry_failed else ["pending"]
                        ):
                            if stage == "reminder" and (
                                note["remind_at"] is None
                                or note["remind_at"] > time.time()
                            ):
                                continue
                            await deliver_note(
                                client, note, stage, retry_failed=retry_failed
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning(
                        "Business note recovery failed: {}", type(error).__name__
                    )
    finally:
        if task is not None:
            _DRAIN_TASKS.discard(task)


async def shutdown_business_notes():
    """Stop relay jobs before the native client and database are closed."""
    current = asyncio.current_task()
    tasks = {task for task in _DRAIN_TASKS if task is not current and not task.done()}
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
