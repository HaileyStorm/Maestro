"""Offline contracts for Blender MCP launcher/API/UI integration."""
from __future__ import annotations

import ast
import copy
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from unittest import mock
from pathlib import Path

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
        self.assertLess(finalize.index("_require_blender_ready()"), finalize.index("_ensure_llm_loaded()"))
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
        component = (ROOT / "ui/src/components/Sidebar/BlenderSceneTool.tsx").read_text(encoding="utf-8")
        self.assertIn("<BlenderSceneTool", tools)
        self.assertIn("<BlenderSceneTool", refs)
        self.assertIn("compact\n", refs)
        self.assertIn('aria-label="Reference creation method"', refs)
        self.assertIn("planBlenderScene", component)
        self.assertIn("Run Director review → full video", component)
        self.assertIn("finalizeBlenderScene", component)
        self.assertIn("Approve reference", component)
        self.assertNotIn("Approve & sample", component)
        self.assertIn("review_frames", component)
        self.assertIn("const endFrame = frameCount - 1", component)
        self.assertIn("hosted limit", component)

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
        self.assertIn("can_access_output", resolve_asset)

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
        for value in (
            "setSidebarMode('studio')",
            "setGenerationMode('video')",
            "conditioned_prompt",
            "ic_lora_attention_strength",
            "ic_lora_reference_downscale",
            "setGuideVideoFps",
            "setGuideVideoFrameCount",
        ):
            self.assertIn(value, library)


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
