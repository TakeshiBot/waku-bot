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
from waku.common.memory_store import memttlcache
from waku.config import app_config
from waku.logger import logger
from waku.services.anime_characters import (
    AmbiguousCharacterError,
    matches_name,
    resolve_character,
)
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
_pools: dict[tuple[str, int], tuple[float, list[dict]]] = {}
_selection_lock = asyncio.Lock()
_HISTORY_TTL = 7 * 24 * 3600


class NoFreshImageError(Exception):
    """Matching results exist, but their artwork/content has already been selected here."""


async def conversation_image_keyword(keyword, scope, request_text=""):
    """Keep a failed named request for follow-ups instead of silently switching to random."""
    key = "anime-query:" + scope
    keyword = (keyword or "").strip()
    random_request = re.search(
        r"\b(?:random|ngẫu nhiên|ngau nhien|bất kỳ|bat ky|anything)\b",
        request_text,
        re.I,
    )
    if random_request and keyword.casefold() in {
        "random",
        "ngẫu nhiên",
        "ngau nhien",
        "bất kỳ",
        "bat ky",
    }:
        keyword = ""
    if random_request and not keyword:
        await memttlcache.delete(key)
        return ""
    previous = await memttlcache.get(key, "")
    if not isinstance(previous, str):
        previous = ""
    normalized = resolve_image_search(keyword, 2)
    if previous and keyword and normalized and not normalized[0] and not random_request:
        # "R18 instead" refers to the same character, not random R18 art.
        keyword = previous + " " + keyword
    follow_up = re.search(
        r"\b(?:lấy nhanh|lay nhanh|gửi tiếp|gui tiep|lấy tiếp|lay tiep|thử lại|thu lai|ảnh khác|anh khac|retry|again)\b",
        request_text,
        re.I,
    )
    if (
        previous
        and not random_request
        and (
            not keyword
            or (
                follow_up
                and not artwork_matches_keyword({"title": request_text}, keyword)
            )
        )
    ):
        keyword = previous
    if keyword:
        normalized = resolve_image_search(keyword, 2)
        if normalized and normalized[0]:
            # Rating is selected anew for each request; only remember the character/tag.
            await memttlcache.set(key, normalized[0], ttl=1800)
    return keyword


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
    rating_text = re.sub(
        r"\br[ -]18\b", "r18", request_text or keyword, flags=re.IGNORECASE
    )
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
    """Resolve full/native names, collect pages, then validate every result's metadata."""
    keyword = keyword.replace("_", " ").strip()
    if not keyword or len(keyword) > 100:
        return []
    cache_key = (keyword.casefold(), mode)
    cached = _pools.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    character = await resolve_character(keyword)
    aliases = list(character.aliases) if character else []
    queries = [keyword]
    if character:
        queries.extend(
            filter(
                None,
                (
                    character.native,
                    character.full.replace(" ", "_"),
                    "_".join(reversed(character.full.split())),
                ),
            )
        )
    candidates, identities = [], set()

    def matches(item):
        if character:
            fields = [item.get("title", ""), *(item.get("tags") or [])]
            return any(
                matches_name(str(field), alias) for field in fields for alias in aliases
            )
        return artwork_matches_keyword(item, keyword)

    async def collect(query, hybrid=False):
        page_ids = set()
        for page in range(1, 5):
            params = {"keyword": query, "r18": mode, "hybrid": hybrid, "page_size": 50}
            if page > 1:
                params["page"] = page
            try:
                response = await client.get("/artwork/list", params=params)
                payload = response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError) as error:
                logger.debug("Anime search alias failed: {}", type(error).__name__)
                break
            if response.status_code != 200:
                break
            if not isinstance(payload, dict) or payload.get("status", 200) != 200:
                break
            items = payload.get("data")
            if not isinstance(items, list):
                break
            new = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                identity = item.get("id") or item.get("source_url") or repr(item)
                if identity in page_ids:
                    continue
                page_ids.add(identity)
                new += 1
                if (
                    identity not in identities
                    and image_allowed(item.get("r18"), mode)
                    and item.get("pictures")
                    and matches(item)
                ):
                    identities.add(identity)
                    candidates.append(item)
            if len(items) < 50 or not new or len(candidates) >= 200:
                break

    # Public API deployments differ in operator support. Query aliases separately.
    for query in dict.fromkeys(queries):
        await collect(query)
        if len(candidates) >= 200:
            break
    if character:
        related = list(
            dict.fromkeys(
                tag
                for item in candidates
                for tag in character.related_tags(item)
                if tag not in aliases
            )
        )[:4]
        if related:
            aliases.extend(related)
            for tag in related:
                await collect(tag)
    if not candidates and app_config.manyacg_hybrid_search:
        await collect(keyword, hybrid=True)
    logger.debug(
        "Anime search: query={!r}, character={!r}, matching_artworks={}",
        keyword,
        character.full if character else None,
        len(candidates),
    )
    if len(_pools) >= 128:
        _pools.pop(next(iter(_pools)))
    _pools[cache_key] = time.monotonic() + (300 if candidates else 15), candidates
    return candidates


