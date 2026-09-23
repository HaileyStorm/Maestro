"""CPU ffmpeg coverage for the reversible horizontal-flip transform."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.video_transform import horizontal_flip  # noqa: E402


def _binary(env_name: str, fallback: str) -> str | None:
    configured = os.environ.get(env_name)
    if configured:
        return configured if Path(configured).is_file() else shutil.which(configured)
    return shutil.which(fallback)


FFMPEG = _binary("FFMPEG_BINARY", "ffmpeg")
FFPROBE = _binary("FFPROBE_BINARY", "ffprobe")


def _supports_encoder(ffmpeg: str | None, encoder: str) -> bool:
    if not ffmpeg:
        return False
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    return completed.returncode == 0 and any(
        len(parts) >= 2 and parts[1] == encoder
        for parts in (line.split() for line in completed.stdout.splitlines())
    )


WEBM_SUPPORT = _supports_encoder(FFMPEG, "libvpx-vp9") and _supports_encoder(FFMPEG, "libopus")


def _run(command: list[str], *, input_data: bytes | None = None) -> bytes:
    completed = subprocess.run(
        command,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise AssertionError(f"command failed ({completed.returncode}): {detail}")
    return completed.stdout


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_asymmetric_source(ffmpeg: str, root: Path) -> Path:
    width, height, frame_count = 32, 16, 4
    frame = bytearray()
    for _y in range(height):
        for _x in range(width):
            frame.extend((235, 30, 30) if _x < width // 2 else (30, 30, 235))
    raw = root / "source.rgb"
    raw.write_bytes(bytes(frame) * frame_count)
    source = root / "source.mp4"
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            "5",
            "-i",
            str(raw),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000:duration=0.8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:sample_rate=8000:duration=0.8",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "8000",
            "-ac",
            "1",
            "-b:a",
            "32k",
            str(source),
        ]
    )
    return source


def _decode_first_frame(ffmpeg: str, path: Path) -> tuple[int, int, bytes]:
    raw = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
    )
    return 32, 16, raw


def _patch_mean(frame: bytes, width: int, height: int, x0: int, x1: int) -> tuple[float, ...]:
    values: list[int] = []
    for y in range(2, height - 2):
        for x in range(x0, x1):
            offset = (y * width + x) * 3
            values.extend(frame[offset : offset + 3])
    count = len(values) // 3
    return tuple(sum(values[channel::3]) / count for channel in range(3))


def _audio_hash(ffmpeg: str, path: Path, index: int) -> str:
    encoded = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-map",
            f"0:a:{index}",
            "-c",
            "copy",
            "-f",
            "adts",
            "pipe:1",
        ]
    )
    return hashlib.sha256(encoded).hexdigest()


def _audio_stream_count(ffprobe: str, path: Path) -> int:
    output = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    return len([line for line in output.decode("utf-8").splitlines() if line.strip()])


def _stream_copy_hash(ffmpeg: str, path: Path, index: int) -> str:
    encoded = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-map",
            f"0:a:{index}",
            "-c",
            "copy",
            "-f",
            "data",
            "pipe:1",
        ]
    )
    return hashlib.sha256(encoded).hexdigest()


def _stream_codecs(ffprobe: str, path: Path) -> list[tuple[str, str]]:
    output = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ]
    )
    return [
        (stream["codec_type"], stream["codec_name"])
        for stream in json.loads(output)["streams"]
    ]


def _probe(ffprobe: str, path: Path) -> tuple[dict, dict]:
    output = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,r_frame_rate,nb_frames:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(output)
    video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
    return video, payload["format"]


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are required")
class HorizontalFlipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="maestro-video-transform-")
        self.root = Path(self._temporary.name)
        self.source = _make_asymmetric_source(FFMPEG, self.root)
        self.source_digest = _sha256(self.source)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _assert_no_temporary_siblings(self) -> None:
        self.assertEqual(list(self.root.glob(".horizontal-flip-*")), [])

    def test_actual_flip_preserves_audio_geometry_rate_and_duration(self) -> None:
        destination = self.root / "flipped.mp4"
        result = horizontal_flip(self.source, destination, timeout=30)
        self.assertEqual(result, str(destination.resolve()))
        self.assertTrue(destination.is_file())
        self.assertEqual(_sha256(self.source), self.source_digest)

        source_frame = _decode_first_frame(FFMPEG, self.source)
        flipped_frame = _decode_first_frame(FFMPEG, destination)
        self.assertEqual(source_frame[:2], flipped_frame[:2])
        width, height = source_frame[:2]
        source_left = _patch_mean(source_frame[2], width, height, 3, 12)
        source_right = _patch_mean(source_frame[2], width, height, 20, 29)
        flipped_left = _patch_mean(flipped_frame[2], width, height, 3, 12)
        flipped_right = _patch_mean(flipped_frame[2], width, height, 20, 29)
        for expected, observed in zip(source_right, flipped_left):
            self.assertLess(abs(expected - observed), 24)
        for expected, observed in zip(source_left, flipped_right):
            self.assertLess(abs(expected - observed), 24)

        source_audio_count = _audio_stream_count(FFPROBE, self.source)
        flipped_audio_count = _audio_stream_count(FFPROBE, destination)
        self.assertEqual(source_audio_count, flipped_audio_count)
        self.assertGreaterEqual(source_audio_count, 2)
        self.assertEqual(
            [_audio_hash(FFMPEG, self.source, index) for index in range(source_audio_count)],
            [_audio_hash(FFMPEG, destination, index) for index in range(flipped_audio_count)],
        )
        source_video, source_format = _probe(FFPROBE, self.source)
        flipped_video, flipped_format = _probe(FFPROBE, destination)
        self.assertEqual((source_video["width"], source_video["height"]),
                         (flipped_video["width"], flipped_video["height"]))
        self.assertEqual(source_video["r_frame_rate"], flipped_video["r_frame_rate"])
        if source_video.get("nb_frames") and flipped_video.get("nb_frames"):
            self.assertEqual(source_video["nb_frames"], flipped_video["nb_frames"])
        self.assertAlmostEqual(float(source_format["duration"]),
                               float(flipped_format["duration"]), delta=0.05)
        self._assert_no_temporary_siblings()

    def test_existing_destination_is_never_overwritten(self) -> None:
        destination = self.root / "existing.mp4"
        destination.write_bytes(b"keep this output")
        with self.assertRaises(FileExistsError):
            horizontal_flip(self.source, destination)
        self.assertEqual(destination.read_bytes(), b"keep this output")
        self.assertEqual(_sha256(self.source), self.source_digest)
        self._assert_no_temporary_siblings()

    def test_source_and_destination_must_differ(self) -> None:
        with self.assertRaises(ValueError):
            horizontal_flip(self.source, self.source)

    def test_failure_and_cancellation_remove_partial_temporary_outputs(self) -> None:
        for error in (RuntimeError("encoder failed"), InterruptedError("cancelled"), TimeoutError("timed out")):
            with self.subTest(error=type(error).__name__):
                destination = self.root / f"{type(error).__name__}.mp4"

                def failing_runner(command, *, timeout, abort_check):
                    Path(command[-1]).write_bytes(b"partial")
                    raise error

                with self.assertRaises(type(error)):
                    horizontal_flip(self.source, destination, runner=failing_runner)
                self.assertFalse(destination.exists())
                self._assert_no_temporary_siblings()

    def test_nonzero_runner_status_does_not_publish(self) -> None:
        destination = self.root / "failed.mp4"

        def failed_runner(command, *, timeout, abort_check):
            Path(command[-1]).write_bytes(b"partial")
            return 1

        with self.assertRaisesRegex(RuntimeError, "^horizontal flip encoding failed$"):
            horizontal_flip(self.source, destination, runner=failed_runner)
        self.assertFalse(destination.exists())
        self._assert_no_temporary_siblings()

    @unittest.skipUnless(WEBM_SUPPORT, "VP9 and Opus encoders are required")
    def test_webm_vp9_opus_branch_keeps_suffix_and_audio_packets(self) -> None:
        source = self.root / "source.webm"
        destination = self.root / "flipped.webm"
        _run(
            [
                FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(self.source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "libvpx-vp9",
                "-deadline",
                "realtime",
                "-cpu-used",
                "8",
                "-crf",
                "30",
                "-b:v",
                "0",
                "-c:a",
                "libopus",
                "-ar",
                "8000",
                "-ac",
                "1",
                str(source),
            ]
        )
        source_digest = _sha256(source)
        source_audio = _stream_copy_hash(FFMPEG, source, 0)
        result = horizontal_flip(source, destination, timeout=30)
        self.assertEqual(result, str(destination.resolve()))
        self.assertEqual(destination.suffix, ".webm")
        self.assertEqual(_sha256(source), source_digest)
        self.assertEqual(_stream_codecs(FFPROBE, source), [("video", "vp9"), ("audio", "opus")])
        self.assertEqual(_stream_codecs(FFPROBE, destination), [("video", "vp9"), ("audio", "opus")])
        self.assertEqual(source_audio, _stream_copy_hash(FFMPEG, destination, 0))
        source_video, source_format = _probe(FFPROBE, source)
        output_video, output_format = _probe(FFPROBE, destination)
        self.assertEqual((source_video["width"], source_video["height"]),
                         (output_video["width"], output_video["height"]))
        self.assertEqual(source_video["r_frame_rate"], output_video["r_frame_rate"])
        self.assertAlmostEqual(float(source_format["duration"]),
                               float(output_format["duration"]), delta=0.05)
        self._assert_no_temporary_siblings()

    def test_mov_pcm_branch_keeps_suffix_and_audio_packets(self) -> None:
        source = self.root / "source.mov"
        destination = self.root / "flipped.mov"
        _run(
            [
                FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(self.source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "8000",
                "-ac",
                "1",
                str(source),
            ]
        )
        source_digest = _sha256(source)
        source_audio = _stream_copy_hash(FFMPEG, source, 0)
        result = horizontal_flip(source, destination, timeout=30)
        self.assertEqual(result, str(destination.resolve()))
        self.assertEqual(destination.suffix, ".mov")
        self.assertEqual(_sha256(source), source_digest)
        self.assertEqual(_stream_codecs(FFPROBE, source), [("video", "h264"), ("audio", "pcm_s16le")])
        self.assertEqual(_stream_codecs(FFPROBE, destination), [("video", "h264"), ("audio", "pcm_s16le")])
        self.assertEqual(source_audio, _stream_copy_hash(FFMPEG, destination, 0))
        source_video, source_format = _probe(FFPROBE, source)
        output_video, output_format = _probe(FFPROBE, destination)
        self.assertEqual((source_video["width"], source_video["height"]),
                         (output_video["width"], output_video["height"]))
        self.assertEqual(source_video["r_frame_rate"], output_video["r_frame_rate"])
        self.assertAlmostEqual(float(source_format["duration"]),
                               float(output_format["duration"]), delta=0.05)
        self._assert_no_temporary_siblings()


if __name__ == "__main__":
    unittest.main()
