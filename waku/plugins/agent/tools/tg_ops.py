"""A single tool that calls Telegram methods on the current chat.

The API mirrors the Telegram Bot API: `method` is a Bot API method name and
`params` uses the Bot API field names. Only a whitelist of sending/expressive
methods is exposed, plus bot-specific extensions. chat_id is always the
current chat and cannot be overridden.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pyrogram
import pyrogram.errors
from pydantic_ai import RunContext

from waku.common.safe_http import DEFAULT_MAX_BYTES, UnsafeUrlError, safe_download_bytes
from waku.logger import logger
from waku.plugins.agent.localization import tr
from waku.plugins.agent.output import record_tool_reply, text_already_sent

from .. import datatype
from . import block, io, send_ops, workspace

# method -> (pyrogram client method, allowed params, required params)
_METHODS: dict[str, tuple[str, set[str], set[str]]] = {
    "sendMessage": (
        "send_message",
        {"text", "parse_mode", "disable_web_page_preview", "reply_to_message_id"},
        {"text"},
    ),
    "sendPhoto": (
        "send_photo",
        {"photo", "caption", "has_spoiler", "reply_to_message_id"},
        {"photo"},
    ),
    "sendDocument": (
        "send_document",
        {"document", "content", "file_name", "caption", "reply_to_message_id"},
        set(),
    ),
    "sendReaction": (
        "send_reaction",
        {"message_id", "emoji"},
        {"emoji"},
    ),
    "sendPoll": (
        "send_poll",
        {
            "question",
            "options",
            "is_anonymous",
            "allows_multiple_answers",
            "reply_to_message_id",
        },
        {"question", "options"},
    ),
    "sendDice": ("send_dice", {"emoji"}, set()),
    "sendAudio": (
        "send_audio",
        {"audio", "caption", "reply_to_message_id"},
        {"audio"},
    ),
    "sendVideo": (
        "send_video",
        {"video", "caption", "reply_to_message_id"},
        {"video"},
    ),
    "sendVoice": (
        "send_voice",
        {"voice", "caption", "reply_to_message_id"},
        {"voice"},
    ),
    "sendAnimation": (
        "send_animation",
        {"animation", "caption", "reply_to_message_id"},
        {"animation"},
    ),
}

_MEDIA_FIELDS = {
    "photo",
    "document",
    "audio",
    "video",
    "voice",
    "animation",
}


def _session_key(ctx: RunContext[datatype.ContextDeps]) -> str:
    """Workspace session key: chat id for groups, user id for private chats."""
    deps = ctx.deps
    return str(deps.chat_id) if deps.chat_id != deps.user_id else str(deps.user_id)


_WAKU_EXTENSIONS = {
    "scheduleMessage",
    "blockUser",
}


def _method_list() -> str:
    standard = ", ".join(sorted(_METHODS))
    ext = ", ".join(sorted(_WAKU_EXTENSIONS))
    return f"{standard}, {ext}"


async def _call_waku_extension(
    ctx: RunContext[datatype.ContextDeps], method: str, params: dict[str, Any]
) -> str:
    if method == "scheduleMessage":
        text = params.get("text")
        schedule_time = params.get("schedule_time")
        if not text or not schedule_time:
            return tr("schedule_fields_required")
        return await send_ops.schedule_message(ctx, str(schedule_time), text=str(text))
    if method == "blockUser":
        try:
            duration = int(params.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            return tr("block_duration_integer")
        if duration <= 0:
            return tr("block_duration_range")
        return await block.block_user(
            ctx,
            duration,
            ctx.deps.user_id,
            str(params.get("reason") or ""),
        )
    return tr("method_unknown", p0=method)


def _as_message_id(value: Any) -> int | None:
    """value as a Telegram message id, or None when it cannot be one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


