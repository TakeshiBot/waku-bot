"""Group administration with current Telegram rights and explicit user intent.

These tools never use bot-owner roles to bypass Telegram administrator rights.
All mutations are serialized per group; warnings and permission snapshots are durable.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import unicodedata
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import wraps
from uuid import uuid4

from pydantic_ai import RunContext
from pyrogram import Client, raw
from pyrogram.enums import ChatMemberStatus, ChatType, MessageEntityType
from pyrogram.errors import RPCError
from pyrogram.types import ChatAdministratorRights

from waku.common.rich_message import message_plain_text, rich_message_mention_ids
from waku.common.utils import get_reply_target
from waku.database import get_chat_by_id
from waku.database import group_moderation as store
from waku.i18n import normalize_locale
from waku.logger import logger
from waku.plugins.title.permissions import (
    ADMIN_RIGHTS,
    CHANNEL_ONLY,
    group_preset,
    supported,
)

from .. import datatype
from ..localization import current_locale
from . import moderation_permissions as permissions

MODERATION_RIGHTS = {
    **dict.fromkeys(
        (
            "ban_user",
            "kick_user",
            "mute_user",
            "unban_user",
            "unmute_user",
            "warn_user",
            "reset_user_warnings",
            "set_chat_permissions",
            "lock_chat",
            "unlock_chat",
        ),
        "can_restrict_members",
    ),
    "delete_replied_message": "can_delete_messages",
    "promote_user": "can_promote_members",
    "demote_user": "can_promote_members",
    "pin_chat_message": "can_pin_messages",
    "unpin_chat_message": "can_pin_messages",
    "set_slow_mode": "can_restrict_members",
    "set_chat_title": "can_change_info",
    "set_chat_description": "can_change_info",
    "set_member_tag": "can_manage_tags",
    "clear_member_tag": "can_manage_tags",
}
__all__ = [*MODERATION_RIGHTS, "MODERATION_RIGHTS", "prepare_group_moderation"]
_locks: dict[int, asyncio.Lock] = {}
_MAX_DURATION = 366 * 86400
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_GROUPS = (ChatType.GROUP, ChatType.SUPERGROUP)
_ADMINS = (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
_operation_effect: ContextVar[bool] = ContextVar("moderation_effect", default=False)
_operation_completed: ContextVar[bool] = ContextVar(
    "moderation_completed", default=False
)
_operation_dispatched: ContextVar[bool] = ContextVar(
    "moderation_dispatched", default=False
)
_operation_uncertain: ContextVar[bool] = ContextVar(
    "moderation_uncertain", default=False
)
_canonical_key: ContextVar[str | None] = ContextVar(
    "moderation_canonical", default=None
)
_active_action: ContextVar[str] = ContextVar("moderation_action", default="")

# Tool responses are deliberately short, bilingual, and independent of model prose.
_TEXT = {
    "disabled": (
        "AI quản trị nhóm đang tắt; quản trị viên cần bật và Lưu trong /config trước.",
        "AI group moderation is disabled; an administrator must enable and Save it in /config first.",
    ),
    "group": (
        "Chỉ hỗ trợ nhóm Telegram hiện tại.",
        "Only the current Telegram group is supported.",
    ),
    "anonymous": (
        "Không xác minh được người quản trị ẩn danh; hãy gửi bằng tài khoản cá nhân.",
        "Anonymous administrators cannot be verified; send from your personal account.",
    ),
    "actor": (
        "Bạn cần quyền quản trị Telegram phù hợp cho thao tác này.",
        "You need the corresponding Telegram administrator permission.",
    ),
    "bot": (
        "Bot thiếu quyền quản trị Telegram cần thiết.",
        "The bot lacks the required Telegram administrator permission.",
    ),
    "target": (
        "Hãy reply thành viên hoặc ghi chính xác @username/ID của họ trong yêu cầu.",
        "Reply to the member or explicitly include their exact @username/ID in your request.",
    ),
    "reference": (
        'Tham chiếu mục tiêu đã được backend xác minh: {target}. Người gửi đang reply đúng câu trả lời gần nhất của Waku cho chính họ. Đây là đối tượng duy nhất do chính người đó nêu hoặc backend xác định trong chuỗi reply này. Nếu yêu cầu mới đã rõ, gọi tool với target="{target}" ngay; không hỏi lại tên đối tượng hoặc xác nhận lần nữa. Chỉ kế thừa danh tính đối tượng, không kế thừa lệnh hay quyền; tool vẫn kiểm tra quyền Telegram hiện tại.',
        'Backend-verified target reference: {target}. This sender is replying to Waku\'s exact most recent answer to this same sender. This unique target was named by that sender or resolved by the backend in this reply chain. For a clear new action, call the tool with target="{target}" immediately without asking for the target or confirmation again. Only identity is referenced, not past actions or authority; tools recheck current Telegram rights.',
    ),
    "protected": (
        "Không thể ban/mute/kick chủ nhóm, quản trị viên hoặc chính người ra lệnh. Tài khoản bot khác không tự được bảo vệ chỉ vì là bot.",
        "The owner, administrators and requesting user are protected from ban/mute/kick. Other bot accounts are not protected merely for being bots.",
    ),
    "self": (
        "Mục tiêu được chọn là chính bot đang thực thi yêu cầu. Đây không phải lệnh cấm quản trị các bot khác; hãy chọn đúng @username/ID của đối tượng người dùng đã yêu cầu.",
        "The selected target is this executing bot itself, which is protected. This is not a ban on managing other bot accounts; select the exact requested username/ID.",
    ),
    "member": (
        "Không xác minh được thành viên phù hợp trong nhóm này.",
        "A suitable member of this group could not be verified.",
    ),
    "supergroup": (
        "Thao tác này cần supergroup Telegram.",
        "This operation requires a Telegram supergroup.",
    ),
    "duration": (
        "Thời hạn phải là s/m/h/d/w, từ 30 giây đến 366 ngày; để trống chỉ khi muốn vĩnh viễn.",
        "Duration must use s/m/h/d/w, from 30 seconds to 366 days; leave empty only for permanent actions.",
    ),
    "editable": (
        "Không xác minh được quyền của bạn và bot để thay đổi quản trị viên này.",
        "Your and the bot's authority to edit this administrator could not be verified.",
    ),
    "message": (
        "Hãy reply tin nhắn hoặc ghi ID tin nhắn trong yêu cầu hiện tại.",
        "Reply to a message or explicitly include its ID in the current request.",
    ),
    "text": (
        "Nội dung không hợp lệ hoặc vượt giới hạn Telegram.",
        "The text is invalid or exceeds Telegram's limit.",
    ),
    "slow": (
        "Slow mode chỉ hỗ trợ 0, 10, 30, 60, 300, 900 hoặc 3600 giây.",
        "Slow mode supports only 0, 10, 30, 60, 300, 900 or 3600 seconds.",
    ),
    "slow_bot": (
        "Telegram không cho tài khoản bot thay đổi slow mode gốc; quản trị viên cần chỉnh trong ứng dụng Telegram.",
        "Telegram does not allow bot accounts to change native slow mode; an administrator must change it in the Telegram app.",
    ),
    "uncertain": (
        "Đã gửi thao tác nhưng chưa xác minh được kết quả Telegram ({error}); không tự gửi lại. Hãy kiểm tra trạng thái nhóm.",
        "The action was dispatched but its Telegram outcome is unverified ({error}); it will not be resent automatically. Check the group's state.",
    ),
    "snapshot": (
        "Không có bản quyền gốc đã lưu phù hợp; không tự cấp lại quyền.",
        "No matching saved permissions exist; permissions will not be guessed.",
    ),
    "changed": (
        "Quyền đã thay đổi ngoài thao tác này; không ghi đè cấu hình hiện tại.",
        "Permissions changed outside this operation; the current settings will not be overwritten.",
    ),
    "done": ("Đã thực hiện {action}{target}.", "Completed {action}{target}."),
    "error": (
        "Thao tác chưa hoàn tất ({error}).",
        "The operation did not complete ({error}).",
    ),
    "partial": (
        "Một phần thao tác đã được lưu/thực hiện nhưng bước tiếp theo chưa hoàn tất ({error}); không tự lặp lại phần đã thành công.",
        "Part of the action was saved/applied but a subsequent step failed ({error}); successful steps will not be repeated.",
    ),
    "warning": ("Đã cảnh cáo {target}: {count}/3.", "Warned {target}: {count}/3."),
    "punished": (
        "Đã cảnh cáo {target} và mute 24 giờ; cảnh cáo đã được đặt lại.",
        "Warned {target} and muted for 24 hours; warnings were reset.",
    ),
    "warn_failed": (
        "Đã lưu cảnh cáo {target}: {count}/3, nhưng mute chưa hoàn tất ({error}); cảnh cáo được giữ lại.",
        "Saved warning for {target}: {count}/3, but muting failed ({error}); warnings remain recorded.",
    ),
    "absent": (
        "Thành viên hiện không ở trạng thái cần xử lý; không thay đổi.",
        "The member is not in the relevant state; no change was made.",
    ),
}


class ModerationDenied(ValueError):
    pass


class _MutationRejected(RuntimeError):
    """An explicit negative Telegram receipt, rather than a lost receipt."""

    pass


class _ExistingReceipt(Exception):
    def __init__(self, result: str):
        self.result = result


def _confirmed_rejection(error: Exception) -> bool:
    if isinstance(error, _MutationRejected):
        return True
    return isinstance(error, RPCError) and 0 < getattr(error, "CODE", 500) < 500


def _text(ctx, key: str, **values) -> str:
    locale = getattr(ctx.deps, "locale", None) or current_locale()
    index = 0 if normalize_locale(locale) == "vi" else 1
    return _TEXT[key][index].format(**values)


def _now() -> datetime:
    return datetime.now(UTC)


def parse_duration(duration: str, *, allow_permanent: bool = True) -> int:
    """Parse explicit Rose-style units without converting invalid input to forever."""
    if not isinstance(duration, str):
        raise ModerationDenied("duration")
    if not duration.strip():
        if allow_permanent:
            return 0
        raise ModerationDenied("duration")
    match = re.fullmatch(r"([1-9]\d{0,8})([smhdw])", duration.strip().lower())
    if match is None:
        raise ModerationDenied("duration")
    seconds = (
        int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match[2]]
    )
    if not 30 <= seconds <= _MAX_DURATION:
        raise ModerationDenied("duration")
    return seconds


def _until(seconds: int) -> datetime:
    if not seconds:
        return _EPOCH
    # Native Telegram treats <30 seconds or >366 days as permanent. Small boundary
    # margins protect valid boundary requests from transport latency/clock skew.
    safe_seconds = min(max(seconds, 35), _MAX_DURATION - 60)
    return _now() + timedelta(seconds=safe_seconds)


def _has_right(member, right: str) -> bool:
    return member.status == ChatMemberStatus.OWNER or (
        member.status == ChatMemberStatus.ADMINISTRATOR
        and (
            bool(getattr(member.privileges, right, False))
            or right == "can_manage_chat"
            and any(
                bool(getattr(member.privileges, name, False)) for name in ADMIN_RIGHTS
            )
        )
    )


async def _identity(ctx) -> int:
    me = getattr(ctx.deps.client, "me", None) or await ctx.deps.client.get_me()
    if me is None or not me.id:
        raise ModerationDenied("bot")
    return me.id


async def _basic_bot_privileges(client, chat_id: int, member):
    if member.status != ChatMemberStatus.ADMINISTRATOR or member.privileges is not None:
        return
    peer = await client.resolve_peer(chat_id)
    if not isinstance(peer, raw.types.InputPeerChat):
        raise ModerationDenied("bot")
    response = await client.invoke(raw.functions.messages.GetChats(id=[peer.chat_id]))
    chat = next((chat for chat in response.chats if chat.id == peer.chat_id), None)
    rights = getattr(chat, "admin_rights", None)
    if not isinstance(rights, raw.types.ChatAdminRights):
        raise ModerationDenied("bot")
    member.privileges = ChatAdministratorRights._parse(rights)


async def _current_members(ctx):
    client = ctx.deps.client
    actor = await client.get_chat_member(ctx.deps.chat_id, ctx.deps.user_id)
    bot_id = await _identity(ctx)
    bot = await client.get_chat_member(ctx.deps.chat_id, bot_id)
    if ctx.deps.message.chat.type == ChatType.GROUP:
        # Basic groups have one admin role rather than editable granular admin
        # flags. The bot's own actual flags are present in raw Chat.admin_rights.
        if actor.status == ChatMemberStatus.ADMINISTRATOR and actor.privileges is None:
            actor.privileges = ChatAdministratorRights(
                can_manage_chat=True,
                can_delete_messages=True,
                can_restrict_members=True,
                can_change_info=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_manage_tags=True,
            )
        await _basic_bot_privileges(client, ctx.deps.chat_id, bot)
    return actor, bot, bot_id


async def _authorize(ctx, right: str):
    deps = ctx.deps
    message = deps.message
    if (
        message is None
        or message.chat is None
        or message.chat.id != deps.chat_id
        or message.chat.type not in _GROUPS
        or getattr(message, "business_connection_id", None)
    ):
        raise ModerationDenied("group")
    if message.sender_chat is not None or message.from_user is None:
        raise ModerationDenied("anonymous")
    if message.from_user.id != deps.user_id or message.from_user.is_bot:
        raise ModerationDenied("actor")
    if not await _moderation_enabled(deps.chat_id):
        raise ModerationDenied("disabled")
    actor, bot, bot_id = await _current_members(ctx)
    if not _has_right(actor, right):
        raise ModerationDenied("actor")
    if not _has_right(bot, right):
        raise ModerationDenied("bot")
    return actor, bot, bot_id


async def _moderation_enabled(chat_id: int) -> bool:
    row = await get_chat_by_id(chat_id)
    return row is not None and row.chat_config.agent_moderation_enabled is True


async def prepare_group_moderation(ctx: RunContext[datatype.ContextDeps], tool):
    """Expose capabilities in groups; execution returns actual permission denials."""
    if tool.name not in MODERATION_RIGHTS:
        return None
    message = ctx.deps.message
    if (
        message is None
        or message.chat is None
        or message.chat.id != ctx.deps.chat_id
        or message.chat.type not in _GROUPS
        or getattr(message, "business_connection_id", None)
    ):
        return None
    return tool


def _request_text(ctx) -> str:
    return message_plain_text(ctx.deps.message, include_link_targets=False) or ""


def _explicit_token(ctx, token: str) -> bool:
    return _token_in_text(_request_text(ctx), token)


def _token_in_text(text: str, token: str) -> bool:
    return (
        re.search(r"(?<![\w@])" + re.escape(token) + r"(?!\w)", text, re.I) is not None
    )


def _named_targets(ctx, text: str) -> list[str]:
    """Exact mentions take precedence over a reply author; ignore a leading wake tag."""
    me = getattr(ctx.deps.client, "me", None)
    own_names = {
        (getattr(me, "username", None) or "").casefold(),
        *(
            name.username.casefold()
            for name in (getattr(me, "usernames", None) or [])
            if name.active is True
        ),
    }
    names = []
    for match in re.finditer(r"(?<![\w@/])@[A-Za-z][A-Za-z0-9_]{3,31}(?!\w)", text):
        name = match[0].casefold()
        if name[1:] in own_names and not text[: match.start()].strip():
            continue
        if name not in names:
            names.append(name)
    return names


def _explicit_target_hint(ctx) -> bool:
    text = _request_text(ctx)
    return bool(
        _named_targets(ctx, text) or _mention_ids(ctx) or _numeric_target_ids(text)
    )


def _numeric_target_ids(text: str) -> set[int]:
    """A duration's numeric value is not evidence for a Telegram user ID."""
    ids = set()
    for match in re.finditer(r"(?<![\w@])[1-9]\d{0,18}(?!\w)", text):
        following = text[match.end() :].lstrip()
        if re.match(
            r"(?:[smhdw]|giây|phút|giờ|ngày|tuần|seconds?|minutes?|hours?|days?|weeks?)\b",
            following,
            re.I,
        ):
            continue
        ids.add(int(match[0]))
    return ids


