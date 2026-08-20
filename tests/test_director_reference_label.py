"""Continuum Director visual-reference helpers.

Locks leftover 1.9.0 `_director_reference_label` probes onto Continuum
`_director_visual_reference_paths` and `_director_role_prompt`. Do not
invent leftover "character 1" / character-name fallbacks, and do not
restore that helper.
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
    "_director_reference_label",
)
_LEFTOVER_RECONNECTS = (
    "character 1",
    "location 1",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _visual_helpers_source() -> str:
    source = _read(_PIPELINE_PATH)
    start = source.index("def _director_visual_reference_paths(")
    end = source.index("\ndef _director_effective_shot_image_policy(", start)
    return source[start:end]


class TestContinuumDirectorReferenceLabelGates(unittest.TestCase):
    def test_pipeline_does_not_restore_leftover_reference_label_helper(self):
        source = _read(_PIPELINE_PATH)

        # Leftover 1.9.0 compiled Ref2VA role text through
        # `_director_reference_label`, falling back to character names or
        # "character 1" / "location 1". Continuum dropped that helper.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(pipeline, name))
                self.assertNotIn(f"def {name}(", source)

    def test_continuum_helpers_keep_paths_and_lora_roles_not_leftover_labels(self):
        source = _read(_PIPELINE_PATH)
        hook = _visual_helpers_source()
        self.assertIn("def _director_visual_reference_paths(", source)
        self.assertIn("def _director_role_prompt(", source)
        self.assertIn("character_ref_labels", source)
        self.assertNotIn("def _director_reference_label(", source)
        for leftover in _LEFTOVER_HELPERS + _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=leftover):
                self.assertNotIn(leftover, hook)

    def test_visual_refs_stay_paths_not_leftover_character_labels(self):
        # Leftover `_director_reference_label` invented "Hero" from
        # characters[] or "character 1" when labels were missing.
        # Continuum's path helper ignores labels and names.
        params = {
            "reference_image_path": "/tmp/cast.png",
            "character_ref_paths": ["/tmp/hero.png", ""],
            "character_ref_labels": ["Hero"],
            "characters": [{"name": "Hero"}],
            "location_ref_paths": ["/tmp/hallway.png"],
            "location_ref_labels": [],
        }
        paths = pipeline._director_visual_reference_paths(params)
        self.assertEqual(
            paths,
            ["/tmp/cast.png", "/tmp/hero.png", "/tmp/hallway.png"],
        )
        self.assertNotIn("Hero", paths)
        for leftover in _LEFTOVER_RECONNECTS:
            self.assertNotIn(leftover, paths)
        self.assertFalse(hasattr(pipeline, "_director_reference_label"))

    def test_role_prompt_keeps_lora_fragments_not_leftover_character_noun(self):
        loras = {
            "parameter_expansions": [
                {"text": "cool fluorescent", "scopes": ("generation",)},
                {"text": "ignore me", "scopes": ("editing",)},
            ],
        }
        prompt = pipeline._director_role_prompt("A closed door.", loras, "creator")
        self.assertEqual(prompt, "A closed door., cool fluorescent")
        self.assertNotIn("character 1", prompt)
        self.assertNotIn("Hero", prompt)
        self.assertFalse(hasattr(pipeline, "_director_reference_label"))

    def test_keyframe_normalize_fail_closed_without_leftover_role_labels(self):
        gen_params = {
            "image_refs": ["cast.png"],
            "frames_positions": [0],
            "per_clip_keyframes": [["hallway.png", "cast.png"]],
        }
        refs = pipeline._normalize_director_h3_keyframe_refs(gen_params)
        self.assertEqual(refs, ["cast.png", "hallway.png"])
        self.assertEqual(gen_params["image_refs"], ["cast.png", "hallway.png"])
        self.assertNotIn("role", gen_params)
        self.assertNotIn("character_ref_labels", gen_params)
        self.assertNotIn("location_ref_labels", gen_params)
        for leftover in _LEFTOVER_RECONNECTS:
            self.assertNotIn(leftover, refs)

    def test_longform_prepare_fail_closed_without_leftover_reference_label(self):
        gen_params = {
            "model_type": "ltx2_25_dev",
            "prompt": "A closed door.",
        }
        original = dict(gen_params)
        restored = pipeline._prepare_director_h3_longform(
            gen_params,
            params={
                "video_model": "ltx2_25_dev",
                "character_ref_labels": ["Hero"],
            },
            clip_plans=[{"video_prompt": "A closed door."}],
            planned_clips=[{"duration_sec": 4.0}],
            fps=24,
        )
        self.assertIsNone(restored)
        self.assertEqual(gen_params, original)
        self.assertNotIn("character_ref_labels", gen_params)
        self.assertNotIn("reference_manifest", gen_params)
        self.assertFalse(hasattr(pipeline, "_director_reference_label"))


if __name__ == "__main__":
    unittest.main()
