"""Persistent Markdown files, one live selection and its application scope."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pyrogram.enums import ChatType

from waku import database
from waku.config import app_config
from waku.logger import logger

# Also serves as the source for automatic migration from the single-file menu.
INSTRUCTIONS_FILE = Path("data/admin_dm/AGENTS.md")
MAX_FILE_BYTES = 32 * 1024
MAX_FILES = 100
_SAVE_LOCK = asyncio.Lock()
_FILE_ID = re.compile(r"^[a-f0-9]{16}$")
SCOPES = ("admin", "dm", "global")


class InstructionsError(ValueError):
    pass


@dataclass(frozen=True)
class MarkdownFile:
    id: str
    name: str


@dataclass(frozen=True)
class LibrarySnapshot:
    files: tuple[MarkdownFile, ...] = ()
    selected: str | None = None
    scope: str = "admin"

    @property
    def active_name(self) -> str | None:
        return next(
            (file.name for file in self.files if file.id == self.selected), None
        )


def decode_markdown(data: bytes) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise InstructionsError("too_large")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise InstructionsError("encoding") from error
    if not content.strip():
        raise InstructionsError("empty_file")
    if "\x00" in content:
        raise InstructionsError("invalid_content")
    return content


def validate_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or len(name.encode("utf-8")) > 255
        or Path(name).suffix.lower() != ".md"
    ):
        raise InstructionsError("invalid_file")
    return name


def _blob(file_id: str) -> Path:
    if not _FILE_ID.fullmatch(file_id):
        raise InstructionsError("storage")
    return INSTRUCTIONS_FILE.parent / "files" / (file_id + ".md")


def _atomic_write(path: Path, data: bytes):
    if path.is_symlink():
        raise InstructionsError("storage")
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


def _write_library(library: LibrarySnapshot):
    data = {
        "version": 1,
        "selected": library.selected,
        "scope": library.scope,
        "files": [{"id": file.id, "name": file.name} for file in library.files],
    }
    _atomic_write(
        INSTRUCTIONS_FILE.parent / "library.json",
        json.dumps(data, ensure_ascii=False).encode("utf-8"),
    )


def _read_content(path: Path) -> str:
    if path.is_symlink():
        raise InstructionsError("storage")
    with path.open("rb") as source:
        return decode_markdown(source.read(MAX_FILE_BYTES + 1))


def _read_library() -> LibrarySnapshot:
    path = INSTRUCTIONS_FILE.parent / "library.json"
    if path.is_symlink():
        raise InstructionsError("storage")
    if not path.exists():
        if not INSTRUCTIONS_FILE.exists():
            return LibrarySnapshot()
        content = _read_content(INSTRUCTIONS_FILE)
        file = MarkdownFile(secrets.token_hex(8), "AGENTS.md")
        _atomic_write(_blob(file.id), content.encode("utf-8"))
        library = LibrarySnapshot((file,), file.id)
        _write_library(library)
        # Publish the new state before removing the legacy file.
        try:
            INSTRUCTIONS_FILE.unlink()
        except OSError as error:
            logger.warning("Legacy admin MD cleanup failed: {}", type(error).__name__)
        return library
    try:
        with path.open("rb") as source:
            payload = json.loads(source.read(128 * 1024))
        rows = payload["files"]
        if (
            payload.get("version") != 1
            or not isinstance(rows, list)
            or len(rows) > MAX_FILES
        ):
            raise ValueError("Invalid library")
        files = tuple(
            MarkdownFile(row["id"], validate_name(row["name"])) for row in rows
        )
        ids = {file.id for file in files}
        if len(ids) != len(files) or len({file.name for file in files}) != len(files):
            raise ValueError("Duplicate file")
        for file in files:
            _blob(file.id)
        selected = payload.get("selected")
        if selected is not None and selected not in ids:
            raise ValueError("Invalid selection")
        scope = payload.get("scope", "admin")
        if scope not in SCOPES:
            raise ValueError("Invalid scope")
        return LibrarySnapshot(files, selected, scope)
    except (ValueError, TypeError, KeyError) as error:
        raise InstructionsError("storage") from error


async def _locked(operation, *args):
    async with _SAVE_LOCK:
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise


async def list_files() -> LibrarySnapshot:
    return await _locked(_read_library)


def _add_file(name: str, content: str):
    name = validate_name(name)
    data = content.encode("utf-8")
    decode_markdown(data)
    library = _read_library()
    existing = next((file for file in library.files if file.name == name), None)
    if existing is None and len(library.files) >= MAX_FILES:
        raise InstructionsError("too_many_files")
    file = existing or MarkdownFile(secrets.token_hex(8), name)
    _atomic_write(_blob(file.id), data)
    updated = LibrarySnapshot(
        library.files if existing else (*library.files, file),
        library.selected,
        library.scope,
    )
    if existing is None:
        _write_library(updated)
    return updated, existing is not None


async def add_file(name: str, content: str):
    return await _locked(_add_file, name, content)


def _toggle(file_id: str):
    library = _read_library()
    if not any(file.id == file_id for file in library.files):
        raise InstructionsError("stale")
    selected = None if library.selected == file_id else file_id
    updated = LibrarySnapshot(library.files, selected, library.scope)
    _write_library(updated)
    return updated


async def toggle_file(file_id: str):
    return await _locked(_toggle, file_id)


def _delete_selected(expected: str | None):
    if expected is None:
        raise InstructionsError("choose_file")
    library = _read_library()
    if library.selected != expected:
        raise InstructionsError("stale")
    updated = LibrarySnapshot(
        tuple(file for file in library.files if file.id != expected),
        scope=library.scope,
    )
    _write_library(updated)
    try:
        _blob(expected).unlink(missing_ok=True)
    except OSError as error:
        # Removed from the library already; an orphan is never loaded by the bot.
        logger.warning("Deleted admin MD cleanup failed: {}", type(error).__name__)
    return updated


async def delete_selected(expected: str | None):
    return await _locked(_delete_selected, expected)


def _cycle_scope(expected: str):
    library = _read_library()
    if library.scope != expected:
        raise InstructionsError("stale")
    scope = SCOPES[(SCOPES.index(library.scope) + 1) % len(SCOPES)]
    updated = LibrarySnapshot(library.files, library.selected, scope)
    _write_library(updated)
    return updated


async def cycle_scope(expected: str):
    return await _locked(_cycle_scope, expected)


def _selected_content(selected: str, scope: str):
    library = _read_library()
    if (library.selected, library.scope) != (selected, scope):
        return None
    return _read_content(_blob(selected))


async def is_admin(user_id: int) -> bool:
    if user_id in app_config.owners:
        return True
    user = await database.get_user_by_id(user_id)
    return bool(user and user.is_bot_global_admin and not user.is_blocked)


async def _scope_applies(scope: str, deps) -> bool:
    message = getattr(deps, "message", None)
    if message is None and getattr(deps, "chat", None) is not None:
        message = deps  # Business and Guest agents use the native Message as deps.
    if message is None:
        return False
    if hasattr(message, "guild") and getattr(message, "channel", None) is not None:
        return scope == "global" or (scope == "dm" and message.guild is None)
    chat = getattr(message, "chat", None)
    if chat is None:
        return False
    if scope == "global":
        return chat.type in (
            ChatType.PRIVATE,
            ChatType.BOT,
            ChatType.GROUP,
            ChatType.SUPERGROUP,
            ChatType.CHANNEL,
        )
    if scope == "dm":
        return not getattr(message, "business_connection_id", None) and chat.type in (
            ChatType.PRIVATE,
            ChatType.BOT,
        )
    user_id = getattr(deps, "user_id", None)
    return not (
        chat.type != ChatType.PRIVATE
        or chat.id != user_id
        or getattr(deps, "chat_id", None) != chat.id
        or getattr(message, "business_connection_id", None)
        or getattr(message, "guest_query_id", None)
        or not await is_admin(user_id)
    )


async def load_for_context(deps) -> str | None:
    try:
        library = await list_files()
        if not library.selected or not await _scope_applies(library.scope, deps):
            return None
        return await _locked(_selected_content, library.selected, library.scope)
    except (OSError, InstructionsError) as error:
        logger.warning("Markdown context unavailable: {}", type(error).__name__)
        return None
