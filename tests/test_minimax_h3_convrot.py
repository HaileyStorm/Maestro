from __future__ import annotations

import json
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
