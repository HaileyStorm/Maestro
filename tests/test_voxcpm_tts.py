"""Catalog and script-breakdown contracts for VoxCPM 2."""
from __future__ import annotations

import json
import ast
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
_HANDLER = _APP / "models" / "TTS" / "voxcpm_handler.py"
_DEFAULTS = _APP / "defaults" / "voxcpm2.json"
_WGP = _APP / "wgp.py"
_ENHANCERS = _APP / "models" / "TTS" / "prompt_enhancers.py"


class VoxCpmModelFolderTests(unittest.TestCase):
    def setUp(self):
        if str(_APP) not in sys.path:
            sys.path.insert(0, str(_APP))
        from models.TTS import voxcpm_handler
        from shared.utils import files_locator

        self.handler = voxcpm_handler
        self.locator = files_locator
        self.old_roots = list(files_locator.get_checkpoints_paths())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(files_locator.set_checkpoints_paths, self.old_roots)
        self.primary = Path(self.temp.name) / "downloads"
        self.fallback = Path(self.temp.name) / "installed"
        files_locator.set_checkpoints_paths([str(self.primary), str(self.fallback)])

    def install(self, root, files=None):
        folder = root / "voxcpm2"
        folder.mkdir(parents=True, exist_ok=True)
        if files is None:
            files = self.handler._get_voxcpm2_download_def()["fileList"][0]
        for name in files:
            (folder / name).write_text("disposable model fixture", encoding="utf-8")
        return folder

    def load_handler(self):
        # Exercise the actual handler without importing torch or constructing a model.
        pipeline_module = types.ModuleType("models.TTS.voxcpm.pipeline")
        package = types.ModuleType("models.TTS.voxcpm")
        package.__path__ = []

        class Pipeline:
            def __init__(self, ckpt_root):
                self.ckpt_root = ckpt_root
                self.model = object()

            def load(self):
                return self

        pipeline_module.VoxCpmPipeline = Pipeline
        with patch.dict(sys.modules, {
            package.__name__: package, pipeline_module.__name__: pipeline_module,
        }):
            return self.handler.family_handler.load_model(
                None, "voxcpm2", "voxcpm2", {},
            )

    def pipeline_constructor(self):
        # Execute the unchanged native constructor body with explicit CPU and a
        # forbidden CUDA probe; the rest of the model module is never imported.
        path = _APP / "models" / "TTS" / "voxcpm" / "pipeline.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "VoxCpmPipeline")
        ctor = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                    and n.name == "__init__")
        module = ast.Module(body=[ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), ctor], type_ignores=[])
        namespace = dict(vars(self.handler))
        namespace.update(os=os, fl=self.locator, torch=types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: self.fail("CUDA probe")),
        ))
        exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
        return type("ConstructorFixture", (), {"__init__": namespace["__init__"]})

    def test_loader_finds_complete_fallback_with_empty_or_partial_primary(self):
        complete = self.install(self.fallback)
        for partial in (False, True):
            with self.subTest(partial_primary=partial):
                if partial:
                    self.install(self.primary, ["config.json"])
                self.assertIsNone(self.handler.family_handler.validate_generative_settings(
                    "voxcpm2", {}, {},
                ))
                pipeline, components = self.load_handler()
                self.assertEqual(Path(pipeline.ckpt_root), complete)
                self.assertIs(components["voxcpm"], pipeline.model)

    def test_complete_primary_keeps_priority(self):
        complete = self.install(self.primary)
        self.install(self.fallback)
        pipeline, _ = self.load_handler()
        self.assertEqual(Path(pipeline.ckpt_root), complete)

    def test_split_incomplete_install_is_rejected_before_pipeline(self):
        self.install(self.primary, ["config.json", "model.safetensors"])
        self.install(self.fallback, ["audiovae.pth", "tokenizer.json"])
        self.assertIsNotNone(self.handler.family_handler.validate_generative_settings(
            "voxcpm2", {}, {},
        ))
        with self.assertRaisesRegex(RuntimeError, "weights are not installed"):
            self.load_handler()

    def test_default_constructor_uses_fallback_and_preserves_explicit_root(self):
        complete = self.install(self.fallback)
        constructor = self.pipeline_constructor()
        self.assertEqual(Path(constructor(device="cpu").ckpt_root), complete)
        explicit = str(Path(self.temp.name) / "explicit")
        self.assertEqual(constructor(ckpt_root=explicit, device="cpu").ckpt_root, explicit)


