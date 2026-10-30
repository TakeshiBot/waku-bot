from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from urllib.parse import urljoin

import discord
import httpx
from ddgs import DDGS
from pydantic_ai import BinaryContent, UserContent

from waku import common
from waku.common.http import get_agent_http_client
from waku.common.image_caption import short_image_caption
from waku.common.safe_http import MAX_REDIRECTS, is_safe_web_url
from waku.config import app_config
from waku.i18n import i18n
from waku.logger import logger
from waku.services import manyacg as manyacg_service
from waku.services.anime_images import (
    NoFreshImageError,
    enabled_image_sources,
    fetch_anime_artwork,
    image_services_notice,
    manyacg_candidates,
    original_image_url,
    release_anime_image,
    remember_anime_image,
)
from waku.services.manyacg import manyacg_client

from .constants import _DISCORD_IMAGE_BATCH_MAX
from .embeds import discord_command_embed
from .permissions import _channel_allowed
from .settings import (
    _discord_dm_settings,
    _discord_guild_settings,
    _discord_r18_mode,
)
from .state import _discord_image_lock
from .utilities import _channel_name, _guild_name

_DISCORD_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_DISCORD_MEDIA_MAX_BYTES = 8 * 1024 * 1024

def _find_artwork_url(content: str) -> str | None:
    for regex in manyacg_service.ARTWORK_ALL_REGEX:
        match = regex.search(content)
        if match:
            artwork_url = match.group()
            if not artwork_url.startswith("http"):
                artwork_url = "https://" + artwork_url
            return artwork_url
    return None

def _discord_media_enabled() -> bool:
    return app_config.agent_multimodal and "photo" in app_config.agent_multimodal_inputs


async def _send_discord_media_notice(message: discord.Message, text: str) -> None:
    """Send a consistent embed for media command status and error messages."""
    await message.channel.send(
        embed=discord_command_embed(text, title="Waku"),
        reference=message,
        mention_author=False,
    )

