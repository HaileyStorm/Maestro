"""Model-free regressions for the sealed Character Sheet workflow contract."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _ROOT / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from services.character_sheet_workflow import (  # noqa: E402
    CONTRACT_SCHEMA_VERSION,
    DEFAULT_PROFILE_ID,
    QWEN_IMAGE_EDIT_OPERATION,
    CharacterSheetWorkflowError,
    apply_failed_panel_repairs,
    assert_character_sheet_execution_authorized,
    assert_character_sheet_replay,
    build_character_sheet_plan,
    build_character_sheet_execution_authorization,
    canonical_character_sheet_plan,
    character_sheet_profile_catalog,
    normalize_character_sheet_profile,
    public_character_sheet_plan,
    validate_character_sheet_plan,
)
from services import character_sheet_workflow as _workflow  # noqa: E402


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class CharacterSheetWorkflowTests(unittest.TestCase):
    maxDiff = None

    def _anchor(self, *, kind: str = "generated", label: str = "anchor"):
        return {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "project_id": "project-private-001",
            "anchor_id": "anchor-private-001",
            "kind": kind,
            "sha256": _sha(label),
        }

    def _resource_base(self, profile_id: str = DEFAULT_PROFILE_ID):
        return {
            "profile_id": profile_id,
            "base_model": {
                "id": "server-base-private",
                "revision": "base-revision-private",
            },
            "lora": {
                "id": "server-lora-private",
                "revision": "lora-revision-private",
            },
            "schedule": {
                "id": "server-schedule-private",
                "revision": "schedule-revision-private",
            },
            "terms": {
                "id": "server-terms-private",
                "revision": "terms-revision-private",
                "acceptance_digest": _sha("private-terms-acceptance"),
            },
            "planner": {
                "id": "local-planner-private",
                "revision": "planner-revision-private",
                "local": True,
            },
            "reviewer": {
                "id": "local-reviewer-private",
                "revision": "reviewer-revision-private",
                "local": True,
            },
            "editor": {
                "id": "local-qwen-editor-private",
                "revision": "editor-revision-private",
                "local": True,
                "operation": QWEN_IMAGE_EDIT_OPERATION,
            },
        }

    def _authorize(
        self,
        resource_base,
        *,
        anchor=None,
        project_id: str = "project-private-001",
        issued_at_unix: int = 1_000,
        expires_at_unix: int = 1_600,
        seed: int = 987654321,
    ):
        clean = copy.deepcopy(resource_base)
        clean.pop("execution_authorization", None)
        clean["execution_authorization"] = (
            build_character_sheet_execution_authorization(
                project_id=project_id,
                profile=clean["profile_id"],
                anchor=anchor or self._anchor(),
                resource_base=clean,
                revision="availability-revision-private",
                evidence_digest=_sha("private-availability-evidence"),
                nonce="authorization-nonce-private",
                issued_at_unix=issued_at_unix,
                expires_at_unix=expires_at_unix,
                seed=seed,
            )
        )
        return clean

    def _resources(
        self,
        profile_id: str = DEFAULT_PROFILE_ID,
        *,
        anchor=None,
        seed: int = 987654321,
    ):
        return self._authorize(
            self._resource_base(profile_id), anchor=anchor, seed=seed,
        )

    def _panels(self):
        roles = ("identity_front", "three_quarter", "profile", "back")
        coordinates = (
            (0, 0, 768, 512),
            (768, 0, 768, 512),
            (0, 512, 768, 512),
            (768, 512, 768, 512),
        )
        return [
            {
                "role": role,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "sha256": _sha(f"panel-{role}"),
            }
            for role, (x, y, width, height) in zip(
                roles, coordinates, strict=True,
            )
        ]

    def _plan(self, **overrides):
        chosen_anchor = overrides.get("anchor", self._anchor())
        chosen_seed = overrides.get("seed", 987654321)
        values = {
            "project_id": "project-private-001",
            "anchor": chosen_anchor,
            "resources": self._resources(
                anchor=chosen_anchor, seed=chosen_seed,
            ),
            "panels": self._panels(),
            "seed": chosen_seed,
            "authorization_checked_at_unix": 1_100,
        }
        values.update(overrides)
        return build_character_sheet_plan(**values)

    def test_catalog_has_exact_choices_and_no_invented_executable_profile(self):
        catalog = character_sheet_profile_catalog()
        self.assertEqual(
            [item["id"] for item in catalog],
            [
                "quad_flux2_klein",
                "quad_krea2",
                "dynamic_krea2_experimental",
                "triple_flux2_klein",
            ],
        )
        self.assertEqual(
            [item["id"] for item in catalog if item["default"]],
            [DEFAULT_PROFILE_ID],
        )
        self.assertFalse(any(item["available"] for item in catalog))
        self.assertFalse(any(item["executable"] for item in catalog))
        by_id = {item["id"]: item for item in catalog}
        self.assertEqual(by_id["quad_krea2"]["status"], "legal_blocked")
        self.assertEqual(
            by_id["dynamic_krea2_experimental"]["status"], "legal_blocked",
        )
        self.assertTrue(by_id["dynamic_krea2_experimental"]["experimental"])
        self.assertEqual(
            by_id["triple_flux2_klein"]["status"], "later_unavailable",
        )

    def test_default_requires_current_server_availability_evidence(self):
        with self.assertRaises(CharacterSheetWorkflowError):
            normalize_character_sheet_profile()
        self.assertEqual(
            normalize_character_sheet_profile(
                None, available_profile_ids=(DEFAULT_PROFILE_ID,),
            ),
            DEFAULT_PROFILE_ID,
        )
        plan = self._plan()
        self.assertEqual(plan["profile"], DEFAULT_PROFILE_ID)

        unavailable = self._resources()
        unavailable["execution_authorization"]["status"] = "blocked"
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(resources=unavailable)

    def test_authorization_is_short_lived_and_bound_to_exact_private_inputs(self):
        authorization = self._resources()
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(
                resources=authorization,
                authorization_checked_at_unix=1_600,
            )

        changed_anchor = self._anchor(label="cross-anchor")
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(anchor=changed_anchor, resources=authorization)

        changed_resource = copy.deepcopy(authorization)
        changed_resource["base_model"]["revision"] = "cross-resource-revision"
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(resources=changed_resource)

        other_anchor = self._anchor()
        other_anchor["project_id"] = "other-project"
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(
                project_id="other-project",
                anchor=other_anchor,
                resources=authorization,
            )

        with self.assertRaises(CharacterSheetWorkflowError):
            build_character_sheet_execution_authorization(
                project_id="project-private-001",
                profile=DEFAULT_PROFILE_ID,
                anchor=self._anchor(),
                resource_base=self._resource_base(),
                revision="availability-revision-private",
                evidence_digest=_sha("private-availability-evidence"),
                nonce="authorization-nonce-private",
                issued_at_unix=1_000,
                expires_at_unix=2_000,
                seed=987654321,
            )

        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(seed=987654322, resources=authorization)

    def test_execution_rechecks_trusted_seal_evidence_and_freshness(self):
        plan = self._plan()
        evidence = plan["resources"]["execution_authorization"]["evidence_digest"]
        self.assertEqual(
            assert_character_sheet_execution_authorized(
                plan,
                now_unix=1_200,
                expected_plan_seal=plan["plan_seal"],
                expected_authorization_evidence_digest=evidence,
            ),
            plan,
        )
        for kwargs in (
            {"now_unix": 1_600, "expected_plan_seal": plan["plan_seal"],
             "expected_authorization_evidence_digest": evidence},
            {"now_unix": 1_200, "expected_plan_seal": _sha("wrong-plan"),
             "expected_authorization_evidence_digest": evidence},
            {"now_unix": 1_200, "expected_plan_seal": plan["plan_seal"],
             "expected_authorization_evidence_digest": _sha("wrong-evidence")},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(CharacterSheetWorkflowError):
                    assert_character_sheet_execution_authorized(plan, **kwargs)

    def test_krea_profiles_are_representable_but_cannot_execute(self):
        for profile_id in ("quad_krea2", "dynamic_krea2_experimental"):
            resources = self._resources()
            resources["profile_id"] = profile_id
            with self.subTest(profile=profile_id):
                with self.assertRaises(CharacterSheetWorkflowError):
                    self._plan(profile=profile_id, resources=resources)
        triple = self._resources()
        triple["profile_id"] = "triple_flux2_klein"
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(
                profile="triple_flux2_klein",
                resources=triple,
            )

    def test_dynamic_is_never_selected_implicitly(self):
        resources = self._resources()
        resources["profile_id"] = "dynamic_krea2_experimental"
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(resources=resources)
        self.assertEqual(
            [item["id"] for item in character_sheet_profile_catalog() if item["default"]],
            ["quad_flux2_klein"],
        )

    def test_unknown_mismatch_and_boolean_inputs_fail_closed(self):
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(profile="future_profile")
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(profile=True)
        with self.assertRaises(CharacterSheetWorkflowError):
            normalize_character_sheet_profile(
                None,
                available_profile_ids=(DEFAULT_PROFILE_ID,),
                require_available=1,
            )
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(seed=True)

        mismatch = self._resources()
        mismatch["profile_id"] = "quad_krea2"
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(resources=mismatch)

        bad_locality = self._resources()
        bad_locality["reviewer"]["local"] = 1
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(resources=bad_locality)

        bad_coordinate = self._panels()
        bad_coordinate[0]["x"] = False
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(panels=bad_coordinate)

        bad_anchor_version = self._anchor()
        bad_anchor_version["schema_version"] = True
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(anchor=bad_anchor_version)

        bad_plan_version = self._plan()
        bad_plan_version["schema_version"] = True
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(bad_plan_version)

        class EqualityGadget:
            def __eq__(self, _other):
                return True

        gadget_plan = self._plan()
        gadget_plan["planner_version"] = EqualityGadget()
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(gadget_plan)
        gadget_editor = self._resources()
        gadget_editor["editor"]["operation"] = EqualityGadget()
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(resources=gadget_editor)
        gadget_authorization = self._resources()
        gadget_authorization["execution_authorization"]["status"] = EqualityGadget()
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(resources=gadget_authorization)
        gadget_commitment = self._plan()
        gadget_commitment["commitments"]["anchor"] = EqualityGadget()
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(gadget_commitment)
        gadget_provenance = self._plan()
        gadget_provenance["provenance"]["service"] = EqualityGadget()
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(gadget_provenance)

    def test_anchor_is_project_scoped_and_kind_is_authored(self):
        imported = self._plan(anchor=self._anchor(kind="imported"))
        self.assertEqual(imported["anchor"]["kind"], "imported")
        mismatch = self._anchor()
        mismatch["project_id"] = "another-project"
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(anchor=mismatch)
        bad_kind = self._anchor()
        bad_kind["kind"] = "inferred"
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(anchor=bad_kind)

    def test_changed_anchor_changes_seal_and_expected_anchor_rejects_replay(self):
        first = self._plan()
        changed_anchor = self._anchor(label="changed-anchor")
        second = self._plan(anchor=changed_anchor)
        self.assertNotEqual(first["plan_seal"], second["plan_seal"])
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(second, expected_anchor=self._anchor())

        tampered = copy.deepcopy(first)
        tampered["anchor"]["sha256"] = changed_anchor["sha256"]
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(tampered)

    def test_panel_order_cardinality_and_digest_are_exact(self):
        plan = self._plan()
        self.assertEqual(
            [panel["role"] for panel in plan["panels"]],
            ["identity_front", "three_quarter", "profile", "back"],
        )
        for candidate in (
            list(reversed(self._panels())),
            self._panels()[:-1],
            [*self._panels(), self._panels()[0]],
        ):
            with self.subTest(count=len(candidate)):
                with self.assertRaises(CharacterSheetWorkflowError):
                    self._plan(panels=candidate)
        invalid_digest = self._panels()
        invalid_digest[0]["sha256"] = "A" * 64
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(panels=invalid_digest)

    def test_failed_panel_only_repair_preserves_accepted_bytes_and_anchor(self):
        plan = self._plan()
        replacement = copy.deepcopy(plan["panels"][2])
        replacement["sha256"] = _sha("repaired-profile")
        before_accepted = {
            panel["role"]: json.dumps(panel, sort_keys=True, separators=(",", ":"))
            for panel in plan["panels"]
            if panel["role"] != "profile"
        }
        repaired = apply_failed_panel_repairs(
            plan, failed_roles=["profile"], repaired_panels=[replacement],
        )
        self.assertEqual(repaired["anchor"], plan["anchor"])
        self.assertEqual(repaired["resources"], plan["resources"])
        self.assertEqual(repaired["seed"], plan["seed"])
        self.assertEqual(repaired["parent_plan_seal"], plan["plan_seal"])
        self.assertEqual(repaired["repair_lineage"][0]["failed_roles"], ["profile"])
        self.assertEqual(
            repaired["repair_lineage"][0]["operation"],
            QWEN_IMAGE_EDIT_OPERATION,
        )
        after_accepted = {
            panel["role"]: json.dumps(panel, sort_keys=True, separators=(",", ":"))
            for panel in repaired["panels"]
            if panel["role"] != "profile"
        }
        self.assertEqual(after_accepted, before_accepted)
        self.assertEqual(repaired["panels"][2]["sha256"], replacement["sha256"])
        self.assertNotEqual(repaired["plan_seal"], plan["plan_seal"])

    def test_repair_rejects_extra_unchanged_moved_or_unordered_panels(self):
        plan = self._plan()
        replacement = copy.deepcopy(plan["panels"][2])
        replacement["sha256"] = _sha("replacement")
        with self.assertRaises(CharacterSheetWorkflowError):
            apply_failed_panel_repairs(
                plan,
                failed_roles=["profile"],
                repaired_panels=[replacement, copy.deepcopy(plan["panels"][3])],
            )
        unchanged = copy.deepcopy(plan["panels"][2])
        with self.assertRaises(CharacterSheetWorkflowError):
            apply_failed_panel_repairs(
                plan, failed_roles=["profile"], repaired_panels=[unchanged],
            )
        moved = copy.deepcopy(replacement)
        moved["x"] += 1
        with self.assertRaises(CharacterSheetWorkflowError):
            apply_failed_panel_repairs(
                plan, failed_roles=["profile"], repaired_panels=[moved],
            )
        replacement_back = copy.deepcopy(plan["panels"][3])
        replacement_back["sha256"] = _sha("replacement-back")
        with self.assertRaises(CharacterSheetWorkflowError):
            apply_failed_panel_repairs(
                plan,
                failed_roles=["back", "profile"],
                repaired_panels=[replacement_back, replacement],
            )

    def test_multiple_repair_attempts_form_one_valid_chain(self):
        plan = self._plan()
        profile = copy.deepcopy(plan["panels"][2])
        profile["sha256"] = _sha("profile-repair-one")
        first = apply_failed_panel_repairs(
            plan, failed_roles=["profile"], repaired_panels=[profile],
        )
        back = copy.deepcopy(first["panels"][3])
        back["sha256"] = _sha("back-repair-two")
        second = apply_failed_panel_repairs(
            first, failed_roles=["back"], repaired_panels=[back],
        )
        self.assertEqual(len(second["repair_lineage"]), 2)
        self.assertEqual(second["repair_lineage"][1]["attempt"], 2)
        self.assertEqual(
            second["repair_lineage"][1]["before_panel_commitments"],
            second["repair_lineage"][0]["after_panel_commitments"],
        )
        self.assertEqual(validate_character_sheet_plan(second), second)

        wrong_parent = copy.deepcopy(second)
        wrong_parent["parent_plan_seal"] = _sha("wrong-parent")
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(wrong_parent)

        other_base = self._plan(seed=123)
        other_profile = copy.deepcopy(other_base["panels"][2])
        other_profile["sha256"] = profile["sha256"]
        other_first = apply_failed_panel_repairs(
            other_base,
            failed_roles=["profile"],
            repaired_panels=[other_profile],
        )
        spliced = copy.deepcopy(first)
        spliced["repair_lineage"][0] = copy.deepcopy(
            other_first["repair_lineage"][0]
        )
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(spliced)

    def test_persisted_repair_lineage_cannot_move_a_failed_panel(self):
        plan = self._plan()
        replacement = copy.deepcopy(plan["panels"][2])
        replacement["sha256"] = _sha("moved-persisted-replacement")
        repaired = apply_failed_panel_repairs(
            plan, failed_roles=["profile"], repaired_panels=[replacement],
        )
        candidate = copy.deepcopy(repaired)
        event = candidate["repair_lineage"][0]
        event["after_panels"][2]["x"] += 1
        candidate["panels"][2]["x"] += 1
        event["after_panel_commitments"] = _workflow._panel_commitments(
            event["after_panels"],
        )
        unsigned_event = {
            key: value for key, value in event.items() if key != "event_seal"
        }
        event["event_seal"] = _workflow._seal(
            "character-sheet-repair-event-v1", unsigned_event,
        )
        unsigned = _workflow._unsigned_plan(
            profile_id=candidate["profile"],
            project_id=candidate["project_scope"]["project_id"],
            anchor=candidate["anchor"],
            resources=candidate["resources"],
            seed=candidate["seed"],
            panels=candidate["panels"],
            repair_lineage=candidate["repair_lineage"],
            parent_plan_seal=candidate["parent_plan_seal"],
            initial_panels_commitment=candidate["commitments"]["initial_panels"],
            authorization_checked_at_unix=(
                candidate["authorization_checked_at_unix"]
            ),
        )
        resealed = {
            **unsigned,
            "plan_seal": _workflow._seal(
                "character-sheet-plan-v1", unsigned,
            ),
        }
        with self.assertRaisesRegex(CharacterSheetWorkflowError, "moved"):
            validate_character_sheet_plan(resealed)

    def test_seed_resource_and_revision_commitments_are_bound(self):
        plan = self._plan()
        self.assertEqual(
            set(plan["commitments"]),
            {
                "anchor", "seed", "base_model", "lora", "schedule", "terms",
                "planner", "reviewer", "editor", "execution_authorization",
                "initial_panels", "panels", "repair_lineage",
            },
        )
        for field, resource_key in (
            ("base_model", "base_model"),
            ("lora", "lora"),
            ("planner", "planner"),
            ("reviewer", "reviewer"),
            ("editor", "editor"),
        ):
            changed = self._resources()
            changed[resource_key]["revision"] += "-changed"
            changed = self._authorize(changed)
            candidate = self._plan(resources=changed)
            self.assertNotEqual(
                candidate["commitments"][field], plan["commitments"][field],
            )
            self.assertNotEqual(candidate["plan_seal"], plan["plan_seal"])
        changed_seed = self._plan(seed=987654322)
        self.assertNotEqual(changed_seed["commitments"]["seed"], plan["commitments"]["seed"])

    def test_terms_and_execution_authorization_are_mandatory_and_sealed(self):
        plan = self._plan()
        self.assertEqual(
            plan["resources"]["terms"]["revision"], "terms-revision-private",
        )
        self.assertRegex(plan["commitments"]["terms"], r"^[0-9a-f]{64}$")
        self.assertRegex(
            plan["commitments"]["execution_authorization"], r"^[0-9a-f]{64}$",
        )
        for missing in ("terms", "execution_authorization"):
            resources = self._resources()
            resources.pop(missing)
            with self.subTest(missing=missing):
                with self.assertRaises(CharacterSheetWorkflowError):
                    self._plan(resources=resources)
        malformed = self._resources()
        malformed["terms"]["acceptance_digest"] = "missing"
        with self.assertRaises(CharacterSheetWorkflowError):
            self._plan(resources=malformed)

    def test_public_projection_is_content_free_and_project_private(self):
        plan = self._plan()
        public = public_character_sheet_plan(plan)
        rendered = json.dumps(public, sort_keys=True)
        private_values = (
            "project-private-001",
            "anchor-private-001",
            "server-base-private",
            "server-lora-private",
            "server-terms-private",
            "local-planner-private",
            "local-reviewer-private",
            "local-qwen-editor-private",
            plan["anchor"]["sha256"],
            plan["panels"][0]["sha256"],
        )
        for private in private_values:
            self.assertNotIn(private, rendered)
        self.assertEqual(
            set(public),
            {
                "schema_version", "planner_version", "profile",
                "profile_status", "execution_status", "experimental",
                "anchor_kind", "panel_count", "ordered_panel_roles",
                "repair_operation", "repair_attempt_count", "repaired_roles",
                "planning_locality", "review_locality", "private_output",
            },
        )
        self.assertTrue(public["private_output"])
        self.assertEqual(
            public["execution_status"],
            "authorization_was_valid_at_plan_time",
        )
        expired_resources = self._authorize(
            self._resource_base(), expires_at_unix=1_200,
        )
        expired_plan = self._plan(resources=expired_resources)
        self.assertEqual(
            public_character_sheet_plan(expired_plan)["execution_status"],
            "authorization_was_valid_at_plan_time",
        )
        with self.assertRaises(CharacterSheetWorkflowError):
            assert_character_sheet_execution_authorized(
                expired_plan,
                now_unix=1_200,
                expected_plan_seal=expired_plan["plan_seal"],
                expected_authorization_evidence_digest=(
                    expired_plan["resources"]["execution_authorization"][
                        "evidence_digest"
                    ]
                ),
            )
        private_anchor = self._anchor(label="different-private-anchor")
        private_variant = self._resources()
        private_variant["base_model"]["id"] = "different-private-model"
        private_variant = self._authorize(
            private_variant, anchor=private_anchor, seed=1,
        )
        changed = self._plan(
            anchor=private_anchor,
            resources=private_variant,
            panels=[
                {**panel, "sha256": _sha(f"different-{panel['role']}")}
                for panel in self._panels()
            ],
            seed=1,
        )
        self.assertEqual(public_character_sheet_plan(changed), public)

    def test_canonical_seals_replay_and_tamper_validation_are_deterministic(self):
        first = self._plan()
        reordered_resources = {
            key: copy.deepcopy(self._resources()[key])
            for key in reversed(tuple(self._resources()))
        }
        second = self._plan(resources=reordered_resources)
        self.assertEqual(first, second)
        self.assertEqual(
            canonical_character_sheet_plan(first),
            canonical_character_sheet_plan(second),
        )

        round_trip = json.loads(json.dumps(first))
        self.assertEqual(
            assert_character_sheet_replay(
                first,
                round_trip,
                expected_project_id="project-private-001",
                expected_anchor=self._anchor(),
            ),
            first,
        )
        tampered = copy.deepcopy(round_trip)
        tampered["resources"]["lora"]["revision"] = "tampered-revision"
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(tampered)
        with self.assertRaises(CharacterSheetWorkflowError):
            assert_character_sheet_replay(first, self._plan(seed=1))
        with self.assertRaises(CharacterSheetWorkflowError):
            validate_character_sheet_plan(
                self._plan(seed=1), expected_plan_seal=first["plan_seal"],
            )

    def test_closed_schema_rejects_private_creative_payloads(self):
        plan = self._plan()
        for target, key in (
            (plan, "creative_prompt"),
            (plan["anchor"], "path"),
            (plan["panels"][0], "caption"),
            (plan["resources"], "model_path"),
        ):
            candidate = copy.deepcopy(plan)
            if target is plan:
                candidate[key] = "PRIVATE CREATIVE TEXT"
            elif target is plan["anchor"]:
                candidate["anchor"][key] = "/private/anchor.png"
            elif target is plan["panels"][0]:
                candidate["panels"][0][key] = "PRIVATE CREATIVE TEXT"
            else:
                candidate["resources"][key] = "/private/model.safetensors"
            with self.assertRaises(CharacterSheetWorkflowError):
                validate_character_sheet_plan(candidate)


if __name__ == "__main__":
    unittest.main()
