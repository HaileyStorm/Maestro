from __future__ import annotations

import os
import sys
import unittest


APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from services.sample_campaign_release import (  # noqa: E402
    SampleCampaignReleaseCoordinator,
    SampleCampaignReleaseError,
)


def _pair(*, maestro_status="queued", control_status="queued"):
    digest = "a" * 64

    def job(job_id, arm, peer, status):
        return {
            "id": job_id,
            "kind": "sample_campaign_generation",
            "status": status,
            "queue_class": "background_sample",
            "queue_priority": -1000,
            "queue_held": status == "queued",
            "_recovery_owner_digest": "owner",
            "_recovery_project_digest": "project",
            "workspace": "default",
            "recovery_cursor": {"sample_campaign": {
                "schema": 1,
                "pair_id": "pair-1",
                "pair_manifest_digest": digest,
                "arm": arm,
                "peer_job_id": peer,
            }},
        }

    return [
        job("job-maestro", "maestro", "job-control", maestro_status),
        job("job-control", "control", "job-maestro", control_status),
    ]


class SampleCampaignReleaseCoordinatorTests(unittest.TestCase):
    def coordinator(self, **overrides):
        self.persisted = []
        self.started = []

        def persist(job, held, state):
            self.persisted.append((job["id"], held, state))
            job["queue_held"] = held
            job["recovery_state"] = state
            return True

        defaults = {
            "ordinary_work_present": lambda: False,
            "another_sample_active": lambda _pair_id: False,
            "validate_pair_requests": lambda _maestro, _control: True,
            "readiness": lambda _job: (True, "ready"),
            "persist_hold": persist,
            "force_hold": lambda job: job.update(queue_held=True),
            "start_worker": lambda job_id: self.started.append(job_id),
        }
        defaults.update(overrides)
        return SampleCampaignReleaseCoordinator(**defaults)

    def test_held_pair_releases_only_maestro_arm(self):
        jobs = _pair()
        result = self.coordinator().release_one("pair-1", jobs)
        self.assertEqual(result.status, "released")
        self.assertEqual(result.arm, "maestro")
        self.assertEqual(self.started, ["job-maestro"])
        self.assertFalse(jobs[0]["queue_held"])
        self.assertTrue(jobs[1]["queue_held"])

    def test_completed_maestro_releases_control_without_rerunning_output(self):
        jobs = _pair(maestro_status="completed")
        result = self.coordinator().release_one("pair-1", jobs)
        self.assertEqual(result.arm, "control")
        self.assertEqual(self.started, ["job-control"])
        self.assertEqual(jobs[0]["status"], "completed")

    def test_user_work_and_active_sample_preserve_both_holds(self):
        for override, reason in (
            ({"ordinary_work_present": lambda: True}, "ordinary_work_waiting"),
            ({"another_sample_active": lambda _pair: True}, "sample_arm_active"),
        ):
            with self.subTest(reason=reason):
                jobs = _pair()
                result = self.coordinator(**override).release_one("pair-1", jobs)
                self.assertEqual((result.status, result.reason), ("held", reason))
                self.assertTrue(all(job["queue_held"] for job in jobs))
                self.assertEqual(self.started, [])

    def test_unknown_readiness_and_private_evidence_fail_closed(self):
        for override, reason in (
            ({"readiness": lambda _job: (False, "telemetry_unknown")}, "telemetry_unknown"),
            ({"readiness": lambda _job: (_ for _ in ()).throw(RuntimeError())}, "readiness_unknown"),
            ({"validate_pair_requests": lambda *_jobs: False}, "private_evidence_invalid"),
        ):
            with self.subTest(reason=reason):
                jobs = _pair()
                result = self.coordinator(**override).release_one("pair-1", jobs)
                self.assertEqual((result.status, result.reason), ("held", reason))
                self.assertTrue(jobs[0]["queue_held"])
                self.assertEqual(self.started, [])

    def test_persistence_failure_never_starts_worker(self):
        jobs = _pair()
        result = self.coordinator(
            persist_hold=lambda *_args: False,
        ).release_one("pair-1", jobs)
        self.assertEqual(result.reason, "release_persistence_failed")
        self.assertTrue(jobs[0]["queue_held"])
        self.assertEqual(self.started, [])

    def test_worker_start_failure_reasserts_hold(self):
        jobs = _pair()
        result = self.coordinator(
            start_worker=lambda _job_id: (_ for _ in ()).throw(RuntimeError()),
        ).release_one("pair-1", jobs)
        self.assertEqual(result.reason, "worker_start_failed")
        self.assertTrue(jobs[0]["queue_held"])
        self.assertEqual(
            self.persisted,
            [
                ("job-maestro", False, "sample_campaign_released"),
                ("job-maestro", True, "sample_campaign_held"),
            ],
        )

    def test_worker_and_rehold_persistence_failures_still_force_memory_hold(self):
        jobs = _pair()
        calls = 0

        def persist(job, held, state):
            nonlocal calls
            calls += 1
            if calls == 1:
                job.update(queue_held=held, recovery_state=state)
                return True
            raise RuntimeError("persistence unavailable")

        result = self.coordinator(
            persist_hold=persist,
            start_worker=lambda _job_id: (_ for _ in ()).throw(RuntimeError()),
        ).release_one("pair-1", jobs)
        self.assertEqual(result.reason, "worker_start_failed")
        self.assertTrue(jobs[0]["queue_held"])

    def test_nonreciprocal_or_partially_released_pair_is_rejected(self):
        jobs = _pair()
        jobs[1]["recovery_cursor"]["sample_campaign"]["peer_job_id"] = "wrong"
        with self.assertRaises(SampleCampaignReleaseError):
            self.coordinator().release_one("pair-1", jobs)

    def test_pair_owner_project_workspace_priority_and_schema_must_match(self):
        changes = (
            ("_recovery_owner_digest", "other-owner"),
            ("_recovery_project_digest", "other-project"),
            ("workspace", "other-workspace"),
            ("queue_priority", -999),
        )
        for field, value in changes:
            with self.subTest(field=field):
                jobs = _pair()
                for job in jobs:
                    job.update({
                        "_recovery_owner_digest": "owner",
                        "_recovery_project_digest": "project",
                        "workspace": "default",
                    })
                jobs[1][field] = value
                with self.assertRaises(SampleCampaignReleaseError):
                    self.coordinator().release_one("pair-1", jobs)
        jobs = _pair()
        jobs[1]["recovery_cursor"]["sample_campaign"]["schema"] = 2
        with self.assertRaises(SampleCampaignReleaseError):
            self.coordinator().release_one("pair-1", jobs)

    def test_completed_peer_private_evidence_is_revalidated_before_control(self):
        jobs = _pair(maestro_status="completed")
        observed = []

        def validate(maestro, control):
            observed.extend((maestro["id"], control["id"]))
            return False

        result = self.coordinator(
            validate_pair_requests=validate,
        ).release_one("pair-1", jobs)
        self.assertEqual(result.reason, "private_evidence_invalid")
        self.assertEqual(observed, ["job-maestro", "job-control"])
        self.assertEqual(self.started, [])
        jobs = _pair()
        jobs[0]["queue_held"] = False
        with self.assertRaises(SampleCampaignReleaseError):
            self.coordinator().release_one("pair-1", jobs)

    def test_completed_pair_is_idempotent(self):
        jobs = _pair(maestro_status="completed", control_status="completed")
        result = self.coordinator().release_one("pair-1", jobs)
        self.assertEqual(result.status, "complete")
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
