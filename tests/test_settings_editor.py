"""Settings edits exercise real TOML and the new base schema, without bot IO."""

import ast
import tomllib
from pathlib import Path
from typing import Any

import pydantic
import pytest

from waku.i18n import i18n
from waku.services.settings_editor import (
    SettingsEditError,
    SettingsEditor,
    display_value,
    parse_value,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def schemas():
    tree = ast.parse((ROOT / "waku/config/__init__.py").read_text(encoding="utf-8"))
    definitions = [
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name in {"ProviderConfig", "_AppConfig"}
    ]
    namespace = {
        "pydantic": pydantic,
        "Path": Path,
        "Any": Any,
        "i18n": i18n,
        "__file__": str(ROOT / "waku/config/__init__.py"),
    }
    exec(
        compile(ast.Module(body=definitions, type_ignores=[]), "config-schema", "exec"),
        namespace,
    )
    return namespace["_AppConfig"], namespace["ProviderConfig"]


@pytest.fixture
def editor(tmp_path, schemas):
    path = tmp_path / "settings.toml"
    path.write_text(
        '# Keep this comment\ntoken = "example-token"\nowners = [1]\n'
        'agent_model = "default/example"\nagent_prompt = "old" # prompt comment\n'
        '[agent_providers.default]\nurl = "http://localhost:8000/v1"\nkey = "example-key"\n',
        encoding="utf-8",
    )
    schema, provider = schemas
    runtime = schema(
        token="environment-token", owners=[1], agent_prompt="stale-runtime-prompt"
    )
    return SettingsEditor([path], schema, provider, lambda: runtime.model_dump())


def revision(editor):
    return editor.snapshot()[1]


def read(editor):
    return tomllib.loads(editor.path.read_text(encoding="utf-8"))


def test_multiline_prompt_and_root_insert_preserve_comments_and_provider(editor):
    prompt = (
        'Tiếng Việt\n"quote" and \\path\n[agent_providers.fake]\nkey = "not-a-table"'
    )
    editor.set_value("agent_prompt", prompt, revision(editor))
    editor.set_value("rss_interval", "42", revision(editor))
    parsed = read(editor)
    assert parsed["agent_prompt"] == prompt
    assert parsed["rss_interval"] == 42
    assert "rss_interval" not in parsed["agent_providers"]["default"]
    assert "fake" not in parsed["agent_providers"]
    assert "# Keep this comment" in editor.path.read_text()
    assert "# prompt comment" in editor.path.read_text()
    assert parsed["agent_providers"]["default"]["key"] == "example-key"


@pytest.mark.parametrize(
    "key,value",
    [
        ("rss_interval", "0"),
        ("owners", '"oops"'),
        ("agent_model", "default/"),
        ("agent_model", "missing/model"),
        ("agent_providers.default.type", "unsupported"),
    ],
)
def test_invalid_values_preserve_original(editor, key, value):
    original = editor.path.read_bytes()
    with pytest.raises(SettingsEditError):
        editor.set_value(key, value, revision(editor))
    assert editor.path.read_bytes() == original


def test_stale_file_is_never_overwritten(editor):
    before = revision(editor)
    editor.path.write_text(editor.path.read_text() + "\n# external edit\n")
    original = editor.path.read_bytes()
    with pytest.raises(SettingsEditError, match="stale_file"):
        editor.set_value("lang", "en", before)
    assert editor.path.read_bytes() == original


def test_replace_failure_preserves_original_and_cleans_temp(editor, monkeypatch):
    from waku.services import settings_editor

    original = editor.path.read_bytes()

    def fail(*args):
        raise OSError("no space")

    monkeypatch.setattr(settings_editor.os, "replace", fail)
    with pytest.raises(SettingsEditError, match="write_failed"):
        editor.set_value("lang", "en", revision(editor))
    assert editor.path.read_bytes() == original
    assert not list(editor.path.parent.glob(".settings-editor-*"))


def test_provider_crud_and_existing_dotted_name(editor):
    editor.add_provider("new-provider", revision(editor))
    editor.set_value(
        "agent_providers.new-provider.api_type", "ollama", revision(editor)
    )
    assert read(editor)["agent_providers"]["new-provider"]["api_type"] == "ollama"
    editor.delete_provider("new-provider", revision(editor))
    assert "new-provider" not in read(editor)["agent_providers"]
    editor.path.write_text(
        editor.path.read_text() + '\n[agent_providers."foo.bar"]\nkey = "old"\n'
    )
    editor.set_value("agent_providers.foo.bar.key", "replacement", revision(editor))
    assert read(editor)["agent_providers"]["foo.bar"]["key"] == "replacement"
    editor.delete_provider("foo.bar", revision(editor))
    assert "default" in read(editor)["agent_providers"]


def test_delete_referenced_provider_rejected(editor):
    original = editor.path.read_bytes()
    with pytest.raises(SettingsEditError, match="provider_in_use"):
        editor.delete_provider("default", revision(editor))
    assert editor.path.read_bytes() == original


@pytest.mark.parametrize("layered", [False, True])
@pytest.mark.parametrize("operation", ["add", "edit"])
def test_schema_default_providers_materialize_without_toml_null(
    tmp_path, schemas, layered, operation
):
    schema, provider_schema = schemas
    base = tmp_path / "settings.toml"
    base.write_text('# Keep defaults\ntoken="example"\nowners=[1]\n', encoding="utf-8")
    paths = [base]
    if layered:
        override = tmp_path / "settings.dev.toml"
        override.write_text('lang="vi"\n', encoding="utf-8")
        paths.append(override)
    runtime = schema(token="example", owners=[1])
    editor = SettingsEditor(paths, schema, provider_schema, runtime.model_dump)
    assert editor.snapshot()[0]["agent_providers"]["default"]["proxy"] is None
    base_before = base.read_bytes()
    if operation == "add":
        editor.add_provider("local", revision(editor))
        assert read(editor)["agent_providers"]["local"]["type"] == "chat_completions"
    else:
        editor.set_value("agent_providers.default.key", "replacement", revision(editor))
        assert read(editor)["agent_providers"]["default"]["key"] == "replacement"
    assert "proxy" not in read(editor)["agent_providers"]["default"]
    assert (
        editor.snapshot()[0]["agent_providers"]["default"]["url"]
        == provider_schema().url
    )
    editor.validate_for_restart()
    if layered:
        assert base.read_bytes() == base_before
    else:
        assert "# Keep defaults" in base.read_text(encoding="utf-8")


def test_secret_values_never_appear_in_display_or_validation_error(editor):
    for key in ("token", "agent_providers.default.key", "agent_proxy", "db_url"):
        assert "private-credential" not in display_value(key, "private-credential")
    assert "private-credential" not in display_value(
        "provider.url",
        "https://private-credential@example.test/v1?key=private-credential",
    )
    with pytest.raises(SettingsEditError) as result:
        editor.set_value("owners", '["private-credential"]', revision(editor))
    assert "private-credential" not in str(result.value)


def test_clear_nullable_field_reads_defaults_instead_of_stale_runtime(editor):
    editor.set_value("agent_model_small", "default/small", revision(editor))
    editor.set_value("agent_model_small", "null", revision(editor))
    assert "agent_model_small" not in read(editor)
    assert editor.snapshot()[0]["agent_model_small"] is None


def test_override_file_is_targeted_without_rewriting_base(editor):
    original = editor.path.read_bytes()
    override = editor.path.parent / "settings.dev.toml"
    override.write_text('lang = "vi"\n')
    layered = SettingsEditor(
        [editor.path, override], editor.schema, editor.provider_schema, editor.runtime
    )
    layered.set_value("lang", "en", revision(layered))
    assert editor.path.read_bytes() == original
    assert tomllib.loads(override.read_text())["lang"] == "en"


def test_nullable_inherited_value_not_silently_cleared(editor):
    editor.set_value("agent_model_small", "default/small", revision(editor))
    override = editor.path.parent / "settings.dev.toml"
    override.write_text('lang = "vi"\n')
    layered = SettingsEditor(
        [editor.path, override], editor.schema, editor.provider_schema, editor.runtime
    )
    with pytest.raises(SettingsEditError, match="inherited_value"):
        layered.set_value("agent_model_small", "null", revision(layered))


def test_parse_union_numbers_lists_and_strings():
    assert parse_value("-100123", str | int | None) == -100123
    assert parse_value("@channel", str | int | None) == "@channel"
    assert parse_value("[1, 2]", list[int]) == [1, 2]
    assert (
        parse_value('"quoted prompt"\nsecond line', str)
        == '"quoted prompt"\nsecond line'
    )
    assert parse_value("null", str | None) is None


def test_read_and_edit_crlf_file(editor):
    editor.path.write_bytes(
        editor.path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    )
    editor.set_value("lang", "en", revision(editor))
    assert read(editor)["lang"] == "en"


def test_root_unknown_tables_survive_edit(editor):
    editor.path.write_text(editor.path.read_text() + '\n[custom]\nvalue = "keep me"\n')
    editor.set_value("lang", "en", revision(editor))
    assert read(editor)["custom"]["value"] == "keep me"
