from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition

from waku.common.rich_message import message_plain_text
from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent import datatype
from waku.plugins.agent.expression_policy import expression_settings
from waku.services import image_gen
from waku.services.image_requests import explicit_image_operation
from waku.services.telegram_images import telegram_image_settings


async def prepare_manyacg_tools(
    ctx: RunContext[datatype.ContextDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """Keep public image fetching available; keyed tools need a ManyACG key."""
    # Anime images have public API fallbacks even without a ManyACG key.
    if tool_def.name == "send_anime_photo" or app_config.manyacg_api_key:
        return tool_def
    return None


async def prepare_sticker_tools(
    ctx: RunContext[datatype.ContextDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """Use only this chat's inventory, with DM enabled and Business excluded."""
    enabled, _ = await expression_settings(ctx.deps.message, ctx.deps.chat_id)
    if not enabled:
        return None
    try:
        from .. import sticker_memory, sticker_vec

        if not sticker_memory.search_ready():
            return None
        count = await sticker_vec.count(ctx.deps.chat_id)
        if count < 1:
            return None
    except Exception as e:
        logger.warning(f"Sticker availability check failed: {e}")
        return None
    return tool_def


async def prepare_image_tools(
    ctx: RunContext[datatype.ContextDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """Show the image tool when either image service is available."""
    text = message_plain_text(ctx.deps.message)
    if not any(explicit_image_operation(text, action) for action in ("generate", "edit")):
        return None
    if ctx.deps.chat_id is None or not (await telegram_image_settings(ctx.deps.chat_id))[0]:
        return None
    if (
        image_gen.image_gen_client is not None
        or image_gen.image_edit_client is not None
    ):
        return tool_def
    return None
