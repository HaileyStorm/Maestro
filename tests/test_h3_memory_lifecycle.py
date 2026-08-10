"""Model-free regressions for H3 host-memory admission and teardown."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

from app.services.oom_detect import build_failure_details


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _nodes(path: str, *names: str):
    tree = ast.parse(_source(path), filename=path)
    wanted = set(names)
    return [
        node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and node.name in wanted
    ]


def _load(path: str, names: tuple[str, ...], namespace: dict):
    module = ast.Module(body=_nodes(path, *names), type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, path, "exec"), namespace)
    return namespace


class H3HostMemoryLifecycleTests(unittest.TestCase):
    def test_release_severs_wrapper_before_flushing_host_allocator(self):
        events = []
        wrapper = SimpleNamespace(release=lambda: events.append("wrapper"))
        offloader = SimpleNamespace(release=lambda: events.append("offloader"))
        namespace = {
            "wan_model": wrapper,
            "offloadobj": offloader,
            "offload": SimpleNamespace(
                shared_state={"_cache": object()},
                flush_torch_caches=lambda: events.append("flush"),
            ),
            "gc": SimpleNamespace(collect=lambda: events.append("gc")),
            "_invalidate_loaded_model_state": Mock(),
        }
        _load(
            "app/wgp.py",
            ("clear_gen_cache", "release_model"),
            namespace,
        )

        namespace["release_model"]()

        self.assertIsNone(namespace["wan_model"])
        self.assertIsNone(namespace["offloadobj"])
        self.assertEqual(events[:3], ["offloader", "wrapper", "flush"])
        self.assertNotIn("_cache", namespace["offload"].shared_state)

    def test_memavailable_admission_fails_before_loader_allocation(self):
        namespace = {
            "os": os,
            "time": time,
            "_GIB": 1,
            "_H3_CALIBRATED_RUNTIME_OVERHEAD_BYTES": 10,
            "_H3_LOAD_TRANSIENT_RESERVE_BYTES": 10,
            "_H3_MEMORY_RECLAIM_WAIT_SECONDS": 0,
            "_H3_MEMORY_RECLAIM_POLL_INITIAL_SECONDS": 0.01,
            "_H3_MEMORY_RECLAIM_POLL_MAX_SECONDS": 0.01,
            "_host_memory_snapshot": lambda: (25, 100),
            "_load_resource_status": Mock(),
        }
        _load(
            "app/wgp.py",
            (
                "HostMemoryAdmissionError",
                "_h3_checkpoint_bytes",
                "_h3_required_host_memory_bytes",
                "_require_h3_host_memory",
            ),
            namespace,
        )
        with tempfile.TemporaryDirectory() as root:
            checkpoint = Path(root, "checkpoint.bin")
            checkpoint.write_bytes(b"x" * 10)
            with self.assertRaisesRegex(
                namespace["HostMemoryAdmissionError"],
                "host memory is too low",
            ) as raised:
                namespace["_require_h3_host_memory"]([str(checkpoint)], 1)
        self.assertEqual(raised.exception.stage, "model_load")
        self.assertEqual(raised.exception.code, "insufficient_host_memory")

    def test_memavailable_admission_allows_bounded_headroom(self):
        namespace = {
            "os": os,
            "time": time,
            "_GIB": 1,
            "_H3_CALIBRATED_RUNTIME_OVERHEAD_BYTES": 10,
            "_H3_LOAD_TRANSIENT_RESERVE_BYTES": 10,
            "_H3_MEMORY_RECLAIM_WAIT_SECONDS": 0,
            "_H3_MEMORY_RECLAIM_POLL_INITIAL_SECONDS": 0.01,
            "_H3_MEMORY_RECLAIM_POLL_MAX_SECONDS": 0.01,
            "_host_memory_snapshot": lambda: (31, 100),
            "_load_resource_status": Mock(),
        }
        _load(
            "app/wgp.py",
            (
                "HostMemoryAdmissionError",
                "_h3_checkpoint_bytes",
                "_h3_required_host_memory_bytes",
                "_require_h3_host_memory",
            ),
            namespace,
        )
        with tempfile.TemporaryDirectory() as root:
            checkpoint = Path(root, "checkpoint.bin")
            checkpoint.write_bytes(b"x" * 10)
            result = namespace["_require_h3_host_memory"](
                [str(checkpoint), str(checkpoint)], 1,
            )
        self.assertEqual(result["checkpoint_bytes"], 10)
        self.assertEqual(result["required_bytes"], 30)

    def test_calibrated_guard_admits_every_registered_h3_stack_at_103_gib(self):
        gib = 1024 ** 3
        namespace = {
            "_H3_CALIBRATED_RUNTIME_OVERHEAD_BYTES": 17 * gib,
            "_H3_LOAD_TRANSIENT_RESERVE_BYTES": 6 * gib,
        }
        _load(
            "app/wgp.py",
            ("_h3_required_host_memory_bytes",),
            namespace,
        )
        registered_checkpoint_stacks = {
            "base": 42_458_411_463,
            "ref2va": 42_458_411_463,
            "w4a8": 34_041_063_863,
            "pinkcherry": 66_215_432_005,
        }

        for name, checkpoint_bytes in registered_checkpoint_stacks.items():
            with self.subTest(model=name):
                required = namespace["_h3_required_host_memory_bytes"](
                    checkpoint_bytes
                )
                self.assertLess(required, 103 * gib)
                self.assertGreater(required, 29 * gib)

    def test_admission_waits_for_bounded_reclamation_then_passes(self):
        now = [0.0]
        snapshots = iter(((25, 100), (27, 100), (31, 100)))
        fake_time = SimpleNamespace(
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )
        emitted = []
        namespace = {
            "os": os,
            "time": fake_time,
            "_GIB": 1,
            "_H3_CALIBRATED_RUNTIME_OVERHEAD_BYTES": 10,
            "_H3_LOAD_TRANSIENT_RESERVE_BYTES": 10,
            "_H3_MEMORY_RECLAIM_WAIT_SECONDS": 2,
            "_H3_MEMORY_RECLAIM_POLL_INITIAL_SECONDS": 0.1,
            "_H3_MEMORY_RECLAIM_POLL_MAX_SECONDS": 0.5,
            "_host_memory_snapshot": lambda: next(snapshots),
            "_load_resource_status": (
                lambda stage, started: {"phase": stage, "elapsed": now[0] - started}
            ),
        }
        _load(
            "app/wgp.py",
            (
                "HostMemoryAdmissionError",
                "_h3_checkpoint_bytes",
                "_h3_required_host_memory_bytes",
                "_require_h3_host_memory",
            ),
            namespace,
        )
        with tempfile.TemporaryDirectory() as root:
            checkpoint = Path(root, "checkpoint.bin")
            checkpoint.write_bytes(b"x" * 10)
            result = namespace["_require_h3_host_memory"](
                [str(checkpoint)], 1, status_callback=emitted.append,
            )

        self.assertEqual(result["available_bytes"], 31)
        self.assertTrue(result["waited_for_reclamation"])
        self.assertGreaterEqual(len(emitted), 2)
        self.assertTrue(all(
            event["phase"] == "Waiting for host memory reclamation"
            for event in emitted
        ))

    def test_admission_reclamation_wait_is_bounded_and_cancellable(self):
        now = [0.0]
        cancelled = [False]
        fake_time = SimpleNamespace(
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )
        namespace = {
            "os": os,
            "time": fake_time,
            "_GIB": 1,
            "_H3_CALIBRATED_RUNTIME_OVERHEAD_BYTES": 10,
            "_H3_LOAD_TRANSIENT_RESERVE_BYTES": 10,
            "_H3_MEMORY_RECLAIM_WAIT_SECONDS": 0.25,
            "_H3_MEMORY_RECLAIM_POLL_INITIAL_SECONDS": 0.1,
            "_H3_MEMORY_RECLAIM_POLL_MAX_SECONDS": 0.1,
            "_host_memory_snapshot": lambda: (25, 100),
            "_load_resource_status": lambda stage, started: {"phase": stage},
        }
        _load(
            "app/wgp.py",
            (
                "HostMemoryAdmissionError",
                "_h3_checkpoint_bytes",
                "_h3_required_host_memory_bytes",
                "_require_h3_host_memory",
            ),
            namespace,
        )
        with tempfile.TemporaryDirectory() as root:
            checkpoint = Path(root, "checkpoint.bin")
            checkpoint.write_bytes(b"x" * 10)
            with self.assertRaises(namespace["HostMemoryAdmissionError"]):
                namespace["_require_h3_host_memory"]([str(checkpoint)], 1)
            self.assertLessEqual(now[0], 0.25)

            cancelled[0] = True
            with self.assertRaises(InterruptedError):
                namespace["_require_h3_host_memory"](
                    [str(checkpoint)], 1,
                    cancel_callback=lambda: cancelled[0],
                )

    def test_host_memory_rejection_keeps_actionable_safe_failure_stage(self):
        error = RuntimeError("private local detail")
        error.stage = "model_load"
        error.code = "insufficient_host_memory"

        details = build_failure_details(error)

        self.assertEqual(details["stage"], "model_load")
        self.assertEqual(details["code"], "insufficient_host_memory")
        self.assertEqual(
            details["detail"],
            "The generation model could not be loaded with the available "
            "host memory.",
        )
        self.assertNotIn("private local detail", str(details))

    def test_model_load_heartbeat_stops_and_never_emits_after_close(self):
        emitted = []
        namespace = {
            "threading": threading,
            "time": time,
            "_load_resource_status": (
                lambda stage, started: {"phase": stage, "message": stage}
            ),
        }
        _load(
            "app/wgp.py",
            ("_ModelLoadStatusReporter",),
            namespace,
        )
        reporter = namespace["_ModelLoadStatusReporter"](
            emitted.append,
            interval_seconds=0.01,
        )
        reporter.start("Profiling model offload")
        time.sleep(0.12)
        reporter.close()
        count_at_close = len(emitted)
        time.sleep(0.06)

        self.assertGreaterEqual(count_at_close, 2)
        self.assertEqual(len(emitted), count_at_close)
        self.assertTrue(all(
            event["phase"] == "Profiling model offload" for event in emitted
        ))

    def test_cancelled_heartbeat_raises_at_next_safe_boundary(self):
        cancelled = threading.Event()
        namespace = {
            "threading": threading,
            "time": time,
            "_load_resource_status": (
                lambda stage, started: {"phase": stage, "message": stage}
            ),
        }
        _load(
            "app/wgp.py",
            ("_ModelLoadStatusReporter",),
            namespace,
        )
        reporter = namespace["_ModelLoadStatusReporter"](
            lambda _event: None,
            cancel_callback=cancelled.is_set,
            interval_seconds=0.01,
        )
        reporter.start("Loading H3 transformer checkpoint")
        cancelled.set()
        time.sleep(0.03)
        with self.assertRaises(InterruptedError):
            reporter.check_cancelled()
        reporter.close()

    def test_h3_partial_constructor_and_release_sever_all_components(self):
        source = _source("app/models/minimax_h3/minimax_h3_main.py")
        tree = ast.parse(source)
        model_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MiniMaxH3Model"
        )
        constructor = next(
            node for node in model_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        release = next(
            node for node in model_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "release"
        )
        release_source = ast.get_source_segment(source, release)
        constructor_source = ast.get_source_segment(source, constructor)
        for component in (
            "transformer", "conditioner", "vae", "audio_vae",
            "scheduler", "audio_scheduler",
        ):
            self.assertIn(f"self.{component} = None", release_source)
        self.assertIn("except Exception:", constructor_source)
        self.assertIn("self.release()", constructor_source)
        self.assertIn("offload.flush_torch_caches()", constructor_source)

    def test_profile_failure_drops_aliases_and_partial_owner_before_flush(self):
        source = _source("app/wgp.py")
        load_models = next(
            node for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "load_models"
        )
        load_source = ast.get_source_segment(source, load_models)
        failure_cleanup = load_source.split("except Exception:", 1)[1]
        flush_index = failure_cleanup.index("offload.flush_torch_caches()")
        for cleanup in (
            "offload.last_offload_obj = None",
            "partial_offloader = None",
            "pipe = None",
            "kwargs = None",
            "loras_transformer = None",
            "handler_model_kwargs = None",
        ):
            self.assertLess(failure_cleanup.index(cleanup), flush_index)

    def test_rejection_and_cancel_paths_restore_process_default_device(self):
        source = _source("app/wgp.py")
        load_models = next(
            node for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "load_models"
        )
        load_source = ast.get_source_segment(source, load_models)
        admission = load_source.index("_require_h3_host_memory(")
        set_cpu = load_source.index("torch.set_default_device('cpu')")
        start_reporter = load_source.index("status_reporter.start(")
        main_try = load_source.rfind("try:", 0, start_reporter)
        restore = load_source.index(
            "torch.set_default_device(previous_default_device)"
        )

        # Admission rejects before changing global tensor placement; a
        # pre-cancel from reporter.start is inside the restoring try/finally.
        self.assertLess(admission, set_cpu)
        self.assertLess(set_cpu, main_try)
        self.assertLess(main_try, start_reporter)
        self.assertLess(start_reporter, restore)
        self.assertIn("if not model_load_succeeded:", load_source)
        self.assertIn(
            'previous_default_device = args.gpu if len(args.gpu) > 0 else "cpu"',
            load_source,
        )
        self.assertLess(
            load_source.index("model_load_succeeded = True"), restore,
        )


class H3LlmExclusionTests(unittest.TestCase):
    def setUp(self):
        self.previous_services = sys.modules.get("services")
        self.services = ModuleType("services")
        sys.modules["services"] = self.services

    def tearDown(self):
        if self.previous_services is None:
            sys.modules.pop("services", None)
        else:
            sys.modules["services"] = self.previous_services

    def test_h3_task_evicts_local_llm_and_holds_lease_through_operation(self):
        events = []
        lock = threading.RLock()
        llm = SimpleNamespace(
            _lock=lock,
            get_status=lambda: {"provider": "local", "loaded": True},
            unload_model=lambda: events.append("unload"),
        )
        self.services.llm_service = llm
        wgp = SimpleNamespace(
            get_base_model_type=lambda _model: "minimax_h3",
            _load_resource_status=lambda stage, _started: {
                "phase": stage, "message": stage,
            },
        )
        namespace = {"wgp": wgp, "time": time}
        _load(
            "app/launch.py",
            ("_run_generation_task_with_llm_exclusion",),
            namespace,
        )

        result = namespace["_run_generation_task_with_llm_exclusion"](
            "minimax_h3",
            lambda command, _payload: events.append(command),
            lambda: events.append("operation") or "done",
        )

        self.assertEqual(result, "done")
        self.assertEqual(events, ["status", "unload", "operation"])

    def test_non_h3_task_does_not_touch_llm(self):
        llm = SimpleNamespace(
            _lock=threading.RLock(),
            get_status=Mock(),
            unload_model=Mock(),
        )
        self.services.llm_service = llm
        namespace = {
            "wgp": SimpleNamespace(get_base_model_type=lambda _model: "wan"),
            "time": time,
        }
        _load(
            "app/launch.py",
            ("_run_generation_task_with_llm_exclusion",),
            namespace,
        )
        self.assertEqual(
            namespace["_run_generation_task_with_llm_exclusion"](
                "wan", Mock(), lambda: "done",
            ),
            "done",
        )
        llm.get_status.assert_not_called()
        llm.unload_model.assert_not_called()

    def test_local_llm_load_releases_resident_h3_before_model_lease(self):
        events = []

        class Lease:
            def __enter__(self):
                events.append("llm-lease")

            def __exit__(self, *_args):
                events.append("llm-release")

        self.services.llm_service = SimpleNamespace(
            loaded_model_lease=lambda **_kwargs: Lease(),
        )
        namespace = {
            "_gen_lock": threading.Lock(),
            "wgp": SimpleNamespace(
                transformer_type="minimax_h3",
                wan_model=object(),
                offloadobj=object(),
                release_model=lambda: events.append("h3-release"),
            ),
        }
        _load(
            "app/launch.py",
            ("_run_llm_with_selection",),
            namespace,
        )

        result = namespace["_run_llm_with_selection"](
            {"provider": "local", "model_id": "local-model"},
            lambda: events.append("operation") or "done",
        )

        self.assertEqual(result, "done")
        self.assertEqual(
            events,
            ["h3-release", "llm-lease", "operation", "llm-release"],
        )


if __name__ == "__main__":
    unittest.main()
