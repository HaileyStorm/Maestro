"""Offline contracts for Blender MCP launcher/API/UI integration."""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest import mock
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import HTTPException

from app.services.output_access import OutputShareManager, stamp_sidecar_policy

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "app" / "launch.py"
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _load_functions(*names, extra=None):
    source = LAUNCH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH))
    wanted = set(names)
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    namespace = {
        "HTTPException": HTTPException,
        "hashlib": hashlib,
        "hmac": hmac,
        "json": json,
        "os": os,
        "threading": threading,
        "time": __import__("time"),
        "uuid": uuid,
        "stamp_sidecar_policy": stamp_sidecar_policy,
        "_blender_candidate_status_lock": threading.RLock(),
    }
    namespace.update(extra or {})
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(LAUNCH), "exec"),
        namespace,
    )
    return namespace


class BlenderDirectorVisionTests(unittest.TestCase):
    def exercise(self, root, *, nonvision_lease=None, revision_mutator=None,
                 director_prompt="Move the cube across the scene", approve=True):
        from services.blender_mcp_service import BlenderMCPService

        selection = {
            "model_id": "configured-vision", "device": "cuda", "provider": "local",
            "remote_url": "", "api_key": "", "local_gguf_path": "",
            "gguf_file_override": "",
        }
        observations = SimpleNamespace(
            leases=[], calls=[], samples=[], videos=[], assets=[], inside=False,
            resolves=0, generated=0, vision_checks=0, status_checks=0,
            generation_inputs=[],
        )
        self.observations = observations
        plan = {
            "scene": {"clear_scene": True, "objects": [{
                "name": "Cube", "primitive": "cube", "location": [0, 0, 0],
                "material": {"name": "Blue", "color": [0.3, 0.5, 0.9, 1]},
            }]},
            "animation": {"frame_start": 0, "frame_end": 23, "objects": [{
                "name": "Cube", "keyframes": [
                    {"frame": 0, "location": [0, 0, 0], "interpolation": "LINEAR"},
                    {"frame": 23, "location": [2, 0, 0], "interpolation": "LINEAR"},
                ],
            }]},
            "review_frames": [0, 23], "fps": 24,
            "director_prompt": director_prompt,
            "semantic_mapping": {
                "legend": [{"object_name": "Cube", "primitive": "cube",
                            "color": [0.3, 0.5, 0.9, 1], "subject": "cube",
                            "action": "moves to the right"}],
                "conditioned_prompt": "moving cube",
            },
        }

        async def body():
            return {"workspace": "protected-project", "plan": plan, "max_attempts": 2}

        request = SimpleNamespace(
            json=body, method="POST",
            state=SimpleNamespace(maestro_session_id="a" * 32, maestro_remote=False),
        )

        def promote(req):
            req.state.maestro_remote = True

        def project_access(req, workspace):
            self.assertTrue(req.state.maestro_remote)
            self.assertEqual(workspace, "protected-project")
            return str(root)

        def resolve():
            observations.resolves += 1
            return dict(selection)

        def lease(selected, operation, **_kwargs):
            self.assertFalse(observations.inside)
            observations.leases.append(dict(selected))
            observations.inside = True
            try:
                return operation()
            finally:
                observations.inside = False

        def vision_available():
            self.assertTrue(observations.inside)
            observations.vision_checks += 1
            return len(observations.leases) != nonvision_lease

        def status():
            self.assertTrue(observations.inside)
            observations.status_checks += 1
            return {"model_id": observations.leases[-1]["model_id"]}

        def generate(**kwargs):
            self.assertTrue(observations.inside)
            self.assertEqual(len(kwargs["image_paths"]), 2)
            self.assertTrue(all(Path(path).is_file() for path in kwargs["image_paths"]))
            observations.generation_inputs.append(copy.deepcopy(kwargs))
            observations.generated += 1
            if observations.generated == 1 or not approve:
                revised = copy.deepcopy({
                    "verdict": "revise", "analysis": "Move farther",
                    "scene": plan["scene"], "animation": plan["animation"],
                    "review_frames": [0, 23],
                    "semantic_mapping": plan["semantic_mapping"],
                })
                revised["animation"]["objects"][0]["keyframes"][1]["location"] = [3, 0, 0]
                if revision_mutator:
                    revision_mutator(revised)
                return json.dumps(revised)
            return json.dumps({"verdict": "approved", "analysis": "Motion is readable"})

        def invoke(tool, args):
            self.assertFalse(observations.inside, "Blender must not render under the LLM lease")
            observations.calls.append(tool)
            if tool == "render_preview":
                # A concurrent settings change must not replace the frozen selection.
                selection["model_id"] = "changed-text-chat"
                outputs = []
                for frame in args["frames"]:
                    path = root / (Path(args["output_path"]).stem + f"_{frame}.png")
                    path.write_bytes(b"offline-frame")
                    outputs.append({"output_path": str(path)})
                return {"outputs": outputs}
            if tool == "render_animation":
                path = root / args["output_path"]
                path.write_bytes(b"offline-video")
                return {"output_path": str(path)}
            return {}

        def preview_sidecar(project, name, **kwargs):
            observations.samples.append(kwargs)
            (root / (Path(name).stem + ".meta.json")).write_text("{}")

        def create_asset(*args, **kwargs):
            observations.assets.append(kwargs)
            return {"id": "asset", "variants": [{"id": "variant"}]}

        @contextmanager
        def render_slot():
            self.assertFalse(observations.inside)
            yield

        def forbidden(*args, **kwargs):
            self.fail("Blender review must not use global text-chat dispatch")

        # Real pure validators exercise the revision boundary; the transport,
        # rendering and model remain offline.
        service = BlenderMCPService(SimpleNamespace(), root)
        service.invoke = invoke
        llm = SimpleNamespace(
            vision_available=vision_available, get_status=status, generate=generate,
        )
        namespace = _load_functions(
            "blender_director_finalize", "_run_authorized_llm_with_selection",
            "_normalize_blender_semantic_mapping", "_normalize_blender_review_frames",
            extra={
                "api": SimpleNamespace(post=lambda _path: lambda function: function),
                "Request": object, "asyncio": asyncio, "quote": quote,
                "_promote_external_llm_request": promote,
                "_llm_chat_request_is_external": lambda req: req.state.maestro_remote,
                "_require_project_access": project_access,
                "_get_active_workspace": forbidden,
                "_require_blender_ready": lambda: None,
                "output_policy_from_request": lambda *args, **kwargs: {
                    "private": False, "explicit": False,
                },
                "_blender_service_for": lambda *args: service,
                "_resolve_vision_llm_selection": resolve,
                "_run_llm_with_selection": lease,
                "_ensure_llm_loaded": forbidden,
                "_run_configured_llm_operation": forbidden,
                "_blender_scene_lock": threading.RLock(),
                "_BlenderGpuRenderSlot": render_slot,
                "_write_blender_preview_sidecar": preview_sidecar,
                "_write_blender_video_sidecar": lambda *args, **kwargs:
                    observations.videos.append(kwargs),
                "_blender_plan_json": json.loads,
                "_project_asset_store": lambda: SimpleNamespace(create_asset=create_asset),
                "_blender_error": lambda error: HTTPException(503, str(error)),
            },
        )
        with mock.patch("services.llm_service", llm, create=True):
            return asyncio.run(namespace["blender_director_finalize"](request))

    def test_review_freezes_vision_selection_and_records_leased_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.exercise(Path(temporary))
        observed = self.observations
        self.assertEqual(result["status"], "awaiting_user_review")
        self.assertEqual([item["verdict"] for item in result["director_reviews"]], ["revise", "approved"])
        self.assertEqual(observed.resolves, 1)
        self.assertEqual(len(observed.leases), 3)
        self.assertEqual(observed.vision_checks, 3)
        self.assertEqual(observed.status_checks, 3)
        self.assertTrue(all(item == observed.leases[0] for item in observed.leases))
        self.assertEqual(observed.leases[0]["model_id"], "configured-vision")
        self.assertTrue(all(item["policy"]["private"] for item in observed.samples))
        self.assertEqual(result["director_model"], "configured-vision")
        self.assertEqual(observed.videos[0]["director_model"], "configured-vision")
        candidate = observed.assets[0]["variants"][0]
        self.assertEqual(candidate["metadata"]["director_model"], "configured-vision")
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(observed.calls.count("render_animation"), 1)

    def test_review_supplies_the_contract_to_the_model_without_mutating_tools(self):
        from services.blender_mcp_service import PUBLIC_TOOL_SCHEMAS

        original = copy.deepcopy(PUBLIC_TOOL_SCHEMAS)
        with tempfile.TemporaryDirectory() as temporary:
            result = self.exercise(
                Path(temporary), director_prompt="Adult characters in a controversial violent drama",
            )
        self.assertEqual(result["status"], "awaiting_user_review")
        inputs = self.observations.generation_inputs[0]
        self.assertIn(json.dumps(inputs["json_schema"]), inputs["prompt"])
        self.assertIn("Adult characters in a controversial violent drama", inputs["prompt"])
        self.assertEqual(PUBLIC_TOOL_SCHEMAS, original)
        self.assertNotIn('"$ref"', json.dumps(inputs["json_schema"]))

    def test_invalid_revisions_remain_unpublished_and_clean_all_review_frames(self):
        def arbitrary_code(value):
            value["scene"]["objects"][0]["python"] = "bpy.ops.object.delete()"

        def out_of_range(value):
            value["animation"]["objects"][0]["keyframes"][1]["frame"] = 24

        def conflicting_legend(value):
            value["semantic_mapping"]["legend"][0]["color"] = [1, 0, 0, 1]

        for mutate in (arbitrary_code, out_of_range, conflicting_legend):
            with self.subTest(mutate=mutate.__name__), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(HTTPException) as failure:
                    self.exercise(Path(temporary), revision_mutator=mutate)
                self.assertEqual(failure.exception.status_code, 422)
                self.assertEqual(list(Path(temporary).iterdir()), [])
                self.assertNotIn("render_animation", self.observations.calls)
                self.assertEqual(self.observations.calls.count("scene_create"), 1)
                self.assertEqual(self.observations.assets, [])
                self.assertEqual(self.observations.videos, [])

    def test_nonvision_load_stops_before_scene_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(HTTPException) as failure:
                self.exercise(Path(temporary), nonvision_lease=1)
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual(failure.exception.status_code, 409)
        self.assertIn("Prompt Enhance", failure.exception.detail)
        self.assertEqual(self.observations.calls, [])
        self.assertEqual(self.observations.generated, 0)

    def test_nonapproval_returns_feedback_without_a_candidate_or_review_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(HTTPException) as failure:
                self.exercise(Path(temporary), approve=False)
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(failure.exception.detail, {
            "code": "blender_review_not_approved",
            "message": "Director could not approve this animation. Adjust the scene or request, then review again.",
            "review_count": 2, "feedback": "Move farther",
        })
        self.assertEqual(self.observations.generated, 2)
        self.assertNotIn("render_animation", self.observations.calls)
        self.assertEqual(self.observations.assets, [])

    def test_lost_vision_capability_cleans_samples_without_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(HTTPException) as failure:
                self.exercise(Path(temporary), nonvision_lease=3)
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(self.observations.generated, 1)
        self.assertNotIn("render_animation", self.observations.calls)
        self.assertEqual(self.observations.assets, [])
        self.assertEqual(self.observations.videos, [])


