"""Synthetic regressions for reusable project reference-sheet construction."""

from __future__ import annotations

import hashlib
import json
import os
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

from services import reference_sheets
from services.reference_sheets import (
    ROLE_RECIPES,
    PanelFile,
    ReferenceSheetReviewError,
    ReferenceSheetStructureError,
    build_failed_panel_repair_plan,
    build_reference_sheet_plan,
    build_semantic_review_request,
    compose_reference_sheet,
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

    def test_production_generates_every_panel_independently_and_returns_lineage(self):
        plan = self._plan()
        calls = []

        def generate(request):
            calls.append((request.role, request.strategy, request.index))
            return self._image(
                f"generated-{request.index}.png",
                (request.index * 31, request.index * 29, request.index * 23),
            )

        result = create_reference_sheet(
            plan,
            self.outputs / "production.png",
            generate_panel=generate,
            reviewer=lambda _request: self._pass_review(),
        )
        self.assertEqual(
            calls,
            [(role, "independent", index) for index, role in enumerate(plan.panel_roles)],
        )
        roles = [artifact.role for artifact in result.artifacts]
        self.assertEqual(roles.count("sheet"), 1)
        self.assertEqual(tuple(roles[:-1]), plan.panel_roles)
        self.assertNotIn("palette", roles)
        self.assertEqual(result.review.status, "pass")
        self.assertEqual(result.public_metadata()["roles"]["panels"], list(plan.panel_roles))
        self.assertEqual(list(self.outputs.glob("*.review-*.png")), [])

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
        )
        self.assertEqual(calls[0], ("generate", plan.panel_roles[0], "identity_anchor", None))
        self.assertEqual(
            [(kind, role, strategy) for kind, role, strategy, _ in calls[1:]],
            [("edit", role, "targeted_edit") for role in plan.panel_roles[1:]],
        )
        self.assertTrue(all(call[3] == anchor.resolve() for call in calls[1:]))
        self.assertEqual(self._digest(anchor), anchor_digest)
        self.assertEqual(result.artifacts[0].provenance.strategy, "anchor_edit")

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

    def test_review_request_and_strict_parser_are_fidelity_only(self):
        plan = self._plan()
        request = build_semantic_review_request(plan, self.outputs / "sheet.png")
        self.assertIn("identity", request.instruction)
        self.assertIn("requested details", request.instruction)
        self.assertIn("view", request.instruction)
        self.assertIn("accessories", request.instruction)
        self.assertIn("style", request.instruction)
        self.assertIn("Do not perform content moderation", request.instruction)
        self.assertIn("permissibility", request.instruction)
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
