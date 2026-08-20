"""Continuum Director H3 frame-lattice and reference helpers.

Locks leftover 1.9.0 `_repair_saved_h3_frame_lattice` /
`_director_h3_reference_manifest` /
`_extract_director_continuation_frame` /
`_director_same_logical_scene` probes onto Continuum
`_quantize_clip_frame_schedule`, `_director_visual_reference_paths`,
`_normalize_director_h3_keyframe_refs`, `_attach_director_h3_shot_contracts`,
and `_rehydrate_director_h3_longform`. Do not invent leftover saved-timeline
repairs, audio-role manifests, or last-frame PNG handoffs, and do not restore
those helpers.
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
    "_repair_saved_h3_frame_lattice",
    "_director_h3_reference_manifest",
    "_extract_director_continuation_frame",
    "_director_same_logical_scene",
)
_LEFTOVER_RECONNECTS = (
    "_h3_frame_lattice_repair",
    "drive_audio_path",
    "voice_sample",
    "_director_continuity_group",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TestContinuumDirectorH3FrameLatticeGates(unittest.TestCase):
    def test_pipeline_does_not_restore_leftover_lattice_helpers(self):
        source = _read(_PIPELINE_PATH)

        # Leftover 1.9.0 silently upgraded saved 24 fps counts onto H3's
        # 124-then-17 lattice, compiled one Ref2VA image/audio manifest, and
        # extracted a last-frame PNG when adjacent shots shared a continuity
        # group. Continuum dropped that reconnect.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(pipeline, name))
                self.assertNotIn(f"def {name}(", source)

    def test_continuum_helpers_keep_live_quantize_and_visual_refs(self):
        source = _read(_PIPELINE_PATH)
        self.assertIn("def _quantize_clip_frame_schedule(", source)
        self.assertIn("def _director_visual_reference_paths(", source)
        self.assertIn("def _normalize_director_h3_keyframe_refs(", source)
        self.assertIn("def _attach_director_h3_shot_contracts(", source)
        self.assertIn("def _rehydrate_director_h3_longform(", source)
        for name in _LEFTOVER_HELPERS:
            self.assertNotIn(f"def {name}(", source)

    def test_quantize_uses_live_carry_not_leftover_saved_repair(self):
        # Leftover repair mapped [120, 144] to [124, 141] and wrote
        # `_h3_frame_lattice_repair` onto saved state. Continuum only
        # quantizes the live request with min/step carry.
        self.assertEqual(
            pipeline._quantize_clip_frame_schedule([120, 144], 124, 17),
            [124, 137],
        )
        self.assertNotEqual(
            pipeline._quantize_clip_frame_schedule([120, 144], 124, 17),
            [124, 141],
        )
        self.assertFalse(hasattr(pipeline, "_repair_saved_h3_frame_lattice"))

    def test_visual_refs_stay_paths_not_leftover_audio_manifest(self):
        params = {
            "reference_image_path": "/tmp/cast.png",
            "character_ref_paths": ["/tmp/hero.png", ""],
            "location_ref_paths": ["/tmp/hallway.png"],
            "drive_audio_path": "/tmp/song.wav",
        }
        paths = pipeline._director_visual_reference_paths(params)
        self.assertEqual(
            paths,
            ["/tmp/cast.png", "/tmp/hero.png", "/tmp/hallway.png"],
        )
        for leftover in _LEFTOVER_RECONNECTS:
            self.assertNotIn(leftover, paths)

        gen_params = {
            "image_refs": ["cast.png"],
            "frames_positions": [0],
            "per_clip_keyframes": [["hallway.png", "cast.png"]],
        }
        refs = pipeline._normalize_director_h3_keyframe_refs(gen_params)
        self.assertEqual(refs, ["cast.png", "hallway.png"])
        self.assertEqual(gen_params["image_refs"], ["cast.png", "hallway.png"])
        self.assertNotIn("frames_positions", gen_params)
        self.assertNotIn("per_clip_keyframes", gen_params)
        self.assertEqual(
            gen_params["custom_settings"]["h3_director_keyframes"],
            "semantic_references",
        )
        self.assertNotIn("role", gen_params)
        self.assertNotIn("intent", gen_params)
        self.assertNotIn("drive_audio_path", gen_params)

    def test_shot_contract_keeps_continuity_not_leftover_last_frame(self):
        shot = SimpleNamespace(
            shot_id="shot-lattice",
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
        self.assertEqual(contract["continuity_strategy"], "independent")
        self.assertEqual(contract["shot_id"], "shot-lattice")
        for leftover in (
            "_director_continuity_group",
            "continuation_frame",
            "last_frame_png",
            "drive_audio_path",
            "_h3_frame_lattice_repair",
        ):
            self.assertNotIn(leftover, contract)

    def test_rehydrate_fail_closed_without_leftover_lattice_repair(self):
        plan = {
            "model_type": "minimax_h3_ref2va",
            "shot_plan": {"version": 0, "semantic_physical_contract_version": 1},
            "clip_frames": [120, 144],
            "planned_frames": 264,
        }
        gen_params = {"prompt": "A closed door."}
        restored = pipeline._rehydrate_director_h3_longform(gen_params, plan)
        self.assertFalse(restored)
        self.assertNotIn("_h3_frame_lattice_repair", plan)
        self.assertNotIn("_h3_frame_lattice_repair", gen_params)
        self.assertNotIn("continuation_frame", gen_params)
        self.assertEqual(plan["clip_frames"], [120, 144])


if __name__ == "__main__":
    unittest.main()
