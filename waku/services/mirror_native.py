"""Read stock Mirrorz replies using its exact formats and stored command IDs.

This adapter never guesses a task from its filename, a user mention or a URL
alone. A native reply is bound to the original command, or a status block's
explicit Telegram command hyperlink. Human-facing replies remain AI generated.
Completions with cloud links hidden/private and no output buttons stay unknown;
they require a structured result receipt to confirm without guessing.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from waku.database import mirror_tasks as repository

_MISSING = object()


def is_forwarded(message):
    origin = getattr(message, "forward_origin", _MISSING)
    # Avoid touching the deprecated SDK property when modern metadata exists.
    if origin is not _MISSING:
        return bool(origin)
    return bool(getattr(message, "forward_date", None))


_DISABLED = {
    "The Mirror command is currently disabled.",
    "The Leech command is currently disabled.",
    "Bulk downloads are currently disabled.",
    "Multi-downloads are currently disabled. Please try without the -i flag.",
    "Seeding is currently disabled. Please try without the -d flag.",
    "FFmpeg commands are currently disabled.",
    "No Upload Destination!",
    "Wrong Upload Destination!",
    "No dump chat selected!",
    "No Gofile Token Found!",
    "No BuzzHeavier Token Found!",
    "No PixelDrain Key Found!",
    "Start the bot and try again!",
}
_RESULT_LABEL = re.compile(
    r"(?:(?:cloud|mega|rclone|index|view|download|gofile|buzzheavier|pixeldrain|doodstream|streamtape|mixdrop|litterbox|catbox|1fichier|uploadhaven|uploadnow|fileditch|filebin|gdrive|drive|filemoon|send|filepress|filelions|gdtot|krakenfiles)\s*(?:link)?|[A-Za-z][A-Za-z0-9 _-]{0,50} Link)\Z",
    re.I,
)
_CANCEL_REASONS = {
    "stopped by user!",
    "download cancelled by user!",
    "cancelled by user!",
    "task have been removed from queue/download",
    "your upload has been stopped!",
    "your clone has been stopped!",
    "upload has been manually cancelled",
    "upload has been manually cancelled!",
    "telegram clone stopped by user!",
    "your youtube upload has been cancelled!",
}


def _cancelled(reason):
    return (
        reason.strip().lower() in _CANCEL_REASONS
        or re.fullmatch(
            r"(?:[A-Za-z][A-Za-z0-9 -]{0,40} upload has been cancelled!|(?:Download|Upload|Clone|Archive|Extract|FFmpeg|Metadata) stopped by user!)",
            reason.strip(),
            flags=re.I,
        )
        is not None
    )


def _untag(message, text):
    """Only remove the actual Waku account's initial mention, never free prose."""
    own = getattr(getattr(message, "_client", None), "me", None)
    username = getattr(own, "username", None)
    if username and text.lower().startswith("@" + username.lower() + " "):
        return text[len(username) + 2 :].strip()
    for entity in getattr(message, "entities", None) or []:
        if (
            getattr(entity, "offset", None) == 0
            and getattr(getattr(entity, "user", None), "id", None)
            == getattr(own, "id", None)
            and getattr(own, "id", None) is not None
        ):
            length = getattr(entity, "length", None)
            if type(length) is int and length > 0:
                try:
                    return (
                        text.encode("utf-16-le")[length * 2 :]
                        .decode("utf-16-le")
                        .strip()
                    )
                except UnicodeError:
                    pass
    return None


def _safe_url(value):
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or any(c.isspace() for c in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _label(value):
    return re.sub(r"^[^A-Za-z0-9]+", "", str(value or "")).strip()


def _result_links(message, task):
    result = []
    seen = {task["url"].rstrip("/")}
    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or []:
        for button in row:
            url = getattr(button, "url", None)
            label = _label(getattr(button, "text", ""))
            if not _safe_url(url) or not _RESULT_LABEL.fullmatch(label):
                continue
            if re.search(
                r"\b(?:source|original|help|support|donate|profile|task)\b", label, re.I
            ):
                continue
            parsed = urlsplit(url)
            # Source/task/profile/help links never become a download button.
            if (
                parsed.hostname.lower()
                in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}
                or url.rstrip("/") in seen
            ):
                continue
            seen.add(url.rstrip("/"))
            result.append({"label": label[:100], "url": url})
    return result[:20]


