from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import torch
import torch.nn as nn


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from models.minimax_h3.spectrum import (  # noqa: E402
    SpectrumCompatibilityError,
    SpectrumGenerationController,
    SpectrumStateError,
    run_length_tensor_signature,
    small_tensor_signature,
    spectrum_anchor_indices,
    spectrum_scheduler_grid_points,
    validate_spectrum_request,
)
from models.minimax_h3.scheduler import MiniMaxH3Scheduler  # noqa: E402
from models.minimax_h3.minimax_h3_main import (  # noqa: E402
    _advance_paired_h3_latents,
    _reset_paired_h3_schedulers,
)
from models.minimax_h3.transformer import _spectrum_finalize_target_hidden  # noqa: E402


class _ScaleHead(nn.Module):
    def __init__(self, scale: float):
        super().__init__()
        self.scale = scale

    def forward(self, value):
        return value * self.scale


class _FakeFinalLayer(nn.Module):
    def __init__(self):
        super().__init__()
        self.audio_out = _ScaleHead(10.0)
        self.video_out = _ScaleHead(100.0)
        self.calls = 0

    def forward(self, hidden, curve, _turbo, timestep_runs):
        self.calls += 1
        output = hidden.clone()
        for start, end, value in timestep_runs:
            output[:, start:end] += curve[value]
        return output


class _MutatingFinalLayer(_FakeFinalLayer):
    def forward(self, hidden, curve, _turbo, timestep_runs):
        self.calls += 1
        for start, end, value in timestep_runs:
            hidden[:, start:end].add_(curve[value])
        return hidden


def valid_request(**overrides):
    request = {
        "selected_model_type": "minimax_h3",
        "model_def": {},
        "reference_mode": False,
        "sampling_steps": 20,
        "attention_engine": "sol_attn",
        "custom_settings": {
            "h3_attention_engine": "sol_attn",
            "h3_spectrum_profile": "spectrum_h3_v1",
        },
        "activated_loras": [],
        "loras_multipliers": "",
        "skip_steps_cache_type": 0,
        "native_boundary": False,
    }
    request.update(overrides)
    return request


