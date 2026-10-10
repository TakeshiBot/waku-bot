"""Markdown -> Telegram formatting converters.

Wraps telegramify-markdown and exposes three public functions:

    convert_md(text) -> tuple[str, list[pyrogram.types.MessageEntity]]
    convert_md_chunks(text, max_utf16_len=4096) -> list[tuple[str, entities]]
    convert_rich_md(text) -> list[pyrogram.types.InputRichMessage]

convert_md returns (plain_text, entities), or (original_text, []) on failure.
Entities use UTF-16 code-unit offsets as required by the Telegram Bot API.
convert_md_chunks additionally splits text that exceeds Telegram's per-message
length limit. convert_rich_md returns rich message payloads already split
within Telegram's byte/block limits, or [] when the text is empty or
conversion fails.
"""

from html.parser import HTMLParser
from typing import Any, cast

import pyrogram.enums
import pyrogram.types
import telegramify_markdown
from telegramify_markdown.config import get_runtime_config

from waku.logger import logger


def _entity_type(type_str: str) -> pyrogram.enums.MessageEntityType:
    """Map a telegramify-markdown type string to a Pyrogram enum member.

    Falls back to UNKNOWN for any unrecognised type.
    """
    try:
        return pyrogram.enums.MessageEntityType[type_str.upper()]
    except KeyError:
        return pyrogram.enums.MessageEntityType.UNKNOWN


# One-time configuration: suppress emoji heading prefixes for cleaner output
_cfg = get_runtime_config()
_cfg.markdown_symbol.heading_level_1 = ""
_cfg.markdown_symbol.heading_level_2 = ""
_cfg.markdown_symbol.heading_level_3 = ""
_cfg.markdown_symbol.heading_level_4 = ""


def _pyrogram_entities(
    tg_entities: list[Any],
) -> list[pyrogram.types.MessageEntity]:
    """Map telegramify-markdown entities to Pyrogram MessageEntity objects."""
    pyrogram_entities = []
    for e in tg_entities:
        etype = _entity_type(e.type)
        kwargs: dict[str, Any] = {
            "type": etype,
            "offset": e.offset,
            "length": e.length,
        }
        if etype == pyrogram.enums.MessageEntityType.PRE:
            kwargs["language"] = e.language if e.language is not None else ""
        if etype == pyrogram.enums.MessageEntityType.BLOCKQUOTE:
            kwargs["expandable"] = True
        if etype == pyrogram.enums.MessageEntityType.TEXT_LINK:
            kwargs["url"] = e.url
        pyrogram_entities.append(pyrogram.types.MessageEntity(**kwargs))
    return pyrogram_entities


def convert_md(
    text: str,
) -> tuple[str, list[pyrogram.types.MessageEntity]]:
    """Convert Markdown to plain text + Telegram MessageEntity list.

    Returns (plain_text, entities) on success.
    Returns (original_text, []) on any conversion error (safe fallback).

    Entities carry UTF-16 offsets and can be passed directly to Pyrogram's
    reply_text / edit_text ``entities`` parameter without setting parse_mode.
    """
    if not text:
        return text, []
    try:
        plain, tg_entities = telegramify_markdown.convert(text)
        return plain, _pyrogram_entities(tg_entities)
    except Exception as e:
        logger.debug(f"Markdown conversion failed: {e}")
        return text, []


def convert_md_chunks(
    text: str,
    max_utf16_len: int = 4096,
) -> list[tuple[str, list[pyrogram.types.MessageEntity]]]:
    """Convert Markdown to plain text + entities, split to fit Telegram.

    Only splits when the converted text exceeds ``max_utf16_len`` UTF-16 code
    units (Telegram's per-message limit); entities crossing a split are
    clipped into both chunks. Returns [] when nothing is left to send and
    falls back to bounded entity-less chunks on conversion error.
    """
    if not text:
        return []
    try:
        plain, tg_entities = telegramify_markdown.convert(text)
        return [
            (chunk_text, _pyrogram_entities(chunk_entities))
            for chunk_text, chunk_entities in telegramify_markdown.split_entities(
                plain, tg_entities, max_utf16_len
            )
        ]
    except Exception as e:
        logger.debug(f"Markdown conversion failed: {e}")
        return [(chunk, []) for chunk in split_plain_text(text, max_utf16_len)]


