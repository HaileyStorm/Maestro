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
