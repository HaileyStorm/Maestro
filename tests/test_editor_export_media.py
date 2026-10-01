"""Real CPU media checks for a non-destructive, frame-aligned Editor cut."""

from __future__ import annotations

import hashlib
import array
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.editor_export import render_single_source_cut, render_video_sequence  # noqa: E402


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

    def test_sequence_joins_mixed_canvas_fps_and_audio_at_exact_frames(self):
        sources = []
        for name, color, size, fps, audio in (("red", "red", "128x72", 24, True), ("blue", "blue", "72x128", 30, False)):
            source = self.root / f"{name}.mp4"
            command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                       "-f", "lavfi", "-i", f"color={color}:s={size}:r={fps}:d=2"]
            if audio:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac"]
            command += ["-c:v", "libx264", "-threads", "2", "-preset", "ultrafast", str(source)]
            self.run_media(command)
            sources.append(source)
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
        clips = [{"path": str(path), "source_in": 0.25, "duration": 0.55, "has_audio": index == 0}
                 for index, path in enumerate(sources)]
        destination = self.root / "sequence.mp4"
        render_video_sequence(clips, destination, width=128, height=72, fps=24, timeout=30)
        media = self.probe(destination)
        self.assertEqual([(stream["codec_type"], stream["codec_name"]) for stream in media["streams"]], [("video", "h264"), ("audio", "aac")])
        self.assertAlmostEqual(float(media["streams"][0]["duration"]), 26 / 24, delta=0.001)
        pixels = self.run_media([FFMPEG, "-v", "error", "-i", str(destination), "-map", "0:v", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
        stride = 128 * 72 * 3
        self.assertEqual(len(pixels), stride * 26)
        center = (36 * 128 + 64) * 3
        for frame in (0, 12):
            red, _, blue = pixels[frame * stride + center:frame * stride + center + 3]
            self.assertGreater(red, 200)
            self.assertLess(blue, 30)
        for frame in (13, 25):
            red, _, blue = pixels[frame * stride + center:frame * stride + center + 3]
            self.assertLess(red, 30)
            self.assertGreater(blue, 200)
        # Portrait source is fitted, preserving black letterbox outside the image.
        self.assertLess(max(pixels[13 * stride:13 * stride + 3]), 10)
        samples = array.array("f", self.run_media([FFMPEG, "-v", "error", "-i", str(destination), "-map", "0:a", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"]))
        self.assertGreater(max(abs(value) for value in samples[4800:19200]), 0.03)
        self.assertLess(max(abs(value) for value in samples[33600:43200]), 0.001)
        self.assertEqual([hashlib.sha256(path.read_bytes()).hexdigest() for path in sources], hashes)
        def cancelled(command, **_options):
            Path(command[-1]).write_bytes(b"partial")
            return 0
        with self.assertRaises(InterruptedError):
            render_video_sequence(clips, self.root / "cancelled.mp4", width=128, height=72, fps=24, runner=cancelled, abort_check=lambda: True)
        self.assertFalse((self.root / "cancelled.mp4").exists())
        with self.assertRaises(FileExistsError):
            render_video_sequence(clips, destination, width=128, height=72, fps=24)
        self.assertFalse(list(self.root.glob(".editor-sequence-*")))

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
