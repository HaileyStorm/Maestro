"""Model-free contracts for the managed MiniMax-H3 Turbo runtime."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
sys.path.insert(0, str(_APP))

from services import h3_turbo  # noqa: E402


_TRANSFORMER = _APP / "models" / "minimax_h3" / "transformer.py"
_MAIN = _APP / "models" / "minimax_h3" / "minimax_h3_main.py"
_HANDLER = _APP / "models" / "minimax_h3" / "minimax_h3_handler.py"
_SCHEDULER = _APP / "models" / "minimax_h3" / "scheduler.py"
_WGP = _APP / "wgp.py"
_UPSTREAM = _APP / "models" / "minimax_h3" / "UPSTREAM.md"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _base_model_def() -> dict:
    return {
        "URLs": [
            "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/"
            "0543966fbdce5ba05709a8f2031c94bdba629b4a/diffusion_models/"
            "minimax_h3_fl2va_pruned_fp8_scaled.safetensors"
        ],
        "minimax_h3_reference_mode": False,
    }


def _turbo_settings(attention: str = "sdpa") -> dict:
    return {
        "h3_turbo_profile": h3_turbo.H3_TURBO_PROFILE_ID,
        "h3_attention_engine": attention,
    }


class _FakeTensor:
    def __init__(self, shape, dtype):
        self.shape = shape
        self.dtype = dtype


class TestH3TurboArtifactContract(unittest.TestCase):
    def test_pins_exact_upstream_revisions_and_artifact_identity(self):
        self.assertEqual(
            h3_turbo.H3_TURBO_LORA_REVISION,
            "afc0346516372a17162c14df3c5264de1d9aa1c0",
        )
        self.assertEqual(
            h3_turbo.H3_TURBO_NODE_COMMIT,
            "55fee864dd7b2976b1c4ce3c3d5f7968f181409f",
        )
        self.assertEqual(h3_turbo.H3_TURBO_LORA_SIZE, 779_849_816)
        self.assertEqual(
            h3_turbo.H3_TURBO_LORA_SHA256,
            "5f3a626cd72c93a8b9318d6760c510bc5092d2ab13aaba1f932c5bab07a416d3",
        )
        self.assertEqual(h3_turbo.H3_TURBO_GRID_SIZE, 5_510_600)
        self.assertNotIn("/main/", h3_turbo.H3_TURBO_LORA_URL)
        self.assertNotIn("/main/", h3_turbo.H3_TURBO_GRID_URL)

    def test_exact_header_map_has_518_tensors_and_51_adaln_pairs(self):
        self.assertEqual(len(h3_turbo.H3_TURBO_TENSOR_SHAPES), 518)
        self.assertEqual(len(h3_turbo.H3_TURBO_ADALN_MODULES), 51)
        self.assertEqual(
            h3_turbo.H3_TURBO_TENSOR_SHAPES[
                "blocks.0.adaln_proj.linear.lora_B.weight"
            ],
            (96768, 16),
        )
        self.assertEqual(
            h3_turbo.H3_TURBO_TENSOR_SHAPES[
                "final_layer.adaln_proj.linear.lora_B.weight"
            ],
            (10752, 16),
        )

    def test_published_pair_passes_full_size_digest_and_header_validation(self):
        status = h3_turbo.turbo_assets_status()
        if not status["available"]:
            self.skipTest(status["reason"])
        assets = h3_turbo.resolve_turbo_assets()
        self.assertEqual(h3_turbo.validate_turbo_lora(assets.lora_path), assets.lora_path)
        self.assertEqual(h3_turbo.validate_turbo_grid(assets.grid_path), assets.grid_path)

    def test_runtime_key_map_is_exact_and_adaln_is_removed_from_generic_loader(self):
        dtype = object()
        fake_torch = types.SimpleNamespace(
            bfloat16=dtype,
            is_tensor=lambda value: isinstance(value, _FakeTensor),
        )
        state = {
            name: _FakeTensor(shape, dtype)
            for name, shape in h3_turbo.H3_TURBO_TENSOR_SHAPES.items()
        }
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            captured = h3_turbo.strip_and_capture_adaln(state)
        self.assertEqual(len(captured), 51)
        self.assertEqual(len(state), 416)
        self.assertFalse(any("adaln_proj" in name for name in state))
        self.assertTrue(all("adaln_proj" in name for name in captured))

    def test_runtime_key_map_rejects_missing_extra_and_wrong_shape(self):
        dtype = object()
        fake_torch = types.SimpleNamespace(
            bfloat16=dtype,
            is_tensor=lambda value: isinstance(value, _FakeTensor),
        )
        valid = {
            name: _FakeTensor(shape, dtype)
            for name, shape in h3_turbo.H3_TURBO_TENSOR_SHAPES.items()
        }
        cases = []
        missing = dict(valid)
        missing.pop(next(iter(missing)))
        cases.append(missing)
        extra = dict(valid)
        extra["surprise.lora_A.weight"] = _FakeTensor((1, 1), dtype)
        cases.append(extra)
        wrong = dict(valid)
        wrong["blocks.0.mlp.fc2.lora_A.weight"] = _FakeTensor((63, 14336), dtype)
        cases.append(wrong)
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            for state in cases:
                with self.subTest(keys=len(state)):
                    with self.assertRaises(h3_turbo.H3TurboCompatibilityError):
                        h3_turbo.validate_runtime_state_dict(state)


class TestH3TurboMathAndLifecycle(unittest.TestCase):
    def test_adaln_injection_is_b_at_a_at_silu_t_emb(self):
        a = np.array([[1.0, 2.0, -1.0], [0.5, -2.0, 1.0]], dtype=np.float32)
        b = np.array([[2.0, 0.0], [-1.0, 3.0]], dtype=np.float32)
        temb = np.array([[1.0, 0.5, -2.0], [0.0, 2.0, 1.0]], dtype=np.float32)
        expected = np.stack([b @ a @ row for row in temb])
        np.testing.assert_allclose(
            h3_turbo.h3_turbo_adaln_delta(a, b, temb),
            expected,
            rtol=0,
            atol=0,
        )

    def test_quantization_safe_backbone_update_is_residual_output_math(self):
        x = np.array([[1.0, 2.0, -1.0], [0.5, 0.0, 3.0]], dtype=np.float32)
        a = np.array([[1.0, -1.0, 2.0], [0.0, 0.5, 1.0]], dtype=np.float32)
        b = np.array([[2.0, 0.0], [-1.0, 3.0], [0.25, 1.0]], dtype=np.float32)
        np.testing.assert_allclose(
            h3_turbo.h3_turbo_residual_delta(x, a, b),
            np.stack([b @ a @ row for row in x]),
            rtol=0,
            atol=0,
        )

    def test_prepare_activate_clear_lifecycle_uses_managed_pair(self):
        status = h3_turbo.turbo_assets_status()
        if not status["available"]:
            self.skipTest(status["reason"])

        class FakeTransformer:
            def __init__(self):
                self.grid = None
                self.active = False

            def prepare_h3_turbo(self, grid, **_kwargs):
                self.grid = grid

            def activate_h3_turbo(self):
                self.active = True

            def clear_h3_turbo(self):
                self.grid = None
                self.active = False

        transformer = FakeTransformer()
        assets = h3_turbo.prepare_h3_turbo_runtime(
            transformer,
            custom_settings=_turbo_settings(),
            model_def=_base_model_def(),
        )
        self.assertEqual(Path(transformer.grid), assets.grid_path)
        h3_turbo.activate_h3_turbo_runtime(transformer)
        self.assertTrue(transformer.active)
        h3_turbo.clear_h3_turbo_runtime(transformer)
        self.assertFalse(transformer.active)
        self.assertIsNone(transformer.grid)

    def test_managed_pair_publish_is_all_or_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.part"
            second = root / "second.part"
            first.write_bytes(b"first-good")
            second.write_bytes(b"second-bad")
            release = root / "releases" / "candidate"

            def first_validator(path):
                if path.read_bytes() != b"first-good":
                    raise ValueError("bad first")

            def second_validator(path):
                if path.read_bytes() != b"second-good":
                    raise ValueError("bad second")

            with self.assertRaises(ValueError):
                h3_turbo._atomic_publish_validated_pair(
                    first,
                    second,
                    release,
                    first_name="first.bin",
                    second_name="second.bin",
                    validate_first=first_validator,
                    validate_second=second_validator,
                )
            self.assertFalse(release.exists())

            second.write_bytes(b"second-good")
            h3_turbo._atomic_publish_validated_pair(
                first,
                second,
                release,
                first_name="first.bin",
                second_name="second.bin",
                validate_first=first_validator,
                validate_second=second_validator,
            )
            self.assertEqual(sorted(path.name for path in release.iterdir()), ["first.bin", "second.bin"])

    def test_ref2va_visual_gate_record_requires_both_steps_and_all_criteria(self):
        criteria = {
            "reference_adherence": True,
            "motion": True,
            "coherence": True,
            "no_collapse": True,
        }
        with tempfile.TemporaryDirectory() as temporary:
            failed = h3_turbo.record_ref2va_live_validation(
                {"4": {"passed": True, "criteria": criteria}},
                root=temporary,
            )
            self.assertFalse(failed["passed"])
            passed = h3_turbo.record_ref2va_live_validation(
                {
                    "4": {"passed": True, "criteria": criteria},
                    "8": {"passed": True, "criteria": criteria},
                },
                root=temporary,
            )
            self.assertTrue(passed["passed"])
        self.assertNotIn("record_ref2va_live_validation", _source(_WGP))


class TestH3TurboSchedulingAndPolicy(unittest.TestCase):
    def test_four_uses_dual_clock_and_eight_remains_paired(self):
        four = h3_turbo.resolve_h3_turbo_schedule(4)
        self.assertEqual(
            (
                four.video_grid_points,
                four.audio_grid_points,
                four.video_scheduler_steps,
                four.audio_scheduler_steps,
                four.master_evaluations,
            ),
            (5, 9, 4, 8, 8),
        )
        self.assertEqual(four.video_timestep_indices, (0, 0, 1, 1, 2, 2, 3, 3))
        self.assertEqual(four.audio_timestep_indices, tuple(range(8)))
        self.assertEqual(four.video_advance_ticks, (1, 3, 5, 7))

        eight = h3_turbo.resolve_h3_turbo_schedule(8)
        self.assertEqual(
            (
                eight.video_grid_points,
                eight.audio_grid_points,
                eight.video_scheduler_steps,
                eight.audio_scheduler_steps,
                eight.master_evaluations,
            ),
            (9, 9, 8, 8, 8),
        )
        self.assertEqual(eight.video_timestep_indices, tuple(range(8)))
        self.assertEqual(eight.video_advance_ticks, tuple(range(8)))

        scheduler_source = _source(_SCHEDULER)
        self.assertIn("num_inference_steps - 1", scheduler_source)
        self.assertIn("self.timesteps = (1.0 - sigmas[:-1])", scheduler_source)
        main_source = _source(_MAIN)
        self.assertIn("resolve_h3_turbo_schedule(sampling_steps)", main_source)
        self.assertIn("video_scheduler_points = int(sampling_steps) + 1", main_source)
        self.assertIn("audio_scheduler_points = video_scheduler_points", main_source)
        self.assertIn("advance_video=index in video_advance_ticks", main_source)
        self.assertIn("len(timesteps) != len(audio_timesteps)", main_source)

    def test_schedule_identity_is_canonical_json_safe_and_validated(self):
        four = h3_turbo.turbo_schedule_identity(4)
        eight = h3_turbo.turbo_schedule_identity(8)
        self.assertEqual(json.loads(json.dumps(four, sort_keys=True)), four)
        self.assertEqual(
            four,
            {
                "profile_id": h3_turbo.H3_TURBO_PROFILE_ID,
                "algorithm_version": h3_turbo.H3_TURBO_SCHEDULE_ALGORITHM_VERSION,
                "authored_video_steps": 4,
                "video_evaluations": 4,
                "audio_evaluations": 8,
                "evaluation_alias_semantics": "scheduler_steps",
                "master_evaluations": 8,
                "transformer_evaluations": 8,
                "video_scheduler_steps": 4,
                "audio_scheduler_steps": 8,
            },
        )
        self.assertNotEqual(four, eight)
        self.assertEqual(h3_turbo.validate_turbo_schedule_identity(four), four)
        with self.assertRaises(h3_turbo.H3TurboCompatibilityError):
            h3_turbo.validate_turbo_schedule_identity({
                **four, "audio_evaluations": 4,
            })

    def test_base_and_ref2va_resolve_the_same_schedule(self):
        for base_model_type, model_def, authorized in (
            ("minimax_h3", _base_model_def(), False),
            (
                "minimax_h3_ref2va",
                {
                    "URLs": [h3_turbo.H3_TURBO_REF2VA_CHECKPOINT],
                    "minimax_h3_reference_mode": True,
                },
                True,
            ),
        ):
            with self.subTest(base_model_type=base_model_type):
                self.assertTrue(h3_turbo.validate_turbo_request(
                    base_model_type=base_model_type,
                    model_def=model_def,
                    custom_settings=_turbo_settings(),
                    authored_steps=4,
                    _h3_turbo_validation_authorized=authorized,
                ))
                self.assertEqual(
                    h3_turbo.resolve_h3_turbo_schedule(4).public_identity(),
                    h3_turbo.turbo_schedule_identity(4),
                )

    def test_incomplete_master_loop_resets_both_schedule_clocks(self):
        source = _source(_MAIN)
        loop = source[source.index("def _run_h3_master_schedule"):]
        loop = loop[:loop.index("AUDIO_LATENTS_MEAN =")]
        self.assertIn("finally:", loop)
        self.assertIn("if not completed:", loop)
        self.assertIn("reset()", loop)
        self.assertIn("self._ref2va_handoff_cache = None", source)

    def test_sol_dense_gate_counts_all_turbo_four_transformer_ticks(self):
        request = {
            "base_model_type": "minimax_h3",
            "model_def": _base_model_def(),
            "authored_steps": 4,
        }
        with self.assertRaisesRegex(
            h3_turbo.H3TurboCompatibilityError, "every effective transformer",
        ):
            h3_turbo.validate_turbo_request(
                **request,
                custom_settings={
                    **_turbo_settings("sol_attn"),
                    "h3_sol_dense_steps": 4,
                },
            )
        self.assertTrue(h3_turbo.validate_turbo_request(
            **request,
            custom_settings={
                **_turbo_settings("sol_attn"),
                "h3_sol_dense_steps": 8,
            },
        ))

    def test_structural_matrix_accepts_validated_variants_attention_and_stacking(self):
        self.assertTrue(
            h3_turbo.validate_turbo_request(
                base_model_type="minimax_h3",
                model_def=_base_model_def(),
                custom_settings=_turbo_settings(),
                authored_steps=4,
                activated_loras=[],
                loras_multipliers="",
                skip_steps_cache_type="",
            )
        )
        self.assertTrue(
            h3_turbo.validate_turbo_request(
                base_model_type="minimax_h3",
                model_def=_base_model_def(),
                custom_settings=_turbo_settings(),
                authored_steps=6,
                activated_loras=["shape-compatible-user.safetensors"],
                loras_multipliers="0.5",
                skip_steps_cache_type="",
            )
        )

        variants = [
            (
                "minimax_h3",
                {**_base_model_def(), "URLs": [h3_turbo.H3_TURBO_W4A8_CHECKPOINT]},
                _turbo_settings(),
            ),
            (
                "minimax_h3_ref2va",
                {
                    "URLs": [h3_turbo.H3_TURBO_REF2VA_CHECKPOINT],
                    "minimax_h3_reference_mode": True,
                },
                _turbo_settings(),
            ),
        ]
        for base_model_type, model_def, settings in variants:
            with self.subTest(model_def=model_def):
                self.assertTrue(
                    h3_turbo.validate_turbo_request(
                        base_model_type=base_model_type,
                        model_def=model_def,
                        custom_settings=settings,
                        authored_steps=8,
                        activated_loras=(
                            ["compatible-user-lora.safetensors"]
                            if base_model_type == "minimax_h3_ref2va"
                            else []
                        ),
                        loras_multipliers=("0.75" if base_model_type == "minimax_h3_ref2va" else ""),
                        skip_steps_cache_type="",
                        _h3_turbo_validation_authorized=(
                            base_model_type == "minimax_h3_ref2va"
                        ),
                    )
                )

        cases = [
            {"base_model_type": "wan"},
            {"model_def": {**_base_model_def(), "URLs": ["unknown-h3.safetensors"]}},
            {
                "custom_settings": {
                    **_turbo_settings("sol_attn"),
                    "h3_sol_dense_steps": 3,
                }
            },
            {"authored_steps": 3},
            {"authored_steps": 9},
            {"activated_loras": [h3_turbo.H3_TURBO_LORA_FILENAME]},
            {
                "model_def": {
                    **_base_model_def(),
                    "URLs": [h3_turbo.H3_TURBO_W4A8_CHECKPOINT],
                },
                "activated_loras": ["user.safetensors"],
            },
            {"skip_steps_cache_type": "tea"},
        ]
        defaults = {
            "base_model_type": "minimax_h3",
            "model_def": _base_model_def(),
            "custom_settings": _turbo_settings(),
            "authored_steps": 6,
            "activated_loras": [],
            "loras_multipliers": "",
            "skip_steps_cache_type": "",
        }
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(h3_turbo.H3TurboCompatibilityError):
                    h3_turbo.validate_turbo_request(**{**defaults, **overrides})

    def test_pinkcherry_is_incompatible_with_every_turbo_profile(self):
        model_def = {
            **_base_model_def(),
            "URLs": [h3_turbo.H3_TURBO_PINKCHERRY_CHECKPOINT],
        }
        for steps in (4, 8):
            with self.subTest(steps=steps), self.assertRaisesRegex(
                h3_turbo.H3TurboCompatibilityError,
                "incompatible with PinkCherry",
            ):
                h3_turbo.validate_turbo_request(
                    base_model_type="minimax_h3",
                    model_def=model_def,
                    custom_settings=_turbo_settings(),
                    authored_steps=steps,
                    activated_loras=[],
                    skip_steps_cache_type="",
                )

    def test_ref2va_is_gated_until_release_bound_visual_record_passes(self):
        model_def = {
            "URLs": [h3_turbo.H3_TURBO_REF2VA_CHECKPOINT],
            "minimax_h3_reference_mode": True,
        }
        criteria = {
            "reference_adherence": True,
            "motion": True,
            "coherence": True,
            "no_collapse": True,
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            h3_turbo,
            "_DEFAULT_MANAGED_ROOT",
            Path(temporary),
        ):
            with self.assertRaisesRegex(
                h3_turbo.H3TurboCompatibilityError,
                "visual gates",
            ):
                h3_turbo.validate_turbo_request(
                    base_model_type="minimax_h3_ref2va",
                    model_def=model_def,
                    custom_settings=_turbo_settings(),
                    authored_steps=4,
                )
            with self.assertRaisesRegex(
                h3_turbo.H3TurboCompatibilityError,
                "visual gates",
            ):
                h3_turbo.validate_turbo_request(
                    base_model_type="minimax_h3_ref2va",
                    model_def=model_def,
                    custom_settings=_turbo_settings(),
                    authored_steps=4,
                    _h3_turbo_validation_authorized="true",
                )
            matrix = h3_turbo.turbo_compatibility_matrix()
            self.assertEqual(
                matrix["variants"]["ref2va_fp8"]["status"],
                "live_visual_gate_required",
            )
            h3_turbo.record_ref2va_live_validation(
                {
                    "4": {"passed": True, "criteria": criteria},
                    "8": {"passed": True, "criteria": criteria},
                }
            )
            self.assertTrue(
                h3_turbo.validate_turbo_request(
                    base_model_type="minimax_h3_ref2va",
                    model_def=model_def,
                    custom_settings=_turbo_settings(),
                    authored_steps=4,
                )
            )
            matrix = h3_turbo.turbo_compatibility_matrix()
            self.assertEqual(matrix["variants"]["ref2va_fp8"]["status"], "ready")
            self.assertEqual(matrix["cache"]["tea"]["status"], "unsupported")

    def test_ref2va_custom_setting_cannot_replace_server_authorization(self):
        model_def = {
            "URLs": [h3_turbo.H3_TURBO_REF2VA_CHECKPOINT],
            "minimax_h3_reference_mode": True,
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            h3_turbo,
            "_DEFAULT_MANAGED_ROOT",
            Path(temporary),
        ):
            with self.assertRaisesRegex(
                h3_turbo.H3TurboCompatibilityError,
                "visual gates",
            ):
                h3_turbo.validate_turbo_request(
                    base_model_type="minimax_h3_ref2va",
                    model_def=model_def,
                    custom_settings={
                        **_turbo_settings(),
                        "h3_turbo_validation_mode": "synthetic_ref2va",
                    },
                    authored_steps=4,
                )
        self.assertNotIn(
            "h3_turbo_validation_mode",
            _source(_HANDLER),
        )

    def test_native_h3_is_not_rewritten_to_turbo(self):
        self.assertFalse(
            h3_turbo.validate_turbo_request(
                base_model_type="minimax_h3",
                model_def=_base_model_def(),
                custom_settings={"h3_attention_engine": "sol_attn"},
                authored_steps=20,
            )
        )
        self.assertFalse(h3_turbo.turbo_requested(None))

    def test_sage2_turbo_reports_release_bound_base_four_and_eight_validation(self):
        with mock.patch(
            "services.h3_acceleration.sage2_validation_status",
            return_value={"passed": True, "reason": None},
        ):
            matrix = h3_turbo.turbo_compatibility_matrix()
        self.assertEqual(
            matrix["attention"]["sage2"]["status"],
            "validated_base_draft_fast",
        )
        self.assertIn("W4A8 and Ref2VA remain unvalidated", matrix["attention"]["sage2"]["reason"])
        self.assertIn("PinkCherry is incompatible with Turbo", matrix["attention"]["sage2"]["reason"])
        self.assertEqual(matrix["variants"]["pinkcherry_int8"]["status"], "unsupported")
        self.assertIn("cold SDPA baseline is provenance-only", matrix["attention"]["sage2"]["reason"])
        for steps in (4, 8):
            self.assertTrue(h3_turbo.validate_turbo_request(
                base_model_type="minimax_h3",
                model_def=_base_model_def(),
                custom_settings=_turbo_settings("sage2"),
                authored_steps=steps,
                activated_loras=[],
            ))

    def test_sage2_turbo_rejects_every_unvalidated_checkpoint_variant(self):
        variants = (
            ("minimax_h3", h3_turbo.H3_TURBO_W4A8_CHECKPOINT, False),
            ("minimax_h3_ref2va", h3_turbo.H3_TURBO_REF2VA_CHECKPOINT, True),
        )
        with mock.patch.object(
            h3_turbo,
            "ref2va_live_validation_status",
            return_value={"passed": True, "reason": None},
        ):
            for base_model_type, checkpoint, reference_mode in variants:
                with self.subTest(checkpoint=checkpoint), self.assertRaisesRegex(
                    h3_turbo.H3TurboCompatibilityError,
                    "release-validated only for Base H3",
                ):
                    h3_turbo.validate_turbo_request(
                        base_model_type=base_model_type,
                        model_def={
                            "URLs": [checkpoint],
                            "minimax_h3_reference_mode": reference_mode,
                        },
                        custom_settings=_turbo_settings("sage2"),
                        authored_steps=8,
                    )


class TestH3TurboIntegrationSource(unittest.TestCase):
    def test_transformer_keeps_custom_weights_unregistered_and_clears_them(self):
        source = _source(_TRANSFORMER)
        self.assertIn("object.__setattr__(self, \"_h3_turbo_pending_adaln\"", source)
        self.assertIn("def preprocess_loras", source)
        self.assertIn("strip_and_capture_adaln", source)
        self.assertIn("def activate_h3_turbo", source)
        self.assertIn("def clear_h3_turbo", source)
        self.assertIn("_make_h3_turbo_residual_hook", source)
        self.assertIn("register_forward_hook", source)
        self.assertNotIn("register_buffer(\n            \"_h3_turbo", source)

    def test_wgp_wraps_generic_mmgp_lora_lifecycle(self):
        source = _source(_WGP)
        self.assertIn("prepare_h3_turbo_runtime(", source)
        self.assertIn("activate_h3_turbo_runtime(trans_lora)", source)
        self.assertGreaterEqual(source.count("clear_h3_turbo_runtime(trans_lora)"), 4)
        self.assertIn(
            'inputs.pop("_h3_turbo_validation_authorized", None)',
            source,
        )
        self.assertIn(
            'if "_h3_turbo_validation_authorized" in params:',
            source,
        )
        handler_source = _source(_HANDLER)
        self.assertIn(
            'inputs.get("_h3_turbo_validation_authorized") is True',
            handler_source,
        )
        self.assertIn(
            '_h3_turbo_validation_authorized is True',
            source,
        )
        runtime_source = _source(_MAIN)
        self.assertIn(
            '_kwargs.get("_h3_turbo_validation_authorized") is True',
            runtime_source,
        )
        self.assertIn("offload.load_loras_into_model(", source)
        self.assertIn("offload.unload_loras_from_model(trans_lora)", source)

    def test_handler_preserves_hidden_runtime_flag(self):
        source = _source(_HANDLER)
        self.assertIn('"h3_turbo_profile"', source)
        self.assertIn("validate_turbo_request(", source)

    def test_upstream_document_records_turbo_provenance(self):
        source = _source(_UPSTREAM)
        self.assertIn(h3_turbo.H3_TURBO_LORA_REVISION, source)
        self.assertIn(h3_turbo.H3_TURBO_NODE_COMMIT, source)
        self.assertIn(h3_turbo.H3_TURBO_LORA_SHA256, source)
        self.assertIn(h3_turbo.H3_TURBO_GRID_SHA256, source)


if __name__ == "__main__":
    unittest.main()
