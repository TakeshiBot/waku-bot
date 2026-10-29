from __future__ import annotations

import re
from types import SimpleNamespace

import discord
from pydantic_ai import RunContext

from waku.logger import logger
from waku.services.anime_images import (
    IMAGE_SERVICES_UNAVAILABLE,
    NoFreshImageError,
    enabled_image_sources,
    image_source_enabled,
    release_anime_image,
    resolve_image_search,
)
from waku.services.telegram_images import image_allowed

from .. import state
from ..constants import (
    _DISCORD_IMAGE_BATCH_MAX,
    _DISCORD_IMAGE_BUSY_WAIT_SECONDS,
)
from ..media import (
    _fetch_discord_anime_artwork,
    _send_discord_anime_photos,
)
from ..messages import _resolve_discord_channel
from ..models import (
    DiscordAnimePhotoInfo,
    DiscordAnimePhotoResult,
    DiscordArtistInfo,
    DiscordContextDeps,
    DiscordWebImageResult,
)
from ..permissions import (
    _can_view_channel,
    _channel_allowed,
    _discord_allowed_mentions,
    _is_discord_bot_admin,
)
from ..settings import (
    _discord_dm_settings,
    _discord_guild_settings,
)
from ..state import _discord_image_lock


async def send_discord_web_image(
    ctx: RunContext[DiscordContextDeps], query: str, count: int = 1
) -> DiscordWebImageResult:
    """Search the web for 1-10 photos and prepare a single attachment album.

    Use count for the full requested amount, including follow-up requests.
    Preserve the source/subject from conversation context. For a named website
    or gallery, prefer read_discord_web_page followed by send_discord_web_media
    with the real photo URLs, rather than search-result thumbnails.
    Captions contain source metadata only; write your conversational reply
    separately. delivery_pending means the album follows that final reply.
    Do not call repeatedly for the same batch or claim more photos than the
    returned prepared_count. This is explicit-use web retrieval, not generation.
    """
    from .web_media import search_and_send_web_images

    return await search_and_send_web_images(ctx, query, count)

