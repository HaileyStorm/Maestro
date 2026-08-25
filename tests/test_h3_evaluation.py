"""Offline, model-free regressions for the MiniMax H3 evaluation service."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.h3_evaluation import (  # noqa: E402
    CONDITIONING_ENCODE_SEED,
    H3EvaluationError,
    MINIMAX_H3_10EROS_BETA3_FULL_ID,
    MINIMAX_H3_10EROS_BETA3_SKIP_ID,
    MINIMAX_H3_FL2VA_ID,
    MINIMAX_H3_REF2VA_ID,
    build_h3_evaluation_manifest,
    build_h3_evaluation_report,
    get_h3_profile_catalog,
    validate_h3_evaluation_manifest,
)


def _manifest(**overrides):
    values = {
        "project_id": "project-alpha",
        "job_id": "job-0001",
        "model_type": MINIMAX_H3_FL2VA_ID,
        "resolved_seed": 123456,
        "prompt": "A factual evaluation prompt.",
        "frame_count": 124,
        "resolution": "864x480",
        "conditioning": {"first_frame": "inputs/start.png"},
        "output_artifacts": ["outputs/result.mp4"],
        "explicit": True,
    }
    values.update(overrides)
    return build_h3_evaluation_manifest(**values)


class H3CatalogTests(unittest.TestCase):
    def test_catalog_preserves_official_ids_revisions_and_component_roles(self):
        catalog = get_h3_profile_catalog()
        self.assertEqual(catalog["pinned_as_of"], "2026-08-06")
        self.assertEqual(catalog["experimental_updated_as_of"], "2026-08-25")
        profiles = catalog["profiles"]
        self.assertIn("minimax_h3", profiles)
        self.assertIn("minimax_h3_ref2va", profiles)

        fl2va_repositories = profiles["minimax_h3"]["repositories"]
        ref2va_repositories = profiles["minimax_h3_ref2va"]["repositories"]
        self.assertTrue(any(
            item["revision"] == "0543966fbdce5ba05709a8f2031c94bdba629b4a"
            for item in fl2va_repositories
        ))
        self.assertTrue(any(
            item["revision"] == "eb8a16107c595128b3a578f82d2ce2f75920c355"
            for item in ref2va_repositories
        ))
        self.assertTrue(any(
            item["revision"] == "5d9b308a59ab12e67147f191e184baf704185bd1"
            for item in fl2va_repositories
        ))
        self.assertTrue(any(
            item["revision"] == "abc5e9bf71fd38f53cd471bc3acaa84bc5ecbfdc"
            for item in fl2va_repositories
        ))

        w4a8 = profiles["kijai_minimax_h3_w4a8_convrot"]
        self.assertEqual(
            w4a8["revision"], "8b48334e6263a39b34eef85f9f5e271ba4506945",
        )
        self.assertTrue(w4a8["experimental"])
        self.assertFalse(w4a8["enabled_by_default"])
        self.assertIn("work in progress", w4a8["upstream_pr_notes"][0]["note"])
        self.assertTrue(any(
            note["url"].endswith("/pull/15334")
            for note in w4a8["upstream_pr_notes"]
        ))

        encoder = profiles[
            "ethanfel_qwen3vl_32b_ultra_heretic_h3_int8_convrot"
        ]
        self.assertEqual(
            encoder["revision"], "e8967f6a39ea5b4939a1aff81be3e8706490c0e8",
        )
        self.assertEqual(encoder["component_role"], "conditioning_encoder_only")
        self.assertFalse(encoder["video_model"])
        self.assertEqual(encoder["license"], "Apache-2.0")
        self.assertFalse(encoder["enabled_by_default"])

        skip = profiles[MINIMAX_H3_10EROS_BETA3_SKIP_ID]
        full = profiles[MINIMAX_H3_10EROS_BETA3_FULL_ID]
        self.assertEqual(skip["maestro_experiment_policy"]["priority"], 1)
        self.assertEqual(full["maestro_experiment_policy"]["priority"], 2)
        self.assertEqual(skip["pinned_as_of"], "2026-08-25")
        self.assertEqual(full["pinned_as_of"], "2026-08-25")
        self.assertEqual(skip["mode"], "turbo_hybrid")
        self.assertEqual(full["mode"], "turbo_hybrid")
        self.assertEqual(skip["revision"], "09beb98782a6feb2f44c39c46179743ca8607c6c")
        self.assertEqual(full["revision"], "84ea7a6ec06e0cb5f2f35615e25e3529c5ec6c02")
        self.assertEqual(skip["artifact_size_bytes"], 22_513_576_472)
        self.assertEqual(full["artifact_size_bytes"], 20_973_147_816)
        self.assertEqual(skip["layer_policy"]["marker_count"], 184)
        self.assertEqual(full["layer_policy"]["marker_count"], 200)
        self.assertFalse(skip["execution_available"])
        self.assertFalse(full["execution_available"])
        self.assertFalse(skip["enabled_by_default"])
        self.assertFalse(full["enabled_by_default"])
        self.assertNotEqual(skip["model_type"], MINIMAX_H3_FL2VA_ID)
        self.assertNotEqual(skip["model_type"], MINIMAX_H3_REF2VA_ID)

    def test_catalog_is_returned_as_an_isolated_copy(self):
        first = get_h3_profile_catalog()
        first["profiles"]["minimax_h3"]["label"] = "changed"
        second = get_h3_profile_catalog()
        self.assertNotEqual(second["profiles"]["minimax_h3"]["label"], "changed")


class H3ManifestTests(unittest.TestCase):
    def test_manifest_is_deterministic_portable_and_records_runtime_facts(self):
        first = _manifest()
        second = _manifest()
        self.assertEqual(first, second)
        self.assertEqual(first["manifest_id"], second["manifest_id"])
        json.dumps(first)
        validate_h3_evaluation_manifest(first)

        self.assertEqual(first["profile"]["id"], "minimax_h3")
        self.assertEqual(first["request"]["resolved_seed"], 123456)
        self.assertEqual(first["request"]["conditioning_encode_seed"], 42)
        self.assertEqual(first["conditioning"]["encode_seed"], 42)
        self.assertEqual(first["geometry"]["fps"], 24)
        self.assertEqual(first["geometry"]["frame_grid"], {
            "modulus": 17, "remainder": 5,
        })
        self.assertEqual(first["scheduler"]["video_shift"], 12.0)
        self.assertEqual(first["scheduler"]["audio_shift"], 3.0)
        self.assertTrue(first["artifact_policy"]["private"])
        self.assertEqual(first["lineage"], {
            "project_id": "project-alpha", "job_id": "job-0001",
        })
        serialized = json.dumps(first)
        self.assertNotIn(str(Path(__file__).resolve().parent), serialized)

    def test_unresolved_or_invalid_seeds_are_rejected(self):
        for seed in (-1, None, "12", True, 2**32):
            with self.subTest(seed=seed), self.assertRaises(H3EvaluationError):
                _manifest(resolved_seed=seed)

    def test_native_frame_grid_canvas_and_scheduler_limits_are_enforced(self):
        for changes in (
            {"frame_count": 123},
            {"frame_count": 125},
            {"frame_count": 346},
            {"resolution": "800x600"},
            {"fps": 30},
            {"sampling_steps": 1},
        ):
            with self.subTest(changes=changes), self.assertRaises(H3EvaluationError):
                _manifest(**changes)

    def test_fl2va_and_ref2va_conditioning_are_mutually_exclusive(self):
        with self.assertRaisesRegex(H3EvaluationError, "FL2VA accepts only"):
            _manifest(conditioning={"images": ["inputs/ref.png"]})

        ref = _manifest(
            model_type=MINIMAX_H3_REF2VA_ID,
            frame_count=107,
            conditioning={
                "images": ["inputs/identity.png"],
                "videos": [{"path": "inputs/motion.mp4", "duration_seconds": 4}],
                "audio": [{"path": "inputs/voice.wav", "duration_seconds": 3}],
            },
        )
        self.assertEqual(ref["profile"]["id"], "minimax_h3_ref2va")
        self.assertEqual(ref["conditioning"]["mode"], "semantic_references")
        self.assertEqual(
            ref["geometry"]["mode_limits"]["minimum_duration_seconds"], 4.0,
        )
        with self.assertRaisesRegex(H3EvaluationError, "Ref2VA accepts only"):
            _manifest(
                model_type=MINIMAX_H3_REF2VA_ID,
                frame_count=107,
                conditioning={"first_frame": "inputs/start.png"},
            )

    def test_ref2va_reference_limits_and_relative_paths_are_enforced(self):
        with self.assertRaisesRegex(H3EvaluationError, "at least as many visual"):
            _manifest(
                model_type=MINIMAX_H3_REF2VA_ID,
                frame_count=107,
                conditioning={
                    "audio": [{"path": "inputs/voice.wav", "duration_seconds": 3}],
                },
            )
        with self.assertRaisesRegex(H3EvaluationError, "total at most 15"):
            _manifest(
                model_type=MINIMAX_H3_REF2VA_ID,
                frame_count=107,
                conditioning={
                    "images": ["inputs/a.png", "inputs/b.png"],
                    "audio": [
                        {"path": "inputs/a.wav", "duration_seconds": 8},
                        {"path": "inputs/b.wav", "duration_seconds": 8},
                    ],
                },
            )
        for path in ("/tmp/output.mp4", "C:\\output.mp4", "outputs/../escape.mp4"):
            with self.subTest(path=path), self.assertRaises(H3EvaluationError):
                _manifest(output_artifacts=[path])

    def test_experimental_profiles_are_off_without_explicit_opt_in(self):
        with self.assertRaisesRegex(H3EvaluationError, "allow_experimental"):
            _manifest(profile_id="kijai_minimax_h3_w4a8_convrot")
        manifest = _manifest(
            profile_id="kijai_minimax_h3_w4a8_convrot",
            encoder_profile_id=(
                "ethanfel_qwen3vl_32b_ultra_heretic_h3_int8_convrot"
            ),
            allow_experimental=True,
        )
        self.assertTrue(manifest["profile"]["experimental"])
        self.assertEqual(
            manifest["profile"]["encoder_option"]["component_role"],
            "conditioning_encoder_only",
        )

    def test_beta3_scaffold_manifests_are_portable_distinct_and_non_executable(self):
        manifests = []
        for profile_id in (
            MINIMAX_H3_10EROS_BETA3_SKIP_ID,
            MINIMAX_H3_10EROS_BETA3_FULL_ID,
        ):
            with self.subTest(profile_id=profile_id):
                values = {
                    "project_id": "project-beta3",
                    "job_id": f"job-{profile_id.rsplit('_', 1)[-1]}",
                    "model_type": profile_id,
                    "resolved_seed": 77,
                    "prompt": "A content-free scaffold fixture.",
                    "frame_count": 124,
                    "resolution": "608x352",
                    "sampling_steps": 6,
                    "conditioning": {},
                    "profile_id": profile_id,
                    "allow_experimental": True,
                    "sampler_candidate": (
                        "er_sde/simple"
                        if profile_id == MINIMAX_H3_10EROS_BETA3_SKIP_ID
                        else "multires/simple"
                    ),
                }
                first = build_h3_evaluation_manifest(**values)
                second = build_h3_evaluation_manifest(**values)
                self.assertEqual(first, second)
                validate_h3_evaluation_manifest(first)
                self.assertTrue(first["profile"]["scaffold_only"])
                self.assertFalse(first["profile"]["execution_available"])
                self.assertEqual(first["conditioning"]["mode"], "scaffold_only")
                self.assertEqual(
                    first["request"]["sampler_candidate"],
                    values["sampler_candidate"],
                )
                self.assertNotIn(str(Path(__file__).resolve().parent), json.dumps(first))
                report = build_h3_evaluation_report(first)
                self.assertEqual(report["execution"]["status"], "skipped")
                self.assertEqual(
                    report["configuration_facts"]["sampler_candidate"],
                    values["sampler_candidate"],
                )
                with self.assertRaisesRegex(H3EvaluationError, "cannot be passed"):
                    build_h3_evaluation_report(first, lambda _value: {})
                manifests.append(first)
        self.assertNotEqual(manifests[0]["manifest_id"], manifests[1]["manifest_id"])
        with self.assertRaisesRegex(H3EvaluationError, "do not claim"):
            build_h3_evaluation_manifest(
                project_id="project-beta3",
                job_id="job-bad-conditioning",
                model_type=MINIMAX_H3_10EROS_BETA3_SKIP_ID,
                resolved_seed=77,
                prompt="A content-free scaffold fixture.",
                frame_count=124,
                resolution="608x352",
                sampling_steps=6,
                conditioning={"first_frame": "inputs/first.png"},
                profile_id=MINIMAX_H3_10EROS_BETA3_SKIP_ID,
                allow_experimental=True,
                sampler_candidate="er_sde/simple",
            )

        base_values = {
            "project_id": "project-beta3",
            "job_id": "job-invalid-schedule",
            "model_type": MINIMAX_H3_10EROS_BETA3_SKIP_ID,
            "resolved_seed": 77,
            "prompt": "A content-free scaffold fixture.",
            "frame_count": 124,
            "resolution": "608x352",
            "conditioning": {},
            "profile_id": MINIMAX_H3_10EROS_BETA3_SKIP_ID,
            "allow_experimental": True,
        }
        for changes, message in (
            ({"sampling_steps": 5, "sampler_candidate": "er_sde/simple"}, "six"),
            ({"sampling_steps": 7, "sampler_candidate": "er_sde/simple"}, "six"),
            ({"sampling_steps": 6}, "sampler_candidate"),
            ({"sampling_steps": 6, "sampler_candidate": "euler/simple"}, "sampler_candidate"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                H3EvaluationError, message,
            ):
                build_h3_evaluation_manifest(**base_values, **changes)

    def test_explicit_defaults_private_but_caller_may_deliberately_override(self):
        self.assertTrue(_manifest(explicit=True)["artifact_policy"]["private"])
        self.assertFalse(
            _manifest(explicit=True, private=False)["artifact_policy"]["private"]
        )
        self.assertFalse(_manifest(explicit=False)["artifact_policy"]["private"])

    def test_manifest_tampering_is_detected(self):
        manifest = _manifest()
        manifest["scheduler"]["video_shift"] = 0.0
        with self.assertRaisesRegex(H3EvaluationError, "not the canonical"):
            validate_h3_evaluation_manifest(manifest)


class H3ReportTests(unittest.TestCase):
    def test_offline_report_skips_execution_and_never_invents_measurements(self):
        manifest = _manifest()
        first = build_h3_evaluation_report(manifest)
        second = build_h3_evaluation_report(manifest)
        self.assertEqual(first, second)
        self.assertEqual(first["execution"], {
            "status": "skipped", "reason": "executor_not_provided",
        })
        for metric in first["metrics"].values():
            self.assertEqual(metric["status"], "unavailable")
            self.assertIsNone(metric["value"])
        self.assertNotIn("winner", json.dumps(first).lower())

    def test_executor_receives_exact_unchanged_manifest_and_reports_observations(self):
        manifest = _manifest()
        snapshot = copy.deepcopy(manifest)
        seen = []

        def executor(value):
            seen.append(value)
            return {
                "status": "completed",
                "observed_metrics": {
                    "generation_wall_time_seconds": 12.5,
                    "peak_gpu_memory_bytes": 1_234_567,
                    "output_video_frames": 124,
                    "artifact_sha256": "a" * 64,
                },
                "artifacts": ["outputs/result.mp4"],
            }

        report = build_h3_evaluation_report(manifest, executor)
        self.assertIs(seen[0], manifest)
        self.assertEqual(manifest, snapshot)
        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(
            report["metrics"]["peak_gpu_memory_bytes"],
            {
                "status": "available",
                "value": 1_234_567,
                "source": "executor_observation",
            },
        )
        self.assertEqual(
            report["metrics"]["output_video_fps"]["status"], "unavailable",
        )

    def test_executor_mutation_or_unsupported_metrics_fail_closed(self):
        manifest = _manifest()

        def mutate(value):
            value["request"]["resolved_seed"] = 9
            return {"status": "completed"}

        with self.assertRaisesRegex(H3EvaluationError, "mutated"):
            build_h3_evaluation_report(manifest, mutate)

        with self.assertRaisesRegex(H3EvaluationError, "unsupported observed"):
            build_h3_evaluation_report(
                _manifest(),
                lambda _value: {
                    "status": "completed",
                    "observed_metrics": {"subjective_quality_score": 100},
                },
            )

    def test_executor_exception_is_reported_without_fabricated_metrics(self):
        def fail(_manifest):
            raise RuntimeError("local runtime unavailable")

        report = build_h3_evaluation_report(_manifest(), fail)
        self.assertEqual(report["execution"], {
            "status": "failed",
            "reason": "executor_raised",
            "error_type": "RuntimeError",
        })
        self.assertTrue(all(
            metric["status"] == "unavailable"
            for metric in report["metrics"].values()
        ))


if __name__ == "__main__":
    unittest.main()
