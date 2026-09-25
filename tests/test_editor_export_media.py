"""Real CPU media checks for a non-destructive, frame-aligned Editor cut."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.editor_export import render_single_source_cut  # noqa: E402


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are required")
class EditorExportMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_media(self, command):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace")[-600:])
        return result.stdout

    def make_source(self, *, audio_tracks=2):
        source = self.root / "original.mkv"
        command = [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=24:duration=3",
        ]
        for frequency in (440, 660)[:audio_tracks]:
            command += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=3"]
        command += ["-map", "0:v:0"]
        for index in range(audio_tracks):
            command += ["-map", f"{index + 1}:a:0"]
        command += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source)]
        self.run_media(command)
        return source

    def probe(self, path):
        return json.loads(self.run_media([
            FFPROBE, "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,codec_name,start_time,duration",
            "-of", "json", str(path),
        ]))

    def test_cut_reencodes_video_and_both_audio_tracks_from_zero(self):
        source = self.make_source()
        original_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.root / "cut.mp4"
        result = render_single_source_cut(
            source, destination, source_in=0.5, duration=1.3, timeout=30,
        )
        self.assertEqual(result, str(destination))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_digest)
        media = self.probe(destination)
        streams = media["streams"]
        self.assertEqual([(item["codec_type"], item["codec_name"]) for item in streams], [
            ("video", "h264"), ("audio", "aac"), ("audio", "aac"),
        ])
        for stream in streams:
            self.assertAlmostEqual(float(stream["start_time"]), 0.0, delta=0.025)
        self.assertAlmostEqual(float(streams[0]["duration"]), 1.3, delta=1 / 24 + 0.001)
        for stream in streams[1:]:
            self.assertAlmostEqual(float(stream["duration"]), 1.3, delta=0.035)

    def test_video_without_audio_keeps_a_playable_cut(self):
        source = self.make_source(audio_tracks=0)
        destination = self.root / "silent.mp4"
        render_single_source_cut(source, destination, source_in=1.0, duration=0.5, timeout=30)
        self.assertEqual([stream["codec_type"] for stream in self.probe(destination)["streams"]], ["video"])

    def test_failed_or_cancelled_render_never_replaces_an_output(self):
        source = self.make_source(audio_tracks=0)
        destination = self.root / "cut.mp4"
        def partial_encode(command, **_kwargs):
            Path(command[-1]).write_bytes(b"partial")
            return 1
        with self.assertRaisesRegex(RuntimeError, "encoding failed"):
            render_single_source_cut(source, destination, source_in=0, duration=0.5, runner=partial_encode)
        self.assertFalse(destination.exists())

        def cancelled_encode(command, **_kwargs):
            Path(command[-1]).write_bytes(b"complete")
            return 0
        with self.assertRaisesRegex(InterruptedError, "cancelled"):
            render_single_source_cut(
                source, destination, source_in=0, duration=0.5,
                runner=cancelled_encode, abort_check=lambda: True,
            )
        self.assertFalse(destination.exists())
        destination.write_bytes(b"owned by another export")
        with self.assertRaises(FileExistsError):
            render_single_source_cut(source, destination, source_in=0, duration=0.5)
        self.assertEqual(destination.read_bytes(), b"owned by another export")


if __name__ == "__main__":
    unittest.main()
