"""Model-free and optional runtime regressions for MiniMax H3 support."""
from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
_HANDLER_PATH = _APP / "models" / "minimax_h3" / "minimax_h3_handler.py"
_MAIN_PATH = _APP / "models" / "minimax_h3" / "minimax_h3_main.py"
_PACKING_PATH = _APP / "models" / "minimax_h3" / "packing.py"
_TRANSFORMER_PATH = _APP / "models" / "minimax_h3" / "transformer.py"
_CONDITIONER_PATH = _APP / "models" / "minimax_h3" / "conditioner.py"
_CHECKPOINT_PATH = _APP / "models" / "minimax_h3" / "checkpoint.py"
_NVFP4_PATH = _APP / "shared" / "qtypes" / "nvfp4.py"
_WGP_PATH = _APP / "wgp.py"
_LAUNCH_PATH = _APP / "launch.py"
_LLM_SERVICE_PATH = _APP / "services" / "llm_service.py"
_DEFAULT_PATH = _APP / "defaults" / "minimax_h3.json"
_STORE_PATH = _ROOT / "ui" / "src" / "stores" / "useStore.ts"
_TYPES_PATH = _ROOT / "ui" / "src" / "types" / "index.ts"
_PROMPT_INPUT_PATH = _ROOT / "ui" / "src" / "components" / "Sidebar" / "PromptInput.tsx"
_MODEL_SELECTOR_PATH = _ROOT / "ui" / "src" / "components" / "Sidebar" / "ModelSelector.tsx"
_ADVANCED_SETTINGS_PATH = _ROOT / "ui" / "src" / "components" / "Sidebar" / "AdvancedSettings.tsx"
_ENHANCE_GUIDES_PATH = _APP / "services" / "enhance_guides.py"
_PROMPT_POLISH_PATH = _APP / "services" / "director" / "prompt_polish.py"
_H3_ENHANCE_GUIDE_PATH = _APP / "services" / "llm_guides" / "enhance" / "minimax_h3_video.md"
_H3_REF2VA_GUIDE_PATH = (
    _APP / "services" / "llm_guides" / "enhance" / "minimax_h3_ref2va_video.md"
)
_H3_DIALECT_GUIDE_PATH = _APP / "services" / "llm_guides" / "dialect" / "minimax_h3_video.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _HTTPException(Exception):
    def __init__(self, *, status_code: int, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _AdmissionRequest:
    def __init__(self, body: dict):
        self._body = body
        self.state = types.SimpleNamespace(maestro_remote=False)

    async def json(self) -> dict:
        return copy.deepcopy(self._body)


def _load_launch_functions(names: set[str], namespace: dict) -> dict:
    tree = ast.parse(_read(_LAUNCH_PATH), filename=str(_LAUNCH_PATH))
    selected = []
    for node in tree.body:
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ):
            continue
        node = copy.deepcopy(node)
        node.decorator_list = []
        selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    exec(
        compile(ast.fix_missing_locations(module), str(_LAUNCH_PATH), "exec"),
        namespace,
    )
    return namespace


def _w4a8_admission_namespace():
    """Load W4A8 admission paths without importing or invoking a model."""
    forbidden_events: list[str] = []
    project_access_permissions: list[str] = []
    failed_preparations: list[dict] = []
    finished_jobs: list[tuple] = []
    capability_probes: list[bool] = []
    worker_admissions: list[str] = []

    def forbidden(name: str):
        def reject(*args, **kwargs):
            forbidden_events.append(name)
            raise AssertionError(f"{name} ran after W4A8 rejection")

        return reject

    acceleration = types.ModuleType("services.h3_acceleration")

    def unavailable_status(*, probe_kernel: bool):
        capability_probes.append(probe_kernel)
        return {
            "w4a8": {
                "available": False,
                "reason": "model-free test runtime",
            },
        }

    acceleration.get_h3_acceleration_status = unavailable_status

    def require_project_generation(request, workspace, *, permission):
        if permission != "project.generate":
            raise AssertionError(f"unexpected project permission: {permission}")
        project_access_permissions.append(permission)
        return "/tmp/project"

    def require_h3_legal(model_types):
        if any(
            str(model_type or "").startswith("minimax_h3")
            for model_type in model_types or ()
        ):
            raise _HTTPException(
                status_code=451,
                detail="A separate written MiniMax H3 license is required",
            )

    namespace = {
        "Request": _AdmissionRequest,
        "_GenerationPreparationRequest": object,
        "HTTPException": _HTTPException,
        "copy": copy,
        "hashlib": __import__("hashlib"),
        "os": os,
        "time": types.SimpleNamespace(time=lambda: 1.0),
        "traceback": __import__("traceback"),
        "torch": types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: False),
        ),
        "wgp": types.SimpleNamespace(get_model_def=lambda model_type: {}),
        "_H3_LONG_STUDIO_MODELS": {
            "minimax_h3", "minimax_h3_w4a8_fl2va",
        },
        "_H3_W4A8_FL2VA_MODEL": "minimax_h3_w4a8_fl2va",
        "_H3_TURBO_BENCHMARK_REFERENCE_BYTES": 0,
        "_H3_TURBO_BENCHMARK_REFERENCE_SHA256": "",
        "_require_h3_native_boundary_experimental": lambda body: None,
        "_validate_h3_sampling_steps": lambda body: None,
        "_validate_h3_explicit_multiclip_request": lambda body: None,
        "_prepare_h3_long_studio_request": lambda body: None,
        "_validate_h3_lightx2v_recovery_identity": lambda body: None,
        "_get_active_workspace": lambda: "default",
        "_require_project_access": require_project_generation,
        "_require_h3_legal_execution": require_h3_legal,
        "_project_access_permissions": project_access_permissions,
        "_reject_client_h3_internal_state": lambda body: None,
        "_reject_client_h3_turbo_validation_controls": lambda body: None,
        "_authorize_generation_media_inputs": (
            lambda request, body, workspace: None
        ),
        "_require_remote_visible_models": lambda request, models: None,
        "_apply_h3_adaptive_checkpoint": lambda body: None,
        "_resolve_h3_style_workflow_request": lambda body: None,
        "_apply_h3_style_workflow_to_request": lambda body: None,
        "_normalize_video_prompt_type": lambda body: None,
        "_normalize_image_prompt_type": lambda body: None,
        "_jobs": {},
        "_credit_prepare_admission": lambda job: None,
        "_credit_prepare_dispatch": lambda job: None,
        "_credit_block_runtime_error": lambda job: None,
        "_CREDIT_INTERNAL_PARAMS": frozenset(),
        "CreditRuntimeError": ValueError,
        "EntitlementError": ValueError,
        "is_cancel_requested": lambda job: False,
        "update_preparation_job": lambda job, **updates: True,
        "fail_preparation": (
            lambda job, **updates: failed_preparations.append(dict(updates))
        ),
        "generation_slot": (
            lambda lock, job, **kwargs: contextlib.nullcontext(True)
        ),
        "_gen_lock": object(),
        "_active_gen_states": {"other-worker": {}},
        "_stamp_requested_generation_residency": lambda job, **kwargs: None,
        "try_start": lambda job, **kwargs: (
            worker_admissions.append(str(job.get("id") or "")) or True
        ),
        "_queue_recovery_delivery_pending": lambda job: None,
        "_queue_recovery_checkpoint": (
            lambda job, **updates: job.update(updates) or True
        ),
        "_hold_h3_job_for_legal_access": lambda job: job.update({
            "status": "queued",
            "queue_held": True,
            "recovery_state": "blocked",
            "reruns_denoise": False,
            "_recovery_reason_code": "h3_legal_access_required",
        }) or True,
        "_director_image_role_wire_mode": lambda body: "legacy",
        "_require_h3_offload_plan_parity": lambda job: None,
        "_require_job_model_recipe_terms": lambda job: None,
        "_apply_per_job_coefficient": lambda job: None,
        "finish_job": (
            lambda *args, **kwargs: finished_jobs.append((args, kwargs))
        ),
        "_restore_base_coefficient": lambda: None,
    }
    for name in (
        "_h3_estimate_context",
        "_validate_h3_turbo_estimate_context",
        "_require_h3_generation_terms",
        "_h3_generation_requirements",
        "write_sealed_request_manifest",
        "complete_preparation",
        "_start_generation_worker",
        "_ensure_versioned_model_current",
        "_ensure_h3_effective_models_current",
        "register_abort_state",
    ):
        namespace[name] = forbidden(name)
    _load_launch_functions(
        {
            "_trusted_h3_prepared_plan",
            "_require_job_runtime_model_admission",
            "_h3_job_model_types",
            "_h3_effective_model_types",
            "_require_h3_acceleration_available",
            "_plan_generation_submission",
            "preview_generation_plan",
            "_run_generation_preparation",
            "_run_generation",
        },
        namespace,
    )
    return (
        namespace,
        acceleration,
        forbidden_events,
        failed_preparations,
        finished_jobs,
        capability_probes,
        worker_admissions,
    )


def _load_handler_class():
    tree = ast.parse(_read(_HANDLER_PATH), filename=str(_HANDLER_PATH))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if any(name.startswith("_") for name in names):
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in (
            "_hf_url",
            "_is_reference_mode",
            "_required_runtime_asset_manifest",
            "_required_asset_filenames",
        ):
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "family_handler":
            selected.append(node)
    namespace = {
        "os": os,
        "torch": types.SimpleNamespace(bfloat16="bfloat16"),
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(_HANDLER_PATH), "exec"), namespace)
    return namespace["family_handler"]


