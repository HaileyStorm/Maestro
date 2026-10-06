"""CPU-only checks for LTX audio and resident decoder recovery contracts."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import numpy as np


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
_WGP_PATH = _APP / "wgp.py"
if os.fspath(_APP) not in sys.path:
    sys.path.insert(0, os.fspath(_APP))


def _wgp_tree() -> ast.Module:
    return ast.parse(_WGP_PATH.read_text(encoding="utf-8"))


def _load_wgp_helpers(*names: str) -> dict[str, object]:
    selected = [
        node
        for node in _wgp_tree().body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if len(selected) != len(names):
        raise AssertionError(f"Missing WGP helpers: {names}")
    namespace = {"os": os}
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(_WGP_PATH), "exec"),
        namespace,
    )
    return namespace


class TestLtxAudioRuntimeContracts(unittest.TestCase):
    def test_standalone_audio_inference_preserves_explicit_and_control_modes(self):
        normalize = _load_wgp_helpers(
            "_normalize_audio_prompt_type_from_guide"
        )["_normalize_audio_prompt_type_from_guide"]
        enabled = {"infer_audio_prompt_from_guide": True}

        self.assertEqual(
            normalize(enabled, 0, "song.wav", None, "", "NV"),
            "ANV",
        )
        for selector in ("A", "K", "2"):
            self.assertEqual(
                normalize(enabled, 0, "song.wav", None, "", selector),
                selector,
            )
        self.assertEqual(
            normalize(enabled, 0, "song.wav", "control.mp4", "V", ""),
            "",
        )
        self.assertEqual(
            normalize(enabled, 1, "song.wav", None, "", ""),
            "",
        )
        self.assertEqual(normalize({}, 0, "song.wav", None, "", "N"), "N")
        self.assertEqual(normalize(enabled, 0, None, None, "", "N"), "N")
        for malformed_selector in (["K"], {"source": "K"}, 2):
            with self.subTest(malformed_selector=malformed_selector):
                self.assertEqual(
                    normalize(
                        enabled,
                        0,
                        "song.wav",
                        None,
                        "",
                        malformed_selector,
                    ),
                    "A",
                )

    def test_sample_count_accepts_channel_and_sample_major_waveforms(self):
        count = _load_wgp_helpers("_audio_waveform_sample_count")[
            "_audio_waveform_sample_count"
        ]

        self.assertEqual(count(np.zeros((2, 48_000), dtype=np.float32)), 48_000)
        self.assertEqual(count(np.zeros((48_000, 2), dtype=np.float32)), 48_000)
        self.assertEqual(count(np.zeros((48_000,), dtype=np.float32)), 48_000)
        self.assertEqual(count(np.zeros((2, 0), dtype=np.float32)), 0)
        self.assertEqual(count(None), 0)

    def test_generated_audio_uses_model_sample_rate_with_safe_fallback(self):
        resolve = _load_wgp_helpers("resolve_generated_audio_sampling_rate")[
            "resolve_generated_audio_sampling_rate"
        ]

        self.assertEqual(resolve({"audio_sampling_rate": 24_000}, 16_000), 24_000)
        self.assertEqual(resolve({}, 16_000), 16_000)
        self.assertEqual(resolve({"audio_sampling_rate": 0}, 16_000), 16_000)
        self.assertEqual(resolve({"audio_sampling_rate": "invalid"}, 16_000), 16_000)
        self.assertEqual(resolve(None, 16_000), 16_000)

    def test_conditioning_role_keeps_original_delivery_audio(self):
        namespace = _load_wgp_helpers(
            "_validate_audio_conditioning_guide",
            "_resolve_audio_guide_roles",
        )
        resolve = namespace["_resolve_audio_guide_roles"]
        validate = namespace["_validate_audio_conditioning_guide"]

        self.assertEqual(resolve("soundtrack.wav", None), (
            "soundtrack.wav",
            "soundtrack.wav",
        ))
        self.assertIsNone(validate(""))

        with tempfile.TemporaryDirectory() as directory:
            conditioning_path = Path(directory) / "voice.wav"
            conditioning_path.touch()
            self.assertEqual(
                resolve("soundtrack.wav", conditioning_path),
                ("soundtrack.wav", os.fspath(conditioning_path)),
            )

            missing_path = Path(directory) / "private-missing.wav"
            with self.assertRaises(ValueError) as caught:
                resolve("soundtrack.wav", missing_path)
            self.assertNotIn(os.fspath(missing_path), str(caught.exception))

        with self.assertRaises(ValueError):
            resolve("soundtrack.wav", ["one.wav", "two.wav"])

    def test_non_ltx_model_rejects_conditioning_before_model_load(self):
        namespace = _load_wgp_helpers(
            "_validate_audio_conditioning_guide",
            "_is_ltx25_runtime",
            "_validate_audio_conditioning_guide_for_model",
        )
        validate = namespace[
            "_validate_audio_conditioning_guide_for_model"
        ]

        self.assertIsNone(validate({}, None))
        self.assertIsNone(validate({}, ""))
        with tempfile.TemporaryDirectory() as directory:
            conditioning_path = Path(directory) / "private-voice.wav"
            conditioning_path.touch()
            with self.assertRaises(ValueError) as caught:
                validate({}, conditioning_path)
            self.assertNotIn(os.fspath(conditioning_path), str(caught.exception))
            self.assertEqual(
                validate({"ltx25_native_runtime": True}, conditioning_path),
                os.fspath(conditioning_path),
            )
            self.assertEqual(
                validate({"external_runtime": "ltx25"}, conditioning_path),
                os.fspath(conditioning_path),
            )

        source = _WGP_PATH.read_text(encoding="utf-8")
        generation = source[source.index("def _generate_video_impl("):]
        guard_index = generation.index(
            "_validate_audio_conditioning_guide_for_model("
        )
        reprofile_index = generation.index(
            "configuration_reprofiled = _release_for_model_reprofile("
        )
        self.assertLess(guard_index, reprofile_index)

    def test_generation_routes_original_audio_to_final_mux(self):
        source = _WGP_PATH.read_text(encoding="utf-8")

        self.assertIn('"audio_conditioning_guide", "audio_source"', source)
        self.assertIn("audio_conditioning_guide=None", source)
        self.assertIn("original_audio_guide = audio_guide", source)
        self.assertIn(
            "original_audio_guide, audio_guide = _resolve_audio_guide_roles(",
            source,
        )
        self.assertIn("else (original_audio_guide or audio_source)", source)
        self.assertIn(
            'concat_configs["audio_guide"] = original_audio_guide',
            source,
        )
        self.assertEqual(
            source.count(
                "audio_guide6,\n            validated_audio_conditioning_guide,"
            ),
            2,
        )

    def test_audio_window_paths_use_layout_neutral_sample_counts(self):
        source = _WGP_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        native_window_conditions = [
            node.test
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and "audio_guide_window_slicing" in ast.unparse(node.test)
            and not any(
                isinstance(child, ast.Name) and child.id == "audio_guide"
                for child in ast.walk(node.test)
            )
        ]

        self.assertGreaterEqual(source.count("_audio_waveform_sample_count("), 5)
        self.assertEqual(len(native_window_conditions), 1)
        native_window_predicate = compile(
            ast.Expression(native_window_conditions[0]),
            str(_WGP_PATH),
            "eval",
        )
        self.assertTrue(eval(native_window_predicate, {}, {
            "model_def": {"audio_guide_window_slicing": True},
            "semantic_reference_mode": False,
        }))
        self.assertFalse(eval(native_window_predicate, {}, {
            "model_def": {"audio_guide_window_slicing": True},
            "semantic_reference_mode": True,
        }))
        self.assertIn("input_fills_window = (", source)
        self.assertIn("resolve_generated_audio_sampling_rate(", source)
        self.assertIn('or "D" in audio_prompt_type', source)
        self.assertNotIn("input_waveform.shape[0] == 0", source)

    def test_ltx25_decoder_reuses_same_resident_variant_and_reloads_change(self):
        namespace = _load_wgp_helpers(
            "_is_ltx25_runtime",
            "_resolve_ltx25_video_vae_request",
            "_apply_ltx25_video_vae_request",
        )
        apply_request = namespace["_apply_ltx25_video_vae_request"]
        handler_module = ModuleType("models.ltx25.ltx25_handler")
        handler_module.normalize_video_vae_variant = lambda value: {
            None: "fast",
            "fast": "fast",
            "conv": "fast",
            "nad": "nad",
            "diffusion": "nad",
        }[value]
        ltx25 = {
            "ltx25_native_runtime": True,
            "ltx25_video_vae_default": "fast",
        }

        with mock.patch.dict(
            sys.modules,
            {"models.ltx25.ltx25_handler": handler_module},
        ):
            kwargs = {"other_model_state": "preserved"}
            self.assertFalse(apply_request({}, "nad", None, kwargs, False))
            self.assertEqual(kwargs, {"other_model_state": "preserved"})

            kwargs = {}
            self.assertFalse(apply_request(ltx25, None, None, kwargs, False))
            self.assertEqual(kwargs, {"ltx25_video_vae": "fast"})

            kwargs = {}
            self.assertFalse(
                apply_request(
                    ltx25,
                    "conv",
                    SimpleNamespace(video_vae_variant="fast"),
                    kwargs,
                    False,
                )
            )
            self.assertEqual(kwargs, {"ltx25_video_vae": "fast"})

            kwargs = {}
            self.assertTrue(
                apply_request(
                    ltx25,
                    "diffusion",
                    SimpleNamespace(video_vae_variant="fast"),
                    kwargs,
                    False,
                )
            )
            self.assertEqual(kwargs, {"ltx25_video_vae": "nad"})

            kwargs = {}
            self.assertTrue(
                apply_request(
                    ltx25,
                    "fast",
                    SimpleNamespace(video_vae_variant="fast"),
                    kwargs,
                    True,
                )
            )
            self.assertEqual(kwargs, {"ltx25_video_vae": "fast"})

    def test_ltx25_decoder_is_forwarded_without_clobbering_other_model_kwargs(self):
        source = _WGP_PATH.read_text(encoding="utf-8")

        self.assertIn('ltx25_video_vae="fast"', source)
        self.assertIn('model_kwargs["ltx25_video_vae"]', source)
        self.assertIn("wan_model if model_type == transformer_type else None", source)
        self.assertIn("reload_needed = _apply_ltx25_video_vae_request(", source)
        self.assertIn('model_kwargs["VAE_upsampling"]', source)
        self.assertNotIn('model_kwargs = {"VAE_upsampling"', source)


class TestNativeRetakeFrameBoundary(unittest.TestCase):
    def _run_pipeline(self, frames, *, end_frame=-1, mask_path=None, source_frames=None):
        import torch

        source_path = _APP / "models/ltx2/ltx_pipelines/retake.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        pipeline_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                              and node.name == "RetakePipeline")
        observed = {}
        actual_frames = frames if source_frames is None else source_frames
        source = torch.arange(actual_frames, dtype=torch.float32).reshape(1, 1, -1, 1, 1)
        original = source.clone()

        def encode(video, encoder, tiling):
            # Native VAE's temporal contract, also used by its tiled encoder.
            self.assertEqual((video.shape[2] - 1) % 8, 0)
            observed["encoder_input"] = video.clone()
            return torch.zeros(1, 1, (video.shape[2] - 1) // 8 + 1, 1, 1)

        def denoise(**kwargs):
            observed["denoise"] = kwargs
            self.assertEqual((kwargs["output_shape"].frames - 1) // 8 + 1,
                             kwargs["initial_video_latent"].shape[2])
            observed["model_frames"] = kwargs["output_shape"].frames
            return (SimpleNamespace(latent=kwargs["initial_video_latent"]),
                    SimpleNamespace(latent=torch.zeros(1)))

        def decode_video(latent, decoder, tiling, **kwargs):
            observed["decode_frames"] = kwargs["expected_frames"]
            available = (latent.shape[2] - 1) * 8 + 1
            return torch.zeros(min(available, kwargs["expected_frames"]), 1, 1, 3)

        namespace = {
            "torch": torch, "os": os, "log": mock.Mock(),
            "PipelineComponents": lambda **kwargs: SimpleNamespace(video_scale_factors=SimpleNamespace(time=8)),
            "TextEncoderCache": lambda: SimpleNamespace(encode=lambda *args, **kwargs: [(None, None)]),
            "GaussianNoiser": mock.Mock(), "EulerDiffusionStep": mock.Mock(),
            "resolve_text_connectors": lambda *args: (None, None, None),
            "cleanup_memory": lambda: None, "load_video_conditioning": lambda **kwargs: source,
            "vae_encode_video": encode, "TemporalRegionMask": lambda **kwargs: SimpleNamespace(**kwargs),
            "SpatialRegionMask": lambda **kwargs: SimpleNamespace(**kwargs),
            "VideoPixelShape": lambda **kwargs: SimpleNamespace(**kwargs),
            "DISTILLED_SIGMA_VALUES": [1.0, 0.0], "bind_interrupt_check": lambda *args: None,
            "simple_denoising_func": mock.Mock(), "denoise_audio_video": denoise,
            "vae_decode_video_to_tensor": decode_video,
            "vae_decode_audio": lambda *args: torch.zeros(2, round(observed["model_frames"] * 48000 / 24)),
        }
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                                 pipeline_class], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(source_path), "exec"), namespace)
        models = SimpleNamespace(**{name: object() for name in
            ("text_encoder", "video_encoder", "transformer", "video_decoder", "audio_decoder")},
            vocoder=SimpleNamespace(output_sampling_rate=48000))
        pipeline = namespace["RetakePipeline"](models, device="cpu", dtype=torch.float32)
        with mock.patch.object(torch.cuda, "synchronize"):
            video, audio = pipeline("private-source.mp4", "change the lighting", 247804,
                                    32, 32, frames, 24, end_frame=end_frame,
                                    spatial_mask_path=mask_path)
        self.assertTrue(torch.equal(source, original), "Source tensor must remain unchanged")
        return video, audio, observed

    def test_arbitrary_cut_preserves_exact_video_and_audio_duration(self):
        for frames, model_frames in ((124, 129), (53, 57), (121, 121), (129, 129), (1, 1)):
            with self.subTest(frames=frames):
                video, audio, observed = self._run_pipeline(frames)
                self.assertEqual(observed["model_frames"], model_frames)
                self.assertEqual(video.shape[0], frames)
                self.assertEqual(audio.shape[-1], frames * 2000)
                self.assertEqual(observed["denoise"]["conditionings"][0].end_frame,
                                 (model_frames - 1) // 8 + 1)
                tail = observed["encoder_input"][0, 0, frames - 1:, 0, 0]
                self.assertTrue(bool((tail == frames - 1).all()))

    def test_partial_temporal_and_spatial_masks_keep_the_requested_region(self):
        _, _, observed = self._run_pipeline(124, end_frame=65)
        self.assertEqual(observed["denoise"]["conditionings"][0].end_frame, 9)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mask.npy"
            mask = np.zeros((124, 2, 2), dtype=bool)
            mask[-1, 0, 0] = True
            np.save(path, mask)
            _, _, observed = self._run_pipeline(124, mask_path=str(path))
            result = observed["denoise"]["conditionings"][0].pixel_mask
            self.assertEqual(result.shape[0], 129)
            self.assertTrue(bool(result[123:, 0, 0].all()))
            self.assertTrue(np.array_equal(np.load(path), mask))

    def test_source_frame_mismatch_is_rejected_before_encoding(self):
        with self.assertRaisesRegex(ValueError, "source frame count"):
            self._run_pipeline(124, source_frames=121)


if __name__ == "__main__":
    unittest.main()
