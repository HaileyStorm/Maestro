"""Project isolation and durability for the first Continuum Editor slice."""

from __future__ import annotations

import os
import ast
import asyncio
import copy
import hashlib
import hmac
import json
import re
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

from fastapi import HTTPException


_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.editor_projects import (  # noqa: E402
    EditorProjectError,
    apply_output_video_trim,
    create_editor_project,
    create_output_video_timeline,
    list_editor_projects,
    load_editor_project,
    resolve_editor_asset,
    save_editor_project,
)


class TestEditorProjectFoundation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.outputs = os.path.join(self.temp.name, "outputs")
        self.uploads = os.path.join(self.temp.name, "uploads")
        for directory in (self.outputs, self.uploads):
            os.mkdir(directory)

    def _workspace(self, name: str) -> str:
        path = os.path.join(self.outputs, name)
        os.mkdir(path)
        return path

    def test_output_timeline_reopens_by_exact_revision_and_saves_only_trim(self):
        self._workspace("scene")
        media = {"type": "video", "duration": 20.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True}
        first = create_output_video_timeline(
            workspace="scene", output_name="clip.mp4", output_revision="a-b.c-d", media=media,
        )
        same = create_output_video_timeline(
            workspace="scene", output_name="clip.mp4", output_revision="a-b.c-d", media=media,
        )
        changed = create_output_video_timeline(
            workspace="scene", output_name="clip.mp4", output_revision="new-revision", media=media,
        )
        self.assertEqual(first["id"], same["id"])
        self.assertNotEqual(first["id"], changed["id"])
        first = save_editor_project(self.outputs, "scene", first, expected_revision=0)
        proposed = dict(first, name="Forged name", assets={"source-video": {"path": "/private"}})
        proposed["tracks"] = [{
            "id": "video-main", "items": [{"source_in": 2.0, "duration": 11.0}],
        }]
        updated = apply_output_video_trim(first, proposed)
        self.assertEqual((updated["name"], updated["assets"]), (first["name"], first["assets"]))
        self.assertEqual(updated["tracks"][0]["items"][0]["source_in"], 2.0)
        self.assertEqual(updated["tracks"][0]["items"][0]["duration"], 11.0)
        with self.assertRaisesRegex(EditorProjectError, "valid source range"):
            apply_output_video_trim(first, dict(proposed, tracks=[{
                "id": "video-main", "items": [{"source_in": 19.0, "duration": 4.0}],
            }]))

    def test_round_trip_and_stale_autosave_cannot_overwrite_newer_edit(self):
        self._workspace("scene-a")
        project = create_editor_project(name="My cut", workspace="scene-a")
        created = save_editor_project(self.outputs, "scene-a", project, expected_revision=0)
        self.assertEqual(created["revision"], 1)
        stale = dict(created)
        changed = dict(created, name="Second edit")
        saved = save_editor_project(self.outputs, "scene-a", changed, expected_revision=1)
        self.assertEqual(saved["revision"], 2)
        with self.assertRaisesRegex(EditorProjectError, "reload"):
            save_editor_project(self.outputs, "scene-a", stale, expected_revision=1)
        loaded = load_editor_project(self.outputs, "scene-a", created["id"])
        self.assertEqual((loaded["name"], loaded["revision"]), ("Second edit", 2))
        self.assertEqual(list_editor_projects(self.outputs, "scene-a")[0]["name"], "Second edit")
        self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(
            os.path.join(self.outputs, "scene-a", ".maestro_editor")
        )))

    def test_project_switch_rejects_stale_document_and_source(self):
        source_a = self._workspace("scene-a")
        source_b = self._workspace("scene-b")
        media_a = os.path.join(source_a, "clip.mp4")
        with open(media_a, "wb") as handle:
            handle.write(b"clip")
        with open(os.path.join(source_b, "clip.mp4"), "wb") as handle:
            handle.write(b"different clip")
        project = create_editor_project(workspace="scene-a")
        with self.assertRaisesRegex(EditorProjectError, "different workspace"):
            save_editor_project(self.outputs, "scene-b", project, expected_revision=0)
        with self.assertRaisesRegex(EditorProjectError, "different workspace"):
            resolve_editor_asset(
                {"name": "clip.mp4", "origin": "output", "workspace": "scene-a"},
                save_root=self.outputs, workspace="scene-b", uploads_root=self.uploads,
            )
        with self.assertRaises(EditorProjectError):
            resolve_editor_asset(
                {"name": "clip.mp4", "origin": "output", "path": media_a},
                save_root=self.outputs, workspace="scene-b", uploads_root=self.uploads,
            )
        self.assertEqual(resolve_editor_asset(
            {"name": "clip.mp4", "origin": "output", "workspace": "scene-a"},
            save_root=self.outputs, workspace="scene-a", uploads_root=self.uploads,
        ), media_a)

    def test_saved_asset_uses_opaque_output_id_without_client_host_path(self):
        self._workspace("scene")
        project = create_editor_project(workspace="scene")
        project["assets"]["clip"] = {
            "id": "clip", "name": "clip.mp4", "type": "video",
            "origin": "output", "workspace": "scene", "output_id": "unit-clip",
            "output_revision": "sha256:abc123",
            "path": "/private/other-user/clip.mp4", "url": "https://example.invalid/secret",
        }
        saved = save_editor_project(self.outputs, "scene", project, expected_revision=0)
        self.assertEqual(saved["assets"]["clip"]["output_id"], "unit-clip")
        self.assertEqual(saved["assets"]["clip"]["output_revision"], "sha256:abc123")
        self.assertNotIn("path", saved["assets"]["clip"])
        self.assertNotIn("url", saved["assets"]["clip"])
        self.assertEqual(
            load_editor_project(self.outputs, "scene", saved["id"])["assets"]["clip"],
            saved["assets"]["clip"],
        )

    def test_links_and_global_upload_fallback_do_not_cross_project_boundary(self):
        scene = self._workspace("scene")
        outside = os.path.join(self.temp.name, "private.mp4")
        with open(outside, "wb") as handle:
            handle.write(b"private")
        os.symlink(outside, os.path.join(scene, "clip.mp4"))
        with self.assertRaises(EditorProjectError):
            resolve_editor_asset(
                {"name": "clip.mp4", "origin": "output"},
                save_root=self.outputs, workspace="scene", uploads_root=self.uploads,
            )
        with open(os.path.join(self.uploads, "private.wav"), "wb") as handle:
            handle.write(b"private")
        with self.assertRaisesRegex(EditorProjectError, "Upload import"):
            resolve_editor_asset(
                {"name": "private.wav", "origin": "upload"},
                save_root=self.outputs, workspace="scene", uploads_root=self.uploads,
            )
        os.symlink(self.temp.name, os.path.join(self.outputs, "linked"))
        with self.assertRaises(EditorProjectError):
            save_editor_project(
                self.outputs, "linked", create_editor_project(workspace="linked"),
                expected_revision=0,
            )

    def test_project_file_symlink_cannot_be_read_or_followed_on_save(self):
        scene = self._workspace("scene")
        directory = os.path.join(scene, ".maestro_editor")
        os.mkdir(directory)
        outside = os.path.join(self.temp.name, "private.json")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write('{"name":"private"}')
        project = create_editor_project(workspace="scene")
        os.symlink(outside, os.path.join(directory, project["id"] + ".json"))
        with self.assertRaises(EditorProjectError):
            load_editor_project(self.outputs, "scene", project["id"])
        with self.assertRaises(EditorProjectError):
            save_editor_project(self.outputs, "scene", project, expected_revision=0)
        self.assertEqual(list_editor_projects(self.outputs, "scene"), [])


