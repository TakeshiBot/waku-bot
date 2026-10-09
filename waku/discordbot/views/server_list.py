"""Private DM inventory opened by the bot administrator's !server command."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import discord

from waku.i18n import normalize_locale
from waku.logger import logger

from .. import state
from ..embeds import discord_command_embed
from ..permissions import _is_discord_user_bot_admin
from ..settings import _discord_dm_settings


async def _error(interaction, text):
    embed = discord_command_embed(text, color=discord.Color.red())
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


def _join_order(guild):
    joined_at = getattr(getattr(guild, "me", None), "joined_at", None)
    if not isinstance(joined_at, datetime):
        return True, timedelta(0), guild.id
    if joined_at.tzinfo is None:
        joined_at = joined_at.replace(tzinfo=UTC)
    joined_at = joined_at.astimezone(UTC)
    return False, -(joined_at - datetime(1970, 1, 1, tzinfo=UTC)), guild.id


async def _joined_server_rows(client):
    return sorted(list(client.guilds), key=_join_order)


def _server_pages(rows, lang="vi") -> list[discord.Embed]:
    vi = normalize_locale(lang) == "vi"
    header = "**# · Server ID · Thành viên**" if vi else "**# · Server ID · Members**"
    lines = []
    page_lines = []
    for number, guild in enumerate(rows, 1):
        members = guild.member_count if guild.member_count is not None else "—"
        line = f"{number}. `{guild.id}` · {members}"
        if (
            len(page_lines) >= 50
            or len(header) + 1 + len("\n".join([*page_lines, line])) > 4096
        ):
            lines.append(page_lines)
            page_lines = []
        page_lines.append(line)
    if page_lines:
        lines.append(page_lines)
    if not lines:
        lines = [[]]
    pages = []
    for index, page in enumerate(lines):
        description = (
            header + "\n" + "\n".join(page)
            if page
            else (
                "Bot hiện chưa tham gia server nào."
                if vi
                else "The bot has not joined any servers."
            )
        )
        embed = discord_command_embed(
            description,
            title="Server Discord của Waku" if vi else "Waku Discord servers",
        )
        embed.set_footer(
            text=(
                f"Trang {index + 1}/{len(lines)} • {len(rows)} server • Mới tham gia trước • Chỉ bạn thấy menu"
                if vi
                else f"Page {index + 1}/{len(lines)} • {len(rows)} servers • Newest joined first • Only you can see this menu"
            )
        )
        pages.append(embed)
    return pages


class DiscordServerListView(discord.ui.View):
    def __init__(self, message: discord.Message, user_id: int, rows, lang="vi"):
        super().__init__(timeout=900)
        self.message = message
        self.user_id = user_id
        self.lang = lang
        self.pages = _server_pages(rows, lang)
        self.page = 0
        self._lock = asyncio.Lock()
        self._sync()

    def _t(self, vi, en):
        return vi if normalize_locale(self.lang) == "vi" else en

    async def interaction_check(self, interaction):
        if (
            self.is_finished()
            or interaction.user.id != self.user_id
            or interaction.guild is not None
            or interaction.message is None
            or interaction.message.id != self.message.id
            or interaction.message.channel.id != self.message.channel.id
            or not _is_discord_user_bot_admin(interaction.user)
        ):
            await _error(
                interaction,
                self._t(
                    "Menu đã hết hạn hoặc bạn không có quyền dùng menu này. Nhắn `!server` cho bot để mở lại.",
                    "This menu expired or you cannot access it. DM the bot `!server` to reopen it.",
                ),
            )
            return False
        task = asyncio.current_task()
        if task is not None and task not in state.discord_message_tasks:
            state.discord_message_tasks.add(task)
            task.add_done_callback(state.discord_message_tasks.discard)
        return True

    def _sync(self):
        self.previous_page.label = self._t("Trước", "Previous")
        self.next_page.label = self._t("Sau", "Next")
        self.reload_servers.label = self._t("Tải lại", "Reload")
        self.close_menu.label = self._t("Đóng", "Close")
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= len(self.pages) - 1

    async def _ack(self, interaction):
        if not await self.interaction_check(interaction):
            return False
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        return True

    async def _navigate(self, interaction, delta):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            self.page = max(0, min(self.page + delta, len(self.pages) - 1))
            self._sync()
            await self.message.edit(embed=self.pages[self.page], view=self)

    @discord.ui.button(label="Trước", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction, button):
        await self._navigate(interaction, -1)

    @discord.ui.button(label="Sau", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction, button):
        await self._navigate(interaction, 1)

    @discord.ui.button(label="Tải lại", style=discord.ButtonStyle.primary)
    async def reload_servers(self, interaction, button):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            rows = await _joined_server_rows(interaction.client)
            if self.is_finished():
                return
            if not await self.interaction_check(interaction):
                return
            self.pages = _server_pages(rows, self.lang)
            self.page = min(self.page, len(self.pages) - 1)
            self._sync()
            await self.message.edit(embed=self.pages[self.page], view=self)

    @discord.ui.button(label="Đóng", style=discord.ButtonStyle.danger)
    async def close_menu(self, interaction, button):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            await self.message.edit(
                embed=discord_command_embed(
                    self._t("Đã đóng danh sách server.", "Server list closed.")
                ),
                view=None,
            )
            self.stop()

    async def on_timeout(self):
        self.stop()
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass

    async def on_error(self, interaction, error, item):
        logger.warning(f"Discord server menu failed: {type(error).__name__}")
        await _error(
            interaction,
            self._t(
                "Không cập nhật được danh sách. Hãy thử lại.",
                "Server list update failed. Try again.",
            ),
        )


async def open_discord_server_list_message(message: discord.Message) -> None:
    if (
        message.guild is not None
        or not isinstance(message.channel, discord.DMChannel)
        or message.author.bot
        or not _is_discord_user_bot_admin(message.author)
    ):
        return
    menu_message = await message.channel.send(
        embed=discord_command_embed("Đang tải danh sách server… / Loading server list…")
    )
    try:
        settings = await _discord_dm_settings(message.author)
        if state.discord_client is None:
            raise RuntimeError("Discord client unavailable")
        rows = await _joined_server_rows(state.discord_client)
        if not _is_discord_user_bot_admin(message.author):
            await menu_message.edit(
                embed=discord_command_embed(
                    "Quyền quản trị bot đã bị thu hồi. / Bot administrator access was revoked."
                ),
                view=None,
            )
            return
        view = DiscordServerListView(
            menu_message, message.author.id, rows, settings.lang
        )
        await menu_message.edit(embed=view.pages[0], view=view)
    except Exception as error:
        logger.warning(f"Discord server list open failed: {type(error).__name__}")
        await menu_message.edit(
            embed=discord_command_embed(
                "Không tải được danh sách server. Hãy thử lại. / Server list unavailable. Try again.",
                color=discord.Color.red(),
            ),
            view=None,
        )
