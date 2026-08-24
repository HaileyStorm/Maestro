"""Durability and lifecycle contracts for the hard-off credit journal."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.credit_accounting import (
    CreditAccountingConflict,
    CreditAccountingError,
    CreditAccountingIntegrityError,
    CreditAccountingJournal,
    CreditAccountingPolicy,
    CreditSourceBalance,
)
from services.entitlements import ExclusiveLeaseError

SECRET = b"credit-accounting-test-integrity-key-0000000000000000"
ACCOUNT = "key_" + hashlib.sha256(b"account").hexdigest()
SOURCE_A = "key_" + hashlib.sha256(b"source-a").hexdigest()
SOURCE_B = "key_" + hashlib.sha256(b"source-b").hexdigest()


def reservation(index: int) -> str:
    return "reservation_" + hashlib.sha256(f"reservation-{index}".encode()).hexdigest()


def operation(index: int) -> str:
    return "operation_" + hashlib.sha256(f"operation-{index}".encode()).hexdigest()


def _process_reserve(path: str, index: int, gate, results) -> None:
    journal = CreditAccountingJournal(
        path,
        integrity_key=SECRET,
        policy=CreditAccountingPolicy(enforcement_enabled=True),
    )
    gate.wait()
    try:
        receipt = journal.reserve(
            account_key=ACCOUNT,
            reservation_id=reservation(index),
            operation_id=operation(index + 100),
            requested_units=8,
            as_of="2026-08-11T10:01:00Z",
        )
        results.put(receipt.affected_units)
    except (CreditAccountingError, OSError) as error:  # pragma: no cover
        results.put(type(error).__name__)


def _process_revalidate(path: str, gate, results) -> None:
    journal = CreditAccountingJournal(
        path,
        integrity_key=SECRET,
        policy=CreditAccountingPolicy(enforcement_enabled=True),
    )
    gate.wait()
    try:
        receipt = journal.revalidate_reservation(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(201),
            unit="compute_seconds",
            sources=[CreditSourceBalance(SOURCE_A, 5)],
            as_of="2026-08-11T10:02:00Z",
        )
        results.put((
            "revalidate", receipt.reservation_status,
            receipt.affected_units, receipt.reservation_revision,
        ))
    except (CreditAccountingError, OSError) as error:  # pragma: no cover
        results.put(("revalidate_error", type(error).__name__))


def _process_consume(path: str, gate, results) -> None:
    journal = CreditAccountingJournal(
        path,
        integrity_key=SECRET,
        policy=CreditAccountingPolicy(enforcement_enabled=True),
    )
    gate.wait()
    try:
        receipt = journal.consume(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(202),
            expected_revision=1,
            as_of="2026-08-11T10:02:00Z",
        )
        results.put(("consume", receipt.affected_units, receipt.reservation_revision))
    except (CreditAccountingError, OSError) as error:  # pragma: no cover
        results.put(("consume_error", type(error).__name__))


class CreditAccountingJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "credits.json"
        self.enabled = CreditAccountingJournal(
            self.path,
            integrity_key=SECRET,
            policy=CreditAccountingPolicy(enforcement_enabled=True),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def reconcile(self, sources, *, op: int = 1, at: str = "2026-08-11T10:00:00Z"):
        return self.enabled.reconcile(
            account_key=ACCOUNT,
            operation_id=operation(op),
            unit="compute_seconds",
            sources=sources,
            as_of=at,
        )

    def reserve(self, index: int, units: int, *, at="2026-08-11T10:01:00Z"):
        return self.enabled.reserve(
            account_key=ACCOUNT,
            reservation_id=reservation(index),
            operation_id=operation(index + 10),
            requested_units=units,
            as_of=at,
        )

    def transition(
        self,
        kind: str,
        index: int,
        op: int,
        *,
        at="2026-08-11T10:02:00Z",
        expected_revision: int = 1,
    ):
        arguments = {
            "account_key": ACCOUNT,
            "reservation_id": reservation(index),
            "operation_id": operation(op),
            "as_of": at,
        }
        arguments["expected_revision"] = expected_revision
        return getattr(self.enabled, kind)(**arguments)

    def revalidate(
        self,
        index: int,
        sources,
        *,
        op: int,
        at="2026-08-11T10:02:00Z",
    ):
        return self.enabled.revalidate_reservation(
            account_key=ACCOUNT,
            reservation_id=reservation(index),
            operation_id=operation(op),
            unit="compute_seconds",
            sources=sources,
            as_of=at,
        )

    def settle(
        self,
        index: int,
        op: int,
        terminal_status: str,
        server_billable_units: int | None,
        *,
        at="2026-08-11T10:03:00Z",
        expected_revision: int = 2,
    ):
        return self.enabled.settle(
            account_key=ACCOUNT,
            reservation_id=reservation(index),
            operation_id=operation(op),
            terminal_status=terminal_status,
            server_billable_units=server_billable_units,
            expected_revision=expected_revision,
            as_of=at,
        )

    def test_default_is_hard_off_and_does_not_create_state(self):
        disabled = CreditAccountingJournal(self.path, integrity_key=SECRET)
        reconciled = disabled.reconcile(
            account_key=ACCOUNT,
            operation_id=operation(2),
            unit="compute_seconds",
            sources=[CreditSourceBalance(SOURCE_A, 10)],
            as_of="2026-08-11T10:00:00Z",
        )
        receipt = disabled.reserve(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(1),
            requested_units=10,
            as_of="2026-08-11T10:00:00Z",
        )
        self.assertEqual(reconciled.state, "disabled")
        self.assertEqual(receipt.state, "disabled")
        self.assertEqual(receipt.affected_units, 0)
        self.assertEqual(disabled.public_projection(ACCOUNT)["state"], "disabled")
        self.assertFalse(self.path.exists())
        self.assertFalse(disabled.lock_path.exists())

        absent_parent = Path(self.temporary.name) / "absent" / "credits.json"
        with self.assertRaises(CreditAccountingError):
            CreditAccountingJournal(absent_parent, integrity_key=SECRET)
        self.assertFalse(absent_parent.parent.exists())

    def test_earliest_expiry_allocation_and_restart_are_durable(self):
        self.reconcile([
            CreditSourceBalance(SOURCE_A, 10, "2026-08-11T12:00:00Z"),
            CreditSourceBalance(SOURCE_B, 7, "2026-08-11T11:00:00Z"),
        ])
        receipt = self.reserve(1, 12)
        self.assertEqual(receipt.affected_units, 12)

        restarted = CreditAccountingJournal(
            self.path,
            integrity_key=SECRET,
            policy=CreditAccountingPolicy(enforcement_enabled=True),
        )
        projection = restarted.public_projection(ACCOUNT)
        self.assertEqual(projection["reserved_units"], 12)
        self.assertEqual(projection["available_units"], 5)
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        allocations = envelope["state"]["accounts"][ACCOUNT]["reservations"][
            reservation(1)
        ]["allocations"]
        self.assertEqual(
            allocations,
            [
                {"source_key": SOURCE_B, "units": 7},
                {"source_key": SOURCE_A, "units": 5},
            ],
        )

    def test_operation_retry_is_idempotent_and_altered_retry_conflicts(self):
        sources = [CreditSourceBalance(SOURCE_A, 10)]
        first = self.reconcile(sources)
        self.assertEqual(self.reconcile(sources), first)
        with self.assertRaises(CreditAccountingConflict):
            self.reconcile([CreditSourceBalance(SOURCE_A, 9)])

        reserved = self.reserve(1, 8)
        replay = self.enabled.reserve(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(11),
            requested_units=8,
            as_of="2026-08-11T10:01:00Z",
        )
        self.assertEqual(replay, reserved)
        with self.assertRaises(CreditAccountingConflict):
            self.enabled.reserve(
                account_key=ACCOUNT,
                reservation_id=reservation(1),
                operation_id=operation(11),
                requested_units=7,
                as_of="2026-08-11T10:01:00Z",
            )

    def test_unchanged_revalidation_is_fully_funded_revision_stable_and_idempotent(self):
        sources = [CreditSourceBalance(SOURCE_A, 10)]
        self.reconcile(sources)
        reserved = self.reserve(1, 10)
        self.assertEqual(reserved.reservation_revision, 1)
        self.assertTrue(reserved.fully_funded)

        current = self.revalidate(1, sources, op=40)
        self.assertEqual(current.reservation_status, "pending")
        self.assertEqual(current.requested_units, 10)
        self.assertEqual(current.affected_units, 10)
        self.assertEqual(current.reservation_revision, 1)
        self.assertTrue(current.fully_funded)
        self.assertEqual(self.revalidate(1, sources, op=40), current)
        with self.assertRaises(CreditAccountingConflict):
            self.revalidate(
                1,
                [CreditSourceBalance(SOURCE_A, 9)],
                op=40,
            )

    def test_partial_full_refund_and_expiry_revalidation_revisions(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        self.reserve(1, 10)
        partial = self.revalidate(
            1, [CreditSourceBalance(SOURCE_A, 6)], op=41,
        )
        self.assertEqual(
            (
                partial.reservation_status, partial.requested_units,
                partial.affected_units, partial.reservation_revision,
                partial.fully_funded,
            ),
            ("pending", 10, 6, 2, False),
        )
        with self.assertRaises(CreditAccountingConflict):
            self.transition("consume", 1, 42, expected_revision=1)
        with self.assertRaises(CreditAccountingConflict):
            self.transition("consume", 1, 43, expected_revision=2)

        refunded = self.revalidate(
            1, [CreditSourceBalance(SOURCE_A, 0)], op=44,
        )
        self.assertEqual(
            (
                refunded.reservation_status, refunded.affected_units,
                refunded.reservation_revision, refunded.fully_funded,
            ),
            ("invalidated", 0, 3, False),
        )

        self.reconcile([CreditSourceBalance(SOURCE_A, 10)], op=45)
        self.reserve(2, 10)
        expired = self.revalidate(
            2,
            [CreditSourceBalance(
                SOURCE_A, 10, "2026-08-11T10:04:00Z",
            )],
            op=46,
            at="2026-08-11T10:04:00Z",
        )
        self.assertEqual(
            (
                expired.reservation_status, expired.affected_units,
                expired.reservation_revision, expired.fully_funded,
            ),
            ("invalidated", 0, 2, False),
        )

    def test_current_revision_consumes_after_restart_and_stale_revision_rejects(self):
        sources = [CreditSourceBalance(SOURCE_A, 10)]
        self.reconcile(sources)
        self.reserve(1, 10)
        current = self.revalidate(1, sources, op=47)

        restarted = CreditAccountingJournal(
            self.path,
            integrity_key=SECRET,
            policy=CreditAccountingPolicy(enforcement_enabled=True),
        )
        with self.assertRaises(CreditAccountingConflict):
            restarted.consume(
                account_key=ACCOUNT,
                reservation_id=reservation(1),
                operation_id=operation(48),
                expected_revision=current.reservation_revision + 1,
                as_of="2026-08-11T10:03:00Z",
            )
        consumed = restarted.consume(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(49),
            expected_revision=current.reservation_revision,
            as_of="2026-08-11T10:03:00Z",
        )
        self.assertEqual(consumed.reservation_status, "consumed")
        self.assertEqual(consumed.reservation_revision, 2)
        self.assertEqual(consumed.affected_units, consumed.requested_units)
        self.assertFalse(consumed.fully_funded)
        self.assertTrue(consumed.allocation_satisfied)
        self.assertFalse(consumed.terminal_satisfied)

    def test_terminal_settlement_refunds_failure_and_bounds_cancelled_use(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 40)])

        self.reserve(1, 10)
        self.transition("consume", 1, 30)
        failed = self.settle(1, 31, "failed", None)
        self.assertEqual(
            (
                failed.reservation_status,
                failed.affected_units,
                failed.reservation_revision,
                failed.allocation_satisfied,
                failed.terminal_satisfied,
            ),
            ("settled", 0, 3, False, True),
        )
        self.assertEqual(
            self.enabled.public_projection(ACCOUNT)["consumed_units"], 0,
        )

        self.reserve(2, 10)
        self.transition("consume", 2, 32)
        cancelled = self.settle(2, 33, "cancelled", 4)
        self.assertEqual(cancelled.affected_units, 4)
        self.assertEqual(
            self.settle(
                2, 33, "cancelled", 9,
                at="2026-08-11T10:04:00Z",
            ),
            cancelled,
        )
        self.assertEqual(
            self.enabled.public_projection(ACCOUNT)["consumed_units"], 4,
        )

        self.reserve(3, 10)
        self.transition("consume", 3, 34)
        capped = self.settle(3, 35, "cancelled", 99)
        self.assertEqual(capped.affected_units, 10)
        self.assertEqual(
            self.enabled.public_projection(ACCOUNT)["consumed_units"], 14,
        )

    def test_success_settlement_and_legacy_receipts_survive_restart_replay(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        self.reserve(1, 10)
        consumed = self.transition("consume", 1, 30)
        before = json.loads(self.path.read_text(encoding="utf-8"))["state"][
            "operations"
        ][operation(30)]["receipt"]

        settled = self.settle(1, 31, "completed", None)
        self.assertEqual(settled.reservation_status, "settled")
        self.assertEqual(settled.affected_units, 10)
        self.assertTrue(settled.terminal_satisfied)
        self.assertEqual(
            self.enabled.public_projection(ACCOUNT)["consumed_units"], 10,
        )

        restarted = CreditAccountingJournal(
            self.path,
            integrity_key=SECRET,
            policy=CreditAccountingPolicy(enforcement_enabled=True),
        )
        replayed_settlement = restarted.settle(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(31),
            terminal_status="completed",
            server_billable_units=None,
            expected_revision=2,
            as_of="2026-08-11T10:09:00Z",
        )
        self.assertEqual(replayed_settlement, settled)
        with self.assertRaises(CreditAccountingConflict):
            restarted.settle(
                account_key=ACCOUNT,
                reservation_id=reservation(1),
                operation_id=operation(31),
                terminal_status="cancelled",
                server_billable_units=0,
                expected_revision=2,
                as_of="2026-08-11T10:09:00Z",
            )
        replayed_consume = restarted.consume(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(30),
            expected_revision=1,
            as_of="2026-08-11T10:02:00Z",
        )
        self.assertEqual(replayed_consume, consumed)
        after = json.loads(self.path.read_text(encoding="utf-8"))["state"][
            "operations"
        ][operation(30)]["receipt"]
        self.assertEqual(after, before)

    def test_cancel_settlement_refunds_the_exact_lot_remainder(self):
        self.reconcile([
            CreditSourceBalance(
                SOURCE_A, 13, "2026-08-11T12:00:00Z",
            ),
            CreditSourceBalance(
                SOURCE_B, 7, "2026-08-11T11:00:00Z",
            ),
        ])
        self.reserve(1, 20)
        self.transition("consume", 1, 30)
        settled = self.settle(1, 31, "cancelled", 8)
        self.assertEqual(settled.affected_units, 8)
        state = json.loads(self.path.read_text(encoding="utf-8"))["state"]
        self.assertEqual(
            state["accounts"][ACCOUNT]["reservations"][reservation(1)][
                "allocations"
            ],
            [
                {"source_key": SOURCE_B, "units": 7},
                {"source_key": SOURCE_A, "units": 1},
            ],
        )

    def test_expired_consume_persists_clock_and_backdated_retry_stays_rejected(self):
        expiring = [
            CreditSourceBalance(SOURCE_A, 10, "2026-08-11T10:05:00Z"),
        ]
        self.reconcile(expiring)
        self.reserve(1, 10)
        with self.assertRaises(CreditAccountingConflict):
            self.transition(
                "consume", 1, 53,
                at="2026-08-11T10:05:00Z",
                expected_revision=1,
            )
        with self.assertRaises(CreditAccountingConflict):
            self.transition(
                "consume", 1, 54,
                at="2026-08-11T10:04:00Z",
                expected_revision=2,
            )
        projection = self.enabled.public_projection(ACCOUNT)
        self.assertEqual(projection["clock_high_water"], "2026-08-11T10:05:00Z")
        self.assertEqual(projection["reserved_units"], 0)

    def test_operation_bound_failure_preserves_pending_consume_and_release(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 20)])
        self.reserve(1, 10)
        self.reserve(2, 10)
        with patch("services.credit_accounting.MAX_OPERATIONS", 3):
            with self.assertRaises(CreditAccountingError):
                self.transition("consume", 1, 60)
            with self.assertRaises(CreditAccountingError):
                self.transition("release", 2, 61)
        projection = self.enabled.public_projection(ACCOUNT)
        self.assertEqual(projection["consumed_units"], 0)
        self.assertEqual(projection["reserved_units"], 20)
        self.assertEqual(projection["pending_reservations"], 2)

    def test_schema_v2_state_fails_as_explicitly_incompatible(self):
        legacy_state = {
            "schema_version": 2,
            "clock_high_water": None,
            "next_sequence": 1,
            "accounts": {},
            "operations": {},
        }
        envelope = {
            "state": legacy_state,
            "state_hmac": self.enabled._seal(legacy_state),
        }
        self.path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(CreditAccountingIntegrityError):
            self.enabled.public_projection(ACCOUNT)

    def test_release_is_terminal_idempotent_after_revision_drift(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        self.reserve(1, 10)
        partial = self.revalidate(
            1, [CreditSourceBalance(SOURCE_A, 6)], op=70,
        )
        self.assertEqual(partial.reservation_revision, 2)

        released = self.enabled.release(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(71),
            expected_revision=1,
            as_of="2026-08-11T10:03:00Z",
        )
        self.assertEqual(released.reservation_status, "released")
        self.assertEqual(released.reservation_revision, 3)
        self.assertTrue(released.terminal_satisfied)
        self.assertFalse(released.allocation_satisfied)
        self.assertFalse(released.fully_funded)
        self.assertEqual(
            self.enabled.release(
                account_key=ACCOUNT,
                reservation_id=reservation(1),
                operation_id=operation(71),
                expected_revision=1,
                as_of="2026-08-11T10:03:00Z",
            ),
            released,
        )
        with self.assertRaises(CreditAccountingConflict):
            self.enabled.release(
                account_key=ACCOUNT,
                reservation_id=reservation(1),
                operation_id=operation(71),
                expected_revision=2,
                as_of="2026-08-11T10:03:00Z",
            )
        already_terminal = self.enabled.release(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(72),
            expected_revision=1,
            as_of="2026-08-11T10:04:00Z",
        )
        self.assertEqual(already_terminal.reservation_status, "released")
        self.assertEqual(already_terminal.reservation_revision, 3)
        self.assertTrue(already_terminal.terminal_satisfied)

    def test_release_satisfies_expired_invalidated_and_rejects_consumed_or_future(self):
        expiring = [
            CreditSourceBalance(SOURCE_A, 10, "2026-08-11T10:05:00Z"),
        ]
        self.reconcile(expiring)
        self.reserve(1, 10)
        invalidated = self.revalidate(
            1, expiring, op=73, at="2026-08-11T10:05:00Z",
        )
        self.assertEqual(invalidated.reservation_status, "invalidated")
        satisfied = self.enabled.release(
            account_key=ACCOUNT,
            reservation_id=reservation(1),
            operation_id=operation(74),
            expected_revision=1,
            as_of="2026-08-11T10:06:00Z",
        )
        self.assertEqual(satisfied.reservation_status, "invalidated")
        self.assertEqual(satisfied.reservation_revision, 2)
        self.assertTrue(satisfied.terminal_satisfied)

        self.reconcile([CreditSourceBalance(SOURCE_B, 10)], op=75)
        self.reserve(2, 10)
        self.transition("consume", 2, 76)
        with self.assertRaises(CreditAccountingConflict):
            self.enabled.release(
                account_key=ACCOUNT,
                reservation_id=reservation(2),
                operation_id=operation(77),
                expected_revision=1,
                as_of="2026-08-11T10:07:00Z",
            )
        with self.assertRaises(CreditAccountingConflict):
            self.enabled.release(
                account_key=ACCOUNT,
                reservation_id=reservation(1),
                operation_id=operation(78),
                expected_revision=99,
                as_of="2026-08-11T10:07:00Z",
            )

    def test_terminal_revalidation_uses_consistent_current_funding_semantics(self):
        sources = [CreditSourceBalance(SOURCE_A, 10)]
        self.reconcile(sources)
        self.reserve(1, 10)
        consumed = self.transition("consume", 1, 55)
        revalidated_consumed = self.revalidate(1, sources, op=56)
        self.assertFalse(consumed.fully_funded)
        self.assertFalse(revalidated_consumed.fully_funded)

        self.reconcile([CreditSourceBalance(SOURCE_B, 10)], op=57)
        self.reserve(2, 10)
        released = self.transition("release", 2, 58)
        revalidated_released = self.revalidate(
            2, [CreditSourceBalance(SOURCE_B, 10)], op=59,
        )
        self.assertFalse(released.fully_funded)
        self.assertFalse(revalidated_released.fully_funded)

    def test_consumed_units_never_resurrect_after_refund_and_regrant(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        self.reserve(1, 10)
        self.transition("consume", 1, 50)
        refunded = self.revalidate(
            1, [CreditSourceBalance(SOURCE_A, 0)], op=51,
        )
        self.assertEqual(refunded.reservation_status, "consumed")
        self.assertEqual(refunded.affected_units, 10)
        regranted = self.revalidate(
            1, [CreditSourceBalance(SOURCE_A, 10)], op=52,
        )
        self.assertEqual(regranted.reservation_status, "consumed")
        projection = self.enabled.public_projection(ACCOUNT)
        self.assertEqual(projection["consumed_units"], 10)
        self.assertEqual(projection["available_units"], 0)

    def test_consume_and_release_require_pending_and_release_restores_exact_lots(self):
        self.reconcile([
            CreditSourceBalance(SOURCE_A, 10, "2026-08-11T12:00:00Z"),
            CreditSourceBalance(SOURCE_B, 10, "2026-08-11T11:00:00Z"),
        ])
        self.reserve(1, 15)
        released = self.transition("release", 1, 30)
        self.assertEqual(released.affected_units, 15)
        self.assertEqual(self.enabled.public_projection(ACCOUNT)["available_units"], 20)
        with self.assertRaises(CreditAccountingConflict):
            self.transition("consume", 1, 31)

        self.reserve(2, 15)
        consumed = self.transition("consume", 2, 32)
        self.assertEqual(consumed.reservation_status, "consumed")
        self.assertEqual(self.enabled.public_projection(ACCOUNT)["consumed_units"], 15)
        with self.assertRaises(CreditAccountingConflict):
            self.transition("release", 2, 33)

    def test_released_reallocated_stale_consume_fails(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        self.reserve(1, 10)
        self.transition("release", 1, 30)
        self.reserve(2, 10)
        with self.assertRaises(CreditAccountingConflict):
            self.transition("consume", 1, 31)
        self.assertEqual(self.transition("consume", 2, 32).affected_units, 10)

    def test_refund_and_expiry_trim_pending_without_resurrecting_consumed(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 100)])
        self.reserve(1, 60)
        self.transition("consume", 1, 30)
        self.reserve(2, 40)

        self.reconcile([CreditSourceBalance(SOURCE_A, 70)], op=2)
        projection = self.enabled.public_projection(ACCOUNT)
        self.assertEqual(projection["consumed_units"], 60)
        self.assertEqual(projection["reserved_units"], 10)
        self.reconcile([CreditSourceBalance(SOURCE_A, 0)], op=3)
        projection = self.enabled.public_projection(ACCOUNT)
        self.assertEqual(projection["consumed_units"], 60)
        self.assertEqual(projection["reserved_units"], 0)
        with self.assertRaises(CreditAccountingConflict):
            self.transition("consume", 2, 31)

        self.reconcile([CreditSourceBalance(SOURCE_A, 100)], op=4)
        self.assertEqual(self.enabled.public_projection(ACCOUNT)["available_units"], 40)
        self.reconcile(
            [CreditSourceBalance(SOURCE_A, 100, "2026-08-11T11:00:00Z")],
            op=5,
        )
        self.reserve(3, 20, at="2026-08-11T10:30:00Z")
        self.reconcile(
            [CreditSourceBalance(SOURCE_A, 100, "2026-08-11T11:00:00Z")],
            op=6,
            at="2026-08-11T11:00:00Z",
        )
        self.assertEqual(self.enabled.public_projection(ACCOUNT)["reserved_units"], 0)

    def test_clock_high_water_never_moves_backward(self):
        self.reconcile(
            [CreditSourceBalance(SOURCE_A, 10, "2026-08-11T11:00:00Z")],
            at="2026-08-11T12:00:00Z",
        )
        self.reconcile(
            [CreditSourceBalance(SOURCE_A, 10, "2026-08-11T11:00:00Z")],
            op=2,
            at="2026-08-11T09:00:00Z",
        )
        projection = self.enabled.public_projection(ACCOUNT)
        self.assertEqual(projection["clock_high_water"], "2026-08-11T12:00:00Z")
        self.assertEqual(projection["available_units"], 0)

    def test_fractional_timestamps_are_not_rounded_into_early_expiry_or_replay(self):
        self.reconcile(
            [CreditSourceBalance(SOURCE_A, 10, "2026-08-11T10:00:00.900Z")],
            at="2026-08-11T10:00:00.100Z",
        )
        projection = self.enabled.public_projection(ACCOUNT)
        self.assertEqual(projection["available_units"], 10)
        self.assertEqual(
            projection["clock_high_water"], "2026-08-11T10:00:00.100000Z",
        )
        with self.assertRaises(CreditAccountingConflict):
            self.enabled.reconcile(
                account_key=ACCOUNT,
                operation_id=operation(1),
                unit="compute_seconds",
                sources=[
                    CreditSourceBalance(
                        SOURCE_A, 10, "2026-08-11T10:00:00.900Z",
                    ),
                ],
                as_of="2026-08-11T10:00:00.900Z",
            )

    def test_fractional_expiry_order_uses_time_not_timestamp_text(self):
        self.reconcile([
            CreditSourceBalance(SOURCE_A, 5, "2026-08-11T11:00:00.100Z"),
            CreditSourceBalance(SOURCE_B, 5, "2026-08-11T11:00:00Z"),
        ])
        self.reserve(1, 5)
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        allocations = envelope["state"]["accounts"][ACCOUNT]["reservations"][
            reservation(1)
        ]["allocations"]
        self.assertEqual(allocations, [{"source_key": SOURCE_B, "units": 5}])

    def test_real_processes_share_one_account_wide_atomic_reservation(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        context = multiprocessing.get_context("spawn")
        gate = context.Barrier(2)
        results = context.Queue()
        processes = [
            context.Process(
                target=_process_reserve,
                args=(str(self.path), index, gate, results),
            )
            for index in (1, 2)
        ]
        for process in processes:
            process.start()
        values = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(values), [2, 8])
        projection = self.enabled.public_projection(ACCOUNT)
        self.assertEqual(projection["reserved_units"], 10)
        self.assertEqual(projection["available_units"], 0)

    def test_concurrent_revalidate_and_guarded_consume_serialize_safely(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        self.reserve(1, 10)
        context = multiprocessing.get_context("spawn")
        gate = context.Barrier(2)
        results = context.Queue()
        processes = [
            context.Process(
                target=_process_revalidate,
                args=(str(self.path), gate, results),
            ),
            context.Process(
                target=_process_consume,
                args=(str(self.path), gate, results),
            ),
        ]
        for process in processes:
            process.start()
        values = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
            self.assertEqual(process.exitcode, 0)

        projection = self.enabled.public_projection(ACCOUNT)
        consume = next(item for item in values if item[0].startswith("consume"))
        revalidated = next(
            item for item in values if item[0].startswith("revalidate")
        )
        if consume[0] == "consume":
            self.assertEqual(consume[1:], (10, 2))
            self.assertEqual(revalidated[1], "consumed")
            self.assertEqual(projection["consumed_units"], 10)
            self.assertEqual(projection["reserved_units"], 0)
        else:
            self.assertEqual(consume, ("consume_error", "CreditAccountingConflict"))
            self.assertEqual(revalidated[1:], ("pending", 5, 2))
            self.assertEqual(projection["consumed_units"], 0)
            self.assertEqual(projection["reserved_units"], 5)

    def test_corruption_unsafe_paths_bounds_and_lock_failure_fail_closed(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["state"]["accounts"][ACCOUNT]["sources"][SOURCE_A][
            "effective_units"
        ] = 999
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(CreditAccountingIntegrityError):
            self.enabled.public_projection(ACCOUNT)

        bounded = CreditAccountingJournal(
            Path(self.temporary.name) / "bounded.json",
            integrity_key=SECRET,
            policy=CreditAccountingPolicy(enforcement_enabled=True),
            max_state_bytes=4_096,
        )
        bounded.path.write_bytes(b"x" * 4_097)
        with self.assertRaises(CreditAccountingIntegrityError):
            bounded.public_projection(ACCOUNT)

        unsafe = Path(self.temporary.name) / "unsafe.json"
        unsafe.symlink_to(self.path)
        unsafe_journal = CreditAccountingJournal(
            unsafe,
            integrity_key=SECRET,
            policy=CreditAccountingPolicy(enforcement_enabled=True),
        )
        with self.assertRaises(CreditAccountingIntegrityError):
            unsafe_journal.public_projection(ACCOUNT)

        with patch(
            "services.credit_accounting.exclusive_file_lease",
            side_effect=ExclusiveLeaseError("held"),
        ), self.assertRaises(ExclusiveLeaseError):
            self.enabled.public_projection(ACCOUNT)

    def test_authenticated_cross_field_accounting_inconsistency_fails_closed(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        state = payload["state"]
        state["accounts"][ACCOUNT]["sources"][SOURCE_A]["consumed_units"] = 5
        payload["state_hmac"] = self.enabled._seal(state)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(CreditAccountingIntegrityError):
            self.enabled.public_projection(ACCOUNT)

    def test_authenticated_empty_terminal_reservation_fails_closed(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        self.reserve(1, 5)
        self.transition("release", 1, 30)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        state = payload["state"]
        state["accounts"][ACCOUNT]["reservations"][reservation(1)][
            "allocations"
        ] = []
        payload["state_hmac"] = self.enabled._seal(state)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(CreditAccountingIntegrityError):
            self.enabled.public_projection(ACCOUNT)

    def test_authenticated_impossible_operation_receipts_fail_closed(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        self.reserve(1, 10)
        original = json.loads(self.path.read_text(encoding="utf-8"))
        operation_entry = original["state"]["operations"][operation(11)][
            "receipt"
        ]
        mutations = (
            {"reservation_revision": None},
            {"reservation_status": None},
            {"affected_units": 11},
            {"requested_units": 0},
            {"state": "invented_state"},
            {"state": "consumed"},
            {"state": "disabled"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = json.loads(json.dumps(original))
                receipt = payload["state"]["operations"][operation(11)][
                    "receipt"
                ]
                self.assertEqual(receipt, operation_entry)
                receipt.update(mutation)
                payload["state_hmac"] = self.enabled._seal(payload["state"])
                self.path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(CreditAccountingIntegrityError):
                    self.enabled.public_projection(ACCOUNT)

    def test_timestamp_precision_beyond_microseconds_is_rejected(self):
        for timestamp in (
            "2026-08-11T10:00:00.1234567Z",
            "2026-08-11 10:00:00.1234567+00:00",
            "2026-08-11t10:00:00,1234567+00:00",
            "20260811T100000.1234567+0000",
            "2026-08-11T100000.1234567+00:00",
            "2026-08-11T10:00:00+01:02.1234567",
        ):
            with self.subTest(timestamp=timestamp), self.assertRaises(
                CreditAccountingError,
            ):
                self.reconcile(
                    [CreditSourceBalance(SOURCE_A, 10)],
                    at=timestamp,
                )

    def test_projection_and_persistence_are_content_free(self):
        self.reconcile([CreditSourceBalance(SOURCE_A, 10)])
        self.reserve(1, 4)
        projection = self.enabled.public_projection(ACCOUNT)
        self.assertEqual(set(projection), {
            "schema_version", "state", "enforcement_enabled", "unit",
            "clock_high_water", "available_units", "reserved_units",
            "consumed_units", "pending_reservations",
        })
        encoded = self.path.read_text(encoding="utf-8")
        for forbidden in (
            "prompt", "job", "media", "output", "provider", "email", "name",
        ):
            self.assertNotIn(forbidden, encoded.lower())

    def test_supporter_tier_bonus_is_an_opaque_non_monetary_source_lot(self):
        supporter_bonus = "key_" + hashlib.sha256(
            b"supporter-tier-bonus-grant",
        ).hexdigest()
        self.reconcile([
            CreditSourceBalance(
                supporter_bonus,
                25,
                "2026-09-11T10:00:00Z",
            ),
        ])
        reserved = self.reserve(1, 20)
        self.assertEqual(reserved.affected_units, 20)
        encoded = self.path.read_text(encoding="utf-8").lower()
        for forbidden in (
            "amount_minor", "currency", "cash_value", "purchase",
            "transferable", "refundable", "guaranteed_compute",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_only_opaque_keyed_ids_and_integer_units_are_accepted(self):
        with self.assertRaises(CreditAccountingError):
            self.enabled.reconcile(
                account_key="person@example.com",
                operation_id=operation(1),
                unit="compute_seconds",
                sources=[],
                as_of="2026-08-11T10:00:00Z",
            )
        with self.assertRaises(CreditAccountingError):
            self.reconcile([CreditSourceBalance("raw-source", 1)])
        with self.assertRaises(CreditAccountingError):
            self.reconcile([CreditSourceBalance(SOURCE_A, True)])


if __name__ == "__main__":
    unittest.main()
