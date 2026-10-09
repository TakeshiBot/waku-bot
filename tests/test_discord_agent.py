"""Discord AI/domain regressions without importing bot startup or external IO."""

import ast
import asyncio
import io
import random
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
import httpx
import pydantic_ai
import pytest
from pydantic_ai import BinaryContent, ModelRetry
from pydantic_ai.messages import (
    MULTI_MODAL_CONTENT_TYPES,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

ROOT = Path(__file__).resolve().parents[1]


def load_definitions(path, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Assign,
                ast.AnnAssign,
            ),
        )
    ]
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[future, *definitions], type_ignores=[])
            ),
            str(path),
            "exec",
        ),
        namespace,
    )


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


@pytest.fixture
def domain():
    async def compact(messages, *_args, **_kwargs):
        return list(messages)

    config = SimpleNamespace(
        agent_run_timeout=1,
        agent_download_timeout=1,
        agent_small_model_timeout=1,
        agent_model_options={"temperature": 0.4},
        agent_model_multimodal_options={"temperature": 0.2},
        agent_model_small_options={"max_tokens": 50},
        agent_multimodal_max_items=0,
        agent_multimodal_input_count=2,
        agent_multimodal=True,
        agent_multimodal_inputs=["photo"],
        agent_group_memory=True,
        agent_proxy=None,
        cachettl_agent_history=60,
        nickname="waku",
        discord_keywords=None,
        manyacg_bot="example_bot",
    )
    namespace = dict(
        __name__="__main__",
        asyncio=asyncio,
        io=io,
        random=random,
        re=re,
        Counter=Counter,
        dataclass=dataclass,
        replace=replace,
        UTC=UTC,
        datetime=datetime,
        ZoneInfo=ZoneInfo,
        ZoneInfoNotFoundError=ZoneInfoNotFoundError,
        BOT_TIMEZONE=ZoneInfo("Asia/Ho_Chi_Minh"),
        SimpleNamespace=SimpleNamespace,
        urljoin=urljoin,
        MAX_REDIRECTS=5,
        is_safe_web_url=Mock(return_value=True),
        discord=discord,
        httpx=httpx,
        pydantic_ai=pydantic_ai,
        BinaryContent=BinaryContent,
        ModelRequest=ModelRequest,
        ModelResponse=ModelResponse,
        TextPart=TextPart,
        ToolReturnPart=ToolReturnPart,
        UserPromptPart=UserPromptPart,
        MULTI_MODAL_CONTENT_TYPES=MULTI_MODAL_CONTENT_TYPES,
        ModelRetry=ModelRetry,
        app_config=config,
        logger=Mock(),
        common=SimpleNamespace(
            memttlcache=SimpleNamespace(get=AsyncMock(return_value=[]), set=AsyncMock())
        ),
        state=SimpleNamespace(
            discord_client=None, discord_agent=None, discord_recovery_agent=None
        ),
        provider=SimpleNamespace(
            make_model_settings=Mock(side_effect=lambda options: options or None)
        ),
        compact_history=AsyncMock(side_effect=compact),
        truncate_multimodal=Mock(side_effect=lambda messages, _limit: list(messages)),
        get_agent_http_client=Mock(return_value=None),
        build_usage_limits=Mock(return_value=object()),
        _discord_guild_settings=AsyncMock(
            return_value=SimpleNamespace(
                enabled=True,
                ai_reply=True,
                setu_enabled=True,
                group_memory_enabled=True,
                lang="vi",
            )
        ),
        _discord_dm_ai_reply_enabled=AsyncMock(return_value=False),
        _can_read_message_history=Mock(return_value=True),
        _can_view_channel=Mock(return_value=True),
        _channel_allowed=AsyncMock(return_value=True),
        _discord_r18_mode=AsyncMock(return_value=0),
        _contains_r18_keyword=Mock(return_value=False),
        _discord_allowed_mentions=Mock(return_value=discord.AllowedMentions.none()),
        _is_discord_bot_admin=Mock(return_value=False),
        manyacg_client=None,
        manyacg_service=SimpleNamespace(ARTWORK_ALL_REGEX=[]),
    )
    package = ROOT / "waku/discordbot"
    for name in (
        "constants",
        "models",
        "utilities",
        "history",
        "media",
        "messages",
        "agent",
        "tools/search",
        "tools/server",
        "tools/images",
        "tools/reactions",
        "tools/users",
    ):
        load_definitions(package / f"{name}.py", namespace)
    return namespace


