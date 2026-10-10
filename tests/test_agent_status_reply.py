"""Persona-based failure replies use a real, offline PydanticAI model run."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from waku.plugins.agent import recovery
from waku.plugins.agent.output import DeliveryUncertain


@pytest.fixture
def deps(monkeypatch):
    monkeypatch.setattr(recovery.app_config, "agent_model_options", {})
    return SimpleNamespace(
        instructions="Custom Waku persona: speak warmly and use the user's style.",
        message=SimpleNamespace(
            text="waku mute @user 1m", rich_message=None, caption=None
        ),
        moderation_results={
            "mute:3": "Completed mute user 3.",
            "duplicate": "Completed mute user 3.",
        },
        side_effects_started=True,
    )


@pytest.mark.asyncio
async def test_status_model_uses_persona_language_verified_receipts_and_no_tools(deps):
    calls = []

    async def respond(messages, info):
        calls.append((messages, info))
        return ModelResponse(parts=[TextPart("Tui đã mute người đó một phút rồi nha.")])

    result = await recovery.compose_status_reply(
        FunctionModel(respond),
        deps,
        "vi",
        "The main run failed after a confirmed mute.",
        "Additional conversation context.",
    )
    assert result[0] == "Tui đã mute người đó một phút rồi nha."
    assert len(calls) == 1
    messages, info = calls[0]
    assert deps.instructions in info.instructions
    assert "Additional conversation context." in info.instructions
    assert info.instructions.index(
        "Additional conversation context."
    ) < info.instructions.index("Write one short")
    assert "Do not invent successful actions" in info.instructions
    assert "unknown Telegram outcomes" in info.instructions
    assert not info.function_tools and not info.output_tools
    payload = json.loads(
        next(
            part.content
            for msg in messages
            for part in msg.parts
            if isinstance(part, UserPromptPart)
        )
    )
    assert payload["request"] == deps.message.text
    assert payload["moderation_results"] == ["Completed mute user 3."]
    assert payload["side_effects_started"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [None, "   "])
async def test_failed_or_empty_model_allows_static_fallback(deps, output):
    calls = []

    async def respond(messages, info):
        calls.append(info)
        if output is None:
            raise RuntimeError("API unavailable")
        return ModelResponse(parts=[TextPart(output)])

    assert (
        await recovery.compose_status_reply(
            FunctionModel(respond), deps, "vi", "Interrupted"
        )
        is None
    )
    assert len(calls) == 1


@pytest.fixture
def delivery(monkeypatch, deps):
    compose = AsyncMock(
        return_value=("Natural AI status.", SimpleNamespace(requests=1))
    )
    output = AsyncMock(return_value=True)
    record = Mock()
    settle = AsyncMock()
    monkeypatch.setattr(recovery, "compose_status_reply", compose)
    monkeypatch.setattr(recovery, "reply_output", output)
    monkeypatch.setattr(recovery, "record_sent_text", record)
    monkeypatch.setattr(recovery.quota, "settle", settle)
    message = SimpleNamespace(reply_text=AsyncMock())

    async def run():
        await recovery.reply_agent_status(
            "client",
            message,
            model="model",
            deps=deps,
            lang="vi",
            fallback="Static fallback.",
            facts="Verified facts",
            subject="subject",
        )

    return SimpleNamespace(
        run=run,
        compose=compose,
        output=output,
        record=record,
        settle=settle,
        message=message,
    )


@pytest.mark.asyncio
async def test_successful_status_goes_through_existing_rich_output_and_quota(delivery):
    await delivery.run()
    delivery.output.assert_awaited_once_with(
        "client", delivery.message, "Natural AI status."
    )
    delivery.record.assert_called_once()
    delivery.settle.assert_awaited_once()
    delivery.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_static_fallback_only_when_status_model_unavailable(delivery):
    delivery.compose.return_value = None
    await delivery.run()
    delivery.message.reply_text.assert_awaited_once_with(
        "Static fallback.", reply_markup=None
    )
    delivery.output.assert_not_awaited()
    delivery.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_lost_status_ack_never_sends_another_message(delivery):
    delivery.output.side_effect = DeliveryUncertain("Lost ACK")
    await delivery.run()
    delivery.output.assert_awaited_once()
    delivery.message.reply_text.assert_not_awaited()
    delivery.record.assert_not_called()
    delivery.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_settlement_failure_does_not_repeat_delivered_reply(delivery):
    delivery.settle.side_effect = RuntimeError("Settlement failed")
    await delivery.run()
    delivery.output.assert_awaited_once()
    delivery.record.assert_called_once()
    delivery.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_status_run_is_not_replaced_with_static_reply(delivery):
    delivery.compose.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await delivery.run()
    delivery.output.assert_not_awaited()
    delivery.message.reply_text.assert_not_awaited()
