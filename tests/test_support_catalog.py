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
        self.assertTrue(catalog.providers)
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
            "MAESTRO_SUPPORT_GITHUB_SPONSORS_ENABLED": "true",
            "MAESTRO_SUPPORT_GITHUB_SPONSORS_URL": (
                "https://github.com/sponsors/example"
            ),
            "MAESTRO_SUPPORT_GITHUB_SPONSORS_WEBHOOK_SECRET": "do-not-leak",
            "UNRELATED_SECRET": "also-do-not-leak",
        }
        projection = public_support_catalog(env=env, local_config_path=None)
        github = next(
            item for item in projection["providers"]
            if item["provider_id"] == "github_sponsors"
        )
        self.assertEqual(github["state"], "available")
        self.assertTrue(github["configured"])
        self.assertEqual(
            github["support_url"], "https://github.com/sponsors/example",
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

    def test_local_public_config_is_ignored_path_compatible_and_env_wins(self):
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
                env={"MAESTRO_SUPPORT_BUY_ME_A_COFFEE_ENABLED": "false"},
                local_config_path=path,
            )
        status = next(
            item for item in catalog.providers
            if item.definition.provider_id == "buy_me_a_coffee"
        )
        self.assertFalse(status.enabled)
        self.assertTrue(status.configured)
        self.assertEqual(status.state, "disabled")
        self.assertIsNone(status.public_projection()["support_url"])

    def test_catalog_rejects_secret_fields_credentials_and_wrong_hosts(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "support.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "providers": {
                    "stripe": {
                        "enabled": True,
                        "webhook_secret": "tracked-secret",
                    },
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(
                SupportCatalogError, "public settings only",
            ):
                load_support_catalog(env={}, local_config_path=path)
        rejected = (
            "http://github.com/sponsors/example",
            "https://user:password@github.com/sponsors/example",
            "https://evil.example/sponsors/example",
            "https://github.com:444/sponsors/example",
        )
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(
                SupportCatalogError,
            ):
                load_support_catalog(env={
                    "MAESTRO_SUPPORT_GITHUB_SPONSORS_ENABLED": "true",
                    "MAESTRO_SUPPORT_GITHUB_SPONSORS_URL": value,
                }, local_config_path=None)


if __name__ == "__main__":
    unittest.main()
