"""Byte/line access to sandbox, workspace and persisted-file targets."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from pydantic_ai import RunContext

from kmua.common.safe_http import UnsafeUrlError, safe_download_bytes
from kmua.config import app_config
from kmua.logger import logger
from kmua.plugins.agent.localization import tr

from .. import code_repo, datatype, workspace
from .media import _download_chat_media, _download_tme_media, _tme_message_parts
from .protocols import _require, _split_target


def _session_key(ctx: RunContext[datatype.ContextDeps]) -> str:
    """The workspace session key: the chat id for groups, the user id for
    private chats — matching the agent session granularity. Each key owns a
    dedicated workspace database."""
    deps = ctx.deps
    return str(deps.chat_id) if deps.chat_id != deps.user_id else str(deps.user_id)


def _safe_sandbox_path(rel: str) -> Path:
    """Resolve a sandbox-relative path, rejecting escapes."""
    p = Path(rel.lstrip("/"))
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(tr("tool_path_escapes_the_sandbox_p0", p0=rel))
    return p


def _sandbox_target(session_key: str, rel: str) -> Path:
    """The real path for a sandbox:// target, refusing symlink traversal.

    Sandbox commands may create symlinks pointing outside the sandbox;
    following one would let the io tools read or write arbitrary
    bot-readable files. Every path component must be a real directory or
    file, never a symlink.
    """
    from kmua.services import sandbox

    root = sandbox.session_shell_dir(session_key)
    rel_path = _safe_sandbox_path(rel)
    probe = root
    for part in rel_path.parts:
        probe = probe / part
        if probe.is_symlink():
            raise ValueError(tr("tool_sandbox_paths_must_not_use_symlinks_p0", p0=rel))
    return root / rel_path


async def read_bytes(path: str, ctx: RunContext[datatype.ContextDeps]) -> bytes:
    """Read a target as raw bytes (persist payloads and binary copies)."""
    protocol, rest = _split_target(path)
    denied = _require(protocol, ctx.deps)
    if denied:
        raise ValueError(denied)
    if protocol == "work://":
        return await workspace.read_file_bytes(_session_key(ctx), rest)
    if protocol == "sandbox://":
        target = _sandbox_target(_session_key(ctx), rest)
        return target.read_bytes()
    if protocol == "kmua://":
        agent = await code_repo.get_code_agentfs()
        if agent is None:
            raise ValueError(tr("tool_code_repository_not_initialized"))
        raw = await agent.fs.read_file(rest, encoding=None)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return raw
    if protocol == "persist://":
        return await _download_persisted(ctx, rest)
    if protocol == "chat://" and rest.startswith("/media/"):
        return await _download_chat_media(ctx, rest)
    if protocol == "http":
        if _tme_message_parts(rest) is not None:
            # Telegram media is downloaded through the bot client, not the
            # plain HTTP path (t.me pages are HTML, the files live in MTProto).
            return await _download_tme_media(ctx, rest)
        try:
            return await safe_download_bytes(
                rest, max_bytes=app_config.agent_download_max_bytes
            )
        except (UnsafeUrlError, Exception) as e:
            raise ValueError(tr("tool_download_failed_p0", p0=e)) from None
    raise ValueError(tr("tool_target_p0_is_not_readable", p0=path))


async def _write_persisted(
    ctx: RunContext[datatype.ContextDeps], name: str, content: str | bytes
) -> str:
    """Persist a file: send it to the chat as a document and record it.

    content may be plain text or a protocol reference (work://, kmua://,
    persist://) whose bytes are persisted; binary payloads go through
    references so they are not round-tripped through text.
    """
    name = name.lstrip("/")
    if not name or "/" in name or len(name) > 256:
        return tr("filename_invalid")
    data = content.encode("utf-8") if isinstance(content, str) else content
    if not data:
        return tr("content_empty")
    if len(data) > workspace.MAX_WORKSPACE_FILE_SIZE:
        return tr("file_size_limit", p0=workspace.MAX_WORKSPACE_FILE_SIZE)
    client = ctx.deps.client

    try:
        sent = await client.send_document(
            ctx.deps.chat_id,
            document=BytesIO(data),
            file_name=name,
            caption=name,
        )
    except Exception as e:
        logger.error(f"persist write failed for {name!r}: {e}")
        return tr("file_send_failed")
    assert sent is not None, tr("tool_send_document_returned_none")
    assert sent.document is not None, tr("tool_sent_message_has_no_document")
    doc = sent.document
    from kmua.database import persistent_file as pf_db

    try:
        await pf_db.upsert_persistent_file(
            chat_id=ctx.deps.chat_id,
            name=name,
            description=None,
            tg_message_id=sent.id,
            file_id=doc.file_id,
            file_unique_id=doc.file_unique_id,
            file_name=doc.file_name,
            mime_type=doc.mime_type,
            file_size=doc.file_size,
        )
    except Exception as e:
        # The document was already sent; a failed record would leave an
        # orphan file in chat history, so remove it again.
        logger.error(f"persist record failed for {name!r}: {e}")
        try:
            await client.delete_messages(ctx.deps.chat_id, sent.id)
        except Exception:
            pass
        return tr("file_record_failed")
    logger.info(
        f"agent persisted file {name!r} to chat {ctx.deps.chat_id} (message {sent.id})"
    )
    return tr("file_persisted", p0=name)


async def _download_persisted(
    ctx: RunContext[datatype.ContextDeps], name: str
) -> bytes:
    """Download a persisted file's bytes from chat history."""
    from kmua.database import persistent_file as pf_db

    name = name.lstrip("/")
    record = await pf_db.get_persistent_file(ctx.deps.chat_id, name)
    if record is None:
        raise ValueError(tr("persisted_file_missing", p0=repr(name)))
    from pyrogram.file_id import FileId

    timeout = app_config.agent_download_timeout or None

    async def _collect():
        file_id = FileId.decode(record.file_id)
        if file_id is None:
            raise ValueError(tr("tool_the_stored_file_id_is_invalid"))
        chunks = [chunk async for chunk in ctx.deps.client.get_file(file_id)]
        return b"".join(chunks)

    return await asyncio.wait_for(_collect(), timeout=timeout)
