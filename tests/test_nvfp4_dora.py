"""CPU numerical contracts through the installed MMGP DoRA router."""
from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest

import torch

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from mmgp.offload import offload
from mmgp.quant_router import QLinearQuantoRouter, _load_with_qmodule
from shared.qtypes import nvfp4


def make_router(*, bias: bool) -> QLinearQuantoRouter:
    router = QLinearQuantoRouter(
        64, 8, bias=bias, dtype=torch.float32, weights=nvfp4._NVFP4_QTYPE
    )
    state = {
        "weight": torch.full((8, 32), 0x22, dtype=torch.uint8),
        "weight_scale": torch.ones(128, 4, dtype=torch.float8_e4m3fn),
        "input_global_scale": torch.tensor(1.0),
        "alpha": torch.tensor(1.0),
        "pre_quant_scale": torch.linspace(0.5, 2.0, 64),
    }
    if bias:
        state["bias"] = torch.linspace(-0.5, 0.5, 8)
    missing, unexpected, errors = [], [], []
    _load_with_qmodule(
        router, nvfp4.QLinearNVFP4, state, "", {}, True,
        missing, unexpected, errors,
    )
    if (missing, unexpected, errors) != ([], [], []):
        raise AssertionError((missing, unexpected, errors))
    assert router._mm_effective_weight_input_scale is router._nvfp4_pre_quant_scale
    return router


def adapter(
    rank: int,
    *,
    seed: int,
    dora: bool,
    alpha: float = 1.0,
    with_bias: bool = False,
) -> list[object]:
    a = ((torch.arange(rank * 64, dtype=torch.float32).reshape(rank, 64) + seed) % 13 - 6) / 64
    b = ((torch.arange(8 * rank, dtype=torch.float32).reshape(8, rank) + 2 * seed) % 11 - 5) / 32
    diff_b = torch.linspace(-0.2, 0.2, 8) if with_bias else None
    g_abs = torch.linspace(4.0, 7.5, 8).reshape(8, 1) if dora else None
    return [a, b, diff_b, g_abs, alpha, {"type": "lora"}]


def scaling(model: object, name: str, data: list[object]) -> float:
    return float(model._loras_scaling[name]) * float(data[4])


def reference_dora(
    router: QLinearQuantoRouter,
    model: object,
    data_by_key: dict[str, list[object]],
    x: torch.Tensor,
) -> torch.Tensor:
    # Match pinned MMGP 3.7.12 blend/order semantics exactly: scale only W0,
    # merge ordinary BA and bias first, then normalize/blend all effective DoRA.
    weight = router.weight.dequantize().clone()
    scale = router._mm_effective_weight_input_scale.to(weight)
    weight.mul_(scale[None, :])
    bias = None if router.bias is None else router.bias.clone()

    for name in model._loras_active_adapters:
        data = data_by_key.get(name + "_GPU")
        if data is None:
            continue
        a, b, diff_b, g_abs = data[:4]
        strength = scaling(model, name, data)
        if strength == 0 or g_abs is not None:
            continue
        if a is not None:
            weight.addmm_(b, a, alpha=strength)
        if diff_b is not None:
            # Preserve 3.7.12's no-bias mixed-path behavior: seed from diff_b,
            # then apply the same adapter's scaled delta in the common add.
            if bias is None:
                bias = diff_b.clone()
            bias.add_(diff_b, alpha=strength)

    eps = 1e-8
    g0 = torch.linalg.vector_norm(
        weight.float(), dim=1, keepdim=True, dtype=torch.float32
    ).clamp_min(eps)
    direction = weight.float() / g0
    direction_update = None
    magnitude = None
    dora_bias = None
    for name in model._loras_active_adapters:
        data = data_by_key.get(name + "_GPU")
        if data is None:
            continue
        a, b, diff_b, g_abs = data[:4]
        if g_abs is None:
            continue
        strength = scaling(model, name, data)
        if strength == 0:
            continue
        if a is not None and b is not None:
            update = (b @ a) / g0
            update.mul_(strength)
            direction_update = update if direction_update is None else direction_update.add(update)
        if magnitude is None:
            magnitude = g0.clone()
        magnitude.add_(g_abs - g0, alpha=strength)
        if diff_b is not None:
            update_bias = diff_b * strength
            dora_bias = update_bias if dora_bias is None else dora_bias.add(update_bias)

    if magnitude is None:
        magnitude = g0
    if direction_update is not None:
        direction.add_(direction_update)
        direction.div_(torch.linalg.vector_norm(
            direction, dim=1, keepdim=True, dtype=torch.float32
        ).clamp_min(eps))
    weight = direction * magnitude
    if dora_bias is not None:
        bias = dora_bias if bias is None else bias.add(dora_bias)
    return torch.nn.functional.linear(x, weight, bias)


