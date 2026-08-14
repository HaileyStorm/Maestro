from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.gpu_idle_gate import (  # noqa: E402
    GpuIdleSnapshot,
    SustainedGpuIdleGate,
    capture_gpu_idle_snapshot,
)


@dataclass
class _ProcessRecord:
    pid: object
    usedGpuMemory: object = None


@dataclass
class _Utilization:
    gpu: object


@dataclass
class _Child:
    pid: object


class _Process:
    def __init__(self, children):
        self._children = children

    def children(self, *, recursive):
        if recursive is not True:
            raise AssertionError("recursive PID attribution is required")
        return list(self._children)


class _Psutil:
    def __init__(self, children=(), error=None):
        self._children = tuple(children)
        self._error = error

    def Process(self, pid):
        if self._error is not None:
            raise self._error
        if pid != 100:
            raise AssertionError("unexpected Maestro PID")
        return _Process(self._children)


class _ChangingPsutil(_Psutil):
    def __init__(self):
        super().__init__()
        self._calls = 0

    def Process(self, pid):
        self._calls += 1
        children = () if self._calls == 1 else (_Child(101),)
        return _Process(children)


class _Nvml:
    class NVMLError_FunctionNotFound(Exception):
        pass

    class NVMLError_NotSupported(Exception):
        pass

    def __init__(self, *, utilization=2, compute=(), graphics=()):
        self.utilization = utilization
        self.compute = tuple(compute)
        self.graphics = tuple(graphics)
        self.initialized = False
        self.shutdown_calls = 0

    def nvmlInit(self):
        self.initialized = True

    def nvmlDeviceGetHandleByIndex(self, index):
        if not self.initialized or index != 0:
            raise AssertionError("GPU 0 must be queried after NVML initialization")
        return "gpu-0"

    def nvmlDeviceGetUtilizationRates(self, handle):
        if handle != "gpu-0":
            raise AssertionError("unexpected GPU handle")
        return _Utilization(self.utilization)

    def nvmlDeviceGetComputeRunningProcesses_v3(self, handle):
        return list(self.compute)

    def nvmlDeviceGetGraphicsRunningProcesses_v3(self, handle):
        return list(self.graphics)

    def nvmlShutdown(self):
        self.shutdown_calls += 1


def _idle_snapshot() -> GpuIdleSnapshot:
    return GpuIdleSnapshot(
        available=True,
        attribution_complete=True,
        idle=True,
        reason="idle_snapshot",
        gpu_utilization_percent=2.0,
    )


