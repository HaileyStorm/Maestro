"""Strict raw-body webhook and replay boundary regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.entitlements import (  # noqa: E402
    ContributionConflict,
    ContributionLedger,
    opaque_key,
)
from services.support_webhooks import (  # noqa: E402
    FakeSignedWebhookAdapter,
    FileWebhookReplayGuard,
    ManualContributionAdapter,
    WebhookPayloadError,
    WebhookReplayError,
    WebhookReplayIntegrityError,
    WebhookSignatureError,
    SupportWebhookError,
    WebhookTimestampError,
    process_signed_webhook,
)
from services import support_webhooks  # noqa: E402


NOW = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
SIGNING_KEY = b"fake-webhook-signing-key-000000000000000"
IDENTITY_KEY = b"fake-webhook-identity-key-00000000000000"
INTEGRITY_KEY = b"support-storage-integrity-key-00000000000"


def body(**overrides) -> bytes:
    payload = {
        "event_id": "provider-event-private-1",
        "subject_id": "private-user@example.test",
        "kind": "one_time_contribution",
        "occurred_at": "2026-08-11T09:59:00Z",
        "amount_minor": 2_500,
        "currency": "USD",
    }
    payload.update(overrides)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


class SupportWebhookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ledger_path = root / "contributions.json"
        self.replay_path = root / "replay.json"
        self.adapter = FakeSignedWebhookAdapter(
            signing_secret=SIGNING_KEY,
            identity_secret=IDENTITY_KEY,
        )
        self.ledger = ContributionLedger(
            self.ledger_path,
            integrity_key=INTEGRITY_KEY,
            allow_test_path=True,
        )
        self.guard = FileWebhookReplayGuard(
            self.replay_path,
            integrity_key=INTEGRITY_KEY,
            allow_test_path=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def signed(self, raw: bytes, at: datetime = NOW) -> dict[str, str]:
        return self.adapter.headers(raw, int(at.timestamp()))

    def test_exact_raw_body_signature_and_timestamp_are_required(self):
        raw = body()
        headers = self.signed(raw)
        draft = self.adapter.verify_and_translate(
            raw, headers, received_at=NOW,
        )
        self.assertEqual(draft.kind, "one_time_contribution")
        self.assertRegex(draft.subject_key, r"^key_[0-9a-f]{64}$")
        altered = raw + b" "
        with self.assertRaises(WebhookSignatureError):
            self.adapter.verify_and_translate(
                altered, headers, received_at=NOW,
            )
        stale_headers = self.signed(raw, NOW - timedelta(minutes=6))
        with self.assertRaises(WebhookTimestampError):
            self.adapter.verify_and_translate(
                raw, stale_headers, received_at=NOW,
            )
        future_headers = self.signed(raw, NOW + timedelta(minutes=6))
        with self.assertRaises(WebhookTimestampError):
            self.adapter.verify_and_translate(
                raw, future_headers, received_at=NOW,
            )

    def test_process_is_restart_safe_idempotent_and_replays_are_rejected(self):
        raw = body()
        headers = self.signed(raw)
        event = process_signed_webhook(
            self.adapter, self.ledger, self.guard,
            raw, headers, received_at=NOW,
        )
        self.assertEqual(event.sequence, 1)
        restarted_ledger = ContributionLedger(
            self.ledger_path,
            integrity_key=INTEGRITY_KEY,
            allow_test_path=True,
        )
        restarted_guard = FileWebhookReplayGuard(
            self.replay_path,
            integrity_key=INTEGRITY_KEY,
            allow_test_path=True,
        )
        with self.assertRaises(WebhookReplayError):
            process_signed_webhook(
                self.adapter, restarted_ledger, restarted_guard,
                raw, headers, received_at=NOW,
            )
        self.assertEqual(len(restarted_ledger.events()), 1)
        stored = self.ledger_path.read_text(encoding="utf-8")
        self.assertNotIn("provider-event-private-1", stored)
        self.assertNotIn("private-user@example.test", stored)

    def test_signed_account_link_projects_provider_support_to_opaque_account(self):
        account_id = "maestro-private-account"
        account_subject = opaque_key(
            "maestro_account_support", account_id, IDENTITY_KEY,
        )
        link_adapter = FakeSignedWebhookAdapter(
            signing_secret=SIGNING_KEY,
            identity_secret=IDENTITY_KEY,
            account_link_resolver=lambda provider, provider_subject, claimed: (
                account_subject
                if (
                    provider == "fake_support"
                    and provider_subject == "private-user@example.test"
                    and claimed == account_id
                ) else None
            ),
        )
        link_raw = body(
            event_id="verified-link-event",
            kind="account_link_verified",
            account_id=account_id,
            amount_minor=0,
        )
        link = process_signed_webhook(
            link_adapter, self.ledger, self.guard,
            link_raw, link_adapter.headers(link_raw, int(NOW.timestamp())),
            received_at=NOW,
        )
        contribution_raw = body(event_id="linked-contribution")
        process_signed_webhook(
            link_adapter, self.ledger, self.guard,
            contribution_raw,
            link_adapter.headers(contribution_raw, int(NOW.timestamp())),
            received_at=NOW,
        )
        projection = self.ledger.privacy_safe_user_projection(
            account_subject, as_of=NOW,
        )
        self.assertEqual(projection["currency_totals_minor"], {"USD": 2_500})
        self.assertEqual(link.subject_key, account_subject)
        self.assertRegex(link.related_event_key or "", r"^key_[0-9a-f]{64}$")
        stored = self.ledger_path.read_text(encoding="utf-8")
        self.assertNotIn(account_id, stored)
        self.assertNotIn("private-user@example.test", stored)

    def test_account_id_is_required_only_for_account_link_events(self):
        missing = body(kind="account_link_verified", amount_minor=0)
        with self.assertRaisesRegex(WebhookPayloadError, "account_id"):
            self.adapter.verify_and_translate(
                missing, self.signed(missing), received_at=NOW,
            )
        extraneous = body(account_id="not-allowed-here")
        with self.assertRaisesRegex(WebhookPayloadError, "account_id"):
            self.adapter.verify_and_translate(
                extraneous, self.signed(extraneous), received_at=NOW,
            )
        unverified = body(
            kind="account_link_verified",
            account_id="unverified-account",
            amount_minor=0,
        )
        with self.assertRaisesRegex(WebhookPayloadError, "server verification"):
            self.adapter.verify_and_translate(
                unverified, self.signed(unverified), received_at=NOW,
            )
        rejecting_adapter = FakeSignedWebhookAdapter(
            signing_secret=SIGNING_KEY,
            identity_secret=IDENTITY_KEY,
            account_link_resolver=lambda provider, subject, account: None,
        )
        with self.assertRaisesRegex(WebhookPayloadError, "verification failed"):
            rejecting_adapter.verify_and_translate(
                unverified,
                rejecting_adapter.headers(unverified, int(NOW.timestamp())),
                received_at=NOW,
            )

    def test_crash_between_ledger_and_replay_seal_can_finish_on_retry(self):
        raw = body(event_id="crash-safe-event")
        headers = self.signed(raw)
        draft = self.adapter.verify_and_translate(raw, headers, received_at=NOW)
        first = self.ledger.append(draft, received_at=NOW)
        retried = process_signed_webhook(
            self.adapter, self.ledger, self.guard,
            raw, headers, received_at=NOW,
        )
        self.assertEqual(retried, first)
        self.assertEqual(len(self.ledger.events()), 1)

    def test_account_link_crash_retry_survives_resolver_state_change(self):
        account_id = "crash-retry-account"
        account_subject = opaque_key(
            "maestro_account_support", account_id, IDENTITY_KEY,
        )
        active = {"value": True}

        def resolver(provider, provider_subject, claimed_account):
            if (
                active["value"]
                and provider == "fake_support"
                and provider_subject == "private-user@example.test"
                and claimed_account == account_id
            ):
                return account_subject
            return None

        adapter = FakeSignedWebhookAdapter(
            signing_secret=SIGNING_KEY,
            identity_secret=IDENTITY_KEY,
            account_link_resolver=resolver,
        )
        raw = body(
            event_id="crash-safe-link",
            kind="account_link_verified",
            account_id=account_id,
            amount_minor=0,
        )
        headers = adapter.headers(raw, int(NOW.timestamp()))
        draft = adapter.verify_and_translate(raw, headers, received_at=NOW)
        first = self.ledger.append(draft, received_at=NOW)
        active["value"] = False

        altered = body(
            event_id="crash-safe-link",
            kind="account_link_verified",
            account_id="different-account-claim",
            amount_minor=0,
        )
        with self.assertRaises(ContributionConflict):
            process_signed_webhook(
                adapter,
                self.ledger,
                self.guard,
                altered,
                adapter.headers(altered, int(NOW.timestamp())),
                received_at=NOW,
            )

        retried = process_signed_webhook(
            adapter, self.ledger, self.guard,
            raw, headers, received_at=NOW,
        )
        self.assertEqual(retried, first)
        self.assertEqual(len(self.ledger.events()), 1)
        with self.assertRaises(WebhookReplayError):
            process_signed_webhook(
                adapter, self.ledger, self.guard,
                raw, headers, received_at=NOW,
            )

    def test_malformed_duplicate_unknown_and_shape_errors_fail_closed(self):
        malformed = b"{not-json"
        with self.assertRaises(WebhookPayloadError):
            self.adapter.verify_and_translate(
                malformed, self.signed(malformed), received_at=NOW,
            )
        duplicate = (
            b'{"event_id":"a","event_id":"b","subject_id":"u",'
            b'"kind":"one_time_contribution",'
            b'"occurred_at":"2026-08-11T09:59:00Z"}'
        )
        with self.assertRaisesRegex(WebhookPayloadError, "duplicate"):
            self.adapter.verify_and_translate(
                duplicate, self.signed(duplicate), received_at=NOW,
            )
        unknown = body(prompt="must-never-be-accepted")
        with self.assertRaises(WebhookPayloadError):
            self.adapter.verify_and_translate(
                unknown, self.signed(unknown), received_at=NOW,
            )
        boolean_amount = body(amount_minor=True)
        with self.assertRaises(WebhookPayloadError):
            self.adapter.verify_and_translate(
                boolean_amount, self.signed(boolean_amount), received_at=NOW,
            )

    def test_replay_state_tamper_is_detected_after_restart(self):
        raw = body()
        process_signed_webhook(
            self.adapter, self.ledger, self.guard,
            raw, self.signed(raw), received_at=NOW,
        )
        payload = json.loads(self.replay_path.read_text(encoding="utf-8"))
        payload["entries"] = {}
        self.replay_path.write_text(json.dumps(payload), encoding="utf-8")
        restarted = FileWebhookReplayGuard(
            self.replay_path,
            integrity_key=INTEGRITY_KEY,
            allow_test_path=True,
        )
        with self.assertRaises(WebhookReplayIntegrityError):
            restarted.record(
                "fake_support", self.ledger.events()[0].source_event_key,
                now=NOW,
            )

    def test_replay_publication_bounds_preserve_exact_previous_state(self):
        raw = body(event_id="first-replay-bound")
        first = self.adapter.verify_and_translate(
            raw, self.signed(raw), received_at=NOW,
        )
        self.guard.record(first.provider, first.source_event_key, now=NOW)
        before = self.replay_path.read_bytes()
        second_raw = body(event_id="second-replay-bound")
        second = self.adapter.verify_and_translate(
            second_raw, self.signed(second_raw), received_at=NOW,
        )
        with mock.patch.object(support_webhooks, "MAX_REPLAY_ENTRIES", 1):
            with self.assertRaisesRegex(SupportWebhookError, "entry bound"):
                self.guard.record(second.provider, second.source_event_key, now=NOW)
            self.assertEqual(self.replay_path.read_bytes(), before)
        with mock.patch.object(
            support_webhooks, "MAX_REPLAY_STATE_BYTES", len(before) + 16,
        ):
            with self.assertRaisesRegex(SupportWebhookError, "byte bound"):
                self.guard.record(second.provider, second.source_event_key, now=NOW)
            self.assertEqual(self.replay_path.read_bytes(), before)

    def test_replay_write_does_not_require_unix_fchmod(self):
        raw = body(event_id="windows-replay-write")
        draft = self.adapter.verify_and_translate(
            raw, self.signed(raw), received_at=NOW,
        )
        with mock.patch.object(support_webhooks.os, "fchmod", None):
            self.guard.record(draft.provider, draft.source_event_key, now=NOW)
        self.assertTrue(self.replay_path.is_file())

    def test_two_replay_guard_instances_serialize_without_lost_state(self):
        other = FileWebhookReplayGuard(
            self.replay_path,
            integrity_key=INTEGRITY_KEY,
            allow_test_path=True,
        )
        raw = body(event_id="concurrent-replay")
        draft = self.adapter.verify_and_translate(
            raw, self.signed(raw), received_at=NOW,
        )
        entered = threading.Event()
        release = threading.Event()
        original_write = self.guard._write
        errors: list[BaseException] = []

        def blocking_write(entries):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test replay release timed out")
            original_write(entries)

        def record(guard):
            try:
                guard.record(
                    draft.provider, draft.source_event_key, now=NOW,
                )
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(self.guard, "_write", blocking_write):
            first = threading.Thread(target=record, args=(self.guard,))
            second = threading.Thread(target=record, args=(other,))
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            time.sleep(0.05)
            self.assertTrue(second.is_alive())
            release.set()
            first.join(2)
            second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], WebhookReplayError)

    def test_manual_adapter_is_explicitly_nonproduction_and_opaque(self):
        adapter = ManualContributionAdapter(identity_secret=IDENTITY_KEY)
        self.assertFalse(adapter.production_ready)
        self.assertNotIn(IDENTITY_KEY.decode("ascii"), repr(adapter))
        self.assertNotIn(SIGNING_KEY.decode("ascii"), repr(self.adapter))
        self.assertNotIn(IDENTITY_KEY.decode("ascii"), repr(self.adapter))
        draft = adapter.draft(
            event_id="manual-private-event",
            subject_id="manual-private-user",
            kind="one_time_contribution",
            occurred_at=NOW,
            amount_minor=500,
        )
        self.ledger.append(draft, received_at=NOW)
        stored = self.ledger_path.read_text(encoding="utf-8")
        self.assertNotIn("manual-private-event", stored)
        self.assertNotIn("manual-private-user", stored)
        link = adapter.draft(
            event_id="manual-private-link",
            subject_id="manual-provider-user",
            kind="account_link_verified",
            occurred_at=NOW,
            linked_account_id="manual-maestro-account",
        )
        self.ledger.append(link, received_at=NOW)
        stored = self.ledger_path.read_text(encoding="utf-8")
        self.assertNotIn("manual-provider-user", stored)
        self.assertNotIn("manual-maestro-account", stored)
        with self.assertRaisesRegex(WebhookPayloadError, "linked account"):
            adapter.draft(
                event_id="bad-manual-link",
                subject_id="manual-provider-user",
                kind="account_link_verified",
                occurred_at=NOW,
            )

    def test_invalid_timestamp_header_and_unknown_event_kind_fail(self):
        raw = body()
        headers = self.signed(raw)
        headers["x-maestro-support-timestamp"] = "not-a-time"
        with self.assertRaises(WebhookTimestampError):
            self.adapter.verify_and_translate(raw, headers, received_at=NOW)
        invalid_kind = body(kind="invented")
        draft = self.adapter.verify_and_translate(
            invalid_kind, self.signed(invalid_kind), received_at=NOW,
        )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.ledger.append(draft, received_at=NOW)


if __name__ == "__main__":
    unittest.main()
