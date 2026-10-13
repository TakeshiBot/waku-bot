"""A runtime Markdown instruction file scoped to Telegram bot-admin DMs."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pyrogram.enums import ChatType

from waku import database
from waku.config import app_config
from waku.logger import logger

INSTRUCTIONS_FILE = Path("data/admin_dm/AGENTS.md")
MAX_FILE_BYTES = 32 * 1024
_SAVE_LOCK = asyncio.Lock()


class InstructionsError(ValueError):
    pass


@dataclass(frozen=True)
class InstructionsSnapshot:
    content: str | None
    revision: str


def decode_markdown(data: bytes) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise InstructionsError("too_large")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise InstructionsError("encoding") from error
    if not content.strip() or "\x00" in content:
        raise InstructionsError("invalid_file")
    return content


def _read_snapshot() -> InstructionsSnapshot:
    path = INSTRUCTIONS_FILE
    if path.is_symlink():
        raise InstructionsError("storage")
    try:
        with path.open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return InstructionsSnapshot(None, "missing")
    return InstructionsSnapshot(decode_markdown(data), hashlib.sha256(data).hexdigest())


async def read_snapshot() -> InstructionsSnapshot:
    return await asyncio.to_thread(_read_snapshot)


def _write(content: str | None, revision: str) -> InstructionsSnapshot:
    current = _read_snapshot()
    if current.revision != revision:
        raise InstructionsError("stale")
    path = INSTRUCTIONS_FILE
    if content is None:
        path.unlink(missing_ok=True)
        return InstructionsSnapshot(None, "missing")
    data = content.encode("utf-8")
    decode_markdown(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
            temporary = Path(target.name)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return InstructionsSnapshot(content, hashlib.sha256(data).hexdigest())


async def save(content: str | None, revision: str) -> InstructionsSnapshot:
    async with _SAVE_LOCK:
        task = asyncio.create_task(asyncio.to_thread(_write, content, revision))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Hold the lock until the atomic disk write actually finishes.
            await task
            raise


async def is_admin(user_id: int) -> bool:
    if user_id in app_config.owners:
        return True
    user = await database.get_user_by_id(user_id)
    return bool(user and user.is_bot_global_admin and not user.is_blocked)


async def load_for_admin_dm(deps) -> str | None:
    message = getattr(deps, "message", None)
    chat = getattr(message, "chat", None)
    user_id = getattr(deps, "user_id", None)
    if (
        chat is None
        or chat.type != ChatType.PRIVATE
        or chat.id != user_id
        or getattr(deps, "chat_id", None) != chat.id
        or getattr(message, "business_connection_id", None)
        or getattr(message, "guest_query_id", None)
        or not await is_admin(user_id)
    ):
        return None
    try:
        snapshot = await read_snapshot()
    except (OSError, InstructionsError) as error:
        logger.warning("Admin DM instructions unavailable: {}", type(error).__name__)
        return None
    return snapshot.content
