"""CPU regression checks for SeedVC's background channel preservation."""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import torch
import torchaudio


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from postprocessing.voice_clone import _ffmpeg_demux_audio, _remix_vocals_with_background


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


if __name__ == "__main__":
    unittest.main()
