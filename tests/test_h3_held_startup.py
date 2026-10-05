"""Model-free startup recovery for H3 work held before execution."""

from __future__ import annotations

import copy
import hmac
import json
import math
import tempfile
import time
import types
import unittest

import test_h3_legal_access as legal_fixture
from services.h3_offload_plan import H3_OFFLOAD_PLAN_PARAM_KEY
from services.queue_recovery import QueueRecoveryJournal
from services.queue_recovery_adapter import serialize_job
from services.queue_recovery_runtime import QueueRecoveryRuntimeError
from test_h3_legal_access import (
    ROOT,
    H3LegalAccessError,
    _load_launch_function,
    require_h3_execution_allowed,
)


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


class H3HeldStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "app/launch.py").read_text(encoding="utf-8")

    def setUp(self):
        self.params = {
            "model_type": "minimax_h3",
            "_h3_cumulative_append": True,
            "video_length": 141,
            "sliding_window_size": 124,
        }
        self.snapshot = {
            "id": "held-job",
            "workspace": "project",
            "status": "queued",
            "queue_held": True,
            "project_instance": "project:v1:" + "b" * 64,
            "owner_principal": "owner:v1:" + "a" * 64,
            "request_manifest": {},
            "recovery_attempt": None,
            "phase": "",
            "started_at": None,
            "execution_attempt": 1,
            "step": 0,
            "progress": 0,
            "clip_current": 0,
            "overall_progress": 0,
            "window_current": 0,
            "window_step": 0,
            "window_progress": 0,
            "clip_progress": 0,
        }
        self.projects = {"project": ("/project", self.snapshot["project_instance"])}
        self.calls = []
        self.namespace = {
            "hmac": hmac,
            "math": math,
            "time": time,
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            "H3_OFFLOAD_PLAN_PARAM_KEY": H3_OFFLOAD_PLAN_PARAM_KEY,
            "HTTPException": HTTPException,
            "H3LegalAccessError": H3LegalAccessError,
            "require_h3_execution_allowed": require_h3_execution_allowed,
            "wgp": types.SimpleNamespace(
                models_def={},
                server_config={
                    "services": legal_fixture.H3LegalAccessPolicyTests()._services(
                        "CA"
                    ),
                },
            ),
            "_h3_registered_architectures": lambda _models: (),
            "load_request_manifest": lambda *_args, **_kwargs: {
                "params": copy.deepcopy(self.params),
                "inputs": [],
            },
            "validate_manifest_inputs": lambda *_args: None,
            "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: True,
            "_require_h3_offload_plan_parity": lambda _job: None,
            "_queue_recovery_reconcile_cursor": lambda *_args, **_kwargs: None,
            "_h3_incomplete_recovery_prefix": lambda _job: None,
            "_job_uses_registered_h3": lambda _job: True,
            "next_recovery_attempt": lambda *_args: self.fail("Attempt consumed"),
            "_queue_recovery_worker": lambda *_args: self.fail("Worker started"),
        }
        for name in (
            "_h3_job_model_types",
            "_require_h3_legal_execution",
            "_h3_cow_manual_source_supported",
            "_queue_recovery_materialize_job",
        ):
            _load_launch_function(self.source, name, self.namespace)
        require = self.namespace["_require_h3_legal_execution"]

        def legal(models):
            self.calls.append(models)
            require(models)

        self.namespace["_require_h3_legal_execution"] = legal

    def recover(self, **changes):
        return self.namespace["_queue_recovery_materialize_job"](
            {**self.snapshot, **changes},
            self.projects,
        )

    def test_allowed_untouched_hold_restores_without_worker_or_attempt(self):
        for previous_reason in ("", "h3_legal_access_required"):
            with self.subTest(previous_reason=previous_reason):
                job, may_start = self.recover(
                    _recovery_reason_code=previous_reason,
                    recovery_attempt=0 if previous_reason else None,
                    recovery_cursor={
                        "completed_units": [],
                        "ordinary_repeat_offset": 0,
                    },
                )
                self.assertFalse(may_start)
                self.assertTrue(job["queue_held"])
                self.assertEqual(job["status"], "queued")
                self.assertEqual(job["recovery_state"], "restored")
                self.assertEqual(job["_recovery_reason_code"], "")
                self.assertEqual(job["recovery_attempt"], 0)
                self.assertEqual(job["execution_attempt"], 1)
                self.assertEqual(job["params"], self.params)
                self.assertEqual(job["_recovery_manifest_pointer"], {})
                self.assertTrue(job["_recovery_worker_pending"])
        self.assertEqual(self.calls, [("minimax_h3",)] * 2)

    def test_crash_after_explicit_release_restores_pending_receipt_to_held(self):
        job, may_start = self.recover()
        self.assertFalse(may_start)
        job["queue_held"] = False
        serialized = serialize_job(
            job,
            owner_digest=self.snapshot["owner_principal"],
            project_digest=self.snapshot["project_instance"],
            request_manifest={},
        )
        self.assertTrue(serialized["_recovery_worker_pending"])
        self.assertFalse(serialized["queue_held"])
        with tempfile.TemporaryDirectory() as folder:
            journal = QueueRecoveryJournal(f"{folder}/queue.jsonl")
            journal.commit_job(
                job["id"], serialized,
                expected_revision=0, expected_epoch=journal.recover().epoch,
            )
            serialized = journal.recover().jobs[job["id"]]
        recovered, may_start = self.namespace["_queue_recovery_materialize_job"](
            serialized, self.projects,
        )
        self.assertFalse(may_start)
        self.assertTrue(recovered["queue_held"])
        self.assertTrue(recovered["_recovery_worker_pending"])
        self.assertEqual(recovered["recovery_state"], "restored")
        self.assertEqual(recovered["execution_attempt"], 1)
        self.assertEqual(recovered["recovery_attempt"], 0)
        for patch in (
            {"phase": "loading"}, {"started_at": 1}, {"step": 1},
            {"_recovery_worker_pending": False},
            {"_recovery_worker_pending": 1},
            {"recovery_cursor": {"completed_units": [{"kind": "h3_segment"}]}},
        ):
            with self.subTest(patch=patch):
                denied, may_start = self.namespace["_queue_recovery_materialize_job"](
                    {**serialized, **patch}, self.projects,
                )
                self.assertFalse(may_start)
                self.assertNotEqual(denied["recovery_state"], "restored")

    def test_pending_receipt_serializer_rejects_non_boolean_values(self):
        from services.queue_recovery_adapter import QueueRecoveryAdapterError

        job, _ = self.recover()
        for value in (1, "true", [], None):
            with self.subTest(value=value), self.assertRaises(QueueRecoveryAdapterError):
                serialize_job(
                    {**job, "_recovery_worker_pending": value},
                    owner_digest=self.snapshot["owner_principal"],
                    project_digest=self.snapshot["project_instance"],
                    request_manifest={},
                )

    def test_actual_policy_denial_remains_held(self):
        for services in ({}, legal_fixture.H3LegalAccessPolicyTests()._services("US")):
            self.namespace["wgp"].server_config["services"] = services
            job, may_start = self.recover()
            self.assertFalse(may_start)
            self.assertTrue(job["queue_held"])
            self.assertEqual(job["recovery_state"], "blocked")
            self.assertEqual(job["_recovery_reason_code"], "h3_legal_access_required")
        self.assertEqual(self.calls, [("minimax_h3",)] * 2)

    def test_interrupted_remote_or_ambiguous_work_is_not_relaxed(self):
        for changes in (
            {"source_remote": True},
            {"queue_held": False},
            {"status": "running"},
            {"recovery_attempt": 1},
            {"recovery_attempt": "bad"},
            {"recovery_attempt": False},
            {"started_at": 1},
            {"phase_started_at": 1},
            {"execution_attempt": 2},
            {"execution_attempt": None},
            {"phase": "loading"},
            {"step": 1},
            {"progress": 1},
            {"clip_current": 1},
            {"window_current": 1},
            {"overall_progress": 1},
            {"recovery_unit": {"kind": "h3_segment"}},
            {"recovery_cursor": {"completed_units": [{"kind": "h3_segment"}]}},
            {"recovery_cursor": {"completed_units": [{"kind": "ordinary"}]}},
            {"recovery_cursor": {"completed_units": [], "ordinary_repeat_offset": 1}},
            {
                "recovery_cursor": {
                    "completed_units": [],
                    "ordinary_repeat_offset": False,
                }
            },
            {"recovery_cursor": "bad"},
            {"failure_details": {"code": "failure"}},
            {"output_files": ["output.mp4"]},
            {"artifact_files": ["native.mp4"]},
            {"_recovery_reason_code": "h3_peak_calibration_required"},
        ):
            with self.subTest(changes=changes):
                job, may_start = self.recover(**changes)
                self.assertFalse(may_start)
                self.assertTrue(job["queue_held"])
                self.assertNotEqual(job["recovery_state"], "restored")
        self.assertEqual(self.calls, [])

    def test_reconciliation_cannot_erase_prior_execution_evidence(self):
        self.namespace["_queue_recovery_reconcile_cursor"] = (
            lambda job, *_args, **_kwargs: job.update(
                {
                    "recovery_cursor": {"completed_units": []},
                }
            )
        )
        job, may_start = self.recover(
            recovery_cursor={"completed_units": [{"kind": "h3_segment"}]},
        )
        self.assertFalse(may_start)
        self.assertTrue(job["queue_held"])
        self.assertEqual(job["recovery_state"], "blocked")
        self.assertEqual(self.calls, [])

    def test_missing_core_state_stays_conservative(self):
        for field in ("execution_attempt", "phase", "progress", "step"):
            snapshot = dict(self.snapshot)
            snapshot.pop(field)
            job, may_start = self.namespace["_queue_recovery_materialize_job"](
                snapshot,
                self.projects,
            )
            self.assertFalse(may_start)
            self.assertTrue(job["queue_held"])
            self.assertEqual(job["recovery_state"], "blocked")
        self.assertEqual(self.calls, [])

    def test_verified_prefix_keeps_manual_recovery_authority(self):
        self.namespace["_h3_incomplete_recovery_prefix"] = lambda _job: 1
        self.namespace["_h3_native_boundary_exact_retry_allowed"] = lambda _job: False
        job, may_start = self.recover(_recovery_reason_code="h3_legal_access_required")
        self.assertFalse(may_start)
        self.assertTrue(job["queue_held"])
        self.assertEqual(job["recovery_state"], "blocked")
        self.assertEqual(
            job["_recovery_reason_code"],
            "h3_generation_recovery_authorization_required",
        )
        self.assertEqual(self.calls, [])

    def test_repeated_restart_cannot_normalize_ambiguous_state_into_pristine_hold(self):
        for changes in (
            {"recovery_attempt": "bad"},
            {"recovery_attempt": False},
            {"failure_details": {"code": "failure"}},
            {"recovery_cursor": {"completed_units": [{"kind": "h3_segment"}]}},
        ):
            with self.subTest(changes=changes):
                self.namespace["_queue_recovery_reconcile_cursor"] = (
                    lambda job, *_args, **_kwargs: job.update(
                        {
                            "recovery_cursor": {"completed_units": []},
                        }
                    )
                )
                job, may_start = self.recover(**changes)
                for _restart in range(3):
                    self.assertFalse(may_start)
                    self.assertEqual(job["recovery_state"], "blocked")
                    self.assertEqual(
                        job["_recovery_reason_code"],
                        "h3_generation_recovery_authorization_required",
                    )
                    # Startup checkpoints this normalized count. Use the real
                    # persistence allowlist before the next materialization.
                    job["recovery_attempt"] = int(job.get("recovery_attempt", 0) or 0)
                    persisted = serialize_job(
                        job,
                        owner_digest=self.snapshot["owner_principal"],
                        project_digest=self.snapshot["project_instance"],
                        request_manifest=self.snapshot["request_manifest"],
                    )
                    job, may_start = self.namespace["_queue_recovery_materialize_job"](
                        json.loads(json.dumps(persisted)),
                        self.projects,
                    )
        self.assertEqual(self.calls, [])

    def test_project_input_and_finality_holds_take_precedence(self):
        for mutation in ("project", "input", "finality"):
            with self.subTest(mutation=mutation):
                namespace = self.namespace.copy()
                projects = self.projects if mutation != "project" else {}
                if mutation == "input":

                    def invalid(*_args, **_kwargs):
                        raise QueueRecoveryRuntimeError("Invalid manifest")

                    namespace["load_request_manifest"] = invalid
                if mutation == "finality":
                    namespace["_queue_recovery_final_adoption_jobs"] = {
                        ("project", "held-job"): {"state": "quarantined"},
                    }
                _load_launch_function(
                    self.source, "_queue_recovery_materialize_job", namespace
                )
                job, may_start = namespace["_queue_recovery_materialize_job"](
                    self.snapshot,
                    projects,
                )
                self.assertFalse(may_start)
                self.assertTrue(job["queue_held"])
                self.assertEqual(job["recovery_state"], "blocked")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
