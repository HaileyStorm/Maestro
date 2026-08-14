"""Model-free launch/runtime recovery contract tests."""
from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import contextvars
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
import uuid

from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    artifact_descriptor,
    atomic_write_request_manifest,
    cleanup_orphan_request_manifests,
    cleanup_orphan_staged_outputs,
    discover_request_manifest_pointers,
    ensure_recovery_staging_directory,
    load_request_manifest,
    next_recovery_attempt,
    protected_artifact_descriptor,
    promote_recovery_staged_artifact,
    quarantine_artifact,
    remove_request_manifest,
    recovery_unit_id,
    replay_concat_to_stable_output,
    replay_delivery_from_protected_native,
    sha256_file as recovery_sha256_file,
    validate_artifact_descriptor,
    validate_manifest_inputs,
    validate_protected_artifact_descriptor,
    write_sealed_request_manifest,
)
from services.queue_recovery_adapter import (
    QueueRecoveryCoordinator,
    owner_principal_digest,
    project_instance_digest,
)
from services.queue_recovery import QueueRecoveryJournal
from services.h3_benchmark import H3AllocationLedger
from services.h3_offload_plan import (
    H3OffloadPlanError,
    H3_OFFLOAD_PLAN_PARAM_KEY,
    assert_h3_offload_plan_parity,
    build_h3_offload_plan,
    seal_h3_offload_plan,
    validate_h3_offload_plan,
)
from services.h3_duration_plan import (
    GeneratedFrameCount,
    H3DurationOraclePlan,
    H3SegmentFrameRange,
    PublishedFrameCount,
    PublishedFrameGrid,
    redistribute_segment_duration,
    snap_published_duration,
)
from services.output_access import stamp_sidecar_policy
from services.job_lifecycle import authorized_logical_queue_projection


ROOT = Path(__file__).resolve().parents[1]


def _synthetic_scheduler_snapshot(jobs):
    jobs = list(jobs)
    return {
        "paused": False,
        "pause_after_current": False,
        "summary": {},
        "positions": {},
        "states": {id(job): dict(job) for job in jobs},
        "wait_reasons": {},
    }


def _tree(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"), relative)


def _function(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


def _isolated_functions(tree: ast.Module, names: tuple[str, ...], namespace: dict):
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "isolated-launch-recovery", "exec"), namespace)
    return namespace


def _committed_v1_h3_plan(plan: dict) -> dict:
    """Project a synthetic planner result into the committed v1 replay shape."""
    result = copy.deepcopy(plan)
    result["semantic_physical_contract_version"] = 1
    result.pop("prompt_contract_seal", None)
    result.pop("event_ownership", None)
    prompts = [""] * len(result["shots"])
    for contract in result["source_contracts"]:
        contract["prompt_rewrite_for_physical_split"] = False
        for field in (
            "physical_prompt_compiler_version", "event_ownership",
            "executable_prompt_sha256",
        ):
            contract.pop(field, None)
        for position in contract["segment_indices"]:
            prompts[position] = contract["semantic_prompt"]
    for item in result["dialogue_manifest"]:
        contract = result["source_contracts"][item["source_index"]]
        item["segment_index"] = contract["segment_indices"][0]
    for contract in result["source_contracts"]:
        contract["dialogue_manifest"] = [
            copy.deepcopy(item) for item in result["dialogue_manifest"]
            if item["source_index"] == contract["source_index"]
        ]
    result["semantic_shots"] = copy.deepcopy(result["source_contracts"])
    result["clip_prompts"] = prompts
    for index, shot in enumerate(result["shots"]):
        shot["prompt"] = prompts[index]
        shot["dialogue_manifest_indices"] = [
            manifest_index
            for manifest_index, item in enumerate(result["dialogue_manifest"])
            if item["segment_index"] == index
        ]
    return result


class QueueRecoveryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_manifest_is_atomic_private_relative_and_hash_pinned(self):
        pointer = atomic_write_request_manifest(
            self.project,
            job_id="job-a",
            params={"prompt": "private prompt", "input": "/private/source.png"},
            inputs=[{"scope": "synthetic", "path": "/private/source.png"}],
        )
        self.assertEqual(pointer["path"], ".maestro-recovery/job-a.request.json")
        self.assertFalse(os.path.isabs(pointer["path"]))
        path = self.project / pointer["path"]
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        manifest = load_request_manifest(
            self.project, pointer, expected_job_id="job-a",
        )
        self.assertEqual(manifest["params"]["prompt"], "private prompt")
        self.assertEqual(manifest["params"]["input"], "/private/source.png")
        self.assertNotIn("/private/source.png", json.dumps(pointer))

        path.write_text(json.dumps({"schema": 1}), encoding="utf-8")
        with self.assertRaises(QueueRecoveryRuntimeError):
            load_request_manifest(self.project, pointer, expected_job_id="job-a")

    def test_private_manifest_discovery_is_bounded_hash_exact_and_content_free(self):
        first = atomic_write_request_manifest(
            self.project,
            job_id="job-discovery",
            params={"prompt": "PRIVATE_DISCOVERY_SENTINEL"},
            inputs=[],
        )
        second = write_sealed_request_manifest(
            self.project,
            job_id="job-discovery",
            params={"prompt": "PRIVATE_SECOND_SENTINEL"},
            inputs=[],
        )
        exact = discover_request_manifest_pointers(
            self.project, expected_sha256=second["sha256"],
        )
        self.assertEqual(exact, [{
            "job_id": "job-discovery", "pointer": second,
        }])
        projected = json.dumps(exact)
        self.assertNotIn("PRIVATE_DISCOVERY_SENTINEL", projected)
        self.assertNotIn("PRIVATE_SECOND_SENTINEL", projected)
        self.assertEqual(
            {item["pointer"]["sha256"] for item in
             discover_request_manifest_pointers(self.project)},
            {first["sha256"], second["sha256"]},
        )
        with self.assertRaises(QueueRecoveryRuntimeError):
            discover_request_manifest_pointers(
                self.project, maximum_candidates=1,
            )

    def test_manifest_schema_and_every_input_are_fail_closed(self):
        pointer = atomic_write_request_manifest(
            self.project,
            job_id="job-b",
            params={"repeat_generation": 2},
            inputs=[{"id": 1}, {"id": 2}],
        )
        manifest = load_request_manifest(
            self.project, pointer, expected_job_id="job-b",
        )
        visited = []

        def validator(descriptor):
            visited.append(descriptor["id"])
            return descriptor["id"] == 1

        with self.assertRaises(QueueRecoveryRuntimeError):
            validate_manifest_inputs(manifest, validator)
        self.assertEqual(visited, [1, 2])

    def test_prepared_manifest_versions_are_private_immutable_and_loadable(self):
        first = write_sealed_request_manifest(
            self.project,
            job_id="job-prepared",
            params={"prompt": "first private version"},
            inputs=[],
        )
        second = write_sealed_request_manifest(
            self.project,
            job_id="job-prepared",
            params={"prompt": "second private version"},
            inputs=[],
        )
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(
            stat.S_IMODE((self.project / first["path"]).stat().st_mode),
            0o600,
        )
        self.assertEqual(
            load_request_manifest(
                self.project, first, expected_job_id="job-prepared",
            )["params"]["prompt"],
            "first private version",
        )
        self.assertEqual(
            load_request_manifest(
                self.project, second, expected_job_id="job-prepared",
            )["params"]["prompt"],
            "second private version",
        )

    def test_manifest_load_and_remove_reject_symlinked_private_directory(self):
        pointer = atomic_write_request_manifest(
            self.project,
            job_id="job-parent-link",
            params={"prompt": "private"},
            inputs=[],
        )
        recovery_dir = self.project / ".maestro-recovery"
        outside = Path(self.temporary.name) / "outside-recovery"
        recovery_dir.rename(outside)
        recovery_dir.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(QueueRecoveryRuntimeError):
            load_request_manifest(
                self.project, pointer, expected_job_id="job-parent-link",
            )
        with self.assertRaises(QueueRecoveryRuntimeError):
            discover_request_manifest_pointers(self.project)
        self.assertEqual(
            cleanup_orphan_request_manifests(self.project, []), 0,
        )
        self.assertFalse(remove_request_manifest(self.project, pointer))
        self.assertTrue((outside / Path(pointer["path"]).name).is_file())

    def test_manifest_discovery_and_cleanup_pin_parent_directory(self):
        pointer = atomic_write_request_manifest(
            self.project,
            job_id="job-parent-race",
            params={"value": "inside"},
            inputs=[],
        )
        recovery = self.project / ".maestro-recovery"
        moved = self.project / ".maestro-recovery-moved"
        outside = Path(self.temporary.name) / "outside-parent-race"
        outside.mkdir()
        outside_file = outside / Path(pointer["path"]).name
        outside_file.write_text("outside must remain", encoding="utf-8")
        original_listdir = os.listdir
        swapped = False

        def swap_after_list(directory):
            nonlocal swapped
            names = original_listdir(directory)
            if not swapped:
                recovery.rename(moved)
                recovery.symlink_to(outside, target_is_directory=True)
                swapped = True
            return names

        with mock.patch(
            "services.queue_recovery_runtime.os.listdir",
            side_effect=swap_after_list,
        ):
            discovered = discover_request_manifest_pointers(self.project)
        self.assertEqual(discovered[0]["pointer"]["sha256"], pointer["sha256"])
        self.assertEqual(outside_file.read_text(encoding="utf-8"),
                         "outside must remain")

        recovery.unlink()
        moved.rename(recovery)
        swapped = False
        with mock.patch(
            "services.queue_recovery_runtime.os.listdir",
            side_effect=swap_after_list,
        ):
            self.assertEqual(
                cleanup_orphan_request_manifests(self.project, []), 1,
            )
        self.assertEqual(outside_file.read_text(encoding="utf-8"),
                         "outside must remain")

    def test_manifest_discovery_closes_descriptor_on_enumeration_error(self):
        atomic_write_request_manifest(
            self.project,
            job_id="job-enumeration-error",
            params={"value": "private"},
            inputs=[],
        )
        descriptor = os.open(
            self.project / ".maestro-recovery", os.O_RDONLY,
        )
        with mock.patch(
            "services.queue_recovery_runtime._open_private_directory",
            return_value=descriptor,
        ), mock.patch(
            "services.queue_recovery_runtime.os.listdir",
            side_effect=OSError("transient enumeration failure"),
        ):
            with self.assertRaises(QueueRecoveryRuntimeError):
                discover_request_manifest_pointers(self.project)
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_manifest_fallback_without_dir_fd_is_functional_and_link_safe(self):
        pointer = atomic_write_request_manifest(
            self.project,
            job_id="job-no-dir-fd",
            params={"repeat_generation": 1},
            inputs=[],
        )
        with mock.patch(
            "services.queue_recovery_runtime._manifest_dir_fd_supported",
            return_value=False,
        ):
            manifest = load_request_manifest(
                self.project, pointer, expected_job_id="job-no-dir-fd",
            )
            self.assertEqual(manifest["params"]["repeat_generation"], 1)
            self.assertFalse(remove_request_manifest(self.project, pointer))
            self.assertTrue((self.project / pointer["path"]).is_file())

    def test_unit_identity_is_deterministic_and_dependency_sensitive(self):
        first = recovery_unit_id("job-c", "h3_segment", variant=1, index=2)
        self.assertEqual(
            first,
            recovery_unit_id("job-c", "h3_segment", variant=1, index=2),
        )
        dependent = recovery_unit_id(
            "job-c", "concat", variant=1, dependencies=[first],
            settings={"fit": "cover"},
        )
        self.assertNotEqual(first, dependent)
        self.assertNotIn("prompt", dependent)

    def test_artifact_requires_media_sidecar_hash_and_producer_unit(self):
        media = self.project / "result.mp4"
        sidecar = self.project / "result.meta.json"
        media.write_bytes(b"media")
        unit = recovery_unit_id("job-d", "ordinary_repeat", index=0)
        sidecar.write_text(json.dumps({
            "job_id": "job-d", "producer_unit_id": unit,
        }), encoding="utf-8")
        descriptor = artifact_descriptor(
            self.project,
            basename=media.name,
            sidecar_basename=sidecar.name,
            producer_unit_id=unit,
        )
        self.assertTrue(validate_artifact_descriptor(self.project, descriptor))
        media.write_bytes(b"changed")
        self.assertFalse(validate_artifact_descriptor(self.project, descriptor))

        quarantine_artifact(self.project, descriptor)
        self.assertFalse(media.exists())
        self.assertFalse(sidecar.exists())
        quarantined = list((self.project / ".maestro-recovery" / "quarantine").iterdir())
        self.assertEqual(len(quarantined), 2)

    def test_recovery_attempts_are_bounded(self):
        self.assertEqual(next_recovery_attempt({}), (1, True))
        self.assertEqual(next_recovery_attempt({"recovery_attempt": 2}), (3, True))
        self.assertEqual(next_recovery_attempt({"recovery_attempt": 3}), (4, False))

    def test_orphan_manifest_cleanup_is_bounded_and_preserves_live_pointer(self):
        live = atomic_write_request_manifest(
            self.project, job_id="live", params={}, inputs=[],
        )
        atomic_write_request_manifest(
            self.project, job_id="orphan-a", params={}, inputs=[],
        )
        atomic_write_request_manifest(
            self.project, job_id="orphan-b", params={}, inputs=[],
        )
        self.assertEqual(
            cleanup_orphan_request_manifests(
                self.project, [live["path"]], maximum_removals=1,
            ),
            1,
        )
        self.assertTrue((self.project / live["path"]).exists())
        self.assertEqual(
            cleanup_orphan_request_manifests(self.project, [live["path"]]),
            1,
        )

    def test_terminal_staging_cleanup_is_bounded_and_preserves_live_jobs(self):
        staging = Path(ensure_recovery_staging_directory(self.project))
        live = staging / "unit-live-job-t0-r0-w1.mp4"
        stale_one = staging / "unit-stale-job-t0-r0-w1.mp4"
        stale_two = staging / "unit-stale-job-t0-r0-w1.json"
        stale_audio = staging / "unit-stale-job-t0-r0-w1-audio-tmp.wav"
        for path in (live, stale_one, stale_two, stale_audio):
            path.write_bytes(b"staged")
        self.assertEqual(
            cleanup_orphan_staged_outputs(
                self.project, ["live-job"], maximum_removals=1,
            ),
            1,
        )
        self.assertTrue(live.exists())
        self.assertEqual(
            cleanup_orphan_staged_outputs(
                self.project, ["live-job"], maximum_removals=8,
            ),
            2,
        )
        self.assertTrue(live.exists())

    def test_crash_left_preprocessing_audio_is_never_public_and_is_retired(self):
        staging = Path(ensure_recovery_staging_directory(self.project))
        temporary_audio = (
            staging / "unit-terminal-job-t0-pre-control-audio.wav"
        )
        temporary_audio.write_bytes(b"private-preprocessing")
        self.assertEqual(
            [path.name for path in self.project.iterdir() if path.suffix == ".wav"],
            [],
        )
        self.assertEqual(
            cleanup_orphan_staged_outputs(
                self.project, [], maximum_removals=8,
            ),
            1,
        )
        self.assertFalse(temporary_audio.exists())

    def test_interrupted_concat_replays_only_components_to_one_stable_final(self):
        first = self.project / "segment-0.mp4"
        second = self.project / "segment-1.mp4"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        calls = []

        def concatenate(paths, staging):
            calls.append([Path(path).name for path in paths])
            Path(staging).write_bytes(b"+".join(Path(path).read_bytes() for path in paths))
            return True

        # Simulate a crash after promotion but before any journal checkpoint,
        # then restart the concat-only unit. It replaces the same basename.
        for _ in range(2):
            result = replay_concat_to_stable_output(
                self.project,
                component_basenames=[first.name, second.name],
                output_basename="job-v1-multiclip.mp4",
                concatenate=concatenate,
            )
            self.assertEqual(result, "job-v1-multiclip.mp4")
        self.assertEqual(calls, [[first.name, second.name], [first.name, second.name]])
        self.assertEqual(
            [path.name for path in self.project.glob("*multiclip*.mp4")],
            ["job-v1-multiclip.mp4"],
        )
        self.assertEqual(
            (self.project / "job-v1-multiclip.mp4").read_bytes(),
            b"first+second",
        )

    def test_interrupted_delivery_replays_only_verified_native_to_stable_work(self):
        native = self.project / ".maestro-delivery-job-x.native.mp4"
        sidecar = self.project / ".maestro-delivery-job-x.native.meta.json"
        native.write_bytes(b"native")
        sidecar.write_text('{"private":true}', encoding="utf-8")
        unit = recovery_unit_id(
            "job-delivery", "h3_delivery", settings={"fit": "exact"},
        )
        descriptor = protected_artifact_descriptor(
            self.project,
            basename=native.name,
            sidecar_basename=sidecar.name,
            original_basename="final.mp4",
            producer_unit_id=unit,
        )
        calls = []

        def deliver(source, staging):
            calls.append(Path(source).name)
            Path(staging).write_bytes(Path(source).read_bytes() + b"-delivered")
            return True

        for _ in range(2):
            replay_delivery_from_protected_native(
                self.project,
                protected_descriptor=descriptor,
                work_basename=".maestro-delivery-job-x.work.mp4",
                deliver=deliver,
            )
        self.assertEqual(calls, [native.name, native.name])
        self.assertEqual(
            (self.project / ".maestro-delivery-job-x.work.mp4").read_bytes(),
            b"native-delivered",
        )
        self.assertEqual(
            len(list(self.project.glob(".maestro-delivery-job-x.work.mp4"))), 1,
        )
        native.write_bytes(b"tampered")
        self.assertFalse(
            validate_protected_artifact_descriptor(self.project, descriptor),
        )
        with self.assertRaises(QueueRecoveryRuntimeError):
            replay_delivery_from_protected_native(
                self.project,
                protected_descriptor=descriptor,
                work_basename=".maestro-delivery-job-x.work.mp4",
                deliver=deliver,
            )

    def test_recovery_subdirectories_reject_preexisting_symlinks(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        recovery = self.project / ".maestro-recovery"
        recovery.mkdir()
        (recovery / "staging").symlink_to(outside, target_is_directory=True)
        component = self.project / "segment.mp4"
        component.write_bytes(b"segment")
        with self.assertRaises(QueueRecoveryRuntimeError):
            replay_concat_to_stable_output(
                self.project,
                component_basenames=[component.name],
                output_basename="joined.mp4",
                concatenate=lambda _sources, target: (
                    Path(target).write_bytes(b"joined") is None
                ),
            )
        self.assertEqual(list(outside.iterdir()), [])
        (recovery / "staging").unlink()
        (recovery / "quarantine").symlink_to(outside, target_is_directory=True)
        media = self.project / "result.mp4"
        sidecar = self.project / "result.meta.json"
        unit = recovery_unit_id("job-link", "ordinary_repeat")
        media.write_bytes(b"media")
        sidecar.write_text(json.dumps({
            "producer_unit_id": unit,
        }), encoding="utf-8")
        descriptor = artifact_descriptor(
            self.project,
            basename=media.name,
            sidecar_basename=sidecar.name,
            producer_unit_id=unit,
        )
        with self.assertRaises(QueueRecoveryRuntimeError):
            quarantine_artifact(self.project, descriptor)
        self.assertTrue(media.exists())
        self.assertEqual(list(outside.iterdir()), [])

    def _assert_media_before_sidecar_crash_is_hidden_and_stable(self, kind: str):
        staging = Path(ensure_recovery_staging_directory(self.project))
        basename = f"unit-job-crash-{kind}-r0-w1.mp4"
        staged = staging / basename
        staged.write_bytes(b"interrupted-native")
        self.assertFalse((self.project / basename).exists())
        self.assertEqual(
            [path for path in self.project.iterdir() if path.suffix == ".mp4"],
            [],
        )

        # Restart reruns the interrupted denoise into the same private target.
        staged.write_bytes(b"verified-native")
        unit_id = recovery_unit_id("job-crash", kind)
        sidecar = self.project / f"{Path(basename).stem}.meta.json"
        with sidecar.open("w", encoding="utf-8") as handle:
            json.dump({"producer_unit_id": unit_id}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        self.assertTrue(sidecar.exists())
        self.assertFalse((self.project / basename).exists())
        promote_recovery_staged_artifact(
            self.project,
            staged_path=staged,
            output_basename=basename,
        )
        descriptor = artifact_descriptor(
            self.project,
            basename=basename,
            sidecar_basename=sidecar.name,
            producer_unit_id=unit_id,
        )
        self.assertTrue(validate_artifact_descriptor(
            self.project, descriptor, producer_unit_id=unit_id,
        ))
        self.assertEqual(
            [path.name for path in self.project.iterdir() if path.suffix == ".mp4"],
            [basename],
        )

    def test_ordinary_media_save_crash_never_creates_public_duplicate(self):
        self._assert_media_before_sidecar_crash_is_hidden_and_stable(
            "ordinary_repeat",
        )

    def test_h3_segment_media_save_crash_never_creates_public_duplicate(self):
        self._assert_media_before_sidecar_crash_is_hidden_and_stable(
            "h3_segment",
        )


class QueueLaunchWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = _tree("app/launch.py")
        cls.wgp = _tree("app/wgp.py")
        cls.launch_source = (ROOT / "app/launch.py").read_text(encoding="utf-8")
        cls.wgp_source = (ROOT / "app/wgp.py").read_text(encoding="utf-8")

    def test_bootstrap_binds_hook_before_registry_and_http_submission(self):
        coordinator = self.launch_source.index("_queue_recovery_coordinator =")
        hook = self.launch_source.index("configure_durability_hook(", coordinator)
        registry = self.launch_source.index("class _JobRegistry", hook)
        generation_route = self.launch_source.index('@api.post("/api/v1/generate")')
        self.assertLess(coordinator, hook)
        self.assertLess(hook, registry)
        self.assertLess(registry, generation_route)

    def test_h3_sidecar_params_are_frozen_before_media_materialization(self):
        namespace = _isolated_functions(
            self.launch,
            ("_snapshot_h3_recovery_task_params",),
            {
                "json": json,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            },
        )
        snapshot = namespace["_snapshot_h3_recovery_task_params"]
        params = {
            "image_refs": ["authorized-reference.png"],
            "multi_clip_info": {"automatic_h3_longform": True},
        }
        frozen = snapshot(params, params["multi_clip_info"])
        params["image_refs"][0] = object()
        self.assertEqual(frozen["image_refs"], ["authorized-reference.png"])
        json.dumps(frozen)
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError, "not serializable",
        ):
            snapshot(
                {
                    "image_refs": [object()],
                    "multi_clip_info": {"automatic_h3_longform": True},
                },
                {"automatic_h3_longform": True},
            )
        worker = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        freeze_at = worker.index("h3_task_sidecar_params: dict[str, dict]")
        parse_at = worker.index("queue, error = wgp._parse_task_manifest(")
        validate_at = worker.index("validated_params = wgp.validate_task(")
        self.assertLess(freeze_at, parse_at)
        self.assertLess(parse_at, validate_at)
        self.assertIn(
            'task_sidecar_params = h3_task_sidecar_params.get(',
            worker[parse_at:validate_at],
        )
        self.assertIn(
            "task_params=task_sidecar_params",
            worker[validate_at:],
        )

    def test_registration_precedes_publication_and_thread_start(self):
        node = _function(self.launch, "_queue_recovery_register_and_publish")
        source = ast.get_source_segment(self.launch_source, node)
        self.assertIsNotNone(source)
        manifest = source.index("atomic_write_request_manifest(")
        register = source.index("_queue_recovery_coordinator.register_job(", manifest)
        publish = source.index("_jobs.publish_prepared(", register)
        start = source.index("thread.start()", publish)
        self.assertLess(manifest, register)
        self.assertLess(register, publish)
        self.assertLess(publish, start)

    def test_h3_offload_plan_is_sealed_before_admission_and_manifest_swap(self):
        register = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_queue_recovery_register_and_publish"),
        )
        self.assertLess(
            register.index("_seal_h3_offload_plan_for_job("),
            register.index("_jobs.prepare("),
        )
        preparation = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_run_generation_preparation"),
        )
        self.assertLess(
            preparation.index("_seal_h3_offload_plan_for_job("),
            preparation.index("write_sealed_request_manifest("),
        )
        self.assertIn("h3_offload_plan", preparation)
        approval = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_approve_waiting_generation_plan"),
        )
        self.assertLess(
            approval.index("_seal_h3_offload_plan_for_job("),
            approval.index("write_sealed_request_manifest("),
        )

    def test_h3_offload_plan_parity_precedes_restart_and_worker_use(self):
        materialize = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_queue_recovery_materialize_job"),
        )
        self.assertLess(
            materialize.index("_require_h3_offload_plan_parity("),
            materialize.index("_queue_recovery_reconcile_cursor("),
        )
        self.assertIn('runtime["_h3_offload_legacy_recovery"] = True', materialize)
        parity = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_require_h3_offload_plan_parity"),
        )
        self.assertIn('job.get("_h3_offload_legacy_recovery") is True', parity)
        self.assertIn("params=params", parity)
        self.assertNotIn("allow_legacy", parity)
        worker = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        self.assertLess(
            worker.index("_require_h3_offload_plan_parity("),
            worker.index("_apply_per_job_coefficient(job)"),
        )
        public_status = ast.get_source_segment(
            self.launch_source, _function(self.launch, "get_status"),
        )
        self.assertIn("public_h3_offload_plan(", public_status)
        self.assertNotIn('get("digest")', public_status)

    def test_legacy_peak_recovery_preserves_prefix_profile_identity(self):
        recovery = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_prepare_h3_peak_recovery"),
        )
        prior = recovery.index(
            "prior_effective_profile = _h3_effective_offload_profile("
        )
        prefix = recovery.index(
            "segment_profiles.append(prior_effective_profile)"
        )
        suffix = recovery.index("segment_profiles.append(recovered_profile)")
        self.assertLess(prior, prefix)
        self.assertLess(prefix, suffix)

    def test_child_dispatch_uses_each_recovery_segment_profile(self):
        params = {
            "model_type": "minimax_h3",
            "resolution": "1344x768",
            "num_inference_steps": 20,
            "override_profile": 5,
            "_h3_longform": {
                "fps": 24,
                "clip_frames": [124, 141],
                "clip_published_frames": [124, 134],
                "segment_models": [
                    {"model_type": "minimax_h3"},
                    {"model_type": "minimax_h3_ref2va"},
                ],
            },
        }
        plan = build_h3_offload_plan(
            params,
            effective_profile=5,
            source="recovery_profile",
            segment_profiles=[4, 5],
        )
        manifest = []
        for output_index in range(2):
            for index, segment in enumerate(plan["segments"]):
                manifest.append({
                    "id": len(manifest) + 1,
                    "params": {
                        "model_type": segment["model_type"],
                        "video_length": segment["generated_frames"],
                        "override_profile": -1,
                        "multi_clip_info": {
                            "automatic_h3_longform": True,
                            "index": index,
                            "output_index": output_index,
                        },
                    },
                })
        namespace = _isolated_functions(
            self.launch,
            ("_apply_h3_offload_plan_to_manifest",),
            {
                "validate_h3_offload_plan": validate_h3_offload_plan,
                "H3OffloadPlanError": H3OffloadPlanError,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            },
        )
        namespace["_apply_h3_offload_plan_to_manifest"](manifest, plan)
        self.assertEqual(
            [task["params"]["override_profile"] for task in manifest],
            [4, 5, 4, 5],
        )

    def test_enhance_before_generate_admits_then_prepares_without_gpu_slot(self):
        submit = ast.get_source_segment(
            self.launch_source, _function(self.launch, "generate"),
        )
        preparation = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_run_generation_preparation"),
        )
        llm_authority = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_run_authorized_llm_with_selection"),
        )
        self.assertIn('body.pop(\n        "enhance_before_generate"', submit)
        self.assertIn(
            '"preparing" if durable_generation_preparation else "queued"',
            submit,
        )
        self.assertIn("_queue_recovery_register_and_publish(", submit)
        self.assertIn("complete_preparation(", preparation)
        self.assertIn("write_sealed_request_manifest(", preparation)
        self.assertIn(
            'enhancement_params["prompt"] = role_base_prompt', preparation,
        )
        self.assertIn(
            "_apply_authoritative_generation_prompt(prepared_params, enhanced)",
            preparation,
        )
        self.assertNotIn("generation_slot(", preparation)
        self.assertNotIn("with _gen_lock", preparation)
        self.assertIn("maestro_cpu_text_operation", llm_authority)
        self.assertIn("_coordinate_generation=True", llm_authority)

    def test_plan_approval_uses_sealed_source_without_repeating_llm(self):
        approval = ast.get_source_segment(
            self.launch_source, _function(self.launch, "approve_generation_plan"),
        )
        promotion = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_approve_waiting_generation_plan"),
        )
        self.assertIn("_approve_waiting_generation_plan(", approval)
        self.assertIn("load_request_manifest(", promotion)
        self.assertIn('"_maestro_prepared_source"', promotion)
        self.assertIn("write_sealed_request_manifest(", promotion)
        self.assertIn("approve_prepared_job(", promotion)
        self.assertNotIn("llm_enhance_prompt", approval)
        self.assertNotIn("llm_enhance_prompt", promotion)

    def test_public_h3_plan_exposes_server_owned_duration_contract(self):
        namespace = _isolated_functions(
            self.launch,
            (
                "_h3_duration_plan_revision",
                "_public_h3_boundary",
                "_public_h3_long_plan",
            ),
            {
                "copy": copy,
                "hashlib": hashlib,
                "json": json,
            },
        )
        plan = {
            "clip_count": 2,
            "fps": 24,
            "requested_frames": 265,
            "planned_frames": 265,
            "published_frames": 265,
            "clip_frames": [124, 141],
            "clip_published_frames": [124, 141],
            "segment_models": [
                {"model_type": "minimax_h3", "reason": "test"},
                {"model_type": "minimax_h3", "reason": "test"},
            ],
            "clip_boundaries": [
                {"type": "continuous", "source": "model_grid"},
            ],
            "_duration_target_published_frames": 265,
            "_duration_segment_limits": [
                {
                    "index": 0, "minimum": 124, "maximum": 345,
                    "step": 1, "offset": 0, "authored_locked": True,
                },
                {
                    "index": 1, "minimum": 124, "maximum": 345,
                    "step": 1, "offset": 0, "authored_locked": False,
                },
            ],
            "_duration_snap_candidates": {
                "nearest": {"candidate_published_frames": 345},
                "down": {"candidate_published_frames": 265},
            },
            "_duration_redistribution_mode": "future",
            "_duration_outcome": "exact",
            "_duration_reason": "Exact test plan.",
            "_duration_residual_published_frames": 0,
            "_duration_completed_locks": [1],
        }
        plan["_duration_revision"] = namespace[
            "_h3_duration_plan_revision"
        ](plan)
        public = namespace["_public_h3_long_plan"](plan)
        duration = public["duration_plan"]
        self.assertTrue(duration["revision"].startswith("h3dp1_"))
        self.assertEqual(duration["target_published_frames"], 265)
        self.assertEqual(duration["current_published_frames"], 265)
        self.assertEqual(duration["current_generated_frames"], 265)
        self.assertEqual(duration["residual_published_frames"], 0)
        self.assertEqual(duration["redistribution_mode"], "future")
        self.assertEqual(duration["segments"][0]["lock_reason"], "authored")
        self.assertEqual(duration["segments"][1]["lock_reason"], "completed")
        changed = copy.deepcopy(plan)
        changed["clip_published_frames"][1] -= 1
        self.assertNotEqual(
            namespace["_h3_duration_plan_revision"](changed),
            duration["revision"],
        )

    def test_duration_approval_requires_current_server_revision(self):
        namespace = _isolated_functions(
            self.launch,
            ("_h3_duration_plan_revision", "_apply_h3_duration_approval"),
            {"hashlib": hashlib, "hmac": hmac, "json": json},
        )
        plan = {
            "clip_frames": [124, 141],
            "clip_published_frames": [124, 141],
            "published_frames": 265,
        }
        plan["_duration_revision"] = namespace[
            "_h3_duration_plan_revision"
        ](plan)
        with self.assertRaisesRegex(ValueError, "revision is stale"):
            namespace["_apply_h3_duration_approval"](
                {},
                plan,
                plan_revision="h3dp1_" + "0" * 64,
                duration_snap_mode=None,
                segment_duration_edits=[],
                duration_redistribution=None,
            )

    def test_duration_stamp_uses_authoritative_oracle_and_integer_bounds(self):
        class FakeWgp:
            @staticmethod
            def get_model_def(_model_type):
                return {
                    "frames_minimum": 124,
                    "frames_maximum": 345,
                    "fps": 24,
                }

        def oracle_factory(_body):
            def oracle(total):
                value = int(total)
                count = (value + 344) // 345
                base, extra = divmod(value, count)
                published = tuple(
                    PublishedFrameCount(base + int(index < extra))
                    for index in range(count)
                )
                return H3DurationOraclePlan(
                    requested_published_frames=PublishedFrameCount(value),
                    generated_frames=tuple(
                        GeneratedFrameCount(int(item)) for item in published
                    ),
                    published_frames=published,
                    confidence="high",
                    reason="Synthetic authoritative planner evidence.",
                )
            return oracle

        namespace = _isolated_functions(
            self.launch,
            (
                "_h3_duration_segment_limits",
                "_h3_duration_plan_revision",
                "_public_h3_duration_snap_result",
                "_stamp_h3_duration_contract",
            ),
            {
                "GeneratedFrameCount": GeneratedFrameCount,
                "H3DurationOraclePlan": H3DurationOraclePlan,
                "PublishedFrameCount": PublishedFrameCount,
                "PublishedFrameGrid": PublishedFrameGrid,
                "copy": copy,
                "hashlib": hashlib,
                "json": json,
                "snap_published_duration": snap_published_duration,
                "wgp": FakeWgp,
                "_h3_duration_oracle": oracle_factory,
            },
        )
        plan = {
            "clip_count": 2,
            "fps": 24,
            "requested_frames": 480,
            "published_frames": 480,
            "clip_frames": [243, 243],
            "clip_published_frames": [240, 240],
            "segment_frames_maximum": 345,
            "segment_models": [
                {"model_type": "minimax_h3"},
                {"model_type": "minimax_h3"},
            ],
            "segment_policy": {"id": "native_default"},
        }
        namespace["_stamp_h3_duration_contract"](
            {"model_type": "minimax_h3"}, plan,
        )
        self.assertTrue(plan["_duration_revision"].startswith("h3dp1_"))
        self.assertEqual(plan["_duration_segment_limits"][0]["minimum"], 124)
        self.assertEqual(plan["_duration_segment_limits"][0]["step"], 1)
        self.assertEqual(
            set(plan["_duration_snap_candidates"]), {"nearest", "down"},
        )
        self.assertTrue(all(
            type(candidate["candidate_published_frames"]) in {int, type(None)}
            for candidate in plan["_duration_snap_candidates"].values()
        ))

    def test_duration_snap_replans_from_source_not_normalized_multiclip_state(self):
        oracle_source = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_h3_duration_oracle"),
        )
        approval_source = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_apply_h3_duration_approval"),
        )
        self.assertIn('candidate["multi_prompts_gen_type"] = 0', oracle_source)
        self.assertIn('candidate["prompt"] = authoritative_prompt', oracle_source)
        self.assertLess(
            oracle_source.index('candidate["multi_prompts_gen_type"] = 0'),
            oracle_source.index("_prepare_h3_long_studio_request(candidate)"),
        )
        self.assertIn("_replay_h3_duration_shot_plan(", approval_source)
        self.assertNotIn(
            '_prepare_h3_long_studio_request(prepared_params)',
            approval_source,
        )

    def test_one_segment_snap_replays_sealed_sources_and_rejects_hybrids(self):
        from services.director_pipeline import (
            _bind_director_h3_runtime_contract,
            _validate_director_h3_runtime_contract,
        )
        from services.h3_shot_planner import plan_h3_native_shots

        class FakeWgp:
            @staticmethod
            def get_model_def(_model_type):
                return {"frames_minimum": 124, "frames_maximum": 345}

            @staticmethod
            def align_model_frame_count(frame_count, _model_def):
                return max(124, int(frame_count))

        namespace = _isolated_functions(
            self.launch,
            (
                "_h3_duration_segment_limits",
                "_h3_duration_plan_revision",
                "_h3_duration_segment_oracle",
                "_replay_h3_duration_shot_plan",
                "_apply_h3_duration_approval",
            ),
            {
                "GeneratedFrameCount": GeneratedFrameCount,
                "H3DurationOraclePlan": H3DurationOraclePlan,
                "H3SegmentFrameRange": H3SegmentFrameRange,
                "PublishedFrameCount": PublishedFrameCount,
                "PublishedFrameGrid": PublishedFrameGrid,
                "copy": copy,
                "hashlib": hashlib,
                "hmac": hmac,
                "json": json,
                "redistribute_segment_duration": redistribute_segment_duration,
                "wgp": FakeWgp,
                "_MULTI_CLIP_SEPARATOR": "\n---MAESTRO-CLIP---\n",
                "_plan_h3_adaptive_models": (
                    lambda _body, *, clip_count, **_kwargs: [
                        {"model_type": "minimax_h3"}
                        for _ in range(clip_count)
                    ]
                ),
                "_stamp_h3_duration_contract": (
                    lambda _body, target: target.update({
                        "_duration_revision": "h3dp1_resealed",
                    })
                ),
            },
        )
        source = "An adult archivist closes a ledger in a quiet archive."
        shot_plan = plan_h3_native_shots(
            global_prompt=source,
            structured_shots=[{
                "shot_id": "archive",
                "spatial_setup": "The archivist stands beside the desk",
                "closing_blocking": "The ledger closes",
            }],
            clip_frame_counts=[175, 175],
            clip_requested_frames=[175, 171],
            fps=24,
        )
        plan = {
            "clip_count": 2,
            "fps": 24,
            "global_prompt": source,
            "requested_frames": 346,
            "published_frames": 346,
            "planned_frames": 350,
            "clip_frames": [175, 175],
            "clip_published_frames": [175, 171],
            "clip_boundaries": list(shot_plan["clip_boundaries"]),
            "segment_frames_maximum": 345,
            "segment_models": [
                {"model_type": "minimax_h3"},
                {"model_type": "minimax_h3"},
            ],
            "segment_source_indices": [0, 0],
            "h3_style_workflow": {"synthetic": "sealed-style"},
            "shot_plan": shot_plan,
            "_duration_target_published_frames": 346,
            "_duration_segment_limits": [
                {"minimum": 124, "maximum": 345, "step": 1, "offset": 0},
                {"minimum": 124, "maximum": 345, "step": 1, "offset": 0},
            ],
            "_duration_snap_candidates": {
                "down": {
                    "candidate_published_frames": 345,
                    "segment_count": 1,
                    "generated_frames": [345],
                    "segment_published_frames": [345],
                    "confidence": "high",
                },
            },
        }
        shot_plan["h3_style_workflow"] = copy.deepcopy(
            plan["h3_style_workflow"]
        )
        _bind_director_h3_runtime_contract(plan)
        plan["_duration_revision"] = namespace[
            "_h3_duration_plan_revision"
        ](plan)
        params = {"model_type": "minimax_h3"}
        revised = namespace["_apply_h3_duration_approval"](
            params,
            copy.deepcopy(plan),
            plan_revision=plan["_duration_revision"],
            duration_snap_mode="down",
            segment_duration_edits=[],
            duration_redistribution=None,
        )
        self.assertEqual(revised["clip_count"], 1)
        self.assertEqual(revised["clip_published_frames"], [345])
        self.assertEqual(
            revised["shot_plan"]["source_contracts"][0]["authored_prompt"],
            shot_plan["source_contracts"][0]["authored_prompt"],
        )
        self.assertEqual(
            [shot["source_index"] for shot in revised["shot_plan"]["shots"]],
            [0],
        )
        self.assertEqual(revised["segment_source_indices"], [0])
        self.assertEqual(
            revised["shot_plan"]["h3_style_workflow"],
            plan["h3_style_workflow"],
        )
        _validate_director_h3_runtime_contract(revised, revised["shot_plan"])

        manual = copy.deepcopy(plan)
        manual_revision = namespace["_h3_duration_plan_revision"](manual)
        manual_revised = namespace["_apply_h3_duration_approval"](
            {"model_type": "minimax_h3"},
            manual,
            plan_revision=manual_revision,
            duration_snap_mode="manual",
            segment_duration_edits=[
                {"segment_index": 2, "published_frames": 170},
            ],
            duration_redistribution="none",
        )
        _validate_director_h3_runtime_contract(
            manual_revised, manual_revised["shot_plan"],
        )
        self.assertEqual(
            manual_revised["shot_plan"]["h3_style_workflow"],
            plan["h3_style_workflow"],
        )
        multi_source = plan_h3_native_shots(
            global_prompt="Two sealed authored sources.",
            source_prompts=["First authored source.", "Second authored source."],
            source_indices=[0, 1],
            clip_frame_counts=[175, 175],
            fps=24,
        )
        with self.assertRaisesRegex(ValueError, "preserve every sealed H3 source"):
            namespace["_replay_h3_duration_shot_plan"](
                {
                    "global_prompt": "Two sealed authored sources.",
                    "fps": 24,
                    "segment_frames_maximum": 345,
                    "shot_plan": multi_source,
                },
                generated=[345],
                published=[345],
            )
        for edits, redistribution in (([{"segment_index": 1, "published_frames": 340}], None), ([], "none")):
            with self.subTest(edits=edits, redistribution=redistribution):
                with self.assertRaisesRegex(ValueError, "cannot be combined"):
                    namespace["_apply_h3_duration_approval"](
                        {}, copy.deepcopy(plan),
                        plan_revision=plan["_duration_revision"],
                        duration_snap_mode="down",
                        segment_duration_edits=edits,
                        duration_redistribution=redistribution,
                    )

    def test_manual_duration_edit_replans_generated_and_published_geometry(self):
        class FakeWgp:
            @staticmethod
            def get_model_def(_model_type):
                return {
                    "frames_minimum": 124,
                    "frames_maximum": 345,
                    "frame_alignment_modulus": 17,
                    "frame_alignment_remainder": 5,
                }

            @staticmethod
            def align_model_frame_count(frame_count, _model_def):
                value = max(124, int(frame_count))
                remainder = (value - 5) % 17
                return value if remainder == 0 else value + 17 - remainder

        namespace = _isolated_functions(
            self.launch,
            (
                "_h3_duration_plan_revision",
                "_h3_duration_segment_oracle",
                "_apply_h3_duration_approval",
            ),
            {
                "GeneratedFrameCount": GeneratedFrameCount,
                "H3DurationOraclePlan": H3DurationOraclePlan,
                "H3SegmentFrameRange": H3SegmentFrameRange,
                "PublishedFrameCount": PublishedFrameCount,
                "PublishedFrameGrid": PublishedFrameGrid,
                "copy": copy,
                "hashlib": hashlib,
                "hmac": hmac,
                "json": json,
                "redistribute_segment_duration": redistribute_segment_duration,
                "wgp": FakeWgp,
                "_MULTI_CLIP_SEPARATOR": "\n---MAESTRO-CLIP---\n",
                "_replay_h3_duration_shot_plan": (
                    lambda _plan, *, generated, published: {
                        "clip_prompts": [f"clip-{index}" for index in range(len(generated))],
                        "clip_published_frames": list(published),
                        "clip_boundaries": list(_plan.get("clip_boundaries") or []),
                        "segment_policy": {
                            "clip_requested_frames": list(published),
                        },
                    }
                ),
                "_stamp_h3_duration_contract": (
                    lambda _body, target: target.update({
                        "_duration_revision": "h3dp1_resealed",
                    })
                ),
            },
        )
        plan = {
            "clip_count": 2,
            "fps": 24,
            "global_prompt": "A camera glides through a quiet room.",
            "requested_frames": 265,
            "published_frames": 265,
            "planned_frames": 265,
            "clip_frames": [124, 141],
            "clip_published_frames": [124, 141],
            "clip_boundaries": [
                {"type": "continuous", "source": "model_grid"},
            ],
            "segment_frames_maximum": 345,
            "segment_models": [
                {"model_type": "minimax_h3"},
                {"model_type": "minimax_h3"},
            ],
            "_duration_target_published_frames": 265,
            "_duration_segment_limits": [
                {
                    "minimum": 124, "maximum": 345,
                    "step": 1, "offset": 0, "authored_locked": False,
                },
                {
                    "minimum": 124, "maximum": 345,
                    "step": 1, "offset": 0, "authored_locked": False,
                },
            ],
        }
        plan["_duration_revision"] = namespace[
            "_h3_duration_plan_revision"
        ](plan)
        params = {"model_type": "minimax_h3"}
        revised = namespace["_apply_h3_duration_approval"](
            params,
            plan,
            plan_revision=plan["_duration_revision"],
            duration_snap_mode="manual",
            segment_duration_edits=[
                {"segment_index": 2, "published_frames": 130},
            ],
            duration_redistribution="none",
        )
        self.assertEqual(revised["clip_frames"], [124, 141])
        self.assertEqual(revised["clip_published_frames"], [124, 130])
        self.assertEqual(revised["published_frames"], 254)
        self.assertEqual(revised["_duration_residual_published_frames"], 11)
        self.assertEqual(revised["_duration_outcome"], "acceptable")
        self.assertEqual(params["video_length"], 254)
        self.assertEqual(params["_h3_longform"], revised)

    def test_duration_controls_flow_through_the_atomic_approval(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        revision = "h3dp1_" + "a" * 64
        captured = {}

        class FakeRequest:
            async def json(self):
                return {
                    "workspace": "project-a",
                    "plan_revision": revision,
                    "duration_snap_mode": "manual",
                    "segment_duration_edits": [
                        {"segment_index": 2, "published_frames": 130},
                    ],
                    "duration_redistribution": "future",
                }

        job = {
            "id": "job-duration-plan",
            "status": "waiting_for_plan_approval",
            "plan_review_deadline": None,
        }

        def approve(_job, **kwargs):
            captured.update(kwargs)
            return {"status": "queued"}

        namespace = _isolated_functions(
            self.launch,
            ("approve_generation_plan",),
            {
                "api": types.SimpleNamespace(
                    post=lambda *_args, **_kwargs: lambda function: function,
                ),
                "Request": object,
                "Response": object,
                "HTTPException": FakeHTTPException,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "math": __import__("math"),
                "time": time,
                "_set_recovery_no_store": lambda _response: None,
                "_require_owned_job_project": lambda *_args: job,
                "_approve_waiting_generation_plan": approve,
                "fail_preparation": lambda *_args, **_kwargs: True,
            },
        )
        result = asyncio.run(namespace["approve_generation_plan"](
            job["id"], FakeRequest(), object(),
        ))
        self.assertEqual(result["status"], "queued")
        self.assertEqual(captured["plan_revision"], revision)
        self.assertEqual(captured["duration_snap_mode"], "manual")
        self.assertEqual(captured["duration_redistribution"], "future")
        self.assertEqual(captured["segment_duration_edits"][0]["segment_index"], 2)

    def test_duration_controls_reject_unhashable_values_as_bad_requests(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        job = {
            "id": "job-duration-plan",
            "status": "waiting_for_plan_approval",
            "plan_review_deadline": None,
        }
        namespace = _isolated_functions(
            self.launch,
            ("approve_generation_plan",),
            {
                "api": types.SimpleNamespace(
                    post=lambda *_args, **_kwargs: lambda function: function,
                ),
                "Request": object,
                "Response": object,
                "HTTPException": FakeHTTPException,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "math": __import__("math"),
                "time": time,
                "_set_recovery_no_store": lambda _response: None,
                "_require_owned_job_project": lambda *_args: job,
                "_approve_waiting_generation_plan": lambda *_args, **_kwargs: (
                    self.fail("invalid duration controls reached approval")
                ),
                "fail_preparation": lambda *_args, **_kwargs: True,
            },
        )

        for field in ("duration_snap_mode", "duration_redistribution"):
            class FakeRequest:
                async def json(self, selected_field=field):
                    return {"workspace": "project-a", selected_field: []}

            with self.subTest(field=field):
                with self.assertRaises(FakeHTTPException) as raised:
                    asyncio.run(namespace["approve_generation_plan"](
                        job["id"], FakeRequest(), object(),
                    ))
                self.assertEqual(raised.exception.status_code, 400)

        hybrid_bodies = [
            {
                "workspace": "project-a",
                "duration_snap_mode": "nearest",
                "segment_duration_edits": [
                    {"segment_index": 1, "published_frames": 345},
                ],
            },
            {
                "workspace": "project-a",
                "duration_snap_mode": "down",
                "duration_redistribution": "none",
            },
        ]
        for hybrid_body in hybrid_bodies:
            class HybridRequest:
                async def json(self, selected_body=hybrid_body):
                    return selected_body

            with self.subTest(hybrid_body=hybrid_body):
                with self.assertRaises(FakeHTTPException) as raised:
                    asyncio.run(namespace["approve_generation_plan"](
                        job["id"], HybridRequest(), object(),
                    ))
                self.assertEqual(raised.exception.status_code, 400)

    def test_recovery_reseal_refreshes_duration_locks_and_fingerprint(self):
        recovery = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_replan_h3_final_segment_for_peak"),
        )
        self.assertIn(
            'longform["_duration_completed_locks"] = list(range(completed_prefix))',
            recovery,
        )
        self.assertIn("_stamp_h3_duration_contract(result, longform)", recovery)

    def test_corrupt_waiting_manifest_terminalizes_on_manual_approval(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class FakeRequest:
            async def json(self):
                return {"workspace": "project-a"}

        job = {
            "id": "job-corrupt-plan",
            "status": "waiting_for_plan_approval",
            "plan_review_deadline": None,
        }
        namespace = _isolated_functions(
            self.launch,
            ("approve_generation_plan",),
            {
                "api": types.SimpleNamespace(
                    post=lambda *_args, **_kwargs: lambda function: function,
                ),
                "Request": object,
                "Response": object,
                "HTTPException": FakeHTTPException,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "math": __import__("math"),
                "time": time,
                "_set_recovery_no_store": lambda _response: None,
                "_require_owned_job_project": lambda *_args: job,
                "_approve_waiting_generation_plan": (
                    lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        QueueRecoveryRuntimeError("corrupt manifest")
                    )
                ),
                "fail_preparation": lambda target, **_updates: (
                    target.update({"status": "failed"}) or True
                ),
            },
        )
        with self.assertRaises(FakeHTTPException) as raised:
            asyncio.run(namespace["approve_generation_plan"](
                job["id"], FakeRequest(), object(),
            ))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(job["status"], "failed")

    def test_expired_manual_approval_never_claims_which_plan_won(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class FakeRequest:
            async def json(self):
                return {"workspace": "project-a"}

        job = {
            "id": "job-expired-cancelled",
            "status": "waiting_for_plan_approval",
            "plan_review_deadline": time.time() - 1,
        }

        for winner in ("cancelled", "queued"):
            with self.subTest(winner=winner):
                job["status"] = "waiting_for_plan_approval"
                job["plan_review_deadline"] = time.time() - 1

                def expire(_job_id):
                    job["status"] = winner
                    job["plan_review_deadline"] = None

                namespace = _isolated_functions(
                    self.launch,
                    ("approve_generation_plan",),
                    {
                        "api": types.SimpleNamespace(
                            post=lambda *_args, **_kwargs: lambda function: function,
                        ),
                        "Request": object,
                        "Response": object,
                        "HTTPException": FakeHTTPException,
                        "math": __import__("math"),
                        "time": time,
                        "_set_recovery_no_store": lambda _response: None,
                        "_require_owned_job_project": lambda *_args: job,
                        "_expire_plan_review": expire,
                    },
                )
                with self.assertRaises(FakeHTTPException) as raised:
                    asyncio.run(namespace["approve_generation_plan"](
                        job["id"], FakeRequest(), object(),
                    ))
                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(
                    raised.exception.detail,
                    "This job is no longer waiting for plan approval",
                )

    def test_custom_preparation_thread_failure_is_nonretryable(self):
        class Registry(dict):
            def prepare(self, job):
                return job

            def publish_prepared(self, job_id, job):
                self[job_id] = job

        class FailingThread:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                raise RuntimeError("injected start failure")

        registry = Registry()
        checkpoints = []
        namespace = _isolated_functions(
            self.launch,
            (
                "_queue_recovery_register_and_publish",
                "_stamp_h3_lightx2v_recovery_identity",
            ),
            {
                "_jobs": registry,
                "_credit_prepare_submission": lambda _job: False,
                "_stamp_job_origin": lambda job: job,
                "_session_secret": lambda: b"queue-recovery-test-secret-32b!",
                "owner_principal_digest": owner_principal_digest,
                "_workspace_lifecycle_lock": __import__("threading").RLock(),
                "_require_job_workspace_available": lambda _job: None,
                "_queue_recovery_project_identity": lambda *_args: (
                    "project:v1:" + "a" * 64
                ),
                "atomic_write_request_manifest": lambda *_args, **_kwargs: {
                    "path": ".maestro-recovery/job-prep.request.json",
                    "schema": 1, "sha256": "b" * 64, "size": 10,
                },
                "_queue_recovery_input_descriptors": lambda *_args: [],
                "_stamp_requested_generation_residency": lambda _job: None,
                "_queue_recovery_with_bounded_compaction": lambda operation: operation(),
                "_queue_recovery_coordinator": types.SimpleNamespace(
                    register_job=lambda *_args, **_kwargs: None,
                ),
                "durable_queue_state": lambda **_kwargs: {},
                "remove_request_manifest": lambda *_args: None,
                "_run_generation": lambda *_args: None,
                "_require_job_model_recipe_terms": lambda _job: None,
                "threading": types.SimpleNamespace(Thread=FailingThread),
                "_queue_recovery_worker": lambda _job: None,
                "_queue_recovery_checkpoint": lambda job, **updates: (
                    job.update(updates), checkpoints.append(dict(updates))
                ),
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            },
        )
        with self.assertRaises(QueueRecoveryRuntimeError):
            namespace["_queue_recovery_register_and_publish"](
                {
                    "id": "job-prep", "session_id": "owner", "workspace": "default",
                    "out_dir": "/project", "params": {},
                },
                worker=lambda *_args: None,
                recovery_kind="studio_repaint_preparation",
            )
        self.assertIn("job-prep", registry)
        self.assertEqual(checkpoints[-1]["recovery_state"], "blocked_preparation")
        self.assertTrue(checkpoints[-1]["queue_held"])
        self.assertFalse(checkpoints[-1]["reruns_denoise"])

    def test_all_studio_generation_routes_use_one_durable_helper(self):
        for name in (
            "generate", "retake_video_endpoint", "edit_anything_endpoint",
            "inpaint_endpoint", "generate_project_asset_references",
            "repaint_endpoint", "recast_endpoint", "outpaint_endpoint",
            "blend_endpoint",
        ):
            with self.subTest(name=name):
                node = _function(self.launch, name)
                calls = {
                    child.func.id
                    for child in ast.walk(node)
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                }
                self.assertIn("_queue_recovery_register_and_publish", calls)

    def test_recovery_reads_and_actions_are_no_store_and_blocked_eta_is_split(self):
        for name in (
            "get_status", "cancel_job", "list_jobs", "get_queue_state",
            "set_job_queue_priority", "hold_queued_job", "resume_held_job",
            "resume_recovered_job", "retry_recovered_job",
            "start_queued_job_next", "set_job_output_count", "get_job_log",
        ):
            with self.subTest(name=name):
                source = ast.get_source_segment(
                    self.launch_source, _function(self.launch, name),
                )
                self.assertIn("_set_recovery_no_store(response)", source)
        for name in ("get_status", "list_jobs", "get_queue_state"):
            source = ast.get_source_segment(
                self.launch_source, _function(self.launch, name),
            )
            self.assertIn("estimate_after_resume", source)
            self.assertIn("recovery_blocked", source)
            self.assertIn("_public_resource_metadata", source)
            self.assertIn("_public_parent_job_id", source)
        registration = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_queue_recovery_register_and_publish"),
        )
        self.assertIn("only recovery record", registration)
        self.assertIn("project-relative pointer", registration)

    def test_public_resource_projection_is_closed_and_content_free(self):
        namespace = _isolated_functions(
            self.launch,
            ("_public_resource_metadata",),
            {
                "resource_descriptor": lambda job: (
                    {
                        "intent": "generation",
                        "execution": "standard",
                        "preemptible": False,
                    }
                    if job.get("resource_intent") == "generation"
                    else None
                ),
            },
        )
        public = namespace["_public_resource_metadata"]
        self.assertEqual(public({"resource_intent": "generation"}), {
            "resource_descriptor": {
                "intent": "generation",
                "execution": "standard",
                "preemptible": False,
            },
        })
        self.assertEqual(
            public({"resource_intent": "private-device-key"}),
            {"resource_descriptor": None},
        )

    def test_public_parent_relation_is_bounded_and_opaque(self):
        namespace = _isolated_functions(
            self.launch,
            ("_public_parent_job_id",),
            {},
        )
        public = namespace["_public_parent_job_id"]
        self.assertEqual(public({"parent_job_id": "parent-123"}), "parent-123")
        for invalid in (
            None, True, "", ".", "..", "../parent", "parent/child",
            "parent\\child", "a" * 257, "parent\nchild",
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(public({"parent_job_id": invalid}))

    def test_job_registry_snapshots_survive_child_publish_remove_stress(self):
        status_source = ast.get_source_segment(
            self.launch_source, _function(self.launch, "get_status"),
        )
        jobs_source = ast.get_source_segment(
            self.launch_source, _function(self.launch, "list_jobs"),
        )
        queue_source = ast.get_source_segment(
            self.launch_source, _function(self.launch, "get_queue_state"),
        )
        wait_source = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_queue_wait_reason_for_job"),
        )
        busy_source = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_workspace_has_busy_jobs"),
        )
        cancel_source = ast.get_source_segment(
            self.launch_source, _function(self.launch, "cancel_job"),
        )
        self.assertIn("_jobs.get(job_id)", status_source)
        self.assertIn("list(_jobs.values())", jobs_source)
        self.assertIn("list(_jobs.values())", queue_source)
        self.assertIn("_jobs.values()", wait_source)
        self.assertIn("list(_jobs.items())", busy_source)
        self.assertIn("job = _jobs.get(job_id)", cancel_source)
        self.assertNotIn("job_id not in _jobs", cancel_source)

        class Context:
            def get(self):
                return None

        class_node = next(
            copy.deepcopy(item) for item in self.launch.body
            if isinstance(item, ast.ClassDef) and item.name == "_JobRegistry"
        )
        module = ast.Module(body=[class_node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {
            "threading": threading,
            "_request_remote": Context(),
            "_request_session_id": Context(),
            "_workspace_lifecycle_lock": threading.RLock(),
            "_require_job_workspace_available": lambda _job: None,
        }
        exec(compile(module, "isolated-job-registry", "exec"), namespace)
        registry = namespace["_JobRegistry"]()
        busy_namespace = _isolated_functions(
            self.launch,
            (
                "_path_targets_workspace", "_job_targets_workspace",
                "_workspace_has_busy_jobs",
            ),
            {
                "os": os,
                "_jobs": registry,
                "_active_gen_states": {},
            },
        )

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code

        def cancel_candidate(job, **_kwargs):
            job["status"] = "cancelled"
            return types.SimpleNamespace(
                abort_signalled=False, was_running=False,
            )

        cancel_namespace = _isolated_functions(
            self.launch,
            ("cancel_job",),
            {
                "api": types.SimpleNamespace(
                    post=lambda *_args, **_kwargs: lambda function: function,
                ),
                "Request": object,
                "Response": object,
                "HTTPException": FakeHTTPException,
                "_jobs": registry,
                "_set_recovery_no_store": lambda _response: None,
                "_job_owned_by_request": lambda *_args: True,
                "request_cancel": cancel_candidate,
                "_active_gen_states": {},
            },
        )
        failures = []
        start = threading.Event()

        def mutate():
            try:
                start.wait()
                for index in range(5_000):
                    key = f"child-{index % 5}"
                    registry[key] = {
                        "id": key,
                        "status": "queued",
                        "workspace": "project-a",
                        "out_dir": "/synthetic/project-a",
                        "params": {},
                    }
                    registry.pop(key, None)
                    if index % 25 == 0:
                        time.sleep(0)
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=mutate)
        worker.start()
        start.set()
        observations = 0
        try:
            while worker.is_alive():
                # Mirrors /status, /jobs, /queue, workspace-busy, and cancel.
                candidate = registry.get("child-0")
                if candidate is not None:
                    self.assertIsInstance(dict(candidate), dict)
                for job in registry.values():
                    self.assertIsInstance(job, dict)
                for job_id, job in registry.items():
                    self.assertIsInstance(job_id, str)
                    self.assertIsInstance(job, dict)
                self.assertIsInstance(
                    busy_namespace["_workspace_has_busy_jobs"](
                        "project-a", "/synthetic/project-a",
                    ),
                    bool,
                )
                try:
                    result = cancel_namespace["cancel_job"](
                        "child-0", object(), object(),
                    )
                    self.assertEqual(result["status"], "cancelled")
                except FakeHTTPException as error:
                    self.assertEqual(error.status_code, 404)
                observations += 1
        except Exception as error:
            failures.append(error)
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertGreater(observations, 0)
        self.assertEqual(failures, [])

    def test_actual_success_and_error_responses_are_no_store(self):
        namespace = _isolated_functions(
            self.launch,
            (
                "_recovery_response_requires_no_store",
                "_stamp_recovery_no_store_response",
                "_call_next_with_recovery_no_store",
            ),
            {"Request": object, "Response": object},
        )
        middleware_call = namespace["_call_next_with_recovery_no_store"]

        async def exercise(path, status_code):
            request = types.SimpleNamespace(
                url=types.SimpleNamespace(path=path),
            )

            async def call_next(_request):
                return types.SimpleNamespace(
                    status_code=status_code, headers={},
                )

            return await middleware_call(request, call_next)

        for status_code in (200, 404, 409, 503):
            with self.subTest(status_code=status_code):
                response = asyncio.run(exercise(
                    "/api/v1/queue/job-a/recovery-retry", status_code,
                ))
                self.assertEqual(
                    response.headers["Cache-Control"], "private, no-store",
                )
                self.assertEqual(response.headers["Pragma"], "no-cache")
        unrelated = asyncio.run(exercise("/api/v1/outputs", 404))
        self.assertEqual(unrelated.headers, {})

    def test_startup_recovery_is_registered_before_other_worker_startup(self):
        recovery = self.launch_source.index(
            'def _start_queue_recovery_before_background_workers'
        )
        updater = self.launch_source.index("def _start_versioned_model_updates")
        self.assertLess(recovery, updater)
        restore = _function(self.launch, "_restore_queue_recovery_on_startup")
        source = ast.get_source_segment(self.launch_source, restore)
        scheduler = source.index("restore_scheduler_state(")
        worker = source.index("threading.Thread(", scheduler)
        self.assertLess(scheduler, worker)
        cleanup = source.index("cleanup_orphan_request_manifests(")
        timer = source.index("_schedule_plan_review_auto_approval(")
        self.assertLess(cleanup, timer)
        self.assertIn('"waiting_for_plan_approval"', source)

    def test_expired_plan_timer_accepts_exact_frozen_plan_without_browser(self):
        jobs = {
            "job-timer": {
                "id": "job-timer",
                "status": "waiting_for_plan_approval",
                "plan_review_deadline": time.time() + 0.03,
            },
        }
        accepted = __import__("threading").Event()

        def approve(job, **kwargs):
            self.assertIsNone(kwargs["request"])
            self.assertEqual(kwargs["segment_overrides"], [])
            self.assertEqual(kwargs["boundary_overrides"], [])
            job["status"] = "queued"
            accepted.set()

        namespace = _isolated_functions(
            self.launch,
            (
                "_cancel_plan_review_timer",
                "_expire_plan_review",
                "_schedule_plan_review_auto_approval",
            ),
            {
                "threading": __import__("threading"),
                "math": __import__("math"),
                "time": time,
                "_jobs": jobs,
                "_plan_review_timer_lock": __import__("threading").Lock(),
                "_plan_review_timers": {},
                "_approve_waiting_generation_plan": approve,
                "fail_preparation": lambda *_args, **_kwargs: self.fail(
                    "valid timer unexpectedly failed preparation"
                ),
            },
        )
        namespace["_schedule_plan_review_auto_approval"](jobs["job-timer"])
        self.assertTrue(accepted.wait(1.0))
        self.assertEqual(jobs["job-timer"]["status"], "queued")

    def test_ref2va_acceptance_arms_only_owned_project_waiters_once(self):
        class FakeHTTPException(Exception):
            pass

        jobs = {
            "owned": {
                "id": "owned", "workspace": "project-a",
                "status": "waiting_for_plan_approval",
                "plan_review_required": True,
                "plan_review_terms_required": True,
                "plan_review_deadline": None,
            },
            "foreign": {
                "id": "foreign", "workspace": "project-a",
                "status": "waiting_for_plan_approval",
                "plan_review_required": True,
                "plan_review_terms_required": True,
                "plan_review_deadline": None,
            },
            "other-project": {
                "id": "other-project", "workspace": "project-b",
                "status": "waiting_for_plan_approval",
                "plan_review_required": True,
                "plan_review_terms_required": True,
                "plan_review_deadline": None,
            },
            "already-armed": {
                "id": "already-armed", "workspace": "project-a",
                "status": "waiting_for_plan_approval",
                "plan_review_required": True,
                "plan_review_terms_required": False,
                "plan_review_deadline": 90.0,
            },
        }
        armed = []

        def require(job_id, _request, workspace):
            self.assertEqual(workspace, "project-a")
            if job_id == "foreign":
                raise FakeHTTPException()
            return jobs[job_id]

        def arm(job, *, deadline):
            armed.append((job["id"], deadline))
            job["plan_review_terms_required"] = False
            job["plan_review_deadline"] = deadline
            return True

        namespace = _isolated_functions(
            self.launch,
            ("_reconcile_ref2va_waiting_plan_reviews",),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "threading": __import__("threading"),
                "time": types.SimpleNamespace(time=lambda: 100.0),
                "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
                "_plan_terms_reconciliation_lock": (
                    __import__("threading").RLock()
                ),
                "_jobs": jobs,
                "_ref2va_host_terms_accepted": lambda: True,
                "_require_owned_job_project": require,
                "_arm_ref2va_waiting_plan_review": arm,
            },
        )
        reconcile = namespace["_reconcile_ref2va_waiting_plan_reviews"]
        reconcile(request=object(), workspace="project-a")
        reconcile(request=object(), workspace="project-a")
        self.assertEqual(armed, [("owned", 116.0)])
        self.assertEqual(jobs["already-armed"]["plan_review_deadline"], 90.0)

    def test_plan_timer_install_loses_benignly_to_cancel(self):
        job = {
            "id": "job-race",
            "status": "waiting_for_plan_approval",
            "plan_review_required": True,
            "plan_review_terms_required": True,
            "plan_review_deadline": None,
        }

        def arm(target, *, plan_review_deadline, **_updates):
            target["plan_review_terms_required"] = False
            target["plan_review_deadline"] = plan_review_deadline
            return True

        def schedule(target):
            target["status"] = "cancelled"
            target["plan_review_deadline"] = None
            raise ValueError("deadline cleared by cancellation")

        namespace = _isolated_functions(
            self.launch,
            ("_arm_ref2va_waiting_plan_review",),
            {
                "_waiting_plan_project_is_current": lambda _job: True,
                "arm_prepared_job_plan_review": arm,
                "_schedule_plan_review_auto_approval": schedule,
                "fail_preparation": lambda *_args, **_kwargs: self.fail(
                    "a cancellation winner must not be failed"
                ),
            },
        )
        self.assertTrue(namespace["_arm_ref2va_waiting_plan_review"](
            job, deadline=116.0,
        ))
        self.assertEqual(job["status"], "cancelled")

    def test_ref2va_reconciliation_continues_after_one_arm_failure(self):
        class FakeHTTPException(Exception):
            pass

        jobs = {
            job_id: {
                "id": job_id,
                "workspace": "project-a",
                "status": "waiting_for_plan_approval",
                "plan_review_required": True,
                "plan_review_terms_required": True,
                "plan_review_deadline": None,
            }
            for job_id in ("broken", "healthy")
        }
        armed = []

        def arm(job, *, deadline):
            if job["id"] == "broken":
                raise OSError("synthetic journal failure")
            armed.append((job["id"], deadline))
            return True

        namespace = _isolated_functions(
            self.launch,
            ("_reconcile_ref2va_waiting_plan_reviews",),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "time": types.SimpleNamespace(time=lambda: 100.0),
                "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
                "_plan_terms_reconciliation_lock": (
                    __import__("threading").RLock()
                ),
                "_jobs": jobs,
                "_ref2va_host_terms_accepted": lambda: True,
                "_require_owned_job_project": (
                    lambda job_id, _request, _workspace: jobs[job_id]
                ),
                "_arm_ref2va_waiting_plan_review": arm,
            },
        )
        with self.assertRaisesRegex(OSError, "synthetic journal failure"):
            namespace["_reconcile_ref2va_waiting_plan_reviews"](
                request=object(), workspace="project-a",
            )
        self.assertEqual(armed, [("healthy", 116.0)])

    def test_terms_acceptance_and_preparation_share_missed_job_handshake(self):
        preparation = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_run_generation_preparation"),
        )
        reconcile = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_reconcile_ref2va_waiting_plan_reviews"),
        )
        self.assertIn("_plan_terms_reconciliation_lock.acquire()", preparation)
        complete = preparation.index("completed = complete_preparation(")
        postcheck = preparation.index(
            "if terms_blocked and _ref2va_host_terms_accepted()", complete,
        )
        self.assertGreater(postcheck, complete)
        self.assertIn("with _plan_terms_reconciliation_lock:", reconcile)

    def test_client_ref2va_flag_cannot_bypass_host_terms(self):
        class FakeHTTPException(Exception):
            def __init__(self, **kwargs):
                self.status_code = kwargs.get("status_code")

        namespace = _isolated_functions(
            self.launch,
            ("_require_h3_generation_terms",),
            {
                "HTTPException": FakeHTTPException,
                "_h3_generation_requirements": lambda _body, _plan: {
                    "ref2va_terms_required": True,
                },
                "_ref2va_host_terms_accepted": lambda: False,
            },
        )
        with self.assertRaises(FakeHTTPException):
            namespace["_require_h3_generation_terms"](
                {"h3_ref2va_terms_accepted": True}, None,
            )

    def test_calibrated_replan_preserves_prefix_and_exact_publication(self):
        from services.h3_shot_planner import plan_h3_native_shots

        class RecoveryError(RuntimeError):
            pass

        class FakeWgp:
            @staticmethod
            def get_model_def(_model):
                return {
                    "frames_minimum": 107,
                    "frames_maximum": 345,
                    "frames_steps": 17,
                    "fps": 24,
                }

            @staticmethod
            def get_model_min_frames_and_step(_model):
                return 107, 17, 17

            @staticmethod
            def align_model_frame_count(value, _model_def):
                return max(107, ((int(value) - 5) // 17) * 17 + 5)

            @staticmethod
            def get_output_type_for_model(_model, _image_mode):
                return "video"

            @staticmethod
            def compute_profile(override, _output):
                return 4 if override == -1 else override

        namespace = _isolated_functions(
            self.launch,
            (
                "_h3_effective_offload_profile",
                "_h3_peak_recovery_identity",
                "_replan_h3_final_segment_for_peak",
            ),
            {
                "copy": __import__("copy"),
                "wgp": FakeWgp,
                "QueueRecoveryRuntimeError": RecoveryError,
                "_H3_REF2VA_MODEL": "minimax_h3_ref2va",
                "_H3_PEAK_RECOVERY_POLICY_VERSION": 1,
                "_MULTI_CLIP_SEPARATOR": "\n---MAESTRO-CLIP---\n",
                "_stamp_h3_duration_contract": lambda _body, _plan: None,
            },
        )
        prefix_frames = [107, 124, 141, 175]
        prefix_published = list(prefix_frames)
        semantic_source = (
            "<Subject 1> holds position while the hangar doors close."
        )
        original_shot_plan = plan_h3_native_shots(
            global_prompt=semantic_source,
            clip_frame_counts=prefix_frames + [294],
            clip_requested_frames=prefix_published + [292],
            fps=24,
            clip_boundaries=[
                {"type": "continuous", "source": "model_grid"}
                for _ in range(4)
            ],
            source_prompts=[semantic_source],
            source_indices=[0] * 5,
            structured_shots=[{
                "shot_id": "authored-stable",
                "environment": "orbital hangar",
                "subjects_on_screen": [{
                    "speaker_name": "Pilot",
                    "visual_description": "silver flight suit",
                }],
                "dialogue_beats": [{
                    "spoken_text": "Hold position.",
                    "language": "English",
                    "speaker_id": "pilot-1",
                }],
            }],
        )
        sealed_prompts = list(original_shot_plan["clip_prompts"])
        params = {
            "model_type": "minimax_h3_ref2va",
            "resolution": "1344x768",
            "num_inference_steps": 20,
            "override_profile": 4,
            "custom_settings": {
                "h3_attention_engine": "sol_attn",
                "h3_sol_dense_steps": 10,
                "h3_sol_dense_blocks": 2,
            },
            "per_clip_prompts": sealed_prompts,
            "_h3_longform": {
                "fps": 24,
                "global_prompt": semantic_source,
                "clip_count": 5,
                "clip_frames": prefix_frames + [294],
                "clip_published_frames": prefix_published + [292],
                "clip_trim_tail_frames": [0, 0, 0, 0, 2],
                "clip_boundaries": [
                    {"type": "continuous", "source": "model_grid"}
                    for _ in range(4)
                ],
                "segment_models": [
                    {"model_type": "minimax_h3_ref2va", "reason": "test"}
                    for _ in range(5)
                ],
                "published_frames": sum(prefix_published) + 292,
                "shot_plan": original_shot_plan,
            },
        }
        replanned = namespace["_replan_h3_final_segment_for_peak"](
            params,
            completed_prefix=4,
            choice={
                "frame_ceiling": 192,
                "offload_profile": 4,
                "allocation_revision": 1,
                "allocation_snapshot": "a" * 64,
            },
        )
        plan = replanned["_h3_longform"]
        self.assertEqual(plan["clip_frames"][:4], prefix_frames)
        self.assertEqual(plan["clip_published_frames"][:4], prefix_published)
        self.assertEqual(sum(plan["clip_published_frames"]),
                         sum(prefix_published) + 292)
        self.assertGreater(len(plan["clip_frames"]), 5)
        self.assertTrue(all(value <= 192 for value in plan["clip_frames"][4:]))
        self.assertEqual(plan["peak_recovery_identities"][:4], [None] * 4)
        self.assertEqual(
            [shot["authored_shot_id"] for shot in plan["shot_plan"]["shots"][:4]],
            ["authored-stable"] * 4,
        )
        self.assertEqual(
            plan["shot_plan"]["shots"][4]["semantic_shot_index"], 0,
        )
        self.assertEqual(
            plan["shot_plan"]["dialogue_manifest"],
            original_shot_plan["dialogue_manifest"],
        )
        self.assertEqual(
            plan["shot_plan"]["semantic_shots"][0]["reference_labels"],
            original_shot_plan["semantic_shots"][0]["reference_labels"],
        )
        self.assertEqual(
            plan["shot_plan"]["semantic_shots"][0]["visual_context"],
            original_shot_plan["semantic_shots"][0]["visual_context"],
        )
        for compiler_field in (
            "authored_prompt", "visual_context", "opening_blocking",
            "final_blocking", "structured_dialogue_blocks",
        ):
            self.assertEqual(
                plan["shot_plan"]["source_contracts"][0][compiler_field],
                original_shot_plan["source_contracts"][0][compiler_field],
            )
        self.assertEqual(
            replanned["per_clip_prompts"][:4], sealed_prompts[:4],
        )
        for index in range(4):
            for field in (
                "physical_segment_id", "execution_cursor_frame",
                "execution_slice", "frames", "published_frames",
                "published_start_frame", "published_end_frame_exclusive",
                "prompt", "dialogue_manifest_indices",
            ):
                self.assertEqual(
                    plan["shot_plan"]["shots"][index][field],
                    original_shot_plan["shots"][index][field],
                )
        self.assertEqual(
            plan["shot_plan"]["global_prompt"], semantic_source,
        )
        self.assertEqual(
            plan["shot_plan"]["semantic_physical_contract_version"], 2,
        )
        from services.h3_shot_planner import (
            seal_h3_shot_plan,
            validate_h3_shot_plan_seal,
        )
        validate_h3_shot_plan_seal(plan["shot_plan"])

        canonical_duration = (
            sum(prefix_published) + 292
        ) / 24
        canonical_prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            f"[Shot 1] [0s-{canonical_duration:.10f}s] "
            "shot_name: Archive | "
            "audiovisual_description: <Subject 1> studies a ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        opening_action = (
            "the cabinet stays locked. The warning lamp flickers twice!"
        )
        final_action = "the archivist closes the ledger"
        canonical_plan = plan_h3_native_shots(
            global_prompt=canonical_prompt,
            clip_frame_counts=prefix_frames + [294],
            clip_requested_frames=prefix_published + [292],
            fps=24,
            clip_boundaries=[
                {"type": "continuous", "source": "model_grid"}
                for _ in range(4)
            ],
            source_prompts=[canonical_prompt],
            source_indices=[0] * 5,
            structured_shots=[{
                "shot_id": "canonical-archive",
                "spatial_setup": opening_action,
                "closing_blocking": final_action,
            }],
        )
        canonical_plan["director_runtime_contract"] = {
            "clip_count": 5,
            "segment_policy": {"clip_requested_frames": [
                *prefix_published, 292,
            ]},
            "segment_models": copy.deepcopy(
                params["_h3_longform"]["segment_models"]
            ),
            "segment_source_indices": [0] * 5,
        }
        seal_h3_shot_plan(canonical_plan)
        canonical_params = {
            **params,
            "per_clip_prompts": list(canonical_plan["clip_prompts"]),
            "_h3_longform": {
                **params["_h3_longform"],
                "global_prompt": canonical_prompt,
                "segment_policy": copy.deepcopy(
                    canonical_plan["director_runtime_contract"][
                        "segment_policy"
                    ]
                ),
                "segment_source_indices": [0] * 5,
                "shot_plan": canonical_plan,
            },
        }
        canonical_replanned = namespace[
            "_replan_h3_final_segment_for_peak"
        ](
            canonical_params,
            completed_prefix=4,
            choice={
                "frame_ceiling": 192,
                "offload_profile": 4,
                "allocation_revision": 1,
                "allocation_snapshot": "e" * 64,
            },
        )
        canonical_replanned_plan = canonical_replanned[
            "_h3_longform"
        ]["shot_plan"]
        self.assertEqual(
            canonical_replanned_plan["source_contracts"][0][
                "opening_blocking"
            ],
            opening_action,
        )
        self.assertEqual(
            canonical_replanned_plan["source_contracts"][0][
                "authored_prompt"
            ],
            canonical_plan["source_contracts"][0]["authored_prompt"],
        )
        self.assertEqual(
            sum(
                opening_action in prompt
                for prompt in canonical_replanned["per_clip_prompts"]
            ),
            1,
        )
        self.assertEqual(
            sum(
                "The warning lamp flickers twice!" in prompt
                for prompt in canonical_replanned["per_clip_prompts"]
            ),
            1,
        )
        self.assertIn(
            opening_action, canonical_replanned["per_clip_prompts"][0],
        )
        closing_events = [
            event for event in canonical_replanned_plan["event_ownership"]
            if event["kind"] == "final_blocking"
        ]
        self.assertEqual(len(closing_events), 1)
        self.assertEqual(closing_events[0]["executable_payload"], final_action)
        self.assertEqual(
            closing_events[0]["owner_segment_index"],
            len(canonical_replanned_plan["shots"]) - 1,
        )
        original_range_event = next(
            event for event in canonical_plan["event_ownership"]
            if event["kind"] == "range"
        )
        rebuilt_range_event = next(
            event for event in canonical_replanned_plan["event_ownership"]
            if event["kind"] == "range"
        )
        self.assertEqual(
            [
                item for item in rebuilt_range_event["continuation_slices"]
                if item["segment_index"] < 4
            ],
            [
                item for item in original_range_event["continuation_slices"]
                if item["segment_index"] < 4
            ],
        )
        self.assertEqual(
            [
                item["segment_index"]
                for item in rebuilt_range_event["continuation_slices"]
                if item["segment_index"] >= 4
            ],
            [4, 5],
        )
        self.assertEqual(
            sum(
                final_action in prompt
                for prompt in canonical_replanned["per_clip_prompts"]
            ),
            1,
        )
        self.assertEqual(
            canonical_replanned["per_clip_prompts"][:4],
            canonical_plan["clip_prompts"][:4],
        )
        validate_h3_shot_plan_seal(canonical_replanned_plan)
        self.assertEqual(
            canonical_replanned["_h3_longform"]["segment_source_indices"],
            [0] * len(canonical_replanned["per_clip_prompts"]),
        )
        self.assertEqual(
            canonical_replanned_plan["director_runtime_contract"][
                "segment_source_indices"
            ],
            canonical_replanned["_h3_longform"][
                "segment_source_indices"
            ],
        )
        self.assertEqual(
            canonical_replanned_plan["director_runtime_contract"][
                "clip_count"
            ],
            len(canonical_replanned["per_clip_prompts"]),
        )
        self.assertEqual(
            canonical_replanned["_h3_longform"]["segment_policy"],
            canonical_replanned_plan["segment_policy"],
        )
        self.assertEqual(
            canonical_replanned_plan["director_runtime_contract"][
                "segment_policy"
            ],
            canonical_replanned_plan["segment_policy"],
        )

        tampered_params = copy.deepcopy(canonical_params)
        tampered_plan = tampered_params["_h3_longform"]["shot_plan"]
        for contracts_field in ("source_contracts", "semantic_shots"):
            tampered_plan[contracts_field][0]["visual_context"] = (
                "VISUAL CONTINUITY (world, cast, and setting only): "
                "setting: altered archive."
            )
        seal_h3_shot_plan(tampered_plan)
        with self.assertRaisesRegex(
            RecoveryError, "changed a sealed semantic prompt",
        ):
            namespace["_replan_h3_final_segment_for_peak"](
                tampered_params,
                completed_prefix=4,
                choice={
                    "frame_ceiling": 192,
                    "offload_profile": 4,
                    "allocation_revision": 1,
                    "allocation_snapshot": "f" * 64,
                },
            )

        committed_v1 = _committed_v1_h3_plan(original_shot_plan)
        v1_params = {
            **params,
            "per_clip_prompts": list(committed_v1["clip_prompts"]),
            "_h3_longform": {
                **params["_h3_longform"],
                "shot_plan": committed_v1,
            },
        }
        v1_replanned = namespace[
            "_replan_h3_final_segment_for_peak"
        ](
            v1_params,
            completed_prefix=4,
            choice={
                "frame_ceiling": 192,
                "offload_profile": 4,
                "allocation_revision": 1,
                "allocation_snapshot": "d" * 64,
            },
        )
        self.assertEqual(
            v1_replanned["_h3_longform"]["shot_plan"][
                "semantic_physical_contract_version"
            ],
            1,
        )
        self.assertEqual(
            v1_replanned["per_clip_prompts"][:4],
            committed_v1["clip_prompts"][:4],
        )
        self.assertNotIn(
            "prompt_contract_seal",
            v1_replanned["_h3_longform"]["shot_plan"],
        )

        # Pre-v1 plans have no semantic contract to replay. Their sealed
        # per-clip prompts are nevertheless immutable: prefix children remain
        # one-to-one sources and only the final source fans out physically.
        legacy_prompts = [
            f"[Legacy child {index + 1}] fixed sealed worker prompt."
            for index in range(5)
        ]
        legacy_shot_plan = plan_h3_native_shots(
            global_prompt="Legacy root must not replace sealed children.",
            clip_frame_counts=prefix_frames + [294],
            clip_requested_frames=prefix_published + [292],
            fps=24,
            clip_boundaries=[
                {"type": "continuous", "source": "model_grid"}
                for _ in range(4)
            ],
            source_prompts=legacy_prompts,
            source_indices=list(range(5)),
        )
        legacy_shot_plan.pop("semantic_physical_contract_version", None)
        legacy_shot_plan.pop("semantic_shots", None)
        legacy_shot_plan["source_contracts"] = (
            legacy_shot_plan["source_contracts"][:1]
        )
        legacy_shot_plan["dialogue_manifest"] = []
        for legacy_shot in legacy_shot_plan["shots"]:
            legacy_shot["source_index"] = 0
            legacy_shot.pop("authored_shot_id", None)
            legacy_shot.pop("semantic_shot_index", None)
        legacy_params = {
            **params,
            "per_clip_prompts": list(legacy_prompts),
            "_h3_longform": {
                **params["_h3_longform"],
                "global_prompt": (
                    "Legacy root must not replace sealed children."
                ),
                "shot_plan": legacy_shot_plan,
            },
        }
        legacy_replanned = namespace[
            "_replan_h3_final_segment_for_peak"
        ](
            legacy_params,
            completed_prefix=4,
            choice={
                "frame_ceiling": 277,
                "offload_profile": 5,
                "allocation_revision": 1,
                "allocation_snapshot": "b" * 64,
            },
        )
        legacy_plan = legacy_replanned["_h3_longform"]
        rebuilt_prompts = legacy_replanned["per_clip_prompts"]
        self.assertEqual(
            [value.encode("utf-8") for value in rebuilt_prompts[:4]],
            [value.encode("utf-8") for value in legacy_prompts[:4]],
        )
        self.assertGreater(len(rebuilt_prompts), 5)
        self.assertEqual(
            [value.encode("utf-8") for value in rebuilt_prompts[4:]],
            [legacy_prompts[-1].encode("utf-8")]
            * (len(rebuilt_prompts) - 4),
        )
        self.assertEqual(legacy_plan["clip_frames"][:4], prefix_frames)
        self.assertEqual(
            legacy_plan["clip_published_frames"][:4], prefix_published,
        )
        self.assertEqual(
            sum(legacy_plan["clip_published_frames"]),
            sum(prefix_published) + 292,
        )
        self.assertEqual(
            [shot["source_index"] for shot in
             legacy_plan["shot_plan"]["shots"][:4]],
            list(range(4)),
        )
        self.assertTrue(all(
            shot["source_index"] == 4
            for shot in legacy_plan["shot_plan"]["shots"][4:]
        ))
        self.assertEqual(
            legacy_plan["shot_plan"]["semantic_physical_contract_version"],
            1,
        )
        self.assertEqual(
            len(legacy_plan["shot_plan"]["semantic_shots"]), 5,
        )
        self.assertEqual(
            legacy_plan["shot_plan"]["semantic_shots"][-1][
                "segment_indices"
            ],
            [4, 5],
        )
        self.assertEqual(
            legacy_plan["peak_recovery_identities"][:4], [None] * 4,
        )
        first_replacement_identity = copy.deepcopy(
            legacy_plan["peak_recovery_identities"][4]
        )
        second_replanned = namespace[
            "_replan_h3_final_segment_for_peak"
        ](
            legacy_replanned,
            completed_prefix=len(legacy_plan["clip_frames"]) - 1,
            choice={
                "frame_ceiling": 107,
                "offload_profile": 5,
                "allocation_revision": 2,
                "allocation_snapshot": "c" * 64,
            },
        )
        second_plan = second_replanned["_h3_longform"]
        self.assertEqual(
            second_plan["peak_recovery_identities"][:4], [None] * 4,
        )
        self.assertEqual(
            second_plan["peak_recovery_identities"][4],
            first_replacement_identity,
        )
        self.assertEqual(
            [value.encode("utf-8") for value in
             second_replanned["per_clip_prompts"][:5]],
            [value.encode("utf-8") for value in rebuilt_prompts[:5]],
        )
        self.assertEqual(
            sum(second_plan["clip_published_frames"]),
            sum(prefix_published) + 292,
        )
        malformed_history = copy.deepcopy(legacy_replanned)
        malformed_history["_h3_longform"][
            "peak_recovery_identities"
        ] = [None]
        with self.assertRaises(RecoveryError):
            namespace["_replan_h3_final_segment_for_peak"](
                malformed_history,
                completed_prefix=len(legacy_plan["clip_frames"]) - 1,
                choice={
                    "frame_ceiling": 107,
                    "offload_profile": 5,
                    "allocation_revision": 2,
                    "allocation_snapshot": "c" * 64,
                },
            )

    def test_semantic_execution_slices_dispatch_exact_children(self):
        from services.h3_shot_planner import (
            plan_h3_native_shots,
            seal_h3_shot_plan,
        )

        class RecoveryError(RuntimeError):
            pass

        prompt = "[Shot 1] The pilot crosses the hangar without a cut."
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[158, 345],
            clip_requested_frames=[144, 336],
            fps=24,
            clip_boundaries=[{
                "type": "continuous", "source": "model_grid",
            }],
        )
        namespace = _isolated_functions(
            self.launch,
            ("_h3_execution_shots_for_dispatch",),
            {
                "copy": __import__("copy"),
                "hashlib": hashlib,
                "QueueRecoveryRuntimeError": RecoveryError,
            },
        )
        dispatch = namespace["_h3_execution_shots_for_dispatch"]
        local_prompts = list(plan["clip_prompts"])
        shots = dispatch(
            {"shot_plan": plan}, local_prompts, 2,
        )
        self.assertEqual(shots[0]["execution_cursor_frame"], 0)
        self.assertEqual(shots[1]["execution_cursor_frame"], 144)
        self.assertNotEqual(
            shots[0]["prompt"].encode(), shots[1]["prompt"].encode(),
        )
        self.assertEqual(
            shots[1]["execution_slice"]["end_frame_exclusive"]
            - shots[1]["execution_slice"]["start_frame"],
            336,
        )
        broken = __import__("copy").deepcopy(plan)
        broken["shots"].pop()
        with self.assertRaises(RecoveryError):
            dispatch({"shot_plan": broken}, local_prompts, 2)

        corrupt_owner = copy.deepcopy(plan)
        corrupt_event = corrupt_owner["source_contracts"][0][
            "event_ownership"
        ][0]
        corrupt_event.update({
            "owner_segment_index": 1,
            "owner_physical_segment_index": 1,
            "owner_physical_segment_id": "h3-authored-shot-1:segment-2",
        })
        corrupt_owner["semantic_shots"] = copy.deepcopy(
            corrupt_owner["source_contracts"]
        )
        corrupt_owner["event_ownership"] = [
            copy.deepcopy(item)
            for contract in corrupt_owner["source_contracts"]
            for item in contract["event_ownership"]
        ]
        seal_h3_shot_plan(corrupt_owner)
        with self.assertRaisesRegex(RecoveryError, "event ownership"):
            dispatch({"shot_plan": corrupt_owner}, local_prompts, 2)

        v1_plan = _committed_v1_h3_plan(plan)
        v1_prompts = list(v1_plan["clip_prompts"])
        v1_shots = dispatch({"shot_plan": v1_plan}, v1_prompts, 2)
        self.assertEqual(len(v1_shots), 2)
        self.assertEqual(v1_shots[1]["execution_cursor_frame"], 144)

        unversioned = copy.deepcopy(v1_plan)
        unversioned.pop("semantic_physical_contract_version", None)
        self.assertIsNone(
            dispatch({"shot_plan": unversioned}, v1_prompts, 2)
        )
        future = copy.deepcopy(plan)
        future["semantic_physical_contract_version"] = 99
        with self.assertRaisesRegex(RecoveryError, "unsupported"):
            dispatch({"shot_plan": future}, local_prompts, 2)

    def test_h3_recovery_settings_persist_actual_contract_version(self):
        class RecoveryError(RuntimeError):
            pass

        namespace = _isolated_functions(
            self.launch,
            ("_h3_segment_recovery_settings",),
            {"copy": copy, "QueueRecoveryRuntimeError": RecoveryError},
        )
        settings = namespace["_h3_segment_recovery_settings"]({
            "generated_frames": 158,
            "published_frames": 144,
            "trim_tail_frames": 14,
            "native_boundary_conditioning": False,
            "semantic_physical_contract_version": 2,
            "semantic_shot_index": 0,
            "physical_segment_index": 0,
            "physical_segment_count": 2,
            "predecessor_segment_index": None,
            "execution_cursor_frame": 0,
            "execution_slice": {
                "start_frame": 0,
                "end_frame_exclusive": 144,
            },
        })
        self.assertEqual(settings["semantic_execution"]["version"], 2)
        with self.assertRaisesRegex(RecoveryError, "unsupported"):
            namespace["_h3_segment_recovery_settings"]({
                "generated_frames": 158,
                "published_frames": 144,
                "trim_tail_frames": 14,
                "native_boundary_conditioning": False,
                "semantic_physical_contract_version": 3,
            })

    def test_recovered_v2_h3_worker_preserves_replan_contract_to_parser(self):
        """Exercise restored task materialization through the pre-model boundary."""
        from services.h3_shot_planner import plan_h3_native_shots

        class ParserBoundary(BaseException):
            pass

        class FakeHTTPException(Exception):
            def __init__(self, detail=""):
                super().__init__(detail)
                self.detail = detail

        clip_frames = [107, 124, 141, 158, 158, 141]
        published_frames = [107, 124, 141, 158, 158, 134]
        tail_trims = [0, 0, 0, 0, 0, 7]
        prompt = "SYNTHETIC_RECOVERY_PROMPT"
        shot_plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=clip_frames,
            clip_requested_frames=published_frames,
            fps=24,
            clip_boundaries=[{
                "type": "cut", "source": "synthetic",
            }] * 5,
        )
        peak_identity = {
            "offload_profile": 5,
            "allocation_revision": 7,
            "allocation_snapshot": "a" * 64,
        }
        longform = {
            "clip_count": 6,
            "clip_frames": clip_frames,
            "clip_published_frames": published_frames,
            "clip_trim_tail_frames": tail_trims,
            "requested_frames": sum(published_frames),
            "planned_frames": sum(clip_frames),
            "clip_boundaries": [{
                "type": "cut", "source": "synthetic",
            }] * 5,
            "segment_models": [{
                "model_type": "minimax_h3_ref2va",
                "reason": "synthetic recovery",
            }] * 6,
            "native_boundary_conditioning": False,
            "preserve_generated_audio": True,
            "peak_recovery_identities": [
                None, None, None, None,
                copy.deepcopy(peak_identity),
                copy.deepcopy(peak_identity),
            ],
            "shot_plan": shot_plan,
        }
        params = {
            "model_type": "minimax_h3",
            "resolution": "1344x768",
            "multi_prompts_gen_type": 3,
            "prompt": "\n---MAESTRO-CLIP---\n".join(
                shot_plan["clip_prompts"]
            ),
            "per_clip_prompts": list(shot_plan["clip_prompts"]),
            "per_clip_frames": clip_frames,
            "num_inference_steps": 20,
            "override_profile": -1,
            "repeat_generation": 1,
            "custom_settings": {"h3_attention_engine": "sol_attn"},
            "_h3_longform": longform,
        }
        plan = seal_h3_offload_plan(params, effective_profile=5)
        restarted_plan = json.loads(json.dumps(plan))
        restarted_params = json.loads(json.dumps(params))
        self.assertEqual(
            assert_h3_offload_plan_parity(
                plan, restarted_plan, params=params,
            ),
            plan,
        )
        captured = []

        class FakeWGP:
            task_id = 0
            video_profile = 2
            server_config = {"services": {}}
            save_path = ""
            image_save_path = ""
            audio_save_path = ""

            @staticmethod
            def get_model_def(_model_type):
                return {
                    "frames_minimum": 5,
                    "frames_steps": 17,
                    "latent_size": 17,
                }

            @staticmethod
            def get_model_min_frames_and_step(_model_type):
                return 5, 17, 17

            @staticmethod
            def align_model_frame_count(frames, _model_def):
                return int(frames)

            @staticmethod
            def _parse_task_manifest(manifest, _state, _cwd):
                captured.extend(copy.deepcopy(manifest))
                raise ParserBoundary()

        recovery_root = tempfile.TemporaryDirectory()
        self.addCleanup(recovery_root.cleanup)
        job = {
            "id": "synthetic-restored-h3",
            "status": "queued",
            "params": restarted_params,
            "h3_offload_plan": restarted_plan,
            "out_dir": recovery_root.name,
            "recovery_cursor": {
                "units": [{
                    "kind": "h3_segment", "index": index, "variant": 0,
                } for index in range(4)],
            },
        }
        namespace = _isolated_functions(
            self.launch,
            (
                "_h3_execution_shots_for_dispatch",
                "_apply_h3_offload_plan_to_manifest",
                "_require_h3_offload_plan_parity",
                "_snapshot_h3_recovery_task_params",
                "_expand_h3_longform_outputs",
                "_run_generation",
            ),
            {
                "copy": copy,
                "hashlib": hashlib,
                "json": json,
                "os": os,
                "time": time,
                "traceback": __import__("traceback"),
                "torch": types.SimpleNamespace(
                    cuda=types.SimpleNamespace(is_available=lambda: False),
                ),
                "wgp": FakeWGP,
                "_jobs": {job["id"]: job},
                "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                "_sample_campaign_execution_attempt": contextvars.ContextVar(
                    "test_sample_attempt", default=None,
                ),
                "_sample_campaign_transition_lock": threading.RLock(),
                "_lifecycle_update_job": lambda *_args, **_kwargs: True,
                "_lifecycle_register_abort_state": (
                    lambda *_args, **_kwargs: True
                ),
                "_lifecycle_record_job_outputs": (
                    lambda _job, outputs, **_kwargs: list(outputs)
                ),
                "_lifecycle_finish_job": lambda *_args, **_kwargs: False,
                "settle_sample_preemption": lambda *_args, **_kwargs: False,
                "_credit_prepare_admission": lambda _job: False,
                "_credit_prepare_dispatch": lambda _job: False,
                "_credit_block_runtime_error": lambda _job: None,
                "CreditRuntimeError": ValueError,
                "EntitlementError": ValueError,
                "_CREDIT_INTERNAL_PARAMS": frozenset(),
                "_active_gen_states": {},
                "_gen_lock": object(),
                "_H3_LONG_STUDIO_MODELS": {"minimax_h3"},
                "_H3_FL2VA_MODELS": {"minimax_h3"},
                "_MULTI_CLIP_SEPARATOR": "\n---CLIP---\n",
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "H3_OFFLOAD_PLAN_PARAM_KEY": H3_OFFLOAD_PLAN_PARAM_KEY,
                "assert_h3_offload_plan_parity": assert_h3_offload_plan_parity,
                "validate_h3_offload_plan": validate_h3_offload_plan,
                "H3OffloadPlanError": H3OffloadPlanError,
                "HTTPException": FakeHTTPException,
                "generation_slot": (
                    lambda *_args, **_kwargs: contextlib.nullcontext(True)
                ),
                "_stamp_requested_generation_residency": lambda *_args, **_kwargs: None,
                "try_start": lambda *_args, **_kwargs: True,
                "_apply_per_job_coefficient": lambda *_args: None,
                "_queue_recovery_delivery_pending": lambda *_args: None,
                "_require_job_runtime_model_admission": lambda _job: None,
                "_trusted_h3_prepared_plan": (
                    lambda body, *_args, **_kwargs: body.get("_h3_longform")
                ),
                "_apply_h3_adaptive_checkpoint": lambda *_args: None,
                "_require_h3_native_boundary_experimental": lambda *_args: None,
                "_validate_h3_sampling_steps": lambda *_args: None,
                "_validate_h3_explicit_multiclip_request": lambda *_args: None,
                "_prepare_h3_long_studio_request": (
                    lambda body: body.get("_h3_longform")
                ),
                "_validate_h3_segment_plan": lambda *_args, **_kwargs: None,
                "_validate_h3_lightx2v_recovery_identity": lambda *_args: None,
                "_require_h3_acceleration_available": lambda *_args, **_kwargs: None,
                "_require_h3_generation_terms": lambda *_args: None,
                "_public_h3_long_plan": lambda plan: {"clip_count": plan["clip_count"]},
                "_h3_effective_model_types": (
                    lambda *_args: ["minimax_h3_ref2va"]
                ),
                "_ensure_versioned_model_current": lambda *_args: {},
                "_ensure_h3_effective_models_current": lambda *_args, **_kwargs: {},
                "update_job": lambda *_args, **_kwargs: True,
                "register_abort_state": lambda *_args, **_kwargs: True,
                "_interrupt_wan_model": lambda: None,
                "_ensure_managed_loras_present": lambda *_args, **_kwargs: None,
                "_merge_h3_ref2va_keyframes": lambda refs, frames: list(refs or []) + list(frames),
                "finish_job": lambda *_args, **_kwargs: False,
                "_safe_failure_updates": lambda error, _job: {"error": type(error).__name__},
                "unregister_abort_state": lambda *_args: None,
                "_restore_base_coefficient": lambda: None,
                "_workspace_dir": lambda: job["out_dir"],
            },
        )
        managed = types.SimpleNamespace(
            ensure_video_depth_checkpoint=lambda *_args, **_kwargs: None,
            uses_temporal_depth=lambda _params: False,
        )
        with mock.patch.dict(
            sys.modules, {"services.managed_preprocessors": managed},
        ):
            with self.assertRaises(ParserBoundary):
                namespace["_run_generation"](job["id"])

        self.assertEqual(len(captured), 6)
        self.assertEqual(FakeWGP.video_profile, 2)
        for index, task in enumerate(captured):
            task_params = task["params"]
            self.assertEqual(task_params["override_profile"], 5)
            self.assertEqual(
                task_params["multi_clip_info"][
                    "semantic_physical_contract_version"
                ],
                2,
            )
            self.assertEqual(
                task_params["_h3_execution_slice"],
                shot_plan["shots"][index]["execution_slice"],
            )
            self.assertEqual(
                task_params["multi_clip_info"]["execution_slice"],
                shot_plan["shots"][index]["execution_slice"],
            )
        for index in (4, 5):
            self.assertEqual(
                captured[index]["params"]["multi_clip_info"][
                    "peak_recovery_identity"
                ],
                peak_identity,
            )

    def test_local_recovery_prepare_never_starts_and_binds_exact_evidence(self):
        source = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "prepare_local_h3_generation_recovery"),
        )
        self.assertIn("expected_manifest_sha256", source)
        self.assertIn("expected_cursor_sha256", source)
        self.assertIn("hmac.compare_digest", source)
        self.assertIn("_local_h3_recovery_candidates", source)
        self.assertIn("_register_discovered_local_h3_recovery", source)
        self.assertNotIn("threading.Thread", source)

        discovery = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "discover_local_h3_generation_recovery"),
        )
        self.assertIn("expected_project_digest", discovery)
        self.assertIn("expected_manifest_sha256", discovery)
        self.assertIn("_local_h3_recovery_cursor_digest", discovery)
        self.assertNotIn("params", discovery)
        self.assertNotIn("prompt", discovery)

        startup = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_restore_queue_recovery_on_startup"),
        )
        self.assertLess(
            startup.index("_preserve_local_h3_recovery_evidence"),
            startup.index("cleanup_orphan_request_manifests("),
        )
        self.assertIn("recovery_cleanup_blocked.add(workspace)", startup)
        self.assertIn("if workspace in recovery_cleanup_blocked", startup)

        namespace = _isolated_functions(
            self.launch,
            ("_preserve_local_h3_recovery_evidence",),
            {
                "_local_h3_recovery_candidates": (
                    lambda *_args: (_ for _ in ()).throw(
                        QueueRecoveryRuntimeError("transient discovery fault")
                    )
                ),
            },
        )
        live_manifests = {}
        live_staging = {}
        self.assertFalse(namespace[
            "_preserve_local_h3_recovery_evidence"
        ]("x_test", "/project", live_manifests, live_staging))
        self.assertEqual(live_manifests, {})
        self.assertEqual(live_staging, {})

        candidates = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_local_h3_recovery_candidates"),
        )
        self.assertIn(
            "Local H3 recovery evidence could not be validated", candidates,
        )
        self.assertNotIn(
            "except (QueueRecoveryRuntimeError, TypeError, ValueError):\n"
            "            continue",
            candidates,
        )

    def test_local_recovery_discovery_mints_and_registers_one_exact_incarnation(self):
        pointer = {
            "path": ".maestro-recovery/job-local.request.json",
            "sha256": "a" * 64,
        }
        created_at = 4_321.25
        namespace = _isolated_functions(
            self.launch,
            ("_local_h3_recovery_candidates",),
            {
                "discover_request_manifest_pointers": lambda *_args, **_kwargs: [{
                    "job_id": "job-local", "pointer": pointer,
                }],
                "load_request_manifest": lambda *_args, **_kwargs: {
                    "params": {
                        "model_type": "minimax_h3",
                        "generation_mode": "video",
                        "repeat_generation": 1,
                    },
                },
                "_local_h3_discovery_owner_digest": lambda _manifest: "owner",
                "validate_manifest_inputs": lambda *_args, **_kwargs: None,
                "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: None,
                "_queue_recovery_reconcile_cursor": lambda *_args, **_kwargs: None,
                "_h3_incomplete_recovery_prefix": lambda _job: 1,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "copy": copy,
                "time": types.SimpleNamespace(time=lambda: created_at),
            },
        )
        candidates = namespace["_local_h3_recovery_candidates"](
            "synthetic-project", "/synthetic-project",
        )
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["created_at"], created_at)

        observed = {}

        class FakeJobs:
            def prepare(self, job):
                return dict(job)

            def publish_prepared(self, job_id, prepared):
                observed["published"] = (job_id, dict(prepared))

        class FakeCoordinator:
            def register_job(self, job, **kwargs):
                observed["registered"] = (dict(job), dict(kwargs))

        project_digest = "project:v1:" + "b" * 64
        register_namespace = _isolated_functions(
            self.launch,
            ("_register_discovered_local_h3_recovery",),
            {
                "_jobs": FakeJobs(),
                "_workspace_lifecycle_lock": __import__("threading").RLock(),
                "_require_job_workspace_available": lambda _job: None,
                "_queue_recovery_project_identity": (
                    lambda *_args: project_digest
                ),
                "_queue_recovery_with_bounded_compaction": lambda operation: operation(),
                "_queue_recovery_coordinator": FakeCoordinator(),
                "durable_queue_state": lambda *, additions=(): {
                    "created_at": additions[0]["created_at"],
                },
                "hmac": hmac,
                "DurableTransition": object,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            },
        )
        registered = register_namespace[
            "_register_discovered_local_h3_recovery"
        ](candidate, project_digest=project_digest)
        self.assertEqual(registered["created_at"], created_at)
        self.assertEqual(observed["registered"][0]["created_at"], created_at)
        self.assertEqual(
            observed["registered"][1]["global_state"]["created_at"],
            created_at,
        )
        self.assertEqual(observed["published"][1]["created_at"], created_at)

    def test_local_recovery_discovery_selects_exact_sealed_revision(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        fake_api = types.SimpleNamespace(
            get=lambda *_args, **_kwargs: lambda function: function,
        )
        project_digest = "project:v1:" + "a" * 64
        candidate_calls = []
        worker_starts = []

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first = write_sealed_request_manifest(
                project,
                job_id="job-two-revisions",
                params={"revision": 1},
                inputs=[],
            )
            second = write_sealed_request_manifest(
                project,
                job_id="job-two-revisions",
                params={"revision": 2},
                inputs=[],
            )

            def candidates(
                workspace,
                project_dir,
                *,
                expected_manifest_sha256="",
            ):
                self.assertEqual(workspace, "x_test")
                self.assertEqual(project_dir, str(project))
                candidate_calls.append(expected_manifest_sha256)
                return [
                    {
                        "id": entry["job_id"],
                        "_recovery_manifest_pointer": dict(entry["pointer"]),
                        "recovery_cursor": {"completed_units": ["sealed"]},
                    }
                    for entry in discover_request_manifest_pointers(
                        project,
                        expected_sha256=expected_manifest_sha256,
                    )
                ]

            namespace = _isolated_functions(
                self.launch,
                (
                    "discover_local_h3_generation_recovery",
                    "_validate_local_h3_recovery_discovery_selectors",
                    "_require_current_local_h3_recovery_project",
                    "_raise_local_h3_recovery_http_error",
                    "get_local_h3_recovery_discovery",
                ),
                {
                    "api": fake_api,
                    "Request": object,
                    "Response": object,
                    "HTTPException": FakeHTTPException,
                    "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                    "re": re,
                    "hmac": hmac,
                    "_get_active_workspace": lambda: "x_test",
                    "_require_workspace_not_deleting": lambda _workspace: None,
                    "_existing_workspace_dir": lambda _workspace: str(project),
                    "_queue_recovery_project_identity": (
                        lambda *_args: project_digest
                    ),
                    "_local_h3_recovery_candidates": candidates,
                    "_local_h3_recovery_cursor_digest": (
                        lambda job: job["_recovery_manifest_pointer"]["sha256"]
                    ),
                    "_h3_incomplete_recovery_prefix": lambda _job: 1,
                    "_require_local_recovery_control_request": (
                        lambda _request: None
                    ),
                    "_set_recovery_no_store": (
                        lambda response: response.headers.update({
                            "Cache-Control": "private, no-store",
                        })
                    ),
                    "_reserve_workspace_operations": (
                        lambda *_args: contextlib.nullcontext()
                    ),
                    "_register_discovered_local_h3_recovery": (
                        lambda *_args, **_kwargs: worker_starts.append(
                            "registered"
                        )
                    ),
                    "threading": types.SimpleNamespace(
                        Thread=lambda *_args, **_kwargs: worker_starts.append(
                            "worker"
                        )
                    ),
                },
            )
            response = types.SimpleNamespace(headers={})
            with self.assertRaises(FakeHTTPException) as ambiguous:
                namespace["get_local_h3_recovery_discovery"](
                    object(), response, workspace="x_test",
                )
            self.assertEqual(ambiguous.exception.status_code, 409)
            self.assertEqual(
                ambiguous.exception.detail,
                "Local recovery evidence is unavailable or changed",
            )
            self.assertEqual(candidate_calls, [""])
            self.assertEqual(
                response.headers["Cache-Control"], "private, no-store",
            )

            selected_response = types.SimpleNamespace(headers={})
            selected = namespace["get_local_h3_recovery_discovery"](
                object(),
                selected_response,
                workspace="x_test",
                project_digest=project_digest,
                manifest_sha256=second["sha256"],
            )
            self.assertEqual(selected["manifest_sha256"], second["sha256"])
            self.assertEqual(
                selected_response.headers["Cache-Control"],
                "private, no-store",
            )
            self.assertEqual(candidate_calls, ["", second["sha256"]])
            self.assertEqual(set(selected), {
                "job_id", "workspace", "project_digest", "manifest_sha256",
                "cursor_sha256", "completed_segments", "status",
            })

            calls_before_stale = list(candidate_calls)
            with self.assertRaises(FakeHTTPException) as stale:
                namespace["get_local_h3_recovery_discovery"](
                    object(),
                    types.SimpleNamespace(headers={}),
                    workspace="x_test",
                    project_digest="project:v1:" + "b" * 64,
                    manifest_sha256=second["sha256"],
                )
            self.assertEqual(stale.exception.status_code, 409)
            self.assertEqual(candidate_calls, calls_before_stale)

            for invalid_project, invalid_manifest in (
                ("a" * 64, second["sha256"]),
                (project_digest, second["sha256"].upper()),
            ):
                with self.subTest(
                    project_digest=invalid_project[:16],
                    manifest_sha256=invalid_manifest[:16],
                ):
                    with self.assertRaises(FakeHTTPException) as invalid:
                        namespace["get_local_h3_recovery_discovery"](
                            object(),
                            types.SimpleNamespace(headers={}),
                            workspace="x_test",
                            project_digest=invalid_project,
                            manifest_sha256=invalid_manifest,
                        )
                    self.assertEqual(invalid.exception.status_code, 400)

            self.assertEqual(worker_starts, [])
            self.assertNotIn(
                "threading.Thread",
                ast.get_source_segment(
                    self.launch_source,
                    _function(
                        self.launch, "prepare_local_h3_generation_recovery",
                    ),
                ),
            )
            self.assertEqual(
                {
                    entry["pointer"]["sha256"]
                    for entry in discover_request_manifest_pointers(project)
                },
                {first["sha256"], second["sha256"]},
            )
            self.assertTrue((project / first["path"]).is_file())
            self.assertTrue((project / second["path"]).is_file())

    def test_discovered_cursor_change_rolls_back_durable_registration(self):
        jobs = {}
        rolled_back = []
        candidate = {
            "id": "job-discovered",
            "cursor_digest": "before",
            "_recovery_manifest_pointer": {"sha256": "a" * 64},
        }

        def register(job, *, project_digest):
            self.assertEqual(project_digest, "project-digest")
            jobs[job["id"]] = job
            return job

        def reconcile(job, _project_dir):
            job["cursor_digest"] = "after"

        def rollback(job_id):
            rolled_back.append(job_id)
            jobs.pop(job_id, None)

        namespace = _isolated_functions(
            self.launch,
            ("prepare_local_h3_generation_recovery",),
            {
                "_queue_recovery_checkpoint_lock": __import__("threading").RLock(),
                "_jobs": jobs,
                "_existing_workspace_dir": lambda _workspace: "/project",
                "_queue_recovery_project_identity": lambda *_args: "project-digest",
                "_local_h3_recovery_candidates": lambda *_args, **_kwargs: [candidate],
                "_local_h3_recovery_cursor_digest": lambda job: job["cursor_digest"],
                "_register_discovered_local_h3_recovery": register,
                "_queue_recovery_reconcile_cursor": reconcile,
                "_rollback_discovered_local_h3_recovery": rollback,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "hmac": hmac,
            },
        )
        with self.assertRaises(QueueRecoveryRuntimeError):
            namespace["prepare_local_h3_generation_recovery"](
                "job-discovered",
                expected_manifest_sha256="a" * 64,
                expected_cursor_sha256="before",
                workspace="x_test",
                expected_project_digest="project-digest",
            )
        self.assertEqual(rolled_back, ["job-discovered"])
        self.assertEqual(jobs, {})

        candidate["cursor_digest"] = "before"
        namespace = _isolated_functions(
            self.launch,
            ("prepare_local_h3_generation_recovery",),
            {
                "_queue_recovery_checkpoint_lock": __import__("threading").RLock(),
                "_jobs": jobs,
                "_existing_workspace_dir": lambda _workspace: "/project",
                "_queue_recovery_project_identity": lambda *_args: "project-digest",
                "_local_h3_recovery_candidates": lambda *_args, **_kwargs: [candidate],
                "_local_h3_recovery_cursor_digest": lambda job: job["cursor_digest"],
                "_register_discovered_local_h3_recovery": register,
                "_queue_recovery_reconcile_cursor": (
                    lambda *_args: (_ for _ in ()).throw(
                        QueueRecoveryRuntimeError("transient cursor fault")
                    )
                ),
                "_rollback_discovered_local_h3_recovery": rollback,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "hmac": hmac,
            },
        )
        with self.assertRaises(QueueRecoveryRuntimeError):
            namespace["prepare_local_h3_generation_recovery"](
                "job-discovered",
                expected_manifest_sha256="a" * 64,
                expected_cursor_sha256="before",
                workspace="x_test",
                expected_project_digest="project-digest",
            )
        self.assertEqual(rolled_back, ["job-discovered", "job-discovered"])
        self.assertEqual(jobs, {})

    def test_local_recovery_control_is_content_free_and_start_is_separate(self):
        read_source = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_read_local_h3_recovery_assertion"),
        )
        self.assertLess(
            read_source.index("_require_local_recovery_control_request"),
            read_source.index("await request.json()"),
        )

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class BodyRequest:
            def __init__(self, body):
                self.body = body
                self.body_read = False

            async def json(self):
                self.body_read = True
                return self.body

        rejection = _isolated_functions(
            self.launch,
            ("_read_local_h3_recovery_assertion",),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "_require_local_recovery_control_request": (
                    lambda _request: (_ for _ in ()).throw(
                        FakeHTTPException(status_code=403, detail="local only")
                    )
                ),
            },
        )
        remote_request = BodyRequest({"prompt": "PRIVATE_SENTINEL"})
        with self.assertRaises(FakeHTTPException):
            asyncio.run(rejection[
                "_read_local_h3_recovery_assertion"
            ](remote_request, job_id="job-recovery"))
        self.assertFalse(remote_request.body_read)

        fake_api = types.SimpleNamespace(
            get=lambda *_args, **_kwargs: lambda function: function,
            post=lambda *_args, **_kwargs: lambda function: function,
        )
        digest_a = "project:v1:" + "a" * 64
        digest_b, digest_c = "b" * 64, "c" * 64
        job = {
            "id": "job-recovery",
            "workspace": "x_test",
            "out_dir": "/private/project",
            "status": "queued",
            "recovery_state": "blocked",
            "queue_held": True,
            "prompt": "PRIVATE_SENTINEL",
            "_recovery_reason_code": "h3_generation_oom_replanned",
            "_recovery_manifest_pointer": {"sha256": digest_b},
        }
        calls = []

        def prepare(job_id, **kwargs):
            calls.append(("prepare", job_id, dict(kwargs)))
            return {"status": "blocked"}

        def start(job_id, **kwargs):
            self.assertEqual(
                job["_recovery_reason_code"],
                "h3_generation_oom_replanned",
            )
            calls.append(("start", job_id, dict(kwargs)))
            return {
                "job_id": job_id,
                "status": "queued",
                "recovery_state": "retrying",
                "recovery_attempt": 1,
            }

        namespace = _isolated_functions(
            self.launch,
            (
                "_validate_local_h3_recovery_assertion",
                "_read_local_h3_recovery_assertion",
                "_local_h3_recovery_control_status",
                "_raise_local_h3_recovery_http_error",
                "get_local_h3_recovery_status",
                "prepare_local_h3_recovery_control",
                "start_local_h3_recovery_control",
            ),
            {
                "api": fake_api,
                "Request": object,
                "Response": object,
                "HTTPException": FakeHTTPException,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "copy": __import__("copy"),
                "_LOCAL_H3_RECOVERY_ASSERTION_KEYS": frozenset({
                    "workspace", "project_digest", "manifest_sha256",
                    "cursor_sha256",
                }),
                "_LOCAL_H3_RECOVERY_JOB_ID_RE": re.compile(
                    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
                ),
                "re": re,
                "hmac": hmac,
                "_require_local_recovery_control_request": lambda _request: None,
                "_set_recovery_no_store": (
                    lambda response: response.headers.update({
                        "Cache-Control": "private, no-store",
                    })
                ),
                "_reserve_workspace_operations": (
                    lambda *_args: contextlib.nullcontext()
                ),
                "_require_current_local_h3_recovery_project": (
                    lambda *_args, **_kwargs: (
                        "/private/project", digest_a,
                    )
                ),
                "_queue_recovery_project_identity": (
                    lambda *_args: digest_a
                ),
                "_queue_recovery_reason_code": (
                    lambda value: value.get("reason", "h3_generation_oom_replanned")
                ),
                "_local_h3_recovery_cursor_digest": lambda _job: digest_c,
                "_queue_recovery_revalidate_job": (
                    lambda value: (
                        value.__setitem__("_recovery_reason_code", "")
                        is None
                    )
                ),
                "prepare_local_h3_generation_recovery": prepare,
                "start_local_h3_generation_recovery": start,
                "_jobs": {job["id"]: job},
            },
        )
        assertion = {
            "workspace": "x_test",
            "project_digest": digest_a,
            "manifest_sha256": digest_b,
            "cursor_sha256": digest_c,
        }
        self.assertEqual(
            namespace["_validate_local_h3_recovery_assertion"](
                assertion, job_id=job["id"],
            ),
            assertion,
        )
        for invalid_project_digest in (
            "a" * 64,
            "project:v2:" + "a" * 64,
            "project:v1:" + "a" * 63,
            "project:v1:" + "A" * 64,
            " project:v1:" + "a" * 64,
            "project:v1:" + "a" * 64 + "x",
        ):
            with self.subTest(project_digest=invalid_project_digest[:20]):
                with self.assertRaises(FakeHTTPException):
                    namespace["_validate_local_h3_recovery_assertion"](
                        {
                            **assertion,
                            "project_digest": invalid_project_digest,
                        },
                        job_id=job["id"],
                    )
        with self.assertRaises(FakeHTTPException):
            asyncio.run(namespace[
                "prepare_local_h3_recovery_control"
            ](
                job["id"],
                BodyRequest({**assertion, "prompt": "PRIVATE_SENTINEL"}),
                types.SimpleNamespace(headers={}, status_code=200),
            ))
        self.assertEqual(calls, [])
        response = types.SimpleNamespace(headers={}, status_code=200)
        prepared = asyncio.run(namespace[
            "prepare_local_h3_recovery_control"
        ](job["id"], BodyRequest(assertion), response))
        self.assertEqual([call[0] for call in calls], ["prepare"])
        self.assertTrue(prepared["queue_held"])
        self.assertNotIn("PRIVATE_SENTINEL", repr(prepared))
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

        status = namespace["get_local_h3_recovery_status"](
            job["id"], BodyRequest({}), types.SimpleNamespace(headers={}),
            **assertion,
        )
        self.assertNotIn("PRIVATE_SENTINEL", repr(status))
        self.assertEqual(
            job["_recovery_reason_code"],
            "h3_generation_oom_replanned",
        )
        started_response = types.SimpleNamespace(headers={}, status_code=200)
        started = asyncio.run(namespace[
            "start_local_h3_recovery_control"
        ](job["id"], BodyRequest(assertion), started_response))
        self.assertEqual([call[0] for call in calls], ["prepare", "start"])
        self.assertEqual(started_response.status_code, 202)
        self.assertEqual(started["status"], "queued")
        self.assertNotIn("PRIVATE_SENTINEL", repr(started))

    def test_h3_success_steps_classify_probe_vs_voting_production(self):
        generation_source = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        self.assertIn(
            "_h3_allocation_success_outcome(task_params)",
            generation_source,
        )
        self.assertIn(
            "_record_h3_allocation_success_observations",
            generation_source,
        )
        namespace = _isolated_functions(
            self.launch,
            (
                "_h3_allocation_scenario",
                "_h3_allocation_success_outcome",
                "_record_h3_allocation_success_observations",
            ),
            {
                "_H3_PEAK_RECOVERY_POLICY_VERSION": 1,
                "_h3_peak_recovery_identity": (
                    lambda params, **_kwargs: {
                        "model_type": "minimax_h3_ref2va",
                        "width": 1344,
                        "height": 768,
                        "sampling_steps": params["num_inference_steps"],
                        "attention_engine": "sol_attn",
                        "offload_profile": 4,
                        "sol_tau": 1.0,
                        "sol_dense_steps": 10,
                        "sol_dense_blocks": 2,
                        "sol_min_tokens": 4096,
                    }
                ),
                "wgp": types.SimpleNamespace(
                    _host_memory_snapshot=lambda: (64 << 30, 80 << 30),
                    _h3_residency_epoch=1,
                ),
                "json": json,
                "hashlib": hashlib,
            },
        )
        classify = namespace["_h3_allocation_success_outcome"]
        self.assertEqual(classify({
            "num_inference_steps": 2,
            "prompt": "PRIVATE_SENTINEL",
        }), "probe_success")
        self.assertEqual(
            classify({"num_inference_steps": 20}), "production_success",
        )
        class IntSteps(int):
            pass

        for malformed in (None, True, "20", 20.0, IntSteps(20), object()):
            with self.subTest(malformed=repr(malformed)):
                self.assertEqual(
                    classify({"num_inference_steps": malformed}),
                    "probe_success",
                )

        sentinel_params = {
            "num_inference_steps": 2,
            "prompt": "PRIVATE_SENTINEL",
            "custom_settings": {},
        }
        with mock.patch(
            "services.oom_detect.safe_allocator_facts",
            return_value={"free_bytes": 20 << 30},
        ):
            real_probe_scenario = namespace["_h3_allocation_scenario"](
                sentinel_params, frame_count=124,
            )
        self.assertNotIn("PRIVATE_SENTINEL", repr(real_probe_scenario))

        def scenario(steps, frames=124):
            return {
                "model_type": "minimax_h3_ref2va",
                "width": 1344,
                "height": 768,
                "frame_count": frames,
                "authored_steps": steps,
                "effective_steps": steps,
                "attention_engine": "sol_attn",
                "attention_signature": "sha256-" + "d" * 64,
                "schedule_id": "native",
                "resource_kind": "cuda_allocation",
                "offload_profile": 4,
                "policy_version": 1,
                "host_ram_band_gib": 64,
                "free_vram_band_gib": 20,
                "residency_epoch_band": 1,
            }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "ledger.json")
            ledger = H3AllocationLedger(path)
            namespace["_get_h3_allocation_ledger"] = lambda: ledger
            namespace["_h3_allocation_outcome_is_clean"] = (
                lambda _job: (True, "")
            )
            namespace["_record_h3_allocation_success_observations"](
                {}, [
                    (real_probe_scenario, "probe_success"),
                    (scenario(20), "production_success"),
                    (scenario(2, 107), "production_success"),
                ],
            )
            probe = ledger.snapshot(real_probe_scenario)
            production = ledger.snapshot(scenario(20))
            self.assertEqual(probe["probe_success_episodes"], 1)
            self.assertEqual(probe["strong_success_episodes"], 0)
            self.assertFalse(probe["upward_confirmed"])
            self.assertEqual(production["probe_success_episodes"], 0)
            self.assertEqual(production["strong_success_episodes"], 1)
            mislabeled = ledger.snapshot(scenario(2, 107))
            self.assertEqual(mislabeled["probe_success_episodes"], 1)
            self.assertEqual(mislabeled["strong_success_episodes"], 0)

            namespace["_h3_allocation_outcome_is_clean"] = (
                lambda _job: (False, "foreign_gpu_allocation")
            )
            namespace["_record_h3_allocation_success_observations"](
                {}, [(scenario(20, 141), "production_success")],
            )
            contaminated = ledger.snapshot(scenario(20, 141))
            self.assertEqual(contaminated["strong_success_episodes"], 0)
            self.assertNotIn("PRIVATE_SENTINEL", path.read_text("ascii"))

            cleanliness_checks = []
            recorded = []

            def dirty_then_clean(_job):
                cleanliness_checks.append(True)
                return (
                    (False, "foreign_gpu_allocation")
                    if len(cleanliness_checks) == 1 else (True, "")
                )

            namespace["_h3_allocation_outcome_is_clean"] = dirty_then_clean
            namespace["_get_h3_allocation_ledger"] = lambda: types.SimpleNamespace(
                record=lambda *args: recorded.append(args),
            )
            namespace["_record_h3_allocation_success_observations"](
                {}, [
                    (scenario(20, 158), "production_success"),
                    (scenario(20, 175), "production_success"),
                ],
            )
            self.assertEqual(len(cleanliness_checks), 1)
            self.assertEqual(recorded, [])

    def test_host_terms_reconcile_all_current_durable_projects(self):
        jobs = {
            name: {
                "id": name,
                "workspace": workspace,
                "status": "waiting_for_plan_approval",
                "plan_review_required": True,
                "plan_review_terms_required": True,
                "plan_review_deadline": None,
            }
            for name, workspace in (("a", "project-a"), ("b", "project-b"))
        }
        armed = []
        namespace = _isolated_functions(
            self.launch,
            ("_reconcile_ref2va_waiting_plan_reviews",),
            {
                "Request": object,
                "HTTPException": Exception,
                "time": types.SimpleNamespace(time=lambda: 100.0),
                "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
                "_plan_terms_reconciliation_lock": (
                    __import__("threading").RLock()
                ),
                "_jobs": jobs,
                "_ref2va_host_terms_accepted": lambda: True,
                "_waiting_plan_project_is_current": lambda _job: True,
                "_queue_recovery_revalidate_job": lambda _job: True,
                "_arm_ref2va_waiting_plan_review": (
                    lambda job, **kwargs: armed.append(
                        (job["id"], kwargs["deadline"])
                    ) or True
                ),
            },
        )
        namespace["_reconcile_ref2va_waiting_plan_reviews"]()
        self.assertEqual(armed, [("a", 116.0), ("b", 116.0)])

    def test_cancelled_waiter_wins_over_late_plan_timer(self):
        jobs = {
            "job-cancelled": {
                "id": "job-cancelled",
                "status": "waiting_for_plan_approval",
                "plan_review_deadline": time.time() + 0.03,
            },
        }
        approved = __import__("threading").Event()
        namespace = _isolated_functions(
            self.launch,
            (
                "_cancel_plan_review_timer",
                "_expire_plan_review",
                "_schedule_plan_review_auto_approval",
            ),
            {
                "threading": __import__("threading"),
                "math": __import__("math"),
                "time": time,
                "_jobs": jobs,
                "_plan_review_timer_lock": __import__("threading").Lock(),
                "_plan_review_timers": {},
                "_approve_waiting_generation_plan": (
                    lambda *_args, **_kwargs: approved.set()
                ),
                "fail_preparation": lambda *_args, **_kwargs: None,
            },
        )
        namespace["_schedule_plan_review_auto_approval"](
            jobs["job-cancelled"],
        )
        jobs["job-cancelled"]["status"] = "cancelled"
        self.assertFalse(approved.wait(0.15))
        self.assertEqual(jobs["job-cancelled"]["status"], "cancelled")

    def test_restored_ownership_uses_hmac_and_resume_revalidates_project(self):
        owned = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_job_owned_by_request"),
        )
        resume = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_resume_recovered_job"),
        )
        self.assertIn("owner_principal_digest", owned)
        self.assertIn("hmac.compare_digest", owned)
        self.assertIn("_require_project_access", resume)
        self.assertIn("_queue_recovery_revalidate_job", resume)

    def test_project_recreation_and_missing_input_restore_block_without_worker(self):
        namespace = _isolated_functions(
            self.launch,
            ("_queue_recovery_materialize_job",),
            {
                "hmac": hmac,
                "math": __import__("math"),
                "time": time,
                "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "_queue_recovery_worker": lambda _job: object(),
                "load_request_manifest": lambda *_args, **_kwargs: {
                    "params": {}, "inputs": [{}],
                },
                "validate_manifest_inputs": lambda *_args: (_ for _ in ()).throw(
                    QueueRecoveryRuntimeError("missing input")
                ),
                "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: False,
                "_require_h3_offload_plan_parity": lambda *_args, **_kwargs: None,
                "_queue_recovery_reconcile_cursor": lambda *_args: None,
                "next_recovery_attempt": lambda _job: (1, True),
            },
        )
        materialize = namespace["_queue_recovery_materialize_job"]
        snapshot = {
            "id": "job-blocked", "status": "queued", "workspace": "project-a",
            "owner_principal": "owner:v1:" + "a" * 64,
            "project_instance": "project:v1:" + "b" * 64,
            "request_manifest": {"path": ".maestro-recovery/job-blocked.request.json"},
        }
        recreated, auto_resume = materialize(
            snapshot,
            {"project-a": ("/project", "project:v1:" + "c" * 64)},
        )
        self.assertFalse(auto_resume)
        self.assertEqual(recreated["recovery_state"], "blocked")
        self.assertNotIn("out_dir", recreated)

        missing_input, auto_resume = materialize(
            snapshot,
            {"project-a": ("/project", snapshot["project_instance"])},
        )
        self.assertFalse(auto_resume)
        self.assertEqual(missing_input["recovery_state"], "blocked")
        self.assertEqual(missing_input["out_dir"], "/project")

        corrupt_waiting = materialize(
            {**snapshot, "status": "waiting_for_plan_approval"},
            {"project-a": ("/project", snapshot["project_instance"])},
        )[0]
        self.assertEqual(corrupt_waiting["status"], "failed")
        self.assertEqual(corrupt_waiting["recovery_state"], "terminal")

    def test_waiting_plan_restore_does_not_increment_attempt_or_start_worker(self):
        namespace = _isolated_functions(
            self.launch,
            ("_queue_recovery_materialize_job",),
            {
                "hmac": hmac,
                "math": __import__("math"),
                "time": time,
                "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "_queue_recovery_worker": lambda _job: (_ for _ in ()).throw(
                    AssertionError("waiting state resolved a worker")
                ),
                "load_request_manifest": lambda *_args, **_kwargs: {
                    "params": {"_maestro_prepared_source": {}},
                    "inputs": [],
                },
                "validate_manifest_inputs": lambda *_args: None,
                "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: True,
                "_require_h3_offload_plan_parity": lambda *_args, **_kwargs: None,
                "_queue_recovery_reconcile_cursor": lambda *_args: None,
                "next_recovery_attempt": lambda _job: (_ for _ in ()).throw(
                    AssertionError("waiting state incremented recovery attempt")
                ),
            },
        )
        snapshot = {
            "id": "job-waiting",
            "status": "waiting_for_plan_approval",
            "workspace": "project-a",
            "owner_principal": "owner:v1:" + "a" * 64,
            "project_instance": "project:v1:" + "b" * 64,
            "request_manifest": {
                "path": ".maestro-recovery/job-waiting."
                + "1" * 32 + ".request.json",
            },
            "recovery_attempt": 2,
            "plan_review_required": True,
            "plan_review_deadline": time.time() - 10,
        }
        restored, auto_resume = namespace[
            "_queue_recovery_materialize_job"
        ](
            snapshot,
            {"project-a": ("/project", snapshot["project_instance"])},
        )
        self.assertFalse(auto_resume)
        self.assertEqual(restored["status"], "waiting_for_plan_approval")
        self.assertEqual(restored["recovery_attempt"], 2)
        self.assertTrue(restored["plan_review_required"])
        self.assertEqual(
            restored["plan_review_deadline"],
            snapshot["plan_review_deadline"],
        )

    def test_nonstarted_recovery_paths_do_not_consume_attempts(self):
        namespace = _isolated_functions(
            self.launch,
            ("_queue_recovery_materialize_job",),
            {
                "hmac": hmac,
                "math": __import__("math"),
                "time": time,
                "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "_queue_recovery_worker": lambda _job: object(),
                "load_request_manifest": lambda *_args, **_kwargs: {
                    "params": {}, "inputs": [],
                },
                "validate_manifest_inputs": lambda *_args: None,
                "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: True,
                "_require_h3_offload_plan_parity": lambda *_args, **_kwargs: None,
                "_queue_recovery_reconcile_cursor": lambda *_args: None,
                "_h3_incomplete_recovery_prefix": lambda _job: None,
                "next_recovery_attempt": next_recovery_attempt,
            },
        )
        materialize = namespace["_queue_recovery_materialize_job"]
        base = {
            "id": "job-recovery", "status": "queued",
            "workspace": "project-a",
            "owner_principal": "owner:v1:" + "a" * 64,
            "project_instance": "project:v1:" + "b" * 64,
            "request_manifest": {
                "path": ".maestro-recovery/job-recovery.request.json",
            },
            "recovery_attempt": 2,
        }
        projects = {"project-a": ("/project", base["project_instance"])}
        for updates in (
            {"source_remote": True},
            {"queue_held": True},
        ):
            with self.subTest(updates=updates):
                restored, auto_resume = materialize({**base, **updates}, projects)
                self.assertFalse(auto_resume)
                self.assertEqual(restored["recovery_attempt"], 2)
        restored, auto_resume = materialize(base, projects)
        self.assertTrue(auto_resume)
        self.assertEqual(restored["recovery_attempt"], 3)

    def test_held_incomplete_h3_restart_restores_exact_prepare_authority(self):
        prefix_valid = {"value": True}
        prune_reconciled_units = {"value": False}
        next_attempt_calls = []
        manifest_sha = "d" * 64
        cursor_sha = "e" * 64

        def reconcile_cursor(job, _project_dir):
            if prune_reconciled_units["value"]:
                job["recovery_cursor"] = {"completed_units": []}

        namespace = _isolated_functions(
            self.launch,
            (
                "_queue_recovery_materialize_job",
                "prepare_local_h3_generation_recovery",
            ),
            {
                "hmac": hmac,
                "math": __import__("math"),
                "time": time,
                "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "_queue_recovery_worker": lambda _job: object(),
                "load_request_manifest": lambda *_args, **_kwargs: {
                    "params": {"_h3_longform": {"clip_count": 5}},
                    "inputs": [],
                },
                "validate_manifest_inputs": lambda *_args: None,
                "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: True,
                "_require_h3_offload_plan_parity": lambda *_args, **_kwargs: None,
                "_queue_recovery_reconcile_cursor": reconcile_cursor,
                "_h3_incomplete_recovery_prefix": (
                    lambda _job: 4 if prefix_valid["value"] else None
                ),
                "next_recovery_attempt": lambda _job: (
                    next_attempt_calls.append(True) or (3, True)
                ),
                "_queue_recovery_checkpoint_lock": __import__("threading").RLock(),
                "_jobs": {},
                "_local_h3_recovery_cursor_digest": lambda _job: cursor_sha,
                "_queue_recovery_reason_code": (
                    lambda job: str(job.get("_recovery_reason_code") or "")
                ),
                "_queue_recovery_revalidate_job": lambda _job: True,
                "_prepare_h3_peak_recovery": lambda job: job.update({
                    "_recovery_reason_code": "h3_generation_oom_replanned",
                }) or True,
            },
        )
        materialize = namespace["_queue_recovery_materialize_job"]
        snapshot = {
            "id": "job-h3-held",
            "status": "queued",
            "queue_held": True,
            "recovery_state": "restored",
            "_recovery_reason_code": "",
            "workspace": "project-a",
            "owner_principal": "owner:v1:" + "a" * 64,
            "project_instance": "project:v1:" + "b" * 64,
            "request_manifest": {
                "path": ".maestro-recovery/job-h3-held.request.json",
                "sha256": manifest_sha,
            },
            "recovery_attempt": 2,
        }
        projects = {"project-a": ("/project", snapshot["project_instance"])}
        restored, auto_resume = materialize(snapshot, projects)
        self.assertFalse(auto_resume)
        self.assertEqual(restored["status"], "queued")
        self.assertTrue(restored["queue_held"])
        self.assertEqual(restored["recovery_state"], "blocked")
        self.assertEqual(
            restored["_recovery_reason_code"],
            "h3_generation_recovery_authorization_required",
        )
        self.assertEqual(restored["recovery_attempt"], 2)
        self.assertEqual(next_attempt_calls, [])

        namespace["_jobs"][restored["id"]] = restored
        prepared = namespace["prepare_local_h3_generation_recovery"](
            restored["id"],
            expected_manifest_sha256=manifest_sha,
            expected_cursor_sha256=cursor_sha,
        )
        self.assertEqual(
            prepared["recovery_reason"], "h3_generation_oom_replanned",
        )

        for reason in (
            "h3_peak_calibration_required",
            "h3_generation_oom_replanned",
        ):
            with self.subTest(reason=reason):
                held, may_start = materialize({
                    **snapshot, "_recovery_reason_code": reason,
                }, projects)
                self.assertFalse(may_start)
                self.assertEqual(held["_recovery_reason_code"], reason)
                self.assertTrue(held["queue_held"])

        cancelled, may_start = materialize({
            **snapshot, "status": "cancelled", "cancel_requested": True,
        }, projects)
        self.assertFalse(may_start)
        self.assertEqual(cancelled["status"], "cancelled")

        remote_failed, may_start = materialize({
            **snapshot,
            "status": "failed",
            "queue_held": False,
            "source_remote": True,
        }, projects)
        self.assertFalse(may_start)
        self.assertEqual(
            remote_failed["recovery_state"], "blocked_remote_reauth",
        )
        self.assertEqual(
            remote_failed["_recovery_reason_code"],
            "owner_reauthentication_required",
        )

        prefix_valid["value"] = False
        ordinary, may_start = materialize({
            **snapshot, "id": "job-ordinary-held",
        }, projects)
        self.assertFalse(may_start)
        self.assertEqual(ordinary["recovery_state"], "restored")
        self.assertEqual(ordinary["_recovery_reason_code"], "")
        self.assertTrue(ordinary["queue_held"])

        prune_reconciled_units["value"] = True
        malformed, may_start = materialize({
            **snapshot,
            "id": "job-malformed-prefix",
            "recovery_cursor": {"completed_units": [{
                "kind": "h3_segment", "variant": 0, "index": 2,
            }]},
        }, projects)
        self.assertFalse(may_start)
        self.assertEqual(malformed["recovery_state"], "blocked")
        self.assertEqual(
            malformed["_recovery_reason_code"], "input_missing_or_changed",
        )

    def test_durable_h3_resource_retry_survives_prepare_crash_points(self):
        next_attempt_calls = []
        namespace = _isolated_functions(
            self.launch,
            ("_queue_recovery_materialize_job",),
            {
                "hmac": hmac,
                "math": __import__("math"),
                "time": time,
                "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
                "_MAX_AUTOMATIC_RESOURCE_RETRIES": 2,
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "_queue_recovery_worker": lambda _job: object(),
                "load_request_manifest": lambda *_args, **_kwargs: {
                    "params": {"_h3_longform": {"clip_count": 5}},
                    "inputs": [],
                },
                "validate_manifest_inputs": lambda *_args: None,
                "_queue_recovery_manifest_validator": (
                    lambda *_args, **_kwargs: True
                ),
                "_require_h3_offload_plan_parity": (
                    lambda *_args, **_kwargs: None
                ),
                "_queue_recovery_reconcile_cursor": lambda *_args: None,
                "_h3_incomplete_recovery_prefix": lambda _job: 4,
                "_h3_dependency_closed_recovery_prefix": lambda _job: 4,
                "next_recovery_attempt": lambda _job: (
                    next_attempt_calls.append(True) or (99, False)
                ),
            },
        )
        materialize = namespace["_queue_recovery_materialize_job"]
        base = {
            "id": "job-h3-resource-retry",
            "status": "queued",
            "queue_held": True,
            "recovery_state": "retrying",
            "workspace": "project-a",
            "owner_principal": "owner:v1:" + "a" * 64,
            "project_instance": "project:v1:" + "b" * 64,
            "request_manifest": {
                "path": ".maestro-recovery/job-h3-resource-retry.request.json",
            },
            "recovery_attempt": 3,
            "resource_retry_attempt": 1,
            "resource_retry_limit": 2,
            "resource_retry_phase": "generation",
            "resource_retry_reason": "generation_oom",
        }
        projects = {"project-a": ("/project", base["project_instance"])}

        pending, auto_resume = materialize({
            **base,
            "_recovery_reason_code": "h3_resource_retry_pending_replan",
        }, projects)
        self.assertTrue(auto_resume)
        self.assertTrue(pending["queue_held"])
        self.assertEqual(pending["recovery_state"], "retrying")
        self.assertEqual(pending["resource_retry_attempt"], 1)

        replanned, auto_resume = materialize({
            **base,
            "_recovery_reason_code": "h3_generation_oom_replanned",
        }, projects)
        self.assertTrue(auto_resume)
        self.assertFalse(replanned["queue_held"])
        self.assertEqual(replanned["recovery_state"], "retrying")
        self.assertEqual(replanned["resource_retry_attempt"], 1)
        self.assertEqual(next_attempt_calls, [])

        calls = []
        preparation = _isolated_functions(
            self.launch,
            ("_prepare_pending_h3_resource_retry",),
            {
                "_prepare_h3_peak_recovery": lambda _job: (
                    _ for _ in ()
                ).throw(OSError("storage unavailable")),
                "_queue_recovery_checkpoint": lambda job, **updates: (
                    calls.append(("checkpoint", dict(updates)))
                    or job.update(updates) is None
                ),
                "_release_model_for_resource_retry": lambda _job: (
                    calls.append(("release", {}))
                ),
                "update_queue_job": lambda *_args, **_kwargs: True,
            },
        )
        original_failure = {"code": "cuda_oom", "is_oom": True}
        failed = {
            "id": "job-h3-prepare-failed",
            "failure_details": dict(original_failure),
        }
        self.assertFalse(
            preparation["_prepare_pending_h3_resource_retry"](failed)
        )
        self.assertEqual(failed["failure_details"], original_failure)
        self.assertTrue(failed["queue_held"])
        self.assertEqual(
            failed["_recovery_reason_code"],
            "h3_resource_retry_prepare_failed",
        )
        self.assertEqual([call[0] for call in calls], ["checkpoint", "release"])

        unhold_calls = []
        unhold_failure = _isolated_functions(
            self.launch,
            ("_prepare_pending_h3_resource_retry",),
            {
                "_prepare_h3_peak_recovery": lambda _job: True,
                "_queue_recovery_checkpoint": lambda *_args, **_kwargs: (
                    _ for _ in ()
                ).throw(OSError("journal unavailable")),
                "_release_model_for_resource_retry": lambda _job: (
                    unhold_calls.append("release")
                ),
                "update_queue_job": lambda *_args, **_kwargs: (
                    unhold_calls.append("queue") or True
                ),
                "is_cancel_requested": lambda _job: False,
            },
        )
        held_replanned = {
            "id": "job-h3-replanned-held",
            "status": "queued",
            "queue_held": True,
            "_recovery_reason_code": "h3_generation_oom_replanned",
        }
        self.assertFalse(
            unhold_failure["_prepare_pending_h3_resource_retry"](
                held_replanned
            )
        )
        self.assertTrue(held_replanned["queue_held"])
        self.assertEqual(unhold_calls, ["release"])

        boundary = _isolated_functions(
            self.launch,
            ("_h3_resource_retry_boundary",),
            {
                "_queue_recovery_delivery_pending": lambda _job: None,
                "_h3_dependency_closed_recovery_prefix": lambda _job: None,
                "_queue_recovery_units": lambda _job: [
                    {"kind": "h3_segment", "variant": 0, "index": 0},
                    {"kind": "h3_segment", "variant": 0, "index": 1},
                    {"kind": "h3_concat", "variant": 0, "index": 0},
                ],
            },
        )
        self.assertEqual(
            boundary["_h3_resource_retry_boundary"]({
                "requested_outputs": 1,
                "params": {"_h3_longform": {"clip_count": 2}},
            }),
            "finalization",
        )

    def test_remote_recovery_resume_requires_exact_owner_and_revalidation(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class FakeThread:
            started = 0

            def __init__(self, *, target, args, daemon, name):
                self.target = target
                self.args = args

            def start(self):
                FakeThread.started += 1

        secret = b"queue-recovery-test-secret-32b!"
        owner = "owner-session"
        worker = lambda *_args: None
        selected_worker = {"value": None}
        project_access_checks = []
        job = {
            "id": "job-remote", "workspace": "project-a",
            "recovery_state": "blocked_remote_reauth",
            "_recovery_owner_digest": owner_principal_digest(secret, owner),
            "private": True, "explicit": False,
        }
        namespace = _isolated_functions(
            self.launch,
            ("_resume_recovered_job",),
            {
                "HTTPException": FakeHTTPException,
                "_require_owned_job": lambda _job_id, request: (
                    job
                    if request.state.maestro_session_id == owner
                    else (_ for _ in ()).throw(FakeHTTPException(
                        status_code=404, detail="Job not found",
                    ))
                ),
                "_require_project_access": (
                    lambda _request, workspace, *, permission: (
                        project_access_checks.append((workspace, permission))
                        or "/project"
                    )
                ),
                "owner_principal_digest": owner_principal_digest,
                "_session_secret": lambda: secret,
                "hmac": hmac,
                "_queue_recovery_revalidate_job": lambda _job: True,
                "_queue_recovery_delivery_pending": lambda _job: None,
                "_queue_recovery_checkpoint": lambda target, **updates: (
                    target.update(updates) or True
                ),
                "_queue_recovery_checkpoint_lock": __import__("threading").RLock(),
                "_queue_recovery_reason_code": lambda _job: (
                    "owner_reauthentication_required"
                    if selected_worker["value"] is not None
                    else "preparation_must_resubmit"
                ),
                "_QUEUE_RECOVERY_REASON_TEXT": {
                    "owner_reauthentication_required": "Unlock this project to resume",
                    "preparation_must_resubmit": "This preparation must be resubmitted",
                },
                "next_recovery_attempt": next_recovery_attempt,
                "MAX_RECOVERY_ATTEMPTS": 3,
                "update_queue_job": lambda *_args, **_kwargs: True,
                "_queue_recovery_worker": lambda _job: selected_worker["value"],
                "_require_job_runtime_model_admission": lambda _job: None,
                "threading": types.SimpleNamespace(Thread=FakeThread),
            },
        )
        resume = namespace["_resume_recovered_job"]
        wrong_request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_session_id="other-owner"),
        )
        with self.assertRaises(FakeHTTPException) as raised:
            resume(job["id"], wrong_request)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(FakeThread.started, 0)

        right_request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_session_id=owner),
        )
        before = dict(job)
        with self.assertRaises(FakeHTTPException) as raised:
            resume(job["id"], right_request)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(job, before)
        self.assertEqual(FakeThread.started, 0)

        selected_worker["value"] = worker
        result = resume(job["id"], right_request)
        self.assertEqual(result["recovery_state"], "retrying")
        self.assertEqual(result["recovery_attempt"], 1)
        self.assertEqual(FakeThread.started, 1)
        self.assertEqual(
            project_access_checks,
            [("project-a", "project.generate")],
        )

        job.update({
            "recovery_attempt": 3,
            "recovery_state": "blocked_remote_reauth",
        })
        with self.assertRaises(FakeHTTPException) as raised:
            resume(job["id"], right_request)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(job["recovery_attempt"], 3)
        self.assertEqual(job["_recovery_reason_code"], "attempt_limit_reached")
        self.assertEqual(FakeThread.started, 1)

    def test_recovered_remote_reads_require_current_unlocked_active_project(self):
        secret = b"queue-recovery-test-secret-32b!"
        owner = "owner-session"
        project_digest = "project:v1:" + "a" * 64
        active = {}
        access = types.SimpleNamespace(
            status=lambda *_args: types.SimpleNamespace(
                protected=True, unlocked=True,
            ),
        )
        current_digest = {"value": project_digest}
        project_permission_checks = []
        namespace = _isolated_functions(
            self.launch,
            (
                "_recovered_job_remote_project_accessible",
                "_job_owned_by_request",
            ),
            {
                "os": os,
                "hmac": hmac,
                "Request": object,
                "HTTPException": Exception,
                "QueueRecoveryAdapterError": RuntimeError,
                "_accounts_enabled": lambda: False,
                "_remote_active_projects": active,
                "_remote_active_projects_lock": __import__("threading").RLock(),
                "_existing_workspace_dir": lambda _workspace: "/projects/project-a",
                "_require_account_project_permission": (
                    lambda request, project_dir, permission: (
                        project_permission_checks.append((
                            request.state.maestro_session_id,
                            project_dir,
                            permission,
                        ))
                    )
                ),
                "_project_access": access,
                "_queue_recovery_existing_project_identity": (
                    lambda *_args: current_digest["value"]
                ),
                "_session_secret": lambda: secret,
                "owner_principal_digest": owner_principal_digest,
            },
        )
        owned = namespace["_job_owned_by_request"]
        job = {
            "id": "job-remote", "workspace": "project-a",
            "out_dir": "/projects/project-a", "session_id": None,
            "_recovery_owner_digest": owner_principal_digest(secret, owner),
            "_recovery_project_digest": project_digest,
        }
        remote = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_remote=True, maestro_session_id=owner,
        ))
        self.assertFalse(owned(job, remote))
        active[owner] = "another-project"
        self.assertFalse(owned(job, remote))
        active[owner] = "project-a"
        access.status = lambda *_args: types.SimpleNamespace(
            protected=True, unlocked=False,
        )
        self.assertFalse(owned(job, remote))
        access.status = lambda *_args: types.SimpleNamespace(
            protected=True, unlocked=True,
        )
        self.assertTrue(owned(job, remote))
        current_digest["value"] = "project:v1:" + "b" * 64
        self.assertFalse(owned(job, remote))

        legacy = {
            "id": "legacy-remote", "workspace": "project-a",
            "out_dir": "/projects/project-a", "session_id": owner,
        }
        active[owner] = "another-project"
        self.assertFalse(owned(legacy, remote))
        active[owner] = "project-a"
        access.status = lambda *_args: types.SimpleNamespace(
            protected=True, unlocked=False,
        )
        self.assertFalse(owned(legacy, remote))
        access.status = lambda *_args: types.SimpleNamespace(
            protected=True, unlocked=True,
        )
        self.assertTrue(owned(legacy, remote))
        self.assertTrue(project_permission_checks)
        self.assertEqual(
            {permission for _, _, permission in project_permission_checks},
            {"project.read"},
        )
        self.assertEqual(
            {project_dir for _, project_dir, _ in project_permission_checks},
            {"/projects/project-a"},
        )

        # After account-project cutover, membership is the sole project gate.
        # Legacy project-password state must not be consulted for a member,
        # while a nonmember remains indistinguishable from a missing job.
        current_digest["value"] = project_digest
        access.status = lambda *_args: (_ for _ in ()).throw(
            AssertionError("active membership checked a project password")
        )
        namespace["_require_account_project_permission"] = (
            lambda *_args: {"state": "active"}
        )
        self.assertTrue(owned(job, remote))
        namespace["_require_account_project_permission"] = (
            lambda *_args: (_ for _ in ()).throw(Exception("not a member"))
        )
        self.assertFalse(owned(job, remote))

        # Local ownership compatibility does not depend on the remote active
        # project map, while still requiring the exact recovered principal.
        active.clear()
        namespace["_require_account_project_permission"] = (
            lambda *_args: {"state": "active"}
        )
        local = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_remote=False, maestro_session_id=owner,
        ))
        self.assertTrue(owned(job, local))

    def test_remote_recovery_project_probe_never_creates_missing_marker(self):
        class FakeAdapterError(RuntimeError):
            pass

        secret = b"queue-recovery-test-secret-32b!"
        namespace = _isolated_functions(
            self.launch,
            ("_queue_recovery_existing_project_identity",),
            {
                "os": os,
                "re": __import__("re"),
                "stat": stat,
                "QueueRecoveryAdapterError": FakeAdapterError,
                "project_instance_digest": project_instance_digest,
                "_session_secret": lambda: secret,
            },
        )
        read_identity = namespace[
            "_queue_recovery_existing_project_identity"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            marker = project / ".maestro-project-instance"
            with self.assertRaises(FakeAdapterError):
                read_identity(str(project))
            self.assertFalse(marker.exists())
            marker.write_text("a" * 32 + "\n", encoding="ascii")
            self.assertEqual(
                read_identity(str(project)),
                project_instance_digest(secret, "a" * 32),
            )

    def test_blocked_recovery_metadata_is_safe_action_specific_and_bounded(self):
        namespace = _isolated_functions(
            self.launch,
            (
                "_queue_recovery_is_blocked",
                "_queue_recovery_attempt",
                "_queue_recovery_reason_code",
                "_public_queue_recovery_metadata",
            ),
            {
                "MAX_RECOVERY_ATTEMPTS": 3,
                "_BLOCKED_QUEUE_RECOVERY_STATES": frozenset({
                    "blocked", "blocked_preparation", "blocked_remote_reauth",
                }),
                "_QUEUE_RECOVERY_REASON_TEXT": {
                    "attempt_limit_reached": "Recovery attempt limit reached",
                    "input_missing_or_changed": "A required input is missing or changed",
                    "owner_reauthentication_required": "Unlock this project to resume",
                    "preparation_must_resubmit": "This preparation must be resubmitted",
                    "project_missing_or_recreated": "The recovery project is missing or was recreated",
                    "worker_start_failed": "The recovery worker could not be started",
                },
                "_queue_recovery_worker": lambda job: (
                    None if job.get("unsafe_worker") else object()
                ),
            },
        )
        public = namespace["_public_queue_recovery_metadata"]
        cases = (
            ("project_missing_or_recreated", [], False),
            ("input_missing_or_changed", ["retry"], True),
            ("worker_start_failed", ["retry"], True),
        )
        for reason, actions, actionable in cases:
            with self.subTest(reason=reason):
                metadata = public({
                    "recovery_state": "blocked",
                    "recovery_attempt": 1,
                    "_recovery_reason_code": reason,
                })
                self.assertEqual(metadata["recovery_reason"], reason)
                self.assertEqual(metadata["recovery_actions"], actions)
                self.assertEqual(metadata["recovery_actionable"], actionable)
                self.assertNotIn("/", json.dumps(metadata))
        remote = public({
            "recovery_state": "blocked_remote_reauth",
            "recovery_attempt": 1,
            "_recovery_reason_code": "owner_reauthentication_required",
        })
        self.assertEqual(remote["recovery_actions"], ["resume"])
        capped = public({
            "recovery_state": "blocked",
            "recovery_attempt": 3,
            "_recovery_reason_code": "worker_start_failed",
        })
        self.assertEqual(capped["recovery_reason"], "attempt_limit_reached")
        self.assertEqual(capped["recovery_actions"], [])
        preparation = public({
            "recovery_state": "blocked_preparation",
            "recovery_attempt": 1,
            "unsafe_worker": True,
        })
        self.assertEqual(
            preparation["recovery_reason"], "preparation_must_resubmit",
        )
        self.assertEqual(preparation["recovery_actions"], [])

    def test_generic_queue_controls_reject_all_blocked_recovery_states(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code

        namespace = _isolated_functions(
            self.launch,
            (
                "_queue_recovery_is_blocked",
                "_require_generic_queue_control_job",
            ),
            {
                "HTTPException": FakeHTTPException,
                "Request": object,
                "_BLOCKED_QUEUE_RECOVERY_STATES": frozenset({
                    "blocked", "blocked_preparation", "blocked_remote_reauth",
                }),
                "_require_owned_job": lambda *_args: current["job"],
            },
        )
        require = namespace["_require_generic_queue_control_job"]
        current = {"job": {}}
        for state in (
            "blocked", "blocked_preparation", "blocked_remote_reauth",
        ):
            with self.subTest(state=state):
                current["job"] = {"recovery_state": state}
                with self.assertRaises(FakeHTTPException) as raised:
                    require("job", object())
                self.assertEqual(raised.exception.status_code, 409)
        current["job"] = {"recovery_state": "restored"}
        self.assertIs(require("job", object()), current["job"])

        for name in (
            "set_job_queue_priority", "hold_queued_job", "resume_held_job",
            "start_queued_job_next", "set_job_output_count",
        ):
            with self.subTest(route=name):
                source = ast.get_source_segment(
                    self.launch_source, _function(self.launch, name),
                )
                self.assertIn("_require_generic_queue_control_job", source)

    def test_blocked_status_has_no_position_or_live_eta(self):
        fake_api = types.SimpleNamespace(
            get=lambda *_args, **_kwargs: lambda function: function,
        )
        job = {
            "id": "job-blocked", "status": "queued", "progress": 0,
            "created_at": 123.25,
            "message": "Recovery worker could not be started",
            "output_files": [], "error": None,
            "recovery_state": "blocked", "h3_estimate": {"seconds": 20},
            "current_segment_boundary": {
                "type": "cut", "source": "explicit_cut",
                "event": "PRIVATE_BOUNDARY_SENTINEL",
            },
        }
        jobs = {job["id"]: job}
        namespace = _isolated_functions(
            self.launch,
            (
                "_public_h3_boundary", "_public_job_prompt_fields",
                "_public_job_created_at", "get_status", "list_jobs",
            ),
            {
                "api": fake_api,
                "Request": object,
                "Response": object,
                "HTTPException": Exception,
                "_jobs": jobs,
                "_set_recovery_no_store": lambda response: response.headers.update({
                    "Cache-Control": "private, no-store",
                }),
                "_job_owned_by_request": lambda *_args: True,
                "snapshot_job": lambda value: dict(value),
                "queue_scheduler_snapshot": _synthetic_scheduler_snapshot,
                "authorized_logical_queue_projection": (
                    authorized_logical_queue_projection
                ),
                "_queue_recovery_is_blocked": lambda value: (
                    value.get("recovery_state") == "blocked"
                ),
                "_job_eta_values": lambda _value: (50, 10),
                "queue_position": lambda _value: 4,
                "_queue_wait_reason_for_job": lambda _value: "waiting",
                "_public_queue_residency_metadata": lambda *_args, **_kwargs: {},
                "_public_resource_metadata": lambda _value: {
                    "resource_descriptor": None,
                },
                "_public_parent_job_id": lambda _value: None,
                "_public_logical_job_kind": lambda _value: None,
                "_public_progress_telemetry": lambda _value: {},
                "public_h3_offload_plan": lambda _value: None,
                "math": __import__("math"),
                "job_events": lambda *_args: [],
                "queue_control_state": lambda: {},
                "_public_queue_recovery_metadata": lambda _value: {
                    "recovery_blocked": True,
                },
            },
        )
        response = types.SimpleNamespace(headers={})
        result = namespace["get_status"](
            job["id"],
            types.SimpleNamespace(state=types.SimpleNamespace(
                maestro_remote=False,
            )),
            response,
        )
        self.assertIsNone(result["queue_position"])
        self.assertIsNone(result["queue_wait_reason"])
        self.assertIsNone(result["eta_seconds"])
        self.assertNotIn("PRIVATE_BOUNDARY_SENTINEL", repr(result))
        self.assertEqual(
            result["current_segment_boundary"],
            {"type": "cut", "source": "explicit_cut"},
        )
        self.assertIsNone(result["subtask_eta_seconds"])
        self.assertIsNone(result["h3_estimate"])
        self.assertEqual(result["estimate_after_resume"], {"seconds": 20})
        self.assertEqual(result["created_at"], 123.25)
        self.assertEqual(
            response.headers["Cache-Control"], "private, no-store",
        )
        remote_result = namespace["get_status"](
            job["id"],
            types.SimpleNamespace(state=types.SimpleNamespace(
                maestro_remote=True,
            )),
            types.SimpleNamespace(headers={}),
        )
        self.assertEqual(remote_result["created_at"], 123.25)
        for invalid in (None, True, "123.25", float("inf"), -1):
            with self.subTest(invalid_created_at=invalid):
                job["created_at"] = invalid
                invalid_result = namespace["get_status"](
                    job["id"],
                    types.SimpleNamespace(state=types.SimpleNamespace(
                        maestro_remote=False,
                    )),
                    types.SimpleNamespace(headers={}),
                )
                self.assertEqual(invalid_result["created_at"], 0.0)
        jobs["job-current"] = {
            **job,
            "id": "job-current",
            "created_at": 7.5,
        }
        list_response = types.SimpleNamespace(headers={})
        listed = namespace["list_jobs"](
            types.SimpleNamespace(state=types.SimpleNamespace(
                maestro_remote=True,
            )),
            list_response,
        )
        self.assertEqual(
            [item["created_at"] for item in listed["jobs"]],
            [0.0, 7.5],
        )
        self.assertEqual(
            list_response.headers["Cache-Control"], "private, no-store",
        )

    def test_reference_terminal_journal_restore_projects_child_oom_correlation(self):
        secret = b"reference-journal-projection-secret"
        owner = "owner-session"
        owner_digest = owner_principal_digest(secret, owner)
        project_digest = project_instance_digest(secret, "a" * 32)
        private_error = "/private/reference/prompt-and-traceback"
        child = {
            "id": "reference-child",
            "status": "failed",
            "workspace": "project",
            "model_type": "flux2_klein_9b",
            "generation_mode": "image",
            "created_at": 101.0,
            "progress": 0,
            "message": "Generation failed.",
            "output_files": [],
            "parent_job_id": "reference-parent",
        }
        parent = {
            "id": "reference-parent",
            "status": "failed",
            "workspace": "project",
            "model_type": "flux2_klein_9b",
            "generation_mode": "image",
            "created_at": 100.0,
            "progress": 0,
            "message": "Generation failed.",
            "output_files": [],
            "failed_child_job_id": child["id"],
            "failed_child_status": "failed",
            "failed_child_reason": "cuda_oom",
            "failure_details": {
                "code": "cuda_oom",
                "stage": "denoise",
                "detail": private_error,
                "exception_type": "OutOfMemoryError",
                "is_oom": True,
                "allocator": {"device_type": "cuda", "free_bytes": 12},
            },
            "oom_info": {
                "is_oom": True,
                "stage": "denoise",
                "current_coefficient": 0.8,
                "suggested_coefficient": 0.7,
                "message": private_error,
                "traceback": private_error,
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            journal = QueueRecoveryJournal(Path(temporary) / "queue.jsonl")
            coordinator = QueueRecoveryCoordinator(journal)
            for job in (parent, child):
                coordinator.register_job(
                    job,
                    owner_digest=owner_digest,
                    project_digest=project_digest,
                    request_manifest={"kind": "synthetic"},
                )
            restored = QueueRecoveryCoordinator(journal).restore().jobs

            materializer = _isolated_functions(
                self.launch,
                ("_queue_recovery_materialize_job",),
                {
                    "hmac": hmac,
                    "math": __import__("math"),
                    "time": time,
                    "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                    "load_request_manifest": lambda *_args, **_kwargs: {
                        "params": {}, "inputs": [],
                    },
                    "validate_manifest_inputs": lambda *_args: None,
                    "_queue_recovery_manifest_validator": (
                        lambda *_args, **_kwargs: True
                    ),
                    "_require_h3_offload_plan_parity": lambda _job: None,
                    "_queue_recovery_reconcile_cursor": lambda *_args: None,
                    "_h3_incomplete_recovery_prefix": lambda _job: None,
                },
            )["_queue_recovery_materialize_job"]
            jobs = {
                job_id: materializer(
                    snapshot,
                    {"project": ("/safe/project", project_digest)},
                )[0]
                for job_id, snapshot in restored.items()
            }

            fake_api = types.SimpleNamespace(
                get=lambda *_args, **_kwargs: lambda function: function,
            )
            namespace = _isolated_functions(
                self.launch,
                (
                    "_job_owned_by_request", "_public_parent_job_id",
                    "_public_logical_job_kind",
                    "_public_failed_child_metadata", "_public_job_prompt_fields",
                    "_public_job_created_at", "get_status", "list_jobs",
                ),
                {
                    "api": fake_api,
                    "Request": object,
                    "Response": object,
                    "HTTPException": Exception,
                    "_jobs": jobs,
                    "hmac": hmac,
                    "math": __import__("math"),
                    "re": re,
                    "owner_principal_digest": owner_principal_digest,
                    "_session_secret": lambda: secret,
                    "_recovered_job_remote_project_accessible": (
                        lambda *_args: True
                    ),
                    "snapshot_job": lambda value: dict(value),
                    "queue_scheduler_snapshot": _synthetic_scheduler_snapshot,
                    "authorized_logical_queue_projection": (
                        authorized_logical_queue_projection
                    ),
                    "_set_recovery_no_store": lambda response: None,
                    "_queue_recovery_is_blocked": lambda _job: False,
                    "_job_eta_values": lambda _job: (None, None),
                    "queue_position": lambda _job: None,
                    "_queue_wait_reason_for_job": lambda _job: None,
                    "_public_h3_boundary": lambda _value: None,
                    "public_h3_offload_plan": lambda _value: None,
                    "_public_resource_metadata": lambda _job: {},
                    "_public_queue_residency_metadata": (
                        lambda *_args, **_kwargs: {}
                    ),
                    "_public_progress_telemetry": lambda _job: {},
                    "job_events": lambda *_args: [],
                    "queue_control_state": lambda: {},
                    "_public_queue_recovery_metadata": lambda _job: {},
                },
            )
            request = types.SimpleNamespace(state=types.SimpleNamespace(
                maestro_session_id=owner,
                maestro_remote=False,
            ))
            status = namespace["get_status"](
                parent["id"], request, types.SimpleNamespace(headers={}),
            )
            listed = namespace["list_jobs"](
                request, types.SimpleNamespace(headers={}),
            )

        relation = {
            "failed_child_job_id": child["id"],
            "failed_child_status": "failed",
            "failed_child_reason": "cuda_oom",
        }
        self.assertEqual({key: status[key] for key in relation}, relation)
        self.assertEqual(status["failure_details"]["code"], "cuda_oom")
        self.assertEqual(
            status["oom_info"]["message"],
            "The operation ran out of GPU memory.",
        )
        parent_row = next(
            row for row in listed["jobs"] if row["job_id"] == parent["id"]
        )
        child_row = next(
            row for row in listed["jobs"] if row["job_id"] == child["id"]
        )
        self.assertEqual(
            {key: parent_row[key] for key in relation}, relation,
        )
        self.assertEqual(child_row["parent_job_id"], parent["id"])
        self.assertNotIn(private_error, repr(status))
        self.assertNotIn(private_error, repr(listed))

    def test_wgp_completed_repeat_offset_skips_only_outer_dispatch(self):
        generate = _function(self.wgp, "generate_video")
        arguments = [argument.arg for argument in generate.args.args]
        self.assertIn("repeat_start_offset", arguments)
        source = ast.get_source_segment(self.wgp_source, generate)
        self.assertIn("completed_repeats = max(", source)
        self.assertIn("int(repeat_start_offset or 0)", source)
        self.assertIn("repeat_no = 0", source)

    def test_native_recovery_uses_private_stable_target_before_promotion(self):
        generate = ast.get_source_segment(
            self.wgp_source, _function(self.wgp, "generate_video"),
        )
        self.assertIn("durable_output_dir = candidate", generate)
        self.assertIn("output_dir = durable_output_dir", generate)
        self.assertIn("durable_output_dir or save_path", generate)
        self.assertIn("{durable_file_stem}-audio-tmp.wav", generate)
        runner = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        staging = runner.index("ensure_recovery_staging_directory(")
        sidecar = runner.index("media_paths=", staging)
        promotion = runner.index(
            "_queue_recovery_promote_staged_outputs(", sidecar,
        )
        checkpoint = runner.index("_queue_recovery_checkpoint_unit(", promotion)
        self.assertLess(staging, sidecar)
        self.assertLess(sidecar, promotion)
        self.assertLess(promotion, checkpoint)

    def test_recoverable_h3_handoffs_never_use_public_project_root(self):
        runner = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        continuation = runner.index(
            'f"{recovery_output_prefix}-continuation.png"'
        )
        self.assertLess(
            runner.rindex("if recovery_staging_dir:", 0, continuation),
            continuation,
        )
        self.assertIn(
            "recovery_staging_dir=recovery_staging_dir", runner,
        )
        ref2va = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_attach_h3_ref2va_handoff"),
        )
        self.assertIn(
            'f"{recovery_output_prefix}-continuation-ref.mp4"', ref2va,
        )

    def test_all_recovery_preprocessing_audio_uses_private_unit_prefix(self):
        generate = ast.get_source_segment(
            self.wgp_source, _function(self.wgp, "generate_video"),
        )
        validation = generate.index(
            'raise RuntimeError("Recovery output staging identity is invalid")'
        )
        first_preprocess = generate.index(
            '_recovery_preprocess_path("control-audio")'
        )
        self.assertLess(validation, first_preprocess)
        for label in (
            "control-audio", "clean-audio-1", "clean-audio-2",
            "speaker-1", "speaker-2", "speaker-clean", "clean-audio",
            "clip-offset",
        ):
            with self.subTest(label=label):
                self.assertIn(
                    f'_recovery_preprocess_path("{label}")', generate,
                )
        self.assertIn(
            'f"{durable_output_prefix}-pre-audio-norm-"', generate,
        )
        self.assertIn(
            'f"{durable_output_prefix}-pre-null-"', generate,
        )

    def test_recovered_custom_workers_never_fall_back_to_generation(self):
        generation = lambda *_args: "generation"
        blend = lambda *_args: "blend"
        outpaint = lambda *_args: "outpaint"
        namespace = _isolated_functions(
            self.launch,
            ("_queue_recovery_worker",),
            {
                "_run_generation": generation,
                "_run_blend_generation": blend,
                "_prepare_and_run_outpaint": outpaint,
            },
        )
        select = namespace["_queue_recovery_worker"]
        self.assertIs(select({"kind": "studio_generation"}), generation)
        self.assertIs(select({"kind": "studio_blend"}), blend)
        self.assertIs(
            select({"kind": "studio_outpaint_preparation"}), outpaint,
        )
        for kind in (
            "sample_campaign_generation",
            "studio_project_asset_preparation",
            "studio_repaint_preparation",
            "studio_recast_preparation",
        ):
            with self.subTest(kind=kind):
                self.assertIsNone(select({"kind": kind}))

    def test_startup_reconciles_native_staged_before_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            job_id = "job-orphan-delivery"
            native = project / ".maestro-delivery-job-orphan.native.mp4"
            meta_path = project / ".maestro-delivery-job-orphan.native.meta.json"
            native.write_bytes(b"native-final")
            native_sha = hashlib.sha256(native.read_bytes()).hexdigest()
            dependency = recovery_unit_id(job_id, "h3_concat")
            settings = {
                "delivery_fit": "upscale_exact",
                "delivery_resolution": "1920x1080",
                "native_hashes": [native_sha],
                "spatial_upsampling": "flashvsr2",
            }
            unit_id = recovery_unit_id(
                job_id, "h3_delivery",
                dependencies=[dependency], settings=settings,
            )
            owner = "a" * 32
            meta_path.write_text(json.dumps({
                "private": True,
                "delivery_native_source": True,
                "delivery_recovery": {
                    "source_job_id": job_id,
                    "original_filename": "final.mp4",
                    "owner_session_id": owner,
                    "queue_recovery_unit_id": unit_id,
                    "queue_recovery_dependencies": [dependency],
                    "queue_recovery_settings": settings,
                },
            }), encoding="utf-8")
            secret = b"queue-recovery-test-secret-32b!"
            namespace = _isolated_functions(
                self.launch,
                ("_queue_recovery_reconcile_orphan_delivery",),
                {
                    "os": os, "json": json, "hmac": hmac,
                    "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                    "recovery_unit_id": recovery_unit_id,
                    "_protected_recovery_artifact_descriptor": protected_artifact_descriptor,
                    "owner_principal_digest": owner_principal_digest,
                    "_session_secret": lambda: secret,
                },
            )
            job = {
                "id": job_id,
                "_recovery_owner_digest": owner_principal_digest(secret, owner),
            }
            pending = namespace["_queue_recovery_reconcile_orphan_delivery"](
                job, str(project),
            )
            self.assertIsNotNone(pending)
            self.assertEqual(pending["unit_id"], unit_id)
            self.assertEqual(pending["artifacts"][0]["sha256"], native_sha)
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            raw["delivery_recovery"].pop("owner_session_id")
            meta_path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertIsNone(
                namespace["_queue_recovery_reconcile_orphan_delivery"](
                    job, str(project),
                )
            )
            raw["delivery_recovery"]["owner_session_id"] = "b" * 32
            meta_path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertIsNone(
                namespace["_queue_recovery_reconcile_orphan_delivery"](
                    job, str(project),
                )
            )

    def test_delivery_partial_native_rename_is_completed_from_durable_intent(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            original = "final.mp4"
            source = project / original
            source_meta = project / "final.meta.json"
            source.write_bytes(b"native")
            source_meta.write_text(json.dumps({
                "job_id": "job-stage", "producer_artifact_class": "final",
                "producer_unit_id": recovery_unit_id("job-stage", "h3_concat"),
            }), encoding="utf-8")
            source_descriptor = artifact_descriptor(
                project,
                basename=original,
                sidecar_basename=source_meta.name,
                producer_unit_id=recovery_unit_id("job-stage", "h3_concat"),
            )
            native_name = ".maestro-delivery-job-stage-unit-0-final.native.mp4"
            native_meta_name = ".maestro-delivery-job-stage-unit-0-final.native.meta.json"
            work_name = ".maestro-delivery-job-stage-unit-0-final.work.mp4"
            # Crash after media rename but before sidecar rename/enrichment.
            source.rename(project / native_name)
            plan = {
                "unit_id": recovery_unit_id("job-stage", "h3_delivery"),
                "dependencies": [], "settings": {},
                "staging": [{
                    "original_basename": original,
                    "native_basename": native_name,
                    "native_sidecar_basename": native_meta_name,
                    "source": source_descriptor,
                    "work_basename": work_name,
                }],
            }
            namespace = _isolated_functions(
                self.launch,
                ("_atomic_write_json", "_atomic_write_bytes", "_stage_h3_delivery_native_outputs"),
                {
                    "os": os, "json": json, "uuid": uuid, "base64": base64,
                    "hashlib": hashlib,
                    "_recovery_sha256_file": recovery_sha256_file,
                    "stamp_sidecar_policy": stamp_sidecar_policy,
                },
            )
            job = {
                "id": "job-stage", "session_id": "a" * 32,
                "workspace": "project-a", "params": {},
                "access_policy": {"private": False, "explicit": False},
            }
            staged = namespace["_stage_h3_delivery_native_outputs"](
                job, str(project), [original], plan,
            )
            self.assertEqual(len(staged), 1)
            self.assertFalse(source_meta.exists())
            native_meta = project / native_meta_name
            self.assertTrue(native_meta.exists())
            durable = json.loads(native_meta.read_text(encoding="utf-8"))[
                "delivery_recovery"
            ]
            self.assertEqual(durable["queue_recovery_unit_id"], plan["unit_id"])
            self.assertEqual(
                base64.b64decode(durable["original_sidecar_b64"]),
                json.dumps({
                    "job_id": "job-stage", "producer_artifact_class": "final",
                    "producer_unit_id": recovery_unit_id("job-stage", "h3_concat"),
                }).encode("utf-8"),
            )

    def test_delivery_partial_sidecar_rename_is_enriched_without_policy_loss(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            original = "final.mp4"
            original_sidecar = {
                "job_id": "job-stage", "private": True,
                "owner_session_id": "owner",
                "producer_artifact_class": "final",
                "producer_unit_id": recovery_unit_id("job-stage", "h3_concat"),
            }
            native_name = ".maestro-delivery-job-stage-unit-0-final.native.mp4"
            native_meta_name = ".maestro-delivery-job-stage-unit-0-final.native.meta.json"
            native = project / native_name
            native_meta = project / native_meta_name
            source = project / original
            source_meta = project / "final.meta.json"
            source.write_bytes(b"native")
            # Crash after both renames but before native sidecar enrichment.
            raw_original = json.dumps(original_sidecar).encode("utf-8")
            source_meta.write_bytes(raw_original)
            source_descriptor = artifact_descriptor(
                project,
                basename=original,
                sidecar_basename=source_meta.name,
                producer_unit_id=recovery_unit_id("job-stage", "h3_concat"),
            )
            source.rename(native)
            source_meta.rename(native_meta)
            plan = {
                "unit_id": recovery_unit_id("job-stage", "h3_delivery"),
                "dependencies": [], "settings": {},
                "staging": [{
                    "original_basename": original,
                    "native_basename": native_name,
                    "native_sidecar_basename": native_meta_name,
                    "source": source_descriptor,
                    "work_basename": ".maestro-delivery-job-stage-unit-0-final.work.mp4",
                }],
            }
            namespace = _isolated_functions(
                self.launch,
                ("_atomic_write_json", "_atomic_write_bytes", "_stage_h3_delivery_native_outputs"),
                {
                    "os": os, "json": json, "uuid": uuid, "base64": base64,
                    "hashlib": hashlib,
                    "_recovery_sha256_file": lambda path: (
                        Path(path).stat().st_size,
                        hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                    ),
                    "stamp_sidecar_policy": stamp_sidecar_policy,
                },
            )
            job = {
                "id": "job-stage", "session_id": "owner",
                "workspace": "project-a", "params": {},
                "access_policy": {"private": True, "explicit": False},
            }
            staged = namespace["_stage_h3_delivery_native_outputs"](
                job, str(project), [original], plan,
            )
            self.assertEqual(len(staged), 1)
            enriched = json.loads(native_meta.read_text(encoding="utf-8"))
            recovery = enriched["delivery_recovery"]
            self.assertEqual(
                base64.b64decode(recovery["original_sidecar_b64"]), raw_original,
            )
            self.assertEqual(recovery["owner_session_id"], "owner")
            self.assertFalse((project / "final.meta.json").exists())

    def test_delivery_intent_rejects_replaced_media_or_sidecar_without_consuming(self):
        for mutate in ("media", "sidecar"):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                original = "final.mp4"
                media = project / original
                sidecar = project / "final.meta.json"
                producer = recovery_unit_id("job-stage", "h3_concat")
                media.write_bytes(b"native-original")
                sidecar.write_text(json.dumps({
                    "job_id": "job-stage",
                    "producer_artifact_class": "final",
                    "producer_unit_id": producer,
                }), encoding="utf-8")
                source_descriptor = artifact_descriptor(
                    project,
                    basename=original,
                    sidecar_basename=sidecar.name,
                    producer_unit_id=producer,
                )
                if mutate == "media":
                    media.write_bytes(b"native-replaced")
                else:
                    sidecar.write_text(json.dumps({
                        "job_id": "job-stage", "producer_unit_id": producer,
                        "tampered": True,
                    }), encoding="utf-8")
                native_name = ".maestro-delivery-job-stage-unit-0-final.native.mp4"
                native_meta_name = ".maestro-delivery-job-stage-unit-0-final.native.meta.json"
                plan = {
                    "unit_id": recovery_unit_id("job-stage", "h3_delivery"),
                    "dependencies": [], "settings": {},
                    "staging": [{
                        "original_basename": original,
                        "native_basename": native_name,
                        "native_sidecar_basename": native_meta_name,
                        "source": source_descriptor,
                        "work_basename": ".maestro-delivery-job-stage-unit-0-final.work.mp4",
                    }],
                }
                namespace = _isolated_functions(
                    self.launch,
                    ("_atomic_write_json", "_atomic_write_bytes", "_stage_h3_delivery_native_outputs"),
                    {
                        "os": os, "json": json, "uuid": uuid, "base64": base64,
                        "hashlib": hashlib,
                        "_recovery_sha256_file": lambda path: (
                            Path(path).stat().st_size,
                            hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                        ),
                        "stamp_sidecar_policy": stamp_sidecar_policy,
                    },
                )
                job = {
                    "id": "job-stage", "session_id": "owner",
                    "workspace": "project-a", "params": {},
                    "access_policy": {"private": True, "explicit": False},
                }
                with self.assertRaises(RuntimeError):
                    namespace["_stage_h3_delivery_native_outputs"](
                        job, str(project), [original], plan,
                    )
                self.assertTrue(media.exists())
                self.assertTrue(sidecar.exists())
                self.assertFalse((project / native_name).exists())
                self.assertFalse((project / native_meta_name).exists())

    def test_delivery_intent_is_durable_before_native_rename(self):
        deliver = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_deliver_h3_outputs_transactionally"),
        )
        self.assertLess(
            deliver.index("intent_checkpoint(job, delivery_plan)"),
            deliver.index("_stage_h3_delivery_native_outputs("),
        )

    def test_h3_continuation_hash_mismatch_prevents_segment_skip(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            media = project / "segment.mp4"
            sidecar = project / "segment.meta.json"
            staging = Path(ensure_recovery_staging_directory(project))
            continuation = staging / "unit-job-h3-t0-continuation.png"
            media.write_bytes(b"segment")
            continuation.write_bytes(b"frame")
            unit_id = recovery_unit_id("job-h3", "h3_segment")
            sidecar.write_text(json.dumps({
                "producer_unit_id": unit_id,
            }), encoding="utf-8")
            artifact = artifact_descriptor(
                project,
                basename=media.name,
                sidecar_basename=sidecar.name,
                producer_unit_id=unit_id,
            )
            unit = {
                "artifacts": [artifact], "dependencies": [], "index": 0,
                "kind": "h3_segment", "state": "completed",
                "unit_id": unit_id, "variant": 0,
                "continuation": {
                    "basename": continuation.name,
                    "dependency": unit_id,
                    "mode": "last_frame",
                    "sha256": hashlib.sha256(b"frame").hexdigest(),
                    "size": len(b"frame"),
                    "storage": "recovery_staging",
                },
            }
            namespace = _isolated_functions(
                self.launch,
                (
                    "_queue_recovery_units",
                    "_queue_recovery_continuation_path",
                    "_queue_recovery_unit_matches",
                ),
                {
                    "os": os,
                    "ensure_recovery_staging_directory": (
                        ensure_recovery_staging_directory
                    ),
                    "_recovery_sha256_file": lambda path: (
                        Path(path).stat().st_size,
                        hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                    ),
                    "validate_artifact_descriptor": validate_artifact_descriptor,
                    "_quarantine_recovery_artifact": lambda *_args: None,
                    "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                },
            )
            job = {"recovery_cursor": {"completed_units": [unit]}}
            matcher = namespace["_queue_recovery_unit_matches"]
            self.assertIsNotNone(matcher(
                job, kind="h3_segment", variant=0, index=0,
                project_dir=str(project),
            ))
            public_continuation = project / "_continuation_legacy.png"
            public_continuation.write_bytes(b"frame")
            legacy = dict(unit)
            legacy["continuation"] = dict(unit["continuation"])
            legacy["continuation"]["basename"] = public_continuation.name
            legacy["continuation"].pop("storage")
            job["recovery_cursor"]["completed_units"] = [legacy]
            self.assertIsNone(matcher(
                job, kind="h3_segment", variant=0, index=0,
                project_dir=str(project),
            ))
            job["recovery_cursor"]["completed_units"] = [unit]
            continuation.write_bytes(b"changed")
            self.assertIsNone(matcher(
                job, kind="h3_segment", variant=0, index=0,
                project_dir=str(project),
            ))

    def test_h3_continuation_accepts_only_attested_current_staging_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            staging = Path(ensure_recovery_staging_directory(project))
            staged = staging / "unit-job-h3-t0-r0-w1.mp4"
            staged.write_bytes(b"video")
            namespace = _isolated_functions(
                self.launch,
                ("_queue_recovery_resolve_task_video_path",),
                {"os": os},
            )
            resolve = namespace["_queue_recovery_resolve_task_video_path"]
            self.assertEqual(
                resolve(str(staged), str(project), {staged.name: str(staged)}),
                str(staged.resolve()),
            )
            self.assertIsNone(resolve(str(staged), str(project), {}))
            public = project / "segment.mp4"
            public.write_bytes(b"public")
            self.assertEqual(
                resolve(str(public), str(project), {}), str(public.resolve()),
            )

    def test_h3_continuation_crash_is_private_and_restart_hash_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            staging = Path(ensure_recovery_staging_directory(project))
            continuation = (
                staging / "unit-job-h3-t0-continuation-ref.mp4"
            )
            continuation.write_bytes(b"temporal-tail")
            self.assertEqual(
                [
                    path.name for path in project.iterdir()
                    if path.suffix.lower() in {".png", ".mp4"}
                ],
                [],
            )
            namespace = _isolated_functions(
                self.launch,
                (
                    "_queue_recovery_continuation_descriptor",
                    "_queue_recovery_continuation_path",
                    "_apply_h3_recovered_continuation",
                ),
                {
                    "os": os,
                    "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                    "ensure_recovery_staging_directory": (
                        ensure_recovery_staging_directory
                    ),
                    "_recovery_sha256_file": recovery_sha256_file,
                    "_H3_REF2VA_HANDOFF_FRAMES": 9,
                },
            )
            unit_id = recovery_unit_id("job-h3", "h3_segment")
            descriptor = namespace[
                "_queue_recovery_continuation_descriptor"
            ](
                str(project), str(continuation),
                mode="temporal_tail", dependency=unit_id,
            )
            self.assertEqual(descriptor["storage"], "recovery_staging")
            self.assertFalse(os.path.isabs(descriptor["basename"]))
            unit = {"continuation": descriptor, "unit_id": unit_id}
            next_task = {"params": {}}
            namespace["_apply_h3_recovered_continuation"](
                unit, next_task, str(project),
            )
            self.assertEqual(
                next_task["params"]["video_guide"],
                str(continuation.resolve()),
            )
            continuation.write_bytes(b"mutated")
            size, digest = recovery_sha256_file(continuation)
            self.assertNotEqual(size, descriptor["size"])
            self.assertNotEqual(digest, descriptor["sha256"])

    def test_h3_sidecar_gap_without_predecessor_is_not_reconstructed(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            job_id = "job-sidecar-gap"
            dependency = recovery_unit_id(job_id, "h3_segment", index=0)
            staging = Path(ensure_recovery_staging_directory(project))
            continuation = (
                staging / "unit-job-sidecar-gap-t1-continuation.png"
            )
            continuation.write_bytes(b"handoff")
            dependencies = [dependency]
            unit_id = recovery_unit_id(
                job_id, "h3_segment", index=1,
                dependencies=dependencies,
            )
            media = project / "segment-1.mp4"
            sidecar = project / "segment-1.meta.json"
            media.write_bytes(b"segment-one")
            continuation_descriptor = {
                "basename": continuation.name,
                "dependency": unit_id,
                "mode": "last_frame",
                "sha256": hashlib.sha256(b"handoff").hexdigest(),
                "size": len(b"handoff"),
                "storage": "recovery_staging",
            }
            sidecar_value = {
                "job_id": job_id,
                "producer_unit_id": unit_id,
                "producer_unit_kind": "h3_segment",
                "producer_unit_variant": 0,
                "producer_unit_index": 1,
                "producer_unit_dependencies": dependencies,
                "producer_unit_settings": {},
                "producer_unit_continuation": continuation_descriptor,
                "producer_unit_artifact_names": [media.name],
                "producer_media_sha256": hashlib.sha256(b"segment-one").hexdigest(),
                "producer_media_size": len(b"segment-one"),
            }
            sidecar.write_text(json.dumps(sidecar_value), encoding="utf-8")
            fake_search = types.ModuleType("services.search_index")
            fake_search.load_media_sidecars = lambda _project: {
                media.name: sidecar_value,
            }
            namespace = _isolated_functions(
                self.launch,
                (
                    "_queue_recovery_units",
                    "_queue_recovery_continuation_path",
                    "_queue_recovery_unit_matches",
                    "_h3_dependency_closed_recovery_units",
                    "_queue_recovery_reconcile_cursor",
                ),
                {
                    "os": os, "hmac": hmac,
                    "ensure_recovery_staging_directory": (
                        ensure_recovery_staging_directory
                    ),
                    "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                    "recovery_unit_id": recovery_unit_id,
                    "_recovery_sha256_file": recovery_sha256_file,
                    "_recovery_artifact_descriptor": artifact_descriptor,
                    "validate_artifact_descriptor": validate_artifact_descriptor,
                    "_quarantine_recovery_artifact": lambda *_args: None,
                    "_queue_recovery_reconcile_orphan_delivery": lambda *_args: None,
                },
            )
            job = {"id": job_id, "recovery_cursor": {"completed_units": []}}
            with mock.patch.dict(
                sys.modules, {"services.search_index": fake_search},
            ):
                namespace["_queue_recovery_reconcile_cursor"](job, str(project))
            recovered = job["recovery_cursor"]["completed_units"]
            self.assertEqual(recovered, [])

    def test_reconcile_repairs_modern_roles_reseals_and_drops_ambiguous_repeat(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            job_id = "job-role-repair"
            specs = [
                ("segment.mp4", "h3_segment", "final", "component"),
                ("joined.mp4", "h3_concat", "component", "final"),
                ("repeat.mp4", "ordinary_repeat", None, None),
            ]
            units = []
            sidecar_values = {}
            for index, (name, kind, stale_role, _expected) in enumerate(specs):
                dependencies = []
                settings = {}
                if kind == "h3_concat":
                    dependencies = [units[0]["unit_id"]]
                    settings = {
                        "component_hashes": [hashlib.sha256(
                            b"media-h3_segment"
                        ).hexdigest()],
                        "clip_start_frames": [0],
                    }
                unit_id = recovery_unit_id(
                    job_id, kind, variant=0, index=index,
                    dependencies=dependencies, settings=settings,
                )
                payload = f"media-{kind}".encode("ascii")
                media = project / name
                media.write_bytes(payload)
                meta = {
                    "job_id": job_id,
                    "output_filename": name,
                    "producer_unit_artifact_names": [name],
                    "producer_unit_id": unit_id,
                    "producer_unit_kind": kind,
                    "producer_unit_variant": 0,
                    "producer_unit_index": index,
                    "producer_unit_dependencies": dependencies,
                    "producer_unit_settings": settings,
                    "producer_media_sha256": hashlib.sha256(payload).hexdigest(),
                    "producer_media_size": len(payload),
                }
                if stale_role is not None:
                    meta["producer_artifact_class"] = stale_role
                    meta["artifact_class"] = stale_role
                sidecar_values[name] = meta
                sidecar = project / f"{Path(name).stem}.meta.json"
                sidecar.write_text(json.dumps(meta), encoding="utf-8")
                descriptor = artifact_descriptor(
                    project,
                    basename=name,
                    sidecar_basename=sidecar.name,
                    producer_unit_id=unit_id,
                )
                units.append({
                    "artifacts": [descriptor],
                    "dependencies": dependencies,
                    "index": index,
                    "kind": kind,
                    "state": "completed",
                    "unit_id": unit_id,
                    "variant": 0,
                })
                if settings:
                    units[-1]["settings"] = settings

            def atomic_write(path, value):
                Path(path).write_text(json.dumps(value), encoding="utf-8")

            fake_search = types.ModuleType("services.search_index")
            fake_search.load_media_sidecars = lambda _project: sidecar_values
            namespace = _isolated_functions(
                self.launch,
                (
                    "_queue_recovery_units",
                    "_queue_recovery_unit_matches",
                    "_queue_recovery_expected_artifact_role",
                    "_queue_recovery_repair_unit_roles",
                    "_h3_dependency_closed_recovery_units",
                    "_queue_recovery_reconcile_cursor",
                ),
                {
                    "os": os, "json": json, "hmac": hmac,
                    "_RECOVERY_UNIT_FIXED_ARTIFACT_ROLES": {
                        "h3_segment": "component",
                        "h3_concat": "final",
                        "h3_delivery": "final",
                    },
                    "_RECOVERY_ARTIFACT_ROLES": {
                        "final", "component", "window", "temporary",
                    },
                    "_atomic_write_json": atomic_write,
                    "ensure_recovery_staging_directory": (
                        ensure_recovery_staging_directory
                    ),
                    "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                    "recovery_unit_id": recovery_unit_id,
                    "_recovery_sha256_file": recovery_sha256_file,
                    "_recovery_artifact_descriptor": artifact_descriptor,
                    "validate_artifact_descriptor": validate_artifact_descriptor,
                    "_quarantine_recovery_artifact": lambda *_args: None,
                    "_queue_recovery_reconcile_orphan_delivery": lambda *_args: None,
                },
            )
            job = {
                "id": job_id,
                "recovery_cursor": {"completed_units": units},
                "output_files": [
                    "segment.mp4", "joined.mp4", "repeat.mp4",
                ],
            }
            with mock.patch.dict(
                sys.modules, {"services.search_index": fake_search},
            ):
                namespace["_queue_recovery_reconcile_cursor"](
                    job, str(project),
                )

            repaired_units = job["recovery_cursor"]["completed_units"]
            self.assertEqual(
                {unit["kind"] for unit in repaired_units},
                {"h3_segment", "h3_concat"},
            )
            self.assertEqual(job["output_files"], ["joined.mp4"])
            self.assertEqual(job["join_output_file"], "joined.mp4")
            self.assertEqual(set(job["artifact_files"]), {
                "segment.mp4", "joined.mp4", "repeat.mp4",
            })
            for name, kind, _stale, expected in specs[:2]:
                meta = json.loads(
                    (project / f"{Path(name).stem}.meta.json").read_text(
                        encoding="utf-8",
                    )
                )
                self.assertEqual(meta["artifact_class"], expected)
                self.assertEqual(meta["producer_artifact_class"], expected)
                self.assertEqual(
                    meta["artifact_lineage"],
                    f"h3:{job_id}:variant:0",
                )
                unit = next(
                    value for value in repaired_units
                    if value["kind"] == kind
                )
                self.assertTrue(validate_artifact_descriptor(
                    project, unit["artifacts"][0],
                    producer_unit_id=unit["unit_id"],
                ))
            ambiguous = json.loads(
                (project / "repeat.meta.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("artifact_class", ambiguous)
            self.assertNotIn("producer_artifact_class", ambiguous)

            (project / "joined.mp4").unlink()
            (project / "joined.meta.json").unlink()
            (project / "repeat.mp4").unlink()
            (project / "repeat.meta.json").unlink()
            fake_search.load_media_sidecars = lambda _project: {
                "segment.mp4": json.loads(
                    (project / "segment.meta.json").read_text(encoding="utf-8")
                ),
            }
            segment_unit = next(
                unit for unit in repaired_units
                if unit["kind"] == "h3_segment"
            )
            failed_job = {
                "id": job_id,
                "recovery_cursor": {"completed_units": [segment_unit]},
                "output_files": ["segment.mp4"],
                "join_output_file": "segment.mp4",
            }
            with mock.patch.dict(
                sys.modules, {"services.search_index": fake_search},
            ):
                namespace["_queue_recovery_reconcile_cursor"](
                    failed_job, str(project),
                )
            self.assertEqual(failed_job["output_files"], [])
            self.assertEqual(failed_job["artifact_files"], ["segment.mp4"])
            self.assertNotIn("join_output_file", failed_job)

    def test_partial_multi_artifact_promotion_never_completes_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            job_id = "job-multi-gap"
            names = ["unit-job-multi-gap-r0-w1.mp4", "unit-job-multi-gap-r0-w2.mp4"]
            unit_id = recovery_unit_id(
                job_id, "ordinary_repeat", index=0,
            )
            values = {}
            for index, name in enumerate(names):
                payload = f"media-{index}".encode("ascii")
                meta = {
                    "job_id": job_id,
                    "producer_unit_artifact_names": list(names),
                    "producer_unit_id": unit_id,
                    "producer_unit_kind": "ordinary_repeat",
                    "producer_unit_variant": 0,
                    "producer_unit_index": 0,
                    "producer_unit_dependencies": [],
                    "producer_unit_settings": {},
                    "producer_media_sha256": hashlib.sha256(payload).hexdigest(),
                    "producer_media_size": len(payload),
                }
                values[name] = meta
                (project / f"{Path(name).stem}.meta.json").write_text(
                    json.dumps(meta), encoding="utf-8",
                )
                if index == 0:
                    (project / name).write_bytes(payload)
            fake_search = types.ModuleType("services.search_index")
            fake_search.load_media_sidecars = lambda _project: values
            namespace = _isolated_functions(
                self.launch,
                (
                    "_queue_recovery_units",
                    "_queue_recovery_continuation_path",
                    "_queue_recovery_unit_matches",
                    "_h3_dependency_closed_recovery_units",
                    "_queue_recovery_reconcile_cursor",
                ),
                {
                    "os": os, "hmac": hmac,
                    "ensure_recovery_staging_directory": (
                        ensure_recovery_staging_directory
                    ),
                    "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                    "recovery_unit_id": recovery_unit_id,
                    "_recovery_sha256_file": recovery_sha256_file,
                    "_recovery_artifact_descriptor": artifact_descriptor,
                    "validate_artifact_descriptor": validate_artifact_descriptor,
                    "_quarantine_recovery_artifact": lambda *_args: None,
                    "_queue_recovery_reconcile_orphan_delivery": lambda *_args: None,
                },
            )
            job = {"id": job_id, "recovery_cursor": {"completed_units": []}}
            with mock.patch.dict(
                sys.modules, {"services.search_index": fake_search},
            ):
                namespace["_queue_recovery_reconcile_cursor"](job, str(project))
            self.assertEqual(job["recovery_cursor"]["completed_units"], [])
            self.assertEqual(job["recovery_cursor"]["ordinary_repeat_offset"], 0)

            (project / names[1]).write_bytes(b"media-1")
            with mock.patch.dict(
                sys.modules, {"services.search_index": fake_search},
            ):
                namespace["_queue_recovery_reconcile_cursor"](job, str(project))
            recovered = job["recovery_cursor"]["completed_units"]
            self.assertEqual(len(recovered), 1)
            self.assertEqual(
                {item["basename"] for item in recovered[0]["artifacts"]},
                set(names),
            )
            self.assertEqual(job["recovery_cursor"]["ordinary_repeat_offset"], 1)

    def test_startup_thread_failure_blocks_one_job_and_starts_the_next(self):
        class Registry(dict):
            def prepare(self, job):
                return job

            def publish_prepared(self, job_id, job):
                self[job_id] = job

        class FakeThread:
            created = []

            def __init__(self, *, target, args, daemon, name):
                self.args = args
                self.name = name
                self.started = False
                FakeThread.created.append(self)

            def start(self):
                if len(FakeThread.created) == 1:
                    raise RuntimeError("injected start failure")
                self.started = True

        snapshots = {
            "first": {"id": "first", "status": "queued", "workspace": "default"},
            "second": {"id": "second", "status": "queued", "workspace": "default"},
        }
        registry = Registry()

        def checkpoint(job, **updates):
            job.update(updates)

        namespace = _isolated_functions(
            self.launch,
            ("_restore_queue_recovery_on_startup",),
            {
                "_queue_recovery_workers_started": False,
                "_queue_recovery_existing_projects": lambda: {},
                "_queue_recovery_restored": types.SimpleNamespace(
                    jobs=snapshots,
                    global_state={},
                ),
                "_queue_recovery_materialize_job": lambda snapshot, _projects: (
                    {
                        "id": snapshot["id"], "status": "queued",
                        "workspace": "default", "queue_held": False,
                        "recovery_attempt": 1, "recovery_state": "restored",
                        "reruns_denoise": True, "message": "Queued",
                    },
                    True,
                ),
                "_queue_recovery_checkpoint": checkpoint,
                "_jobs": registry,
                "restore_scheduler_state": lambda *_args: None,
                "cleanup_orphan_request_manifests": lambda *_args: 0,
                "cleanup_orphan_staged_outputs": lambda *_args: 0,
                "_queue_recovery_coordinator": types.SimpleNamespace(
                    compact=lambda: None,
                ),
                "threading": types.SimpleNamespace(Thread=FakeThread),
                "_queue_recovery_worker": lambda _job: (
                    lambda *_args: None
                ),
                "_queue_recovery_delivery_pending": lambda _job: None,
                "_require_job_runtime_model_admission": lambda _job: None,
            },
        )
        namespace["_restore_queue_recovery_on_startup"]()
        self.assertEqual(registry["first"]["recovery_state"], "blocked")
        self.assertTrue(registry["first"]["queue_held"])
        self.assertTrue(FakeThread.created[1].started)

    def test_sample_pair_owner_gate_precedes_private_body_parsing_and_submission(self):
        submit = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "submit_sample_campaign_pair"),
        )
        owner = submit.index("_require_sample_campaign_owner(request)")
        body = submit.index("body = await request.json()")
        project = submit.index("_require_project_access(", body)
        prepare = submit.index("_prepare_sample_campaign_arm(", project)
        atomic = submit.index("HeldSamplePairCoordinator(", prepare)
        result = submit.index("coordinator.submit(", atomic)
        self.assertLess(owner, body)
        self.assertLess(body, project)
        self.assertLess(project, prepare)
        self.assertLess(prepare, atomic)
        self.assertLess(atomic, result)
        self.assertIn('permission="project.generate"', submit)
        self.assertNotIn("thread.start", submit)
        self.assertNotIn("_run_generation(", submit)

        prepare_arm = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_prepare_sample_campaign_arm"),
        )
        authorize = prepare_arm.index("_authorize_generation_media_inputs(")
        descriptors = prepare_arm.index(
            "_queue_recovery_input_descriptors(job, owner_digest)", authorize,
        )
        manifest_check = prepare_arm.index(
            "validate_private_arm_request(", descriptors,
        )
        self.assertLess(authorize, descriptors)
        self.assertLess(descriptors, manifest_check)
        self.assertNotIn("_recovery_sha256_file", prepare_arm)
        self.assertNotIn("_sample_campaign_private_inputs", self.launch_source)

    def test_sample_pair_route_is_direct_loopback_only_before_session_and_body(self):
        middleware = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_maestro_session_middleware"),
        )
        route_gate = middleware.index('request.url.path == "/api/v1/sample-campaign"')
        session = middleware.index("_attach_account_request_state(")
        self.assertLess(route_gate, session)
        self.assertIn("_sample_campaign_control_denial(request)", middleware[route_gate:session])

    def test_sample_pair_owner_gate_denies_lan_nonowner_and_stale_reauth(self):
        class Denied(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        namespace = _isolated_functions(
            self.launch,
            ("_require_sample_campaign_owner",),
            {
                "Request": object,
                "HTTPException": Denied,
                "_runtime_share_registration_is_local": (
                    lambda request: request.local
                ),
                "_request_is_cloudflare_remote": (
                    lambda request: request.remote
                ),
                "_request_has_account_capability": (
                    lambda request, capability: capability == "owner.admin"
                    and request.owner
                ),
                "_request_has_recent_account_reauth": (
                    lambda request: request.recent
                ),
            },
        )
        require = namespace["_require_sample_campaign_owner"]
        allowed = types.SimpleNamespace(
            local=True, remote=False, owner=True, recent=True,
        )
        require(allowed)
        for change in (
            {"local": False},
            {"remote": True},
            {"owner": False},
            {"recent": False},
        ):
            with self.subTest(change=change), self.assertRaises(Denied) as raised:
                require(types.SimpleNamespace(**{**vars(allowed), **change}))
            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(
                raised.exception.detail,
                "Sample campaign controls are unavailable",
            )

    def test_sample_pair_registry_batch_validates_every_job_before_visibility(self):
        registry_node = next(
            node for node in self.launch.body
            if isinstance(node, ast.ClassDef) and node.name == "_JobRegistry"
        )
        module = ast.Module(body=[registry_node], type_ignores=[])
        ast.fix_missing_locations(module)
        observed = []

        def admission(job):
            observed.append(job["id"])
            if job["id"] == "second":
                raise RuntimeError("injected second admission failure")

        namespace = {
            "threading": threading,
            "_workspace_lifecycle_lock": threading.RLock(),
            "_require_job_workspace_available": admission,
            "QueueRecoveryRuntimeError": RuntimeError,
            "_request_remote": types.SimpleNamespace(get=lambda: False),
            "HTTPException": RuntimeError,
            "output_policy_from_request": lambda *_args, **_kwargs: {},
        }
        exec(compile(module, "sample-registry", "exec"), namespace)
        registry = namespace["_JobRegistry"]()
        with self.assertRaisesRegex(RuntimeError, "second admission"):
            registry.publish_prepared_many((
                ("first", {"id": "first"}),
                ("second", {"id": "second"}),
            ))
        self.assertEqual(observed, ["first", "second"])
        self.assertEqual(registry, {})

    def test_sample_pair_ref2va_revision_comes_from_server_pinned_url(self):
        class Invalid(ValueError):
            pass

        model_def = {
            "URLs": [
                "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/"
                "eb8a16107c595128b3a578f82d2ce2f75920c355/"
                "diffusion_models/ref2va.safetensors"
            ],
        }
        namespace = _isolated_functions(
            self.launch,
            ("_sample_campaign_model_revision",),
            {
                "wgp": types.SimpleNamespace(
                    get_model_def=lambda model_type: (
                        model_def if model_type == "minimax_h3_ref2va" else {}
                    ),
                ),
                "Mapping": dict,
                "re": re,
                "urlsplit": __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit,
                "SampleCampaignSubmissionError": Invalid,
            },
        )
        resolve = namespace["_sample_campaign_model_revision"]
        self.assertEqual(
            resolve("minimax_h3_ref2va"),
            "eb8a16107c595128b3a578f82d2ce2f75920c355",
        )
        with self.assertRaises(Invalid):
            resolve("minimax_h3")

    def test_sample_pair_recovery_groups_require_exact_reciprocal_peers(self):
        namespace = _isolated_functions(
            self.launch,
            ("_valid_sample_campaign_recovery_groups",),
            {
                "Sequence": list,
                "Mapping": dict,
                "Any": object,
                "_SAMPLE_CAMPAIGN_QUEUE_CLASS": "background_sample",
                "_SAMPLE_CAMPAIGN_QUEUE_PRIORITY": -1000,
            },
        )
        validate = namespace["_valid_sample_campaign_recovery_groups"]
        digest = "d" * 64

        def snapshot(job_id, arm, peer):
            return {
                "id": job_id,
                "kind": "sample_campaign_generation",
                "status": "queued",
                "workspace": "default",
                "owner_principal": "owner",
                "project_instance": "project",
                "queue_class": "background_sample",
                "queue_priority": -1000,
                "queue_held": True,
                "recovery_cursor": {"sample_campaign": {
                    "schema": 1,
                    "pair_id": "pair-1",
                    "pair_manifest_digest": digest,
                    "arm": arm,
                    "peer_job_id": peer,
                }},
            }

        maestro = snapshot("maestro-job", "maestro", "control-job")
        control = snapshot("control-job", "control", "maestro-job")
        self.assertEqual(validate((maestro, control)), ((maestro, control),))
        self.assertEqual(validate((maestro,)), ())
        self.assertEqual(
            validate((maestro, {**control, "owner_principal": "other"})), (),
        )
        nonreciprocal = copy.deepcopy(control)
        nonreciprocal["recovery_cursor"]["sample_campaign"]["peer_job_id"] = "missing"
        self.assertEqual(validate((maestro, nonreciprocal)), ())
        duplicate_arm = snapshot("control-job", "maestro", "maestro-job")
        self.assertEqual(validate((maestro, duplicate_arm)), ())

        startup = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_restore_queue_recovery_on_startup"),
        )
        self.assertIn("_valid_sample_campaign_recovery_groups", startup)
        self.assertIn("_jobs.publish_prepared_many", startup)
        self.assertIn('if snapshot.get("kind") == "sample_campaign_generation"', startup)

    def test_sample_pair_generic_controls_cannot_release_held_jobs(self):
        class Denied(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        namespace = _isolated_functions(
            self.launch,
            ("_reject_generic_sample_campaign_release",),
            {"Mapping": dict, "Any": object, "HTTPException": Denied},
        )
        reject = namespace["_reject_generic_sample_campaign_release"]
        reject({"kind": "studio_generation"})
        with self.assertRaises(Denied) as raised:
            reject({"kind": "sample_campaign_generation"})
        self.assertEqual(raised.exception.status_code, 409)
        for name in (
            "set_job_queue_priority", "hold_queued_job", "resume_held_job",
            "start_queued_job_next", "set_job_output_count",
        ):
            source = ast.get_source_segment(
                self.launch_source, _function(self.launch, name),
            )
            self.assertIn("_reject_generic_sample_campaign_release(job)", source)
        cancel = ast.get_source_segment(
            self.launch_source, _function(self.launch, "cancel_job"),
        )
        self.assertLess(
            cancel.index('job.get("kind") == "sample_campaign_generation"'),
            cancel.index("request_cancel("),
        )
        cancel_namespace = _isolated_functions(
            self.launch,
            ("cancel_job",),
            {
                "api": types.SimpleNamespace(
                    post=lambda *_args, **_kwargs: lambda function: function,
                ),
                "Request": object,
                "Response": object,
                "HTTPException": Denied,
                "_jobs": {
                    "sample": {"kind": "sample_campaign_generation"},
                },
                "_set_recovery_no_store": lambda _response: None,
                "_job_owned_by_request": lambda *_args: True,
                "request_cancel": lambda *_args, **_kwargs: self.fail(
                    "generic cancellation reached the sample job"
                ),
                "_active_gen_states": {},
            },
        )
        with self.assertRaises(Denied) as cancelled:
            cancel_namespace["cancel_job"]("sample", object(), object())
        self.assertEqual(cancelled.exception.status_code, 409)
        resume = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_resume_recovered_job"),
        )
        self.assertIn('job.get("kind") == "sample_campaign_generation"', resume)

    def test_sample_pair_recovery_linkage_tamper_fails_closed(self):
        arm = types.SimpleNamespace(
            private_input_paths=("/private/input.png",),
            input_fingerprints=("a" * 64,),
        )
        pair = types.SimpleNamespace(
            pair_id="pair-1", maestro=arm, control=arm,
        )
        namespace = _isolated_functions(
            self.launch,
            ("_validate_sample_campaign_recovery_linkage",),
            {
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "_SAMPLE_CAMPAIGN_LINKAGE_SCHEMA": 1,
                "_SAMPLE_CAMPAIGN_QUEUE_CLASS": "background_sample",
                "_SAMPLE_CAMPAIGN_QUEUE_PRIORITY": -1000,
                "Mapping": dict,
                "parse_private_pair_manifest": lambda _value: pair,
                "pair_manifest_digest": lambda _pair: "b" * 64,
                "SampleCampaignSubmissionError": ValueError,
            },
        )
        validate = namespace["_validate_sample_campaign_recovery_linkage"]
        linkage = {
            "schema": 1,
            "pair_id": "pair-1",
            "pair_manifest_digest": "b" * 64,
            "arm": "maestro",
            "peer_job_id": "control-job",
        }
        snapshot = {
            "id": "maestro-job",
            "kind": "sample_campaign_generation",
            "status": "queued",
            "queue_class": "background_sample",
            "queue_priority": -1000,
            "queue_held": True,
            "recovery_cursor": {"sample_campaign": copy.deepcopy(linkage)},
        }
        private = {
            "pair_manifest": {"private": True},
            "pair_manifest_digest": "b" * 64,
            "linkage": copy.deepcopy(linkage),
        }
        params = {"prompt": "private", "_sample_campaign_private": private}
        inputs = ({"path": "/private/input.png", "sha256": "a" * 64},)
        self.assertEqual(validate(snapshot, params, inputs), {"prompt": "private"})
        for tampered_snapshot, tampered_params in (
            ({**snapshot, "queue_class": "user"}, params),
            (snapshot, {**params, "_sample_campaign_private": {
                **private, "pair_manifest_digest": "c" * 64,
            }}),
            (snapshot, {}),
        ):
            with self.assertRaises(QueueRecoveryRuntimeError):
                validate(tampered_snapshot, tampered_params, inputs)

        materialize = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_queue_recovery_materialize_job"),
        )
        loaded = materialize.index('manifest_params = dict(manifest["params"])')
        linkage_check = materialize.index(
            "manifest_params = sample_linkage_validator(", loaded,
        )
        stripped = materialize.index('runtime["params"] = manifest_params', linkage_check)
        unblocked = materialize.index('blocked_reason = ""', linkage_check)
        self.assertLess(loaded, linkage_check)
        self.assertLess(linkage_check, stripped)
        self.assertLess(linkage_check, unblocked)

    def test_sample_release_owner_gate_and_post_slot_guard_precede_start(self):
        release = ast.get_source_segment(
            self.launch_source, _function(self.launch, "release_sample_campaign_arm"),
        )
        self.assertLess(
            release.index("_require_sample_campaign_owner(request)"),
            release.index("body = await request.json()"),
        )
        self.assertIn("SampleCampaignReleaseCoordinator(", release)
        self.assertIn("coordinator.release_one(", release)
        self.assertIn("with _sample_campaign_release_lock:", release)

        runner = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        slot = runner.index("with generation_slot(")
        guard = runner.index("admission_guard(job)", slot)
        start = runner.index("if not try_start(", guard)
        marker = runner.index("on_started()", start)
        self.assertLess(slot, guard)
        self.assertLess(guard, start)
        self.assertLess(start, marker)
        dedicated = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_run_sample_campaign_generation"),
        )
        self.assertIn(
            "admission_guard=_sample_campaign_post_slot_guard", dedicated,
        )

    def test_sample_post_slot_failure_reasserts_hold_without_model_work(self):
        namespace = _isolated_functions(
            self.launch,
            ("_sample_campaign_post_slot_guard",),
            {
                "Mapping": dict,
                "Any": object,
                "_sample_campaign_validate_live_request": lambda _job: True,
                "_sample_campaign_release_readiness": (
                    lambda _job: (False, "ordinary_work_waiting")
                ),
                "_sample_campaign_persist_hold": (
                    lambda job, held, state: job.update({
                        "queue_held": held, "recovery_state": state,
                    }) is None
                ),
                "_sample_campaign_force_hold": lambda job: job.update(
                    queue_held=True,
                ),
                "_sample_campaign_retry_sampler_lock": threading.Lock(),
                "_sample_campaign_retry_ready_identity": None,
                "_sample_campaign_reset_idle_gate": lambda: None,
            },
        )
        job = {"queue_held": False}
        self.assertFalse(namespace["_sample_campaign_post_slot_guard"](job))
        self.assertTrue(job["queue_held"])
        self.assertEqual(job["recovery_state"], "sample_campaign_held")

    def test_sample_completed_first_arm_restores_without_duplicate_dispatch(self):
        namespace = _isolated_functions(
            self.launch,
            ("_valid_sample_campaign_recovery_groups",),
            {
                "Sequence": list,
                "Mapping": dict,
                "Any": object,
                "_SAMPLE_CAMPAIGN_QUEUE_CLASS": "background_sample",
                "_SAMPLE_CAMPAIGN_QUEUE_PRIORITY": -1000,
            },
        )
        digest = "d" * 64

        def snapshot(job_id, arm, peer, status, held):
            return {
                "id": job_id,
                "kind": "sample_campaign_generation",
                "status": status,
                "workspace": "default",
                "owner_principal": "owner",
                "project_instance": "project",
                "queue_class": "background_sample",
                "queue_priority": -1000,
                "queue_held": held,
                "recovery_cursor": {"sample_campaign": {
                    "schema": 1,
                    "pair_id": "pair-1",
                    "pair_manifest_digest": digest,
                    "arm": arm,
                    "peer_job_id": peer,
                }},
            }

        maestro = snapshot(
            "maestro-job", "maestro", "control-job", "completed", False,
        )
        control = snapshot(
            "control-job", "control", "maestro-job", "queued", True,
        )
        validate = namespace["_valid_sample_campaign_recovery_groups"]
        self.assertEqual(validate((maestro, control)), ((maestro, control),))
        interrupted_maestro = snapshot(
            "maestro-job", "maestro", "control-job", "running", False,
        )
        initial_control = snapshot(
            "control-job", "control", "maestro-job", "queued", True,
        )
        self.assertEqual(
            validate((interrupted_maestro, initial_control)),
            ((interrupted_maestro, initial_control),),
        )
        released_control = snapshot(
            "control-job", "control", "maestro-job", "queued", False,
        )
        self.assertEqual(
            validate((maestro, released_control)),
            ((maestro, released_control),),
        )

        materialize = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_queue_recovery_materialize_job"),
        )
        sample_branch = materialize.index(
            'if snapshot.get("kind") == "sample_campaign_generation":',
        )
        completed = materialize.index('if status == "completed"', sample_branch)
        held = materialize.index('"status": "queued"', completed)
        self.assertLess(completed, held)
        self.assertIn('"recovery_state": "terminal"', materialize[completed:held])

    def test_sample_release_keeps_private_envelope_out_of_engine_params(self):
        validate = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_sample_campaign_validate_live_request"),
        )
        self.assertIn("load_request_manifest(", validate)
        self.assertIn("validate_manifest_inputs(", validate)
        self.assertIn("_validate_sample_campaign_recovery_linkage(", validate)
        self.assertIn("return clean == dict(job.get(\"params\") or {})", validate)
        worker = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_run_sample_campaign_generation"),
        )
        self.assertNotIn("load_request_manifest", worker)
        self.assertNotIn("_sample_campaign_private", worker)

    def test_sample_pair_id_is_immutable_and_exact_retry_is_idempotent(self):
        class Conflict(RuntimeError):
            pass

        class Arm:
            MAESTRO = "maestro-enum"
            CONTROL = "control-enum"

        pair = types.SimpleNamespace(pair_id="pair-1")
        digest = "a" * 64

        def job(arm, job_id, pair_digest=digest):
            peer = "control-id" if arm == "maestro" else "maestro-id"
            return {
                "id": job_id,
                "kind": "sample_campaign_generation",
                "status": "queued",
                "workspace": "default",
                "recovery_cursor": {"sample_campaign": {
                    "pair_id": "pair-1",
                    "pair_manifest_digest": pair_digest,
                    "arm": arm,
                    "peer_job_id": peer,
                }},
            }

        jobs = {
            "maestro-id": job("maestro", "maestro-id"),
            "control-id": job("control", "control-id"),
        }
        namespace = _isolated_functions(
            self.launch,
            ("_existing_sample_campaign_pair",),
            {
                "Mapping": dict,
                "Any": object,
                "_jobs": jobs,
                "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                "_SampleCampaignPairConflict": Conflict,
                "CampaignArm": Arm,
                "sample_arm_job_id": lambda value, arm: {
                    (digest, Arm.MAESTRO): "maestro-id",
                    (digest, Arm.CONTROL): "control-id",
                }.get((value, arm), "different-id"),
            },
        )
        existing = namespace["_existing_sample_campaign_pair"]
        result = existing(pair, digest, workspace="default")
        self.assertEqual(result["status"], "held")
        self.assertEqual(len(result["arms"]), 2)
        with self.assertRaises(Conflict):
            existing(pair, "b" * 64, workspace="default")

        submit = ast.get_source_segment(
            self.launch_source, _function(self.launch, "submit_sample_campaign_pair"),
        )
        lock = submit.index("with _sample_campaign_release_lock:")
        lookup = submit.index("_existing_sample_campaign_pair(", lock)
        prepare = submit.index("_prepare_sample_campaign_arm(", lookup)
        self.assertLess(lock, lookup)
        self.assertLess(lookup, prepare)

    def test_sample_worker_reholds_only_not_started_queued_exit(self):
        def run_case(job, runner):
            persisted = []
            namespace = _isolated_functions(
                self.launch,
                ("_run_sample_campaign_generation",),
                {
                    "_jobs": {"sample": job},
                    "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                    "_SAMPLE_CAMPAIGN_QUEUE_CLASS": "background_sample",
                    "_sample_campaign_post_slot_guard": lambda _job: True,
                    "_sample_campaign_execution_attempt": (
                        contextvars.ContextVar(
                            "test_sample_worker_attempt", default=None,
                        )
                    ),
                    "_run_generation": runner,
                    "_sample_campaign_persist_hold": (
                        lambda current, held, state: persisted.append(
                            (current["id"], held, state)
                        ) or current.update(queue_held=held) is None
                    ),
                    "_sample_campaign_force_hold": lambda current: current.update(
                        queue_held=True,
                    ),
                },
            )
            try:
                namespace["_run_sample_campaign_generation"]("sample")
            except RuntimeError:
                pass
            return persisted

        base = {
            "id": "sample",
            "kind": "sample_campaign_generation",
            "queue_class": "background_sample",
            "status": "queued",
            "queue_held": False,
            "execution_attempt": 1,
        }
        pending = {**base, "started_at": 1.0}
        self.assertEqual(
            run_case(pending, lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("pre-start failure")
            )),
            [("sample", True, "sample_campaign_held")],
        )
        self.assertTrue(pending["queue_held"])
        started = dict(base)

        def reached_start(*_args, on_started, **_kwargs):
            on_started()
            return False

        self.assertEqual(run_case(started, reached_start), [])
        completed = {**base, "status": "completed"}
        self.assertEqual(run_case(completed, lambda *_args, **_kwargs: True), [])

    def test_sample_release_revalidates_both_arms(self):
        observed = []
        namespace = _isolated_functions(
            self.launch,
            ("_sample_campaign_validate_live_pair",),
            {
                "Mapping": dict,
                "Any": object,
                "_sample_campaign_validate_live_request": (
                    lambda job: observed.append(job["id"]) or job["valid"]
                ),
            },
        )
        validate = namespace["_sample_campaign_validate_live_pair"]
        self.assertFalse(validate(
            {"id": "maestro", "valid": False},
            {"id": "control", "valid": False},
        ))
        self.assertEqual(observed, ["maestro", "control"])

    def test_sample_preemption_watcher_starts_after_recovery_and_stops(self):
        source = self.launch_source
        recovery = source.index(
            "def _start_queue_recovery_before_background_workers"
        )
        watcher = source.index(
            "def _start_sample_campaign_preemption_after_recovery"
        )
        updater = source.index("def _start_versioned_model_updates")
        self.assertLess(recovery, watcher)
        self.assertLess(watcher, updater)
        watcher_source = ast.get_source_segment(
            source,
            _function(self.launch, "_sample_campaign_preemption_loop"),
        )
        self.assertIn("SampleCampaignPreemptionCoordinator(", watcher_source)
        self.assertIn("capture_foreign_gpu_significance", watcher_source)
        self.assertIn("_sample_campaign_preemption_stop", watcher_source)
        stop_source = ast.get_source_segment(
            source,
            _function(self.launch, "_stop_sample_campaign_preemption_watcher"),
        )
        self.assertIn(".set()", stop_source)

    def test_sample_release_enforces_retry_time_and_attempt_cas(self):
        checkpoints = []
        namespace = _isolated_functions(
            self.launch,
            ("_sample_campaign_persist_hold",),
            {
                "Mapping": dict,
                "math": __import__("math"),
                "time": types.SimpleNamespace(time=lambda: 100.0),
                "_SAMPLE_CAMPAIGN_QUEUE_CLASS": "background_sample",
                "_SAMPLE_CAMPAIGN_QUEUE_PRIORITY": -1000,
                "SAMPLE_RETRY_MAX_ATTEMPT": 1_000_000,
                "_queue_recovery_checkpoint": (
                    lambda job, **updates: checkpoints.append(
                        (job, updates)
                    ) or True
                ),
            },
        )
        persist = namespace["_sample_campaign_persist_hold"]
        job = {
            "execution_attempt": 7,
            "sample_retry": {"attempt": 2, "not_before": 101.0},
        }
        self.assertFalse(persist(job, False, "sample_campaign_released"))
        self.assertEqual(checkpoints, [])
        job["sample_retry"]["not_before"] = 100.0
        self.assertTrue(persist(job, False, "sample_campaign_released"))
        self.assertEqual(
            checkpoints[0][1]["expected_execution_attempt"], 7,
        )
        self.assertFalse(persist(
            {**job, "execution_attempt": True},
            False,
            "sample_campaign_released",
        ))

    def test_sample_worker_carries_one_attempt_through_all_publishers(self):
        worker = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        dedicated = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_run_sample_campaign_generation"),
        )
        checkpoint = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_queue_recovery_checkpoint"),
        )
        self.assertIn("expected_execution_attempt=execution_attempt", dedicated)
        self.assertIn("_sample_campaign_execution_attempt.set", dedicated)
        self.assertIn("_sample_campaign_execution_attempt.reset", dedicated)
        for publisher in (
            "def update_job(",
            "def record_job_outputs(",
            "def finish_job(",
            "def register_abort_state(",
        ):
            self.assertIn(publisher, worker)
        self.assertIn("settle_sample_preemption(", worker)
        self.assertLess(
            worker.rindex("settle_sample_preemption("),
            worker.rindex("unregister_abort_state("),
        )
        self.assertIn("context_attempt", checkpoint)
        self.assertIn("expected_execution_attempt", checkpoint)

    def test_preemption_path_has_no_foreign_process_control(self):
        service = (ROOT / "app/services/sample_campaign_preemption.py").read_text(
            encoding="utf-8",
        )
        for forbidden in (
            "os.kill", "signal.", "terminate(", "kill(", "process_name",
            "import os", "import signal", "import subprocess", "import psutil",
        ):
            self.assertNotIn(forbidden, service)

    def test_urgent_work_projection_never_mutates_user_job(self):
        namespace = _isolated_functions(
            self.launch,
            ("_sample_campaign_urgent_ordinary_work_present",),
            {
                "Mapping": dict,
                "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                "RESOURCE_INTENT_GENERATION": "generation",
                "RESOURCE_EXECUTION_STANDARD": "standard",
                "_jobs": {},
            },
        )
        user = {
            "id": "user",
            "kind": "studio_generation",
            "status": "queued",
            "queue_held": False,
            "queue_class": "user",
            "resource_intent": "generation",
            "resource_execution": "standard",
        }
        namespace["_jobs"]["user"] = user
        before = copy.deepcopy(user)
        self.assertTrue(
            namespace["_sample_campaign_urgent_ordinary_work_present"]()
        )
        self.assertEqual(user, before)

    def test_due_retry_sampler_uses_bounded_two_second_window_then_release(self):
        waits = []
        releases = []
        resets = []
        identity = (
            90.0, "pair", "sample", 4, 2, "a" * 64, "maestro", "peer",
        )

        class _Stop:
            def is_set(self):
                return False

            def wait(self, delay):
                waits.append(delay)
                return False

        class _Coordinator:
            def __init__(self, **_kwargs):
                pass

            def release_one(self, pair_id, jobs):
                releases.append((pair_id, tuple(jobs)))
                return types.SimpleNamespace(status="released")

        readiness = iter((
            (False, "idle_window_pending"),
            (False, "idle_window_pending"),
            (False, "idle_window_pending"),
            (False, "idle_window_pending"),
            (True, "ready"),
        ))
        namespace = _isolated_functions(
            self.launch,
            ("_sample_campaign_run_due_retry_sampler",),
            {
                "_sample_campaign_retry_sampler_identity": identity,
                "_sample_campaign_retry_ready_identity": None,
                "_sample_campaign_retry_sampler_thread": None,
                "threading": threading,
                "_sample_campaign_reset_idle_gate": lambda: resets.append(True),
                "_sample_campaign_preemption_stop": _Stop(),
                "_sample_campaign_urgent_ordinary_work_present": lambda: False,
                "_sample_campaign_retry_identity_current": lambda value: value == identity,
                "capture_foreign_gpu_significance": lambda: types.SimpleNamespace(
                    known=True, significant=False,
                ),
                "_jobs": {"sample": {"id": "sample"}},
                "Mapping": dict,
                "_sample_campaign_release_readiness": lambda _job: next(readiness),
                "_SAMPLE_CAMPAIGN_RETRY_SNAPSHOTS": 5,
                "_SAMPLE_CAMPAIGN_RETRY_SNAPSHOT_GAP_SECONDS": 2.0,
                "_sample_campaign_release_lock": threading.Lock(),
                "SampleCampaignReleaseCoordinator": _Coordinator,
                "_sample_campaign_ordinary_work_present": lambda: False,
                "_sample_campaign_other_arm_active": lambda _pair: False,
                "_sample_campaign_validate_live_pair": lambda *_args: True,
                "_sample_campaign_persist_hold": lambda *_args: True,
                "_sample_campaign_force_hold": lambda *_args: None,
                "_start_sample_campaign_worker": lambda *_args: None,
                "_sample_campaign_retry_sampler_lock": threading.Lock(),
            },
        )
        namespace["_sample_campaign_run_due_retry_sampler"](identity)
        self.assertEqual(waits, [2.0, 2.0, 2.0, 2.0])
        self.assertEqual(releases[0][0], "pair")
        self.assertEqual(len(resets), 1)
        self.assertEqual(
            namespace["_sample_campaign_retry_ready_identity"], identity,
        )
        self.assertIsNone(namespace["_sample_campaign_retry_sampler_identity"])

    def test_due_retry_sampler_stops_on_unknown_external_telemetry(self):
        resets = []
        identity = (
            90.0, "pair", "sample", 4, 2, "a" * 64, "maestro", "peer",
        )
        namespace = _isolated_functions(
            self.launch,
            ("_sample_campaign_run_due_retry_sampler",),
            {
                "_sample_campaign_retry_sampler_identity": identity,
                "_sample_campaign_retry_ready_identity": None,
                "_sample_campaign_retry_sampler_thread": None,
                "threading": threading,
                "_sample_campaign_reset_idle_gate": lambda: resets.append(True),
                "_sample_campaign_preemption_stop": types.SimpleNamespace(
                    is_set=lambda: False,
                    wait=lambda _delay: self.fail("unknown telemetry must stop"),
                ),
                "_sample_campaign_urgent_ordinary_work_present": lambda: False,
                "_sample_campaign_retry_identity_current": lambda _value: True,
                "capture_foreign_gpu_significance": lambda: types.SimpleNamespace(
                    known=False, significant=False,
                ),
                "_jobs": {"sample": {"id": "sample"}},
                "Mapping": dict,
                "_SAMPLE_CAMPAIGN_RETRY_SNAPSHOTS": 5,
                "_SAMPLE_CAMPAIGN_RETRY_SNAPSHOT_GAP_SECONDS": 2.0,
                "_sample_campaign_retry_sampler_lock": threading.Lock(),
            },
        )
        namespace["_sample_campaign_run_due_retry_sampler"](identity)
        self.assertEqual(len(resets), 2)
        self.assertIsNone(namespace["_sample_campaign_retry_sampler_identity"])

    def test_retry_ready_proof_survives_async_start_until_worker_guard(self):
        resets = []
        identity = (
            90.0, "pair", "sample", 4, 2, "a" * 64, "maestro", "peer",
        )
        namespace = _isolated_functions(
            self.launch,
            ("_sample_campaign_post_slot_guard",),
            {
                "Mapping": dict,
                "Any": object,
                "_sample_campaign_retry_sampler_lock": threading.Lock(),
                "_sample_campaign_retry_ready_identity": identity,
                "_sample_campaign_validate_live_request": lambda _job: True,
                "_sample_campaign_release_readiness": (
                    lambda _job: (True, "ready")
                ),
                "_sample_campaign_reset_idle_gate": (
                    lambda: resets.append(True)
                ),
                "_sample_campaign_persist_hold": lambda *_args: True,
                "_sample_campaign_force_hold": lambda *_args: None,
            },
        )
        job = {"id": "sample", "execution_attempt": 4}
        self.assertTrue(namespace["_sample_campaign_post_slot_guard"](job))
        self.assertIsNone(namespace["_sample_campaign_retry_ready_identity"])
        self.assertEqual(resets, [True])

    def test_retry_sampler_is_separate_and_main_poll_remains_fifteen_seconds(self):
        source = self.launch_source
        loop = ast.get_source_segment(
            source, _function(self.launch, "_sample_campaign_preemption_loop"),
        )
        starter = ast.get_source_segment(
            source,
            _function(self.launch, "_sample_campaign_maybe_start_due_retry_sampler"),
        )
        self.assertIn("_SAMPLE_CAMPAIGN_PREEMPTION_POLL_SECONDS", loop)
        self.assertIn("_sample_campaign_maybe_start_due_retry_sampler()", loop)
        self.assertIn("threading.Thread(", starter)
        self.assertIn('name="sample-campaign-retry-window"', starter)
        self.assertIn("daemon=True", starter)

    def test_settled_retry_restart_accepts_only_exact_crash_window(self):
        namespace = _isolated_functions(
            self.launch,
            ("_safe_sample_campaign_restart_snapshot",),
            {
                "Mapping": dict,
                "Any": object,
                "math": __import__("math"),
                "SAMPLE_RETRY_MAX_ATTEMPT": 1_000_000,
            },
        )
        safe = namespace["_safe_sample_campaign_restart_snapshot"]
        held = {
            "status": "queued", "queue_held": True,
            "recovery_state": "sample_campaign_held",
            "sample_retry": {"attempt": 0, "not_before": None},
        }
        crash_window = {
            **held,
            "recovery_state": "sample_campaign_released",
            "sample_retry": {"attempt": 2, "not_before": 100.0},
        }
        self.assertTrue(safe(held))
        self.assertTrue(safe(crash_window))
        requested_crash = {
            **crash_window,
            "status": "running",
            "queue_held": False,
            "resource_state": "preemption_requested",
        }
        self.assertTrue(safe(requested_crash))
        for invalid in (
            {**crash_window, "queue_held": False},
            {**crash_window, "status": "running"},
            {**requested_crash, "resource_state": "running"},
            {**crash_window, "sample_retry": {
                "attempt": 0, "not_before": None,
            }},
            {**crash_window, "sample_retry": {
                "attempt": 2, "not_before": float("nan"),
            }},
        ):
            self.assertFalse(safe(invalid))
        worker = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        settle = worker.rindex("settle_sample_preemption(")
        restamp = worker.index("_sample_campaign_persist_hold(", settle)
        unregister = worker.rindex("unregister_abort_state(")
        self.assertLess(settle, restamp)
        self.assertLess(restamp, unregister)
        materialize = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_queue_recovery_materialize_job"),
        )
        self.assertIn('"resource_state": "queued"', materialize)
        restore = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_restore_queue_recovery_on_startup"),
        )
        self.assertLess(
            restore.index("_safe_sample_campaign_restart_snapshot(snapshot)"),
            restore.index("_queue_recovery_materialize_job(snapshot, projects)"),
        )

    def test_sample_repeat_registration_and_failed_settlement_remain_recoverable(self):
        worker = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        boundary = worker.index("# No model call is active")
        resumed = worker.index("if resumed:", boundary)
        boundary_source = worker[boundary:resumed]
        self.assertIn("if not sample_worker:", boundary_source)
        finalizer = worker[worker.rindex("finally:"):]
        self.assertIn('job.get("resource_state")', finalizer)
        self.assertIn('== "preemption_requested"', finalizer)
        retry = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_sample_campaign_settle_requested_jobs"),
        )
        self.assertIn("settle_sample_preemption(", retry)
        self.assertIn("_sample_campaign_persist_hold(", retry)

    def test_watcher_retries_failed_sample_settlement_without_orphaning_state(self):
        job = {
            "id": "sample", "kind": "sample_campaign_generation",
            "resource_state": "preemption_requested", "execution_attempt": 3,
        }
        state = {"abort": True}
        active = {"sample": state}
        attempts = []

        def settle(target, **kwargs):
            attempts.append(kwargs["expected_execution_attempt"])
            if len(attempts) == 1:
                raise OSError("journal unavailable")
            active.pop("sample")
            target.update({
                "status": "queued", "queue_held": True,
                "resource_state": "queued", "execution_attempt": 4,
            })
            return True

        namespace = _isolated_functions(
            self.launch,
            ("_sample_campaign_settle_requested_jobs",),
            {
                "Mapping": dict,
                "_active_gen_states": active,
                "_jobs": {"sample": job},
                "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                "_sample_campaign_transition_lock": threading.RLock(),
                "settle_sample_preemption": settle,
                "_sample_campaign_persist_hold": lambda *_args: True,
            },
        )
        namespace["_sample_campaign_settle_requested_jobs"]()
        self.assertIs(active["sample"], state)
        namespace["_sample_campaign_settle_requested_jobs"]()
        self.assertNotIn("sample", active)
        self.assertEqual(attempts, [3, 3])

    def test_sample_h3_delivery_receives_attempt_fenced_publishers(self):
        worker = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        self.assertIn("update_job_fn=update_job", worker)
        self.assertIn("finish_job_fn=finish_job", worker)
        delivery = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_deliver_h3_outputs_transactionally"),
        )
        self.assertIn("update_job_fn=None", delivery)
        self.assertIn("publisher(job, output_files=[])", delivery)
        publication = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_publish_h3_delivery_outputs"),
        )
        self.assertIn("publisher(job, output_files=final_names)", publication)
        protected = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_process_h3_delivery_from_protected_native"),
        )
        self.assertIn("update_job_fn=update_job_fn", protected)
        self.assertIn('== "preemption_requested"', protected)
        flashvsr = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_chunked_flashvsr_upscale"),
        )
        self.assertIn("publisher(job, **changes)", flashvsr)
        self.assertIn("if not publisher(", flashvsr)

    def test_sample_abort_registration_executes_the_attempt_fenced_branch(self):
        runner = _function(self.launch, "_run_generation")
        nested = {
            node.name: node
            for node in ast.walk(runner)
            if isinstance(node, ast.FunctionDef)
            and node.name in {"_sample_attempt", "register_abort_state"}
        }
        module = ast.Module(
            body=[nested["_sample_attempt"], nested["register_abort_state"]],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        calls = []

        def lifecycle_register(target, target_id, active, state, **kwargs):
            calls.append(kwargs["expected_execution_attempt"])
            active[target_id] = state
            return True

        namespace = {
            "Any": object,
            "Mapping": dict,
            "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
            "worker_execution_attempt": 3,
            "_sample_campaign_transition_lock": threading.RLock(),
            "_lifecycle_register_abort_state": lifecycle_register,
        }
        exec(compile(module, "sample-register-abort", "exec"), namespace)
        job = {
            "kind": "sample_campaign_generation",
            "resource_state": "running",
        }
        active = {}
        state = {"abort": False}
        self.assertTrue(namespace["register_abort_state"](
            job, "sample", active, state,
        ))
        self.assertIs(active["sample"], state)
        self.assertEqual(calls, [3])

    def test_safe_unit_checkpoint_propagates_persistence_failure(self):
        job = {
            "id": "sample",
            "recovery_cursor": {"ordinary_repeat_offset": 1},
        }
        before = copy.deepcopy(job["recovery_cursor"])
        namespace = _isolated_functions(
            self.launch,
            ("_queue_recovery_checkpoint_unit",),
            {
                "recovery_unit_id": lambda *_args, **_kwargs: "unit",
                "_recovery_artifact_descriptor": (
                    lambda *_args, **kwargs: {"basename": kwargs["basename"]}
                ),
                "_queue_recovery_units": lambda _job: [],
                "_queue_recovery_checkpoint": lambda *_args, **_kwargs: False,
                "os": os,
            },
        )
        committed = namespace["_queue_recovery_checkpoint_unit"](
            job,
            kind="ordinary_repeat",
            variant=0,
            index=2,
            project_dir="/tmp",
            artifact_names=["output.mp4"],
            ordinary_repeat_offset=2,
        )
        self.assertIsNone(committed)
        self.assertEqual(job["recovery_cursor"], before)

    def test_h3_delivery_forwards_the_exact_injected_publisher(self):
        calls = []

        def fenced(*_args, **_kwargs):
            return True

        namespace = _isolated_functions(
            self.launch,
            ("_deliver_h3_outputs_transactionally",),
            {
                "_H3DeliveryFailure": RuntimeError,
                "uuid": uuid,
                "_queue_recovery_delivery_plan": lambda *_args, **_kwargs: {},
                "_queue_recovery_checkpoint_delivery_intent": (
                    lambda *_args: True
                ),
                "_queue_recovery_checkpoint_delivery_pending": (
                    lambda *_args: True
                ),
                "_stage_h3_delivery_native_outputs": (
                    lambda *_args: [{"recovery_descriptor": {}}]
                ),
                "_release_h3_delivery_vram": lambda: [],
                "is_cancel_requested": lambda _job: False,
                "_process_h3_delivery_from_protected_native": (
                    lambda *_args, **kwargs: calls.append(
                        kwargs["update_job_fn"]
                    ) or ["final.mp4"]
                ),
                "update_job": lambda *_args, **_kwargs: True,
            },
        )
        delivered = namespace["_deliver_h3_outputs_transactionally"](
            {"id": "sample"}, "/tmp", ["native.mp4"], "off",
            "1920x1080", "upscale_exact", update_job_fn=fenced,
        )
        self.assertEqual(delivered, ["final.mp4"])
        self.assertEqual(calls, [fenced])

    def test_h3_publication_holds_preemption_lock_through_final_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native_meta = root / "native.meta.json"
            source_meta = root / "final.meta.json"
            rollback_meta = root / "rollback.meta.json"
            work_path = root / "work.mp4"
            source_path = root / "final.mp4"
            native_meta.write_text(
                json.dumps({"job_id": "sample", "explicit": False}),
                encoding="utf-8",
            )
            work_path.write_bytes(b"media")
            staged = [{
                "file_name": "final.mp4",
                "native_meta": str(native_meta),
                "source_meta": str(source_meta),
                "rollback_meta": str(rollback_meta),
                "work_path": str(work_path),
                "source_path": str(source_path),
            }]
            transition = threading.RLock()
            publisher_entered = threading.Event()
            allow_publisher = threading.Event()
            preemption_acquired = threading.Event()
            descriptors = []
            producer_unit = recovery_unit_id("sample", "h3_delivery")
            job = {
                "id": "sample", "kind": "sample_campaign_generation",
                "workspace": "default", "session_id": "owner",
                "resource_state": "running", "output_files": [],
                "access_policy": {"private": True, "explicit": False},
            }

            def publisher(target, **updates):
                publisher_entered.set()
                self.assertTrue(allow_publisher.wait(2.0))
                target.update(updates)
                return True

            def atomic_json(path, payload):
                Path(path).write_text(json.dumps(payload), encoding="utf-8")

            def commit_final_sidecar(_names):
                sidecar = json.loads(source_meta.read_text(encoding="utf-8"))
                self.assertEqual(sidecar["artifact_class"], "final")
                self.assertTrue(sidecar["private"])
                sidecar.update({
                    "producer_unit_id": producer_unit,
                    "producer_unit_kind": "h3_delivery",
                })
                atomic_json(source_meta, sidecar)
                descriptors.append(artifact_descriptor(
                    root,
                    basename="final.mp4",
                    sidecar_basename="final.meta.json",
                    producer_unit_id=producer_unit,
                ))
                return True

            namespace = _isolated_functions(
                self.launch,
                ("_publish_h3_delivery_outputs",),
                {
                    "os": os, "json": json, "time": time,
                    "_sample_campaign_transition_lock": transition,
                    "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                    "update_job": publisher,
                    "is_cancel_requested": lambda _job: False,
                    "_atomic_write_json": atomic_json,
                    "stamp_sidecar_policy": stamp_sidecar_policy,
                },
            )
            result = []
            publication = threading.Thread(
                target=lambda: result.extend(
                    namespace["_publish_h3_delivery_outputs"](
                        job,
                        staged,
                        update_job_fn=publisher,
                        publication_commit_fn=commit_final_sidecar,
                    )
                ),
            )
            publication.start()
            self.assertTrue(publisher_entered.wait(2.0))

            def preempt():
                with transition:
                    preemption_acquired.set()
                    job["resource_state"] = "preemption_requested"

            contender = threading.Thread(target=preempt)
            contender.start()
            self.assertFalse(preemption_acquired.wait(0.05))
            allow_publisher.set()
            publication.join(2.0)
            contender.join(2.0)
            self.assertEqual(result, ["final.mp4"])
            self.assertTrue(preemption_acquired.is_set())
            final_sidecar = json.loads(source_meta.read_text(encoding="utf-8"))
            self.assertEqual(final_sidecar["artifact_class"], "final")
            self.assertTrue(final_sidecar["private"])
            self.assertTrue(validate_artifact_descriptor(root, descriptors[0]))

    def test_h3_rejected_initial_publisher_aborts_before_gpu_release(self):
        released = []
        namespace = _isolated_functions(
            self.launch,
            ("_deliver_h3_outputs_transactionally",),
            {
                "_H3DeliveryFailure": RuntimeError,
                "uuid": uuid,
                "update_job": lambda *_args, **_kwargs: False,
                "_release_h3_delivery_vram": lambda: released.append(True),
                "_stage_h3_delivery_native_outputs": lambda *_args: [],
            },
        )
        with self.assertRaises(InterruptedError):
            namespace["_deliver_h3_outputs_transactionally"](
                {"id": "sample"}, "/tmp", ["native.mp4"], "off",
                "1920x1080", "upscale_exact",
                update_job_fn=lambda *_args, **_kwargs: False,
            )
        self.assertEqual(released, [])

    def test_h3_stale_rollback_never_clears_new_attempt_outputs(self):
        namespace = _isolated_functions(
            self.launch,
            ("_rollback_h3_delivery_publication",),
            {
                "os": os,
                "_sample_campaign_transition_lock": threading.RLock(),
                "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                "update_job": lambda *_args, **_kwargs: False,
            },
        )
        job = {
            "kind": "sample_campaign_generation",
            "execution_attempt": 4,
            "output_files": ["new-attempt.mp4"],
            "_h3_delivery_publication": {"staged": []},
        }
        namespace["_rollback_h3_delivery_publication"](
            job, update_job_fn=lambda *_args, **_kwargs: False,
        )
        self.assertEqual(job["output_files"], ["new-attempt.mp4"])

    def test_watcher_stop_joins_and_retires_both_threads(self):
        stop = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_stop_sample_campaign_preemption_watcher"),
        )
        self.assertIn("thread.join(timeout=5.0)", stop)
        self.assertIn("_sample_campaign_retry_sampler_thread", stop)
        start = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_start_sample_campaign_preemption_watcher"),
        )
        self.assertIn("_sample_campaign_retry_sampler_thread.is_alive()", start)

    def test_sample_sidecars_require_successful_attempt_fenced_publication(self):
        worker = ast.get_source_segment(
            self.launch_source, _function(self.launch, "_run_generation"),
        )
        publish = worker.index("published_outputs = record_job_outputs(")
        sidecar = worker.index("_write_output_sidecars(new_files", publish)
        gate = worker.index("name in published_outputs", publish)
        self.assertLess(gate, sidecar)
        lock = worker.rfind("with _sample_campaign_transition_lock:", 0, publish)
        self.assertGreater(lock, 0)
        self.assertLess(lock, publish)
        self.assertGreaterEqual(worker.count("sample_safe_unit_current(abort_state)"), 7)
        self.assertNotIn("\n                        _queue_recovery_checkpoint_unit(", worker)


if __name__ == "__main__":
    unittest.main()