async def _download_discord_media(url: str, *, max_bytes: int = _DISCORD_MEDIA_MAX_BYTES) -> tuple[bytes, str] | None:
    timeout = httpx.Timeout(12.0, connect=6.0)
    shared_client = get_agent_http_client()
    client = shared_client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not await asyncio.to_thread(is_safe_web_url, current):
                return None
            async with client.stream(
                "GET", current,
                headers={"User-Agent": "WakuDiscordBot/1.0"},
                timeout=timeout, follow_redirects=False,
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        return None
                    current = urljoin(current, location)
                    continue
                if response.status_code != 200:
                    return None
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in _DISCORD_IMAGE_CONTENT_TYPES:
                    return None
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        return None
                return bytes(data), content_type
        return None
    except httpx.HTTPError as e:
        logger.debug(f"Discord media download failed: {e.__class__.__name__}")
        return None
    finally:
        if shared_client is None:
            await client.aclose()

async def _discord_attachment_contents(
    message: discord.Message, label: str = "attachment"
) -> tuple[list[str], list[UserContent]]:
    summaries: list[str] = []
    contents: list[UserContent] = []
    if not message.attachments:
        return summaries, contents
    for attachment in message.attachments[:4]:
        content_type = (attachment.content_type or "").split(";", 1)[0].lower()
        size_kb = max(1, int((attachment.size or 0) / 1024))
        summaries.append(
            f"- {label}: {attachment.filename} type={content_type or 'unknown'} size={size_kb}KB"
        )
        if not _discord_media_enabled():
            continue
        if attachment.size and attachment.size > _DISCORD_MEDIA_MAX_BYTES:
            summaries.append(f"  skipped: file is larger than {_DISCORD_MEDIA_MAX_BYTES // 1024 // 1024}MB")
            continue
        # Unknown size/MIME must use the bounded streaming path: Attachment.read
        # materializes the entire response before we can inspect its size.
        if not attachment.size or content_type not in _DISCORD_IMAGE_CONTENT_TYPES:
            downloaded = await _download_discord_media(attachment.url)
            if downloaded is None:
                continue
            data, content_type = downloaded
        else:
            try:
                data = await attachment.read(use_cached=True)
            except Exception as e:
                logger.debug(f"Discord attachment download failed: {e.__class__.__name__}: {e}")
                downloaded = await _download_discord_media(attachment.url)
                if downloaded is None:
                    continue
                data, content_type = downloaded
        if len(data) <= _DISCORD_MEDIA_MAX_BYTES:
            contents.append(BinaryContent(data=data, media_type=content_type))
        else:
            summaries.append(f"  skipped: downloaded file is larger than {_DISCORD_MEDIA_MAX_BYTES // 1024 // 1024}MB")
    return summaries, contents

async def _discord_sticker_contents(
    message: discord.Message, label: str = "sticker"
) -> tuple[list[str], list[UserContent]]:
    summaries: list[str] = []
    contents: list[UserContent] = []
    if not message.stickers:
        return summaries, contents
    for sticker in message.stickers[:3]:
        sticker_format = getattr(sticker, "format", None)
        summaries.append(f"- {label}: {sticker.name} format={sticker_format}")
        if not _discord_media_enabled():
            continue
        url = getattr(sticker, "url", None)
        if not url:
            continue
        try:
            downloaded = await _download_discord_media(str(url))
        except Exception as e:
            logger.debug(f"Discord sticker download failed: {e.__class__.__name__}: {e}")
            continue
        if downloaded is None:
            continue
        data, content_type = downloaded
        contents.append(BinaryContent(data=data, media_type=content_type))
    return summaries, contents

def _discord_artwork_view(source_url: str | None) -> discord.ui.View | None:
    if not source_url:
        return None
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Chi tiết", url=source_url))
    return view

def _discord_artwork_embed(
    *,
    title: str,
    source_url: str,
    image_url: str,
    r18: bool,
    description: str | None = None,
    index: int | None = None,
    source_type: str = "",
) -> discord.Embed:
    title = short_image_caption(title)
    display_title = f"🔞 {title}" if r18 else title
    if index is not None:
        display_title = f"{display_title} ({index})"
    embed = discord.Embed(
        title=display_title[:256],
        url=source_url,
        description=(description or "")[:3500] or None,
        color=0xFF8AC5 if not r18 else 0xFF5C8A,
    )
    embed.set_image(url=image_url)
    embed.set_footer(text="Waku API" if source_type == "waku-api" else "nekos.best" if source_type == "nekos.best" else "ManyACG / Pixiv")
    return embed

async def _fetch_manyacg_discord_artwork(
    keyword: str = "",
    r18_mode: int = 0,
):
    if manyacg_client is None:
        return None
    return await manyacg_candidates(manyacg_client.client, keyword, r18_mode)

async def _fetch_discord_anime_artwork(keyword: str = "", r18_mode: int = 0, *, scope=None):
    return await fetch_anime_artwork(keyword, r18_mode, _fetch_manyacg_discord_artwork, scope=scope)


async def _send_discord_image_embed(
    message: discord.Message,
    embed: discord.Embed,
    image_url: str,
    view: discord.ui.View | None = None,
    *,
    spoiler: bool = False,
    channel: discord.abc.Messageable | None = None,
    content: str | None = None,
    allowed_mentions: discord.AllowedMentions | None = None,
    reference: discord.Message | None = None,
) -> bool:
    target_channel = channel or message.channel
    if not spoiler:
        embed.set_image(url=image_url)
        await target_channel.send(
            content=content,
            embed=embed,
            view=view,
            reference=reference,
            allowed_mentions=allowed_mentions,
            mention_author=False,
        )
        return True

    downloaded = await _download_image_bytes(image_url)
    if downloaded is None:
        logger.warning("Discord R18 spoiler image download failed; not exposing image URL")
        return False

    data, content_type = downloaded
    filename = "SPOILER_" + _image_filename(content_type)
    file = discord.File(io.BytesIO(data), filename=filename, spoiler=True)
    await target_channel.send(
        content=content,
        file=file,
        view=view,
        reference=reference,
        allowed_mentions=allowed_mentions,
        mention_author=False,
    )
    return True

async def _send_discord_anime_photo_card(
    message: discord.Message,
    artwork: manyacg_service.Artwork,
    picture: manyacg_service.Picture,
    *,
    channel: discord.abc.Messageable | None = None,
    content: str | None = None,
    caption: str | None = None,
    allowed_mentions: discord.AllowedMentions | None = None,
    reference: discord.Message | None = None,
) -> bool:
    embed = _discord_artwork_embed(
        title=artwork.title,
        source_url=artwork.source_url,
        image_url="",
        r18=artwork.r18,
        source_type=artwork.source_type,
    )
    caption_text = (caption or "").strip()
    try:
        # Agent prose belongs to a normal message; only artwork metadata is in the card.
        text = "\n\n".join(part for part in (content, caption_text) if part)
        if text:
            from .messages import _split_reply

            destination = channel if channel is not None else message.channel
            for index, part in enumerate(_split_reply(text)):
                await destination.send(
                    part, allowed_mentions=allowed_mentions or discord.AllowedMentions.none(),
                    reference=reference if index == 0 else None, mention_author=False,
                )
        success = await _send_discord_image_embed(
            message,
            embed,
            picture.regular,
            _discord_artwork_view(original_image_url(artwork, picture)),
            spoiler=artwork.r18,
            channel=channel,
            content=None,
            allowed_mentions=allowed_mentions,
            reference=reference,
        )
    except BaseException:
        await release_anime_image(f"discord:{(channel if channel is not None else message.channel).id}", artwork, picture)
        raise
    if success:
        destination = channel if channel is not None else message.channel
        await remember_anime_image(f"discord:{destination.id}", artwork, picture)
        logger.info(
            "Discord anime send success: "
            f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
            f"title={artwork.title!r} r18={artwork.r18} spoiler={artwork.r18}"
        )
    else:
        await release_anime_image(f"discord:{(channel if channel is not None else message.channel).id}", artwork, picture)
        logger.error(
            "Discord anime send failed: "
            f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
            f"title={artwork.title!r} r18={artwork.r18}"
        )
    return success


async def _send_discord_anime_photos(
    message, items, *, channel=None, content=None, caption=None,
    allowed_mentions=None, reference=None,
) -> int:
    """Keep single-photo cards; upload multiple photos as native attachments."""
    destination = channel if channel is not None else message.channel
    scope = f"discord:{destination.id}"
    files = []
    batch = []
    size = 0
    sent_count = 0
    # Discord permits a 25 MiB request. Leave space for multipart and metadata.
    max_request_bytes = 24 * 1024 * 1024

    async def send_batch():
        nonlocal sent_count
        view = discord.ui.View()
        titles = []
        for index, (artwork, picture) in enumerate(batch):
            number = sent_count + index + 1
            title = discord.utils.escape_markdown(artwork.title[:70]).replace("\n", " ")
            titles.append(f"{number}. {title}")
            detail_url = original_image_url(artwork, picture)
            if detail_url:
                view.add_item(discord.ui.Button(
                    label=f"{number}. Chi tiết", url=detail_url, row=index // 5,
                ))
        try:
            await destination.send(
                content="\n".join(titles), files=files, view=view if view.children else None,
                reference=reference if sent_count == 0 else None,
                allowed_mentions=discord.AllowedMentions.none(), mention_author=False,
            )
            sent_count += len(batch)
            for artwork, picture in batch:
                await remember_anime_image(scope, artwork, picture)
        finally:
            for file in files:
                file.close()
                file.fp.close()

    try:
        if len(items) == 1:
            return int(await _send_discord_anime_photo_card(
                message, *items[0], channel=channel, content=content,
                caption=caption, allowed_mentions=allowed_mentions, reference=reference,
            ))
        prose = "\n\n".join(part for part in (content, caption) if part)
        if prose:
            from .messages import _split_reply

            for index, part in enumerate(_split_reply(prose)):
                await destination.send(
                    part, allowed_mentions=allowed_mentions or discord.AllowedMentions.none(),
                    reference=reference if index == 0 else None, mention_author=False,
                )
        for index, (artwork, picture) in enumerate(items):
            downloaded = await _download_image_bytes(picture.regular)
            if downloaded is None:
                raise RuntimeError("Could not download an image for the Discord album")
            data, content_type = downloaded
            if files and (size + len(data) > max_request_bytes or len(files) == _DISCORD_IMAGE_BATCH_MAX):
                await send_batch()
                files, batch, size = [], [], 0
            filename = f"waku_{index + 1}_" + _image_filename(content_type)
            files.append(discord.File(
                io.BytesIO(data), filename=filename, spoiler=artwork.r18,
                description=f"{artwork.title}\n{artwork.source_url}"[:1024],
            ))
            batch.append((artwork, picture))
            size += len(data)
        if files:
            await send_batch()
        return sent_count
    finally:
        for file in files:
            file.close()
            file.fp.close()
        for artwork, picture in items:
            await release_anime_image(scope, artwork, picture)


async def _fetch_discord_anime_batch(keyword, r18_mode, count, *, scope):
    items = []
    try:
        for _ in range(max(1, min(_DISCORD_IMAGE_BATCH_MAX, count))):
            try:
                item = await _fetch_discord_anime_artwork(keyword=keyword, r18_mode=r18_mode, scope=scope)
            except NoFreshImageError:
                if items:
                    break
                raise
            if item is None:
                break
            items.append(item)
        return items
    except BaseException:
        for artwork, picture in items:
            await release_anime_image(scope, artwork, picture)
        raise

async def _search_web_images(query: str, max_results: int = 8) -> list[dict]:
    def _search() -> list[dict]:
        with DDGS(proxy=app_config.agent_proxy or None, timeout=12) as ddgs:
            return list(ddgs.images(query, max_results=max_results, safesearch="moderate"))

    return await asyncio.wait_for(asyncio.to_thread(_search), timeout=20)

async def _download_image_bytes(url: str) -> tuple[bytes, str] | None:
    return await _download_discord_media(url, max_bytes=8_000_000)

def _image_filename(content_type: str) -> str:
    extension = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }.get(content_type, "jpg")
    return f"waku_image.{extension}"

