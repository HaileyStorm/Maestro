"""Stop this Pinokio app's server and wait for its old local listener to close."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse


APP_ROOT = Path(__file__).resolve().parents[2]
STOP_TIMEOUT_SECONDS = 45


def _pterm(binary: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [binary, *args],
        cwd=APP_ROOT,
        capture_output=True,
        text=True,
        timeout=6,
        check=True,
    )


def _status(binary: str) -> dict:
    payload = json.loads(_pterm(binary, "status", APP_ROOT.name).stdout)
    if not isinstance(payload, dict) or not isinstance(payload.get("path"), str):
        raise ValueError("Pinokio status is invalid")
    if Path(payload["path"]).resolve() != APP_ROOT:
        raise ValueError("Pinokio selected a different app")
    if not isinstance(payload.get("running_scripts"), list):
        raise ValueError("Pinokio script status is invalid")
    return payload


def _old_listener(status: dict) -> tuple[str, int] | None:
    url = status.get("ready_url")
    if not isinstance(url, str) or not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Pinokio reported a non-local server")
    if not parsed.port:
        raise ValueError("Pinokio reported a server without a port")
    return parsed.hostname, parsed.port


def _listener_closed(listener: tuple[str, int]) -> bool:
    try:
        with socket.create_connection(listener, timeout=0.4):
            return False
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def stop_and_wait(binary: str, *, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
    before = _status(binary)
    running = before["running_scripts"]
    if "start.js" not in running:
        if before.get("ready_url"):
            raise ValueError("Pinokio reports an unowned active server")
        return
    listener = _old_listener(before)
    if listener is None:
        raise ValueError("The running server has no verifiable local listener")
    _pterm(binary, "stop", "start.js")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            after = _status(binary)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            time.sleep(0.5)
            continue
        if "start.js" not in after["running_scripts"] and _listener_closed(listener):
            return
        time.sleep(0.5)
    raise TimeoutError("The old Maestro server did not stop")


def main() -> int:
    binary = shutil.which("pterm")
    if not binary:
        print("MAESTRO_OLD_BACKEND_STOP_FAILED: Pinokio CLI unavailable", flush=True)
        return 1
    try:
        stop_and_wait(binary)
    except (OSError, subprocess.SubprocessError, ValueError, TimeoutError) as error:
        print(f"MAESTRO_OLD_BACKEND_STOP_FAILED: {type(error).__name__}", flush=True)
        return 1
    print("MAESTRO_OLD_BACKEND_STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
