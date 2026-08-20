"""Continuum Director execution-profile snapshot helpers.

Locks leftover 1.9.0 `_director_video_execution_profile` getter probes onto
Continuum `_create_director_video_execution_profile` and
`_prepare_director_h3_longform`. Do not invent leftover empty-dict copies
or restore that helper.
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
    "_director_video_execution_profile",
)
_LEFTOVER_RECONNECTS = (
    "use_director_v2",
    "return dict(profile)",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _hook_source() -> str:
    source = _read(_PIPELINE_PATH)
    start = source.index("def _create_director_video_execution_profile(")
    end = source.index("\ndef start_pipeline(", start)
    return source[start:end]


class TestContinuumDirectorVideoExecutionProfileGates(unittest.TestCase):
    def test_pipeline_does_not_restore_leftover_profile_getter(self):
        source = _read(_PIPELINE_PATH)

        # Leftover 1.9.0 copied params["_director_video_execution_profile"]
        # through `_director_video_execution_profile`, inventing {} when
        # the snapshot was missing. Continuum dropped that getter.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(pipeline, name))
                self.assertNotIn(f"def {name}(", source)

    def test_continuum_helpers_keep_snapshot_not_leftover_getter(self):
        source = _read(_PIPELINE_PATH)
        hook = _hook_source()
        self.assertIn("def _create_director_video_execution_profile(", source)
        self.assertIn("def _prepare_director_h3_longform(", source)
        self.assertIn('params["_director_video_execution_profile"] = profile', hook)
        self.assertNotIn("def _director_video_execution_profile(", source)
        self.assertNotIn("def _director_video_execution_profile(", hook)
        for leftover in _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=leftover):
                self.assertNotIn(leftover, hook)

    def test_create_hook_stamps_key_without_leftover_getter_copy(self):
        # Leftover getter returned dict(profile) so callers could mutate a
        # copy. Continuum's hook stores the live snapshot object only.
        params = {
            "video_model": "minimax_h3_ref2va",
            "video_params": {"resolution": "1280x720"},
            "director_max_shot_frames": 81,
        }
        profile = pipeline._create_director_video_execution_profile(params)
        self.assertIs(params["_director_video_execution_profile"], profile)
        self.assertEqual(profile["normalized_resolution"], "1280x720")
        self.assertEqual(profile["effective_max_frames"], 81)
        self.assertFalse(hasattr(pipeline, "_director_video_execution_profile"))

    def test_missing_snapshot_stays_absent_not_leftover_empty_dict(self):
        params = {"video_model": "ltx2_25_dev"}
        self.assertNotIn("_director_video_execution_profile", params)
        self.assertFalse(hasattr(pipeline, "_director_video_execution_profile"))
        self.assertIsNone(params.get("_director_video_execution_profile"))

    def test_longform_prepare_fail_closed_without_leftover_profile_getter(self):
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
        self.assertNotIn("_director_video_execution_profile", gen_params)
        self.assertNotIn("use_director_v2", gen_params)
        self.assertFalse(hasattr(pipeline, "_director_video_execution_profile"))

    def test_runtime_bind_fail_closed_without_leftover_profile_getter(self):
        plan = {
            "model_type": "minimax_h3_ref2va",
            "shot_plan": {"version": 0, "semantic_physical_contract_version": 1},
        }
        pipeline._bind_director_h3_runtime_contract(plan)
        self.assertNotIn("director_runtime_contract", plan["shot_plan"])
        self.assertNotIn("_director_video_execution_profile", plan)
        self.assertNotIn("use_director_v2", plan)
        self.assertFalse(hasattr(pipeline, "_director_video_execution_profile"))


if __name__ == "__main__":
    unittest.main()
