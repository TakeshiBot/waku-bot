"""Small cached character-name index; image selection stays in anime_images."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass

import httpx

from waku.common.http import _clients
from waku.logger import logger

_cache = {}
_QUERY = """query ($name: String!) {
  Page(perPage: 5) { characters(search: $name, sort: FAVOURITES_DESC) {
    name { full native } favourites
  } }
}"""


def name_words(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    text = "".join(
        "".join(
            c
            for c in unicodedata.normalize("NFD", char)
            if not unicodedata.combining(c)
        )
        if "LATIN" in unicodedata.name(char, "")
        else char
        for char in text
    )
    return re.findall(r"[^\W_]+", text)


def name_matches(field, name):
    tokens, wanted = name_words(field), name_words(name)
    if not wanted:
        return False
    if all(word in tokens for word in wanted):
        return True
    compact = "".join(wanted)
    joined = "".join(tokens)
    # Full romanized names can be concatenated. Short kana must be whole tokens:
    # レム (Rem) inside ムレムレ教授 is an unrelated Blue Archive illustration.
    return (len(wanted) > 1 and compact == joined) or (
        not compact.isascii() and len(compact) >= 3 and compact in joined
    )


@dataclass(frozen=True)
class ImageCharacter:
    full: str
    native: str

    @property
    def aliases(self):
        return tuple(
            filter(
                None, (self.full, " ".join(reversed(self.full.split())), self.native)
            )
        )


async def image_character(keyword):
    """Resolve a clear identity only; never pick randomly among similarly named people."""
    key = " ".join(name_words(keyword))
    if (
        not key
        or len(key) > 80
        or key in {"neko", "waifu", "husbando", "kitsune", "anime", "random"}
    ):
        return None
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    result, ttl = None, 30
    try:
        client = _clients.get("anime-names")
        if client is None or client.is_closed:
            client = httpx.AsyncClient(timeout=4, headers={"User-Agent": "WAKU Bot"})
            _clients["anime-names"] = client
        response = await client.post(
            "https://graphql.anilist.co",
            json={"query": _QUERY, "variables": {"name": keyword}},
        )
        response.raise_for_status()
        records = response.json()["data"]["Page"]["characters"]
        matches = [
            r
            for r in records
            if any(
                name_matches(r["name"].get(k) or "", keyword)
                for k in ("full", "native")
            )
        ]
        if matches:
            first = matches[0]
            exact = set(name_words(first["name"]["full"])) == set(name_words(keyword))
            dominant = len(matches) == 1 or (first.get("favourites") or 0) >= max(
                10, 3 * (matches[1].get("favourites") or 0)
            )
            # False prevents an ambiguous nickname from becoming a mixed image pool.
            result = (
                ImageCharacter(first["name"]["full"], first["name"].get("native") or "")
                if exact or dominant
                else False
            )
        ttl = 3600
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
        logger.debug("Anime name index unavailable: {}", type(error).__name__)
        # Full names can still be checked literally during an index outage.
        # A bare ambiguous nickname must not fall back to a namesake's images.
        result = False if len(name_words(keyword)) == 1 and key.isascii() else None
    if len(_cache) >= 256:
        _cache.pop(next(iter(_cache)))
    _cache[key] = time.monotonic() + ttl, result
    return result
