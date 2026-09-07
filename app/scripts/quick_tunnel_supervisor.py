"""Own a direct Cloudflare Quick Tunnel and feed the registration watcher.

Pinokio already owns its own Cloudflare child and should use ``--publish-url``
to adopt that public URL without starting a second tunnel. Direct Windows and
systemd launch paths use the default supervise mode, which starts cloudflared
without a shell, atomically publishes rotations, and owns the watcher child.
"""

from __future__ import annotations

import argparse
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

from register_share_url import (
    _canonical_loopback_origin,
    _canonical_quick_tunnel_url,
)
from share_registration_watch import (
    LeaseUnavailableError,
    RegistrationLease,
    default_quick_url_file,
    secure_clear_runtime_text,
    secure_publish_runtime_text,
)

DEFAULT_TUNNEL_STARTUP_BUDGET_SECONDS = 75.0
DEFAULT_RESTART_BACKOFF_SECONDS = 2.0
_QUICK_URL_PATTERN = re.compile(
    r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com",
    re.IGNORECASE,
)


def _emit(message: str) -> None:
    print(message, flush=True)


def runtime_url_file(origin: str, *, runtime_dir: Path | None = None) -> Path:
    return default_quick_url_file(origin, runtime_dir=runtime_dir)


def clear_quick_url(
    path: Path,
    *,
    before_unlink: Callable[[], None] | None = None,
) -> bool:
    """Remove only the exact validated runtime file and durably publish absence."""

    return secure_clear_runtime_text(path, before_unlink=before_unlink)


def publish_quick_url(
    path: Path,
    quick_url: str,
    *,
    before_replace: Callable[[], None] | None = None,
) -> str:
    """Atomically publish a canonical public URL in an owner-only file."""

    canonical = _canonical_quick_tunnel_url(quick_url)
    secure_publish_runtime_text(
        path,
        canonical + "\n",
        before_replace=before_replace,
    )
    return canonical


def _reader(stream: IO[str], output: queue.Queue[str | None]) -> None:
    try:
        for line in stream:
            output.put(line)
    finally:
        output.put(None)


def _terminate_and_reap(process: subprocess.Popen, timeout: float = 10.0) -> None:
    if process.poll() is not None:
        process.wait()
        return
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return