def message():
    return SimpleNamespace(
        channel=SimpleNamespace(
            id=8, name="general", typing=lambda: Typing(), send=AsyncMock()
        ),
        author=SimpleNamespace(id=7, name="User", display_name="User", bot=False),
        guild=None,
        id=9,
        content="hello",
        clean_content="hello",
        mentions=[],
        reference=None,
        attachments=[],
        stickers=[],
    )


def test_sanitize_history_preserves_text_beside_tools_and_does_not_mutate(domain):
    request = ModelRequest(
        parts=[UserPromptPart("hello"), ToolReturnPart("lookup", "private", "a")]
    )
    response = ModelResponse(
        parts=[TextPart("answer"), ToolCallPart("lookup", {}, "a")]
    )
    empty = ModelResponse(parts=[TextPart(" ")])
    retry = ModelRequest(
        parts=[RetryPromptPart("again", tool_name="lookup", tool_call_id="a")]
    )
    cleaned = domain["_sanitize_discord_history"]([request, response, empty, retry])
    assert len(cleaned) == 2
    assert cleaned[0].parts == [request.parts[0]]
    assert cleaned[1].parts == [response.parts[0]]
    assert len(request.parts) == len(response.parts) == 2


def test_text_model_history_strips_binary_but_preserves_text_and_instructions(domain):
    picture = BinaryContent(b"private image", media_type="image/png")
    request = ModelRequest(
        parts=[UserPromptPart(["describe", picture])], instructions="Keep instructions"
    )
    cleaned = domain["_strip_multimodal_history_for_text_model"]([request])
    assert cleaned[0].instructions == request.instructions
    assert cleaned[0].parts[0].content[0] == "describe"
    assert all(isinstance(item, str) for item in cleaned[0].parts[0].content)
    assert request.parts[0].content[1] is picture


async def test_prepare_history_uses_shared_compaction_limits_and_run_model(domain):
    domain["app_config"].agent_multimodal_max_items = 2
    original = [ModelRequest(parts=[UserPromptPart("hello")])]
    model, agent, deps = object(), SimpleNamespace(model=object()), object()
    assert (
        await domain["_prepare_discord_history"](original, model, deps, agent)
        == original
    )
    domain["truncate_multimodal"].assert_called_once_with(original, 2)
    domain["compact_history"].assert_awaited_once_with(
        original, model, deps=deps, agent=agent
    )


async def test_agent_run_deadline_includes_history_preparation(domain):
    async def stalled(*_args, **_kwargs):
        await asyncio.Event().wait()

    domain["state"].discord_agent = SimpleNamespace(run=AsyncMock())
    domain["_prepare_discord_history"] = stalled
    domain["app_config"].agent_run_timeout = 0.01
    with pytest.raises(TimeoutError):
        await domain["_run_discord_agent_once"](
            message(), ["hello"], "history", [], None
        )
    domain["state"].discord_agent.run.assert_not_awaited()


@pytest.mark.parametrize("multimodal", [False, True])
async def test_agent_applies_model_options_and_caches_sanitized_history(
    domain, multimodal
):
    history = [
        ModelResponse(parts=[TextPart("answer"), ToolCallPart("lookup", {}, "a")])
    ]
    run = AsyncMock(
        return_value=SimpleNamespace(output="answer", all_messages=lambda: history)
    )
    domain["state"].discord_agent = SimpleNamespace(run=run)
    domain["_send_reply"] = AsyncMock()
    prompt = (
        ["hello", BinaryContent(b"image", media_type="image/png")]
        if multimodal
        else ["hello"]
    )
    await domain["_run_discord_agent_once"](message(), prompt, "history", [], None)
    assert run.await_args.kwargs["model_settings"] == {
        "temperature": 0.2 if multimodal else 0.4
    }
    assert (
        run.await_args.kwargs["usage_limits"]
        is domain["build_usage_limits"].return_value
    )
    cached = domain["common"].memttlcache.set.await_args.args[1]
    assert cached[0].parts == [history[0].parts[0]]


