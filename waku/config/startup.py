"""Keep optional AI setup from preventing access to Telegram's admin menu.

Only the in-memory configuration is adjusted. The TOML file retains incomplete
values so the owner can finish configuring the bot through /config.
"""

import json
from urllib.parse import urlsplit


def prepare_optional_config(config) -> list[str]:
    """Disable unavailable resources, returning field names safe to log."""
    unavailable = []

    def usable(spec: str | None) -> bool:
        if not spec or not spec.strip():
            return False
        name, separator, model = spec.partition("/")
        if not separator:
            name, model = "default", spec
        provider = config.agent_providers.get(name.strip())
        if (
            not model.strip()
            or provider is None
            or provider.type not in {"chat_completions", "responses"}
            or provider.api_type not in {"openai", "ollama"}
            or (provider.api_type != "ollama" and not provider.key.strip())
        ):
            return False
        try:
            url = urlsplit(provider.url)
            if url.scheme not in {"http", "https"} or not url.hostname:
                return False
            _ = url.port
            proxy = provider.proxy or config.agent_proxy
            if proxy:
                parsed = urlsplit(proxy)
                if (
                    parsed.scheme not in {"http", "https", "socks5", "socks5h"}
                    or not parsed.hostname
                ):
                    return False
                _ = parsed.port
        except ValueError:
            return False
        return True

    if config.agent and not usable(config.agent_chat_model):
        config.agent = False
        unavailable.append("agent_chat_model")
    if config.agent:
        for field in (
            "agent_vision_model",
            "agent_model_small",
            "agent_struct_model",
            "agent_image_gen_model",
            "agent_image_edit_model",
        ):
            if getattr(config, field) and not usable(getattr(config, field)):
                setattr(config, field, None)
                unavailable.append(field)
        if config.agent_sticker_search_mode == "embedding" and not usable(
            config.agent_sticker_embed_model
        ):
            config.agent_sticker_search_mode = "chat"
            unavailable.append("agent_sticker_embed_model")

    if not config.agent:
        config.agent_powermem_config = None
    if config.agent and config.agent_powermem_config_path:
        try:
            with open(config.agent_powermem_config_path, encoding="utf-8") as stream:
                memory = json.load(stream)
            if not isinstance(memory, dict):
                raise ValueError("invalid_memory_config")
            if config.agent_powermem_custom_fact_extraction_prompt is not None:
                memory["custom_fact_extraction_prompt"] = (
                    config.agent_powermem_custom_fact_extraction_prompt
                )
            config.agent_powermem_config = memory
        except (OSError, ValueError):
            config.agent_powermem_config = None
            unavailable.append("agent_powermem_config_path")
    return unavailable


def telegram_config_errors(config) -> list[str]:
    """Validate the four credentials needed to connect and administer Telegram."""
    errors = []
    bot_id, separator, secret = config.token.strip().partition(":")
    if (
        not separator
        or not bot_id.isdecimal()
        or int(bot_id) <= 0
        or not secret.strip()
    ):
        errors.append("token")
    if not config.owners or any(owner <= 0 for owner in config.owners):
        errors.append("owners")
    if config.api_id <= 0:
        errors.append("api_id")
    api_hash = config.api_hash.strip()
    if len(api_hash) != 32 or any(
        char not in "0123456789abcdefABCDEF" for char in api_hash
    ):
        errors.append("api_hash")
    return errors
