"""Model-free launch orchestration tests for hosted support credits."""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.credit_accounting import (
    CreditAccountingError,
    CreditAccountingJournal,
    CreditAccountingPolicy,
    CreditSourceBalance,
)
from services.credit_runtime import (
    CreditRuntimePolicy,
    quote_reservation,
    reserve_quote,
    transition_reservation,
)
from services.entitlements import (
    SUPPORT_PRIORITY_IDENTITY_CONTRACTS,
    EntitlementError,
    support_priority_capability_marker,
)
from services.job_lifecycle import (
    _credit_queue_metadata_from_quote,
    _reset_queue_state_for_tests,
    apply_credit_queue_decision,
    configure_credit_lifecycle_callback,
    configure_durability_hook,
    consume_credit_queue_reservation,
    finish_job,
    update_job,
)
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    atomic_write_request_manifest,
    discover_request_manifest_pointers,
    load_request_manifest,
    remove_request_manifest,
)

SOURCE = (APP / "launch.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(APP / "launch.py"))
ACCOUNT_PARAM = "_maestro_credit_account_id"
REALM_PARAM = "_maestro_credit_execution_realm"
BASELINE_PARAM = "_maestro_credit_accounting_baseline"
CLEANUP_PARAM = "_maestro_credit_accounting_cleanup"
ENFORCED = CreditRuntimePolicy(enforcement_enabled=True)
STANDARD = {
    "capability_id": "generation.standard",
    "support_priority_eligible": True,
    "marker": "standard_support_priority_policy",
}
ACCOUNTING_ACCOUNT = "key_" + "a" * 64
ACCOUNTING_SOURCE = "key_" + "b" * 64
ACCOUNTING_SECRET = b"synthetic-launch-accounting-secret"


def _functions(names: set[str], namespace: dict) -> dict:
    selected = []
    for node in TREE.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ):
            node = copy.deepcopy(node)
            node.decorator_list = []
            selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(  # noqa: S102 - compile selected launch functions without importing WGP.
        compile(module, "isolated-credit-launch", "exec"), namespace,
    )
    return namespace


def _allowance(units: int) -> dict:
    return {
        "state": "recorded_not_enforced",
        "enforcement_enabled": False,
        "unit": "compute_seconds",
        "as_of": "2026-08-11T10:00:00Z",
        "effective_allowance": units,
        "sources": [{
            "source": "recurring_support",
            "source_event_id": "event_credit_launch_0001",
            "granted_allowance": units,
            "effective_allowance": units,
            "expires_at": "2026-09-11T10:00:00Z",
            "status": "active" if units else "inactive",
            "refund_state": "none",
        }],
    }


def _quote(*, units: int, capability: dict = STANDARD):
    return quote_reservation(
        realm="hosted",
        requested_units=10,
        recorded_allowance=_allowance(units),
        capability_priority=capability,
        policy=ENFORCED,
    )


