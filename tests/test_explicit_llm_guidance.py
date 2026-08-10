"""Contracts for request-scoped explicit LLM prompt-authoring guidance."""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import llm_service  # noqa: E402
from services.director import nsfw_guidance, prompt_polish  # noqa: E402
from services.director import guide_loader as director_guide_loader  # noqa: E402
from services import guide_loader as shared_guide_loader  # noqa: E402
from services import director_pipeline  # noqa: E402


GUIDES = (
    APP / "services/llm_guides/director/nsfw_screenplay_rules.md",
    APP / "services/llm_guides/director/nsfw_video_rules.md",
    APP / "services/llm_guides/director/nsfw_image_rules.md",
    APP / "services/llm_guides/enhance/nsfw_shared.md",
)


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""


def _launch_functions(*names: str, **namespace):
    path = APP / "launch.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = []
    wanted = set(names)
    if any(name.startswith("director_plan_") for name in wanted):
        wanted.add("_run_llm_route_operation")
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in wanted
        ):
            node.decorator_list = []
            selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace.setdefault("_promote_external_llm_request", lambda _request: None)
    namespace.setdefault(
        "_resolve_direct_llm_selection",
        lambda _request: namespace.get("_configured_llm_selection", lambda: {})(),
    )
    namespace.setdefault("_llm_route_progress_callback", lambda _request: None)
    namespace.setdefault(
        "_resolved_local_response_assist",
        lambda _body, _selection: None,
    )
    namespace.setdefault(
        "_run_authorized_llm_with_selection",
        lambda _request, selection, operation, *args, **kwargs: namespace[
            "_run_llm_with_selection"
        ](selection, operation, *args, **kwargs),
    )
    namespace.setdefault(
        "traceback", types.SimpleNamespace(print_exc=lambda: None),
    )
    namespace.setdefault(
        "_request_project_workspace",
        lambda _request, workspace: workspace or "default",
    )
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


class ExplicitGuideTextTests(unittest.TestCase):
    def test_every_guide_is_forceful_concrete_and_scope_bounded(self):
        for path in GUIDES:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                lowered = text.casefold()
                self.assertIn("explicit content authoring", lowered)
                self.assertIn("this request is explicit-authorized", lowered)
                self.assertIn("her center", lowered)
                self.assertIn("physical intimacy", lowered)
                self.assertIn("graphic violence", lowered)
                self.assertIn("brutal attack", lowered)
                self.assertIn("chronological order", lowered)
                self.assertIn("user-specified speaker name", lowered)
                self.assertIn("never rename", lowered)
                self.assertIn("stable", lowered)
                self.assertIn("do not", lowered)
                self.assertTrue(
                    "never invent" in lowered or "do not invent" in lowered,
                )
                self.assertIn("escalat", lowered)

    def test_injection_matrix_preserves_non_explicit_behavior(self):
        base = "ROLE\n\nOrdinary model rules."
        safe = nsfw_guidance.inject_content_guidance(base, False, "enhance")
        explicit = nsfw_guidance.inject_content_guidance(base, True, "enhance")
        self.assertEqual(safe, base)
        self.assertIn("EXPLICIT CONTENT AUTHORING", explicit)
        self.assertIn("her center", explicit)
        self.assertIn("GRAPHIC-VIOLENCE REQUESTS", explicit)
        self.assertTrue(explicit.startswith(base))

    def test_spoken_action_lines_keep_names_and_authored_words(self):
        for path in (GUIDES[0], GUIDES[1], GUIDES[3]):
            with self.subTest(path=path.name):
                lowered = path.read_text(encoding="utf-8").casefold()
                self.assertIn("fights, sex, threats, pain/reaction exchanges", lowered)
                self.assertIn("every spoken or vocal line", lowered)
                self.assertIn("preserve authored dialogue wording verbatim", lowered)
                self.assertIn("user-specified speaker name", lowered)
                self.assertIn("short, natural exclamations and vocal reactions", lowered)
                self.assertIn("synchronize it to the exact action", lowered)
                self.assertIn("avoid long speeches and generic narration", lowered)
                self.assertIn("never invent sexual content when it is absent", lowered)


