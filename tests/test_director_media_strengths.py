"""Continuum Director media-strength helpers.

Locks leftover 1.9.0 `_normalize_director_media_strengths` /
`_director_uses_fixed_media_strength` probes onto Continuum
`_create_director_video_execution_profile` and
`_prepare_director_h3_longform`. Do not invent leftover H3/LTX-2.5
strength rewrites or restore those helpers.
"""
from __future__ import annotations

import os
import sys
import unittest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import director_pipeline as pipeline  # noqa: E402


_PIPELINE_PATH = os.path.join(_APP, "services", "director_pipeline.py")
_LEFTOVER_HELPERS = (
    "_normalize_director_media_strengths",
    "_director_uses_fixed_media_strength",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TestContinuumDirectorMediaStrengthGates(unittest.TestCase):
    def test_pipeline_does_not_restore_leftover_media_strength_helpers(self):
        source = _read(_PIPELINE_PATH)

        # Leftover 1.9.0 forced input_video_strength and audio_scale to
        # 1.0 for every H3 / LTX-2.5 Director job. Continuum dropped
        # that rewrite and keeps submitted strengths.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(pipeline, name))
                self.assertNotIn(f"def {name}(", source)

    def test_continuum_helpers_keep_snapshot_not_leftover_strength_lock(self):
        source = _read(_PIPELINE_PATH)
        self.assertIn("def _create_director_video_execution_profile(", source)
        self.assertIn("def _prepare_director_h3_longform(", source)
        for name in _LEFTOVER_HELPERS:
            self.assertNotIn(f"def {name}(", source)

    def test_execution_profile_preserves_submitted_media_strengths(self):
        # Leftover normalize rewrote H3/LTX-2.5 strengths in place.
        # Continuum's snapshot hook records canvas/max-frames only.
        params = {
            "video_model": "minimax_h3_ref2va",
            "video_params": {
                "resolution": "1280x720",
                "input_video_strength": 0.25,
            },
            "audio_scale": 3.0,
            "director_max_shot_frames": 81,
        }
        profile = pipeline._create_director_video_execution_profile(params)
        self.assertTrue(profile["is_minimax_h3"])
        self.assertEqual(params["video_params"]["input_video_strength"], 0.25)
        self.assertEqual(params["audio_scale"], 3.0)
        self.assertNotIn("input_video_strength", profile)
        self.assertNotIn("audio_scale", profile)
        self.assertFalse(hasattr(pipeline, "_normalize_director_media_strengths"))

    def test_longform_prepare_does_not_stamp_leftover_strength_lock(self):
        gen_params = {
            "model_type": "ltx2_25_dev",
            "prompt": "A closed door.",
            "video_params": {"input_video_strength": 0.4},
            "audio_scale": 2.5,
        }
        original = {
            "video_params": dict(gen_params["video_params"]),
            "audio_scale": gen_params["audio_scale"],
        }
        restored = pipeline._prepare_director_h3_longform(
            gen_params,
            params={"video_model": "ltx2_25_dev"},
            clip_plans=[{"video_prompt": "A closed door."}],
            planned_clips=[{"duration_sec": 4.0}],
            fps=24,
        )
        self.assertIsNone(restored)
        self.assertEqual(
            gen_params["video_params"]["input_video_strength"],
            original["video_params"]["input_video_strength"],
        )
        self.assertEqual(gen_params["audio_scale"], original["audio_scale"])
        self.assertFalse(hasattr(pipeline, "_director_uses_fixed_media_strength"))

    def test_runtime_bind_fail_closed_without_leftover_strength_normalize(self):
        plan = {
            "model_type": "minimax_h3_ref2va",
            "shot_plan": {"version": 0, "semantic_physical_contract_version": 1},
            "video_params": {"input_video_strength": 0.3},
            "audio_scale": 1.8,
        }
        pipeline._bind_director_h3_runtime_contract(plan)
        self.assertNotIn("director_runtime_contract", plan["shot_plan"])
        self.assertEqual(plan["video_params"]["input_video_strength"], 0.3)
        self.assertEqual(plan["audio_scale"], 1.8)
        self.assertFalse(hasattr(pipeline, "_normalize_director_media_strengths"))


if __name__ == "__main__":
    unittest.main()