def parse_reply(message, task):
    """Return an actual native terminal event, or None for menus/progress."""
    text = str(
        getattr(message, "text", None) or getattr(message, "caption", None) or ""
    ).strip()
    if not text or "WAKU_MIRROR_EVENT " in text:
        return None
    failed = (
        text in _DISABLED
        or text.startswith("ERROR:")
        or bool(re.match(r"^\W*Task Checks\s*:\s*\n", text) and "Name" in text)
        or bool(
            re.match(r"^\W*Download Stopped!\s*\n", text)
            and "Due To" in text
            and "Task By" in text
        )
        or bool(
            re.match(r"^\W*Limit Breached:\s*\n", text)
            and "Task Size" in text
            and "In Mode" in text
        )
        or bool(re.fullmatch(r"(?:Rclone Config:|NO TOKEN!) .+ not Exists!", text))
        or bool(re.fullmatch(r"Unknown dump chat '.+'! Configured dumps: .+", text))
    )
    payload = dict(
        v=1,
        request_id=task["id"],
        command_message_id=task["command_message_id"],
        bridge_protocol=False,
    )
    if re.match(r"^\W*Download Stopped!\s*\n", text):
        reason = next(
            (
                line.split("→", 1)[1].strip()
                for line in text.splitlines()
                if "Due To" in line and "→" in line
            ),
            "",
        )
        if _cancelled(reason):
            return dict(payload, event="cancelled", error=reason)
    upload_error = _untag(message, text)
    if upload_error:
        if _cancelled(upload_error):
            return dict(payload, event="cancelled", error=upload_error[:2048])
        if upload_error.startswith(
            (
                "Upload failed: ",
                "Files Corrupted or unable to upload. ",
                "Mega clone error: ",
                "MegaUpload failed for ",
                "Internal error: ",
                "No files to upload. In case you have filled EXCLUDED_EXTENSIONS,",
            )
        ) or upload_error in {
            "Mega credentials not configured for this user.",
            "Failed to get Mega root node.",
            "Mega import returned no node.",
            "No files to upload in folder",
        }:
            return dict(payload, event="failed", error=upload_error[:2048])
    if failed:
        return dict(payload, event="failed", error=text[:2048])
    if not all(
        marker in text
        for marker in ("Task Size", "Task By", "Time Taken", "In Mode", "Out Mode")
    ):
        return None
    links = _result_links(message, task)
    provider_errors = re.findall(r"(?m)^\s*[A-Za-z][A-Za-z0-9 ]*: Error - .+", text)
    # Stock multiupload reports a completion-shaped message even if every
    # provider failed. A visible provider error without any output is failure.
    if not links:
        if provider_errors:
            return dict(payload, event="failed", error=text[:2048])
        return None
    result = dict(
        payload, event="completed", links=links, name=text.splitlines()[0][:1024]
    )
    if provider_errors:
        result["error"] = "\n".join(line.strip() for line in provider_errors)[:2048]
    return result


def _entity_links(message, text):
    encoded = text.encode("utf-16-le")
    entities = (
        getattr(message, "entities", None)
        or getattr(message, "caption_entities", None)
        or []
    )
    for entity in entities:
        url = getattr(entity, "url", None)
        offset, length = getattr(entity, "offset", -1), getattr(entity, "length", -1)
        if (
            not _safe_url(url)
            or type(offset) is not int
            or type(length) is not int
            or offset < 0
            or length < 1
        ):
            continue
        try:
            before = encoded[: offset * 2].decode("utf-16-le")
            label = encoded[offset * 2 : (offset + length) * 2].decode("utf-16-le")
        except UnicodeError:
            continue
        if label == "Link":
            yield len(before), url