def _load_frame_aligner():
    tree = ast.parse(_read(_WGP_PATH), filename=str(_WGP_PATH))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "align_model_frame_count"
    )
    namespace = {}
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(_WGP_PATH), "exec"), namespace)
    return namespace["align_model_frame_count"]


def _load_conditioner_marker_contract():
    tree = ast.parse(_read(_CONDITIONER_PATH), filename=str(_CONDITIONER_PATH))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if any(name.startswith("H3_") for name in names):
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_ensure_h3_marker_tokens":
            selected.append(node)
    namespace = {}
    module = ast.Module(body=selected, type_ignores=[])
    exec(
        compile(ast.fix_missing_locations(module), str(_CONDITIONER_PATH), "exec"),
        namespace,
    )
    return namespace


class _MarkerTokenizer:
    def __init__(
        self,
        *,
        id_overrides=None,
        encoded_overrides=None,
        decoded_overrides=None,
    ):
        self.additional_special_tokens = ["<legacy_special>"]
        self.ids = {
            "<d>": 151669,
            "</d>": 151670,
            "<|cutoff|>": 151671,
            "<|lyrics_start|>": 151672,
            "<|lyrics_end|>": 151673,
            "<|caption_start|>": 151674,
            "<|caption_end|>": 151675,
        }
        self.ids.update(id_overrides or {})
        self.encoded_overrides = encoded_overrides or {}
        self.decoded_overrides = decoded_overrides or {}
        self.calls = []
        self.length = 151936

    def __len__(self):
        return self.length

    def add_special_tokens(self, value, *, replace_additional_special_tokens):
        self.calls.append((copy.deepcopy(value), replace_additional_special_tokens))
        for token in value["additional_special_tokens"]:
            if token not in self.additional_special_tokens:
                self.additional_special_tokens.append(token)
        return 0

    def convert_tokens_to_ids(self, token):
        return self.ids[token]

    def encode(self, token, *, add_special_tokens):
        if add_special_tokens:
            raise AssertionError("H3 marker validation must use literal encoding")
        return self.encoded_overrides.get(token, [self.ids[token]])

    def decode(self, token_ids, *, skip_special_tokens, clean_up_tokenization_spaces):
        if skip_special_tokens or clean_up_tokenization_spaces:
            raise AssertionError("H3 marker validation must preserve literal markers")
        token_id = token_ids[0]
        expected = next(token for token, value in self.ids.items() if value == token_id)
        return self.decoded_overrides.get(token_id, expected)


class TestMiniMaxH3MarkerTokens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        contract = _load_conditioner_marker_contract()
        cls.ensure_markers = staticmethod(contract["_ensure_h3_marker_tokens"])
        cls.marker_ids = contract["H3_MARKER_TOKEN_IDS"]

    def test_registers_only_missing_markers_without_replacing_existing_specials(self):
        tokenizer = _MarkerTokenizer()
        tokenizer.additional_special_tokens.append("<d>")
        self.assertIs(self.ensure_markers(tokenizer), tokenizer)
        self.assertEqual(tokenizer.additional_special_tokens[0], "<legacy_special>")
        self.assertEqual(
            tokenizer.calls,
            [
                (
                    {"additional_special_tokens": list(self.marker_ids)[1:]},
                    False,
                )
            ],
        )
        self.ensure_markers(tokenizer)
        self.assertEqual(len(tokenizer.calls), 1)

    def test_rejects_marker_id_drift(self):
        tokenizer = _MarkerTokenizer(id_overrides={"<d>": 151668})
        with self.assertRaisesRegex(ValueError, "checkpoint requires 151669"):
            self.ensure_markers(tokenizer)

    def test_rejects_non_literal_marker_encoding_or_decoding(self):
        tokenizer = _MarkerTokenizer(encoded_overrides={"<d>": [1, 2]})
        with self.assertRaisesRegex(ValueError, "single ID 151669"):
            self.ensure_markers(tokenizer)

        tokenizer = _MarkerTokenizer(decoded_overrides={151669: "different"})
        with self.assertRaisesRegex(ValueError, "decode literally"):
            self.ensure_markers(tokenizer)

    def test_rejects_tokenizer_larger_than_checkpoint_embedding(self):
        tokenizer = _MarkerTokenizer()
        tokenizer.length = 151937
        with self.assertRaisesRegex(ValueError, "embedding bound"):
            self.ensure_markers(tokenizer)


