"""Exercise deployment failures with isolated files and a fake Docker CLI."""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    for name in ("run.sh", "docker-compose.yml", "settings.ex.toml"):
        shutil.copy2(ROOT / name, root / name)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "docker-calls.jsonl"
    state = tmp_path / "container-state"
    state.write_text("running")
    docker = binaries / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DOCKER_CALLS'], 'a') as handle:\n"
        "    handle.write(json.dumps(args) + '\\n')\n"
        "state = Path(os.environ['DOCKER_STATE'])\n"
        "if args[0] == 'info':\n"
        "    if '--format' in args: print(os.environ['DOCKER_ROOT'])\n"
        "elif args[0] == 'compose' and args[1] != 'version':\n"
        "    args = args[7:]\n"
        "    action = args[0]\n"
        "    if action == 'up' and '--help' in args:\n"
        "        print('--wait --wait-timeout')\n"
        "    elif action == 'build' and os.environ.get('FAIL_BUILD'):\n"
        "        sys.exit(12)\n"
        "    elif action == 'run' and os.environ.get('FAIL_CONFIG'):\n"
        "        sys.exit(13)\n"
        "    elif action == 'ps':\n"
        "        if state.read_text() == 'running' or '-a' in args:\n"
        "            print('fake-container')\n"
        "    elif action == 'stop': state.write_text('stopped')\n"
        "    elif action in ('start', 'up'): state.write_text('running')\n"
    )
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "DOCKER_CALLS": str(calls),
        "DOCKER_STATE": str(state),
        "DOCKER_ROOT": str(tmp_path),
        "MIN_FREE_GB": "0",
    }

    def run(*args, **overrides):
        result = subprocess.run(
            ["bash", str(root / "run.sh"), *args],
            cwd=tmp_path,  # The script must resolve its own project directory.
            env={**env, **overrides},
            input="",
            capture_output=True,
            text=True,
            timeout=15,
        )
        history = (
            [json.loads(line) for line in calls.read_text().splitlines()]
            if calls.exists()
            else []
        )
        return result, history

    return root, binaries, state, run


def actions(history):
    return [call[7] for call in history if call[0] == "compose" and len(call) > 7]


def test_init_migrates_private_settings_preserving_values_and_root_link(deployment):
    root, _, _, run = deployment
    original = '# Private comment\ntoken = "123:test-key"\nowners = [42]\n'
    (root / "settings.toml").write_text(original)
    (root / "settings.dev.toml").write_text('nickname = "custom"\n')
    result, history = run("init")
    assert result.returncode == 0, result.stderr
    assert not history
    assert (root / "settings.toml").is_symlink()
    assert (root / "settings.toml").read_text() == original
    assert (root / "settings.dev.toml").is_symlink()
    assert (root / "config/settings.toml").stat().st_mode & 0o777 == 0o600
    assert list((root / ".backups").glob("settings-init-*.toml"))
    assert run("init")[0].returncode == 0  # Idempotent, no duplicate migration.


def test_init_refuses_two_independent_configs_without_overwriting(deployment):
    root, _, _, run = deployment
    (root / "config").mkdir()
    (root / "config/settings.toml").write_text("target")
    (root / "settings.toml").write_text("original")
    result, _ = run("init")
    assert result.returncode != 0
    assert (root / "settings.toml").read_text() == "original"
    assert (root / "config/settings.toml").read_text() == "target"


@pytest.mark.parametrize("failure", ["FAIL_BUILD", "FAIL_CONFIG"])
def test_failed_build_or_validation_never_stops_existing_bot(deployment, failure):
    _, _, state, run = deployment
    result, history = run("deploy", **{failure: "1"})
    assert result.returncode != 0
    assert state.read_text() == "running"
    assert "stop" not in actions(history)
    assert all("--help" in call for call in history if len(call) > 7 and call[7] == "up")


def test_deploy_validates_before_stop_then_backs_up_and_waits(deployment):
    root, _, state, run = deployment
    (root / "data").mkdir()
    (root / "data/sentinel").write_text("existing runtime data")
    result, history = run("--deploy")
    assert result.returncode == 0, result.stderr
    order = actions(history)
    assert order.index("build") < order.index("run") < order.index("stop")
    up = [call for call in history if len(call) > 7 and call[7] == "up" and "--help" not in call]
    assert len(up) == 1 and "--wait" in up[0] and "--no-build" in up[0]
    assert not any("prune" in call or "down" in call for call in history)
    assert state.read_text() == "running"
    with tarfile.open(next((root / ".backups").glob("*.tar.gz"))) as archive:
        assert archive.extractfile("data/sentinel").read() == b"existing runtime data"
        assert tomllib.loads(archive.extractfile("config/settings.toml").read().decode())["lang"] == "vi"


def test_manual_backup_resumes_existing_bot(deployment):
    root, _, state, run = deployment
    result, history = run("backup")
    assert result.returncode == 0, result.stderr
    assert actions(history).index("stop") < actions(history).index("start")
    assert state.read_text() == "running"
    assert list((root / ".backups").glob("*.tar.gz"))


def test_failed_backup_resumes_existing_bot(deployment):
    _, binaries, state, run = deployment
    tar = binaries / "tar"
    tar.write_text("#!/bin/sh\nexit 14\n")
    tar.chmod(0o755)
    result, history = run("deploy")
    assert result.returncode != 0
    assert state.read_text() == "running"
    assert "start" in actions(history)
    assert all("--help" in call for call in history if len(call) > 7 and call[7] == "up")


def test_update_refuses_dirty_git_without_pull_or_docker_changes(deployment):
    _, binaries, _, run = deployment
    git = binaries / "git"
    git.write_text('#!/bin/sh\nif [ "$1" = status ]; then echo " M waku/local.py"; fi\n')
    git.chmod(0o755)
    result, history = run("update")
    assert result.returncode != 0
    assert "local" in result.stderr
    assert not history


@pytest.mark.parametrize("port", ["0", "65536", "x", "1;echo bad"])
def test_invalid_port_rejected_before_docker(deployment, port):
    _, _, _, run = deployment
    result, history = run("status", WAKU_PORT=port)
    assert result.returncode != 0
    assert not history


def test_help_and_unknown_action_never_call_docker(deployment):
    _, _, _, run = deployment
    assert run("--help")[0].returncode == 0
    result, history = run("typo")
    assert result.returncode != 0 and not history
