"""Verification session lifecycle: registry, persistence, sweeps and Telegram actions."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from pyrogram import enums, raw
from pyrogram.client import Client
from pyrogram.errors import RPCError
from pyrogram.raw.functions.messages.edit_message import EditMessage
from pyrogram.types import (
    Chat,
    ChatPermissions,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
    User,
)

from waku import common, database
from waku.bot.client import client
from waku.database.models import ChatConfig, VerificationSession
from waku.i18n import i18n
from waku.logger import logger
from waku.plugins.agent.tools import moderation_permissions as native_permissions
from waku.plugins.title.authority import has_right
from waku.plugins.verify.challenge import (
    _challenge_markup,
    build_challenge_text,
    make_emoji_challenge,
    make_math_challenge,
    make_math_hard_challenge,
    make_qa_challenge,
)

NATIVE_RESTORE_KEY = "_native_restore_permissions"
NATIVE_APPLIED_KEY = "_native_applied_permissions"
ACTION_RECEIPTS_KEY = "_verification_action_receipts"


async def verification_authorized(
    bot, chat_id, user_id, *, actor_id=None, allow_banned=False
):
    """Current Telegram roles only; bot configuration roles grant no punishments."""
    if (
        type(chat_id) is not int
        or chat_id >= 0
        or type(user_id) is not int
        or user_id <= 0
        or user_id == 1087968824
    ):
        return False
    if actor_id is not None and (
        type(actor_id) is not int
        or actor_id <= 0
        or actor_id == 1087968824
        or actor_id == user_id
    ):
        return False
    try:
        me = bot.me or await bot.get_me()
        if user_id == me.id:
            return False
        target = await bot.get_chat_member(chat_id, user_id)
        if (
            target.user is None
            or target.user.id != user_id
            or target.status
            in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)
        ):
            return False
        allowed = (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED)
        if allow_banned:
            allowed += (enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.LEFT)
        if target.status not in allowed or (
            target.status == enums.ChatMemberStatus.RESTRICTED
            and target.is_member is False
            and not allow_banned
        ):
            return False
        own = await bot.get_chat_member(chat_id, me.id)
        if (
            own.status == enums.ChatMemberStatus.ADMINISTRATOR
            and own.privileges is None
        ):
            from waku.plugins.agent.tools.moderation import _basic_bot_privileges

            await _basic_bot_privileges(bot, chat_id, own)
        if not has_right(own, "can_restrict_members"):
            return False
        if actor_id is not None:
            actor = await bot.get_chat_member(chat_id, actor_id)
            if (
                actor.user is None
                or actor.user.id != actor_id
                or actor.user.is_bot
                or not has_right(actor, "can_restrict_members")
            ):
                return False
    except Exception:
        return False
    return True


async def capture_restore_snapshot(bot, chat_id, user_id):
    """Capture authoritative individual raw flags; failed reads never mean unrestricted."""
    if not await verification_authorized(bot, chat_id, user_id):
        raise ValueError("verification_authority")
    return await native_permissions.member_rights(bot, chat_id, user_id)


async def _verification_mutate(
    bot, session_row, action, *, actor_id=None, rights=None, expected=None
):
    """One dispatch per persisted action, retaining uncertain receipts for staff review."""
    payload = dict(session_row.payload or {})
    receipts = dict(payload.get(ACTION_RECEIPTS_KEY) or {})
    if (
        "pending" in receipts.values()
        or payload.get("_verification_manual_review") is True
    ):
        return False
    if receipts.get(action) == "done":
        return True
    dispatched = False
    try:
        peer = await bot.resolve_peer(session_row.chat_id)
        participant = await bot.resolve_peer(session_row.user_id)
        if action == "kick" and isinstance(peer, raw.types.InputPeerChat):
            query = raw.functions.messages.DeleteChatUser(
                chat_id=peer.chat_id, user_id=participant
            )
        else:
            if not isinstance(peer, raw.types.InputPeerChannel):
                return (
                    False  # Basic-group ban is only a kick; never label it permanent.
                )
            if action in ("ban", "kick"):
                native = raw.types.ChatBannedRights(until_date=0, view_messages=True)
            elif action == "unban":
                native = raw.types.ChatBannedRights(until_date=0)
            else:
                native = native_permissions.to_native(rights)
            query = raw.functions.channels.EditBanned(
                channel=peer, participant=participant, banned_rights=native
            )
        if not await verification_authorized(
            bot,
            session_row.chat_id,
            session_row.user_id,
            actor_id=actor_id,
            allow_banned=action == "unban",
        ):
            return False
        if (
            expected is not None
            and await native_permissions.member_rights(
                bot, session_row.chat_id, session_row.user_id
            )
            != expected
        ):
            return False
        receipts[action] = "pending"
        payload[ACTION_RECEIPTS_KEY] = receipts
        if action == "restrict":
            payload[NATIVE_APPLIED_KEY] = native_permissions.snapshot(
                query.banned_rights
            )
        session_row.payload = payload
        await database.update_verification_session(session_row)
        # Database IO cannot turn an earlier permission snapshot into current authority.
        if not await verification_authorized(
            bot,
            session_row.chat_id,
            session_row.user_id,
            actor_id=actor_id,
            allow_banned=action == "unban",
        ):
            raise ValueError("verification_authority")
        if (
            expected is not None
            and await native_permissions.member_rights(
                bot, session_row.chat_id, session_row.user_id
            )
            != expected
        ):
            raise ValueError("verification_permissions_changed")
        dispatched = True
        result = await bot.invoke(query, retries=1, sleep_threshold=0)
        if result is None or result is False:
            return False
        receipts[action] = "done"
        session_row.payload = {**payload, ACTION_RECEIPTS_KEY: receipts}
        await database.update_verification_session(session_row)
        return True
    except Exception as error:
        if (
            not dispatched
            or isinstance(error, RPCError)
            and 0 < getattr(error, "CODE", 500) < 500
        ):
            receipts.pop(action, None)
            session_row.payload = {**payload, ACTION_RECEIPTS_KEY: receipts}
            try:
                await database.update_verification_session(session_row)
            except Exception:
                pass
        logger.warning(
            "Verification {} not confirmed: {}", action, type(error).__name__
        )
        return False


RESULT_MESSAGE_TTL = 30

# Maximum messages deleted from the verification window after sticker verification fails.
MAX_WINDOW_MESSAGE_DELETE = 300

# --------------------------------------------------------------------------- Session registry.
# The DB is authoritative; in-memory O(1) cache maps session_id to session and (chat_id, user_id) to session_id.

_sessions: dict[int, VerificationSession] = {}
_by_user: dict[tuple[int, int], int] = {}
_user_locks: dict[tuple[int, int], asyncio.Lock] = {}
_user_lock_refs: dict[tuple[int, int], int] = {}
_user_lock_owners: dict[tuple[int, int], asyncio.Task] = {}


@asynccontextmanager
async def verification_lock(chat_id: int, user_id: int):
    """Serialize session creation and completion for one chat member."""
    key = (chat_id, user_id)
    task = asyncio.current_task()
    if task is not None and _user_lock_owners.get(key) is task:
        yield
        return
    lock = _user_locks.setdefault(key, asyncio.Lock())
    _user_lock_refs[key] = _user_lock_refs.get(key, 0) + 1
    try:
        async with lock:
            _user_lock_owners[key] = task
            try:
                yield
            finally:
                _user_lock_owners.pop(key, None)
    finally:
        refs = _user_lock_refs[key] - 1
        if refs == 0:
            _user_lock_refs.pop(key, None)
            if _user_locks.get(key) is lock:
                _user_locks.pop(key, None)
        else:
            _user_lock_refs[key] = refs


def _register(session_row: VerificationSession) -> None:
    _sessions[session_row.id] = session_row
    _by_user[(session_row.chat_id, session_row.user_id)] = session_row.id


def _unregister(session_id: int) -> None:
    session_row = _sessions.pop(session_id, None)
    if session_row is None:
        return
    if _by_user.get((session_row.chat_id, session_row.user_id)) == session_id:
        _by_user.pop((session_row.chat_id, session_row.user_id), None)


def _get_for(chat_id: int, user_id: int) -> VerificationSession | None:
    session_id = _by_user.get((chat_id, user_id))
    if session_id is None:
        return None
    return _sessions.get(session_id)


async def resolve_session(session_row):
    """Read the current durable row under the member lock; removed rows stay removed."""
    try:
        current = await database.get_verification_session(session_row.id)
    except Exception as error:
        logger.warning("Verification session read failed: {}", type(error).__name__)
        return None
    if current is None or (current.chat_id, current.user_id) != (
        session_row.chat_id,
        session_row.user_id,
    ):
        if current is None:
            _unregister(session_row.id)
        return None
    _register(current)
    return current


async def capture_restore_permissions(
    bot: Client,
    chat_id: int,
    user_id: int,
) -> ChatPermissions | None:
    """Capture existing custom restrictions; normal members return None and are fully unrestricted after verification."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception as e:
        logger.warning(
            f"verify: failed to read permissions for {user_id} in {chat_id}: {e}"
        )
        raise ValueError("verification_snapshot_unavailable") from e
    return getattr(member, "permissions", None)


