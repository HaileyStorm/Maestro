"""Focused durable marker coverage for logical Reference jobs."""

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.queue_recovery import QueueRecoveryJournal  # noqa: E402
from services.queue_recovery_adapter import (  # noqa: E402
    QueueRecoveryAdapterError,
    QueueRecoveryCoordinator,
    _durable_order_key,
    owner_principal_digest,
    project_instance_digest,
    serialize_job,
)


SECRET = b"logical-reference-recovery-test-secret"
OWNER = owner_principal_digest(SECRET, "owner-session")
PROJECT = project_instance_digest(SECRET, "a" * 32)


def _serialize(job):
    return serialize_job(
        job,
        owner_digest=OWNER,
        project_digest=PROJECT,
        request_manifest={"kind": "reference-test"},
    )


class LogicalReferenceRecoveryTests(unittest.TestCase):
    def test_atomic_registration_restores_both_held_sample_arms_or_neither(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            coordinator = QueueRecoveryCoordinator(journal)
            jobs = (
                {
                    "id": "sample-maestro",
                    "status": "queued",
                    "queue_class": "background_sample",
                    "queue_priority": -1000,
                    "queue_held": True,
                    "created_at": 1,
                },
                {
                    "id": "sample-control",
                    "status": "queued",
                    "queue_class": "background_sample",
                    "queue_priority": -1000,
                    "queue_held": True,
                    "created_at": 2,
                },
            )
            coordinator.register_jobs_atomic(tuple(
                (job, OWNER, PROJECT, {"kind": "sample-arm"})
                for job in jobs
            ))
            restored = QueueRecoveryCoordinator(journal).restore().jobs

            self.assertEqual(set(restored), {"sample-maestro", "sample-control"})
            for snapshot in restored.values():
                self.assertEqual(snapshot["queue_class"], "background_sample")
                self.assertEqual(snapshot["queue_priority"], -1000)
                self.assertTrue(snapshot["queue_held"])

        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            coordinator = QueueRecoveryCoordinator(journal)
            with self.assertRaisesRegex(QueueRecoveryAdapterError, "queue_class"):
                coordinator.register_jobs_atomic((
                    (
                        jobs[0], OWNER, PROJECT, {"kind": "sample-arm"},
                    ),
                    (
                        {**jobs[1], "queue_class": "invalid"},
                        OWNER,
                        PROJECT,
                        {"kind": "sample-arm"},
                    ),
                ))
            self.assertEqual(
                QueueRecoveryCoordinator(journal).restore().jobs,
                {},
            )

            with self.assertRaisesRegex(QueueRecoveryAdapterError, "job is invalid"):
                coordinator.register_jobs_atomic((
                    (
                        jobs[0], OWNER, PROJECT, {"kind": "sample-arm"},
                    ),
                    (
                        "not-a-job",  # type: ignore[arg-type]
                        OWNER,
                        PROJECT,
                        {"kind": "sample-arm"},
                    ),
                ))
            self.assertEqual(coordinator.restore().jobs, {})

    def test_atomic_registration_rejects_duplicate_or_existing_job_without_partial_commit(self):
        job = {
            "id": "sample-arm",
            "status": "queued",
            "queue_class": "background_sample",
            "queue_priority": -1000,
            "queue_held": True,
        }
        registration = (job, OWNER, PROJECT, {"kind": "sample-arm"})
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            coordinator = QueueRecoveryCoordinator(journal)
            with self.assertRaisesRegex(QueueRecoveryAdapterError, "duplicate"):
                coordinator.register_jobs_atomic((registration, registration))
            self.assertEqual(coordinator.restore().jobs, {})

            coordinator.register_job(
                job,
                owner_digest=OWNER,
                project_digest=PROJECT,
                request_manifest={"kind": "sample-arm"},
            )
            other = (
                {**job, "id": "other-arm"},
                OWNER,
                PROJECT,
                {"kind": "sample-arm"},
            )
            with self.assertRaisesRegex(QueueRecoveryAdapterError, "already registered"):
                coordinator.register_jobs_atomic((registration, other))
            self.assertEqual(set(coordinator.restore().jobs), {"sample-arm"})

    def test_background_sample_queue_class_is_strict_and_orders_after_users(self):
        background = _serialize({
            "id": "background-local",
            "status": "queued",
            "queue_class": "background_sample",
            "source_remote": False,
            "queue_priority": 1_000_000,
            "_queue_manual_order": 99,
            "created_at": 0,
        })
        remote_user = _serialize({
            "id": "remote-user",
            "status": "queued",
            "queue_class": "user",
            "source_remote": True,
            "queue_priority": -1_000_000,
            "created_at": 1,
        })
        legacy_local_user = _serialize({
            "id": "legacy-local-user",
            "status": "queued",
            "source_remote": False,
            "queue_priority": -1_000_000,
            "created_at": 2,
        })
        ordered = sorted(
            (background, remote_user, legacy_local_user),
            key=lambda job: _durable_order_key(job, 0),
        )
        self.assertEqual(
            [job["id"] for job in ordered],
            ["legacy-local-user", "remote-user", "background-local"],
        )
        self.assertEqual(background["queue_class"], "background_sample")
        self.assertEqual(remote_user["queue_class"], "user")
        self.assertNotIn("queue_class", legacy_local_user)
        for invalid in ("background", "sample", "", None, 3):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                QueueRecoveryAdapterError,
                "queue_class",
            ):
                _serialize({
                    "id": "invalid-queue-class",
                    "status": "queued",
                    "queue_class": invalid,
                })

    def test_sample_retry_defaults_and_strict_allowlist(self):
        defaulted = _serialize({
            "id": "sample-default",
            "kind": "sample_campaign_generation",
            "status": "queued",
            "queue_class": "background_sample",
        })
        self.assertEqual(defaulted["sample_retry"], {
            "attempt": 0, "not_before": None,
        })
        scheduled = _serialize({
            **defaulted,
            "id": "sample-scheduled",
            "sample_retry": {"attempt": 2, "not_before": 1234.5},
        })
        self.assertEqual(scheduled["sample_retry"], {
            "attempt": 2, "not_before": 1234.5,
        })
        invalid = (
            ({"attempt": 1}, "sample_retry"),
            ({"attempt": 0, "not_before": 1.0}, "sample_retry"),
            ({"attempt": 1, "not_before": None}, "sample_retry"),
            ({"attempt": True, "not_before": 1.0}, "sample_retry"),
            ({"attempt": 1, "not_before": float("nan")}, "sample_retry"),
        )
        for retry, message in invalid:
            with self.subTest(retry=retry), self.assertRaisesRegex(
                QueueRecoveryAdapterError, message,
            ):
                _serialize({
                    "id": "sample-invalid",
                    "kind": "sample_campaign_generation",
                    "status": "queued",
                    "queue_class": "background_sample",
                    "sample_retry": retry,
                })
        with self.assertRaisesRegex(QueueRecoveryAdapterError, "reserved"):
            _serialize({
                "id": "user-invalid",
                "kind": "generation",
                "status": "queued",
                "queue_class": "user",
                "sample_retry": {"attempt": 1, "not_before": 10.0},
            })

    def test_preemption_requested_sample_restores_same_job_held(self):
        job = {
            "id": "sample-preempted",
            "kind": "sample_campaign_generation",
            "status": "running",
            "queue_class": "background_sample",
            "queue_priority": -1000,
            "queue_held": False,
            "resource_intent": "generation",
            "resource_execution": "standard",
            "preemption_mode": "none",
            "resource_state": "preemption_requested",
            "execution_attempt": 7,
            "sample_retry": {"attempt": 3, "not_before": 2000.0},
            "progress": 91,
            "step": 17,
            "overall_progress": 80,
            "window_progress": 70,
            "clip_progress": 60,
            "output_files": ["committed.mp4"],
            "artifact_files": ["committed.mp4"],
            "recovery_cursor": {
                "sample_campaign": {
                    "peer_job_id": "sample-peer",
                    "arm": "control",
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            QueueRecoveryCoordinator(journal).register_job(
                job,
                owner_digest=OWNER,
                project_digest=PROJECT,
                request_manifest={"kind": "sample-arm"},
            )
            recovered = QueueRecoveryCoordinator(journal).restore().jobs[
                job["id"]
            ]
        self.assertEqual(recovered["id"], job["id"])
        self.assertEqual(recovered["status"], "queued")
        self.assertTrue(recovered["queue_held"])
        self.assertEqual(recovered["resource_state"], "queued")
        self.assertEqual(recovered["execution_attempt"], 8)
        for field in (
            "progress", "step", "overall_progress",
            "window_progress", "clip_progress",
        ):
            self.assertEqual(recovered[field], 0)
        self.assertEqual(recovered["sample_retry"], job["sample_retry"])
        self.assertEqual(recovered["output_files"], ["committed.mp4"])
        self.assertEqual(
            recovered["recovery_cursor"]["sample_campaign"]["peer_job_id"],
            "sample-peer",
        )

    def test_sample_preemption_restart_state_is_strict(self):
        base = {
            "id": "sample-invalid-preemption",
            "kind": "sample_campaign_generation",
            "status": "running",
            "queue_class": "background_sample",
            "resource_state": "preemption_requested",
            "execution_attempt": 1,
        }
        with self.assertRaisesRegex(QueueRecoveryAdapterError, "preemption"):
            _serialize(base)
        with self.assertRaisesRegex(QueueRecoveryAdapterError, "preemption"):
            _serialize({
                **base,
                "status": "queued",
                "sample_retry": {"attempt": 1, "not_before": 50.0},
            })

    def test_resource_retry_state_is_complete_bounded_and_round_trips(self):
        retry = _serialize({
            "id": "resource-retry", "status": "queued",
            "resource_retry_attempt": 1,
            "resource_retry_limit": 2,
            "resource_retry_phase": "model_load",
            "resource_retry_reason": "host_memory_pressure",
        })
        self.assertEqual(retry["resource_retry_attempt"], 1)
        self.assertEqual(retry["resource_retry_limit"], 2)
        self.assertEqual(retry["resource_retry_phase"], "model_load")
        self.assertEqual(
            retry["resource_retry_reason"], "host_memory_pressure",
        )

        invalid = (
            {
                "resource_retry_attempt": 1,
                "resource_retry_limit": 2,
                "resource_retry_phase": "model_load",
            },
            {
                "resource_retry_attempt": 3,
                "resource_retry_limit": 2,
                "resource_retry_phase": "model_load",
                "resource_retry_reason": "host_memory_pressure",
            },
            {
                "resource_retry_attempt": 1,
                "resource_retry_limit": 9,
                "resource_retry_phase": "model_load",
                "resource_retry_reason": "host_memory_pressure",
            },
            {
                "resource_retry_attempt": 1,
                "resource_retry_limit": 2,
                "resource_retry_phase": "unknown",
                "resource_retry_reason": "host_memory_pressure",
            },
            {
                "resource_retry_attempt": 1,
                "resource_retry_limit": 2,
                "resource_retry_phase": "generation",
                "resource_retry_reason": "finalization_oom",
            },
        )
        for index, fields in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(
                QueueRecoveryAdapterError,
            ):
                _serialize({
                    "id": f"bad-resource-{index}",
                    "status": "queued",
                    **fields,
                })

    def test_gpu_resource_retry_reconstructs_only_safe_oom_info(self):
        private = "/private/models/secret.safetensors traceback"
        retry = _serialize({
            "id": "gpu-resource-retry",
            "status": "queued",
            "resource_retry_attempt": 1,
            "resource_retry_limit": 2,
            "resource_retry_phase": "generation",
            "resource_retry_reason": "generation_oom",
            "failure_details": {
                "code": "cuda_oom",
                "stage": "denoise",
                "detail": private,
                "exception_type": "OutOfMemoryError",
                "is_oom": True,
                "allocator": {
                    "device_type": "cuda",
                    "free_bytes": 10,
                    "private_path": private,
                },
            },
            "oom_info": {
                "is_oom": True,
                "stage": "denoise",
                "current_coefficient": 0.8,
                "suggested_coefficient": 0.1,
                "message": private,
                "allocator": {
                    "device_type": "cuda",
                    "free_bytes": 10,
                    "private_path": private,
                },
                "traceback": private,
            },
        })
        self.assertEqual(retry["oom_info"], {
            "is_oom": True,
            "stage": "denoise",
            "current_coefficient": 0.8,
            "suggested_coefficient": 0.7,
            "message": "The operation ran out of GPU memory.",
            "allocator": {"device_type": "cuda", "free_bytes": 10},
        })
        self.assertNotIn(private, repr(retry))

        invalid_jobs = (
            {
                "failure_details": {
                    "code": "cuda_oom", "stage": "denoise",
                    "exception_type": "RuntimeError", "is_oom": True,
                },
                "oom_info": "raw traceback",
            },
            {
                "failure_details": {
                    "code": "cuda_oom", "stage": "denoise",
                    "exception_type": "RuntimeError", "is_oom": True,
                },
                "oom_info": {
                    "is_oom": True, "current_coefficient": float("inf"),
                },
            },
            {
                "failure_details": {
                    "code": "generation_failed", "stage": "generation",
                    "exception_type": "RuntimeError", "is_oom": False,
                },
                "oom_info": {"is_oom": True, "current_coefficient": 0.8},
            },
        )
        for index, updates in enumerate(invalid_jobs):
            with self.subTest(index=index), self.assertRaises(
                QueueRecoveryAdapterError,
            ):
                _serialize({
                    "id": f"bad-gpu-retry-{index}",
                    "status": "queued",
                    "resource_retry_attempt": 1,
                    "resource_retry_limit": 2,
                    "resource_retry_phase": "generation",
                    "resource_retry_reason": "generation_oom",
                    **updates,
                })

        with self.assertRaises(QueueRecoveryAdapterError):
            _serialize({
                "id": "bad-host-retry-oom",
                "status": "queued",
                "resource_retry_attempt": 1,
                "resource_retry_limit": 2,
                "resource_retry_phase": "model_load",
                "resource_retry_reason": "host_memory_pressure",
                "failure_details": {
                    "code": "cuda_oom", "stage": "generation",
                    "exception_type": "RuntimeError", "is_oom": True,
                },
                "oom_info": {"is_oom": True, "current_coefficient": 0.8},
            })

    def test_marker_is_strict_and_requires_the_corresponding_relation(self):
        parent = _serialize({
            "id": "reference-parent", "status": "queued",
            "logical_job_kind": "reference_pack_parent",
        })
        child = _serialize({
            "id": "reference-child", "status": "queued",
            "logical_job_kind": "reference_pack_child",
            "parent_job_id": "reference-parent",
        })
        self.assertEqual(parent["logical_job_kind"], "reference_pack_parent")
        self.assertEqual(child["logical_job_kind"], "reference_pack_child")
        self.assertEqual(child["parent_job_id"], "reference-parent")

        invalid = (
            {"id": "bad-kind", "status": "queued", "logical_job_kind": "reference"},
            {
                "id": "parent-with-parent", "status": "queued",
                "logical_job_kind": "reference_pack_parent",
                "parent_job_id": "other",
            },
            {
                "id": "child-without-parent", "status": "queued",
                "logical_job_kind": "reference_pack_child",
            },
            {
                "id": "self-child", "status": "queued",
                "logical_job_kind": "reference_pack_child",
                "parent_job_id": "self-child",
            },
        )
        for job in invalid:
            with self.subTest(job=job["id"]), self.assertRaises(
                QueueRecoveryAdapterError,
            ):
                _serialize(job)

    def test_marker_survives_journal_restart_without_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = QueueRecoveryJournal(Path(temporary) / "queue.jsonl")
            coordinator = QueueRecoveryCoordinator(journal)
            jobs = (
                {
                    "id": "reference-parent", "status": "queued",
                    "logical_job_kind": "reference_pack_parent",
                },
                {
                    "id": "reference-child", "status": "queued",
                    "logical_job_kind": "reference_pack_child",
                    "parent_job_id": "reference-parent",
                },
                {
                    "id": "legacy-reference-looking", "status": "queued",
                    "message": "Reference child", "parent_job_id": "reference-parent",
                },
            )
            for job in jobs:
                coordinator.register_job(
                    job,
                    owner_digest=OWNER,
                    project_digest=PROJECT,
                    request_manifest={"kind": "reference-test"},
                )

            restored = QueueRecoveryCoordinator(journal).restore().jobs

        self.assertEqual(
            restored["reference-parent"]["logical_job_kind"],
            "reference_pack_parent",
        )
        self.assertEqual(
            restored["reference-child"]["logical_job_kind"],
            "reference_pack_child",
        )
        self.assertNotIn(
            "logical_job_kind", restored["legacy-reference-looking"],
        )


if __name__ == "__main__":
    unittest.main()
