"""Strict raw-body webhook and replay boundary regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
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
    BMAC_RUNTIME_CONFIG_SCHEMA_VERSION,
    BMAC_SUPPORT_EVIDENCE_CONTRACT,
    BmacSupportWebhookAdapter,
    BmacWebhookConfig,
    BmacWebhookVerifier,
    DIRECT_STRIPE_SUPPORT_EVIDENCE_CONTRACT,
    DirectStripeSupportWebhookAdapter,
    FakeSignedWebhookAdapter,
    FileWebhookReplayGuard,
    ManualContributionAdapter,
    STRIPE_BMAC_CREATOR_SURFACE,
    STRIPE_BMAC_DEPLOYMENT_SCOPE,
    STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT,
    SupportAssociationIntegrityError,
    StripeWebhookVerifier,
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
STRIPE_SECRET = b"whsec_test_only_000000000000000000000000"
BMAC_SECRET = b"bmac_test_only_0000000000000000000000000"


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


def stripe_body(**overrides) -> bytes:
    payload = {
        "id": "evt_testSupport123",
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {"object": {"id": "cs_test_private"}},
    }
    payload.update(overrides)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def stripe_event(
    event_id: str,
    event_type: str,
    stripe_object: dict,
    **overrides,
) -> bytes:
    if event_id.startswith("evt_"):
        event_id = "evt_" + event_id[4:].replace("_", "")
    payload = {
        "id": event_id,
        "type": event_type,
        "created": int(NOW.timestamp()),
        "livemode": False,
        "data": {"object": stripe_object},
    }
    payload.update(overrides)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def stripe_headers(raw: bytes, at: datetime = NOW) -> dict[str, str]:
    timestamp = int(at.timestamp())
    digest = hmac.new(
        STRIPE_SECRET,
        str(timestamp).encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={digest}"}


def bmac_event(
    event_id: int,
    event_type: str,
    data: dict,
    *,
    attempt: int = 1,
    live_mode: bool = False,
) -> bytes:
    return json.dumps({
        "event_id": event_id,
        "type": event_type,
        "live_mode": live_mode,
        "created": int(NOW.timestamp()),
        "attempt": attempt,
        "data": data,
    }, sort_keys=True, separators=(",", ":")).encode()


def bmac_headers(raw: bytes) -> dict[str, str]:
    return {
        "x-signature-sha256": hmac.new(
            BMAC_SECRET, raw, hashlib.sha256,
        ).hexdigest(),
    }


class SupportWebhookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ledger_path = root / "contributions.json"
        self.replay_path = root / "replay.json"
        self.association_path = root / "bmac-associations.json"
        self.account_subject = opaque_key(
            "maestro_account_support", "existing-maestro-account", IDENTITY_KEY,
        )
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

    def bmac_adapter(self, *, linked: bool = True) -> BmacSupportWebhookAdapter:
        supporter_key = opaque_key("bmac_supporter", "42", IDENTITY_KEY)
        config = BmacWebhookConfig.from_runtime_json(
            json.dumps({
                "schema_version": BMAC_RUNTIME_CONFIG_SCHEMA_VERSION,
                "creator_surface": STRIPE_BMAC_CREATOR_SURFACE,
                "deployment_scope": STRIPE_BMAC_DEPLOYMENT_SCOPE,
                "live_mode": False,
                "account_links": (
                    {supporter_key: self.account_subject} if linked else {}
                ),
            }),
            signing_secret=BMAC_SECRET,
            identity_secret=IDENTITY_KEY,
            association_integrity_key=INTEGRITY_KEY,
        )
        return BmacSupportWebhookAdapter(
            config,
            association_path=self.association_path,
            allow_test_path=True,
        )

    @staticmethod
    def donation_data(**overrides) -> dict:
        payload = {
            "id": 98765,
            "object": "payment",
            "transaction_id": "pi_private_transaction",
            "status": "succeeded",
            "refunded": "false",
            "amount": 15.0,
            "currency": "USD",
            "supporter_id": 42,
            "supporter_name": "Private Name",
            "supporter_email": "private@example.test",
            "message": "Private message",
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def membership_data(**overrides) -> dict:
        payload = {
            "id": 555,
            "psp_id": "sub_private_membership",
            "object": "membership",
            "status": "active",
            "canceled": "false",
            "paused": "false",
            "amount": 12.5,
            "currency": "USD",
            "supporter_id": 42,
            "supporter_name": "Private Name",
            "supporter_email": "private@example.test",
            "membership_level_id": 7,
            "membership_level_name": "Private Level",
        }
        payload.update(overrides)
        return payload

    @classmethod
    def recurring_data(cls, **overrides) -> dict:
        payload = cls.membership_data(
            id=777,
            psp_id="sub_private_recurring",
            object="recurring_donation",
        )
        payload.pop("membership_level_id", None)
        payload.pop("membership_level_name", None)
        payload.update(overrides)
        return payload

    def test_native_bmac_evidence_is_distinct_from_optional_direct_stripe(self):
        projection = BMAC_SUPPORT_EVIDENCE_CONTRACT.public_projection()
        self.assertIs(
            STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT,
            BMAC_SUPPORT_EVIDENCE_CONTRACT,
        )
        self.assertFalse(projection["enabled"])
        self.assertEqual(projection["provider_id"], "buy_me_a_coffee")
        self.assertEqual(projection["transport"], "native_buy_me_a_coffee_webhook")
        self.assertEqual(projection["signature_header"], "x-signature-sha256")
        self.assertNotIn("fraud_screening_role", projection)
        self.assertEqual(set(projection["positive_event_types"]), {
            "donation.created", "membership.started", "membership.updated",
            "recurring_donation.started", "recurring_donation.updated",
        })
        self.assertEqual(set(projection["reversal_event_types"]), {
            "donation.refunded", "membership.cancelled", "membership.paused",
            "recurring_donation.cancelled",
        })
        direct = DIRECT_STRIPE_SUPPORT_EVIDENCE_CONTRACT.public_projection()
        self.assertFalse(direct["enabled"])
        self.assertFalse(DirectStripeSupportWebhookAdapter.production_ready)
        self.assertEqual(direct["provider_id"], "direct_stripe_support")
        self.assertEqual(direct["transport"], "optional_direct_stripe_webhook")
        self.assertEqual(
            direct["fraud_screening_role"],
            "radar_fraud_screening_only_never_authorization",
        )
        serialized = json.dumps(projection, sort_keys=True).lower()
        self.assertNotIn("secret", serialized)
        self.assertNotIn("email", serialized)
        self.assertNotIn(STRIPE_BMAC_CREATOR_SURFACE, serialized)
        self.assertNotIn(STRIPE_BMAC_DEPLOYMENT_SCOPE, serialized)
        self.assertNotIn("benefit_policy", serialized)

    def test_native_bmac_config_reuses_shared_creator_and_rejects_provider_policy(self):
        supporter_key = opaque_key("bmac_supporter", "42", IDENTITY_KEY)
        base = {
            "schema_version": BMAC_RUNTIME_CONFIG_SCHEMA_VERSION,
            "creator_surface": STRIPE_BMAC_CREATOR_SURFACE,
            "deployment_scope": STRIPE_BMAC_DEPLOYMENT_SCOPE,
            "live_mode": False,
            "account_links": {supporter_key: self.account_subject},
        }

        def load(payload):
            return BmacWebhookConfig.from_runtime_json(
                json.dumps(payload),
                signing_secret=BMAC_SECRET,
                identity_secret=IDENTITY_KEY,
                association_integrity_key=INTEGRITY_KEY,
            )

        config = load(base)
        self.assertEqual(config.account_links[supporter_key], self.account_subject)
        self.assertNotIn(STRIPE_BMAC_CREATOR_SURFACE, repr(config))
        self.assertNotIn(STRIPE_BMAC_DEPLOYMENT_SCOPE, repr(config))
        with self.assertRaisesRegex(SupportWebhookError, "shared creator"):
            load({**base, "creator_surface": "create_second_creator"})
        with self.assertRaisesRegex(SupportWebhookError, "private usable"):
            load({**base, "deployment_scope": "public_release"})
        for forbidden in (
            "benefit_policy", "create_creator_account", "kyc", "bank_account",
        ):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(SupportWebhookError, "shape"):
                    load({**base, forbidden: {}})
        with self.assertRaisesRegex(SupportWebhookError, "opaque associations"):
            load({**base, "account_links": {"private@example.test": self.account_subject}})

    def test_native_bmac_signature_covers_exact_raw_body(self):
        verifier = BmacWebhookVerifier(BMAC_SECRET)
        raw = bmac_event(1, "donation.created", self.donation_data())
        verified = verifier.verify_event(raw, bmac_headers(raw), received_at=NOW)
        self.assertEqual(verified["event_id"], 1)
        with self.assertRaises(WebhookSignatureError):
            verifier.verify_event(raw + b" ", bmac_headers(raw), received_at=NOW)
        with self.assertRaises(WebhookSignatureError):
            verifier.verify_event(raw, {"x-signature-sha256": "0" * 64}, received_at=NOW)
        unsupported = bmac_event(
            2, "checkout.session.completed", self.donation_data(),
        )
        with self.assertRaises(WebhookPayloadError):
            verifier.verify_event(
                unsupported, bmac_headers(unsupported), received_at=NOW,
            )
        excessive_retry = bmac_event(
            3, "donation.created", self.donation_data(), attempt=6,
        )
        with self.assertRaises(WebhookPayloadError):
            verifier.verify_event(
                excessive_retry, bmac_headers(excessive_retry), received_at=NOW,
            )

    def test_direct_stripe_verifier_remains_optional_generic_infrastructure(self):
        verifier = StripeWebhookVerifier(STRIPE_SECRET)
        raw = stripe_body()
        self.assertEqual(
            verifier.verify_event(raw, stripe_headers(raw), received_at=NOW)["id"],
            "evt_testSupport123",
        )
        with self.assertRaises(WebhookSignatureError):
            verifier.verify_event(raw + b" ", stripe_headers(raw), received_at=NOW)

    def test_native_bmac_linked_donation_records_opaque_support_without_pii(self):
        adapter = self.bmac_adapter(linked=True)
        raw = bmac_event(10, "donation.created", self.donation_data())
        event = process_signed_webhook(
            adapter, self.ledger, self.guard,
            raw, bmac_headers(raw), received_at=NOW,
        )
        self.assertEqual(event.kind, "one_time_contribution")
        self.assertEqual(event.subject_key, self.account_subject)
        self.assertEqual(event.amount_minor, 1_500)
        self.assertEqual(event.currency, "USD")
        self.assertEqual(self.association_path.stat().st_mode & 0o777, 0o600)
        stored = self.association_path.read_text(encoding="utf-8")
        ledger = self.ledger_path.read_text(encoding="utf-8")
        for private in (
            "Private Name", "private@example.test", "Private message",
            "pi_private_transaction", "98765", BMAC_SECRET.decode("ascii"),
        ):
            self.assertNotIn(private, stored)
            self.assertNotIn(private, ledger)

    def test_native_bmac_unlinked_donation_is_pending_without_account_benefit(self):
        adapter = self.bmac_adapter(linked=False)
        raw = bmac_event(11, "donation.created", self.donation_data())
        event = process_signed_webhook(
            adapter, self.ledger, self.guard,
            raw, bmac_headers(raw), received_at=NOW,
        )
        supporter_key = opaque_key("bmac_supporter", "42", IDENTITY_KEY)
        self.assertEqual(event.subject_key, supporter_key)
        account_projection = self.ledger.privacy_safe_user_projection(
            self.account_subject, as_of=NOW,
        )
        self.assertEqual(account_projection["currency_totals_minor"], {})
        pending_projection = self.ledger.privacy_safe_user_projection(
            supporter_key, as_of=NOW,
        )
        self.assertEqual(pending_projection["currency_totals_minor"], {"USD": 1_500})

    def test_native_bmac_retry_attempt_is_restart_safe_and_replay_rejected(self):
        adapter = self.bmac_adapter()
        first_raw = bmac_event(12, "donation.created", self.donation_data())
        first = process_signed_webhook(
            adapter, self.ledger, self.guard,
            first_raw, bmac_headers(first_raw), received_at=NOW,
        )
        retry_raw = bmac_event(
            12, "donation.created", self.donation_data(), attempt=2,
        )
        with self.assertRaises(WebhookReplayError):
            process_signed_webhook(
                self.bmac_adapter(),
                ContributionLedger(
                    self.ledger_path,
                    integrity_key=INTEGRITY_KEY,
                    allow_test_path=True,
                ),
                FileWebhookReplayGuard(
                    self.replay_path,
                    integrity_key=INTEGRITY_KEY,
                    allow_test_path=True,
                ),
                retry_raw,
                bmac_headers(retry_raw),
                received_at=NOW,
            )
        self.assertEqual(self.ledger.events(), (first,))

    def test_native_bmac_refund_targets_exact_original_contribution(self):
        adapter = self.bmac_adapter()
        created_raw = bmac_event(20, "donation.created", self.donation_data())
        funding = process_signed_webhook(
            adapter, self.ledger, self.guard,
            created_raw, bmac_headers(created_raw), received_at=NOW,
        )
        refund_raw = bmac_event(
            21,
            "donation.refunded",
            self.donation_data(status="refunded", refunded="true"),
        )
        refund = process_signed_webhook(
            adapter, self.ledger, self.guard,
            refund_raw, bmac_headers(refund_raw), received_at=NOW,
        )
        self.assertEqual(refund.kind, "refund")
        self.assertEqual(refund.related_event_key, funding.source_event_key)
        self.assertEqual(refund.subject_key, funding.subject_key)
        projection = self.ledger.privacy_safe_user_projection(
            self.account_subject, as_of=NOW,
        )
        source = next(
            row for row in projection["recorded_allowance"]["sources"]
            if row["source_event_id"] == funding.event_id
        )
        self.assertEqual(source["refund_state"], "full")
        with self.assertRaisesRegex(WebhookPayloadError, "exact original"):
            unknown = bmac_event(
                22,
                "donation.refunded",
                self.donation_data(
                    id=99999,
                    transaction_id="pi_unknown",
                    status="refunded",
                    refunded="true",
                ),
            )
            adapter.verify_and_translate(
                unknown, bmac_headers(unknown), received_at=NOW,
            )

    def test_native_bmac_refund_crash_before_ledger_append_retries_exactly(self):
        adapter = self.bmac_adapter()
        created_raw = bmac_event(23, "donation.created", self.donation_data())
        funding = process_signed_webhook(
            adapter, self.ledger, self.guard,
            created_raw, bmac_headers(created_raw), received_at=NOW,
        )
        refund_raw = bmac_event(
            24,
            "donation.refunded",
            self.donation_data(status="refunded", refunded="true"),
        )
        expected = adapter.verify_and_translate(
            refund_raw, bmac_headers(refund_raw), received_at=NOW,
        )
        self.assertEqual(len(self.ledger.events()), 1)
        refund = process_signed_webhook(
            self.bmac_adapter(), self.ledger, self.guard,
            refund_raw, bmac_headers(refund_raw), received_at=NOW,
        )
        self.assertEqual(refund.related_event_key, funding.source_event_key)
        self.assertEqual(refund.amount_minor, expected.amount_minor)
        self.assertEqual(len(self.ledger.events()), 2)

    def test_native_bmac_membership_maps_started_updated_paused_cancelled(self):
        adapter = self.bmac_adapter()

        def process(event_id, event_type, data):
            raw = bmac_event(event_id, event_type, data)
            return process_signed_webhook(
                adapter, self.ledger, self.guard,
                raw, bmac_headers(raw), received_at=NOW,
            )

        started = process(30, "membership.started", self.membership_data())
        updated = process(31, "membership.updated", self.membership_data())
        paused = process(
            32, "membership.paused",
            self.membership_data(status="paused", paused="true"),
        )
        cancelled = process(
            33, "membership.cancelled",
            self.membership_data(status="canceled", canceled="true"),
        )
        self.assertEqual(started.kind, "recurring_started")
        self.assertEqual(updated.kind, "recurring_renewed")
        self.assertEqual(updated.related_event_key, started.source_event_key)
        for event in (paused, cancelled):
            self.assertEqual(event.kind, "recurring_canceled")
            self.assertEqual(event.related_event_key, started.source_event_key)
            self.assertEqual(event.contract_key, started.contract_key)
        stored = self.association_path.read_text(encoding="utf-8")
        self.assertNotIn("Private Name", stored)
        self.assertNotIn("private@example.test", stored)
        self.assertNotIn("Private Level", stored)

    def test_native_bmac_recurring_donation_maps_full_lifecycle(self):
        adapter = self.bmac_adapter()

        def process(event_id, event_type, data):
            raw = bmac_event(event_id, event_type, data)
            return process_signed_webhook(
                adapter, self.ledger, self.guard,
                raw, bmac_headers(raw), received_at=NOW,
            )

        started = process(
            40, "recurring_donation.started", self.recurring_data(),
        )
        updated = process(
            41, "recurring_donation.updated", self.recurring_data(),
        )
        cancelled = process(
            42,
            "recurring_donation.cancelled",
            self.recurring_data(status="canceled", canceled="true"),
        )
        self.assertEqual(started.kind, "recurring_started")
        self.assertEqual(updated.kind, "recurring_renewed")
        self.assertEqual(updated.related_event_key, started.source_event_key)
        self.assertEqual(cancelled.kind, "recurring_canceled")
        self.assertEqual(cancelled.related_event_key, started.source_event_key)

    def test_native_bmac_association_tamper_fails_closed(self):
        adapter = self.bmac_adapter()
        raw = bmac_event(50, "donation.created", self.donation_data())
        process_signed_webhook(
            adapter, self.ledger, self.guard,
            raw, bmac_headers(raw), received_at=NOW,
        )
        payload = json.loads(self.association_path.read_text(encoding="utf-8"))
        next(iter(payload["targets"].values()))["subject_key"] = opaque_key(
            "maestro_account_support", "tampered-account", IDENTITY_KEY,
        )
        self.association_path.write_text(json.dumps(payload), encoding="utf-8")
        refund_raw = bmac_event(
            51,
            "donation.refunded",
            self.donation_data(status="refunded", refunded="true"),
        )
        with self.assertRaises(SupportAssociationIntegrityError):
            adapter.verify_and_translate(
                refund_raw, bmac_headers(refund_raw), received_at=NOW,
            )

    def test_native_bmac_crash_before_ledger_append_retries_exactly(self):
        adapter = self.bmac_adapter()
        raw = bmac_event(60, "donation.created", self.donation_data())
        expected = adapter.verify_and_translate(
            raw, bmac_headers(raw), received_at=NOW,
        )
        self.assertEqual(self.ledger.events(), ())
        event = process_signed_webhook(
            self.bmac_adapter(), self.ledger, self.guard,
            raw, bmac_headers(raw), received_at=NOW,
        )
        self.assertEqual(event.source_event_key, expected.source_event_key)
        self.assertEqual(event.subject_key, expected.subject_key)
        self.assertEqual(len(self.ledger.events()), 1)

    @unittest.skipIf(sys.platform == "win32", "POSIX permission semantics")
    def test_native_bmac_runtime_files_require_0600_and_no_symlinks(self):
        root = Path(self.temp.name)
        config_path = root / "bmac-runtime.json"
        config_path.write_text(json.dumps({
            "schema_version": BMAC_RUNTIME_CONFIG_SCHEMA_VERSION,
            "creator_surface": STRIPE_BMAC_CREATOR_SURFACE,
            "deployment_scope": STRIPE_BMAC_DEPLOYMENT_SCOPE,
            "live_mode": False,
            "account_links": {},
        }), encoding="utf-8")
        config_path.chmod(0o600)
        secret_paths = {}
        for name, value in {
            "WEBHOOK": BMAC_SECRET,
            "IDENTITY": IDENTITY_KEY,
            "ASSOCIATION": INTEGRITY_KEY,
        }.items():
            path = root / f"{name.lower()}.secret"
            path.write_bytes(value)
            path.chmod(0o600)
            secret_paths[name] = path
        env = {
            "MAESTRO_SUPPORT_BMAC_CONFIG_FILE": str(config_path),
            "MAESTRO_SUPPORT_BMAC_WEBHOOK_SECRET_FILE": str(secret_paths["WEBHOOK"]),
            "MAESTRO_SUPPORT_BMAC_IDENTITY_HMAC_KEY_FILE": str(secret_paths["IDENTITY"]),
            "MAESTRO_SUPPORT_BMAC_ASSOCIATION_HMAC_KEY_FILE": str(secret_paths["ASSOCIATION"]),
        }
        loaded = BmacWebhookConfig.from_environment(env)
        self.assertFalse(loaded.live_mode)
        config_path.chmod(0o644)
        with self.assertRaisesRegex(SupportWebhookError, "0600"):
            BmacWebhookConfig.from_environment(env)
        config_path.chmod(0o600)
        symlink = root / "linked-runtime.json"
        symlink.symlink_to(config_path)
        env["MAESTRO_SUPPORT_BMAC_CONFIG_FILE"] = str(symlink)
        with self.assertRaisesRegex(SupportWebhookError, "symlink"):
            BmacWebhookConfig.from_environment(env)

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

    def test_signed_refund_revokes_promotional_bonus_without_mutation(self):
        funding_raw = body(event_id="signed-funding", amount_minor=2_500)
        funding_headers = self.adapter.headers(
            funding_raw, int(NOW.timestamp()),
        )
        funding_draft = self.adapter.verify_and_translate(
            funding_raw, funding_headers, received_at=NOW,
        )
        funding = process_signed_webhook(
            self.adapter, self.ledger, self.guard,
            funding_raw, funding_headers, received_at=NOW,
        )
        refund_raw = body(
            event_id="signed-refund",
            kind="refund",
            amount_minor=2_500,
            related_event_id="signed-funding",
        )
        process_signed_webhook(
            self.adapter, self.ledger, self.guard,
            refund_raw,
            self.adapter.headers(refund_raw, int(NOW.timestamp())),
            received_at=NOW,
        )
        projection = self.ledger.privacy_safe_user_projection(
            funding_draft.subject_key, as_of=NOW,
        )
        bonus = next(
            row for row in projection["recorded_allowance"]["sources"]
            if row["source_event_id"] == funding.event_id
        )
        self.assertEqual(bonus["source"], "supporter_tier_bonus")
        self.assertEqual(bonus["status"], "canceled")
        self.assertEqual(bonus["effective_allowance"], 0)
        self.assertEqual(self.ledger.events()[0], funding)

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
        self.assertEqual(adapter.verification_method, "owner_attested")
        self.assertEqual(self.adapter.verification_method, "signed_webhook")
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
        with self.assertRaisesRegex(SupportWebhookError, "does not prove"):
            process_signed_webhook(
                adapter, self.ledger, self.guard, b"{}", {}, received_at=NOW,
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
