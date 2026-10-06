"""Original donor equations independently check Control weight adaptation.

This fixture uses separate Q/K/V, Diffusers [value, gate] SwiGLU, mixed
timestep/modalities, padding attention, and nonzero skips. It detects a
plausible but wrong key-only checkpoint conversion and packed-buffer aliasing.
"""

from pathlib import Path
import copy
import sys
import unittest
from unittest.mock import patch

try:
    import torch
    from torch.nn import functional as F
except ModuleNotFoundError as error:
    raise unittest.SkipTest("Torch is required for CPU H3 Control checks") from error

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from models.minimax_h3.control import MiniMaxH3ControlBranch, add_control_hint
from models.minimax_h3.transformer import MiniMaxH3Transformer


def donor_weights(dtype, *, heads=1, head_dim=8):
    generator = torch.Generator().manual_seed(823)
    weights = {}

    def put(key, shape, *, projection=False):
        weights[key] = (torch.randn(shape, generator=generator) * 0.15).to(
            torch.float32 if projection else dtype,
        )

    put("control_proj_in.weight", (8, 12), projection=True)
    put("control_proj_in.bias", (8,), projection=True)
    for index in range(2):
        prefix = f"control_blocks.{index}."
        for suffix, shape in (
            ("norm1.weight", (8,)), ("norm2.weight", (8,)),
            ("adaln_proj.linear.weight", (144, 4)),
            ("adaln_proj.linear.bias", (144,)),
            ("after_proj.weight", (8, 8)), ("after_proj.bias", (8,)),
            ("attn.norm_q.weight", (head_dim,)), ("attn.norm_k.weight", (head_dim,)),
            ("attn.to_q.weight", (heads * head_dim, 8)),
            ("attn.to_k.weight", (heads * head_dim, 8)),
            ("attn.to_v.weight", (heads * head_dim, 8)),
            ("attn.to_out.0.weight", (8, heads * head_dim)),
            ("ff.net.0.proj.weight", (24, 8)), ("ff.net.2.weight", (8, 12)),
        ):
            put(prefix + suffix, shape)
        if index == 0:
            put(prefix + "before_proj.weight", (8, 8))
            put(prefix + "before_proj.bias", (8,))
    return weights


def branch(dtype, *, heads=1, head_dim=8):
    return MiniMaxH3ControlBranch(
        hidden_size=8, num_attention_heads=heads, attention_head_dim=head_dim,
        ffn_dim=12, time_embed_dim=4, control_in_dim=3,
        control_blocks_places=(0, 2), dtype=dtype,
    ).eval()


def inputs(dtype):
    packed = torch.linspace(-0.8, 0.9, 48).reshape(1, 6, 8).to(dtype)
    control = torch.linspace(0.6, -0.4, 24).reshape(1, 2, 12)
    video, audio = torch.tensor([0, 3]), torch.tensor([2, 4])
    curve = torch.tensor([[0.4, -0.8, 1.1, -0.3], [0.9, 0.1, -0.5, 0.7]])
    # t0-video, t1-text, t0-audio, t1-video, t1-audio, padded text.
    table_rows = torch.tensor([0, 4, 2, 3, 5, 1])
    runs = tuple((i, i + 1, int(row)) for i, row in enumerate(table_rows))
    rotary = (torch.ones(6, 6), torch.zeros(6, 6))
    padding = torch.tensor([False] * 5 + [True])
    mask = padding[:, None] == padding[None, :]
    return packed, control, video, audio, curve, runs, rotary, mask


def original_reference(weights, args, *, heads=1):
    """VideoX-Fun original block/branch equations, without native helpers."""
    packed, control, video, audio, curve, runs, rotary, mask = args
    rows = torch.tensor([row for _start, _end, row in runs])
    stream = packed.index_copy(1, video, F.linear(
        control, weights["control_proj_in.weight"], weights["control_proj_in.bias"],
    ).to(packed.dtype))
    hints = {}
    for index, place in enumerate((0, 2)):
        prefix = f"control_blocks.{index}."

        def linear(x, name, bias=False):
            return F.linear(x, weights[prefix + name + ".weight"],
                            weights[prefix + name + ".bias"] if bias else None)

        def norm(x, name):
            return F.rms_norm(x, (x.shape[-1],), weights[prefix + name + ".weight"], 1e-5)

        def rotate(x):
            cos, sin = (value.to(x.dtype)[None, :, None] for value in rotary)
            size = cos.shape[-1]
            first, second = x[..., :size].chunk(2, -1)
            rotated = torch.cat((-second, first), -1)
            return torch.cat((x[..., :size] * cos + rotated * sin, x[..., size:]), -1)

        if index == 0:
            stream = linear(stream, "before_proj", True) + packed
        table = linear(F.silu(curve).to(packed.dtype), "adaln_proj.linear", True)
        shift_a, scale_a, gate_a, shift_m, scale_m, gate_m = table.view(-1, 48).chunk(6, -1)
        normalized = norm(stream, "norm1") * (1 + scale_a[rows]) + shift_a[rows]
        query, key, value = [linear(normalized, f"attn.to_{part}").reshape(1, 6, heads, -1)
                             for part in ("q", "k", "v")]
        query = rotate(norm(query, "attn.norm_q"))
        key = rotate(norm(key, "attn.norm_k"))
        attended = F.scaled_dot_product_attention(
            query.transpose(1, 2), key.transpose(1, 2), value.transpose(1, 2),
            attn_mask=mask[None, None],
        ).transpose(1, 2).flatten(2, 3)
        stream = stream + gate_a[rows] * linear(attended, "attn.to_out.0")
        normalized = norm(stream, "norm2") * (1 + scale_m[rows]) + shift_m[rows]
        value, gate = linear(normalized, "ff.net.0.proj").chunk(2, -1)
        stream = stream + gate_m[rows] * linear(value * F.silu(gate), "ff.net.2")
        hints[place] = linear(stream, "after_proj", True).index_fill(1, audio, 0)
    return hints


