"""Locale-scoped agent prose; protocol names and user data remain untouched."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction, signature

from kmua.i18n import i18n

_locale: ContextVar[str | None] = ContextVar("agent_locale", default=None)


def current_locale() -> str:
    return _locale.get() or i18n.default_locale


def tr(key: str, *, locale: str | None = None, **values) -> str:
    text = i18n.t(f"bot.agent_i18n.{key}", locale=locale or current_locale())
    return text.format(**values) if values else text


@contextmanager
def locale_scope(locale: str | None):
    """Task-local scope, reset even if a model call fails or is cancelled."""
    token = _locale.set(locale or current_locale())
    try:
        yield
    finally:
        _locale.reset(token)


def configured_prompt(field: str, locale: str | None = None) -> str:
    """Translate built-in defaults while preserving an operator's custom prompt."""
    from kmua.config import app_config

    configured = getattr(app_config, field)
    key = f"bot.prompts.{field}"
    defaults = {i18n.t(key, lang) for lang in i18n.available_locales}
    if configured in defaults:
        return i18n.t(key, locale=locale or current_locale())
    return configured


def localized_argument(parameter: str):
    """Scope pure prompt builders and model runners to their locale argument."""

    def decorate(function):
        sig = signature(function)

        def locale_for(args, kwargs):
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            value = bound.arguments.get(parameter)
            if isinstance(value, str):
                return value
            deps = getattr(value, "deps", value)
            return getattr(deps, "locale", None) or current_locale()

        if iscoroutinefunction(function):

            @wraps(function)
            async def async_wrapped(*args, **kwargs):
                with locale_scope(locale_for(args, kwargs)):
                    return await function(*args, **kwargs)

            return async_wrapped

        @wraps(function)
        def wrapped(*args, **kwargs):
            with locale_scope(locale_for(args, kwargs)):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def localized_message(function=None, *, prefer_chat: bool = False):
    """Resolve Telegram handler locale once; nested prompts inherit this scope."""

    def decorate(function):
        sig = signature(function)

        @wraps(function)
        async def wrapped(*args, **kwargs):
            from kmua import database

            bound = sig.bind_partial(*args, **kwargs)
            event = bound.arguments.get("message") or bound.arguments.get(
                "callback_query"
            )
            user = getattr(event, "from_user", None)
            message = getattr(event, "message", None) or event
            chat = getattr(message, "chat", None)
            locale = current_locale()
            if prefer_chat and chat is not None and chat.id is not None and chat.id < 0:
                locale = (await database.get_chat_config(chat.id)).lang
            elif user is not None and getattr(user, "id", None) is not None:
                locale = (await database.get_user_config(user.id)).lang
            elif chat is not None and getattr(chat, "id", None) is not None:
                locale = (await database.get_chat_config(chat.id)).lang
            with locale_scope(locale):
                return await function(*args, **kwargs)

        return wrapped

    return decorate(function) if function is not None else decorate
