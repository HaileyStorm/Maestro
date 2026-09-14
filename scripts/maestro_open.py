#!/usr/bin/env python3
"""Start Maestro through Pinokio and open its freshly verified local UI.

The helper is intentionally a thin local control-plane client.  It asks
Pinokio for the current app state, starts the existing user service only when
that app is not already running, and opens a URL only after direct loopback
``/health`` and ``/ready`` checks succeed.  It never starts a model, submits a
job, writes application state, or reuses a remembered port.

Examples::

    python scripts/maestro_open.py
    python scripts/maestro_open.py --no-open
    python scripts/maestro_open.py --status

The installed desktop shortcut may invoke this file through the app's managed
Python environment.  ``--pterm`` is available for installations whose pterm
binary is not on ``PATH``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request
import urllib.error
import urllib.request
import webbrowser


REPO_ROOT = Path(__file__).resolve().parents[1]
# Cold starts can spend several minutes importing the selected WanGP runtime.
# Keep the wait bounded while allowing the managed service to finish its first
# import; callers can shorten this with --timeout for diagnostics.
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_SECONDS = 2.0
CONTROL_PLANE_TIMEOUT_SECONDS = 60.0
PROGRESS_INTERVAL_SECONDS = 15.0
PTERM_COMMAND_TIMEOUT_SECONDS = 15.0
SYSTEMCTL_COMMAND_TIMEOUT_SECONDS = 20.0
HTTP_PROBE_TIMEOUT_SECONDS = 5.0
SERVICE_NAME = "maestro-continuum.service"
APP_QUERY = "Maestro Continuum"
DIRECT_APP_REF = "pinokio://127.0.0.1:42000/api/Maestro.git"


class MaestroOpenError(RuntimeError):
    """A user-actionable helper failure."""


class CommandFailureError(MaestroOpenError):
    """An argument-vector command returned a non-zero status."""

    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


class CommandTimeoutError(MaestroOpenError):
    """An argument-vector command exceeded its bounded timeout."""


class ControlPlaneUnavailableError(MaestroOpenError):
    """The Pinokio control plane could not be contacted yet."""


class ForeignAppError(MaestroOpenError):
    """pterm resolved a different checkout than this helper belongs to."""


@dataclass(frozen=True)
class AppStatus:
    ref: str
    payload: Mapping[str, Any]


def _json_payload(raw: str, *, command: str) -> Mapping[str, Any]:
    """Decode pterm's JSON, tolerating harmless leading terminal chatter."""

    text = (raw or "").lstrip("\ufeff \t\r\n")
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(text)
    except json.JSONDecodeError:
        value = None
        for index, character in enumerate(text):
            if character not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
                break
            except json.JSONDecodeError:
                continue
        if value is None:
            sample = " ".join(text.split())[:240]
            raise MaestroOpenError(
                f"pterm {command} returned invalid JSON"
                + (f": {sample}" if sample else ".")
            )
    if not isinstance(value, Mapping):
        raise MaestroOpenError(f"pterm {command} returned an unexpected JSON value.")
    return value


def _command_error(argv: Sequence[str], completed: Any) -> MaestroOpenError:
    rendered = " ".join(str(part) for part in argv)
    detail = " ".join(str(getattr(completed, "stderr", "") or "").split())
    if detail:
        detail = f": {detail[:300]}"
    return CommandFailureError(
        f"Command failed ({getattr(completed, 'returncode', '?')}): {rendered}{detail}",
        stderr=str(getattr(completed, "stderr", "") or ""),
    )