async def _cleanup_session(session_row: VerificationSession) -> None:
    """Delete the DB row and remove it from the registry."""
    try:
        await database.delete_verification_session(session_row.id)
    except Exception as e:
        logger.error(f"verify: failed to delete session {session_row.id}: {e}")
        return
    _unregister(session_row.id)


async def _delete_message_later(chat_id: int, message_id: int) -> None:
    """Delete the result notice after a delay, ignoring failures."""
    await asyncio.sleep(RESULT_MESSAGE_TTL)
    try:
        await client.delete_messages(chat_id, message_id)
    except RPCError as e:
        logger.debug(f"verify: failed to auto-delete message {message_id}: {e}")


def _schedule_result_delete(chat_id: int, message_id: int | None) -> None:
    """Automatically delete result notices after their TTL."""
    if message_id is not None:
        common.spawn(_delete_message_later(chat_id, message_id))


async def _user_mention(user: User | Chat | int) -> str:
    """HTML mention for the user/channel; fall back to an ID link if lookup fails."""
    try:
        if isinstance(user, int):
            fetched = await client.get_users(user)
            target: User | Chat = fetched[0] if isinstance(fetched, list) else fetched
        else:
            target = user
        return await common.mention_html(target)
    except (RPCError, ValueError):
        user_id = getattr(user, "id", None) or user
        return f"<a href='tg://user?id={user_id}'>User</a>"