class RequestGateTests(unittest.TestCase):
    def _load_launch_gate(self, services: dict):
        launch_path = APP / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_explicit_llm_guidance_allowed"
        )
        namespace = {
            "wgp": types.SimpleNamespace(server_config={"services": services}),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(launch_path), "exec"), namespace)
        return namespace["_explicit_llm_guidance_allowed"]

    def test_gate_requires_literal_true_consent_and_non_public_provider(self):
        allowed = {
            "nsfw_mode": True,
            "nsfw_accepted_at": "2026-08-08T00:00:00Z",
            "llm_provider": "local",
        }
        gate = self._load_launch_gate(allowed)
        self.assertTrue(gate({"explicit_output": True}))
        self.assertFalse(gate({"explicit_output": 1}))
        self.assertFalse(gate({"explicit_output": "true"}))
        self.assertFalse(gate({}))

        for override in (
            {"nsfw_mode": False},
            {"nsfw_accepted_at": ""},
            {"llm_provider": "openai"},
            {"llm_provider": "Anthropic"},
        ):
            with self.subTest(override=override):
                services = dict(allowed)
                services.update(override)
                self.assertFalse(
                    self._load_launch_gate(services)({"explicit_output": True}),
                )

    def test_missing_guidance_authorization_does_not_refuse_local_content(self):
        services = {
            "nsfw_mode": False,
            "nsfw_accepted_at": "",
            "llm_provider": "local",
        }

        class FakeRequest:
            def __init__(self, remote: bool):
                self.state = types.SimpleNamespace(maestro_remote=remote)

            async def json(self):
                return {
                    "prompt": "Locally processed sensitive creative text.",
                    "explicit_output": True,
                    "mode": "video",
                }

        namespace = _launch_functions(
            "_explicit_llm_guidance_allowed",
            "llm_enhance_prompt",
            Request=object,
            HTTPException=RuntimeError,
            asyncio=asyncio,
            os=__import__("os"),
            wgp=types.SimpleNamespace(
                server_config={"services": services, "enhancer_enabled": 0},
            ),
            _get_active_workspace=lambda: "default",
            _request_project_workspace=(
                lambda _request, workspace: workspace or "default"
            ),
            _require_project_access=lambda *_args: "default",
            _resolve_authorized_request_media=lambda *_args: None,
            _resolve_prompt_enhancer_selection=(
                lambda *_args, **_kwargs: ("", "", False)
            ),
            _configured_llm_selection=lambda: {},
            _run_llm_with_selection=(
                lambda _selection, operation, *args, **kwargs:
                operation(*args, **kwargs)
            ),
        )
        with mock.patch.object(
            llm_service,
            "enhance_prompt",
            return_value="Locally processed sensitive creative text.",
        ) as enhance:
            local = asyncio.run(
                namespace["llm_enhance_prompt"](FakeRequest(False)),
            )
            remote = asyncio.run(
                namespace["llm_enhance_prompt"](FakeRequest(True)),
            )

        self.assertEqual(local, remote)
        self.assertEqual(
            local["enhanced"], "Locally processed sensitive creative text.",
        )
        self.assertEqual(enhance.call_count, 2)
        self.assertTrue(all(
            call.kwargs["nsfw"] is False for call in enhance.call_args_list
        ))

    def test_structural_image_cleanup_does_not_filter_sensitive_subjects(self):
        prompt = (
            "A nude adult couple poses beside graphic battlefield gore, "
            "static tableau, cinematic lighting."
        )
        self.assertEqual(prompt_polish.sanitize_image_prompt(prompt), prompt)

    def test_full_director_planner_passes_sensitive_prompt_locally_and_via_cloudflare(self):
        services = {
            "nsfw_mode": False,
            "nsfw_accepted_at": "",
            "llm_provider": "local",
        }
        sensitive = (
            "A nude consenting adult survives graphic battlefield violence, "
            "then gives a controversial political speech."
        )
        observed = []

        class FakeRequest:
            def __init__(self, remote):
                self.state = types.SimpleNamespace(maestro_remote=remote)

            async def json(self):
                return {
                    "workspace": "project-a",
                    "clips": [{"start": 0, "end": 5}],
                    "scene_description": sensitive,
                    "explicit_output": False,
                }

        namespace = _launch_functions(
            "_explicit_llm_guidance_allowed",
            "director_plan_prompts_and_images",
            Request=object,
            HTTPException=RuntimeError,
            traceback=types.SimpleNamespace(print_exc=lambda: None),
            wgp=types.SimpleNamespace(server_config={"services": services}),
            _authorize_director_media_inputs=lambda *_args: "project-a",
            _configured_llm_selection=lambda: {},
            _run_llm_with_selection=(
                lambda _selection, operation, *args, **kwargs:
                operation(*args, **kwargs)
            ),
        )
        planned = [{"video_prompt": sensitive, "image_prompt": sensitive}]
        with mock.patch.object(
            llm_service,
            "plan_clip_prompts_and_images",
            side_effect=lambda **kwargs: observed.append(kwargs) or planned,
        ):
            local = asyncio.run(namespace["director_plan_prompts_and_images"](FakeRequest(False)))
            remote = asyncio.run(namespace["director_plan_prompts_and_images"](FakeRequest(True)))

        self.assertEqual(local, remote)
        self.assertEqual(local, {"clip_plans": planned})
        self.assertEqual([call["scene_description"] for call in observed], [sensitive, sensitive])
        self.assertTrue(all(call["nsfw"] is False for call in observed))