async def _reply_target_reference(ctx, reply, bot_id: int):
    """Use only our exact recent reply to this sender, never arbitrary bot prose/history."""
    if (
        reply is None
        or reply.from_user is None
        or reply.from_user.id != bot_id
        or reply.sender_chat is not None
        or getattr(reply, "forward_origin", None) is not None
    ):
        return None
    from waku.common.memory_store import memttlcache

    from ..state import bot_last_reply_key

    try:
        last = await memttlcache.get(bot_last_reply_key(ctx.deps.chat_id))
        if (
            isinstance(last, datatype.BotLastReply)
            and last.message_id == reply.id
            and last.reply_to_user_id == ctx.deps.user_id
            and 0 <= _now().timestamp() - last.timestamp <= 300
        ):
            reference = getattr(last, "moderation_reference", None)
            if reference is not None:
                if (
                    isinstance(reference, datatype.ModerationReference)
                    and 0 <= _now().timestamp() - reference.timestamp <= 300
                    and re.fullmatch(
                        r"(?:@[A-Za-z][A-Za-z0-9_]{3,31}|[1-9]\d{0,18})",
                        reference.target,
                    )
                ):
                    return reference
                return None
            # Compatibility for replies cached before references were added.
            names = _named_targets(ctx, last.original_user_message)
            if len(names) == 1:
                return datatype.ModerationReference(names[0], last.timestamp)
    except Exception as error:
        logger.debug(
            "Moderation reply target lookup unavailable: {}", type(error).__name__
        )
    return None


