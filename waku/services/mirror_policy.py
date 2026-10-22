"""Keep AI-generated Telegram output separate from the trusted Mirror transport."""

from __future__ import annotations

import re
from functools import lru_cache
from html.parser import HTMLParser

import telegramify_markdown

from waku.config import app_config


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


_COMMAND = re.compile(
    r"\A\s*/(?:mirror|m|qbmirror|qbm|qm|jdmirror|jm|nzbmirror|nm|ytdl|y|uphoster|up|"
    r"leech|l|qbleech|qbl|ql|jdleech|jl|nzbleech|nl|ytdlleech|yl|clone|cl|"
    r"cancel|c|cancelall|call|wakustatus|status|s|c_[A-Za-z0-9_-]+)"
    r"(?:@([A-Za-z0-9_]+))?(?=\s|\Z)",
    flags=re.IGNORECASE,
)


@lru_cache(maxsize=64)
def _rendered_variants(text: str) -> tuple[str, ...]:
    variants = [text]
    try:
        parser = _PlainHTML()
        parser.feed(text)
        variants.append("".join(parser.parts))
        for variant in tuple(variants):
            variants.append(telegramify_markdown.convert(variant)[0])
    except Exception:
        pass
    return tuple(variants)


def is_raw_mirror_command(text: object, chat_id: int) -> bool:
    """Check both source and rendered text; prose mentioning /m remains allowed."""
    username = app_config.mirror_bot_username.strip().removeprefix("@")
    mirror_chat = app_config.mirror_group_id
    if (
        not username
        or mirror_chat >= 0
        or not isinstance(text, str)
        or ("/" not in text and "&" not in text)
    ):
        return False
    for variant in _rendered_variants(text):
        match = _COMMAND.match(variant)
        if match and (
            match.group(1).lower() == username.lower()
            if match.group(1)
            else chat_id == mirror_chat
        ):
            return True
    return False
