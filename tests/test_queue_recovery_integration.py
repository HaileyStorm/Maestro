"""Model-free lifecycle integration tests for durable queue recovery."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from services import job_lifecycle as lifecycle
from services.queue_recovery import QueueRecoveryJournal
from services.queue_recovery_adapter import (
    QueueRecoveryAdapterError,
    QueueRecoveryCoordinator,
    ensure_project_instance_marker,
    owner_principal_digest,
    project_instance_digest,
    serialize_global_state,
    serialize_job,
)
from services.h3_offload_plan import build_h3_offload_plan


class QueueRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        lifecycle._reset_queue_state_for_tests()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.journal = QueueRecoveryJournal(self.root / "queue.jsonl")
        self.coordinator = QueueRecoveryCoordinator(self.journal)
        self.secret = b"synthetic-queue-recovery-secret"
        self.owner = owner_principal_digest(self.secret, "raw-session-id")
        self.project = project_instance_digest(self.secret, "a" * 32)

    def tearDown(self):
        lifecycle._reset_queue_state_for_tests()
        self.temporary.cleanup()

    def _job(self, job_id="job-a", status="queued", **updates):
        job = {
            "id": job_id,
            "workspace": "synthetic-project",
            "model_type": "hunyuan3d",
            "generation_mode": "video",
            "private": True,
            "explicit": True,
            "explicit_output": False,
            "status": status,
            "message": "Queued",
            "created_at": 100.0,
            "queue_priority": 0,
            "queue_held": False,
            "params": {"prompt": "must not leak implicitly", "repeat_generation": 1},
            "prompt": "must not leak implicitly",
            "session_id": "must-not-persist",
            "out_dir": "/must/not/persist",
            "requested_outputs": 1,
            "output_files": [],
        }
        job.update(updates)
        return job

    def _register(self, job, manifest=None, global_state=None):
        self.coordinator.register_job(
            job,
            owner_digest=self.owner,
            project_digest=self.project,
            request_manifest=manifest or {
                "prompt": "explicit synthetic manifest",
                "input": "uploads/reference.png",
            },
            global_state=global_state,
        )

    @staticmethod
    def _credit_queue(
        *,
        decision="hosted_priority_credit",
        queue_band=1,
        requested=True,
        reservation_state="reserved",
        revalidation_state=None,
        transition_id="transition_recovery_0001",
        accounting=False,
    ):
        result = {
            "schema_version": 2 if accounting else 1,
            "realm": "hosted",
            "enforcement_enabled": True,
            "metering_applied": True,
            "decision": decision,
            "requested_units_positive": requested,
            "queue_band": queue_band,
            "reservation_state": reservation_state,
            "reservation_revision": (
                None if reservation_state is None else "e" * 64
            ),
            "revalidation_state": revalidation_state,
            "allowance_revision": "a" * 64,
            "allowance_observed_at": "2026-08-11T10:00:00Z",
            "transition_id": transition_id,
            "transition_history": [],
        }
        if accounting:
            result.update({
                "accounting_reservation_id": "reservation_" + "1" * 32,
                "accounting_reservation_revision": 1,
            })
        fingerprint_fields = [
                    "schema_version",
                    "realm",
                    "enforcement_enabled",
                    "metering_applied",
                    "decision",
                    "requested_units_positive",
                    "queue_band",
                    "reservation_state",
                    "reservation_revision",
                    "revalidation_state",
                    "allowance_revision",
                    "allowance_observed_at",
        ]
        if accounting:
            fingerprint_fields.extend([
                "accounting_reservation_id",
                "accounting_reservation_revision",
            ])
        fingerprint = hashlib.sha256(json.dumps(
            {key: result[key] for key in fingerprint_fields},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")).hexdigest()
        result["transition_history"] = [[transition_id, fingerprint]]
        return result

    def test_server_job_incarnation_timestamp_round_trips_exactly(self):
        job = self._job("discovered-job", created_at=4_321.25)
        self._register(job)
        restored = self.coordinator.restore().jobs[job["id"]]
        self.assertEqual(restored["created_at"], 4_321.25)

    def test_credit_queue_envelope_and_three_band_order_survive_restart(self):
        active = self._job(
            "active-credit",
            created_at=30.0,
            source_remote=True,
            credit_queue=self._credit_queue(),
        )
        standard = self._job(
            "standard-credit",
            created_at=20.0,
            source_remote=True,
        )
        depleted = self._job(
            "depleted-credit",
            created_at=10.0,
            source_remote=True,
            credit_queue=self._credit_queue(
                decision="hosted_baseline",
                queue_band=-1,
                reservation_state=None,
                transition_id="transition_recovery_0002",
            ),
        )
        for job in (depleted, standard, active):
            self._register(job)

        recovered = QueueRecoveryCoordinator(self.journal).restore()
        self.assertEqual(
            recovered.jobs[active["id"]]["credit_queue"],
            active["credit_queue"],
        )
        self.assertEqual(recovered.global_state["queue_order"], [
            standard["id"],
            active["id"],
            depleted["id"],
        ])
        self.assertTrue(
            recovered.jobs[active["id"]]["_credit_revalidation_required"],
        )
        self.assertFalse(recovered.jobs[depleted["id"]]["queue_held"])

    def test_credit_queue_v2_round_trips_only_opaque_accounting_linkage(self):
        credit_queue = self._credit_queue(accounting=True)
        job = self._job("accounting-credit", credit_queue=credit_queue)
        self._register(job)

        restored = QueueRecoveryCoordinator(self.journal).restore().jobs[job["id"]]
        self.assertEqual(restored["credit_queue"], credit_queue)
        self.assertTrue(restored["_credit_revalidation_required"])
        encoded = json.dumps(restored["credit_queue"], sort_keys=True)
        self.assertNotIn("account_id", encoded)
        self.assertNotIn("source", encoded)
        self.assertNotIn("provider", encoded)

    def test_credit_queue_recovery_rejects_unknown_or_inconsistent_metadata(self):
        variants = []
        unknown = self._credit_queue()
        unknown["account_id"] = "must-not-persist"
        variants.append(unknown)
        boosted_exclusion = self._credit_queue(
            decision="capability_excluded",
            queue_band=1,
            reservation_state=None,
        )
        variants.append(boosted_exclusion)
        wrong_realm = self._credit_queue()
        wrong_realm["realm"] = "lan"
        variants.append(wrong_realm)
        raw_allowance_identity = self._credit_queue()
        raw_allowance_identity["allowance_revision"] = "event_credit_0001"
        variants.append(raw_allowance_identity)
        wrong_reservation_revision = self._credit_queue()
        wrong_reservation_revision["reservation_revision"] = None
        variants.append(wrong_reservation_revision)
        unhashable_decision = self._credit_queue()
        unhashable_decision["decision"] = []
        variants.append(unhashable_decision)
        unhashable_reservation = self._credit_queue()
        unhashable_reservation["reservation_state"] = []
        variants.append(unhashable_reservation)

        for index, credit_queue in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(
                QueueRecoveryAdapterError,
            ):
                serialize_job(
                    self._job(f"tampered-credit-{index}", credit_queue=credit_queue),
                    owner_digest=self.owner,
                    project_digest=self.project,
                    request_manifest={"kind": "synthetic"},
                )

    def test_resource_intent_is_closed_and_legacy_active_jobs_restore_conservatively(self):
        job = self._job(
            "resource-job",
            resource_intent="generation",
            parent_job_id="parent-job",
        )
        self._register(job)
        restored = self.coordinator.restore().jobs[job["id"]]
        self.assertEqual(restored["resource_intent"], "generation")
        self.assertEqual(restored["parent_job_id"], "parent-job")

        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_job(
                self._job("bad-resource", resource_intent="raw-device-key"),
                owner_digest=self.owner,
                project_digest=self.project,
                request_manifest={"kind": "synthetic"},
            )
        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_job(
                self._job("bad-parent", parent_job_id="../raw-parent"),
                owner_digest=self.owner,
                project_digest=self.project,
                request_manifest={"kind": "synthetic"},
            )

        legacy = self._job("legacy-resource")
        self._register(legacy)
        recovered = QueueRecoveryCoordinator(self.journal).restore().jobs[
            legacy["id"]
        ]
        self.assertEqual(recovered["resource_intent"], "generation")

        cpu = self._job(
            "cpu-resource",
            resource_intent="text",
            resource_execution="cpu",
            preemption_mode="discard_restart",
            resource_state="running",
            execution_attempt=3,
        )
        self._register(cpu)
        recovered_cpu = QueueRecoveryCoordinator(self.journal).restore().jobs[
            cpu["id"]
        ]
        self.assertEqual(recovered_cpu["resource_intent"], "text")
        self.assertEqual(recovered_cpu["resource_execution"], "standard")
        self.assertEqual(recovered_cpu["preemption_mode"], "none")
        self.assertEqual(recovered_cpu["resource_state"], "queued")
        self.assertEqual(recovered_cpu["execution_attempt"], 3)

    def test_reference_terminal_parent_child_failure_round_trips_safely(self):
        private_error = "/private/models/secret.safetensors traceback payload"
        child = self._job(
            "reference-child",
            status="failed",
            parent_job_id="reference-parent",
            message="Generation failed.",
            resource_intent="generation",
            resource_execution="standard",
            preemption_mode="none",
            resource_state="released",
            execution_attempt=1,
        )
        parent = self._job(
            "reference-parent",
            status="failed",
            message="Generation failed.",
            resource_intent="text",
            resource_execution="standard",
            preemption_mode="none",
            resource_state="released",
            execution_attempt=1,
            failed_child_job_id=child["id"],
            failed_child_status="failed",
            failed_child_reason="model_load_failed",
            failure_details={
                "code": "model_load_failed",
                "stage": "model_load",
                "detail": private_error,
                "exception_type": "RuntimeError",
                "is_oom": False,
                "private_path": private_error,
            },
            error=private_error,
            traceback=private_error,
        )
        self._register(parent)
        self._register(child)

        restored = QueueRecoveryCoordinator(self.journal).restore().jobs
        restored_parent = restored[parent["id"]]
        restored_child = restored[child["id"]]
        self.assertEqual(
            restored_parent["failed_child_job_id"], child["id"],
        )
        self.assertEqual(restored_parent["failed_child_status"], "failed")
        self.assertEqual(
            restored_parent["failed_child_reason"], "model_load_failed",
        )
        self.assertEqual(restored_parent["failure_details"], {
            "code": "model_load_failed",
            "stage": "model_load",
            "detail": (
                "The generation model could not be loaded with the available host memory."
            ),
            "exception_type": "RuntimeError",
            "is_oom": False,
        })
        self.assertEqual(restored_child["parent_job_id"], parent["id"])
        self.assertNotIn(private_error, repr(restored))
        self.assertNotIn(
            private_error,
            (self.root / "queue.jsonl").read_text(encoding="utf-8"),
        )

    def test_reference_parent_child_oom_round_trips_only_safe_banner_fields(self):
        private_error = "/private/cuda/allocator traceback"
        child = self._job(
            "reference-oom-child",
            status="failed",
            parent_job_id="reference-oom-parent",
        )
        parent = self._job(
            "reference-oom-parent",
            status="failed",
            failed_child_job_id=child["id"],
            failed_child_status="failed",
            failed_child_reason="cuda_oom",
            failure_details={
                "code": "cuda_oom",
                "stage": "denoise",
                "detail": private_error,
                "exception_type": "OutOfMemoryError",
                "is_oom": True,
                "allocator": {
                    "device_type": "cuda",
                    "free_bytes": 10,
                    "private_path": private_error,
                },
            },
            oom_info={
                "is_oom": True,
                "stage": "denoise",
                "current_coefficient": 0.8,
                "suggested_coefficient": 0.7,
                "message": private_error,
                "allocator": {
                    "device_type": "cuda",
                    "free_bytes": 10,
                    "private_path": private_error,
                },
                "traceback": private_error,
            },
        )
        self._register(parent)
        self._register(child)

        restored = QueueRecoveryCoordinator(self.journal).restore().jobs
        restored_parent = restored[parent["id"]]
        self.assertEqual(restored_parent["failure_details"]["code"], "cuda_oom")
        self.assertEqual(restored_parent["oom_info"], {
            "is_oom": True,
            "stage": "denoise",
            "current_coefficient": 0.8,
            "suggested_coefficient": 0.7,
            "message": "The operation ran out of GPU memory.",
            "allocator": {"device_type": "cuda", "free_bytes": 10},
        })
        self.assertNotIn(private_error, repr(restored_parent))
        self.assertNotIn(
            private_error,
            (self.root / "queue.jsonl").read_text(encoding="utf-8"),
        )

    def test_optional_failure_diagnostics_nulls_are_omitted(self):
        snapshot = serialize_job(
            self._job(
                "completed-job",
                status="completed",
                failure_details=None,
                oom_info=None,
            ),
            owner_digest=self.owner,
            project_digest=self.project,
            request_manifest={"kind": "synthetic"},
        )
        self.assertNotIn("failure_details", snapshot)
        self.assertNotIn("oom_info", snapshot)

    def test_non_reference_rich_oom_info_retains_existing_drop_behavior(self):
        snapshot = serialize_job(
            self._job(
                "h3-delivery-failure",
                status="failed",
                failure_details={
                    "code": "cuda_oom",
                    "stage": "delivery",
                    "exception_type": "RuntimeError",
                    "is_oom": True,
                },
                oom_info={
                    "is_oom": True,
                    "delivery_resolution": "3840x2160",
                    "manual_retry_count": 3,
                    "recovery_action": "private-rich-h3-contract",
                },
            ),
            owner_digest=self.owner,
            project_digest=self.project,
            request_manifest={"kind": "synthetic"},
        )
        self.assertEqual(snapshot["failure_details"]["stage"], "delivery")
        self.assertNotIn("oom_info", snapshot)
        self.assertNotIn("private-rich-h3-contract", repr(snapshot))

    def test_same_job_resource_retry_oom_round_trips_safe_banner(self):
        private_error = "/private/cuda/retry allocator traceback"
        job = self._job(
            "same-job-oom-retry",
            status="queued",
            resource_retry_attempt=1,
            resource_retry_limit=2,
            resource_retry_phase="finalization",
            resource_retry_reason="finalization_oom",
            failure_details={
                "code": "cuda_oom",
                "stage": "delivery",
                "detail": private_error,
                "exception_type": "OutOfMemoryError",
                "is_oom": True,
                "allocator": {
                    "device_type": "cuda",
                    "free_bytes": 20,
                    "private_path": private_error,
                },
            },
            oom_info={
                "is_oom": True,
                "stage": "delivery",
                "current_coefficient": 0.8,
                "suggested_coefficient": 0.1,
                "message": private_error,
                "allocator": {
                    "device_type": "cuda",
                    "free_bytes": 20,
                    "private_path": private_error,
                },
                "traceback": private_error,
            },
        )
        self._register(job)

        restored = QueueRecoveryCoordinator(self.journal).restore().jobs[
            job["id"]
        ]
        self.assertEqual(restored["resource_retry_attempt"], 1)
        self.assertEqual(restored["resource_retry_phase"], "finalization")
        self.assertEqual(restored["failure_details"]["stage"], "delivery")
        self.assertEqual(restored["oom_info"], {
            "is_oom": True,
            "stage": "delivery",
            "current_coefficient": 0.8,
            "suggested_coefficient": 0.7,
            "message": "The operation ran out of GPU memory.",
            "allocator": {"device_type": "cuda", "free_bytes": 20},
        })
        self.assertNotIn(private_error, repr(restored))
        self.assertNotIn(
            private_error,
            (self.root / "queue.jsonl").read_text(encoding="utf-8"),
        )

    def test_reference_failure_durable_fields_fail_closed(self):
        valid = {
            "failed_child_job_id": "reference-child",
            "failed_child_status": "failed",
            "failed_child_reason": "generation_failed",
        }
        invalid_updates = (
            {"failed_child_job_id": "../private-child"},
            {"failed_child_job_id": "reference-parent"},
            {"failed_child_status": "running"},
            {"failed_child_reason": "private/path"},
            {"failed_child_reason": "a" * 65},
            {"failed_child_reason": True},
            {"failed_child_job_id": "reference-child"},
            {**valid, "failed_child_status": None},
        )
        for index, updates in enumerate(invalid_updates):
            with self.subTest(index=index, updates=updates):
                with self.assertRaises(QueueRecoveryAdapterError):
                    serialize_job(
                        self._job(
                            "reference-parent", status="failed", **updates,
                        ),
                        owner_digest=self.owner,
                        project_digest=self.project,
                        request_manifest={"kind": "synthetic"},
                    )

        for invalid_details in ("raw traceback", ["raw traceback"]):
            with self.subTest(invalid_details=invalid_details):
                with self.assertRaises(QueueRecoveryAdapterError):
                    serialize_job(
                        self._job(
                            "reference-parent",
                            status="failed",
                            failure_details=invalid_details,
                        ),
                        owner_digest=self.owner,
                        project_digest=self.project,
                        request_manifest={"kind": "synthetic"},
                    )

        invalid_oom = (
            "raw traceback",
            {"is_oom": False, "current_coefficient": 0.8},
            {"is_oom": True, "current_coefficient": True},
            {"is_oom": True, "current_coefficient": float("inf")},
            {"is_oom": True, "current_coefficient": 1.1},
        )
        for value in invalid_oom:
            with self.subTest(invalid_oom=value):
                with self.assertRaises(QueueRecoveryAdapterError):
                    serialize_job(
                        self._job(
                            "reference-parent",
                            status="failed",
                            **valid,
                            failure_details={
                                "code": "cuda_oom",
                                "stage": "generation",
                                "exception_type": "RuntimeError",
                                "is_oom": True,
                            },
                            oom_info=value,
                        ),
                        owner_digest=self.owner,
                        project_digest=self.project,
                        request_manifest={"kind": "synthetic"},
                    )

        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_job(
                self._job(
                    "reference-parent",
                    status="failed",
                    **valid,
                    failure_details={
                        "code": "generation_failed",
                        "stage": "generation",
                        "exception_type": "RuntimeError",
                        "is_oom": False,
                    },
                    oom_info={"is_oom": True, "current_coefficient": 0.8},
                ),
                owner_digest=self.owner,
                project_digest=self.project,
                request_manifest={"kind": "synthetic"},
            )

        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_job(
                self._job("reference-parent", status="running", **valid),
                owner_digest=self.owner,
                project_digest=self.project,
                request_manifest={"kind": "synthetic"},
            )

    def test_blocked_resource_admission_survives_coordinator_restart(self):
        job = self._job(
            "blocked-resource",
            kind="studio_project_asset_preparation",
            resource_intent="generation",
        )
        self._register(job, global_state={
            "paused": False,
            "pause_after_current": False,
            "manual_order_sequence": 0,
            "queue_order": [job["id"]],
        })
        lifecycle.configure_durability_hook(
            self.coordinator.prospective_transition,
        )
        self.assertTrue(lifecycle.block_resource_admission_failure(job))
        restored = QueueRecoveryCoordinator(self.journal).restore().jobs[
            job["id"]
        ]
        self.assertEqual(restored["status"], "queued")
        self.assertTrue(restored["queue_held"])
        self.assertEqual(restored["recovery_state"], "blocked_preparation")
        self.assertEqual(restored["phase"], "resource_admission_failed")
        self.assertEqual(restored["resource_intent"], "generation")
        self.assertNotIn("session_id", restored)

    def test_positive_allowlist_preserves_authority_ui_and_unit_state_only(self):
        job = self._job(
            recovery_unit={"kind": "segment", "index": 2, "complete": True},
            window_current=2,
            window_total=4,
            window_step=5,
            window_total_steps=20,
            window_progress=25,
            overall_progress=40,
            clip_current=2,
            clip_total=4,
            clip_progress=0.25,
            current_segment_model="hunyuan3d",
            current_segment_boundary={
                "type": "cut", "source": "explicit_cut",
                "at_seconds": 5.0, "event": "AUTHORED_SENTINEL",
            },
            h3_segment_plan={
                "kind": "h3_segments",
                "clip_count": 2,
                "fps": 24,
                "published_frames": 240,
                "checkpoint_options": [{
                    "model_type": "minimax_h3_ref2va",
                    "name": "Ref2VA",
                    "conditioning_mode": "semantic_references",
                    "is_downloaded": False,
                    "managed_download": True,
                    "auto_download": True,
                    "terms_required": True,
                    "available": True,
                    "unavailable_reason": "",
                    "download_url": "must not persist",
                }],
                "segments": [{
                    "index": 1,
                    "frames": 124,
                    "duration_seconds": 124 / 24,
                    "generated_frames": 124,
                    "published_frames": 116,
                    "generated_duration_seconds": 124 / 24,
                    "published_duration_seconds": 116 / 24,
                    "model_type": "hunyuan3d",
                    "boundary_from_previous": {
                        "type": "transition",
                        "source": "explicit_transition",
                        "event": "AUTHORED_SENTINEL",
                    },
                    "prompt_preview": "implicit prompt must be dropped",
                }],
            },
        )
        snapshot = serialize_job(
            job,
            owner_digest=self.owner,
            project_digest=self.project,
            request_manifest={"prompt": "explicit synthetic manifest"},
        )
        rendered = repr(snapshot)
        self.assertEqual(snapshot["workspace"], "synthetic-project")
        self.assertEqual(snapshot["model_type"], "hunyuan3d")
        self.assertTrue(snapshot["private"])
        self.assertTrue(snapshot["explicit"])
        self.assertEqual(snapshot["recovery_unit"]["index"], 2)
        self.assertEqual(snapshot["window_total_steps"], 20)
        recovered_plan = snapshot["h3_segment_plan"]
        self.assertEqual(recovered_plan["fps"], 24)
        self.assertEqual(recovered_plan["published_frames"], 240)
        self.assertEqual(recovered_plan["segments"][0]["generated_frames"], 124)
        self.assertEqual(recovered_plan["segments"][0]["published_frames"], 116)
        self.assertEqual(
            recovered_plan["segments"][0]["published_duration_seconds"],
            116 / 24,
        )
        self.assertEqual(snapshot["request_manifest"]["prompt"], "explicit synthetic manifest")
        self.assertEqual(
            recovered_plan["checkpoint_options"][0]["model_type"],
            "minimax_h3_ref2va",
        )
        self.assertNotIn("download_url", recovered_plan["checkpoint_options"][0])
        self.assertNotIn("must not leak implicitly", rendered)
        self.assertNotIn("session_id", rendered)
        self.assertNotIn("out_dir", rendered)
        self.assertNotIn("prompt_preview", rendered)
        self.assertNotIn("AUTHORED_SENTINEL", rendered)
        self.assertEqual(
            snapshot["current_segment_boundary"],
            {"type": "cut", "source": "explicit_cut", "at_seconds": 5.0},
        )

    def test_waiting_h3_duration_plan_round_trips_across_restart(self):
        candidate = {
            "requested_published_frames": 346,
            "candidate_published_frames": 345,
            "segment_count": 1,
            "generated_frames": [345],
            "segment_published_frames": [345],
            "confidence": "high",
            "applied": True,
            "reason": "Authoritative boundary.",
        }
        duration_plan = {
            "revision": "h3dp1_" + "a" * 64,
            "target_published_frames": 346,
            "current_published_frames": 346,
            "current_generated_frames": 350,
            "fps": 24,
            "snap_candidates": {
                "nearest": copy.deepcopy(candidate),
                "down": copy.deepcopy(candidate),
            },
            "segments": [
                {
                    "index": 1,
                    "published_frames": 175,
                    "min_published_frames": 124,
                    "max_published_frames": 345,
                    "grid_step": 1,
                    "grid_offset": 0,
                    "authored_locked": True,
                    "completed_locked": False,
                    "lock_reason": "authored",
                },
                {
                    "index": 2,
                    "published_frames": 171,
                    "min_published_frames": 124,
                    "max_published_frames": 345,
                    "grid_step": 1,
                    "grid_offset": 0,
                    "authored_locked": False,
                    "completed_locked": True,
                    "lock_reason": "completed",
                },
            ],
            "redistribution_mode": "future",
            "outcome": "exact",
            "reason": "Server-owned duration plan.",
            "residual_published_frames": 0,
        }
        public_plan = {
            "kind": "h3_segments",
            "clip_count": 2,
            "fps": 24,
            "published_frames": 346,
            "segments": [
                {
                    "index": 1,
                    "generated_frames": 175,
                    "published_frames": 175,
                    "generated_duration_seconds": 175 / 24,
                    "published_duration_seconds": 175 / 24,
                    "duration_min_published_frames": 124,
                    "duration_max_published_frames": 345,
                    "duration_grid_step": 1,
                    "duration_grid_offset": 0,
                    "authored_locked": True,
                    "completed_locked": False,
                    "lock_reason": "authored",
                },
                {
                    "index": 2,
                    "generated_frames": 175,
                    "published_frames": 171,
                    "generated_duration_seconds": 175 / 24,
                    "published_duration_seconds": 171 / 24,
                    "duration_min_published_frames": 124,
                    "duration_max_published_frames": 345,
                    "duration_grid_step": 1,
                    "duration_grid_offset": 0,
                    "authored_locked": False,
                    "completed_locked": True,
                    "lock_reason": "completed",
                },
            ],
            "duration_plan": duration_plan,
        }
        job = self._job(
            "waiting-duration",
            status="waiting_for_plan_approval",
            h3_segment_plan=public_plan,
            plan_review_required=True,
            plan_review_deadline=200.0,
        )
        self._register(job)
        restored = QueueRecoveryCoordinator(self.journal).restore().jobs[job["id"]]
        self.assertEqual(restored["status"], "waiting_for_plan_approval")
        self.assertEqual(restored["h3_segment_plan"]["duration_plan"], duration_plan)
        self.assertEqual(
            restored["h3_segment_plan"]["segments"][0][
                "duration_min_published_frames"
            ],
            124,
        )
        self.assertTrue(
            restored["h3_segment_plan"]["segments"][1]["completed_locked"]
        )

    def test_h3_recovery_geometry_rejects_nonfinite_or_boolean_numbers(self):
        base_plan = {
            "kind": "h3_segments",
            "clip_count": 1,
            "fps": 24,
            "published_frames": 124,
            "segments": [{
                "index": 1,
                "generated_frames": 124,
                "published_frames": 124,
                "generated_duration_seconds": 124 / 24,
                "published_duration_seconds": 124 / 24,
            }],
        }
        invalid_plans = []
        for field, value in (("fps", float("inf")), ("published_frames", True)):
            invalid_plans.append({**base_plan, field: value})
        invalid_plans.append({
            **base_plan,
            "segments": [{**base_plan["segments"][0], "published_frames": 0}],
        })
        for plan in invalid_plans:
            with self.subTest(plan=plan):
                with self.assertRaises(QueueRecoveryAdapterError):
                    serialize_job(
                        self._job(h3_segment_plan=plan),
                        owner_digest=self.owner,
                        project_digest=self.project,
                        request_manifest={"prompt": "synthetic"},
                    )

    def test_manifest_and_global_reject_absolute_paths_and_unknown_fields(self):
        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_job(
                self._job(),
                owner_digest=self.owner,
                project_digest=self.project,
                request_manifest={"input": "/private/input.png"},
            )
        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_job(
                self._job(),
                owner_digest=self.owner,
                project_digest=self.project,
                request_manifest={"clientSecret": "must-not-persist"},
            )
        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_global_state({"paused": False, "prompt": "no"})
        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_global_state({"paused": False, "queue_order": ["/job"]})
        snapshot = serialize_job(
            self._job(output_files=[r"clips\..\private.mp4", r"clips\safe.mp4"]),
            owner_digest=self.owner,
            project_digest=self.project,
            request_manifest={"prompt": "synthetic"},
        )
        self.assertEqual(snapshot["output_files"], ["clips/safe.mp4"])

    def test_h3_offload_plan_round_trips_exactly_and_tampering_fails_closed(self):
        params = {
            "model_type": "minimax_h3",
            "resolution": "1344x768",
            "video_length": 124,
            "num_inference_steps": 20,
            "override_profile": 4,
            "prompt": "PRIVATE_OFFLOAD_SENTINEL",
        }
        plan = build_h3_offload_plan(params, effective_profile=5)
        job = self._job(
            model_type="minimax_h3",
            h3_offload_plan=plan,
        )
        self._register(job)
        restored = self.coordinator.restore().jobs[job["id"]]
        self.assertEqual(restored["h3_offload_plan"], plan)
        self.assertNotIn("PRIVATE_OFFLOAD_SENTINEL", repr(restored))

        tampered = copy.deepcopy(plan)
        tampered["profile"] = 5
        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_job(
                self._job(h3_offload_plan=tampered),
                owner_digest=self.owner,
                project_digest=self.project,
                request_manifest={"schema": 1},
            )

    def test_legacy_recovery_job_without_offload_plan_remains_compatible(self):
        job = self._job("legacy-h3", model_type="minimax_h3")
        self._register(job)
        restored = self.coordinator.restore().jobs[job["id"]]
        self.assertNotIn("h3_offload_plan", restored)

    def test_hmac_owner_interface_never_persists_raw_principal(self):
        job = self._job()
        self._register(job)
        raw = (self.root / "queue.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("raw-session-id", raw)
        self.assertIn(self.owner, raw)

    def test_project_marker_is_stable_recreation_distinct_and_symlink_safe(self):
        project = self.root / "project"
        project.mkdir()
        first = ensure_project_instance_marker(project)
        self.assertEqual(first, ensure_project_instance_marker(project))
        marker = project / ".maestro-project-instance"
        marker.unlink()
        project.rmdir()
        project.mkdir()
        second = ensure_project_instance_marker(project)
        self.assertNotEqual(first, second)

        target = self.root / "target"
        target.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            ensure_project_instance_marker(linked)

        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        nested = real_parent / "project"
        nested.mkdir()
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            ensure_project_instance_marker(linked_parent / "project")

    def test_project_marker_detects_post_create_inode_replacement(self):
        project = self.root / "raced-project"
        project.mkdir()
        marker = project / ".maestro-project-instance"
        real_fsync = os.fsync
        calls = 0

        def replace_after_first_fsync(descriptor):
            nonlocal calls
            real_fsync(descriptor)
            calls += 1
            if calls == 1:
                original = project / ".original-marker"
                marker.rename(original)
                marker.write_text("b" * 32 + "\n", encoding="ascii")

        with mock.patch(
            "services.queue_recovery_adapter.os.fsync",
            side_effect=replace_after_first_fsync,
        ):
            with self.assertRaises(QueueRecoveryAdapterError):
                ensure_project_instance_marker(project)

    def test_project_marker_rejects_hardlink_created_during_read(self):
        project = self.root / "hardlink-raced-project"
        project.mkdir()
        ensure_project_instance_marker(project)
        marker = project / ".maestro-project-instance"
        alias = project / ".marker-alias"
        real_read = os.read
        linked = False

        def link_during_read(descriptor, count):
            nonlocal linked
            value = real_read(descriptor, count)
            if not linked:
                os.link(marker, alias)
                linked = True
            return value

        with mock.patch(
            "services.queue_recovery_adapter.os.read",
            side_effect=link_during_read,
        ):
            with self.assertRaises(QueueRecoveryAdapterError):
                ensure_project_instance_marker(project)
        self.assertTrue(alias.is_file())

    def test_persistence_failure_prevents_job_and_global_memory_mutation(self):
        job = self._job()
        before = dict(job)

        def fail(_proposal):
            raise OSError("injected durable failure")

        lifecycle.configure_durability_hook(fail)
        with self.assertRaises(OSError):
            lifecycle.update_queue_job(job, priority=99, held=True)
        self.assertEqual(job, before)
        self.assertEqual(lifecycle.queue_control_state()["paused"], False)
        with self.assertRaises(OSError):
            lifecycle.set_queue_paused(True)
        self.assertEqual(lifecycle.queue_control_state()["paused"], False)

    def test_persistence_failure_prevents_abort_and_safe_yield_side_effects(self):
        job = self._job(status="running", hold_after_output=True)
        active = {"job-a": {"abort": False}}
        lifecycle.register_abort_state(job, "job-a", active, active["job-a"])
        generation_lock = threading.Lock()
        generation_lock.acquire()

        def fail(_proposal):
            raise OSError("injected durable failure")

        lifecycle.configure_durability_hook(fail)
        with self.assertRaises(OSError):
            lifecycle.request_cancel(job, job_id="job-a", active_states=active)
        self.assertEqual(job["status"], "running")
        self.assertFalse(job.get("cancel_requested", False))
        self.assertFalse(active["job-a"]["abort"])
        with self.assertRaises(OSError):
            lifecycle.yield_generation_slot_after_output(generation_lock, job)
        self.assertTrue(generation_lock.locked())
        self.assertEqual(job["status"], "running")
        generation_lock.release()

    def test_queue_candidate_copy_cannot_overwrite_concurrent_lifecycle_edit(self):
        job = self._job()
        queue_hook_entered = threading.Event()
        release_queue_hook = threading.Event()

        def blocking_hook(proposal):
            if proposal.name == "queue_job":
                queue_hook_entered.set()
                self.assertTrue(release_queue_hook.wait(timeout=2))

        lifecycle.configure_durability_hook(blocking_hook)
        results = []
        queue_thread = threading.Thread(
            target=lambda: results.append(
                lifecycle.update_queue_job(job, priority=9),
            ),
        )
        output_thread = threading.Thread(
            target=lambda: results.append(
                lifecycle.update_requested_outputs(job, 5),
            ),
        )
        queue_thread.start()
        self.assertTrue(queue_hook_entered.wait(timeout=2))
        output_thread.start()
        time.sleep(0.03)
        self.assertTrue(output_thread.is_alive())
        release_queue_hook.set()
        queue_thread.join(timeout=2)
        output_thread.join(timeout=2)
        self.assertEqual(results, [True, True])
        self.assertEqual(job["queue_priority"], 9)
        self.assertEqual(job["requested_outputs"], 5)
        self.assertEqual(job["params"]["repeat_generation"], 5)

    def test_cancel_is_published_before_reentrant_interrupt_callback(self):
        job = self._job(status="running")
        active = {"job-a": {"abort": False}}
        callback_results = []
        lifecycle.register_abort_state(
            job,
            "job-a",
            active,
            active["job-a"],
            interrupt_model=lambda: callback_results.append(
                lifecycle.finish_job(job, "completed", message="too late"),
            ),
        )
        result = lifecycle.request_cancel(
            job, job_id="job-a", active_states=active,
        )
        self.assertTrue(result.changed)
        self.assertEqual(callback_results, [False])
        self.assertEqual(job["status"], "cancelled")

    def test_generation_slot_releases_lock_when_pause_persistence_fails(self):
        job = self._job(pause_queue_after=True)
        lifecycle.restore_scheduler_state([job], {
            "paused": False,
            "pause_after_current": False,
            "manual_order_sequence": 0,
            "queue_order": [job["id"]],
        })

        def fail_pause(proposal):
            if proposal.name == "slot_pause_after_current":
                raise OSError("injected pause persistence failure")

        lifecycle.configure_durability_hook(fail_pause)
        generation_lock = threading.Lock()
        with self.assertRaises(OSError):
            with lifecycle.generation_slot(generation_lock, job) as acquired:
                self.assertTrue(acquired)
        self.assertNotIn("_generation_slot_owned", job)
        self.assertFalse(lifecycle.queue_control_state()["paused"])
        self.assertTrue(generation_lock.acquire(blocking=False))
        generation_lock.release()

    def test_queue_register_failure_consumes_no_restore_or_sequence_state(self):
        job = self._job(_queue_restore_sequence=7)

        def fail_register(proposal):
            if proposal.name == "queue_register":
                raise OSError("injected registration persistence failure")

        lifecycle.configure_durability_hook(fail_register)
        with self.assertRaises(OSError):
            lifecycle.acquire_generation_slot(threading.Lock(), job)
        self.assertEqual(job["_queue_restore_sequence"], 7)
        self.assertNotIn("_queue_enqueued_monotonic", job)
        self.assertEqual(lifecycle._queue_sequence, 0)
        self.assertNotIn(id(job), lifecycle._queue_waiters)

    def test_correlated_transition_survives_coordinator_restart(self):
        job = self._job()
        self._register(job, global_state={
            "paused": False,
            "pause_after_current": False,
            "manual_order_sequence": 0,
            "queue_order": [job["id"]],
        })
        lifecycle.configure_durability_hook(self.coordinator.prospective_transition)
        self.assertTrue(lifecycle.update_queue_job(job, priority=7, held=True))

        restarted = QueueRecoveryCoordinator(
            QueueRecoveryJournal(self.root / "queue.jsonl"),
        ).restore()
        self.assertEqual(restarted.jobs[job["id"]]["queue_priority"], 7)
        self.assertTrue(restarted.jobs[job["id"]]["queue_held"])
        self.assertFalse(restarted.global_state["paused"])
        self.assertNotIn("_queue_enqueued_monotonic", repr(restarted.jobs))

    def test_sealed_preparation_pointer_and_waiting_state_commit_together(self):
        job = self._job(status="preparing", message="Enhancing prompt")
        initial = {
            "path": ".maestro-recovery/job-a.request.json",
            "schema": 1, "sha256": "a" * 64, "size": 10,
        }
        prepared = {
            "path": ".maestro-recovery/job-a.11111111111111111111111111111111.request.json",
            "schema": 1, "sha256": "b" * 64, "size": 11,
        }
        self._register(job, manifest=initial)
        lifecycle.configure_durability_hook(
            self.coordinator.prospective_transition,
        )
        self.assertTrue(lifecycle.complete_preparation(
            job,
            request_manifest=prepared,
            waiting_for_approval=True,
            plan_review_deadline=time.time() + 16,
            params={"prompt": "private enhanced prompt"},
            phase="awaiting_plan_approval",
            message="Review the generation plan",
        ))

        restored = QueueRecoveryCoordinator(
            QueueRecoveryJournal(self.root / "queue.jsonl"),
        ).restore().jobs[job["id"]]
        self.assertEqual(restored["status"], "waiting_for_plan_approval")
        self.assertTrue(restored["plan_review_required"])
        self.assertGreater(restored["plan_review_deadline"], time.time())
        self.assertEqual(restored["request_manifest"], prepared)
        self.assertNotIn("private enhanced prompt", repr(restored))

    def test_terms_unblock_deadline_is_durable_and_keeps_frozen_manifest(self):
        job = self._job(status="preparing", message="Planning generation")
        initial = {
            "path": ".maestro-recovery/job-a.request.json",
            "schema": 1, "sha256": "a" * 64, "size": 10,
        }
        prepared = {
            "path": ".maestro-recovery/job-a.22222222222222222222222222222222.request.json",
            "schema": 1, "sha256": "b" * 64, "size": 11,
        }
        self._register(job, manifest=initial)
        lifecycle.configure_durability_hook(
            self.coordinator.prospective_transition,
        )
        self.assertTrue(lifecycle.complete_preparation(
            job,
            request_manifest=prepared,
            waiting_for_approval=True,
            plan_review_terms_required=True,
            phase="awaiting_plan_terms",
            message="Approval required to accept Ref2VA terms",
        ))
        deadline = time.time() + 16
        self.assertTrue(lifecycle.arm_prepared_job_plan_review(
            job,
            plan_review_deadline=deadline,
            phase="awaiting_plan_approval",
            message="Review the generation plan",
        ))

        restored = QueueRecoveryCoordinator(
            QueueRecoveryJournal(self.root / "queue.jsonl"),
        ).restore().jobs[job["id"]]
        self.assertEqual(restored["status"], "waiting_for_plan_approval")
        self.assertFalse(restored["plan_review_terms_required"])
        self.assertEqual(restored["plan_review_deadline"], deadline)
        self.assertEqual(restored["request_manifest"], prepared)

    def test_initial_registration_can_atomically_include_scheduler_order(self):
        job = self._job()
        self._register(job, global_state=lifecycle.durable_queue_state(
            additions=(job,),
        ))
        restored = self.coordinator.restore()
        self.assertEqual(restored.global_state["queue_order"], [job["id"]])

    def test_later_registration_preserves_all_equal_time_queue_entries(self):
        first = self._job("first", created_at=100.0)
        second = self._job("second", created_at=100.0)
        self._register(first, global_state=lifecycle.durable_queue_state(
            additions=(first,),
        ))
        self._register(second, global_state=lifecycle.durable_queue_state(
            additions=(second,),
        ))
        self.assertEqual(
            self.coordinator.restore().global_state["queue_order"],
            ["first", "second"],
        )

    def test_registration_order_includes_prior_job_without_global_snapshot(self):
        first = self._job("first", created_at=100.0)
        second = self._job("second", created_at=100.0)
        self._register(first)
        self._register(second, global_state=lifecycle.durable_queue_state(
            additions=(second,),
        ))
        self.assertEqual(
            self.coordinator.restore().global_state["queue_order"],
            ["first", "second"],
        )

    def test_lifecycle_start_outputs_finish_and_terminal_tombstone(self):
        job = self._job()
        self._register(job)
        lifecycle.configure_durability_hook(self.coordinator.prospective_transition)
        self.assertTrue(lifecycle.try_start(job, message="Running"))
        self.assertEqual(lifecycle.record_job_outputs(job, ["result.mp4"]), ["result.mp4"])
        self.assertTrue(lifecycle.finish_job(job, "completed", message="Done"))
        restored = self.coordinator.restore()
        self.assertEqual(restored.jobs[job["id"]]["status"], "completed")
        self.assertEqual(restored.jobs[job["id"]]["output_files"], ["result.mp4"])
        self.coordinator.tombstone_terminal(job["id"])
        self.assertNotIn(job["id"], self.coordinator.restore().jobs)

    def test_terminal_path_message_is_redacted_without_losing_transition(self):
        job = self._job()
        self._register(job)
        lifecycle.configure_durability_hook(self.coordinator.prospective_transition)
        self.assertTrue(lifecycle.try_start(job))
        self.assertTrue(lifecycle.finish_job(
            job,
            "failed",
            message="Renderer failed at /private/runtime/output.tmp",
        ))
        restored = self.coordinator.restore().jobs[job["id"]]
        self.assertEqual(restored["status"], "failed")
        self.assertEqual(restored["message"], "[path redacted]")
        self.assertNotIn("/private", repr(restored))

    def test_restore_reapplies_allowlist_and_rejects_raw_identities(self):
        prompt_path = self.root / "generic.jsonl"
        prompt_journal = QueueRecoveryJournal(prompt_path)
        prompt_journal.commit_job(
            "job-generic",
            {
                "id": "job-generic",
                "status": "queued",
                "workspace": "synthetic-project",
                "owner_principal": self.owner,
                "project_instance": self.project,
                "request_manifest": {"prompt": "explicit manifest"},
                "prompt": "generic journal field must be stripped",
            },
            expected_revision=0,
            expected_epoch=0,
        )
        clean = QueueRecoveryCoordinator(prompt_journal).restore()
        self.assertNotIn("prompt", clean.jobs["job-generic"])
        self.assertEqual(
            clean.jobs["job-generic"]["request_manifest"]["prompt"],
            "explicit manifest",
        )

        invalid_journal = QueueRecoveryJournal(self.root / "invalid.jsonl")
        invalid_journal.commit_job(
            "job-invalid",
            {
                "id": "job-invalid",
                "status": "queued",
                "owner_principal": "raw-session-id",
                "project_instance": self.project,
                "request_manifest": {},
            },
            expected_revision=0,
            expected_epoch=0,
        )
        with self.assertRaises(QueueRecoveryAdapterError):
            QueueRecoveryCoordinator(invalid_journal)

    def test_restore_rejects_semantically_invalid_generic_global_state(self):
        invalid_values = (
            {"paused": "false"},
            {"pause_after_current": []},
            {"manual_order_sequence": "bad"},
            {"manual_order_sequence": -1},
            {"manual_order_sequence": 1 << 80},
            {"queue_order": "job-a"},
            {"queue_order": [""]},
            {"queue_order": ["../job"]},
            {"queue_order": ["job-a", "job-a"]},
        )
        for index, invalid in enumerate(invalid_values):
            with self.subTest(invalid=invalid):
                journal = QueueRecoveryJournal(
                    self.root / f"invalid-global-{index}.jsonl",
                )
                journal.commit_global(
                    invalid,
                    expected_revision=0,
                    expected_epoch=0,
                )
                with self.assertRaises(QueueRecoveryAdapterError):
                    QueueRecoveryCoordinator(journal)

    def test_adapter_compaction_rewrites_only_sanitized_active_snapshots(self):
        path = self.root / "generic-compact.jsonl"
        journal = QueueRecoveryJournal(path, max_events=1)
        journal.commit_state(
            jobs={
                "job-generic": {
                    "id": "job-generic",
                    "status": "queued",
                    "workspace": "synthetic-project",
                    "owner_principal": self.owner,
                    "project_instance": self.project,
                    "request_manifest": {"prompt": "explicit manifest"},
                    "prompt": "raw generic prompt must disappear",
                },
            },
            tombstones=("retired",),
            global_state={
                "paused": False,
                "pause_after_current": False,
                "manual_order_sequence": 0,
                "queue_order": ["job-generic"],
            },
            expected_job_revisions={"job-generic": 0, "retired": 0},
            expected_global_revision=0,
            expected_epoch=0,
        )
        coordinator = QueueRecoveryCoordinator(journal)
        compacted = coordinator.compact()
        self.assertEqual(compacted.epoch, 1)
        self.assertEqual(compacted.job_revisions["job-generic"], 2)
        self.assertNotIn("retired", compacted.job_revisions)
        self.assertNotIn("prompt", compacted.jobs["job-generic"])
        replayed = journal.recover()
        self.assertNotIn("prompt", replayed.jobs["job-generic"])
        self.assertNotIn(
            "raw generic prompt must disappear",
            path.read_text(encoding="utf-8"),
        )

    def test_restore_scheduler_state_preserves_order_and_pause_not_monotonic(self):
        first = self._job("first", queue_priority=0)
        second = self._job("second", queue_priority=0)
        lifecycle.restore_scheduler_state([first, second], {
            "paused": True,
            "pause_after_current": False,
            "manual_order_sequence": 8,
            "queue_order": ["second", "first"],
        })
        self.assertEqual(lifecycle.queue_control_state(), {
            "paused": True, "pause_after_current": False,
        })
        # Restore timestamps are freshly process-local, never supplied by the
        # recovered state. Queue order remains deterministic before threads run.
        self.assertIsInstance(first["_queue_enqueued_monotonic"], float)
        self.assertIsInstance(second["_queue_enqueued_monotonic"], float)
        waiter_order = [
            entry[1][1]["id"]
            for entry in sorted(
                lifecycle._queue_waiters.items(), key=lambda item: item[1][0],
            )
        ]
        self.assertEqual(waiter_order, ["second", "first"])

    def test_concurrent_transition_commits_are_revision_serialized(self):
        first = self._job("first")
        second = self._job("second")
        self._register(first)
        self._register(second)
        lifecycle.configure_durability_hook(self.coordinator.prospective_transition)
        barrier = threading.Barrier(3)
        errors = []

        def mutate(job, priority):
            try:
                barrier.wait()
                lifecycle.update_queue_job(job, priority=priority)
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)

        threads = [
            threading.Thread(target=mutate, args=(first, 10)),
            threading.Thread(target=mutate, args=(second, 20)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(errors, [])
        restored = self.coordinator.restore().jobs
        self.assertEqual(restored["first"]["queue_priority"], 10)
        self.assertEqual(restored["second"]["queue_priority"], 20)


if __name__ == "__main__":
    unittest.main()
