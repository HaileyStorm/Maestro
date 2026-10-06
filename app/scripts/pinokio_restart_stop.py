"""Stop this Pinokio app and verify its published or starting backend drained."""

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
    if url is None or url == "":
        return None
    if not isinstance(url, str):
        raise ValueError("Pinokio server URL is invalid")
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


def _starting_backend_drained() -> bool:
    """A missing ready URL is not proof that a failed Start's Python exited.

    Inspect all same-user Python processes, including a backend that appeared
    during Stop. Never signal a PID; only Pinokio stops its owned script.
    """
    try:
        import psutil
    except ImportError:
        raise ValueError("Backend process inspection is unavailable") from None
    try:
        owner = psutil.Process().username()
        for process in psutil.process_iter():
            try:
                name = process.name().casefold()
                if not name.startswith(("python", "pypy")):
                    continue
                if process.username() != owner:
                    continue
                argv = process.cmdline()
                if not argv:
                    raise ValueError("Python process command is unavailable")
                candidates = [Path(arg) for arg in argv[1:]
                              if Path(arg).name in {"launch.py", "wgp.py"}]
                if not candidates:
                    continue
                cwd = Path(process.cwd())
                if any((cwd / script).resolve() in {
                    APP_ROOT / "app" / "launch.py", APP_ROOT / "app" / "wgp.py"
                } for script in candidates):
                    return False
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
    except (psutil.Error, OSError):
        raise ValueError("Backend process inspection is incomplete") from None
    return True


def stop_and_wait(binary: str, *, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
    before = _status(binary)
    running = before["running_scripts"]
    if "start.js" not in running:
        if before.get("ready_url"):
            raise ValueError("Pinokio reports an unowned active server")
        if not _starting_backend_drained():
            raise ValueError("A backend remains outside the running Start script")
        return
    listener = _old_listener(before)
    _pterm(binary, "stop", "start.js")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            after = _status(binary)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            time.sleep(0.5)
            continue
        if "start.js" not in after["running_scripts"]:
            if listener is not None:
                if _listener_closed(listener):
                    return
            elif not after.get("ready_url") and _starting_backend_drained():
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
