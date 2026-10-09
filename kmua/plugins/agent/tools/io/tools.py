"""The six IO tool functions exposed to the model."""

from __future__ import annotations

import builtins
from typing import Any

from ddgs import DDGS
from pydantic_ai import ModelRetry, RunContext, ToolReturn
from pydantic_ai.common_tools.duckduckgo import DuckDuckGoSearchTool

from kmua.config import app_config
from kmua.logger import logger
from kmua.plugins.agent.localization import tr

from .. import bot, chat, code_repo, datatype, workspace
from .content import _read_content
from .media import (
    MediaPayload,
    _media_tool_return,
    _native_image_return,
    _native_media_return,
    _tme_message_parts,
    _transcribe_media_tool_return,
)
from .protocols import _require, _split_target
from .targets import _sandbox_target, _session_key, _write_persisted, read_bytes


async def read(
    ctx: RunContext[datatype.ContextDeps],
    path: str,
    start_line: int = 1,
    max_lines: int = 200,
) -> str | ToolReturn:
    """Read text or return binary content.

    Protocols:
    - kmua://kmua/plugins/x.py — a file from the bot's own codebase
    - work://notes/hello.html — a file from this chat's workspace
    - persist://report.txt — a file persisted for this chat
    - chat://media/123 — media with message id 123 in this chat
    - https://t.me/MoreACG/27411 — a public t.me message or its media
    - chat://info — information about the current group
    - chat://history — the latest messages of this group; optional query:
      ?before=<id> / ?after=<id> anchor N messages around an id (count=N,
      default 50), ?from_id=<a>&to_id=<b> for an inclusive id range, or
      ?reply_chain_of=<id> for the full reply chain of message <id> (oldest
      ancestor first, <id> last)
    - https://example.com — a web page or binary resource

    Text targets support start_line/max_lines paging (1-indexed; max_lines up
    to 1500).
    """
    if max_lines < 1 or max_lines > 1500:
        raise ModelRetry(tr("tool_max_lines_must_be_between_1_and_1500"))
    if start_line < 1:
        raise ModelRetry(tr("tool_start_line_must_be_1"))
    try:
        protocol, rest = _split_target(path)
    except ValueError:
        protocol, rest = "", ""

    is_media_target = (protocol == "chat://" and rest.startswith("/media/")) or (
        protocol == "http" and _tme_message_parts(path) is not None
    )
    if is_media_target:
        try:
            native = await _native_image_return(
                ctx,
                path,
                path,
                start_line=start_line,
                max_lines=max_lines,
            )
            if native is None:
                native = await _native_media_return(
                    ctx,
                    path,
                    path,
                    start_line=start_line,
                    max_lines=max_lines,
                )
        except Exception as e:
            logger.error(f"read error for {path}: {e}")
            return tr("error", p0=e)
        if native is not None:
            return native

    try:
        result = await _read_content(ctx, path, start_line, max_lines)
        if isinstance(result, MediaPayload):
            if app_config.agent_multimodal_mode == "transcribe":
                return await _transcribe_media_tool_return(
                    ctx,
                    label=result.label,
                    media_type=result.media_type,
                    data=result.data,
                )
            return _media_tool_return(
                ctx,
                label=result.label,
                media_type=result.media_type,
                data=result.data,
            )
        return result
    except Exception as e:
        logger.error(f"read error for {path}: {e}")
        return tr("error", p0=e)


