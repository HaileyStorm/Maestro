"""Deterministic, model-free H3 delivery transaction regressions."""
from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.oom_detect import (  # noqa: E402
    build_failure_details,
    delivery_oom_info,
    detect_oom,
    normalize_failure_details,
)
from services.output_access import stamp_sidecar_policy  # noqa: E402
from services.queue_recovery_runtime import (  # noqa: E402
    QueueRecoveryRuntimeError,
    artifact_descriptor,
    protected_artifact_descriptor,
    recovery_unit_id,
    sha256_file,
    validate_artifact_descriptor,
    validate_protected_artifact_descriptor,
    replay_delivery_from_protected_native,
)


def _load_launch_symbols(*names: str, namespace: dict | None = None) -> dict:
    source = (APP / "launch.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP / "launch.py"))
    wanted = set(names) | {
        "_h3_delivery_finishing_identity", "_validate_h3_delivery_finishing",
        "_build_h3_delivery_finishing", "_apply_h3_delivery_finishing",
        "_validate_h3_recovery_finishing", "_h3_cow_manual_source_supported", "_h3_cow_child_settings",
    }
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    for node in nodes:
        node.decorator_list = []
    loaded = {
        "json": json,
        "os": os,
        "time": time,
        "uuid": uuid,
        "base64": base64,
        "hashlib": hashlib,
        "_recovery_sha256_file": sha256_file,
        "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
        "stamp_sidecar_policy": stamp_sidecar_policy,
        **(namespace or {}),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP / "launch.py"), "exec"), loaded)
    return loaded


def _sidecar(filename: str) -> dict:
    return {
        "output_filename": filename,
        "params": {"model_type": "minimax_h3_video", "seed": 11},
        "producer_artifact_class": "final",
        "artifact_class": "final",
        "artifact_lineage": "producer-lineage",
        "private": False,
        "explicit": False,
        "workspace": "project-a",
    }


class StructuredFailureDetailsTests(unittest.TestCase):
    def test_vae_cuda_oom_is_safe_structured_and_confident(self):
        error = RuntimeError(
            "CUDA out of memory while reading /private/model and secret prompt"
        )
        details = build_failure_details(
            error,
            stage="vae_decode",
            code="vae_decode_failed",
            segment={"current": 14, "total": 14, "variant": 1},
            window={"current": 19, "total": 19},
            step={"current": 19, "total": 19},
            allocator={
                "device_type": "cuda",
                "free_bytes": 1024,
                "total_bytes": 8192,
                "private_path": "/private/model",
            },
        )
        self.assertEqual(details["stage"], "vae_decode")
        self.assertEqual(details["code"], "cuda_oom")
        self.assertEqual(details["exception_type"], "RuntimeError")
        self.assertTrue(details["is_oom"])
        self.assertEqual(
            details["segment"], {"current": 14, "total": 14, "variant": 1},
        )
        self.assertEqual(details["window"], {"current": 19, "total": 19})
        self.assertEqual(details["step"], {"current": 19, "total": 19})
        self.assertEqual(details["allocator"], {
            "device_type": "cuda", "free_bytes": 1024, "total_bytes": 8192,
        })
        public = json.dumps(details)
        self.assertNotIn("/private", public)
        self.assertNotIn("secret prompt", public)
        self.assertIsNotNone(detect_oom(error, 0.8))

    def test_ffmpeg_and_generic_vae_failures_never_claim_vram(self):
        for error, stage, code in (
            (
                RuntimeError(
                    "ffmpeg exited after host out of memory at /private/output.mp4"
                ),
                "concat",
                "concat_process_failed",
            ),
            (
                ValueError("VAE tensor shape mismatch at /private/model"),
                "vae_decode",
                "vae_decode_failed",
            ),
        ):
            with self.subTest(stage=stage):
                details = build_failure_details(
                    error, stage=stage, code=code,
                )
                self.assertFalse(details["is_oom"])
                self.assertEqual(details["stage"], stage)
                self.assertEqual(details["code"], code)
                self.assertNotIn("allocator", details)
                self.assertNotIn("VRAM", details["detail"])
                self.assertNotIn("/private", json.dumps(details))
                self.assertIsNone(detect_oom(error, 0.8))

    def test_normalizer_drops_content_and_unknown_tokens(self):
        details = normalize_failure_details({
            "stage": "../../private",
            "code": "bad code /private",
            "exception_type": "Runtime Error /private",
            "detail": "secret prompt",
            "is_oom": False,
            "allocator": {"free_bytes": 7},
        })
        self.assertEqual(details, {
            "code": "generation_failed",
            "stage": "generation",
            "detail": "Generation failed.",
            "exception_type": "Exception",
            "is_oom": False,
        })
        self.assertNotIn("private", json.dumps(details))


