from __future__ import annotations

import asyncio

import discord
from discord import app_commands

from waku.logger import logger

from . import state
from .commands import register_discord_commands
from .constants import DISCORD_MEMBERS_INTENT
from .embeds import discord_command_embed
from .handlers import (
    _handle_discord_admin_command,
    _handle_message,
    _maybe_handle_discord_media_request,
)
from .history import (
    _record_discord_group_memory,
    _remember_discord_emojis,
    _remember_discord_reaction_style,
)
from .media import _send_discord_seg_interaction
from .messages import _is_seg_command, _matches_keyword, _should_wake
from .permissions import _is_discord_user_bot_admin
from .settings import _discord_global_ai_enabled
from .utilities import _channel_name, _guild_name, _message_text


class WakuCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction) -> bool:
        if interaction.guild is None and not _is_discord_user_bot_admin(interaction.user):
            return False
        if state.discord_stopping:
            await interaction.response.send_message(
                embed=discord_command_embed("Waku đang khởi động lại. Thử lại sau một chút nhé."),
                ephemeral=True,
            )
            return False
        task = asyncio.current_task()
        if task is not None:
            state.discord_message_tasks.add(task)
            task.add_done_callback(state.discord_message_tasks.discard)
        return True

    async def on_error(self, interaction, error) -> None:
        if interaction.guild is None and not _is_discord_user_bot_admin(interaction.user):
            return
        logger.warning("Discord slash command failed: {}", type(error).__name__)
        if isinstance(error, app_commands.CommandNotFound):
            text = "Lệnh này đã thay đổi. Mở lại danh sách lệnh `/` và thử lại."
        elif isinstance(error, app_commands.CheckFailure):
            text = "Bạn không có quyền dùng lệnh này trong kênh hiện tại."
        else:
            text = "Không hoàn tất được lệnh. Thử lại sau một chút nhé."
        embed = discord_command_embed(text, title="Không thể thực hiện lệnh")
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(content=None, embed=embed, view=None)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            logger.debug("Discord command error response expired or unavailable")


def _create_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True
    intents.members = DISCORD_MEMBERS_INTENT
    client = discord.Client(intents=intents)
    command_tree = WakuCommandTree(client)
    register_discord_commands(command_tree)
    slash_commands_synced = False

    @command_tree.command(name="seg", description="Gửi ảnh anime/Pixiv")
    @app_commands.describe(keyword="Từ khoá tìm ảnh, có thể bỏ trống")
    async def seg(interaction: discord.Interaction, keyword: str | None = None) -> None:
        await _send_discord_seg_interaction(interaction, keyword)

    @client.event
    async def on_ready() -> None:
        nonlocal slash_commands_synced
        user = client.user
        if user is None:
            logger.warning("Discord client ready without user")
            return
        logger.success(f"Discord AI chat ready as {user} ({user.id})")
        if not slash_commands_synced:
            try:
                synced = await command_tree.sync()
            except discord.HTTPException as e:
                logger.warning(f"Discord slash command sync failed: {type(e).__name__}")
            else:
                slash_commands_synced = True
                logger.info(f"Discord slash commands synced: {len(synced)}")

    @client.event
    async def on_message(message: discord.Message) -> None:
        task = asyncio.current_task()
        if task is not None:
            state.discord_message_tasks.add(task)
        try:
            await _dispatch_message(message)
        finally:
            if task is not None:
                state.discord_message_tasks.discard(task)

    async def _dispatch_message(message: discord.Message) -> None:
        bot_user = client.user
        if bot_user is None or message.author.bot or state.discord_stopping:
            return
        content = _message_text(message)
        if await _handle_discord_admin_command(message):
            return
        if message.guild is None and (
            not isinstance(message.channel, discord.DMChannel)
            or not _is_discord_user_bot_admin(message.author)
        ):
            return
        if await _discord_global_ai_enabled():
            task = asyncio.current_task()
            if task is not None:
                state.discord_ai_tasks.add(task)
            try:
                await _record_discord_group_memory(message)
                await _remember_discord_emojis(message)
                await _remember_discord_reaction_style(message)
            except Exception as error:
                logger.warning(
                    f"Discord background memory update failed: {error.__class__.__name__}"
                )
            finally:
                if task is not None:
                    state.discord_ai_tasks.discard(task)
        should_wake, prompt = await _should_wake(message, bot_user)
        if not should_wake:
            return
        logger.debug(
            "Discord wake: "
            f"guild={_guild_name(message)!r} channel={_channel_name(message)!r} "
            f"user={message.author.id} len={len(content)} "
            f"keyword={_matches_keyword(content)} "
            f"seg_command={_is_seg_command(content)}"
        )
        if await _maybe_handle_discord_media_request(message):
            return
        await _handle_message(message, prompt)

    return client
