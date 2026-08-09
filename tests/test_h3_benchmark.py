from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
import json

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.h3_benchmark import (  # noqa: E402
    H3BenchmarkCache,
    H3BenchmarkError,
    aggregate_h3_estimates,
    build_benchmark_report,
    build_benchmark_spec,
    estimate_h3_output,
    measure_benchmark,
    normalize_estimate_context,
    record_observation,
    validate_output_artifacts,
)


def spec(case="text_only", engine="sdpa", signature=None):
    return build_benchmark_spec(
        case_id=case,
        hardware={"gpu": "test", "compute_capability": "sm120", "driver": "1"},
        runtime={"torch": "2", "cuda": "13", "triton": "3", "model_load_state": "resident"},
        model={"id": "minimax_h3", "revision": "abc", "sha256": "1" * 64},
        engine={"id": engine, "revision": "dense" if engine == "sdpa" else "sol1"},
        encoder={"id": "nvfp4", "revision": "enc", "sha256": "2" * 64},
        input_signature=signature,
    )


class H3BenchmarkTests(unittest.TestCase):
    def test_cache_key_changes_with_reference_and_engine(self):
        base = spec()
        frame = spec("first_frame", signature={"has_start": True})
        sol = spec(engine="sol_attn")
        self.assertNotEqual(base["cache_key"], frame["cache_key"])
        self.assertNotEqual(base["cache_key"], sol["cache_key"])

    def test_cache_key_separates_accelerator_hardware_and_window_geometry(self):
        native = spec()
        turbo = spec()
        turbo["model"]["accelerator"] = "turbo"
        turbo["cache_key"] = build_benchmark_spec(
            case_id="text_only",
            hardware=turbo["hardware"], runtime=turbo["runtime"],
            model=turbo["model"], engine=turbo["engine"],
            encoder=turbo["encoder"],
        )["cache_key"]
        other_gpu = build_benchmark_spec(
            case_id="text_only",
            hardware={**native["hardware"], "gpu": "other"},
            runtime=native["runtime"], model=native["model"],
            engine=native["engine"], encoder=native["encoder"],
        )
        long_window = build_benchmark_spec(
            case_id="text_only",
            hardware=native["hardware"], runtime=native["runtime"],
            model=native["model"], engine=native["engine"],
            encoder=native["encoder"],
            task={"profile": "observed_job", "frame_count": 248,
                  "processed_frame_count": 248, "window_count": 2},
        )
        self.assertNotEqual(native["cache_key"], turbo["cache_key"])
        self.assertNotEqual(native["cache_key"], other_gpu["cache_key"])
        self.assertNotEqual(native["cache_key"], long_window["cache_key"])

    def test_audio_and_multirate_identity_are_safe_cache_factors(self):
        native = spec()
        remix = build_benchmark_spec(
            case_id="text_only",
            hardware=native["hardware"], runtime=native["runtime"],
            model=native["model"], engine=native["engine"],
            encoder=native["encoder"],
            task={
                "source_audio_mode": "remix_source",
                "audio_algorithm_version": "maestro_h3_source_audio_v1",
            },
        )
        evidence = build_benchmark_spec(
            case_id="text_only",
            hardware=native["hardware"], runtime=native["runtime"],
            model=native["model"], engine=native["engine"],
            encoder=native["encoder"],
            task={
                "profile": "observed_job",
                "sampling_steps": 8,
                "multirate_profile": "t8_4v8a_evidence_v1",
                "video_evaluations": 4,
                "audio_evaluations": 8,
            },
        )
        self.assertNotEqual(native["cache_key"], remix["cache_key"])
        self.assertNotEqual(native["cache_key"], evidence["cache_key"])
        encoded = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("path", encoded)
        self.assertNotIn("prompt", encoded)

    def test_audio_identity_cannot_smuggle_paths_through_safe_task_fields(self):
        baseline = spec()
        common = {
            "case_id": "text_only",
            "hardware": baseline["hardware"],
            "runtime": baseline["runtime"],
            "model": baseline["model"],
            "engine": baseline["engine"],
            "encoder": baseline["encoder"],
        }
        for mode in ("native", "remix_source"):
            with self.subTest(mode=mode), self.assertRaises(H3BenchmarkError):
                build_benchmark_spec(**common, task={
                    "source_audio_mode": mode,
                    "audio_algorithm_version": "/private/source.wav",
                })

    def test_power_limit_is_safe_identity_and_spectrum_metrics_are_content_free(self):
        powered = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "test", "power_limit_watts": 575, "device_path": "/dev/private"},
            runtime={"torch": "2", "model_load_state": "resident"},
            model={"id": "minimax_h3", "accelerator": "spectrum"},
            engine={"id": "sol_attn"},
            encoder={"id": "nvfp4"},
        )
        unpowered = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "test", "power_limit_watts": 450},
            runtime={"torch": "2", "model_load_state": "resident"},
            model={"id": "minimax_h3", "accelerator": "spectrum"},
            engine={"id": "sol_attn"},
            encoder={"id": "nvfp4"},
        )
        self.assertNotEqual(powered["cache_key"], unpowered["cache_key"])
        record = record_observation(
            powered,
            wall_time_seconds=42,
            output_frames=124,
            output_valid=True,
            actual_transformer_calls=11,
            forecast_transformer_calls=9,
            replay_transformer_calls=0,
            average_power_watts=401.5,
            energy_joules=16863,
            phase_times_seconds={
                "spectrum_anchor_capture": 40,
                "spectrum_offline_replay": 2,
                "private_media_path": 999,
            },
        )
        encoded = json.dumps(record, sort_keys=True)
        self.assertNotIn("device_path", encoded)
        self.assertNotIn("private_media_path", encoded)
        self.assertEqual(record["actual_transformer_calls"], 11)
        self.assertEqual(record["forecast_transformer_calls"], 9)
        self.assertEqual(record["replay_transformer_calls"], 0)
        self.assertEqual(record["average_power_watts"], 401.5)
        self.assertEqual(record["energy_joules"], 16863)

    def test_invalid_power_and_call_metrics_are_rejected(self):
        with self.assertRaisesRegex(H3BenchmarkError, "actual_transformer_calls"):
            record_observation(
                spec(), wall_time_seconds=1, output_frames=124, output_valid=True,
                actual_transformer_calls=1.5,
            )
        with self.assertRaisesRegex(H3BenchmarkError, "energy_joules"):
            record_observation(
                spec(), wall_time_seconds=1, output_frames=124, output_valid=True,
                energy_joules=float("nan"),
            )

    def test_reference_case_requires_content_free_shape(self):
        with self.assertRaisesRegex(H3BenchmarkError, "content-free"):
            spec("ref2va")

    def test_spec_strips_urls_hashes_revisions_and_seed(self):
        result = spec("ref2va", signature={
            "sha256": "a" * 64,
            "file_count": 2,
        })
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("sha256", encoded)
        self.assertNotIn("revision", encoded)
        self.assertNotIn("seed", encoded)
        self.assertEqual(result["input_shape"], {"image_count": 2})

    def test_measurement_is_observed_and_normalized(self):
        ticks = iter((10.0, 20.0, 30.0, 35.0))
        dense = measure_benchmark(
            spec(), lambda _spec: {"output_valid": True, "output_frames": 124},
            clock=lambda: next(ticks),
        )
        sol = measure_benchmark(
            spec(engine="sol_attn"),
            lambda _spec: {"output_valid": True, "output_frames": 124, "peak_gpu_memory_bytes": 123},
            clock=lambda: next(ticks),
        )
        report = build_benchmark_report([dense, sol])
        self.assertEqual(report["records"][0]["normalized_speed_index"], 100.0)
        self.assertEqual(report["records"][1]["normalized_speed_index"], 200.0)
        self.assertTrue(all(not item["comparable_to_maestro_quick_task"] for item in report["published_external"]))

    def test_report_never_normalizes_across_hardware(self):
        dense = record_observation(
            spec(), wall_time_seconds=100, output_frames=124, output_valid=True,
        )
        other_spec = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "other", "compute_capability": "sm90"},
            runtime={"torch": "2", "cuda": "13", "model_load_state": "resident"},
            model={"id": "minimax_h3"}, engine={"id": "sol_attn"},
            encoder={"id": "nvfp4"},
        )
        other = record_observation(
            other_spec, wall_time_seconds=10, output_frames=124, output_valid=True,
        )
        report = build_benchmark_report([dense, other])
        self.assertEqual(report["records"][0]["normalized_speed_index"], 100.0)
        self.assertIsNone(report["records"][1]["normalized_speed_index"])

    def test_invalid_output_is_never_cached_as_a_measurement(self):
        with self.assertRaisesRegex(H3BenchmarkError, "finite/artifact"):
            measure_benchmark(spec(), lambda _spec: {"output_valid": False})

    def test_cache_aggregates_same_configuration_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = H3BenchmarkCache(Path(directory) / "cache.json")
            first = record_observation(
                spec(), wall_time_seconds=2, output_frames=124, output_valid=True,
                actual_transformer_calls=11,
            )
            second = record_observation(
                spec(), wall_time_seconds=1, output_frames=124, output_valid=True,
                actual_transformer_calls=11,
            )
            cache.put(first)
            cache.put(second)
            loaded = cache.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["sample_count"], 2)
            self.assertEqual(loaded[0]["generation_wall_time_seconds"], 1.5)
            self.assertEqual(loaded[0]["actual_transformer_calls"], 11)

    def test_legacy_cache_migrates_without_media_hash_or_exact_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            legacy = measure_benchmark(
                spec("first_frame", signature={"has_start": True}),
                lambda _spec: {"output_valid": True, "output_frames": 124},
                clock=iter((1.0, 3.0)).__next__,
            )
            legacy["schema_version"] = 1
            legacy["measured_at_unix"] = 1234.567
            legacy["artifact_sha256"] = "b" * 64
            legacy["observed_day_utc"] = "/private/session-alice/file.png"
            legacy["spec"]["input_signature"] = {"sha256": "a" * 64, "file_count": 1}
            legacy["spec"].pop("input_shape", None)
            path.write_text(json.dumps({"schema_version": 1, "records": [legacy]}), encoding="utf-8")
            loaded = H3BenchmarkCache(path).load()
            encoded = path.read_text(encoding="utf-8")
            self.assertEqual(len(loaded), 1)
            self.assertNotIn("artifact_sha256", encoded)
            self.assertNotIn("measured_at_unix", encoded)
            self.assertNotIn("sha256", encoded)
            self.assertNotIn("session-alice", encoded)
            self.assertEqual(loaded[0]["observed_day_utc"], "legacy")

    def test_estimate_scales_duration_and_uses_resident_observations_only(self):
        observed = record_observation(
            spec(), wall_time_seconds=100, output_frames=124, output_valid=True,
        )
        base = {
            "model_type": "minimax_h3", "duration_seconds": 5,
            "window_seconds": 15, "num_inference_steps": 4,
            "resolution": "608x352",
            "custom_settings": {"h3_attention_engine": "sdpa"},
            "reference_shape": {},
        }
        short = estimate_h3_output(base, [observed], model_resident=True)
        long = estimate_h3_output({**base, "duration_seconds": 10}, [observed], model_resident=True)
        self.assertEqual(short["source"], "local_observations")
        self.assertGreater(long["seconds"], short["seconds"] * 1.9)
        self.assertEqual(short["model_load_seconds"], 0)
        cold_spec = spec()
        cold_spec["runtime"]["model_load_state"] = "cold"
        cold = record_observation(
            cold_spec, wall_time_seconds=999, output_frames=124, output_valid=True,
        )
        fallback = estimate_h3_output(base, [cold], model_resident=False)
        self.assertEqual(fallback["source"], "rtx_5090_baseline")
        self.assertEqual(fallback["model_load_state"], "cold")

    def test_mislabeled_cold_generation_phase_never_trains_run_eta(self):
        cold_spec = spec()
        cold_spec["runtime"]["model_load_state"] = "cold"
        cold = record_observation(
            cold_spec, wall_time_seconds=318, output_frames=124,
            output_valid=True, phase_times_seconds={"generation": 318},
        )
        context = {
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 4,
            "resolution": "608x352",
            "custom_settings": {"h3_attention_engine": "sdpa"},
            "reference_shape": {},
        }
        estimate = estimate_h3_output(context, [cold], model_resident=False)
        self.assertEqual(estimate["source"], "rtx_5090_baseline")
        self.assertNotEqual(estimate["generation_seconds"], 318)

    def test_native_observation_never_answers_turbo_profile(self):
        observed = record_observation(
            spec(), wall_time_seconds=100, output_frames=124, output_valid=True,
        )
        turbo = estimate_h3_output({
            "model_type": "minimax_h3", "duration_seconds": 5,
            "window_seconds": 15, "num_inference_steps": 4,
            "resolution": "608x352", "tea_cache": 0,
            "custom_settings": {
                "h3_attention_engine": "sdpa",
                "h3_turbo_profile": "h3_turbo_v4",
            },
            "reference_shape": {},
        }, [observed])
        self.assertEqual(turbo["source"], "rtx_5090_baseline")

    def test_native_observation_never_answers_spectrum_profile(self):
        observed = record_observation(
            spec(engine="sol_attn"), wall_time_seconds=100,
            output_frames=124, output_valid=True,
        )
        spectrum = estimate_h3_output({
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 20,
            "resolution": "608x352", "tea_cache": 0,
            "custom_settings": {
                "h3_attention_engine": "sol_attn",
                "h3_spectrum_profile": "spectrum_h3_v1",
            },
            "reference_shape": {},
        }, [observed])
        self.assertEqual(spectrum["source"], "rtx_5090_baseline")
        self.assertIn("spectrum accelerator", spectrum["matched_factors"])
        self.assertIn("assumes no speedup", " ".join(spectrum["uncertainty_reasons"]))

    def test_previous_spectrum_algorithm_version_never_calibrates_current_profile(self):
        old_spec = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "test"},
            runtime={"torch": "2", "cuda": "13", "model_load_state": "resident"},
            model={
                "id": "minimax_h3", "accelerator": "spectrum",
                "accelerator_version": "maestro-clean-room-1",
            },
            engine={
                "id": "sol_attn", "tau": 1.0, "dense_steps": 10,
                "dense_blocks": 2, "min_tokens": 4096,
            },
            encoder={"id": "nvfp4"},
            task={
                "profile": "observed_job", "width": 608, "height": 352,
                "frame_count": 124, "processed_frame_count": 124,
                "sampling_steps": 20,
            },
        )
        observed = record_observation(
            old_spec, wall_time_seconds=10, output_frames=124, output_valid=True,
        )
        estimate = estimate_h3_output({
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 20,
            "resolution": "608x352", "tea_cache": 0,
            "custom_settings": {
                "h3_attention_engine": "sol_attn",
                "h3_spectrum_profile": "spectrum_h3_v1",
            },
            "reference_shape": {},
        }, [observed])
        self.assertEqual(estimate["source"], "rtx_5090_baseline")
        self.assertIn("maestro-clean-room-2", estimate["matched_factors"])

    def test_window_overlap_is_interpreted_as_frames(self):
        context = normalize_estimate_context({
            "duration_seconds": 20,
            "window_seconds": 10,
            "window_overlap": 24,
            "num_inference_steps": 4,
            "resolution": "608x352",
        })
        self.assertEqual(context["window_count"], 3)
        self.assertEqual(context["processed_frame_count"], 22 * 24)

    def test_sol_knob_changes_never_reuse_incompatible_observation(self):
        tuned_spec = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "test"},
            runtime={"torch": "2", "cuda": "13", "model_load_state": "resident"},
            model={"id": "minimax_h3", "accelerator": "native"},
            engine={
                "id": "sol_attn", "tau": 1.0, "dense_steps": 10,
                "dense_blocks": 2, "min_tokens": 4096,
            },
            encoder={"id": "nvfp4"},
            task={"profile": "observed_job", "sampling_steps": 20},
        )
        observed = record_observation(
            tuned_spec, wall_time_seconds=100, output_frames=124, output_valid=True,
        )
        context = {
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 4,
            "resolution": "608x352", "reference_shape": {},
            "custom_settings": {
                "h3_attention_engine": "sol_attn", "h3_sol_tau": 1.0,
                "h3_sol_dense_steps": 10, "h3_sol_dense_blocks": 2,
                "h3_sol_min_tokens": 4096,
            },
        }
        self.assertEqual(
            estimate_h3_output(context, [observed])["source"],
            "local_observations",
        )
        changed = {
            **context,
            "custom_settings": {**context["custom_settings"], "h3_sol_dense_steps": 4},
        }
        self.assertEqual(
            estimate_h3_output(changed, [observed])["source"],
            "rtx_5090_baseline",
        )

    def test_all_dense_sol_is_conservative_local_upper_bound_for_quality_and_sdpa(self):
        dense_spec = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "test"},
            runtime={"torch": "2", "cuda": "13", "model_load_state": "resident"},
            model={"id": "minimax_h3", "accelerator": "native"},
            engine={
                "id": "sol_attn", "tau": 1.0, "dense_steps": 50,
                "dense_blocks": 51, "min_tokens": 4096,
            },
            encoder={"id": "nvfp4"},
            task={
                "profile": "observed_job", "width": 608, "height": 352,
                "frame_count": 124, "processed_frame_count": 124,
                "sampling_steps": 20,
            },
        )
        observed = record_observation(
            dense_spec, wall_time_seconds=45, output_frames=124, output_valid=True,
        )
        base = {
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 20,
            "resolution": "608x352", "reference_shape": {},
        }
        quality = estimate_h3_output({
            **base,
            "custom_settings": {
                "h3_attention_engine": "sol_attn", "h3_sol_tau": 1.0,
                "h3_sol_dense_steps": 10, "h3_sol_dense_blocks": 2,
                "h3_sol_min_tokens": 4096,
            },
        }, [observed])
        ultra_engine = estimate_h3_output({
            **base,
            "custom_settings": {"h3_attention_engine": "sdpa"},
        }, [observed])
        for estimate in (quality, ultra_engine):
            self.assertEqual(estimate["source"], "local_compatible_upper_bound")
            self.assertEqual(estimate["confidence"], "low")
            self.assertEqual(estimate["seconds"], 45)
            self.assertIn("conservative upper bound", " ".join(estimate["uncertainty_reasons"]))

    def test_sparse_sol_never_substitutes_for_sdpa(self):
        sparse = record_observation(
            build_benchmark_spec(
                case_id="text_only", hardware={"gpu": "test"},
                runtime={"torch": "2", "cuda": "13", "model_load_state": "resident"},
                model={"id": "minimax_h3", "accelerator": "native"},
                engine={
                    "id": "sol_attn", "tau": 1.0, "dense_steps": 10,
                    "dense_blocks": 2, "min_tokens": 4096,
                },
                encoder={"id": "nvfp4"},
                task={"profile": "observed_job", "sampling_steps": 20},
            ),
            wall_time_seconds=30, output_frames=124, output_valid=True,
        )
        estimate = estimate_h3_output({
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 20,
            "resolution": "608x352", "reference_shape": {},
            "custom_settings": {"h3_attention_engine": "sdpa"},
        }, [sparse])
        self.assertEqual(estimate["source"], "rtx_5090_baseline")

    def test_delivery_profiles_include_learned_upscale_and_exact_fit_time(self):
        base = {
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 30,
            "resolution": "1344x768", "reference_shape": {},
            "custom_settings": {"h3_attention_engine": "sdpa"},
        }
        ultra = estimate_h3_output({
            **base,
            "spatial_upsampling": "flashvsr2pass2",
            "delivery_resolution": "2688x1536",
            "delivery_fit": "upscale_exact",
        }, [])
        delivery_4k = estimate_h3_output({
            **base,
            "spatial_upsampling": "flashvsr3",
            "delivery_resolution": "3840x2160",
            "delivery_fit": "center_crop",
        }, [])
        delivery_1080 = estimate_h3_output({
            **base,
            "num_inference_steps": 20,
            "spatial_upsampling": "flashvsr1.5",
            "delivery_resolution": "1920x1080",
            "delivery_fit": "center_crop",
        }, [])
        self.assertEqual(ultra["delivery_resolution"], "2688x1536")
        self.assertEqual(delivery_4k["delivery_resolution"], "3840x2160")
        self.assertEqual(delivery_1080["delivery_resolution"], "1920x1080")
        self.assertIn("2016x1152 learned upscale", " ".join(delivery_1080["matched_factors"]))
        self.assertGreater(ultra["postprocess_seconds"], 0)
        self.assertGreater(delivery_4k["postprocess_seconds"], 0)
        self.assertLessEqual(abs(
            ultra["seconds"]
            - ultra["generation_seconds"]
            - ultra["postprocess_seconds"]
        ), 1)
        self.assertIn("learned upscale", " ".join(delivery_4k["matched_factors"]))
        self.assertIn("center crop/downsample", " ".join(delivery_4k["matched_factors"]))

    def test_delivery_target_cannot_masquerade_as_native_resolution(self):
        with self.assertRaisesRegex(H3BenchmarkError, "requires spatial upsampling"):
            normalize_estimate_context({
                "resolution": "1344x768", "duration_seconds": 5,
                "num_inference_steps": 30,
                "delivery_resolution": "3840x2160",
                "delivery_fit": "center_crop",
            })

    def test_calibrated_fallback_does_not_repeat_cold_load_in_run_eta(self):
        base = {
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "reference_shape": {},
            "activated_loras": [], "tea_cache": 0,
        }
        high = estimate_h3_output({
            **base, "num_inference_steps": 20, "resolution": "1344x768",
            "custom_settings": {"h3_attention_engine": "sol_attn"},
        }, [])
        draft = estimate_h3_output({
            **base, "num_inference_steps": 4, "resolution": "608x352",
            "custom_settings": {
                "h3_attention_engine": "sage2",
                "h3_turbo_profile": "h3_turbo_v4",
            },
        }, [])
        self.assertEqual(high["source"], "rtx_5090_baseline")
        self.assertLess(high["seconds"], 240)
        self.assertLess(draft["seconds"], 20)

    def test_sage2_has_a_distinct_estimator_signature_and_unknown_engines_fail(self):
        sage_spec = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "test"},
            runtime={"torch": "2", "cuda": "12.8", "model_load_state": "resident"},
            model={"id": "minimax_h3", "accelerator": "native"},
            engine={"id": "sage2", "effective_id": "sage2"},
            encoder={"id": "nvfp4"},
        )
        observed = record_observation(
            sage_spec, wall_time_seconds=80, output_frames=124, output_valid=True,
        )
        context = {
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 4,
            "resolution": "608x352", "reference_shape": {},
            "custom_settings": {"h3_attention_engine": "sage2"},
        }
        normalized = normalize_estimate_context(context)
        self.assertEqual(normalized["engine_signature"], {"id": "sage2"})
        self.assertEqual(estimate_h3_output(context, [observed])["source"], "local_observations")
        fallback_spec = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "test"},
            runtime={"torch": "2", "cuda": "12.8", "model_load_state": "resident"},
            model={"id": "minimax_h3", "accelerator": "native"},
            engine={"id": "sage2", "effective_id": "sdpa"},
            encoder={"id": "nvfp4"},
        )
        fallback = record_observation(
            fallback_spec, wall_time_seconds=20, output_frames=124, output_valid=True,
        )
        self.assertEqual(
            estimate_h3_output(context, [fallback])["source"],
            "rtx_5090_baseline",
        )
        with self.assertRaisesRegex(H3BenchmarkError, "Unknown H3 attention engine"):
            normalize_estimate_context({
                **context,
                "custom_settings": {"h3_attention_engine": "mystery"},
            })

    def test_estimate_payload_contains_only_safe_explanation_factors(self):
        estimate = estimate_h3_output({
            "model_type": "minimax_h3", "duration_seconds": 20,
            "num_inference_steps": 20, "resolution": "960x544",
            "activated_loras": ["/private/secret.safetensors"],
            "reference_shape": {"image_count": 1},
        }, [])
        encoded = json.dumps(estimate)
        self.assertNotIn("private", encoded)
        self.assertNotIn("secret", encoded)
        self.assertIn("LoRA", encoded)
        self.assertIn("uncertainty_reasons", estimate)

    def test_cross_reference_case_extrapolation_stays_low_confidence(self):
        observed = record_observation(
            spec(), wall_time_seconds=100, output_frames=124, output_valid=True,
        )
        estimate = estimate_h3_output({
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 4,
            "resolution": "608x352",
            "custom_settings": {"h3_attention_engine": "sdpa"},
            "reference_shape": {"has_start": True},
        }, [observed])
        self.assertEqual(estimate["source"], "local_observations")
        self.assertEqual(estimate["confidence"], "low")
        self.assertTrue(any(
            "different case" in reason
            for reason in estimate["uncertainty_reasons"]
        ))

    def test_confirmed_segment_estimates_are_summed(self):
        aggregate = aggregate_h3_estimates([
            {
                "seconds": 100, "range_seconds": {"low": 70, "high": 130},
                "confidence": "medium", "sample_count": 1,
                "model_load_seconds": 150, "model_load_state": "cold",
                "uncertainty_reasons": ["first"],
            },
            {
                "seconds": 80, "range_seconds": {"low": 60, "high": 100},
                "confidence": "low", "sample_count": 0,
                "model_load_seconds": 0, "model_load_state": "resident",
                "uncertainty_reasons": ["second"],
            },
        ])
        self.assertEqual(aggregate["seconds"], 180)
        self.assertEqual(aggregate["range_seconds"], {"low": 130, "high": 230})
        self.assertEqual(aggregate["confidence"], "low")
        self.assertEqual(aggregate["model_load_seconds"], 150)

    def test_observation_scales_for_user_lora_count(self):
        observed = record_observation(
            spec(), wall_time_seconds=100, output_frames=124, output_valid=True,
        )
        base = {
            "model_type": "minimax_h3", "duration_seconds": 124 / 24,
            "window_seconds": 15, "num_inference_steps": 4,
            "resolution": "608x352",
            "custom_settings": {"h3_attention_engine": "sdpa"},
            "reference_shape": {},
        }
        plain = estimate_h3_output(base, [observed])
        with_lora = estimate_h3_output(
            {**base, "activated_loras": ["user.safetensors"]}, [observed],
        )
        self.assertGreater(with_lora["seconds"], plain["seconds"])

    def test_artifact_validation_rejects_empty_or_unprobeable_media(self):
        class Probe:
            def __init__(self, code=0, stdout="video\n"):
                self.returncode = code
                self.stdout = stdout

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "output.mp4")
            path.write_bytes(b"")
            calls = []
            self.assertFalse(validate_output_artifacts(
                directory, [path.name], probe_runner=lambda *a, **k: calls.append(a),
            ))
            self.assertEqual(calls, [])
            path.write_bytes(b"not-empty")
            self.assertFalse(validate_output_artifacts(
                directory, [path.name],
                probe_runner=lambda *a, **k: Probe(1, ""),
            ))
            self.assertTrue(validate_output_artifacts(
                directory, [path.name],
                probe_runner=lambda *a, **k: Probe(),
            ))
            self.assertFalse(validate_output_artifacts(
                directory, ["../output.mp4"],
                probe_runner=lambda *a, **k: Probe(),
            ))


if __name__ == "__main__":
    unittest.main()
