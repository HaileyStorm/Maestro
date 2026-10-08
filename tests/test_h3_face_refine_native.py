"""CPU equations and actual miniature sampler wiring, not full H3 acceptance."""

from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

import torch

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP)) if str(APP) not in sys.path else None
from models.minimax_h3.face_refine import (
    H3FaceRefinePayload, configure_face_refine_schedule,
    face_refine_row_multipliers, hold_face_refine_rows,
    validate_face_refine_request,
)
from models.minimax_h3.scheduler import MiniMaxH3Scheduler
import test_minimax_h3_control as control_tests


class H3FaceRefineNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.old_threads)

    def payload(self, multipliers=None, strength=0.5):
        return H3FaceRefinePayload(torch.full((1, 3, 124, 32, 32), 0.4), strength,
                                   multipliers or (0.0,) * 124)

    def validate(self, payload, **overrides):
        request = dict(frame_num=124, height=32, width=32, fps=24, sampling_steps=4,
                       reference_mode=False, model_type="minimax_h3",
                       custom_settings={"h3_attention_engine": "sdpa"},
                       conditioning=(), kwargs={})
        request.update(overrides)
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"):
            return validate_face_refine_request(payload, **request)

    def test_tail_comes_from_long_grid_and_preserves_native_full_strength(self):
        scheduler = MiniMaxH3Scheduler(shift=12)
        grid = configure_face_refine_schedule(scheduler, steps=4, strength=0.25, device="cpu")
        raw = torch.linspace(1.0, 0.0, 17)[-5:]
        expected = 12 * raw / (1 + 11 * raw)
        torch.testing.assert_close(grid, expected, rtol=0, atol=0)
        self.assertEqual(len(scheduler.timesteps), 4)
        self.assertNotAlmostEqual(float(grid[0]), 0.25)
        ordinary = MiniMaxH3Scheduler()
        ordinary.set_timesteps(5, device="cpu")
        full = configure_face_refine_schedule(scheduler, steps=4, strength=1, device="cpu")
        torch.testing.assert_close(full, ordinary.sigmas, rtol=0, atol=0)

    def test_temporal_mapping_and_sigma_correct_hold(self):
        payload = self.payload(tuple(i / 123 for i in range(124)))
        weights = face_refine_row_multipliers(payload, device="cpu")
        self.assertEqual(weights.shape, (37, 1))
        torch.testing.assert_close(weights[:, 0], torch.linspace(0, 1, 37))
        clean = torch.full((37, 96), 2.0)
        noise = torch.full_like(clean, -1.0)
        proposed = torch.full_like(clean, 9.0)
        blended = hold_face_refine_rows(proposed, clean=clean, noise=noise,
                                       multipliers=weights, next_sigma=0.2)
        expected = (weights * 9 + (1 - weights) * 1.4).expand_as(blended)
        torch.testing.assert_close(blended, expected)
        self.assertEqual(blended[0, 0].item(), expected[0, 0].item())
        self.assertEqual(blended[-1, 0].item(), 9.0)

    def test_admission_snapshots_media_and_rejects_clock_modes_and_serialization(self):
        payload = self.payload()
        captured = self.validate(payload)
        payload.video.zero_()
        self.assertEqual(captured.video.max().item(), torch.tensor(0.4).item())
        for overrides in (
            {"frame_num": 125}, {"frame_num": 22}, {"fps": 25}, {"height": 33},
            {"reference_mode": True}, {"sampling_steps": True},
            {"custom_settings": {"h3_attention_engine": "sage2"}},
            {"custom_settings": {"h3_attention_engine": "sdpa", "h3_turbo_profile": None}},
            {"conditioning": (torch.zeros(1),)}, {"kwargs": {"activated_loras": ["x"]}},
            {"kwargs": {"_h3_cumulative_step": None}},
            {"kwargs": {"multi_clip_info": {"total": 2}}},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.validate(payload, **overrides)
        for invalid in (
            {"video": payload.video}, self.payload(strength=0), self.payload(strength=float("nan")),
            H3FaceRefinePayload(payload.video, 0.5, (0.0,) * 123),
            H3FaceRefinePayload(payload.video, 0.5, (float("nan"),) * 124),
            H3FaceRefinePayload(payload.video.double(), 0.5, (0.0,) * 124),
        ):
            with self.subTest(invalid=type(invalid)), self.assertRaises(ValueError):
                self.validate(invalid)
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="0"), self.assertRaises(ValueError):
            validate_face_refine_request(None, frame_num=124, height=32, width=32,
                fps=24, sampling_steps=4, reference_mode=False, model_type="minimax_h3",
                custom_settings={}, conditioning=(), kwargs={})

    def test_sampler_holds_source_rows_at_each_clock_and_leaves_audio_rng_order(self):
        main, runtime, _ = control_tests.OriginalH3ControlSamplerTests.runtime()
        runtime._interrupt = False
        payload = self.payload()
        clean = runtime._encode_face_refine_video(payload)
        calls, decoded, noise_draws = [], [], []
        original_randn = main.randn_tensor

        def randn(*args, **kwargs):
            result = original_randn(*args, **kwargs)
            noise_draws.append(result.clone())
            return result

        def decode(**kwargs):
            decoded.append(kwargs["packed_rows"].clone())
            return torch.zeros(1, 3, 124, 32, 32), None

        def observe(module, args, kwargs):
            calls.append(kwargs["hidden_states"].clone())

        hook = runtime.transformer.register_forward_pre_hook(observe, with_kwargs=True)
        try:
            with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), \
                    patch.object(main, "_decode_h3_video_rows", side_effect=decode), \
                    patch.object(main, "randn_tensor", side_effect=randn):
                output = runtime.generate("An adult in a violent fictional scene", frame_num=124,
                    height=32, width=32, sampling_steps=4, seed=73, _h3_face_refine=payload,
                    custom_settings={"h3_attention_engine": "sdpa"},
                    callback=lambda *args, **kwargs: payload.video.fill_(0.9))
        finally:
            hook.remove()
        self.assertIsNotNone(output)
        self.assertEqual(len(calls), 4)
        from models.minimax_h3.packing import patchify_video_latents
        noise = patchify_video_latents(noise_draws[0], (1, 2, 2))
        scheduler = MiniMaxH3Scheduler()
        grid = configure_face_refine_schedule(scheduler, steps=4, strength=0.5, device="cpu")
        for actual, sigma in zip(calls, grid):
            torch.testing.assert_close(actual[0], (1 - sigma) * clean + sigma * noise,
                                       rtol=0, atol=2e-6)
        torch.testing.assert_close(decoded[0], clean, rtol=0, atol=0)
        self.assertEqual(runtime._sampler_prompts[-1], "An adult in a violent fictional scene")
        # Source pixels did not alter the seed or draw sequence for either modality.
        generator = torch.Generator(device="cpu").manual_seed(73)
        expected_video = original_randn(noise_draws[0].shape, generator=generator,
                                       device="cpu", dtype=torch.float32)
        expected_audio = original_randn(noise_draws[1].shape, generator=generator,
                                       device="cpu", dtype=torch.float32)
        torch.testing.assert_close(noise_draws[0], expected_video, rtol=0, atol=0)
        torch.testing.assert_close(noise_draws[1], expected_audio, rtol=0, atol=0)

    def test_cancellation_then_ordinary_generation_is_repeatable(self):
        main, runtime, _ = control_tests.OriginalH3ControlSamplerTests.runtime()
        decoded = []
        def decode(**kwargs):
            decoded.append(kwargs["packed_rows"].clone())
            return torch.zeros(1, 3, 124, 32, 32), None
        request = dict(frame_num=124, height=32, width=32, sampling_steps=4, seed=73,
                       custom_settings={"h3_attention_engine": "sdpa"})
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), \
                patch.object(main, "_decode_h3_video_rows", side_effect=decode):
            runtime.generate("Ordinary", **request)
            stopped = runtime.generate("Repair", **request, _h3_face_refine=self.payload(),
                callback=lambda index, *args, **kwargs: setattr(runtime, "_interrupt", True) if index >= 0 else None)
            self.assertIsNone(stopped)
            self.assertIsNone(runtime.scheduler.step_index)
            self.assertIsNone(runtime.audio_scheduler.step_index)
            runtime.generate("Ordinary", **request)
        torch.testing.assert_close(decoded[0], decoded[1], rtol=0, atol=0)
        torch.testing.assert_close(runtime._sampler_audio_latents[0],
                                   runtime._sampler_audio_latents[1], rtol=0, atol=0)

    def test_fractional_hold_matches_euler_at_every_actual_transformer_tick(self):
        main, runtime, _ = control_tests.OriginalH3ControlSamplerTests.runtime()
        runtime._interrupt = False
        payload = self.payload(tuple(i / 123 for i in range(124)))
        clean = runtime._encode_face_refine_video(payload)
        inputs, predictions, decoded = [], [], []
        def before(module, args, kwargs):
            inputs.append(kwargs["hidden_states"][0].clone())
        def after(module, args, output):
            predictions.append(output[0][0].clone())
        def decode(**kwargs):
            decoded.append(kwargs["packed_rows"].clone())
            return torch.zeros(1, 3, 124, 32, 32), None
        hooks = [runtime.transformer.register_forward_pre_hook(before, with_kwargs=True),
                 runtime.transformer.register_forward_hook(after)]
        try:
            with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), \
                    patch.object(main, "_decode_h3_video_rows", side_effect=decode):
                runtime.generate("Crop motion", frame_num=124, height=32, width=32,
                    sampling_steps=4, seed=73, _h3_face_refine=payload,
                    custom_settings={"h3_attention_engine": "sdpa"})
        finally:
            for hook in hooks:
                hook.remove()
        from models.minimax_h3.packing import patchify_video_latents
        generator = torch.Generator(device="cpu").manual_seed(73)
        noise = patchify_video_latents(main.randn_tensor((1, 24, 37, 2, 2),
            generator=generator, device="cpu", dtype=torch.float32), (1, 2, 2))
        raw = torch.linspace(1, 0, 9)[-5:]
        sigmas = 12 * raw / (1 + 11 * raw)
        weights = torch.linspace(0, 1, 37).view(-1, 1)
        expected = (1 - sigmas[0]) * clean + sigmas[0] * noise
        self.assertEqual(len(inputs), 4)
        for index, (actual, velocity) in enumerate(zip(inputs, predictions)):
            torch.testing.assert_close(actual, expected, rtol=0, atol=3e-6)
            sigma, next_sigma = sigmas[index:index + 2]
            clock_sigma = 1 - (1 - sigma)
            ratio = next_sigma / sigma
            proposed = ratio * actual + (1 - ratio) * (actual + clock_sigma * velocity)
            held = (1 - next_sigma) * clean + next_sigma * noise
            expected = weights * proposed + (1 - weights) * held
        torch.testing.assert_close(decoded[0], expected, rtol=0, atol=3e-6)

    def test_invalid_private_request_precedes_vae_and_prompt_encoding(self):
        main, runtime, _ = control_tests.OriginalH3ControlSamplerTests.runtime()
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), \
                patch.object(runtime.vae, "encode", side_effect=AssertionError("encoded")), \
                patch.object(runtime, "conditioner", side_effect=AssertionError("prompt")):
            for kwargs in ({"_h3_face_refine": self.payload(), "frame_num": 125},
                           {"custom_settings": {"_h3_face_refine": {}}}):
                with self.assertRaises(ValueError):
                    runtime.generate("Ordinary", **kwargs)

    def test_full_strength_all_edited_is_exactly_the_ordinary_sampler(self):
        main, runtime, _ = control_tests.OriginalH3ControlSamplerTests.runtime()
        decoded = []
        def decode(**kwargs):
            decoded.append(kwargs["packed_rows"].clone())
            return torch.zeros(1, 3, 124, 32, 32), None
        request = dict(frame_num=124, height=32, width=32, sampling_steps=4, seed=73,
                       custom_settings={"h3_attention_engine": "sdpa"})
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), \
                patch.object(main, "_decode_h3_video_rows", side_effect=decode):
            runtime.generate("Ordinary", **request)
            runtime.generate("Ordinary", **request,
                             _h3_face_refine=self.payload((1.0,) * 124, strength=1))
        torch.testing.assert_close(decoded[0], decoded[1], rtol=0, atol=0)
        torch.testing.assert_close(runtime._sampler_audio_latents[0],
                                   runtime._sampler_audio_latents[1], rtol=0, atol=0)

    def test_native_encoder_matches_raw_mode_statistics_and_rejects_wrong_lattice(self):
        main, runtime, _ = control_tests.OriginalH3ControlSamplerTests.runtime()
        runtime._interrupt = False
        payload = self.payload()
        from models.minimax_h3.packing import patchify_video_latents
        from diffusers.models.autoencoders.vae import DiagonalGaussianDistribution
        pixels = (payload.video - torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1, 1)) / \
            torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1, 1)
        with torch.inference_mode():
            moments = runtime.vae._encode(pixels)
            raw = DiagonalGaussianDistribution(moments).mode()
            mean = torch.tensor(runtime.vae.config.latents_mean).view(1, 24, 1, 1, 1)
            std = torch.tensor(runtime.vae.config.latents_std).view(1, 24, 1, 1, 1)
            expected = patchify_video_latents((raw - mean) / std, (1, 2, 2))
            actual = runtime._encode_face_refine_video(payload)
        torch.testing.assert_close(actual, expected, rtol=0, atol=0)
        import types
        wrong = types.SimpleNamespace(latent_dist=types.SimpleNamespace(
            mode=lambda: torch.zeros(1, 24, 36, 2, 2)))
        with patch.object(runtime.vae, "encode", return_value=wrong), self.assertRaisesRegex(ValueError, "clock/canvas"):
            runtime._encode_face_refine_video(payload)

    def test_saved_and_client_inputs_cannot_admit_the_private_payload(self):
        from models.minimax_h3.minimax_h3_handler import family_handler
        for inputs in ({"_h3_face_refine": self.payload()},
                       {"_h3_face_refine_dispatch": None},
                       {"custom_settings": {"_h3_face_refine": {}}}):
            with self.subTest(inputs=list(inputs)):
                self.assertIn("private worker handoff", family_handler.validate_generative_settings(
                    "minimax_h3", {}, inputs))

    def test_source_audio_is_encoded_conditioned_and_locked_across_sampler_ticks(self):
        import types
        main, runtime, _ = control_tests.OriginalH3ControlSamplerTests.runtime()
        waveform = torch.linspace(-0.2, 0.2, 2 * 165600).reshape(2, 165600)
        original = waveform.clone()
        payload = self.payload()
        payload = H3FaceRefinePayload(payload.video, payload.strength,
                                      payload.frame_multipliers, waveform)
        raw = torch.linspace(-0.3, 0.4, 2 * 32 * 207).reshape(2, 32, 207)
        encoded_inputs, forwarded = [], []
        def encode(value):
            encoded_inputs.append(value.clone())
            return types.SimpleNamespace(latent_dist=types.SimpleNamespace(mode=lambda: raw))
        runtime.audio_vae.encode = encode
        def observe(module, args, kwargs):
            forwarded.append({key: kwargs[key].clone() for key in
                ("audio_hidden_states", "timestep", "timestep_indices", "audio_indices")})
        hook = runtime.transformer.register_forward_pre_hook(observe, with_kwargs=True)
        try:
            with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), \
                    patch.object(main, "_decode_h3_video_rows", return_value=(torch.zeros(1, 3, 124, 32, 32), None)):
                result = runtime.generate("An adult in a violent fictional scene", frame_num=124,
                    height=32, width=32, sampling_steps=4, seed=73, _h3_face_refine=payload,
                    custom_settings={"h3_attention_engine": "sdpa"},
                    callback=lambda *args, **kwargs: waveform.zero_())
        finally:
            hook.remove()
        self.assertIsNotNone(result)
        self.assertEqual(len(encoded_inputs), 1)
        torch.testing.assert_close(encoded_inputs[0], original[:, None], rtol=0, atol=0)
        mean = torch.tensor(main.AUDIO_LATENTS_MEAN).view(1, 32, 1)
        std = torch.tensor(main.AUDIO_LATENTS_STD).view(1, 32, 1)
        expected = ((raw - mean) / std).permute(0, 2, 1).reshape(414, 32)
        self.assertEqual(len(forwarded), 4)
        for call in forwarded:
            torch.testing.assert_close(call["audio_hidden_states"][0], expected, rtol=0, atol=0)
            audio_times = call["timestep"][call["timestep_indices"]][call["audio_indices"]]
            torch.testing.assert_close(audio_times, torch.ones_like(audio_times), rtol=0, atol=0)

    def test_source_audio_clock_validation_and_encoding_cancellation(self):
        base = self.payload()
        for waveform in (torch.zeros(2, 165599), torch.zeros(2, 165600, dtype=torch.float64),
                         torch.full((2, 165600), float("nan")), torch.full((2, 165600), 1.1)):
            with self.assertRaises(ValueError):
                self.validate(H3FaceRefinePayload(base.video, 0.5, base.frame_multipliers, waveform))
        main, runtime, _ = control_tests.OriginalH3ControlSamplerTests.runtime()
        waveform = torch.zeros(2, 165600)
        payload = H3FaceRefinePayload(base.video, 0.5, base.frame_multipliers, waveform)
        def stop(value):
            runtime._interrupt = True
            return torch.zeros(2, 32, 207)
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), \
                patch.object(runtime, "_encode_reference_audio", side_effect=stop):
            self.assertIsNone(runtime.generate("Crop", frame_num=124, height=32, width=32,
                sampling_steps=4, _h3_face_refine=payload,
                custom_settings={"h3_attention_engine": "sdpa"}))
        self.assertEqual(runtime._sampler_prompts, [])


if __name__ == "__main__":
    unittest.main()
