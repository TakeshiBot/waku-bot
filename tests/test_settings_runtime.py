"""Offline preparation/atomic activation of provider-bound runtime objects."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import waku.config as config
from waku.config import ProviderConfig, _AppConfig
from waku.plugins.agent import provider
from waku.services import settings_runtime as runtime


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,token_changed,expected",
    [
        (False, False, ["stop"]),
        (True, False, ["start"]),
        (True, True, ["stop", "start"]),
        (False, True, ["stop"]),
    ],
)
async def test_discord_saved_changes_apply_without_restart(
    staged, monkeypatch, enabled, token_changed, expected
):
    from waku import discordbot

    staged.current.discord_enabled = not enabled
    staged.current.discord_token = "offline-old"
    staged.candidate.discord_enabled = enabled
    staged.candidate.discord_token = "offline-new" if token_changed else "offline-old"
    changes = {"discord_enabled"} | ({"discord_token"} if token_changed else set())
    calls = []

    async def lifecycle(action):
        assert staged.current.discord_enabled is enabled
        assert staged.current.discord_token == staged.candidate.discord_token
        calls.append(action)

    async def stop():
        await lifecycle("stop")

    async def start():
        await lifecycle("start")

    monkeypatch.setattr(discordbot, "stop_discord_bot", AsyncMock(side_effect=stop))
    monkeypatch.setattr(discordbot, "start_discord_bot", AsyncMock(side_effect=start))
    monkeypatch.setattr(
        discordbot, "get_discord_runtime_status", Mock(return_value="connecting")
    )
    plan = await runtime.prepare_settings_application(staged.candidate, changes)
    assert plan.live_fields == changes and not plan.restart_fields
    assert not calls  # No side effects before settings are committed.
    await plan.activate()
    await plan.activate()
    assert calls == expected
    assert plan.discord_status == ("connecting" if enabled else "offline")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "offline"])
async def test_discord_activation_failure_keeps_saved_settings_and_reports_failure(
    staged, monkeypatch, failure
):
    from waku import discordbot

    staged.current.discord_enabled = False
    staged.candidate.discord_enabled = True
    staged.candidate.discord_token = "offline"
    monkeypatch.setattr(
        discordbot,
        "start_discord_bot",
        AsyncMock(
            side_effect=RuntimeError("credential") if failure == "exception" else None
        ),
    )
    monkeypatch.setattr(discordbot, "stop_discord_bot", AsyncMock())
    monkeypatch.setattr(discordbot, "get_discord_runtime_status", lambda: "offline")
    plan = await runtime.prepare_settings_application(
        staged.candidate, {"discord_enabled", "discord_token"}
    )
    await plan.activate()
    assert staged.current.discord_enabled
    assert plan.discord_status == "error"


@pytest.mark.asyncio
async def test_discord_enable_without_token_is_rejected_before_commit(staged):
    staged.current.discord_enabled = False
    staged.candidate.discord_enabled = True
    staged.candidate.discord_token = ""
    with pytest.raises(ValueError, match="requires an AI model and a token"):
        await runtime.prepare_settings_application(
            staged.candidate, {"discord_enabled"}
        )
    assert not staged.current.discord_enabled


class MemoryResult(BaseModel):
    summary: str


@pytest.mark.asyncio
async def test_reload_uses_same_hot_model_plan_and_retains_startup_resources(
    staged, monkeypatch
):
    monkeypatch.setattr(config, "app_config", staged.current)
    staged.candidate.token = "next-start-only"
    monkeypatch.setattr(config, "_read_config_candidate", lambda: staged.candidate)

    success, message, changed = await config.reload_config(locale="vi")

    assert success and "khởi động lại" in message
    assert "agent_model" in changed and "token" not in changed
    assert staged.current.token == "offline"
    assert staged.current.agent_model == "default/new"
    assert staged.main.agent.model.model_name == "default/new"
    assert staged.current.agent_providers["default"].key == "prospective"
    for client in staged.http_module._clients.values():
        await client.aclose()


@pytest.mark.asyncio
async def test_reload_prepare_failure_keeps_running_config_and_masks_secrets(
    staged, monkeypatch
):
    monkeypatch.setattr(config, "app_config", staged.current)
    monkeypatch.setattr(config, "_read_config_candidate", lambda: staged.candidate)

    def fail(*args, **kwargs):
        raise RuntimeError("secret-token-key-url")

    monkeypatch.setattr(provider, "make_chat_model", fail)
    success, message, changed = await config.reload_config(locale="vi")

    assert not success and not changed
    assert "secret-token-key-url" not in message
    assert staged.current.agent_model == "default/old"
    assert staged.main.agent.model.model_name == "old"


@pytest.fixture
def staged(monkeypatch):
    current = _AppConfig(
        token="offline",
        owners=[1],
        agent=True,
        agent_model="default/old",
        agent_image_gen_model="default/image-old",
        agent_providers={
            "default": ProviderConfig(url="https://old.invalid/v1", key="old")
        },
    )
    monkeypatch.setattr(runtime, "app_config", current)
    old_model = TestModel(model_name="old")
    main = ModuleType("waku.plugins.agent.agent")
    main.agent = Agent(old_model, instructions="Keep main instructions", tools=[])
    main.model = main.small_model = main.multimodal_model = main.struct_model = (
        old_model
    )
    main.memory_agent = Agent(
        old_model, output_type=MemoryResult, instructions="Old memory"
    )
    memory = ModuleType("waku.plugins.agent.memory")
    memory.memory_agent = main.memory_agent
    comment = ModuleType("waku.plugins.agent.channel_comment")
    comment.struct_model = old_model
    comment.comment_agent = Agent(old_model, output_type=MemoryResult)
    sticker = ModuleType("waku.plugins.agent.sticker_memory")
    sticker._description_agent = Agent(old_model)
    followup = ModuleType("waku.plugins.agent.followup")
    followup.model = followup.small_model = followup.multimodal_model = old_model
    followup._default_relevance_check_agent = Agent(old_model, output_type=MemoryResult)
    discord = ModuleType("waku.discordbot.state")
    discord.discord_agent = Agent(old_model, instructions="Keep Discord instructions")
    discord.discord_recovery_agent = Agent(old_model)
    image = ModuleType("waku.services.image_gen")
    image._ImageGenerationClient = type("Generation", (), {})
    image._ImageEditClient = type("Edit", (), {})
    image.image_gen_client = object()
    image.image_edit_client = object()
    http_module = ModuleType("waku.common.http")
    http_module._clients = {}
    for module in (
        main,
        memory,
        comment,
        sticker,
        followup,
        discord,
        image,
        http_module,
    ):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    calls = []

    def make(spec, *, providers, http_client):
        assert providers is not current.agent_providers
        assert isinstance(http_client, httpx.AsyncClient)
        calls.append((spec, providers, http_client))
        return TestModel(model_name=spec)

    monkeypatch.setattr(provider, "make_chat_model", make)
    candidate = current.model_copy(deep=True)
    candidate.agent_model = "default/new"
    candidate.agent_model_small = "default/small-new"
    candidate.agent_model_multimodal = "default/mm-new"
    candidate.agent_struct_model = "default/struct-new"
    candidate.agent_providers["default"].key = "prospective"
    candidate.agent_memory_prompt = "New memory instructions"
    candidate.agent_model_options = {"temperature": 0.7}
    candidate.agent_struct_model_options = {"max_tokens": 40}
    candidate.agent_image_gen_model = "default/image-new"
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_prepare_offline_then_activate_all_static_bindings(staged):
    before = (
        staged.main.agent,
        staged.main.memory_agent,
        staged.comment.comment_agent,
        staged.image.image_gen_client,
        staged.discord.discord_agent,
    )
    plan = await runtime.prepare_settings_application(
        staged.candidate,
        {
            "agent_model",
            "agent_providers.default.key",
            "agent_model_small",
            "agent_model_multimodal",
            "agent_struct_model",
            "agent_memory_prompt",
            "agent_model_options",
            "agent_struct_model_options",
            "agent_image_gen_model",
        },
    )
    assert not plan.restart_fields
    assert "agent_model" in plan.live_fields and "agent_providers" in plan.live_fields
    assert staged.current.agent_model == "default/old"
    assert staged.main.agent.model.model_name == "old"
    assert staged.main.memory_agent is before[1]
    assert staged.comment.comment_agent is before[2]
    assert staged.image.image_gen_client is before[3]
    assert not staged.http_module._clients
    plan.apply()
    plan.apply()  # Idempotent, no duplicate allocation or registration.
    assert staged.main.agent is before[0]
    assert staged.current.agent_model == "default/new"
    assert staged.current.agent_providers["default"].key == "prospective"
    assert staged.discord.discord_agent is before[4]
    assert staged.main.agent.model.model_name == "default/new"
    assert staged.main.agent.model_settings == {"temperature": 0.7}
    assert staged.main.small_model.model_name == "default/small-new"
    assert staged.main.multimodal_model.model_name == "default/mm-new"
    assert staged.main.struct_model.model_name == "default/struct-new"
    assert staged.memory.memory_agent is staged.main.memory_agent
    assert staged.main.memory_agent is not before[1]
    assert staged.main.memory_agent.output_type is MemoryResult
    result = await staged.main.memory_agent.run("Extract offline memory")
    assert isinstance(result.output, MemoryResult)
    assert staged.comment.struct_model is staged.main.struct_model
    assert staged.comment.comment_agent.model_settings == {
        "max_tokens": 40,
        "thinking": False,
    }
    assert staged.sticker._description_agent.model is staged.main.model
    assert staged.followup.model is staged.main.model
    assert staged.followup.small_model is staged.main.small_model
    assert staged.followup.multimodal_model is staged.main.multimodal_model
    assert (
        staged.followup._default_relevance_check_agent.model is staged.main.small_model
    )
    assert staged.discord.discord_agent.model is staged.main.model
    assert staged.image.image_gen_client.model == "image-new"
    assert staged.image.image_edit_client.model == "image-new"
    assert (
        str(staged.image.image_gen_client._client.base_url) == "https://old.invalid/v1/"
    )
    assert staged.image.image_gen_client._client.api_key == "prospective"
    assert len(staged.http_module._clients) == len(plan._clients) == 1
    assert all(not client.is_closed for client in plan._clients)
    for client in plan._clients:
        await client.aclose()


@pytest.mark.asyncio
async def test_file_save_failure_discards_pools_without_activating(staged):
    plan = await runtime.prepare_settings_application(staged.candidate, {"agent_model"})
    await plan.discard()
    assert all(client.is_closed for client in plan._clients)
    assert staged.main.model.model_name == "old"
    assert not staged.http_module._clients


@pytest.mark.asyncio
async def test_prepare_failure_sanitized_and_closes_created_pools(staged, monkeypatch):
    clients = []
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        result = real_client(*args, **kwargs)
        clients.append(result)
        return result

    def fail(*args, **kwargs):
        raise RuntimeError("prospective secret credential URL")

    monkeypatch.setattr(runtime.httpx, "AsyncClient", client)
    monkeypatch.setattr(provider, "make_chat_model", fail)
    with pytest.raises(ValueError) as caught:
        await runtime.prepare_settings_application(staged.candidate, {"agent_model"})
    assert str(caught.value) == "Unable to prepare runtime model settings"
    assert clients and all(obj.is_closed for obj in clients)
    assert staged.main.model.model_name == "old"


@pytest.mark.asyncio
async def test_embedding_provider_restart_only_if_active_provider_affected(staged):
    staged.current.agent_sticker_memory = True
    plan = await runtime.prepare_settings_application(
        staged.candidate, {"agent_providers.default.key", "agent_model"}
    )
    assert plan.restart_fields == {"agent_providers", "agent_model"}
    assert not plan._clients
    assert not staged.calls
    staged.candidate.agent_providers["default"] = staged.current.agent_providers[
        "default"
    ].model_copy()
    staged.candidate.agent_providers["unrelated"] = ProviderConfig(key="new")
    plan = await runtime.prepare_settings_application(
        staged.candidate, {"agent_providers"}
    )
    assert not plan.restart_fields
    await plan.discard()


@pytest.mark.asyncio
async def test_restart_resources_vs_dynamic_turn_settings(staged):
    names = {
        "token",
        "rss_interval",
        "discord_token",
        "agent_secret_masking",
        "agent_sticker_embed_dimensions",
        "agent_tool_output_spill",
        "agent_prompt",
        "business_chat_enabled",
        "discord_keywords",
        "agent_model_timeout",
        "agent_multimodal_inputs",
        "agent_streaming",
    }
    staged.candidate.token = "changed"
    staged.candidate.rss_interval += 1
    staged.candidate.discord_token = "changed"
    staged.candidate.agent_secret_masking = False
    staged.candidate.agent_sticker_embed_dimensions += 1
    staged.candidate.agent_tool_output_spill = False
    staged.candidate.agent_prompt = "New prompt"
    staged.candidate.business_chat_enabled = True
    staged.candidate.discord_keywords = ["new"]
    staged.candidate.agent_model_timeout += 1
    staged.candidate.agent_multimodal_inputs = ["photo", "video"]
    staged.candidate.agent_streaming = False
    plan = await runtime.prepare_settings_application(staged.candidate, names)
    assert plan.restart_fields == {
        "token",
        "rss_interval",
        "agent_secret_masking",
        "agent_sticker_embed_dimensions",
        "agent_tool_output_spill",
    }
    assert plan.live_fields == names - plan.restart_fields
    assert not staged.calls
    assert staged.current.agent_streaming
    assert not staged.current.business_chat_enabled
    plan.apply()
    assert not staged.current.agent_streaming
    assert staged.current.business_chat_enabled
    assert staged.current.agent_prompt == "New prompt"
    assert staged.current.token == "offline"  # Save pending, no startup resource swap.


@pytest.mark.asyncio
async def test_env_shadow_unchanged_value_needs_no_restart_or_rebuild(staged):
    plan = await runtime.prepare_settings_application(staged.candidate, {"token"})
    assert not plan.restart_fields
    assert not plan.live_fields
    assert not plan._clients
    assert not staged.calls
    plan.apply()
    assert staged.current.token == "offline"


@pytest.mark.asyncio
async def test_owners_live_but_unselected_candidate_changes_never_leak(staged):
    staged.candidate.owners = [2]
    plan = await runtime.prepare_settings_application(
        staged.candidate, {"owners", "agent_model"}
    )
    assert not plan.restart_fields
    assert staged.current.owners == [1]
    plan.apply()
    assert staged.current.owners == [2]
    assert staged.current.agent_model_small is None
    assert staged.main.small_model is staged.main.model
    assert staged.current.agent_providers["default"].key == "old"
    assert staged.calls[0][1]["default"].key == "old"
    for client in plan._clients:
        await client.aclose()


def test_restart_classification_allocates_no_resources(staged):
    staged.candidate.token = "changed"
    assert runtime.settings_restart_fields(staged.candidate, {"token", "owners"}) == {
        "token"
    }
    assert not staged.calls
    assert not staged.http_module._clients


@pytest.mark.asyncio
async def test_private_telegram_image_mode_applies_without_restart(staged):
    staged.candidate.manyacg_r18_mode = 1
    plan = await runtime.prepare_settings_application(
        staged.candidate, {"manyacg_r18_mode"}
    )
    assert plan.live_fields == {"manyacg_r18_mode"}
    assert not plan.restart_fields
    assert staged.current.manyacg_r18_mode == 0
    await plan.activate()
    assert staged.current.manyacg_r18_mode == 1


def test_provider_factory_keeps_existing_global_proxy_path(monkeypatch):
    cfg = ProviderConfig(url="https://existing.invalid/v1", key="old")
    monkeypatch.setattr(
        provider, "app_config", SimpleNamespace(agent_providers={"default": cfg})
    )
    client = object()
    monkeypatch.setattr(provider, "_get_http_client_for_provider", lambda _: client)
    monkeypatch.setattr(provider, "OpenAIProvider", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        provider, "VideoCapableOpenAIChatModel", lambda **kwargs: kwargs
    )
    model = provider.make_chat_model("model")
    assert model["provider"]["http_client"] is client
    assert model["provider"]["base_url"] == cfg.url


@pytest.mark.asyncio
async def test_pending_embedding_provider_change_cannot_sneak_in_via_model_edit(staged):
    staged.current.agent_sticker_memory = True
    plan = await runtime.prepare_settings_application(staged.candidate, {"agent_model"})
    assert plan.restart_fields == {"agent_model"}
    assert not plan._clients
    assert not staged.calls


@pytest.mark.asyncio
async def test_disabled_agent_prepare_does_not_import_or_initialize(
    staged, monkeypatch
):
    monkeypatch.delitem(sys.modules, "waku.plugins.agent.agent")
    staged.candidate.agent = False
    plan = await runtime.prepare_settings_application(
        staged.candidate, {"agent_model", "agent"}
    )
    assert "agent" in plan.restart_fields
    assert "waku.plugins.agent.agent" not in sys.modules
    assert not staged.calls


def test_provider_prospective_factory_uses_candidate_not_global(monkeypatch):
    clients = []

    def make(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(name="candidate-provider")

    monkeypatch.setattr(provider, "OpenAIProvider", make)
    monkeypatch.setattr(
        provider, "VideoCapableOpenAIChatModel", lambda **kwargs: kwargs
    )
    monkeypatch.setattr(
        provider,
        "_get_http_client_for_provider",
        lambda _: pytest.fail("live cache used"),
    )
    providers = {"next": ProviderConfig(url="https://next.invalid/v1", key="new")}
    client = object()
    model = provider.make_chat_model(
        "next/model", providers=providers, http_client=client
    )
    assert model["model_name"] == "model"
    assert clients == [
        {"base_url": "https://next.invalid/v1", "api_key": "new", "http_client": client}
    ]
