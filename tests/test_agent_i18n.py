"""Agent locale contracts without a bot token, database, or model API calls."""

import ast
import asyncio
import html
import inspect
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from kmua.i18n import i18n
from kmua.plugins.agent.localization import (
    configured_prompt,
    current_locale,
    locale_scope,
    localized_argument,
    localized_message,
    tr,
)

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "kmua/plugins/agent"
PARAMETER = re.compile(r"(?<!\{)\{(p\d+)\}(?!\})")
CATALOGUES = {
    locale: yaml.safe_load(
        (ROOT / f"kmua/i18n/locales/{locale}/bot.agent.yml").read_text(encoding="utf-8")
    )["bot"]["agent_i18n"]
    for locale in ("vi", "en", "zh-CN")
}


def load_functions(filename, names, **namespace):
    """Execute actual pure functions without importing Telegram/model startup."""
    tree = ast.parse((AGENT / filename).read_text(encoding="utf-8"))
    definitions = [
        node for node in ast.walk(tree) if getattr(node, "name", None) in names
    ]
    assert {node.name for node in definitions} == set(names)
    # Python's postponed annotations don't affect function behavior; fixtures
    # supply structural data matching the real dataclasses.
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[future, *definitions], type_ignores=[])
    )
    namespace.update(tr=tr, localized_argument=localized_argument)
    exec(compile(module, str(AGENT / filename), "exec"), namespace)
    return SimpleNamespace(**namespace)


def test_agent_catalogues_have_matching_keys_and_formatted_parameters():
    assert (
        CATALOGUES["vi"].keys() == CATALOGUES["en"].keys() == CATALOGUES["zh-CN"].keys()
    )
    for key, chinese in CATALOGUES["zh-CN"].items():
        expected = set(PARAMETER.findall(chinese))
        for locale, catalogue in CATALOGUES.items():
            text = catalogue[key]
            assert text.strip(), (locale, key)
            assert set(PARAMETER.findall(text)) == expected, (locale, key)
            if expected:
                values = {parameter: f"value-{parameter}" for parameter in expected}
                formatted = tr(key, locale=locale, **values)
                assert not PARAMETER.search(formatted), (locale, key)
                assert all(value in formatted for value in values.values()), (
                    locale,
                    key,
                )


def test_agent_catalogue_unicode_is_preserved():
    for locale, catalogue in CATALOGUES.items():
        for key, text in catalogue.items():
            # URI query delimiters and final question marks are intentional.
            prose = text.replace("chat://history?reply_chain_of=", "")
            assert "\ufffd" not in prose and "??" not in prose, (locale, key)
            if locale == "vi":
                assert not re.search(r"\?[ A-Za-z]", prose), (locale, key, prose)
    assert CATALOGUES["vi"]["output_language"].startswith("Trả lời bằng tiếng Việt")


def test_compaction_merge_preserves_tags_data_and_localized_headings():
    summary = "User data 中文 {literal} <arbitrary-tag>"
    for locale, headings in (
        ("vi", ("Đang thực hiện", "Các bước tiếp theo")),
        ("zh-CN", ("进行中", "下一步")),
        ("en", ("In Progress", "Next Steps")),
    ):
        result = tr(
            "compaction_merge", locale=locale, p0="configured instructions", p1=summary
        )
        assert all(heading in result for heading in headings)
        assert f"<previous-summary>\n{summary}\n</previous-summary>" in result


def test_agent_runtime_text_has_no_chinese_literals_and_references_exist():
    for path in AGENT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if not isinstance(parents.get(node), ast.Expr):
                    assert not re.search(r"[\u3400-\u9fff]", node.value), (
                        path,
                        node.lineno,
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
            ):
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    continue
                key = node.args[0].value
                assert key in CATALOGUES["vi"], (path, node.lineno, key)
                required = set(PARAMETER.findall(CATALOGUES["vi"][key]))
                supplied = {
                    keyword.arg for keyword in node.keywords if keyword.arg != "locale"
                }
                assert required == supplied, (
                    path,
                    node.lineno,
                    key,
                    required,
                    supplied,
                )


def test_agent_command_reply_prose_is_always_localized():
    tree = ast.parse((AGENT / "agent.py").read_text(encoding="utf-8"))
    commands = {
        "clear_sessions_command",
        "set_model_command",
        "set_prompt_command",
        "forget_history",
    }
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in commands
    ]
    assert {function.name for function in functions} == commands
    for function in functions:
        for node in ast.walk(function):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr != "reply_text"
            ):
                continue
            for argument in node.args:
                assert not isinstance(argument, (ast.Constant, ast.JoinedStr)), (
                    function.name,
                    node.lineno,
                )


