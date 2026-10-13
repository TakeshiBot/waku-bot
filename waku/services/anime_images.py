"""Shared anime search, rating filters and source adapters for both platforms."""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from waku.common.http import _clients
from waku.config import app_config
from waku.logger import logger
from waku.services.image_requests import contains_r18_request
from waku.services.manyacg import Artist, Artwork, Picture
from waku.services.telegram_images import image_allowed, normalize_image_mode

_API_URL = "https://nekos.best/api/v2"
_USER_AGENT = "WakuBot (https://github.com/TakeshiBot/waku-bot)"
_categories: dict[str, str] = {}
_catalog_expires = 0.0
_catalog_lock = asyncio.Lock()
_search_lock = asyncio.Lock()
_next_search = 0.0
_blocked_until = 0.0
_RATING_MARKER = re.compile(r"\b(?:r[ -]?18|nsfw|sfw)\b|18\+|🔞", re.IGNORECASE)
_SAFE_MARKER = re.compile(r"\bsfw\b|\ban toàn\b|\ban toan\b", re.IGNORECASE)
_SAFE_NEGATION = re.compile(
    r"\b(?:no|not|non|without|không|khong|ko)\s*-?\s*(?:r[ -]?18|nsfw)\b",
    re.IGNORECASE,
)


def resolve_image_search(keyword, mode, *, request_text=""):
    """Separate rating instructions from character names without relaxing policy."""
    mode = normalize_image_mode(mode)
    keyword = unicodedata.normalize("NFC", keyword or "").strip()
    keyword = re.sub(r"\br[ -]18\b", "r18", keyword, flags=re.IGNORECASE)
    rating_text = re.sub(r"\br[ -]18\b", "r18", request_text or keyword, flags=re.IGNORECASE)
    adult = contains_r18_request(rating_text) or contains_r18_request(keyword)
    safe = any(
        _SAFE_MARKER.search(text) or _SAFE_NEGATION.search(text)
        for text in (rating_text, keyword)
    )
    if adult and (mode == 0 or safe):
        return None
    if safe and mode == 1:
        return None
    effective_mode = 1 if adult else 0 if safe else mode
    query = _SAFE_NEGATION.sub(" ", keyword)
    query = _SAFE_MARKER.sub(" ", query)
    query = _RATING_MARKER.sub(" ", query)
    query = re.sub(r"\s+", " ", query).strip(" ,;")
    return query, effective_mode


def _search_tokens(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.findall(r"[^\W_]+", value)


def artwork_matches_keyword(artwork, keyword):
    """Require name/tag evidence; semantic proximity alone is not a match."""
    tokens = _search_tokens(keyword)
    if not tokens:
        return not keyword.strip()
    fields = [_value(artwork, "title", ""), *(_value(artwork, "tags", []) or [])]
    for token in tokens:
        if not any(
            token in _search_tokens(field)
            or (not token.isascii() and token in " ".join(_search_tokens(field)))
            for field in fields
        ):
            return False
    return True


async def search_manyacg_artworks(client, keyword, mode):
    """Prefer literal matches, then validate candidates from semantic search."""
    keyword = keyword.replace("_", " ").strip()
    # The API limits keywords to 50 characters. Never silently drop constraints.
    if not keyword or len(keyword) > 50:
        return []
    variants = [False, True] if app_config.manyacg_hybrid_search else [False]
    for hybrid in variants:
        response = await client.get(
            "/artwork/list",
            params={"keyword": keyword, "r18": mode, "hybrid": hybrid, "page_size": 30},
        )
        if response.status_code != 200:
            continue
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status", 200) != 200:
            continue
        items = payload.get("data")
        if not isinstance(items, list):
            continue
        candidates = [
            item for item in items
            if isinstance(item, dict)
            and image_allowed(item.get("r18"), mode)
            and item.get("pictures")
            and artwork_matches_keyword(item, keyword)
        ]
        if candidates:
            return candidates
    return []


def _client() -> httpx.AsyncClient:
    client = _clients.get("nekos.best")
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            base_url=_API_URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=httpx.Timeout(10, connect=5),
        )
        _clients["nekos.best"] = client
    return client


async def _request(path: str, params=None):
    global _blocked_until
    if time.monotonic() < _blocked_until:
        return None
    try:
        response = await _client().get(path, params=params)
        if response.status_code == 429:
            delay = 30.0
            try:
                reset = datetime.fromisoformat(
                    response.headers["x-rate-limit-reset"].replace("Z", "+00:00")
                )
                delay = max(1, min(300, reset.timestamp() - time.time()))
            except (KeyError, ValueError):
                pass
            _blocked_until = time.monotonic() + delay
            logger.warning(
                "nekos.best rate limited; temporarily using the other image source"
            )
            return None
        if response.status_code != 200:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception as error:
        logger.warning("nekos.best request failed: {}", type(error).__name__)
        return None


