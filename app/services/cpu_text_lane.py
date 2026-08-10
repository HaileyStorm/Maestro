"""Bounded CPU-only text coexistence and conservative preemption policy.

This module owns no prompts, model identities, paths, or provider selection.
Launch supplies content-free host measurements and exact runtime control
callbacks after it has classified an operation as local and text-only.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

GIB = 1024 ** 3
ADMISSION_REASONS = frozenset({
    "admitted",
    "lane_busy",
    "memory_pressure",
    "thread_pressure",
    "cpu_pressure",
    "cancelled",
})


@dataclass(frozen=True)
class HostAdmissionSnapshot:
    """Content-free measurements used for one CPU coexistence decision."""

    available_bytes: int
    total_bytes: int
    required_bytes: int
    logical_threads: int
    worker_threads: int
    cpu_percent: float


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in ADMISSION_REASONS:
            raise ValueError("Invalid CPU text admission reason")


def host_admission_decision(
    snapshot: HostAdmissionSnapshot,
    *,
    minimum_reserve_bytes: int = 8 * GIB,
    reserve_fraction: float = 0.10,
    minimum_spare_threads: int = 4,
    maximum_cpu_percent: float = 85.0,
) -> AdmissionDecision:
    """Admit only when RAM, thread, and current CPU budgets all pass."""
    numeric = (
        snapshot.available_bytes,
        snapshot.total_bytes,
        snapshot.required_bytes,
        snapshot.logical_threads,
        snapshot.worker_threads,
    )
    if any(type(value) is not int or value < 0 for value in numeric):
        return AdmissionDecision(False, "memory_pressure")
    if (
        not isinstance(snapshot.cpu_percent, (int, float))
        or isinstance(snapshot.cpu_percent, bool)
        or not math.isfinite(float(snapshot.cpu_percent))
    ):
        return AdmissionDecision(False, "cpu_pressure")
    reserve = max(
        max(0, int(minimum_reserve_bytes)),
        int(snapshot.total_bytes * max(0.0, min(float(reserve_fraction), 0.5))),
    )
    if snapshot.available_bytes < snapshot.required_bytes + reserve:
        return AdmissionDecision(False, "memory_pressure")
    if snapshot.logical_threads < snapshot.worker_threads + minimum_spare_threads:
        return AdmissionDecision(False, "thread_pressure")
    if float(snapshot.cpu_percent) > maximum_cpu_percent:
        return AdmissionDecision(False, "cpu_pressure")
    return AdmissionDecision(True, "admitted")


@dataclass(frozen=True)
class BreakEvenEstimate:
    """Known seconds remaining on each discard/restart alternative."""

    cpu_remaining_seconds: float | None
    stop_release_seconds: float | None
    gpu_load_seconds: float | None
    gpu_remaining_seconds: float | None


def should_preempt_cpu_attempt(
    estimate: BreakEvenEstimate,
    *,
    minimum_hysteresis_seconds: float = 15.0,
    hysteresis_fraction: float = 0.20,
) -> bool:
    """Return true only for a complete, conservative delivery-time win."""
    values = (
        estimate.cpu_remaining_seconds,
        estimate.stop_release_seconds,
        estimate.gpu_load_seconds,
        estimate.gpu_remaining_seconds,
    )
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
        for value in values
    ):
        return False
    cpu_remaining = float(estimate.cpu_remaining_seconds)
    accelerated = sum(float(value) for value in values[1:])
    hysteresis = max(
        max(0.0, float(minimum_hysteresis_seconds)),
        accelerated * max(0.0, min(float(hysteresis_fraction), 1.0)),
    )
    return cpu_remaining > accelerated + hysteresis


@dataclass(frozen=True)
class RuntimeAttemptTokens:
    runtime_generation: int
    runtime_attempt_id: int

    def __post_init__(self) -> None:
        if type(self.runtime_generation) is not int or self.runtime_generation < 1:
            raise ValueError("Invalid runtime generation token")
        if type(self.runtime_attempt_id) is not int or self.runtime_attempt_id < 1:
            raise ValueError("Invalid runtime attempt token")


class CPUTextLease:
    """One exact process-local CPU text lane lease."""

    def __init__(self, lane: CPUTextLane, owner_key: str, token: str):
        self._lane = lane
        self.owner_key = owner_key
        self.token = token
        self.acquired_monotonic = time.monotonic()
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._lane.release(self)

    def __enter__(self) -> CPUTextLease:
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.release()


class CPUTextLane:
    """FIFO admission for exactly one concurrent local CPU text attempt."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._active: CPUTextLease | None = None
        self._waiters: deque[tuple[str, str]] = deque()
        self._runtime_tokens: dict[str, tuple[str, RuntimeAttemptTokens]] = {}

    def acquire(
        self,
        owner_key: str,
        *,
        snapshot_supplier: Callable[[], HostAdmissionSnapshot],
        cancel_requested: Callable[[], bool] = lambda: False,
        poll_interval: float = 0.1,
        timeout: float | None = None,
    ) -> tuple[CPUTextLease | None, AdmissionDecision]:
        """Wait FIFO for an admitted lease; cancellation has no side effects."""
        if not isinstance(owner_key, str) or not owner_key:
            raise ValueError("CPU text lane owner key is required")
        waiter_token = uuid.uuid4().hex
        deadline = (
            None if timeout is None
            else time.monotonic() + max(0.0, float(timeout))
        )
        last = AdmissionDecision(False, "lane_busy")
        with self._condition:
            self._waiters.append((waiter_token, owner_key))
            try:
                while True:
                    if cancel_requested():
                        return None, AdmissionDecision(False, "cancelled")
                    is_head = bool(
                        self._waiters and self._waiters[0][0] == waiter_token
                    )
                    if is_head and self._active is None:
                        try:
                            snapshot = snapshot_supplier()
                        except Exception:
                            # Measurement failure is admission failure, never a
                            # reason to guess around a resident GPU workload.
                            last = AdmissionDecision(False, "memory_pressure")
                        else:
                            last = host_admission_decision(snapshot)
                        if last.admitted:
                            lease = CPUTextLease(
                                self, owner_key, uuid.uuid4().hex,
                            )
                            self._active = lease
                            self._waiters.popleft()
                            return lease, last
                    else:
                        last = AdmissionDecision(False, "lane_busy")
                    if deadline is not None and time.monotonic() >= deadline:
                        return None, last
                    wait_for = max(0.01, float(poll_interval))
                    if deadline is not None:
                        wait_for = min(
                            wait_for, max(0.0, deadline - time.monotonic()),
                        )
                    self._condition.wait(timeout=wait_for)
            finally:
                self._waiters = deque(
                    item for item in self._waiters if item[0] != waiter_token
                )

    def release(self, lease: CPUTextLease) -> None:
        with self._condition:
            if self._active is not lease or self._active.token != lease.token:
                return
            tokens = self._runtime_tokens.get(lease.owner_key)
            if tokens is not None and tokens[0] == lease.token:
                self._runtime_tokens.pop(lease.owner_key, None)
            self._active = None
            self._condition.notify_all()

    def bind_runtime_tokens(
        self,
        lease: CPUTextLease,
        *,
        runtime_generation: int,
        runtime_attempt_id: int,
    ) -> RuntimeAttemptTokens:
        tokens = RuntimeAttemptTokens(runtime_generation, runtime_attempt_id)
        with self._condition:
            if self._active is not lease or lease.released:
                raise RuntimeError("CPU text lease is no longer active")
            self._runtime_tokens[lease.owner_key] = (lease.token, tokens)
            return tokens

    def runtime_tokens(self, owner_key: str) -> RuntimeAttemptTokens | None:
        with self._condition:
            value = self._runtime_tokens.get(owner_key)
            return None if value is None else value[1]

    def clear_runtime_tokens(self, lease: CPUTextLease) -> None:
        with self._condition:
            value = self._runtime_tokens.get(lease.owner_key)
            if value is not None and value[0] == lease.token:
                self._runtime_tokens.pop(lease.owner_key, None)

    def notify(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def aggregate_snapshot(self) -> dict[str, int]:
        """Return only anonymous process-local counts for global summaries."""
        with self._condition:
            return {
                "cpu_text_running": int(self._active is not None),
                "cpu_text_waiting": len(self._waiters),
            }


class PreemptionGate:
    """Bound restart count/cooldown around the pure break-even predicate."""

    def __init__(
        self,
        *,
        maximum_restarts: int = 1,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.maximum_restarts = max(0, int(maximum_restarts))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._lock = threading.Lock()
        self._restarts: dict[str, int] = {}
        self._last_restart: dict[str, float] = {}

    def permits(
        self,
        owner_key: str,
        estimate: BreakEvenEstimate,
        *,
        now: float | None = None,
    ) -> bool:
        if not should_preempt_cpu_attempt(estimate):
            return False
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            if self._restarts.get(owner_key, 0) >= self.maximum_restarts:
                return False
            previous = self._last_restart.get(owner_key)
            return previous is None or current - previous >= self.cooldown_seconds

    def record(self, owner_key: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            count = self._restarts.get(owner_key, 0)
            if count >= self.maximum_restarts:
                raise RuntimeError("CPU text restart limit reached")
            self._restarts[owner_key] = count + 1
            self._last_restart[owner_key] = current

    def clear(self, owner_key: str) -> None:
        with self._lock:
            self._restarts.pop(owner_key, None)
            self._last_restart.pop(owner_key, None)
