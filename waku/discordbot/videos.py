"""Waku API video commands and deferred AI delivery on Discord."""

from typing import Literal

import discord
from pydantic_ai import RunContext

from waku import common, i18n
from waku.config import app_config
from waku.logger import logger
from waku.services.catalog_videos import (
    VideoError,
    VideoResult,
    catalog_notice,
    fetch_catalog_video,
    prepare_video,
)

from .models import DiscordContextDeps
from .permissions import _channel_allowed
from .settings import _discord_dm_settings, _discord_guild_settings


async def _prepare(context, keyword, image_type=None, request_text=""):
    settings = await (
        _discord_guild_settings(context.guild)
        if context.guild
        else _discord_dm_settings(context.author)
    )
    if not settings.setu_enabled:
        raise ValueError(
            i18n.t("bot.msg.manyacg.chat_setu_disabled", locale=settings.lang)
        )
    mode = settings.r18_mode if context.guild else 0
    row = await fetch_catalog_video(
        keyword, mode, image_type, request_text=request_text
    )
    limit = min(
        20 * 1024 * 1024, getattr(context.guild, "filesize_limit", 10 * 1024 * 1024)
    )
    if row["size"] > limit:
        raise ValueError(
            f"Video vượt giới hạn file {limit // 1024 // 1024} MB của kênh Discord này."
            if settings.lang != "en"
            else f"Video exceeds this channel's {limit // 1024 // 1024} MB attachment limit."
        )
    return row, await prepare_video(row, limit=limit)


async def _send(channel, row, data, *, reference=None):
    view = discord.ui.View()
    if source := row.get("source_url"):
        view.add_item(discord.ui.Button(label="Bản gốc", url=source))
    kwargs = {
        "content": str(row.get("title") or "")[:1000] or None,
        "file": discord.File(
            data,
            filename=row.get("filename") or "video.mp4",
            spoiler=row["rating"] == "r18",
        ),
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if view.children:
        kwargs["view"] = view
    if reference:
        kwargs["reference"] = reference
        kwargs["mention_author"] = False
    await channel.send(**kwargs)
    return VideoResult(sent_count=1)


async def send_discord_catalog_video(
    ctx: RunContext[DiscordContextDeps],
    keyword: str = "",
    image_type: Literal["anime", "real"] | None = None,
) -> VideoResult:
    """Prepare ONE catalog video/clip, only when asked for video (not a photo).

    Empty keyword selects random; otherwise pass exact requested tags, comma
    separated, including r18/sfw when requested. No vision/tagging is involved.
    Respect no-match/policy errors. delivery_pending means reply naturally now;
    the video follows your final chat reply, so do not claim it already arrived.
    """
    message = ctx.deps.message
    if not await _channel_allowed(message):
        return VideoResult(success=False, message="Waku is disabled in this channel.")
    try:
        row, data = await _prepare(message, keyword, image_type, message.content)
    except VideoError as error:
        return VideoResult(
            success=False,
            message=catalog_notice(keyword=keyword, lang="en", code=error.code),
        )
    except ValueError as error:
        return VideoResult(success=False, message=str(error))
    except Exception:
        return VideoResult(
            success=False, message="Video download failed. No video was sent."
        )

    async def deliver():
        return await _send(message.channel, row, data, reference=message)

    async def cleanup():
        data.close()

    if queue := ctx.deps.deferred_images:
        queue.add(deliver, cleanup)
        return VideoResult(delivery_pending=True, prepared_count=1)
    try:
        return await deliver()
    finally:
        await cleanup()


async def send_discord_vseg(message, keyword="", *, interaction=None):
    if not await _channel_allowed(message):
        if interaction:
            await interaction.edit_original_response(
                content="Kênh này không nằm trong phạm vi hoạt động của Waku."
            )
        return True
    settings = await (
        _discord_guild_settings(message.guild)
        if message.guild
        else _discord_dm_settings(message.author)
    )

    async def notice(text):
        if interaction:
            await interaction.edit_original_response(content=text)
        else:
            await message.reply(
                text,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    key = f"discord_seg_cd:{message.channel.id}:{message.author.id}"
    if await common.memttlcache.get(key, False):
        await notice(i18n.t("bot.msg.manyacg.setu_cd", locale=settings.lang))
        return True
    await common.memttlcache.set(key, True, ttl=app_config.manyacg_setu_cd)
    data = None
    try:
        row, data = await _prepare(message, keyword)
        await _send(
            message.channel, row, data, reference=None if interaction else message
        )
        if interaction:
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass
    except VideoError as error:
        await notice(
            catalog_notice(keyword=keyword, lang=settings.lang, code=error.code)
        )
    except ValueError as error:
        await notice(str(error))
    except Exception as error:
        logger.warning("Discord catalog video failed: {}", type(error).__name__)
        await notice(catalog_notice(lang=settings.lang, code="unavailable"))
    finally:
        if data:
            data.close()
    return True
