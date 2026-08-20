"""Continuum Director max-frames snapshot helpers.

Locks leftover 1.9.0 `_director_effective_max_frames` /
`_director_native_window_frames` probes onto Continuum
`_create_director_video_execution_profile` and
`_prepare_director_h3_longform`. Do not invent leftover hardware-profile
or 345-frame fallbacks, and do not restore those helpers.
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
    "_director_effective_max_frames",
    "_director_native_window_frames",
)
_LEFTOVER_RECONNECTS = (
    "frames_maximum",
    "sliding_window_size",
    "video_length",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _hook_source() -> str:
    source = _read(_PIPELINE_PATH)
    start = source.index("def _create_director_video_execution_profile(")
    end = source.index("\ndef start_pipeline(", start)
    return source[start:end]


class TestContinuumDirectorEffectiveMaxFramesGates(unittest.TestCase):
    def test_pipeline_does_not_restore_leftover_max_frame_helpers(self):
        source = _read(_PIPELINE_PATH)

        # Leftover 1.9.0 read effective_max_frames from a hardware
        # execution profile, then fell back to model_def.frames_maximum
        # or 345. Continuum dropped that helper and keeps a snapshot.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(pipeline, name))
                self.assertNotIn(f"def {name}(", source)

    def test_continuum_helpers_keep_snapshot_not_leftover_frame_fallback(self):
        source = _read(_PIPELINE_PATH)
        hook = _hook_source()
        self.assertIn("def _create_director_video_execution_profile(", source)
        self.assertIn("def _prepare_director_h3_longform(", source)
        self.assertIn("director_max_shot_frames", hook)
        self.assertNotIn("345", hook)
        for leftover in _LEFTOVER_HELPERS + _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=leftover):
                self.assertNotIn(leftover, hook)

    def test_execution_profile_snapshots_manual_ceiling_not_leftover_345(self):
        # Leftover _director_effective_max_frames invented 345 when the
        # profile and model_def omitted frames_maximum. Continuum copies
        # the submitted Director ceiling only.
        params = {
            "video_model": "minimax_h3_ref2va",
            "video_params": {"resolution": "1280x720"},
            "director_max_shot_frames": 81,
        }
        profile = pipeline._create_director_video_execution_profile(
            params,
            model_def={"frames_maximum": 345},
        )
        self.assertEqual(profile["effective_max_frames"], 81)
        self.assertTrue(profile["manual_override"])
        self.assertEqual(params["_director_video_execution_profile"], profile)
        self.assertFalse(hasattr(pipeline, "_director_effective_max_frames"))

    def test_missing_manual_ceiling_stays_empty_not_leftover_fallback(self):
        params = {
            "video_model": "minimax_h3_ref2va",
            "video_params": {"resolution": "1280x720"},
        }
        profile = pipeline._create_director_video_execution_profile(
            params,
            model_def={"frames_maximum": 345, "settings": {"sliding_window_size": 121}},
        )
        self.assertIsNone(profile["effective_max_frames"])
        self.assertFalse(profile["manual_override"])
        self.assertFalse(hasattr(pipeline, "_director_native_window_frames"))

    def test_longform_prepare_fail_closed_without_leftover_max_frames(self):
        gen_params = {
            "model_type": "ltx2_25_dev",
            "prompt": "A closed door.",
        }
        original = dict(gen_params)
        restored = pipeline._prepare_director_h3_longform(
            gen_params,
            params={"video_model": "ltx2_25_dev"},
            clip_plans=[{"video_prompt": "A closed door."}],
            planned_clips=[{"duration_sec": 4.0}],
            fps=24,
        )
        self.assertIsNone(restored)
        self.assertEqual(gen_params, original)
        self.assertNotIn("effective_max_frames", gen_params)
        self.assertNotIn("sliding_window_size", gen_params)
        self.assertFalse(hasattr(pipeline, "_director_effective_max_frames"))

    def test_runtime_bind_fail_closed_without_leftover_native_window(self):
        plan = {
            "model_type": "minimax_h3_ref2va",
            "shot_plan": {"version": 0, "semantic_physical_contract_version": 1},
        }
        pipeline._bind_director_h3_runtime_contract(plan)
        self.assertNotIn("director_runtime_contract", plan["shot_plan"])
        self.assertNotIn("effective_max_frames", plan)
        self.assertNotIn("sliding_window_size", plan)
        self.assertFalse(hasattr(pipeline, "_director_native_window_frames"))


if __name__ == "__main__":
    unittest.main()
