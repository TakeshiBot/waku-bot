"""Admin-only mirror delegation, with an AI acknowledgement before dispatch."""

from __future__ import annotations

import asyncio
import re

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pyrogram import enums

from waku.common.rich_message import message_plain_text
from waku.common.telegram_authority import is_bot_admin
from waku.common.utils import get_reply_target
from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent import datatype
from waku.services.mirror_commands import (
    MAX_BATCH,
    build_command,
    expand_sources,
    normalize_mode,
    normalize_options,
    render_options,
    validate_source,
)
from waku.services.mirror_policy import is_raw_mirror_command  # noqa: F401
from waku.services.transfer_intent import allows_transfer

__all__ = ["cancel_mirror", "mirror", "mirror_instructions", "prepare_mirror"]

_USERNAME = re.compile(r"[A-Za-z0-9_]{5,32}\Z")
_TOOL_KEY = "mirror"
_ALLOWED_CHATS = {
    enums.ChatType.PRIVATE,
    enums.ChatType.GROUP,
    enums.ChatType.SUPERGROUP,
    enums.ChatType.FORUM,
}


async def _authorized(deps: datatype.ContextDeps) -> bool:
    message = deps.message
    actor = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    return bool(
        chat
        and chat.type in _ALLOWED_CHATS
        and chat.id == deps.chat_id
        and actor
        and actor.id == deps.user_id
        and not getattr(actor, "is_bot", False)
        and not getattr(message, "sender_chat", None)
        and not any(
            getattr(message, name, None)
            for name in (
                "business_connection_id",
                "guest_query_id",
                "guest_bot_caller_user",
                "guest_bot_caller_chat",
            )
        )
        and await is_bot_admin(actor.id)
    )


def _target() -> tuple[int, str] | None:
    chat_id = app_config.mirror_group_id
    username = app_config.mirror_bot_username.strip().removeprefix("@")
    if chat_id >= 0 or not _USERNAME.fullmatch(username):
        return None
    return chat_id, username


