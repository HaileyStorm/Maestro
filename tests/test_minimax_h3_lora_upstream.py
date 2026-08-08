"""CPU-only regressions for the selective Wan2GP H3 LoRA compatibility port."""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import torch
    from safetensors.torch import load_file
except ModuleNotFoundError as error:  # lightweight CI intentionally omits Torch
    raise unittest.SkipTest("Torch and safetensors are required for H3 LoRA tests") from error


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from models.minimax_h3.lora_affine import (
    LoraModuleSpec,
    convert_adaln_loras,
    normalize_h3_lora_state_dict,
)
from models.minimax_h3.transformer import MiniMaxH3Transformer

MAPS = APP / "models" / "minimax_h3" / "lora_affine_maps"


def _affine_package(architecture: str, width: int):
    package_width = 8 if width == 4 else width
    package = load_file(str(MAPS / f"{architecture}_rank{package_width}.sft"), device="cpu")
    table = package["adaln_t_table"].float()
    affine = package["adaln_affine_map"].float()
    if width == 4:
        table = table[:, :4]
        affine = torch.cat((affine[:4], affine[-1:]))
    return table, affine


def _tiny_transformer() -> MiniMaxH3Transformer:
    return MiniMaxH3Transformer(
        hidden_size=8,
        num_layers=1,
        token_refiner_layers=1,
        num_attention_heads=2,
        attention_head_dim=4,
        ffn_dim=12,
        video_channels=2,
        audio_channels=2,
        text_dim=10,
        curve_grid=1025,
        curve_dim=8,
        rope_freq_dim=2,
        dtype=torch.float32,
    )


def _specs(model: MiniMaxH3Transformer):
    return model._ordinary_lora_module_specs()


def _pair(module: str, in_features: int, out_features: int, rank: int = 2):
    return {
        module + ".lora_A.weight": torch.arange(rank * in_features, dtype=torch.float32).reshape(rank, in_features),
        module + ".lora_B.weight": torch.arange(out_features * rank, dtype=torch.float32).reshape(out_features, rank),
    }


