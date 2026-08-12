"""Pure evidence, interpolation, and policy tests for model residency."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.model_residency import (  # noqa: E402
    ModelResidencyError,
    ModelResidencyEvidenceStore,
    ModelResidencyPersistenceError,
    ModelResidencyValidationError,
    ResidencySingleflight,
    build_residency_key,
    choose_profile_action,
)
import services.model_residency as residency_module  # noqa: E402


def residency_parts(
    *,
    artifact="minimax-h3-nvfp4",
    frames=10,
    free_vram=10,
    free_host=40,
    epoch=1,
    budget=12,
    profile=4,
):
    return {
        "model": {
            "artifact_id": artifact,
            "artifact_revision": "sha256-abc123",
            "family": "minimax_h3",
            "quantization": "nvfp4",
        },
        "runtime": {
            "runtime_id": "wan2gp",
            "runtime_version": "1.4",
            "build_id": "maestro-2026-08-11",
            "driver_version": "580.82",
        },
        "hardware": {
            "accelerator": "nvidia-rtx4090",
            "total_vram_gib": 24,
            "total_host_ram_gib": 64,
        },
        "workload": {
            "kind": "h3-video",
            "width": 960,
            "height": 544,
            "frame_count": frames,
            "steps": 20,
            "reference_count": 1,
            "lora_count": 0,
            "stage_count": 1,
        },
        "settings": {
            "offload_profile": profile,
            "resident_budget_gib": budget,
            "attention_backend": "sdpa",
            "cache_mode": "block-swap",
            "weight_quantization": "nvfp4",
        },
        "condition": {
            "free_vram_band_gib": free_vram,
            "free_host_ram_band_gib": free_host,
            "residency_epoch_band": epoch,
        },
        "policy_revision": 1,
    }


def residency_key(**updates):
    return build_residency_key(**residency_parts(**updates))


class ModelResidencyEvidenceTests(unittest.TestCase):
    def test_key_is_exact_content_free_and_rejects_private_fields(self):
        base = residency_key()
        changed = residency_key(free_host=32)
        self.assertNotEqual(base["exact_key"], changed["exact_key"])
        self.assertEqual(base["compatibility_key"], changed["compatibility_key"])
        encoded = json.dumps(base, sort_keys=True)
        for forbidden in ("prompt", "path", "project", "session", "media"):
            self.assertNotIn(forbidden, encoded.lower())

        parts = residency_parts()
        with self.assertRaises(ModelResidencyValidationError):
            build_residency_key(
                **{**parts, "model": {
                    **parts["model"], "prompt": "PRIVATE STORY",
                }},
            )
        with self.assertRaises(ModelResidencyValidationError):
            build_residency_key(
                **{**parts, "model": {
                    **parts["model"], "artifact_id": "/private/model/path",
                }},
            )

    def test_all_exact_fields_are_required_and_policy_revision_is_an_integer(self):
        omissions = (
            ("model", "family"),
            ("runtime", "runtime_version"),
            ("runtime", "driver_version"),
            ("workload", "reference_count"),
            ("workload", "lora_count"),
            ("workload", "stage_count"),
            ("settings", "attention_backend"),
            ("settings", "cache_mode"),
            ("settings", "weight_quantization"),
        )
        for group, field in omissions:
            parts = residency_parts()
            parts[group].pop(field)
            with self.subTest(group=group, field=field), self.assertRaises(
                ModelResidencyValidationError
            ):
                build_residency_key(**parts)
        parts = residency_parts()
        parts["policy_revision"] = 1.9
        with self.assertRaises(ModelResidencyValidationError):
            build_residency_key(**parts)

    def test_allowed_tokens_are_domain_hashed_before_key_or_disk_persistence(self):
        private = "PRIVATE_SENTINEL_TOKEN"
        parts = residency_parts(artifact=private)
        parts["runtime"]["build_id"] = private
        key = build_residency_key(**parts)
        self.assertNotIn(private, json.dumps(key, sort_keys=True))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "residency.json")
            ModelResidencyEvidenceStore(path).record_success(
                key, observed_at=100,
            )
            self.assertNotIn(private, path.read_text(encoding="ascii"))

    def test_residency_epoch_is_a_categorical_compatibility_boundary(self):
        epoch_one = residency_key(frames=8, epoch=1, budget=12)
        epoch_ninety_nine = residency_key(frames=12, epoch=99, budget=10)
        target = residency_key(frames=10, epoch=50, budget=14)
        self.assertNotEqual(
            epoch_one["compatibility_key"],
            epoch_ninety_nine["compatibility_key"],
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json")
            )
            store.record_success(epoch_one, observed_at=100)
            store.record_success(epoch_ninety_nine, observed_at=101)
            self.assertEqual(
                store.recommend(target, now=110)["status"], "unsupported",
            )
    def test_prior_run_survives_restart_and_non_destructive_a_b_a_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "residency.json")
            a = residency_key(frames=10, budget=12)
            b = residency_key(frames=14, budget=10)
            store = ModelResidencyEvidenceStore(path)
            store.record_success(a, observed_at=100)
            store.record_success(b, observed_at=200)

            restarted = ModelResidencyEvidenceStore(path)
            first_a = restarted.recommend(a, now=300)
            observed_b = restarted.recommend(b, now=300)
            second_a = restarted.recommend(a, now=300)
            self.assertEqual(first_a, second_a)
            self.assertEqual(first_a["resident_budget_gib"], 12)
            self.assertEqual(observed_b["resident_budget_gib"], 10)
            self.assertEqual(first_a["provenance"]["source"], "prior_run")
            self.assertEqual(first_a["provenance"]["kind"], "exact")
            self.assertEqual(restarted.snapshot()["prior_run_records"], 2)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_shipped_seed_is_immutable_and_exact_prior_run_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            key = residency_key()
            seed = {
                "evidence_version": 1,
                "key": key,
                "sample_count": 7,
            }
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json"), shipped_evidence=[seed],
            )
            seed["sample_count"] = 999
            shipped = store.recommend(key, now=100)
            self.assertEqual(shipped["provenance"]["source"], "shipped")
            self.assertEqual(shipped["provenance"]["sample_count"], 7)

            store.record_success(key, observed_at=110)
            prior = store.recommend(key, now=120)
            self.assertEqual(prior["provenance"]["source"], "prior_run")
            self.assertEqual(prior["confidence"], "high")

    def test_oom_is_exact_condition_scoped_and_ages_without_deleting_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "residency.json")
            exact = residency_key(free_host=40, epoch=1, budget=12)
            other_condition = residency_key(free_host=32, epoch=2, budget=12)
            store = ModelResidencyEvidenceStore(path, oom_ttl_seconds=120)
            store.record_success(exact, observed_at=100)
            store.record_success(other_condition, observed_at=101)
            store.record_oom(
                exact, phase="finalization", required_margin_gib=2,
                observed_at=110,
            )

            pressured = store.recommend(exact, now=120)
            unaffected = store.recommend(other_condition, now=120)
            self.assertEqual(pressured["resident_budget_gib"], 10)
            self.assertEqual(pressured["active_oom_count"], 1)
            self.assertEqual(pressured["oom_phases"], ["finalization"])
            self.assertEqual(unaffected["resident_budget_gib"], 12)
            self.assertEqual(unaffected["active_oom_count"], 0)

            aged = ModelResidencyEvidenceStore(
                path, oom_ttl_seconds=120,
            ).recommend(exact, now=231)
            self.assertEqual(aged["resident_budget_gib"], 12)
            self.assertEqual(aged["active_oom_count"], 0)
            self.assertEqual(aged["provenance"]["kind"], "exact")

    def test_newer_oom_margin_replaces_old_margin_instead_of_poisoning_forever(self):
        with tempfile.TemporaryDirectory() as directory:
            key = residency_key()
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json"), oom_ttl_seconds=120,
            )
            store.record_success(key, observed_at=10)
            store.record_oom(
                key, phase="model_load", required_margin_gib=4,
                observed_at=20,
            )
            store.record_oom(
                key, phase="model_load", required_margin_gib=1,
                observed_at=30,
            )
            recommendation = store.recommend(key, now=31)
            self.assertEqual(recommendation["resident_budget_gib"], 11)

    def test_later_success_does_not_erase_recent_oom_before_its_condition_ttl(self):
        with tempfile.TemporaryDirectory() as directory:
            key = residency_key()
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json"), oom_ttl_seconds=120,
            )
            store.record_oom(
                key, phase="model_load", required_margin_gib=2,
                observed_at=20,
            )
            store.record_success(key, observed_at=30)
            still_conservative = store.recommend(key, now=31)
            self.assertEqual(still_conservative["resident_budget_gib"], 10)
            self.assertEqual(still_conservative["active_oom_count"], 1)
            aged = store.recommend(key, now=141)
            self.assertEqual(aged["resident_budget_gib"], 12)
            self.assertEqual(aged["active_oom_count"], 0)

    def test_nearby_interpolation_is_compatible_conservative_and_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "residency.json")
            store = ModelResidencyEvidenceStore(path)
            low = residency_key(frames=8, free_vram=8, free_host=32, budget=12)
            high = residency_key(frames=12, free_vram=12, free_host=48, budget=10)
            target = residency_key(frames=10, free_vram=10, free_host=40, budget=14)
            store.record_success(low, observed_at=100)
            store.record_success(high, observed_at=101)

            estimate = store.recommend(target, now=110)
            self.assertEqual(estimate["status"], "supported")
            self.assertEqual(estimate["provenance"]["kind"], "interpolation")
            self.assertEqual(estimate["provenance"]["source"], "prior_run")
            self.assertEqual(estimate["confidence"], "medium")
            self.assertLess(estimate["resident_budget_gib"], 10)
            self.assertGreater(estimate["uncertainty"]["resident_budget_gib"], 0)

            incompatible = residency_key(
                artifact="other-artifact", frames=10, budget=14,
            )
            unsupported = store.recommend(incompatible, now=110)
            self.assertEqual(unsupported["status"], "unsupported")
            self.assertEqual(unsupported["confidence"], "insufficient")

    def test_nearby_prior_run_precedes_shipped_but_exact_shipped_still_dominates(self):
        with tempfile.TemporaryDirectory() as directory:
            shipped_near = residency_key(frames=8, budget=9)
            exact_target = residency_key(frames=10, budget=14)
            seed = {
                "evidence_version": 1,
                "key": shipped_near,
                "sample_count": 4,
            }
            store = ModelResidencyEvidenceStore(
                Path(directory, "residency.json"), shipped_evidence=[seed],
            )
            store.record_success(residency_key(frames=12, budget=10), observed_at=50)
            nearby = store.recommend(exact_target, now=60)
            self.assertEqual(nearby["provenance"]["source"], "prior_run")

            exact_seed = {
                "evidence_version": 1,
                "key": exact_target,
                "sample_count": 2,
            }
            exact_store = ModelResidencyEvidenceStore(
                Path(directory, "other.json"),
                shipped_evidence=[seed, exact_seed],
            )
            exact_store.record_success(
                residency_key(frames=12, budget=10), observed_at=50,
            )
            exact = exact_store.recommend(exact_target, now=60)
            self.assertEqual(exact["provenance"]["source"], "shipped")
            self.assertEqual(exact["provenance"]["kind"], "exact")

    def test_store_is_bounded_and_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "residency.json")
            store = ModelResidencyEvidenceStore(path, max_records=2)
            for index in range(3):
                store.record_success(
                    residency_key(frames=8 + index, budget=12 - index),
                    observed_at=100 + index,
                )
            self.assertEqual(store.snapshot()["prior_run_records"], 2)
            path.write_text('{"schema_version":999}', encoding="ascii")
            os.chmod(path, 0o600)
            with self.assertRaises(ModelResidencyPersistenceError):
                store.snapshot()

    def test_target_symlink_fails_closed_with_and_without_no_follow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "residency.json")
            external = Path(directory, "external.json")
            store = ModelResidencyEvidenceStore(path)
            store.record_success(residency_key(), observed_at=100)
            os.replace(path, external)
            try:
                path.symlink_to(external)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlinks unavailable: {error}")
            with self.assertRaises(ModelResidencyPersistenceError):
                store._read(None)
            # Deterministically exercise Windows' O_NOFOLLOW=0 branch even
            # when this suite runs on Linux.
            with mock.patch.object(residency_module.os, "name", "nt"), \
                    mock.patch.object(
                        residency_module.os, "O_NOFOLLOW", 0, create=True,
                    ):
                with self.assertRaises(ModelResidencyPersistenceError):
                    store._read(None)

    def test_windows_branch_binds_open_handle_to_lstat_target_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "residency.json")
            store = ModelResidencyEvidenceStore(path)
            store.record_success(residency_key(), observed_at=100)
            actual = os.lstat(path)
            fields = list(actual)
            fields[1] += 1  # st_ino: simulate replacement between lstat/open.
            mismatched = os.stat_result(fields)
            with mock.patch.object(residency_module.os, "name", "nt"), \
                    mock.patch.object(
                        residency_module.os, "O_NOFOLLOW", 0, create=True,
                    ), mock.patch.object(
                        residency_module.os, "lstat", return_value=mismatched,
                    ):
                with self.assertRaises(ModelResidencyPersistenceError):
                    store._read(None)

    def test_reopening_with_a_lower_bound_uses_only_newest_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "residency.json")
            original = ModelResidencyEvidenceStore(path, max_records=4)
            keys = []
            for index in range(4):
                key = residency_key(frames=8 + index, budget=12 - index)
                keys.append(key)
                original.record_success(key, observed_at=100 + index)
            bounded = ModelResidencyEvidenceStore(path, max_records=2)
            self.assertEqual(bounded.snapshot()["prior_run_records"], 2)
            self.assertNotEqual(
                bounded.recommend(keys[0], now=110)["provenance"]["kind"],
                "exact",
            )


class ModelResidencyPolicyTests(unittest.TestCase):
    def test_expected_wall_clock_policy_can_prefer_estimate_or_profile(self):
        recommendation = {
            "status": "supported", "confidence": "low",
            "active_oom_count": 0,
        }
        estimate = choose_profile_action(
            recommendation, profiling_cost_seconds=120,
            recovery_cost_seconds=100,
        )
        self.assertEqual(estimate["decision"], "use_estimate")
        profile = choose_profile_action(
            recommendation, profiling_cost_seconds=10,
            recovery_cost_seconds=100,
            failure_probability=0.5,
        )
        self.assertEqual(profile["decision"], "profile")
        unsupported = choose_profile_action(
            {"status": "unsupported", "confidence": "insufficient"},
            profiling_cost_seconds=1000, recovery_cost_seconds=1,
        )
        self.assertEqual(unsupported["decision"], "profile")

    def test_singleflight_coalesces_concurrent_exact_key_work(self):
        singleflight = ResidencySingleflight()
        key = residency_key()["exact_key"]
        entered = threading.Event()
        release = threading.Event()
        calls = []
        results = []

        def operation():
            calls.append("called")
            entered.set()
            release.wait(timeout=2)
            return {"profile": 4}

        threads = [
            threading.Thread(
                target=lambda: results.append(singleflight.run(key, operation))
            )
            for _ in range(2)
        ]
        threads[0].start()
        self.assertTrue(entered.wait(timeout=2))
        threads[1].start()
        time.sleep(0.05)
        release.set()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(calls, ["called"])
        self.assertEqual(results, [{"profile": 4}, {"profile": 4}])

    def test_singleflight_same_thread_reentry_fails_fast_and_recovers(self):
        singleflight = ResidencySingleflight()
        key = residency_key()["exact_key"]
        with self.assertRaises(ModelResidencyError):
            singleflight.run(
                key,
                lambda: singleflight.run(key, lambda: {"profile": 5}),
            )
        self.assertEqual(
            singleflight.run(key, lambda: {"profile": 4}),
            {"profile": 4},
        )


if __name__ == "__main__":
    unittest.main()
