from __future__ import annotations

import ast
import importlib.util
import json
import sys
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

HANDLER_PATH = APP_ROOT / "models" / "ltx25" / "ltx25_handler.py"
DEFAULT_PATH = APP_ROOT / "defaults" / "ltx2_25.json"
DEV_DEFAULT_PATH = APP_ROOT / "defaults" / "ltx2_25_dev.json"
NVFP4_DEFAULT_PATH = APP_ROOT / "defaults" / "ltx2_25_nvfp4.json"
LTX2_PATH = APP_ROOT / "models" / "ltx2" / "ltx2.py"
ATTENTION_PATH = APP_ROOT / "shared" / "attention.py"
DISTILLED_PATH = (
    APP_ROOT / "models" / "ltx2" / "ltx_pipelines" / "distilled.py"
)
WGP_PATH = APP_ROOT / "wgp.py"
LAUNCH_PATH = APP_ROOT / "launch.py"
PINOKIO_PATH = ROOT / "pinokio.js"
UPDATE_PATH = ROOT / "update.js"
REQUIREMENTS_PATH = APP_ROOT / "requirements.txt"
STORE_PATH = ROOT / "ui" / "src" / "stores" / "useStore.ts"
ADVANCED_PATH = (
    ROOT / "ui" / "src" / "components" / "Sidebar" / "AdvancedSettings.tsx"
)
INPUTS_PANEL_PATH = (
    ROOT / "ui" / "src" / "components" / "Sidebar" / "InputsPanel.tsx"
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LTX25HandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("torch") is None:
            raise unittest.SkipTest(
                "LTX-2.5 runtime integration tests require PyTorch"
            )
        cls.handler_module = _load_module("ltx25_handler_test", HANDLER_PATH)

    def test_missing_managed_component_does_not_require_an_account(self):
        with mock.patch.object(
            self.handler_module.fl,
            "locate_file",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                FileNotFoundError,
                "public and require no account",
            ) as raised:
                self.handler_module._locate_component(
                    "transformer",
                    "missing-test-component.safetensors",
                )

        self.assertNotIn("repository is gated", str(raised.exception))

    def test_model_definition_uses_separate_architecture(self):
        payload = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
        model = payload["model"]
        self.assertEqual(model["architecture"], "ltx2_25")
        self.assertIn("LTX-2.5", model["name"])
        self.assertEqual(model["ltx2_pipeline"], "distilled")
        self.assertEqual(payload["num_inference_steps"], 8)
        self.assertEqual(payload["guidance_scale"], 1.0)
        self.assertEqual((payload["video_length"] - 1) % 8, 0)

    def test_optional_dev_and_nvfp4_model_definitions_are_shipped(self):
        dev = json.loads(DEV_DEFAULT_PATH.read_text(encoding="utf-8"))
        nvfp4 = json.loads(NVFP4_DEFAULT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(dev["model"]["architecture"], "ltx2_25")
        self.assertEqual(dev["model"]["ltx2_pipeline"], "two_stage")
        self.assertIn("22b-dev_diffusion_model_bf16", dev["model"]["URLs"][0])
        self.assertIn(
            "22b-dev_diffusion_model_int8_convrot",
            dev["model"]["URLs"][1],
        )
        self.assertEqual(dev["num_inference_steps"], 30)
        self.assertEqual(dev["audio_guidance_scale"], 7.0)
        self.assertEqual(dev["sample_solver"], "euler")

        self.assertEqual(nvfp4["model"]["architecture"], "ltx2_25")
        self.assertEqual(nvfp4["model"]["ltx2_pipeline"], "distilled")
        self.assertEqual(len(nvfp4["model"]["URLs"]), 1)
        self.assertIn("diffusion_model_nvfp4", nvfp4["model"]["URLs"][0])
        self.assertEqual(nvfp4["num_inference_steps"], 8)

    def test_handler_matches_native_two_stage_contract(self):
        model_def = self.handler_module.family_handler.query_model_def(
            "ltx2_25", {}
        )
        self.assertEqual(model_def["fps"], 24)
        self.assertEqual(model_def["frames_steps"], 8)
        self.assertEqual(model_def["block_size"], 64)
        self.assertTrue(model_def["returns_audio"])
        self.assertTrue(model_def["auto_null_audio"])
        self.assertTrue(model_def["multimedia_generation"])
        self.assertTrue(model_def["lock_inference_steps"])
        self.assertTrue(model_def["ltx25_native_runtime"])
        self.assertNotIn("external_runtime", model_def)
        self.assertTrue(model_def["sliding_window"])
        self.assertTrue(model_def["video_continuation"])
        self.assertTrue(model_def["custom_frames_injection"])
        self.assertTrue(model_def["any_audio_prompt"])
        self.assertTrue(model_def["audio_guide_window_slicing"])
        self.assertTrue(model_def["infer_audio_prompt_from_guide"])
        self.assertTrue(model_def["sliding_window_audio_history"])
        self.assertTrue(model_def["sliding_window_end_image_at_final"])
        self.assertEqual(model_def["image_prompt_types_allowed"], "TSEVL")
        self.assertEqual(
            model_def["audio_prompt_type_sources"]["selection"],
            ["", "A", "K", "2"],
        )
        self.assertIn(
            ("Inject Frames", "KFI"),
            model_def["guide_custom_choices"]["choices"],
        )
        self.assertEqual(model_def["ltx2_pipeline"], "distilled")
        self.assertEqual(
            model_def["text_encoder_folder"],
            "gemma4-12b-ltx-v1",
        )
        self.assertEqual(
            model_def["resolution_presets"]["1080p"]["values"]["16:9"],
            "1920x1088",
        )
        self.assertEqual(model_def["ltx25_video_vae_default"], "fast")
        self.assertEqual(
            [
                choice["value"]
                for choice in model_def["ltx25_video_vae_choices"]
            ],
            ["fast", "nad"],
        )

    def test_dev_exposes_quality_controls_without_distilled_locks(self):
        model_def = self.handler_module.family_handler.query_model_def(
            "ltx2_25", {"ltx2_pipeline": "two_stage"}
        )
        self.assertEqual(model_def["ltx2_pipeline"], "two_stage")
        self.assertNotIn("lock_inference_steps", model_def)
        self.assertNotIn("lock_guidance_scale", model_def)
        self.assertTrue(model_def["audio_guidance"])
        self.assertTrue(model_def["adaptive_projected_guidance"])
        self.assertTrue(model_def["cfg_star"])
        self.assertTrue(model_def["perturbation"])
        self.assertFalse(model_def["no_negative_prompt"])
        self.assertEqual(model_def["visible_phases"], 1)

        defaults = {}
        self.handler_module.family_handler.update_default_settings(
            "ltx2_25", {"ltx2_pipeline": "two_stage"}, defaults
        )
        self.assertEqual(defaults["num_inference_steps"], 30)
        self.assertEqual(defaults["guidance_scale"], 3.0)
        self.assertEqual(defaults["audio_guidance_scale"], 7.0)
        self.assertEqual(defaults["sample_solver"], "euler")

    def test_pipeline_specific_validation_repairs_stale_settings(self):
        distilled_inputs = {
            "video_length": 241,
            "num_inference_steps": 30,
            "guidance_scale": 3.0,
            "audio_guidance_scale": 7.0,
        }
        self.handler_module.family_handler.validate_generative_settings(
            "ltx2_25",
            {"ltx2_pipeline": "distilled"},
            distilled_inputs,
        )
        self.assertEqual(distilled_inputs["num_inference_steps"], 8)
        self.assertEqual(distilled_inputs["guidance_scale"], 1.0)
        self.assertEqual(distilled_inputs["audio_guidance_scale"], 1.0)

        dev_inputs = {
            "video_length": 241,
            "num_inference_steps": 26,
            "guidance_scale": 2.5,
            "sample_solver": "unipc",
            "self_refiner_setting": 1,
        }
        self.handler_module.family_handler.validate_generative_settings(
            "ltx2_25", {"ltx2_pipeline": "two_stage"}, dev_inputs
        )
        self.assertEqual(dev_inputs["num_inference_steps"], 26)
        self.assertEqual(dev_inputs["guidance_scale"], 2.5)
        self.assertEqual(dev_inputs["sample_solver"], "euler")
        self.assertEqual(dev_inputs["self_refiner_setting"], 0)

    def test_video_vae_variant_normalization_is_strict(self):
        normalize = self.handler_module.normalize_video_vae_variant
        self.assertEqual(normalize(None), "fast")
        self.assertEqual(normalize("conv"), "fast")
        self.assertEqual(normalize("diffusion"), "nad")
        self.assertEqual(
            normalize("ltx-2.5-video-vae-conv-bf16.safetensors"),
            "fast",
        )
        with self.assertRaisesRegex(ValueError, "Choose 'fast' or 'nad'"):
            normalize("mystery")

    def test_saved_non_lattice_frame_count_is_repaired(self):
        inputs = {"video_length": 120}
        self.handler_module.family_handler.validate_generative_settings(
            "ltx2_25", {}, inputs
        )
        self.assertEqual((inputs["video_length"] - 1) % 8, 0)
        self.assertGreaterEqual(inputs["video_length"], 9)

    def test_audio_sample_count_accepts_common_waveform_layouts(self):
        import numpy as np

        tree = ast.parse(WGP_PATH.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_audio_waveform_sample_count"
        )
        namespace = {}
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(WGP_PATH),
                "exec",
            ),
            namespace,
        )
        count = namespace["_audio_waveform_sample_count"]
        self.assertEqual(count(np.zeros((2, 48_000), dtype=np.float32)), 48_000)
        self.assertEqual(count(np.zeros((48_000, 2), dtype=np.float32)), 48_000)
        self.assertEqual(count(np.zeros((48_000,), dtype=np.float32)), 48_000)
        self.assertEqual(count(np.zeros((2, 0), dtype=np.float32)), 0)

    def test_ltx_continuation_audio_accepts_generated_sample_major_tail(self):
        import numpy as np
        import torch

        tree = ast.parse(LTX2_PATH.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_prepare_ltx_audio_waveform"
        )
        namespace = {"torch": torch}
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(LTX2_PATH),
                "exec",
            ),
            namespace,
        )
        prepare = namespace["_prepare_ltx_audio_waveform"]

        sample_major_mono = np.zeros((12_000, 1), dtype=np.float32)
        sample_major_stereo = np.zeros((12_000, 2), dtype=np.float32)
        channel_major_stereo = np.zeros((2, 12_000), dtype=np.float32)

        self.assertEqual(tuple(prepare(sample_major_mono, 2).shape), (1, 1, 12_000))
        self.assertEqual(tuple(prepare(sample_major_stereo, 2).shape), (1, 2, 12_000))
        self.assertEqual(tuple(prepare(channel_major_stereo, 2).shape), (1, 2, 12_000))

    def test_standalone_soundtrack_is_durably_routed(self):
        launch = LAUNCH_PATH.read_text(encoding="utf-8")
        wgp = WGP_PATH.read_text(encoding="utf-8")
        store = STORE_PATH.read_text(encoding="utf-8")
        panel = INPUTS_PANEL_PATH.read_text(encoding="utf-8")
        self.assertIn('"infer_audio_prompt_from_guide"', launch)
        self.assertIn('model_def.get("infer_audio_prompt_from_guide"', wgp)
        self.assertIn("_audio_waveform_sample_count(input_waveform)", wgp)
        self.assertIn("infer_audio_prompt_from_guide === true", store)
        self.assertIn("params.audio_scale ?? 1.0", panel)
        self.assertIn("setParam('audio_scale'", panel)
        self.assertIn("Isolate vocals for better lip sync", panel)
        self.assertNotIn("modality_scale", panel)

    def test_preseparated_vocals_can_condition_without_replacing_soundtrack(self):
        wgp = WGP_PATH.read_text(encoding="utf-8")
        self.assertIn("audio_conditioning_guide=None", wgp)
        self.assertIn('"audio_conditioning_guide", "audio_source"', wgp)
        self.assertIn("original_audio_guide = audio_guide", wgp)
        self.assertIn(
            "original_audio_guide, audio_guide = _resolve_audio_guide_roles(",
            wgp,
        )
        self.assertIn("output_new_audio_filepath = original_audio_guide", wgp)

    def test_component_downloads_use_native_wangp_stack(self):
        definitions = self.handler_module.family_handler.query_model_files(
            lambda value: [value],
            "ltx2_25",
            {
                "URLs": [
                    "https://example/"
                    "ltx-2.5-22b-distilled_diffusion_model_"
                    "int8_convrot.safetensors"
                ]
            },
        )
        self.assertEqual(len(definitions), 2)
        self.assertTrue(
            all(
                item["repoId"] == "DeepBeepMeep/LTX-2"
                for item in definitions
            )
        )
        files = [
            name
            for group in definitions[0]["fileList"]
            for name in group
        ]
        self.assertIn("ltx-2.5-22b_video_vae_bf16.safetensors", files)
        self.assertIn(
            "ltx-2.5-22b_diffusion_video_vae_bf16.safetensors",
            files,
        )
        self.assertIn("ltx-2.5-22b_audio_vae_bf16.safetensors", files)
        self.assertIn(
            "ltx-2.5-spatial-upscaler-x2-1.0_bf16.safetensors",
            files,
        )
        self.assertIn(
            "ltx-2.5-22b_video_embeddings_connector_"
            "int8_convrot.safetensors",
            files,
        )
        self.assertEqual(
            definitions[1]["sourceFolderList"],
            ["gemma4-12b-ltx-v1"],
        )

    def test_nvfp4_definition_downloads_matching_connectors(self):
        definitions = self.handler_module.family_handler.query_model_files(
            lambda value: [value],
            "ltx2_25",
            json.loads(NVFP4_DEFAULT_PATH.read_text(encoding="utf-8"))["model"],
        )
        files = definitions[0]["fileList"][0]
        self.assertIn(
            "ltx-2.5-22b_video_embeddings_connector_nvfp4_bf16.safetensors",
            files,
        )
        self.assertIn(
            "ltx-2.5-22b_audio_embeddings_connector_nvfp4_bf16.safetensors",
            files,
        )
        self.assertNotIn(
            "ltx-2.5-22b_video_embeddings_connector_int8_convrot.safetensors",
            files,
        )

    def test_native_handler_uses_persistent_mmgp_and_dynamic_loras(self):
        handler = HANDLER_PATH.read_text(encoding="utf-8")
        ltx2 = LTX2_PATH.read_text(encoding="utf-8")
        wgp = WGP_PATH.read_text(encoding="utf-8")
        self.assertIn("from models.ltx2.ltx2 import LTX2", handler)
        self.assertIn("return model, pipe", handler)
        self.assertNotIn('return model, {"external_runtime": True', handler)
        self.assertIn("_attach_lora_preprocessor(self.diffuser_model)", ltx2)
        self.assertIn("LTX2_COMFY_LORA_UNDERSCORED_NAMES", ltx2)
        self.assertIn("offload.load_loras_into_model(", wgp)
        # Verify the actual reload decision rather than optional diagnostic copy.
        guards = [
            node for node in ast.walk(ast.parse(wgp))
            if isinstance(node, ast.If)
            and ast.unparse(node.test)
            == "model_type != transformer_type or reload_needed or profile != loaded_profile"
        ]
        self.assertEqual(len(guards), 1)
        self.assertTrue(any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "load_models"
            for statement in guards[0].body for node in ast.walk(statement)
        ))
        decision = compile(ast.Expression(guards[0].test), str(WGP_PATH), "eval")
        for model, profile, reload_needed, expected in (
            ("ltx2_25", 4, False, False),
            ("ltx2_25", 4, True, True),
            ("ltx2_25", 5, False, True),
            ("ltx2_25_dev", 4, False, True),
        ):
            with self.subTest(model=model, profile=profile, reload=reload_needed):
                self.assertEqual(eval(decision, {}, {
                    "model_type": model, "transformer_type": "ltx2_25",
                    "profile": profile, "loaded_profile": 4,
                    "reload_needed": reload_needed,
                }), expected)

    def test_native_ltx25_uses_wangp_ancestral_eight_plus_three_path(self):
        source = DISTILLED_PATH.read_text(encoding="utf-8")
        self.assertIn("LTX25EulerAncestralDiffusionStep", source)
        self.assertIn("elif use_ancestral_sampler:", source)
        sampler_branch = source.split(
            "# Keep the native LTX-2.5 ancestral trajectory", 1
        )[1].split("self_refiner_handler = None", 1)[0]
        self.assertIn(
            "elif use_ancestral_sampler:\n"
            "            stepper_stage2 = stepper",
            sampler_branch,
        )
        self.assertIn("int(seed) + 10000", source)

    def test_sliding_window_prefix_preserves_full_motion_history(self):
        source = LTX2_PATH.read_text(encoding="utf-8")
        prefix_block = source.split(
            "def _append_prefix_entries(target_list, extra_list=None):", 1
        )[1].split("def _append_suffix_entries", 1)[0]
        self.assertIn(
            "input_video[:, :frame_count].permute(1, 2, 3, 0)",
            prefix_block,
        )
        self.assertNotIn("frame_indices = list(range", prefix_block)
        self.assertNotIn("input_video[:, frame_idx]", prefix_block)

    def test_masked_sdpa_normalizes_bf16_mask_for_fp32_query(self):
        source = ATTENTION_PATH.read_text(encoding="utf-8")
        sdpa_block = source.split("def sdpa_wrapper(", 1)[1].split(
            "def get_attention_modes()", 1
        )[0]
        self.assertIn("torch.is_floating_point(attention_mask)", sdpa_block)
        self.assertIn(
            "attention_mask.dtype not in (torch.float32, q.dtype)",
            sdpa_block,
        )
        self.assertIn("dtype=q.dtype", sdpa_block)

    def test_obsolete_sidecar_is_not_advertised_or_updated(self):
        launcher = PINOKIO_PATH.read_text(encoding="utf-8")
        updater = UPDATE_PATH.read_text(encoding="utf-8")
        handler = HANDLER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("ltx25_install", launcher)
        self.assertNotIn("install_ltx25_runtime", updater)
        self.assertNotIn("external_runtime", handler)

    def test_video_vae_choice_is_exposed_and_sent_to_generation(self):
        launch = LAUNCH_PATH.read_text(encoding="utf-8")
        wgp = WGP_PATH.read_text(encoding="utf-8")
        store = STORE_PATH.read_text(encoding="utf-8")
        advanced = ADVANCED_PATH.read_text(encoding="utf-8")
        self.assertIn('"ltx25_video_vae_choices"', launch)
        self.assertIn('ltx25_video_vae="fast"', wgp)
        self.assertIn('model_kwargs["ltx25_video_vae"]', wgp)
        self.assertIn("params.ltx25_video_vae", store)
        self.assertIn("newParams.ltx25_video_vae", store)
        self.assertIn("LTX-2.5 Video Decoder", advanced)

    def test_ltx25_shares_ltx2_lora_directory(self):
        args = SimpleNamespace(lora_dir_ltx2="X:/shared-ltx-loras")
        self.assertEqual(
            self.handler_module.family_handler.get_lora_dir(
                "ltx2_25", args, "X:/loras"
            ),
            "X:/shared-ltx-loras",
        )

    def test_ltx25_is_available_to_compatible_director_workflows(self):
        from services.director_model_compat import assess_director_model

        model_def = self.handler_module.family_handler.query_model_def(
            "ltx2_25", {}
        )
        assessment = assess_director_model(
            "ltx2_25",
            model_def,
            family="ltx25",
            architecture="ltx2_25",
        )
        self.assertTrue(assessment["video"]["music_video"]["compatible"])
        self.assertTrue(
            assessment["video"]["short_film_audio"]["compatible"]
        )
        self.assertTrue(
            assessment["video"]["short_film_story"]["compatible"]
        )
        self.assertTrue(assessment["video"]["seamless"]["compatible"])
        self.assertFalse(assessment["supports_voice_reference"])

    def test_model_is_enabled_once_for_existing_and_fresh_installs(self):
        store = STORE_PATH.read_text(encoding="utf-8")
        default_block = store.split(
            "const DEFAULT_ENABLED_MODELS = new Set([", 1
        )[1].split("])\n", 1)[0]
        self.assertIn("'ltx2_25'", default_block)
        self.assertIn("10: ['ltx2_25']", store)
        self.assertNotIn("'ltx2_25_dev'", default_block)
        self.assertNotIn("'ltx2_25_nvfp4'", default_block)


