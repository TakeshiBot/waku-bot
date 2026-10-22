import datetime
import html
from dataclasses import dataclass
from hashlib import md5
from typing import Literal

import pyrogram
import pyrogram.errors
from pydantic_ai import ModelRetry, RunContext

from waku import common, database, i18n
from waku.bot.client import client
from waku.common.rich_message import message_plain_text
from waku.common.telegram_runtime import telegram_chat_allowed
from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent.localization import tr
from waku.plugins.agent.output import record_tool_reply, text_already_sent
from waku.plugins.manyacg import manyacg
from waku.services.anime_images import (
    NoFreshImageError,
    fetch_anime_artwork,
    manyacg_candidates,
    original_image_url,
    release_anime_image,
    remember_anime_image,
    resolve_image_search,
)
from waku.services.mirror_policy import is_raw_mirror_command
from waku.services.telegram_images import image_allowed, telegram_image_settings
from waku.timezone import BOT_TIMEZONE

from .. import datatype, quota, sticker_memory, sticker_vec
from ..expression_policy import expression_settings


@dataclass
class SendResult:
    success: bool
    message: str | None = None

    def text(self) -> str:
        if self.success:
            msg = tr("send_succeeded")
            if self.message:
                msg = tr("send_info", p0=msg, p1=self.message)
            return msg
        msg = tr("send_failed")
        if self.message:
            msg = tr("send_error", p0=msg, p1=self.message)
        return msg


# Module-level so APScheduler can serialize jobs by reference.


async def _scheduled_text_job(chat_id: int, text: str) -> None:
    if is_raw_mirror_command(text, chat_id):
        return
    if not await telegram_chat_allowed(chat_id):
        return
    try:
        await client.send_message(chat_id=chat_id, text=text)
        logger.info("Scheduled text message sent successfully")
    except Exception as e:
        logger.error(f"Scheduled text message failed: {e.__class__.__name__}: {e}")


async def _scheduled_media_job(
    chat_id: int,
    media_type: Literal["photo", "video", "audio", "document"],
    media_url: str,
    caption: str,
) -> None:
    if is_raw_mirror_command(caption, chat_id):
        return
    if not await telegram_chat_allowed(chat_id):
        return
    try:
        match media_type:
            case "photo":
                await client.send_photo(
                    chat_id=chat_id,
                    photo=media_url,
                    caption=caption,
                )
            case "video":
                await client.send_video(
                    chat_id=chat_id,
                    video=media_url,
                    caption=caption,
                )
            case "audio":
                await client.send_audio(
                    chat_id=chat_id,
                    audio=media_url,
                    caption=caption,
                )
            case "document":
                await client.send_document(
                    chat_id=chat_id,
                    document=media_url,
                    caption=caption,
                )
        logger.info(f"Scheduled {media_type} message sent successfully")
    except Exception as e:
        logger.error(
            f"Scheduled {media_type} message failed: {e.__class__.__name__}: {e}"
        )


async def _scheduled_poll_job(
    chat_id: int,
    question: str,
    options: list[str],
    is_anonymous: bool,
    allows_multiple_answers: bool,
) -> None:
    if not await telegram_chat_allowed(chat_id):
        return
    try:
        from waku.bot.client import client

        await client.send_poll(
            chat_id=chat_id,
            question=question,
            options=list(options),  # type: ignore[arg-type]
            is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple_answers,
        )
        logger.info(f"Scheduled poll sent successfully: {question[:30]}...")
    except Exception as e:
        logger.error(f"Scheduled poll failed: {e.__class__.__name__}: {e}")


def _parse_schedule_time(schedule_time: str) -> datetime.datetime:
    """Parse an ISO 8601 schedule time; a timezone-less value means UTC+7 bot
    time, so the comparison with the aware clock never mismatches."""
    parsed = datetime.datetime.fromisoformat(schedule_time)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BOT_TIMEZONE)
    return parsed


