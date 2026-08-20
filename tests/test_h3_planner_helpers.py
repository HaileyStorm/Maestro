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
    _parse_json_object,
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


if __name__ == "__main__":
    unittest.main()