async def test_delivery_failure_never_retries_model_or_stores_history(domain):
    run = AsyncMock(
        return_value=SimpleNamespace(output="answer", all_messages=lambda: [])
    )
    domain["state"].discord_agent = SimpleNamespace(run=run)
    domain["_send_reply"] = AsyncMock(side_effect=RuntimeError("send failed"))
    with pytest.raises(domain["DiscordPostRunError"], match="delivery"):
        await domain["_run_discord_agent_once"](
            message(), ["hello"], "history", [], None
        )
    run.assert_awaited_once()
    domain["common"].memttlcache.set.assert_not_awaited()


async def test_native_pydantic_agent_accepts_deps_settings_limits_and_history(domain):
    async def respond(messages, _info):
        assert isinstance(messages[-1], ModelRequest)
        return ModelResponse(parts=[TextPart("native answer")])

    domain["build_usage_limits"].return_value = UsageLimits(request_limit=2)
    domain["state"].discord_agent = pydantic_ai.Agent(
        FunctionModel(respond), deps_type=domain["DiscordContextDeps"], output_type=str
    )
    domain["_send_reply"] = AsyncMock()
    await domain["_run_discord_agent_once"](message(), ["hello"], "history", [], None)
    domain["_send_reply"].assert_awaited_once()
    assert domain["_send_reply"].await_args.args[1] == "native answer"
    cached = domain["common"].memttlcache.set.await_args.args[1]
    assert isinstance(cached[-1], ModelResponse)


def test_history_error_classification_excludes_unrelated_provider_400(domain):
    unrelated = RuntimeError("Thinking mode does not support tool_choice")
    unrelated.status_code = 400
    assert not domain["_is_discord_history_error"](unrelated)
    history_error = RuntimeError(
        "messages[2].tool_call_id did not match previous message"
    )
    history_error.status_code = 400
    assert domain["_is_discord_history_error"](history_error)


def test_reply_chunks_preserve_text_and_code_fences(domain):
    chunks = domain["_split_reply"]("a" * 5000)
    assert len(chunks) == 3 and "".join(chunks) == "a" * 5000
    code = "```python\n" + "\n".join("x = 1" for _ in range(500)) + "\n```"
    chunks = domain["_split_reply"](code)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1900 and chunk.count("```") % 2 == 0 for chunk in chunks)
    oversized = domain["_split_reply"]("x" * 20000)
    assert len(oversized) == domain["DISCORD_REPLY_MAX_MESSAGES"]
    assert "đã lược bớt" in oversized[-1]


async def test_reply_blocks_everyone_roles_and_reply_ping(domain):
    incoming = message()
    await domain["_send_reply"](incoming, "hello @everyone <@&123456789012345678>")
    mentions = incoming.channel.send.await_args.kwargs["allowed_mentions"]
    assert not mentions.everyone and not mentions.roles and not mentions.replied_user


async def test_channel_resolution_rejects_other_guild_and_dm_targets(domain):
    incoming = message()
    incoming.guild = SimpleNamespace(
        id=1,
        get_channel_or_thread=lambda _id: None,
        get_channel=lambda _id: None,
        get_thread=lambda _id: None,
    )
    other = SimpleNamespace(id=99, guild=SimpleNamespace(id=2))
    client = SimpleNamespace(
        get_channel=Mock(return_value=other), fetch_channel=AsyncMock()
    )
    domain["state"].discord_client = client
    assert await domain["_resolve_discord_channel"](incoming, 99) is None
    client.fetch_channel.assert_not_awaited()
    incoming.guild = None
    assert await domain["_resolve_discord_channel"](incoming, 8) is incoming.channel
    assert await domain["_resolve_discord_channel"](incoming, 99) is None