class OriginalH3ControlTests(unittest.TestCase):
    def test_multihead_unequal_inner_width_and_nonzero_rotary_match_original(self):
        for dtype in (torch.float32, torch.bfloat16):
            with self.subTest(dtype=dtype), torch.inference_mode():
                weights = donor_weights(dtype, heads=2)
                model = branch(dtype, heads=2)
                model.load_state_dict(model.adapt_original_state_dict(weights), strict=True)
                args = list(inputs(dtype))
                axes = torch.tensor([[0.2, 0.5, -0.3], [1.2, -0.2, 0.7],
                                     [0.4, 1.1, -0.6], [0.8, 0.3, 1.4],
                                     [-0.5, 0.9, 0.6], [0.1, -0.8, 1.7]])
                angles = torch.cat((axes, axes), -1)
                args[6] = (angles.cos(), angles.sin())
                expected = original_reference(weights, args, heads=2)
                actual = model(*args)
                for place in expected:
                    torch.testing.assert_close(actual[place], expected[place], rtol=0,
                                               atol=2e-7 if dtype == torch.float32 else 0)

    def test_real_transformer_injects_original_skips_after_matching_blocks(self):
        torch.manual_seed(41)
        model = MiniMaxH3Transformer(
            hidden_size=8, num_layers=3, token_refiner_layers=1,
            num_attention_heads=1, attention_head_dim=8, ffn_dim=12,
            video_channels=1, audio_channels=2, text_dim=4,
            curve_grid=None, curve_dim=4, time_embed_hidden_size=8,
            rope_freq_dim=1, dtype=torch.float32,
        ).eval()
        reference = copy.deepcopy(model)
        control = branch(torch.float32)
        weights = donor_weights(torch.float32)
        control.load_state_dict(control.adapt_original_state_dict(weights))
        control_rows = inputs(torch.float32)[1]
        kwargs = dict(
            hidden_states=torch.linspace(-0.4, 0.7, 8).reshape(1, 2, 4),
            audio_hidden_states=torch.linspace(0.2, 0.8, 4).reshape(1, 2, 2),
            encoder_hidden_states=torch.linspace(-0.5, 0.5, 8).reshape(1, 2, 4),
            timestep=torch.tensor([0.3, 0.7]),
            timestep_indices=torch.tensor([0, 1, 0, 1, 1, 0]),
            token_tags=torch.tensor([0, 1, 2, 0, 2, -1]),
            position_ids=torch.zeros(6, 3), video_indices=torch.tensor([0, 3]),
            audio_indices=torch.tensor([2, 4]), text_indices=torch.tensor([1, 5]),
        )
        expected_hints = {}

        def capture_original_branch(_module, block_args):
            packed, curve, _turbo, runs, rotary, mask, _acceleration = block_args
            expected_hints.update(original_reference(weights, (
                packed, control_rows, kwargs["video_indices"], kwargs["audio_indices"],
                curve, runs, rotary, mask,
            )))

        def after(place):
            def inject(_module, _args, output):
                return output + expected_hints[place] * 0.6
            return inject

        hooks = [reference.blocks[0].register_forward_pre_hook(capture_original_branch)]
        hooks += [reference.blocks[place].register_forward_hook(after(place)) for place in (0, 2)]
        try:
            with torch.inference_mode():
                baseline = model(**kwargs)
                with patch.object(control, "forward", side_effect=AssertionError("zero strength ran Control")):
                    bypass = model(**kwargs, h3_control_branch=control,
                                   h3_control_rows=control_rows, h3_control_strength=0)
                torch.testing.assert_close(bypass.sample, baseline.sample, rtol=0, atol=0)
                torch.testing.assert_close(bypass.audio_sample, baseline.audio_sample, rtol=0, atol=0)
                expected = reference(**kwargs)
                actual = model(**kwargs, h3_control_branch=control,
                               h3_control_rows=control_rows, h3_control_strength=0.6)
                torch.testing.assert_close(actual.sample, expected.sample, rtol=0, atol=3e-7)
                torch.testing.assert_close(actual.audio_sample, expected.audio_sample, rtol=0, atol=3e-7)
                self.assertFalse(torch.equal(actual.sample, baseline.sample))
                # No direct audio skip does not imply bit-identical soundtrack:
                # subsequent joint base attention still mixes all modalities.
                self.assertFalse(torch.equal(actual.audio_sample, baseline.audio_sample))
                for bad in (
                    {"h3_attention_engine": "sol_attn"},
                    {"h3_spectrum_controller": object()},
                    {"h3_control_strength": float("nan")},
                ):
                    request = dict(h3_control_branch=control, h3_control_rows=control_rows, **bad)
                    with self.assertRaises(ValueError):
                        model(**kwargs, **request)
                model.use_adaln_curves = True
                with self.assertRaisesRegex(ValueError, "full-timestep"):
                    model(**kwargs, h3_control_branch=control, h3_control_rows=control_rows)
        finally:
            for hook in hooks:
                hook.remove()

    def test_original_equations_and_source_buffers_survive_native_inference(self):
        for dtype in (torch.float32, torch.bfloat16):
            with self.subTest(dtype=dtype), torch.inference_mode():
                weights = donor_weights(dtype)
                weights_before = {key: tensor.clone() for key, tensor in weights.items()}
                model = branch(dtype)
                model.load_state_dict(model.adapt_original_state_dict(weights), strict=True)
                args = inputs(dtype)
                packed_before = args[0].clone()
                expected = original_reference(weights, args)
                actual = model(*args, offload_hints=True)
                self.assertEqual(set(actual), {0, 2})
                for place in expected:
                    torch.testing.assert_close(actual[place], expected[place], rtol=0, atol=2e-7 if dtype == torch.float32 else 0)
                    self.assertEqual(actual[place].device.type, "cpu")
                    self.assertTrue(torch.equal(actual[place][:, args[3]], torch.zeros_like(actual[place][:, args[3]])))
                torch.testing.assert_close(args[0], packed_before, rtol=0, atol=0)
                for key in weights:
                    torch.testing.assert_close(weights[key], weights_before[key], rtol=0, atol=0)

    def test_checkpoint_drift_fails_before_loading_any_tensors(self):
        model = branch(torch.float32)
        weights = donor_weights(torch.float32)
        mutations = [
            lambda w: w.pop("control_blocks.1.attn.to_v.weight"),
            lambda w: w.update({"control_blocks.0.post_norm.weight": torch.ones(8)}),
            lambda w: w.update({"control_blocks.0.adaln_proj.linear.weight": torch.ones(144, 2)}),
            # Matching fused row total must not conceal different Q/K widths.
            lambda w: w.update({"control_blocks.0.attn.to_q.weight": torch.ones(7, 8),
                                "control_blocks.0.attn.to_k.weight": torch.ones(9, 8)}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                malformed = dict(weights)
                mutate(malformed)
                with self.assertRaises(ValueError):
                    model.adapt_original_state_dict(malformed)

    def test_cancellation_returns_no_partial_skips_and_preserves_base(self):
        with torch.inference_mode():
            model = branch(torch.float32)
            model.load_state_dict(model.adapt_original_state_dict(donor_weights(torch.float32)))
            for cancel_at in (1, 3, 5):
                args = inputs(torch.float32)
                before = args[0].clone()
                calls = 0

                def interrupted():
                    nonlocal calls
                    calls += 1
                    return calls == cancel_at

                self.assertIsNone(model(*args, interrupted=interrupted))
                torch.testing.assert_close(args[0], before, rtol=0, atol=0)

    def test_bad_layout_and_compact_timestep_are_rejected(self):
        model = branch(torch.float32)
        for index, replacement in (
            (4, torch.zeros(2, 2)), (1, torch.zeros(1, 1, 12)),
            (2, torch.tensor([0, 0])), (3, torch.tensor([0, 4])),
            (2, torch.tensor([0, 6])),
        ):
            with self.subTest(index=index):
                args = list(inputs(torch.float32))
                args[index] = replacement
                with self.assertRaises(ValueError):
                    model(*args)

    def test_zero_init_and_strength_bypass_preserve_base_identity(self):
        with torch.inference_mode():
            model = branch(torch.float32)
            args = inputs(torch.float32)
            hints = model(*args)
            for hint in hints.values():
                self.assertEqual(torch.count_nonzero(hint).item(), 0)
            hidden = args[0]
            hints = {0: torch.full_like(hidden, 0.4), 2: torch.full_like(hidden, -0.2)}
            self.assertIs(add_control_hint(hidden, hints, 0, strength=0), hidden)
            self.assertIs(add_control_hint(hidden, hints, 1, strength=1), hidden)
            torch.testing.assert_close(add_control_hint(hidden, hints, 2, strength=0.5), hidden - 0.1)
            for strength in (True, float("nan"), float("inf"), -0.1, 1.1):
                with self.assertRaises(ValueError):
                    add_control_hint(hidden, hints, 0, strength=strength)


if __name__ == "__main__":
    unittest.main()
