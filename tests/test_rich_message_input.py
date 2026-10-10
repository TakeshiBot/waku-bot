"""Embedded Telegram file IDs use the bounded existing input pipeline."""

from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pyrogram
import pytest
from pydantic_ai import BinaryContent
from pyrogram import types

from waku.common.rich_message import rich_media_messages
from waku.plugins.agent import input_format, prompt


@pytest.mark.asyncio
async def test_bot_sender_keeps_actual_group_administrator_status(monkeypatch):
    from waku.common import tgmethod

    lookup = AsyncMock(
        return_value=SimpleNamespace(
            status=pyrogram.enums.ChatMemberStatus.ADMINISTRATOR
        )
    )
    monkeypatch.setattr(tgmethod, "get_chat_member", lookup)
    source = message([], chat_type=pyrogram.enums.ChatType.SUPERGROUP)
    source.from_user.is_bot = True
    sender = await input_format.resolve_sender(None, source.chat.id, source)
    lookup.assert_awaited_once_with(None, source.chat.id, source.from_user.id)
    assert sender.kind == "Bot"
    assert sender.status == input_format.tr("administrator")


def photo(unique="photo-a", *, size=10, file_id="native-file-a"):
    return types.Photo(
        file_id=file_id,
        file_unique_id=unique,
        width=2,
        height=2,
        file_size=size,
        date=datetime(2026, 1, 1),
    )


def message(blocks, *, message_id=1, chat_type=pyrogram.enums.ChatType.PRIVATE):
    return types.Message(
        id=message_id,
        date=datetime(2026, 1, 1),
        chat=types.Chat(id=10, type=chat_type, title="group"),
        from_user=types.User(id=12, first_name="User"),
        rich_message=types.RichMessage(blocks=blocks),
    )


@pytest.fixture
def media_config(monkeypatch):
    for name, value in {
        "agent_multimodal": True,
        "agent_multimodal_inputs": ["photo", "video", "audio"],
        "agent_multimodal_input_count": 2,
        "agent_multimodal_max_items": 3,
    }.items():
        monkeypatch.setattr(input_format.app_config, name, value)
    download = AsyncMock(return_value=BytesIO(b"native bytes"))
    monkeypatch.setattr(prompt, "_download_media_with_timeout", download)
    return download


def test_native_media_tree_traversal_and_url_rejection_are_bounded():
    nested = types.RichBlockDetails(
        summary="Details",
        blocks=[
            types.RichBlockPhoto(photo=photo()),
            types.RichBlockPhoto(photo=photo(file_id="http://localhost/secret")),
        ],
    )
    source = message([nested])
    exposed = rich_media_messages(source)
    assert len(exposed) == 1 and exposed[0].photo.file_id == "native-file-a"
    assert exposed[0].id == source.id
    nested.blocks.append(nested)
    assert len(rich_media_messages(source)) == 1
    assert (
        len(
            rich_media_messages(
                message([types.RichBlockPhoto(photo=photo(str(i))) for i in range(100)])
            )
        )
        == 50
    )


@pytest.mark.asyncio
async def test_rich_budget_native_downloads_dedupe_existing_and_enforces_caps(
    media_config,
):
    source = message(
        [
            types.RichBlockPhoto(photo=photo("seen")),
            types.RichBlockPhoto(photo=photo("new", file_id="new-file")),
            types.RichBlockPhoto(photo=photo("new", file_id="duplicate-file")),
            types.RichBlockPhoto(photo=photo("too-big", size=10 * 1024 * 1024 + 1)),
        ]
    )
    lines, binaries, metadata = await input_format.rich_media_contents(
        pyrogram.Client("rich-input", in_memory=True),
        [source],
        initial_seen={"seen": 4},
        start_number=5,
    )
    assert len(binaries) == 1 and binaries[0].data == b"native bytes"
    assert metadata == {"new": 5}
    assert sum("image_number=" in line for line in lines) == 1
    assert sum("referenced_media=" in line for line in lines) == 2
    assert "unprocessed=" in lines[-1]
    media_config.assert_awaited_once()
    assert media_config.await_args.args[1] == "duplicate-file"


@pytest.mark.asyncio
async def test_rich_budget_off_and_post_download_oversize_not_sent(
    media_config, monkeypatch
):
    source = message([types.RichBlockPhoto(photo=photo())])
    monkeypatch.setattr(input_format.app_config, "agent_multimodal", False)
    lines, binaries, metadata = await input_format.rich_media_contents(None, [source])
    assert "unprocessed=" in lines[0] and not binaries and not metadata
    media_config.assert_not_awaited()
    monkeypatch.setattr(input_format.app_config, "agent_multimodal", True)
    media_config.return_value = BytesIO(b"x" * (10 * 1024 * 1024 + 1))
    _, binaries, metadata = await input_format.rich_media_contents(None, [source])
    assert not binaries and not metadata


