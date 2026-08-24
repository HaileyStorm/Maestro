"""Regressions for Krea's server-wide manual owner-review policy."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.krea_owner_policy import (
    KREA_AUP_URL,
    KREA_LICENSE_DATE,
    KREA_LICENSE_URL,
    KREA_LICENSE_VERSION,
    KREA_OWNER_DECLARATION,
    KREA_POLICY_SCHEMA_VERSION,
    KREA_ROLE_USE_SCOPES,
    KreaOwnerPolicyError,
    krea_owner_policy_status,
    record_krea_owner_policy,
)


class KreaOwnerPolicyTests(unittest.TestCase):
    def _services(self, *, role_use_scopes=None):
        services = {}
        record_krea_owner_policy(
            services,
            owner_attested=True,
            manual_review_accepted=True,
            local_content_stays_local=True,
            attribution_accepted=True,
            role_use_scopes=(
                dict(KREA_ROLE_USE_SCOPES)
                if role_use_scopes is None else role_use_scopes
            ),
            license_version=KREA_LICENSE_VERSION,
            license_date=KREA_LICENSE_DATE,
            declared_at_unix=1_700_000_000,
        )
        return services

    def test_current_license_and_aup_are_explicit(self):
        self.assertEqual(KREA_LICENSE_VERSION, "v1")
        self.assertEqual(KREA_LICENSE_DATE, "2026-06-22")
        self.assertEqual(KREA_LICENSE_URL, "https://www.krea.ai/krea-2-licensing")
        self.assertEqual(KREA_AUP_URL, "https://www.krea.ai/krea-2-use-policy")

    def test_missing_attestation_fails_closed_without_content_filtering(self):
        self.assertEqual(
            krea_owner_policy_status({}),
            {
                "attested": False,
                "availability_status": "owner_attestation_required",
                "migration_required": False,
                "local_execution_allowed": False,
                "hosted_execution_allowed": False,
                "maestro_content_filtering": False,
            },
        )

    def test_manual_owner_review_unlocks_only_the_local_license_gate(self):
        status = krea_owner_policy_status(self._services())
        self.assertEqual(status["availability_status"], "license_conditions_recorded")
        self.assertTrue(status["local_execution_allowed"])
        self.assertFalse(status["hosted_execution_allowed"])
        self.assertTrue(status["manual_owner_review"])
        self.assertFalse(status["maestro_content_filtering"])
        self.assertEqual(status["role_use_scopes"], KREA_ROLE_USE_SCOPES)
        self.assertNotIn("prompt", repr(status).casefold())

    def test_owner_and_user_scope_map_is_exact(self):
        self.assertEqual(KREA_POLICY_SCHEMA_VERSION, 2)
        status = krea_owner_policy_status(self._services())
        self.assertEqual(
            status["role_use_scopes"],
            {"owner": "noncommercial", "user": "commercial_under_1m"},
        )
        for invalid in (
            {"owner": "commercial_under_1m", "user": "commercial_under_1m"},
            {"owner": "noncommercial"},
            {**KREA_ROLE_USE_SCOPES, "admin": "noncommercial"},
            "noncommercial",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(KreaOwnerPolicyError):
                self._services(role_use_scopes=invalid)

    def test_every_acknowledgement_and_current_license_are_required(self):
        base = {
            "owner_attested": True,
            "manual_review_accepted": True,
            "local_content_stays_local": True,
            "attribution_accepted": True,
            "role_use_scopes": dict(KREA_ROLE_USE_SCOPES),
            "license_version": KREA_LICENSE_VERSION,
            "license_date": KREA_LICENSE_DATE,
            "declared_at_unix": 1,
        }
        for key in (
            "owner_attested", "manual_review_accepted",
            "local_content_stays_local", "attribution_accepted",
        ):
            changed = dict(base)
            changed[key] = False
            with self.subTest(key=key), self.assertRaises(KreaOwnerPolicyError):
                record_krea_owner_policy({}, **changed)
        for key in ("license_version", "license_date"):
            changed = dict(base)
            changed[key] = "stale"
            with self.subTest(key=key), self.assertRaises(KreaOwnerPolicyError):
                record_krea_owner_policy({}, **changed)

    def test_tamper_and_schema_drift_fail_closed(self):
        services = self._services()
        self.assertEqual(
            services["krea_owner_policy"]["declaration"],
            KREA_OWNER_DECLARATION,
        )
        for key, value in (
            ("maestro_content_filtering", True),
            ("manual_review_accepted", False),
            ("license_date", "stale"),
        ):
            changed = copy.deepcopy(services)
            changed["krea_owner_policy"][key] = value
            self.assertFalse(krea_owner_policy_status(changed)["attested"])
        extra = copy.deepcopy(services)
        extra["krea_owner_policy"]["prompt_scanner"] = True
        self.assertFalse(krea_owner_policy_status(extra)["attested"])

    def test_schema_v1_is_preserved_as_migration_required_not_execution(self):
        services = {
            "krea_owner_policy": {
                "schema_version": 1,
                "owner_attested": True,
                "manual_review_accepted": True,
                "local_content_stays_local": True,
                "attribution_accepted": True,
                "maestro_content_filtering": False,
                "use_scope": "noncommercial",
                "declaration": KREA_OWNER_DECLARATION,
                "license_version": KREA_LICENSE_VERSION,
                "license_date": KREA_LICENSE_DATE,
                "declared_at_unix": 1,
            },
        }
        status = krea_owner_policy_status(services)
        self.assertFalse(status["attested"])
        self.assertTrue(status["migration_required"])
        self.assertEqual(
            status["availability_status"], "owner_policy_migration_required",
        )
        self.assertFalse(status["local_execution_allowed"])
        self.assertFalse(status["hosted_execution_allowed"])


class KreaOwnerPolicyWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = (APP / "launch.py").read_text(encoding="utf-8")

    def test_owner_routes_are_reauth_bound_and_no_store(self):
        self.assertIn('@api.get("/api/v1/krea/owner-policy")', self.launch)
        self.assertIn('@api.put("/api/v1/krea/owner-policy")', self.launch)
        self.assertGreaterEqual(
            self.launch.count("_require_owner_policy_control(request)"), 4,
        )
        self.assertIn('path == "/api/v1/krea/owner-policy"', self.launch)

    def test_route_records_manual_review_and_never_client_content_rules(self):
        self.assertIn("record_krea_owner_policy(", self.launch)
        self.assertIn('if "use_scope" in body:', self.launch)
        self.assertIn("schema v1 is no longer accepted", self.launch)
        self.assertIn('"attribution_accepted", "role_use_scopes"', self.launch)
        self.assertIn('role_use_scopes=body.get("role_use_scopes")', self.launch)
        self.assertIn('"content_handling": "manual_owner_review"', self.launch)
        self.assertNotIn("prompt_filter", self.launch)
        self.assertNotIn("content_classifier", self.launch)


if __name__ == "__main__":
    unittest.main()
