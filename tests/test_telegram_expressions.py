"""Telegram periodic hints without bot initialization or network calls."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.tools import ToolDefinition

from waku.plugins.agent.localization import tr


@pytest.fixture
def expressions():
    config = SimpleNamespace(
        agent_periodic_sticker_interval=5, agent_periodic_reaction_interval=3
    )
    store = AsyncMock()
    store.get.return_value = 0
    namespace = {
        "app_config": config,
        "common": SimpleNamespace(memstore=store),
        "state": SimpleNamespace(
            periodic_sticker_counter_key=lambda c, u: f"sticker:{c}:{u}",
            periodic_reaction_counter_key=lambda c, u: f"reaction:{c}:{u}",
        ),
        "tr": tr,
        "ToolDefinition": ToolDefinition,
        "prepare_sticker_tools": AsyncMock(return_value=ToolDefinition(name="send_sticker")),
    }
    path = Path(__file__).resolve().parents[1] / "waku/plugins/agent/expressions.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = [
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *[n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
    ]
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])), str(path), "exec"), namespace)
    deps = SimpleNamespace(
        periodic_expressions_enabled=True,
        message=SimpleNamespace(business_connection_id=None),
        chat_id=-100123,
        user_id=42,
        tools_called_this_turn=set(),
        locale="vi",
    )
    return SimpleNamespace(**namespace, config=config, store=store, ctx=SimpleNamespace(deps=deps))


@pytest.mark.parametrize("turns,interval,due", [(0, 0, False), (0, 1, True), (3, 5, False), (4, 5, True), (5, 5, False), (9, 5, True)])
def test_due_on_nth_turn(expressions, turns, interval, due):
    assert expressions._due(turns, interval) is due


@pytest.mark.asyncio
async def test_same_turn_repeated_requests_do_not_advance_counters(expressions):
    expressions.store.get.side_effect = lambda key, default: 4 if key.startswith("sticker:") else 2
    first = await expressions.periodic_expression_instructions(expressions.ctx)
    second = await expressions.periodic_expression_instructions(expressions.ctx)
    assert first == second
    assert "send_sticker" in first and "sendReaction" in first
    expressions.store.set.assert_not_called()


@pytest.mark.asyncio
async def test_successful_tools_remove_further_hints(expressions):
    expressions.store.get.return_value = 14
    expressions.ctx.deps.tools_called_this_turn.update({"send_sticker", "tg.sendReaction"})
    assert await expressions.periodic_expression_instructions(expressions.ctx) == ""
    expressions.store.get.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_sticker_memory_does_not_request_sticker(expressions):
    expressions.store.get.return_value = 14
    expressions.prepare_sticker_tools.return_value = None
    text = await expressions.periodic_expression_instructions(expressions.ctx)
    assert "send_sticker" not in text and "sendReaction" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["business", "ask", "missing_message", "missing_chat"])
async def test_unrelated_runs_do_not_get_hints(expressions, reason):
    if reason == "business":
        expressions.ctx.deps.message.business_connection_id = "business-test"
    elif reason == "ask":
        expressions.ctx.deps.periodic_expressions_enabled = False
    elif reason == "missing_message":
        expressions.ctx.deps.message = None
    else:
        expressions.ctx.deps.chat_id = None
    assert await expressions.periodic_expression_instructions(expressions.ctx) == ""
    expressions.store.get.assert_not_called()


@pytest.mark.asyncio
async def test_zero_disables_both_hints(expressions):
    expressions.config.agent_periodic_sticker_interval = 0
    expressions.config.agent_periodic_reaction_interval = 0
    assert await expressions.periodic_expression_instructions(expressions.ctx) == ""
    expressions.store.get.assert_not_called()
