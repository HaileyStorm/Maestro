"""CPU filesystem and fake-model restart contracts for retained H3 AV state."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services import h3_cumulative_recovery as recovery
from services.h3_cumulative_latents import H3CumulativeLatents
from services.h3_native_continuation import plan_h3_native_continuation_step
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    cleanup_orphan_staged_outputs,
)
from test_minimax_h3_cumulative import fake_model


class H3CumulativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.identity = recovery.H3CumulativeIdentity(
            "owner-1", "project-1", "chain-1", "job-1", "a" * 64, 64, 64
        )
        self.dependency = "unit:v1:" + "b" * 64
        self.state = H3CumulativeLatents(
            torch.arange(24 * 42 * 4 * 4, dtype=torch.float32).reshape(1, 24, 42, 4, 4),
            torch.arange(2 * 32 * 235, dtype=torch.float32).reshape(2, 32, 235),
            141,
        )
        self.staging = self.project / ".maestro-recovery" / "staging"

    def write(self, **kwargs):
        return recovery.write_h3_cumulative_checkpoint(
            self.project,
            self.state,
            self.identity,
            self.dependency,
            **kwargs,
        )

    def load(self, receipt, **kwargs):
        return recovery.load_h3_cumulative_checkpoint(
            self.project,
            receipt,
            kwargs.get("identity", self.identity),
            kwargs.get("dependency", self.dependency),
        )

    def test_roundtrip_private_file_json_receipt_and_independent_storage(self):
        receipt = json.loads(json.dumps(self.write()))
        restored = self.load(receipt)
        for field in ("video", "audio"):
            torch.testing.assert_close(
                getattr(restored.state, field),
                getattr(self.state, field),
                rtol=0,
                atol=0,
            )
            self.assertNotEqual(
                getattr(restored.state, field).data_ptr(),
                getattr(self.state, field).data_ptr(),
            )
        self.assertEqual(restored.identity, self.identity)
        self.assertEqual(restored.dependency, self.dependency)
        self.assertEqual(
            stat.S_IMODE((self.staging / receipt["basename"]).stat().st_mode), 0o600
        )
        self.assertEqual(stat.S_IMODE(self.staging.stat().st_mode), 0o700)
        self.assertNotIn(str(self.project), json.dumps(receipt))
        self.assertNotIn("model_token", receipt)
        self.assertEqual(list(self.staging.glob("*.tmp")), [])

    def test_queue_cleanup_preserves_live_job_and_removes_terminal_checkpoint(self):
        receipt = self.write()
        self.assertEqual(
            cleanup_orphan_staged_outputs(self.project, live_job_ids=["job-1"]), 0
        )
        self.load(receipt)
        self.assertEqual(
            cleanup_orphan_staged_outputs(self.project, live_job_ids=[]), 1
        )
        self.assertFalse((self.staging / receipt["basename"]).exists())

    def test_replaced_temporary_entry_is_preserved_on_cancel_and_publication(self):
        def run_case(cancelled):
            with self.subTest(cancelled=cancelled):
                replacement = None
                original_write = os.write

                def replace_after_write(handle, payload):
                    nonlocal replacement
                    count = original_write(handle, payload)
                    if replacement is None:
                        replacement = next(self.staging.glob("*.tmp"))
                        replacement.unlink()
                        replacement.write_bytes(b"foreign replacement")
                    return count

                def cancel():
                    return cancelled and replacement is not None

                expected = InterruptedError if cancelled else QueueRecoveryRuntimeError
                with (
                    patch.object(recovery.os, "write", side_effect=replace_after_write),
                    self.assertRaises(expected),
                ):
                    self.write(abort_check=cancel)
                self.assertEqual(replacement.read_bytes(), b"foreign replacement")
                self.assertEqual(list(self.staging.glob("*.safetensors")), [])
                replacement.unlink()

        for cancelled in (True, False):
            run_case(cancelled)

    def test_identity_and_dependency_mismatches_fail_before_tensor_load(self):
        receipt = self.write()
        variants = (
            replace(self.identity, owner_id="owner-2"),
            replace(self.identity, project_id="project-2"),
            replace(self.identity, chain_id="chain-2"),
            replace(self.identity, job_id="job-2"),
            replace(self.identity, runtime_sha256="c" * 64),
            replace(self.identity, width=96),
        )
        with patch.object(
            recovery, "load", side_effect=AssertionError("must not allocate")
        ):
            for identity in variants:
                with (
                    self.subTest(identity=identity),
                    self.assertRaisesRegex(
                        QueueRecoveryRuntimeError, "identity or dependency"
                    ),
                ):
                    self.load(receipt, identity=identity)
            with self.assertRaisesRegex(
                QueueRecoveryRuntimeError, "identity or dependency"
            ):
                self.load(receipt, dependency="unit:v1:" + "d" * 64)

    def test_tampered_file_and_size_fail_before_tensor_load(self):
        receipt = self.write()
        path = self.staging / receipt["basename"]
        with path.open("r+b") as handle:
            handle.seek(-1, os.SEEK_END)
            handle.write(b"\xff")
        with patch.object(
            recovery, "load", side_effect=AssertionError("must not allocate")
        ):
            with self.assertRaisesRegex(QueueRecoveryRuntimeError, "hash or size"):
                self.load(receipt)
            bad = dict(receipt, size=receipt["size"] + 1)
            with self.assertRaisesRegex(QueueRecoveryRuntimeError, "hash or size"):
                self.load(bad)

    def test_symlink_hardlink_and_directory_escape_fail_closed(self):
        receipt = self.write()
        path = self.staging / receipt["basename"]
        outside = self.project / "outside.safetensors"
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.load(receipt)
        path.unlink()
        os.link(outside, path)
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.load(receipt)
        for name in ("../outside.safetensors", str(outside)):
            with self.assertRaisesRegex(QueueRecoveryRuntimeError, "file receipt"):
                self.load(dict(receipt, basename=name))

    def test_header_metadata_and_shapes_are_checked_before_tensor_load(self):
        receipt = self.write()
        original = (self.staging / receipt["basename"]).read_bytes()
        length = int.from_bytes(original[:8], "little")
        header = json.loads(original[8 : 8 + length])
        for changed in ("frame", "canvas", "shape", "dtype", "extra", "offset"):
            mutated = copy.deepcopy(header)
            if changed == "frame":
                mutated["__metadata__"]["frame_count"] = "124"
            elif changed == "canvas":
                mutated["__metadata__"]["identity"] = "{}"
            elif changed == "shape":
                mutated["video"]["shape"][0] = 2
            elif changed == "dtype":
                mutated["audio"]["dtype"] = "F16"
            elif changed == "extra":
                mutated["unwanted"] = {}
            else:
                mutated["audio"]["data_offsets"][0] = 1
            encoded = json.dumps(mutated).encode()
            payload = (
                len(encoded).to_bytes(8, "little") + encoded + original[8 + length :]
            )
            digest = hashlib.sha256(payload).hexdigest()
            bad = dict(
                receipt,
                basename=f"unit-job-1-h3-av-{digest}.safetensors",
                sha256=digest,
                size=len(payload),
            )
            (self.staging / bad["basename"]).write_bytes(payload)
            with (
                self.subTest(changed=changed),
                patch.object(
                    recovery, "load", side_effect=AssertionError("must not allocate")
                ),
                self.assertRaises(QueueRecoveryRuntimeError),
            ):
                self.load(bad)

    def test_cancellation_and_failed_publication_preserve_existing_checkpoint(self):
        receipt = self.write()
        previous = (self.staging / receipt["basename"]).read_bytes()
        self.state = H3CumulativeLatents(
            self.state.video + 1, self.state.audio + 1, 141
        )
        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            return calls >= 3

        with self.assertRaises(InterruptedError):
            self.write(abort_check=cancel)
        with (
            patch.object(
                recovery.os, "link", side_effect=OSError("injected publication failure")
            ),
            self.assertRaisesRegex(QueueRecoveryRuntimeError, "committed"),
        ):
            self.write()
        self.assertEqual((self.staging / receipt["basename"]).read_bytes(), previous)
        self.assertEqual(list(self.staging.glob("*.tmp")), [])
        self.load(receipt)

    def test_post_publication_failure_leaves_only_unreferenced_sealed_orphan(self):
        with (
            patch.object(
                recovery.os,
                "fsync",
                side_effect=[None, OSError("injected directory sync failure")],
            ),
            self.assertRaisesRegex(QueueRecoveryRuntimeError, "committed"),
        ):
            self.write()
        files = list(self.staging.iterdir())
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.endswith(".safetensors"))
        self.assertEqual(files[0].stat().st_nlink, 1)
        self.assertEqual(list(self.staging.glob("*.tmp")), [])

    def test_collision_never_replaces_existing_file(self):
        receipt = self.write()
        path = self.staging / receipt["basename"]
        payload = path.read_bytes()
        path.write_bytes(b"foreign collision evidence")
        with (
            patch.object(recovery, "save", return_value=payload),
            self.assertRaisesRegex(QueueRecoveryRuntimeError, "collides"),
        ):
            self.write()
        self.assertEqual(path.read_bytes(), b"foreign collision evidence")
        self.assertEqual(list(self.staging.glob("*.tmp")), [])

    def test_directory_replacement_during_read_cannot_restore_tensors(self):
        receipt = self.write()
        original_read = recovery._read_exact_file_at

        def replace_directory(*args, **kwargs):
            payload = original_read(*args, **kwargs)
            self.staging.rename(self.staging.with_name("old-staging"))
            self.staging.mkdir(mode=0o700)
            return payload

        with (
            patch.object(
                recovery, "_read_exact_file_at", side_effect=replace_directory
            ),
            patch.object(
                recovery, "load", side_effect=AssertionError("must not allocate")
            ),
            self.assertRaisesRegex(QueueRecoveryRuntimeError, "changed during access"),
        ):
            self.load(receipt)

    def test_platform_without_directory_handles_fails_before_storage_creation(self):
        with (
            patch.object(recovery, "_DIRECTORY_HANDLES_SUPPORTED", False),
            self.assertRaisesRegex(QueueRecoveryRuntimeError, "this platform"),
        ):
            self.write()
        self.assertFalse((self.project / ".maestro-recovery").exists())

    def test_unsafe_staging_directory_and_missing_read_do_not_create_storage(self):
        receipt = {
            "schema": 1,
            "mode": "cumulative_append",
            "storage": "recovery_staging",
            "basename": f"unit-job-1-h3-av-{'a' * 64}.safetensors",
            "sha256": "a" * 64,
            "size": 100,
            "identity": self.identity.__dict__,
            "dependency": self.dependency,
            "frame_count": 141,
            "published_frames": 141,
        }
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.load(receipt)
        self.assertFalse((self.project / ".maestro-recovery").exists())
        foreign = self.project / "foreign"
        foreign.mkdir()
        (self.project / ".maestro-recovery").symlink_to(
            foreign, target_is_directory=True
        )
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.write()
        self.assertEqual(list(foreign.iterdir()), [])

    def test_trimmed_state_survives_serialization_and_remains_terminal(self):
        self.state = H3CumulativeLatents(self.state.video, self.state.audio, 141, 140)
        restored = self.load(self.write())
        self.assertEqual(
            (restored.state.frame_count, restored.state.published_frames), (141, 140)
        )

    def test_nonfinite_numeric_state_rejected_without_creating_storage(self):
        for value in (float("nan"), float("inf")):
            video = self.state.video.clone()
            video[0, 0, 0, 0, 0] = value
            self.state = H3CumulativeLatents(video, self.state.audio, 141)
            with self.assertRaisesRegex(QueueRecoveryRuntimeError, "non-finite"):
                self.write()
        self.assertFalse((self.project / ".maestro-recovery").exists())

    def test_fresh_model_restores_and_appends_after_previous_instance_release(self):
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}):
            original = fake_model()
            generated = original.generate(
                "scene",
                frame_num=141,
                height=64,
                width=64,
                sampling_steps=2,
                seed=123,
                custom_settings={"h3_attention_engine": "sdpa"},
                _h3_cumulative_capture=True,
            )
            self.state = generated["_h3_cumulative_handoff"]["state"]
            receipt = self.write()
            original.release()
            restored = self.load(json.loads(json.dumps(receipt)))
            fresh = fake_model()
            # This restart test uses CPU model fakes. The actual loader/temp-file
            # binding and rejection paths live in test_h3_runtime_binding.
            with patch.object(
                fresh,
                "verified_h3_runtime_sha256",
                return_value=self.identity.runtime_sha256,
            ):
                handoff = fresh.restore_h3_cumulative_handoff(
                    restored, expected_identity=self.identity
                )
            step = plan_h3_native_continuation_step(
                22, 34, absolute_context_start_frame=119
            )
            next_result = fresh.generate(
                "next scene",
                frame_num=56,
                height=64,
                width=64,
                sampling_steps=2,
                seed=456,
                custom_settings={"h3_attention_engine": "sdpa"},
                _h3_cumulative_capture=True,
                _h3_cumulative_previous=handoff,
                _h3_cumulative_step=step,
            )
            next_state = next_result["_h3_cumulative_handoff"]["state"]
            self.assertEqual(next_state.frame_count, 175)
            torch.testing.assert_close(
                next_state.video[:, :, :42], self.state.video, rtol=0, atol=0
            )
            torch.testing.assert_close(
                next_state.audio[..., :235], self.state.audio, rtol=0, atol=0
            )
            self.assertFalse(
                any(isinstance(value, torch.Tensor) for value in vars(fresh).values())
            )

    def test_restore_gate_identity_and_released_model_fail_closed(self):
        restored = self.load(self.write())
        fresh = fake_model()
        with (
            patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}),
            self.assertRaisesRegex(ValueError, "experimental gate"),
        ):
            fresh.restore_h3_cumulative_handoff(
                restored, expected_identity=self.identity
            )
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}):
            with self.assertRaisesRegex(ValueError, "matching identity"):
                fresh.restore_h3_cumulative_handoff(
                    restored, expected_identity=replace(self.identity, chain_id="other")
                )
            fresh.release()
            with self.assertRaisesRegex(ValueError, "loaded native"):
                fresh.restore_h3_cumulative_handoff(
                    restored, expected_identity=self.identity
                )


if __name__ == "__main__":
    unittest.main()
