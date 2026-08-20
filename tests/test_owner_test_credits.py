"""Focused owner test-credit accounting regressions."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.owner_test_credits import (  # noqa: E402
    OwnerTestCreditError,
    OwnerTestCreditLedger,
)


def _key(label: str) -> str:
    return "key_" + hashlib.sha256(label.encode("utf-8")).hexdigest()


class OwnerTestCreditLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ledger = OwnerTestCreditLedger(
            Path(self.temporary.name) / "owner-test.json",
            integrity_key=b"owner-test-integrity-key-32-bytes!",
            target_balance=10,
        )
        self.account = _key("account")
        self.source = _key("source")

    def dispatch(self, job: str, units: int):
        return self.ledger.record_dispatch(
            account_key=self.account,
            source_key=self.source,
            job_key=job,
            requested_units=units,
            as_of="2026-08-18T18:00:00Z",
        )

    def settle(self, job: str, receipt, status: str, elapsed: float = 0):
        return self.ledger.settle(
            account_key=self.account,
            job_key=job,
            reservation_id=receipt.reservation_id,
            expected_revision=receipt.reservation_revision,
            terminal_status=status,
            started_at=100.0,
            finished_at=100.0 + elapsed,
        )

    def test_initial_projection_is_visible_and_auto_refilling(self):
        projection = self.ledger.public_projection(self.account)
        self.assertEqual(projection["state"], "active")
        self.assertTrue(projection["test_only"])
        self.assertTrue(projection["auto_top_up"])
        self.assertEqual(projection["available_units"], 10)
        self.assertEqual(projection["used_units"], 0)

    def test_completed_failed_and_cancelled_settlement_match_real_semantics(self):
        completed = self.dispatch("completed", 6)
        projection = self.settle("completed", completed, "completed")
        self.assertEqual(projection["available_units"], 4)
        self.assertEqual(projection["used_units"], 6)

        failed = self.dispatch("failed", 3)
        projection = self.settle("failed", failed, "failed")
        self.assertEqual(projection["available_units"], 4)
        self.assertEqual(projection["used_units"], 6)

        cancelled = self.dispatch("cancelled", 4)
        projection = self.settle(
            "cancelled", cancelled, "cancelled", elapsed=1.2,
        )
        self.assertEqual(projection["available_units"], 2)
        self.assertEqual(projection["used_units"], 8)

    def test_low_balance_auto_refills_before_dispatch(self):
        first = self.dispatch("first", 10)
        self.settle("first", first, "completed")
        second = self.dispatch("second", 3)
        projection = self.settle("second", second, "completed")
        self.assertEqual(projection["available_units"], 7)
        self.assertEqual(projection["used_units"], 13)

    def test_projection_stays_test_only_and_does_not_echo_job_keys(self):
        projection = self.ledger.public_projection(self.account)
        self.assertEqual(projection["schema_version"], 1)
        self.assertEqual(projection["unit"], "maestro_test_credits")
        self.assertEqual(projection["target_balance"], 10)
        self.assertTrue(projection["test_only"])
        self.assertNotIn("admission", projection)
        self.assertNotIn("priority", projection)
        receipt = self.dispatch("large-over-target", 25)
        self.assertTrue(receipt.reservation_id.startswith("reservation_"))
        self.assertNotIn("large-over-target", receipt.reservation_id)
        self.assertNotIn(self.account, receipt.reservation_id)
        after = self.ledger.public_projection(self.account)
        self.assertEqual(after["available_units"], 0)
        self.assertEqual(after["used_units"], 25)
        self.assertTrue(after["test_only"])

    def test_invalid_request_and_cancel_without_start_fail_closed(self):
        with self.assertRaisesRegex(OwnerTestCreditError, "test credit request is invalid"):
            self.dispatch("zero", 0)
        with self.assertRaisesRegex(OwnerTestCreditError, "test credit request is invalid"):
            self.dispatch("bool", True)
        with self.assertRaisesRegex(OwnerTestCreditError, "test target balance is invalid"):
            OwnerTestCreditLedger(
                Path(self.temporary.name) / "bad-target.json",
                integrity_key=b"owner-test-integrity-key-32-bytes!",
                target_balance=0,
            )
        receipt = self.dispatch("cancel-no-start", 2)
        with self.assertRaisesRegex(
            OwnerTestCreditError,
            "cancelled test credit settlement has no start time",
        ):
            self.ledger.settle(
                account_key=self.account,
                job_key="cancel-no-start",
                reservation_id=receipt.reservation_id,
                expected_revision=receipt.reservation_revision,
                terminal_status="cancelled",
                started_at=None,
                finished_at=101.0,
            )
        still = self.ledger.public_projection(self.account)
        self.assertEqual(still["used_units"], 2)
        self.assertTrue(still["test_only"])


if __name__ == "__main__":
    unittest.main()
