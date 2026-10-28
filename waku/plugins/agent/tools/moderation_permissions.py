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


def normalize_defaults(value: dict | None) -> dict:
    """Upgrade a persisted default-rights snapshot from older Telegram layers.

    Telegram has added granular media rights over time. Old active locks may
    therefore lack fields that a newer Kurigram release requires. The old
    aggregate fields describe their safe effective value; unrelated new rights
    default to allowed, as they did before Telegram introduced them.
    """
    if not isinstance(value, dict):
        raise ValueError("Invalid banned-rights snapshot")
    date = value.get("until_date", 0)
    if type(date) is not int or not 0 <= date <= 2**31 - 1:
        raise ValueError("Invalid banned-rights expiry")
    if any(name in value and type(value[name]) is not bool for name in RIGHT_FIELDS):
        raise ValueError("Invalid banned-rights snapshot")

    legacy_messages = bool(value.get("send_messages", False))
    legacy_media = bool(value.get("send_media", False))
    granular_media = {
        "send_photos",
        "send_videos",
        "send_roundvideos",
        "send_audios",
        "send_voices",
        "send_docs",
    }
    return {
        "until_date": date,
        **{
            name: value.get(
                name,
                legacy_messages
                if name == "send_plain"
                else legacy_media
                if name in granular_media
                else False,
            )
            for name in RIGHT_FIELDS
        },
    }


def same_defaults(left: dict | None, right: dict | None) -> bool:
    """Compare effective group defaults, not Telegram's wire representation.

    Telegram returns INT32_MAX for default-rights until_date, even when the
    request used 0. The legacy send_messages/send_media aggregate flags are
    also redundant with the granular send permissions used by modern clients.
    TDLib's RestrictedRights follows the same granular comparison:
    https://github.com/tdlib/td/blob/master/td/telegram/DialogParticipant.cpp
    Personal mute expiries must still be compared exactly; this is group-only.
    """
    try:
        left = normalize_defaults(left)
        right = normalize_defaults(right)
    except ValueError:
        return False
    return all(
        left.get(name) == right.get(name)
        for name in RIGHT_FIELDS
        if name not in {"send_messages", "send_media"}
    )


def to_native(value: dict, *, until_date: int | None = None):
    value = normalize_defaults(value)
    date = value["until_date"] if until_date is None else until_date
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