class GpuIdleSnapshotTests(unittest.TestCase):
    def test_current_process_and_recursive_children_are_allowed(self):
        nvml = _Nvml(
            compute=(_ProcessRecord(100),),
            graphics=(_ProcessRecord(101, usedGpuMemory=None),),
        )
        snapshot = capture_gpu_idle_snapshot(
            nvml_module=nvml,
            psutil_module=_Psutil(children=(_Child(101),)),
            own_pid=100,
        )
        self.assertTrue(snapshot.available)
        self.assertTrue(snapshot.attribution_complete)
        self.assertTrue(snapshot.idle)
        self.assertEqual(snapshot.observed_process_count, 2)
        self.assertEqual(snapshot.foreign_process_count, 0)
        self.assertEqual(nvml.shutdown_calls, 1)

    def test_any_foreign_compute_or_graphics_pid_denies_even_without_memory(self):
        cases = (
            _Nvml(compute=(_ProcessRecord(200),)),
            _Nvml(graphics=(_ProcessRecord(200, usedGpuMemory=None),)),
        )
        for nvml in cases:
            with self.subTest(kind="foreign"):
                snapshot = capture_gpu_idle_snapshot(
                    nvml_module=nvml,
                    psutil_module=_Psutil(),
                    own_pid=100,
                )
                self.assertFalse(snapshot.idle)
                self.assertEqual(snapshot.reason, "foreign_gpu_process")
                self.assertEqual(snapshot.foreign_process_count, 1)

    def test_high_utilization_denies_after_complete_attribution(self):
        snapshot = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(utilization=10.1),
            psutil_module=_Psutil(),
            own_pid=100,
        )
        self.assertTrue(snapshot.available)
        self.assertTrue(snapshot.attribution_complete)
        self.assertFalse(snapshot.idle)
        self.assertEqual(snapshot.reason, "gpu_utilization_busy")

    def test_missing_process_api_pid_ambiguity_and_psutil_failure_deny(self):
        no_graphics = _Nvml()
        no_graphics.nvmlDeviceGetGraphicsRunningProcesses_v3 = None
        cases = (
            (no_graphics, _Psutil()),
            (_Nvml(compute=(_ProcessRecord(None),)), _Psutil()),
            (_Nvml(), _Psutil(error=PermissionError("denied"))),
            (_Nvml(), _Psutil(children=(_Child(None),))),
            (_Nvml(), _ChangingPsutil()),
        )
        for nvml, psutil in cases:
            with self.subTest(case=type(psutil).__name__):
                snapshot = capture_gpu_idle_snapshot(
                    nvml_module=nvml,
                    psutil_module=psutil,
                    own_pid=100,
                )
                self.assertFalse(snapshot.available)
                self.assertFalse(snapshot.attribution_complete)
                self.assertFalse(snapshot.idle)

    def test_v2_process_api_fallback_is_supported_when_v3_is_absent(self):
        nvml = _Nvml(compute=(_ProcessRecord(100),))
        nvml.nvmlDeviceGetComputeRunningProcesses_v2 = (
            nvml.nvmlDeviceGetComputeRunningProcesses_v3
        )
        nvml.nvmlDeviceGetGraphicsRunningProcesses_v2 = (
            nvml.nvmlDeviceGetGraphicsRunningProcesses_v3
        )
        nvml.nvmlDeviceGetComputeRunningProcesses_v3 = None
        nvml.nvmlDeviceGetGraphicsRunningProcesses_v3 = None
        snapshot = capture_gpu_idle_snapshot(
            nvml_module=nvml,
            psutil_module=_Psutil(),
            own_pid=100,
        )
        self.assertTrue(snapshot.idle)

    def test_runtime_not_supported_uses_v2_but_other_query_errors_deny(self):
        for error_type in (
            _Nvml.NVMLError_NotSupported,
            _Nvml.NVMLError_FunctionNotFound,
        ):
            with self.subTest(error_type=error_type.__name__):
                nvml = _Nvml(compute=(_ProcessRecord(100),))
                nvml.nvmlDeviceGetComputeRunningProcesses_v2 = (
                    lambda _handle: list(nvml.compute)
                )
                nvml.nvmlDeviceGetGraphicsRunningProcesses_v2 = (
                    lambda _handle: list(nvml.graphics)
                )

                def unsupported(_handle, selected=error_type):
                    raise selected()

                nvml.nvmlDeviceGetComputeRunningProcesses_v3 = unsupported
                nvml.nvmlDeviceGetGraphicsRunningProcesses_v3 = unsupported
                self.assertTrue(capture_gpu_idle_snapshot(
                    nvml_module=nvml,
                    psutil_module=_Psutil(),
                    own_pid=100,
                ).idle)

        broken = _Nvml()

        def query_failure(_handle):
            raise RuntimeError("driver query failed")

        broken.nvmlDeviceGetGraphicsRunningProcesses_v3 = query_failure
        denied = capture_gpu_idle_snapshot(
            nvml_module=broken,
            psutil_module=_Psutil(),
            own_pid=100,
        )
        self.assertFalse(denied.available)
        self.assertFalse(denied.idle)

    def test_nvml_init_and_handle_failures_deny(self):
        for method in ("nvmlInit", "nvmlDeviceGetHandleByIndex"):
            with self.subTest(method=method):
                nvml = _Nvml()

                def failure(*_args):
                    raise RuntimeError("driver unavailable")

                setattr(nvml, method, failure)
                snapshot = capture_gpu_idle_snapshot(
                    nvml_module=nvml,
                    psutil_module=_Psutil(),
                    own_pid=100,
                )
                self.assertFalse(snapshot.available)
                self.assertFalse(snapshot.attribution_complete)


