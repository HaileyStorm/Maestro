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
    GIBIBYTE,
    GpuIdleSnapshot,
    SustainedGpuIdleGate,
    capture_gpu_idle_snapshot,
)


@dataclass
class _ProcessRecord:
    pid: object
    usedGpuMemory: object = 0


@dataclass
class _Utilization:
    gpu: object


@dataclass
class _MemoryInfo:
    total: object
    used: object


@dataclass
class _ProcessUtilization:
    pid: object
    timeStamp: object
    smUtil: object = 0
    memUtil: object = 0
    encUtil: object = 0
    decUtil: object = 0


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

    def name(self):
        raise AssertionError("process-name allowlists are forbidden")

    def exe(self):
        raise AssertionError("executable allowlists are forbidden")

    def cmdline(self):
        raise AssertionError("command-line allowlists are forbidden")


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

    class NVMLError_NotFound(Exception):
        pass

    def __init__(
        self,
        *,
        utilization=2,
        total_memory=32 * GIBIBYTE,
        used_memory=0,
        compute=(),
        graphics=(),
        process_utilization=(),
    ):
        self.utilization = utilization
        self.total_memory = total_memory
        self.used_memory = used_memory
        self.compute = tuple(compute)
        self.graphics = tuple(graphics)
        self.process_utilization = tuple(process_utilization)
        self.initialized = False
        self.shutdown_calls = 0
        self.process_utilization_cutoffs = []

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

    def nvmlDeviceGetMemoryInfo(self, handle):
        if handle != "gpu-0":
            raise AssertionError("unexpected GPU handle")
        return _MemoryInfo(self.total_memory, self.used_memory)

    def nvmlDeviceGetComputeRunningProcesses_v3(self, handle):
        return list(self.compute)

    def nvmlDeviceGetGraphicsRunningProcesses_v3(self, handle):
        return list(self.graphics)

    def nvmlDeviceGetProcessUtilization(self, handle, last_seen_timestamp):
        if handle != "gpu-0":
            raise AssertionError("unexpected GPU handle")
        self.process_utilization_cutoffs.append(last_seen_timestamp)
        return list(self.process_utilization)

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

    def test_ten_incidental_graphics_clients_allow_at_live_desktop_baseline(self):
        per_process = 3 * GIBIBYTE // 10
        graphics = tuple(
            _ProcessRecord(200 + index, per_process)
            for index in range(10)
        )
        snapshot = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                utilization=21,
                used_memory=per_process * 10,
                graphics=graphics,
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertTrue(snapshot.idle)
        self.assertEqual(snapshot.observed_process_count, 10)
        self.assertEqual(snapshot.foreign_process_count, 10)

    def test_graphics_utilization_and_memory_boundaries_are_explicit(self):
        cases = (
            (25, 32 * GIBIBYTE, 4 * GIBIBYTE, True, "idle_snapshot"),
            (25.1, 32 * GIBIBYTE, 0, False, "gpu_utilization_busy"),
            (
                25,
                32 * GIBIBYTE,
                4 * GIBIBYTE + 1,
                False,
                "foreign_graphics_memory",
            ),
            (25, 20 * GIBIBYTE, 3 * GIBIBYTE, True, "idle_snapshot"),
            (
                25,
                20 * GIBIBYTE,
                3 * GIBIBYTE + 1,
                False,
                "foreign_graphics_memory",
            ),
        )
        for utilization, total_bytes, process_bytes, idle, reason in cases:
            with self.subTest(utilization=utilization, process_bytes=process_bytes):
                snapshot = capture_gpu_idle_snapshot(
                    nvml_module=_Nvml(
                        utilization=utilization,
                        total_memory=total_bytes,
                        used_memory=process_bytes,
                        graphics=(_ProcessRecord(200, process_bytes),),
                    ),
                    psutil_module=_Psutil(),
                    own_pid=100,
                    wall_clock=lambda: 10.0,
                )
                self.assertEqual(snapshot.idle, idle)
                self.assertEqual(snapshot.reason, reason)

    def test_fresh_or_large_foreign_compute_denies(self):
        active = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                used_memory=128 * 1024 ** 2,
                compute=(_ProcessRecord(200, 128 * 1024 ** 2),),
                process_utilization=(
                    _ProcessUtilization(200, 9_000_000, smUtil=1.1),
                ),
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertFalse(active.idle)
        self.assertEqual(active.reason, "foreign_compute_activity")

        large = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                used_memory=GIBIBYTE,
                compute=(_ProcessRecord(200, GIBIBYTE),),
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertFalse(large.idle)
        self.assertEqual(large.reason, "foreign_compute_memory")

        ten_percent = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                total_memory=5 * GIBIBYTE,
                used_memory=GIBIBYTE // 2,
                compute=(_ProcessRecord(200, GIBIBYTE // 2),),
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertFalse(ten_percent.idle)
        self.assertEqual(ten_percent.reason, "foreign_compute_memory")

    def test_inactive_small_compute_contexts_allow_without_name_allowlists(self):
        warp_bytes = 215 * 1024 ** 2
        rustdesk_bytes = 612 * 1024 ** 2
        snapshot = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                utilization=21,
                used_memory=warp_bytes + rustdesk_bytes,
                compute=(
                    _ProcessRecord(200, warp_bytes),
                    _ProcessRecord(201, rustdesk_bytes),
                ),
                process_utilization=(
                    _ProcessUtilization(200, 7_000_000, smUtil=1),
                    _ProcessUtilization(201, 9_000_000),
                ),
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertTrue(snapshot.idle)
        self.assertEqual(snapshot.foreign_process_count, 2)

    def test_compute_and_graphics_overlap_is_deduplicated_as_compute(self):
        snapshot = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                used_memory=128 * 1024 ** 2,
                compute=(_ProcessRecord(200, 128 * 1024 ** 2),),
                graphics=(_ProcessRecord(200, 128 * 1024 ** 2),),
                process_utilization=(
                    _ProcessUtilization(200, 9_000_000, memUtil=2),
                ),
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertFalse(snapshot.idle)
        self.assertEqual(snapshot.reason, "foreign_compute_activity")
        self.assertEqual(snapshot.observed_process_count, 1)
        self.assertEqual(snapshot.foreign_process_count, 1)

    def test_conflicting_compute_graphics_overlap_fails_closed(self):
        cases = (
            (0, 5 * GIBIBYTE),
            (0, None),
            (None, 0),
        )
        for compute_bytes, graphics_bytes in cases:
            with self.subTest(
                compute_bytes=compute_bytes,
                graphics_bytes=graphics_bytes,
            ):
                snapshot = capture_gpu_idle_snapshot(
                    nvml_module=_Nvml(
                        used_memory=5 * GIBIBYTE,
                        compute=(_ProcessRecord(200, compute_bytes),),
                        graphics=(_ProcessRecord(200, graphics_bytes),),
                    ),
                    psutil_module=_Psutil(),
                    own_pid=100,
                    wall_clock=lambda: 10.0,
                )
                self.assertFalse(snapshot.available)
                self.assertFalse(snapshot.idle)

    def test_unknown_compute_memory_activity_or_api_denies(self):
        malformed_activity = _ProcessUtilization(200, 9_000_000)
        malformed_activity.smUtil = None
        missing_api = _Nvml()
        missing_api.nvmlDeviceGetProcessUtilization = None
        cases = (
            _Nvml(compute=(_ProcessRecord(200, None),)),
            _Nvml(
                used_memory=1,
                compute=(_ProcessRecord(200, 1),),
                process_utilization=(malformed_activity,),
            ),
            missing_api,
        )
        for nvml in cases:
            with self.subTest(case=nvml):
                snapshot = capture_gpu_idle_snapshot(
                    nvml_module=nvml,
                    psutil_module=_Psutil(),
                    own_pid=100,
                    wall_clock=lambda: 10.0,
                )
                self.assertFalse(snapshot.available)
                self.assertFalse(snapshot.idle)

    def test_not_found_process_utilization_is_valid_zero_activity_evidence(self):
        nvml = _Nvml(
            used_memory=1,
            compute=(_ProcessRecord(200, 1),),
        )

        def no_nonzero_samples(_handle, last_seen_timestamp):
            nvml.process_utilization_cutoffs.append(last_seen_timestamp)
            raise _Nvml.NVMLError_NotFound()

        nvml.nvmlDeviceGetProcessUtilization = no_nonzero_samples
        snapshot = capture_gpu_idle_snapshot(
            nvml_module=nvml,
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertTrue(snapshot.available)
        self.assertTrue(snapshot.idle)
        self.assertEqual(nvml.process_utilization_cutoffs, [0])

    def test_documented_capture_limits_cannot_be_relaxed(self):
        for kwargs in (
            {"max_gpu_utilization_percent": 25.1},
            {"max_foreign_compute_utilization_percent": 1.1},
            {"process_utilization_freshness_seconds": 3.1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    capture_gpu_idle_snapshot(
                        nvml_module=_Nvml(),
                        psutil_module=_Psutil(),
                        own_pid=100,
                        wall_clock=lambda: 10.0,
                        **kwargs,
                    )

    def test_device_utilization_and_wall_clock_do_not_coerce_ambiguous_types(self):
        for utilization in (True, "2"):
            with self.subTest(utilization=utilization):
                snapshot = capture_gpu_idle_snapshot(
                    nvml_module=_Nvml(utilization=utilization),
                    psutil_module=_Psutil(),
                    own_pid=100,
                    wall_clock=lambda: 10.0,
                )
                self.assertFalse(snapshot.available)
        for wall_clock in (lambda: True, lambda: "10"):
            with self.subTest(wall_clock=wall_clock):
                snapshot = capture_gpu_idle_snapshot(
                    nvml_module=_Nvml(),
                    psutil_module=_Psutil(),
                    own_pid=100,
                    wall_clock=wall_clock,
                )
                self.assertFalse(snapshot.available)

    def test_wddm_unknown_graphics_bytes_use_valid_aggregate_residual(self):
        graphics = tuple(_ProcessRecord(200 + index, None) for index in range(10))
        allowed = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                utilization=21,
                used_memory=int(3.5 * GIBIBYTE),
                graphics=graphics,
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertTrue(allowed.idle)

        blocked = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                utilization=21,
                used_memory=4 * GIBIBYTE + 1,
                graphics=graphics,
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertFalse(blocked.idle)
        self.assertEqual(blocked.reason, "foreign_graphics_memory")

        invalid_memory = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                total_memory=None,
                used_memory=int(3.5 * GIBIBYTE),
                graphics=graphics,
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertFalse(invalid_memory.available)

    def test_unlisted_fresh_utilization_pid_denies_as_a_race(self):
        snapshot = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                process_utilization=(
                    _ProcessUtilization(987654, 9_000_000, smUtil=1),
                ),
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        self.assertFalse(snapshot.available)
        self.assertEqual(snapshot.reason, "telemetry_or_attribution_unavailable")

    def test_stale_or_future_process_utilization_denies(self):
        for timestamp in (6_999_999, 10_000_001):
            with self.subTest(timestamp=timestamp):
                snapshot = capture_gpu_idle_snapshot(
                    nvml_module=_Nvml(
                        used_memory=1,
                        compute=(_ProcessRecord(200, 1),),
                        process_utilization=(
                            _ProcessUtilization(200, timestamp, smUtil=1),
                        ),
                    ),
                    psutil_module=_Psutil(),
                    own_pid=100,
                    wall_clock=lambda: 10.0,
                )
                self.assertFalse(snapshot.available)

    def test_snapshot_never_exposes_process_names_or_pids(self):
        snapshot = capture_gpu_idle_snapshot(
            nvml_module=_Nvml(
                used_memory=1,
                graphics=(_ProcessRecord(987654, 1),),
            ),
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
        )
        rendered = f"{snapshot!r} {snapshot.reason}"
        self.assertNotIn("987654", rendered)
        self.assertNotIn("Warp", rendered)

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
                    wall_clock=lambda: 10.0,
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
            wall_clock=lambda: 10.0,
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
                    wall_clock=lambda: 10.0,
                ).idle)

        broken = _Nvml()

        def query_failure(_handle):
            raise RuntimeError("driver query failed")

        broken.nvmlDeviceGetGraphicsRunningProcesses_v3 = query_failure
        denied = capture_gpu_idle_snapshot(
            nvml_module=broken,
            psutil_module=_Psutil(),
            own_pid=100,
            wall_clock=lambda: 10.0,
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
                    wall_clock=lambda: 10.0,
                )
                self.assertFalse(snapshot.available)
                self.assertFalse(snapshot.attribution_complete)


class SustainedGpuIdleGateTests(unittest.TestCase):
    def test_five_nearby_snapshots_over_eight_seconds_release_gate(self):
        gate = SustainedGpuIdleGate()
        decisions = [
            gate.observe(_idle_snapshot(), observed_at=observed_at)
            for observed_at in (10.0, 12.0, 14.0, 16.0, 18.0)
        ]
        self.assertTrue(all(not decision.ready for decision in decisions[:-1]))
        self.assertTrue(decisions[-1].ready)
        self.assertEqual(decisions[-1].reason, "idle_window_ready")
        self.assertEqual(decisions[-1].consecutive_idle_snapshots, 5)
        self.assertEqual(decisions[-1].idle_window_seconds, 8.0)

    def test_busy_foreign_or_unknown_snapshot_resets_the_window(self):
        blockers = (
            GpuIdleSnapshot(True, True, False, "gpu_utilization_busy", 80.0),
            GpuIdleSnapshot(
                True,
                True,
                False,
                "foreign_compute_activity",
                2.0,
                1,
                1,
            ),
            GpuIdleSnapshot(False, False, False, "telemetry_import_unavailable"),
        )
        for blocker in blockers:
            with self.subTest(reason=blocker.reason):
                gate = SustainedGpuIdleGate()
                for observed_at in (10.0, 12.0, 14.0, 16.0):
                    gate.observe(_idle_snapshot(), observed_at=observed_at)
                denied = gate.observe(blocker, observed_at=18.0)
                restarted = [
                    gate.observe(_idle_snapshot(), observed_at=observed_at)
                    for observed_at in (20.0, 22.0, 24.0, 26.0, 28.0)
                ]
                self.assertFalse(denied.ready)
                self.assertEqual(denied.reason, blocker.reason)
                self.assertEqual(restarted[0].consecutive_idle_snapshots, 1)
                self.assertTrue(restarted[-1].ready)

    def test_bounded_sample_gap_accepts_boundary_and_resets_above_it(self):
        accepted = SustainedGpuIdleGate()
        accepted_decisions = [
            accepted.observe(_idle_snapshot(), observed_at=observed_at)
            for observed_at in (10.0, 12.0, 15.0, 17.0, 18.0)
        ]
        self.assertTrue(accepted_decisions[-1].ready)

        for next_time in (12.0, 12.0 - 0.1, 15.0 + 0.1):
            with self.subTest(next_time=next_time):
                gate = SustainedGpuIdleGate()
                gate.observe(_idle_snapshot(), observed_at=10.0)
                gate.observe(_idle_snapshot(), observed_at=12.0)
                decision = gate.observe(_idle_snapshot(), observed_at=next_time)
                self.assertFalse(decision.ready)
                self.assertEqual(decision.consecutive_idle_snapshots, 1)
                self.assertEqual(decision.idle_window_seconds, 0.0)

    def test_internally_inconsistent_idle_snapshot_fails_closed(self):
        contradictory = (
            (False, False, True, "invalid", None, 0, 0),
            (True, True, True, "foreign_compute_activity", 2.0, 1, 1),
            (True, True, True, "idle_snapshot", None, 0, 0),
            (True, True, True, "gpu_utilization_busy", 2.0, 1, 0),
        )
        for values in contradictory:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    GpuIdleSnapshot(*values)

    def test_nonfinite_observation_time_resets_window(self):
        for observed_at in (
            float("nan"),
            float("inf"),
            float("-inf"),
            True,
            "12",
        ):
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
            lambda: "10",
            lambda: True,
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
            {"required_consecutive_snapshots": 4},
            {"minimum_idle_window_seconds": 7.9},
            {"maximum_sample_gap_seconds": 3.1},
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
