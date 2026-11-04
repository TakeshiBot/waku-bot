"""Commit metadata for local checkouts and Docker images, without bot imports."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from waku.timezone import BOT_TIMEZONE


@lru_cache(maxsize=1)
def runtime_info() -> dict[str, str]:
    """Build identity for status messages; Docker supplies these at build time."""
    values = {"version": os.getenv("WAKU_VERSION", "").strip()}
    commands = {"version": ["git", "describe", "--tags", "--always", "--dirty"]}
    for key, command in commands.items():
        if values[key]:
            continue
        try:
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=2, check=True)
            values[key] = result.stdout.strip()[:160]
        except (OSError, subprocess.SubprocessError):
            values[key] = ""
    return values


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


def _update_source() -> tuple[str, str]:
    repository = os.getenv("WAKU_REPOSITORY", "").strip()
    ref = os.getenv("WAKU_GIT_REF", "").strip()
    if repository and ref:
        return repository, ref

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=2, check=True,
        ).stdout.strip()

    head = git("symbolic-ref", "--quiet", "HEAD")
    remote, ref = git(
        "for-each-ref", "--format=%(upstream:remotename)%0a%(upstream:remoteref)", head
    ).splitlines()
    url = git("remote", "get-url", remote)
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return (match[1] if match else ""), ref.removeprefix("refs/heads/")


def check_updates() -> dict:
    """Read upstream status without fetching, pulling or changing the checkout."""
    current = commit_info()["commit"]
    result = {"status": "unknown", "current": current, "latest": "", "count": 0}
    try:
        repository, ref = _update_source()
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not current or not ref:
            return result
        def read(path):
            request = Request(
                f"https://api.github.com/repos/{repository}/{path}",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "Waku-Bot"},
            )
            with urlopen(request, timeout=6) as response:
                return json.load(response)

        latest = read(f"commits/{quote(ref, safe='')}")["sha"]
        if not re.fullmatch(r"[0-9a-fA-F]{40}", latest):
            return result
        if latest.startswith(current):
            return {**result, "status": "identical", "latest": latest[:12]}
        data = read(f"compare/{current}...{latest}?per_page=1")
        status = data["status"]
        if status in {"ahead", "identical", "behind", "diverged"}:
            result.update(status=status, latest=latest[:12], count=int(data["ahead_by"]))
    except (OSError, URLError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        pass
    return result
