"""Continuum Director model-compat helpers.

Locks leftover 1.9.0 `supports_director_seamless` / `OMNI_REFERENCE` /
`director_audio_input_mode` probes onto Continuum `assess_director_model`,
`_image_creator_capability`, and `_image_editor_capability`. Do not invent
the leftover omni/seamless helpers or restore a combined-only image result.
"""
from __future__ import annotations

import os
import sys
import unittest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.director_model_compat import (  # noqa: E402
    DIRECTOR_PIPELINE_TYPES,
    assess_director_model,
)
from services.director_video_strategy import (  # noqa: E402
    BOUNDED_START_END,
    ROLLING_WINDOW,
)


_COMPAT_PATH = os.path.join(_APP, "services", "director_model_compat.py")
_LEFTOVER_NAMES = (
    "supports_director_seamless",
    "OMNI_REFERENCE",
    "director_audio_input_mode",
    "native_voice_reference",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _image_editor(**updates):
    model_def = {
        "name": "Reference editor",
        "image_outputs": True,
        "image_ref_choices": {
            "choices": [("None", ""), ("Main plus references", "KI")],
        },
    }
    model_def.update(updates)
    return model_def


def _ltx_video(**updates):
    model_def = {
        "name": "LTX",
        "image_prompt_types_allowed": "TSEV",
        "sliding_window": True,
        "any_audio_prompt": True,
        "returns_audio": True,
        "audio_guide_window_slicing": True,
        "custom_frames_injection": True,
        "auto_null_audio": True,
    }
    model_def.update(updates)
    return model_def


class TestContinuumDirectorModelCompatGates(unittest.TestCase):
    def test_compat_does_not_restore_leftover_omni_seamless_helpers(self):
        source = _read(_COMPAT_PATH)

        # Leftover 1.9.0 gated seamless with supports_director_seamless,
        # exempted extra refs via OMNI_REFERENCE, and treated
        # director_audio_input_mode=reference_manifest as soundtrack input.
        # Continuum dropped those leftover helpers.
        self.assertFalse(hasattr(assess_director_model, "supports_director_seamless"))
        self.assertNotIn("def supports_director_seamless(", source)
        for name in _LEFTOVER_NAMES:
            with self.subTest(leftover=name):
                self.assertNotIn(name, source)

    def test_continuum_helpers_split_image_roles_not_leftover_combined_only(self):
        source = _read(_COMPAT_PATH)
        self.assertIn("def assess_director_model(", source)
        self.assertIn("def _image_creator_capability(", source)
        self.assertIn("def _image_editor_capability(", source)
        self.assertIn("def _seamless_capability(", source)
        self.assertIn("DIRECTOR_PIPELINE_TYPES", source)
        self.assertEqual(
            DIRECTOR_PIPELINE_TYPES,
            ("music_video", "short_film_audio", "short_film_story"),
        )
        self.assertIn("creator", source)
        self.assertIn("editor", source)
        self.assertIn("video_strategy(model_def) != ROLLING_WINDOW", source)
        self.assertIn("custom_frames_injection", source)

    def test_unavailable_model_exposes_creator_and_editor_roles(self):
        result = assess_director_model("missing", None)
        self.assertFalse(result["image"]["compatible"])
        self.assertFalse(result["image"]["creator"]["compatible"])
        self.assertFalse(result["image"]["editor"]["compatible"])
        self.assertIn("unavailable", result["image"]["reason"])
        self.assertFalse(result["supports_audio_input"])
        self.assertEqual(result["voice_reference_mode"], "none")
        self.assertEqual(result["video_strategy"], ROLLING_WINDOW)
        for workflow in (*DIRECTOR_PIPELINE_TYPES, "seamless"):
            self.assertFalse(result["video"][workflow]["compatible"])

    def test_reference_editor_keeps_independent_creator_and_editor_roles(self):
        result = assess_director_model("editor", _image_editor())
        image = result["image"]
        self.assertTrue(image["compatible"])
        self.assertTrue(image["creator"]["compatible"])
        self.assertTrue(image["editor"]["compatible"])
        self.assertEqual(image["creator"]["reasons"], [])
        self.assertEqual(image["editor"]["reasons"], [])

    def test_plain_image_model_is_creator_only_not_combined_leftover_pass(self):
        result = assess_director_model(
            "plain",
            {"name": "Plain", "image_outputs": True},
        )
        image = result["image"]
        self.assertTrue(image["creator"]["compatible"])
        self.assertFalse(image["editor"]["compatible"])
        self.assertFalse(image["compatible"])
        self.assertIn("reference editing", image["reason"])
        self.assertIn("reference editing", image["editor"]["reasons"][0])

    def test_editor_that_cannot_bootstrap_rejects_creator_role(self):
        result = assess_director_model(
            "edit-only",
            _image_editor(image_ref_choices={"choices": [("Edit", "KI")]}),
        )
        image = result["image"]
        self.assertFalse(image["creator"]["compatible"])
        self.assertTrue(image["editor"]["compatible"])
        self.assertFalse(image["compatible"])
        self.assertIn("plain generation", image["reason"])

    def test_ltx_supports_all_director_workflows_without_leftover_omni(self):
        result = assess_director_model(
            "ltx-custom",
            _ltx_video(),
            architecture="ltx2_22B",
        )
        for workflow in (*DIRECTOR_PIPELINE_TYPES, "seamless"):
            self.assertTrue(result["video"][workflow]["compatible"], workflow)
        self.assertTrue(result["supports_voice_reference"])
        self.assertEqual(result["voice_reference_mode"], "id_lora")
        self.assertEqual(result["video_strategy"], ROLLING_WINDOW)
        self.assertEqual(result["audio_input_mode"], "generic_audio_guide")

    def test_native_audio_output_is_not_audio_input(self):
        result = assess_director_model(
            "ovi",
            {
                "image_prompt_types_allowed": "TSVL",
                "sliding_window": True,
                "returns_audio": True,
            },
        )
        self.assertFalse(result["video"]["music_video"]["compatible"])
        self.assertTrue(result["video"]["short_film_story"]["compatible"])
        self.assertFalse(result["supports_audio_input"])
        self.assertTrue(result["generates_audio"])
        self.assertEqual(result["audio_input_mode"], "none")

    def test_leftover_reference_manifest_is_not_soundtrack_input(self):
        result = assess_director_model(
            "minimax_h3_ref2va",
            {
                "name": "H3 Ref2VA",
                "image_prompt_types_allowed": "",
                "sliding_window": False,
                "returns_audio": True,
                "omni_reference": True,
                "director_video_strategy": "omni_reference",
                "director_audio_input_mode": "reference_manifest",
                "director_reference_mode": "omni_manifest",
                "director_shot_image_support": "direct_references",
            },
            architecture="minimax_h3_ref2va",
        )
        # Leftover 1.9.0 treated reference_manifest as music/audio input.
        # Continuum keeps H3 bounded and does not restore that shortcut.
        self.assertEqual(result["video_strategy"], BOUNDED_START_END)
        self.assertFalse(result["video"]["music_video"]["compatible"])
        self.assertFalse(result["video"]["short_film_audio"]["compatible"])
        self.assertFalse(result["video"]["short_film_story"]["compatible"])
        self.assertIn("start-frame", result["video"]["short_film_story"]["reason"])
        self.assertFalse(result["video"]["seamless"]["compatible"])
        self.assertFalse(result["supports_audio_input"])
        self.assertEqual(result["audio_input_mode"], "none")
        self.assertFalse(result["supports_voice_reference"])
        self.assertEqual(result["shot_image_support"], "direct_references")

    def test_bounded_h3_does_not_use_leftover_seamless_helper(self):
        result = assess_director_model(
            "minimax_h3",
            {
                "name": "H3 First / Last",
                "image_prompt_types_allowed": "TSE",
                "sliding_window": True,
                "video_continuation": True,
                "custom_frames_injection": True,
                "returns_audio": True,
                "director_video_strategy": "bounded_start_end",
                "director_audio_input_mode": "none",
            },
            architecture="minimax_h3",
        )
        # Leftover 1.9.0 enabled seamless via supports_director_seamless.
        # Continuum keeps seamless on rolling-window + frame injection only.
        self.assertEqual(result["video_strategy"], BOUNDED_START_END)
        self.assertTrue(result["video"]["short_film_story"]["compatible"])
        self.assertFalse(result["video"]["seamless"]["compatible"])
        self.assertIn("standard Director mode", result["video"]["seamless"]["reason"])

    def test_seamless_requires_rolling_window_and_frame_injection(self):
        rolling = assess_director_model(
            "ordinary-i2v",
            {
                "image_prompt_types_allowed": "SEVL",
                "sliding_window": True,
            },
        )
        self.assertFalse(rolling["video"]["short_film_story"]["compatible"])
        self.assertIn("synchronized dialogue", rolling["video"]["short_film_story"]["reason"])
        self.assertFalse(rolling["video"]["seamless"]["compatible"])
        self.assertIn("frame/keyframe injection", rolling["video"]["seamless"]["reason"])

        seamless = assess_director_model("ltx-custom", _ltx_video())
        self.assertTrue(seamless["video"]["seamless"]["compatible"])


if __name__ == "__main__":
    unittest.main()
