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
    create_output_video_timeline, load_editor_project, save_editor_project,
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
            "time": time, "uuid": uuid,
            "wgp": types.SimpleNamespace(server_config={"save_path": str(self.outputs)}),
            "_request_remote": contextvars.ContextVar("remote", default=True),
            "_reserve_workspace_operations": lambda *_args: nullcontext(),
            "_output_lineage_mutation_guard": lambda *_args: nullcontext(),
            "_require_project_access": self.authorize,
            "_require_authorized_output": self.output,
            "_output_share_revision": lambda *_args: self.source_revision(),
            "public_output_policy": public_output_policy,
            "_jobs": self.jobs,
            "_new_generation_job_id": lambda: "a" * 32,
            "_queue_recovery_register_and_publish": self.register,
            "_run_tool_editor_export": object(),
        }
        load_functions(self.ns, "_editor_request_body", "_editor_save_root",
                       "_editor_require_current_source", "export_output_editor_project")

    def source_revision(self):
        media = self.source.read_bytes()
        sidecar = self.source.with_suffix(".meta.json").read_bytes()
        return "sha256:" + hashlib.sha256(media + sidecar).hexdigest()

    def authorize(self, _request, project, *, existing_only=False, permission=None):
        self.permissions.append((project, existing_only, permission))
        if project != "scene":
            raise HTTPException(status_code=403, detail="Project access denied")
        return str(self.project)

    def output(self, request, project, name):
        self.authorize(request, project, existing_only=True, permission="project.mutate")
        if name != "source.mp4" or not self.source.exists():
            raise HTTPException(status_code=404, detail="Output not found")
        sidecar = json.loads(self.source.with_suffix(".meta.json").read_text())
        return str(self.project), str(self.source), sidecar

    def register(self, job, **options):
        self.registered.append((job, options))
        self.jobs[job["id"]] = job

    def submit(self, *, expected=None, project="scene"):
        return asyncio.run(self.ns["export_output_editor_project"](
            project, self.timeline["id"], Request({
                "expected_revision": self.timeline["revision"] if expected is None else expected,
            }),
        ))

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
            "load_media_sidecars": lambda _directory, _names: {
                "source.mp4": json.loads(self.source.with_suffix(".meta.json").read_text())},
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
