"""Model-free parity, lifetime and failure checks for the H3 temporal sink."""

import math
import sys
import types
import unittest
import weakref
from pathlib import Path

import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from models.minimax_h3.video_vae import AutoencoderKLMiniMaxH3


def make_vae(*, drop=3):
    # Keep the real native temporal recipe, blend and registered hook wrapper;
    # replace learned clip inference with a context-dependent CPU transform.
    vae = object.__new__(AutoencoderKLMiniMaxH3)
    nn.Module.__init__(vae)
    vae.register_to_config(clip_length=17, token_drop=drop)
    vae.temporal_compression_ratio = 4
    vae.tokens_chunk_size = math.ceil(17 / 4)
    vae.token_overlap = (-drop) % vae.tokens_chunk_size
    vae.frame_pre_padding = (-17) % 4
    vae.frame_overlap = max(vae.token_overlap * 4 - vae.frame_pre_padding, 0)
    vae.use_slicing = False
    vae.calls = 0

    def decode(self, z):
        self.calls += 1
        return z.repeat_interleave(4, dim=2) + z.mean(dim=2, keepdim=True)

    vae._decode_clip = types.MethodType(decode, vae)
    return vae


class H3TemporalSinkTests(unittest.TestCase):
    def test_exact_native_eager_parity_including_all_padding_residues(self):
        for drop in (0, 3):
            for dtype in (torch.float32, torch.float16):
                for length in range(7, 63):
                    with self.subTest(drop=drop, dtype=dtype, length=length):
                        vae = make_vae(drop=drop)
                        z = torch.arange(3 * length * 2 * 4).reshape(1, 3, length, 2, 4).to(dtype) / 100
                        original = z.clone()
                        expected = vae.decode(z, return_dict=False)[0]
                        chunks = []
                        total = vae.decode_to_sink(z, lambda chunk, chunks=chunks: chunks.append(chunk.clone()))
                        actual = torch.cat(chunks, dim=2)
                        self.assertEqual(total, expected.shape[2])
                        torch.testing.assert_close(actual, expected, rtol=0, atol=0)
                        torch.testing.assert_close(z, original, rtol=0, atol=0)

    def test_registered_hook_and_all_work_stay_inside_callers_context(self):
        vae = make_vae()
        events = []

        class Hook:
            def pre_forward(self, module):
                self_module = module
                events.append(("load", self_module.calls))

        vae._hf_hook = Hook()
        native_decode = vae._decode_clip

        def decode(z):
            events.append(("decode", torch.is_inference_mode_enabled(), torch.is_autocast_enabled("cpu")))
            return native_decode(z)

        vae._decode_clip = decode

        def sink(chunk):
            events.append(("sink", torch.is_inference_mode_enabled(), torch.is_autocast_enabled("cpu")))

        with torch.inference_mode(), torch.autocast("cpu", dtype=torch.bfloat16):
            total = vae.decode_to_sink(torch.ones(1, 3, 12, 2, 4), sink)
            events.append(("returned", total))
        self.assertEqual(events[0], ("load", 0))
        self.assertEqual(events[-1], ("returned", 39))
        self.assertEqual(sum(event[0] == "load" for event in events), 1)
        self.assertTrue(all(event[1:] == (True, True) for event in events if event[0] in {"decode", "sink"}))

    def test_long_video_does_not_retain_every_full_decoder_buffer(self):
        vae = make_vae()
        native_decode = vae._decode_clip
        buffers = []
        maximum_live = maximum_full_frames = maximum_emitted = 0

        def decode(z):
            nonlocal maximum_live, maximum_full_frames
            result = native_decode(z)
            buffers.append(weakref.ref(result))
            maximum_live = max(maximum_live, sum(ref() is not None for ref in buffers))
            maximum_full_frames = max(maximum_full_frames, result.shape[2])
            return result

        vae._decode_clip = decode

        def sink(chunk):
            nonlocal maximum_emitted
            maximum_emitted = max(maximum_emitted, chunk.shape[2])

        total = vae.decode_to_sink(torch.ones(1, 3, 502, 2, 4), sink)
        self.assertEqual(total, 1705)
        self.assertGreater(vae.calls, 90)
        self.assertLessEqual(maximum_live, 3)
        self.assertLessEqual(maximum_full_frames, 28)
        self.assertLessEqual(maximum_emitted, 22)
        self.assertTrue(all(ref() is None for ref in buffers))

    def test_sink_failure_stops_decoding_and_preserves_input(self):
        vae = make_vae()
        z = torch.ones(1, 3, 42, 2, 4)
        original = z.clone()
        called = []

        def sink(chunk):
            called.append(chunk.shape[2])
            raise RuntimeError("encoder failed")

        with self.assertRaisesRegex(RuntimeError, "encoder failed"):
            vae.decode_to_sink(z, sink)
        self.assertEqual(len(called), 1)
        self.assertEqual(vae.calls, 2)
        torch.testing.assert_close(z, original, rtol=0, atol=0)

    def test_decoder_failure_does_not_emit_or_hide_partial_completion(self):
        vae = make_vae()
        native_decode = vae._decode_clip
        output = []

        def decode(z):
            if vae.calls == 1:
                raise RuntimeError("VAE failed")
            return native_decode(z)

        vae._decode_clip = decode
        with self.assertRaisesRegex(RuntimeError, "VAE failed"):
            vae.decode_to_sink(torch.ones(1, 3, 42, 2, 4), output.append)
        self.assertEqual(output, [])
        self.assertEqual(vae.calls, 1)

    def test_cancellation_before_decode_and_after_first_sink_stops_work(self):
        vae = make_vae()
        with self.assertRaises(InterruptedError):
            vae.decode_to_sink(torch.ones(1, 3, 42, 2, 4), lambda _: None, abort_check=lambda: True)
        self.assertEqual(vae.calls, 0)
        state = {"cancel": False}

        def sink(chunk):
            state["cancel"] = True

        with self.assertRaises(InterruptedError):
            vae.decode_to_sink(torch.ones(1, 3, 42, 2, 4), sink, abort_check=lambda: state["cancel"])
        self.assertEqual(vae.calls, 2)

    def test_lazy_or_async_consumers_are_rejected_without_leaking_work(self):
        async def async_sink(chunk):
            return None

        def lazy_sink(chunk):
            yield chunk

        for sink in (
            async_sink, lazy_sink, lambda chunk: async_sink(chunk), lambda chunk: lazy_sink(chunk),
            lambda chunk: iter((chunk,)), lambda chunk: 1,
        ):
            vae = make_vae()
            with self.subTest(sink=sink), self.assertRaisesRegex(TypeError, "synchronous"):
                vae.decode_to_sink(torch.ones(1, 3, 42, 2, 4), sink)
            self.assertLessEqual(vae.calls, 2)

    def test_invalid_batch_empty_or_integer_latents_fail_before_clip_decode(self):
        for z in (
            torch.ones(2, 3, 7, 2, 4), torch.ones(1, 3, 0, 2, 4),
            torch.ones(1, 3, 7, 2, 4, dtype=torch.uint8),
        ):
            vae = make_vae()
            with self.subTest(shape=z.shape, dtype=z.dtype), self.assertRaises(ValueError):
                vae.decode_to_sink(z, lambda _: None)
            self.assertEqual(vae.calls, 0)


if __name__ == "__main__":
    unittest.main()
