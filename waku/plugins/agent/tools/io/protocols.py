"""Protocol parsing and per-protocol availability.

The single source of truth for which protocols each IO tool may use: the
`_require` gate enforces it at call time and the prepare functions in
`prepares.py` mirror it into tool visibility.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent.localization import tr

from .. import code_repo, datatype

_PROTOCOLS = (
    "waku://",
    "work://",
    "sandbox://",
    "persist://",
    "chat://",
    "memory://",
    "web://",
)


def _split_target(path: str) -> tuple[str, str]:
    """Return (protocol, rest) where rest is an agentfs path starting with '/'.

    Raises ValueError for unsupported targets.
    """
    if path.startswith("kmua://"):
        path = "waku://" + path[len("kmua://") :]
    for proto in _PROTOCOLS:
        if path.startswith(proto):
            rest = path[len(proto) :]
            if not rest.startswith("/"):
                rest = "/" + rest
            if proto == "waku://" and (rest == "/kmua" or rest.startswith("/kmua/")):
                rest = "/waku" + rest[len("/kmua") :]
            return proto, rest
    if path.startswith(("http://", "https://")):
        return "http", path
    raise ValueError(tr("target_unsupported", p0=path))


def _is_group_chat(deps: datatype.ContextDeps) -> bool:
    return deps.chat_id != deps.user_id


def _web_search_enabled() -> bool:
    return "websearch" in app_config.agent_extra_tools


def _web_fetch_enabled() -> bool:
    return "webfetch" in app_config.agent_extra_tools


def _waku_enabled() -> bool:
    return bool(app_config.agent_code_awareness) and (
        code_repo._code_agentfs is not None
    )


async def _sandbox_enabled(ctx: RunContext[datatype.ContextDeps]) -> bool:
    if not app_config.agent_shell_enabled:
        return False
    from ..shell_tool import _shell_allowed_in_chat

    if not _shell_allowed_in_chat(ctx.deps.chat_id):
        return False
    from waku.services import sandbox

    return await sandbox.landrun_available()


async def _memory_enabled(ctx: RunContext[datatype.ContextDeps]) -> bool:
    """Group memory usable in this chat: configured, initialized, enabled
    for this chat, and a group chat."""
    if not _is_group_chat(ctx.deps) or ctx.deps.powermemory is None:
        return False
    from ...agent import powermemory_ready

    if not powermemory_ready:
        return False
    try:
        from waku import database

        return (await database.get_chat_config(ctx.deps.chat_id)).group_memory_enabled
    except Exception as e:
        logger.debug(f"memory availability check failed: {e.__class__.__name__}: {e}")
        return False


def _require(protocol: str, deps: datatype.ContextDeps) -> str | None:
    """Return an error message when a protocol is disabled, else None."""
    if protocol == "waku://" and not app_config.agent_code_awareness:
        return tr("codebase_disabled")
    if protocol == "work://" and not app_config.agent_workspace_enabled:
        return tr("workspace_disabled")
    if protocol == "http" and not _web_fetch_enabled():
        return tr("web_disabled")
    if protocol == "web://" and not _web_search_enabled():
        return tr("websearch_disabled")
    if protocol == "chat://" and not _is_group_chat(deps):
        return tr("chat_protocol_group_only")
    if protocol == "memory://" and not deps.powermemory:
        return tr("memory_chat_unavailable")
    return None
