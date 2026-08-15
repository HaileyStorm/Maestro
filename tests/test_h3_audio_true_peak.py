"""Model-free regression coverage for H3 final-container true-peak safety."""

from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.h3_audio_safety import (  # noqa: E402
    H3AudioSafetyError,
    POLICY_VERSION,
    _peak_from_ebur128_lines,
    _verify_stream_contract,
    enforce_true_peak_safety,
    measure_true_peak,
)
from services.queue_recovery_runtime import recovery_unit_id  # noqa: E402


def _write_pcm16(path, audio, sample_rate=32000):
    samples = np.asarray(audio, dtype=np.float64)
    if samples.ndim == 1:
        samples = np.stack([samples, samples], axis=1)
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(pcm.shape[1])
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def _make_media(directory, audio, *, codec="aac", sample_rate=32000):
    wav_path = Path(directory) / f"source-{codec}.wav"
    media_path = Path(directory) / f"output-{codec}.mp4"
    _write_pcm16(wav_path, audio, sample_rate)
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=1",
        "-i", str(wav_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", codec,
    ]
    if codec == "aac":
        command.extend(["-b:a", "192k"])
    command.extend(["-shortest", str(media_path)])
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    return media_path


def _probe(path):
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def _streams(probe):
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    return audio, video


def _decode_audio(path):
    completed = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
            "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le", "-",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return np.frombuffer(completed.stdout, dtype="<f4").reshape(-1, 2)


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg and ffprobe are required",
)
class H3TruePeakRuntimeTests(unittest.TestCase):
    def test_measurement_is_deterministic_and_detects_intersample_peak(self):
        # This independently validated near-Nyquist fixture is -1.94 dBFS at
        # sample instants while FFmpeg ebur128 reconstructs about +1.7 dBTP.
        audio = np.tile([0.8, 0.8, -0.8, -0.8], 8000)
        with tempfile.TemporaryDirectory() as directory:
            media = _make_media(directory, audio, codec="alac")
            first = measure_true_peak(media)
            second = measure_true_peak(media)
        self.assertAlmostEqual(float(np.max(np.abs(audio))), 0.8, places=6)
        self.assertAlmostEqual(20 * math.log10(0.8), -1.9382, places=3)
        self.assertAlmostEqual(first["peak_linear"], 1.212, places=3)
        self.assertAlmostEqual(first["peak_dbtp"], 1.67, places=2)
        self.assertEqual(first, second)
        self.assertEqual(first["oversample_factor"], 4)
        for unsupported in (2, 4.5, 8, True):
            with self.subTest(unsupported=unsupported), self.assertRaises(
                ValueError
            ):
                measure_true_peak(media, oversample_factor=unsupported)

    def test_quiet_and_silent_outputs_are_not_rewritten_or_boosted(self):
        sample_rate = 32000
        time_axis = np.arange(sample_rate) / sample_rate
        cases = {
            "quiet": 0.05 * np.sin(2 * np.pi * 440 * time_axis),
            "silence": np.zeros(sample_rate),
        }
        for label, audio in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                media = _make_media(directory, audio, codec="aac")
                before = hashlib.sha256(media.read_bytes()).hexdigest()
                stats = enforce_true_peak_safety(media)
                after = hashlib.sha256(media.read_bytes()).hexdigest()
                self.assertEqual(before, after)
                self.assertEqual(stats["applied_gain_db"], 0.0)
                self.assertTrue(stats["verified"])
                if label == "silence":
                    self.assertIsNone(stats["measured_pre_dbtp"])

    def test_aac_and_lossless_remux_preserve_contract_and_verify_ceiling(self):
        sample_rate = 32000
        time_axis = np.arange(sample_rate) / sample_rate
        envelope = np.ones(sample_rate)
        envelope[sample_rate // 2:] = 0.4 / 0.99
        stereo = np.stack(
            [
                0.99 * envelope * np.sin(2 * np.pi * 997 * time_axis),
                0.99 * envelope * np.sin(2 * np.pi * 997 * time_axis + 0.3),
            ],
            axis=1,
        )
        for codec in ("aac", "alac"):
            with self.subTest(codec=codec), tempfile.TemporaryDirectory() as directory:
                media = _make_media(directory, stereo, codec=codec)
                source = Path(directory) / f"source-{codec}.wav"
                source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
                before_audio, before_video = _streams(_probe(media))
                decoded_before = _decode_audio(media)
                media.chmod(0o640)
                stats = enforce_true_peak_safety(media)
                after_audio, after_video = _streams(_probe(media))
                measured = measure_true_peak(media)
                decoded_after = _decode_audio(media)

                self.assertLessEqual(measured["peak_dbtp"], -1.0)
                self.assertLess(stats["applied_gain_db"], 0.0)
                self.assertTrue(stats["verified"])
                self.assertEqual(after_audio["codec_name"], codec)
                self.assertEqual(after_audio["sample_rate"], str(sample_rate))
                self.assertEqual(after_audio["channels"], 2)
                self.assertAlmostEqual(
                    float(after_audio["duration"]),
                    float(before_audio["duration"]),
                    places=3,
                )
                self.assertEqual(after_video["codec_name"], before_video["codec_name"])
                self.assertAlmostEqual(
                    float(after_video["duration"]),
                    float(before_video["duration"]),
                    places=3,
                )
                self.assertEqual(
                    hashlib.sha256(source.read_bytes()).hexdigest(),
                    source_digest,
                )
                self.assertEqual(stat.S_IMODE(media.stat().st_mode), 0o640)
                if codec == "alac":
                    # The only signal change is one constant negative gain:
                    # loud and quiet halves retain the same relative dynamics.
                    midpoint = min(len(decoded_before), len(decoded_after)) // 2
                    gain_first = np.sqrt(
                        np.mean(decoded_after[:midpoint] ** 2)
                        / np.mean(decoded_before[:midpoint] ** 2)
                    )
                    gain_second = np.sqrt(
                        np.mean(decoded_after[midpoint:] ** 2)
                        / np.mean(decoded_before[midpoint:] ** 2)
                    )
                    self.assertAlmostEqual(gain_first, gain_second, places=4)

    def test_stats_are_versioned_finite_and_path_free(self):
        sample_rate = 32000
        time_axis = np.arange(sample_rate) / sample_rate
        audio = 0.99 * np.sin(2 * np.pi * 997 * time_axis)
        with tempfile.TemporaryDirectory() as directory:
            media = _make_media(directory, audio, codec="aac")
            stats = enforce_true_peak_safety(media)
        self.assertEqual(stats["policy_version"], POLICY_VERSION)
        self.assertEqual(stats["target_dbtp"], -1.0)
        self.assertEqual(set(stats), {
            "policy_version", "target_dbtp", "measured_pre_dbtp",
            "measured_post_dbtp", "applied_gain_db", "oversample_factor",
            "verified",
        })
        serialized = json.dumps(stats, allow_nan=False)
        self.assertNotIn("/", serialized)
        self.assertNotIn("\\", serialized)
        for key, value in stats.items():
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value), key)

    def test_policy_has_no_compressor_limiter_clipper_or_normalizer(self):
        source = (APP / "services" / "h3_audio_safety.py").read_text(
            encoding="utf-8"
        ).casefold()
        for forbidden in ("acompressor", "alimiter", "loudnorm", "dynaudnorm"):
            self.assertNotIn(forbidden, source)
        self.assertIn('"-filter:a:0"', source)
        self.assertIn('f"volume=', source)
        self.assertIn("ebur128=metadata=1:peak=true", source)

    def test_invalid_media_and_nonfinite_decoded_samples_fail_closed(self):
        with self.assertRaisesRegex(H3AudioSafetyError, "non-finite"):
            _peak_from_ebur128_lines([
                "lavfi.r128.true_peak=nan",
            ])
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.mp4"
            invalid.write_bytes(b"not media")
            with self.assertRaises(H3AudioSafetyError):
                enforce_true_peak_safety(invalid)

    def test_meter_timeout_kills_the_decoder(self):
        class MeterLines:
            def __iter__(self):
                return iter(())

            def close(self):
                pass

        class HungProcess:
            def __init__(self):
                self.stderr = MeterLines()
                self.killed = False

            def wait(self, timeout=None):
                if not self.killed:
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                return -9

            def poll(self):
                return None if not self.killed else -9

            def kill(self):
                self.killed = True

        process = HungProcess()
        probe = ({}, {"sample_rate": "32000", "channels": 2}, {})
        with (
            mock.patch(
                "services.h3_audio_safety._probe_media",
                return_value=probe,
            ),
            mock.patch(
                "services.h3_audio_safety.subprocess.Popen",
                return_value=process,
            ),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            measure_true_peak("bounded.mp4")
        self.assertTrue(process.killed)

    def test_stream_contract_requires_layout_and_all_existing_times(self):
        before_audio = {
            "sample_rate": "32000", "channels": 2,
            "channel_layout": "stereo", "duration": "1.000000",
            "start_time": "0.125000",
        }
        before_video = {
            "codec_name": "h264", "duration": "1.000000",
            "start_time": "0.000000",
        }
        _verify_stream_contract(
            before_audio, before_video,
            dict(before_audio), dict(before_video),
        )
        for missing_from, key in (
            ("audio", "channel_layout"),
            ("audio", "duration"),
            ("audio", "start_time"),
            ("video", "duration"),
            ("video", "start_time"),
        ):
            after_audio = dict(before_audio)
            after_video = dict(before_video)
            (after_audio if missing_from == "audio" else after_video).pop(key)
            with self.subTest(missing_from=missing_from, key=key), self.assertRaises(
                H3AudioSafetyError
            ):
                _verify_stream_contract(
                    before_audio, before_video, after_audio, after_video,
                )


class H3TruePeakWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (APP / "wgp.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _function_source(self, name):
        node = next(
            item for item in self.tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )
        return ast.get_source_segment(self.source, node)

    def test_helper_gates_resolved_h3_families_and_maps_failure(self):
        helper = self._function_source("enforce_h3_final_audio_safety")
        self.assertIn('{"minimax_h3", "minimax_h3_ref2va"}', helper)
        self.assertIn('stage="audio_mux"', helper)
        self.assertIn('code="audio_true_peak_failed"', helper)
        self.assertIn('"Verifying H3 Audio Safety"', helper)

    def test_mux_verifies_before_success_and_cleanup(self):
        generate = self._function_source("generate_video")
        combine = generate.index("combine_and_concatenate_video_with_audio_tracks(")
        verify = generate.index("enforce_h3_final_audio_safety(", combine)
        success = generate.index("h3_mux_succeeded = True", verify)
        self.assertLess(combine, verify)
        self.assertLess(verify, success)

        resume = self._function_source("resume_h3_source_audio_premux")
        resume_verify = resume.index("safety_stats = safety(")
        cleanup = resume.index("for temporary in (premux_video, recovered_audio)")
        self.assertLess(resume_verify, cleanup)

    def test_metadata_status_retake_and_multiclip_paths_are_covered(self):
        generate = self._function_source("generate_video")
        self.assertIn('configs["h3_audio_true_peak"]', generate)
        self.assertIn('gen["h3_audio_true_peak"]', generate)
        self.assertIn("retake_safety_stats = enforce_h3_final_audio_safety", generate)
        self.assertIn("concat_safety_stats = enforce_h3_final_audio_safety", generate)
        self.assertIn('concat_configs["h3_audio_true_peak"]', generate)

    def test_components_defer_safety_without_skipping_retake_stitching(self):
        generate = self._function_source("generate_video")
        self.assertIn(
            "h3_audio_safety_deferred_to_multiclip_final = (", generate,
        )
        self.assertIn(
            "and _retake_stitch_info is None", generate,
        )
        stitch = generate.index("if retake_was_requested:")
        safety = generate.index("retake_safety_stats =", stitch)
        guard = generate.rfind(
            "not h3_audio_safety_deferred_to_multiclip_final",
            stitch,
            safety,
        )
        self.assertGreater(guard, stitch)
        self.assertIn(
            "h3_mux_succeeded = _retake_stitch_info is None",
            generate,
        )
        retained_cleanup = generate.index(
            "if retake_was_requested and h3_keep_premux:", safety,
        )
        self.assertGreater(retained_cleanup, safety)
        self.assertIn(
            'gen["artifact_list"] = [',
            generate[safety:retained_cleanup],
        )

    def test_failed_recovery_safety_removes_final_but_keeps_premux(self):
        resume_node = next(
            item for item in self.tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "resume_h3_source_audio_premux"
        )

        class StageError(RuntimeError):
            def __init__(self, message, *, stage, code):
                super().__init__(message)
                self.stage, self.code = stage, code

        def remove_final(path):
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass

        namespace = {
            "os": __import__("os"),
            "PostDecodeStageError": StageError,
            "combine_and_concatenate_video_with_audio_tracks": None,
            "enforce_h3_final_audio_safety": None,
            "remove_failed_h3_final_output": remove_final,
        }
        module = ast.Module(body=[resume_node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(APP / "wgp.py"), "exec"), namespace)
        resume = namespace["resume_h3_source_audio_premux"]

        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            video = staging / "unit-job-premux-video.mp4"
            audio = staging / "unit-job-premux-audio.wav"
            output = staging / "unit-job-final.mp4"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")

            def combine(output_path, *_args, **_kwargs):
                Path(output_path).write_bytes(b"unverified")

            def fail_safety(*_args):
                raise StageError(
                    "injected", stage="audio_mux",
                    code="audio_true_peak_failed",
                )

            with self.assertRaises(StageError):
                resume(
                    premux_video_path=video,
                    premux_audio_path=audio,
                    final_audio_path=None,
                    output_path=output,
                    recovery_staging_dir=staging,
                    combine_fn=combine,
                    safety_fn=fail_safety,
                )
            self.assertFalse(output.exists())
            self.assertTrue(video.exists())
            self.assertTrue(audio.exists())


class H3TruePeakLaunchFinalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (APP / "launch.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _function_source(self, name):
        node = next(
            item for item in self.tree.body
            if isinstance(item, ast.FunctionDef) and item.name == name
        )
        return ast.get_source_segment(self.source, node)

    def _isolated_rejoin_helpers(self, fake_wgp):
        nodes = [
            next(
                item for item in self.tree.body
                if isinstance(item, ast.FunctionDef) and item.name == name
            )
            for name in (
                "_rejoin_source_model_identity",
                "_enforce_rejoined_h3_final_audio",
            )
        ]

        class FakeHTTPException(RuntimeError):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        namespace = {
            "HTTPException": FakeHTTPException,
            "wgp": fake_wgp,
        }
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(APP / "launch.py"), "exec"), namespace)
        return namespace, FakeHTTPException

    def test_deferred_helper_gates_family_and_persists_status(self):
        helper = self._function_source("_enforce_deferred_h3_final_audio")
        self.assertIn('wgp.get_base_model_type(model_type)', helper)
        self.assertIn('{"minimax_h3", "minimax_h3_ref2va"}', helper)
        self.assertIn('code="audio_true_peak_failed"', helper)
        self.assertNotIn("h3_audio_true_peak=public_stats", helper)

    def test_all_shot_finalizers_verify_before_sidecar_and_completion(self):
        for name, sidecar_call in (
            ("_run_recast_shot_generation", "_write_recast_shot_aware_sidecar("),
            ("_run_repaint_shot_generation", "_write_repaint_shot_aware_sidecar("),
            ("_run_outpaint_shot_generation", "_write_outpaint_shot_aware_sidecar("),
        ):
            with self.subTest(name=name):
                source = self._function_source(name)
                concat = source.index("wgp.concatenate_multi_clip_videos(")
                safety = source.index("_enforce_deferred_h3_final_audio(", concat)
                sidecar = source.index(sidecar_call, safety)
                completed = source.index('"completed"', sidecar)
                self.assertLess(concat, safety)
                self.assertLess(safety, sidecar)
                self.assertLess(sidecar, completed)

    def test_recovery_concat_verifies_staging_before_atomic_promotion(self):
        source = self._function_source(
            "_run_generation_with_sample_coordination"
        ) if any(
            isinstance(item, ast.FunctionDef)
            and item.name == "_run_generation_with_sample_coordination"
            for item in self.tree.body
        ) else self.source
        callback = source.index("def concatenate(component_paths, staging_path):")
        safety = source.index("_enforce_deferred_h3_final_audio(", callback)
        promotion = source.index("replay_concat_to_stable_output(", safety)
        self.assertLess(callback, safety)
        self.assertLess(safety, promotion)

    def test_recovery_attestation_is_outside_unit_identity_and_required(self):
        checkpoint = self._function_source("_queue_recovery_checkpoint_unit")
        identity = checkpoint.index("unit_id = recovery_unit_id(")
        attestation = checkpoint.index('unit["attestation"] =')
        self.assertLess(identity, attestation)
        matcher = self._function_source("_queue_recovery_unit_matches")
        self.assertIn('unit.get("kind") == "h3_concat"', matcher)
        self.assertIn('true_peak.get("policy_version") != POLICY_VERSION', matcher)
        self.assertIn('true_peak.get("verified") is not True', matcher)

        policy = {
            "policy_version": POLICY_VERSION,
            "target_dbtp": -1.0,
        }
        settings = {
            "component_hashes": ["a", "b"],
            "h3_audio_true_peak_policy": policy,
        }
        first = recovery_unit_id(
            "job", "h3_concat", variant=0, dependencies=[],
            settings=settings,
        )
        second = recovery_unit_id(
            "job", "h3_concat", variant=0, dependencies=[],
            settings=dict(settings),
        )
        self.assertEqual(first, second)
        tampered = dict(settings)
        tampered["h3_audio_true_peak_policy"] = {
            **policy, "target_dbtp": -0.5,
        }
        self.assertNotEqual(
            first,
            recovery_unit_id(
                "job", "h3_concat", variant=0, dependencies=[],
                settings=tampered,
            ),
        )

    def test_normal_concat_publishes_stats_only_after_checkpoint(self):
        source = self._function_source("_run_generation")
        concat_start = source.index("if concat_names and not task_error:")
        segment_start = source.rfind(
            "if segment_names and sealed_segment_unit is None:",
            0,
            concat_start,
        )
        self.assertNotIn(
            "h3_audio_true_peak=dict(",
            source[segment_start:concat_start],
        )
        concat_source = source[concat_start:]
        checkpoint = concat_source.index('kind="h3_concat"')
        published = concat_source.index(
            "h3_audio_true_peak=dict(", checkpoint,
        )
        self.assertLess(checkpoint, published)

    def test_rejoin_model_identity_is_consistent_and_h3_fail_closed(self):
        class FakeWGP:
            @staticmethod
            def get_base_model_type(model_type):
                return {
                    "minimax_h3_w4a8_fl2va": "minimax_h3",
                    "minimax_h3_pinkcherry_fl2va": "minimax_h3",
                    "wan_2_2": "wan_2_2",
                }.get(model_type, model_type)

        namespace, HTTPError = self._isolated_rejoin_helpers(FakeWGP())
        resolve = namespace["_rejoin_source_model_identity"]
        h3 = resolve([
            {"params": {"model_type": "minimax_h3_w4a8_fl2va"}},
            {"params": {"model_type": "minimax_h3_pinkcherry_fl2va"}},
        ])
        self.assertEqual(h3["base_model_type"], "minimax_h3")
        self.assertEqual(h3["model_type"], "minimax_h3")
        self.assertTrue(h3["requires_h3_audio_safety"])

        with self.assertRaises(HTTPError) as mixed:
            resolve([
                {"params": {"model_type": "minimax_h3_w4a8_fl2va"}},
                {"params": {"model_type": "wan_2_2"}},
            ])
        self.assertEqual(mixed.exception.status_code, 409)
        with self.assertRaises(HTTPError):
            resolve([
                {"params": {"model_type": "minimax_h3_w4a8_fl2va"}},
                {"params": {}},
            ])
        with self.assertRaises(HTTPError):
            resolve([{
                "model_type": "wan_2_2",
                "params": {"model_type": "minimax_h3_w4a8_fl2va"},
            }])
        self.assertIsNone(resolve([{"params": {}}, {"params": {}}]))
        ordinary = resolve([
            {"params": {"model_type": "wan_2_2"}},
            {"params": {"model_type": "wan_2_2"}},
        ])
        self.assertFalse(ordinary["requires_h3_audio_safety"])

    def test_rejoin_safety_cleans_failed_h3_and_skips_non_h3(self):
        class FakeWGP:
            calls = 0
            should_fail = False

            @staticmethod
            def get_base_model_type(model_type):
                return model_type

            @classmethod
            def enforce_h3_final_audio_safety(cls, path, model_type):
                cls.calls += 1
                if cls.should_fail:
                    raise RuntimeError("injected true-peak failure")
                return {
                    "policy_version": POLICY_VERSION,
                    "target_dbtp": -1.0,
                    "measured_pre_dbtp": -0.2,
                    "measured_post_dbtp": -1.8,
                    "applied_gain_db": -1.6,
                    "oversample_factor": 4,
                    "verified": True,
                }

            @staticmethod
            def remove_failed_h3_final_output(path):
                Path(path).unlink(missing_ok=True)

        namespace, HTTPError = self._isolated_rejoin_helpers(FakeWGP)
        enforce = namespace["_enforce_rejoined_h3_final_audio"]
        h3 = {
            "base_model_type": "minimax_h3",
            "requires_h3_audio_safety": True,
        }
        ordinary = {
            "base_model_type": "wan_2_2",
            "requires_h3_audio_safety": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            joined = Path(directory) / "joined.mp4"
            joined.write_bytes(b"verified candidate")
            stats = enforce(str(joined), h3)
            self.assertTrue(stats["verified"])
            self.assertTrue(joined.exists())

            calls = FakeWGP.calls
            self.assertIsNone(enforce(str(joined), ordinary))
            self.assertEqual(FakeWGP.calls, calls)
            self.assertTrue(joined.exists())

            FakeWGP.should_fail = True
            with self.assertRaises(HTTPError) as failed:
                enforce(str(joined), h3)
            self.assertEqual(failed.exception.status_code, 500)
            self.assertFalse(joined.exists())

    def test_rejoin_route_verifies_before_sidecar_and_keeps_attestation(self):
        source = self._function_source("rejoin_clips")
        identity = source.index("_rejoin_source_model_identity(")
        concat = source.index("concatenate_multi_clip_videos(", identity)
        safety = source.index("_enforce_rejoined_h3_final_audio(", concat)
        promotion = source.index(
            "os.replace(concat_staging_path, concat_path)", safety,
        )
        sidecar = source.index("joined_sidecar =", promotion)
        publish = source.index("os.replace(joined_meta_temp", sidecar)
        self.assertLess(identity, concat)
        self.assertLess(concat, safety)
        self.assertLess(safety, promotion)
        self.assertLess(promotion, sidecar)
        self.assertLess(sidecar, publish)
        self.assertIn('joined_sidecar["model_type"]', source)
        self.assertIn('joined_sidecar["h3_audio_true_peak"]', source)
        post_promotion = source[promotion:]
        policy = post_promotion.index("_inherit_media_access_policy(")
        cleanup = post_promotion.index(
            "for path in (joined_meta_temp, joined_meta_path, concat_path):",
            policy,
        )
        self.assertLess(policy, cleanup)


class H3TruePeakDirectorRejoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = APP / "services" / "director_pipeline.py"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _isolated_rejoin(self, state, pipeline_file, fake_wgp, updates):
        names = {
            "_director_rejoin_model_identity",
            "_director_rejoin_h3_adoption_verified",
            "_enforce_director_rejoin_h3_final_audio",
            "_rejoin_clips_impl",
        }
        nodes = [
            item for item in self.tree.body
            if isinstance(item, ast.FunctionDef) and item.name in names
        ]
        namespace = {
            "_wgp": fake_wgp,
            "hashlib": hashlib,
            "json": json,
            "os": __import__("os"),
            "time": __import__("time"),
            "uuid": __import__("uuid"),
            "load_pipeline_state": lambda _out, _pid: state,
            "_find_pipeline_file": lambda _out, _pid: str(pipeline_file),
            "_invalid_saved_media_numbers": lambda *_args: [],
            "shot_images_required": lambda _policy: False,
            "_saved_pipeline_shot_image_policy": lambda _state: "none",
            "_audio_timeline_start": lambda _clips: 0.0,
            "_update_saved_pipeline": (
                lambda out_dir, pid, update: updates.append((out_dir, pid))
            ),
        }
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.path), "exec"), namespace)
        return namespace

    @staticmethod
    def _state(model_type):
        return {
            "workspace": "default",
            "video_model": model_type,
            "_params_snapshot": {"video_model": model_type},
            "clips": [
                {"video_filename": "clip-0.mp4", "video_stale": False},
                {"video_filename": "clip-1.mp4", "video_stale": False},
            ],
            "recovery": {"inputs": []},
            "output_files": [],
        }

    def test_saved_h3_rejoin_is_verified_and_old_adoption_replays(self):
        class FakeWGP:
            concat_calls = 0
            safety_calls = 0

            @staticmethod
            def get_base_model_type(model_type):
                if model_type in {
                    "minimax_h3_w4a8_fl2va",
                    "minimax_h3_pinkcherry_fl2va",
                }:
                    return "minimax_h3"
                return model_type

            @classmethod
            def concatenate_multi_clip_videos(
                cls, _clips, output, _audio, **_kwargs,
            ):
                cls.concat_calls += 1
                Path(output).write_bytes(b"joined h3 media")
                return True

            @classmethod
            def enforce_h3_final_audio_safety(cls, _path, base_model_type):
                cls.safety_calls += 1
                self.assertEqual(base_model_type, "minimax_h3")
                return {
                    "policy_version": POLICY_VERSION,
                    "target_dbtp": -1.0,
                    "measured_pre_dbtp": 0.2,
                    "measured_post_dbtp": -1.8,
                    "applied_gain_db": -2.0,
                    "oversample_factor": 4,
                    "verified": True,
                }

            @staticmethod
            def remove_failed_h3_final_output(path):
                Path(path).unlink(missing_ok=True)

        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            (out_dir / "clip-0.mp4").write_bytes(b"clip zero")
            (out_dir / "clip-1.mp4").write_bytes(b"clip one")
            state = self._state("minimax_h3_w4a8_fl2va")
            # A variant mirror is compatible because both resolve to Base H3.
            state["_params_snapshot"]["video_model"] = (
                "minimax_h3_pinkcherry_fl2va"
            )
            updates = []
            namespace = self._isolated_rejoin(
                state, out_dir / "pipeline.json", FakeWGP, updates,
            )
            rejoin = namespace["_rejoin_clips_impl"]

            first = rejoin(str(out_dir), "h3")
            self.assertNotIn("adopted", first)
            self.assertEqual(FakeWGP.concat_calls, 1)
            self.assertEqual(FakeWGP.safety_calls, 1)
            final_path = out_dir / first["filename"]
            sidecar_path = final_path.with_suffix(".meta.json")
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["model_type"], "minimax_h3")
            self.assertEqual(
                sidecar["params"]["source_model_types"],
                [
                    "minimax_h3_pinkcherry_fl2va",
                    "minimax_h3_w4a8_fl2va",
                ],
            )
            self.assertTrue(sidecar["h3_audio_true_peak"]["verified"])

            adopted = rejoin(str(out_dir), "h3")
            self.assertTrue(adopted["adopted"])
            self.assertEqual(FakeWGP.concat_calls, 1)
            self.assertEqual(FakeWGP.safety_calls, 1)

            # A legacy/unverified sidecar must be removed and regenerated.
            sidecar.pop("h3_audio_true_peak")
            sidecar["params"].pop("h3_audio_true_peak")
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
            replayed = rejoin(str(out_dir), "h3")
            self.assertNotIn("adopted", replayed)
            self.assertEqual(FakeWGP.concat_calls, 2)
            self.assertEqual(FakeWGP.safety_calls, 2)

    def test_saved_rejoin_non_h3_noop_and_h3_failure_cleans_staging(self):
        class FakeWGP:
            fail = False
            safety_calls = 0

            @staticmethod
            def get_base_model_type(model_type):
                return model_type

            @staticmethod
            def concatenate_multi_clip_videos(
                _clips, output, _audio, **_kwargs,
            ):
                Path(output).write_bytes(b"candidate")
                return True

            @classmethod
            def enforce_h3_final_audio_safety(cls, _path, _base):
                cls.safety_calls += 1
                if cls.fail:
                    raise RuntimeError("injected safety failure")
                return {"verified": True}

            @staticmethod
            def remove_failed_h3_final_output(path):
                Path(path).unlink(missing_ok=True)

        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            (out_dir / "clip-0.mp4").write_bytes(b"clip zero")
            (out_dir / "clip-1.mp4").write_bytes(b"clip one")
            updates = []

            ordinary = self._state("wan_2_2")
            namespace = self._isolated_rejoin(
                ordinary, out_dir / "pipeline.json", FakeWGP, updates,
            )
            result = namespace["_rejoin_clips_impl"](
                str(out_dir), "ordinary",
            )
            self.assertTrue((out_dir / result["filename"]).is_file())
            self.assertEqual(FakeWGP.safety_calls, 0)

            for path in out_dir.glob("director_rejoin_ordinary_*"):
                path.unlink()
            h3 = self._state("minimax_h3")
            failure_namespace = self._isolated_rejoin(
                h3, out_dir / "pipeline.json", FakeWGP, updates,
            )
            FakeWGP.fail = True
            with self.assertRaisesRegex(RuntimeError, "injected safety failure"):
                failure_namespace["_rejoin_clips_impl"](str(out_dir), "failed")
            self.assertFalse(list(out_dir.glob("director_rejoin_failed_*")))
            self.assertFalse(list(out_dir.glob(".*.staging.mp4")))

    def test_saved_rejoin_rejects_mixed_family_and_orders_publication(self):
        source = ast.get_source_segment(
            self.source,
            next(
                item for item in self.tree.body
                if isinstance(item, ast.FunctionDef)
                and item.name == "_rejoin_clips_impl"
            ),
        )
        adoption = source.index("_director_rejoin_h3_adoption_verified(")
        concat = source.index("concatenate_multi_clip_videos(", adoption)
        safety = source.index(
            "_enforce_director_rejoin_h3_final_audio(", concat,
        )
        promotion = source.index("os.replace(staging_path, output_path)", safety)
        sidecar = source.index("sidecar = {", promotion)
        self.assertLess(adoption, concat)
        self.assertLess(concat, safety)
        self.assertLess(safety, promotion)
        self.assertLess(promotion, sidecar)

        class FakeWGP:
            @staticmethod
            def get_base_model_type(model_type):
                return model_type

        state = self._state("minimax_h3")
        state["_params_snapshot"]["video_model"] = "wan_2_2"
        namespace = self._isolated_rejoin(
            state, Path("pipeline.json"), FakeWGP(), [],
        )
        with self.assertRaisesRegex(ValueError, "mixed video model families"):
            namespace["_director_rejoin_model_identity"](state)


if __name__ == "__main__":
    unittest.main()
