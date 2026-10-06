"""Original donor equations independently check Control weight adaptation.

This fixture uses separate Q/K/V, Diffusers [value, gate] SwiGLU, mixed
timestep/modalities, padding attention, and nonzero skips. It detects a
plausible but wrong key-only checkpoint conversion and packed-buffer aliasing.
"""

from pathlib import Path
import ast
import copy
import hashlib
import sys
import tempfile
import types
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

from models.minimax_h3.control import MiniMaxH3ControlBranch, add_control_hint, encode_control_rows
from models.minimax_h3 import control as control_module
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
                # A registered child uses the same donor arithmetic without
                # activating a second MMGP pipeline root. No-control forwards
                # still bypass the resident child entirely.
                model.bind_control_branch(control)
                implicit = model(**kwargs, h3_control_rows=control_rows, h3_control_strength=0.6)
                torch.testing.assert_close(implicit.sample, actual.sample, rtol=0, atol=0)
                torch.testing.assert_close(implicit.audio_sample, actual.audio_sample, rtol=0, atol=0)
                with patch.object(control, "forward", side_effect=AssertionError("ordinary forward ran Control")):
                    ordinary = model(**kwargs)
                torch.testing.assert_close(ordinary.sample, baseline.sample, rtol=0, atol=0)
                torch.testing.assert_close(ordinary.audio_sample, baseline.audio_sample, rtol=0, atol=0)
                with self.assertRaisesRegex(ValueError, "registered for residency"):
                    model(**kwargs, h3_control_branch=branch(torch.float32), h3_control_rows=control_rows)
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


