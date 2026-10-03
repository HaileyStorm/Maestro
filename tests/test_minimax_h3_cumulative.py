"""Real H3 generate method with CPU fakes; no weights or device acceptance."""

from __future__ import annotations

import os
import sys
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from models.minimax_h3.minimax_h3_main import (
    AUDIO_LATENTS_MEAN,
    AUDIO_LATENTS_STD,
    VIDEO_LATENTS_MEAN,
    VIDEO_LATENTS_STD,
    MiniMaxH3Model,
)
from models.minimax_h3.packing import audio_latent_num_frames
from models.minimax_h3.scheduler import MiniMaxH3Scheduler
from services.h3_cumulative_latents import H3CumulativeLatents
from services.h3_native_continuation import (
    audio_tick_at_frame,
    latent_frames_for_video_frames,
    plan_h3_native_continuation_step,
    plan_h3_native_continuation_tail,
)


class FakeTransformer:
    config = SimpleNamespace(patch_size=(1, 2, 2))

    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, **kwargs):
        self.calls.append(
            {
                key: value.detach().clone()
                if isinstance(value, torch.Tensor)
                else value
                for key, value in kwargs.items()
            }
        )
        if self.fail:
            raise InterruptedError("fake paired sampling interruption")
        return torch.ones_like(kwargs["hidden_states"]), torch.ones_like(
            kwargs["audio_hidden_states"]
        )


class FakeConditioner:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt, device, keyframes, **kwargs):
        self.prompts.append(prompt)
        return torch.zeros(1, 1, 4), torch.ones(1, dtype=torch.long)


class FakeVideoVAE:
    spatial_compression_ratio = 16

    def __init__(self):
        self.inputs = []

    def decode(self, latents, **kwargs):
        self.inputs.append(latents.clone())
        frames = (latents.shape[2] - 2) // 5 * 17 + 5
        return (
            torch.zeros(1, 3, frames, latents.shape[3] * 16, latents.shape[4] * 16),
        )


class FakeAudioVAE:
    def __init__(self):
        self.inputs = []
        self.fail = False
        self.after_decode = None

    def decode(self, latents, **kwargs):
        self.inputs.append(latents.clone())
        if self.fail:
            raise RuntimeError("fake audio decode failure")
        if self.after_decode:
            self.after_decode()
        return (torch.ones(2, 1, latents.shape[-1] * 800),)


def fake_model():
    model = object.__new__(MiniMaxH3Model)
    model.device = torch.device("cpu")
    model.dtype = torch.float32
    model.model_def = {}
    model.selected_model_type = "minimax_h3"
    model.reference_mode = False
    model.transformer = FakeTransformer()
    model.conditioner = FakeConditioner()
    model.vae = FakeVideoVAE()
    model.audio_vae = FakeAudioVAE()
    model.scheduler = MiniMaxH3Scheduler(shift=12.0)
    model.audio_scheduler = MiniMaxH3Scheduler(shift=3.0)
    model._ref2va_handoff_cache = None
    model._h3_cumulative_token = None
    model._interrupt = False
    return model


