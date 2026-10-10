"""Sending Telegram rich messages (Bot API 10.1 / MTProto layer 228+).

Pyrogram's high-level ``Client.send_rich_message`` mis-parses the
``UpdateShortSentMessage`` reply for channel peers (it reads ``peer.chat_id``
on an ``InputPeerChannel``), so rich messages are sent through the raw
``messages.SendMessage`` call here instead.

A rich message has no ``Message.text``: its content lives in structured
blocks. ``message_plain_text`` renders those blocks back to text so history,
reply chains and prompts keep working for rich messages.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pyrogram
from pyrogram import utils
from pyrogram.client import Client
from pyrogram.raw.base.input_rich_message import (
    InputRichMessage as _RawInputRichMessage,
)
from pyrogram.raw.base.reply_markup import ReplyMarkup as _RawReplyMarkup
from pyrogram.raw.functions.messages.send_message import SendMessage as _RawSendMessage
from pyrogram.raw.types.update_bot_new_business_message import (
    UpdateBotNewBusinessMessage as _RawUpdateBotNewBusinessMessage,
)
from pyrogram.raw.types.update_message_id import UpdateMessageID as _RawUpdateMessageID
from pyrogram.raw.types.update_new_channel_message import (
    UpdateNewChannelMessage as _RawUpdateNewChannelMessage,
)
from pyrogram.raw.types.update_new_message import (
    UpdateNewMessage as _RawUpdateNewMessage,
)
from pyrogram.raw.types.update_short import UpdateShort as _RawUpdateShort
from pyrogram.raw.types.update_short_sent_message import (
    UpdateShortSentMessage as _RawUpdateShortSentMessage,
)

__all__ = [
    "message_plain_text",
    "rich_html_plain_text",
    "rich_message_plain_text",
    "rich_media_messages",
    "rich_message_mention_ids",
    "send_rich_message",
    "sent_message_id",
]


class _RichHTMLText(HTMLParser):
    """Read rich HTML locally, including content stored in attributes."""

    _BLOCKS = {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "tr",
        "blockquote",
        "aside",
        "pre",
        "details",
        "summary",
        "table",
        "ul",
        "ol",
        "footer",
        "figure",
        "figcaption",
        "tg-math-block",
        "tg-reference",
        "tg-collage",
        "tg-slideshow",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, int]] = []
        self.cells: list[int] = []
        self.trailing_break = False

    def _linebreak(self):
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")
            self.trailing_break = True

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"br", "hr"}:
            # Explicit line breaks must keep blank lines (including code).
            self.parts.append("\n")
            self.trailing_break = False
        elif tag == "tr":
            self.cells.append(0)
        elif tag in {"td", "th"} and self.cells:
            if self.cells[-1]:
                self.parts.append(" | ")
            self.cells[-1] += 1
        elif tag == "a":
            self.links.append((attributes.get("href") or "", len(self.parts)))
        elif tag == "input" and attributes.get("type") == "checkbox":
            self.parts.append("[x] " if "checked" in attributes else "[ ] ")
        elif tag in {"img", "video", "audio", "tg-document"}:
            alt = attributes.get("alt") or ""
            source = attributes.get("src") or ""
            if source.startswith("tg://emoji"):
                self.parts.append(alt)
            else:
                self.parts.append(alt)
                if source:
                    self.parts.append(f" ({source})" if alt else source)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "a" and self.links:
            target, start = self.links.pop()
            label = "".join(self.parts[start:])
            if target and not target.startswith("#") and label != target:
                self.parts.append(f" ({target})")
        if tag == "tr" and self.cells:
            self.cells.pop()
        if tag in self._BLOCKS:
            self._linebreak()

    def handle_data(self, data):
        self.parts.append(data)
        self.trailing_break = False


def rich_html_plain_text(html_text: str) -> str:
    """Plain text of a rich HTML payload, formatting dropped.

    Used to deliver content that could not be sent as a rich message; it
    handles the tag set telegramify-markdown emits, not arbitrary HTML.
    """
    if not html_text:
        return ""
    parser = _RichHTMLText()
    parser.feed(html_text)
    parser.close()
    text = "".join(parser.parts)
    return text[:-1] if parser.trailing_break and parser.parts[-1] == "\n" else text


def sent_message_id(result: object) -> int | None:
    """Message id carried by a ``messages.SendMessage`` reply, if any."""
    if isinstance(result, _RawUpdateShort):
        return sent_message_id(result.update)
    if isinstance(result, (_RawUpdateShortSentMessage, _RawUpdateMessageID)):
        return result.id
    if isinstance(
        result,
        (
            _RawUpdateNewMessage,
            _RawUpdateNewChannelMessage,
            _RawUpdateBotNewBusinessMessage,
        ),
    ):
        message = getattr(result, "message", None)
        return message.id if message is not None else None
    for update in getattr(result, "updates", None) or ():
        message_id = sent_message_id(update)
        if message_id is not None:
            return message_id
    return None


def _rich_text_plain(value: object, *, include_link_targets: bool = True) -> str:
    """Render a RichText tree (str / list / wrapper objects) to plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(
            _rich_text_plain(item, include_link_targets=include_link_targets)
            for item in value
        )
    expression = getattr(value, "expression", None)
    if isinstance(expression, str):
        return expression
    alternative = getattr(value, "alternative_text", None)
    text = getattr(value, "text", None)
    if text is not None:
        plain = _rich_text_plain(text, include_link_targets=include_link_targets)
        target = getattr(value, "url", None)
        if (
            include_link_targets
            and isinstance(target, str)
            and target
            and plain != target
        ):
            return f"{plain} ({target})"
        credit = getattr(value, "credit", None)
        return "\n".join(
            part
            for part in (
                plain,
                _rich_text_plain(credit, include_link_targets=include_link_targets),
            )
            if part
        )
    if isinstance(alternative, str):
        return alternative
    return ""