class SustainedGpuIdleGateTests(unittest.TestCase):
    def test_three_nearby_snapshots_over_four_seconds_release_gate(self):
        gate = SustainedGpuIdleGate()
        first = gate.observe(_idle_snapshot(), observed_at=10.0)
        second = gate.observe(_idle_snapshot(), observed_at=12.0)
        third = gate.observe(_idle_snapshot(), observed_at=14.0)
        self.assertFalse(first.ready)
        self.assertFalse(second.ready)
        self.assertTrue(third.ready)
        self.assertEqual(third.reason, "idle_window_ready")
        self.assertEqual(third.consecutive_idle_snapshots, 3)
        self.assertEqual(third.idle_window_seconds, 4.0)

    def test_busy_foreign_or_unknown_snapshot_resets_the_window(self):
        blockers = (
            GpuIdleSnapshot(True, True, False, "gpu_utilization_busy", 80.0),
            GpuIdleSnapshot(True, True, False, "foreign_gpu_process", 2.0, 1, 1),
            GpuIdleSnapshot(False, False, False, "telemetry_import_unavailable"),
        )
        for blocker in blockers:
            with self.subTest(reason=blocker.reason):
                gate = SustainedGpuIdleGate()
                gate.observe(_idle_snapshot(), observed_at=10.0)
                gate.observe(_idle_snapshot(), observed_at=12.0)
                denied = gate.observe(blocker, observed_at=14.0)
                restarted = gate.observe(_idle_snapshot(), observed_at=16.0)
                self.assertFalse(denied.ready)
                self.assertEqual(denied.reason, blocker.reason)
                self.assertEqual(restarted.consecutive_idle_snapshots, 1)

    def test_large_nonpositive_or_backwards_sample_gap_restarts_window(self):
        for third_time in (12.0, 16.0):
            with self.subTest(third_time=third_time):
                gate = SustainedGpuIdleGate()
                gate.observe(_idle_snapshot(), observed_at=10.0)
                gate.observe(_idle_snapshot(), observed_at=12.0)
                decision = gate.observe(_idle_snapshot(), observed_at=third_time)
                self.assertFalse(decision.ready)
                self.assertEqual(decision.consecutive_idle_snapshots, 1)
                self.assertEqual(decision.idle_window_seconds, 0.0)

    def test_internally_inconsistent_idle_snapshot_fails_closed(self):
        contradictory = (
            (False, False, True, "invalid", None, 0, 0),
            (True, True, True, "foreign_gpu_process", 2.0, 1, 1),
            (True, True, True, "idle_snapshot", None, 0, 0),
            (True, True, True, "idle_snapshot", 2.0, 1, 1),
        )
        for values in contradictory:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    GpuIdleSnapshot(*values)

    def test_nonfinite_observation_time_resets_window(self):
        for observed_at in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(observed_at=observed_at):
                gate = SustainedGpuIdleGate()
                gate.observe(_idle_snapshot(), observed_at=10.0)
                decision = gate.observe(_idle_snapshot(), observed_at=observed_at)
                self.assertFalse(decision.ready)
                self.assertEqual(decision.reason, "invalid_observation_time")
                self.assertEqual(decision.consecutive_idle_snapshots, 0)

    def test_raising_or_nonnumeric_clock_fails_closed(self):
        clocks = (
            lambda: (_ for _ in ()).throw(RuntimeError("clock failed")),
            lambda: "not-a-number",
        )
        for clock in clocks:
            with self.subTest(clock=clock):
                gate = SustainedGpuIdleGate(monotonic=clock)
                decision = gate.observe(_idle_snapshot())
                self.assertFalse(decision.ready)
                self.assertEqual(decision.reason, "invalid_observation_time")
                self.assertEqual(decision.consecutive_idle_snapshots, 0)

    def test_snapshot_reason_must_be_nonempty_text(self):
        for reason in ("", None, 3):
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    GpuIdleSnapshot(False, False, False, reason)  # type: ignore[arg-type]

    def test_nonfinite_timing_configuration_is_rejected(self):
        for kwargs in (
            {"minimum_idle_window_seconds": float("nan")},
            {"minimum_idle_window_seconds": float("inf")},
            {"maximum_sample_gap_seconds": float("nan")},
            {"maximum_sample_gap_seconds": float("inf")},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    SustainedGpuIdleGate(**kwargs)


if __name__ == "__main__":
    unittest.main()