async def schedule_message(
    ctx: RunContext[datatype.ContextDeps],
    schedule_time: str | None,
    send_immediately: bool = False,
    text: str | None = None,
    media_type: Literal["photo", "video", "audio", "document"] | None = None,
    media_url: str | None = None,
    caption: str | None = None,
) -> str:
    """Schedule a message to be sent at a specific time.

    Use this tool to schedule delayed messages (text or media) for future delivery.

    Args:
        schedule_time: ISO 8601 datetime string for scheduled delivery,
            e.g. "2025-06-04T15:00:00+07:00". Must be in the future.
            A timezone-less value uses Asia/Ho_Chi_Minh (UTC+7).
        send_immediately: If True, send the message immediately.
        text: The message text to send (for text messages). Either text or media
            must be provided, but not both.
        media_type: Media type for media messages. One of "photo", "video",
            "audio", "document". Required if media_url is provided.
        media_url: Direct URL for media types. Required if media_type is provided.
        caption: Optional caption for media messages.
    """
    if ctx.deps.message is None or ctx.deps.chat_id is None:
        return SendResult(success=False, message=tr("context_unavailable")).text()
    if not send_immediately and not schedule_time:
        raise ModelRetry(
            tr("tool_must_provide_either_schedule_time_or_send_immediately_true")
        )

    schedule_datetime: datetime.datetime | None = None
    if not send_immediately and schedule_time:
        try:
            schedule_datetime = _parse_schedule_time(schedule_time)
        except ValueError as e:
            raise ModelRetry(tr("schedule_invalid", p0=e))
        if schedule_datetime < datetime.datetime.now(datetime.UTC):
            raise ModelRetry(tr("tool_schedule_time_must_be_in_the_future"))

    has_text = text is not None and text.strip()
    has_media = media_type is not None or media_url is not None

    if has_text and has_media:
        raise ModelRetry(tr("text_media_conflict"))
    if not has_text and not has_media:
        raise ModelRetry(
            tr("tool_must_provide_either_text_or_media_media_type_media_url")
        )
    if has_media and not media_type:
        raise ModelRetry(tr("tool_media_type_is_required_when_providing_media_url"))
    if has_media and not media_url:
        raise ModelRetry(tr("tool_media_url_is_required_when_providing_media_type"))

    chat_id = ctx.deps.chat_id

    if is_raw_mirror_command(text, chat_id) or is_raw_mirror_command(caption, chat_id):
        return SendResult(
            success=False,
            message="Use the admin-only mirror tool for Mirror requests; raw forwarding is disabled.",
        ).text()

    if has_text and getattr(ctx.deps, "mirror_dispatched", False):
        return SendResult(
            success=False,
            message="The mirror request was dispatched. EndTurn without more text; task updates are sent asynchronously.",
        ).text()

    if send_immediately:
        if has_text and getattr(ctx.deps, "image_delivered", False):
            return SendResult(
                success=False,
                message="The generated image was delivered. End the turn without additional text.",
            ).text()
        try:
            if has_text:
                assert text is not None
                if not text_already_sent(ctx.deps, text, markdown=False):
                    result = await ctx.deps.client.send_message(
                        chat_id=chat_id, text=text
                    )
                    await record_tool_reply(ctx.deps, result, text)
                return SendResult(success=True, message=tr("message_sent")).text()
            else:
                assert media_type is not None
                assert media_url is not None
                caption = caption if caption else ""
                match media_type:
                    case "photo":
                        await ctx.deps.client.send_photo(
                            chat_id=chat_id,
                            photo=media_url,
                            caption=caption,
                        )
                    case "video":
                        await ctx.deps.client.send_video(
                            chat_id=chat_id,
                            video=media_url,
                            caption=caption,
                        )
                    case "audio":
                        await ctx.deps.client.send_audio(
                            chat_id=chat_id,
                            audio=media_url,
                            caption=caption,
                        )
                    case "document":
                        await ctx.deps.client.send_document(
                            chat_id=chat_id,
                            document=media_url,
                            caption=caption,
                        )
                return SendResult(
                    success=True, message=tr("media_sent", p0=media_type)
                ).text()
        except Exception as e:
            logger.error(f"Immediate send failed: {e.__class__.__name__}: {e}")
            return SendResult(
                success=False, message=tr("send_operation_failed", p0=e)
            ).text()
    else:
        assert schedule_datetime is not None

        if has_text:
            assert text is not None
            text_content = text
            job_key = (
                f"agent_schedule_msg:{chat_id}:{ctx.deps.user_id}"
                f":{schedule_datetime.timestamp()}"
                f":{md5(text_content.encode()).hexdigest()}"
            )

            common.jobqueue.add_onetime_job(
                job_key,
                run_date=schedule_datetime,
                func=_scheduled_text_job,
                args=[chat_id, text_content],
            )
        else:
            assert media_type is not None
            assert media_url is not None
            _caption = caption if caption else ""
            job_key = (
                f"agent_schedule_media:{chat_id}:{ctx.deps.user_id}"
                f":{schedule_datetime.timestamp()}"
                f":{md5(media_url.encode()).hexdigest()}"
            )

            common.jobqueue.add_onetime_job(
                job_key,
                run_date=schedule_datetime,
                func=_scheduled_media_job,
                args=[chat_id, media_type, media_url, _caption],
            )

        return SendResult(
            success=True, message=tr("scheduled", p0=schedule_datetime.isoformat())
        ).text()


