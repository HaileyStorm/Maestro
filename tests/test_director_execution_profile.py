"""Continuum Director video execution-profile helpers.

Locks leftover 1.9.0 `_director_hardware_snapshot` / canvas-normalize /
Turbo-preset reconnects onto Continuum's snapshot-only pipeline hook plus
`build_director_video_execution_profile`. Do not invent leftover hardware
canvas rewrites or restore `_director_hardware_snapshot`.
"""
from __future__ import annotations

import os
import sys
import unittest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.director_pipeline import (  # noqa: E402
    _create_director_video_execution_profile,
)
from services.director_video_strategy import (  # noqa: E402
    BOUNDED_START_END,
    build_director_video_execution_profile,
)


_PIPELINE_PATH = os.path.join(_APP, "services", "director_pipeline.py")
_STRATEGY_PATH = os.path.join(_APP, "services", "director_video_strategy.py")
_LEFTOVER_NAMES = (
    "_director_hardware_snapshot",
    "_get_cached_hardware",
    "activated_lora_count",
    "lora_strength",
    "lora_weights",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _hook_source() -> str:
    source = _read(_PIPELINE_PATH)
    start = source.index("def _create_director_video_execution_profile(")
    end = source.index("\ndef start_pipeline(", start)
    return source[start:end]


class TestContinuumDirectorExecutionProfileGates(unittest.TestCase):
    def test_pipeline_does_not_restore_leftover_hardware_snapshot(self):
        source = _read(_PIPELINE_PATH)
        hook = _hook_source()

        # Leftover 1.9.0 rebuilt the live profile through
        # _director_hardware_snapshot / _get_cached_hardware and then
        # rewrote the submitted canvas plus Turbo presets. Continuum
        # dropped that reconnect and kept a snapshot stub.
        self.assertFalse(
            hasattr(
                sys.modules["services.director_pipeline"],
                "_director_hardware_snapshot",
            )
        )
        self.assertNotIn("def _director_hardware_snapshot(", source)
        self.assertNotIn("from launch import _get_cached_hardware", source)
        self.assertNotIn("build_director_video_execution_profile(", hook)
        for name in _LEFTOVER_NAMES:
            with self.subTest(leftover=name):
                self.assertNotIn(name, hook)

    def test_continuum_helpers_keep_strategy_builder_not_pipeline_rewrite(self):
        source = _read(_PIPELINE_PATH)
        strategy = _read(_STRATEGY_PATH)
        self.assertIn("def _create_director_video_execution_profile(", source)
        self.assertIn("1.9.0 used this hook for hardware-normalized canvas", source)
        self.assertIn("def build_director_video_execution_profile(", strategy)
        self.assertIn("DIRECTOR_VIDEO_EXECUTION_PROFILE_VERSION", strategy)
        self.assertNotIn("def _director_hardware_snapshot(", source)
        self.assertNotIn("def _director_hardware_snapshot(", strategy)

    def test_pipeline_hook_copies_requested_canvas_and_refuses_leftover_turbo(self):
        params = {
            "video_model": "minimax_h3_ref2va",
            "video_params": {
                "resolution": "1280x720",
                "minimax_h3_turbo_mode": True,
            },
            "director_max_shot_frames": 81,
        }
        profile = _create_director_video_execution_profile(
            params,
            hardware={"gpu_vram_gb": 8},
        )
        self.assertTrue(profile["is_minimax_h3"])
        self.assertEqual(profile["normalized_resolution"], "1280x720")
        self.assertEqual(profile["gpu_vram_gb"], 8.0)
        self.assertEqual(profile["effective_max_frames"], 81)
        self.assertTrue(profile["manual_override"])
        self.assertFalse(profile["turbo_mode"])
        self.assertIs(params["_director_video_execution_profile"], profile)
        for leftover in (
            "checkpoint",
            "omni_reference",
            "activated_lora_count",
            "lora_strength",
            "lora_weights",
            "video_strategy",
        ):
            self.assertNotIn(leftover, profile)

    def test_live_strategy_profile_omits_leftover_turbo_and_lora_keys(self):
        profile = build_director_video_execution_profile(
            "minimax_h3",
            {
                "architecture": "minimax_h3",
                "fps": 24,
                "frames_minimum": 1,
                "frames_steps": 1,
                "frames_maximum": 345,
            },
            {
                "resolution": "768x768",
                "minimax_h3_turbo_mode": True,
                "activated_loras": ["obsolete.safetensors"],
                "loras_multipliers": "0.75",
            },
            {"gpu_vram_gb": 24},
        )
        self.assertEqual(profile["video_strategy"], BOUNDED_START_END)
        self.assertEqual(profile["requested_resolution"], "768x768")
        self.assertNotIn("turbo_mode", profile)
        self.assertNotIn("checkpoint", profile)
        self.assertNotIn("omni_reference", profile)
        self.assertNotIn("activated_lora_count", profile)
        self.assertNotIn("lora_strength", profile)
        self.assertNotIn("lora_weights", profile)


if __name__ == "__main__":
    unittest.main()
