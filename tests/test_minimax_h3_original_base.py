"""Real MMGP loading of a thirteen-shard CPU fixture, with numerical QKV proof."""

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import torch
from accelerate import init_empty_weights
from safetensors.torch import save_file

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from models.minimax_h3 import original_base
from models.minimax_h3.transformer import MiniMaxH3Transformer
from services.h3_runtime_binding import H3RuntimeBindingError


def tiny_transformer(**unused):
    return MiniMaxH3Transformer(
        hidden_size=8, num_layers=2, token_refiner_layers=1,
        num_attention_heads=2, attention_head_dim=8, ffn_dim=12,
        video_channels=1, audio_channels=2, text_dim=4,
        curve_grid=None, curve_dim=4, timestep_input_dim=4,
        time_embed_hidden_size=8, rope_freq_dim=1, dtype=torch.bfloat16,
    ).eval().requires_grad_(False)


def loader_functions():
    # Execute the actual loader functions without importing unrelated media
    # pipelines or constructing any production-sized model.
    path = APP / "models/minimax_h3/minimax_h3_main.py"
    tree = ast.parse(path.read_text())
    names = {"_load_transformer", "_restore_interleaved_transformer_qkv", "_probe_transformer_checkpoint"}
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    from mmgp import offload, quant_router
    namespace = dict(__package__="models.minimax_h3", torch=torch,
                     MiniMaxH3Transformer=tiny_transformer,
                     init_empty_weights=init_empty_weights, offload=offload,
                     quant_router=quant_router)
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class OriginalH3BaseLoaderTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(41)
        self.reference = tiny_transformer()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.functions = loader_functions()
        names = [f"model-{i:05d}-of-00013.safetensors" for i in range(1, 14)]
        shards = [{} for _ in names]
        self.weight_map = {}
        # Independent inverse packing: interleave heads' Q/K/V, preserving
        # native [gate,value] FFN weights and all FP32 islands unchanged.
        for index, (key, value) in enumerate(self.reference.state_dict().items()):
            value = value.clone()
            if key.endswith(".qkv_proj.weight"):
                q, k, v = value.chunk(3, dim=0)
                value = torch.cat([torch.cat((q[h*8:(h+1)*8], k[h*8:(h+1)*8], v[h*8:(h+1)*8]))
                                   for h in range(2)]).contiguous()
            shards[index % 13][key] = value
            self.weight_map[key] = names[index % 13]
        for name, state in zip(names, shards):
            save_file(state, self.directory / name)
        self.index = self.directory / "model.safetensors.index.json"
        self.index.write_text(json.dumps({"weight_map": self.weight_map}))
        self.paths = [self.directory / name for name in names]
        self.pin_fixture()

    def pin_fixture(self):
        rows = tuple((p.name, p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.paths)
        if hasattr(self, "pins"):
            for pin in self.pins:
                pin.stop()
        self.pins = [patch.object(original_base, "ORIGINAL_BASE_SHARDS", rows),
                     patch.object(original_base, "ORIGINAL_BASE_INDEX_SHA256", hashlib.sha256(self.index.read_bytes()).hexdigest())]
        for pin in self.pins:
            pin.start()
            self.addCleanup(pin.stop)

    def load(self, filename=None, **kwargs):
        return self.functions["_load_transformer"](
            filename if filename is not None else str(self.paths[0]), torch.bfloat16, **kwargs,
        )

    def test_real_mmgp_loads_all_thirteen_shards_and_matches_numerical_reference(self):
        for filenames in (str(self.paths[0]), [str(p) for p in reversed(self.paths)]):
            with self.subTest(filenames=type(filenames).__name__), torch.inference_mode():
                loaded = self.load(filenames)
                self.assertEqual(loaded.h3_qkv_layout, "interleaved")
                self.assertEqual(len(loaded.h3_original_base_evidence), 14)
                self.assertFalse(loaded.training)
                for key, expected in self.reference.state_dict().items():
                    actual = loaded.state_dict()[key]
                    self.assertEqual(actual.device.type, "cpu")
                    self.assertEqual(actual.dtype, expected.dtype)
                    torch.testing.assert_close(actual, expected, rtol=0, atol=0)
                self.assertEqual(loaded.blocks[0].attn.qkv_proj.weight.dtype, torch.bfloat16)
                self.assertEqual(loaded.time_embedder.proj_out.weight.dtype, torch.float32)
                self.assertEqual(loaded.video_patch_proj.weight.dtype, torch.float32)
                kwargs = dict(
                    hidden_states=torch.linspace(-0.4, 0.7, 8).reshape(1, 2, 4),
                    audio_hidden_states=torch.linspace(0.2, 0.8, 4).reshape(1, 2, 2),
                    encoder_hidden_states=torch.linspace(-0.5, 0.5, 8).reshape(1, 2, 4),
                    timestep=torch.tensor([0.3, 0.7]),
                    timestep_indices=torch.tensor([0, 1, 0, 1, 1, 0]),
                    token_tags=torch.tensor([0, 1, 2, 0, 2, -1]),
                    position_ids=torch.tensor([[0.2,0.1,0.4],[0.5,0.3,0.1],[0.4,0.7,0.2],
                                               [0.8,0.3,0.2],[0.2,0.6,0.3],[0.4,0.2,0.8]]),
                    video_indices=torch.tensor([0, 3]), audio_indices=torch.tensor([2, 4]),
                    text_indices=torch.tensor([1, 5]),
                )
                expected = self.reference(**copy.deepcopy(kwargs))
                actual = loaded(**copy.deepcopy(kwargs))
                torch.testing.assert_close(actual.sample, expected.sample, rtol=0, atol=0)
                torch.testing.assert_close(actual.audio_sample, expected.audio_sample, rtol=0, atol=0)

    def test_missing_or_changed_late_shard_fails_before_mmgp_assignment(self):
        from mmgp import offload
        path = self.paths[-1]
        raw = path.read_bytes()
        for mutation in ("missing", "changed"):
            with self.subTest(mutation=mutation), patch.object(offload, "load_model_data") as loader:
                if mutation == "missing":
                    path.unlink()
                    error = FileNotFoundError
                else:
                    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                    error = ValueError
                with self.assertRaises(error):
                    self.load()
                loader.assert_not_called()
                path.write_bytes(raw)

    def test_index_key_closure_fails_before_mmgp(self):
        from mmgp import offload
        with patch.object(offload, "load_model_data") as loader:
            self.weight_map.pop(next(iter(self.weight_map)))
            self.index.write_text(json.dumps({"weight_map": self.weight_map}))
            self.pin_fixture()
            with self.assertRaisesRegex(ValueError, "native tensor set"):
                self.load()
            loader.assert_not_called()

    def test_misplaced_or_wrong_shape_tensors_and_quantization_metadata_are_rejected(self):
        from mmgp import offload
        from safetensors.torch import load_file
        path = self.paths[-1]
        raw = path.read_bytes()
        for mutation in ("misplaced", "shape", "quantization"):
            with self.subTest(mutation=mutation), patch.object(offload, "load_model_data") as loader:
                state = load_file(path)
                key = next(iter(state))
                metadata = None
                if mutation == "misplaced":
                    state["unexpected.weight"] = state.pop(key)
                elif mutation == "shape":
                    state[key] = state[key].flatten()[:1].clone()
                else:
                    metadata = {"quantization_map": "{}"}
                save_file(state, path, metadata=metadata)
                self.pin_fixture()
                with self.assertRaises(ValueError):
                    self.load()
                loader.assert_not_called()
                path.write_bytes(raw)

    def test_community_checkpoint_keeps_existing_layout_and_loader(self):
        from mmgp import offload
        with patch.object(offload, "load_model_data") as loader, \
             patch.object(original_base, "load_original_base_into_model") as original_loader:
            self.functions["_probe_transformer_checkpoint"] = lambda _path: {
                "architecture": "full_timestep", "curve_grid": None, "curve_dim": 4,
            }
            loaded = self.load("community.safetensors", qkv_layout="contiguous")
            original_loader.assert_not_called()
            self.assertEqual(loaded.h3_qkv_layout, "contiguous")
            self.assertEqual(loader.call_args.args[1], "community.safetensors")

    def test_sidecar_including_dangling_link_is_rejected(self):
        path = self.paths[10].with_name(self.paths[10].stem + "_map.json")
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                if dangling:
                    path.symlink_to(self.directory / "absent-map")
                else:
                    path.write_text("{}")
                with self.assertRaisesRegex(ValueError, "sidecars"):
                    self.load()
                path.unlink()

    def test_partial_or_cross_directory_roster_is_rejected(self):
        for paths in ([str(p) for p in self.paths[:-1]],
                      [str(p) for p in self.paths[:-1]] + [str(self.directory / "elsewhere" / self.paths[-1].name)]):
            with self.subTest(paths=paths):
                with self.assertRaises(ValueError):
                    self.load(paths)

    def test_replacement_between_preprocessing_and_assignment_is_rejected(self):
        from mmgp import offload
        real_load = offload.load_model_data
        def replace_then_load(model, files, **kwargs):
            original_callback = kwargs["pre_load_callback"]
            def changed_callback(model):
                path = self.paths[-1]
                replacement = path.with_suffix(".replacement")
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
                original_callback(model)
            kwargs["pre_load_callback"] = changed_callback
            return real_load(model, files, **kwargs)
        with patch.object(offload, "load_model_data", side_effect=replace_then_load):
            with self.assertRaises(H3RuntimeBindingError):
                self.load()

    def test_cancellation_before_loading_and_before_assignment_returns_no_model(self):
        with self.assertRaises(InterruptedError):
            self.load(interrupted=lambda: True)
        from mmgp import offload
        real_load = offload.load_model_data
        cancelled = False
        def cancel_then_load(*args, **kwargs):
            nonlocal cancelled
            cancelled = True
            return real_load(*args, **kwargs)
        with patch.object(offload, "load_model_data", side_effect=cancel_then_load):
            with self.assertRaises(InterruptedError):
                self.load(interrupted=lambda: cancelled)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO replacement is a POSIX boundary")
    def test_fifo_replacement_of_index_or_shard_is_bounded_and_never_assigned(self):
        script = '''
import contextlib, os, sys
from unittest.mock import patch
from test_minimax_h3_original_base import OriginalH3BaseLoaderTests, original_base
from mmgp import offload
from services.h3_runtime_binding import H3RuntimeBindingError
case = OriginalH3BaseLoaderTests()
case.setUp()
real_open = original_base._open_captured
target = case.index if sys.argv[1] == 'index' else case.paths[-1]
@contextlib.contextmanager
def replace_then_open(captured):
    if captured.requested == str(target):
        target.unlink()
        os.mkfifo(target)
    with real_open(captured) as opened:
        yield opened
try:
    with patch.object(original_base, '_open_captured', replace_then_open), patch.object(offload, 'load_model_data') as loader:
        try:
            case.load()
        except H3RuntimeBindingError:
            loader.assert_not_called()
            assert target.exists() and not target.is_file()
        else:
            raise AssertionError('FIFO replacement was accepted')
finally:
    case.doCleanups()
'''
        for target in ("index", "shard"):
            with self.subTest(target=target):
                result = subprocess.run(
                    [sys.executable, "-c", script, target],
                    cwd=Path(__file__).parent, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