@pytest.mark.asyncio
async def test_dm_prompt_rich_media_and_text_reach_model(media_config):
    source = message(
        [types.RichBlockParagraph(text="Xin chào"), types.RichBlockPhoto(photo=photo())]
    )
    contents, needs_multimodal, _ = await prompt.get_input_prompt(
        pyrogram.Client("rich-input", in_memory=True), source
    )
    assert "Xin chào" in contents[0]
    assert "image_number=1" in contents[1]
    assert isinstance(contents[2], BinaryContent)
    assert needs_multimodal


@pytest.mark.asyncio
@pytest.mark.parametrize("per_turn, expected_count", [(2, 2), (3, 3)])
async def test_dm_reply_and_current_share_budget_current_priority_and_unique_numbers(
    media_config, monkeypatch, per_turn, expected_count
):
    monkeypatch.setattr(
        input_format.app_config, "agent_multimodal_input_count", per_turn
    )
    reply = message(
        [
            types.RichBlockPhoto(photo=photo("reply-a", file_id="reply-a")),
            types.RichBlockPhoto(photo=photo("reply-b", file_id="reply-b")),
        ],
        message_id=1,
    )
    source = message(
        [
            types.RichBlockPhoto(photo=photo("current-a", file_id="current-a")),
            types.RichBlockPhoto(photo=photo("current-b", file_id="current-b")),
        ],
        message_id=2,
    )
    source.reply_to_message = reply
    source.reply_to_message_id = reply.id
    contents, needs_multimodal, _ = await prompt.get_input_prompt(
        pyrogram.Client("rich-input", in_memory=True), source
    )
    assert sum(isinstance(item, BinaryContent) for item in contents) == expected_count
    labels = "\n".join(item for item in contents if isinstance(item, str))
    assert labels.count("image_number=1 ") == 1
    assert labels.count("image_number=2 ") == 1
    assert labels.count("image_number=3 ") == expected_count - 2
    assert [call.args[1] for call in media_config.await_args_list[:2]] == [
        "current-a",
        "current-b",
    ]
    assert needs_multimodal


@pytest.mark.asyncio
async def test_dm_reply_dedupes_current_attachment_without_repeating_binary(
    media_config,
):
    reply = message(
        [types.RichBlockPhoto(photo=photo("same", file_id="reply-file"))], message_id=1
    )
    source = message(
        [types.RichBlockPhoto(photo=photo("same", file_id="current-file"))],
        message_id=2,
    )
    source.reply_to_message = reply
    source.reply_to_message_id = reply.id
    contents, _, _ = await prompt.get_input_prompt(
        pyrogram.Client("rich-input", in_memory=True), source
    )
    assert sum(isinstance(item, BinaryContent) for item in contents) == 1
    labels = "\n".join(item for item in contents if isinstance(item, str))
    assert labels.count("image_number=1 ") == 1
    assert "referenced_media=1" in labels
    media_config.assert_awaited_once()
    assert media_config.await_args.args[1] == "current-file"


@pytest.mark.asyncio
async def test_dm_ordinary_current_media_consumes_reply_rich_budget(
    media_config, monkeypatch
):
    monkeypatch.setattr(input_format.app_config, "agent_multimodal_input_count", 1)
    reply = message(
        [types.RichBlockPhoto(photo=photo("reply", file_id="reply-file"))], message_id=1
    )
    source = types.Message(
        id=2,
        chat=reply.chat,
        from_user=reply.from_user,
        media=pyrogram.enums.MessageMediaType.PHOTO,
        photo=photo("current", file_id="current-file"),
        reply_to_message=reply,
        reply_to_message_id=reply.id,
    )
    contents, _, _ = await prompt.get_input_prompt(
        pyrogram.Client("rich-input", in_memory=True), source
    )
    assert sum(isinstance(item, BinaryContent) for item in contents) == 1
    media_config.assert_awaited_once()
    assert media_config.await_args.args[1] == "current-file"


@pytest.mark.asyncio
async def test_dm_ordinary_reply_references_current_rich_duplicate(media_config):
    source = message(
        [types.RichBlockPhoto(photo=photo("same", file_id="current-file"))],
        message_id=2,
    )
    reply = types.Message(
        id=1,
        chat=source.chat,
        from_user=source.from_user,
        media=pyrogram.enums.MessageMediaType.PHOTO,
        photo=photo("same", file_id="reply-file"),
    )
    source.reply_to_message = reply
    source.reply_to_message_id = reply.id
    contents, _, _ = await prompt.get_input_prompt(
        pyrogram.Client("rich-input", in_memory=True), source
    )
    assert sum(isinstance(item, BinaryContent) for item in contents) == 1
    assert any(
        "referenced_media=1" in item for item in contents if isinstance(item, str)
    )
    media_config.assert_awaited_once()
    assert media_config.await_args.args[1] == "current-file"


