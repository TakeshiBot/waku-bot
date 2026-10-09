"""Catalogue contracts: placeholders and challenge data must survive translation."""

import ast
import re
from pathlib import Path

import pytest
import yaml

from kmua.i18n import DEFAULT_LOCALE, I18n, i18n, normalize_locale

ROOT = Path(__file__).resolve().parents[1]
FIELDS = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z_0-9]*)(?:![rsa])?(?::[^{}]*)?\}(?!\})")


def flatten(data, prefix=""):
    result = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten(value, path))
        else:
            result[path] = value
    return result


def compare_shape(source, target, path):
    assert type(source) is type(target), path
    if isinstance(source, str):
        assert target.strip(), path
        assert set(FIELDS.findall(source)) == set(FIELDS.findall(target)), path
    elif isinstance(source, dict):
        assert source.keys() == target.keys(), path
        for key in source:
            compare_shape(source[key], target[key], f"{path}.{key}")
    elif isinstance(source, list):
        assert len(source) == len(target), path
        for index, (old, new) in enumerate(zip(source, target, strict=True)):
            compare_shape(old, new, f"{path}[{index}]")
    else:
        assert source == target, path


def test_vietnamese_covers_chinese_and_english_catalogue():
    vi = flatten(i18n.translations["vi"])
    for locale in ("zh-CN", "en"):
        source = flatten(i18n.translations[locale])
        assert not source.keys() - vi.keys(), sorted(source.keys() - vi.keys())
    for path, source in flatten(i18n.translations["zh-CN"]).items():
        compare_shape(source, vi[path], path)


def test_vi_has_no_chinese_text_or_duplicate_keys():
    seen = set()
    for path in sorted((ROOT / "kmua/i18n/locales/vi").glob("*.yml")):
        contents = path.read_text(encoding="utf-8")
        assert not re.search(r"[\u3400-\u9fff]", contents), path
        values = flatten(yaml.safe_load(contents))
        assert not seen & values.keys(), (path, seen & values.keys())
        seen.update(values)


@pytest.mark.parametrize(
    ("tag", "normalized"),
    [
        (None, "vi"),
        ("", "vi"),
        ("vi-VN", "vi"),
        ("VI_vn", "vi"),
        ("en-US", "en"),
        ("zh-TW", "zh-Hant"),
        ("zh-cn", "zh-CN"),
        ("ja", "ja-JP"),
        ("ko-KR", "ko-KR"),
        ("Martian", "Martian"),
        ("🤪", "🤪"),
        (" vi-VN ", "vi"),
        ("zh-Hant-TW", "zh-Hant"),
    ],
)
def test_aliases(tag, normalized):
    assert normalize_locale(tag) == normalized


def test_default_fallback_and_explicit_language():
    assert DEFAULT_LOCALE == i18n.default_locale == "vi"
    key = "bot.button.back"
    assert i18n.t(key) == i18n.t(key, locale="vi-VN") == "Quay lại"
    assert i18n.t(key, locale="unsupported") == "Quay lại"
    assert i18n.t(key, locale="en") == "Back"
    assert i18n.t("missing.key") == "missing.key"
    assert i18n.get_available_locales()[0] == "vi"


def test_partial_language_falls_back_to_vi():
    key = "log.commands_updated"
    assert i18n.t(key, locale="en") == i18n.t(key, locale="vi")


def test_structured_verification_questions_keep_valid_answers():
    questions = i18n.get_raw("bot.msg.verify.default_questions", locale="vi")
    assert len(questions) == 3
    for question in questions:
        assert set(question["answers"]) <= set(question["options"])
        assert len(question["options"]) == len(set(question["options"]))
        assert question.get("select", "any") == "any"
    key = "bot.msg.verify.math_hard.calculus.limit"
    assert i18n.t(key).format(k=3) == r"$\lim_{x\to 0}\frac{\sin(3x)}{x}$"
    assert "{a}" not in i18n.t("bot.msg.verify.challenge_math_easy").format(
        a=2, b=3, attempts=2, max=3
    )


def test_random_list_and_raw_values_are_distinct():
    key = "bot.msg.loading"
    choices = i18n.get_raw(key)
    assert i18n.trl(key) in choices
    assert isinstance(i18n.get_raw("bot.msg.verify.default_questions"), list)
    assert i18n.t(key) == key
    assert i18n.trl("missing.list") == "missing.list"


