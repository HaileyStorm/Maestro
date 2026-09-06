from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from services.character_sheet_profile_gate import (
    CharacterSheetProfileGateError,
    PROFILE_GATE_RESOLVER,
    resolve_character_sheet_profile_gate,
)
from services.character_sheet_workflow import PROFILE_DEFINITIONS
from services.host_terms import KREA2_MOODY_MIX_V7_RECIPE_ID
from services.host_terms import CURRENT_HOST_TERM_VERSIONS, accept_host_term
from services.krea_owner_policy import (
    KREA_LICENSE_DATE,
    KREA_LICENSE_VERSION,
    KREA_OWNER_DECLARATION,
    KREA_POLICY_SCHEMA_VERSION,
    KREA_ROLE_USE_SCOPES,
    record_krea_owner_policy,
)
from services.model_terms import model_terms_statuses, required_model_terms


class _DictSubclass(dict):
    pass


class _StrSubclass(str):
    pass


class CharacterSheetProfileGateTests(unittest.TestCase):
    def setUp(self):
        profile_ids = list(PROFILE_DEFINITIONS)
        self.model_defs = {
            "base-model": {
                "character_sheet_profile_bindings": {
                    profile_id: "base_model" for profile_id in profile_ids
                },
            },
            "sheet-lora": {
                "character_sheet_profile_bindings": {
                    profile_id: "lora" for profile_id in profile_ids
                },
            },
        }
        self.components = {
            "base_model": {
                "source": "server_resolved",
                "model_type": "base-model",
                "revision": "base-r1",
                "artifact_ready": True,
                "authorization_ready": True,
            },
            "lora": {
                "source": "server_resolved",
                "model_type": "sheet-lora",
                "revision": "lora-r1",
                "artifact_ready": True,
                "authorization_ready": True,
            },
            "project": {
                "source": "server_resolved",
                "ready": True,
                "revision": "project-r1",
                "evidence_commitment": "1" * 64,
            },
            "vlm": {
                "source": "server_resolved",
                "ready": True,
                "revision": "vlm-r1",
                "evidence_commitment": "2" * 64,
                "local": True,
            },
            "editor": {
                "source": "server_resolved",
                "ready": True,
                "revision": "editor-r1",
                "evidence_commitment": "3" * 64,
                "local": True,
            },
        }

    @staticmethod
    def _terms(_services, model_type, _model_defs):
        return [{
            "term": f"{model_type}-terms",
            "version": 1,
            "accepted": True,
        }]

    def _krea_services(self, declared_at=123):
        services = {}
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
            declared_at_unix=declared_at,
        )
        return services

    def _resolve(self, services=None, *, profile=None, components=None):
        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses",
            side_effect=self._terms,
        ):
            return resolve_character_sheet_profile_gate(
                {} if services is None else services,
                profile=profile,
                components=self.components if components is None else components,
                model_defs=self.model_defs,
            )

    def test_omitted_profile_is_allowed_conservative_flux_default(self):
        decision = self._resolve()
        self.assertEqual(decision["schema_version"], 2)
        self.assertEqual(decision["resolver"], PROFILE_GATE_RESOLVER)
        self.assertEqual(decision["profile"], "quad_flux2_klein")
        self.assertEqual(decision["selection"], "default")
        self.assertFalse(decision["experimental"])
        self.assertEqual(decision["status"], "ready_snapshot")
        self.assertEqual(decision["profile_status"], "requires_server_authorization")
        self.assertFalse(decision["execution_authority"])
        self.assertEqual(decision["reasons"], [])
        self.assertEqual(decision["gates"]["owner"]["status"], "not_applicable")
        self.assertTrue(decision["gates"]["owner"]["ready"])
        self.assertRegex(decision["profile_commitment"], r"^sha256:[0-9a-f]{64}$")

    def test_krea_requires_current_owner_policy(self):
        decision = self._resolve(profile="quad_krea2")
        self.assertEqual(decision["selection"], "explicit")
        self.assertEqual(decision["status"], "blocked")
        self.assertEqual(decision["reasons"], ["krea_owner_attestation_required"])
        self.assertFalse(decision["gates"]["owner"]["ready"])

    def test_exact_v2_role_map_allows_without_selecting_an_actor(self):
        decision = self._resolve(
            self._krea_services(), profile="quad_krea2"
        )
        self.assertEqual(decision["status"], "ready_snapshot")
        self.assertTrue(decision["gates"]["owner"]["attested"])
        self.assertTrue(decision["gates"]["owner"]["role_scopes_valid"])
        encoded = json.dumps(decision, sort_keys=True)
        self.assertNotIn("noncommercial", encoded)
        self.assertNotIn("commercial_under_1m", encoded)
        self.assertNotIn("declared_at", encoded)

    def test_role_map_drift_blocks_and_rotates_private_commitments(self):
        services = self._krea_services()
        admitted = self._resolve(services, profile="quad_krea2")
        changed = copy.deepcopy(services)
        changed["krea_owner_policy"]["role_use_scopes"]["owner"] = (
            "commercial_under_1m"
        )
        blocked = self._resolve(changed, profile="quad_krea2")
        self.assertEqual(blocked["status"], "blocked")
        self.assertFalse(blocked["gates"]["owner"]["role_scopes_valid"])
        self.assertNotEqual(
            admitted["gates"]["owner"]["commitment"],
            blocked["gates"]["owner"]["commitment"],
        )
        self.assertNotEqual(
            admitted["profile_commitment"], blocked["profile_commitment"],
        )

    def test_dynamic_krea_is_explicit_experimental_and_not_default(self):
        decision = self._resolve(
            self._krea_services(), profile="dynamic_krea2_experimental"
        )
        self.assertEqual(decision["profile"], "dynamic_krea2_experimental")
        self.assertEqual(decision["selection"], "explicit")
        self.assertTrue(decision["experimental"])
        self.assertEqual(decision["status"], "ready_snapshot")

    def test_snapshot_profile_fields_match_the_canonical_profile_definitions(self):
        for profile_id, definition in PROFILE_DEFINITIONS.items():
            services = (
                self._krea_services()
                if profile_id in {"quad_krea2", "dynamic_krea2_experimental"}
                else {}
            )
            with self.subTest(profile=profile_id):
                decision = self._resolve(services, profile=profile_id)
                self.assertEqual(decision["profile"], profile_id)
                self.assertEqual(decision["profile_status"], definition["status"])
                self.assertIs(decision["experimental"], definition["experimental"])
                self.assertEqual(decision["selection"], "explicit")
                self.assertFalse(decision["execution_authority"])
                expected = (
                    "later_unavailable"
                    if definition["status"] == "later_unavailable"
                    else "ready_snapshot"
                )
                self.assertEqual(decision["status"], expected)

    def test_triple_is_always_later_unavailable_and_unknown_is_rejected(self):
        blocked = copy.deepcopy(self.components)
        blocked["project"]["ready"] = False
        decision = self._resolve(profile="triple_flux2_klein", components=blocked)
        self.assertEqual(decision["status"], "later_unavailable")
        self.assertEqual(decision["reasons"], ["profile_later_unavailable"])
        with self.assertRaises(CharacterSheetProfileGateError):
            self._resolve(profile="future_profile")

    def test_each_non_owner_gate_fails_independently(self):
        cases = (
            ("base_model", "artifact_ready", False, "base_model_artifact_unavailable"),
            ("base_model", "authorization_ready", False, "base_model_authorization_required"),
            ("lora", "artifact_ready", False, "lora_artifact_unavailable"),
            ("lora", "authorization_ready", False, "lora_authorization_required"),
            ("project", "ready", False, "project_not_ready"),
            ("vlm", "ready", False, "vlm_not_ready"),
            ("vlm", "local", False, "vlm_must_be_local"),
            ("editor", "ready", False, "editor_not_ready"),
            ("editor", "local", False, "editor_must_be_local"),
        )
        for component, field, value, reason in cases:
            with self.subTest(component=component, field=field):
                changed = copy.deepcopy(self.components)
                changed[component][field] = value
                decision = self._resolve(components=changed)
                self.assertEqual(decision["status"], "blocked")
                self.assertEqual(decision["reasons"], [reason])

    def test_base_and_lora_terms_fail_independently_from_owner_policy(self):
        def terms(_services, model_type, _model_defs):
            return [{
                "term": f"{model_type}-terms",
                "version": 1,
                "accepted": model_type != "base-model",
            }]

        services = self._krea_services()
        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses",
            side_effect=terms,
        ):
            decision = resolve_character_sheet_profile_gate(
                services,
                profile="quad_krea2",
                components=self.components,
                model_defs=self.model_defs,
            )
        self.assertEqual(decision["status"], "blocked")
        self.assertEqual(decision["reasons"], ["base_model_terms_required"])
        self.assertTrue(decision["gates"]["owner"]["ready"])
        self.assertFalse(decision["gates"]["base_model"]["terms_ready"])
        self.assertTrue(decision["gates"]["lora"]["terms_ready"])

        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses",
            side_effect=self._terms,
        ):
            missing_owner = resolve_character_sheet_profile_gate(
                {},
                profile="quad_krea2",
                components=self.components,
                model_defs=self.model_defs,
            )
        self.assertTrue(missing_owner["gates"]["base_model"]["terms_ready"])
        self.assertEqual(
            missing_owner["reasons"], ["krea_owner_attestation_required"]
        )

    def test_lora_terms_failure_is_reported_separately(self):
        def terms(_services, model_type, _model_defs):
            return [{"term": "t", "version": 1, "accepted": model_type != "sheet-lora"}]

        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses",
            side_effect=terms,
        ):
            decision = resolve_character_sheet_profile_gate(
                {}, components=self.components, model_defs=self.model_defs
            )
        self.assertEqual(decision["reasons"], ["lora_terms_required"])

    def test_commitments_are_deterministic_and_rotate_on_private_drift(self):
        services = self._krea_services(declared_at=123)
        first = self._resolve(services, profile="quad_krea2")
        again = self._resolve(copy.deepcopy(services), profile="quad_krea2")
        self.assertEqual(first, again)

        changed = copy.deepcopy(self.components)
        changed["base_model"]["revision"] = "base-r2"
        model_drift = self._resolve(services, profile="quad_krea2", components=changed)
        self.assertNotEqual(
            first["gates"]["base_model"]["artifact_commitment"],
            model_drift["gates"]["base_model"]["artifact_commitment"],
        )
        self.assertNotEqual(first["profile_commitment"], model_drift["profile_commitment"])

        evidence_drift = copy.deepcopy(self.components)
        evidence_drift["project"]["evidence_commitment"] = "4" * 64
        project_drift = self._resolve(
            services, profile="quad_krea2", components=evidence_drift
        )
        self.assertNotEqual(
            first["gates"]["project"]["commitment"],
            project_drift["gates"]["project"]["commitment"],
        )

        policy_drift = self._resolve(
            self._krea_services(declared_at=124), profile="quad_krea2"
        )
        self.assertNotEqual(
            first["gates"]["owner"]["commitment"],
            policy_drift["gates"]["owner"]["commitment"],
        )
        self.assertNotEqual(first["profile_commitment"], policy_drift["profile_commitment"])

    def test_term_status_drift_rotates_only_terms_and_aggregate_commitments(self):
        def terms_version(version):
            return lambda _services, model_type, _defs: [{
                "term": f"{model_type}-terms", "version": version, "accepted": True,
            }]

        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses",
            side_effect=terms_version(1),
        ):
            first = resolve_character_sheet_profile_gate(
                {}, components=self.components, model_defs=self.model_defs
            )
        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses",
            side_effect=terms_version(2),
        ):
            second = resolve_character_sheet_profile_gate(
                {}, components=self.components, model_defs=self.model_defs
            )
        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses",
            side_effect=terms_version("1"),
        ):
            string_version = resolve_character_sheet_profile_gate(
                {}, components=self.components, model_defs=self.model_defs
            )
        self.assertNotEqual(
            first["gates"]["base_model"]["terms_commitment"],
            second["gates"]["base_model"]["terms_commitment"],
        )
        self.assertEqual(
            first["gates"]["base_model"]["artifact_commitment"],
            second["gates"]["base_model"]["artifact_commitment"],
        )
        self.assertNotEqual(first["profile_commitment"], second["profile_commitment"])
        self.assertNotEqual(
            first["gates"]["base_model"]["terms_commitment"],
            string_version["gates"]["base_model"]["terms_commitment"],
        )

    def test_models_must_be_registered_and_bound_to_the_selected_profile_role(self):
        unknown = copy.deepcopy(self.components)
        unknown["base_model"]["model_type"] = "unregistered-model"
        with self.assertRaisesRegex(
            CharacterSheetProfileGateError, "not a registered server model"
        ):
            self._resolve(components=unknown)

        cross_profile_defs = copy.deepcopy(self.model_defs)
        cross_profile_defs["base-model"]["character_sheet_profile_bindings"] = {
            "quad_krea2": "base_model",
        }
        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses",
            side_effect=self._terms,
        ):
            with self.assertRaisesRegex(
                CharacterSheetProfileGateError, "not bound to the selected"
            ):
                resolve_character_sheet_profile_gate(
                    {}, components=self.components, model_defs=cross_profile_defs
                )

        wrong_role_defs = copy.deepcopy(self.model_defs)
        wrong_role_defs["base-model"]["character_sheet_profile_bindings"][
            "quad_flux2_klein"
        ] = "lora"
        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses",
            side_effect=self._terms,
        ):
            with self.assertRaisesRegex(
                CharacterSheetProfileGateError, "not bound to the selected"
            ):
                resolve_character_sheet_profile_gate(
                    {}, components=self.components, model_defs=wrong_role_defs
                )

    def test_registered_krea_recipe_requires_its_exact_creator_manifest(self):
        components = copy.deepcopy(self.components)
        components["base_model"]["model_type"] = KREA2_MOODY_MIX_V7_RECIPE_ID
        model_defs = copy.deepcopy(self.model_defs)
        model_defs[KREA2_MOODY_MIX_V7_RECIPE_ID] = {
            "character_sheet_profile_bindings": {
                "quad_krea2": "base_model",
            },
        }
        with patch(
            "services.character_sheet_profile_gate.model_terms_statuses"
        ) as statuses:
            with self.assertRaisesRegex(
                CharacterSheetProfileGateError, "creator terms manifest is invalid"
            ):
                resolve_character_sheet_profile_gate(
                    self._krea_services(),
                    profile="quad_krea2",
                    components=components,
                    model_defs=model_defs,
                )
        statuses.assert_not_called()

    def test_registered_krea_recipe_uses_real_integer_term_versions(self):
        root = Path(__file__).resolve().parents[1]
        recipe_path = (
            root / "app/defaults" / f"{KREA2_MOODY_MIX_V7_RECIPE_ID}.json"
        )
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))["model"]
        recipe["character_sheet_profile_bindings"] = {
            "quad_krea2": "base_model",
        }
        model_defs = {
            KREA2_MOODY_MIX_V7_RECIPE_ID: recipe,
            "sheet-lora": {
                "character_sheet_profile_bindings": {
                    "quad_krea2": "lora",
                },
            },
        }
        services = self._krea_services()
        for term in required_model_terms(
            KREA2_MOODY_MIX_V7_RECIPE_ID, model_defs
        ):
            accept_host_term(
                services,
                term,
                CURRENT_HOST_TERM_VERSIONS[term],
                accepted_at="2026-08-23T00:00:00Z",
            )
        statuses = model_terms_statuses(
            services, KREA2_MOODY_MIX_V7_RECIPE_ID, model_defs
        )
        self.assertTrue(statuses)
        self.assertTrue(all(type(item["version"]) is int for item in statuses))
        self.assertTrue(all(item["accepted"] is True for item in statuses))

        components = copy.deepcopy(self.components)
        components["base_model"]["model_type"] = KREA2_MOODY_MIX_V7_RECIPE_ID
        decision = resolve_character_sheet_profile_gate(
            services,
            profile="quad_krea2",
            components=components,
            model_defs=model_defs,
        )
        self.assertEqual(decision["status"], "ready_snapshot")
        self.assertFalse(decision["execution_authority"])
        self.assertTrue(decision["gates"]["base_model"]["terms_ready"])

    def test_term_version_rejects_bool_and_non_scalar_values(self):
        for invalid in (True, False, [], {}, 1.5, None):
            with self.subTest(version=repr(invalid)):
                with patch(
                    "services.character_sheet_profile_gate.model_terms_statuses",
                    return_value=[{
                        "term": "synthetic-terms",
                        "version": invalid,
                        "accepted": True,
                    }],
                ):
                    with self.assertRaisesRegex(
                        CharacterSheetProfileGateError, "terms status is invalid"
                    ):
                        resolve_character_sheet_profile_gate(
                            {},
                            components=self.components,
                            model_defs=self.model_defs,
                        )

    def test_every_readiness_descriptor_rejects_client_authored_source(self):
        for component in self.components:
            with self.subTest(component=component):
                changed = copy.deepcopy(self.components)
                changed[component]["source"] = "client"
                with self.assertRaisesRegex(
                    CharacterSheetProfileGateError, "current server resolver"
                ):
                    self._resolve(components=changed)

    def test_closed_schemas_exact_types_and_subclasses_are_rejected(self):
        invalid_cases = []
        extra_root = copy.deepcopy(self.components)
        extra_root["client_attestation"] = True
        invalid_cases.append(({}, None, extra_root, self.model_defs))
        missing_root = copy.deepcopy(self.components)
        del missing_root["editor"]
        invalid_cases.append(({}, None, missing_root, self.model_defs))
        invalid_cases.append(({}, None, _DictSubclass(self.components), self.model_defs))
        invalid_cases.append((_DictSubclass(), None, self.components, self.model_defs))
        invalid_cases.append(({}, None, self.components, _DictSubclass(self.model_defs)))
        invalid_cases.append(({}, _StrSubclass("quad_flux2_klein"), self.components, self.model_defs))
        for services, profile, components, model_defs in invalid_cases:
            with self.subTest(profile=profile, component_type=type(components).__name__):
                with self.assertRaises(CharacterSheetProfileGateError):
                    resolve_character_sheet_profile_gate(
                        services,
                        profile=profile,
                        components=components,
                        model_defs=model_defs,
                    )

        for component, field, invalid in (
            ("base_model", "artifact_ready", 1),
            ("base_model", "model_type", _StrSubclass("base-model")),
            ("lora", "revision", "has spaces"),
            ("project", "evidence_commitment", _StrSubclass("1" * 64)),
            ("vlm", "local", 1),
        ):
            changed = copy.deepcopy(self.components)
            changed[component][field] = invalid
            with self.subTest(component=component, field=field):
                with self.assertRaises(CharacterSheetProfileGateError):
                    resolve_character_sheet_profile_gate(
                        {}, components=changed, model_defs=self.model_defs
                    )

        changed = copy.deepcopy(self.components)
        changed["project"]["extra"] = True
        with self.assertRaises(CharacterSheetProfileGateError):
            resolve_character_sheet_profile_gate(
                {}, components=changed, model_defs=self.model_defs
            )

        subclass_binding_defs = copy.deepcopy(self.model_defs)
        subclass_binding_defs["base-model"]["character_sheet_profile_bindings"] = (
            _DictSubclass(
                subclass_binding_defs["base-model"]["character_sheet_profile_bindings"]
            )
        )
        with self.assertRaises(CharacterSheetProfileGateError):
            resolve_character_sheet_profile_gate(
                {}, components=self.components, model_defs=subclass_binding_defs
            )

    def test_decision_is_redacted_and_does_not_scan_or_bind_creative_content(self):
        services = self._krea_services()
        services["private_prompt"] = "SECRET violent adult controversial creative text"
        first = self._resolve(services, profile="quad_krea2")
        services["private_prompt"] = "completely different subject"
        second = self._resolve(services, profile="quad_krea2")
        self.assertEqual(first, second)
        encoded = json.dumps(first, sort_keys=True)
        for secret in (
            "SECRET", "violent", "adult", "controversial", "base-model",
            "base-r1", "project-r1", "noncommercial",
            "commercial_under_1m", "123",
        ):
            self.assertNotIn(secret, encoded)
        self.assertEqual(
            set(first),
            {
                "schema_version", "resolver", "profile", "selection",
                "profile_status", "experimental", "status",
                "execution_authority", "reasons", "gates", "profile_commitment",
            },
        )

    def test_import_reads_but_does_not_modify_public_v1_workflow_contracts(self):
        root = Path(__file__).resolve().parents[1]
        paths = (
            root / "app/services/character_sheet_workflow.py",
            root / "app/services/character_sheet_capabilities.py",
        )
        before = [path.read_bytes() for path in paths]
        __import__("services.character_sheet_profile_gate")
        self.assertEqual(before, [path.read_bytes() for path in paths])
        source = (root / "app/services/character_sheet_profile_gate.py").read_text()
        self.assertIn("from services.character_sheet_workflow import PROFILE_DEFINITIONS", source)
        self.assertNotIn("character_sheet_capabilities", source)


if __name__ == "__main__":
    unittest.main()
