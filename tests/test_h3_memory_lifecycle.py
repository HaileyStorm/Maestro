"""Model-free regressions for H3 host-memory admission and teardown."""
from __future__ import annotations

import ast
import copy
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
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.model_residency import (  # noqa: E402
    ModelResidencyError,
    ModelResidencyEvidenceStore,
    ResidencySingleflight,
    build_residency_key,
    choose_profile_action,
)


def _runtime_residency_key(*, artifact="model-a", frames=10, budget=12):
    return build_residency_key(
        model={
            "artifact_id": artifact,
            "artifact_revision": "sha256-abc123",
            "family": "minimax_h3",
            "quantization": "nvfp4",
        },
        runtime={
            "runtime_id": "wan2gp",
            "runtime_version": "1.4",
            "build_id": "maestro-runtime",
            "driver_version": "580.82",
        },
        hardware={
            "accelerator": "nvidia-rtx4090",
            "total_vram_gib": 24,
            "total_host_ram_gib": 64,
        },
        workload={
            "kind": "h3-video",
            "width": 960,
            "height": 544,
            "frame_count": frames,
            "steps": 20,
            "reference_count": 1,
            "lora_count": 0,
            "stage_count": 1,
        },
        settings={
            "offload_profile": 4,
            "resident_budget_gib": budget,
            "attention_backend": "sdpa",
            "cache_mode": "none",
            "weight_quantization": "nvfp4",
        },
        condition={
            "free_vram_band_gib": 10,
            "free_host_ram_band_gib": 40,
            "residency_epoch_band": 0,
        },
        policy_revision=1,
    )


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
            "copy": copy,
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
        self.assertEqual(raised.exception.available_bytes, 25)
        self.assertEqual(raised.exception.required_bytes, 30)
        self.assertEqual(raised.exception.total_bytes, 100)
        self.assertTrue(raised.exception.retryable)

    def test_memavailable_admission_marks_impossible_capacity_nonretryable(self):
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
            checkpoint.write_bytes(b"x" * 90)
            with self.assertRaises(
                namespace["HostMemoryAdmissionError"],
            ) as raised:
                namespace["_require_h3_host_memory"]([str(checkpoint)], 1)

        self.assertEqual(raised.exception.available_bytes, 25)
        self.assertEqual(raised.exception.required_bytes, 110)
        self.assertEqual(raised.exception.total_bytes, 100)
        self.assertFalse(raised.exception.retryable)

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


