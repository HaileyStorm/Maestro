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

        self.assertGreaterEqual(source.count("_audio_waveform_sample_count("), 5)
        self.assertIn(
            'elif model_def.get("audio_guide_window_slicing", False):',
            source,
        )
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


if __name__ == "__main__":
    unittest.main()
