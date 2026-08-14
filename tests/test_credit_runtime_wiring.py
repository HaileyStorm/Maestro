"""Lifecycle/recovery wiring for disabled-by-default support credits."""

from __future__ import annotations

import copy
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.credit_accounting import (
    CreditAccountingJournal,
    CreditAccountingPolicy,
    CreditSourceBalance,
)
from services.credit_runtime import (
    CreditRuntimePolicy,
    quote_reservation,
    reserve_quote,
    revalidate_reservation,
    transition_reservation,
)
from services.job_lifecycle import (
    CREDIT_PRIORITY_AGE_CEILING_SECONDS,
    MAX_CREDIT_PRIORITY_BYPASSES,
    CreditQueueTransitionConflict,
    _credit_queue_band,
    _credit_queue_fingerprint,
    _queue_order_key,
    _record_queue_admission,
    _reset_queue_state_for_tests,
    _select_next_waiter,
    _validated_credit_queue_metadata,
    apply_credit_queue_decision,
    block_resource_admission_failure,
    configure_credit_lifecycle_callback,
    configure_durability_hook,
    consume_credit_queue_reservation,
    fail_preparation,
    finish_job,
    promote_queued_job,
    queue_position,
    queue_wait_reason,
    request_cancel,
    restore_scheduler_state,
    snapshot_job,
    try_start,
)
from services.queue_recovery import QueueRecoveryJournal
from services.queue_recovery_adapter import (
    QueueRecoveryAdapterError,
    QueueRecoveryCoordinator,
    _durable_order_key,
    _safe_credit_queue,
)

_STANDARD_CAPABILITY = {
    "capability_id": "video.generate",
    "support_priority_eligible": True,
    "marker": "standard_support_priority_policy",
}
_EXCLUDED_CAPABILITY = {
    "capability_id": "moody.generate",
    "support_priority_eligible": False,
    "marker": "creator_terms_exclude_support_priority",
    "creator_term": "moody_license",
}
_ENFORCED = CreditRuntimePolicy(enforcement_enabled=True)
_ALLOWANCE_REVISION = "a" * 64
_ACCOUNTING_SECRET = b"synthetic-lifecycle-accounting-secret"
_ACCOUNTING_ACCOUNT = "key_" + "a" * 64
_ACCOUNTING_SOURCE = "key_" + "b" * 64
_ACCOUNTING_RESERVATION = "reservation_" + "c" * 32


def _allowance(units: int) -> dict:
    source = {
        "source": "recurring_support",
        "source_event_id": "event_00000001",
        "granted_allowance": units,
        "effective_allowance": units,
        "expires_at": "2026-09-11T10:00:00Z",
        "status": "active" if units else "inactive",
        "refund_state": "none",
    }
    return {
        "state": "recorded_not_enforced",
        "enforcement_enabled": False,
        "unit": "generation_credit",
        "as_of": "2026-08-11T10:00:00Z",
        "effective_allowance": units,
        "sources": [source],
    }


def _quote_at(*, units: int, as_of: str):
    allowance = _allowance(units)
    allowance["as_of"] = as_of
    return quote_reservation(
        realm="hosted",
        requested_units=1,
        recorded_allowance=allowance,
        capability_priority=_STANDARD_CAPABILITY,
        policy=_ENFORCED,
    )


def _quote(
    *,
    realm: str = "hosted",
    units: int = 2,
    requested: int = 1,
    capability: dict = _STANDARD_CAPABILITY,
    enforced: bool = True,
    as_of: str = "2026-08-11T10:00:00Z",
):
    allowance = _allowance(units)
    allowance["as_of"] = as_of
    return quote_reservation(
        realm=realm,
        requested_units=requested,
        recorded_allowance=allowance,
        capability_priority=capability,
        policy=_ENFORCED if enforced else CreditRuntimePolicy(),
    )


def _job(job_id: str, *, created_at: float, priority: int = 0) -> dict:
    return {
        "id": job_id,
        "status": "queued",
        "message": "Queued",
        "source_remote": True,
        "created_at": created_at,
        "queue_priority": priority,
        "queue_held": False,
    }


