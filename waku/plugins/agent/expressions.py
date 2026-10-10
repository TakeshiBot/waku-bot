"""Periodic expression hints for ordinary Telegram AI turns."""

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition

from waku import common
from waku.config import app_config

from . import datatype, state
from .localization import tr
from .tools.prepare import prepare_sticker_tools


def _due(completed_turns: int, interval: int) -> bool:
    return interval > 0 and (completed_turns + 1) % interval == 0


async def periodic_expression_instructions(
    ctx: RunContext[datatype.ContextDeps],
) -> str:
    """Suggest expressions once per turn, only while their tools are usable.

    Counters are advanced by the message handler after its run. Reading here
    does not advance them again on every model request within that same run.
    Ask callbacks, followups and Business runs do not opt into these hints.
    """
    deps = ctx.deps
    if (
        not deps.periodic_expressions_enabled
        or deps.message is None
        or deps.chat_id is None
        or deps.user_id is None
        or getattr(deps.message, "business_connection_id", None)
    ):
        return ""
    hints = []
    if (
        app_config.agent_periodic_sticker_interval > 0
        and "send_sticker" not in deps.tools_called_this_turn
        and await prepare_sticker_tools(ctx, ToolDefinition(name="send_sticker"))
        is not None
    ):
        turns = await common.memstore.get(
            state.periodic_sticker_counter_key(deps.chat_id, deps.user_id), 0
        )
        if _due(turns, app_config.agent_periodic_sticker_interval):
            hints.append(tr("periodic_sticker_hint", locale=deps.locale or None))
    if (
        app_config.agent_periodic_reaction_interval > 0
        and "tg.sendReaction" not in deps.tools_called_this_turn
    ):
        turns = await common.memstore.get(
            state.periodic_reaction_counter_key(deps.chat_id, deps.user_id), 0
        )
        if _due(turns, app_config.agent_periodic_reaction_interval):
            hints.append(tr("periodic_reaction_hint", locale=deps.locale or None))
    return "\n\n".join(hints)
