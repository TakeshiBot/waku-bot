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
from waku.services.image_requests import contains_r18_request
from waku.services.manyacg import Artist, Artwork, Picture
from waku.services.telegram_images import image_allowed, normalize_image_mode

_API_URL = "https://nekos.best/api/v2"
_USER_AGENT = "WakuBot (https://github.com/TakeshiBot/waku-bot)"
_categories: dict[str, str] = {}
_catalog_expires = 0.0
_catalog_lock = asyncio.Lock()
_blocked_until = 0.0
_selection_lock = asyncio.Lock()
_HISTORY_TTL = 24 * 3600
_HISTORY_SIZE = 50


class NoFreshImageError(Exception):
    """Results contain only the last delivered image or images still being sent."""


class ImageServicesNotConfiguredError(Exception):
    """No enabled, configured image catalog. Never use an undeclared fallback."""


def enabled_image_sources():
    sources = []
    if app_config.manyacg_enabled and app_config.manyacg_api_url.strip():
        sources.append("manyacg")
    if (
        app_config.waku_api_enabled
        and app_config.waku_api_url.strip()
        and (app_config.waku_api_key or "").strip()
    ):
        sources.append("waku")
    return sources


def image_services_notice(lang="vi"):
    return (
        "No image service is configured for this bot."
        if lang == "en" else "Bot chưa được cấu hình dịch vụ ảnh."
    )


IMAGE_SERVICES_UNAVAILABLE = (
    "No image API service is enabled/configured. Explain this naturally to the user in your own words. "
    "No image was sent. Do not claim success, generate an image or switch to web search unless requested."
)


def _requested_tags(keyword):
    aliases = {"nekomimi": "cat_ears", "cat ears": "cat_ears", "tai mèo": "cat_ears", "hầu gái": "maid"}
    return [aliases.get(part.strip().casefold(), part.strip().replace(" ", "_").lower())
            for part in keyword.split(",") if part.strip()]


def enforce_explicit_tags(keyword, request_text):
    """Do not lose a tag explicitly named by the user during AI tool selection."""
    cleaned = re.sub(r"(?<!\w)(?:tag|thẻ)\s*[:=]?\s*#?([\w()]+)", r"\1", keyword or "", flags=re.I)
    tags = _requested_tags(cleaned)
    for match in re.finditer(r"(?<!\w)(?:tag|thẻ)\s*[:=]?\s*#?([\w()]+)", request_text or "", re.I):
        tag = match.group(1).casefold()
        if tag not in tags:
            tags.append(tag)
    return ",".join(tags)


def _subject_terms(subject):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", subject or "").casefold())


def _subject_matches(artwork, subject):
    """Match catalog metadata, without inventing character identity or aliases."""
    terms = _subject_terms(subject)
    available = set(_subject_terms(" ".join([
        str(_value(artwork, "title", "") or ""),
        *[tag for tag in (_value(artwork, "tags", []) or []) if isinstance(tag, str)],
    ])))
    return bool(terms) and all(term in available for term in terms)


def anime_search_matches(artwork, keyword, subject=""):
    available = {str(tag).casefold() for tag in (_value(artwork, "tags", []) or []) if isinstance(tag, str)}
    return (not subject.strip() or _subject_matches(artwork, subject)) and all(
        tag in available for tag in _requested_tags(keyword)
    )