async def _send_discord_seg(message: discord.Message, keyword: str = "", count: int = 1) -> bool:
    # Text commands accept `!seg 5 tai mèo`; slash commands have a count option.
    parts = keyword.split(maxsplit=1)
    if parts and parts[0].isascii() and parts[0].isdigit():
        count = min(_DISCORD_IMAGE_BATCH_MAX, max(1, int(parts[0])))
        keyword = parts[1] if len(parts) > 1 else ""
    settings = (
        await _discord_guild_settings(message.guild)
        if message.guild is not None
        else await _discord_dm_settings(message.author)
    )
    if not settings.setu_enabled:
        await _send_discord_media_notice(
            message, i18n.t("bot.msg.manyacg.chat_setu_disabled", locale=settings.lang)
        )
        return True
    if not enabled_image_sources():
        await _send_discord_media_notice(message, image_services_notice(settings.lang))
        return True
    ratekey = f"discord_seg_cd:{message.channel.id}:{message.author.id}"
    if await common.memttlcache.get(ratekey, False):
        await _send_discord_media_notice(message, "Please wait a moment before requesting another image.")
        return True

    image_lock = _discord_image_lock()
    if image_lock.locked():
        logger.info(f"discord_image_queue_busy seg user={message.author.id} channel={message.channel.id}")
        await _send_discord_media_notice(
            message,
            "Waku đang xử lý ảnh khác, đợi vài giây rồi gọi lại nha.",
        )
        return True
    await common.memttlcache.set(ratekey, True, ttl=app_config.manyacg_setu_cd)

    try:
        r18_mode = await _discord_r18_mode(message.guild)
        async with message.channel.typing():
            async with image_lock:
                fetched = await _fetch_discord_anime_batch(keyword, r18_mode, count, scope=f"discord:{message.channel.id}")
                if not fetched:
                    await _send_discord_media_notice(message, "Could not fetch an image from the available image sources.")
                    return True
                await _send_discord_anime_photos(message, fetched)
            return True
    except NoFreshImageError:
        await _send_discord_media_notice(message, i18n.t("bot.msg.manyacg.no_fresh", locale=settings.lang))
        return True
    except Exception as e:
        logger.error(f"Discord seg error: {e.__class__.__name__}: {e}")
        await _send_discord_media_notice(message, "Image sending failed. Please try again later.")
        return True


