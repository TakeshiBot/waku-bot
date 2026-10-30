"""Compact photo metadata for single-image messages on chat clients."""

import unicodedata


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