async def _delete_user_messages_in_window(session_row: VerificationSession) -> None:
    """Delete the user's messages from joining until verification failure, guarding the unrestricted sticker method."""
    created_at = session_row.created_at
    if created_at is None:
        return
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    message_ids: list[int] = []
    try:
        async for message in client.search_messages(
            session_row.chat_id,
            from_user=session_row.user_id,
            min_date=created_at,
            max_date=datetime.now(UTC),
        ):
            message_ids.append(message.id)
            if len(message_ids) >= MAX_WINDOW_MESSAGE_DELETE:
                break
    except RPCError as e:
        logger.warning(
            f"verify: failed to search history for session {session_row.id}: {e}"
        )
        return
    if not message_ids:
        return
    try:
        for start in range(0, len(message_ids), 100):
            if not await verification_authorized(
                client, session_row.chat_id, session_row.user_id, allow_banned=True
            ):
                return
            me = client.me or await client.get_me()
            own = await client.get_chat_member(session_row.chat_id, me.id)
            if not has_right(own, "can_delete_messages"):
                return
            await client.delete_messages(
                session_row.chat_id, message_ids[start : start + 100]
            )
    except RPCError as e:
        logger.warning(
            f"verify: failed to delete window messages for session {session_row.id}: {e}"
        )


async def _chat_config(chat_id: int) -> ChatConfig | None:
    """Read group configuration; return None for deleted chats."""
    try:
        return await database.get_chat_config(chat_id)
    except ValueError:
        return None


def _is_expired(session_row: VerificationSession) -> bool:
    """Attach UTC to naive SQLite timestamps before comparison."""
    expires_at = session_row.expires_at
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