async def group_moderation_context(ctx) -> str:
    """Expose only a verified reference identity so AI can handle a new follow-up."""
    message = ctx.deps.message
    if (
        message is None
        or message.chat is None
        or message.chat.id != ctx.deps.chat_id
        or message.chat.type not in _GROUPS
        or message.from_user is None
        or message.from_user.id != ctx.deps.user_id
        or message.from_user.is_bot
        or message.sender_chat is not None
        or getattr(message, "business_connection_id", None)
    ):
        return ""
    if _explicit_target_hint(ctx):
        names = _named_targets(ctx, _request_text(ctx))
        ids = _mention_ids(ctx) | _numeric_target_ids(_request_text(ctx))
        if len(names) == 1 and not ids:
            ctx.deps.moderation_reference = datatype.ModerationReference(
                names[0], _now().timestamp()
            )
        elif len(ids) == 1 and not names:
            ctx.deps.moderation_reference = datatype.ModerationReference(
                str(next(iter(ids))), _now().timestamp()
            )
        return ""
    reply = get_reply_target(message)
    if reply is None or reply.chat is None or reply.chat.id != ctx.deps.chat_id:
        return ""
    if not await _moderation_enabled(ctx.deps.chat_id):
        return ""
    reference = await _reply_target_reference(ctx, reply, await _identity(ctx))
    if reference is None:
        return ""
    ctx.deps.moderation_reference = reference
    return _text(ctx, "reference", target=reference.target)


def _mention_ids(ctx) -> set[int]:
    message = ctx.deps.message
    return rich_message_mention_ids(message) | {
        entity.user.id
        for entity in (message.entities or message.caption_entities or [])
        if entity.type == MessageEntityType.TEXT_MENTION and entity.user is not None
    }


async def _target(
    ctx, user_id: int | None, target: str, *, allow_admin=False, allow_absent=False
):
    reply = get_reply_target(ctx.deps.message)
    if reply is not None and (reply.chat is None or reply.chat.id != ctx.deps.chat_id):
        raise ModerationDenied("target")
    reply_user = (
        reply.from_user if reply is not None and reply.sender_chat is None else None
    )
    bot_id = await _identity(ctx)
    source_text = _request_text(ctx)
    names = _named_targets(ctx, source_text)
    requested_ids = _mention_ids(ctx) | _numeric_target_ids(source_text)
    reference = None
    if not names and not _explicit_target_hint(ctx):
        reference = await _reply_target_reference(ctx, reply, bot_id)
        if reference is not None:
            source_text = reference.target
            names = _named_targets(ctx, source_text)
    target = target.strip()
    if not target and not (user_id is not None and user_id in requested_ids):
        # A model may accidentally pass the ID of our replied message's author.
        # The sender's unique named target is stronger evidence than that ID.
        if len(names) == 1:
            target, user_id = names[0], None
        elif len(names) > 1 and user_id is None:
            raise ModerationDenied("target")
        elif len(_mention_ids(ctx)) == 1:
            user_id = next(iter(_mention_ids(ctx)))
        elif len(requested_ids) == 1 and user_id is None:
            user_id = next(iter(requested_ids))
        elif len(requested_ids) > 1 and user_id is None:
            raise ModerationDenied("target")
        elif reference is not None and reference.target.isdecimal():
            user_id = int(reference.target)
    supplied_id = user_id if target else None
    if target.strip():
        value = target.strip()
        if re.fullmatch(r"[1-9]\d{0,18}", value):
            user_id = int(value)
        elif re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", value):
            if not (
                _explicit_token(ctx, value)
                or len(names) == 1
                and names[0] == value.casefold()
                and _token_in_text(source_text, value)
            ):
                raise ModerationDenied("target")
            user = await ctx.deps.client.get_users(value)
            if user is None or not any(
                name.lower() == value[1:].lower()
                for name in [
                    getattr(user, "username", "") or "",
                    *[
                        username.username
                        for username in (getattr(user, "usernames", None) or [])
                        if username.active is True
                    ],
                ]
            ):
                raise ModerationDenied("target")
            user_id = user.id
        else:
            raise ModerationDenied("target")
    if (
        supplied_id is not None
        and supplied_id != user_id
        and supplied_id in requested_ids
    ):
        # Distinct IDs explicitly named by the sender are ambiguous. An ID
        # invented by the model (e.g. the reply bot's ID) cannot override the
        # exact username we just resolved from the sender's request.
        raise ModerationDenied("target")
    if user_id is None:
        if reply_user is None:
            ids = _mention_ids(ctx)
            if len(ids) != 1:
                raise ModerationDenied("target")
            user_id = ids.pop()
        else:
            user_id = reply_user.id
    elif (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or not 0 < user_id < 2**63
    ):
        raise ModerationDenied("target")
    elif not (
        (reply_user is not None and reply_user.id == user_id)
        or user_id in requested_ids
        or reference is not None
        and reference.target == str(user_id)
        or (
            target.startswith("@")
            and (
                _explicit_token(ctx, target)
                or len(names) == 1
                and names[0] == target.casefold()
            )
        )
    ):
        raise ModerationDenied("target")
    if user_id == bot_id:
        raise ModerationDenied("self")
    if user_id == ctx.deps.user_id:
        raise ModerationDenied("protected")
    ctx.deps.moderation_reference = datatype.ModerationReference(
        str(user_id), _now().timestamp()
    )
    key = f"member:{_active_action.get()}:{user_id}"
    _canonical_key.set(key)
    receipt = ctx.deps.moderation_results.get(key)
    if receipt is not None:
        raise _ExistingReceipt(receipt)
    member = await ctx.deps.client.get_chat_member(ctx.deps.chat_id, user_id)
    if member.user is None or member.user.id != user_id:
        raise ModerationDenied("member")
    if member.status == ChatMemberStatus.OWNER or (
        member.status == ChatMemberStatus.ADMINISTRATOR and not allow_admin
    ):
        raise ModerationDenied("protected")
    if not allow_absent and (
        member.status in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT)
        or (member.status == ChatMemberStatus.RESTRICTED and member.is_member is False)
    ):
        raise ModerationDenied("member")
    return user_id, member