async def _respond_to_discord_seg_interaction(
    interaction: discord.Interaction,
    text: str,
    *,
    ephemeral: bool = True,
) -> None:
    """Respond to a slash command with the same embed style as text commands."""
    embed = discord_command_embed(text, title="Waku")
    if interaction.response.is_done():
        await interaction.edit_original_response(content=None, embed=embed)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def _send_discord_seg_interaction(
    interaction: discord.Interaction,
    keyword: str | None = None,
    count: int = 1,
) -> None:
    """Run `/seg`, mirroring the text command without requiring a fake message."""
    channel = interaction.channel
    if not isinstance(channel, discord.abc.Messageable):
        await _respond_to_discord_seg_interaction(
            interaction,
            "Lệnh này chỉ dùng được trong kênh có thể gửi tin nhắn.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    request_context = SimpleNamespace(guild=guild, channel=channel, author=interaction.user)
    if not await _channel_allowed(request_context):
        await _respond_to_discord_seg_interaction(
            interaction, "Kênh này không nằm trong phạm vi hoạt động đã cấu hình của Waku."
        )
        return
    settings = (
        await _discord_guild_settings(guild)
        if guild is not None
        else await _discord_dm_settings(interaction.user)
    )
    def notice(vi: str, en: str) -> str:
        return en if settings.lang == "en" else vi

    if not settings.setu_enabled:
        await _respond_to_discord_seg_interaction(
            interaction,
            i18n.t("bot.msg.manyacg.chat_setu_disabled", locale=settings.lang),
        )
        return

    if not enabled_image_sources():
        await _respond_to_discord_seg_interaction(interaction, image_services_notice(settings.lang))
        return
    ratekey = f"discord_seg_cd:{channel.id}:{interaction.user.id}"
    if await common.memttlcache.get(ratekey, False):
        await _respond_to_discord_seg_interaction(
            interaction,
            notice("Chờ một chút rồi yêu cầu ảnh tiếp nhé.", "Please wait a moment before requesting another image."),
        )
        return

    image_lock = _discord_image_lock()
    if image_lock.locked():
        await _respond_to_discord_seg_interaction(
            interaction,
            "Waku đang xử lý ảnh khác, đợi vài giây rồi gọi lại nha.",
        )
        return
    await common.memttlcache.set(ratekey, True, ttl=app_config.manyacg_setu_cd)

    try:
        r18_mode = await _discord_r18_mode(guild)
        async with image_lock:
            fetched = await _fetch_discord_anime_batch(
                (keyword or "").strip(), r18_mode, count, scope=f"discord:{channel.id}",
            )
            if not fetched:
                await _respond_to_discord_seg_interaction(
                    interaction,
                    notice("Không lấy được ảnh. Thử lại sau nhé.", "Could not fetch an image. Please try again later."),
                )
                return
            sent = await _send_discord_anime_photos(request_context, fetched)
        if not sent:
            await _respond_to_discord_seg_interaction(
                interaction,
                notice("Gửi ảnh thất bại. Thử lại sau nhé.", "Image sending failed. Please try again later."),
            )
            return
        try:
            await interaction.delete_original_response()
        except discord.HTTPException as e:
            logger.debug(f"Discord seg slash placeholder cleanup failed: {e}")
    except NoFreshImageError:
        await _respond_to_discord_seg_interaction(interaction, i18n.t("bot.msg.manyacg.no_fresh", locale=settings.lang))
    except Exception as e:
        logger.error(f"Discord seg slash error: {e.__class__.__name__}: {e}")
        await _respond_to_discord_seg_interaction(
            interaction,
            notice("Gửi ảnh thất bại. Thử lại sau nhé.", "Image sending failed. Please try again later."),
        )

async def _send_discord_artwork(message: discord.Message, artwork_url: str) -> bool:
    settings = (
        await _discord_guild_settings(message.guild)
        if message.guild is not None
        else await _discord_dm_settings(message.author)
    )
    if not settings.setu_enabled:
        await _send_discord_media_notice(
            message, i18n.t("bot.msg.manyacg.chat_setu_disabled", locale=settings.lang)
        )
        return True
    if manyacg_client is None:
        await _send_discord_media_notice(message, "ManyACG is not configured, so Waku cannot parse artwork links yet.")
        return True
    if not app_config.manyacg_api_key:
        await _send_discord_media_notice(message, "ManyACG API key is missing, so Waku cannot parse artwork links yet.")
        return True

    try:
        async with message.channel.typing():
            resp = await manyacg_client.fetch_artwork(artwork_url)
        if resp.status != 200 or resp.data is None:
            await _send_discord_media_notice(message, "Could not fetch artwork from this link.")
            return True
        artwork = resp.data
        if artwork.r18 and await _discord_r18_mode(message.guild) == 0:
            await _send_discord_media_notice(message, "R18 artwork is disabled here.")
            return True
        pictures = sorted(artwork.pictures or [], key=lambda item: item.index)
        if not pictures:
            await _send_discord_media_notice(message, "This artwork has no image to send.")
            return True
        sent = False
        view = _discord_artwork_view(artwork.source_url)
        for index, picture in enumerate(pictures[:4], start=1):
            embed = _discord_artwork_embed(
                title=artwork.title,
                source_url=artwork.source_url,
                image_url="",
                r18=artwork.r18,
                description=artwork.description if index == 1 else None,
                index=index if len(pictures) > 1 else None,
            )
            if index == 1 and len(pictures) > 4:
                embed.add_field(
                    name="More images",
                    value=f"This artwork has {len(pictures)} images; sending the first 4.",
                    inline=False,
                )
            success = await _send_discord_image_embed(
                message,
                embed,
                picture.original,
                view if not sent else None,
                spoiler=artwork.r18,
            )
            if not success:
                await _send_discord_media_notice(
                    message,
                    "Failed to upload this R18 artwork as a spoiler attachment.",
                )
                return True
            sent = True
        return True
    except Exception as e:
        logger.error(f"Discord artwork parse error: {e.__class__.__name__}: {e}")
        await _send_discord_media_notice(message, "Failed to parse this artwork link.")
        return True
