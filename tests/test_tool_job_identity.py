"""Unreserved contracts for tools/upscale job IDs and queue recovery."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.job_lifecycle import DurableTransition  # noqa: E402
from services.queue_recovery import QueueRecoveryJournal  # noqa: E402
from services.queue_recovery_adapter import (  # noqa: E402
    QueueRecoveryAdapterError,
    QueueRecoveryCoordinator,
    owner_principal_digest,
    project_instance_digest,
)
from services.tool_job_identity import (  # noqa: E402
    JOB_ID_HEX_LENGTH,
    is_unique_generation_job_id,
    new_unique_job_id,
    tool_job_requires_recovery_registration,
)


SECRET = b"tool-job-identity-test-secret"
OWNER = owner_principal_digest(SECRET, "owner-session")
PROJECT = project_instance_digest(SECRET, "a" * 32)
LAUNCH = ROOT / "app" / "launch.py"


def _tool_job(job_id: str) -> dict:
    return {
        "id": job_id,
        "status": "queued",
        "kind": "tool_upscale",
        "workspace": "h3-draft-tests",
        "progress": 0,
        "step": 0,
        "total_steps": 0,
        "message": "Queued (upscale)",
        "created_at": 1.0,
        "output_files": [],
    }


class ToolJobIdentityTests(unittest.TestCase):
    def test_new_unique_job_id_is_32_hex_and_avoids_collisions(self):
        taken = {new_unique_job_id() for _ in range(8)}
        self.assertTrue(all(is_unique_generation_job_id(item) for item in taken))
        extra = new_unique_job_id(taken)
        self.assertTrue(is_unique_generation_job_id(extra))
        self.assertNotIn(extra, taken)
        self.assertEqual(len(extra), JOB_ID_HEX_LENGTH)

    def test_eight_hex_ids_are_not_generation_style(self):
        self.assertFalse(is_unique_generation_job_id("deadbeef"))
        self.assertFalse(is_unique_generation_job_id("g" * 32))

    def test_tool_kinds_require_recovery_registration(self):
        self.assertTrue(tool_job_requires_recovery_registration(
            {"kind": "tool_upscale"},
        ))
        self.assertTrue(tool_job_requires_recovery_registration(
            {"kind": "tool_revoice"},
        ))
        self.assertFalse(tool_job_requires_recovery_registration(
            {"kind": "studio_generation"},
        ))

    def test_transition_without_register_raises_exact_recovery_error(self):
        job = _tool_job(new_unique_job_id())
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            coordinator = QueueRecoveryCoordinator(journal)
            with self.assertRaisesRegex(
                QueueRecoveryAdapterError,
                "Queue recovery job must be registered before transition",
            ):
                coordinator.prospective_transition(DurableTransition(
                    name="start",
                    jobs=({**job, "status": "running"},),
                ))

    def test_register_then_transition_accepts_32_hex_tool_job(self):
        job = _tool_job(new_unique_job_id())
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            coordinator = QueueRecoveryCoordinator(journal)
            coordinator.register_job(
                job,
                owner_digest=OWNER,
                project_digest=PROJECT,
                request_manifest={"kind": "tool_upscale"},
            )
            coordinator.prospective_transition(DurableTransition(
                name="start",
                jobs=({**job, "status": "running"},),
            ))
            restored = coordinator.restore().jobs
            self.assertEqual(restored[job["id"]]["status"], "running")
            self.assertEqual(restored[job["id"]]["kind"], "tool_upscale")

    def test_live_tools_upscale_registers_32_hex_before_publish(self):
        tree = ast.parse(LAUNCH.read_text(encoding="utf-8"), filename=str(LAUNCH))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "tools_upscale"
        )
        source = ast.get_source_segment(
            LAUNCH.read_text(encoding="utf-8"), function,
        )
        self.assertIsNotNone(source)
        self.assertNotIn("uuid.uuid4().hex[:8]", source)
        self.assertNotIn('_jobs[job_id] =', source)
        self.assertIn("_new_generation_job_id", source)
        self.assertIn("_queue_recovery_register_and_publish", source)
        self.assertIn("session_id", source)
