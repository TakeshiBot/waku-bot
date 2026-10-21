"""Persist group identity without mixing Guest IDs with native chat IDs."""

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import aliased

from waku.common.memory_store import memttlcache
from waku.config import runtime_config

from . import pagination
from .db import with_session, with_tx
from .models import ChatData, TelegramChatPresence


def _same_chat_identity(guest, chat_id, title, username):
    """Compare identity as well as ID: Guest IDs have their own namespace."""
    guest_handle = func.lower(func.coalesce(guest.username, ""))
    native_handle = func.lower(func.coalesce(username, ""))
    return or_(
        and_(guest_handle != "", guest_handle == native_handle),
        and_(
            guest_handle == "",
            native_handle == "",
            guest.chat_id == chat_id,
            guest.title == title,
        ),
    )


def _same_guest_chat(guest, chat_id, title, username):
    return and_(
        guest.scope == "guests", _same_chat_identity(guest, chat_id, title, username)
    )


async def record_telegram_presence(chat, scope="groups", *, is_member=True):
    if scope not in {"groups", "guests"} or type(chat.id) is not int:
        return
    title = (chat.title or str(chat.id))[:256]
    username = getattr(chat, "username", None)
    key = f"telegram-presence:{scope}:{chat.id}"
    value = (title, username, is_member)
    if await memttlcache.get(key) == value:
        return
    other_ids = await _record(scope, chat.id, title, username, is_member)
    other_scope = "groups" if scope == "guests" else "guests"
    for other_id in other_ids:
        await memttlcache.delete(f"telegram-presence:{other_scope}:{other_id}")
    await memttlcache.set(key, value, ttl=300)


@with_tx
async def _record(scope, chat_id, title, username, is_member, session=None):
    values = dict(
        scope=scope,
        chat_id=chat_id,
        title=title,
        username=username,
        is_member=is_member,
    )
    changed = dict(
        title=title, username=username, is_member=is_member, updated_at=func.now()
    )
    # A transient Telegram lookup failure must not erase confirmed membership.
    if scope == "groups" and is_member is None:
        changed.pop("is_member")
    if runtime_config.db_is_mysql:
        stmt = (
            mysql_insert(TelegramChatPresence)
            .values(**values)
            .on_duplicate_key_update(**changed)
        )
    else:
        insert = pg_insert if runtime_config.db_is_postgres else sqlite_insert
        stmt = (
            insert(TelegramChatPresence)
            .values(**values)
            .on_conflict_do_update(index_elements=["scope", "chat_id"], set_=changed)
        )
    await session.execute(stmt)
    # Guest delivery is sufficient evidence of Guest mode. A later native
    # message or self-join event moves the chat back into Groups.
    other_scope = "groups" if scope == "guests" else "guests"
    if scope == "groups" and is_member is not True:
        return []
    matching = (
        TelegramChatPresence.scope == other_scope,
        _same_chat_identity(TelegramChatPresence, chat_id, title, username),
    )
    other_ids = (
        (await session.execute(select(TelegramChatPresence.chat_id).where(*matching)))
        .scalars()
        .all()
    )
    if scope == "guests":
        await session.execute(
            update(TelegramChatPresence)
            .where(*matching)
            .values(is_member=False, updated_at=func.now())
        )
    else:
        await session.execute(delete(TelegramChatPresence).where(*matching))
    return other_ids


@with_session
async def get_telegram_presence_page(scope="groups", page=1, size=15, session=None):
    if scope not in {"groups", "guests"}:
        raise ValueError("invalid_scope")
    page, size = pagination.normalize_page(page, size)
    conditions = [TelegramChatPresence.scope == scope]
    if scope == "groups":
        conditions.append(TelegramChatPresence.is_member.is_(True))
        # Also hide stale native rows from versions that recorded Guest updates
        # as ordinary membership; an actual native join clears the Guest row.
        guest = aliased(TelegramChatPresence)
        conditions.append(
            ~exists(
                select(guest.chat_id).where(
                    _same_guest_chat(
                        guest,
                        TelegramChatPresence.chat_id,
                        TelegramChatPresence.title,
                        TelegramChatPresence.username,
                    )
                )
            )
        )
    total = (
        await session.execute(
            select(func.count()).select_from(TelegramChatPresence).where(*conditions)
        )
    ).scalar() or 0
    page = min(page, max(1, (total + size - 1) // size))
    rows = (
        (
            await session.execute(
                select(TelegramChatPresence)
                .where(*conditions)
                .order_by(
                    TelegramChatPresence.created_at.desc(),
                    TelegramChatPresence.chat_id.desc(),
                )
                .offset(pagination.offset_for(page, size))
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return pagination.Page(items=rows, total=total, page=page, size=size)


@with_session
async def get_unclassified_telegram_groups(
    *, after_id=None, refresh=False, size=50, session=None
):
    conditions = [ChatData.id < 0]
    # Guest deliveries must not be backfilled into ordinary membership.
    conditions.append(
        ~exists(
            select(TelegramChatPresence.chat_id).where(
                _same_guest_chat(
                    TelegramChatPresence, ChatData.id, ChatData.title, ChatData.username
                )
            )
        )
    )
    if not refresh:
        conditions.append(
            ~exists(
                select(TelegramChatPresence.chat_id).where(
                    TelegramChatPresence.scope == "groups",
                    TelegramChatPresence.chat_id == ChatData.id,
                )
            )
        )
    if after_id is not None:
        conditions.append(ChatData.id > after_id)
    return (
        (
            await session.execute(
                select(ChatData).where(*conditions).order_by(ChatData.id).limit(size)
            )
        )
        .scalars()
        .all()
    )
