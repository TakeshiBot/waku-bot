"""Backend presentation coverage and compatibility checks without starting the bot."""

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path
from string import Formatter
from types import ModuleType, SimpleNamespace

import pytest

from kmua.gift import define as gifts
from kmua.i18n import i18n

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def meme(monkeypatch):
    package = ModuleType("_backend_test_meme")
    package.__path__ = [str(ROOT / "kmua/plugins/inlinequery/manomeme")]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    drawer = _load(
        "_backend_test_meme.drawer",
        "kmua/plugins/inlinequery/manomeme/drawer.py",
        monkeypatch,
    )
    utils = _load(
        "_backend_test_meme.utils",
        "kmua/plugins/inlinequery/manomeme/utils.py",
        monkeypatch,
    )
    return drawer, utils


def _flatten(mapping, prefix=""):
    result = {}
    for key, value in mapping.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten(value, path))
        else:
            result[path] = value
    return result


def test_extracted_catalogues_have_matching_keys_and_placeholders():
    catalogs = {
        locale: _flatten(
            json.loads(
                (ROOT / "kmua/i18n/locales" / locale / "bot.hardcoded.yml").read_text(
                    encoding="utf-8"
                )
            )
        )
        for locale in ("vi", "zh-CN", "en")
    }
    assert catalogs["vi"].keys() == catalogs["zh-CN"].keys() == catalogs["en"].keys()
    formatter = Formatter()
    for key, text in catalogs["zh-CN"].items():
        fields = {
            (name, spec, conversion)
            for _, name, spec, conversion in formatter.parse(text)
            if name is not None
        }
        for locale in ("vi", "en"):
            translated = catalogs[locale][key]
            assert fields == {
                (name, spec, conversion)
                for _, name, spec, conversion in formatter.parse(translated)
                if name is not None
            }, (locale, key)
            assert not re.search(r"[\u3400-\u9fff]", translated), (locale, key)


def test_gift_metadata_resolves_on_each_use_in_the_selected_locale():
    for gift in [*gifts.list_all_gifts(), gifts.OTHERWORLDLY_FLOWER]:
        for locale in ("vi", "en", "zh-CN"):
            texts = [
                gifts.get_display_name(gift.id, locale),
                gift.get_description(locale),
                gift.get_comment(locale),
            ]
            assert all(text and not text.startswith("bot.") for text in texts)
            if locale != "zh-CN":
                assert all(not re.search(r"[\u3400-\u9fff]", text) for text in texts)
        assert gifts.get_display_name(gift.id) == gifts.get_display_name(gift.id, "vi")
    assert gifts.get_rarity_display_name(999) == "Không rõ"
    assert gifts.get_rarity_display_name(1, "zh-CN") == "凡芽"
    assert gifts.get_rarity_display_name(1, "en") == "Common sprout"


def test_inline_tips_are_independent_per_locale_and_use_stable_command_tokens(meme):
    drawer, utils = meme
    vi = utils.result_anan_tips("vi")
    zh = utils.result_anan_tips("zh-CN")
    assert vi.title == "Anan nói"
    assert zh.title == "安安说"
    assert vi.reply_markup.inline_keyboard[0][0].text == "Si mê"
    assert zh.reply_markup.inline_keyboard[0][0].text == "病娇"
    assert (
        vi.reply_markup.inline_keyboard[0][0].switch_inline_query_current_chat
        == "ms anan yandere "
    )
    assert utils.result_anan_tips("vi").title == vi.title
    assert utils.resolve_face("speechless") == utils.resolve_face("无语") == "无语"
    assert utils.face_token("无语") == "speechless"
    assert utils.resolve_face("invalid") is None
    assert drawer.get_character("Hiro") == drawer.get_character("希罗")
    assert drawer.get_character("Ema") == drawer.get_character("艾玛")


def test_meme_statement_parser_preserves_chinese_and_accepts_ascii_aliases(meme):
    drawer, utils = meme
    chinese = utils.parse_options('[赞同] "đồng ý" 【伪证】 "tôi không ở đó"')
    english = utils.parse_options('[agreement] "đồng ý" [PERJURY] "tôi không ở đó"')
    assert [(o.statement, o.text) for o in chinese] == [
        (o.statement, o.text) for o in english
    ]
    assert [o.statement for o in english] == [
        drawer.Statement.AGREEMENT,
        drawer.Statement.PURJURY,
    ]
    options = utils.parse_options('agreement "magic is useful" refutation "no"')
    assert [o.text for o in options] == ["magic is useful", "no"]


def test_api_locale_prefers_saved_language_and_respects_weighted_header(monkeypatch):
    module = _load("_backend_test_api_i18n", "kmua/webapp/i18n.py", monkeypatch)
    request = SimpleNamespace(
        state=SimpleNamespace(), headers={"accept-language": "en-US;q=0.4,vi-VN;q=0.9"}
    )
    assert module.request_locale(request) == "vi"
    request.state.locale = "zh-CN"
    assert module.request_locale(request) == "zh-CN"
    del request.state.locale
    request.headers["accept-language"] = "unknown, en-US;q=0.5"
    assert module.request_locale(request) == "en"
    request.headers["accept-language"] = "zh-CN;q=0, en;q=bad"
    assert module.request_locale(request) == i18n.default_locale


