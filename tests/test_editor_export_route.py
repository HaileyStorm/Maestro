"""Editor export keeps project, revision, privacy and queue publication bound."""

from __future__ import annotations

import ast
import asyncio
import copy
from contextlib import nullcontext
import contextvars
import hashlib
import hmac
import json
import math
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from fastapi import HTTPException  # noqa: E402
from services.editor_projects import (  # noqa: E402
    append_output_video_clip, apply_output_video_trim, create_output_video_timeline, load_editor_project, save_editor_project,
)
from services.output_access import public_output_policy, stamp_sidecar_policy  # noqa: E402
from services.queue_recovery_runtime import (atomic_write_request_manifest, load_request_manifest,
    sha256_file, recovery_unit_id, artifact_descriptor, validate_artifact_descriptor, QueueRecoveryRuntimeError)
from services.queue_recovery_adapter import processed_tool_publication_pending, QueueRecoveryAdapterError


TREE = ast.parse((ROOT / "app/launch.py").read_text(encoding="utf-8"))


def load_functions(namespace, *names):
    wanted = set(names)
    if "_editor_export_source" in wanted:
        wanted.add("_editor_export_context")
    if "_run_tool_editor_export" in wanted:
        wanted.update({"_editor_export_context", "_editor_export_publication_members",
            "_retract_editor_export_publication", "_cleanup_cancelled_editor_export_output",
            "_resume_editor_export_output", "_publish_editor_export_output",
            "_materialize_editor_export_publication", "_prepare_editor_export_completion_retry"})
    selected = []
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            cloned = copy.deepcopy(node)
            cloned.decorator_list = []
            selected.append(cloned)
    assert {node.name for node in selected} == wanted
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])),
                 "launch.py", "exec"), namespace)


class Request:
    def __init__(self, body):
        self.body = body
        self.state = types.SimpleNamespace(maestro_session_id="owner-session")

    async def stream(self):
        yield json.dumps(self.body).encode("utf-8")