def _mark(ctx, *, completed=False) -> None:
    ctx.deps.side_effects_started = True
    _operation_effect.set(True)
    if completed:
        _operation_completed.set(True)


async def _verify_target(ctx, user_id: int, *, allow_absent=False, allow_admin=False):
    if user_id == await _identity(ctx):
        raise ModerationDenied("self")
    if user_id == ctx.deps.user_id:
        raise ModerationDenied("protected")
    member = await ctx.deps.client.get_chat_member(ctx.deps.chat_id, user_id)
    if member.user is None or member.user.id != user_id:
        raise ModerationDenied("member")
    if member.status == ChatMemberStatus.OWNER or (
        member.status == ChatMemberStatus.ADMINISTRATOR and not allow_admin
    ):
        raise ModerationDenied("protected")
    if not allow_absent and (
        member.status in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT)
        or (member.status == ChatMemberStatus.RESTRICTED and member.is_member is False)
    ):
        raise ModerationDenied("member")
    return member


class _MutationClient:
    """Per-call SDK facade; never replaces the shared client's transport."""

    def __init__(self, client, before_send, ctx):
        self._client = client
        self._before_send = before_send
        self._ctx = ctx

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def invoke(self, query, **kwargs):
        if isinstance(query, raw.functions.channels.GetParticipant):
            # Native promotion/title methods read metadata before their mutation.
            return await self._client.invoke(query, **kwargs)
        await self._before_send(query)
        _operation_dispatched.set(True)
        _operation_uncertain.set(True)
        _mark(self._ctx)
        # Kurigram counts attempts, not additional retries: zero sends no RPC.
        kwargs.update(retries=1, sleep_threshold=0)
        try:
            result = await self._client.invoke(query, **kwargs)
        except Exception as error:
            if _confirmed_rejection(error):
                _operation_uncertain.set(False)
            elif await _verify_mutation_outcome(self._client, query):
                _operation_uncertain.set(False)
                _mark(self._ctx, completed=True)
                return True
            raise
        if result is None:
            if await _verify_mutation_outcome(self._client, query):
                _operation_uncertain.set(False)
                _mark(self._ctx, completed=True)
                return True
            raise RuntimeError("TelegramReceiptMissing")
        _operation_uncertain.set(False)
        if result is False:
            raise _MutationRejected("TelegramRejected")
        _mark(self._ctx, completed=True)
        return result


async def _verify_mutation_outcome(client, query) -> bool:
    """Read back an uncertain member mutation; never dispatch it a second time."""
    try:
        if isinstance(query, raw.functions.channels.EditAdmin):
            response = await client.invoke(
                raw.functions.channels.GetParticipant(
                    channel=query.channel, participant=query.user_id
                ),
                retries=1,
                timeout=8,
                sleep_threshold=0,
            )
            member = response.participant
            desired = query.admin_rights
            fields = inspect.signature(raw.types.ChatAdminRights).parameters
            if not any(bool(getattr(desired, field, False)) for field in fields):
                return (
                    isinstance(
                        member,
                        (
                            raw.types.ChannelParticipant,
                            raw.types.ChannelParticipantSelf,
                        ),
                    )
                    and member.user_id == query.user_id.user_id
                )
            return (
                isinstance(member, raw.types.ChannelParticipantAdmin)
                and member.user_id == query.user_id.user_id
                and all(
                    bool(getattr(member.admin_rights, field, False))
                    == bool(getattr(desired, field, False))
                    for field in fields
                )
                and (query.rank is None or (member.rank or "") == query.rank)
            )
        if isinstance(query, raw.functions.channels.EditBanned):
            response = await client.invoke(
                raw.functions.channels.GetParticipant(
                    channel=query.channel, participant=query.participant
                ),
                retries=1,
                timeout=8,
                sleep_threshold=0,
            )
            member = response.participant
            if isinstance(member, raw.types.ChannelParticipantBanned):
                return (
                    isinstance(member.peer, raw.types.PeerUser)
                    and member.peer.user_id == query.participant.user_id
                    and permissions.snapshot(member.banned_rights)
                    == permissions.snapshot(query.banned_rights)
                )
            return (
                isinstance(
                    member,
                    (raw.types.ChannelParticipant, raw.types.ChannelParticipantSelf),
                )
                and member.user_id == query.participant.user_id
                and permissions.snapshot(query.banned_rights)
                == permissions.snapshot(None)
            )
    except Exception as error:
        logger.debug("Moderation result lookup failed: {}", type(error).__name__)
    return False


async def _mutate(
    ctx,
    right: str,
    method,
    *args,
    _target_id=None,
    _allow_absent=False,
    _allow_admin=False,
    _demote=False,
    _promotion_rights=None,
    _duration_seconds=None,
    _update_mute=False,
    _admin_title=False,
    _expected_rights=None,
    _expected_member_rights=None,
    **kwargs,
):
    async def before_send(query):
        if _target_id is not None:
            member = await _verify_target(
                ctx,
                _target_id,
                allow_absent=_allow_absent,
                allow_admin=_demote or _admin_title or _allow_admin,
            )
            if (
                _demote
                or _allow_admin
                and member.status == ChatMemberStatus.ADMINISTRATOR
            ):
                await _editable(ctx, member)
            if _admin_title and (
                member.status != ChatMemberStatus.ADMINISTRATOR
                or member.can_be_edited is not True
            ):
                raise ModerationDenied("editable")
        actor, bot, _ = await _authorize(ctx, right)
        if _expected_rights is not None:
            current = await permissions.default_rights(
                ctx.deps.client, ctx.deps.chat_id
            )
            if current != _expected_rights:
                raise ModerationDenied("changed")
        if _expected_member_rights is not None:
            current = await permissions.member_rights(
                ctx.deps.client, ctx.deps.chat_id, _target_id
            )
            if current != _expected_member_rights:
                raise ModerationDenied("changed")
        if _admin_title and query is not None:
            # EditAdmin also writes permissions. Do not restore the stale rights
            # the native title helper read before checking the actor's authority.
            response = await ctx.deps.client.invoke(
                raw.functions.channels.GetParticipant(
                    channel=query.channel, participant=query.user_id
                )
            )
            fresh = response.participant
            if (
                not isinstance(fresh, raw.types.ChannelParticipantAdmin)
                or fresh.can_edit is not True
            ):
                raise ModerationDenied("editable")
            fields = inspect.signature(raw.types.ChatAdminRights).parameters
            if any(
                bool(getattr(fresh.admin_rights, name, False))
                != bool(getattr(query.admin_rights, name, False))
                for name in fields
            ):
                raise ModerationDenied("changed")
        if _promotion_rights is not None and query is not None:
            for name in _promotion_rights:
                if not _has_right(actor, name) or not _has_right(bot, name):
                    raise ModerationDenied(
                        "actor" if not _has_right(actor, name) else "bot"
                    )
            query.admin_rights = raw.types.ChatAdminRights(
                **{
                    flag: name in _promotion_rights
                    for name, flag in ADMIN_RIGHTS.items()
                    if supported(name)
                },
            )
        if _duration_seconds is not None and query is not None:
            until = _until(_duration_seconds)
            query.banned_rights.until_date = int(until.timestamp())
            if _update_mute:
                await store.patch_state(
                    ctx.deps.chat_id,
                    _target_id,
                    {
                        "mute_applied": permissions.snapshot(query.banned_rights),
                        "mute_until": until.isoformat(),
                    },
                )
        if isinstance(query, raw.functions.channels.EditBanned):
            expiry = query.banned_rights.until_date
            if expiry and not 30 <= expiry - int(_now().timestamp()) <= _MAX_DURATION:
                raise ModerationDenied("duration")

    client = ctx.deps.client
    facade = _MutationClient(client, before_send, ctx)
    name = getattr(method, "__name__", None)
    if method == client.invoke:
        result = await facade.invoke(*args, **kwargs)
    elif getattr(method, "__self__", None) is client and callable(
        getattr(Client, name or "", None)
    ):
        result = await getattr(Client, name)(facade, *args, **kwargs)
    else:
        # Test/custom transport boundaries still receive the same last-minute checks.
        await before_send(None)
        _operation_dispatched.set(True)
        _operation_uncertain.set(True)
        _mark(ctx)
        try:
            result = await method(*args, **kwargs)
        except RPCError as error:
            if _confirmed_rejection(error):
                _operation_uncertain.set(False)
            raise
        _operation_uncertain.set(False)
    if result is False:
        raise RuntimeError("TelegramRejected")
    _mark(ctx, completed=True)
    return result


