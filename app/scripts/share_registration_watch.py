"""Continuously replay Maestro's ephemeral public-origin registration.

The Worker update credential is accepted only through the process environment.
Diagnostics are fixed reason codes; exceptions, response bodies, and credentials
are never printed or persisted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import stat
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

from register_share_url import (
    _canonical_loopback_origin,
    _canonical_quick_tunnel_url,
    _canonical_workers_dev_url,
    register_share_url,
    replay_share_url,
)

DEFAULT_STARTUP_BUDGET_SECONDS = 240.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_REFRESH_INTERVAL_SECONDS = 15.0
DEFAULT_WORKER_RETRY_INTERVAL_SECONDS = 300.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 3.0
_PROBE_USER_AGENT = "Maestro-Share-Watch/1.0"


class WatchConfigurationError(ValueError):
    """Raised for invalid content-free watcher configuration."""


class LeaseUnavailableError(RuntimeError):
    """Raised when another watcher owns the registration lifecycle."""


def _emit(message: str) -> None:
    print(message, flush=True)


def _current_uid() -> int | None:
    getuid = getattr(os, "getuid", None)
    return getuid() if callable(getuid) else None


def _posix_security_available() -> bool:
    return os.name != "nt" and _current_uid() is not None


def _validate_secure_directory(path: Path, *, create: bool = False) -> Path:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise WatchConfigurationError("runtime_directory_unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise WatchConfigurationError("runtime_directory_unsafe")
    current_uid = _current_uid()
    if _posix_security_available() and metadata.st_uid != current_uid:
        raise WatchConfigurationError("runtime_directory_wrong_owner")
    # On Windows the owner's temporary-directory ACL is the confinement
    # boundary; POSIX ownership and permission bits are not authoritative.
    if _posix_security_available() and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise WatchConfigurationError("runtime_directory_permissions")
    return path


def default_runtime_directory() -> Path:
    current_uid = _current_uid()
    owner_key = str(current_uid) if current_uid is not None else hashlib.sha256(
        str(Path.home()).encode("utf-8"),
    ).hexdigest()[:16]
    return _validate_secure_directory(
        Path(tempfile.gettempdir()) / f"maestro-share-runtime-{owner_key}",
        create=True,
    )


def _validate_secure_regular(metadata: os.stat_result, *, reason: str) -> None:
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise WatchConfigurationError(f"{reason}_type")
    current_uid = _current_uid()
    if _posix_security_available() and metadata.st_uid != current_uid:
        raise WatchConfigurationError(f"{reason}_owner")
    if _posix_security_available() and stat.S_IMODE(metadata.st_mode) != 0o600:
        raise WatchConfigurationError(f"{reason}_permissions")
    if metadata.st_nlink != 1:
        raise WatchConfigurationError(f"{reason}_links")


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def secure_read_runtime_text(path: Path, *, max_bytes: int = 512) -> str:
    """Read one owner-only regular file without following links or races."""

    _validate_secure_directory(path.parent)
    try:
        before = path.lstat()
        _validate_secure_regular(before, reason="runtime_file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except (OSError, WatchConfigurationError) as error:
        raise WatchConfigurationError("runtime_file_unavailable") from error
    try:
        opened = os.fstat(descriptor)
        _validate_secure_regular(opened, reason="runtime_file")
        if _identity(before) != _identity(opened):
            raise WatchConfigurationError("runtime_file_changed")
        content = os.read(descriptor, max_bytes + 1)
        if len(content) > max_bytes:
            raise WatchConfigurationError("runtime_file_too_large")
        after = path.lstat()
        _validate_secure_regular(after, reason="runtime_file")
        if _identity(opened) != _identity(after):
            raise WatchConfigurationError("runtime_file_changed")
    except OSError as error:
        raise WatchConfigurationError("runtime_file_unavailable") from error
    finally:
        os.close(descriptor)
    try:
        return content.decode("utf-8")
    except UnicodeError as error:
        raise WatchConfigurationError("runtime_file_encoding") from error


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if _identity(opened) != _identity(current):
            raise OSError("runtime directory changed")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_replace(source: Path, destination: Path) -> None:
    if os.name != "nt":
        os.replace(source, destination)
        return
    import ctypes

    move_file = ctypes.windll.kernel32.MoveFileExW
    move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    move_file.restype = ctypes.c_int
    if not move_file(str(source), str(destination), 0x1 | 0x8):
        raise OSError("durable runtime-file replacement failed")


def _existing_identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    _validate_secure_regular(metadata, reason="runtime_file")
    return _identity(metadata)


def secure_publish_runtime_text(
    path: Path,
    content: str,
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    payload = content.encode("utf-8")
    if len(payload) > 512:
        raise WatchConfigurationError("runtime_file_too_large")
    directory = _validate_secure_directory(path.parent, create=True)
    expected_destination = _existing_identity(path)
    temporary = directory / f".{path.name}.{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    temporary_identity: tuple[int, int] | None = None
    try:
        if hasattr(os, "fchmod") and os.name != "nt":
            os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        _validate_secure_regular(opened, reason="temporary_file")
        temporary_identity = _identity(opened)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("runtime-file write did not advance")
            offset += written
        os.fsync(descriptor)
        current_temp = temporary.lstat()
        _validate_secure_regular(current_temp, reason="temporary_file")
        if _identity(current_temp) != temporary_identity:
            raise OSError("temporary runtime file changed")
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(directory)
        if before_replace is not None:
            before_replace()
        current_temp = temporary.lstat()
        _validate_secure_regular(current_temp, reason="temporary_file")
        if _identity(current_temp) != temporary_identity:
            raise OSError("temporary runtime file changed")
        if _existing_identity(path) != expected_destination:
            raise OSError("runtime destination changed")
        _durable_replace(temporary, path)
        _fsync_directory(directory)
        published = path.lstat()
        _validate_secure_regular(published, reason="runtime_file")
        if _identity(published) != temporary_identity:
            raise OSError("published runtime file changed")
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def secure_open_lock_file(path: Path) -> IO[bytes]:
    """Open or create one owner-only, single-link lock without following links."""

    _validate_secure_directory(path.parent, create=True)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
        )
        created = True
    except FileExistsError:
        try:
            before = path.lstat()
            _validate_secure_regular(before, reason="lease_file")
            descriptor = os.open(path, os.O_RDWR | nofollow)
        except (OSError, WatchConfigurationError) as error:
            raise LeaseUnavailableError("registration lease path is unsafe") from error
    except OSError as error:
        raise LeaseUnavailableError("registration lease path is unavailable") from error
    try:
        if created and hasattr(os, "fchmod") and os.name != "nt":
            os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        _validate_secure_regular(opened, reason="lease_file")
        current = path.lstat()
        _validate_secure_regular(current, reason="lease_file")
        if _identity(opened) != _identity(current):
            raise LeaseUnavailableError("registration lease path changed")
        return os.fdopen(descriptor, "r+b", closefd=True)
    except (OSError, WatchConfigurationError, LeaseUnavailableError):
        os.close(descriptor)
        raise LeaseUnavailableError("registration lease path is unsafe") from None


def _process_alive(pid: int | None) -> bool:
    if pid is None:
        return True
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _listener_ready(origin: str, timeout: float = 1.0) -> bool:
    from urllib.parse import urlsplit

    parsed = urlsplit(origin)
    port = parsed.port or 80
    try:
        with socket.create_connection((parsed.hostname or "", port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_probe(origin: str, path: str, timeout: float) -> bool:
    request = Request(
        origin + path,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": _PROBE_USER_AGENT},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read(1)
            return response.status == 200
    except (HTTPError, URLError, OSError, TimeoutError):
        return False


def _pid_from_file(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        pid = int(secure_read_runtime_text(path, max_bytes=64).strip())
    except (OSError, UnicodeError, ValueError, WatchConfigurationError):
        return None
    return pid if pid > 0 else None


class RegistrationLease:
    """One non-blocking host-local owner for a loopback registration target."""

    def __init__(
        self,
        origin: str,
        *,
        runtime_dir: Path | None = None,
        namespace: str = "registration",
    ) -> None:
        if namespace not in {"registration", "tunnel-supervisor"}:
            raise WatchConfigurationError("lease_namespace_invalid")
        digest = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:24]
        directory = runtime_dir or default_runtime_directory()
        self.path = directory / f"maestro-share-{namespace}-{digest}.lock"
        self._handle: IO[bytes] | None = None

    def __enter__(self) -> "RegistrationLease":
        handle = secure_open_lock_file(self.path)
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            handle.close()
            raise LeaseUnavailableError("registration lease is already held") from error
        try:
            opened = os.fstat(handle.fileno())
            current = self.path.lstat()
            _validate_secure_regular(opened, reason="lease_file")
            _validate_secure_regular(current, reason="lease_file")
            if _identity(opened) != _identity(current):
                raise LeaseUnavailableError("registration lease path changed")
        except (OSError, WatchConfigurationError, LeaseUnavailableError):
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            raise LeaseUnavailableError("registration lease path is unsafe") from None
        self._handle = handle
        return self

    def __exit__(self, *_args) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def assert_current(self) -> None:
        handle = self._handle
        if handle is None:
            raise LeaseUnavailableError("registration lease is not held")
        try:
            opened = os.fstat(handle.fileno())
            current = self.path.lstat()
            _validate_secure_regular(opened, reason="lease_file")
            _validate_secure_regular(current, reason="lease_file")
        except (OSError, WatchConfigurationError) as error:
            raise LeaseUnavailableError("registration lease path is unsafe") from error
        if _identity(opened) != _identity(current):
            raise LeaseUnavailableError("registration lease path changed")


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    reason: str


def wait_for_backend(
    origin: str,
    *,
    startup_budget_seconds: float = DEFAULT_STARTUP_BUDGET_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    backend_pid: int | None = None,
    backend_pid_file: Path | None = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    listener_ready: Callable[[str, float], bool] = _listener_ready,
    http_probe: Callable[[str, str, float], bool] = _http_probe,
) -> ReadinessResult:
    """Stage process, listener, health, and durable readiness within one budget."""

    deadline = now() + max(1.0, startup_budget_seconds)
    last_stage = "process"
    while now() < deadline:
        current_pid = _pid_from_file(backend_pid_file) or backend_pid
        if current_pid is not None and not _process_alive(current_pid):
            return ReadinessResult(False, "backend_process_exited")
        last_stage = "listener"
        if not listener_ready(origin, min(1.0, request_timeout_seconds)):
            sleep(min(poll_interval_seconds, max(0.0, deadline - now())))
            continue
        last_stage = "health"
        if not http_probe(origin, "/health", request_timeout_seconds):
            sleep(min(poll_interval_seconds, max(0.0, deadline - now())))
            continue
        last_stage = "ready"
        if not http_probe(origin, "/ready", request_timeout_seconds):
            sleep(min(poll_interval_seconds, max(0.0, deadline - now())))
            continue
        return ReadinessResult(True, "ready")
    return ReadinessResult(False, f"{last_stage}_timeout")


class QuickUrlSource:
    def __init__(self, value: str, file_path: Path | None) -> None:
        self._value = _canonical_quick_tunnel_url(value) if value else ""
        self._file_path = file_path

    def current(self) -> str:
        if self._file_path is None:
            if not self._value:
                raise WatchConfigurationError("quick_url_missing")
            return self._value
        try:
            raw = secure_read_runtime_text(self._file_path).strip()
        except (OSError, WatchConfigurationError) as error:
            raise WatchConfigurationError("quick_url_file_unavailable") from error
        if not raw:
            raise WatchConfigurationError("quick_url_file_empty")
        self._value = _canonical_quick_tunnel_url(raw)
        return self._value


def default_quick_url_file(origin: str, *, runtime_dir: Path | None = None) -> Path:
    canonical = _canonical_loopback_origin(origin)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    directory = (
        _validate_secure_directory(runtime_dir, create=True)
        if runtime_dir is not None else default_runtime_directory()
    )
    return directory / f"maestro-quick-tunnel-{digest}.url"


def default_registration_status_file(
    origin: str, *, runtime_dir: Path | None = None,
) -> Path:
    canonical = _canonical_loopback_origin(origin)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    directory = (
        _validate_secure_directory(runtime_dir, create=True)
        if runtime_dir is not None else default_runtime_directory()
    )
    return directory / f"maestro-share-registration-{digest}.json"


def wait_for_registration_status(
    origin: str,
    quick_url: str,
    *,
    status_file: Path | None = None,
    budget_seconds: float = 90.0,
    poll_interval_seconds: float = 1.0,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str] | None:
    canonical_origin = _canonical_loopback_origin(origin)
    canonical_quick = _canonical_quick_tunnel_url(quick_url)
    path = status_file or default_registration_status_file(canonical_origin)
    deadline = now() + max(1.0, budget_seconds)
    while now() < deadline:
        try:
            payload = json.loads(secure_read_runtime_text(path))
            if (
                isinstance(payload, dict)
                and payload.get("quick_url") == canonical_quick
                and payload.get("kind") in {"stable", "quick"}
                and isinstance(payload.get("selected_url"), str)
            ):
                selected = (
                    _canonical_workers_dev_url(payload["selected_url"])
                    if payload["kind"] == "stable"
                    else _canonical_quick_tunnel_url(payload["selected_url"])
                )
                if payload["kind"] == "quick" and selected != canonical_quick:
                    raise ValueError("registration status quick URL mismatch")
                return selected, payload["kind"]
        except (OSError, ValueError, TypeError, json.JSONDecodeError, WatchConfigurationError):
            pass
        sleep(min(poll_interval_seconds, max(0.0, deadline - now())))
    return None


def watch_registration(
    origin: str,
    quick_source: QuickUrlSource,
    *,
    stable_url: str,
    update_secret: str,
    startup_budget_seconds: float = DEFAULT_STARTUP_BUDGET_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    refresh_interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
    worker_retry_interval_seconds: float = DEFAULT_WORKER_RETRY_INTERVAL_SECONDS,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    backend_pid: int | None = None,
    backend_pid_file: Path | None = None,
    once: bool = False,
    max_cycles: int | None = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    wait_backend: Callable[..., ReadinessResult] = wait_for_backend,
    register: Callable[..., tuple[str, str]] = register_share_url,
    replay: Callable[..., tuple[str, str]] = replay_share_url,
    status_file: Path | None = None,
    lease_check: Callable[[], None] | None = None,
) -> int:
    """Replay after readiness epochs, Quick URL changes, and periodic refreshes."""

    local_origin = _canonical_loopback_origin(origin)
    registration_status_file = status_file or default_registration_status_file(local_origin)
    last_quick = ""
    last_selected = ""
    last_kind = ""
    last_backend_identity: int | None = None
    was_ready = False
    next_refresh = 0.0
    next_worker_retry = 0.0
    pending_status_event = ""
    cycles = 0

    while max_cycles is None or cycles < max_cycles:
        if lease_check is not None:
            lease_check()
        cycles += 1
        quick_url = quick_source.current()
        backend_identity = _pid_from_file(backend_pid_file) or backend_pid
        readiness = wait_backend(
            local_origin,
            startup_budget_seconds=startup_budget_seconds,
            poll_interval_seconds=poll_interval_seconds,
            request_timeout_seconds=request_timeout_seconds,
            backend_pid=backend_pid,
            backend_pid_file=backend_pid_file,
            now=now,
            sleep=sleep,
        )
        if not readiness.ready:
            _emit(f"MAESTRO_SHARE_WATCH_WAIT {readiness.reason}")
            if readiness.reason == "backend_process_exited":
                return 3
            was_ready = False
            if once:
                return 4
            sleep(poll_interval_seconds)
            continue

        if pending_status_event:
            try:
                secure_publish_runtime_text(
                    registration_status_file,
                    json.dumps(
                        {
                            "quick_url": last_quick,
                            "selected_url": last_selected,
                            "kind": last_kind,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ) + "\n",
                )
            except (OSError, ValueError, WatchConfigurationError):
                _emit("MAESTRO_SHARE_WATCH_WAIT status_unavailable")
                was_ready = True
                sleep(poll_interval_seconds)
                continue
            _emit(pending_status_event)
            pending_status_event = ""
            if once:
                return 0

        readiness_epoch = not was_ready
        backend_identity = _pid_from_file(backend_pid_file) or backend_pid
        identity_changed = (
            last_backend_identity is not None
            and backend_identity is not None
            and backend_identity != last_backend_identity
        )
        rotated = bool(last_quick and quick_url != last_quick)
        refresh_due = now() >= next_refresh
        worker_retry_due = (
            bool(last_selected)
            and last_kind == "quick"
            and bool(stable_url and update_secret)
            and now() >= next_worker_retry
        )
        if readiness_epoch or identity_changed or rotated or refresh_due:
            try:
                full_registration = not last_selected or rotated or worker_retry_due
                if full_registration:
                    selected, kind = register(
                        local_origin,
                        quick_url,
                        stable_url=stable_url,
                        update_secret=update_secret,
                    )
                else:
                    selected, kind = replay(
                        local_origin,
                        quick_url,
                        last_selected,
                        stable_verified=last_kind == "stable",
                    )
            except (HTTPError, URLError, OSError, TimeoutError, ValueError, TypeError):
                _emit("MAESTRO_SHARE_WATCH_WAIT registration_unavailable")
                was_ready = False
                if once:
                    return 5
                sleep(poll_interval_seconds)
                continue
            first = not last_selected
            changed = selected != last_selected or kind != last_kind or rotated
            if first:
                pending_status_event = f"MAESTRO_SHARE_READY {selected} {kind}"
            elif changed:
                pending_status_event = f"MAESTRO_SHARE_UPDATED {selected} {kind}"
            else:
                pending_status_event = f"MAESTRO_SHARE_REPLAYED {kind}"
            last_selected = selected
            last_kind = kind
            last_quick = quick_url
            last_backend_identity = backend_identity
            next_refresh = now() + max(1.0, refresh_interval_seconds)
            if full_registration:
                next_worker_retry = now() + max(1.0, worker_retry_interval_seconds)
            try:
                secure_publish_runtime_text(
                    registration_status_file,
                    json.dumps(
                        {
                            "quick_url": last_quick,
                            "selected_url": last_selected,
                            "kind": last_kind,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ) + "\n",
                )
            except (OSError, ValueError, WatchConfigurationError):
                _emit("MAESTRO_SHARE_WATCH_WAIT status_unavailable")
                was_ready = True
                sleep(poll_interval_seconds)
                continue
            _emit(pending_status_event)
            pending_status_event = ""
            if once:
                return 0
        was_ready = True
        sleep(min(poll_interval_seconds, max(0.1, next_refresh - now())))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay Maestro share registration across backend restarts",
        epilog=(
            "Runnable directly from Pinokio, Windows, or a systemd ExecStart. "
            "Keep PINOKIO_STABLE_SHARE_UPDATE_SECRET in the service environment."
        ),
    )
    parser.add_argument("--origin", default=os.environ.get("MAESTRO_LOCAL_ORIGIN", ""))
    parser.add_argument(
        "--quick-url", default=os.environ.get("MAESTRO_QUICK_SHARE_URL", ""),
    )
    parser.add_argument(
        "--quick-url-file",
        type=Path,
        default=(
            Path(os.environ["MAESTRO_QUICK_SHARE_URL_FILE"])
            if os.environ.get("MAESTRO_QUICK_SHARE_URL_FILE") else None
        ),
    )
    parser.add_argument("--backend-pid", type=int, default=None)
    parser.add_argument("--runtime-dir", type=Path, default=None)
    parser.add_argument(
        "--backend-pid-file",
        type=Path,
        default=(
            Path(os.environ["MAESTRO_BACKEND_PID_FILE"])
            if os.environ.get("MAESTRO_BACKEND_PID_FILE") else None
        ),
    )
    parser.add_argument(
        "--startup-budget-seconds", type=float, default=DEFAULT_STARTUP_BUDGET_SECONDS,
    )
    parser.add_argument(
        "--refresh-interval-seconds", type=float, default=DEFAULT_REFRESH_INTERVAL_SECONDS,
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--wait-registered-url", default="")
    parser.add_argument("--wait-backend-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    try:
        origin = _canonical_loopback_origin(options.origin)
        if options.wait_backend_only:
            readiness = wait_for_backend(
                origin,
                startup_budget_seconds=options.startup_budget_seconds,
                backend_pid=options.backend_pid,
                backend_pid_file=options.backend_pid_file,
            )
            if not readiness.ready:
                _emit(f"MAESTRO_BACKEND_WAIT_FAILED {readiness.reason}")
                return 4
            _emit("MAESTRO_BACKEND_READY")
            return 0
        if options.wait_registered_url:
            registered = wait_for_registration_status(
                origin,
                options.wait_registered_url,
                status_file=default_registration_status_file(
                    origin, runtime_dir=options.runtime_dir,
                ),
                budget_seconds=options.startup_budget_seconds,
            )
            if registered is None:
                _emit("MAESTRO_SHARE_WATCH_FAILED registration_timeout")
                return 4
            selected, kind = registered
            _emit(f"MAESTRO_SHARE_READY {selected} {kind}")
            return 0
        quick_url_file = options.quick_url_file
        if quick_url_file is None and not options.quick_url:
            quick_url_file = default_quick_url_file(
                origin, runtime_dir=options.runtime_dir,
            )
        source = QuickUrlSource(options.quick_url, quick_url_file)
        with RegistrationLease(origin, runtime_dir=options.runtime_dir) as lease:
            try:
                return watch_registration(
                    origin,
                    source,
                    stable_url=os.environ.get("PINOKIO_STABLE_SHARE_URL", ""),
                    update_secret=os.environ.get("PINOKIO_STABLE_SHARE_UPDATE_SECRET", ""),
                    startup_budget_seconds=options.startup_budget_seconds,
                    refresh_interval_seconds=options.refresh_interval_seconds,
                    backend_pid=options.backend_pid,
                    backend_pid_file=options.backend_pid_file,
                    once=options.once,
                    lease_check=lease.assert_current,
                    status_file=default_registration_status_file(
                        origin, runtime_dir=options.runtime_dir,
                    ),
                )
            except LeaseUnavailableError:
                _emit("MAESTRO_SHARE_WATCH_FAILED lease_changed")
                return 2
    except LeaseUnavailableError:
        _emit("MAESTRO_SHARE_WATCH_ALREADY_RUNNING")
        return 0
    except (WatchConfigurationError, ValueError, OSError):
        _emit("MAESTRO_SHARE_WATCH_FAILED invalid_configuration")
        return 2
    except KeyboardInterrupt:
        _emit("MAESTRO_SHARE_WATCH_STOPPED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
