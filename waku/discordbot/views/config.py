"""Private, session-bound Discord slash command settings."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import discord

from waku.config import app_config
from waku.i18n import normalize_locale
from waku.logger import logger

from .. import state
from ..embeds import discord_command_embed
from ..models import DiscordGuildSettings
from ..permissions import _is_discord_user_bot_admin
from ..settings import (
    _discord_dm_settings,
    _discord_global_ai_enabled,
    _discord_guild_settings,
    _r18_mode_label,
    _rotate_discord_history_epoch,
    _set_discord_dm_settings,
    _set_discord_global_ai_enabled,
    _set_discord_guild_settings,
)


def _can_manage(interaction) -> bool:
    if interaction.guild is None:
        return _is_discord_user_bot_admin(interaction.user)
    permissions = getattr(interaction, "permissions", None)
    if permissions is None:
        permissions = getattr(interaction.user, "guild_permissions", None)
    return (
        interaction.guild.owner_id == interaction.user.id
        or bool(permissions and permissions.administrator)
        or _is_discord_user_bot_admin(interaction.user)
    )


async def _private_error(interaction, description: str) -> None:
    embed = discord_command_embed(description, title="Waku", color=discord.Color.red())
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


class DiscordConfigView(discord.ui.View):
    def __init__(
        self,
        interaction: discord.Interaction,
        settings: DiscordGuildSettings,
        global_ai_enabled: bool | None = None,
    ):
        super().__init__(timeout=900)
        self.origin = interaction
        self.user_id = interaction.user.id
        self.guild = interaction.guild
        self.guild_id = self.guild.id if self.guild else None
        self.pending_settings = replace(settings)
        self.saved_settings = replace(settings)
        self.pending_global_ai = (
            global_ai_enabled if _is_discord_user_bot_admin(interaction.user) else None
        )
        self.saved_global_ai = self.pending_global_ai
        self.section = "chat"
        self.notice = ""
        self._lock = asyncio.Lock()
        self._sync_controls()

    def _t(self, vi: str, en: str) -> str:
        return vi if normalize_locale(self.pending_settings.lang) == "vi" else en

    def _global_access(self, user) -> bool:
        return self.pending_global_ai is not None and _is_discord_user_bot_admin(user)

    def _dirty(self) -> bool:
        return (
            self.pending_settings != self.saved_settings
            or self.pending_global_ai != self.saved_global_ai
        )

    async def interaction_check(self, interaction) -> bool:
        if interaction.guild is None and not _is_discord_user_bot_admin(
            interaction.user
        ):
            return False
        guild_id = interaction.guild.id if interaction.guild else None
        if (
            self.is_finished()
            or interaction.user.id != self.user_id
            or guild_id != self.guild_id
        ):
            await _private_error(
                interaction,
                self._t(
                    "Menu đã hết hạn hoặc không thuộc về bạn. Mở lại `/config`.",
                    "This menu expired or belongs to another user. Open `/config` again.",
                ),
            )
            return False
        if not _can_manage(interaction):
            await _private_error(
                interaction,
                self._t(
                    "Bạn cần là chủ server, quản trị viên server hoặc quản trị viên bot.",
                    "Server owner, server administrator or bot administrator access is required.",
                ),
            )
            return False
        task = asyncio.current_task()
        if task is not None and task not in state.discord_message_tasks:
            state.discord_message_tasks.add(task)
            task.add_done_callback(state.discord_message_tasks.discard)
        return True

    def embed(self) -> discord.Embed:
        settings = self.pending_settings

        def on(value):
            return self._t("Bật", "On") if value else self._t("Tắt", "Off")

        scope = (
            discord.utils.escape_markdown(self.guild.name)[:150]
            if self.guild
            else self._t("Cá nhân trong DM", "Personal DM")
        )
        embed = discord_command_embed(
            self._t(
                "Bấm nút để thay đổi. Chỉ áp dụng khi bấm **Lưu**.",
                "Use the buttons to change settings. Changes apply only after **Save**.",
            )
            + (f"\n{self.notice}" if self.notice else ""),
            title=self._t("Cấu hình Waku", "Waku settings"),
        )
        embed.add_field(name=self._t("Phạm vi", "Scope"), value=scope, inline=False)
        chat = self._t("Trả lời AI: ", "AI replies: ") + f"**{on(settings.ai_reply)}**"
        if self.guild:
            chat += (
                "\n"
                + self._t("Trả lời bot khác: ", "Replies to other bots: ")
                + f"**{on(settings.reply_to_bots)}**"
            )
        chat += "\n" + self._t(
            "Lệnh slash vẫn hoạt động khi tắt AI.",
            "Slash commands remain available when AI is off.",
        )
        embed.add_field(
            name=self._t("AI / trò chuyện", "AI / chat"), value=chat, inline=False
        )
        if self.guild:
            embed.add_field(
                name=self._t("Bộ nhớ kênh", "Channel memory"),
                value=f"**{on(settings.group_memory_enabled)}**\n"
                + self._t(
                    "Bộ nhớ riêng từng kênh. Lưu thay đổi sẽ làm mới lịch sử AI trong server.",
                    "Memory is separate per channel. Saving a change resets the server's AI history.",
                ),
                inline=False,
            )
        embed.add_field(
            name=self._t("Ảnh / R18", "Images / R18"),
            value=f"**{_r18_mode_label(settings.setu_enabled, settings.r18_mode)}**\n"
            + (
                self._t(
                    "Nút Ảnh bật/tắt riêng; nút R18 chuyển An toàn → R18 → Cả hai. Ảnh R18 gửi dạng spoiler.",
                    "Images can be toggled separately; R18 cycles Safe → R18 → Mixed. R18 images are sent as spoilers.",
                )
                if self.guild
                else self._t(
                    "DM chỉ hỗ trợ ảnh an toàn.", "DM supports safe images only."
                )
            ),
            inline=False,
        )
        embed.add_field(
            name=self._t("Ngôn ngữ", "Language"),
            value="Tiếng Việt"
            if normalize_locale(settings.lang) == "vi"
            else "English",
            inline=False,
        )
        if self._global_access(self.origin.user):
            embed.add_field(
                name=self._t("AI Discord toàn bot", "Global Discord AI"),
                value=f"**{on(self.pending_global_ai)}**\n"
                + self._t(
                    "Áp dụng cho mọi server và DM, có hiệu lực sau khi Lưu. Lệnh và menu vẫn hoạt động khi tắt.",
                    "Applies to all servers and DMs after Save. Commands and menus remain available when off.",
                ),
                inline=False,
            )
        embed.add_field(
            name=self._t("Trạng thái", "Status"),
            value=f"AI: **{on(bool(app_config.agent))}**\n"
            + self._t("Giới hạn kênh toàn bot: ", "Global channel restrictions: ")
            + (
                self._t("Đang bật", "Active")
                if app_config.discord_channel_allowlist
                else self._t("Không giới hạn", "Unrestricted")
            ),
            inline=False,
        )
        embed.set_footer(
            text=self._t(
                "Chỉ bạn thấy menu • Hết hạn sau 15 phút",
                "Only you can see this menu • Expires after 15 minutes",
            )
            + (
                self._t(" • Có thay đổi chưa lưu", " • Unsaved changes")
                if self._dirty()
                else ""
            )
        )
        return embed

    def _button(
        self,
        label,
        callback,
        *,
        style=discord.ButtonStyle.secondary,
        row=1,
        disabled=False,
    ):
        button = discord.ui.Button(label=label, style=style, row=row, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    def _sync_controls(self) -> None:
        self.clear_items()
        settings = self.pending_settings
        has_global = self._global_access(self.origin.user)
        if self.section == "global_ai" and not has_global:
            self.section = "chat"
        controls = [
            (
                self._t("Trả lời AI", "AI replies"),
                settings.ai_reply,
                self.toggle_ai_reply,
            ),
        ]
        if self.guild:
            controls.extend(
                [
                    (
                        self._t("Trả lời bot khác", "Replies to bots"),
                        settings.reply_to_bots,
                        self.toggle_reply_to_bots,
                    ),
                    (
                        self._t("Bộ nhớ kênh", "Channel memory"),
                        settings.group_memory_enabled,
                        self.toggle_group_memory,
                    ),
                ]
            )
        controls.append(
            (self._t("Ảnh", "Images"), settings.setu_enabled, self.toggle_images)
        )
        if self.guild:
            mode = (
                self._t("Tắt ảnh", "Images off")
                if not settings.setu_enabled
                else [
                    self._t("An toàn", "Safe"),
                    self._t("Chỉ R18", "R18 only"),
                    self._t("Cả hai", "Mixed"),
                ][settings.r18_mode]
            )
            controls.append(
                (
                    f"R18: {mode}",
                    settings.setu_enabled and settings.r18_mode > 0,
                    self.cycle_r18_mode,
                )
            )
        language = (
            "Tiếng Việt" if normalize_locale(settings.lang) == "vi" else "English"
        )
        controls.append(
            (self._t("Ngôn ngữ: ", "Language: ") + language, True, self.toggle_lang)
        )
        if has_global:
            controls.append(
                (
                    self._t("AI Discord toàn bot", "Global Discord AI"),
                    self.pending_global_ai,
                    self.toggle_global_ai,
                )
            )
        for index, (label, enabled, callback) in enumerate(controls):
            self._button(
                ("✅ " if enabled else "❌ ") + label,
                callback,
                style=discord.ButtonStyle.success
                if enabled
                else discord.ButtonStyle.secondary,
                row=index // 2,
                disabled=callback == self.cycle_r18_mode and not settings.setu_enabled,
            )
        dirty = self._dirty()
        for label, callback, style, disabled in [
            (
                self._t("Lưu", "Save"),
                self.save_config,
                discord.ButtonStyle.success,
                not dirty,
            ),
            (
                self._t("Huỷ thay đổi", "Cancel changes"),
                self.cancel_changes,
                discord.ButtonStyle.secondary,
                not dirty,
            ),
            (
                self._t("Tải lại", "Reload"),
                self.reload_settings,
                discord.ButtonStyle.primary,
                False,
            ),
            (
                self._t("Đóng", "Close"),
                self.close_menu,
                discord.ButtonStyle.danger,
                False,
            ),
        ]:
            self._button(label, callback, style=style, row=4, disabled=disabled)

    async def _refresh(self, interaction):
        if self.is_finished():
            return
        self._sync_controls()
        await interaction.edit_original_response(
            content=None, embed=self.embed(), view=self
        )

    async def _ack(self, interaction):
        if not await self.interaction_check(interaction):
            return False
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        return True

    async def _change(self, interaction, *, field=None, value=None, section=None):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            if section == "global_ai" and not self._global_access(interaction.user):
                await _private_error(
                    interaction,
                    self._t(
                        "Chỉ quản trị viên bot được cấu hình AI toàn Discord.",
                        "Only bot administrators can configure global Discord AI.",
                    ),
                )
                return
            if field:
                setattr(
                    self.pending_settings, field, value() if callable(value) else value
                )
            if section:
                self.section = section
            self.notice = ""
            await self._refresh(interaction)

    async def toggle_ai_reply(self, interaction):
        await self._change(
            interaction,
            field="ai_reply",
            value=lambda: not self.pending_settings.ai_reply,
        )

    async def toggle_reply_to_bots(self, interaction):
        if self.guild:
            await self._change(
                interaction,
                field="reply_to_bots",
                value=lambda: not self.pending_settings.reply_to_bots,
            )

    async def toggle_images(self, interaction):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            self.pending_settings.setu_enabled = not self.pending_settings.setu_enabled
            if self.guild is None:
                self.pending_settings.r18_mode = 0
            self.notice = ""
            await self._refresh(interaction)

    async def cycle_r18_mode(self, interaction):
        if not self.guild or not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            if not self.pending_settings.setu_enabled:
                return
            self.pending_settings.r18_mode = (self.pending_settings.r18_mode + 1) % 3
            self.notice = ""
            await self._refresh(interaction)

    async def toggle_global_ai(self, interaction):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            if not self._global_access(interaction.user):
                await _private_error(
                    interaction,
                    self._t(
                        "Chỉ quản trị viên bot được cấu hình AI toàn Discord.",
                        "Only bot administrators can configure global Discord AI.",
                    ),
                )
                return
            self.pending_global_ai = not self.pending_global_ai
            self.notice = ""
            await self._refresh(interaction)

    async def toggle_group_memory(self, interaction):
        if self.guild:
            await self._change(
                interaction,
                field="group_memory_enabled",
                value=lambda: not self.pending_settings.group_memory_enabled,
            )

    async def toggle_lang(self, interaction):
        await self._change(
            interaction,
            field="lang",
            value=lambda: (
                "en" if normalize_locale(self.pending_settings.lang) == "vi" else "vi"
            ),
        )

    async def toggle_r18(self, interaction):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            settings = self.pending_settings
            if self.guild:
                index = (settings.r18_mode + 1) if settings.setu_enabled else 0
                index = (index + 1) % 4
                settings.setu_enabled, settings.r18_mode = index != 0, max(0, index - 1)
            else:
                settings.setu_enabled = not settings.setu_enabled
                settings.r18_mode = 0
            self.notice = ""
            await self._refresh(interaction)

    async def save_config(self, interaction):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            snapshot = replace(self.pending_settings)
            global_snapshot = self.pending_global_ai
            local_changed = snapshot != self.saved_settings
            global_changed = global_snapshot != self.saved_global_ai
            if global_changed and not self._global_access(interaction.user):
                await _private_error(
                    interaction,
                    self._t(
                        "Quyền quản trị bot đã thay đổi. Tải lại menu trước khi Lưu.",
                        "Bot administrator access changed. Reload the menu before Save.",
                    ),
                )
                return
            memory_changed = (
                snapshot.group_memory_enabled
                != self.saved_settings.group_memory_enabled
            )
            self.notice = ""
            try:
                if local_changed:
                    if self.guild:
                        await _set_discord_guild_settings(self.guild, snapshot)
                    else:
                        await _set_discord_dm_settings(interaction.user, snapshot)
                    # Keep committed local and global drafts independent when
                    # one storage operation succeeds and the next one fails.
                    self.saved_settings = replace(snapshot)
                    if self.guild and memory_changed:
                        try:
                            await _rotate_discord_history_epoch(self.guild)
                        except Exception as error:
                            logger.warning(
                                f"Discord history reset failed: {type(error).__name__}"
                            )
                            self.notice = self._t(
                                "Không làm mới được lịch sử; hãy dùng `/forget`.",
                                "History reset failed; use `/forget`.",
                            )
                if global_changed:
                    if self.is_finished():
                        return
                    if not self._global_access(interaction.user):
                        raise PermissionError("Bot administrator access changed")
                    await _set_discord_global_ai_enabled(global_snapshot)
                    self.saved_global_ai = global_snapshot
            except Exception as error:
                logger.warning(f"Discord settings save failed: {type(error).__name__}")
                await _private_error(
                    interaction,
                    self._t(
                        "Không lưu được tất cả thay đổi. Mục chưa lưu vẫn còn trong menu; hãy thử lại hoặc tải lại.",
                        "Not all changes were saved. Unsaved changes remain in the menu; retry or reload.",
                    ),
                )
                await self._refresh(interaction)
                return
            self.notice = self._t("Đã lưu cấu hình.", "Settings saved.") + (
                " " + self.notice if self.notice else ""
            )
            await self._refresh(interaction)

    async def cancel_changes(self, interaction):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            self.pending_settings = replace(self.saved_settings)
            self.pending_global_ai = self.saved_global_ai
            self.notice = self._t(
                "Đã hủy thay đổi chưa lưu.", "Unsaved changes cancelled."
            )
            await self._refresh(interaction)

    async def reload_settings(self, interaction):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            settings = (
                await _discord_guild_settings(self.guild)
                if self.guild
                else await _discord_dm_settings(interaction.user)
            )
            global_ai = (
                await _discord_global_ai_enabled()
                if _is_discord_user_bot_admin(interaction.user)
                else None
            )
            self.saved_settings = replace(settings)
            self.pending_settings = replace(settings)
            self.saved_global_ai = self.pending_global_ai = global_ai
            self.notice = self._t(
                "Đã tải lại cấu hình hiện tại.", "Current settings reloaded."
            )
            await self._refresh(interaction)

    async def close_menu(self, interaction):
        if not await self._ack(interaction):
            return
        async with self._lock:
            if not await self.interaction_check(interaction):
                return
            await interaction.edit_original_response(
                embed=discord_command_embed(
                    self._t(
                        "Đã đóng menu. Thay đổi chưa lưu được bỏ qua.",
                        "Menu closed. Unsaved changes discarded.",
                    )
                ),
                view=None,
            )
            self.stop()

    async def on_timeout(self) -> None:
        self.stop()
        for child in self.children:
            child.disabled = True
        try:
            await self.origin.edit_original_response(view=self)
        except discord.HTTPException:
            pass

    async def on_error(self, interaction, error, item) -> None:
        logger.warning(f"Discord config menu failed: {type(error).__name__}")
        await _private_error(
            interaction,
            self._t(
                "Không thực hiện được. Hãy thử lại hoặc mở lại `/config`.",
                "Action failed. Try again or reopen `/config`.",
            ),
        )


async def open_discord_config(interaction: discord.Interaction) -> None:
    if not _can_manage(interaction):
        if interaction.guild is None:
            return
        await _private_error(
            interaction,
            "Bạn cần quyền quản trị server hoặc bot để mở `/config`. / Server or bot administrator access is required.",
        )
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        settings = (
            await _discord_guild_settings(interaction.guild)
            if interaction.guild
            else await _discord_dm_settings(interaction.user)
        )
        global_ai = (
            await _discord_global_ai_enabled()
            if _is_discord_user_bot_admin(interaction.user)
            else None
        )
        if not _can_manage(interaction):
            return
        view = DiscordConfigView(interaction, settings, global_ai_enabled=global_ai)
        await interaction.edit_original_response(embed=view.embed(), view=view)
    except Exception as error:
        logger.warning(f"Discord config open failed: {type(error).__name__}")
        await interaction.edit_original_response(
            embed=discord_command_embed(
                "Không tải được cấu hình. Hãy thử lại. / Settings unavailable. Try again.",
                color=discord.Color.red(),
            ),
            view=None,
        )