async def _set_rights(
    ctx,
    value: dict,
    *,
    user_id: int | None = None,
    duration_seconds: int | None = None,
    expected_current: dict | None = None,
    expected_member: dict | None = None,
):
    client = ctx.deps.client
    peer = await client.resolve_peer(ctx.deps.chat_id)
    native = permissions.to_native(value)
    if user_id is None:
        query = raw.functions.messages.EditChatDefaultBannedRights(
            peer=peer, banned_rights=native
        )
    else:
        participant = await client.resolve_peer(user_id)
        query = raw.functions.channels.EditBanned(
            channel=peer, participant=participant, banned_rights=native
        )
    # Recheck after peer resolution, immediately before sending the mutation.
    return await _mutate(
        ctx,
        "can_restrict_members",
        client.invoke,
        query,
        _target_id=user_id,
        _duration_seconds=duration_seconds,
        _update_mute=duration_seconds is not None,
        _expected_rights=expected_current,
        _expected_member_rights=expected_member,
    )


def _guarded(function):
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(ctx, *args, **kwargs):
        bound = signature.bind(ctx, *args, **kwargs)
        bound.apply_defaults()
        fingerprint = (
            function.__name__
            + ":"
            + json.dumps(
                {key: value for key, value in bound.arguments.items() if key != "ctx"},
                sort_keys=True,
                ensure_ascii=True,
            )
        )
        async with _locks.setdefault(ctx.deps.chat_id, asyncio.Lock()):
            results = getattr(ctx.deps, "moderation_results", None)
            if results is None:
                ctx.deps.moderation_results = results = {}
            if fingerprint in results:
                return results[fingerprint]
            token = _operation_effect.set(False)
            completed_token = _operation_completed.set(False)
            dispatch_token = _operation_dispatched.set(False)
            uncertain_token = _operation_uncertain.set(False)
            canonical_token = _canonical_key.set(None)
            action_token = _active_action.set(function.__name__)
            try:
                await _authorize(ctx, MODERATION_RIGHTS[function.__name__])
                result = await function(ctx, *args, **kwargs)
            except _ExistingReceipt as receipt:
                result = receipt.result
            except ModerationDenied as error:
                result = _text(ctx, str(error))
                if _operation_completed.get():
                    result = _text(ctx, "partial", error=result)
            except Exception as error:
                # Credential-bearing RPC/provider exception text is never surfaced.
                logger.warning(
                    "Group moderation {} failed: {}",
                    function.__name__,
                    type(error).__name__,
                )
                result = _text(
                    ctx,
                    "uncertain"
                    if _operation_uncertain.get()
                    else ("partial" if _operation_completed.get() else "error"),
                    error=type(error).__name__,
                )
            finally:
                occurred = _operation_effect.get()
                canonical = _canonical_key.get()
                _operation_effect.reset(token)
                _operation_completed.reset(completed_token)
                _operation_dispatched.reset(dispatch_token)
                _operation_uncertain.reset(uncertain_token)
                _canonical_key.reset(canonical_token)
                _active_action.reset(action_token)
            if occurred:
                results[fingerprint] = result
                if canonical is not None:
                    results[canonical] = result
            return result

    return wrapped


def _receipt(ctx, action: str, user_id: int) -> str | None:
    """Canonical identity prevents repeating a member action with alternate syntax."""
    key = f"member:{action}:{user_id}"
    _canonical_key.set(key)
    return ctx.deps.moderation_results.get(key)


def _done(ctx, action: str, user_id: int | None = None) -> str:
    return _text(ctx, "done", action=action, target=f" ({user_id})" if user_id else "")


def _supergroup(ctx):
    if ctx.deps.message.chat.type != ChatType.SUPERGROUP:
        raise ModerationDenied("supergroup")


async def _mute(ctx, user_id: int, member, seconds: int):
    state = await store.get_state(ctx.deps.chat_id, user_id)
    current = await permissions.member_rights(
        ctx.deps.client, ctx.deps.chat_id, user_id
    )
    saved = state.get("mute_permissions")
    if saved is not None:
        permissions.to_native(saved)
        permissions.to_native(state.get("mute_applied"))
    old_until = int(state.get("mute_applied", {}).get("until_date", 0))
    if (
        saved is not None
        and old_until
        and old_until <= int(_now().timestamp())
        and member.status != ChatMemberStatus.RESTRICTED
    ):
        saved = None
    if saved is not None:
        applied = state.get("mute_applied")
        if current != applied:
            saved = current
    else:
        saved = current
    # An explicit timed mute replaces the restriction expiry, just like /tmute.
    # Telegram restores its group defaults at expiry; old personal restrictions
    # are not a reason to reject a current administrator's requested mute.
    until = _until(seconds)
    muted = permissions.muted(current, until_date=int(until.timestamp()))
    updates = {
        "mute_permissions": saved,
        "mute_applied": muted,
        "mute_until": until.isoformat(),
        "mute_generation": uuid4().hex,
    }
    # Save before the network call: a crash after Telegram ACK cannot lose restore data.
    await store.patch_state(ctx.deps.chat_id, user_id, updates)
    _mark(ctx)
    try:
        await _set_rights(
            ctx,
            muted,
            user_id=user_id,
            duration_seconds=seconds,
            expected_member=current,
        )
    except Exception as error:
        if (
            isinstance(error, ModerationDenied)
            or _confirmed_rejection(error)
            or not _operation_dispatched.get()
        ):
            await store.patch_state(
                ctx.deps.chat_id, user_id, {key: state.get(key) for key in updates}
            )
        raise


@_guarded
async def ban_user(
    ctx: RunContext[datatype.ContextDeps],
    user_id: int | None = None,
    target: str = "",
    duration: str = "",
    reason: str = "",
) -> str:
    """Ban a replied/explicit member. Empty duration is permanent; e.g. 30m is temporary."""
    seconds = parse_duration(duration)
    _supergroup(ctx)
    user_id, member = await _target(ctx, user_id, target, allow_absent=True)
    if (
        member.status == ChatMemberStatus.BANNED
        and not seconds
        and not member.until_date
    ):
        return _text(ctx, "absent")
    await _mutate(
        ctx,
        "can_restrict_members",
        ctx.deps.client.ban_chat_member,
        ctx.deps.chat_id,
        user_id,
        until_date=_until(seconds),
        _target_id=user_id,
        _allow_absent=True,
        _duration_seconds=seconds,
    )
    return _done(ctx, "ban", user_id)


