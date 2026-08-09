"""Model-free launch/runtime recovery contract tests."""
from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
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
    ensure_recovery_staging_directory,
    load_request_manifest,
    next_recovery_attempt,
    protected_artifact_descriptor,
    promote_recovery_staged_artifact,
    quarantine_artifact,
    recovery_unit_id,
    replay_concat_to_stable_output,
    replay_delivery_from_protected_native,
    sha256_file as recovery_sha256_file,
    validate_artifact_descriptor,
    validate_manifest_inputs,
    validate_protected_artifact_descriptor,
)
from services.queue_recovery_adapter import (
    owner_principal_digest,
    project_instance_digest,
)
from services.output_access import stamp_sidecar_policy


ROOT = Path(__file__).resolve().parents[1]


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
        registration = ast.get_source_segment(
            self.launch_source,
            _function(self.launch, "_queue_recovery_register_and_publish"),
        )
        self.assertIn("only recovery record", registration)
        self.assertIn("project-relative pointer", registration)

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
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "_queue_recovery_worker": lambda _job: object(),
                "load_request_manifest": lambda *_args, **_kwargs: {
                    "params": {}, "inputs": [{}],
                },
                "validate_manifest_inputs": lambda *_args: (_ for _ in ()).throw(
                    QueueRecoveryRuntimeError("missing input")
                ),
                "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: False,
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
                "_require_project_access": lambda *_args: "/project",
                "owner_principal_digest": owner_principal_digest,
                "_session_secret": lambda: secret,
                "hmac": hmac,
                "_queue_recovery_revalidate_job": lambda _job: True,
                "_queue_recovery_delivery_pending": lambda _job: None,
                "_queue_recovery_checkpoint": lambda target, **updates: target.update(updates),
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
                "_remote_active_projects": active,
                "_remote_active_projects_lock": __import__("threading").RLock(),
                "_existing_workspace_dir": lambda _workspace: "/projects/project-a",
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

        # Local ownership compatibility does not depend on the remote active
        # project map, while still requiring the exact recovered principal.
        active.clear()
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
            "message": "Recovery worker could not be started",
            "output_files": [], "error": None,
            "recovery_state": "blocked", "h3_estimate": {"seconds": 20},
        }
        namespace = _isolated_functions(
            self.launch,
            ("_public_job_prompt_fields", "get_status"),
            {
                "api": fake_api,
                "Request": object,
                "Response": object,
                "HTTPException": Exception,
                "_jobs": {job["id"]: job},
                "_set_recovery_no_store": lambda response: response.headers.update({
                    "Cache-Control": "private, no-store",
                }),
                "_job_owned_by_request": lambda *_args: True,
                "snapshot_job": lambda value: dict(value),
                "_queue_recovery_is_blocked": lambda value: (
                    value.get("recovery_state") == "blocked"
                ),
                "_job_eta_values": lambda _value: (50, 10),
                "queue_position": lambda _value: 4,
                "_queue_wait_reason_for_job": lambda _value: "waiting",
                "_public_queue_residency_metadata": lambda *_args, **_kwargs: {},
                "_public_progress_telemetry": lambda _value: {},
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
        self.assertIsNone(result["subtask_eta_seconds"])
        self.assertIsNone(result["h3_estimate"])
        self.assertEqual(result["estimate_after_resume"], {"seconds": 20})
        self.assertEqual(
            response.headers["Cache-Control"], "private, no-store",
        )

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
            },
        )
        namespace["_restore_queue_recovery_on_startup"]()
        self.assertEqual(registry["first"]["recovery_state"], "blocked")
        self.assertTrue(registry["first"]["queue_held"])
        self.assertTrue(FakeThread.created[1].started)


if __name__ == "__main__":
    unittest.main()
