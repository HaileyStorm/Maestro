"""Offline contribution-ledger, entitlement, and privacy regressions."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.entitlements import (  # noqa: E402
    ContributionConflict,
    ContributionEventDraft,
    ContributionLedger,
    EntitlementError,
    LedgerIntegrityError,
    opaque_key,
    support_priority_capability_marker,
)
from services import entitlements  # noqa: E402
from services.host_terms import (  # noqa: E402
    KREA2_MOODY_CUTIE_V4_RECIPE_ID,
    KREA2_MOODY_MIX_V7_RECIPE_ID,
)


NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
KEY = b"ledger-integrity-key-for-tests-000000000000"
IDENTITY_KEY = b"identity-key-for-support-tests-00000000000"


def keyed(namespace: str, value: str) -> str:
    return opaque_key(namespace, value, IDENTITY_KEY)


class ContributionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "contributions.json"
        self.ledger = ContributionLedger(
            self.path, integrity_key=KEY, allow_test_path=True,
        )
        self.subject = keyed("test_subject", "private-user@example.test")

    def tearDown(self):
        self.temp.cleanup()

    def draft(
        self,
        event_id: str,
        kind: str,
        *,
        occurred_at: str = "2026-08-11T08:00:00Z",
        amount: int = 0,
        currency: str = "USD",
        contract: str | None = None,
        related: str | None = None,
        item: str | None = None,
        state: str | None = None,
        actor: str | None = None,
    ) -> ContributionEventDraft:
        return ContributionEventDraft(
            provider="fake_support",
            source_event_key=keyed("fake_event", event_id),
            subject_key=self.subject,
            kind=kind,
            occurred_at=occurred_at,
            amount_minor=amount,
            currency=currency,
            contract_key=None if contract is None else keyed("fake_contract", contract),
            related_event_key=None if related is None else keyed("fake_event", related),
            fulfillment_item=item,
            fulfillment_status=state,
            actor_key=None if actor is None else keyed("admin_actor", actor),
        )

    def test_append_is_immutable_idempotent_and_conflicts_fail_closed(self):
        draft = self.draft("evt-1", "one_time_contribution", amount=2_500)
        first = self.ledger.append(draft, received_at=NOW)
        replay = self.ledger.append(
            draft, received_at="2026-08-11T09:05:00Z",
        )
        self.assertEqual(replay, first)
        self.assertEqual(len(self.ledger.events()), 1)
        with self.assertRaises(FrozenInstanceError):
            first.amount_minor = 1
        with self.assertRaises(ContributionConflict):
            self.ledger.append(
                self.draft("evt-1", "one_time_contribution", amount=2_501),
                received_at=NOW,
            )

    def test_restart_verifies_integrity_and_tampering_fails_closed(self):
        self.ledger.append(
            self.draft("evt-1", "one_time_contribution", amount=500),
            received_at=NOW,
        )
        restarted = ContributionLedger(
            self.path, integrity_key=KEY, allow_test_path=True,
        )
        self.assertEqual(len(restarted.events()), 1)
        with self.assertRaises(LedgerIntegrityError):
            ContributionLedger(
                self.path,
                integrity_key=b"different-ledger-key-0000000000000000000",
                allow_test_path=True,
            ).events()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["events"][0]["amount_minor"] = 9_999
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(LedgerIntegrityError):
            restarted.events()

    def test_publication_size_rejection_preserves_exact_previous_ledger(self):
        self.ledger.append(
            self.draft("first", "one_time_contribution", amount=500),
            received_at=NOW,
        )
        before = self.path.read_bytes()
        with mock.patch.object(
            entitlements, "MAX_LEDGER_BYTES", len(before) + 64,
        ):
            with self.assertRaisesRegex(EntitlementError, "byte bound"):
                self.ledger.append(
                    self.draft(
                        "second-event-with-a-longer-opaque-source",
                        "one_time_contribution",
                        amount=500,
                    ),
                    received_at="2026-08-11T09:01:00Z",
                )
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.ledger.events()), 1)

    def test_two_instances_serialize_appends_without_lost_updates(self):
        other = ContributionLedger(
            self.path, integrity_key=KEY, allow_test_path=True,
        )
        entered = threading.Event()
        release = threading.Event()
        first_write = self.ledger._write_unlocked
        errors: list[BaseException] = []

        def blocking_write(events):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test write release timed out")
            first_write(events)

        def append(target, draft, received_at):
            try:
                target.append(draft, received_at=received_at)
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(self.ledger, "_write_unlocked", blocking_write):
            first_thread = threading.Thread(target=append, args=(
                self.ledger,
                self.draft("first", "one_time_contribution", amount=500),
                "2026-08-11T09:00:00Z",
            ))
            second_thread = threading.Thread(target=append, args=(
                other,
                self.draft("second", "one_time_contribution", amount=500),
                "2026-08-11T09:01:00Z",
            ))
            first_thread.start()
            self.assertTrue(entered.wait(1))
            second_thread.start()
            time.sleep(0.05)
            self.assertTrue(second_thread.is_alive())
            release.set()
            first_thread.join(2)
            second_thread.join(2)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(self.ledger.events()), 2)

    def test_unsafe_exclusive_lock_path_fails_closed(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.lock_path.mkdir()
        with self.assertRaisesRegex(EntitlementError, "lock path"):
            self.ledger.append(
                self.draft("blocked", "one_time_contribution", amount=500),
                received_at=NOW,
            )
        self.assertFalse(self.path.exists())

    def test_windows_native_lock_backend_and_missing_fchmod_are_supported(self):
        descriptor = os.open(
            self.path.parent / "windows.lock",
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        calls = []
        fake_msvcrt = types.SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            locking=lambda fd, mode, count: calls.append((fd, mode, count)),
        )
        try:
            with (
                mock.patch.object(entitlements.os, "name", "nt"),
                mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
            ):
                backend = entitlements._acquire_native_lock(
                    descriptor, timeout_seconds=0.1, poll_seconds=0.01,
                )
                self.assertEqual(backend, "windows")
                entitlements._release_native_lock(descriptor, backend)
        finally:
            os.close(descriptor)
        self.assertEqual([mode for _, mode, _ in calls], [1, 2])
        with mock.patch.object(entitlements.os, "fchmod", None):
            self.ledger.append(
                self.draft("windows-write", "one_time_contribution", amount=500),
                received_at=NOW,
            )
        self.assertEqual(len(self.ledger.events()), 1)

    def test_out_of_order_refund_and_cancel_project_by_event_time(self):
        # Provider delivery order is deliberately the reverse of semantic order.
        self.ledger.append(self.draft(
            "refund-1", "refund", amount=1_000, related="gift-1",
            occurred_at="2026-08-11T08:30:00Z",
        ), received_at="2026-08-11T09:00:00Z")
        self.ledger.append(self.draft(
            "cancel-1", "recurring_canceled", contract="membership-1",
            occurred_at="2026-08-11T08:45:00Z",
        ), received_at="2026-08-11T09:01:00Z")
        self.ledger.append(self.draft(
            "start-1", "recurring_started", amount=1_000,
            contract="membership-1", occurred_at="2026-08-11T08:00:00Z",
        ), received_at="2026-08-11T09:02:00Z")
        self.ledger.append(self.draft(
            "gift-1", "one_time_contribution", amount=2_500,
            occurred_at="2026-08-11T07:30:00Z",
        ), received_at="2026-08-11T09:03:00Z")

        projection = self.ledger.privacy_safe_user_projection(self.subject)
        self.assertEqual(projection["currency_totals_minor"], {"USD": 2_500})
        self.assertEqual(projection["one_time_tier"], "supporter")
        self.assertEqual(projection["active_recurring_count"], 0)
        self.assertIsNone(projection["recurring_tier"])
        admin = self.ledger.reauthenticated_admin_projection(self.subject)
        self.assertEqual(admin["unresolved"], [])
        self.assertEqual(len(admin["audit"]), 4)

    def test_projection_keys_identical_opaque_values_by_provider(self):
        first = self.draft(
            "shared-event", "recurring_started", amount=1_000,
            contract="shared-contract", occurred_at="2026-08-11T08:00:00Z",
        )
        second = replace(first, provider="other_support", amount_minor=2_500)
        self.ledger.append(first, received_at="2026-08-11T09:00:00Z")
        self.ledger.append(second, received_at="2026-08-11T09:01:00Z")
        self.ledger.append(self.draft(
            "refund-first", "refund", amount=500, related="shared-event",
            occurred_at="2026-08-11T08:15:00Z",
        ), received_at="2026-08-11T09:02:00Z")
        self.ledger.append(self.draft(
            "cancel-first", "recurring_canceled", contract="shared-contract",
            occurred_at="2026-08-11T08:30:00Z",
        ), received_at="2026-08-11T09:03:00Z")
        projection = self.ledger.privacy_safe_user_projection(self.subject)
        self.assertEqual(projection["currency_totals_minor"], {"USD": 3_000})
        self.assertEqual(projection["active_recurring_count"], 1)
        self.assertEqual(projection["recurring_tier"], "patron")

    def test_refund_and_chargeback_are_compensating_not_destructive(self):
        self.ledger.append(self.draft(
            "gift", "one_time_contribution", amount=10_000,
        ), received_at=NOW)
        self.ledger.append(self.draft(
            "refund", "refund", amount=2_000, related="gift",
        ), received_at="2026-08-11T09:01:00Z")
        self.ledger.append(self.draft(
            "chargeback", "chargeback", amount=3_000, related="gift",
        ), received_at="2026-08-11T09:02:00Z")
        projection = self.ledger.privacy_safe_user_projection(self.subject)
        self.assertEqual(projection["currency_totals_minor"], {"USD": 5_000})
        self.assertEqual(projection["event_count"], 3)
        original = self.ledger.events()[0]
        self.assertEqual(original.kind, "one_time_contribution")
        self.assertEqual(original.amount_minor, 10_000)

    def test_manual_fulfillment_has_private_user_and_opaque_admin_views(self):
        self.ledger.append(self.draft(
            "gift", "one_time_contribution", amount=2_500,
        ), received_at=NOW)
        self.ledger.append(self.draft(
            "fulfill-1", "fulfillment_set", related="gift",
            item="one_time_credit_grant", state="complete", actor="owner-1",
        ), received_at="2026-08-11T09:01:00Z")
        user = self.ledger.privacy_safe_user_projection(self.subject)
        self.assertEqual(user["fulfillment"], [{
            "target_event_id": self.ledger.events()[0].event_id,
            "item": "one_time_credit_grant",
            "status": "complete",
        }])
        serialized_user = json.dumps(user, sort_keys=True)
        self.assertNotIn(self.subject, serialized_user)
        self.assertNotIn("actor_key", serialized_user)
        self.assertNotIn("source_event_key", serialized_user)
        self.assertNotIn("private-user@example.test", self.path.read_text(encoding="utf-8"))
        admin = self.ledger.reauthenticated_admin_projection(self.subject)
        self.assertEqual(admin["subject_key"], self.subject)
        self.assertRegex(admin["fulfillment"][0]["actor_key"], r"^key_[0-9a-f]{64}$")
        self.assertNotIn("owner-1", json.dumps(admin))

    def test_delayed_older_fulfillment_does_not_replace_newer_status(self):
        self.ledger.append(self.draft(
            "gift", "one_time_contribution", amount=2_500,
        ), received_at=NOW)
        self.ledger.append(self.draft(
            "fulfilled-new", "fulfillment_set", related="gift",
            item="one_time_credit_grant", state="complete", actor="owner-1",
            occurred_at="2026-08-11T08:30:00Z",
        ), received_at="2026-08-11T09:01:00Z")
        self.ledger.append(self.draft(
            "fulfilled-old", "fulfillment_set", related="gift",
            item="one_time_credit_grant", state="pending", actor="owner-1",
            occurred_at="2026-08-11T08:15:00Z",
        ), received_at="2026-08-11T09:02:00Z")
        projection = self.ledger.privacy_safe_user_projection(self.subject)
        self.assertEqual(projection["fulfillment"][0]["status"], "complete")

    def test_malformed_events_and_orphan_adjustments_are_bounded(self):
        with self.assertRaises(EntitlementError):
            self.ledger.append(self.draft("bad", "refund", amount=100))
        with self.assertRaises(EntitlementError):
            self.ledger.append(self.draft(
                "bad-cancel", "recurring_canceled",
            ))
        self.ledger.append(self.draft(
            "orphan", "chargeback", amount=100, related="missing",
        ), received_at=NOW)
        user = self.ledger.privacy_safe_user_projection(self.subject)
        self.assertEqual(user["currency_totals_minor"], {})
        self.assertNotIn("unresolved", user)
        admin = self.ledger.reauthenticated_admin_projection(self.subject)
        self.assertEqual(
            admin["unresolved"][0]["reason"],
            "unresolved_or_mismatched_adjustment",
        )

    def test_moody_exclusion_is_exact_server_identity_not_content_classification(self):
        for capability_id in (
            KREA2_MOODY_MIX_V7_RECIPE_ID,
            KREA2_MOODY_CUTIE_V4_RECIPE_ID,
        ):
            marker = support_priority_capability_marker(capability_id)
            self.assertFalse(marker["support_priority_eligible"])
            self.assertEqual(
                marker["marker"], "creator_terms_exclude_support_priority",
            )
        ordinary = support_priority_capability_marker("ordinary-model")
        self.assertTrue(ordinary["support_priority_eligible"])
        self.assertNotIn("prompt", json.dumps(ordinary).lower())

    def test_ledger_schema_has_no_content_or_contact_fields(self):
        fields = set(ContributionEventDraft.__dataclass_fields__)
        forbidden = {
            "prompt", "media", "job", "output", "log", "email", "name",
            "content", "explicit", "safety",
        }
        self.assertTrue(fields.isdisjoint(forbidden))


if __name__ == "__main__":
    unittest.main()
