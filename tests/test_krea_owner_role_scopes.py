"""Actor-aware Krea policy and ordinary-generation wiring regressions."""

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
    KREA_LICENSE_DATE,
    KREA_LICENSE_VERSION,
    KREA_OWNER_DECLARATION,
    KREA_POLICY_SCHEMA_VERSION,
    KREA_ROLE_USE_SCOPES,
    KreaOwnerPolicyError,
    is_registered_krea2_model,
    krea_owner_policy_status,
    record_krea_owner_policy,
    resolve_krea_actor_scope,
)


class KreaActorPolicyTests(unittest.TestCase):
    def _services(self) -> dict[str, object]:
        services: dict[str, object] = {}
        record_krea_owner_policy(
            services,
            schema_version=KREA_POLICY_SCHEMA_VERSION,
            declaration=KREA_OWNER_DECLARATION,
            owner_attested=True,
            manual_review_accepted=True,
            local_content_stays_local=True,
            attribution_accepted=True,
            role_use_scopes=dict(KREA_ROLE_USE_SCOPES),
            license_version=KREA_LICENSE_VERSION,
            license_date=KREA_LICENSE_DATE,
            declared_at_unix=1_700_000_000,
        )
        return services

    def test_current_policy_records_the_exact_role_map_and_four_acknowledgements(self):
        services = self._services()
        record = services["krea_owner_policy"]
        self.assertEqual(KREA_POLICY_SCHEMA_VERSION, 3)
        self.assertEqual(
            record["role_use_scopes"],
            {"owner": "noncommercial", "user": "commercial_under_1m"},
        )
        for key in (
            "owner_attested",
            "manual_review_accepted",
            "local_content_stays_local",
            "attribution_accepted",
        ):
            self.assertIs(record[key], True)
        self.assertIs(record["maestro_content_filtering"], False)

    def test_owner_and_user_resolve_from_server_role_only(self):
        services = self._services()
        self.assertEqual(
            resolve_krea_actor_scope(services, "owner"), "noncommercial",
        )
        self.assertEqual(
            resolve_krea_actor_scope(services, "user"), "commercial_under_1m",
        )

    def test_missing_unknown_and_subclass_roles_are_blocked(self):
        services = self._services()

        class Role(str):
            pass

        for role in (None, "", "admin", Role("owner")):
            with self.subTest(role=role), self.assertRaises(KreaOwnerPolicyError):
                resolve_krea_actor_scope(services, role)

    def test_scope_map_tamper_and_extra_roles_fail_closed(self):
        services = self._services()
        for role_map in (
            {"owner": "commercial_under_1m", "user": "commercial_under_1m"},
            {"owner": "noncommercial", "user": "noncommercial"},
            {**KREA_ROLE_USE_SCOPES, "admin": "noncommercial"},
        ):
            changed = copy.deepcopy(services)
            changed["krea_owner_policy"]["role_use_scopes"] = role_map
            self.assertFalse(krea_owner_policy_status(changed)["attested"])
            with self.assertRaises(KreaOwnerPolicyError):
                resolve_krea_actor_scope(changed, "owner")

    def test_v1_record_is_migration_required_and_never_executable(self):
        services = {
            "krea_owner_policy": {
                "schema_version": 1,
                "owner_attested": True,
                "manual_review_accepted": True,
                "local_content_stays_local": True,
                "attribution_accepted": True,
                "maestro_content_filtering": False,
                "use_scope": "commercial_under_1m",
                "declaration": 'I accept responsibility for manually reviewing Krea 2 use and outputs under the Krea 2 Community License and Acceptable Use Policy.',
                "license_version": KREA_LICENSE_VERSION,
                "license_date": KREA_LICENSE_DATE,
                "declared_at_unix": 1,
            },
        }
        status = krea_owner_policy_status(services)
        self.assertEqual(
            status["availability_status"], "owner_policy_migration_required",
        )
        self.assertIs(status["migration_required"], True)
        self.assertIs(status["local_execution_allowed"], False)
        with self.assertRaisesRegex(KreaOwnerPolicyError, "current manual-review confirmation"):
            resolve_krea_actor_scope(services, "user")

    def test_public_status_is_local_only_and_content_neutral(self):
        status = krea_owner_policy_status(self._services())
        self.assertIs(status["local_execution_allowed"], True)
        self.assertIs(status["hosted_execution_allowed"], False)
        self.assertIs(status["maestro_content_filtering"], False)
        self.assertEqual(status["role_use_scopes"], KREA_ROLE_USE_SCOPES)
        encoded = repr(status).casefold()
        for forbidden in ("prompt", "output", "declaration", "account_id"):
            self.assertNotIn(forbidden, encoded)

    def test_only_registered_exact_krea2_architectures_match(self):
        definitions = {
            "krea2_raw": {"architecture": "krea2_raw"},
            "moody": {"architecture": "krea2_raw"},
            "flux_krea": {"architecture": "flux"},
            "fake-krea": {"architecture": "not_krea2_raw"},
        }
        self.assertTrue(is_registered_krea2_model("krea2_raw", definitions))
        self.assertTrue(is_registered_krea2_model("moody", definitions))
        self.assertFalse(is_registered_krea2_model("flux_krea", definitions))
        self.assertFalse(is_registered_krea2_model("fake-krea", definitions))
        self.assertFalse(is_registered_krea2_model("missing", definitions))
        self.assertFalse(is_registered_krea2_model("krea2_raw", {}))


class KreaActorLaunchWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = (APP / "launch.py").read_text(encoding="utf-8")

    def test_put_route_requires_v2_role_map_and_rejects_v1_scalar(self):
        self.assertIn('if "use_scope" in body:', self.launch)
        self.assertIn("schema v1 is no longer accepted", self.launch)
        self.assertIn('"attribution_accepted", "role_use_scopes"', self.launch)
        self.assertIn('role_use_scopes=body.get("role_use_scopes")', self.launch)

    def test_main_generation_rejects_spoofed_authority_and_resolves_server_role(self):
        self.assertIn("_reject_client_krea_authority(body)", self.launch)
        self.assertIn("_request_krea_principal_role(\n        request", self.launch)
        self.assertIn(
            'principal = getattr(request.state, "maestro_account_principal", None)',
            self.launch,
        )
        self.assertIn(
            'detail="Krea account role and use scope are server-owned"',
            self.launch,
        )

    def test_only_bounded_role_is_stored_and_rechecked_on_retry(self):
        self.assertIn('_KREA_JOB_ROLE_KEY = "_krea_principal_role"', self.launch)
        self.assertIn("{_KREA_JOB_ROLE_KEY: _krea_principal_role}", self.launch)
        self.assertIn("role = job.get(_KREA_JOB_ROLE_KEY)", self.launch)
        self.assertIn("_require_job_krea_actor_admission(job)", self.launch)
        self.assertNotIn('job["krea_use_scope"]', self.launch)
        self.assertNotIn('body["krea_use_scope"]', self.launch)

    def test_non_krea_models_return_before_account_policy_resolution(self):
        start = self.launch.index("def _request_krea_principal_role")
        end = self.launch.index("def _require_job_krea_actor_admission", start)
        helper = self.launch[start:end]
        self.assertLess(
            helper.index("if not _is_registered_krea2_model(model_type):"),
            helper.index("maestro_account_principal"),
        )

    def test_get_projection_is_bounded_and_hosted_execution_remains_false(self):
        self.assertIn('@api.get("/api/v1/krea/owner-policy")', self.launch)
        self.assertIn("status = krea_owner_policy_status(", self.launch)
        self.assertIn('"content_handling": "manual_owner_review"', self.launch)
        self.assertNotIn("prompt_filter", self.launch)
        self.assertNotIn("content_classifier", self.launch)


if __name__ == "__main__":
    unittest.main()
