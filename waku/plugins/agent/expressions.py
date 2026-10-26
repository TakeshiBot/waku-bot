"""Periodic expression hints for ordinary Telegram AI turns."""

from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.tools import ToolDefinition
from pyrogram.enums import ChatType, MessageMediaType

from waku import common
from waku.common.utils import get_reply_target
from waku.config import app_config

from . import datatype, state
from .expression_policy import expression_settings
from .localization import tr
from .tools.prepare import prepare_sticker_tools


def requires_media_text_reply(deps: datatype.ContextDeps) -> bool:
    """Captionless media sent directly to the bot still deserves a spoken reply."""
    message = deps.message
    if (
        not deps.periodic_expressions_enabled
        or message is None
        or getattr(message, "business_connection_id", None)
        or getattr(message, "guest_query_id", None)
        or getattr(message, "media", None)
        not in (MessageMediaType.PHOTO, MessageMediaType.STICKER)
        or common.message_plain_text(message).strip()
        or deps.sent_texts
        or deps.image_delivered
        or deps.mirror_dispatched
    ):
        return False
    chat = getattr(message, "chat", None)
    if chat is None:
        return False
    if chat.type == ChatType.PRIVATE:
        return True
    if not getattr(message, "reply_to_message", None):
        return False
    reply = get_reply_target(message)
    bot_id = getattr(getattr(deps.client, "me", None), "id", None)
    return bool(
        bot_id
        and reply
        and getattr(getattr(reply, "chat", None), "id", None) == deps.chat_id
        and getattr(getattr(reply, "from_user", None), "id", None) == bot_id
    )


def media_reply_instructions(ctx: RunContext[datatype.ContextDeps]) -> str:
    if requires_media_text_reply(ctx.deps):
        return tr("media_text_reply_required", locale=ctx.deps.locale or None)
    return ""


def validate_media_reply(ctx: RunContext[datatype.ContextDeps], output: Any) -> Any:
    if isinstance(output, datatype.EndTurn) and requires_media_text_reply(ctx.deps):
        raise ModelRetry(tr("media_text_reply_required", locale=ctx.deps.locale or None))
    return output


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
    _, reactions_enabled = await expression_settings(deps.message, deps.chat_id)
    if (
        app_config.agent_periodic_sticker_interval > 0
        and "send_sticker" not in deps.tools_called_this_turn
        and await prepare_sticker_tools(ctx, ToolDefinition(name="send_sticker"))
        is not None
    ):
        hints.append(tr("natural_sticker_hint", locale=deps.locale or None))
        turns = await common.memstore.get(
            state.periodic_sticker_counter_key(deps.chat_id, deps.user_id), 0
        )
        if _due(turns, app_config.agent_periodic_sticker_interval):
            hints.append(tr("periodic_sticker_hint", locale=deps.locale or None))
    if (
        app_config.agent_periodic_reaction_interval > 0
        and reactions_enabled
        and "tg.sendReaction" not in deps.tools_called_this_turn
    ):
        hints.append(tr("natural_reaction_hint", locale=deps.locale or None))
        turns = await common.memstore.get(
            state.periodic_reaction_counter_key(deps.chat_id, deps.user_id), 0
        )
        if _due(turns, app_config.agent_periodic_reaction_interval):
            hints.append(tr("periodic_reaction_hint", locale=deps.locale or None))
    return "\n\n".join(hints)
