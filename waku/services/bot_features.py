"""Database-backed global bot switches and app-only quota settings."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from waku import database
from waku.config import ProviderConfig, _AppConfig, _resolve_settings_files, app_config
from waku.logger import logger
from waku.runtime_fields import (
    APP_ONLY_FIELDS,
    FEATURE_FIELDS,
    PRIVATE_FEATURE_FIELDS,
    RUNTIME_FIELDS,
)

FEATURES = FEATURE_FIELDS


def _validate_values(values: dict[str, Any]) -> dict[str, bool | int]:
    unknown = set(values) - RUNTIME_FIELDS
    if unknown:
        raise ValueError(f"Unsupported runtime setting(s): {sorted(unknown)!r}")

    for key, value in values.items():
        if key in (*FEATURE_FIELDS, *PRIVATE_FEATURE_FIELDS) and not isinstance(value, bool):
            raise ValueError(f"{key} must be a boolean")
        if key in APP_ONLY_FIELDS and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"{key} must be a non-negative integer")

    # Validate against the canonical config model as a batch so constraints and
    # future field type changes cannot be bypassed by a direct DB write.
    payload = app_config.model_dump()
    payload.update(values)
    candidate = _AppConfig.model_validate(payload)
    return {key: getattr(candidate, key) for key in values}


async def get_runtime_values() -> dict[str, bool | int]:
    stored = await database.read_bot_runtime_settings(RUNTIME_FIELDS)
    return _validate_values(stored)


async def save_values(
    values: dict[str, Any],
    *,
    expected_values: dict[str, Any] | None = None,
) -> dict[str, bool | int]:
    """Validate and atomically persist a runtime batch without applying it.

    Feature menu callers hold ``SETTINGS_APPLICATION_LOCK`` while preparing
    their live activation plan, calling this function and activating the plan.
    Keeping the DB write free of that lock avoids lock recursion.
    """
    validated = _validate_values(values)
    expected = (
        None if expected_values is None else _validate_values(expected_values)
    )
    saved = await database.save_bot_runtime_settings(
        validated, expected_values=expected
    )
    if not saved:
        raise ValueError("runtime_conflict")
    return validated


async def initialize_runtime_values() -> dict[str, bool | int]:
    """Seed legacy config defaults once, then apply the DB values at startup."""
    defaults = {key: getattr(app_config, key) for key in RUNTIME_FIELDS}
    stored = await database.read_bot_runtime_settings(RUNTIME_FIELDS)
    missing = {key: value for key, value in defaults.items() if key not in stored}
    if missing:
        await database.save_bot_runtime_settings(_validate_values(missing))
    effective = _validate_values({**defaults, **stored})
    for key, value in effective.items():
        setattr(app_config, key, value)
    # Import lazily: the editor imports runtime-field metadata and is also used
    # by the Telegram config plugin. Startup must apply DB values before plugin
    # discovery, while cleanup failure must never prevent the bot from running.
    try:
        from waku.services.settings_editor import SettingsEditor

        paths = [Path(path) for path in _resolve_settings_files()]
        editor = SettingsEditor(
            paths,
            _AppConfig,
            ProviderConfig,
            lambda: app_config.model_dump(),
        )
        await asyncio.to_thread(editor.migrate_model_fields)
        await asyncio.to_thread(editor.remove_runtime_fields)
    except Exception as error:
        logger.warning(
            "Could not migrate legacy settings in TOML "
            f"({error.__class__.__name__})"
        )
    return effective


__all__ = ["FEATURES", "get_runtime_values", "initialize_runtime_values", "save_values"]
