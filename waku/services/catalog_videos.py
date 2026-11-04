"""Single-video retrieval from Waku API, sharing catalog rating/tag policy."""

import io
from dataclasses import dataclass

import httpx
import pyrogram

from waku.common.safe_http import safe_download_bytes
from waku.config import app_config
from waku.services.anime_images import (
    _requested_tags,
    _web_url,
    enabled_image_sources,
    enforce_explicit_tags,
    resolve_image_search,
)
from waku.services.telegram_images import image_allowed

MAX_VIDEO_BYTES = 20 * 1024 * 1024


@dataclass
class VideoResult:
    success: bool = True
    message: str | None = None
    delivery_pending: bool = False
    prepared_count: int = 0
    sent_count: int = 0


class VideoError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def catalog_notice(kind="video", keyword="", *, lang="vi", code="no_match"):
    """Command notices distinguish an empty search from a service failure."""
    en = lang == "en"
    noun = (
        ("video" if kind == "video" else "image")
        if en
        else ("video" if kind == "video" else "ảnh")
    )
    if code == "not_configured":
        return (
            "Waku API is not configured or enabled for videos."
            if en
            else "Chưa bật hoặc cấu hình Waku API để lấy video."
        )
    if code == "policy":
        return (
            "This request conflicts with the chat's Safe/R18 setting."
            if en
            else "Yêu cầu này không phù hợp với chế độ Safe/R18 của cuộc chat."
        )
    if code == "unavailable":
        return (
            "The media service is temporarily unavailable. Please try again later."
            if en
            else "Dịch vụ đang gặp lỗi. Thử lại sau nhé."
        )
    if keyword.strip():
        query = keyword.strip()[:160]
        return (
            f"No {noun} matches “{query}” in the current Safe/R18 mode."
            if en
            else f"Không có {noun} phù hợp với “{query}” trong chế độ Safe/R18 hiện tại."
        )
    return (
        f"No {noun} is available in the current Safe/R18 mode."
        if en
        else f"Chưa có {noun} phù hợp trong chế độ Safe/R18 hiện tại."
    )


async def fetch_catalog_video(keyword="", mode=2, image_type=None, *, request_text=""):
    if "waku" not in enabled_image_sources():
        raise VideoError("not_configured")
    resolved = resolve_image_search(keyword, mode, request_text=request_text)
    if resolved is None:
        raise VideoError("policy")
    query, effective_mode = resolved
    requested = [tag.removeprefix("#") for tag in _requested_tags(enforce_explicit_tags(query, request_text))]
    base = app_config.waku_api_url.strip().rstrip("/")
    params = {
        "media_type": "video",
        "count": 1,
        "rating": {0: "safe", 1: "r18", 2: "mixed"}[effective_mode],
    }
    if requested:
        params["tags"] = ",".join(requested)
    if image_type:
        params["image_type"] = image_type
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
            response = await client.get(
                base
                + ("/images/random" if base.endswith("/v1") else "/v1/images/random"),
                params=params,
                headers={"Authorization": "Bearer " + app_config.waku_api_key.strip()},
            )
            response.raise_for_status()
            rows = response.json().get("images", [])
        for row in rows:
            if (
                not isinstance(row, dict)
                or not str(row.get("mime_type", "")).startswith("video/")
                or not _web_url(row.get("url"))
                or row.get("rating") not in {"safe", "r18"}
                or not image_allowed(row["rating"] == "r18", effective_mode)
                or any(tag not in row.get("tags", []) for tag in requested)
                or image_type
                and row.get("image_type") != image_type
                or not 0 < int(row.get("size", 0)) <= MAX_VIDEO_BYTES
            ):
                continue
            return row
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
        raise VideoError("unavailable") from error
    raise VideoError("no_match")


async def prepare_video(row, *, limit=MAX_VIDEO_BYTES):
    if row["size"] > limit:
        raise ValueError("Video exceeds this channel's attachment size limit.")
    data = await safe_download_bytes(
        row["url"], max_bytes=min(limit, MAX_VIDEO_BYTES), timeout=60
    )
    file = io.BytesIO(data)
    file.name = row.get("filename") or "video.mp4"
    return file


async def send_telegram_video(message, row, file):
    buttons = []
    if source := _web_url(row.get("source_url")):
        buttons.append(pyrogram.types.InlineKeyboardButton("Bản gốc", url=source))
    if detail := _web_url(row.get("telegram_file_url")):
        buttons.append(pyrogram.types.InlineKeyboardButton("Chi tiết", url=detail))
    return await message.reply_video(
        video=file,
        caption=str(row.get("title") or "")[:1024],
        parse_mode=pyrogram.enums.ParseMode.DISABLED,
        supports_streaming=True,
        has_spoiler=row["rating"] == "r18",
        reply_markup=pyrogram.types.InlineKeyboardMarkup([buttons])
        if buttons
        else None,
    )