async def _convert_params(
    ctx: RunContext[datatype.ContextDeps],
    method: str,
    params: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Map Bot API params to pyrogram kwargs. Returns (kwargs, error)."""
    _, allowed, required = _METHODS[method]
    unknown = set(params) - allowed - {"chat_id"}
    if unknown:
        return None, (
            tr(
                "fields_unknown",
                p0=method,
                p1=", ".join(sorted(unknown)),
                p2=", ".join(sorted(allowed)),
            )
        )
    if "chat_id" in params:
        return None, tr("chat_id_fixed")
    if method == "sendDocument" and "content" in params and "document" in params:
        return None, tr("document_content_conflict")
    if (
        method == "sendDocument"
        and "content" not in params
        and "document" not in params
    ):
        return None, tr("document_content_required")
    missing = required - set(params)
    if missing:
        return (
            None,
            tr("fields_required", p0=method, p1=", ".join(sorted(missing))),
        )
    kwargs: dict[str, Any] = {}
    for key, value in params.items():
        if key == "parse_mode":
            if value is None:
                kwargs[key] = None
            elif isinstance(value, str):
                normalized = value.strip().lower()
                mapping = {
                    "html": pyrogram.enums.ParseMode.HTML,
                    "markdown": pyrogram.enums.ParseMode.MARKDOWN,
                    "markdownv2": pyrogram.enums.ParseMode.MARKDOWN,
                    "markdown2": pyrogram.enums.ParseMode.MARKDOWN,
                    "md": pyrogram.enums.ParseMode.MARKDOWN,
                    "default": pyrogram.enums.ParseMode.DEFAULT,
                    "disabled": pyrogram.enums.ParseMode.DISABLED,
                    "none": None,
                }
                if normalized in mapping:
                    kwargs[key] = mapping[normalized]
                else:
                    return None, (tr("parse_mode_invalid", p0=value))
            elif isinstance(value, pyrogram.enums.ParseMode):
                kwargs[key] = value
            else:
                return None, (tr("parse_mode_invalid", p0=value))
            continue
        if key == "reply_to_message_id":
            kwargs["reply_parameters"] = pyrogram.types.ReplyParameters(
                message_id=int(value)
            )
        elif method == "sendReaction" and key == "message_id":
            if value is None:
                # JSON null means "not given": fall through to the default below.
                continue
            target = _as_message_id(value)
            if target is None:
                return None, (tr("reaction_message_invalid", p0=repr(value)))
            kwargs[key] = target
        elif key == "disable_web_page_preview":
            kwargs["link_preview_options"] = pyrogram.types.LinkPreviewOptions(
                is_disabled=bool(value)
            )
        elif key == "options":
            kwargs["options"] = list(value)
        elif method == "sendDocument" and key == "content":
            kwargs["document"] = _named_media(
                method,
                str(params.get("file_name") or "document.txt"),
                str(value).encode("utf-8"),
            )
        elif (
            key in _MEDIA_FIELDS
            and isinstance(value, str)
            and value.startswith(("waku://", "kmua://"))
        ):
            # waku:// references read a file from the bot's own codebase
            # (read-only, same access as the read tool).
            try:
                raw = await io.read_bytes(value, ctx)
            except Exception as e:
                return None, tr("error", p0=e)
            kwargs[key] = _named_media(method, value, raw)
        elif (
            key in _MEDIA_FIELDS
            and isinstance(value, str)
            and value.startswith("work://")
        ):
            # work:// references read a file from this session's workspace.
            rest = "/" + value[len("work://") :].lstrip("/")
            try:
                raw = await workspace.read_file_bytes(_session_key(ctx), rest)
            except Exception as e:
                return None, tr("error", p0=e)
            kwargs[key] = _named_media(method, value, raw)
        elif (
            key in _MEDIA_FIELDS
            and isinstance(value, str)
            and value.startswith("sandbox://")
        ):
            # sandbox:// references read a file from this session's shell
            # sandbox (same symlink-guarded access as the io tools).
            try:
                raw = await io.read_bytes(value, ctx)
            except Exception as e:
                return None, tr("error", p0=e)
            kwargs[key] = _named_media(method, value, raw)
        elif (
            key in _MEDIA_FIELDS
            and isinstance(value, str)
            and value.startswith(("http://", "https://"))
        ):
            # Media URLs are downloaded through the SSRF-guarded client so
            # pyrogram never fetches an arbitrary address itself. The in-memory
            # file must carry a .name so pyrogram can infer type and file name
            # (its docs require file-like objects to set ".name").
            try:
                raw = await safe_download_bytes(value, max_bytes=DEFAULT_MAX_BYTES)
            except UnsafeUrlError as e:
                return None, tr("error", p0=e)
            except Exception as e:
                return (
                    None,
                    tr("download_failed", p0=key, p1=e.__class__.__name__),
                )
            kwargs[key] = _named_media(method, value, raw)
        elif key in _MEDIA_FIELDS:
            return None, (tr("media_target_invalid", p0=key))
        else:
            kwargs[key] = value
    if method == "sendReaction" and "message_id" not in kwargs:
        # No target given: react to the message being answered. That is the
        # only id this tool can vouch for, so the common case never depends on
        # an id the model had to produce.
        kwargs["message_id"] = ctx.deps.message.id
    return kwargs, None


# Fallback extensions per method when the URL carries no recognizable one.
_DEFAULT_MEDIA_EXT: dict[str, str] = {
    "sendPhoto": ".jpg",
    "sendDocument": ".bin",
    "sendAudio": ".mp3",
    "sendVideo": ".mp4",
    "sendVoice": ".ogg",
    "sendAnimation": ".gif",
}


def _named_media(method: str, url: str, raw: bytes) -> BytesIO:
    """Wrap bytes in a BytesIO carrying a filename for pyrogram.

    url is an http(s) URL, a work:// reference or a plain file name.
    """
    from urllib.parse import unquote, urlparse

    if url.startswith("work://"):
        path_part = url[len("work://") :].lstrip("/")
    elif url.startswith(("http://", "https://")):
        path_part = unquote(urlparse(url).path)
    else:
        path_part = url
    name = path_part.rsplit("/", 1)[-1]
    if not name or name in (".", ".."):
        name = "file"
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not suffix:
        # No extension: fall back to the method's default so pyrogram can
        # infer the media type. Any explicit extension is kept as-is.
        name += _DEFAULT_MEDIA_EXT.get(method, ".bin")
    media = BytesIO(raw)
    media.name = name
    return media


async def tg(
    ctx: RunContext[datatype.ContextDeps],
    method: str,
    params: dict[str, Any] = {},
) -> str:
    """Call a Telegram method on the current chat, using Telegram Bot API naming.

    chat_id is always the current chat and is set for you.

    Standard methods (params follow Bot API field names):
    - sendPhoto: photo (http(s) URL, work:// or waku:// reference), caption, has_spoiler, reply_to_message_id
    - sendDocument: document (http(s) URL or a work:// / waku:// reference) OR content (plain text made into the document), plus file_name, caption, reply_to_message_id
    - sendReaction: emoji; message_id is optional (omit it to react to the message being answered)
    - sendPoll: question, options (2-8 strings), is_anonymous, allows_multiple_answers, reply_to_message_id
    - sendDice: emoji (🎲 🎯 🎳 🎰 🎲 variants)
    - sendAudio / sendVideo / sendVoice / sendAnimation: the media field, caption, reply_to_message_id
    - sendMessage: text, parse_mode (HTML / MarkdownV2), disable_web_page_preview, reply_to_message_id

    Media fields accept a public http(s) URL, a work:// file reference from this chat's workspace, or a waku:// codebase file.

    Bot extensions:
    - scheduleMessage: text, schedule_time (ISO 8601, must be in the future).
    - blockUser: duration_minutes (1-10080), reason (optional).

    Note: your text output is automatically sent as a reply to the current user --
          you do not need `sendMessage` for normal replies. Use this tool only when you need some extra message sending beyond the default reply (e.g. reply to others, second message).
          After sending text with this tool, do not repeat that text in your final output.

    Example: tg("sendPoll", {"question": "Lunch?", "options": ["noodles", "rice"]})
    """
    if ctx.deps.message is None or ctx.deps.message.id is None:
        return tr("error_context_unavailable")

    if method in _WAKU_EXTENSIONS:
        return await _call_waku_extension(ctx, method, params)
    if method not in _METHODS:
        return tr("method_unknown_supported", p0=method, p1=_method_list())

    kwargs, error = await _convert_params(ctx, method, params)
    if error:
        return error
    assert kwargs is not None
    reply_parameters = kwargs.get("reply_parameters")
    is_current_reply = method == "sendMessage" and (
        reply_parameters is None or reply_parameters.message_id == ctx.deps.message.id
    )
    if is_current_reply and text_already_sent(
        ctx.deps, str(kwargs["text"]), markdown=False
    ):
        return tr("method_ok", p0=method)
    client_method = _METHODS[method][0]
    try:
        result = await getattr(ctx.deps.client, client_method)(
            chat_id=ctx.deps.chat_id, **kwargs
        )
    except pyrogram.errors.MessageIdInvalid:
        logger.error(f"tg {method} error: target message not in this chat")
        if method == "sendReaction":
            return tr("reaction_wrong_chat")
        return tr("method_invalid_message", p0=method)
    except Exception as e:
        logger.error(f"tg {method} error: {e.__class__.__name__}: {e}")
        return tr("method_failed", p0=method, p1=e.__class__.__name__)
    if is_current_reply:
        await record_tool_reply(ctx.deps, result, str(kwargs["text"]))
    message_id = getattr(result, "id", None) or getattr(result, "message_id", None)
    if message_id is not None:
        return tr("tool_p0_sent_message_id_p1", p0=method, p1=message_id)
    return tr("method_ok", p0=method)


__all__ = ["tg"]