class LTXConvRotRuntimeTests(unittest.TestCase):
    """CPU checkpoint/LoRA contracts; no model downloads or GPU inference."""

    @classmethod
    def setUpClass(cls):
        for dependency in ("torch", "optimum.quanto", "mmgp"):
            if importlib.util.find_spec(dependency) is None:
                raise unittest.SkipTest(f"ConvRot runtime needs {dependency}")
        import torch
        from mmgp import offload, quant_router
        from models.ltx2.ltx2 import LTX2
        from shared.qtypes import int8_convrot

        cls.torch, cls.router = torch, quant_router
        cls.handler, cls.ltx_class = int8_convrot, LTX2
        cls.scaling_method = staticmethod(offload.offload._get_lora_scaling)
        # Exercise the production registration list without starting WGP.
        tree = ast.parse(WGP_PATH.read_text(encoding="utf-8"))
        assignment = next(node for node in tree.body
                          if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "_HANDLER_MODULES"
                                  for t in node.targets))
        for handler in ast.literal_eval(assignment.value):
            quant_router.register_handler(handler)

    def _fixture(self, dtype=None, convrot=True, rows=3, features=4):
        torch = self.torch
        dtype = dtype or torch.float32
        weight = (torch.arange(rows * features).reshape(rows, features) % 9 - 4).to(torch.int8)
        scale = torch.arange(1, rows + 1).float() / 8
        bias = torch.arange(rows).to(dtype) / 10
        descriptor = {"format": "int8_tensorwise", "convrot": convrot,
                      "convrot_groupsize": 4}
        state = {"0.weight": weight.clone(), "0.weight_scale": scale.clone(),
                 "0.bias": bias.clone(), "0.comfy_quant": torch.tensor(
                     list(json.dumps(descriptor).encode()), dtype=torch.uint8)}
        converted = self.router.detect_and_convert(state, dtype, verboseLevel=0)
        self.assertEqual(converted["quant_map"]["0"]["weights"], "qint8_convrot")
        self.handler.apply_pre_quantization(None, converted["state_dict"],
                                            converted["quant_map"])
        layer = self.router.QLinearQuantoRouter(
            features, rows, bias=True, dtype=dtype,
            weights=self.handler._QINT8_CONVROT_QTYPE)
        model = torch.nn.Sequential(layer)
        model.load_state_dict(converted["state_dict"], strict=True)
        value = torch.tensor([[[.25, .5, -.75, 1.25], [-.5, .125, .875, -.25]]],
                             dtype=dtype).repeat(1, 1, features // 4)
        # Independently specify the checkpoint's orthonormal group-4 transform.
        rotation = torch.tensor([[1, 1, 1, -1], [1, 1, -1, 1],
                                 [1, -1, 1, 1], [-1, 1, 1, 1]], dtype=dtype) / 2
        rotation = torch.block_diag(*([rotation] * (features // 4)))
        original_weight = (weight.to(dtype) * scale.to(dtype)[:, None])
        if convrot:
            original_weight = original_weight @ rotation.T
        expected = torch.nn.functional.linear(value, original_weight, bias)
        return model, value, expected

    def test_registered_checkpoint_preserves_rotation_row_scales_and_dtype(self):
        torch = self.torch
        for dtype in (torch.float32, torch.float16, torch.bfloat16):
            for rotated in (False, True):
                with self.subTest(dtype=dtype, rotated=rotated), torch.inference_mode():
                    # Quanto CPU BF16 uses Torch's packed kernel, whose tiny
                    # 3x4 matrix tail crashes in the installed Torch build.
                    # Use a supported packed shape; FP32/FP16 retain tiny cases.
                    packed = dtype == torch.bfloat16
                    model, value, expected = self._fixture(
                        dtype, rotated, rows=16 if packed else 3,
                        features=16 if packed else 4)
                    actual = model(value)
                    self.assertEqual(actual.dtype, dtype)
                    torch.testing.assert_close(actual, expected, rtol=.01, atol=.01)

    def test_ltx_finalization_keeps_native_base_and_source_space_lora_once(self):
        torch = self.torch
        model, value, baseline = self._fixture()
        layer = model[0]
        native_forward = layer.forward
        layer._mm_lora_old_forward = native_forward
        # Reproduce an ordinary Linear hook that would omit the rotation.
        layer.forward = lambda x: torch.nn.functional.linear(x, layer.weight, layer.bias)
        lora_a = torch.tensor([[.2, -.1, .3, .4], [.1, .5, -.2, .3]])
        lora_b = torch.tensor([[.3, .5], [-.4, .6], [.7, -.1]])
        diff_b = torch.tensor([.1, -.2, .3])
        layer._mm_lora_data = {"fixture_GPU": (lora_a, lora_b, diff_b, None, 2., {})}
        model._loras_active_adapters = ["fixture"]
        model._loras_scaling = {"fixture": [.25, .75]}
        model._lora_step_no = 1
        layer._mm_lora_model = model
        layer._mm_manager = SimpleNamespace(_get_lora_scaling=lambda *args:
                                            self.scaling_method(None, *args))
        ltx = self.ltx_class.__new__(self.ltx_class)
        ltx.model = model
        expected = baseline + 1.5 * ((value @ lora_a.T) @ lora_b.T + diff_b)
        with torch.inference_mode():
            ltx.finalize_loras()
            torch.testing.assert_close(model(value), expected)
            ltx.finalize_loras()
            torch.testing.assert_close(model(value), expected)
            model._loras_scaling["fixture"] = 0
            torch.testing.assert_close(model(value), baseline)
            model._loras_scaling["fixture"] = 1
            model._loras_active_adapters = []
            torch.testing.assert_close(model(value), baseline)
            model._loras_active_adapters = ["fixture"]
            layer._mm_lora_data["fixture_GPU"] = (
                lora_a, lora_b, diff_b, torch.ones(3), 2., {})
            with self.assertRaisesRegex(RuntimeError, "DoRA adapters are not supported"):
                model(value)

    def test_fused_qkv_split_preserves_contiguous_rows_and_scales(self):
        torch = self.torch
        model, _, _ = self._fixture(rows=6)
        weight = model[0].weight
        state = {"attn.qkv.weight._data": weight._data,
                 "attn.qkv.weight._scale": weight._scale,
                 "attn.qkv.convrot_group_size": torch.tensor(4),
                 "attn.qkv.qweight": torch.empty(0, dtype=torch.uint8)}
        split, bases = self.handler.split_fused_weights(state, {"qkv": {
            "mapped_modules": ["q", "k", "v"], "split_sizes": [2, 2, 2]}})
        self.assertEqual(bases, ["attn.qkv"])
        self.assertNotIn("attn.qkv.qweight", split)
        for index, name in enumerate(("q", "k", "v")):
            torch.testing.assert_close(split[f"attn.{name}.weight._data"],
                                       weight._data[index * 2:index * 2 + 2])
            torch.testing.assert_close(split[f"attn.{name}.weight._scale"],
                                       weight._scale[index * 2:index * 2 + 2])
            self.assertEqual(split[f"attn.{name}.convrot_group_size"].item(), 4)


if __name__ == "__main__":
    unittest.main()
