"""Restart preserves one process and only follows an explicit request."""

import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from waku.services import process_restart


def test_request_restart_is_idempotent_and_resets_if_signal_fails(monkeypatch):
    monkeypatch.setattr(process_restart, "_requested", False)
    kill = Mock(side_effect=OSError("signal failed"))
    monkeypatch.setattr(process_restart.os, "kill", kill)
    with pytest.raises(OSError):
        process_restart.request_restart()
    assert not process_restart._requested
    kill.side_effect = None
    process_restart.request_restart()
    process_restart.request_restart()
    assert kill.call_count == 2
    kill.assert_called_with(os.getpid(), signal.SIGINT)


def test_normal_shutdown_does_not_restart(monkeypatch):
    monkeypatch.setattr(process_restart, "_requested", False)
    replace = Mock()
    monkeypatch.setattr(process_restart.os, "execv", replace)
    process_restart.replace_process_if_requested()
    replace.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="Bot deployment runs under WSL/Linux")
def test_requested_restart_executes_same_interpreter_pid_directory_and_environment(
    tmp_path,
):
    package = tmp_path / "waku"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "assert str(os.getpid()) == os.environ['RESTART_TEST_PID']\n"
        "assert sys.executable == os.environ['RESTART_TEST_PYTHON']\n"
        "assert str(Path.cwd()) == os.environ['RESTART_TEST_DIRECTORY']\n"
        "assert os.environ['RESTART_TEST_SIGNAL'] == 'received'\n"
        "assert Path('shutdown-complete').read_text() == 'closed'\n"
        "print('restarted-after-shutdown')\n",
        encoding="utf-8",
    )
    helper = Path(process_restart.__file__).resolve()
    script = (
        "import importlib.util, os, signal, sys\n"
        f"spec = importlib.util.spec_from_file_location('restart', {str(helper)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "os.environ['RESTART_TEST_PID'] = str(os.getpid())\n"
        "os.environ['RESTART_TEST_PYTHON'] = sys.executable\n"
        "os.environ['RESTART_TEST_DIRECTORY'] = os.getcwd()\n"
        "signal.signal(signal.SIGINT, lambda *args: os.environ.update(RESTART_TEST_SIGNAL='received'))\n"
        "module.request_restart()\n"
        "try:\n"
        "    open('shutdown-complete', 'w').write('closed')\n"
        "finally:\n"
        "    module.replace_process_if_requested()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "restarted-after-shutdown"
