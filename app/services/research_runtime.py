"""Local-only lifecycle coordinator for scheduled Maestro research.

The durable schedule and sanitized display model remain owned by
``ResearchStore``.  This module only coordinates background process lifetime,
single-flight admission, and the explicit implementation capability.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import secrets
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Protocol

from services.research_implementation import ResearchImplementationRunner
from services.research_store import DEFAULT_READINESS_THRESHOLD, ResearchStore


PUBLIC_RESEARCH_DISCLOSURE = (
    "Only public model, tool, and LoRA catalog metadata is sent to DeepSeek "
    "through Nous. If DeepSeek's mechanical gate fails or its circuit opens, "
    "isolated GPT-5.6 Luna is the only fallback. Maestro never sends project "
    "names, prompts, jobs, media, or logs."
)
DEFAULT_RESEARCH_TIMEOUT_SECONDS = 30 * 60
DEFAULT_NONCE_TTL_SECONDS = 60
DEFAULT_SCHEDULER_POLL_SECONDS = 30
DEFAULT_RETRY_DELAY_SECONDS = 5 * 60


class ResearchRuntimeError(RuntimeError):
    """A concise control-surface error that is safe to return locally."""


class ResearchRuntimeBusy(ResearchRuntimeError):
    pass


class ResearchRuntimeNotReady(ResearchRuntimeError):
    pass


class ResearchNonceError(ResearchRuntimeError):
    pass


class _Process(Protocol):
    pid: int
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int: ...
    def send_signal(self, signal_number: int) -> None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


class SessionNonceStore:
    """Bounded, in-memory, single-use implementation capabilities."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_NONCE_TTL_SECONDS,
        maximum_entries: int = 128,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if ttl_seconds <= 0:
            raise ValueError("nonce ttl must be positive")
        self.ttl_seconds = float(ttl_seconds)
        self.maximum_entries = max(1, min(int(maximum_entries), 1_024))
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, float]] = {}

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("ascii", errors="strict")).hexdigest()

    def _prune_locked(self, now: float) -> None:
        for digest, (_session_id, expires_at) in tuple(self._entries.items()):
            if expires_at <= now:
                self._entries.pop(digest, None)
        overflow = len(self._entries) - self.maximum_entries + 1
        if overflow > 0:
            oldest = sorted(self._entries, key=lambda key: self._entries[key][1])
            for digest in oldest[:overflow]:
                self._entries.pop(digest, None)

    def issue(self, session_id: str) -> dict[str, Any]:
        owner = str(session_id or "")
        if not owner:
            raise ResearchNonceError("A local Maestro session is required")
        token = secrets.token_urlsafe(32)
        now = self.monotonic()
        with self._lock:
            self._prune_locked(now)
            self._entries[self._digest(token)] = (owner, now + self.ttl_seconds)
        return {
            "nonce": token,
            "expires_in_seconds": max(1, int(self.ttl_seconds)),
        }

    def consume(self, session_id: str, token: str) -> None:
        owner = str(session_id or "")
        supplied = str(token or "")
        if not owner or not supplied:
            raise ResearchNonceError("The implementation authorization is missing")
        try:
            digest = self._digest(supplied)
        except (UnicodeEncodeError, ValueError):
            raise ResearchNonceError("The implementation authorization is invalid") from None
        now = self.monotonic()
        with self._lock:
            record = self._entries.pop(digest, None)
            self._prune_locked(now)
        if record is None or record[1] <= now or not secrets.compare_digest(record[0], owner):
            raise ResearchNonceError("The implementation authorization is invalid or expired")


def research_cli_command(app_root: Path) -> list[str]:
    """Return the fixed application-environment research command."""
    if os.name == "nt":
        python = app_root / "env" / "Scripts" / "python.exe"
    else:
        python = app_root / "env" / "bin" / "python"
    return [
        str(python),
        str(app_root / "scripts" / "run_research_cycle.py"),
        "run",
    ]