class TestMiniMaxH3Definition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.handler = _load_handler_class()

    def test_default_model_is_pinned_and_consumer_friendly(self):
        defaults = json.loads(_DEFAULT_PATH.read_text(encoding="utf-8"))
        model = defaults["model"]
        self.assertEqual(model["architecture"], "minimax_h3")
        self.assertEqual(defaults["num_inference_steps"], 28)
        self.assertEqual(defaults["video_length"], 124)
        self.assertEqual(defaults["resolution"], "1344x768")
        self.assertIn("minimax_h3_fl2va_pruned_fp8_scaled.safetensors", model["URLs"][0])
        self.assertIn("0543966fbdce5ba05709a8f2031c94bdba629b4a", model["URLs"][0])

    def test_all_h3_variants_default_to_high_without_accelerators_or_delivery(self):
        for filename in (
            "minimax_h3.json",
            "minimax_h3_pinkcherry_fl2va.json",
            "minimax_h3_w4a8_fl2va.json",
            "minimax_h3_ref2va.json",
        ):
            with self.subTest(filename=filename):
                defaults = json.loads(
                    (_APP / "defaults" / filename).read_text(encoding="utf-8")
                )
                self.assertEqual(defaults["num_inference_steps"], 28)
                self.assertEqual(defaults["resolution"], "1344x768")
                self.assertEqual(
                    defaults["custom_settings"],
                    {"h3_attention_engine": "sol_attn"},
                )
                self.assertEqual(defaults["tea_cache"], 0)
                self.assertEqual(defaults["activated_loras"], [])
                self.assertEqual(defaults["loras_multipliers"], "")
                self.assertEqual(defaults["spatial_upsampling"], "")
                self.assertEqual(defaults["delivery_resolution"], "")
                self.assertEqual(defaults["delivery_fit"], "")

    def test_pinkcherry_conditioner_update_has_bounded_compatibility_policy(self):
        defaults = json.loads(
            (_APP / "defaults" / "minimax_h3_pinkcherry_fl2va.json").read_text(
                encoding="utf-8"
            )
        )
        policy = defaults["model"]["component_updates"][0]["compatibility"]
        self.assertEqual(policy["quantization_family"], "h3_int8_convrot")
        required = policy["required_tensors"]
        self.assertEqual(
            required["model.embed_tokens.comfy_quant"]["json_fields"],
            {"format": "int8_tensorwise"},
        )
        self.assertEqual(
            required["model.embed_tokens.weight"]["shape"],
            [151936, 5120],
        )
        self.assertEqual(
            required["model.embed_tokens.weight_scale"]["shapes"],
            [[], [1], [151936], [151936, 1]],
        )
        self.assertTrue(required["model.embed_tokens.weight_scale"]["finite_positive"])

        main_required = defaults["model"]["model_update"]["compatibility"]["required_tensors"]
        self.assertEqual(
            main_required["blocks.0.adaln_proj.linear.weight"]["variants"],
            [
                {"dtype": "I8", "shape": [96768, 2688]},
                {"dtypes": ["F16", "BF16"], "shape": [96768, 8]},
            ],
        )

    def test_handler_hydrates_fresh_defaults_from_high_profile(self):
        defaults = {
            "prompt": "keep me",
            "h3_adaptive_conditioning": False,
        }
        self.handler.update_default_settings("minimax_h3", {}, defaults)
        self.assertEqual(defaults["prompt"], "keep me")
        self.assertFalse(defaults["h3_adaptive_conditioning"])
        self.assertEqual(defaults["num_inference_steps"], 28)
        self.assertEqual(defaults["resolution"], "1344x768")
        self.assertEqual(
            defaults["custom_settings"],
            {"h3_attention_engine": "sol_attn"},
        )
        self.assertEqual(defaults["tea_cache"], 0)
        self.assertEqual(defaults["activated_loras"], [])
        self.assertEqual(defaults["loras_multipliers"], "")
        self.assertNotIn("model_type", defaults)

    def test_handler_exposes_base_fl2va_contract(self):
        model_def = self.handler.query_model_def("minimax_h3", {})
        self.assertEqual(
            self.handler.query_supported_types(),
            ["minimax_h3", "minimax_h3_ref2va", "minimax_h3_10eros_beta3"],
        )
        self.assertEqual((model_def["fps"], model_def["frames_minimum"]), (24, 124))
        self.assertEqual((model_def["frames_steps"], model_def["frames_maximum"]), (17, 345))
        self.assertEqual(
            (model_def["frame_alignment_modulus"], model_def["frame_alignment_remainder"]),
            (17, 5),
        )
        self.assertEqual(model_def["image_prompt_types_allowed"], "TSE")
        self.assertTrue(model_def["end_frames_always_enabled"])
        self.assertTrue(model_def["t2v_class"])
        self.assertTrue(model_def["i2v_class"])
        self.assertTrue(model_def["returns_audio"])
        self.assertTrue(model_def["no_negative_prompt"])
        self.assertFalse(model_def["sliding_window"])
        self.assertEqual(
            model_def["resolution_preset_order"],
            ["480p", "540p", "720p", "1080p"],
        )
        self.assertEqual(
            model_def["resolution_presets"]["720p"]["values"]["16:9"],
            "1280x704",
        )
        self.assertEqual(
            model_def["resolution_presets"]["768p"]["values"]["16:9"],
            "1344x768",
        )
        self.assertNotIn("768p", model_def["resolution_preset_order"])
        self.assertTrue(model_def["supports_auto_aspect"])
        self.assertEqual(
            model_def["auto_resolution_fallbacks"]["auto_1080p"],
            "1920x1088",
        )
        native_values = {value for _label, value in model_def["resolutions"]}
        for definition in model_def["resolution_presets"].values():
            self.assertTrue(
                set(definition["values"].values())
                - set(model_def["auto_resolution_budgets"])
                <= native_values,
            )
        self.assertIn("qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", model_def["text_encoder_URLs"][0])

    def test_ref2va_defaults_native_segment_max_without_lengthening_video(self):
        defaults = {}
        self.handler.update_default_settings("minimax_h3_ref2va", {}, defaults)
        self.assertEqual(defaults["video_length"], 124)
        self.assertEqual(defaults["sliding_window_size"], 345)

    def test_h3_reserves_transformer_activation_workspace(self):
        source = _read(_HANDLER_PATH)
        self.assertIn("_TRANSFORMER_WORKING_VRAM_MB = 10 * 1024", source)
        self.assertIn('"workingVRAM": {', source)
        self.assertIn('"transformer": _TRANSFORMER_WORKING_VRAM_MB', source)

    def test_all_auxiliary_downloads_are_revision_pinned(self):
        downloads = self.handler.query_model_files(lambda item: [item], "minimax_h3")
        self.assertEqual(len(downloads), 2)
        self.assertEqual(downloads[0]["repoId"], "Comfy-Org/MiniMax-H3")
        self.assertEqual(downloads[0]["revision"], "0543966fbdce5ba05709a8f2031c94bdba629b4a")
        self.assertEqual(downloads[0]["sourceFolderList"], ["vae"])
        self.assertIn("minimax_h3_video_vae_fp16.safetensors", downloads[0]["fileList"][0])
        self.assertIn("minimax_h3_audio_vae_fp32.safetensors", downloads[0]["fileList"][0])
        self.assertEqual(downloads[1]["repoId"], "MiniMaxAI/MiniMax-H3")
        self.assertEqual(downloads[1]["revision"], "5d9b308a59ab12e67147f191e184baf704185bd1")

    def test_maestro_registers_the_family_and_uses_its_native_frame_grid(self):
        source = _read(_WGP_PATH)
        self.assertIn('"models.minimax_h3.minimax_h3_handler"', source)
        self.assertIn("video_length = align_model_frame_count(video_length, model_def)", source)
        self.assertIn(
            "frame_num=align_model_frame_count(current_video_length, model_def, for_generation=True)",
            source,
        )
        self.assertIn('model_def.get("frames_maximum", None)', source)

    def test_h3_is_enabled_for_existing_and_fresh_installs(self):
        store = _read(_STORE_PATH)
        default_block = store.split("const DEFAULT_ENABLED_MODELS = new Set([", 1)[1].split("])\n", 1)[0]
        self.assertIn("'minimax_h3'", default_block)
        defaults_version = int(
            store.split("const DEFAULTS_VERSION = ", 1)[1].splitlines()[0]
        )
        self.assertGreaterEqual(defaults_version, 7)
        self.assertIn("6: ['minimax_h3']", store)
        self.assertIn("7: ['minimax_h3_ref2va']", store)
        self.assertIn('md.get("returns_audio", False)', _read(_LAUNCH_PATH))

    def test_h3_prompt_guides_cover_native_audio_and_director(self):
        self.assertIn('"minimax_h3": "minimax_h3_video.md"', _read(_ENHANCE_GUIDES_PATH))
        self.assertIn('"minimax_h3": "minimax_h3_video"', _read(_PROMPT_POLISH_PATH))
        enhance_guide = _read(_H3_ENHANCE_GUIDE_PATH)
        dialect_guide = _read(_H3_DIALECT_GUIDE_PATH)
        self.assertIn("joint video-and-audio", enhance_guide)
        for required in (
            "integrated_multimodal_description:",
            "overall_soundscape:",
            "non_diegetic_music:",
            "(S1)",
            "<d>[English]",
            "remain silent with their mouths closed",
        ):
            self.assertIn(required, enhance_guide)
        self.assertIn("but supplies no script", enhance_guide)
        self.assertIn("SOURCE AND CANON FIDELITY", enhance_guide)
        self.assertIn("immutable dialogue list", enhance_guide)
        self.assertIn("TIMED SILENCE AROUND DIALOGUE", enhance_guide)
        self.assertIn("idle staring", enhance_guide)
        self.assertIn("<d>[English] Exact words.</d>", dialect_guide)
        self.assertIn(
            "Preserve supplied dialogue verbatim. When speech is requested "
            "without a script",
            dialect_guide,
        )
        self.assertIn(
            "After the final line, keep mouths closed and extend or hold only "
            "the requested state and atmosphere.",
            dialect_guide,
        )
        self.assertIn("never invent them as filler", dialect_guide)

    def test_ref2va_prompt_guide_preserves_reference_and_audio_safety(self):
        self.assertIn(
            '"minimax_h3_ref2va": "minimax_h3_ref2va_video.md"',
            _read(_ENHANCE_GUIDES_PATH),
        )
        guide = _read(_H3_REF2VA_GUIDE_PATH)
        for required in (
            "<Picture 1>",
            "<Video 1>",
            "<Audio 1>",
            "subject_definitions:",
            "summary:",
            "retention_analysis:",
            "detailed_description:",
            "overall_soundscape:",
            "non_diegetic_music:",
            "fully_preserved",
            "fully_copy",
            "VOICE REFERENCE",
            "AUDIO REUSE / PERFORMANCE DRIVER",
            "immutable dialogue list",
            "TIMED SILENCE AROUND DIALOGUE",
            "do not authorize",
        ):
            self.assertIn(required, guide)

    def test_h3_enhance_path_preserves_context_ir_contract(self):
        launch = _read(_LAUNCH_PATH)
        llm_service = _read(_LLM_SERVICE_PATH)
        self.assertIn("needs_h3_context_ir", launch)
        self.assertIn("and not needs_h3_context_ir", launch)
        self.assertIn("and not explicit_guidance", launch)
        self.assertIn("is_h3_context_ir", llm_service)
        self.assertIn('mode in ("video", "avatar") and not is_h3_context_ir', llm_service)
        self.assertIn("CRITICAL MINIMAX H3 OUTPUT CONTRACT", llm_service)
        self.assertIn("1200 if is_h3_ref2va else 768", llm_service)

    def test_non_sliding_h3_enhance_request_stays_one_timeline(self):
        store = _read(_STORE_PATH)
        prompt_input = _read(_PROMPT_INPUT_PATH)
        self.assertIn("effectiveSlidingWindowGeometry(", store)
        self.assertIn("state.modelOptions", store)
        self.assertIn("supportsSlidingWindows = modelOptions?.sliding_window === true", prompt_input)
        self.assertIn("effectiveSlidingWindowGeometry(", prompt_input)

    def test_h3_attention_choice_round_trips_through_preferences_presets_and_outputs(self):
        store = _read(_STORE_PATH)
        advanced = _read(_APP / "../ui/src/components/Sidebar/AdvancedSettings.tsx")
        self.assertIn("maestro:h3-attention-engine", store)
        self.assertIn("custom_settings: { h3_attention_engine: 'sol_attn' }", store)
        self.assertIn("custom_settings: _restorableH3CustomSettings(params.custom_settings)", store)
        self.assertIn("_restorableH3CustomSettings(p.custom_settings)", store)
        self.assertIn("h3_attention_engine: restoredEngine", store)
        self.assertIn("localStorage.setItem(H3_ATTENTION_ENGINE_KEY, restoredEngine)", store)
        self.assertIn("localStorage.setItem(H3_ATTENTION_ENGINE_KEY, engine)", store)
        self.assertIn("event.target.value === 'sage2' ? 'sage2' : 'sol_attn'", advanced)
        self.assertIn("Official SageAttention2++ · {h3Acceleration?.sage2.validated ? 'tested for Base H3' : 'not yet tested'}", advanced)
        self.assertIn("params.model_type !== 'minimax_h3'", advanced)
        transformer = _read(_APP / "models/minimax_h3/transformer.py")
        main = _read(_APP / "models/minimax_h3/minimax_h3_main.py")
        self.assertIn("maybe_sage2_attention", transformer)
        self.assertIn('tensor_layout="NHD"', transformer)
        self.assertIn('is_causal=False', transformer)
        self.assertIn('{"sdpa", "sol_attn", "sage2"}', main)

    def test_spectrum_profile_type_and_custom_restore_key_stay_in_ui_parity(self):
        store = _read(_STORE_PATH)
        types_source = _read(_TYPES_PATH)
        restorable = store[
            store.index("const H3_RESTORABLE_CUSTOM_KEYS"):
            store.index("type H3AttentionEngine")
        ]
        self.assertIn("'h3_spectrum_profile'", restorable)
        profile_ids = types_source[
            types_source.index("export type H3PerformanceProfileId"):
            types_source.index("export type H3EstimateConfidence")
        ]
        self.assertIn("'spectrum_experimental'", profile_ids)

    def test_h3_style_workflow_route_admission_is_id_only_and_server_resolved(self):
        from services.h3_upstream_skills import builtin_catalog

        catalog = builtin_catalog()
        namespace = {
            "Request": object,
            "HTTPException": _HTTPException,
            "_H3_LONG_STUDIO_MODELS": {
                "minimax_h3", "minimax_h3_ref2va",
            },
            "_h3_skill_catalog_updater": types.SimpleNamespace(
                load=lambda: copy.deepcopy(catalog),
            ),
        }
        _load_launch_functions({
            "_resolve_h3_style_workflow_request",
            "_apply_h3_style_workflow_to_request",
        }, namespace)
        resolve = namespace["_resolve_h3_style_workflow_request"]
        apply = namespace["_apply_h3_style_workflow_to_request"]
        style_id = catalog["styles"][0]["id"]

        body = {
            "model_type": "minimax_h3",
            "prompt": "A freeform H3 scene.",
            "h3_style_workflow": style_id,
        }
        resolved = resolve(body)
        self.assertEqual(body["h3_style_workflow"], resolved)
        self.assertEqual(resolved["id"], style_id)
        self.assertEqual(resolved["catalog_revision"], "bundled")
        self.assertEqual(apply(body), "freeform")
        self.assertTrue(body["prompt"].startswith(
            f"H3 workflow guidance [{style_id}]:",
        ))

        omitted = {"model_type": "minimax_h3", "h3_style_workflow": ""}
        self.assertIsNone(resolve(omitted))
        self.assertNotIn("h3_style_workflow", omitted)
        for rejected in (
            {
                "model_type": "minimax_h3",
                "h3_style_workflow": {"id": style_id},
            },
            {
                "model_type": "other",
                "h3_style_workflow": style_id,
            },
            {
                "model_type": "minimax_h3",
                "h3_style_workflow": "unknown-style",
            },
            {
                "model_type": "minimax_h3",
                "h3_style_workflow": style_id,
                "h3_style_workflow_prompt_brief": "client brief",
            },
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(_HTTPException) as error:
                    resolve(rejected)
                self.assertEqual(error.exception.status_code, 400)

        launch = _read(_LAUNCH_PATH)
        self.assertIn(
            '_resolve_h3_style_workflow_request(body, model_field="video_model")',
            launch,
        )
        director_plan = launch.split(
            "async def director_v2_plan", 1,
        )[1].split("@api.", 1)[0]
        self.assertIn(
            "workflow = _resolve_h3_style_workflow_request(",
            director_plan,
        )
        self.assertIn('body, model_field="video_model"', director_plan)
        self.assertGreaterEqual(
            launch.count("_resolve_h3_style_workflow_request(body)"), 2,
        )
        self.assertIn(
            'planner_kwargs_base["h3_style_workflow_present"] = workflow is not None',
            launch,
        )

    def test_h3_style_workflow_route_exposes_persisted_refresh_failure(self):
        from services.h3_upstream_skills import builtin_catalog

        offline = builtin_catalog()
        offline.update({
            "update_status": "offline_fallback",
            "last_refresh_attempt_at": 200.0,
            "update_error": "offline",
        })
        namespace = {
            "_h3_skill_catalog_updater": types.SimpleNamespace(
                load=lambda: copy.deepcopy(offline),
            ),
        }
        _load_launch_functions({"h3_style_workflow_catalog"}, namespace)
        self.assertEqual(
            namespace["h3_style_workflow_catalog"](), offline,
        )

    def test_generation_enhancer_projection_validates_workflow_and_hides_brief(self):
        from services.h3_upstream_skills import (
            builtin_catalog,
            resolve_h3_style_workflow,
        )

        catalog = builtin_catalog()
        workflow = resolve_h3_style_workflow(
            catalog["styles"][0]["id"], catalog,
        )
        namespace = {
            "math": __import__("math"),
            "_H3_LONG_STUDIO_MODELS": {"minimax_h3"},
        }
        _load_launch_functions({"_generation_enhancement_request"}, namespace)
        project = namespace["_generation_enhancement_request"]({
            "model_type": "minimax_h3",
            "generation_mode": "video",
            "prompt": "a paper-crafted harbor",
            "visual_style": "hand-painted gouache",
            "h3_style_workflow": workflow,
            "_duration_seconds": 30,
        }, "project-a")
        self.assertIs(project["h3_style_workflow_present"], True)
        self.assertEqual(project["visual_style"], "hand-painted gouache")
        self.assertNotIn("h3_style_workflow", project)
        self.assertNotIn(workflow["prompt_brief"], json.dumps(project))

        drifted = copy.deepcopy(workflow)
        drifted["prompt_brief"] += " drift"
        with self.assertRaises(ValueError):
            namespace["_generation_enhancement_request"]({
                "model_type": "minimax_h3",
                "prompt": "a paper-crafted harbor",
                "h3_style_workflow": drifted,
            }, "project-a")

    def test_enhancer_route_trusts_workflow_presence_only_for_durable_generation(self):
        from services import llm_operations, llm_service

        captured = []

        class Request:
            def __init__(self, *, durable: bool):
                self.state = types.SimpleNamespace(
                    maestro_generation_preparation=durable,
                    maestro_cpu_text_operation="prompt_enhancement",
                )

            async def json(self):
                return {
                    "workspace": "project-a",
                    "prompt": "a paper-crafted harbor",
                    "mode": "video",
                    "model_type": "minimax_h3",
                    "visual_style": "hand-painted gouache",
                    "h3_style_workflow_present": True,
                }

        async def run_blocking(operation, *args, **kwargs):
            return operation(*args, **kwargs)

        def require_project_generation(_request, _workspace, *, permission):
            self.assertEqual(permission, "project.generate")

        namespace = {
            "Request": Request,
            "HTTPException": _HTTPException,
            "wgp": types.SimpleNamespace(server_config={
                "enhancer_enabled": 0,
                "services": {},
            }),
            "os": os,
            "json": json,
            "_promote_external_llm_request": lambda _request: None,
            "_request_project_workspace": lambda _request, value: value,
            "_require_project_access": require_project_generation,
            "_resolve_authorized_request_media": (
                lambda _request, path, _workspace: path
            ),
            "_explicit_llm_guidance_allowed": lambda _body: False,
            "_resolve_prompt_enhancer_selection": (
                lambda *_args, **_kwargs: ("test-enhancer", "cpu", False)
            ),
            "_resolved_local_response_assist": (
                lambda _body, _selection: None
            ),
            "_llm_route_progress_callback": lambda _request: None,
            "_run_authorized_llm_with_selection": (
                lambda _request, _selection, operation: operation()
            ),
        }
        _load_launch_functions({"llm_enhance_prompt"}, namespace)

        def enhance_prompt(**kwargs):
            captured.append(dict(kwargs))
            return "enhanced H3 prompt"

        with mock.patch.object(
            llm_operations, "run_blocking_shielded", side_effect=run_blocking,
        ), mock.patch.object(
            llm_service, "enhance_prompt", side_effect=enhance_prompt,
        ):
            durable = asyncio.run(namespace["llm_enhance_prompt"](
                Request(durable=True),
            ))
            public = asyncio.run(namespace["llm_enhance_prompt"](
                Request(durable=False),
            ))

        self.assertEqual(durable["enhanced"], "enhanced H3 prompt")
        self.assertEqual(public["enhanced"], "enhanced H3 prompt")
        self.assertIs(captured[0]["h3_style_workflow_present"], True)
        self.assertIs(captured[1]["h3_style_workflow_present"], False)
        self.assertEqual(captured[0]["visual_style"], "hand-painted gouache")

    def test_durable_generate_enhances_before_server_workflow_compilation(self):
        from services.h3_upstream_skills import (
            builtin_catalog,
            resolve_h3_style_workflow,
        )

        catalog = builtin_catalog()
        workflow = resolve_h3_style_workflow(
            catalog["styles"][0]["id"], catalog,
        )
        enhanced_prompt = (
            "subject_definitions:\n"
            "<Subject 1> is a paper harbor keeper.\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0.00s-5.00s] shot_name: Paper doorway | "
            "audiovisual_description: <Subject 1> opens a folded-paper door. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape:\nsoft paper movement.\n"
            "non_diegetic_music:\nnone."
        )
        captured = {}
        failures = []

        class PreparationRequest:
            def __init__(self, body=None):
                self.state = types.SimpleNamespace(
                    maestro_cpu_text_job=None,
                    maestro_cpu_text_operation=None,
                    maestro_cpu_text_text_only=False,
                )
                self._body = copy.deepcopy(body or {})

            def with_body(self, body):
                clone = PreparationRequest(body)
                clone.state = self.state
                return clone

            async def json(self):
                return copy.deepcopy(self._body)

        async def enhance(request):
            captured["enhance_body"] = await request.json()
            return {"enhanced": enhanced_prompt}

        job = {
            "id": "h3-style-enhance",
            "workspace": "project-a",
            "out_dir": "/tmp/project-a",
            "execution_attempt": 1,
            "params": {
                "model_type": "minimax_h3",
                "generation_mode": "video",
                "prompt": "a paper-crafted harbor",
                "visual_style": "",
                "h3_style_workflow": workflow,
            },
        }

        def prepare(body):
            captured["planned_body"] = copy.deepcopy(body)
            return None

        namespace = {
            "Request": object,
            "copy": copy,
            "asyncio": asyncio,
            "math": __import__("math"),
            "time": types.SimpleNamespace(time=lambda: 100.0),
            "_H3_LONG_STUDIO_MODELS": {"minimax_h3"},
            "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
            "_jobs": {job["id"]: job},
            "is_cancel_requested": lambda _job: False,
            "_queue_recovery_delivery_pending": lambda _job: None,
            "_h3_job_model_types": lambda _job: ("minimax_h3",),
            "_require_h3_legal_execution": lambda _models: None,
            "update_preparation_job": lambda _job, **_updates: True,
            "llm_enhance_prompt": enhance,
            "_require_h3_native_boundary_experimental": lambda _body: None,
            "_apply_fresh_h3_role_defaults": lambda _body, _request: "high",
            "_validate_h3_sampling_steps": lambda _body: None,
            "_validate_h3_explicit_multiclip_request": lambda _body: None,
            "_prepare_h3_long_studio_request": prepare,
            "_require_h3_acceleration_available": lambda _body, _plan: None,
            "_h3_estimate_context": lambda _body, _plan: {},
            "_validate_h3_turbo_estimate_context": lambda *_args, **_kwargs: None,
            "_validate_h3_spectrum_estimate_context": lambda _context: None,
            "_validate_h3_lightx2v_estimate_context": lambda _context: None,
            "_local_owner_may_run_unvalidated_h3_turbo_ref2va": (
                lambda _request: False
            ),
            "_require_remote_visible_models": lambda _request, _models: None,
            "_h3_effective_model_types": lambda _body, _plan: [],
            "_require_h3_generation_terms": lambda _body, _plan: None,
            "_h3_profile_estimate_payload": lambda *_args, **_kwargs: {
                "current": {"estimate": {}},
            },
            "_h3_generation_requirements": lambda _body, _plan: {
                "ref2va_terms_required": False,
                "checkpoint_options": [],
            },
            "_remote_visible_model_ids": lambda _request: None,
            "_public_h3_long_plan": lambda _plan, _requirements: None,
            "_seal_h3_offload_plan_for_job": lambda _params: None,
            "_queue_recovery_input_descriptors": lambda *_args: [],
            "write_sealed_request_manifest": lambda *_args, **_kwargs: {
                "path": "request.json",
            },
            "complete_preparation": lambda *_args, **_kwargs: False,
            "remove_request_manifest": lambda *_args, **_kwargs: None,
            "fail_preparation": (
                lambda *_args, **kwargs: failures.append(dict(kwargs))
            ),
        }
        _load_launch_functions({
            "_apply_authoritative_generation_prompt",
            "_generation_enhancement_request",
            "_apply_h3_style_workflow_to_request",
            "_plan_generation_submission",
            "_run_generation_preparation",
        }, namespace)
        namespace["_run_generation_preparation"](
            job["id"], PreparationRequest(), enhance=True,
        )

        enhance_body = captured["enhance_body"]
        planned_body = captured["planned_body"]
        self.assertIs(enhance_body["h3_style_workflow_present"], True)
        self.assertNotIn("h3_style_workflow", enhance_body)
        self.assertNotIn(workflow["prompt_brief"], json.dumps(enhance_body))
        self.assertEqual(failures, [])
        self.assertEqual(planned_body["h3_style_workflow"], workflow)
        self.assertIn(
            f"H3 workflow guidance [{workflow['id']}]",
            planned_body["prompt"],
        )
        self.assertNotIn("photorealistic realism", planned_body["prompt"])

    def test_h3_omitted_defaults_are_role_aware_and_explicit_steps_win(self):
        namespace = {
            "Request": object,
            "copy": copy,
            "_H3_LONG_STUDIO_MODELS": {"minimax_h3"},
        }
        _load_launch_functions({"_apply_fresh_h3_role_defaults"}, namespace)
        apply_defaults = namespace["_apply_fresh_h3_role_defaults"]

        owner_request = types.SimpleNamespace(
            state=types.SimpleNamespace(
                maestro_account_principal={"role": "owner"},
            ),
        )
        owner_body = {"model_type": "minimax_h3"}
        self.assertEqual(apply_defaults(owner_body, owner_request), "high")
        self.assertEqual(owner_body["num_inference_steps"], 28)
        self.assertEqual(owner_body["resolution"], "1344x768")

        user_request = types.SimpleNamespace(
            state=types.SimpleNamespace(
                maestro_account_principal={"role": "user"},
            ),
        )
        user_body = {
            "model_type": "minimax_h3",
            "num_inference_steps": 32,
            "resolution": "768x768",
            "custom_settings": {"h3_attention_engine": "sdpa"},
        }
        self.assertEqual(apply_defaults(user_body, user_request), "quality")
        self.assertEqual(user_body["num_inference_steps"], 32)
        self.assertEqual(user_body["resolution"], "768x768")
        self.assertEqual(
            user_body["custom_settings"]["h3_attention_engine"], "sdpa",
        )

        fresh_user_body = {"model_type": "minimax_h3"}
        apply_defaults(fresh_user_body, user_request)
        self.assertEqual(fresh_user_body["num_inference_steps"], 23)
        self.assertEqual(fresh_user_body["resolution"], "960x544")

    def test_h3_legal_gate_precedes_w4a8_planning_and_worker_work(self):
        launch = _read(_LAUNCH_PATH)
        selector = _read(_MODEL_SELECTOR_PATH)
        advanced = _read(_ADVANCED_SETTINGS_PATH)
        self.assertIn("def _require_h3_acceleration_available", launch)
        self.assertEqual(launch.count("_require_h3_acceleration_available("), 3)
        tree = ast.parse(launch, filename=str(_LAUNCH_PATH))
        shared_planning_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_plan_generation_submission"
        )
        shared_planning = ast.get_source_segment(
            launch, shared_planning_node,
        )
        self.assertIn("_require_h3_acceleration_available(body, plan)", shared_planning)
        worker_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_run_generation"
        )
        worker = ast.get_source_segment(launch, worker_node)
        self.assertIn("_require_h3_acceleration_available(\n                    raw_params", worker)

        (
            admission,
            acceleration,
            forbidden_events,
            failed,
            finished,
            probes,
            worker_admissions,
        ) = _w4a8_admission_namespace()
        rejected_body = {
            "workspace": "default",
            "model_type": "minimax_h3_w4a8_fl2va",
            "prompt": "model-free admission probe",
            "image_mode": 2,
        }
        with mock.patch.dict(
            sys.modules,
            {"services.h3_acceleration": acceleration},
        ):
            with self.assertRaises(_HTTPException) as preview_error:
                asyncio.run(admission["preview_generation_plan"](
                    _AdmissionRequest(rejected_body),
                ))
            self.assertEqual(worker_admissions, [])
            preparation_job = {
                "id": "w4a8-preparation",
                "params": copy.deepcopy(rejected_body),
                "workspace": "default",
                "out_dir": "/tmp/project",
            }
            admission["_jobs"] = {"w4a8-preparation": preparation_job}
            admission["_run_generation_preparation"](
                "w4a8-preparation",
                _AdmissionRequest({}),
                enhance=False,
            )
            self.assertEqual(worker_admissions, [])
            worker_job = {
                "id": "w4a8-worker",
                "params": copy.deepcopy(rejected_body),
                "status": "queued",
                "out_dir": "",
            }
            admission["_jobs"] = {"w4a8-worker": worker_job}
            self.assertFalse(admission["_run_generation"]("w4a8-worker"))

        self.assertEqual(preview_error.exception.status_code, 451)
        self.assertIn(
            "separate written MiniMax H3 license",
            preview_error.exception.detail,
        )
        self.assertEqual(
            admission["_project_access_permissions"],
            ["project.generate"],
        )
        self.assertEqual(probes, [])
        self.assertEqual(worker_admissions, [])
        self.assertEqual(forbidden_events, [])
        self.assertEqual(failed, [])
        self.assertEqual(finished, [])
        for held in (preparation_job, worker_job):
            self.assertEqual(held["status"], "queued")
            self.assertIs(held["queue_held"], True)
            self.assertEqual(held["recovery_state"], "blocked")
            self.assertIs(held["reruns_denoise"], False)
            self.assertEqual(
                held["_recovery_reason_code"],
                "h3_legal_access_required",
            )
        self.assertIn("w4a8Capability?.available !== true", selector)
        self.assertIn("disabled={w4a8Unavailable || legalBlocked}", selector)
        self.assertIn("setH3Custom('h3_sol_dense_steps', 0)", advanced)

    def test_legal_block_parks_waiting_plan_before_terms_arm_timer_or_approval(self):
        sys.path.insert(0, str(_APP))
        try:
            from services.host_terms import (
                REF2VA_TERM,
                accept_host_term,
                host_term_accepted,
            )
        finally:
            sys.path.pop(0)
        services = {}
        accept_host_term(
            services,
            REF2VA_TERM,
            1,
            accepted_at="2026-08-15T00:00:00Z",
        )
        self.assertTrue(host_term_accepted(services, REF2VA_TERM))
        holds = []
        project_checks = []

        def legal_block(_model_types):
            raise _HTTPException(
                status_code=451,
                detail="A separate written MiniMax H3 license is required",
            )

        def hold(job):
            holds.append(str(job.get("id") or ""))
            job.update({
                "status": "queued",
                "queue_held": True,
                "recovery_state": "blocked",
                "reruns_denoise": False,
                "_recovery_reason_code": "h3_legal_access_required",
            })
            return True

        def waiting_job(*, deadline=None):
            return {
                "id": "held-plan",
                "status": "waiting_for_plan_approval",
                "params": {"model_type": "minimax_h3_ref2va"},
                "plan_review_required": True,
                "plan_review_terms_required": True,
                "plan_review_deadline": deadline,
            }

        def project_is_current(_job):
            project_checks.append(str(_job.get("id") or ""))
            return True

        job = waiting_job()
        namespace = {
            "HTTPException": _HTTPException,
            "math": __import__("math"),
            "threading": threading,
            "time": types.SimpleNamespace(time=lambda: 2.0),
            "hmac": __import__("hmac"),
            "copy": copy,
            "_jobs": {job["id"]: job},
            "_plan_review_timer_lock": threading.Lock(),
            "_plan_review_timers": {},
            "_plan_terms_reconciliation_lock": threading.RLock(),
            "_PLAN_REVIEW_TIMEOUT_SECONDS": 16.0,
            "_ref2va_host_terms_accepted": lambda: host_term_accepted(
                services, REF2VA_TERM,
            ),
            "_require_h3_legal_execution": legal_block,
            "_h3_job_model_types": lambda _job: ("minimax_h3_ref2va",),
            "_hold_h3_job_for_legal_access": hold,
            "_waiting_plan_project_is_current": project_is_current,
            "_queue_recovery_revalidate_job": lambda _job: True,
            "arm_prepared_job_plan_review": (
                lambda *_args, **_kwargs: self.fail(
                    "plan arm ran after legal block",
                )
            ),
            "fail_preparation": lambda *_args, **_kwargs: self.fail(
                "legal block became terminal preparation failure",
            ),
        }
        _load_launch_functions({
            "_arm_ref2va_waiting_plan_review",
            "_reconcile_ref2va_waiting_plan_reviews",
            "_approve_waiting_generation_plan",
            "_expire_plan_review",
            "_schedule_plan_review_auto_approval",
        }, namespace)

        namespace["_reconcile_ref2va_waiting_plan_reviews"](
            schedule_timers=True,
        )
        self.assertEqual(job["_recovery_reason_code"], "h3_legal_access_required")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(namespace["_plan_review_timers"], {})
        self.assertEqual(project_checks, ["held-plan"])

        job.update(waiting_job())
        self.assertFalse(namespace["_arm_ref2va_waiting_plan_review"](
            job, deadline=10.0,
        ))
        self.assertEqual(job["_recovery_reason_code"], "h3_legal_access_required")
        self.assertEqual(project_checks, ["held-plan"])

        job.update(waiting_job(deadline=1.0))
        namespace["_schedule_plan_review_auto_approval"](job)
        self.assertEqual(job["_recovery_reason_code"], "h3_legal_access_required")
        self.assertEqual(namespace["_plan_review_timers"], {})

        job.update(waiting_job(deadline=1.0))
        namespace["_expire_plan_review"](job["id"])
        self.assertEqual(job["_recovery_reason_code"], "h3_legal_access_required")
        self.assertEqual(job["status"], "queued")
        self.assertGreaterEqual(holds.count("held-plan"), 4)

    def test_frame_aligner_preserves_h3_and_legacy_grids(self):
        align = _load_frame_aligner()
        h3 = {
            "frames_minimum": 124,
            "frames_maximum": 345,
            "frame_alignment_modulus": 17,
            "frame_alignment_remainder": 5,
            "frame_alignment_mode": "ceil",
            "latent_size": 17,
        }
        self.assertEqual([align(value, h3) for value in (1, 120, 124, 125, 345, 999)], [124, 124, 124, 141, 345, 345])
        legacy = {"latent_size": 4, "frames_steps": 4}
        self.assertEqual(align(120, legacy), 117)
        self.assertEqual(align(120, legacy, for_generation=True), 121)