async def waku_candidates(keyword, mode, image_type=None, *, subject=""):
    base = app_config.waku_api_url.strip().rstrip("/")
    endpoint = base + ("/images/random" if base.endswith("/v1") else "/v1/images/random")
    client = _clients.get("waku-images")
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=5), follow_redirects=False
        )
        _clients["waku-images"] = client
    params = {"rating": {0: "safe", 1: "r18", 2: "mixed"}[mode], "count": 10}
    if keyword.strip():
        params["tags"] = ",".join(_requested_tags(keyword))
    if image_type:
        params["image_type"] = image_type
    if subject.strip():
        params["q"] = subject.strip()
    response = await client.get(
        endpoint, params=params,
        headers={"Authorization": "Bearer " + app_config.waku_api_key.strip()},
    )
    if response.status_code != 200:
        logger.warning("Waku image API returned HTTP {}", response.status_code)
        return []
    payload = response.json()
    rows = payload.get("images", []) if isinstance(payload, dict) else []
    items = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("rating") not in {"safe", "r18"}:
            continue
        if not image_allowed(row["rating"] == "r18", mode) or (image_type and row.get("image_type") != image_type):
            continue
        if any(tag not in {str(t).casefold() for t in row.get("tags", []) if isinstance(t, str)} for tag in _requested_tags(keyword)):
            continue
        if subject and not _subject_matches(row, subject):
            continue
        url = _web_url(row.get("url"))
        if not url or not row.get("id"):
            continue
        identity = "waku-" + str(row["id"])
        picture = Picture(
            id=identity, width=0, height=0, index=0,
            hash=str(row.get("sha256") or identity), thumb_hash="",
            file_name=row.get("filename"), thumbnail=url, regular=url,
            delivery_url=_web_url(row.get("telegram_file_url")),
        )
        items.append(Artwork(
            id=identity, created_at=str(row.get("created_at") or ""),
            title=str(row.get("title") or "Waku Image"), description="",
            tags=row.get("tags") or [], source_url=_web_url(row.get("source_url")) or url,
            source_type="waku-api", source_available=bool(_web_url(row.get("source_url"))), r18=row["rating"] == "r18",
            artist=Artist(id="", name="", username="", uid="", type="waku-api"),
            pictures=[picture],
        ))
    return items


_RATING_MARKER = re.compile(r"\b(?:r[ -]?18|nsfw|sfw)\b|18\+|🔞", re.IGNORECASE)
_SAFE_MARKER = re.compile(r"\bsfw\b|\ban toàn\b|\ban toan\b", re.IGNORECASE)
_SAFE_NEGATION = re.compile(
    r"\b(?:no|not|non|without|không|khong|ko)\s*-?\s*(?:r[ -]?18|nsfw)\b",
    re.IGNORECASE,
)


def resolve_image_search(keyword, mode, *, request_text=""):
    """Extract rating instructions without relaxing the conversation's policy."""
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


async def manyacg_candidates(client, keyword, mode, *, subject=""):
    """Search by tag/theme like the legacy base; use random only without a query."""
    keyword = keyword.strip()
    headers = {"X-API-KEY": app_config.manyacg_api_key} if app_config.manyacg_api_key else {}
    query = " ".join(part for part in (subject.strip(), keyword) if part)
    if query:
        response = await client.get(
            "/artwork/list",
            params={
                "r18": mode,
                "hybrid": app_config.manyacg_hybrid_search,
                "keyword": query,
                "page_size": 30,
            },
            headers=headers,
        )
    else:
        response = await client.get("/artwork/random", params={"r18": mode, "limit": 10}, headers=headers)
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
    catalog = await _image_categories()
    if not catalog:
        return None
    keyword = keyword.strip()[:200]
    category = keyword.casefold()
    category = category if category in catalog else random.choice(list(catalog))
    data = await _request("/" + category, {"amount": 1})
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


def original_image_url(artwork, picture) -> str | None:
    if _value(artwork, "source_type") == "waku-api":
        return _value(picture, "delivery_url")
    if _value(artwork, "source_type") == "nekos.best":
        return _value(picture, "regular")
    picture_id = _value(picture, "id")
    return (
        f"https://t.me/{app_config.manyacg_bot}/?start=file_{picture_id}"
        if picture_id
        else _value(artwork, "source_url")
    )


def image_source_enabled(artwork):
    provider = "waku" if _value(artwork, "source_type") == "waku-api" else "manyacg"
    return provider in enabled_image_sources()