async def send_poll(
    ctx: RunContext[datatype.ContextDeps],
    question: str,
    options: list[str],
    is_anonymous: bool = False,
    allows_multiple_answers: bool = False,
    schedule_time: str | None = None,
) -> str:
    """Send a poll to the current chat.

    Use this tool to create interactive polls with 2-8 options for users to vote on.

    Args:
        question: The poll question text (1-300 characters).
        options: List of 2-8 answer options for the poll.
        is_anonymous: Whether the poll is anonymous.
        allows_multiple_answers: Whether users can select multiple answers.
        schedule_time: Optional ISO 8601 datetime string to schedule delivery,
            e.g. "2025-06-04T15:00:00+07:00". If omitted, sends immediately.
            A timezone-less value uses Asia/Ho_Chi_Minh (UTC+7).
    """
    if ctx.deps.message is None or ctx.deps.chat_id is None:
        return SendResult(success=False, message=tr("context_unavailable")).text()

    schedule_datetime: datetime.datetime | None = None
    if schedule_time is not None:
        try:
            schedule_datetime = _parse_schedule_time(schedule_time)
        except ValueError as e:
            raise ModelRetry(tr("schedule_invalid", p0=e))
        if schedule_datetime < datetime.datetime.now(datetime.UTC):
            raise ModelRetry(tr("tool_schedule_time_must_be_in_the_future"))

    reply_params = pyrogram.types.ReplyParameters(
        message_id=ctx.deps.message.id,
    )
    chat_id = ctx.deps.chat_id

    if not question or not question.strip():
        raise ModelRetry(tr("tool_question_is_required_for_poll"))
    if not options or len(options) < 2:
        raise ModelRetry(tr("tool_options_must_have_at_least_2_items"))
    if len(options) > 10:
        raise ModelRetry(tr("tool_options_must_have_at_most_10_items"))

    if schedule_datetime is not None:
        await _schedule_poll(
            ctx,
            question,
            options,
            is_anonymous,
            allows_multiple_answers,
            schedule_datetime,
            chat_id,
        )
        return SendResult(
            success=True, message=tr("scheduled", p0=schedule_datetime.isoformat())
        ).text()

    try:
        await ctx.deps.client.send_poll(
            chat_id=chat_id,
            question=question,
            options=list(options),  # type: ignore[arg-type]
            is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple_answers,
            reply_parameters=reply_params,
        )
    except Exception as e:
        logger.error(f"send_poll failed: {e.__class__.__name__}: {e}")
        raise ModelRetry(tr("poll_send_failed", p0=e.__class__.__name__, p1=e))

    return SendResult(success=True).text()