async def restore_member_permissions(
    bot: Client, session_row: VerificationSession, *, actor_id=None
) -> bool:
    """Restore the pre-verification permissions without granting new rights."""
    try:
        payload = session_row.payload or {}
        receipts = payload.get(ACTION_RECEIPTS_KEY) or {}
        if (
            "pending" in receipts.values()
            or payload.get("_verification_manual_review") is True
        ):
            return False
        if session_row.method == "sticker":
            return await verification_authorized(
                bot, session_row.chat_id, session_row.user_id, actor_id=actor_id
            )
        if receipts.get("restore") == "done":
            return await verification_authorized(
                bot, session_row.chat_id, session_row.user_id, actor_id=actor_id
            )
        current = await native_permissions.member_rights(
            bot, session_row.chat_id, session_row.user_id
        )
        defaults = await native_permissions.default_rights(bot, session_row.chat_id)
        saved = payload.get(NATIVE_RESTORE_KEY)
        applied = payload.get(NATIVE_APPLIED_KEY)
        if saved is not None and applied is None:
            return False
        if saved is None:
            # Legacy ChatPermissions aggregated four flags using ANY and omitted
            # native flags/expiry. Their original restrictions cannot be inferred.
            session_row.payload = {**payload, "_verification_manual_review": True}
            await database.update_verification_session(session_row)
            return False
        native_permissions.to_native(saved)
        if applied is not None:
            native_permissions.to_native(applied)
            if current != applied:
                return False
        if saved["until_date"] and saved["until_date"] <= int(
            datetime.now(UTC).timestamp()
        ):
            saved = native_permissions.snapshot(None)
        elif (
            saved["until_date"]
            and not 30
            <= saved["until_date"] - int(datetime.now(UTC).timestamp())
            <= 366 * 86400
        ):
            return False  # Never turn a nearly expired temporary restriction permanent.
        restored = native_permissions.clamp_to_defaults(saved, defaults)
        return await _verification_mutate(
            bot,
            session_row,
            "restore",
            actor_id=actor_id,
            rights=restored,
            expected=current,
        )
    except Exception as error:
        logger.warning("Verification restore not confirmed: {}", type(error).__name__)
        return False


# --------------------------------------------------------------------------- Lifecycle.


async def load_active_sessions() -> None:
    """Load DB sessions into the registry at startup."""
    try:
        sessions = await database.get_all_verification_sessions()
    except Exception as e:
        logger.error(f"verify: failed to load active sessions: {e}")
        return
    for session_row in sessions:
        _register(session_row)
    if sessions:
        logger.info(f"verify: restored {len(sessions)} active verification session(s)")


async def verify_sweep() -> None:
    """Periodically handle timeouts, disabled verification and deleted chats; one failure does not stop the rest."""
    try:
        sessions = await database.get_all_verification_sessions()
    except Exception as e:
        logger.error(f"verify: sweep failed to load sessions: {e}")
        return
    for session_row in sessions:
        try:
            if (
                "pending"
                in ((session_row.payload or {}).get(ACTION_RECEIPTS_KEY) or {}).values()
            ):
                continue  # Unknown external outcomes require manual review, never automatic resends.
            if (session_row.payload or {}).get("_verification_manual_review") is True:
                continue
            config = await _chat_config(session_row.chat_id)
            if config is None:
                # Silently clean up the deleted chat.
                await _cleanup_session(session_row)
                continue
            if not config.verify_enabled:
                await _cancel_session(session_row)
                continue
            if _is_expired(session_row):
                await _fail_session(session_row, "timeout")
        except Exception:
            logger.exception(f"verify: sweep error on session {session_row.id}")


async def handle_user_left(chat_id: int, user_id: int) -> None:
    """On leaving or banning, delete the DB row and remove the session from the registry."""
    async with verification_lock(chat_id, user_id):
        try:
            rows = await database.get_verification_sessions_for_user(chat_id, user_id)
        except Exception as error:
            logger.warning("Verification leave read failed: {}", type(error).__name__)
            return
        for row in rows:
            receipts = (row.payload or {}).get(ACTION_RECEIPTS_KEY) or {}
            if "pending" in receipts.values() or (
                receipts.get("kick") == "done" and receipts.get("unban") != "done"
            ):
                return  # Own kick's BANNED update must not erase unfinished unban.
        try:
            await database.delete_verification_sessions_for_user(chat_id, user_id)
        except Exception as error:
            logger.warning(
                "Verification leave cleanup failed: {}", type(error).__name__
            )
            return
        session_id = _by_user.pop((chat_id, user_id), None)
        if session_id is not None:
            _unregister(session_id)