@_guarded
async def kick_user(
    ctx: RunContext[datatype.ContextDeps],
    user_id: int | None = None,
    target: str = "",
    reason: str = "",
) -> str:
    """Remove a member and let them rejoin; never kick an administrator."""
    user_id, _ = await _target(ctx, user_id, target)
    await _mutate(
        ctx,
        "can_restrict_members",
        ctx.deps.client.ban_chat_member,
        ctx.deps.chat_id,
        user_id,
        _target_id=user_id,
    )
    if ctx.deps.message.chat.type == ChatType.GROUP:
        return _done(ctx, "kick", user_id)
    # Recheck rights again for the second mutation. Failure reports partial success.
    await _mutate(
        ctx,
        "can_restrict_members",
        ctx.deps.client.unban_chat_member,
        ctx.deps.chat_id,
        user_id,
        _target_id=user_id,
        _allow_absent=True,
    )
    return _done(ctx, "kick", user_id)


@_guarded
async def unban_user(
    ctx: RunContext[datatype.ContextDeps],
    user_id: int | None = None,
    target: str = "",
    reason: str = "",
) -> str:
    """Allow a banned member to rejoin, without changing an active member's permissions."""
    _supergroup(ctx)
    user_id, member = await _target(ctx, user_id, target, allow_absent=True)
    if member.status != ChatMemberStatus.BANNED:
        return _text(ctx, "absent")
    await _mutate(
        ctx,
        "can_restrict_members",
        ctx.deps.client.unban_chat_member,
        ctx.deps.chat_id,
        user_id,
        _target_id=user_id,
        _allow_absent=True,
    )
    return _done(ctx, "unban", user_id)


@_guarded
async def mute_user(
    ctx: RunContext[datatype.ContextDeps],
    user_id: int | None = None,
    target: str = "",
    duration: str = "",
    reason: str = "",
) -> str:
    """Mute sending without banning. Save exact old permissions; empty duration is permanent."""
    _supergroup(ctx)
    seconds = parse_duration(duration)
    user_id, member = await _target(ctx, user_id, target)
    await _mute(ctx, user_id, member, seconds)
    return _done(ctx, "mute", user_id)


@_guarded
async def unmute_user(
    ctx: RunContext[datatype.ContextDeps],
    user_id: int | None = None,
    target: str = "",
    reason: str = "",
) -> str:
    """Lift a member's mute, even when another admin/bot imposed it."""
    _supergroup(ctx)
    user_id, member = await _target(ctx, user_id, target)
    state = await store.get_state(ctx.deps.chat_id, user_id)
    if member.status != ChatMemberStatus.RESTRICTED:
        if state.get("mute_permissions"):
            await store.patch_state(
                ctx.deps.chat_id,
                user_id,
                {
                    key: None
                    for key in (
                        "mute_permissions",
                        "mute_applied",
                        "mute_until",
                        "mute_generation",
                    )
                },
            )
            _mark(ctx)
        return _text(ctx, "absent")
    current = await permissions.member_rights(
        ctx.deps.client, ctx.deps.chat_id, user_id
    )
    defaults = await permissions.default_rights(ctx.deps.client, ctx.deps.chat_id)
    # Prefer the pre-mute snapshot only while it still describes our mute.
    # Otherwise /unmute means lifting the current personal restrictions.
    saved = (
        dict(state["mute_permissions"])
        if state.get("mute_permissions") is not None
        and current == state.get("mute_applied")
        else permissions.snapshot(None)
    )
    permissions.to_native(saved)
    until = saved["until_date"]
    if until and until <= int(_now().timestamp()):
        saved = permissions.snapshot(None)
    restored = permissions.clamp_to_defaults(saved, defaults)
    await _set_rights(ctx, restored, user_id=user_id, expected_member=current)
    await store.patch_state(
        ctx.deps.chat_id,
        user_id,
        {
            key: None
            for key in (
                "mute_permissions",
                "mute_applied",
                "mute_until",
                "mute_generation",
            )
        },
    )
    return _done(ctx, "unmute", user_id)


async def _message_id(ctx, message_id: int | None):
    reply = get_reply_target(ctx.deps.message)
    if reply is not None and (reply.chat is None or reply.chat.id != ctx.deps.chat_id):
        raise ModerationDenied("message")
    if message_id is None:
        if reply is None:
            raise ModerationDenied("message")
        message_id = reply.id
    if (
        isinstance(message_id, bool)
        or not isinstance(message_id, int)
        or message_id <= 0
    ):
        raise ModerationDenied("message")
    if not (reply is not None and reply.id == message_id) and not _explicit_token(
        ctx, str(message_id)
    ):
        raise ModerationDenied("message")
    message = await ctx.deps.client.get_messages(ctx.deps.chat_id, message_id)
    if (
        message is None
        or message.empty
        or message.chat is None
        or message.chat.id != ctx.deps.chat_id
    ):
        raise ModerationDenied("message")
    return message_id


@_guarded
async def delete_replied_message(
    ctx: RunContext[datatype.ContextDeps], reason: str = ""
) -> str:
    """Delete only the current replied message in this group."""
    message_id = await _message_id(ctx, None)
    deleted = await _mutate(
        ctx,
        "can_delete_messages",
        ctx.deps.client.delete_messages,
        ctx.deps.chat_id,
        message_id,
    )
    if not deleted:
        raise RuntimeError("MessageNotDeleted")
    return _done(ctx, "delete", message_id)


@_guarded
async def pin_chat_message(
    ctx: RunContext[datatype.ContextDeps],
    message_id: int | None = None,
    disable_notification: bool = False,
) -> str:
    """Pin a replied/explicit existing message in this group."""
    message_id = await _message_id(ctx, message_id)
    await _mutate(
        ctx,
        "can_pin_messages",
        ctx.deps.client.pin_chat_message,
        ctx.deps.chat_id,
        message_id,
        disable_notification=disable_notification,
    )
    return _done(ctx, "pin", message_id)


@_guarded
async def unpin_chat_message(
    ctx: RunContext[datatype.ContextDeps], message_id: int | None = None
) -> str:
    """Unpin a replied/explicit message; never implicitly unpin every message."""
    message_id = await _message_id(ctx, message_id)
    await _mutate(
        ctx,
        "can_pin_messages",
        ctx.deps.client.unpin_chat_message,
        ctx.deps.chat_id,
        message_id,
    )
    return _done(ctx, "unpin", message_id)


def _validate_label(value: str, *, empty=False):
    if (
        not isinstance(value, str)
        or (not empty and not value.strip())
        or len(value) > 16
    ):
        raise ModerationDenied("text")
    if any(
        unicodedata.category(char) in ("So", "Cs", "Cc") or char in "\ufe0f\u200d\u20e3"
        for char in value
    ):
        raise ModerationDenied("text")


async def _editable(ctx, member):
    actor, _, _ = await _authorize(ctx, "can_promote_members")
    if (
        member.status != ChatMemberStatus.ADMINISTRATOR
        or member.can_be_edited is not True
    ):
        raise ModerationDenied("editable")
    if actor.status == ChatMemberStatus.OWNER:
        return
    # Both Telegram's bot editability and the requesting administrator's chain
    # must be verified. Missing promoter metadata is never an authorization grant.
    seen = set()
    for _ in range(32):
        promoter = member.promoted_by
        if promoter is None or promoter.id in seen:
            break
        if promoter.id == ctx.deps.user_id:
            return
        seen.add(promoter.id)
        member = await ctx.deps.client.get_chat_member(ctx.deps.chat_id, promoter.id)
        if member.status != ChatMemberStatus.ADMINISTRATOR:
            break
    raise ModerationDenied("editable")