async def _schedule_poll(
    ctx: RunContext[datatype.ContextDeps],
    question: str,
    options: list[str],
    is_anonymous: bool,
    allows_multiple_answers: bool,
    schedule_datetime: datetime.datetime,
    chat_id: int,
) -> None:
    job_key = (
        f"agent_send_poll:{chat_id}:{ctx.deps.user_id}"
        f":{schedule_datetime.timestamp()}"
        f":{md5(question.encode()).hexdigest()}"
    )

    common.jobqueue.add_onetime_job(
        job_key,
        run_date=schedule_datetime,
        func=_scheduled_poll_job,
        args=[chat_id, question, options, is_anonymous, allows_multiple_answers],
    )


async def send_sticker(
    ctx: RunContext[datatype.ContextDeps],
    query: str,
) -> str:
    """Search for a semantically matching sticker and send it.

    Args:
        query: Natural language description of the desired sticker, e.g. "happy excited",
               "sad crying", "thumbs up approval".
    """
    if ctx.deps.chat_id is None or ctx.deps.message is None:
        return SendResult(success=False, message=tr("context_unavailable")).text()

    enabled, _ = await expression_settings(ctx.deps.message, ctx.deps.chat_id)
    if not enabled:
        return SendResult(success=False, message=tr("stickers_chat_disabled")).text()
    if "send_sticker" in ctx.deps.tools_called_this_turn:
        return SendResult(success=True).text()

    if not sticker_memory.search_ready():
        return SendResult(
            success=False, message=tr("sticker_memory_unconfigured")
        ).text()

    result = await sticker_memory.select_sticker(
        query, ctx.deps.chat_id, quota.subject_of(ctx.deps.message)
    )
    if not result:
        raise ModelRetry(tr("sticker_no_match"))

    file_id, _description = result
    logger.debug(
        "send_sticker: selected a stored sticker ({})",
        app_config.agent_sticker_search_mode,
    )
    try:
        await ctx.deps.client.send_sticker(
            chat_id=ctx.deps.chat_id,
            sticker=file_id,
            reply_parameters=pyrogram.types.ReplyParameters(
                message_id=ctx.deps.message.id,
            ),
        )
    except Exception as e:
        logger.error(f"send_sticker send error: {e.__class__.__name__}: {e}")
        raise ModelRetry(tr("sticker_send_failed", p0=e.__class__.__name__, p1=e))
    # Suppress periodic hints on later model requests within this same run.
    ctx.deps.tools_called_this_turn.add("send_sticker")
    ctx.deps.side_effects_started = True
    return SendResult(success=True).text()


@dataclass
class Artist:
    name: str
    type: str
    username: str
    uid: str


@dataclass
class AnimePhotoInfo:
    title: str
    source_url: str
    r18: bool
    description: str | None = None
    artist: Artist | None = None
    tags: list[str] | None = None


@dataclass
class AnimePhotoResult:
    success: bool = True
    message: str | None = None
    data: AnimePhotoInfo | None = None
    # Internal capability metadata for the model, never a user-facing sentence.
    random_only: bool = False
    delivery_pending: bool = False


async def _fetch_manyacg_anime_artwork(
    keyword: str = "", r18_mode: int = 0
):
    return await manyacg_candidates(manyacg.httpx_client, keyword, r18_mode)


async def _fetch_anime_artwork(keyword: str = "", r18_mode: int = 0, *, scope=None):
    return await fetch_anime_artwork(
        keyword, r18_mode, _fetch_manyacg_anime_artwork, as_dict=True, scope=scope
    )