class SpectrumControllerTests(unittest.TestCase):
    @staticmethod
    def _controller(context=("sealed-context",)):
        signatures = tuple((index,) for index in range(20))
        return SpectrumGenerationController(
            validate_spectrum_request(**valid_request()),
            total_steps=20,
            context_signature=context,
            step_signatures=signatures,
            audio_row_count=1,
            video_row_count=1,
        ), signatures

    def test_public_20_step_schedule_has_eleven_actual_and_nine_forecast_slots(self):
        self.assertEqual(spectrum_scheduler_grid_points(20), 21)
        scheduler = MiniMaxH3Scheduler(shift=12.0)
        scheduler.set_timesteps(spectrum_scheduler_grid_points(20), device="cpu")
        self.assertEqual(len(scheduler.timesteps), 20)
        anchors = spectrum_anchor_indices(20)
        self.assertEqual(anchors, (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 19))
        self.assertEqual(20 - len(anchors), 9)

    def test_paired_capture_and_offline_replay_keep_audio_blend_zero(self):
        context = ("sealed-context",)
        controller, signatures = self._controller(context)
        calls = []
        capture = []
        for index in range(20):
            feature = controller.capture_feature(
                index,
                context_signature=context,
                step_signature=signatures[index],
                actual_call=lambda index=index: (
                    calls.append(index)
                    or torch.tensor(
                        [[[100.0 + index], [float(index * index)]]]
                    )
                ),
            )
            capture.append(feature)
        self.assertEqual(calls, list(spectrum_anchor_indices(20)))
        self.assertEqual(capture[1][0, 0, 0].item(), 100.0)
        # After two anchors the causal path is a two-point extrapolator.
        self.assertEqual(capture[3][0, 0, 0].item(), 103.0)
        controller.seal_capture()

        replay = [
            controller.replay_feature(
                index,
                context_signature=context,
                step_signature=signatures[index],
            )
            for index in range(20)
        ]
        # v0.2.1's zero audio spectral share means current-slot bracketing
        # interpolation, not a prior-anchor audio hold.
        self.assertEqual(replay[1][0, 0, 0].item(), 101.0)
        self.assertEqual(replay[3][0, 0, 0].item(), 103.0)
        self.assertNotEqual(replay[1][0, 1, 0].item(), replay[0][0, 1, 0].item())
        stats = controller.stats()
        self.assertEqual(stats["actual_transformer_calls"], 11)
        self.assertEqual(stats["forecast_transformer_calls"], 9)
        self.assertEqual(stats["replay_transformer_calls"], 0)
        self.assertEqual(stats["audio_blend_weight"], 0.0)
        self.assertEqual(stats["replay_steps"], 20)

    def test_context_timestep_and_reset_invalidation_fail_closed(self):
        controller, signatures = self._controller(("original",))
        with self.assertRaisesRegex(SpectrumStateError, "conditioning/layout"):
            controller.capture_feature(
                0,
                context_signature=("changed",),
                step_signature=signatures[0],
                actual_call=lambda: torch.ones((1, 2, 1)),
            )
        with self.assertRaisesRegex(SpectrumStateError, "schedule changed"):
            controller.capture_feature(
                0,
                context_signature=("original",),
                step_signature=(999,),
                actual_call=lambda: torch.ones((1, 2, 1)),
            )
        controller.reset("segment_boundary")
        with self.assertRaisesRegex(SpectrumStateError, "reset"):
            controller.capture_feature(
                0,
                context_signature=("original",),
                step_signature=signatures[0],
                actual_call=lambda: torch.ones((1, 2, 1)),
            )

    def test_regression_solver_failure_becomes_a_safe_fallback_signal(self):
        context = ("sealed",)
        controller, signatures = self._controller(context)
        for index in range(20):
            controller.capture_feature(
                index,
                context_signature=context,
                step_signature=signatures[index],
                actual_call=lambda index=index: torch.full(
                    (1, 2, 1), float(index)
                ),
            )
        controller.seal_capture()
        controller.replay_feature(
            0, context_signature=context, step_signature=signatures[0]
        )
        with patch("torch.linalg.solve", side_effect=RuntimeError("synthetic")):
            with self.assertRaisesRegex(SpectrumStateError, "solved safely"):
                controller.replay_feature(
                    1, context_signature=context, step_signature=signatures[1]
                )

    def test_affine_correction_preserves_constant_features_exactly(self):
        context = ("constant",)
        controller, signatures = self._controller(context)
        constant = torch.tensor([[[7.0], [13.0]]])
        for index in range(20):
            controller.capture_feature(
                index,
                context_signature=context,
                step_signature=signatures[index],
                actual_call=lambda constant=constant: constant,
            )
        controller.seal_capture()
        replay = [
            controller.replay_feature(
                index,
                context_signature=context,
                step_signature=signatures[index],
            )
            for index in range(20)
        ]
        self.assertTrue(all(torch.equal(value, constant) for value in replay))

    def test_nonfinite_validation_disables_video_spectral_branch(self):
        context = ("invalid-validation",)
        controller, signatures = self._controller(context)
        for index in range(20):
            controller.capture_feature(
                index,
                context_signature=context,
                step_signature=signatures[index],
                actual_call=lambda index=index: torch.tensor(
                    [[[float(index)]], [[float(index * index)]]]
                ).transpose(0, 1),
            )
        controller.seal_capture()
        controller._validation_scores["video"] = {
            anchor: float("inf") for anchor in controller.anchor_indices
        }
        feature = controller.replay_feature(
            0, context_signature=context, step_signature=signatures[0]
        )
        self.assertTrue(torch.equal(feature, controller._anchors[0]))
        feature = controller.replay_feature(
            1, context_signature=context, step_signature=signatures[1]
        )
        expected_video = torch.lerp(
            controller._anchors[0][:, 1:],
            controller._anchors[2][:, 1:],
            0.5,
        )
        self.assertTrue(torch.equal(feature[:, 1:], expected_video))

    def test_cancel_reset_discards_generation_local_hidden_history(self):
        context = ("cancel",)
        controller, signatures = self._controller(context)
        for index in range(5):
            controller.capture_feature(
                index,
                context_signature=context,
                step_signature=signatures[index],
                actual_call=(
                    (lambda index=index: torch.full((1, 2, 1), float(index)))
                    if controller.requires_actual(index) else None
                ),
            )
        controller.reset("cancelled")
        self.assertEqual(controller._anchors, {})
        self.assertEqual(controller.stats()["reset_reason"], "cancelled")
        with self.assertRaisesRegex(SpectrumStateError, "reset"):
            controller.capture_feature(
                5,
                context_signature=context,
                step_signature=signatures[5],
                actual_call=None,
            )

    def test_small_signatures_reject_unbounded_schedule_content(self):
        with self.assertRaisesRegex(SpectrumStateError, "safe bound"):
            small_tensor_signature(torch.zeros(4097))

    def test_large_high_resolution_row_schedule_has_lossless_bounded_signature(self):
        rows = torch.cat((
            torch.zeros(12000, dtype=torch.long),
            torch.ones(24000, dtype=torch.long),
            torch.full((8000,), 2, dtype=torch.long),
        ))
        signature = run_length_tensor_signature(rows)
        self.assertEqual(signature[0], (44000,))
        self.assertEqual(
            signature[2],
            ((0.0, 12000), (1.0, 24000), (2.0, 8000)),
        )
        alternating = torch.arange(4097, dtype=torch.long).remainder(2)
        with self.assertRaisesRegex(SpectrumStateError, "run signature"):
            run_length_tensor_signature(alternating)

    def test_native_pair_advance_updates_both_modalities_and_replay_resets_state(self):
        video_scheduler = MiniMaxH3Scheduler(shift=12.0)
        audio_scheduler = MiniMaxH3Scheduler(shift=3.0)
        _reset_paired_h3_schedulers(
            video_scheduler, audio_scheduler, grid_points=3, device="cpu"
        )
        video_rows = torch.zeros((3, 2), dtype=torch.float32)
        audio_rows = torch.zeros((3, 2), dtype=torch.float32)
        prediction = (
            torch.ones((1, 3, 2), dtype=torch.float32),
            torch.full((1, 3, 2), 2.0, dtype=torch.float32),
        )
        _advance_paired_h3_latents(
            video_rows=video_rows,
            audio_rows=audio_rows,
            prediction=prediction,
            video_timestep=video_scheduler.timesteps[0],
            audio_timestep=audio_scheduler.timesteps[0],
            video_scheduler=video_scheduler,
            audio_scheduler=audio_scheduler,
            num_condition_video_rows=1,
            num_condition_audio_rows=1,
        )
        self.assertTrue(torch.equal(video_rows[0], torch.zeros(2)))
        self.assertTrue(torch.equal(audio_rows[0], torch.zeros(2)))
        self.assertTrue(bool((video_rows[1:] != 0).all()))
        self.assertTrue(bool((audio_rows[1:] != 0).all()))
        captured_video = video_rows.clone()
        captured_audio = audio_rows.clone()
        self.assertEqual(video_scheduler.step_index, 1)
        self.assertEqual(audio_scheduler.step_index, 1)

        _reset_paired_h3_schedulers(
            video_scheduler, audio_scheduler, grid_points=3, device="cpu"
        )
        self.assertIsNone(video_scheduler.step_index)
        self.assertIsNone(audio_scheduler.step_index)
        video_rows.zero_()
        audio_rows.zero_()
        _advance_paired_h3_latents(
            video_rows=video_rows,
            audio_rows=audio_rows,
            prediction=prediction,
            video_timestep=video_scheduler.timesteps[0],
            audio_timestep=audio_scheduler.timesteps[0],
            video_scheduler=video_scheduler,
            audio_scheduler=audio_scheduler,
            num_condition_video_rows=1,
            num_condition_audio_rows=1,
        )
        self.assertTrue(torch.equal(video_rows, captured_video))
        self.assertTrue(torch.equal(audio_rows, captured_audio))

    def test_fake_transformer_runs_eleven_blocks_but_fresh_heads_on_all_two_pass_steps(self):
        context = ("fake-h3-layout",)
        controller, signatures = self._controller(context)
        video_scheduler = MiniMaxH3Scheduler(shift=12.0)
        audio_scheduler = MiniMaxH3Scheduler(shift=3.0)
        _reset_paired_h3_schedulers(
            video_scheduler, audio_scheduler,
            grid_points=spectrum_scheduler_grid_points(20), device="cpu",
        )
        video_rows = torch.zeros((1, 1), dtype=torch.float32)
        audio_rows = torch.zeros((1, 1), dtype=torch.float32)
        initial_video = video_rows.clone()
        initial_audio = audio_rows.clone()
        final_layer = _FakeFinalLayer()
        curve = torch.arange(20, dtype=torch.float32).view(20, 1)
        block_calls = []
        archived = {}

        def finish(feature, index):
            return _spectrum_finalize_target_hidden(
                final_layer=final_layer,
                target_hidden=feature,
                curve=curve,
                turbo_silu_t_emb=None,
                target_timestep_indices=torch.tensor([index, index]),
                num_condition_audio_rows=0,
                num_condition_video_rows=0,
                total_audio_rows=1,
                total_video_rows=1,
                audio_target_rows=1,
                return_dict=False,
            )

        for index, (video_timestep, audio_timestep) in enumerate(zip(
            video_scheduler.timesteps, audio_scheduler.timesteps,
        )):
            def actual(index=index):
                block_calls.append(index)
                feature = torch.tensor([[[
                    audio_rows.item() + index,
                ], [
                    video_rows.item() + index * index,
                ]]], dtype=torch.float32)
                archived[index] = feature.clone()
                return feature

            feature = controller.capture_feature(
                index,
                context_signature=context,
                step_signature=signatures[index],
                actual_call=actual if controller.requires_actual(index) else None,
            )
            prediction = finish(feature, index)
            _advance_paired_h3_latents(
                video_rows=video_rows,
                audio_rows=audio_rows,
                prediction=prediction,
                video_timestep=video_timestep,
                audio_timestep=audio_timestep,
                video_scheduler=video_scheduler,
                audio_scheduler=audio_scheduler,
                num_condition_video_rows=0,
                num_condition_audio_rows=0,
            )
        controller.seal_capture()
        self.assertEqual(block_calls, list(spectrum_anchor_indices(20)))
        self.assertEqual(video_scheduler.step_index, 20)
        self.assertEqual(audio_scheduler.step_index, 20)

        video_rows.copy_(initial_video)
        audio_rows.copy_(initial_audio)
        _reset_paired_h3_schedulers(
            video_scheduler, audio_scheduler,
            grid_points=spectrum_scheduler_grid_points(20), device="cpu",
        )
        for index, (video_timestep, audio_timestep) in enumerate(zip(
            video_scheduler.timesteps, audio_scheduler.timesteps,
        )):
            feature = controller.replay_feature(
                index,
                context_signature=context,
                step_signature=signatures[index],
            )
            if index in archived:
                self.assertTrue(torch.equal(feature, archived[index]))
            prediction = finish(feature, index)
            _advance_paired_h3_latents(
                video_rows=video_rows,
                audio_rows=audio_rows,
                prediction=prediction,
                video_timestep=video_timestep,
                audio_timestep=audio_timestep,
                video_scheduler=video_scheduler,
                audio_scheduler=audio_scheduler,
                num_condition_video_rows=0,
                num_condition_audio_rows=0,
            )
        self.assertEqual(final_layer.calls, 40)
        self.assertEqual(video_scheduler.step_index, 20)
        self.assertEqual(audio_scheduler.step_index, 20)
        self.assertEqual(controller.stats()["actual_transformer_calls"], 11)
        self.assertEqual(controller.stats()["forecast_transformer_calls"], 9)
        controller.reset("completed")
        self.assertFalse(controller.active)

    def test_fresh_heads_restore_condition_row_geometry_without_caching_conditions(self):
        final_layer = _FakeFinalLayer()
        video, audio = _spectrum_finalize_target_hidden(
            final_layer=final_layer,
            target_hidden=torch.tensor([[[2.0], [3.0]]]),
            curve=torch.tensor([[5.0]]),
            turbo_silu_t_emb=None,
            target_timestep_indices=torch.tensor([0, 0]),
            num_condition_audio_rows=2,
            num_condition_video_rows=1,
            total_audio_rows=3,
            total_video_rows=2,
            audio_target_rows=1,
            return_dict=False,
        )
        self.assertEqual(tuple(audio.shape), (1, 3, 1))
        self.assertEqual(tuple(video.shape), (1, 2, 1))
        self.assertTrue(torch.equal(audio[:, :2], torch.zeros((1, 2, 1))))
        self.assertTrue(torch.equal(video[:, :1], torch.zeros((1, 1, 1))))
        self.assertEqual(audio[0, 2, 0].item(), 70.0)
        self.assertEqual(video[0, 1, 0].item(), 800.0)

    def test_fresh_head_cannot_mutate_a_sealed_hidden_anchor(self):
        hidden = torch.tensor([[[2.0], [3.0]]])
        original = hidden.clone()
        _spectrum_finalize_target_hidden(
            final_layer=_MutatingFinalLayer(),
            target_hidden=hidden,
            curve=torch.tensor([[5.0]]),
            turbo_silu_t_emb=None,
            target_timestep_indices=torch.tensor([0, 0]),
            num_condition_audio_rows=0,
            num_condition_video_rows=0,
            total_audio_rows=1,
            total_video_rows=1,
            audio_target_rows=1,
            return_dict=False,
        )
        self.assertTrue(torch.equal(hidden, original))