def _identity_keys(artwork, picture):
    # Distinct pages of the same work are valid choices. Compare actual image identities.
    values = [
        ("picture", _value(picture, "id")),
        ("url", _value(picture, "regular")),
    ]
    fingerprint = _value(picture, "hash", "")
    if isinstance(fingerprint, str) and fingerprint.strip("p:0"):
        values.append(("hash", fingerprint))
    if not any(value for _, value in values):
        values.append(("art", _value(artwork, "id") or _value(artwork, "source_url")))
    return [
        kind + ":" + hashlib.sha256(str(value).encode()).hexdigest()[:32]
        for kind, value in values
        if value
    ]


def _history_entries(value):
    return (
        [
            entry
            for entry in value
            if isinstance(entry, list) and all(isinstance(key, str) for key in entry)
        ]
        if isinstance(value, list)
        else []
    )


async def _select_fresh(items, mode, scope, as_dict):
    async with _selection_lock:
        history = (
            _history_entries(await memttlcache.get("anime-recent:v2:" + scope, []))
            if scope
            else []
        )
        reserved = (
            await memttlcache.get("anime-reserved:v2:" + scope, []) if scope else []
        )
        if not isinstance(reserved, list):
            reserved = []
        reserved_keys = set(reserved)
        last_image = set(history[-1]) if history else set()
        candidates = list(items)
        random.shuffle(candidates)
        available = []
        for artwork, fallback in candidates:
            if not image_allowed(_value(artwork, "r18"), mode):
                continue
            pictures = list(
                _value(artwork, "pictures", []) or ([fallback] if fallback else [])
            )
            random.shuffle(pictures)
            for picture in pictures:
                keys = _identity_keys(artwork, picture)
                if scope and (
                    reserved_keys.intersection(keys) or last_image.intersection(keys)
                ):
                    continue
                rank = max(
                    (
                        index
                        for index, entry in enumerate(history)
                        if set(entry).intersection(keys)
                    ),
                    default=-1,
                )
                available.append((rank, artwork, picture, keys))
        # Unseen images first, then the least recently delivered. Never repeat the last one.
        available.sort(key=lambda candidate: candidate[0])
        for _, artwork, picture, keys in available:
            if as_dict:
                selected = (
                    artwork.model_dump() if isinstance(artwork, Artwork) else artwork,
                    picture.model_dump() if isinstance(picture, Picture) else picture,
                )
            elif isinstance(artwork, dict):
                try:
                    selected = (
                        Artwork.model_validate(artwork),
                        Picture.model_validate(picture),
                    )
                except ValueError:
                    continue
            else:
                selected = artwork, picture
            if scope:
                # Reserve while a batch/network send is in progress, not after delivery.
                await memttlcache.set(
                    "anime-reserved:v2:" + scope, (reserved + keys)[-512:], ttl=300
                )
            return selected
    return None


async def remember_anime_image(scope, artwork, picture):
    """Remember successful deliveries and release their temporary reservations."""
    try:
        async with _selection_lock:
            key = "anime-recent:v2:" + scope
            history = _history_entries(await memttlcache.get(key, []))
            keys = _identity_keys(artwork, picture)
            history = [entry for entry in history if not set(entry).intersection(keys)]
            await memttlcache.set(
                key, (history + [keys])[-_HISTORY_SIZE:], ttl=_HISTORY_TTL
            )
            reservation_key = "anime-reserved:v2:" + scope
            reserved = await memttlcache.get(reservation_key, [])
            if isinstance(reserved, list):
                await memttlcache.set(
                    reservation_key,
                    [item for item in reserved if item not in keys],
                    ttl=300,
                )
    except Exception as error:
        # Delivery already succeeded. Never tell the caller to retry a delivered image.
        logger.warning("Anime delivery history update failed: {}", type(error).__name__)


async def release_anime_image(scope, artwork, picture):
    """A failed send must not keep an otherwise available image locked for minutes."""
    try:
        async with _selection_lock:
            key = "anime-reserved:v2:" + scope
            reserved = await memttlcache.get(key, [])
            keys = _identity_keys(artwork, picture)
            if isinstance(reserved, list):
                await memttlcache.set(
                    key, [item for item in reserved if item not in keys], ttl=300
                )
    except Exception as error:
        logger.warning("Anime reservation cleanup failed: {}", type(error).__name__)