class TestMiniMaxH3RuntimeSource(unittest.TestCase):
    def test_runtime_uses_the_official_dual_scheduler_and_audio_output(self):
        main = _read(_MAIN_PATH)
        self.assertIn("MiniMaxH3Scheduler(shift=12.0)", main)
        self.assertIn("MiniMaxH3Scheduler(shift=3.0)", main)
        self.assertIn("audio_sampling_rate\": 32000", main)
        self.assertIn("MINIMAX_H3_KEYFRAME_ENCODE_SEED", main)
        self.assertIn("prepare_keyframe_image", main)

    def test_native_preview_reuses_the_final_video_decode_recipe(self):
        main = _read(_MAIN_PATH)
        helper = main.split("def _decode_h3_video_rows", 1)[1].split(
            "def _first_path", 1
        )[0]
        preview = main.split("def decode_h3_preview_rows", 1)[1].split(
            "def _encode_keyframes", 1
        )[0]
        final_decode = main.split('report_phase("Decoding H3 video")', 1)[1].split(
            'report_phase("Decoding H3 audio")', 1
        )[0]

        self.assertIn("unpatchify_video_tokens(", helper)
        self.assertIn("VIDEO_LATENTS_MEAN", helper)
        self.assertIn("VIDEO_LATENTS_STD", helper)
        self.assertIn("vae.decode(denormalized_latents", helper)
        self.assertIn("MINIMAX_H3_PIXEL_MEAN", helper)
        self.assertIn("MINIMAX_H3_PIXEL_STD", helper)
        self.assertIn("_decode_h3_video_rows(", preview)
        self.assertIn("_decode_h3_video_rows(", final_decode)
        self.assertNotIn("vae.decode(", preview)
        self.assertNotIn("vae.decode(", final_decode)

    def test_consumer_checkpoint_shapes_are_kept_native(self):
        transformer = _read(_TRANSFORMER_PATH)
        conditioner = _read(_CONDITIONER_PATH)
        self.assertIn("self.qkv_proj", transformer)
        self.assertIn("self.fc1", transformer)
        self.assertIn("adaln_t_table", transformer)
        self.assertIn("curve_dim: int = 8", transformer)
        self.assertIn("TEXT_ENCODER_LAYERS = 50", conditioner)
        self.assertIn("class MiniMaxH3Int8Embedding", conditioner)
        self.assertIn("pre_quant_scale", conditioner)
        self.assertIn("self.model.norm = nn.Identity()", conditioner)
        self.assertIn("attention_mask=attention_mask,", conditioner)
        self.assertIn("native causal attention", conditioner)
        self.assertIn("dtype=torch.float32", transformer)

    def test_compact_vae_adapters_and_nvfp4_awq_scale_are_present(self):
        checkpoint = _read(_CHECKPOINT_PATH)
        nvfp4 = _read(_NVFP4_PATH)
        self.assertIn("_reorder_interleaved_qkv", checkpoint)
        self.assertIn("weight_g", checkpoint)
        self.assertIn("weight_v", checkpoint)
        self.assertIn('qmodule.register_buffer(\n                "pre_quant_scale"', nvfp4)
        self.assertIn("input = input * pre_quant_scale.to", nvfp4)

    def test_conditioner_loader_preserves_mixed_quantization_contract(self):
        main = _read(_MAIN_PATH)
        checkpoint = _read(_CHECKPOINT_PATH)
        self.assertIn("preprocess_sd=preprocess_conditioner_state_dict", main)
        self.assertIn("with init_empty_weights(include_buffers=False):", main)
        self.assertIn('descriptor.get("format") != "int8_tensorwise"', checkpoint)
        self.assertIn('state_dict.pop(f"{prefix}.comfy_quant", None)', checkpoint)

    def test_h3_loaders_materialize_nonpersistent_runtime_buffers(self):
        main = _read(_MAIN_PATH)
        video_loader = main.split("def _load_video_vae", 1)[1].split("def _load_audio_vae", 1)[0]
        audio_loader = main.split("def _load_audio_vae", 1)[1].split("class MiniMaxH3Model", 1)[0]
        self.assertIn("init_empty_weights(include_buffers=False)", video_loader)
        self.assertIn("init_empty_weights(include_buffers=False)", audio_loader)

    def test_upstream_provenance_is_recorded(self):
        provenance = _read(_APP / "models" / "minimax_h3" / "UPSTREAM.md")
        self.assertIn("abc5e9bf71fd38f53cd471bc3acaa84bc5ecbfdc", provenance)
        self.assertIn("5d9b308a59ab12e67147f191e184baf704185bd1", provenance)
        self.assertIn("0543966fbdce5ba05709a8f2031c94bdba629b4a", provenance)
        self.assertIn("Apache-2.0", provenance)


