"""CPU-only checks for LTX audio and resident decoder recovery contracts."""
from __future__ import annotations

import ast
from fractions import Fraction
import math
import os
from pathlib import Path
import subprocess
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


class TestRetakeFrameRate(unittest.TestCase):
    def setUp(self):
        self.model_path = _APP / "models/ltx2/ltx2.py"
        self.tree = ast.parse(self.model_path.read_text(encoding="utf-8"))
        helper = next(node for node in self.tree.body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "_retake_frame_bounds")
        namespace = {"math": math}
        exec(compile(ast.Module(body=[helper], type_ignores=[]),
                     str(self.model_path), "exec"), namespace)
        self.bounds = namespace["_retake_frame_bounds"]

    def test_time_selection_and_full_tail_survive_frame_rate_conversion(self):
        self.assertEqual(self.bounds(60, 120, 300, 30, 250, 25), (50, 100))
        for end in (-1, 0, 300, 400):
            self.assertEqual(self.bounds(60, end, 300, 30, 251, 25), (50, 251))
        self.assertEqual(self.bounds(0, 124, 124, 24, 124, 24), (0, 124))
        self.assertEqual(self.bounds(59, 60, 60, 60, 25, 25), (24, 25))
        for start, end in ((300, 300), (120, 60)):
            with self.assertRaises(ValueError):
                self.bounds(start, end, 300, 30, 250, 25)

    def test_native_canvas_keeps_source_and_respects_smaller_preset(self):
        tree = _wgp_tree()
        block = next(node for node in ast.walk(tree)
                     if isinstance(node, ast.Assign)
                     and "get_vae_block_size" in ast.unparse(node))
        override = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.If)
                        and any(isinstance(child, ast.Assign)
                                and ast.unparse(child) == "block_size = 32"
                                for child in node.body))
        canvas = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.Assign)
                      and isinstance(node.targets[0], ast.Tuple)
                      and ast.unparse(node.targets[0]) == "(width, height)"
                      and "int(width) // block_size" in ast.unparse(node.value))
        native = next(node for node in ast.walk(self.tree)
                      if isinstance(node, ast.If)
                      and ast.unparse(node.test).startswith("user_h > 0 and user_w > 0"))
        code = compile(ast.Module(body=[block, override, canvas], type_ignores=[]),
                       str(_WGP_PATH), "exec")
        native_code = compile(ast.Module(body=[native], type_ignores=[]),
                              str(self.model_path), "exec")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.touch()
            cases = [
                ("ltx2_22B", "native", str(source), (608, 352), (608, 352)),
                ("ltx2_19B", "native", str(source), (608, 352), (608, 352)),
                ("ltx2_22B", "native", str(source), (320, 192), (320, 192)),
                ("ltx2_22B", "legacy", str(source), (608, 352), (576, 320)),
                ("ltx2_22B", "native", None, (608, 352), (576, 320)),
                ("ltx2_22B", "native", str(source) + ".missing", (608, 352), (576, 320)),
                ("other_model", "native", str(source), (608, 352), (576, 320)),
            ]
            for model, engine, filename, requested, expected in cases:
                with self.subTest(model=model, engine=engine, requested=requested):
                    ns = {"os": os, "base_model_type": model,
                          "model_handler": SimpleNamespace(get_vae_block_size=lambda _: 64),
                          "retake_video": filename, "retake_engine": engine,
                          "width": str(requested[0]), "height": str(requested[1])}
                    exec(code, ns)
                    self.assertEqual((ns["width"], ns["height"]), expected)
                    if engine == "native" and filename == str(source) and model.startswith("ltx2"):
                        ns.update(user_h=ns["height"], user_w=ns["width"], src_h=352, src_w=608)
                        exec(native_code, ns)
                        expected_native = (608, 352) if requested == (608, 352) else (320, 160)
                        self.assertEqual((ns["aligned_w"], ns["aligned_h"]), expected_native)

    def _write_video(self, path, frames, fps, *, engine_index=0, width=32, height=32):
        import av

        # Execute the native extraction's actual stream setup without loading models.
        stream_setup = [node for node in ast.walk(self.tree)
                        if isinstance(node, ast.Assign)
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Attribute)
                        and node.value.func.attr == "add_stream"
                        and "retake_fps" in ast.unparse(node.value)][engine_index]
        with av.open(str(path), mode="w") as container:
            namespace = {"out_container": container, "retake_fps": fps,
                         "Fraction": Fraction}
            exec(compile(ast.Module(body=[stream_setup], type_ignores=[]),
                         str(self.model_path), "exec"), namespace)
            stream = namespace["stream"]
            stream.width, stream.height = width, height
            stream.pix_fmt = "yuv420p"
            stream.options = {"threads": "1"}
            for _ in range(frames):
                frame = av.VideoFrame.from_ndarray(
                    np.zeros((height, width, 3), dtype=np.uint8), format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

    def test_real_conversion_preserves_selected_seconds(self):
        import av

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            converted = Path(directory) / "converted.mp4"
            self._write_video(source, 300, 30)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source),
                            "-vf", "fps=25", "-c:v", "libx264", "-threads", "1",
                            str(converted)], check=True, capture_output=True, timeout=30)
            with av.open(str(converted)) as container:
                frame_count = sum(1 for _ in container.decode(video=0))
            self.assertEqual(frame_count, 250)
            start, end = self.bounds(60, 120, 300, 30, frame_count, 25)
            self.assertEqual((start / 25, end / 25), (2, 4))

    def test_real_fractional_partial_stitch_preserves_frames_and_duration(self):
        import av
        import gc
        import time

        stitch = next(node for node in ast.walk(_wgp_tree())
                      if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "sf > 0 or ef < tf")
        fps = float(Fraction(24_000, 1_001))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            retake = Path(directory) / "retake.mp4"
            self._write_video(source, 240, fps)
            for engine_index in (0, 1):
                self._write_video(retake, 48, fps, engine_index=engine_index)
                with av.open(str(retake)) as container:
                    self.assertEqual(container.streams.video[0].average_rate,
                                     Fraction(24_000, 1_001))
            namespace = {"sf": 48, "ef": 96, "tf": 240, "stitch_fps": fps,
                         "src": str(source), "retake_path": str(retake),
                         "path": str(retake), "subprocess": subprocess,
                         "os": os, "gc": gc, "time": time}
            with mock.patch("builtins.print"):
                exec(compile(ast.Module(body=[stitch], type_ignores=[]),
                             str(_WGP_PATH), "exec"), namespace)
            self.assertEqual(namespace["result"].returncode, 0,
                             namespace["result"].stderr)
            with av.open(str(retake)) as container:
                stream = container.streams.video[0]
                self.assertEqual(stream.average_rate, Fraction(24_000, 1_001))
                duration = float(stream.duration * stream.time_base)
                frame_count = sum(1 for _ in container.decode(video=0))
            self.assertEqual(frame_count, 240)
            self.assertAlmostEqual(duration, 240 / fps, places=3)

    def test_real_audio_mux_keeps_full_and_partial_video_timeline(self):
        import av
        import gc
        import time

        tree = _wgp_tree()
        stitch = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "sf > 0 or ef < tf")
        # Execute both production mux branches, including the actual duration
        # probe, with B-frame video and audio that ends before its final frame.
        outer = next(node for node in ast.walk(tree)
                     if isinstance(node, ast.Try)
                     and any(isinstance(child, ast.Assign)
                             and ast.unparse(child).startswith("video_probe =")
                             for child in node.body))
        begin = next(i for i, node in enumerate(outer.body)
                     if isinstance(node, ast.Assign)
                     and ast.unparse(node).startswith("video_probe ="))
        end = next(i for i, node in enumerate(outer.body)
                   if isinstance(node, ast.If)
                   and ast.unparse(node.test).startswith("should_mux_original and"))
        mux_code = compile(ast.Module(body=outer.body[begin:end + 1], type_ignores=[]),
                           str(_WGP_PATH), "exec")
        stitch_code = compile(ast.Module(body=[stitch], type_ignores=[]), str(_WGP_PATH), "exec")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            audio = Path(directory) / "source.wav"
            source_with_audio = Path(directory) / "source_audio.mp4"
            self._write_video(source, 124, 24, width=608, height=352)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "sine=frequency=440:sample_rate=32000", "-t", "5.152", str(audio)],
                           check=True, capture_output=True, timeout=30)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-i", str(audio),
                            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                            str(source_with_audio)], check=True, capture_output=True, timeout=30)
            for partial in (False, True):
                for regenerate in (False, True):
                    with self.subTest(partial=partial, regenerate=regenerate):
                        output = Path(directory) / "output.mp4"
                        sf, ef = (0, 124) if not partial else (24, 72)
                        self._write_video(output, ef - sf, 24, width=608, height=352)
                        ns = {"sf": sf, "ef": ef, "tf": 124, "stitch_fps": 24,
                              "src": str(source), "retake_path": str(output), "path": str(output),
                              "subprocess": subprocess, "os": os, "gc": gc, "time": time,
                              "math": math, "retake_audio_path": str(audio),
                              "si": {"original_video": str(source_with_audio), "regenerate_audio": regenerate}}
                        with mock.patch("builtins.print"):
                            exec(stitch_code, ns)
                            exec(mux_code, ns)
                        self.assertEqual(ns["mux_result"].returncode, 0, ns["mux_result"].stderr)
                        with av.open(str(output)) as container:
                            video = container.streams.video[0]
                            self.assertEqual(video.average_rate, Fraction(24))
                            self.assertEqual((video.width, video.height), (608, 352))
                            self.assertAlmostEqual(float(video.duration * video.time_base), 124 / 24, places=3)
                            self.assertEqual(sum(1 for _ in container.decode(video=0)), 124)
                        with av.open(str(output)) as container:
                            audio_stream = container.streams.audio[0]
                            self.assertLessEqual(float(audio_stream.duration * audio_stream.time_base), 124 / 24 + .04)
                            if not regenerate:
                                self.assertAlmostEqual(float(audio_stream.duration * audio_stream.time_base), 5.152, delta=.04)

    def test_partial_result_uses_segment_clock_for_encoding_and_completeness(self):
        tree = _wgp_tree()
        binding = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.If)
                       and ast.unparse(node.test) == "_retake_stitch_info is not None"
                       and any(isinstance(child, ast.Name) and child.id == "current_video_length"
                               for child in ast.walk(node)))
        abort = next(node for node in ast.walk(tree)
                     if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "abort"
                             for target in node.targets)
                     and "sample.shape[1] < current_video_length" in ast.unparse(node))
        output_rate = next(node for node in ast.walk(tree)
                           if isinstance(node, ast.Assign)
                           and ast.unparse(node) == "output_fps = fps")
        namespace = {"fps": 25, "current_video_length": 300,
                     "math": math,
                     "_retake_stitch_info": {"fps": 24, "start_frame": 48, "end_frame": 96,
                                              "total_frames": 300},
                     "sample": SimpleNamespace(shape=(3, 48, 32, 32)),
                     "abort_scheduled": False, "is_image": False, "audio_only": False}
        code = compile(ast.Module(body=[binding, abort, output_rate], type_ignores=[]),
                       str(_WGP_PATH), "exec")
        exec(code, namespace)
        self.assertEqual(namespace["fps"], 24)
        self.assertEqual(namespace["output_fps"], 24)
        self.assertFalse(namespace["abort"])
        namespace["sample"] = SimpleNamespace(shape=(3, 47, 32, 32))
        exec(code, namespace)
        self.assertTrue(namespace["abort"])
        namespace["abort_scheduled"] = False
        namespace["sample"] = SimpleNamespace(shape=(3, 124, 32, 32))
        namespace["_retake_stitch_info"] = {
            "fps": 24, "start_frame": 0, "end_frame": 124, "total_frames": 124,
        }
        exec(code, namespace)
        self.assertFalse(namespace["abort"])
        self.assertEqual(namespace["output_fps"], 24)
        namespace["_retake_stitch_info"]["fps"] = 0
        with self.assertRaises(ValueError):
            exec(code, namespace)
        namespace["_retake_stitch_info"]["fps"] = 24
        namespace["abort_scheduled"] = True
        exec(code, namespace)
        self.assertTrue(namespace["abort"])


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
