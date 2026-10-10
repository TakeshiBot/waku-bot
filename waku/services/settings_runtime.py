"""Prepare live settings changes without importing or mutating bot runtimes.

Only already-loaded application modules are touched by apply(). Model objects
and their HTTP pools are constructed offline before the settings file commits.
Startup-owned resources and vector-index identity changes require a restart.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent

from waku.config import _AppConfig, app_config

SETTINGS_APPLICATION_LOCK = asyncio.Lock()

_MODEL_FIELDS = {
    "agent_providers",
    "agent_proxy",
    "agent_model",
    "agent_model_small",
    "agent_model_multimodal",
    "agent_struct_model",
    "agent_model_options",
    "agent_model_small_options",
    "agent_model_multimodal_options",
    "agent_struct_model_options",
    "agent_memory_prompt",
    "agent_sticker_description_model",
    "agent_sticker_description_prompt",
    "agent_image_gen_model",
    "agent_image_edit_model",
}
_STARTUP_FIELDS = {
    "token",
    "api_id",
    "api_hash",
    "session_name",
    "use_ipv6",
    "workdir",
    "db_url",
    "jobstore_db_url",
    "pg_pgroonga",
    "automigrate",
    "debug",
    "log_level",
    "log_retention_days",
    "agent",
    "webapp",
    "webapp_host",
    "webapp_port",
    "health_check_enabled",
    "health_check_host",
    "health_check_port",
    "webapp_allow_origins",
    "webapp_trusted_proxies",
    "webapp_menu_button",
    "webapp_url",
    "loop_monitor_enabled",
    "loop_monitor_interval",
    "loop_monitor_threshold",
    "loop_monitor_native_dump_timeout",
    "session_health_enabled",
    "session_health_interval",
    "session_health_timeout",
    "session_health_threshold",
    "session_health_cooldown",
    "session_health_restart_timeout",
    "session_health_stale",
    "session_crypto_timeout",
    "rss_enabled",
    "rss_interval",
    "avatar_change_enabled",
    "avatar_change_interval",
    "avatar_refresh_concurrency",
    "redis",
    "redis_endpoint",
    "redis_port",
    "redis_db",
    "redis_password",
    "agent_sticker_memory",
    "agent_sticker_search_mode",
    "agent_sticker_embed_model",
    "agent_sticker_embed_dimensions",
    "agent_sticker_db_path",
    "agent_powermem_config",
    "agent_powermem_config_path",
    "agent_powermem_custom_fact_extraction_prompt",
    "agent_code_awareness",
    "agent_code_exclude_patterns",
    "agent_extra_tools",
    "agent_shell_concurrency",
    # These values are embedded in the main Agent's capability graph.
    "agent_secret_masking",
    "agent_tool_output_limit",
    "agent_tool_output_max_chars",
    "agent_tool_output_spill",
    "agent_context_window_tokens",
    "agent_clamp_max_part_ratio",
    "agent_usage_request_limit",
    "agent_usage_total_tokens_limit",
    "cachedir",
}


def _config_reads(node: ast.AST) -> set[str]:
    return {
        child.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute)
        and isinstance(child.value, ast.Name)
        and child.value.id == "app_config"
    }


@lru_cache(maxsize=1)
def _source_config_usage() -> tuple[set[str], set[str]]:
    """Catch default arguments, decorators and module/class-bound settings.

    The explicit set also covers startup functions; remaining references read
    settings at call time. Scanning source does not import disabled plugins.
    """
    bound: set[str] = set()
    used: set[str] = set()

    class Bindings(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            for value in [
                *node.decorator_list,
                *node.args.defaults,
                *(v for v in node.args.kw_defaults if v is not None),
            ]:
                bound.update(_config_reads(value))

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node):
            bound.update(_config_reads(node.value))

        def visit_AnnAssign(self, node):
            if node.value is not None:
                bound.update(_config_reads(node.value))

        def visit_If(self, node):
            bound.update(_config_reads(node.test))
            self.generic_visit(node)

    for path in Path(__file__).resolve().parents[1].rglob("*.py"):
        if path == Path(__file__).resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        used.update(_config_reads(tree))
        Bindings().visit(tree)
    return bound, used


@dataclass
class SettingsApplicationPlan:
    restart_fields: set[str]
    live_fields: set[str]
    _assignments: list[tuple[Any, str, Any]] = field(default_factory=list, repr=False)
    _clients: list[httpx.AsyncClient] = field(default_factory=list, repr=False)
    _config_values: dict[str, Any] = field(default_factory=dict, repr=False)
    _applied: bool = field(default=False, repr=False)
    _activated: bool = field(default=False, repr=False)
    discord_status: str | None = None

    def apply(self) -> None:
        """Commit staged bindings synchronously after the atomic file save."""
        if self._applied:
            return
        for target, name, value in self._assignments:
            setattr(target, name, value)
        for name, value in self._config_values.items():
            setattr(app_config, name, value)
        # Keep old pools alive for existing runs. Both old and new pools are
        # closed by the application's normal HTTP shutdown hook.
        http_module = sys.modules.get("waku.common.http")
        if http_module is not None:
            for client in self._clients:
                http_module._clients[f"settings-runtime:{id(client)}"] = client
        self._applied = True

    async def discard(self) -> None:
        """Release prospective pools if the file write is rejected/fails."""
        if not self._applied:
            for client in self._clients:
                await client.aclose()

    async def activate(self) -> None:
        """Activate saved bindings, then reconcile the optional Discord service."""
        self.apply()
        if self._activated:
            return
        self._activated = True
        if not self.live_fields & {"discord_enabled", "discord_token"}:
            return
        from waku import discordbot
        from waku.logger import logger

        try:
            if "discord_token" in self.live_fields or not app_config.discord_enabled:
                await discordbot.stop_discord_bot()
            if app_config.discord_enabled:
                await discordbot.start_discord_bot()
                self.discord_status = discordbot.get_discord_runtime_status()
                if self.discord_status in {"offline", "stopped", "error"}:
                    self.discord_status = "error"
            else:
                self.discord_status = "offline"
        except Exception as error:
            # The file was saved; explain the service failure instead of
            # claiming that persistence failed or rolling back the draft UI.
            self.discord_status = "error"
            logger.error(
                "Discord configuration activation failed: {}", type(error).__name__
            )


def _effective_roots(candidate: _AppConfig, changed_fields: set[str]) -> set[str]:
    def value(config, path):
        for part in path.split("."):
            config = (
                config.get(part)
                if isinstance(config, dict)
                else getattr(config, part, None)
            )
        return config

    return {
        name.split(".", 1)[0]
        for name in changed_fields
        if value(candidate, name) != value(app_config, name)
    }


def _embedding_provider_changed(candidate: _AppConfig) -> bool:
    if (
        not app_config.agent_sticker_memory
        or app_config.agent_sticker_search_mode != "embedding"
    ):
        return False
    spec = app_config.agent_sticker_embed_model
    name = spec.split("/", 1)[0] if "/" in spec else "default"
    return app_config.agent_providers.get(name) != candidate.agent_providers.get(name)


def settings_restart_fields(
    candidate: _AppConfig, changed_fields: set[str]
) -> set[str]:
    """Classify effective changes without constructing models/HTTP clients."""
    roots = _effective_roots(candidate, changed_fields)
    bound, used = _source_config_usage()
    restart = roots & (_STARTUP_FIELDS | (bound - _MODEL_FIELDS - {"lang"}))
    restart.update(roots - used - _MODEL_FIELDS - {"lang"})
    # The same provider may serve chat and embeddings. Do not let a new
    # embedding identity query an index containing vectors from the old one.
    embedding_provider_changed = _embedding_provider_changed(candidate)
    if embedding_provider_changed and "agent_providers" in roots:
        restart.add("agent_providers")
    if (
        app_config.agent_sticker_memory
        and app_config.agent_sticker_search_mode == "embedding"
        and "agent_proxy" in roots
    ):
        restart.add("agent_proxy")
    main = sys.modules.get("waku.plugins.agent.agent")
    # If a provider update needs restart, retain every old provider-bound
    # object. Unrelated settings still apply; next restart adopts new models.
    if (
        candidate.agent != app_config.agent
        or (
            candidate.agent
            and (
                main is None
                or getattr(main, "agent", None) is None
                or not candidate.agent_model
            )
        )
        or restart & {"agent_providers", "agent_proxy", "agent"}
        or embedding_provider_changed
        or (
            app_config.agent_sticker_memory
            and app_config.agent_sticker_search_mode == "embedding"
            and candidate.agent_proxy != app_config.agent_proxy
        )
    ):
        restart.update(roots & _MODEL_FIELDS)
    return restart


async def prepare_settings_application(
    candidate: _AppConfig, changed_fields: set[str]
) -> SettingsApplicationPlan:
    roots = _effective_roots(candidate, changed_fields)
    restart = await asyncio.to_thread(
        settings_restart_fields, candidate, changed_fields
    )
    plan = SettingsApplicationPlan(restart, roots - restart)
    if (
        plan.live_fields & {"discord_enabled", "discord_token"}
        and candidate.discord_enabled
        and not (
            candidate.discord_token and app_config.agent and app_config.agent_model
        )
    ):
        raise ValueError("Discord requires an AI model and a token")
    snapshot = app_config.model_copy(deep=True)
    proposed = candidate.model_copy(deep=True)
    for name in plan.live_fields:
        setattr(snapshot, name, getattr(proposed, name))
    plan._config_values = {name: getattr(snapshot, name) for name in plan.live_fields}
    candidate = snapshot
    main = sys.modules.get("waku.plugins.agent.agent")
    if not candidate.agent or not (plan.live_fields & _MODEL_FIELDS):
        return plan
    from waku.plugins.agent import provider

    pools: dict[str | None, httpx.AsyncClient] = {}
    models: dict[str, Any] = {}

    def pool(spec: str) -> httpx.AsyncClient:
        cfg, _ = provider.resolve_spec(spec, providers=candidate.agent_providers)
        proxy = (cfg.proxy or candidate.agent_proxy or "").strip() or None
        if proxy not in pools:
            client = httpx.AsyncClient(
                proxy=proxy, timeout=httpx.Timeout(60, connect=10)
            )
            pools[proxy] = client
            plan._clients.append(client)
        return pools[proxy]

    def model(spec: str):
        if spec not in models:
            models[spec] = provider.make_chat_model(
                spec, providers=candidate.agent_providers, http_client=pool(spec)
            )
        return models[spec]

    def assign(target, name, value):
        plan._assignments.append((target, name, value))

    def replace_aliases(old, new):
        if old is None:
            return
        for name, module in list(sys.modules.items()):
            if name.startswith("waku.") and module is not None:
                for attribute, value in list(vars(module).items()):
                    if value is old:
                        assign(module, attribute, new)

    try:
        main_model = model(candidate.agent_model)
        small = model(candidate.agent_model_small or candidate.agent_model)
        multimodal = model(candidate.agent_model_multimodal or candidate.agent_model)
        structured = model(candidate.agent_struct_model or candidate.agent_model)
        # Explicit bindings preserve the fallback aliases when several roles
        # previously referenced the same main model.
        for name, value in [
            ("model", main_model),
            ("small_model", small),
            ("multimodal_model", multimodal),
            ("struct_model", structured),
        ]:
            assign(main, name, value)
            old = getattr(main, name)
            for module_name, module in list(sys.modules.items()):
                if (
                    module_name.startswith("waku.")
                    and module is not None
                    and old is not None
                    and vars(module).get(name) is old
                ):
                    assign(module, name, value)
        assign(main.agent, "model", main_model)
        assign(
            main.agent,
            "model_settings",
            provider.make_model_settings(candidate.agent_model_options),
        )
        followup = sys.modules.get("waku.plugins.agent.followup")
        if followup is not None:
            relevance = getattr(followup, "_default_relevance_check_agent", None)
            if relevance is not None:
                assign(relevance, "model", small)
                assign(
                    relevance,
                    "model_settings",
                    provider.make_model_settings(candidate.agent_model_small_options),
                )
        if main.memory_agent is not None:
            old = main.memory_agent
            memory = Agent(
                structured,
                output_type=old.output_type,
                instructions=candidate.agent_memory_prompt,
                model_settings=provider.make_model_settings(
                    candidate.agent_struct_model_options
                ),
                capabilities=[old.root_capability],
                retries=5,
            )
            replace_aliases(old, memory)
        comment_module = sys.modules.get("waku.plugins.agent.channel_comment")
        if comment_module is not None:
            old = comment_module.comment_agent
            options = dict(candidate.agent_struct_model_options or {})
            options.setdefault("thinking", False)
            comment = Agent(
                structured,
                output_type=old.output_type,
                model_settings=provider.make_model_settings(options),
                capabilities=[old.root_capability],
                retries=5,
            )
            assign(comment_module, "struct_model", structured)
            assign(comment_module, "comment_agent", comment)
        sticker = sys.modules.get("waku.plugins.agent.sticker_memory")
        if sticker is not None and sticker._description_agent is not None:
            assign(
                sticker._description_agent,
                "model",
                model(
                    candidate.agent_sticker_description_model
                    or candidate.agent_model_multimodal
                    or candidate.agent_model
                ),
            )
        if sticker is not None and getattr(sticker, "_selector_agent", None) is not None:
            assign(
                sticker._selector_agent,
                "model",
                model(
                    candidate.agent_model_small
                    or candidate.agent_sticker_description_model
                    or candidate.agent_model_multimodal
                    or candidate.agent_model
                ),
            )
            assign(
                sticker._selector_agent,
                "model_settings",
                provider.make_model_settings(candidate.agent_model_small_options),
            )
        discord = sys.modules.get("waku.discordbot.state")
        if discord is not None:
            for name in ("discord_agent", "discord_recovery_agent"):
                active = getattr(discord, name, None)
                if active is not None:
                    assign(active, "model", main_model)
        image = sys.modules.get("waku.services.image_gen")
        if image is not None:
            for name, klass, spec in [
                (
                    "image_gen_client",
                    image._ImageGenerationClient,
                    candidate.agent_image_gen_model,
                ),
                (
                    "image_edit_client",
                    image._ImageEditClient,
                    candidate.agent_image_edit_model or candidate.agent_image_gen_model
                    if candidate.agent_image_gen_model
                    else None,
                ),
            ]:
                obj = None
                if spec:
                    args = provider.make_openai_client_args(
                        spec, providers=candidate.agent_providers
                    )
                    obj = klass.__new__(klass)
                    obj.model = args.pop("model")
                    obj._client = AsyncOpenAI(**args, http_client=pool(spec))
                    cfg, _ = provider.resolve_spec(
                        spec, providers=candidate.agent_providers
                    )
                    obj._proxy = cfg.proxy or candidate.agent_proxy
                assign(image, name, obj)
        return plan
    except Exception:
        await plan.discard()
        # Never surface a provider URL, key, proxy credential or original SDK
        # exception text through the private config UI or logs.
        raise ValueError("Unable to prepare runtime model settings") from None
