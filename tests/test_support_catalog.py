"""Offline contracts for the public support-provider catalog."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.support_catalog import (  # noqa: E402
    PROVIDER_DEFINITIONS,
    SupportCatalogError,
    load_support_catalog,
    public_support_catalog,
)


class SupportCatalogTests(unittest.TestCase):
    def test_tracked_catalog_has_threadspan_and_disabled_config_slots(self):
        catalog = load_support_catalog(env={}, local_config_path=None)
        self.assertEqual(
            [item.definition.provider_id for item in catalog.providers],
            [
                "threadspan",
                "buy_me_a_coffee",
                "direct_compute_sponsorship",
                "patreon",
            ],
        )
        threadspan = catalog.providers[0]
        self.assertEqual(threadspan.state, "available")
        self.assertTrue(threadspan.enabled)
        self.assertTrue(threadspan.configured)
        self.assertTrue(all(
            not item.enabled for item in catalog.providers[1:]
        ))
        projection = catalog.public_projection()
        self.assertFalse(projection["paid_capacity_enabled"])
        self.assertTrue(all(
            item["support_url"] is None for item in projection["providers"]
        ))
        destinations = projection["providers"][0]["destinations"]
        self.assertEqual(destinations, [
            {"network": "BTC", "destination": "1K628QLEh3sS8sEdzZfvuqqHRecVckSgaJ"},
            {
                "network": "ADA",
                "destination": "addr1q9fd05jktgv49094z8hvjp6cqvn7npt8hfzjna4dvhezmvpgl92x5cevqghl4ng0we2es4xjp59gvm3nttdzwf9ym6lqr3628x",
            },
            {"network": "ETH", "destination": "0x78b6adac22415568A7F725a865206ccFd1a82F4c"},
        ])
        terms = projection["supporter_benefits"]["terms"]
        self.assertEqual(
            projection["supporter_benefits"]["one_time_bonus_cap"], 1_000,
        )
        self.assertTrue(all(
            tier["benefits"] == [
                "supporter_recognition", "bounded_queue_priority",
            ]
            for mode in ("one_time_tiers", "recurring_tiers")
            for tier in projection["supporter_benefits"][mode]
        ))
        default_notice = projection["supporter_benefits"]["notice"]
        self.assertNotIn("early access", default_notice)
        self.assertNotIn("convenience", default_notice)
        self.assertEqual(terms, {
            "cash_value": False,
            "transferable": False,
            "refundable": False,
            "guaranteed_compute": False,
            "guaranteed_service": False,
            "unused_bonus_may_expire_or_be_revoked": True,
        })
        with self.assertRaises(FrozenInstanceError):
            PROVIDER_DEFINITIONS[0].display_name = "Changed"

    def test_environment_enables_only_an_approved_public_destination(self):
        env = {
            "MAESTRO_SUPPORT_BUY_ME_A_COFFEE_ENABLED": "true",
            "MAESTRO_SUPPORT_BUY_ME_A_COFFEE_URL": (
                "https://buymeacoffee.com/example"
            ),
            "MAESTRO_SUPPORT_BUY_ME_A_COFFEE_WEBHOOK_SECRET": "do-not-leak",
            "UNRELATED_SECRET": "also-do-not-leak",
        }
        projection = public_support_catalog(env=env, local_config_path=None)
        coffee = next(
            item for item in projection["providers"]
            if item["provider_id"] == "buy_me_a_coffee"
        )
        self.assertEqual(coffee["state"], "available")
        self.assertTrue(coffee["configured"])
        self.assertEqual(
            coffee["support_url"], "https://buymeacoffee.com/example",
        )
        evidence = coffee["support_evidence"]
        self.assertFalse(evidence["enabled"])
        self.assertEqual(
            evidence["transport"], "native_buy_me_a_coffee_webhook",
        )
        self.assertEqual(evidence["signature_header"], "x-signature-sha256")
        self.assertNotIn("fraud_screening_role", evidence)
        self.assertFalse(evidence["grants_app_or_account_authorization"])
        self.assertFalse(evidence["projects_personal_address_or_phone"])
        serialized = json.dumps(projection, sort_keys=True)
        self.assertNotIn("do-not-leak", serialized)
        self.assertNotIn("also-do-not-leak", serialized)
        self.assertNotIn("person@example", serialized.lower())
        self.assertNotIn("123 main", serialized.lower())

    def test_enabled_without_destination_is_truthfully_unconfigured(self):
        catalog = load_support_catalog(
            env={"MAESTRO_SUPPORT_PATREON_ENABLED": "yes"},
            local_config_path=None,
        )
        patreon = next(
            item for item in catalog.providers
            if item.definition.provider_id == "patreon"
        )
        self.assertTrue(patreon.enabled)
        self.assertFalse(patreon.configured)
        self.assertEqual(patreon.state, "unconfigured")
        self.assertIsNone(patreon.public_projection()["support_url"])

    def test_local_public_config_is_ignored_and_env_wins_per_field(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "support.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "providers": {
                    "buy_me_a_coffee": {
                        "enabled": True,
                        "support_url": "https://buymeacoffee.com/example",
                    },
                },
            }), encoding="utf-8")
            catalog = load_support_catalog(
                env={
                    "MAESTRO_SUPPORT_BUY_ME_A_COFFEE_ENABLED": "false",
                    "MAESTRO_SUPPORT_BUY_ME_A_COFFEE_URL": (
                        "https://www.buymeacoffee.com/replacement"
                    ),
                },
                local_config_path=path,
            )
        status = next(
            item for item in catalog.providers
            if item.definition.provider_id == "buy_me_a_coffee"
        )
        self.assertFalse(status.enabled)
        self.assertTrue(status.configured)
        self.assertEqual(status.state, "disabled")
        self.assertEqual(
            status.support_url,
            "https://www.buymeacoffee.com/replacement",
        )
        self.assertIsNone(status.public_projection()["support_url"])

    def test_empty_environment_url_clears_local_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "support.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "providers": {
                    "patreon": {
                        "enabled": True,
                        "support_url": "https://www.patreon.com/example",
                    },
                },
            }), encoding="utf-8")
            catalog = load_support_catalog(env={
                "MAESTRO_SUPPORT_PATREON_URL": "",
            }, local_config_path=path)
        patreon = next(
            item for item in catalog.providers
            if item.definition.provider_id == "patreon"
        )
        self.assertTrue(patreon.enabled)
        self.assertFalse(patreon.configured)
        self.assertEqual(patreon.state, "unconfigured")

    def test_direct_compute_config_restores_only_an_approved_vast_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "support.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "providers": {
                    "direct_compute_sponsorship": {
                        "enabled": True,
                        "support_url": "https://cloud.vast.ai/",
                    },
                },
            }), encoding="utf-8")
            catalog = load_support_catalog(env={}, local_config_path=path)
            public_projection = public_support_catalog(
                env={}, local_config_path=path,
            )
        status = next(
            item for item in catalog.providers
            if item.definition.provider_id == "direct_compute_sponsorship"
        )
        direct = status.public_projection()
        self.assertEqual(direct["display_name"], "Vast.ai compute sponsorship")
        self.assertIsNone(direct["public_home_url"])
        self.assertEqual(direct["state"], "locked")
        self.assertIsNone(direct["support_url"])
        available = status.public_projection(recovery_confirmed=True)
        self.assertEqual(available["state"], "available")
        self.assertEqual(available["support_url"], "https://cloud.vast.ai/")
        public_direct = next(
            item for item in public_projection["providers"]
            if item["provider_id"] == "direct_compute_sponsorship"
        )
        self.assertEqual(public_direct["state"], "locked")
        self.assertIsNone(public_direct["support_url"])
        self.assertIn("does not process the payment", direct["description"])
        self.assertIn("convert dollars into credits", direct["description"])
        self.assertIn("guarantee compute or service", direct["description"])

    def test_catalog_accepts_exact_configured_provider_hosts(self):
        accepted = {
            "buy_me_a_coffee": (
                "https://buymeacoffee.com/example",
                "https://www.buymeacoffee.com/example",
            ),
            "patreon": (
                "https://patreon.com/example",
                "https://www.patreon.com/example",
            ),
            "direct_compute_sponsorship": (
                "https://vast.ai/",
                "https://cloud.vast.ai/support",
            ),
        }
        for provider_id, values in accepted.items():
            for value in values:
                with self.subTest(provider_id=provider_id, value=value):
                    prefix = f"MAESTRO_SUPPORT_{provider_id.upper()}"
                    catalog = load_support_catalog(env={
                        f"{prefix}_ENABLED": "true",
                        f"{prefix}_URL": value,
                    }, local_config_path=None)
                    status = next(
                        item for item in catalog.providers
                        if item.definition.provider_id == provider_id
                    )
                    self.assertEqual(status.state, "available")
                    self.assertEqual(status.support_url, value)

    def test_catalog_rejects_secret_fields_credentials_and_unsafe_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "support.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "providers": {
                    "patreon": {
                        "enabled": True,
                        "webhook_secret": "tracked-secret",
                    },
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(
                SupportCatalogError, "public settings only",
            ):
                load_support_catalog(env={}, local_config_path=path)
        rejected = {
            "buy_me_a_coffee": (
                "http://buymeacoffee.com/example",
                "https://user:password@buymeacoffee.com/example",
                "https://coffee.buymeacoffee.com/example",
                "https://buymeacoffee.com:444/example",
                "https://buymeacoffee.com/example?private=value",
                "https://buymeacoffee.com/example#private",
                "https://buymeacoffee.com\\@evil.example/example",
                "https://buymeacoffee.com/example\n",
            ),
            "patreon": (
                "https://creator.patreon.com/example",
                "https://patreon.com./example",
            ),
            "direct_compute_sponsorship": (
                "http://vast.ai/",
                "https://account.vast.ai/support",
                "https://vast.ai/support?token=private",
            ),
        }
        for provider_id, values in rejected.items():
            for value in values:
                with (
                    self.subTest(provider_id=provider_id, value=value),
                    self.assertRaises(SupportCatalogError),
                ):
                    prefix = f"MAESTRO_SUPPORT_{provider_id.upper()}"
                    load_support_catalog(env={
                        f"{prefix}_ENABLED": "true",
                        f"{prefix}_URL": value,
                    }, local_config_path=None)

    def test_tier_config_is_strict_portable_and_explicit(self):
        configured = {
            "currency": "USD",
            "credit_unit": "maestro_credits",
            "one_time_bonus_cap": 200,
            "one_time_validity_seconds": 3600,
            "recurring_validity_seconds": 1800,
            "promotional_credits_enabled": True,
            "one_time_tiers": [{
                "tier": "friend",
                "minimum_minor": 700,
                "promotional_maestro_credits": 30,
                "benefits": [
                    "supporter_recognition", "bounded_queue_priority",
                    "early_access_updates",
                ],
            }],
            "recurring_tiers": [{
                "tier": "member",
                "minimum_minor": 500,
                "promotional_maestro_credits": 20,
                "benefits": [
                    "supporter_recognition", "bounded_queue_priority",
                    "supporter_convenience",
                ],
            }],
        }
        projection = public_support_catalog(
            env={"MAESTRO_SUPPORTER_TIERS_JSON": json.dumps(configured)},
            local_config_path=None,
        )["supporter_benefits"]
        self.assertEqual(
            projection["one_time_tiers"][0]["promotional_maestro_credits"],
            30,
        )
        self.assertIn(
            "early_access_updates",
            projection["one_time_tiers"][0]["benefits"],
        )
        self.assertIn(
            "supporter_convenience",
            projection["recurring_tiers"][0]["benefits"],
        )
        self.assertIn("recognition", projection["notice"])
        for malformed in (
            {**configured, "cash_value": True},
            {**configured, "one_time_bonus_cap": -1},
            {**configured, "credit_unit": "compute_seconds"},
            {**configured, "one_time_validity_seconds": 10 ** 20},
            {**configured, "one_time_tiers": []},
            {
                **configured,
                "one_time_tiers": [{
                    **configured["one_time_tiers"][0],
                    "benefits": [[]],
                }],
            },
        ):
            with self.assertRaises(SupportCatalogError):
                load_support_catalog(
                    env={"MAESTRO_SUPPORTER_TIERS_JSON": json.dumps(malformed)},
                    local_config_path=None,
                )


if __name__ == "__main__":
    unittest.main()
