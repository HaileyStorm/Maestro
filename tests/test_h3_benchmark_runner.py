"""Offline contracts for the local synthetic H3 benchmark runner."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "app" / "scripts" / "benchmark_h3_profiles.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_h3_profiles", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runner
_SPEC.loader.exec_module(runner)


class TestH3BenchmarkMatrix(unittest.TestCase):
    def test_default_matrix_covers_requested_native_turbo_variants(self):
        matrix = runner.build_matrix()
        self.assertEqual(
            [case.case_id for case in matrix],
            [
                "base_native_sdpa",
                "base_turbo_4_sdpa",
                "base_turbo_8_sdpa",
                "base_exact_dense_sol",
                "w4a8_turbo_8_sdpa",
                "ref2va_native_sdpa",
                "ref2va_turbo_4_sdpa",
                "ref2va_turbo_8_sdpa",
            ],
        )
        ref_steps = {
            case.steps for case in matrix
            if case.model_type == "minimax_h3_ref2va" and case.turbo
        }
        self.assertEqual(ref_steps, {4, 8})
        self.assertTrue(all(case.export_frames for case in matrix if case.semantic_reference))

    def test_safe_overrides_and_selection_are_ordered(self):
        matrix = runner.build_matrix(
            ["base_turbo_8_sdpa", "base_native_sdpa"],
            {"base_turbo_8_sdpa": {"resolution": "864x480", "steps": 6}},
        )
        self.assertEqual([case.case_id for case in matrix], [
            "base_turbo_8_sdpa", "base_native_sdpa",
        ])
        self.assertEqual(matrix[0].steps, 6)
        self.assertEqual(matrix[0].resolution, "864x480")

    def test_sage2_cases_are_opt_in_base_native_and_turbo_validation_only(self):
        case_ids = (
            "base_native_sage2", "base_turbo_4_sage2", "base_turbo_8_sage2",
            "base_fast_864_turbo_8_sage2",
        )
        defaults = [case.case_id for case in runner.build_matrix()]
        self.assertTrue(all(case_id not in defaults for case_id in case_ids))
        cases = runner.build_matrix(case_ids)
        self.assertEqual([case.case_id for case in cases], list(case_ids))
        self.assertTrue(all(case.model_type == "minimax_h3" for case in cases))
        self.assertTrue(all(case.attention_engine == "sage2" for case in cases))
        self.assertEqual([case.turbo for case in cases], [False, True, True, True])
        self.assertEqual([case.steps for case in cases], [20, 4, 8, 8])
        for case in cases:
            payload = runner.build_generation_payload(
                case, project="synthetic-project", seed=7, reference_path=None,
            )
            self.assertEqual(payload["custom_settings"]["h3_attention_engine"], "sage2")
            self.assertEqual(
                "h3_turbo_profile" in payload["custom_settings"], case.turbo,
            )

    def test_fast_864_sage_gate_has_an_exact_opt_in_sdpa_pair(self):
        case_ids = ("base_fast_864_turbo_8_sdpa", "base_fast_864_turbo_8_sage2")
        self.assertTrue(all(case_id not in [case.case_id for case in runner.build_matrix()] for case_id in case_ids))
        sdpa, sage = runner.build_matrix(case_ids)
        for case in (sdpa, sage):
            self.assertEqual(case.resolution, "864x480")
            self.assertEqual(case.steps, 8)
            self.assertTrue(case.turbo)
            self.assertTrue(case.export_frames)
        self.assertEqual(sdpa.attention_engine, "sdpa")
        self.assertEqual(sage.attention_engine, "sage2")
        sdpa_payload = runner.build_generation_payload(sdpa, project="fast-864", seed=314159265, reference_path=None)
        sage_payload = runner.build_generation_payload(sage, project="fast-864", seed=314159265, reference_path=None)
        self.assertEqual(sdpa_payload["seed"], sage_payload["seed"])
        self.assertEqual(sdpa_payload["resolution"], sage_payload["resolution"])
        self.assertEqual(sdpa_payload["num_inference_steps"], sage_payload["num_inference_steps"])

    def test_high_and_delivery_profiles_are_exact_opt_in_cases(self):
        case_ids = (
            "base_high_native_sol", "base_1080p_delivery",
            "base_ultra_delivery", "base_4k_delivery",
        )
        defaults = [case.case_id for case in runner.build_matrix()]
        self.assertTrue(all(case_id not in defaults for case_id in case_ids))
        high, hd, ultra, delivery_4k = runner.build_matrix(case_ids)
        self.assertEqual(
            (high.resolution, high.steps, high.attention_engine,
             high.sol_dense_steps, high.sol_dense_blocks),
            ("1344x768", 20, "sol_attn", 10, 2),
        )
        self.assertEqual(
            (hd.spatial_upsampling, hd.delivery_resolution, hd.delivery_fit),
            ("flashvsr1.5", "1920x1080", "center_crop"),
        )
        self.assertEqual(
            (ultra.spatial_upsampling, ultra.delivery_resolution, ultra.delivery_fit),
            ("flashvsr2pass2", "2688x1536", "upscale_exact"),
        )
        self.assertEqual(
            (delivery_4k.spatial_upsampling, delivery_4k.delivery_resolution,
             delivery_4k.delivery_fit),
            ("flashvsr3", "3840x2160", "center_crop"),
        )
        for case in (hd, ultra, delivery_4k):
            payload = runner.build_generation_payload(
                case, project="synthetic-project", seed=7, reference_path=None,
            )
            self.assertEqual(payload["spatial_upsampling"], case.spatial_upsampling)
            self.assertEqual(payload["delivery_resolution"], case.delivery_resolution)
            self.assertEqual(payload["delivery_fit"], case.delivery_fit)

    def test_matrix_config_rejects_prompts_paths_and_model_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "matrix.json"
            for unsafe in ("prompt", "image_refs", "model_type", "output_path"):
                with self.subTest(field=unsafe):
                    config.write_text(json.dumps({
                        "base_native_sdpa": {unsafe: "private-value"},
                    }), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "unsupported matrix override"):
                        runner.load_matrix_overrides(str(config))

    def test_invalid_turbo_steps_native_resolution_and_types_fail_closed(self):
        invalid = (
            {"steps": 3},
            {"steps": True},
            {"resolution": "1920x1080"},
            {"attention_engine": "mystery"},
            {"export_frames": "yes"},
            {"enabled": 0},
        )
        for override in invalid:
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    runner.build_matrix(
                        ["base_turbo_4_sdpa"],
                        {"base_turbo_4_sdpa": override},
                    )
        for override in (
            {"steps": 6},
            {"resolution": "864x480"},
        ):
            with self.subTest(ref2va_override=override), self.assertRaises(ValueError):
                runner.build_matrix(
                    ["ref2va_turbo_4_sdpa"],
                    {"ref2va_turbo_4_sdpa": override},
                )


class TestH3BenchmarkSafety(unittest.TestCase):
    def test_base_url_is_strictly_loopback(self):
        accepted = (
            "http://127.0.0.1:42016",
            "http://127.99.4.2:9000/maestro/",
            "http://localhost:8000",
            "https://[::1]:8443",
        )
        for value in accepted:
            with self.subTest(value=value):
                self.assertTrue(runner.validate_local_base_url(value))
        rejected = (
            "https://example.com",
            "http://192.168.1.5:42016",
            "file:///tmp/server",
            "http://user:secret@localhost:8000",
            "http://localhost:8000?token=secret",
            "http://127.evil.example:8000",
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(Exception):
                    runner.validate_local_base_url(value)

    def test_procedural_reference_is_deterministic_valid_png(self):
        first = runner.procedural_reference_png(96, 64)
        second = runner.procedural_reference_png(96, 64)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", first)
        self.assertTrue(first.endswith(b"IEND\xaeB`\x82"))

    def test_payload_is_single_output_and_ref_uses_image_reference_mode(self):
        cases = {case.case_id: case for case in runner.build_matrix()}
        base = runner.build_generation_payload(
            cases["base_turbo_4_sdpa"],
            project="synthetic-project",
            seed=7,
            reference_path=None,
        )
        self.assertEqual(base["repeat_generation"], 1)
        self.assertTrue(base["private_output"])
        self.assertEqual(base["custom_settings"]["h3_turbo_profile"], "h3_turbo_v4")
        self.assertNotIn("h3_turbo_validation_mode", base["custom_settings"])
        ref = runner.build_generation_payload(
            cases["ref2va_turbo_4_sdpa"],
            project="synthetic-project",
            seed=7,
            reference_path="synthetic.png",
        )
        self.assertEqual(ref["image_refs"], ["synthetic.png"])
        self.assertTrue(ref["h3_ref2va_terms_accepted"])
        self.assertEqual(ref["video_prompt_type"], "I")
        self.assertNotIn("h3_turbo_validation_mode", ref["custom_settings"])

    def test_submit_uses_header_only_for_ref2va_turbo_visual_probes(self):
        client = runner.MaestroClient("http://127.0.0.1:42016", 5)
        with mock.patch.object(
            client, "json", return_value={"job_id": "synthetic-job"},
        ) as request:
            ref_case = runner.build_matrix(["ref2va_turbo_4_sdpa"])[0]
            payload = runner.build_generation_payload(
                ref_case,
                project="synthetic-project",
                seed=7,
                reference_path="synthetic.png",
            )
            self.assertEqual(client.submit(payload), "synthetic-job")
        self.assertEqual(
            request.call_args.kwargs["headers"],
            {runner.BENCHMARK_HEADER_NAME: runner.BENCHMARK_HEADER_VALUE},
        )

        for case_id in (
            "base_native_sdpa",
            "base_turbo_4_sdpa",
            "w4a8_turbo_8_sdpa",
            "ref2va_native_sdpa",
            "base_native_sage2",
            "base_turbo_4_sage2",
            "base_turbo_8_sage2",
            "base_fast_864_turbo_8_sdpa",
            "base_fast_864_turbo_8_sage2",
        ):
            case = runner.build_matrix([case_id])[0]
            payload = runner.build_generation_payload(
                case,
                project="synthetic-project",
                seed=7,
                reference_path=("synthetic.png" if case.semantic_reference else None),
            )
            with self.subTest(case_id=case_id), mock.patch.object(
                client, "json", return_value={"job_id": "ordinary-job"},
            ) as ordinary:
                self.assertEqual(client.submit(payload), "ordinary-job")
            self.assertIsNone(ordinary.call_args.kwargs["headers"])

    def test_project_access_does_not_change_global_active_workspace(self):
        client = runner.MaestroClient("http://127.0.0.1:42016", 5)
        with mock.patch.object(client, "json", return_value={}) as request:
            client.establish_project_access("synthetic-project", "secret")
        calls = request.call_args_list
        self.assertEqual(calls[0].args[:2], ("GET", "/api/v1/workspaces"))
        self.assertEqual(
            calls[1].args[:2],
            ("POST", "/api/v1/workspaces/synthetic-project/unlock"),
        )
        self.assertFalse(any(call.args[0] == "PUT" for call in calls))

    def test_redirects_and_proxies_are_disabled_and_request_is_revalidated(self):
        client = runner.MaestroClient("http://127.0.0.1:42016", 5)
        self.assertTrue(any(
            isinstance(handler, runner._NoRedirectHandler)
            for handler in client._opener.handlers
        ))
        self.assertEqual(client._proxy_handler.proxies, {})
        handler = runner._NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://evil"))
        client.base_url = "https://example.com"
        with self.assertRaisesRegex(runner.BenchmarkError, "non_loopback_request"):
            client.json("GET", "/api/v1/workspaces")

    def test_client_sends_the_validated_loopback_origin(self):
        client = runner.MaestroClient("http://127.0.0.1:42016", 5)
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"{}"

        def open_request(request, **_kwargs):
            captured["origin"] = request.get_header("Origin")
            return Response()

        client._opener.open = open_request
        client._request("POST", "/api/v1/generate", data=b"{}")
        self.assertEqual(captured["origin"], "http://127.0.0.1:42016")

    def test_failure_classification_never_returns_raw_error(self):
        secret = "/private/user/path/checkpoint.safetensors incompatible"
        category = runner.classify_failure(secret, 500)
        self.assertEqual(category, "compatibility")
        self.assertNotIn("private", category)
        self.assertNotIn("checkpoint", category)

    def test_dry_run_makes_no_client_and_prints_only_sanitized_matrix(self):
        stdout = io.StringIO()
        with mock.patch.object(runner, "MaestroClient") as client, contextlib.redirect_stdout(stdout):
            result = runner.main([
                "--base-url", "http://127.0.0.1:42016",
                "--project", "synthetic-project",
                "--case", "ref2va_turbo_4_sdpa",
                "--dry-run",
            ])
        self.assertEqual(result, 0)
        client.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("ref2va_turbo_4_sdpa", output)
        self.assertNotIn(runner.SYNTHETIC_REF_PROMPT, output)
        self.assertNotIn("password", output.lower())


class _FakeClient:
    def __init__(self, status):
        self.status = status
        self.submitted = []
        self.cancelled = []

    def submit(self, payload):
        self.submitted.append(payload)
        return "synthetic-job"

    def poll(self, _job_id, **_kwargs):
        return dict(self.status)

    def download_output(self, _filename, _project, destination):
        destination.write_bytes(b"synthetic-video")

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        return True


class TestH3BenchmarkRecords(unittest.TestCase):
    def test_probe_requires_exact_video_and_audio_contract(self):
        def completed(
            width=608,
            height=352,
            duration="5.166667",
            frames="124",
            sample_rate="32000",
            channels=2,
        ):
            return types.SimpleNamespace(stdout=json.dumps({
                "streams": [
                    {
                        "codec_type": "video",
                        "width": width,
                        "height": height,
                        "duration": duration,
                        "nb_read_frames": frames,
                    },
                    {
                        "codec_type": "audio",
                        "sample_rate": sample_rate,
                        "channels": channels,
                    },
                ],
                "format": {"duration": duration},
            }))

        with mock.patch.object(runner.shutil, "which", return_value="ffprobe"), mock.patch.object(
            runner.subprocess, "run", return_value=completed(),
        ):
            valid = runner.probe_video(
                Path("synthetic.mp4"), expected_resolution="608x352",
            )
        self.assertEqual(valid["validation"], "valid")
        self.assertTrue(all(valid["checks"].values()))

        mismatches = (
            completed(width=864),
            completed(duration="8.0"),
            completed(frames="123"),
            completed(sample_rate="48000"),
            completed(channels=1),
        )
        for result in mismatches:
            with self.subTest(result=result.stdout), mock.patch.object(
                runner.shutil, "which", return_value="ffprobe",
            ), mock.patch.object(runner.subprocess, "run", return_value=result):
                invalid = runner.probe_video(
                    Path("synthetic.mp4"), expected_resolution="608x352",
                )
            self.assertEqual(invalid["validation"], "invalid")

        without_audio = completed()
        decoded = json.loads(without_audio.stdout)
        decoded["streams"] = [decoded["streams"][0]]
        without_audio.stdout = json.dumps(decoded)
        with mock.patch.object(runner.shutil, "which", return_value="ffprobe"), mock.patch.object(
            runner.subprocess, "run", return_value=without_audio,
        ):
            invalid = runner.probe_video(
                Path("synthetic.mp4"), expected_resolution="608x352",
            )
        self.assertEqual(invalid["validation"], "invalid")
        self.assertFalse(invalid["checks"]["audio_preserved"])

    def test_postprocess_stage_timing_is_reduced_without_timestamps(self):
        result = runner.summarize_postprocess_phase_times([
            {"at": 10.0, "phase": "Generation"},
            {"at": 20.0, "phase": "Upscaling"},
            {"at": 27.25, "phase": "Delivery fit"},
            {"at": 29.0, "phase": "Finalizing"},
        ])
        self.assertEqual(result, {"upscale": 7.25, "delivery_fit": 1.75})

    def test_completed_record_is_content_free_and_validated(self):
        client = _FakeClient({
            "status": "completed",
            "output_files": ["prompt-bearing-server-name.mp4"],
            "prompt_preview": "must not be retained",
            "events": [{"message": "private server detail"}],
        })
        case = runner.build_matrix(["ref2va_turbo_8_sdpa"])[0]
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            runner, "probe_video", return_value={
                "validation": "valid", "duration_seconds": 5.0,
                "width": 608, "height": 352,
            },
        ), mock.patch.object(
            runner, "export_representative_frames",
            return_value=["start.png", "middle.png", "end.png"],
        ):
            record = runner.run_case(
                client,
                case,
                project="synthetic-project",
                seed=9,
                reference_path="synthetic.png",
                output_dir=Path(temporary),
                poll_interval=0.01,
                case_timeout=5,
            )
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["output_count"], 1)
        self.assertEqual(record["validity"]["validation"], "valid")
        encoded = json.dumps(record)
        self.assertNotIn("prompt-bearing", encoded)
        self.assertNotIn("must not be retained", encoded)
        self.assertNotIn("private server detail", encoded)
        self.assertNotIn("synthetic.png", encoded)

    def test_completed_ref_case_requires_valid_probe_and_all_three_frames(self):
        client = _FakeClient({
            "status": "completed", "output_files": ["synthetic.mp4"],
        })
        case = runner.build_matrix(["ref2va_turbo_4_sdpa"])[0]
        variants = (
            ({"validation": "invalid", "duration_seconds": 5.166},
             ["start.png", "middle.png", "end.png"]),
            ({"validation": "valid", "duration_seconds": 5.166},
             ["start.png", "middle.png"]),
        )
        for validity, frames in variants:
            with self.subTest(validity=validity, frames=frames), tempfile.TemporaryDirectory() as temporary, mock.patch.object(
                runner, "probe_video", return_value=validity,
            ), mock.patch.object(
                runner, "export_representative_frames", return_value=frames,
            ):
                record = runner.run_case(
                    client, case, project="synthetic-project", seed=9,
                    reference_path="synthetic.png", output_dir=Path(temporary),
                    poll_interval=0.01, case_timeout=5,
                )
            self.assertEqual(record["status"], "invalid")
            self.assertEqual(record["failure_category"], "output_validation")

    def test_timeout_and_keyboard_interrupt_cancel_owned_job_and_stop_matrix(self):
        case = runner.build_matrix(["base_native_sdpa"])[0]
        failures = (
            runner.BenchmarkError("timeout"),
            KeyboardInterrupt(),
        )
        for failure in failures:
            client = _FakeClient({})
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                client, "poll", side_effect=failure,
            ):
                record = runner.run_case(
                    client, case, project="synthetic-project", seed=9,
                    reference_path=None, output_dir=Path("unused"),
                    poll_interval=0.01, case_timeout=5,
                )
            self.assertEqual(client.cancelled, ["synthetic-job"])
            self.assertTrue(record["cancel_requested"])
            self.assertTrue(record["stop_matrix"])

    def test_main_stops_after_a_case_requests_matrix_stop(self):
        fake_client = mock.Mock()
        stopped = {
            "config": runner.DEFAULT_CASES[0].public_config(),
            "status": "failed",
            "wall_time_seconds": 0.1,
            "output_count": 0,
            "validity": {"validation": "not_produced"},
            "visual_frames": [],
            "failure_category": "timeout",
            "stop_matrix": True,
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            runner, "MaestroClient", return_value=fake_client,
        ), mock.patch.object(
            runner, "run_case", return_value=stopped,
        ) as run_case, mock.patch.object(
            runner, "write_report", return_value=Path(temporary) / "report.json",
        ):
            result = runner.main([
                "--base-url", "http://127.0.0.1:42016",
                "--project", "synthetic-project",
                "--case", "base_native_sdpa",
                "--case", "base_turbo_4_sdpa",
                "--output-dir", temporary,
            ])
        self.assertEqual(result, 1)
        self.assertEqual(run_case.call_count, 1)

    def test_multiple_outputs_are_rejected_without_downloading(self):
        client = _FakeClient({
            "status": "completed",
            "output_files": ["one.mp4", "two.mp4"],
        })
        case = runner.build_matrix(["base_native_sdpa"])[0]
        with mock.patch.object(client, "download_output") as download:
            record = runner.run_case(
                client,
                case,
                project="synthetic-project",
                seed=9,
                reference_path=None,
                output_dir=Path("unused"),
                poll_interval=0.01,
                case_timeout=5,
            )
        self.assertEqual(record["status"], "invalid")
        self.assertEqual(record["failure_category"], "unexpected_output_count")
        download.assert_not_called()

    def test_failed_record_keeps_only_safe_structured_failure_facts(self):
        client = _FakeClient({
            "status": "failed",
            "error": "private path /tmp/secret/input.png",
            "failure_details": {
                "code": "segment_encode_failed",
                "stage": "segment_checkpoint",
                "detail": "private path /tmp/secret/input.png",
                "exception_type": "RuntimeError",
                "is_oom": False,
                "unexpected": "must not survive",
            },
        })
        case = runner.build_matrix(["base_native_sdpa"])[0]
        record = runner.run_case(
            client,
            case,
            project="synthetic-project",
            seed=9,
            reference_path=None,
            output_dir=Path("unused"),
            poll_interval=0.01,
            case_timeout=5,
        )
        self.assertEqual(record["failure_category"], "runtime")
        self.assertEqual(record["failure_details"], {
            "code": "segment_encode_failed",
            "stage": "segment_checkpoint",
            "is_oom": False,
        })
        encoded = json.dumps(record)
        self.assertNotIn("/tmp/secret", encoded)
        self.assertNotIn("RuntimeError", encoded)
        self.assertNotIn("must not survive", encoded)

    def test_failure_details_reject_content_tokens_and_respect_oom_boolean(self):
        case = runner.build_matrix(["base_native_sdpa"])[0]
        variants = (
            ({
                "code": True,
                "stage": "secret_input",
                "is_oom": False,
            }, "child encoder out of memory", {"is_oom": False}, "runtime"),
            ({
                "code": "private_prompt",
                "stage": "denoise",
                "is_oom": True,
            }, "generation failed", {
                "stage": "denoise", "is_oom": True,
            }, "out_of_memory"),
        )
        for details, error, expected_details, expected_category in variants:
            with self.subTest(details=details):
                record = runner.run_case(
                    _FakeClient({
                        "status": "failed",
                        "error": error,
                        "failure_details": details,
                    }),
                    case,
                    project="synthetic-project",
                    seed=9,
                    reference_path=None,
                    output_dir=Path("unused"),
                    poll_interval=0.01,
                    case_timeout=5,
                )
            self.assertEqual(record["failure_details"], expected_details)
            self.assertEqual(record["failure_category"], expected_category)
            encoded = json.dumps(record)
            self.assertNotIn("secret_input", encoded)
            self.assertNotIn("private_prompt", encoded)

    def test_report_contains_no_password_project_or_prompt(self):
        records = [{
            "config": runner.DEFAULT_CASES[0].public_config(),
            "status": "completed",
            "wall_time_seconds": 1.0,
            "output_count": 1,
            "validity": {"validation": "valid"},
            "visual_frames": [],
        }]
        with tempfile.TemporaryDirectory() as temporary:
            path = runner.write_report(Path(temporary), [runner.DEFAULT_CASES[0]], records)
            encoded = path.read_text(encoding="utf-8")
        self.assertNotIn("password", encoded.lower())
        self.assertNotIn("project", encoded.lower())
        self.assertNotIn(runner.SYNTHETIC_PROMPT, encoded)


if __name__ == "__main__":
    unittest.main()
