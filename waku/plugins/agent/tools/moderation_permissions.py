"""Lossless Telegram banned-rights snapshots for group moderation.

Kurigram's ChatPermissions collapses stickers/GIFs/games/inline into one value.
Store native flags so restoring a mute or lock cannot grant previously denied rights.
"""

from __future__ import annotations

import inspect
from typing import Any

from pyrogram import raw

RIGHT_FIELDS = tuple(
    name
    for name in inspect.signature(raw.types.ChatBannedRights).parameters
    if name != "until_date"
)
SEND_FIELDS = (
    "send_messages",
    "send_media",
    "send_stickers",
    "send_gifs",
    "send_games",
    "send_inline",
    "embed_links",
    "send_polls",
    "send_photos",
    "send_videos",
    "send_roundvideos",
    "send_audios",
    "send_voices",
    "send_docs",
    "send_plain",
    "send_reactions",
)


def snapshot(rights: Any | None) -> dict:
    """Explicit bools make native absent flags and restored flags comparable."""
    return {
        "until_date": int(getattr(rights, "until_date", 0) or 0),
        **{name: bool(getattr(rights, name, False)) for name in RIGHT_FIELDS},
    }


def to_native(value: dict, *, until_date: int | None = None):
    if not isinstance(value, dict) or any(
        type(value.get(name)) is not bool for name in RIGHT_FIELDS
    ):
        raise ValueError("Invalid banned-rights snapshot")
    date = value.get("until_date") if until_date is None else until_date
    if type(date) is not int or not 0 <= date <= 2**31 - 1:
        raise ValueError("Invalid banned-rights expiry")
    return raw.types.ChatBannedRights(
        until_date=date,
        **{name: value[name] for name in RIGHT_FIELDS},
    )


def muted(value: dict, *, until_date: int = 0) -> dict:
    result = dict(value)
    result.update({name: True for name in SEND_FIELDS})
    result["view_messages"] = False
    result["until_date"] = until_date
    return result


def unmuted(value: dict) -> dict:
    result = dict(value)
    result.update({name: False for name in SEND_FIELDS})
    result["view_messages"] = False
    result["until_date"] = 0
    return result


def clamp_to_defaults(value: dict, defaults: dict) -> dict:
    """Restore personal restrictions while respecting newer group restrictions."""
    to_native(value)
    to_native(defaults)
    return {
        "until_date": int(value.get("until_date", 0)),
        **{name: bool(value[name] or defaults[name]) for name in RIGHT_FIELDS},
    }


async def default_rights(client, chat_id: int) -> dict:
    peer = await client.resolve_peer(chat_id)
    if isinstance(peer, raw.types.InputPeerChannel):
        response = await client.invoke(raw.functions.channels.GetChannels(id=[peer]))
        expected_id = peer.channel_id
    elif isinstance(peer, raw.types.InputPeerChat):
        response = await client.invoke(
            raw.functions.messages.GetChats(id=[peer.chat_id])
        )
        expected_id = peer.chat_id
    else:
        raise ValueError("Not a Telegram group")
    chat = next((chat for chat in response.chats if chat.id == expected_id), None)
    if chat is None:
        raise ValueError("Group rights could not be verified")
    # Missing default_banned_rights on a native group means no default restrictions.
    return snapshot(getattr(chat, "default_banned_rights", None))


async def member_rights(client, chat_id: int, user_id: int) -> dict:
    peer = await client.resolve_peer(chat_id)
    participant = await client.resolve_peer(user_id)
    response = await client.invoke(
        raw.functions.channels.GetParticipant(channel=peer, participant=participant)
    )
    member = response.participant
    if isinstance(member, raw.types.ChannelParticipantBanned):
        if member.banned_rights.view_messages:
            raise ValueError("Banned member cannot be unmuted")
        return snapshot(member.banned_rights)
    if not isinstance(
        member, (raw.types.ChannelParticipant, raw.types.ChannelParticipantSelf)
    ):
        raise ValueError("Member rights could not be verified")
    return snapshot(None)
