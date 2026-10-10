"""Coalesced Discord previews with one final reply and no model/tool retries."""

from __future__ import annotations

import asyncio
import random

import discord

from .constants import DISCORD_REPLY_DELAY_MAX, DISCORD_REPLY_DELAY_MIN
from .messages import _split_reply


class DiscordReplyStream:
    UPDATE_INTERVAL = 2.0

    def __init__(self, message: discord.Message, deps):
        self.message = message
        self.deps = deps
        self.text = ""
        self.preview: discord.Message | None = None
        self._displayed = ""
        self._task: asyncio.Task | None = None
        self._error: Exception | None = None
        self._closed = False
        self._finalized = False

    async def update(self, text: str):
        if self._closed:
            return
        if self._error is not None:
            raise self._error
        self.text = text
        if self._task is None and text.strip():
            await self._publish()
            self._task = asyncio.create_task(self._loop(), name="discord-reply-stream")

    async def _publish(self):
        chunks = _split_reply(self.text)
        if not chunks or chunks[0] == self._displayed:
            return
        content = chunks[0]
        if self.preview is None:
            reference = discord.MessageReference(
                message_id=self.message.id,
                channel_id=self.message.channel.id,
                guild_id=getattr(self.message.guild, "id", None),
                fail_if_not_exists=False,
            )
            # A lost send ACK is not proof that no reply landed. Never retry
            # the model (or its tools) once preview delivery has been attempted.
            self.deps.side_effects_started = True
            self.preview = await self.message.channel.send(
                content,
                reference=reference,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            self.preview = await self.preview.edit(
                content=content, allowed_mentions=discord.AllowedMentions.none()
            )
        self._displayed = content

    async def _loop(self):
        try:
            while not self._closed:
                await asyncio.sleep(self.UPDATE_INTERVAL)
                if not self._closed:
                    await self._publish()
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self._error = error

    async def _stop(self):
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def finalize(self, text: str):
        if self._finalized:
            return self.preview is not None
        self._finalized = True
        self.text = text
        await self._stop()
        if self._error is not None:
            raise self._error
        chunks = _split_reply(text)
        if self.preview is None:
            return False
        if not chunks:
            await self.preview.delete()
            return True
        mentions = discord.AllowedMentions(
            users=True, roles=False, everyone=False, replied_user=False
        )
        async with self.message.channel.typing():
            self.preview = await self.preview.edit(
                content=chunks[0], allowed_mentions=mentions
            )
            self._displayed = chunks[0]
            for index, chunk in enumerate(chunks[1:], start=1):
                await asyncio.sleep(
                    random.uniform(DISCORD_REPLY_DELAY_MIN, DISCORD_REPLY_DELAY_MAX)
                    + len(chunks[index - 1]) / 900
                )
                await self.message.channel.send(
                    chunk, allowed_mentions=mentions, mention_author=False
                )
        return True

    async def abort(self, note: str | None = None):
        await self._stop()
        if note and self.preview is not None:
            content = (
                _split_reply(self.text)[0] if self.text.strip() else self._displayed
            )
            content = content[: 1900 - len(note) - 2].rstrip() + "\n\n" + note
            try:
                await self.preview.edit(
                    content=content, allowed_mentions=discord.AllowedMentions.none()
                )
            except Exception:
                # The original error remains authoritative. No replacement send.
                pass
