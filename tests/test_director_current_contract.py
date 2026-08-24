"""Focused regressions for Director's current model and preview contracts."""
from __future__ import annotations

import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services import director_model_compat as compat  # noqa: E402
from services import director_pipeline as pipeline  # noqa: E402
from services import llm_operations  # noqa: E402
from services.llm_cancellation import LlmCancellationHandle  # noqa: E402
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
        "copy": copy,
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
        "h3_public_availability": lambda *_args, **_kwargs: {},
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
        self.server_config = {"services": {}}
        self.families_infos = {
            "test": (1, "Test"),
            "unknown": (99, "Unknown"),
        }

    def test_class_i2v(self, model_type: str):
        return not bool(self.models[model_type].get("image_outputs"))

    def test_class_t2v(self, model_type: str):
        return not bool(self.models[model_type].get("image_outputs"))


class TestDirectorPreviewScopedOperationContract(unittest.TestCase):
    @staticmethod
    def _submission_namespace(events, manager, project_instance):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(LAUNCH_PATH))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "director_v2_plan"
        )
        function = copy.deepcopy(function)
        function.decorator_list = []

        def authorize(_request, body):
            events.append("authorize_media")
            body["workspace"] = "project"
            return "project"

        public_status = _launch_functions_namespace(
            ["_llm_route_public_status"],
            Mapping=dict,
            Any=object,
        )["_llm_route_public_status"]

        namespace = {
            "Request": _Request,
            "HTTPException": _HTTPException,
            "JSONResponse": _JSONResponse,
            "copy": copy,
            "hmac": __import__("hmac"),
            "wgp": types.SimpleNamespace(server_config={"services": {
                "director_prompt_polish": "third_pass",
            }}),
            "_DIRECTOR_PREVIEW_MAX_MEDIA_BYTES": 1024,
            "_DIRECTOR_PREVIEW_MAX_TOTAL_MEDIA_BYTES": 2048,
            "_promote_external_llm_request": (
                lambda _request: events.append("promote")
            ),
            "_request_project_workspace": (
                lambda _request, value: events.append("workspace") or value
            ),
            "_require_project_access": (
                lambda *_args, **_kwargs: events.append("authorize_project")
            ),
            "_llm_operation_scope": (
                lambda *_args: ("owner", project_instance)
            ),
            "_normalize_llm_route_request_id": (
                lambda value: uuid.UUID(value).hex
            ),
            "_reject_client_director_image_role_internals": (
                lambda _body: events.append("reject_internals")
            ),
            "_authorize_director_media_inputs": authorize,
            "_resolve_director_image_role_request": (
                lambda *_args: events.append("resolve_roles") or "legacy"
            ),
            "_resolve_h3_style_workflow_request": (
                lambda *_args, **_kwargs: events.append("workflow") or None
            ),
            "_explicit_llm_guidance_allowed": (
                lambda _body: events.append("guidance") or False
            ),
            "_director_preview_media_paths": (
                lambda _body: events.append("media_paths") or []
            ),
            "_seal_prompt_enhancement_images": (
                lambda *_args, **_kwargs: events.append("seal") or []
            ),
            "_resolve_direct_llm_selection": (
                lambda _request: events.append("selection") or {
                    "model_id": "local/model", "provider": "local",
                }
            ),
            "_director_preview_effective_digest": (
                lambda *_args, **_kwargs: events.append("digest") or "digest"
            ),
            "_llm_route_public_status": public_status,
            "_ScopedPromptEnhancementRequest": types.SimpleNamespace(
                snapshot_authority=lambda _request: {"session_id": "owner"},
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[function], type_ignores=[],
        )), str(LAUNCH_PATH), "exec"), namespace)
        return namespace

    def test_caller_uuid_and_project_instance_submit_one_scoped_operation(self):
        events = []
        captured = {}
        project_instance = "a" * 64
        request_id = str(uuid.uuid4())

        class Manager:
            @staticmethod
            def submit(**kwargs):
                events.append("submit")
                captured.update(kwargs)
                return {
                    "request_id": kwargs["request_id"],
                    "operation_kind": kwargs["operation_kind"],
                    "status": "running",
                }

        namespace = self._submission_namespace(
            events, Manager(), project_instance,
        )
        request = _Request({
            "request_id": request_id,
            "project_instance": project_instance,
            "workspace": "project",
            "skill_type": "music_video",
            "director_flags": {},
        })
        request.state.maestro_session_id = "owner"
        request.state.maestro_remote = False
        with patch.object(
            llm_operations, "llm_route_operation_manager", Manager(),
        ):
            response = asyncio.run(namespace["director_v2_plan"](request))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(captured["request_id"], uuid.UUID(request_id).hex)
        self.assertEqual(captured["project_instance_key"], project_instance)
        self.assertEqual(captured["operation_kind"], "director_preview")
        self.assertEqual(captured["effective_input_digest"], "digest")
        self.assertTrue(callable(captured["execute"]))
        self.assertLess(
            events.index("media_paths"), events.index("authorize_media"),
        )
        self.assertLess(events.index("authorize_project"), events.index("seal"))
        self.assertLess(events.index("seal"), events.index("selection"))
        self.assertLess(events.index("digest"), events.index("submit"))

    def test_coalesced_operation_status_is_returned_with_202(self):
        project_instance = "a" * 64
        request_id = str(uuid.uuid4())
        existing = {
            "request_id": uuid.UUID(request_id).hex,
            "operation_kind": "director_preview",
            "status": "running",
            "progress": {"phase": "generating", "pass": 1},
        }

        class Manager:
            @staticmethod
            def submit(**_kwargs):
                return dict(existing)

        namespace = self._submission_namespace(
            [], Manager(), project_instance,
        )
        request = _Request({
            "request_id": request_id,
            "project_instance": project_instance,
            "workspace": "project",
            "skill_type": "music_video",
            "director_flags": {},
        })
        request.state.maestro_session_id = "owner"
        request.state.maestro_remote = False
        with patch.object(
            llm_operations, "llm_route_operation_manager", Manager(),
        ):
            response = asyncio.run(namespace["director_v2_plan"](request))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, existing)

    def test_coalesced_failed_operation_is_projected_before_202(self):
        project_instance = "a" * 64
        request_id = str(uuid.uuid4())

        class Manager:
            @staticmethod
            def submit(**kwargs):
                return {
                    "request_id": kwargs["request_id"],
                    "operation_kind": "director_preview",
                    "status": "failed",
                    "error": {
                        "code": "llm_operation_failed",
                        "message": "provider key at /private/path",
                        "retryable": True,
                    },
                }

        namespace = self._submission_namespace(
            [], Manager(), project_instance,
        )
        request = _Request({
            "request_id": request_id,
            "project_instance": project_instance,
            "workspace": "project",
            "skill_type": "music_video",
            "director_flags": {},
        })
        request.state.maestro_session_id = "owner"
        request.state.maestro_remote = False
        with patch.object(
            llm_operations, "llm_route_operation_manager", Manager(),
        ):
            response = asyncio.run(namespace["director_v2_plan"](request))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content["error"], {
            "code": "director_preview_failed",
            "message": "Director could not build this preview.",
            "retryable": True,
        })
        self.assertNotIn("/private/path", json.dumps(response.content))

    def test_manager_conflict_and_foreign_scope_miss_are_bounded(self):
        project_instance = "a" * 64
        request_body = {
            "request_id": str(uuid.uuid4()),
            "project_instance": project_instance,
            "workspace": "project",
            "skill_type": "music_video",
            "director_flags": {},
        }

        class ConflictManager:
            @staticmethod
            def submit(**_kwargs):
                raise llm_operations.LlmRouteOperationConflictError(
                    "private digest mismatch",
                )

        class ForeignManager:
            @staticmethod
            def submit(**_kwargs):
                return None

        for manager, expected_status in (
            (ConflictManager(), 409),
            (ForeignManager(), 404),
        ):
            with self.subTest(expected_status=expected_status):
                namespace = self._submission_namespace(
                    [], manager, project_instance,
                )
                request = _Request(dict(request_body))
                request.state.maestro_session_id = "owner"
                request.state.maestro_remote = False
                with patch.object(
                    llm_operations, "llm_route_operation_manager", manager,
                ), self.assertRaises(_HTTPException) as raised:
                    asyncio.run(namespace["director_v2_plan"](request))
                self.assertEqual(raised.exception.status_code, expected_status)
                self.assertNotIn("private digest", str(raised.exception.detail))

    def test_recreated_project_is_rejected_before_media_or_model_resolution(self):
        events = []
        namespace = self._submission_namespace(
            events, object(), "b" * 64,
        )
        request = _Request({
            "request_id": str(uuid.uuid4()),
            "project_instance": "a" * 64,
            "workspace": "project",
        })
        request.state.maestro_session_id = "owner"
        request.state.maestro_remote = False
        with self.assertRaises(_HTTPException) as raised:
            asyncio.run(namespace["director_v2_plan"](request))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("authorize_media", events)
        self.assertNotIn("seal", events)
        self.assertNotIn("selection", events)

    def test_media_shape_caps_preserve_bounded_nested_lists(self):
        namespace = _launch_functions_namespace(
            ["_director_preview_media_paths"],
            Mapping=dict,
            Any=object,
            _DIRECTOR_PREVIEW_MEDIA_FIELDS=("character_ref_paths",),
            _DIRECTOR_PREVIEW_MAX_MEDIA_ITEMS=4,
            _DIRECTOR_PREVIEW_MAX_MEDIA_DEPTH=3,
            _DIRECTOR_PREVIEW_MAX_MEDIA_SHAPE_ENTRIES=12,
        )
        flatten = namespace["_director_preview_media_paths"]
        self.assertEqual(
            flatten({
                "character_ref_paths": [["one", ["two"]], "three"],
            }),
            ["one", "two", "three"],
        )
        with self.assertRaises(_HTTPException) as too_many:
            flatten({
                "character_ref_paths": ["one", "two", "three", "four", "five"],
            })
        self.assertEqual(too_many.exception.status_code, 400)
        with self.assertRaises(_HTTPException) as too_deep:
            flatten({"character_ref_paths": [[[['one']]]]})
        self.assertEqual(too_deep.exception.status_code, 400)

    def test_worker_source_fences_media_all_passes_and_snapshot_cleanup(self):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(LAUNCH_PATH))
        route = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "director_v2_plan"
        )
        route_source = ast.get_source_segment(source, route)
        self.assertIn('operation_kind="director_preview"', route_source)
        self.assertIn("_revalidate_prompt_enhancement_images", route_source)
        self.assertIn("_materialize_prompt_enhancement_images", route_source)
        self.assertIn("_remove_prompt_enhancement_snapshots", route_source)
        self.assertIn("_run_authorized_llm_with_selection", route_source)
        self.assertGreaterEqual(
            route_source.count("operation_cancellation.checkpoint()"), 7,
        )
        third_pass = route_source[route_source.index(
            "polish_prompts_third_pass("
        ):]
        for keyword in (
            "response_assist=route_response_assist",
            "progress_callback=route_progress",
            "cancel_handle=operation_cancellation",
        ):
            self.assertIn(keyword, third_pass)

    def test_progress_pass_limit_tracks_frozen_polish_mode(self):
        from services.director import prompt_polish

        class Plan:
            @staticmethod
            def to_dict():
                return {"plan": True}

        class DirectorOrchestrator:
            def __init__(self, **_kwargs):
                pass

            @staticmethod
            def plan(_skill_type, **_kwargs):
                return Plan()

            @staticmethod
            def render_plan(plan, **_kwargs):
                return plan

            @staticmethod
            def plan_to_clip_plans(_rendered):
                return [{"video_prompt": "bounded prompt"}]

        orchestrator_module = types.SimpleNamespace(
            DirectorOrchestrator=DirectorOrchestrator,
            DirectorFlags=types.SimpleNamespace(
                from_dict=lambda _value: types.SimpleNamespace(),
            ),
        )
        guidance_module = types.SimpleNamespace(
            EXPLICIT_GUIDANCE_SNAPSHOT_KEY="_server_snapshot",
        )

        for polish_mode, expected in (
            ("off", [("director_plan", 2), ("director_render", 2)]),
            ("third_pass", [
                ("director_plan", 3),
                ("director_render", 3),
                ("director_polish", 3),
            ]),
        ):
            with self.subTest(polish_mode=polish_mode):
                progress = []
                namespace = _director_plan_namespace(
                    lambda operation: operation(),
                )
                namespace.update({
                    "wgp": types.SimpleNamespace(
                        server_config={"services": {}},
                    ),
                    "_llm_route_progress_callback": (
                        lambda _request: progress.append
                    ),
                    "_resolved_local_response_assist": (
                        lambda *_args, **_kwargs: None
                    ),
                    "_with_llm_route_progress": (
                        lambda operation, *_args, **_kwargs: operation
                    ),
                    "_apply_h3_style_workflow_to_director_clips": (
                        lambda *_args: None
                    ),
                })
                request = _Request({"skill_type": "music_video"})
                request.state.maestro_director_preview_worker = True
                request.state.maestro_director_preview_selection = {
                    "provider": "local",
                }
                request.state.maestro_director_preview_assist = None
                request.state.maestro_director_preview_guidance = False
                request.state.maestro_director_preview_workflow = None
                request.state.maestro_director_preview_polish_mode = polish_mode
                request.state.maestro_llm_cancel_handle = (
                    LlmCancellationHandle()
                )
                shielded_calls = []
                with patch.dict(sys.modules, {
                    "services.director.orchestrator": orchestrator_module,
                    "services.director.nsfw_guidance": guidance_module,
                }), patch(
                    "services.llm_operations.run_blocking_shielded",
                    new=_blocking_shield_stub(shielded_calls),
                ), patch.object(
                    prompt_polish,
                    "polish_prompts_third_pass",
                    side_effect=lambda clips, *_args, **_kwargs: clips,
                ):
                    result = asyncio.run(
                        namespace["director_v2_plan"](request),
                    )

                self.assertEqual(result["production_plan"], {"plan": True})
                observed = [
                    (event["stage"], event["pass_limit"])
                    for event in progress
                ]
                self.assertEqual(observed, expected)

    def test_terminal_failure_projection_is_stable_and_redacted(self):
        namespace = _launch_functions_namespace(
            ["_llm_route_public_status"],
            Mapping=dict,
            Any=object,
        )
        private = {
            "status": "failed",
            "error": {
                "code": "llm_operation_failed",
                "message": "provider key at /private/path",
                "retryable": True,
            },
        }
        projected = namespace["_llm_route_public_status"](
            "director_preview", private,
        )
        self.assertEqual(projected["error"], {
            "code": "director_preview_failed",
            "message": "Director could not build this preview.",
            "retryable": True,
        })
        self.assertNotIn("/private/path", json.dumps(projected))
        self.assertEqual(
            namespace["_llm_route_public_status"]("enhance", private),
            private,
        )

    def test_cancel_after_plan_prevents_render_polish_and_result(self):
        cancellation = LlmCancellationHandle()
        events = []

        class Plan:
            @staticmethod
            def to_dict():
                events.append("result")
                return {"private": True}

        class DirectorOrchestrator:
            def __init__(self, **_kwargs):
                pass

            @staticmethod
            def plan(_skill_type, **_kwargs):
                events.append("plan")
                cancellation.cancel()
                return Plan()

            @staticmethod
            def render_plan(*_args, **_kwargs):
                events.append("render")
                raise AssertionError("render ran after cancellation")

        namespace = _director_plan_namespace(lambda operation: operation())
        namespace.update({
            "wgp": types.SimpleNamespace(server_config={"services": {}}),
            "_llm_route_progress_callback": lambda _request: None,
            "_resolved_local_response_assist": (
                lambda *_args, **_kwargs: None
            ),
            "_with_llm_route_progress": (
                lambda operation, *_args, **_kwargs: operation
            ),
            "_apply_h3_style_workflow_to_director_clips": (
                lambda *_args: events.append("workflow")
            ),
        })
        request = _Request({"skill_type": "music_video"})
        request.state.maestro_director_preview_worker = True
        request.state.maestro_director_preview_selection = {"provider": "local"}
        request.state.maestro_director_preview_assist = None
        request.state.maestro_director_preview_guidance = False
        request.state.maestro_director_preview_workflow = None
        request.state.maestro_director_preview_polish_mode = "third_pass"
        request.state.maestro_llm_cancel_handle = cancellation
        orchestrator_module = types.SimpleNamespace(
            DirectorOrchestrator=DirectorOrchestrator,
            DirectorFlags=types.SimpleNamespace(
                from_dict=lambda _value: types.SimpleNamespace(),
            ),
        )
        guidance_module = types.SimpleNamespace(
            EXPLICIT_GUIDANCE_SNAPSHOT_KEY="_server_snapshot",
        )
        shielded_calls = []
        with patch.dict(sys.modules, {
            "services.director.orchestrator": orchestrator_module,
            "services.director.nsfw_guidance": guidance_module,
        }), patch(
            "services.llm_operations.run_blocking_shielded",
            new=_blocking_shield_stub(shielded_calls),
        ), self.assertRaises(Exception) as raised:
            asyncio.run(namespace["director_v2_plan"](request))

        self.assertEqual(type(raised.exception).__name__, "LlmRequestCancelled")
        self.assertEqual(events, ["plan"])


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
        skip_dirs = {"env", "venv", ".venv", "site-packages", "__pycache__"}
        stack = [APP_DIR]
        while stack:
            current = stack.pop()
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    if child.name not in skip_dirs:
                        stack.append(child)
                    continue
                if child.suffix not in {".py", ".md"}:
                    continue
                relative = child.relative_to(APP_DIR)
                source = child.read_text(encoding="utf-8", errors="replace")
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
        request.state.maestro_director_preview_worker = True
        request.state.maestro_director_preview_selection = {"provider": "local"}
        request.state.maestro_director_preview_assist = None
        request.state.maestro_director_preview_guidance = False
        request.state.maestro_director_preview_workflow = None
        request.state.maestro_director_preview_polish_mode = "off"
        request.state.maestro_llm_cancel_handle = LlmCancellationHandle()
        with patch(
            "services.llm_operations.run_blocking_shielded",
            new=_blocking_shield_stub(shielded_calls),
        ), patch("traceback.print_exc") as print_exc:
            with self.assertRaises(_HTTPException) as raised:
                asyncio.run(namespace["director_v2_plan"](request))

        self.assertEqual(len(shielded_calls), 1)
        self.assertIs(
            shielded_calls[0][0],
            namespace["_run_authorized_llm_with_selection"],
        )
        self.assertEqual(operations, [shielded_calls[0][1][2]])
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
        request.state.maestro_director_preview_worker = True
        request.state.maestro_director_preview_selection = {"provider": "local"}
        request.state.maestro_director_preview_assist = None
        request.state.maestro_director_preview_guidance = False
        request.state.maestro_director_preview_workflow = None
        request.state.maestro_director_preview_polish_mode = "off"
        request.state.maestro_llm_cancel_handle = LlmCancellationHandle()
        with patch(
            "services.llm_operations.run_blocking_shielded",
            new=_blocking_shield_stub(shielded_calls),
        ), patch("traceback.print_exc") as print_exc:
            with self.assertRaises(_HTTPException) as raised:
                asyncio.run(namespace["director_v2_plan"](request))

        self.assertEqual(len(shielded_calls), 1)
        self.assertIs(
            shielded_calls[0][0],
            namespace["_run_authorized_llm_with_selection"],
        )
        self.assertEqual(operations, [shielded_calls[0][1][2]])
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


