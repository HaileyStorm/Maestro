"""CPU-only checks for measured video headers used by finishing history."""

from pathlib import Path
import io
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services.media_info import probe_video_facts


class MediaInfoTests(unittest.TestCase):
    def test_actual_video_headers_and_invalid_file(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg and ffprobe are required")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=64x48:r=24:d=1",
                "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(source),
            ], check=True, timeout=20)
            facts = probe_video_facts(source)
            self.assertIsNotNone(facts)
            self.assertEqual((facts["width"], facts["height"]), (64, 48))
            self.assertEqual(facts["fps"], 24)
            self.assertTrue(facts["has_audio"])
            self.assertEqual(facts["audio_channels"], 1)
            self.assertGreater(facts["duration_seconds"], 0)
            self.assertEqual(facts["size_bytes"], source.stat().st_size)
            self.assertNotIn("name", facts)

            invalid = Path(directory) / "invalid.mp4"
            invalid.write_bytes(b"not video")
            self.assertIsNone(probe_video_facts(invalid))

    def test_oversized_probe_output_is_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"video")
            class OversizedProcess:
                stdout = io.BytesIO(b" " * 65537)
                def poll(self): return 0
                def wait(self, timeout=None): return 0
            with patch("services.media_info.subprocess.Popen", return_value=OversizedProcess()):
                self.assertIsNone(probe_video_facts(path))

    def test_cancellation_stops_a_stalled_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"video")
            real_popen = subprocess.Popen
            def sleeping_probe(*_args, **kwargs):
                return real_popen([
                    sys.executable, "-c",
                    "import sys,time; sys.stdout.write('x'); sys.stdout.flush(); time.sleep(10)",
                ], **kwargs)
            start = time.monotonic()
            with patch("services.media_info.subprocess.Popen", side_effect=sleeping_probe):
                self.assertIsNone(probe_video_facts(
                    path, cancel_check=lambda: time.monotonic() - start >= 0.2))
            self.assertLess(time.monotonic() - start, 2)


if __name__ == "__main__":
    unittest.main()
