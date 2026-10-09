"""Presentation locale for API error responses."""

from dataclasses import dataclass
from typing import Any

from waku.i18n import i18n, normalize_locale


def request_locale(request) -> str:
    # An authenticated user's saved preference takes priority over the browser.
    stored = getattr(request.state, "locale", None)
    if stored:
        return normalize_locale(stored)
    candidates = []
    for index, item in enumerate(request.headers.get("accept-language", "").split(",")):
        tag, *parameters = item.strip().split(";")
        quality = 1.0
        for parameter in parameters:
            name, separator, value = parameter.strip().partition("=")
            if name.lower() == "q" and separator:
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if 0 < quality <= 1:
            candidates.append((-quality, index, normalize_locale(tag)))
    for _, _, locale in sorted(candidates):
        if locale in i18n.available_locales:
            return locale
    return i18n.default_locale


def error_message(code: str, locale: str = "") -> str:
    key = f"bot.hardcoded.api.{code}"
    translated = i18n.t(key, locale=locale)
    if translated == key:
        return i18n.t("bot.hardcoded.api.INTERNAL_ERROR", locale=locale)
    return translated


@dataclass(frozen=True)
class LocalizedMessage:
    """Keep precise error meaning until the response locale is known."""

    key: str
    values: dict[str, Any]

    def render(self, locale: str = "") -> str:
        return i18n.t(self.key, locale=locale).format(**self.values)

    def __str__(self) -> str:
        # Exception logs and Pydantic diagnostics stay readable before rendering.
        return self.render("en")


def api_text(key: str, **values: Any) -> LocalizedMessage:
    return LocalizedMessage(f"bot.hardcoded.api_reasons.{key}", values)


def validation_text(key: str, **values: Any) -> LocalizedMessage:
    return LocalizedMessage(f"bot.hardcoded.api_validation.{key}", values)


class LocalizedValidationError(ValueError):
    """Retain Pydantic's value_error type while attaching a translatable reason."""

    def __init__(self, reason: LocalizedMessage):
        super().__init__(str(reason))
        self.localized_reason = reason


def known_reason(message: str | LocalizedMessage) -> LocalizedMessage | None:
    if isinstance(message, LocalizedMessage):
        return message
    # Domain services share exceptions with Telegram and use stable English text.
    # Only known application reasons are exposed; library internals use code fallback.
    reasons = i18n.get_raw("bot.hardcoded.api_reasons", locale="en") or {}
    for key, template in reasons.items():
        if template == message:
            return api_text(key)
    return None


def validation_message(error: dict[str, Any], locale: str = "") -> str:
    ctx = error.get("ctx") or {}
    reason = getattr(ctx.get("error"), "localized_reason", None)
    if isinstance(reason, LocalizedMessage):
        return reason.render(locale)
    kind = error.get("type", "")
    aliases = {
        "missing": "required",
        "int_type": "integer",
        "int_parsing": "integer",
        "int_from_float": "integer",
        "float_type": "number",
        "float_parsing": "number",
        "finite_number": "number",
        "bool_type": "boolean",
        "bool_parsing": "boolean",
        "string_type": "string",
        "string_unicode": "string",
        "list_type": "list",
        "dict_type": "object",
        "model_type": "object",
        "json_invalid": "json",
        "json_type": "json",
        "date_type": "date",
        "date_parsing": "date",
        "date_from_datetime_parsing": "date",
        "datetime_type": "date",
        "datetime_parsing": "date",
        "datetime_from_date_parsing": "date",
        "url_type": "url",
        "url_parsing": "url",
    }
    key = aliases.get(kind, kind)
    parameter_keys = {
        "greater_than": ("gt",),
        "greater_than_equal": ("ge",),
        "less_than": ("lt",),
        "less_than_equal": ("le",),
        "multiple_of": ("multiple_of",),
        "string_too_short": ("min_length",),
        "string_too_long": ("max_length",),
        "too_short": ("min_length",),
        "too_long": ("max_length",),
        "literal_error": ("expected",),
        "url_scheme": ("expected_schemes",),
    }
    full_key = f"bot.hardcoded.api_validation.{key}"
    if i18n.t(full_key, locale=locale) == full_key:
        return i18n.t("bot.hardcoded.api.invalid_field", locale=locale)
    # Only schema-provided bounds/allowed values are interpolated. Neither input,
    # exception objects nor arbitrary parsing-error context is reflected.
    names = parameter_keys.get(key, ())
    if any(name not in ctx for name in names):
        return i18n.t("bot.hardcoded.api.invalid_field", locale=locale)
    return validation_text(key, **{name: ctx[name] for name in names}).render(locale)
