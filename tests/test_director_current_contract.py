"""Focused regressions for Director's current model and preview contracts."""
from __future__ import annotations

import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
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
        "_reject_client_director_image_role_internals": lambda body: None,
        "_authorize_director_media_inputs": lambda request, body: None,
        "_resolve_director_image_role_request": lambda request, body: "legacy",
        "_resolve_h3_style_workflow_request": (
            lambda body, model_field="video_model": None
        ),
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
        self.assertTrue(
            catalog["image"]["director"]["image"]["creator"]["compatible"]
        )
        self.assertTrue(
            catalog["image"]["director"]["image"]["editor"]["compatible"]
        )
        self.assertEqual(
            catalog["image"]["director"]["image"]["creator"]["reasons"],
            [],
        )
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

    def test_capabilities_v1_exposes_bounded_role_readiness_contract(self):
        registry = _CatalogRegistry({"image": _image_editor()})
        remote_visibility = [None]
        namespace = _launch_functions_namespace(
            ["director_capabilities"],
            wgp=registry,
            _director_explicit_creator_resolution=lambda unrestricted: {
                "resolved_model": "image",
                "selection_source": (
                    "verified_manual_preference" if unrestricted
                    else "safe_fallback"
                ),
            },
            _remote_visible_model_ids=lambda request: remote_visibility[0],
            _director_image_candidate_readiness=lambda model, role: {
                "model_type": model,
                "compatible": True,
                "ready": True,
                "reasons": [],
                "actions": [],
                "enabled": True,
                "downloaded": True,
            },
            _DIRECTOR_EXPLICIT_CREATOR_MODELS=("image",),
            _DIRECTOR_SAFE_IMAGE_MODEL="image",
            _DIRECTOR_DEFAULT_EDITOR_MODEL="image",
            _DIRECTOR_READINESS_REASONS=frozenset({"model_not_downloaded"}),
            _DIRECTOR_READINESS_ACTIONS=frozenset({"download_model"}),
        )
        response = namespace["director_capabilities"](
            _Request(), explicit_output=True,
        )
        self.assertEqual(response["schema_version"], 1)
        self.assertEqual(
            response["readiness_reason_values"], ["model_not_downloaded"],
        )
        self.assertEqual(
            response["readiness_action_values"], ["download_model"],
        )
        self.assertEqual(
            response["image_roles"]["creator"]["selection_source"],
            "verified_manual_preference",
        )
        self.assertEqual(
            response["image_roles"]["editor"]["selection_source"],
            "fixed_default",
        )
        self.assertEqual(
            response["image_roles"]["creator"]["lora_catalog_endpoint"],
            "/api/v1/loras/{model_type}/details",
        )
        remote_visibility[0] = frozenset()
        remote = namespace["director_capabilities"](_Request())
        self.assertIsNone(remote["image_roles"]["creator"]["resolved_model"])
        self.assertIsNone(remote["image_roles"]["editor"]["resolved_model"])
        self.assertEqual(remote["image_roles"]["creator"]["candidates"], [])

    def test_automatic_creator_requires_complete_readiness_in_preference_order(self):
        candidates = [
            {
                "model_type": "moody",
                "enabled": True,
                "manual_checkpoint_verified": True,
                "terms_accepted": True,
                "downloaded": False,
                "ready": True,
            },
            {
                "model_type": "cutie",
                "enabled": True,
                "manual_checkpoint_verified": True,
                "terms_accepted": True,
                "downloaded": True,
                "ready": True,
            },
        ]
        namespace = _launch_functions_namespace(
            ["_director_explicit_creator_resolution"],
            _project_reference_explicit_generation_model=lambda: {
                "candidates": candidates,
            },
            _DIRECTOR_EXPLICIT_CREATOR_MODELS=("moody", "cutie"),
            _DIRECTOR_SAFE_IMAGE_MODEL="safe",
        )
        self.assertEqual(
            namespace["_director_explicit_creator_resolution"](
                unrestricted=True,
            ),
            {
                "resolved_model": "cutie",
                "selection_source": "verified_manual_preference",
            },
        )

    def test_role_lora_wire_is_filename_scoped_and_strength_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            weight = Path(directory, "look.safetensors")
            weight.write_bytes(b"weight")

            class LoraRegistry:
                @staticmethod
                def resolve_lora_path(model_type, lora_id):
                    return str(weight) if lora_id == weight.name else ""

            captured = []

            def resolve(items, **kwargs):
                captured.append((items, kwargs))
                return [{
                    "id": weight.name,
                    "multiplier": 1.25,
                    "parameter_schema_digest": None,
                    "parameter_values": (),
                    "parameter_expansions": [],
                }]

            namespace = _launch_functions_namespace(
                ["_director_role_lora_request"],
                wgp=LoraRegistry(),
                os=__import__("os"),
                math=__import__("math"),
                _project_reference_lora_parameter_schema=(
                    lambda model, path: None
                ),
                _project_reference_resolve_additional_loras=resolve,
            )
            result = namespace["_director_role_lora_request"](
                [{"id": weight.name, "multiplier": 1.25}],
                model_type="creator",
                scope="generation",
            )
            self.assertEqual(result[0]["id"], weight.name)
            self.assertEqual(captured[0][0][0]["scope"], "generation")
            with self.assertRaises(_HTTPException) as raised:
                namespace["_director_role_lora_request"](
                    [{"id": weight.name, "multiplier": 10.01}],
                    model_type="creator",
                    scope="generation",
                )
            self.assertEqual(raised.exception.status_code, 400)


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
            _revalidate_saved_director_runtime=lambda request, state: state,
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

    def test_creator_and_editor_roles_are_independent_but_legacy_stays_combined(self):
        creator_only = {
            "name": "Creator only",
            "image_outputs": True,
        }
        editor_only = {
            "name": "Editor only",
            "image_outputs": True,
            "one_image_ref_needed": True,
            "image_ref_choices": {
                "choices": [("Main plus references", "KI")],
            },
        }
        creator = compat.assess_director_model("creator", creator_only)["image"]
        editor = compat.assess_director_model("editor", editor_only)["image"]
        self.assertTrue(creator["creator"]["compatible"])
        self.assertFalse(creator["editor"]["compatible"])
        self.assertFalse(creator["compatible"])
        self.assertFalse(editor["creator"]["compatible"])
        self.assertTrue(editor["editor"]["compatible"])
        self.assertFalse(editor["compatible"])

        self._validate(
            {
                "creator": creator_only,
                "editor": editor_only,
                "video": _ltx_video(),
            },
            image_creator_model="creator",
            image_editor_model="editor",
            video_model="video",
            pipeline_type="short_film_story",
        )

    def test_image_role_and_legacy_request_keys_are_ambiguous(self):
        namespace = _launch_functions_namespace(
            ["_director_image_role_wire_mode"],
            _DIRECTOR_IMAGE_ROLE_FIELDS=frozenset({
                "image_creator_model", "image_editor_model",
                "image_creator_loras", "image_editor_loras",
            }),
            _DIRECTOR_LEGACY_IMAGE_FIELDS=frozenset({
                "image_model", "image_loras",
            }),
        )
        classify = namespace["_director_image_role_wire_mode"]
        self.assertEqual(classify({}), "legacy")
        self.assertEqual(classify({"image_creator_model": None}), "roles")
        with self.assertRaises(_HTTPException) as raised:
            classify({
                "image_creator_model": None,
                "image_model": "legacy",
            })
        self.assertEqual(raised.exception.status_code, 400)

    def test_generic_preview_flattens_the_admitted_role_snapshot(self):
        namespace = _launch_functions_namespace(
            ["_apply_director_image_role_generation"],
        )
        resolved_loras = {
            "creator": {
                "activated_loras": ["creator.safetensors"],
                "loras_multipliers": "0.75",
                "parameter_expansions": [{
                    "text": "creator parameter fragment",
                    "scopes": ["generation"],
                }],
            },
            "editor": {
                "activated_loras": ["editor.safetensors"],
                "loras_multipliers": "1.25",
                "parameter_expansions": [{
                    "text": "editor parameter fragment",
                    "scopes": ["editing"],
                }],
            },
        }

        def role_loras(_body, role):
            return resolved_loras[role]

        def role_prompt(prompt, loras, _role):
            return ", ".join(
                [prompt, *(item["text"] for item in loras["parameter_expansions"])]
            )

        with patch.object(
            pipeline, "_director_image_role_loras", side_effect=role_loras,
        ), patch.object(
            pipeline, "_director_role_prompt", side_effect=role_prompt,
        ), patch.object(
            pipeline, "_director_image_params",
            side_effect=lambda _body, model: {
                "resolution": "1024x576" if model == "creator" else "768x768",
                "num_inference_steps": 12,
                "guidance_scale": 2,
            },
        ), patch.object(
            pipeline, "_limit_director_image_refs",
            side_effect=lambda _model, refs, **_kwargs: refs[:1],
        ):
            creator = {
                "prompt": "anchor",
                "image_refs": [],
                "image_creator_model": "creator",
                "image_editor_model": "editor",
                "model_type": "stale-studio-model",
                "activated_loras": ["stale.safetensors"],
            }
            self.assertEqual(
                namespace["_apply_director_image_role_generation"](creator),
                "creator",
            )
            self.assertEqual(creator["model_type"], "creator")
            self.assertEqual(creator["activated_loras"], ["creator.safetensors"])
            self.assertEqual(creator["loras_multipliers"], "0.75")
            self.assertEqual(creator["resolution"], "1024x576")
            self.assertEqual(creator["video_prompt_type"], "")
            self.assertIn("creator parameter fragment", creator["prompt"])
            creator_prompt = creator["prompt"]
            namespace["_apply_director_image_role_generation"](creator)
            self.assertEqual(creator["prompt"], creator_prompt)

            editor = {
                "prompt": "continuity",
                "image_refs": ["one.png", "two.png"],
                "image_creator_model": "creator",
                "image_editor_model": "editor",
                "resolution": "1920x1080",
            }
            self.assertEqual(
                namespace["_apply_director_image_role_generation"](editor),
                "editor",
            )
            self.assertEqual(editor["model_type"], "editor")
            self.assertEqual(editor["image_refs"], ["one.png"])
            self.assertEqual(editor["activated_loras"], ["editor.safetensors"])
            self.assertEqual(editor["loras_multipliers"], "1.25")
            self.assertEqual(editor["resolution"], "1920x1080")
            self.assertEqual(editor["video_prompt_type"], "KI")
            self.assertIn("editor parameter fragment", editor["prompt"])

        with self.assertRaises(_HTTPException) as raised:
            namespace["_apply_director_image_role_generation"]({
                "prompt": "invalid",
                "image_refs": "one.png",
                "image_creator_model": "creator",
                "image_editor_model": "editor",
            })
        self.assertEqual(raised.exception.status_code, 400)

    def test_generic_role_internals_are_server_owned_and_publicly_stripped(self):
        internal_fields = frozenset({
            "_director_image_role", "_director_image_role_loras",
            "_director_image_role_selection", "_director_image_role_base_prompt",
        })
        namespace = _launch_functions_namespace(
            [
                "_reject_client_director_image_role_internals",
                "_strip_director_image_role_internals",
            ],
            _DIRECTOR_IMAGE_ROLE_INTERNAL_FIELDS=internal_fields,
        )
        reject = namespace["_reject_client_director_image_role_internals"]
        with self.assertRaises(_HTTPException) as raised:
            reject({
                "model_type": "legacy-image",
                "_director_image_role_selection": {"creator": "forged"},
            })
        self.assertEqual(raised.exception.status_code, 400)

        public = {
            "model_type": "creator",
            "image_creator_model": "creator",
            "_director_image_role": "creator",
            "_director_image_role_loras": {"creator": [{"private": True}]},
            "_director_image_role_selection": {"creator": "safe_fallback"},
            "_director_image_role_base_prompt": "private base prompt",
        }
        namespace["_strip_director_image_role_internals"](public)
        self.assertEqual(public, {
            "model_type": "creator",
            "image_creator_model": "creator",
        })
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name in (
            "generate", "director_pipeline_start", "director_v2_plan",
        ):
            function = next(
                node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function_name
            )
            function_source = ast.get_source_segment(source, function)
            self.assertIn(
                "_reject_client_director_image_role_internals(body)",
                function_source,
            )
        public_function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_public_pipeline_state"
        )
        self.assertIn(
            "_strip_director_image_role_internals(snapshot)",
            ast.get_source_segment(source, public_function),
        )

    def test_generic_role_recovery_reruns_admission_before_execution(self):
        events = []

        def resolve(request, params):
            events.append(("resolve", request.state.maestro_remote))
            params["image_creator_model"] = "creator"
            params["image_editor_model"] = "editor"

        def apply(params):
            events.append(("apply", params["image_creator_model"]))
            params["model_type"] = "creator"

        namespace = _launch_functions_namespace(
            ["_require_job_runtime_model_admission"],
            _director_image_role_wire_mode=lambda params: (
                "roles" if "image_creator_model" in params else "legacy"
            ),
            _resolve_director_image_role_request=resolve,
            _apply_director_image_role_generation=apply,
            _require_job_model_recipe_terms=lambda job: events.append(
                ("legacy_terms", job["id"]),
            ),
        )
        role_job = {
            "id": "role-preview",
            "source_remote": True,
            "params": {"image_creator_model": None},
        }
        namespace["_require_job_runtime_model_admission"](role_job)
        self.assertEqual(events, [
            ("resolve", True),
            ("apply", "creator"),
        ])
        self.assertEqual(role_job["model_type"], "creator")

        namespace["_require_job_runtime_model_admission"]({
            "id": "legacy", "params": {"model_type": "legacy-image"},
        })
        self.assertEqual(events[-1], ("legacy_terms", "legacy"))

    def test_enhanced_role_prompt_replaces_the_sealed_base_before_reflattening(self):
        flattened = []

        def apply(body):
            body["prompt"] = body["_director_image_role_base_prompt"] + ", role-fragment"
            flattened.append(body["prompt"])

        namespace = _launch_functions_namespace(
            ["_apply_authoritative_generation_prompt"],
            _apply_director_image_role_generation=apply,
        )
        role_params = {
            "prompt": "original, role-fragment",
            "_director_image_role_base_prompt": "original",
        }
        namespace["_apply_authoritative_generation_prompt"](
            role_params, "enhanced original",
        )
        self.assertEqual(
            role_params["_director_image_role_base_prompt"],
            "enhanced original",
        )
        self.assertEqual(
            role_params["prompt"], "enhanced original, role-fragment",
        )
        self.assertEqual(flattened, ["enhanced original, role-fragment"])

        legacy_params = {"prompt": "original"}
        namespace["_apply_authoritative_generation_prompt"](
            legacy_params, "enhanced original",
        )
        self.assertEqual(legacy_params, {"prompt": "enhanced original"})

    def test_legacy_intermediate_postprocess_keys_migrate_to_final_video_once(self):
        namespace = _launch_functions_namespace(
            ["_migrate_director_final_video_postprocess"],
        )
        params = {
            "image_spatial_upsampling": "flashvsr2",
            "image_film_grain_intensity": 0.2,
            "image_film_grain_saturation": 0.7,
        }
        namespace["_migrate_director_final_video_postprocess"](params)
        self.assertEqual(params["video_spatial_upsampling"], "flashvsr2")
        self.assertEqual(params["video_film_grain_intensity"], 0.2)
        self.assertEqual(params["video_film_grain_saturation"], 0.7)
        self.assertFalse(any(key.startswith("image_film_grain") for key in params))
        self.assertNotIn("image_spatial_upsampling", params)

        explicit_final = {
            "image_spatial_upsampling": "legacy",
            "video_spatial_upsampling": "flashvsr3",
        }
        namespace["_migrate_director_final_video_postprocess"](explicit_final)
        self.assertEqual(explicit_final, {
            "video_spatial_upsampling": "flashvsr3",
        })

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