@pytest.mark.asyncio
async def test_parallel_locales_do_not_leak_and_reset_on_error_or_cancellation():
    @localized_argument("lang")
    async def translated(lang: str, fail: bool = False) -> str:
        await asyncio.sleep(0)
        if fail:
            raise RuntimeError("test")
        return tr("sticker_reply_required")

    before = current_locale()
    vi, en, zh = await asyncio.gather(
        *(translated(lang) for lang in ("vi", "en", "zh-CN"))
    )
    assert vi == CATALOGUES["vi"]["sticker_reply_required"]
    assert en == CATALOGUES["en"]["sticker_reply_required"]
    assert zh == CATALOGUES["zh-CN"]["sticker_reply_required"]
    assert current_locale() == before
    with pytest.raises(RuntimeError):
        await translated("en", fail=True)
    assert current_locale() == before
    assert inspect.signature(translated) == inspect.signature(translated.__wrapped__)

    @localized_argument("lang")
    async def cancelled(lang: str):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cancelled("zh-CN")
    assert current_locale() == before


def test_builtin_prompts_are_localized_and_custom_prompts_are_preserved(monkeypatch):
    field = "agent_multimodal_transcribe_prompt"
    fake = SimpleNamespace(app_config=SimpleNamespace())
    monkeypatch.setitem(sys.modules, "kmua.config", fake)
    key = f"bot.prompts.{field}"
    for locale in ("vi", "en", "zh-CN"):
        setattr(fake.app_config, field, i18n.t(key, locale))
        with locale_scope("vi"):
            assert configured_prompt(field) == i18n.t(key, "vi")
    custom = "Custom 中文 prompt {user_data}; keep this exactly."
    setattr(fake.app_config, field, custom)
    assert configured_prompt(field, "vi") == custom


@pytest.mark.asyncio
async def test_message_and_callback_locale_resolution(monkeypatch):
    async def user_config(user_id):
        return SimpleNamespace(lang="en")

    async def chat_config(chat_id):
        return SimpleNamespace(lang="zh-CN")

    import kmua

    monkeypatch.setattr(
        kmua,
        "database",
        SimpleNamespace(get_user_config=user_config, get_chat_config=chat_config),
        raising=False,
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42), chat=SimpleNamespace(id=-10)
    )

    @localized_message
    async def handler(message):
        return current_locale()

    @localized_message(prefer_chat=True)
    async def group_handler(message):
        return current_locale()

    @localized_message
    async def callback(callback_query):
        return current_locale()

    assert await handler(message) == "en"
    assert await group_handler(message) == "zh-CN"
    assert (
        await callback(SimpleNamespace(from_user=message.from_user, message=message))
        == "en"
    )
    message.chat.id = 42
    assert await group_handler(message) == "en"
    assert current_locale() == "vi"


@pytest.mark.asyncio
async def test_ask_uses_group_locale_and_private_user_locale():
    import pyrogram

    notify = AsyncMock()
    functions = load_functions(
        "agent.py",
        {"_run_agent_for_ask"},
        app_config=SimpleNamespace(
            agent=True, agent_private_chat_required_channel=None
        ),
        agent=object(),
        is_chat_allowed=lambda chat_id: True,
        tools=SimpleNamespace(is_user_blocked=AsyncMock(return_value=False)),
        pyrogram=pyrogram,
        database=SimpleNamespace(
            get_user_config=AsyncMock(return_value=SimpleNamespace(lang="en")),
            get_chat_config=AsyncMock(return_value=SimpleNamespace(lang="zh-CN")),
        ),
        quota=SimpleNamespace(
            subject_for_chat=lambda user_id, chat: object(),
            can_start=AsyncMock(return_value=False),
            get_state=AsyncMock(return_value=object()),
            notify_exhausted=notify,
        ),
        trace=SimpleNamespace(note_rejection=AsyncMock()),
    )
    message = SimpleNamespace(
        id=5, chat=SimpleNamespace(id=-10, type=pyrogram.enums.ChatType.SUPERGROUP)
    )
    await functions._run_agent_for_ask(None, message, 42, -10, "answer")
    assert notify.call_args.args[-1] == "zh-CN"
    message.chat.id = 42
    message.chat.type = pyrogram.enums.ChatType.PRIVATE
    await functions._run_agent_for_ask(None, message, 42, 42, "answer")
    assert notify.call_args.args[-1] == "en"


