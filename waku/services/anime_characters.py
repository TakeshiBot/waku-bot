"""Resolve character names to public AniList names, not semantically similar art."""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass

import httpx

from waku.common.http import _clients
from waku.logger import logger

_cache: dict[str, tuple[float, CharacterName | AmbiguousCharacterError | None]] = {}
_lock = asyncio.Lock()
_blocked_until = 0.0
_QUERY = """query ($name: String!) {
  Page(perPage: 5) {
    characters(search: $name, sort: FAVOURITES_DESC) {
      name { full native } favourites
    }
  }
}"""


def words(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())


def matches_name(field: str, alias: str) -> bool:
    needle, haystack = words(alias), words(field)
    if not needle:
        return False
    if all(token in haystack for token in needle):
        return True
    # Tags often concatenate a full name: tokisakikurumi, 初音ミク生誕祭2024.
    compact = "".join(needle)
    joined = "".join(haystack)
    embedded_name = not compact.isascii() and (
        len(compact) >= 3 or re.fullmatch(r"[\u3400-\u9fff]{2,}", compact)
    )
    # Short kana names such as レム/リン also occur inside unrelated Japanese words.
    return (len(needle) > 1 and compact == joined) or (
        bool(embedded_name) and compact in joined
    )


class AmbiguousCharacterError(Exception):
    """A short name has several plausible identities; ask instead of mixing them."""

    def __init__(self, choices):
        self.choices = tuple(choices)
        super().__init__(", ".join(self.choices))


@dataclass(frozen=True)
class CharacterName:
    full: str
    native: str

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                filter(
                    None,
                    (
                        self.full,
                        " ".join(reversed(self.full.split())),
                        self.native,
                    ),
                )
            )
        )

    def matches(self, artwork) -> bool:
        get = (
            artwork.get
            if isinstance(artwork, dict)
            else lambda k, d=None: getattr(artwork, k, d)
        )
        fields = [get("title", ""), *(get("tags", []) or [])]
        return any(
            matches_name(str(field), alias)
            for field in fields
            for alias in self.aliases
        )

    def related_tags(self, artwork) -> list[str]:
        """Learn only native-name fragments actually tagged on a confirmed artwork."""
        native = "".join(words(self.native))
        return [
            tag
            for tag in artwork.get("tags", [])
            if isinstance(tag, str)
            and not tag.isascii()
            and 2 <= len("".join(words(tag))) < len(native)
            and "".join(words(tag)) in native
        ]


async def resolve_character(keyword: str) -> CharacterName | None:
    """Use full/native names for clear matches; don't treat nickname similarity as proof."""
    global _blocked_until
    key = " ".join(words(keyword))
    if (
        not key
        or len(key) > 80
        or len(words(key)) > 5
        or key
        in {
            "neko",
            "waifu",
            "husbando",
            "kitsune",
            "anime",
            "random",
        }
    ):
        return None
    async with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > time.monotonic():
            if isinstance(cached[1], AmbiguousCharacterError):
                raise cached[1]
            return cached[1]
        if time.monotonic() < _blocked_until:
            return None
        result = None
        ttl = 300
        try:
            client = _clients.get("anime-character-names")
            if client is None or client.is_closed:
                client = httpx.AsyncClient(
                    timeout=5, headers={"User-Agent": "WAKU Bot"}
                )
                _clients["anime-character-names"] = client
            response = await client.post(
                "https://graphql.anilist.co",
                json={"query": _QUERY, "variables": {"name": keyword}},
            )
            if response.status_code == 429:
                _blocked_until = time.monotonic() + 60
            response.raise_for_status()
            records = (
                response.json().get("data", {}).get("Page", {}).get("characters", [])
            )
            candidates = [
                record
                for record in records
                if isinstance(record, dict)
                and isinstance(record.get("name"), dict)
                and any(
                    matches_name(alias, keyword)
                    for alias in (
                        record["name"].get("full") or "",
                        record["name"].get("native") or "",
                    )
                )
            ]
            if candidates:
                first = candidates[0]
                # A short ambiguous name needs a clear leading match. Full names win directly.
                exact = set(words(key)) == set(words(first["name"].get("full") or ""))
                dominant = len(candidates) == 1 or (
                    first.get("favourites") or 0
                ) >= max(10, 3 * (candidates[1].get("favourites") or 0))
                if exact or dominant:
                    result = CharacterName(
                        first["name"]["full"], first["name"].get("native") or ""
                    )
                else:
                    result = AmbiguousCharacterError(
                        record["name"]["full"] for record in candidates
                    )
            ttl = 24 * 3600
        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
        ) as error:
            logger.debug("Character-name lookup unavailable: {}", type(error).__name__)
        if len(_cache) >= 256:
            _cache.pop(next(iter(_cache)))
        _cache[key] = time.monotonic() + ttl, result
        if isinstance(result, AmbiguousCharacterError):
            raise result
        return result