def test_infographic_help_keeps_valid_multiline_example():
    help_text = i18n.t("bot.msg.infographic.usage")
    assert "\\\n" not in help_text
    assert "horizontal-arrow\ndata\n  title" in help_text


@pytest.mark.parametrize("lang", ["vi", "en", "zh-CN"])
def test_config_prompt_defaults_follow_language_and_preserve_custom(lang):
    from typing import Any

    import pydantic

    path = ROOT / "kmua/config/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"ProviderConfig", "_AppConfig"}
    ]
    namespace = {
        "pydantic": pydantic,
        "Any": Any,
        "Path": Path,
        "i18n": i18n,
        "__file__": str(path),
        "__name__": "config_test",
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    config_type = namespace["_AppConfig"]
    config_type.model_rebuild(_types_namespace=namespace)
    assert config_type(token="test-token", owners=[]).lang == "vi"
    config = config_type(token="test-token", owners=[], lang=lang)
    assert config.lang == lang
    for field in (
        "agent_compaction_summary_instruction",
        "agent_multimodal_transcribe_prompt",
        "agent_sticker_description_prompt",
        "agent_channel_comment_prompt",
    ):
        assert getattr(config, field) == i18n.t(f"bot.prompts.{field}", locale=lang)
    custom = config_type(
        token="test-token",
        owners=[],
        lang=lang,
        agent_channel_comment_prompt="Custom ${protocol} prompt",
    )
    assert custom.agent_channel_comment_prompt == "Custom ${protocol} prompt"


def test_shipped_settings_and_dev_payload_default_to_vi():
    import tomllib

    settings = tomllib.loads((ROOT / "settings.ex.toml").read_text(encoding="utf-8"))
    assert settings["lang"] == "vi"
    tree = ast.parse((ROOT / "scripts/dev_init_data.py").read_text(encoding="utf-8"))
    defaults = [
        item.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "--lang"
        for item in node.keywords
        if item.arg == "default" and isinstance(item.value, ast.Constant)
    ]
    assert defaults == ["vi"]


def test_reload_and_changed_default_are_consistent():
    translations = I18n()
    translations.set_default_locale("en-US")
    assert translations.t("bot.button.back") == "Back"
    translations.reload()
    assert translations.t("bot.button.back") == "Back"
    translations.set_default_locale("vi-VN")
    assert translations.t("bot.button.back") == "Quay lại"
    with pytest.raises(ValueError):
        translations.set_default_locale("invalid")


def test_malformed_catalogues_fail_visibly(tmp_path):
    target = tmp_path / "vi"
    target.mkdir()
    (target / "bad.yml").write_text("- not a mapping", encoding="utf-8")
    with pytest.raises(ValueError, match="Catalogue root"):
        I18n(tmp_path)


def test_duplicate_yaml_keys_fail_visibly(tmp_path):
    target = tmp_path / "vi"
    target.mkdir()
    (target / "bad.yml").write_text(
        "bot:\n  title: first\n  title: second\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Duplicate translation key"):
        I18n(tmp_path)


def test_static_translation_references_exist():
    missing = []
    for path in (ROOT / "kmua").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = node.func
            if not (
                isinstance(function, ast.Attribute)
                and function.attr in {"t", "trl", "get_raw"}
                and isinstance(function.value, ast.Name)
                and function.value.id == "i18n"
            ):
                continue
            key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if i18n.get_raw(key.value, locale="vi") is None:
                    missing.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: {key.value}"
                    )
    assert not missing, "\n".join(missing)


def test_saved_language_preserved_while_new_defaults_are_vi():
    # The dataclasses are evaluated without importing database drivers or connecting.
    path = ROOT / "kmua/database/models.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    from dataclasses import asdict, dataclass, field

    namespace = {"asdict": asdict, "dataclass": dataclass, "field": field}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in {"UserConfig", "ChatConfig"}
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    for name in ("UserConfig", "ChatConfig"):
        config = namespace[name]
        assert config().lang == "vi"
        assert config.from_dict({}).lang == "vi"
        assert config.from_dict(None).lang == "vi"
        assert config.from_dict({"lang": "en"}).lang == "en"
        assert config.from_dict({"lang": "zh-CN"}).lang == "zh-CN"


def test_complete_source_audit_passes():
    from scripts.check_i18n import validate

    errors, backend_count, frontend_count, retained = validate()
    assert not errors, "\n".join(errors)
    assert backend_count >= 408
    assert frontend_count >= 456
    assert retained  # Chinese parser aliases and asset IDs remain compatible.
