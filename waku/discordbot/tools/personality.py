"""Group personality reactions that only ignore the current sender."""

import asyncio

from pydantic_ai import RunContext

from waku.config import app_config
from waku.logger import logger
from waku.services import personality

from ..models import DiscordContextDeps
from ..permissions import _is_discord_user_bot_admin


async def prepare_personality(ctx, definition):
    message = ctx.deps.message
    if message.guild is not None and not message.author.bot:
        return definition
    return None


async def _members(message):
    guild = message.guild
    member = await asyncio.wait_for(guild.fetch_member(message.author.id), 10)
    rights = member.guild_permissions
    if (
        member.id != message.author.id
        or member.bot
        or member.id == guild.owner_id
        or _is_discord_user_bot_admin(member)
        or any(
            getattr(rights, flag, False)
            for flag in (
                "administrator",
                "manage_guild",
                "moderate_members",
                "kick_members",
                "ban_members",
            )
        )
    ):
        raise ValueError("The current member is protected.")
    return member


async def react_to_disrespect(
    ctx: RunContext[DiscordContextDeps],
    reason: str,
) -> dict:
    """React only to this sender's direct group harassment, based on your judgment.

    One call per offensive message. Only briefly ignore this sender in the server.
    Never timeout, mute, kick or ban. No target parameter or DM use.
    Ordinary disagreement, quotation or friendly teasing is not harassment.
    """
    message = ctx.deps.message
    if (
        not app_config.discord_enabled
        or message.guild is None
        or message.author.bot
        or getattr(message, "webhook_id", None)
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        return {
            "status": "denied",
            "reason": "A real group sender and your own factual reason are required.",
        }
    try:
        await _members(message)

        async def apply(action, seconds):
            if action != "ignore":
                return {
                    "status": "denied",
                    "reason": "Discord personality reactions only support ignoring the sender.",
                }
            try:
                await _members(message)
            except Exception:
                return {
                    "status": "denied",
                    "reason": "Current member/roles/permissions could not be verified.",
                }
            return {"status": "applied"}

        ctx.deps.side_effects_started = True
        return await personality.react(
            "discord", message.guild.id, message.author.id, message.id, apply
        )
    except Exception as error:
        logger.debug("Discord personality reaction denied: {}", type(error).__name__)
        return {
            "status": "denied",
            "reason": "The member is protected or could not be verified.",
        }
