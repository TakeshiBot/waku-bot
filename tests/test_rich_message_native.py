"""Rich content through the installed SDK, without a Telegram connection."""

from io import BytesIO
from unittest.mock import AsyncMock

import pyrogram
import pytest
from pyrogram import raw, types
from pyrogram.file_id import FileId
from pyrogram.raw.core import TLObject

from waku.common.rich_message import (
    message_plain_text,
    rich_html_plain_text,
    rich_media_messages,
    rich_message_mention_ids,
    rich_message_plain_text,
    send_rich_message,
)
from waku.plugins.agent import styling


def roundtrip(value):
    return TLObject.read(BytesIO(value.write()))


@pytest.mark.asyncio
async def test_native_rich_blocks_survive_wire_parsing_and_prompt_extraction():
    rich = raw.types.RichMessage(
        photos=[],
        documents=[],
        blocks=[
            raw.types.PageBlockParagraph(
                text=raw.types.TextConcat(
                    texts=[
                        raw.types.TextBold(
                            text=raw.types.TextPlain(text="Xin chào 🙂 ")
                        ),
                        raw.types.TextUrl(
                            text=raw.types.TextPlain(text="nguồn"),
                            url="https://example.com",
                            webpage_id=0,
                        ),
                        raw.types.TextCustomEmoji(document_id=1, alt="❤"),
                        raw.types.TextMath(source="x^2"),
                    ]
                )
            ),
            raw.types.PageBlockList(
                items=[
                    raw.types.PageListItemText(
                        text=raw.types.TextPlain(text="ordinary")
                    ),
                    raw.types.PageListItemText(
                        text=raw.types.TextPlain(text="todo"), checkbox=True
                    ),
                    raw.types.PageListItemText(
                        text=raw.types.TextPlain(text="done"),
                        checkbox=True,
                        checked=True,
                    ),
                ]
            ),
            raw.types.PageBlockTable(
                title=raw.types.TextPlain(text="Table title"),
                rows=[
                    raw.types.PageTableRow(
                        cells=[
                            raw.types.PageTableCell(text=raw.types.TextPlain(text="A")),
                            raw.types.PageTableCell(text=raw.types.TextPlain(text="B")),
                        ]
                    ),
                ],
            ),
            raw.types.PageBlockBlockquote(
                text=raw.types.TextPlain(text="quote"),
                caption=raw.types.TextPlain(text="author"),
            ),
            raw.types.PageBlockDetails(
                title=raw.types.TextPlain(text="Details"),
                blocks=[
                    raw.types.PageBlockPreformatted(
                        text=raw.types.TextPlain(text="  x < y\n  y > z"),
                        language="python",
                    ),
                    raw.types.PageBlockMath(source="E=mc^2"),
                ],
            ),
        ],
    )
    client = pyrogram.Client("rich-unit", in_memory=True)
    parsed = await types.RichMessage._parse(client, roundtrip(rich))
    text = rich_message_plain_text(parsed)
    assert "Xin chào 🙂 nguồn (https://example.com)❤x^2" in text
    assert "• ordinary\n[ ] todo\n[x] done" in text
    assert "A | B\nTable title" in text
    assert "author" in text and "quote" in text
    assert "Details\n  x < y\n  y > z\nE=mc^2" in text
    assert message_plain_text(types.Message(id=1, rich_message=parsed)) == text


def test_native_caption_credit_survives_nested_media_text():
    caption = types.RichBlockCaption(text="ảnh", credit="tác giả")
    rich = types.RichMessage(blocks=[types.RichBlockPhoto(photo=None, caption=caption)])
    assert rich_message_plain_text(rich) == "ảnh\ntác giả"