class H3DeliveryTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out_dir = self.temp.name
        self.job = {
            "id": "job-1",
            "status": "running",
            "session_id": "owner-session",
            "workspace": "project-a",
            "access_policy": {
                "private": False,
                "explicit": False,
                "owner_session_id": None,
            },
            "output_files": [],
        }
        self.files = ["variant-a.mp4", "variant-b.mp4"]
        for index, filename in enumerate(self.files):
            Path(self.out_dir, filename).write_bytes(f"native-{index}".encode())
            Path(self.out_dir, Path(filename).stem + ".meta.json").write_text(
                json.dumps(_sidecar(filename)), encoding="utf-8",
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_copy_on_write_keeps_manual_recovery_compatibility_exclusions(self):
        eligible = _load_launch_symbols(
            "_h3_copy_on_write_delivery_eligible",
        )["_h3_copy_on_write_delivery_eligible"]
        base = {
            "director_final_video_postprocess": False,
            "film_grain_intensity": 0,
            "voice_clone_enabled": False,
            "voice_clone_refs": [],
        }
        self.assertTrue(eligible(True, **base))
        self.assertFalse(eligible(False, **base))
        self.assertFalse(eligible(True, **{**base, "director_final_video_postprocess": True}))
        for change in (
            {"film_grain_intensity": 0.25},
            {"voice_clone_enabled": True, "voice_clone_refs": ["reference.wav"]},
        ):
            with self.subTest(change=change):
                self.assertFalse(eligible(True, **{**base, **change}))
        self.assertTrue(eligible(
            True, **{**base, "voice_clone_enabled": True},
        ))

    def test_transaction_forwards_copy_on_write_to_durable_plan(self):
        planner = Mock(side_effect=RuntimeError("stop before staging"))
        symbols = _load_launch_symbols(
            "_H3DeliveryFailure",
            "_deliver_h3_outputs_transactionally",
            namespace={
                "_queue_recovery_delivery_plan": planner,
                "_queue_recovery_checkpoint_delivery_intent": Mock(),
                "_queue_recovery_checkpoint_delivery_pending": Mock(),
            },
        )
        with self.assertRaises(symbols["_H3DeliveryFailure"]):
            symbols["_deliver_h3_outputs_transactionally"](
                self.job, self.out_dir, [self.files[0]],
                "flashvsr3", "3840x2160", "center_crop",
                copy_on_write=True,
            )
        self.assertTrue(planner.call_args.kwargs["copy_on_write"])

    def test_live_shaped_native_policy_restamp_reseals_without_accepting_media_change(self):
        filename = self.files[0]
        unit_id = recovery_unit_id("job-1", "ordinary_repeat", index=0)
        sidecar_path = Path(self.out_dir, Path(filename).stem + ".meta.json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar.update({
            "job_id": "job-1",
            "producer_unit_id": unit_id,
            "producer_unit_kind": "ordinary_repeat",
            "producer_unit_variant": 0,
            "producer_unit_index": 0,
            "producer_unit_dependencies": [],
            "producer_artifact_class": "final",
            "artifact_class": "final",
        })
        media_size, media_sha256 = sha256_file(Path(self.out_dir, filename))
        sidecar.update({
            "producer_media_size": media_size,
            "producer_media_sha256": media_sha256,
        })
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        stale = artifact_descriptor(
            self.out_dir,
            basename=filename,
            sidecar_basename=sidecar_path.name,
            producer_unit_id=unit_id,
        )
        unit = {
            "artifacts": [stale],
            "dependencies": [],
            "index": 0,
            "kind": "ordinary_repeat",
            "state": "completed",
            "unit_id": unit_id,
            "variant": 0,
        }
        self.job["params"] = {
            "spatial_upsampling": "flashvsr3",
            "delivery_resolution": "3840x2160",
            "delivery_fit": "center_crop",
        }
        self.job["recovery_cursor"] = {"completed_units": [unit]}

        # This is the exact sanctioned refresh that invalidated the live safe-
        # unit descriptor: producer evidence/media stay fixed while delivery
        # makes the native project-private and temporary before protection.
        sidecar.update({
            "owner_session_id": "obsolete-session",
            "artifact_class": "temporary",
            "delivery_native_source": True,
        })
        stamp_sidecar_policy(sidecar, {"private": True}, workspace=self.job["workspace"])
        self.assertNotIn("owner_session_id", sidecar)
        sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        self.assertFalse(validate_artifact_descriptor(
            self.out_dir, stale, producer_unit_id=unit_id,
        ))
        restamped_sidecar_bytes = sidecar_path.read_bytes()

        symbols = _load_launch_symbols(
            "_atomic_write_json",
            "_queue_recovery_expected_artifact_role",
            "_queue_recovery_reseal_delivery_source",
            "_queue_recovery_delivery_plan",
            "_stage_h3_delivery_native_outputs",
            namespace={
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "_RECOVERY_ARTIFACT_ROLES": {"final", "window"},
                "_RECOVERY_UNIT_FIXED_ARTIFACT_ROLES": {},
                "_queue_recovery_units": lambda job: list(
                    (job.get("recovery_cursor") or {}).get("completed_units") or []
                ),
                "_recovery_artifact_descriptor": artifact_descriptor,
                "_recovery_sha256_file": sha256_file,
                "hashlib": hashlib,
                "hmac": hmac,
                "recovery_unit_id": recovery_unit_id,
                "validate_artifact_descriptor": validate_artifact_descriptor,
            },
        )
        plan = symbols["_queue_recovery_delivery_plan"](
            self.job,
            self.out_dir,
            [filename],
            spatial_upsampling="flashvsr3",
            delivery_resolution="3840x2160",
            delivery_fit="center_crop",
        )
        refreshed = plan["staging"][0]["source"]
        self.assertEqual(refreshed["sha256"], stale["sha256"])
        self.assertEqual(refreshed["size"], stale["size"])
        self.assertNotEqual(refreshed["sidecar_sha256"], stale["sidecar_sha256"])
        self.assertTrue(validate_artifact_descriptor(
            self.out_dir, refreshed, producer_unit_id=unit_id,
        ))
        self.assertEqual(sidecar_path.read_bytes(), restamped_sidecar_bytes)

        current_sidecar_bytes = sidecar_path.read_bytes()
        for key, value in (("workspace", "another-project"), ("job_id", "another-job"),
                           ("private", False), ("artifact_class", "final"),
                           ("delivery_native_source", False), ("producer_artifact_class", "unknown")):
            with self.subTest(tampered_field=key):
                tampered = dict(sidecar, **{key: value})
                sidecar_path.write_text(json.dumps(tampered), encoding="utf-8")
                before_reseal = sidecar_path.read_bytes()
                self.assertIsNone(symbols["_queue_recovery_reseal_delivery_source"](
                    self.job, self.out_dir, unit, stale, filename,
                ))
                self.assertEqual(sidecar_path.read_bytes(), before_reseal)
        sidecar_path.write_bytes(current_sidecar_bytes)
        forged_unit_id = recovery_unit_id("job-1", "ordinary_repeat", index=1)
        forged_unit = dict(unit, index=1, unit_id=forged_unit_id)
        forged_sidecar = json.loads(current_sidecar_bytes.decode("utf-8"))
        forged_sidecar.update({
            "producer_unit_id": forged_unit_id,
            "producer_unit_index": 1,
        })
        sidecar_path.write_text(json.dumps(forged_sidecar), encoding="utf-8")
        self.job["recovery_cursor"] = {"completed_units": [forged_unit]}
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError,
            "verified native producer unit",
        ):
            symbols["_queue_recovery_delivery_plan"](
                self.job,
                self.out_dir,
                [filename],
                spatial_upsampling="flashvsr3",
                delivery_resolution="3840x2160",
                delivery_fit="center_crop",
            )
        sidecar_path.write_bytes(current_sidecar_bytes)
        self.job["recovery_cursor"] = {"completed_units": [unit]}

        media_path = Path(self.out_dir, filename)
        original_media = media_path.read_bytes()
        media_path.write_bytes(original_media + b"-changed")
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError,
            "verified native producer unit",
        ):
            symbols["_queue_recovery_delivery_plan"](
                self.job,
                self.out_dir,
                [filename],
                spatial_upsampling="flashvsr3",
                delivery_resolution="3840x2160",
                delivery_fit="center_crop",
            )
        media_path.write_bytes(original_media)

        staged = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, [filename], plan,
        )
        self.assertEqual(len(staged), 1)
        self.assertTrue(Path(staged[0]["native_path"]).is_file())
        self.assertTrue(Path(staged[0]["native_meta"]).is_file())
        self.assertFalse(Path(self.out_dir, filename).exists())

    def test_copy_on_write_delivery_staging_preserves_sealed_parent(self):
        filename = self.files[0]
        unit_id = recovery_unit_id("job-1", "ordinary_repeat", index=0)
        media_path = Path(self.out_dir, filename)
        sidecar_path = Path(self.out_dir, Path(filename).stem + ".meta.json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        size, digest = sha256_file(media_path)
        sidecar.update({
            "job_id": "job-1",
            "producer_unit_id": unit_id,
            "producer_unit_kind": "ordinary_repeat",
            "producer_unit_variant": 0,
            "producer_unit_index": 0,
            "producer_unit_dependencies": [],
            "producer_media_size": size,
            "producer_media_sha256": digest,
        })
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        artifact = artifact_descriptor(
            self.out_dir, basename=filename,
            sidecar_basename=sidecar_path.name, producer_unit_id=unit_id,
        )
        self.job["recovery_cursor"] = {"completed_units": [{
            "artifacts": [artifact], "dependencies": [], "index": 0,
            "kind": "ordinary_repeat", "state": "completed",
            "unit_id": unit_id, "variant": 0,
        }]}
        original_media = media_path.read_bytes()
        original_sidecar = sidecar_path.read_bytes()
        def checkpoint(job, **updates):
            job.update(updates)
            return True

        symbols = _load_launch_symbols(
            "_atomic_write_json",
            "_atomic_create_json",
            "_stage_h3_delivery_native_outputs_v2",
            "_stage_h3_delivery_native_outputs",
            "_queue_recovery_delivery_plan",
            "_queue_recovery_checkpoint_delivery_intent",
            "_queue_recovery_checkpoint_delivery_pending",
            "_queue_recovery_validate_delivery_parents",
            "_queue_recovery_completed_delivery_sidecar",
            "_queue_recovery_checkpoint_delivery_publication",
            "_queue_recovery_reconcile_delivery_publication",
            "_queue_recovery_delivery_pending",
            "_queue_recovery_checkpoint_delivery_completed",
            "_queue_recovery_restore_delivery_staged",
            "_publish_h3_delivery_outputs",
            "_finalize_h3_delivery_publication",
            namespace={
                "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                "_queue_recovery_units": lambda job: list(job["recovery_cursor"]["completed_units"]),
                "_recovery_sha256_file": sha256_file,
                "_recovery_artifact_descriptor": artifact_descriptor,
                "hashlib": hashlib, "hmac": hmac, "re": re,
                "recovery_unit_id": recovery_unit_id,
                "validate_artifact_descriptor": validate_artifact_descriptor,
                "_protected_recovery_artifact_descriptor": protected_artifact_descriptor,
                "validate_protected_artifact_descriptor": validate_protected_artifact_descriptor,
                "_queue_recovery_checkpoint": checkpoint,
                "_queue_recovery_checkpoint_unit": lambda _job, **_kwargs: {"unit_id": plan["unit_id"]},
                "_h3_final_output_integrity": lambda *_args, **_kwargs: {"validation": "valid"},
                "_sample_campaign_transition_lock": threading.RLock(),
                "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                "is_cancel_requested": lambda _job: False,
                "logging": logging,
            },
        )
        plan = symbols["_queue_recovery_delivery_plan"](
            self.job, self.out_dir, [filename],
            spatial_upsampling="flashvsr3",
            delivery_resolution="3840x2160", delivery_fit="center_crop",
            copy_on_write=True,
        )
        self.assertEqual(plan["publication_schema"], 2)
        final_name = plan["staging"][0]["final_basename"]
        self.assertNotEqual(final_name, filename)
        changed_plan = json.loads(json.dumps(plan))
        changed_plan["staging"][0]["final_basename"] = "h3-delivery-wrong.mp4"
        with self.assertRaisesRegex(RuntimeError, "names are invalid"):
            symbols["_stage_h3_delivery_native_outputs"](
                self.job, self.out_dir, [filename], changed_plan,
            )
        self.assertEqual(media_path.read_bytes(), original_media)
        self.assertEqual(sidecar_path.read_bytes(), original_sidecar)
        intent = symbols["_queue_recovery_checkpoint_delivery_intent"](
            self.job, plan,
        )
        self.assertEqual(intent["state"], "staging_native")
        self.assertEqual(intent["publication_schema"], 2)
        staged = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, [filename], plan,
        )
        self.assertEqual(staged[0]["file_name"], final_name)
        self.assertEqual(media_path.read_bytes(), original_media)
        self.assertEqual(sidecar_path.read_bytes(), original_sidecar)
        self.assertTrue(validate_artifact_descriptor(
            self.out_dir, artifact, producer_unit_id=unit_id,
        ))
        self.assertEqual(Path(staged[0]["native_path"]).read_bytes(), original_media)
        self.assertFalse(Path(staged[0]["source_path"]).exists())
        repeated = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, [filename], plan,
        )
        self.assertEqual(repeated[0]["native_path"], staged[0]["native_path"])
        self.assertEqual(media_path.read_bytes(), original_media)
        self.assertEqual(sidecar_path.read_bytes(), original_sidecar)
        pending = symbols["_queue_recovery_checkpoint_delivery_pending"](
            self.job, self.out_dir, repeated, plan,
        )
        self.assertEqual(pending["publication_schema"], 2)
        self.assertEqual(pending["sources"], [artifact])
        restored = symbols["_queue_recovery_restore_delivery_staged"](
            self.job, self.out_dir, pending,
        )
        self.assertEqual(restored[0]["file_name"], final_name)
        self.assertEqual(restored[0]["parent_basename"], filename)
        wrong_work = {
            **pending,
            "work_basenames": [".maestro-delivery-other.work.mp4"],
        }
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError, "staging identity changed",
        ):
            symbols["_queue_recovery_restore_delivery_staged"](
                self.job, self.out_dir, wrong_work,
            )
        other_name = "different-parent.mp4"
        other_unit = recovery_unit_id("job-1", "ordinary_repeat", index=1)
        other_path = Path(self.out_dir, other_name)
        other_path.write_bytes(b"different-parent-media")
        other_size, other_digest = sha256_file(other_path)
        other_meta = dict(sidecar)
        other_meta.update({
            "output_filename": other_name,
            "producer_unit_id": other_unit,
            "producer_unit_index": 1,
            "producer_media_size": other_size,
            "producer_media_sha256": other_digest,
        })
        other_sidecar = Path(self.out_dir, "different-parent.meta.json")
        other_sidecar.write_text(json.dumps(other_meta), encoding="utf-8")
        other_artifact = artifact_descriptor(
            self.out_dir, basename=other_name,
            sidecar_basename=other_sidecar.name,
            producer_unit_id=other_unit,
        )
        substituted = {**pending, "sources": [other_artifact]}
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError, "Sealed delivery parent changed",
        ):
            symbols["_queue_recovery_restore_delivery_staged"](
                self.job, self.out_dir, substituted,
            )
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError, "Sealed delivery parent changed",
        ):
            symbols["_queue_recovery_checkpoint_delivery_completed"](
                self.job, self.out_dir, [final_name], substituted,
            )
        sidecar_path.write_bytes(original_sidecar + b" ")
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError, "Sealed delivery parent changed",
        ):
            symbols["_queue_recovery_restore_delivery_staged"](
                self.job, self.out_dir, pending,
            )
        sidecar_path.write_bytes(original_sidecar)
        self.assertTrue(validate_artifact_descriptor(
            self.out_dir, artifact, producer_unit_id=unit_id,
        ))
        Path(restored[0]["work_path"]).write_bytes(original_media + b"-delivered")
        publisher = lambda job, **updates: bool(job.update(updates) or True)
        foreign_final = Path(self.out_dir, final_name)
        foreign_final.write_bytes(b"foreign-output")
        with self.assertRaises(FileExistsError):
            symbols["_publish_h3_delivery_outputs"](
                self.job, restored,
                completed_delivery={"spatial_upsampling": "flashvsr3"},
                update_job_fn=publisher,
            )
        self.assertEqual(foreign_final.read_bytes(), b"foreign-output")
        self.assertEqual(media_path.read_bytes(), original_media)
        self.assertFalse(Path(restored[0]["source_meta"]).exists())
        foreign_final.unlink()
        with tempfile.TemporaryDirectory() as external_root:
            sentinel = Path(external_root, "sentinel.txt")
            sentinel.write_bytes(b"outside-unchanged")
            rollback_link = Path(restored[0]["rollback_meta"])
            try:
                rollback_link.symlink_to(sentinel)
            except OSError:
                self.skipTest("Symlinks are unavailable on this platform")
            with self.assertRaises(FileExistsError):
                symbols["_publish_h3_delivery_outputs"](
                    self.job, restored,
                    completed_delivery={"spatial_upsampling": "flashvsr3"},
                    update_job_fn=publisher,
                )
            self.assertEqual(sentinel.read_bytes(), b"outside-unchanged")
            self.assertTrue(rollback_link.is_symlink())
            self.assertFalse(Path(restored[0]["source_meta"]).exists())
            rollback_link.unlink()
        delivered = symbols["_publish_h3_delivery_outputs"](
            self.job, restored,
            completed_delivery={"spatial_upsampling": "flashvsr3"},
            update_job_fn=publisher,
            publication_commit_fn=lambda names: bool(
                symbols["_queue_recovery_checkpoint_delivery_completed"](
                    self.job, self.out_dir, names, pending,
                )
            ),
        )
        self.assertEqual(delivered, [final_name])
        self.assertEqual(Path(self.out_dir, final_name).read_bytes(), original_media + b"-delivered")
        self.assertEqual(media_path.read_bytes(), original_media)
        self.assertEqual(sidecar_path.read_bytes(), original_sidecar)
        self.assertTrue(validate_artifact_descriptor(
            self.out_dir, artifact, producer_unit_id=unit_id,
        ))
        symbols["_finalize_h3_delivery_publication"](self.job)
        self.assertFalse(Path(restored[0]["native_path"]).exists())
        self.assertTrue(validate_artifact_descriptor(
            self.out_dir, artifact, producer_unit_id=unit_id,
        ))

    def test_copy_on_write_restart_retracts_only_exact_partial_publication(self):
        from services.atomic_file_publish import (
            PublishedFileDurabilityError,
            publish_file_no_replace,
        )

        def fixture(root, *, count=1):
            filenames = []
            parents = []
            parent_paths = []
            parent_sidecars = []
            units = []
            for index in range(count):
                filename = f"sealed-parent-{index}.mp4"
                filenames.append(filename)
                media_path = Path(root, filename)
                media_path.write_bytes(f"sealed-native-{index}".encode())
                parent_size, parent_hash = sha256_file(media_path)
                parent_unit = recovery_unit_id(
                    "job-1", "ordinary_repeat", index=index,
                )
                parent_meta = Path(root, f"sealed-parent-{index}.meta.json")
                sidecar = _sidecar(filename)
                sidecar.update({
                    "job_id": "job-1",
                    "producer_unit_id": parent_unit,
                    "producer_unit_kind": "ordinary_repeat",
                    "producer_unit_variant": 0,
                    "producer_unit_index": index,
                    "producer_unit_dependencies": [],
                    "producer_media_size": parent_size,
                    "producer_media_sha256": parent_hash,
                })
                parent_meta.write_text(json.dumps(sidecar), encoding="utf-8")
                parent = artifact_descriptor(
                    root, basename=filename,
                    sidecar_basename=parent_meta.name,
                    producer_unit_id=parent_unit,
                )
                parents.append(parent)
                parent_paths.append(media_path)
                parent_sidecars.append(parent_meta)
                units.append({
                    "artifacts": [parent], "dependencies": [], "index": index,
                    "kind": "ordinary_repeat", "state": "completed",
                    "unit_id": parent_unit, "variant": 0,
                })
            job = {
                **self.job,
                "out_dir": root,
                "recovery_cursor": {"completed_units": units},
            }
            def checkpoint(current, **updates):
                current.update(updates)
                return True

            symbols = _load_launch_symbols(
                "_atomic_write_json", "_atomic_create_json",
                "_stage_h3_delivery_native_outputs_v2",
                "_stage_h3_delivery_native_outputs",
                "_queue_recovery_delivery_plan",
                "_queue_recovery_checkpoint_delivery_intent",
                "_queue_recovery_checkpoint_delivery_pending",
                "_queue_recovery_validate_delivery_parents",
                "_queue_recovery_completed_delivery_sidecar",
                "_queue_recovery_checkpoint_delivery_publication",
                "_queue_recovery_reconcile_delivery_publication",
                "_queue_recovery_restore_delivery_staged",
                "_queue_recovery_delivery_pending",
                "_publish_h3_delivery_outputs",
                "_resume_pending_h3_delivery_only",
                namespace={
                    "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
                    "_queue_recovery_units": lambda current: list(
                        current["recovery_cursor"]["completed_units"]
                    ),
                    "_recovery_sha256_file": sha256_file,
                    "_protected_recovery_artifact_descriptor": protected_artifact_descriptor,
                    "validate_protected_artifact_descriptor": validate_protected_artifact_descriptor,
                    "validate_artifact_descriptor": validate_artifact_descriptor,
                    "_queue_recovery_checkpoint": checkpoint,
                    "_queue_recovery_unit_matches": lambda *_args, **_kwargs: None,
                    "_h3_final_output_integrity": lambda *_args, **_kwargs: {
                        "validation": "valid",
                    },
                    "_release_h3_delivery_vram": lambda: [],
                    "_sample_campaign_transition_lock": threading.RLock(),
                    "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                    "is_cancel_requested": lambda _job: False,
                    "logging": logging, "hashlib": hashlib, "hmac": hmac,
                    "re": re, "recovery_unit_id": recovery_unit_id,
                },
            )
            plan = symbols["_queue_recovery_delivery_plan"](
                job, root, filenames, spatial_upsampling="flashvsr3",
                delivery_resolution="3840x2160", delivery_fit="center_crop",
                copy_on_write=True,
            )
            symbols["_queue_recovery_checkpoint_delivery_intent"](job, plan)
            staged = symbols["_stage_h3_delivery_native_outputs"](
                job, root, filenames, plan,
            )
            symbols["_queue_recovery_checkpoint_delivery_pending"](
                job, root, staged, plan,
            )
            for index, item in enumerate(staged):
                Path(item["work_path"]).write_bytes(
                    f"delivered-child-{index}".encode()
                )
            return (
                job, symbols, staged, parents[0],
                parent_paths[0], parent_sidecars[0],
            )

        for crash_at in (
            "private_sidecar", "media_rename", "final_sidecar",
            "sealed_sidecar", "durability_failure",
        ):
            with self.subTest(crash_at=crash_at), tempfile.TemporaryDirectory() as root:
                job, symbols, staged, parent, media_path, parent_meta = fixture(root)
                parent_bytes = media_path.read_bytes()
                parent_meta_bytes = parent_meta.read_bytes()
                item = staged[0]
                final_media = Path(item["source_path"])
                final_meta = Path(item["source_meta"])
                publisher = lambda current, **updates: bool(current.update(updates) or True)
                commit_fn = None
                if crash_at == "private_sidecar":
                    original_create = symbols["_atomic_create_json"]
                    def interrupted_create(path, value):
                        original_create(path, value)
                        raise KeyboardInterrupt("simulated restart")
                    symbols["_atomic_create_json"] = interrupted_create
                    publish_context = patch(
                        "services.atomic_file_publish.publish_file_no_replace",
                        publish_file_no_replace,
                    )
                elif crash_at == "media_rename":
                    def interrupted_publish(source, destination):
                        publish_file_no_replace(source, destination)
                        if destination == item["source_path"]:
                            raise KeyboardInterrupt("simulated restart")
                    publish_context = patch(
                        "services.atomic_file_publish.publish_file_no_replace",
                        interrupted_publish,
                    )
                elif crash_at == "durability_failure":
                    def failed_directory_sync(source, destination):
                        publish_file_no_replace(source, destination)
                        if destination == item["source_path"]:
                            raise PublishedFileDurabilityError(
                                5, "simulated directory sync failure",
                            )
                    publish_context = patch(
                        "services.atomic_file_publish.publish_file_no_replace",
                        failed_directory_sync,
                    )
                elif crash_at == "final_sidecar":
                    original_write = symbols["_atomic_write_json"]
                    def interrupted_final(path, value):
                        original_write(path, value)
                        if path == item["source_meta"]:
                            raise KeyboardInterrupt("simulated restart")
                    symbols["_atomic_write_json"] = interrupted_final
                    publish_context = patch(
                        "services.atomic_file_publish.publish_file_no_replace",
                        publish_file_no_replace,
                    )
                else:
                    def interrupted_seal(names):
                        pending = job["recovery_cursor"]["delivery_pending"]
                        sidecar = json.loads(final_meta.read_text(encoding="utf-8"))
                        size, digest = sha256_file(final_media)
                        sealed = symbols["_queue_recovery_completed_delivery_sidecar"](
                            sidecar, pending=pending, file_names=names,
                            media_size=size, media_sha256=digest,
                        )
                        symbols["_atomic_write_json"](str(final_meta), sealed)
                        raise KeyboardInterrupt("simulated restart")
                    commit_fn = interrupted_seal
                    publish_context = patch(
                        "services.atomic_file_publish.publish_file_no_replace",
                        publish_file_no_replace,
                    )
                expected_error = (
                    PublishedFileDurabilityError
                    if crash_at == "durability_failure" else KeyboardInterrupt
                )
                with publish_context, self.assertRaises(expected_error):
                    symbols["_publish_h3_delivery_outputs"](
                        job, staged,
                        completed_delivery={"spatial_upsampling": "flashvsr3"},
                        update_job_fn=publisher,
                        publication_commit_fn=commit_fn,
                    )
                self.assertIn("publication", job["recovery_cursor"]["delivery_pending"])
                self.assertEqual(
                    final_meta.is_file(), crash_at != "durability_failure",
                )
                self.assertEqual(media_path.read_bytes(), parent_bytes)
                self.assertEqual(parent_meta.read_bytes(), parent_meta_bytes)
                if crash_at == "durability_failure":
                    self.assertFalse(final_media.exists())
                    continue
                restarted = json.loads(json.dumps({
                    key: value for key, value in job.items()
                    if not key.startswith("_h3_delivery")
                }))
                saw_clean_destination = []
                def process_after_reconcile(*_args, **_kwargs):
                    saw_clean_destination.append(
                        not final_media.exists() and not final_meta.exists()
                    )
                    raise KeyboardInterrupt("stopped before GPU work")
                symbols["_process_h3_delivery_from_protected_native"] = (
                    process_after_reconcile
                )
                if crash_at == "private_sidecar":
                    final_media.write_bytes(b"foreign-media")
                    with self.assertRaisesRegex(
                        QueueRecoveryRuntimeError,
                        "destination already exists",
                    ):
                        symbols["_resume_pending_h3_delivery_only"](
                            restarted, update_job_fn=publisher,
                        )
                    self.assertEqual(final_media.read_bytes(), b"foreign-media")
                    self.assertFalse(final_meta.exists())
                    final_media.unlink()
                with self.assertRaisesRegex(
                    KeyboardInterrupt, "stopped before GPU work",
                ):
                    symbols["_resume_pending_h3_delivery_only"](
                        restarted, update_job_fn=publisher,
                    )
                self.assertEqual(saw_clean_destination, [True])
                self.assertFalse(final_media.exists())
                self.assertFalse(final_meta.exists())
                self.assertEqual(media_path.read_bytes(), parent_bytes)
                self.assertEqual(parent_meta.read_bytes(), parent_meta_bytes)
                self.assertTrue(validate_artifact_descriptor(
                    root, parent, producer_unit_id=parent["producer_unit_id"],
                ))

        with tempfile.TemporaryDirectory() as root:
            job, symbols, staged, _parent, _media_path, _parent_meta = fixture(
                root, count=2,
            )
            def update(current, **updates):
                current.update(updates)
                return True

            def stop_after_both_sidecars(_names):
                raise KeyboardInterrupt("simulated restart")

            with self.assertRaises(KeyboardInterrupt):
                symbols["_publish_h3_delivery_outputs"](
                    job, staged,
                    completed_delivery={"spatial_upsampling": "flashvsr3"},
                    update_job_fn=update,
                    publication_commit_fn=stop_after_both_sidecars,
                )
            final_paths = [
                (Path(item["source_path"]), Path(item["source_meta"]))
                for item in staged
            ]
            before = [(media.read_bytes(), meta.read_bytes())
                      for media, meta in final_paths]
            Path(staged[1]["rollback_meta"]).unlink()
            restarted = json.loads(json.dumps({
                key: value for key, value in job.items()
                if not key.startswith("_h3_delivery")
            }))
            restored = symbols["_queue_recovery_restore_delivery_staged"](
                restarted, root,
                restarted["recovery_cursor"]["delivery_pending"],
            )
            with self.assertRaisesRegex(
                QueueRecoveryRuntimeError,
                "Private delivery rollback sidecar is unavailable",
            ):
                symbols["_queue_recovery_reconcile_delivery_publication"](
                    restarted, restored,
                )
            self.assertEqual(
                [(media.read_bytes(), meta.read_bytes())
                 for media, meta in final_paths],
                before,
            )

    def _symbols(self, upscale, fit, *, cancelled=None):
        release = Mock(return_value=["released_h3", "cleared_cuda_cache"])
        cancel = cancelled or (lambda job: bool(job.get("cancel_requested")))

        def update(job, **values):
            if cancel(job):
                return False
            job.update(values)
            return True

        namespace = {
            "logging": logging,
            "wgp": SimpleNamespace(server_config={"vram_safety_coefficient": 0.8}),
            "is_cancel_requested": cancel,
            "update_job": update,
            "_sample_campaign_transition_lock": threading.RLock(),
            "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
            "_release_h3_delivery_vram": release,
            "_apply_spatial_upsampling_to_file": upscale,
            "_apply_delivery_fit_to_file": fit,
            "_persist_h3_delivery_oom_info": Mock(return_value=True),
            "_persist_h3_delivery_failure_details": Mock(return_value=True),
            "_h3_final_output_integrity": Mock(return_value={
                "validation": "valid", "reports": [],
            }),
        }
        symbols = _load_launch_symbols(
            "_H3DeliveryFailure",
            "_atomic_write_json",
            "_atomic_write_bytes",
            "_stage_h3_delivery_native_outputs",
            "_reset_h3_delivery_work",
            "_h3_delivery_native_available",
            "_publish_h3_delivery_outputs",
            "_finalize_h3_delivery_publication",
            "_rollback_h3_delivery_publication",
            "_deliver_h3_outputs_transactionally",
            namespace=namespace,
        )
        return symbols, release

    def _finishing_symbols(self):
        self.job["out_dir"] = self.out_dir
        units = []
        for index, filename in enumerate(self.files):
            unit_id = recovery_unit_id("job-1", "ordinary_repeat", index=index)
            meta_path = Path(self.out_dir, Path(filename).stem + ".meta.json")
            meta = json.loads(meta_path.read_text())
            size, digest = sha256_file(Path(self.out_dir, filename))
            meta.update({
                "job_id": "job-1", "producer_unit_id": unit_id,
                "producer_unit_kind": "ordinary_repeat", "producer_unit_variant": 0,
                "producer_unit_index": index, "producer_unit_dependencies": [],
                "producer_media_size": size, "producer_media_sha256": digest,
            })
            meta_path.write_text(json.dumps(meta))
            units.append({
                "artifacts": [artifact_descriptor(
                    self.out_dir, basename=filename, sidecar_basename=meta_path.name,
                    producer_unit_id=unit_id,
                )], "dependencies": [], "index": index, "kind": "ordinary_repeat",
                "state": "completed", "unit_id": unit_id, "variant": 0,
            })
        self.job["recovery_cursor"] = {"completed_units": units}
        ref = Path(self.out_dir, "voice.wav")
        ref.write_bytes(b"authorized voice")
        access = Path(str(ref) + ".access.json")
        access.write_text(json.dumps({"owner_session_id": "owner-session"}))
        self.job["params"] = {
            "film_grain_intensity": 0.25, "film_grain_saturation": 0.5,
            "voice_clone_enabled": True, "voice_clone_mode": "two",
            "voice_clone_refs": [str(ref)], "delivery_resolution": "3840x2160",
        }
        events = []
        def transform(path, tag):
            events.append((tag, Path(path).read_bytes()))
            Path(path).write_bytes(Path(path).read_bytes() + tag.encode())
        def checkpoint(job, **updates):
            job.update(updates)
            return True
        def inputs(job, _owner):
            result = []
            for index, path in enumerate(job["params"]["voice_clone_refs"]):
                size, digest = sha256_file(path)
                sidecar_size, sidecar_digest = sha256_file(path + ".access.json")
                result.append({
                    "field": f"voice_clone_refs:{index}", "path": path,
                    "size": size, "sha256": digest, "scope": "upload",
                    "sidecar_path": path + ".access.json", "sidecar_size": sidecar_size,
                    "sidecar_sha256": sidecar_digest,
                })
            return result
        def authorize(descriptor, *, owner_digest, **_kwargs):
            return (owner_digest == "owner-session"
                    and descriptor["path"] == str(ref)
                    and sha256_file(ref) == (descriptor["size"], descriptor["sha256"])
                    and sha256_file(access) == (descriptor["sidecar_size"], descriptor["sidecar_sha256"]))
        def unit_checkpoint(job, **values):
            pending = job["recovery_cursor"]["delivery_pending"]
            unit = {"unit_id": pending["unit_id"], **values}
            unit["artifacts"] = [artifact_descriptor(
                self.out_dir, basename=name,
                sidecar_basename=Path(name).stem + ".meta.json",
                producer_unit_id=unit["unit_id"],
            ) for name in values["artifact_names"]]
            events.append(("checkpoint", unit))
            return unit
        namespace = {
            "hashlib": hashlib, "hmac": hmac, "recovery_unit_id": recovery_unit_id,
            "_queue_recovery_units": lambda job: job["recovery_cursor"]["completed_units"],
            "_queue_recovery_checkpoint": checkpoint,
            "_queue_recovery_checkpoint_unit": unit_checkpoint,
            "_queue_recovery_unit_matches": lambda *_args, **_kwargs: None,
            "_queue_recovery_input_descriptors": inputs,
            "_queue_recovery_manifest_validator": authorize,
            "owner_principal_digest": lambda _secret, owner: owner,
            "_session_secret": lambda: "test",
            "validate_artifact_descriptor": validate_artifact_descriptor,
            "validate_protected_artifact_descriptor": validate_protected_artifact_descriptor,
            "_protected_recovery_artifact_descriptor": protected_artifact_descriptor,
            "replay_delivery_from_protected_native": replay_delivery_from_protected_native,
            "_release_h3_delivery_vram": lambda: [],
            "_apply_spatial_upsampling_to_file": lambda path, *_args, **_kwargs: transform(path, "-up"),
            "_apply_delivery_fit_to_file": lambda path, *_args, **_kwargs: transform(path, "-fit"),
            "_apply_film_grain_to_file": lambda path, *_args, **_kwargs: transform(path, "-grain"),
            "_h3_final_output_integrity": lambda *_args, **_kwargs: {"validation": "valid"},
            "_sample_campaign_transition_lock": threading.RLock(),
            "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
            "is_cancel_requested": lambda job: bool(job.get("cancel_requested")),
            "update_job": lambda job, **values: checkpoint(job, **values),
            "finish_job": lambda job, _status, **values: checkpoint(job, **values),
            "try_requeue": lambda *_args, **_kwargs: True,
            "_persist_h3_delivery_failure_details": Mock(return_value=True),
            "logging": logging,
            "wgp": SimpleNamespace(server_config={"vram_safety_coefficient": 0.8}),
        }
        symbols = _load_launch_symbols(
            "_H3DeliveryFailure", "_atomic_write_json", "_atomic_write_bytes", "_atomic_create_json",
            "_stage_h3_delivery_native_outputs_v2", "_stage_h3_delivery_native_outputs",
            "_queue_recovery_delivery_plan", "_queue_recovery_checkpoint_delivery_intent",
            "_queue_recovery_checkpoint_delivery_pending", "_queue_recovery_delivery_pending",
            "_queue_recovery_validate_delivery_parents", "_queue_recovery_completed_delivery_sidecar",
            "_queue_recovery_checkpoint_delivery_publication", "_queue_recovery_reconcile_delivery_publication",
            "_queue_recovery_restore_delivery_staged", "_queue_recovery_checkpoint_delivery_completed",
            "_publish_h3_delivery_outputs", "_process_h3_delivery_from_protected_native",
            "_deliver_h3_outputs_transactionally", "_resume_pending_h3_delivery_only",
            "_retry_h3_delivery_postprocess_only", "_reset_h3_delivery_work",
            "_h3_delivery_native_available", "_finalize_h3_delivery_publication",
            "_rollback_h3_delivery_publication", namespace=namespace,
        )
        voice = ModuleType("postprocessing.voice_clone")
        def clone(*, video_path, strict_in_place, mode, voice_ref_paths, **_kwargs):
            self.assertTrue(strict_in_place)
            self.assertEqual(mode, "single")
            self.assertEqual(voice_ref_paths, [str(ref)])
            transform(video_path, "-voice")
            return True
        voice.apply_voice_clone_to_file = clone
        return symbols, voice, ref, events

    def _deliver_finished(self, symbols, *, commit=None):
        return symbols["_deliver_h3_outputs_transactionally"](
            self.job, self.out_dir, self.files, "flashvsr3", "3840x2160", "center_crop",
            copy_on_write=False,
            publication_commit_fn=commit or (lambda names: bool(
                symbols["_queue_recovery_checkpoint_delivery_completed"](
                    self.job, self.out_dir, names, self.job["recovery_cursor"]["delivery_pending"],
                )
            )),
        )

    def test_transactional_finishing_seals_final_bytes_and_distinct_outcomes(self):
        symbols, voice, ref, events = self._finishing_symbols()
        normal_clone = voice.apply_voice_clone_to_file
        def clone(**kwargs):
            if Path(kwargs["video_path"]).read_bytes().startswith(b"native-1"):
                return False
            return normal_clone(**kwargs)
        voice.apply_voice_clone_to_file = clone
        def grain(path, *_args, **_kwargs):
            if Path(path).read_bytes().startswith(b"native-1"):
                raise RuntimeError("optional encoder unavailable")
            Path(path).write_bytes(Path(path).read_bytes() + b"-grain")
        symbols["_apply_film_grain_to_file"] = grain
        with patch.dict(sys.modules, {"postprocessing.voice_clone": voice}):
            names = self._deliver_finished(symbols)
        for index, name in enumerate(names):
            media = Path(self.out_dir, name)
            self.assertEqual(media.read_bytes(), [b"native-0-up-fit-grain-voice", b"native-1-up-fit"][index])
            meta_path = media.with_suffix(".meta.json")
            meta = json.loads(meta_path.read_text())
            self.assertNotIn(str(ref), meta_path.read_text())
            self.assertNotIn("optional_finishing", meta["delivery_recovery"])
            self.assertNotIn("optional_finishing", meta["delivery_recovery"]["queue_recovery_settings"])
            self.assertEqual(meta["postprocessing"]["steps"][-2:], [
                {"step": "film_grain", "outcome": "applied" if index == 0 else "not_applied"},
                {"step": "voice_clone", "outcome": "applied" if index == 0 else "not_applied"},
            ])
            self.assertTrue(validate_artifact_descriptor(
                self.out_dir, artifact_descriptor(
                    self.out_dir, basename=name, sidecar_basename=meta_path.name,
                    producer_unit_id=meta["producer_unit_id"],
                ),
            ))
            self.assertEqual(sha256_file(media), (meta["producer_media_size"], meta["producer_media_sha256"]))
        self.assertEqual(events[-1][0], "checkpoint")
        for index, item in enumerate(self.job["_h3_delivery_native"]):
            self.assertEqual(Path(item["native_path"]).read_bytes(), f"native-{index}".encode())

    def test_finishing_crash_replay_uses_frozen_contract_and_clean_native(self):
        symbols, voice, ref, events = self._finishing_symbols()
        publisher = symbols["_publish_h3_delivery_outputs"]
        symbols["_publish_h3_delivery_outputs"] = Mock(side_effect=RuntimeError("crash before publication"))
        with patch.dict(sys.modules, {"postprocessing.voice_clone": voice}):
            with self.assertRaises(symbols["_H3DeliveryFailure"]):
                self._deliver_finished(symbols)
        pending = json.loads(json.dumps(self.job["recovery_cursor"]["delivery_pending"]))
        identity = pending["settings"]["optional_finishing"]
        self.assertEqual(identity["voice_clone"]["mode"], "single")
        self.assertNotIn(str(ref), json.dumps(identity))
        self.assertEqual(pending["optional_finishing"]["voice_clone"]["references"][0]["path"], str(ref))
        # A new worker has only the durable cursor, not volatile work or outcomes.
        restarted = {key: value for key, value in self.job.items() if not key.startswith("_h3_delivery")}
        restarted["params"] = {"film_grain_intensity": 0.9, "voice_clone_enabled": True}
        symbols["_publish_h3_delivery_outputs"] = publisher
        with patch.dict(sys.modules, {"postprocessing.voice_clone": voice}):
            self.assertTrue(symbols["_resume_pending_h3_delivery_only"](restarted))
        for index, name in enumerate(restarted["output_files"]):
            self.assertEqual(Path(self.out_dir, name).read_bytes(), f"native-{index}-up-fit-grain-voice".encode())
            meta = json.loads(Path(self.out_dir, Path(name).stem + ".meta.json").read_text())
            self.assertEqual(meta["producer_unit_settings"]["optional_finishing"], identity)
        self.assertEqual([value for tag, value in events if tag == "-up"], [b"native-0", b"native-1"] * 2)

    def test_completed_finishing_adopts_sealed_delivery_after_reference_deletion(self):
        symbols, voice, ref, events = self._finishing_symbols()
        with patch.dict(sys.modules, {"postprocessing.voice_clone": voice}):
            names = self._deliver_finished(symbols)
        unit = next(value for tag, value in events if tag == "checkpoint")
        sealed = [(Path(self.out_dir, name).read_bytes(),
                   Path(self.out_dir, Path(name).stem + ".meta.json").read_bytes()) for name in names]
        ref.unlink()
        def completed_unit(*_args, **_kwargs):
            self.assertTrue(all(validate_artifact_descriptor(
                self.out_dir, descriptor, producer_unit_id=unit["unit_id"],
            ) for descriptor in unit["artifacts"]))
            return unit
        symbols["_queue_recovery_unit_matches"] = completed_unit
        symbols["_process_h3_delivery_from_protected_native"] = Mock(side_effect=AssertionError("completed delivery must not replay"))
        symbols["_queue_recovery_manifest_validator"] = Mock(side_effect=AssertionError("completed delivery must not consume references"))
        restarted = json.loads(json.dumps({key: value for key, value in self.job.items() if not key.startswith("_h3_delivery")}))
        changed = json.loads(json.dumps(restarted))
        changed["recovery_cursor"]["delivery_pending"]["optional_finishing"]["film_grain"]["intensity"] = 0.5
        with self.assertRaises(symbols["_H3DeliveryFailure"]):
            symbols["_resume_pending_h3_delivery_only"](changed)
        self.assertTrue(symbols["_resume_pending_h3_delivery_only"](restarted))
        self.assertEqual(restarted["output_files"], names)
        self.assertNotIn("delivery_pending", restarted["recovery_cursor"])
        self.assertEqual(restarted["recovery_state"], "terminal")
        self.assertEqual([(Path(self.out_dir, name).read_bytes(),
                           Path(self.out_dir, Path(name).stem + ".meta.json").read_bytes()) for name in names], sealed)
        symbols["_process_h3_delivery_from_protected_native"].assert_not_called()
        symbols["_queue_recovery_manifest_validator"].assert_not_called()

    def test_changed_finishing_refs_block_recovery_before_work_or_publication(self):
        symbols, voice, ref, _events = self._finishing_symbols()
        symbols["_apply_spatial_upsampling_to_file"] = Mock(side_effect=RuntimeError("hold native"))
        with self.assertRaises(symbols["_H3DeliveryFailure"]):
            self._deliver_finished(symbols)
        recovery = self.job["_h3_delivery_recovery"]
        upscale = Mock()
        publish = Mock()
        symbols["_apply_spatial_upsampling_to_file"] = upscale
        symbols["_publish_h3_delivery_outputs"] = publish
        original = ref.read_bytes()
        for mutation in ("changed", "missing", "access"):
            with self.subTest(mutation=mutation):
                ref.write_bytes(original)
                Path(str(ref) + ".access.json").write_text(json.dumps({"owner_session_id": "owner-session"}))
                if mutation == "changed":
                    ref.write_bytes(b"changed reference")
                elif mutation == "missing":
                    ref.unlink()
                else:
                    Path(str(ref) + ".access.json").write_text("changed owner")
                with self.assertRaises(symbols["_H3DeliveryFailure"]):
                    symbols["_resume_pending_h3_delivery_only"](self.job)
                child = {**self.job, "params": {}, "_h3_delivery_recovery_source_job": "job-1"}
                with self.assertRaises(symbols["_H3DeliveryFailure"]):
                    symbols["_retry_h3_delivery_postprocess_only"](child, recovery["staged"], recovery)
                self.assertEqual(recovery["manual_retry_count"], 0)
                self.assertFalse(recovery["consumed"])
        upscale.assert_not_called()
        publish.assert_not_called()
        self.assertEqual(self.job["output_files"], [])

    def test_finishing_cancellation_and_ambiguous_mutation_keep_native_private(self):
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                self.out_dir = str(Path(self.temp.name, str(cancelled)))
                Path(self.out_dir).mkdir()
                for index, filename in enumerate(self.files):
                    Path(self.out_dir, filename).write_bytes(f"native-{index}".encode())
                    Path(self.out_dir, Path(filename).stem + ".meta.json").write_text(json.dumps(_sidecar(filename)))
                symbols, voice, _ref, _events = self._finishing_symbols()
                def grain(path, *_args, **_kwargs):
                    Path(path).write_bytes(b"partial finishing")
                    if cancelled:
                        self.job["cancel_requested"] = True
                        raise InterruptedError("cancel during grain")
                    raise RuntimeError("failed after mutation")
                symbols["_apply_film_grain_to_file"] = grain
                with self.assertRaises(InterruptedError if cancelled else symbols["_H3DeliveryFailure"]):
                    self._deliver_finished(symbols)
                self.assertEqual(self.job["output_files"], [])
                for index, item in enumerate(self.job["_h3_delivery_native"]):
                    self.assertEqual(Path(item["native_path"]).read_bytes(), f"native-{index}".encode())
                    self.assertFalse(Path(item["source_path"]).exists())
                self.job.pop("cancel_requested", None)
                self.job.pop("_h3_delivery_native", None)
                self.job.pop("_h3_delivery_recovery", None)

    def test_manual_retry_finishes_from_native_and_refreshes_final_receipts(self):
        symbols, voice, _ref, _events = self._finishing_symbols()
        publisher = symbols["_publish_h3_delivery_outputs"]
        symbols["_publish_h3_delivery_outputs"] = Mock(side_effect=RuntimeError("crash before publication"))
        with patch.dict(sys.modules, {"postprocessing.voice_clone": voice}):
            with self.assertRaises(symbols["_H3DeliveryFailure"]):
                self._deliver_finished(symbols)
        recovery = self.job["_h3_delivery_recovery"]
        symbols["_publish_h3_delivery_outputs"] = publisher
        child = {**self.job, "id": "manual-child", "params": {}, "_h3_delivery_recovery_source_job": "job-1"}
        with patch.dict(sys.modules, {"postprocessing.voice_clone": voice}):
            names = symbols["_retry_h3_delivery_postprocess_only"](child, recovery["staged"], recovery)
        for index, name in enumerate(names):
            media = Path(self.out_dir, name)
            meta_path = media.with_suffix(".meta.json")
            meta = json.loads(meta_path.read_text())
            self.assertEqual(media.read_bytes(), f"native-{index}-up-fit-grain-voice".encode())
            self.assertEqual(meta["job_id"], "job-1")
            self.assertEqual(meta["recovery_job_id"], "manual-child")
            self.assertEqual(meta["postprocessing"]["steps"][-1], {"step": "voice_clone", "outcome": "applied"})
            self.assertEqual(sha256_file(media), (meta["producer_media_size"], meta["producer_media_sha256"]))
            descriptor = artifact_descriptor(
                self.out_dir, basename=name, sidecar_basename=meta_path.name,
                producer_unit_id=meta["producer_unit_id"],
            )
            self.assertTrue(validate_artifact_descriptor(self.out_dir, descriptor))

    def test_legacy_optional_request_blocks_retry_but_can_accept_native(self):
        symbols, _release = self._symbols(Mock(), Mock())
        # Old retained sidecars record requested grain but have no sealed finishing contract.
        for name in self.files:
            meta_path = Path(self.out_dir, Path(name).stem + ".meta.json")
            meta = json.loads(meta_path.read_text())
            meta["params"]["film_grain_intensity"] = 0.25
            meta_path.write_text(json.dumps(meta))
        staged = symbols["_stage_h3_delivery_native_outputs"](self.job, self.out_dir, self.files)
        recovery = {"staged": staged, "delivery_resolution": "3840x2160", "delivery_fit": "center_crop"}
        retry = _load_launch_symbols("_retry_h3_delivery_postprocess_only", namespace=symbols)
        with self.assertRaisesRegex(symbols["_H3DeliveryFailure"], "finishing inputs are missing or changed"):
            retry["_retry_h3_delivery_postprocess_only"](self.job, staged, recovery)
        symbols["_apply_spatial_upsampling_to_file"].assert_not_called()
        symbols["_reset_h3_delivery_work"](staged)
        names = symbols["_publish_h3_delivery_outputs"](
            self.job, staged, recovery_action="accept_native", requested_target="3840x2160",
        )
        for index, name in enumerate(names):
            self.assertEqual(Path(self.out_dir, name).read_bytes(), f"native-{index}".encode())
            meta = json.loads(Path(self.out_dir, Path(name).stem + ".meta.json").read_text())
            self.assertNotIn("postprocessing", meta)

    def test_finishing_contract_bounds_grain_and_preserves_effective_reference_order(self):
        symbols = _load_launch_symbols("_H3DeliveryFailure", namespace={
            "owner_principal_digest": lambda _secret, owner: owner,
            "_session_secret": lambda: "test",
            "_queue_recovery_manifest_validator": lambda descriptor, **_kwargs: (
                sha256_file(descriptor["path"]) == (descriptor["size"], descriptor["sha256"])
            ),
        })
        for grain in (None, {"intensity": float("nan"), "saturation": 0.5},
                      {"intensity": 1.1, "saturation": 0.5}, {"intensity": True, "saturation": 0.5}):
            with self.subTest(grain=grain), self.assertRaises(symbols["_H3DeliveryFailure"]):
                symbols["_validate_h3_delivery_finishing"](self.job, {"schema_version": 1, "film_grain": grain})
        references = [Path(self.out_dir, f"voice-{index}.wav") for index in range(3)]
        for index, path in enumerate(references):
            path.write_bytes(f"voice-{index}".encode())
        def inputs(job, _owner):
            return [{"path": path, "size": sha256_file(path)[0], "sha256": sha256_file(path)[1], "scope": "upload"}
                    for path in job["params"]["voice_clone_refs"]]
        symbols["_queue_recovery_input_descriptors"] = inputs
        params = {"voice_clone_enabled": True, "voice_clone_mode": "two", "voice_clone_refs": [str(path) for path in references]}
        first = symbols["_build_h3_delivery_finishing"](self.job, params)
        self.assertEqual([ref["path"] for ref in first["voice_clone"]["references"]], params["voice_clone_refs"][:2])
        references[-1].unlink()  # An unused third reference is not consumed.
        second = symbols["_build_h3_delivery_finishing"](self.job, {**params, "voice_clone_refs": params["voice_clone_refs"][:2][::-1]})
        self.assertNotEqual(symbols["_h3_delivery_finishing_identity"](first), symbols["_h3_delivery_finishing_identity"](second))

    def test_strict_voice_remux_failure_cleans_owned_temp_without_sibling(self):
        tree = ast.parse((APP / "postprocessing/voice_clone.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_ffmpeg_remux_audio")
        import subprocess
        namespace = {"os": os, "tempfile": tempfile, "subprocess": subprocess}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "strict-voice-remux", "exec"), namespace)
        path = str(Path(self.out_dir, self.files[0]))
        before = Path(path).read_bytes()
        def ffmpeg(command, **_kwargs):
            Path(command[-1]).write_bytes(b"converted audio")
            return SimpleNamespace(returncode=0)
        with patch.object(subprocess, "run", side_effect=ffmpeg), patch.object(os, "replace", side_effect=PermissionError("locked")), patch("time.sleep"), patch("gc.collect"):
            self.assertFalse(namespace["_ffmpeg_remux_audio"](path, "audio.wav", strict_in_place=True))
        self.assertEqual(Path(path).read_bytes(), before)
        self.assertFalse(any("voicecloned" in name for name in os.listdir(self.out_dir)))

    def test_first_delivery_oom_releases_and_retries_same_native_files_once(self):
        calls = []

        def upscale(
            path, method, job=None, *, abort_check=None, update_job_fn=None,
        ):
            calls.append((Path(path).name, method))
            if len(calls) == 1:
                raise RuntimeError("CUDA out of memory at /secret/model/path")
            with open(path, "ab") as handle:
                handle.write(b"-upscaled")

        def fit(path, resolution, mode, job=None):
            with open(path, "ab") as handle:
                handle.write(b"-fit")

        symbols, release = self._symbols(upscale, fit)
        delivered = symbols["_deliver_h3_outputs_transactionally"](
            self.job, self.out_dir, self.files,
            "flashvsr3", "3840x2160", "center_crop",
        )
        symbols["_finalize_h3_delivery_publication"](self.job)

        self.assertEqual(delivered, self.files)
        self.assertEqual(release.call_count, 2)
        self.assertEqual(len(calls), 3)
        for index, filename in enumerate(self.files):
            self.assertEqual(
                Path(self.out_dir, filename).read_bytes(),
                f"native-{index}".encode() + b"-upscaled-fit",
            )
            meta = json.loads(Path(
                self.out_dir, Path(filename).stem + ".meta.json",
            ).read_text(encoding="utf-8"))
            self.assertEqual(meta["postprocessing"], {
                "version": 1,
                "steps": [
                    {"step": "upscale", "outcome": "applied", "method": "flashvsr3"},
                    {"step": "delivery_fit", "outcome": "applied"},
                ],
            })
        self.assertFalse(any(name.startswith(".maestro-delivery-") for name in os.listdir(self.out_dir)))

    def test_second_oom_is_path_redacted_and_retains_private_owned_native(self):
        def upscale(
            path, method, job=None, *, abort_check=None, update_job_fn=None,
        ):
            raise RuntimeError(f"CUDA out of memory while reading {path}")

        symbols, release = self._symbols(upscale, Mock())
        failure_type = symbols["_H3DeliveryFailure"]
        with self.assertRaises(failure_type) as caught:
            symbols["_deliver_h3_outputs_transactionally"](
                self.job, self.out_dir, self.files,
                "flashvsr3", "3840x2160", "center_crop",
            )

        info = caught.exception.oom_info
        self.assertEqual(release.call_count, 2)
        self.assertEqual(info["stage"], "h3_delivery")
        self.assertEqual(info["requested_target"], "3840x2160")
        self.assertEqual(info["retry_count"], 1)
        self.assertTrue(info["native_available"])
        self.assertTrue(info["recoverable"])
        self.assertNotIn(self.out_dir, json.dumps(info))
        persisted = symbols["_persist_h3_delivery_failure_details"]
        persisted.assert_called_once()
        self.assertTrue(persisted.call_args.args[1]["is_oom"])
        self.assertIs(persisted.call_args.args[2], info)
        hidden_media = [
            name for name in os.listdir(self.out_dir)
            if name.startswith(".maestro-delivery-")
            and ".native." in name
            and not name.endswith(".meta.json")
        ]
        self.assertEqual(len(hidden_media), 2)
        for name in hidden_media:
            meta = json.loads(Path(
                self.out_dir, os.path.splitext(name)[0] + ".meta.json",
            ).read_text(encoding="utf-8"))
            self.assertTrue(meta["private"])
            self.assertEqual(
                meta["delivery_recovery"]["owner_session_id"], "owner-session",
            )
            self.assertEqual(meta["artifact_class"], "temporary")
        self.assertEqual(self.job["output_files"], [])

    def test_cancellation_wins_over_an_oom_and_never_publishes_final(self):
        def upscale(
            path, method, job=None, *, abort_check=None, update_job_fn=None,
        ):
            job["cancel_requested"] = True
            raise RuntimeError("CUDA out of memory")

        symbols, release = self._symbols(upscale, Mock())
        with self.assertRaises(InterruptedError):
            symbols["_deliver_h3_outputs_transactionally"](
                self.job, self.out_dir, self.files,
                "flashvsr3", "3840x2160", "center_crop",
            )
        self.assertEqual(release.call_count, 1)
        self.assertEqual(self.job["output_files"], [])
        self.assertFalse(any(Path(self.out_dir, name).exists() for name in self.files))

    def test_exact_fit_failure_has_no_lower_quality_fallback(self):
        fit = Mock(side_effect=RuntimeError("exact canvas mismatch"))
        symbols, release = self._symbols(Mock(), fit)
        failure_type = symbols["_H3DeliveryFailure"]
        with self.assertRaises(failure_type) as caught:
            symbols["_deliver_h3_outputs_transactionally"](
                self.job, self.out_dir, self.files,
                "flashvsr3", "3840x2160", "center_crop",
            )
        self.assertIsNone(caught.exception.oom_info)
        self.assertEqual(release.call_count, 1)
        self.assertEqual(fit.call_count, 1)
        self.assertEqual(self.job["output_files"], [])
        persisted = symbols["_persist_h3_delivery_failure_details"]
        persisted.assert_called_once()
        self.assertEqual(persisted.call_args.args[1]["stage"], "delivery")
        self.assertFalse(persisted.call_args.args[1]["is_oom"])
        self.assertIsNone(persisted.call_args.args[2])

    def test_persisted_delivery_failure_is_safe_and_does_not_invent_oom(self):
        native_meta = Path(self.out_dir, ".native.meta.json")
        native_meta.write_text(json.dumps({
            "delivery_recovery": {"schema_version": 1},
        }), encoding="utf-8")
        job = {"_h3_delivery_recovery": {"staged": [
            {"native_meta": str(native_meta)},
        ]}}
        symbols = _load_launch_symbols(
            "_atomic_write_json",
            "_atomic_write_bytes",
            "_persist_h3_delivery_failure_details",
        )
        self.assertTrue(symbols["_persist_h3_delivery_failure_details"](
            job,
            {
                "stage": "publication",
                "code": "publication_failed",
                "exception_type": "RuntimeError",
                "detail": "/private/path and prompt",
                "is_oom": False,
            },
        ))
        recovery = json.loads(native_meta.read_text(encoding="utf-8"))[
            "delivery_recovery"
        ]
        self.assertNotIn("oom_info", recovery)
        self.assertEqual(recovery["failure_details"]["stage"], "publication")
        self.assertFalse(recovery["failure_details"]["is_oom"])
        self.assertNotIn("/private", json.dumps(recovery))

    def test_staging_metadata_fault_restores_original_media_and_sidecar(self):
        exact_sidecars = {}
        for filename in self.files:
            path = Path(self.out_dir, Path(filename).stem + ".meta.json")
            raw = ("{\n  \"output_filename\": \"" + filename
                   + "\", \"artifact_class\": \"final\"\n}\n").encode()
            path.write_bytes(raw)
            exact_sidecars[filename] = raw
        symbols, _ = self._symbols(Mock(), Mock())
        symbols["_atomic_write_json"] = Mock(
            side_effect=OSError("injected metadata failure"),
        )
        with self.assertRaises(OSError):
            symbols["_stage_h3_delivery_native_outputs"](
                self.job, self.out_dir, self.files,
            )
        for filename in self.files:
            self.assertTrue(Path(self.out_dir, filename).is_file())
            self.assertTrue(Path(
                self.out_dir, Path(filename).stem + ".meta.json",
            ).is_file())
            self.assertEqual(Path(
                self.out_dir, Path(filename).stem + ".meta.json",
            ).read_bytes(), exact_sidecars[filename])
        self.assertFalse(any(name.startswith(".maestro-delivery-") for name in os.listdir(self.out_dir)))

    def test_delivery_wraps_path_bearing_staging_fault_in_safe_error(self):
        Path(self.out_dir, "variant-a.meta.json").unlink()
        symbols, _ = self._symbols(Mock(), Mock())
        failure_type = symbols["_H3DeliveryFailure"]
        with self.assertRaises(failure_type) as caught:
            symbols["_deliver_h3_outputs_transactionally"](
                self.job, self.out_dir, self.files,
                "flashvsr3", "3840x2160", "center_crop",
            )
        self.assertEqual(
            str(caught.exception),
            "Unable to protect native H3 outputs for delivery",
        )
        self.assertNotIn(self.out_dir, str(caught.exception))

    def test_cancel_during_multioutput_commit_rolls_back_partial_final(self):
        checks = {"count": 0}

        def cancel_during_second_publish(_job):
            checks["count"] += 1
            return checks["count"] >= 3

        symbols, _ = self._symbols(
            Mock(), Mock(), cancelled=cancel_during_second_publish,
        )
        staged = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, self.files,
        )
        symbols["_reset_h3_delivery_work"](staged)
        with self.assertRaises(InterruptedError):
            symbols["_publish_h3_delivery_outputs"](self.job, staged)
        self.assertFalse(any(Path(self.out_dir, name).exists() for name in self.files))
        self.assertTrue(symbols["_h3_delivery_native_available"](staged))

    def test_locked_partial_final_retains_private_sidecar_on_rollback(self):
        checks = {"count": 0}

        def cancel_during_second_publish(_job):
            checks["count"] += 1
            return checks["count"] >= 3

        symbols, _ = self._symbols(
            Mock(), Mock(), cancelled=cancel_during_second_publish,
        )
        staged = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, self.files,
        )
        symbols["_reset_h3_delivery_work"](staged)
        real_replace = os.replace

        def locked_rollback(source, destination):
            if source == staged[0]["source_path"] and destination == staged[0]["work_path"]:
                raise PermissionError("injected viewer lock")
            return real_replace(source, destination)

        with patch("os.replace", side_effect=locked_rollback):
            with self.assertRaises(InterruptedError):
                symbols["_publish_h3_delivery_outputs"](self.job, staged)
        self.assertTrue(Path(staged[0]["source_path"]).is_file())
        retained = json.loads(Path(staged[0]["source_meta"]).read_text(encoding="utf-8"))
        self.assertTrue(retained["private"])
        self.assertEqual(retained["workspace"], "project-a")
        self.assertEqual(retained["artifact_class"], "temporary")

    def test_invalid_delivered_media_rolls_back_before_final_publication(self):
        symbols, _ = self._symbols(Mock(), Mock())
        integrity = Mock(return_value={"validation": "invalid", "reports": []})
        symbols["_h3_final_output_integrity"] = integrity
        self.job["h3_segment_plan"] = {"published_frames": 124, "fps": 24}
        self.job["params"] = {"delivery_resolution": "1920x1080"}
        staged = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, self.files,
        )
        symbols["_reset_h3_delivery_work"](staged)
        with self.assertRaises(symbols["_H3DeliveryFailure"]) as caught:
            symbols["_publish_h3_delivery_outputs"](self.job, staged)
        self.assertEqual(caught.exception.code, "h3_output_integrity_failed")
        integrity.assert_called_once_with(
            self.out_dir, self.files, expected_frames=124, expected_fps=24.0,
            expected_resolution=(1920, 1080),
        )
        self.assertEqual(self.job["output_files"], [])
        self.assertFalse(any(Path(self.out_dir, name).exists() for name in self.files))
        for item in staged:
            sidecar = json.loads(Path(item["source_meta"]).read_text(encoding="utf-8"))
            self.assertTrue(sidecar["private"])
            self.assertEqual(sidecar["artifact_class"], "temporary")

    def test_cancel_after_lifecycle_update_retracts_and_rolls_back_final(self):
        def upscale(
            path, method, job=None, *, abort_check=None, update_job_fn=None,
        ):
            with open(path, "ab") as handle:
                handle.write(b"-upscaled")

        symbols, _ = self._symbols(upscale, Mock())
        ordinary_update = symbols["update_job"]

        def update_then_cancel(job, **values):
            result = ordinary_update(job, **values)
            if values.get("output_files") == self.files:
                job["cancel_requested"] = True
            return result

        symbols["update_job"] = update_then_cancel
        with self.assertRaises(InterruptedError):
            symbols["_deliver_h3_outputs_transactionally"](
                self.job, self.out_dir, self.files,
                "flashvsr3", "3840x2160", "center_crop",
            )
        self.assertEqual(self.job["output_files"], [])
        self.assertFalse(any(Path(self.out_dir, name).exists() for name in self.files))
        self.assertTrue(symbols["_h3_delivery_native_available"](
            self.job["_h3_delivery_native"],
        ))

    def test_accept_native_publishes_accurate_recovery_sidecar(self):
        symbols, _ = self._symbols(Mock(), Mock())
        symbols["wgp"].get_video_info = lambda _path: (25.0, 1344, 768, 5.0)
        staged = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, self.files,
        )
        symbols["_reset_h3_delivery_work"](staged)
        self.job["_h3_delivery_recovery_source_job"] = "job-1"
        self.job["id"] = "recovery-child"
        outputs = symbols["_publish_h3_delivery_outputs"](
            self.job,
            staged,
            recovery_action="accept_native",
            requested_target="3840x2160",
        )
        self.assertEqual(outputs, self.files)
        for filename in self.files:
            meta = json.loads(Path(
                self.out_dir, Path(filename).stem + ".meta.json",
            ).read_text(encoding="utf-8"))
            self.assertEqual(meta["job_id"], "job-1")
            self.assertEqual(meta["producer_job_id"], "job-1")
            self.assertEqual(meta["recovery_job_id"], "recovery-child")
            self.assertEqual(meta["artifact_lineage"], "producer-lineage")
            self.assertEqual(meta["delivery_recovery"]["action"], "accept_native")
            self.assertEqual(meta["delivery_recovery"]["actual_resolution"], "1344x768")
            self.assertEqual(meta["params"]["requested_delivery_resolution"], "3840x2160")
            self.assertEqual(meta["params"]["delivery_resolution"], "")
            self.assertEqual(meta["params"]["spatial_upsampling"], "")
            self.assertNotIn("postprocessing", meta)
            self.assertEqual(meta["delivery_recovery"]["producer_job_id"], "job-1")
            self.assertEqual(
                meta["delivery_recovery"]["recovery_job_id"], "recovery-child",
            )

    def test_postpublish_cancel_restores_all_private_sidecars_byte_exact(self):
        symbols, _ = self._symbols(Mock(), Mock())
        staged = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, self.files,
        )
        symbols["_reset_h3_delivery_work"](staged)
        symbols["_publish_h3_delivery_outputs"](self.job, staged)
        for filename in self.files:
            meta = json.loads(Path(
                self.out_dir, Path(filename).stem + ".meta.json",
            ).read_text(encoding="utf-8"))
            self.assertNotIn("postprocessing", meta)
        expected = [Path(item["rollback_meta"]).read_bytes() for item in staged]
        self.job["cancel_requested"] = True
        symbols["_rollback_h3_delivery_publication"](self.job)
        for item, original in zip(staged, expected):
            self.assertEqual(Path(item["source_meta"]).read_bytes(), original)
            self.assertFalse(Path(item["source_path"]).exists())
        self.assertEqual(self.job["output_files"], [])

    def test_native_cleanup_fault_keeps_sidecar_and_tracking(self):
        symbols, _ = self._symbols(Mock(), Mock())
        staged = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, self.files,
        )
        symbols["_reset_h3_delivery_work"](staged)
        symbols["_publish_h3_delivery_outputs"](self.job, staged)
        real_remove = os.remove

        def fail_native_only(path):
            if path == staged[0]["native_path"]:
                raise PermissionError("locked native")
            return real_remove(path)

        with patch("os.remove", side_effect=fail_native_only):
            complete = symbols["_finalize_h3_delivery_publication"](self.job)
        self.assertFalse(complete)
        self.assertTrue(Path(staged[0]["native_path"]).is_file())
        self.assertTrue(Path(staged[0]["native_meta"]).is_file())
        self.assertTrue(self.job["_h3_delivery_cleanup_pending"])
        self.assertEqual(self.job["_h3_delivery_native"], [staged[0]])

    def test_manual_retry_runs_postprocess_only_once_without_denoise(self):
        upscale = Mock()
        fit = Mock()
        symbols, release = self._symbols(upscale, fit)
        staged = symbols["_stage_h3_delivery_native_outputs"](
            self.job, self.out_dir, self.files,
        )
        retry_symbols = _load_launch_symbols(
            "_H3DeliveryFailure",
            "_retry_h3_delivery_postprocess_only",
            namespace=symbols,
        )
        recovery = {
            "spatial_upsampling": "flashvsr3",
            "delivery_resolution": "3840x2160",
            "delivery_fit": "center_crop",
            "manual_retry_count": 1,
        }
        outputs = retry_symbols["_retry_h3_delivery_postprocess_only"](
            self.job, staged, recovery,
        )
        self.assertEqual(outputs, self.files)
        self.assertEqual(release.call_count, 1)
        self.assertEqual(upscale.call_count, 2)
        self.assertEqual(fit.call_count, 2)
        for filename in self.files:
            meta = json.loads(Path(
                self.out_dir, Path(filename).stem + ".meta.json",
            ).read_text(encoding="utf-8"))
            self.assertEqual(meta["delivery_recovery"]["action"], "retry_delivery")
            self.assertEqual(meta["postprocessing"]["steps"], [
                {"step": "upscale", "outcome": "applied", "method": "flashvsr3"},
                {"step": "delivery_fit", "outcome": "applied"},
            ])
        self.assertFalse(hasattr(symbols["wgp"], "generate_video"))