class EnhancerPropagationTests(unittest.TestCase):
    def test_raw_enhancer_gets_strong_context_only_when_authorized(self):
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return "enhanced"

        with mock.patch.object(llm_service, "generate", side_effect=fake_generate):
            llm_service.enhance_prompt(
                "synthetic request", raw_enhancer_mode=True, nsfw=False,
            )
            llm_service.enhance_prompt(
                "synthetic request", raw_enhancer_mode=True, nsfw=True,
            )

        self.assertEqual(calls[0]["system_prompt"], "")
        self.assertIn("EXPLICIT CONTENT AUTHORING", calls[1]["system_prompt"])
        self.assertIn("brutal attack", calls[1]["system_prompt"])

    def test_launch_routes_propagate_request_scoped_decision(self):
        launch = APP / "launch.py"
        enhance = _function_source(launch, "llm_enhance_prompt")
        chat = _function_source(launch, "_execute_llm_chat")
        preview = _function_source(launch, "director_v2_plan")
        start = _function_source(launch, "director_pipeline_start")

        self.assertIn("explicit_guidance = _explicit_llm_guidance_allowed(body)", enhance)
        self.assertIn("and not explicit_guidance", enhance)
        self.assertIn("nsfw=explicit_guidance", enhance)
        self.assertIn("_explicit_llm_guidance_allowed(body)", chat)
        self.assertIn("inject_content_guidance", chat)
        self.assertIn('planner_kwargs["nsfw"] = _explicit_llm_guidance_allowed(body)', preview)
        self.assertIn("body.pop(EXPLICIT_GUIDANCE_SNAPSHOT_KEY, None)", preview)
        self.assertIn("body.pop(EXPLICIT_GUIDANCE_SNAPSHOT_KEY, None)", start)

    def test_composed_video_rules_preserve_dialogue_names_after_visual_rules(self):
        captured = {}

        def fake_generate(**kwargs):
            captured.update(kwargs)
            return "enhanced synthetic prompt"

        with (
            mock.patch(
                "services.enhance_guides.get_enhance_guide",
                return_value="BASE MODEL GUIDE",
            ),
            mock.patch.object(llm_service, "generate", side_effect=fake_generate),
        ):
            llm_service.enhance_prompt(
                "synthetic request", mode="video", nsfw=True,
            )

        system = captured["system_prompt"]
        explicit_at = system.index("EXPLICIT CONTENT AUTHORING")
        visual_at = system.index("CHARACTER REFERENCES")
        dialogue_override_at = system.index(
            "SPOKEN/VOCAL DIALOGUE OVERRIDES THE VISUAL-NAME RULE",
        )
        self.assertLess(explicit_at, visual_at)
        self.assertLess(visual_at, dialogue_override_at)
        self.assertIn("preserve every user-specified name", system)
        self.assertIn("non-dialogue action/camera prose", system)

    def test_provider_mutation_during_model_prepare_fails_before_send(self):
        services = {
            "nsfw_mode": True,
            "nsfw_accepted_at": "2026-08-08T00:00:00Z",
            "llm_provider": "local",
        }

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        class FakeRequest:
            state = types.SimpleNamespace(
                maestro_generation_preparation=False,
            )

            async def json(self):
                return {
                    "prompt": "synthetic explicit request",
                    "explicit_output": True,
                    "mode": "video",
                }

        class MutatingAsyncio:
            @staticmethod
            async def to_thread(function, *args, **kwargs):
                result = function(*args, **kwargs)
                services["llm_provider"] = "openai"
                return result

        namespace = _launch_functions(
            "_explicit_llm_guidance_allowed",
            "_resolve_prompt_enhancer_selection",
            "llm_enhance_prompt",
            Request=object,
            HTTPException=FakeHTTPException,
            asyncio=MutatingAsyncio,
            os=__import__("os"),
            wgp=types.SimpleNamespace(
                server_config={"services": services, "enhancer_enabled": 0},
            ),
            _DEFAULT_ENHANCE_LLM_REPO=(
                "Youssofal/Qwen3.6-27B-Abliterated-Heretic-Uncensored-GGUF"
            ),
            _get_active_workspace=lambda: "default",
            _request_project_workspace=(
                lambda _request, workspace: workspace or "default"
            ),
            _require_project_access=lambda *_args: "default",
            _resolve_authorized_request_media=lambda *_args: None,
            _configured_llm_selection=lambda: {},
            _run_llm_with_selection=(
                lambda _selection, operation, *args, **kwargs: (
                    services.__setitem__("llm_provider", "openai")
                    or operation(*args, **kwargs)
                )
            ),
        )
        with (
            mock.patch.object(llm_service, "load_model"),
            mock.patch.object(
                llm_service, "enhance_prompt",
                side_effect=AssertionError("explicit prompt reached provider"),
            ),
            self.assertRaises(FakeHTTPException) as raised,
        ):
            asyncio.run(namespace["llm_enhance_prompt"](FakeRequest()))
        self.assertEqual(raised.exception.status_code, 409)

    def test_task_and_capability_based_model_routing(self):
        enhancer = "Youssofal/Qwen3.6-27B-Abliterated-Heretic-Uncensored-GGUF"
        heavy = "MoonRide/gemma-4-31B-it-heretic-ara-GGUF"
        text_only = "unsloth/Qwen3.5-2B-GGUF"
        definitions = {
            "dedicated-vision-model": {
                "prompt_enhancer_model": "owner/dedicated-vision",
            },
            "dedicated-text-model": {
                "prompt_enhancer_model": "owner/dedicated-text",
            },
        }
        registry = {
            enhancer: {"mmproj_file": "qwen-mmproj.gguf"},
            text_only: {"mmproj_file": None},
            "owner/dedicated-vision": {"mmproj_file": "dedicated-mmproj.gguf"},
            "owner/dedicated-text": {"mmproj_file": None},
        }
        namespace = _launch_functions(
            "_resolve_prompt_enhancer_selection",
            "_resolve_vision_llm_selection",
            wgp=types.SimpleNamespace(
                server_config={"services": {
                    "enhance_llm_model_id": enhancer,
                    "enhance_llm_device": "cuda",
                }},
                get_model_def=lambda model_type: definitions.get(model_type),
            ),
            _DEFAULT_ENHANCE_LLM_REPO=enhancer,
        )
        resolve = namespace["_resolve_prompt_enhancer_selection"]
        self.assertEqual(resolve("", {}), (enhancer, "cuda", False))
        self.assertEqual(
            resolve("", {"enhance_llm_model_id": text_only}),
            (text_only, "cuda", False),
        )
        self.assertEqual(
            resolve(
                "",
                {"enhance_llm_model_id": text_only},
                has_images=True,
                model_registry=registry,
            ),
            (enhancer, "cuda", False),
        )
        with mock.patch.object(llm_service, "MODEL_REGISTRY", registry):
            self.assertEqual(namespace["_resolve_vision_llm_selection"](), {
                "model_id": enhancer,
                "device": "cuda",
                "provider": "local",
                "remote_url": "",
                "api_key": "",
                "local_gguf_path": "",
                "gguf_file_override": "",
            })
        self.assertEqual(
            resolve(
                "dedicated-vision-model",
                {"enhance_llm_model_id": enhancer},
                has_images=True,
                model_registry=registry,
            ),
            ("owner/dedicated-vision", "cuda", True),
        )
        self.assertEqual(
            resolve(
                "dedicated-text-model",
                {"enhance_llm_model_id": enhancer},
                has_images=True,
                model_registry=registry,
            ),
            (enhancer, "cuda", False),
        )
        self.assertEqual(llm_service.DEFAULT_ENHANCE_HF_REPO, enhancer)
        self.assertEqual(llm_service.DEFAULT_HF_REPO, heavy)
        config = json.loads((APP / "wgp_config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["services"]["enhance_llm_model_id"], enhancer)
        self.assertEqual(config["services"]["llm_model_id"], heavy)
        self.assertTrue(llm_service.MODEL_REGISTRY[enhancer]["mmproj_file"])
        self.assertFalse(llm_service.MODEL_REGISTRY[text_only].get("mmproj_file"))


class LegacyPlannerTests(unittest.TestCase):
    def test_legacy_llm_planner_injects_only_when_explicit(self):
        systems = []

        def fake_generate(**kwargs):
            systems.append(kwargs["system_prompt"])
            return "1. synthetic cinematic prompt"

        clip = {"start": 0.0, "end": 5.0, "section_label": "verse"}
        with mock.patch.object(llm_service, "generate", side_effect=fake_generate):
            llm_service.plan_clip_prompts([clip], "synthetic", nsfw=False)
            llm_service.plan_clip_prompts([clip], "synthetic", nsfw=True)
        self.assertNotIn("EXPLICIT CONTENT AUTHORING", systems[0])
        self.assertIn("EXPLICIT CONTENT AUTHORING", systems[1])
        self.assertIn("GRAPHIC VIOLENCE", systems[1])

    def test_durable_legacy_pipeline_forwards_persisted_decision(self):
        key = nsfw_guidance.EXPLICIT_GUIDANCE_SNAPSHOT_KEY
        observed = {}

        def fake_plan(**kwargs):
            observed.update(kwargs)
            return []

        with mock.patch.object(
            llm_service, "plan_clip_prompts_and_images",
            side_effect=fake_plan,
        ):
            director_pipeline._run_planning_legacy(
                "synthetic-pipeline",
                {
                    key: True,
                    "scene_description": "synthetic",
                    "planned_clips": [{"start": 0, "end": 5}],
                },
                "music_video",
            )
        self.assertIs(observed["nsfw"], True)

    def test_legacy_http_planner_executes_request_gate(self):
        services = {
            "nsfw_mode": True,
            "nsfw_accepted_at": "2026-08-08T00:00:00Z",
            "llm_provider": "local",
        }
        observed = []
        progress = []
        progress_callback = progress.append

        class FakeRequest:
            async def json(self):
                return {
                    "clips": [{"start": 0, "end": 5}],
                    "scene_description": "synthetic",
                    "explicit_output": True,
                }

        namespace = _launch_functions(
            "_explicit_llm_guidance_allowed",
            "director_plan_prompts_and_images",
            Request=object,
            HTTPException=RuntimeError,
            traceback=types.SimpleNamespace(print_exc=lambda: None),
            wgp=types.SimpleNamespace(server_config={"services": services}),
            _authorize_director_media_inputs=lambda *_args: None,
            _configured_llm_selection=lambda: {},
            _llm_route_progress_callback=lambda _request: progress_callback,
            _resolved_local_response_assist=(
                lambda body, _selection: (
                    {"marker": "server-owned"}
                    if body.get("explicit_output") is True
                    and bool(services.get("nsfw_accepted_at"))
                    else None
                )
            ),
            _run_llm_with_selection=(
                lambda _selection, operation, *args, **kwargs:
                operation(*args, **kwargs)
            ),
        )
        local_result = [{
            "video_prompt": "Local model output is returned unchanged.",
            "image_prompt": "A private local-only test prompt.",
        }]
        with mock.patch.object(
            llm_service, "plan_clip_prompts_and_images",
            side_effect=lambda **kwargs: observed.append(kwargs) or local_result,
        ):
            result = asyncio.run(
                namespace["director_plan_prompts_and_images"](FakeRequest()),
            )
            def revoke_consent_before_operation(
                _selection, operation, *args, **kwargs,
            ):
                services["nsfw_accepted_at"] = ""
                return operation(*args, **kwargs)

            namespace["_run_llm_with_selection"] = (
                revoke_consent_before_operation
            )
            revoked = asyncio.run(
                namespace["director_plan_prompts_and_images"](FakeRequest()),
            )
        self.assertEqual(result, {"clip_plans": local_result})
        self.assertEqual(revoked, {"clip_plans": local_result})
        self.assertIs(observed[0]["nsfw"], True)
        self.assertEqual(
            observed[0]["response_assist"], {"marker": "server-owned"},
        )
        self.assertIs(observed[0]["progress_callback"], progress_callback)
        self.assertIs(observed[1]["nsfw"], False)
        self.assertIsNone(observed[1]["response_assist"])
        self.assertIs(observed[1]["progress_callback"], progress_callback)

    def test_all_legacy_http_prompt_planners_forward_the_gate(self):
        launch = APP / "launch.py"
        for name in (
            "director_plan_prompts",
            "director_plan_angle_prompts",
            "director_plan_prompts_and_images",
            "director_plan_short_film_prompts",
            "director_plan_short_film_script",
        ):
            with self.subTest(name=name):
                source = _function_source(launch, name)
                self.assertIn(
                    'explicit_guidance_keyword="nsfw"',
                    source,
                )
                self.assertIn("_run_llm_route_operation", source)


class GuideCacheTests(unittest.TestCase):
    def tearDown(self):
        director_guide_loader.load_guide.cache_clear()
        shared_guide_loader.load_guide.cache_clear()

    def test_guide_loaders_require_new_process_cache_for_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            director_file = root / "cached.md"
            director_file.write_text("first director", encoding="utf-8")
            director_guide_loader.load_guide.cache_clear()
            with mock.patch.object(director_guide_loader, "_GUIDES_DIR", tmp):
                self.assertEqual(
                    director_guide_loader.load_guide("cached.md"),
                    "first director",
                )
                director_file.write_text("second director", encoding="utf-8")
                self.assertEqual(
                    director_guide_loader.load_guide("cached.md"),
                    "first director",
                )
                director_guide_loader.load_guide.cache_clear()
                self.assertEqual(
                    director_guide_loader.load_guide("cached.md"),
                    "second director",
                )

            enhance_dir = root / "enhance"
            enhance_dir.mkdir()
            enhance_file = enhance_dir / "cached.md"
            enhance_file.write_text("first enhance", encoding="utf-8")
            shared_guide_loader.load_guide.cache_clear()
            with mock.patch.object(shared_guide_loader, "_GUIDES_ROOT", tmp):
                self.assertEqual(
                    shared_guide_loader.load_guide("enhance", "cached"),
                    "first enhance",
                )
                enhance_file.write_text("second enhance", encoding="utf-8")
                self.assertEqual(
                    shared_guide_loader.load_guide("enhance", "cached"),
                    "first enhance",
                )
                shared_guide_loader.load_guide.cache_clear()
                self.assertEqual(
                    shared_guide_loader.load_guide("enhance", "cached"),
                    "second enhance",
                )

    def test_restart_semantics_are_documented(self):
        self.assertIn("after Maestro restarts", shared_guide_loader.__doc__)
        self.assertIn("after a\nMaestro restart", nsfw_guidance.__doc__)


class DirectorSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.original_wgp = director_pipeline._wgp

    def tearDown(self):
        director_pipeline._wgp = self.original_wgp
        director_pipeline._pipelines.clear()

    @staticmethod
    def _services(provider="local"):
        return {
            "nsfw_mode": True,
            "nsfw_accepted_at": "2026-08-08T00:00:00Z",
            "llm_provider": provider,
        }

    def test_fresh_start_overwrites_client_snapshot_and_persists_decision(self):
        key = nsfw_guidance.EXPLICIT_GUIDANCE_SNAPSHOT_KEY
        with tempfile.TemporaryDirectory() as tmp:
            director_pipeline._wgp = types.SimpleNamespace(
                server_config={"services": self._services()},
                save_path=tmp,
            )
            params = {
                "explicit_output": True,
                key: False,
                "llm_provider": "openai",
                "pipeline_type": "music_video",
            }
            with (
                mock.patch.object(director_pipeline, "_validate_director_models"),
                mock.patch.object(
                    director_pipeline,
                    "_resolve_fresh_shot_image_policy",
                    return_value="generate",
                ),
                mock.patch.object(director_pipeline, "_start_pipeline_worker"),
            ):
                pid = director_pipeline.start_pipeline(params)
            self.assertIs(
                director_pipeline._pipelines[pid]["params"][key], True,
            )
            self.assertEqual(
                director_pipeline._pipelines[pid]["params"]["llm_provider"],
                "local",
            )
            saved = Path(
                tmp, director_pipeline.pipeline_state_filename(pid),
            ).read_text(encoding="utf-8")
            self.assertIn(f'"{key}": true', saved)

    def test_public_provider_overwrites_injected_true_and_recovery_is_snapshot_only(self):
        key = nsfw_guidance.EXPLICIT_GUIDANCE_SNAPSHOT_KEY
        director_pipeline._wgp = types.SimpleNamespace(
            server_config={"services": self._services("openai")},
        )
        self.assertFalse(
            director_pipeline._fresh_explicit_guidance_decision(
                {"explicit_output": True, key: True},
            )
        )
        self.assertTrue(director_pipeline._explicit_guidance_from_snapshot({key: True}))
        self.assertFalse(director_pipeline._explicit_guidance_from_snapshot({key: 1}))
        self.assertFalse(director_pipeline._explicit_guidance_from_snapshot({}))

    def test_public_saved_state_scrubs_private_guidance_decision(self):
        key = nsfw_guidance.EXPLICIT_GUIDANCE_SNAPSHOT_KEY
        namespace = _launch_functions(
            "_public_pipeline_state",
            copy=copy,
            _redact_local_paths=lambda value: value,
            _sanitize_director_public_failures=lambda value: value,
        )
        source = {
            "pipeline_id": "synthetic",
            "_params_snapshot": {
                key: True,
                "explicit_output": True,
                "video_model": "synthetic-model",
            },
        }
        public = namespace["_public_pipeline_state"](source)
        self.assertNotIn(key, public["_params_snapshot"])
        self.assertIs(source["_params_snapshot"][key], True)
        self.assertIs(public["_params_snapshot"]["explicit_output"], True)

    def test_planning_and_polish_read_only_persisted_decision(self):
        path = APP / "services/director_pipeline.py"
        planner = _function_source(path, "_run_planning_v2")
        runner = _function_source(path, "_run_pipeline")
        starter = _function_source(path, "start_pipeline")
        restored = _function_source(path, "restore_registered_pipeline")
        self.assertIn("nsfw = _explicit_guidance_from_snapshot(params)", planner)
        self.assertIn("nsfw = _explicit_guidance_from_snapshot(params)", runner)
        self.assertIn("_fresh_explicit_guidance_decision(params)", starter)
        self.assertIn("_normalize_explicit_guidance_snapshot", restored)


if __name__ == "__main__":
    unittest.main()
