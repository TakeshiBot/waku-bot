"""Host-only deployment bridge. Runs only the fixed `bash run.sh update` action.

No bot token, Docker socket mount in the bot, or user-supplied shell commands.
The detached worker survives replacement of the bot container. Standard library
only, so the host needs Python 3 rather than the bot's virtual environment.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def write_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(value).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def read_json(path):
    try:
        with path.open("rb") as stream:
            value = json.loads(stream.read(64 * 1024))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def heartbeat(directory):
    write_json(directory / "worker.json", {"time": time.time(), "pid": os.getpid()})


def serve(root):
    directory = root / "data/maintenance"
    with (directory / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        # If this worker died while updating, do not replay a deployment.
        previous = read_json(directory / "result.json")
        if previous.get("status") == "deploying":
            write_json(directory / "result.json", {**previous, "status": "failed"})
        while True:
            heartbeat(directory)
            request = read_json(directory / "request.json")
            result = read_json(directory / "result.json")
            delivered = read_json(directory / "delivered.json")
            identifier = request.get("id", "")
            if (
                request.get("action") != "update"
                or not isinstance(identifier, str)
                or not re.fullmatch(r"[0-9a-f]{32}", identifier)
                or result.get("id") == identifier
                or delivered.get("id") == identifier
            ):
                time.sleep(2)
                continue
            created = request.get("created_at")
            if (
                not isinstance(created, (int, float))
                or not 0 <= time.time() - created < 3600
            ):
                # A restored backup must not replay a historical deployment.
                write_json(
                    directory / "result.json", {"id": identifier, "status": "failed"}
                )
                continue
            write_json(
                directory / "result.json", {"id": identifier, "status": "deploying"}
            )
            try:
                with (root / "logs/maintenance-update.log").open(
                    "ab", buffering=0
                ) as output:
                    output.write(("\nMaintenance update " + identifier + "\n").encode())
                    process = subprocess.Popen(
                        ["bash", str(root / "run.sh"), "update"],
                        cwd=root,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                    )
                    while process.poll() is None:
                        heartbeat(directory)
                        time.sleep(2)
                    code = process.returncode
                write_json(
                    directory / "result.json",
                    {
                        "id": identifier,
                        "status": "success" if code == 0 else "failed",
                        "exit_code": code,
                    },
                )
            except OSError:
                write_json(
                    directory / "result.json", {"id": identifier, "status": "failed"}
                )


def ensure(root):
    directory = root / "data/maintenance"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = read_json(directory / "worker.json")
    if 0 <= time.time() - current.get("time", 0) < 15:
        return
    with (root / "logs/maintenance-worker.log").open("ab", buffering=0) as output:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve", str(root)],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    for _ in range(30):
        if 0 <= time.time() - read_json(directory / "worker.json").get("time", 0) < 15:
            return
        time.sleep(0.1)
    raise SystemExit(
        "Could not start the host deployment worker; see logs/maintenance-worker.log"
    )


if __name__ == "__main__":
    action, location = sys.argv[1:]
    project = Path(location).resolve(strict=True)
    if not (project / "run.sh").is_file():
        raise SystemExit("Invalid project directory")
    if action == "ensure":
        ensure(project)
    elif action == "serve":
        serve(project)
    else:
        raise SystemExit("Invalid worker action")