async def send_discord_anime_photo(
    ctx: RunContext[DiscordContextDeps],
    keyword: str = "",
    target_channel_id: int | None = None,
    target_user_ids: list[int] | None = None,
    caption: str | None = None,
    allow_everyone: bool = False,
    count: int = 1,
) -> DiscordAnimePhotoResult:
    """Get and send images from configured catalogs to the current Discord chat.

    This uses configured ManyACG/Waku API catalogs, including cosplay/photos if
    present in Waku API. For a named website/gallery/cosplayer, research
    that actual source and use send_discord_web_media with its real photo URLs.

    Call this tool when the user naturally asks Waku to send/show/give an
    anime/Pixiv image, picture, photo, seg, ảnh, hình, or similar. The current
    Discord server R18 mode and the user's requested rating are enforced.
    If this tool returns success=False, tell the user the image could not be
    sent instead of claiming that an image was sent.

    Args:
        keyword: Optional tag/theme to search in enabled image APIs, e.g. "nekomimi" or
            "cat ears" for tai mèo, "maid" for hầu gái. Empty means random.
            Use a concise tag rather than the entire user's sentence.
            Results are randomly selected within the API search results.
            Exact character identity is not supported: for a named character,
            use an empty keyword and explain this limitation in your normal chat
            reply, in the user's language and your own style. Never claim an exact
            character match. Captions contain artwork metadata only.
            A delivery_pending result means photos are prepared and will be sent
            AFTER your final chat reply. Write that reply now; do not call this
            tool again or claim the upload has already completed.
            Preserve explicit r18/sfw instructions in this argument.
        target_channel_id: Optional target channel ID for immediate cross-channel sends.
            Bot-admin-only when different from the current channel.
        target_user_ids: Discord user IDs to mention in the sent image message.
            Use when the user asks to gift/call/tag someone for the image.
        caption: Optional text for a gift or cross-channel recipient, sent as a
            SEPARATE chat message, never inside the image caption/embed. Leave empty
            for ordinary current-chat requests and write your normal final reply.
        allow_everyone: Bot-admin-only permission to allow @everyone/@here mentions.
        count: Number of images requested for this turn. The backend caps this to
            10 images per album. One image keeps its embed; multiple images use
            native attachments in the same message, split only for upload size limits.
    """
    if not enabled_image_sources():
        return DiscordAnimePhotoResult(success=False, message=IMAGE_SERVICES_UNAVAILABLE)
    message = ctx.deps.message
    settings = await _discord_guild_settings(message.guild) if message.guild is not None else await _discord_dm_settings(message.author)
    if not settings.setu_enabled:
        return DiscordAnimePhotoResult(success=False, message="Image sending is disabled for this conversation.")
    target_channel = await _resolve_discord_channel(message, target_channel_id)
    if target_channel is None or not isinstance(target_channel, discord.abc.Messageable):
        return DiscordAnimePhotoResult(success=False, message="Target channel is not messageable or was not found.")
    if not _can_view_channel(target_channel, message.guild, requester=message.author):
        return DiscordAnimePhotoResult(success=False, message="Cannot access that Discord channel.")
    if not await _channel_allowed(SimpleNamespace(guild=message.guild, channel=target_channel)):
        return DiscordAnimePhotoResult(success=False, message="Waku is disabled for that channel.")
    target_channel_actual_id = getattr(target_channel, "id", message.channel.id)
    is_admin = _is_discord_bot_admin(message)
    if target_channel_actual_id != message.channel.id and not is_admin:
        return DiscordAnimePhotoResult(success=False, message="Only bot admins can send images in another channel.")
    if allow_everyone and not is_admin:
        return DiscordAnimePhotoResult(success=False, message="Only bot admins can send @everyone/@here mentions.")

    caption_text = (caption or "").strip()
    if ("@everyone" in caption_text or "@here" in caption_text) and not is_admin:
        return DiscordAnimePhotoResult(success=False, message="Only bot admins can send @everyone/@here mentions.")
    target_ids = list(target_user_ids or [])
    bot_user_id = state.discord_client.user.id if state.discord_client and state.discord_client.user else None
    if caption_text:
        target_ids.extend(int(user_id) for user_id in re.findall(r"<@!?(\d+)>", caption_text))
    if not target_ids:
        target_ids = [
            user.id
            for user in message.mentions
            if not user.bot and user.id != bot_user_id
        ]
    target_ids = [user_id for user_id in dict.fromkeys(target_ids) if user_id != bot_user_id]
    mentions = [f"<@{user_id}>" for user_id in target_ids if user_id > 0]
    mention_prefix = " ".join(mention for mention in mentions if mention not in caption_text)
    message_content = None
    embed_caption = caption_text
    has_everyone_mention = "@everyone" in caption_text or "@here" in caption_text
    if mentions and caption_text:
        message_content = f"{mention_prefix} {caption_text}".strip()
        embed_caption = ""
    elif has_everyone_mention and caption_text:
        message_content = caption_text
        embed_caption = ""
    elif mentions:
        message_content = " ".join(mentions)
    if message_content and len(message_content) > 2000:
        return DiscordAnimePhotoResult(
            success=False,
            message="Image caption including mentions exceeds Discord's 2000-character limit.",
        )
    if len(embed_caption) > 2000:
        return DiscordAnimePhotoResult(
            success=False,
            message="The separate image-related chat message exceeds Discord's 2000-character limit.",
        )

    r18_mode = settings.r18_mode if message.guild is not None else 0
    request = resolve_image_search(
        keyword, r18_mode, request_text=getattr(message, "content", "") or ""
    )
    if request is None:
        return DiscordAnimePhotoResult(
            success=False,
            message="The requested Safe/R18 rating is not permitted by this conversation's image mode.",
        )
    keyword, r18_mode = request

    requested_count = max(1, int(count or 1))
    capped_count = min(requested_count, _DISCORD_IMAGE_BATCH_MAX)
    if requested_count > _DISCORD_IMAGE_BATCH_MAX:
        logger.info(
            "discord_image_batch_capped "
            f"user={message.author.id} channel={target_channel_actual_id} "
            f"requested={requested_count} capped={capped_count}"
        )

    image_lock = _discord_image_lock()
    if image_lock.locked():
        logger.info(
            "discord_image_queue_busy "
            f"user={message.author.id} channel={target_channel_actual_id} wait={_DISCORD_IMAGE_BUSY_WAIT_SECONDS}"
        )
        return DiscordAnimePhotoResult(
            success=False,
            message="Discord image sender is busy. Ask the user to wait a few seconds; the image will be available soon.",
            requested_count=requested_count,
            capped_count=capped_count,
            wait_seconds=_DISCORD_IMAGE_BUSY_WAIT_SECONDS,
        )

    prepared = []
    deferred = False
    scope = f"discord:{target_channel_actual_id}"

    async def cleanup():
        for artwork, picture in prepared:
            await release_anime_image(scope, artwork, picture)

    async def deliver():
        if any(not image_source_enabled(artwork) for artwork, _ in prepared):
            return DiscordAnimePhotoResult(success=False, message="The prepared image source was disabled before delivery. No image was sent; explain naturally to the user.")
        current = await _discord_guild_settings(message.guild) if message.guild is not None else await _discord_dm_settings(message.author)
        mode = current.r18_mode if message.guild is not None else 0
        if not current.setu_enabled or any(not image_allowed(artwork.r18, mode) for artwork, _ in prepared):
            return DiscordAnimePhotoResult(success=False, message="Image settings changed before delivery; the prepared images are no longer permitted.")
        async with image_lock:
            # A single upload contains the whole album, with per-file spoilers.
            ctx.deps.side_effects_started = True
            sent_count = await _send_discord_anime_photos(
                message, prepared, channel=target_channel,
                content=message_content, caption=embed_caption,
                allowed_mentions=_discord_allowed_mentions(allow_everyone),
                reference=message if target_channel_actual_id == message.channel.id else None,
            )
        return DiscordAnimePhotoResult(
            success=sent_count > 0,
            data=_anime_photo_info(prepared[-1][0]) if sent_count else None,
            sent_count=sent_count,
            requested_count=requested_count,
            capped_count=capped_count,
        )

    try:
        for _ in range(capped_count):
            try:
                fetched = await _fetch_discord_anime_artwork(
                    keyword.strip(), r18_mode=r18_mode, scope=scope
                )
            except NoFreshImageError:
                if prepared:
                    break
                return DiscordAnimePhotoResult(success=False, message=(
                    "The available images duplicate the last image or images currently being sent. "
                    "Try a different tag or try again later."
                ))
            if fetched is None:
                break
            prepared.append(fetched)
        if not prepared:
            return DiscordAnimePhotoResult(
                success=False,
                message="No anime image was found for this query, or the image source is unavailable. Do not claim an image was sent.",
                requested_count=requested_count,
                capped_count=capped_count,
            )
        logger.info(
            "discord_image_batch_prepared user={} channel={} count={} requested={}",
            message.author.id, target_channel_actual_id, len(prepared), requested_count,
        )
        queue = getattr(ctx.deps, "deferred_images", None)
        if queue is not None:
            queue.add(deliver, cleanup)
            deferred = True
            return DiscordAnimePhotoResult(
                delivery_pending=True,
                prepared_count=len(prepared),
                data=_anime_photo_info(prepared[0][0]),
                requested_count=requested_count,
                capped_count=capped_count,
            )
        return await deliver()
    finally:
        if not deferred:
            await cleanup()


def _anime_photo_info(artwork):
    artist = None
    if artwork.artist is not None:
        artist = DiscordArtistInfo(
            name=artwork.artist.name,
            type=artwork.artist.type,
            username=artwork.artist.username,
            uid=artwork.artist.uid,
        )
    return DiscordAnimePhotoInfo(
        title=artwork.title,
        source_url=artwork.source_url,
        r18=artwork.r18,
        description=artwork.description[:512],
        artist=artist,
        tags=artwork.tags[:10],
    )