def _rich_block_plain(block: object, *, include_link_targets: bool = True) -> str:
    """Render one RichBlock (and its nested blocks) to plain text."""
    if isinstance(block, pyrogram.types.RichBlockListItem):
        prefix = ""
        if block.has_checkbox:
            prefix = "[x] " if block.is_checked else "[ ] "
        elif block.label:
            prefix = f"{block.label} "
        return prefix + " ".join(
            part
            for part in (
                _rich_block_plain(item, include_link_targets=include_link_targets)
                for item in block.blocks
            )
            if part
        )
    if isinstance(block, pyrogram.types.RichBlockTable):
        rows = "\n".join(
            " | ".join(
                _rich_text_plain(cell.text, include_link_targets=include_link_targets)
                for cell in row
            )
            for row in block.cells
        )
        return "\n".join(
            part
            for part in (
                rows,
                _rich_text_plain(
                    block.caption, include_link_targets=include_link_targets
                ),
            )
            if part
        )
    parts: list[str] = []
    expression = getattr(block, "expression", None)
    if isinstance(expression, str):
        parts.append(expression)
    for attr in ("summary", "text", "caption", "credit"):
        value = getattr(block, attr, None)
        if value is not None:
            parts.append(
                _rich_text_plain(value, include_link_targets=include_link_targets)
            )
    for attr in ("items", "blocks"):
        nested = getattr(block, attr, None)
        if nested:
            parts.extend(
                _rich_block_plain(child, include_link_targets=include_link_targets)
                for child in nested
            )
    return "\n".join(part for part in parts if part)


def rich_message_plain_text(
    rich_message: pyrogram.types.RichMessage, *, include_link_targets: bool = True
) -> str:
    """Best-effort plain text of a rich message, block per line."""
    return "\n".join(
        part
        for part in (
            _rich_block_plain(block, include_link_targets=include_link_targets)
            for block in rich_message.blocks
        )
        if part
    )


def message_plain_text(
    message: pyrogram.types.Message, *, include_link_targets: bool = True
) -> str:
    """Message text or caption, falling back to its rich message content."""
    text = message.text or message.caption
    if text:
        return text
    rich_message = getattr(message, "rich_message", None)
    if rich_message is None:
        return ""
    return rich_message_plain_text(
        rich_message, include_link_targets=include_link_targets
    )


