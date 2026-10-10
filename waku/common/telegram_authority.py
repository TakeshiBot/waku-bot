"""Fresh Telegram authority for settings; bot roles do not grant group rights."""

import asyncio

from pyrogram import enums, raw


async def can_manage_group_settings(
    client, user_id: int, chat_id: int, *, require_restrict_members: bool = False
) -> bool:
    if (
        type(user_id) is not int
        or user_id <= 0
        or user_id == 1087968824  # Anonymous administrator cannot be identified.
        or type(chat_id) is not int
        or chat_id >= 0
    ):
        return False
    try:
        member = await asyncio.wait_for(client.get_chat_member(chat_id, user_id), 10)
    except Exception:
        return False
    if member.status == enums.ChatMemberStatus.OWNER:
        return True
    privileges = member.privileges
    if member.status == enums.ChatMemberStatus.ADMINISTRATOR and privileges is None:
        # Basic groups expose the current administrator role without granular
        # member flags. Confirm the native peer rather than assuming any missing
        # privileges describe a basic group (channels must still fail closed).
        try:
            peer = await asyncio.wait_for(client.resolve_peer(chat_id), 10)
        except Exception:
            return False
        return isinstance(peer, raw.types.InputPeerChat) and peer.chat_id == -chat_id
    return bool(
        member.status == enums.ChatMemberStatus.ADMINISTRATOR
        and privileges
        and privileges.can_change_info is True
        and (not require_restrict_members or privileges.can_restrict_members is True)
    )


async def can_manage_bot_settings(client, user_id: int, chat_id: int) -> bool:
    if (
        type(user_id) is not int
        or user_id <= 0
        or user_id == 1087968824
        or type(chat_id) is not int
        or chat_id >= 0
    ):
        return False
    # Import inside the call to avoid common/database initialization cycles.
    from waku import database
    from waku.config import app_config

    if user_id in app_config.owners:
        return True
    user = await database.get_user_by_id(user_id)
    if user and user.is_bot_global_admin and not user.is_blocked:
        return True
    association = await database.get_association(user_id, chat_id)
    if (
        association
        and association.is_bot_admin is True
        and type(association.promoted_by) is int
        and association.promoted_by > 0
        and association.promoted_by != 1087968824
        and not (user and user.is_blocked)
    ):
        # Explicit /botpromote delegation is distinct from the retired automatic
        # owner grant, whose promoted_by was never set. It grants bot settings
        # only and is never consulted by can_manage_group_settings.
        return True
    return await can_manage_group_settings(client, user_id, chat_id)