@_guarded
async def promote_user(
    ctx: RunContext[datatype.ContextDeps],
    user_id: int | None = None,
    target: str = "",
    title: str = "",
    reason: str = "",
    permissions: list[str] | None = None,
    use_configured_permissions: bool = False,
) -> str:
    """Promote with explicit permissions or the caller/bot intersection by default, like Rose.

    Use the /sett preset only when the requester explicitly asks for configured permissions.
    """
    _supergroup(ctx)
    if title:
        _validate_label(title)
    user_id, member = await _target(ctx, user_id, target, allow_admin=True)
    if member.status == ChatMemberStatus.ADMINISTRATOR:
        await _editable(ctx, member)
    if type(use_configured_permissions) is not bool or (
        use_configured_permissions and permissions is not None
    ):
        raise ModerationDenied("text")
    selected = permissions
    if selected is None and not use_configured_permissions:
        actor, bot, _ = await _authorize(ctx, "can_promote_members")
        selected = [
            name
            for name in ADMIN_RIGHTS
            if name not in CHANNEL_ONLY
            and name not in {"can_promote_members", "is_anonymous"}
            and supported(name)
            and _has_right(actor, name)
            and _has_right(bot, name)
        ]
    if use_configured_permissions:
        row = await get_chat_by_id(ctx.deps.chat_id)
        try:
            preset = (
                group_preset(row.chat_config.title_permissions or {}) if row else {}
            )
        except ValueError:
            raise ModerationDenied("text") from None
        selected = [name for name, value in preset.items() if value]
    requested = ["can_manage_chat", *(selected or [])]
    if any(
        not isinstance(name, str) or name in CHANNEL_ONLY or not supported(name)
        for name in requested
    ):
        raise ModerationDenied("text")
    await _mutate(
        ctx,
        "can_promote_members",
        ctx.deps.client.promote_chat_member,
        ctx.deps.chat_id,
        user_id,
        _target_id=user_id,
        _promotion_rights=requested,
        _allow_admin=True,
    )
    if title:
        promoted = await ctx.deps.client.get_chat_member(ctx.deps.chat_id, user_id)
        if promoted.can_be_edited is not True:
            raise ModerationDenied("editable")
        await _mutate(
            ctx,
            "can_promote_members",
            ctx.deps.client.set_administrator_title,
            ctx.deps.chat_id,
            user_id,
            title,
            _target_id=user_id,
            _admin_title=True,
        )
    return _done(ctx, "promote", user_id)


@_guarded
async def demote_user(
    ctx: RunContext[datatype.ContextDeps],
    user_id: int | None = None,
    target: str = "",
    reason: str = "",
) -> str:
    """Demote only an editable admin within the requesting administrator's promotion chain."""
    _supergroup(ctx)
    user_id, member = await _target(ctx, user_id, target, allow_admin=True)
    await _editable(ctx, member)
    privileges = ChatAdministratorRights(
        **{
            name: False
            for name in inspect.signature(ChatAdministratorRights).parameters
        }
    )
    await _mutate(
        ctx,
        "can_promote_members",
        ctx.deps.client.promote_chat_member,
        ctx.deps.chat_id,
        user_id,
        privileges=privileges,
        _target_id=user_id,
        _demote=True,
    )
    return _done(ctx, "demote", user_id)


@_guarded
async def set_member_tag(
    ctx: RunContext[datatype.ContextDeps],
    tag: str,
    user_id: int | None = None,
    target: str = "",
) -> str:
    """Set a 1–16 character, non-emoji tag for a regular group member."""
    _validate_label(tag)
    user_id, _ = await _target(ctx, user_id, target)
    await _mutate(
        ctx,
        "can_manage_tags",
        ctx.deps.client.set_chat_member_tag,
        ctx.deps.chat_id,
        user_id,
        tag,
        _target_id=user_id,
    )
    return _done(ctx, "tag", user_id)


@_guarded
async def clear_member_tag(
    ctx: RunContext[datatype.ContextDeps], user_id: int | None = None, target: str = ""
) -> str:
    """Remove a regular member's tag without changing administrator titles."""
    user_id, _ = await _target(ctx, user_id, target)
    await _mutate(
        ctx,
        "can_manage_tags",
        ctx.deps.client.set_chat_member_tag,
        ctx.deps.chat_id,
        user_id,
        None,
        _target_id=user_id,
    )
    return _done(ctx, "clear tag", user_id)


@_guarded
async def warn_user(
    ctx: RunContext[datatype.ContextDeps],
    user_id: int | None = None,
    target: str = "",
    reason: str = "",
) -> str:
    """Persist a warning (30-day expiry); at 3 warnings mute for 24h then reset on success."""
    _supergroup(ctx)
    if len(reason) > 500:
        raise ModerationDenied("text")
    user_id, member = await _target(ctx, user_id, target)
    receipt = _receipt(ctx, "warn", user_id)
    if receipt is not None:
        return receipt
    await _authorize(ctx, "can_restrict_members")
    state = await store.increment_warning(
        ctx.deps.chat_id, user_id, reason, (_now() + timedelta(days=30)).isoformat()
    )
    _mark(ctx, completed=True)
    count = state["warning_count"]
    if count < 3:
        return _text(ctx, "warning", target=user_id, count=count)
    try:
        await _mute(ctx, user_id, member, 86400)
        reset = await store.reset_warnings(
            ctx.deps.chat_id, user_id, expected_generation=state["warning_generation"]
        )
        if not reset:
            return _text(ctx, "partial", error="WarningStateChanged")
    except Exception as error:
        return _text(
            ctx, "warn_failed", target=user_id, count=count, error=type(error).__name__
        )
    return _text(ctx, "punished", target=user_id)


@_guarded
async def reset_user_warnings(
    ctx: RunContext[datatype.ContextDeps], user_id: int | None = None, target: str = ""
) -> str:
    """Clear this member's durable warning count and reasons."""
    user_id, _ = await _target(ctx, user_id, target, allow_absent=True)
    await _authorize(ctx, "can_restrict_members")
    await store.reset_warnings(ctx.deps.chat_id, user_id)
    _mark(ctx, completed=True)
    return _done(ctx, "reset warnings", user_id)


@_guarded
async def set_slow_mode(ctx: RunContext[datatype.ContextDeps], seconds: int = 0) -> str:
    """Set Telegram's supported slow-mode interval; 0 disables it."""
    _supergroup(ctx)
    if isinstance(seconds, bool) or seconds not in (0, 10, 30, 60, 300, 900, 3600):
        raise ModerationDenied("slow")
    me = getattr(ctx.deps.client, "me", None) or await ctx.deps.client.get_me()
    if me.is_bot:
        return _text(ctx, "slow_bot")
    await _mutate(
        ctx,
        "can_restrict_members",
        ctx.deps.client.set_slow_mode,
        ctx.deps.chat_id,
        seconds,
    )
    return _done(ctx, "slow mode")


