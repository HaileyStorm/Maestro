"""CPU-only PDD tensor/executor proof; no artifact/device acceptance."""
from __future__ import annotations

import ast
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
from safetensors.torch import save_file, load_file

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from models.minimax_h3.pdd import (PDDParallelHead, install_pdd_heads, pdd_sigmas,
                                    pdd_sampling_plans_for_sigmas)
from models.minimax_h3.transformer import MiniMaxH3Transformer
from services.h3_pdd import (H3PDDError, PDD_FILES, PDD_PROFILE, qualify_cpu_pdd,
                             prepare_pdd_runtime, activate_pdd_runtime, clear_pdd_runtime,
                             validate_pdd_request, enforce_pdd_runtime)


def tiny_transformer():
    return MiniMaxH3Transformer(hidden_size=8, num_layers=1, token_refiner_layers=1,
                               num_attention_heads=2, attention_head_dim=4, ffn_dim=12,
                               video_channels=2, audio_channels=2, text_dim=10,
                               curve_grid=1025, curve_dim=8, rope_freq_dim=2,
                               dtype=torch.float32)


def artifact_state(transformer, offset=0):
    state = {}
    for prefix, head in (("proj_out", transformer.final_layer.video_out),
                         ("audio_proj_out", transformer.final_layer.audio_out)):
        for name, value in (("weight", head.weight), ("bias", head.bias)):
            bank = torch.stack([torch.full_like(value, (index + offset) / 32)
                                for index in range(32)])
            state[prefix + "." + name] = bank.to(torch.bfloat16)
    state["transformer_blocks.0.attn.to_out.0.lora_down"] = torch.ones(2, 8)
    state["transformer_blocks.0.attn.to_out.0.lora_up"] = torch.ones(8, 2)
    return state


