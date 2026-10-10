"""Slash-command utilities; settings responses stay private to the requester."""

from __future__ import annotations

from types import SimpleNamespace

import discord
from discord import app_commands

from waku import common
from waku.config import app_config
from waku.logger import logger

from . import state
from .embeds import discord_command_embed
from .messages import _discord_wake_keywords
from .permissions import _is_discord_user_bot_admin
from .settings import (
    _discord_global_ai_enabled,
    _discord_guild_settings,
    _history_key,
    _waiting_key,
)
from .views.config import open_discord_config

_INVITE_URL = (
    "https://discord.com/oauth2/authorize?client_id={bot_id}"
    "&permissions=274878032960&integration_type=0&scope=bot+applications.commands"
)


async def _ack(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def _render(interaction, description, *, title="Waku", view=None):
    return await interaction.edit_original_response(
        content=None,
        embed=discord_command_embed(description, title=title),
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def _context(interaction):
    return SimpleNamespace(
        guild=interaction.guild, channel=interaction.channel, author=interaction.user
    )


def _can_use_commands(interaction) -> bool:
    return interaction.guild is not None or (
        isinstance(interaction.channel, discord.DMChannel)
        and _is_discord_user_bot_admin(interaction.user)
    )


def _can_configure(interaction) -> bool:
    if interaction.guild is None:
        return _can_use_commands(interaction)
    permissions = interaction.permissions
    return bool(
        interaction.guild.owner_id == interaction.user.id
        or (permissions and permissions.administrator)
        or _is_discord_user_bot_admin(interaction.user)
    )


async def config_command(interaction: discord.Interaction) -> None:
    if not _can_use_commands(interaction):
        return
    if not _can_configure(interaction):
        await _ack(interaction)
        await _render(
            interaction,
            "Chỉ chủ server hoặc quản trị viên được thay đổi cấu hình.",
            title="Không đủ quyền",
        )
        return
    # This helper acknowledges before reading settings and binds the ephemeral
    # view to its original requester/guild.
    await open_discord_config(interaction)


async def help_command(interaction: discord.Interaction) -> None:
    if not _can_use_commands(interaction):
        return
    await _ack(interaction)
    keywords = (
        ", ".join(f"`{keyword}`" for keyword in _discord_wake_keywords()) or "không có"
    )
    text = (
        "**Trò chuyện**\n"
        f"Tag Waku, trả lời tin của Waku hoặc dùng từ khoá {keywords}. "
        "Waku hoạt động ngay khi được thêm vào server; không cần xin duyệt. "
        "Chỉ bot admin được trò chuyện trong DM; bật/tắt chat DM bằng `/config`.\n\n"
        "**Lệnh**\n"
        "`/config` — cài đặt server (chủ server hoặc admin); bot admin dùng được trong DM.\n"
        "`/help` — hướng dẫn sử dụng.\n"
        "`/forget` — xoá ngữ cảnh của bạn ở kênh hiện tại.\n"
        "`/invite` — link mời Waku vào server.\n"
        "`/seg [keyword]` — gửi ảnh anime/Pixiv; vẫn dùng được `!seg`.\n"
        "`/clean [amount]` — dọn tối đa 50 tin của Waku (người có quyền)."
    )
    if _is_discord_user_bot_admin(interaction.user):
        text += "\n**Quản trị bot trong DM:** mọi slash command ở trên và `!info [server_id]`, `!server`, `!bc`."
    await _render(interaction, text, title="Trợ giúp Waku")


async def send_discord_info_message(
    message: discord.Message, arguments: str = ""
) -> bool:
    """Send an operational summary only to a configured bot admin in DM."""
    if (
        message.guild is not None
        or not isinstance(message.channel, discord.DMChannel)
        or message.author.bot
        or not _is_discord_user_bot_admin(message.author)
    ):
        return False
    client = state.discord_client
    guild_id_text = arguments.strip()
    if guild_id_text:
        if (
            not guild_id_text.isascii()
            or not guild_id_text.isdecimal()
            or int(guild_id_text) <= 0
        ):
            description = (
                "Cú pháp: `!info [server_id]`. Server ID phải là số nguyên dương."
            )
        else:
            guild = client.get_guild(int(guild_id_text)) if client is not None else None
            if guild is None:
                description = "Waku chưa tham gia server này. Dùng `!server` để xem các server hiện có."
            else:
                settings = await _discord_guild_settings(guild)
                global_ai = await _discord_global_ai_enabled()
                owner = getattr(guild, "owner", None)
                description = (
                    f"Server: **{guild.name}**\n"
                    f"Guild ID: `{guild.id}`\n"
                    f"Thành viên: **{guild.member_count if guild.member_count is not None else 'chưa rõ'}**\n"
                    f"Chủ server: **{getattr(owner, 'display_name', 'chưa có trong cache')}** (`{guild.owner_id}`)\n"
                    f"Kênh: **{len(guild.channels)}**\n"
                    f"AI Reply đã lưu: **{'BẬT' if settings.ai_reply else 'TẮT'}**\n"
                    f"AI toàn bộ Discord: **{'BẬT' if global_ai else 'TẮT'}**\n"
                    f"AI Reply hiệu lực: **{'BẬT' if global_ai and settings.ai_reply else 'TẮT'}**\n"
                    f"Trả lời bot khác: **{'BẬT' if settings.reply_to_bots else 'TẮT'}**\n"
                    f"Bộ nhớ kênh: **{'BẬT' if settings.group_memory_enabled else 'TẮT'}**\n"
                    f"Ảnh: **{'BẬT' if settings.setu_enabled else 'TẮT'}**\n"
                    f"R18: `{settings.r18_mode}`\n"
                    f"Ngôn ngữ: `{settings.lang}`"
                )
        await message.channel.send(
            embed=discord_command_embed(description, title="Thông tin server Waku"),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    bot_user = getattr(client, "user", None)
    global_ai = await _discord_global_ai_enabled()
    guilds = getattr(client, "guilds", [])
    ready = client is not None and client.is_ready()
    latency = getattr(client, "latency", None)
    latency_text = (
        f"{latency * 1000:.0f} ms"
        if isinstance(latency, int | float) and 0 <= latency < float("inf")
        else "chưa rõ"
    )
    description = (
        f"Bot: **{getattr(bot_user, 'name', 'Waku')}** (`{getattr(bot_user, 'id', 'chưa rõ')}`)\n"
        f"Kết nối Discord: **{'SẴN SÀNG' if ready else 'CHƯA SẴN SÀNG'}**\n"
        f"Server đang tham gia: **{len(guilds)}**\n"
        f"Thành viên trong các server: **{sum(guild.member_count or 0 for guild in guilds)}**\n"
        f"Độ trễ: **{latency_text}**\n"
        f"AI toàn bộ Discord: **{'BẬT' if global_ai else 'TẮT'}**\n"
        f"AI đang sẵn sàng: **{'BẬT' if global_ai and app_config.agent and state.discord_agent is not None else 'TẮT'}**\n"
        "Bot admin dùng được mọi lệnh và bật/tắt chat DM trong `/config`."
    )
    await message.channel.send(
        embed=discord_command_embed(description, title="Thông tin Waku"),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return True


async def forget_command(interaction: discord.Interaction) -> None:
    if not _can_use_commands(interaction):
        return
    await _ack(interaction)
    if interaction.channel is None:
        await _render(
            interaction,
            "Không xác định được kênh trò chuyện.",
            title="Không thể xoá ngữ cảnh",
        )
        return
    waiting_key = _waiting_key(interaction.user.id)
    async with state._discord_turn_lock(interaction.user.id):
        busy = bool(await common.memstore.get(waiting_key))
        if not busy:
            await common.memstore.set(waiting_key, True)
    if busy:
        await _render(
            interaction,
            "Waku đang xử lý tin nhắn của bạn. Chờ xong rồi dùng `/forget`.",
            title="Waku đang xử lý",
        )
        return
    # Claim the same lock/flag as the AI handler before awaiting cache IO.
    try:
        key = await _history_key(_context(interaction))
        await common.memttlcache.delete(key)
    finally:
        await common.memstore.delete(waiting_key)
    await _render(
        interaction, "Đã xoá ngữ cảnh của bạn ở kênh hiện tại.", title="Đã quên"
    )


async def invite_command(interaction: discord.Interaction) -> None:
    if not _can_use_commands(interaction):
        return
    await _ack(interaction)
    bot_user = getattr(interaction.client, "user", None)
    if bot_user is None:
        await _render(
            interaction,
            "Waku chưa sẵn sàng để tạo link mời. Thử lại sau nhé.",
            title="Link mời Waku",
        )
        return
    view = discord.ui.View(timeout=300)
    view.add_item(
        discord.ui.Button(
            label="Mời Waku vào server",
            style=discord.ButtonStyle.link,
            url=_INVITE_URL.format(bot_id=bot_user.id),
        )
    )
    await _render(
        interaction,
        "Bấm nút để thêm Waku vào server. Không cần xin duyệt sau khi thêm.",
        title="Mời Waku vào server",
        view=view,
    )


async def clean_command(
    interaction: discord.Interaction, amount: app_commands.Range[int, 1, 50] = 50
) -> None:
    if not _can_use_commands(interaction):
        return
    await _ack(interaction)
    permissions = interaction.permissions
    if interaction.guild is not None and not (
        interaction.guild.owner_id == interaction.user.id
        or permissions.administrator
        or permissions.manage_messages
    ):
        await _render(
            interaction,
            "Bạn cần quyền Manage Messages hoặc Administrator để dùng `/clean`.",
            title="Không đủ quyền",
        )
        return
    if not 1 <= amount <= 50:
        await _render(
            interaction,
            "Số tin cần dọn phải từ 1 đến 50.",
            title="Giá trị không hợp lệ",
        )
        return
    channel = interaction.channel
    history = getattr(channel, "history", None)
    bot_user = getattr(interaction.client, "user", None)
    if bot_user is None or not callable(history):
        await _render(
            interaction,
            "Không đọc được lịch sử ở kênh này.",
            title="Không thể dọn tin nhắn",
        )
        return
    deleted = 0
    try:
        async for candidate in history(limit=1000):
            if candidate.author.id != bot_user.id:
                continue
            await candidate.delete()
            deleted += 1
            if deleted >= amount:
                break
    except discord.HTTPException as error:
        logger.warning(f"Discord clean failed: {error.__class__.__name__}")
        await _render(
            interaction,
            f"Đã xoá {deleted} tin của Waku trước khi Discord báo lỗi. Kiểm tra quyền đọc lịch sử/xoá tin rồi thử lại.",
            title="Dọn tin nhắn chưa hoàn tất",
        )
        return
    await _render(
        interaction, f"Đã xoá {deleted} tin nhắn của Waku.", title="Đã dọn tin nhắn"
    )


def register_discord_commands(tree: app_commands.CommandTree) -> None:
    for name, description, callback in (
        ("config", "Cài đặt Waku cho server hoặc bot admin trong DM", config_command),
        ("help", "Hướng dẫn dùng Waku", help_command),
        ("forget", "Xoá ngữ cảnh trò chuyện của bạn ở kênh này", forget_command),
        ("invite", "Mời Waku vào server", invite_command),
        ("clean", "Dọn tối đa 50 tin nhắn của Waku", clean_command),
    ):
        tree.command(name=name, description=description)(callback)
