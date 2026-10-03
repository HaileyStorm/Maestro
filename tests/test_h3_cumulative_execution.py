"""CPU evidence for the private worker, AV recovery and last-output publication."""

# Execute reviewed repository AST seams without importing the live server.
# ruff: noqa: S102
from __future__ import annotations

import ast
import copy
import itertools
import json
import os
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_h3_cumulative_queue as queue_tests
import torch
from services import h3_cumulative_recovery as recovery
from services.h3_cumulative_execution import (
    build_h3_cumulative_tasks,
    copy_h3_cumulative_final,
    create_h3_cumulative_dispatch,
    h3_cumulative_authority,
    h3_cumulative_settings,
    prepare_h3_cumulative_request,
    staged_h3_cumulative_descriptor,
)
from services.h3_cumulative_latents import H3CumulativeLatents
from services.h3_cumulative_queue import encode_h3_queue_receipt
from services.h3_native_continuation import (
    audio_tick_at_frame,
    latent_frames_for_video_frames,
)
from services.queue_recovery_adapter import AUTOMATIC_RETIREMENT_STATUSES
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    cleanup_orphan_staged_outputs,
    ensure_recovery_staging_directory,
    promote_recovery_staged_artifact,
    recovery_unit_id,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[1]


def request(**updates):
    return {
        "_h3_cumulative_append": True,
        "model_type": "minimax_h3",
        "resolution": "64x64",
        "video_length": 158,
        "sliding_window_size": 141,
        "prompt": "An adult courier crosses the hall.",
        "repeat_generation": 1,
        "batch_size": 1,
        **updates,
    }


