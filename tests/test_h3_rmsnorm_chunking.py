"""CPU numerical and allocation-bound checks for the upstream H3 norm port."""
import unittest
from unittest.mock import patch

import torch

from app.models.minimax_h3 import transformer


class H3RMSNormChunkingTests(unittest.TestCase):
    def test_full_block_and_final_layer_match_native_norm_path(self):
        block = transformer.MiniMaxH3Block(
            8, 1, 8, 12, 2, 1e-6, torch.float32,
            adaln_dtype=torch.float32,
        ).eval()
        final = transformer.MiniMaxH3FinalLayer(
            8, 2, 2, 3, 1e-6, torch.float32,
            adaln_dtype=torch.float32,
        ).eval()
        data = torch.randn(1, 11, 8)
        curve = torch.randn(1, 2)
        runs = ((0, 11, 0),)
        rotary = (torch.ones(11, 6), torch.zeros(11, 6))
        with torch.no_grad(), patch.object(transformer, 'MINIMAX_H3_ACTIVATION_CHUNK_TOKENS', 3):
            with patch.object(transformer, '_rms_norm_in_chunks', side_effect=lambda norm, x: norm(x)):
                expected = final(block(data.clone(), curve, None, runs, rotary, None), curve, None, runs)
            actual = final(block(data.clone(), curve, None, runs, rotary, None), curve, None, runs)
        torch.testing.assert_close(actual, expected, rtol=0, atol=0)

    def test_native_parity_and_hooks_for_noncontiguous_batches(self):
        for dtype in (torch.float32, torch.float16, torch.bfloat16):
            with self.subTest(dtype=dtype):
                norm = torch.nn.RMSNorm(12, eps=1e-6, dtype=dtype)
                data = torch.randn(2, 23, 24, dtype=dtype)[..., ::2]
                before = data.clone()
                calls = []
                with torch.no_grad():
                    expected = norm(data)
                    hook = norm.register_forward_hook(
                        lambda module, args, result: calls.append(args[0].shape[-2])
                    )
                    try:
                        with patch.object(transformer, 'MINIMAX_H3_ACTIVATION_CHUNK_TOKENS', 7):
                            actual = transformer._rms_norm_in_chunks(norm, data)
                    finally:
                        hook.remove()
                torch.testing.assert_close(actual, expected, rtol=0, atol=0)
                torch.testing.assert_close(data, before, rtol=0, atol=0)
                self.assertEqual(calls, [7, 7, 7, 2])
                self.assertEqual(actual.dtype, expected.dtype)

    def test_training_keeps_native_autograd_and_one_module_call(self):
        norm = torch.nn.RMSNorm(4, dtype=torch.float32)
        data = torch.randn(2, 11, 4, requires_grad=True)
        expected = norm(data)
        expected_grad, = torch.autograd.grad(expected.square().sum(), data)
        with patch.object(norm, 'forward', wraps=norm.forward) as forward:
            with patch.object(transformer, 'MINIMAX_H3_ACTIVATION_CHUNK_TOKENS', 3):
                actual = transformer._rms_norm_in_chunks(norm, data)
            self.assertEqual(forward.call_count, 1)
        actual_grad, = torch.autograd.grad(actual.square().sum(), data)
        torch.testing.assert_close(actual_grad, expected_grad, rtol=0, atol=0)

    def test_short_empty_and_multiaxis_inputs_use_native_module(self):
        for shape, normalized in (((2, 3, 4), 4), ((2, 0, 4), 4), ((2, 9, 4), (9, 4))):
            with self.subTest(shape=shape), torch.no_grad():
                norm = torch.nn.RMSNorm(normalized)
                data = torch.randn(shape)
                with patch.object(norm, 'forward', wraps=norm.forward) as forward:
                    with patch.object(transformer, 'MINIMAX_H3_ACTIVATION_CHUNK_TOKENS', 7):
                        actual = transformer._rms_norm_in_chunks(norm, data)
                    self.assertEqual(forward.call_count, 1)
                torch.testing.assert_close(actual, norm(data), rtol=0, atol=0)


if __name__ == '__main__':
    unittest.main()