def run_hook(
    *,
    bias: bool,
    active: list[str],
    strengths: dict[str, float],
    data_by_key: dict[str, list[object]],
) -> tuple[QLinearQuantoRouter, object, torch.Tensor, torch.Tensor, int]:
    router = make_router(bias=bias)
    x = torch.arange(2 * 3 * 64, dtype=torch.float32).reshape(2, 3, 64) / 384
    model = types.SimpleNamespace(
        _loras_active_adapters=active,
        _loras_scaling=strengths,
    )
    packed_before = router.weight._data.clone()
    controller = object.__new__(offload)
    hooked = controller.hook_lora(router, model, "test", {}, {}, "projection")
    router._mm_manager = controller
    router._mm_lora_data.update(data_by_key)
    native_calls = 0
    old_forward = router._mm_lora_old_forward

    def counted_native(*args: object, **kwargs: object) -> torch.Tensor:
        nonlocal native_calls
        native_calls += 1
        return old_forward(*args, **kwargs)

    router._mm_lora_old_forward = counted_native
    actual = hooked(x)
    torch.testing.assert_close(router.weight._data, packed_before, rtol=0, atol=0)
    if actual.device.type != "cpu":
        raise AssertionError(actual.device)
    return router, model, x, actual, native_calls


def record(name: str, actual: torch.Tensor, expected: torch.Tensor, native_calls: int) -> None:
    torch.testing.assert_close(actual, expected, rtol=2e-6, atol=2e-6)


