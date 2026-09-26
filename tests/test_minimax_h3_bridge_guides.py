"""CPU-only contracts for the native Ref2VA AddGuide sampler seam."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
_BRIDGE_SETTING = [
    {"slot": 1, "frame_idx": 0, "frames": 22},
    {"slot": 2, "frame_idx": -22, "frames": 22},
]


class TestMiniMaxH3BridgeGuides(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if str(_APP) not in sys.path:
            sys.path.insert(0, str(_APP))
            cls._inserted_app = True
        else:
            cls._inserted_app = False
        import torch
        from models.minimax_h3.minimax_h3_handler import family_handler
        from models.minimax_h3.packing import (
            H3BridgeGuideError,
            build_ref2va_packed_sequence,
            prepare_h3_bridge_video_inputs,
            validate_h3_bridge_guides_request,
            validate_h3_bridge_guides_setting,
            video_latent_num_frames,
        )

        cls.torch = torch
        cls.handler = family_handler
        cls.H3BridgeGuideError = H3BridgeGuideError
        cls.build_ref2va_packed_sequence = staticmethod(build_ref2va_packed_sequence)
        cls.prepare_h3_bridge_video_inputs = staticmethod(prepare_h3_bridge_video_inputs)
        cls.validate_h3_bridge_guides_request = staticmethod(validate_h3_bridge_guides_request)
        cls.validate_h3_bridge_guides_setting = staticmethod(validate_h3_bridge_guides_setting)
        cls.video_latent_num_frames = staticmethod(video_latent_num_frames)

    @classmethod
    def tearDownClass(cls):
        if cls._inserted_app and sys.path and sys.path[0] == str(_APP):
            sys.path.pop(0)

    def _selected_videos(self, frames=32):
        values = self.torch.linspace(-1.0, 1.0, frames).view(1, frames, 1, 1)
        video = values.expand(3, -1, 4, 8).contiguous()
        return ((1, video), (2, video))

    def test_fixed_setting_and_video_windows_have_exact_geometry(self):
        torch = self.torch
        settings = {"_h3_bridge_guides": _BRIDGE_SETTING}
        self.assertTrue(self.validate_h3_bridge_guides_setting(settings))
        self.assertEqual(self.video_latent_num_frames(22), 7)

        semantic_videos, clips = self.prepare_h3_bridge_video_inputs(
            self._selected_videos(), 8, 8
        )
        self.assertEqual(semantic_videos, ())
        self.assertEqual(len(clips), 2)
        self.assertEqual(tuple(clips[0].shape), (3, 22, 8, 8))
        self.assertEqual(tuple(clips[1].shape), (3, 22, 8, 8))
        # Slot 1 contributes its trailing frames; slot 2 contributes its head.
        expected = torch.linspace(-1, 1, 32)
        self.assertAlmostEqual(float(clips[0][:, 0].mean()), float(expected[10]), places=2)
        self.assertAlmostEqual(float(clips[0][:, -1].mean()), 1.0, places=2)
        self.assertAlmostEqual(float(clips[1][:, 0].mean()), -1.0, places=2)
        self.assertAlmostEqual(float(clips[1][:, -1].mean()), float(expected[21]), places=2)

        horizontal = torch.linspace(-1.0, 1.0, 8).view(1, 1, 1, 8)
        spatial = horizontal.expand(3, 22, 4, 8).contiguous()
        _semantic, resized = self.prepare_h3_bridge_video_inputs(
            ((1, spatial), (2, spatial)), 8, 8
        )
        self.assertTrue(torch.equal(resized[0], resized[1]))
        cover_row = resized[0][0, 0].mean(dim=0)
        cover_span = cover_row.max() - cover_row.min()
        self.assertLess(float(cover_span), 1.4)
        self.assertAlmostEqual(float(cover_row.mean()), 0.0, places=2)

    def test_wrong_descriptor_model_soundtrack_and_inputs_fail_closed(self):
        torch = self.torch
        selected = self._selected_videos()
        validate = self.validate_h3_bridge_guides_request

        self.assertTrue(
            validate(
                {"_h3_bridge_guides": _BRIDGE_SETTING},
                reference_mode=True,
                selected_video_slots=selected,
                audio_prompt_type="",
            )
        )
        with self.assertRaises(self.H3BridgeGuideError):
            validate(
                {"_h3_bridge_guides": _BRIDGE_SETTING},
                reference_mode=False,
                selected_video_slots=selected,
                audio_prompt_type="",
            )
        with self.assertRaises(self.H3BridgeGuideError):
            validate(
                {"_h3_bridge_guides": _BRIDGE_SETTING},
                reference_mode=True,
                selected_video_slots=selected,
                audio_prompt_type="K",
            )
        with self.assertRaises(self.H3BridgeGuideError):
            validate(
                {"_h3_bridge_guides": _BRIDGE_SETTING},
                reference_mode=True,
                selected_video_slots=selected[:1],
                audio_prompt_type="",
            )

        wrong_values = (
            tuple(_BRIDGE_SETTING),
            [{"slot": True, "frame_idx": 0, "frames": 22}, _BRIDGE_SETTING[1]],
            [{"slot": 1, "frame_idx": 0, "frames": 22, "extra": 1}, _BRIDGE_SETTING[1]],
            [{"slot": 1, "frame_idx": 1, "frames": 22}, _BRIDGE_SETTING[1]],
        )
        for value in wrong_values:
            with self.subTest(value=value), self.assertRaises(self.H3BridgeGuideError):
                self.validate_h3_bridge_guides_setting({"_h3_bridge_guides": value})

        with self.assertRaisesRegex(self.H3BridgeGuideError, "preprocessed CTHW RGB"):
            self.prepare_h3_bridge_video_inputs(
                ((1, "one.mp4"), (2, "two.mp4")), 8, 8
            )
        short_video = torch.zeros(3, 21, 4, 8)
        with self.assertRaisesRegex(self.H3BridgeGuideError, "at least 22"):
            self.prepare_h3_bridge_video_inputs(((1, short_video), (2, short_video)), 8, 8)

    def test_handler_does_not_promote_addguide_clips_to_semantic_video_refs(self):
        model_def = self.handler.query_model_def("minimax_h3_ref2va", {})
        self.assertNotIn("_h3_bridge_guides", model_def["runtime_custom_settings"])
        inputs = {
            "custom_settings": {"_h3_bridge_guides": _BRIDGE_SETTING},
            "video_prompt_type": "V+-",
            "video_guide": "slot-one.mp4",
            "video_guide2": "slot-two.mp4",
            "audio_prompt_type": "",
            "prompt": "Follow the two visual guide clips.",
        }
        self.assertIsNone(
            self.handler.validate_generative_settings(
                "minimax_h3_ref2va", {}, inputs
            )
        )
        unresolved_semantic_tag = dict(inputs, prompt="Use <Video 1> as semantic context.")
        error = self.handler.validate_generative_settings(
            "minimax_h3_ref2va", {}, unresolved_semantic_tag
        )
        self.assertIn("does not resolve to a supplied MiniMax H3 video", error)

        base_model_error = self.handler.validate_generative_settings(
            "minimax_h3", {}, inputs
        )
        self.assertIn("require the Ref2VA checkpoint", base_model_error)

    def test_multi_latent_anchors_use_increasing_endpoint_times(self):
        torch = self.torch
        layout = self.build_ref2va_packed_sequence(
            torch.ones(2, dtype=torch.long),
            [],
            32,
            4,
            4,
            42,
            (1, 2, 2),
            keyframe_anchors=(("first", 7), ("last", 7)),
        )
        rows_per_frame = 4
        condition = layout.position_ids[
            layout.video_indices[: layout.num_condition_video_rows], 0
        ].view(14, rows_per_frame)
        target = layout.position_ids[
            layout.video_indices[layout.num_condition_video_rows :], 0
        ].view(32, rows_per_frame)

        first_times = condition[:7, 0]
        last_times = condition[7:, 0]
        self.assertTrue(torch.all(first_times[1:] > first_times[:-1]))
        self.assertTrue(torch.all(last_times[1:] > last_times[:-1]))
        self.assertTrue(torch.equal(first_times, target[:7, 0]))
        self.assertTrue(torch.equal(last_times, target[-7:, 0]))

        with self.assertRaisesRegex(ValueError, "cannot exceed target latent frames"):
            self.build_ref2va_packed_sequence(
                torch.ones(2, dtype=torch.long),
                [],
                6,
                4,
                4,
                8,
                (1, 2, 2),
                keyframe_anchors=(("last", 7),),
            )


if __name__ == "__main__":
    unittest.main()
