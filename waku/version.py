"""Commit metadata for local checkouts and Docker images, without bot imports."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from waku.timezone import BOT_TIMEZONE


@lru_cache(maxsize=1)
def _git_commit() -> tuple[str, str, str]:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H%n%s%n%cI"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=True,
        )
        commit, subject, date = result.stdout.strip().split("\n", 2)
        return commit, subject[:160], date
    except (OSError, subprocess.SubprocessError, ValueError):
        return "", "", ""


def commit_info() -> dict[str, str]:
    """Prefer the image's build commit; never show a VPS host checkout as its build."""
    commit = os.getenv("WAKU_COMMIT", "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        subject = os.getenv("WAKU_COMMIT_SUBJECT", "")[:160]
        date = os.getenv("WAKU_COMMIT_DATE", "")
    else:
        commit, subject, date = _git_commit()
    try:
        date = datetime.fromisoformat(date).astimezone(BOT_TIMEZONE).strftime(
            "%H:%M %d/%m/%Y"
        )
    except ValueError:
        date = ""
    return {"commit": commit[:12], "subject": subject, "date": date}