class OriginalH3ControlLoaderTests(unittest.TestCase):
    def test_native_mmgp_streams_complete_donor_and_preserves_projection_dtype(self):
        from safetensors.torch import save_file

        weights = donor_weights(torch.bfloat16)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.safetensors"
            save_file(weights, path)
            raw = path.read_bytes()
            with patch.object(control_module, "ORIGINAL_CONTROL_BYTES", len(raw)), \
                 patch.object(control_module, "ORIGINAL_CONTROL_SHA256", hashlib.sha256(raw).hexdigest()), \
                 patch.object(control_module, "MiniMaxH3ControlBranch", side_effect=lambda **kw: branch(kw["dtype"])):
                loaded = control_module.load_original_control_branch(path)
            self.assertFalse(loaded.training)
            self.assertTrue(all(not p.requires_grad and p.device.type == "cpu" for p in loaded.parameters()))
            self.assertEqual(loaded.control_proj_in.weight.dtype, torch.float32)
            self.assertEqual(loaded.control_blocks[0].attn.qkv_proj.weight.dtype, torch.bfloat16)
            expected = original_reference(weights, inputs(torch.bfloat16))
            actual = loaded(*inputs(torch.bfloat16))
            for place in expected:
                torch.testing.assert_close(actual[place], expected[place], rtol=0, atol=0)

    def test_unpinned_or_unsafe_files_fail_before_native_loading(self):
        from mmgp import offload

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong.safetensors"
            path.write_bytes(b"different checkpoint")
            with patch.object(offload, "load_model_data") as loader:
                with self.assertRaises(ValueError):
                    control_module.load_original_control_branch(path)
                with patch.object(control_module, "ORIGINAL_CONTROL_BYTES", path.stat().st_size):
                    with self.assertRaisesRegex(ValueError, "original bytes"):
                        control_module.load_original_control_branch(path)
                unsafe = path.with_suffix(".pt")
                unsafe.write_bytes(path.read_bytes())
                with patch.object(control_module, "ORIGINAL_CONTROL_BYTES", unsafe.stat().st_size):
                    with self.assertRaises(ValueError):
                        control_module.load_original_control_branch(unsafe)
                    sharded = path.with_name("control-00001-of-00002.safetensors")
                    sharded.write_bytes(path.read_bytes())
                    with self.assertRaises(ValueError):
                        control_module.load_original_control_branch(sharded)
                    adjacent = path.with_name("wrong_map.json")
                    adjacent.write_text("{}")
                    with self.assertRaisesRegex(ValueError, "adjacent"):
                        control_module.load_original_control_branch(path)
                with self.assertRaises(InterruptedError):
                    control_module.load_original_control_branch(path, interrupted=lambda: True)
                loader.assert_not_called()

    def test_postload_replacement_or_cancellation_never_returns_a_branch(self):
        from mmgp import offload
        from safetensors.torch import save_file

        real_loader = offload.load_model_data
        for action in ("replace", "cancel", "late_map"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "original.safetensors"
                save_file(donor_weights(torch.bfloat16), path)
                raw = path.read_bytes()
                cancelled = []

                def load_then_change(*args, **kwargs):
                    if action == "late_map":
                        path.with_name("original_map.json").write_text("{}")
                    real_loader(*args, **kwargs)
                    if action == "replace":
                        path.rename(path.with_suffix(".retained"))
                        path.write_bytes(raw)  # Equal bytes, distinct loaded-file binding.
                    elif action == "cancel":
                        cancelled.append(True)

                with patch.object(control_module, "ORIGINAL_CONTROL_BYTES", len(raw)), \
                     patch.object(control_module, "ORIGINAL_CONTROL_SHA256", hashlib.sha256(raw).hexdigest()), \
                     patch.object(control_module, "MiniMaxH3ControlBranch", side_effect=lambda **kw: branch(kw["dtype"])), \
                     patch.object(offload, "load_model_data", side_effect=load_then_change):
                    with self.assertRaises(InterruptedError if action == "cancel" else ValueError):
                        control_module.load_original_control_branch(path, interrupted=lambda: bool(cancelled))


class OriginalH3ControlResidencyPreparationTests(unittest.TestCase):
    @staticmethod
    def runtime():
        from accelerate import init_empty_weights

        # Complete original graph geometry, with no learned storage or CUDA.
        with init_empty_weights(include_buffers=True):
            transformer = MiniMaxH3Transformer(curve_grid=None, curve_dim=2688)
            control = MiniMaxH3ControlBranch()
        source = APP / "models/minimax_h3/minimax_h3_main.py"
        model = next(node for node in ast.parse(source.read_text()).body
                     if isinstance(node, ast.ClassDef) and node.name == "MiniMaxH3Model")
        methods = [node for node in model.body if isinstance(node, ast.FunctionDef)
                   and node.name in ("_load_control_branch", "release")]
        namespace = {"torch": torch, "__package__": "models.minimax_h3",
                     "clear_pdd_runtime": lambda _transformer: None}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), namespace)
        runtime = types.SimpleNamespace(
            transformer=transformer, dtype=torch.bfloat16, _interrupt=False,
            reference_mode=False, selected_model_type="minimax_h3",
            _h3_runtime_snapshot=None, _h3_runtime_binding=None, _h3_cumulative_token=None,
        )
        runtime.load = types.MethodType(namespace["_load_control_branch"], runtime)
        runtime.release = types.MethodType(namespace["release"], runtime)
        return runtime, control

    def test_native_graph_discovery_and_alias_safe_release(self):
        from mmgp import offload
        runtime, control = self.runtime()
        transformer_alias = runtime.transformer
        ordinary_keys = set(transformer_alias.state_dict())
        with patch.object(control_module, "load_original_control_branch", return_value=control):
            runtime.load("already-acquired-original.safetensors")
        self.assertIs(transformer_alias.h3_control_branch, control)
        towers, floors = offload._detect_main_towers(transformer_alias)
        self.assertIn("blocks.", towers)
        self.assertIn("h3_control_branch.control_blocks.", towers)
        self.assertTrue(all(any(block is floor for floor in floors) for block in control.control_blocks))
        self.assertIs(dict(transformer_alias.named_modules())["h3_control_branch.control_proj_in"],
                      control.control_proj_in)
        self.assertEqual(control.control_proj_in._lock_dtype, torch.float32)
        runtime.release()
        self.assertIsNone(runtime.transformer)
        self.assertFalse(hasattr(transformer_alias, "h3_control_branch"))
        self.assertEqual(set(transformer_alias.state_dict()), ordinary_keys)

    def test_ineligible_or_profiled_runtime_never_loads_weights(self):
        runtime, control = self.runtime()
        cases = [
            (runtime, "reference_mode", True),
            (runtime, "selected_model_type", "minimax_h3_ref"),
            (runtime, "_h3_runtime_snapshot", object()),
            (runtime, "_h3_runtime_binding", object()),
            (runtime, "_h3_cumulative_token", object()),
            (runtime, "dtype", torch.float16),
            (runtime.transformer, "use_adaln_curves", True),
            (runtime.transformer, "_h3_turbo_prepared", True),
            (runtime.transformer.config, "in_channels", 1),
        ]
        with patch.object(control_module, "load_original_control_branch") as loader:
            for owner, field, value in cases:
                original = getattr(owner, field)
                with self.subTest(field=field):
                    setattr(owner, field, value)
                    try:
                        with self.assertRaises(ValueError):
                            runtime.load("unused")
                    finally:
                        setattr(owner, field, original)
            runtime.transformer.blocks[0]._hf_hook = object()
            with self.assertRaisesRegex(ValueError, "before MMGP"):
                runtime.load("unused")
            with self.assertRaisesRegex(ValueError, "before MMGP"):
                runtime.transformer.bind_control_branch(control)
            del runtime.transformer.blocks[0]._hf_hook
            loader.assert_not_called()
        runtime.transformer.bind_control_branch(control)
        with self.assertRaisesRegex(ValueError, "already bound"):
            runtime.transformer.bind_control_branch(control)

    def test_cancelled_load_never_attaches_a_partial_branch(self):
        runtime, control = self.runtime()
        runtime._interrupt = True
        with patch.object(control_module, "load_original_control_branch") as loader:
            with self.assertRaises(InterruptedError):
                runtime.load("unused")
            loader.assert_not_called()
        runtime._interrupt = False

        def cancelled(_filename, *, interrupted):
            self.assertFalse(interrupted())
            runtime._interrupt = True
            return control

        with patch.object(control_module, "load_original_control_branch", side_effect=cancelled):
            with self.assertRaises(InterruptedError):
                runtime.load("unused")
        self.assertFalse(hasattr(runtime.transformer, "h3_control_branch"))


