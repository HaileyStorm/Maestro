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
    AllowanceRule,
    BenefitPolicy,
    ContributionConflict,
    ContributionEventDraft,
    ContributionLedger,
    EntitlementError,
    LedgerIntegrityError,
    ManualContributionConflict,
    RecordedAllowancePolicy,
    DEFAULT_BENEFIT_POLICY,
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

    def test_verified_links_resolve_one_provider_subject_to_one_account(self):
        provider_subject = keyed("fake_support_subject", "provider-user")
        funding = replace(
            self.draft("linked-gift", "one_time_contribution", amount=2_500),
            subject_key=provider_subject,
        )
        stored_funding = self.ledger.append(
            funding, received_at="2026-08-11T08:01:00Z",
        )
        other_subject = keyed("test_subject", "other-account")
        self.ledger.append(ContributionEventDraft(
            provider="fake_support",
            source_event_key=keyed("fake_event", "link-other"),
            subject_key=other_subject,
            kind="account_link_verified",
            occurred_at="2026-08-11T08:20:00Z",
            contract_key=keyed("account_claim", "other-account"),
            related_event_key=provider_subject,
        ), received_at="2026-08-11T08:30:00Z")
        # An older same-owner verification may arrive later without changing ownership.
        self.ledger.append(ContributionEventDraft(
            provider="fake_support",
            source_event_key=keyed("fake_event", "link-other-older"),
            subject_key=other_subject,
            kind="account_link_verified",
            occurred_at="2026-08-11T08:10:00Z",
            contract_key=keyed("account_claim", "other-account"),
            related_event_key=provider_subject,
        ), received_at="2026-08-11T08:31:00Z")

        mine = self.ledger.privacy_safe_user_projection(self.subject, as_of=NOW)
        other = self.ledger.privacy_safe_user_projection(other_subject, as_of=NOW)
        self.assertEqual(mine["currency_totals_minor"], {})
        self.assertEqual(other["currency_totals_minor"], {"USD": 2_500})
        self.assertNotIn(provider_subject, json.dumps(other))
        linked_fulfillment = self.ledger.transition_fulfillment(
            subject_key=other_subject,
            target_event_id=stored_funding.event_id,
            item="one_time_credit_grant",
            status="pending",
            source_event_key=keyed(
                "fulfillment_request", "linked-provider-subject",
            ),
            actor_key=keyed("admin_actor", "owner-1"),
            occurred_at="2026-08-11T08:32:00Z",
            received_at="2026-08-11T08:32:01Z",
        )
        self.assertEqual(linked_fulfillment.subject_key, provider_subject)
        self.assertEqual(linked_fulfillment.provider, stored_funding.provider)
        self.assertEqual(
            linked_fulfillment.related_event_key,
            stored_funding.source_event_key,
        )
        linked_completed = self.ledger.transition_fulfillment(
            subject_key=other_subject,
            target_event_id=stored_funding.event_id,
            item="one_time_credit_grant",
            status="fulfilled",
            source_event_key=keyed(
                "fulfillment_request", "linked-provider-subject-complete",
            ),
            actor_key=keyed("admin_actor", "owner-1"),
            occurred_at="2026-08-11T08:33:00Z",
            received_at="2026-08-11T08:33:01Z",
        )
        self.assertEqual(linked_completed.fulfillment_status, "fulfilled")

        before_rejected_transitions = self.path.read_bytes()
        with self.assertRaisesRegex(EntitlementError, "current owner"):
            self.ledger.append(ContributionEventDraft(
                provider="fake_support",
                source_event_key=keyed("fake_event", "foreign-revoke"),
                subject_key=self.subject,
                kind="account_link_revoked",
                occurred_at="2026-08-11T08:35:00Z",
                contract_key=keyed("account_claim", "private-user@example.test"),
                related_event_key=provider_subject,
            ), received_at="2026-08-11T08:36:00Z")
        with self.assertRaisesRegex(EntitlementError, "owner revocation"):
            self.ledger.append(ContributionEventDraft(
                provider="fake_support",
                source_event_key=keyed("fake_event", "foreign-steal"),
                subject_key=self.subject,
                kind="account_link_verified",
                occurred_at="2026-08-11T08:36:00Z",
                contract_key=keyed("account_claim", "private-user@example.test"),
                related_event_key=provider_subject,
            ), received_at="2026-08-11T08:37:00Z")
        self.assertEqual(self.path.read_bytes(), before_rejected_transitions)
        still_other = self.ledger.privacy_safe_user_projection(
            other_subject, as_of=NOW,
        )
        self.assertEqual(still_other["currency_totals_minor"], {"USD": 2_500})
        self.assertEqual(
            self.ledger.privacy_safe_user_projection(
                self.subject, as_of=NOW,
            )["currency_totals_minor"],
            {},
        )

        self.ledger.append(ContributionEventDraft(
            provider="fake_support",
            source_event_key=keyed("fake_event", "unlink-other"),
            subject_key=other_subject,
            kind="account_link_revoked",
            occurred_at="2026-08-11T08:40:00Z",
            contract_key=keyed("account_claim", "other-account"),
            related_event_key=provider_subject,
        ), received_at="2026-08-11T08:41:00Z")
        revoked = self.ledger.privacy_safe_user_projection(
            other_subject, as_of=NOW,
        )
        self.assertEqual(revoked["currency_totals_minor"], {})
        with self.assertRaises(ContributionConflict):
            self.ledger.transition_fulfillment(
                subject_key=other_subject,
                target_event_id=stored_funding.event_id,
                item="retention_follow_up",
                status="pending",
                source_event_key=keyed(
                    "fulfillment_request", "revoked-provider-subject",
                ),
                actor_key=keyed("admin_actor", "owner-1"),
                occurred_at="2026-08-11T08:45:00Z",
                received_at="2026-08-11T08:45:01Z",
            )
        self.assertEqual(self.ledger.events()[0], stored_funding)
        self.ledger.append(ContributionEventDraft(
            provider="fake_support",
            source_event_key=keyed("fake_event", "link-self-after-revoke"),
            subject_key=self.subject,
            kind="account_link_verified",
            occurred_at="2026-08-11T08:50:00Z",
            contract_key=keyed("account_claim", "private-user@example.test"),
            related_event_key=provider_subject,
        ), received_at="2026-08-11T08:51:00Z")
        transferred = self.ledger.privacy_safe_user_projection(
            self.subject, as_of=NOW,
        )
        self.assertEqual(transferred["currency_totals_minor"], {"USD": 2_500})
        with self.assertRaises(ContributionConflict):
            self.ledger.transition_fulfillment(
                subject_key=other_subject,
                target_event_id=stored_funding.event_id,
                item="retention_follow_up",
                status="pending",
                source_event_key=keyed(
                    "fulfillment_request", "transferred-away-provider-subject",
                ),
                actor_key=keyed("admin_actor", "owner-1"),
                occurred_at="2026-08-11T08:55:00Z",
                received_at="2026-08-11T08:55:01Z",
            )
        with self.assertRaises(ContributionConflict):
            self.ledger.transition_fulfillment(
                subject_key=other_subject,
                target_event_id=stored_funding.event_id,
                item="backdated_follow_up",
                status="pending",
                source_event_key=keyed(
                    "fulfillment_request", "backdated-former-owner",
                ),
                actor_key=keyed("admin_actor", "owner-1"),
                occurred_at="2026-08-11T08:30:00Z",
                received_at="2026-08-11T08:55:02Z",
            )
        former_owner = self.ledger.reauthenticated_admin_projection(
            other_subject, as_of=NOW,
        )
        self.assertEqual(former_owner["fulfillment"], [])
        with self.assertRaises(ContributionConflict):
            self.ledger.transition_fulfillment(
                subject_key=self.subject,
                target_event_id=stored_funding.event_id,
                item="one_time_credit_grant",
                status="pending",
                source_event_key=keyed(
                    "fulfillment_request", "transferred-same-item-reset",
                ),
                actor_key=keyed("admin_actor", "owner-1"),
                occurred_at="2026-08-11T08:56:00Z",
                received_at="2026-08-11T08:56:01Z",
            )
        transferred_projection = self.ledger.reauthenticated_admin_projection(
            self.subject, as_of=NOW,
        )
        transferred_task = next(
            row for row in transferred_projection["fulfillment"]
            if row["item"] == "one_time_credit_grant"
        )
        self.assertEqual(transferred_task["status"], "fulfilled")
        self.assertEqual(transferred_task["target_event_id"], stored_funding.event_id)
        transferred_reversal = self.ledger.transition_fulfillment(
            subject_key=self.subject,
            target_event_id=stored_funding.event_id,
            item="one_time_credit_grant",
            status="reversed",
            source_event_key=keyed(
                "fulfillment_request", "transferred-same-item-reversal",
            ),
            actor_key=keyed("admin_actor", "owner-1"),
            occurred_at="2026-08-11T08:56:30Z",
            received_at="2026-08-11T08:56:31Z",
        )
        self.assertEqual(transferred_reversal.fulfillment_status, "reversed")
        transferred_fulfillment = self.ledger.transition_fulfillment(
            subject_key=self.subject,
            target_event_id=stored_funding.event_id,
            item="retention_follow_up",
            status="pending",
            source_event_key=keyed(
                "fulfillment_request", "transferred-provider-subject",
            ),
            actor_key=keyed("admin_actor", "owner-1"),
            occurred_at="2026-08-11T08:57:00Z",
            received_at="2026-08-11T08:57:01Z",
        )
        self.assertEqual(transferred_fulfillment.subject_key, provider_subject)
        admin = self.ledger.reauthenticated_admin_projection(
            self.subject, as_of=NOW,
        )
        self.assertEqual(admin["subject_key"], self.subject)

    def test_recorded_allowance_is_source_distinct_capped_and_time_bounded(self):
        policy = BenefitPolicy(
            currency=DEFAULT_BENEFIT_POLICY.currency,
            one_time_rules=DEFAULT_BENEFIT_POLICY.one_time_rules,
            recurring_rules=DEFAULT_BENEFIT_POLICY.recurring_rules,
            allowance_policy=RecordedAllowancePolicy(
                unit="compute_seconds",
                free_allowance_units=10,
                one_time_rules=(
                    AllowanceRule(500, 100),
                    AllowanceRule(2_500, 300),
                ),
                recurring_rules=(
                    AllowanceRule(300, 50),
                    AllowanceRule(1_000, 200),
                ),
                one_time_cap_units=400,
                one_time_validity_seconds=3_600,
                recurring_validity_seconds=1_800,
            ),
        )
        expired = self.ledger.append(self.draft(
            "expired-gift", "one_time_contribution", amount=2_500,
            occurred_at="2026-08-11T08:00:00Z",
        ), received_at="2026-08-11T08:01:00Z")
        partial = self.ledger.append(self.draft(
            "partial-gift", "one_time_contribution", amount=2_500,
            occurred_at="2026-08-11T08:30:00Z",
        ), received_at="2026-08-11T08:31:00Z")
        capped = self.ledger.append(self.draft(
            "capped-gift", "one_time_contribution", amount=2_500,
            occurred_at="2026-08-11T08:40:00Z",
        ), received_at="2026-08-11T08:41:00Z")
        over_cap = self.ledger.append(self.draft(
            "over-cap-gift", "one_time_contribution", amount=500,
            occurred_at="2026-08-11T08:42:00Z",
        ), received_at="2026-08-11T08:43:00Z")
        refunded = self.ledger.append(self.draft(
            "refunded-gift", "one_time_contribution", amount=500,
            occurred_at="2026-08-11T08:50:00Z",
        ), received_at="2026-08-11T08:51:00Z")
        recurring = self.ledger.append(self.draft(
            "recurring", "recurring_started", amount=1_000,
            contract="membership", occurred_at="2026-08-11T08:45:00Z",
        ), received_at="2026-08-11T08:46:00Z")
        self.ledger.append(self.draft(
            "partial-refund", "refund", amount=2_000,
            related="partial-gift", occurred_at="2026-08-11T08:55:00Z",
        ), received_at="2026-08-11T08:56:00Z")
        self.ledger.append(self.draft(
            "full-refund", "refund", amount=500,
            related="refunded-gift", occurred_at="2026-08-11T08:56:00Z",
        ), received_at="2026-08-11T08:57:00Z")
        self.ledger.append(self.draft(
            "recurring-refund", "refund", amount=700,
            related="recurring", occurred_at="2026-08-11T08:57:00Z",
        ), received_at="2026-08-11T08:58:00Z")

        recorded = self.ledger.privacy_safe_user_projection(
            self.subject, policy=policy, as_of=NOW,
        )["recorded_allowance"]
        self.assertEqual(recorded["state"], "recorded_not_enforced")
        self.assertFalse(recorded["enforcement_enabled"])
        self.assertEqual(recorded["effective_allowance"], 460)
        rows = {
            row["source_event_id"]: row for row in recorded["sources"]
            if row["source_event_id"] is not None
        }
        self.assertEqual(rows[expired.event_id]["status"], "expired")
        self.assertEqual(rows[partial.event_id]["refund_state"], "partial")
        self.assertEqual(rows[partial.event_id]["effective_allowance"], 100)
        self.assertEqual(rows[capped.event_id]["effective_allowance"], 300)
        self.assertEqual(rows[over_cap.event_id]["status"], "capped")
        self.assertEqual(rows[over_cap.event_id]["effective_allowance"], 0)
        self.assertEqual(rows[refunded.event_id]["status"], "refunded")
        self.assertEqual(rows[recurring.event_id]["effective_allowance"], 50)
        self.assertTrue(all("provider" not in row for row in recorded["sources"]))

        later = self.ledger.privacy_safe_user_projection(
            self.subject,
            policy=policy,
            as_of="2026-08-11T09:40:00Z",
        )["recorded_allowance"]
        self.assertEqual(later["effective_allowance"], 110)
        expired_boundary = self.ledger.privacy_safe_user_projection(
            self.subject,
            policy=policy,
            as_of="2026-08-11T09:42:00Z",
        )["recorded_allowance"]
        self.assertEqual(expired_boundary["effective_allowance"], 10)

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

    def test_recurring_cancellation_is_currency_independent(self):
        policy = BenefitPolicy(
            currency="EUR",
            one_time_rules=(),
            recurring_rules=(),
            allowance_policy=RecordedAllowancePolicy(
                unit="compute_seconds",
                free_allowance_units=0,
                one_time_rules=(),
                recurring_rules=(AllowanceRule(500, 100),),
                one_time_cap_units=0,
                one_time_validity_seconds=0,
                recurring_validity_seconds=3_600,
            ),
        )
        source = replace(self.draft(
            "eur-renewal", "recurring_renewed", amount=1_000,
            contract="eur-membership", occurred_at="2026-08-11T08:00:00Z",
        ), currency="EUR")
        self.ledger.append(source, received_at="2026-08-11T08:01:00Z")
        # Moneyless cancellation defaults to USD but still closes the contract.
        self.ledger.append(self.draft(
            "eur-cancel", "recurring_canceled", contract="eur-membership",
            occurred_at="2026-08-11T08:30:00Z",
        ), received_at="2026-08-11T08:31:00Z")
        projection = self.ledger.privacy_safe_user_projection(
            self.subject, policy=policy, as_of=NOW,
        )
        self.assertEqual(projection["active_recurring_count"], 0)
        recurring = next(
            row for row in projection["recorded_allowance"]["sources"]
            if row["source"] == "recurring_support"
        )
        self.assertEqual(recurring["status"], "canceled")
        self.assertEqual(recurring["effective_allowance"], 0)

    def test_manual_fulfillment_has_private_user_and_opaque_admin_views(self):
        self.ledger.append(self.draft(
            "gift", "one_time_contribution", amount=2_500,
        ), received_at=NOW)
        # Reproduce a schema-v1 record written before ``complete`` was retired.
        with mock.patch.object(
            entitlements,
            "FULFILLMENT_STATES",
            frozenset({*entitlements.FULFILLMENT_STATES, "complete"}),
        ):
            self.ledger.append(self.draft(
                "fulfill-1", "fulfillment_set", related="gift",
                item="one_time_credit_grant", state="complete", actor="owner-1",
            ), received_at="2026-08-11T09:01:00Z")
        user = self.ledger.privacy_safe_user_projection(self.subject)
        self.assertEqual(user["fulfillment"], [{
            "target_event_id": self.ledger.events()[0].event_id,
            "item": "one_time_credit_grant",
            "status": "fulfilled",
        }])
        serialized_user = json.dumps(user, sort_keys=True)
        self.assertNotIn(self.subject, serialized_user)
        self.assertNotIn("actor_key", serialized_user)
        self.assertNotIn("proof_reference", serialized_user)
        self.assertNotIn("source_event_key", serialized_user)
        self.assertNotIn("private-user@example.test", self.path.read_text(encoding="utf-8"))
        admin = self.ledger.reauthenticated_admin_projection(self.subject)
        self.assertEqual(admin["subject_key"], self.subject)
        self.assertRegex(admin["fulfillment"][0]["actor_key"], r"^key_[0-9a-f]{64}$")
        self.assertIsNone(admin["fulfillment"][0]["proof_reference"])
        self.assertEqual(admin["audit"][1]["fulfillment_status"], "complete")
        self.assertNotIn("owner-1", json.dumps(admin))
        reversed_event = self.ledger.transition_fulfillment(
            subject_key=self.subject,
            target_event_id=self.ledger.events()[0].event_id,
            item="one_time_credit_grant",
            status="reversed",
            source_event_key=keyed("fulfillment_request", "reverse-legacy"),
            actor_key=keyed("admin_actor", "owner-1"),
            occurred_at="2026-08-11T09:02:00Z",
            received_at="2026-08-11T09:02:01Z",
        )
        self.assertEqual(reversed_event.fulfillment_status, "reversed")

    def test_delayed_older_fulfillment_does_not_replace_newer_status(self):
        self.ledger.append(self.draft(
            "gift", "one_time_contribution", amount=2_500,
        ), received_at=NOW)
        self.ledger.append(self.draft(
            "fulfilled-new", "fulfillment_set", related="gift",
            item="one_time_credit_grant", state="fulfilled", actor="owner-1",
            occurred_at="2026-08-11T08:30:00Z",
        ), received_at="2026-08-11T09:01:00Z")
        self.ledger.append(self.draft(
            "fulfilled-old", "fulfillment_set", related="gift",
            item="one_time_credit_grant", state="pending", actor="owner-1",
            occurred_at="2026-08-11T08:15:00Z",
        ), received_at="2026-08-11T09:02:00Z")
        projection = self.ledger.privacy_safe_user_projection(self.subject)
        self.assertEqual(projection["fulfillment"][0]["status"], "fulfilled")

    def test_fulfillment_transition_graph_and_idempotency_are_atomic(self):
        funding = self.ledger.append(self.draft(
            "gift", "one_time_contribution", amount=2_500,
        ), received_at=NOW)
        kwargs = {
            "subject_key": self.subject,
            "target_event_id": funding.event_id,
            "item": "one_time_credit_grant",
            "source_event_key": keyed("fulfillment_request", "pending-1"),
            "actor_key": keyed("admin_actor", "owner-1"),
            "contract_key": keyed("fulfillment_proof", "proof-1"),
            "occurred_at": "2026-08-11T09:02:00Z",
            "received_at": "2026-08-11T09:02:01Z",
        }
        pending = self.ledger.transition_fulfillment(status="pending", **kwargs)
        self.assertEqual(
            self.ledger.transition_fulfillment(status="pending", **kwargs),
            pending,
        )
        self.assertEqual(len(self.ledger.events()), 2)
        admin_pending = self.ledger.reauthenticated_admin_projection(self.subject)
        self.assertEqual(
            admin_pending["fulfillment"][0]["proof_reference"],
            kwargs["contract_key"],
        )
        with self.assertRaises(ContributionConflict):
            self.ledger.transition_fulfillment(status="fulfilled", **kwargs)
        with self.assertRaises(ContributionConflict):
            self.ledger.transition_fulfillment(
                **{**kwargs, "source_event_key": keyed(
                    "fulfillment_request", "pending-2",
                )},
                status="pending",
            )
        progressed = self.ledger.transition_fulfillment(
            **{**kwargs, "source_event_key": keyed(
                "fulfillment_request", "progress-1",
            )},
            status="in_progress",
        )
        fulfilled = self.ledger.transition_fulfillment(
            **{**kwargs, "source_event_key": keyed(
                "fulfillment_request", "fulfilled-1",
            )},
            status="fulfilled",
        )
        reversed_event = self.ledger.transition_fulfillment(
            **{**kwargs, "source_event_key": keyed(
                "fulfillment_request", "reversed-1",
            )},
            status="reversed",
        )
        self.assertEqual(
            [progressed.fulfillment_status, fulfilled.fulfillment_status,
             reversed_event.fulfillment_status],
            ["in_progress", "fulfilled", "reversed"],
        )
        with self.assertRaises(ContributionConflict):
            self.ledger.transition_fulfillment(
                **{**kwargs, "source_event_key": keyed(
                    "fulfillment_request", "terminal-1",
                )},
                status="fulfilled",
            )
        branch_cases = (
            ("direct-fulfilled", ("pending", "fulfilled")),
            ("pending-declined", ("pending", "declined")),
            ("progress-declined", ("pending", "in_progress", "declined")),
        )
        for offset, (label, states) in enumerate(branch_cases, start=1):
            branch_funding = self.ledger.append(self.draft(
                f"gift-{label}", "one_time_contribution", amount=500,
            ), received_at=f"2026-08-11T09:1{offset}:00Z")
            for step, branch_status in enumerate(states):
                self.ledger.transition_fulfillment(
                    subject_key=self.subject,
                    target_event_id=branch_funding.event_id,
                    item="one_time_credit_grant",
                    status=branch_status,
                    source_event_key=keyed(
                        "fulfillment_request", f"{label}-{branch_status}",
                    ),
                    actor_key=keyed("admin_actor", "owner-1"),
                    occurred_at=f"2026-08-11T09:1{offset}:0{step + 1}Z",
                    received_at=f"2026-08-11T09:1{offset}:1{step + 1}Z",
                )
            projected = self.ledger.privacy_safe_user_projection(self.subject)
            selected = next(
                row for row in projected["fulfillment"]
                if row["target_event_id"] == branch_funding.event_id
            )
            self.assertEqual(selected["status"], states[-1])

    def test_fulfillment_targets_only_same_subject_funding(self):
        funding = self.ledger.append(self.draft(
            "gift", "one_time_contribution", amount=2_500,
        ), received_at=NOW)
        common = {
            "target_event_id": funding.event_id,
            "item": "one_time_credit_grant",
            "status": "pending",
            "source_event_key": keyed("fulfillment_request", "wrong-subject"),
            "actor_key": keyed("admin_actor", "owner-1"),
        }
        with self.assertRaises(ContributionConflict):
            self.ledger.transition_fulfillment(
                subject_key=keyed("test_subject", "other"), **common,
            )
        adjustment = self.ledger.append(self.draft(
            "refund", "refund", amount=100, related="gift",
        ), received_at=NOW)
        with self.assertRaises(ContributionConflict):
            self.ledger.transition_fulfillment(
                subject_key=self.subject,
                target_event_id=adjustment.event_id,
                **{key: value for key, value in common.items()
                   if key != "target_event_id"},
            )

    def test_competing_initial_fulfillment_transitions_serialize(self):
        funding = self.ledger.append(self.draft(
            "gift", "one_time_contribution", amount=2_500,
        ), received_at=NOW)
        barrier = threading.Barrier(2)
        outcomes = []

        def transition(label):
            barrier.wait()
            try:
                event = self.ledger.transition_fulfillment(
                    subject_key=self.subject,
                    target_event_id=funding.event_id,
                    item="one_time_credit_grant",
                    status="pending",
                    source_event_key=keyed("fulfillment_request", label),
                    actor_key=keyed("admin_actor", "owner-1"),
                )
                outcomes.append(("ok", event.event_id))
            except ContributionConflict:
                outcomes.append(("conflict", label))

        threads = [
            threading.Thread(target=transition, args=(label,))
            for label in ("race-a", "race-b")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(result[0] for result in outcomes), [
            "conflict", "ok",
        ])
        self.assertEqual(len(self.ledger.events()), 2)

    def test_manual_owner_contribution_lifecycle_is_atomic_and_idempotent(self):
        common = {
            "subject_key": self.subject,
            "source": "buy_me_a_coffee",
            "currency": "USD",
            "actor_key": keyed("manual_actor", "owner-1"),
            "occurred_at": NOW,
            "received_at": NOW,
        }
        one_time = self.ledger.record_manual_contribution(
            **common,
            kind="one_time_contribution",
            amount_minor=1_000,
            target_event_id=None,
            source_event_key=keyed("manual_request", "gift-1"),
        )
        self.assertEqual(one_time.provider, "manual_buy_me_a_coffee")
        self.assertEqual(
            self.ledger.record_manual_contribution(
                **common,
                kind="one_time_contribution",
                amount_minor=1_000,
                target_event_id=None,
                source_event_key=keyed("manual_request", "gift-1"),
            ),
            one_time,
        )
        with self.assertRaises(ManualContributionConflict):
            self.ledger.record_manual_contribution(
                **common,
                kind="one_time_contribution",
                amount_minor=1_001,
                target_event_id=None,
                source_event_key=keyed("manual_request", "gift-1"),
            )

        recurring = self.ledger.record_manual_contribution(
            **common,
            kind="recurring_started",
            amount_minor=500,
            target_event_id=None,
            source_event_key=keyed("manual_request", "recurring-start"),
        )
        renewed = self.ledger.record_manual_contribution(
            **common,
            kind="recurring_renewed",
            amount_minor=500,
            target_event_id=recurring.event_id,
            source_event_key=keyed("manual_request", "recurring-renew"),
        )
        self.assertEqual(renewed.contract_key, recurring.contract_key)
        self.assertEqual(renewed.related_event_key, recurring.source_event_key)
        canceled = self.ledger.record_manual_contribution(
            **common,
            kind="recurring_canceled",
            amount_minor=0,
            target_event_id=renewed.event_id,
            source_event_key=keyed("manual_request", "recurring-cancel"),
        )
        self.assertEqual(canceled.contract_key, recurring.contract_key)
        self.assertEqual(
            self.ledger.record_manual_contribution(
                **common,
                kind="recurring_canceled",
                amount_minor=0,
                target_event_id=renewed.event_id,
                source_event_key=keyed("manual_request", "recurring-cancel"),
            ),
            canceled,
        )
        with self.assertRaises(ManualContributionConflict):
            self.ledger.record_manual_contribution(
                **common,
                kind="recurring_renewed",
                amount_minor=500,
                target_event_id=renewed.event_id,
                source_event_key=keyed("manual_request", "renew-after-cancel"),
            )

        refund = self.ledger.record_manual_contribution(
            **common,
            kind="refund",
            amount_minor=600,
            target_event_id=one_time.event_id,
            source_event_key=keyed("manual_request", "refund"),
        )
        chargeback = self.ledger.record_manual_contribution(
            **common,
            kind="chargeback",
            amount_minor=400,
            target_event_id=one_time.event_id,
            source_event_key=keyed("manual_request", "chargeback"),
        )
        self.assertEqual(refund.related_event_key, one_time.source_event_key)
        self.assertEqual(chargeback.related_event_key, one_time.source_event_key)
        with self.assertRaises(ManualContributionConflict):
            self.ledger.record_manual_contribution(
                **common,
                kind="refund",
                amount_minor=1,
                target_event_id=one_time.event_id,
                source_event_key=keyed("manual_request", "excess-refund"),
            )
        self.assertEqual(
            self.ledger.reauthenticated_admin_projection(self.subject)[
                "currency_totals_minor"
            ]["USD"],
            1_000,
        )

    def test_manual_targets_are_same_account_source_currency_and_active_chain(self):
        actor = keyed("manual_actor", "owner")
        funding = self.ledger.record_manual_contribution(
            subject_key=self.subject,
            source="patreon",
            kind="recurring_started",
            amount_minor=900,
            currency="USD",
            target_event_id=None,
            source_event_key=keyed("manual_request", "patreon-start"),
            actor_key=actor,
            occurred_at=NOW,
            received_at=NOW,
        )
        base = {
            "subject_key": self.subject,
            "kind": "recurring_renewed",
            "amount_minor": 900,
            "target_event_id": funding.event_id,
            "actor_key": actor,
            "occurred_at": NOW,
            "received_at": NOW,
        }
        for label, replacement in (
            ("source", {"source": "buy_me_a_coffee", "currency": "USD"}),
            ("currency", {"source": "patreon", "currency": "EUR"}),
            ("account", {
                "source": "patreon",
                "currency": "USD",
                "subject_key": keyed("test_subject", "other"),
            }),
        ):
            with self.subTest(label=label), self.assertRaises(
                ManualContributionConflict,
            ):
                self.ledger.record_manual_contribution(
                    **{**base, **replacement},
                    source_event_key=keyed("manual_request", f"bad-{label}"),
                )

    def test_competing_manual_adjustments_serialize_remaining_net(self):
        common = {
            "subject_key": self.subject,
            "source": "direct_compute_sponsorship",
            "currency": "USD",
            "actor_key": keyed("manual_actor", "owner"),
        }
        funding = self.ledger.record_manual_contribution(
            **common,
            kind="one_time_contribution",
            amount_minor=100,
            target_event_id=None,
            source_event_key=keyed("manual_request", "compute-funding"),
        )
        barrier = threading.Barrier(2)
        outcomes = []

        def adjust(label):
            barrier.wait()
            try:
                self.ledger.record_manual_contribution(
                    **common,
                    kind="refund",
                    amount_minor=75,
                    target_event_id=funding.event_id,
                    source_event_key=keyed("manual_request", label),
                )
                outcomes.append("ok")
            except ManualContributionConflict:
                outcomes.append("conflict")

        threads = [threading.Thread(target=adjust, args=(label,)) for label in (
            "race-refund-a", "race-refund-b",
        )]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), ["conflict", "ok"])
        self.assertEqual(len(self.ledger.events()), 2)

    def test_malformed_events_and_orphan_adjustments_are_bounded(self):
        with self.assertRaises(EntitlementError):
            self.ledger.append(self.draft("bad", "refund", amount=100))
        with self.assertRaises(EntitlementError):
            self.ledger.append(self.draft(
                "bad-cancel", "recurring_canceled",
            ))
        with self.assertRaises(EntitlementError):
            self.ledger.append(self.draft(
                "bad-link", "account_link_verified",
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