class DoraInputScaleProtocolTests(unittest.TestCase):
    def tearDown(self) -> None:
        self.assertFalse(torch.cuda.is_initialized())

    def test_zero_alpha_dora_with_ordinary_lora_keeps_native_dispatch(self) -> None:
        for bias in (False, True):
            with self.subTest(bias=bias), torch.no_grad():
                data = {
                    "zero_GPU": adapter(2, seed=1, dora=True, alpha=0),
                    "ordinary_GPU": adapter(3, seed=3, dora=False),
                }
                router, _, x, actual, calls = run_hook(
                    bias=bias, active=["zero", "ordinary"],
                    strengths={"zero": 0.7, "ordinary": 0.4}, data_by_key=data,
                )
                expected = torch.nn.functional.linear(
                    x * router._nvfp4_pre_quant_scale,
                    router.weight.dequantize(), router.bias,
                ) + 0.4 * (x @ data["ordinary_GPU"][0].T @ data["ordinary_GPU"][1].T)
                self.assertEqual(calls, 1)
                torch.testing.assert_close(actual, expected)

    def test_malformed_input_scale_is_rejected_before_dora_merge(self) -> None:
        for scale, error in ((1.0, TypeError), (torch.ones(1, 64), ValueError),
                             (torch.ones(63), ValueError),
                             (torch.full((64,), float("nan")), ValueError),
                             (torch.full((64,), float("inf")), ValueError),
                             (torch.full((64,), 1e300, dtype=torch.float64), ValueError)):
            with self.subTest(scale_type=type(scale)), torch.no_grad():
                router = make_router(bias=False)
                router._mm_effective_weight_input_scale = scale
                packed = router.weight._data.clone()
                model = types.SimpleNamespace(
                    _loras_active_adapters=["dora"], _loras_scaling={"dora": 0.5},
                )
                controller = object.__new__(offload)
                hooked = controller.hook_lora(router, model, "test", {}, {}, "projection")
                router._mm_manager = controller
                router._mm_lora_data["dora_GPU"] = adapter(2, seed=1, dora=True)
                with self.assertRaisesRegex(error, "_mm_effective_weight_input_scale"):
                    hooked(torch.ones(1, 64))
                torch.testing.assert_close(router.weight._data, packed, rtol=0, atol=0)

    def test_unscaled_linear_without_protocol_keeps_dora_math(self) -> None:
        with torch.no_grad():
            linear = torch.nn.Linear(4, 3, bias=True)
            linear.weight.copy_(torch.arange(12).reshape(3, 4) / 12 + 0.1)
            linear.bias.copy_(torch.arange(3) / 10)
            original = linear.weight.clone()
            a, b = torch.ones(1, 4) / 8, torch.ones(3, 1) / 4
            magnitude = torch.tensor([[1.0], [2.0], [3.0]])
            strength = -0.25
            model = types.SimpleNamespace(
                _loras_active_adapters=["dora"], _loras_scaling={"dora": strength},
            )
            controller = object.__new__(offload)
            hooked = controller.hook_lora(linear, model, "test", {}, {}, "projection")
            linear._mm_manager = controller
            linear._mm_lora_data["dora_GPU"] = [a, b, None, magnitude, 1., {"type": "lora"}]
            x = torch.arange(8).reshape(2, 4).float() / 8
            direction = original + strength * (b @ a)
            blended_magnitude = (1 - strength) * original.norm(dim=1, keepdim=True) + strength * magnitude
            expected_weight = direction / direction.norm(dim=1, keepdim=True) * blended_magnitude
            torch.testing.assert_close(hooked(x), torch.nn.functional.linear(x, expected_weight, linear.bias))
            torch.testing.assert_close(linear.weight, original, rtol=0, atol=0)

    def test_active_dora_with_lokr_fails_instead_of_dropping_dora(self) -> None:
        with torch.no_grad():
            router = make_router(bias=False)
            model = types.SimpleNamespace(
                _loras_active_adapters=["dora", "lokr"],
                _loras_scaling={"dora": 0.5, "lokr": 0.2},
            )
            controller = object.__new__(offload)
            hooked = controller.hook_lora(router, model, "test", {}, {}, "projection")
            router._mm_manager = controller
            router._mm_lora_data.update({
                "dora_GPU": adapter(2, seed=1, dora=True),
                "lokr_GPU": [torch.ones(2, 2), torch.ones(4, 32), None, None, 1., {"type": "lokr"}],
            })
            with self.assertRaisesRegex(ValueError, "DoRA.*LoKr|LoKr.*DoRA"):
                hooked(torch.ones(1, 64))
            model._loras_scaling["dora"] = 0
            expected = router._mm_lora_old_forward(torch.ones(1, 64)) + 0.2 * torch.ones(1, 8) * 64
            torch.testing.assert_close(hooked(torch.ones(1, 64)), expected)
            model._loras_scaling.update(dora=0.5, lokr=0)
            actual = hooked(torch.ones(1, 64))
            model._loras_active_adapters = ["dora"]
            torch.testing.assert_close(actual, hooked(torch.ones(1, 64)))

    def test_zero_strength_dora_keeps_native_dispatch(self) -> None:
        for bias in (False, True):
            with self.subTest(bias=bias), torch.no_grad():
                data = {"zero_GPU": adapter(2, seed=1, dora=True)}
                router, _model, x, actual, calls = run_hook(
                    bias=bias, active=["zero"], strengths={"zero": 0.0}, data_by_key=data
                )
                base_bias = router.bias
                expected = torch.nn.functional.linear(
                    x * router._nvfp4_pre_quant_scale,
                    router.weight.dequantize(), base_bias,
                )
                self.assertEqual(calls, 1)
                record(f"zero_dora_bias_{bias}", actual, expected, calls)

    def test_nonzero_dora_uses_scaled_materialized_base(self) -> None:
        for bias in (False, True):
            with self.subTest(bias=bias), torch.no_grad():
                data = {"dora_GPU": adapter(2, seed=2, dora=True, with_bias=True)}
                router, model, x, actual, calls = run_hook(
                    bias=bias, active=["dora"], strengths={"dora": 0.35}, data_by_key=data
                )
                self.assertEqual(calls, 0)
                record(
                    f"nonzero_dora_bias_{bias}", actual,
                    reference_dora(router, model, data, x), calls,
                )

    def test_mixed_ranks_and_active_order_match_pinned_blend(self) -> None:
        data = {
            "ordinary_r1_GPU": adapter(1, seed=3, dora=False, alpha=0.5, with_bias=True),
            "dora_r2_GPU": adapter(2, seed=4, dora=True, alpha=0.75, with_bias=True),
            "ordinary_r3_GPU": adapter(3, seed=5, dora=False, alpha=0.25),
            "dora_r4_GPU": adapter(4, seed=6, dora=True, alpha=0.5),
        }
        strengths = {
            "ordinary_r1": 0.4, "dora_r2": 0.3,
            "ordinary_r3": -0.2, "dora_r4": 0.15,
        }
        order = ["ordinary_r1", "dora_r2", "ordinary_r3", "dora_r4"]
        for bias in (False, True):
            for active in (order, list(reversed(order))):
                with self.subTest(bias=bias, active=active), torch.no_grad():
                    router, model, x, actual, calls = run_hook(
                        bias=bias, active=active, strengths=strengths, data_by_key=data
                    )
                    self.assertEqual(calls, 0)
                    record(
                        f"mixed_bias_{bias}_{active[0]}", actual,
                        reference_dora(router, model, data, x), calls,
                    )

    def test_ordinary_lora_path_is_unchanged(self) -> None:
        for bias in (False, True):
            with self.subTest(bias=bias), torch.no_grad():
                data = {
                    "ordinary_GPU": adapter(3, seed=8, dora=False, alpha=0.5, with_bias=True),
                }
                strengths = {"ordinary": 0.4}
                router, model, x, actual, calls = run_hook(
                    bias=bias, active=["ordinary"],
                    strengths=strengths, data_by_key=data,
                )
                expected = torch.nn.functional.linear(
                    x * router._nvfp4_pre_quant_scale,
                    router.weight.dequantize(), router.bias,
                )
                ordinary = data["ordinary_GPU"]
                strength = scaling(model, "ordinary", ordinary)
                expected.add_(torch.nn.functional.linear(
                    x, ordinary[1] @ ordinary[0], ordinary[2]
                ), alpha=strength)
                self.assertEqual(calls, 1)
                record(f"ordinary_unchanged_bias_{bias}", actual, expected, calls)



if __name__ == "__main__":
    unittest.main()
