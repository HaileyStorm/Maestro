"""Focused, model-free checks for a held H3 final sidecar reseal."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest import mock

from scripts.recover_h3_final_sidecar import (
    FinalSidecarResealError,
    ResealInspection,
    plan_h3_final_sidecar_reseal,
    prepare_h3_final_sidecar_recovery,
)
from services.queue_recovery_runtime import QueueRecoveryRuntimeError


class FinalSidecarResealTests(unittest.TestCase):
    def setUp(self):
        segment_id = "unit:v1:" + "a" * 64
        final_id = "unit:v1:" + "b" * 64
        settings = {"component_hashes": ["c" * 64], "clip_start_frames": [0]}
        segment = {
            "kind": "h3_segment", "state": "completed", "unit_id": segment_id,
            "variant": 0, "index": 0,
            "artifacts": [{"basename": "segment.mp4", "sha256": "c" * 64}],
        }
        final = {
            "kind": "h3_concat", "state": "completed", "unit_id": final_id,
            "variant": 0, "index": 0, "dependencies": [segment_id],
            "settings": settings,
            "artifacts": [{
                "basename": "final.mp4", "size": 123, "sha256": "d" * 64,
                "sidecar_basename": "final.meta.json", "sidecar_size": 71,
                "sidecar_sha256": "e" * 64, "producer_unit_id": final_id,
            }],
        }
        self.snapshot = {
            "id": "job1", "status": "queued", "queue_held": True,
            "_recovery_reason_code": "final_output_recovery_incomplete",
            "reruns_denoise": False, "private": True, "explicit": False,
            "recovery_unit": final,
            "recovery_cursor": {"completed_units": [segment]},
        }
        self.candidate = {
            "job_id": "job1", "kind": "h3_concat", "unit_id": final_id,
            "unit_variant": 0, "unit_index": 0,
            "dependencies": (segment_id,), "settings": settings,
            "dest_media": "final.mp4", "dest_sidecar": "final.meta.json",
            "media_size": 123, "media_sha256": "d" * 64,
            "sidecar_size": 74, "sidecar_sha256": "f" * 64,
        }

    def plan(self, snapshot=None, candidate=None):
        return plan_h3_final_sidecar_reseal(
            snapshot or self.snapshot,
            candidate=candidate or self.candidate,
            private=True, explicit=False,
        )

    def test_keeps_old_descriptor_and_hold_while_resealing_only_sidecar(self):
        original = deepcopy(self.snapshot)
        updated = self.plan()
        self.assertEqual(self.snapshot, original)
        self.assertEqual(updated["status"], "queued")
        self.assertIs(updated["queue_held"], True)
        self.assertIs(updated["reruns_denoise"], False)
        old = updated["recovery_cursor"]["final_sidecar_reseal_history"][0]
        self.assertEqual(old["prior_unit"], original["recovery_unit"])
        self.assertEqual(
            updated["recovery_unit"]["artifacts"][0]["sha256"], "d" * 64,
        )
        self.assertEqual(
            updated["recovery_unit"]["artifacts"][0]["sidecar_sha256"],
            "f" * 64,
        )
        self.assertEqual(
            updated["recovery_cursor"]["completed_units"][1],
            updated["recovery_unit"],
        )

    def test_changed_media_or_producer_graph_is_rejected(self):
        for key, value in (
            ("media_sha256", "0" * 64),
            ("media_size", 124),
            ("dependencies", ("unit:v1:" + "0" * 64,)),
            ("settings", {}),
            ("unit_id", "unit:v1:" + "0" * 64),
        ):
            with self.subTest(key=key):
                candidate = dict(self.candidate, **{key: value})
                with self.assertRaises(FinalSidecarResealError):
                    self.plan(candidate=candidate)

    def test_unheld_or_incomplete_segment_is_rejected(self):
        for path, value in (
            (("status",), "running"),
            (("_recovery_reason_code",), "h3_legal_access_required"),
            (("reruns_denoise",), True),
            (("recovery_cursor", "completed_units"), []),
        ):
            with self.subTest(path=path):
                snapshot = deepcopy(self.snapshot)
                if len(path) == 1:
                    snapshot[path[0]] = value
                else:
                    snapshot[path[0]][path[1]] = value
                with self.assertRaises(FinalSidecarResealError):
                    self.plan(snapshot=snapshot)

    def test_matching_sidecar_requires_no_reseal(self):
        candidate = dict(self.candidate, sidecar_sha256="e" * 64)
        with self.assertRaises(FinalSidecarResealError):
            self.plan(candidate=candidate)

    def test_malformed_existing_audit_history_is_not_overwritten(self):
        for history in ({}, 0, None):
            with self.subTest(history=history):
                snapshot = deepcopy(self.snapshot)
                snapshot["recovery_cursor"]["final_sidecar_reseal_history"] = history
                with self.assertRaises(FinalSidecarResealError):
                    self.plan(snapshot=snapshot)

    def test_journal_cas_precedes_dependency_staging(self):
        events = []

        class Journal:
            def commit_state(self, **_kwargs):
                events.append("cas")

        proposed = self.plan()
        inspection = ResealInspection(
            app_directory=Path("app"), journal=Journal(), sequence=7,
            epoch=1, revision=3, job_id="job1", workspace="project",
            project=Path("project"), snapshot=self.snapshot,
            candidate=self.candidate, proposed=proposed, needs_commit=True,
        )

        def stage(*_args, **_kwargs):
            events.append("stage")
            return {
                "final_sidecar_sha256": "f" * 64,
                "final_sidecar_seal_match": True,
            }

        with mock.patch(
            "scripts.recover_h3_final_sidecar.inspect_h3_final_sidecar_reseal",
            return_value=inspection,
        ), mock.patch(
            "services.queue_recovery_final_adoption.stage_quarantined_h3_segment_for_final_adoption",
            create=True, side_effect=stage,
        ), mock.patch(
            "services.queue_recovery_final_adoption._discover",
            return_value=([self.candidate], 0),
        ), mock.patch(
            "services.queue_recovery_final_adoption._complete_groups",
            return_value=([{"job_id": "job1"}], []),
        ):
            result = prepare_h3_final_sidecar_recovery(
                inspection, expected_sequence=7,
            )
        self.assertEqual(events, ["cas", "stage"])
        self.assertEqual(result["state"], "prepared_for_startup_adoption")
        self.assertTrue(result["job_held"])

        events.clear()
        with self.assertRaises(FinalSidecarResealError):
            prepare_h3_final_sidecar_recovery(
                inspection, expected_sequence=8,
            )
        self.assertEqual(events, [])

    def test_staging_failure_after_cas_retries_without_another_reseal(self):
        events = []

        class Journal:
            def commit_state(self, **_kwargs):
                events.append("cas")

        journal = Journal()
        proposed = self.plan()
        first = ResealInspection(
            app_directory=Path("app"), journal=journal, sequence=7,
            epoch=1, revision=3, job_id="job1", workspace="project",
            project=Path("project"), snapshot=self.snapshot,
            candidate=self.candidate, proposed=proposed, needs_commit=True,
        )
        resumed = ResealInspection(
            app_directory=Path("app"), journal=journal, sequence=8,
            epoch=1, revision=4, job_id="job1", workspace="project",
            project=Path("project"), snapshot=proposed,
            candidate=self.candidate, proposed=proposed, needs_commit=False,
        )

        def stage(*_args, **_kwargs):
            events.append("stage")
            if events.count("stage") == 1:
                raise QueueRecoveryRuntimeError("interrupted copy")
            return {
                "final_sidecar_sha256": "f" * 64,
                "final_sidecar_seal_match": True,
            }

        with mock.patch(
            "scripts.recover_h3_final_sidecar.inspect_h3_final_sidecar_reseal",
            side_effect=[first, resumed],
        ), mock.patch(
            "services.queue_recovery_final_adoption.stage_quarantined_h3_segment_for_final_adoption",
            side_effect=stage,
        ), mock.patch(
            "services.queue_recovery_final_adoption._discover",
            return_value=([self.candidate], 0),
        ), mock.patch(
            "services.queue_recovery_final_adoption._complete_groups",
            return_value=([{"job_id": "job1"}], []),
        ):
            with self.assertRaises(QueueRecoveryRuntimeError):
                prepare_h3_final_sidecar_recovery(first, expected_sequence=7)
            result = prepare_h3_final_sidecar_recovery(
                resumed, expected_sequence=8,
            )
        self.assertEqual(events, ["cas", "stage", "stage"])
        self.assertEqual(result["journal_sequence"], 8)
        self.assertEqual(len(proposed["recovery_cursor"]["final_sidecar_reseal_history"]), 1)


if __name__ == "__main__":
    unittest.main()