def _to_rich_html(text: str) -> str:
    """Rich-message HTML does not preserve newlines; convert them explicitly to <br>."""
    return text.replace("\n", "<br>")


async def _send_challenge(
    bot: Client,
    chat_id: int,
    session_row: VerificationSession,
    config: ChatConfig,
    lang: str,
    user_mention: str = "",
) -> Message:
    """Send the challenge and propagate failures for the caller to handle."""
    text = build_challenge_text(
        config,
        session_row.method,
        session_row.payload or {},
        session_row.attempts_left,
        wrong_prefix=False,
        lang=lang,
        user_mention=user_mention,
    )
    if session_row.method == "math_hard":
        # Rich messages: <tg-math> uses HTML mode; convert newlines explicitly to <br>.
        return await bot.send_rich_message(
            chat_id,
            InputRichMessage(html=_to_rich_html(text)),
            reply_markup=_challenge_markup(session_row, lang),
        )
    return await bot.send_message(
        chat_id, text, reply_markup=_challenge_markup(session_row, lang)
    )


async def _edit_challenge_message(
    bot: Client,
    session_row: VerificationSession,
    text: str,
    markup: InlineKeyboardMarkup | None,
) -> None:
    """Edit the challenge; math_hard is rich text and uses EditMessage.rich_message."""
    if session_row.challenge_message_id is None:
        return
    if session_row.method == "math_hard":
        peer = await bot.resolve_peer(session_row.chat_id)
        if peer is None:
            return
        extra: dict[str, Any] = {}
        if markup is not None:
            written = await markup.write(bot)
            if written is not None:
                extra["reply_markup"] = written
        await bot.invoke(
            EditMessage(
                peer=peer,
                id=session_row.challenge_message_id,
                rich_message=InputRichMessage(html=_to_rich_html(text)).write(),
                **extra,
            )
        )
    elif markup is None:
        await bot.edit_message_text(
            session_row.chat_id, session_row.challenge_message_id, text
        )
    else:
        await bot.edit_message_text(
            session_row.chat_id,
            session_row.challenge_message_id,
            text,
            reply_markup=markup,
        )


async def _fail_session(session_row: VerificationSession, reason: str) -> bool:
    """Announce only acknowledged policy actions; unknown results stay for staff review."""
    async with verification_lock(session_row.chat_id, session_row.user_id):
        session_row = await resolve_session(session_row)
        if session_row is None:
            return False
        config = await _chat_config(session_row.chat_id)
        if config is None:
            await _cleanup_session(session_row)
            return False
        action, lang = config.verify_fail_action, config.lang
        if action == "kick":
            if not await _verification_mutate(client, session_row, "kick"):
                return False
            peer = await client.resolve_peer(session_row.chat_id)
            if isinstance(peer, raw.types.InputPeerChannel):
                if not await _verification_mutate(client, session_row, "unban"):
                    return False
        elif action == "ban":
            if not await _verification_mutate(client, session_row, "ban"):
                return False
        elif not await restore_member_permissions(client, session_row):
            return False
        if session_row.challenge_message_id is not None:
            try:
                await client.delete_messages(
                    session_row.chat_id, session_row.challenge_message_id
                )
            except Exception as error:
                logger.debug(
                    "Verification challenge cleanup failed: {}", type(error).__name__
                )
        if session_row.method == "sticker":
            await _delete_user_messages_in_window(session_row)
        try:
            prefix = i18n.t(f"bot.msg.verify.{reason}_prefix", locale=lang).format(
                user=await _user_mention(session_row.user_id)
            )
            suffix = i18n.t(f"bot.msg.verify.action_{action}", locale=lang)
            notice = await client.send_message(session_row.chat_id, prefix + suffix)
            _schedule_result_delete(session_row.chat_id, notice.id)
        except Exception as error:
            logger.debug("Verification result notice failed: {}", type(error).__name__)
        await _cleanup_session(session_row)
        return True


