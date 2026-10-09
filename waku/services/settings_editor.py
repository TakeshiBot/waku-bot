"""Validated, comment-preserving edits to the current base's TOML settings.

This module has no bot/config initialization side effects. Callers provide the
current schemas and settings paths; every write re-reads and validates the file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, get_args

import pydantic
import tomlkit

_WRITE_LOCK = threading.RLock()
_PROVIDER_NAME = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")


class SettingsEditError(ValueError):
    """A public error code, deliberately never containing configuration values."""


def is_secret(key: str) -> bool:
    return (
        any(
            marker in key.lower()
            for marker in (
                "token",
                "secret",
                "password",
                "api_hash",
                "api_key",
                "db_url",
                "proxy",
            )
        )
        or key.rsplit(".", 1)[-1] == "key"
    )


def display_value(key: str, value: Any, *, limit: int = 700) -> str:
    if is_secret(key):
        return "••••••" if value not in (None, "", [], {}) else "—"
    text = json.dumps(value, ensure_ascii=False, default=str)
    # Endpoints sometimes contain credentials even though the field is named URL.
    if "url" in key and isinstance(value, str):
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(value)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                return "••••••"
        except ValueError:
            return "••••••"
    return text if len(text) <= limit else text[:limit] + "…"


def parse_value(text: str, annotation: Any) -> Any:
    """Strings/prompts are literal; other types accept JSON or a TOML value."""
    adapter = pydantic.TypeAdapter(annotation)
    if text.strip().lower() in {"null", "none"}:
        try:
            return adapter.validate_python(None, strict=True)
        except pydantic.ValidationError:
            raise SettingsEditError("invalid_value") from None
    # Test string acceptance strictly, so a numeric field never consumes raw text.
    args = get_args(annotation)
    if annotation is str or (
        str in args and all(arg in (str, type(None)) for arg in args)
    ):
        return text
    if text.strip().lower() in {"null", "none"}:
        value = None
    else:
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            try:
                value = tomllib.loads("value = " + text)["value"]
            except (ValueError, KeyError):
                # Path and similar typed scalar settings accept plain text too.
                value = text
    try:
        return adapter.validate_python(value)
    except (pydantic.ValidationError, ValueError, TypeError):
        raise SettingsEditError("invalid_value") from None


class SettingsEditor:
    def __init__(
        self,
        paths: list[Path],
        schema: type[pydantic.BaseModel],
        provider_schema: type[pydantic.BaseModel],
        runtime: Callable[[], Mapping[str, Any]],
    ):
        if not paths:
            raise SettingsEditError("missing_file")
        # The last settings file is the override layer Dynaconf reads last.
        self.paths = paths
        self.path = paths[-1]
        self.schema = schema
        self.provider_schema = provider_schema
        self.runtime = runtime

    def _read(self):
        try:
            raw = self.path.read_bytes()
            document = tomlkit.parse(raw.decode("utf-8-sig"))
            digest = hashlib.sha256()
            for path in self.paths:
                layer = raw if path == self.path else path.read_bytes()
                digest.update(len(layer).to_bytes(8, "big"))
                digest.update(layer)
        except (OSError, ValueError):
            raise SettingsEditError("invalid_file") from None
        return document, digest.hexdigest()

    def _defaults(self) -> dict[str, Any]:
        runtime = self.runtime()
        # Only required fields may need an environment-only value. Optional
        # fields come from schema defaults/files, never a stale running model.
        return self.schema.model_validate(
            {
                name: runtime[name]
                for name, field in self.schema.model_fields.items()
                if field.is_required() and name in runtime
            }
        ).model_dump()

    def snapshot(self) -> tuple[dict[str, Any], str]:
        _, revision = self._read()
        values = self._defaults()
        try:
            for path in self.paths:
                values.update(
                    {
                        key.lower(): value
                        for key, value in tomllib.loads(
                            path.read_text(encoding="utf-8-sig")
                        ).items()
                        if key.lower() in self.schema.model_fields
                    }
                )
        except (OSError, ValueError):
            raise SettingsEditError("invalid_file") from None
        return values, revision

    def _key(self, document, name: str) -> str:
        return next((key for key in document if key.lower() == name), name)

    @staticmethod
    def _toml_providers(providers: Mapping[str, Mapping[str, Any]]) -> dict:
        # Schema defaults contain optional fields such as proxy=None. TOML
        # represents those defaults by omitting the field, never a null value.
        return {
            name: {key: value for key, value in fields.items() if value is not None}
            for name, fields in providers.items()
        }

    def _validate(self, document) -> None:
        values = self._defaults()
        try:
            for path in self.paths[:-1]:
                values.update(
                    {
                        k.lower(): v
                        for k, v in tomllib.loads(
                            path.read_text(encoding="utf-8-sig")
                        ).items()
                    }
                )
            values.update({k.lower(): v for k, v in document.unwrap().items()})
            candidate = self.schema.model_validate(values)
            providers = getattr(candidate, "agent_providers", {})
            for provider in providers.values():
                if provider.type not in {"chat_completions", "responses"}:
                    raise SettingsEditError("invalid_value")
                if provider.api_type not in {"openai", "ollama"}:
                    raise SettingsEditError("invalid_value")
            # Named model specs must retain their provider when it is removed.
            for key, value in values.items():
                if (
                    key.startswith("agent_")
                    and "model" in key
                    and isinstance(value, str)
                ):
                    if value:
                        if "/" in value:
                            name, _, model = value.partition("/")
                            if not model.strip():
                                raise SettingsEditError("invalid_value")
                            name = name.strip()
                        else:
                            name = "default"
                        if name not in providers:
                            raise SettingsEditError("provider_in_use")
        except (pydantic.ValidationError, TypeError, ValueError) as exc:
            if isinstance(exc, SettingsEditError):
                raise
            raise SettingsEditError("invalid_value") from None

    def _write(self, revision: str, change: Callable) -> None:
        with _WRITE_LOCK:
            document, current_revision = self._read()
            if current_revision != revision:
                raise SettingsEditError("stale_file")
            change(document)
            self._validate(document)
            payload = tomlkit.dumps(document).encode("utf-8")
            # Parse independently before replacing a working config file.
            tomllib.loads(payload.decode("utf-8"))
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.path.parent, prefix=".settings-editor-", delete=False
                ) as stream:
                    temp_path = Path(stream.name)
                    os.chmod(temp_path, self.path.stat().st_mode & 0o777)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if self._read()[1] != revision:
                    raise SettingsEditError("stale_file")
                os.replace(temp_path, self.path)
            except OSError:
                raise SettingsEditError("write_failed") from None
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

    def validate_for_restart(self) -> None:
        document, _ = self._read()
        self._validate(document)

    def set_value(self, key: str, text: str, revision: str) -> None:
        if key.startswith("agent_providers."):
            name, field = key.removeprefix("agent_providers.").rsplit(".", 1)
            if field not in self.provider_schema.model_fields:
                raise SettingsEditError("invalid_value")
            value = parse_value(
                text, self.provider_schema.model_fields[field].annotation
            )

            def change(document):
                providers = document.get(self._key(document, "agent_providers"), {})
                if name not in providers:
                    # It may be inherited from the lower settings layer.
                    values, _ = self.snapshot()
                    inherited = values.get("agent_providers", {}).get(name)
                    if inherited is None:
                        raise SettingsEditError("invalid_value")
                    if not providers:
                        document[self._key(document, "agent_providers")] = (
                            self._toml_providers(values.get("agent_providers", {}))
                        )
                        providers = document[self._key(document, "agent_providers")]
                    providers[name] = self._toml_providers({name: inherited})[name]
                if value is None:
                    providers[name].pop(field, None)
                else:
                    providers[name][field] = value

        else:
            field = self.schema.model_fields.get(key)
            if field is None or key in {"agent_providers", "agent_powermem_config"}:
                raise SettingsEditError("invalid_value")
            value = parse_value(text, field.annotation)

            def change(document):
                name = self._key(document, key)
                if value is None:
                    # TOML has no null. Only allow clearing a nullable-default field.
                    if field.default is not None:
                        raise SettingsEditError("invalid_value")
                    document.pop(name, None)
                    lower = self.snapshot()[0].get(key)
                    if lower is not None and any(
                        key
                        in {
                            k.lower()
                            for k in tomllib.loads(p.read_text(encoding="utf-8-sig"))
                        }
                        for p in self.paths[:-1]
                    ):
                        raise SettingsEditError("inherited_value")
                else:
                    if isinstance(value, Path):
                        value_to_write = str(value)
                    else:
                        value_to_write = value
                    document[name] = value_to_write

        self._write(revision, change)

    def add_provider(self, name: str, revision: str) -> None:
        if not _PROVIDER_NAME.fullmatch(name):
            raise SettingsEditError("invalid_name")

        def change(document):
            values, _ = self.snapshot()
            if name in values.get("agent_providers", {}):
                raise SettingsEditError("provider_exists")
            key = self._key(document, "agent_providers")
            if key not in document:
                document[key] = self._toml_providers(values.get("agent_providers", {}))
            document[key][name] = self.provider_schema().model_dump(exclude_none=True)

        self._write(revision, change)

    def delete_provider(self, name: str, revision: str) -> None:
        def change(document):
            key = self._key(document, "agent_providers")
            if name not in document.get(key, {}):
                raise SettingsEditError("inherited_value")
            del document[key][name]

        self._write(revision, change)