class _EditorRequest:
    def __init__(self, body):
        self.body = body

    async def stream(self):
        yield json.dumps(self.body).encode("utf-8")


class TestEditorProjectRoutes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.outputs = os.path.join(self.temp.name, "outputs")
        self.scene = os.path.join(self.outputs, "scene")
        os.makedirs(self.scene)
        self.clip = os.path.join(self.scene, "clip.mp4")
        with open(self.clip, "wb") as handle:
            handle.write(b"fixture")
        self.revision = "a-b.c-d"
        self.private = False
        self.permissions = []
        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        wanted = {
            "_editor_request_body", "_editor_save_root",
            "_editor_require_current_source", "open_output_editor_project",
            "save_output_editor_project", "serve_file",
        }
        selected = []
        for node in ast.parse(launch_path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
                cloned = copy.deepcopy(node)
                cloned.decorator_list = []
                selected.append(cloned)
        self.assertEqual({node.name for node in selected}, wanted)
        module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))

        def authorize(_request, project, *, existing_only=False, permission=None):
            self.permissions.append((project, existing_only, permission))
            if project != "scene":
                raise HTTPException(status_code=403, detail="Project access denied")
            return self.scene

        def output(_request, project, name):
            authorize(_request, project, existing_only=True, permission="project.mutate")
            if name != "clip.mp4":
                raise HTTPException(status_code=404, detail="Output not found")
            return self.scene, self.clip, {"private": self.private}

        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "json": json,
            "hmac": hmac,
            "re": re,
            "os": os,
            "subprocess": subprocess,
            "wgp": types.SimpleNamespace(server_config={"save_path": self.outputs}),
            "_reserve_workspace_operations": lambda _project: nullcontext(),
            "_require_project_access": authorize,
            "_require_authorized_output": output,
            "_output_revision": lambda *_args: self.revision,
            "_output_share_revision": lambda *_args: "sha256:" + hashlib.sha256(
                Path(self.clip).read_bytes() + (b"private" if self.private else b""),
            ).hexdigest(),
            "_request_project_workspace": lambda _request, workspace: workspace,
            "public_output_policy": lambda sidecar: {"private": bool(sidecar.get("private"))},
            "_GALLERY_MEDIA_EXTENSIONS": {".mp4"},
        }
        exec(compile(module, str(launch_path), "exec"), namespace)
        self.routes = namespace

    def test_open_replay_trim_and_stale_or_foreign_mutation(self):
        media = {
            "type": "video", "duration": 12.0, "width": 1280,
            "height": 720, "fps": 30.0, "has_audio": True,
        }
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        with mock.patch("services.editor_projects.probe_media", return_value=media):
            opened = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
            replay = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
        self.assertEqual((opened["id"], opened["revision"]), (replay["id"], replay["revision"]))
        self.assertEqual(opened["revision"], 1)
        self.assertEqual(opened["assets"]["source-video"]["output_revision"],
                         "sha256:" + hashlib.sha256(b"fixture").hexdigest())
        self.assertFalse(opened["assets"]["source-video"]["private"])
        proposed = copy.deepcopy(opened)
        proposed["name"] = "Forged title"
        proposed["assets"]["source-video"]["name"] = "other.mp4"
        clip = proposed["tracks"][0]["items"][0]
        clip.update(source_in=2.0, duration=6.0)
        save_request = _EditorRequest({"project": proposed, "expected_revision": 1})
        saved = asyncio.run(self.routes["save_output_editor_project"]("scene", opened["id"], save_request))["project"]
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["name"], opened["name"])
        self.assertEqual(saved["assets"]["source-video"]["name"], "clip.mp4")
        self.assertEqual(saved["tracks"][0]["items"][0]["source_in"], 2.0)
        with self.assertRaises(HTTPException) as stale:
            asyncio.run(self.routes["save_output_editor_project"]("scene", opened["id"], save_request))
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as foreign:
            asyncio.run(self.routes["open_output_editor_project"]("other", request))
        self.assertEqual(foreign.exception.status_code, 403)
        with open(self.clip, "wb") as handle:
            handle.write(b"changed")
        with self.assertRaises(HTTPException) as changed:
            asyncio.run(self.routes["save_output_editor_project"](
                "scene", saved["id"], _EditorRequest({"project": saved, "expected_revision": 2}),
            ))
        self.assertEqual(changed.exception.status_code, 409)
        self.assertTrue(all(permission == "project.mutate" for _, _, permission in self.permissions))

    def test_invalid_video_is_uneditable_not_a_stale_conflict(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        with mock.patch("services.editor_projects.probe_media", return_value={
            "type": "video", "duration": 0,
        }):
            with self.assertRaises(HTTPException) as invalid:
                asyncio.run(self.routes["open_output_editor_project"]("scene", request))
        self.assertEqual(invalid.exception.status_code, 422)

    def test_replacement_during_probe_cannot_create_draft_with_stale_media_or_privacy(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        media = {
            "type": "video", "duration": 12.0, "width": 1280,
            "height": 720, "fps": 30.0, "has_audio": True,
        }

        def replace_during_probe(_path):
            self.private = True
            with open(self.clip, "wb") as handle:
                handle.write(b"changed")
            return media

        with mock.patch("services.editor_projects.probe_media", side_effect=replace_during_probe):
            with self.assertRaises(HTTPException) as changed:
                asyncio.run(self.routes["open_output_editor_project"]("scene", request))
        self.assertEqual(changed.exception.status_code, 409)
        self.assertEqual(list_editor_projects(self.outputs, "scene"), [])

    def test_privacy_change_during_probe_does_not_create_draft(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})

        def change_privacy(_path):
            self.private = True
            return {
                "type": "video", "duration": 12.0, "width": 1280,
                "height": 720, "fps": 30.0, "has_audio": True,
            }

        with mock.patch("services.editor_projects.probe_media", side_effect=change_privacy):
            with self.assertRaises(HTTPException) as changed:
                asyncio.run(self.routes["open_output_editor_project"]("scene", request))
        self.assertEqual(changed.exception.status_code, 409)
        self.assertEqual(list_editor_projects(self.outputs, "scene"), [])

    def test_changed_while_hashing_is_a_stale_source_conflict(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        media = {
            "type": "video", "duration": 12.0, "width": 1280,
            "height": 720, "fps": 30.0, "has_audio": True,
        }
        real_revision = self.routes["_output_share_revision"]

        def interrupted_hash(*_args):
            raise OSError("Output changed while reading")

        try:
            self.routes["_output_share_revision"] = interrupted_hash
            with self.assertRaises(HTTPException) as opening:
                asyncio.run(self.routes["open_output_editor_project"]("scene", request))
            self.assertEqual(opening.exception.status_code, 409)
        finally:
            self.routes["_output_share_revision"] = real_revision

        with mock.patch("services.editor_projects.probe_media", return_value=media):
            opened = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
        try:
            self.routes["_output_share_revision"] = interrupted_hash
            with self.assertRaises(HTTPException) as saving:
                asyncio.run(self.routes["save_output_editor_project"](
                    "scene", opened["id"], _EditorRequest({
                        "project": opened, "expected_revision": opened["revision"],
                    }),
                ))
            self.assertEqual(saving.exception.status_code, 409)
            with self.assertRaises(HTTPException) as preview:
                self.routes["serve_file"](
                    request, "clip.mp4", workspace="scene",
                    content_revision=opened["assets"]["source-video"]["output_revision"],
                )
            self.assertEqual(preview.exception.status_code, 409)
        finally:
            self.routes["_output_share_revision"] = real_revision

    def test_preview_rejects_same_name_replacement_even_when_gallery_token_is_unchanged(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        with mock.patch("services.editor_projects.probe_media", return_value={
            "type": "video", "duration": 12.0, "width": 1280, "height": 720,
            "fps": 30.0, "has_audio": True,
        }):
            opened = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
        original_revision = opened["assets"]["source-video"]["output_revision"]
        self.private = True
        with open(self.clip, "wb") as handle:
            handle.write(b"replace")
        with self.assertRaises(HTTPException) as stale_preview:
            self.routes["serve_file"](
                request, "clip.mp4", workspace="scene", content_revision=original_revision,
            )
        self.assertEqual(stale_preview.exception.status_code, 409)
        with self.assertRaises(HTTPException) as stale_save:
            asyncio.run(self.routes["save_output_editor_project"](
                "scene", opened["id"], _EditorRequest({
                    "project": opened, "expected_revision": opened["revision"],
                }),
            ))
        self.assertEqual(stale_save.exception.status_code, 409)

    def test_oversized_editor_payload_is_rejected_before_parse(self):
        with self.assertRaises(HTTPException) as oversized:
            asyncio.run(self.routes["_editor_request_body"](_EditorRequest({"blob": "x" * 70000})))
        self.assertEqual(oversized.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
