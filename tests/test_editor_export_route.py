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
    append_output_video_clip, create_output_video_timeline, load_editor_project, save_editor_project,
)
from services.output_access import public_output_policy, stamp_sidecar_policy  # noqa: E402


TREE = ast.parse((ROOT / "app/launch.py").read_text(encoding="utf-8"))


def load_functions(namespace, *names):
    wanted = set(names)
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
            "json": json, "os": os, "hmac": hmac, "math": math,
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
                       "_editor_require_current_source", "export_output_editor_project", "append_output_editor_clip")

    def source_revision(self, path=None):
        path = path or self.source
        media = path.read_bytes()
        sidecar = path.with_suffix(".meta.json").read_bytes()
        return "sha256:" + hashlib.sha256(media + sidecar).hexdigest()

    def authorize(self, _request, project, *, existing_only=False, permission=None):
        self.permissions.append((project, existing_only, permission))
        if project != "scene":
            raise HTTPException(status_code=403, detail="Project access denied")
        return str(self.project)

    def output(self, request, project, name):
        self.authorize(request, project, existing_only=True, permission="project.mutate")
        path = self.project / name
        if name not in {"source.mp4", "second.mp4"} or not path.exists():
            raise HTTPException(status_code=404, detail="Output not found")
        sidecar_path = path.with_suffix(".meta.json")
        sidecar = json.loads(sidecar_path.read_text()) if sidecar_path.exists() else {}
        return str(self.project), str(path), sidecar

    def register(self, job, **options):
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
                name: json.loads((Path(directory) / name).with_suffix(".meta.json").read_text()) for name in names},
            "stamp_sidecar_policy": stamp_sidecar_policy,
        })
        load_functions(self.ns, "_editor_export_source", "_write_tool_sidecar", "_run_tool_editor_export")
        return job

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