class TestDirectorPreflightContract(unittest.TestCase):
    TRIO = {
        "video_model": "minimax_h3_pinkcherry_fl2va",
        "image_creator_model": "krea2_moody_mix_v7_fp8",
        "continuity_editor_model": "qwen_image_edit_2511",
    }
    FAILURE_CODES = frozenset({
        "director_model_unavailable",
        "director_model_not_ready",
        "director_model_terms_required",
        "director_role_lora_unavailable",
        "director_reference_unavailable",
    })
    FAILURE_COMPONENTS = frozenset({
        "video_model", "image_creator_model", "continuity_editor_model",
        "image_creator_lora", "continuity_editor_lora",
        "character_reference", "location_reference", "starting_image",
    })

    def _body(self, **updates):
        body = {
            "explicit_output": True,
            "pipeline_type": "short_film_story",
            **self.TRIO,
            "image_creator_loras": [{"id": "moody.safetensors", "multiplier": 1}],
            "continuity_editor_loras": [{
                "id": "uncensored.safetensors", "multiplier": 0.8,
            }],
            "director_resolution_preset": "720p",
            "director_aspect_ratio": "16:9",
            "reference_presence": {
                "starting_image": True,
                "character": False,
                "location": True,
            },
        }
        body.update(updates)
        return body

    def test_content_free_preflight_adapts_roles_and_has_no_runtime_side_effects(self):
        admitted = []

        def resolve(request, body, *, component_errors=False):
            admitted.append((request, copy.deepcopy(body), component_errors))
            body["_director_image_role_loras"] = {
                "creator": [{"id": "moody.safetensors"}],
                "editor": [{"id": "uncensored.safetensors"}],
            }
            return "roles"

        namespace = _launch_functions_namespace(
            ["_director_preflight_admission_body", "director_preflight"],
            _resolve_director_image_role_request=resolve,
            _director_component_error_response=lambda error: None,
            _director_validate_resolution_request=lambda *_args, **_kwargs: {
                "preset": "720p",
                "aspect_ratio": "16:9",
                "video_resolution": "1280x704",
                "image_resolution": "1280x720",
            },
        )
        request = _Request(self._body())
        response = asyncio.run(namespace["director_preflight"](request))
        self.assertEqual(response, {
            "status": "ready",
            "resolved": {
                "pipeline_type": "short_film_story",
                **self.TRIO,
                "director_resolution_preset": "720p",
                "director_aspect_ratio": "16:9",
                "video_resolution": "1280x704",
                "image_resolution": "1280x720",
            },
            "components": [
                {"component": "video_model", "status": "ready"},
                {"component": "image_creator_model", "status": "ready"},
                {"component": "continuity_editor_model", "status": "ready"},
                {"component": "image_creator_lora", "status": "ready"},
                {"component": "continuity_editor_lora", "status": "ready"},
                {"component": "starting_image", "status": "ready"},
                {"component": "character_reference", "status": "not_required"},
                {"component": "location_reference", "status": "ready"},
            ],
        })
        self.assertEqual(len(admitted), 1)
        admitted_body = admitted[0][1]
        self.assertTrue(admitted[0][2])
        self.assertEqual(
            admitted_body["image_editor_model"],
            self.TRIO["continuity_editor_model"],
        )
        self.assertNotIn("continuity_editor_model", admitted_body)
        self.assertEqual(
            admitted_body["image_editor_loras"],
            self._body()["continuity_editor_loras"],
        )

        source = LAUNCH_PATH.read_text(encoding="utf-8")
        function = next(
            node for node in ast.parse(source).body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "director_preflight"
        )
        implementation = ast.get_source_segment(source, function)
        for forbidden in (
            "_init_pipeline", "_authorize_director_media_inputs",
            "start_pipeline", "_begin_workspace_operation",
            "_resolve_authorized_request_media", "download_model", "load_models",
        ):
            self.assertNotIn(forbidden, implementation)

    def test_model_options_emits_the_authoritative_resolution_contract(self):
        model_def = {
            "architecture": "h3",
            "resolution_presets": {
                "720p": {
                    "label": "Native",
                    "values": {"16:9": "1344x768"},
                },
            },
            "resolution_preset_order": ["720p"],
            "supports_auto_aspect": False,
            "resolutions": [("Native", "1344x768")],
        }
        registry = type("Registry", (), {
            "get_model_def": lambda _self, _model: model_def,
            "get_default_settings": lambda _self, _model: {},
        })()
        namespace = _launch_functions_namespace(
            ["get_model_options"],
            wgp=registry,
            _require_remote_visible_models=lambda *_args: None,
            _model_resolution_contract=lambda definition: {
                "resolution_presets": definition["resolution_presets"],
                "resolution_preset_order": definition["resolution_preset_order"],
                "supports_auto_aspect": definition["supports_auto_aspect"],
            },
        )
        result = namespace["get_model_options"]("h3", _Request())
        self.assertEqual(result["resolution_preset_order"], ["720p"])
        self.assertEqual(
            result["resolution_presets"]["720p"]["values"]["16:9"],
            "1344x768",
        )
        self.assertFalse(result["supports_auto_aspect"])
        self.assertEqual(
            result["resolutions"],
            [{"label": "Native", "value": "1344x768"}],
        )

    def test_preflight_and_start_share_exact_resolution_validation(self):
        video = {
            "resolution_presets": {
                "720p": {"label": "720p", "values": {
                    "16:9": "1280x704", "auto": "auto_720p",
                }},
                "768p": {"label": "768p", "values": {
                    "16:9": "1344x768",
                }},
            },
            "resolution_preset_order": ["720p"],
            "supports_auto_aspect": True,
            "resolutions": [("Native", "1344x768")],
        }
        image = {}
        registry = type("Registry", (), {
            "get_model_def": lambda _self, model: {
                "video": video,
                "image": image,
                "editor": image,
                "different-editor": {
                    "resolution_presets": {
                        "720p": {"label": "720p", "values": {
                            "16:9": "1024x576",
                        }},
                    },
                    "resolution_preset_order": ["720p"],
                    "supports_auto_aspect": False,
                },
            }.get(model),
        })()
        default_presets = {
            "720p": {"label": "720p", "values": {"16:9": "1280x720"}},
        }
        namespace = _launch_functions_namespace(
            [
                "_model_resolution_contract",
                "_director_model_resolution",
                "_director_validate_resolution_request",
            ],
            wgp=registry,
            _DEFAULT_RESOLUTION_PRESETS=default_presets,
            _DEFAULT_RESOLUTION_PRESET_ORDER=["720p"],
            _RESOLUTION_ASPECT_VALUES=frozenset({"auto", "16:9"}),
        )
        validate = namespace["_director_validate_resolution_request"]
        selection = {
            "video_model": "video",
            "image_creator_model": "image",
            "image_editor_model": "editor",
            "director_resolution_preset": "720p",
            "director_aspect_ratio": "16:9",
        }
        resolved = validate(
            selection,
            require_selection=True,
            require_resolved_values=False,
        )
        self.assertEqual(resolved["video_resolution"], "1280x704")
        self.assertEqual(resolved["image_resolution"], "1280x720")
        exact_start = {
            **selection,
            "video_params": {"resolution": "1280x704"},
            "image_params": {"resolution": "1280x720"},
        }
        self.assertEqual(
            validate(
                exact_start,
                require_selection=False,
                require_resolved_values=True,
            )["video_resolution"],
            "1280x704",
        )
        for invalid in (
            {**exact_start, "video_params": {"resolution": "1280x720"}},
            {**exact_start, "director_resolution_preset": "1080p"},
            {**exact_start, "director_aspect_ratio": "9:16"},
            {**exact_start, "image_editor_model": "different-editor"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(_HTTPException) as raised:
                    validate(
                        invalid,
                        require_selection=False,
                        require_resolved_values=True,
                    )
                self.assertEqual(raised.exception.status_code, 400)

        # Hidden 768p is not a new selectable tier, but a recovered concrete
        # native value remains legal without inventing a preset/aspect pair.
        legacy = validate(
            {
                "video_model": "video",
                "video_params": {"resolution": "1344x768"},
            },
            require_selection=False,
            require_resolved_values=True,
        )
        self.assertTrue(legacy["legacy"])
        self.assertIsNone(legacy["preset"])

    def test_all_visible_moody_qwen_pinkcherry_resolve_independently(self):
        visible = set(self.TRIO.values())
        events = []

        def require_visible(request, models):
            events.append(("visible", request.state.maestro_remote, tuple(models)))
            if request.state.maestro_remote and any(
                model not in visible for model in models
            ):
                raise _HTTPException(status_code=404, detail="Model not found")

        def ready(model, *, image_role="", video_role=False, component=""):
            events.append(("ready", model, image_role, component, video_role))

        def loras(value, *, model_type, scope):
            events.append(("loras", model_type, scope, copy.deepcopy(value)))
            return list(value or ())

        namespace = _launch_functions_namespace(
            [
                "_director_component_error",
                "_director_require_visible_model",
                "_resolve_director_image_role_request",
            ],
            _DIRECTOR_FAILURE_CODES=self.FAILURE_CODES,
            _DIRECTOR_FAILURE_COMPONENTS=self.FAILURE_COMPONENTS,
            _require_remote_visible_models=require_visible,
            _require_h3_legal_execution=lambda _models: None,
            _migrate_director_final_video_postprocess=lambda body: None,
            _director_image_role_wire_mode=lambda body: "roles",
            _director_explicit_creator_resolution=lambda unrestricted: {},
            _DIRECTOR_DEFAULT_EDITOR_MODEL="unused",
            _director_model_ready_or_raise=ready,
            _director_role_lora_request=loras,
            _director_validate_workflow_or_raise=lambda body: events.append((
                "workflow", body["pipeline_type"], body["video_model"],
            )),
        )
        for remote in (False, True):
            with self.subTest(remote=remote):
                events.clear()
                request = _Request()
                request.state.maestro_remote = remote
                body = {
                    "explicit_output": True,
                    "pipeline_type": "short_film_story",
                    "video_model": self.TRIO["video_model"],
                    "image_creator_model": self.TRIO["image_creator_model"],
                    "image_editor_model": self.TRIO["continuity_editor_model"],
                    "image_creator_loras": [{"id": "moody.safetensors"}],
                    "image_editor_loras": [{"id": "uncensored.safetensors"}],
                }
                self.assertEqual(
                    namespace["_resolve_director_image_role_request"](
                        request, body, component_errors=True,
                    ),
                    "roles",
                )
                self.assertEqual(
                    [event[1] for event in events if event[0] == "ready"],
                    [
                        self.TRIO["image_creator_model"],
                        self.TRIO["continuity_editor_model"],
                        self.TRIO["video_model"],
                    ],
                )
                self.assertEqual(
                    [event[2] for event in events if event[0] == "loras"],
                    ["generation", "editing"],
                )
                self.assertEqual(
                    set(body["_director_image_role_loras"]),
                    {"creator", "editor"},
                )
                self.assertIn((
                    "workflow",
                    "short_film_story",
                    self.TRIO["video_model"],
                ), events)

    def test_preflight_rejects_shape_drift_and_projects_closed_errors_at_top_level(self):
        namespace = _launch_functions_namespace(
            [
                "_director_component_error_response",
                "_director_preflight_admission_body",
                "director_preflight",
            ],
            _DIRECTOR_FAILURE_CODES=self.FAILURE_CODES,
            _DIRECTOR_FAILURE_COMPONENTS=self.FAILURE_COMPONENTS,
            _resolve_director_image_role_request=lambda *_args, **_kwargs: (
                (_ for _ in ()).throw(_HTTPException(
                    status_code=404,
                    detail={
                        "code": "director_model_unavailable",
                        "component": "video_model",
                        "message": "Selected Director model is unavailable in this session.",
                    },
                ))
            ),
        )
        response = asyncio.run(namespace["director_preflight"](
            _Request(self._body()),
        ))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, {
            "code": "director_model_unavailable",
            "component": "video_model",
            "message": "Selected Director model is unavailable in this session.",
        })
        for invalid in (
            {**self._body(), "workspace": "private-project"},
            {**self._body(), "explicit_output": 1},
            {**self._body(), "pipeline_type": "unknown_workflow"},
            {**self._body(), "video_model": "   "},
            {**self._body(), "continuity_editor_model": " qwen_image_edit_2511"},
            {**self._body(), "reference_presence": {"starting_image": True}},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(_HTTPException) as raised:
                    namespace["_director_preflight_admission_body"](invalid)
                self.assertEqual(raised.exception.status_code, 400)

    def test_exact_workflow_compatibility_uses_service_validator_and_closed_error(self):
        namespace = _launch_functions_namespace(
            ["_director_component_error", "_director_validate_workflow_or_raise"],
            _DIRECTOR_FAILURE_CODES=self.FAILURE_CODES,
            _DIRECTOR_FAILURE_COMPONENTS=self.FAILURE_COMPONENTS,
        )
        body = {
            "pipeline_type": "short_film_story",
            "video_model": self.TRIO["video_model"],
            "seamless": False,
        }
        with patch.object(pipeline, "_validate_director_models") as validate:
            namespace["_director_validate_workflow_or_raise"](body)
        validate.assert_called_once_with(body, stages=("video",))

        with patch.object(
            pipeline,
            "_validate_director_models",
            side_effect=pipeline.DirectorModelCompatibilityError(
                "private model/workflow reason",
            ),
        ):
            with self.assertRaises(_HTTPException) as raised:
                namespace["_director_validate_workflow_or_raise"](body)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, {
            "code": "director_model_unavailable",
            "component": "video_model",
            "message": "Selected Director model is unavailable for this workflow.",
        })
        self.assertNotIn(
            "private model/workflow reason",
            json.dumps(raised.exception.detail),
        )

        h3_body = {
            "pipeline_type": "short_film_story",
            "video_model": "pinkcherry",
            "seamless": False,
        }
        with patch.object(
            pipeline, "_wgp", _Registry({"pinkcherry": _h3_video()}),
        ):
            namespace["_director_validate_workflow_or_raise"](h3_body)
            for incompatible in (
                {**h3_body, "pipeline_type": "music_video"},
                {**h3_body, "seamless": True},
                {**h3_body, "voice_reference": "present"},
            ):
                with self.subTest(incompatible=incompatible):
                    with self.assertRaises(_HTTPException) as raised:
                        namespace["_director_validate_workflow_or_raise"](
                            incompatible,
                        )
                    self.assertEqual(raised.exception.status_code, 404)
                    self.assertEqual(
                        raised.exception.detail["component"], "video_model",
                    )

    def test_model_admission_maps_unavailable_terms_and_readiness(self):
        state = {
            "model_def": None,
            "terms": None,
            "downloaded": True,
            "manifest_valid": True,
        }
        registry = type("Registry", (), {
            "get_model_def": lambda _self, _model: state["model_def"],
            "models_def": {},
        })()

        def require_terms(_models):
            if state["terms"] is not None:
                raise _HTTPException(status_code=409, detail=state["terms"])

        namespace = _launch_functions_namespace(
            ["_director_component_error", "_director_model_ready_or_raise"],
            _DIRECTOR_FAILURE_CODES=self.FAILURE_CODES,
            _DIRECTOR_FAILURE_COMPONENTS=self.FAILURE_COMPONENTS,
            wgp=registry,
            _require_model_recipe_terms=require_terms,
            _check_model_downloaded=lambda _model: state["downloaded"],
        )
        cases = (
            (None, None, True, True, 404, "director_model_unavailable"),
            ({}, None, True, False, 404, "director_model_unavailable"),
            ({}, "terms", True, True, 409, "director_model_terms_required"),
            ({}, None, False, True, 409, "director_model_not_ready"),
        )
        for model_def, terms, downloaded, manifest_valid, status, code in cases:
            with self.subTest(code=code):
                state.update({
                    "model_def": model_def,
                    "terms": terms,
                    "downloaded": downloaded,
                    "manifest_valid": manifest_valid,
                })
                with patch(
                    "services.model_terms.model_terms_manifest_valid",
                    side_effect=lambda *_args: state["manifest_valid"],
                ):
                    with self.assertRaises(_HTTPException) as raised:
                        namespace["_director_model_ready_or_raise"](
                            self.TRIO["video_model"], component="video_model",
                        )
                self.assertEqual(raised.exception.status_code, status)
                self.assertEqual(raised.exception.detail["code"], code)
                self.assertEqual(
                    raised.exception.detail["component"], "video_model",
                )

        state.update({
            "model_def": {},
            "terms": None,
            "downloaded": True,
            "manifest_valid": True,
        })
        registry.models_def = {"plain-video": {}}
        self.assertIsNone(namespace["_director_model_ready_or_raise"](
            "plain-video", component="video_model",
        ))

    def test_preflight_rejects_a_ready_model_with_no_director_video_role(self):
        model = {
            "name": "Still-only",
            "image_outputs": True,
        }
        registry = type("Registry", (), {
            "models_def": {"still": model},
            "get_model_def": lambda _self, _model: model,
            "get_model_family": lambda _self, _model, for_ui=True: "test",
            "get_base_model_type": lambda _self, _model: "still",
        })()
        namespace = _launch_functions_namespace(
            ["_director_component_error", "_director_model_ready_or_raise"],
            _DIRECTOR_FAILURE_CODES=self.FAILURE_CODES,
            _DIRECTOR_FAILURE_COMPONENTS=self.FAILURE_COMPONENTS,
            wgp=registry,
            _require_model_recipe_terms=lambda _models: None,
            _check_model_downloaded=lambda _model: True,
        )
        with patch(
            "services.model_terms.model_terms_manifest_valid",
            return_value=True,
        ):
            with self.assertRaises(_HTTPException) as raised:
                namespace["_director_model_ready_or_raise"](
                    "still", video_role=True, component="video_model",
                )
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail["code"], "director_model_unavailable")
        self.assertEqual(raised.exception.detail["component"], "video_model")

    def test_role_lora_failures_remain_bound_to_the_selected_role(self):
        failing_scope = ["generation"]

        def resolve_loras(_value, *, model_type, scope):
            if scope == failing_scope[0]:
                raise _HTTPException(
                    status_code=409,
                    detail=f"unavailable for {model_type}",
                )
            return []

        namespace = _launch_functions_namespace(
            ["_director_component_error", "_resolve_director_image_role_request"],
            _DIRECTOR_FAILURE_CODES=self.FAILURE_CODES,
            _DIRECTOR_FAILURE_COMPONENTS=self.FAILURE_COMPONENTS,
            _migrate_director_final_video_postprocess=lambda body: None,
            _director_image_role_wire_mode=lambda body: "roles",
            _director_explicit_creator_resolution=lambda unrestricted: {},
            _DIRECTOR_DEFAULT_EDITOR_MODEL="unused",
            _director_require_visible_model=lambda *_args: None,
            _director_model_ready_or_raise=lambda *_args, **_kwargs: None,
            _director_role_lora_request=resolve_loras,
        )
        for scope, component in (
            ("generation", "image_creator_lora"),
            ("editing", "continuity_editor_lora"),
        ):
            with self.subTest(component=component):
                failing_scope[0] = scope
                with self.assertRaises(_HTTPException) as raised:
                    namespace["_resolve_director_image_role_request"](
                        _Request(),
                        {
                            "video_model": self.TRIO["video_model"],
                            "image_creator_model": self.TRIO["image_creator_model"],
                            "image_editor_model": self.TRIO["continuity_editor_model"],
                        },
                        component_errors=True,
                    )
                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(raised.exception.detail, {
                    "code": "director_role_lora_unavailable",
                    "component": component,
                    "message": "Selected Director role LoRA is unavailable.",
                })


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
            _h3_job_model_types=lambda _job: (),
            _require_h3_legal_execution=lambda _models: None,
            _require_job_krea_actor_admission=lambda _job: None,
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