class TestMiniMaxH3ConditionerCheckpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(_APP))
        import torch

        cls.torch = torch

    @classmethod
    def tearDownClass(cls):
        if sys.path and sys.path[0] == str(_APP):
            sys.path.pop(0)

    def _state_dict(self, scale, *, weight=None, marker=None):
        if weight is None:
            weight = self.torch.tensor(
                [[1, -2, 3], [4, 5, -6], [-7, 8, 9], [10, -11, 12]],
                dtype=self.torch.int8,
            )
        if marker is None:
            marker = self.torch.tensor(
                list(b'{"format":"int8_tensorwise"}'), dtype=self.torch.uint8,
            )
        return {
            "model.embed_tokens.comfy_quant": marker,
            "model.embed_tokens.weight": weight,
            "model.embed_tokens.weight_scale": scale,
        }

    def test_marked_scalar_and_row_scales_normalize_to_contiguous_rows(self):
        from models.minimax_h3.checkpoint import preprocess_conditioner_state_dict

        cases = (
            (self.torch.tensor(0.25), [0.25] * 4),
            (self.torch.tensor([0.25]), [0.25] * 4),
            (self.torch.tensor([0.5, 0.25, 2.0, 0.125]), [0.5, 0.25, 2.0, 0.125]),
            (self.torch.tensor([[0.5], [0.25], [2.0], [0.125]]), [0.5, 0.25, 2.0, 0.125]),
        )
        for scale, expected in cases:
            with self.subTest(shape=tuple(scale.shape)):
                processed = preprocess_conditioner_state_dict(self._state_dict(scale))
                normalized = processed["model.embed_tokens.weight_scale"]
                self.assertEqual(tuple(normalized.shape), (4, 1))
                self.assertTrue(normalized.is_contiguous())
                self.assertEqual(normalized[:, 0].tolist(), expected)
                self.assertNotIn("model.embed_tokens.comfy_quant", processed)

        processed = preprocess_conditioner_state_dict(
            self._state_dict(self.torch.tensor([0.25]))
        )
        embedding = self.torch.nn.Module()
        embedding.register_parameter(
            "weight",
            self.torch.nn.Parameter(
                self.torch.empty(4, 3, dtype=self.torch.int8), requires_grad=False,
            ),
        )
        embedding.register_parameter(
            "weight_scale",
            self.torch.nn.Parameter(self.torch.empty(4, 1), requires_grad=False),
        )
        embedding.load_state_dict(
            {
                "weight": processed["model.embed_tokens.weight"],
                "weight_scale": processed["model.embed_tokens.weight_scale"],
            },
            assign=True,
        )
        ids = self.torch.tensor([3, 0, 2])
        actual = self.torch.nn.functional.embedding(ids, embedding.weight).float()
        actual *= self.torch.nn.functional.embedding(ids, embedding.weight_scale)
        self.assertTrue(
            self.torch.equal(
                actual,
                processed["model.embed_tokens.weight"][ids].float() * 0.25,
            )
        )

    def test_composed_convrot_and_conditioner_preprocessors_preserve_embedding_marker(self):
        from models.minimax_h3.checkpoint import preprocess_conditioner_state_dict
        from models.minimax_h3.convrot import adapt_int8_convrot_state_dict

        torch = self.torch

        class TinyQwen(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.model = torch.nn.Module()
                self.model.embed_tokens = torch.nn.Embedding(4, 3)

        state_dict = self._state_dict(self.torch.tensor([0.25]))
        adapted = adapt_int8_convrot_state_dict(
            TinyQwen(), state_dict, output_dtype=self.torch.float32,
        )
        self.assertIn("model.embed_tokens.comfy_quant", adapted)
        processed = preprocess_conditioner_state_dict(adapted)
        self.assertNotIn("model.embed_tokens.comfy_quant", processed)
        self.assertEqual(
            tuple(processed["model.embed_tokens.weight_scale"].shape),
            (4, 1),
        )

    def test_unmarked_floating_embedding_retains_fallback(self):
        from models.minimax_h3.checkpoint import preprocess_conditioner_state_dict

        weight = self.torch.randn(4, 3)
        processed = preprocess_conditioner_state_dict({"model.embed_tokens.weight": weight})
        self.assertTrue(
            self.torch.equal(
                processed["model.embed_tokens.weight_scale"],
                self.torch.ones(4, 1),
            )
        )
        malformed = {"model.embed_tokens.weight": self.torch.randn(4)}
        self.assertNotIn(
            "model.embed_tokens.weight_scale",
            preprocess_conditioner_state_dict(malformed),
        )

    def test_marked_embedding_rejects_malformed_metadata_weights_and_scales(self):
        from models.minimax_h3.checkpoint import preprocess_conditioner_state_dict

        valid_scale = self.torch.tensor(0.25)
        invalid_cases = (
            (
                self._state_dict(
                    valid_scale,
                    marker=self.torch.tensor(list(b"[]"), dtype=self.torch.uint8),
                ),
                "invalid quantization metadata",
            ),
            (
                self._state_dict(
                    valid_scale,
                    marker=self.torch.tensor(
                        list(b'{"format":"nvfp4"}'), dtype=self.torch.uint8,
                    ),
                ),
                "unsupported quantization format",
            ),
            (
                self._state_dict(
                    valid_scale,
                    weight=self.torch.ones(4, 3, dtype=self.torch.int32),
                ),
                "two-dimensional INT8",
            ),
            (
                self._state_dict(
                    valid_scale,
                    weight=self.torch.ones(2, 2, 3, dtype=self.torch.int8),
                ),
                "two-dimensional INT8",
            ),
            (
                self._state_dict(self.torch.tensor([0.25, 0.5])),
                "expected a scalar or per-row scale",
            ),
            (self._state_dict(self.torch.tensor(0.0)), "finite and positive"),
            (self._state_dict(self.torch.tensor(float("nan"))), "finite and positive"),
            (self._state_dict(self.torch.tensor(1, dtype=self.torch.int32)), "floating-point scales"),
        )
        for state_dict, message in invalid_cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                preprocess_conditioner_state_dict(state_dict)


def _gpu_runtime_available():
    if not all(
        importlib.util.find_spec(name) is not None
        for name in ("torch", "diffusers", "transformers")
    ):
        return False
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


_RUNTIME_AVAILABLE = _gpu_runtime_available()


@unittest.skipUnless(_RUNTIME_AVAILABLE, "MiniMax H3 CUDA runtime is not available")
class TestMiniMaxH3RuntimeMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(_APP))
        import torch

        cls.torch = torch

    @classmethod
    def tearDownClass(cls):
        if sys.path and sys.path[0] == str(_APP):
            sys.path.pop(0)

    def test_video_patch_round_trip_and_scheduler_length(self):
        from models.minimax_h3.packing import patchify_video_latents, unpatchify_video_tokens
        from models.minimax_h3.scheduler import MiniMaxH3Scheduler

        source = self.torch.arange(1 * 2 * 3 * 4 * 6, dtype=self.torch.float32).reshape(1, 2, 3, 4, 6)
        rows = patchify_video_latents(source, (1, 2, 2))
        restored = unpatchify_video_tokens(rows, 3, 4, 6, 2, (1, 2, 2))
        self.assertTrue(self.torch.equal(source, restored))

        scheduler = MiniMaxH3Scheduler(shift=12.0)
        scheduler.set_timesteps(20, device="cpu")
        self.assertEqual(len(scheduler.sigmas), 20)
        self.assertEqual(len(scheduler.timesteps), 19)
        self.assertEqual(float(scheduler.timesteps[0]), 0.0)
        self.assertEqual(float(scheduler.sigmas[-1]), 0.0)

    def test_keyframe_normalization_stays_on_cpu_with_non_cpu_default_device(self):
        from models.minimax_h3.minimax_h3_main import _keyframe_latent_stats_cpu

        previous_device = self.torch.get_default_device()
        try:
            # Maestro runs with a CUDA default device. ``meta`` reproduces the
            # constructor-routing behavior without requiring a GPU in CI.
            self.torch.set_default_device("meta")
            means, stds = _keyframe_latent_stats_cpu()
        finally:
            self.torch.set_default_device(previous_device)

        self.assertEqual(means.device.type, "cpu")
        self.assertEqual(stds.device.type, "cpu")
        self.assertEqual(tuple(means.shape), (1, 24, 1, 1, 1))
        self.assertEqual(tuple(stds.shape), (1, 24, 1, 1, 1))
        self.assertEqual(means.dtype, self.torch.float32)
        self.assertEqual(stds.dtype, self.torch.float32)

    def test_tiny_joint_transformer_forward(self):
        from models.minimax_h3.transformer import MiniMaxH3Transformer

        model = MiniMaxH3Transformer(
            hidden_size=8,
            num_layers=1,
            token_refiner_layers=1,
            num_attention_heads=1,
            attention_head_dim=8,
            ffn_dim=12,
            video_channels=2,
            audio_channels=3,
            patch_size=(1, 1, 1),
            text_dim=6,
            curve_grid=4,
            curve_dim=2,
            rope_freq_dim=1,
            dtype=self.torch.float32,
        ).eval()
        # Production weights replace this table from the checkpoint.  The
        # tiny model has no checkpoint, so initialize its empty placeholder
        # to keep the numerical smoke test deterministic.
        model.adaln_t_table.data.zero_()
        self.assertEqual(model.video_patch_proj._lock_dtype, self.torch.float32)
        self.assertEqual(model.audio_patch_proj._lock_dtype, self.torch.float32)
        self.assertEqual(model.blocks[0].adaln_proj.linear._lock_dtype, self.torch.float16)
        self.assertEqual(model.final_layer.adaln_proj.linear._lock_dtype, self.torch.float16)
        self.assertEqual(model.final_layer.video_out._lock_dtype, self.torch.float32)
        self.assertEqual(model.final_layer.audio_out._lock_dtype, self.torch.float32)
        video_rows = self.torch.randn(1, 3, 2)
        audio_rows = self.torch.randn(1, 4, 3)
        text_rows = self.torch.randn(1, 2, 6)
        position_ids = self.torch.zeros(9, 3, dtype=self.torch.float64)
        token_tags = self.torch.tensor([1, 1, 2, 2, 2, 2, 0, 0, 0])
        timestep_indices = self.torch.tensor([0, 0, 1, 1, 1, 1, 0, 0, 0])
        video, audio = model(
            hidden_states=video_rows,
            audio_hidden_states=audio_rows,
            encoder_hidden_states=text_rows,
            timestep=self.torch.tensor([0.1, 0.4]),
            timestep_indices=timestep_indices,
            token_tags=token_tags,
            position_ids=position_ids,
            video_indices=self.torch.tensor([6, 7, 8]),
            audio_indices=self.torch.tensor([2, 3, 4, 5]),
            text_indices=self.torch.tensor([0, 1]),
            return_dict=False,
        )
        self.assertEqual(tuple(video.shape), (1, 3, 2))
        self.assertEqual(tuple(audio.shape), (1, 4, 3))
        self.assertTrue(self.torch.isfinite(video).all())
        self.assertTrue(self.torch.isfinite(audio).all())

    def test_tiny_full_timestep_transformer_forward(self):
        from models.minimax_h3.transformer import MiniMaxH3Transformer

        model = MiniMaxH3Transformer(
            hidden_size=8,
            num_layers=1,
            token_refiner_layers=1,
            num_attention_heads=1,
            attention_head_dim=8,
            ffn_dim=12,
            video_channels=2,
            audio_channels=3,
            patch_size=(1, 1, 1),
            text_dim=6,
            curve_grid=None,
            curve_dim=6,
            timestep_input_dim=4,
            time_embed_hidden_size=8,
            rope_freq_dim=1,
            dtype=self.torch.float32,
        ).eval()
        self.assertTrue(model.config.full_timestep)
        self.assertFalse(model.use_adaln_curves)
        self.assertTrue(model.blocks[0].adaln_proj.apply_silu)
        self.assertEqual(tuple(model.blocks[0].adaln_proj.linear.weight.shape), (144, 6))
        video, audio = model(
            hidden_states=self.torch.randn(1, 3, 2),
            audio_hidden_states=self.torch.randn(1, 4, 3),
            encoder_hidden_states=self.torch.randn(1, 2, 6),
            timestep=self.torch.tensor([0.1, 0.4]),
            timestep_indices=self.torch.tensor([0, 0, 1, 1, 1, 1, 0, 0, 0]),
            token_tags=self.torch.tensor([1, 1, 2, 2, 2, 2, 0, 0, 0]),
            position_ids=self.torch.zeros(9, 3, dtype=self.torch.float64),
            video_indices=self.torch.tensor([6, 7, 8]),
            audio_indices=self.torch.tensor([2, 3, 4, 5]),
            text_indices=self.torch.tensor([0, 1]),
            return_dict=False,
        )
        self.assertEqual(tuple(video.shape), (1, 3, 2))
        self.assertEqual(tuple(audio.shape), (1, 4, 3))
        self.assertTrue(self.torch.isfinite(video).all())
        self.assertTrue(self.torch.isfinite(audio).all())

    def test_chunked_h3_projections_match_unchunked_math(self):
        import models.minimax_h3.transformer as h3_transformer

        attention = h3_transformer.MiniMaxH3Attention(8, 1, 8, 1e-5, self.torch.float32).eval()
        mlp = h3_transformer.MiniMaxH3MLP(8, 12, self.torch.float32).eval()
        hidden = self.torch.randn(1, 7, 8)
        positions = self.torch.zeros(7, 3)
        rotary = h3_transformer.MiniMaxH3RotaryEmbedding(1)(positions)

        previous = h3_transformer.MINIMAX_H3_ACTIVATION_CHUNK_TOKENS
        try:
            with self.torch.inference_mode():
                h3_transformer.MINIMAX_H3_ACTIVATION_CHUNK_TOKENS = 64
                expected_attention = attention(hidden, rotary)
                expected_mlp = mlp(hidden)
                h3_transformer.MINIMAX_H3_ACTIVATION_CHUNK_TOKENS = 2
                actual_attention = attention(hidden, rotary)
                actual_mlp = mlp(hidden)
        finally:
            h3_transformer.MINIMAX_H3_ACTIVATION_CHUNK_TOKENS = previous

        self.assertTrue(self.torch.allclose(actual_attention, expected_attention, atol=1e-5, rtol=1e-5))
        self.assertTrue(self.torch.allclose(actual_mlp, expected_mlp, atol=1e-5, rtol=1e-5))

    def test_curve_adaln_uses_fp32_math_with_compact_fp16_storage(self):
        from models.minimax_h3.transformer import MiniMaxH3AdaLNProjection

        projection = MiniMaxH3AdaLNProjection(2, 2, 2, 1, self.torch.float16).eval()
        projection.linear.weight.data.copy_(
            self.torch.tensor(
                [[0.3333, -1.777], [2.125, 0.03125], [-0.8125, 1.333], [3.141, -2.718]],
                dtype=self.torch.float16,
            )
        )
        projection.linear.bias.data.copy_(
            self.torch.tensor([0.125, -0.25, 0.375, -0.5], dtype=self.torch.float16)
        )
        curve = self.torch.tensor([[0.12345, -0.98765]], dtype=self.torch.float32)
        chunks = projection(curve)
        actual = self.torch.cat(chunks, dim=-1)
        expected = self.torch.nn.functional.linear(
            curve,
            projection.linear.weight.float(),
            projection.linear.bias.float(),
        )

        self.assertEqual(projection.linear.weight.dtype, self.torch.float16)
        self.assertEqual(actual.dtype, self.torch.float32)
        self.assertTrue(self.torch.equal(actual, expected))

    def test_nvfp4_pre_quant_scale_loads_and_affects_forward(self):
        from models.minimax_h3.conditioner import MiniMaxH3PreScaledLinear
        from shared.qtypes.nvfp4 import QLinearNVFP4, _NVFP4_QTYPE

        source = MiniMaxH3PreScaledLinear(3, 2, bias=True, dtype=self.torch.float32)
        qmodule = QLinearNVFP4.qcreate(source, _NVFP4_QTYPE, device="cpu")
        qmodule.weight = self.torch.nn.Parameter(
            self.torch.tensor([[1.0, 2.0, 3.0], [-1.0, 0.5, 2.0]])
        )
        qmodule.bias = self.torch.nn.Parameter(self.torch.tensor([0.25, -0.5]))

        scale = self.torch.tensor([2.0, 3.0, 4.0])
        missing_keys, unexpected_keys, error_messages = [], [], []
        state_dict = {"pre_quant_scale": scale.clone()}
        qmodule._load_from_state_dict(
            state_dict,
            "",
            {},
            False,
            missing_keys,
            unexpected_keys,
            error_messages,
        )
        self.assertTrue(self.torch.equal(qmodule.pre_quant_scale, scale))
        self.assertNotIn("pre_quant_scale", state_dict)

        input_rows = self.torch.tensor([[1.0, 1.0, 1.0]])
        expected = self.torch.nn.functional.linear(
            input_rows * scale,
            qmodule.weight,
            qmodule.bias,
        )
        self.assertTrue(self.torch.equal(qmodule(input_rows), expected))

        # MMGP's quant router transfers ordinary handler attributes but omits
        # registered buffers. Simulate that transfer and prove the mirrored
        # scale still governs the routed forward path.
        self.assertTrue(self.torch.equal(qmodule._nvfp4_pre_quant_scale, scale))
        del qmodule._buffers["pre_quant_scale"]
        self.assertFalse(hasattr(qmodule, "pre_quant_scale"))
        self.assertTrue(self.torch.equal(qmodule(input_rows), expected))

    def test_nvfp4_fallback_matches_official_combined_scale_order(self):
        from shared.qtypes.nvfp4 import (
            _NVFP4_LAYOUT_TENSORCORE,
            _dequantize_nvfp4_weight,
        )

        # TensorCore scale tiles require 128 output rows and 64 input
        # channels at minimum.  0xFF decodes to two -6.0 FP4 values.
        packed_weight = self.torch.full((128, 32), 0xFF, dtype=self.torch.uint8)
        block_scale = self.torch.full(
            (128, 4),
            0.00099945068359375,
            dtype=self.torch.bfloat16,
        )
        tensor_scale = self.torch.tensor(0.0030059814453125, dtype=self.torch.float32)
        actual = _dequantize_nvfp4_weight(
            packed_weight,
            block_scale,
            self.torch.ones((), dtype=self.torch.float32),
            tensor_scale,
            self.torch.bfloat16,
            self.torch.device("cpu"),
            layout=_NVFP4_LAYOUT_TENSORCORE,
        )
        expected_value = self.torch.tensor(-6.0, dtype=self.torch.bfloat16) * (
            block_scale[0, 0] * tensor_scale.to(self.torch.bfloat16)
        )
        old_order_value = (
            self.torch.tensor(-6.0, dtype=self.torch.bfloat16) * block_scale[0, 0]
        ) * tensor_scale.to(self.torch.bfloat16)

        self.assertTrue(self.torch.equal(actual, self.torch.full_like(actual, expected_value)))
        self.assertNotEqual(expected_value.item(), old_order_value.item())

    def test_row_scaled_int8_embedding_loads_and_dequantizes_selected_rows(self):
        from models.minimax_h3.checkpoint import preprocess_conditioner_state_dict
        from models.minimax_h3.conditioner import MiniMaxH3Int8Embedding

        weight = self.torch.tensor(
            [[1, -2, 3], [4, 5, -6], [-7, 8, 9], [10, -11, 12]],
            dtype=self.torch.int8,
        )
        scales = self.torch.tensor([0.5, 0.25, 2.0, 0.125], dtype=self.torch.float32)
        marker = self.torch.tensor(
            list(b'{"format":"int8_tensorwise"}'),
            dtype=self.torch.uint8,
        )
        state_dict = {
            "model.embed_tokens.comfy_quant": marker,
            "model.embed_tokens.weight": weight.clone(),
            "model.embed_tokens.weight_scale": scales.clone(),
        }
        processed = preprocess_conditioner_state_dict(state_dict)
        self.assertNotIn("model.embed_tokens.comfy_quant", processed)
        self.assertEqual(tuple(processed["model.embed_tokens.weight_scale"].shape), (4, 1))

        embedding = MiniMaxH3Int8Embedding(4, 3, None, self.torch.float32)
        embedding.load_state_dict(
            {
                "weight": processed["model.embed_tokens.weight"],
                "weight_scale": processed["model.embed_tokens.weight_scale"],
            },
            assign=True,
        )
        input_ids = self.torch.tensor([[3, 0, 2, 3]])
        expected = weight[input_ids].float() * scales[input_ids].unsqueeze(-1)
        self.assertTrue(self.torch.equal(embedding(input_ids), expected))
        self.assertFalse(embedding.weight.requires_grad)
        self.assertEqual(embedding._lock_dtype, self.torch.float32)


if __name__ == "__main__":
    unittest.main()
