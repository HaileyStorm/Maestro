"""Deterministic, model-free H3 delivery transaction regressions."""
from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


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
    recovery_unit_id,
    sha256_file,
    validate_artifact_descriptor,
)


def _load_launch_symbols(*names: str, namespace: dict | None = None) -> dict:
    source = (APP / "launch.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP / "launch.py"))
    wanted = set(names)
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

    def _symbols(self, upscale, fit, *, cancelled=None):
        release = Mock(return_value=["released_h3", "cleared_cuda_cache"])
        cancel = cancelled or (lambda job: bool(job.get("cancel_requested")))

        def update(job, **values):
            if cancel(job):
                return False
            job.update(values)
            return True

        namespace = {
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
        publish = Mock(return_value=self.files)
        retry_symbols = _load_launch_symbols(
            "_H3DeliveryFailure",
            "_retry_h3_delivery_postprocess_only",
            namespace={
                **symbols,
                "_publish_h3_delivery_outputs": publish,
            },
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
        self.assertEqual(publish.call_count, 1)
        self.assertFalse(hasattr(symbols["wgp"], "generate_video"))

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
                    "schema_version": 1, "source_job_id": "source",
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