async def test_prompt_respects_combined_multimodal_budget_and_timezone(domain):
    domain["_discord_attachment_contents"] = AsyncMock(
        return_value=(
            ["image"],
            [
                BinaryContent(b"a", media_type="image/png"),
                BinaryContent(b"b", media_type="image/png"),
            ],
        )
    )
    domain["_discord_sticker_contents"] = AsyncMock(
        return_value=([], [BinaryContent(b"c", media_type="image/png")])
    )
    domain["_discord_emoji_hint"] = AsyncMock(return_value=None)
    domain["_discord_reaction_hint"] = AsyncMock(return_value=None)
    domain["app_config"].agent_multimodal_max_items = 1
    prompt, multimedia = await domain["_build_prompt_impl"](message(), "describe")
    assert multimedia and len(prompt) == 2
    assert "omitted" in prompt[0] and "+07:00" in prompt[0]
    assert domain["DiscordGuildSettings"]().lang == "vi"


async def test_unknown_size_attachment_uses_bounded_download(domain):
    download = AsyncMock(return_value=(b"image", "image/png"))
    domain["_download_discord_media"] = download
    attachment = SimpleNamespace(
        filename="photo.png",
        content_type="image/png",
        size=0,
        url="https://example.test/photo.png",
        read=AsyncMock(),
    )
    summaries, contents = await domain["_discord_attachment_contents"](
        SimpleNamespace(attachments=[attachment])
    )
    assert len(summaries) == 1 and contents[0].data == b"image"
    attachment.read.assert_not_awaited()
    download.assert_awaited_once_with(attachment.url)


@pytest.mark.parametrize("oversized", [False, True])
async def test_media_stream_reuses_proxy_client_without_closing_it(domain, oversized):
    class Response:
        status_code = 200
        headers = {"content-type": "image/png; charset=binary"}

        async def aiter_bytes(self):
            yield b"123"
            yield b"456"

    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=Response())
    manager.__aexit__ = AsyncMock()
    client = SimpleNamespace(stream=Mock(return_value=manager), aclose=AsyncMock())
    domain["get_agent_http_client"].return_value = client
    result = await domain["_download_discord_media"](
        "https://example.test/image.png", max_bytes=5 if oversized else 6
    )
    assert result is None if oversized else result == (b"123456", "image/png")
    client.aclose.assert_not_awaited()
    assert client.stream.call_args.kwargs["follow_redirects"] is False


async def test_media_redirect_checks_target_before_second_request(domain):
    response = SimpleNamespace(
        status_code=302, headers={"location": "http://127.0.0.1/private"}
    )
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=response)
    manager.__aexit__ = AsyncMock()
    client = SimpleNamespace(stream=Mock(return_value=manager), aclose=AsyncMock())
    domain["get_agent_http_client"].return_value = client
    domain["is_safe_web_url"].side_effect = [True, False]
    assert (
        await domain["_download_discord_media"]("https://example.test/image.png")
        is None
    )
    client.stream.assert_called_once()
    assert domain["is_safe_web_url"].call_args_list[-1].args == (
        "http://127.0.0.1/private",
    )


def test_keyword_defaults_to_new_nickname_and_empty_list_disables_keyword_wake(domain):
    assert domain["_discord_wake_keywords"]() == ["waku"]
    domain["app_config"].discord_keywords = []
    assert not domain["_matches_keyword"]("waku hello")


async def test_message_search_requires_requester_visibility_before_reading(domain):
    incoming = message()
    incoming.guild = SimpleNamespace(id=1)
    incoming.channel.history = Mock()
    domain["_can_read_message_history"].return_value = False
    result = await domain["search_discord_messages"](
        SimpleNamespace(deps=SimpleNamespace(message=incoming)), "secret"
    )
    assert not result.success
    incoming.channel.history.assert_not_called()
    domain["_can_read_message_history"].assert_called_once_with(
        incoming.channel, incoming.guild, requester=incoming.author
    )