class SpectrumCompatibilityTests(unittest.TestCase):
    @staticmethod
    def _launch_spectrum_validator():
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_validate_h3_spectrum_estimate_context"
        )

        class FakeWgp:
            @staticmethod
            def get_model_def(model_type):
                return {
                    "minimax_h3_reference_mode": model_type == "minimax_h3_ref2va",
                }

        namespace = {
            "wgp": FakeWgp(),
            "_H3_BASE_FL2VA_MODEL": "minimax_h3",
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "launch.py", "exec"), namespace)
        return namespace["_validate_h3_spectrum_estimate_context"]

    @staticmethod
    def _launch_observation_recorder(stats, records):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_record_h3_benchmark_observation"
        )

        class Cache:
            def put(self, record):
                records.append(record)

        namespace = {
            "wgp": SimpleNamespace(
                wan_model=SimpleNamespace(_last_spectrum_stats=stats),
                get_model_def=lambda _model: {},
            ),
            "torch": torch,
            "sys": sys,
            "_H3_LONG_STUDIO_MODELS": {"minimax_h3"},
            "_H3_REF2VA_MODEL": "minimax_h3_ref2va",
            "_H3_W4A8_FL2VA_MODEL": "minimax_h3_w4a8_fl2va",
            "_H3_PEAK_RECOVERY_POLICY_VERSION": 1,
            "_h3_effective_offload_profile": (
                lambda params: int(params.get("override_profile") or 4)
            ),
            "_h3_benchmark_input_signature": lambda _params, _case: {},
            "_get_h3_benchmark_cache": lambda: Cache(),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "launch.py", "exec"), namespace)
        return namespace["_record_h3_benchmark_observation"]

    def test_exact_base_native_matrix_is_accepted_for_sdpa_and_sol(self):
        for engine in ("sdpa", "sol_attn"):
            with self.subTest(engine=engine):
                config = validate_spectrum_request(
                    **valid_request(
                        attention_engine=engine,
                        custom_settings={
                            "h3_attention_engine": engine,
                            "h3_spectrum_profile": "spectrum_h3_v1",
                        },
                    )
                )
                self.assertEqual(config.profile_id, "spectrum_h3_v1")
                self.assertEqual(config.audio_blend_weight, 0.0)

    def test_unsupported_matrix_is_rejected_before_inference(self):
        cases = {
            "Ref2VA": {"selected_model_type": "minimax_h3_ref2va", "reference_mode": True},
            "W4A8": {"selected_model_type": "minimax_h3_w4a8_fl2va"},
            "PinkCherry": {"selected_model_type": "minimax_h3_pinkcherry_fl2va"},
            "20 native": {"sampling_steps": 19},
            "Dense SDPA": {"attention_engine": "sage2"},
            "Turbo": {"custom_settings": {
                "h3_attention_engine": "sol_attn",
                "h3_spectrum_profile": "spectrum_h3_v1",
                "h3_turbo_profile": "h3_turbo_v4",
            }},
            "LoRAs": {"activated_loras": ["user.safetensors"]},
            "multipliers": {"loras_multipliers": "1.0"},
            "step cache": {"skip_steps_cache_type": "tea"},
            "boundary": {"native_boundary": True},
        }
        for message, overrides in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(
                SpectrumCompatibilityError, message
            ):
                validate_spectrum_request(**valid_request(**overrides))

    def test_no_profile_is_a_noop_for_native_behavior(self):
        request = valid_request(custom_settings={"h3_attention_engine": "sol_attn"})
        self.assertIsNone(validate_spectrum_request(**request))

    def test_estimator_validator_executes_the_same_semantic_and_segment_matrix(self):
        validate = self._launch_spectrum_validator()
        valid = {
            "model_type": "minimax_h3",
            "num_inference_steps": 20,
            "custom_settings": {
                "h3_attention_engine": "sol_attn",
                "h3_spectrum_profile": "spectrum_h3_v1",
            },
            "reference_shape": {},
            "activated_loras": [],
            "loras_multipliers": "",
            "tea_cache": 0,
        }
        validate(valid)
        with self.assertRaisesRegex(SpectrumCompatibilityError, "Ref2VA"):
            validate({**valid, "reference_shape": {"image_count": 1}})
        with self.assertRaisesRegex(SpectrumCompatibilityError, "Ref2VA"):
            validate({
                **valid,
                "_segment_contexts": [
                    valid,
                    {**valid, "model_type": "minimax_h3_ref2va"},
                ],
            })
        with self.assertRaisesRegex(SpectrumCompatibilityError, "Ref2VA"):
            validate({
                **valid,
                "model_type": "minimax_h3_ref2va",
                "_segment_contexts": [valid],
            })

    def test_observation_capture_skips_fallback_and_labels_only_completed_spectrum(self):
        params = {
            "model_type": "minimax_h3",
            "video_length": 124,
            "num_inference_steps": 20,
            "resolution": "608x352",
            "custom_settings": {
                "h3_attention_engine": "sol_attn",
                "h3_spectrum_profile": "spectrum_h3_v1",
            },
        }
        records = []
        fallback = self._launch_observation_recorder(
            {"reset_reason": "native_fallback"}, records,
        )
        fallback(
            params, wall_time_seconds=10, output_files=["out.mp4"], out_dir=".",
        )
        self.assertEqual(records, [])

        stats = {
            "reset_reason": "completed",
            "algorithm_version": "maestro-clean-room-2",
            "actual_transformer_calls": 11,
            "forecast_transformer_calls": 9,
            "replay_transformer_calls": 0,
            "replay_steps": 20,
            "anchor_capture_seconds": 7.0,
            "offline_replay_seconds": 2.0,
        }
        completed = self._launch_observation_recorder(stats, records)
        with patch(
            "services.h3_benchmark.validate_output_artifacts", return_value=True,
        ):
            completed(
                params, wall_time_seconds=10,
                output_files=["out.mp4"], out_dir=".",
            )
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["spec"]["model"]["accelerator"], "spectrum")
        self.assertEqual(
            record["spec"]["model"]["accelerator_version"],
            "maestro-clean-room-2",
        )
        self.assertEqual(record["actual_transformer_calls"], 11)
        self.assertEqual(record["forecast_transformer_calls"], 9)
        self.assertEqual(
            record["phase_times_seconds"]["spectrum_offline_replay"], 2.0,
        )

    def test_runtime_and_endpoint_include_the_same_explicit_profile_key(self):
        handler = (APP / "models" / "minimax_h3" / "minimax_h3_handler.py").read_text(
            encoding="utf-8"
        )
        main = (APP / "models" / "minimax_h3" / "minimax_h3_main.py").read_text(
            encoding="utf-8"
        )
        launch = (APP / "launch.py").read_text(encoding="utf-8")
        self.assertIn('"h3_spectrum_profile"', handler)
        self.assertIn("validate_spectrum_request(", main)
        self.assertIn('"h3_spectrum_profile", "h3_native_boundary_conditioning"', launch)
        estimate_endpoint = launch[
            launch.index("async def h3_estimate"):
            launch.index("@api.get(\"/api/v1/h3/benchmark\")")
        ]
        self.assertIn("context = _h3_estimate_context(body)", estimate_endpoint)
        self.assertIn(
            "_validate_h3_spectrum_estimate_context(context)",
            estimate_endpoint,
        )
        self.assertIn("spectrum_compatibility=spectrum_compatibility", launch)
        observation = launch[
            launch.index("def _record_h3_benchmark_observation"):
            launch.index("def _h3_estimate_context")
        ]
        self.assertIn('candidate_stats.get(\n            "reset_reason"\n        ) != "completed"', observation)
        self.assertIn('"accelerator": (\n                "spectrum"', observation)
        self.assertIn("actual_transformer_calls=", observation)
        self.assertIn('"spectrum_h3_v1"', launch)

    def test_runtime_resets_generation_local_controller_on_every_exit(self):
        source = (APP / "models" / "minimax_h3" / "minimax_h3_main.py").read_text(
            encoding="utf-8"
        )
        spectrum_branch = source[source.index("if spectrum_config is None:"):]
        self.assertIn("finally:", spectrum_branch)
        self.assertIn("reset_denoising_schedulers()", spectrum_branch)
        self.assertEqual(spectrum_branch.count("reset_denoising_schedulers()"), 2)
        self.assertIn("controller.reset(spectrum_reset_reason)", spectrum_branch)
        self.assertIn('spectrum_reset_reason = "cancelled"', spectrum_branch)


if __name__ == "__main__":
    unittest.main()
