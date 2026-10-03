"""CPU queue/journal/media and deferred loaded-model H3 recovery contracts."""

# AST execution uses reviewed repository source, never request input.
# ruff: noqa: S102
from __future__ import annotations

import ast
import copy
import hashlib
import hmac
import json
import os
import pickle
import re
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import torch
from safetensors.torch import save
from services import h3_cumulative_recovery as recovery
from services.h3_cumulative_latents import H3CumulativeLatents
from services.h3_cumulative_queue import (
    H3CumulativeQueueDispatch,
    H3QueueAuthority,
    decode_h3_queue_receipt,
    encode_h3_queue_receipt,
    verify_h3_queue_receipt,
)
from services.h3_native_continuation import plan_h3_native_continuation_step
from services.queue_recovery import (
    QueueRecoveryJournal,
    QueueRecoveryValidationError,
    _validate_json_mapping,
)
from services.queue_recovery_adapter import (
    QueueRecoveryAdapterError,
    ensure_project_instance_marker,
    project_instance_digest,
)
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    artifact_descriptor,
    recovery_unit_id,
    validate_artifact_descriptor,
)
from test_h3_cumulative_dispatch import wrapper
from test_minimax_h3_cumulative import fake_model

ROOT = Path(__file__).resolve().parents[1]


class H3CumulativeQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.threads = torch.get_num_threads()
        torch.set_num_threads(1)
        cls.launch = ast.parse((ROOT / "app/launch.py").read_text())
        cls.wgp = ast.parse((ROOT / "app/wgp.py").read_text())

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.threads)

    def setUp(self):
        gate = patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"})
        gate.start()
        self.addCleanup(gate.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)
        self.secret = b"private-test-project-secret"
        self.marker = ensure_project_instance_marker(self.project)
        digest = project_instance_digest(self.secret, self.marker)
        self.authority = H3QueueAuthority("owner-1", digest, "chain-1", "job-1", 64, 64)
        self.identity = self.authority.identity("a" * 64)
        self.settings = {
            "cumulative_append": {"chain_id": "chain-1", "width": 64, "height": 64},
            "generated_frames": 141,
            "published_frames": 141,
            "trim_tail_frames": 0,
            "discard_prefix_frames": 0,
            "native_boundary_conditioning": False,
        }
        self.dependency = recovery_unit_id(
            "job-1", "h3_segment", settings=self.settings
        )
        self.state = H3CumulativeLatents(
            torch.zeros(1, 24, 42, 4, 4),
            torch.zeros(2, 32, 235),
            141,
        )
        self.receipt = encode_h3_queue_receipt(
            recovery.write_h3_cumulative_checkpoint(
                self.project,
                self.state,
                self.identity,
                self.dependency,
            )
        )
        self.job = {
            "id": "job-1",
            "_recovery_owner_digest": "owner-1",
            "_recovery_project_digest": digest,
        }
        self.unit = {
            "kind": "h3_segment",
            "index": 0,
            "variant": 0,
            "state": "completed",
            "unit_id": self.dependency,
            "dependencies": [],
            "settings": self.settings,
            "continuation": self.receipt,
        }
        self.sidecar = {
            "producer_unit_id": self.dependency,
            "producer_unit_kind": "h3_segment",
            "producer_unit_variant": 0,
            "producer_unit_index": 0,
            "producer_unit_dependencies": [],
            "producer_unit_settings": self.settings,
            "producer_unit_continuation": self.receipt,
        }
        (self.project / "one.mp4").write_bytes(b"synthetic-media-no-encoder")
        self.write_sidecar()
        self.job["recovery_cursor"] = {"completed_units": [self.unit]}
        self.commits = []
        names = {
            "_queue_recovery_units",
            "_queue_recovery_unit_matches",
            "_queue_recovery_verify_h3_cumulative",
            "_queue_recovery_checkpoint_unit",
            "_queue_recovery_enrich_h3_continuation",
            "_queue_recovery_existing_project_identity",
        }
        functions = [
            node
            for node in self.launch.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        self.assertEqual({node.name for node in functions}, names)
        self.ns = {
            "os": os,
            "stat": stat,
            "re": re,
            "hmac": hmac,
            "json": json,
            "hashlib": hashlib,
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            "QueueRecoveryAdapterError": QueueRecoveryAdapterError,
            "project_instance_digest": project_instance_digest,
            "_session_secret": lambda: self.secret,
            "recovery_unit_id": recovery_unit_id,
            "validate_artifact_descriptor": validate_artifact_descriptor,
            "_recovery_artifact_descriptor": artifact_descriptor,
            "_queue_recovery_checkpoint": self.commit,
            "_quarantine_recovery_artifact": lambda *args: None,
            "_atomic_write_json": lambda path, value: Path(path).write_text(
                json.dumps(value)
            ),
        }
        exec(
            compile(
                ast.Module(body=functions, type_ignores=[]), "launch-h3-queue", "exec"
            ),
            self.ns,
        )

    def commit(self, job, **updates):
        # Exercise the actual journal's strict JSON validator on every commit.
        _validate_json_mapping(
            updates, max_depth=32, max_nodes=10000, max_string_bytes=10000
        )
        self.commits.append(copy.deepcopy(updates))
        job.update(updates)
        return True

    def write_sidecar(self):
        (self.project / "one.meta.json").write_text(json.dumps(self.sidecar))
        self.unit["artifacts"] = [
            artifact_descriptor(
                self.project,
                basename="one.mp4",
                sidecar_basename="one.meta.json",
                producer_unit_id=self.dependency,
            )
        ]

    def matches(self, **kwargs):
        kwargs.setdefault("expected_h3_cumulative_authority", self.authority)
        return self.ns["_queue_recovery_unit_matches"](
            self.job,
            kind="h3_segment",
            variant=0,
            index=0,
            project_dir=str(self.project),
            **kwargs,
        )

    def checkpoint(self, continuation=None):
        return self.ns["_queue_recovery_checkpoint_unit"](
            self.job,
            kind="h3_segment",
            variant=0,
            index=0,
            project_dir=str(self.project),
            artifact_names=["one.mp4"],
            settings=self.settings,
            continuation=continuation,
            expected_h3_cumulative_authority=self.authority,
        )

    def test_receipt_roundtrip_passes_journal_without_allowing_runtime_fields(self):
        encoded = _validate_json_mapping(
            self.receipt,
            max_depth=32,
            max_nodes=10000,
            max_string_bytes=10000,
        )
        raw, identity = decode_h3_queue_receipt(
            encoded, self.authority, self.dependency
        )
        self.assertEqual(identity, self.identity)
        self.assertEqual(raw["identity"]["runtime_sha256"], "a" * 64)
        with self.assertRaises(QueueRecoveryValidationError):
            _validate_json_mapping(
                raw, max_depth=32, max_nodes=10000, max_string_bytes=10000
            )
        self.assertNotIn(str(self.project), json.dumps(encoded))

    def test_receipt_rejects_other_owner_project_job_chain_canvas_and_dependency(self):
        for key, value in (
            ("owner_id", "other"),
            ("project_id", "other"),
            ("job_id", "other"),
            ("chain_id", "other"),
            ("width", 96),
        ):
            with self.subTest(key=key), self.assertRaises(QueueRecoveryRuntimeError):
                decode_h3_queue_receipt(
                    self.receipt,
                    replace(self.authority, **{key: value}),
                    self.dependency,
                )
        with self.assertRaises(QueueRecoveryRuntimeError):
            decode_h3_queue_receipt(self.receipt, self.authority, "unit:v1:" + "c" * 64)
        invalid = copy.deepcopy(self.receipt)
        invalid["identity"]["runtime_sha256"] = "a" * 64
        with self.assertRaises(QueueRecoveryRuntimeError):
            decode_h3_queue_receipt(invalid, self.authority, self.dependency)

    def test_actual_journal_restart_retains_receipt_and_skip_evidence(self):
        journal_path = self.project / "queue-journal.jsonl"
        journal = QueueRecoveryJournal(journal_path)
        initial = journal.recover()
        journal.commit_job(
            "job-1", self.job, expected_revision=0, expected_epoch=initial.epoch
        )
        restarted = QueueRecoveryJournal(journal_path).recover()
        self.job = restarted.jobs["job-1"]
        self.assertEqual(
            self.job["recovery_cursor"]["completed_units"][0]["continuation"],
            self.receipt,
        )
        self.assertIsNotNone(self.matches())
        self.assertNotIn(b"runtime_sha256", journal_path.read_bytes())
        self.assertNotIn(str(self.project).encode(), journal_path.read_bytes())

    def test_actual_skip_validates_both_media_and_av_without_tensor_restore(self):
        with patch.object(
            recovery, "load", side_effect=AssertionError("allocated tensors")
        ):
            self.assertEqual(self.matches(), self.unit)
        path = self.project / ".maestro-recovery/staging" / self.receipt["basename"]
        path.unlink()
        self.assertIsNone(
            self.matches(consumed_continuations=frozenset([self.dependency]))
        )

    def test_cumulative_skip_requires_independent_trusted_plan_authority(self):
        self.assertIsNone(self.matches(expected_h3_cumulative_authority=None))
        for changed in (
            replace(self.authority, chain_id="other"),
            replace(self.authority, width=96),
        ):
            with self.subTest(authority=changed):
                self.assertIsNone(
                    self.matches(expected_h3_cumulative_authority=changed)
                )

    def test_valid_media_does_not_allow_missing_av_receipt_or_changed_project(self):
        self.unit.pop("continuation")
        self.assertIsNone(self.matches())
        self.unit["continuation"] = self.receipt
        (self.project / ".maestro-project-instance").write_text("c" * 32)
        self.assertIsNone(self.matches())
        (self.project / ".maestro-project-instance").unlink()
        self.assertIsNone(self.matches())

    def test_journal_receipt_must_match_the_sealed_media_sidecar(self):
        self.sidecar.pop("producer_unit_continuation")
        self.write_sidecar()
        self.assertIsNone(self.matches())
        self.sidecar["producer_unit_continuation"] = self.receipt
        self.sidecar["producer_unit_settings"] = {}
        self.write_sidecar()
        self.assertIsNone(self.matches())

    def test_actual_reseal_preserves_av_receipt_and_rejects_replacement(self):
        self.assertEqual(self.checkpoint()["continuation"], self.receipt)
        altered = dict(self.receipt, sha256="c" * 64)
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.checkpoint(altered)
        self.assertEqual(len(self.commits), 1)

    def test_actual_enrichment_binds_receipt_into_sidecar_then_journal(self):
        self.unit.pop("continuation")
        self.sidecar.pop("producer_unit_continuation")
        self.write_sidecar()
        updated = self.ns["_queue_recovery_enrich_h3_continuation"](
            self.job,
            str(self.project),
            self.unit,
            variant=0,
            index=0,
            dependencies=[],
            settings=self.settings,
            continuation=self.receipt,
            expected_h3_cumulative_authority=self.authority,
        )
        self.assertEqual(updated["continuation"], self.receipt)
        self.assertEqual(
            json.loads((self.project / "one.meta.json").read_text())[
                "producer_unit_continuation"
            ],
            self.receipt,
        )
        self.assertEqual(self.matches(), updated)

    def test_invalid_av_cannot_commit_or_enrich_media(self):
        path = self.project / ".maestro-recovery/staging" / self.receipt["basename"]
        path.write_bytes(b"damaged")
        original = (self.project / "one.meta.json").read_bytes()
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.checkpoint(self.receipt)
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.ns["_queue_recovery_enrich_h3_continuation"](
                self.job,
                str(self.project),
                self.unit,
                variant=0,
                index=0,
                dependencies=[],
                settings=self.settings,
                continuation=self.receipt,
                expected_h3_cumulative_authority=self.authority,
            )
        self.assertEqual((self.project / "one.meta.json").read_bytes(), original)
        self.assertEqual(self.commits, [])

    def test_enrichment_preserves_a_conflicting_sealed_sidecar_receipt(self):
        self.unit.pop("continuation")
        self.sidecar["producer_unit_continuation"] = {
            "mode": "last_frame",
            "dependency": self.dependency,
        }
        self.write_sidecar()
        before = (self.project / "one.meta.json").read_bytes()
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.ns["_queue_recovery_enrich_h3_continuation"](
                self.job,
                str(self.project),
                self.unit,
                variant=0,
                index=0,
                dependencies=[],
                settings=self.settings,
                continuation=self.receipt,
                expected_h3_cumulative_authority=self.authority,
            )
        self.assertEqual((self.project / "one.meta.json").read_bytes(), before)
        self.assertEqual(self.commits, [])

    def test_self_consistent_nonfinite_payload_is_rejected_without_tensor_load(self):
        video = self.state.video.clone()
        video[0, 0, 0, 0, 0] = float("nan")
        payload = save(
            {"video": video, "audio": self.state.audio},
            metadata={
                "contract": recovery._CONTRACT,
                "identity": recovery._canonical_json(self.identity.__dict__).decode(),
                "dependency": self.dependency,
                "frame_count": "141",
                "published_frames": "141",
            },
        )
        digest = hashlib.sha256(payload).hexdigest()
        bad = dict(
            self.receipt,
            sha256=digest,
            size=len(payload),
            basename=f"unit-job-1-h3-av-{digest}.safetensors",
        )
        (self.project / ".maestro-recovery/staging" / bad["basename"]).write_bytes(
            payload
        )
        with (
            patch.object(
                recovery, "load", side_effect=AssertionError("allocated tensors")
            ),
            self.assertRaisesRegex(QueueRecoveryRuntimeError, "non-finite"),
        ):
            verify_h3_queue_receipt(
                self.project,
                bad,
                self.authority,
                self.dependency,
                frame_count=141,
                published_frames=141,
            )

    def model(self, digest="a" * 64):
        model = fake_model()
        # Test substitute: real bundle asset binding has its own focused suite.
        model.verified_h3_runtime_sha256 = lambda: digest
        return model

    def test_actual_wgp_short_append_geometry_bypasses_first_clip_minimum_only_privately(
        self,
    ):
        align = next(
            node
            for node in self.wgp.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "align_model_frame_count"
        )
        namespace = {}
        exec(
            compile(ast.Module(body=[align], type_ignores=[]), "wgp-alignment", "exec"),
            namespace,
        )
        definition = {
            "frame_alignment_modulus": 17,
            "frame_alignment_remainder": 5,
            "frames_minimum": 124,
            "frames_maximum": 345,
        }
        self.assertEqual(namespace["align_model_frame_count"](56, definition), 124)
        from services.h3_cumulative_dispatch import H3CumulativeDispatch

        dispatch = H3CumulativeDispatch(frames=56)
        dispatch.begin(
            {
                "model_type": "minimax_h3",
                "video_length": 56,
                "repeat_generation": 1,
                "batch_size": 1,
                "prompt": "scene",
            }
        )
        expressions = [
            node
            for node in ast.walk(self.wgp)
            if isinstance(node, ast.IfExp)
            and isinstance(node.body, ast.Call)
            and isinstance(node.body.func, ast.Name)
            and node.body.func.id == "align_model_frame_count"
            and isinstance(node.orelse, ast.Call)
            and isinstance(node.orelse.func, ast.Attribute)
            and node.orelse.func.attr == "sampling_frames"
        ]
        self.assertEqual(len(expressions), 3)
        for active, expected in ((dispatch, 56), (None, 124)):
            namespace.update(
                _h3_cumulative_dispatch=active,
                video_length=56,
                current_video_length=56,
                model_def=definition,
            )
            for expression in expressions:
                self.assertEqual(
                    eval(
                        compile(
                            ast.Expression(expression), "wgp-private-window", "eval"
                        ),
                        namespace,
                    ),
                    expected,
                )
        with self.assertRaises(ValueError):
            H3CumulativeDispatch(frames=362)

    def run_dispatch(self, dispatch, model, *, success=True):
        def impl(
            *,
            model_type="minimax_h3",
            video_length=141,
            repeat_generation=1,
            batch_size=1,
            image_mode=0,
            prompt="A moving scene",
            multi_prompts_gen_type=2,
            override_profile=5,
            resolution="64x64",
            _h3_cumulative_dispatch=None,
        ):
            # Execute the actual WGP sampler-call keyword expression.
            call = next(
                node
                for node in ast.walk(self.wgp)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "model_kwargs"
            )
            namespace = {
                "_h3_cumulative_dispatch": _h3_cumulative_dispatch,
                "base_model_type": model_type,
                "current_video_length": video_length,
                "model_def": {},
                "repeat_no": 1,
                "window_no": 1,
                "wan_model": model,
                "align_model_frame_count": lambda value, *a, **kw: value,
            }
            kwargs = eval(
                compile(ast.Expression(call), "wgp-loaded-model-call", "eval"),
                namespace,
            )
            samples = model.generate(
                prompt,
                height=64,
                width=64,
                frame_num=video_length,
                sampling_steps=2,
                seed=123,
                custom_settings={"h3_attention_engine": "sdpa"},
                **kwargs,
            )
            _h3_cumulative_dispatch.capture(samples)
            return success

        run, _ = wrapper(impl)
        return run(video_length=dispatch.frames, _h3_cumulative_dispatch=dispatch)

    def test_loaded_model_restore_runs_after_wrapper_begin_then_seals_full_output(self):
        model = self.model()
        step = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=119
        )
        dispatch = H3CumulativeQueueDispatch(
            frames=56,
            project_directory=self.project,
            authority=self.authority,
            previous_receipt=self.receipt,
            previous_dependency=self.dependency,
            step=step,
        )
        self.assertIsNone(dispatch.previous)
        with self.assertRaises(TypeError):
            pickle.dumps(dispatch)
        self.assertTrue(self.run_dispatch(dispatch, model))
        self.assertEqual(dispatch.handoff["state"].frame_count, 175)
        torch.testing.assert_close(
            dispatch.handoff["state"].video[:, :, :42], self.state.video
        )
        sealed = dispatch.seal_completed("unit:v1:" + "c" * 64)
        self.assertEqual(sealed["frame_count"], 175)
        self.assertEqual(sealed["published_frames"], 175)
        self.assertIsNone(dispatch.handoff)
        with self.assertRaises(QueueRecoveryRuntimeError):
            dispatch.seal_completed("unit:v1:" + "c" * 64)

    def test_changed_loaded_bundle_fails_before_sampling(self):
        dispatch = H3CumulativeQueueDispatch(
            frames=56,
            project_directory=self.project,
            authority=self.authority,
            previous_receipt=self.receipt,
            previous_dependency=self.dependency,
            step=plan_h3_native_continuation_step(
                22, 34, absolute_context_start_frame=119
            ),
        )
        model = self.model("b" * 64)
        with patch.object(model, "generate", wraps=model.generate) as generate:
            with self.assertRaises(QueueRecoveryRuntimeError):
                self.run_dispatch(dispatch, model)
            generate.assert_not_called()
        self.assertIsNone(dispatch.previous)
        self.assertIsNone(dispatch.handoff)

    def test_first_capture_and_failure_never_seal_partial_av(self):
        model = self.model()
        dispatch = H3CumulativeQueueDispatch(
            frames=141, project_directory=self.project, authority=self.authority
        )
        self.assertFalse(self.run_dispatch(dispatch, model, success=False))
        with self.assertRaises(QueueRecoveryRuntimeError):
            dispatch.seal_completed(self.dependency)
        self.assertIsNone(dispatch.handoff)
        fresh = H3CumulativeQueueDispatch(
            frames=141, project_directory=self.project, authority=self.authority
        )
        self.assertTrue(self.run_dispatch(fresh, model))
        model.verified_h3_runtime_sha256 = lambda: "c" * 64
        with self.assertRaises(QueueRecoveryRuntimeError):
            fresh.seal_completed(self.dependency)

    def test_legacy_prompt_only_unit_still_skips_without_cumulative_requirements(self):
        self.unit.pop("settings")
        self.unit["continuation"] = {
            "mode": "prompt_only",
            "dependency": self.dependency,
        }
        self.assertEqual(self.matches(), self.unit)


if __name__ == "__main__":
    unittest.main()
