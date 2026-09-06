from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_dasiwa  # noqa: E402
from services import h3_checkpoint_receipts  # noqa: E402


def _write_safetensors(path: Path, metadata: dict[str, str], count: int) -> None:
    header: dict[str, object] = {"__metadata__": metadata}
    for index in range(count):
        header[f"tensor.{index}"] = {
            "dtype": "BF16", "shape": [0], "data_offsets": [0, 0],
        }
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded)


class H3DasiwaTests(unittest.TestCase):
    def test_wgp_binds_dasiwa_to_the_loader_resolver_before_load_or_reuse(self):
        source = (APP / "wgp.py").read_text(encoding="utf-8")
        resolver_start = source.index("def get_resolved_transformer_checkpoint_path")
        resolver_end = source.index("\n\n", resolver_start)
        resolver = source[resolver_start:resolver_end]
        self.assertIn("get_model_filename(", resolver)
        self.assertIn("get_compatible_local_model_filename(", resolver)

        load_start = source.index("def load_models")
        load_end = source.index("\ndef ", load_start + 4)
        load_source = source[load_start:load_end]
        self.assertIn("resolved_primary_model_path = local_file_name", load_source)
        self.assertEqual(
            load_source.count("recheck_dasiwa_checkpoint_admission("), 3,
        )
        load_call = load_source.index("model_type_handler.load_model(")
        checkpoint_rechecks = [
            index
            for index in range(len(load_source))
            if load_source.startswith("recheck_dasiwa_checkpoint_admission(", index)
        ]
        self.assertLess(checkpoint_rechecks[1], load_call)
        self.assertGreater(checkpoint_rechecks[2], load_call)
        load_cleanup = load_source.index("except Exception:", load_call)
        self.assertLess(checkpoint_rechecks[2], load_cleanup)
        self.assertIn("wan_model.release()", load_source[load_cleanup:])
        self.assertLess(
            load_source.index("recheck_dasiwa_checkpoint_admission("),
            load_source.index("Loading Model"),
        )

        generation_start = source.index("def _generate_video_impl")
        generation = source[generation_start:]
        admission_default = generation.index("dasiwa_checkpoint_admission = None")
        candidate_resolution = generation.index("dasiwa_lora_candidates = (")
        enforcement = generation.index("enforce_dasiwa_runtime(")
        reprofile = generation.index("_release_for_model_reprofile(")
        load = generation.index("wan_model, offloadobj = load_models(")
        admission_forward = generation.index(
            "h3_dasiwa_admission=dasiwa_checkpoint_admission",
        )
        self.assertLess(admission_default, candidate_resolution)
        self.assertLess(admission_default, admission_forward)
        self.assertLess(enforcement, reprofile)
        self.assertLess(enforcement, load)
        self.assertEqual(generation.count("recheck_dasiwa_lora_admission("), 2)
        self.assertIn("admitted_dasiwa_lora_path(", generation)
        launch = (APP / "launch.py").read_text(encoding="utf-8")
        planner = launch[
            launch.index("def _plan_generation_submission("):
            launch.index("\n@api.post(\"/api/v1/generate/plan\")")
        ]
        self.assertIn("_validate_h3_lora_request(body, plan)", planner)
        self.assertLess(
            planner.index("_prepare_h3_long_studio_request(body)"),
            planner.index("_validate_h3_lora_request(body, plan)"),
        )
        lora_load = generation.index("offload.load_loras_into_model(")
        lora_rechecks = [
            index
            for index in range(len(generation))
            if generation.startswith("recheck_dasiwa_lora_admission(", index)
        ]
        self.assertLess(lora_rechecks[0], lora_load)
        self.assertGreater(lora_rechecks[1], lora_load)
        lora_cleanup = generation.index("except BaseException:", lora_load)
        self.assertLess(lora_rechecks[1], lora_cleanup)
        self.assertIn("_unload_generation_loras()", generation[lora_cleanup:])

        residency_start = source.index("def get_requested_residency_identity")
        residency_end = source.index("\ndef ", residency_start + 4)
        self.assertNotIn(
            "dasiwa_checkpoint_admission",
            source[residency_start:residency_end],
        )

    def test_pinned_identity_keeps_owner_override_and_exact_base_gate(self):
        identity = h3_dasiwa.dasiwa_identity()
        self.assertEqual(
            identity["revision"],
            "da516a7394d11bc5264375697848ca8fe52ba406",
        )
        self.assertEqual(identity["size"], 794_888_664)
        self.assertEqual(
            identity["sha256"],
            "d2a9a723d97520232f17b6fec33335f9e94b03b2c67b56f91f16780355479274",
        )
        self.assertEqual(identity["authored_steps"], 4)
        self.assertFalse(identity["model_card_gate"])
        self.assertEqual(
            set(identity["incompatible_accelerators"]),
            {"turbo", "lightx2v", "spectrum", "sla", "matlow"},
        )

    def test_header_only_validation_and_status_require_exact_base_identity(self):
        metadata = {
            "compatible_main_sha256": h3_dasiwa.DASIWA_COMPATIBLE_BASE_SHA256,
            "compatibility_scope": "exact_checkpoint_sha256_only",
            "sampler_steps": "4",
            "tensor_count": "569",
            "validation_status": "static_projection_validated; perceptual_render_pending",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / h3_dasiwa.DASIWA_FILENAME
            _write_safetensors(path, metadata, 569)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with (
                mock.patch.object(h3_dasiwa, "DASIWA_SIZE", path.stat().st_size),
                mock.patch.object(h3_dasiwa, "DASIWA_SHA256", digest),
            ):
                validated = h3_dasiwa.validate_dasiwa_lora(path)
                self.assertTrue(validated["header_validated"])
                self.assertEqual(validated["tensor_count"], 569)
                mismatch = h3_dasiwa.experiment_status(
                    h3_dasiwa.DASIWA_ARTIFACT_ID,
                    root=root,
                    selected_checkpoint_status={
                        "verified": True,
                        "compatibility": "exact_base",
                        "sha256": "0" * 64,
                    },
                )
                self.assertFalse(mismatch["available"])
                self.assertIn("exact compatible", mismatch["reason"])
                self.assertNotIn("path", mismatch)
                available = h3_dasiwa.experiment_status(
                    h3_dasiwa.DASIWA_ARTIFACT_ID,
                    root=root,
                    selected_checkpoint_status={
                        "verified": True,
                        "compatibility": "exact_base",
                        "sha256": h3_dasiwa.DASIWA_COMPATIBLE_BASE_SHA256.upper(),
                    },
                )
                self.assertTrue(available["available"])
                self.assertFalse(available["download_required"])
                suspected = h3_dasiwa.experiment_status(
                    h3_dasiwa.DASIWA_ARTIFACT_ID,
                    root=root,
                    selected_checkpoint_status={
                        "verified": True,
                        "compatibility": "suspected_compatible_base",
                        "sha256": h3_dasiwa.DASIWA_SUSPECTED_BASE_SHA256,
                    },
                )
                self.assertTrue(suspected["available"])
                self.assertEqual(
                    suspected["compatibility"], "suspected_compatible_base"
                )

    def test_better_motion_is_download_required_without_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            status = h3_dasiwa.experiment_status(
                h3_dasiwa.BETTER_MOTION_ARTIFACT_ID,
                root=temporary,
            )
        self.assertFalse(status["available"])
        self.assertTrue(status["download_required"])
        self.assertEqual(status["filename"], h3_dasiwa.BETTER_MOTION_FILENAME)
        self.assertIn("downloaded explicitly", status["reason"])
        identity = h3_dasiwa.better_motion_identity()
        self.assertEqual(identity["version_id"], 3_257_589)
        self.assertEqual(identity["size"], 298_261_888)
        self.assertEqual(identity["benchmark_strengths"], [0.5, 0.7, 0.9, 1.0])

    def test_suspected_base_contract_is_exact_and_explicit(self):
        self.assertEqual(
            h3_dasiwa.DASIWA_SUSPECTED_BASE_FILENAME,
            "minimax_h3_ref2va_pruned_fp8_scaled.safetensors",
        )
        self.assertEqual(
            h3_dasiwa.DASIWA_SUSPECTED_BASE_SHA256,
            "f86f2f79ebd2d76eb8eeb46091e83982e6ff51d255747e7b16e92834b392b8e9",
        )
        self.assertEqual(h3_dasiwa.DASIWA_SUSPECTED_BASE_SIZE, 20_958_205_608)

    def test_unchanged_validation_failure_is_cached_for_status_polling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / h3_dasiwa.DASIWA_FILENAME).write_bytes(b"invalid")
            h3_dasiwa._cached_validation.cache_clear()
            failure = h3_dasiwa.H3ExperimentCompatibilityError("pinned failure")
            with mock.patch.object(
                h3_dasiwa, "validate_dasiwa_lora", side_effect=failure,
            ) as validator:
                statuses = [
                    h3_dasiwa.experiment_status(
                        h3_dasiwa.DASIWA_ARTIFACT_ID,
                        root=root,
                        selected_checkpoint_status={
                            "verified": True,
                            "compatibility": "exact_base",
                            "sha256": h3_dasiwa.DASIWA_COMPATIBLE_BASE_SHA256,
                        },
                    )
                    for _ in range(2)
                ]
            h3_dasiwa._cached_validation.cache_clear()
        self.assertEqual(validator.call_count, 1)
        self.assertTrue(all(status["reason"] == "pinned failure" for status in statuses))
        self.assertTrue(all(not status["available"] for status in statuses))

    def test_actual_suspected_checkpoint_receipt_drives_runtime_contract(self):
        metadata = {
            "compatible_main_sha256": h3_dasiwa.DASIWA_COMPATIBLE_BASE_SHA256,
            "compatibility_scope": "exact_checkpoint_sha256_only",
            "sampler_steps": "4",
            "tensor_count": "569",
            "validation_status": "static_projection_validated; perceptual_render_pending",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / h3_dasiwa.DASIWA_SUSPECTED_BASE_FILENAME
            checkpoint.write_bytes(b"tiny-ref2va-checkpoint")
            lora = root / h3_dasiwa.DASIWA_FILENAME
            _write_safetensors(lora, metadata, 569)
            checkpoint_digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            lora_digest = hashlib.sha256(lora.read_bytes()).hexdigest()
            receipt_root = root / "receipts"
            h3_dasiwa._cached_validation.cache_clear()
            with (
                mock.patch.object(
                    h3_dasiwa, "DASIWA_SUSPECTED_BASE_SIZE", checkpoint.stat().st_size,
                ),
                mock.patch.object(
                    h3_dasiwa, "DASIWA_SUSPECTED_BASE_SHA256", checkpoint_digest,
                ),
                mock.patch.object(h3_dasiwa, "DASIWA_SIZE", lora.stat().st_size),
                mock.patch.object(h3_dasiwa, "DASIWA_SHA256", lora_digest),
            ):
                verified = h3_dasiwa.enforce_dasiwa_runtime(
                    model_type="minimax_h3_ref2va",
                    activated_loras=[h3_dasiwa.DASIWA_FILENAME],
                    loras_multipliers="1.0",
                    num_inference_steps=4,
                    custom_settings={"h3_attention_engine": "sdpa"},
                    skip_steps_cache_type="",
                    selected_checkpoint_path=checkpoint,
                    selected_lora_path=lora,
                    receipt_root=receipt_root,
                )
                reused = h3_dasiwa.enforce_dasiwa_runtime(
                    model_type="minimax_h3_ref2va",
                    activated_loras=[h3_dasiwa.DASIWA_FILENAME],
                    loras_multipliers="1.0",
                    num_inference_steps=4,
                    custom_settings={},
                    skip_steps_cache_type="",
                    selected_checkpoint_path=checkpoint,
                    selected_lora_path=lora,
                    receipt_root=receipt_root,
                )
                rechecked = h3_dasiwa.recheck_dasiwa_checkpoint_admission(
                    reused, checkpoint, receipt_root=receipt_root,
                )
                with mock.patch.object(
                    h3_checkpoint_receipts,
                    "_stream_sha256",
                    side_effect=AssertionError("passive status hashed content"),
                ):
                    passive = h3_dasiwa.dasiwa_checkpoint_status(
                        checkpoint, receipt_root=receipt_root,
                    )
                priority_root = root / "new-priority-root"
                priority_root.mkdir()
                priority_candidate = priority_root / h3_dasiwa.DASIWA_FILENAME
                priority_candidate.write_bytes(b"different-priority-copy")
                admitted_choice = h3_dasiwa.admitted_dasiwa_lora_path(
                    reused, priority_candidate, lora,
                )
                arbitrary = root / "arbitrary_ref2va.safetensors"
                arbitrary.write_bytes(checkpoint.read_bytes())
                arbitrary_status = h3_dasiwa.dasiwa_checkpoint_status(
                    arbitrary, receipt_root=receipt_root,
                )
                lora_replacement = root / "replacement-lora.tmp"
                lora_replacement.write_bytes(lora.read_bytes())
                os.replace(lora_replacement, lora)
                with self.assertRaisesRegex(
                    h3_dasiwa.H3ExperimentCompatibilityError, "LoRA identity changed",
                ):
                    h3_dasiwa.recheck_dasiwa_lora_admission(reused, lora)
                lora_target = root / "dasiwa-target.safetensors"
                lora.rename(lora_target)
                lora.symlink_to(lora_target)
                with self.assertRaisesRegex(
                    h3_dasiwa.H3ExperimentCompatibilityError,
                    "regular owner",
                ):
                    h3_dasiwa.enforce_dasiwa_runtime(
                        model_type="minimax_h3_ref2va",
                        activated_loras=[h3_dasiwa.DASIWA_FILENAME],
                        loras_multipliers="1.0",
                        num_inference_steps=4,
                        custom_settings={},
                        skip_steps_cache_type="",
                        selected_checkpoint_path=checkpoint,
                        selected_lora_path=lora,
                        receipt_root=receipt_root,
                    )
                checkpoint_replacement = root / "replacement-checkpoint.tmp"
                checkpoint_replacement.write_bytes(checkpoint.read_bytes())
                os.replace(checkpoint_replacement, checkpoint)
                with self.assertRaisesRegex(
                    h3_dasiwa.H3ExperimentCompatibilityError,
                    "checkpoint identity changed",
                ):
                    h3_dasiwa.recheck_dasiwa_checkpoint_admission(
                        reused, checkpoint, receipt_root=receipt_root,
                    )
            h3_dasiwa._cached_validation.cache_clear()
        self.assertEqual(verified["compatibility"], "suspected_compatible_base")
        self.assertFalse(verified["receipt_reused"])
        self.assertTrue(reused["receipt_reused"])
        self.assertTrue(rechecked["receipt_reused"])
        self.assertTrue(passive["available"])
        self.assertEqual(admitted_choice, str(lora))
        self.assertNotIn("_checkpoint_binding", passive)
        self.assertFalse(arbitrary_status["available"])
        for private_key in ("path", "uid", "ino", "dev", "mtime_ns", "ctime_ns"):
            self.assertNotIn(private_key, verified)

    def test_passive_checkpoint_status_never_hashes_or_creates_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / h3_dasiwa.DASIWA_SUSPECTED_BASE_FILENAME
            checkpoint.write_bytes(b"tiny-ref2va-checkpoint")
            receipt_root = root / "receipts"
            digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            with (
                mock.patch.object(
                    h3_dasiwa, "DASIWA_SUSPECTED_BASE_SIZE", checkpoint.stat().st_size,
                ),
                mock.patch.object(h3_dasiwa, "DASIWA_SUSPECTED_BASE_SHA256", digest),
                mock.patch.object(
                    h3_checkpoint_receipts,
                    "_stream_sha256",
                    side_effect=AssertionError("passive status hashed content"),
                ),
            ):
                status = h3_dasiwa.dasiwa_checkpoint_status(
                    checkpoint, receipt_root=receipt_root,
                )
            self.assertFalse(status["available"])
            self.assertTrue(status["candidate"])
            self.assertTrue(status["preparation_required"])
            self.assertFalse(receipt_root.exists())

            lora_root = root / "loras"
            lora_root.mkdir()
            lora = lora_root / h3_dasiwa.DASIWA_FILENAME
            lora.write_bytes(b"tiny-lora-candidate")
            with (
                mock.patch.object(h3_dasiwa, "DASIWA_SIZE", lora.stat().st_size),
                mock.patch.object(
                    h3_dasiwa,
                    "_verified_artifact",
                    side_effect=AssertionError("passive LoRA status hashed content"),
                ),
            ):
                lora_status = h3_dasiwa.dasiwa_lora_candidate_status(
                    root=lora_root,
                )
            self.assertTrue(lora_status["candidate"])
            self.assertTrue(lora_status["preparation_required"])

            wrong_size = root / "wrong-size" / h3_dasiwa.DASIWA_SUSPECTED_BASE_FILENAME
            wrong_size.parent.mkdir()
            wrong_size.write_bytes(b"wrong")
            wrong_name = root / "wrong-name.safetensors"
            wrong_name.write_bytes(checkpoint.read_bytes())
            with mock.patch.object(
                h3_dasiwa, "DASIWA_SUSPECTED_BASE_SIZE", checkpoint.stat().st_size,
            ):
                wrong_size_status = h3_dasiwa.dasiwa_checkpoint_status(
                    wrong_size, receipt_root=receipt_root,
                )
                wrong_name_status = h3_dasiwa.dasiwa_checkpoint_status(
                    wrong_name, receipt_root=receipt_root,
                )
            self.assertFalse(wrong_size_status.get("candidate", False))
            self.assertFalse(wrong_name_status.get("candidate", False))

    def test_runtime_rejects_bad_steps_strength_stack_cache_and_checkpoint(self):
        common = {
            "model_type": "minimax_h3_ref2va",
            "activated_loras": [h3_dasiwa.DASIWA_FILENAME],
            "loras_multipliers": "1.0",
            "num_inference_steps": 4,
            "custom_settings": {},
            "skip_steps_cache_type": "",
            "selected_checkpoint_path": "missing.safetensors",
            "selected_lora_path": "missing-lora.safetensors",
        }
        for field, value, message in (
            ("model_type", "minimax_h3", "Ref2VA"),
            ("num_inference_steps", 5, "four"),
            ("loras_multipliers", "0.9", "1.0"),
            ("activated_loras", [h3_dasiwa.DASIWA_FILENAME, "other.safetensors"], "stacked"),
            ("skip_steps_cache_type", "tea", "step cache"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                h3_dasiwa.H3ExperimentCompatibilityError, message,
            ):
                h3_dasiwa.enforce_dasiwa_runtime(**{**common, field: value})
        for accelerator_key in (
            "h3_turbo_profile", "h3_lightx2v_profile", "h3_spectrum_profile",
            "h3_sla_profile", "h3_matlow_profile",
        ):
            with self.subTest(accelerator_key=accelerator_key), self.assertRaisesRegex(
                h3_dasiwa.H3ExperimentCompatibilityError, "accelerator",
            ):
                h3_dasiwa.enforce_dasiwa_runtime(**{
                    **common,
                    "custom_settings": {accelerator_key: "enabled"},
                })
        with self.assertRaisesRegex(
            h3_dasiwa.H3ExperimentCompatibilityError, "verified Dasiwa contract",
        ):
            h3_dasiwa.enforce_dasiwa_runtime(**common)

    def test_plan_validation_rejects_stacked_loras_and_mixed_segments(self):
        common = {
            "activated_loras": [h3_dasiwa.DASIWA_FILENAME],
            "loras_multipliers": "1.0",
            "num_inference_steps": 4,
            "custom_settings": {},
        }
        self.assertTrue(h3_dasiwa.validate_dasiwa_request(
            model_types=["minimax_h3_ref2va"], **common,
        ))
        self.assertFalse(h3_dasiwa.validate_dasiwa_request(
            model_types=["minimax_h3"],
            activated_loras=[],
            loras_multipliers="",
            num_inference_steps=28,
            custom_settings={},
        ))
        with self.assertRaisesRegex(
            h3_dasiwa.H3ExperimentCompatibilityError, "stacked",
        ):
            h3_dasiwa.validate_dasiwa_request(
                model_types=["minimax_h3_ref2va"],
                **{
                    **common,
                    "activated_loras": [
                        h3_dasiwa.DASIWA_FILENAME, "other.safetensors",
                    ],
                },
            )
        with self.assertRaisesRegex(
            h3_dasiwa.H3ExperimentCompatibilityError, "every planned shot",
        ):
            h3_dasiwa.validate_dasiwa_request(
                model_types=["minimax_h3", "minimax_h3_ref2va"],
                **common,
            )

    def test_dasiwa_status_rejects_final_symlink_and_wrong_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.safetensors"
            target.write_bytes(b"artifact")
            link = root / h3_dasiwa.DASIWA_FILENAME
            link.symlink_to(target)
            status = h3_dasiwa.experiment_status(
                h3_dasiwa.DASIWA_ARTIFACT_ID,
                root=root,
                selected_checkpoint_status={
                    "verified": True,
                    "compatibility": "exact_base",
                    "sha256": h3_dasiwa.DASIWA_COMPATIBLE_BASE_SHA256,
                },
            )
            self.assertFalse(status["available"])
            self.assertFalse(status["downloaded"])
            self.assertIn("regular owner", status["reason"])

            link.unlink()
            link.write_bytes(b"artifact")
            with mock.patch.object(h3_dasiwa, "_same_owner", return_value=False):
                status = h3_dasiwa.experiment_status(
                    h3_dasiwa.DASIWA_ARTIFACT_ID,
                    root=root,
                    selected_checkpoint_status={
                        "verified": True,
                        "compatibility": "exact_base",
                        "sha256": h3_dasiwa.DASIWA_COMPATIBLE_BASE_SHA256,
                    },
                )
            self.assertFalse(status["available"])
            self.assertIn("regular owner", status["reason"])


if __name__ == "__main__":
    unittest.main()
