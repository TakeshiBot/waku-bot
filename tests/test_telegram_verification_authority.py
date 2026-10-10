"""Real Kurigram participant parsing and verification punishment/restoration guards."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import Client, raw
from pyrogram.enums import ChatType
from pyrogram.errors import ChatAdminRequired, Timeout
from pyrogram.types import Chat, User

from waku.database.models import VerificationSession
from waku.plugins.verify import session as sess
from waku.plugins.verify import verify

CHAT = -1000000000123


def member(user_id):
    return raw.types.ChannelParticipant(user_id=user_id, date=1)


def admin(user_id, *, restrict=True):
    return raw.types.ChannelParticipantAdmin(
        user_id=user_id,
        promoted_by=1,
        date=1,
        admin_rights=raw.types.ChatAdminRights(other=True, ban_users=restrict),
        can_edit=True,
    )


@pytest.fixture
def env(monkeypatch):
    client = Client("verify-offline", api_id=1234, api_hash="offline", in_memory=True)
    client.me = User(id=99, first_name="Bot", is_bot=True)
    client.is_connected = client.is_initialized = True
    members = {
        1: raw.types.ChannelParticipantCreator(
            user_id=1, admin_rights=raw.types.ChatAdminRights()
        ),
        2: admin(2),
        3: member(3),
        99: admin(99),
    }
    defaults = sess.native_permissions.snapshot(
        raw.types.ChatBannedRights(until_date=0, send_gifs=True)
    )
    writes, events = [], []
    row = VerificationSession(
        id=1,
        chat_id=CHAT,
        user_id=3,
        method="math_easy",
        payload={},
        challenge_message_id=20,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        attempts_left=3,
    )
    config = SimpleNamespace(lang="en", verify_fail_action="ban", verify_enabled=True)

    async def resolve(peer):
        return (
            raw.types.InputPeerChannel(channel_id=123, access_hash=0)
            if peer == CHAT
            else raw.types.InputPeerUser(user_id=peer, access_hash=0)
        )

    async def invoke(query, **kwargs):
        if isinstance(query, raw.functions.channels.GetParticipant):
            return raw.types.channels.ChannelParticipant(
                participant=deepcopy(members[query.participant.user_id]),
                chats=[],
                users=[
                    raw.types.User(id=i, first_name=f"User{i}", bot=i == 99)
                    for i in members
                ],
            )
        if isinstance(query, raw.functions.channels.GetChannels):
            return SimpleNamespace(
                chats=[
                    SimpleNamespace(
                        id=123,
                        default_banned_rights=sess.native_permissions.to_native(
                            defaults
                        ),
                    )
                ]
            )
        writes.append((query, kwargs))
        events.append("write")
        if isinstance(query, raw.functions.channels.EditBanned):
            rights = deepcopy(query.banned_rights)
            members[3] = raw.types.ChannelParticipantBanned(
                peer=raw.types.PeerUser(user_id=3),
                kicked_by=99,
                date=1,
                banned_rights=rights,
                left=bool(rights.view_messages),
            )
        return raw.types.Updates(updates=[], users=[], chats=[], date=1, seq=1)

    monkeypatch.setattr(client, "resolve_peer", AsyncMock(side_effect=resolve))
    monkeypatch.setattr(client, "invoke", AsyncMock(side_effect=invoke))
    monkeypatch.setattr(
        client, "send_message", AsyncMock(return_value=SimpleNamespace(id=30))
    )
    monkeypatch.setattr(client, "delete_messages", AsyncMock(return_value=1))
    monkeypatch.setattr(sess, "client", client)
    monkeypatch.setattr(sess, "_chat_config", AsyncMock(return_value=config))
    monkeypatch.setattr(verify, "_chat_config", AsyncMock(return_value=config))
    monkeypatch.setattr(sess, "_user_mention", AsyncMock(return_value="User"))
    monkeypatch.setattr(
        sess,
        "_edit_challenge_message",
        AsyncMock(side_effect=lambda *a: events.append("edit")),
    )
    monkeypatch.setattr(sess, "_schedule_result_delete", Mock())
    store = {1: row}
    monkeypatch.setattr(
        sess.database,
        "get_verification_session",
        AsyncMock(side_effect=lambda identifier: store.get(identifier)),
    )
    monkeypatch.setattr(
        sess.database,
        "get_verification_sessions_for_user",
        AsyncMock(
            side_effect=lambda chat, user: [
                r for r in store.values() if r.chat_id == chat and r.user_id == user
            ]
        ),
    )
    monkeypatch.setattr(
        sess.database,
        "update_verification_session",
        AsyncMock(side_effect=lambda current: store.update({current.id: current})),
    )
    monkeypatch.setattr(
        sess.database,
        "delete_verification_session",
        AsyncMock(side_effect=lambda identifier: store.pop(identifier, None)),
    )
    monkeypatch.setattr(sess.database, "mark_user_verified", AsyncMock())
    sess._sessions.clear()
    sess._by_user.clear()
    sess._register(row)
    return SimpleNamespace(
        client=client,
        row=row,
        config=config,
        members=members,
        defaults=defaults,
        writes=writes,
        events=events,
        invoke=invoke,
        store=store,
    )


@pytest.mark.asyncio
async def test_bot_role_admin_without_telegram_restrict_cannot_ban(env, monkeypatch):
    env.members[2] = member(2)
    query = SimpleNamespace(
        from_user=User(id=2, first_name="Bot admin"),
        message=SimpleNamespace(id=20, chat=Chat(id=CHAT, type=ChatType.SUPERGROUP)),
        data="verify_admin:1:ban",
        answer=AsyncMock(),
    )
    monkeypatch.setattr(verify, "message_locale", AsyncMock(return_value="en"))
    monkeypatch.setattr(
        verify.database,
        "get_user_config",
        AsyncMock(return_value=SimpleNamespace(lang="en")),
    )
    await verify.on_verify_admin_callback(env.client, query)
    assert not env.writes
    assert query.answer.call_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_admin_ban_announces_only_after_ack(env):
    assert await sess._admin_ban_session(env.row, "en", actor_id=2)
    assert env.events == ["write", "edit"]
    assert env.writes[0][1] == {"retries": 1, "sleep_threshold": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ChatAdminRequired(), Timeout()])
async def test_failed_or_unknown_ban_never_announces_success(env, error):
    original = env.client.invoke.side_effect

    async def invoke(query, **kwargs):
        if isinstance(query, raw.functions.channels.EditBanned):
            env.writes.append((query, kwargs))
            raise error
        return await original(query, **kwargs)

    env.client.invoke.side_effect = invoke
    assert not await sess._admin_ban_session(env.row, "en", actor_id=2)
    assert "edit" not in env.events
    assert env.row.id in sess._sessions
    if isinstance(error, Timeout):
        assert not await sess._admin_ban_session(env.row, "en", actor_id=2)
        assert len(env.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("target_admin,bot_restrict", [(True, True), (False, False)])
async def test_scheduled_policy_fresh_roles_protect_new_admin_or_missing_bot_right(
    env, target_admin, bot_restrict
):
    if target_admin:
        env.members[3] = admin(3)
    env.members[99] = admin(99, restrict=bot_restrict)
    assert not await sess._fail_session(env.row, "timeout")
    assert not env.writes
    env.client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_permission_loss_after_peer_resolution_blocks_native_write(env):
    original = env.client.resolve_peer.side_effect

    async def resolve(peer):
        if peer == 3:
            env.members[2] = member(2)
        return await original(peer)

    env.client.resolve_peer.side_effect = resolve
    assert not await sess._admin_ban_session(env.row, "en", actor_id=2)
    assert not env.writes


@pytest.mark.asyncio
async def test_native_capture_and_restore_keep_individual_flags_and_new_defaults(env):
    prior = sess.native_permissions.snapshot(
        raw.types.ChatBannedRights(
            until_date=0, send_stickers=True, send_gifs=False, edit_rank=True
        )
    )
    env.members[3] = raw.types.ChannelParticipantBanned(
        peer=raw.types.PeerUser(user_id=3),
        kicked_by=1,
        date=1,
        banned_rights=sess.native_permissions.to_native(prior),
        left=False,
    )
    captured = await sess.capture_restore_snapshot(env.client, CHAT, 3)
    assert captured == prior
    env.row.payload[sess.NATIVE_RESTORE_KEY] = captured
    assert await sess._verification_mutate(
        env.client,
        env.row,
        "restrict",
        rights=sess.native_permissions.muted(captured),
        expected=captured,
    )
    env.defaults["send_photos"] = True
    assert await sess.restore_member_permissions(env.client, env.row, actor_id=2)
    rights = env.writes[-1][0].banned_rights
    assert (
        rights.send_stickers
        and rights.send_gifs
        and rights.send_photos
        and rights.edit_rank
    )
    assert not rights.send_messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {sess.NATIVE_RESTORE_KEY: {"until_date": 0}},
        {"_restore_permissions": {"can_send_messages": True}},
    ],
)
async def test_missing_or_malformed_snapshot_never_unrestricts(env, payload):
    env.row.payload = payload
    assert not await sess.restore_member_permissions(env.client, env.row)
    assert not env.writes


@pytest.mark.asyncio
async def test_external_rights_change_prevents_restore(env):
    prior = sess.native_permissions.snapshot(None)
    env.row.payload[sess.NATIVE_RESTORE_KEY] = prior
    assert await sess._verification_mutate(
        env.client,
        env.row,
        "restrict",
        rights=sess.native_permissions.muted(prior),
        expected=prior,
    )
    env.members[3].banned_rights.invite_users = True
    assert not await sess.restore_member_permissions(env.client, env.row)
    assert len(env.writes) == 1


@pytest.mark.asyncio
async def test_timeout_pending_receipt_sweep_never_resends(env, monkeypatch):
    env.row.payload[sess.ACTION_RECEIPTS_KEY] = {"ban": "pending"}
    env.row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    monkeypatch.setattr(
        sess.database,
        "get_all_verification_sessions",
        AsyncMock(return_value=[env.row]),
    )
    await sess.verify_sweep()
    assert not env.writes


@pytest.mark.asyncio
async def test_unknown_kick_never_attempts_unban(env):
    env.config.verify_fail_action = "kick"
    original = env.client.invoke.side_effect

    async def invoke(query, **kwargs):
        if isinstance(query, raw.functions.channels.EditBanned):
            env.writes.append((query, kwargs))
            raise Timeout()
        return await original(query, **kwargs)

    env.client.invoke.side_effect = invoke
    assert not await sess._fail_session(env.row, "timeout")
    assert len(env.writes) == 1 and env.writes[0][0].banned_rights.view_messages
    env.client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_snapshot_metadata_survives_wrong_answer(env, monkeypatch):
    env.row.payload[sess.NATIVE_RESTORE_KEY] = sess.native_permissions.snapshot(None)
    env.row.payload[sess.NATIVE_APPLIED_KEY] = sess.native_permissions.muted(
        sess.native_permissions.snapshot(None)
    )
    env.row.payload[sess.ACTION_RECEIPTS_KEY] = {"restrict": "done"}
    env.config.verify_questions = []
    env.config.verify_max_attempts = 3
    monkeypatch.setattr(
        sess, "make_math_challenge", lambda: {"options": [1], "answers": [1]}
    )
    await sess._wrong_answer(env.row, env.config, "en")
    assert sess.NATIVE_RESTORE_KEY in env.row.payload
    assert sess.NATIVE_APPLIED_KEY in env.row.payload
    assert env.row.payload[sess.ACTION_RECEIPTS_KEY] == {"restrict": "done"}


@pytest.mark.asyncio
async def test_unknown_ban_cannot_be_overridden_by_restore(env):
    env.row.payload[sess.ACTION_RECEIPTS_KEY] = {"ban": "pending"}
    env.row.payload[sess.NATIVE_RESTORE_KEY] = sess.native_permissions.snapshot(None)
    assert not await sess.restore_member_permissions(env.client, env.row)
    assert not await sess._verification_mutate(
        env.client, env.row, "restore", rights=sess.native_permissions.snapshot(None)
    )
    assert not env.writes


@pytest.mark.asyncio
async def test_sticker_approval_cannot_clear_an_unknown_punishment_receipt(env):
    env.row.method = "sticker"
    env.row.payload[sess.ACTION_RECEIPTS_KEY] = {"ban": "pending"}
    assert not await sess._succeed_session(env.row, "en")
    sess.database.mark_user_verified.assert_not_awaited()
    sess.database.delete_verification_session.assert_not_awaited()
    assert env.store[1].payload[sess.ACTION_RECEIPTS_KEY] == {"ban": "pending"}


@pytest.mark.asyncio
async def test_authoritative_capture_failure_never_means_unrestricted(env):
    env.client.invoke.side_effect = Timeout()
    with pytest.raises(ValueError):
        await sess.capture_restore_snapshot(env.client, CHAT, 3)
    with pytest.raises(ValueError):
        await sess.capture_restore_permissions(env.client, CHAT, 3)


@pytest.mark.asyncio
async def test_basic_group_policy_kick_is_single_native_delete_not_ban_unban(
    env, monkeypatch
):
    known = {user: await env.client.get_chat_member(CHAT, user) for user in env.members}
    monkeypatch.setattr(
        env.client,
        "get_chat_member",
        AsyncMock(side_effect=lambda chat, user: known[user]),
    )
    original = env.client.resolve_peer.side_effect

    async def resolve(peer):
        return (
            raw.types.InputPeerChat(chat_id=123)
            if peer == CHAT
            else await original(peer)
        )

    env.client.resolve_peer.side_effect = resolve
    env.config.verify_fail_action = "ban"
    assert not await sess._fail_session(env.row, "timeout")
    assert not env.writes
    env.config.verify_fail_action = "kick"
    assert await sess._fail_session(env.row, "timeout")
    assert len(env.writes) == 1 and isinstance(
        env.writes[0][0], raw.functions.messages.DeleteChatUser
    )


@pytest.mark.asyncio
async def test_legacy_aggregated_permissions_require_manual_review_without_grants(
    env,
):
    from pyrogram.types import ChatPermissions

    from waku.plugins.verify.challenge import PERMISSION_FIELDS

    env.row.payload["_restore_permissions"] = {name: True for name in PERMISSION_FIELDS}
    applied = ChatPermissions().write()
    env.members[3] = raw.types.ChannelParticipantBanned(
        peer=raw.types.PeerUser(user_id=3),
        kicked_by=99,
        date=1,
        banned_rights=applied,
        left=False,
    )
    assert not await sess.restore_member_permissions(env.client, env.row)
    assert not env.writes
    assert env.row.payload["_verification_manual_review"] is True


@pytest.mark.asyncio
async def test_nearly_expired_snapshot_does_not_become_permanent(env):
    saved = sess.native_permissions.snapshot(
        raw.types.ChatBannedRights(
            until_date=int(datetime.now(UTC).timestamp()) + 10, invite_users=True
        )
    )
    applied = sess.native_permissions.muted(saved)
    applied["until_date"] = 0
    env.row.payload.update(
        {sess.NATIVE_RESTORE_KEY: saved, sess.NATIVE_APPLIED_KEY: applied}
    )
    env.members[3] = raw.types.ChannelParticipantBanned(
        peer=raw.types.PeerUser(user_id=3),
        kicked_by=99,
        date=1,
        banned_rights=sess.native_permissions.to_native(applied),
        left=False,
    )
    assert not await sess.restore_member_permissions(env.client, env.row)
    assert not env.writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipts", [{"kick": "done"}, {"unban": "pending"}, {"restrict": "pending"}]
)
async def test_leave_update_preserves_unfinished_durable_receipts_even_without_registry(
    env, monkeypatch, receipts
):
    env.row.payload[sess.ACTION_RECEIPTS_KEY] = receipts
    sess._sessions.clear()
    sess._by_user.clear()
    delete = AsyncMock()
    monkeypatch.setattr(sess.database, "delete_verification_sessions_for_user", delete)

    await sess.handle_user_left(CHAT, 3)

    delete.assert_not_awaited()
    assert env.store[1] is env.row


@pytest.mark.asyncio
async def test_leave_update_cleans_confirmed_completed_kick(env, monkeypatch):
    env.row.payload[sess.ACTION_RECEIPTS_KEY] = {"kick": "done", "unban": "done"}
    delete = AsyncMock(side_effect=lambda *args: env.store.clear())
    monkeypatch.setattr(sess.database, "delete_verification_sessions_for_user", delete)

    await sess.handle_user_left(CHAT, 3)

    delete.assert_awaited_once_with(CHAT, 3)
    assert not env.store and not sess._sessions


@pytest.mark.asyncio
async def test_removed_session_cannot_be_resurrected_by_stale_wrong_answer(env):
    stale = deepcopy(env.row)
    env.store.clear()
    await sess._wrong_answer(stale, env.config, "en")
    assert not env.store
    sess.database.update_verification_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_answer_waits_for_punishment_and_preserves_unknown_receipt(
    env, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()
    stale = deepcopy(env.row)
    original = env.client.invoke.side_effect
    env.config.verify_questions = []
    env.config.verify_max_attempts = 3
    monkeypatch.setattr(
        sess, "make_math_challenge", lambda: {"options": [1], "answers": [1]}
    )

    async def invoke(query, **kwargs):
        if isinstance(query, raw.functions.channels.EditBanned):
            entered.set()
            await release.wait()
            raise Timeout()
        return await original(query, **kwargs)

    env.client.invoke.side_effect = invoke
    punishment = asyncio.create_task(sess._admin_ban_session(env.row, "en", actor_id=2))
    await asyncio.wait_for(entered.wait(), timeout=2)
    answer = asyncio.create_task(sess._wrong_answer(stale, env.config, "en"))
    await asyncio.sleep(0)
    assert not answer.done()
    release.set()
    assert not await asyncio.wait_for(punishment, timeout=2)
    await asyncio.wait_for(answer, timeout=2)
    assert env.store[1].payload[sess.ACTION_RECEIPTS_KEY] == {"ban": "pending"}


@pytest.mark.asyncio
async def test_member_lock_is_reentrant_and_releases_cancelled_waiters():
    async def exercise():
        async with sess.verification_lock(CHAT, 3):
            async with sess.verification_lock(CHAT, 3):

                async def wait():
                    async with sess.verification_lock(CHAT, 3):
                        raise AssertionError("cancelled waiter entered")

                task = asyncio.create_task(wait())
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    await asyncio.wait_for(exercise(), timeout=2)
    assert (CHAT, 3) not in sess._user_locks
    assert (CHAT, 3) not in sess._user_lock_owners


@pytest.mark.asyncio
async def test_concurrent_multi_select_keeps_both_selections_without_lost_receipts(
    env, monkeypatch
):
    env.row.method = "custom_qa"
    env.row.payload = {
        "options": ["a", "b", "c"],
        "answers": ["a", "b"],
        "selected": [],
        sess.ACTION_RECEIPTS_KEY: {"restrict": "done"},
    }
    message = SimpleNamespace(
        id=20,
        chat=Chat(id=CHAT, type=ChatType.SUPERGROUP),
        edit_reply_markup=AsyncMock(),
    )
    monkeypatch.setattr(verify, "message_locale", AsyncMock(return_value="en"))
    queries = [
        SimpleNamespace(
            from_user=User(id=3, first_name="Member"),
            message=message,
            data=f"verify:1:{index}",
            answer=AsyncMock(),
        )
        for index in (0, 1)
    ]
    await asyncio.wait_for(
        asyncio.gather(
            *[verify.on_verify_callback(env.client, query) for query in queries]
        ),
        timeout=2,
    )
    assert env.store[1].payload["selected"] == [0, 1]
    assert env.store[1].payload[sess.ACTION_RECEIPTS_KEY] == {"restrict": "done"}
    assert not env.writes