class H3PDDTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.threads)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model = tiny_transformer()
        self.original = (self.model.final_layer.video_out, self.model.final_layer.audio_out)
        self.path = Path(self.temp.name) / PDD_FILES["minimax_h3"]
        self.write_artifact()

    def write_artifact(self, offset=0):
        save_file(artifact_state(self.model, offset), str(self.path),
                  metadata={"pdd_num_steps": "32", "pdd_block_size": "4"})

    def load(self, strength=1):
        admission = qualify_cpu_pdd(self.path, family="minimax_h3", strength=strength)
        prepare_pdd_runtime(self.model, admission)
        normalized = self.model.preprocess_loras("minimax_h3", load_file(str(self.path)))
        self.assertEqual(set(normalized), {"blocks.0.attn.out_proj.lora_A.weight",
                                          "blocks.0.attn.out_proj.lora_B.weight"})
        controller = activate_pdd_runtime(self.model)
        return admission, controller

    def assert_restored(self):
        self.assertIs(self.model.final_layer.video_out, self.original[0])
        self.assertIs(self.model.final_layer.audio_out, self.original[1])
        self.assertFalse(hasattr(self.model, "_h3_pdd_admission"))

    def test_published_schedule_and_independent_nonuniform_overlap(self):
        for shift in (12, 3):
            # Scalar reference independently computes interval intersection.
            def sigma(x):
                return shift * x / (1 + (shift - 1) * x)
            published = [sigma(1 - index / 8) for index in range(9)]
            torch.testing.assert_close(pdd_sigmas(shift), torch.tensor(published, dtype=torch.float64))
            for runtime in (published, [1, .98, .93, .79, .52, .21, .07, .01, 0]):
                fine = [1 - sigma(1 - index / 32) for index in range(33)]
                expected = []
                for left, right in zip(runtime[:-1], runtime[1:]):
                    a, b = 1 - left, 1 - right
                    expected.append([max(0, min(b, y) - max(a, x)) / (b - a)
                                     for x, y in zip(fine[:-1], fine[1:])])
                torch.testing.assert_close(pdd_sampling_plans_for_sigmas(runtime, shift),
                                           torch.tensor(expected, dtype=torch.float64), rtol=1e-10, atol=1e-10)
        with self.assertRaises(ValueError):
            pdd_sampling_plans_for_sigmas([1, .5, 0], 12)

    def test_coupled_fp32_output_and_constant_strength(self):
        admission, controller = self.load(.5)
        controller.configure_sigmas(pdd_sigmas(12), pdd_sigmas(3))
        hidden = torch.arange(8, dtype=torch.bfloat16)[None]
        state = load_file(str(self.path))
        for index in range(8):
            controller.set_step(index)
            for prefix, shift, head, original in (
                ("proj_out", 12, controller.video, self.original[0]),
                ("audio_proj_out", 3, controller.audio, self.original[1]),
            ):
                weights = pdd_sampling_plans_for_sigmas(pdd_sigmas(shift), shift)[index]
                weight = sum(float(w) * v.float() for w, v in zip(weights, state[prefix + ".weight"]))
                bias = sum(float(w) * v.float() for w, v in zip(weights, state[prefix + ".bias"]))
                expected = torch.lerp(torch.nn.functional.linear(hidden.float(), original.weight, original.bias),
                                      torch.nn.functional.linear(hidden.float(), weight, bias), .5)
                output = head(hidden)
                self.assertEqual(output.dtype, torch.float32)
                torch.testing.assert_close(output, expected)
        self.assertEqual(controller.video.step, controller.audio.step)
        with self.assertRaises(ValueError):
            controller.configure_sigmas(pdd_sigmas(12), torch.linspace(1, 0, 9))
        clear_pdd_runtime(self.model)
        self.assert_restored()

    def test_backbone_uses_existing_swiglu_order_and_rejects_unpaired_factors(self):
        state = artifact_state(self.model)
        state["transformer_blocks.0.ff.net.0.proj.lora_down"] = torch.ones(2, 8)
        state["transformer_blocks.0.ff.net.0.proj.lora_up"] = torch.arange(48, dtype=torch.float32).reshape(24, 2)
        save_file(state, str(self.path), metadata={"pdd_num_steps": "32", "pdd_block_size": "4"})
        admission = qualify_cpu_pdd(self.path, family="minimax_h3")
        prepare_pdd_runtime(self.model, admission)
        actual = self.model.preprocess_loras("minimax_h3", state)
        expected_up = torch.cat((state["transformer_blocks.0.ff.net.0.proj.lora_up"][12:],
                                 state["transformer_blocks.0.ff.net.0.proj.lora_up"][:12]))
        torch.testing.assert_close(actual["blocks.0.mlp.fc1.lora_B.weight"], expected_up)
        clear_pdd_runtime(self.model)
        del state["transformer_blocks.0.ff.net.0.proj.lora_up"]
        save_file(state, str(self.path), metadata={"pdd_num_steps": "32", "pdd_block_size": "4"})
        with self.assertRaisesRegex(H3PDDError, "roster"):
            qualify_cpu_pdd(self.path, family="minimax_h3")
        self.assert_restored()

    def test_setup_failure_restores_first_head_after_second_assignment_fails(self):
        class FailSecond(torch.nn.Module):
            def __setattr__(self, name, value):
                if name == "audio_out" and isinstance(value, PDDParallelHead):
                    raise InterruptedError("setup stopped")
                return super().__setattr__(name, value)
        layer = FailSecond()
        layer.video_out, layer.audio_out = self.original
        self.model.final_layer = layer
        with self.assertRaises(InterruptedError):
            install_pdd_heads(self.model, load_file(str(self.path)), strength=1)
        self.assert_restored()

    def test_changed_adapter_bytes_changes_identity_and_stale_admission_rejects(self):
        first = qualify_cpu_pdd(self.path, family="minimax_h3")
        identity = first.identity()
        self.write_artifact(2)
        with self.assertRaises(ValueError):
            first.verify()
        second = qualify_cpu_pdd(self.path, family="minimax_h3")
        self.assertNotEqual(identity, second.identity())

    def test_loaded_normalizer_change_invalidates_runtime_identity(self):
        from models.minimax_h3 import lora_affine
        admission = qualify_cpu_pdd(self.path, family="minimax_h3")
        identity = admission.identity()
        def replacement(*args, **kwargs):
            return {}
        replacement.__module__ = lora_affine.__name__
        with patch.object(lora_affine, "normalize_h3_lora_state_dict", replacement):
            with self.assertRaisesRegex(H3PDDError, "implementation changed"):
                admission.verify()
        self.assertEqual(admission.identity(), identity)

    def test_distinct_reference_family_and_closed_native_gate(self):
        reference = self.path.with_name(PDD_FILES["minimax_h3_ref2va"])
        reference.write_bytes(self.path.read_bytes())
        ref = qualify_cpu_pdd(reference, family="minimax_h3_ref2va")
        self.assertEqual(ref.family, "minimax_h3_ref2va")
        with self.assertRaises(H3PDDError):
            qualify_cpu_pdd(reference, family="minimax_h3")
        request = dict(model_type="minimax_h3", custom_settings={"h3_pdd_profile": PDD_PROFILE, "h3_attention_engine": "sdpa"},
                       activated_loras=[str(self.path)], loras_multipliers="1", num_inference_steps=8)
        self.assertEqual(validate_pdd_request(**request), 1)
        reference_request = request | {"model_type": "minimax_h3_ref2va",
                                       "activated_loras": [str(reference)], "audio_prompt_type": "AK"}
        self.assertEqual(validate_pdd_request(**reference_request), 1)
        with self.assertRaisesRegex(H3PDDError, "awaiting exact"):
            enforce_pdd_runtime(**reference_request)
        self.assertIsNone(enforce_pdd_runtime(model_type="ordinary", custom_settings={},
                          activated_loras=[object()], loras_multipliers=None,
                          num_inference_steps=20))
        with self.assertRaisesRegex(H3PDDError, "awaiting exact"):
            enforce_pdd_runtime(**request)
        for changes in ({"num_inference_steps": 7}, {"model_type": "minimax_h3_w4a8"},
                        {"loras_multipliers": "1;1"}, {"loras_multipliers": "nan"},
                        {"activated_loras": [str(self.path), "other.safetensors"]},
                        {"skip_steps_cache_type": "tea"}, {"cumulative": True},
                        {"native_boundary": True}, {"audio_prompt_type": "R"},
                        {"custom_settings": {"h3_pdd_profile": PDD_PROFILE, "h3_attention_engine": "sol_attn"}}):
            with self.subTest(changes=changes), self.assertRaises(H3PDDError):
                validate_pdd_request(**(request | changes))
        with patch.object(type(self.model), "parameters", return_value=iter([SimpleNamespace(device=SimpleNamespace(type="cuda"))])):
            with self.assertRaisesRegex(H3PDDError, "cannot authorize"):
                ref.verify(self.model)

    def test_actual_wgp_lora_load_failure_and_unload_restore_heads(self):
        tree = ast.parse((ROOT / "app/wgp.py").read_text())
        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        names = ("_unload_generation_loras", "_load_generation_loras")
        factory = ast.FunctionDef(name="factory", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
                                  body=[ast.parse("_generation_loras_loaded = False").body[0],
                                        *(functions[name] for name in names),
                                        ast.parse("return _load_generation_loras, _unload_generation_loras").body[0]], decorator_list=[])
        events = []
        admission = qualify_cpu_pdd(self.path, family="minimax_h3")
        def load(*args, **kwargs):
            events.append("load")
            kwargs["preprocess_sd"]("minimax_h3", load_file(str(self.path)))
        namespace = dict(offload=SimpleNamespace(load_loras_into_model=load,
                         unload_loras_from_model=lambda model: events.append("unload")),
                         trans_lora=self.model, trans=self.model, trans2_lora=None,
                         clear_h3_turbo_runtime=lambda model: None, clear_pdd_runtime=clear_pdd_runtime,
                         prepare_pdd_runtime=prepare_pdd_runtime, activate_pdd_runtime=activate_pdd_runtime,
                         pdd_admission=admission, loras_selected=[str(self.path)], loras_list_mult_choices_nums=[1],
                         loaded_profile=5, server_config={}, dasiwa_checkpoint_admission=None, turbo_assets=None,
                         wan_model=SimpleNamespace(device="cpu"), lightx2v_runtime_requested=False,
                         get_loras_preprocessor=lambda model, family: model.preprocess_loras,
                         base_model_type="minimax_h3", os=__import__("os"))
        self.model._loras_errors = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[factory], type_ignores=[])), "wgp-lifecycle", "exec"), namespace)
        load_owned, unload_owned = namespace["factory"]()
        load_owned()
        self.assertIsInstance(self.model.final_layer.video_out, PDDParallelHead)
        unload_owned()
        self.assert_restored()
        def fail(*args, **kwargs):
            load(*args, **kwargs)
            raise InterruptedError("owned load interrupted")
        namespace["offload"].load_loras_into_model = fail
        with self.assertRaises(InterruptedError):
            load_owned()
        self.assert_restored()
        self.assertEqual(events, ["load", "unload", "load", "unload"])
        namespace["offload"].load_loras_into_model = load
        load_owned()
        namespace["offload"].unload_loras_from_model = lambda model: (_ for _ in ()).throw(RuntimeError("unload failed"))
        with self.assertRaises(RuntimeError):
            unload_owned()
        self.assert_restored()

    def test_real_generate_uses_eight_paired_steps_and_restores_on_all_exits(self):
        from models.minimax_h3.minimax_h3_main import MiniMaxH3Model
        from models.minimax_h3.scheduler import MiniMaxH3Scheduler
        class Conditioner:
            def __call__(self, *args, **kwargs):
                return torch.zeros(1, 1, 4), torch.ones(1, dtype=torch.long)
        class VideoVAE:
            spatial_compression_ratio = 16
            def decode(self, latents, **kwargs):
                frames = (latents.shape[2] - 2) // 5 * 17 + 5
                return (torch.zeros(1, 3, frames, latents.shape[3] * 16, latents.shape[4] * 16),)
        class AudioVAE:
            def decode(self, latents, **kwargs):
                return (torch.ones(2, 1, latents.shape[-1] * 800),)
        model = object.__new__(MiniMaxH3Model)
        model.device, model.dtype = torch.device("cpu"), torch.float32
        model.model_def, model.selected_model_type, model.reference_mode = {}, "minimax_h3", False
        model.transformer, model.conditioner = self.model, Conditioner()
        model.vae, model.audio_vae = VideoVAE(), AudioVAE()
        model.scheduler, model.audio_scheduler = MiniMaxH3Scheduler(shift=12), MiniMaxH3Scheduler(shift=3)
        model._ref2va_handoff_cache = model._h3_cumulative_token = None
        options = dict(input_prompt="CPU fixture", height=64, width=64, frame_num=124,
                       sampling_steps=8, seed=123,
                       custom_settings={"h3_pdd_profile": PDD_PROFILE, "h3_attention_engine": "sdpa"})
        for outcome in ("success", "cancel", "failure"):
            admission, controller = self.load()
            calls = []
            def predict(transformer, **kwargs):
                calls.append((controller.video.step, controller.audio.step))
                transformer.final_layer.video_out(torch.ones(1, 8))
                transformer.final_layer.audio_out(torch.ones(1, 8))
                if len(calls) == 3 and outcome == "failure":
                    raise RuntimeError("paired predictor failed")
                if len(calls) == 3 and outcome == "cancel":
                    model._interrupt = True
                return torch.ones_like(kwargs["hidden_states"]), torch.ones_like(kwargs["audio_hidden_states"])
            self.model.forward = types.MethodType(predict, self.model)
            if outcome == "failure":
                with self.assertRaisesRegex(RuntimeError, "paired predictor failed"):
                    model.generate(**options)
            else:
                result = model.generate(**options)
                self.assertEqual(result is None, outcome == "cancel")
            self.assertEqual(calls, [(i, i) for i in range(8 if outcome == "success" else 3)])
            torch.testing.assert_close(model.scheduler.sigmas.double(), pdd_sigmas(12), rtol=1e-6, atol=1e-7)
            torch.testing.assert_close(model.audio_scheduler.sigmas.double(), pdd_sigmas(3), rtol=1e-6, atol=1e-7)
            self.assert_restored()
        self.load()
        calls_before = list(calls)
        model.device = torch.device("cuda")
        with self.assertRaisesRegex(H3PDDError, "native execution device"):
            model.generate(**options)
        self.assertEqual(calls, calls_before)
        self.assert_restored()
        model.device = torch.device("cpu")
        self.load()
        model.release()
        self.assertIsNone(model.transformer)
        self.assert_restored()


if __name__ == "__main__":
    unittest.main()