class CreditLaunchWiringTests(unittest.TestCase):
    def setUp(self):
        configure_durability_hook(None)
        configure_credit_lifecycle_callback(None)
        _reset_queue_state_for_tests()
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self):
        configure_credit_lifecycle_callback(None)
        configure_durability_hook(None)
        _reset_queue_state_for_tests()
        self.temporary.cleanup()

    @staticmethod
    def _namespace(quote):
        return {
            "CreditRuntimePolicy": CreditRuntimePolicy,
            "_CREDIT_ACCOUNT_PARAM": ACCOUNT_PARAM,
            "_CREDIT_REALM_PARAM": REALM_PARAM,
            "_CREDIT_BASELINE_PARAM": BASELINE_PARAM,
            "_CREDIT_CLEANUP_PARAM": CLEANUP_PARAM,
            "_CREDIT_EXEMPT_JOB_KINDS": frozenset({
                "tool_upscale", "tool_revoice",
            }),
            "_credit_admission_evaluations": {},
            "_credit_runtime_policy": lambda: ENFORCED,
            "_credit_server_execution_realm": lambda: "hosted",
            "_credit_account_id": lambda _job, persisted=True: "a" * 32,
            "_credit_evaluation": lambda _job: (quote, "a" * 64),
            "_job_model_term_ids": lambda job: [
                str((job.get("params") or {}).get("model_type") or "standard")
            ],
            "_credit_queue_metadata_from_quote": _credit_queue_metadata_from_quote,
            "_credit_transition_id": None,
            "_credit_reservation": None,
            "reserve_quote": reserve_quote,
            "transition_reservation": transition_reservation,
            "hashlib": hashlib,
            "json": json,
            "apply_credit_queue_decision": apply_credit_queue_decision,
            "configure_credit_lifecycle_callback": (
                configure_credit_lifecycle_callback
            ),
            "consume_credit_queue_reservation": (
                consume_credit_queue_reservation
            ),
            "update_job": update_job,
            "is_cancel_requested": lambda job: bool(job.get("cancel_requested")),
        }

    def _orchestrator(self, quote):
        namespace = self._namespace(quote)
        _functions({
            "_credit_accounting_reservation_id",
            "_credit_transition_id",
            "_credit_reservation",
            "_credit_job_exempt",
            "_credit_prepare_submission_manifest",
            "_credit_prepare_submission",
            "_credit_prepare_admission",
            "_credit_prepare_dispatch",
        }, namespace)
        return namespace

    def _real_accounting(self, *, units: int = 20):
        journal = CreditAccountingJournal(
            Path(self.temporary.name) / "credits.json",
            integrity_key=ACCOUNTING_SECRET,
            policy=CreditAccountingPolicy(enforcement_enabled=True),
        )
        recorded = _allowance(units)
        sources = (
            CreditSourceBalance(
                ACCOUNTING_SOURCE,
                units,
                "2026-09-11T10:00:00Z",
            ),
        )
        quote = _quote(units=units)
        jobs = {}
        namespace = self._namespace(quote)
        namespace.update({
            "CreditAccountingError": CreditAccountingError,
            "CreditLifecycleCallbackError": RuntimeError,
            "EntitlementError": EntitlementError,
            "CreditRuntimeError": RuntimeError,
            "CreditRuntimePolicy": CreditRuntimePolicy,
            "_CREDIT_LINEAGE_JOB_KINDS": frozenset({
                "director_pipeline",
                "director_preparation",
                "studio_generation_preparation",
                "studio_project_asset_preparation",
            }),
            "_credit_accounting_lock": threading.RLock(),
            "_credit_accounting_reservation_accounts": {},
            "_credit_accounting_journal": lambda: journal,
            "_credit_accounting_existing_journal": lambda: journal,
            "_credit_accounting_enabled": lambda: True,
            "_credit_accounting_account_key": lambda _job: ACCOUNTING_ACCOUNT,
            "_credit_accounting_context": lambda _job: (
                ACCOUNTING_ACCOUNT,
                recorded,
                sources,
                recorded["as_of"],
            ),
            "_credit_recorded_allowance": lambda _job: recorded,
            "_credit_allowance_revision": lambda _recorded: "a" * 64,
            "_credit_requested_units": lambda _job: 10,
            "_credit_capability_priority": lambda _job: STANDARD,
            "quote_reservation": quote_reservation,
            "_jobs": jobs,
        })
        _functions({
            "_credit_accounting_operation_id",
            "_credit_accounting_reservation_id",
            "_credit_accounting_receipt_projection",
            "_credit_accounting_lifecycle_callback",
            "_credit_accounting_reserve",
            "_credit_accounting_revalidate",
            "_credit_baseline_quote",
            "_credit_release_accounting",
            "_credit_transition_id",
            "_credit_reservation",
            "_credit_job_exempt",
            "_credit_job_lineage_only",
            "_credit_prepare_submission_manifest",
            "_credit_prepare_submission",
            "_credit_prepare_admission",
            "_credit_prepare_dispatch",
        }, namespace)
        return namespace, journal, jobs

    def test_disabled_local_and_lan_leave_legacy_submission_bytes_unchanged(self):
        for label, policy, realm in (
            ("disabled", CreditRuntimePolicy(), "hosted"),
            ("local", ENFORCED, "local"),
            ("lan", ENFORCED, "lan"),
        ):
            with self.subTest(label=label):
                namespace = self._namespace(_quote(units=20))
                namespace.update({
                    "_credit_runtime_policy": lambda policy=policy: policy,
                    "_credit_server_execution_realm": lambda realm=realm: realm,
                })
                _functions({
                    "_credit_accounting_reservation_id",
                    "_credit_job_exempt",
                    "_credit_prepare_submission_manifest",
                    "_credit_prepare_submission",
                }, namespace)
                job = {
                    "id": label,
                    "status": "queued",
                    "params": {"model_type": "standard"},
                }
                before = copy.deepcopy(job)
                self.assertFalse(namespace["_credit_prepare_submission"](job))
                self.assertEqual(job, before)

    def test_hosted_active_and_depleted_are_stamped_without_denial_or_hold(self):
        for units, expected_band, expected_state in (
            (20, 1, "reserved"),
            (0, -1, None),
        ):
            with self.subTest(units=units):
                namespace = self._orchestrator(_quote(units=units))
                job = {
                    "id": f"hosted-{units}",
                    "status": "queued",
                    "queue_held": False,
                    "params": {"model_type": "standard"},
                }
                self.assertTrue(namespace["_credit_prepare_submission"](job))
                self.assertEqual(job["credit_queue"]["queue_band"], expected_band)
                self.assertEqual(
                    job["credit_queue"]["reservation_state"], expected_state,
                )
                self.assertFalse(job["queue_held"])
                self.assertEqual(job["status"], "queued")
                self.assertEqual(job["params"][REALM_PARAM], "hosted")
                self.assertEqual(job["params"][ACCOUNT_PARAM], "a" * 32)

    def test_dispatch_consumes_once_and_replay_is_idempotent(self):
        namespace = self._orchestrator(_quote(units=20))
        job = {
            "id": "dispatch-once",
            "status": "queued",
            "params": {"model_type": "standard"},
        }
        namespace["_credit_prepare_submission"](job)
        self.assertTrue(namespace["_credit_prepare_admission"](job))
        job["status"] = "running"
        self.assertTrue(namespace["_credit_prepare_dispatch"](job))
        self.assertEqual(job["credit_queue"]["reservation_state"], "consumed")
        after = copy.deepcopy(job)
        self.assertFalse(namespace["_credit_prepare_dispatch"](job))
        self.assertEqual(job, after)
        self.assertEqual(len(job["credit_queue"]["transition_history"]), 3)

    def test_dispatch_consumes_the_exact_admission_observation(self):
        namespace = self._orchestrator(_quote(units=20))
        calls = []

        def evaluation(_job):
            calls.append(len(calls))
            allowance = _allowance(20)
            allowance["as_of"] = (
                f"2026-08-11T10:0{len(calls) - 1}:00Z"
            )
            quote = quote_reservation(
                realm="hosted",
                requested_units=10,
                recorded_allowance=allowance,
                capability_priority=STANDARD,
                policy=ENFORCED,
            )
            return quote, hashlib.sha256(
                f"observation-{len(calls)}".encode()
            ).hexdigest()

        namespace["_credit_evaluation"] = evaluation
        job = {
            "id": "exact-admission-observation",
            "status": "queued",
            "params": {"model_type": "standard"},
        }
        namespace["_credit_prepare_submission"](job)
        namespace["_credit_prepare_admission"](job)
        self.assertEqual(len(calls), 2)
        job["status"] = "running"
        self.assertTrue(namespace["_credit_prepare_dispatch"](job))
        self.assertEqual(len(calls), 2)
        self.assertEqual(job["credit_queue"]["reservation_state"], "consumed")

    def test_recovery_uses_current_local_realm_over_persisted_hosted_stamp(self):
        namespace = self._orchestrator(_quote(units=20))
        job = {
            "id": "hosted-to-local-recovery",
            "status": "queued",
            "params": {"model_type": "standard"},
        }
        namespace.update({
            "_credit_server_execution_realm": lambda: "hosted",
            "_credit_recorded_allowance": lambda _job: _allowance(20),
            "_credit_requested_units": lambda _job: 10,
            "_credit_capability_priority": lambda _job: STANDARD,
            "quote_reservation": quote_reservation,
        })
        _functions({
            "_credit_allowance_revision", "_credit_evaluation",
        }, namespace)
        namespace["_credit_prepare_submission"](job)
        self.assertEqual(job["credit_queue"]["queue_band"], 1)
        namespace["_credit_server_execution_realm"] = lambda: "local"
        self.assertTrue(namespace["_credit_prepare_admission"](job))
        self.assertEqual(job["credit_queue"]["realm"], "local")
        self.assertFalse(job["credit_queue"]["metering_applied"])
        self.assertEqual(job["credit_queue"]["queue_band"], 0)
        source = ast.get_source_segment(
            SOURCE,
            next(
                node for node in TREE.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_credit_evaluation"
            ),
        )
        self.assertIn("realm = _credit_server_execution_realm()", source)
        self.assertNotIn("params.get(_CREDIT_REALM_PARAM)", source)

    def test_cancellation_before_dispatch_never_consumes(self):
        namespace = self._orchestrator(_quote(units=20))
        job = {
            "id": "cancel-before-consume",
            "status": "queued",
            "params": {"model_type": "standard"},
        }
        namespace["_credit_prepare_submission"](job)
        namespace["_credit_prepare_admission"](job)
        job.update(status="running", cancel_requested=True)
        before = copy.deepcopy(job["credit_queue"])
        self.assertFalse(namespace["_credit_prepare_dispatch"](job))
        self.assertEqual(job["credit_queue"], before)

    def test_generation_early_admission_exits_discard_credit_snapshot(self):
        class RuntimeAdmissionError(Exception):
            pass

        class ModelAdmissionError(Exception):
            def __init__(self, detail):
                super().__init__(detail)
                self.detail = detail

        @contextlib.contextmanager
        def acquired_slot(*_args, **_kwargs):
            yield True

        def assert_case(label):
            job = {"id": label, "status": "queued", "params": {}}
            evaluations = {}
            dispatched = []
            finished = []

            def prepare_admission(_job):
                evaluations[label] = object()

            def require_parity(_job):
                if label == "h3-parity":
                    raise RuntimeAdmissionError("sealed plan changed")

            def require_model_admission(_job):
                if label == "model-admission":
                    raise ModelAdmissionError("terms changed")

            namespace = {
                "time": time,
                "_jobs": {label: job},
                "_credit_prepare_admission": prepare_admission,
                "CreditRuntimeError": RuntimeAdmissionError,
                "EntitlementError": RuntimeAdmissionError,
                "_credit_block_runtime_error": lambda _job: None,
                "_stamp_requested_generation_residency": (
                    lambda *_args, **_kwargs: None
                ),
                "generation_slot": acquired_slot,
                "_gen_lock": object(),
                "try_start": lambda *_args, **_kwargs: (
                    label != "start-refused"
                ),
                "_credit_admission_evaluations": evaluations,
                "_require_h3_offload_plan_parity": require_parity,
                "QueueRecoveryRuntimeError": RuntimeAdmissionError,
                "finish_job": lambda *_args, **_kwargs: finished.append(label),
                "_queue_recovery_delivery_pending": lambda _job: None,
                "_require_job_runtime_model_admission": require_model_admission,
                "HTTPException": ModelAdmissionError,
                "_director_image_role_wire_mode": lambda _params: "none",
                "_credit_prepare_dispatch": lambda _job: dispatched.append(
                    label
                ),
                "_restore_base_coefficient": lambda: None,
                "_active_gen_states": {"fixture": object()},
            }
            _functions({"_run_generation"}, namespace)
            self.assertFalse(namespace["_run_generation"](label))
            self.assertNotIn(label, evaluations)
            self.assertEqual(dispatched, [])
            self.assertEqual(
                bool(finished), label in {"h3-parity", "model-admission"},
            )

        for label in ("start-refused", "h3-parity", "model-admission"):
            with self.subTest(label=label):
                assert_case(label)

    def test_production_runtime_policy_is_hard_off(self):
        namespace = {
            "CreditRuntimePolicy": CreditRuntimePolicy,
            "_CREDIT_RUNTIME_ACCOUNTING_DURABLE": False,
            "_CREDIT_RUNTIME_VALIDATED_POLICY_UNITS": 0,
            "_accounts_enabled": lambda: True,
            "_env_flag_enabled": lambda _name: True,
        }
        _functions({"_credit_runtime_policy"}, namespace)
        self.assertFalse(
            namespace["_credit_runtime_policy"]().enforcement_enabled
        )

    def test_hard_off_accounting_never_creates_an_artifact_or_callback(self):
        callbacks = []
        namespace = {
            "_credit_accounting_enabled": lambda: False,
            "_credit_accounting_value": None,
            "_credit_accounting_lock": threading.RLock(),
            "configure_credit_lifecycle_callback": callbacks.append,
        }
        _functions({"_credit_accounting_journal"}, namespace)
        self.assertIsNone(namespace["_credit_accounting_journal"]())
        self.assertEqual(callbacks, [None])
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    def test_private_lineage_precedence_and_recovery_hydration(self):
        parent = {"params": {ACCOUNT_PARAM: "b" * 32}}
        jobs = {"parent": parent}
        namespace = {
            "_jobs": jobs,
            "_request_account_id": types.SimpleNamespace(get=lambda: "e" * 32),
            "_CREDIT_ACCOUNT_PARAM": ACCOUNT_PARAM,
            "re": re,
            "_credit_accounting_lock": threading.RLock(),
            "_credit_accounting_reservation_accounts": {},
        }
        _functions({"_credit_account_id"}, namespace)
        child = {
            "id": "private-lineage",
            "parent_job_id": "parent",
            "params": {ACCOUNT_PARAM: "c" * 32},
            "_credit_account_id": "d" * 32,
        }
        self.assertEqual(namespace["_credit_account_id"](child), "d" * 32)
        child.pop("_credit_account_id")
        self.assertEqual(namespace["_credit_account_id"](child), "b" * 32)
        jobs.clear()
        self.assertEqual(namespace["_credit_account_id"](child), "c" * 32)
        namespace["_credit_accounting_account_key"] = lambda job: (
            "key_" + namespace["_credit_account_id"](job)
        )
        _functions({"_credit_accounting_hydrate_job"}, namespace)
        child["credit_queue"] = {
            "schema_version": 2,
            "accounting_reservation_id": "reservation_" + "f" * 32,
        }
        self.assertTrue(namespace["_credit_accounting_hydrate_job"](child))
        self.assertEqual(
            namespace["_credit_accounting_reservation_accounts"],
            {"reservation_" + "f" * 32: "key_" + "c" * 32},
        )

    def test_real_journal_reserve_revalidate_consume_and_early_release(self):
        namespace, journal, jobs = self._real_accounting(units=30)
        configure_credit_lifecycle_callback(
            namespace["_credit_accounting_lifecycle_callback"],
        )
        consumed = {
            "id": "real-consume",
            "kind": "studio_generation",
            "status": "queued",
            "workspace": "default",
            "created_at": 1.0,
            "params": {"model_type": "standard"},
        }
        jobs[consumed["id"]] = consumed
        self.assertTrue(namespace["_credit_prepare_submission"](consumed))
        self.assertEqual(consumed["credit_queue"]["schema_version"], 2)
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 10)
        self.assertTrue(namespace["_credit_prepare_admission"](consumed))
        consumed["status"] = "running"
        self.assertTrue(namespace["_credit_prepare_dispatch"](consumed))
        self.assertEqual(
            consumed["credit_queue"]["reservation_state"], "consumed",
        )
        projection = journal.public_projection(ACCOUNTING_ACCOUNT)
        self.assertEqual(projection["reserved_units"], 0)
        self.assertEqual(projection["consumed_units"], 10)
        self.assertFalse(namespace["_credit_release_accounting"](
            consumed, persist_baseline=False,
        ))

        released = {
            "id": "real-release",
            "kind": "studio_generation",
            "status": "queued",
            "workspace": "default",
            "created_at": 2.0,
            "params": {"model_type": "standard"},
        }
        jobs[released["id"]] = released
        self.assertTrue(namespace["_credit_prepare_submission"](released))
        self.assertTrue(namespace["_credit_release_accounting"](
            released, persist_baseline=False,
        ))
        self.assertNotIn("credit_queue", released)
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 0)

    def test_rollback_retry_uses_a_fresh_reservation_identity(self):
        namespace, journal, jobs = self._real_accounting(units=20)
        job = {
            "id": "rollback-retry",
            "kind": "studio_generation",
            "status": "queued",
            "workspace": "default",
            "created_at": 6.0,
            "params": {"model_type": "standard"},
        }
        jobs[job["id"]] = job
        self.assertTrue(namespace["_credit_prepare_submission"](job))
        first_id = job["credit_queue"]["accounting_reservation_id"]
        self.assertTrue(namespace["_credit_release_accounting"](
            job, persist_baseline=False,
        ))
        self.assertTrue(namespace["_credit_prepare_submission"](job))
        second_id = job["credit_queue"]["accounting_reservation_id"]
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 10)

    def test_partial_reserve_degrades_to_schema_v1_baseline(self):
        namespace, journal, jobs = self._real_accounting(units=15)
        first = {
            "id": "partial-first", "kind": "studio_generation",
            "status": "queued", "workspace": "default",
            "created_at": 7.0, "params": {"model_type": "standard"},
        }
        second = {
            "id": "partial-second", "kind": "studio_generation",
            "status": "queued", "workspace": "default",
            "created_at": 8.0, "params": {"model_type": "standard"},
        }
        jobs.update({first["id"]: first, second["id"]: second})
        self.assertTrue(namespace["_credit_prepare_submission"](first))
        self.assertTrue(namespace["_credit_prepare_submission"](second))
        self.assertEqual(second["credit_queue"]["schema_version"], 1)
        self.assertEqual(second["credit_queue"]["queue_band"], 0)
        self.assertIsNone(second["credit_queue"]["reservation_state"])
        self.assertTrue(namespace["_credit_prepare_admission"](second))
        self.assertEqual(second["credit_queue"]["queue_band"], 0)
        self.assertIsNone(second["credit_queue"]["reservation_state"])
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 10)

    def test_dispatch_journal_outage_downgrades_running_job_then_finishes(self):
        namespace, journal, jobs = self._real_accounting(units=20)
        job = {
            "id": "dispatch-outage", "kind": "studio_generation",
            "status": "queued", "workspace": "default",
            "created_at": 9.0, "params": {"model_type": "standard"},
        }
        jobs[job["id"]] = job
        self.assertTrue(namespace["_credit_prepare_submission"](job))
        self.assertTrue(namespace["_credit_prepare_admission"](job))
        job["status"] = "running"
        configure_credit_lifecycle_callback(
            lambda _event: (_ for _ in ()).throw(
                CreditAccountingError("offline")
            ),
        )
        self.assertFalse(namespace["_credit_prepare_dispatch"](job))
        self.assertEqual(job["credit_queue"]["schema_version"], 1)
        self.assertEqual(job["credit_queue"]["queue_band"], 0)
        self.assertTrue(finish_job(job, "failed"))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 0)

    def test_true_dispatch_journal_outage_retains_retryable_cleanup_link(self):
        namespace, journal, jobs = self._real_accounting(units=20)
        job = {
            "id": "dispatch-store-outage", "kind": "studio_generation",
            "status": "queued", "workspace": "default",
            "created_at": 10.0, "params": {"model_type": "standard"},
        }
        jobs[job["id"]] = job
        self.assertTrue(namespace["_credit_prepare_submission"](job))
        self.assertTrue(namespace["_credit_prepare_admission"](job))
        job["status"] = "running"

        class UnavailableJournal:
            def release(self, **_kwargs):
                raise OSError("offline")

        configure_credit_lifecycle_callback(
            lambda _event: (_ for _ in ()).throw(OSError("offline")),
        )
        namespace["_credit_accounting_existing_journal"] = (
            lambda: UnavailableJournal()
        )
        self.assertFalse(namespace["_credit_prepare_dispatch"](job))
        self.assertEqual(job["credit_queue"]["schema_version"], 2)
        reservation_id = job["credit_queue"]["accounting_reservation_id"]
        self.assertIn(
            reservation_id,
            namespace["_credit_accounting_reservation_accounts"],
        )
        self.assertTrue(finish_job(job, "failed"))
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 10)
        namespace["_credit_accounting_existing_journal"] = lambda: journal
        self.assertTrue(namespace["_credit_release_accounting"](
            job, persist_baseline=True,
        ))
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 0)

    def test_admission_journal_outage_runs_at_baseline_and_retries_cleanup(self):
        namespace, journal, jobs = self._real_accounting(units=20)
        job = {
            "id": "admission-store-outage", "kind": "studio_generation",
            "status": "queued", "workspace": "default",
            "created_at": 11.0, "params": {"model_type": "standard"},
        }
        jobs[job["id"]] = job
        self.assertTrue(namespace["_credit_prepare_submission"](job))

        class UnavailableJournal:
            def release(self, **_kwargs):
                raise OSError("offline")

        namespace["_credit_accounting_revalidate"] = (
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline"))
        )
        namespace["_credit_accounting_existing_journal"] = (
            lambda: UnavailableJournal()
        )
        self.assertTrue(namespace["_credit_prepare_admission"](job))
        self.assertEqual(job["credit_queue"]["schema_version"], 1)
        self.assertEqual(job["credit_queue"]["queue_band"], 0)
        self.assertIsNone(job["credit_queue"]["reservation_state"])
        self.assertIn(CLEANUP_PARAM, job["params"])
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 10)

        job["status"] = "running"
        self.assertTrue(finish_job(job, "failed"))
        namespace["_credit_accounting_existing_journal"] = lambda: journal
        self.assertTrue(namespace["_credit_release_accounting"](
            job, persist_baseline=True,
        ))
        self.assertNotIn(CLEANUP_PARAM, job["params"])
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 0)

    def test_current_local_realm_releases_and_downgrades_v2_to_baseline(self):
        namespace, journal, jobs = self._real_accounting(units=20)
        realm = ["hosted"]
        namespace["_credit_server_execution_realm"] = lambda: realm[0]
        namespace["_credit_evaluation"] = lambda job: (
            quote_reservation(
                realm=realm[0],
                requested_units=10,
                recorded_allowance=_allowance(20),
                capability_priority=STANDARD,
                policy=ENFORCED,
            ),
            "a" * 64,
        )
        job = {
            "id": "realm-change",
            "kind": "studio_generation",
            "status": "queued",
            "workspace": "default",
            "created_at": 3.0,
            "params": {"model_type": "standard"},
        }
        jobs[job["id"]] = job
        self.assertTrue(namespace["_credit_prepare_submission"](job))
        realm[0] = "local"
        namespace["_credit_accounting_enabled"] = lambda: False
        self.assertTrue(namespace["_credit_prepare_admission"](job))
        self.assertEqual(job["credit_queue"]["schema_version"], 1)
        self.assertEqual(job["credit_queue"]["realm"], "local")
        self.assertEqual(job["credit_queue"]["queue_band"], 0)
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 0)

    def test_reference_and_director_parents_carry_lineage_children_reserve(self):
        namespace, journal, jobs = self._real_accounting(units=30)
        for parent_kind, child_kind in (
            ("studio_project_asset_preparation", "studio_generation"),
            ("director_pipeline", "director_child"),
        ):
            with self.subTest(parent_kind=parent_kind):
                parent = {
                    "id": parent_kind,
                    "kind": parent_kind,
                    "status": "queued",
                    "workspace": "default",
                    "created_at": 4.0,
                    "params": {"model_type": "standard"},
                }
                jobs[parent["id"]] = parent
                self.assertTrue(
                    namespace["_credit_prepare_submission"](parent)
                )
                self.assertEqual(parent["params"][ACCOUNT_PARAM], "a" * 32)
                self.assertNotIn("credit_queue", parent)
                child = {
                    "id": parent_kind + "-child",
                    "parent_job_id": parent["id"],
                    "kind": child_kind,
                    "status": "queued",
                    "workspace": "default",
                    "created_at": 5.0,
                    "params": {"model_type": "standard"},
                }
                jobs[child["id"]] = child
                self.assertTrue(
                    namespace["_credit_prepare_submission"](child)
                )
                self.assertEqual(child["credit_queue"]["schema_version"], 2)
                self.assertTrue(namespace["_credit_release_accounting"](
                    child, persist_baseline=False,
                ))
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "pending_reservations"
        ], 0)

    def test_registration_failure_path_compensates_reserved_credit(self):
        function = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_queue_recovery_register_and_publish"
        )
        source = ast.get_source_segment(SOURCE, function)
        failure = source[source.index("except Exception:"):]
        self.assertIn(
            'globals().get("_credit_release_accounting")', failure,
        )
        self.assertIn("released = bool(credit_release(", failure)
        self.assertLess(
            failure.index("released = bool(credit_release("),
            failure.index("remove_request_manifest("),
        )
        self.assertIn("request_manifest is not None and released", failure)
        self.assertLess(
            source.index("credit_manifest_preflight(prepared)"),
            source.index("atomic_write_request_manifest("),
        )
        self.assertLess(
            source.index("atomic_write_request_manifest("),
            source.rindex("_credit_prepare_submission(prepared)"),
        )
        self.assertLess(
            source.rindex("_credit_prepare_submission(prepared)"),
            source.index("register_job("),
        )

    def test_manifest_preflight_creates_no_journal_hold(self):
        namespace, journal, jobs = self._real_accounting(units=20)
        job = {
            "id": "manifest-write-failure", "kind": "studio_generation",
            "status": "queued", "workspace": "default",
            "created_at": 13.0, "params": {"model_type": "standard"},
        }
        jobs[job["id"]] = job
        self.assertTrue(
            namespace["_credit_prepare_submission_manifest"](job)
        )
        self.assertIn(CLEANUP_PARAM, job["params"])
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 0)

    def test_unregistered_manifest_retains_and_retries_cleanup_obligation(self):
        namespace, journal, jobs = self._real_accounting(units=20)
        namespace.update({
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            "discover_request_manifest_pointers": (
                discover_request_manifest_pointers
            ),
            "load_request_manifest": load_request_manifest,
            "remove_request_manifest": remove_request_manifest,
        })
        _functions({
            "_credit_reconcile_unregistered_cleanup_manifests",
        }, namespace)
        job = {
            "id": "unregistered-cleanup", "kind": "studio_generation",
            "status": "queued", "workspace": "default",
            "created_at": 12.0, "params": {"model_type": "standard"},
        }
        jobs[job["id"]] = job
        self.assertTrue(namespace["_credit_prepare_submission"](job))
        pointer = atomic_write_request_manifest(
            self.temporary.name,
            job_id=job["id"],
            params=job["params"],
            inputs=(),
        )

        class UnavailableJournal:
            def release(self, **_kwargs):
                raise OSError("offline")

        namespace["_credit_accounting_existing_journal"] = (
            lambda: UnavailableJournal()
        )
        retained, blocked = namespace[
            "_credit_reconcile_unregistered_cleanup_manifests"
        ]({"default": (self.temporary.name, "project")}, ())
        self.assertEqual(blocked, set())
        self.assertEqual(retained, {"default": [pointer["path"]]})
        self.assertEqual(len(discover_request_manifest_pointers(
            self.temporary.name,
        )), 1)
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 10)

        namespace["_credit_accounting_existing_journal"] = lambda: journal
        retained, blocked = namespace[
            "_credit_reconcile_unregistered_cleanup_manifests"
        ]({"default": (self.temporary.name, "project")}, ())
        self.assertEqual((retained, blocked), ({}, set()))
        self.assertEqual(discover_request_manifest_pointers(
            self.temporary.name,
        ), [])
        self.assertEqual(journal.public_projection(ACCOUNTING_ACCOUNT)[
            "reserved_units"
        ], 0)

    def test_malformed_existing_server_stamp_has_no_admission_side_effects(self):
        namespace = self._orchestrator(_quote(units=20))
        job = {
            "id": "malformed-stamp",
            "status": "queued",
            "params": {
                "model_type": "standard",
                REALM_PARAM: "hosted",
            },
            "credit_queue": {"schema_version": 1, "queue_band": 1},
        }
        before = copy.deepcopy(job)
        with self.assertRaises(ValueError):
            namespace["_credit_prepare_admission"](job)
        self.assertEqual(job, before)

    def test_credit_runtime_error_durably_unblocks_queued_or_running_job(self):
        blocked = []
        failed = []
        namespace = {
            "_credit_admission_evaluations": {},
            "block_resource_admission_failure": (
                lambda job: blocked.append(job["id"]) or True
            ),
            "finish_job": lambda job, status, **updates: (
                failed.append((job["id"], status, updates)) or True
            ),
        }
        _functions({"_credit_block_runtime_error"}, namespace)
        namespace["_credit_block_runtime_error"]({
            "id": "queued-bad-stamp", "status": "queued",
        })
        namespace["_credit_block_runtime_error"]({
            "id": "running-bad-stamp", "status": "running",
        })
        self.assertEqual(blocked, ["queued-bad-stamp"])
        self.assertEqual(failed[0][0:2], ("running-bad-stamp", "failed"))
        self.assertEqual(failed[0][2]["recovery_state"], "terminal")

    def test_allowance_revision_is_content_free_and_state_sensitive(self):
        namespace = {"hashlib": hashlib, "json": json}
        _functions({"_credit_allowance_revision"}, namespace)
        original = _allowance(20)
        unrelated_identity = copy.deepcopy(original)
        unrelated_identity["sources"][0]["source_event_id"] = (
            "different_private_event"
        )
        changed_state = copy.deepcopy(original)
        changed_state["sources"][0]["effective_allowance"] = 19
        changed_state["effective_allowance"] = 19
        revision = namespace["_credit_allowance_revision"](original)
        self.assertRegex(revision, r"^[0-9a-f]{64}$")
        self.assertEqual(
            revision,
            namespace["_credit_allowance_revision"](unrelated_identity),
        )
        self.assertNotEqual(
            revision,
            namespace["_credit_allowance_revision"](changed_state),
        )

    def test_exact_moody_relation_neutralizes_mixed_h3_boost_only(self):
        class Wgp:
            def __init__(self):
                self.models_def = {
                    "ordinary": {},
                    "moody_alias": {"URLs": "krea2_moody_mix_v7_fp8"},
                    "moody_named_only": {},
                    "krea2_moody_mix_v7_fp8": {},
                }

        namespace = {
            "SUPPORT_PRIORITY_IDENTITY_CONTRACTS": (
                SUPPORT_PRIORITY_IDENTITY_CONTRACTS
            ),
            "support_priority_capability_marker": (
                support_priority_capability_marker
            ),
            "_job_model_term_ids": lambda job: job["models"],
            "wgp": Wgp(),
            "hashlib": hashlib,
            "json": json,
        }
        _functions({"_credit_capability_priority"}, namespace)
        mixed = namespace["_credit_capability_priority"]({
            "models": ["ordinary", "moody_alias"],
        })
        named = namespace["_credit_capability_priority"]({
            "models": ["moody_named_only"],
        })
        self.assertFalse(mixed["support_priority_eligible"])
        self.assertEqual(
            mixed["capability_id"], "krea2_moody_mix_v7_fp8",
        )
        self.assertTrue(named["support_priority_eligible"])
        excluded = _quote(units=20, capability=mixed)
        self.assertTrue(excluded.submission_allowed)
        self.assertEqual(excluded.decision, "capability_excluded")

    def test_h3_effective_segment_models_feed_capability_reduction(self):
        namespace = {}
        _functions({"_job_model_term_ids"}, namespace)
        job = {
            "model_type": "minimax_h3",
            "params": {
                "model_type": "minimax_h3",
                "_h3_longform": {"segment_models": [
                    {"model_type": "ordinary"},
                    {"model_type": "krea2_moody_cutie_v4_fp8"},
                ]},
            },
        }
        self.assertEqual(namespace["_job_model_term_ids"](job), [
            "minimax_h3", "ordinary", "krea2_moody_cutie_v4_fp8",
        ])

    def test_h3_physical_model_change_is_requoted_before_dispatch(self):
        excluded = _quote(
            units=20,
            capability=support_priority_capability_marker(
                "krea2_moody_cutie_v4_fp8"
            ),
        )
        namespace = self._namespace(_quote(units=20))

        def models(job):
            params = job.get("params") or {}
            result = [str(params.get("model_type") or "standard")]
            plan = params.get("_h3_longform") or {}
            result.extend(
                str(item.get("model_type") or "")
                for item in plan.get("segment_models") or []
                if isinstance(item, dict)
            )
            return [item for item in result if item]

        namespace["_job_model_term_ids"] = models
        namespace["_credit_evaluation"] = lambda job: (
            (
                excluded
                if "krea2_moody_cutie_v4_fp8" in models(job)
                else _quote(units=20)
            ),
            "a" * 64,
        )
        _functions({
            "_credit_accounting_reservation_id",
            "_credit_transition_id",
            "_credit_reservation",
            "_credit_job_exempt",
            "_credit_prepare_submission_manifest",
            "_credit_prepare_submission",
            "_credit_prepare_admission",
            "_credit_prepare_dispatch",
        }, namespace)
        job = {
            "id": "h3-model-change",
            "status": "queued",
            "params": {"model_type": "minimax_h3"},
        }
        namespace["_credit_prepare_submission"](job)
        self.assertEqual(job["credit_queue"]["queue_band"], 1)
        job["params"]["_h3_longform"] = {"segment_models": [
            {"model_type": "minimax_h3_ref2va"},
            {"model_type": "krea2_moody_cutie_v4_fp8"},
        ]}
        self.assertTrue(namespace["_credit_prepare_admission"](job))
        self.assertEqual(job["credit_queue"]["decision"], "capability_excluded")
        self.assertEqual(job["credit_queue"]["queue_band"], 0)
        self.assertIsNone(job["credit_queue"]["reservation_state"])

    def test_director_child_inherits_server_account_without_double_submit(self):
        from services import director_pipeline as director

        original = director._pipelines
        director._pipelines = {
            "director-a": {"params": {
                ACCOUNT_PARAM: "b" * 32,
                REALM_PARAM: "hosted",
            }},
        }
        captured = []
        jobs = {}

        def register(job, **_kwargs):
            captured.append(copy.deepcopy(job))
            jobs[job["id"]] = job
            return types.SimpleNamespace()

        namespace = {
            "re": re,
            "os": os,
            "hmac": __import__("hmac"),
            "threading": __import__("threading"),
            "_CREDIT_ACCOUNT_PARAM": ACCOUNT_PARAM,
            "_CREDIT_REALM_PARAM": REALM_PARAM,
            "_credit_runtime_policy": lambda: ENFORCED,
            "_credit_server_execution_realm": lambda: "hosted",
            "_jobs": jobs,
            "_queue_recovery_register_and_publish": register,
            "_run_generation": lambda _job_id: None,
            "QueueRecoveryRuntimeError": RuntimeError,
        }
        try:
            _functions({"_director_recovery_submit_child"}, namespace)
            child = {
                "id": "director-child-a",
                "status": "queued",
                "params": {"_director_pipeline_id": "director-a"},
                "workspace": "default",
                "out_dir": "/tmp/project",
            }
            result = namespace["_director_recovery_submit_child"](
                child,
                "director-a",
                {"kind": "clip", "variant": 0, "index": 1},
                0,
            )
            self.assertIs(result, child)
            self.assertEqual(captured[0]["_credit_account_id"], "b" * 32)
            again = namespace["_director_recovery_submit_child"](
                dict(child),
                "director-a",
                {"kind": "clip", "variant": 0, "index": 1},
                0,
            )
            self.assertIs(again, child)
            self.assertEqual(len(captured), 1)
        finally:
            director._pipelines = original

    def test_director_lineage_uses_the_same_hosted_policy_gate(self):
        for label, policy, realm in (
            ("disabled", CreditRuntimePolicy(), "hosted"),
            ("local", ENFORCED, "local"),
            ("lan", ENFORCED, "lan"),
            ("hosted", ENFORCED, "hosted"),
        ):
            with self.subTest(label=label):
                namespace = {
                    "_CREDIT_ACCOUNT_PARAM": ACCOUNT_PARAM,
                    "_CREDIT_REALM_PARAM": REALM_PARAM,
                    "_credit_runtime_policy": lambda policy=policy: policy,
                    "_credit_server_execution_realm": lambda realm=realm: realm,
                    "_request_account_id": types.SimpleNamespace(
                        get=lambda: "c" * 32
                    ),
                    "re": re,
                }
                _functions({"_credit_prepare_director_pipeline"}, namespace)
                params = {"video_model": "standard"}
                before = copy.deepcopy(params)
                changed = namespace["_credit_prepare_director_pipeline"](params)
                if label == "hosted":
                    self.assertTrue(changed)
                    self.assertEqual(params[ACCOUNT_PARAM], "c" * 32)
                    self.assertEqual(params[REALM_PARAM], "hosted")
                else:
                    self.assertFalse(changed)
                    self.assertEqual(params, before)

    def test_director_public_state_does_not_expose_credit_lineage(self):
        namespace = {
            "_CREDIT_INTERNAL_PARAMS": frozenset({ACCOUNT_PARAM, REALM_PARAM}),
            "_strip_director_image_role_internals": lambda _snapshot: None,
            "_redact_local_paths": lambda state: state,
            "_sanitize_director_public_failures": lambda state: state,
        }
        _functions({"_public_pipeline_state"}, namespace)
        public = namespace["_public_pipeline_state"]({
            "llm_log": {"private": "content"},
            "_params_snapshot": {
                ACCOUNT_PARAM: "b" * 32,
                REALM_PARAM: "hosted",
                "video_model": "standard",
            },
        })
        self.assertIsNone(public["llm_log"])
        self.assertEqual(
            public["_params_snapshot"], {"video_model": "standard"},
        )

    def test_requested_units_exclude_ordinary_model_load(self):
        namespace = {"math": math}
        _functions({"_credit_requested_units"}, namespace)
        self.assertEqual(namespace["_credit_requested_units"]({
            "h3_estimate": {"seconds": 125.2, "model_load_seconds": 25.2},
            "params": {},
        }), 100)

    def test_standalone_gpu_tools_are_explicitly_exempt(self):
        namespace = {
            "_CREDIT_EXEMPT_JOB_KINDS": frozenset({
                "tool_upscale", "tool_revoice",
            }),
        }
        _functions({"_credit_job_exempt"}, namespace)
        self.assertTrue(namespace["_credit_job_exempt"]({
            "kind": "tool_upscale",
        }))
        self.assertTrue(namespace["_credit_job_exempt"]({
            "kind": "tool_revoice",
        }))
        self.assertFalse(namespace["_credit_job_exempt"]({
            "kind": "studio_generation",
        }))
        for function_name, kind in (
            ("tools_upscale", "tool_upscale"),
            ("tools_revoice", "tool_revoice"),
        ):
            function = next(
                node for node in TREE.body
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == function_name
            )
            source = ast.get_source_segment(SOURCE, function)
            self.assertIn(f'"kind": "{kind}"', source)

    def test_revalidation_and_consumption_straddle_runtime_admission(self):
        function = next(
            node for node in TREE.body
            if isinstance(node, ast.FunctionDef) and node.name == "_run_generation"
        )
        source = ast.get_source_segment(SOURCE, function)
        self.assertLess(
            source.index("_credit_prepare_admission(job)"),
            source.index("with generation_slot("),
        )
        self.assertLess(
            source.index("_require_job_runtime_model_admission(job)"),
            source.index("_credit_prepare_dispatch(job)"),
        )
        self.assertLess(
            source.index("_credit_prepare_dispatch(job)"),
            source.index("wgp.generate_video"),
        )
        self.assertLess(
            source.index("if not try_start("),
            source.index("_credit_prepare_dispatch(job)"),
        )
        self.assertGreaterEqual(
            source.count("_credit_block_runtime_error(job)"), 2,
        )
        parity_failure = source[
            source.index("except QueueRecoveryRuntimeError:"):
            source.index("# Recheck durable/internal/recovery jobs")
        ]
        self.assertIn(
            "_credit_admission_evaluations.pop(job_id, None)",
            parity_failure,
        )

    def test_retry_recovery_and_director_workers_converge_on_dispatch_gate(self):
        sources = {
            node.name: ast.get_source_segment(SOURCE, node)
            for node in TREE.body
            if isinstance(node, ast.FunctionDef) and node.name in {
                "_queue_recovery_worker",
                "_start_generation_worker",
                "_try_automatic_resource_retry",
                "_director_recovery_submit_child",
            }
        }
        self.assertIn("return _run_generation", sources["_queue_recovery_worker"])
        self.assertIn("target=_run_generation", sources["_start_generation_worker"])
        self.assertIn(
            "_start_generation_worker(job, name_prefix=\"studio-resource-retry\")",
            sources["_try_automatic_resource_retry"],
        )
        self.assertIn(
            "_queue_recovery_register_and_publish(",
            sources["_director_recovery_submit_child"],
        )


if __name__ == "__main__":
    unittest.main()
