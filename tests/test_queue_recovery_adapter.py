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