class QuickTunnelSupervisor:
    def __init__(
        self,
        *,
        origin: str,
        url_file: Path,
        cloudflared: str,
        watcher_script: Path,
        backend_pid_file: Path | None = None,
        startup_budget_seconds: float = DEFAULT_TUNNEL_STARTUP_BUDGET_SECONDS,
        restart_backoff_seconds: float = DEFAULT_RESTART_BACKOFF_SECONDS,
        start_watcher: bool = True,
        max_tunnel_starts: int | None = None,
    ) -> None:
        self.origin = _canonical_loopback_origin(origin)
        self.url_file = url_file
        self.cloudflared = cloudflared
        self.watcher_script = watcher_script
        self.backend_pid_file = backend_pid_file
        self.startup_budget_seconds = max(5.0, startup_budget_seconds)
        self.restart_backoff_seconds = max(0.1, restart_backoff_seconds)
        self.start_watcher = start_watcher
        self.max_tunnel_starts = max_tunnel_starts
        self.stop_event = threading.Event()
        self.tunnel: subprocess.Popen[str] | None = None
        self.watcher: subprocess.Popen[str] | None = None
        self.last_url = ""
        self.next_watcher_start = 0.0

    def request_stop(self, *_args) -> None:
        self.stop_event.set()

    def _start_tunnel(self) -> tuple[subprocess.Popen[str], queue.Queue[str | None]]:
        child_environment = dict(os.environ)
        child_environment.pop("PINOKIO_STABLE_SHARE_UPDATE_SECRET", None)
        child_environment.pop("CLOUDFLARE_API_TOKEN", None)
        process = subprocess.Popen(
            [
                self.cloudflared,
                "tunnel",
                "--no-autoupdate",
                "--url",
                self.origin,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
            env=child_environment,
        )
        if process.stdout is None:
            _terminate_and_reap(process)
            raise RuntimeError("cloudflared_output_unavailable")
        lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(
            target=_reader, args=(process.stdout, lines), daemon=True,
        ).start()
        return process, lines

    def _ensure_watcher(self) -> None:
        if not self.start_watcher:
            return
        if self.watcher is not None and self.watcher.poll() is None:
            return
        if self.watcher is not None:
            retry_delay = 15.0 if self.watcher.returncode == 0 else 2.0
            self.next_watcher_start = max(
                self.next_watcher_start, time.monotonic() + retry_delay,
            )
            self.watcher = None
        if time.monotonic() < self.next_watcher_start:
            return
        command = [
            sys.executable,
            str(self.watcher_script),
            "--origin",
            self.origin,
            "--quick-url-file",
            str(self.url_file),
            "--runtime-dir",
            str(self.url_file.parent),
        ]
        if self.backend_pid_file is not None:
            command.extend(("--backend-pid-file", str(self.backend_pid_file)))
        self.watcher = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            shell=False,
        )
        self.next_watcher_start = 0.0

    def _consume_tunnel(
        self, process: subprocess.Popen[str], lines: queue.Queue[str | None],
    ) -> None:
        deadline = time.monotonic() + self.startup_budget_seconds
        captured = False
        while not self.stop_event.is_set():
            if not captured and time.monotonic() >= deadline:
                _emit("MAESTRO_QUICK_TUNNEL_WAIT startup_timeout")
                _terminate_and_reap(process)
                return
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                if process.poll() is not None:
                    return
                self._ensure_watcher() if captured else None
                continue
            if line is None:
                return
            match = _QUICK_URL_PATTERN.search(line)
            if match is None:
                continue
            try:
                quick_url = publish_quick_url(self.url_file, match.group(0))
            except (OSError, ValueError):
                _emit("MAESTRO_QUICK_TUNNEL_WAIT publish_failed")
                continue
            if quick_url != self.last_url:
                event = "READY" if not self.last_url else "ROTATED"
                _emit(f"MAESTRO_QUICK_TUNNEL_{event} {quick_url}")
                self.last_url = quick_url
            captured = True
            self._ensure_watcher()

    def run(self) -> int:
        starts = 0
        try:
            while not self.stop_event.is_set():
                if self.max_tunnel_starts is not None and starts >= self.max_tunnel_starts:
                    return 0
                starts += 1
                _emit("MAESTRO_QUICK_TUNNEL_STARTING")
                try:
                    process, lines = self._start_tunnel()
                except (OSError, RuntimeError):
                    _emit("MAESTRO_QUICK_TUNNEL_FAILED launch_failed")
                    return 2
                self.tunnel = process
                self._consume_tunnel(process, lines)
                _terminate_and_reap(process)
                self.tunnel = None
                if self.stop_event.is_set():
                    break
                _emit("MAESTRO_QUICK_TUNNEL_RESTARTING")
                self.stop_event.wait(self.restart_backoff_seconds)
            return 0
        finally:
            if self.tunnel is not None:
                _terminate_and_reap(self.tunnel)
                self.tunnel = None
            if self.watcher is not None:
                _terminate_and_reap(self.watcher)
                self.watcher = None
            _emit("MAESTRO_QUICK_TUNNEL_STOPPED")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Own or adopt a Quick Tunnel and publish rotations",
        epilog=(
            "Default mode is suitable for systemd ExecStart and portable Windows. "
            "Pinokio must use --publish-url because Pinokio owns its tunnel child."
        ),
    )
    parser.add_argument("--origin", default=os.environ.get("MAESTRO_LOCAL_ORIGIN", ""))
    parser.add_argument("--url-file", type=Path, default=None)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--publish-url", default="")
    action.add_argument("--clear-url", action="store_true")
    parser.add_argument("--backend-pid-file", type=Path, default=None)
    parser.add_argument(
        "--cloudflared",
        default=os.environ.get("MAESTRO_CLOUDFLARED_PATH", ""),
    )
    parser.add_argument(
        "--startup-budget-seconds",
        type=float,
        default=DEFAULT_TUNNEL_STARTUP_BUDGET_SECONDS,
    )
    parser.add_argument("--no-watcher", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-tunnel-starts", type=int, default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    try:
        origin = _canonical_loopback_origin(options.origin)
        target_file = options.url_file or runtime_url_file(origin)
        if options.clear_url:
            clear_quick_url(target_file)
            _emit("MAESTRO_QUICK_TUNNEL_CLEARED")
            return 0
        if options.publish_url:
            quick_url = publish_quick_url(target_file, options.publish_url)
            _emit(f"MAESTRO_QUICK_TUNNEL_READY {quick_url}")
            return 0
        cloudflared = options.cloudflared or shutil.which("cloudflared") or ""
        if not cloudflared:
            _emit("MAESTRO_QUICK_TUNNEL_FAILED cloudflared_missing")
            return 2
        try:
            with RegistrationLease(
                origin,
                runtime_dir=target_file.parent,
                namespace="tunnel-supervisor",
            ):
                supervisor = QuickTunnelSupervisor(
                    origin=origin,
                    url_file=target_file,
                    cloudflared=cloudflared,
                    watcher_script=Path(__file__).with_name("share_registration_watch.py"),
                    backend_pid_file=options.backend_pid_file,
                    startup_budget_seconds=options.startup_budget_seconds,
                    start_watcher=not options.no_watcher,
                    max_tunnel_starts=options.max_tunnel_starts,
                )
                previous_handlers: dict[int, object] = {}
                for signal_name in ("SIGINT", "SIGTERM"):
                    selected = getattr(signal, signal_name, None)
                    if selected is None:
                        continue
                    previous_handlers[selected] = signal.getsignal(selected)
                    signal.signal(selected, supervisor.request_stop)
                try:
                    return supervisor.run()
                finally:
                    for selected, handler in previous_handlers.items():
                        signal.signal(selected, handler)
        except LeaseUnavailableError:
            _emit("MAESTRO_QUICK_TUNNEL_ALREADY_RUNNING")
            return 0
    except (OSError, ValueError):
        _emit("MAESTRO_QUICK_TUNNEL_FAILED invalid_configuration")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
