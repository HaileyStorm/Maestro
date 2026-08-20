"""Locks leftover 1.9.0 `generate_video` AST probes to Continuum impl names.

The public wrapper is H3 OOM relief only (`*args, **kwargs`). Residency
evidence, repeat-offset dispatch, durable recovery paths, and premux
preprocess labels live on `_generate_video_impl`. Do not invent those
helpers on the wrapper.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WGP_PATH = ROOT / "app" / "wgp.py"
WGP_SOURCE = WGP_PATH.read_text(encoding="utf-8")


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function {name!r} not found")


def _source(name: str) -> str:
    segment = ast.get_source_segment(WGP_SOURCE, _function(ast.parse(WGP_SOURCE), name))
    if segment is None:
        raise AssertionError(f"No source for {name!r}")
    return segment


class GenerateVideoImplLeftoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(WGP_SOURCE, filename=str(WGP_PATH))
        cls.wrapper = _source("generate_video")
        cls.impl = _source("_generate_video_impl")
        cls.load_models = _source("load_models")

    def test_wrapper_is_oom_relief_only(self):
        self.assertIn("H3OomReliefRetry", self.wrapper)
        self.assertIn("_generate_video_impl(*bound_args, **bound_kwargs)", self.wrapper)
        self.assertNotIn("requested_residency_evidence_context", self.wrapper)
        self.assertNotIn("repeat_start_offset", self.wrapper)
        self.assertNotIn("durable_output_dir = candidate", self.wrapper)
        self.assertNotIn("_recovery_preprocess_path(", self.wrapper)

    def test_load_and_impl_wire_residency_evidence_once(self):
        self.assertIn("return_template=True", self.load_models)
        self.assertIn(
            "_register_model_residency_evidence_context(\n"
            "        residency_key, template=residency_template,",
            self.load_models,
        )
        self.assertEqual(
            self.impl.count("requested_residency_evidence_context = "),
            1,
        )
        self.assertIn(
            "residency_context=requested_residency_evidence_context",
            self.impl,
        )
        clear_current = self.impl.index(
            "_clear_current_model_residency_evidence_context()"
        )
        self.assertLess(clear_current, self.impl.index("_auto_aspect ="))
        self.assertIn(
            "derive_current_model_residency_evidence_context(\n"
            "        finalized_residency_evidence_context,",
            self.impl,
        )
        self.assertEqual(
            self.impl.count("derive_current_model_residency_evidence_context("),
            1,
        )
        finalized = self.impl.index("finalized_residency_evidence_context = ")
        self.assertLess(
            self.impl.index(
                "video_length = align_model_frame_count(video_length, model_def)"
            ),
            finalized,
        )
        self.assertLess(
            self.impl.index("width, height = resolution.split"),
            finalized,
        )
        self.assertLess(
            self.impl.index("first_window_video_length = current_video_length"),
            finalized,
        )
        finalized_source = self.impl[finalized:]
        self.assertIn('resolution=f"{width}x{height}"', finalized_source)
        self.assertIn("frame_count=first_window_video_length", finalized_source)
        self.assertIn("loras=loras_selected", finalized_source)
        self.assertIn("attention_backend=attn", finalized_source)

    def test_completed_repeat_offset_skips_only_outer_dispatch(self):
        generate = _function(self.tree, "_generate_video_impl")
        arguments = [argument.arg for argument in generate.args.args]
        self.assertIn("repeat_start_offset", arguments)
        self.assertIn("completed_repeats = max(", self.impl)
        self.assertIn("int(repeat_start_offset or 0)", self.impl)
        self.assertIn("repeat_no = 0", self.impl)

    def test_native_recovery_uses_private_stable_target(self):
        self.assertIn("durable_output_dir = candidate", self.impl)
        self.assertIn("output_dir = durable_output_dir", self.impl)
        self.assertIn("durable_output_dir or save_path", self.impl)
        self.assertIn("{durable_file_stem}-audio-tmp.wav", self.impl)

    def test_recovery_preprocessing_audio_uses_private_unit_prefix(self):
        validation = self.impl.index(
            'raise RuntimeError("Recovery output staging identity is invalid")'
        )
        first_preprocess = self.impl.index(
            '_recovery_preprocess_path("control-audio")'
        )
        self.assertLess(validation, first_preprocess)
        for label in (
            "control-audio", "clean-audio-1", "clean-audio-2",
            "speaker-1", "speaker-2", "speaker-clean", "clean-audio",
            "clip-offset",
        ):
            with self.subTest(label=label):
                self.assertIn(
                    f'_recovery_preprocess_path("{label}")', self.impl,
                )
        self.assertIn(
            'f"{durable_output_prefix}-pre-audio-norm-"', self.impl,
        )
        self.assertIn(
            'f"{durable_output_prefix}-pre-null-"', self.impl,
        )


if __name__ == "__main__":
    unittest.main()