class CreditRuntimeWiringTests(unittest.TestCase):
    def setUp(self):
        _reset_queue_state_for_tests()

    def tearDown(self):
        _reset_queue_state_for_tests()

    def _stamp(self, job: dict, quote, suffix: str) -> None:
        reservation = None
        if quote.reservation_required:
            reservation = reserve_quote(
                quote,
                reservation_id=f"reservation_{suffix}",
            )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                quote,
                transition_id=f"transition_{suffix}",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=reservation,
            )
        )

    def _stamp_v2(
        self,
        job: dict,
        suffix: str,
        *,
        revision: int = 1,
        accounting_reservation_id: str = "reservation_" + "1" * 32,
    ):
        quote = _quote()
        reservation = reserve_quote(
            quote,
            reservation_id=f"reservation_runtime_{suffix}",
        )
        self.assertTrue(apply_credit_queue_decision(
            job,
            quote,
            transition_id=f"transition_{suffix}",
            allowance_revision=_ALLOWANCE_REVISION,
            reservation=reservation,
            accounting_reservation_id=accounting_reservation_id,
            accounting_reservation_revision=revision,
        ))
        return quote, reservation

    def _released_v2(
        self,
        job: dict,
        *,
        owner_exempt: bool,
        suffix: str,
    ) -> dict:
        target = copy.deepcopy(job["credit_queue"])
        target["decision"] = (
            "owner_exempt_release"
            if owner_exempt
            else "hosted_priority_credit"
        )
        target["queue_band"] = 0 if owner_exempt else -1
        target["reservation_state"] = "released"
        target["revalidation_state"] = "released"
        target["accounting_reservation_revision"] += 1
        target["transition_id"] = f"transition_released_{suffix}"
        target["transition_history"] = [[
            target["transition_id"],
            _credit_queue_fingerprint(target),
        ]]
        job["credit_queue"] = target
        return target

    def test_owner_release_tombstone_is_neutral_and_round_trips(self):
        active = _job("owner-release-active", created_at=3.0)
        owner = _job("owner-release-neutral", created_at=2.0)
        released = _job("ordinary-release-depleted", created_at=1.0)
        self._stamp_v2(active, "owner_release_active")
        self._stamp_v2(owner, "owner_release_neutral")
        self._stamp_v2(released, "ordinary_release_depleted")
        owner_metadata = self._released_v2(
            owner,
            owner_exempt=True,
            suffix="owner_neutral",
        )
        self._released_v2(
            released,
            owner_exempt=False,
            suffix="ordinary_depleted",
        )

        self.assertEqual(_credit_queue_band(active), 1)
        self.assertEqual(_credit_queue_band(owner), 0)
        self.assertEqual(_credit_queue_band(released), -1)
        jobs = [released, owner, active]
        runtime_order = [
            job["id"]
            for _, job in sorted(
                enumerate(jobs),
                key=_queue_order_key,
            )
        ]
        durable_order = [
            job["id"]
            for _, job in sorted(
                enumerate(jobs),
                key=lambda entry: _durable_order_key(entry[1], entry[0]),
            )
        ]
        expected = [active["id"], owner["id"], released["id"]]
        self.assertEqual(runtime_order, expected)
        self.assertEqual(durable_order, expected)

        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
            QueueRecoveryCoordinator(journal).register_job(
                owner,
                owner_digest="owner:v1:" + "c" * 64,
                project_digest="project:v1:" + "d" * 64,
                request_manifest={"kind": "synthetic"},
            )
            recovered = QueueRecoveryCoordinator(journal).restore().jobs[owner["id"]]
        self.assertEqual(recovered["credit_queue"], owner_metadata)
        self.assertNotIn("_credit_revalidation_required", recovered)

    def test_owner_release_tombstone_rejects_forged_combinations(self):
        job = _job("owner-release-forgery", created_at=1.0)
        self._stamp_v2(job, "owner_release_forgery")
        valid = self._released_v2(
            job,
            owner_exempt=True,
            suffix="valid_owner",
        )
        self.assertEqual(_validated_credit_queue_metadata(valid), valid)
        self.assertEqual(_safe_credit_queue(valid), valid)

        forged = []
        for updates in (
            {"queue_band": -1},
            {"reservation_state": "consumed"},
            {"revalidation_state": None},
            {"requested_units_positive": False},
        ):
            candidate = copy.deepcopy(valid)
            candidate.update(updates)
            candidate["transition_history"][-1][1] = _credit_queue_fingerprint(
                candidate
            )
            forged.append(candidate)
        v1 = copy.deepcopy(valid)
        v1["schema_version"] = 1
        v1.pop("accounting_reservation_id")
        v1.pop("accounting_reservation_revision")
        v1["transition_history"][-1][1] = _credit_queue_fingerprint(v1)
        forged.append(v1)

        for candidate in forged:
            with (
                self.subTest(candidate=candidate),
                self.assertRaises(
                    ValueError,
                ),
            ):
                _validated_credit_queue_metadata(candidate)
            with self.assertRaises(QueueRecoveryAdapterError):
                _safe_credit_queue(candidate)

    def test_disabled_and_unmetered_quotes_leave_legacy_job_bytes_unchanged(self):
        for label, quote in (
            ("disabled", _quote(enforced=False)),
            ("local", _quote(realm="local")),
            ("lan", _quote(realm="lan")),
        ):
            with self.subTest(label=label):
                job = _job(label, created_at=1.0)
                before = copy.deepcopy(job)
                self.assertFalse(
                    apply_credit_queue_decision(
                        job,
                        quote,
                        transition_id=f"transition_{label}",
                        allowance_revision=_ALLOWANCE_REVISION,
                    )
                )
                self.assertEqual(job, before)

    def test_active_standard_excluded_and_depleted_use_three_bounded_bands(self):
        active = _job("active", created_at=40.0, priority=-999_999)
        standard = _job("standard", created_at=20.0)
        excluded = _job("excluded", created_at=10.0)
        depleted = _job("depleted", created_at=1.0, priority=999_999)
        self._stamp(active, _quote(), "active")
        self._stamp(
            excluded,
            _quote(capability=_EXCLUDED_CAPABILITY),
            "excluded",
        )
        self._stamp(depleted, _quote(units=0), "depleted")

        self.assertEqual(active["credit_queue"]["queue_band"], 1)
        self.assertEqual(excluded["credit_queue"]["queue_band"], 0)
        self.assertEqual(depleted["credit_queue"]["queue_band"], -1)
        self.assertFalse(depleted["queue_held"])

        eligible = list(
            enumerate(
                (depleted, excluded, standard, active),
                start=1,
            )
        )
        selected, _, _ = _select_next_waiter(eligible)
        self.assertIs(selected[1], active)
        eligible.remove(selected)
        selected, _, _ = _select_next_waiter(eligible)
        self.assertIs(selected[1], excluded)
        eligible.remove(selected)
        selected, _, _ = _select_next_waiter(eligible)
        self.assertIs(selected[1], standard)
        eligible.remove(selected)
        selected, _, _ = _select_next_waiter(eligible)
        self.assertIs(selected[1], depleted)

    def test_local_first_and_manual_start_next_remain_above_support_band(self):
        local = _job("local", created_at=20.0)
        local["source_remote"] = False
        remote_active = _job("remote-active", created_at=1.0)
        self._stamp(remote_active, _quote(), "remote_active")
        selected, _, _ = _select_next_waiter(
            [
                (1, remote_active),
                (2, local),
            ]
        )
        self.assertIs(selected[1], local)

        remote_manual = _job("remote-manual", created_at=30.0)
        self.assertTrue(promote_queued_job(remote_manual))
        selected, _, _ = _select_next_waiter(
            [
                (1, remote_active),
                (2, remote_manual),
            ]
        )
        self.assertIs(selected[1], remote_manual)

    def test_depleted_jobs_remain_automatic_fifo_within_their_band(self):
        older = _job("depleted-old", created_at=10.0, priority=-999_999)
        newer = _job("depleted-new", created_at=20.0, priority=999_999)
        self._stamp(older, _quote(units=0), "depleted_old")
        self._stamp(newer, _quote(units=0), "depleted_new")
        selected, reason, skipped = _select_next_waiter(
            [
                (2, newer),
                (1, older),
            ]
        )
        self.assertIs(selected[1], older)
        self.assertEqual(reason, "queue_order")
        self.assertEqual(skipped, [])

    def test_continuous_funded_arrivals_yield_bounded_fifo_capacity(self):
        depleted_old = _job(
            "depleted-old", created_at=1.0, priority=-999_999,
        )
        depleted_new = _job(
            "depleted-new", created_at=2.0, priority=999_999,
        )
        self._stamp(depleted_old, _quote(units=0), "starved_old")
        self._stamp(depleted_new, _quote(units=0), "starved_new")
        eligible = [(1, depleted_old), (2, depleted_new)]

        for index in range(MAX_CREDIT_PRIORITY_BYPASSES):
            funded = _job(
                f"funded-{index}", created_at=10.0 + index,
            )
            self._stamp(funded, _quote(), f"funded_{index}")
            eligible.append((10 + index, funded))
            selected, reason, skipped = _select_next_waiter(eligible)
            self.assertIs(selected[1], funded)
            _record_queue_admission(selected[1], reason, skipped, eligible)
            eligible.remove(selected)

        next_funded = _job("funded-next", created_at=20.0)
        self._stamp(next_funded, _quote(), "funded_next")
        eligible.append((20, next_funded))
        selected, reason, skipped = _select_next_waiter(eligible)
        self.assertIs(selected[1], depleted_old)
        self.assertEqual(reason, "credit_starvation_guard")
        self.assertEqual(skipped, [])

        _record_queue_admission(selected[1], reason, skipped, eligible)
        eligible.remove(selected)
        selected, reason, skipped = _select_next_waiter(eligible)
        self.assertIs(selected[1], depleted_new)
        self.assertEqual(reason, "credit_starvation_guard")
        self.assertEqual(skipped, [])

    def test_depleted_age_bound_survives_scheduler_restore(self):
        depleted = _job(
            "depleted-restored",
            created_at=time.time() - CREDIT_PRIORITY_AGE_CEILING_SECONDS - 1,
        )
        funded = _job("funded-restored", created_at=time.time())
        depleted["_credit_priority_bypass_count"] = (
            MAX_CREDIT_PRIORITY_BYPASSES - 1
        )
        restore_scheduler_state(
            [depleted, funded],
            {"queue_order": [depleted["id"], funded["id"]]},
        )
        self.assertNotIn("_credit_priority_bypass_count", depleted)
        self._stamp(depleted, _quote(units=0), "restored_depleted")
        self._stamp(funded, _quote(), "restored_funded")

        self.assertEqual(queue_position(depleted), 1)
        self.assertEqual(queue_position(funded), 2)

    def test_restored_invalid_depleted_age_conservatively_spends_priority(self):
        for label, created_at in (
            ("missing", None),
            ("nonfinite", float("nan")),
            ("future", time.time() + 3600),
        ):
            with self.subTest(label=label):
                depleted = _job(
                    f"depleted-{label}", created_at=1.0,
                )
                if created_at is None:
                    depleted.pop("created_at")
                else:
                    depleted["created_at"] = created_at
                funded = _job(
                    f"funded-{label}", created_at=time.time(),
                )
                restore_scheduler_state(
                    [depleted, funded],
                    {"queue_order": [depleted["id"], funded["id"]]},
                )
                self._stamp(
                    depleted, _quote(units=0), f"restored_{label}_depleted",
                )
                self._stamp(
                    funded, _quote(), f"restored_{label}_funded",
                )
                self.assertEqual(queue_position(depleted), 1)
                self.assertEqual(queue_position(funded), 2)

    def test_job_snapshot_cannot_mutate_server_owned_credit_metadata(self):
        job = _job("snapshot", created_at=1.0)
        self._stamp(job, _quote(), "snapshot")
        public = snapshot_job(job)
        public["credit_queue"]["queue_band"] = -1
        public["credit_queue"]["transition_history"][0][1] = "0" * 64
        self.assertEqual(job["credit_queue"]["queue_band"], 1)
        self.assertNotEqual(
            job["credit_queue"]["transition_history"][0][1],
            "0" * 64,
        )

    def test_invalid_present_metadata_fails_conservatively_to_depleted(self):
        job = _job("tampered", created_at=1.0)
        self._stamp(job, _quote(units=0), "tampered")
        job["credit_queue"]["queue_band"] = 1
        self.assertEqual(_credit_queue_band(job), -1)

    def test_transition_retry_is_idempotent_and_rebinding_is_rejected(self):
        transitions = []
        configure_durability_hook(transitions.append)
        job = _job("retry", created_at=1.0)
        quote = _quote()
        reservation = reserve_quote(
            quote,
            reservation_id="reservation_retry",
        )
        kwargs = {
            "transition_id": "transition_retry",
            "allowance_revision": _ALLOWANCE_REVISION,
            "reservation": reservation,
        }
        self.assertTrue(apply_credit_queue_decision(job, quote, **kwargs))
        first = copy.deepcopy(job)
        self.assertFalse(apply_credit_queue_decision(job, quote, **kwargs))
        self.assertEqual(job, first)
        self.assertEqual([item.name for item in transitions], ["credit_queue"])

        with self.assertRaises(CreditQueueTransitionConflict):
            apply_credit_queue_decision(
                job,
                _quote(units=0),
                transition_id="transition_retry",
                allowance_revision="b" * 64,
            )
        self.assertEqual([item.name for item in transitions], ["credit_queue"])

    def test_same_transition_retry_after_recovery_does_not_commit_again(self):
        job = _job("restart-retry", created_at=1.0)
        quote = _quote()
        reservation = reserve_quote(
            quote,
            reservation_id="reservation_restart_retry",
        )
        kwargs = {
            "transition_id": "transition_restart_retry",
            "allowance_revision": _ALLOWANCE_REVISION,
            "reservation": reservation,
        }
        self.assertTrue(apply_credit_queue_decision(job, quote, **kwargs))

        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
            coordinator = QueueRecoveryCoordinator(journal)
            coordinator.register_job(
                job,
                owner_digest="owner:v1:" + "a" * 64,
                project_digest="project:v1:" + "b" * 64,
                request_manifest={"kind": "synthetic"},
                global_state={
                    "paused": False,
                    "pause_after_current": False,
                    "manual_order_sequence": 0,
                    "queue_order": [job["id"]],
                },
            )
            restarted = QueueRecoveryCoordinator(journal)
            recovered = restarted.restore().jobs[job["id"]]
            before_epoch = restarted.epoch
            configure_durability_hook(restarted.prospective_transition)
            self.assertFalse(
                apply_credit_queue_decision(
                    recovered,
                    quote,
                    **kwargs,
                )
            )
            self.assertEqual(restarted.epoch, before_epoch)

    def test_historical_transition_retry_never_reverts_a_later_decision(self):
        job = _job("history", created_at=1.0)
        active_quote = _quote()
        active_reservation = reserve_quote(
            active_quote,
            reservation_id="reservation_history_active",
        )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                active_quote,
                transition_id="transition_history_active",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=active_reservation,
            )
        )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                _quote(units=0, as_of="2026-08-11T10:01:00Z"),
                transition_id="transition_history_depleted",
                allowance_revision="b" * 64,
            )
        )
        depleted = copy.deepcopy(job)

        self.assertFalse(
            apply_credit_queue_decision(
                job,
                active_quote,
                transition_id="transition_history_active",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=active_reservation,
            )
        )
        self.assertEqual(job, depleted)

    def test_renewed_allowance_revision_restores_priority_without_stale_rollback(self):
        job = _job("renewal", created_at=1.0)
        active_quote = _quote(as_of="2026-08-11T10:00:00Z")
        active_reservation = reserve_quote(
            active_quote,
            reservation_id="reservation_renewal_active",
        )
        old_kwargs = {
            "transition_id": "transition_renewal_active_old",
            "allowance_revision": "a" * 64,
            "reservation": active_reservation,
        }
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                active_quote,
                **old_kwargs,
            )
        )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                _quote(units=0, as_of="2026-08-11T10:01:00Z"),
                transition_id="transition_renewal_depleted",
                allowance_revision="b" * 64,
            )
        )
        self.assertEqual(job["credit_queue"]["queue_band"], -1)

        renewed_quote = _quote(as_of="2026-08-11T10:02:00Z")
        renewed_reservation = reserve_quote(
            renewed_quote,
            reservation_id="reservation_renewal_new",
        )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                renewed_quote,
                transition_id="transition_renewal_active_new",
                allowance_revision="c" * 64,
                reservation=renewed_reservation,
            )
        )
        renewed = copy.deepcopy(job)
        self.assertEqual(job["credit_queue"]["queue_band"], 1)
        self.assertEqual(job["credit_queue"]["allowance_revision"], "c" * 64)

        self.assertFalse(
            apply_credit_queue_decision(
                job,
                active_quote,
                **old_kwargs,
            )
        )
        self.assertEqual(job, renewed)

    def test_fresh_transition_id_cannot_replay_an_older_allowance_observation(self):
        job = _job("stale-observation", created_at=1.0)
        depleted_quote = _quote_at(
            units=0,
            as_of="2026-08-11T10:01:00Z",
        )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                depleted_quote,
                transition_id="transition_stale_depleted",
                allowance_revision="b" * 64,
            )
        )
        renewed_quote = _quote_at(
            units=2,
            as_of="2026-08-11T10:02:00Z",
        )
        renewed_reservation = reserve_quote(
            renewed_quote,
            reservation_id="reservation_stale_renewed",
        )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                renewed_quote,
                transition_id="transition_stale_renewed",
                allowance_revision="c" * 64,
                reservation=renewed_reservation,
            )
        )
        renewed = copy.deepcopy(job)

        stale_quote = _quote_at(
            units=0,
            as_of="2026-08-11T10:01:30Z",
        )
        with self.assertRaises(CreditQueueTransitionConflict):
            apply_credit_queue_decision(
                job,
                stale_quote,
                transition_id="transition_stale_fresh_id",
                allowance_revision="d" * 64,
            )
        self.assertEqual(job, renewed)

        same_time_conflict = _quote_at(
            units=0,
            as_of="2026-08-11T10:02:00Z",
        )
        with self.assertRaises(CreditQueueTransitionConflict):
            apply_credit_queue_decision(
                job,
                same_time_conflict,
                transition_id="transition_same_time_conflict",
                allowance_revision="e" * 64,
            )
        self.assertEqual(job, renewed)

    def test_running_reservation_consume_is_exact_durable_and_idempotent(self):
        transitions = []
        job = _job("consume", created_at=1.0)
        quote = _quote()
        reserved = reserve_quote(
            quote,
            reservation_id="reservation_consume",
        )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                quote,
                transition_id="transition_consume_reserved",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=reserved,
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
            coordinator = QueueRecoveryCoordinator(journal)
            coordinator.register_job(
                job,
                owner_digest="owner:v1:" + "e" * 64,
                project_digest="project:v1:" + "f" * 64,
                request_manifest={"kind": "synthetic"},
            )

            def persist(proposal):
                transitions.append(proposal)
                coordinator.prospective_transition(proposal)

            configure_durability_hook(persist)
            job["status"] = "running"
            consumed = transition_reservation(reserved, "consume")
            kwargs = {
                "transition_id": "transition_consume_running",
                "allowance_revision": _ALLOWANCE_REVISION,
                "reservation": consumed,
            }
            self.assertTrue(consume_credit_queue_reservation(job, quote, **kwargs))
            self.assertEqual(job["credit_queue"]["reservation_state"], "consumed")
            self.assertEqual(job["credit_queue"]["queue_band"], 1)
            self.assertFalse(consume_credit_queue_reservation(job, quote, **kwargs))
            self.assertEqual(
                [transition.name for transition in transitions],
                ["credit_reservation_consumed"],
            )
            restarted = QueueRecoveryCoordinator(journal).restore().jobs[job["id"]]
            self.assertEqual(
                restarted["credit_queue"]["reservation_state"],
                "consumed",
            )
            self.assertEqual(
                restarted["credit_queue"]["allowance_revision"],
                _ALLOWANCE_REVISION,
            )

    def test_running_consume_preserves_valid_revalidation_state(self):
        job = _job("consume-revalidated", created_at=1.0)
        quote = _quote()
        reserved = reserve_quote(
            quote,
            reservation_id="reservation_consume_revalidated",
        )
        revalidated = revalidate_reservation(reserved, _allowance(2))
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                quote,
                transition_id="transition_consume_revalidated_reserved",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=reserved,
                revalidation=revalidated,
            )
        )
        job["status"] = "running"
        consumed = transition_reservation(reserved, "consume")
        self.assertTrue(
            consume_credit_queue_reservation(
                job,
                quote,
                transition_id="transition_consume_revalidated_running",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=consumed,
            )
        )
        self.assertEqual(job["credit_queue"]["revalidation_state"], "valid")
        self.assertEqual(job["credit_queue"]["reservation_state"], "consumed")

    def test_running_consume_cannot_create_or_reband_priority(self):
        quote = _quote()
        consumed = transition_reservation(
            reserve_quote(quote, reservation_id="reservation_consume_reject"),
            "consume",
        )
        fresh = _job("fresh-running", created_at=1.0)
        fresh["status"] = "running"
        self.assertFalse(
            consume_credit_queue_reservation(
                fresh,
                quote,
                transition_id="transition_consume_fresh",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=consumed,
            )
        )
        self.assertNotIn("credit_queue", fresh)

        job = _job("changed-running", created_at=1.0)
        self._stamp(job, quote, "changed_running")
        job["status"] = "running"
        before = copy.deepcopy(job)
        self.assertFalse(
            consume_credit_queue_reservation(
                job,
                quote,
                transition_id="transition_consume_changed",
                allowance_revision="b" * 64,
                reservation=consumed,
            )
        )
        self.assertEqual(job, before)
        job["cancel_requested"] = True
        cancelled = copy.deepcopy(job)
        self.assertFalse(
            consume_credit_queue_reservation(
                job,
                quote,
                transition_id="transition_consume_cancelled",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=consumed,
            )
        )
        self.assertEqual(job, cancelled)

    def test_same_id_cannot_rebind_active_decision_to_disabled(self):
        job = _job("disable-conflict", created_at=1.0)
        quote = _quote()
        reservation = reserve_quote(
            quote,
            reservation_id="reservation_disable_conflict",
        )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                quote,
                transition_id="transition_disable_conflict",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=reservation,
            )
        )
        with self.assertRaises(CreditQueueTransitionConflict):
            apply_credit_queue_decision(
                job,
                _quote(enforced=False),
                transition_id="transition_disable_conflict",
                allowance_revision="b" * 64,
            )

    def test_distinct_disabled_transition_retains_prior_retry_fences(self):
        job = _job("disabled-history", created_at=1.0)
        active_quote = _quote()
        reservation = reserve_quote(
            active_quote,
            reservation_id="reservation_disabled_history",
        )
        active_kwargs = {
            "transition_id": "transition_disabled_history_active",
            "allowance_revision": _ALLOWANCE_REVISION,
            "reservation": reservation,
        }
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                active_quote,
                **active_kwargs,
            )
        )
        self.assertTrue(
            apply_credit_queue_decision(
                job,
                _quote(
                    enforced=False,
                    as_of="2026-08-11T10:01:00Z",
                ),
                transition_id="transition_disabled_history_off",
                allowance_revision="b" * 64,
            )
        )
        disabled = copy.deepcopy(job)
        self.assertEqual(job["credit_queue"]["queue_band"], 0)
        self.assertFalse(job["credit_queue"]["metering_applied"])

        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
            coordinator = QueueRecoveryCoordinator(journal)
            coordinator.register_job(
                job,
                owner_digest="owner:v1:" + "c" * 64,
                project_digest="project:v1:" + "d" * 64,
                request_manifest={"kind": "synthetic"},
            )
            recovered = QueueRecoveryCoordinator(journal).restore().jobs[job["id"]]
            self.assertEqual(recovered["credit_queue"], job["credit_queue"])

        self.assertFalse(
            apply_credit_queue_decision(
                job,
                active_quote,
                **active_kwargs,
            )
        )
        self.assertEqual(job, disabled)
        with self.assertRaises(CreditQueueTransitionConflict):
            apply_credit_queue_decision(
                job,
                active_quote,
                transition_id="transition_disabled_history_off",
                allowance_revision="c" * 64,
                reservation=reservation,
            )

    def test_transition_history_rejects_overflow_instead_of_evicting_fence(self):
        job = _job("history-cap", created_at=1.0)
        for index in range(32):
            self.assertTrue(
                apply_credit_queue_decision(
                    job,
                    _quote(
                        units=0 if index % 2 == 0 else 2,
                        capability=(
                            _STANDARD_CAPABILITY
                            if index % 2 == 0
                            else _EXCLUDED_CAPABILITY
                        ),
                        as_of=f"2026-08-11T10:00:{index:02d}Z",
                    ),
                    transition_id=f"transition_history_cap_{index:03d}",
                    allowance_revision=(format(index + 1, "064x")),
                )
            )
        frozen = copy.deepcopy(job)
        with self.assertRaises(CreditQueueTransitionConflict):
            apply_credit_queue_decision(
                job,
                _quote(),
                transition_id="transition_history_cap_overflow",
                allowance_revision="f" * 64,
                reservation=reserve_quote(
                    _quote(),
                    reservation_id="reservation_history_cap_overflow",
                ),
            )
        self.assertEqual(job, frozen)

    def test_v2_accounting_callback_consumes_exact_current_revision(self):
        job = _job("accounting-consume", created_at=1.0)
        quote, reserved = self._stamp_v2(
            job, "accounting_consume", revision=7,
        )
        events = []

        def callback(event):
            events.append(dict(event))
            return {
                "reservation_status": "consumed",
                "reservation_revision": event["expected_revision"] + 1,
                "fully_funded": False,
                "allocation_satisfied": True,
                "terminal_satisfied": False,
            }

        configure_credit_lifecycle_callback(callback)
        self.assertTrue(try_start(job))
        self.assertTrue(consume_credit_queue_reservation(
            job,
            quote,
            transition_id="transition_accounting_consume_running",
            allowance_revision=_ALLOWANCE_REVISION,
            reservation=transition_reservation(reserved, "consume"),
        ))
        self.assertEqual(job["credit_queue"]["reservation_state"], "consumed")
        self.assertEqual(
            job["credit_queue"]["accounting_reservation_revision"], 8,
        )
        self.assertEqual(set(events[0]), {
            "action", "job_id", "accounting_reservation_id",
            "expected_revision", "operation_id", "as_of",
            "require_full_requested",
        })
        self.assertEqual(events[0]["action"], "consume")
        self.assertEqual(events[0]["expected_revision"], 7)
        self.assertTrue(events[0]["require_full_requested"])
        self.assertNotIn("account", events[0])
        self.assertNotIn("source", events[0])
        self.assertNotIn("provider", events[0])

    def test_callback_failure_aborts_consume_and_crash_retry_replays_operation(self):
        job = _job("accounting-replay", created_at=1.0)
        quote, reserved = self._stamp_v2(job, "accounting_replay")
        self.assertTrue(try_start(job))
        original = copy.deepcopy(job)
        events = []

        def callback(event):
            events.append(dict(event))
            return {
                "reservation_status": "consumed",
                "reservation_revision": 2,
                "fully_funded": False,
                "allocation_satisfied": True,
                "terminal_satisfied": False,
            }

        configure_credit_lifecycle_callback(callback)
        configure_durability_hook(
            lambda _transition: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        with self.assertRaises(RuntimeError):
            consume_credit_queue_reservation(
                job,
                quote,
                transition_id="transition_accounting_replay_running",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=transition_reservation(reserved, "consume"),
            )
        self.assertEqual(job, original)
        configure_durability_hook(None)
        self.assertTrue(consume_credit_queue_reservation(
            job,
            quote,
            transition_id="transition_accounting_replay_running",
            allowance_revision=_ALLOWANCE_REVISION,
            reservation=transition_reservation(reserved, "consume"),
        ))
        self.assertEqual(events[0], events[1])

        failed = _job("accounting-failure", created_at=2.0)
        failed_quote, failed_reserved = self._stamp_v2(
            failed, "accounting_failure",
        )
        self.assertTrue(try_start(failed))
        configure_credit_lifecycle_callback(None)
        configure_credit_lifecycle_callback(
            lambda _event: (_ for _ in ()).throw(RuntimeError("journal failed")),
        )
        frozen = copy.deepcopy(failed)
        with self.assertRaises(RuntimeError):
            consume_credit_queue_reservation(
                failed,
                failed_quote,
                transition_id="transition_accounting_failure_running",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=transition_reservation(failed_reserved, "consume"),
            )
        self.assertEqual(failed, frozen)

    def test_reserved_v2_releases_on_predispatch_terminal_paths_only_once(self):
        events = []

        def callback(event):
            events.append(dict(event))
            return {
                "reservation_status": (
                    "consumed" if event["action"] == "consume" else "released"
                ),
                "reservation_revision": event["expected_revision"] + 1,
                "fully_funded": False,
                "allocation_satisfied": event["action"] == "consume",
                "terminal_satisfied": event["action"] == "release",
            }

        configure_credit_lifecycle_callback(callback)

        cancelled = _job("release-cancel", created_at=1.0)
        self._stamp_v2(cancelled, "release_cancel")
        self.assertTrue(request_cancel(cancelled).changed)

        preparation = _job("release-preparation", created_at=2.0)
        self._stamp_v2(preparation, "release_preparation")
        preparation["status"] = "preparing"
        self.assertTrue(fail_preparation(preparation))

        blocked = _job("release-blocked", created_at=3.0)
        self._stamp_v2(blocked, "release_blocked")
        self.assertTrue(block_resource_admission_failure(blocked))

        terminal = _job("release-terminal", created_at=4.0)
        self._stamp_v2(terminal, "release_terminal")
        terminal["status"] = "running"
        self.assertTrue(finish_job(terminal, "failed"))

        self.assertEqual([event["action"] for event in events], [
            "release", "release", "release", "release",
        ])
        for job in (cancelled, preparation, blocked, terminal):
            self.assertEqual(job["credit_queue"]["reservation_state"], "released")
            self.assertEqual(
                job["credit_queue"]["accounting_reservation_revision"], 2,
            )

    def test_consumed_reservation_is_never_released_on_cancel(self):
        job = _job("consume-no-release", created_at=1.0)
        quote, reserved = self._stamp_v2(job, "consume_no_release")
        actions = []

        def callback(event):
            actions.append(event["action"])
            return {
                "reservation_status": (
                    "consumed" if event["action"] == "consume" else "released"
                ),
                "reservation_revision": event["expected_revision"] + 1,
                "fully_funded": False,
                "allocation_satisfied": event["action"] == "consume",
                "terminal_satisfied": event["action"] == "release",
            }

        configure_credit_lifecycle_callback(callback)
        self.assertTrue(try_start(job))
        self.assertTrue(consume_credit_queue_reservation(
            job,
            quote,
            transition_id="transition_consume_no_release_running",
            allowance_revision=_ALLOWANCE_REVISION,
            reservation=transition_reservation(reserved, "consume"),
        ))
        self.assertTrue(request_cancel(job).changed)
        self.assertEqual(actions, ["consume"])

    def test_restored_priority_credit_is_fenced_until_server_revalidation(self):
        restored = _job("restored-credit", created_at=1.0)
        self._stamp(restored, _quote(), "restored_credit")
        restore_scheduler_state([restored], {"queue_order": [restored["id"]]})
        self.assertTrue(restored["_credit_revalidation_required"])
        self.assertEqual(_credit_queue_band(restored), 0)
        self.assertIsNone(queue_position(restored))
        self.assertEqual(queue_wait_reason(restored), "credit_revalidation")
        self.assertFalse(promote_queued_job(restored))

        renewed_quote = _quote(as_of="2026-08-11T10:01:00Z")
        renewed = reserve_quote(
            renewed_quote, reservation_id="reservation_restored_renewed",
        )
        self.assertTrue(apply_credit_queue_decision(
            restored,
            renewed_quote,
            transition_id="transition_restored_revalidated",
            allowance_revision="b" * 64,
            reservation=renewed,
        ))
        self.assertNotIn("_credit_revalidation_required", restored)

    def test_restored_running_priority_credit_cannot_consume_while_fenced(self):
        job = _job("restored-running-credit", created_at=1.0)
        quote = _quote()
        reserved = reserve_quote(
            quote, reservation_id="reservation_restored_running_credit",
        )
        self.assertTrue(apply_credit_queue_decision(
            job,
            quote,
            transition_id="transition_restored_running_reserved",
            allowance_revision=_ALLOWANCE_REVISION,
            reservation=reserved,
        ))
        job["status"] = "running"
        restore_scheduler_state([job], {})
        self.assertTrue(job["_credit_revalidation_required"])
        self.assertFalse(consume_credit_queue_reservation(
            job,
            quote,
            transition_id="transition_restored_running_consumed",
            allowance_revision=_ALLOWANCE_REVISION,
            reservation=transition_reservation(reserved, "consume"),
        ))
        self.assertTrue(job["_credit_revalidation_required"])
        self.assertEqual(job["credit_queue"]["reservation_state"], "reserved")

    def test_accounting_callback_is_not_called_when_history_is_full(self):
        job = _job("accounting-history-cap", created_at=1.0)
        self._stamp_v2(job, "accounting_history_cap")
        history = job["credit_queue"]["transition_history"]
        while len(history) < 32:
            history.insert(0, [
                f"transition_accounting_history_{len(history):03d}",
                format(len(history) + 1, "064x"),
            ])
        calls = []
        configure_credit_lifecycle_callback(
            lambda event: calls.append(dict(event)) or {
                "reservation_status": "released",
                "reservation_revision": 2,
                "fully_funded": False,
                "allocation_satisfied": False,
                "terminal_satisfied": True,
            },
        )
        frozen = copy.deepcopy(job)
        with self.assertRaises(CreditQueueTransitionConflict):
            request_cancel(job)
        self.assertEqual(calls, [])
        self.assertEqual(job, frozen)

    def test_real_accounting_journal_consume_receipt_satisfies_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = CreditAccountingJournal(
                Path(directory) / "credit-accounting.json",
                integrity_key=_ACCOUNTING_SECRET,
                policy=CreditAccountingPolicy(enforcement_enabled=True),
            )
            journal.reconcile(
                account_key=_ACCOUNTING_ACCOUNT,
                operation_id="operation_" + "1" * 32,
                unit="generation_credit",
                sources=[CreditSourceBalance(_ACCOUNTING_SOURCE, 1)],
                as_of="2026-08-11T10:00:00Z",
            )
            receipt = journal.reserve(
                account_key=_ACCOUNTING_ACCOUNT,
                reservation_id=_ACCOUNTING_RESERVATION,
                operation_id="operation_" + "2" * 32,
                requested_units=1,
                as_of="2026-08-11T10:00:00Z",
            )
            job = _job("real-journal-consume", created_at=1.0)
            quote, reserved = self._stamp_v2(
                job,
                "real_journal_consume",
                revision=receipt.reservation_revision,
                accounting_reservation_id=_ACCOUNTING_RESERVATION,
            )
            self.assertTrue(try_start(job))

            def callback(event):
                result = journal.consume(
                    account_key=_ACCOUNTING_ACCOUNT,
                    reservation_id=event["accounting_reservation_id"],
                    operation_id=event["operation_id"],
                    expected_revision=event["expected_revision"],
                    as_of=event["as_of"],
                )
                return {
                    "reservation_status": result.reservation_status,
                    "reservation_revision": result.reservation_revision,
                    "fully_funded": result.fully_funded,
                    "allocation_satisfied": result.allocation_satisfied,
                    "terminal_satisfied": result.terminal_satisfied,
                }

            configure_credit_lifecycle_callback(callback)
            self.assertTrue(consume_credit_queue_reservation(
                job,
                quote,
                transition_id="transition_real_journal_consume_running",
                allowance_revision=_ALLOWANCE_REVISION,
                reservation=transition_reservation(reserved, "consume"),
            ))
            self.assertEqual(job["credit_queue"]["reservation_state"], "consumed")
            self.assertEqual(
                job["credit_queue"]["accounting_reservation_revision"], 2,
            )

    def test_real_journal_stale_and_invalidated_release_is_terminal_satisfied(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = CreditAccountingJournal(
                Path(directory) / "credit-accounting.json",
                integrity_key=_ACCOUNTING_SECRET,
                policy=CreditAccountingPolicy(enforcement_enabled=True),
            )
            journal.reconcile(
                account_key=_ACCOUNTING_ACCOUNT,
                operation_id="operation_" + "3" * 32,
                unit="generation_credit",
                sources=[CreditSourceBalance(_ACCOUNTING_SOURCE, 2)],
                as_of="2026-08-11T10:00:00Z",
            )
            journal.reserve(
                account_key=_ACCOUNTING_ACCOUNT,
                reservation_id=_ACCOUNTING_RESERVATION,
                operation_id="operation_" + "4" * 32,
                requested_units=1,
                as_of="2026-08-11T10:00:00Z",
            )
            invalidated = journal.revalidate_reservation(
                account_key=_ACCOUNTING_ACCOUNT,
                reservation_id=_ACCOUNTING_RESERVATION,
                operation_id="operation_" + "5" * 32,
                unit="generation_credit",
                sources=[CreditSourceBalance(_ACCOUNTING_SOURCE, 0)],
                as_of="2026-08-11T10:01:00Z",
            )
            self.assertEqual(invalidated.reservation_status, "invalidated")
            job = _job("real-journal-release", created_at=1.0)
            self._stamp_v2(
                job,
                "real_journal_release",
                revision=1,
                accounting_reservation_id=_ACCOUNTING_RESERVATION,
            )

            def callback(event):
                result = journal.release(
                    account_key=_ACCOUNTING_ACCOUNT,
                    reservation_id=event["accounting_reservation_id"],
                    operation_id=event["operation_id"],
                    expected_revision=event["expected_revision"],
                    as_of=event["as_of"],
                )
                return {
                    "reservation_status": result.reservation_status,
                    "reservation_revision": result.reservation_revision,
                    "fully_funded": result.fully_funded,
                    "allocation_satisfied": result.allocation_satisfied,
                    "terminal_satisfied": result.terminal_satisfied,
                }

            configure_credit_lifecycle_callback(callback)
            self.assertTrue(request_cancel(job).changed)
            self.assertEqual(job["credit_queue"]["reservation_state"], "released")
            self.assertEqual(
                job["credit_queue"]["accounting_reservation_revision"],
                invalidated.reservation_revision,
            )


if __name__ == "__main__":
    unittest.main()
