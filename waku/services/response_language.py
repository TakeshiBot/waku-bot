"""Response-language policy; automatic mode is independent of UI translations."""

from waku.i18n import normalize_locale


def normalize_response_language(value: str | None) -> str:
    value = normalize_locale(value.strip().lower()) if isinstance(value, str) and value.strip() else "auto"
    return value if value in {"auto", "vi", "en"} else "auto"


def response_ui_locale(value: str | None) -> str:
    return "en" if normalize_response_language(value) == "en" else "vi"


def response_language_instructions(value: str | None) -> str:
    language = normalize_response_language(value)
    if language == "auto":
        return (
            "Response language mode: AUTOMATIC. For each turn, you MUST reply in "
            "the language of the CURRENT user's latest natural-language message, "
            "including short greetings. They do not need to request a language "
            "change explicitly. Follow any explicit request for an output language. "
            "Support any language, not just Vietnamese or English; for mixed "
            "messages use the user's main conversational language. Technical "
            "terms, URLs, code, filenames, quoted text, image captions, tool "
            "results and other users' messages must not determine reply language. "
            "Re-evaluate each turn instead of continuing a previous language. "
            "For image/sticker/file-only messages, keep this user's most recent "
            "conversational language in this channel; never reset after vision. "
            "Only if there is no linguistic input or user-language context may "
            "you use Vietnamese. This response-language policy takes priority "
            "over a persona's default language."
        )
    name = "Vietnamese (tiếng Việt)" if language == "vi" else "English (tiếng Anh)"
    return f"Default response language: {name}. Reply in {name} unless the user explicitly requests another language."
