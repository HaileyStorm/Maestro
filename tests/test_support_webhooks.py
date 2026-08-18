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
    FakeSignedWebhookAdapter,
    FileWebhookReplayGuard,
    ManualContributionAdapter,
    STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT,
    StripeAssociationIntegrityError,
    StripeBmacSupportWebhookAdapter,
    StripeBmacWebhookConfig,
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


class SupportWebhookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ledger_path = root / "contributions.json"
        self.replay_path = root / "replay.json"
        self.association_path = root / "stripe-associations.json"
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

    def stripe_adapter(
        self,
        *,
        payment_link: str = "plink_test_support",
        customer: str = "cus_test_linked",
    ) -> StripeBmacSupportWebhookAdapter:
        config = StripeBmacWebhookConfig.from_runtime_json(
            json.dumps({
                "schema_version": 1,
                "livemode": False,
                "payment_links": {
                    payment_link: {"currency": "USD"},
                },
                "prices": {
                    "price_test_support": {"currency": "USD"},
                },
                "account_links": {customer: self.account_subject},
            }),
            signing_secret=STRIPE_SECRET,
            identity_secret=IDENTITY_KEY,
            association_integrity_key=INTEGRITY_KEY,
        )
        return StripeBmacSupportWebhookAdapter(
            config,
            association_path=self.association_path,
            allow_test_path=True,
        )

    @staticmethod
    def checkout_object(**overrides) -> dict:
        payload = {
            "id": "cs_test_support",
            "object": "checkout.session",
            "livemode": False,
            "mode": "payment",
            "status": "complete",
            "payment_status": "paid",
            "payment_link": "plink_test_support",
            "customer": "cus_test_linked",
            "payment_intent": "pi_test_support",
            "amount_total": 2_500,
            "currency": "usd",
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def invoice_object(**overrides) -> dict:
        payload = {
            "id": "in_test_support",
            "object": "invoice",
            "livemode": False,
            "status": "paid",
            "paid": True,
            "customer": "cus_test_linked",
            "subscription": "sub_test_support",
            "billing_reason": "subscription_create",
            "amount_paid": 1_200,
            "currency": "usd",
            "lines": {"data": [{"price": {"id": "price_test_support"}}]},
            "payments": {"data": [{
                "status": "paid",
                "payment": {"payment_intent": "pi_test_invoice"},
            }]},
        }
        payload.update(overrides)
        return payload

    def test_stripe_bmac_contract_is_disabled_non_authorizing_and_public_safe(self):
        projection = STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT.public_projection()
        self.assertFalse(projection["enabled"])
        self.assertEqual(projection["verification"], "signed_webhook_required")
        self.assertEqual(projection["radar_role"], "fraud_screening_only")
        self.assertFalse(projection["grants_app_or_account_authorization"])
        self.assertFalse(projection["projects_personal_address_or_phone"])
        self.assertFalse(projection["projects_api_keys_or_provider_subjects"])
        self.assertEqual(projection["server_mapping_keys"], ["payment_link", "price"])
        serialized = json.dumps(projection, sort_keys=True).lower()
        self.assertNotIn("secret", serialized)
        self.assertNotIn("email", serialized)

    def test_stripe_verifier_requires_exact_raw_body_signature_and_freshness(self):
        verifier = StripeWebhookVerifier(STRIPE_SECRET)
        raw = stripe_body()
        verified = verifier.verify_event(
            raw, stripe_headers(raw), received_at=NOW,
        )
        self.assertEqual(verified["id"], "evt_testSupport123")
        with self.assertRaises(WebhookSignatureError):
            verifier.verify_event(
                raw + b" ", stripe_headers(raw), received_at=NOW,
            )
        with self.assertRaises(WebhookTimestampError):
            verifier.verify_event(
                raw,
                stripe_headers(raw, NOW - timedelta(minutes=6)),
                received_at=NOW,
            )
        unsupported = stripe_body(type="radar.early_fraud_warning.created")
        with self.assertRaises(WebhookPayloadError):
            verifier.verify_event(
                unsupported, stripe_headers(unsupported), received_at=NOW,
            )

    def test_stripe_verifier_accepts_declared_refund_reversal_evidence(self):
        verifier = StripeWebhookVerifier(STRIPE_SECRET)
        raw = stripe_body(type="charge.refunded")
        verified = verifier.verify_event(
            raw, stripe_headers(raw), received_at=NOW,
        )
        self.assertEqual(verified["type"], "charge.refunded")

    def test_production_stripe_checkout_records_only_approved_opaque_support(self):
        adapter = self.stripe_adapter()
        checkout = self.checkout_object(
            metadata={"maestro_account": "attacker-controlled"},
            customer_details={"email": "private@example.test"},
        )
        raw = stripe_event(
            "evt_checkout_success", "checkout.session.completed", checkout,
        )
        event = process_signed_webhook(
            adapter, self.ledger, self.guard,
            raw, stripe_headers(raw), received_at=NOW,
        )
        self.assertTrue(adapter.production_ready)
        self.assertEqual(event.kind, "one_time_contribution")
        self.assertEqual(event.subject_key, self.account_subject)
        self.assertEqual(event.amount_minor, 2_500)
        self.assertEqual(event.currency, "USD")
        self.assertEqual(self.association_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            self.association_path.with_suffix(".json.lock").stat().st_mode & 0o777,
            0o600,
        )
        stored = self.association_path.read_text(encoding="utf-8")
        for private in (
            "cs_test_support", "cus_test_linked", "pi_test_support",
            "private@example.test", "attacker-controlled",
            STRIPE_SECRET.decode("ascii"),
        ):
            self.assertNotIn(private, stored)
        self.assertNotIn(STRIPE_SECRET.decode("ascii"), repr(adapter))
        self.assertNotIn(IDENTITY_KEY.decode("ascii"), repr(adapter.config))

    def test_production_stripe_rejects_unknown_mapping_and_missing_account_link(self):
        adapter = self.stripe_adapter()
        unknown_mapping = self.checkout_object(payment_link="plink_unknown_support")
        raw = stripe_event(
            "evt_unknown_mapping", "checkout.session.completed", unknown_mapping,
        )
        with self.assertRaisesRegex(WebhookPayloadError, "not approved"):
            adapter.verify_and_translate(
                raw, stripe_headers(raw), received_at=NOW,
            )

        missing_link = self.checkout_object(
            customer="cus_unlinked_customer",
            metadata={"maestro_account": self.account_subject},
            customer_details={"email": "linked-looking@example.test"},
        )
        raw = stripe_event(
            "evt_missing_account", "checkout.session.completed", missing_link,
        )
        with self.assertRaisesRegex(WebhookPayloadError, "no existing opaque"):
            adapter.verify_and_translate(
                raw, stripe_headers(raw), received_at=NOW,
            )

    def test_production_stripe_replay_is_restart_safe(self):
        adapter = self.stripe_adapter()
        raw = stripe_event(
            "evt_checkout_replay", "checkout.session.completed",
            self.checkout_object(),
        )
        first = process_signed_webhook(
            adapter, self.ledger, self.guard,
            raw, stripe_headers(raw), received_at=NOW,
        )
        restarted = self.stripe_adapter()
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
                restarted, restarted_ledger, restarted_guard,
                raw, stripe_headers(raw), received_at=NOW,
            )
        self.assertEqual(restarted_ledger.events(), (first,))

    def test_production_stripe_refund_and_dispute_target_exact_funding(self):
        adapter = self.stripe_adapter()
        funding_raw = stripe_event(
            "evt_checkout_original", "checkout.session.completed",
            self.checkout_object(),
        )
        funding = process_signed_webhook(
            adapter, self.ledger, self.guard,
            funding_raw, stripe_headers(funding_raw), received_at=NOW,
        )
        refund_raw = stripe_event(
            "evt_refund_original", "charge.refunded",
            {
                "id": "ch_test_support",
                "object": "charge",
                "livemode": False,
                "payment_intent": "pi_test_support",
                "amount_refunded": 500,
                "currency": "usd",
                "metadata": {"radar": "fraud-screening-only"},
            },
        )
        refund = process_signed_webhook(
            adapter, self.ledger, self.guard,
            refund_raw, stripe_headers(refund_raw), received_at=NOW,
        )
        dispute_raw = stripe_event(
            "evt_dispute_original", "charge.dispute.created",
            {
                "id": "du_test_support",
                "object": "dispute",
                "livemode": False,
                "charge": "ch_test_support",
                "payment_intent": "pi_test_support",
                "amount": 700,
                "currency": "usd",
                "status": "needs_response",
                "metadata": {"maestro_account": "must-not-authorize"},
            },
        )
        dispute = process_signed_webhook(
            adapter, self.ledger, self.guard,
            dispute_raw, stripe_headers(dispute_raw), received_at=NOW,
        )
        self.assertEqual(refund.kind, "refund")
        self.assertEqual(dispute.kind, "chargeback")
        self.assertEqual(refund.related_event_key, funding.source_event_key)
        self.assertEqual(dispute.related_event_key, funding.source_event_key)
        self.assertEqual(refund.subject_key, funding.subject_key)
        self.assertEqual(dispute.subject_key, funding.subject_key)
        stored = self.association_path.read_text(encoding="utf-8")
        self.assertNotIn("ch_test_support", stored)
        self.assertNotIn("pi_test_support", stored)
        self.assertNotIn("radar", stored.lower())

    def test_production_stripe_subscription_deletion_targets_original_start(self):
        adapter = self.stripe_adapter()
        invoice_raw = stripe_event(
            "evt_invoice_start", "invoice.paid", self.invoice_object(),
        )
        started = process_signed_webhook(
            adapter, self.ledger, self.guard,
            invoice_raw, stripe_headers(invoice_raw), received_at=NOW,
        )
        renewal_raw = stripe_event(
            "evt_invoice_renewal",
            "invoice.paid",
            self.invoice_object(
                id="in_test_renewal",
                billing_reason="subscription_cycle",
                payments={"data": [{
                    "status": "paid",
                    "payment": {"payment_intent": "pi_test_renewal"},
                }]},
            ),
        )
        renewed = process_signed_webhook(
            adapter, self.ledger, self.guard,
            renewal_raw, stripe_headers(renewal_raw), received_at=NOW,
        )
        deleted_raw = stripe_event(
            "evt_subscription_deleted", "customer.subscription.deleted",
            {
                "id": "sub_test_support",
                "object": "subscription",
                "livemode": False,
                "status": "canceled",
                "customer": "cus_untrusted_payload_only",
            },
        )
        canceled = process_signed_webhook(
            adapter, self.ledger, self.guard,
            deleted_raw, stripe_headers(deleted_raw), received_at=NOW,
        )
        self.assertEqual(started.kind, "recurring_started")
        self.assertEqual(renewed.kind, "recurring_renewed")
        self.assertEqual(renewed.related_event_key, started.source_event_key)
        self.assertEqual(canceled.kind, "recurring_canceled")
        self.assertEqual(canceled.related_event_key, started.source_event_key)
        self.assertEqual(canceled.contract_key, started.contract_key)
        self.assertEqual(canceled.subject_key, started.subject_key)

    def test_production_stripe_association_tamper_fails_closed(self):
        adapter = self.stripe_adapter()
        raw = stripe_event(
            "evt_tamper_origin", "checkout.session.completed",
            self.checkout_object(),
        )
        process_signed_webhook(
            adapter, self.ledger, self.guard,
            raw, stripe_headers(raw), received_at=NOW,
        )
        payload = json.loads(self.association_path.read_text(encoding="utf-8"))
        target = next(iter(payload["targets"].values()))
        target["subject_key"] = opaque_key(
            "maestro_account_support", "tampered-account", IDENTITY_KEY,
        )
        self.association_path.write_text(json.dumps(payload), encoding="utf-8")
        refund_raw = stripe_event(
            "evt_tamper_refund", "charge.refunded",
            {
                "id": "ch_test_tamper",
                "object": "charge",
                "livemode": False,
                "payment_intent": "pi_test_support",
                "amount_refunded": 500,
                "currency": "usd",
            },
        )
        with self.assertRaises(StripeAssociationIntegrityError):
            adapter.verify_and_translate(
                refund_raw, stripe_headers(refund_raw), received_at=NOW,
            )

    def test_production_stripe_crash_before_ledger_append_retries_exactly(self):
        adapter = self.stripe_adapter()
        raw = stripe_event(
            "evt_crash_association", "checkout.session.completed",
            self.checkout_object(),
        )
        expected = adapter.verify_and_translate(
            raw, stripe_headers(raw), received_at=NOW,
        )
        self.assertEqual(self.ledger.events(), ())
        restarted = self.stripe_adapter()
        event = process_signed_webhook(
            restarted, self.ledger, self.guard,
            raw, stripe_headers(raw), received_at=NOW,
        )
        self.assertEqual(event.source_event_key, expected.source_event_key)
        self.assertEqual(event.subject_key, expected.subject_key)
        self.assertEqual(len(self.ledger.events()), 1)

    @unittest.skipIf(sys.platform == "win32", "POSIX permission semantics")
    def test_production_stripe_runtime_files_require_0600_and_no_symlinks(self):
        root = Path(self.temp.name)
        config_path = root / "stripe-runtime.json"
        config_path.write_text(json.dumps({
            "schema_version": 1,
            "livemode": False,
            "payment_links": {"plink_test_support": {"currency": "USD"}},
            "prices": {"price_test_support": {"currency": "USD"}},
            "account_links": {"cus_test_linked": self.account_subject},
        }), encoding="utf-8")
        config_path.chmod(0o600)
        secret_paths = {}
        for name, value in {
            "WEBHOOK": STRIPE_SECRET,
            "IDENTITY": IDENTITY_KEY,
            "ASSOCIATION": INTEGRITY_KEY,
        }.items():
            path = root / f"{name.lower()}.secret"
            path.write_bytes(value)
            path.chmod(0o600)
            secret_paths[name] = path
        env = {
            "MAESTRO_SUPPORT_STRIPE_BMAC_CONFIG_FILE": str(config_path),
            "MAESTRO_SUPPORT_STRIPE_WEBHOOK_SECRET_FILE": str(
                secret_paths["WEBHOOK"],
            ),
            "MAESTRO_SUPPORT_STRIPE_IDENTITY_HMAC_KEY_FILE": str(
                secret_paths["IDENTITY"],
            ),
            "MAESTRO_SUPPORT_STRIPE_ASSOCIATION_HMAC_KEY_FILE": str(
                secret_paths["ASSOCIATION"],
            ),
        }
        loaded = StripeBmacWebhookConfig.from_environment(env)
        self.assertFalse(loaded.livemode)
        config_path.chmod(0o644)
        with self.assertRaisesRegex(SupportWebhookError, "0600"):
            StripeBmacWebhookConfig.from_environment(env)
        config_path.chmod(0o600)
        symlink = root / "linked-runtime.json"
        symlink.symlink_to(config_path)
        env["MAESTRO_SUPPORT_STRIPE_BMAC_CONFIG_FILE"] = str(symlink)
        with self.assertRaisesRegex(SupportWebhookError, "symlink"):
            StripeBmacWebhookConfig.from_environment(env)

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