async def manyacg_candidates(client, keyword, mode):
    """All valid candidates; choose once in the shared selector, never in each adapter."""
    if keyword:
        return await search_manyacg_artworks(client, keyword, mode)
    response = await client.get("/artwork/random", params={"r18": mode, "limit": 10})
    if response.status_code != 200:
        return []
    payload = response.json()
    items = payload.get("data") if isinstance(payload, dict) else None
    return [
        item
        for item in items or []
        if isinstance(item, dict)
        and image_allowed(item.get("r18"), mode)
        and item.get("pictures")
    ]


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


def _identity_keys(artwork, picture):
    values = [
        ("art", _value(artwork, "id")),
        ("source", _value(artwork, "source_url")),
        ("picture", _value(picture, "id")),
        ("url", _value(picture, "regular")),
    ]
    fingerprint = _value(picture, "hash", "")
    if fingerprint and fingerprint.strip("p:0"):
        values.append(("hash", fingerprint))
    return [
        kind + ":" + hashlib.sha256(str(value).encode()).hexdigest()[:32]
        for kind, value in values
        if value
    ]


async def _select_fresh(items, mode, scope, as_dict):
    cache_key = "anime-recent:" + (scope or "")
    async with _selection_lock:
        history = await memttlcache.get(cache_key, []) if scope else []
        if not isinstance(history, list):
            history = []
        reserved = await memttlcache.get("anime-reserved:" + scope, []) if scope else []
        if not isinstance(reserved, list):
            reserved = []
        seen = set(history + reserved)
        candidates = list(items)
        random.shuffle(candidates)
        for artwork, fallback in candidates:
            if not image_allowed(_value(artwork, "r18"), mode):
                continue
            pictures = list(
                _value(artwork, "pictures", []) or ([fallback] if fallback else [])
            )
            random.shuffle(pictures)
            for picture in pictures:
                keys = _identity_keys(artwork, picture)
                if scope and seen.intersection(keys):
                    continue
                if as_dict:
                    selected = (
                        artwork.model_dump()
                        if isinstance(artwork, Artwork)
                        else artwork,
                        picture.model_dump()
                        if isinstance(picture, Picture)
                        else picture,
                    )
                elif isinstance(artwork, dict):
                    selected = (
                        Artwork.model_validate(artwork),
                        Picture.model_validate(picture),
                    )
                else:
                    selected = artwork, picture
                if scope:
                    # Reserve before returning so concurrent calls and albums cannot select it twice.
                    await memttlcache.set(
                        "anime-reserved:" + scope, (reserved + keys)[-512:], ttl=300
                    )
                return selected
    return None


async def remember_anime_image(scope, artwork, picture):
    """Only successful deliveries enter the long-lived history; failures just reserve briefly."""
    try:
        async with _selection_lock:
            key = "anime-recent:" + scope
            history = await memttlcache.get(key, [])
            if not isinstance(history, list):
                history = []
            await memttlcache.set(
                key,
                (history + _identity_keys(artwork, picture))[-512:],
                ttl=_HISTORY_TTL,
            )
    except Exception as error:
        # Delivery already succeeded. Never tell the caller to retry a delivered image.
        logger.warning("Anime delivery history update failed: {}", type(error).__name__)


async def fetch_anime_artwork(
    keyword, r18_mode, manyacg_fetch, *, as_dict=False, scope=None
):
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
        if not keyword or keyword.casefold() in {
            "neko",
            "waifu",
            "husbando",
            "kitsune",
        }:
            providers.append("nekos")
        if not keyword:
            random.shuffle(providers)
    repeated = False
    attempts = [
        provider
        for provider in providers
        for _ in range(3 if provider == "nekos" and scope else 1)
    ]
    for provider in attempts:
        try:
            item = (
                await _fetch_nekos_artwork(keyword)
                if provider == "nekos"
                else await manyacg_fetch(keyword, mode)
            )
            if not item:
                continue
            if isinstance(item, list):
                choices = [(artwork, None) for artwork in item]
            elif image_allowed(_value(item[0], "r18"), mode):
                if provider == "manyacg" and not artwork_matches_keyword(
                    item[0], keyword
                ):
                    continue
                choices = [item]
            else:
                continue
            selected = await _select_fresh(choices, mode, scope, as_dict)
            if selected is not None:
                # Keep the legacy tuple identity for callers not asking for dict conversion.
                return (
                    item
                    if isinstance(item, tuple) and not as_dict and selected == item
                    else selected
                )
            repeated = repeated or bool(choices)
        except AmbiguousCharacterError:
            raise
        except Exception as error:
            logger.warning("{} image fetch failed: {}", provider, type(error).__name__)
    if repeated and scope:
        raise NoFreshImageError(
            "No unseen matching artwork remains in this conversation"
        )
    return None
