"""Lifecycle/recovery wiring for disabled-by-default support credits."""

from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.credit_runtime import (
    CreditRuntimePolicy,
    quote_reservation,
    reserve_quote,
    revalidate_reservation,
    transition_reservation,
)
from services.job_lifecycle import (
    CreditQueueTransitionConflict,
    _credit_queue_band,
    _reset_queue_state_for_tests,
    _select_next_waiter,
    apply_credit_queue_decision,
    configure_durability_hook,
    consume_credit_queue_reservation,
    promote_queued_job,
    snapshot_job,
)
from services.queue_recovery import QueueRecoveryJournal
from services.queue_recovery_adapter import QueueRecoveryCoordinator

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
        older = _job("depleted-old", created_at=10.0)
        newer = _job("depleted-new", created_at=20.0)
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


if __name__ == "__main__":
    unittest.main()