class ModelResidencyRuntimeIntegrationTests(unittest.TestCase):
    def _namespace(self, offload_backend, *, singleflight=None):
        namespace = {
            "offload": offload_backend,
            "hashlib": __import__("hashlib"),
            "ModelResidencyError": ModelResidencyError,
            "choose_profile_action": choose_profile_action,
            "_MODEL_RESIDENCY_PROFILE_COST_SECONDS": 90.0,
            "_MODEL_RESIDENCY_RECOVERY_COST_SECONDS": 180.0,
            "_model_residency_singleflight": (
                singleflight or ResidencySingleflight()
            ),
            "_model_residency_offload_setup_lock": threading.Lock(),
        }
        return _load(
            "app/wgp.py",
            (
                "_mmgp_profile_defaults",
                "_select_model_residency_plan",
                "_residency_status",
                "_record_model_residency_oom",
                "_model_residency_graph_flight_key",
                "_run_model_offload_with_residency",
            ),
            namespace,
        )

    def _evidence_context_namespace(self, store):
        namespace = {
            "copy": copy,
            "hashlib": __import__("hashlib"),
            "math": __import__("math"),
            "wan_model": object(),
            "offloadobj": object(),
            "reload_needed": False,
            "_loaded_model_residency_evidence_template": None,
            "_loaded_model_residency_evidence_context_id": None,
            "_model_residency_evidence_contexts": {},
            "_model_residency_evidence_context_sequence": 0,
            "_model_residency_evidence_context_lock": threading.RLock(),
            "_MODEL_RESIDENCY_EVIDENCE_CONTEXT_VERSION": 1,
            "_MODEL_RESIDENCY_EVIDENCE_CONTEXT_MAX": 16,
            "_get_model_residency_store": lambda: store,
        }
        return _load(
            "app/wgp.py",
            (
                "_residency_digest_token",
                "_register_model_residency_evidence_context",
                "get_current_model_residency_evidence_context",
                "record_model_residency_runtime_outcome",
            ),
            namespace,
        )

    def test_runtime_evidence_context_is_opaque_and_deep_copied(self):
        store = SimpleNamespace(record_success=Mock(), record_oom=Mock())
        namespace = self._evidence_context_namespace(store)
        key = _runtime_residency_key()

        issued = namespace["_register_model_residency_evidence_context"](key)
        issued["condition"]["free_vram_band_gib"] = 999
        current = namespace["get_current_model_residency_evidence_context"]()

        self.assertEqual(current["exact_key"], key["exact_key"])
        self.assertEqual(current["offload_profile"], 4.0)
        self.assertEqual(current["resident_budget_gib"], 12.0)
        self.assertEqual(current["condition"]["free_vram_band_gib"], 10.0)
        self.assertEqual(
            set(current),
            {
                "schema_version", "context_id", "exact_key",
                "offload_profile", "resident_budget_gib", "condition",
            },
        )
        self.assertNotIn("prompt", repr(current).lower())
        self.assertNotIn("path", repr(current).lower())

    def test_runtime_outcomes_use_registered_exact_key_across_release(self):
        store = SimpleNamespace(record_success=Mock(), record_oom=Mock())
        namespace = self._evidence_context_namespace(store)
        key = _runtime_residency_key()
        captured = namespace["_register_model_residency_evidence_context"](key)

        self.assertTrue(namespace["record_model_residency_runtime_outcome"](
            "success", phase="generation",
        ))
        namespace["wan_model"] = None
        namespace["offloadobj"] = None
        namespace["reload_needed"] = True
        namespace["_loaded_model_residency_evidence_context_id"] = None
        self.assertIsNone(
            namespace["get_current_model_residency_evidence_context"](),
        )
        self.assertTrue(namespace["record_model_residency_runtime_outcome"](
            "oom", phase="finalization", required_margin_gib=2.5,
            evidence_context=captured,
        ))

        success_key = store.record_success.call_args.args[0]
        oom_key = store.record_oom.call_args.args[0]
        self.assertEqual(success_key["exact_key"], key["exact_key"])
        self.assertEqual(oom_key["exact_key"], key["exact_key"])
        self.assertEqual(store.record_oom.call_args.kwargs["phase"], "finalization")
        self.assertEqual(
            store.record_oom.call_args.kwargs["required_margin_gib"], 2.5,
        )

    def test_runtime_outcome_rejects_tampered_unknown_and_evicted_contexts(self):
        store = SimpleNamespace(record_success=Mock(), record_oom=Mock())
        namespace = self._evidence_context_namespace(store)
        oldest = namespace["_register_model_residency_evidence_context"](
            _runtime_residency_key(frames=1),
        )
        tampered = copy.deepcopy(oldest)
        tampered["exact_key"] = _runtime_residency_key(frames=2)["exact_key"]
        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "oom", phase="generation", evidence_context=tampered,
        ))
        unknown = copy.deepcopy(oldest)
        unknown["context_id"] = "sha256-" + "0" * 64
        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "success", phase="generation", evidence_context=unknown,
        ))

        for frame_count in range(2, 19):
            namespace["_register_model_residency_evidence_context"](
                _runtime_residency_key(frames=frame_count),
            )
        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "oom", phase="finalization", evidence_context=oldest,
        ))
        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "oom", phase="invalid", evidence_context=oldest,
        ))
        store.record_success.assert_not_called()
        store.record_oom.assert_not_called()

    def test_runtime_outcome_store_failure_is_safe_fallback(self):
        store = SimpleNamespace(
            record_success=Mock(side_effect=OSError("unavailable")),
            record_oom=Mock(side_effect=OSError("unavailable")),
        )
        namespace = self._evidence_context_namespace(store)
        namespace["_register_model_residency_evidence_context"](
            _runtime_residency_key(),
        )

        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "success", phase="generation",
        ))
        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "oom", phase="generation", required_margin_gib=1.0,
        ))

    def test_runtime_outcome_without_current_context_is_safe_fallback(self):
        store = SimpleNamespace(record_success=Mock(), record_oom=Mock())
        namespace = self._evidence_context_namespace(store)
        namespace["wan_model"] = None
        namespace["offloadobj"] = None
        namespace["reload_needed"] = True

        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "oom", phase="generation", required_margin_gib=1.0,
        ))
        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "success", phase="generation",
        ))
        store.record_success.assert_not_called()
        store.record_oom.assert_not_called()

    def test_a_b_a_prior_run_reuse_skips_profile_after_store_restart(self):
        backend = SimpleNamespace(
            profile=Mock(side_effect=lambda *_args, **_kwargs: object()),
            all=Mock(side_effect=lambda *_args, **_kwargs: object()),
        )
        namespace = self._namespace(backend)
        statuses = []
        a = _runtime_residency_key(artifact="model-a")
        b = _runtime_residency_key(artifact="model-b")
        kwargs = {"vram_safety_coefficient": 0.8}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "residency.json")
            store = ModelResidencyEvidenceStore(path)
            namespace["_run_model_offload_with_residency"](
                object(), profile_no=4, offload_kwargs=kwargs,
                key=a, store=store, status_callback=statuses.append,
            )
            namespace["_run_model_offload_with_residency"](
                object(), profile_no=4, offload_kwargs=kwargs,
                key=b, store=store, status_callback=statuses.append,
            )
            restarted = ModelResidencyEvidenceStore(path)
            namespace["_run_model_offload_with_residency"](
                object(), profile_no=4, offload_kwargs=kwargs,
                key=a, store=restarted, status_callback=statuses.append,
            )

        self.assertEqual(backend.profile.call_count, 2)
        self.assertEqual(backend.all.call_count, 1)
        self.assertTrue(all(
            "Profiling model offload" in status for status in statuses[:2]
        ))
        self.assertIn("exact prior-run evidence", statuses[2])
        self.assertNotIn("Profiling model offload", statuses[2])

    def test_nearby_policy_result_uses_conservative_direct_plan(self):
        backend = SimpleNamespace(profile=Mock(), all=Mock(return_value=object()))
        namespace = self._namespace(backend)
        statuses = []
        with tempfile.TemporaryDirectory() as directory:
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json")
            )
            store.record_success(
                _runtime_residency_key(frames=8, budget=12), observed_at=100,
            )
            store.record_success(
                _runtime_residency_key(frames=12, budget=10), observed_at=101,
            )
            target = _runtime_residency_key(frames=10, budget=14)
            namespace["_run_model_offload_with_residency"](
                object(), profile_no=4,
                offload_kwargs={"vram_safety_coefficient": 0.8},
                key=target, store=store, status_callback=statuses.append,
            )

        backend.profile.assert_not_called()
        backend.all.assert_called_once()
        self.assertLess(
            backend.all.call_args.kwargs["vram_safety_coefficient"], 0.8,
        )
        self.assertIn("compatible interpolation", statuses[0])

    def test_same_graph_concurrency_runs_one_actual_profile(self):
        entered = threading.Event()
        release = threading.Event()
        pipe = object()
        result = object()

        def profile(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return result

        backend = SimpleNamespace(
            profile=Mock(side_effect=profile), all=Mock(),
        )
        namespace = self._namespace(backend)
        key = _runtime_residency_key()
        with tempfile.TemporaryDirectory() as directory:
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json")
            )
            results = []

            def run():
                results.append(namespace["_run_model_offload_with_residency"](
                    pipe, profile_no=4,
                    offload_kwargs={"vram_safety_coefficient": 0.8},
                    key=key, store=store, status_callback=lambda _stage: None,
                ))

            threads = [threading.Thread(target=run) for _ in range(2)]
            threads[0].start()
            self.assertTrue(entered.wait(timeout=2))
            threads[1].start()
            time.sleep(0.05)
            release.set()
            for thread in threads:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())

        backend.profile.assert_called_once()
        backend.all.assert_not_called()
        self.assertEqual(results, [result, result])

    def test_different_graphs_serialize_setup_and_receive_local_offloaders(self):
        first_entered = threading.Event()
        direct_entered = threading.Event()
        release = threading.Event()

        def profile(pipe, *_args, **_kwargs):
            first_entered.set()
            release.wait(timeout=2)
            return SimpleNamespace(pipe=pipe)

        def direct(pipe, *_args, **_kwargs):
            direct_entered.set()
            return SimpleNamespace(pipe=pipe)

        backend = SimpleNamespace(
            profile=Mock(side_effect=profile), all=Mock(side_effect=direct),
        )
        namespace = self._namespace(backend)
        key = _runtime_residency_key()
        pipes = [object(), object()]
        results = {}
        with tempfile.TemporaryDirectory() as directory:
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json")
            )

            def run(index):
                results[index] = namespace[
                    "_run_model_offload_with_residency"
                ](
                    pipes[index], profile_no=4,
                    offload_kwargs={"vram_safety_coefficient": 0.8},
                    key=key, store=store,
                    status_callback=lambda _stage: None,
                )

            threads = [
                threading.Thread(target=run, args=(index,))
                for index in range(2)
            ]
            threads[0].start()
            self.assertTrue(first_entered.wait(timeout=2))
            threads[1].start()
            setup_overlapped = direct_entered.wait(timeout=0.1)
            release.set()
            self.assertTrue(direct_entered.wait(timeout=2))
            for thread in threads:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())

        self.assertFalse(setup_overlapped)
        backend.profile.assert_called_once()
        backend.all.assert_called_once()
        self.assertIs(results[0].pipe, pipes[0])
        self.assertIs(results[1].pipe, pipes[1])
        self.assertIsNot(results[0], results[1])

    def test_profile_failure_is_not_cached_and_next_active_load_can_retry(self):
        result = object()
        backend = SimpleNamespace(
            profile=Mock(side_effect=[RuntimeError("profile failed"), result]),
            all=Mock(),
        )
        namespace = self._namespace(backend)
        statuses = []
        key = _runtime_residency_key()
        with tempfile.TemporaryDirectory() as directory:
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json")
            )
            with self.assertRaisesRegex(RuntimeError, "profile failed"):
                namespace["_run_model_offload_with_residency"](
                    object(), profile_no=4,
                    offload_kwargs={"vram_safety_coefficient": 0.8},
                    key=key, store=store, status_callback=statuses.append,
                )
            recovered = namespace["_run_model_offload_with_residency"](
                object(), profile_no=4,
                offload_kwargs={"vram_safety_coefficient": 0.8},
                key=key, store=store, status_callback=statuses.append,
            )

        self.assertIs(recovered, result)
        self.assertEqual(backend.profile.call_count, 2)
        backend.all.assert_not_called()
        self.assertEqual(
            sum("Profiling model offload" in status for status in statuses), 2,
        )

    def test_unavailable_evidence_falls_back_to_truthful_actual_profile(self):
        class UnavailableStore:
            def recommend(self, _key):
                raise ModelResidencyError("unavailable")

            def record_success(self, _key):
                raise ModelResidencyError("unavailable")

        backend = SimpleNamespace(
            profile=Mock(return_value=object()), all=Mock(),
        )
        namespace = self._namespace(backend)
        statuses = []
        namespace["_run_model_offload_with_residency"](
            object(), profile_no=4,
            offload_kwargs={"vram_safety_coefficient": 0.8},
            key=_runtime_residency_key(), store=UnavailableStore(),
            status_callback=statuses.append,
        )

        backend.profile.assert_called_once()
        backend.all.assert_not_called()
        self.assertEqual(
            statuses, [
                "Profiling model offload · residency evidence unavailable"
            ],
        )

    def test_unexpected_store_errors_fall_back_without_losing_real_load(self):
        class UnexpectedStore:
            def recommend(self, _key):
                raise OSError("unavailable")

            def record_success(self, _key):
                raise OSError("unavailable")

        result = object()
        backend = SimpleNamespace(
            profile=Mock(return_value=result), all=Mock(),
        )
        namespace = self._namespace(backend)
        statuses = []

        loaded = namespace["_run_model_offload_with_residency"](
            object(), profile_no=4,
            offload_kwargs={"vram_safety_coefficient": 0.8},
            key=_runtime_residency_key(), store=UnexpectedStore(),
            status_callback=statuses.append,
        )

        self.assertIs(loaded, result)
        backend.profile.assert_called_once()
        backend.all.assert_not_called()
        self.assertEqual(
            statuses,
            ["Profiling model offload · residency evidence unavailable"],
        )

    def test_unexpected_oom_evidence_write_error_never_masks_load_error(self):
        namespace = self._namespace(
            SimpleNamespace(profile=Mock(), all=Mock()),
        )
        store = SimpleNamespace(
            record_oom=Mock(side_effect=OSError("unavailable")),
        )
        load_error = RuntimeError("authoritative load error")
        load_error.code = "insufficient_host_memory"

        namespace["_record_model_residency_oom"](
            store, _runtime_residency_key(), load_error,
        )

        store.record_oom.assert_called_once()

    def test_explicit_reprofile_bypasses_exact_success_evidence(self):
        backend = SimpleNamespace(
            profile=Mock(return_value=object()), all=Mock(),
        )
        namespace = self._namespace(backend)
        statuses = []
        key = _runtime_residency_key()
        with tempfile.TemporaryDirectory() as directory:
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json")
            )
            store.record_success(key, observed_at=100)
            namespace["_run_model_offload_with_residency"](
                object(), profile_no=4,
                offload_kwargs={"vram_safety_coefficient": 0.8},
                key=key, store=store, status_callback=statuses.append,
                force_reprofile=True,
            )

        backend.profile.assert_called_once()
        backend.all.assert_not_called()
        self.assertEqual(
            statuses, ["Profiling model offload · explicit reprofile"],
        )

    def test_direct_profile_defaults_match_pinned_mmgp_contract(self):
        backend = SimpleNamespace(profile=Mock(), all=Mock())
        namespace = self._namespace(backend)
        defaults = namespace["_mmgp_profile_defaults"]
        expected = {
            1: (True, None),
            2: (True, {"transformer": 1200, "*": 3000}),
            3: ("transformer", None),
            4: ("transformer", {"transformer": 1200, "*": 3000}),
            5: (False, {"transformer": 400, "*": 3000}),
        }
        for profile, (pinned, budgets) in expected.items():
            with self.subTest(profile=profile):
                resolved = defaults(profile)
                self.assertEqual(resolved["pinnedMemory"], pinned)
                self.assertEqual(resolved["budgets"], budgets)
                self.assertTrue(resolved["asyncTransfers"])
        self.assertIsNone(defaults(4.5))

    def test_effective_preload_key_input_tracks_server_fallback(self):
        namespace = {
            "args": SimpleNamespace(preload="0"),
            "server_config": {"preload_in_VRAM": 1250},
        }
        _load(
            "app/wgp.py", ("_effective_preload_setting",), namespace,
        )
        self.assertEqual(namespace["_effective_preload_setting"](), 1250)
        namespace["server_config"]["preload_in_VRAM"] = 2250
        self.assertEqual(namespace["_effective_preload_setting"](), 2250)
        namespace["args"].preload = "3000"
        self.assertEqual(namespace["_effective_preload_setting"](), 3000)

        load_source = ast.get_source_segment(
            _source("app/wgp.py"),
            next(
                node for node in ast.parse(_source("app/wgp.py")).body
                if isinstance(node, ast.FunctionDef)
                and node.name == "load_models"
            ),
        )
        self.assertIn(
            '"preload": str(_effective_preload_setting())', load_source,
        )

    def test_runtime_key_uses_real_dimensions_without_retaining_path_text(self):
        fake_torch = SimpleNamespace(
            __version__="2.9.0",
            version=SimpleNamespace(cuda="13.0"),
            cuda=SimpleNamespace(is_available=lambda: False),
        )
        namespace = {
            "copy": copy,
            "os": os,
            "hashlib": __import__("hashlib"),
            "re": __import__("re"),
            "torch": fake_torch,
            "_GIB": 1024 ** 3,
            "_host_memory_snapshot": lambda: (40 << 30, 64 << 30),
            "get_overridden_attention": lambda _model: None,
            "attention_mode": "sdpa",
            "get_auto_attention": lambda: "sdpa",
            "transformer_quantization": "nvfp4",
            "WanGP_version": "10.9875",
            "mmgp_version": "3.7.12",
            "_MODEL_RESIDENCY_POLICY_REVISION": 1,
            "build_residency_key": build_residency_key,
        }
        _load(
            "app/wgp.py",
            (
                "_residency_digest_token",
                "_residency_artifact_revision",
                "_bounded_nonnegative_int",
                "_count_residency_items",
                "_generation_residency_context",
                "_residency_hardware_snapshot",
                "_resolve_model_residency_attention",
                "_build_model_residency_key_from_template",
                "_build_model_residency_key",
            ),
            namespace,
        )
        context = namespace["_generation_residency_context"](
            output_type="video", resolution="960x544", frame_count=81,
            steps=20, references=[["one", "two"], None], loras=["lora"],
            stage_count=8, cache_mode="tea", attention_backend="sol_attn",
        )
        with tempfile.TemporaryDirectory() as directory:
            private_path = Path(directory, "PRIVATE_SENTINEL_MODEL.safetensors")
            auxiliary_path = Path(directory, "h3-video-vae.safetensors")
            private_path.write_bytes(b"weights")
            auxiliary_path.write_bytes(b"auxiliary-v1")
            first, template = namespace["_build_model_residency_key"](
                model_type="minimax_h3", base_model_type="minimax_h3",
                artifact_paths=[str(private_path), str(auxiliary_path)], profile=4,
                vram_safety_coefficient=0.8, transformer_dtype="float16",
                load_environment={"compile": False},
                residency_context=context,
                return_template=True,
            )
            rebuilt = namespace["_build_model_residency_key_from_template"](
                template, context,
            )
            auxiliary_path.write_bytes(b"auxiliary-v2-expanded")
            changed_auxiliary = namespace["_build_model_residency_key"](
                model_type="minimax_h3", base_model_type="minimax_h3",
                artifact_paths=[str(private_path), str(auxiliary_path)], profile=4,
                vram_safety_coefficient=0.8, transformer_dtype="float16",
                load_environment={"compile": False},
                residency_context=context,
            )
            context["frame_count"] = 82
            changed = namespace["_build_model_residency_key"](
                model_type="minimax_h3", base_model_type="minimax_h3",
                artifact_paths=[str(private_path), str(auxiliary_path)], profile=4,
                vram_safety_coefficient=0.8, transformer_dtype="float16",
                load_environment={"compile": False},
                residency_context=context,
            )

        self.assertEqual(first["identity"]["workload"]["reference_count"], 2)
        self.assertEqual(first["identity"]["workload"]["lora_count"], 1)
        self.assertEqual(first["exact_key"], rebuilt["exact_key"])
        self.assertNotEqual(first["exact_key"], changed_auxiliary["exact_key"])
        self.assertNotEqual(first["exact_key"], changed["exact_key"])
        self.assertNotIn("PRIVATE_SENTINEL", repr(first))
        self.assertNotIn("PRIVATE_SENTINEL", repr(template))

    def test_template_refactor_preserves_legacy_exact_key_formula(self):
        fake_torch = SimpleNamespace(
            __version__="2.9.0", version=SimpleNamespace(cuda="13.0"),
        )
        hardware = {
            "accelerator": "sha256-accelerator",
            "total_vram_gib": 24.0,
            "total_host_ram_gib": 64.0,
            "free_vram_band_gib": 10,
            "free_host_ram_band_gib": 40,
            "driver_version": "sha256-driver",
        }
        namespace = {
            "copy": copy,
            "hashlib": __import__("hashlib"),
            "torch": fake_torch,
            "transformer_quantization": "nvfp4",
            "WanGP_version": "10.9875",
            "mmgp_version": "3.7.12",
            "_MODEL_RESIDENCY_POLICY_REVISION": 1,
            "_residency_hardware_snapshot": lambda: dict(hardware),
            "_residency_artifact_revision": lambda _paths: "sha256-revision",
            "get_overridden_attention": lambda _model: "sdpa",
            "attention_mode": "sdpa",
            "get_auto_attention": lambda: "sdpa",
            "build_residency_key": build_residency_key,
        }
        _load(
            "app/wgp.py",
            (
                "_residency_digest_token",
                "_bounded_nonnegative_int",
                "_resolve_model_residency_attention",
                "_build_model_residency_key_from_template",
                "_build_model_residency_key",
            ),
            namespace,
        )
        context = {
            "kind": "video", "width": 960, "height": 544,
            "frame_count": 81, "steps": 20, "reference_count": 1,
            "lora_count": 1, "lora_signature": "sha256-lora",
            "stage_count": 2, "cache_mode": "tea",
            "attention_backend": "flash",
        }
        load_environment = {"compile": False, "preload": "1250"}
        actual = namespace["_build_model_residency_key"](
            model_type="minimax_h3", base_model_type="minimax_h3",
            artifact_paths=[], profile=4, vram_safety_coefficient=0.8,
            transformer_dtype="float16",
            load_environment=load_environment,
            residency_context=context,
        )
        digest = namespace["_residency_digest_token"]
        weight_quantization = digest("nvfp4", "float16")
        expected = build_residency_key(
            model={
                "artifact_id": digest("minimax_h3"),
                "artifact_revision": "sha256-revision",
                "family": digest("minimax_h3"),
                "quantization": weight_quantization,
            },
            runtime={
                "runtime_id": "wan2gp",
                "runtime_version": digest("10.9875"),
                "build_id": digest(
                    "10.9875", "3.7.12", "2.9.0", "13.0",
                    load_environment, "sha256-lora",
                ),
                "driver_version": "sha256-driver",
            },
            hardware={
                "accelerator": "sha256-accelerator",
                "total_vram_gib": 24.0,
                "total_host_ram_gib": 64.0,
            },
            workload={
                "kind": digest("video"), "width": 960, "height": 544,
                "frame_count": 81, "steps": 20, "reference_count": 1,
                "lora_count": 1, "stage_count": 2,
            },
            settings={
                "offload_profile": 4.0,
                "resident_budget_gib": 24.0 * 0.8,
                "attention_backend": digest("flash"),
                "cache_mode": digest("tea"),
                "weight_quantization": weight_quantization,
            },
            condition={
                "free_vram_band_gib": 10,
                "free_host_ram_band_gib": 40,
                "residency_epoch_band": 0,
            },
            policy_revision=1,
        )

        self.assertEqual(actual, expected)

    def test_generation_derives_new_exact_context_for_resident_model(self):
        fake_torch = SimpleNamespace(
            __version__="2.9.0",
            version=SimpleNamespace(cuda="13.0"),
            cuda=SimpleNamespace(is_available=lambda: False),
        )
        namespace = self._evidence_context_namespace(
            SimpleNamespace(record_success=Mock(), record_oom=Mock()),
        )
        namespace.update({
            "torch": fake_torch,
            "_GIB": 1024 ** 3,
            "_host_memory_snapshot": lambda: (40 << 30, 64 << 30),
            "_MODEL_RESIDENCY_POLICY_REVISION": 1,
            "build_residency_key": build_residency_key,
        })
        _load(
            "app/wgp.py",
            (
                "_bounded_nonnegative_int",
                "_residency_hardware_snapshot",
                "_build_model_residency_key_from_template",
                "derive_current_model_residency_evidence_context",
            ),
            namespace,
        )
        template = {
            "default_kind": "video",
            "default_attention": "sdpa",
            "model": {
                "artifact_id": "sha256-model",
                "artifact_revision": "sha256-revision",
                "family": "sha256-family",
                "quantization": "sha256-quantization",
            },
            "runtime": {
                "runtime_version": "sha256-runtime",
                "build_parts": ("runtime", "build", {"compile": False}),
            },
            "settings": {
                "offload_profile": 4.0,
                "vram_safety_coefficient": 0.8,
                "weight_quantization": "sha256-quantization",
            },
        }
        namespace["_loaded_model_residency_evidence_template"] = template
        first = namespace["derive_current_model_residency_evidence_context"]({
            "kind": "video", "width": 960, "height": 544,
            "frame_count": 81, "steps": 20, "reference_count": 1,
            "lora_count": 0, "lora_signature": "sha256-none",
            "stage_count": 1, "cache_mode": "none",
            "attention_backend": "sdpa",
        })
        second = namespace["derive_current_model_residency_evidence_context"]({
            "kind": "video", "width": 960, "height": 544,
            "frame_count": 161, "steps": 20, "reference_count": 1,
            "lora_count": 1, "lora_signature": "sha256-lora-b",
            "stage_count": 2, "cache_mode": "tea",
            "attention_backend": "flash",
        })

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first["exact_key"], second["exact_key"])
        self.assertEqual(
            namespace["get_current_model_residency_evidence_context"](),
            second,
        )
        self.assertNotIn("prompt", repr(second).lower())

    def test_failed_finalized_derivation_clears_prior_current_context(self):
        store = SimpleNamespace(record_success=Mock(), record_oom=Mock())
        namespace = self._evidence_context_namespace(store)
        namespace["_loaded_model_residency_evidence_template"] = {
            "path_free": True,
        }
        namespace["_register_model_residency_evidence_context"](
            _runtime_residency_key(frames=8),
        )
        namespace["_build_model_residency_key_from_template"] = Mock(
            side_effect=ValueError("invalid finalized workload"),
        )
        _load(
            "app/wgp.py",
            ("derive_current_model_residency_evidence_context",),
            namespace,
        )

        self.assertIsNone(
            namespace["derive_current_model_residency_evidence_context"]({
                "frame_count": 16,
            }),
        )
        self.assertIsNone(
            namespace["get_current_model_residency_evidence_context"](),
        )
        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "oom", phase="generation", required_margin_gib=1.0,
        ))
        store.record_success.assert_not_called()
        store.record_oom.assert_not_called()

    def test_new_workload_clears_prior_implicit_context_until_derived(self):
        store = SimpleNamespace(record_success=Mock(), record_oom=Mock())
        namespace = self._evidence_context_namespace(store)
        captured = namespace["_register_model_residency_evidence_context"](
            _runtime_residency_key(frames=8),
        )
        _load(
            "app/wgp.py",
            ("_clear_current_model_residency_evidence_context",),
            namespace,
        )

        namespace["_clear_current_model_residency_evidence_context"]()

        self.assertIsNone(
            namespace["get_current_model_residency_evidence_context"](),
        )
        self.assertFalse(namespace["record_model_residency_runtime_outcome"](
            "oom", phase="generation", required_margin_gib=1.0,
        ))
        self.assertTrue(namespace["record_model_residency_runtime_outcome"](
            "oom", phase="finalization", required_margin_gib=1.0,
            evidence_context=captured,
        ))

    def test_load_and_generation_wire_exact_evidence_context_once(self):
        source = _source("app/wgp.py")
        functions = {
            node.name: ast.get_source_segment(source, node)
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"load_models", "generate_video", "_generate_video_impl"}
        }
        load_source = functions["load_models"]
        generate_source = functions["_generate_video_impl"]
        wrapper = ast.parse(functions["generate_video"])
        self.assertEqual(sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_generate_video_impl"
            for node in ast.walk(wrapper)
        ), 1)

        self.assertIn("return_template=True", load_source)
        self.assertIn(
            "_register_model_residency_evidence_context(\n"
            "        residency_key, template=residency_template,",
            load_source,
        )
        self.assertEqual(
            generate_source.count("requested_residency_evidence_context = "),
            1,
        )
        self.assertIn(
            "residency_context=requested_residency_evidence_context",
            generate_source,
        )
        clear_current = generate_source.index(
            "_clear_current_model_residency_evidence_context()"
        )
        self.assertLess(clear_current, generate_source.index("_auto_aspect ="))
        self.assertIn(
            "derive_current_model_residency_evidence_context(\n"
            "        finalized_residency_evidence_context,",
            generate_source,
        )
        self.assertEqual(
            generate_source.count(
                "derive_current_model_residency_evidence_context("
            ),
            1,
        )
        finalized = generate_source.index(
            "finalized_residency_evidence_context = "
        )
        self.assertLess(
            generate_source.index(
                "video_length = align_model_frame_count(video_length, model_def)"
            ),
            finalized,
        )
        self.assertLess(
            generate_source.index("width, height = resolution.split"),
            finalized,
        )
        self.assertLess(
            generate_source.index(
                "first_window_video_length = current_video_length"
            ),
            finalized,
        )
        finalized_source = generate_source[finalized:]
        self.assertIn('resolution=f"{width}x{height}"', finalized_source)
        self.assertIn("frame_count=first_window_video_length", finalized_source)
        self.assertIn("loras=loras_selected", finalized_source)
        self.assertIn("attention_backend=attn", finalized_source)