def _run_command(
    argv: Sequence[str],
    *,
    timeout: float,
    runner: Callable[..., Any] | None = None,
) -> Any:
    """Run an argument-vector command without invoking a shell."""

    runner = runner or subprocess.run
    try:
        completed = runner(
            [str(part) for part in argv],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise MaestroOpenError(f"Command is unavailable: {argv[0]}") from error
    except subprocess.TimeoutExpired as error:
        rendered = " ".join(str(part) for part in argv)
        raise CommandTimeoutError(
            f"Command timed out after {timeout:g}s: {rendered}"
        ) from error
    except OSError as error:
        raise MaestroOpenError(f"Could not run {argv[0]}: {error}") from error
    if getattr(completed, "returncode", 1) != 0:
        raise _command_error(argv, completed)
    return completed


def _candidate_executables(home: Path) -> Iterable[Path]:
    base = home / "bin" / "npm" / "bin" / "pterm"
    yield base
    # The suffixes make the resolver useful on a copied Pinokio installation
    # without changing the Linux systemd path used by the normal helper.
    yield base.with_suffix(".cmd")
    yield base.with_suffix(".exe")


def resolve_pterm(
    explicit: str | os.PathLike[str] | None = None,
    *,
    which: Callable[[str], str | None] | None = None,
    config_path: Path | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve pterm from an explicit path, PATH, or Pinokio's config home."""

    if explicit is not None:
        candidate = Path(explicit).expanduser()
        if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return candidate.resolve()
        raise MaestroOpenError(
            f"pterm is not executable at {candidate}. "
            "Pass --pterm with the installed Pinokio pterm path."
        )

    which = which or shutil.which
    from_path = which("pterm")
    if from_path:
        candidate = Path(from_path).expanduser()
        if candidate.is_file():
            return candidate.resolve()

    config_path = config_path or (Path.home() / ".pinokio" / "config.json")
    home = home or Path.home()
    config_error: str | None = None
    configured_home: Path | None = None
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        config_error = f"Pinokio config is missing at {config_path}"
    except (OSError, json.JSONDecodeError) as error:
        config_error = f"Pinokio config at {config_path} is unreadable ({error})"
    else:
        raw_home = config.get("home") if isinstance(config, Mapping) else None
        if isinstance(raw_home, str) and raw_home.strip():
            configured_home = Path(raw_home).expanduser()
            if not configured_home.is_absolute():
                configured_home = (home / configured_home).resolve()
            for candidate in _candidate_executables(configured_home):
                if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
                    return candidate.resolve()
        else:
            config_error = f"Pinokio config at {config_path} has no usable home"

    checked = (
        str(configured_home / "bin" / "npm" / "bin" / "pterm")
        if configured_home
        else "the configured Pinokio home"
    )
    detail = f"; {config_error}" if config_error else ""
    raise MaestroOpenError(
        "pterm was not found on PATH or in Pinokio's configured home"
        f" (checked {checked}{detail}). Pass --pterm with the installed path."
    )


def _status_candidates(payload: Mapping[str, Any]) -> list[str]:
    apps = payload.get("apps")
    if not isinstance(apps, list):
        return [DIRECT_APP_REF]
    refs: list[str] = []
    for app in apps:
        if not isinstance(app, Mapping):
            continue
        ref = app.get("ref")
        app_id = str(app.get("app_id") or app.get("name") or "")
        title = str(app.get("title") or "")
        if (
            isinstance(ref, str)
            and ref
            and (app_id == "Maestro.git" or "maestro" in title.lower())
        ):
            if ref not in refs:
                refs.append(ref)
    if DIRECT_APP_REF not in refs:
        refs.append(DIRECT_APP_REF)
    return refs


def _looks_like_control_plane_failure(error: MaestroOpenError) -> bool:
    """Recognize transport failures without treating bad JSON/path as transient."""

    if isinstance(error, ControlPlaneUnavailableError):
        return True
    # Only the command's actual stderr (or a real timeout exception) is
    # evidence of a temporarily unreachable control plane.  Do not inspect
    # the rendered argv: pterm's normal ``--timeout=5000`` argument would make
    # every terminal status error look transient.
    if isinstance(error, CommandTimeoutError):
        return True
    if not isinstance(error, CommandFailureError):
        return False
    lowered = error.stderr.lower()
    return any(
        token in lowered
        for token in (
            "econnrefused",
            "connection refused",
            "connection reset",
            "could not connect",
            "connect error",
            "fetch failed",
        )
    )


def _query_status(
    pterm: Path,
    ref: str,
    *,
    expected_root: Path,
    runner: Callable[..., Any] | None,
    command_timeout: float = PTERM_COMMAND_TIMEOUT_SECONDS,
) -> AppStatus:
    try:
        completed = _run_command(
            [str(pterm), "status", ref, "--probe", "--timeout=5000"],
            timeout=command_timeout,
            runner=runner,
        )
    except MaestroOpenError as error:
        if _looks_like_control_plane_failure(error):
            raise ControlPlaneUnavailableError(str(error)) from error
        raise
    payload = _json_payload(completed.stdout, command="status")
    raw_path = payload.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ForeignAppError(
            "pterm status did not identify a filesystem path; refusing to start an "
            "unverified app."
        )
    try:
        returned_root = Path(raw_path).expanduser().resolve()
    except OSError as error:
        raise ForeignAppError(f"pterm status returned an invalid app path: {raw_path}") from error
    if returned_root != expected_root.resolve():
        raise ForeignAppError(
            "pterm resolved a different Maestro checkout "
            f"({returned_root}); refusing to start it."
        )
    return AppStatus(ref=ref, payload=payload)


def _resolve_app_status(
    pterm: Path,
    *,
    expected_root: Path,
    runner: Callable[..., Any] | None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AppStatus:
    search_timeout = PTERM_COMMAND_TIMEOUT_SECONDS
    if deadline is not None:
        search_timeout = max(0.0, min(search_timeout, deadline - clock()))
        if search_timeout <= 0:
            raise ControlPlaneUnavailableError("Pinokio status polling deadline reached.")
    try:
        search = _run_command(
            [
                str(pterm),
                "search",
                APP_QUERY,
                "--mode",
                "balanced",
                "--min-match",
                "1",
                "--limit",
                "8",
            ],
            timeout=search_timeout,
            runner=runner,
        )
    except MaestroOpenError as error:
        if _looks_like_control_plane_failure(error):
            raise ControlPlaneUnavailableError(str(error)) from error
        raise
    search_payload = _json_payload(search.stdout, command="search")
    candidates = _status_candidates(search_payload)
    foreign_error: ForeignAppError | None = None
    command_error: MaestroOpenError | None = None
    control_error: ControlPlaneUnavailableError | None = None
    for ref in candidates:
        command_timeout = PTERM_COMMAND_TIMEOUT_SECONDS
        if deadline is not None:
            command_timeout = max(0.0, min(command_timeout, deadline - clock()))
            if command_timeout <= 0:
                break
        try:
            return _query_status(
                pterm,
                ref,
                expected_root=expected_root,
                runner=runner,
                command_timeout=command_timeout,
            )
        except ForeignAppError as error:
            foreign_error = error
        except ControlPlaneUnavailableError as error:
            control_error = error
        except MaestroOpenError as error:
            command_error = error
    if foreign_error is not None:
        raise foreign_error
    if control_error is not None:
        raise control_error
    if command_error is not None:
        raise command_error
    if deadline is not None and clock() >= deadline:
        raise ControlPlaneUnavailableError("Pinokio status polling deadline reached.")
    raise MaestroOpenError("pterm search found no Maestro app reference.")


def _loopback_url(raw_url: Any) -> str | None:
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    raw_url = raw_url.strip()
    try:
        parsed = urlsplit(raw_url)
        hostname = (parsed.hostname or "").lower()
        # Access is deliberately limited to an HTTP loopback origin.  This
        # prevents a stale LAN/Cloudflare URL or embedded credentials from
        # being opened by a convenience command.
        if (
            parsed.scheme.lower() != "http"
            or hostname not in {"127.0.0.1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            return None
        _ = parsed.port  # force malformed-port validation
    except ValueError:
        return None
    return urlunsplit(("http", parsed.netloc, "", "", ""))


def _http_probe(
    url: str,
    *,
    opener: Callable[..., Any] | None = None,
) -> tuple[bool, str]:
    opener = opener or urllib.request.urlopen
    try:
        response = opener(
            Request(url, headers={"Accept": "application/json"}),
            timeout=HTTP_PROBE_TIMEOUT_SECONDS,
        )
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            status = getcode() if callable(getcode) else None
        close = getattr(response, "close", None)
        if callable(close):
            close()
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        return False, str(error) or error.__class__.__name__
    if isinstance(status, int) and 200 <= status < 300:
        return True, f"HTTP {status}"
    return False, f"HTTP {status if status is not None else 'unknown'}"


def _verify_local_ready(
    base_url: str,
    *,
    opener: Callable[..., Any] | None,
) -> tuple[bool, str]:
    for suffix in ("health", "ready"):
        ok, detail = _http_probe(f"{base_url}/{suffix}", opener=opener)
        if not ok:
            return False, f"/{suffix} {detail}"
    return True, "health and ready are HTTP 2xx"


def _state_line(status: AppStatus) -> str:
    payload = status.payload
    state = payload.get("state") or "unknown"
    running = payload.get("running") is True
    ready = payload.get("ready") is True
    return f"Maestro: {state} (running={'yes' if running else 'no'}, ready={'yes' if ready else 'no'})"


def _wait_for_control_plane(
    pterm: Path,
    *,
    expected_root: Path,
    timeout: float,
    runner: Callable[..., Any] | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> AppStatus:
    started = clock()
    last_error: str | None = None
    while True:
        try:
            return _resolve_app_status(
                pterm,
                expected_root=expected_root,
                runner=runner,
                deadline=started + timeout,
                clock=clock,
            )
        except ForeignAppError:
            raise
        except ControlPlaneUnavailableError as error:
            elapsed = clock() - started
            message = str(error)
            if message != last_error:
                print("Waiting for Pinokio control plane…", flush=True)
                last_error = message
            if elapsed >= timeout:
                raise MaestroOpenError(
                    f"Pinokio control plane did not become reachable within {timeout:g}s. "
                    "Inspect it with: journalctl --user -u pinokio -n40 --no-pager"
                ) from error
            remaining = timeout - (clock() - started)
            sleep(max(0.0, min(DEFAULT_POLL_SECONDS, remaining)))


def _timeout_error(timeout: float) -> MaestroOpenError:
    return MaestroOpenError(
        f"Maestro did not become ready within {timeout:g}s. "
        "Inspect the launcher with: "
        "journalctl --user -u maestro-continuum -n40 --no-pager"
    )


def _wait_for_ready(
    initial: AppStatus,
    pterm: Path,
    *,
    expected_root: Path,
    timeout: float,
    poll_seconds: float,
    runner: Callable[..., Any] | None,
    opener: Callable[..., Any] | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[AppStatus, str]:
    started = clock()
    status = initial
    last_line: str | None = None
    last_progress = started
    while True:
        now = clock()
        line = _state_line(status)
        if line != last_line or now - last_progress >= PROGRESS_INTERVAL_SECONDS:
            suffix = ""
            if line == last_line:
                suffix = f" · waited {int(max(0.0, now - started))}s"
            print(f"{line}{suffix}", flush=True)
            last_line = line
            last_progress = now
        if status.payload.get("ready") is True:
            raw_url = status.payload.get("ready_url")
            if raw_url is not None and _loopback_url(raw_url) is None:
                raise MaestroOpenError(
                    "Maestro reported a non-loopback ready_url; refusing to open it."
                )
            base_url = _loopback_url(raw_url)
            if base_url:
                healthy, detail = _verify_local_ready(base_url, opener=opener)
                if healthy:
                    return status, base_url
                print(f"Maestro is ready in Pinokio but {detail}; waiting.", flush=True)

        elapsed = clock() - started
        if elapsed >= timeout:
            raise _timeout_error(timeout)
        remaining = timeout - (clock() - started)
        if remaining <= 0:
            raise _timeout_error(timeout)
        try:
            status = _query_status(
                pterm,
                status.ref,
                expected_root=expected_root,
                runner=runner,
                command_timeout=min(PTERM_COMMAND_TIMEOUT_SECONDS, remaining),
            )
        except ForeignAppError:
            raise
        except ControlPlaneUnavailableError as error:
            print(f"Maestro status check: {error}", flush=True)
        remaining = timeout - (clock() - started)
        if remaining <= 0:
            raise _timeout_error(timeout)
        sleep(max(0.0, min(poll_seconds, remaining)))


def _status_only(
    status: AppStatus,
    *,
    opener: Callable[..., Any] | None,
) -> int:
    print(_state_line(status), flush=True)
    if status.payload.get("ready") is not True:
        return 0
    raw_url = status.payload.get("ready_url")
    base_url = _loopback_url(raw_url)
    if base_url is None:
        print("Maestro is ready, but its URL is not a local loopback; nothing was opened.", flush=True)
        return 0
    healthy, detail = _verify_local_ready(base_url, opener=opener)
    if healthy:
        print(f"Maestro ready: {base_url}", flush=True)
    else:
        print(f"Maestro is not reachable at {base_url} ({detail}).", flush=True)
    return 0


def run(
    argv: Sequence[str] | None = None,
    *,
    command_runner: Callable[..., Any] | None = None,
    opener: Callable[..., Any] | None = None,
    browser_opener: Callable[[str], Any] | None = None,
    which: Callable[[str], str | None] | None = None,
    which_systemctl: Callable[[str], str | None] | None = None,
    config_path: Path | None = None,
    home: Path | None = None,
    expected_root: Path = REPO_ROOT,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status",
        action="store_true",
        help="show Pinokio's current Maestro state without starting or opening it",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="verify readiness and print the URL without opening a browser",
    )
    parser.add_argument(
        "--pterm",
        metavar="PATH",
        help="explicit path to Pinokio's pterm executable",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=f"bounded readiness wait (default: {int(DEFAULT_TIMEOUT_SECONDS)}s)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not math.isfinite(args.timeout) or args.timeout < 0:
        parser.error("--timeout must be a finite number zero or greater")

    pterm = resolve_pterm(
        args.pterm,
        which=which,
        config_path=config_path,
        home=home,
    )
    if args.status:
        status = _resolve_app_status(
            pterm,
            expected_root=expected_root,
            runner=command_runner,
        )
        return _status_only(status, opener=opener)

    systemctl = (which_systemctl or which or shutil.which)("systemctl")
    if not systemctl:
        raise MaestroOpenError(
            "systemctl is unavailable. Start Pinokio, then run: "
            "systemctl --user start maestro-continuum.service"
        )
    # Starting a user service is idempotent.  Pinokio must be available before
    # pterm can resolve the app, while --status intentionally skips this call.
    _run_command(
        [systemctl, "--user", "start", "pinokio.service"],
        timeout=SYSTEMCTL_COMMAND_TIMEOUT_SECONDS,
        runner=command_runner,
    )
    print("Pinokio control plane requested; resolving Maestro.", flush=True)
    status = _wait_for_control_plane(
        pterm,
        expected_root=expected_root,
        timeout=min(args.timeout, CONTROL_PLANE_TIMEOUT_SECONDS),
        runner=command_runner,
        clock=clock,
        sleep=sleep,
    )
    if status.payload.get("running") is True:
        print("Maestro is already running; waiting for its current start.", flush=True)
    else:
        _run_command(
            [systemctl, "--user", "start", SERVICE_NAME],
            timeout=SYSTEMCTL_COMMAND_TIMEOUT_SECONDS,
            runner=command_runner,
        )
        print("Maestro start requested; waiting for Pinokio readiness.", flush=True)

    _status, base_url = _wait_for_ready(
        status,
        pterm,
        expected_root=expected_root,
        timeout=args.timeout,
        poll_seconds=DEFAULT_POLL_SECONDS,
        runner=command_runner,
        opener=opener,
        clock=clock,
        sleep=sleep,
    )
    print(f"Maestro ready: {base_url}", flush=True)
    if not args.no_open:
        browser_opener = browser_opener or webbrowser.open
        try:
            opened = browser_opener(base_url)
        except Exception as error:  # browser adapters vary by desktop
            raise MaestroOpenError(
                f"Maestro is ready at {base_url}, but the browser could not be opened: {error}"
            ) from error
        if opened is False:
            raise MaestroOpenError(
                f"Maestro is ready at {base_url}, but the browser did not open it."
            )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except MaestroOpenError as error:
        print(f"Maestro launch failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