class OriginalH3ControlConditioningTests(unittest.TestCase):
    @staticmethod
    def native_runtime():
        from models.minimax_h3.video_vae import AutoencoderKLMiniMaxH3

        # Actual learned native encoder, temporal clock and distribution. Only
        # channel widths/decoder size shrink; no checkpoint or GPU is needed.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(738)
            vae = AutoencoderKLMiniMaxH3(
                block_out_channels=(4,) * 6, layers_per_block=1,
                norm_num_groups=1, decoder_num_layers=1,
                decoder_num_attention_heads=1, decoder_attention_head_dim=8,
                latents_mean=tuple(0.1 + i / 100 for i in range(24)),
                latents_std=tuple(1 + i / 50 for i in range(24)),
            ).eval().requires_grad_(False)
        source = APP / "models/minimax_h3/minimax_h3_main.py"
        model = next(node for node in ast.parse(source.read_text()).body
                     if isinstance(node, ast.ClassDef) and node.name == "MiniMaxH3Model")
        method = next(node for node in model.body
                      if isinstance(node, ast.FunctionDef) and node.name == "_encode_control_video")
        namespace = {"torch": torch, "__package__": "models.minimax_h3"}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        runtime = types.SimpleNamespace(vae=vae, device=torch.device("cpu"),
                                        patch_size=(1, 2, 2), _interrupt=False)
        runtime.encode = types.MethodType(namespace["_encode_control_video"], runtime)
        return runtime

    def test_native_learned_vae_mode_matches_chunked_donor_without_rng_or_rounding(self):
        from diffusers.models.autoencoders.vae import DiagonalGaussianDistribution

        runtime = self.native_runtime()
        video = torch.linspace(0.05, 0.95, 3 * 22 * 32 * 32).reshape(1, 3, 22, 32, 32)
        original = video.clone()
        pixels = (video - torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1, 1)) / \
            torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1, 1)
        padded = torch.cat((pixels, pixels[:, :, -1:].repeat(1, 1, 12, 1, 1)), dim=2)
        with torch.inference_mode():
            # Independent training recipe: per-clip native inference, raw mean
            # half of the moments, tail drop, then FP32 normalization/packing.
            moments = torch.cat([runtime.vae.quant_conv(runtime.vae.encoder(chunk))
                                 for chunk in padded.split(17, dim=2)], dim=2)[:, :, :-3]
            raw = moments.chunk(2, dim=1)[0].float()
            mean = torch.tensor(runtime.vae.config.latents_mean).view(1, 24, 1, 1, 1)
            std = torch.tensor(runtime.vae.config.latents_std).view(1, 24, 1, 1, 1)
            expected = self.independently_pack((raw - mean) / std)
        hook_calls = []
        hook = runtime.vae.encoder.register_forward_pre_hook(
            lambda _module, args: hook_calls.append((args[0].shape[2], torch.is_inference_mode_enabled())))
        before_rng = torch.random.get_rng_state().clone()
        try:
            with patch.object(DiagonalGaussianDistribution, "sample",
                              side_effect=AssertionError("Control sampled the posterior")):
                rows = runtime.encode(video, height=32, width=32, num_frames=22)
                again = runtime.encode(video, height=32, width=32, num_frames=22)
        finally:
            hook.remove()
        torch.testing.assert_close(rows[0, :, :96], expected, rtol=0, atol=0)
        torch.testing.assert_close(again, rows, rtol=0, atol=0)
        torch.testing.assert_close(torch.random.get_rng_state(), before_rng, rtol=0, atol=0)
        torch.testing.assert_close(video, original, rtol=0, atol=0)
        self.assertEqual(hook_calls, [(17, True)] * 4)
        self.assertEqual(rows.shape, (1, 7, 196))
        self.assertEqual(torch.count_nonzero(rows[..., 96:]).item(), 0)

    def test_native_inpaint_encodes_black_source_with_loaded_vae_statistics(self):
        runtime = self.native_runtime()
        mask = torch.ones(1, 1, 22, 32, 32)
        rows = runtime.encode(torch.zeros(1, 3, 22, 32, 32), height=32,
                              width=32, num_frames=22, mask_video=mask)
        self.assertEqual(torch.count_nonzero(rows[..., 96:100]).item(), 0)
        # Both supplied control and implicit masked source are black pixels:
        # their learned normalized latent rows coincide and remain nonzero.
        torch.testing.assert_close(rows[..., :96], rows[..., 100:], rtol=0, atol=0)
        self.assertGreater(torch.count_nonzero(rows[..., 100:]).item(), 0)

    def test_native_cancellation_and_missing_runtime_do_not_return_control_rows(self):
        runtime = self.native_runtime()
        video = torch.zeros(1, 3, 22, 32, 32)
        runtime._interrupt = True
        with patch.object(runtime.vae, "encode", side_effect=AssertionError("cancelled VAE ran")):
            self.assertIsNone(runtime.encode(video, height=32, width=32, num_frames=22))
        runtime._interrupt = False
        hook = runtime.vae.encoder.register_forward_pre_hook(
            lambda _module, _args: setattr(runtime, "_interrupt", True))
        try:
            self.assertIsNone(runtime.encode(video, height=32, width=32, num_frames=22))
        finally:
            hook.remove()
        runtime._interrupt = False
        runtime.patch_size = (1, 1, 1)
        with self.assertRaisesRegex(ValueError, "patch geometry"):
            runtime.encode(video, height=32, width=32, num_frames=22)
        runtime.vae = None
        with self.assertRaisesRegex(RuntimeError, "VAE is unavailable"):
            runtime.encode(video, height=32, width=32, num_frames=22)

    @staticmethod
    def request(video, encode_mode, **overrides):
        return encode_control_rows(
            video, encode_mode=encode_mode, latents_mean=[0.25] * 24,
            latents_std=[2.0] * 24, height=32, width=32, num_frames=22,
            **overrides,
        )

    @staticmethod
    def raw_latents(offset=0):
        return torch.arange(24 * 7 * 2 * 2).view(1, 24, 7, 2, 2).float() / 100 + offset

    @staticmethod
    def independently_pack(latents):
        # Here each 2x2 latent frame is one patch. Columns are channel-major,
        # then pixel-row/pixel-column; rows advance on the native latent clock.
        return torch.stack([latents[0, :, frame].flatten() for frame in range(7)])

    def test_frame_fit_imagenet_and_unrounded_latent_normalization(self):
        video = torch.linspace(0.1, 0.9, 3 * 3 * 2 * 2).reshape(1, 3, 3, 2, 2)
        before = video.clone()
        observed = []
        raw = self.raw_latents()

        def encode_mode(pixels):
            observed.append(pixels.clone())
            return raw

        rows = self.request(video, encode_mode)
        self.assertEqual(rows.shape, (1, 7, 196))
        self.assertEqual(len(observed), 1)
        pixels = observed[0]
        self.assertEqual(pixels.shape, (1, 3, 22, 32, 32))
        mean, std = torch.tensor([0.485, 0.456, 0.406]), torch.tensor([0.229, 0.224, 0.225])
        torch.testing.assert_close(pixels[0, :, 0, 0, 0], (video[0, :, 0, 0, 0] - mean) / std)
        torch.testing.assert_close(pixels[0, :, 0, -1, -1], (video[0, :, 0, -1, -1] - mean) / std)
        torch.testing.assert_close(pixels[:, :, 21], pixels[:, :, 2], rtol=0, atol=0)
        expected = self.independently_pack((raw - 0.25) / 2)
        torch.testing.assert_close(rows[0, :, :96], expected, rtol=0, atol=0)
        self.assertEqual(torch.count_nonzero(rows[0, :, 96:]).item(), 0)
        torch.testing.assert_close(video, before, rtol=0, atol=0)

    def test_inpaint_training_order_visibility_clock_and_masked_pixel_encoding(self):
        control = torch.full((1, 3, 30, 32, 32), 0.4)
        source = torch.full((1, 3, 30, 32, 32), 0.8)
        mask = torch.zeros(1, 1, 22, 2, 2)
        # A boundary at exactly 0.5 stays visible; 0.6 is regenerated.
        mask[:, :, 0::2, :, :1] = 0.5
        mask[:, :, 1::2, :, :1] = 0.6
        mask[:, :, :, :, 1:] = 1
        snapshots = [value.clone() for value in (control, source, mask)]
        observed = []

        def encode_mode(pixels):
            observed.append(pixels.clone())
            return self.raw_latents(offset=0 if len(observed) == 1 else 10)

        rows = self.request(control, encode_mode, mask_video=mask, inpaint_video=source)
        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[0].shape[2], 22)
        mean = torch.tensor([0.485, 0.456, 0.406])
        std = torch.tensor([0.229, 0.224, 0.225])
        torch.testing.assert_close(observed[1][0, :, 0, 0, 0], (0.8 - mean) / std)
        torch.testing.assert_close(observed[1][0, :, 1, 0, 0], -mean / std)
        torch.testing.assert_close(observed[1][0, :, 0, 0, -1], -mean / std)
        # Bilinear mask fitting creates soft boundaries. Re-hardening must
        # remove those before masking source pixels, without a second VAE pass.
        for channel in range(3):
            normalized = observed[1][0, channel]
            black = -mean[channel] / std[channel]
            kept = (0.8 - mean[channel]) / std[channel]
            self.assertTrue((torch.isclose(normalized, black) | torch.isclose(normalized, kept)).all())
        torch.testing.assert_close(rows[0, :, :96], self.independently_pack((self.raw_latents() - 0.25) / 2))
        torch.testing.assert_close(rows[0, :, 100:], self.independently_pack((self.raw_latents(10) - 0.25) / 2))
        # Independently sample the original trilinear time coordinates. Spatial
        # sample centers lie inside constant left/right halves of this mask.
        expected_visibility = []
        for frame in range(7):
            coordinate = (frame + 0.5) * 22 / 7 - 0.5
            low = int(coordinate)
            fraction = coordinate - low
            left = (1 - low % 2) * (1 - fraction) + (1 - (low + 1) % 2) * fraction
            expected_visibility.append([left, 0, left, 0])
        torch.testing.assert_close(rows[0, :, 96:100], torch.tensor(expected_visibility), rtol=0, atol=2e-6)
        for original, before in zip((control, source, mask), snapshots):
            torch.testing.assert_close(original, before, rtol=0, atol=0)

    def test_absent_inpaint_source_encodes_black_pixels_instead_of_zero_latents(self):
        seen = []

        def encode_mode(pixels):
            seen.append(pixels)
            return self.raw_latents(10 if len(seen) == 2 else 0)

        rows = self.request(torch.zeros(1, 3, 22, 32, 32), encode_mode,
                            mask_video=torch.zeros(1, 1, 22, 32, 32))
        self.assertEqual(len(seen), 2)
        self.assertTrue((seen[1] < 0).all())
        self.assertTrue((rows[0, :, 96:100] == 1).all())
        torch.testing.assert_close(rows[0, :, 100:], self.independently_pack((self.raw_latents(10) - 0.25) / 2))

    def test_bad_input_or_mode_geometry_never_gets_padded_or_cropped_into_validity(self):
        video = torch.zeros(1, 3, 22, 32, 32)
        for malformed in (video.to(torch.uint8), video[:, :, :0], video + float("nan"), video + 1.1):
            with self.subTest(shape=malformed.shape):
                calls = []
                with self.assertRaises(ValueError):
                    self.request(malformed, lambda pixels: calls.append(pixels))
                self.assertEqual(calls, [])
        for mode in (torch.zeros(1, 24, 8, 2, 2), torch.zeros(1, 24, 7, 3, 2),
                     self.raw_latents() * float("nan")):
            with self.assertRaises(ValueError):
                self.request(video, lambda _pixels: mode)
        with self.assertRaisesRegex(ValueError, "requires a mask"):
            self.request(video, lambda _pixels: self.raw_latents(), inpaint_video=video)

    def test_cancellation_discards_prepared_rows_before_or_after_owned_encode(self):
        for stop_after_encode in (False, True):
            observed = []

            def encode_mode(pixels):
                observed.append(pixels)
                return self.raw_latents()

            rows = self.request(torch.zeros(1, 3, 22, 32, 32), encode_mode,
                                interrupted=lambda: bool(observed) if stop_after_encode else True)
            self.assertIsNone(rows)
            self.assertEqual(len(observed), 1 if stop_after_encode else 0)


if __name__ == "__main__":
    unittest.main()