class TestMiniMaxH3CumulativeGenerate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.threads)

    def setUp(self):
        self.gate = patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"})
        self.gate.start()
        self.addCleanup(self.gate.stop)
        self.model = fake_model()

    def generate(self, **kwargs):
        options = {
            "input_prompt": "A moving scene",
            "height": 64,
            "width": 64,
            "frame_num": 141,
            "sampling_steps": 2,
            "seed": 123,
            "custom_settings": {"h3_attention_engine": "sdpa"},
            "_h3_cumulative_capture": True,
        }
        options.update(kwargs)
        return self.model.generate(**options)

    def start(self):
        result = self.generate()
        self.model.transformer.calls.clear()
        return result["_h3_cumulative_handoff"]

    def append(self, handoff, step, **kwargs):
        return self.generate(
            frame_num=step.target_frames,
            _h3_cumulative_previous=handoff,
            _h3_cumulative_step=step,
            **kwargs,
        )

    def test_short_first_window_and_tail_require_private_capture(self):
        for frames in (22, 56):
            with self.subTest(frames=frames):
                result = self.generate(frame_num=frames)
                handoff = result["_h3_cumulative_handoff"]
                previous = handoff["state"]
                self.assertEqual(previous.frame_count, frames)
                self.assertEqual(result["x"].shape[1], frames)
                step = plan_h3_native_continuation_step(
                    22, 17, absolute_context_start_frame=frames - 22
                )
                appended = self.append(handoff, step)
                state = appended["_h3_cumulative_handoff"]["state"]
                self.assertEqual(state.frame_count, frames + 17)
                torch.testing.assert_close(
                    state.video[:, :, : previous.video.shape[2]],
                    previous.video,
                    rtol=0,
                    atol=0,
                )
                torch.testing.assert_close(
                    state.audio[..., : previous.audio.shape[-1]],
                    previous.audio,
                    rtol=0,
                    atol=0,
                )
        with self.assertRaisesRegex(ValueError, "supports 5-15s"):
            self.model.generate(
                "ordinary scene",
                frame_num=56,
                height=64,
                width=64,
                sampling_steps=2,
                custom_settings={"h3_attention_engine": "sdpa"},
            )
        for frames in (5, 6, 21, 22.0, True):
            with (
                self.subTest(rejected_frames=frames),
                self.assertRaisesRegex(
                    ValueError, "requires at least 22 integer frames"
                ),
            ):
                self.generate(frame_num=frames)

    def test_real_sampler_uses_separate_guide_and_absolute_audio_span(self):
        handoff = self.start()
        previous = handoff["state"]
        video_before, audio_before = previous.video.clone(), previous.audio.clone()
        step = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=119
        )
        self.assertEqual(step.target_audio_ticks, 94)
        self.assertEqual(audio_latent_num_frames(step.target_frames), 93)
        result = self.append(handoff, step)
        state = result["_h3_cumulative_handoff"]["state"]
        self.assertEqual((state.frame_count, state.audio.shape[-1]), (175, 292))
        self.assertEqual(tuple(result["x"].shape), (3, 175, 64, 64))
        self.assertEqual(result["audio"].shape, (round(Fraction(175 * 32000, 24)), 2))
        torch.testing.assert_close(
            state.video[:, :, : previous.video.shape[2]], video_before, rtol=0, atol=0
        )
        torch.testing.assert_close(
            state.audio[..., : previous.audio.shape[-1]], audio_before, rtol=0, atol=0
        )
        torch.testing.assert_close(previous.video, video_before, rtol=0, atol=0)
        torch.testing.assert_close(previous.audio, audio_before, rtol=0, atol=0)
        self.assertNotEqual(state.video.data_ptr(), previous.video.data_ptr())
        calls = self.model.transformer.calls
        self.assertEqual(len(calls), 2)
        # Seven guide frames occupy 28 packed rows; 17 target frames occupy 68.
        self.assertEqual(calls[0]["hidden_states"].shape, (1, 96, 96))
        self.assertEqual(
            calls[0]["audio_hidden_states"].shape,
            (1, 2 * (step.context_audio_ticks + 94), 32),
        )
        torch.testing.assert_close(
            calls[0]["hidden_states"][:, :28],
            calls[1]["hidden_states"][:, :28],
            rtol=0,
            atol=0,
        )
        self.assertFalse(
            torch.equal(
                calls[0]["hidden_states"][:, 28:], calls[1]["hidden_states"][:, 28:]
            )
        )
        expected_audio = (
            previous.audio[..., -step.context_audio_ticks :]
            .permute(0, 2, 1)
            .reshape(-1, 32)
        )
        torch.testing.assert_close(
            calls[0]["audio_hidden_states"][0, : 2 * step.context_audio_ticks],
            expected_audio,
            rtol=0,
            atol=0,
        )
        torch.testing.assert_close(
            calls[0]["audio_hidden_states"][:, : 2 * step.context_audio_ticks],
            calls[1]["audio_hidden_states"][:, : 2 * step.context_audio_ticks],
            rtol=0,
            atol=0,
        )
        indices = calls[0]["video_indices"]
        positions = calls[0]["position_ids"]
        self.assertEqual(positions.dtype, torch.float64)
        torch.testing.assert_close(
            positions[indices[:28]], positions[indices[28:56]], rtol=0, atol=0
        )
        # Both VAEs receive full assembled state after denormalization.
        video_mean = torch.tensor(VIDEO_LATENTS_MEAN).view(1, 24, 1, 1, 1)
        video_std = torch.tensor(VIDEO_LATENTS_STD).view(1, 24, 1, 1, 1)
        audio_mean = torch.tensor(AUDIO_LATENTS_MEAN).view(1, 32, 1)
        audio_std = torch.tensor(AUDIO_LATENTS_STD).view(1, 32, 1)
        torch.testing.assert_close(
            self.model.vae.inputs[-1], state.video * video_std + video_mean
        )
        torch.testing.assert_close(
            self.model.audio_vae.inputs[-1], state.audio * audio_std + audio_mean
        )
        self.assertIsNone(self.model._ref2va_handoff_cache)

    def test_trim_happens_after_full_decode_and_ends_chain(self):
        handoff = self.start()
        step = plan_h3_native_continuation_tail(
            50, absolute_context_start_frame=119
        ).steps[0]
        result = self.append(handoff, step)
        next_handoff = result["_h3_cumulative_handoff"]
        state = next_handoff["state"]
        self.assertEqual((state.frame_count, state.published_frames), (192, 191))
        self.assertEqual(result["x"].shape[1], 191)
        self.assertEqual(result["audio"].shape[0], round(Fraction(191 * 32000, 24)))
        self.assertEqual(self.model.vae.inputs[-1].shape[2], 57)
        next_step = plan_h3_native_continuation_step(
            22, 17, absolute_context_start_frame=170
        )
        with self.assertRaisesRegex(ValueError, "trimmed chain"):
            self.append(next_handoff, next_step)

    def test_audio_clock_completion_padding_is_explicit_and_normal_output_unchanged(
        self,
    ):
        captured = self.generate(frame_num=158)
        normal = self.model.generate(
            "A moving scene",
            height=64,
            width=64,
            frame_num=158,
            sampling_steps=2,
            seed=123,
            custom_settings={"h3_attention_engine": "sdpa"},
        )
        self.assertEqual(
            captured["_h3_cumulative_handoff"]["audio_padding_samples"], 267
        )
        self.assertEqual(normal["audio"].shape[0], 263 * 800)
        np.testing.assert_array_equal(captured["audio"][: 263 * 800], normal["audio"])
        np.testing.assert_array_equal(
            captured["audio"][263 * 800 :], np.zeros((267, 2))
        )
        torch.testing.assert_close(captured["x"], normal["x"], rtol=0, atol=0)
        self.assertNotIn("_h3_cumulative_handoff", normal)

    def test_gate_and_incompatible_modes_fail_before_conditioning(self):
        with (
            patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}),
            self.assertRaisesRegex(ValueError, "experimental capture gate"),
        ):
            self.generate()
        for settings in (
            {"h3_turbo_profile": "probe"},
            {"h3_spectrum_profile": "probe"},
            {"h3_lightx2v_profile": "probe"},
            {"h3_source_audio_mode": "lock_source"},
            {"h3_native_boundary_conditioning": True},
            {"_h3_bridge_guides": {}},
        ):
            with (
                self.subTest(settings=settings),
                self.assertRaisesRegex(ValueError, "independent native"),
            ):
                self.generate(custom_settings=settings)
        self.assertEqual(self.model.conditioner.prompts, [])

    def test_handoff_requires_same_instance_exact_window_and_canvas(self):
        handoff = self.start()
        step = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=119
        )
        with self.assertRaisesRegex(ValueError, "same loaded model"):
            fake_model().generate(
                "scene",
                _h3_cumulative_capture=True,
                _h3_cumulative_previous=handoff,
                _h3_cumulative_step=step,
            )
        with self.assertRaisesRegex(ValueError, "planned sampling window"):
            self.generate(
                frame_num=57, _h3_cumulative_previous=handoff, _h3_cumulative_step=step
            )
        with self.assertRaisesRegex(ValueError, "canvas"):
            self.append(handoff, step, width=96)
        with self.assertRaisesRegex(ValueError, "new keyframes"):
            self.append(handoff, step, image_start=object())
        stale = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=102
        )
        with self.assertRaisesRegex(ValueError, "previous frame boundary"):
            self.append(handoff, stale)
        self.assertEqual(self.model.transformer.calls, [])

    def test_sampling_and_decode_failures_do_not_publish_or_change_retained_state(self):
        handoff = self.start()
        state = handoff["state"]
        video_before, audio_before = state.video.clone(), state.audio.clone()
        step = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=119
        )
        self.model.transformer.fail = True
        with self.assertRaises(InterruptedError):
            self.append(handoff, step)
        self.model.transformer.fail = False
        self.model.audio_vae.fail = True
        with self.assertRaisesRegex(RuntimeError, "audio decode failure"):
            self.append(handoff, step)
        self.model.audio_vae.fail = False
        self.model.audio_vae.after_decode = lambda: setattr(
            self.model, "_interrupt", True
        )
        self.assertIsNone(self.append(handoff, step))
        torch.testing.assert_close(state.video, video_before, rtol=0, atol=0)
        torch.testing.assert_close(state.audio, audio_before, rtol=0, atol=0)
        self.assertIs(self.model._h3_cumulative_token, handoff["model_token"])
        self.assertFalse(
            any(isinstance(value, torch.Tensor) for value in vars(self.model).values())
        )

    def test_sampling_cancellation_and_release(self):
        handoff = self.start()
        step = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=119
        )

        def cancel(index, *args, **kwargs):
            if index == 0:
                self.model._interrupt = True

        self.assertIsNone(self.append(handoff, step, callback=cancel))
        self.assertIs(self.model._h3_cumulative_token, handoff["model_token"])
        self.model.release()
        self.assertIsNone(self.model._h3_cumulative_token)
        self.assertIsNone(self.model.transformer)

    def test_new_capture_and_intervening_ordinary_call_invalidate_old_handoff(self):
        old = self.start()
        new = self.start()
        step = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=119
        )
        with self.assertRaisesRegex(ValueError, "same loaded model"):
            self.append(old, step)
        self.model.generate(
            "ordinary scene",
            height=64,
            width=64,
            frame_num=141,
            sampling_steps=2,
            custom_settings={"h3_attention_engine": "sdpa"},
            seed=123,
        )
        self.assertIsNone(self.model._h3_cumulative_token)
        with self.assertRaisesRegex(ValueError, "same loaded model"):
            self.append(new, step)

    def test_large_retained_state_fails_before_sampling_and_full_copy(self):
        handoff = self.start()
        frames = 22 + 17 * 40000
        handoff["state"] = H3CumulativeLatents(
            # Expanded zero views model shape without allocating large storage.
            torch.zeros(1).expand(1, 24, latent_frames_for_video_frames(frames), 4, 4),
            torch.zeros(1).expand(2, 32, audio_tick_at_frame(frames)),
            frames,
        )
        step = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=frames - 22
        )
        with self.assertRaisesRegex(ValueError, "512 MiB allocation limit"):
            self.append(handoff, step)
        self.assertEqual(self.model.transformer.calls, [])

    def test_under_latent_cap_full_decode_is_bounded_before_sampling(self):
        handoff = self.start()
        frames = 22 + 17 * 400
        handoff["state"] = H3CumulativeLatents(
            torch.zeros(1).expand(
                1, 24, latent_frames_for_video_frames(frames), 30, 54
            ),
            torch.zeros(1).expand(2, 32, audio_tick_at_frame(frames)),
            frames,
        )
        step = plan_h3_native_continuation_step(
            22, 119, absolute_context_start_frame=frames - 22
        )
        with self.assertRaisesRegex(ValueError, "private 2 GiB output limit"):
            self.append(handoff, step, height=480, width=864)
        self.assertEqual(self.model.transformer.calls, [])

    def test_creative_prompts_follow_identical_uninspected_path(self):
        prompts = (
            "A gentle scene",
            "An adult erotic scene",
            "A violent controversial scene",
        )
        for prompt in prompts:
            self.generate(input_prompt=prompt)
        self.assertEqual(self.model.conditioner.prompts, list(prompts))


if __name__ == "__main__":
    unittest.main()