class DurableResourceRetryLaunchTests(unittest.TestCase):
    def _retry_namespace(self, *, attempt=1, boundary=None, prepared=True):
        calls = []

        def try_resource_retry(job, **kwargs):
            calls.append(("requeue", job["id"], kwargs))
            if attempt is None:
                return None
            job["status"] = "queued"
            job["resource_retry_attempt"] = attempt
            return attempt

        namespace = {
            "_MAX_AUTOMATIC_RESOURCE_RETRIES": 2,
            "QueueRecoveryRuntimeError": RuntimeError,
            "try_resource_retry": try_resource_retry,
            "_h3_resource_retry_boundary": lambda _job: boundary,
            "_h3_incomplete_recovery_prefix": (
                lambda _job: 0 if boundary == "generation" else None
            ),
            "_prepare_h3_peak_recovery": (
                lambda job: calls.append(("prepare", job["id"])) or prepared
            ),
            "_queue_recovery_checkpoint": (
                lambda job, **updates: calls.append(
                    ("checkpoint", job["id"], updates)
                ) or job.update(updates) is None
            ),
            "update_queue_job": (
                lambda job, **updates: calls.append(
                    ("queue", job["id"], updates)
                ) or True
            ),
            "_release_model_for_resource_retry": (
                lambda job: calls.append(("release", job["id"]))
            ),
            "_start_generation_worker": (
                lambda job, **kwargs: calls.append(
                    ("start", job["id"], kwargs)
                )
            ),
            "is_cancel_requested": lambda _job: False,
        }
        _load(
            "app/launch.py",
            (
                "_prepare_pending_h3_resource_retry",
                "_try_automatic_resource_retry",
            ),
            namespace,
        )
        return namespace, calls

    def test_host_pressure_retries_same_job_but_impossible_load_is_terminal(self):
        details = {
            "stage": "model_load", "code": "insufficient_host_memory",
            "detail": "safe", "is_oom": False,
        }
        job = {"id": "same-child", "status": "running"}
        namespace, calls = self._retry_namespace()

        self.assertFalse(namespace["_try_automatic_resource_retry"](
            job, {"failure_details": details}, retryable=False,
        ))
        self.assertEqual(calls, [])

        self.assertTrue(namespace["_try_automatic_resource_retry"](
            job, {"failure_details": details}, retryable=True,
        ))
        self.assertEqual(job["id"], "same-child")
        self.assertEqual(
            [entry[0] for entry in calls], ["requeue", "release", "start"],
        )
        self.assertEqual(calls[0][2]["phase"], "model_load")
        self.assertEqual(
            calls[0][2]["reason"], "host_memory_pressure",
        )

    def test_exhausted_resource_retry_stays_terminal(self):
        namespace, calls = self._retry_namespace(attempt=None)
        handled = namespace["_try_automatic_resource_retry"](
            {"id": "exhausted", "status": "running"},
            {"failure_details": {
                "stage": "model_load",
                "code": "insufficient_host_memory",
                "detail": "safe",
                "is_oom": False,
            }},
            retryable=True,
        )
        self.assertFalse(handled)
        self.assertEqual([entry[0] for entry in calls], ["requeue"])

    def test_generation_oom_replans_only_missing_h3_suffix(self):
        namespace, calls = self._retry_namespace(boundary="generation")
        job = {"id": "late-h3", "status": "running"}
        self.assertTrue(namespace["_try_automatic_resource_retry"](
            job,
            {"failure_details": {
                "stage": "denoise", "code": "cuda_oom",
                "detail": "safe", "is_oom": True,
            }},
        ))
        self.assertEqual(
            [entry[0] for entry in calls],
            ["requeue", "prepare", "release", "checkpoint", "queue", "start"],
        )
        self.assertEqual(calls[0][2]["reason"], "generation_oom")

    def test_complete_h3_segments_retry_finalization_without_replan(self):
        namespace = {
            "_queue_recovery_delivery_pending": lambda _job: None,
            "_h3_dependency_closed_recovery_prefix": lambda _job: None,
            "_h3_incomplete_recovery_prefix": lambda _job: None,
            "_queue_recovery_units": lambda _job: [
                {"kind": "h3_segment", "variant": 0, "index": 0},
                {"kind": "h3_segment", "variant": 0, "index": 1},
            ],
        }
        _load(
            "app/launch.py",
            ("_h3_resource_retry_boundary",),
            namespace,
        )
        job = {
            "requested_outputs": 1,
            "params": {"_h3_longform": {"clip_count": 2}},
        }
        self.assertEqual(
            namespace["_h3_resource_retry_boundary"](job),
            "finalization",
        )

    def test_launch_residency_bridge_is_optional_and_fail_open(self):
        records = []
        context = {"context_id": "opaque"}
        active = [True]
        namespace = {
            "wgp": SimpleNamespace(
                get_current_model_residency_evidence_context=(
                    lambda: dict(context) if active[0] else None
                ),
                record_model_residency_runtime_outcome=(
                    lambda outcome, **kwargs: records.append(
                        (outcome, kwargs)
                    ) or True
                ),
            ),
        }
        _load(
            "app/launch.py",
            (
                "_current_model_residency_evidence_context",
                "_record_model_residency_runtime_outcome",
            ),
            namespace,
        )
        captured = namespace[
            "_current_model_residency_evidence_context"
        ]()
        self.assertEqual(captured, context)
        # Delivery/finalization may release the model before surfacing its OOM.
        # The launch bridge must forward the already-captured opaque context.
        active[0] = False
        self.assertIsNone(namespace[
            "_current_model_residency_evidence_context"
        ]())
        self.assertTrue(namespace[
            "_record_model_residency_runtime_outcome"
        ]("oom", phase="finalization", evidence_context=captured))
        self.assertEqual(records[0][0], "oom")
        self.assertEqual(records[0][1]["phase"], "finalization")
        self.assertEqual(records[0][1]["evidence_context"], context)

        run_generation = ast.get_source_segment(
            _source("app/launch.py"),
            next(
                node for node in ast.parse(_source("app/launch.py")).body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_run_generation"
            ),
        )
        self.assertIn(
            "last_residency_context = task_residency_context",
            run_generation,
        )
        self.assertIn(
            '"oom",\n'
            '                                    phase="finalization",\n'
            "                                    evidence_context=last_residency_context,",
            run_generation,
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

        class NativeGpuLock:
            def acquire(self, blocking=True):
                events.append("native-acquire")
                return True

            def release(self):
                events.append("native-release")

        native_gpu_lock = NativeGpuLock()

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
                acquire_native_gpu_execution_lock=(
                    lambda: native_gpu_lock.acquire()
                ),
                native_gpu_execution_lock=native_gpu_lock,
            ),
            "_wgp_native_gpu_slot_state": threading.local(),
        }
        _load(
            "app/launch.py",
            (
                "_WgpNativeGpuWaitCancelled",
                "_WgpNativeGpuExecutionSlot",
                "_release_wgp_model_with_native_gpu_exclusion",
                "_local_llm_uses_native_gpu",
                "_run_llm_with_selection",
            ),
            namespace,
        )

        result = namespace["_run_llm_with_selection"](
            {"provider": "local", "model_id": "local-model"},
            lambda: events.append("operation") or "done",
        )

        self.assertEqual(result, "done")
        self.assertEqual(
            events,
            [
                "native-acquire",
                "h3-release",
                "llm-lease",
                "operation",
                "llm-release",
                "native-release",
            ],
        )

    def test_generation_yields_residency_before_flashvsr_postprocess(self):
        events = []
        namespace = {
            "wan_model": object(),
            "offloadobj": object(),
            "torch": SimpleNamespace(
                cuda=SimpleNamespace(
                    is_available=lambda: False,
                    empty_cache=lambda: events.append("empty"),
                    ipc_collect=lambda: events.append("ipc"),
                ),
            ),
            "gc": SimpleNamespace(collect=lambda: events.append("gc")),
            "flashvsr": SimpleNamespace(
                is_upsampling=lambda value: str(value or "").startswith("flashvsr"),
            ),
            "release_model": lambda: events.append("release_model"),
            "print": lambda *_args, **_kwargs: None,
        }
        _load(
            "app/wgp.py",
            (
                "generation_residency_must_yield_for_postprocess",
                "release_generation_residency_for_postprocess",
            ),
            namespace,
        )
        self.assertTrue(
            namespace["generation_residency_must_yield_for_postprocess"](
                "flashvsr2pass2",
            )
        )
        self.assertFalse(
            namespace["generation_residency_must_yield_for_postprocess"]("")
        )
        self.assertTrue(namespace["release_generation_residency_for_postprocess"]())
        self.assertEqual(events, ["release_model"])
        wgp_source = _source("app/wgp.py")
        self.assertIn(
            "release_generation_residency_for_postprocess()",
            wgp_source,
        )
        self.assertIn(
            "not single_repeat_dispatch",
            wgp_source,
        )
        bridge = _source("app/postprocessing/flashvsr/wgp_bridge.py")
        self.assertIn(
            "release_generation_residency_for_postprocess",
            bridge,
        )
        self.assertIn("decode_frame_budget", bridge)
        self.assertIn("finalization windows", bridge)


if __name__ == "__main__":
    unittest.main()