class VoxCpmCatalogTests(unittest.TestCase):
    def test_defaults_and_handler_are_registered(self):
        payload = json.loads(_DEFAULTS.read_text(encoding="utf-8"))
        self.assertEqual(payload["model"]["architecture"], "voxcpm2")
        self.assertIn("openbmb/VoxCPM2", payload["model"]["URLs"][0])
        handler = _HANDLER.read_text(encoding="utf-8")
        pipeline = (_APP / "models" / "TTS" / "voxcpm" / "pipeline.py").read_text(
            encoding="utf-8",
        )
        self.assertIn('with torch.device("cpu")', pipeline)
        self.assertNotIn("torch.device(self.device)", pipeline)
        self.assertIn('audio_sampling_rate', pipeline)
        self.assertIn("48000", pipeline)
        self.assertIn("reference_wav_path", pipeline)
        self.assertIn("format_voxcpm_turn_text", pipeline)
        self.assertIn("return [VOXCPM2_MODEL_TYPE]", handler)
        self.assertIn('VOXCPM2_MODEL_TYPE = "voxcpm2"', handler)
        self.assertIn("Write speakers and emotion", handler)
        self.assertIn("weights are not installed", handler)
        self.assertIn("audiovae.pth", handler)
        self.assertIn("tokenization_voxcpm2.py", handler)
        self.assertIn("VoxCpmPipeline", handler)
        wgp = _WGP.read_text(encoding="utf-8")
        self.assertIn("models.TTS.voxcpm_handler", wgp)
        enhancers = _ENHANCERS.read_text(encoding="utf-8")
        self.assertIn("VOXCPM_SCRIPT_PROMPT", enhancers)
        self.assertIn("Speaker N [emotion]", enhancers)

    def test_script_breakdown_reads_speakers_and_emotions(self):
        import sys

        if str(_APP) not in sys.path:
            sys.path.insert(0, str(_APP))
        from services.tts_script_breakdown import parse_tts_script_turns

        turns = parse_tts_script_turns(
            "Speaker 1 [warm]: Hello there.\n"
            "Speaker 2 [dry]: That is what you said yesterday.\n"
            "And I meant it.\n"
        )
        self.assertEqual(turns[0]["speaker"], "Speaker 1")
        self.assertEqual(turns[0]["emotion"], "warm")
        self.assertEqual(turns[1]["emotion"], "dry")
        self.assertIn("meant it", turns[1]["line"])
        self.assertEqual(parse_tts_script_turns(""), [])

    def test_voice_design_is_parenthetical_not_spoken(self):
        import sys

        if str(_APP) not in sys.path:
            sys.path.insert(0, str(_APP))
        from services.tts_script_breakdown import format_voxcpm_turn_text

        text = format_voxcpm_turn_text(
            "I checked the schedule twice, and everything still lines up.",
            emotion="warm",
            alt_prompt="warm, unhurried, slightly amused",
        )
        self.assertTrue(text.startswith("(warm, unhurried, slightly amused, warm)"))
        self.assertIn("I checked the schedule twice", text)
        self.assertNotIn("[warm]", text)
        self.assertFalse(text.startswith("warm, unhurried"))

    def test_catalog_defaults_match_handler_and_script_shape(self):
        payload = json.loads(_DEFAULTS.read_text(encoding="utf-8"))
        handler = _HANDLER.read_text(encoding="utf-8")
        self.assertEqual(payload["duration_seconds"], 20)
        self.assertEqual(payload["multi_prompts_gen_type"], 2)
        self.assertEqual(
            payload["prompt"],
            "Speaker 1 [warm]: I checked the schedule twice, and everything still lines up.",
        )
        self.assertEqual(payload["alt_prompt"], "warm, unhurried, slightly amused")
        self.assertIn('"duration_seconds": 20', handler)
        self.assertIn('"multi_prompts_gen_type": 2', handler)
        self.assertIn('"video_length": 0', handler)
        self.assertIn('"num_inference_steps": 0', handler)
        self.assertIn('"audio_prompt_type": "A"', handler)
        self.assertIn('"audio_only": True', handler)
        self.assertIn('"image_outputs": False', handler)
        self.assertIn('"inference_steps": False', handler)
        self.assertIn('"default": 20', handler)
        self.assertIn('"placeholder": "warm, unhurried, slightly amused"', handler)

        import sys

        if str(_APP) not in sys.path:
            sys.path.insert(0, str(_APP))
        from services.tts_script_breakdown import (
            format_voxcpm_turn_text,
            parse_tts_script_turns,
        )

        turns = parse_tts_script_turns(payload["prompt"])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["speaker"], "Speaker 1")
        self.assertEqual(turns[0]["emotion"], "warm")
        spoken = format_voxcpm_turn_text(
            turns[0]["line"],
            emotion=turns[0]["emotion"],
            alt_prompt=payload["alt_prompt"],
        )
        self.assertTrue(spoken.startswith("(warm, unhurried, slightly amused, warm)"))
        self.assertIn(turns[0]["line"], spoken)
        self.assertNotIn("[warm]", spoken)


if __name__ == "__main__":
    unittest.main()
