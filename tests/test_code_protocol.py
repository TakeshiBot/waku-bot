"""Old agent history must still resolve the renamed codebase."""

import ast
from pathlib import Path

import pytest


@pytest.fixture
def split_target():
    source = (
        Path(__file__).resolve().parents[1] / "waku/plugins/agent/tools/io/protocols.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_split_target"
        or isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_PROTOCOLS"
            for target in node.targets
        )
    ]
    namespace = {"tr": lambda key, **kwargs: key}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace
    )
    return namespace["_split_target"]


@pytest.mark.parametrize(
    "target",
    [
        "waku://waku/plugins/agent/output.py",
        "waku:///waku/plugins/agent/output.py",
        "kmua://kmua/plugins/agent/output.py",
        "kmua:///kmua/plugins/agent/output.py",
        "waku://kmua/plugins/agent/output.py",
    ],
)
def test_codebase_references_use_current_root(split_target, target):
    assert split_target(target) == ("waku://", "/waku/plugins/agent/output.py")


def test_other_paths_are_not_rebranded(split_target):
    assert split_target("work://kmua/file.txt") == ("work://", "/kmua/file.txt")
    assert split_target("waku://kmua-extra/file.txt") == (
        "waku://",
        "/kmua-extra/file.txt",
    )
    assert split_target("kmua://") == ("waku://", "/")
    assert split_target("https://example.com/kmua") == (
        "http",
        "https://example.com/kmua",
    )
    with pytest.raises(ValueError):
        split_target("unknown://file.txt")
