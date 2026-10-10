"""Native SDK label operations and staged preset callbacks without Telegram IO."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import Client, enums, raw
from pyrogram.types import Chat, ChatAdministratorRights, ChatMember, Message, User

from waku.plugins.title import authority, title
from waku.plugins.title.permissions import ADMIN_RIGHTS, CHANNEL_ONLY, supported
from waku.plugins.title.utils import TitlePermissionsMarkup

CHAT = -1000000000123


@pytest.fixture
def env(monkeypatch):
    client = Client("title-test", api_id=1234, api_hash="offline", in_memory=True)
    client.me = User(id=99, first_name="Bot", is_bot=True)
    members = {
        2: ChatMember(
            user=User(id=2, first_name="Actor"),
            status=enums.ChatMemberStatus.ADMINISTRATOR,
            privileges=ChatAdministratorRights(
                can_promote_members=True, can_manage_tags=True
            ),
        ),
        3: ChatMember(
            user=User(id=3, first_name="Target"), status=enums.ChatMemberStatus.MEMBER
        ),
        99: ChatMember(
            user=client.me,
            status=enums.ChatMemberStatus.ADMINISTRATOR,
            privileges=ChatAdministratorRights(
                can_promote_members=True, can_manage_tags=True
            ),
        ),
    }
    rights = raw.types.ChatAdminRights(
        other=True,
        delete_messages=True,
        manage_linked_peers=True,
        manage_welcome_messages=True,
        anonymous=True,
    )
    calls = []

    async def resolve(peer):
        return (
            raw.types.InputPeerChannel(channel_id=123, access_hash=0)
            if peer == CHAT
            else raw.types.InputPeerUser(user_id=peer, access_hash=0)
        )

    async def invoke(query, **kwargs):
        if isinstance(query, raw.functions.channels.GetParticipant):
            return SimpleNamespace(
                participant=raw.types.ChannelParticipantAdmin(
                    user_id=3,
                    promoted_by=2,
                    date=1,
                    admin_rights=deepcopy(rights),
                    can_edit=True,
                )
            )
        if isinstance(query, raw.functions.channels.GetChannels):
            return SimpleNamespace(
                chats=[
                    SimpleNamespace(
                        id=123,
                        default_banned_rights=raw.types.ChatBannedRights(
                            until_date=0, edit_rank=False
                        ),
                    )
                ]
            )
        calls.append((query, kwargs))
        return raw.types.Updates(updates=[], users=[], chats=[], date=1, seq=1)

    monkeypatch.setattr(client, "resolve_peer", AsyncMock(side_effect=resolve))
    monkeypatch.setattr(
        client,
        "get_chat_member",
        AsyncMock(side_effect=lambda chat, user: deepcopy(members[user])),
    )
    monkeypatch.setattr(client, "invoke", AsyncMock(side_effect=invoke))
    config = SimpleNamespace(title_permissions=None, lang="en")
    monkeypatch.setattr(
        title.database,
        "get_chat_by_id",
        AsyncMock(return_value=SimpleNamespace(chat_config=config)),
    )
    monkeypatch.setattr(
        title.database, "get_chat_config", AsyncMock(return_value=config)
    )
    monkeypatch.setattr(title.database, "update_chat_config_fields", AsyncMock())
    title._sessions.clear()
    monkeypatch.setattr(title, "schedule_saved_menu_cleanup", Mock())
    chat = Chat(id=CHAT, type=enums.ChatType.SUPERGROUP, title="Group")
    sent = SimpleNamespace(
        id=10, chat=chat, edit_text=AsyncMock(), edit_reply_markup=AsyncMock()
    )
    message = Message(
        id=5, chat=chat, from_user=User(id=2, first_name="Actor"), text="/sett"
    )
    monkeypatch.setattr(message, "reply_text", AsyncMock(return_value=sent))
    return SimpleNamespace(
        client=client,
        members=members,
        rights=rights,
        calls=calls,
        message=message,
        sent=sent,
        config=config,
    )


def admin_target(env, *, promoter=2):
    env.members[3] = ChatMember(
        user=User(id=3, first_name="Target"),
        status=enums.ChatMemberStatus.ADMINISTRATOR,
        can_be_edited=True,
        promoted_by=User(id=promoter, first_name="Promoter"),
        privileges=ChatAdministratorRights(can_manage_chat=True),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["New tag", ""])
async def test_regular_tag_never_promotes_or_demotes(env, label):
    await authority.change_title(env.client, CHAT, 2, 3, label)
    query, kwargs = env.calls[0]
    assert isinstance(query, raw.functions.messages.EditChatParticipantRank)
    assert query.rank == label
    assert kwargs == {"retries": 1, "sleep_threshold": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["New title", ""])
async def test_admin_label_preserves_all_native_rights(env, label):
    admin_target(env)
    await authority.change_title(env.client, CHAT, 2, 3, label)
    query, kwargs = env.calls[0]
    assert isinstance(query, raw.functions.channels.EditAdmin)
    assert query.admin_rights.write() == env.rights.write()
    assert query.rank == label
    assert kwargs == {"retries": 1, "sleep_threshold": 0}


@pytest.mark.asyncio
async def test_unrelated_promoter_cannot_change_admin(env):
    admin_target(env, promoter=1)
    env.members[1] = ChatMember(
        user=User(id=1, first_name="Owner"), status=enums.ChatMemberStatus.OWNER
    )
    with pytest.raises(authority.TitleDenied):
        await authority.change_title(env.client, CHAT, 2, 3, "Label")
    assert env.calls == []


@pytest.mark.asyncio
async def test_admin_self_label_needs_bot_editable_not_self_promotion_right(env):
    admin_target(env)
    env.members[3].privileges.can_promote_members = False
    await authority.change_title(env.client, CHAT, 3, 3, "Self")
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_self_regular_tag_respects_default_and_personal_permission(
    env, monkeypatch
):
    env.members[3].privileges = None
    await authority.change_title(env.client, CHAT, 3, 3, "Self")
    env.calls.clear()
    from waku.plugins.agent.tools import moderation_permissions

    monkeypatch.setattr(
        moderation_permissions,
        "default_rights",
        AsyncMock(return_value={"edit_rank": True}),
    )
    with pytest.raises(authority.TitleDenied):
        await authority.change_title(env.client, CHAT, 3, 3, "Self")
    assert not env.calls


@pytest.mark.asyncio
async def test_fresh_permission_loss_at_native_dispatch_blocks_tag(env):
    original = env.client.resolve_peer.side_effect

    async def resolve(peer):
        if peer == 3:
            env.members[2].privileges.can_manage_tags = False
        return await original(peer)

    env.client.resolve_peer.side_effect = resolve
    with pytest.raises(authority.TitleDenied):
        await authority.change_title(env.client, CHAT, 2, 3, "Label")
    assert not env.calls


def callback(env, session, action, user_id=2):
    return SimpleNamespace(
        message=env.sent,
        from_user=User(id=user_id, first_name="User"),
        data=f"sett:{session.token}:{session.revision}:{action}",
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_preset_toggle_is_draft_save_is_cas_and_cancel_discards(env):
    await title.set_title_permissions(env.client, env.message)
    session = next(iter(title._sessions.values()))
    index = list(ADMIN_RIGHTS).index("can_send_welcome_messages")
    await title.set_title_permissions_callback(
        env.client, callback(env, session, str(index))
    )
    title.database.update_chat_config_fields.assert_not_awaited()
    assert session.draft["can_send_welcome_messages"] is True
    await title.set_title_permissions_callback(
        env.client, callback(env, session, "cancel")
    )
    assert session.draft == {}
    await title.set_title_permissions_callback(
        env.client, callback(env, session, str(index))
    )
    await title.set_title_permissions_callback(
        env.client, callback(env, session, "save")
    )
    title.database.update_chat_config_fields.assert_awaited_once_with(
        env.message.chat,
        {"title_permissions": {"can_send_welcome_messages": True}},
        expected_fields={"title_permissions": None},
    )


@pytest.mark.asyncio
async def test_preset_bound_user_and_fresh_admin_right(env):
    await title.set_title_permissions(env.client, env.message)
    session = next(iter(title._sessions.values()))
    await title.set_title_permissions_callback(
        env.client, callback(env, session, "save", user_id=3)
    )
    env.members[2].privileges.can_promote_members = False
    await title.set_title_permissions_callback(
        env.client, callback(env, session, "save")
    )
    title.database.update_chat_config_fields.assert_not_awaited()


def test_full_native_flags_and_channel_only_labels():
    assert len(ADMIN_RIGHTS) == 18 and all(supported(name) for name in ADMIN_RIGHTS)
    markup = TitlePermissionsMarkup({}, "en", "a" * 16).build()
    assert len([b for row in markup.inline_keyboard for b in row]) == 19
    assert [b.callback_data.rsplit(":", 1)[-1] for b in markup.inline_keyboard[-1]] == [
        "save"
    ]
    for name in CHANNEL_ONLY:
        index = list(ADMIN_RIGHTS).index(name)
        assert "channel" in markup.inline_keyboard[index // 2][index % 2].text


@pytest.mark.parametrize("value", ["a" * 17, "🙂", "\n", "⌚", "©"])
def test_invalid_label_is_rejected(value):
    with pytest.raises(authority.TitleDenied):
        authority.validate_title(value)


@pytest.mark.asyncio
async def test_preset_same_render_double_click_is_consumed_once(env):
    await title.set_title_permissions(env.client, env.message)
    session = next(iter(title._sessions.values()))
    click = callback(env, session, str(list(ADMIN_RIGHTS).index("can_promote_members")))
    await title.set_title_permissions_callback(env.client, click)
    await title.set_title_permissions_callback(env.client, click)
    assert session.draft["can_promote_members"] is True


@pytest.mark.asyncio
async def test_serialized_legacy_baseline_is_preserved_for_cas(env):
    env.config.title_permissions = '{"can_promote_members": true}'
    await title.set_title_permissions(env.client, env.message)
    session = next(iter(title._sessions.values()))
    await title.set_title_permissions_callback(
        env.client, callback(env, session, "save")
    )
    assert title.database.update_chat_config_fields.call_args.kwargs[
        "expected_fields"
    ] == {"title_permissions": env.config.title_permissions}


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [None, False])
async def test_unconfirmed_label_receipt_never_reports_success(env, ack):
    original = env.client.invoke.side_effect

    async def invoke(query, **kwargs):
        if isinstance(query, raw.functions.messages.EditChatParticipantRank):
            return ack
        return await original(query, **kwargs)

    env.client.invoke.side_effect = invoke
    with pytest.raises(authority.TitleDenied, match="error"):
        await authority.change_title(env.client, CHAT, 2, 3, "Label")


@pytest.mark.asyncio
async def test_preset_conflict_retains_draft_and_does_not_claim_saved(env):
    await title.set_title_permissions(env.client, env.message)
    session = next(iter(title._sessions.values()))
    session.draft["can_promote_members"] = True
    title.database.update_chat_config_fields.side_effect = ValueError("config_conflict")
    await title.set_title_permissions_callback(
        env.client, callback(env, session, "save")
    )
    assert session.baseline is None
    assert session.draft["can_promote_members"] is True
    assert "Open /sett again" in env.sent.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_preset_expiry_and_cross_message_binding_prevent_save(env):
    await title.set_title_permissions(env.client, env.message)
    session = next(iter(title._sessions.values()))
    query = callback(env, session, "save")
    query.message = SimpleNamespace(id=11, chat=env.message.chat)
    await title.set_title_permissions_callback(env.client, query)
    session.expires = 0
    await title.set_title_permissions_callback(
        env.client, callback(env, session, "save")
    )
    title.database.update_chat_config_fields.assert_not_awaited()
    assert session.token not in title._sessions


@pytest.mark.asyncio
async def test_late_admin_rights_change_blocks_label_overwrite(env):
    admin_target(env)
    original = env.client.invoke.side_effect
    count = 0

    async def invoke(query, **kwargs):
        nonlocal count
        if isinstance(query, raw.functions.channels.GetParticipant):
            count += 1
            if count == 2:
                env.rights.delete_messages = False
        return await original(query, **kwargs)

    env.client.invoke.side_effect = invoke
    with pytest.raises(authority.TitleDenied, match="changed"):
        await authority.change_title(env.client, CHAT, 2, 3, "Label")
    assert not env.calls
