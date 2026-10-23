"""Trusted Telegram mirror transport; human-facing text belongs to the AI."""

from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import urlsplit

from pyrogram import enums, types
from pyrogram.errors import BadRequest, Forbidden, Unauthorized

from waku.database import mirror_tasks as repository
from waku.services.mirror_commands import (
    MAX_BATCH,
    build_command,
    expand_sources,
    normalize_mode,
    normalize_options,
    render_options,
    validate_source,
)

TERMINAL = repository.TERMINAL
claim_notification = repository.claim_notification
finish_notification = repository.finish_notification
recover_delivery_claims = repository.recover_delivery_claims
get_task = repository.get
expire_acknowledgements = repository.expire_acknowledgements


def is_mirror_media(message):
    return bool(
        message
        and any(
            getattr(message, kind, None)
            for kind in (
                "document",
                "photo",
                "video",
                "audio",
                "voice",
                "video_note",
                "sticker",
                "animation",
            )
        )
    )


def _message_link(chat_id, message_id):
    if type(chat_id) is int and str(chat_id).startswith("-100"):
        return f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"
    return ""


def _source_link(message):
    chat = message.chat
    username = getattr(chat, "username", None)
    if (
        chat.id < 0
        and isinstance(username, str)
        and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username)
    ):
        return f"https://t.me/{username}/{message.id}"
    return _message_link(chat.id, message.id)


async def pending_notifications():
    await repository.expire_acknowledgements()
    return await repository.pending_notifications()