def split_plain_text(text: str, max_utf16_len: int = 4096) -> list[str]:
    """Split already-plain text to fit Telegram's per-message limit.

    Returns [] when the text is empty or whitespace-only.
    """
    if not text.strip():
        return []
    if max_utf16_len < 2:
        raise ValueError("max_utf16_len must fit a UTF-16 surrogate pair")
    # This fallback must remain usable even when the Markdown splitter fails.
    # Python characters keep astral emoji together; Telegram counts them as two
    # UTF-16 units. Keep every character, including boundary whitespace.
    chunks: list[str] = []
    start = 0
    units = 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > max_utf16_len:
            chunks.append(text[start:index])
            start = index
            units = 0
        units += width
    chunks.append(text[start:])
    return chunks


def convert_rich_md(text: str) -> list[pyrogram.types.InputRichMessage]:
    """Convert Markdown to sendable Telegram rich message payloads.

    Headings, tables, formulas, task lists, details and inline media are kept
    as structured blocks. Output is split at Telegram's rich message limits
    (32768 bytes / 500 blocks), so long text yields several payloads to send
    in order. Returns [] when the text is empty or conversion fails.
    """
    if not text or not text.strip():
        return []
    try:
        payloads = [
            cast(telegramify_markdown.InputRichMessage, item.rich_message)
            for item in telegramify_markdown.telegramify_rich(text)
        ]
        # Use Telegram's own Markdown parser when the complete source fits.
        # The library's HTML renderer turns task checkboxes into emoji and
        # escapes details/other native tags, losing structured semantics.
        if len(text.encode("utf-8")) <= 32768:
            html = "".join(payload.html or "" for payload in payloads)
            if not _rich_html_within_limits(html) or not _rich_html_within_limits(text):
                return []
            return [pyrogram.types.InputRichMessage(markdown=text)]
        # Validate the entire batch before any message can be delivered. The
        # library can leave a single giant table/list unsplit, and its block
        # counter omits nested list items and table rows.
        if any(
            not payload.html or not _rich_html_within_limits(payload.html)
            for payload in payloads
        ):
            return []
        return [
            pyrogram.types.InputRichMessage(
                html=payload.html,
                markdown=payload.markdown,
                is_rtl=payload.is_rtl,
                skip_entity_detection=payload.skip_entity_detection,
            )
            for payload in payloads
        ]
    except Exception as e:
        logger.debug(f"Rich markdown conversion failed: {e}")
        return []


class _RichHTMLLimits(HTMLParser):
    _BLOCKS = {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "pre",
        "footer",
        "hr",
        "ul",
        "ol",
        "li",
        "blockquote",
        "aside",
        "table",
        "tr",
        "details",
        "tg-math-block",
        "tg-reference",
        "tg-collage",
        "tg-slideshow",
        "img",
        "video",
        "audio",
        "tg-document",
        "tg-map",
    }
    _VOID = {"br", "hr", "img", "input", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = 0
        self.media = 0
        self.stack: list[str] = []
        self.rows: list[int] = []
        self.valid = True

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.blocks += tag in self._BLOCKS
        if tag in {"img", "video", "audio", "tg-document"}:
            self.media += not (attributes.get("src") or "").startswith("tg://emoji")
        if tag == "tr":
            self.rows.append(0)
        elif tag in {"td", "th"} and self.rows:
            try:
                self.rows[-1] += max(1, int(attributes.get("colspan") or 1))
            except ValueError:
                self.valid = False
            self.valid &= self.rows[-1] <= 20
        if tag not in self._VOID:
            self.stack.append(tag)
        self.valid &= len(self.stack) <= 16 and self.blocks <= 500 and self.media <= 50

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "tr" and self.rows:
            self.rows.pop()
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag) :]


def _rich_html_within_limits(html: str) -> bool:
    # Counting encoded markup is conservative: it also bounds visible text,
    # custom emoji alternatives, formula source and any escaping overhead.
    if len(html.encode("utf-8")) > 32768:
        return False
    parser = _RichHTMLLimits()
    parser.feed(html)
    parser.close()
    return parser.valid
