"""Public Telegram administrator flags and their installed MTProto equivalents."""

import inspect
import json

from pyrogram import raw
from pyrogram.types import ChatAdministratorRights

ADMIN_RIGHTS = {
    "can_manage_chat": "other",
    "can_change_info": "change_info",
    "can_delete_messages": "delete_messages",
    "can_restrict_members": "ban_users",
    "can_invite_users": "invite_users",
    "can_pin_messages": "pin_messages",
    "can_promote_members": "add_admins",
    "is_anonymous": "anonymous",
    "can_manage_video_chats": "manage_call",
    "can_manage_topics": "manage_topics",
    "can_manage_tags": "manage_ranks",
    "can_post_stories": "post_stories",
    "can_edit_stories": "edit_stories",
    "can_delete_stories": "delete_stories",
    "can_send_welcome_messages": "manage_welcome_messages",
    "can_post_messages": "post_messages",
    "can_edit_messages": "edit_messages",
    "can_manage_direct_messages": "manage_direct_messages",
}
CHANNEL_ONLY = frozenset(
    {"can_post_messages", "can_edit_messages", "can_manage_direct_messages"}
)


def supported(name: str) -> bool:
    return (
        name in ADMIN_RIGHTS
        and name in inspect.signature(ChatAdministratorRights).parameters
        and ADMIN_RIGHTS[name]
        in inspect.signature(raw.types.ChatAdminRights).parameters
    )


def group_preset(value: object) -> dict[str, bool]:
    """Reject unsupported or malformed grants rather than silently losing rights."""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or any(type(v) is not bool for v in value.values()):
        raise ValueError("invalid_permissions")
    if any(v and (k in CHANNEL_ONLY or not supported(k)) for k, v in value.items()):
        raise ValueError("unsupported_permissions")
    return {k: v for k, v in value.items() if k in ADMIN_RIGHTS}
