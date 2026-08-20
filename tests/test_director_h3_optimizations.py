"""Continuum Director H3 optimization helpers.

Locks leftover 1.9.0 `_apply_director_h3_optimizations` /
`_saved_director_video_execution_profile` /
`_validate_saved_profile_for_current_hardware` probes onto Continuum
`_attach_director_h3_shot_contracts`, `_bind_director_h3_runtime_contract`,
and `_rehydrate_director_h3_longform`. Do not invent leftover Turbo / Sol /
First Block Cache reconnects or restore those helpers.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import director_pipeline as pipeline  # noqa: E402


_PIPELINE_PATH = os.path.join(_APP, "services", "director_pipeline.py")
_LEFTOVER_HELPERS = (
    "_apply_director_h3_optimizations",
    "_saved_director_video_execution_profile",
    "_validate_saved_profile_for_current_hardware",
)
_LEFTOVER_RECONNECTS = (
    "override_attention",
    "skip_steps_cache_type",
    "skip_steps_multiplier",
    "skip_steps_start_step_perc",
    "minimax_h3_turbo_preset",
    "first_block",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TestContinuumDirectorH3OptimizationGates(unittest.TestCase):
    def test_pipeline_does_not_restore_leftover_optimization_helpers(self):
        source = _read(_PIPELINE_PATH)

        # Leftover 1.9.0 copied Turbo / Sol / First Block Cache from a
        # hardware-revalidated saved execution profile onto every child job.
        # Continuum dropped that reconnect.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(pipeline, name))
                self.assertNotIn(f"def {name}(", source)
        for name in _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=name):
                self.assertNotIn(name, source)

    def test_continuum_helpers_keep_shot_and_runtime_contracts(self):
        source = _read(_PIPELINE_PATH)
        self.assertIn("def _attach_director_h3_shot_contracts(", source)
        self.assertIn("def _bind_director_h3_runtime_contract(", source)
        self.assertIn("def _validate_director_h3_runtime_contract(", source)
        self.assertIn("def _rehydrate_director_h3_longform(", source)
        self.assertIn("_DIRECTOR_H3_RUNTIME_CONTRACT_FIELDS", source)
        self.assertNotIn("def _apply_director_h3_optimizations(", source)
        self.assertNotIn("def _saved_director_video_execution_profile(", source)
        self.assertNotIn(
            "def _validate_saved_profile_for_current_hardware(",
            source,
        )
        for leftover in _LEFTOVER_RECONNECTS:
            self.assertNotIn(leftover, pipeline._DIRECTOR_H3_RUNTIME_CONTRACT_FIELDS)

    def test_shot_contract_keeps_story_fields_not_leftover_turbo(self):
        shot = SimpleNamespace(
            shot_id="shot-opt",
            continuity_strategy="independent",
            environment="a quiet hallway",
            visual_style="steady handheld",
            lighting="cool fluorescent",
            spatial_setup="camera facing the door",
            subjects_on_screen=[],
            dialogue_beats=[],
            ending_beat="the door stays closed",
            audio_plan=None,
            metadata={},
        )
        clip_plans = [{"video_prompt": "A closed door.", "image_prompt": ""}]
        planned = [{"duration_sec": 4.0}]
        pipeline._attach_director_h3_shot_contracts(
            clip_plans, planned, [shot],
        )
        contract = clip_plans[0]["_h3_shot"]
        self.assertEqual(contract["shot_id"], "shot-opt")
        self.assertEqual(contract["closing_blocking"], "the door stays closed")
        self.assertEqual(planned[0]["_h3_shot"], contract)
        for leftover in (
            "turbo_mode",
            "minimax_h3_turbo_mode",
            "override_attention",
            "skip_steps_cache_type",
            "video_execution_profile",
            "lora_weights",
        ):
            self.assertNotIn(leftover, contract)

    def test_runtime_bind_and_rehydrate_fail_closed_without_leftover_apply(self):
        plan = {
            "model_type": "minimax_h3_ref2va",
            "shot_plan": {"version": 0, "semantic_physical_contract_version": 1},
        }
        pipeline._bind_director_h3_runtime_contract(plan)
        self.assertNotIn("director_runtime_contract", plan["shot_plan"])
        self.assertNotIn("minimax_h3_turbo_mode", plan)
        self.assertNotIn("override_attention", plan)

        gen_params = {"prompt": "A closed door."}
        restored = pipeline._rehydrate_director_h3_longform(gen_params, plan)
        self.assertFalse(restored)
        self.assertNotIn("minimax_h3_turbo_mode", gen_params)
        self.assertNotIn("override_attention", gen_params)
        self.assertNotIn("skip_steps_cache_type", gen_params)
        self.assertNotIn("_director_video_execution_profile", gen_params)


if __name__ == "__main__":
    unittest.main()