def test_every_api_error_code_has_localized_fallback_message(monkeypatch):
    module = _load("_backend_test_api_i18n", "kmua/webapp/i18n.py", monkeypatch)
    tree = ast.parse((ROOT / "kmua/webapp/errors.py").read_text(encoding="utf-8"))
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ErrorCode"
    )
    codes = [
        n.value.value
        for n in cls.body
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
    ]
    assert codes
    for code in codes:
        for locale in ("vi", "zh-CN", "en"):
            assert module.error_message(code, locale) == i18n.t(
                f"bot.hardcoded.api.{code}", locale=locale
            )
            assert not module.error_message(code, locale).startswith("bot.")
    assert module.error_message("UNRECOGNIZED", "vi") == module.error_message(
        "INTERNAL_ERROR", "vi"
    )


def test_api_error_handlers_localize_without_changing_codes_or_statuses(monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    package = ModuleType("kmua.webapp")
    package.__path__ = [str(ROOT / "kmua/webapp")]
    monkeypatch.setitem(sys.modules, "kmua.webapp", package)
    locale_module = _load("kmua.webapp.i18n", "kmua/webapp/i18n.py", monkeypatch)
    logger_module = ModuleType("kmua.logger")
    logger_module.logger = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "kmua.logger", logger_module)
    errors = _load("_backend_test_errors", "kmua/webapp/errors.py", monkeypatch)
    app = FastAPI()
    errors.install_error_handlers(app)

    @app.get("/domain")
    def domain(request: Request):
        request.state.locale = "vi"
        raise errors.ApiError(
            errors.ErrorCode.INSUFFICIENT_COINS, "Not enough coins", 409
        )

    @app.get("/validate")
    def validate(count: int):
        return {"count": count}

    @app.get("/invalid-url")
    def invalid_url():
        raise errors.ApiError(
            errors.ErrorCode.VALIDATION_FAILED, locale_module.api_text("url_http")
        )

    @app.get("/quota-private")
    def quota_private():
        raise errors.ApiError(
            errors.ErrorCode.VALIDATION_FAILED,
            locale_module.api_text("quota_group_only"),
        )

    schemas = _load("_backend_test_schemas", "kmua/webapp/schemas.py", monkeypatch)

    @app.post("/question")
    def question(payload: schemas.VerifyQuestionIn):
        return payload.model_dump()

    with TestClient(app) as client:
        response = client.get("/domain", headers={"Accept-Language": "en"})
        assert response.status_code == 409
        assert response.json() == {
            "code": "INSUFFICIENT_COINS",
            "message": locale_module.api_text("coins_insufficient").render("vi"),
        }
        response = client.get("/validate?count=bad", headers={"Accept-Language": "vi"})
        assert response.status_code == 422
        payload = response.json()
        assert payload["code"] == "VALIDATION_FAILED"
        assert payload["message"] == locale_module.error_message(
            "VALIDATION_FAILED", "vi"
        )
        assert payload["details"]["fields"][0]["loc"] == ["query", "count"]
        assert payload["details"]["fields"][0]["msg"] == i18n.t(
            "bot.hardcoded.api_validation.integer", "vi"
        )

        url = client.get("/invalid-url", headers={"Accept-Language": "vi"})
        quota = client.get("/quota-private", headers={"Accept-Language": "vi"})
        assert url.status_code == quota.status_code == 400
        assert url.json()["code"] == quota.json()["code"] == "VALIDATION_FAILED"
        assert url.json()["message"] == locale_module.api_text("url_http").render("vi")
        assert quota.json()["message"] == locale_module.api_text(
            "quota_group_only"
        ).render("vi")
        assert url.json()["message"] != quota.json()["message"]
        assert not url.json()["message"].startswith("Request")
        response = client.post(
            "/question",
            headers={"Accept-Language": "vi"},
            json={
                "question": "A valid question",
                "options": ["one", "two"],
                "answers": ["PRIVATE USER INPUT"],
                "select": "any",
            },
        )
        assert response.status_code == 422
        field = response.json()["details"]["fields"][0]
        assert field["loc"] == ["body"]
        assert field["type"] == "value_error"
        assert field["msg"] == locale_module.validation_text("answers_options").render(
            "vi"
        )
        assert "PRIVATE USER INPUT" not in response.text


def test_validation_reasons_preserve_schema_bounds_and_hide_user_input(monkeypatch):
    module = _load("_backend_test_api_i18n", "kmua/webapp/i18n.py", monkeypatch)
    message = module.validation_message(
        {"type": "less_than_equal", "ctx": {"le": 100}, "input": "PRIVATE INPUT"}, "vi"
    )
    assert message == module.validation_text("less_than_equal", le=100).render("vi")
    assert "100" in message and "PRIVATE INPUT" not in message
    required = module.validation_message({"type": "missing"}, "vi")
    integer = module.validation_message(
        {"type": "int_parsing", "input": "secret"}, "vi"
    )
    assert required != integer
    assert "secret" not in integer
