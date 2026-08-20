"""Continuum H3 window-planner helpers.

Locks leftover 1.9.0 `h3_window_planner` / `plan_h3_sliding_windows` /
`h3_window_plan_signature` / `parse_h3_manual_window_prompts` /
`normalize_h3_injected_keyframes` probes onto Continuum
`h3_planner_helpers` geometry and compile helpers. Do not invent the
leftover sliding-window architecture or restore `h3_window_planner`.
"""
from __future__ import annotations

import os
import sys
import unittest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.h3_planner_helpers import (  # noqa: E402
    compile_h3_window_prompts,
    compute_h3_window_boundaries,
)


_LAUNCH_PATH = os.path.join(_APP, "launch.py")
_HELPERS_PATH = os.path.join(_APP, "services", "h3_planner_helpers.py")
_LEFTOVER_MODULE = os.path.join(_APP, "services", "h3_window_planner.py")
_LEFTOVER_NAMES = (
    "plan_h3_sliding_windows",
    "h3_window_plan_signature",
    "parse_h3_manual_window_prompts",
    "normalize_h3_injected_keyframes",
    "_plan_contract_violations",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TestContinuumWindowPlannerGates(unittest.TestCase):
    def test_launch_does_not_restore_leftover_sliding_window_module(self):
        launch = _read(_LAUNCH_PATH)
        helpers = _read(_HELPERS_PATH)

        # Leftover 1.9.0 imported services.h3_window_planner as the live
        # sliding-window planner. Continuum dropped that module and kept
        # geometry/compile helpers only.
        self.assertFalse(os.path.isfile(_LEFTOVER_MODULE))
        self.assertNotIn("h3_window_planner", launch)
        self.assertNotIn("from services.h3_window_planner", helpers)
        self.assertNotIn("import h3_window_planner", helpers)
        for name in _LEFTOVER_NAMES:
            with self.subTest(leftover=name):
                self.assertNotIn(f"def {name}", helpers)
                self.assertNotIn(name, launch)

    def test_continuum_helpers_keep_geometry_and_compile_not_sliding_windows(self):
        helpers = _read(_HELPERS_PATH)
        self.assertIn("This is not the deleted sliding-window architecture", helpers)
        self.assertIn("def compute_h3_window_boundaries(", helpers)
        self.assertIn("def compile_h3_window_prompts(", helpers)
        self.assertIn("def _fallback_plan(", helpers)
        self.assertIn("def _narrative_dialogue_expected(", helpers)
        self.assertNotIn("def plan_h3_sliding_windows(", helpers)

    def test_window_boundaries_are_committed_output_spans(self):
        spans = compute_h3_window_boundaries(240, 81, fps=24.0)
        self.assertGreaterEqual(len(spans), 2)
        self.assertEqual(spans[0]["start_frame"], 0)
        self.assertEqual(spans[-1]["end_frame"], 240)
        for previous, current in zip(spans, spans[1:]):
            self.assertEqual(previous["end_frame"], current["start_frame"])

    def test_compile_window_prompts_uses_continuum_helpers_not_leftover_signatures(self):
        spans = compute_h3_window_boundaries(48, 48, fps=24.0)
        compiled = compile_h3_window_prompts(
            {
                "subject_continuity": "One unchanged subject",
                "setting_continuity": "One unchanged location",
                "visual_continuity": "One coherent live-action style",
                "initial_state": "The subject begins at rest",
                "ambient_audio": "continuous room tone",
                "music": "N/A",
                "windows": [
                    {
                        "window": 1,
                        "title": "Opening",
                        "coverage": "cinematic coverage",
                        "pacing": "real-time",
                        "shots": [
                            {
                                "shot": 1,
                                "start_seconds": 0.0,
                                "end_seconds": 2.0,
                                "transition": "opening composition",
                                "framing": "medium shot",
                                "camera": "locked camera",
                                "action": "The subject steps forward",
                                "dialogue": [],
                                "sound_effects": "N/A",
                            }
                        ],
                        "closing_state": "The subject holds the new stance",
                    }
                ],
            },
            spans,
        )
        self.assertEqual(len(compiled), 1)
        prompt = compiled[0]["prompt"]
        self.assertIn("The subject steps forward", prompt)
        self.assertNotIn("{", prompt)
        self.assertNotIn("}", prompt)
        self.assertEqual(prompt.count("integrated_multimodal_description:"), 1)


if __name__ == "__main__":
    unittest.main()
