from pathlib import Path

import orjson

from waku.i18n import normalize_locale
from waku.logger import logger

_word_dict_cache: dict[str, dict[str, list[str]]] = {}


def _load_words(locale: str) -> dict[str, list[str]]:
    """Load the safe word-reply dictionary for one locale."""
    internal_path = Path(__file__).parent / "word_dicts"
    normalized_locale = normalize_locale(locale)
    locale_path = internal_path / f"{normalized_locale}.json"
    language_path = internal_path / f"{normalized_locale.split('-', 1)[0]}.json"
    if locale_path.is_file():
        files = [locale_path]
    elif language_path.is_file():
        files = [language_path]
    else:
        files = []

    words: dict[str, list[str]] = {}
    logger.debug(f"loading word dict for locale {normalized_locale} from {files}")
    for file in files:
        try:
            with file.open(encoding="utf-8") as f:
                for keyword, replies in orjson.loads(f.read()).items():
                    if keyword in words:
                        words[keyword].extend(replies)
                    else:
                        words[keyword] = replies
        except Exception as e:
            logger.error(
                f"loading word dict failed: {file}: {e.__class__.__name__}: {e}"
            )
            continue
    return words


def get_word_dict(locale: str = "vi") -> dict[str, list[str]]:
    """Return a lazily loaded, locale-specific word-reply dictionary."""
    normalized_locale = normalize_locale(locale)
    if normalized_locale not in _word_dict_cache:
        _word_dict_cache[normalized_locale] = _load_words(normalized_locale)
    return _word_dict_cache[normalized_locale]
