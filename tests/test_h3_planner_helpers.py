"""Import-clean regressions for Continuum H3 planner helpers."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.h3_planner_helpers import (  # noqa: E402
    _infer_camera_coverage,
    _normalized_window_shots,
    _parse_json_object,
    compile_h3_window_prompts,
    compute_h3_window_boundaries,
    normalize_h3_camera_coverage,
)
from services.h3_sequence_planner import (  # noqa: E402
    compute_h3_native_sequence_windows,
)
from services.h3_story_ledger import plan_h3_story_segments  # noqa: E402


class H3PlannerHelperTests(unittest.TestCase):
    def test_sequence_and_story_modules_import_without_window_planner(self):
        self.assertTrue(callable(compute_h3_native_sequence_windows))
        self.assertTrue(callable(plan_h3_story_segments))

    def test_camera_coverage_and_boundaries(self):
        self.assertEqual(normalize_h3_camera_coverage("MULTI-SHOT"), "multi_shot")
        spans = compute_h3_window_boundaries(480, 240, fps=24, overlap_frames=0)
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0]["start_frame"], 0)
        self.assertEqual(spans[-1]["end_frame"], 480)

    def test_parse_json_object_strips_fences(self):
        parsed = _parse_json_object('```json\n{"ok": true}\n```')
        self.assertEqual(parsed, {"ok": True})

    def test_unknown_coverage_falls_back_to_auto(self):
        self.assertEqual(normalize_h3_camera_coverage("orbit"), "auto")
        self.assertEqual(_infer_camera_coverage("one-take hallway walk"), "continuous")
        self.assertEqual(
            _infer_camera_coverage("Maya Chen starts a conversation with Jordan Hale"),
            "multi_shot",
        )

    def test_overlapping_windows_cover_the_full_span(self):
        spans = compute_h3_window_boundaries(
            720, 240, fps=24, overlap_frames=24, discard_frames=0,
        )
        self.assertGreaterEqual(len(spans), 3)
        self.assertEqual(spans[0]["start_frame"], 0)
        self.assertEqual(spans[-1]["end_frame"], 720)
        self.assertEqual(spans[0]["end_frame"], spans[1]["start_frame"])

    def test_shot_compiler_keeps_authored_cuts(self):
        shots = _normalized_window_shots(
            {
                "shots": [
                    {
                        "shot": 1,
                        "end_seconds": 4.0,
                        "transition": "opening composition",
                        "framing": "wide kitchen",
                        "camera": "locked wide",
                        "action": "She fills a glass at the sink.",
                    },
                    {
                        "shot": 2,
                        "end_seconds": 8.0,
                        "transition": "hard cut",
                        "framing": "close on the glass",
                        "camera": "push in",
                        "action": "The glass rings when she sets it down.",
                    },
                ],
            },
            8.0,
        )
        self.assertEqual([shot["shot"] for shot in shots], [1, 2])
        self.assertEqual(shots[1]["transition"], "hard cut")
        compiled = compile_h3_window_prompts(
            {
                "subject_continuity": "The same woman in the same kitchen",
                "setting_continuity": "Night kitchen, linoleum and chrome",
                "windows": [
                    {
                        "title": "Sink then glass",
                        "closing_state": "The glass rests on the counter",
                        "shots": shots,
                    },
                ],
            },
            [{"index": 1, "start_frame": 0, "end_frame": 192, "start_seconds": 0.0, "end_seconds": 8.0}],
        )
        prompt = compiled[0]["prompt"]
        self.assertIn("[Shot 1]", prompt)
        self.assertIn("[Shot 2]", prompt)
        self.assertIn("hard cut", prompt)
        self.assertNotIn("same shot / camera continues", prompt)

    def test_parse_json_object_extracts_object_from_prose(self):
        parsed = _parse_json_object('Planner said:\n{"ok": true, "n": 2}\nThanks.')
        self.assertEqual(parsed, {"ok": True, "n": 2})
        self.assertIsNone(_parse_json_object("not json at all"))


if __name__ == "__main__":
    unittest.main()
