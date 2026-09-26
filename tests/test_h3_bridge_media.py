"""CPU-only synthetic media checks for native MiniMax H3 Bridge assembly."""

from __future__ import annotations

import io
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from dataclasses import replace
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from services.h3_bridge_media import (
    H3BridgeMediaCancelled,
    H3BridgeMediaError,
    SourceProbe,
    _verify_cfr_timestamps,
    assemble_bridge,
    prepare_guides,
    probe_source,
    validate_bridge_source_dimensions,
)
from services.h3_bridge_plan import plan_h3_bridge

_WIDTH = 64
_HEIGHT = 64
_FPS = 24
_SAMPLES_PER_SECOND = 48_000
_FRAME_BYTES_YUV420P = _WIDTH * _HEIGHT * 3 // 2


def _write_audio(path: Path, frames: int, *, frequency: int) -> None:
    sample_count = frames * (_SAMPLES_PER_SECOND // _FPS)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(_SAMPLES_PER_SECOND)
        samples = bytearray()
        for index in range(sample_count):
            sample = int(
                12_000 * math.sin(2 * math.pi * frequency * index / _SAMPLES_PER_SECOND)
            )
            samples.extend(struct.pack("<h", sample))
        output.writeframes(samples)


def _write_marked_audio(path: Path, frames: int) -> None:
    """Use distinct tones in generated hidden head, published span, and tail."""

    start = 17 * (_SAMPLES_PER_SECOND // _FPS)
    end = 90 * (_SAMPLES_PER_SECOND // _FPS)
    sample_count = frames * (_SAMPLES_PER_SECOND // _FPS)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(_SAMPLES_PER_SECOND)
        samples = bytearray()
        for index in range(sample_count):
            if index < start:
                frequency, phase_index = 220, index
            elif index < end:
                frequency, phase_index = 880, index - start
            else:
                frequency, phase_index = 440, index - end
            sample = int(
                12_000
                * math.sin(2 * math.pi * frequency * phase_index / _SAMPLES_PER_SECOND)
            )
            samples.extend(struct.pack("<h", sample))
        output.writeframes(samples)


def _write_video(
    path: Path,
    frames: int,
    *,
    fps: int = _FPS,
    audio_path: Path | None = None,
) -> None:
    """Create deterministic grayscale frame markers in a lossless CPU clip."""

    raw_path = path.with_name(path.stem + ".rgb")
    raw = bytearray()
    for frame in range(frames):
        gray = round(frame * 255 / max(1, frames - 1))
        raw.extend(bytes((gray,)) * (_WIDTH * _HEIGHT * 3))
    raw_path.write_bytes(raw)

    command = [
        shutil.which("ffmpeg") or "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-threads",
        "1",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{_WIDTH}x{_HEIGHT}",
        "-framerate",
        str(fps),
        "-i",
        str(raw_path),
    ]
    if audio_path is not None:
        command.extend(["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0"])
    else:
        command.extend(["-map", "0:v:0"])
    command.extend(
        [
            "-frames:v",
            str(frames),
            "-c:v",
            "ffv1",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if audio_path is not None:
        command.extend(["-c:a", "pcm_s16le", "-ar", str(_SAMPLES_PER_SECOND)])
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True)


def _source_commitment(probe, start: int, end: int) -> dict[str, object]:
    return {
        "sha256": probe.sha256,
        "start_frame": start,
        "end_frame_exclusive": end,
    }


def _decode_yuv420(path: Path) -> list[bytes]:
    result = subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    data = result.stdout
    if len(data) % _FRAME_BYTES_YUV420P:
        raise AssertionError("decoder returned a partial video frame")
    return [
        data[index : index + _FRAME_BYTES_YUV420P]
        for index in range(0, len(data), _FRAME_BYTES_YUV420P)
    ]


def _luma_mean(frame: bytes) -> int:
    return sum(frame[: _WIDTH * _HEIGHT]) // (_WIDTH * _HEIGHT)


def _decode_audio_s16(path: Path) -> list[int]:
    result = subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ar",
            str(_SAMPLES_PER_SECOND),
            "-ac",
            "1",
            "-f",
            "s16le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    raw = result.stdout[: len(result.stdout) // 2 * 2]
    return [sample[0] for sample in struct.iter_unpack("<h", raw)]


def _frequency(
    samples: list[int], start_seconds: float, *, window_seconds: float = 0.1
) -> float:
    start = round(start_seconds * _SAMPLES_PER_SECOND)
    end = start + round(window_seconds * _SAMPLES_PER_SECOND)
    window = samples[start:end]
    crossings = sum(
        1
        for left, right in pairwise(window)
        if (left < 0 <= right) or (right < 0 <= left)
    )
    return crossings / (2 * window_seconds)


def _plan(
    a_probe,
    b_probe,
    *,
    a_range=(24, 80),
    b_range=(20, 76),
    audio_mode="generated",
    trims=True,
):
    options = {"hidden_head_frames": 17, "hidden_tail_frames": 17} if trims else {}
    return plan_h3_bridge(
        _source_commitment(a_probe, *a_range),
        _source_commitment(b_probe, *b_range),
        generated_frames=107,
        bridge_audio_mode=audio_mode,
        **options,
    )


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg tools are required"
)
class H3BridgeMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="h3-bridge-media-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_probe_normalizes_arbitrary_cfr_to_the_24fps_clock(self) -> None:
        source = self.root / "source-30fps.mkv"
        _write_video(source, 120, fps=30)

        probe = probe_source(source)

        self.assertEqual(probe.fps.numerator, 30)
        self.assertEqual(probe.frame_count, 120)
        self.assertEqual(probe.frames_24fps, 96)
        self.assertEqual(probe.duration, 4)
        self.assertEqual(probe.duration_24fps, 4)
        self.assertFalse(probe.has_audio)
        self.assertTrue(probe.sha256.startswith("sha256:"))

    def test_probe_rejects_oversized_sparse_media_before_tools_or_hashing(self) -> None:
        source = self.root / "oversized-sparse.bin"
        with source.open("wb") as output:
            output.truncate(8 * 1024**3 + 1)

        with (
            patch("services.h3_bridge_media._tools") as tools,
            patch("services.h3_bridge_media._hash_file") as hash_file,
            patch("services.h3_bridge_media._verify_cfr_timestamps") as scan,
            self.assertRaisesRegex(H3BridgeMediaError, "8 GiB size limit"),
        ):
            probe_source(source)

        tools.assert_not_called()
        hash_file.assert_not_called()
        scan.assert_not_called()

    def test_probe_applies_duration_and_fps_bounds_before_hash_or_timestamp_scan(
        self,
    ) -> None:
        source = self.root / "mocked-source.bin"
        source.write_bytes(b"synthetic probe input")

        def payload(*, fps: str, duration: str | None) -> bytes:
            video = {
                "codec_type": "video",
                "avg_frame_rate": fps,
                "r_frame_rate": fps,
                "time_base": "1/240",
                "width": 64,
                "height": 64,
            }
            media_format = {}
            if duration is not None:
                video["duration"] = duration
                media_format["duration"] = duration
            return json.dumps({"streams": [video], "format": media_format}).encode()

        for body, message in (
            (payload(fps="24/1", duration="1801"), "30-minute duration limit"),
            (payload(fps="241/1", duration="1"), "240 fps admission limit"),
            (payload(fps="24/1", duration=None), "unavailable or ambiguous"),
        ):
            with (
                patch("services.h3_bridge_media._run_command", return_value=body),
                patch("services.h3_bridge_media._hash_file") as hash_file,
                patch("services.h3_bridge_media._verify_cfr_timestamps") as scan,
                self.assertRaisesRegex(H3BridgeMediaError, message),
            ):
                probe_source(source)
            hash_file.assert_not_called()
            scan.assert_not_called()

    def test_probe_caps_timestamp_scan_and_rechecks_actual_duration(self) -> None:
        source = self.root / "bounded-source.bin"
        source.write_bytes(b"synthetic probe input")
        digest = "sha256:" + "0" * 64
        body = json.dumps(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "avg_frame_rate": "240/1",
                        "r_frame_rate": "240/1",
                        "time_base": "1/240",
                        "duration": "1800",
                        "width": 64,
                        "height": 64,
                    }
                ],
                "format": {"duration": "1800"},
            }
        ).encode()
        with (
            patch("services.h3_bridge_media._run_command", return_value=body),
            patch("services.h3_bridge_media._hash_file", return_value=digest),
            patch(
                "services.h3_bridge_media._verify_cfr_timestamps",
                return_value=432_000,
            ) as scan,
        ):
            probe = probe_source(source)
        self.assertEqual(probe.frame_count, 432_000)
        self.assertEqual(scan.call_args.kwargs["max_frame_count"], 432_000)

        too_long_body = body.replace(b'"1800"', b'"1"')
        with (
            patch("services.h3_bridge_media._run_command", return_value=too_long_body),
            patch("services.h3_bridge_media._hash_file", return_value=digest),
            patch(
                "services.h3_bridge_media._verify_cfr_timestamps",
                return_value=432_001,
            ) as scan,
            self.assertRaisesRegex(H3BridgeMediaError, "30-minute duration limit"),
        ):
            probe_source(source)
        self.assertEqual(scan.call_args.kwargs["max_frame_count"], 432_000)

    def test_timestamp_scan_stops_at_its_derived_frame_bound(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = io.BytesIO(b"0\n1\n2\n3\n")

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def kill(self) -> None:
                return None

        process = FakeProcess()
        with (
            patch("services.h3_bridge_media.subprocess.Popen", return_value=process),
            self.assertRaisesRegex(H3BridgeMediaError, "30-minute duration limit"),
        ):
            _verify_cfr_timestamps(
                "ffprobe",
                self.root / "synthetic.mkv",
                fps=Fraction(24, 1),
                time_base=Fraction(1, 24),
                cancel_check=None,
                max_frame_count=3,
            )

    def test_dimension_admission_rejects_per_side_and_pixel_area_bounds(self) -> None:
        baseline = SourceProbe(
            sha256="sha256:" + "0" * 64,
            fps=24,
            frame_count=96,
            frames_24fps=96,
            width=64,
            height=64,
            has_audio=False,
            audio_channels=None,
            audio_duration=None,
        )
        validate_bridge_source_dimensions(baseline, baseline)
        for width, height in ((4097, 64), (4000, 4000)):
            with self.subTest(width=width, height=height):
                invalid = replace(baseline, width=width, height=height)
                with self.assertRaisesRegex(
                    H3BridgeMediaError,
                    "4096 pixels per side or 12 megapixels",
                ):
                    validate_bridge_source_dimensions(baseline, invalid)

    def test_guides_preserve_selected_frame_mapping_and_reject_drift_or_bounds(
        self,
    ) -> None:
        a_path, b_path = self.root / "a.mkv", self.root / "b.mkv"
        _write_video(a_path, 96)
        _write_video(b_path, 96)
        a_probe, b_probe = probe_source(a_path), probe_source(b_path)
        plan = _plan(a_probe, b_probe)
        temp_dir = self.root / "guides"
        temp_dir.mkdir()

        a_guide, b_guide = prepare_guides(a_path, b_path, plan, temp_dir)

        self.assertEqual(a_guide.name, "a-tail.mp4")
        self.assertEqual(b_guide.name, "b-head.mp4")
        for guide, source, expected_range in (
            (a_guide, a_path, range(24, 80)),
            (b_guide, b_path, range(20, 76)),
        ):
            expected_luma = [_luma_mean(frame) for frame in _decode_yuv420(source)]
            actual_luma = [_luma_mean(frame) for frame in _decode_yuv420(guide)]
            self.assertEqual(len(actual_luma), 56)
            for observed, expected_frame in zip(actual_luma, expected_range):
                nearest = min(
                    range(len(expected_luma)),
                    key=lambda index: abs(expected_luma[index] - observed),
                )
                self.assertEqual(nearest, expected_frame)

        invalid_range_plan = _plan(a_probe, b_probe, a_range=(41, 97))
        with self.assertRaisesRegex(H3BridgeMediaError, "exceeds the normalized video"):
            prepare_guides(a_path, b_path, invalid_range_plan, temp_dir)

        a_path.write_bytes(a_path.read_bytes() + b"source drift")
        with self.assertRaisesRegex(H3BridgeMediaError, "hash does not match"):
            prepare_guides(a_path, b_path, plan, temp_dir)

    def test_assembly_trims_bridge_and_keeps_audio_in_plan_order(self) -> None:
        a_path, b_path = self.root / "a.mkv", self.root / "b.mkv"
        generated_path = self.root / "generated.mkv"
        a_audio = self.root / "a.wav"
        generated_audio = self.root / "generated.wav"
        _write_audio(a_audio, 96, frequency=440)
        _write_marked_audio(generated_audio, 107)
        _write_video(a_path, 96, audio_path=a_audio)
        _write_video(b_path, 96)
        _write_video(generated_path, 107, audio_path=generated_audio)
        a_probe, b_probe = probe_source(a_path), probe_source(b_path)
        plan = _plan(a_probe, b_probe)
        staging = self.root / "assembled.mkv"

        summary = assemble_bridge(a_path, b_path, generated_path, plan, staging)

        self.assertEqual(summary["fps"], 24)
        self.assertEqual(summary["frame_count"], 229)
        self.assertEqual(summary["clip_a_frames"], 80)
        self.assertEqual(summary["bridge_published_frames"], 73)
        self.assertEqual(summary["clip_b_frames"], 76)
        self.assertTrue(summary["audio_present"])
        self.assertEqual(summary["bridge_audio_selected_start_tick"], 28)
        self.assertEqual(summary["bridge_audio_selected_end_tick"], 150)
        self.assertEqual(summary["bridge_audio_selected_ticks"], 122)
        self.assertEqual(summary["bridge_audio_video_duration_frames"], 73)
        self.assertEqual(summary["bridge_audio_video_duration_fps"], 24)
        self.assertTrue(summary["generated_sha256"].startswith("sha256:"))
        self.assertNotIn(str(staging), repr(summary))
        output_probe = probe_source(staging)
        self.assertEqual(output_probe.frames_24fps, 229)
        self.assertTrue(output_probe.has_audio)

        audio = _decode_audio_s16(staging)
        a_duration = 80 / _FPS
        bridge_duration = 73 / _FPS
        self.assertAlmostEqual(_frequency(audio, 0.5), 440, delta=45)
        # Hidden generated audio before frame 17 must not become the bridge head.
        self.assertAlmostEqual(_frequency(audio, a_duration + 0.1), 880, delta=60)
        self.assertAlmostEqual(
            _frequency(audio, a_duration + bridge_duration - 0.2), 880, delta=60
        )
        silence_start = round(
            (a_duration + bridge_duration + 0.2) * _SAMPLES_PER_SECOND
        )
        silence_end = round((a_duration + bridge_duration + 0.3) * _SAMPLES_PER_SECOND)
        self.assertLess(
            max(abs(value) for value in audio[silence_start:silence_end]), 250
        )

        original_output = staging.read_bytes()
        with self.assertRaisesRegex(
            H3BridgeMediaError, "staging output already exists"
        ):
            assemble_bridge(a_path, b_path, generated_path, plan, staging)
        self.assertEqual(staging.read_bytes(), original_output)

        short_audio = self.root / "short-audio.wav"
        short_generated = self.root / "short-generated.mkv"
        _write_audio(short_audio, 24, frequency=880)
        _write_video(short_generated, 107, audio_path=short_audio)
        short_staging = self.root / "short-audio-stage.mkv"
        with self.assertRaisesRegex(
            H3BridgeMediaError, "does not cover the published tick range"
        ):
            assemble_bridge(a_path, b_path, short_generated, plan, short_staging)
        self.assertFalse(short_staging.exists())

        a_path.write_bytes(a_path.read_bytes() + b"source drift")
        drift_staging = self.root / "drift-stage.mkv"
        with self.assertRaisesRegex(H3BridgeMediaError, "hash does not match"):
            assemble_bridge(a_path, b_path, generated_path, plan, drift_staging)
        self.assertFalse(drift_staging.exists())

    def test_silent_assembly_omits_audio_and_cancellation_leaves_no_stage(self) -> None:
        a_path, b_path = self.root / "a.mkv", self.root / "b.mkv"
        generated_path = self.root / "generated.mkv"
        _write_video(a_path, 96)
        _write_video(b_path, 96)
        _write_video(generated_path, 107)
        a_probe, b_probe = probe_source(a_path), probe_source(b_path)
        plan = _plan(
            a_probe,
            b_probe,
            a_range=(40, 96),
            b_range=(0, 56),
            audio_mode="silent",
            trims=False,
        )
        staging = self.root / "silent.mkv"

        with self.assertRaises(H3BridgeMediaCancelled):
            assemble_bridge(
                a_path,
                b_path,
                generated_path,
                plan,
                staging,
                cancel_check=lambda: True,
            )
        self.assertFalse(staging.exists())

        summary = assemble_bridge(a_path, b_path, generated_path, plan, staging)
        self.assertEqual(summary["frame_count"], 299)
        self.assertFalse(summary["audio_present"])
        self.assertFalse(probe_source(staging).has_audio)


if __name__ == "__main__":
    unittest.main()
