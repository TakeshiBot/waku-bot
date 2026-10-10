"""Restart the current interpreter after the normal application shutdown."""

import os
import signal
import sys

_requested = False


def request_restart() -> None:
    global _requested
    if _requested:
        return
    _requested = True
    try:
        os.kill(os.getpid(), signal.SIGINT)
    except BaseException:
        _requested = False
        raise


def replace_process_if_requested() -> None:
    if _requested:
        # Same working directory, environment and interpreter; no parallel bot
        # instance or dependency on Docker/systemd/our local WSL launcher.
        os.execv(sys.executable, [sys.executable, "-m", "waku"])