async def write(
    ctx: RunContext[datatype.ContextDeps],
    path: str,
    content: str | bytes | None = None,
) -> str:
    """Write content to a target. The path is the destination; content is the payload.

    Protocols:
    - work://notes/hello.html — save content as a file in the chat's workspace
    - persist://report.txt    — persist a file for this chat: it is sent as a
        document to the chat and recorded; later runs can read, list, delete
        and overwrite it by name.
    - memory://               — store content as a fact about this group in its
        long-term memory

    content can be plain text or a reference to another target whose content
    is used as the payload — e.g. work://notes/hello.html (workspace file) or
    kmua://kmua/services/wechat.py (codebase file), handy for
    copying files between targets. A chat://media/<message_id> reference
    downloads a message's media from this chat, and an https://t.me/... link
    from a public Telegram chat. Binary payloads must go
    through a reference (work://, persist://, chat://media, a t.me link).

    Keep content under 5 MB. Prefer edit when modifying an existing
    workspace file.
    """
    try:
        protocol, rest = _split_target(path)
    except ValueError as e:
        return tr("error", p0=e)
    denied = _require(protocol, ctx.deps)
    if denied:
        return denied
    if isinstance(content, str):
        try:
            _split_target(content)
        except ValueError:
            pass  # plain text
        else:
            # Protocol reference: resolve the referenced content as raw bytes
            # (lossless for binary payloads; text round-tripping would
            # corrupt non-UTF-8 bytes).
            try:
                content = await read_bytes(content, ctx)
            except Exception as e:
                logger.error(f"write error for {path}: resolving {content}: {e}")
                return tr("error", p0=e)
    if content is None:
        return tr("target_content_required", p0=path)
    try:
        if protocol == "memory://":
            text = content if isinstance(content, str) else content.decode("utf-8")
            return await chat.update_group_memory(ctx, text)
        if protocol == "persist://":
            return await _write_persisted(ctx, rest, content)
        if protocol == "sandbox://":
            target = _sandbox_target(_session_key(ctx), rest)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                content.encode("utf-8") if isinstance(content, str) else content
            )
            return tr(
                "workspace_written",
                p0=len(content)
                if isinstance(content, bytes)
                else len(content.encode("utf-8")),
                p1=path,
            )
        if protocol != "work://":
            return tr("target_not_writable", p0=path)
        await workspace.write_file(_session_key(ctx), rest, content)
    except Exception as e:
        logger.error(f"write error for {path}: {e}")
        return tr("error", p0=e)
    size = len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))
    return tr("workspace_written_hint", p0=size, p1=path)


async def edit(
    ctx: RunContext[datatype.ContextDeps],
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    line: int | None = None,
) -> str:
    """Edit a file in the chat's workspace (work:// only).

    Replaces old_text with new_text in the file. Give enough surrounding text
    to make old_text unique. Pass replace_all=True to change every occurrence.
    Pass line (1-indexed) to restrict the edit to a single line, handy for
    long files.
    """
    try:
        protocol, rest = _split_target(path)
    except ValueError as e:
        return tr("error", p0=e)
    if protocol != "work://":
        return tr("target_not_editable", p0=path)
    denied = _require(protocol, ctx.deps)
    if denied:
        return denied
    try:
        await workspace.edit_file(
            _session_key(ctx), rest, old_text, new_text, replace_all, line
        )
    except Exception as e:
        logger.error(f"edit error for {path}: {e}")
        return tr("error", p0=e)
    return tr("workspace_edited", p0=path)


