"""Content-free regressions for detached generation preparation failures."""

from __future__ import annotations

import ast
from collections.abc import Mapping
import contextlib
import copy
import ipaddress
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
_LAUNCH = _APP / "launch.py"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from services.job_lifecycle import (  # noqa: E402
    _credit_queue_fingerprint,
    _reset_queue_state_for_tests,
    complete_preparation,
    configure_credit_lifecycle_callback,
    configure_durability_hook,
    fail_preparation,
    update_preparation_job,
)
from services.planning_failure import (  # noqa: E402
    PLANNING_FAILURE_ENVELOPE_KEYS,
    PLANNING_FAILURE_REASON_CODES,
    normalize_planning_failure_envelope,
    planning_failure_envelope,
    planning_failure_event,
    public_planning_failure_message,
    remove_exact_request_manifest,
)
from services.queue_recovery_runtime import (  # noqa: E402
    MANIFEST_DIRECTORY,
    _manifest_dir_fd_supported,
    atomic_write_request_manifest,
    load_request_manifest,
)


class _HTTPException(Exception):
    def __init__(self, *, status_code: int):
        self.status_code = status_code


def _load_launch_nodes(names: set[str], namespace: dict) -> dict:
    source = _LAUNCH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_LAUNCH))
    selected = []
    for node in tree.body:
        if not (
            isinstance(node, (ast.FunctionDef, ast.ClassDef))
            and node.name in names
        ):
            continue
        node = copy.deepcopy(node)
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
        selected.append(node)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(selected, type_ignores=[])),
            str(_LAUNCH),
            "exec",
        ),
        namespace,
    )
    return namespace


def _request(role: str = "owner"):
    return types.SimpleNamespace(
        headers={
            "authorization": "must-not-be-retained",
            "cookie": "must-not-be-retained",
            "x-forwarded-proto": "https",
        },
        base_url="http://127.0.0.1/",
        client=types.SimpleNamespace(host="127.0.0.1"),
        state=types.SimpleNamespace(
            maestro_session_id="synthetic-session",
            maestro_remote=False,
            maestro_account_principal={
                "id": "a" * 32,
                "role": role,
                "username": "must-not-be-retained",
                "recently_reauthenticated": True,
            },
        ),
    )


def _reserved_credit() -> dict:
    queue = {
        "schema_version": 2,
        "realm": "hosted",
        "enforcement_enabled": True,
        "metering_applied": True,
        "decision": "hosted_priority_credit",
        "requested_units_positive": True,
        "queue_band": 1,
        "reservation_state": "reserved",
        "reservation_revision": "a" * 64,
        "revalidation_state": None,
        "allowance_revision": "b" * 64,
        "allowance_observed_at": "2026-08-18T00:00:00Z",
        "transition_id": "transition_planning_reserved",
        "transition_history": [],
        "accounting_reservation_id": "reservation_" + "c" * 32,
        "accounting_reservation_revision": 1,
    }
    queue["transition_history"] = [[
        queue["transition_id"], _credit_queue_fingerprint(queue),
    ]]
    return queue


