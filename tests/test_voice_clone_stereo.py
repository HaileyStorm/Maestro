"""CPU regression checks for SeedVC background channels and cancellation."""

from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import torch
import torchaudio


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from postprocessing.voice_clone import (
    _ffmpeg_demux_audio, _remix_vocals_with_background, apply_voice_clone_to_file,
)


class VoiceCloneStereoTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for demux")
    def test_demux_retains_stereo_source_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            extracted = root / "extracted.wav"
            original = torch.stack((torch.full((4096,), 0.1), torch.full((4096,), 0.3)))
            torchaudio.save(str(source), original, 44100)

            self.assertTrue(_ffmpeg_demux_audio(str(source), str(extracted), sample_rate=44100))
            audio, rate = torchaudio.load(str(extracted))
            self.assertEqual(rate, 44100)
            self.assertEqual(audio.shape[0], 2)
            self.assertAlmostEqual(float((audio[1] - audio[0]).mean()), 0.2, delta=0.001)

    def test_remix_centers_voice_without_collapsing_background(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            voice = root / "voice.wav"
            background = root / "background.wav"
            remixed = root / "remixed.wav"
            torchaudio.save(str(voice), torch.full((1, 4096), 0.2), 44100)
            torchaudio.save(
                str(background),
                torch.stack((torch.full((4096,), 0.1), torch.full((4096,), 0.3))),
                44100,
            )

            _remix_vocals_with_background(str(voice), str(background), str(remixed))
            audio, rate = torchaudio.load(str(remixed))
            self.assertEqual(rate, 44100)
            self.assertEqual(audio.shape[0], 2)
            self.assertAlmostEqual(float(audio[0].mean()), 0.3, delta=0.001)
            self.assertAlmostEqual(float(audio[1].mean()), 0.5, delta=0.001)
            self.assertAlmostEqual(float((audio[1] - audio[0]).mean()), 0.2, delta=0.001)

    def test_cancel_after_vocal_separation_skips_seedvc_model_load(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "source.mp4"
            reference = Path(directory) / "reference.wav"
            video.write_bytes(b"original")
            reference.write_bytes(b"reference")
            cancelled = False

            def get_stems(*_args):
                nonlocal cancelled
                cancelled = True
                return "vocals.wav", "instrumental.wav"

            seedvc = types.ModuleType("postprocessing.seedvc")
            seedvc.download_assets = Mock()
            seedvc.get_model = Mock()
            separator = types.ModuleType("preprocessing.extract_vocals")
            separator.get_stems = get_stems
            with patch.dict(sys.modules, {
                "postprocessing.seedvc": seedvc,
                "preprocessing.extract_vocals": separator,
            }), patch("postprocessing.voice_clone._ffmpeg_demux_audio", return_value=True):
                result = apply_voice_clone_to_file(
                    str(video), [str(reference)], cancel_check=lambda: cancelled,
                )

            self.assertFalse(result)
            seedvc.download_assets.assert_not_called()
            seedvc.get_model.assert_not_called()
            self.assertEqual(video.read_bytes(), b"original")

    def test_cancel_after_seedvc_conversion_skips_remux(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "source.mp4"
            reference = Path(directory) / "reference.wav"
            video.write_bytes(b"original")
            reference.write_bytes(b"reference")
            cancelled = False

            def convert_tensor(**_kwargs):
                nonlocal cancelled
                cancelled = True
                return torch.zeros((2, 64))

            seedvc = types.ModuleType("postprocessing.seedvc")
            seedvc.download_assets = Mock()
            seedvc.get_model = Mock(return_value=types.SimpleNamespace(convert_tensor=convert_tensor))
            separator = types.ModuleType("preprocessing.extract_vocals")
            separator.get_stems = Mock(side_effect=RuntimeError("unavailable"))
            with patch.dict(sys.modules, {
                "postprocessing.seedvc": seedvc,
                "preprocessing.extract_vocals": separator,
            }), patch("postprocessing.voice_clone._ffmpeg_demux_audio", return_value=True), \
                    patch("postprocessing.voice_clone.torchaudio.load", return_value=(torch.zeros((1, 64)), 44100)), \
                    patch("postprocessing.voice_clone._load_reference_voice", return_value=(torch.zeros((1, 64)), 44100)), \
                    patch("postprocessing.voice_clone.torch.cuda.is_available", return_value=False), \
                    patch("postprocessing.voice_clone._ffmpeg_remux_audio") as remux:
                result = apply_voice_clone_to_file(
                    str(video), [str(reference)], cancel_check=lambda: cancelled,
                )

            self.assertFalse(result)
            remux.assert_not_called()
            self.assertEqual(video.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