async def test_reaction_to_other_message_requires_requester_history_permission(domain):
    incoming = message()
    incoming.channel.fetch_message = AsyncMock()
    domain["_can_read_message_history"].return_value = False
    result = await domain["send_discord_reaction"](
        SimpleNamespace(deps=SimpleNamespace(message=incoming)),
        "👍",
        target_message_id=100,
    )
    assert not result.success
    incoming.channel.fetch_message.assert_not_awaited()


@pytest.mark.parametrize("tool", ["send_discord_anime_photo", "send_discord_web_image"])
async def test_disabled_images_stop_tool_before_network(domain, tool):
    incoming = message()
    incoming.guild = SimpleNamespace(id=1)
    domain["_discord_guild_settings"].return_value.setu_enabled = False
    domain["_search_web_images"] = AsyncMock()
    result = await domain[tool](
        SimpleNamespace(deps=SimpleNamespace(message=incoming)), "anime"
    )
    assert not result.success
    domain["_search_web_images"].assert_not_awaited()


@pytest.mark.parametrize("allowed", [False, True])
async def test_cross_channel_image_send_requires_allowlist_and_admin_before_fetch(
    domain, allowed
):
    incoming = message()
    incoming.guild = SimpleNamespace(id=1)
    target = MagicMock(spec=discord.TextChannel)
    target.id = 99
    domain["_resolve_discord_channel"] = AsyncMock(return_value=target)
    domain["_channel_allowed"].return_value = allowed
    domain["_fetch_discord_anime_artwork"] = AsyncMock()
    result = await domain["send_discord_anime_photo"](
        SimpleNamespace(deps=SimpleNamespace(message=incoming)), target_channel_id=99
    )
    assert not result.success
    assert ("Only bot admins" if allowed else "disabled") in result.message
    domain["_fetch_discord_anime_artwork"].assert_not_awaited()


async def test_guild_user_search_does_not_fetch_user_from_another_server(domain):
    incoming = message()
    incoming.guild = SimpleNamespace(
        get_member=Mock(return_value=None),
        fetch_member=AsyncMock(side_effect=LookupError()),
        members=[],
    )
    client = SimpleNamespace(get_user=Mock(), fetch_user=AsyncMock())
    domain["state"].discord_client = client
    assert await domain["_find_discord_users"](incoming, "123456789012345678") == []
    client.get_user.assert_not_called()
    client.fetch_user.assert_not_awaited()


async def test_r18_spoiler_download_failure_does_not_expose_image(domain):
    incoming = message()
    domain["_download_image_bytes"] = AsyncMock(return_value=None)
    assert not await domain["_send_discord_image_embed"](
        incoming, discord.Embed(), "https://example.test/nsfw.jpg", spoiler=True
    )
    incoming.channel.send.assert_not_awaited()


async def test_group_memory_retains_batch_after_failure_and_retries(domain):
    domain["_DISCORD_GROUP_MEMORY_BATCH_SIZE"] = 1
    add = AsyncMock(side_effect=[RuntimeError("temporary"), "stored"])
    domain["_get_powermemory"] = lambda: SimpleNamespace(add=add)
    cache = {}

    async def get(key, default=None):
        return cache.get(key, default)

    async def set_value(key, value, ttl=None):
        cache[key] = value

    domain["common"].memttlcache.get = get
    domain["common"].memttlcache.set = set_value
    incoming = message()
    incoming.guild = SimpleNamespace(id=1)
    incoming.created_at = datetime.now(UTC)
    await domain["_record_discord_group_memory"](incoming)
    key = domain["_discord_group_messages_key"](1)
    assert len(cache[key]) == 1
    cache.pop(domain["_discord_group_memory_update_key"](1) + ":retry")
    await domain["_record_discord_group_memory"](incoming)
    assert cache[key] == []
    assert add.await_count == 2