async def prepare_mirror(
    ctx: RunContext[datatype.ContextDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    if _target() and await _authorized(ctx.deps):
        return tool_def
    return None


def mirror_instructions() -> str:
    return (
        "Mirror/download delegation: when the current user explicitly asks you to "
        "mirror, leech, clone or download links or Telegram attachments, use the mirror tool if it is available. "
        "Information lookup, identifying a character/image, finding an image or its source, "
        "and follow-ups such as 'find it'/'tìm đi' are research, NEVER a mirror/download task. "
        "For research use the available web search tool (Telegram: websearch; "
        "Discord: search_web) and read. Never substitute mirror/ytdl when unsure or when "
        "reverse image search is unavailable. An attachment in the conversation does not authorize transfer. "
        "Choose mode to match the user's intent: mirror/m, leech/l, qbmirror/qm, qbleech/ql, "
        "jdmirror/jm, jdleech/jl, nzbmirror/nm, nzbleech/nl, ytdl/y, ytdlleech/yl, clone/cl, uphoster/up. "
        "For multiple explicit links, submit all of them together in urls (up to 20), not url. "
        "Waku creates and tracks a separate task per source, applying mode/options to each; do not use native -i/-b. "
        "Use the options mapping for explicit processing choices: extract/unpack -> {'-e': true}, "
        "zip/compress -> {'-z': true}; passwords are string values for these flags. "
        "Other documented options include -n rename, -up upload destination, -ud leech destination, "
        "-sp split size, -doc/-med document/media, -d seed ratio:time, -j join, -seedr, -ad, "
        "-yt YouTube upload, -sv sample video, -ss screenshots, -ca/-cv conversion, -t thumbnail, "
        "-meta metadata, -ns rename rules, -tl thumbnails, -rcf rclone flags, -h headers, -au/-ap authentication, "
        "-ff configured FFmpeg operation/list, -opt yt-dlp options, and clone filters -ct/-ex/-mn/-xn/-mc/-xc, -sync/-fwd. "
        "For a requested yt-dlp quality use -opt with a Python dict string, e.g. "
        "{'format': 'bv*[height<=720]+ba/b[height<=720]'} for 720p. Otherwise the backend uses best quality automatically. "
        "Use only options appropriate to the chosen mode and explicitly requested or unambiguously implied. "
        "Never invent passwords, destinations, credentials or flags. Ask a brief clarification only when needed. "
        "Do not add interactive -s selection unless explicitly asked; bot-owned menus may require interaction in the Mirror group. "
        "The user's wording "
        "is natural language, not a fixed command. Never emulate mirror requests "
        "with tg.sendMessage, shell, or another sending tool. For a replied photo "
        "or file, omit url and source_message_id to use the actual replied/current "
        "attachment. To select a different observed attachment, pass its actual "
        "message ID in source_message_id. Never pass the strings 'null' or 'None'. "
        "The backend resolves the source directly on Telegram, using its public "
        "message link where possible or copying a private attachment to the "
        "Mirror group if needed. Never download, read, write or export attachment "
        "bytes to the workspace or shell for mirroring. The Mirror bot need not "
        "belong to the source chat. No "
        "image interpretation or transcription is needed to mirror an attachment. "
        "Never invent a download URL, file ID or source message ID. Pass a short, natural "
        "acknowledgement in the user's conversational language: you are about to "
        "send the request, not claiming a task already exists. The mirror tool "
        "delivers this acknowledgement before dispatch. After the tool reports "
        "dispatch or uncertain delivery, end the turn silently using EndTurn; "
        "task acceptance and the final result will arrive asynchronously and be "
        "reported to the original chat. Do not send duplicate acknowledgements, "
        "poll status, invent task IDs, or claim completion. On validation/access "
        "failure, explain only the returned factual error in your own words; do "
        "not invent unsupported-format errors or propose downloads/workspace "
        "workarounds. When the user asks to stop/cancel a mirror task, use "
        "cancel_mirror, not a raw Telegram command. Omit request_id when replying "
        "to a task update or cancelling an active tracked task from any group or DM. "
        "Bot admins may manage tracked tasks across chats, including tasks requested by other bot admins. "
        "An exact backend request ID or actual Mirror GID may select the task directly. If "
        "several tasks match, ask which named file they mean, unless they explicitly asked to cancel all their tasks; "
        "then cancel each returned request_id through cancel_mirror. A cancellation "
        "request is not confirmation: report only the tool's factual state and "
        "wait for the asynchronous terminal result before saying it is cancelled. "
        "Never expose internal IDs or raw cancellation commands in normal "
        "conversation. If the "
        "tool is unavailable, explain that mirror delegation is only available "
        "to configured bot admins in ordinary Telegram chats. Do not bypass it."
    )


def _validate_url(value: str, mode="mirror") -> str | None:
    return validate_source(value, mode)


async def _resolve_source(deps, source_message_id):
    from waku.services.mirror_bridge import is_mirror_media

    if source_message_id is None:
        reply = getattr(deps.message, "reply_to_message", None)
        source = reply if is_mirror_media(reply) else deps.message
        source_message_id = getattr(source, "id", None)
    if type(source_message_id) is not int or source_message_id <= 0:
        return None
    source = await asyncio.wait_for(
        deps.client.get_messages(deps.chat_id, source_message_id), timeout=10
    )
    if (
        not source
        or getattr(source, "empty", False)
        or getattr(source, "id", None) != source_message_id
        or getattr(getattr(source, "chat", None), "id", None) != deps.chat_id
        or not is_mirror_media(source)
    ):
        return None
    return source


async def mirror(
    ctx: RunContext[datatype.ContextDeps],
    url: str | None = "",
    *,
    acknowledgement: str,
    source_message_id: int | None = None,
    mode: str = "mirror",
    options: dict[str, str | bool] | None = None,
    urls: list[str] | None = None,
) -> dict:
    """Delegate explicit Mirrorz transfers, optionally batching multiple sources.

    Only bot owners/global admins may use this tool. It acknowledges in the
    original chat before dispatch; external task receipts and results follow
    asynchronously in that chat. Never use a generic Telegram send instead.

    Args:
        url: Exact source supplied by the user: HTTP(S), magnet, Telegram link,
            configured rclone remote or Drive ID. NZB/uphoster require direct
            HTTP(S)/FTP links or a staged Telegram attachment.
            Omit this argument for a
            replied/current Telegram attachment. No command, flags or extra text.
        acknowledgement: Your brief natural-language reply acknowledging that
            you are about to submit this request, in the conversation's language.
            Do not claim the Mirror bot has accepted or completed the task yet.
        source_message_id: Actual attachment message ID in this conversation.
            Omit URL when using this. Omitted means the replied/current attachment.
        mode: Transfer command: mirror/m, leech/l, qbmirror/qm, qbleech/ql,
            jdmirror/jm, jdleech/jl, nzbmirror/nm, nzbleech/nl, ytdl/y,
            ytdlleech/yl, clone/cl, uphoster/up. Defaults to mirror.
        options: Supported Mirrorz flags mapped to true/false or exact string
            values. E.g. {"-e": true, "-z": true, "-n": "archive.zip"}.
            No shell quoting. Apply only requested options. -i/-b are replaced
            by urls so each task is tracked. Do not invent destinations/passwords.
        urls: Up to 20 exact sources in one request, all using mode/options.
            Omit url/source_message_id when using this batch argument.
    """
    deps = ctx.deps
    if not await _authorized(deps):
        return {"status": "denied", "error": "Bot admin permission is required."}
    target = _target()
    if target is None:
        return {
            "status": "not_configured",
            "error": "Mirror group/bot is not configured.",
        }
    if _TOOL_KEY in deps.tools_called_this_turn:
        return {
            "status": "already_requested",
            "instruction": "This turn already submitted a mirror request. EndTurn without more text.",
        }
    reply = get_reply_target(deps.message)
    if not await allows_transfer(
        message_plain_text(deps.message), message_plain_text(reply) if reply else ""
    ):
        return {
            "status": "intent_not_confirmed",
            "error": "No explicit file-transfer request was verified. Nothing was sent or copied. "
            "For information/image identification use web search, not mirror. If transfer was intended, clarify the request.",
        }
    source = None
    attachment_requested = url is None or (
        isinstance(url, str) and url.strip().lower() in {"", "null", "none"}
    )
    try:
        mode = normalize_mode(mode)
        options = normalize_options(mode, options)
        render_options(mode, options)
    except ValueError as error:
        return {"status": "invalid_options", "error": str(error)}
    if urls is not None:
        if (
            not attachment_requested
            or source_message_id is not None
            or not isinstance(urls, list)
            or not 1 <= len(urls) <= MAX_BATCH
            or any(_validate_url(item, mode) is None for item in urls)
        ):
            return {
                "status": "invalid_sources",
                "error": "Provide 1–20 valid sources in urls without url or source_message_id.",
            }
        sources = list(urls)
        attachment_requested = False
    else:
        sources = None
    validated_url = (
        _validate_url(url, mode)
        if sources is None and not attachment_requested
        else None
    )
    if (
        sources is None
        and not attachment_requested
        and (validated_url is None or source_message_id is not None)
    ):
        return {
            "status": "invalid_url",
            "error": "Provide one valid transfer source, a urls batch, or omit both to use a Telegram attachment; do not mix source types.",
        }
    if attachment_requested:
        try:
            source = await _resolve_source(deps, source_message_id)
        except Exception as error:
            logger.warning("Mirror source lookup failed: {}", type(error).__name__)
            return {
                "status": "source_unavailable",
                "error": "The Telegram attachment could not be retrieved; no task was sent.",
            }
        if source is None:
            return {
                "status": "source_missing",
                "error": "No Telegram attachment was verified in this chat at the selected message. No task was sent.",
            }
        if getattr(source, "has_protected_content", False) or getattr(
            source.chat, "has_protected_content", False
        ):
            return {
                "status": "source_protected",
                "error": "This attachment has protected content and cannot be copied for mirror.",
            }
        if not str(target[0]).startswith("-100"):
            return {
                "status": "invalid_media_target",
                "error": "Telegram attachment mirroring requires a supergroup as the Mirror group.",
            }
    if not acknowledgement.strip() or len(acknowledgement) > 1500:
        return {
            "status": "invalid_acknowledgement",
            "error": "Provide a brief acknowledgement.",
        }
    mirror_group_id, mirror_username = target
    try:
        if source is None:
            sources = expand_sources(sources or [validated_url], mode)
        for item in sources or [validated_url or "https://t.me/c/1/1"]:
            build_command(mode, mirror_username, item, options, "0" * 32)
    except ValueError as error:
        return {"status": "invalid_options", "error": str(error)}
    try:
        target_bot = await deps.client.get_users(mirror_username)
        if (
            not getattr(target_bot, "is_bot", False)
            or not getattr(target_bot, "username", None)
            or target_bot.username.lower() != mirror_username.lower()
            or target_bot.id == getattr(getattr(deps.client, "me", None), "id", None)
        ):
            return {
                "status": "invalid_bot",
                "error": "The mirror target must be another Telegram bot.",
            }
    except Exception as error:
        logger.warning("Mirror bot lookup failed: {}", type(error).__name__)
        return {
            "status": "lookup_failed",
            "error": "The configured Mirror bot could not be resolved.",
        }

    publish = deps.publish_image_progress
    if not callable(publish):
        return {
            "status": "delivery_unavailable",
            "error": "The originating chat cannot be acknowledged.",
        }
    # The marker also prevents model retries from re-sending an acknowledgement
    # after dispatch fails or becomes uncertain.
    deps.tools_called_this_turn.add(_TOOL_KEY)
    try:
        delivered = await publish(acknowledgement.strip())
    except Exception as error:
        logger.warning("Mirror acknowledgement failed: {}", type(error).__name__)
        return {
            "status": "acknowledgement_failed",
            "error": "Acknowledgement delivery failed; no task was sent.",
        }
    if not delivered:
        return {
            "status": "acknowledgement_failed",
            "error": "Acknowledgement was not delivered; no task was sent.",
        }

    # Re-check operator authority after the potentially slow lookup/delivery.
    if not await _authorized(deps):
        return {
            "status": "denied",
            "error": "Bot admin permission was revoked; no task was sent.",
        }
    from waku.services import mirror_bridge

    deps.side_effects_started = True
    results = []
    items = sources or [validated_url]
    for index, item in enumerate(items):
        try:
            source_args = {"source_message": source} if source is not None else {}
            result = await mirror_bridge.submit(
                deps.client,
                origin_chat_id=deps.chat_id,
                origin_message_id=deps.message.id,
                requester_id=deps.user_id,
                mirror_chat_id=mirror_group_id,
                mirror_bot_id=target_bot.id,
                mirror_bot_username=mirror_username,
                url=item,
                mode=mode,
                options=options,
                item_index=index,
                batch_size=len(items),
                **source_args,
            )
            results.append(result)
        except Exception as error:
            logger.error(
                "Mirror dispatch could not be recorded: {}", type(error).__name__
            )
            return {
                "status": "dispatch_error",
                "tasks": results,
                "error": "The current transfer could not be confirmed. Earlier returned tasks remain tracked; remaining items were not sent. Do not retry automatically; the current item may already have been submitted.",
            }
    deps.mirror_dispatched = True
    return {
        **(
            results[0]
            if len(results) == 1
            else {"status": "batch_dispatched", "tasks": results}
        ),
        "instruction": "The initial acknowledgement was delivered. EndTurn without more text; task updates will be reported asynchronously.",
    }


async def cancel_mirror(
    ctx: RunContext[datatype.ContextDeps], request_id: str | None = None
) -> dict:
    """Cancel a tracked Mirror task as a bot admin, from any group or DM.

    Use for natural-language requests to cancel/stop a mirror. Omit the internal
    request ID to select by the actual replied task update, or the user's active
    task across chats. Bot admins may manage other bot admins' tracked tasks.
    If ambiguous, ask which file they mean; cancel each candidate only when
    explicitly asked to cancel all. A sent
    cancellation request is not a confirmed cancellation; report the returned
    facts in your own words and await the separate terminal result.

    Args:
        request_id: An actual tracked request ID or Mirror GID, if known.
            Omit to resolve from the real reply or active tasks across chats.
            Never invent an ID or ask the user to execute a raw command.
    """
    deps = ctx.deps
    if not await _authorized(deps):
        return {"status": "denied", "error": "Bot admin permission is required."}
    if request_id is not None and (
        not isinstance(request_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) is None
    ):
        return {
            "status": "invalid_request",
            "error": "The internal task reference is invalid; omit it to resolve the actual reply or active task.",
        }
    reply = getattr(deps.message, "reply_to_message", None)
    reply_id = getattr(reply, "id", None)
    if type(reply_id) is not int or reply_id <= 0:
        reply_id = None
    from waku.services import mirror_cancel

    try:
        facts = await mirror_cancel.cancel(
            deps.client,
            requester_id=deps.user_id,
            origin_chat_id=deps.chat_id,
            request_id=request_id,
            reply_message_id=reply_id,
            admin_scope=True,
        )
    except Exception as error:
        logger.error("Mirror cancellation failed: {}", type(error).__name__)
        return {
            "status": "cancel_error",
            "error": "The cancellation request could not be confirmed. Do not claim it was cancelled or repeat the action automatically.",
        }
    if facts.get("status") in {"cancel_requested", "cancel_uncertain"}:
        deps.side_effects_started = True
    return {
        **facts,
        "instruction": "Explain only these factual results in your own words. A sent/uncertain request is not a confirmed cancellation; await the asynchronous terminal result. Do not display internal IDs or raw commands.",
    }
