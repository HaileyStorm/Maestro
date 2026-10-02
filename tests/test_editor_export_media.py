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
    def test_title_positions_stacking_and_narrow_canvas_fit(self):
        from services.editor_export import _title_filters
        from PIL import Image
        _title_filters([{"id": "long", "text": "W" * 160, "start": 0, "duration": 1, "position": "top"}],
            self.root, width=64, height=64, duration=1, fps=24, first_input=1, base="cut")
        with Image.open(self.root / "title-0.png") as raster:
            self.assertLessEqual(raster.width, 64 * 0.9)
        source = self.root / "positions.mkv"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=black:s=128x72:r=24:d=1",
            "-c:v", "libx264", "-threads", "2", str(source)])
        first = {"id": "wide", "text": "MMMMMMMM", "start": 0, "duration": 1, "position": "top"}
        second = {**first, "id": "narrow", "text": "IIIIIIII"}
        pixels = []
        for index, layers in enumerate(([first, second], [second, first], [{**first, "position": "bottom"}])):
            destination = self.root / f"position-{index}.mp4"
            render_single_source_cut(source, destination, source_in=0, duration=1, text_layers=layers,
                canvas={"width": 128, "height": 72, "fps": 24}, timeout=30)
            frame = self.run_media([FFMPEG, "-v", "error", "-i", str(destination), "-frames:v", "1",
                "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
            pixels.append(frame)
            rows = [row for row in range(72) if max(frame[row * 384:(row + 1) * 384]) > 80]
            self.assertTrue(rows)
            self.assertLess(max(rows), 20) if index < 2 else self.assertGreater(min(rows), 50)
        self.assertNotEqual(pixels[0], pixels[1])

    def test_blank_title_still_fits_odd_source_to_saved_canvas(self):
        source = self.root / "odd.mkv"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=127x71:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-map", "0:v", "-map", "1:a",
            "-c:v", "ffv1", "-threads", "2", "-c:a", "pcm_s16le", str(source)])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.root / "blank.mp4"
        render_single_source_cut(source, destination, source_in=0, duration=1,
            text_layers=[{"id": "blank", "text": "", "start": 0, "duration": 1, "position": "top"}],
            canvas={"width": 128, "height": 72, "fps": 24}, timeout=30)
        streams = json.loads(self.run_media([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(destination)]))["streams"]
        self.assertEqual((streams[0]["width"], streams[0]["height"]), (128, 72))
        self.assertEqual([stream["codec_type"] for stream in streams], ["video", "audio"])
        self.assertAlmostEqual(float(streams[0]["duration"]), 1, delta=0.001)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertFalse(list(self.root.glob(".editor-titles-*")))

    def test_literal_titles_have_half_open_times_across_a_join_and_keep_all_cut_audio(self):
        source = self.root / "black.mkv"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=black:s=128x72:r=24:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-f", "lavfi", "-i", "sine=frequency=660:duration=3",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-threads", "2", "-c:a", "aac", str(source)])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        layer = {"id": "literal", "text": "[v]; 'hello' %{text}", "start": 0.75, "duration": 0.5, "position": "center"}
        for sequence in (False, True):
            destination = self.root / f"titles-{sequence}.mp4"
            if sequence:
                render_video_sequence([{"path": source, "source_in": 0, "duration": 1, "has_audio": True}] * 2,
                    destination, width=128, height=72, fps=24, text_layers=[layer], timeout=30)
            else:
                render_single_source_cut(source, destination, source_in=0.5, duration=2,
                    text_layers=[layer], canvas={"width": 128, "height": 72}, timeout=30)
            streams = self.probe(destination)["streams"]
            self.assertEqual(len([item for item in streams if item["codec_type"] == "audio"]), 1 if sequence else 2)
            self.assertAlmostEqual(float(streams[0]["duration"]), 2, delta=0.001)
            frames = self.run_media([FFMPEG, "-v", "error", "-i", str(destination), "-map", "0:v", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
            stride = 128 * 72 * 3
            self.assertEqual(len(frames), stride * 48)
            for frame in (0, 17, 30, 47):
                self.assertLess(max(frames[frame * stride:(frame + 1) * stride]), 12)
            for frame in (18, 23, 24, 29):
                self.assertGreater(max(frames[frame * stride:(frame + 1) * stride]), 150)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertFalse(list(self.root.glob(".editor-titles-*")))

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
