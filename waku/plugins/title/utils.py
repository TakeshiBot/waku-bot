from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from waku import i18n

from .permissions import ADMIN_RIGHTS, CHANNEL_ONLY, supported


class TitlePermissionsMarkup:
    def __init__(self, permissions=None, lang="", token="", revision=0):
        self.permissions = permissions or {}
        self.lang, self.token = lang, token
        self.revision = revision

    def build(self):
        buttons = []
        for index, name in enumerate(ADMIN_RIGHTS):
            disabled = name in CHANNEL_ONLY or not supported(name)
            enabled = name == "can_manage_chat" or self.permissions.get(name) is True
            mark = "⚠" if disabled else "✅" if enabled else "❌"
            buttons.append(
                InlineKeyboardButton(
                    mark + " " + i18n.t(f"title_menu.rights.{name}", locale=self.lang),
                    callback_data=f"sett:{self.token}:{self.revision}:{index}",
                )
            )
        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        rows.append(
            [
                InlineKeyboardButton(
                    i18n.t("title_menu.save", locale=self.lang),
                    callback_data=f"sett:{self.token}:{self.revision}:save",
                )
            ]
        )
        return InlineKeyboardMarkup(rows)
