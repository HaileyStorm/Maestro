"""Synthetic regressions for reusable project reference-sheet construction."""

from __future__ import annotations

import hashlib
import copy
import json
import os
import re
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import mock

from PIL import Image, ImageFont

_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _ROOT / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from services import project_assets, reference_sheets, search_index
from services.reference_sheets import (
    ArtifactProvenance,
    PACK_ROLE_RECIPES,
    PackIntelligenceSelection,
    PackLoraSelection,
    PackModelSchedule,
    PackOperationRoute,
    ReferencePackArtifact,
    ROLE_RECIPES,
    PanelFile,
    ReferenceSheetReviewError,
    ReferenceSheetStructureError,
    build_failed_panel_repair_plan,
    build_reference_pack_plan,
    build_reference_sheet_plan,
    build_semantic_review_request,
    compose_reference_sheet,
    create_reference_pack,
    create_reference_sheet,
    parse_semantic_review_result,
    review_reference_sheet,
    validate_panel_files,
)


class ReferenceSheetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sources = self.root / "sources"
        self.outputs = self.root / "outputs"
        self.sources.mkdir()
        self.outputs.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _image(
        self,
        name: str,
        color: tuple[int, int, int],
        size: tuple[int, int] = (96, 80),
    ) -> Path:
        path = self.sources / name
        image = Image.new("RGB", size, color)
        image.save(path, format="PNG")
        image.close()
        return path

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _plan(self, mode: str = "production", asset_type: str = "character"):
        return build_reference_sheet_plan(
            asset_type=asset_type,
            mode=mode,
            creative_request="A precise continuity reference for the requested design.",
            model="local/qwen-image-edit",
            panel_size=(96, 80),
            draft_size=(240, 180),
            columns=2,
            palette_swatches=6,
        )

    def _panel_files(self, plan):
        result = []
        for index, role in enumerate(plan.panel_roles):
            result.append(PanelFile(
                role,
                self._image(
                    f"{index}-{role}.png",
                    ((index * 43 + 20) % 256, (index * 67 + 40) % 256, (index * 89 + 60) % 256),
                ),
            ))
        return result

    def _pack_plan(self, **updates):
        values = {
            "reference_type": "character",
            "mode": "production",
            "intent": "generic",
            "depth": "standard",
            "creative_request": "A precise adaptive continuity pack.",
            "generation_model": "flux2_dev",
            "editor_model": "qwen_image_edit_2511_20B_fp8_lightning_8step",
            "sheet_size": (96, 80),
            "planning": PackIntelligenceSelection("auto", "deterministic", "local"),
            "review_selection": PackIntelligenceSelection("test-reviewer", "test-reviewer", "local"),
        }
        values.update(updates)
        return build_reference_pack_plan(**values)

    def _pack_artifacts(self, plan, prefix="review"):
        return tuple(
            ReferencePackArtifact(
                path=self._image(
                    f"{prefix}-{index}.png",
                    ((index * 37 + 20) % 255, 40, 60),
                    plan.sheet_size,
                ),
                role=role,
                index=index,
                model=plan.generation_model,
                provenance=ArtifactProvenance(
                    "synthetic", plan.planner_version,
                ),
                anchor_role=plan.anchor_role,
            )
            for index, role in enumerate(plan.output_roles)
        )

    @staticmethod
    def _pass_review():
        return {
            "status": "pass",
            "checks": {
                "identity": True,
                "request": True,
                "view": True,
                "accessory": True,
                "style": True,
            },
            "failed_roles": [],
            "reason_codes": [],
        }

    @staticmethod
    def _fail_review(*roles: str):
        return {
            "status": "fail",
            "checks": {
                "identity": False,
                "request": True,
                "view": True,
                "accessory": True,
                "style": True,
            },
            "failed_roles": list(roles),
            "reason_codes": ["identity_mismatch"],
        }

    @staticmethod
    def _rubric_observations(reference_type, roles, failures=None):
        failures = failures or {}
        applicability = dict(reference_sheets.fidelity_rubric_role_applicability(
            reference_type, roles,
        ))
        return tuple(
            reference_sheets.FidelityRubricObservation(
                item.item_id,
                "fail" if role in failures.get(item.item_id, ()) else "pass",
                (role,) if role in failures.get(item.item_id, ()) else (),
                role,
            )
            for item in reference_sheets.FIDELITY_RUBRIC
            for role in applicability[item.item_id]
        )

    def test_versioned_modes_and_ordered_role_recipes(self):
        expected = {
            "character": (
                "identity_front", "three_quarter", "profile", "full_body",
                "expression", "accessory_detail",
            ),
            "setting": ("establishing", "reverse_angle", "mid_view", "detail", "lighting"),
            "item": ("hero_view", "side_view", "rear_view", "detail", "scale_context"),
            "style": ("keyframe", "composition", "lighting", "material", "motion"),
        }
        for asset_type, roles in expected.items():
            with self.subTest(asset_type=asset_type):
                plan = self._plan(asset_type=asset_type)
                self.assertEqual(plan.schema_version, 1)
                self.assertEqual(plan.planner_version, "reference-sheet-v1")
                self.assertEqual(plan.panel_roles, roles)
                self.assertEqual(tuple(recipe.role for recipe in ROLE_RECIPES[asset_type]), roles)
        for mode in ("production", "hybrid", "draft"):
            self.assertEqual(self._plan(mode=mode).mode, mode)
        with self.assertRaises(ValueError):
            self._plan(mode="automatic")
        with self.assertRaises(ValueError):
            self._plan(asset_type="person")
        with self.assertRaises(ValueError):
            build_reference_sheet_plan(
                asset_type="character", mode="production", creative_request="x",
                model="/tmp/private/model", panel_size=(96, 80),
            )
        with self.assertRaises(ValueError):
            build_reference_sheet_plan(
                asset_type="character", mode="production", creative_request="x",
                model="C:/Users/Alice/private-model", panel_size=(96, 80),
            )
        with self.assertRaises(TypeError):
            ROLE_RECIPES["character"] = ()

    def test_parameterized_lora_values_are_private_and_public_summary_is_digest_only(self):
        selection = PackLoraSelection(
            lora_id="owner-control.safetensors",
            multiplier=1.2,
            requested_scope="auto",
            resolved_scopes=("generation", "editing"),
            roles=("canonical_identity", "turnaround"),
            revision="local",
            source_sha256="a" * 64,
            parameter_schema_digest="b" * 64,
            parameter_commitment_context="9" * 64,
            parameter_values=(("body_scale", "PRIVATE_OWNER_VALUE"),),
            parameter_values_digest="c" * 64,
            parameter_expansion_digest="d" * 64,
        )
        plan = self._pack_plan(additional_loras=(selection,))
        public = plan.public_preview()["additional_loras"]["applied"][0]
        self.assertEqual(public["parameters"], {
            "count": 1,
            "ids": ["body_scale"],
            "schema_digest": "b" * 64,
            "values_digest": "c" * 64,
            "expansion_digest": "d" * 64,
        })
        self.assertNotIn("PRIVATE_OWNER_VALUE", json.dumps(public))
        private = plan.private_authored_settings()[
            "additional_lora_parameters"
        ][0]
        self.assertEqual(
            private["values"],
            [{"id": "body_scale", "value": "PRIVATE_OWNER_VALUE"}],
        )
        skipped = replace(selection, skipped_reason="incompatible")
        skipped_public = skipped.public_metadata()
        self.assertEqual(skipped_public["parameters"], public["parameters"])
        self.assertNotIn("PRIVATE_OWNER_VALUE", json.dumps(skipped_public))
        trigger_only = replace(
            selection,
            parameter_values=(),
            parameter_values_digest="e" * 64,
            parameter_expansion_digest="f" * 64,
        )
        trigger_public = trigger_only.public_metadata()
        self.assertEqual(trigger_public["parameters"]["count"], 0)
        self.assertEqual(trigger_public["parameters"]["ids"], [])
        trigger_plan = self._pack_plan(additional_loras=(trigger_only,))
        self.assertEqual(
            trigger_plan.private_authored_settings()[
                "additional_lora_parameters"
            ][0]["values"],
            [],
        )

        malformed_digest = replace(selection, parameter_schema_digest=123)
        with self.assertRaisesRegex(ValueError, "sealed selections"):
            self._pack_plan(additional_loras=(malformed_digest,))
        malformed_context = replace(
            selection, parameter_commitment_context="short",
        )
        with self.assertRaisesRegex(ValueError, "sealed selections"):
            self._pack_plan(additional_loras=(malformed_context,))

        snapshot = plan.private_authored_settings()
        duplicate_lora = copy.deepcopy(snapshot)
        duplicate_lora["additional_lora_parameters"].append(copy.deepcopy(
            duplicate_lora["additional_lora_parameters"][0]
        ))
        with self.assertRaisesRegex(ValueError, "private authored"):
            reference_sheets.reference_pack_authored_settings_seal(
                duplicate_lora,
            )
        duplicate_parameter = copy.deepcopy(snapshot)
        duplicate_parameter["additional_lora_parameters"][0]["values"].append({
            "id": "body_scale", "value": "duplicate",
        })
        with self.assertRaisesRegex(ValueError, "private authored"):
            reference_sheets.reference_pack_authored_settings_seal(
                duplicate_parameter,
            )
        nonfinite = copy.deepcopy(snapshot)
        nonfinite["additional_lora_parameters"][0]["values"][0]["value"] = float("nan")
        with self.assertRaisesRegex(ValueError, "private authored"):
            reference_sheets.reference_pack_authored_settings_seal(nonfinite)
        too_many = copy.deepcopy(snapshot)
        too_many["additional_lora_parameters"] = [
            {
                **copy.deepcopy(snapshot["additional_lora_parameters"][0]),
                "id": f"lora-{index}.safetensors",
            }
            for index in range(65)
        ]
        with self.assertRaisesRegex(ValueError, "private authored"):
            reference_sheets.reference_pack_authored_settings_seal(too_many)

    def test_deterministic_collage_geometry_order_labels_and_no_clipping(self):
        plan = self._plan()
        panels = self._panel_files(plan)
        first = self.outputs / "first.png"
        second = self.outputs / "second.png"
        first_geometry = compose_reference_sheet(plan, panels, first)
        second_geometry = compose_reference_sheet(plan, panels, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first_geometry, second_geometry)
        self.assertEqual(
            tuple(place.role for place in first_geometry.placements),
            plan.panel_roles,
        )
        for placement, panel in zip(first_geometry.placements, panels):
            left, top, right, bottom = placement.image_box
            self.assertEqual((right - left, bottom - top), plan.panel_size)
            with Image.open(panel.path) as source, Image.open(first) as sheet:
                # Exact corner pixels prove the source is pasted at native size,
                # without crop, rescale, or label overlap.
                self.assertEqual(sheet.getpixel((left, top)), source.getpixel((0, 0)))
                self.assertEqual(
                    sheet.getpixel((right - 1, bottom - 1)),
                    source.getpixel((source.width - 1, source.height - 1)),
                )
            self.assertLessEqual(placement.label_box[2], first_geometry.canvas_size[0])
            self.assertLessEqual(placement.image_box[3], first_geometry.palette_box[1])
            label = next(recipe.label for recipe in plan.panels if recipe.role == placement.role)
            bounds = ImageFont.load_default().getbbox(label)
            self.assertLessEqual(bounds[2] - bounds[0] + 16, placement.label_box[2] - placement.label_box[0])

    def test_structural_validation_rejects_roles_dimensions_and_duplicate_files(self):
        plan = self._plan()
        panels = self._panel_files(plan)
        with self.assertRaisesRegex(ReferenceSheetStructureError, "panel_roles_invalid"):
            validate_panel_files(
                panels[:-1], expected_roles=plan.panel_roles, panel_size=plan.panel_size,
            )
        duplicates = list(panels)
        duplicates[1] = PanelFile(duplicates[0].role, duplicates[1].path)
        with self.assertRaisesRegex(ReferenceSheetStructureError, "panel_role_duplicate"):
            validate_panel_files(
                duplicates, expected_roles=plan.panel_roles, panel_size=plan.panel_size,
            )
        duplicate_path = list(panels)
        duplicate_path[1] = PanelFile(plan.panel_roles[1], duplicate_path[0].path)
        with self.assertRaisesRegex(ReferenceSheetStructureError, "panel_file_duplicate"):
            validate_panel_files(
                duplicate_path, expected_roles=plan.panel_roles, panel_size=plan.panel_size,
            )
        if hasattr(os, "link"):
            hard_link = self.sources / "hard-link.png"
            try:
                os.link(panels[0].path, hard_link)
            except OSError:
                hard_link = None
            if hard_link is not None:
                hard_link_panels = list(panels)
                hard_link_panels[1] = PanelFile(plan.panel_roles[1], hard_link)
                with self.assertRaisesRegex(
                    ReferenceSheetStructureError, "panel_file_duplicate",
                ):
                    validate_panel_files(
                        hard_link_panels,
                        expected_roles=plan.panel_roles,
                        panel_size=plan.panel_size,
                    )
        wrong_size = list(panels)
        wrong_size[-1] = PanelFile(
            plan.panel_roles[-1], self._image("wrong.png", (1, 2, 3), (95, 80)),
        )
        with self.assertRaises(ReferenceSheetStructureError) as caught:
            validate_panel_files(
                wrong_size, expected_roles=plan.panel_roles, panel_size=plan.panel_size,
            )
        self.assertEqual(caught.exception.reason_code, "panel_dimensions_invalid")
        self.assertEqual(caught.exception.failed_roles, (plan.panel_roles[-1],))

    def test_composition_never_overwrites_an_existing_output(self):
        plan = self._plan()
        destination = self.outputs / "existing.png"
        destination.write_bytes(b"preserve-me")
        with self.assertRaisesRegex(ReferenceSheetStructureError, "sheet_output_exists"):
            compose_reference_sheet(plan, self._panel_files(plan), destination)
        self.assertEqual(destination.read_bytes(), b"preserve-me")

    def test_composition_save_failure_preserves_concurrent_output_replacement(self):
        plan = self._plan()
        destination = self.outputs / "save-race.png"
        real_save = Image.Image.save
        replaced = []

        def replace_then_fail(image, fp, *args, **kwargs):
            if destination.exists() and not replaced:
                destination.unlink()
                destination.write_bytes(b"external-replacement")
                replaced.append(True)
                raise OSError("synthetic image save failure")
            return real_save(image, fp, *args, **kwargs)

        with (
            mock.patch.object(Image.Image, "save", replace_then_fail),
            self.assertRaisesRegex(OSError, "synthetic image save failure"),
        ):
            compose_reference_sheet(plan, self._panel_files(plan), destination)
        self.assertEqual(replaced, [True])
        self.assertEqual(destination.read_bytes(), b"external-replacement")

    def test_composition_successful_save_detects_and_preserves_path_replacement(self):
        plan = self._plan()
        panels = self._panel_files(plan)
        destination = self.outputs / "save-success-race.png"
        real_save = Image.Image.save
        replaced = []

        def save_then_replace(image, fp, *args, **kwargs):
            result = real_save(image, fp, *args, **kwargs)
            destination.unlink()
            destination.write_bytes(b"external-replacement")
            replaced.append(True)
            return result

        with (
            mock.patch.object(Image.Image, "save", save_then_replace),
            self.assertRaisesRegex(ReferenceSheetStructureError, "sheet_output_replaced"),
        ):
            compose_reference_sheet(plan, panels, destination)
        self.assertEqual(replaced, [True])
        self.assertEqual(destination.read_bytes(), b"external-replacement")

    def test_composition_descriptor_dup_failure_leaves_no_partial_output(self):
        plan = self._plan()
        panels = self._panel_files(plan)
        destination = self.outputs / "dup-failure.png"
        with (
            mock.patch.object(reference_sheets.os, "dup", side_effect=OSError("too many files")),
            self.assertRaisesRegex(OSError, "too many files"),
        ):
            compose_reference_sheet(plan, panels, destination)
        self.assertFalse(destination.exists())

    def test_v2_production_generates_one_immutable_anchor_then_reference_derivatives(self):
        plan = self._pack_plan()
        calls = []
        anchor = self._image("pack-anchor.png", (40, 50, 60))
        anchor_digest = self._digest(anchor)

        def generate(request):
            calls.append(("generate", request.role, request.strategy, None, None))
            return anchor

        def edit(primary, canonical, request):
            calls.append((
                "edit", request.role, request.strategy,
                primary.resolve(), canonical.resolve(),
            ))
            return self._image(
                f"pack-{request.index}.png",
                (request.index * 31, request.index * 29, request.index * 23),
            )

        result = create_reference_pack(
            plan,
            generate_sheet=generate,
            edit_sheet=edit,
            reviewer=lambda _request: True,
        )
        self.assertEqual(len([call for call in calls if call[0] == "generate"]), 1)
        self.assertEqual(calls[0][2], "canonical_anchor")
        self.assertEqual(
            [call[2] for call in calls[1:]],
            ["reference_guided_derivative"] * (len(plan.sheets) - 1),
        )
        self.assertTrue(all(call[4] == anchor.resolve() for call in calls[1:]))
        self.assertEqual(self._digest(anchor), anchor_digest)
        self.assertEqual(result.artifacts[0].provenance.strategy, "canonical_anchor")
        self.assertTrue(all(
            item.provenance.strategy == "reference_guided_derivative"
            for item in result.artifacts[1:]
        ))
        self.assertEqual(tuple(item.role for item in result.artifacts), plan.sheet_roles)

    def test_v2_reviewer_cannot_mutate_or_replace_any_reviewed_artifact(self):
        for replacement_kind in ("mutate", "regular", "symlink"):
            if replacement_kind == "symlink" and not hasattr(os, "symlink"):
                continue
            with self.subTest(replacement_kind=replacement_kind):
                plan = self._pack_plan(depth="compact")
                anchor = self._image(
                    f"review-{replacement_kind}.png", (40, 50, 60),
                )
                external = self._image(
                    f"review-{replacement_kind}-external.png", (5, 6, 7),
                )

                def review(request):
                    # The reviewer receives a descriptor-backed path on POSIX;
                    # resolving it simulates an attempted mutation of the
                    # private review stage rather than the authored source.
                    reviewed = request.sheet_paths[0].resolve()
                    if replacement_kind == "mutate":
                        with Image.new("RGB", plan.sheet_size, (200, 10, 20)) as image:
                            image.save(reviewed, format="PNG")
                    else:
                        reviewed.unlink()
                        if replacement_kind == "regular":
                            reviewed.write_bytes(b"unowned-regular-replacement")
                        else:
                            reviewed.symlink_to(external)
                    return True

                with self.assertRaisesRegex(
                    ReferenceSheetStructureError, "sheet_stage_modified",
                ):
                    create_reference_pack(
                        plan,
                        generate_sheet=lambda _request: anchor,
                        edit_sheet=lambda *_args: self.fail("compact cannot edit"),
                        reviewer=review,
                    )
                if replacement_kind == "mutate":
                    self.assertFalse(anchor.exists())
                elif replacement_kind == "regular":
                    self.assertEqual(
                        anchor.read_bytes(), b"unowned-regular-replacement",
                    )
                    anchor.unlink()
                else:
                    self.assertTrue(anchor.is_symlink())
                    self.assertEqual(anchor.resolve(), external.resolve())
                    anchor.unlink()
                self.assertTrue(external.is_file())

    def test_v2_reviewer_consumes_sealed_copy_during_source_swap_and_restore(self):
        plan = self._pack_plan(depth="compact")
        anchor = self._image("review-swap-anchor.png", (40, 50, 60))
        alternate = self._image("review-swap-alternate.png", (200, 10, 20))
        original_digest = self._digest(anchor)
        alternate_digest = self._digest(alternate)
        reviewed = []

        def review(request):
            held_original = anchor.with_name("review-swap-held.png")
            os.replace(anchor, held_original)
            os.replace(alternate, anchor)
            try:
                reviewed.append(self._digest(request.sheet_paths[0]))
            finally:
                os.replace(anchor, alternate)
                os.replace(held_original, anchor)
            return True

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=lambda *_args: self.fail("compact cannot edit"),
            reviewer=review,
        )
        expected_calls = sum(
            len(roles) for _item_id, roles in
            reference_sheets.fidelity_rubric_role_applicability(
                plan.reference_type, plan.output_roles,
            )
        )
        self.assertEqual(reviewed, [original_digest] * expected_calls)
        self.assertNotEqual(reviewed[0], alternate_digest)
        self.assertEqual(result.review.artifact_seals[0].sha256, reviewed[0])
        self.assertEqual(self._digest(anchor), original_digest)

    def test_v2_review_returns_private_exact_artifact_seals(self):
        plan = self._pack_plan(depth="compact")
        anchor = self._image("review-sealed.png", (41, 51, 61))
        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=lambda *_args: self.fail("compact cannot edit"),
            reviewer=lambda _request: True,
        )
        self.assertEqual(len(result.review.artifact_seals), 1)
        seal = result.review.artifact_seals[0]
        current = anchor.stat()
        self.assertEqual((seal.role, seal.index), (plan.output_roles[0], 0))
        self.assertEqual((seal.device, seal.inode), (current.st_dev, current.st_ino))
        self.assertEqual(seal.size, current.st_size)
        self.assertEqual(seal.sha256, self._digest(anchor))
        public = json.dumps(result.public_metadata())
        self.assertNotIn(seal.sha256, public)
        self.assertNotIn(str(anchor), public)

    def test_v2_review_fails_closed_without_stable_descriptor_path(self):
        plan = self._pack_plan(depth="compact")
        anchor = self._image("review-no-descriptor.png", (42, 52, 62))
        reviewer = mock.Mock(side_effect=self._pass_review)
        with mock.patch.object(
            reference_sheets, "_review_descriptor_path", return_value=None,
        ):
            result = create_reference_pack(
                plan,
                generate_sheet=lambda _request: anchor,
                edit_sheet=lambda *_args: self.fail("compact cannot edit"),
                reviewer=reviewer,
            )
        reviewer.assert_not_called()
        self.assertEqual(result.review.status, "review_unavailable")

    def test_v2_failed_anchor_regenerates_all_outputs_into_new_files(self):
        plan = self._pack_plan(depth="standard")
        generated = []
        edited = []
        review_count = 0

        def generate(request):
            path = self._image(
                f"anchor-generation-{len(generated)}.png",
                (30 + len(generated), 40, 50),
            )
            generated.append((request, path, self._digest(path)))
            return path

        def edit(_primary, _anchor, request):
            path = self._image(
                f"anchor-derivative-{len(edited)}.png",
                (60 + len(edited), 70, 80),
            )
            edited.append((request, path, self._digest(path)))
            return path

        def review(_request):
            nonlocal review_count
            review_count += 1
            return review_count != 1

        result = create_reference_pack(
            plan,
            generate_sheet=generate,
            edit_sheet=edit,
            repair_sheet=lambda *_args: self.fail(
                "anchor rejection regenerates instead of mutating"
            ),
            reviewer=review,
            max_repair_attempts=1,
        )
        self.assertEqual(result.repaired_roles, (plan.anchor_role,))
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(edited), 2 * (len(plan.sheets) - 1))
        self.assertEqual(
            [item.provenance.strategy for item in result.artifacts],
            ["canonical_anchor_regeneration"]
            + ["reference_guided_regeneration"] * (len(plan.sheets) - 1),
        )
        for _request, path, digest in (*generated[:1], *edited[:len(plan.sheets) - 1]):
            self.assertTrue(path.exists())
            self.assertEqual(self._digest(path), digest)

    def test_v2_failed_callout_repairs_only_that_authored_output(self):
        plan = self._pack_plan(
            depth="compact",
            detail_callouts=[{"kind": "face", "operation": "reconstruct"}],
        )
        anchor = self._image("callout-repair-anchor.png", (81, 91, 101))
        original_callout = self._image("callout-original.png", (111, 121, 131))
        repaired_callout = self._image("callout-repaired.png", (141, 151, 161))
        anchor_digest = self._digest(anchor)
        original_digest = self._digest(original_callout)
        review_count = 0
        failed_once = False
        repairs = []

        def review(request):
            nonlocal review_count, failed_once
            review_count += 1
            if (
                not failed_once
                and request.item_id == "authored_callouts"
                and request.target_role == plan.detail_callouts[0].target_role
            ):
                failed_once = True
                return False
            return True

        def repair(primary, canonical, request):
            repairs.append((primary, canonical, request))
            return repaired_callout

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=lambda *_args: original_callout,
            repair_sheet=repair,
            reviewer=review,
            max_repair_attempts=1,
        )
        self.assertEqual(result.repaired_roles, (plan.detail_callouts[0].target_role,))
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0][0], original_callout.resolve())
        self.assertEqual(repairs[0][1], anchor.resolve())
        repaired = result.artifacts[-1]
        self.assertEqual(repaired.path, repaired_callout.resolve())
        self.assertEqual(repaired.provenance.strategy, "detail_callout_repair")
        self.assertEqual(repaired.detail_provenance["source_role"], plan.anchor_role)
        self.assertEqual(self._digest(anchor), anchor_digest)
        self.assertEqual(self._digest(original_callout), original_digest)

    def test_v2_depth_types_presets_and_anchor_basis_are_sealed(self):
        expected_counts = {"compact": 1, "standard": 3, "comprehensive": 5}
        aliases = {
            "character": "character", "setting": "location", "item": "prop",
            "machine": "vehicle", "creature": "creature",
            "accessory": "wardrobe", "style": "world",
        }
        for source_type, canonical in aliases.items():
            for depth, count in expected_counts.items():
                with self.subTest(source_type=source_type, depth=depth):
                    plan = self._pack_plan(
                        reference_type=source_type,
                        depth=depth,
                        editor_model="editor/model",
                    )
                    self.assertEqual(plan.reference_type, canonical)
                    self.assertEqual(len(plan.sheets), count)
                    self.assertEqual(plan.sheets[0], PACK_ROLE_RECIPES[canonical][0])
                    self.assertEqual(len(plan.plan_seal), 64)
        custom = self._pack_plan(depth="custom", sheet_count=2)
        self.assertEqual(len(custom.sheets), 2)
        with self.assertRaises(ValueError):
            self._pack_plan(depth="standard", sheet_count=2)
        with self.assertRaises(ValueError):
            self._pack_plan(depth="custom", sheet_count=6)

        anatomy = self._pack_plan(preset="anatomy")
        self.assertEqual(anatomy.anchor_basis, "anatomy")
        self.assertEqual(anatomy.anchor_privacy, "private_blurred")
        clothed = self._pack_plan(anchor_basis="primary_outfit")
        self.assertEqual(clothed.anchor_basis, "primary_outfit")
        self.assertNotEqual(anatomy.plan_seal, clothed.plan_seal)
        with self.assertRaises(ValueError):
            self._pack_plan(preset="anatomy", anchor_basis="primary_outfit")
        with self.assertRaises(ValueError):
            self._pack_plan(reference_type="prop", anchor_basis="anatomy")

    def test_identity_focus_is_opt_in_face_and_body_probe(self):
        self.assertEqual(reference_sheets.PACK_DEFAULT_PRESETS["character"], "identity")
        self.assertIn(
            "identity_focus", reference_sheets.PACK_TYPE_PRESETS["character"],
        )
        self.assertEqual(
            reference_sheets.reference_pack_ordered_roles(
                "character", "identity",
            ),
            (
                "canonical_identity", "turnaround", "expressions",
                "wardrobe", "identity_details",
            ),
        )

        plan = self._pack_plan(preset="identity_focus")
        self.assertEqual(plan.schema_version, 2)
        self.assertEqual(plan.planner_version, "reference-pack-v2")
        self.assertEqual(plan.preset, "identity_focus")
        self.assertEqual(
            plan.sheet_roles, ("frontal_face", "body_front", "body_back"),
        )
        self.assertEqual(plan.anchor_role, "frontal_face")
        self.assertEqual(plan.anchor_strategy, "layout_probe_anchor")
        self.assertEqual(plan.anchor_basis, "primary_outfit")
        self.assertEqual(
            [(sheet.role, sheet.label) for sheet in plan.sheets],
            [
                ("frontal_face", "FRONTAL FACE"),
                ("body_front", "BODY FRONT"),
                ("body_back", "BODY BACK"),
            ],
        )
        self.assertEqual(
            plan.public_preview()["ordered_output_roles"],
            ["frontal_face", "body_front", "body_back"],
        )
        self.assertEqual(
            plan.public_preview()["anchor_strategy"], "layout_probe_anchor",
        )
        self.assertEqual(
            reference_sheets.reference_pack_ordered_roles(
                "character", "identity_focus", 5,
            ),
            (
                "frontal_face", "body_front", "body_back",
                "identity_details", "expressions",
            ),
        )
        with self.assertRaisesRegex(ValueError, "preset does not match"):
            self._pack_plan(reference_type="prop", preset="identity_focus")

        # The archival collage contract remains separate and unchanged.
        self.assertEqual(
            tuple(recipe.role for recipe in reference_sheets.ROLE_RECIPES["character"]),
            (
                "identity_front", "three_quarter", "profile", "full_body",
                "expression", "accessory_detail",
            ),
        )

    def test_identity_focus_artifact_provenance_stays_layout_probe_specific(self):
        plan = self._pack_plan(
            preset="identity_focus", depth="compact",
        )
        generated = []
        review_count = 0

        def generate(request):
            generated.append(request)
            return self._image(
                f"identity-focus-anchor-{len(generated)}.png",
                (40 + len(generated), 50, 60),
                plan.sheet_size,
            )

        def review(_request):
            nonlocal review_count
            review_count += 1
            return review_count != 1

        result = create_reference_pack(
            plan,
            generate_sheet=generate,
            edit_sheet=lambda *_args: self.fail("compact cannot edit"),
            repair_sheet=lambda *_args: self.fail(
                "anchor rejection regenerates instead of repairing",
            ),
            reviewer=review,
            max_repair_attempts=1,
        )
        self.assertEqual(
            [request.strategy for request in generated],
            ["layout_probe_anchor", "layout_probe_anchor_regeneration"],
        )
        self.assertTrue(all(
            request.routing_operation == "generation" for request in generated
        ))
        self.assertEqual(
            result.artifacts[0].provenance.strategy,
            "layout_probe_anchor_regeneration",
        )
        ordinary = self._pack_plan(depth="compact")
        self.assertEqual(ordinary.anchor_strategy, "canonical_anchor")
        self.assertEqual(
            ordinary.public_preview()["anchor_strategy"], "canonical_anchor",
        )

    def test_v2_private_output_and_blur_define_truthful_sealed_anchor_privacy(self):
        seals = set()
        for private, blurred, expected in (
            (False, False, "project_visible"),
            (False, True, "project_blurred"),
            (True, False, "private_visible"),
            (True, True, "private_blurred"),
        ):
            with self.subTest(private=private, blurred=blurred):
                plan = self._pack_plan(
                    private_output=private,
                    initial_blur=blurred,
                )
                preview = plan.public_preview()
                self.assertEqual(plan.anchor_privacy, expected)
                self.assertEqual(preview["anchor_privacy"], expected)
                self.assertEqual(preview["private_output"], private)
                self.assertEqual(preview["initial_blur"], blurred)
                seals.add(plan.plan_seal)
        self.assertEqual(len(seals), 4)

        anatomy = self._pack_plan(preset="anatomy")
        self.assertTrue(anatomy.private_output)
        self.assertTrue(anatomy.initial_blur)
        self.assertEqual(anatomy.anchor_privacy, "private_blurred")
        forged = replace(anatomy, private_output=False)
        with self.assertRaisesRegex(ValueError, "unsupported reference-pack plan"):
            create_reference_pack(
                forged,
                generate_sheet=lambda _request: self.fail("must not execute"),
                edit_sheet=lambda *_args: self.fail("must not execute"),
            )

    def test_v2_operation_routes_are_complete_bounded_and_part_of_plan_seal(self):
        standard = self._pack_plan()
        standard_routes = standard.public_preview()["operation_routing"]
        self.assertEqual(standard_routes["requested_capability"], "standard")
        self.assertTrue(all(
            route["status"] == "standard"
            for route in standard_routes["operations"].values()
        ))

        skipped = self._pack_plan(content_capability="unrestricted_local")
        self.assertTrue(all(
            route["status"] == "skipped"
            and route["reason"] == "no_verified_compatible_recipe"
            and route["requested_model"] == route["resolved_model"]
            for route in skipped.public_preview()["operation_routing"]["operations"].values()
        ))
        self.assertNotEqual(standard.plan_seal, skipped.plan_seal)

        applied_routes = tuple(PackOperationRoute(
            operation=operation,
            requested_capability="unrestricted_local",
            requested_model=(
                "flux2_dev" if operation == "generation"
                else "qwen_image_edit_2511_20B_fp8_lightning_8step"
            ),
            resolved_model=f"verified_{operation}",
            status="applied",
            schedule=PackModelSchedule(
                model=f"verified_{operation}",
                steps=10,
                guidance=1.0,
                guidance_key="guidance_scale",
                source="model_default",
            ),
            recipe_id=f"verified-{operation}-v1",
            verification_status="verified",
        ) for operation in ("generation", "edit", "repair", "callout"))
        applied = self._pack_plan(
            content_capability="unrestricted_local",
            operation_routing=applied_routes,
        )
        self.assertNotEqual(applied.plan_seal, skipped.plan_seal)
        self.assertEqual(
            applied.public_preview()["operation_routing"]["operations"]["repair"]["resolved_model"],
            "verified_repair",
        )
        forged = replace(applied, operation_routing=tuple(reversed(applied.operation_routing)))
        with self.assertRaisesRegex(ValueError, "unsupported reference-pack plan"):
            create_reference_pack(
                forged,
                generate_sheet=lambda _request: self.fail("must not execute"),
                edit_sheet=lambda *_args: self.fail("must not execute"),
            )

    def test_v2_draft_is_truthfully_unanchored_and_never_edits_or_repairs(self):
        plan = self._pack_plan(
            mode="draft",
            editor_model=None,
            depth="custom",
            sheet_count=2,
        )
        requests = []

        def generate(request):
            requests.append(request)
            return self._image(
                f"draft-pack-{request.index}.png",
                (20 + request.index, 30, 40),
            )

        result = create_reference_pack(
            plan,
            generate_sheet=generate,
            edit_sheet=lambda *_args: self.fail("draft cannot edit"),
            repair_sheet=lambda *_args: self.fail("draft cannot repair"),
            reviewer=lambda request: request.target_role != request.sheet_roles[-1],
            max_repair_attempts=5,
        )
        self.assertEqual([item.strategy for item in requests], ["draft_one_shot"] * 2)
        self.assertIsNone(plan.anchor_role)
        self.assertEqual(plan.anchor_strategy, "draft_one_shot")
        self.assertEqual(result.repair_attempts_used, 0)
        self.assertEqual(result.review.status, "fail")

    def test_v2_detail_callouts_are_type_discriminated_and_exact_never_reconstructs(self):
        plan = self._pack_plan(detail_callouts=[{
            "kind": "garment", "operation": "auto",
        }])
        callout = plan.public_preview()["detail_callouts"][0]
        self.assertEqual(callout["kind"], "garment")
        self.assertIn(callout["source_role"], plan.sheet_roles)
        with self.assertRaises(ValueError):
            self._pack_plan(detail_callouts=[{
                "kind": "mechanism", "operation": "enhance",
            }])
        with self.assertRaises(ValueError):
            self._pack_plan(
                intent="exact_spec",
                detail_callouts=[{"kind": "face", "operation": "reconstruct"}],
            )

    def test_v2_sufficient_detail_source_uses_deterministic_crop_without_editor(self):
        plan = self._pack_plan(
            depth="comprehensive",
            detail_callouts=[{"kind": "face", "operation": "auto"}],
        )
        anchor = self._image("detail-anchor.png", (90, 100, 110))
        edited_roles = []

        def edit(_primary, _anchor, request):
            edited_roles.append(request.role)
            return self._image(
                f"detail-edit-{request.index}.png",
                (request.index * 10, 20, 30),
            )

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=edit,
            reviewer=lambda _request: True,
        )
        detail = next(item for item in result.artifacts if item.role.startswith(
            "detail_callout:builtin:face"
        ))
        self.assertIn("identity_details", edited_roles)
        self.assertEqual(detail.provenance.strategy, "deterministic_crop")
        self.assertEqual(detail.detail_provenance["resolved_operation"], "crop")
        self.assertIsNone(detail.detail_provenance["editor_model"])
        self.assertEqual(len(detail.detail_provenance["source_digest"]), 64)

    def test_v2_ordered_authored_fields_are_sealed_and_public_preview_is_label_free(self):
        private_label = "PRIVATE_CUSTOM_EXPRESSION"
        fields = {
            "poses": [
                {
                    "id": "views:front", "label": "front",
                    "custom": False, "group": "views",
                },
                {
                    "id": "custom:0123456789abcdef",
                    "label": private_label,
                    "custom": True, "group": "expressions",
                },
            ],
        }
        plan = self._pack_plan(type_fields=fields)
        preview = plan.public_preview()
        self.assertNotIn(private_label, json.dumps(preview))
        self.assertEqual(
            preview["authored_settings"]["type_fields"][0]["items"],
            [
                {"id": "views:front", "custom": False, "group": "views"},
                {
                    "id": "custom:0123456789abcdef",
                    "custom": True,
                    "group": "expressions",
                },
            ],
        )
        private = plan.private_authored_settings()
        self.assertEqual(private["type_fields"]["poses"][1]["label"], private_label)
        self.assertEqual(
            reference_sheets.reference_pack_authored_settings_seal(private),
            plan.authored_settings_seal,
        )
        with self.assertRaises(ValueError):
            reference_sheets.reference_pack_authored_settings_seal({
                "type_fields": {},
            })
        reversed_plan = self._pack_plan(type_fields={
            "poses": list(reversed(fields["poses"])),
        })
        self.assertNotEqual(plan.authored_settings_seal, reversed_plan.authored_settings_seal)
        self.assertNotEqual(plan.plan_seal, reversed_plan.plan_seal)
        relabeled = self._pack_plan(type_fields={
            "poses": [fields["poses"][0], {
                **fields["poses"][1], "label": "PRIVATE_OTHER_EXPRESSION",
            }],
        })
        self.assertNotEqual(plan.plan_seal, relabeled.plan_seal)

    def test_v2_authored_style_is_normalized_private_and_commitment_only_public(self):
        private_style = "PRIVATE ETCHED INK STYLE"
        plan = self._pack_plan(
            style=f"  {private_style}  ",
            style_commitment="a" * 64,
        )
        private = plan.private_authored_settings()
        public = plan.public_preview()["authored_settings"]
        self.assertEqual(private["style"], private_style)
        self.assertTrue(public["style_present"])
        self.assertEqual(public["style_commitment"], "a" * 64)
        self.assertNotIn(private_style, json.dumps(public))
        self.assertEqual(
            reference_sheets.reference_pack_authored_settings_seal(private),
            plan.authored_settings_seal,
        )
        changed = copy.deepcopy(private)
        changed["style"] = "PRIVATE DIFFERENT STYLE"
        self.assertNotEqual(
            reference_sheets.reference_pack_authored_settings_seal(changed),
            plan.authored_settings_seal,
        )
        with self.assertRaisesRegex(ValueError, "commitment"):
            self._pack_plan(style=private_style)
        with self.assertRaisesRegex(ValueError, "style_commitment"):
            self._pack_plan(
                style=private_style, style_commitment="not-a-commitment",
            )

    def test_v2_authored_contract_conditions_generation_repair_and_isolated_review(self):
        private_style = "PRIVATE ADULT COPPERPLATE NOCTURNE"
        private_type_label = "PRIVATE INTIMATE INJURY SILHOUETTE"
        private_callout_label = "PRIVATE GRAPHIC WOUND IRIS FILIGREE"
        plan = self._pack_plan(
            depth="standard",
            style=private_style,
            style_commitment="b" * 64,
            type_fields={
                "poses": [{
                    "id": "custom:0123456789abcdef",
                    "label": private_type_label,
                    "custom": True,
                    "group": "poses",
                }],
            },
            detail_callouts=[{
                "custom_id": "custom:abcdef0123456789",
                "label": private_callout_label,
                "kind": "custom",
                "operation": "enhance",
                "source_role": "canonical_identity",
            }],
        )
        callback_requests = []
        review_requests = []

        def generate(request):
            callback_requests.append(request)
            self.assertEqual(request.authored_contract.style, private_style)
            return self._image(
                f"authored-{request.index}.png", (21, 22, 23), plan.sheet_size,
            )

        def edit(_primary, _canonical, request):
            callback_requests.append(request)
            return self._image(
                f"authored-{request.index}-{len(callback_requests)}.png",
                (request.index + 30, 31, 32), plan.sheet_size,
            )

        def reviewer(request):
            review_requests.append(request)
            contract = request.authored_contract
            self.assertRegex(contract.contract_seal, r"^[0-9a-f]{64}$")
            if request.item_id in {"style_language", "materials_palette"}:
                return contract.style == private_style
            self.assertIsNone(contract.style)
            if request.item_id in {
                "identity_anchor", "structural_proportions", "authored_details",
                "anatomy_callouts", "pose_view", "cross_sheet_continuity",
            } and request.target_role in plan.sheet_roles:
                return any(
                    item.label == private_type_label
                    for _field, items in contract.type_fields for item in items
                )
            if request.item_id == "authored_callouts":
                return (
                    len(contract.detail_callouts) == 1
                    and contract.detail_callouts[0].label == private_callout_label
                )
            self.assertEqual(contract.type_fields, ())
            self.assertEqual(contract.detail_callouts, ())
            return True

        result = create_reference_pack(
            plan,
            generate_sheet=generate,
            edit_sheet=edit,
            reviewer=reviewer,
        )
        self.assertEqual(result.review.status, "pass")
        by_role = {request.role: request for request in callback_requests}
        base_contract = by_role[plan.sheet_roles[1]].authored_contract
        self.assertEqual(base_contract.style, private_style)
        self.assertEqual(
            base_contract.type_fields[0][1][0].label, private_type_label,
        )
        self.assertEqual(base_contract.detail_callouts, ())
        detail_contract = by_role[plan.detail_callouts[0].target_role].authored_contract
        self.assertEqual(detail_contract.style, private_style)
        self.assertEqual(detail_contract.type_fields, ())
        self.assertEqual(
            detail_contract.detail_callouts[0].label, private_callout_label,
        )

        assessment = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", plan.output_roles, {
                "materials_palette": (plan.sheet_roles[1],),
            }),
            reference_type="character",
            allowed_roles=plan.output_roles,
        )
        brief = reference_sheets.build_fidelity_correction_brief(assessment)
        repair_request = reference_sheets._pack_repair_request(
            plan, plan.sheets[1], assessment, brief,
        )
        self.assertEqual(repair_request.authored_contract, base_contract)
        forged_item = replace(
            base_contract.type_fields[0][1][0], label="TAMPERED",
        )
        forged_contract = replace(
            base_contract,
            type_fields=((base_contract.type_fields[0][0], (forged_item,)),),
        )
        with self.assertRaisesRegex(ValueError, "authored request contract is invalid"):
            reference_sheets._validate_pack_authored_request_contract(
                forged_contract,
            )
        public = json.dumps(result.public_metadata())
        for secret in (private_style, private_type_label, private_callout_label):
            self.assertNotIn(secret, public)
        contract_field_names = " ".join(
            next(iter(review_requests)).authored_contract.__dataclass_fields__
        ).casefold()
        for category_field in (
            "mature", "violent", "safety", "permissibility", "classification",
        ):
            self.assertNotIn(category_field, contract_field_names)
        self.assertTrue(review_requests)

    def test_v2_legacy_type_field_is_one_lossless_item(self):
        authored = "Views: front, profile; Expressions: calm, tense"
        plan = self._pack_plan(
            depth="compact",
            type_fields={"poses": authored},
        )
        item = plan.private_authored_settings()["type_fields"]["poses"][0]
        self.assertEqual(item, {
            "id": "legacy:poses",
            "label": authored,
            "custom": True,
            "group": "legacy",
        })
        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: self._image(
                "legacy-pack.png", (1, 2, 3), size=plan.sheet_size,
            ),
            edit_sheet=lambda *_args: self.fail("compact pack must not edit"),
            reviewer=lambda _request: True,
        )
        self.assertEqual(result.plan.private_authored_settings()["type_fields"], {
            "poses": [item],
        })
        self.assertEqual(result.plan.authored_settings_seal, plan.authored_settings_seal)

    def test_v2_multiple_callouts_execute_as_independent_ordered_targets(self):
        callouts = [
            {
                "custom_id": "builtin:face",
                "label": "Face",
                "kind": "face",
                "operation": "enhance",
                "source_role": "turnaround",
            },
            {
                "custom_id": "custom:abcdef0123456789",
                "label": "PRIVATE_CUSTOM_DETAIL",
                "kind": "custom",
                "operation": "reconstruct",
                "source_role": "expressions",
            },
        ]
        plan = self._pack_plan(detail_callouts=callouts)
        anchor = self._image("multi-callout-anchor.png", (2, 3, 4))
        produced = {}
        edit_calls = []

        def edit(primary, canonical, request):
            edit_calls.append((request, primary, canonical))
            path = self._image(
                f"multi-callout-{request.index}.png",
                (request.index + 20, 30, 40),
            )
            produced[request.role] = path
            return path

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=edit,
            reviewer=lambda _request: True,
        )
        self.assertEqual(tuple(item.role for item in result.artifacts), plan.output_roles)
        detail_calls = [call for call in edit_calls if call[0].detail_custom_id]
        self.assertEqual(
            [call[0].detail_custom_id for call in detail_calls],
            ["builtin:face", "custom:abcdef0123456789"],
        )
        self.assertEqual(detail_calls[0][1], produced["turnaround"].resolve())
        self.assertEqual(detail_calls[1][1], produced["expressions"].resolve())
        self.assertTrue(all(call[2] == anchor.resolve() for call in detail_calls))
        public = json.dumps(result.public_metadata())
        self.assertNotIn("PRIVATE_CUSTOM_DETAIL", public)
        self.assertIn("custom:abcdef0123456789", public)

        forged_callout = replace(
            plan.detail_callouts[1], label="TAMPERED_PRIVATE_LABEL",
        )
        forged = replace(
            plan,
            detail_callouts=(plan.detail_callouts[0], forged_callout),
        )
        with self.assertRaisesRegex(ValueError, "unsupported reference-pack plan"):
            create_reference_pack(
                forged,
                generate_sheet=lambda _request: self.fail("must not execute"),
                edit_sheet=lambda *_args: self.fail("must not execute"),
            )

    def test_v2_explicit_callout_source_and_mode_constraints_fail_closed(self):
        forged_source = {
            "custom_id": "builtin:face", "label": "Face", "kind": "face",
            "operation": "auto", "source_role": "identity_details",
        }
        with self.assertRaisesRegex(ValueError, "source"):
            self._pack_plan(detail_callouts=[forged_source])
        with self.assertRaisesRegex(ValueError, "draft packs"):
            self._pack_plan(
                mode="draft",
                editor_model=None,
                detail_callouts=[{"kind": "face", "operation": "auto"}],
            )
        with self.assertRaisesRegex(ValueError, "exact_spec"):
            self._pack_plan(
                intent="exact_spec",
                detail_callouts=[{
                    **forged_source,
                    "source_role": "canonical_identity",
                    "operation": "reconstruct",
                }],
            )

    def test_character_profile_enums_age_bounds_and_no_inference(self):
        for gender in ("woman", "man", "non_binary", "unspecified"):
            with self.subTest(gender=gender):
                profile = reference_sheets.normalize_character_profile({
                    "gender": gender,
                })
                self.assertEqual(profile.gender, gender)
                self.assertIsNone(profile.age)
                self.assertEqual(profile.explicit_anatomy, ())
                self.assertNotIn("adult", json.dumps(
                    profile.private_metadata(),
                ).casefold())

        for invalid in ("Woman", "female", "non-binary", "", None, 1):
            with self.subTest(invalid_gender=invalid):
                with self.assertRaisesRegex(ValueError, "gender"):
                    reference_sheets.normalize_character_profile({
                        "gender": invalid,
                    })
        for valid_age in (0, 17, 18, 999):
            with self.subTest(valid_age=valid_age):
                self.assertEqual(
                    reference_sheets.normalize_character_profile({
                        "age": valid_age,
                    }).age,
                    valid_age,
                )
        for invalid_age in (-1, 1000, True, 18.0, "18"):
            with self.subTest(invalid_age=invalid_age):
                with self.assertRaisesRegex(ValueError, "age"):
                    reference_sheets.normalize_character_profile({
                        "age": invalid_age,
                    })
        with self.assertRaisesRegex(ValueError, "at least 18"):
            reference_sheets.normalize_character_profile(
                {"age": 17, "explicit_anatomy": ["vulva"]},
                explicit_convenience=True,
            )
        omitted = reference_sheets.normalize_character_profile(
            {"explicit_anatomy": ["penis"]},
            explicit_convenience=True,
        )
        self.assertIsNone(omitted.age)
        self.assertNotIn("adult", json.dumps(
            omitted.private_metadata(),
        ).casefold())

        ordered = reference_sheets.normalize_character_profile({
            "explicit_anatomy": ["penis", "breasts", "vulva"],
        })
        self.assertEqual(
            ordered.explicit_anatomy, ("breasts", "vulva", "penis"),
        )
        for invalid_anatomy in (
            ["breasts", "breasts"], ["uterus"], "vulva", [1],
        ):
            with self.subTest(invalid_anatomy=invalid_anatomy):
                with self.assertRaisesRegex(ValueError, "explicit_anatomy"):
                    reference_sheets.normalize_character_profile({
                        "explicit_anatomy": invalid_anatomy,
                    })

    def test_character_profile_legacy_absence_and_type_scope(self):
        legacy = self._pack_plan()
        self.assertIsNone(legacy.character_profile)
        self.assertIsNone(legacy.managed_character_callouts)
        self.assertFalse(legacy.explicit_convenience)
        self.assertNotIn("character_profile", legacy.private_authored_settings())
        self.assertNotIn(
            "managed_character_callouts", legacy.private_authored_settings(),
        )
        self.assertNotIn(
            "character_profile", legacy.public_preview()["authored_settings"],
        )
        self.assertEqual(legacy.detail_callouts, ())

        for reference_type in ("location", "prop", "creature", "wardrobe", "world"):
            with self.subTest(reference_type=reference_type):
                with self.assertRaisesRegex(ValueError, "only for character"):
                    self._pack_plan(
                        reference_type=reference_type,
                        character_profile={"gender": "woman"},
                    )

    def test_character_explicit_convenience_derives_exact_private_callouts(self):
        plan = self._pack_plan(
            character_profile={
                "gender": "non_binary",
                "age": 23,
                "explicit_anatomy": ["penis", "breasts", "vulva"],
            },
            explicit_convenience=True,
        )
        self.assertEqual(
            [callout.label for callout in plan.detail_callouts],
            ["breasts (front)", "breasts (profile)", "vulva", "penis"],
        )
        self.assertEqual(
            [callout.custom_id for callout in plan.detail_callouts],
            [
                "custom:cpref00000001", "custom:cpref00000002",
                "custom:cpref00000003", "custom:cpref00000004",
            ],
        )
        self.assertEqual(
            [callout.source_role for callout in plan.detail_callouts],
            [
                "canonical_identity", "turnaround",
                "canonical_identity", "canonical_identity",
            ],
        )
        compact = self._pack_plan(
            depth="compact",
            character_profile={
                "gender": "unspecified",
                "explicit_anatomy": ["breasts", "vulva", "penis"],
            },
            explicit_convenience=True,
        )
        self.assertEqual(compact.sheet_roles, ("canonical_identity",))
        self.assertEqual(
            [callout.source_role for callout in compact.detail_callouts],
            ["canonical_identity"] * 4,
        )
        state = plan.managed_character_callouts
        self.assertIsNotNone(state)
        self.assertTrue(all(
            item.provenance == "character-profile-explicit-v1"
            and item.status == "active"
            for item in state.entries
        ))
        self.assertEqual(
            reference_sheets.reference_pack_authored_settings_seal(
                plan.private_authored_settings(),
            ),
            plan.authored_settings_seal,
        )

        preview = plan.public_preview()
        public_text = json.dumps(preview, sort_keys=True)
        for raw in (
            "non_binary", "breasts (front)", "breasts (profile)",
            '"vulva"', '"penis"', "cpref000000", "breasts_front",
        ):
            self.assertNotIn(raw, public_text)
        profile_public = preview["authored_settings"]["character_profile"]
        self.assertEqual(profile_public["explicit_anatomy"]["count"], 3)
        self.assertTrue(profile_public["gender"]["present"])
        self.assertTrue(profile_public["age"]["present"])
        self.assertTrue(all(
            re.fullmatch(r"[0-9a-f]{64}", item)
            for item in profile_public["explicit_anatomy"]["commitments"]
        ))
        managed_public = preview["authored_settings"][
            "managed_character_callouts"
        ]
        self.assertEqual(managed_public["active_count"], 4)
        self.assertEqual(managed_public["tombstone_count"], 0)
        self.assertEqual(managed_public["rename_count"], 0)
        self.assertEqual(
            preview["ordered_output_roles"][-4:],
            [
                "detail_callout:managed:1", "detail_callout:managed:2",
                "detail_callout:managed:3", "detail_callout:managed:4",
            ],
        )

        anchor = self._image(
            "managed-profile-anchor.png", (31, 41, 51), plan.sheet_size,
        )
        callback_contracts = []

        def edit(_primary, _anchor, request):
            callback_contracts.append(request.authored_contract)
            return self._image(
                f"managed-profile-{request.index}.png",
                (request.index + 60, 70, 80),
                plan.sheet_size,
            )

        result = create_reference_pack(
            plan,
            generate_sheet=lambda request: (
                callback_contracts.append(request.authored_contract) or anchor
            ),
            edit_sheet=edit,
            reviewer=lambda _request: True,
        )
        self.assertEqual(tuple(
            artifact.role for artifact in result.artifacts
        ), plan.output_roles)
        self.assertTrue(any(
            contract.character_facts is not None
            and contract.character_facts.explicit_anatomy
            for contract in callback_contracts
        ))
        public_artifacts = json.dumps([
            artifact.public_metadata() for artifact in result.artifacts
        ])
        for raw in (
            "breasts (front)", "breasts (profile)", '"vulva"', '"penis"',
        ):
            self.assertNotIn(raw, public_artifacts)

    def test_managed_convenience_crops_use_publication_safe_private_basenames(self):
        plan = self._pack_plan(
            depth="compact",
            character_profile={
                "gender": "unspecified",
                "explicit_anatomy": ["breasts", "vulva", "penis"],
            },
            explicit_convenience=True,
        )
        hidden_source = self._image(
            ".synthetic_private_anchor.png", (31, 41, 51), plan.sheet_size,
        )
        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: hidden_source,
            edit_sheet=lambda *_args: self.fail(
                "sufficient deterministic crops must not invoke the editor"
            ),
            reviewer=lambda _request: True,
        )
        managed = tuple(
            artifact for artifact in result.artifacts
            if artifact.detail_provenance is not None
            and artifact.detail_provenance.get("managed") is True
        )
        self.assertEqual(len(managed), 4)
        basenames = tuple(artifact.path.name for artifact in managed)
        self.assertEqual(len(set(basenames)), len(basenames))
        for artifact, basename in zip(managed, basenames):
            self.assertEqual(artifact.provenance.strategy, "deterministic_crop")
            self.assertEqual(project_assets._validate_basename(basename), basename)
            self.assertRegex(
                basename, r"^reference-detail-[0-9a-f]{24}\.png$",
            )
            self.assertNotIn(hidden_source.name, basename)
            self.assertTrue(artifact.path.is_file())
            self.assertEqual(
                artifact.path.with_suffix(".meta.json").read_text(
                    encoding="utf-8",
                ),
                "null\n",
            )
            self.assertIn(artifact.path, result.private_source_paths)
        sidecar_cache = search_index.load_media_sidecars(
            str(self.sources), set(basenames),
        )
        self.assertEqual(sidecar_cache, {})
        gallery_visible = []
        for basename in basenames:
            media_path = self.sources / basename
            sidecar_path = media_path.with_suffix(".meta.json")
            if sidecar_path.is_file() and sidecar_cache.get(basename) is None:
                continue
            gallery_visible.append(basename)
        self.assertEqual(gallery_visible, [])
        for basename in basenames:
            sidecar_path = (self.sources / basename).with_suffix(".meta.json")
            self.assertTrue(sidecar_path.is_file())
            self.assertIsNone(sidecar_cache.get(basename))
        ordinary_review_stage = reference_sheets._staging_path(
            self.outputs / "ordinary.png",
        )
        self.assertTrue(ordinary_review_stage.name.startswith("."))
        self.assertFalse(ordinary_review_stage.exists())
        self.assertEqual(
            tuple(seal.role for seal in result.review.artifact_seals),
            plan.output_roles,
        )
        public = json.dumps({
            "result": result.public_metadata(),
            "artifacts": [item.public_metadata() for item in managed],
        }, sort_keys=True)
        self.assertNotIn(hidden_source.name, public)
        for callout in plan.detail_callouts:
            self.assertNotIn(callout.label, public)
            self.assertNotIn(callout.custom_id, public)

    def test_character_draft_retains_private_profile_without_managed_callouts(self):
        plan = self._pack_plan(
            mode="draft",
            editor_model=None,
            character_profile={
                "gender": "non_binary",
                "age": 27,
                "explicit_anatomy": ["breasts", "vulva", "penis"],
            },
            explicit_convenience=True,
        )
        self.assertTrue(plan.explicit_convenience)
        self.assertEqual(plan.detail_callouts, ())
        self.assertIsNone(plan.managed_character_callouts)
        self.assertEqual(
            plan.character_profile.explicit_anatomy,
            ("breasts", "vulva", "penis"),
        )
        private = plan.private_authored_settings()
        self.assertEqual(
            private["character_profile"]["explicit_anatomy"],
            ["breasts", "vulva", "penis"],
        )
        self.assertNotIn("managed_character_callouts", private)
        replay = self._pack_plan(
            mode="draft",
            editor_model=None,
            character_profile=private["character_profile"],
            explicit_convenience=True,
        )
        self.assertEqual(replay, plan)

    def test_character_profile_role_scoped_planning_generation_and_review_facts(self):
        plan = self._pack_plan(
            depth="comprehensive",
            character_profile={
                "gender": "man", "age": 41,
                "explicit_anatomy": ["breasts", "penis"],
            },
            explicit_convenience=True,
        )
        canonical = reference_sheets.reference_pack_authored_contract(
            plan, target_role="canonical_identity",
        )
        self.assertEqual(canonical.character_facts.gender, "man")
        self.assertEqual(canonical.character_facts.age, 41)
        self.assertEqual(
            canonical.character_facts.explicit_anatomy,
            ("breasts", "penis"),
        )
        expression = reference_sheets.reference_pack_authored_contract(
            plan, target_role="expressions",
        )
        self.assertEqual(expression.character_facts.gender, "man")
        self.assertEqual(expression.character_facts.age, 41)
        self.assertEqual(expression.character_facts.explicit_anatomy, ())

        style_review = reference_sheets.reference_pack_authored_contract(
            plan, target_role="canonical_identity",
            rubric_item_id="style_language",
        )
        self.assertIsNone(style_review.character_facts)
        anatomy_review = reference_sheets.reference_pack_authored_contract(
            plan, target_role="canonical_identity",
            rubric_item_id="anatomy_callouts",
        )
        self.assertEqual(
            anatomy_review.character_facts.explicit_anatomy,
            ("breasts", "penis"),
        )
        callout_review = reference_sheets.reference_pack_authored_contract(
            plan, target_role=plan.detail_callouts[-1].target_role,
            rubric_item_id="authored_callouts",
        )
        self.assertEqual(
            callout_review.character_facts.explicit_anatomy, ("penis",),
        )
        self.assertEqual(callout_review.character_facts.age, 41)

        observed = []
        artifacts = self._pack_artifacts(plan, prefix="profile-contract")

        def reviewer(request):
            if request.item_id == "anatomy_callouts" and request.target_role in {
                "canonical_identity", "turnaround", "identity_details",
            }:
                observed.append(request.authored_contract.character_facts)
            return True

        result = reference_sheets.review_reference_pack(
            plan, artifacts, reviewer,
        )
        self.assertEqual(result.status, "pass")
        self.assertTrue(observed)
        self.assertTrue(all(facts.age == 41 for facts in observed))

    def test_character_managed_callout_removal_rename_and_retry_are_stable(self):
        first = self._pack_plan(
            character_profile={
                "gender": "woman", "explicit_anatomy": ["breasts", "vulva"],
            },
            explicit_convenience=True,
        )
        replay = self._pack_plan(
            character_profile=first.character_profile.private_metadata(),
            managed_character_callouts=(
                first.managed_character_callouts.private_metadata()
            ),
            detail_callouts=[
                item.private_metadata() for item in first.detail_callouts
            ],
            explicit_convenience=True,
        )
        self.assertEqual(replay, first)

        remaining = [
            item.private_metadata() for item in first.detail_callouts[1:]
        ]
        removed = self._pack_plan(
            character_profile=first.character_profile.private_metadata(),
            managed_character_callouts=(
                first.managed_character_callouts.private_metadata()
            ),
            detail_callouts=remaining,
            explicit_convenience=True,
        )
        self.assertNotIn(
            "breasts (front)", [item.label for item in removed.detail_callouts],
        )
        self.assertEqual(
            removed.managed_character_callouts.entries[0].status, "tombstoned",
        )
        removed_replay = self._pack_plan(
            character_profile=removed.character_profile.private_metadata(),
            managed_character_callouts=(
                removed.managed_character_callouts.private_metadata()
            ),
            detail_callouts=[
                item.private_metadata() for item in removed.detail_callouts
            ],
            explicit_convenience=True,
        )
        self.assertEqual(removed_replay, removed)

        renamed_wire = [
            item.private_metadata() for item in first.detail_callouts
        ]
        renamed_wire[0]["label"] = "owner preferred front detail"
        renamed = self._pack_plan(
            character_profile=first.character_profile.private_metadata(),
            managed_character_callouts=(
                first.managed_character_callouts.private_metadata()
            ),
            detail_callouts=renamed_wire,
            explicit_convenience=True,
        )
        self.assertEqual(
            renamed.detail_callouts[0].label, "owner preferred front detail",
        )
        self.assertTrue(renamed.managed_character_callouts.entries[0].renamed)
        renamed_replay = self._pack_plan(
            character_profile=renamed.character_profile.private_metadata(),
            managed_character_callouts=(
                renamed.managed_character_callouts.private_metadata()
            ),
            detail_callouts=[
                item.private_metadata() for item in renamed.detail_callouts
            ],
            explicit_convenience=True,
        )
        self.assertEqual(renamed_replay, renamed)

    def test_character_profile_and_managed_state_are_sealed_against_forgery(self):
        plan = self._pack_plan(
            character_profile={
                "gender": "woman", "age": 22,
                "explicit_anatomy": ["vulva"],
            },
            explicit_convenience=True,
        )
        forged_profile = replace(plan.character_profile, gender="man")
        with self.assertRaisesRegex(ValueError, "unsupported reference-pack plan"):
            reference_sheets._validate_reference_pack_plan(replace(
                plan, character_profile=forged_profile,
            ))
        forged_entry = replace(
            plan.managed_character_callouts.entries[0],
            label="forged label", renamed=False,
        )
        forged_state = replace(
            plan.managed_character_callouts,
            entries=(forged_entry,),
        )
        with self.assertRaisesRegex(ValueError, "unsupported reference-pack plan"):
            reference_sheets._validate_reference_pack_plan(replace(
                plan, managed_character_callouts=forged_state,
            ))
        reserved = {
            "custom_id": "custom:cpref00000001",
            "label": "collision",
            "kind": "custom",
            "operation": "auto",
            "source_role": "canonical_identity",
        }
        with self.assertRaisesRegex(ValueError, "reserved managed"):
            self._pack_plan(detail_callouts=[reserved])
        noncanonical = copy.deepcopy(plan.private_authored_settings())
        noncanonical["character_profile"]["explicit_anatomy"] = [
            "penis", "vulva",
        ]
        with self.assertRaisesRegex(ValueError, "private authored settings"):
            reference_sheets.reference_pack_authored_settings_seal(
                noncanonical,
            )

    def test_character_managed_public_tree_masks_roles_ids_labels_and_unkeyed_hashes(self):
        plan = self._pack_plan(
            character_profile={
                "gender": "woman", "age": 29,
                "explicit_anatomy": ["breasts", "vulva", "penis"],
            },
            explicit_convenience=True,
        )
        managed_role = plan.detail_callouts[2].target_role
        assessment = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", plan.output_roles, {
                "authored_callouts": (managed_role,),
            }),
            reference_type="character",
            allowed_roles=plan.output_roles,
        )
        review = reference_sheets.SemanticReviewResult(
            status="fail",
            checks=tuple(assessment.dimension_checks_dict().items()),
            failed_roles=assessment.failed_roles,
            reason_codes=assessment.reason_codes,
            fidelity_assessment=assessment,
            fidelity_accepted=False,
            fidelity_attempt_index=0,
        )
        artifacts = list(self._pack_artifacts(plan, prefix="public-canary"))
        managed_index = plan.output_roles.index(managed_role)
        callout = plan.detail_callouts[2]
        private_request = reference_sheets._pack_generation_request(
            plan,
            reference_sheets.PackSheetRecipe(
                callout.target_role, callout.label,
                f"authored detail target: {callout.label}",
            ),
            managed_index,
            strategy="detail_callout",
            routing_operation="callout",
            source_role=callout.source_role,
            source_digest="a" * 64,
            operation="enhance",
            normalized_crop=(0.0, 0.0, 1.0, 1.0),
            callout=callout,
        )
        artifacts[managed_index] = replace(
            artifacts[managed_index],
            detail_provenance={
                "managed": True,
                "custom_id": callout.custom_id,
                "kind": callout.kind,
                "source_role": callout.source_role,
                "source_digest": "a" * 64,
                "normalized_crop": [0.0, 0.0, 1.0, 1.0],
                "requested_operation": callout.requested_operation,
                "resolved_operation": "enhance",
                "editor_model": plan.editor_model,
                "label_digest": callout.label_digest,
                "seal": private_request.detail_seal,
                "commitment": plan.character_profile.commitment(
                    "managed_artifact", callout.private_metadata(),
                ),
                "commitment_kind": "nonce_bound_v1",
            },
        )
        result = reference_sheets.ReferencePackResult(
            plan=plan,
            artifacts=tuple(artifacts),
            review=review,
            repaired_roles=(managed_role,),
            max_repair_attempts=1,
            repair_attempts_used=1,
            private_source_paths=(),
        )
        brief = reference_sheets.build_fidelity_correction_brief(assessment)
        nested = {
            "preview": plan.public_preview(),
            "result": result.public_metadata(),
            "artifacts": [item.public_metadata() for item in artifacts],
            "assessment": assessment.public_metadata(),
            "correction": brief.public_metadata(),
        }
        public_text = json.dumps(nested, sort_keys=True)
        self.assertIn("detail_callout:managed", public_text)
        self.assertIsNone(nested["correction"]["commitment"])
        canaries = {
            *(item.custom_id for item in plan.detail_callouts),
            *(item.label for item in plan.detail_callouts),
            *(item.label_digest for item in plan.detail_callouts),
            *(item.key for item in plan.managed_character_callouts.entries),
            plan.managed_character_callouts.state_seal,
            plan.character_profile.profile_seal,
            private_request.detail_seal,
        }
        for canary in canaries:
            with self.subTest(canary=canary):
                self.assertNotIn(canary, public_text)
        managed_artifact = nested["artifacts"][managed_index]
        self.assertEqual(managed_artifact["role"], "detail_callout:managed")
        self.assertNotIn("seal", managed_artifact["detail"])
        self.assertNotIn("label_digest", managed_artifact["detail"])
        self.assertNotIn("custom_id", managed_artifact["detail"])
        self.assertRegex(
            managed_artifact["detail"]["commitment"], r"^[0-9a-f]{64}$",
        )

    def test_v2_public_metadata_is_prompt_path_and_review_text_free(self):
        secret = "PRIVATE PACK REQUEST"
        plan = self._pack_plan(creative_request=secret)
        anchor = self._image("private-pack-anchor.png", (1, 2, 3))

        def edit(_primary, _anchor, request):
            return self._image(
                f"private-pack-{request.index}.png", (request.index, 4, 5),
            )

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=edit,
            reviewer=lambda _request: True,
        )
        public = json.dumps({
            "pack": result.public_metadata(),
            "artifacts": [item.public_metadata() for item in result.artifacts],
        })
        self.assertNotIn(secret, public)
        self.assertNotIn(str(self.root), public)
        self.assertNotIn("creative_request", public)

    def test_hybrid_generates_anchor_then_targeted_edits_in_order(self):
        plan = self._plan(mode="hybrid")
        calls = []
        anchor = self._image("anchor.png", (80, 90, 100))
        anchor_digest = self._digest(anchor)

        def generate(request):
            calls.append(("generate", request.role, request.strategy, None))
            return anchor

        def edit(anchor_path, request):
            calls.append(("edit", request.role, request.strategy, anchor_path))
            return self._image(
                f"edit-{request.index}.png",
                (request.index * 35, request.index * 27, request.index * 19),
            )

        result = create_reference_sheet(
            plan,
            self.outputs / "hybrid.png",
            generate_panel=generate,
            edit_panel=edit,
            reviewer=lambda _request: self._pass_review(),
            editor_model="local/editor-model",
        )
        self.assertEqual(calls[0], ("generate", plan.panel_roles[0], "identity_anchor", None))
        self.assertEqual(
            [(kind, role, strategy) for kind, role, strategy, _ in calls[1:]],
            [("edit", role, "targeted_edit") for role in plan.panel_roles[1:]],
        )
        self.assertTrue(all(call[3] == anchor.resolve() for call in calls[1:]))
        self.assertEqual(self._digest(anchor), anchor_digest)
        self.assertEqual(result.artifacts[0].provenance.strategy, "anchor_edit")
        self.assertEqual(result.artifacts[0].model, plan.model)
        self.assertTrue(all(
            artifact.model == "local/editor-model"
            for artifact in result.artifacts[1:-1]
        ))

    def test_hybrid_rejects_edit_reusing_anchor_output(self):
        plan = self._plan(mode="hybrid")
        anchor = self._image("anchor.png", (80, 90, 100))
        with self.assertRaisesRegex(ReferenceSheetStructureError, "generated_output_not_new"):
            create_reference_sheet(
                plan,
                self.outputs / "hybrid.png",
                generate_panel=lambda _request: anchor,
                edit_panel=lambda anchor_path, _request: anchor_path,
            )
        self.assertFalse((self.outputs / "hybrid.png").exists())

    def test_draft_is_exactly_one_generation_and_palette_is_embedded_only(self):
        plan = self._plan(mode="draft")
        draft = self._image("draft.png", (17, 83, 149), plan.draft_size)
        original = self._digest(draft)
        calls = []

        def generate(request):
            calls.append(request)
            return draft

        result = create_reference_sheet(
            plan,
            self.outputs / "draft-sheet.png",
            generate_draft=generate,
            generate_panel=lambda _request: self.fail("draft must not generate panels"),
            reviewer=lambda _request: self._pass_review(),
        )
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].palette_embedded)
        self.assertEqual(calls[0].panel_roles, plan.panel_roles)
        self.assertEqual(
            [artifact.role for artifact in result.artifacts],
            [*plan.panel_roles, "sheet"],
        )
        self.assertTrue(all(artifact.path is None for artifact in result.artifacts[:-1]))
        self.assertEqual(result.geometry.canvas_size, (240, 252))
        self.assertEqual(result.geometry.palette_box, (0, 180, 240, 252))
        self.assertEqual(self._digest(draft), original)
        with Image.open(result.sheet_path) as sheet:
            self.assertEqual(sheet.crop((0, 0, 240, 180)).getpixel((0, 0)), (17, 83, 149))
            self.assertNotEqual(sheet.getpixel((10, 190)), (17, 83, 149))

    def test_draft_rejects_wrong_dimensions_without_creating_final(self):
        plan = self._plan(mode="draft")
        with self.assertRaisesRegex(ReferenceSheetStructureError, "draft_dimensions_invalid"):
            create_reference_sheet(
                plan,
                self.outputs / "bad-draft.png",
                generate_draft=lambda _request: self._image("small.png", (1, 2, 3)),
            )
        self.assertFalse((self.outputs / "bad-draft.png").exists())

    def test_v2_rubric_questions_are_isolated_boolean_only_and_history_free(self):
        roles = ("turnaround", "detail_callout:face")
        paths = (self.sources / "one.png", self.sources / "two.png")
        first = reference_sheets.build_fidelity_rubric_question(
            item_id="style_language",
            reference_type="character",
            creative_request="PRIVATE AUTHOR REQUEST",
            sheet_paths=paths,
            sheet_roles=roles,
            target_role=roles[0],
        )
        second = reference_sheets.build_fidelity_rubric_question(
            item_id="authored_callouts",
            reference_type="character",
            creative_request="PRIVATE AUTHOR REQUEST",
            sheet_paths=paths,
            sheet_roles=roles,
            target_role=roles[1],
        )
        self.assertEqual(first.response_schema, {"type": "boolean"})
        schema = second.response_schema
        self.assertIsInstance(schema, dict)
        self.assertEqual(tuple(schema), ("type",))
        self.assertEqual(tuple(schema.keys()), ("type",))
        self.assertEqual(tuple(schema.items()), (("type", "boolean"),))
        self.assertEqual(tuple(schema.values()), ("boolean",))
        self.assertEqual(schema["type"], "boolean")
        self.assertIsNone(schema.get("properties"))
        with self.assertRaises(KeyError):
            _ = schema["properties"]
        self.assertEqual(json.dumps(schema), '{"type": "boolean"}')
        self.assertIs(copy.copy(schema), schema)
        self.assertIs(copy.deepcopy(schema), schema)
        with self.assertRaisesRegex(TypeError, "immutable"):
            schema["type"] = "object"
        with self.assertRaisesRegex(TypeError, "immutable"):
            schema.update({"properties": {}})
        with self.assertRaisesRegex(TypeError, "immutable"):
            schema.setdefault("properties", {})
        with self.assertRaisesRegex(TypeError, "immutable"):
            schema.pop("type")
        with self.assertRaisesRegex(TypeError, "immutable"):
            schema.clear()
        forged_schema = replace(
            second,
            response_schema={
                "type": "boolean",
                "properties": {"checks": {"required": ["violent_register_fidelity"]}},
            },
        )
        with self.assertRaisesRegex(ValueError, "rubric question is invalid"):
            reference_sheets.record_fidelity_rubric_answer(
                forged_schema, True,
            )
        self.assertNotEqual(first.question, second.question)
        self.assertNotIn(first.question, second.instruction)
        self.assertNotIn(first.question, second.question)
        self.assertNotIn("attempt", second.instruction.casefold())
        self.assertNotIn("tolerance", second.instruction.casefold())
        for forbidden_field in ("history", "messages", "prior_answer", "grade"):
            self.assertNotIn(forbidden_field, second.__dataclass_fields__)
        for value, expected in ((True, True), (False, False), ("true", True)):
            with self.subTest(value=value):
                self.assertIs(
                    reference_sheets.parse_fidelity_rubric_answer(value), expected,
                )
        bound = reference_sheets.record_fidelity_rubric_answer(second, False)
        self.assertEqual(bound.item_id, "authored_callouts")
        self.assertEqual(bound.outcome, "fail")
        self.assertEqual(bound.affected_roles, (roles[1],))
        self.assertEqual(bound.reviewed_role, roles[1])
        for invalid in (
            1, "exact", "true\nreview prose", {"answer": True},
            {"grade": "minor_residual"}, self._pass_review(), None,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    reference_sheets.ReferenceSheetReviewError,
                    "review_unavailable",
                ):
                    reference_sheets.parse_fidelity_rubric_answer(invalid)

    def test_v2_mapping_answer_retries_once_without_legacy_fanout(self):
        plan = self._pack_plan(depth="custom", sheet_count=2)
        artifacts = self._pack_artifacts(plan, "mapping-rejected")
        requests = []

        def legacy_mapping(request):
            requests.append(request)
            return self._pass_review()

        result = reference_sheets.review_reference_pack(
            plan, artifacts, legacy_mapping,
        )
        self.assertEqual(len(requests), 2)
        self.assertIs(requests[0], requests[1])
        self.assertEqual(result.status, "review_unavailable")
        self.assertIsNone(result.fidelity_assessment)
        self.assertIsNone(result.fidelity_accepted)

    def test_v2_pack_review_exact_call_matrix_isolated_and_cache_equivalent(self):
        plan = self._pack_plan(depth="custom", sheet_count=2)
        artifacts = self._pack_artifacts(plan, "matrix")
        expected = tuple(
            (item_id, role)
            for item_id, roles in
            reference_sheets.fidelity_rubric_role_applicability(
                plan.reference_type, plan.output_roles,
            )
            for role in roles
        )
        direct_requests = []
        cached_requests = []

        def direct(request):
            direct_requests.append(request)
            return True

        cache = {pair: "true" for pair in expected}

        def cached(request):
            cached_requests.append(request)
            return cache[(request.item_id, request.target_role)]

        direct_result = reference_sheets.review_reference_pack(
            plan, artifacts, direct,
        )
        cached_result = reference_sheets.review_reference_pack(
            plan, artifacts, cached,
        )
        for requests in (direct_requests, cached_requests):
            self.assertEqual(
                tuple((item.item_id, item.target_role) for item in requests),
                expected,
            )
            self.assertEqual(len(requests), len(expected))
            for request in requests:
                self.assertEqual(request.response_schema, {"type": "boolean"})
                self.assertIn(request.target_role, request.question)
                for forbidden in (
                    "history", "messages", "prior_answer", "prior_question",
                ):
                    self.assertNotIn(forbidden, request.__dataclass_fields__)
        self.assertEqual(
            direct_result.fidelity_assessment,
            cached_result.fidelity_assessment,
        )
        self.assertTrue(direct_result.fidelity_accepted)
        self.assertEqual(direct_result.status, "pass")
        self.assertIsNotNone(direct_result.fidelity_assessment)

    def test_v2_pack_reviewer_retries_same_isolated_boolean_request(self):
        plan = self._pack_plan(depth="custom", sheet_count=2)
        artifacts = self._pack_artifacts(plan, "review-retry")
        calls = []

        def flaky(request):
            calls.append(request)
            if len(calls) % 2:
                raise RuntimeError("transient reviewer failure")
            return True

        result = reference_sheets.review_reference_pack(
            plan, artifacts, flaky,
        )
        expected_questions = sum(
            len(roles) for _item_id, roles in
            reference_sheets.fidelity_rubric_role_applicability(
                plan.reference_type, plan.output_roles,
            )
        )
        self.assertEqual(len(calls), expected_questions * 2)
        for first, second in zip(calls[::2], calls[1::2]):
            self.assertIs(first, second)
            for forbidden in (
                "history", "messages", "prior_answer", "prior_question",
            ):
                self.assertNotIn(forbidden, first.__dataclass_fields__)
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.fidelity_assessment.assessment_class, "exact")

    def test_v2_pack_reviewer_exhausted_malformed_or_exception_is_ungraded(self):
        plan = self._pack_plan(depth="custom", sheet_count=2)
        for label, reviewer in (
            ("malformed", lambda _request: {"answer": True}),
            (
                "exception",
                lambda _request: (_ for _ in ()).throw(
                    RuntimeError("reviewer unavailable")
                ),
            ),
        ):
            with self.subTest(label=label):
                calls = []

                def counted(request):
                    calls.append(request)
                    return reviewer(request)

                result = reference_sheets.review_reference_pack(
                    plan,
                    self._pack_artifacts(plan, f"ungraded-{label}"),
                    counted,
                )
                self.assertEqual(
                    len(calls),
                    reference_sheets.FIDELITY_QUESTION_REVIEW_ATTEMPTS,
                )
                self.assertIs(calls[0], calls[1])
                self.assertEqual(result.status, "review_unavailable")
                self.assertIsNone(result.fidelity_assessment)
                self.assertIsNone(result.fidelity_accepted)
                self.assertEqual(result.reason_codes, ("review_unavailable",))

    def test_v2_unavailable_review_returns_valid_pack_for_deferred_review(self):
        plan = self._pack_plan(depth="custom", sheet_count=2)
        anchor = self._image("deferred-anchor.png", (20, 30, 40))
        derivative = self._image("deferred-derivative.png", (50, 60, 70))
        repair_calls = []

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=lambda *_args: derivative,
            reviewer=lambda _request: {"grade": "exact"},
            repair_sheet=lambda *_args: repair_calls.append(True),
            max_repair_attempts=5,
        )
        self.assertEqual(tuple(item.path for item in result.artifacts), (
            anchor.resolve(), derivative.resolve(),
        ))
        self.assertEqual(result.review.status, "review_unavailable")
        self.assertIsNone(result.review.fidelity_assessment)
        self.assertEqual(repair_calls, [])
        self.assertEqual(len(result.attempt_history), 1)
        self.assertTrue(result.publication_eligible)
        public = result.public_metadata()
        self.assertEqual(public["publication_status"], "ready")
        self.assertTrue(public["publication_eligible"])
        self.assertEqual(
            public["review"]["attempt_history"][0]["review_outcome"],
            "review_unavailable",
        )

    def test_v2_corrected_pack_with_unavailable_rereview_stays_ungraded(self):
        plan = self._pack_plan(depth="custom", sheet_count=2)
        anchor = self._image("rereview-anchor.png", (20, 30, 40))
        derivative = self._image("rereview-derivative.png", (50, 60, 70))
        repaired = self._image("rereview-repaired.png", (80, 90, 100))
        question_count = sum(
            len(roles) for _item_id, roles in
            reference_sheets.fidelity_rubric_role_applicability(
                plan.reference_type, plan.output_roles,
            )
        )
        calls = []

        def reviewer(request):
            calls.append(request)
            if len(calls) <= question_count:
                return not (
                    request.item_id == "materials_palette"
                    and request.target_role == plan.output_roles[1]
                )
            return {"grade": "exact"}

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=lambda *_args: derivative,
            reviewer=reviewer,
            repair_sheet=lambda *_args: repaired,
            max_repair_attempts=1,
        )
        self.assertEqual(
            [item.review.status for item in result.attempt_history],
            ["fail", "review_unavailable"],
        )
        self.assertEqual(result.selected_attempt_index, 1)
        self.assertEqual(result.artifacts[1].path, repaired.resolve())
        self.assertEqual(result.review.status, "review_unavailable")
        self.assertIsNone(result.review.fidelity_assessment)
        self.assertIsNone(result.final_correction_brief)
        self.assertTrue(derivative.is_file())
        self.assertTrue(repaired.is_file())
        public = result.public_metadata()
        self.assertEqual(public["publication_status"], "ready")
        self.assertEqual(public["review"]["selected_attempt_index"], 1)
        self.assertEqual(
            public["review"]["attempt_history"][1]["review_outcome"],
            "review_unavailable",
        )
        self.assertTrue(public["review"]["attempt_history"][1]["selected"])

    def test_v2_create_pack_strict_attempt_zero_records_tolerated_minor_retry(self):
        plan = self._pack_plan(depth="custom", sheet_count=2)
        anchor = self._image("strict-anchor.png", (20, 30, 40))
        derivative = self._image("strict-derivative.png", (50, 60, 70))
        repaired = self._image("strict-repaired.png", (80, 90, 100))
        calls = []
        repairs = []

        def reviewer(request):
            calls.append((request.item_id, request.target_role))
            return not (
                request.item_id == "materials_palette"
                and request.target_role == plan.output_roles[1]
            )

        def repair(primary, canonical, request):
            repairs.append((primary, canonical, request))
            return repaired

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=lambda *_args: derivative,
            reviewer=reviewer,
            repair_sheet=repair,
            max_repair_attempts=1,
        )
        calls_per_attempt = sum(
            len(roles) for _item_id, roles in
            reference_sheets.fidelity_rubric_role_applicability(
                plan.reference_type, plan.output_roles,
            )
        )
        self.assertEqual(len(calls), calls_per_attempt * 2)
        self.assertEqual(len(repairs), 1)
        repair_request = repairs[0][2]
        self.assertRegex(
            repair_request.correction_brief_commitment, r"^[0-9a-f]{64}$",
        )
        self.assertIn(
            repair_request.correction_brief, repair_request.objective,
        )
        self.assertEqual(result.repaired_roles, (plan.output_roles[1],))
        # A retry that meets its versioned tolerance target ranks ahead of an
        # attempt that did not meet the target, even at the same rubric score.
        self.assertEqual(result.selected_attempt_index, 1)
        self.assertEqual(result.review.status, "pass")
        self.assertTrue(result.review.fidelity_accepted)
        self.assertEqual(result.review.fidelity_attempt_index, 1)
        self.assertEqual(len(result.attempt_history), 2)
        self.assertTrue(result.attempt_history[1].review.fidelity_accepted)
        self.assertEqual(
            result.review.fidelity_assessment.worst_severity,
            "minor_residual",
        )
        self.assertEqual(result.review.fidelity_assessment.status, "fail")
        public_review = result.public_metadata()["review"]
        self.assertTrue(public_review["accepted"])
        self.assertEqual(public_review["attempt_index"], 1)
        self.assertTrue(public_review["publication_eligible"])
        self.assertEqual(public_review["selected_attempt_index"], 1)
        self.assertTrue(public_review["attempt_history"][1]["selected"])
        self.assertEqual(
            public_review["assessment"]["assessment_class"],
            "minor_residual",
        )

    def test_v2_worse_repair_keeps_earlier_best_artifacts(self):
        plan = self._pack_plan(depth="custom", sheet_count=2)
        anchor = self._image("best-anchor.png", (20, 30, 40))
        derivative = self._image("best-derivative.png", (50, 60, 70))
        repaired = self._image("worse-repair.png", (80, 90, 100))
        question_count = sum(
            len(roles) for _item_id, roles in
            reference_sheets.fidelity_rubric_role_applicability(
                plan.reference_type, plan.output_roles,
            )
        )
        calls = []

        def reviewer(request):
            attempt = len(calls) // question_count
            calls.append(request)
            if attempt == 0:
                return not (
                    request.item_id == "materials_palette"
                    and request.target_role == plan.output_roles[1]
                )
            return not (
                request.target_role == plan.output_roles[1]
                and request.item_id in {
                    "style_language", "materials_palette", "identity_anchor",
                }
            )

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=lambda *_args: derivative,
            reviewer=reviewer,
            repair_sheet=lambda *_args: repaired,
            max_repair_attempts=1,
        )
        self.assertEqual(result.selected_attempt_index, 0)
        self.assertEqual(result.artifacts[1].path, derivative.resolve())
        self.assertEqual(len(result.attempt_history), 2)
        self.assertEqual(
            result.attempt_history[1].artifacts[1].path, repaired.resolve(),
        )
        self.assertLess(
            reference_sheets.reference_candidate_ranking_key(
                reference_sheets.ReferenceCandidateAssessment(
                    0, result.attempt_history[0].review.fidelity_assessment, 0,
                ),
            ),
            reference_sheets.reference_candidate_ranking_key(
                reference_sheets.ReferenceCandidateAssessment(
                    1, result.attempt_history[1].review.fidelity_assessment, 1,
                ),
            ),
        )

    def test_v2_exhausted_residual_returns_improved_best_with_correction(self):
        plan = self._pack_plan(depth="custom", sheet_count=2)
        anchor = self._image("residual-anchor.png", (20, 30, 40))
        derivative = self._image("residual-derivative.png", (50, 60, 70))
        repaired = self._image("residual-repair.png", (80, 90, 100))
        question_count = sum(
            len(roles) for _item_id, roles in
            reference_sheets.fidelity_rubric_role_applicability(
                plan.reference_type, plan.output_roles,
            )
        )
        calls = []
        initial_failures = {
            "style_language", "materials_palette", "identity_anchor",
            "structural_proportions", "authored_details",
        }
        improved_failures = {
            "style_language", "materials_palette", "identity_anchor",
        }

        def reviewer(request):
            attempt = len(calls) // question_count
            calls.append(request)
            failures = initial_failures if attempt == 0 else improved_failures
            return not (
                request.target_role == plan.output_roles[1]
                and request.item_id in failures
            )

        result = create_reference_pack(
            plan,
            generate_sheet=lambda _request: anchor,
            edit_sheet=lambda *_args: derivative,
            reviewer=reviewer,
            repair_sheet=lambda *_args: repaired,
            max_repair_attempts=1,
        )
        self.assertEqual(result.selected_attempt_index, 1)
        self.assertEqual(result.artifacts[1].path, repaired.resolve())
        self.assertFalse(result.review.fidelity_accepted)
        self.assertEqual(result.review.status, "fail")
        self.assertIsNotNone(result.final_correction_brief)
        self.assertRegex(
            result.final_correction_brief.commitment, r"^[0-9a-f]{64}$",
        )
        public = result.public_metadata()
        self.assertTrue(public["publication_eligible"])
        self.assertEqual(public["publication_status"], "ready")
        self.assertEqual(public["review"]["selected_attempt_index"], 1)
        self.assertEqual(
            public["review"]["final_correction"]["commitment"],
            result.final_correction_brief.commitment,
        )

    def test_v2_all_types_localize_roles_world_composition_and_neutral_path(self):
        prohibited = (
            "mature", "violent", "safety", "permissibility", "policy",
            "moderation", "refusal",
        )
        for reference_type in reference_sheets.PACK_REFERENCE_TYPES:
            with self.subTest(reference_type=reference_type):
                plan = self._pack_plan(
                    reference_type=reference_type,
                    depth="compact",
                    creative_request=(
                        "Preserve every authored intimate, injury, and material detail."
                    ),
                )
                artifacts = self._pack_artifacts(plan, f"type-{reference_type}")
                seen = []

                def reviewer(request):
                    seen.append(request)
                    return request.item_id != "authored_details"

                result = reference_sheets.review_reference_pack(
                    plan, artifacts, reviewer,
                )
                neutral_plan = self._pack_plan(
                    reference_type=reference_type,
                    depth="compact",
                    creative_request="Preserve every authored design detail.",
                )
                neutral_seen = []
                neutral_result = reference_sheets.review_reference_pack(
                    neutral_plan,
                    self._pack_artifacts(neutral_plan, f"neutral-{reference_type}"),
                    lambda request: neutral_seen.append(request) or True,
                )
                expected = tuple(
                    (item_id, role)
                    for item_id, roles in
                    reference_sheets.fidelity_rubric_role_applicability(
                        reference_type, plan.output_roles,
                    )
                    for role in roles
                )
                self.assertEqual(
                    tuple((item.item_id, item.target_role) for item in seen),
                    expected,
                )
                self.assertEqual(result.failed_roles, plan.output_roles)
                self.assertEqual(neutral_result.status, "pass")
                self.assertEqual(
                    tuple(
                        (
                            item.item_id, item.target_role, item.instruction,
                            item.question, item.authored_contract,
                            tuple(item.response_schema.items()),
                        )
                        for item in seen
                    ),
                    tuple(
                        (
                            item.item_id, item.target_role, item.instruction,
                            item.question, item.authored_contract,
                            tuple(item.response_schema.items()),
                        )
                        for item in neutral_seen
                    ),
                )
                active_contract = json.dumps([
                    {
                        "instruction": item.instruction,
                        "question": item.question,
                        "schema": item.response_schema,
                    }
                    for item in seen
                ]).casefold()
                for word in prohibited:
                    self.assertNotIn(word, active_contract)
                    self.assertNotIn(
                        word,
                        " ".join(seen[0].__dataclass_fields__).casefold(),
                    )
                if reference_type == "world":
                    self.assertIn(
                        ("pose_view", plan.output_roles[0]), expected,
                    )

    def test_v2_forged_assessment_and_brief_fail_at_consumers(self):
        roles = ("turnaround", "expressions")
        assessment = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "materials_palette": (roles[0],),
            }),
            reference_type="character",
            allowed_roles=roles,
        )
        forged_dimension = replace(
            assessment.dimensions[0], matched_weight=999,
        )
        forged = replace(
            assessment,
            dimensions=(forged_dimension, *assessment.dimensions[1:]),
        )
        consumers = (
            lambda: forged.public_metadata(),
            lambda: reference_sheets.fidelity_attempt_accepted(
                forged, attempt_index=1,
            ),
            lambda: reference_sheets.build_fidelity_correction_brief(forged),
            lambda: reference_sheets.reference_candidate_ranking_key(
                reference_sheets.ReferenceCandidateAssessment(0, forged, 0),
            ),
        )
        for consumer in consumers:
            with self.subTest(consumer=consumer):
                with self.assertRaisesRegex(ValueError, "assessment is invalid"):
                    consumer()
        brief = reference_sheets.build_fidelity_correction_brief(assessment)
        forged_brief = replace(brief, commitment="0" * 64)
        with self.assertRaisesRegex(ValueError, "correction brief is invalid"):
            forged_brief.public_metadata()
        with self.assertRaisesRegex(ValueError, "correction brief is invalid"):
            reference_sheets._validate_fidelity_correction_brief(
                assessment, forged_brief,
            )

    def test_v2_server_projection_is_weighted_and_deterministic(self):
        roles = ("composition_language",)
        observations = self._rubric_observations("world", roles, {
            "materials_palette": roles,
            "authored_details": roles,
        })
        first = reference_sheets.project_fidelity_assessment(
            observations, reference_type="world", allowed_roles=roles,
        )
        second = reference_sheets.project_fidelity_assessment(
            observations, reference_type="world", allowed_roles=roles,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.version, "fidelity_assessment_v2")
        self.assertEqual(first.rubric_version, "reference-fidelity-rubric-v1")
        by_dimension = {item.dimension: item for item in first.dimensions}
        self.assertEqual(
            tuple(by_dimension),
            ("style", "identity_structure", "details_register", "pose_view_continuity"),
        )
        self.assertEqual(by_dimension["style"].grade, "minor_residual")
        self.assertEqual(
            (by_dimension["style"].matched_weight, by_dimension["style"].applicable_weight),
            (3, 5),
        )
        self.assertEqual(by_dimension["details_register"].grade, "material_residual")
        self.assertEqual(by_dimension["pose_view_continuity"].grade, "exact")
        public = first.public_metadata()
        self.assertEqual(public["status"], "fail")
        self.assertEqual(public["score_basis_points"], 7222)
        self.assertEqual(public["dimension_checks"], {
            "identity_structure": True,
            "pose_view_continuity": True,
            "details_register": False,
            "style": False,
        })
        self.assertEqual(public["failed_roles"], list(roles))
        self.assertEqual(public["reason_codes"], [
            "style_mismatch", "detail_register_mismatch",
        ])
        self.assertNotIn("PRIVATE AUTHOR REQUEST", json.dumps(public))
        self.assertNotIn("critique", json.dumps(public).casefold())

    def test_v2_projection_rejects_unavailable_or_forged_applicability(self):
        roles = ("composition_language",)
        observations = list(self._rubric_observations("world", roles))
        observations[0] = replace(
            observations[0], outcome="review_unavailable",
        )
        with self.assertRaisesRegex(
            reference_sheets.ReferenceSheetReviewError, "review_unavailable",
        ):
            reference_sheets.project_fidelity_assessment(
                observations, reference_type="world", allowed_roles=roles,
            )
        observations = list(self._rubric_observations("world", roles))
        observations.append(reference_sheets.FidelityRubricObservation(
            "authored_callouts", "pass", (), roles[0],
        ))
        with self.assertRaisesRegex(
            reference_sheets.ReferenceSheetReviewError, "review_unavailable",
        ):
            reference_sheets.project_fidelity_assessment(
                observations, reference_type="world", allowed_roles=roles,
            )

    def test_v2_retry_policy_is_separate_strict_then_bounded(self):
        roles = ("turnaround", "expressions")
        exact = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles),
            reference_type="character", allowed_roles=roles,
        )
        minor = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "materials_palette": (roles[0],),
            }),
            reference_type="character", allowed_roles=roles,
        )
        material = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "style_language": roles,
            }),
            reference_type="character", allowed_roles=roles,
        )
        self.assertTrue(reference_sheets.fidelity_attempt_accepted(
            exact, attempt_index=0,
        ))
        self.assertFalse(reference_sheets.fidelity_attempt_accepted(
            minor, attempt_index=0,
        ))
        self.assertTrue(reference_sheets.fidelity_attempt_accepted(
            minor, attempt_index=1,
        ))
        self.assertFalse(reference_sheets.fidelity_attempt_accepted(
            material, attempt_index=5,
        ))

        bounded_final = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "materials_palette": roles,
                "structural_proportions": roles,
            }),
            reference_type="character", allowed_roles=roles,
        )
        self.assertEqual(bounded_final.assessment_class, "material_residual")
        self.assertEqual(bounded_final.worst_severity, "minor_residual")
        self.assertEqual(bounded_final.score_basis_points, 7727)
        self.assertFalse(reference_sheets.fidelity_attempt_accepted(
            bounded_final, attempt_index=0,
        ))
        self.assertFalse(reference_sheets.fidelity_attempt_accepted(
            bounded_final, attempt_index=1,
        ))
        self.assertTrue(reference_sheets.fidelity_attempt_accepted(
            bounded_final, attempt_index=2,
        ))
        self.assertTrue(reference_sheets.fidelity_attempt_accepted(
            bounded_final, attempt_index=99,
        ))
        self.assertEqual(
            reference_sheets.FIDELITY_ATTEMPT_ACCEPTANCE_POLICY_VERSION,
            "reference-fidelity-attempt-acceptance-v2",
        )

        cumulative_material = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "materials_palette": roles,
                "structural_proportions": roles,
                "anatomy_callouts": roles,
                "cross_sheet_continuity": roles,
            }),
            reference_type="character", allowed_roles=roles,
        )
        self.assertEqual(cumulative_material.assessment_class, "material_residual")
        self.assertEqual(cumulative_material.worst_severity, "minor_residual")
        self.assertEqual(cumulative_material.score_basis_points, 5909)
        self.assertFalse(reference_sheets.fidelity_attempt_accepted(
            cumulative_material, attempt_index=1,
        ))
        self.assertFalse(reference_sheets.fidelity_attempt_accepted(
            cumulative_material, attempt_index=99,
        ))

    def test_v2_correction_brief_is_closed_committed_and_deterministic(self):
        roles = ("turnaround", "expressions")
        assessment = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "materials_palette": (roles[0],),
                "anatomy_callouts": (roles[1],),
            }),
            reference_type="character", allowed_roles=roles,
        )
        first = reference_sheets.build_fidelity_correction_brief(assessment)
        second = reference_sheets.build_fidelity_correction_brief(assessment)
        self.assertEqual(first, second)
        self.assertEqual(first.template_id, "reference-residual-correction")
        self.assertEqual(first.template_version, "v1")
        self.assertRegex(first.commitment, r"^[0-9a-f]{64}$")
        self.assertEqual(first.affected_roles, roles)
        self.assertIn("authored materials", first.rendered_brief)
        self.assertIn("authored anatomy", first.rendered_brief)
        self.assertNotIn("reviewer", first.rendered_brief.casefold())
        exact = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles),
            reference_type="character", allowed_roles=roles,
        )
        self.assertIsNone(reference_sheets.build_fidelity_correction_brief(exact))

    def test_v2_candidate_recommendation_ranking_and_ties_are_deterministic(self):
        roles = ("turnaround", "expressions")
        minor_one = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "materials_palette": (roles[0],),
            }),
            reference_type="character", allowed_roles=roles,
        )
        minor_two = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "materials_palette": (roles[0],),
                "anatomy_callouts": (roles[1],),
            }),
            reference_type="character", allowed_roles=roles,
        )
        exact = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles),
            reference_type="character", allowed_roles=roles,
        )
        candidates = (
            reference_sheets.ReferenceCandidateAssessment(8, minor_two, 0),
            reference_sheets.ReferenceCandidateAssessment(6, minor_one, 2),
            reference_sheets.ReferenceCandidateAssessment(7, minor_one, 1),
        )
        higher_score_key = reference_sheets.reference_candidate_ranking_key(
            candidates[2],
        )
        lower_score_key = reference_sheets.reference_candidate_ranking_key(
            candidates[0],
        )
        self.assertLess(higher_score_key[0], lower_score_key[0])
        self.assertLess(higher_score_key[3], lower_score_key[3])
        self.assertEqual(len(higher_score_key), 7)
        self.assertEqual(
            reference_sheets.recommend_reference_candidate(tuple(reversed(candidates))).candidate_index,
            7,
        )
        exact_candidates = (
            reference_sheets.ReferenceCandidateAssessment(4, exact, 1),
            reference_sheets.ReferenceCandidateAssessment(2, exact, 1),
        )
        self.assertEqual(
            reference_sheets.recommend_reference_candidate(exact_candidates).candidate_index,
            2,
        )
        self.assertEqual(
            reference_sheets.recommend_reference_candidate(
                (*candidates, exact_candidates[0]),
            ).candidate_index,
            4,
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            reference_sheets.recommend_reference_candidate((
                candidates[0],
                reference_sheets.ReferenceCandidateAssessment(
                    candidates[0].candidate_index, minor_one, 0,
                ),
            ))

    def test_v2_candidate_ranking_prefers_bounded_eligible_over_material_dimension(self):
        roles = ("turnaround", "expressions")
        material_dimension = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "style_language": (roles[0],),
            }),
            reference_type="character", allowed_roles=roles,
        )
        bounded_eligible = reference_sheets.project_fidelity_assessment(
            self._rubric_observations("character", roles, {
                "materials_palette": roles,
                "structural_proportions": roles,
            }),
            reference_type="character", allowed_roles=roles,
        )
        self.assertEqual(material_dimension.assessment_class, "minor_residual")
        self.assertEqual(material_dimension.worst_severity, "material_residual")
        self.assertFalse(reference_sheets.fidelity_attempt_accepted(
            material_dimension, attempt_index=2,
        ))
        self.assertEqual(bounded_eligible.assessment_class, "material_residual")
        self.assertEqual(bounded_eligible.worst_severity, "minor_residual")
        self.assertTrue(reference_sheets.fidelity_attempt_accepted(
            bounded_eligible, attempt_index=2,
        ))
        candidates = (
            reference_sheets.ReferenceCandidateAssessment(
                0, material_dimension, 2,
            ),
            reference_sheets.ReferenceCandidateAssessment(
                1, bounded_eligible, 2,
            ),
        )
        self.assertEqual(
            reference_sheets.recommend_reference_candidate(
                candidates,
            ).candidate_index,
            1,
        )

    def test_review_request_and_strict_parser_are_fidelity_only(self):
        plan = self._plan()
        request = build_semantic_review_request(plan, self.outputs / "sheet.png")
        self.assertIn("identity", request.instruction)
        self.assertIn("requested details", request.instruction)
        self.assertIn("view", request.instruction)
        self.assertIn("accessories", request.instruction)
        self.assertIn("style", request.instruction)
        active_instruction = request.instruction.casefold()
        for obsolete in (
            "moderation", "maturity", "mature", "violent", "safety",
            "policy", "permissibility", "classification",
        ):
            self.assertNotIn(obsolete, active_instruction)
        parsed = parse_semantic_review_result(
            json.dumps(self._pass_review()), allowed_roles=plan.panel_roles,
        )
        self.assertEqual(parsed.status, "pass")
        self.assertTrue(all(parsed.checks_dict().values()))
        reordered = self._pass_review()
        reordered["checks"] = dict(reversed(list(reordered["checks"].items())))
        self.assertEqual(
            parse_semantic_review_result(reordered, allowed_roles=plan.panel_roles).status,
            "pass",
        )

        invalid = self._pass_review()
        invalid["commentary"] = "free-form review text"
        with self.assertRaises(ReferenceSheetReviewError):
            parse_semantic_review_result(invalid, allowed_roles=plan.panel_roles)
        invalid = self._pass_review()
        invalid["reason_codes"] = ["policy_issue"]
        with self.assertRaises(ReferenceSheetReviewError):
            parse_semantic_review_result(invalid, allowed_roles=plan.panel_roles)

    def test_legacy_unrestricted_review_is_read_compatibility_only(self):
        off_plan = self._pack_plan(
            content_capability="unrestricted_local",
            review_contract="standard_fidelity_v1",
        )
        self.assertEqual(off_plan.review_contract, "standard_fidelity_v1")
        plan = self._pack_plan(
            depth="compact",
            content_capability="unrestricted_local",
            review_selection=PackIntelligenceSelection(
                "chosen-vlm", "chosen-vlm", "local", "a" * 64,
            ),
        )
        self.assertFalse(hasattr(
            reference_sheets, "build_reference_pack_review_request",
        ))
        checks = reference_sheets._UNRESTRICTED_CHECK_NAMES
        self.assertEqual(checks[-4:], (
            "overall_fidelity",
            "mature_register_fidelity",
            "violent_register_fidelity",
            "detail_register_fidelity",
        ))
        payload = {
            "status": "fail",
            "checks": {name: name != "detail_register_fidelity" for name in checks},
            "failed_roles": [plan.output_roles[0]],
            "reason_codes": ["detail_register_mismatch"],
        }
        parsed = parse_semantic_review_result(
            payload,
            allowed_roles=plan.output_roles,
            check_names=checks,
        )
        self.assertEqual(parsed.failed_roles, (plan.output_roles[0],))
        self.assertEqual(parsed.reason_codes, ("detail_register_mismatch",))
        payload["critique"] = "must never cross the strict contract"
        with self.assertRaises(ReferenceSheetReviewError):
            parse_semantic_review_result(
                payload,
                allowed_roles=plan.output_roles,
                check_names=checks,
            )
    def test_malformed_missing_or_throwing_review_is_review_unavailable(self):
        plan = self._plan()
        sheet = self._image("review.png", (2, 3, 4))
        for reviewer in (
            None,
            lambda _request: "not-json",
            lambda _request: {"status": "pass"},
            lambda _request: (_ for _ in ()).throw(RuntimeError("provider offline")),
        ):
            with self.subTest(reviewer=reviewer):
                result = review_reference_sheet(plan, sheet, reviewer)
                self.assertEqual(result.status, "review_unavailable")
                self.assertEqual(result.reason_codes, ("review_unavailable",))

    def test_review_failure_repairs_only_first_recipe_role_once_then_rereviews(self):
        plan = self._plan()
        generated = {}

        def generate(request):
            path = self._image(
                f"panel-{request.index}.png",
                (request.index * 21, request.index * 17, request.index * 13),
            )
            generated[request.role] = path
            return path

        reviews = [
            self._fail_review(plan.panel_roles[2], plan.panel_roles[0]),
            self._pass_review(),
        ]
        repair_calls = []
        original_digest = {}

        def repair(path, request):
            original_digest[request.role] = self._digest(path)
            repair_calls.append((path, request.role, request.reason_codes))
            return self._image("repaired.png", (250, 240, 230))

        result = create_reference_sheet(
            plan,
            self.outputs / "repaired-sheet.png",
            generate_panel=generate,
            reviewer=lambda _request: reviews.pop(0),
            repair_panel=repair,
        )
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(repair_calls[0][1], plan.panel_roles[0])
        self.assertEqual(repair_calls[0][2], ("identity_mismatch",))
        self.assertEqual(result.repaired_roles, (plan.panel_roles[0],))
        self.assertEqual(result.review.status, "pass")
        self.assertEqual(
            self._digest(generated[plan.panel_roles[0]]),
            original_digest[plan.panel_roles[0]],
        )
        repaired_artifact = next(
            artifact for artifact in result.artifacts if artifact.role == plan.panel_roles[0]
        )
        self.assertEqual(repaired_artifact.provenance.strategy, "repaired_panel")

    def test_failed_repair_is_never_repeated(self):
        plan = self._plan()
        repairs = []
        review_count = 0

        def generate(request):
            return self._image(
                f"p-{request.index}.png",
                (request.index * 15, request.index * 12, request.index * 9),
            )

        def review(_request):
            nonlocal review_count
            review_count += 1
            return self._fail_review(plan.panel_roles[0])

        def repair(_path, request):
            repairs.append(request.role)
            return self._image("only-repair.png", (200, 190, 180))

        result = create_reference_sheet(
            plan,
            self.outputs / "still-failed.png",
            generate_panel=generate,
            reviewer=review,
            repair_panel=repair,
        )
        self.assertEqual(repairs, [plan.panel_roles[0]])
        self.assertEqual(review_count, 2)
        self.assertEqual(result.review.status, "fail")
        self.assertEqual(
            build_failed_panel_repair_plan(plan, result.review),
            (plan.panel_roles[0],),
        )

    def test_semantic_repair_loop_stops_exactly_at_five_attempts(self):
        plan = self._plan()
        repairs = []
        reviews = []

        def generate(request):
            return self._image(
                f"bounded-{request.index}.png",
                (request.index * 13, request.index * 17, request.index * 19),
            )

        def review(request):
            reviews.append(request.sheet_path)
            return self._fail_review(plan.panel_roles[0])

        def repair(path, request):
            repairs.append((path, request.role))
            return self._image(
                f"bounded-repair-{len(repairs)}.png",
                (200, 150 + len(repairs), 100),
            )

        result = create_reference_sheet(
            plan,
            self.outputs / "bounded-five.png",
            generate_panel=generate,
            reviewer=review,
            repair_panel=repair,
            max_repair_attempts=5,
        )
        self.assertEqual(len(repairs), 5)
        self.assertEqual(len(reviews), 6)
        self.assertEqual(len(set(reviews)), 6)
        self.assertEqual(
            [role for _path, role in repairs],
            [plan.panel_roles[0]] * 5,
        )
        self.assertEqual(result.review.status, "fail")
        self.assertEqual(result.repair_attempts_used, 5)
        self.assertEqual(result.max_repair_attempts, 5)
        self.assertEqual(result.repaired_roles, (plan.panel_roles[0],) * 5)
        self.assertTrue(result.sheet_path.is_file())
        metadata = result.public_metadata()
        self.assertEqual(metadata["max_repair_attempts"], 5)
        self.assertEqual(metadata["repair_attempts_used"], 5)
        self.assertEqual(metadata["generation_model"], plan.model)

    def test_zero_budget_and_draft_never_repair_panels(self):
        production = self._plan()

        def generate(request):
            return self._image(
                f"zero-{request.index}.png", (request.index * 11, 80, 90),
            )

        production_result = create_reference_sheet(
            production,
            self.outputs / "zero-budget.png",
            generate_panel=generate,
            reviewer=lambda _request: self._fail_review(
                production.panel_roles[0],
            ),
            repair_panel=lambda *_args: self.fail("zero budget must not repair"),
            max_repair_attempts=0,
        )
        self.assertEqual(production_result.review.status, "fail")
        self.assertEqual(production_result.repair_attempts_used, 0)

        draft = self._plan(mode="draft")
        draft_source = self._image(
            "no-repair-draft.png", (1, 2, 3), draft.draft_size,
        )
        draft_result = create_reference_sheet(
            draft,
            self.outputs / "no-repair-draft-sheet.png",
            generate_draft=lambda _request: draft_source,
            reviewer=lambda _request: self._fail_review(draft.panel_roles[0]),
            repair_panel=lambda *_args: self.fail("draft must not repair"),
            max_repair_attempts=5,
        )
        self.assertEqual(draft_result.review.status, "fail")
        self.assertEqual(draft_result.max_repair_attempts, 5)
        self.assertEqual(draft_result.repair_attempts_used, 0)

    def test_service_repair_budget_rejects_values_outside_zero_through_five(self):
        plan = self._plan()
        for value in (-1, 6, True, 1.0):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "max_repair_attempts"),
            ):
                create_reference_sheet(
                    plan,
                    self.outputs / f"invalid-budget-{value}.png",
                    generate_panel=lambda _request: self.fail(
                        "invalid budget must fail before generation",
                    ),
                    max_repair_attempts=value,
                )

    def test_one_structurally_invalid_panel_can_be_repaired_once(self):
        plan = self._plan()
        last_role = plan.panel_roles[-1]
        repairs = []

        def generate(request):
            size = (95, 80) if request.role == last_role else plan.panel_size
            return self._image(
                f"struct-{request.index}.png",
                (request.index * 18, request.index * 14, request.index * 10),
                size,
            )

        def repair(_path, request):
            repairs.append((request.role, request.reason_codes))
            return self._image("struct-fixed.png", (77, 88, 99), plan.panel_size)

        result = create_reference_sheet(
            plan,
            self.outputs / "struct-fixed-sheet.png",
            generate_panel=generate,
            reviewer=lambda _request: self._pass_review(),
            repair_panel=repair,
        )
        self.assertEqual(repairs, [(last_role, ("panel_dimensions_invalid",))])
        self.assertEqual(result.repaired_roles, (last_role,))

    def test_multiple_structural_failures_attempt_only_one_repair_then_fail_closed(self):
        plan = self._plan()
        bad_roles = set(plan.panel_roles[:2])
        repairs = []

        def generate(request):
            size = (95, 80) if request.role in bad_roles else plan.panel_size
            return self._image(
                f"multi-{request.index}.png", (20 + request.index, 30, 40), size,
            )

        def repair(_path, request):
            repairs.append(request.role)
            return self._image("one-fixed.png", (90, 80, 70), plan.panel_size)

        with self.assertRaisesRegex(ReferenceSheetStructureError, "panel_dimensions_invalid"):
            create_reference_sheet(
                plan,
                self.outputs / "multi-invalid.png",
                generate_panel=generate,
                repair_panel=repair,
            )
        self.assertEqual(repairs, [plan.panel_roles[0]])
        self.assertFalse((self.outputs / "multi-invalid.png").exists())

    def test_structural_repair_loop_revalidates_in_recipe_order(self):
        plan = self._plan()
        bad_roles = set(plan.panel_roles[:2])
        repairs = []

        def generate(request):
            size = (95, 80) if request.role in bad_roles else plan.panel_size
            return self._image(
                f"multi-bounded-{request.index}.png",
                (20 + request.index, 30, 40),
                size,
            )

        def repair(_path, request):
            repairs.append(request.role)
            return self._image(
                f"multi-fixed-{len(repairs)}.png",
                (90, 80, 70),
                plan.panel_size,
            )

        result = create_reference_sheet(
            plan,
            self.outputs / "multi-fixed-sheet.png",
            generate_panel=generate,
            reviewer=lambda _request: self._pass_review(),
            repair_panel=repair,
            max_repair_attempts=2,
        )
        self.assertEqual(repairs, list(plan.panel_roles[:2]))
        self.assertEqual(result.repaired_roles, plan.panel_roles[:2])
        self.assertEqual(result.repair_attempts_used, 2)
        self.assertEqual(result.review.status, "pass")

    def test_forged_or_stale_plans_are_rejected_at_execution_boundary(self):
        plan = self._plan()
        forged = (
            replace(plan, mode="not-a-mode"),
            replace(plan, asset_type="person"),
            replace(plan, planner_version="reference-sheet-v0"),
            replace(plan, panels=tuple(reversed(plan.panels))),
            replace(plan, columns=9),
        )
        for candidate in forged:
            with (
                self.subTest(candidate=candidate),
                self.assertRaisesRegex(ValueError, "unsupported reference-sheet plan"),
            ):
                create_reference_sheet(
                    candidate,
                    self.outputs / "forged.png",
                    generate_panel=lambda _request: self.fail("must not execute"),
                    generate_draft=lambda _request: self.fail("must not execute"),
                )

        stale = replace(plan, planner_version="reference-sheet-v0")
        with self.assertRaisesRegex(ValueError, "unsupported reference-sheet plan"):
            compose_reference_sheet(
                stale, self._panel_files(plan), self.outputs / "stale-compose.png",
            )
        with self.assertRaisesRegex(ValueError, "unsupported reference-sheet plan"):
            build_semantic_review_request(stale, self.outputs / "sheet.png")
        with self.assertRaisesRegex(ValueError, "unsupported reference-sheet plan"):
            build_failed_panel_repair_plan(
                stale,
                parse_semantic_review_result(
                    self._pass_review(), allowed_roles=plan.panel_roles,
                ),
            )
        self.assertFalse((self.outputs / "stale-compose.png").exists())

    def test_oversized_image_is_rejected_from_header_before_decode(self):
        plan = self._plan()
        oversized = self._image("oversized.png", (1, 2, 3), (4097, 80))
        panels = self._panel_files(plan)
        panels[0] = PanelFile(plan.panel_roles[0], oversized)
        original_load = Image.Image.load
        loaded_oversized = []

        def checked_load(image, *args, **kwargs):
            if image.size == (4097, 80):
                loaded_oversized.append(True)
            return original_load(image, *args, **kwargs)

        with (
            mock.patch.object(Image.Image, "load", checked_load),
            self.assertRaisesRegex(
                ReferenceSheetStructureError, "panel_dimensions_exceed_limit",
            ),
        ):
            validate_panel_files(
                panels, expected_roles=plan.panel_roles, panel_size=plan.panel_size,
            )
        self.assertEqual(loaded_oversized, [])

    def test_semantic_repair_second_composition_failure_never_publishes_partial_final(self):
        plan = self._plan()

        def generate(request):
            return self._image(
                f"atomic-{request.index}.png", (50 + request.index, 60, 70),
            )

        saves = 0
        real_save = reference_sheets._save_new_png

        def fail_second_save(image, path):
            nonlocal saves
            saves += 1
            if saves == 2:
                raise OSError("synthetic second composition failure")
            return real_save(image, path)

        with (
            mock.patch.object(reference_sheets, "_save_new_png", fail_second_save),
            self.assertRaisesRegex(OSError, "synthetic second composition failure"),
        ):
            create_reference_sheet(
                plan,
                self.outputs / "atomic-final.png",
                generate_panel=generate,
                reviewer=lambda _request: self._fail_review(plan.panel_roles[0]),
                repair_panel=lambda _path, _request: self._image(
                    "atomic-repair.png", (200, 210, 220),
                ),
            )
        self.assertFalse((self.outputs / "atomic-final.png").exists())
        self.assertEqual(list(self.outputs.iterdir()), [])

    def test_repair_failure_never_deletes_interval_stage_replacement(self):
        plan = self._plan()
        for replacement_kind in ("regular", "symlink"):
            if replacement_kind == "symlink" and not hasattr(os, "symlink"):
                continue
            with self.subTest(replacement_kind=replacement_kind):
                reviewed_paths = []

                def generate(request):
                    return self._image(
                        f"interval-{replacement_kind}-{request.index}.png",
                        (40 + request.index, 50, 60),
                    )

                def review(request):
                    reviewed_paths.append(request.sheet_path)
                    return self._fail_review(plan.panel_roles[0])

                external = self._image(
                    f"interval-external-{replacement_kind}.png",
                    (1, 2, 3),
                )

                def replace_then_fail(_path, _request):
                    stage = reviewed_paths[-1]
                    self.assertFalse(stage.exists())
                    if replacement_kind == "regular":
                        stage.write_bytes(b"unowned-regular-replacement")
                    else:
                        stage.symlink_to(external)
                    raise RuntimeError("synthetic repair failure")

                final = self.outputs / f"interval-{replacement_kind}.png"
                with self.assertRaisesRegex(
                    RuntimeError, "synthetic repair failure",
                ):
                    create_reference_sheet(
                        plan,
                        final,
                        generate_panel=generate,
                        reviewer=review,
                        repair_panel=replace_then_fail,
                    )
                stage = reviewed_paths[-1]
                self.assertFalse(final.exists())
                if replacement_kind == "regular":
                    self.assertEqual(
                        stage.read_bytes(), b"unowned-regular-replacement",
                    )
                else:
                    self.assertTrue(stage.is_symlink())
                    self.assertEqual(stage.resolve(), external.resolve())

    def test_concurrent_final_creation_is_preserved_and_stage_is_cleaned(self):
        plan = self._plan()
        final = self.outputs / "concurrent.png"

        def generate(request):
            return self._image(
                f"race-{request.index}.png", (70 + request.index, 80, 90),
            )

        def review(_request):
            final.write_bytes(b"external-winner")
            return self._pass_review()

        with self.assertRaisesRegex(ReferenceSheetStructureError, "sheet_output_exists"):
            create_reference_sheet(
                plan, final, generate_panel=generate, reviewer=review,
            )
        self.assertEqual(final.read_bytes(), b"external-winner")
        self.assertEqual(list(self.outputs.glob(".*.review-*.png")), [])

    def test_stage_path_swap_at_publish_boundary_still_uses_reviewed_descriptor(self):
        plan = self._plan()
        final = self.outputs / "link-swap.png"

        def generate(request):
            return self._image(
                f"link-swap-{request.index}.png", (80 + request.index, 20, 30),
            )

        real_promote = reference_sheets._promote_new_file
        replacements = []
        reviewed = []

        def review(request):
            reviewed.append((request.sheet_path, self._digest(request.sheet_path)))
            return self._pass_review()

        def swap_then_promote(destination, snapshot):
            source_path = reviewed[0][0]
            with Image.open(source_path) as image:
                size = image.size
            source_path.unlink()
            replacement = Image.new("RGB", size, (255, 0, 255))
            replacement.save(source_path, format="PNG")
            replacement.close()
            replacements.append(source_path)
            return real_promote(destination, snapshot)

        with mock.patch.object(reference_sheets, "_promote_new_file", swap_then_promote):
            result = create_reference_sheet(
                plan,
                final,
                generate_panel=generate,
                reviewer=review,
            )
        self.assertEqual(self._digest(result.sheet_path), reviewed[0][1])
        self.assertTrue(replacements[0].exists())
        self.assertNotEqual(self._digest(replacements[0]), reviewed[0][1])
        replacements[0].unlink()

    def test_destination_replacement_during_descriptor_copy_is_preserved(self):
        plan = self._plan()
        final = self.outputs / "destination-swap.png"

        def generate(request):
            return self._image(
                f"destination-swap-{request.index}.png",
                (60 + request.index, 30, 40),
            )

        real_fsync = os.fsync
        swapped = []

        def swap_during_fsync(descriptor):
            if final.exists() and not swapped:
                final.unlink()
                final.write_bytes(b"external-winner")
                swapped.append(True)
            return real_fsync(descriptor)

        with (
            mock.patch.object(reference_sheets.os, "fsync", swap_during_fsync),
            self.assertRaisesRegex(ReferenceSheetStructureError, "sheet_publish_replaced"),
        ):
            create_reference_sheet(
                plan,
                final,
                generate_panel=generate,
                reviewer=lambda _request: self._pass_review(),
            )
        self.assertEqual(swapped, [True])
        self.assertEqual(final.read_bytes(), b"external-winner")

    def test_same_inode_destination_corruption_is_detected_and_rolled_back(self):
        plan = self._plan()
        final = self.outputs / "destination-corrupt.png"

        def generate(request):
            return self._image(
                f"destination-corrupt-{request.index}.png",
                (30 + request.index, 40, 50),
            )

        real_fsync = os.fsync
        corrupted = []

        def corrupt_during_fsync(descriptor):
            if final.exists() and not corrupted:
                with final.open("wb") as handle:
                    handle.write(b"Z")
                corrupted.append(True)
            return real_fsync(descriptor)

        with (
            mock.patch.object(reference_sheets.os, "fsync", corrupt_during_fsync),
            self.assertRaisesRegex(ReferenceSheetStructureError, "sheet_publish_modified"),
        ):
            create_reference_sheet(
                plan,
                final,
                generate_panel=generate,
                reviewer=lambda _request: self._pass_review(),
            )
        self.assertEqual(corrupted, [True])
        self.assertFalse(final.exists())

    def test_publication_succeeds_when_fchmod_is_unavailable(self):
        plan = self._plan()

        def generate(request):
            return self._image(
                f"no-fchmod-{request.index}.png", (40 + request.index, 50, 60),
            )

        with mock.patch.object(reference_sheets.os, "fchmod", None, create=True):
            result = create_reference_sheet(
                plan,
                self.outputs / "no-fchmod.png",
                generate_panel=generate,
                reviewer=lambda _request: self._pass_review(),
            )
        self.assertTrue(result.sheet_path.is_file())
        self.assertEqual(result.review.status, "pass")

    def test_reviewer_cannot_mutate_reviewed_stage_before_publication(self):
        plan = self._plan()
        final = self.outputs / "mutated.png"

        def generate(request):
            return self._image(
                f"mutation-{request.index}.png", (90 + request.index, 40, 50),
            )

        def mutate(request):
            os.chmod(request.sheet_path, 0o600)
            with Image.open(request.sheet_path) as source:
                size = source.size
            replacement = Image.new("RGB", size, (255, 0, 255))
            replacement.save(request.sheet_path, format="PNG")
            replacement.close()
            return self._pass_review()

        with self.assertRaisesRegex(ReferenceSheetStructureError, "sheet_stage_modified"):
            create_reference_sheet(
                plan, final, generate_panel=generate, reviewer=mutate,
            )
        self.assertFalse(final.exists())
        self.assertEqual(list(self.outputs.glob(".*.review-*.png")), [])

    def test_reviewer_stage_replacement_or_symlink_is_never_published_or_deleted(self):
        plan = self._plan()

        def generate(request):
            return self._image(
                f"replacement-{request.index}.png", (110 + request.index, 50, 60),
            )

        replacement_paths = []

        def replace_stage(request):
            with Image.open(request.sheet_path) as source:
                size = source.size
            request.sheet_path.unlink()
            image = Image.new("RGB", size, (1, 2, 3))
            image.save(request.sheet_path, format="PNG")
            image.close()
            replacement_paths.append(request.sheet_path)
            return self._pass_review()

        with self.assertRaises(ReferenceSheetStructureError):
            create_reference_sheet(
                plan,
                self.outputs / "replaced.png",
                generate_panel=generate,
                reviewer=replace_stage,
            )
        self.assertFalse((self.outputs / "replaced.png").exists())
        self.assertTrue(replacement_paths[0].exists())
        replacement_paths[0].unlink()

        if hasattr(os, "symlink"):
            symlinks = []

            def symlink_stage(request):
                with Image.open(request.sheet_path) as source:
                    size = source.size
                external = self._image("external-stage.png", (4, 5, 6), size)
                request.sheet_path.unlink()
                request.sheet_path.symlink_to(external)
                symlinks.append(request.sheet_path)
                return self._pass_review()

            with self.assertRaises(ReferenceSheetStructureError):
                create_reference_sheet(
                    plan,
                    self.outputs / "symlinked.png",
                    generate_panel=generate,
                    reviewer=symlink_stage,
                )
            self.assertFalse((self.outputs / "symlinked.png").exists())
            self.assertTrue(symlinks[0].is_symlink())
            symlinks[0].unlink()

    def test_relative_output_is_stable_when_reviewer_changes_cwd(self):
        original_cwd = Path.cwd()
        other = self.root / "other-cwd"
        other.mkdir()
        try:
            os.chdir(self.outputs)
            production = self._plan()

            def generate(request):
                return self._image(
                    f"cwd-{request.index}.png", (120 + request.index, 70, 80),
                )

            def move_cwd(_request):
                os.chdir(other)
                return self._pass_review()

            result = create_reference_sheet(
                production,
                "relative-production.png",
                generate_panel=generate,
                reviewer=move_cwd,
            )
            self.assertEqual(result.sheet_path, self.outputs / "relative-production.png")
            self.assertTrue(result.sheet_path.is_file())
            self.assertEqual(list(self.outputs.glob(".*.review-*.png")), [])

            os.chdir(self.outputs)
            draft_plan = self._plan(mode="draft")
            draft = self._image("cwd-draft.png", (10, 20, 30), draft_plan.draft_size)
            draft_result = create_reference_sheet(
                draft_plan,
                "relative-draft.png",
                generate_draft=lambda _request: draft,
                reviewer=move_cwd,
            )
            self.assertEqual(draft_result.sheet_path, self.outputs / "relative-draft.png")
            self.assertTrue(draft_result.sheet_path.is_file())
            self.assertEqual(list(self.outputs.glob(".*.review-*.png")), [])
        finally:
            os.chdir(original_cwd)

    def test_public_metadata_and_artifacts_never_persist_request_paths_or_review_text(self):
        secret = "PRIVATE CREATIVE REQUEST SHOULD NOT PERSIST"
        plan = build_reference_sheet_plan(
            asset_type="item",
            mode="production",
            creative_request=secret,
            model="local/model-v1",
            panel_size=(96, 80),
        )

        def generate(request):
            return self._image(
                f"private-{request.index}.png",
                (request.index * 11, request.index * 7, request.index * 5),
            )

        result = create_reference_sheet(
            plan,
            self.outputs / "private-sheet.png",
            generate_panel=generate,
            reviewer=lambda _request: self._pass_review(),
        )
        public = json.dumps(result.public_metadata(), sort_keys=True)
        artifact_public = json.dumps(
            [artifact.public_metadata() for artifact in result.artifacts], sort_keys=True,
        )
        for forbidden in (secret, str(self.root), "private-sheet.png", "creative_request", "sheet_path"):
            self.assertNotIn(forbidden, public)
            self.assertNotIn(forbidden, artifact_public)
        for artifact in result.artifacts:
            self.assertEqual(
                set(artifact.public_metadata()),
                {"role", "model", "provenance", "reason_codes"},
            )
            self.assertEqual(
                set(artifact.public_metadata()["provenance"]),
                {"strategy", "version"},
            )
        with self.assertRaises(FrozenInstanceError):
            result.artifacts[0].provenance.strategy = "prompt=SECRET"
        roles = [artifact.public_metadata()["role"] for artifact in result.artifacts]
        self.assertEqual(roles.count("sheet"), 1)
        self.assertEqual(set(roles) - {"sheet"}, set(plan.panel_roles))


if __name__ == "__main__":
    unittest.main()
