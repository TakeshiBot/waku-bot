"""Bot controls persisted in the database, separate from deployment settings."""

FEATURE_FIELDS = (
    "telegram_enabled",
    "discord_enabled",
    "business_chat_enabled",
    "dm_sticker_download_enabled",
)
PRIVATE_FEATURE_FIELDS = frozenset({"business_chat_prompt_enabled"})
APP_ONLY_FIELDS = frozenset({"agent_quota_free_daily_tokens"})
RUNTIME_FIELDS = frozenset(FEATURE_FIELDS) | PRIVATE_FEATURE_FIELDS | APP_ONLY_FIELDS
# Sticker learning is controlled by each group, with no global switch.
RETIRED_RUNTIME_FIELDS = frozenset({"agent_sticker_memory"})
