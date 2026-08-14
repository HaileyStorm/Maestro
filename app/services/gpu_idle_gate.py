"""Fail-closed GPU-idle attribution for background sample campaigns.

This internal service deliberately does not change the public live-stats schema,
queue work, or preempt a running generation.  It answers one narrow question:
whether repeated GPU-0 snapshots prove that no significant external GPU work is
present and device utilization has remained low for a bounded window.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import time
from types import ModuleType
from typing import Any, Callable


GIBIBYTE = 1024 ** 3
DEFAULT_MAX_GPU_UTILIZATION_PERCENT = 25.0
DEFAULT_MAX_FOREIGN_COMPUTE_UTILIZATION_PERCENT = 1.0
DEFAULT_PROCESS_UTILIZATION_FRESHNESS_SECONDS = 3.0
DEFAULT_REQUIRED_CONSECUTIVE_SNAPSHOTS = 5
DEFAULT_MINIMUM_IDLE_WINDOW_SECONDS = 8.0
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
        if self.idle and self.reason != "idle_snapshot":
            raise ValueError("Idle GPU snapshot is contradictory")
        if self.reason.startswith("foreign_") and self.foreign_process_count == 0:
            raise ValueError("Foreign-work snapshot has no foreign process")


@dataclass(frozen=True, slots=True)
class GpuIdleDecision:
    """State of the sustained idle window after observing one snapshot."""

    ready: bool
    reason: str
    consecutive_idle_snapshots: int
    idle_window_seconds: float


@dataclass(frozen=True, slots=True)
class ForeignGpuSignificance:
    """PID-free preemption evidence projected from one attributed capture."""

    known: bool
    significant: bool
    reason: str

    def __post_init__(self) -> None:
        significant_reasons = {
            "foreign_compute_memory",
            "foreign_compute_activity",
            "foreign_graphics_memory",
        }
        if type(self.known) is not bool or type(self.significant) is not bool:
            raise ValueError("foreign GPU significance flags are invalid")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("foreign GPU significance reason is invalid")
        if self.significant and not self.known:
            raise ValueError("unknown foreign GPU significance cannot be positive")
        if self.significant and self.reason not in significant_reasons:
            raise ValueError("positive foreign GPU significance reason is invalid")
        if not self.significant and self.reason in significant_reasons:
            raise ValueError("negative foreign GPU significance reason is invalid")


def _unavailable(reason: str) -> GpuIdleSnapshot:
    return GpuIdleSnapshot(
        available=False,
        attribution_complete=False,
        idle=False,
        reason=reason,
    )


def _unknown_significance(reason: str) -> ForeignGpuSignificance:
    return ForeignGpuSignificance(
        known=False,
        significant=False,
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


def _query_process_utilization(
    nvml_module: Any,
    handle: Any,
) -> tuple[Any, ...]:
    query = getattr(nvml_module, "nvmlDeviceGetProcessUtilization", None)
    if not callable(query):
        raise RuntimeError("NVML process utilization is unavailable")
    not_found = getattr(nvml_module, "NVMLError_NotFound", None)
    try:
        # NVML permits either zero or a timestamp returned by an earlier NVML
        # query. A wall-clock-derived cursor can skip active samples, so this
        # stateless capture asks for the complete retained sample buffer.
        return tuple(query(handle, 0))
    except Exception as error:
        if (
            isinstance(not_found, type)
            and issubclass(not_found, Exception)
            and isinstance(error, not_found)
        ):
            # NVML uses NOT_FOUND as positive evidence that no process had
            # non-zero utilization after the supplied timestamp.
            return ()
        raise


def _memory_info(nvml_module: Any, handle: Any) -> tuple[int, int]:
    info = nvml_module.nvmlDeviceGetMemoryInfo(handle)
    total = getattr(info, "total", None)
    used = getattr(info, "used", None)
    if (
        type(total) is not int
        or type(used) is not int
        or total <= 0
        or used < 0
        or used > total
    ):
        raise ValueError("GPU memory totals are ambiguous")
    return total, used


def _unknown_memory_values(nvml_module: Any) -> set[int]:
    values = {-1}
    for name in (
        "NVML_VALUE_NOT_AVAILABLE_ulonglong",
        "NVML_VALUE_NOT_AVAILABLE_uint64",
    ):
        candidate = getattr(nvml_module, name, None)
        candidate = getattr(candidate, "value", candidate)
        if type(candidate) is int:
            values.add(candidate)
    return values


def _process_memory_bytes(
    record: Any,
    *,
    nvml_module: Any,
    total_bytes: int,
) -> int | None:
    value = getattr(record, "usedGpuMemory", None)
    if value is None or value in _unknown_memory_values(nvml_module):
        return None
    if type(value) is not int or value < 0 or value > total_bytes:
        raise ValueError("GPU process memory is ambiguous")
    return value


def _index_processes(
    records: tuple[Any, ...],
    *,
    nvml_module: Any,
    total_bytes: int,
) -> dict[int, int | None]:
    indexed: dict[int, int | None] = {}
    for record in records:
        pid = getattr(record, "pid", None)
        if type(pid) is not int or pid <= 0:
            raise ValueError("NVML process identity is ambiguous")
        memory_bytes = _process_memory_bytes(
            record,
            nvml_module=nvml_module,
            total_bytes=total_bytes,
        )
        if pid in indexed and indexed[pid] != memory_bytes:
            raise ValueError("NVML process memory changed during attribution")
        indexed[pid] = memory_bytes
    return indexed


def _utilization_percent(sample: Any) -> float:
    values: list[float] = []
    for field in ("smUtil", "memUtil", "encUtil", "decUtil"):
        value = getattr(sample, field, None)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 100
        ):
            raise ValueError("GPU process utilization is ambiguous")
        values.append(float(value))
    return max(values)


def _capture_gpu_classifications(
    *,
    nvml_module: ModuleType | Any | None = None,
    psutil_module: ModuleType | Any | None = None,
    own_pid: int | None = None,
    max_gpu_utilization_percent: float = DEFAULT_MAX_GPU_UTILIZATION_PERCENT,
    max_foreign_compute_utilization_percent: float = (
        DEFAULT_MAX_FOREIGN_COMPUTE_UTILIZATION_PERCENT
    ),
    process_utilization_freshness_seconds: float = (
        DEFAULT_PROCESS_UTILIZATION_FRESHNESS_SECONDS
    ),
    wall_clock: Callable[[], float] = time.time,
) -> tuple[GpuIdleSnapshot, ForeignGpuSignificance]:
    """Capture once, then project release readiness and foreign significance."""

    for value, field, maximum in (
        (
            max_gpu_utilization_percent,
            "GPU utilization threshold",
            DEFAULT_MAX_GPU_UTILIZATION_PERCENT,
        ),
        (
            max_foreign_compute_utilization_percent,
            "foreign compute utilization threshold",
            DEFAULT_MAX_FOREIGN_COMPUTE_UTILIZATION_PERCENT,
        ),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 100
            or value > maximum
        ):
            raise ValueError(f"{field} is invalid")
    if (
        isinstance(process_utilization_freshness_seconds, bool)
        or not isinstance(process_utilization_freshness_seconds, (int, float))
        or not math.isfinite(process_utilization_freshness_seconds)
        or process_utilization_freshness_seconds <= 0
        or process_utilization_freshness_seconds
        > DEFAULT_PROCESS_UTILIZATION_FRESHNESS_SECONDS
    ):
        raise ValueError("process utilization freshness is invalid")
    if not callable(wall_clock):
        raise ValueError("GPU telemetry clock is invalid")
    resolved = _resolve_modules(nvml_module, psutil_module)
    if resolved is None:
        reason = "telemetry_import_unavailable"
        return _unavailable(reason), _unknown_significance(reason)
    nvml, psutil = resolved

    initialized = False
    try:
        nvml.nvmlInit()
        initialized = True
        handle = nvml.nvmlDeviceGetHandleByIndex(0)
        raw_utilization = nvml.nvmlDeviceGetUtilizationRates(handle).gpu
        if (
            isinstance(raw_utilization, bool)
            or not isinstance(raw_utilization, (int, float))
            or not math.isfinite(raw_utilization)
            or not 0 <= raw_utilization <= 100
        ):
            raise ValueError("GPU utilization is invalid")
        utilization = float(raw_utilization)
        total_memory_bytes, used_memory_bytes = _memory_info(nvml, handle)
        raw_observed_at = wall_clock()
        if (
            isinstance(raw_observed_at, bool)
            or not isinstance(raw_observed_at, (int, float))
            or not math.isfinite(raw_observed_at)
            or raw_observed_at < 0
        ):
            raise ValueError("GPU telemetry clock is invalid")
        observed_at = float(raw_observed_at)
        observed_at_microseconds = int(observed_at * 1_000_000)
        freshness_microseconds = int(
            float(process_utilization_freshness_seconds) * 1_000_000
        )
        oldest_fresh_timestamp = max(
            0,
            observed_at_microseconds - freshness_microseconds,
        )
        resolved_own_pid = os.getpid() if own_pid is None else own_pid
        allowed = _allowed_process_ids(psutil, resolved_own_pid)
        compute = _index_processes(
            _query_processes(nvml, handle, "Compute"),
            nvml_module=nvml,
            total_bytes=total_memory_bytes,
        )
        graphics = _index_processes(
            _query_processes(nvml, handle, "Graphics"),
            nvml_module=nvml,
            total_bytes=total_memory_bytes,
        )
        process_utilization = _query_process_utilization(
            nvml,
            handle,
        )
        observed = set(compute) | set(graphics)
        for pid in set(compute) & set(graphics):
            if compute[pid] != graphics[pid]:
                raise ValueError("Cross-API GPU process memory is ambiguous")
        fresh_utilization_by_pid: dict[int, float] = {}
        stale_utilization_pids: set[int] = set()
        for sample in process_utilization:
            pid = getattr(sample, "pid", None)
            timestamp = getattr(sample, "timeStamp", None)
            if (
                type(pid) is not int
                or pid <= 0
                or type(timestamp) is not int
                or timestamp < 0
                or timestamp > observed_at_microseconds
            ):
                raise ValueError("GPU process utilization attribution is ambiguous")
            if pid not in observed:
                raise ValueError("GPU process utilization raced process attribution")
            if timestamp < oldest_fresh_timestamp:
                stale_utilization_pids.add(pid)
                continue
            fresh_utilization_by_pid[pid] = max(
                fresh_utilization_by_pid.get(pid, 0.0),
                _utilization_percent(sample),
            )
        if _allowed_process_ids(psutil, resolved_own_pid) != allowed:
            raise ValueError("Maestro process tree changed during attribution")

        foreign_compute = set(compute) - allowed
        # A PID reported by both APIs is conservatively a compute process.
        foreign_graphics = set(graphics) - set(compute) - allowed
        foreign = foreign_compute | foreign_graphics
        if any(
            pid in stale_utilization_pids and pid not in fresh_utilization_by_pid
            for pid in foreign_compute
        ):
            raise ValueError("Foreign compute activity freshness is unavailable")

        compute_memory_bytes = 0
        for pid in foreign_compute:
            process_bytes = compute[pid]
            if process_bytes is None:
                raise ValueError("Foreign compute memory is unavailable")
            compute_memory_bytes += process_bytes

        known_memory_by_pid: dict[int, int] = {}
        for pid in observed:
            process_bytes = compute.get(pid) if pid in compute else graphics.get(pid)
            if process_bytes is not None:
                known_memory_by_pid[pid] = process_bytes
        known_process_bytes = sum(known_memory_by_pid.values())
        if known_process_bytes > used_memory_bytes:
            raise ValueError("Aggregate GPU process memory is ambiguous")

        graphics_memory_bytes = sum(
            graphics[pid]
            for pid in foreign_graphics
            if graphics[pid] is not None
        )
        if any(graphics[pid] is None for pid in foreign_graphics):
            # WDDM omits per-process bytes. The device-used residual is a
            # conservative aggregate upper bound for all unknown contexts.
            graphics_memory_bytes += used_memory_bytes - known_process_bytes
    except Exception:
        reason = "telemetry_or_attribution_unavailable"
        return _unavailable(reason), _unknown_significance(reason)
    finally:
        shutdown = getattr(nvml, "nvmlShutdown", None)
        if initialized and callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass

    compute_memory_threshold = min(
        GIBIBYTE,
        total_memory_bytes * 0.10,
    )
    compute_memory_significant = compute_memory_bytes >= compute_memory_threshold
    compute_activity_significant = any(
        fresh_utilization_by_pid.get(pid, 0.0)
        > float(max_foreign_compute_utilization_percent)
        for pid in foreign_compute
    )
    graphics_memory_threshold = min(
        4 * GIBIBYTE,
        total_memory_bytes * 0.15,
    )
    graphics_memory_significant = (
        graphics_memory_bytes > graphics_memory_threshold
    )

    if compute_memory_significant:
        significance = ForeignGpuSignificance(
            known=True,
            significant=True,
            reason="foreign_compute_memory",
        )
    elif compute_activity_significant:
        significance = ForeignGpuSignificance(
            known=True,
            significant=True,
            reason="foreign_compute_activity",
        )
    elif graphics_memory_significant:
        significance = ForeignGpuSignificance(
            known=True,
            significant=True,
            reason="foreign_graphics_memory",
        )
    elif utilization > float(max_gpu_utilization_percent):
        significance = _unknown_significance("device_utilization_only")
    else:
        significance = ForeignGpuSignificance(
            known=True,
            significant=False,
            reason="no_significant_foreign_work",
        )

    if compute_memory_significant:
        snapshot = GpuIdleSnapshot(
            available=True,
            attribution_complete=True,
            idle=False,
            reason="foreign_compute_memory",
            gpu_utilization_percent=utilization,
            observed_process_count=len(observed),
            foreign_process_count=len(foreign),
        )
    elif compute_activity_significant:
        snapshot = GpuIdleSnapshot(
            available=True,
            attribution_complete=True,
            idle=False,
            reason="foreign_compute_activity",
            gpu_utilization_percent=utilization,
            observed_process_count=len(observed),
            foreign_process_count=len(foreign),
        )
    elif utilization > float(max_gpu_utilization_percent):
        snapshot = GpuIdleSnapshot(
            available=True,
            attribution_complete=True,
            idle=False,
            reason="gpu_utilization_busy",
            gpu_utilization_percent=utilization,
            observed_process_count=len(observed),
            foreign_process_count=len(foreign),
        )
    elif graphics_memory_significant:
        snapshot = GpuIdleSnapshot(
            available=True,
            attribution_complete=True,
            idle=False,
            reason="foreign_graphics_memory",
            gpu_utilization_percent=utilization,
            observed_process_count=len(observed),
            foreign_process_count=len(foreign),
        )
    else:
        snapshot = GpuIdleSnapshot(
            available=True,
            attribution_complete=True,
            idle=True,
            reason="idle_snapshot",
            gpu_utilization_percent=utilization,
            observed_process_count=len(observed),
            foreign_process_count=len(foreign),
        )
    return snapshot, significance


def capture_gpu_idle_snapshot(
    *,
    nvml_module: ModuleType | Any | None = None,
    psutil_module: ModuleType | Any | None = None,
    own_pid: int | None = None,
    max_gpu_utilization_percent: float = DEFAULT_MAX_GPU_UTILIZATION_PERCENT,
    max_foreign_compute_utilization_percent: float = (
        DEFAULT_MAX_FOREIGN_COMPUTE_UTILIZATION_PERCENT
    ),
    process_utilization_freshness_seconds: float = (
        DEFAULT_PROCESS_UTILIZATION_FRESHNESS_SECONDS
    ),
    wall_clock: Callable[[], float] = time.time,
) -> GpuIdleSnapshot:
    """Capture one GPU-0 snapshot; every ambiguity denies release."""

    snapshot, _significance = _capture_gpu_classifications(
        nvml_module=nvml_module,
        psutil_module=psutil_module,
        own_pid=own_pid,
        max_gpu_utilization_percent=max_gpu_utilization_percent,
        max_foreign_compute_utilization_percent=(
            max_foreign_compute_utilization_percent
        ),
        process_utilization_freshness_seconds=(
            process_utilization_freshness_seconds
        ),
        wall_clock=wall_clock,
    )
    return snapshot


def capture_foreign_gpu_significance(
    *,
    nvml_module: ModuleType | Any | None = None,
    psutil_module: ModuleType | Any | None = None,
    own_pid: int | None = None,
    max_gpu_utilization_percent: float = DEFAULT_MAX_GPU_UTILIZATION_PERCENT,
    max_foreign_compute_utilization_percent: float = (
        DEFAULT_MAX_FOREIGN_COMPUTE_UTILIZATION_PERCENT
    ),
    process_utilization_freshness_seconds: float = (
        DEFAULT_PROCESS_UTILIZATION_FRESHNESS_SECONDS
    ),
    wall_clock: Callable[[], float] = time.time,
) -> ForeignGpuSignificance:
    """Return only positively attributed foreign-work preemption evidence."""

    _snapshot, significance = _capture_gpu_classifications(
        nvml_module=nvml_module,
        psutil_module=psutil_module,
        own_pid=own_pid,
        max_gpu_utilization_percent=max_gpu_utilization_percent,
        max_foreign_compute_utilization_percent=(
            max_foreign_compute_utilization_percent
        ),
        process_utilization_freshness_seconds=(
            process_utilization_freshness_seconds
        ),
        wall_clock=wall_clock,
    )
    return significance


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
        if (
            type(required_consecutive_snapshots) is not int
            or required_consecutive_snapshots < DEFAULT_REQUIRED_CONSECUTIVE_SNAPSHOTS
        ):
            raise ValueError("consecutive GPU snapshot count is invalid")
        for value, field, lower_bound, upper_bound in (
            (
                minimum_idle_window_seconds,
                "minimum GPU idle window",
                DEFAULT_MINIMUM_IDLE_WINDOW_SECONDS,
                None,
            ),
            (
                maximum_sample_gap_seconds,
                "maximum GPU sample gap",
                None,
                DEFAULT_MAXIMUM_SAMPLE_GAP_SECONDS,
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
                or (lower_bound is not None and value < lower_bound)
                or (upper_bound is not None and value > upper_bound)
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
            raw_now = self._monotonic() if observed_at is None else observed_at
        except Exception:
            self.reset()
            return GpuIdleDecision(False, "invalid_observation_time", 0, 0.0)
        if (
            isinstance(raw_now, bool)
            or not isinstance(raw_now, (int, float))
            or not math.isfinite(raw_now)
        ):
            self.reset()
            return GpuIdleDecision(False, "invalid_observation_time", 0, 0.0)
        now = float(raw_now)
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