class EditorExportRouteTests(unittest.TestCase):
    def test_single_alternate_uses_project_clock_and_revalidates_unused_take_without_rendering_it(self):
        from services.editor_projects import (add_output_video_take, switch_output_video_take,
            add_output_image_layer, editor_text_layers, editor_image_layers, editor_uses_sequence_clock)
        second = self.project / "second.mp4"; second.write_bytes(b"alternate-video")
        second.with_suffix(".meta.json").write_text(json.dumps({"workspace":"scene","private":False,"explicit":False}))
        logo = self.project / "logo.png"; logo.write_bytes(b"static-image")
        logo.with_suffix(".meta.json").write_text(json.dumps({"workspace":"scene","private":False,"explicit":False}))
        current = copy.deepcopy(self.timeline)
        current["canvas"].update(fps=60, width=129, height=73)
        current["assets"]["source-video"]["fps"] = 60
        clip_id = current["tracks"][0]["items"][0]["id"]
        current = add_output_video_take(current, clip_id=clip_id, output_name=second.name,
            output_revision=self.source_revision(second), media={"type":"video","duration":3,"width":128,"height":72,"fps":24,"private":False})
        alt = current["tracks"][0]["items"][0]["take_asset_ids"][-1]
        current = add_output_image_layer(current,output_name=logo.name,output_revision=self.source_revision(logo),media={"type":"image","width":16,"height":8,"private":False})
        proposed = copy.deepcopy(current)
        next(track for track in proposed["tracks"] if track["id"] == "titles-main")["items"] = [{
            "id":"first-frame","text":"One project frame","start":0,"duration":1/60,"position":"bottom"}]
        next(track for track in proposed["tracks"] if track["id"] == "images-main")["items"][0]["duration"] = 1/60
        current = apply_output_video_trim(current,proposed)
        current = switch_output_video_take(current,clip_id=clip_id,asset_id=alt)
        self.assertTrue(editor_uses_sequence_clock(current))
        self.assertEqual(editor_text_layers(current,require_fit=True)[0]["duration"],1/60)
        self.assertEqual(editor_image_layers(current,require_fit=True)[0]["duration"],1/60)
        self.timeline = save_editor_project(str(self.outputs),"scene",current,expected_revision=1)
        self.submit()
        job = self.registered[0][0]; params = job["params"]
        self.assertEqual(params["editor_source_fps"],60)
        self.assertEqual(params["editor_canvas"], {**self.timeline["canvas"],"width":130,"height":74})
        self.assertEqual((self.timeline["canvas"]["width"],self.timeline["canvas"]["height"]),(129,73))
        self.assertEqual([item["path"] for item in params["editor_sources"]],[str(second)])
        self.assertEqual(params["editor_source_path"],[str(second)])
        self.assertFalse(params["private_output"])
        self.ns.update(_existing_workspace_dir=lambda _workspace:str(self.project),
            load_media_sidecars=lambda directory,names:{name:json.loads((Path(directory)/name).with_suffix(".meta.json").read_text()) for name in names})
        load_functions(self.ns,"_editor_export_source")
        self.assertEqual(self.ns["_editor_export_source"](job)[0],str(second))
        self.source.write_bytes(b"changed inactive source")
        with self.assertRaises(HTTPException) as changed: self.submit()
        self.assertEqual(changed.exception.status_code,409)
        self.assertEqual(len(self.registered),1)

    def test_fractional_repeated_clip_registration_and_layers_follow_encoded_clock(self):
        from services.editor_projects import add_output_audio_layer, add_output_image_layer, editor_audio_layer, editor_image_layers, editor_text_layers
        current = append_output_video_clip(self.timeline, output_name=self.source.name,
            output_revision=self.source_revision(), media={"type": "video", "duration": 3,
                "width": 128, "height": 72, "fps": 24, "has_audio": True, "private": True})
        proposed = copy.deepcopy(current)
        for item in proposed["tracks"][0]["items"]:
            item.update(source_in=0, duration=2 / 3)
        current = apply_output_video_trim(current, proposed)
        current["canvas"]["fps"] = 3.75
        current = add_output_audio_layer(current, output_name="sound.wav", output_revision="audio",
            media={"type": "audio", "duration": 5, "has_audio": True})
        current = add_output_image_layer(current, output_name="logo.png", output_revision="image",
            media={"type": "image", "width": 16, "height": 8})
        self.assertEqual(editor_audio_layer(current, require_fit=True)["duration"], 1.6)
        self.assertEqual(editor_image_layers(current, require_fit=True)[0]["duration"], 1.6)
        proposed = copy.deepcopy(current)
        next(track for track in proposed["tracks"] if track["id"] == "titles-main")["items"] = [{
            "id": "last-frame", "text": "Final frame", "start": 0, "duration": 1.6, "position": "bottom",
        }]
        current = apply_output_video_trim(current, proposed)
        self.assertEqual(editor_text_layers(current, require_fit=True)[0]["duration"], 1.6)
        # Register the same saved sequence without fixture audio/still files.
        current["tracks"] = [track for track in current["tracks"] if track["id"] not in {"audio-main", "images-main"}]
        current["assets"] = {key: asset for key, asset in current["assets"].items() if asset["type"] == "video"}
        self.timeline = save_editor_project(str(self.outputs), "scene", current,
            expected_revision=self.timeline["revision"])
        self.submit()
        job = self.registered[0][0]
        self.assertEqual(job["params"]["editor_duration"], 1.6)
        self.assertEqual(job["params"]["editor_text_layers"][0]["duration"], 1.6)
        self.assertEqual(len(job["params"]["editor_sources"]), 2)
        self.assertEqual(len(self.timeline["assets"]), 1)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.outputs = self.root / "outputs"
        self.project = self.outputs / "scene"
        self.project.mkdir(parents=True)
        self.source = self.project / "source.mp4"
        self.source.write_bytes(b"original-video")
        self.source.with_suffix(".meta.json").write_text(json.dumps({
            "workspace": "scene", "private": True, "explicit": True,
            "params": {"prompt": "A quiet stage", "model_type": "source-model",
                       "multi_clip_info": {"group_id": "source-group"}},
        }))
        self.permissions = []
        self.registered = []
        self.jobs = {}
        self.timeline = create_output_video_timeline(
            workspace="scene", output_name="source.mp4",
            output_revision=self.source_revision(),
            media={"type": "video", "duration": 3.0, "width": 128,
                   "height": 72, "fps": 24, "has_audio": True, "private": True},
        )
        self.timeline = save_editor_project(
            str(self.outputs), "scene", self.timeline, expected_revision=0,
        )
        self.ns = {
            "Request": object, "HTTPException": HTTPException,
            "json": json, "os": os, "hmac": hmac, "math": math, "hashlib": hashlib,
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError, "QueueRecoveryAdapterError": QueueRecoveryAdapterError,
            "load_request_manifest": load_request_manifest, "_recovery_sha256_file": sha256_file,
            "recovery_unit_id": recovery_unit_id, "_recovery_artifact_descriptor": artifact_descriptor,
            "validate_artifact_descriptor": validate_artifact_descriptor,
            "processed_tool_publication_pending": processed_tool_publication_pending,
            "_queue_recovery_checkpoint": lambda job, **updates: job.update(**updates) or True,
            "_queue_recovery_existing_project_identity": lambda _path: "editor-project-instance",
            "_RECOVERABLE_INPUT_KEYS": {"editor_source_path", "editor_audio_path", "editor_image_path"},
            "_app_dir": str(self.root),
            "time": time, "uuid": uuid, "copy": copy, "subprocess": subprocess,
            "wgp": types.SimpleNamespace(server_config={"save_path": str(self.outputs)}),
            "_request_remote": contextvars.ContextVar("remote", default=True),
            "_reserve_workspace_operations": lambda *_args: nullcontext(),
            "_output_lineage_mutation_guard": lambda *_args: nullcontext(),
            "_require_project_access": self.authorize,
            "_require_authorized_output": self.output,
            "_output_share_revision": lambda path, *_args: self.source_revision(Path(path)),
            "_output_revision": lambda *_args: "gallery-current",
            "public_output_policy": public_output_policy,
            "_jobs": self.jobs,
            "_new_generation_job_id": lambda: "a" * 32,
            "_queue_recovery_register_and_publish": self.register,
            "_run_tool_editor_export": object(),
        }
        load_functions(self.ns, "_editor_request_body", "_editor_save_root",
                       "_editor_require_current_source", "export_output_editor_project", "append_output_editor_clip", "add_output_editor_audio", "add_output_editor_image")
        load_functions(self.ns, "_queue_recovery_file_values", "_queue_recovery_input_descriptors", "_queue_recovery_manifest_validator")

    def source_revision(self, path=None):
        path = path or self.source
        media = path.read_bytes()
        sidecar_path = path.with_suffix(".meta.json")
        sidecar = sidecar_path.read_bytes() if sidecar_path.exists() else b""
        return "sha256:" + hashlib.sha256(media + sidecar).hexdigest()

    def authorize(self, _request, project, *, existing_only=False, permission=None):
        self.permissions.append((project, existing_only, permission))
        if project != "scene":
            raise HTTPException(status_code=403, detail="Project access denied")
        return str(self.project)

    def output(self, request, project, name):
        self.authorize(request, project, existing_only=True, permission="project.mutate")
        path = self.project / name
        if name not in {"source.mp4", "second.mp4", "sound.wav", "logo.png", "second.png"} or not path.exists():
            raise HTTPException(status_code=404, detail="Output not found")
        sidecar_path = path.with_suffix(".meta.json")
        sidecar = json.loads(sidecar_path.read_text()) if sidecar_path.exists() else {}
        return str(self.project), str(path), sidecar

    def register(self, job, **options):
        job.update(_recovery_owner_digest="editor-owner", _recovery_project_digest="editor-project-instance",
                   access_policy={key: bool(job["params"].get(key + "_output")) for key in ("private", "explicit")})
        job.update(job["access_policy"])
        job["_recovery_manifest_pointer"] = atomic_write_request_manifest(self.project,
            job_id=job["id"], params=job["params"],
            inputs=self.ns["_queue_recovery_input_descriptors"](job, "editor-owner"))
        self.registered.append((job, options))
        self.jobs[job["id"]] = job

    def submit(self, *, expected=None, project="scene"):
        return asyncio.run(self.ns["export_output_editor_project"](
            project, self.timeline["id"], Request({
                "expected_revision": self.timeline["revision"] if expected is None else expected,
            }),
        ))

    def second_source(self):
        path = self.project / "second.mp4"
        path.write_bytes(b"second-video")
        path.with_suffix(".meta.json").write_text(json.dumps({"workspace": "scene", "private": True, "explicit": True}))
        return path

    def sequence(self):
        second = self.second_source()
        self.timeline = save_editor_project(str(self.outputs), "scene", append_output_video_clip(
            self.timeline, output_name=second.name, output_revision=self.source_revision(second),
            media={"type": "video", "duration": 2.0, "width": 72, "height": 128, "fps": 30, "has_audio": False, "private": True},
        ), expected_revision=self.timeline["revision"])
        return second

    def audio_source(self):
        path = self.project / "sound.wav"
        path.write_bytes(b"audio-source")
        path.with_suffix(".meta.json").write_text(json.dumps({"workspace": "scene", "private": True, "explicit": True}))
        return path

    def import_audio(self, body=None):
        return asyncio.run(self.ns["add_output_editor_audio"]("scene", self.timeline["id"], Request(body or {
            "expected_revision": self.timeline["revision"], "output_name": "sound.wav", "output_revision": "gallery-current"})))

    def test_audio_import_cas_privacy_probe_race_and_forged_metadata(self):
        path = self.audio_source()
        media = {"type": "audio", "duration": 5, "has_audio": True}
        with mock.patch("services.editor_projects.probe_media", return_value=media):
            saved = self.import_audio()["project"]
        asset = saved["assets"]["source-audio"]
        self.assertEqual(asset["output_revision"], self.source_revision(path)); self.assertTrue(asset["private"])
        self.assertNotIn("path", asset); self.assertEqual(saved["revision"], 2)
        with self.assertRaises(HTTPException) as stale: self.import_audio()
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as forged:
            self.import_audio({"expected_revision": 2, "output_name": "sound.wav", "output_revision": "gallery-current", "path": "/foreign.wav"})
        self.assertEqual(forged.exception.status_code, 400)
        self.timeline = saved
        with self.assertRaises(HTTPException) as duplicate: self.import_audio()
        self.assertEqual(duplicate.exception.status_code, 422)

    def test_audio_replaced_or_privacy_changed_during_import_is_refused(self):
        path = self.audio_source()
        def mutate(_path):
            path.with_suffix(".meta.json").write_text(json.dumps({"private": False}))
            return {"type": "audio", "duration": 5, "has_audio": True}
        with mock.patch("services.editor_projects.probe_media", side_effect=mutate), self.assertRaises(HTTPException) as changed:
            self.import_audio()
        self.assertEqual(changed.exception.status_code, 409)
        self.assertEqual(load_editor_project(str(self.outputs), "scene", self.timeline["id"])["revision"], 1)

    def with_audio(self):
        from services.editor_projects import add_output_audio_layer
        path = self.audio_source()
        self.timeline = save_editor_project(str(self.outputs), "scene", add_output_audio_layer(
            self.timeline, output_name=path.name, output_revision=self.source_revision(path),
            media={"type": "audio", "duration": 2, "has_audio": True, "private": True}), expected_revision=self.timeline["revision"])
        return path

    def test_audio_sealed_recovery_input_and_private_finality_rechecks(self):
        path = self.with_audio()
        audio = next(track for track in self.timeline["tracks"] if track["id"] == "audio-main")["items"][0]
        audio.update(fade_in=0.5, fade_out=0.75)
        self.timeline = save_editor_project(str(self.outputs), "scene", self.timeline, expected_revision=self.timeline["revision"])
        job = self.worker_namespace()
        params = job["params"]
        self.assertEqual(params["editor_audio_path"], str(path)); self.assertTrue(params["private_output"])
        from services.queue_recovery_runtime import QueueRecoveryRuntimeError, sha256_file
        self.ns.update({"_app_dir": str(self.root), "_RECOVERABLE_INPUT_KEYS": {"editor_source_path", "editor_audio_path"}, "_recovery_sha256_file": sha256_file, "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError})
        load_functions(self.ns, "_queue_recovery_file_values", "_queue_recovery_input_descriptors")
        descriptors = self.ns["_queue_recovery_input_descriptors"](job, "owner")
        self.assertEqual([d["field"] for d in descriptors], ["editor_audio_path:0", "editor_source_path:0"])
        self.assertTrue(all(d["scope"] == "project" for d in descriptors))
        def render(_source, destination, **options):
            self.assertEqual(options["audio_layer"], params["editor_audio_layer"])
            Path(destination).write_bytes(b"rendered")
        probe = {"type": "video", "duration": 3.0, "size": 8, "has_audio": True}
        with mock.patch("services.editor_export.render_single_source_cut", side_effect=render), mock.patch("services.editor_projects.probe_media", return_value=probe):
            self.assertTrue(self.ns["_run_tool_editor_export"](job["id"]))
        meta = json.loads((self.project / job["output_files"][0]).with_suffix(".meta.json").read_text())
        self.assertIsNone(meta["params"]); self.assertTrue(meta["private"])
        self.assertEqual(meta["transform"]["audio_layer"]["revision"], self.source_revision(path))
        self.assertEqual(meta["transform"]["audio_layer"]["fade_in"], 0.5)
        self.assertEqual(meta["transform"]["audio_layer"]["fade_out"], 0.75)
        job["id"] = "b" * 32; self.jobs[job["id"]] = job
        job["status"] = "queued"; job["output_files"] = []
        def replace(_source, destination, **_options):
            Path(destination).write_bytes(b"rendered"); path.write_bytes(b"replacement")
        with mock.patch("services.editor_export.render_single_source_cut", side_effect=replace), mock.patch("services.editor_projects.probe_media", return_value=probe):
            self.assertFalse(self.ns["_run_tool_editor_export"](job["id"]))
        self.assertEqual(job["status"], "failed")
        self.assertFalse(job["output_files"])
        self.assertFalse((self.project / ("editor_cut_" + job["id"] + ".mp4")).exists())
        # Sidecarless media remains live-only; it does not authorize restart recovery.
        path.with_suffix(".meta.json").unlink()
        self.assertEqual(self.ns["_queue_recovery_input_descriptors"](job, "owner")[0]["scope"], "derived")

    def image_source(self):
        path = self.project / "logo.png"
        path.write_bytes(b"image-source")
        path.with_suffix(".meta.json").write_text(json.dumps({"workspace": "scene", "private": True, "explicit": True}))
        return path

    def import_image(self, body=None):
        return asyncio.run(self.ns["add_output_editor_image"]("scene", self.timeline["id"], Request(body or {
            "expected_revision": self.timeline["revision"], "output_name": "logo.png", "output_revision": "gallery-current"})))

    def test_image_import_cas_privacy_probe_race_and_forged_metadata(self):
        path = self.image_source()
        media = {"type": "image", "width": 32, "height": 16, "has_audio": False}
        with mock.patch("services.editor_projects.inspect_editor_still", return_value=media):
            saved = self.import_image()["project"]
        asset = saved["assets"]["source-image"]
        self.assertEqual(asset["output_revision"], self.source_revision(path)); self.assertTrue(asset["private"])
        self.assertNotIn("path", asset); self.assertEqual(saved["revision"], 2)
        with self.assertRaises(HTTPException) as stale: self.import_image()
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as forged:
            self.import_image({"expected_revision": 2, "output_name": "logo.png", "output_revision": "gallery-current", "path": "/foreign.wav"})
        self.assertEqual(forged.exception.status_code, 400)
        self.timeline = saved
        with mock.patch("services.editor_projects.inspect_editor_still", return_value=media):
            duplicate = self.import_image()["project"]
        self.assertEqual(len(next(track for track in duplicate["tracks"] if track["id"] == "images-main")["items"]), 2)

    def test_image_replaced_or_privacy_changed_during_import_is_refused(self):
        path = self.image_source()
        def mutate(_path):
            path.with_suffix(".meta.json").write_text(json.dumps({"private": False}))
            return {"type": "image", "width": 32, "height": 16, "has_audio": False}
        with mock.patch("services.editor_projects.inspect_editor_still", side_effect=mutate), self.assertRaises(HTTPException) as changed:
            self.import_image()
        self.assertEqual(changed.exception.status_code, 409)
        self.assertEqual(load_editor_project(str(self.outputs), "scene", self.timeline["id"])["revision"], 1)

    def with_image(self):
        from services.editor_projects import add_output_image_layer
        path = self.image_source()
        self.timeline = save_editor_project(str(self.outputs), "scene", add_output_image_layer(
            self.timeline, output_name=path.name, output_revision=self.source_revision(path),
            media={"type": "image", "width": 32, "height": 16, "private": True}), expected_revision=self.timeline["revision"])
        return path

    def test_multiple_images_all_sources_recovery_privacy_order_and_finality(self):
        from services.editor_projects import add_output_image_layer
        first = self.with_image()
        first.with_suffix(".meta.json").write_text(json.dumps({"workspace": "scene", "private": False, "explicit": False}))
        self.source.with_suffix(".meta.json").write_text(json.dumps({"workspace": "scene", "private": False, "explicit": False}))
        self.timeline["assets"]["source-image"].update(private=False, output_revision=self.source_revision(first))
        self.timeline["assets"]["source-video"].update(private=False, output_revision=self.source_revision())
        second = self.project / "second.png"
        second.write_bytes(b"second still")
        second.with_suffix(".meta.json").write_text(json.dumps({"workspace": "scene", "private": True, "explicit": True}))
        self.timeline = save_editor_project(str(self.outputs), "scene", add_output_image_layer(
            self.timeline, output_name=second.name, output_revision=self.source_revision(second),
            media={"type": "image", "width": 32, "height": 16}), expected_revision=self.timeline["revision"])
        job = self.worker_namespace()
        params = job["params"]
        self.assertEqual(params["editor_image_path"], [str(first), str(second)])
        self.assertTrue(params["explicit_output"])
        self.assertTrue(params["private_output"])
        from services.queue_recovery_runtime import QueueRecoveryRuntimeError, sha256_file
        self.ns.update({"_app_dir": str(self.root), "_RECOVERABLE_INPUT_KEYS": next({item.value for item in node.value.args[0].elts if isinstance(item, ast.Constant)} for node in TREE.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_RECOVERABLE_INPUT_KEYS" for t in node.targets)), "_recovery_sha256_file": sha256_file, "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError})
        load_functions(self.ns, "_queue_recovery_file_values", "_queue_recovery_input_descriptors")
        descriptors = self.ns["_queue_recovery_input_descriptors"](job, "owner")
        self.assertEqual([d["field"] for d in descriptors], ["editor_image_path:0", "editor_image_path:1", "editor_source_path:0"])
        probe = {"type": "video", "duration": 3.0, "size": 8, "has_audio": True, "width": 128, "height": 72}
        def render(_source, destination, **options):
            self.assertEqual(options["image_layer"], params["editor_image_layer"])
            Path(destination).write_bytes(b"rendered")
        with mock.patch("services.editor_export.render_single_source_cut", side_effect=render), mock.patch("services.editor_projects.probe_media", return_value=probe):
            self.assertTrue(self.ns["_run_tool_editor_export"](job["id"]))
        meta = json.loads((self.project / job["output_files"][0]).with_suffix(".meta.json").read_text())
        self.assertEqual([item["name"] for item in meta["transform"]["image_layers"]], [first.name, second.name])
        self.assertNotIn("path", meta["transform"]["image_layers"][1])
        job["id"] = "b" * 32; self.jobs[job["id"]] = job
        job["status"] = "queued"; job["output_files"] = []
        job["recovery_cursor"] = {}
        self.register(job)
        def replace(_source, destination, **_options):
            Path(destination).write_bytes(b"rendered"); second.write_bytes(b"replaced")
        with mock.patch("services.editor_export.render_single_source_cut", side_effect=replace), mock.patch("services.editor_projects.probe_media", return_value=probe):
            self.assertFalse(self.ns["_run_tool_editor_export"](job["id"]))
        self.assertEqual(job["output_files"], [])
        self.assertFalse((self.project / f"editor_cut_{job['id']}.mp4").exists())

    def test_legacy_single_image_job_source_binding_is_retained(self):
        path = self.with_image()
        job = self.worker_namespace()
        job["params"]["editor_image_layer"] = job["params"]["editor_image_layer"][0]
        job["params"]["editor_image_path"] = str(path)
        self.register(job)
        self.assertEqual(self.ns["_editor_export_source"](job)[0], str(self.source))
        job["params"]["editor_image_path"] = str(self.source)
        with self.assertRaises(ValueError): self.ns["_editor_export_source"](job)

    def test_image_sealed_recovery_input_and_private_finality_rechecks(self):
        self.source.with_suffix(".meta.json").write_text(json.dumps({"workspace": "scene", "private": False, "explicit": False}))
        self.timeline["assets"]["source-video"].update(private=False, output_revision=self.source_revision())
        path = self.with_image()
        next(track for track in self.timeline["tracks"] if track["id"] == "images-main")["items"][0]["opacity"] = 0
        self.timeline = save_editor_project(str(self.outputs), "scene", self.timeline, expected_revision=self.timeline["revision"])
        job = self.worker_namespace()
        params = job["params"]
        self.assertEqual(params["editor_image_path"], [str(path)]); self.assertTrue(params["private_output"])
        from services.queue_recovery_runtime import QueueRecoveryRuntimeError, sha256_file
        self.ns.update({"_app_dir": str(self.root), "_RECOVERABLE_INPUT_KEYS": next({item.value for item in node.value.args[0].elts if isinstance(item, ast.Constant)} for node in TREE.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_RECOVERABLE_INPUT_KEYS" for t in node.targets)), "_recovery_sha256_file": sha256_file, "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError})
        load_functions(self.ns, "_queue_recovery_file_values", "_queue_recovery_input_descriptors")
        descriptors = self.ns["_queue_recovery_input_descriptors"](job, "owner")
        self.assertEqual([d["field"] for d in descriptors], ["editor_image_path:0", "editor_source_path:0"])
        self.assertTrue(all(d["scope"] == "project" for d in descriptors))
        def render(_source, destination, **options):
            self.assertEqual(options["image_layer"], params["editor_image_layer"])
            Path(destination).write_bytes(b"rendered")
        probe = {"type": "video", "duration": 3.0, "size": 8, "has_audio": True, "width": 128, "height": 72}
        with mock.patch("services.editor_export.render_single_source_cut", side_effect=render), mock.patch("services.editor_projects.probe_media", return_value=probe):
            self.assertTrue(self.ns["_run_tool_editor_export"](job["id"]))
        meta = json.loads((self.project / job["output_files"][0]).with_suffix(".meta.json").read_text())
        self.assertIsNone(meta["params"]); self.assertTrue(meta["private"])
        self.assertEqual(meta["transform"]["image_layer"]["revision"], self.source_revision(path))
        job["id"] = "b" * 32; self.jobs[job["id"]] = job
        job["status"] = "queued"; job["output_files"] = []
        def replace(_source, destination, **_options):
            Path(destination).write_bytes(b"rendered"); path.write_bytes(b"replacement")
        with mock.patch("services.editor_export.render_single_source_cut", side_effect=replace), mock.patch("services.editor_projects.probe_media", return_value=probe):
            self.assertFalse(self.ns["_run_tool_editor_export"](job["id"]))
        self.assertEqual(job["status"], "failed")
        self.assertFalse(job["output_files"])
        self.assertFalse((self.project / ("editor_cut_" + job["id"] + ".mp4")).exists())
        # Sidecarless media remains live-only; it does not authorize restart recovery.
        path.with_suffix(".meta.json").unlink()
        self.assertEqual(self.ns["_queue_recovery_input_descriptors"](job, "owner")[0]["scope"], "derived")

    def test_append_uses_gallery_revision_server_media_and_cas(self):
        second = self.second_source()
        request = {"expected_revision": 1, "output_name": second.name, "output_revision": "gallery-current"}
        def append(body):
            return asyncio.run(self.ns["append_output_editor_clip"]("scene", self.timeline["id"], Request(body)))
        with mock.patch("services.editor_projects.probe_media", return_value={"type": "video", "duration": 2, "width": 72, "height": 128, "fps": 30, "has_audio": False}):
            saved = append(request)["project"]
        self.assertEqual(saved["revision"], 2)
        asset = next(asset for asset in saved["assets"].values() if asset["output_id"] == second.name)
        self.assertEqual(asset["output_revision"], self.source_revision(second))
        self.assertTrue(asset["private"])
        self.assertNotIn("path", asset)
        with self.assertRaises(HTTPException) as stale:
            append(request)
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as forged:
            append(dict(request, expected_revision=2, path="/foreign/source.mp4"))
        self.assertEqual(forged.exception.status_code, 400)

    def test_shared_source_append_uses_stat_token_reuses_asset_and_preserves_cas(self):
        load_functions(self.ns, "_output_revision")
        listing = self.ns["_output_revision"](str(self.source), str(self.project), self.source.name)
        self.assertNotEqual(listing, self.source_revision())
        media = {"type": "video", "duration": 3, "width": 128, "height": 72, "fps": 24, "has_audio": True}
        body = {"expected_revision": 1, "output_name": self.source.name, "output_revision": listing}
        def append(request):
            return asyncio.run(self.ns["append_output_editor_clip"]("scene", self.timeline["id"], Request(request)))["project"]
        with mock.patch("services.editor_projects.probe_media", return_value=media):
            saved = append(body)
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(len(saved["assets"]), 1)
        items = saved["tracks"][0]["items"]
        self.assertEqual([item["asset_id"] for item in items], ["source-video", "source-video"])
        self.assertNotEqual(items[0]["id"], items[1]["id"])
        self.assertIsNot(items[0]["take_states"], items[1]["take_states"])
        self.assertEqual(saved["assets"]["source-video"], self.timeline["assets"]["source-video"])
        with self.assertRaises(HTTPException) as stale:
            append(body)
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as wrong_pin:
            append({**body, "expected_revision": 2, "output_revision": self.source_revision()})
        self.assertEqual(wrong_pin.exception.status_code, 409)
        for field, value in (("duration", 4), ("width", 130), ("fps", 30), ("has_audio", False)):
            with self.subTest(field=field), mock.patch("services.editor_projects.probe_media", return_value={**media, field: value}) as probe:
                with self.assertRaises(HTTPException) as contradictory:
                    append({**body, "expected_revision": 2})
                self.assertEqual(contradictory.exception.status_code, 422)
                probe.assert_called_once()
        self.assertEqual(load_editor_project(str(self.outputs), "scene", self.timeline["id"]), saved)

    def test_append_rejects_privacy_media_and_probe_races_without_saving(self):
        second = self.second_source()
        load_functions(self.ns, "_output_revision")
        media = {"type": "video", "duration": 2, "width": 72, "height": 128, "fps": 30, "has_audio": False}
        sidecar = second.with_suffix(".meta.json")
        source_sidecar = self.source.with_suffix(".meta.json")
        original = (second.read_bytes(), sidecar.read_bytes(), source_sidecar.read_bytes())
        for mode, status in (("media", 409), ("privacy", 409), ("existing-privacy", 409), ("invalid-media", 422)):
            with self.subTest(mode=mode):
                second.write_bytes(original[0]); sidecar.write_bytes(original[1]); source_sidecar.write_bytes(original[2])
                listing = self.ns["_output_revision"](str(second), str(self.project), second.name)
                body = {"expected_revision": 1, "output_name": second.name, "output_revision": listing}
                def mutate(_path):
                    if mode == "media": second.write_bytes(b"replaced-video")
                    elif mode == "privacy": sidecar.write_text(json.dumps({"workspace": "scene", "private": False}))
                    elif mode == "existing-privacy": source_sidecar.write_text(json.dumps({"workspace": "scene", "private": False}))
                    return {**media, "type": "audio"} if mode == "invalid-media" else media
                with mock.patch("services.editor_projects.probe_media", side_effect=mutate) as probe:
                    with self.assertRaises(HTTPException) as rejected:
                        asyncio.run(self.ns["append_output_editor_clip"]("scene", self.timeline["id"], Request(body)))
                self.assertEqual(rejected.exception.status_code, status)
                probe.assert_called_once_with(str(second))
                self.assertEqual(load_editor_project(str(self.outputs), "scene", self.timeline["id"]), self.timeline)
        with self.assertRaises(HTTPException) as foreign:
            asyncio.run(self.ns["append_output_editor_clip"]("foreign", self.timeline["id"], Request(body)))
        self.assertEqual(foreign.exception.status_code, 403)

    def test_shared_source_export_restores_distinct_indices_exact_ranges_and_sealed_authority(self):
        from services.queue_recovery import QueueRecoveryJournal
        from services.queue_recovery_adapter import QueueRecoveryCoordinator
        from services.queue_recovery_runtime import (
            QueueRecoveryRuntimeError, load_request_manifest, sha256_file,
            validate_manifest_inputs, write_sealed_request_manifest,
        )
        from services.output_access import output_policy_from_request
        self.sequence()
        current = append_output_video_clip(self.timeline, output_name=self.source.name,
            output_revision=self.source_revision(), media={"type": "video", "duration": 3, "width": 128,
                "height": 72, "fps": 24, "has_audio": True, "private": True})
        proposed = copy.deepcopy(current)
        proposed["tracks"][0]["items"][0].update(source_in=0.25, duration=0.55)
        proposed["tracks"][0]["items"][1].update(source_in=0.5, duration=0.75)
        proposed["tracks"][0]["items"][2].update(source_in=1.25, duration=0.55)
        proposed["tracks"][0]["items"].reverse()
        self.timeline = save_editor_project(str(self.outputs), "scene", apply_output_video_trim(current, proposed), expected_revision=self.timeline["revision"])
        job = self.worker_namespace()
        # The route fixture skips registration; apply its real publication policy before journaling.
        registry = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "_JobRegistry")
        prepare = copy.deepcopy(next(node for node in registry.body if isinstance(node, ast.FunctionDef) and node.name == "prepare"))
        policy_class = ast.ClassDef(name="EditorPolicyRegistry", bases=[ast.Name(id="dict", ctx=ast.Load())],
            keywords=[], body=[prepare], decorator_list=[])
        self.ns["output_policy_from_request"] = output_policy_from_request
        exec(compile(ast.fix_missing_locations(ast.Module(body=[policy_class], type_ignores=[])), "launch.py", "exec"), self.ns)
        self.ns["EditorPolicyRegistry"]().prepare(job)
        original_params = copy.deepcopy(job["params"])
        paths = [str(self.source), str(self.project / "second.mp4"), str(self.source)]
        ranges = [(1.25, 0.55), (0.5, 0.75), (0.25, 0.55)]
        self.assertEqual(original_params["editor_source_path"], paths)
        self.assertEqual([(item["source_in"], item["duration"]) for item in original_params["editor_sources"]], ranges)
        self.ns.update({"_app_dir": str(self.root), "_RECOVERABLE_INPUT_KEYS": {"editor_source_path"},
            "_recovery_sha256_file": sha256_file, "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            "load_request_manifest": load_request_manifest, "validate_manifest_inputs": validate_manifest_inputs,
            # This Editor job has no model or H3 completed-unit graph.
            "_queue_recovery_reconcile_cursor": lambda *_args, **_kwargs: None,
            "_job_uses_registered_h3": lambda _job: False,
        })
        load_functions(self.ns, "_queue_recovery_file_values", "_queue_recovery_input_descriptors",
            "_queue_recovery_manifest_validator", "_queue_recovery_materialize_job", "_h3_cow_manual_source_supported",
            "_queue_recovery_worker", "_require_h3_offload_plan_parity")
        owner, project = "owner:v1:" + "a" * 64, "project:v1:" + "b" * 64
        descriptors = self.ns["_queue_recovery_input_descriptors"](job, owner)
        self.assertEqual([item["field"] for item in descriptors], [f"editor_source_path:{index}" for index in range(3)])
        self.assertEqual([item["scope"] for item in descriptors], ["project"] * 3)
        self.assertEqual({key: value for key, value in descriptors[0].items() if key != "field"},
                         {key: value for key, value in descriptors[2].items() if key != "field"})
        manifest = write_sealed_request_manifest(self.project, job_id=job["id"], params=original_params, inputs=descriptors)
        journal = self.root / "private-queue.json"
        QueueRecoveryCoordinator(QueueRecoveryJournal(journal)).register_job(
            job, owner_digest=owner, project_digest=project, request_manifest=manifest)
        job.update(_recovery_owner_digest=owner, _recovery_project_digest=project, _recovery_manifest_pointer=manifest)
        self.ns["_queue_recovery_existing_project_identity"] = lambda _path: project
        snapshot = QueueRecoveryCoordinator(QueueRecoveryJournal(journal)).restore().jobs[job["id"]]
        self.assertNotIn("params", snapshot)
        self.assertNotIn(str(self.project), json.dumps(snapshot))
        loaded = load_request_manifest(self.project, snapshot["request_manifest"], expected_job_id=job["id"])
        self.assertEqual(loaded["params"], original_params)
        self.assertEqual(loaded["inputs"], descriptors)
        recovered, may_start = self.ns["_queue_recovery_materialize_job"](snapshot, {"scene": (str(self.project), project)})
        self.assertFalse(may_start)
        self.assertEqual(recovered["recovery_state"], "blocked_remote_reauth")
        self.assertIsNone(recovered["session_id"])
        self.assertEqual(recovered["params"], original_params)
        self.assertEqual(recovered["access_policy"], {"private": True, "explicit": True})
        def render(clips, destination, **_options):
            self.assertEqual([item["path"] for item in clips], paths)
            self.assertEqual([(item["source_in"], item["duration"]) for item in clips], ranges)
            Path(destination).write_bytes(b"rendered")
        with mock.patch("services.editor_export.render_video_sequence", side_effect=render), mock.patch(
            "services.editor_projects.probe_media", return_value={"type": "video", "duration": 44 / 24,
                "size": 8, "has_audio": True, "width": 128, "height": 72, "fps": 24}):
            self.assertTrue(self.ns["_run_tool_editor_export"](job["id"]))
        sidecar = json.loads((self.project / job["output_files"][0]).with_suffix(".meta.json").read_text())
        sources = sidecar["transform"]["sources"]
        self.assertEqual([(item["source_in"], item["duration"]) for item in sources], ranges)
        self.assertEqual(sources[0]["revision"], sources[2]["revision"])
        self.assertTrue(sidecar["private"])
        self.source.with_suffix(".meta.json").write_text(json.dumps({"workspace": "scene", "private": False}))
        blocked, may_start = self.ns["_queue_recovery_materialize_job"](snapshot, {"scene": (str(self.project), project)})
        self.assertFalse(may_start)
        self.assertEqual(blocked["_recovery_reason_code"], "input_missing_or_changed")
        self.assertNotIn("params", blocked)

    def test_export_after_original_clip_removal_seals_only_remaining_source(self):
        second = self.sequence()
        sidecar = second.with_suffix(".meta.json")
        sidecar.write_text(json.dumps({"workspace":"scene", "private":False, "explicit":False}))
        self.timeline["assets"][self.timeline["tracks"][0]["items"][1]["asset_id"]]["output_revision"] = self.source_revision(second)
        proposed = copy.deepcopy(self.timeline); proposed["tracks"][0]["items"].pop(0)
        self.timeline = save_editor_project(str(self.outputs), "scene",
            apply_output_video_trim(self.timeline, proposed), expected_revision=self.timeline["revision"])
        self.submit()
        params = self.registered[-1][0]["params"]
        self.assertEqual(params["editor_source_name"], second.name)
        self.assertEqual(params["editor_source_path"], str(second))
        self.assertNotIn("editor_sources", params)
        self.assertEqual(params["editor_canvas"], self.timeline["canvas"])
        self.assertEqual(params["editor_source_fps"], 30)
        self.assertFalse(params["private_output"])
        self.assertFalse(params["explicit_output"])
        self.assertTrue(self.source.exists())

    def test_sequence_checks_second_source_inherits_privacy_and_seals_recovery(self):
        # A public first source must still yield a private/explicit sequence.
        self.source.with_suffix(".meta.json").write_text(json.dumps({"workspace": "scene", "private": False, "explicit": False}))
        timeline = load_editor_project(str(self.outputs), "scene", self.timeline["id"])
        timeline["assets"]["source-video"]["output_revision"] = self.source_revision()
        self.timeline = save_editor_project(str(self.outputs), "scene", timeline, expected_revision=1)
        second = self.sequence()
        self.submit()
        params = self.registered[0][0]["params"]
        self.assertEqual(params["editor_source_path"], [str(self.source), str(second)])
        self.assertTrue(params["private_output"])
        self.assertTrue(params["explicit_output"])
        self.assertEqual(params["editor_duration"], 5)
        from services.queue_recovery_runtime import QueueRecoveryRuntimeError, sha256_file
        self.ns.update({"_app_dir": str(self.root), "_RECOVERABLE_INPUT_KEYS": {"editor_source_path"}, "_recovery_sha256_file": sha256_file, "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError})
        load_functions(self.ns, "_queue_recovery_file_values", "_queue_recovery_input_descriptors")
        descriptors = self.ns["_queue_recovery_input_descriptors"](self.registered[0][0], "owner")
        self.assertEqual([item["field"] for item in descriptors], ["editor_source_path:0", "editor_source_path:1"])
        self.assertEqual([item["scope"] for item in descriptors], ["project", "project"])
        second.with_suffix(".meta.json").unlink()
        descriptors = self.ns["_queue_recovery_input_descriptors"](self.registered[0][0], "owner")
        self.assertEqual(descriptors[1]["scope"], "derived")
        self.jobs.clear()
        self.registered.clear()
        with self.assertRaises(HTTPException) as stale:
            self.submit()
        self.assertEqual(stale.exception.status_code, 409)
        self.assertFalse(self.registered)

    def test_submission_seals_source_cut_policy_and_deduplicates_active_job(self):
        result = self.submit()
        self.assertEqual(result, {"job_id": "a" * 32, "status": "queued"})
        self.assertEqual(self.submit(), result)
        self.assertEqual(len(self.registered), 1)
        job, options = self.registered[0]
        self.assertEqual(options["recovery_kind"], "tool_editor_export")
        self.assertIs(options["worker"], self.ns["_run_tool_editor_export"])
        self.assertEqual(job["params"]["editor_source_revision"], self.source_revision())
        self.assertEqual(job["params"]["editor_source_path"], str(self.source))
        self.assertEqual((job["params"]["editor_source_in"], job["params"]["editor_duration"]), (0.0, 3.0))
        self.assertTrue(job["params"]["private_output"])
        self.assertTrue(job["params"]["explicit_output"])
        self.assertIn(("scene", True, "project.generate"), self.permissions)
        self.assertIn(("scene", True, "project.mutate"), self.permissions)

    def test_saved_positive_trim_start_can_export_its_exact_revision(self):
        timeline = load_editor_project(str(self.outputs), "scene", self.timeline["id"])
        timeline["tracks"][0]["items"][0]["source_in"] = 0.5
        timeline["tracks"][0]["items"][0]["duration"] = 1.3
        self.timeline = save_editor_project(
            str(self.outputs), "scene", timeline, expected_revision=1,
        )
        self.assertEqual(self.submit()["status"], "queued")
        params = self.registered[0][0]["params"]
        self.assertEqual(params["editor_revision"], 2)
        self.assertEqual((params["editor_source_in"], params["editor_duration"]), (0.5, 1.3))

    def test_stale_foreign_or_changed_source_cannot_queue(self):
        with self.assertRaises(HTTPException) as stale:
            self.submit(expected=2)
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as foreign:
            self.submit(project="other")
        self.assertEqual(foreign.exception.status_code, 403)
        self.source.write_bytes(b"replacement")
        with self.assertRaises(HTTPException) as changed:
            self.submit()
        self.assertEqual(changed.exception.status_code, 409)
        self.assertFalse(self.registered)

    def test_unsupported_second_layer_is_rejected_instead_of_silently_omitted(self):
        timeline = load_editor_project(str(self.outputs), "scene", self.timeline["id"])
        timeline["tracks"][2]["items"] = [{
            "id": "title", "start": 0, "duration": 1, "text": "TITLE",
        }]
        self.timeline = save_editor_project(
            str(self.outputs), "scene", timeline, expected_revision=1,
        )
        with self.assertRaises(HTTPException) as unsupported:
            self.submit()
        self.assertEqual(unsupported.exception.status_code, 422)
        self.assertFalse(self.registered)

    def test_supported_title_is_sealed_with_single_source_and_canvas(self):
        from services.editor_projects import apply_output_video_trim
        proposed = copy.deepcopy(self.timeline)
        proposed["tracks"][2]["items"] = [{
            "id": "text", "text": "Literal [v]; adult fiction", "start": 0.5, "duration": 1, "position": "bottom",
        }]
        self.timeline = save_editor_project(str(self.outputs), "scene",
            apply_output_video_trim(self.timeline, proposed), expected_revision=1)
        self.submit()
        params = self.registered[0][0]["params"]
        self.assertEqual(params["editor_text_layers"], proposed["tracks"][2]["items"])
        self.assertEqual(params["editor_canvas"], self.timeline["canvas"])
        self.assertIsInstance(params["editor_source_path"], str)
        self.assertTrue(params["private_output"])

    def test_unsupported_clip_effect_is_rejected_instead_of_silently_omitted(self):
        timeline = load_editor_project(str(self.outputs), "scene", self.timeline["id"])
        timeline["tracks"][0]["items"][0]["fade_in"] = 0.5
        self.timeline = save_editor_project(
            str(self.outputs), "scene", timeline, expected_revision=1,
        )
        with self.assertRaises(HTTPException) as unsupported:
            self.submit()
        self.assertEqual(unsupported.exception.status_code, 422)
        self.assertFalse(self.registered)

    def test_source_disappearing_during_final_revision_check_reports_conflict(self):
        calls = 0
        def revision(*_args):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("media moved")
            return self.source_revision()
        self.ns["_output_share_revision"] = revision
        with self.assertRaises(HTTPException) as changed:
            self.submit()
        self.assertEqual(changed.exception.status_code, 409)
        self.assertFalse(self.registered)

    def test_recovery_seals_editor_source_and_blocks_legacy_restart(self):
        from services.queue_recovery_runtime import QueueRecoveryRuntimeError, sha256_file
        self.submit()
        job = self.registered[0][0]
        self.ns.update({
            "_app_dir": str(self.root),
            "_RECOVERABLE_INPUT_KEYS": {"editor_source_path"},
            "_recovery_sha256_file": sha256_file,
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
        })
        load_functions(self.ns, "_queue_recovery_file_values", "_queue_recovery_input_descriptors")
        descriptors = self.ns["_queue_recovery_input_descriptors"](job, "owner")
        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0]["field"], "editor_source_path:0")
        self.assertEqual(descriptors[0]["scope"], "project")
        job["kind"] = "tool_hflip"
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.ns["_queue_recovery_input_descriptors"](job, "owner")
        job["kind"] = "tool_editor_export"
        self.source.with_suffix(".meta.json").unlink()
        self.assertEqual(
            self.ns["_queue_recovery_input_descriptors"](job, "owner")[0]["scope"],
            "derived",
        )

    def worker_namespace(self):
        self.submit()
        job = self.registered[0][0]
        job["access_policy"] = {"private": True, "explicit": True}
        self.ns.update({
            "_gen_lock": threading.Lock(), "_active_gen_states": {},
            "generation_slot": lambda *_args: nullcontext(True),
            "try_start": lambda current, **_kw: current.update(status="running") or True,
            "finish_job": lambda current, status, **updates: current.update(status=status, **updates) or True,
            "register_abort_state": lambda *_args: True,
            "unregister_abort_state": mock.Mock(),
            "is_cancel_requested": lambda current: current.get("status") == "cancelled",
            "_existing_workspace_dir": lambda _workspace: str(self.project),
            "load_media_sidecars": lambda directory, names: {
                name: json.loads((Path(directory) / name).with_suffix(".meta.json").read_text())
                for name in names if (Path(directory) / name).with_suffix(".meta.json").exists()},
            "stamp_sidecar_policy": stamp_sidecar_policy,
        })
        load_functions(self.ns, "_editor_export_source", "_write_tool_sidecar", "_run_tool_editor_export")
        return job

    def test_sidecarless_editor_source_works_only_with_original_live_authorization(self):
        self.source.with_suffix('.meta.json').unlink()
        self.timeline['assets']['source-video']['output_revision'] = self.source_revision()
        self.timeline = save_editor_project(str(self.outputs), 'scene', self.timeline, expected_revision=1)
        job = self.worker_namespace()
        manifest = load_request_manifest(self.project, job['_recovery_manifest_pointer'], expected_job_id=job['id'])
        self.assertEqual(manifest['inputs'][0]['scope'], 'derived')
        job['access_policy'] = {key: job[key] for key in ('private', 'explicit')}
        with mock.patch('services.editor_export.render_single_source_cut', side_effect=lambda _src, dst, **_kw: Path(dst).write_bytes(b'rendered')), \
             mock.patch('services.editor_projects.probe_media', return_value={'type':'video','duration':3,'size':8,'has_audio':True}):
            self.assertTrue(self.ns['_run_tool_editor_export'](job['id']))
        job['session_id'] = None
        with self.assertRaises(QueueRecoveryRuntimeError): self.ns['_editor_export_context'](job)

    def test_worker_publishes_a_private_final_copy_with_cut_provenance(self):
        job = self.worker_namespace()
        def render(_source, destination, **options):
            self.assertEqual(options["source_in"], 0)
            Path(destination).write_bytes(b"rendered-video")
        with mock.patch("services.editor_export.render_single_source_cut", side_effect=render), \
             mock.patch("services.editor_projects.probe_media", return_value={
                 "type": "video", "duration": 3.0, "size": 14, "has_audio": True,
             }):
            self.assertTrue(self.ns["_run_tool_editor_export"](job["id"]))
        self.assertEqual(job["status"], "completed")
        media = self.project / job["output_files"][0]
        self.assertEqual(media.read_bytes(), b"rendered-video")
        sidecar = json.loads(media.with_suffix(".meta.json").read_text())
        self.assertEqual(sidecar["tool_source_revision"], self.source_revision())
        self.assertEqual(sidecar["transform"]["kind"], "editor_trim")
        self.assertEqual(sidecar["transform"]["editor_project_id"], self.timeline["id"])
        self.assertEqual(sidecar["params"]["model_type"], "source-model")
        self.assertNotIn("multi_clip_info", sidecar["params"])
        self.assertTrue(sidecar["private"])
        self.assertTrue(sidecar["explicit"])
        self.assertEqual(self.source.read_bytes(), b"original-video")

    def test_editor_publication_persistence_failure_and_concurrent_foreign_name_never_claim_output(self):
        for mode in ("persistence", "foreign"):
            with self.subTest(mode=mode):
                self.jobs.clear(); self.registered.clear()
                job = self.worker_namespace()
                output = self.project / f"editor_cut_{job['id']}.mp4"
                sidecar = output.with_suffix('.meta.json')
                from services.atomic_file_publish import publish_file_no_replace
                def publish(source, destination):
                    if mode == "foreign" and destination == str(output):
                        # Same bytes, foreign inode: hashes cannot prove publication ownership.
                        output.write_bytes(Path(source).read_bytes())
                    return publish_file_no_replace(source, destination)
                if mode == "persistence":
                    self.ns['_queue_recovery_checkpoint'] = lambda *_args, **_kwargs: False
                with mock.patch("services.editor_export.render_single_source_cut", side_effect=lambda _source, dest, **_kw: Path(dest).write_bytes(b"rendered-video")), \
                     mock.patch("services.editor_projects.probe_media", return_value={"type":"video","duration":3,"size":14,"has_audio":True}), \
                     mock.patch("services.atomic_file_publish.publish_file_no_replace", side_effect=publish):
                    self.assertFalse(self.ns['_run_tool_editor_export'](job['id']))
                self.assertEqual(job['output_files'], [])
                if mode == "persistence":
                    self.assertFalse(output.exists()); self.assertFalse(sidecar.exists())
                else:
                    self.assertEqual(output.read_bytes(), b"rendered-video")
                    self.assertTrue(sidecar.exists())
                    self.assertEqual(job['recovery_state'], 'blocked')
                    before = output.read_bytes(), sidecar.read_bytes()
                    job.update(status='queued', queue_held=False)
                    with mock.patch("services.editor_export.render_single_source_cut") as encoder:
                        self.assertFalse(self.ns['_run_tool_editor_export'](job['id']))
                    encoder.assert_not_called()
                    self.assertEqual((output.read_bytes(), sidecar.read_bytes()), before)
                self.ns['_queue_recovery_checkpoint'] = lambda current, **kw: current.update(**kw) or True
                if output.exists(): output.unlink()
                if sidecar.exists(): sidecar.unlink()

    def test_editor_request_and_policy_drift_fail_before_encode(self):
        for mode in ('params', 'policy', 'project', 'input-closure'):
            with self.subTest(mode=mode):
                self.jobs.clear(); self.registered.clear()
                job = self.worker_namespace()
                if mode == 'params': job['params']['editor_source_in'] = 1
                elif mode == 'policy': job['access_policy']['private'] = False
                elif mode == 'project': job['_recovery_project_digest'] = 'recreated'
                else:
                    job['_recovery_manifest_pointer'] = atomic_write_request_manifest(self.project,
                        job_id=job['id'], params=job['params'], inputs=[])
                with mock.patch('services.editor_export.render_single_source_cut') as encoder:
                    self.assertFalse(self.ns['_run_tool_editor_export'](job['id']))
                encoder.assert_not_called()
                self.assertEqual(job['output_files'], [])
                self.assertFalse(list(self.project.glob('editor_cut_*')))

    def test_title_worker_receives_sealed_plan_and_drops_inherited_regeneration_recipe(self):
        from services.editor_projects import apply_output_video_trim
        proposed = copy.deepcopy(self.timeline)
        layer = {"id": "one", "text": "TITLE", "start": 0.5, "duration": 1, "position": "center"}
        proposed["tracks"][2]["items"] = [layer]
        self.timeline = save_editor_project(str(self.outputs), "scene",
            apply_output_video_trim(self.timeline, proposed), expected_revision=1)
        job = self.worker_namespace()
        def render(_source, destination, **options):
            self.assertEqual(options["text_layers"], [layer])
            self.assertEqual(options["canvas"], self.timeline["canvas"])
            Path(destination).write_bytes(b"rendered-video")
        with mock.patch("services.editor_export.render_single_source_cut", side_effect=render), \
             mock.patch("services.editor_projects.probe_media", return_value={
                 "type": "video", "duration": 3.0, "size": 14, "has_audio": True, "width": 128, "height": 72,
             }):
            self.assertTrue(self.ns["_run_tool_editor_export"](job["id"]))
        sidecar = json.loads((self.project / job["output_files"][0]).with_suffix(".meta.json").read_text())
        self.assertIsNone(sidecar["params"])
        self.assertEqual(sidecar["transform"]["text_layers"], [layer])
        self.assertTrue(sidecar["private"])
        self.assertEqual(self.source.read_bytes(), b"original-video")

    def test_trim_preserves_nested_history_and_sealed_publication_without_reencoding(self):
        nested = {'version':2,'steps':[{'step':'upscale','outcome':'applied','method':'lanczos2'}], 'branches':[
            {'name':'earlier.mp4','revision':'sha256:'+'a'*64,'source_in':2,'duration':1,
             'history':{'version':1,'steps':[{'step':'voice_clone','outcome':'unconfirmed'}]}},
            {'name':'legacy.mp4','revision':'sha256:'+'b'*64,'source_in':0,'duration':1},
        ]}
        source_meta = self.source.with_suffix('.meta.json')
        metadata = json.loads(source_meta.read_text()); metadata['postprocessing'] = nested
        source_meta.write_text(json.dumps(metadata))
        self.timeline['assets']['source-video']['output_revision'] = self.source_revision()
        self.timeline = save_editor_project(str(self.outputs), 'scene', self.timeline, expected_revision=1)
        job = self.worker_namespace()
        completion = self.ns['finish_job']
        def finish(current, status, **kw):
            return False if status == 'completed' else completion(current, status, **kw)
        self.ns['finish_job'] = finish
        with mock.patch('services.editor_export.render_single_source_cut', side_effect=lambda src,dst,**kw:Path(dst).write_bytes(b'rendered')), \
             mock.patch('services.editor_projects.probe_media', return_value={'type':'video','duration':3,'size':8,'has_audio':True}):
            self.assertFalse(self.ns['_run_tool_editor_export'](job['id']))
        output = self.project / f"editor_cut_{job['id']}.mp4"
        sealed = output.with_suffix('.meta.json').read_bytes()
        history = json.loads(sealed)['postprocessing']
        self.assertEqual(history['branches'][0]['history'], nested)
        self.assertEqual(history['steps'], [])
        source_bytes, metadata_bytes = self.source.read_bytes(), source_meta.read_bytes()
        source_meta.unlink(); self.source.unlink()
        job.update(status='queued',queue_held=False)
        self.ns['finish_job'] = completion
        with mock.patch('services.editor_export.render_single_source_cut') as encoder:
            self.assertFalse(self.ns['_run_tool_editor_export'](job['id']))
        encoder.assert_not_called()
        self.assertEqual(output.with_suffix('.meta.json').read_bytes(), sealed)
        # Editor's existing source-authority requirement still applies.
        self.source.write_bytes(source_bytes); source_meta.write_bytes(metadata_bytes)
        job.update(status='queued',queue_held=False)
        with mock.patch('services.editor_export.render_single_source_cut') as encoder, \
             mock.patch('services.recorded_finishing.read_admitted_history', side_effect=AssertionError('sealed history must be adopted')):
            self.assertTrue(self.ns['_run_tool_editor_export'](job['id']))
        encoder.assert_not_called()
        self.assertEqual(output.with_suffix('.meta.json').read_bytes(), sealed)

    def test_editor_finishing_keeps_repeated_clip_histories_and_trims_separate(self):
        second = self.sequence()
        histories = {
            self.source.name: {"version": 1, "steps": [{"step": "film_grain", "outcome": "applied", "private_path": str(self.root)}]},
            second.name: {"version": 1, "steps": [{"step": "voice_clone", "outcome": "not_applied"}]},
        }
        for source in (self.source, second):
            sidecar = source.with_suffix('.meta.json')
            metadata = json.loads(sidecar.read_text()); metadata['postprocessing'] = histories[source.name]
            sidecar.write_text(json.dumps(metadata))
        # Save the exact updated source revisions, then append A a second time.
        current = copy.deepcopy(self.timeline)
        for asset in current['assets'].values():
            asset['output_revision'] = self.source_revision(self.project / asset['output_id'])
        current = append_output_video_clip(current, output_name=self.source.name,
            output_revision=self.source_revision(), media={"type":"video","duration":3,"width":128,"height":72,"fps":24,"has_audio":True,"private":True})
        proposed = copy.deepcopy(current)
        proposed['tracks'][0]['items'][-1].update(source_in=1, duration=1)
        current = apply_output_video_trim(current, proposed)
        self.timeline = save_editor_project(str(self.outputs), 'scene', current, expected_revision=self.timeline['revision'])
        job = self.worker_namespace()
        with mock.patch('services.editor_export.render_video_sequence', side_effect=lambda clips, dst, **kw: Path(dst).write_bytes(b'rendered')), \
             mock.patch('services.editor_projects.probe_media', return_value={'type':'video','duration':6,'size':8,'has_audio':True,'width':128,'height':72,'fps':24}):
            self.assertTrue(self.ns['_run_tool_editor_export'](job['id']))
        sidecar = json.loads((self.project / job['output_files'][0]).with_suffix('.meta.json').read_text())
        history = sidecar['postprocessing']
        self.assertEqual(history['steps'], [])
        self.assertEqual([b['name'] for b in history['branches']], ['source.mp4','second.mp4','source.mp4'])
        self.assertEqual([b['source_in'] for b in history['branches']], [0,0,1])
        self.assertEqual(history['branches'][0]['history']['steps'], [{'step':'film_grain','outcome':'applied'}])
        self.assertEqual(history['branches'][1]['history'], histories[second.name])
        self.assertEqual(history['branches'][2]['history'], history['branches'][0]['history'])
        self.assertNotIn(str(self.root), json.dumps(history))

    def test_sequence_worker_rechecks_second_source_before_atomic_publication(self):
        second = self.sequence()
        job = self.worker_namespace()
        def render(clips, destination, **options):
            self.assertEqual([item["path"] for item in clips], [str(self.source), str(second)])
            self.assertEqual((options["width"], options["height"], options["fps"]), (128, 72, 24))
            Path(destination).write_bytes(b"rendered-video")
        with mock.patch("services.editor_export.render_video_sequence", side_effect=render), mock.patch("services.editor_projects.probe_media", return_value={"type": "video", "duration": 5.0, "size": 14, "has_audio": True, "width": 128, "height": 72, "fps": 24}):
            self.assertTrue(self.ns["_run_tool_editor_export"](job["id"]))
        sidecar = json.loads((self.project / job["output_files"][0]).with_suffix(".meta.json").read_text())
        self.assertEqual(sidecar["transform"]["kind"], "editor_sequence")
        self.assertIsNone(sidecar["params"])
        self.assertEqual([item["name"] for item in sidecar["transform"]["sources"]], ["source.mp4", "second.mp4"])
        self.assertNotIn(str(self.project), json.dumps(sidecar))
        self.jobs.clear()
        self.registered.clear()
        job = self.worker_namespace()
        def changed(clips, destination, **options):
            render(clips, destination, **options)
            second.write_bytes(b"replacement")
        before = set(self.project.glob("editor_cut_*"))
        with mock.patch("services.editor_export.render_video_sequence", side_effect=changed), mock.patch("services.editor_projects.probe_media", return_value={"type": "video", "duration": 5.0, "size": 14, "has_audio": True, "width": 128, "height": 72, "fps": 24}):
            self.assertFalse(self.ns["_run_tool_editor_export"](job["id"]))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(set(self.project.glob("editor_cut_*")), before)

    def test_sequence_metadata_without_single_recipe_retains_provenance(self):
        second = self.sequence()
        job = self.worker_namespace()
        filename = "sequence.mp4"
        (self.project / filename).write_bytes(b"video")
        self.ns["_write_tool_sidecar"](str(self.project), filename, source_name=self.source.name,
            tool="editor_export", params={"model_type": "source-model", "seed": 123}, elapsed=1,
            job_id=job["id"], source_revision=self.source_revision())
        self.ns.update({"_request_project_workspace": lambda _request, workspace: workspace,
                        "_redact_local_paths": lambda value: value})
        load_functions(self.ns, "get_output_metadata")
        self.ns["_require_authorized_output"] = lambda *_args: (str(self.project), str(self.project / filename), {})
        metadata = self.ns["get_output_metadata"](Request({}), filename, workspace="scene")
        self.assertEqual(metadata["source"], "sidecar")
        self.assertIsNone(metadata["params"])
        self.assertEqual([item["name"] for item in metadata["transform"]["sources"]], [self.source.name, second.name])
        self.assertTrue(metadata["private"])

    def test_sequence_wrong_canvas_or_fps_never_publishes(self):
        self.sequence()
        for width, fps in ((64, 24), (128, 30)):
            with self.subTest(width=width, fps=fps):
                self.jobs.clear()
                self.registered.clear()
                job = self.worker_namespace()
                def render(_clips, destination, **_options):
                    Path(destination).write_bytes(b"rendered-video")
                with mock.patch("services.editor_export.render_video_sequence", side_effect=render), mock.patch("services.editor_projects.probe_media", return_value={"type": "video", "duration": 5, "size": 14, "has_audio": True, "width": width, "height": 72, "fps": fps}):
                    self.assertFalse(self.ns["_run_tool_editor_export"](job["id"]))
                self.assertEqual(job["status"], "failed")
                self.assertFalse(list(self.project.glob("editor_cut_*")))

    def test_single_remaining_clip_uses_canvas_and_rejects_wrong_size(self):
        second = self.sequence()
        proposed = copy.deepcopy(self.timeline)
        proposed["tracks"][0]["items"].pop(0)
        self.timeline = save_editor_project(str(self.outputs), "scene",
            apply_output_video_trim(self.timeline, proposed), expected_revision=self.timeline["revision"])
        for width, height in ((72, 128), (128, 72)):
            with self.subTest(width=width, height=height):
                self.jobs.clear(); self.registered.clear()
                job = self.worker_namespace()
                def render(source, destination, **options):
                    self.assertEqual(source, str(second))
                    self.assertEqual(options["canvas"], self.timeline["canvas"])
                    Path(destination).write_bytes(b"rendered-video")
                with mock.patch("services.editor_export.render_single_source_cut", side_effect=render), mock.patch("services.editor_projects.probe_media", return_value={
                    "type": "video", "duration": 2, "size": 14, "has_audio": False,
                    "width": width, "height": height, "fps": 30,
                }):
                    accepted = self.ns["_run_tool_editor_export"](job["id"])
                self.assertEqual(accepted, width == 128)
                if accepted:
                    sidecar = json.loads((self.project / job["output_files"][0]).with_suffix(".meta.json").read_text())
                    self.assertEqual(sidecar["transform"]["canvas"], self.timeline["canvas"])
                    self.assertIsNone(sidecar["params"])
                else:
                    self.assertFalse(list(self.project.glob("editor_cut_*")))

    def test_worker_cancel_or_source_replacement_never_publishes(self):
        for mode in ("cancel", "changed"):
            with self.subTest(mode=mode):
                job = self.worker_namespace()
                def render(_source, destination, **_options):
                    Path(destination).write_bytes(b"rendered-video")
                    if mode == "cancel":
                        job["status"] = "cancelled"
                    else:
                        self.source.write_bytes(b"replacement")
                with mock.patch("services.editor_export.render_single_source_cut", side_effect=render), \
                     mock.patch("services.editor_projects.probe_media", return_value={
                         "type": "video", "duration": 3.0, "size": 14, "has_audio": True,
                     }):
                    self.assertFalse(self.ns["_run_tool_editor_export"](job["id"]))
                self.assertEqual(job["status"], "cancelled" if mode == "cancel" else "failed")
                self.assertFalse(list(self.project.glob("editor_cut_*.mp4")))
                self.assertFalse(list(self.project.glob("editor_cut_*.meta.json")))
                self.source.write_bytes(b"original-video")
                self.jobs.clear()
                self.registered.clear()


if __name__ == "__main__":
    unittest.main()