class H3CumulativeExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        queue_tests.H3CumulativeQueueTests.setUpClass()
        cls.launch = queue_tests.H3CumulativeQueueTests.launch

    @classmethod
    def tearDownClass(cls):
        queue_tests.H3CumulativeQueueTests.tearDownClass()

    def setUp(self):
        self.fixture = queue_tests.H3CumulativeQueueTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.job = self.fixture.job
        self.job["workspace"] = "default"
        self.job["params"] = request()
        self.plan = prepare_h3_cumulative_request(self.job["params"])
        self.authority = h3_cumulative_authority(self.job, self.plan, 0)
        self.ns = self.fixture.ns
        extra = {
            "_h3_cumulative_job_plan",
            "_h3_cumulative_final_settings",
            "_h3_true_peak_policy_identity",
            "_publish_h3_cumulative_final",
            "_h3_dependency_closed_recovery_units",
            "_queue_recovery_reconcile_cursor",
            "_queue_recovery_continuation_path",
            "_queue_recovery_completed_h3_graph",
            "_queue_recovery_adopt_staged_h3_cumulative",
        }
        functions = [
            node
            for node in self.launch.body
            if isinstance(node, ast.FunctionDef) and node.name in extra
        ]
        self.assertEqual({node.name for node in functions}, extra)
        self.ns["_sample_campaign_transition_lock"] = threading.RLock()
        self.ns["_recovery_sha256_file"] = sha256_file
        self.ns["ensure_recovery_staging_directory"] = ensure_recovery_staging_directory
        self.ns["promote_recovery_staged_artifact"] = promote_recovery_staged_artifact
        self.ns["_queue_recovery_reconcile_orphan_delivery"] = lambda *args: None
        exec(
            compile(
                ast.Module(body=functions, type_ignores=[]), "cumulative-launch", "exec"
            ),
            self.ns,
        )
        self.job["recovery_cursor"]["completed_units"] = []
        self.units = []
        for index, window in enumerate(self.plan["windows"]):
            settings = h3_cumulative_settings(self.plan, self.authority, index)
            dependencies = []
            if self.units:
                previous = self.units[-1]
                dependencies = [previous["unit_id"]]
                settings.update(
                    predecessor_artifact_hashes=sorted(
                        artifact["sha256"] for artifact in previous["artifacts"]
                    ),
                    predecessor_continuation_sha256=previous["continuation"]["sha256"],
                )
            dependency = recovery_unit_id(
                self.job["id"],
                "h3_segment",
                index=index,
                dependencies=dependencies,
                settings=settings,
            )
            frames = window["cumulative_generated_frames"]
            state = H3CumulativeLatents(
                torch.zeros(1, 24, latent_frames_for_video_frames(frames), 4, 4),
                torch.zeros(2, 32, audio_tick_at_frame(frames)),
                frames,
                published_frames=window["cumulative_published_frames"],
            )
            receipt = encode_h3_queue_receipt(
                recovery.write_h3_cumulative_checkpoint(
                    self.fixture.project,
                    state,
                    self.authority.identity("a" * 64),
                    dependency,
                )
            )
            name = f"window-{index}.mp4"
            (self.fixture.project / name).write_bytes(
                f"complete-container-{index}".encode()
            )
            unit = {
                "kind": "h3_segment",
                "index": index,
                "variant": 0,
                "state": "completed",
                "unit_id": dependency,
                "dependencies": dependencies,
                "settings": settings,
                "continuation": receipt,
            }
            self.write_sidecars([name], recovery_units={name: unit}, task_params={})
            unit["artifacts"] = [
                queue_tests.artifact_descriptor(
                    self.fixture.project,
                    basename=name,
                    sidecar_basename=f"window-{index}.meta.json",
                    producer_unit_id=dependency,
                )
            ]
            self.units.append(unit)
        self.job["recovery_cursor"]["completed_units"] = self.units

    def write_sidecars(self, names, *, recovery_units, task_params, media_paths=None):
        for name in names:
            unit = recovery_units[name]
            # The real writer places a pending marker before stable promotion.
            if media_paths:
                self.assertFalse((self.fixture.project / name).exists())
                self.assertTrue(Path(media_paths[name]).is_file())
            sidecar = {
                f"producer_unit_{key}": unit[key]
                for key in ("kind", "index", "variant", "dependencies", "settings")
            }
            sidecar["producer_unit_id"] = unit["unit_id"]
            if "continuation" in unit:
                sidecar["producer_unit_continuation"] = unit["continuation"]
            sidecar["params"] = task_params
            media = (media_paths or {}).get(name) or self.fixture.project / name
            size, digest = sha256_file(media)
            role = "final" if unit["kind"] == "h3_concat" else "component"
            sidecar.update(
                job_id=self.job["id"],
                workspace="default",
                output_filename=name,
                producer_unit_artifact_names=names,
                producer_media_size=size,
                producer_media_sha256=digest,
                producer_artifact_class=role,
                artifact_class=role,
                private=role == "component",
            )
            (self.fixture.project / f"{Path(name).stem}.meta.json").write_text(
                json.dumps(sidecar)
            )

    def match(self, index=0, kind="h3_segment"):
        return self.ns["_queue_recovery_unit_matches"](
            self.job,
            kind=kind,
            variant=0,
            index=index,
            project_dir=str(self.fixture.project),
            quarantine_invalid=False,
        )

    def test_admission_gate_and_recovery_exclusions_are_independent(self):
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}):
            with self.assertRaisesRegex(ValueError, "disabled"):
                prepare_h3_cumulative_request(request())
            self.assertEqual(
                prepare_h3_cumulative_request(
                    request(custom_settings=None), require_gate=False
                ),
                self.plan,
            )
            for invalid in (
                {"audio_source": "guide.wav"},
                {"batch_size": True},
                {"custom_settings": []},
                {"resolution": "65x64"},
                {"film_grain_intensity": 0.2},
                {"spatial_upsampling": "flashvsr2pass2"},
                {"temporal_upsampling": "rife2"},
                {"voice_clone_enabled": True},
                {"tts_dynaudnorm": True},
            ):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    prepare_h3_cumulative_request(
                        request(**invalid), require_gate=False
                    )

    def test_request_rejects_native_output_limits_before_tensor_allocation(self):
        cases = (
            (
                {
                    "resolution": "8192x8192",
                    "video_length": 73,
                    "sliding_window_size": 73,
                },
                "512 MiB",
            ),
            (
                {
                    "resolution": "864x480",
                    "video_length": 500,
                    "sliding_window_size": 345,
                },
                "2 GiB",
            ),
        )
        for settings, message in cases:
            source = request(**settings)
            original = copy.deepcopy(source)
            with (
                self.subTest(settings=settings),
                patch.object(torch, "empty", side_effect=AssertionError("allocation")),
                patch.object(torch, "zeros", side_effect=AssertionError("allocation")),
                self.assertRaisesRegex(ValueError, message),
            ):
                prepare_h3_cumulative_request(source, require_gate=False)
            self.assertEqual(source, original)

    def test_request_bound_counts_terminal_grid_padding_before_publication_trim(self):
        settings = request(
            resolution="512x512", video_length=668, sliding_window_size=345
        )
        plan = prepare_h3_cumulative_request(settings, require_gate=False)
        self.assertEqual(plan["windows"][-1]["cumulative_generated_frames"], 668)
        # Requested 669 frames fit below 2 GiB, but native sampling/decoding must
        # produce the full legal 685-frame AV state before publication trims it.
        requested_bytes = 4 * (3 * 669 * 512 * 512 + 2 * audio_tick_at_frame(669) * 800)
        self.assertLess(requested_bytes, 2 * 1024**3)
        with self.assertRaisesRegex(ValueError, "2 GiB"):
            prepare_h3_cumulative_request(
                dict(settings, video_length=669), require_gate=False
            )

    def test_tasks_use_variant_major_short_windows_and_serializable_params(self):
        job = copy.deepcopy(self.job)
        job["params"]["repeat_generation"] = 2
        job["params"]["seed"] = 8
        original = copy.deepcopy(job)
        tasks = build_h3_cumulative_tasks(job, self.plan, itertools.count().__next__)
        self.assertEqual(
            [task["params"]["video_length"] for task in tasks], [141, 39, 141, 39]
        )
        self.assertEqual([task["params"]["seed"] for task in tasks], [8, 8, 9, 9])
        self.assertEqual(
            [task["params"]["multi_clip_info"]["output_index"] for task in tasks],
            [0, 0, 1, 1],
        )
        self.assertEqual(job, original)
        json.dumps(tasks)
        for task in tasks:
            self.assertNotIn("_h3_cumulative_append", task["params"])
            self.assertEqual(task["params"]["trim_tail_frames"], 0)
            self.assertTrue(task["params"]["multi_clip_info"]["defer_concat"])
        dispatch = create_h3_cumulative_dispatch(
            self.plan, self.authority, 1, self.fixture.project, self.units[0]
        )
        self.assertEqual(dispatch.frames, 39)
        with self.assertRaises(TypeError):
            json.dumps(dispatch)

    def test_trusted_plan_recovers_media_and_av_without_explicit_journal_authority(
        self,
    ):
        self.assertEqual(self.match(0), self.units[0])
        self.assertEqual(self.match(1), self.units[1])
        self.job["params"]["prompt"] += " Altered source."
        self.assertIsNone(self.match(0))

    def test_predecessor_receipt_and_dependencies_are_required(self):
        self.units[1]["settings"]["predecessor_continuation_sha256"] = "0" * 64
        self.assertIsNone(self.match(1))
        self.units[1]["settings"]["predecessor_continuation_sha256"] = self.units[0][
            "continuation"
        ]["sha256"]
        self.units[1]["dependencies"] = []
        self.assertIsNone(self.match(1))

    def test_canvas_variant_and_gate_off_source_changes_fail_closed(self):
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}):
            self.assertIsNotNone(self.match(0))
            for changes in (
                {"resolution": "96x64"},
                {"audio_source": "foreign.wav"},
                {"video_length": 175},
            ):
                saved = dict(self.job["params"])
                self.job["params"].update(changes)
                self.assertIsNone(self.match(0))
                self.job["params"] = saved
        self.assertNotEqual(
            h3_cumulative_authority(self.job, self.plan, 0).chain_id,
            h3_cumulative_authority(
                {**self.job, "params": {**self.job["params"], "repeat_generation": 2}},
                self.plan,
                1,
            ).chain_id,
        )

    def test_malformed_predecessor_metadata_fails_closed(self):
        predecessor = self.units[0]
        for key, value in (
            ("artifacts", [None]),
            ("artifacts", {}),
            ("continuation", []),
        ):
            with self.subTest(key=key, value=value):
                saved = predecessor[key]
                predecessor[key] = value
                self.assertIsNone(self.match(1))
                predecessor[key] = saved

    def stage_window(self, index):
        unit = self.units[index]
        old = unit["artifacts"][0]["basename"]
        name = f"unit-{self.job['id']}-t{index}.mp4"
        old_meta = self.fixture.project / f"{Path(old).stem}.meta.json"
        meta = json.loads(old_meta.read_text())
        meta["output_filename"] = name
        meta["producer_unit_artifact_names"] = [name]
        path = self.fixture.project / ".maestro-recovery" / "staging" / name
        (self.fixture.project / old).replace(path)
        old_meta.unlink()
        sidecar = self.fixture.project / f"{Path(name).stem}.meta.json"
        sidecar.write_text(json.dumps(meta))
        return path, sidecar

    def run_success_cleanup(self, completed):
        worker = next(
            node
            for node in self.launch.body
            if isinstance(node, ast.FunctionDef) and node.name == "_run_generation"
        )
        cleanup = [
            node
            for node in ast.walk(worker)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "success and job.get('status') == 'completed'"
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "cleanup_orphan_staged_outputs"
                for child in ast.walk(node)
            )
        ]
        self.assertEqual(len(cleanup), 1)
        namespace = {
            "success": True,
            "job": completed,
            "job_id": completed["id"],
            "allocation_success_observations": [],
            "_record_h3_allocation_success_observations": Mock(),
            "_jobs": {
                self.job["id"]: self.job,
                completed["id"]: completed,
                "active-job": {"id": "active-job", "status": "queued"},
                "cancelled-job": {"id": "cancelled-job", "status": "cancelled"},
            },
            "out_dir": str(self.fixture.project),
            "os": os,
            "AUTOMATIC_RETIREMENT_STATUSES": AUTOMATIC_RETIREMENT_STATUSES,
            "cleanup_orphan_staged_outputs": cleanup_orphan_staged_outputs,
        }
        exec(
            compile(
                ast.Module(body=cleanup, type_ignores=[]), "worker-cleanup", "exec"
            ),
            namespace,
        )

    def test_other_job_success_preserves_failed_retry_av_and_partial_staging(self):
        self.job["status"] = "failed"
        staging = Path(ensure_recovery_staging_directory(self.fixture.project))
        failed_partial = staging / f"unit-{self.job['id']}-h3-av-partial.tmp"
        active_partial = staging / "unit-active-job-h3-av-partial.tmp"
        retired_partial = staging / "unit-other-job-h3-av-partial.tmp"
        cancelled_partial = staging / "unit-cancelled-job-h3-av-partial.tmp"
        for path in (
            failed_partial,
            active_partial,
            retired_partial,
            cancelled_partial,
        ):
            path.write_bytes(b"partial checkpoint")
        self.run_success_cleanup({"id": "other-job", "status": "completed"})
        self.assertTrue(failed_partial.exists())
        self.assertTrue(active_partial.exists())
        self.assertFalse(retired_partial.exists())
        self.assertFalse(cancelled_partial.exists())
        for index in range(2):
            self.assertEqual(self.match(index)["unit_id"], self.units[index]["unit_id"])

    def test_completed_chain_av_survives_success_until_startup_retires_snapshot(self):
        stats = {**self.ns["_h3_true_peak_policy_identity"](), "verified": True}
        self.ns["_enforce_deferred_h3_final_audio"] = lambda *args, **kwargs: stats
        self.ns["_publish_h3_cumulative_final"](
            self.job,
            self.plan,
            0,
            str(self.fixture.project),
            write_sidecars=self.write_sidecars,
            abort_check=lambda: None,
            update_job_fn=lambda *args, **kwargs: True,
        )
        self.job["status"] = "completed"
        self.run_success_cleanup(self.job)
        self.run_success_cleanup({"id": "other-job", "status": "completed"})
        events = []

        class Registry(dict):
            def prepare(self, job):
                return dict(job)

            def publish_prepared(self, job_id, job):
                self[job_id] = job

        def materialize(snapshot, projects):
            graph = self.ns["_queue_recovery_completed_h3_graph"](
                snapshot, str(self.fixture.project)
            )
            self.assertIsNotNone(graph)
            events.append("verified_graph")
            return dict(snapshot, out_dir=str(self.fixture.project)), False

        def compact():
            self.assertEqual(events, ["verified_graph"])
            events.append("compacted")

        def cleanup(project, live_ids):
            self.assertEqual(events, ["verified_graph", "compacted"])
            events.append("cleaned")
            return cleanup_orphan_staged_outputs(project, live_ids)

        startup = next(
            node
            for node in self.launch.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_restore_queue_recovery_on_startup"
        )
        namespace = {
            "os": os,
            "AUTOMATIC_RETIREMENT_STATUSES": AUTOMATIC_RETIREMENT_STATUSES,
            "_queue_recovery_workers_started": False,
            "_CREDIT_CLEANUP_PARAM": "_maestro_credit_accounting_cleanup",
            "_queue_recovery_existing_projects": lambda: {
                "default": (str(self.fixture.project), self.authority.project_id)
            },
            "_queue_recovery_restored": types.SimpleNamespace(
                jobs={self.job["id"]: self.job}, global_state={}
            ),
            "_queue_recovery_materialize_job": materialize,
            "_jobs": Registry(),
            "restore_scheduler_state": lambda *args: None,
            "_queue_recovery_coordinator": types.SimpleNamespace(compact=compact),
            "cleanup_orphan_request_manifests": lambda *args: 0,
            "cleanup_orphan_staged_outputs": cleanup,
        }
        exec(
            compile(ast.Module(body=[startup], type_ignores=[]), "startup", "exec"),
            namespace,
        )
        self.assertTrue(namespace["_restore_queue_recovery_on_startup"]())
        self.assertEqual(events, ["verified_graph", "compacted", "cleaned"])
        for unit in self.units:
            path = self.fixture.project / ".maestro-recovery" / "staging"
            self.assertFalse((path / unit["continuation"]["basename"]).exists())

    def test_startup_adopts_staged_chain_without_generation_gate(self):
        staged = [self.stage_window(index)[0] for index in range(2)]
        self.job["recovery_cursor"]["completed_units"] = []
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}):
            self.ns["_queue_recovery_reconcile_cursor"](
                self.job, str(self.fixture.project), adopt_staged=True
            )
        self.assertEqual(len(self.job["recovery_cursor"]["completed_units"]), 2)
        for index, path in enumerate(staged):
            self.assertFalse(path.exists())
            self.assertTrue((self.fixture.project / path.name).is_file())
            self.assertEqual(self.match(index)["unit_id"], self.units[index]["unit_id"])
        self.assertEqual(self.job["output_files"], [])
        self.assertEqual(
            self.ns["_queue_recovery_adopt_staged_h3_cumulative"](
                self.job, str(self.fixture.project)
            ),
            0,
        )

    def test_staged_adoption_rejects_changed_source_media_av_and_privacy(self):
        path, sidecar = self.stage_window(1)
        original = json.loads(sidecar.read_text())
        params = dict(self.job["params"])
        for failure in ("source", "media", "av", "privacy", "predecessor"):
            with self.subTest(failure=failure):
                meta = copy.deepcopy(original)
                self.job["params"] = dict(params)
                self.job["recovery_cursor"]["completed_units"] = [self.units[0]]
                if failure == "source":
                    self.job["params"]["prompt"] += " Changed."
                elif failure == "media":
                    meta["producer_media_sha256"] = "0" * 64
                elif failure == "av":
                    meta["producer_unit_continuation"]["sha256"] = "0" * 64
                elif failure == "privacy":
                    meta["private"] = False
                else:
                    self.job["recovery_cursor"]["completed_units"] = []
                sidecar.write_text(json.dumps(meta))
                self.assertEqual(
                    self.ns["_queue_recovery_adopt_staged_h3_cumulative"](
                        self.job, str(self.fixture.project)
                    ),
                    0,
                )
                self.assertTrue(path.is_file())
                self.assertFalse((self.fixture.project / path.name).exists())

    def test_staged_descriptor_rejects_symlink_and_changed_private_directory(self):
        path, _sidecar = self.stage_window(1)
        unit_id = self.units[1]["unit_id"]
        self.assertEqual(
            staged_h3_cumulative_descriptor(self.fixture.project, path.name, unit_id)[
                "sha256"
            ],
            self.units[1]["artifacts"][0]["sha256"],
        )
        original = path.with_suffix(".saved")
        path.replace(original)
        path.symlink_to(original)
        with self.assertRaises(QueueRecoveryRuntimeError):
            staged_h3_cumulative_descriptor(self.fixture.project, path.name, unit_id)
        path.unlink()
        original.replace(path)
        path.parent.chmod(0o755)
        with self.assertRaises(QueueRecoveryRuntimeError):
            staged_h3_cumulative_descriptor(self.fixture.project, path.name, unit_id)
        path.parent.chmod(0o700)

    def test_staged_discovery_preserves_existing_and_ambiguous_outputs(self):
        path, sidecar = self.stage_window(1)
        self.job["recovery_cursor"]["completed_units"] = [self.units[0]]
        self.ns["_queue_recovery_reconcile_cursor"](self.job, str(self.fixture.project))
        self.assertTrue(path.is_file())
        target = self.fixture.project / path.name
        target.write_bytes(b"existing unrelated winner")
        adopt = self.ns["_queue_recovery_adopt_staged_h3_cumulative"]
        self.assertEqual(adopt(self.job, str(self.fixture.project)), 0)
        self.assertEqual(target.read_bytes(), b"existing unrelated winner")
        target.unlink()
        other = path.with_name(f"unit-{self.job['id']}-t1-other.mp4")
        other.write_bytes(path.read_bytes())
        meta = json.loads(sidecar.read_text())
        meta["output_filename"] = other.name
        meta["producer_unit_artifact_names"] = [other.name]
        other_sidecar = self.fixture.project / f"{other.stem}.meta.json"
        other_sidecar.write_text(json.dumps(meta))
        self.assertEqual(adopt(self.job, str(self.fixture.project)), 0)
        self.assertTrue(path.is_file())
        self.assertTrue(other.is_file())
        self.assertFalse(target.exists())

    def test_cancelled_copy_never_reaches_audio_or_publication(self):
        audio = Mock()
        publication = Mock()

        def cancel():
            raise InterruptedError("cancelled")

        with self.assertRaises(InterruptedError):
            copy_h3_cumulative_final(
                self.job,
                self.plan,
                0,
                self.fixture.project,
                self.units[-1],
                enforce_audio=audio,
                prepare_publication=publication,
                abort_check=cancel,
            )
        audio.assert_not_called()
        publication.assert_not_called()
        self.assertEqual(list(self.fixture.project.glob("cumulative_*.mp4")), [])

    def test_actual_final_helper_copies_terminal_and_restart_reuses_final(self):
        audio_calls = []
        stats = {**self.ns["_h3_true_peak_policy_identity"](), "verified": True}
        self.ns["_enforce_deferred_h3_final_audio"] = lambda job, path, **kwargs: (
            audio_calls.append(Path(path).read_bytes()) or stats
        )
        publish = self.ns["_publish_h3_cumulative_final"]
        args = (self.job, self.plan, 0, str(self.fixture.project))
        kwargs = {
            "write_sidecars": self.write_sidecars,
            "abort_check": lambda: None,
            "update_job_fn": lambda *args, **kwargs: True,
        }
        unit = publish(*args, **kwargs)
        self.assertEqual(audio_calls, [b"complete-container-1"])
        self.assertEqual(unit["settings"]["assembly"], "cumulative_last_output")
        self.assertEqual(unit["dependencies"], [item["unit_id"] for item in self.units])
        self.assertEqual(self.match(kind="h3_concat"), unit)
        self.assertEqual(publish(*args, **kwargs), unit)
        self.assertEqual(len(audio_calls), 1)
        self.job["status"] = "completed"
        graph = self.ns["_queue_recovery_completed_h3_graph"](
            self.job, str(self.fixture.project)
        )
        self.assertIsNotNone(graph)
        self.assertEqual(
            {item["unit_id"] for item in graph["completed_units"]},
            {
                item["unit_id"]
                for item in self.job["recovery_cursor"]["completed_units"]
            },
        )
        # Reconstruct all units from sidecars, including a final whose journal
        # commit was lost after media promotion. AV verification remains real.
        self.job["recovery_cursor"] = {"completed_units": []}
        self.ns["_queue_recovery_reconcile_cursor"](self.job, str(self.fixture.project))
        self.assertEqual(self.match(kind="h3_concat"), unit)

    def test_copy_audio_failure_keeps_snapshot_and_never_promotes_final(self):
        with self.assertRaisesRegex(QueueRecoveryRuntimeError, "audio"):
            copy_h3_cumulative_final(
                self.job,
                self.plan,
                0,
                self.fixture.project,
                self.units[-1],
                enforce_audio=lambda path: {"verified": False},
                abort_check=lambda: None,
            )
        self.assertEqual(
            (self.fixture.project / "window-1.mp4").read_bytes(),
            b"complete-container-1",
        )
        self.assertEqual(list(self.fixture.project.glob("cumulative_*.mp4")), [])

    def test_worker_recovered_terminal_never_reaches_generation(self):
        worker = next(
            node
            for node in self.launch.body
            if isinstance(node, ast.FunctionDef) and node.name == "_run_generation"
        )
        block = next(
            node
            for node in ast.walk(worker)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "h3_cumulative_plan is not None"
            and any(
                isinstance(item, ast.ImportFrom)
                and item.module == "services.h3_cumulative_execution"
                for item in node.body
            )
            and "recovered_cumulative" in ast.unparse(node)
        )
        generation = Mock()
        final = {"artifacts": [{"basename": "final.mp4"}]}
        ns = {
            **self.ns,
            "h3_cumulative_plan": self.plan,
            "recovery_clip_info": {"index": 1, "output_index": 0},
            "job": self.job,
            "out_dir": str(self.fixture.project),
            "gen": {"file_list": []},
            "producer_artifact_roles": {},
            "join_output_file": None,
            "completed": 0,
            "task_no": 2,
            "_publish_cumulative_variant": Mock(return_value=final),
            "generation": generation,
            "_h3_checkpoint_error": QueueRecoveryRuntimeError,
        }
        loop = ast.For(
            target=ast.Name(id="_", ctx=ast.Store()),
            iter=ast.List(elts=[ast.Constant(0)], ctx=ast.Load()),
            body=[
                block,
                ast.Expr(
                    value=ast.Call(
                        func=ast.Name(id="generation", ctx=ast.Load()),
                        args=[],
                        keywords=[],
                    )
                ),
            ],
            orelse=[],
        )
        module = ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[]))
        exec(compile(module, "actual-worker-recovery", "exec"), ns)
        generation.assert_not_called()
        self.assertEqual(ns["completed"], 1)
        self.assertEqual(
            ns["producer_artifact_roles"],
            {"window-1.mp4": "component", "final.mp4": "final"},
        )

    def test_actual_worker_forwards_dispatch_outside_task_json(self):
        handler = next(
            node
            for node in ast.walk(self.launch)
            if isinstance(node, ast.FunctionDef) and node.name == "make_error_handler"
        )
        dispatch = object()
        calls = []

        def generate_video(
            task, send_cmd, *, plugin_data, model_type, _h3_cumulative_dispatch=None
        ):
            calls.append((_h3_cumulative_dispatch, copy.deepcopy(task)))

        ns = {
            "worker_start_lock": threading.Lock(),
            "worker_start_state": {"cancelled": False},
            "worker_started": threading.Event(),
            "time": types.SimpleNamespace(perf_counter=lambda: 1),
            "inspect": __import__("inspect"),
            "wgp": types.SimpleNamespace(generate_video=generate_video),
            "task_h3_turbo_validation_authorized": False,
            "cumulative_dispatch": dispatch,
            "_H3_LONG_STUDIO_MODELS": set(),
            "_run_generation_task_with_llm_exclusion": lambda model, cmd, run: run(),
        }
        exec(
            compile(
                ast.Module(body=[handler], type_ignores=[]),
                "actual-worker-dispatch",
                "exec",
            ),
            ns,
        )
        task = {"params": {"model_type": "minimax_h3"}}
        ns["make_error_handler"](
            task, task["params"], lambda *args: None, {}, cumulative_dispatch=dispatch
        )()
        self.assertIs(calls[0][0], dispatch)
        self.assertEqual(calls[0][1], task)
        json.dumps(task)

    def test_actual_wgp_keeps_short_append_as_one_window(self):
        wgp = queue_tests.H3CumulativeQueueTests.wgp
        impl = next(
            node
            for node in wgp.body
            if isinstance(node, ast.FunctionDef) and node.name == "_generate_video_impl"
        )
        quantize = next(
            node
            for node in impl.body
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "_h3_cumulative_dispatch is not None"
            and "sliding_window_size" in ast.unparse(node)
        )
        channel = create_h3_cumulative_dispatch(
            self.plan, self.authority, 1, self.fixture.project, self.units[0]
        )
        settings = build_h3_cumulative_tasks(
            self.job, self.plan, itertools.count().__next__
        )[1]["params"]
        channel.begin(settings)
        ns = {
            "_h3_cumulative_dispatch": channel,
            "video_length": 39,
            "sliding_window_size": 39,
            "latent_size": 4,
        }
        exec(
            compile(
                ast.Module(body=[quantize], type_ignores=[]),
                "actual-wgp-window",
                "exec",
            ),
            ns,
        )
        self.assertEqual(ns["sliding_window_size"], 39)
        self.assertFalse(ns["video_length"] > ns["sliding_window_size"])
        channel.discard()
        ns.update(_h3_cumulative_dispatch=None, sliding_window_size=39)
        exec(
            compile(
                ast.Module(body=[quantize], type_ignores=[]),
                "ordinary-wgp-window",
                "exec",
            ),
            ns,
        )
        self.assertEqual(ns["sliding_window_size"], 37)

    def test_actual_worker_seals_av_and_media_before_publishing_terminal(self):
        block = next(
            node
            for node in ast.walk(self.launch)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "cumulative_dispatch is not None"
            and "seal_completed" in ast.unparse(node)
        )
        terminal = self.units.pop()
        self.job["recovery_cursor"]["completed_units"] = list(self.units)
        name = "window-1.mp4"
        stage = self.fixture.project / ".stage.mp4"
        (self.fixture.project / name).replace(stage)
        (self.fixture.project / "window-1.meta.json").unlink()
        events = []
        channel = Mock()

        def seal(dependency, **kwargs):
            self.assertEqual(dependency, terminal["unit_id"])
            events.append("av")
            return terminal["continuation"]

        channel.seal_completed.side_effect = seal
        previous = self.units[0]

        def promote(gen, root, paths):
            events.append("media")
            for basename, path in paths.items():
                Path(path).replace(Path(root) / basename)

        def publish(variant):
            events.append("final")
            self.assertEqual(self.match(1)["unit_id"], terminal["unit_id"])
            return {"artifacts": [{"basename": "final.mp4"}]}

        ns = {
            **self.ns,
            "job": self.job,
            "job_id": self.job["id"],
            "out_dir": str(self.fixture.project),
            "cumulative_dispatch": channel,
            "cumulative_variant": 0,
            "cumulative_index": 1,
            "cumulative_authority": self.authority,
            "h3_cumulative_plan": self.plan,
            "_h3_verified_segment_dependency_evidence": lambda *args: (
                [previous["unit_id"]],
                {
                    "predecessor_artifact_hashes": [previous["artifacts"][0]["sha256"]],
                    "predecessor_continuation_sha256": previous["continuation"][
                        "sha256"
                    ],
                },
            ),
            "task_media_names": [name],
            "task_video_names": [name],
            "task_staged_media": {name: str(stage)},
            "_h3_checkpoint_error": QueueRecoveryRuntimeError,
            "is_cancel_requested": lambda job: False,
            "_cumulative_abort_check": lambda: None,
            "_write_output_sidecars": self.write_sidecars,
            "_queue_recovery_promote_staged_outputs": promote,
            "h3_delivery_native_source": False,
            "task_sidecar_params": {},
            "gen": {"file_list": []},
            "producer_artifact_roles": {},
            "join_output_file": None,
            "_publish_cumulative_variant": publish,
        }

        def writer(names, **kwargs):
            events.append("sidecar")
            kwargs.pop("native_source")
            self.assertTrue(kwargs.pop("private_native_parent"))
            self.assertEqual(kwargs["task_params"]["video_length"], 158)
            return self.write_sidecars(names, **kwargs)

        ns["_write_output_sidecars"] = writer
        exec(
            compile(
                ast.Module(body=[block], type_ignores=[]), "actual-worker-seal", "exec"
            ),
            ns,
        )
        self.assertEqual(events, ["av", "sidecar", "media", "final"])
        channel.discard.assert_called_once()


if __name__ == "__main__":
    unittest.main()