class _FakeAssetStore:
    def __init__(self, variant, *, fail_status=False):
        self.variant = copy.deepcopy(variant)
        self.asset_metadata = {
            "tool": "blender_mcp",
            "director_reviewed": True,
            "director_approved": False,
            "artifact_lineage": variant["metadata"]["artifact_lineage"],
        }
        self.fail_status = fail_status
        self.status_calls = []

    def get_asset(self, _project, _workspace, _asset):
        return {
            "id": "asset",
            "metadata": copy.deepcopy(self.asset_metadata),
            "variants": [copy.deepcopy(self.variant)],
        }

    def get_variant(self, _project, _workspace, _asset, _variant):
        return copy.deepcopy(self.variant)

    def set_variant_status(self, _project, _workspace, _asset, _variant, status):
        self.status_calls.append(status)
        if self.fail_status:
            raise OSError("injected manifest failure")
        self.variant["status"] = status
        return copy.deepcopy(self.variant)


class BlenderLaunchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = LAUNCH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(LAUNCH))

    def function(self, name: str) -> str:
        node = next(
            item for item in self.tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )
        return ast.get_source_segment(self.source, node)

    def test_every_blender_operation_is_project_scoped_and_structured(self):
        invoke = self.function("_invoke_blender_project_tool")
        render = self.function("blender_render_preview")
        plan = self.function("blender_director_plan")

        self.assertIn("_require_project_access", invoke)
        self.assertIn("service.invoke", invoke)
        self.assertIn("_require_project_access", render)
        self.assertIn("_write_blender_preview_sidecar", render)
        self.assertIn("_project_asset_store().create_asset", render)
        self.assertIn("_normalize_scene_create", plan)
        self.assertIn("_normalize_animation", plan)
        self.assertNotIn('body.get("code")', self.source)
        self.assertIn("frame_count = max(1, round(duration * fps))", plan)
        self.assertIn("frame_end = frame_count - 1", plan)
        self.assertIn("BlenderMCPLimits().max_total_frames", plan)

    def test_readiness_matrix_gates_actions_before_director_model_work(self):
        status = self.function("blender_mcp_status")
        invoke = self.function("_invoke_blender_project_tool")
        plan = self.function("blender_director_plan")
        finalize = self.function("blender_director_finalize")
        component = (ROOT / "ui/src/components/Sidebar/BlenderSceneTool.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn("_blender_readiness(runtime)", status)
        for key in ("mcp_attested", "runtime_attested", "mcp_sdk_ready", "bridge_ready"):
            self.assertIn(f'"{key}"', self.function("_blender_readiness"))
            if key != "mcp_sdk_ready":
                self.assertIn(f"readiness.{key}", component)
        self.assertIn("_require_blender_ready()", invoke)
        self.assertLess(plan.index("_require_blender_ready()"), plan.index("_ensure_llm_loaded()"))
        self.assertLess(finalize.index("_require_blender_ready()"), finalize.index("_run_authorized_llm_with_selection("))
        self.assertNotIn("disabled={!installed", component)
        self.assertGreaterEqual(component.count("disabled={!ready"), 6)
        self.assertIn("Verify / Repair Blender Runtime", self.function("_blender_readiness"))

    def test_missing_standard_mcp_sdk_makes_blender_unready_with_repair_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "blender_mcp"
            (checkout / "mcp" / "blmcp").mkdir(parents=True)
            (checkout / "mcp" / "blmcp" / "__init__.py").write_text("", encoding="utf-8")
            from app.services.blender_mcp_service import PINNED_INSTALL
            (checkout / ".maestro-attested").write_text(
                json.dumps({
                    "repository": PINNED_INSTALL.repository,
                    "tag": PINNED_INSTALL.tag,
                    "revision": PINNED_INSTALL.revision,
                    "package_version": PINNED_INSTALL.package_version,
                    "transport": PINNED_INSTALL.transport,
                }),
                encoding="utf-8",
            )
            readiness = _load_functions(
                "_blender_readiness",
                extra={
                    "_blender_checkout_root": lambda: str(checkout),
                    "_blender_runtime_info": lambda: {"version": "5.1.2"},
                    "_blender_mcp_sdk_ready": lambda: False,
                    "_blender_bridge_ready": lambda: True,
                },
            )["_blender_readiness"]({"version": "5.1.2"})

        self.assertTrue(readiness["installed"])
        self.assertFalse(readiness["ready"])
        self.assertFalse(readiness["mcp_sdk_ready"])
        self.assertIn("Verify / Repair Blender MCP Support", readiness["recovery_action"])

    def test_standard_mcp_sdk_probe_fails_closed_on_missing_or_incomplete_api(self):
        probe = _load_functions("_blender_mcp_sdk_ready")["_blender_mcp_sdk_ready"]
        real_import = __import__

        def missing_mcp(name, *args, **kwargs):
            if name == "mcp" or name.startswith("mcp."):
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=missing_mcp):
            self.assertFalse(probe())

        def incomplete_mcp(name, *args, **kwargs):
            if name == "mcp":
                return type("IncompleteMCP", (), {})()
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=incomplete_mcp):
            self.assertFalse(probe())

        def broken_mcp(name, *args, **kwargs):
            if name == "mcp" or name.startswith("mcp."):
                raise RuntimeError("incompatible transitive dependency")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=broken_mcp):
            self.assertFalse(probe())

    def test_pinned_install_and_lifecycle_are_wired(self):
        installer = (ROOT / "blender_mcp_install.js").read_text(encoding="utf-8")
        runtime_installer = (ROOT / "blender_runtime_install.js").read_text(encoding="utf-8")
        runtime_start = (ROOT / "blender_runtime_start.js").read_text(encoding="utf-8")
        pin = "03004fd0216bfe5e0a3d9ac9b47d5efadc3d78c4"
        self.assertIn("https://projects.blender.org/lab/blender_mcp.git", installer)
        self.assertIn(pin, installer)
        self.assertIn("provision-mcp --destination services/blender_mcp", installer)
        self.assertIn("!exists('app/services/blender_mcp/.maestro-attested')", installer)
        self.assertIn("remote set-url origin https://projects.blender.org/lab/blender_mcp.git", installer)
        self.assertIn("attest-mcp --checkout services/blender_mcp", installer)
        self.assertIn('uv pip install "mcp[cli]==1.12.4" services/blender_mcp/mcp', installer)
        requirements = (ROOT / "app" / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("mcp[cli]==1.12.4", requirements)
        self.assertIn("provision-runtime", runtime_installer)
        self.assertLess(
            runtime_installer.index("provision-runtime"),
            runtime_installer.index("https://download.blender.org"),
        )
        self.assertIn("blender-5.1.2-linux-x64.tar.xz", runtime_installer)
        self.assertIn("aaccb355f50183979b698bcce7467103a76261b5fa59f4972295842662a285fb", runtime_installer)
        self.assertIn("attest-runtime --marker tools/blender/runtime.json", runtime_start)
        self.assertIn("blender_mcp_install.js", (ROOT / "install.js").read_text(encoding="utf-8"))
        self.assertIn("blender_mcp_install.js", (ROOT / "update.js").read_text(encoding="utf-8"))
        self.assertIn("app/services/blender_mcp", (ROOT / "reset.js").read_text(encoding="utf-8"))
        menu = (ROOT / "pinokio.js").read_text(encoding="utf-8")
        self.assertIn("Verify / Repair Blender MCP Support", menu)
        self.assertIn("Install Blender MCP Support", menu)
        self.assertIn('href: "blender_mcp_install.js"', menu)
        self.assertIn("atexit.register(_close_blender_services)", self.source)

    def test_tools_and_director_reference_ui_both_expose_blender(self):
        tools = (ROOT / "ui/src/components/Sidebar/ToolsPanel.tsx").read_text(encoding="utf-8")
        refs = (ROOT / "ui/src/components/Sidebar/ProjectReferenceLibrary.tsx").read_text(encoding="utf-8")
        sidebar = (ROOT / "ui/src/components/Sidebar/Sidebar.tsx").read_text(encoding="utf-8")
        component = (ROOT / "ui/src/components/Sidebar/BlenderSceneTool.tsx").read_text(encoding="utf-8")
        self.assertIn("<BlenderSceneTool", tools)
        self.assertIn("['blender', 'Blender']", tools)
        self.assertIn("<BlenderSceneTool", refs)
        self.assertIn("compact\n", refs)
        self.assertIn('aria-label="Reference creation method"', refs)
        self.assertIn("Blender Motion Video", refs)
        self.assertIn("Blender also remains available under Tools.", refs)
        self.assertIn("aria-hidden={!active}", refs)
        self.assertIn("\n      hidden={!active}", refs)
        self.assertIn("<ProjectReferenceLibrary active={isReference} />", sidebar)
        self.assertIn("planBlenderScene", component)
        self.assertIn("Plan, review, and render", component)
        self.assertIn("finalizeBlenderScene", component)
        self.assertIn("Keep motion video", component)
        self.assertNotIn("Approve reference", component)
        self.assertNotIn("Approve & sample", component)
        self.assertIn("review_frames", component)
        self.assertIn("const endFrame = frameCount - 1", component)
        self.assertIn("Maestro limit", component)

    def test_no_camera_error_is_actionable_without_exposing_host_paths(self):
        blender_error = _load_functions("_blender_error")["_blender_error"]
        from services.blender_mcp_service import BlenderMCPToolError

        response = blender_error(BlenderMCPToolError("Cannot render, no camera"))
        self.assertEqual(response.status_code, 503)
        self.assertIn("prepare a camera", response.detail)
        self.assertIn("Recreate the structured scene", response.detail)
        self.assertNotIn(str(ROOT), response.detail)

    def test_director_finalize_publishes_a_review_candidate_not_a_final(self):
        writer = self.function("_write_blender_video_sidecar")
        finalize = self.function("blender_director_finalize")
        status_route = self.function("set_project_asset_variant_status")
        public_assets = self.function("_public_authorized_project_assets")
        serve_asset = self.function("serve_project_asset_media")
        resolve_asset = self.function("_resolve_authorized_project_asset_media")
        self.assertIn('"artifact_class": "temporary"', writer)
        self.assertIn('"director_approved": False', writer)
        self.assertIn('"user_review_status": "candidate"', writer)
        self.assertIn('"status": "candidate"', finalize)
        self.assertNotIn('"director_approved": True', finalize)
        self.assertIn('"gallery_output_filename"', finalize)
        self.assertIn('"review_owner_session_hash"', finalize)
        self.assertIn("_set_blender_candidate_status", status_route)
        self.assertIn("_can_access_project_asset_variant", public_assets)
        self.assertIn("_can_access_project_asset_variant", serve_asset)
        self.assertIn("_can_access_project_asset_variant", resolve_asset)
        self.assertNotIn("can_access_output", resolve_asset)

    def test_semantic_mapping_is_normalized_preserved_and_handed_to_studio(self):
        normalize = _load_functions("_normalize_blender_semantic_mapping")[
            "_normalize_blender_semantic_mapping"
        ]
        scene = {
            "objects": [{
                "name": "HeroGuide",
                "primitive": "cube",
                "material": {"name": "HeroBlue", "color": [0.1, 0.2, 0.8, 1.0]},
            }]
        }
        mapping = normalize(
            {
                "legend": [{
                    "object_name": "HeroGuide",
                    "primitive": "cube",
                    "color": [0.1, 0.2, 0.8, 1.0],
                    "subject": "the lead performer",
                    "action": "crosses the room",
                }],
                "conditioned_prompt": "The blue cube drives the lead performer crossing the room.",
            },
            scene,
            director_prompt="A performer crosses a room",
        )
        self.assertEqual(mapping["legend"][0]["object_name"], "HeroGuide")
        self.assertEqual(mapping["legend"][0]["subject"], "the lead performer")
        self.assertIn("blue cube", mapping["conditioned_prompt"])
        self.assertEqual(
            normalize(None, scene, director_prompt="ignored", fallback=mapping),
            mapping,
        )
        with self.assertRaisesRegex(ValueError, "not in the normalized scene"):
            normalize(
                {"legend": [{"object_name": "Missing", "subject": "x", "action": "y"}]},
                scene,
                director_prompt="prompt",
            )

        finalize = self.function("blender_director_finalize")
        self.assertIn('verdict.get("semantic_mapping")', finalize)
        self.assertIn('"semantic_mapping": semantic_mapping', finalize)
        library = (ROOT / "ui/src/components/Sidebar/ProjectReferenceLibrary.tsx").read_text(
            encoding="utf-8"
        )
        apply_start = library.index("  const applyReference = async")
        apply_end = library.index("\n\n  return (", apply_start)
        apply_reference = library[apply_start:apply_end]
        video_apply, _director_apply = apply_reference.split(
            "} else if (referenceReturnMode === 'director')", 1,
        )
        classification = apply_reference.split(
            "const isBlenderControlVideo = Boolean(", 1,
        )[1].split("\n      )", 1)[0]
        subprocess.run([
            "node", "-e", r"""
const assert = require('node:assert/strict');
const expression = JSON.parse(process.argv[1]);
const isControl = new Function('output', 'metadata', 'return Boolean(' + expression + ');');
for (const metadata of [
  {recommended_video_prompt_type: 'TVG'},
  {semantic_mapping: {conditioned_prompt: 'A performer crosses the room'}},
  {recommended_model_type: 'ltx2_22B_1_1'},
]) {
  assert.equal(isControl({media_type: 'video/mp4'}, metadata), true);
  assert.equal(isControl({media_type: 'image/png'}, metadata), false);
  assert.equal(isControl({media_type: 'audio/wav'}, metadata), false);
  assert.equal(isControl({}, metadata), false);
}
assert.equal(isControl({media_type: 'video/mp4'}, {}), false);
assert.equal(isControl({media_type: 'video/mp4'}, {conditioned_prompt: 'ordinary reference'}), false);
""", json.dumps(classification),
        ], check=True, capture_output=True, text=True, cwd=ROOT)
        for value in (
            "if (isBlenderControlVideo)",
            "destination = 'studio'",
            "setGenerationMode('video')",
            "conditioned_prompt",
            "ic_lora_attention_strength",
            "ic_lora_reference_downscale",
            "setGuideVideoFps",
            "setGuideVideoFrameCount",
            "setSidebarMode(destination)",
        ):
            self.assertIn(value, apply_reference)
        self.assertNotIn("addCharacterRef", video_apply)
        self.assertNotIn("addLocationRef", video_apply)
        self.assertIn(
            "Use in Generate as an LTX-2.3 control and prompt", library,
        )


class BlenderCandidateTransactionTests(unittest.TestCase):
    def _variant(self, filename="candidate.mp4"):
        return {
            "id": "variant",
            "variant_type": "blender_video",
            "status": "candidate",
            "provenance": "generated",
            "metadata": {
                "tool": "blender_mcp",
                "artifact_lineage": "blender-director:one",
                "gallery_output_filename": filename,
                "review_owner_session_hash": hashlib.sha256(
                    ("a" * 32).encode("utf-8")
                ).hexdigest(),
                "requested_private": False,
                "requested_explicit": False,
                "director_model": "vision-model",
            },
        }

    def _sidecar(self):
        return {
            "tool": "blender_mcp",
            "artifact_lineage": "blender-director:one",
            "artifact_class": "temporary",
            "director_reviewed": True,
            "director_approved": False,
            "user_review_status": "candidate",
            "director_model": "vision-model",
            "private": False,
            "explicit": False,
            "workspace": "project",
            "provenance_marker": "preserve-me",
        }

    def _function_for(self, store, revoked, revoke_fn=None):
        namespace = _load_functions(
            "_stage_json_replacement",
            "_restore_file_bytes",
            "_set_blender_candidate_status",
            extra={
                "_project_asset_store": lambda: store,
                "_revoke_output_shares": revoke_fn or (
                    lambda workspace, names: revoked.append((workspace, list(names)))
                ),
            },
        )
        return namespace["_set_blender_candidate_status"]

    def _write_candidate(self, directory):
        media = Path(directory, "candidate.mp4")
        media.write_bytes(b"video")
        sidecar = Path(directory, "candidate.meta.json")
        raw = (json.dumps(self._sidecar(), separators=(",", ":")) + "\n").encode()
        sidecar.write_bytes(raw)
        return sidecar, raw

    def test_video_sidecar_stays_non_final_until_user_review(self):
        namespace = _load_functions("_write_blender_video_sidecar")
        write_sidecar = namespace["_write_blender_video_sidecar"]
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "candidate.mp4").write_bytes(b"video")
            write_sidecar(
                directory,
                "candidate.mp4",
                workspace="project",
                policy={"private": False, "explicit": False},
                lineage="blender-director:one",
                frame_start=0,
                frame_end=23,
                fps=24,
                review_attempts=1,
                director_model="vision-model",
                control_mode="TVG",
                semantic_mapping={
                    "legend": [{
                        "object_name": "Guide",
                        "primitive": "cube",
                        "color": [0.2, 0.3, 0.4, 1.0],
                        "subject": "subject",
                        "action": "moves",
                    }],
                    "conditioned_prompt": "The cube controls the subject.",
                },
            )
            sidecar = json.loads(
                Path(directory, "candidate.meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sidecar["artifact_class"], "temporary")
            self.assertFalse(sidecar["director_approved"])
            self.assertEqual(sidecar["user_review_status"], "candidate")
            self.assertEqual(
                sidecar["semantic_mapping"]["legend"][0]["object_name"], "Guide"
            )
            self.assertEqual(
                sidecar["params"]["conditioned_prompt"],
                "The cube controls the subject.",
            )

    def test_keep_atomically_promotes_and_revokes_old_shares(self):
        with tempfile.TemporaryDirectory() as directory:
            sidecar_path, _ = self._write_candidate(directory)
            store = _FakeAssetStore(self._variant())
            revoked = []
            result = self._function_for(store, revoked)(
                "project", "main", "asset", "variant", "kept",
                out_dir=directory, session_id="a" * 32,
            )
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "kept")
            self.assertEqual(sidecar["artifact_class"], "final")
            self.assertTrue(sidecar["director_approved"])
            self.assertEqual(sidecar["user_review_status"], "kept")
            self.assertEqual(sidecar["provenance_marker"], "preserve-me")
            self.assertFalse(sidecar["private"])
            self.assertEqual(revoked, [("project", ["candidate.mp4"])])

    def test_reject_preserves_provenance_but_is_private_non_final_and_revoked(self):
        with tempfile.TemporaryDirectory() as directory:
            sidecar_path, _ = self._write_candidate(directory)
            store = _FakeAssetStore(self._variant())
            revoked = []
            share_manager = OutputShareManager(
                str(Path(directory, "shares.json")), b"s" * 32,
            )
            share = share_manager.create(
                workspace="project",
                filename="candidate.mp4",
                revision="before-review",
                media_type="video/mp4",
                explicit=False,
            )

            def revoke(workspace, names):
                revoked.append((workspace, list(names)))
                return sum(
                    share_manager.revoke(workspace=workspace, filename=name)
                    for name in names
                )

            result = self._function_for(store, revoked, revoke)(
                "project", "main", "asset", "variant", "rejected",
                out_dir=directory, session_id="b" * 32,
            )
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(sidecar["artifact_class"], "temporary")
            self.assertFalse(sidecar["director_approved"])
            self.assertEqual(sidecar["user_review_status"], "rejected")
            self.assertEqual(sidecar["provenance_marker"], "preserve-me")
            self.assertTrue(sidecar["private"])
            self.assertNotIn("owner_session_id", sidecar)
            self.assertEqual(revoked, [("project", ["candidate.mp4"])])
            self.assertIsNone(share_manager.resolve(share["token"]))

    def test_manifest_failure_restores_sidecar_status_after_revoke(self):
        with tempfile.TemporaryDirectory() as directory:
            sidecar_path, original = self._write_candidate(directory)
            store = _FakeAssetStore(self._variant(), fail_status=True)
            revoked = []
            with self.assertRaisesRegex(
                HTTPException, "Could not update the Blender review candidate",
            ):
                self._function_for(store, revoked)(
                    "project", "main", "asset", "variant", "kept",
                    out_dir=directory, session_id="c" * 32,
                )
            self.assertEqual(sidecar_path.read_bytes(), original)
            self.assertEqual(store.variant["status"], "candidate")
            self.assertEqual(revoked, [("project", ["candidate.mp4"])])
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_revoke_failure_leaves_exact_sidecar_and_manifest_status(self):
        with tempfile.TemporaryDirectory() as directory:
            sidecar_path, original = self._write_candidate(directory)
            store = _FakeAssetStore(self._variant())
            revoked = []

            def fail_revoke(_workspace, _names):
                raise OSError("injected durable share-store failure")

            with self.assertRaisesRegex(
                HTTPException, "Could not update the Blender review candidate",
            ):
                self._function_for(store, revoked, fail_revoke)(
                    "project", "main", "asset", "variant", "kept",
                    out_dir=directory, session_id="c" * 32,
                )
            self.assertEqual(sidecar_path.read_bytes(), original)
            self.assertEqual(store.variant["status"], "candidate")
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_unaccepted_copied_media_is_visible_only_to_its_review_owner(self):
        access = _load_functions("_can_access_project_asset_variant")[
            "_can_access_project_asset_variant"
        ]
        variant = self._variant()
        self.assertTrue(access(variant, "a" * 32))
        self.assertFalse(access(variant, "b" * 32))
        variant["status"] = "rejected"
        self.assertTrue(access(variant, "a" * 32))
        self.assertFalse(access(variant, "b" * 32))
        variant["status"] = "kept"
        self.assertTrue(access(variant, "b" * 32))


if __name__ == "__main__":
    unittest.main()
