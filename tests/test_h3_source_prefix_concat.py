"""CPU media regressions for sealed H3 source-prefix concatenation."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
WGP = ROOT / "app" / "wgp.py"
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _load_concat():
    names = {
        "PostDecodeStageError",
        "_h3_source_prefix_media_info",
        "_h3_source_prefix_temporal_filter_parts",
        "_snapshot_h3_source_prefix",
        "snapshot_h3_source_prefix",
        "_verify_h3_source_prefix_output",
        "concatenate_multi_clip_videos",
        "extract_h3_source_prefix_last_frame",
        "get_exact_video_frame_count",
    }
    tree = ast.parse(WGP.read_text(encoding="utf-8"), filename=str(WGP))
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    namespace = {
        "math": math,
        "os": os,
        "subprocess": subprocess,
        "tempfile": tempfile,
        "time": time,
    }
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(WGP), "exec"), namespace)
    return namespace


def _load_metadata_helpers():
    names = {
        "_redact_h3_source_prefix_path_for_metadata",
        "_multi_clip_image_start_metadata",
        "_verify_h3_source_prefix_output",
    }
    tree = ast.parse(WGP.read_text(encoding="utf-8"), filename=str(WGP))
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {"copy": copy}
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(WGP), "exec"), namespace)
    return namespace


class H3SourcePrefixMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.namespace = _load_metadata_helpers()

    def test_source_path_is_redacted_without_mutating_runtime_descriptor(self):
        runtime_info = {
            "source_prefix": {
                "path": "/private/sealed/source.mp4",
                "sha256": "a" * 64,
                "retained_frames": 48,
            },
            "group_id": "group-1",
        }
        configs = {"multi_clip_info": runtime_info, "prompt": "keep this"}

        result = self.namespace[
            "_redact_h3_source_prefix_path_for_metadata"
        ](configs)

        self.assertEqual(
            result["multi_clip_info"]["source_prefix"]["path"], "[redacted]",
        )
        self.assertEqual(
            result["multi_clip_info"]["source_prefix"]["sha256"], "a" * 64,
        )
        self.assertEqual(runtime_info["source_prefix"]["path"], "/private/sealed/source.mp4")
        self.assertEqual(result["prompt"], "keep this")

        plain_info = {"group_id": "without-prefix"}
        plain_configs = {"multi_clip_info": plain_info}
        self.assertIs(
            self.namespace["_redact_h3_source_prefix_path_for_metadata"](
                plain_configs,
            )["multi_clip_info"],
            plain_info,
        )

    def test_first_source_derived_image_start_is_omitted_only_for_prefix_metadata(self):
        group = {
            0: {"image_start": "/private/recovery/source-frame.png"},
            1: {"image_start": "/public/generated/frame.png"},
        }

        with_prefix = self.namespace["_multi_clip_image_start_metadata"](
            group, 2, {"path": "/private/sealed/source.mp4"},
        )
        without_prefix = self.namespace["_multi_clip_image_start_metadata"](
            group, 2,
        )

        self.assertEqual(with_prefix, [None, "/public/generated/frame.png"])
        self.assertEqual(
            without_prefix,
            ["/private/recovery/source-frame.png", "/public/generated/frame.png"],
        )

    def test_metadata_redaction_is_wired_before_video_metadata_writes(self):
        tree = ast.parse(WGP.read_text(encoding="utf-8"), filename=str(WGP))
        generate = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_generate_video_impl"
        )
        redaction_lines = [
            node.lineno for node in ast.walk(generate)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_redact_h3_source_prefix_path_for_metadata"
        ]
        metadata_write_lines = [
            node.lineno for node in ast.walk(generate)
            if isinstance(node, ast.Call)
            and (
                getattr(node.func, "id", None) == "save_video_metadata"
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "dump"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "json"
                )
            )
        ]

        self.assertEqual(len(redaction_lines), 1)
        self.assertTrue(metadata_write_lines)
        self.assertTrue(all(redaction_lines[0] < line for line in metadata_write_lines))

    def test_audio_verifier_rejects_short_tail_but_allows_bounded_aac_padding(self):
        expected_duration = 74 / 24

        def verify(duration):
            payload = {
                "streams": [
                    {
                        "codec_type": "video", "nb_read_frames": "74",
                        "r_frame_rate": "24/1", "width": 64, "height": 48,
                    },
                    {
                        "codec_type": "audio", "sample_rate": "32000",
                        "duration": str(duration),
                    },
                ],
            }
            with mock.patch(
                "subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(payload),
                ),
            ):
                return self.namespace["_verify_h3_source_prefix_output"](
                    "unused.mp4", 74, 24, 64, 48, "ffprobe",
                )

        self.assertTrue(verify(expected_duration + 21 / 32000))
        self.assertFalse(verify(expected_duration - 939 / 32000))


@unittest.skipUnless(FFMPEG and FFPROBE, "CPU ffmpeg/ffprobe required")
class H3SourcePrefixConcatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.namespace = _load_concat()
        cls.concatenate = staticmethod(cls.namespace["concatenate_multi_clip_videos"])
        cls.extract_last_frame = staticmethod(
            cls.namespace["extract_h3_source_prefix_last_frame"]
        )
        cls.error_type = cls.namespace["PostDecodeStageError"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="h3-source-prefix-test-")
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _make_clip(self, path, *, width, height, fps, frames, audio_hz=None, audio_rate=32000):
        command = [FFMPEG, "-v", "error", "-nostdin", "-y"]
        command += [
            "-f", "lavfi", "-i",
            f"color=c=blue:s={width}x{height}:r={fps}:d={frames / fps}",
        ]
        if audio_hz is not None:
            command += [
                "-f", "lavfi", "-i",
                f"sine=frequency={audio_hz}:sample_rate={audio_rate}:duration={frames / fps}",
            ]
        command += ["-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio_hz is not None:
            command += ["-c:a", "aac", "-ar", str(audio_rate), "-ac", "2", "-shortest"]
        command.append(str(path))
        subprocess.run(command, check=True, capture_output=True, timeout=60)
        return path

    def _make_timestamp_clip(self, path, *, width, height, fps, frames):
        raw = bytearray()
        for index in range(frames):
            timestamp = min(index, 52)
            color = (10 + timestamp * 4, 5 + timestamp * 3, timestamp * 2)
            raw.extend(bytes(color) * (width * height))
        subprocess.run(
            [
                FFMPEG, "-v", "error", "-nostdin", "-y",
                "-f", "rawvideo", "-pixel_format", "rgb24",
                "-video_size", f"{width}x{height}", "-framerate", str(fps),
                "-i", "-", "-frames:v", str(frames), "-c:v", "libx264",
                "-crf", "0", "-pix_fmt", "yuv444p", str(path),
            ],
            input=raw, check=True, capture_output=True, timeout=60,
        )
        return path

    def _make_vfr_clip(self, path, *, audio_duration):
        subprocess.run(
            [
                FFMPEG, "-v", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i",
                "testsrc2=size=64x48:rate=25:duration=0.4",
                "-f", "lavfi", "-i",
                "testsrc2=size=64x48:rate=20:duration=0.5",
                "-f", "lavfi", "-i",
                f"sine=frequency=440:sample_rate=48000:duration={audio_duration}",
                "-filter_complex",
                "[0:v]setpts=PTS-STARTPTS[v0];"
                "[1:v]setpts=PTS-STARTPTS[v1];"
                "[v0][v1]concat=n=2:v=1:a=0[outv]",
                "-map", "[outv]", "-map", "2:a:0", "-fps_mode", "vfr",
                "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(path),
            ],
            check=True, capture_output=True, timeout=60,
        )
        return path

    def _descriptor(self, source, *, native_frames, retained_frames=24):
        payload = source.read_bytes()
        return {
            "version": 1,
            "input_field": "video_source:0",
            "path": str(source.resolve()),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "source_native_frames": native_frames,
            "retained_frames": retained_frames,
            "output_fps": 24,
            "fit": "contain",
            "conditioning": "last_frame",
            "audio_policy": "preserve_source_then_generated",
        }

    def _probe(self, path):
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        return json.loads(result.stdout)["streams"]

    def _decode_mono(self, path, *, start, duration, rate=32000):
        result = subprocess.run(
            [
                FFMPEG, "-v", "error", "-nostdin", "-i", str(path),
                "-ss", f"{start:.6f}", "-t", f"{duration:.6f}",
                "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le",
                "-ar", str(rate), "-ac", "1", "-",
            ],
            check=True, capture_output=True, timeout=60,
        )
        return [item[0] for item in struct.iter_unpack("<f", result.stdout)]

    def _decode_png_rgb(self, path):
        result = subprocess.run(
            [
                FFMPEG, "-v", "error", "-nostdin", "-i", str(path),
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            check=True, capture_output=True, timeout=60,
        )
        return result.stdout

    @staticmethod
    def _tone_power(samples, rate, frequency):
        omega = 2 * math.pi * frequency / rate
        coefficient = 2 * math.cos(omega)
        first = second = 0.0
        for sample in samples:
            current = sample + coefficient * first - second
            second, first = first, current
        return first * first + second * second - coefficient * first * second

    def _assert_audio_duration(self, path, expected_seconds):
        audio = next(stream for stream in self._probe(path) if stream.get("codec_type") == "audio")
        rate = int(audio["sample_rate"])
        duration = float(audio["duration"])
        delta = duration - expected_seconds
        self.assertGreaterEqual(delta, -(1 / rate) - 1e-6)
        self.assertLessEqual(delta, 1024 / rate + 1e-6)

    def test_audible_source_prefix_one_generated_clip_and_no_external_override(self):
        source = self._make_clip(
            self.directory / "source.mp4", width=32, height=24, fps=12,
            frames=12, audio_hz=440, audio_rate=48000,
        )
        generated = self._make_clip(
            self.directory / "generated.mp4", width=64, height=48, fps=24,
            frames=24, audio_hz=880,
        )
        external = self.directory / "external.wav"
        subprocess.run(
            [FFMPEG, "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
             "sine=frequency=1320:sample_rate=32000:duration=2", str(external)],
            check=True, capture_output=True, timeout=60,
        )
        output = self.directory / "joined-one.mp4"

        self.assertTrue(self.concatenate(
            [str(generated)], str(output), str(external),
            source_prefix=self._descriptor(source, native_frames=12),
        ))

        streams = self._probe(output)
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        self.assertEqual(int(video["nb_read_frames"]), 48)
        self.assertEqual((int(video["width"]), int(video["height"])), (64, 48))
        self.assertEqual(video["r_frame_rate"], "24/1")
        self._assert_audio_duration(output, 2.0)
        source_tone = self._decode_mono(output, start=0.2, duration=0.4)
        generated_tone = self._decode_mono(output, start=1.2, duration=0.4)
        self.assertGreater(
            self._tone_power(source_tone, 32000, 440),
            self._tone_power(source_tone, 32000, 1320) * 5,
        )
        self.assertGreater(
            self._tone_power(generated_tone, 32000, 880),
            self._tone_power(generated_tone, 32000, 1320) * 5,
        )

    def test_silent_source_prefix_two_generated_clips_keep_exact_presentation(self):
        source = self._make_clip(
            self.directory / "silent-source.mp4", width=40, height=30, fps=12,
            frames=12,
        )
        first = self._make_clip(
            self.directory / "generated-a.mp4", width=64, height=48, fps=24,
            frames=12, audio_hz=880,
        )
        second = self._make_clip(
            self.directory / "generated-b.mp4", width=64, height=48, fps=24,
            frames=12, audio_hz=660,
        )
        output = self.directory / "joined-two.mp4"

        self.assertTrue(self.concatenate(
            [str(first), str(second)], str(output),
            source_prefix=self._descriptor(source, native_frames=12),
        ))

        streams = self._probe(output)
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        self.assertEqual(int(video["nb_read_frames"]), 48)
        self.assertEqual((int(video["width"]), int(video["height"])), (64, 48))
        self._assert_audio_duration(output, 2.0)
        silence = self._decode_mono(output, start=0.2, duration=0.4)
        self.assertLess(math.sqrt(sum(sample * sample for sample in silence) / len(silence)), 0.01)
        first_tone = self._decode_mono(output, start=1.1, duration=0.3)
        second_tone = self._decode_mono(output, start=1.6, duration=0.3)
        self.assertGreater(self._tone_power(first_tone, 32000, 880), self._tone_power(first_tone, 32000, 660) * 5)
        self.assertGreater(self._tone_power(second_tone, 32000, 660), self._tone_power(second_tone, 32000, 880) * 5)

    def test_native_frame_cap_limits_a_longer_sealed_source(self):
        source = self._make_clip(
            self.directory / "long-source.mp4", width=32, height=24, fps=25,
            frames=125, audio_hz=440, audio_rate=48000,
        )
        generated = self._make_clip(
            self.directory / "generated.mp4", width=64, height=48, fps=24,
            frames=24, audio_hz=880,
        )
        output = self.directory / "joined-capped.mp4"
        descriptor = self._descriptor(
            source, native_frames=125, retained_frames=50,
        )
        captured_filters = []
        real_popen = subprocess.Popen

        def record_ffmpeg(command, *args, **kwargs):
            if command and os.path.basename(command[0]).startswith("ffmpeg"):
                if "-filter_complex" in command:
                    captured_filters.append(command[command.index("-filter_complex") + 1])
            return real_popen(command, *args, **kwargs)

        with mock.patch("subprocess.Popen", side_effect=record_ffmpeg):
            self.assertTrue(self.concatenate(
                [str(generated)], str(output), source_prefix=descriptor,
            ))

        streams = self._probe(output)
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        self.assertEqual(int(video["nb_read_frames"]), 74)
        self._assert_audio_duration(output, 74 / 24)
        self.assertIn("trim=end_frame=125", captured_filters[0])
        self.assertIn("atrim=duration=2.083333333", captured_filters[0])
        prefix_tone = self._decode_mono(output, start=1.2, duration=0.4)
        generated_tone = self._decode_mono(output, start=2.2, duration=0.4)
        self.assertGreater(self._tone_power(prefix_tone, 32000, 440), self._tone_power(prefix_tone, 32000, 880) * 5)
        self.assertGreater(self._tone_power(generated_tone, 32000, 880), self._tone_power(generated_tone, 32000, 440) * 5)

    def test_timestamped_last_frame_matches_concat_prefix_before_spatial_scale(self):
        native_frames = 125
        retained_frames = 50
        output_fps = 24
        source = self._make_timestamp_clip(
            self.directory / "timestamp-source.mp4", width=64, height=48,
            fps=25, frames=125,
        )
        self.assertEqual(self.namespace["get_exact_video_frame_count"](str(source)), 125)
        generated = self._make_clip(
            self.directory / "generated.mp4", width=64, height=48, fps=24,
            frames=24, audio_hz=880,
        )
        output = self.directory / "timestamp-joined.mp4"
        captured_filters = []
        real_popen = subprocess.Popen

        def capture_concat_filter(command, *args, **kwargs):
            if command and os.path.basename(command[0]).startswith("ffmpeg"):
                if "-filter_complex" in command:
                    captured_filters.append(
                        command[command.index("-filter_complex") + 1]
                    )
            return real_popen(command, *args, **kwargs)

        with mock.patch("subprocess.Popen", side_effect=capture_concat_filter):
            self.assertTrue(self.concatenate(
                [str(generated)], str(output),
                source_prefix=self._descriptor(
                    source, native_frames=native_frames,
                    retained_frames=retained_frames,
                ),
            ))

        still = self.directory / "last-prefix-frame.png"
        self.assertEqual(
            self.extract_last_frame(
                str(source), str(still),
                source_native_frames=native_frames,
                retained_frames=retained_frames,
                output_fps=output_fps,
            ),
            str(still),
        )
        before, after = self.namespace[
            "_h3_source_prefix_temporal_filter_parts"
        ](native_frames, retained_frames, output_fps)
        concat_filter = captured_filters[0]
        self.assertIn(f"[0:v:0]{before},", concat_filter)
        self.assertIn(f",{after}[source_prefix_video]", concat_filter)

        reference = self.directory / "reference-last-prefix-frame.png"
        reference_filter = (
            f"{before},{after},"
            f"trim=start_frame={retained_frames - 1}:end_frame={retained_frames},"
            "setpts=PTS-STARTPTS"
        )
        subprocess.run(
            [
                FFMPEG, "-v", "error", "-nostdin", "-y", "-i", str(source),
                "-map", "0:v:0", "-vf", reference_filter, "-vsync", "0",
                "-frames:v", "1", "-update", "1", "-c:v", "png",
                str(reference),
            ],
            check=True, capture_output=True, timeout=60,
        )
        self.assertEqual(self._decode_png_rgb(still), self._decode_png_rgb(reference))
        still_pixels = self._decode_png_rgb(still)
        red_mean = sum(still_pixels[0::3]) / (64 * 48)
        green_mean = sum(still_pixels[1::3]) / (64 * 48)
        blue_mean = sum(still_pixels[2::3]) / (64 * 48)
        self.assertGreater(red_mean, 150)
        self.assertGreater(green_mean, 100)
        self.assertGreater(blue_mean, 50)

        joined_last_prefix_frame = self.directory / "joined-last-prefix-frame.png"
        subprocess.run(
            [
                FFMPEG, "-v", "error", "-nostdin", "-y", "-i", str(output),
                "-map", "0:v:0",
                "-vf", f"trim=start_frame={retained_frames - 1}:end_frame={retained_frames}",
                "-vsync", "0", "-frames:v", "1", "-update", "1",
                "-c:v", "png", str(joined_last_prefix_frame),
            ],
            check=True, capture_output=True, timeout=60,
        )
        joined_pixels = self._decode_png_rgb(joined_last_prefix_frame)
        mean_absolute_difference = sum(
            abs(left - right)
            for left, right in zip(still_pixels, joined_pixels)
        ) / len(still_pixels)
        self.assertLessEqual(mean_absolute_difference, 18.0)

    def test_vfr_source_audio_uses_average_rate_and_keeps_prefix_tail_audible(self):
        source = self._make_vfr_clip(
            self.directory / "vfr-source.mp4", audio_duration=2.12,
        )
        self.assertEqual(self.namespace["get_exact_video_frame_count"](str(source)), 20)
        generated = self._make_clip(
            self.directory / "generated.mp4", width=64, height=48, fps=24,
            frames=24, audio_hz=880,
        )
        output = self.directory / "vfr-joined.mp4"
        source_video = next(
            stream for stream in self._probe(source)
            if stream.get("codec_type") == "video"
        )
        self.assertEqual(source_video["r_frame_rate"], "100/1")
        self.assertEqual(source_video["avg_frame_rate"], "200/9")
        descriptor = self._descriptor(source, native_frames=20, retained_frames=50)
        info = self.namespace["_h3_source_prefix_media_info"](
            descriptor, [str(generated)], FFPROBE,
        )
        self.assertAlmostEqual(info["source_fps"], 200 / 9)

        self.assertTrue(self.concatenate(
            [str(generated)], str(output), source_prefix=descriptor,
        ))
        streams = self._probe(output)
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        self.assertEqual(int(video["nb_read_frames"]), 74)
        self._assert_audio_duration(output, 74 / 24)
        source_tail = self._decode_mono(output, start=2.02, duration=0.04)
        self.assertGreater(
            self._tone_power(source_tail, 32000, 440),
            self._tone_power(source_tail, 32000, 880) * 5,
        )

    def test_cancelled_last_frame_extraction_cleans_only_private_partial(self):
        source = self._make_clip(
            self.directory / "source.mp4", width=32, height=24, fps=12,
            frames=12,
        )
        output = self.directory / "cancelled-frame.png"
        process_started = {"value": False}

        class CancelProcess:
            def __init__(self, command):
                self.returncode = None
                self.timed_out = False
                process_started["value"] = True
                Path(command[-1]).write_bytes(b"partial")

            def communicate(self, timeout=None):
                if not self.timed_out:
                    self.timed_out = True
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                return ("", "")

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

            def poll(self):
                return self.returncode

        def cancel_after_start():
            return process_started["value"]

        with mock.patch("subprocess.Popen", side_effect=lambda command, **kwargs: CancelProcess(command)):
            with self.assertRaises(InterruptedError):
                self.extract_last_frame(
                    str(source), str(output), source_native_frames=12,
                    retained_frames=24, output_fps=24,
                    abort_callback=cancel_after_start,
                )

        self.assertFalse(output.exists())
        self.assertEqual(
            list(self.directory.glob(".h3-source-prefix-frame-*.png")), [],
        )

    def test_snapshot_copies_exact_sealed_bytes_to_staging(self):
        source = self._make_clip(
            self.directory / "source.mp4", width=32, height=24, fps=12,
            frames=12,
        )
        staging = self.directory / "recovery"
        staging.mkdir()
        snapshot = staging / "source-prefix.mp4"
        descriptor = self._descriptor(source, native_frames=12)

        self.assertEqual(
            self.namespace["snapshot_h3_source_prefix"](
                descriptor, str(snapshot),
            ),
            str(snapshot),
        )
        self.assertEqual(snapshot.read_bytes(), source.read_bytes())
        self.assertEqual(
            hashlib.sha256(snapshot.read_bytes()).hexdigest(), descriptor["sha256"],
        )

    def test_snapshot_rejects_same_size_changed_source(self):
        source = self._make_clip(
            self.directory / "source.mp4", width=32, height=24, fps=12,
            frames=12,
        )
        staging = self.directory / "recovery"
        staging.mkdir()
        snapshot = staging / "source-prefix.mp4"
        descriptor = self._descriptor(source, native_frames=12)
        original = source.read_bytes()
        source.write_bytes(bytes([original[0] ^ 1]) + original[1:])

        with self.assertRaises(self.error_type) as caught:
            self.namespace["snapshot_h3_source_prefix"](
                descriptor, str(snapshot),
            )
        self.assertEqual(caught.exception.code, "concat_source_prefix_changed")
        self.assertFalse(snapshot.exists())
        self.assertEqual(list(staging.iterdir()), [])

    def test_cancelled_snapshot_removes_private_partial(self):
        source = self.directory / "large-source.bin"
        source.write_bytes(b"x" * (2 * 1024 * 1024))
        descriptor = {
            "path": str(source.resolve()),
            "size": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        staging = self.directory / "recovery"
        staging.mkdir()
        snapshot = staging / "source-prefix.bin"
        checks = {"count": 0}

        def abort_after_one_chunk():
            checks["count"] += 1
            return checks["count"] > 1

        with self.assertRaises(InterruptedError):
            self.namespace["snapshot_h3_source_prefix"](
                descriptor, str(snapshot), abort_callback=abort_after_one_chunk,
            )

        self.assertFalse(snapshot.exists())
        self.assertEqual(list(staging.iterdir()), [])

    def test_changed_sealed_source_is_rejected_before_concat(self):
        source = self._make_clip(
            self.directory / "source.mp4", width=32, height=24, fps=12,
            frames=12, audio_hz=440,
        )
        generated = self._make_clip(
            self.directory / "generated.mp4", width=64, height=48, fps=24,
            frames=24, audio_hz=880,
        )
        descriptor = self._descriptor(source, native_frames=12)
        source.write_bytes(source.read_bytes() + b"changed")
        output = self.directory / "changed-source.mp4"

        with self.assertRaises(self.error_type) as caught:
            self.concatenate([str(generated)], str(output), source_prefix=descriptor)
        self.assertEqual(caught.exception.code, "concat_source_prefix_changed")
        self.assertFalse(output.exists())

    def test_source_native_frame_count_must_match_full_sealed_source(self):
        source = self._make_clip(
            self.directory / "long-source.mp4", width=32, height=24, fps=25,
            frames=125,
        )
        generated = self._make_clip(
            self.directory / "generated.mp4", width=64, height=48, fps=24,
            frames=24, audio_hz=880,
        )
        output = self.directory / "wrong-native-count.mp4"

        with self.assertRaises(self.error_type) as caught:
            self.concatenate(
                [str(generated)], str(output),
                source_prefix=self._descriptor(
                    source, native_frames=50, retained_frames=50,
                ),
            )

        self.assertEqual(caught.exception.code, "concat_source_prefix_changed")
        self.assertFalse(output.exists())

    def test_generated_clip_without_audio_fails_closed(self):
        source = self._make_clip(
            self.directory / "source.mp4", width=32, height=24, fps=12,
            frames=12,
        )
        generated = self._make_clip(
            self.directory / "silent-generated.mp4", width=64, height=48,
            fps=24, frames=24,
        )
        output = self.directory / "missing-audio.mp4"

        with self.assertRaises(self.error_type) as caught:
            self.concatenate(
                [str(generated)], str(output),
                source_prefix=self._descriptor(source, native_frames=12),
            )
        self.assertEqual(caught.exception.code, "concat_generated_audio_missing")
        self.assertFalse(output.exists())

    def test_cancel_during_concat_removes_partial_and_private_source_copy(self):
        source = self._make_clip(
            self.directory / "source.mp4", width=32, height=24, fps=12,
            frames=12, audio_hz=440,
        )
        generated = self._make_clip(
            self.directory / "generated.mp4", width=64, height=48, fps=24,
            frames=24, audio_hz=880,
        )
        output = self.directory / "cancelled.mp4"
        private_temp_root = self.directory / "private-temp"
        private_temp_root.mkdir(mode=0o700)
        original_tempdir = tempfile.tempdir
        tempfile.tempdir = str(private_temp_root)
        process_started = {"value": False}

        class CancelProcess:
            def __init__(self):
                self.returncode = None
                self.timed_out = False
                process_started["value"] = True

            def communicate(self, timeout=None):
                if not self.timed_out:
                    self.timed_out = True
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                return ("", "")

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

            def poll(self):
                return self.returncode

        def abort():
            return process_started["value"]

        real_popen = subprocess.Popen

        def cancel_only_ffmpeg(command, *args, **kwargs):
            if command and os.path.basename(command[0]).startswith("ffmpeg"):
                return CancelProcess()
            return real_popen(command, *args, **kwargs)

        try:
            with mock.patch("subprocess.Popen", side_effect=cancel_only_ffmpeg):
                result = self.concatenate(
                    [str(generated)], str(output),
                    abort_callback=abort,
                    source_prefix=self._descriptor(source, native_frames=12),
                )
        finally:
            tempfile.tempdir = original_tempdir

        self.assertFalse(result)
        self.assertFalse(output.exists())
        self.assertEqual(list(private_temp_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
