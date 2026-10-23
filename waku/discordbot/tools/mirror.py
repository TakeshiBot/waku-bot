"""Discord admin requests transported through the existing Telegram mirror bridge."""

from __future__ import annotations

import asyncio

import discord
from pydantic_ai import RunContext

from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent.tools.mirror import _target, mirror_instructions
from waku.services import mirror_bridge, mirror_cancel
from waku.services.mirror_commands import (
    MAX_BATCH,
    build_command,
    expand_sources,
    normalize_mode,
    normalize_options,
    validate_source,
)

from ..models import DiscordContextDeps
from ..permissions import _is_discord_user_bot_admin


def _authorized(message):
    return not message.author.bot and _is_discord_user_bot_admin(message.author)


def _transport():
    from waku.bot import client

    return client


async def prepare_discord_mirror(ctx, definition):
    if app_config.telegram_enabled and _target() and _authorized(ctx.deps.message):
        return definition
    return None


def instructions():
    # Keep the shared modes/options documentation, followed by Discord-specific IO.
    return mirror_instructions() + (
        "\nDiscord mirror overrides: use mirror_discord and cancel_discord_mirror. "
        "The configured Telegram Mirror bot handles transfers and Waku reports updates "
        "back to the original Discord channel or DM. Only configured Discord bot admins "
        "can use these tools; server administrator alone does not grant access. "
        "For a current/replied Discord attachment omit url and urls; the backend uses "
        "the verified attachment CDN URL. Do not invent links. Multiple attachments are "
        "submitted as separate tracked tasks. Explicit sources and attachments cannot be mixed. "
        "After dispatch, return an empty final answer; the acknowledgement is already sent. "
        "Do not use EndTurn (not available on Discord). Leech files stay on Telegram, "
        "with available Telegram file links returned as buttons; do not promise Discord uploads."
    )


async def mirror_discord(
    ctx: RunContext[DiscordContextDeps],
    acknowledgement: str,
    url: str | None = None,
    urls: list[str] | None = None,
    mode: str = "mirror",
    options: dict[str, str | bool] | None = None,
) -> dict:
    """Submit admin-requested mirror/leech/clone/yt-dlp transfers via Telegram.

    Supply exact user-provided sources in url OR urls (up to 20). Omit both
    for current/replied Discord attachments. acknowledgement is your brief AI
    reply before submission. All shared Mirrorz modes/options are supported.
    Do not invent URLs/options. Updates arrive asynchronously on Discord.
    """
    deps, message = ctx.deps, ctx.deps.message
    if not _authorized(message):
        return {"status": "denied", "error": "Discord bot admin permission required."}
    target = _target()
    if not app_config.telegram_enabled or not target:
        return {
            "status": "unavailable",
            "error": "Telegram mirror transport is not configured/enabled.",
        }
    if deps.mirror_attempted:
        return {
            "status": "already_requested",
            "instruction": "Do not resubmit this turn.",
        }
    if not acknowledgement.strip() or len(acknowledgement) > 1500:
        return {"status": "invalid_request", "error": "Supply a short acknowledgement."}
    try:
        mode = normalize_mode(mode)
        options = normalize_options(mode, options)
        if url and urls is not None:
            raise ValueError("Use url OR urls, not both.")
        sources = urls if urls is not None else [url] if url else None
        if sources is None:
            source = message
            if not message.attachments and message.reference:
                # Resolve only the real direct reply in the same readable channel.
                source = await message.channel.fetch_message(
                    message.reference.message_id
                )
            sources = [attachment.url for attachment in source.attachments]
        if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_BATCH:
            raise ValueError("Provide 1-20 sources or reply to a Discord attachment.")
        if any(validate_source(item, mode) is None for item in sources):
            raise ValueError("Invalid transfer source for the selected mode.")
        sources = expand_sources(sources, mode)
        for item in sources:
            build_command(mode, target[1], item, options, "0" * 32)
    except (ValueError, discord.HTTPException) as error:
        return {"status": "invalid_request", "error": str(error)}

    client = _transport()
    try:
        bot = await asyncio.wait_for(client.get_users(target[1]), timeout=10)
        if (
            not bot.is_bot
            or str(bot.username).casefold() != target[1].casefold()
            or bot.id == client.me.id
        ):
            return {
                "status": "invalid_bot",
                "error": "Mirror target identity is invalid.",
            }
    except Exception as error:
        logger.warning("Discord mirror target unavailable: {}", type(error).__name__)
        return {
            "status": "unavailable",
            "error": "Telegram Mirror bot is not reachable.",
        }
    if not callable(deps.publish_image_progress):
        return {
            "status": "unavailable",
            "error": "Acknowledgement delivery is unavailable.",
        }
    deps.mirror_attempted = True
    await deps.publish_image_progress(acknowledgement.strip())
    if not _authorized(message):
        return {"status": "denied", "error": "Bot admin permission was revoked."}
    deps.side_effects_started = True
    results = []
    for index, item in enumerate(sources):
        try:
            result = await mirror_bridge.submit(
                client,
                origin_chat_id=message.channel.id,
                origin_message_id=message.id,
                requester_id=message.author.id,
                mirror_chat_id=target[0],
                mirror_bot_id=bot.id,
                mirror_bot_username=target[1],
                url=item,
                mode=mode,
                options=options,
                item_index=index,
                batch_size=len(sources),
                origin_platform="discord",
                origin_guild_id=getattr(message.guild, "id", None),
                origin_request_text=message.content,
            )
            results.append(result)
        except Exception as error:
            logger.warning(
                "Discord mirror dispatch unconfirmed: {}", type(error).__name__
            )
            return {
                "status": "dispatch_error",
                "tasks": results,
                "error": "Current submission outcome is unknown. Do not retry automatically. Earlier tasks remain tracked.",
            }
    deps.mirror_dispatched = True
    return {
        "status": "dispatched",
        "tasks": results,
        "instruction": "Acknowledged already. End silently; updates arrive asynchronously.",
    }


async def cancel_discord_mirror(
    ctx: RunContext[DiscordContextDeps], request_id: str | None = None
) -> dict:
    """Cancel this Discord admin's owned task, using the real reply or active tasks.

    Omit request_id normally. If several tasks match, ask which one or cancel
    each returned owned request_id only when the user requests cancelling all.
    A sent cancellation is not confirmed cancellation; await the terminal update.
    """
    message = ctx.deps.message
    if not _authorized(message) or not app_config.telegram_enabled:
        return {"status": "denied", "error": "Bot admin/Telegram transport required."}
    facts = await mirror_cancel.cancel(
        _transport(),
        requester_id=message.author.id,
        origin_chat_id=message.channel.id,
        request_id=request_id,
        reply_message_id=getattr(message.reference, "message_id", None),
        origin_platform="discord",
    )
    if facts.get("status") in {"cancel_requested", "cancel_uncertain"}:
        ctx.deps.side_effects_started = True
    return facts
