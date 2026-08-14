"""Fail-closed GPU-idle attribution for background sample campaigns.

This internal service deliberately does not change the public live-stats schema,
queue work, or preempt a running generation.  It answers one narrow question:
whether repeated GPU-0 snapshots prove that only Maestro-owned processes are
present and device utilization has remained low for a bounded window.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import time
from types import ModuleType
from typing import Any, Callable


DEFAULT_MAX_GPU_UTILIZATION_PERCENT = 10.0
DEFAULT_REQUIRED_CONSECUTIVE_SNAPSHOTS = 3
DEFAULT_MINIMUM_IDLE_WINDOW_SECONDS = 4.0
DEFAULT_MAXIMUM_SAMPLE_GAP_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class GpuIdleSnapshot:
    """Content-free, PID-free result of one process-attributed GPU query."""

    available: bool
    attribution_complete: bool
    idle: bool
    reason: str
    gpu_utilization_percent: float | None = None
    observed_process_count: int = 0
    foreign_process_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("GPU idle snapshot reason is invalid")
        if any(type(value) is not bool for value in (
            self.available,
            self.attribution_complete,
            self.idle,
        )):
            raise ValueError("GPU idle snapshot flags are invalid")
        if (
            type(self.observed_process_count) is not int
            or type(self.foreign_process_count) is not int
            or self.observed_process_count < 0
            or not 0 <= self.foreign_process_count <= self.observed_process_count
        ):
            raise ValueError("GPU idle snapshot process counts are invalid")
        if self.gpu_utilization_percent is not None and (
            isinstance(self.gpu_utilization_percent, bool)
            or not isinstance(self.gpu_utilization_percent, (int, float))
            or not math.isfinite(self.gpu_utilization_percent)
            or not 0 <= self.gpu_utilization_percent <= 100
        ):
            raise ValueError("GPU idle snapshot utilization is invalid")
        if not self.available:
            if (
                self.attribution_complete
                or self.idle
                or self.gpu_utilization_percent is not None
                or self.observed_process_count
                or self.foreign_process_count
            ):
                raise ValueError("Unavailable GPU snapshot contains observations")
            return
        if not self.attribution_complete:
            raise ValueError("Available GPU snapshot has incomplete attribution")
        if self.gpu_utilization_percent is None:
            raise ValueError("Available GPU snapshot has no utilization")
        if self.idle and (
            self.reason != "idle_snapshot" or self.foreign_process_count != 0
        ):
            raise ValueError("Idle GPU snapshot is contradictory")
        if self.reason == "foreign_gpu_process" and self.foreign_process_count == 0:
            raise ValueError("Foreign-process snapshot has no foreign process")


@dataclass(frozen=True, slots=True)
class GpuIdleDecision:
    """State of the sustained idle window after observing one snapshot."""

    ready: bool
    reason: str
    consecutive_idle_snapshots: int
    idle_window_seconds: float


def _unavailable(reason: str) -> GpuIdleSnapshot:
    return GpuIdleSnapshot(
        available=False,
        attribution_complete=False,
        idle=False,
        reason=reason,
    )


def _resolve_modules(
    nvml_module: ModuleType | Any | None,
    psutil_module: ModuleType | Any | None,
) -> tuple[Any, Any] | None:
    try:
        if nvml_module is None:
            import pynvml as resolved_nvml

            nvml_module = resolved_nvml
        if psutil_module is None:
            import psutil as resolved_psutil

            psutil_module = resolved_psutil
    except Exception:
        return None
    return nvml_module, psutil_module


def _allowed_process_ids(psutil_module: Any, own_pid: int) -> set[int]:
    if type(own_pid) is not int or own_pid <= 0:
        raise ValueError("own PID is invalid")
    process = psutil_module.Process(own_pid)
    children = process.children(recursive=True)
    allowed = {own_pid}
    for child in children:
        pid = getattr(child, "pid", None)
        if type(pid) is not int or pid <= 0:
            raise ValueError("child PID is ambiguous")
        allowed.add(pid)
    return allowed


def _query_processes(nvml_module: Any, handle: Any, kind: str) -> tuple[Any, ...]:
    unsupported_types = tuple(
        candidate
        for candidate in (
            getattr(nvml_module, "NVMLError_FunctionNotFound", None),
            getattr(nvml_module, "NVMLError_NotSupported", None),
        )
        if isinstance(candidate, type) and issubclass(candidate, Exception)
    )
    for suffix in ("_v3", "_v2", ""):
        candidate = getattr(
            nvml_module,
            f"nvmlDeviceGet{kind}RunningProcesses{suffix}",
            None,
        )
        if callable(candidate):
            try:
                return tuple(candidate(handle))
            except Exception as error:
                if unsupported_types and isinstance(error, unsupported_types):
                    continue
                raise
    raise RuntimeError(f"NVML {kind.lower()} process attribution is unavailable")


def capture_gpu_idle_snapshot(
    *,
    nvml_module: ModuleType | Any | None = None,
    psutil_module: ModuleType | Any | None = None,
    own_pid: int | None = None,
    max_gpu_utilization_percent: float = DEFAULT_MAX_GPU_UTILIZATION_PERCENT,
) -> GpuIdleSnapshot:
    """Capture one GPU-0 snapshot; every ambiguity returns an explicit denial."""

    if (
        isinstance(max_gpu_utilization_percent, bool)
        or not isinstance(max_gpu_utilization_percent, (int, float))
        or not 0 <= max_gpu_utilization_percent <= 100
    ):
        raise ValueError("GPU utilization threshold is invalid")
    resolved = _resolve_modules(nvml_module, psutil_module)
    if resolved is None:
        return _unavailable("telemetry_import_unavailable")
    nvml, psutil = resolved

    initialized = False
    try:
        nvml.nvmlInit()
        initialized = True
        handle = nvml.nvmlDeviceGetHandleByIndex(0)
        utilization = float(nvml.nvmlDeviceGetUtilizationRates(handle).gpu)
        if not 0 <= utilization <= 100:
            raise ValueError("GPU utilization is invalid")
        resolved_own_pid = os.getpid() if own_pid is None else own_pid
        allowed = _allowed_process_ids(psutil, resolved_own_pid)
        records = _query_processes(nvml, handle, "Compute") + _query_processes(
            nvml,
            handle,
            "Graphics",
        )
        observed: set[int] = set()
        for record in records:
            pid = getattr(record, "pid", None)
            if type(pid) is not int or pid <= 0:
                raise ValueError("NVML process identity is ambiguous")
            observed.add(pid)
        if _allowed_process_ids(psutil, resolved_own_pid) != allowed:
            raise ValueError("Maestro process tree changed during attribution")
    except Exception:
        return _unavailable("telemetry_or_attribution_unavailable")
    finally:
        shutdown = getattr(nvml, "nvmlShutdown", None)
        if initialized and callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass

    foreign = observed - allowed
    if foreign:
        return GpuIdleSnapshot(
            available=True,
            attribution_complete=True,
            idle=False,
            reason="foreign_gpu_process",
            gpu_utilization_percent=utilization,
            observed_process_count=len(observed),
            foreign_process_count=len(foreign),
        )
    if utilization > float(max_gpu_utilization_percent):
        return GpuIdleSnapshot(
            available=True,
            attribution_complete=True,
            idle=False,
            reason="gpu_utilization_busy",
            gpu_utilization_percent=utilization,
            observed_process_count=len(observed),
        )
    return GpuIdleSnapshot(
        available=True,
        attribution_complete=True,
        idle=True,
        reason="idle_snapshot",
        gpu_utilization_percent=utilization,
        observed_process_count=len(observed),
    )


class SustainedGpuIdleGate:
    """Require nearby consecutive idle snapshots across a minimum duration."""

    def __init__(
        self,
        *,
        required_consecutive_snapshots: int = DEFAULT_REQUIRED_CONSECUTIVE_SNAPSHOTS,
        minimum_idle_window_seconds: float = DEFAULT_MINIMUM_IDLE_WINDOW_SECONDS,
        maximum_sample_gap_seconds: float = DEFAULT_MAXIMUM_SAMPLE_GAP_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(required_consecutive_snapshots) is not int or required_consecutive_snapshots < 2:
            raise ValueError("consecutive GPU snapshot count is invalid")
        for value, field in (
            (minimum_idle_window_seconds, "minimum GPU idle window"),
            (maximum_sample_gap_seconds, "maximum GPU sample gap"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{field} is invalid")
        if not callable(monotonic):
            raise ValueError("monotonic clock is invalid")
        self._required = required_consecutive_snapshots
        self._minimum_window = float(minimum_idle_window_seconds)
        self._maximum_gap = float(maximum_sample_gap_seconds)
        self._monotonic = monotonic
        self._consecutive = 0
        self._window_started_at: float | None = None
        self._last_observed_at: float | None = None

    def reset(self) -> None:
        self._consecutive = 0
        self._window_started_at = None
        self._last_observed_at = None

    def observe(
        self,
        snapshot: GpuIdleSnapshot,
        *,
        observed_at: float | None = None,
    ) -> GpuIdleDecision:
        if not isinstance(snapshot, GpuIdleSnapshot):
            raise ValueError("GPU idle snapshot is invalid")
        try:
            now = float(self._monotonic() if observed_at is None else observed_at)
        except Exception:
            self.reset()
            return GpuIdleDecision(False, "invalid_observation_time", 0, 0.0)
        if not math.isfinite(now):
            self.reset()
            return GpuIdleDecision(False, "invalid_observation_time", 0, 0.0)
        if snapshot.idle and (not snapshot.available or not snapshot.attribution_complete):
            self.reset()
            return GpuIdleDecision(False, "invalid_idle_snapshot", 0, 0.0)
        if not snapshot.idle:
            reason = snapshot.reason
            self.reset()
            return GpuIdleDecision(False, reason, 0, 0.0)
        if self._last_observed_at is not None:
            gap = now - self._last_observed_at
            if gap <= 0 or gap > self._maximum_gap:
                self.reset()
        if self._window_started_at is None:
            self._window_started_at = now
        self._last_observed_at = now
        self._consecutive += 1
        elapsed = max(0.0, now - self._window_started_at)
        ready = self._consecutive >= self._required and elapsed >= self._minimum_window
        return GpuIdleDecision(
            ready=ready,
            reason="idle_window_ready" if ready else "idle_window_pending",
            consecutive_idle_snapshots=self._consecutive,
            idle_window_seconds=elapsed,
        )