def _command_id(url, chat):
    parsed = urlsplit(url)
    if (
        parsed.hostname.lower()
        not in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    parts = parsed.path.strip("/").split("/")
    if (
        len(parts) == 3
        and parts[0] == "c"
        and str(chat.id) == "-100" + parts[1]
        and parts[2].isdigit()
    ):
        return int(parts[2])
    username = getattr(chat, "username", None)
    if (
        len(parts) == 2
        and username
        and parts[0].lower() == username.lower()
        and parts[1].isdigit()
    ):
        return int(parts[1])
    return None


async def _progress(message, actor, chat):
    text = str(
        getattr(message, "text", None) or getattr(message, "caption", None) or ""
    )
    own_id = getattr(getattr(getattr(message, "_client", None), "me", None), "id", None)
    if type(own_id) is not int or not own_id > 0:
        return False
    headers = list(re.finditer(r"(?m)^\d+\. [^\n]+", text))
    consumed = False
    for position, url in _entity_links(message, text):
        command_id = _command_id(url, chat)
        if command_id is None:
            continue
        header = next(
            (item for item in reversed(headers) if item.start() <= position), None
        )
        if header is None:
            continue
        end = next(
            (item.start() for item in headers if item.start() > position), len(text)
        )
        block = text[header.start() : end]
        if re.search(r"Task By [^\n]*\(\s*#ID" + str(own_id) + r"\s*\)", block) is None:
            continue
        cancel = re.search(
            r"(?m)^\W*Stop\s*→\s*(?P<token>/(?P<command>c|cancel)_(?P<gid>[A-Za-z0-9_-]{1,64})(?:@[A-Za-z0-9_]+)?)(?=\s|$)",
            block,
        )
        if cancel is None:
            continue
        task = await repository.find_mirror_command(chat.id, actor.id, command_id)
        if task is None:
            continue
        consumed = True
        if task["status"] in repository.TERMINAL:
            continue
        username = task.get("mirror_bot_username")
        cancellation = cancel.group("token").split("@", 1)[0]
        if username:
            cancellation += "@" + username
        await repository.apply_event(
            dict(
                v=1,
                request_id=task["id"],
                event="accepted",
                command_message_id=command_id,
                gid=cancel.group("gid"),
                name=re.sub(r"^\d+\. ", "", header.group(0))[:1024],
                cancel_command=cancellation,
                bridge_protocol=False,
            ),
            sender_id=actor.id,
            chat_id=chat.id,
        )
    return consumed


async def receive_native(message):
    """Consume native messages only when backed by a trusted tracked command."""
    actor, chat = getattr(message, "from_user", None), getattr(message, "chat", None)
    if (
        not actor
        or not getattr(actor, "is_bot", False)
        or not chat
        or getattr(message, "sender_chat", None)
        or is_forwarded(message)
        or any(
            getattr(message, key, None)
            for key in (
                "business_connection_id",
                "guest_query_id",
            )
        )
    ):
        return False
    reply_id = getattr(message, "reply_to_message_id", None)
    if type(reply_id) is int and reply_id > 0:
        if await _cancellation_reply(message, actor, chat, reply_id):
            # A response to the cancellation command is backend data. Only a
            # lifecycle receipt on the original task can confirm cancellation.
            return True
        task = await repository.find_mirror_command(chat.id, actor.id, reply_id)
        if task is None:
            task = await _fast_reply(message, actor, chat, reply_id)
        if task is not None:
            if task["status"] not in repository.TERMINAL:
                payload = parse_reply(message, task)
                if payload is not None:
                    await repository.apply_event(
                        payload, sender_id=actor.id, chat_id=chat.id
                    )
                else:
                    await _progress(message, actor, chat)
            # A menu or an unknown error must not cause a bot-to-bot AI loop.
            # It also must not be falsely reported as terminal.
            return True
    return await _progress(message, actor, chat)


async def _cancellation_reply(message, actor, chat, reply_id):
    if await repository.find_cancel_command(chat.id, actor.id, reply_id):
        return True
    # Telegram can deliver a bot's response before our send await returns and
    # its cancellation message ID is persisted. Verify the real own-bot reply
    # and its exact original task anchor in that narrow interval.
    reply = getattr(message, "reply_to_message", None)
    own_id = getattr(getattr(getattr(message, "_client", None), "me", None), "id", None)
    if (
        not reply
        or type(own_id) is not int
        or getattr(reply, "id", None) != reply_id
        or getattr(getattr(reply, "from_user", None), "id", None) != own_id
        or getattr(getattr(reply, "chat", None), "id", None) != chat.id
        or is_forwarded(reply)
    ):
        return False
    original_id = getattr(reply, "reply_to_message_id", None)
    if type(original_id) is not int or original_id <= 0:
        return False
    task = await repository.find_mirror_command(chat.id, actor.id, original_id)
    if task is None or task.get("cancel_status") not in {"sending", "sent", "uncertain"}:
        return False
    command = getattr(reply, "text", None)
    return command == "/cancel" or (
        task.get("gid")
        and command == f"/c_{task['gid']}@{task.get('mirror_bot_username')}"
    )


async def _fast_reply(message, actor, chat, reply_id):
    """Bind a reply received before Telegram's command-send await returned."""
    reply = getattr(message, "reply_to_message", None)
    own_id = getattr(getattr(getattr(message, "_client", None), "me", None), "id", None)
    if (
        not reply
        or type(own_id) is not int
        or getattr(reply, "id", None) != reply_id
        or getattr(getattr(reply, "from_user", None), "id", None) != own_id
        or getattr(getattr(reply, "chat", None), "id", None) != chat.id
        or is_forwarded(reply)
    ):
        return None
    lines = str(getattr(reply, "text", None) or "").splitlines()
    if len(lines) != 2 or re.fullmatch(r"Waku-Request: [0-9a-f]{32}", lines[1]) is None:
        return None
    task = await repository.find_unbound_mirror_request(
        lines[1].split(": ", 1)[1], chat.id, actor.id
    )
    if task is None:
        return None
    expected = f"/mirror@{task.get('mirror_bot_username')} {task['url']}"
    if lines[0] != expected:
        return None
    task["command_message_id"] = reply_id
    return task
