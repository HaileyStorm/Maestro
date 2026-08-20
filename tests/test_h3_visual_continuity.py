"""Visual join carry for H3 long-form. No GPU. No video inspection."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest

_APP = Path(__file__).resolve().parents[1] / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from services.h3_shot_planner import plan_h3_native_shots  # noqa: E402
from services.h3_visual_continuity import (  # noqa: E402
    SAME_SOURCE_VISUAL_CARRY_LINE,
    apply_same_source_visual_carry,
    apply_visual_carry_to_shot_plan,
    ref2va_handoff_uses_temporal_tail,
    same_source_visual_carry_line,
)
from shared.utils.prompt_parser import (  # noqa: E402
    classify_timeline_clip_boundaries,
)


LAUNCH = Path(__file__).resolve().parents[1] / "app" / "launch.py"


class H3VisualContinuityTests(unittest.TestCase):
    def test_authored_cuts_still_request_temporal_tail(self):
        for boundary in ("continuous", "precut", "cut", "transition"):
            with self.subTest(boundary=boundary):
                self.assertTrue(ref2va_handoff_uses_temporal_tail(boundary))
        self.assertFalse(ref2va_handoff_uses_temporal_tail("independent"))
        self.assertFalse(ref2va_handoff_uses_temporal_tail(""))

    def test_same_source_carry_prefixes_non_first_clips(self):
        prompts = [
            "[0-10s] Hallway start.",
            "[0-10s] Macro of the cap.",
            "[0-10s] Tracking into the closet.",
        ]
        boundaries = [
            {"type": "cut", "continuity_mode": "independent"},
            {"type": "cut", "continuity_mode": "independent"},
        ]
        updated = apply_same_source_visual_carry(
            prompts, clip_boundaries=boundaries,
        )
        self.assertEqual(updated[0], prompts[0])
        self.assertTrue(updated[1].startswith(SAME_SOURCE_VISUAL_CARRY_LINE))
        self.assertIn("Macro of the cap.", updated[1])
        self.assertTrue(updated[2].startswith(SAME_SOURCE_VISUAL_CARRY_LINE))
        again = apply_same_source_visual_carry(
            updated, clip_boundaries=boundaries,
        )
        self.assertEqual(again[1].count(SAME_SOURCE_VISUAL_CARRY_LINE), 1)

    def test_independent_non_temporal_boundary_skips_visual_carry(self):
        prompts = ["[0-10s] Opening.", "[0-10s] Unrelated beat."]
        boundaries = [
            {"type": "independent", "continuity_mode": "independent"},
        ]
        updated = apply_same_source_visual_carry(
            prompts, clip_boundaries=boundaries,
        )
        self.assertEqual(updated, prompts)

    def test_launch_ref2va_attach_call_site_is_segment_continuation_loop(self):
        """Document the Ember attach site without weakening the canary below."""

        tree = ast.parse(LAUNCH.read_text(encoding="utf-8"), filename=str(LAUNCH))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_attach_h3_ref2va_handoff"
        ]
        self.assertEqual(len(calls), 1)
        segment = ast.get_source_segment(
            LAUNCH.read_text(encoding="utf-8"), calls[0],
        )
        self.assertIsNotNone(segment)
        self.assertIn("ref2va_boundary", segment)
        self.assertIn("last_frame_path=cont_path", segment.replace(" ", ""))

    def test_museum_style_shot_list_is_classified_as_cuts_then_carry_applies(self):
        prompt = (
            "[Shot 1] A traveler stands in a hallway.\n"
            "[Shot 2] At 00:10.000, the camera cuts to a macro of a cap.\n"
            "[Shot 3] At 00:20.000, cut back to a tracking shot."
        )
        frames = [240, 240, 240]
        classified = classify_timeline_clip_boundaries(
            prompt, clip_frame_counts=frames, fps=24,
        )
        self.assertEqual([item["type"] for item in classified], ["cut", "cut"])
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=frames,
            fps=24,
            clip_boundaries=classified,
        )
        self.assertNotIn(
            SAME_SOURCE_VISUAL_CARRY_LINE, plan["clip_prompts"][1],
        )
        apply_visual_carry_to_shot_plan(plan)
        self.assertTrue(
            plan["clip_prompts"][1].startswith(same_source_visual_carry_line()),
        )
        self.assertIn("macro of a cap", plan["clip_prompts"][1].casefold())
        self.assertIn("[Shot 2]", plan["clip_prompts"][1])
        self.assertIn("[Shot 3]", plan["clip_prompts"][2])

    def test_carry_line_locks_identity_and_richer_seams(self):
        line = SAME_SOURCE_VISUAL_CARRY_LINE.casefold()
        for token in (
            "identity",
            "wardrobe",
            "location",
            "camera-world",
            "segment seam",
        ):
            with self.subTest(token=token):
                self.assertIn(token, line)
        self.assertNotIn("avoid [shot", line)
        module = Path(__file__).resolve().parents[1] / "app" / "services" / "h3_visual_continuity.py"
        source = module.read_text(encoding="utf-8").casefold()
        self.assertIn("hard", source)
        self.assertIn("do not tell rewriters to avoid shot markers", source)
        self.assertNotIn("avoid [shot n]", source)

    def test_live_ref2va_handoff_still_tails_only_continuous_and_precut(self):
        """Canary: Ember has not yet applied temporal-tail-on-cut in launch.py."""

        tree = ast.parse(LAUNCH.read_text(encoding="utf-8"), filename=str(LAUNCH))
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_attach_h3_ref2va_handoff"
        )
        source = ast.get_source_segment(
            LAUNCH.read_text(encoding="utf-8"), function,
        )
        self.assertIsNotNone(source)
        self.assertIn(
            'boundary_type in {"continuous", "precut"}',
            source,
        )
        self.assertNotIn("ref2va_handoff_uses_temporal_tail", source)