async def list(ctx: RunContext[datatype.ContextDeps], path: str = "work://") -> str:
    """List files and directories.

    Protocols:
    - work://             — agent workspace (default)
    - persist://          — files persisted for this chat
    - kmua://             — bot's codebase
    """
    try:
        protocol, rest = _split_target(path)
    except ValueError as e:
        return tr("error", p0=e)
    denied = _require(protocol, ctx.deps)
    if denied:
        return denied
    try:
        if protocol == "persist://":
            from kmua.database import persistent_file as pf_db

            files = await pf_db.list_persistent_files(ctx.deps.chat_id)
            _ = rest
            if not files:
                return tr("persisted_files_empty")
            lines = [tr("tool_name_size_updated")]
            for f in files:
                kb = (f.file_size or 0) / 1024
                lines.append(f"{f.name} | {kb:.1f} KB | {f.updated_at:%Y-%m-%d %H:%M}")
            return "\n".join(lines)
        if protocol == "sandbox://":
            root = _sandbox_target(_session_key(ctx), rest)
            if not root.exists():
                return tr("path_missing", p0=path)
            lines = [tr("tool_name_size_type")]
            for item in sorted(root.iterdir(), key=lambda p: p.name):
                kind = "dir" if item.is_dir() else str(item.stat().st_size)
                lines.append(f"{item.name} | {kind}")
            return "\n".join(lines)
        if protocol == "kmua://":
            entries = await code_repo.list_files(rest, include_dirs=True)
            if not entries:
                return tr("path_missing", p0=path)
            lines = [tr("tool_name_size_type")]
            for e in entries:
                kind = "dir" if e.get("is_dir") else str(e.get("size", "?"))
                lines.append(f"{e.get('name', '?')} | {kind}")
            return "\n".join(lines)
        entries = await workspace.list_files(_session_key(ctx), rest)
        if not entries:
            return tr("workspace_empty", p0=path)
        lines = [tr("tool_name_size_type")]
        for e in entries:
            kind = "dir" if e["is_dir"] else str(e["size"])
            lines.append(f"{e['name']} | {kind}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"list error for {path}: {e}")
        return tr("error", p0=e)


def _format_search_results(query: str, results: builtins.list[Any]) -> str:
    """Render search results; results are dicts with title/href/body keys."""
    if not results:
        return tr("search_empty", p0=query)
    lines = [tr("tool_search_results_for_p0_p1", p0=query, p1=len(results))]
    for i, r in enumerate(results, 1):
        title = r.get("title") or ""
        url = r.get("href") or ""
        snippet = r.get("body") or ""
        lines.append(f"\n{i}. {title}\n   {url}\n   {snippet}")
    return "\n".join(lines)


async def _search_web(query: str, max_results: int) -> str:
    results = await DuckDuckGoSearchTool(
        DDGS(), max_results=min(max_results, 10)
    ).__call__(query)
    return _format_search_results(query, results)


async def search(
    ctx: RunContext[datatype.ContextDeps],
    query: str,
    path: str = "kmua://",
    max_results: int = 20,
    use_regex: bool = False,
    case_sensitive: bool = True,
) -> str:
    """Search text across files, web, chat messages or group memory.

    Protocols:
    - kmua://           — the bot's own codebase
    - work://           — the agent workspace
    - web://            — search on Internet
    - chat://           — messages in the current group
    - memory://         — the group's long-term memory (semantic search)

    query must be at least 2 characters; max_results 1-50.
    """
    if not query or len(query) < 2:
        raise ModelRetry(tr("tool_query_must_be_at_least_2_characters"))
    if max_results < 1 or max_results > 50:
        raise ModelRetry(tr("tool_max_results_must_be_between_1_and_50"))
    try:
        protocol, rest = _split_target(path)
    except ValueError as e:
        return tr("error", p0=e)
    denied = _require(protocol, ctx.deps)
    if denied:
        return denied
    try:
        if protocol == "kmua://":
            results = await code_repo.search_in_files(
                query,
                max_results=max_results,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
            )
            if not results:
                return tr("search_empty", p0=query)
            parts = [
                tr("tool_search_results_for_p0_p1_files", p0=query, p1=len(results))
            ]
            for i, r in enumerate(results, 1):
                parts.append(
                    tr(
                        "tool_p0_p1_p2_matches",
                        p0=i,
                        p1=r.get("file", "unknown"),
                        p2=r.get("total_matches", 0),
                    )
                )
                for m in r.get("matches", [])[:3]:
                    parts.append(
                        tr(
                            "tool_line_p0_p1",
                            p0=m.get("line", 0),
                            p1=m.get("content", ""),
                        )
                    )
            return "\n".join(parts)
        if protocol == "work://":
            results = await workspace.search_files(
                _session_key(ctx),
                query,
                path=rest,
                max_results=max_results,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
            )
            if not results:
                return tr("search_empty", p0=query)
            parts = [
                tr("tool_search_results_for_p0_p1_files", p0=query, p1=len(results))
            ]
            for i, r in enumerate(results, 1):
                parts.append(
                    tr(
                        "tool_p0_p1_p2_matches_shown",
                        p0=i,
                        p1=r["file"],
                        p2=len(r["matches"]),
                    )
                )
                for m in r["matches"]:
                    parts.append(tr("tool_line_p0_p1", p0=m["line"], p1=m["content"]))
            return "\n".join(parts)
        if protocol == "web://":
            return await _search_web(query, max_results)
        if protocol == "chat://":
            return await bot.search_messages(ctx, query, count=max_results)
        memories = await chat.search_group_memory(ctx, query)
        if not memories:
            return tr("memories_empty")
        return "\n".join(f"- {m}" for m in memories)
    except Exception as e:
        logger.error(f"search error: {e}")
        return tr("error", p0=e)


async def delete(
    ctx: RunContext[datatype.ContextDeps],
    path: str,
) -> str:
    """Delete a file from the workspace or the persisted set."""
    try:
        protocol, rest = _split_target(path)
    except ValueError as e:
        return tr("error", p0=e)
    denied = _require(protocol, ctx.deps)
    if denied:
        return denied
    try:
        if protocol == "persist://":
            from kmua.database import persistent_file as pf_db

            name = rest.lstrip("/")
            deleted = await pf_db.delete_persistent_file(ctx.deps.chat_id, name)
            if not deleted:
                return tr("error_persisted_file_missing", p0=repr(name))
            return tr("persisted_file_removed", p0=name)
        if protocol == "sandbox://":
            target = _sandbox_target(_session_key(ctx), rest)
            target.unlink(missing_ok=True)
            return tr("workspace_deleted", p0=path)
        if protocol != "work://":
            return tr("target_not_deletable", p0=path)
        await workspace.delete_file(_session_key(ctx), rest)
    except Exception as e:
        logger.error(f"delete error for {path}: {e}")
        return tr("error", p0=e)
    return tr("workspace_deleted", p0=path)
