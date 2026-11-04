from typing import Literal

from pydantic_ai import RunContext

from waku.common.rich_message import message_plain_text
from waku.plugins.agent import datatype
from waku.services.catalog_videos import (
    VideoError,
    VideoResult,
    catalog_notice,
    fetch_catalog_video,
    prepare_video,
    send_telegram_video,
)
from waku.services.telegram_images import telegram_image_settings


async def send_catalog_video(
    ctx: RunContext[datatype.ContextDeps],
    keyword: str = "",
    image_type: Literal["anime", "real"] | None = None,
) -> VideoResult:
    """Prepare ONE video from Waku API, only when the user asks to send a video/clip.

    Empty keyword means random. Otherwise pass requested exact tag(s), comma
    separated; preserve r18/sfw in keyword. image_type is an optional explicitly
    requested visual style. Videos have manual metadata, no AI analysis. Never
    use mirror or the photo tool for a catalog video request. A delivery_pending
    result means write your normal reply now; the video is sent after it. A
    failure is not a successful delivery: explain it naturally to the user.
    """
    message = ctx.deps.message
    if message is None or ctx.deps.chat_id is None:
        return VideoResult(success=False, message="Current chat is unavailable.")
    enabled, mode = await telegram_image_settings(ctx.deps.chat_id)
    if not enabled:
        return VideoResult(
            success=False, message="Catalog media is disabled for this chat."
        )
    try:
        row = await fetch_catalog_video(
            keyword, mode, image_type, request_text=message_plain_text(message)
        )
        file = await prepare_video(row)
    except VideoError as error:
        return VideoResult(
            success=False,
            message=catalog_notice(keyword=keyword, code=error.code, lang="en"),
        )
    except Exception:
        return VideoResult(
            success=False,
            message="Video download failed; no video was prepared or sent.",
        )

    async def deliver():
        await send_telegram_video(message, row, file)
        return VideoResult(sent_count=1)

    async def cleanup():
        file.close()

    if queue := ctx.deps.deferred_images:
        queue.add(deliver, cleanup)
        return VideoResult(delivery_pending=True, prepared_count=1)
    try:
        return await deliver()
    finally:
        await cleanup()


__all__ = ["send_catalog_video"]