async def send_anime_photo(
    ctx: RunContext[datatype.ContextDeps], keyword: str = "", count: int = 1
) -> AnimePhotoResult:
    """Get and send anime photos (also called setu).

    Args:
        keyword: Optional tag/theme to search in ManyACG, e.g. "nekomimi" or
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
            Preserve explicit r18/sfw instructions. Use nekos:neko, nekos:waifu,
            nekos:husbando or nekos:kitsune to select that SFW random source.
        count: How many photos to send, 1-10.
    """
    if ctx.deps.message is None or ctx.deps.message.id is None:
        return AnimePhotoResult(
            success=False, message=tr("current_context_unavailable")
        )
    if ctx.deps.chat_id is None:
        return AnimePhotoResult(
            success=False, message=tr("current_context_unavailable")
        )
    enabled, image_mode = await telegram_image_settings(ctx.deps.chat_id)
    if not enabled:
        return AnimePhotoResult(success=False, message=tr("anime_chat_disabled"))
    request = resolve_image_search(
        keyword, image_mode,
        request_text=message_plain_text(ctx.deps.message),
    )
    if request is None:
        return AnimePhotoResult(success=False, message="The requested Safe/R18 rating is not permitted by this conversation's image mode.")
    keyword, image_mode = request
    fetched = []
    deferred = False
    try:
        ratekey = f"anime_photo_rate_limit:{ctx.deps.chat_id}:{ctx.deps.user_id}"
        if await common.memttlcache.get(ratekey, 0) > 3:
            return AnimePhotoResult(
                success=False,
                message=tr("requests_too_frequent"),
            )
        current_count = await common.memttlcache.get(ratekey, 0)
        await common.memttlcache.set(ratekey, current_count + 1, ttl=10)

        count = max(1, min(10, count))
        for _ in range(count):
            try:
                item = await _fetch_anime_artwork(
                    keyword, r18_mode=image_mode, scope=f"telegram:{ctx.deps.chat_id}"
                )
            except NoFreshImageError:
                if fetched:
                    break
                return AnimePhotoResult(success=False, message=(
                    "The available images duplicate the last image or images currently being sent. "
                    "Try a different tag or try again later."
                ))
            if item is None:
                break
            if not image_allowed(item[0].get("r18"), image_mode):
                break
            fetched.append(item)
        if not fetched:
            return AnimePhotoResult(success=False, message=tr("anime_fetch_failed"))

        queue = getattr(ctx.deps, "deferred_images", None)
        if queue is not None:
            async def cleanup():
                for artwork, picture in fetched:
                    await release_anime_image(f"telegram:{ctx.deps.chat_id}", artwork, picture)

            queue.add(lambda: _deliver_anime_photos(ctx, fetched), cleanup)
            deferred = True
            artwork, _ = fetched[0]
            return AnimePhotoResult(
                delivery_pending=True,
                data=AnimePhotoInfo(
                    title=artwork["title"], source_url=artwork["source_url"],
                    r18=artwork["r18"], tags=artwork.get("tags", [])[:10],
                ),
            )
        return await _deliver_anime_photos(ctx, fetched)
    except Exception as e:
        logger.error(f"send_anime_photo error: {e.__class__.__name__}:{e}")
        return AnimePhotoResult(
            success=False,
            message=tr("operation_error", p0=e.__class__.__name__),
        )
    finally:
        if not deferred:
            for artwork, picture in fetched:
                await release_anime_image(f"telegram:{ctx.deps.chat_id}", artwork, picture)


