"""Validate translation coverage and reject unclassified Chinese runtime strings."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from waku.i18n import i18n  # noqa: E402

FIELDS = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z_0-9]*)(?:![rsa])?(?::[^{}]*)?\}(?!\})")
HAN = re.compile(r"[\u3400-\u9fff]")
LEGACY_TOKENS = {
    # Preserve the old Discord command aliases; these are parser tokens.
    "waku/discordbot/constants.py": {"涩图", "色图"},
    "waku/plugins/inlinequery/main.py": {"无语"},
    "waku/plugins/inlinequery/manomeme/utils.py": {
        "病娇",
        "生气",
        "害羞",
        "无语",
        "开心",
    },
    "waku/plugins/inlinequery/manomeme/drawer.py": {
        "赞同",
        "疑问",
        "伪证",
        "反驳",
        "魔法",
        "艾玛",
        "希罗",
    },
}


def flatten(data, prefix=""):
    result = {}
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten(value, name))
        else:
            result[name] = value
    return result


def shape_errors(source, target, path):
    if type(source) is not type(target):
        return [f"{path}: translation type differs"]
    if isinstance(source, str):
        errors = []
        if not target.strip():
            errors.append(f"{path}: empty translation")
        if set(FIELDS.findall(source)) != set(FIELDS.findall(target)):
            errors.append(f"{path}: placeholders differ")
        return errors
    if isinstance(source, list):
        if len(source) != len(target):
            return [f"{path}: list length differs"]
        return [
            error
            for n, (a, b) in enumerate(zip(source, target, strict=True))
            for error in shape_errors(a, b, f"{path}[{n}]")
        ]
    if isinstance(source, dict):
        if source.keys() != target.keys():
            return [f"{path}: structured data keys differ"]
        return [
            error
            for key in source
            for error in shape_errors(source[key], target[key], f"{path}.{key}")
        ]
    return [] if source == target else [f"{path}: protocol value differs"]


def validate():
    errors = []
    vi = flatten(i18n.translations["vi"])
    for locale in ("zh-CN", "en"):
        source = flatten(i18n.translations[locale])
        errors.extend(f"backend vi missing {key}" for key in source.keys() - vi.keys())
    for key, value in flatten(i18n.translations["zh-CN"]).items():
        if key in vi:
            errors.extend(shape_errors(value, vi[key], f"backend:{key}"))

    frontend = {
        tag: flatten(
            json.loads(
                (ROOT / f"webapp/src/i18n/{tag}.json").read_text(encoding="utf-8")
            )
        )
        for tag in ("vi", "en", "zh-CN")
    }
    for tag in ("en", "zh-CN"):
        if frontend[tag].keys() != frontend["vi"].keys():
            errors.append(f"frontend {tag}/vi keys differ")
        for key, value in frontend[tag].items():
            if key in frontend["vi"]:
                errors.extend(
                    shape_errors(value, frontend["vi"][key], f"frontend:{key}")
                )

    retained = []
    for path in (ROOT / "waku").rglob("*.py"):
        name = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = set()
        imports = set()
        agent_imports = set()
        api_imports = {}
        for node in ast.walk(tree):
            if (
                isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                )
                and node.body
            ):
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(
                    first.value, ast.Constant
                ):
                    docstrings.add(id(first.value))
            if isinstance(node, ast.ImportFrom) and node.module == "waku.i18n":
                imports.update(
                    item.asname or item.name
                    for item in node.names
                    if item.name in {"t", "trl", "get_raw"}
                )
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                "localization"
            ):
                agent_imports.update(
                    item.asname or item.name for item in node.names if item.name == "tr"
                )
            if isinstance(node, ast.ImportFrom) and node.module == "waku.webapp.i18n":
                prefixes = {
                    "api_text": "bot.hardcoded.api_reasons.",
                    "validation_text": "bot.hardcoded.api_validation.",
                }
                api_imports.update(
                    {
                        item.asname or item.name: prefixes[item.name]
                        for item in node.names
                        if item.name in prefixes
                    }
                )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args:
                function = node.func
                prefix = ""
                matched = (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "i18n"
                    and function.attr in {"t", "trl", "get_raw"}
                )
                if isinstance(function, ast.Name):
                    matched = (
                        function.id in imports
                        or function.id in agent_imports
                        or function.id in api_imports
                    )
                    if function.id in agent_imports:
                        prefix = "bot.agent_i18n."
                    if function.id in api_imports:
                        prefix = api_imports[function.id]
                key = node.args[0]
                if (
                    matched
                    and isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                ):
                    if i18n.get_raw(prefix + key.value, locale="vi") is None:
                        errors.append(
                            f"{name}:{node.lineno}: missing {prefix + key.value}"
                        )
            if isinstance(node, ast.Call):
                sink = node.func.attr if isinstance(node.func, ast.Attribute) else ""
                methods = {
                    "reply_text",
                    "reply",
                    "answer",
                    "send_message",
                    "reply_photo",
                    "send_photo",
                    "edit_text",
                    "edit_message_text",
                }
                if sink in methods:
                    expressions = [
                        item.value
                        for item in node.keywords
                        if item.arg in {"text", "caption"}
                    ]
                    if node.args and sink in {
                        "reply_text",
                        "reply",
                        "answer",
                        "edit_text",
                    }:
                        expressions.append(node.args[0])
                    for expression in expressions:
                        literal = ""
                        if isinstance(expression, ast.Constant) and isinstance(
                            expression.value, str
                        ):
                            literal = expression.value
                        elif isinstance(expression, ast.JoinedStr):
                            literal = "".join(
                                item.value
                                for item in expression.values
                                if isinstance(item, ast.Constant)
                                and isinstance(item.value, str)
                            )
                        if re.search(r"[A-Za-z]{3}\s+[A-Za-z]{3}", literal):
                            errors.append(
                                f"{name}:{node.lineno}: hardcoded English message prose"
                            )
            if (
                not isinstance(node, ast.Constant)
                or not isinstance(node.value, str)
                or id(node) in docstrings
                or not HAN.search(node.value)
            ):
                continue
            if node.value in LEGACY_TOKENS.get(name, set()):
                retained.append((name, node.lineno, "legacy parser/asset token"))
            elif name == "waku/database/affection.py" and not HAN.search(
                re.sub(r"--[^\n]*", "", node.value)
            ):
                retained.append((name, node.lineno, "SQL comments"))
            else:
                errors.append(
                    f"{name}:{node.lineno}: unclassified Chinese runtime literal"
                )

    return errors, len(vi), len(frontend["vi"]), retained


def main():
    errors, backend_count, frontend_count, retained = validate()
    print(
        f"Vietnamese catalogue: {backend_count} backend keys, {frontend_count} frontend keys"
    )
    for name, line, reason in retained:
        print(f"Retained: {name}:{line} ({reason})")
    for error in errors:
        print(f"ERROR: {error}")
    print(f"i18n validation: {len(errors)} error(s)")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
