"""Owner retry through the launcher and real lifecycle/journal, without models."""

from __future__ import annotations

import ast
import copy
import hmac
import tempfile
import threading
import types
import unittest
from pathlib import Path

from services import job_lifecycle as lifecycle
from services.queue_recovery import QueueRecoveryJournal
from services.queue_recovery_adapter import (
    QueueRecoveryCoordinator,
    owner_principal_digest,
    project_instance_digest,
)
from services.queue_recovery_runtime import next_recovery_attempt
from test_queue_launch_recovery import _isolated_functions

ROOT = Path(__file__).resolve().parents[1]


class Denied(Exception):
    def __init__(self, *, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


class FailedQueueRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = ast.parse((ROOT / "app/launch.py").read_text())

    def setUp(self):
        lifecycle._reset_queue_state_for_tests()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(lifecycle._reset_queue_state_for_tests)
        self.journal = QueueRecoveryJournal(Path(self.temporary.name) / "queue.jsonl")
        self.coordinator = QueueRecoveryCoordinator(self.journal)
        self.secret = b"synthetic-failed-retry-secret"
        self.owner = "synthetic-owner"
        self.manifest = {"path": "job.request.json", "sha256": "a" * 64}
        self.job = {
            "id": "failed-native-job", "kind": "studio_generation",
            "workspace": "synthetic-project", "status": "failed",
            "recovery_state": "terminal", "recovery_attempt": 0,
            "execution_attempt": 1, "requested_outputs": 1,
            "resource_intent": "generation", "resource_execution": "standard",
            "resource_state": "released", "queue_held": False,
            "params": {"video_length": 141, "repeat_generation": 1},
            "failure_details": {"code": "segment_checkpoint_failed"},
            "error": "Previous failure", "finished_at": 9.0, "started_at": 1.0,
            "progress": 1.0, "step": 28,
            "_recovery_owner_digest": owner_principal_digest(self.secret, self.owner),
            "_recovery_reason_code": "generation_failed",
        }
        self.coordinator.register_job(
            self.job, owner_digest=self.job["_recovery_owner_digest"],
            project_digest=project_instance_digest(self.secret, "b" * 32),
            request_manifest=self.manifest,
        )
        lifecycle.configure_durability_hook(self.coordinator.prospective_transition)
        self.started = []
        self.gates = {}

        test = self

        class WorkerThread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self):
                # A real durable queued candidate must precede worker startup.
                snapshot = test.coordinator.restore().jobs[test.job["id"]]
                test.assertEqual(snapshot["status"], "queued")
                test.assertEqual(snapshot["recovery_attempt"], 1)
                test.started.append(self.kwargs["args"])

        def access(*_args, **_kwargs):
            if not self.gates.get("access", True):
                raise Denied(status_code=403, detail="Permission denied")

        def admission(_job):
            if not self.gates.get("admission", True):
                raise Denied(status_code=409, detail="Model unavailable")

        def owned(*_args):
            if not self.gates.get("owned", True):
                raise Denied(status_code=404, detail="Job not found")
            return self.job

        self.namespace = _isolated_functions(self.launch, ("_resume_recovered_job",), {
            "HTTPException": Denied, "_queue_recovery_checkpoint_lock": threading.RLock(),
            "_require_owned_job": owned, "_require_project_access": access,
            "owner_principal_digest": owner_principal_digest,
            "_session_secret": lambda: self.secret, "hmac": hmac,
            "_queue_recovery_reason_code": lambda job: job.get("_recovery_reason_code", ""),
            "_queue_recovery_revalidate_job": lambda _job: self.gates.get("inputs", True),
            "_queue_recovery_delivery_pending": lambda _job: None,
            "_require_job_runtime_model_admission": admission,
            "_queue_recovery_worker": lambda _job: object(),
            "next_recovery_attempt": next_recovery_attempt, "MAX_RECOVERY_ATTEMPTS": 3,
            "_queue_recovery_checkpoint": lifecycle.checkpoint_recovery_job,
            "retry_failed_recovery_job": lifecycle.retry_failed_recovery_job,
            "update_queue_job": lifecycle.update_queue_job,
            "threading": types.SimpleNamespace(Thread=WorkerThread),
            "_QUEUE_RECOVERY_REASON_TEXT": {},
        })
        self.request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_session_id=self.owner)
        )

    def retry(self):
        return self.namespace["_resume_recovered_job"](
            self.job["id"], self.request, requested_action="retry"
        )

    def test_owner_retry_queues_real_failed_journal_before_worker(self):
        original_params = copy.deepcopy(self.job["params"])
        self.assertFalse(lifecycle.checkpoint_recovery_job(self.job, status="queued"))
        result = self.retry()
        self.assertEqual(result["recovery_attempt"], 1)
        self.assertEqual(self.started, [(self.job["id"],)])
        self.assertEqual(self.job["params"], original_params)
        self.assertEqual(self.job["execution_attempt"], 2)
        self.assertEqual(self.job["resource_state"], "queued")
        self.assertEqual(self.job["step"], 0)
        self.assertNotIn("finished_at", self.job)
        self.assertNotIn("started_at", self.job)
        self.assertFalse(self.job.get("error"))
        restored = self.coordinator.compact().jobs[self.job["id"]]
        self.assertEqual(restored["request_manifest"], self.manifest)
        self.assertEqual(restored["execution_attempt"], 2)
        with self.assertRaises(Denied):
            self.retry()
        self.assertEqual(len(self.started), 1)
        self.assertTrue(lifecycle.try_start(self.job, expected_execution_attempt=2))
        self.assertFalse(lifecycle.finish_job(
            self.job, "completed", expected_execution_attempt=1
        ))

    def test_owner_project_inputs_and_model_gates_precede_retry(self):
        for gate in ("owned", "access", "inputs", "admission"):
            with self.subTest(gate=gate):
                self.gates = {gate: False}
                with self.assertRaises(Denied):
                    self.retry()
                self.assertEqual(self.job["status"], "failed")
                self.assertEqual(self.job["execution_attempt"], 1)
                self.assertEqual(self.started, [])

    def test_failed_legal_resume_consumes_one_retry_after_validation(self):
        self.job["_recovery_reason_code"] = "h3_legal_access_required"
        self.namespace["_queue_recovery_is_blocked"] = lambda _job: True
        self.namespace["_require_h3_legal_access"] = lambda _job, _request: None
        result = self.retry()
        self.assertEqual(result["recovery_attempt"], 1)
        self.assertEqual(self.job["execution_attempt"], 2)
        self.assertEqual(self.started, [(self.job["id"],)])

    def test_persistence_failure_keeps_failed_job_and_starts_no_worker(self):
        lifecycle.configure_durability_hook(None)

        def unavailable(_event):
            raise OSError("Synthetic persistence failure")

        lifecycle.configure_durability_hook(unavailable)
        before = copy.deepcopy(self.job)
        with self.assertRaises(OSError):
            self.retry()
        self.assertEqual(self.job, before)
        self.assertEqual(self.started, [])

    def test_transition_rejects_other_states_and_stale_attempts(self):
        transition = lifecycle.retry_failed_recovery_job
        for updates in (
            {"status": "completed"}, {"status": "cancelled"},
            {"status": "running"}, {"status": "queued"},
            {"execution_attempt": 2}, {"execution_attempt": True},
            {"execution_attempt": lifecycle.MAX_EXECUTION_ATTEMPT},
            {"recovery_attempt": 1}, {"recovery_attempt": True},
            {"kind": "sample_campaign_generation"},
            {"_terminal_transition_active": True},
        ):
            with self.subTest(updates=updates):
                job = {**self.job, **updates}
                before = copy.deepcopy(job)
                self.assertFalse(transition(
                    job, expected_execution_attempt=1,
                    expected_recovery_attempt=0,
                    recovery_attempt=1, recovery_state="retrying",
                ))
                self.assertEqual(job, before)
        overflow = {**self.job, "execution_attempt": lifecycle.MAX_EXECUTION_ATTEMPT}
        self.assertFalse(transition(
            overflow, expected_execution_attempt=lifecycle.MAX_EXECUTION_ATTEMPT,
            expected_recovery_attempt=0, recovery_attempt=1, recovery_state="retrying",
        ))
        for status in ("failed", "completed", "cancelled"):
            job = {**self.job, "status": status}
            self.assertFalse(lifecycle.checkpoint_recovery_job(job, status="queued"))

    def test_later_cancel_or_completion_wins_reentrant_durability(self):
        for winner in ("cancelled", "completed"):
            with self.subTest(winner=winner):
                job = copy.deepcopy(self.job)
                fired = False
                lifecycle.configure_durability_hook(None)

                def persist(event, job=job, winner=winner):
                    nonlocal fired
                    self.coordinator.prospective_transition(event)
                    if event.name != "failed_recovery_retry" or fired:
                        return
                    fired = True
                    self.assertTrue(lifecycle.retry_failed_recovery_job(
                        job, expected_execution_attempt=1,
                        expected_recovery_attempt=0,
                        recovery_attempt=1, recovery_state="retrying",
                    ))
                    if winner == "cancelled":
                        lifecycle.request_cancel(job)
                    else:
                        self.assertTrue(lifecycle.try_start(job))
                        self.assertTrue(lifecycle.finish_job(
                            job, "completed", expected_execution_attempt=2
                        ))

                lifecycle.configure_durability_hook(persist)
                self.assertFalse(lifecycle.retry_failed_recovery_job(
                    job, expected_execution_attempt=1, expected_recovery_attempt=0,
                    recovery_attempt=1, recovery_state="retrying",
                ))
                self.assertTrue(fired)
                self.assertEqual(job["status"], winner)
                restored = self.coordinator.restore().jobs[job["id"]]
                self.assertEqual(restored["status"], winner)


if __name__ == "__main__":
    unittest.main()
