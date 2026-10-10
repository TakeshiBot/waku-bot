"""Image modes filter both API requests and actual returned media."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from waku.config import _AppConfig
from waku.database.models import ChatConfig
from waku.plugins.agent.tools import send_ops
from waku.plugins.manyacg import manyacg
from waku.services import telegram_images as policy


@pytest.mark.parametrize(
    "mode,allowed", [(0, [False]), (1, [True]), (2, [False, True])]
)
def test_mode_filters_known_ratings_and_rejects_missing_or_untrusted_ratings(
    mode, allowed
):
    assert [r18 for r18 in (False, True) if policy.image_allowed(r18, mode)] == allowed
    for unknown in (None, "false", "true", 0, 1):
        assert not policy.image_allowed(unknown, mode)


def test_existing_chat_defaults_safe_and_roundtrips_separate_discord_mode():
    config = ChatConfig.from_dict({"discord_r18_mode": 2, "setu_enabled": False})
    assert config.telegram_r18_mode == 0 and not config.setu_enabled
    config.telegram_r18_mode = 1
    restored = ChatConfig.from_dict(config.to_dict())
    assert restored.telegram_r18_mode == 1 and restored.discord_r18_mode == 2


@pytest.mark.parametrize("value", [-1, 3, 100])
def test_private_image_mode_rejects_out_of_range_values(value):
    with pytest.raises(ValidationError):
        _AppConfig(token="offline", owners=[1], manyacg_r18_mode=value)


@pytest.mark.asyncio
async def test_group_mode_and_enabled_are_independent_of_private_default(monkeypatch):
    monkeypatch.setattr(policy.app_config, "manyacg_r18_mode", 2)
    getter = AsyncMock(return_value=ChatConfig(setu_enabled=False, telegram_r18_mode=1))
    monkeypatch.setattr(policy.database, "get_chat_config", getter)
    assert await policy.telegram_image_settings(-100123) == (False, 1)
    assert await policy.telegram_image_settings(123) == (True, 2)
    getter.assert_awaited_once_with(-100123)
    getter.return_value.telegram_r18_mode = "invalid"
    assert await policy.telegram_image_settings(-100123) == (False, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", ["", "neko"])
@pytest.mark.parametrize("mode", [0, 1, 2])
async def test_ai_search_passes_mode_and_filters_wrong_api_results(
    monkeypatch, keyword, mode
):
    safe = {"id": "safe", "r18": False, "pictures": [{"regular": "safe.invalid"}]}
    adult = {"id": "adult", "r18": True, "pictures": [{"regular": "adult.invalid"}]}
    unknown = {"id": "unknown", "pictures": [{"regular": "unknown.invalid"}]}
    response = SimpleNamespace(
        status_code=200, json=lambda: {"data": [safe, adult, unknown]}
    )
    get = AsyncMock(return_value=response)
    monkeypatch.setattr(manyacg.httpx_client, "get", get)
    result = await send_ops._fetch_anime_artwork(keyword, r18_mode=mode)
    assert get.await_args.kwargs["params"]["r18"] == mode
    assert result is not None and policy.image_allowed(result[0]["r18"], mode)
    response.json = lambda: {"data": [unknown]}
    assert await send_ops._fetch_anime_artwork(keyword, r18_mode=mode) is None


@pytest.mark.asyncio
async def test_disabled_group_blocks_ai_without_fetching(monkeypatch):
    ctx = SimpleNamespace(
        deps=SimpleNamespace(chat_id=-100123, user_id=7, message=SimpleNamespace(id=99))
    )
    monkeypatch.setattr(
        send_ops, "telegram_image_settings", AsyncMock(return_value=(False, 0))
    )
    fetch = AsyncMock()
    monkeypatch.setattr(send_ops, "_fetch_anime_artwork", fetch)
    assert not (await send_ops.send_anime_photo(ctx)).success
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_does_not_send_out_of_mode_media_even_if_fetcher_returns_it(
    monkeypatch,
):
    ctx = SimpleNamespace(
        deps=SimpleNamespace(chat_id=7, user_id=7, message=SimpleNamespace(id=99))
    )
    monkeypatch.setattr(
        send_ops, "telegram_image_settings", AsyncMock(return_value=(True, 0))
    )
    monkeypatch.setattr(send_ops.common.memttlcache, "get", AsyncMock(return_value=0))
    monkeypatch.setattr(send_ops.common.memttlcache, "set", AsyncMock())
    fetch = AsyncMock(return_value=({"r18": True}, {"regular": "adult.invalid"}))
    send = AsyncMock()
    monkeypatch.setattr(send_ops, "_fetch_anime_artwork", fetch)
    monkeypatch.setattr(send_ops, "_send_anime_photo_single", send)
    assert not (await send_ops.send_anime_photo(ctx)).success
    fetch.assert_awaited_once_with("", r18_mode=0)
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,r18", [(0, False), (1, True), (2, True), (0, True)])
async def test_seg_requests_selected_mode_and_never_sends_mismatched_images(
    monkeypatch, mode, r18
):
    artwork = SimpleNamespace(
        r18=r18,
        pictures=[SimpleNamespace(regular="image.invalid", id="picture")],
        source_url="https://source.invalid",
        title="Test",
    )
    api = SimpleNamespace(
        random_artwork=AsyncMock(
            return_value=SimpleNamespace(status=200, data=[artwork])
        )
    )
    monkeypatch.setattr(manyacg, "manyacg_client", api)
    monkeypatch.setattr(
        manyacg, "telegram_image_settings", AsyncMock(return_value=(True, mode))
    )
    monkeypatch.setattr(
        manyacg.database,
        "get_user_config",
        AsyncMock(return_value=SimpleNamespace(lang="vi")),
    )
    monkeypatch.setattr(
        manyacg.common.memttlcache, "get", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(manyacg.common.memttlcache, "set", AsyncMock())
    message = SimpleNamespace(
        chat=SimpleNamespace(id=7, type="private"),
        sender_chat=None,
        from_user=SimpleNamespace(id=7),
        reply=AsyncMock(),
        reply_photo=AsyncMock(),
    )
    await manyacg.setu_command(Mock(), message)
    api.random_artwork.assert_awaited_once_with(limit=1, r18=mode)
    if policy.image_allowed(r18, mode):
        message.reply_photo.assert_awaited_once()
        assert message.reply_photo.await_args.kwargs["has_spoiler"] is r18
    else:
        message.reply_photo.assert_not_awaited()
