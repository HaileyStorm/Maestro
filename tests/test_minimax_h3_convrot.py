from __future__ import annotations

import json
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import torch
    import torch.nn as nn
except ModuleNotFoundError as error:  # lightweight CI intentionally omits Torch
    raise unittest.SkipTest("Torch is required for H3 ConvRot runtime tests") from error

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from models.minimax_h3.convrot import (  # noqa: E402
    ConvRotInt8Linear,
    W4A8ConvRotLinear,
    adapt_int8_convrot_state_dict,
)


def _descriptor(**overrides) -> torch.Tensor:
    value = {
        "format": "int8_tensorwise",
        "convrot": True,
        "convrot_groupsize": 256,
        **overrides,
    }
    return torch.tensor(list(json.dumps(value).encode("utf-8")), dtype=torch.uint8)


class ConvRotAdapterTests(unittest.TestCase):
    def test_transformer_preserves_fractional_inputs_for_quantized_entry_projections(self):
        from models.minimax_h3.transformer import MiniMaxH3Transformer

        for kind in ("int8", "w4a8"):
            with self.subTest(kind=kind), torch.inference_mode():
                model = MiniMaxH3Transformer(
                    hidden_size=8, num_layers=1, token_refiner_layers=1,
                    num_attention_heads=1, attention_head_dim=8, ffn_dim=12,
                    video_channels=32, audio_channels=32, patch_size=(1, 1, 1),
                    text_dim=32, curve_grid=4, curve_dim=2, rope_freq_dim=1,
                    dtype=torch.float32,
                ).eval()
                model.adaln_t_table.zero_()
                names = ("video_patch_proj", "audio_patch_proj", "condition_proj")
                for name in names:
                    original = getattr(model, name)
                    original.weight.fill_(0.25)
                    original.bias.zero_()
                reference = copy.deepcopy(model)
                for name in names:
                    original = getattr(model, name)
                    if kind == "int8":
                        replacement = ConvRotInt8Linear(
                            32, 8, bias=True, output_dtype=torch.float32,
                            convrot=False,
                        )
                        replacement.weight.fill_(1)
                        replacement.weight_scale.fill_(0.25)
                    else:
                        replacement = W4A8ConvRotLinear(original, {
                            "layer.weight": torch.zeros((8, 16), dtype=torch.int8),
                            "layer.weight_s_rel": torch.ones((8, 2), dtype=torch.float8_e4m3fn),
                            "layer.weight_s_channel": torch.ones(8),
                            "layer.bias": torch.zeros(8),
                        }, "layer", output_dtype=torch.float32)
                    replacement.bias.zero_()
                    setattr(model, name, replacement)

                inputs = [torch.linspace(-0.75, 0.75, n * 32).reshape(1, n, 32)
                          for n in (3, 4, 2)]
                kwargs = dict(
                    hidden_states=inputs[0], audio_hidden_states=inputs[1],
                    encoder_hidden_states=inputs[2], timestep=torch.tensor([0.1, 0.4]),
                    timestep_indices=torch.tensor([0, 0, 1, 1, 1, 1, 0, 0, 0]),
                    token_tags=torch.tensor([1, 1, 2, 2, 2, 2, 0, 0, 0]),
                    position_ids=torch.zeros(9, 3, dtype=torch.float64),
                    video_indices=torch.tensor([6, 7, 8]),
                    audio_indices=torch.tensor([2, 3, 4, 5]),
                    text_indices=torch.tensor([0, 1]), return_dict=False,
                )
                expected = reference(**kwargs)
                seen = []

                def linear(value, weight, scale, *args, **options):
                    seen.append(value.clone())
                    # CPU oracle for this fixed fixture, not kernel acceptance.
                    dense = (weight.float() * scale if kind == "int8" else
                             torch.full((8, 32), 0.25))
                    return torch.nn.functional.linear(value.float(), dense).to(options["out_dtype"])

                with patch.dict(sys.modules, {"comfy_kitchen": SimpleNamespace(
                    int8_linear=linear, w4a8_int8_linear=linear,
                )}):
                    actual = model(**kwargs)
                self.assertEqual(len(seen), 3)
                for original, projected in zip(inputs, seen):
                    self.assertEqual(projected.dtype, torch.float32)
                    torch.testing.assert_close(projected, original, rtol=0, atol=0)
                for result, baseline in zip(actual, expected):
                    torch.testing.assert_close(result, baseline, rtol=0, atol=0)

    def test_projection_dtype_uses_declared_float_output_before_packed_storage(self):
        from models.minimax_h3.transformer import _weight_dtype

        for dtype in (torch.float16, torch.bfloat16, torch.float32):
            layer = ConvRotInt8Linear(4, 3, bias=False, output_dtype=dtype)
            self.assertEqual(_weight_dtype(layer, torch.float32), dtype)
        for dtype in (torch.int8, torch.uint8, torch.int32):
            layer = SimpleNamespace(weight=torch.ones(1, dtype=dtype))
            self.assertEqual(_weight_dtype(layer, torch.float32), torch.float32)
        dense = nn.Linear(4, 3, dtype=torch.float16)
        self.assertEqual(_weight_dtype(dense, torch.float32), torch.float16)

    def test_replaces_marked_linear_and_consumes_descriptor(self):
        model = nn.Sequential(nn.Linear(4, 3, bias=True, device="meta"))
        state = {
            "0.comfy_quant": _descriptor(),
            "0.weight": torch.empty((3, 4), dtype=torch.int8),
            "0.weight_scale": torch.empty((3, 1), dtype=torch.float32),
        }

        returned = adapt_int8_convrot_state_dict(
            model, state, output_dtype=torch.bfloat16,
        )

        self.assertIs(returned, state)
        self.assertNotIn("0.comfy_quant", state)
        self.assertIsInstance(model[0], ConvRotInt8Linear)
        self.assertEqual(model[0].weight.device.type, "meta")
        self.assertEqual(model[0].weight.dtype, torch.int8)
        self.assertEqual(tuple(model[0].weight_scale.shape), (3, 1))
        self.assertEqual(model[0].output_dtype, torch.bfloat16)

    def test_rejects_unknown_quantization_format(self):
        model = nn.Sequential(nn.Linear(4, 3))
        with self.assertRaisesRegex(ValueError, "Unsupported H3 quantization"):
            adapt_int8_convrot_state_dict(
                model,
                {"0.comfy_quant": _descriptor(format="asym_w4a8_int8")},
                output_dtype=torch.bfloat16,
            )

    def test_scaled_fp8_marker_is_consumed_without_convrot_rewrite(self):
        model = nn.Sequential(nn.Linear(4, 3, bias=False, device="meta"))
        original = model[0]
        state = {
            "0.comfy_quant": _descriptor(format="float8_e4m3fn", convrot=False),
            "0.weight": torch.empty((3, 4), dtype=torch.float8_e4m3fn),
            "0.weight_scale": torch.ones((), dtype=torch.float32),
        }

        adapt_int8_convrot_state_dict(model, state, output_dtype=torch.bfloat16)

        self.assertIs(model[0], original)
        self.assertNotIn("0.comfy_quant", state)
        self.assertEqual(state["0.weight"].dtype, torch.float8_e4m3fn)
        self.assertIn("0.weight_scale", state)

    def test_preflight_shape_failure_does_not_mutate_model_or_marker(self):
        model = nn.Sequential(nn.Linear(8, 3, bias=False, device="meta"))
        original = model[0]
        state = {
            "0.comfy_quant": _descriptor(),
            "0.weight": torch.empty((3, 4), dtype=torch.int8),
            "0.weight_scale": torch.empty((3, 1), dtype=torch.float32),
        }

        with self.assertRaisesRegex(ValueError, "expected \\(3, 8\\)"):
            adapt_int8_convrot_state_dict(model, state, output_dtype=torch.bfloat16)

        self.assertIs(model[0], original)
        self.assertIn("0.comfy_quant", state)

    def test_forward_passes_convrot_contract_to_comfy_kitchen(self):
        layer = ConvRotInt8Linear(
            4, 3, bias=True, output_dtype=torch.bfloat16,
        )
        observed = {}

        def fake_int8_linear(value, weight, scale, bias, **kwargs):
            observed.update(kwargs)
            return torch.zeros((*value.shape[:-1], weight.shape[0]), dtype=torch.bfloat16)

        fake_module = SimpleNamespace(int8_linear=fake_int8_linear)
        with patch.dict(sys.modules, {"comfy_kitchen": fake_module}):
            output = layer(torch.ones((2, 4), dtype=torch.bfloat16))

        self.assertEqual(tuple(output.shape), (2, 3))
        self.assertEqual(observed["out_dtype"], torch.bfloat16)
        self.assertTrue(observed["convrot"])
        self.assertEqual(observed["convrot_groupsize"], 256)

    def test_replaces_w4a8_linear_from_companion_tensor_names(self):
        model = nn.Sequential(nn.Linear(32, 4, bias=False, device="meta"))
        state = {
            "0.weight": torch.empty((4, 16), dtype=torch.int8),
            "0.weight_s_rel": torch.empty((4, 2), dtype=torch.float8_e4m3fn),
            "0.weight_s_channel": torch.empty((4,), dtype=torch.float32),
            "0.weight_codebook": torch.empty((16,), dtype=torch.float32),
        }
        adapt_int8_convrot_state_dict(model, state, output_dtype=torch.bfloat16)
        self.assertIsInstance(model[0], W4A8ConvRotLinear)
        self.assertEqual(tuple(model[0].weight.shape), (4, 16))
        self.assertEqual(tuple(model[0].weight_s_rel.shape), (4, 2))
        self.assertEqual(model[0].weight.device.type, "meta")

    def test_w4a8_forward_passes_grouped_codebook_contract(self):
        original = nn.Linear(32, 4, bias=False)
        state = {
            "layer.weight": torch.empty((4, 16), dtype=torch.int8),
            "layer.weight_s_rel": torch.empty((4, 2), dtype=torch.float8_e4m3fn),
            "layer.weight_s_channel": torch.empty((4,), dtype=torch.float32),
            "layer.weight_codebook": torch.empty((16,), dtype=torch.float32),
        }
        layer = W4A8ConvRotLinear(
            original, state, "layer", output_dtype=torch.bfloat16,
        )
        observed = {}

        def fake_w4a8(value, weight, s_rel, s_channel, **kwargs):
            observed.update(kwargs)
            return torch.zeros((*value.shape[:-1], 4), dtype=torch.bfloat16)

        fake_module = SimpleNamespace(w4a8_int8_linear=fake_w4a8)
        with patch.dict(sys.modules, {"comfy_kitchen": fake_module}):
            output = layer(torch.ones((2, 32), dtype=torch.bfloat16))
        self.assertEqual(tuple(output.shape), (2, 4))
        self.assertIs(observed["codebook"], layer.weight_codebook)
        self.assertEqual(observed["group_size"], 16)
        self.assertEqual(observed["convrot_groupsize"], 256)


if __name__ == "__main__":
    unittest.main()
