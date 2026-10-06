"""Behavioral CPU checks for the private native interval Guide executor."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

_APP = Path(__file__).resolve().parents[1] / "app"


class TestH3TimelineGuides(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(_APP))
        import torch
        from models.minimax_h3 import timeline_guides
        from models.minimax_h3.minimax_h3_main import MiniMaxH3Model
        from models.minimax_h3.scheduler import MiniMaxH3Scheduler
        from services.h3_guide_plan import plan_h3_guide_inputs
        cls.torch = torch
        cls.guides = timeline_guides
        cls.model_type = MiniMaxH3Model
        cls.scheduler_type = MiniMaxH3Scheduler
        cls.plan = staticmethod(plan_h3_guide_inputs)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(_APP))

    def payload(self, *, indices=(31, -1), frames=(24, 3), audio_ticks=(300, None)):
        inputs, media = [], []
        for index, count, ticks in zip(indices, frames, audio_ticks):
            inputs.append({
                "frame_idx": index,
                "visual": None if count is None else {"sha256": "sha256:" + "a" * 64, "count": count},
                "audio": None if ticks is None else {"sha256": "sha256:" + "b" * 64, "count": ticks},
            })
            media.append(self.guides.H3TimelineGuideMedia(
                None if count is None else self.torch.zeros(3, count, 32, 32),
                None if ticks is None else self.torch.zeros(2, ticks * 800),
            ))
        return self.guides.H3TimelineGuidePayload(self.plan(124, inputs), tuple(media))

    def encoders(self):
        calls = []

        def video(pixels, *, keep_all_latents):
            calls.append(("video", pixels.shape[1], keep_all_latents))
            ticks = 1 if pixels.shape[1] == 1 else 5 * ((pixels.shape[1] - 5) // 17) + 2
            return self.torch.zeros(1, 24, ticks, 2, 2)

        def audio(waveform):
            calls.append(("audio", waveform.shape[-1]))
            count = (waveform.shape[-1] + 799) // 800
            return self.torch.stack([self.torch.full((32, count), 0.25), self.torch.full((32, count), 0.75)])

        return video, audio, calls

    def encode(self, payload, **overrides):
        video, audio, calls = self.encoders()
        kwargs = dict(frame_num=124, height=32, width=32, patch_size=(1, 2, 2), seed=19,
                      device=self.torch.device("cpu"), encode_video=video, encode_audio=audio,
                      interrupted=lambda: False)
        kwargs.update(overrides)
        return self.guides.encode_timeline_guides(payload, **kwargs), calls

    def test_legal_prefix_audio_crop_channel_order_and_seeded_condition_noise(self):
        torch = self.torch
        payload = self.payload()
        original = payload.media[0].visual.clone()
        global_rng = torch.get_rng_state().clone()
        with mock.patch.object(torch, "randn", wraps=torch.randn) as noise:
            rows, calls = self.encode(payload)
        self.assertEqual([call.kwargs["device"] for call in noise.call_args_list], ["cpu", "cpu"])
        self.assertEqual(calls, [("video", 22, True), ("audio", 240000), ("video", 1, True)])
        self.assertEqual(rows.video_anchors, (("clip", 7, 31), ("clip", 1, 123)))
        self.assertEqual(rows.audio_anchors, (("frame", 155, 31),))
        self.assertEqual(tuple(rows.video.shape), (8, 96))
        self.assertEqual(tuple(rows.audio.shape), (310, 32))
        self.assertTrue(torch.equal(rows.audio[:155], torch.full((155, 32), .25)))
        self.assertTrue(torch.equal(rows.audio[155:], torch.full((155, 32), .75)))
        expected = .001 * torch.randn((7, 96), generator=torch.Generator().manual_seed(19))
        self.assertTrue(torch.allclose(rows.video[:7], expected, atol=1e-9, rtol=1e-6))
        self.assertTrue(torch.equal(payload.media[0].visual, original))
        self.assertTrue(torch.equal(torch.get_rng_state(), global_rng))

    def test_overlapping_conditions_remain_ordered_with_independent_rng_restarts(self):
        rows, _ = self.encode(self.payload(indices=(31, 31), frames=(22, 22), audio_ticks=(None, None)))
        self.assertTrue(self.torch.equal(rows.video[:7], rows.video[7:]))
        self.assertEqual(rows.video_anchors, (("clip", 7, 31),) * 2)

    def test_invalid_handoff_and_vae_geometry_reject_before_sampling(self):
        valid = self.payload()
        bad_plan = copy.deepcopy(valid.plan)
        bad_plan["guides"][0]["resolved_frame_idx"] = 32
        for payload in (
            {},
            self.guides.H3TimelineGuidePayload(bad_plan, valid.media),
            self.guides.H3TimelineGuidePayload(valid.plan, valid.media[:1]),
            self.guides.H3TimelineGuidePayload(valid.plan, (self.guides.H3TimelineGuideMedia(self.torch.zeros(3, 22, 32, 32), valid.media[0].waveform), valid.media[1])),
        ):
            with self.subTest(kind=type(payload)), self.assertRaises(ValueError):
                self.encode(payload)
        with self.assertRaisesRegex(ValueError, "video VAE geometry"):
            self.encode(valid, encode_video=lambda *args, **kwargs: self.torch.zeros(1, 24, 6, 2, 2))
        with self.assertRaisesRegex(ValueError, "audio VAE geometry"):
            self.encode(valid, encode_audio=lambda value: self.torch.zeros(2, 32, 299))

    def test_cancel_between_video_and_audio_does_not_encode_later_media(self):
        video, audio, calls = self.encoders()
        with self.assertRaises(InterruptedError):
            self.encode(self.payload(), encode_video=video, encode_audio=audio,
                        interrupted=lambda: bool(calls))
        self.assertEqual(calls, [("video", 22, True)])

    def model(self):
        model = object.__new__(self.model_type)
        model.device = self.torch.device("cpu")
        model.reference_mode = False
        model.selected_model_type = "minimax_h3"
        model.model_def = {}
        model.transformer = types.SimpleNamespace(config=types.SimpleNamespace(patch_size=(1, 2, 2)))
        model.vae = types.SimpleNamespace(spatial_compression_ratio=16)
        model.scheduler = self.scheduler_type(shift=12.0)
        model.audio_scheduler = self.scheduler_type(shift=3.0)
        model.conditioner = mock.Mock(return_value=(self.torch.zeros(1, 4, 8), self.torch.ones(4, dtype=self.torch.long)))
        video, audio, calls = self.encoders()
        model._encode_reference_video = video
        model._encode_reference_audio = audio
        return model, calls

    def test_actual_generate_forward_preserves_guide_rows_and_updates_every_target(self):
        torch = self.torch
        model, calls = self.model()
        observed = []

        def forward(**kwargs):
            observed.append({key: value.detach().clone() for key, value in kwargs.items() if isinstance(value, torch.Tensor)})
            if len(observed) == 3:
                model._interrupt = True
            return (torch.ones_like(kwargs["hidden_states"]), torch.ones_like(kwargs["audio_hidden_states"]))

        forward.config = types.SimpleNamespace(patch_size=(1, 2, 2))
        model.transformer = forward
        with mock.patch.dict(os.environ, {"MAESTRO_H3_TIMELINE_GUIDES_EXPERIMENTAL": "1"}):
            prompt = "Adult characters fight in a violent, controversial story."
            result = model.generate(prompt, frame_num=124, height=32, width=32, sampling_steps=3,
                                    seed=19, custom_settings={"h3_attention_engine": "sdpa"},
                                    _h3_timeline_guides=self.payload())
        self.assertIsNone(result)  # Deliberately cancel after observing paired updates, before any decode.
        self.assertEqual(model.conditioner.call_args.args[0], prompt)
        self.assertEqual(len(observed), 3)
        first, second = observed[:2]
        self.assertEqual(len(calls), 3)
        self.assertTrue(torch.equal(first["hidden_states"][:, :8], second["hidden_states"][:, :8]))
        self.assertTrue(torch.equal(first["audio_hidden_states"][:, :310], second["audio_hidden_states"][:, :310]))
        self.assertTrue(torch.all(first["hidden_states"][:, 8:] != second["hidden_states"][:, 8:]))
        self.assertTrue(torch.all(first["audio_hidden_states"][:, 310:] != second["audio_hidden_states"][:, 310:]))
        positions = first["position_ids"]
        video_indices, audio_indices = first["video_indices"], first["audio_indices"]
        self.assertEqual(video_indices[:8].tolist(), list(range(4, 11)) + [321])
        self.assertEqual(audio_indices[:310].tolist(), list(range(11, 321)))
        origin = 4 + 31 * (5 / 3)
        expected = torch.tensor([origin + span * (5 / 3) for span in (0, 1, 5, 9, 13, 17, 18)], dtype=torch.float64)
        torch.testing.assert_close(positions[video_indices[:7], 0], expected, rtol=0, atol=1e-13)
        self.assertAlmostEqual(float(positions[video_indices[7], 0]), 4 + 123 * 5 / 3)
        self.assertTrue(torch.equal(positions[audio_indices[:155], 0], origin + torch.arange(155, dtype=torch.float64)))
        self.assertTrue(torch.equal(positions[audio_indices[155:310], 0], positions[audio_indices[:155], 0]))
        row_times = second["timestep"][second["timestep_indices"]]
        self.assertTrue(torch.all(row_times[video_indices[:8]] == .999))
        self.assertTrue(torch.all(row_times[audio_indices[:310]] == 1))
        self.assertTrue(torch.all(row_times[video_indices[8:]] < .999))
        self.assertTrue(torch.all(row_times[audio_indices[310:]] < 1))
        # Guide noise uses an isolated stream, leaving the target generator
        # at exactly its ordinary starting state.
        generator = torch.Generator().manual_seed(19)
        expected_video = torch.randn((1, 24, 37, 2, 2), generator=generator).permute(0, 2, 1, 3, 4).reshape(37, 96)
        self.assertTrue(torch.equal(first["hidden_states"][0, 8:], expected_video))
        expected_audio = torch.randn((414, 32), generator=generator)
        self.assertTrue(torch.equal(first["audio_hidden_states"][0, 310:], expected_audio))

    def test_audio_only_guide_can_anchor_the_final_fractional_interval(self):
        from models.minimax_h3.packing import build_packed_sequence, interleave_timeline_guide_conditions
        rows, calls = self.encode(self.payload(indices=(-1,), frames=(None,), audio_ticks=(9,)))
        self.assertIsNone(rows.video)
        self.assertEqual(rows.audio_anchors, (("frame", 2, 123),))
        self.assertEqual(calls, [("audio", 7200)])
        layout = build_packed_sequence(self.torch.ones(4, dtype=self.torch.long), 37, 2, 2, 207,
                                       (1, 2, 2), audio_condition_anchors=rows.audio_anchors)
        layout = interleave_timeline_guide_conditions(layout, rows.condition_order, 1)
        self.assertEqual(layout.num_condition_video_rows, 0)
        self.assertEqual(layout.num_condition_audio_rows, 4)
        self.assertEqual(layout.position_ids[layout.audio_indices[:4], 0].tolist(), [209, 210, 209, 210])
        self.assertEqual(float(layout.position_ids[layout.audio_indices[4], 0]), 4)

    def test_public_and_incompatible_requests_fail_before_encode_or_conditioner(self):
        for gate, extra in (("0", {}), ("1", {"input_frames": self.torch.zeros(3, 22, 32, 32)}),
                            ("1", {"custom_settings": {"_h3_bridge_guides": []}}),
                            ("1", {"_h3_cumulative_capture": True}),
                            ("1", {"image_start": self.torch.zeros(3, 32, 32)}),
                            ("1", {"image_end": self.torch.zeros(3, 32, 32)}),
                            ("1", {"_h3_timeline_additional_stills": []}),
                            ("1", {"activated_loras": ["MiniMax-H3-FL2VA-Acc-8Step.safetensors"]}),
                            ("1", {"skip_steps_cache_type": "tea"}),
                            ("1", {"multi_clip_info": {"total": 2}})):
            model, calls = self.model()
            with mock.patch.dict(os.environ, {"MAESTRO_H3_TIMELINE_GUIDES_EXPERIMENTAL": gate}), self.assertRaises(ValueError):
                model.generate("A scene", frame_num=124, height=32, width=32,
                               _h3_timeline_guides=self.payload(), **extra)
            self.assertEqual(calls, [])
            model.conditioner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
