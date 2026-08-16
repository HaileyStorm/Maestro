"""Offline privacy, authority, and durability tests for the Support facade."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
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
    ContributionConflict,
    ContributionEventDraft,
    ContributionLedger,
    opaque_key,
)
from services import entitlements
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
    SupportPortalError,
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

    def direct_compute_portal(self) -> SupportPortal:
        return SupportPortal(
            account_store=self.account_store,
            ledger=self.ledger,
            acceptance_store=self.store,
            identity_key=IDENTITY_KEY,
            catalog=load_support_catalog(env={
                "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_ENABLED": "true",
                "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_URL": (
                    "https://support.operator.com/maestro"
                ),
            }, local_config_path=None),
        )

    @staticmethod
    def provider(projection, provider_id):
        return next(
            item for item in projection["provider_catalog"]["providers"]
            if item["provider_id"] == provider_id
        )

    def test_default_public_catalog_is_truthfully_disabled_and_has_no_links(self):
        projection = self.portal.public_catalog_projection()
        providers = projection["provider_catalog"]["providers"]
        self.assertEqual(
            [item["provider_id"] for item in providers],
            [
                "buy_me_a_coffee",
                "patreon",
                "direct_compute_sponsorship",
            ],
        )
        self.assertEqual(
            {item["provider_id"]: item["state"] for item in providers},
            {
                "buy_me_a_coffee": "disabled",
                "patreon": "disabled",
                "direct_compute_sponsorship": "locked",
            },
        )
        self.assertTrue(all(item["support_url"] is None for item in providers))
        self.assertEqual(projection["development_cost_recovery"], {
            "target_minor": 100_000,
            "currency": "USD",
            "state": "locked",
        })
        self.assertEqual(
            set(projection["development_cost_recovery"]),
            {"target_minor", "currency", "state"},
        )
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
            "MAESTRO_SUPPORT_BUY_ME_A_COFFEE_ENABLED": "true",
            "MAESTRO_SUPPORT_BUY_ME_A_COFFEE_URL": (
                "https://buymeacoffee.com/example"
            ),
            "MAESTRO_SUPPORT_PATREON_ENABLED": "true",
            "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_ENABLED": "true",
            "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_URL": (
                "https://support.operator.com/maestro"
            ),
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
        coffee = next(
            item for item in providers
            if item["provider_id"] == "buy_me_a_coffee"
        )
        patreon = next(
            item for item in providers
            if item["provider_id"] == "patreon"
        )
        self.assertEqual(coffee["state"], "available")
        self.assertEqual(
            coffee["support_url"], "https://buymeacoffee.com/example",
        )
        self.assertEqual(patreon["state"], "unconfigured")
        self.assertIsNone(patreon["support_url"])
        direct = next(
            item for item in providers
            if item["provider_id"] == "direct_compute_sponsorship"
        )
        self.assertTrue(direct["enabled"])
        self.assertTrue(direct["configured"])
        self.assertEqual(direct["state"], "locked")
        self.assertIsNone(direct["support_url"])
        self.assertTrue(all("public_home_url" not in item for item in providers))

    def test_catalog_loader_refreshes_public_and_self_projection_together(self):
        current = [load_support_catalog(env={}, local_config_path=None)]
        portal = SupportPortal(
            account_store=self.account_store,
            ledger=self.ledger,
            acceptance_store=self.store,
            identity_key=IDENTITY_KEY,
            catalog_loader=lambda: current[0],
        )
        before = portal.public_catalog_projection()["provider_catalog"]
        current[0] = load_support_catalog(env={
            "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_ENABLED": "true",
            "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_URL": (
                "https://support.operator.com/maestro"
            ),
        }, local_config_path=None)
        public = portal.public_catalog_projection()["provider_catalog"]
        authenticated = portal.self_projection(
            self.user_session, remote=True,
        )["provider_catalog"]
        self.assertNotEqual(before, public)
        self.assertEqual(authenticated, public)
        direct = next(
            item for item in public["providers"]
            if item["provider_id"] == "direct_compute_sponsorship"
        )
        self.assertEqual(direct["state"], "locked")
        self.assertIsNone(direct["support_url"])
        self.add_contribution(self.user_id, "recovered", amount=100_000)
        public = portal.public_catalog_projection()["provider_catalog"]
        authenticated = portal.self_projection(
            self.user_session, remote=True,
        )["provider_catalog"]
        self.assertEqual(authenticated, public)
        direct = next(
            item for item in public["providers"]
            if item["provider_id"] == "direct_compute_sponsorship"
        )
        self.assertEqual(direct["state"], "available")
        self.assertEqual(
            direct["support_url"],
            "https://support.operator.com/maestro",
        )

    def test_recovery_boundary_is_server_owned_private_and_surface_invariant(self):
        portal = self.direct_compute_portal()
        self.add_contribution(self.user_id, "boundary-low", amount=99_999)
        with mock.patch.dict(os.environ, {
            "MAESTRO_DEVELOPMENT_COST_RECOVERY_TARGET_MINOR": "1",
            "MAESTRO_DEVELOPMENT_COST_RECOVERY_STATE": "recovered",
        }, clear=False):
            public = portal.public_catalog_projection()
            remote = portal.self_projection(self.user_session, remote=True)
            local = portal.self_projection(self.user_session, remote=False)

        expected_locked = {
            "target_minor": 100_000,
            "currency": "USD",
            "state": "locked",
        }
        self.assertEqual(public["development_cost_recovery"], expected_locked)
        self.assertEqual(remote["development_cost_recovery"], expected_locked)
        self.assertEqual(local["development_cost_recovery"], expected_locked)
        self.assertEqual(
            self.provider(public, "direct_compute_sponsorship")["state"],
            "locked",
        )
        serialized = json.dumps(
            public["development_cost_recovery"], sort_keys=True,
        )
        self.assertNotIn("99999", serialized)
        self.assertNotIn("subject", serialized.lower())
        self.assertNotIn("event", serialized.lower())

        self.add_contribution(self.other_id, "boundary-one", amount=1)
        recovered = portal.public_catalog_projection()
        self.assertEqual(recovered["development_cost_recovery"], {
            "target_minor": 100_000,
            "currency": "USD",
            "state": "recovered",
        })
        direct = self.provider(recovered, "direct_compute_sponsorship")
        self.assertEqual(direct["state"], "available")
        self.assertEqual(
            direct["support_url"], "https://support.operator.com/maestro",
        )

    def test_recovery_read_or_shape_failure_keeps_every_unlock_closed(self):
        portal = self.direct_compute_portal()
        self.add_contribution(self.user_id, "malformed-gate-allowance")
        hosted = {
            "MAESTRO_ACCOUNTS_ENABLED": "true",
            "MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED": "true",
            "MAESTRO_COMPUTE_EXECUTION_REALM": "hosted",
        }
        invalid = (
            None,
            {"target_minor": 100_000, "currency": "USD"},
            {
                "target_minor": 100_000,
                "currency": "USD",
                "state": "recovered",
                "recovered_minor": 100_000,
            },
            {"target_minor": True, "currency": "USD", "state": "recovered"},
            {"target_minor": 1, "currency": "USD", "state": "recovered"},
            {"target_minor": 100_000, "currency": "usd", "state": "recovered"},
            {"target_minor": 100_000, "currency": "USD", "state": "open"},
        )
        with mock.patch.dict(os.environ, hosted, clear=False):
            for value in invalid:
                with self.subTest(value=value), mock.patch.object(
                    self.ledger,
                    "development_cost_recovery_projection",
                    return_value=value,
                ):
                    public = portal.public_catalog_projection()
                    remote = portal.self_projection(
                        self.user_session, remote=True,
                    )
                    local = portal.self_projection(
                        self.user_session, remote=False,
                    )
                    for projection in (public, remote, local):
                        self.assertEqual(
                            projection["development_cost_recovery"]["state"],
                            "locked",
                        )
                        self.assertEqual(
                            self.provider(
                                projection, "direct_compute_sponsorship",
                            )["state"],
                            "locked",
                        )
                        self.assertEqual(
                            projection["benefit_availability"]["state"],
                            "development_cost_recovery_locked",
                        )
                        self.assertEqual(
                            projection["support_priority"]["state"],
                            "development_cost_recovery_locked",
                        )
                    self.assertEqual(
                        remote["account_support"]["benefits"]["state"],
                        "development_cost_recovery_locked",
                    )
                    self.assertEqual(
                        local["account_support"]["benefits"]["state"],
                        "development_cost_recovery_locked",
                    )
                    self.assertEqual(
                        remote["account_support"]["recorded"][
                            "recorded_allowance"
                        ]["state"],
                        "recorded_not_enforced",
                    )
            with mock.patch.object(
                self.ledger,
                "development_cost_recovery_projection",
                side_effect=OSError("synthetic ledger outage"),
            ):
                projection = portal.self_projection(
                    self.user_session, remote=True,
                )
        self.assertEqual(
            projection["development_cost_recovery"]["state"], "locked",
        )
        self.assertEqual(
            projection["account_support"]["benefits"]["state"],
            "development_cost_recovery_locked",
        )

    def test_one_recovery_snapshot_controls_each_projection_atomically(self):
        portal = self.direct_compute_portal()
        recovered = {
            "target_minor": 100_000,
            "currency": "USD",
            "state": "recovered",
        }
        with mock.patch.object(
            self.ledger,
            "development_cost_recovery_projection",
            side_effect=[recovered, AssertionError("read twice")],
        ) as projection_reader:
            projection = portal.self_projection(
                self.user_session, remote=True,
            )
        self.assertEqual(projection_reader.call_count, 1)
        self.assertEqual(
            projection["development_cost_recovery"]["state"], "recovered",
        )
        self.assertEqual(
            self.provider(
                projection, "direct_compute_sponsorship",
            )["state"],
            "available",
        )
        self.assertNotEqual(
            projection["benefit_availability"]["state"],
            "development_cost_recovery_locked",
        )

    def test_catalog_only_adapter_has_no_implicit_unlock_authority(self):
        catalog = load_support_catalog(env={
            "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_ENABLED": "true",
            "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_URL": (
                "https://support.operator.com/maestro"
            ),
        }, local_config_path=None)

        class CatalogOnlyAdapter:
            def _catalog_snapshot(self):
                return catalog

            @staticmethod
            def _scheduler_enforcement_enabled():
                return True

        projection = SupportPortal.public_catalog_projection(
            CatalogOnlyAdapter(),
        )
        self.assertEqual(
            projection["development_cost_recovery"]["state"], "locked",
        )
        self.assertEqual(
            self.provider(
                projection, "direct_compute_sponsorship",
            )["state"],
            "locked",
        )
        self.assertEqual(
            projection["benefit_availability"]["state"],
            "development_cost_recovery_locked",
        )

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
        self.assertEqual(allowance["effective_allowance"], 300)
        self.assertEqual(allowance["sources"][0]["source"], "free")
        self.assertEqual(allowance["sources"][0]["status"], "inactive")
        self.assertEqual(allowance["sources"][1]["source"], "one_time_support")
        self.assertEqual(allowance["sources"][1]["status"], "active")
        self.assertEqual(
            account_support["benefits"]["state"],
            "development_cost_recovery_locked",
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
        self.assertEqual(
            benefits["state"], "development_cost_recovery_locked",
        )
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

    def test_hosted_credit_policy_projects_active_allowance_truthfully(self):
        self.add_contribution(self.user_id, "hosted-credit-active")
        self.add_contribution(
            self.other_id, "hosted-recovery", amount=97_500,
        )
        with mock.patch.dict(os.environ, {
            "MAESTRO_ACCOUNTS_ENABLED": "true",
            "MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED": "true",
            "MAESTRO_COMPUTE_EXECUTION_REALM": "hosted",
        }, clear=False):
            projection = self.portal.self_projection(
                self.user_session,
                remote=True,
            )

        public = projection["benefit_availability"]
        account = projection["account_support"]
        allowance = account["recorded"]["recorded_allowance"]
        self.assertTrue(public["scheduler_enforcement_enabled"])
        self.assertEqual(public["state"], "hosted_priority_available")
        self.assertEqual(allowance["state"], "active")
        self.assertTrue(allowance["enforcement_enabled"])
        self.assertEqual(account["benefits"]["state"], "active")
        self.assertEqual(
            account["benefits"]["effective_benefits"],
            ["bounded_queue_priority"],
        )

    def test_hosted_credit_projection_keeps_owner_and_zero_allowance_exempt(self):
        self.add_contribution(self.owner_id, "owner-hosted-credit")
        default_owner = self.portal.self_projection(
            self.owner_session, remote=True,
        )["account_support"]
        self.assertEqual(
            default_owner["benefits"]["state"],
            "development_cost_recovery_locked",
        )
        self.assertIn("recorded_allowance", default_owner["recorded"])
        self.add_contribution(
            self.user_id, "owner-recovery", amount=97_500,
        )
        hosted = {
            "MAESTRO_ACCOUNTS_ENABLED": "true",
            "MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED": "true",
            "MAESTRO_COMPUTE_EXECUTION_REALM": "hosted",
        }
        with mock.patch.dict(os.environ, hosted, clear=False):
            owner = self.portal.self_projection(
                self.owner_session, remote=True,
            )["account_support"]
            zero = self.portal.self_projection(
                self.other_session, remote=True,
            )["account_support"]

        self.assertEqual(owner["benefits"]["state"], "owner_exempt")
        self.assertEqual(owner["benefits"]["effective_benefits"], [])
        self.assertEqual(
            owner["recorded"]["recorded_allowance"]["state"],
            "recorded_not_enforced",
        )
        self.assertEqual(
            zero["benefits"]["state"], "hosted_priority_available",
        )
        self.assertEqual(zero["benefits"]["effective_benefits"], [])
        self.assertEqual(
            zero["recorded"]["recorded_allowance"]["state"],
            "recorded_not_enforced",
        )

    def test_hosted_benefits_remain_inert_below_recovery_target(self):
        self.add_contribution(self.user_id, "hosted-but-locked")
        with mock.patch.dict(os.environ, {
            "MAESTRO_ACCOUNTS_ENABLED": "true",
            "MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED": "true",
            "MAESTRO_COMPUTE_EXECUTION_REALM": "hosted",
        }, clear=False):
            projection = self.portal.self_projection(
                self.user_session, remote=True,
            )

        self.assertTrue(projection["benefit_availability"][
            "scheduler_enforcement_enabled"
        ])
        self.assertEqual(
            projection["benefit_availability"]["state"],
            "development_cost_recovery_locked",
        )
        account = projection["account_support"]
        self.assertEqual(
            account["benefits"]["state"],
            "development_cost_recovery_locked",
        )
        self.assertEqual(account["benefits"]["effective_benefits"], [])
        self.assertEqual(
            account["recorded"]["recorded_allowance"]["state"],
            "recorded_not_enforced",
        )

    def test_scheduler_resolver_failure_fails_closed(self):
        portal = SupportPortal(
            account_store=self.account_store,
            ledger=self.ledger,
            acceptance_store=self.store,
            identity_key=IDENTITY_KEY,
            catalog=load_support_catalog(env={}, local_config_path=None),
            scheduler_enforcement_resolver=lambda: (_ for _ in ()).throw(
                OSError("synthetic journal outage")
            ),
        )
        projection = portal.public_catalog_projection()
        self.assertFalse(
            projection["benefit_availability"]["scheduler_enforcement_enabled"],
        )

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

    def test_owner_fulfillment_transition_is_server_derived_and_idempotent(self):
        self.add_contribution(self.user_id, "admin-fulfillment")
        target = self.ledger.events()[0]
        request = {
            "remote": True,
            "target_account_id": self.user_id,
            "target_event_id": target.event_id,
            "item": "one_time_credit_grant",
            "status": "pending",
            "idempotency_key": opaque_key(
                "fulfillment_request", "request-1", IDENTITY_KEY,
            ),
            "proof_reference": opaque_key(
                "fulfillment_proof", "proof-1", IDENTITY_KEY,
            ),
        }
        projection = self.portal.transition_owner_fulfillment(
            self.owner_session, **request,
        )
        self.assertEqual(
            projection["account_support"]["recorded"]["fulfillment"][0][
                "status"
            ],
            "pending",
        )
        before = self.ledger.events()
        replay = self.portal.transition_owner_fulfillment(
            self.owner_session, **request,
        )
        self.assertEqual(replay, projection)
        self.assertEqual(self.ledger.events(), before)
        event = before[-1]
        self.assertEqual(event.provider, target.provider)
        self.assertEqual(event.subject_key, self.subject(self.user_id))
        self.assertEqual(event.related_event_key, target.source_event_key)
        self.assertRegex(event.source_event_key, r"^key_[0-9a-f]{64}$")
        self.assertRegex(event.actor_key or "", r"^key_[0-9a-f]{64}$")
        self.assertEqual(event.contract_key, request["proof_reference"])
        stored = self.ledger_path.read_text(encoding="utf-8")
        self.assertNotIn("request-1", stored)
        self.assertNotIn("proof-1", stored)
        self.assertNotIn(self.owner_id, stored)
        self.assertNotIn(self.user_id, json.dumps(projection))
        with self.assertRaises(ContributionConflict):
            self.portal.transition_owner_fulfillment(
                self.owner_session,
                **{**request, "status": "fulfilled"},
            )

    def test_owner_fulfillment_revalidates_authority_and_target_account(self):
        self.add_contribution(self.user_id, "admin-fulfillment-auth")
        target = self.ledger.events()[0]
        request = {
            "remote": True,
            "target_account_id": self.user_id,
            "target_event_id": target.event_id,
            "item": "one_time_credit_grant",
            "status": "pending",
            "idempotency_key": opaque_key(
                "fulfillment_request", "request-auth", IDENTITY_KEY,
            ),
            "proof_reference": None,
        }
        with self.assertRaises(SupportAuthorizationError):
            self.portal.transition_owner_fulfillment(
                self.user_session, **request,
            )
        self.clock.advance(91)
        with self.assertRaises(SupportAuthorizationError):
            self.portal.transition_owner_fulfillment(
                self.owner_session, **request,
            )
        self.assertEqual(len(self.ledger.events()), 1)

    def test_owner_fulfillment_rejects_malformed_or_cross_account_target(self):
        self.add_contribution(self.other_id, "other-fulfillment")
        other_target = self.ledger.events()[0]
        base = {
            "remote": True,
            "target_account_id": self.user_id,
            "target_event_id": other_target.event_id,
            "item": "one_time_credit_grant",
            "status": "pending",
            "idempotency_key": opaque_key(
                "fulfillment_request", "request-cross", IDENTITY_KEY,
            ),
            "proof_reference": None,
        }
        with self.assertRaises(ContributionConflict):
            self.portal.transition_owner_fulfillment(
                self.owner_session, **base,
            )
        for replacement in (
            {"item": "Private free text"},
            {"status": "complete"},
            {"idempotency_key": "contains spaces"},
            {"proof_reference": "not-an-opaque-key"},
            {"proof_reference": {"private": "object"}},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(
                SupportPortalError,
            ):
                self.portal.transition_owner_fulfillment(
                    self.owner_session, **{**base, **replacement},
                )
        self.assertEqual(len(self.ledger.events()), 1)

    def test_owner_manual_contribution_is_server_derived_and_idempotent(self):
        request = {
            "remote": True,
            "target_account_id": self.user_id,
            "source": "buy_me_a_coffee",
            "kind": "one_time_contribution",
            "amount_minor": 1_250,
            "currency": "USD",
            "target_event_id": None,
            "idempotency_key": opaque_key(
                "manual_request", "private-request", IDENTITY_KEY,
            ),
        }
        projection = self.portal.record_owner_contribution(
            self.owner_session, **request,
        )
        self.assertEqual(
            projection["account_support"]["recorded"]["currency_totals_minor"],
            {"USD": 1_250},
        )
        before = self.ledger.events()
        replay = self.portal.record_owner_contribution(
            self.owner_session, **request,
        )
        self.assertEqual(replay, projection)
        self.assertEqual(self.ledger.events(), before)
        event = before[0]
        self.assertEqual(event.provider, "manual_buy_me_a_coffee")
        self.assertEqual(event.subject_key, self.subject(self.user_id))
        self.assertRegex(event.source_event_key, r"^key_[0-9a-f]{64}$")
        self.assertRegex(event.actor_key or "", r"^key_[0-9a-f]{64}$")
        self.assertNotEqual(event.source_event_key, request["idempotency_key"])
        self.assertLessEqual(
            abs((datetime.now(timezone.utc) - datetime.fromisoformat(
                event.occurred_at.replace("Z", "+00:00")
            )).total_seconds()),
            5,
        )
        stored = self.ledger_path.read_text(encoding="utf-8")
        self.assertNotIn(request["idempotency_key"], stored)
        self.assertNotIn(self.owner_id, stored)
        self.assertNotIn(self.user_id, stored)
        self.assertEqual(
            projection["account_support"]["benefits"]["state"],
            "development_cost_recovery_locked",
        )

    def test_owner_manual_contribution_revalidates_authority_and_body_semantics(self):
        valid = {
            "remote": True,
            "target_account_id": self.user_id,
            "source": "patreon",
            "kind": "one_time_contribution",
            "amount_minor": 500,
            "currency": "USD",
            "target_event_id": None,
            "idempotency_key": opaque_key(
                "manual_request", "auth", IDENTITY_KEY,
            ),
        }
        with self.assertRaises(SupportAuthorizationError):
            self.portal.record_owner_contribution(self.user_session, **valid)
        for replacement in (
            {"source": "stripe"},
            {"kind": "gift"},
            {"amount_minor": True},
            {"amount_minor": 0},
            {"currency": "usd"},
            {"target_event_id": "evt_private"},
            {"idempotency_key": "private request"},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(
                SupportPortalError,
            ):
                self.portal.record_owner_contribution(
                    self.owner_session, **{**valid, **replacement},
                )
        self.assertEqual(self.ledger.events(), ())
        local = self.portal.record_owner_contribution(
            self.owner_session, **{**valid, "remote": False},
        )
        self.assertEqual(
            local["account_support"]["recorded"]["event_count"], 1,
        )

    def test_direct_compute_action_is_locked_below_and_opens_at_exact_recovery(self):
        portal = self.direct_compute_portal()
        direct = {
            "remote": True,
            "target_account_id": self.user_id,
            "source": "direct_compute_sponsorship",
            "kind": "one_time_contribution",
            "amount_minor": 2_500,
            "currency": "USD",
            "target_event_id": None,
            "idempotency_key": opaque_key(
                "manual_request", "direct-compute", IDENTITY_KEY,
            ),
        }
        self.add_contribution(self.other_id, "recovery-below", amount=99_999)
        before = self.ledger.events()

        with self.assertRaises(SupportAuthorizationError):
            portal.record_owner_contribution(self.owner_session, **direct)
        self.assertEqual(self.ledger.events(), before)

        self.add_contribution(self.other_id, "recovery-exact", amount=1)
        projection = portal.record_owner_contribution(
            self.owner_session, **direct,
        )
        self.assertEqual(
            projection["development_cost_recovery"]["state"], "recovered",
        )
        events = self.ledger.events()
        self.assertEqual(events[-1].provider, "manual_direct_compute_sponsorship")
        self.assertEqual(events[-1].subject_key, self.subject(self.user_id))
        replay = portal.record_owner_contribution(
            self.owner_session, **direct,
        )
        self.assertEqual(replay, projection)
        self.assertEqual(self.ledger.events(), events)

        recovery_source = events[0]
        self.ledger.append(ContributionEventDraft(
            provider=recovery_source.provider,
            source_event_key=opaque_key(
                "event", "recovery-relock", IDENTITY_KEY,
            ),
            subject_key=recovery_source.subject_key,
            kind="refund",
            occurred_at="2026-08-11T11:01:00Z",
            amount_minor=1,
            currency=recovery_source.currency,
            related_event_key=recovery_source.source_event_key,
        ), received_at=NOW)
        relocked_events = self.ledger.events()
        replay_after_relock = portal.record_owner_contribution(
            self.owner_session, **direct,
        )
        self.assertEqual(
            replay_after_relock["development_cost_recovery"]["state"],
            "locked",
        )
        self.assertEqual(self.ledger.events(), relocked_events)

    def test_direct_compute_action_fails_closed_without_mutating_the_ledger(self):
        portal = self.direct_compute_portal()
        request = {
            "remote": True,
            "target_account_id": self.user_id,
            "source": "direct_compute_sponsorship",
            "kind": "one_time_contribution",
            "amount_minor": 2_500,
            "currency": "USD",
            "target_event_id": None,
            "idempotency_key": opaque_key(
                "manual_request", "direct-fail-closed", IDENTITY_KEY,
            ),
        }
        invalid = (
            None,
            {"target_minor": 100_000, "currency": "USD"},
            {
                "target_minor": 100_000,
                "currency": "USD",
                "state": "recovered",
                "extra": True,
            },
            {"target_minor": 1, "currency": "USD", "state": "recovered"},
        )
        for value in invalid:
            with self.subTest(value=value), mock.patch.object(
                entitlements,
                "_development_cost_recovery_projection",
                return_value=value,
            ), self.assertRaises(SupportAuthorizationError):
                portal.record_owner_contribution(self.owner_session, **request)
            self.assertEqual(self.ledger.events(), ())

        with mock.patch.object(
            entitlements,
            "_development_cost_recovery_projection",
            side_effect=OSError("synthetic recovery authority outage"),
        ), self.assertRaises(SupportAuthorizationError):
            portal.record_owner_contribution(self.owner_session, **request)
        self.assertEqual(self.ledger.events(), ())

    def test_ordinary_manual_source_stays_available_and_idempotent_while_locked(self):
        request = {
            "remote": True,
            "target_account_id": self.user_id,
            "source": "patreon",
            "kind": "one_time_contribution",
            "amount_minor": 100_000,
            "currency": "USD",
            "target_event_id": None,
            "idempotency_key": opaque_key(
                "manual_request", "ordinary-recovery", IDENTITY_KEY,
            ),
        }
        with mock.patch.object(
            self.ledger,
            "development_cost_recovery_projection",
            side_effect=OSError("synthetic recovery authority outage"),
        ):
            projection = self.portal.record_owner_contribution(
                self.owner_session, **request,
            )
            self.assertEqual(
                projection["development_cost_recovery"]["state"], "locked",
            )
        events = self.ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].provider, "manual_patreon")

        replay = self.portal.record_owner_contribution(
            self.owner_session, **request,
        )
        self.assertEqual(replay["development_cost_recovery"]["state"], "recovered")
        self.assertEqual(self.ledger.events(), events)

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