async def _deliver_anime_photos(ctx, fetched):
    enabled, mode = await telegram_image_settings(ctx.deps.chat_id)
    if not enabled or any(not image_allowed(artwork["r18"], mode) for artwork, _ in fetched):
        return AnimePhotoResult(success=False, message="Image settings changed before delivery; the prepared images are no longer permitted.")
    if len(fetched) == 1:
        artwork, picture = fetched[0]
        result = await _send_anime_photo_single(ctx, artwork, picture)
        if result.success:
            ctx.deps.side_effects_started = True
            await remember_anime_image(f"telegram:{ctx.deps.chat_id}", artwork, picture)
        return result

    media: list[
        pyrogram.types.InputMediaPhoto
        | pyrogram.types.InputMediaVideo
        | pyrogram.types.InputMediaAudio
        | pyrogram.types.InputMediaDocument
    ] = [
        pyrogram.types.InputMediaPhoto(
            media=picture["regular"],
            caption=(
                f"<a href='{html.escape(artwork['source_url'], quote=True)}'>{html.escape(artwork['title'][:200])}</a>"
                if i == 0
                else ""
            ),
            parse_mode=pyrogram.enums.ParseMode.HTML if i == 0 else None,
            has_spoiler=artwork["r18"],
        )
        for i, (artwork, picture) in enumerate(fetched)
    ]
    await ctx.deps.client.send_media_group(
        chat_id=ctx.deps.chat_id,
        media=media,
        reply_parameters=pyrogram.types.ReplyParameters(
            message_id=ctx.deps.message.id,
        ),
    )
    ctx.deps.side_effects_started = True
    for artwork, picture in fetched:
        await remember_anime_image(f"telegram:{ctx.deps.chat_id}", artwork, picture)
    first_artwork, _ = fetched[0]
    return AnimePhotoResult(
        success=True,
        data=AnimePhotoInfo(
            title=first_artwork["title"],
            source_url=first_artwork["source_url"],
            r18=first_artwork["r18"],
            description=first_artwork.get("description", "")[:512],
            artist=Artist(
                name=first_artwork.get("artist", {}).get("name", ""),
                type=first_artwork["artist"].get("type", ""),
                username=first_artwork["artist"].get("username", ""),
                uid=first_artwork["artist"].get("uid", ""),
            ),
            tags=first_artwork.get("tags", [])[:10],
        ),
    )


async def _send_anime_photo_single(
    ctx: RunContext[datatype.ContextDeps],
    artwork: dict,
    picture: dict,
) -> AnimePhotoResult:
    user_config = await database.get_user_config(ctx.deps.user_id)
    lang = user_config.lang
    detail_link = (
        f"https://t.me/{app_config.manyacg_channel}/{picture['message_id']}"
        if picture.get("message_id")
        else artwork["source_url"]
    )
    await ctx.deps.client.send_photo(
        chat_id=ctx.deps.chat_id,
        photo=picture["regular"],
        caption=f"<a href='{html.escape(artwork['source_url'], quote=True)}'>{html.escape(artwork['title'][:200])}</a>",
        parse_mode=pyrogram.enums.ParseMode.HTML,
        reply_markup=pyrogram.types.InlineKeyboardMarkup(
            [
                [
                    pyrogram.types.InlineKeyboardButton(
                        text=i18n.t("bot.button.manyacg.detail", locale=lang),
                        url=detail_link,
                    ),
                    pyrogram.types.InlineKeyboardButton(
                        text=i18n.t("bot.button.manyacg.original", locale=lang),
                        url=original_image_url(artwork, picture),
                    ),
                ]
            ]
        ),
        has_spoiler=artwork["r18"],
        reply_parameters=pyrogram.types.ReplyParameters(
            message_id=ctx.deps.message.id,
        ),
    )
    return AnimePhotoResult(
        success=True,
        data=AnimePhotoInfo(
            title=artwork["title"],
            source_url=artwork["source_url"],
            r18=artwork["r18"],
            description=artwork.get("description", "")[:512],
            artist=Artist(
                name=artwork.get("artist", {}).get("name", ""),
                type=artwork["artist"].get("type", ""),
                username=artwork["artist"].get("username", ""),
                uid=artwork["artist"].get("uid", ""),
            ),
            tags=artwork.get("tags", [])[:10],
        ),
    )


async def _send_sticker_checked(
    ctx: RunContext[datatype.ContextDeps], query: str
) -> str:
    """Send a sticker after the availability checks."""
    if not sticker_memory.search_ready():
        return tr("sticker_memory_disabled")
    enabled, _ = await expression_settings(ctx.deps.message, ctx.deps.chat_id)
    if not enabled:
        return tr("stickers_chat_disabled")
    try:
        sticker_count = await sticker_vec.count(ctx.deps.chat_id)
    except Exception as e:
        logger.warning(
            f"Failed to check sticker count for chat {ctx.deps.chat_id}: {e}"
        )
        return tr("sticker_availability_failed", p0=e)
    if sticker_count < 1:
        return tr("stickers_insufficient")
    return await send_sticker(ctx, query)


__all__ = ["send_anime_photo", "send_sticker"]