def rich_media_messages(
    message: pyrogram.types.Message,
) -> list[pyrogram.types.Message]:
    """Expose supported embedded native media to existing download checks.

    Only parsed Telegram file objects are accepted; this never fetches HTML
    URLs. Traversal is bounded even for malformed or cyclic object trees.
    """
    rich = getattr(message, "rich_message", None)
    if rich is None:
        return []
    result: list[pyrogram.types.Message] = []
    pending = [(block, 0) for block in reversed(rich.blocks)]
    visited: set[int] = set()
    kinds = (
        (pyrogram.types.RichBlockPhoto, "photo", pyrogram.enums.MessageMediaType.PHOTO),
        (pyrogram.types.RichBlockVideo, "video", pyrogram.enums.MessageMediaType.VIDEO),
        (pyrogram.types.RichBlockAudio, "audio", pyrogram.enums.MessageMediaType.AUDIO),
        (
            pyrogram.types.RichBlockVoiceNote,
            "voice_note",
            pyrogram.enums.MessageMediaType.VOICE,
        ),
    )
    while pending and len(visited) < 500 and len(result) < 50:
        block, depth = pending.pop()
        if block is None or id(block) in visited or depth >= 16:
            continue
        visited.add(id(block))
        for block_type, attribute, media in kinds:
            if not isinstance(block, block_type):
                continue
            payload = getattr(block, attribute, None)
            file_id = getattr(payload, "file_id", None)
            if (
                payload is not None
                and isinstance(file_id, str)
                and "://" not in file_id
            ):
                result.append(
                    pyrogram.types.Message(
                        id=message.id,
                        media=media,
                        **{media.name.lower(): payload},
                    )
                )
            break
        children = getattr(block, "blocks", None) or getattr(block, "items", None) or ()
        pending.extend((child, depth + 1) for child in reversed(children))
    return result


def rich_message_mention_ids(message: pyrogram.types.Message) -> set[int]:
    """Verified native text mentions, never inferred from links or prose."""
    rich = getattr(message, "rich_message", None)
    if not isinstance(rich, pyrogram.types.RichMessage):
        return set()
    pending: list[tuple[object, int]] = [(rich, 0)]
    visited: set[int] = set()
    result: set[int] = set()
    while pending and len(visited) < 500:
        item, depth = pending.pop()
        if item is None or id(item) in visited or depth > 16:
            continue
        visited.add(id(item))
        if isinstance(item, pyrogram.types.RichTextTextMention):
            user = item.user
            if (
                isinstance(user, pyrogram.types.User)
                and isinstance(user.id, int)
                and not isinstance(user.id, bool)
                and 0 < user.id < 2**63
            ):
                result.add(user.id)
        if isinstance(item, (list, tuple)):
            pending.extend((child, depth) for child in reversed(item[:500]))
        elif isinstance(
            item,
            (
                pyrogram.types.RichMessage,
                pyrogram.types.RichBlock,
                pyrogram.types.RichText,
            ),
        ):
            for attribute in (
                "blocks",
                "items",
                "cells",
                "text",
                "summary",
                "caption",
                "credit",
            ):
                child = getattr(item, attribute, None)
                if child is not None:
                    pending.append((child, depth + 1))
    return result


async def send_rich_message(
    client: Client,
    chat_id: int | str,
    rich_message: _RawInputRichMessage,
    *,
    reply_parameters: pyrogram.types.ReplyParameters | None = None,
    message_thread_id: int | None = None,
    direct_messages_topic_id: int | None = None,
    reply_markup: _RawReplyMarkup | None = None,
    business_connection_id: str | None = None,
) -> int | None:
    """Send one rich message, returning the id of the sent message.

    ``rich_message`` is a written raw payload (``InputRichMessageHTML``,
    ``InputRichMessageMarkdown`` or a block-based ``InputRichMessage``).
    """
    peer = await client.resolve_peer(chat_id)
    if peer is None:
        raise ValueError(f"Cannot resolve peer for chat {chat_id}")
    business_kwargs = (
        {"business_connection_id": business_connection_id}
        if business_connection_id
        else {}
    )
    result = await client.invoke(
        _RawSendMessage(
            peer=peer,
            message="",
            random_id=client.rnd_id(),
            reply_to=await utils.get_reply_to(
                client, reply_parameters, message_thread_id, direct_messages_topic_id
            ),
            rich_message=rich_message,
            reply_markup=reply_markup,
        ),
        **business_kwargs,
    )
    return sent_message_id(result)
