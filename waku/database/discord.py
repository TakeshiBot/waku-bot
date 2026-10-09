"""Discord settings storage, isolated from Telegram chat and user identities."""

from dataclasses import fields as dataclass_fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import with_session, with_tx
from .models import ChatConfig, DiscordChatData


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
    return chat.chat_config if chat is not None else ChatConfig()


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
async def list_authorized_discord_servers(
    session: AsyncSession | None = None,
) -> list[tuple[int, dict]]:
    """Return authorized guilds, excluding DM rows even when enabled."""
    assert session is not None
    rows = await session.execute(
        select(DiscordChatData.id, DiscordChatData.config)
        .where(
            DiscordChatData.id > 0,
            DiscordChatData.config["discord_enabled"].as_boolean().is_(True),
        )
        .order_by(DiscordChatData.id)
    )
    return [(chat_id, config) for chat_id, config in rows.all()]