@pytest.mark.asyncio
async def test_group_rich_media_shares_budget_coverage_and_transcription_order(
    media_config, monkeypatch
):
    monkeypatch.setattr(
        input_format,
        "resolve_sender",
        AsyncMock(
            return_value=input_format.SenderInfo(
                name="User", user_id="12", kind="person", status="member"
            )
        ),
    )
    source = message(
        [
            types.RichBlockParagraph(text="two photos"),
            types.RichBlockPhoto(photo=photo("first")),
            types.RichBlockPhoto(photo=photo("second")),
        ],
        chat_type=pyrogram.enums.ChatType.SUPERGROUP,
    )
    contents, meta = await input_format.build_group_prompt(
        pyrogram.Client("rich-input", in_memory=True), source, [], None
    )
    assert len(contents) == 3 and meta == {"first": 1, "second": 2}
    transcribed = input_format.apply_transcriptions(contents, ["one", "two"])
    assert 'image_number=1 transcribed="one"' in transcribed[0]
    assert 'image_number=2 transcribed="two"' in transcribed[0]
    covered, binaries, metadata = await input_format.rich_media_contents(
        None, [source], initial_seen=meta
    )
    assert not binaries and not metadata
    assert all("referenced_media=" in line for line in covered)


@pytest.mark.asyncio
@pytest.mark.parametrize("current_is_rich", [True, False])
async def test_group_current_media_wins_over_reply_across_native_formats(
    media_config, monkeypatch, current_is_rich
):
    monkeypatch.setattr(input_format.app_config, "agent_multimodal_input_count", 1)
    monkeypatch.setattr(
        input_format,
        "resolve_sender",
        AsyncMock(
            return_value=input_format.SenderInfo(
                name="User", user_id="12", kind="person", status="member"
            )
        ),
    )
    rich = message(
        [types.RichBlockPhoto(photo=photo("rich", file_id="rich-file"))],
        message_id=2 if current_is_rich else 1,
        chat_type=pyrogram.enums.ChatType.SUPERGROUP,
    )
    ordinary = types.Message(
        id=1 if current_is_rich else 2,
        chat=rich.chat,
        from_user=rich.from_user,
        media=pyrogram.enums.MessageMediaType.PHOTO,
        photo=photo("ordinary", file_id="ordinary-file"),
    )
    source, reply = (rich, ordinary) if current_is_rich else (ordinary, rich)
    source.reply_to_message = reply
    source.reply_to_message_id = reply.id
    contents, metadata = await input_format.build_group_prompt(
        pyrogram.Client("rich-input", in_memory=True), source, [], None
    )
    assert len(contents) == 2
    assert metadata == {"rich" if current_is_rich else "ordinary": 1}
    media_config.assert_awaited_once()
    assert media_config.await_args.args[1] == (
        "rich-file" if current_is_rich else "ordinary-file"
    )


@pytest.mark.asyncio
async def test_group_mixed_media_transcriptions_follow_current_ordinary_other_rich_order(
    media_config, monkeypatch
):
    monkeypatch.setattr(input_format.app_config, "agent_multimodal_input_count", 3)
    monkeypatch.setattr(
        input_format,
        "resolve_sender",
        AsyncMock(
            return_value=input_format.SenderInfo(
                name="User", user_id="12", kind="person", status="member"
            )
        ),
    )
    source = message(
        [types.RichBlockPhoto(photo=photo("current", file_id="current-file"))],
        message_id=3,
        chat_type=pyrogram.enums.ChatType.SUPERGROUP,
    )
    historical_rich = message(
        [
            types.RichBlockPhoto(
                photo=photo("rich-history", file_id="rich-history-file")
            )
        ],
        message_id=1,
        chat_type=pyrogram.enums.ChatType.SUPERGROUP,
    )
    ordinary = types.Message(
        id=2,
        chat=source.chat,
        from_user=source.from_user,
        media=pyrogram.enums.MessageMediaType.PHOTO,
        photo=photo("ordinary", file_id="ordinary-file"),
    )
    media_config.side_effect = lambda client, file_id: BytesIO(file_id.encode())
    contents, metadata = await input_format.build_group_prompt(
        pyrogram.Client("rich-input", in_memory=True),
        source,
        [historical_rich, ordinary],
        None,
    )
    assert [binary.data for binary in contents[1:]] == [
        b"current-file",
        b"ordinary-file",
        b"rich-history-file",
    ]
    assert metadata == {"current": 1, "ordinary": 2, "rich-history": 3}
    transcribed = input_format.apply_transcriptions(
        contents, ["current", "ordinary", "rich"]
    )
    assert 'image_number=1 transcribed="current"' in transcribed[0]
    assert 'image_number=2 transcribed="ordinary"' in transcribed[0]
    assert 'image_number=3 transcribed="rich"' in transcribed[0]
