"""Private administrator prefix commands for Discord announcements."""

from __future__ import annotations

from datetime import UTC, datetime

import discord

from waku.logger import logger

from . import state
from .embeds import discord_command_embed
from .permissions import _is_discord_user_bot_admin

__all__ = ["send_discord_broadcast_message"]


async def _respond_to_broadcast_message(
    message: discord.Message,
    description: str,
    *,
    title: str = "Phát thông báo",
    color: discord.Color | None = None,
) -> None:
    await message.channel.send(
        embed=discord_command_embed(description, title=title, color=color),
        allowed_mentions=discord.AllowedMentions.none(),
    )


def _broadcast_bot_member(guild: discord.Guild) -> discord.Member | None:
    if guild.me is not None:
        return guild.me
    client = state.discord_client
    if client is not None and client.user is not None:
        return guild.get_member(client.user.id)
    return None


def _can_send_broadcast(channel: object, member: discord.Member | None) -> bool:
    permissions_for = getattr(channel, "permissions_for", None)
    if member is None or not callable(permissions_for):
        return False
    permissions = permissions_for(member)
    return bool(
        permissions.view_channel
        and permissions.send_messages
        and permissions.embed_links
    )


async def _broadcast_channel(
    guild: discord.Guild,
    preferred_channel: object | None = None,
) -> tuple[discord.abc.Messageable | None, bool]:
    """Choose a writable channel, preferring the invoking channel when available."""
    member = _broadcast_bot_member(guild)
    if (
        preferred_channel is not None
        and isinstance(preferred_channel, discord.abc.Messageable)
        and _can_send_broadcast(preferred_channel, member)
    ):
        return preferred_channel, False

    candidates: list[object] = []
    if guild.system_channel is not None:
        candidates.append(guild.system_channel)
    candidates.extend(guild.text_channels)
    if not guild.text_channels:
        try:
            candidates.extend(
                channel
                for channel in await guild.fetch_channels()
                if isinstance(channel, discord.TextChannel)
            )
        except discord.HTTPException as error:
            logger.debug(
                f"Discord broadcast channel fetch failed for guild={guild.id}: {error}"
            )

    seen: set[int] = set()
    for candidate in candidates:
        channel_id = getattr(candidate, "id", None)
        if channel_id is None or channel_id in seen:
            continue
        seen.add(channel_id)
        if isinstance(candidate, discord.abc.Messageable) and _can_send_broadcast(
            candidate, member
        ):
            return candidate, True
    return None, True


def _parse_broadcast_arguments(arguments: str) -> tuple[str, str] | None:
    parts = arguments.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    target, text = parts[0].casefold(), parts[1].replace("\\n", "\n").strip()
    if target != "all" and not (
        target.isascii()
        and target.isdigit()
        and len(target) <= 20
        and 0 < int(target) < 2**64
    ):
        return None
    return target, text


async def send_discord_broadcast_message(
    message: discord.Message, arguments: str
) -> None:
    """Handle ``!bc all <text>`` or ``!bc <guild_id> <text>`` in an admin's DM.

    Unauthorized callers and guild invocations are deliberately silent. The
    caller's DM receives only status/error embeds; announcements go exclusively
    to the explicitly selected joined guilds.
    """
    if (
        message.guild is not None
        or not isinstance(message.channel, discord.DMChannel)
        or message.author.bot
        or not _is_discord_user_bot_admin(message.author)
    ):
        return
    parsed = _parse_broadcast_arguments(arguments)
    if parsed is None:
        await _respond_to_broadcast_message(
            message,
            "Cú pháp: `!bc all <nội dung>` hoặc `!bc <server_id> <nội dung>`.\n"
            "Dùng `\\n` để xuống dòng. Lệnh chỉ dùng trong DM với Waku.",
            title="Cú pháp phát thông báo",
            color=discord.Color.orange(),
        )
        return
    target, text = parsed
    if not text:
        await _respond_to_broadcast_message(
            message,
            "Nội dung thông báo không được để trống.",
            title="Nội dung không hợp lệ",
            color=discord.Color.orange(),
        )
        return
    if len(text) > 4000:
        await _respond_to_broadcast_message(
            message,
            "Nội dung tối đa là 4.000 ký tự để hiển thị trọn vẹn trong embed.",
            title="Nội dung quá dài",
            color=discord.Color.orange(),
        )
        return
    client = state.discord_client
    if client is None:
        await _respond_to_broadcast_message(
            message,
            "Waku chưa sẵn sàng để phát thông báo. Vui lòng thử lại sau.",
            title="Không thể phát thông báo",
            color=discord.Color.orange(),
        )
        return
    if target == "all":
        guilds = list(client.guilds)
    else:
        guild = client.get_guild(int(target))
        if guild is None:
            await _respond_to_broadcast_message(
                message,
                f"Không tìm thấy server có ID `{target}` mà Waku đang tham gia.",
                title="Không tìm thấy server",
                color=discord.Color.orange(),
            )
            return
        guilds = [guild]
    if not guilds:
        await _respond_to_broadcast_message(
            message,
            "Waku chưa tham gia server nào để phát thông báo.",
            title="Không có server đích",
            color=discord.Color.orange(),
        )
        return

    announcement = discord.Embed(
        title="📢 Thông báo từ Waku",
        description=text,
        color=discord.Color.blurple(),
        timestamp=datetime.now(UTC),
    )
    direct_count = fallback_count = failed_count = 0
    for guild in guilds:
        try:
            # The calling DM can never be a preferred guild destination.
            channel, used_fallback = await _broadcast_channel(guild)
            if channel is None:
                failed_count += 1
                logger.warning(
                    f"Discord broadcast skipped guild={guild.id}: no writable channel"
                )
                continue
            await channel.send(
                embed=announcement, allowed_mentions=discord.AllowedMentions.none()
            )
        except (discord.HTTPException, discord.ClientException) as error:
            failed_count += 1
            logger.warning(
                f"Discord broadcast send failed guild={guild.id}: {type(error).__name__}"
            )
            continue
        if used_fallback:
            fallback_count += 1
        else:
            direct_count += 1

    total_sent = direct_count + fallback_count
    result = discord_command_embed(
        "Phát sóng hoàn tất."
        if total_sent
        else "Không gửi được thông báo đến server nào.",
        title="Kết quả phát thông báo",
        color=discord.Color.green() if total_sent else discord.Color.red(),
    )
    result.add_field(name="Đã gửi", value=f"`{total_sent}` server", inline=True)
    result.add_field(
        name="Kênh fallback", value=f"`{fallback_count}` server", inline=True
    )
    result.add_field(name="Thất bại", value=f"`{failed_count}` server", inline=True)
    result.set_footer(
        text="Đích: toàn bộ server" if target == "all" else f"Đích: server {target}"
    )
    await message.channel.send(
        embed=result, allowed_mentions=discord.AllowedMentions.none()
    )
    logger.info(
        f"Discord broadcast completed: user={message.author.id} target={target!r} "
        f"sent={total_sent} fallback={fallback_count} failed={failed_count}"
    )
