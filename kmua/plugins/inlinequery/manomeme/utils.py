import re

from pyrogram import types

from kmua.i18n import t

from . import drawer as manodrawer

# Chinese values identify the existing image files and remain accepted input aliases.
FACE_FILES = {
    "yandere": "病娇",
    "angry": "生气",
    "shy": "害羞",
    "speechless": "无语",
    "happy": "开心",
}
anan_faces = list(FACE_FILES.values())


def resolve_face(face: str) -> str | None:
    return FACE_FILES.get(face.lower(), face if face in anan_faces else None)


def face_token(face: str) -> str:
    """Use neutral command tokens even when cached input used a legacy alias."""
    return next((key for key, value in FACE_FILES.items() if value == face), face)


def face_display(face: str, locale: str = "") -> str:
    key = face_token(face)
    return t(f"bot.hardcoded.meme.face.{key}", locale=locale)


def markup_anan_tips(locale: str = "") -> types.InlineKeyboardMarkup:
    buttons = [
        types.InlineKeyboardButton(
            text=face_display(face, locale),
            switch_inline_query_current_chat=f"ms anan {key} ",
        )
        for key, face in FACE_FILES.items()
    ]
    return types.InlineKeyboardMarkup([buttons[:2], buttons[2:]])


def markup_trial_tips(
    character: manodrawer.Character, locale: str = ""
) -> types.InlineKeyboardMarkup:
    statements = list(manodrawer.Statement)
    if character == manodrawer.Character.HIRO:
        statements = [
            manodrawer.Statement.PURJURY,
            manodrawer.Statement.REFUTATION,
            manodrawer.Statement.AGREEMENT,
            manodrawer.Statement.DOUBT,
            manodrawer.Statement.MAGIC,
        ]
    buttons = [
        types.InlineKeyboardButton(
            text=statement.get_display(locale),
            switch_inline_query_current_chat=f"ms trial {character.value} {statement.value.lower()} ",
        )
        for statement in statements
    ]
    return types.InlineKeyboardMarkup([buttons[:2], buttons[2:]])


def result_anan_tips(locale: str = "") -> types.InlineQueryResultArticle:
    return types.InlineQueryResultArticle(
        title=t("bot.hardcoded.meme.anan", locale=locale),
        input_message_content=types.InputTextMessageContent(
            message_text=t("bot.hardcoded.meme.anan_prompt", locale=locale)
        ),
        description=t("bot.hardcoded.meme.anan_usage", locale=locale),
        thumb_url="https://kmua.unv.app/assets/manosaba/anan_example.webp",
        reply_markup=markup_anan_tips(locale),
    )


def result_trial_tips(
    character: manodrawer.Character, locale: str = ""
) -> types.InlineQueryResultAnimation:
    name = "hiro" if character == manodrawer.Character.HIRO else "ema"
    return types.InlineQueryResultAnimation(
        title=t(f"bot.hardcoded.meme.{name}_title", locale=locale),
        description=t(f"bot.hardcoded.meme.{name}_description", locale=locale),
        animation_url=f"https://kmua.unv.app/assets/manosaba/trial_{name}.mp4",
        thumb_url=f"https://kmua.unv.app/assets/manosaba/trial_{name}_static.jpg",
        caption=t(f"bot.hardcoded.meme.{name}_caption", locale=locale),
        reply_markup=markup_trial_tips(character, locale),
    )


def parse_options(text: str) -> list[manodrawer.Option]:
    """Parse statement/text pairs, accepting legacy and neutral command tokens."""
    words = [re.escape(word) for word in manodrawer.STATEMENT_ALIASES]
    plain_words = [
        rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])"
        if word.isascii()
        else re.escape(word)
        for word in manodrawer.STATEMENT_ALIASES
    ]
    # Unicode brackets and ASCII brackets are accepted for either alias set.
    stmt_pattern = (
        r"(?:[\[\u3010]("
        + "|".join(words)
        + r")[\]\u3011]|("
        + "|".join(plain_words)
        + r"))"
    )
    quoted_ranges = [
        (match.start(), match.end()) for match in re.finditer(r'"[^"\n]*"', text)
    ]
    statements = [
        match
        for match in re.finditer(stmt_pattern, text, re.IGNORECASE)
        if not any(start <= match.start() < end for start, end in quoted_ranges)
    ]
    options = []
    for index, match in enumerate(statements):
        statement = manodrawer.get_statement(match.group(1) or match.group(2))
        end = (
            statements[index + 1].start() if index + 1 < len(statements) else len(text)
        )
        segment = text[match.end() : end].strip()
        if not segment:
            continue
        quoted = re.match(r'"([^"\n]*)"', segment)
        content = quoted.group(1) if quoted else segment
        options.append(manodrawer.Option(statement, content))
    return options