def _http_url(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 4096
        or any(char.isspace() for char in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


async def submit(
    client,
    *,
    origin_chat_id,
    origin_message_id,
    requester_id,
    mirror_chat_id,
    mirror_bot_id,
    mirror_bot_username,
    url,
    source_message=None,
    mode="mirror",
    options=None,
    item_index=0,
    batch_size=1,
    origin_platform="telegram",
    origin_guild_id=None,
    origin_request_text="",
):
    if origin_platform not in {"telegram", "discord"}:
        raise ValueError("Invalid origin platform")
    if origin_platform != "telegram" and source_message is not None:
        raise ValueError("Telegram media requires a Telegram origin")
    mode = normalize_mode(mode)
    options = normalize_options(mode, options)
    render_options(mode, options)
    if (
        type(item_index) is not int
        or type(batch_size) is not int
        or not 1 <= batch_size <= MAX_BATCH
        or not 0 <= item_index < batch_size
    ):
        raise ValueError("Invalid transfer batch index")
    has_source = source_message is not None
    if has_source:
        if (
            not is_mirror_media(source_message)
            or getattr(getattr(source_message, "chat", None), "id", None)
            != origin_chat_id
            or type(getattr(source_message, "id", None)) is not int
            or source_message.id <= 0
            or getattr(source_message, "has_protected_content", False)
            or getattr(source_message.chat, "has_protected_content", False)
            or not str(mirror_chat_id).startswith("-100")
        ):
            raise ValueError("Invalid Telegram attachment source or Mirror group")
        url = _source_link(source_message)
    elif not validate_source(url, mode):
        raise ValueError("Mirror requires a valid transfer source")
    elif len(expand_sources([url], mode)) != 1:
        raise ValueError(
            "Telegram ranges must be expanded into individually tracked batch sources"
        )
    if (
        not isinstance(mirror_bot_username, str)
        or re.fullmatch(r"[A-Za-z0-9_]{5,32}", mirror_bot_username.lstrip("@")) is None
    ):
        raise ValueError("Invalid mirror bot username")
    if (
        any(
            type(value) is not int or value == 0
            for value in (
                origin_chat_id,
                origin_message_id,
                requester_id,
                mirror_chat_id,
                mirror_bot_id,
            )
        )
        or origin_message_id <= 0
        or requester_id <= 0
        or mirror_chat_id >= 0
        or mirror_bot_id <= 0
    ):
        raise ValueError("Invalid mirror routing identity")
    bot_id = client.me.id
    identity = f"{bot_id}:{origin_chat_id}:{origin_message_id}"
    if origin_platform != "telegram":
        identity = f"{origin_platform}:{identity}"
    token = hashlib.sha256(
        (identity + (f":{item_index}" if item_index else "")).encode()
    ).hexdigest()[:32]
    # Validate the complete wire command before reserving or copying anything.
    build_command(
        mode,
        mirror_bot_username.lstrip("@"),
        url or "https://t.me/c/1/1",
        options,
        token,
    )
    details = {
        "mirror_bot_username": mirror_bot_username.lstrip("@"),
        "mirror_mode": mode,
        "mirror_options": options or {},
        "batch_index": item_index,
        "batch_size": batch_size,
    }
    if origin_platform == "discord":
        details.update(
            origin_platform=origin_platform,
            origin_guild_id=origin_guild_id,
            origin_request_text=str(origin_request_text)[:2000],
        )
    if has_source:
        details.update(
            source_chat_id=origin_chat_id,
            source_message_id=source_message.id,
            source_route=(
                "direct"
                if origin_chat_id == mirror_chat_id
                or url.startswith("https://t.me/")
                and not url.startswith("https://t.me/c/")
                else "copy"
            ),
        )
    task, claimed = await repository.reserve(
        dict(
            id=token,
            origin_chat_id=origin_chat_id,
            origin_message_id=origin_message_id,
            requester_id=requester_id,
            mirror_chat_id=mirror_chat_id,
            mirror_bot_id=mirror_bot_id,
            url=url,
            details=details,
        )
    )
    if not claimed:
        return task
    if has_source and details["source_route"] == "copy":
        # Copy after reserving, so neither model retries nor repeated dispatches
        # can create extra copies/tasks. The Mirror bot can then access the file
        # in its own group, even when it is absent from the source chat or DM.
        try:
            staged = await asyncio.wait_for(
                client.copy_message(
                    mirror_chat_id,
                    origin_chat_id,
                    source_message.id,
                    disable_notification=True,
                    # None removes the original buttons. Telegram rejects
                    # an explicitly supplied inline keyboard with no rows.
                    reply_markup=None,
                ),
                timeout=30,
            )
            staged_id = getattr(staged, "id", None)
            if type(staged_id) is not int or staged_id <= 0:
                raise ValueError("Telegram returned no copied attachment ID")
            url = _message_link(mirror_chat_id, staged_id)
            await repository.record_source(token, url, staged_id)
        except asyncio.CancelledError:
            await asyncio.shield(
                repository.mark_sent(
                    token,
                    status="send_failed",
                    error="Source attachment preparation was interrupted; no Mirror command was sent",
                )
            )
            raise
        except Exception as error:
            return await repository.mark_sent(
                token,
                status="send_failed",
                error=f"Source attachment preparation failed ({type(error).__name__}); no Mirror command was sent",
            )
    command = build_command(mode, mirror_bot_username.lstrip("@"), url, options, token)
    try:
        sent = await asyncio.wait_for(
            client.send_message(
                mirror_chat_id,
                command,
                parse_mode=enums.ParseMode.DISABLED,
                link_preview_options=types.LinkPreviewOptions(is_disabled=True),
            ),
            timeout=30,
        )
        message_id = getattr(sent, "id", None)
        if type(message_id) is not int or message_id <= 0:
            return await repository.mark_sent(
                token,
                status="send_uncertain",
                error="Telegram returned no command message ID; do not resend automatically",
            )
        return await repository.mark_sent(token, message_id)
    except (BadRequest, Forbidden, Unauthorized) as error:
        return await repository.mark_sent(
            token, status="send_failed", error=type(error).__name__
        )
    except asyncio.CancelledError:
        await asyncio.shield(
            repository.mark_sent(
                token,
                status="send_uncertain",
                error="Command send interrupted; do not resend automatically",
            )
        )
        raise
    except Exception as error:
        return await repository.mark_sent(
            token, status="send_uncertain", error=type(error).__name__
        )


async def wait_ack(request_id, timeout=25):
    """Wait for durable accepted/terminal state without retransmitting anything."""
    deadline = asyncio.get_running_loop().time() + min(max(timeout, 0), 60)
    while True:
        task = await repository.get(request_id)
        if (
            task is None
            or task["status"] not in {"sending", "sent", "received", "send_uncertain"}
            or asyncio.get_running_loop().time() >= deadline
        ):
            return task
        await asyncio.sleep(0.25)


async def receive_event(payload, *, sender_id, chat_id):
    """Reject malformed/forged events before any persistent state mutation."""
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or re.fullmatch(r"[0-9a-f]{32}", str(payload.get("request_id", ""))) is None
    ):
        return None
    if (
        payload.get("event")
        not in {"received", "accepted", "completed", "failed", "cancelled"}
        or type(payload.get("command_message_id")) is not int
        or payload["command_message_id"] <= 0
    ):
        return None
    cleaned = {
        key: payload[key] for key in ("v", "request_id", "event", "command_message_id")
    }
    cleaned["bridge_protocol"] = True
    for key, limit in (
        ("gid", 128),
        ("name", 1024),
        ("cancel_command", 256),
        ("error", 2048),
    ):
        if key in payload and isinstance(payload[key], str):
            cleaned[key] = payload[key][:limit]
    if type(payload.get("size")) is int and payload["size"] >= 0:
        cleaned["size"] = payload["size"]
    if isinstance(payload.get("links"), list):
        cleaned["links"] = [
            {"label": str(item.get("label") or "Download")[:100], "url": item["url"]}
            for item in payload["links"][:20]
            if isinstance(item, dict) and _http_url(item.get("url"))
        ]
    return await repository.apply_event(cleaned, sender_id=sender_id, chat_id=chat_id)


async def recovery_status_requests(client):
    """Use stock Mirror status; opt into receipt replay only after an addon event."""
    native_routes = set()
    for task in await repository.claim_status_queries():
        username = task.get("mirror_bot_username")
        if (
            not isinstance(username, str)
            or re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) is None
        ):
            continue
        if task.get("bridge_protocol"):
            command = f"/wakustatus@{username} {task['id']}"
        else:
            route = (task["mirror_chat_id"], task["mirror_bot_id"])
            if route in native_routes:
                continue
            native_routes.add(route)
            command = f"/status@{username} {client.me.id}"
        try:
            await asyncio.wait_for(
                client.send_message(
                    task["mirror_chat_id"],
                    command,
                    parse_mode=enums.ParseMode.DISABLED,
                    link_preview_options=types.LinkPreviewOptions(is_disabled=True),
                ),
                timeout=15,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Queries retry on the next scheduled interval, task creation never does.
            continue
