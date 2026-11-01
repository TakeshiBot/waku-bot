"""Compact photo metadata for single-image messages on chat clients."""

import unicodedata
import textwrap


def wrapped_image_caption(value: str, max_width: int = 28) -> str:
    """Wrap metadata rather than attach a link; Telegram limits captions to 1024 characters."""
    text = " ".join((value or "").split())
    # Client fonts and image sizes differ, so width is deliberately approximate.
    lines = textwrap.wrap(text, width=max_width, break_long_words=True, break_on_hyphens=False)
    caption = "\n".join(lines)
    return caption if len(caption) <= 1024 else caption[:1023].rstrip() + "…"


def short_image_caption(value: str, max_width: int = 28) -> str:
    """Approximate a short single line; actual client and font widths vary."""
    text = " ".join((value or "").split())
    def char_width(char: str) -> int:
        return 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1

    if sum(map(char_width, text)) <= max_width:
        return text
    width = 0
    result = []
    for char in text:
        width += char_width(char)
        if width > max_width - 1:
            break
        result.append(char)
    return "".join(result).rstrip() + "…"