@pytest.mark.asyncio
async def test_admin_command_locales_html_escaping_and_custom_prompt_preservation(
    monkeypatch,
):
    import pyrogram

    import kmua

    database = SimpleNamespace(
        get_user_config=AsyncMock(return_value=SimpleNamespace(lang="en")),
        get_chat_config=AsyncMock(return_value=SimpleNamespace(lang="vi")),
        get_user_by_id=AsyncMock(
            return_value=SimpleNamespace(is_bot_global_admin=True)
        ),
    )
    monkeypatch.setattr(kmua, "database", database, raising=False)
    set_model = AsyncMock()
    set_prompt = AsyncMock()
    functions = load_functions(
        "agent.py",
        {"set_model_command", "set_prompt_command", "clear_sessions_command"},
        localized_message=localized_message,
        PyrogramClient=SimpleNamespace(
            on_message=lambda *args, **kwargs: lambda function: function
        ),
        pyrogram=pyrogram,
        app_config=SimpleNamespace(
            agent=True,
            agent_model="global/model<default>",
            agent_model_multimodal=None,
            agent_model_small=None,
            owners=[42],
        ),
        database=database,
        is_chat_allowed=lambda chat_id: True,
        get_chat_model_override=AsyncMock(return_value="previous/model<old>&"),
        set_chat_model_override=set_model,
        get_chat_prompt_override=AsyncMock(return_value=None),
        set_chat_prompt_override=set_prompt,
        html=html,
        logger=SimpleNamespace(info=lambda *args: None),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(id=-10),
        text="/model main provider/model<new>&",
        reply_text=AsyncMock(),
    )
    await functions.set_model_command(None, message)
    set_model.assert_awaited_once_with(-10, "provider/model<new>&", "main")
    reply = message.reply_text.call_args.args[0]
    assert "Đã đặt mô hình chính" in reply
    assert (
        "provider/model&lt;new&gt;&amp;" in reply
        and "previous/model&lt;old&gt;&amp;" in reply
    )
    functions.get_chat_model_override.return_value = None
    message.text = "/model"
    await functions.set_model_command(None, message)
    reply = message.reply_text.call_args.args[0]
    assert "Mô hình tùy chỉnh hiện tại" in reply and "mặc định chung" in reply
    assert "global/model&lt;default&gt;" in reply
    custom = "Custom 中文 {data} <content>; do not translate."
    message.text = "/prompt " + custom
    await functions.set_prompt_command(None, message)
    set_prompt.assert_awaited_once_with(-10, custom)
    assert message.reply_text.call_args.args[0] == CATALOGUES["vi"]["prompt_set"]
    message.text = "/clear_sessions"
    await functions.clear_sessions_command(None, message)
    assert (
        message.reply_text.call_args.args[0]
        == CATALOGUES["vi"]["clear_sessions_confirm"]
    )


def test_rss_prompts_respect_locale_and_preserve_entries_and_json_protocol():
    functions = load_functions(
        "rss_digest.py", {"build_digest_prompt", "build_broadcast_prompt"}
    )
    entry = SimpleNamespace(
        entry_id="stable-id",
        title="中文 {user}",
        link="https://example.test/news",
        summary="Nguồn gốc\n第二行",
    )
    prompt = functions.build_digest_prompt([entry], "Feed")
    assert "Dưới đây" in prompt and "ngôn ngữ vi" in prompt
    assert entry.title in prompt and entry.link in prompt and "[stable-id]" in prompt
    assert '"entry_id"' in prompt and '"summary"' in prompt and "summaries" in prompt
    assert "These new entries" in functions.build_digest_prompt([entry], "Feed", "en")
    assert "以下" in functions.build_broadcast_prompt([entry], "Feed", "zh-CN")
    assert current_locale() == "vi"


@pytest.mark.asyncio
async def test_time_results_are_vietnamese_and_iso_protocol_is_preserved():
    functions = load_functions(
        "tools/time.py",
        {"_NowResult", "_DifferenceResult", "_now", "_difference"},
        dataclass=dataclass,
        datetime=datetime,
        UTC=UTC,
    )
    with locale_scope("vi"):
        now = await functions._now("UTC", "both")
        assert now.success and "Thời gian hiện tại" in now.message
        assert datetime.fromisoformat(now.iso_format).utcoffset().total_seconds() == 0
        result = await functions._difference(
            "2026-10-01T10:00:00+00:00", "2026-10-02T12:03:04+00:00"
        )
        assert result.success and "1 ngày 2 giờ 3 phút 4 giây" in result.message
        error = await functions._difference("invalid", "invalid")
        assert not error.success and error.message.startswith("Không thể tính")
