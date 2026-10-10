"""Discord settings storage, isolated from Telegram chat and user identities."""

from dataclasses import fields as dataclass_fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import with_session, with_tx
from .models import ChatConfig, DiscordChatData

_GLOBAL_SETTINGS_ID = 0  # Guild IDs are positive; personal DM IDs are negative.


@with_session
async def get_discord_global_ai_enabled(session: AsyncSession | None = None) -> bool:
    assert session is not None
    row = await session.get(DiscordChatData, _GLOBAL_SETTINGS_ID)
    if row is None:
        return True
    value = (row.config or {}).get("discord_global_ai_enabled", True)
    return value if isinstance(value, bool) else False


@with_tx
async def set_discord_global_ai_enabled(
    enabled: bool, session: AsyncSession | None = None
) -> None:
    if not isinstance(enabled, bool):
        raise ValueError("Discord global AI switch must be boolean")
    assert session is not None
    row = (
        await session.execute(
            select(DiscordChatData).where(DiscordChatData.id == _GLOBAL_SETTINGS_ID).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        row = DiscordChatData(id=_GLOBAL_SETTINGS_ID, title="Discord bot settings")
        session.add(row)
    config = dict(row.config or {})
    config["discord_global_ai_enabled"] = enabled
    row.config = config
    await session.flush()


def _check_id(chat_id: int) -> None:
    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
        raise ValueError("Discord settings ID must be an integer")
    if chat_id == 0 or abs(chat_id) > 2**63 - 1:
        raise ValueError("Discord settings ID is outside the signed bigint range")


@with_session
async def get_discord_chat_config(
    chat_id: int, session: AsyncSession | None = None
) -> ChatConfig:
    """Positive IDs are guilds; negative IDs are Discord DM users."""
    _check_id(chat_id)
    assert session is not None
    chat = await session.get(DiscordChatData, chat_id)
    config = chat.chat_config if chat is not None else ChatConfig()
    if chat_id > 0:
        # Old approval flags remain stored for compatibility but no longer
        # control access to guilds the bot has joined.
        config.discord_enabled = True
    return config


@with_tx
async def patch_discord_chat_config(
    chat_id: int,
    updates: dict,
    title: str | None = None,
    username: str | None = None,
    session: AsyncSession | None = None,
) -> ChatConfig:
    """Apply selected fields to the latest stored Discord configuration."""
    _check_id(chat_id)
    valid = {field.name for field in dataclass_fields(ChatConfig)}
    if not updates.keys() <= valid:
        raise ValueError("Unknown Discord configuration field")
    if "discord_reply_to_bots" in updates and not isinstance(
        updates["discord_reply_to_bots"], bool
    ):
        raise ValueError("Discord bot reply switch must be boolean")
    assert session is not None
    chat = (
        await session.execute(
            select(DiscordChatData).where(DiscordChatData.id == chat_id).with_for_update()
        )
    ).scalar_one_or_none()
    if chat is None:
        chat = DiscordChatData(id=chat_id, title=(title or str(chat_id))[:256])
        session.add(chat)
    elif title:
        chat.title = title[:256]
    if username is not None:
        chat.username = username[:64]
    # Preserve unknown JSON keys as well as the current platform's known fields.
    config = dict(chat.config or {})
    config.update(updates)
    chat.config = config
    await session.flush()
    return chat.chat_config


@with_session
async def list_discord_guilds(
    session: AsyncSession | None = None,
) -> list[tuple[int, dict]]:
    """Return all known guilds, including rows from the retired approval flow."""
    assert session is not None
    rows = await session.execute(
        select(DiscordChatData.id, DiscordChatData.config)
        .where(DiscordChatData.id > 0)
        .order_by(DiscordChatData.id)
    )
    return [(chat_id, config) for chat_id, config in rows.all()]
