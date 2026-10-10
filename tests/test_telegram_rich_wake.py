"""Rich-only messages participate in the same Telegram wake filters."""

from types import SimpleNamespace

import pyrogram
import pytest

from waku.plugins.agent import myfilter


def rich_message(text):
    return SimpleNamespace(
        text=None, caption=None, entities=[],
        rich_message=pyrogram.types.RichMessage(blocks=[
            pyrogram.types.RichBlockParagraph(
                text=text
            )
        ]),
    )


@pytest.mark.asyncio
async def test_rich_only_mentions_wake_bot_and_do_not_require_entities(monkeypatch):
    monkeypatch.setattr(myfilter.app_config, "nickname", "waku")
    client = SimpleNamespace(me=SimpleNamespace(username="waku_test_bot"))
    message = rich_message("waku, giúp tôi ghim tin này")
    assert await myfilter.base_filter_func(None, client, message)
    assert await myfilter.mention_me_filter_func(None, client, message)
    assert not await myfilter.mention_me_filter_func(None, client, rich_message("xin chào"))


@pytest.mark.asyncio
async def test_rich_only_commands_follow_the_existing_command_filter():
    assert not await myfilter.base_filter_func(None, None, rich_message("/config"))
