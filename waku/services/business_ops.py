"""Owner-only Business operations, using Telegram's official Bot API methods.

Management calls do not poll updates or replace the native MTProto chat client.
urllib keeps the token-bearing request URL out of httpx's INFO request logger.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from waku.config import app_config
from waku.database import business as repository
from waku.services import business_context as context

OPERATIONS = {
    "send_message": ("can_reply", "sendMessage"),
    "send_media": ("can_reply", None),
    "edit_message": ("can_reply", "editMessageText"),
    "mark_read": ("can_read_messages", "readBusinessMessage"),
    "delete_sent": ("can_delete_sent_messages", "deleteBusinessMessages"),
    "delete_all": ("can_delete_all_messages", "deleteBusinessMessages"),
    "set_name": ("can_edit_name", "setBusinessAccountName"),
    "set_username": ("can_edit_username", "setBusinessAccountUsername"),
    "set_bio": ("can_edit_bio", "setBusinessAccountBio"),
    "set_photo": ("can_edit_profile_photo", "setBusinessAccountProfilePhoto"),
    "remove_photo": ("can_edit_profile_photo", "removeBusinessAccountProfilePhoto"),
    "get_gifts": ("can_view_gifts_and_stars", "getBusinessAccountGifts"),
    "get_stars": ("can_view_gifts_and_stars", "getBusinessAccountStarBalance"),
    "convert_gift": ("can_convert_gifts_to_stars", "convertGiftToStars"),
    "gift_settings": ("can_change_gift_settings", "setBusinessAccountGiftSettings"),
    "upgrade_gift": ("can_transfer_and_upgrade_gifts", "upgradeGift"),
    "transfer_gift": ("can_transfer_and_upgrade_gifts", "transferGift"),
    "transfer_stars": ("can_transfer_stars", "transferBusinessAccountStars"),
    "post_story": ("can_manage_stories", "postStory"),
    "edit_story": ("can_manage_stories", "editStory"),
    "delete_story": ("can_manage_stories", "deleteStory"),
}
READ_ONLY = {"get_gifts", "get_stars"}
_FIELDS = {
    "send_message": {"chat_id", "text"},
    "send_media": {"chat_id", "caption", "media"},
    "edit_message": {"chat_id", "message_id", "text"},
    "mark_read": {"chat_id", "message_id"},
    "delete_sent": {"chat_id", "message_ids"},
    "delete_all": {"chat_id", "message_ids"},
    "set_name": {"first_name", "last_name"},
    "set_username": {"username"},
    "set_bio": {"bio"},
    "set_photo": {"media", "is_public"},
    "remove_photo": {"is_public"},
    "get_gifts": {"offset"},
    "get_stars": set(),
    "convert_gift": {"owned_gift_id"},
    "gift_settings": {"show_gift_button", "accepted_gift_types"},
    "upgrade_gift": {"owned_gift_id", "star_count", "keep_original_details"},
    "transfer_gift": {"owned_gift_id", "new_owner_chat_id", "star_count"},
    "transfer_stars": {"star_count"},
    "post_story": {"media", "caption", "active_period"},
    "edit_story": {"media", "caption", "story_id"},
    "delete_story": {"story_id"},
}
GIFT_TYPES = (
    "unlimited_gifts",
    "limited_gifts",
    "unique_gifts",
    "premium_subscription",
    "gifts_from_channels",
)


def validate_arguments(operation, arguments):
    if (
        operation not in _FIELDS
        or not isinstance(arguments, dict)
        or set(arguments) != _FIELDS[operation]
    ):
        raise BusinessOperationError("invalid_input")
    for key in (
        "chat_id",
        "message_id",
        "story_id",
        "new_owner_chat_id",
        "star_count",
        "active_period",
    ):
        if key in arguments and (
            type(arguments[key]) is not int
            or arguments[key] < (0 if key == "star_count" else 1)
        ):
            raise BusinessOperationError("invalid_input")
    if "message_ids" in arguments and (
        not isinstance(arguments["message_ids"], list)
        or not 1 <= len(arguments["message_ids"]) <= 100
        or any(
            type(value) is not int or not 0 < value < 2**31
            for value in arguments["message_ids"]
        )
    ):
        raise BusinessOperationError("invalid_input")
    for key, limit in (
        ("text", 4096),
        ("caption", 2048 if operation in {"post_story", "edit_story"} else 1024),
        ("first_name", 64),
        ("last_name", 64),
        ("bio", 140),
        ("username", 32),
        ("owned_gift_id", 256),
        ("offset", 512),
    ):
        if key in arguments and (
            not isinstance(arguments[key], str)
            or len(arguments[key]) > limit
            or key in {"text", "first_name", "owned_gift_id"}
            and not arguments[key]
        ):
            raise BusinessOperationError("invalid_input")
    if operation == "transfer_stars" and arguments["star_count"] > 10000:
        raise BusinessOperationError("invalid_input")
    if operation == "transfer_stars" and arguments["star_count"] == 0:
        raise BusinessOperationError("invalid_input")
    for key in ("is_public", "keep_original_details"):
        if key in arguments and type(arguments[key]) is not bool:
            raise BusinessOperationError("invalid_input")
    if operation == "post_story" and arguments["active_period"] not in {
        21600,
        43200,
        86400,
        172800,
    }:
        raise BusinessOperationError("invalid_input")
    if operation == "gift_settings" and (
        type(arguments["show_gift_button"]) is not bool
        or not isinstance(arguments["accepted_gift_types"], dict)
        or set(arguments["accepted_gift_types"]) != set(GIFT_TYPES)
        or not all(
            type(value) is bool for value in arguments["accepted_gift_types"].values()
        )
    ):
        raise BusinessOperationError("invalid_input")
    if "media" in arguments:
        media = arguments["media"]
        if (
            not isinstance(media, dict)
            or media.get("kind") not in _MEDIA_METHODS
            or not isinstance(media.get("file_id"), str)
            or not media["file_id"]
            or "://" in media["file_id"]
        ):
            raise BusinessOperationError("need_media")
        if (
            operation == "set_photo"
            and media["kind"] != "photo"
            or operation in {"post_story", "edit_story"}
            and media["kind"] not in {"photo", "video"}
        ):
            raise BusinessOperationError("need_media")
        if (
            media["kind"] == "video"
            and operation in {"post_story", "edit_story"}
            and not 0 < media.get("duration", 0) <= 60
        ):
            raise BusinessOperationError("invalid_input")


_MEDIA_METHODS = {
    "photo": "sendPhoto",
    "video": "sendVideo",
    "document": "sendDocument",
    "animation": "sendAnimation",
    "audio": "sendAudio",
    "voice": "sendVoice",
    "sticker": "sendSticker",
}


class BusinessOperationError(Exception):
    def __init__(self, code, detail=""):
        self.code, self.detail = code, detail
        super().__init__(code)


def _request(method, parameters, uploads):
    if method not in {item[1] for item in OPERATIONS.values()} | set(
        _MEDIA_METHODS.values()
    ):
        raise BusinessOperationError("invalid_input")
    if uploads:
        boundary = "waku" + secrets.token_hex(16)
        pieces = []
        for name, value in parameters.items():
            encoded = (
                json.dumps(value, ensure_ascii=False)
                if not isinstance(value, str)
                else value
            )
            pieces.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{encoded}\r\n'.encode()
            )
        for name, (data, filename, mime) in uploads.items():
            pieces.extend(
                [
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),
                    data,
                    b"\r\n",
                ]
            )
        pieces.append(f"--{boundary}--\r\n".encode())
        body, content_type = (
            b"".join(pieces),
            "multipart/form-data; boundary=" + boundary,
        )
    else:
        body, content_type = (
            json.dumps(parameters, ensure_ascii=False).encode(),
            "application/json",
        )
    request = Request(
        "https://api.telegram.org/bot" + app_config.token + "/" + method,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urlopen(request, timeout=25) as response:
            data = response.read(2 * 1024 * 1024 + 1)
    except HTTPError as error:
        data = error.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise BusinessOperationError("uncertain")
    payload = json.loads(data)
    if not payload.get("ok"):
        detail = str(payload.get("description") or "").replace(
            app_config.token, "[redacted]"
        )[:240]
        raise BusinessOperationError("api_rejected", detail)
    return payload.get("result")


async def call_api(method, parameters, uploads=None):
    # No retry of mutations: a timeout does not establish whether it executed.
    return await asyncio.to_thread(_request, method, parameters, uploads or {})


async def guard(client, actor_id, connection_id, operation, arguments):
    validate_arguments(operation, arguments)
    connection = await context.ensure_connection(client, connection_id)
    if connection.user.id != actor_id:
        raise BusinessOperationError("owner_only")
    if not connection.is_enabled:
        raise BusinessOperationError("connection_disabled")
    if not context.settings_for(actor_id)["manage"]:
        raise BusinessOperationError("management_disabled")
    rights = context.rights_snapshot(connection.rights)
    if not rights[OPERATIONS[operation][0]]:
        raise BusinessOperationError("missing_right", OPERATIONS[operation][0])
    if (
        arguments.get("star_count", 0) > 0
        and operation in {"transfer_gift", "upgrade_gift"}
        and not rights["can_transfer_stars"]
    ):
        raise BusinessOperationError("missing_right", "can_transfer_stars")
    peer_id = arguments.get("chat_id")
    if peer_id is not None:
        state = await repository.get_state(actor_id, peer_id)
        events = state.get("events", [])
        if not events:
            raise BusinessOperationError("unobserved_chat")
        if operation in {"send_message", "send_media", "edit_message", "mark_read"}:
            incoming = [row["timestamp"] for row in events if row["role"] == "contact"]
            import time

            if not incoming or max(incoming) < time.time() - 86400:
                raise BusinessOperationError("reply_window")
        ids = arguments.get(
            "message_ids",
            [arguments["message_id"]] if "message_id" in arguments else [],
        )
        known = {row["id"]: row for row in events}
        if any(message_id not in known for message_id in ids):
            raise BusinessOperationError("unobserved_message")
        if operation == "delete_sent" and any(
            known[message_id]["role"] != "assistant" for message_id in ids
        ):
            raise BusinessOperationError("not_bot_message")
        if (
            operation == "edit_message"
            and known[arguments["message_id"]]["role"] != "assistant"
        ):
            raise BusinessOperationError("not_bot_message")
    return connection


def _number(value, *, minimum=1, maximum=2**63 - 1):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise BusinessOperationError("invalid_input") from None
    if not minimum <= result <= maximum:
        raise BusinessOperationError("invalid_input")
    return result


def parse_input(operation, text, media=None):
    lines = text.strip().splitlines()
    try:
        if operation in {
            "send_message",
            "edit_message",
            "mark_read",
            "delete_sent",
            "delete_all",
        }:
            args = {"chat_id": _number(lines[0])}
            if operation == "send_message":
                args["text"] = "\n".join(lines[1:])
            elif operation in {"edit_message", "mark_read"}:
                args["message_id"] = _number(lines[1], maximum=2**31 - 1)
                if operation == "edit_message":
                    args["text"] = "\n".join(lines[2:])
            else:
                args["message_ids"] = [
                    _number(value, maximum=2**31 - 1)
                    for value in re.split(r"[\s,]+", " ".join(lines[1:]))
                    if value
                ]
                if not 1 <= len(args["message_ids"]) <= 100:
                    raise BusinessOperationError("invalid_input")
            if "text" in args and not 1 <= len(args["text"]) <= 4096:
                raise BusinessOperationError("invalid_input")
            return args
        if operation == "set_name":
            if not 1 <= len(lines[0]) <= 64:
                raise BusinessOperationError("invalid_input")
            last = lines[1] if len(lines) > 1 and lines[1] != "-" else ""
            if len(last) > 64:
                raise BusinessOperationError("invalid_input")
            return {"first_name": lines[0], "last_name": last}
        if operation == "set_bio":
            if len(text) > 140:
                raise BusinessOperationError("invalid_input")
            return {"bio": "" if text.strip() == "-" else text}
        if operation == "set_username":
            name = text.strip().lstrip("@")
            name = "" if name == "-" else name
            if name and not re.fullmatch(r"[A-Za-z0-9_]{1,32}", name):
                raise BusinessOperationError("invalid_input")
            return {"username": name}
        if operation in {"convert_gift", "upgrade_gift", "transfer_gift"}:
            if not lines[0] or len(lines[0]) > 256:
                raise BusinessOperationError("invalid_input")
            args = {"owned_gift_id": lines[0]}
            if operation == "upgrade_gift":
                args["star_count"] = _number(lines[1], minimum=0)
                args["keep_original_details"] = True
            elif operation == "transfer_gift":
                args["new_owner_chat_id"] = _number(lines[1])
                args["star_count"] = _number(lines[2], minimum=0)
            return args
        if operation == "transfer_stars":
            return {"star_count": _number(text, maximum=10000)}
        if operation == "delete_story":
            return {"story_id": _number(text, maximum=2**31 - 1)}
        if operation == "remove_photo":
            return {"is_public": False}
        if operation in {"set_photo", "send_media", "post_story", "edit_story"}:
            if (
                media is None
                or operation == "set_photo"
                and media["kind"] != "photo"
                or operation in {"post_story", "edit_story"}
                and media["kind"] not in {"photo", "video"}
            ):
                raise BusinessOperationError("need_media")
            args = {"media": media}
            if operation == "set_photo":
                args["is_public"] = False
            elif operation == "send_media":
                args["chat_id"] = _number(lines[0])
                args["caption"] = "\n".join(lines[1:])[:1024]
            else:
                if media["kind"] == "video" and not 0 < media.get("duration", 0) <= 60:
                    raise BusinessOperationError("invalid_input")
                args["caption"] = (
                    text if operation == "post_story" else "\n".join(lines[1:])
                )
                if len(args["caption"]) > 2048:
                    raise BusinessOperationError("invalid_input")
                if operation == "post_story":
                    args["active_period"] = 86400
                else:
                    args["story_id"] = _number(lines[0], maximum=2**31 - 1)
            return args
    except (IndexError, KeyError):
        raise BusinessOperationError("invalid_input") from None
    raise BusinessOperationError("invalid_input")


def media_descriptor(message):
    for kind in _MEDIA_METHODS:
        media = getattr(message, kind, None)
        if media is None:
            continue
        size = getattr(media, "file_size", None)
        if (
            not media.file_id
            or size is None
            or size > (10 if kind == "photo" else 50) * 1024 * 1024
        ):
            raise BusinessOperationError("media_limit")
        return {
            "kind": kind,
            "file_id": media.file_id,
            "size": size,
            "duration": getattr(media, "duration", 0),
            "mime": getattr(media, "mime_type", None)
            or ("image/jpeg" if kind == "photo" else "application/octet-stream"),
        }
    return None


def input_media(operation, message):
    media = media_descriptor(message)
    if media and operation in {"set_photo", "post_story", "edit_story"}:
        if media["kind"] == "document" and media["mime"] in {"image/jpeg", "image/png"}:
            if media["size"] > 10 * 1024 * 1024:
                raise BusinessOperationError("media_limit")
            media = {**media, "kind": "photo"}
    return media


async def execute(client, actor_id, connection_id, operation, arguments):
    await guard(client, actor_id, connection_id, operation, arguments)
    parameters = {
        **{key: value for key, value in arguments.items() if key != "media"},
        "business_connection_id": connection_id,
    }
    uploads = {}
    method = OPERATIONS[operation][1]
    media = arguments.get("media")
    if media:
        stream = await asyncio.wait_for(
            client.download_media(media["file_id"], in_memory=True), timeout=25
        )
        data = stream.getvalue() if stream and hasattr(stream, "getvalue") else None
        if (
            not isinstance(data, bytes)
            or not data
            or len(data) > (10 if media["kind"] == "photo" else 50) * 1024 * 1024
        ):
            raise BusinessOperationError("media_limit")
        mime = media.get("mime", "application/octet-stream")
        if not re.fullmatch(r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+", mime):
            mime = "application/octet-stream"
        uploads["media"] = (data, "business_upload", mime)
        if operation == "set_photo":
            parameters["photo"] = {"type": "static", "photo": "attach://media"}
        elif operation in {"post_story", "edit_story"}:
            parameters["content"] = {
                "type": media["kind"],
                media["kind"]: "attach://media",
            }
            if media["kind"] == "video":
                parameters["content"]["duration"] = media["duration"]
        else:
            method = _MEDIA_METHODS[media["kind"]]
            parameters[media["kind"]] = "attach://media"
            if media["kind"] == "sticker":
                parameters.pop("caption", None)
    if operation in {"delete_all", "delete_sent"}:
        parameters.pop("chat_id", None)
    if operation == "get_gifts":
        parameters["limit"] = 10
    await guard(client, actor_id, connection_id, operation, arguments)
    if arguments.get("chat_id") and operation not in READ_ONLY | {"mark_read"}:
        from waku.plugins.business_chat import halt_business_dialog

        await halt_business_dialog(connection_id, arguments["chat_id"])
        await guard(client, actor_id, connection_id, operation, arguments)
    result = await call_api(method, parameters, uploads)
    if (
        arguments.get("chat_id")
        and isinstance(result, dict)
        and result.get("message_id")
        and operation in {"send_message", "send_media"}
    ):
        import time

        await repository.record_message(
            actor_id,
            arguments["chat_id"],
            {
                "id": result["message_id"],
                "role": "assistant",
                "timestamp": time.time(),
                "text": arguments.get("text", arguments.get("caption", "")),
            },
            remember=context.settings_for(actor_id)["remember"],
        )
    if result is True and operation in {"delete_all", "delete_sent"}:
        await repository.delete_messages(
            actor_id, arguments["chat_id"], arguments["message_ids"]
        )
    if result is True and operation == "gift_settings":
        await repository.patch_state(actor_id, 0, {"gift_settings": arguments})
    if operation in {"set_name", "set_username"} and result is True:
        connection = await context.ensure_connection(client, connection_id)
        for key, value in arguments.items():
            setattr(connection.user, key, value)
        await repository.patch_state(
            actor_id, 0, {"profile": context.profile(connection.user)}
        )
    return result


async def execute_reviewed_action(client, actor_id, token):
    action = await repository.claim_action(token, actor_id)
    if not action:
        raise BusinessOperationError("expired")
    try:
        result = await execute(
            client,
            actor_id,
            action["connection_id"],
            action["operation"],
            action["arguments"],
        )
    except BusinessOperationError as error:
        await repository.finish_action(
            token, actor_id, "uncertain" if error.code == "uncertain" else "failed"
        )
        raise
    except (Exception, asyncio.CancelledError):
        await repository.finish_action(token, actor_id, "uncertain")
        raise BusinessOperationError("uncertain") from None
    await repository.finish_action(token, actor_id, "done")
    return result
