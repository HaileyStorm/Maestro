"""Offline privacy, authority, and durability tests for the Support facade."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.account_auth import AccountAuthStore
from services.entitlements import (
    SUPPORT_PRIORITY_IDENTITY_CONTRACTS,
    ContributionEventDraft,
    ContributionLedger,
    opaque_key,
)
from services.responsible_use import (
    CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
    CURRENT_RESPONSIBLE_USE_VERSION,
    StaleResponsibleUseNoticeError,
    create_acceptance_record,
)
from services.support_catalog import load_support_catalog
from services.support_portal import (
    ResponsibleUseAcceptanceStore,
    ResponsibleUseStoreIntegrityError,
    SupportAuthorizationError,
    SupportPortal,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
LEDGER_KEY = b"portal-ledger-integrity-key-for-tests-000000"
IDENTITY_KEY = b"portal-account-identity-key-for-tests-00000"
ACCEPTANCE_KEY = b"portal-acceptance-integrity-key-for-tests-000"
PASSWORD = "correct horse battery staple"


class _Clock:
    def __init__(self, value: float = 1_800_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _accept_in_process(path: str, subject: str, start) -> None:
    store = ResponsibleUseAcceptanceStore(
        path,
        integrity_key=ACCEPTANCE_KEY,
        allow_test_path=True,
    )
    if not start.wait(10):
        raise RuntimeError("process start gate timed out")
    store.accept(
        subject,
        document_version=CURRENT_RESPONSIBLE_USE_VERSION,
        content_sha256=CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
        now=NOW,
    )


class SupportPortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ledger_path = root / "contributions.json"
        self.acceptance_path = root / "responsible-use.json"
        self.account_path = root / "accounts.json"
        self.clock = _Clock()
        self.account_store = AccountAuthStore(
            str(self.account_path),
            b"portal-account-store-secret-for-tests-000000",
            clock=self.clock,
            password_n=1024,
            session_ttl_seconds=3600,
            reauth_ttl_seconds=90,
            nonce_ttl_seconds=60,
        )
        owner_browser = "a" * 32
        owner_nonce = self.account_store.issue_nonce(
            owner_browser, "bootstrap",
        )["nonce"]
        owner = self.account_store.bootstrap_owner(
            username="Owner",
            password=PASSWORD,
            email="owner@example.test",
            device_label="Owner browser",
            nonce_session_id=owner_browser,
            nonce=owner_nonce,
            remote=False,
        )
        self.owner_session = owner["account_session_id"]
        self.owner_id = owner["account"]["id"]
        self.user_id, self.user_session = self._create_user(
            "PrivateUser", "b" * 32,
        )
        self.other_id, self.other_session = self._create_user(
            "OtherUser", "c" * 32,
        )
        self.ledger = ContributionLedger(
            self.ledger_path,
            integrity_key=LEDGER_KEY,
            allow_test_path=True,
        )
        self.store = ResponsibleUseAcceptanceStore(
            self.acceptance_path,
            integrity_key=ACCEPTANCE_KEY,
            allow_test_path=True,
        )
        self.portal = SupportPortal(
            account_store=self.account_store,
            ledger=self.ledger,
            acceptance_store=self.store,
            identity_key=IDENTITY_KEY,
            catalog=load_support_catalog(env={}, local_config_path=None),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _create_user(self, username: str, browser_session: str) -> tuple[str, str]:
        create_nonce = self.account_store.issue_nonce(
            self.owner_session, "create_account",
        )["nonce"]
        created = self.account_store.create_account(
            actor_session_id=self.owner_session,
            nonce=create_nonce,
            username=username,
            password=PASSWORD,
        )
        login_nonce = self.account_store.issue_nonce(
            browser_session, "login",
        )["nonce"]
        login = self.account_store.login(
            username=username,
            password=PASSWORD,
            device_label=f"{username} browser",
            nonce_session_id=browser_session,
            nonce=login_nonce,
            remote=True,
        )
        return created["account"]["id"], login["account_session_id"]

    @staticmethod
    def subject(account_id: str) -> str:
        return opaque_key(
            "maestro_account_support", account_id, IDENTITY_KEY,
        )

    def add_contribution(
        self,
        account_id: str,
        source: str,
        *,
        amount: int = 2_500,
    ) -> None:
        self.ledger.append(ContributionEventDraft(
            provider="fake_support",
            source_event_key=opaque_key("event", source, IDENTITY_KEY),
            subject_key=self.subject(account_id),
            kind="one_time_contribution",
            occurred_at="2026-08-11T11:00:00Z",
            amount_minor=amount,
        ), received_at=NOW)

    def test_default_public_catalog_is_truthfully_disabled_and_has_no_links(self):
        projection = self.portal.public_catalog_projection()
        providers = projection["provider_catalog"]["providers"]
        self.assertTrue(providers)
        self.assertEqual({item["state"] for item in providers}, {"disabled"})
        self.assertTrue(all(item["support_url"] is None for item in providers))
        self.assertFalse(
            projection["benefit_availability"]["scheduler_enforcement_enabled"]
        )
        self.assertEqual(
            projection["benefit_availability"]["effective_benefits"], [],
        )
        serialized = json.dumps(projection, sort_keys=True)
        self.assertNotIn("webhook", serialized.lower())
        self.assertNotIn("credential", serialized.lower())

    def test_only_server_approved_available_provider_gets_actionable_link(self):
        catalog = load_support_catalog(env={
            "MAESTRO_SUPPORT_GITHUB_SPONSORS_ENABLED": "true",
            "MAESTRO_SUPPORT_GITHUB_SPONSORS_URL": (
                "https://github.com/sponsors/example"
            ),
            "MAESTRO_SUPPORT_PATREON_ENABLED": "true",
        }, local_config_path=None)
        portal = SupportPortal(
            account_store=self.account_store,
            ledger=self.ledger,
            acceptance_store=self.store,
            identity_key=IDENTITY_KEY,
            catalog=catalog,
        )
        providers = portal.public_catalog_projection()["provider_catalog"][
            "providers"
        ]
        github = next(item for item in providers if item["provider_id"] == "github_sponsors")
        patreon = next(item for item in providers if item["provider_id"] == "patreon")
        self.assertEqual(github["state"], "available")
        self.assertEqual(
            github["support_url"], "https://github.com/sponsors/example",
        )
        self.assertEqual(patreon["state"], "unconfigured")
        self.assertIsNone(patreon["support_url"])
        self.assertTrue(all("public_home_url" not in item for item in providers))

    def test_provider_query_or_fragment_data_fails_closed_at_public_facade(self):
        for suffix in ("?email=private@example.test&token=secret", "#secret"):
            with self.subTest(suffix=suffix):
                catalog = load_support_catalog(env={
                    "MAESTRO_SUPPORT_GITHUB_SPONSORS_ENABLED": "true",
                    "MAESTRO_SUPPORT_GITHUB_SPONSORS_URL": (
                        "https://github.com/sponsors/example" + suffix
                    ),
                }, local_config_path=None)
                portal = SupportPortal(
                    account_store=self.account_store,
                    ledger=self.ledger,
                    acceptance_store=self.store,
                    identity_key=IDENTITY_KEY,
                    catalog=catalog,
                )
                with self.assertRaisesRegex(
                    ValueError, "query or fragment",
                ):
                    portal.public_catalog_projection()

    def test_self_projection_is_principal_bound_and_private(self):
        self.add_contribution(self.user_id, "mine")
        self.add_contribution(self.other_id, "other", amount=10_000)
        projection = self.portal.self_projection(self.user_session, remote=True)
        recorded = projection["account_support"]["recorded"]
        self.assertEqual(recorded["currency_totals_minor"], {"USD": 2_500})
        self.assertEqual(recorded["event_count"], 1)
        serialized = json.dumps(projection, sort_keys=True)
        for private in (
            self.user_id,
            self.other_id,
            "PrivateUser",
            self.user_session,
            self.subject(self.user_id),
        ):
            self.assertNotIn(private, serialized)
        self.assertNotIn("subject_key", serialized)
        self.assertNotIn("source_event_key", serialized)

    def test_linked_provider_support_stays_recorded_and_non_enforcing(self):
        provider_subject = opaque_key(
            "fake_support_subject", "private-provider-user", IDENTITY_KEY,
        )
        self.ledger.append(ContributionEventDraft(
            provider="fake_support",
            source_event_key=opaque_key(
                "fake_support_event", "linked-contribution", IDENTITY_KEY,
            ),
            subject_key=provider_subject,
            kind="one_time_contribution",
            occurred_at="2026-08-11T11:00:00Z",
            amount_minor=2_500,
        ), received_at=NOW)
        self.ledger.append(ContributionEventDraft(
            provider="fake_support",
            source_event_key=opaque_key(
                "fake_support_event", "verified-account-link", IDENTITY_KEY,
            ),
            subject_key=self.subject(self.user_id),
            kind="account_link_verified",
            occurred_at="2026-08-11T11:01:00Z",
            contract_key=opaque_key(
                "account_claim", self.user_id, IDENTITY_KEY,
            ),
            related_event_key=provider_subject,
        ), received_at=NOW)

        account_support = self.portal.self_projection(
            self.user_session, remote=True,
        )["account_support"]
        self.assertEqual(
            account_support["recorded"]["currency_totals_minor"],
            {"USD": 2_500},
        )
        allowance = account_support["recorded"]["recorded_allowance"]
        self.assertEqual(allowance["state"], "recorded_not_enforced")
        self.assertFalse(allowance["enforcement_enabled"])
        self.assertEqual(allowance["effective_allowance"], 0)
        self.assertEqual(allowance["sources"][0]["source"], "free")
        self.assertEqual(allowance["sources"][0]["status"], "inactive")
        self.assertEqual(
            account_support["benefits"]["state"], "recorded_not_enforced",
        )
        self.assertFalse(
            account_support["benefits"]["scheduler_enforcement_enabled"],
        )
        self.assertEqual(account_support["benefits"]["effective_benefits"], [])
        serialized = json.dumps(account_support, sort_keys=True)
        self.assertNotIn("private-provider-user", serialized)
        self.assertNotIn(provider_subject, serialized)
        admin = self.portal.owner_admin_projection(
            self.owner_session,
            remote=True,
            target_account_id=self.user_id,
        )["account_support"]["recorded"]
        self.assertEqual(admin["subject_key"], self.subject(self.user_id))

    def test_recorded_benefits_are_never_advertised_as_effective(self):
        self.add_contribution(self.user_id, "tier", amount=10_000)
        benefits = self.portal.self_projection(self.user_session, remote=True)[
            "account_support"
        ]["benefits"]
        self.assertEqual(benefits["state"], "recorded_not_enforced")
        self.assertFalse(benefits["scheduler_enforcement_enabled"])
        self.assertEqual(benefits["effective_benefits"], [])
        self.assertIn("retention_eligibility", benefits["recorded_eligibility"])
        serialized = json.dumps(
            self.portal.self_projection(self.user_session, remote=True)
        )
        self.assertNotIn("minimum_minor", serialized)
        self.assertNotIn("threshold", serialized.lower())

    def test_moody_priority_exclusions_are_exact_and_submission_preserving(self):
        policy = self.portal.public_catalog_projection()["support_priority"]
        self.assertFalse(policy["scheduler_enforcement_enabled"])
        self.assertFalse(policy["effective_priority_boost"])
        self.assertEqual(
            {item["capability_id"] for item in policy["exclusions"]},
            set(SUPPORT_PRIORITY_IDENTITY_CONTRACTS),
        )
        self.assertTrue(all(
            item["support_priority_eligible"] is False
            and item["marker"] == "creator_terms_exclude_support_priority"
            for item in policy["exclusions"]
        ))
        self.assertIn("Submission remains available", policy["notice"])
        self.assertNotIn("prompt", json.dumps(policy).lower())

    def test_live_session_identity_and_revocation_fail_closed(self):
        with self.assertRaises(SupportAuthorizationError):
            self.portal.self_projection("f" * 32, remote=True)
        with self.assertRaises(SupportAuthorizationError):
            self.portal.self_projection(self.user_id, remote=True)
        current = next(
            item for item in self.account_store.list_sessions(self.user_session)
            if item["current"]
        )
        nonce = self.account_store.issue_nonce(
            self.user_session, "revoke_session",
        )["nonce"]
        self.account_store.revoke_session(
            actor_session_id=self.user_session,
            target_handle=current["id"],
            nonce=nonce,
        )
        with self.assertRaises(SupportAuthorizationError):
            self.portal.self_projection(self.user_session, remote=True)

    def test_owner_admin_requires_capabilities_and_fresh_reauthentication(self):
        with self.assertRaises(SupportAuthorizationError):
            self.portal.owner_admin_projection(
                self.user_session,
                remote=True,
                target_account_id=self.user_id,
            )
        self.clock.advance(91)
        with self.assertRaises(SupportAuthorizationError):
            self.portal.owner_admin_projection(
                self.owner_session,
                remote=True,
                target_account_id=self.user_id,
            )

    def test_owner_admin_projection_is_opaque_and_account_store_targeted(self):
        self.add_contribution(self.user_id, "admin-target")
        projection = self.portal.owner_admin_projection(
            self.owner_session,
            remote=True,
            target_account_id=self.user_id,
        )
        recorded = projection["account_support"]["recorded"]
        self.assertEqual(recorded["event_count"], 1)
        self.assertRegex(recorded["subject_key"], r"^key_[0-9a-f]{64}$")
        serialized = json.dumps(projection, sort_keys=True)
        self.assertNotIn(self.user_id, serialized)
        self.assertNotIn("PrivateUser", serialized)
        self.assertNotIn("email", serialized.lower())
        with self.assertRaises(SupportAuthorizationError):
            self.portal.owner_admin_projection(
                self.owner_session,
                remote=True,
                target_account_id="e" * 32,
            )

    def test_responsible_use_acceptance_is_self_bound_and_restart_durable(self):
        before = self.portal.self_projection(
            self.user_session, remote=True,
        )["responsible_use"]
        self.assertFalse(before["status"]["accepted"])
        accepted = self.portal.accept_responsible_use(
            self.user_session,
            remote=True,
            document_version=CURRENT_RESPONSIBLE_USE_VERSION,
            content_sha256=CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
            now=NOW,
        )
        self.assertTrue(accepted["accepted"])
        restarted_store = ResponsibleUseAcceptanceStore(
            self.acceptance_path,
            integrity_key=ACCEPTANCE_KEY,
            allow_test_path=True,
        )
        restarted_accounts = AccountAuthStore(
            str(self.account_path),
            b"portal-account-store-secret-for-tests-000000",
            clock=self.clock,
            password_n=1024,
            session_ttl_seconds=3600,
            reauth_ttl_seconds=90,
            nonce_ttl_seconds=60,
        )
        restarted = SupportPortal(
            account_store=restarted_accounts,
            ledger=self.ledger,
            acceptance_store=restarted_store,
            identity_key=IDENTITY_KEY,
            catalog=load_support_catalog(env={}, local_config_path=None),
        )
        self.assertTrue(
            restarted.self_projection(
                self.user_session, remote=True,
            )["responsible_use"]["status"]["accepted"]
        )
        self.assertFalse(
            restarted.self_projection(
                self.other_session, remote=True,
            )["responsible_use"]["status"]["accepted"]
        )

    def test_public_record_checksum_cannot_authenticate_tampered_binding(self):
        self.portal.accept_responsible_use(
            self.user_session,
            remote=True,
            document_version=CURRENT_RESPONSIBLE_USE_VERSION,
            content_sha256=CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
            now=NOW,
        )
        payload = json.loads(self.acceptance_path.read_text(encoding="utf-8"))
        payload["records"][self.subject(self.user_id)] = create_acceptance_record(
            CURRENT_RESPONSIBLE_USE_VERSION,
            CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
            now=datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc),
        )
        # The replacement carries a valid public record_sha256, but the keyed
        # account+record envelope was not and cannot be recomputed here.
        self.acceptance_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ResponsibleUseStoreIntegrityError):
            self.store.status(self.subject(self.user_id))

    def test_wrong_key_and_oversized_or_duplicate_json_fail_closed(self):
        self.portal.accept_responsible_use(
            self.user_session,
            remote=True,
            document_version=CURRENT_RESPONSIBLE_USE_VERSION,
            content_sha256=CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
            now=NOW,
        )
        wrong = ResponsibleUseAcceptanceStore(
            self.acceptance_path,
            integrity_key=b"wrong-acceptance-integrity-key-for-tests-000",
            allow_test_path=True,
        )
        with self.assertRaises(ResponsibleUseStoreIntegrityError):
            wrong.status(self.subject(self.user_id))
        self.acceptance_path.write_text(
            '{"schema_version":1,"schema_version":1}', encoding="utf-8",
        )
        with self.assertRaises(ResponsibleUseStoreIntegrityError):
            self.store.status(self.subject(self.user_id))
        self.acceptance_path.write_bytes(b"{" + b"x" * (1024 * 1024) + b"}")
        with self.assertRaises(ResponsibleUseStoreIntegrityError):
            self.store.status(self.subject(self.user_id))

    def test_stale_acceptance_request_does_not_replace_current_record(self):
        accepted = self.portal.accept_responsible_use(
            self.user_session,
            remote=True,
            document_version=CURRENT_RESPONSIBLE_USE_VERSION,
            content_sha256=CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
            now=NOW,
        )
        before = self.acceptance_path.read_bytes()
        with self.assertRaises(StaleResponsibleUseNoticeError):
            self.portal.accept_responsible_use(
                self.user_session,
                remote=True,
                document_version=CURRENT_RESPONSIBLE_USE_VERSION,
                content_sha256="0" * 64,
                now=datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc),
            )
        self.assertEqual(self.acceptance_path.read_bytes(), before)
        self.assertEqual(
            self.portal.self_projection(
                self.user_session, remote=True,
            )["responsible_use"]["status"]["accepted_at"],
            accepted["accepted_at"],
        )

    def test_distinct_processes_serialize_account_acceptances(self):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        subjects = tuple(
            opaque_key("maestro_account_support", str(index), IDENTITY_KEY)
            for index in range(4)
        )
        processes = tuple(
            context.Process(
                target=_accept_in_process,
                args=(str(self.acceptance_path), subject, start),
            )
            for subject in subjects
        )
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(15)
        self.assertEqual([process.exitcode for process in processes], [0] * 4)
        self.assertTrue(all(self.store.status(subject)["accepted"] for subject in subjects))

    def test_failed_pre_replace_publication_preserves_previous_store(self):
        self.store.accept(
            self.subject(self.user_id),
            document_version=CURRENT_RESPONSIBLE_USE_VERSION,
            content_sha256=CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
            now=NOW,
        )
        before = self.acceptance_path.read_bytes()
        with mock.patch(
            "services.support_portal.os.replace",
            side_effect=OSError("synthetic replace failure"),
        ), self.assertRaises(OSError):
            self.store.accept(
                self.subject(self.other_id),
                document_version=CURRENT_RESPONSIBLE_USE_VERSION,
                content_sha256=CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
                now=NOW,
            )
        self.assertEqual(self.acceptance_path.read_bytes(), before)
        self.assertFalse(self.store.status(self.subject(self.other_id))["accepted"])

    def test_projection_schema_has_no_creative_contact_or_credential_fields(self):
        self.add_contribution(self.user_id, "privacy")
        projection = self.portal.self_projection(self.user_session, remote=True)
        forbidden = {
            "prompt", "media", "job", "log", "contact", "email",
            "credential", "password", "session", "token", "output",
        }

        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield str(key).lower()
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)

        observed = set(keys(projection))
        self.assertTrue(observed.isdisjoint(forbidden))
        digest = hashlib.sha256(json.dumps(
            projection, sort_keys=True,
        ).encode()).hexdigest()
        self.assertRegex(digest, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
