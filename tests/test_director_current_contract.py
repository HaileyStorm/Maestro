"""Focused regressions for Director's current model and preview contracts."""
from __future__ import annotations

import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services import director_model_compat as compat  # noqa: E402
from services import director_pipeline as pipeline  # noqa: E402
from services.director_video_strategy import (  # noqa: E402
    BOUNDED_START_END,
    ROLLING_WINDOW,
    build_director_video_execution_profile,
    video_strategy,
)


LAUNCH_PATH = APP_DIR / "launch.py"
TYPES_PATH = ROOT / "ui" / "src" / "types" / "index.ts"


class _HTTPException(Exception):
    def __init__(self, *, status_code: int, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _Request:
    def __init__(self, body: dict | None = None):
        self.body = body or {}
        self.state = type(
            "State", (), {"maestro_llm_progress_callback": None},
        )()

    async def json(self):
        return dict(self.body)


class _JSONResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


def _launch_functions_namespace(function_names, **extras):
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    selected = []
    for name in function_names:
        function = next(
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )
        function = copy.deepcopy(function)
        function.decorator_list = []
        selected.append(function)
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Request": _Request,
        "JSONResponse": _JSONResponse,
        "HTTPException": _HTTPException,
        "traceback": __import__("traceback"),
        **extras,
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    return namespace


def _director_plan_namespace(fail_operation):
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "director_v2_plan"
    )
    function = copy.deepcopy(function)
    function.decorator_list = []
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Request": _Request,
        "HTTPException": _HTTPException,
        "_authorize_director_media_inputs": lambda request, body: None,
        "_resolve_direct_llm_selection": lambda request: {},
        "_run_authorized_llm_with_selection": (
            lambda request, selection, operation: fail_operation(operation)
        ),
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    return namespace


def _blocking_shield_stub(invocations):
    async def run_blocking_shielded(function, /, *args, **kwargs):
        invocations.append((function, args, kwargs))
        return function(*args, **kwargs)

    return run_blocking_shielded


