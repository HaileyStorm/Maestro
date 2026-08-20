"""Continuum Director H3 preflight and child-job prepare helpers.

Locks leftover 1.9.0 `_preflight_h3_director_prompts` /
`_prepare_director_generation_params` probes onto Continuum
`_director_h3_canonical_prompt`, `_prepare_director_h3_longform`,
and `_bind_director_h3_runtime_contract`. Do not invent leftover
official-compile GPU preflight, sliding-window memory overrides, or
Turbo normalize reconnects, and do not restore those helpers.
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
    "_preflight_h3_director_prompts",
    "_prepare_director_generation_params",
)
_LEFTOVER_RECONNECTS = (
    "compile_h3_clip_plans",
    "sliding_window_memory_override",
    "normalize_minimax_h3_turbo_request",
    "validate_director_execution_frames",
    "reference_manifests",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TestContinuumDirectorH3PreflightGates(unittest.TestCase):
    def test_pipeline_does_not_restore_leftover_preflight_helpers(self):
        source = _read(_PIPELINE_PATH)

        # Leftover 1.9.0 compiled official Context-IR through
        # compile_h3_clip_plans before GPU work, then stamped
        # sliding_window_memory_override and Turbo normalize onto
        # every H3 child job. Continuum dropped that reconnect.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(pipeline, name))
                self.assertNotIn(f"def {name}(", source)
        for name in _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=name):
                self.assertNotIn(name, source)

    def test_continuum_helpers_keep_canonical_prompt_and_longform(self):
        source = _read(_PIPELINE_PATH)
        self.assertIn("def _director_h3_canonical_prompt(", source)
        self.assertIn("def _prepare_director_h3_longform(", source)
        self.assertIn("def _bind_director_h3_runtime_contract(", source)
        self.assertIn("def _rehydrate_director_h3_longform(", source)
        for name in _LEFTOVER_HELPERS:
            self.assertNotIn(f"def {name}(", source)

    def test_canonical_prompt_fail_closed_without_leftover_compile(self):
        # Leftover preflight auto-set Ref2VA modes and compiled an
        # official prompt from a loose source. Continuum validates
        # Ref2VA in place and fails closed instead of manufacturing
        # official fields.
        with self.assertRaisesRegex(
            ValueError, "Ref2VA prompt validation failed",
        ):
            pipeline._director_h3_canonical_prompt(
                "A closed door.",
                duration_seconds=4.0,
                mode="ref2va",
            )
        self.assertFalse(hasattr(pipeline, "_preflight_h3_director_prompts"))
        self.assertFalse(hasattr(pipeline, "compile_h3_clip_plans"))

    def test_longform_prepare_does_not_stamp_leftover_child_overrides(self):
        gen_params = {
            "model_type": "ltx2_22B_distilled",
            "prompt": "A closed door.",
        }
        original = dict(gen_params)
        restored = pipeline._prepare_director_h3_longform(
            gen_params,
            params={"minimax_h3_turbo_mode": True},
            clip_plans=[{"video_prompt": "A closed door."}],
            planned_clips=[{"duration_sec": 4.0}],
            fps=24,
        )
        self.assertIsNone(restored)
        self.assertEqual(gen_params, original)
        for leftover in (
            "sliding_window_memory_override",
            "minimax_h3_turbo_mode",
            "normalize_minimax_h3_turbo_request",
            "_director_h3_reference_manifest",
        ):
            self.assertNotIn(leftover, gen_params)

    def test_runtime_bind_fail_closed_without_leftover_prepare(self):
        plan = {
            "model_type": "minimax_h3_ref2va",
            "shot_plan": {"version": 0, "semantic_physical_contract_version": 1},
        }
        pipeline._bind_director_h3_runtime_contract(plan)
        self.assertNotIn("director_runtime_contract", plan["shot_plan"])
        self.assertNotIn("sliding_window_memory_override", plan)
        self.assertNotIn("minimax_h3_turbo_mode", plan)
        self.assertNotIn("reference_manifests", plan)

        gen_params = {"prompt": "A closed door."}
        restored = pipeline._rehydrate_director_h3_longform(gen_params, plan)
        self.assertFalse(restored)
        self.assertNotIn("sliding_window_memory_override", gen_params)
        self.assertNotIn("minimax_h3_turbo_mode", gen_params)
        self.assertNotIn("_director_h3_reference_manifest", gen_params)


if __name__ == "__main__":
    unittest.main()
