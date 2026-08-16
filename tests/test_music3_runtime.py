"""Model-, network-, process-, and GPU-free Music 3 runtime lifecycle tests."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import music3_runtime as runtime

from scripts import start_music3_runtime as runtime_cli

UCX_REVISION = runtime.PINNED_UCX_SOURCE_REVISION
UCX_PROBE = b"UCX version 1.20.1\n--- ucx_info -d ---\nTransport: cuda_copy\nTransport: cuda_ipc\n"
FILESYSTEM_CAPABILITY = {
    "schema": "maestro.music3.filesystem-capability.v1",
    "filesystem_type": "testfs",
    "cross_process_flock": True,
    "directory_fsync": True,
    "executable_mode": True,
    "atomic_same_filesystem_replace": True,
    "symlink_detection": True,
}


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class FakeProcess:
    def __init__(self, pid: int = 4242, returncode: int = 0) -> None:
        self.pid = pid
        self.returncode = returncode
        self.wait_calls = 0
        self.kill_calls = 0
        self.stdin = mock.Mock()
        self.stdin.write.return_value = 3

    def wait(self, timeout=None):
        del timeout
        self.wait_calls += 1
        return self.returncode

    def kill(self):
        self.kill_calls += 1
        self.returncode = -signal.SIGKILL


class Music3RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scratch = Path(temporary.name)
        self.pinokio = self.scratch / "pinokio"
        self.pinokio.mkdir(mode=0o700)
        self.location_patch = mock.patch.object(
            runtime,
            "_forbidden_runtime_location",
            return_value=False,
        )
        self.location_patch.start()
        self.addCleanup(self.location_patch.stop)
        self.space_patch = mock.patch.object(
            runtime.shutil,
            "disk_usage",
            return_value=shutil._ntuple_diskusage(
                total=2 * runtime.MIN_PROVISION_FREE_BYTES,
                used=0,
                free=2 * runtime.MIN_PROVISION_FREE_BYTES,
            ),
        )
        self.space_patch.start()
        self.addCleanup(self.space_patch.stop)
        self.ucx_probe_patch = mock.patch.object(
            runtime,
            "_probe_ucx",
            return_value=UCX_PROBE,
        )
        self.ucx_probe_patch.start()
        self.addCleanup(self.ucx_probe_patch.stop)
        self.filesystem_patch = mock.patch.object(
            runtime,
            "_filesystem_capability_evidence",
            return_value=FILESYSTEM_CAPABILITY,
        )
        self.filesystem_patch.start()
        self.addCleanup(self.filesystem_patch.stop)

    def plan(self):
        return runtime.build_music3_provision_plan(
            self.pinokio,
            ucx_version=runtime.PINNED_UCX_VERSION,
            ucx_source_revision=UCX_REVISION,
        )

    def stage(self, plan, generation="generation-1"):
        stage = plan.layout.generations / generation
        (stage / "source").mkdir(parents=True)
        (stage / "model").mkdir()
        (stage / "env" / "bin").mkdir(parents=True)
        (stage / "provenance").mkdir()
        (stage / "source" / "sglang_omni").mkdir()
        (stage / "source" / ".git").mkdir()
        (stage / runtime.GENERATION_LOCK_NAME).touch(mode=0o600)
        (stage / "source" / ".git" / "HEAD").write_text(
            runtime.PINNED_SGLANG_SOURCE_REVISION.removeprefix("git:") + "\n",
            encoding="ascii",
        )
        (stage / "source" / "pyproject.toml").write_text(
            "[project]\nname='sglang-omni'\n",
            encoding="utf-8",
        )
        (stage / "source" / "sglang_omni" / "__init__.py").write_text(
            "__version__ = 'test'\n",
            encoding="utf-8",
        )
        (stage / "model" / "config.json").write_text(
            json.dumps({"model_type": "minimax_music3"}),
            encoding="utf-8",
        )
        (stage / "model" / ".maestro-hf-revision").write_text(
            runtime.MUSIC3_HF_REVISION + "\n",
            encoding="ascii",
        )
        for directory in (
            plan.layout.root,
            plan.layout.generations,
            stage,
            stage / "source",
            stage / "source" / "sglang_omni",
            stage / "source" / ".git",
            stage / "model",
            stage / "env",
            stage / "env" / "bin",
            stage / "provenance",
        ):
            directory.chmod(0o700)
        for artifact in (
            stage / "source" / "pyproject.toml",
            stage / runtime.GENERATION_LOCK_NAME,
            stage / "source" / "sglang_omni" / "__init__.py",
            stage / "source" / ".git" / "HEAD",
            stage / "model" / "config.json",
            stage / "model" / ".maestro-hf-revision",
        ):
            artifact.chmod(0o600)
        executable = stage / "env" / "bin" / "sgl-omni"
        supervisor_python = stage / "env" / "bin" / "python"
        ucx_info = stage / "env" / "bin" / "ucx_info"
        dependency_lock = stage / "env" / "requirements.lock"
        python_runtime = {
            "implementation": "cpython",
            "version": "3.12.3",
            "abi": "cp312",
            "artifact_filename": "cpython-3.12.3-linux-x86_64.tar.zst",
            "artifact_sha256": "sha256:" + ("a" * 64),
            "artifact_size": 1_000_000,
        }
        cuda_runtime = {
            "version": "13.2",
            "architecture": "linux-x86_64",
            "artifact_filename": "cuda-runtime-13.2-linux-x86_64.tar.zst",
            "artifact_sha256": "sha256:" + ("b" * 64),
            "artifact_size": 2_000_000,
        }
        python_runtime_record = stage / runtime.PYTHON_RUNTIME_RECORD
        cuda_runtime_record = stage / runtime.CUDA_RUNTIME_RECORD
        executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        supervisor_python.write_bytes(b"#!/bin/sh\nexit 0\n")
        ucx_info.write_bytes(b"#!/bin/sh\nexit 0\n")
        dependency_lock.write_text(
            "\n".join(sorted(runtime.REQUIRED_RUNTIME_LOCK_LINES)) + "\n",
            encoding="utf-8",
        )
        python_runtime_record.write_text(
            json.dumps(python_runtime, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        cuda_runtime_record.write_text(
            json.dumps(cuda_runtime, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        supervisor_python.chmod(0o700)
        ucx_info.chmod(0o700)
        dependency_lock.chmod(0o600)
        python_runtime_record.chmod(0o600)
        cuda_runtime_record.chmod(0o600)
        ucx_build_record = stage / "provenance" / "ucx-build.json"
        ucx_build_record.write_text(json.dumps({
            "schema": "maestro.music3.ucx-build.v1",
            "ucx_version": runtime.PINNED_UCX_VERSION,
            "ucx_source_revision": runtime.PINNED_UCX_SOURCE_REVISION,
            "ucx_source_tarball_sha256": runtime.PINNED_UCX_TARBALL_SHA256,
            "ucx_source_tarball_size": runtime.PINNED_UCX_TARBALL_SIZE,
            "ucx_configure_flags": list(runtime.PINNED_UCX_CONFIGURE_FLAGS),
            "ucx_info_sha256": _sha256(ucx_info),
        }, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        ucx_build_record.chmod(0o600)
        marker = runtime.build_music3_stage_manifest(
            plan,
            generation_id=generation,
            runtime_executable_sha256=_sha256(executable),
            runtime_source_tree_sha256=runtime.music3_tree_sha256(stage / "source"),
            dependency_lock_sha256=_sha256(dependency_lock),
            environment_tree_sha256=runtime.music3_tree_sha256(stage / "env"),
            ucx_info_sha256=_sha256(ucx_info),
            ucx_build_record_sha256=_sha256(ucx_build_record),
            ucx_probe_sha256="sha256:" + hashlib.sha256(UCX_PROBE).hexdigest(),
            model_snapshot_sha256=runtime.music3_tree_sha256(stage / "model"),
            python_runtime=python_runtime,
            cuda_runtime=cuda_runtime,
        )
        marker_path = stage / runtime.STAGE_MANIFEST_NAME
        marker_path.write_text(
            json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        marker_path.chmod(0o600)
        return stage

    def reviewed_manifest_sha256(self, stage):
        document = json.loads(
            (stage / runtime.STAGE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        return runtime._mapping_sha256(document)

    def token(self, plan, stage):
        return runtime.music3_publication_token(
            plan,
            stage,
            expected_stage_manifest_sha256=self.reviewed_manifest_sha256(stage),
        )

    def publish_stage(self, plan, stage, *, apply_token):
        return runtime.publish_music3_stage(
            plan,
            stage,
            apply_token=apply_token,
            expected_stage_manifest_sha256=self.reviewed_manifest_sha256(stage),
        )

    def publish(self, plan, generation="generation-1"):
        stage = self.stage(plan, generation)
        token = self.token(plan, stage)
        self.publish_stage(plan, stage, apply_token=token)
        return token

    def test_plan_pins_sources_locality_paths_and_free_space(self):
        plan = self.plan()
        rendered = plan.to_mapping()
        self.assertEqual(
            rendered["runtime_source_revision"],
            runtime.PINNED_SGLANG_SOURCE_REVISION,
        )
        self.assertEqual(rendered["model_revision"], runtime.PINNED_MODEL_REVISION)
        self.assertEqual(rendered["ucx_version"], runtime.PINNED_UCX_VERSION)
        self.assertEqual(rendered["ucx_source_revision"], UCX_REVISION)
        self.assertTrue(rendered["filesystem_acceptance"]["directory_fsync"])
        self.assertEqual(rendered["network"], {
            "bind_host": "127.0.0.1",
            "dynamic_port_required": True,
            "wan": False,
            "lan": False,
            "cloudflare": False,
            "rented_compute": False,
        })
        for key in ("home", "cache", "temporary", "generations", "staging"):
            self.assertIn(plan.layout.root, Path(rendered["paths"][key]).parents)
        self.assertEqual(plan.sha256, self.plan().sha256)

    def test_plan_rejects_home_relative_wrong_source_ucx_and_low_space(self):
        cases = (
            {"runtime_source_revision": "git:" + ("d" * 40)},
            {"ucx_version": "1.20.0"},
            {"ucx_source_revision": "main"},
        )
        for updates in cases:
            values = {
                "runtime_source_revision": runtime.PINNED_SGLANG_SOURCE_REVISION,
                "ucx_version": runtime.PINNED_UCX_VERSION,
                "ucx_source_revision": UCX_REVISION,
            }
            values.update(updates)
            with self.subTest(updates=updates), self.assertRaises(runtime.Music3RuntimeError):
                runtime.build_music3_provision_plan(self.pinokio, **values)
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            runtime.resolve_music3_runtime_layout("pinokio")
        self.location_patch.stop()
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            runtime.resolve_music3_runtime_layout(Path("/home") / "hailey" / "pinokio")
        self.location_patch.start()
        with mock.patch.object(
            runtime.shutil,
            "disk_usage",
            return_value=shutil._ntuple_diskusage(total=1, used=0, free=1),
        ), self.assertRaisesRegex(runtime.Music3RuntimeError, "insufficient"):
            runtime.build_music3_provision_plan(
                self.pinokio,
                ucx_version=runtime.PINNED_UCX_VERSION,
                ucx_source_revision=UCX_REVISION,
            )

    def test_symlink_and_hardlink_stage_artifacts_fail_closed(self):
        plan = self.plan()
        stage = self.stage(plan)
        executable = stage / "env" / "bin" / "sgl-omni"
        hardlink = stage / "env" / "bin" / "sgl-omni-copy"
        os.link(executable, hardlink)
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            self.token(plan, stage)
        hardlink.unlink()
        outside = self.scratch / "outside"
        outside.mkdir()
        shutil.rmtree(stage / "source")
        (stage / "source").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            self.token(plan, stage)

    def test_empty_source_model_and_lying_ucx_evidence_fail_closed(self):
        plan = self.plan()
        stage = self.stage(plan)
        with mock.patch.object(
            runtime,
            "_probe_ucx",
            return_value=b"UCX version 1.20.1\n",
        ), self.assertRaisesRegex(runtime.Music3RuntimeSecurityError, "CUDA"):
            self.token(plan, stage)
        (stage / "source" / "pyproject.toml").unlink()
        with self.assertRaisesRegex(runtime.Music3RuntimeSecurityError, "incomplete"):
            self.token(plan, stage)

    def test_independent_manifest_trust_precedes_staged_execution(self):
        plan = self.plan()
        stage = self.stage(plan)
        reviewed = self.reviewed_manifest_sha256(stage)
        document = json.loads(
            (stage / runtime.STAGE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        document["generation_id"] = "attacker-rewrite"
        (stage / runtime.STAGE_MANIFEST_NAME).write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        probe = mock.Mock(return_value=UCX_PROBE)
        self.ucx_probe_patch.stop()
        try:
            with mock.patch.object(runtime, "_probe_ucx", probe), self.assertRaisesRegex(
                runtime.Music3RuntimeSecurityError,
                "independently reviewed",
            ):
                runtime.music3_publication_token(
                    plan,
                    stage,
                    expected_stage_manifest_sha256=reviewed,
                )
        finally:
            self.ucx_probe_patch.start()
        probe.assert_not_called()

    def test_generation_lock_is_required_and_held_during_validation(self):
        plan = self.plan()
        stage = self.stage(plan)
        calls = []
        with mock.patch.object(
            runtime.fcntl,
            "flock",
            side_effect=lambda _descriptor, operation: calls.append(operation),
        ):
            self.token(plan, stage)
        self.assertEqual(calls, [runtime.fcntl.LOCK_SH, runtime.fcntl.LOCK_UN])

        (stage / runtime.GENERATION_LOCK_NAME).unlink()
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            self.token(plan, stage)
        (stage / runtime.GENERATION_LOCK_NAME).touch(mode=0o644)
        (stage / runtime.GENERATION_LOCK_NAME).chmod(0o644)
        with self.assertRaisesRegex(runtime.Music3RuntimeSecurityError, "generation lock"):
            self.token(plan, stage)

    def test_generation_lock_rejects_unlink_recreate_split(self):
        plan = self.plan()
        stage = self.stage(plan)
        lock_path = stage / runtime.GENERATION_LOCK_NAME
        replaced = False

        def flock(_descriptor, operation):
            nonlocal replaced
            if operation == runtime.fcntl.LOCK_SH and not replaced:
                replaced = True
                lock_path.unlink()
                lock_path.touch(mode=0o600)
                lock_path.chmod(0o600)

        with mock.patch.object(runtime.fcntl, "flock", side_effect=flock), self.assertRaisesRegex(
            runtime.Music3RuntimeSecurityError,
            "identity split",
        ):
            self.token(plan, stage)

    def test_duplicate_json_fields_fail_before_stage_execution(self):
        plan = self.plan()
        stage = self.stage(plan)
        marker = stage / runtime.STAGE_MANIFEST_NAME
        payload = marker.read_text(encoding="utf-8")
        marker.write_text(
            '{"generation_id":"duplicate",' + payload[1:],
            encoding="utf-8",
        )
        reviewed = runtime._mapping_sha256(json.loads(marker.read_text(encoding="utf-8")))
        probe = mock.Mock(return_value=UCX_PROBE)
        self.ucx_probe_patch.stop()
        try:
            with mock.patch.object(runtime, "_probe_ucx", probe), self.assertRaisesRegex(
                runtime.Music3RuntimeSecurityError,
                "duplicate fields",
            ):
                runtime.music3_publication_token(
                    plan,
                    stage,
                    expected_stage_manifest_sha256=reviewed,
                )
        finally:
            self.ucx_probe_patch.start()
        probe.assert_not_called()

    def test_deeply_nested_json_is_normalized_to_a_runtime_error(self):
        marker = self.scratch / "deep-marker.json"
        marker.write_text("[" * 2_000 + "0" + "]" * 2_000, encoding="ascii")
        marker.chmod(0o600)
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            runtime._read_json(marker)

    def test_runtime_artifact_semantics_and_records_are_exact(self):
        plan = self.plan()
        stage = self.stage(plan)
        marker = stage / runtime.STAGE_MANIFEST_NAME
        document = json.loads(marker.read_text(encoding="utf-8"))
        document["python_runtime"]["abi"] = "cp311"
        marker.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(runtime.Music3RuntimeError, "ABI"):
            self.token(plan, stage)

        stage = self.stage(plan, "generation-2")
        cuda_record = stage / runtime.CUDA_RUNTIME_RECORD
        cuda_document = json.loads(cuda_record.read_text(encoding="utf-8"))
        cuda_document["artifact_size"] += 1
        cuda_record.write_text(
            json.dumps(cuda_document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        cuda_record.chmod(0o600)
        stage_document = json.loads(
            (stage / runtime.STAGE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        stage_document["environment_tree_sha256"] = runtime.music3_tree_sha256(
            stage / "env"
        )
        (stage / runtime.STAGE_MANIFEST_NAME).write_text(
            json.dumps(stage_document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            runtime.Music3RuntimeSecurityError,
            "artifact record does not match",
        ):
            self.token(plan, stage)

    def test_dependency_lock_and_installed_environment_are_exactly_bound(self):
        plan = self.plan()
        stage = self.stage(plan)
        dependency_lock = stage / "env" / "requirements.lock"
        dependency_lock.write_text(
            dependency_lock.read_text(encoding="utf-8") + "unbounded-package>=1\n",
            encoding="utf-8",
        )
        document = json.loads(
            (stage / runtime.STAGE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        document["dependency_lock_sha256"] = _sha256(dependency_lock)
        document["environment_tree_sha256"] = runtime.music3_tree_sha256(stage / "env")
        (stage / runtime.STAGE_MANIFEST_NAME).write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(runtime.Music3RuntimeSecurityError, "exact pin"):
            self.token(plan, stage)

        conflicting = self.stage(plan, "generation-2")
        conflicting_lock = conflicting / "env" / "requirements.lock"
        conflicting_lock.write_text(
            conflicting_lock.read_text(encoding="utf-8")
            + "flashinfer_python==9.9.9\n",
            encoding="utf-8",
        )
        conflicting_document = json.loads(
            (conflicting / runtime.STAGE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        conflicting_document["dependency_lock_sha256"] = _sha256(conflicting_lock)
        conflicting_document["environment_tree_sha256"] = runtime.music3_tree_sha256(
            conflicting / "env"
        )
        (conflicting / runtime.STAGE_MANIFEST_NAME).write_text(
            json.dumps(conflicting_document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(runtime.Music3RuntimeSecurityError, "conflicting"):
            self.token(plan, conflicting)

    def test_publication_requires_revalidated_token_and_retains_last_good(self):
        plan = self.plan()
        first = self.stage(plan, "generation-1")
        with self.assertRaises(runtime.Music3RuntimeConflict):
            self.publish_stage(plan, first, apply_token="sha256:" + ("0" * 64))
        first_token = self.token(plan, first)
        self.publish_stage(plan, first, apply_token=first_token)
        verified = runtime.verify_music3_runtime(self.pinokio)
        self.assertEqual(verified["generation_id"], "generation-1")
        self.assertEqual(verified["python_runtime"]["abi"], "cp312")
        self.assertEqual(verified["cuda_runtime"]["version"], "13.2")

        second = self.stage(plan, "generation-2")
        second_token = self.token(plan, second)
        self.publish_stage(plan, second, apply_token=second_token)
        self.assertEqual(runtime.verify_music3_runtime(self.pinokio)["generation_id"], "generation-2")
        current_marker = json.loads(plan.layout.current_marker.read_text(encoding="utf-8"))
        self.assertEqual(current_marker["previous"]["generation_id"], "generation-1")
        self.assertEqual(
            Path(current_marker["previous"]["path"]).name,
            "generation-1",
        )
        third = self.stage(plan, "generation-3")
        with self.assertRaisesRegex(runtime.Music3RuntimeConflict, "last-good"):
            self.publish_stage(
                plan,
                third,
                apply_token=self.token(plan, third),
            )

    def test_republishing_a_referenced_generation_rejects_without_nested_lock(self):
        plan = self.plan()
        current = self.stage(plan)
        token = self.token(plan, current)
        self.publish_stage(plan, current, apply_token=token)
        lock_calls = []

        class FakeLock:
            def __enter__(self):
                return lambda: None

            def __exit__(self, *_args):
                return False

        def generation_lock(_layout, generation, *, exclusive):
            lock_calls.append((Path(generation).name, exclusive))
            return FakeLock()

        with mock.patch.object(runtime, "_generation_lock", side_effect=generation_lock), self.assertRaisesRegex(
            runtime.Music3RuntimeConflict,
            "already current",
        ):
            self.publish_stage(plan, current, apply_token=token)
        self.assertEqual(lock_calls, [("generation-1", False), ("generation-1", True)])

    def test_publication_restores_marker_and_directory_after_marker_commit_failure(self):
        plan = self.plan()
        self.publish(plan, "generation-1")
        previous_marker = plan.layout.current_marker.read_bytes()
        stage = self.stage(plan, "generation-2")
        token = self.token(plan, stage)
        original_atomic_json = runtime._atomic_json
        failed_once = False

        def commit_then_fail(path, value):
            nonlocal failed_once
            original_atomic_json(path, value)
            if path == plan.layout.current_marker and not failed_once:
                failed_once = True
                raise OSError("forced post-replace failure")

        with mock.patch.object(
            runtime,
            "_atomic_json",
            side_effect=commit_then_fail,
        ), self.assertRaisesRegex(runtime.Music3RuntimeConflict, "restored"):
            self.publish_stage(plan, stage, apply_token=token)
        self.assertTrue(stage.is_dir())
        self.assertTrue((plan.layout.generations / "generation-1").exists())
        self.assertEqual(
            json.loads(((plan.layout.generations / "generation-1") / runtime.STAGE_MANIFEST_NAME).read_text())["generation_id"],
            "generation-1",
        )
        self.assertEqual(plan.layout.current_marker.read_bytes(), previous_marker)
        self.assertTrue(stage.exists())

    def test_publication_rolls_back_when_generation_lock_exit_fails(self):
        plan = self.plan()
        stage = self.stage(plan)
        token = self.token(plan, stage)
        attempted = threading.Event()
        acquired = threading.Event()
        exit_failed = threading.Event()
        rollback_observed = threading.Event()
        waiter_threads = []
        original_sync_directory = runtime._sync_directory

        def wait_for_lifecycle_lock():
            attempted.set()
            with runtime._lifecycle_lock(plan.layout):
                acquired.set()

        def sync_directory(path):
            if exit_failed.is_set() and path == plan.layout.state:
                self.assertFalse(acquired.is_set())
                rollback_observed.set()
            return original_sync_directory(path)

        class ExitFailureLock:
            def __init__(self, exclusive):
                self.exclusive = exclusive

            def __enter__(self):
                return lambda: None

            def __exit__(self, *_args):
                if self.exclusive:
                    exit_failed.set()
                    waiter = threading.Thread(target=wait_for_lifecycle_lock)
                    waiter_threads.append(waiter)
                    waiter.start()
                    self_outer.assertTrue(attempted.wait(timeout=2))
                    self_outer.assertFalse(acquired.is_set())
                    raise runtime.Music3RuntimeSecurityError("forced lock exit failure")
                return False

        self_outer = self
        with mock.patch.object(
            runtime,
            "_generation_lock",
            side_effect=lambda _layout, _stage, *, exclusive: ExitFailureLock(exclusive),
        ), mock.patch.object(
            runtime,
            "_sync_directory",
            side_effect=sync_directory,
        ), self.assertRaisesRegex(runtime.Music3RuntimeConflict, "last-good was restored"):
            self.publish_stage(plan, stage, apply_token=token)
        for waiter in waiter_threads:
            waiter.join(timeout=2)
            self.assertFalse(waiter.is_alive())
        self.assertTrue(rollback_observed.is_set())
        self.assertTrue(acquired.is_set())
        self.assertFalse(plan.layout.current_marker.exists())
        self.assertTrue(stage.is_dir())

    def test_publication_rolls_back_before_lifecycle_release_failure_escapes(self):
        plan = self.plan()
        stage = self.stage(plan)
        token = self.token(plan, stage)
        marker_present_at_release = []

        class ReleaseFailureLifecycle:
            def __enter__(self):
                def release():
                    marker_present_at_release.append(plan.layout.current_marker.exists())
                    raise runtime.Music3RuntimeSecurityError(
                        "forced lifecycle release failure"
                    )

                return release

            def __exit__(self, *_args):
                return False

        with mock.patch.object(
            runtime,
            "_lifecycle_lock",
            return_value=ReleaseFailureLifecycle(),
        ), self.assertRaisesRegex(runtime.Music3RuntimeConflict, "last-good was restored"):
            self.publish_stage(plan, stage, apply_token=token)
        self.assertEqual(marker_present_at_release, [True])
        self.assertFalse(plan.layout.current_marker.exists())
        self.assertTrue(stage.is_dir())

    def test_publication_fsyncs_final_generation_before_state_marker(self):
        plan = self.plan()
        stage = self.stage(plan)
        token = self.token(plan, stage)
        events = []
        original_sync_tree = runtime._sync_generation_tree
        original_atomic_json = runtime._atomic_json
        original_validate_stage = runtime._validate_stage

        def validate_stage(*args, **kwargs):
            events.append("validate")
            return original_validate_stage(*args, **kwargs)

        def sync_tree(*args, **kwargs):
            events.append("generation")
            return original_sync_tree(*args, **kwargs)

        def atomic_json(path, value):
            if path == plan.layout.current_marker:
                events.append("marker")
            return original_atomic_json(path, value)

        with mock.patch.object(
            runtime,
            "_validate_stage",
            side_effect=validate_stage,
        ), mock.patch.object(runtime, "_sync_generation_tree", side_effect=sync_tree), mock.patch.object(
            runtime,
            "_atomic_json",
            side_effect=atomic_json,
        ):
            self.publish_stage(plan, stage, apply_token=token)
        self.assertEqual(
            events,
            ["validate", "validate", "generation", "validate", "marker"],
        )

    def test_publication_refuses_to_replace_assets_while_process_marker_exists(self):
        plan = self.plan()
        self.publish(plan)
        runtime._atomic_json(plan.layout.process_marker, {
            "schema": runtime.PROCESS_SCHEMA,
            "pid": 4242,
            "process_group_id": 4242,
            "started_at_ticks": 1,
            "launch_command_sha256": "sha256:" + ("a" * 64),
            "observed_command_sha256": "sha256:" + ("b" * 64),
            "plan_sha256": plan.sha256,
            "generation_id": "generation-1",
            "base_url": "http://127.0.0.1:32123",
        })
        stage = self.stage(plan, "generation-2")
        token = self.token(plan, stage)
        with self.assertRaisesRegex(runtime.Music3RuntimeConflict, "ownership"):
            self.publish_stage(plan, stage, apply_token=token)

    def test_start_command_and_environment_are_closed_local_and_runtime_rooted(self):
        plan = self.plan()
        self.publish(plan)
        command = runtime.build_music3_start_command(self.pinokio, port=32123)
        active = plan.layout.generations / "generation-1"
        self.assertEqual(command, (
            str(active / "env" / "bin" / "sgl-omni"),
            "serve",
            "--model-path",
            str(active / "model"),
            "--host",
            "127.0.0.1",
            "--port",
            "32123",
        ))
        with mock.patch.dict(
            os.environ,
            {"PATH": "/unreviewed/bin", "LD_LIBRARY_PATH": "/unreviewed/lib"},
        ):
            environment = runtime.music3_runtime_environment(self.pinokio)
        for key in (
            "HOME",
            "HF_HOME",
            "HF_HUB_CACHE",
            "HUGGINGFACE_HUB_CACHE",
            "TRANSFORMERS_CACHE",
            "UV_CACHE_DIR",
            "TORCH_HOME",
            "XDG_CACHE_HOME",
            "TMPDIR",
        ):
            self.assertIn(plan.layout.root, Path(environment[key]).parents)
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
        self.assertEqual(environment["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(
            environment["PYTHONPYCACHEPREFIX"],
            str(plan.layout.cache / "pycache"),
        )
        self.assertEqual(environment["NCCL_SOCKET_IFNAME"], "lo")
        self.assertEqual(
            environment["PATH"],
            f"{active / 'env' / 'bin'}:/usr/bin:/bin",
        )
        self.assertEqual(
            environment["LD_LIBRARY_PATH"],
            str(active / "env" / "lib"),
        )
        for port in (0, 65536, True, "32123"):
            with self.subTest(port=port), self.assertRaises(runtime.Music3RuntimeError):
                runtime.build_music3_start_command(self.pinokio, port=port)

    def test_ucx_probe_uses_the_same_manifest_bound_library_path(self):
        executable = self.pinokio / "runtime" / "env" / "bin" / "ucx_info"
        completed = mock.Mock(stdout=b"proof")
        self.ucx_probe_patch.stop()
        try:
            with mock.patch.object(runtime.subprocess, "run", return_value=completed) as run:
                self.assertEqual(runtime._probe_ucx(executable), b"proof\n--- ucx_info -d ---\nproof")
        finally:
            self.ucx_probe_patch.start()
        for call in run.call_args_list:
            self.assertEqual(
                call.kwargs["env"]["PATH"],
                f"{executable.parents[1] / 'bin'}:/usr/bin:/bin",
            )
            self.assertEqual(
                call.kwargs["env"]["LD_LIBRARY_PATH"],
                str(executable.parents[1] / "lib"),
            )

    def test_fake_start_and_stop_require_exact_owned_process_tree(self):
        plan = self.plan()
        self.publish(plan)
        captured = {}
        fake = FakeProcess()

        def popen(command, **kwargs):
            captured["command"] = tuple(command)
            captured["kwargs"] = kwargs
            return fake

        stopped = False

        def identity(pid):
            if stopped:
                raise ProcessLookupError(pid)
            return pid, 777, runtime._command_sha256(captured["command"])

        process, marker = runtime.start_music3_runtime(
            self.pinokio,
            port=32123,
            popen_factory=popen,
            identity_reader=identity,
        )
        self.assertIs(process, fake)
        self.assertEqual(captured["command"][1:4], ("-I", "-S", "-c"))
        self.assertEqual(captured["command"][4], runtime._SUPERVISOR_CODE)
        self.assertTrue(captured["kwargs"]["start_new_session"])
        self.assertFalse(captured["kwargs"]["shell"])
        self.assertIs(captured["kwargs"]["stdin"], runtime.subprocess.PIPE)
        self.assertEqual(captured["kwargs"]["bufsize"], 0)
        fake.stdin.write.assert_called_once_with(b"go\n")
        self.assertEqual(marker["base_url"], "http://127.0.0.1:32123")
        self.assertEqual(
            runtime.music3_runtime_status(self.pinokio, identity_reader=identity)["state"],
            "running",
        )
        signals = []

        def signal_group(marker, signal_number, *, identity_reader):
            nonlocal stopped
            del identity_reader
            signals.append((marker["pid"], signal_number))
            stopped = True

        result = runtime.stop_owned_music3_runtime(
            self.pinokio,
            identity_reader=identity,
            owned_group_signaler=signal_group,
            sleeper=lambda _seconds: None,
        )
        self.assertEqual(signals, [(fake.pid, signal.SIGTERM)])
        self.assertTrue(result["stopped"])
        self.assertFalse(plan.layout.process_marker.exists())

    def test_start_rejects_observed_supervisor_argv_drift_before_release(self):
        plan = self.plan()
        self.publish(plan)
        fake = FakeProcess()

        with self.assertRaisesRegex(
            runtime.Music3RuntimeSecurityError,
            "process group identity",
        ):
            runtime.start_music3_runtime(
                self.pinokio,
                port=32123,
                popen_factory=lambda _command, **_kwargs: fake,
                identity_reader=lambda pid: (
                    pid,
                    777,
                    runtime._command_sha256(("foreign", "argv")),
                ),
            )
        self.assertEqual(fake.kill_calls, 1)
        self.assertEqual(fake.wait_calls, 1)
        fake.stdin.write.assert_not_called()
        self.assertFalse(plan.layout.process_marker.exists())

    def test_lifecycle_lock_serializes_concurrent_starts(self):
        plan = self.plan()
        self.publish(plan)
        entered = threading.Event()
        release = threading.Event()
        commands = {}
        calls = []

        def popen(command, **_kwargs):
            calls.append(tuple(command))
            commands[4242] = tuple(command)
            entered.set()
            self.assertTrue(release.wait(timeout=2))
            return FakeProcess()

        def identity(pid):
            return pid, 777, runtime._command_sha256(commands[pid])

        outcomes = []

        def start():
            try:
                runtime.start_music3_runtime(
                    self.pinokio,
                    port=32123,
                    popen_factory=popen,
                    identity_reader=identity,
                )
                outcomes.append("started")
            except runtime.Music3RuntimeConflict:
                outcomes.append("conflict")

        first = threading.Thread(target=start)
        second = threading.Thread(target=start)
        first.start()
        self.assertTrue(entered.wait(timeout=2))
        second.start()
        release.set()
        first.join(timeout=2)
        second.join(timeout=2)
        self.assertEqual(sorted(outcomes), ["conflict", "started"])
        self.assertEqual(len(calls), 1)

    def test_lifecycle_lock_uses_host_local_exclusive_flock(self):
        plan = self.plan()
        plan.layout.root.mkdir(parents=True, mode=0o700)
        plan.layout.root.chmod(0o700)
        calls = []
        with mock.patch.object(
            runtime.fcntl,
            "flock",
            side_effect=lambda _descriptor, operation: calls.append(operation),
        ), runtime._lifecycle_lock(plan.layout):
            self.assertTrue(plan.layout.state.is_dir())
        self.assertEqual(calls, [runtime.fcntl.LOCK_EX, runtime.fcntl.LOCK_UN])

    def test_lifecycle_lock_rejects_unlink_recreate_split(self):
        plan = self.plan()
        plan.layout.root.mkdir(parents=True, mode=0o700)
        plan.layout.root.chmod(0o700)
        replaced = False

        def flock(_descriptor, operation):
            nonlocal replaced
            if operation == runtime.fcntl.LOCK_EX and not replaced:
                replaced = True
                lock_path = plan.layout.state / "lifecycle.lock"
                lock_path.unlink()
                lock_path.touch(mode=0o600)
                lock_path.chmod(0o600)

        with mock.patch.object(runtime.fcntl, "flock", side_effect=flock), self.assertRaisesRegex(
            runtime.Music3RuntimeSecurityError,
            "identity split",
        ), runtime._lifecycle_lock(plan.layout):
            self.fail("split lifecycle lock must not admit work")

    def test_filesystem_acceptance_requires_live_primitives_without_running_them(self):
        self.filesystem_patch.stop()
        try:
            source = inspect.getsource(runtime._filesystem_capability_evidence)
        finally:
            self.filesystem_patch.start()
        for required in (
            "os.fork()",
            "fcntl.LOCK_EX | fcntl.LOCK_NB",
            "os.fsync(descriptor)",
            "_sync_directory(probe)",
            "os.access(file_path, os.X_OK)",
            "os.replace(file_path, renamed_path)",
            "link_path.symlink_to(file_path.name)",
            '"filesystem_type": _filesystem_type(layout.root)',
        ):
            self.assertIn(required, source)

    def test_pidfd_pins_identity_before_group_signal(self):
        observed = "sha256:" + ("d" * 64)
        marker = {
            "pid": 4242,
            "started_at_ticks": 777,
            "observed_command_sha256": observed,
        }
        events = []

        def identity(_pid):
            events.append("identity")
            return 4242, 777, observed

        with mock.patch.object(runtime.os, "pidfd_open", create=True, side_effect=lambda *_: events.append("open") or 91), mock.patch.object(
            runtime.signal,
            "pidfd_send_signal",
            create=True,
            side_effect=lambda _fd, number: events.append(f"pidfd:{number}"),
        ), mock.patch.object(
            runtime.os,
            "killpg",
            side_effect=lambda _pid, number: events.append(f"group:{number}"),
        ), mock.patch.object(
            runtime.os,
            "close",
            side_effect=lambda _fd: events.append("close"),
        ):
            runtime._signal_proven_owned_group(
                marker,
                signal.SIGTERM,
                identity_reader=identity,
            )
        self.assertEqual(events, [
            "open",
            "identity",
            f"pidfd:{signal.SIGSTOP}",
            "identity",
            f"group:{signal.SIGTERM}",
            f"pidfd:{signal.SIGCONT}",
            "close",
        ])

    def test_failed_start_never_releases_the_gated_supervisor(self):
        plan = self.plan()
        self.publish(plan)
        fake = FakeProcess()
        captured = {}

        def popen(command, **_kwargs):
            captured["command"] = tuple(command)
            return fake

        def identity(pid):
            return pid, 777, runtime._command_sha256(captured["command"])

        original_verify = runtime.verify_music3_runtime
        verify_calls = 0

        def fail_after_spawn(pinokio_root):
            nonlocal verify_calls
            verify_calls += 1
            if verify_calls == 1:
                return original_verify(pinokio_root)
            raise runtime.Music3RuntimeSecurityError("forced post-spawn failure")

        with mock.patch.object(
            runtime,
            "verify_music3_runtime",
            side_effect=fail_after_spawn,
        ), self.assertRaisesRegex(runtime.Music3RuntimeSecurityError, "forced"):
            runtime.start_music3_runtime(
                self.pinokio,
                port=32123,
                popen_factory=popen,
                identity_reader=identity,
            )
        self.assertEqual(fake.kill_calls, 1)
        self.assertEqual(fake.wait_calls, 1)
        fake.stdin.write.assert_not_called()
        self.assertFalse(plan.layout.process_marker.exists())

    def test_foreign_process_identity_is_never_signalled(self):
        plan = self.plan()
        self.publish(plan)
        marker = {
            "schema": runtime.PROCESS_SCHEMA,
            "pid": 4242,
            "process_group_id": 4242,
            "started_at_ticks": 777,
            "launch_command_sha256": "sha256:" + ("d" * 64),
            "observed_command_sha256": "sha256:" + ("d" * 64),
            "plan_sha256": plan.sha256,
            "generation_id": "generation-1",
            "base_url": "http://127.0.0.1:32123",
        }
        runtime._atomic_json(plan.layout.process_marker, marker)
        signal_group = mock.Mock()
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            runtime.stop_owned_music3_runtime(
                self.pinokio,
                identity_reader=lambda _pid: (4242, 778, marker["observed_command_sha256"]),
                owned_group_signaler=signal_group,
            )
        signal_group.assert_not_called()

    def test_process_marker_for_another_generation_fails_closed(self):
        plan = self.plan()
        self.publish(plan)
        observed = "sha256:" + ("d" * 64)
        runtime._atomic_json(plan.layout.process_marker, {
            "schema": runtime.PROCESS_SCHEMA,
            "pid": 4242,
            "process_group_id": 4242,
            "started_at_ticks": 777,
            "launch_command_sha256": observed,
            "observed_command_sha256": observed,
            "plan_sha256": plan.sha256,
            "generation_id": "generation-0",
            "base_url": "http://127.0.0.1:32123",
        })
        with self.assertRaisesRegex(runtime.Music3RuntimeSecurityError, "generation"):
            runtime.music3_runtime_status(
                self.pinokio,
                identity_reader=lambda _pid: (4242, 777, observed),
            )

    def test_state_and_reset_quarantine_symlinks_fail_closed(self):
        plan = self.plan()
        stage = self.stage(plan)
        outside = self.scratch / "outside-state"
        outside.mkdir(mode=0o700)
        plan.layout.state.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            self.publish_stage(
                plan,
                stage,
                apply_token=self.token(plan, stage),
            )

    def test_reset_quarantine_symlink_fails_closed(self):
        plan = self.plan()
        self.publish(plan)
        outside = self.scratch / "outside-quarantine"
        outside.mkdir(mode=0o700)
        plan.layout.reset_quarantine.symlink_to(outside, target_is_directory=True)
        reset = runtime.build_music3_reset_plan(self.pinokio)
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            runtime.apply_music3_reset_plan(
                self.pinokio,
                confirmation_token=reset["confirmation_token"],
            )

    def test_group_writable_cache_parent_is_rejected(self):
        plan = self.plan()
        plan.layout.cache.mkdir(parents=True, mode=0o700)
        plan.layout.root.chmod(0o700)
        plan.layout.cache.chmod(0o770)
        with self.assertRaisesRegex(runtime.Music3RuntimeSecurityError, "ownership"):
            runtime._prepare_runtime_directories(plan.layout)

    def test_reset_is_plan_only_until_exact_fresh_token_then_quarantines(self):
        plan = self.plan()
        self.publish(plan)
        reset = runtime.build_music3_reset_plan(self.pinokio)
        current_generation = plan.layout.generations / "generation-1"
        self.assertTrue(current_generation.exists())
        with self.assertRaises(runtime.Music3RuntimeConflict):
            runtime.apply_music3_reset_plan(
                self.pinokio,
                confirmation_token="sha256:" + ("0" * 64),
            )
        self.assertTrue(current_generation.exists())
        with mock.patch.object(
            runtime,
            "_sync_directory",
            wraps=runtime._sync_directory,
        ) as sync_directory:
            result = runtime.apply_music3_reset_plan(
                self.pinokio,
                confirmation_token=reset["confirmation_token"],
            )
        self.assertTrue(result["reset"])
        self.assertFalse(current_generation.exists())
        self.assertTrue(Path(result["quarantine"]).is_dir())
        synced = [call.args[0] for call in sync_directory.call_args_list]
        self.assertIn(plan.layout.generations, synced)
        self.assertIn(plan.layout.state, synced)
        self.assertIn(Path(result["quarantine"]), synced)
        self.assertIn(plan.layout.reset_quarantine, synced)

    def test_reset_namespaces_same_named_generation_and_builder_staging(self):
        plan = self.plan()
        self.publish(plan, "staging")
        plan.layout.staging.mkdir(mode=0o700)
        evidence = plan.layout.staging / "builder-evidence"
        evidence.write_text("kept\n", encoding="utf-8")
        evidence.chmod(0o600)
        reset = runtime.build_music3_reset_plan(self.pinokio)

        result = runtime.apply_music3_reset_plan(
            self.pinokio,
            confirmation_token=reset["confirmation_token"],
        )

        quarantine = Path(result["quarantine"])
        self.assertTrue((quarantine / "generations" / "staging").is_dir())
        self.assertEqual(
            (quarantine / "staging" / "builder-evidence").read_text(encoding="utf-8"),
            "kept\n",
        )

    def test_dangling_process_marker_symlink_fails_closed(self):
        plan = self.plan()
        self.publish(plan)
        plan.layout.process_marker.symlink_to(plan.layout.state / "missing-process.json")
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            runtime.music3_runtime_status(self.pinokio)
        with self.assertRaises(runtime.Music3RuntimeConflict):
            runtime.start_music3_runtime(
                self.pinokio,
                port=32123,
                popen_factory=mock.Mock(),
            )

    def test_cli_exposes_only_the_frozen_lifecycle_subcommands(self):
        script_path = APP / "scripts" / "start_music3_runtime.py"
        source = script_path.read_text(encoding="utf-8")
        for command in ("provision", "verify", "start", "status", "reset-plan"):
            self.assertIn(f'add_parser("{command}")', source)
        for forbidden in ("download", "cloudflare", "share", "rented"):
            self.assertNotIn(f'add_parser("{forbidden}")', source)
        self.assertNotIn("pthread_sigmask", source)
        self.assertLess(source.index("signal.signal"), source.index("start_music3_runtime("))
        compile(runtime._SERVER_GATE_CODE, "<music3-server-gate>", "exec")
        compile(runtime._SUPERVISOR_CODE, "<music3-supervisor>", "exec")
        self.assertIn("os.execvpe", runtime._SERVER_GATE_CODE)
        self.assertIn("signal.signal(signal.SIGTERM", runtime._SUPERVISOR_CODE)
        self.assertIn("termination_requested = True", runtime._SUPERVISOR_CODE)
        self.assertIn("if termination_requested:\n    child.terminate()", runtime._SUPERVISOR_CODE)
        self.assertIn("stdin=subprocess.PIPE", runtime._SUPERVISOR_CODE)
        self.assertIn("while group_members():", runtime._SUPERVISOR_CODE)

    def test_cli_records_startup_signal_and_stops_once_without_masking_child(self):
        handlers = {}
        fake = FakeProcess()
        marker = {"base_url": "http://127.0.0.1:32123"}

        def install_handler(number, handler):
            if callable(handler):
                handlers[number] = handler
            return signal.SIG_DFL

        def start(_root, *, port):
            self.assertEqual(port, 32123)
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return fake, marker

        stop = mock.Mock()
        options = runtime_cli._parser().parse_args([
            "start",
            "--pinokio-root",
            str(self.pinokio),
            "--port",
            "32123",
        ])
        with mock.patch.object(runtime_cli.signal, "signal", side_effect=install_handler), mock.patch.object(
            runtime_cli,
            "start_music3_runtime",
            side_effect=start,
        ), mock.patch.object(runtime_cli, "stop_owned_music3_runtime", stop), mock.patch.object(
            runtime_cli,
            "retire_stopped_music3_process_marker",
            return_value=True,
        ), mock.patch.object(runtime_cli, "_print"):
            self.assertEqual(runtime_cli._start(options), 0)
        stop.assert_called_once_with(str(self.pinokio))

    def test_cli_allows_a_later_signal_to_retry_failed_stop(self):
        handlers = {}
        fake = FakeProcess()

        def install_handler(number, handler):
            if callable(handler):
                handlers[number] = handler
            return signal.SIG_DFL

        def wait(_timeout=None):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            handlers[signal.SIGINT](signal.SIGINT, None)
            return 0

        fake.wait = wait
        stop = mock.Mock(side_effect=[
            runtime.Music3RuntimeError("retry"),
            {"stopped": True},
        ])
        options = runtime_cli._parser().parse_args([
            "start", "--pinokio-root", str(self.pinokio), "--port", "32123",
        ])
        with mock.patch.object(runtime_cli.signal, "signal", side_effect=install_handler), mock.patch.object(
            runtime_cli,
            "start_music3_runtime",
            return_value=(fake, {"base_url": "http://127.0.0.1:32123"}),
        ), mock.patch.object(runtime_cli, "stop_owned_music3_runtime", stop), mock.patch.object(
            runtime_cli,
            "retire_stopped_music3_process_marker",
            return_value=True,
        ), mock.patch.object(runtime_cli, "_print"):
            self.assertEqual(runtime_cli._start(options), 0)
        self.assertEqual(stop.call_count, 2)


if __name__ == "__main__":
    unittest.main()
