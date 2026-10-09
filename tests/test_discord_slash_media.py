"""Slash image requests acknowledge privately before storage/network work."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from waku.discordbot import media
from waku.discordbot.models import DiscordGuildSettings


def request(guild=None):
    response = SimpleNamespace(done=False)
    response.is_done = lambda: response.done

    async def acknowledge(**kwargs):
        response.done = True

    response.defer = AsyncMock(side_effect=acknowledge)
    response.send_message = AsyncMock(side_effect=acknowledge)
    channel = Mock(spec=discord.abc.Messageable)
    channel.id = 20
    return SimpleNamespace(
        response=response, guild=guild, channel=channel,
        user=SimpleNamespace(id=30), edit_original_response=AsyncMock(),
        delete_original_response=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dm", [True, False])
async def test_disabled_images_acknowledge_privately_before_loading_settings(monkeypatch, dm):
    interaction = request(None if dm else SimpleNamespace(id=10))
    monkeypatch.setattr(media, "_channel_allowed", AsyncMock(return_value=True))

    async def load(_):
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        return DiscordGuildSettings(setu_enabled=False)

    monkeypatch.setattr(media, "_discord_dm_settings", load)
    monkeypatch.setattr(media, "_discord_guild_settings", load)
    fetch = AsyncMock()
    monkeypatch.setattr(media, "_fetch_discord_anime_artwork", fetch)
    await media._send_discord_seg_interaction(interaction)
    fetch.assert_not_awaited()
    payload = interaction.edit_original_response.await_args.kwargs
    assert isinstance(payload["embed"], discord.Embed)
    assert payload["content"] is None
    interaction.channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_operator_channel_allowlist_rejects_without_loading_or_fetching(monkeypatch):
    interaction = request(SimpleNamespace(id=10))
    monkeypatch.setattr(media, "_channel_allowed", AsyncMock(return_value=False))
    load = AsyncMock()
    monkeypatch.setattr(media, "_discord_guild_settings", load)
    await media._send_discord_seg_interaction(interaction)
    load.assert_not_awaited()
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert isinstance(interaction.edit_original_response.await_args.kwargs["embed"], discord.Embed)


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [True, False])
async def test_image_result_goes_to_channel_but_status_stays_private(monkeypatch, sent):
    interaction = request(SimpleNamespace(id=10))
    monkeypatch.setattr(media, "_channel_allowed", AsyncMock(return_value=True))
    # An obsolete disabled guild approval cannot prevent a slash image request.
    monkeypatch.setattr(media, "_discord_guild_settings", AsyncMock(return_value=DiscordGuildSettings(enabled=False)))
    monkeypatch.setattr(media, "manyacg_client", object())
    monkeypatch.setattr(media.common, "memttlcache", SimpleNamespace(get=AsyncMock(return_value=False), set=AsyncMock()))
    monkeypatch.setattr(media, "_discord_image_lock", lambda: asyncio.Lock())
    monkeypatch.setattr(media, "_discord_r18_mode", AsyncMock(return_value=0))
    artwork, picture = object(), object()
    fetch = AsyncMock(return_value=(artwork, picture))
    monkeypatch.setattr(media, "_fetch_discord_anime_artwork", fetch)
    send = AsyncMock(return_value=sent)
    monkeypatch.setattr(media, "_send_discord_anime_photo_card", send)
    await media._send_discord_seg_interaction(interaction, " neko ")
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    fetch.assert_awaited_once_with(keyword="neko", r18_mode=0)
    context = send.await_args.args[0]
    assert context.channel is interaction.channel
    assert context.author is interaction.user
    if sent:
        interaction.delete_original_response.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()
    else:
        assert isinstance(interaction.edit_original_response.await_args.kwargs["embed"], discord.Embed)
        interaction.delete_original_response.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("dm", [True, False])
@pytest.mark.parametrize("artwork_link", [True, False])
async def test_image_toggle_also_blocks_text_command_and_artwork_links(monkeypatch, dm, artwork_link):
    message = SimpleNamespace(
        guild=None if dm else SimpleNamespace(id=10),
        author=SimpleNamespace(id=30), channel=SimpleNamespace(send=AsyncMock()),
    )
    settings = AsyncMock(return_value=DiscordGuildSettings(setu_enabled=False))
    monkeypatch.setattr(media, "_discord_dm_settings", settings)
    monkeypatch.setattr(media, "_discord_guild_settings", settings)
    client = SimpleNamespace(fetch_artwork=AsyncMock(), random_artwork=AsyncMock())
    monkeypatch.setattr(media, "manyacg_client", client)
    if artwork_link:
        assert await media._send_discord_artwork(message, "https://example.com/artwork")
    else:
        assert await media._send_discord_seg(message)
    client.fetch_artwork.assert_not_awaited()
    client.random_artwork.assert_not_awaited()
    assert isinstance(message.channel.send.await_args.kwargs["embed"], discord.Embed)