class MiniMaxH3OrdinaryLoraTests(unittest.TestCase):
    def setUp(self):
        self.model = _tiny_transformer()
        self.specs = _specs(self.model)

    def normalize(self, state, model_type="minimax_h3", target_table=None, specs=None):
        return normalize_h3_lora_state_dict(
            model_type,
            state,
            target_table=target_table,
            module_specs=self.specs if specs is None else specs,
        )

    def test_diffusers_peft_qkv_fuses_and_preserves_each_alpha(self):
        state = {}
        for index, projection in enumerate(("q", "k", "v"), start=1):
            prefix = f"base_model.model.transformer.transformer_blocks.0.attn.to_{projection}"
            state[prefix + ".lora_A.default.weight"] = torch.full((2, 8), float(index))
            state[prefix + ".lora_B.default.weight"] = torch.full((8, 2), float(index * 10))
            state[prefix + ".alpha"] = torch.tensor(float(index * 2))

        converted = self.normalize(state)

        self.assertEqual(
            set(converted),
            {
                "blocks.0.attn.qkv_proj.lora_A.weight",
                "blocks.0.attn.qkv_proj.lora_B.weight",
            },
        )
        down = converted["blocks.0.attn.qkv_proj.lora_A.weight"]
        up = converted["blocks.0.attn.qkv_proj.lora_B.weight"]
        self.assertEqual(tuple(down.shape), (6, 8))
        self.assertEqual(tuple(up.shape), (24, 6))
        expected = torch.block_diag(
            torch.full((8, 2), 10.0),
            torch.full((8, 2), 40.0),
            torch.full((8, 2), 90.0),
        )
        torch.testing.assert_close(up, expected)

    def test_diffusers_fc1_reverses_only_the_up_factor_rows(self):
        prefix = "transformer_blocks.0.ff.net.0.proj"
        down = torch.arange(16, dtype=torch.float32).reshape(2, 8)
        up = torch.arange(48, dtype=torch.float32).reshape(24, 2)
        converted = self.normalize(
            {
                prefix + ".lora_A.default.weight": down,
                prefix + ".lora_B.default.weight": up,
            }
        )
        torch.testing.assert_close(converted["blocks.0.mlp.fc1.lora_A.weight"], down)
        torch.testing.assert_close(
            converted["blocks.0.mlp.fc1.lora_B.weight"],
            torch.cat(up.chunk(2, dim=0)[::-1], dim=0),
        )

    def test_kohya_flattened_and_dotted_factor_names_are_supported(self):
        converted = self.normalize(
            {
                "lora_unet_transformer_blocks_0_attn_to_out_0.lora_down.weight": torch.ones((2, 8)),
                "lora_unet_transformer_blocks_0_attn_to_out_0.lora_up.weight": torch.ones((8, 2)),
            }
        )
        self.assertEqual(
            set(converted),
            {
                "blocks.0.attn.out_proj.lora_A.weight",
                "blocks.0.attn.out_proj.lora_B.weight",
            },
        )

        dotted = self.normalize(
            {
                "blocks.0.mlp.fc2.lora.A.default.weight": torch.ones((2, 12)),
                "blocks.0.mlp.fc2.lora.B.default.weight": torch.ones((8, 2)),
            }
        )
        self.assertIn("blocks.0.mlp.fc2.lora_A.weight", dotted)
        self.assertIn("blocks.0.mlp.fc2.lora_B.weight", dotted)

        native_root = self.normalize(
            {
                "lora_unet_blocks_0_attn_to_out_0.lora_down.weight": torch.ones((2, 8)),
                "lora_unet_blocks_0_attn_to_out_0.lora_up.weight": torch.ones((8, 2)),
            }
        )
        self.assertIn("blocks.0.attn.out_proj.lora_A.weight", native_root)

    def test_unknown_or_orphan_or_mixed_or_colliding_states_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown module"):
            self.normalize(_pair("blocks.99.mlp.fc2", 12, 8))
        with self.assertRaisesRegex(ValueError, "missing factor B"):
            self.normalize({"blocks.0.mlp.fc2.lora_A.weight": torch.ones((2, 12))})
        with self.assertRaisesRegex(ValueError, "alpha has no factor pair"):
            self.normalize({"blocks.0.mlp.fc2.alpha": torch.tensor(2.0)})
        with self.assertRaisesRegex(ValueError, "mixes factor naming dialects"):
            self.normalize(
                {
                    "blocks.0.mlp.fc2.lora_A.weight": torch.ones((2, 12)),
                    "blocks.0.mlp.fc2.lora_B.default.weight": torch.ones((8, 2)),
                }
            )
        with self.assertRaisesRegex(ValueError, "mixes path naming dialects"):
            mixed_paths = _pair("blocks.0.mlp.fc2", 12, 8)
            mixed_paths.update(
                {
                    "token_refiner.refiner_blocks.0.ff.net.2.lora_A.weight": torch.ones((2, 12)),
                    "token_refiner.refiner_blocks.0.ff.net.2.lora_B.weight": torch.ones((8, 2)),
                }
            )
            self.normalize(mixed_paths)
        with self.assertRaisesRegex(ValueError, "collide"):
            self.normalize(
                {
                    "blocks.0.mlp.fc2.lora_A.weight": torch.ones((2, 12)),
                    "transformer.blocks.0.mlp.fc2.lora_A.weight": torch.ones((2, 12)),
                    "blocks.0.mlp.fc2.lora_B.weight": torch.ones((8, 2)),
                }
            )

    def test_nonfloating_nonfinite_and_overflowed_states_fail_closed(self):
        module = "blocks.0.mlp.fc2"
        with self.assertRaisesRegex(ValueError, "floating-point"):
            self.normalize(
                {
                    module + ".lora_A.weight": torch.ones((2, 12), dtype=torch.int16),
                    module + ".lora_B.weight": torch.ones((8, 2)),
                }
            )
        for suffix, value in (
            (".lora_A.weight", float("nan")),
            (".lora_B.weight", float("inf")),
            (".diff_b", float("-inf")),
        ):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "finite"):
                state = _pair(module, 12, 8)
                if suffix == ".diff_b":
                    state[module + suffix] = torch.full((8,), value)
                else:
                    state[module + suffix].flatten()[0] = value
                self.normalize(state)

        state = {}
        for projection in ("q", "k", "v"):
            prefix = f"transformer_blocks.0.attn.to_{projection}"
            state[prefix + ".lora_A.weight"] = torch.ones((1, 8), dtype=torch.float16)
            state[prefix + ".lora_B.weight"] = torch.full((8, 1), 65504.0, dtype=torch.float16)
            state[prefix + ".alpha"] = torch.tensor(65504.0)
        with self.assertRaisesRegex(ValueError, "finite"):
            self.normalize(state)

        adaln_module = "blocks.0.adaln_proj.linear"
        adaln_state = _pair(
            adaln_module,
            4,
            self.specs[adaln_module].out_features,
        )
        adaln_state[adaln_module + ".diff_b"] = torch.ones(
            self.specs[adaln_module].out_features,
            dtype=torch.int16,
        )
        with self.assertRaisesRegex(ValueError, "floating-point"):
            self.normalize(
                adaln_state,
                target_table=_affine_package("fl2va", 8)[0],
            )

    def test_partial_qkv_and_invalid_fc1_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "complete Q/K/V"):
            self.normalize(
                {
                    "transformer_blocks.0.attn.to_q.lora_A.weight": torch.ones((2, 8)),
                    "transformer_blocks.0.attn.to_q.lora_B.weight": torch.ones((8, 2)),
                }
            )
        with self.assertRaisesRegex(ValueError, "FC1"):
            self.normalize(
                {
                    "transformer_blocks.0.ff.net.0.proj.lora_A.weight": torch.ones((2, 8)),
                    "transformer_blocks.0.ff.net.0.proj.lora_B.weight": torch.ones((23, 2)),
                }
            )

    def test_explicit_variant_map_accepts_maestro_names_and_rejects_guesses(self):
        state = _pair("blocks.0.mlp.fc2", 12, 8)
        for model_type in (
            "minimax_h3",
            "minimax_h3_fl2va",
            "minimax_h3_fl2va_pruned",
            "minimax_h3_w4a8_fl2va",
            "minimax_h3_pinkcherry_fl2va",
            "minimax_h3_ref2va",
            "minimax_h3_ref2va_pruned",
        ):
            with self.subTest(model_type=model_type):
                self.normalize(dict(state), model_type=model_type)
        with self.assertRaisesRegex(ValueError, "Unsupported MiniMax H3 architecture"):
            self.normalize(dict(state), model_type="some_minimax_h3_fl2va_filename")

    def test_all_adaln_widths_convert_in_both_directions(self):
        module = "blocks.0.adaln_proj.linear"
        out_features = self.specs[module].out_features
        widths = (4, 8, 64, 2688)
        for architecture, model_type in (
            ("fl2va", "minimax_h3"),
            ("ref2va", "minimax_h3_ref2va"),
        ):
            for target_width in widths:
                target_table = (
                    None if target_width == 2688 else _affine_package(architecture, target_width)[0]
                )
                target_specs = dict(self.specs)
                target_specs[module] = LoraModuleSpec(out_features, target_width, True)
                for source_width in widths:
                    with self.subTest(
                        architecture=architecture,
                        source=source_width,
                        target=target_width,
                    ):
                        converted = self.normalize(
                            _pair(module, source_width, out_features),
                            model_type=model_type,
                            target_table=target_table,
                            specs=target_specs,
                        )
                        down = converted[module + ".lora_A.weight"]
                        self.assertEqual(down.shape[1], target_width)
                        self.assertTrue(bool(torch.isfinite(down).all()))
                        self.assertTrue(
                            bool(torch.isfinite(converted[module + ".lora_B.weight"]).all())
                        )
                        if source_width != target_width:
                            bias = converted[module + ".diff_b"]
                            self.assertEqual(bias.shape, (out_features,))
                            self.assertTrue(bool(torch.isfinite(bias).all()))

    def test_affine_conversion_preserves_canonical_adaln_delta(self):
        module = "blocks.0.adaln_proj.linear"
        rank, out_features = 2, 5
        up = torch.linspace(-0.3, 0.3, out_features * rank).reshape(out_features, rank)
        for architecture, model_type in (
            ("fl2va", "minimax_h3"),
            ("ref2va", "minimax_h3_ref2va"),
        ):
            for width in (4, 8, 64):
                table, affine = _affine_package(architecture, width)
                sample_rows = torch.tensor([0, table.shape[0] // 3, table.shape[0] - 1])
                compact = table.index_select(0, sample_rows)
                full = torch.cat((compact, torch.ones((compact.shape[0], 1))), dim=1) @ affine

                full_down = torch.linspace(-0.02, 0.02, rank * 2688).reshape(rank, 2688)
                state = {
                    module + ".lora_A.weight": full_down.clone(),
                    module + ".lora_B.weight": up.clone(),
                }
                convert_adaln_loras(model_type, state, table)
                expected = (full @ full_down.T) @ up.T
                actual = (
                    compact @ state[module + ".lora_A.weight"].T
                ) @ up.T + state[module + ".diff_b"]
                torch.testing.assert_close(actual, expected, rtol=4e-4, atol=4e-4)

                compact_down = torch.linspace(-0.2, 0.2, rank * width).reshape(rank, width)
                reverse = {
                    module + ".lora_A.weight": compact_down.clone(),
                    module + ".lora_B.weight": up.clone(),
                }
                convert_adaln_loras(model_type, reverse, None)
                expected_reverse = (compact @ compact_down.T) @ up.T
                actual_reverse = (
                    full @ reverse[module + ".lora_A.weight"].T
                ) @ up.T + reverse[module + ".diff_b"]
                torch.testing.assert_close(
                    actual_reverse,
                    expected_reverse,
                    rtol=4e-4,
                    atol=4e-4,
                )

    def test_mixed_adaln_source_widths_fail_closed(self):
        fl8 = load_file(str(MAPS / "fl2va_rank8.sft"), device="cpu")["adaln_t_table"]
        first = "blocks.0.adaln_proj.linear"
        second = "final_layer.adaln_proj.linear"
        state = {}
        state.update(_pair(first, 4, self.specs[first].out_features))
        state.update(_pair(second, 8, self.specs[second].out_features))
        with self.assertRaisesRegex(ValueError, "mixes AdaLN input widths"):
            self.normalize(state, target_table=fl8)

        unsupported_specs = dict(self.specs)
        unsupported_specs[first] = LoraModuleSpec(self.specs[first].out_features, 16, True)
        with self.assertRaisesRegex(ValueError, "Unsupported MiniMax H3 AdaLN LoRA conversion"):
            self.normalize(
                _pair(first, 16, self.specs[first].out_features),
                target_table=torch.zeros((17, 16)),
                specs=unsupported_specs,
            )

        with self.assertRaisesRegex(ValueError, "rank-deficient|finite"):
            self.normalize(
                _pair(first, 8, self.specs[first].out_features),
                target_table=torch.zeros((17, 8)),
            )

    def test_managed_turbo_state_reaches_exact_branch_before_normalization(self):
        managed = {"managed.nonordinary.tensor": torch.ones((1,))}
        object.__setattr__(self.model, "_h3_turbo_prepared", True)
        object.__setattr__(self.model, "_h3_turbo_managed_lora_index", 0)
        observed = []

        def validate(value):
            observed.append(value)

        with (
            patch("services.h3_turbo.validate_runtime_state_dict", side_effect=validate),
            patch("services.h3_turbo.strip_and_capture_adaln", return_value={}),
        ):
            returned = self.model.preprocess_loras("minimax_h3", managed)
        self.assertIs(returned, managed)
        self.assertEqual(observed, [managed])
        self.assertTrue(self.model._h3_turbo_managed_seen)

    def test_ordinary_adapter_before_managed_index_is_normalized_independently(self):
        object.__setattr__(self.model, "_h3_turbo_prepared", True)
        object.__setattr__(self.model, "_h3_turbo_managed_lora_index", 1)
        ordinary = {
            "transformer_blocks.0.attn.to_out.0.lora_A.default.weight": torch.ones((2, 8)),
            "transformer_blocks.0.attn.to_out.0.lora_B.default.weight": torch.ones((8, 2)),
        }
        converted = self.model.preprocess_loras("minimax_h3", ordinary)
        self.assertIn("blocks.0.attn.out_proj.lora_A.weight", converted)

        managed = {"managed.nonordinary.tensor": torch.ones((1,))}
        with (
            patch("services.h3_turbo.validate_runtime_state_dict"),
            patch("services.h3_turbo.strip_and_capture_adaln", return_value={}),
        ):
            returned = self.model.preprocess_loras("minimax_h3", managed)
        self.assertIs(returned, managed)


class MiniMaxH3AffineProvenanceTests(unittest.TestCase):
    def test_vendored_affine_packages_are_byte_exact(self):
        expected = {
            "fl2va_rank8.sft": (130072, "a42778e02ab2708dc70e23837ec4d3061b44f938c940decbc7a5b91f2c27c59e"),
            "ref2va_rank8.sft": (130072, "7179899e59fce9c36038cd6c0c57edaced0032c769c436cef234b07bf809381f"),
            "fl2va_rank64.sft": (955640, "df40361cba88c9d6cf300a90d506ed349b349bd23babb6b94f15ab2df1b00f6e"),
            "ref2va_rank64.sft": (955640, "4b661b03438d5d5fcc86be3dad2d9dbbd129720f089f8e94914b369eee198cee"),
        }
        for name, (size, digest) in expected.items():
            with self.subTest(name=name):
                payload = (MAPS / name).read_bytes()
                self.assertEqual(len(payload), size)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

    def test_upstream_commits_and_scope_are_documented(self):
        provenance = (APP / "models" / "minimax_h3" / "UPSTREAM.md").read_text(encoding="utf-8")
        for commit in (
            "55c508821dc9df1a635dd342be91d94d7c7656c3",
            "de258bf136d701a96d52e63b3984355b429eaa1c",
            "6a9f5a62d063e4dab96c75d960650a5be77ff83b",
        ):
            self.assertIn(commit, provenance)
        self.assertIn("does not import those commits' model profiles", provenance)


if __name__ == "__main__":
    unittest.main()
