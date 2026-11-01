"""Source links for Discord photo captions and single-photo buttons."""

from urllib.parse import quote, urlsplit

import discord


def source_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
    except ValueError:
        return None
    return value


def artwork_source(artwork) -> str | None:
    if not getattr(artwork, "source_available", True):
        return None
    return source_url(getattr(artwork, "source_url", None))


def linked_title(title: str, url: str | None, number: int) -> str:
    text = discord.utils.escape_markdown(" ".join(title.split())[:70])
    link = source_url(url)
    if link:
        # Angle brackets suppress previews and protect parentheses in the URL.
        link = quote(link, safe=":/?#[]@!$&'()*+,;=%")
        text = f"[{text}](<{link}>)"
    return f"{number}. {text}"
