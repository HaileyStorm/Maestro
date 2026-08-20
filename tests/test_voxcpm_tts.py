"""Catalog and script-breakdown contracts for VoxCPM 2."""
from __future__ import annotations

import json
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
_HANDLER = _APP / "models" / "TTS" / "voxcpm_handler.py"
_DEFAULTS = _APP / "defaults" / "voxcpm2.json"
_WGP = _APP / "wgp.py"
_ENHANCERS = _APP / "models" / "TTS" / "prompt_enhancers.py"


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