async def fetch_anime_artwork(
    keyword, r18_mode, manyacg_fetch, *, as_dict=False, scope=None, image_type=None,
    subject="", allow_fallback=False, candidate_cache=None,
):
    sources = enabled_image_sources()
    if not sources:
        raise ImageServicesNotConfiguredError()
    request = resolve_image_search(keyword, r18_mode)
    if request is None:
        return None
    keyword, mode = request
    if image_type not in (None, "anime", "real"):
        return None
    subject = subject.strip()
    forced_nekos = not subject and keyword.lower().startswith(("nekos:", "nekos.best:"))
    if forced_nekos:
        if "manyacg" not in sources:
            raise ImageServicesNotConfiguredError()
        if mode == 1:
            return None
        keyword = keyword.partition(":")[2].strip()
        if keyword.casefold() not in {"neko", "waifu", "husbando", "kitsune"}:
            keyword = ""
        providers = ["nekos"]
    else:
        # Strict lookup first. Optional related/random fallbacks run below and
        # callers report mismatches to the model via fallback_count.
        # Other providers do not expose Waku's verified image-type field.
        providers = [source for source in sources if not image_type or source == "waku"]
        random.shuffle(providers)
    repeated = False
    attempts = [
        provider
        for provider in providers
        for _ in range(3 if provider == "nekos" and scope else 1)
    ]
    for provider in attempts:
        try:
            cache_key = (provider, keyword, mode, image_type, subject)
            cached = candidate_cache is not None and provider != "nekos" and bool(keyword or subject)
            if cached and cache_key in candidate_cache:
                item = candidate_cache[cache_key]
            elif provider == "nekos":
                item = await _fetch_nekos_artwork(keyword)
            elif provider == "waku":
                options = {"image_type": image_type} if image_type else {}
                if subject:
                    options["subject"] = subject
                item = await waku_candidates(keyword, mode, **options)
            else:
                item = await manyacg_fetch(keyword, mode, subject=subject) if subject else await manyacg_fetch(keyword, mode)
            if cached:
                candidate_cache[cache_key] = item
            if not item:
                continue
            if ("manyacg" if provider == "nekos" else provider) not in enabled_image_sources():
                continue
            if isinstance(item, list):
                if subject:
                    item = [art for art in item if _subject_matches(art, subject)]
                if keyword and provider == "manyacg":
                    required = _requested_tags(keyword)
                    item = [art for art in item if all(
                        tag in {str(value).casefold() for value in (_value(art, "tags") or []) if isinstance(value, str)}
                        for tag in required
                    )]
                choices = [(artwork, None) for artwork in item]
            elif (image_allowed(_value(item[0], "r18"), mode) and
                  (not subject or _subject_matches(item[0], subject)) and
                  (provider != "manyacg" or not keyword or all(
                      tag in {str(value).casefold() for value in (_value(item[0], "tags") or []) if isinstance(value, str)}
                      for tag in _requested_tags(keyword)
                  ))):
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
        except Exception as error:
            logger.warning("{} image fetch failed: {}", provider, type(error).__name__)
    if allow_fallback and (keyword or subject) and not forced_nekos:
        # Prefer a related match, then random. Never relax rating or image type.
        # A per-turn cache avoids repeating failed searches for every album item.
        related = []
        if keyword and subject:
            related = [("", subject), (keyword, "")]
        elif len(_requested_tags(keyword)) > 1:
            related = [(tag, "") for tag in _requested_tags(keyword)[:2]]
        for alternative_keyword, alternative_subject in [*related, ("", "")]:
            try:
                alternative = await fetch_anime_artwork(
                    alternative_keyword, mode, manyacg_fetch, as_dict=as_dict,
                    scope=scope, image_type=image_type, subject=alternative_subject,
                    allow_fallback=False, candidate_cache=candidate_cache,
                )
            except NoFreshImageError:
                continue
            if alternative is not None:
                return alternative
    if repeated and scope:
        raise NoFreshImageError(
            "Only the last delivered image or images still being sent are available"
        )
    return None
