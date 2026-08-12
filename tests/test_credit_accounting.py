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

    def transition(self, kind: str, index: int, op: int, *, at="2026-08-11T10:02:00Z"):
        return getattr(self.enabled, kind)(
            account_key=ACCOUNT,
            reservation_id=reservation(index),
            operation_id=operation(op),
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
