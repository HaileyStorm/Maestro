"""CPU contracts for native NVFP4 LoRA forwarding and kernel row padding."""
from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import torch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
from mmgp.offload import offload
from mmgp.quant_router import QLinearQuantoRouter, _load_with_qmodule

_ORIGINAL_HOOKS = (offload.hook_lora, offload._lora_generic_forward, offload._lora_linear_forward)
from shared.qtypes import nvfp4


class NVFP4NativeLoraTests(unittest.TestCase):
    def make_router(self, bias=True):
        router = QLinearQuantoRouter(64, 8, bias=bias, dtype=torch.float32,
                                    weights=nvfp4._NVFP4_QTYPE)
        state = {
            'weight': torch.full((8, 32), 0x22, dtype=torch.uint8),
            'weight_scale': torch.ones(128, 4, dtype=torch.float8_e4m3fn),
            'input_global_scale': torch.tensor(1.), 'alpha': torch.tensor(1.),
            'pre_quant_scale': torch.linspace(.5, 2, 64),
        }
        if bias:
            state['bias'] = torch.arange(8, dtype=torch.float32)
        missing, unexpected, errors = [], [], []
        _load_with_qmodule(router, nvfp4.QLinearNVFP4, state, '', {}, True,
                           missing, unexpected, errors)
        self.assertEqual((missing, unexpected, errors), ([], [], []))
        self.assertTrue(router.__dict__.get('_mm_requires_native_linear_forward'))
        return router

    def test_actual_mmgp_lora_path_preserves_scaled_base_and_factor_delta(self):
        for rank in (1, 3, 8):
            for bias in (False, True):
                with self.subTest(rank=rank, bias=bias), torch.no_grad():
                    router = self.make_router(bias)
                    x = torch.arange(128, dtype=torch.float32).reshape(2, 64) / 128
                    a = torch.ones(rank, 64) / 64
                    b = torch.ones(8, rank) / 8
                    model = types.SimpleNamespace(_loras_active_adapters=['a'], _loras_scaling={'a': .5})
                    data = {'a_GPU': [a, b, None, None, 1., {'type': 'lora'}]}
                    controller = object.__new__(offload)
                    expected = torch.nn.functional.linear(
                        x * router._nvfp4_pre_quant_scale,
                        router.weight.dequantize(), router.bias,
                    ) + .5 * (x @ a.T @ b.T)
                    original_bytes = router.weight._data.clone()
                    hooked = controller.hook_lora(router, model, 'test', {}, {}, 'projection')
                    router._mm_manager = controller
                    router._mm_lora_data.update(data)
                    self.assertEqual(hooked.func.__name__, '_mm_lora_linear_forward')
                    actual = hooked(x)
                    torch.testing.assert_close(router.weight._data, original_bytes)
                    torch.testing.assert_close(actual, expected)
                    self.assertTrue(actual.dtype.is_floating_point)
                    self.assertEqual(actual.device.type, 'cpu')

    def test_native_marker_is_an_instance_attribute_and_does_not_patch_mmgp(self):
        module = nvfp4.QLinearNVFP4(64, 8, dtype=torch.float32, weights=nvfp4._NVFP4_QTYPE)
        self.assertIs(module.__dict__.get('_mm_requires_native_linear_forward'), True)
        self.assertEqual(_ORIGINAL_HOOKS,
                         (offload.hook_lora, offload._lora_generic_forward, offload._lora_linear_forward))


class NVFP4KernelPaddingTests(unittest.TestCase):
    def weight(self):
        return types.SimpleNamespace(
            _data=torch.zeros(32, 16, dtype=torch.uint8),
            _scale=torch.ones(128, 4), _input_global_scale=torch.tensor(1.),
            _alpha=torch.tensor(1.), _layout='legacy', size=lambda dim: 32,
        )

    def test_lightx2v_pads_only_rows_and_restores_noncontiguous_batch_shape(self):
        for rows in (1, 50, 128, 129):
            with self.subTest(rows=rows):
                x = torch.arange(2 * rows * 32, dtype=torch.float32).reshape(2, 32, rows).transpose(1, 2)
                x = (x / 256).to(torch.bfloat16)
                bias = torch.arange(32, dtype=torch.bfloat16)
                padded = []
                def quantize(value, scale):
                    padded.append(value.clone())
                    return value, torch.ones(1)
                kernels = types.SimpleNamespace(
                    scaled_nvfp4_quant=quantize,
                    cutlass_scaled_nvfp4_mm=lambda q, *_args, **kw: q * 2 + kw['bias'],
                )
                with patch.object(nvfp4, '_lx_gemm', kernels), patch.object(nvfp4, '_nvfp4_note_kernel'):
                    actual = nvfp4._nvfp4_linear_cuda_lightx2v(x, self.weight(), bias)
                torch.testing.assert_close(actual, x * 2 + bias)
                self.assertEqual(actual.shape, x.shape)
                self.assertEqual(actual.dtype, x.dtype)
                self.assertEqual(padded[0].shape[0] % 128, 0)
                torch.testing.assert_close(padded[0][:2 * rows], x.reshape(-1, 32))
                self.assertEqual(torch.count_nonzero(padded[0][2 * rows:]).item(), 0)

    def test_empty_rows_do_not_dispatch_a_kernel(self):
        kernels = Mock()
        with patch.object(nvfp4, '_lx_gemm', kernels), patch.object(nvfp4, '_nvfp4_note_kernel') as note:
            result = nvfp4._nvfp4_linear_cuda_lightx2v(torch.empty(2, 0, 32), self.weight())
        self.assertEqual(tuple(result.shape), (2, 0, 32))
        self.assertEqual(result.dtype, torch.float32)
        note.assert_not_called()
        kernels.scaled_nvfp4_quant.assert_not_called()
        kernels.cutlass_scaled_nvfp4_mm.assert_not_called()

    def test_kernel_errors_propagate_without_silent_dequantization(self):
        for error in (RuntimeError('kernel failed'), torch.OutOfMemoryError('capacity exhausted')):
            with self.subTest(error=type(error).__name__):
                weight = Mock()
                with patch.object(nvfp4, '_nvfp4_can_use_kernel', return_value=True), \
                     patch.object(nvfp4, '_is_fake_tensor', return_value=False), \
                     patch.object(nvfp4, '_nvfp4_linear_cuda', side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        nvfp4._nvfp4_linear(torch.ones(1, 32), weight)
                self.assertIs(caught.exception, error)
                weight.dequantize.assert_not_called()


if __name__ == '__main__':
    unittest.main()