@pytest.mark.asyncio
async def test_native_text_mentions_are_verified_not_inferred_from_url():
    mention = await types.RichText._parse(
        pyrogram.Client("rich-unit", in_memory=True),
        roundtrip(
            raw.types.TextMentionName(
                text=raw.types.TextPlain(text="Member"), user_id=42
            )
        ),
        users={42: raw.types.User(id=42, first_name="Member")},
    )
    rich = types.RichMessage(
        blocks=[
            types.RichBlockDetails(
                summary="details",
                blocks=[
                    types.RichBlockParagraph(text=types.RichTextBold(text=mention)),
                    types.RichBlockTable(
                        cells=[[types.RichBlockTableCell(text=mention)]]
                    ),
                    types.RichBlockParagraph(
                        text=types.RichTextUrl(text="Member", url="tg://user?id=99")
                    ),
                    types.RichBlockParagraph(
                        text=types.RichTextTextMention(text="Unknown", user=None)
                    ),
                ],
            ),
        ]
    )
    message = types.Message(id=1, rich_message=rich)
    assert rich_message_mention_ids(message) == {42}
    rich.blocks.append(rich.blocks[0])
    rich.blocks[0].blocks.append(rich.blocks[0])
    assert rich_message_mention_ids(message) == {42}


@pytest.mark.parametrize("user_id", [True, 0, -1, "42", 2**63])
def test_invalid_native_mention_ids_cannot_authorize_targets(user_id):
    message = types.Message(
        id=1,
        rich_message=types.RichMessage(
            blocks=[
                types.RichBlockParagraph(
                    text=types.RichTextTextMention(
                        text="Member", user=types.User(id=user_id)
                    )
                ),
            ]
        ),
    )
    assert rich_message_mention_ids(message) == set()


def test_authorization_text_does_not_include_hidden_link_destinations():
    message = types.Message(
        id=1,
        rich_message=types.RichMessage(
            blocks=[
                types.RichBlockDetails(
                    summary="ban this member",
                    blocks=[
                        types.RichBlockParagraph(
                            text=types.RichTextBold(
                                text=types.RichTextUrl(
                                    text="source", url="https://example.com/42"
                                )
                            )
                        ),
                    ],
                ),
            ]
        ),
    )
    assert "42" in message_plain_text(message)
    assert (
        message_plain_text(message, include_link_targets=False)
        == "ban this member\nsource"
    )


@pytest.mark.asyncio
async def test_native_embedded_photo_produces_downloadable_telegram_file_id():
    rich = raw.types.RichMessage(
        blocks=[
            raw.types.PageBlockPhoto(
                photo_id=123,
                caption=raw.types.PageCaption(
                    text=raw.types.TextPlain(text="ảnh"), credit=raw.types.TextEmpty()
                ),
            )
        ],
        photos=[
            raw.types.Photo(
                id=123,
                access_hash=456,
                file_reference=b"ref",
                date=1,
                dc_id=2,
                sizes=[raw.types.PhotoSize(type="x", w=20, h=20, size=30)],
            )
        ],
        documents=[],
    )
    parsed = await types.RichMessage._parse(
        pyrogram.Client("rich-unit", in_memory=True), roundtrip(rich)
    )
    exposed = rich_media_messages(types.Message(id=7, rich_message=parsed))
    assert exposed[0].photo.file_id and exposed[0].photo.file_unique_id
    assert exposed[0].photo.file_size == 30
    assert FileId.decode(exposed[0].photo.file_id).media_id == 123


def test_plain_fallback_entities_use_native_utf16_offsets_after_emoji():
    chunks = styling.convert_md_chunks("🙂 **Xin** [link](https://example.com)")
    text, entities = chunks[0]
    assert text == "🙂 Xin link"
    native = [roundtrip(entity.write()) for entity in entities]
    assert isinstance(native[0], raw.types.MessageEntityBold)
    assert (native[0].offset, native[0].length) == (3, 3)
    assert isinstance(native[1], raw.types.MessageEntityTextUrl)
    assert (native[1].offset, native[1].length, native[1].url) == (
        7,
        4,
        "https://example.com",
    )


def test_html_fallback_keeps_semantic_content_and_escaped_code():
    html = '<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table><p><a href="https://example.com/?a=1&amp;b=2">link</a></p><ul><li><input type="checkbox">todo</li><li><input type="checkbox" checked>done</li></ul><tg-math-block>x&lt;y</tg-math-block><pre>  &lt;tag&gt; &amp;\n  end</pre><img src="https://example.com/a.png" alt="ảnh"/>'
    plain = rich_html_plain_text(html)
    assert "A | B\n1 | 2" in plain
    assert "link (https://example.com/?a=1&b=2)" in plain
    assert "[ ] todo\n[x] done" in plain
    assert "x<y\n  <tag> &\n  end" in plain
    assert "ảnh (https://example.com/a.png)" in plain