class H3ManualCOWChildTests(unittest.TestCase):
    def setUp(self):
        from services.job_lifecycle import _reset_queue_state_for_tests
        _reset_queue_state_for_tests()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        from services.job_lifecycle import configure_durability_hook
        self.addCleanup(lambda: configure_durability_hook(None))

    def _prepare(self, action="retry_delivery", *, register_child=True, source_updates=None):
        import copy
        from contextlib import nullcontext
        from services import job_lifecycle as lifecycle
        from services.queue_recovery import QueueRecoveryJournal
        from services.queue_recovery_adapter import QueueRecoveryCoordinator, owner_principal_digest, project_instance_digest
        from services.queue_recovery_runtime import load_request_manifest, write_sealed_request_manifest
        owner = owner_principal_digest(b"h3-cow-child-test-secret", "owner-session")
        project = project_instance_digest(b"h3-cow-child-test-secret", "a" * 32)
        source = {"id": "aaaaaaaa", "status": "failed", "execution_attempt": 1, "workspace": "project-a", "out_dir": str(self.root),
                  "session_id": "owner-session", "access_policy": {"private": True, "explicit": False}, "private": True,
                  "params": {"model_type": "minimax_h3_video"}, "output_files": [],
                  "_recovery_owner_digest": owner, "_recovery_project_digest": project}
        source.update(source_updates or {})
        self.native = self.root / "native.mp4"
        self.native.write_bytes(b"native-bytes")
        parent_unit = recovery_unit_id(source["id"], "ordinary_repeat")
        meta = dict(_sidecar(self.native.name), job_id=source["id"], producer_unit_id=parent_unit,
                    producer_unit_kind="ordinary_repeat", producer_unit_variant=0, producer_unit_index=0,
                    producer_unit_dependencies=[], producer_media_size=self.native.stat().st_size,
                    producer_media_sha256=sha256_file(self.native)[1])
        self.meta = self.root / "native.meta.json"
        self.meta.write_text(json.dumps(meta))
        self.original = self.native.read_bytes(), self.meta.read_bytes()
        descriptor = artifact_descriptor(self.root, basename=self.native.name, sidecar_basename=self.meta.name, producer_unit_id=parent_unit)
        settings = {"publication_schema": 2, "native_hashes": [descriptor["sha256"]], "delivery_resolution": "1920x1080",
                    "delivery_fit": "upscale_exact", "spatial_upsampling": "flashvsr3"}
        source_unit = recovery_unit_id(source["id"], "h3_delivery", dependencies=[parent_unit], settings=settings)
        source["recovery_cursor"] = {"delivery_pending": {"publication_schema": 2, "state": "staging_native", "unit_id": source_unit,
            "settings": settings, "dependencies": [parent_unit], "staging": [{"source": descriptor}]}}
        coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(self.root / "queue.json"))
        source["_recovery_manifest_pointer"] = write_sealed_request_manifest(self.root, job_id=source["id"], params=source["params"], inputs=[])
        coordinator.register_job(source, owner_digest=owner, project_digest=project, request_manifest=source["_recovery_manifest_pointer"])
        class Registry(dict):
            def prepare(self, job):
                job.setdefault("queue_held", False)
                return job
            def publish_prepared(self, job_id, job):
                self[job_id] = job
        jobs = Registry({source["id"]: source})
        passes = []
        def transform(path, method, **kwargs):
            passes.append((method, Path(path).read_bytes()))
            Path(path).write_bytes(Path(path).read_bytes() + b"-" + method.encode())
        symbols = _load_launch_symbols(
            "_h3_cow_source_control", "_h3_cow_source_evidence", "_h3_cow_child_plan", "_verify_h3_cow_child", "_h3_cow_completed_unit",
            "_new_h3_delivery_recovery_job", "_run_h3_cow_delivery_child", "_finish_h3_cow_child", "_cleanup_h3_cow_child",
            "_schedule_h3_delivery_recovery", "_h3_delivery_recovery_state", "_h3_delivery_native_available",
            "_queue_recovery_materialize_job",
            "_queue_recovery_delivery_pending", "_queue_recovery_checkpoint_delivery_pending", "_queue_recovery_validate_delivery_parents",
            "_queue_recovery_checkpoint_delivery_publication", "_queue_recovery_reconcile_delivery_publication",
            "_queue_recovery_checkpoint_delivery_completed", "_queue_recovery_completed_delivery_sidecar", "_queue_recovery_checkpoint_unit",
            "_stage_h3_delivery_native_outputs_v2", "_queue_recovery_restore_delivery_staged", "_reset_h3_delivery_work",
            "_atomic_create_json", "_atomic_write_json", "_atomic_write_bytes", "_publish_h3_delivery_outputs",
            "_process_h3_delivery_from_protected_native", "_rollback_h3_delivery_publication", "_finalize_h3_delivery_publication", "_H3DeliveryFailure",
            namespace={"copy": copy, "hmac": hmac, "re": re, "logging": logging, "Request": object,
                "_jobs": jobs, "_gen_lock": threading.Lock(), "generation_slot": lifecycle.generation_slot,
                "_WgpNativeGpuExecutionSlot": lambda acquired: nullcontext(), "register_abort_state": lifecycle.register_abort_state,
                "unregister_abort_state": lifecycle.unregister_abort_state, "_active_gen_states": {},
                "try_start": lifecycle.try_start, "request_cancel": lifecycle.request_cancel, "is_cancel_requested": lifecycle.is_cancel_requested,
                "finish_job": lifecycle.finish_job, "update_job": lifecycle.update_job,
                "adopt_completed_h3_delivery_job": lifecycle.adopt_completed_h3_delivery_job,
                "validated_h3_delivery_recovery_control": lifecycle.validated_h3_delivery_recovery_control,
                "_queue_recovery_checkpoint": lifecycle.checkpoint_recovery_job,
                "_queue_recovery_units": lambda job: list((job.get("recovery_cursor") or {}).get("completed_units") or []),
                "_queue_recovery_project_identity": lambda workspace, path: project,
                "load_request_manifest": load_request_manifest, "recovery_unit_id": recovery_unit_id,
                "validate_artifact_descriptor": validate_artifact_descriptor, "_recovery_artifact_descriptor": artifact_descriptor,
                "_protected_recovery_artifact_descriptor": protected_artifact_descriptor,
                "validate_protected_artifact_descriptor": validate_protected_artifact_descriptor,
                "replay_delivery_from_protected_native": replay_delivery_from_protected_native,
                "_release_h3_delivery_vram": lambda: [], "_apply_spatial_upsampling_to_file": transform,
                "_apply_delivery_fit_to_file": lambda path, resolution, fit, **kw: transform(path, fit, **kw),
                "_h3_final_output_integrity": lambda *args, **kw: {"validation": "valid"},
                "_safe_failure_updates": lambda error, job, **kw: {"error": str(error)},
                "_sample_campaign_transition_lock": threading.RLock(), "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_generation",
                "_begin_workspace_operation": Mock(), "_end_workspace_operation": Mock(), "wgp": SimpleNamespace(get_video_info=lambda path: (1, 640, 360, 24)),})
        child = symbols["_new_h3_delivery_recovery_job"](source, action)
        child["_recovery_manifest_pointer"] = write_sealed_request_manifest(self.root, job_id=child["id"], params=child["params"], inputs=[])
        if register_child:
            lifecycle.register_h3_delivery_child(source, child, source_control=symbols["_h3_cow_source_control"](source),
                persist=lambda parent, prepared, old: coordinator.register_h3_delivery_child_atomic(parent, prepared,
                    owner_digest=owner, project_digest=project, request_manifest=child["_recovery_manifest_pointer"], expected_control=old))
            jobs[child["id"]] = child
        lifecycle.configure_durability_hook(coordinator.prospective_transition)
        return symbols, source, child, coordinator, passes

    def test_manual_cow_actions_publish_sealed_child_and_preserve_source(self):
        from services.job_lifecycle import configure_durability_hook
        for action in ("retry_delivery", "accept_native"):
            with self.subTest(action=action):
                # Separate publication names and durable journals for each action.
                directory = self.root / action
                directory.mkdir()
                original_root, self.root = self.root, directory
                symbols, source, child, coordinator, passes = self._prepare(action)
                symbols["_run_h3_cow_delivery_child"](child["id"])
                self.assertEqual(child["status"], "completed", child.get("error"))
                self.assertEqual(source["status"], "failed")
                self.assertEqual(source["h3_delivery_recovery_control"]["manual_retry_count"], int(action == "retry_delivery"))
                self.assertTrue(source["h3_delivery_recovery_control"]["consumed"])
                self.assertEqual((self.native.read_bytes(), self.meta.read_bytes()), self.original)
                expected = b"native-bytes-flashvsr3-upscale_exact" if action == "retry_delivery" else b"native-bytes"
                final = self.root / child["output_files"][0]
                self.assertEqual(final.read_bytes(), expected)
                descriptor = child["recovery_unit"]["artifacts"][0]
                self.assertTrue(validate_artifact_descriptor(self.root, descriptor, producer_unit_id=child["recovery_unit"]["unit_id"]))
                receipt = json.loads((self.root / descriptor["sidecar_basename"]).read_text())
                self.assertEqual(receipt["job_id"], source["id"])
                self.assertEqual(receipt["recovery_job_id"], child["id"])
                self.assertEqual(receipt["delivery_recovery"]["action"], action)
                self.assertEqual(len(passes), 2 if action == "retry_delivery" else 0)
                self.assertNotIn("delivery_pending", child["recovery_cursor"])
                symbols["_begin_workspace_operation"].assert_called_once_with(source["workspace"])
                symbols["_end_workspace_operation"].assert_called_once_with(source["workspace"])
                configure_durability_hook(None)
                self.root = original_root

    def test_failed_worker_attachment_retains_one_durable_held_child_and_source_link(self):
        import asyncio
        from services import job_lifecycle as lifecycle
        from services.queue_recovery_adapter import QueueRecoveryCoordinator
        from services.queue_recovery_runtime import write_sealed_request_manifest
        symbols, source, unused, coordinator, passes = self._prepare(register_child=False)
        class Denied(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                super().__init__(detail)
        class Thread:
            def __init__(self, **kwargs):
                pass
            def start(self):
                raise RuntimeError("worker attachment failed")
        request = SimpleNamespace(json=AsyncMock(return_value={"workspace": source["workspace"], "capability": "accepted"}))
        symbols.update(HTTPException=Denied, _h3_delivery_recovery_lock=threading.RLock(),
            _require_h3_delivery_recovery_job=lambda *args: source,
            _h3_delivery_recovery_token=lambda *args: "accepted", _begin_workspace_operation=Mock(),
            _queue_recovery_coordinator=coordinator, write_sealed_request_manifest=write_sealed_request_manifest,
            register_h3_delivery_child=lifecycle.register_h3_delivery_child, threading=SimpleNamespace(Thread=Thread))
        self.assertIsNotNone(symbols["_h3_delivery_recovery_state"](source))
        with self.assertRaises(Denied) as failure:
            asyncio.run(symbols["_schedule_h3_delivery_recovery"](source["id"], "retry_delivery", request))
        self.assertEqual(failure.exception.status_code, 503)
        restored = QueueRecoveryCoordinator(coordinator.journal).restore().jobs
        self.assertEqual(len(restored), 2)
        control = restored[source["id"]]["h3_delivery_recovery_control"]
        child = restored[control["active_child_id"]]
        self.assertEqual(child["status"], "queued")
        self.assertTrue(child["queue_held"])
        self.assertEqual(child["recovery_state"], "blocked")
        self.assertEqual(child["execution_attempt"], 1)
        self.assertEqual(child["resource_intent"], "generation")
        self.assertEqual(control["manual_retry_count"], 0)
        self.assertFalse(control["consumed"])
        self.assertEqual(source["h3_delivery_recovery_control"], control)
        self.assertEqual((self.native.read_bytes(), self.meta.read_bytes()), self.original)
        self.assertEqual(passes, [])
        with self.assertRaises(Denied) as duplicate:
            asyncio.run(symbols["_schedule_h3_delivery_recovery"](source["id"], "retry_delivery", request))
        self.assertEqual(duplicate.exception.status_code, 404)
        self.assertEqual(QueueRecoveryCoordinator(coordinator.journal).restore().jobs, restored)

    def _restore_held_child(self):
        from services import job_lifecycle as lifecycle
        from services.queue_recovery_adapter import QueueRecoveryCoordinator, QueueRecoveryAdapterError
        symbols, source, child, coordinator, passes = self._prepare()
        class Denied(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                super().__init__(detail)
        self.assertEqual(lifecycle.set_job_hold(child, True), "held")
        lifecycle.configure_durability_hook(None)
        restarted = QueueRecoveryCoordinator(coordinator.journal)
        snapshots = restarted.restore().jobs
        projects = {source["workspace"]: (str(self.root), source["_recovery_project_digest"])}
        source, _ = symbols["_queue_recovery_materialize_job"](snapshots[source["id"]], projects)
        child, may_start = symbols["_queue_recovery_materialize_job"](snapshots[child["id"]], projects)
        self.assertFalse(may_start)
        self.assertTrue(child["queue_held"])
        self.assertEqual(passes, [])
        symbols["_jobs"].update({source["id"]: source, child["id"]: child})
        lifecycle.configure_durability_hook(restarted.prospective_transition)
        symbols.update(_queue_recovery_checkpoint_lock=threading.RLock(),
            _existing_workspace_dir=lambda _: str(self.root),
            _queue_recovery_existing_project_identity=lambda _: source["_recovery_project_digest"],
            _queue_recovery_is_blocked=lambda job: job.get("recovery_state") == "blocked",
            QueueRecoveryAdapterError=QueueRecoveryAdapterError, HTTPException=Denied)
        symbols = _load_launch_symbols("_queue_recovery_revalidate_job",
            "_start_restored_held_generation_worker", namespace=symbols)
        return symbols, source, child, restarted, passes

    def test_held_delivery_child_restarts_then_resume_completes_once(self):
        from services import job_lifecycle as lifecycle
        symbols, source, child, restarted, passes = self._restore_held_child()
        attached = []
        def record_thread(**kwargs):
            thread = threading.Thread(**kwargs)
            attached.append(thread)
            return thread
        symbols["threading"] = SimpleNamespace(Thread=record_thread)
        self.assertEqual(lifecycle.set_job_hold(child, False), "resumed")
        symbols["_start_restored_held_generation_worker"](child)
        symbols["_start_restored_held_generation_worker"](child)
        for thread in attached:
            thread.join(timeout=5)
        self.assertEqual(len(attached), 1)
        self.assertFalse(attached[0].is_alive())
        self.assertEqual(child["status"], "completed", child.get("error"))
        self.assertEqual(len(passes), 2)
        self.assertEqual(source["h3_delivery_recovery_control"]["manual_retry_count"], 1)
        self.assertTrue(source["h3_delivery_recovery_control"]["consumed"])
        self.assertEqual((self.native.read_bytes(), self.meta.read_bytes()), self.original)

    def test_delivery_child_release_start_crash_consumes_pending_on_automatic_restore(self):
        from services import job_lifecycle as lifecycle
        from services.queue_recovery_adapter import QueueRecoveryCoordinator
        symbols, source, child, restarted, passes = self._restore_held_child()
        self.assertTrue(child.get("_recovery_worker_pending"))
        lifecycle.set_job_hold(child, False)
        snapshot = QueueRecoveryCoordinator(restarted.journal).restore().jobs[child["id"]]
        projects = {source["workspace"]: (str(self.root), source["_recovery_project_digest"])}
        child, may_start = symbols["_queue_recovery_materialize_job"](snapshot, projects)
        self.assertTrue(may_start)
        self.assertNotIn("_recovery_worker_pending", child)
        self.assertEqual(passes, [])

    def test_held_delivery_resume_rechecks_native_before_worker_or_charge(self):
        from services import job_lifecycle as lifecycle
        symbols, source, child, restarted, passes = self._restore_held_child()
        self.native.write_bytes(b"changed-native")
        attached = Mock()
        symbols["threading"] = SimpleNamespace(Thread=attached)
        lifecycle.set_job_hold(child, False)
        with self.assertRaises(symbols["HTTPException"]) as failure:
            symbols["_start_restored_held_generation_worker"](child)
        self.assertEqual(failure.exception.status_code, 409)
        attached.assert_not_called()
        self.assertTrue(child["queue_held"])
        self.assertEqual(child["recovery_state"], "blocked")
        self.assertEqual(source["h3_delivery_recovery_control"]["manual_retry_count"], 0)
        self.assertFalse(source["h3_delivery_recovery_control"]["consumed"])
        self.assertEqual(passes, [])

    def test_retained_cow_director_and_cumulative_sources_offer_no_manual_child(self):
        from services.job_lifecycle import configure_durability_hook
        for marker in ("_director_final_video_postprocess", "_h3_cumulative_append"):
            with self.subTest(marker=marker):
                directory = self.root / marker
                directory.mkdir()
                original_root, self.root = self.root, directory
                symbols, source, child, coordinator, passes = self._prepare()
                source["params"][marker] = True
                self.assertIsNone(symbols["_h3_delivery_recovery_state"](source))
                with self.assertRaises(QueueRecoveryRuntimeError):
                    symbols["_new_h3_delivery_recovery_job"](source, "retry_delivery")
                symbols["_run_h3_cow_delivery_child"](child["id"])
                self.assertTrue(child["queue_held"])
                self.assertEqual(source["h3_delivery_recovery_control"]["manual_retry_count"], 0)
                self.assertFalse(source["h3_delivery_recovery_control"]["consumed"])
                self.assertEqual(passes, [])
                self.assertEqual((self.native.read_bytes(), self.meta.read_bytes()), self.original)
                configure_durability_hook(None)
                self.root = original_root

    def test_changed_source_blocks_child_before_charge_or_any_pass(self):
        symbols, source, child, coordinator, passes = self._prepare()
        self.native.write_bytes(b"changed-native")
        symbols["_run_h3_cow_delivery_child"](child["id"])
        self.assertEqual(child["status"], "queued")
        self.assertTrue(child["queue_held"])
        self.assertFalse(child["h3_delivery_recovery_control"]["charged"])
        self.assertEqual(source["h3_delivery_recovery_control"]["manual_retry_count"], 0)
        self.assertFalse(source["h3_delivery_recovery_control"]["consumed"])
        self.assertEqual(passes, [])
        self.assertEqual(list(self.root.glob("h3-delivery-*.mp4")), [])

    def _materialize_pair(self, symbols, source, child, coordinator):
        from services import job_lifecycle as lifecycle
        from services.queue_recovery_adapter import QueueRecoveryCoordinator, serialize_job
        lifecycle.configure_durability_hook(None)
        restarted = QueueRecoveryCoordinator(coordinator.journal)
        snapshots = restarted.restore().jobs
        projects = {source["workspace"]: (str(self.root), source["_recovery_project_digest"])}
        restored_source, source_resume = symbols["_queue_recovery_materialize_job"](snapshots[source["id"]], projects)
        restored_child, child_resume = symbols["_queue_recovery_materialize_job"](snapshots[child["id"]], projects)
        self.assertFalse(source_resume)
        self.assertTrue(child_resume)
        self.assertNotIn("_recovery_worker_pending", restored_child)
        self.assertEqual(serialize_job(restored_source, owner_digest=source["_recovery_owner_digest"],
            project_digest=source["_recovery_project_digest"], request_manifest=source["_recovery_manifest_pointer"]), snapshots[source["id"]])
        symbols["_jobs"].update({restored_source["id"]: restored_source, restored_child["id"]: restored_child})
        lifecycle.configure_durability_hook(restarted.prospective_transition)
        # Startup checkpoints interrupted children, while already-failed parents
        # retain their original durable receipt.
        self.assertTrue(lifecycle.checkpoint_recovery_job(restored_child))
        restored_child["_h3_delivery_control_source"] = restored_source
        return restored_source, restored_child, restarted

    def test_restored_failed_parent_and_queued_child_charge_without_metadata_loss(self):
        from services.queue_recovery_adapter import QueueRecoveryCoordinator
        metadata = {"error": "delivery failed", "message": "retained producer failure", "queue_held": False,
                    "reruns_denoise": True, "recovery_state": "terminal"}
        symbols, source, child, coordinator, passes = self._prepare(source_updates=metadata)
        source, child, restarted = self._materialize_pair(symbols, source, child, coordinator)
        symbols["_run_h3_cow_delivery_child"](child["id"])
        self.assertEqual(child["status"], "completed", child.get("error"))
        durable = QueueRecoveryCoordinator(restarted.journal).restore().jobs
        self.assertEqual(durable[child["id"]]["status"], "completed")
        self.assertEqual(durable[source["id"]]["status"], "failed")
        for key, value in metadata.items():
            if key != "error":
                self.assertEqual(durable[source["id"]][key], value)
        self.assertNotIn("error", source)
        self.assertEqual(durable[source["id"]]["h3_delivery_recovery_control"]["manual_retry_count"], 1)
        self.assertTrue(durable[source["id"]]["h3_delivery_recovery_control"]["consumed"])
        self.assertEqual(len(passes), 2)
        self.assertEqual((self.native.read_bytes(), self.meta.read_bytes()), self.original)

    def test_crash_after_seal_adopts_held_child_without_native_or_second_charge(self):
        from services import job_lifecycle as lifecycle
        from services.queue_recovery_adapter import QueueRecoveryCoordinator
        metadata = {"error": "delivery failed", "message": "retained producer failure", "queue_held": False,
                    "reruns_denoise": True, "recovery_state": "terminal"}
        symbols, source, child, coordinator, passes = self._prepare(source_updates=metadata)
        def persist(proposal):
            coordinator.prospective_transition(proposal)
            if proposal.name == "recovery_checkpoint" and any((job.get("recovery_unit") or {}).get("kind") == "h3_delivery" for job in proposal.jobs):
                raise SystemExit("simulated process crash after seal acknowledgement")
        lifecycle.configure_durability_hook(None)
        lifecycle.configure_durability_hook(persist)
        with self.assertRaises(SystemExit):
            symbols["_run_h3_cow_delivery_child"](child["id"])
        source, child, restarted = self._materialize_pair(symbols, source, child, coordinator)
        self.assertEqual(child["status"], "queued")
        self.assertEqual(source["h3_delivery_recovery_control"]["manual_retry_count"], 1)
        unit = child["recovery_unit"]
        sidecar_path = self.root / unit["artifacts"][0]["sidecar_basename"]
        sealed_bytes = sidecar_path.read_bytes()
        lifecycle.configure_durability_hook(restarted.prospective_transition)
        self.assertTrue(lifecycle.checkpoint_recovery_job(child, status="queued", queue_held=True, resource_state="queued"))
        for hidden in self.root.glob(".maestro-delivery-*"):
            hidden.unlink()
        self.native.unlink()
        passes.clear()
        symbols["_run_h3_cow_delivery_child"](child["id"])
        self.assertEqual(child["status"], "completed", child.get("error"))
        self.assertTrue(source["h3_delivery_recovery_control"]["consumed"])
        self.assertEqual(source["h3_delivery_recovery_control"]["manual_retry_count"], 1)
        self.assertEqual(passes, [])
        self.assertEqual(sidecar_path.read_bytes(), sealed_bytes)
        self.assertTrue(validate_artifact_descriptor(self.root, unit["artifacts"][0], producer_unit_id=unit["unit_id"]))
        durable = QueueRecoveryCoordinator(restarted.journal).restore().jobs
        self.assertEqual(durable[child["id"]]["status"], "completed")
        self.assertEqual(durable[source["id"]]["status"], "failed")
        for key, value in metadata.items():
            if key != "error":
                self.assertEqual(durable[source["id"]][key], value)
        self.assertNotIn("error", source)
        self.assertEqual(durable[source["id"]]["h3_delivery_recovery_control"]["manual_retry_count"], 1)

    def test_cancel_after_publication_seal_retracts_child_and_preserves_source(self):
        from services import job_lifecycle as lifecycle
        from services.queue_recovery_adapter import QueueRecoveryCoordinator
        symbols, source, child, coordinator, passes = self._prepare()
        won = []
        def persist(proposal):
            coordinator.prospective_transition(proposal)
            if not won and any((job.get("recovery_unit") or {}).get("state") == "completed" for job in proposal.jobs):
                won.append(lifecycle.request_cancel(child, job_id=child["id"], active_states=symbols["_active_gen_states"]))
        lifecycle.configure_durability_hook(None)
        lifecycle.configure_durability_hook(persist)
        symbols["_run_h3_cow_delivery_child"](child["id"])
        self.assertTrue(won[0].changed)
        self.assertEqual(child["status"], "cancelled")
        self.assertEqual(child["output_files"], [])
        self.assertEqual((self.native.read_bytes(), self.meta.read_bytes()), self.original)
        self.assertEqual(list(self.root.glob("h3-delivery-*.mp4")), [])
        self.assertFalse(source["h3_delivery_recovery_control"]["consumed"])
        self.assertIsNone(source["h3_delivery_recovery_control"]["active_child_id"])
        self.assertEqual(source["h3_delivery_recovery_control"]["manual_retry_count"], 1)
        restored = QueueRecoveryCoordinator(coordinator.journal).restore().jobs
        self.assertEqual(restored[child["id"]]["status"], "cancelled")
        self.assertEqual(restored[source["id"]]["h3_delivery_recovery_control"], source["h3_delivery_recovery_control"])
        self.assertFalse(lifecycle.finish_job(child, "completed", expected_execution_attempt=1))
        self.assertEqual(QueueRecoveryCoordinator(coordinator.journal).restore().jobs, restored)

    def test_resealed_completed_bytes_cannot_replace_frozen_child_delivery_settings(self):
        import copy
        symbols, source, child, coordinator, passes = self._prepare("accept_native")
        symbols["_run_h3_cow_delivery_child"](child["id"])
        unit = copy.deepcopy(child["recovery_unit"])
        unit["settings"]["delivery_resolution"] = "3840x2160"
        unit["unit_id"] = recovery_unit_id(child["id"], "h3_delivery", dependencies=unit["dependencies"], settings=unit["settings"])
        descriptor = unit["artifacts"][0]
        meta_path = self.root / descriptor["sidecar_basename"]
        sidecar = json.loads(meta_path.read_text())
        sidecar.update(producer_unit_id=unit["unit_id"], producer_unit_settings=unit["settings"])
        meta_path.write_text(json.dumps(sidecar))
        unit["artifacts"] = [artifact_descriptor(self.root, basename=descriptor["basename"],
            sidecar_basename=descriptor["sidecar_basename"], producer_unit_id=unit["unit_id"])]
        self.assertTrue(validate_artifact_descriptor(self.root, unit["artifacts"][0], producer_unit_id=unit["unit_id"]))
        child["recovery_unit"] = unit
        child["recovery_cursor"]["completed_units"] = [unit]
        with self.assertRaises(QueueRecoveryRuntimeError):
            symbols["_h3_cow_completed_unit"](child)
        self.assertEqual(passes, [])

    def test_completed_child_adoption_preserves_final_sidecar_after_cleanup(self):
        from services.job_lifecycle import configure_durability_hook
        from services.queue_recovery_adapter import QueueRecoveryCoordinator
        symbols, source, child, coordinator, passes = self._prepare("accept_native")
        symbols["_run_h3_cow_delivery_child"](child["id"])
        descriptor = child["recovery_unit"]["artifacts"][0]
        before = (self.root / descriptor["sidecar_basename"]).read_bytes()
        restored = QueueRecoveryCoordinator(coordinator.journal).restore().jobs
        # Materialize from durable records while preserving original producer ownership.
        for job in (source, child):
            private = {key: value for key, value in job.items() if key.startswith("_")}
            job.update(restored[job["id"]])
            job.update(private)
        self.native.unlink()
        configure_durability_hook(None)
        configure_durability_hook(lambda proposal: self.fail("Settled adoption must not persist"))
        # Worker cleanup remains a separate idempotent checkpoint; inspect adoption itself here.
        final = symbols["_h3_cow_completed_unit"](child)
        self.assertTrue(symbols["adopt_completed_h3_delivery_job"](child, expected_execution_attempt=1, completed_unit=final[0], output_files=final[1]))
        self.assertEqual((self.root / descriptor["sidecar_basename"]).read_bytes(), before)
        self.assertEqual(passes, [])


class H3DeliverySelectionAndPrivacyTests(unittest.TestCase):
    def test_wgp_release_detaches_before_fault_and_always_cleans_cache(self):
        source = (APP / "wgp.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(APP / "wgp.py"))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"clear_gen_cache", "release_model"}]
        owner = SimpleNamespace(release=Mock(side_effect=RuntimeError("release fault")))
        flush = Mock()
        collect = Mock()
        namespace = {
            "wan_model": None, "offloadobj": owner,
            "offload": SimpleNamespace(shared_state={"_cache": object()},
                                       flush_torch_caches=flush),
            "gc": SimpleNamespace(collect=collect),
            "_invalidate_loaded_model_state": Mock(),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP / "wgp.py"), "exec"), namespace)
        with self.assertRaisesRegex(RuntimeError, "release fault"):
            namespace["release_model"]()
        self.assertIsNone(namespace["wan_model"])
        self.assertIsNone(namespace["offloadobj"])
        self.assertNotIn("_cache", namespace["offload"].shared_state)
        flush.assert_called_once()
        collect.assert_called_once()

    def test_startup_reindexes_durable_owner_private_native(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root, "project-a")
            project.mkdir()
            native = project / ".maestro-delivery-source-t-variant.native.mp4"
            native.write_bytes(b"native")
            meta = Path(os.path.splitext(str(native))[0] + ".meta.json")
            meta.write_text(json.dumps({
                "private": True, "owner_session_id": "owner",
                "workspace": "project-a", "delivery_native_source": True,
                "params": {"model_type": "minimax_h3_video"},
                "delivery_recovery": {
                    "schema_version": 1,
                    "optional_finishing": {"schema_version": 1, "film_grain": {"intensity": 0.25, "saturation": 0.5}}, "source_job_id": "source",
                    "original_filename": "variant.mp4",
                    "requested_target": "3840x2160",
                    "delivery_fit": "center_crop",
                    "spatial_upsampling": "flashvsr3",
                    "producer_job_id": "source",
                    "producer_artifact_class": "final",
                    "final_private": True, "final_explicit": False,
                    "source_remote": True,
                    "owner_session_id": "owner",
                    "manual_retry_count": 1, "manual_retry_limit": 2,
                    "oom_info": {
                        "is_oom": True, "stage": "h3_delivery",
                        "requested_target": "3840x2160",
                        "native_available": True, "retry_count": 1,
                        "recoverable": True,
                        "actions": ["released_h3", "retried_identical_delivery"],
                        "current_coefficient": 0.77,
                        "suggested_coefficient": 0.67,
                        "message": "safe",
                    },
                },
            }), encoding="utf-8")
            jobs = {}
            symbols = _load_launch_symbols(
                "_reindex_h3_delivery_recoveries",
                namespace={
                    "wgp": SimpleNamespace(server_config={"save_path": root}),
                    "_jobs": jobs,
                },
            )
            self.assertEqual(symbols["_reindex_h3_delivery_recoveries"](), 1)
            restored = jobs["source"]
            self.assertEqual(restored["status"], "failed")
            self.assertEqual(restored["session_id"], "owner")
            self.assertTrue(restored["_h3_delivery_recovery"]["restart_supported"])
            self.assertEqual(restored["_h3_delivery_recovery"]["optional_finishing"], {
                "schema_version": 1, "film_grain": {"intensity": 0.25, "saturation": 0.5},
            })
            self.assertTrue(restored["source_remote"])
            self.assertTrue(restored["failure_details"]["is_oom"])
            self.assertEqual(set(restored["oom_info"]), {
                "is_oom", "stage", "requested_target", "native_available",
                "retry_count", "recoverable", "actions",
                "current_coefficient", "suggested_coefficient", "message",
            })
            self.assertEqual(restored["oom_info"]["current_coefficient"], 0.77)
            self.assertEqual(
                restored["oom_info"]["actions"],
                ["released_h3", "retried_identical_delivery"],
            )
            self.assertEqual(
                restored["_h3_delivery_recovery"]["manual_retry_count"], 1,
            )

    def test_startup_reindex_preserves_non_oom_failure_without_vram_claim(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root, "project-a")
            project.mkdir()
            native = project / ".maestro-delivery-source-t-variant.native.mp4"
            native.write_bytes(b"native")
            meta = Path(os.path.splitext(str(native))[0] + ".meta.json")
            meta.write_text(json.dumps({
                "private": True,
                "owner_session_id": "owner",
                "workspace": "project-a",
                "delivery_native_source": True,
                "params": {"model_type": "minimax_h3_video"},
                "delivery_recovery": {
                    "schema_version": 1,
                    "source_job_id": "source",
                    "original_filename": "variant.mp4",
                    "requested_target": "3840x2160",
                    "delivery_fit": "center_crop",
                    "spatial_upsampling": "flashvsr3",
                    "producer_job_id": "source",
                    "producer_artifact_class": "final",
                    "final_private": True,
                    "final_explicit": False,
                    "source_remote": True,
                    "owner_session_id": "owner",
                    "manual_retry_count": 0,
                    "manual_retry_limit": 2,
                    "failure_details": {
                        "stage": "publication",
                        "code": "cuda_oom",
                        "exception_type": "RuntimeError",
                        "detail": "unsafe private content",
                        "is_oom": True,
                    },
                },
            }), encoding="utf-8")
            jobs = {}
            symbols = _load_launch_symbols(
                "_reindex_h3_delivery_recoveries",
                namespace={
                    "wgp": SimpleNamespace(server_config={"save_path": root}),
                    "_jobs": jobs,
                },
            )
            self.assertEqual(symbols["_reindex_h3_delivery_recoveries"](), 1)
            restored = jobs["source"]
            self.assertNotIn("oom_info", restored)
            self.assertFalse(restored["failure_details"]["is_oom"])
            self.assertEqual(restored["failure_details"]["stage"], "publication")
            self.assertEqual(
                restored["failure_details"]["code"], "publication_failed",
            )
            self.assertNotIn("unsafe private content", json.dumps(restored))

    def test_startup_reindex_mixed_multioutput_evidence_is_conservative(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root, "project-a")
            project.mkdir()
            for index, filename in enumerate(("variant-a.mp4", "variant-b.mp4")):
                native = project / (
                    f".maestro-delivery-source-t-{index}.native.mp4"
                )
                native.write_bytes(f"native-{index}".encode())
                recovery = {
                    "schema_version": 1,
                    "source_job_id": "source",
                    "original_filename": filename,
                    "requested_target": "3840x2160",
                    "delivery_fit": "center_crop",
                    "spatial_upsampling": "flashvsr3",
                    "producer_job_id": "source",
                    "producer_artifact_class": "final",
                    "final_private": True,
                    "final_explicit": False,
                    "source_remote": True,
                    "owner_session_id": "owner",
                    "manual_retry_count": 0,
                    "manual_retry_limit": 2,
                    "failure_details": {
                        "stage": "delivery" if index == 0 else "publication",
                        "code": "cuda_oom" if index == 0 else "publication_failed",
                        "exception_type": "RuntimeError",
                        "is_oom": index == 0,
                    },
                }
                if index == 0:
                    recovery["oom_info"] = {
                        "is_oom": True,
                        "current_coefficient": 0.8,
                        "suggested_coefficient": 0.7,
                        "actions": ["released_h3"],
                    }
                Path(os.path.splitext(str(native))[0] + ".meta.json").write_text(
                    json.dumps({
                        "private": True,
                        "owner_session_id": "owner",
                        "workspace": "project-a",
                        "delivery_native_source": True,
                        "params": {"model_type": "minimax_h3_video"},
                        "delivery_recovery": recovery,
                    }),
                    encoding="utf-8",
                )
            jobs = {}
            symbols = _load_launch_symbols(
                "_reindex_h3_delivery_recoveries",
                namespace={
                    "wgp": SimpleNamespace(server_config={"save_path": root}),
                    "_jobs": jobs,
                },
            )
            self.assertEqual(symbols["_reindex_h3_delivery_recoveries"](), 1)
            restored = jobs["source"]
            self.assertNotIn("oom_info", restored)
            self.assertEqual(restored["failure_details"], {
                "code": "delivery_failed",
                "stage": "delivery",
                "detail": "The requested delivery output could not be produced.",
                "exception_type": "Exception",
                "is_oom": False,
            })
            self.assertCountEqual(
                [item["file_name"] for item in restored["_h3_delivery_native"]],
                ["variant-a.mp4", "variant-b.mp4"],
            )

    def test_multiwindow_selection_returns_all_and_only_producer_finals(self):
        symbols = _load_launch_symbols("_authoritative_h3_postprocess_outputs")
        selected = symbols["_authoritative_h3_postprocess_outputs"](
            [
                "variant-a-window.mp4", "variant-a_multiclip.mp4",
                "variant-b-window.mp4", "variant-b_multiclip.mp4",
                "notes.json",
            ],
            {
                "variant-a-window.mp4": "window",
                "variant-a_multiclip.mp4": "final",
                "variant-b-window.mp4": "component",
                "variant-b_multiclip.mp4": "final",
            },
            is_multiclip=True,
            join_output_file="variant-a_multiclip.mp4",
        )
        self.assertEqual(selected, [
            "variant-a_multiclip.mp4", "variant-b_multiclip.mp4",
        ])

    def test_delivery_oom_shape_never_echoes_exception_paths(self):
        info = delivery_oom_info(
            RuntimeError("CUDA out of memory at /private/user/model.bin"),
            0.8,
            requested_target="3840x2160",
            native_available=True,
            retry_count=1,
            actions=["released_h3", "retried_identical_delivery"],
        )
        encoded = json.dumps(info)
        self.assertNotIn("/private", encoded)
        self.assertEqual(info["stage"], "h3_delivery")
        self.assertEqual(info["retry_count"], 1)
        invalid = delivery_oom_info(
            RuntimeError("CUDA out of memory"),
            0.8,
            requested_target="/private/user/target",
            native_available=True,
            retry_count=1,
        )
        self.assertEqual(invalid["requested_target"], "")

    def test_recovery_capabilities_are_opaque_bounded_and_path_free(self):
        symbols = _load_launch_symbols(
            "_h3_delivery_recovery_token",
            "_h3_delivery_recovery_state",
            "_public_h3_delivery_recovery",
            namespace={
                "hmac": hmac,
                "hashlib": hashlib,
                "_session_secret": lambda: b"s" * 32,
                "_h3_delivery_native_available": lambda staged: bool(staged),
            },
        )
        job = {
            "id": "source-job",
            "status": "failed",
            "_h3_delivery_recovery": {
                "nonce": "opaque-nonce",
                "staged": [{"native_path": "/secret/native.mp4"}],
                "delivery_resolution": "3840x2160",
                "manual_retry_count": 0,
                "manual_retry_limit": 2,
                "active_job_id": "",
                "restart_supported": False,
                "unsupported_after_restart_reason": "not indexed at startup",
            },
        }
        public = symbols["_public_h3_delivery_recovery"](job)
        self.assertEqual(
            [action["action"] for action in public["actions"]],
            ["accept_native", "retry_delivery"],
        )
        encoded = json.dumps(public)
        self.assertNotIn("/secret", encoded)
        self.assertNotIn("opaque-nonce", encoded)
        first_retry = public["actions"][1]["capability"]
        job["_h3_delivery_recovery"]["manual_retry_count"] = 1
        second_retry = symbols["_public_h3_delivery_recovery"](job)["actions"][1]["capability"]
        self.assertNotEqual(first_retry, second_retry)
        job["_h3_delivery_recovery"]["manual_retry_count"] = 2
        limited = symbols["_public_h3_delivery_recovery"](job)
        self.assertEqual(
            [action["action"] for action in limited["actions"]],
            ["accept_native"],
        )

    def test_foreign_and_workspace_mismatch_recovery_are_both_404(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        job = {"id": "source-job", "workspace": "project-a", "out_dir": "/tmp/a"}
        request = SimpleNamespace(state=SimpleNamespace(
            maestro_session_id="foreign", maestro_remote=False,
        ))
        project_access = SimpleNamespace(status=Mock(return_value=SimpleNamespace(
            protected=True, unlocked=True,
        )))
        symbols = _load_launch_symbols(
            "_require_h3_delivery_recovery_job",
            namespace={
                "HTTPException": FakeHTTPException,
                "_jobs": {"source-job": job},
                "_job_owned_by_request": lambda _job, _request: False,
                "_existing_workspace_dir": Mock(return_value="/tmp/a"),
                "_project_access": project_access,
            },
        )
        with self.assertRaises(FakeHTTPException) as foreign:
            symbols["_require_h3_delivery_recovery_job"](
                "source-job", request, "project-a",
            )
        self.assertEqual(foreign.exception.status_code, 404)

        symbols["_job_owned_by_request"] = lambda _job, _request: True
        with self.assertRaises(FakeHTTPException) as mismatch:
            symbols["_require_h3_delivery_recovery_job"](
                "source-job", request, "project-b",
            )
        self.assertEqual(mismatch.exception.status_code, 404)
        self.assertEqual(foreign.exception.detail, mismatch.exception.detail)

        request.state.maestro_remote = True
        project_access.status = Mock(return_value=SimpleNamespace(
            protected=True, unlocked=False,
        ))
        with self.assertRaises(FakeHTTPException) as locked:
            symbols["_require_h3_delivery_recovery_job"](
                "source-job", request, "project-a",
            )
        self.assertEqual(locked.exception.status_code, 404)
        self.assertEqual(locked.exception.detail, foreign.exception.detail)

        request.state.maestro_remote = False
        symbols["_existing_workspace_dir"] = Mock(
            side_effect=FakeHTTPException(status_code=404, detail="missing"),
        )
        with self.assertRaises(FakeHTTPException) as deleted:
            symbols["_require_h3_delivery_recovery_job"](
                "source-job", request, "project-a",
            )
        self.assertEqual(deleted.exception.status_code, 404)

    def test_recovery_endpoints_never_call_denoise_or_settings_mutations(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            "_schedule_h3_delivery_recovery",
            "_run_h3_delivery_recovery_job",
            "_retry_h3_delivery_postprocess_only",
        }
        selected = [
            ast.get_source_segment(source, node) or ""
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ]
        contract = "\n".join(selected)
        self.assertNotIn("generate_video(", contract)
        self.assertNotIn("server_config_filename", contract)
        self.assertNotIn("services-config", contract)
        self.assertRegex(
            contract,
            r"with generation_slot\(\s*_gen_lock,\s*job,\s*\)",
        )
        self.assertIn("_retry_h3_delivery_postprocess_only", contract)

    def test_retry_capability_schedules_one_bounded_postprocess_job(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class Request:
            async def json(self):
                return {
                    "workspace": "project-a",
                    "capability": capability,
                }

        source_job = {
            "id": "source-job",
            "status": "failed",
            "workspace": "project-a",
            "out_dir": "/contained/project-a",
            "session_id": "owner",
            "access_policy": {"private": True, "owner_session_id": "owner"},
            "private": True,
            "explicit": False,
            "source_remote": True,
            "params": {"model_type": "minimax_h3_video"},
            "_h3_delivery_recovery": {
                "nonce": "nonce",
                "staged": [{"native_path": "hidden"}],
                "delivery_resolution": "3840x2160",
                "manual_retry_count": 0,
                "manual_retry_limit": 2,
                "active_job_id": "",
            },
        }
        jobs = {"source-job": source_job}
        started = []

        class FakeThread:
            def __init__(self, *, target, args, **_kwargs):
                self.target = target
                self.args = args

            def start(self):
                started.append((self.target, self.args))

        namespace = {
            "HTTPException": FakeHTTPException,
            "hmac": hmac,
            "hashlib": hashlib,
            "uuid": uuid,
            "time": time,
            "threading": SimpleNamespace(Thread=FakeThread),
            "_session_secret": lambda: b"s" * 32,
            "_jobs": jobs,
            "_h3_delivery_recovery_lock": threading.RLock(),
            "_h3_delivery_native_available": lambda staged: bool(staged),
            "_require_h3_delivery_recovery_job": (
                lambda job_id, _request, _workspace: jobs[job_id]
            ),
            "_run_h3_delivery_recovery_job": Mock(),
            "_begin_workspace_operation": Mock(),
            "_end_workspace_operation": Mock(),
        }
        symbols = _load_launch_symbols(
            "_h3_delivery_recovery_token",
            "_h3_delivery_recovery_state",
            "_new_h3_delivery_recovery_job",
            "_schedule_h3_delivery_recovery",
            namespace=namespace,
        )
        capability = symbols["_h3_delivery_recovery_token"](
            source_job, "retry_delivery",
        )
        result = asyncio.run(symbols["_schedule_h3_delivery_recovery"](
            "source-job", "retry_delivery", Request(),
        ))
        self.assertEqual(result["action"], "retry_delivery")
        self.assertFalse(result["reruns_denoise"])
        self.assertFalse(result["mutates_machine_settings"])
        self.assertEqual(source_job["_h3_delivery_recovery"]["manual_retry_count"], 0)
        self.assertEqual(source_job["_h3_delivery_recovery"]["active_job_id"], result["job_id"])
        self.assertEqual(len(started), 1)
        self.assertIn(result["job_id"], jobs)

    def test_thread_start_failure_unwinds_active_job_budget_and_reservation(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
        class Request:
            state = SimpleNamespace(maestro_remote=True)
            async def json(self):
                return {"workspace": "project-a", "capability": capability}
        class FailingThread:
            def __init__(self, **_kwargs): pass
            def start(self): raise RuntimeError("thread unavailable")
        source_job = {
            "id": "source", "status": "failed", "workspace": "project-a",
            "out_dir": "/contained/project-a", "session_id": "owner",
            "access_policy": {}, "params": {},
            "_h3_delivery_recovery": {
                "nonce": "n", "staged": [{}], "manual_retry_count": 0,
                "manual_retry_limit": 2, "active_job_id": "",
            },
        }
        jobs = {"source": source_job}
        ended = Mock()
        namespace = {
            "HTTPException": FakeHTTPException, "hmac": hmac,
            "hashlib": hashlib, "uuid": uuid, "time": time,
            "threading": SimpleNamespace(Thread=FailingThread),
            "_session_secret": lambda: b"s" * 32, "_jobs": jobs,
            "_h3_delivery_recovery_lock": threading.RLock(),
            "_h3_delivery_native_available": lambda staged: bool(staged),
            "_require_h3_delivery_recovery_job": lambda *_args: source_job,
            "_begin_workspace_operation": Mock(),
            "_end_workspace_operation": ended,
            "_run_h3_delivery_recovery_job": Mock(),
        }
        symbols = _load_launch_symbols(
            "_h3_delivery_recovery_token", "_h3_delivery_recovery_state",
            "_new_h3_delivery_recovery_job", "_schedule_h3_delivery_recovery",
            namespace=namespace,
        )
        capability = symbols["_h3_delivery_recovery_token"](source_job, "retry_delivery")
        old_nonce = source_job["_h3_delivery_recovery"]["nonce"]
        with self.assertRaises(FakeHTTPException) as failed:
            asyncio.run(symbols["_schedule_h3_delivery_recovery"](
                "source", "retry_delivery", Request(),
            ))
        self.assertEqual(failed.exception.status_code, 503)
        recovery = source_job["_h3_delivery_recovery"]
        self.assertEqual(recovery["manual_retry_count"], 0)
        self.assertEqual(recovery["active_job_id"], "")
        self.assertNotEqual(recovery["nonce"], old_nonce)
        self.assertEqual(len(jobs), 1)
        ended.assert_called_once_with("project-a")


if __name__ == "__main__":
    unittest.main()
