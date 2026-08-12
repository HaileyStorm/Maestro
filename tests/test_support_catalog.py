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
    def test_tracked_catalog_is_frozen_disabled_and_unconfigured(self):
        catalog = load_support_catalog(env={}, local_config_path=None)
        self.assertEqual(
            [item.definition.provider_id for item in catalog.providers],
            [
                "buy_me_a_coffee",
                "patreon",
                "direct_compute_sponsorship",
            ],
        )
        self.assertEqual(
            {item.state for item in catalog.providers}, {"disabled"},
        )
        self.assertTrue(all(not item.enabled for item in catalog.providers))
        self.assertTrue(all(not item.configured for item in catalog.providers))
        projection = catalog.public_projection()
        self.assertFalse(projection["paid_capacity_enabled"])
        self.assertTrue(all(
            item["support_url"] is None for item in projection["providers"]
        ))
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
        serialized = json.dumps(projection, sort_keys=True)
        self.assertNotIn("do-not-leak", serialized)
        self.assertNotIn("also-do-not-leak", serialized)
        self.assertNotIn("webhook", serialized.lower())

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

    def test_catalog_accepts_exact_provider_hosts_and_public_compute_dns(self):
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
                "https://support.operator.com/maestro",
                "https://continuum.compute.example.org:443/sponsor",
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
                "https://127.0.0.1/maestro",
                "https://[::1]/maestro",
                "https://127.1/maestro",
                "https://localhost/maestro",
                "https://compute/maestro",
                "https://compute.local/maestro",
                "https://compute.localhost/maestro",
                "https://compute.internal/maestro",
                "https://compute.lan/maestro",
                "https://compute.home.arpa/maestro",
                "https://compute.test/maestro",
                "https://compute.example/maestro",
                "https://compute.invalid/maestro",
                "https://compute.onion/maestro",
                "https://-bad.operator.com/maestro",
                "https://operator.123/maestro",
                "https://[bad/maestro",
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

        with self.assertRaises(SupportCatalogError):
            load_support_catalog(env={
                "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_ENABLED": "true",
                "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_URL": (
                    "https://support.operator.com/" + "x" * 2_049
                ),
            }, local_config_path=None)


if __name__ == "__main__":
    unittest.main()