class GenerationPlanningFailureTests(unittest.TestCase):
    def setUp(self):
        _reset_queue_state_for_tests()

    def tearDown(self):
        configure_credit_lifecycle_callback(None)
        configure_durability_hook(None)
        _reset_queue_state_for_tests()

    def _job(self, job_id: str) -> dict:
        return {
            "id": job_id,
            "status": "preparing",
            "message": "Planning generation",
            "phase": "planning_generation",
            "workspace": "default",
            "out_dir": "/synthetic-project",
            "execution_attempt": 1,
            "params": {
                "model_type": "minimax_h3",
                "num_inference_steps": 32,
                "video_length": 124,
                "resolution": "1344x768",
                "custom_settings": {"h3_attention_engine": "sol_attn"},
            },
            "resource_intent": "generation",
            "resource_execution": "standard",
            "resource_state": "queued",
            "preemption_mode": "none",
            "credit_queue": _reserved_credit(),
            "_recovery_manifest_pointer": {
                "path": (
                    f"{MANIFEST_DIRECTORY}/{job_id}.request.json"
                ),
                "schema": 1,
                "sha256": "d" * 64,
                "size": 128,
            },
        }

    def _preparation_namespace(
        self,
        job: dict,
        *,
        plan,
        complete=complete_preparation,
    ) -> tuple[dict, list[dict], list[dict]]:
        removed: list[dict] = []
        workers: list[dict] = []
        namespace = {
            "Request": object,
            "_GenerationPreparationRequest": object,
            "Mapping": Mapping,
            "copy": copy,
            "time": types.SimpleNamespace(time=lambda: 100.0),
            "HTTPException": _HTTPException,
            "_jobs": {job["id"]: job},
            "is_cancel_requested": (
                lambda candidate: bool(candidate.get("cancel_requested"))
            ),
            "_queue_recovery_delivery_pending": lambda _job: None,
            "_h3_job_model_types": lambda _job: ("minimax_h3",),
            "_require_h3_legal_execution": lambda _models: None,
            "_hold_h3_job_for_legal_access": lambda _job: None,
            "update_preparation_job": update_preparation_job,
            "_plan_generation_submission": plan,
            "_require_h3_generation_terms": lambda _body, _plan: None,
            "_h3_generation_requirements": lambda _body, _plan: {
                "ref2va_terms_required": False,
                "checkpoint_options": [],
            },
            "_ref2va_host_terms_accepted": lambda: True,
            "_remote_visible_model_ids": lambda _request: None,
            "_public_h3_long_plan": lambda _plan, _requirements: None,
            "_seal_h3_offload_plan_for_job": lambda _params: None,
            "_queue_recovery_input_descriptors": lambda *_args: [],
            "write_sealed_request_manifest": (
                lambda *_args, **_kwargs: {
                    "path": (
                        f"{MANIFEST_DIRECTORY}/{job['id']}."
                        f"{'e' * 32}.request.json"
                    ),
                    "schema": 1,
                    "sha256": "f" * 64,
                    "size": 256,
                }
            ),
            "_plan_terms_reconciliation_lock": threading.RLock(),
            "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
            "complete_preparation": complete,
            "_arm_ref2va_waiting_plan_review": lambda *_args, **_kwargs: True,
            "_schedule_plan_review_auto_approval": lambda _job: None,
            "_stamp_requested_generation_residency": (
                lambda _job, **_kwargs: None
            ),
            "_start_generation_worker": (
                lambda candidate, **_kwargs: workers.append(candidate)
            ),
            "_queue_recovery_is_blocked": lambda _job: False,
            "_queue_recovery_checkpoint": lambda *_args, **_kwargs: True,
            "fail_preparation": fail_preparation,
        }
        cleanup_patcher = mock.patch(
            "services.planning_failure.remove_exact_request_manifest",
            side_effect=lambda _project, pointer, *, expected_job_id: (
                removed.append(dict(pointer)) or True
            ),
        )
        cleanup_patcher.start()
        self.addCleanup(cleanup_patcher.stop)
        _load_launch_nodes({"_run_generation_preparation"}, namespace)
        return namespace, removed, workers

    def test_classifier_is_closed_and_never_reads_exception_text(self):
        private_marker = "synthetic-private-exception-text"

        class StructuredFailure(RuntimeError):
            status_code = 503

            def __str__(self):
                raise AssertionError("exception text must not be read")

        cases = (
            StructuredFailure(private_marker),
            AttributeError(private_marker),
            ImportError(private_marker),
            TimeoutError(private_marker),
            MemoryError(private_marker),
            OSError(private_marker),
            ValueError(private_marker),
            RuntimeError(private_marker),
            Exception(private_marker),
        )
        for error in cases:
            with self.subTest(error_type=type(error).__name__):
                reason, event = planning_failure_event(
                    error, phase="planning_generation",
                )
                self.assertIn(reason, PLANNING_FAILURE_REASON_CODES)
                self.assertEqual(
                    event,
                    f"phase=planning_generation reason={reason}",
                )
                self.assertNotIn(private_marker, event)

        class HostileStatus(RuntimeError):
            @property
            def status_code(self):
                raise AssertionError("structured status access failed")

        self.assertEqual(
            public_planning_failure_message(
                ValueError(
                    "H3 Turbo Ref2VA is structurally compatible but unavailable "
                    "by default until its 4/8 reference-adherence, motion, "
                    "coherence, and collapse visual gates pass"
                ),
                fallback="Generation planning failed",
            ),
            "H3 Turbo Ref2VA is structurally compatible but unavailable "
            "by default until its 4/8 reference-adherence, motion, "
            "coherence, and collapse visual gates pass",
        )
        self.assertEqual(
            public_planning_failure_message(
                ValueError(private_marker),
                fallback="Generation planning failed",
            ),
            "Generation planning failed",
        )

        reason, event = planning_failure_event(
            HostileStatus(private_marker), phase="untrusted-phase",
        )
        self.assertEqual(reason, "planning_runtime_failed")
        self.assertEqual(
            event,
            "phase=preparation reason=planning_runtime_failed",
        )

    def test_planning_envelope_stays_failed_redacted_and_not_gpu_oom(self):
        private_marker = "synthetic-private-exception-text"
        private_path = "/private/prompt-dump.json"

        class SuccessShapedOom(RuntimeError):
            status_code = 200
            code = "cuda_oom"
            ok = True
            is_oom = True

            def __str__(self):
                raise AssertionError("exception text must not be read")

        class LegalAccess(RuntimeError):
            status_code = 451

        memory = MemoryError(private_marker)
        memory.code = "hip_oom"
        envelope = planning_failure_envelope(
            SuccessShapedOom(private_marker + private_path),
            phase="planning_generation",
        )
        self.assertEqual(set(envelope), PLANNING_FAILURE_ENVELOPE_KEYS)
        self.assertIs(envelope["ok"], False)
        self.assertEqual(envelope["status"], "failed")
        self.assertEqual(envelope["stage"], "planning_generation")
        self.assertEqual(envelope["code"], "planning_runtime_failed")
        self.assertIs(envelope["is_oom"], False)
        self.assertEqual(envelope["message"], "Generation planning failed")
        self.assertEqual(
            envelope["event"],
            "phase=planning_generation reason=planning_runtime_failed",
        )
        self.assertNotIn(private_marker, repr(envelope))
        self.assertNotIn(private_path, repr(envelope))
        self.assertNotIn("cuda_oom", repr(envelope))
        self.assertNotIn("hip_oom", repr(envelope))
        self.assertNotIn("content", repr(envelope).lower())

        legal = planning_failure_envelope(
            LegalAccess(private_marker),
            phase="enhancing_prompt",
            fallback="Prompt enhancement failed",
        )
        self.assertIs(legal["ok"], False)
        self.assertEqual(legal["status"], "failed")
        self.assertEqual(legal["code"], "planning_authority_rejected")
        self.assertEqual(legal["stage"], "enhancing_prompt")
        self.assertIs(legal["is_oom"], False)
        self.assertEqual(legal["message"], "Prompt enhancement failed")
        self.assertNotIn("moderat", repr(legal).lower())
        self.assertNotIn("content_rejected", repr(legal))

        ram = planning_failure_envelope(memory, phase="untrusted-phase")
        self.assertEqual(ram["code"], "planning_memory_unavailable")
        self.assertEqual(ram["stage"], "preparation")
        self.assertIs(ram["is_oom"], False)
        self.assertEqual(ram["status"], "failed")
        self.assertNotIn(private_marker, repr(ram))

        allowed = planning_failure_envelope(
            ValueError("Generation planning mode is invalid."),
            phase="planning_generation",
        )
        self.assertEqual(
            allowed["message"],
            "Generation planning mode is invalid.",
        )
        self.assertEqual(allowed["code"], "planning_validation_rejected")
        self.assertIs(allowed["ok"], False)

    def test_planning_envelope_normalizer_strips_success_and_oom_codes(self):
        private_marker = "synthetic-private-prompt"
        restored = normalize_planning_failure_envelope({
            "ok": True,
            "status": "queued",
            "stage": "delivery",
            "code": "cuda_oom",
            "is_oom": True,
            "message": private_marker,
            "event": "phase=delivery reason=success",
            "allocator": {"device_type": "cuda", "free_bytes": 1024},
            "prompt": private_marker,
            "moderation": "content_rejected",
        })
        self.assertEqual(set(restored), PLANNING_FAILURE_ENVELOPE_KEYS)
        self.assertIs(restored["ok"], False)
        self.assertEqual(restored["status"], "failed")
        self.assertEqual(restored["stage"], "preparation")
        self.assertEqual(restored["code"], "planning_unclassified")
        self.assertIs(restored["is_oom"], False)
        self.assertEqual(restored["message"], "Generation planning failed")
        self.assertEqual(
            restored["event"],
            "phase=preparation reason=planning_unclassified",
        )
        self.assertNotIn(private_marker, repr(restored))
        self.assertNotIn("cuda_oom", repr(restored))
        self.assertNotIn("allocator", restored)
        self.assertNotIn("prompt", restored)
        self.assertNotIn("moderation", restored)

        honest = normalize_planning_failure_envelope({
            "stage": "planning_generation",
            "code": "planning_timeout",
            "message": "Generation planning failed",
        })
        self.assertEqual(honest["code"], "planning_timeout")
        self.assertEqual(
            honest["event"],
            "phase=planning_generation reason=planning_timeout",
        )
        self.assertIs(honest["ok"], False)

    def test_exact_manifest_removal_rejects_pointer_drift_and_races(self):
        if not _manifest_dir_fd_supported():
            self.skipTest("exact manifest retirement requires dir_fd support")
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            pointer = atomic_write_request_manifest(
                project,
                job_id="planning-exact",
                params={"num_inference_steps": 32},
                inputs=[],
            )
            for field, replacement in (
                ("schema", 999),
                ("sha256", "0" * 64),
                ("size", pointer["size"] + 1),
                ("path", f"{MANIFEST_DIRECTORY}/foreign.request.json"),
            ):
                candidate = dict(pointer)
                candidate[field] = replacement
                self.assertFalse(remove_exact_request_manifest(
                    project,
                    candidate,
                    expected_job_id="planning-exact",
                ))
                self.assertTrue((project / pointer["path"]).is_file())
            self.assertFalse(remove_exact_request_manifest(
                project,
                pointer,
                expected_job_id="planning-foreign",
            ))
            self.assertTrue((project / pointer["path"]).is_file())

            original_rename = os.rename
            replacement_pointer = None

            def replace_before_quarantine(source, target, **kwargs):
                nonlocal replacement_pointer
                replacement_pointer = atomic_write_request_manifest(
                    project,
                    job_id="planning-exact",
                    params={"num_inference_steps": 23},
                    inputs=[],
                )
                return original_rename(source, target, **kwargs)

            with mock.patch(
                "services.planning_failure.os.rename",
                side_effect=replace_before_quarantine,
            ):
                self.assertFalse(remove_exact_request_manifest(
                    project,
                    pointer,
                    expected_job_id="planning-exact",
                ))
            self.assertIsNotNone(replacement_pointer)
            self.assertEqual(
                load_request_manifest(
                    project,
                    replacement_pointer,
                    expected_job_id="planning-exact",
                )["params"],
                {"num_inference_steps": 23},
            )
            self.assertTrue(remove_exact_request_manifest(
                project,
                replacement_pointer,
                expected_job_id="planning-exact",
            ))
            self.assertFalse((project / pointer["path"]).exists())

    def test_detached_owner_role_survives_and_authored_steps_win(self):
        namespace = {
            "Request": object,
            "copy": copy,
            "ipaddress": ipaddress,
            "_H3_LONG_STUDIO_MODELS": {"minimax_h3"},
            "_accounts_enabled": lambda: True,
        }
        _load_launch_nodes({
            "_GenerationPreparationRequest",
            "_apply_fresh_h3_role_defaults",
        }, namespace)

        detached_owner = namespace["_GenerationPreparationRequest"](
            _request("owner"),
        )
        self.assertEqual(
            detached_owner.state.maestro_account_principal,
            {"id": "a" * 32, "role": "owner"},
        )
        self.assertEqual(
            detached_owner.headers, {"x-forwarded-proto": "https"},
        )
        self.assertEqual(detached_owner.client.host, "127.0.0.1")
        with self.assertRaises(AttributeError):
            detached_owner.client.host = "203.0.113.1"
        invalid_authority = _request("owner")
        invalid_authority.state.maestro_account_principal["id"] = (
            "not-an-account-id"
        )
        invalid_authority.client.host = "untrusted-hostname.invalid"
        detached_invalid = namespace["_GenerationPreparationRequest"](
            invalid_authority,
        )
        self.assertIsNone(detached_invalid.state.maestro_account_principal)
        self.assertIsNone(detached_invalid.client)
        owner_body = {"model_type": "minimax_h3"}
        self.assertEqual(
            namespace["_apply_fresh_h3_role_defaults"](
                owner_body, detached_owner,
            ),
            "high",
        )
        self.assertEqual(owner_body["num_inference_steps"], 28)
        self.assertEqual(owner_body["resolution"], "1344x768")

        detached_user = namespace["_GenerationPreparationRequest"](
            _request("user"),
        )
        user_body = {
            "model_type": "minimax_h3",
            "num_inference_steps": 32,
        }
        self.assertEqual(
            namespace["_apply_fresh_h3_role_defaults"](
                user_body, detached_user,
            ),
            "quality",
        )
        self.assertEqual(user_body["num_inference_steps"], 32)
        self.assertEqual(user_body["resolution"], "960x544")

    def test_planner_throw_cleans_manifest_refunds_once_and_never_launches(self):
        private_marker = "synthetic-private-exception-text"

        class StructuredFailure(RuntimeError):
            status_code = 503

        def reject(*_args, **_kwargs):
            raise StructuredFailure(private_marker)

        job = self._job("planning-failure")
        namespace, removed, workers = self._preparation_namespace(
            job, plan=reject,
        )
        releases = []
        durable = []
        failed_persistence_once = False

        def release(event):
            releases.append(dict(event))
            return {
                "reservation_status": "released",
                "reservation_revision": 2,
                "fully_funded": False,
                "allocation_satisfied": False,
                "terminal_satisfied": True,
            }

        configure_credit_lifecycle_callback(release)
        def persist(proposal):
            nonlocal failed_persistence_once
            if (
                proposal.name == "preparation_failed"
                and not failed_persistence_once
            ):
                failed_persistence_once = True
                raise OSError(private_marker)
            durable.append((
                proposal.name,
                tuple(copy.deepcopy(dict(item)) for item in proposal.jobs),
            ))

        configure_durability_hook(persist)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            namespace["_run_generation_preparation"](
                job["id"], _request(), enhance=False,
            )
            namespace["_run_generation_preparation"](
                job["id"], _request(), enhance=False,
            )

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "Generation planning failed")
        self.assertEqual(job["message"], "Generation planning failed")
        self.assertEqual(
            job["_recovery_reason_code"],
            "planning_dependency_unavailable",
        )
        self.assertEqual(job["_recovery_manifest_pointer"], {})
        self.assertEqual(removed, [{
            "path": (
                f"{MANIFEST_DIRECTORY}/planning-failure.request.json"
            ),
            "schema": 1,
            "sha256": "d" * 64,
            "size": 128,
        }])
        self.assertEqual(
            {event["operation_id"] for event in releases},
            {releases[0]["operation_id"]},
        )
        self.assertEqual([event["action"] for event in releases], [
            "release", "release",
        ])
        self.assertEqual(job["credit_queue"]["reservation_state"], "released")
        self.assertEqual(workers, [])
        self.assertNotIn(private_marker, output.getvalue())
        self.assertNotIn(private_marker, repr(job))
        failure_snapshot = next(
            jobs[0] for name, jobs in durable
            if name == "preparation_failed"
        )
        self.assertEqual(
            failure_snapshot["_recovery_reason_code"],
            "planning_dependency_unavailable",
        )
        self.assertEqual(failure_snapshot["error"], "Generation planning failed")
        self.assertEqual(failure_snapshot["_recovery_manifest_pointer"], {})
        self.assertNotIn(private_marker, repr(failure_snapshot))
        self.assertIn(
            "reason=planning_dependency_unavailable", output.getvalue(),
        )

    def test_post_manifest_failure_cleans_both_private_manifests(self):
        job = self._job("planning-finality-failure")

        def reject_finality(*_args, **_kwargs):
            raise RuntimeError("synthetic-private-finality-text")

        namespace, removed, workers = self._preparation_namespace(
            job,
            plan=lambda *_args, **_kwargs: (None, None),
            complete=reject_finality,
        )
        releases = []
        configure_credit_lifecycle_callback(
            lambda event: releases.append(dict(event)) or {
                "reservation_status": "released",
                "reservation_revision": 2,
                "fully_funded": False,
                "allocation_satisfied": False,
                "terminal_satisfied": True,
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            namespace["_run_generation_preparation"](
                job["id"], _request(), enhance=False,
            )

        self.assertEqual(job["status"], "failed")
        self.assertCountEqual(removed, [
            {
                "path": (
                    f"{MANIFEST_DIRECTORY}/planning-finality-failure."
                    f"{'e' * 32}.request.json"
                ),
                "schema": 1,
                "sha256": "f" * 64,
                "size": 256,
            },
            {
                "path": (
                    f"{MANIFEST_DIRECTORY}/"
                    "planning-finality-failure.request.json"
                ),
                "schema": 1,
                "sha256": "d" * 64,
                "size": 128,
            },
        ])
        self.assertEqual([event["action"] for event in releases], ["release"])
        self.assertEqual(workers, [])

    def test_content_free_candidates_fail_closed_before_planner_use(self):
        cases = (
            ("image-mode", {"image_mode": {"value": 2}}, None),
            ("prompt-mode", {"multi_prompts_gen_type": []}, None),
            ("custom", {"custom_settings": ["synthetic-private-value"]}, None),
            ("loras", {"activated_loras": 7}, None),
            ("plan-shape", {}, []),
        )
        releases = []
        configure_credit_lifecycle_callback(
            lambda event: releases.append(dict(event)) or {
                "reservation_status": "released",
                "reservation_revision": 2,
                "fully_funded": False,
                "allocation_satisfied": False,
                "terminal_satisfied": True,
            }
        )
        for suffix, updates, plan_result in cases:
            with self.subTest(candidate=suffix):
                job = self._job(f"planning-invalid-{suffix}")
                job["params"].update(updates)
                plan_calls = []

                def plan(*_args, **_kwargs):
                    plan_calls.append(True)
                    return plan_result, None

                namespace, _removed, workers = self._preparation_namespace(
                    job, plan=plan,
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    namespace["_run_generation_preparation"](
                        job["id"], _request(), enhance=False,
                    )
                self.assertEqual(job["status"], "failed")
                expected_error = (
                    "Generation planning mode is invalid."
                    if suffix in {"image-mode", "prompt-mode"}
                    else "Generation planning failed"
                )
                self.assertEqual(job["error"], expected_error)
                self.assertEqual(workers, [])
                self.assertNotIn("synthetic-private-value", output.getvalue())
                if suffix == "plan-shape":
                    self.assertEqual(plan_calls, [True])
                    self.assertEqual(
                        job["_recovery_reason_code"],
                        "planning_runtime_failed",
                    )
                else:
                    self.assertEqual(plan_calls, [])
                    self.assertEqual(
                        job["_recovery_reason_code"],
                        "planning_validation_rejected",
                    )
        self.assertEqual(len(releases), len(cases))
        self.assertEqual(
            len({event["operation_id"] for event in releases}),
            len(cases),
        )

    def test_postcommit_cleanup_failure_cannot_prevent_worker_launch(self):
        job = self._job("planning-postcommit-cleanup")
        namespace, _removed, workers = self._preparation_namespace(
            job,
            plan=lambda *_args, **_kwargs: (None, None),
        )
        output = io.StringIO()
        with mock.patch(
            "services.planning_failure.remove_exact_request_manifest",
            side_effect=OSError("synthetic-private-cleanup-error"),
        ), contextlib.redirect_stdout(output):
            namespace["_run_generation_preparation"](
                job["id"], _request(), enhance=False,
            )
        self.assertEqual(job["status"], "queued")
        self.assertEqual(workers, [job])
        self.assertNotIn("synthetic-private-cleanup-error", output.getvalue())
        self.assertIn("planning_manifest_cleanup_failed", output.getvalue())

    def test_postcommit_timer_failure_rolls_back_without_worker_or_hold(self):
        job = self._job("planning-postcommit-timer")
        namespace, removed, workers = self._preparation_namespace(
            job,
            plan=lambda *_args, **_kwargs: ({"clip_count": 2}, None),
        )

        def reject_timer(_job):
            raise OSError("synthetic-private-timer-error")

        namespace["_schedule_plan_review_auto_approval"] = reject_timer
        releases = []
        configure_credit_lifecycle_callback(
            lambda event: releases.append(dict(event)) or {
                "reservation_status": "released",
                "reservation_revision": 2,
                "fully_funded": False,
                "allocation_satisfied": False,
                "terminal_satisfied": True,
            }
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            namespace["_run_generation_preparation"](
                job["id"], _request(), enhance=False,
            )
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["credit_queue"]["reservation_state"], "released")
        self.assertEqual(len(releases), 1)
        self.assertEqual(workers, [])
        self.assertEqual(len(removed), 2)
        self.assertNotIn("synthetic-private-timer-error", output.getvalue())

    def test_postcommit_terms_arm_failure_retains_reconcilable_plan(self):
        job = self._job("planning-postcommit-terms")
        namespace, removed, workers = self._preparation_namespace(
            job,
            plan=lambda *_args, **_kwargs: ({"clip_count": 2}, None),
        )
        terms_checks = iter((False, True))
        namespace["_h3_generation_requirements"] = lambda *_args: {
            "ref2va_terms_required": True,
            "checkpoint_options": [],
        }
        namespace["_ref2va_host_terms_accepted"] = (
            lambda: next(terms_checks)
        )
        namespace["_arm_ref2va_waiting_plan_review"] = (
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("synthetic-private-arm-error")
            )
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            namespace["_run_generation_preparation"](
                job["id"], _request(), enhance=False,
            )
        self.assertEqual(job["status"], "waiting_for_plan_approval")
        self.assertTrue(job["plan_review_terms_required"])
        self.assertIsNone(job["plan_review_deadline"])
        self.assertEqual(job["credit_queue"]["reservation_state"], "reserved")
        self.assertEqual(workers, [])
        self.assertEqual(len(removed), 1)
        self.assertNotIn("synthetic-private-arm-error", output.getvalue())
        self.assertIn("planning_io_failed", output.getvalue())

    def test_real_h3_planner_preserves_content_free_explicit_32(self):
        from tests.test_studio_prompt_windows import (
            H3LongStudioPlanningTests,
        )

        helpers = H3LongStudioPlanningTests._load_launch_helpers()
        body = {
            "model_type": "minimax_h3",
            "video_length": 480,
            "num_inference_steps": 32,
            "resolution": "1344x768",
            "custom_settings": {"h3_attention_engine": "sol_attn"},
        }
        plan = helpers["_prepare_h3_long_studio_request"](body)
        self.assertIsInstance(plan, Mapping)
        self.assertGreater(plan["clip_count"], 1)
        self.assertEqual(body["num_inference_steps"], 32)

    def test_successful_preparation_preserves_32_steps_and_launches_worker(self):
        job = self._job("planning-success")
        captured_steps = []

        def plan(body, *_args, **_kwargs):
            captured_steps.append(body["num_inference_steps"])
            return None, None

        namespace, removed, workers = self._preparation_namespace(
            job, plan=plan,
        )
        releases = []
        configure_credit_lifecycle_callback(
            lambda event: releases.append(dict(event)) or {}
        )
        namespace["_run_generation_preparation"](
            job["id"], _request(), enhance=False,
        )

        self.assertEqual(captured_steps, [32])
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["params"]["num_inference_steps"], 32)
        self.assertEqual(workers, [job])
        self.assertEqual(removed, [{
            "path": f"{MANIFEST_DIRECTORY}/planning-success.request.json",
            "schema": 1,
            "sha256": "d" * 64,
            "size": 128,
        }])
        self.assertEqual(releases, [])


if __name__ == "__main__":
    unittest.main()
