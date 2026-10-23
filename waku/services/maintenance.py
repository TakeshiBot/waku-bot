"""Durable restart/update receipts shared with the host deployment worker."""

from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified, RPCError

from waku.i18n import t
from waku.logger import logger
from waku.version import commit_info

DIRECTORY = Path("data/maintenance")
BOOT_ID = secrets.token_hex(16)
LOCK = asyncio.Lock()
_UPDATE_STAGES = {"queued", "deploying", "success", "failed"}
_PERMANENT_RECEIPT_ERRORS = {
    "MESSAGE_ID_INVALID",
    "MESSAGE_EDIT_TIME_EXPIRED",
    "MESSAGE_AUTHOR_REQUIRED",
    "MESSAGE_DELETE_FORBIDDEN",
    "CHAT_WRITE_FORBIDDEN",
    "CHAT_ADMIN_REQUIRED",
    "CHANNEL_PRIVATE",
    "USER_IS_BLOCKED",
    "INPUT_USER_DEACTIVATED",
}
_retry_after = {}


def _write(path: Path, value: dict) -> None:
    directory = path.parent
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if hasattr(os, "chown") and os.geteuid() == 0:
            owner = directory.parent.stat()
            os.chown(directory, owner.st_uid, owner.st_gid)
    owner = directory.stat()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            # The host worker must read receipts written by a root Docker bot.
            if hasattr(os, "fchown") and os.geteuid() == 0:
                os.fchown(stream.fileno(), owner.st_uid, owner.st_gid)
            stream.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("rb") as stream:
        value = json.loads(stream.read(64 * 1024))
    if not isinstance(value, dict):
        raise ValueError("Invalid maintenance receipt")
    return value


def worker_ready() -> bool:
    try:
        heartbeat = _read(DIRECTORY / "worker.json")
        return bool(heartbeat and 0 <= time.time() - heartbeat["time"] < 30)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def save_request(action: str, message, user_id: int, locale: str) -> dict:
    pending = _read(DIRECTORY / "request.json")
    if pending and pending.get("id") != (_read(DIRECTORY / "delivered.json") or {}).get(
        "id"
    ):
        raise ValueError("busy")
    request = {
        "id": secrets.token_hex(16),
        "action": action,
        "chat_id": message.chat.id,
        "message_id": message.id,
        "user_id": user_id,
        "locale": locale,
        "created_at": time.time(),
        "boot_id": BOOT_ID,
        "before": commit_info(),
    }
    bot_id = getattr(getattr(getattr(message, "_client", None), "me", None), "id", None)
    if type(bot_id) is int:
        request["bot_id"] = bot_id
    _write(DIRECTORY / "request.json", request)
    return request


def mark_delivered(request: dict, *, outcome="delivered", error=None) -> None:
    result = {"id": request["id"], "outcome": outcome}
    if error:
        result["error"] = error
    _write(DIRECTORY / "delivered.json", result)


async def _undeliverable(request, reason):
    # Telegram cannot restore an absent message or let another bot edit it.
    # Finish only the notification so later maintenance requests are not blocked.
    await asyncio.to_thread(
        mark_delivered, request, outcome="undeliverable", error=reason
    )
    _retry_after.pop(request["id"], None)
    logger.warning(
        "Maintenance {} receipt cannot be edited (chat={}, message={}): {}. "
        "Receipt closed; no replacement message sent.",
        request["action"],
        request["chat_id"],
        request["message_id"],
        reason,
    )


def _tr(key: str, locale: str, **values) -> str:
    return t("bot.restart." + key, locale=locale).format(**values)


async def complete_pending(client) -> None:
    async with LOCK:
        request = None
        try:
            request = await asyncio.to_thread(_read, DIRECTORY / "request.json")
            if not request:
                return
            delivered = await asyncio.to_thread(_read, DIRECTORY / "delivered.json")
            if delivered and delivered.get("id") == request["id"]:
                return
            if _retry_after.get(request["id"], (0, 0))[1] > time.monotonic():
                return
            action = request["action"]
            locale = request["locale"]
            result = None
            if action == "restart":
                if request["boot_id"] == BOOT_ID:
                    return
                status = "restart_success"
            elif action == "update":
                result = await asyncio.to_thread(_read, DIRECTORY / "result.json")
                if not result or result.get("id") != request["id"]:
                    return
                stage = result.get("status")
                if stage not in _UPDATE_STAGES or stage in {"queued", "deploying"}:
                    return
                status = "update_success" if stage == "success" else "update_failed"
            else:
                return
            bot_id = getattr(getattr(client, "me", None), "id", None)
            if type(bot_id) is int and request.get("bot_id", bot_id) != bot_id:
                await _undeliverable(request, "BOT_ID_CHANGED")
                return
            info = commit_info()
            before = request["before"].get("commit") or "?"
            after = info["commit"] or "?"
            elapsed = max(0, int(time.time() - request["created_at"]))
            text = _tr(status, locale)
            text += "\n\n" + _tr(
                "result_details",
                locale,
                before=html.escape(before),
                commit=html.escape(after),
                subject=html.escape(info["subject"] or "—"),
                date=html.escape(info["date"] or "—"),
                seconds=elapsed,
            )
            if result and result.get("status") == "failed":
                text += "\n\n" + _tr("update_log", locale)
            try:
                await asyncio.wait_for(
                    client.edit_message_text(
                        request["chat_id"],
                        request["message_id"],
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=None,
                    ),
                    timeout=30,
                )
            except MessageNotModified:
                pass
            except RPCError as error:
                if error.ID in _PERMANENT_RECEIPT_ERRORS:
                    await _undeliverable(request, error.ID)
                    return
                raise
            await asyncio.to_thread(mark_delivered, request)
            _retry_after.pop(request["id"], None)
        except Exception as error:
            # Retain recoverable failures with backoff, without a new message.
            if request and isinstance(request.get("id"), str):
                attempts = _retry_after.get(request["id"], (0, 0))[0] + 1
                delay = (
                    max(5, error.value)
                    if isinstance(error, FloodWait)
                    else min(300, 5 * 2 ** min(attempts, 6))
                )
                _retry_after.clear()
                _retry_after[request["id"]] = (attempts, time.monotonic() + delay)
            logger.warning(
                "Maintenance receipt delivery failed: {}", type(error).__name__
            )


async def monitor(client) -> None:
    while True:
        await complete_pending(client)
        await asyncio.sleep(5)