async def _cancel_session(session_row: VerificationSession) -> None:
    """When group verification is disabled, remove verification restrictions, delete the challenge and clear the session."""
    async with verification_lock(session_row.chat_id, session_row.user_id):
        session_row = await resolve_session(session_row)
        if session_row is None:
            return False
        if not await restore_member_permissions(client, session_row):
            return
        if session_row.challenge_message_id is not None:
            try:
                await client.delete_messages(
                    session_row.chat_id, session_row.challenge_message_id
                )
            except RPCError as e:
                logger.debug(
                    f"verify: failed to delete challenge {session_row.id}: {e}"
                )
        await _cleanup_session(session_row)


async def _succeed_session(
    session_row: VerificationSession, lang: str, *, actor_id=None
) -> bool:
    """Restore first; never report approval before permissions are acknowledged."""
    async with verification_lock(session_row.chat_id, session_row.user_id):
        session_row = await resolve_session(session_row)
        if session_row is None:
            return False
        if not await restore_member_permissions(client, session_row, actor_id=actor_id):
            return False
        try:
            await database.mark_user_verified(session_row.chat_id, session_row.user_id)
        except Exception as error:
            logger.warning("Verification approval DB failure: {}", type(error).__name__)
            return False
        if session_row.challenge_message_id is not None:
            try:
                await _edit_challenge_message(
                    client,
                    session_row,
                    i18n.t("bot.msg.verify.success", locale=lang).format(
                        user=await _user_mention(session_row.user_id)
                    ),
                    None,
                )
            except Exception as error:
                logger.debug(
                    "Verification success notice failure: {}", type(error).__name__
                )
        _schedule_result_delete(session_row.chat_id, session_row.challenge_message_id)
        await _cleanup_session(session_row)
        return True


async def _admin_ban_session(
    session_row: VerificationSession, lang: str, *, actor_id=None
) -> bool:
    """Permanent ban with current human authority; report only after native ACK."""
    if actor_id is None:
        return False
    async with verification_lock(session_row.chat_id, session_row.user_id):
        session_row = await resolve_session(session_row)
        if session_row is None:
            return False
        if not await _verification_mutate(
            client, session_row, "ban", actor_id=actor_id
        ):
            return False
        if session_row.challenge_message_id is not None:
            try:
                await _edit_challenge_message(
                    client,
                    session_row,
                    i18n.t("bot.msg.verify.admin_banned", locale=lang).format(
                        user=await _user_mention(session_row.user_id)
                    ),
                    None,
                )
            except Exception as error:
                logger.debug(
                    "Verification ban notice failure: {}", type(error).__name__
                )
        _schedule_result_delete(session_row.chat_id, session_row.challenge_message_id)
        await _cleanup_session(session_row)
        return True


# --------------------------------------------------------------------------- Internal helpers.


async def _wrong_answer(
    session_row: VerificationSession,
    config: ChatConfig,
    lang: str,
    *,
    edit_message: Message | None = None,
) -> None:
    """On a wrong answer, consume an attempt; fail if exhausted, otherwise create a new question and update the notice."""
    async with verification_lock(session_row.chat_id, session_row.user_id):
        session_row = await resolve_session(session_row)
        if session_row is None:
            return
        session_row.attempts_left -= 1
        if session_row.attempts_left <= 0:
            await _fail_session(session_row, "failed")
            return
        new_payload: dict = {}
        if session_row.method == "math_easy":
            new_payload = make_math_challenge()
        elif session_row.method == "math_hard":
            new_payload = make_math_hard_challenge(lang)
        elif session_row.method == "emoji":
            new_payload = make_emoji_challenge()
        elif session_row.method == "custom_qa":
            new_payload = make_qa_challenge(config.verify_questions, lang)
        new_payload.update(
            {
                key: value
                for key, value in (session_row.payload or {}).items()
                if key.startswith("_")
            }
        )
        session_row.payload = new_payload
        await database.update_verification_session(session_row)
        if edit_message is not None:
            try:
                await _edit_challenge_message(
                    client,
                    session_row,
                    build_challenge_text(
                        config,
                        session_row.method,
                        session_row.payload or {},
                        session_row.attempts_left,
                        wrong_prefix=True,
                        lang=lang,
                        user_mention=await _user_mention(session_row.user_id),
                    ),
                    _challenge_markup(session_row, lang),
                )
            except RPCError as e:
                logger.debug(
                    f"verify: failed to refresh challenge {session_row.id}: {e}"
                )