async def _image_categories() -> dict[str, str]:
    global _categories, _catalog_expires
    async with _catalog_lock:
        if time.monotonic() < _catalog_expires:
            return _categories
        data = await _request("/endpoints")
        if data is None:
            _catalog_expires = time.monotonic() + 30
            return _categories
        _categories = {
            key: value["format"].lower()
            for key, value in data.items()
            if isinstance(key, str)
            and key.isascii()
            and key.isalpha()
            and isinstance(value, dict)
            and str(value.get("format", "")).lower() in {"png", "jpg", "jpeg", "webp"}
        }
        _catalog_expires = time.monotonic() + 6 * 3600
        return _categories


def _web_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
    ):
        return value
    return None


async def _fetch_nekos_artwork(keyword="") -> tuple[Artwork, Picture] | None:
    global _next_search
    catalog = await _image_categories()
    if not catalog:
        return None
    keyword = keyword.strip()[:200]
    category = keyword.casefold()
    if not keyword or category in catalog:
        category = category if category in catalog else random.choice(list(catalog))
        data = await _request("/" + category, {"amount": 1})
    else:
        # /search allows 7 requests / 5 seconds. Share this pacing across chats.
        async with _search_lock:
            await asyncio.sleep(max(0, _next_search - time.monotonic()))
            _next_search = time.monotonic() + 0.75
            data = await _request("/search", {"query": keyword, "type": 1, "amount": 1})
    results = data.get("results") if data else None
    if not isinstance(results, list):
        return None
    for result in results[:20]:
        if not isinstance(result, dict):
            continue
        url = _web_url(result.get("url"))
        if not url:
            continue
        asset = urlsplit(url)
        segments = asset.path.strip("/").split("/")
        if (
            asset.hostname != "nekos.best"
            or len(segments) != 4
            or segments[:2] != ["api", "v2"]
        ):
            continue
        category = segments[2]
        if category not in catalog or not segments[3].lower().endswith(
            "." + catalog[category]
        ):
            continue
        identity = "nekos-" + hashlib.sha256(url.encode()).hexdigest()[:24]
        dimensions = result.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            dimensions = {}

        def dimension(key):
            value = dimensions.get(key)
            return value if type(value) is int and 0 <= value <= 100000 else 0

        artist_name = str(result.get("artist_name") or "")[:200]
        picture = Picture(
            id=identity,
            width=dimension("width"),
            height=dimension("height"),
            index=0,
            hash=identity,
            thumb_hash="",
            file_name=segments[3],
            thumbnail=url,
            regular=url,
        )
        artwork = Artwork(
            id=identity,
            created_at="",
            title=(category.title() + (" · " + artist_name if artist_name else ""))[
                :256
            ],
            description="",
            tags=[category],
            source_url=_web_url(result.get("source_url")) or url,
            source_type="nekos.best",
            r18=False,
            artist=Artist(
                id="", name=artist_name, username="", uid="", type="nekos.best"
            ),
            pictures=[picture],
        )
        return artwork, picture
    return None


def _value(obj, key, default=None):
    return (
        obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    )


def original_image_url(artwork, picture) -> str:
    if _value(artwork, "source_type") == "nekos.best":
        return _value(picture, "regular")
    picture_id = _value(picture, "id")
    return (
        f"https://t.me/{app_config.manyacg_bot}/?start=file_{picture_id}"
        if picture_id
        else _value(artwork, "source_url")
    )


async def fetch_anime_artwork(keyword, r18_mode, manyacg_fetch, *, as_dict=False):
    request = resolve_image_search(keyword, r18_mode)
    if request is None:
        return None
    keyword, mode = request
    if len(keyword) > 200:
        return None
    forced_nekos = keyword.lower().startswith(("nekos:", "nekos.best:"))
    if forced_nekos:
        if mode == 1:
            return None
        keyword = keyword.partition(":")[2].strip()
        providers = ["nekos"]
    elif mode == 1:
        providers = ["manyacg"]
    else:
        # nekos.best has artist/source metadata, not a character-tag index.
        # Do not use it as a substitute for a named-character search.
        providers = ["manyacg"]
        if not keyword or keyword.casefold() in {"neko", "waifu", "husbando", "kitsune"}:
            providers.append("nekos")
        if not keyword:
            random.shuffle(providers)
    for provider in providers:
        try:
            item = (
                await _fetch_nekos_artwork(keyword)
                if provider == "nekos"
                else await manyacg_fetch(keyword, mode)
            )
            if item is None or not image_allowed(_value(item[0], "r18"), mode):
                continue
            if provider == "manyacg" and not artwork_matches_keyword(item[0], keyword):
                continue
            if provider == "nekos" and as_dict:
                return item[0].model_dump(), item[1].model_dump()
            return item
        except Exception as error:
            logger.warning("{} image fetch failed: {}", provider, type(error).__name__)
    return None