class ResearchRuntime:
    """Own background due checks and explicit local control actions."""

    def __init__(
        self,
        *,
        store: ResearchStore | None = None,
        repo_root: str | os.PathLike[str] | None = None,
        busy_predicate: Callable[[], bool] = lambda: False,
        popen_factory: Callable[..., _Process] = subprocess.Popen,
        implementation_runner_factory: Callable[..., Any] = ResearchImplementationRunner,
        nonce_store: SessionNonceStore | None = None,
        research_timeout_seconds: float = DEFAULT_RESEARCH_TIMEOUT_SECONDS,
        scheduler_poll_seconds: float = DEFAULT_SCHEDULER_POLL_SECONDS,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
        terminate_grace_seconds: float = 5,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.store = store or ResearchStore.default()
        self.app_root = Path(__file__).resolve().parents[1]
        self.repo_root = Path(repo_root or self.app_root.parent).resolve()
        self.busy_predicate = busy_predicate
        self.popen_factory = popen_factory
        self.implementation_runner_factory = implementation_runner_factory
        self.nonces = nonce_store or SessionNonceStore(monotonic=monotonic)
        self.research_timeout_seconds = max(1.0, float(research_timeout_seconds))
        self.scheduler_poll_seconds = max(0.05, float(scheduler_poll_seconds))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self.terminate_grace_seconds = max(0.05, float(terminate_grace_seconds))
        self.monotonic = monotonic
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._implementation_cancel = threading.Event()
        self._scheduler_thread: threading.Thread | None = None
        self._research_thread: threading.Thread | None = None
        self._implementation_thread: threading.Thread | None = None
        self._research_process: _Process | None = None
        self._last_research_attempt = float("-inf")
        self._runtime_error: str | None = None
        self._storage_available = True
        self._started = False

    def _enable_on_first_start(self) -> None:
        state_path = getattr(self.store, "state_path", None)
        if state_path is None or not Path(state_path).exists():
            self.store.enable()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            self._implementation_cancel.clear()
            self._started = True
            self._storage_available = True
            try:
                self._enable_on_first_start()
            except Exception:
                # Research is supplemental. A missing/unwritable research
                # store must fail this control surface closed without
                # preventing the rest of Maestro from starting.
                self._storage_available = False
                self._runtime_error = "Research storage is unavailable."
                return
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                daemon=True,
                name="maestro-research-scheduler",
            )
            self._scheduler_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._implementation_cancel.set()
        with self._lock:
            scheduler = self._scheduler_thread
            research = self._research_thread
            implementation = self._implementation_thread
        # The research worker exclusively owns its child interruption. Calling
        # _stop_process here as well can deliver a second SIGINT while the
        # child's durable finally cleanup is still running.
        # Include the worker's bounded wait poll plus graceful and forced tree
        # termination windows. This stays bounded while ensuring stop() does
        # not return before the sole interruption owner has observed _stop.
        deadline = (
            self.monotonic()
            + self.terminate_grace_seconds * 2
            + 0.5
        )
        for thread in (scheduler, research, implementation):
            if thread is None or thread is threading.current_thread():
                continue
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        with self._lock:
            alive = any(
                thread is not None and thread.is_alive()
                for thread in (
                    self._scheduler_thread,
                    self._research_thread,
                    self._implementation_thread,
                )
            )
            self._started = alive
            if alive:
                self._runtime_error = "Research shutdown is still completing."

    def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                due, _reason = self.store.due()
                retry_ready = (
                    self.monotonic() - self._last_research_attempt
                    >= self.retry_delay_seconds
                )
                if due and retry_ready:
                    try:
                        self.start_research(force=False)
                    except ResearchRuntimeBusy:
                        pass
            except Exception:
                with self._lock:
                    self._runtime_error = "Research schedule status is temporarily unavailable."
            self._stop.wait(self.scheduler_poll_seconds)

    def start_research(self, *, force: bool = True) -> dict[str, Any]:
        with self._lock:
            if not self._storage_available:
                raise ResearchRuntimeError("Research storage is unavailable")
            if self._stop.is_set() and self._started:
                raise ResearchRuntimeBusy("Research is shutting down")
            if self._research_thread is not None and self._research_thread.is_alive():
                raise ResearchRuntimeBusy("A research cycle is already active")
            if (
                self._implementation_thread is not None
                and self._implementation_thread.is_alive()
            ):
                raise ResearchRuntimeBusy("Research is blocked while implementation is starting")
            state = self.store.read_model()
            if state.get("research_active"):
                raise ResearchRuntimeBusy("A research cycle is already active")
            if state.get("implementation_active"):
                raise ResearchRuntimeBusy("Research is blocked while implementation is active")
            self._runtime_error = None
            self._last_research_attempt = self.monotonic()
            thread = threading.Thread(
                target=self._research_worker,
                kwargs={"force": bool(force)},
                daemon=True,
                name="maestro-research-cycle",
            )
            self._research_thread = thread
            thread.start()
        return {"status": "accepted", "force": bool(force)}

    def _research_worker(self, *, force: bool) -> None:
        command = research_cli_command(self.app_root)
        if force:
            command.append("--force")
        process: _Process | None = None
        outcome = "failed"
        try:
            if self._stop.is_set():
                return
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            process = self.popen_factory(
                command,
                cwd=str(self.repo_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=os.name == "posix",
                creationflags=creationflags,
            )
            with self._lock:
                self._research_process = process
            deadline = self.monotonic() + self.research_timeout_seconds
            while True:
                if self._stop.is_set():
                    self._stop_process(process)
                    outcome = "cancelled"
                    break
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    self._stop_process(process)
                    outcome = "timed_out"
                    break
                try:
                    return_code = process.wait(timeout=min(0.25, remaining))
                except (subprocess.TimeoutExpired, TimeoutError):
                    continue
                outcome = "completed" if int(return_code) == 0 else "failed"
                break
        except Exception:
            outcome = "failed"
        finally:
            with self._lock:
                if self._research_process is process:
                    self._research_process = None
                if outcome == "timed_out":
                    self._runtime_error = "The research cycle timed out."
                elif outcome == "failed":
                    self._runtime_error = "The research cycle could not complete."
                elif outcome == "cancelled" and not self._stop.is_set():
                    self._runtime_error = "The research cycle was cancelled."

    def _stop_process(self, process: _Process) -> None:
        pid = int(getattr(process, "pid", 0) or 0)
        try:
            if os.name == "posix" and pid > 0:
                # SIGINT becomes KeyboardInterrupt in the Python CLI, allowing
                # ResearchPipeline.run's finally block and the store lease
                # context to durably clear active state before exit.
                os.killpg(pid, signal.SIGINT)
            elif os.name == "nt" and pid > 0:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            process.wait(timeout=self.terminate_grace_seconds)
            self._terminate_process_tree(process)
            return
        except (subprocess.TimeoutExpired, TimeoutError):
            pass
        except Exception:
            pass
        self._terminate_process_tree(process)

    def _terminate_process_tree(self, process: _Process) -> None:
        pid = int(getattr(process, "pid", 0) or 0)
        if os.name == "nt" and pid > 0:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    shell=False,
                    timeout=self.terminate_grace_seconds,
                )
            except Exception:
                pass
            try:
                process.wait(timeout=self.terminate_grace_seconds)
            except Exception:
                pass
            return
        if os.name == "posix" and pid > 0:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError:
                return
            deadline = self.monotonic() + self.terminate_grace_seconds
            while self.monotonic() < deadline:
                try:
                    os.killpg(pid, 0)
                except ProcessLookupError:
                    return
                except OSError:
                    return
                time.sleep(min(0.05, max(0.0, deadline - self.monotonic())))
            try:
                os.killpg(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=self.terminate_grace_seconds)
            except Exception:
                pass
            return
        try:
            process.kill()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            process.wait(timeout=self.terminate_grace_seconds)
            return
        except (subprocess.TimeoutExpired, TimeoutError):
            if os.name == "posix" and pid > 0:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
            else:
                try:
                    process.kill()
                except Exception:
                    pass
        except Exception:
            return

    def issue_implementation_nonce(self, session_id: str) -> dict[str, Any]:
        return self.nonces.issue(session_id)

    def start_implementation(
        self,
        *,
        session_id: str,
        nonce: str,
        force: bool = False,
    ) -> dict[str, Any]:
        # Consume before every other check so a rejected action cannot be
        # replayed later after application state changes.
        self.nonces.consume(session_id, nonce)
        with self._lock:
            if not self._storage_available:
                raise ResearchRuntimeError("Research storage is unavailable")
            if (
                self._implementation_thread is not None
                and self._implementation_thread.is_alive()
            ):
                raise ResearchRuntimeBusy("An implementation run is already active")
            if self._research_thread is not None and self._research_thread.is_alive():
                raise ResearchRuntimeBusy("Implementation is blocked while research is starting")
            try:
                application_busy = bool(self.busy_predicate())
            except Exception:
                raise ResearchRuntimeBusy("Application activity could not be verified") from None
            if application_busy:
                raise ResearchRuntimeBusy(
                    "Implementation is blocked while generation, Director, download, or recovery work is active"
                )
            state = self.store.read_model(
                readiness_threshold=DEFAULT_READINESS_THRESHOLD,
            )
            if state.get("research_active") or state.get("implementation_active"):
                raise ResearchRuntimeBusy("Research or implementation work is already active")
            eligible = int(state.get("implementation_chunk_count") or 0)
            if eligible < 1:
                raise ResearchRuntimeNotReady("No eligible research suggestions are available")
            if not force and not state.get("implementation_ready"):
                raise ResearchRuntimeNotReady("More eligible research suggestions are required")
            self._runtime_error = None
            self._implementation_cancel.clear()
            thread = threading.Thread(
                target=self._implementation_worker,
                kwargs={"force": bool(force)},
                daemon=True,
                name="maestro-research-implementation",
            )
            self._implementation_thread = thread
            thread.start()
        return {"status": "accepted", "force": bool(force)}

    def _implementation_worker(self, *, force: bool) -> None:
        try:
            runner = self.implementation_runner_factory(
                self.store,
                self.repo_root,
                busy_predicate=self.busy_predicate,
            )
            # No client-supplied paths, prompts, packet IDs, or finding IDs
            # cross this boundary. Force reaches only the runner's readiness
            # count gate.
            runner.run(
                force=force,
                readiness_threshold=DEFAULT_READINESS_THRESHOLD,
                cancel=self._implementation_cancel,
            )
        except Exception:
            with self._lock:
                self._runtime_error = "The implementation run could not start or complete."

    def status(self) -> dict[str, Any]:
        model = dict(self.store.read_model())
        with self._lock:
            research_launching = bool(
                self._research_thread is not None
                and self._research_thread.is_alive()
            )
            implementation_launching = bool(
                self._implementation_thread is not None
                and self._implementation_thread.is_alive()
            )
            runtime_error = self._runtime_error
        if research_launching and not model.get("research_active"):
            model["research_active"] = True
            model["research_phase"] = "starting"
        if implementation_launching and not model.get("implementation_active"):
            model["implementation_active"] = True
        model["runtime_error"] = runtime_error
        model["disclosure"] = PUBLIC_RESEARCH_DISCLOSURE
        return model


__all__ = [
    "PUBLIC_RESEARCH_DISCLOSURE",
    "ResearchNonceError",
    "ResearchRuntime",
    "ResearchRuntimeBusy",
    "ResearchRuntimeError",
    "ResearchRuntimeNotReady",
    "SessionNonceStore",
    "research_cli_command",
]