@_guarded
async def set_chat_permissions(
    ctx: RunContext[datatype.ContextDeps],
    send_messages: bool | None = None,
    send_media: bool | None = None,
    send_stickers: bool | None = None,
    send_gifs: bool | None = None,
    send_games: bool | None = None,
    send_inline: bool | None = None,
    embed_links: bool | None = None,
) -> str:
    """Change only specified group send permissions; unspecified rights stay unchanged."""
    updates = {
        "send_messages": send_messages,
        "send_stickers": send_stickers,
        "send_gifs": send_gifs,
        "send_games": send_games,
        "send_inline": send_inline,
        "embed_links": embed_links,
    }
    current = await permissions.default_rights(ctx.deps.client, ctx.deps.chat_id)
    expected_current = dict(current)
    if send_media is not None:
        updates.update(
            {
                name: send_media
                for name in (
                    "send_media",
                    "send_photos",
                    "send_videos",
                    "send_roundvideos",
                    "send_audios",
                    "send_voices",
                    "send_docs",
                )
            }
        )
    if not any(value is not None for value in updates.values()):
        raise ModerationDenied("text")
    for name, value in updates.items():
        if value is not None:
            if not isinstance(value, bool):
                raise ModerationDenied("text")
            current[name] = not value
    if send_messages is not None:
        current["send_plain"] = not send_messages
    await _set_rights(ctx, current, expected_current=expected_current)
    # Explicit custom permissions replace any saved lock; old timers cannot undo them.
    await store.patch_state(
        ctx.deps.chat_id,
        0,
        {
            key: None
            for key in (
                "lock_permissions",
                "lock_applied",
                "lock_generation",
                "lock_until",
                "lock_actor_id",
            )
        },
    )
    return _done(ctx, "permissions")


@_guarded
async def set_chat_title(ctx: RunContext[datatype.ContextDeps], title: str) -> str:
    """Set the current group's title (1–128 characters), preserving the supplied text."""
    if not isinstance(title, str) or not title.strip() or len(title) > 128:
        raise ModerationDenied("text")
    await _mutate(
        ctx, "can_change_info", ctx.deps.client.set_chat_title, ctx.deps.chat_id, title
    )
    return _done(ctx, "title")


@_guarded
async def set_chat_description(
    ctx: RunContext[datatype.ContextDeps], description: str
) -> str:
    """Set the current group's description (0–255 characters); empty clears it."""
    if not isinstance(description, str) or len(description) > 255:
        raise ModerationDenied("text")
    await _mutate(
        ctx,
        "can_change_info",
        ctx.deps.client.set_chat_description,
        ctx.deps.chat_id,
        description,
    )
    return _done(ctx, "description")


_LOCK_KEYS = (
    "lock_permissions",
    "lock_applied",
    "lock_generation",
    "lock_until",
    "lock_actor_id",
)


def _schedule_unlock(chat_id: int, generation: str, when: datetime, attempt: int = 0):
    from waku.common.jobs import jobqueue

    job = jobqueue.add_onetime_job(
        f"group-unlock:{chat_id}:{generation}",
        unlock_expired_chat,
        when,
        args=[chat_id, generation, attempt] if attempt else [chat_id, generation],
    )
    if getattr(job, "_jobstore_alias", None) == "memory" or str(job.id).endswith(
        "_memory"
    ):
        jobqueue.remove_job(job.id)
        raise RuntimeError("UnlockJobNotPersistent")
    job.modify(misfire_grace_time=None, coalesce=True)
    return job


@_guarded
async def lock_chat(ctx: RunContext[datatype.ContextDeps], duration: str = "") -> str:
    """Lock group sending, saving exact defaults; optional duration restores them persistently."""
    seconds = parse_duration(duration)
    current = await permissions.default_rights(ctx.deps.client, ctx.deps.chat_id)
    state = await store.get_state(ctx.deps.chat_id, 0)
    saved = state.get("lock_permissions")
    if saved is not None:
        permissions.to_native(saved)
        permissions.to_native(state.get("lock_applied"))
    if saved is not None and current != state.get("lock_applied"):
        saved = current
    saved = current if saved is None else saved
    applied = permissions.muted(current)
    generation = uuid4().hex
    until = _now() + timedelta(seconds=seconds) if seconds else None
    updates = {
        "lock_permissions": saved,
        "lock_applied": applied,
        "lock_generation": generation,
        "lock_until": until.isoformat() if until else None,
        "lock_actor_id": ctx.deps.user_id,
    }
    await store.patch_state(ctx.deps.chat_id, 0, updates)
    _mark(ctx)
    try:
        # Scheduling before the network call closes the crash-after-ACK timer gap.
        if until is not None:
            _schedule_unlock(ctx.deps.chat_id, generation, until)
        await _set_rights(ctx, applied, expected_current=current)
    except Exception as error:
        if (
            isinstance(error, ModerationDenied)
            or _confirmed_rejection(error)
            or not _operation_dispatched.get()
        ):
            await store.patch_state(
                ctx.deps.chat_id, 0, {key: state.get(key) for key in _LOCK_KEYS}
            )
        raise
    return _done(ctx, "lock")


@_guarded
async def unlock_chat(ctx: RunContext[datatype.ContextDeps]) -> str:
    """Unlock sending; restore a matching saved lock or clear current send restrictions."""
    state = await store.get_state(ctx.deps.chat_id, 0)
    current = await permissions.default_rights(ctx.deps.client, ctx.deps.chat_id)
    restored = (
        state["lock_permissions"]
        if state.get("lock_permissions") is not None
        and current == state.get("lock_applied")
        else permissions.unmuted(current)
    )
    await _set_rights(ctx, restored, expected_current=current)
    await store.patch_state(ctx.deps.chat_id, 0, {key: None for key in _LOCK_KEYS})
    return _done(ctx, "unlock")


def _retry_unlock(chat_id: int, generation: str, attempt: int):
    delay = min(15 * 2 ** min(attempt, 6), 900)
    try:
        _schedule_unlock(
            chat_id,
            generation,
            _now() + timedelta(seconds=delay),
            attempt=min(attempt + 1, 6),
        )
    except Exception as error:
        logger.error("Could not persist group unlock retry: {}", type(error).__name__)


async def unlock_expired_chat(chat_id: int, generation: str, attempt: int = 0):
    """Persistent system restoration; stale generations never overwrite new settings."""
    from waku.bot import client

    async with _locks.setdefault(chat_id, asyncio.Lock()):
        try:
            state = await store.get_state(chat_id, 0)
            if state.get("lock_generation") != generation or not state.get(
                "lock_until"
            ):
                return
            try:
                until = datetime.fromisoformat(state["lock_until"])
                if until.tzinfo is None:
                    raise ValueError("Naive lock expiry")
                restored = permissions.to_native(state.get("lock_permissions"))
                permissions.to_native(state.get("lock_applied"))
                if restored.view_messages:
                    raise ValueError("Invalid default permissions")
            except (TypeError, ValueError):
                logger.error("Invalid saved group lock state; restoration stopped")
                return
            if not client.is_connected or not client.is_initialized:
                _retry_unlock(chat_id, generation, attempt)
                return
            if until > _now():
                _schedule_unlock(chat_id, generation, until, attempt=attempt)
                return
            current = await permissions.default_rights(client, chat_id)
            if current == state["lock_permissions"]:
                # A previous restore may have succeeded before its ACK/DB write failed.
                await store.patch_state(chat_id, 0, {key: None for key in _LOCK_KEYS})
                return
            if (
                current != state.get("lock_applied")
                or state.get("lock_permissions") is None
            ):
                return
            peer = await client.resolve_peer(chat_id)
            bot = await client.get_chat_member(chat_id, client.me.id)
            if isinstance(peer, raw.types.InputPeerChat):
                await _basic_bot_privileges(client, chat_id, bot)
            if not _has_right(bot, "can_restrict_members"):
                _retry_unlock(chat_id, generation, attempt)
                return
            latest = await store.get_state(chat_id, 0)
            if latest.get("lock_generation") != generation:
                return
            if (
                await permissions.default_rights(client, chat_id)
                != state["lock_applied"]
            ):
                return
            await client.invoke(
                raw.functions.messages.EditChatDefaultBannedRights(
                    peer=peer,
                    banned_rights=restored,
                ),
                retries=1,
                sleep_threshold=0,
            )
            await store.patch_state(chat_id, 0, {key: None for key in _LOCK_KEYS})
        except Exception as error:
            logger.warning("Scheduled group unlock failed: {}", type(error).__name__)
            _retry_unlock(chat_id, generation, attempt)