def _list_models_namespace(registry):
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "list_models"
    )
    function = copy.deepcopy(function)
    function.decorator_list = []
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)

    class _Updater:
        def apply_recorded(self, model_type, model_def):
            return None

        def apply_recorded_components(self, model_type, model_def):
            return None

    namespace = {
        "Request": _Request,
        "wgp": registry,
        "_remote_visible_model_ids": lambda request: None,
        "_versioned_model_updater": _Updater(),
        "_versioned_model_update_status": {},
        "_check_model_downloaded": lambda model_type: True,
        "_public_manual_installation_manifest": lambda model_def: None,
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    return namespace


def _image_editor() -> dict:
    return {
        "name": "Reference editor",
        "image_outputs": True,
        "image_ref_choices": {
            "choices": [
                ("None", ""),
                ("Main plus references", "KI"),
            ],
        },
    }


def _ltx_video(**updates) -> dict:
    model = {
        "name": "LTX",
        "architecture": "ltx2_22B",
        "image_prompt_types_allowed": "TSEV",
        "sliding_window": True,
        "any_audio_prompt": True,
        "returns_audio": True,
        "audio_guide_window_slicing": True,
        "custom_frames_injection": True,
        "auto_null_audio": True,
    }
    model.update(updates)
    return model


def _h3_video(**updates) -> dict:
    model = {
        "name": "H3 FL2VA",
        "architecture": "minimax_h3",
        "image_prompt_types_allowed": "TSE",
        "sliding_window": False,
        "returns_audio": True,
        "director_shot_image_support": "optional",
        "frames_minimum": 124,
        "frames_maximum": 345,
        "frames_steps": 17,
        "fps": 24,
    }
    model.update(updates)
    return model


class _Registry:
    def __init__(self, models: dict[str, dict]):
        self.models = models

    def get_model_def(self, model_type: str):
        return self.models.get(model_type)

    def get_model_family(self, model_type: str, *, for_ui: bool = False):
        return str(self.models.get(model_type, {}).get("family") or "test")

    def get_base_model_type(self, model_type: str):
        model = self.models.get(model_type, {})
        return str(model.get("architecture") or model_type)


class _CatalogRegistry(_Registry):
    def __init__(self, models: dict[str, dict]):
        super().__init__(models)
        self.models_def = self.models
        self.displayed_model_types = list(models)
        self.families_infos = {
            "test": (1, "Test"),
            "unknown": (99, "Unknown"),
        }

    def test_class_i2v(self, model_type: str):
        return not bool(self.models[model_type].get("image_outputs"))

    def test_class_t2v(self, model_type: str):
        return not bool(self.models[model_type].get("image_outputs"))


class TestDirectorPreviewFailureContract(unittest.TestCase):
    def test_local_director_has_no_maestro_content_refusal_layer(self):
        self.assertFalse(
            (APP_DIR / "services" / "director" / "safety_scan.py").exists(),
        )
        self.assertFalse(
            (
                APP_DIR
                / "services/llm_guides/director/nsfw_off_safety_rules.md"
            ).exists(),
        )
        forbidden_symbols = (
            "SafetyViolationError",
            "assert_no_minor_content",
            "assert_safe_final_director_prompts",
            "safety_policy_refusal",
            "strip_sex_act_leet_tokens",
            "_SEX_ACT_LEET_TOKENS",
        )
        for path in APP_DIR.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md"}:
                continue
            relative = path.relative_to(APP_DIR)
            source = path.read_text(encoding="utf-8", errors="replace")
            for symbol in forbidden_symbols:
                self.assertNotIn(symbol, source, msg=f"{relative}: {symbol}")

    def test_unexpected_preview_failure_is_stable_and_keeps_local_traceback(self):
        private_detail = (
            "provider=openrouter api_key=private at "
            "/media/hailey/private/provider-config.json"
        )

        operations = []
        shielded_calls = []

        def fail(operation):
            operations.append(operation)
            raise RuntimeError(private_detail)

        namespace = _director_plan_namespace(fail)
        request = _Request()
        with patch(
            "services.llm_operations.run_blocking_shielded",
            new=_blocking_shield_stub(shielded_calls),
        ), patch("traceback.print_exc") as print_exc:
            with self.assertRaises(_HTTPException) as raised:
                asyncio.run(namespace["director_v2_plan"](request))

        self.assertEqual(len(shielded_calls), 2)
        self.assertIs(
            shielded_calls[0][0], namespace["_resolve_direct_llm_selection"],
        )
        self.assertIs(
            shielded_calls[1][0],
            namespace["_run_authorized_llm_with_selection"],
        )
        self.assertEqual(operations, [shielded_calls[1][1][2]])
        self.assertEqual(operations[0].__name__, "run_director_plan")
        print_exc.assert_called_once_with()
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(
            raised.exception.detail,
            {
                "code": "director_preview_failed",
                "message": "Director could not build this preview.",
            },
        )
        public = json.dumps(raised.exception.detail)
        for private_fragment in (
            private_detail,
            "openrouter",
            "api_key",
            "/media/hailey",
            "provider-config.json",
        ):
            self.assertNotIn(private_fragment, public)

    def test_dependency_http_error_detail_is_also_sanitized(self):
        private_detail = {
            "provider": "private-provider",
            "config_path": "/media/hailey/private/provider-config.json",
        }
        dependency_error = _HTTPException(
            status_code=503,
            detail=private_detail,
        )

        operations = []
        shielded_calls = []

        def fail(operation):
            operations.append(operation)
            raise dependency_error

        namespace = _director_plan_namespace(fail)
        request = _Request()
        with patch(
            "services.llm_operations.run_blocking_shielded",
            new=_blocking_shield_stub(shielded_calls),
        ), patch("traceback.print_exc") as print_exc:
            with self.assertRaises(_HTTPException) as raised:
                asyncio.run(namespace["director_v2_plan"](request))

        self.assertEqual(len(shielded_calls), 2)
        self.assertIs(
            shielded_calls[0][0], namespace["_resolve_direct_llm_selection"],
        )
        self.assertIs(
            shielded_calls[1][0],
            namespace["_run_authorized_llm_with_selection"],
        )
        self.assertEqual(operations, [shielded_calls[1][1][2]])
        self.assertEqual(operations[0].__name__, "run_director_plan")
        print_exc.assert_called_once_with()
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(
            raised.exception.detail,
            {
                "code": "director_preview_failed",
                "message": "Director could not build this preview.",
            },
        )
        public = json.dumps(raised.exception.detail)
        self.assertNotIn(private_detail["provider"], public)
        self.assertNotIn(private_detail["config_path"], public)


class TestDirectorModelCatalogContract(unittest.TestCase):
    def test_catalog_exposes_current_capabilities_used_by_director_ui(self):
        models = {
            "image": _image_editor(),
            "h3": _h3_video(
                description="Bounded H3",
                selector_help="Choose a managed profile.",
            ),
            "ltx": _ltx_video(),
        }
        namespace = _list_models_namespace(_CatalogRegistry(models))
        response = namespace["list_models"](_Request())
        catalog = {item["model_type"]: item for item in response["models"]}

        self.assertTrue(catalog["image"]["director"]["image"]["compatible"])
        self.assertEqual(
            catalog["h3"]["director"]["video_strategy"],
            BOUNDED_START_END,
        )
        self.assertTrue(
            catalog["h3"]["director"]["video"]["short_film_story"]["compatible"]
        )
        self.assertFalse(catalog["h3"]["supports_audio_input"])
        self.assertTrue(catalog["h3"]["generates_audio"])
        self.assertEqual(catalog["h3"]["description"], "Bounded H3")
        self.assertEqual(
            catalog["h3"]["selector_help"],
            "Choose a managed profile.",
        )
        self.assertEqual(
            catalog["ltx"]["director"]["video_strategy"],
            ROLLING_WINDOW,
        )
        self.assertTrue(
            catalog["ltx"]["director"]["video"]["seamless"]["compatible"]
        )
        self.assertTrue(catalog["ltx"]["supports_audio_input"])
        self.assertTrue(catalog["ltx"]["generates_audio"])
        self.assertEqual(
            catalog["ltx"]["director"]["voice_reference_mode"],
            "id_lora",
        )


class TestDirectorSavedActionPublicContract(unittest.TestCase):
    def test_legacy_saved_failure_text_is_replaced_before_publication(self):
        namespace = _launch_functions_namespace(
            ["_safe_director_failure_details", "_sanitize_director_public_failures"],
            _DIRECTOR_PUBLIC_FAILURE_MESSAGES={
                "director_pipeline_failed": (
                    "Director generation stopped after an internal error."
                ),
                "cuda_oom": "Director generation stopped after a GPU memory error.",
            },
            _DIRECTOR_PUBLIC_REPAIR_FAILURE_MESSAGES={
                "director_repair_failed": (
                    "Director repair stopped after an internal error."
                ),
                "cuda_oom": "Director repair stopped after a GPU memory error.",
            },
            wgp=type("Wgp", (), {"server_config": {}})(),
        )
        state = {
            "status": "failed",
            "error": "provider prompt at /private/director/request.json",
            "repair": {
                "status": "failed",
                "error": "repair secret at /private/director/repair.json",
                "message": "repair secret",
            },
        }
        public = namespace["_sanitize_director_public_failures"](state)
        self.assertEqual(public["error_code"], "director_pipeline_failed")
        self.assertEqual(
            public["repair"]["error_code"], "director_repair_failed",
        )
        encoded = json.dumps(public)
        self.assertNotIn("provider prompt", encoded)
        self.assertNotIn("repair secret", encoded)
        self.assertNotIn("/private/director", encoded)

    def test_unexpected_repair_action_failure_is_stable_and_path_free(self):
        private_detail = "provider prompt at /private/director/request.json"
        namespace = _launch_functions_namespace(
            ["_director_action_failure_response", "repair_saved_pipeline"],
            _request_project_workspace=lambda request, workspace: "project-a",
            _init_pipeline=lambda: None,
            _require_saved_pipeline=lambda request, pid, workspace: ({}, "/tmp"),
            _begin_workspace_operation=lambda workspace: None,
            _end_workspace_operation=lambda workspace: None,
        )
        with (
            patch.object(
                pipeline, "start_pipeline_repair",
                side_effect=RuntimeError(private_detail),
            ),
            patch("traceback.print_exc") as print_exc,
        ):
            response = namespace["repair_saved_pipeline"](
                _Request(), "pipeline-a", "project-a",
            )

        print_exc.assert_called_once_with()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content, {
            "code": "director_action_failed",
            "error": "Director could not complete this action.",
        })
        public = json.dumps(response.content)
        self.assertNotIn("provider prompt", public)
        self.assertNotIn("/private/director", public)

    def test_delete_response_keeps_host_directory_and_names_private(self):
        namespace = _launch_functions_namespace(
            ["delete_pipeline_endpoint"],
            _request_project_workspace=lambda request, workspace: "project-a",
            _init_pipeline=lambda: None,
            _require_saved_pipeline=lambda request, pid, workspace: ({}, "/tmp"),
            _begin_workspace_operation=lambda workspace: None,
            _end_workspace_operation=lambda workspace: None,
        )
        private_result = {
            "ok": True,
            "dir": "/private/director/project-a",
            "media_total": 3,
            "media_deleted": 2,
            "media_deferred": 1,
            "errors": ["../private/secret.mp4"],
        }
        with (
            patch.object(pipeline, "delete_pipeline", return_value=private_result),
            patch("builtins.print"),
        ):
            response = namespace["delete_pipeline_endpoint"](
                _Request(), "pipeline-a", "project-a",
            )

        self.assertEqual(response["errors"], ["media_cleanup_incomplete"])
        self.assertEqual(response["error_count"], 1)
        public = json.dumps(response)
        self.assertNotIn("dir", response)
        self.assertNotIn("/private/director", public)
        self.assertNotIn("secret.mp4", public)


