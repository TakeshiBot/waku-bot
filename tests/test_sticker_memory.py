"""Sticker learning, scoped selection and real SQLite storage without APIs."""

import ast
import asyncio
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel, Field, ValidationError

from waku.plugins.agent import sticker_vec


@pytest.fixture
def memory():
    config = SimpleNamespace(
        agent_sticker_search_mode="chat",
        agent_sticker_search_candidates=60,
        agent_small_model_timeout=1,
        agent_download_timeout=1,
    )
    result = SimpleNamespace(output=SimpleNamespace(sticker_id=7), usage=object())
    selector = SimpleNamespace(run=AsyncMock(return_value=result))
    namespace = {
        "asyncio": asyncio,
        "json": json,
        "BytesIO": BytesIO,
        "BaseModel": BaseModel,
        "Field": Field,
        "app_config": config,
        "_selector_agent": selector,
        "embedder": None,
        "quota": SimpleNamespace(can_start=AsyncMock(return_value=True), settle=AsyncMock()),
        "trace": SimpleNamespace(start_trace=AsyncMock(), mark_trace=Mock(), finish_trace=Mock()),
        "sticker_vec": SimpleNamespace(
            candidates=AsyncMock(return_value=[(7, "local-file", "Vui vẻ, cười lớn")]),
            search=AsyncMock(return_value=[("vector-file", "Happy", 0.1)]),
            exists=AsyncMock(return_value=False),
            touch=AsyncMock(),
            upsert=AsyncMock(),
        ),
        "get_embedding": AsyncMock(return_value=[0.1, 0.2]),
        "_get_description": AsyncMock(return_value="Vui vẻ, cười lớn"),
        "logger": Mock(),
        "common": SimpleNamespace(FFMPEG=None),
    }
    path = Path(__file__).resolve().parents[1] / "waku/plugins/agent/sticker_memory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"StickerChoice", "search_ready", "select_sticker", "_process_sticker"}
    definitions = [
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *[n for n in tree.body if getattr(n, "name", None) in names],
    ]
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])), str(path), "exec"), namespace)
    return SimpleNamespace(**namespace, config=config, selector=selector, result=result)


@pytest.mark.asyncio
async def test_chat_selection_uses_group_candidates_and_bills_usage(memory):
    assert await memory.select_sticker("vui vẻ", -100123, "subject") == ("local-file", "Vui vẻ, cười lớn")
    memory.sticker_vec.candidates.assert_awaited_once_with(-100123, 60)
    payload = json.loads(memory.selector.run.call_args.args[0])
    assert payload["stickers"] == [{"id": 7, "description": "Vui vẻ, cười lớn"}]
    memory.get_embedding.assert_not_called()
    memory.quota.settle.assert_awaited_once_with("subject", memory.result.usage)
    memory.trace.finish_trace.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", [None, 999])
async def test_reject_invented_or_foreign_sticker_id(memory, choice):
    memory.result.output.sticker_id = choice
    assert await memory.select_sticker("vui", -100123, "subject") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["empty", "quota", "timeout"])
async def test_unavailable_selection_does_not_produce_file_id(memory, reason):
    if reason == "empty":
        memory.sticker_vec.candidates.return_value = []
    elif reason == "quota":
        memory.quota.can_start.return_value = False
    else:
        memory.selector.run.side_effect = TimeoutError
    assert await memory.select_sticker("vui", -100123, "subject") is None
    memory.quota.settle.assert_not_called()


@pytest.mark.asyncio
async def test_embedding_mode_keeps_native_vector_search(memory):
    memory.config.agent_sticker_search_mode = "embedding"
    assert await memory.select_sticker("vui", -100123, "subject") == ("vector-file", "Happy")
    memory.sticker_vec.search.assert_awaited_once_with(-100123, [0.1, 0.2], k=1)
    memory.selector.run.assert_not_called()


@pytest.mark.asyncio
async def test_learning_in_chat_mode_stores_description_without_embedding_api(memory):
    client = SimpleNamespace(download_media=AsyncMock(return_value=BytesIO(b"fake-image")))
    sticker = SimpleNamespace(file_unique_id="unique", file_id="telegram-file", is_animated=False, is_video=False)
    await memory._process_sticker(client, sticker, -100123, "subject")
    memory.sticker_vec.upsert.assert_awaited_once_with("unique", "telegram-file", -100123, "Vui vẻ, cười lớn", None)
    memory.get_embedding.assert_not_called()


@pytest.mark.asyncio
async def test_learning_in_embedding_mode_requires_real_embedding(memory):
    memory.config.agent_sticker_search_mode = "embedding"
    memory.get_embedding.return_value = None
    client = SimpleNamespace(download_media=AsyncMock(return_value=BytesIO(b"fake-image")))
    sticker = SimpleNamespace(file_unique_id="unique", file_id="telegram-file", is_animated=False, is_video=False)
    await memory._process_sticker(client, sticker, -100123, "subject")
    memory.sticker_vec.upsert.assert_not_called()


@pytest.mark.parametrize("value", [True, 0, -1, "7"])
def test_choice_requires_positive_integer_or_null(memory, value):
    with pytest.raises(ValidationError):
        memory.StickerChoice(sticker_id=value)


@pytest.mark.asyncio
async def test_real_database_chat_and_embedding_records_remain_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(sticker_vec, "_DB_PATH", tmp_path / "stickers.db")
    monkeypatch.setattr(sticker_vec, "_INITIALIZED", False)
    monkeypatch.setattr(sticker_vec, "_write_lock", asyncio.Lock())
    monkeypatch.setattr(sticker_vec.app_config, "agent_sticker_ttl", 0)
    await sticker_vec.init(2)
    await sticker_vec.upsert("chat", "chat-file", -1001, "Happy")
    await sticker_vec.upsert("foreign", "foreign-file", -1002, "Happy")
    await sticker_vec.upsert("vector", "vector-file", -1001, "Sad", [0.1, 0.2])
    candidates = await sticker_vec.candidates(-1001, 60)
    assert {file for _, file, _ in candidates} == {"chat-file", "vector-file"}
    assert len(await sticker_vec.candidates(-1001, 1)) == 1
    assert await sticker_vec.search(-1001, [0.1, 0.2], k=1) == [("vector-file", "Sad", 0.0)]
    await sticker_vec.upsert("chat", "updated-file", -1001, "Excited")
    assert await sticker_vec.count(-1001) == 2
    assert await sticker_vec.delete("chat", -1001)
    assert await sticker_vec.clear(-1001) == 1
    assert await sticker_vec.count(-1002) == 1
