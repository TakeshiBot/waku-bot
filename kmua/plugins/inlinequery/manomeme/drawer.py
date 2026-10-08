"""
This module was copied from https://github.com/zhaomaoniu/nonebot-plugin-manosaba-memes and modified.

Original License:

MIT License

Copyright (c) 2025 Gitai

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import math
from enum import StrEnum

from sketchbook import (
    Drawer,
    DrawerRegion,
    PasteStyle,
    TextFitDrawer,
    TextStyle,
)

from kmua.consts import MANOMEME_PATH
from kmua.i18n import i18n, normalize_locale, t


class Character(StrEnum):
    """Characters available for trail drawing"""

    EMA = "Ema"
    HIRO = "Hiro"

    @property
    def display(self) -> str:
        """Get the display string for the character"""
        return self.get_display()

    def get_display(self, locale: str = "") -> str:
        return t(f"bot.hardcoded.meme.character.{self.value.lower()}", locale=locale)


class Statement(StrEnum):
    """Types of statements for the trail drawing"""

    AGREEMENT = "Agreement"
    DOUBT = "Doubt"
    PURJURY = "Perjury"
    REFUTATION = "Refutation"
    MAGIC = "Magic"

    @property
    def display(self) -> str:
        """Get the display string for the statement"""
        return self.get_display()

    def get_display(self, locale: str = "") -> str:
        return t(f"bot.hardcoded.meme.statement.{self.value.lower()}", locale=locale)


class Option:
    """A trial option for a character to say"""

    def __init__(self, statement: Statement, text: str) -> None:
        """Initialize a trail option

        Args:
            statement (Statement): The type of statement this option represents
            text (str): The text content of the option
        """
        self.statement = statement
        self.text = text


# Legacy Chinese command aliases are part of the input protocol.
STATEMENT_ALIASES = {
    "赞同": Statement.AGREEMENT,
    "疑问": Statement.DOUBT,
    "伪证": Statement.PURJURY,
    "反驳": Statement.REFUTATION,
    "魔法": Statement.MAGIC,
    **{statement.value.lower(): statement for statement in Statement},
}


def get_statement(statement: str) -> Statement:
    return STATEMENT_ALIASES[statement.lower()]


def get_character(character: str) -> Character:
    mapping = {
        "艾玛": Character.EMA,
        "希罗": Character.HIRO,
        "ema": Character.EMA,
        "hiro": Character.HIRO,
    }
    return mapping.get(character.lower(), Character.EMA)


def anan_base_image(face: str | None = None) -> str:
    """Get the base image path for Anan's face

    Args:
        face (Optional[str], optional): The face type to be used. Available: 害羞, 生气, 病娇, 无语, 开心. Defaults to None.

    Returns:
        str: The path to the base image
    """
    if face is None:
        return str(MANOMEME_PATH / "anan/base.png")
    else:
        return str(MANOMEME_PATH / f"anan/{face}.png")


def draw_anan(text: str, face: str | None = None, locale: str = "") -> bytes:
    """Draw the image of what Anan says

    Args:
        text (str): The text to be drawn
        face (Optional[str], optional): The face type to be used. Available: 害羞, 生气, 病娇, 无语, 开心. Defaults to None.

    Returns:
        bytes: The image bytes of the drawn image
    """
    drawer = TextFitDrawer(
        base_image=anan_base_image(face),
        font=str(
            MANOMEME_PATH
            / "fonts"
            / (
                "AaMingTianHuiYouHaoShiFaSheng-2.ttf"
                if normalize_locale(locale or i18n.default_locale).startswith("zh")
                else "SourceHanSerifSC.otf"
            )
        ),
        overlay_image=str(MANOMEME_PATH / "anan/base_overlay.png"),
        region=DrawerRegion(100, 432, 100 + 319, 432 + 204),
    )
    image_bytes = drawer.draw(
        text=text,
        style=TextStyle(color=(0, 0, 0, 255)),
    )
    return image_bytes


def get_statement_image(statement: Statement) -> str:
    """Get the image path for a statement type

    Args:
        statement (Statement): The statement type

    Returns:
        str: The path to the statement image
    """
    mapping = {
        Statement.AGREEMENT: "agreement.png",
        Statement.DOUBT: "doubt.png",
        Statement.PURJURY: "perjury.png",
        Statement.REFUTATION: "refutation.png",
        Statement.MAGIC: "magic.png",
    }
    return str(MANOMEME_PATH / f"trial/{mapping[statement]}")


def get_option_coordinates(number: int) -> list[tuple[int, int]]:
    """Get the coordinates for drawing options based on the number of options

    Args:
        number (int): The number of options

    Returns:
        List[Tuple[int, int]]: A list of (x, y) coordinates for each option
    """
    if number % 2 == 1:
        padding = min(
            286,
            (1080 - 364 - 216) // math.floor(number / 2)
            if math.floor(number / 2) != 0
            else 286,
            (-364 + 47) // math.ceil(-number / 2)
            if math.ceil(-number / 2) != 0
            else 286,
        )
        return [
            (29, 364 + padding * i)
            for i in range(math.ceil(-number / 2), math.floor(number / 2) + 1)
        ]
    else:
        padding = min(
            286.0,
            (1080 - 364 - 216) // (math.floor(number / 2) - 0.5),
            (-364 + 47) // (math.ceil(-number / 2) + 0.5),
        )
        return [
            (29, int(364 + padding * (i + 0.5)))
            for i in range(math.ceil(-number / 2), math.floor(number / 2))
        ]


def draw_trial(character: Character, options: list[Option], locale: str = ""):
    """Draw the trial image for a character saying an option

    Args:
        character (Character): The character who is speaking
        options (List[Option]): The options being spoken

    Returns:
        bytes: The image bytes of the drawn image
    """
    # Bakcground and character
    drawer = Drawer(
        base_image=str(MANOMEME_PATH / "trial/black.png"),
        font=str(MANOMEME_PATH / "fonts/SourceHanSerifSC.otf"),
    )
    drawer = drawer.paste_image(
        str(MANOMEME_PATH / "trial/background.png"),
        region=DrawerRegion(0, 0, 1260, 1080),
        style=PasteStyle(keep_alpha=False),
    ).paste_image(
        str(
            MANOMEME_PATH
            / "trial"
            / ("ema.png" if character == Character.EMA else "hiro.png")
        ),
        region=DrawerRegion(667, 0, 1260, 1080),
        style=PasteStyle(keep_alpha=False),
    )

    # Options, texts, and statements
    coordinates = get_option_coordinates(len(options))
    for option, (x, y) in zip(options, coordinates):
        drawer = drawer.paste_image(
            str(MANOMEME_PATH / "trial/option.png"),
            region=DrawerRegion(x, y, x + 802, y + 216),
            style=PasteStyle(keep_alpha=False),
        ).draw_text(
            text=option.text,
            region=DrawerRegion(x + 109, y + 32, x + 109 + 589, y + 32 + 150),
            style=TextStyle(
                color=(39, 33, 30, 255),
                bracket_color=(39, 33, 30, 255),
                max_font_height=48,
            ),
        )
        region = DrawerRegion(x + 21, y - 41, x + 21 + 146, y - 41 + 126)
        if normalize_locale(locale or i18n.default_locale).startswith("zh"):
            drawer = drawer.paste_image(
                get_statement_image(option.statement),
                region=region,
                style=PasteStyle(keep_alpha=False),
            )
        else:
            # The original statement badges contain baked-in Chinese lettering.
            drawer = drawer.draw_text(
                text=option.statement.get_display(locale),
                region=DrawerRegion(x + 21, y - 41, x + 241, y + 2),
                style=TextStyle(color=(255, 246, 235, 255), max_font_height=28),
            )

    return drawer.finish()