class TestDirectorBackendCompatibility(unittest.TestCase):
    def setUp(self):
        self.original_wgp = pipeline._wgp

    def tearDown(self):
        pipeline._wgp = self.original_wgp

    def _validate(self, models: dict[str, dict], **params) -> None:
        pipeline._wgp = _Registry(models)
        pipeline._validate_director_models(params)

    def test_registry_validation_accepts_current_image_and_rolling_video(self):
        self._validate(
            {"image": _image_editor(), "video": _ltx_video()},
            image_model="image",
            video_model="video",
            pipeline_type="short_film_story",
        )

    def test_plain_image_model_and_editor_that_cannot_bootstrap_are_rejected(self):
        invalid_images = {
            "plain": {"name": "Plain", "image_outputs": False},
            "editor_only": {
                "name": "Editor only",
                "image_outputs": True,
                "image_ref_choices": {
                    "choices": [("Main plus references", "KI")],
                },
            },
        }
        for image_model in invalid_images:
            with self.subTest(image_model=image_model):
                with self.assertRaises(pipeline.DirectorModelCompatibilityError):
                    self._validate(
                        {**invalid_images, "video": _ltx_video()},
                        image_model=image_model,
                        video_model="video",
                        pipeline_type="short_film_story",
                    )

    def test_voice_reference_rejects_non_ltx_model(self):
        non_ltx = _ltx_video(architecture="wan2.2")
        with self.assertRaisesRegex(
            pipeline.DirectorModelCompatibilityError,
            "does not support Director Voice Reference",
        ):
            self._validate(
                {"image": _image_editor(), "video": non_ltx},
                image_model="image",
                video_model="video",
                pipeline_type="short_film_story",
                voice_reference="voice.wav",
            )

    def test_fixed_length_and_required_control_models_are_rejected(self):
        fixed = _ltx_video(sliding_window=False)
        required_control = _ltx_video(
            custom_guide={"required": True},
        )
        for model, reason in (
            (fixed, "sliding-window support"),
            (required_control, "manual guide"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(
                    pipeline.DirectorModelCompatibilityError,
                    reason,
                ):
                    self._validate(
                        {"image": _image_editor(), "video": model},
                        image_model="image",
                        video_model="video",
                        pipeline_type="short_film_story",
                    )

    def test_seamless_requires_rolling_frame_injection(self):
        without_injection = _ltx_video(custom_frames_injection=False)
        with self.assertRaisesRegex(
            pipeline.DirectorModelCompatibilityError,
            "planned frame/keyframe injection",
        ):
            self._validate(
                {"image": _image_editor(), "video": without_injection},
                image_model="image",
                video_model="video",
                pipeline_type="short_film_story",
                seamless=True,
            )

        self._validate(
            {"image": _image_editor(), "video": _ltx_video()},
            image_model="image",
            video_model="video",
            pipeline_type="short_film_story",
            seamless=True,
        )

    def test_h3_is_bounded_without_becoming_seamless_or_rolling(self):
        h3 = _h3_video()
        assessment = compat.assess_director_model(
            "minimax_h3",
            h3,
            architecture="minimax_h3",
        )
        self.assertEqual(video_strategy(h3), BOUNDED_START_END)
        self.assertTrue(
            assessment["video"]["short_film_story"]["compatible"]
        )
        self.assertFalse(assessment["video"]["seamless"]["compatible"])

        self._validate(
            {"image": _image_editor(), "video": h3},
            image_model="image",
            video_model="video",
            pipeline_type="short_film_story",
        )
        with self.assertRaisesRegex(
            pipeline.DirectorModelCompatibilityError,
            "independent native-duration shots",
        ):
            self._validate(
                {"image": _image_editor(), "video": h3},
                image_model="image",
                video_model="video",
                pipeline_type="short_film_story",
                seamless=True,
            )

    def test_architecture_invariants_override_contradictory_strategy_metadata(self):
        self.assertEqual(
            video_strategy(_h3_video(director_video_strategy="rolling_window")),
            BOUNDED_START_END,
        )
        self.assertEqual(
            video_strategy(_ltx_video(director_video_strategy="bounded_start_end")),
            ROLLING_WINDOW,
        )

    def test_resolved_architecture_routes_models_with_incomplete_metadata(self):
        h3 = _h3_video()
        h3.pop("architecture")
        assessment = compat.assess_director_model(
            "custom-h3",
            h3,
            architecture="minimax_h3",
        )
        self.assertEqual(assessment["video_strategy"], BOUNDED_START_END)

        ltx = _ltx_video(director_video_strategy="bounded_start_end")
        ltx.pop("architecture")
        assessment = compat.assess_director_model(
            "custom-ltx",
            ltx,
            architecture="ltx2_22B",
        )
        self.assertEqual(assessment["video_strategy"], ROLLING_WINDOW)

        profile = build_director_video_execution_profile(
            "minimax_h3",
            h3,
            {"resolution": "768x768"},
            {"gpu_vram_gb": 24},
        )
        self.assertEqual(profile["video_strategy"], BOUNDED_START_END)


class TestDirectorObsoleteContractRemoval(unittest.TestCase):
    def test_obsolete_omni_values_are_normalized_out_of_assessment(self):
        model = _h3_video(
            director_video_strategy="omni_reference",
            director_audio_input_mode="reference_manifest",
            director_reference_mode="omni_manifest",
        )
        assessment = compat.assess_director_model("minimax_h3", model)
        self.assertEqual(assessment["video_strategy"], BOUNDED_START_END)
        self.assertEqual(assessment["audio_input_mode"], "none")
        self.assertEqual(assessment["reference_mode"], "start_frame")
        self.assertFalse(assessment["supports_voice_reference"])
        self.assertEqual(assessment["voice_reference_mode"], "none")
        rendered = json.dumps(assessment)
        for obsolete in (
            "omni_reference",
            "reference_manifest",
            "omni_manifest",
            "native_reference",
        ):
            self.assertNotIn(obsolete, rendered)

    def test_execution_profile_omits_legacy_checkpoint_turbo_and_strength_keys(self):
        model = _h3_video(minimax_h3_full_checkpoint=True)
        profile = build_director_video_execution_profile(
            "minimax_h3",
            model,
            {
                "resolution": "768x768",
                "minimax_h3_turbo_mode": True,
                "activated_loras": ["obsolete.safetensors"],
                "loras_multipliers": "0.75",
            },
            {"gpu_vram_gb": 24},
        )
        self.assertEqual(profile["video_strategy"], BOUNDED_START_END)
        self.assertNotEqual(profile["video_strategy"], ROLLING_WINDOW)
        for obsolete_key in (
            "checkpoint",
            "omni_reference",
            "turbo_mode",
            "activated_lora_count",
            "lora_strength",
            "lora_weights",
        ):
            self.assertNotIn(obsolete_key, profile)

    def test_types_expose_only_current_director_compatibility_values(self):
        source = TYPES_PATH.read_text(encoding="utf-8")
        interface = source[
            source.index("export interface DirectorModelCompatibility"):
            source.index("export interface ModelDef")
        ]
        self.assertIn("'rolling_window' | 'bounded_start_end'", interface)
        self.assertIn("'none' | 'id_lora'", interface)
        for obsolete in (
            "omni_reference",
            "reference_manifest",
            "omni_manifest",
            "native_reference",
        ):
            self.assertNotIn(obsolete, interface)


if __name__ == "__main__":
    unittest.main(verbosity=2)