def test_html_fallback_keeps_code_indentation_blank_lines_and_literal_angle_brackets():
    text = "  &lt;literal&gt;\n\n\n  end\n"
    assert rich_html_plain_text(f"<pre>{text}</pre>") == "  <literal>\n\n\n  end\n"


@pytest.mark.asyncio
async def test_raw_rich_send_channel_business_topic_preserves_payload(monkeypatch):
    client = pyrogram.Client("rich-unit", in_memory=True)
    monkeypatch.setattr(
        client,
        "resolve_peer",
        AsyncMock(
            return_value=raw.types.InputPeerChannel(channel_id=44, access_hash=55)
        ),
    )
    monkeypatch.setattr(
        client,
        "invoke",
        AsyncMock(
            return_value=raw.types.UpdateShortSentMessage(
                id=12, pts=1, pts_count=1, date=1
            )
        ),
    )
    payload = types.InputRichMessage(
        html="<p>Xin chào 🙂 &amp; &lt;</p>", is_rtl=True, skip_entity_detection=True
    ).write()
    assert (
        await send_rich_message(
            client,
            -1000000000044,
            payload,
            message_thread_id=7,
            business_connection_id="business-test",
        )
        == 12
    )
    query = client.invoke.await_args.args[0]
    wire = roundtrip(query)
    assert wire.peer.channel_id == 44
    assert wire.reply_to.reply_to_msg_id == 7
    assert wire.rich_message.html == payload.html
    assert wire.rich_message.rtl and wire.rich_message.noautolink
    assert client.invoke.await_args.kwargs == {
        "business_connection_id": "business-test"
    }


@pytest.mark.parametrize("fail_method", ["convert", "split_entities"])
def test_formatting_failure_fallback_still_respects_utf16_limit(
    monkeypatch, fail_method
):
    def fail(*args, **kwargs):
        raise ValueError("invalid formatter output")

    monkeypatch.setattr(styling.telegramify_markdown, fail_method, fail)
    text = "🙂Xin chào\n" * 1000
    chunks = styling.convert_md_chunks(text)
    assert "".join(chunk for chunk, _ in chunks) == text
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk, _ in chunks)
    assert all(not entities for _, entities in chunks)


def test_plain_split_never_drops_boundary_whitespace_or_splits_emoji():
    text = " a🙂\n b🙂  c🙂 "
    chunks = styling.split_plain_text(text, 4)
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 4 for chunk in chunks)


def test_native_markdown_keeps_tasks_details_math_spoilers_links_and_code():
    source = '# Tiêu đề 🙂\n\n- [ ] việc\n- [x] xong\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n<details><summary>Ẩn</summary>||secret||</details>\n\n$x^2$\n\n```python\nx = "<&>"\n```\n[nguồn](https://example.com)'
    payloads = styling.convert_rich_md(source)
    assert len(payloads) == 1
    wire = roundtrip(payloads[0].write())
    assert isinstance(wire, raw.types.InputRichMessageMarkdown)
    assert wire.markdown == source


@pytest.mark.parametrize(
    "source",
    [
        "- item\n" * 501,
        "|" + "|".join(["a"] * 21) + "|\n|" + "|".join(["---"] * 21) + "|",
        "![a](https://example.com/a.jpg)\n\n" * 51,
        "<details>" * 17 + "x" + "</details>" * 17,
        "| A | B |\n|---|---|\n|" + "🙂" * 10000 + "| B |",
    ],
)
def test_invalid_rich_batch_is_rejected_before_any_chunk_is_sent(source):
    assert styling.convert_rich_md(source) == []


def test_long_unicode_rich_conversion_is_valid_on_native_wire_and_keeps_content():
    text = "🙂<&>" * 12000
    payloads = styling.convert_rich_md(text)
    assert len(payloads) > 1
    assert all(len(payload.html.encode("utf-8")) <= 32768 for payload in payloads)
    assert (
        "".join(
            rich_html_plain_text(roundtrip(payload.write()).html)
            for payload in payloads
        )
        == text
    )
