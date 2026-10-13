"""Discord generation/editing through the base's shared image services."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Literal

import discord
from pydantic_ai import ModelRetry, RunContext

from waku import common
from waku.config import app_config
from waku.logger import logger
from waku.services import image_gen
from waku.services.image_limits import (
    OPERATION_TIMEOUT,
    ImageLimitDenied,
    image_limiter,
)
from waku.services.image_requests import (
    GENERATED_IMAGE_CAPTION,
    explicit_image_operation,
    safe_image_prompt,
)

from ..media import _DISCORD_MEDIA_MAX_BYTES, _download_discord_media, _image_filename
from ..models import DiscordContextDeps
from ..permissions import (
    _can_read_message_history,
    _can_view_channel,
    _channel_allowed,
    _channel_permissions,
)
from ..settings import (
    _contains_r18_keyword,
    _discord_dm_settings,
    _discord_guild_settings,
)


@dataclass
class DiscordGeneratedImageResult:
    success: bool
    message: str
    retry_after: int = 0


async def prepare_discord_image_tools(ctx, definition):
    text = ctx.deps.message.content or ""
    if (
        image_gen.image_gen_client is not None
        and explicit_image_operation(text, "generate")
        or image_gen.image_edit_client is not None
        and explicit_image_operation(text, "edit")
    ):
        return definition
    return None


async def _image_settings(message):
    return (
        await _discord_guild_settings(message.guild)
        if message.guild is not None
        else await _discord_dm_settings(message.author)
    )


def _last_image_key(message):
    return f"discord_generated_image:{message.channel.id}:{message.author.id}"


def _can_send_image(message):
    if not _can_view_channel(message.channel, message.guild, requester=message.author):
        return False
    if message.guild is None:
        return True
    permissions = _channel_permissions(message.channel, message.guild)
    send = (
        "send_messages_in_threads"
        if isinstance(message.channel, discord.Thread)
        else "send_messages"
    )
    return bool(
        permissions and permissions.attach_files and getattr(permissions, send, False)
    )


async def _attachment_image(message):
    for attachment in getattr(message, "attachments", []):
        media_type = (attachment.content_type or "").split(";", 1)[0].lower()
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            continue
        if attachment.size and attachment.size > _DISCORD_MEDIA_MAX_BYTES:
            continue
        source = await _download_discord_media(attachment.url)
        if source is not None:
            return source
    return None


async def _edit_source(message):
    # An explicit source must never silently fall back to a different old image.
    # Current attachments take precedence, even if a referenced message is gone.
    if getattr(message, "attachments", []):
        return await _attachment_image(message)
    reference = getattr(message, "reference", None)
    replied = getattr(reference, "resolved", None) or getattr(
        reference, "cached_message", None
    )
    can_read = _can_read_message_history(
        message.channel, message.guild, requester=message.author
    )
    if reference:
        if getattr(reference, "channel_id", None) != message.channel.id:
            return None
        if replied is None and can_read:
            replied = await message.channel.fetch_message(reference.message_id)
        if (
            replied is not None
            and getattr(getattr(replied, "channel", None), "id", None)
            == message.channel.id
        ):
            return await _attachment_image(replied)
        return None
    if can_read:
        previous = await common.memttlcache.get(_last_image_key(message))
        if type(previous) is int:
            old = await message.channel.fetch_message(previous)
            if getattr(getattr(old, "author", None), "id", None) != getattr(
                getattr(message, "_state", None), "self_id", None
            ):
                return None
            return await _attachment_image(old)
    return None


async def discord_image_ops(
    ctx: RunContext[DiscordContextDeps],
    operation: Literal["generate", "edit"],
    prompt: str,
    size: str = "1024x1024",
    *,
    progress_message: str,
) -> DiscordGeneratedImageResult:
    """Generate only when the user explicitly asks to create/draw an image.

    For ordinary send/show/photo requests use send_discord_anime_photo with
    the existing image APIs. Edit only on explicit image-edit requests, using
    a current/replied image or that caller's last generated image in this chat.
    Write progress_message yourself: a brief acknowledgment before image work.
    After confirmed upload end silently, with no completion text or second send.
    On failure explain the returned facts, never claim that an image was sent.
    """
    message = ctx.deps.message
    if not explicit_image_operation(message.content or "", operation):
        return DiscordGeneratedImageResult(
            False,
            "No explicit generation/edit request. Use send_discord_anime_photo for ordinary image requests; do not call an image model.",
        )
    prompt, progress_message = prompt.strip(), progress_message.strip()
    if not prompt or not progress_message or len(progress_message) > 600:
        raise ModelRetry(
            "Provide a non-empty image prompt and a short progress_message."
        )
    settings = await _image_settings(message)
    if (
        not settings.setu_enabled
        or not await _channel_allowed(message)
        or not _can_send_image(message)
    ):
        return DiscordGeneratedImageResult(
            False, "Image sending is disabled or this channel is inaccessible."
        )
    r18 = _contains_r18_keyword(prompt)
    if r18 and (message.guild is None or settings.r18_mode == 0):
        return DiscordGeneratedImageResult(
            False, "R18 images are disabled for this conversation."
        )
    service = (
        image_gen.image_gen_client
        if operation == "generate"
        else image_gen.image_edit_client
    )
    if service is None:
        return DiscordGeneratedImageResult(
            False, "The requested image model is not configured."
        )
    # Separate Discord identities from Telegram IDs while sharing global capacity.
    lease = image_limiter.acquire(-(1 << 64) - message.author.id)
    if isinstance(lease, ImageLimitDenied):
        return DiscordGeneratedImageResult(False, lease.reason, lease.retry_after)
    try:
        async with asyncio.timeout(OPERATION_TIMEOUT):
            source = await _edit_source(message) if operation == "edit" else None
            if operation == "edit" and source is None:
                return DiscordGeneratedImageResult(
                    False, "Attach an image or reply to the image you want edited."
                )
            if ctx.deps.publish_image_progress is not None:
                await ctx.deps.publish_image_progress(progress_message)
            lease.mark_started()
            if operation == "generate":
                result = await service.generate(prompt=safe_image_prompt(prompt, r18=r18), size=size)
            else:
                data, mime = source
                result = await service.edit(
                    prompt=safe_image_prompt(prompt, r18=r18),
                    image_data=data,
                    image_filename=_image_filename(mime),
                    image_mime_type=mime,
                )
            if not result.success or not result.data:
                if not result.success and getattr(result, "retry_safe", False):
                    lease.mark_failed()
                return DiscordGeneratedImageResult(
                    False, result.error or "The image model returned no image."
                )
            settings = await _image_settings(message)
            if (
                not settings.setu_enabled
                or not await _channel_allowed(message)
                or not _can_send_image(message)
                or r18
                and (message.guild is None or settings.r18_mode == 0)
            ):
                return DiscordGeneratedImageResult(
                    False,
                    "Image access changed while processing; the image was not uploaded.",
                )
            limit = getattr(message.guild, "filesize_limit", 10 * 1024 * 1024)
            if len(result.data) > limit:
                return DiscordGeneratedImageResult(
                    False,
                    "The generated image exceeds this Discord chat's upload limit.",
                )
            file = discord.File(
                io.BytesIO(result.data),
                filename=_image_filename(result.media_type),
                spoiler=r18,
            )
            ctx.deps.side_effects_started = True
            sent = await message.channel.send(
                GENERATED_IMAGE_CAPTION,
                file=file,
                reference=discord.MessageReference(
                    message_id=message.id,
                    channel_id=message.channel.id,
                    guild_id=getattr(message.guild, "id", None),
                    fail_if_not_exists=False,
                ),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if sent is None:
                raise RuntimeError("Unconfirmed Discord image upload")
            ctx.deps.image_delivered = True
            try:
                await common.memttlcache.set(
                    _last_image_key(message),
                    sent.id,
                    ttl=app_config.cachettl_agent_history,
                )
            except Exception as error:
                logger.warning(
                    "Discord generated image cache failed: {}", type(error).__name__
                )
            return DiscordGeneratedImageResult(
                True, "Image uploaded with caption. End the turn silently."
            )
    except TimeoutError:
        return DiscordGeneratedImageResult(
            False, "Image processing timed out. Do not automatically retry.", 60
        )
    except discord.HTTPException as error:
        return DiscordGeneratedImageResult(
            False, f"Discord image upload failed: {type(error).__name__}."
        )
    finally:
        lease.release()
