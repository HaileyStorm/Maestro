"""Model-free Director parent/child restart recovery tests."""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import director_pipeline as director  # noqa: E402


def _launch_functions(names: set[str], namespace: dict) -> dict:
    source = (APP / "launch.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP / "launch.py"))
    selected = []
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ):
            node = copy.deepcopy(node)
            node.decorator_list = []
            selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "isolated-director-launch", "exec"), namespace)
    return namespace


class _FakeWgp:
    def __init__(self, save_path: str):
        self.save_path = save_path
        self.server_config = {"services": {}}
        self.concat_calls = 0

    def concatenate_multi_clip_videos(
        self, inputs, output, audio, *, audio_start_sec=0,
    ):
        self.concat_calls += 1
        Path(output).write_bytes(b"joined:" + b"|".join(
            Path(item).read_bytes() for item in inputs
        ))
        return True


class DirectorRecoveryTests(unittest.TestCase):
    def test_launch_imports_re_before_compiling_director_request_ids(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(APP / "launch.py"))
        regex_line = next(
            node.lineno
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_DIRECTOR_REQUEST_ID_RE"
                for target in node.targets
            )
        )
        self.assertTrue(any(
            isinstance(node, ast.Import)
            and node.lineno < regex_line
            and any(alias.name == "re" and alias.asname is None for alias in node.names)
            for node in tree.body
        ))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.originals = {
            "jobs": director._jobs,
            "run": director._run_generation,
            "wgp": director._wgp,
            "pipelines": director._pipelines,
            "threads": director._pipeline_threads,
            "children": director._pipeline_child_jobs,
            "register": director._recovery_register_parent,
            "prepare": director._recovery_prepare_parent_state,
            "checkpoint": director._recovery_checkpoint_parent,
            "prepare_delete": director._recovery_prepare_parent_delete,
            "remove": director._recovery_remove_parent,
            "submit": director._recovery_submit_child,
            "verify": director._recovery_verify_child,
            "validate": director._recovery_validate_child,
            "runtime_admission": director._runtime_admission,
        }
        director._jobs = {}
        director._run_generation = lambda _job_id: None
        director._wgp = _FakeWgp(str(self.root))
        director._pipelines = {}
        director._pipeline_threads = {}
        director._pipeline_child_jobs = {}
        director._recovery_register_parent = None
        director._recovery_prepare_parent_state = None
        director._recovery_checkpoint_parent = None
        director._recovery_prepare_parent_delete = None
        director._recovery_remove_parent = None
        director._recovery_submit_child = None
        director._recovery_verify_child = None
        director._recovery_validate_child = None
        director._runtime_admission = None

    def tearDown(self):
        director._jobs = self.originals["jobs"]
        director._run_generation = self.originals["run"]
        director._wgp = self.originals["wgp"]
        director._pipelines = self.originals["pipelines"]
        director._pipeline_threads = self.originals["threads"]
        director._pipeline_child_jobs = self.originals["children"]
        director._recovery_register_parent = self.originals["register"]
        director._recovery_prepare_parent_state = self.originals["prepare"]
        director._recovery_checkpoint_parent = self.originals["checkpoint"]
        director._recovery_prepare_parent_delete = self.originals[
            "prepare_delete"
        ]
        director._recovery_remove_parent = self.originals["remove"]
        director._recovery_submit_child = self.originals["submit"]
        director._recovery_verify_child = self.originals["verify"]
        director._recovery_validate_child = self.originals["validate"]
        director._runtime_admission = self.originals["runtime_admission"]
        self.temporary.cleanup()

    def _params(self):
        return {
            "auto_mode": True,
            "_maestro_session_id": "owner-a",
            "_maestro_access_policy": {
                "private": True,
                "explicit": False,
                "owner_session_id": "owner-a",
            },
        }

    def test_canonical_filename_matches_service_and_route_contract(self):
        self.assertEqual(
            director.pipeline_state_filename("abc123"),
            "_director_pipeline_abc123.json",
        )
        path = self.root / director.pipeline_state_filename("abc123")
        path.write_text(json.dumps({
            "pipeline_id": "abc123",
            "_params_snapshot": {"_maestro_session_id": "owner-a"},
        }), encoding="utf-8")

        class HTTPError(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code

        recovery_checks = []
        namespace = {
            "os": os,
            "json": json,
            "Request": object,
            "HTTPException": HTTPError,
            "_require_project_access": lambda _request, _workspace: str(self.root),
            "_require_director_recovery_parent_access": (
                lambda *_args, **kwargs: recovery_checks.append(kwargs) or True
            ),
        }
        _launch_functions({"_pipeline_owner", "_require_saved_pipeline"}, namespace)
        state, out_dir = namespace["_require_saved_pipeline"](
            types.SimpleNamespace(
                state=types.SimpleNamespace(maestro_session_id="owner-a"),
            ),
            "abc123",
            "default",
        )
        self.assertEqual(state["pipeline_id"], "abc123")
        self.assertEqual(out_dir, str(self.root))
        self.assertTrue(recovery_checks[0]["revalidate_inputs"])
        self.assertTrue(recovery_checks[0]["allow_blocked"])

    def test_authorized_saved_views_overlay_live_blocked_recovery_without_rewriting_json(self):
        pid = "remote-blocked-view"
        saved = {
            "pipeline_id": pid,
            "status": "running",
            "workspace": "project-a",
            "pipeline_type": "short_film_story",
            "clips": [],
        }
        director._pipelines[pid] = {
            "id": pid,
            "status": "blocked",
            "phase": "blocked_remote_reauth",
            "recovery_state": "blocked_remote_reauth",
            "recovery_actions": ["resume"],
        }
        namespace = {
            "Request": object,
            "HTTPException": RuntimeError,
            "_DIRECTOR_RECOVERY_STATES": frozenset({
                "blocked_input_changed", "blocked_remote_reauth",
                "interrupted", "paused", "retrying", "terminal",
            }),
            "_DIRECTOR_RECOVERY_REASONS": {
                "blocked_input_changed": (
                    "input_missing_or_changed",
                    "A required Director input is missing or changed",
                ),
                "blocked_remote_reauth": (
                    "owner_reauthentication_required",
                    "Unlock this project to resume Director",
                ),
            },
            "_get_active_workspace": lambda: "default",
            "_request_project_workspace": (
                lambda _request, workspace: workspace or "default"
            ),
            "_require_project_access": lambda _request, workspace: (
                str(self.root)
                if workspace == "project-a"
                else (_ for _ in ()).throw(RuntimeError("opaque"))
            ),
            "_require_saved_pipeline": lambda _request, candidate, workspace: (
                (copy.deepcopy(saved), str(self.root))
                if candidate == pid and workspace == "project-a"
                else (_ for _ in ()).throw(RuntimeError("opaque"))
            ),
            "_public_pipeline_state": lambda state: copy.deepcopy(state),
        }
        _launch_functions({
            "_public_director_recovery_metadata",
            "_saved_pipeline_live_recovery_overlay",
            "list_saved_pipelines",
            "get_saved_pipeline",
        }, namespace)
        namespace["_saved_pipeline_live_recovery_overlay"].__globals__.update(namespace)
        request = types.SimpleNamespace()
        with patch.object(director, "list_pipeline_states", return_value=[{
            "id": pid,
            "status": "running",
            "pipeline_type": "short_film_story",
            "created_at": 1.0,
            "clip_count": 0,
            "output_count": 0,
            "scene_description": "",
        }]):
            listed = namespace["list_saved_pipelines"](
                request, "project-a",
            )["pipelines"][0]
        detailed = namespace["get_saved_pipeline"](
            request, pid, "project-a",
        )
        for view in (listed, detailed):
            self.assertEqual(view["status"], "blocked")
            self.assertEqual(view["phase"], "blocked_remote_reauth")
            self.assertEqual(view["recovery_actions"], ["resume"])
            self.assertEqual(
                view["recovery_reason"],
                "owner_reauthentication_required",
            )
        self.assertEqual(saved["status"], "running")
        self.assertNotIn("recovery_actions", saved)

        director._pipelines[pid].update({
            "phase": "blocked_input_changed",
            "recovery_state": "blocked_input_changed",
            # Even a malformed internal action cannot escape the bounded view.
            "recovery_actions": ["resume"],
        })
        changed = namespace["get_saved_pipeline"](
            request, pid, "project-a",
        )
        self.assertEqual(changed["recovery_actions"], [])
        self.assertEqual(changed["recovery_reason"], "input_missing_or_changed")

    def test_parent_json_and_journal_precede_publication_thread_and_ack(self):
        order = []

        def register(pid, pipeline, state, descriptor):
            order.append("register")
            self.assertNotIn(pid, director._pipelines)
            self.assertEqual(state["status"], "queued")
            path = self.root / descriptor["path"]
            self.assertTrue(path.is_file())
            return {"id": f"director-parent-{pid}"}

        def checkpoint(_pid, _state, _descriptor):
            order.append("checkpoint")

        director._recovery_register_parent = register
        director._recovery_checkpoint_parent = checkpoint

        def start(pid, *, resume=False):
            order.append("thread")
            self.assertIn(pid, director._pipelines)
            self.assertIn("_recovery_parent", director._pipelines[pid])
            self.assertFalse(resume)

        with patch.object(director, "_start_pipeline_worker", side_effect=start):
            pid = director.start_pipeline(self._params())
        self.assertEqual(order, ["register", "thread"])
        self.assertIn(pid, director._pipelines)

    def test_failed_parent_registration_never_publishes_or_starts(self):
        director._recovery_register_parent = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("journal failed")
        )
        with (
            patch.object(director, "_start_pipeline_worker") as worker,
            self.assertRaisesRegex(RuntimeError, "journal failed"),
        ):
            director.start_pipeline(self._params())
        worker.assert_not_called()
        self.assertEqual(director._pipelines, {})
        self.assertEqual(
            list(self.root.glob("_director_pipeline_*.json")), [],
        )

    def test_pipeline_json_commit_fsyncs_file_and_directory(self):
        path = self.root / director.pipeline_state_filename("fsync1")
        with patch.object(director.os, "fsync", wraps=os.fsync) as fsync:
            director._write_pipeline_json_unlocked(
                str(path), {"pipeline_id": "fsync1"},
            )
        self.assertEqual(json.loads(path.read_text())["pipeline_id"], "fsync1")
        self.assertGreaterEqual(fsync.call_count, 2)

    def test_saved_mutations_advance_sealed_parent_and_delete_retires_it(self):
        pid = "dashboard-durable"
        path = self.root / director.pipeline_state_filename(pid)
        state = {
            "pipeline_id": pid,
            "status": "completed",
            "workspace": "default",
            "clips": [{"tag": None}],
            "output_files": [],
            "_params_snapshot": self._params(),
        }
        director._write_pipeline_json_unlocked(str(path), state)
        parent = {"recovery_cursor": {
            "pipeline_id": pid,
            "state": director._pipeline_state_descriptor(
                str(self.root), pid,
            ),
        }}
        transitions = []

        def prepare(parent_id, candidate, descriptor):
            self.assertEqual(parent_id, pid)
            self.assertEqual(candidate["pipeline_id"], pid)
            parent["recovery_cursor"]["pending_state"] = dict(descriptor)
            transitions.append("prepare")
            return True

        def commit(parent_id, candidate, descriptor):
            self.assertEqual(parent_id, pid)
            parent["recovery_cursor"]["state"] = dict(descriptor)
            parent["recovery_cursor"].pop("pending_state", None)
            transitions.append("commit")

        director._recovery_prepare_parent_state = prepare
        director._recovery_checkpoint_parent = commit
        self.assertTrue(director.update_clip_tag(
            str(self.root), pid, 0, "good",
        ))
        self.assertTrue(director.update_clip_tag(
            str(self.root), pid, 0, "needs_work",
        ))
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["clips"][0]["tag"],
            "needs_work",
        )
        self.assertEqual(transitions, [
            "prepare", "commit", "prepare", "commit",
        ])
        self.assertEqual(
            parent["recovery_cursor"]["state"],
            director._pipeline_state_descriptor(str(self.root), pid),
        )

        delete_events = []
        director._recovery_prepare_parent_delete = lambda parent_id: (
            delete_events.append(("prepare", path.exists())) or True
        )
        director._recovery_remove_parent = lambda parent_id, project_dir: (
            delete_events.append(("remove", path.exists())) or True
        )
        result = director.delete_pipeline(str(self.root), pid)
        self.assertTrue(result["ok"])
        self.assertEqual(delete_events, [
            ("prepare", True), ("remove", False),
        ])

    def test_sealed_state_loader_rejects_atomic_swap_during_read(self):
        filename = "_director_pipeline_swap1.json"
        path = self.root / filename
        sealed = b'{"pipeline_id":"sealed"}'
        replacement = b'{"pipeline_id":"alterd"}'
        self.assertEqual(len(sealed), len(replacement))
        path.write_bytes(sealed)
        replacement_path = self.root / "replacement.json"
        replacement_path.write_bytes(replacement)
        namespace = {
            "os": os,
            "stat": __import__("stat"),
            "json": json,
            "hashlib": hashlib,
            "hmac": hmac,
            "_DIRECTOR_RECOVERY_STATE_MAX_BYTES": 64 * 1024 * 1024,
        }
        _launch_functions({"_load_director_recovery_state"}, namespace)
        descriptor = {
            "path": filename,
            "sha256": hashlib.sha256(sealed).hexdigest(),
            "size": len(sealed),
        }
        original_read = os.read
        swapped = False

        def read_and_swap(fd, count):
            nonlocal swapped
            chunk = original_read(fd, count)
            if not swapped:
                swapped = True
                os.replace(replacement_path, path)
            return chunk

        with patch.object(os, "read", side_effect=read_and_swap):
            loaded = namespace["_load_director_recovery_state"](
                str(self.root), filename, descriptor,
            )
        self.assertIsNone(loaded)

    def test_reconstruction_after_memory_clear_preserves_committed_plan(self):
        pid = "recovered1"
        state_path = self.root / director.pipeline_state_filename(pid)
        state = {
            "pipeline_id": pid,
            "status": "running",
            "phase": "generating_images",
            "workspace": "default",
            "source_remote": False,
            "created_at": 1.0,
            "_params_snapshot": self._params(),
            "clips": [{
                "image_prompt": "private image prompt",
                "video_prompt": "private video prompt",
                "planned_clip": {"start": 0, "end": 5},
                "start_image_filename": None,
                "keyframe_filenames": [],
                "video_filename": None,
            }],
            "output_files": [],
            "recovery": {"children": {}},
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with patch.object(director, "_start_pipeline_worker") as worker:
            restored = director.restore_registered_pipeline(
                state, str(state_path), {"id": "parent"},
            )
        self.assertEqual(restored["clip_plans"][0]["image_prompt"], "private image prompt")
        worker.assert_called_once_with(pid, resume=True)

    def test_remote_parent_is_blocked_until_explicit_resume(self):
        pid = "remote1"
        state_path = self.root / director.pipeline_state_filename(pid)
        state = {
            "pipeline_id": pid,
            "status": "running",
            "workspace": "default",
            "source_remote": True,
            "_params_snapshot": self._params(),
            "clips": [],
            "output_files": [],
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with patch.object(director, "_start_pipeline_worker") as worker:
            restored = director.restore_registered_pipeline(
                state,
                str(state_path),
                {
                    "id": "parent",
                    "owner_digest": "owner:v1:" + "a" * 64,
                    "project_digest": "project:v1:" + "b" * 64,
                },
                blocked_remote=True,
            )
        worker.assert_not_called()
        self.assertEqual(restored["status"], "blocked")
        self.assertEqual(restored["recovery_actions"], ["resume"])

    def test_input_block_dominates_saved_pause_and_continue_is_rejected(self):
        pid = "paused-invalid"
        state_path = self.root / director.pipeline_state_filename(pid)
        state = {
            "pipeline_id": pid,
            "status": "paused",
            "pause_reason": "review_prompts",
            "workspace": "default",
            "_params_snapshot": self._params(),
            "clips": [],
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with patch.object(director, "_start_pipeline_worker") as worker:
            restored = director.restore_registered_pipeline(
                state,
                str(state_path),
                {"id": "parent"},
                blocked_reason="Saved input changed.",
                defer_worker=True,
            )
            self.assertEqual(restored["status"], "blocked")
            self.assertEqual(restored["recovery_actions"], [])
            self.assertFalse(director.continue_pipeline(pid))
            worker.assert_not_called()

    def test_local_and_reauthenticated_remote_pauses_continue_explicitly(self):
        for remote in (False, True):
            pid = "pause-remote" if remote else "pause-local"
            state_path = self.root / director.pipeline_state_filename(pid)
            state = {
                "pipeline_id": pid,
                "status": "paused",
                "phase": "generating_images",
                "pause_reason": "review_prompts",
                "workspace": "default",
                "source_remote": remote,
                "_params_snapshot": self._params(),
                "clips": [],
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with patch.object(director, "_start_pipeline_worker") as worker:
                restored = director.restore_registered_pipeline(
                    state,
                    str(state_path),
                    {"id": "parent"},
                    blocked_remote=remote,
                    defer_worker=True,
                )
                worker.assert_not_called()
                if remote:
                    self.assertEqual(restored["status"], "blocked")
                    self.assertEqual(restored["recovery_actions"], ["resume"])
                    self.assertTrue(director.reauthorize_paused_pipeline(pid))
                current = director.get_pipeline(pid)
                self.assertEqual(current["status"], "paused")
                self.assertEqual(current["recovery_actions"], ["continue"])
                self.assertTrue(director.continue_pipeline(pid))
                worker.assert_called_once_with(pid, resume=True)

    def test_reauth_and_continue_checkpoint_failures_roll_back_retry_state(self):
        pid = "pause-checkpoint-failure"
        state_path = self.root / director.pipeline_state_filename(pid)
        state = {
            "pipeline_id": pid,
            "status": "paused",
            "phase": "generating_images",
            "pause_reason": "review_images",
            "workspace": "default",
            "source_remote": True,
            "_params_snapshot": self._params(),
            "clips": [],
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        director.restore_registered_pipeline(
            state,
            str(state_path),
            {"id": "parent"},
            blocked_remote=True,
            defer_worker=True,
        )
        director._recovery_checkpoint_parent = lambda *_args: (
            (_ for _ in ()).throw(RuntimeError("journal failed"))
        )
        with self.assertRaisesRegex(RuntimeError, "checkpoint"):
            director.reauthorize_paused_pipeline(pid)
        current = director.get_pipeline(pid)
        self.assertEqual(current["status"], "blocked")
        self.assertEqual(current["recovery_actions"], ["resume"])
        self.assertTrue(current["_recovered_without_worker"])
        self.assertEqual(
            json.loads(state_path.read_text(encoding="utf-8"))["status"],
            "paused",
        )

        director._recovery_checkpoint_parent = None
        self.assertTrue(director.reauthorize_paused_pipeline(pid))
        director._recovery_checkpoint_parent = lambda *_args: (
            (_ for _ in ()).throw(RuntimeError("journal failed"))
        )
        with patch.object(director, "_start_pipeline_worker") as worker:
            with self.assertRaisesRegex(RuntimeError, "checkpoint"):
                director.continue_pipeline(pid)
            worker.assert_not_called()
        current = director.get_pipeline(pid)
        self.assertEqual(current["status"], "paused")
        self.assertEqual(current["recovery_actions"], ["continue"])
        self.assertTrue(current["_recovered_without_worker"])
        self.assertEqual(
            json.loads(state_path.read_text(encoding="utf-8"))["status"],
            "paused",
        )

    def test_restored_worker_waits_for_explicit_post_cleanup_start(self):
        pid = "deferred1"
        state_path = self.root / director.pipeline_state_filename(pid)
        state = {
            "pipeline_id": pid,
            "status": "running",
            "workspace": "default",
            "_params_snapshot": self._params(),
            "clips": [],
            "output_files": [],
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with patch.object(director, "_start_pipeline_worker") as worker:
            restored = director.restore_registered_pipeline(
                state,
                str(state_path),
                {"id": "parent"},
                defer_worker=True,
            )
            worker.assert_not_called()
            self.assertTrue(restored["_recovered_without_worker"])
            self.assertTrue(director.start_restored_pipeline(pid))
            worker.assert_called_once_with(pid, resume=True)

    def test_terminal_and_manual_blocked_states_never_resume(self):
        for status in ("completed", "failed", "cancelled"):
            pid = f"terminal-{status}"
            path = self.root / director.pipeline_state_filename(pid)
            path.write_text(json.dumps({
                "pipeline_id": pid,
                "status": status,
                "_params_snapshot": self._params(),
                "clips": [],
            }), encoding="utf-8")
            with patch.object(director, "_start_pipeline_worker") as worker:
                ok, message = director.resume_pipeline(pid, str(self.root))
            self.assertFalse(ok)
            self.assertIn("Terminal", message)
            worker.assert_not_called()

        blocked = "blocked-input"
        director._pipelines[blocked] = {
            "id": blocked,
            "status": "blocked",
            "recovery_state": "blocked_input_changed",
        }
        with patch.object(director, "_start_pipeline_worker") as worker:
            ok, message = director.resume_pipeline(blocked, str(self.root))
        self.assertFalse(ok)
        self.assertIn("blocked", message.lower())
        worker.assert_not_called()

    def test_runtime_admission_skips_terminal_history_and_rechecks_continue(self):
        calls = []
        director._runtime_admission = lambda params, **kwargs: calls.append(
            (dict(params), dict(kwargs))
        )

        terminal_pid = "terminal-history"
        terminal_path = self.root / director.pipeline_state_filename(terminal_pid)
        terminal = {
            "pipeline_id": terminal_pid,
            "status": "completed",
            "_params_snapshot": self._params(),
            "clips": [],
        }
        terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
        with patch.object(director, "_start_pipeline_worker") as worker:
            restored = director.restore_registered_pipeline(
                terminal, str(terminal_path), {"id": "parent"},
            )
        self.assertEqual(restored["status"], "completed")
        self.assertEqual(calls, [])
        worker.assert_not_called()
        director._runtime_admission = lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(RuntimeError("must not admit history"))
        )
        ok, message = director.resume_pipeline(terminal_pid, str(self.root))
        self.assertFalse(ok)
        self.assertIn("Terminal", message)

        paused_pid = "paused-admission"
        paused_path = self.root / director.pipeline_state_filename(paused_pid)
        paused = {
            "pipeline_id": paused_pid,
            "status": "paused",
            "pause_reason": "review_prompts",
            "source_remote": True,
            "_params_snapshot": self._params(),
            "clips": [],
        }
        paused_path.write_text(json.dumps(paused), encoding="utf-8")
        director._runtime_admission = lambda params, **kwargs: calls.append(
            (dict(params), dict(kwargs))
        )
        director.restore_registered_pipeline(
            paused, str(paused_path), {"id": "parent"}, defer_worker=True,
        )
        self.assertEqual(calls, [])
        with patch.object(director, "_start_pipeline_worker") as worker:
            self.assertTrue(director.continue_pipeline(paused_pid))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1]["source_remote"])
        worker.assert_called_once_with(paused_pid, resume=True)

    def test_completed_child_evidence_is_reused_without_submission(self):
        pid = "reuse1"
        unit = {"kind": "image_start", "variant": 0, "index": 2}
        token = director._child_unit_token(unit)
        director._pipelines[pid] = {
            "id": pid,
            "status": "running",
            "params": {},
            "_recovery": {"children": {token: {
                "state": "completed", "evidence": {"sealed": True},
            }}},
        }
        director._recovery_validate_child = lambda _out, evidence: (
            {"outputs": ["slot.png"], "clip_output_files": {}}
            if evidence == {"sealed": True} else None
        )
        director._recovery_submit_child = lambda *_args: self.fail(
            "completed child must not be submitted again"
        )
        outputs = director._submit_and_wait(
            {
                "_director_pipeline_id": pid,
                "_director_recovery_unit": unit,
            },
            timeout_s=0.1,
            out_dir=str(self.root),
        )
        self.assertEqual(outputs, ["slot.png"])

    def test_invalid_completed_child_gets_one_deterministic_new_attempt(self):
        unit = {"kind": "prepipeline_music", "variant": 0, "index": 0}
        submitted = []
        verify_calls = 0

        def submit(job, parent, child_unit, attempt):
            submitted.append((job["id"], parent, dict(child_unit), attempt))
            job["status"] = "completed"
            director._jobs[job["id"]] = job
            return job

        def verify(_job, _out_dir):
            nonlocal verify_calls
            verify_calls += 1
            if verify_calls == 1:
                return None
            return {
                "outputs": ["music.wav"],
                "clip_output_files": {},
                "artifacts": [{"basename": "music.wav"}],
            }

        director._recovery_submit_child = submit
        director._recovery_verify_child = verify
        outputs = director._submit_and_wait(
            {
                "_director_request_id": "a" * 32,
                "_director_recovery_parent_id": "a" * 32,
                "_director_recovery_unit": unit,
            },
            timeout_s=0.1,
            out_dir=str(self.root),
        )
        self.assertEqual(outputs, ["music.wav"])
        self.assertEqual([item[3] for item in submitted], [0, 1])
        self.assertNotEqual(submitted[0][0], submitted[1][0])
        self.assertEqual(
            submitted[1][0],
            director._director_child_job_id("a" * 32, unit, 1),
        )
        self.assertNotIn(None, director._pipeline_child_jobs)

    def test_resource_requeue_keeps_one_child_and_refreshes_wait_window(self):
        unit = {"kind": "clip", "variant": 0, "index": 0}
        submitted = []
        completion_threads = []

        def submit(job, _parent, _unit, _attempt):
            submitted.append(job["id"])
            job["status"] = "running"
            director._jobs[job["id"]] = job

            def retry_then_complete():
                # Commit the retry after the wait deadline exists, then finish
                # after the original window but inside the refreshed one.
                time.sleep(0.05)
                job["status"] = "queued"
                job["resource_retry_attempt"] = 1
                time.sleep(0.38)
                job["status"] = "completed"

            worker = threading.Thread(target=retry_then_complete)
            worker.start()
            completion_threads.append(worker)
            return job

        director._recovery_submit_child = submit
        director._recovery_verify_child = lambda _job, _out: {
            "outputs": ["clip.mp4"],
            "clip_output_files": {"0": "clip.mp4"},
            "artifacts": [{"basename": "clip.mp4"}],
        }
        outputs = director._submit_and_wait({
            "_director_request_id": "c" * 32,
            "_director_recovery_parent_id": "c" * 32,
            "_director_recovery_unit": unit,
        }, timeout_s=0.30, out_dir=str(self.root))
        for worker in completion_threads:
            worker.join(timeout=1)

        self.assertEqual(outputs, ["clip.mp4"])
        self.assertEqual(len(submitted), 1)
        self.assertEqual(len(set(submitted)), 1)

    def test_terminal_child_preserves_structured_resource_failure(self):
        unit = {"kind": "clip", "variant": 0, "index": 0}
        details = {
            "stage": "model_load",
            "code": "insufficient_host_memory",
            "detail": (
                "The generation model could not be loaded with the available "
                "host memory."
            ),
            "exception_type": "HostMemoryAdmissionError",
            "is_oom": False,
        }

        def submit(job, _parent, _unit, _attempt):
            job.update({
                "status": "failed",
                "error": details["detail"],
                "failure_details": details,
            })
            director._jobs[job["id"]] = job
            return job

        director._recovery_submit_child = submit
        with self.assertRaises(director.DirectorChildGenerationError) as raised:
            director._submit_and_wait({
                "_director_request_id": "d" * 32,
                "_director_recovery_parent_id": "d" * 32,
                "_director_recovery_unit": unit,
            }, timeout_s=0.05, out_dir=str(self.root))
        self.assertEqual(
            director._director_failure_details(
                raised.exception, code="director_pipeline_failed",
            )["code"],
            "insufficient_host_memory",
        )

    def test_invalid_child_retry_preserves_owner_and_access_policy(self):
        unit = {"kind": "prepipeline_music", "variant": 0, "index": 0}
        submitted = []

        def submit(job, _parent, _unit, attempt):
            submitted.append({
                "attempt": attempt,
                "session_id": job.get("session_id"),
                "access_policy": dict(job.get("access_policy") or {}),
            })
            job["status"] = "completed"
            director._jobs[job["id"]] = job
            return job

        verify_count = 0

        def verify(_job, _out_dir):
            nonlocal verify_count
            verify_count += 1
            if verify_count == 1:
                return None
            return {
                "outputs": ["music.wav"],
                "clip_output_files": {},
                "artifacts": [{"basename": "music.wav"}],
            }

        director._recovery_submit_child = submit
        director._recovery_verify_child = verify
        policy = {"private": True, "explicit": False}
        director._submit_and_wait({
            "_director_request_id": "b" * 32,
            "_director_recovery_parent_id": "b" * 32,
            "_director_recovery_unit": unit,
            "_maestro_session_id": "owner-b",
            "_maestro_access_policy": policy,
        }, timeout_s=0.1, out_dir=str(self.root))
        self.assertEqual([item["attempt"] for item in submitted], [0, 1])
        self.assertEqual(
            [item["session_id"] for item in submitted],
            ["owner-b", "owner-b"],
        )
        self.assertEqual(
            [item["access_policy"] for item in submitted],
            [policy, policy],
        )

    def test_partial_or_replaced_image_slots_require_sealed_evidence(self):
        pid = "slots1"
        plans = [
            {"keyframe_prompts": []},
            {"keyframe_prompts": ["middle"]},
        ]
        for name in ("start0.png", "start1.png", "key.png"):
            (self.root / name).write_bytes(name.encode())
        director._pipelines[pid] = {
            "id": pid,
            "status": "running",
            "params": {},
            "_recovery": {"children": {}},
        }
        director._recovery_validate_child = lambda _out, _evidence: None
        self.assertFalse(director._recovered_image_slots_complete(
            pid, str(self.root), {}, plans, ["start0.png"], [[], []],
        ))

        children = {}
        for unit, filename in (
            ({"kind": "image_start", "variant": 0, "index": 0}, "start0.png"),
            ({"kind": "image_start", "variant": 0, "index": 1}, "start1.png"),
            ({"kind": "image_keyframe", "variant": 1, "index": 0}, "key.png"),
        ):
            children[director._child_unit_token(unit)] = {
                "state": "completed",
                "evidence": {"filename": filename},
            }
        director._pipelines[pid]["_recovery"]["children"] = children
        director._recovery_validate_child = lambda _out, evidence: {
            "outputs": [evidence["filename"]],
            "clip_output_files": {},
        }
        self.assertTrue(director._recovered_image_slots_complete(
            pid,
            str(self.root),
            {},
            plans,
            ["start0.png", "start1.png"],
            [[], ["key.png"]],
        ))
        director._recovery_validate_child = lambda _out, evidence: (
            None if evidence["filename"] == "start1.png" else {
                "outputs": [evidence["filename"]],
                "clip_output_files": {},
            }
        )
        self.assertFalse(director._recovered_image_slots_complete(
            pid,
            str(self.root),
            {},
            plans,
            ["start0.png", "start1.png"],
            [[], ["key.png"]],
        ))

    def test_rejoin_is_stable_and_adopts_verified_final_sidecar(self):
        pid = "join1"
        for index in range(2):
            (self.root / f"start-{index}.png").write_bytes(b"image")
            (self.root / f"clip-{index}.mp4").write_bytes(
                f"clip-{index}".encode()
            )
        state = {
            "pipeline_id": pid,
            "workspace": "default",
            "_params_snapshot": self._params(),
            "clips": [{
                "start_image_filename": f"start-{index}.png",
                "video_filename": f"clip-{index}.mp4",
                "video_stale": False,
                "planned_clip": {"start": index * 5, "end": (index + 1) * 5},
            } for index in range(2)],
            "output_files": [],
        }
        (self.root / director.pipeline_state_filename(pid)).write_text(
            json.dumps(state), encoding="utf-8",
        )
        first = director._rejoin_clips_impl(str(self.root), pid)
        second = director._rejoin_clips_impl(str(self.root), pid)
        self.assertEqual(first["filename"], second["filename"])
        self.assertTrue(second["adopted"])
        self.assertEqual(director._wgp.concat_calls, 1)

    def test_legacy_rejoin_identity_hashes_current_audio_content(self):
        pid = "legacy-audio"
        audio = self.root / "song.wav"
        audio.write_bytes(b"first-audio")
        for index in range(2):
            (self.root / f"start-a{index}.png").write_bytes(b"image")
            (self.root / f"clip-a{index}.mp4").write_bytes(
                f"clip-{index}".encode()
            )
        state = {
            "pipeline_id": pid,
            "workspace": "default",
            "_params_snapshot": {
                **self._params(), "audio_path": str(audio),
            },
            "clips": [{
                "start_image_filename": f"start-a{index}.png",
                "video_filename": f"clip-a{index}.mp4",
                "video_stale": False,
                "planned_clip": {"start": index, "end": index + 1},
            } for index in range(2)],
            "output_files": [],
        }
        (self.root / director.pipeline_state_filename(pid)).write_text(
            json.dumps(state), encoding="utf-8",
        )
        first = director._rejoin_clips_impl(str(self.root), pid)
        audio.write_bytes(b"replacement-audio")
        second = director._rejoin_clips_impl(str(self.root), pid)
        self.assertNotEqual(first["filename"], second["filename"])
        self.assertEqual(director._wgp.concat_calls, 2)

    def test_director_parent_journal_contains_only_opaque_recovery_state(self):
        captured = {}
        consumed = []

        class Coordinator:
            def register_job(self, job, **identity):
                captured["job"] = json.loads(json.dumps(job))
                captured["identity"] = identity

        namespace = {
            "os": os,
            "QueueRecoveryRuntimeError": RuntimeError,
            "owner_principal_digest": lambda _secret, _owner: "owner-digest",
            "_session_secret": lambda: b"secret",
            "_queue_recovery_project_identity": (
                lambda _workspace, _project: "project-digest"
            ),
            "_queue_recovery_input_descriptors": lambda _job, _owner: [
                {"field": "audio_path:0", "sha256": "a" * 64, "size": 4},
            ],
            "atomic_write_request_manifest": lambda *_args, **_kwargs: {
                "path": ".maestro-recovery/parent.request.json",
                "schema": 1,
                "sha256": "b" * 64,
                "size": 10,
            },
            "remove_request_manifest": lambda *_args: None,
            "_queue_recovery_with_bounded_compaction": lambda operation: operation(),
            "_queue_recovery_coordinator": Coordinator(),
            "_director_recovery_parents": {},
            "_director_recovery_retire_preparation": (
                lambda request_id, project_dir, **identity: consumed.append(
                    (request_id, project_dir, identity)
                ) or True
            ),
        }
        _launch_functions({
            "_director_recovery_parent_job_id",
            "_director_recovery_register_parent",
        }, namespace)
        sensitive_prompt = "SECRET DIRECTOR PROMPT"
        sensitive_filename = "private-person-name-final.mp4"
        result = namespace["_director_recovery_register_parent"](
            "journal1",
            {
                "workspace": "default",
                "out_dir": str(self.root),
                "params": {
                    "prompt": sensitive_prompt,
                    "director_request_id": "d" * 32,
                    "_maestro_session_id": "owner",
                    "_maestro_access_policy": {"private": True},
                },
                "output_files": [sensitive_filename],
                "created_at": 1.0,
            },
            {"output_files": [sensitive_filename]},
            {"path": "_director_pipeline_journal1.json", "sha256": "c" * 64, "size": 9},
        )
        serialized = json.dumps(captured["job"], sort_keys=True)
        self.assertNotIn(sensitive_prompt, serialized)
        self.assertNotIn(sensitive_filename, serialized)
        self.assertNotIn("output_files", captured["job"])
        self.assertEqual(result["id"], "director-parent-journal1")
        self.assertEqual(consumed, [(
            "d" * 32,
            str(self.root),
            {
                "expected_workspace": "default",
                "expected_owner_digest": "owner-digest",
                "expected_project_digest": "project-digest",
            },
        )])
        namespace["_director_recovery_register_parent"](
            "journal-internal-id",
            {
                "workspace": "default",
                "out_dir": str(self.root),
                "params": {
                    "_director_request_id": "e" * 32,
                    "_maestro_session_id": "owner",
                },
            },
            {},
            {
                "path": "_director_pipeline_journal-internal-id.json",
                "sha256": "f" * 64,
                "size": 9,
            },
        )
        self.assertEqual(len(consumed), 1)

    def test_legacy_parent_payload_is_detected_and_stripped_before_checkpoint(self):
        namespace = {}
        _launch_functions({
            "_director_runtime_parent_from_snapshot",
            "_director_snapshot_has_legacy_artifacts",
        }, namespace)
        sensitive = "private-person-final.mp4"
        snapshot = {
            "id": "director-parent-old",
            "kind": "director_pipeline",
            "status": "running",
            "workspace": "default",
            "owner_principal": "owner-digest",
            "project_instance": "project-digest",
            "request_manifest": {"path": "manifest"},
            "output_files": [sensitive],
            "recovery_cursor": {
                "pipeline_id": "old",
                "output_files": [sensitive],
            },
        }
        self.assertTrue(namespace["_director_snapshot_has_legacy_artifacts"](
            snapshot
        ))
        clean = namespace["_director_runtime_parent_from_snapshot"](
            snapshot,
            identity_key="pipeline_id",
            identity_value="old",
            state_descriptor={
                "path": "_director_pipeline_old.json",
                "sha256": "a" * 64,
                "size": 10,
            },
            unit_kind="director_parent_state",
        )
        serialized = json.dumps(clean, sort_keys=True)
        self.assertNotIn(sensitive, serialized)
        self.assertNotIn("output_files", serialized)

    def test_pipeline_start_validates_public_preparation_before_service_start(self):
        class HTTPError(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code

        request_id = "d" * 32
        starts = []
        checked = []

        async def request_json():
            return {
                "workspace": "project-a",
                "director_request_id": request_id,
                "_director_request_id": "f" * 32,
            }

        namespace = {
            "Request": object,
            "HTTPException": HTTPError,
            "re": __import__("re"),
            "_CREDIT_ACCOUNT_PARAM": "_maestro_credit_account_id",
            "_CREDIT_REALM_PARAM": "_maestro_credit_execution_realm",
            "_credit_prepare_director_pipeline": lambda _body: False,
            "_init_pipeline": lambda: None,
            "_reject_client_director_image_role_internals": lambda _body: None,
            "_authorize_director_media_inputs": (
                lambda _request, _body: "project-a"
            ),
            "_resolve_director_image_role_request": (
                lambda _request, _body: "legacy"
            ),
            "_resolve_h3_style_workflow_request": (
                lambda _body, model_field="model_type": None
            ),
            "_require_director_preparation": (
                lambda _request, candidate, workspace: (
                    checked.append((candidate, workspace))
                    or {"status": "completed"}
                )
            ),
            "_http_output_policy_from_request": (
                lambda *_args, **_kwargs: {
                    "private": True,
                    "explicit": False,
                }
            ),
            "_begin_workspace_operation": lambda _workspace: None,
            "_end_workspace_operation": lambda _workspace: None,
        }
        _launch_functions({"director_pipeline_start"}, namespace)
        request = types.SimpleNamespace(
            json=request_json,
            state=types.SimpleNamespace(maestro_session_id="owner"),
        )
        with patch.object(
            director,
            "start_pipeline",
            side_effect=lambda body: starts.append(dict(body)) or "pipeline",
        ):
            response = asyncio.run(
                namespace["director_pipeline_start"](request)
            )
        self.assertEqual(response, {"pipeline_id": "pipeline"})
        self.assertEqual(checked, [(request_id, "project-a")])
        self.assertEqual(starts[0]["director_request_id"], request_id)
        self.assertNotIn("_director_request_id", starts[0])

        def reject_foreign(*_args, **_kwargs):
            raise HTTPError(status_code=404, detail="Director request not found")

        namespace["_require_director_preparation"] = reject_foreign
        with patch.object(director, "start_pipeline") as start_pipeline:
            with self.assertRaises(HTTPError) as rejected:
                asyncio.run(namespace["director_pipeline_start"](request))
        self.assertEqual(rejected.exception.status_code, 404)
        start_pipeline.assert_not_called()

    def test_changed_input_restores_parent_blocked_and_recreated_project_is_opaque(self):
        class RecoveryError(RuntimeError):
            pass

        class HTTPError(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        owner_digest = "owner-digest"
        project_digest = "project-digest"
        pid = "changed-input"
        state_path = self.root / director.pipeline_state_filename(pid)
        state_path.write_text(json.dumps({
            "pipeline_id": pid,
            "status": "running",
            "workspace": "default",
            "_params_snapshot": {"_maestro_session_id": "owner"},
            "clips": [],
        }), encoding="utf-8")
        checkpoints = []
        tombstones = []
        namespace = {
            "os": os,
            "json": json,
            "hashlib": hashlib,
            "hmac": hmac,
            "stat": __import__("stat"),
            "Request": object,
            "HTTPException": HTTPError,
            "QueueRecoveryRuntimeError": RecoveryError,
            "QueueRecoveryAdapterError": RecoveryError,
            "_director_recovery_parents": {},
            "_director_recovery_rejected_parents": {},
            "_pipeline_owner": lambda params: params.get("_maestro_session_id"),
            "owner_principal_digest": lambda _secret, _owner: owner_digest,
            "_session_secret": lambda: b"secret",
            "load_request_manifest": lambda *_args, **_kwargs: {
                "params": {
                    "pipeline_id": pid,
                    "state_filename": director.pipeline_state_filename(pid),
                },
                "inputs": [{"field": "audio_path:0"}],
            },
            "validate_manifest_inputs": (
                lambda *_args: (_ for _ in ()).throw(
                    RecoveryError("input changed")
                )
            ),
            "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: False,
            "_queue_recovery_checkpoint": (
                lambda parent, **updates: checkpoints.append((parent, updates))
            ),
            "_queue_recovery_durable_transition": (
                lambda proposal: tombstones.extend(proposal.tombstones)
            ),
            "DurableTransition": lambda **kwargs: types.SimpleNamespace(
                **kwargs,
            ),
            "remove_request_manifest": lambda *_args, **_kwargs: True,
            "_init_pipeline": lambda: None,
            "_queue_recovery_project_identity": (
                lambda _workspace, _project: project_digest
            ),
            "_DIRECTOR_RECOVERY_STATE_MAX_BYTES": 64 * 1024 * 1024,
        }
        _launch_functions({
            "_load_director_recovery_state",
            "_load_director_recovery_cursor_state",
            "_director_runtime_parent_from_snapshot",
            "_director_recovery_restore_parent",
            "_require_director_recovery_parent_access",
        }, namespace)
        state_bytes = state_path.read_bytes()
        state_descriptor = {
            "path": state_path.name,
            "sha256": hashlib.sha256(state_bytes).hexdigest(),
            "size": len(state_bytes),
        }
        snapshot = {
            "id": f"director-parent-{pid}",
            "kind": "director_pipeline",
            "workspace": "default",
            "source_remote": False,
            "owner_principal": owner_digest,
            "project_instance": project_digest,
            "request_manifest": {"path": "manifest"},
            "recovery_cursor": {
                "pipeline_id": pid,
                "state": {
                    "path": state_path.name,
                    "sha256": "0" * 64,
                    "size": len(state_bytes),
                },
                "pending_state": state_descriptor,
            },
        }
        original_pipelines = director._pipelines
        director._pipelines = {}
        try:
            with patch.object(director, "_start_pipeline_worker") as worker:
                resumable = namespace["_director_recovery_restore_parent"](
                    snapshot,
                    {"default": (str(self.root), project_digest)},
                )
            self.assertIsNone(resumable)
            worker.assert_not_called()
            self.assertEqual(director._pipelines[pid]["status"], "blocked")
            self.assertEqual(
                director._pipelines[pid]["recovery_actions"], [],
            )
            self.assertEqual(checkpoints[-1][1]["recovery_state"], "blocked")
        finally:
            director._pipelines = original_pipelines

        recreated_pid = "recreated"
        recreated = dict(snapshot)
        recreated["id"] = f"director-parent-{recreated_pid}"
        recreated["recovery_cursor"] = {"pipeline_id": recreated_pid}
        namespace["_director_recovery_restore_parent"](
            recreated,
            {"default": (str(self.root), "new-project-digest")},
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_session_id="owner"),
        )
        namespace["_queue_recovery_project_identity"] = (
            lambda _workspace, _project: "new-project-digest"
        )
        with self.assertRaises(HTTPError) as denied:
            namespace["_require_director_recovery_parent_access"](
                request,
                recreated_pid,
                "default",
                str(self.root),
                revalidate_inputs=True,
            )
        self.assertEqual(denied.exception.status_code, 404)

        tampered_pid = "tampered-state"
        tampered_path = self.root / director.pipeline_state_filename(tampered_pid)
        tampered_path.write_text(json.dumps({
            "pipeline_id": tampered_pid,
            "status": "running",
            "_params_snapshot": {"_maestro_session_id": "owner"},
            "clips": [],
        }), encoding="utf-8")
        original_bytes = tampered_path.read_bytes()
        tampered_snapshot = dict(snapshot)
        tampered_snapshot["id"] = f"director-parent-{tampered_pid}"
        tampered_snapshot["recovery_cursor"] = {
            "pipeline_id": tampered_pid,
            "state": {
                "path": tampered_path.name,
                "sha256": hashlib.sha256(original_bytes).hexdigest(),
                "size": len(original_bytes),
            },
        }
        tampered_path.write_text("{}", encoding="utf-8")
        self.assertIsNone(namespace["_director_recovery_restore_parent"](
            tampered_snapshot,
            {"default": (str(self.root), project_digest)},
        ))
        self.assertIn(
            tampered_pid,
            namespace["_director_recovery_rejected_parents"],
        )
        self.assertNotIn(
            tampered_pid,
            namespace["_director_recovery_parents"],
        )

        deleted_pid = "delete-crash"
        deleted_snapshot = dict(snapshot)
        deleted_snapshot["id"] = f"director-parent-{deleted_pid}"
        deleted_snapshot["recovery_cursor"] = {
            "pipeline_id": deleted_pid,
            "delete_pending": True,
            "state": {
                "path": director.pipeline_state_filename(deleted_pid),
                "sha256": "0" * 64,
                "size": 1,
            },
        }
        self.assertIsNone(namespace["_director_recovery_restore_parent"](
            deleted_snapshot,
            {"default": (str(self.root), project_digest)},
        ))
        self.assertEqual(
            tombstones, [f"director-parent-{deleted_pid}"],
        )

    def test_journal_known_route_returns_the_same_verified_state_bytes(self):
        class RecoveryError(RuntimeError):
            pass

        class HTTPError(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code

        pid = "route-sealed"
        filename = director.pipeline_state_filename(pid)
        state = {
            "pipeline_id": pid,
            "status": "running",
            "_params_snapshot": {"_maestro_session_id": "owner"},
        }
        raw = json.dumps(state, sort_keys=True).encode()
        (self.root / filename).write_bytes(raw)
        descriptor = {
            "path": filename,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
        parent = {
            "id": f"director-parent-{pid}",
            "_recovery_owner_digest": "owner-digest",
            "_recovery_project_digest": "project-digest",
            "_recovery_manifest_pointer": {"path": "manifest"},
            "recovery_cursor": {"pipeline_id": pid, "state": descriptor},
        }
        namespace = {
            "os": os,
            "stat": __import__("stat"),
            "json": json,
            "hashlib": hashlib,
            "hmac": hmac,
            "Request": object,
            "HTTPException": HTTPError,
            "QueueRecoveryRuntimeError": RecoveryError,
            "QueueRecoveryAdapterError": RecoveryError,
            "_DIRECTOR_RECOVERY_STATE_MAX_BYTES": 64 * 1024 * 1024,
            "_director_recovery_parents": {pid: parent},
            "_director_recovery_rejected_parents": {},
            "owner_principal_digest": lambda *_args: "owner-digest",
            "_session_secret": lambda: b"secret",
            "_queue_recovery_project_identity": lambda *_args: "project-digest",
            "load_request_manifest": lambda *_args, **_kwargs: {
                "params": {"pipeline_id": pid, "state_filename": filename},
                "inputs": [],
            },
            "validate_manifest_inputs": lambda *_args: None,
            "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: True,
        }
        _launch_functions({
            "_load_director_recovery_state",
            "_load_director_recovery_cursor_state",
            "_require_director_recovery_parent_access",
        }, namespace)
        verified = namespace["_require_director_recovery_parent_access"](
            types.SimpleNamespace(
                state=types.SimpleNamespace(maestro_session_id="owner"),
            ),
            pid,
            "default",
            str(self.root),
            revalidate_inputs=True,
        )
        self.assertEqual(verified, state)

        wrong = dict(state, pipeline_id="different-parent")
        wrong_raw = json.dumps(wrong, sort_keys=True).encode()
        (self.root / filename).write_bytes(wrong_raw)
        parent["recovery_cursor"]["state"] = {
            "path": filename,
            "sha256": hashlib.sha256(wrong_raw).hexdigest(),
            "size": len(wrong_raw),
        }
        with self.assertRaises(HTTPError) as rejected:
            namespace["_require_director_recovery_parent_access"](
                types.SimpleNamespace(
                    state=types.SimpleNamespace(maestro_session_id="owner"),
                ),
                pid,
                "default",
                str(self.root),
                revalidate_inputs=True,
            )
        self.assertEqual(rejected.exception.status_code, 409)

    def test_preparation_reconstructs_after_memory_clear_and_blocks_bad_inputs(self):
        class RecoveryError(RuntimeError):
            pass

        request_id = "c" * 32
        filename = f"_director_request_{request_id}.json"
        (self.root / filename).write_text(json.dumps({
            "request_id": request_id,
            "workspace": "default",
            "owner_session_id": "owner",
            "status": "running",
            "phase": "music_completed",
            "analysis": {"opaque": "private"},
        }), encoding="utf-8")
        checkpoints = []
        namespace = {
            "os": os,
            "json": json,
            "hashlib": hashlib,
            "hmac": hmac,
            "stat": __import__("stat"),
            "re": __import__("re"),
            "Request": object,
            "QueueRecoveryRuntimeError": RecoveryError,
            "_DIRECTOR_REQUEST_ID_RE": __import__("re").compile(r"^[0-9a-f]{32}$"),
            "_director_preparation_lock": threading.RLock(),
            "_director_preparation_parents": {},
            "_director_preparation_rejected_parents": {},
            "_director_preparation_states": {},
            "owner_principal_digest": lambda _secret, _owner: "owner-digest",
            "_session_secret": lambda: b"secret",
            "load_request_manifest": lambda *_args, **_kwargs: {
                "params": {"request_id": request_id, "state_filename": filename},
                "inputs": [{"field": "audio_path:0"}],
            },
            "validate_manifest_inputs": lambda *_args: None,
            "_queue_recovery_manifest_validator": lambda *_args, **_kwargs: True,
            "_queue_recovery_checkpoint": (
                lambda parent, **updates: checkpoints.append((parent, updates))
            ),
            "_DIRECTOR_RECOVERY_STATE_MAX_BYTES": 64 * 1024 * 1024,
        }
        _launch_functions({
            "_director_preparation_filename",
            "_load_director_recovery_state",
            "_load_director_recovery_cursor_state",
            "_director_runtime_parent_from_snapshot",
            "_director_recovery_restore_preparation",
            "director_preparation_status",
        }, namespace)
        state_bytes = (self.root / filename).read_bytes()
        snapshot = {
            "id": f"director-request-{request_id}",
            "workspace": "default",
            "source_remote": False,
            "owner_principal": "owner-digest",
            "project_instance": "project-digest",
            "request_manifest": {"path": "manifest"},
            "recovery_cursor": {
                "request_id": request_id,
                "state": {
                    "path": filename,
                    "sha256": "0" * 64,
                    "size": len(state_bytes),
                },
                "pending_state": {
                    "path": filename,
                    "sha256": hashlib.sha256(state_bytes).hexdigest(),
                    "size": len(state_bytes),
                },
            },
        }
        self.assertTrue(namespace["_director_recovery_restore_preparation"](
            snapshot,
            {"default": (str(self.root), "project-digest")},
        ))
        self.assertEqual(
            namespace["_director_preparation_states"][request_id]["phase"],
            "music_completed",
        )
        namespace["_require_director_preparation"] = (
            lambda *_args, **_kwargs: dict(
                namespace["_director_preparation_states"][request_id]
            )
        )
        public = namespace["director_preparation_status"](
            types.SimpleNamespace(), request_id,
        )
        self.assertEqual(public["actions"], ["analyze_audio"])
        self.assertNotIn("analysis", public)

        namespace["validate_manifest_inputs"] = (
            lambda *_args: (_ for _ in ()).throw(
                RecoveryError("changed")
            )
        )
        namespace["_director_preparation_parents"].clear()
        namespace["_director_preparation_states"].clear()
        self.assertTrue(namespace["_director_recovery_restore_preparation"](
            snapshot,
            {"default": (str(self.root), "project-digest")},
        ))
        blocked = namespace["_director_preparation_states"][request_id]
        self.assertEqual(blocked["phase"], "blocked_input_changed")
        namespace["_require_director_preparation"] = (
            lambda *_args, **_kwargs: dict(blocked)
        )
        public = namespace["director_preparation_status"](
            types.SimpleNamespace(), request_id,
        )
        self.assertEqual(public["status"], "blocked")
        self.assertEqual(public["actions"], [])
        self.assertNotIn("analysis", public)

        namespace["_director_preparation_parents"].clear()
        namespace["_director_preparation_states"].clear()
        (self.root / filename).write_text("{}", encoding="utf-8")
        self.assertFalse(namespace["_director_recovery_restore_preparation"](
            snapshot,
            {"default": (str(self.root), "project-digest")},
        ))
        self.assertIn(
            request_id,
            namespace["_director_preparation_rejected_parents"],
        )

    def test_failed_preparation_registration_removes_sensitive_state(self):
        class Coordinator:
            def register_job(self, *_args, **_kwargs):
                raise RuntimeError("journal unavailable")

        namespace = {
            "os": os,
            "json": json,
            "hashlib": hashlib,
            "re": __import__("re"),
            "threading": threading,
            "time": time,
            "uuid": uuid,
            "Request": object,
            "QueueRecoveryRuntimeError": RuntimeError,
            "_DIRECTOR_REQUEST_ID_RE": __import__("re").compile(r"^[0-9a-f]{32}$"),
            "_director_preparation_lock": threading.RLock(),
            "_director_preparation_parents": {},
            "_director_preparation_states": {},
            "_require_director_preparation": lambda *_args: None,
            "owner_principal_digest": lambda _secret, _owner: "owner-digest",
            "_session_secret": lambda: b"secret",
            "_queue_recovery_project_identity": (
                lambda _workspace, _project: "project-digest"
            ),
            "_queue_recovery_input_descriptors": lambda *_args: [],
            "atomic_write_request_manifest": lambda *_args, **_kwargs: {
                "path": ".maestro-recovery/request.json",
                "schema": 1,
                "sha256": "a" * 64,
                "size": 1,
            },
            "remove_request_manifest": lambda *_args: None,
            "_queue_recovery_with_bounded_compaction": lambda operation: operation(),
            "_queue_recovery_coordinator": Coordinator(),
        }
        _launch_functions({
            "_director_preparation_filename",
            "_director_preparation_payload",
            "_write_director_preparation_state",
            "_remove_director_preparation_state",
            "_register_director_preparation",
        }, namespace)
        request = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_session_id="owner",
            maestro_remote=False,
        ))
        with self.assertRaisesRegex(RuntimeError, "journal unavailable"):
            namespace["_register_director_preparation"](
                request,
                {"lyrics": "private lyrics"},
                "default",
                str(self.root),
            )
        self.assertEqual(list(self.root.glob("_director_request_*.json")), [])
        self.assertEqual(namespace["_director_preparation_states"], {})

    def test_preparation_retirement_reconciles_crash_and_lost_projects(self):
        request_id = "a" * 32
        parent_id = f"director-request-{request_id}"
        events = []
        namespace = {
            "hmac": hmac,
            "DurableTransition": lambda **kwargs: types.SimpleNamespace(
                **kwargs,
            ),
            "_director_preparation_rejected_parents": {},
            "_director_preparation_filename": (
                lambda candidate: f"_director_request_{candidate}.json"
            ),
            "_remove_director_preparation_state": (
                lambda project, candidate: events.append(
                    ("delete", project, candidate)
                )
            ),
            "_queue_recovery_durable_transition": (
                lambda transition: events.append(
                    ("tombstone", transition.name, transition.tombstones)
                )
            ),
            "remove_request_manifest": (
                lambda project, manifest: events.append(
                    ("manifest", project, manifest)
                )
            ),
        }
        _launch_functions({"_director_recovery_restore_preparation"}, namespace)
        pending = {
            "id": parent_id,
            "workspace": "default",
            "owner_principal": "owner-digest",
            "project_instance": "project-digest",
            "request_manifest": {"path": "manifest"},
            "recovery_cursor": {
                "request_id": request_id,
                "retire_pending": True,
            },
        }
        self.assertFalse(namespace["_director_recovery_restore_preparation"](
            pending,
            {"default": (str(self.root), "project-digest")},
        ))
        self.assertEqual(events, [
            ("delete", str(self.root), request_id),
            (
                "tombstone",
                "director-preparation-retirement-reconciled",
                (parent_id,),
            ),
            ("manifest", str(self.root), {"path": "manifest"}),
        ])
        self.assertNotIn(
            request_id, namespace["_director_preparation_rejected_parents"],
        )

        for projects, transition_name in (
            ({}, "director-preparation-missing-project-retired"),
            (
                {"default": (str(self.root), "replacement-project")},
                "director-preparation-recreated-project-retired",
            ),
        ):
            events.clear()
            terminal = copy.deepcopy(pending)
            terminal["recovery_cursor"].pop("retire_pending")
            terminal["recovery_state"] = "terminal"
            self.assertFalse(
                namespace["_director_recovery_restore_preparation"](
                    terminal, projects,
                )
            )
            self.assertEqual(events, [(
                "tombstone", transition_name, (parent_id,),
            )])

    def test_completed_preparation_retirement_is_intent_first_and_bounded(self):
        request_id = "e" * 32
        filename = f"_director_request_{request_id}.json"
        path = self.root / filename
        path.write_text("{}", encoding="utf-8")
        state = {
            "request_id": request_id,
            "workspace": "default",
            "owner_session_id": "owner",
            "status": "completed",
            "updated_at": 100.0,
        }
        parent = {
            "id": f"director-request-{request_id}",
            "recovery_cursor": {"request_id": request_id},
            "_recovery_manifest_pointer": {"path": "manifest"},
            "_recovery_owner_digest": "owner-digest",
            "_recovery_project_digest": "project-digest",
        }
        events = []
        namespace = {
            "os": os,
            "time": time,
            "re": __import__("re"),
            "Request": object,
            "hmac": hmac,
            "QueueRecoveryAdapterError": RuntimeError,
            "_DIRECTOR_REQUEST_ID_RE": __import__("re").compile(
                r"^[0-9a-f]{32}$"
            ),
            "_DIRECTOR_PREPARATION_RETENTION_S": 7 * 24 * 60 * 60,
            "_DIRECTOR_PREPARATION_MAX_RETAINED": 256,
            "_director_preparation_lock": threading.RLock(),
            "_director_preparation_parents": {request_id: parent},
            "_director_preparation_rejected_parents": {},
            "_director_preparation_states": {request_id: state},
            "_queue_recovery_checkpoint": (
                lambda current, **updates: (
                    events.append(("intent", path.exists())),
                    current.update(updates),
                )[-1]
            ),
            "_queue_recovery_durable_transition": (
                lambda proposal: events.append((
                    "tombstone", path.exists(), tuple(proposal.tombstones),
                ))
            ),
            "DurableTransition": lambda **kwargs: types.SimpleNamespace(
                **kwargs,
            ),
            "owner_principal_digest": (
                lambda _secret, owner: (
                    "owner-digest" if owner == "owner" else "other-owner"
                )
            ),
            "_session_secret": lambda: b"secret",
            "_queue_recovery_project_identity": (
                lambda workspace, project: (
                    "project-digest"
                    if workspace == "default" and project == str(self.root)
                    else "other-project"
                )
            ),
            "remove_request_manifest": lambda *_args, **_kwargs: True,
        }
        _launch_functions({
            "_director_preparation_filename",
            "_remove_director_preparation_state",
            "_director_recovery_retire_preparation",
            "_director_recovery_retire_old_preparations",
        }, namespace)
        for identity in (
            {
                "expected_workspace": "other",
                "expected_owner_digest": "owner-digest",
                "expected_project_digest": "project-digest",
            },
            {
                "expected_workspace": "default",
                "expected_owner_digest": "other-owner",
                "expected_project_digest": "project-digest",
            },
            {
                "expected_workspace": "default",
                "expected_owner_digest": "owner-digest",
                "expected_project_digest": "other-project",
            },
        ):
            self.assertFalse(
                namespace["_director_recovery_retire_preparation"](
                    request_id, str(self.root), **identity,
                )
            )
        self.assertEqual(events, [])
        self.assertTrue(path.exists())
        self.assertTrue(namespace["_director_recovery_retire_preparation"](
            request_id,
            str(self.root),
            expected_workspace="default",
            expected_owner_digest="owner-digest",
            expected_project_digest="project-digest",
        ))
        self.assertEqual(events[0], ("intent", True))
        self.assertEqual(events[1], (
            "tombstone", False, (f"director-request-{request_id}",),
        ))
        self.assertNotIn(request_id, namespace["_director_preparation_states"])

        completed = {
            f"{index:032x}": {
                "request_id": f"{index:032x}",
                "workspace": "default",
                "owner_session_id": "owner",
                "status": "completed",
                "updated_at": float(index),
            }
            for index in range(1, 4)
        }
        active_id = "f" * 32
        completed[active_id] = {
            "request_id": active_id,
            "workspace": "default",
            "owner_session_id": "owner",
            "status": "running",
            "updated_at": 0.0,
        }
        malformed_id = "b" * 32
        completed[malformed_id] = {
            "request_id": malformed_id,
            "workspace": "default",
            "owner_session_id": "owner",
            "status": "completed",
            "updated_at": "malformed",
        }
        namespace["_director_preparation_states"] = completed
        retired = []
        namespace["_director_recovery_retire_preparation"] = (
            lambda candidate, _project: retired.append(candidate) or True
        )
        count = namespace["_director_recovery_retire_old_preparations"](
            {"default": (str(self.root), "project")},
            now=10.0,
            retention_s=1000.0,
            max_retained=2,
        )
        self.assertEqual(count, 2)
        self.assertEqual(retired, [f"{1:032x}", malformed_id])
        self.assertNotIn(active_id, retired)

    def test_completed_preparation_runs_long_lived_retention_maintenance(self):
        request_id = "9" * 32
        parent = {"recovery_cursor": {"request_id": request_id}}
        events = []
        descriptor = {
            "path": f"_director_request_{request_id}.json",
            "sha256": "a" * 64,
            "size": 2,
        }
        namespace = {
            "time": types.SimpleNamespace(time=lambda: 10.0),
            "QueueRecoveryRuntimeError": RuntimeError,
            "_director_preparation_lock": threading.RLock(),
            "_director_preparation_states": {
                request_id: {
                    "request_id": request_id,
                    "status": "running",
                },
            },
            "_director_preparation_parents": {request_id: parent},
            "_director_preparation_payload": (
                lambda _state: (b"{}", dict(descriptor))
            ),
            "_queue_recovery_checkpoint": (
                lambda _parent, **updates: events.append(
                    ("checkpoint", updates.get("message"))
                )
            ),
            "_write_director_preparation_state": (
                lambda _project, _state: (
                    events.append(("write", _project)), dict(descriptor)
                )[1]
            ),
            "_queue_recovery_existing_projects": (
                lambda: events.append(("projects",)) or {
                    "default": (str(self.root), "project-digest")
                }
            ),
            "_director_recovery_retire_old_preparations": (
                lambda projects: events.append(("retire", projects))
            ),
        }
        _launch_functions({"_checkpoint_director_preparation"}, namespace)
        result = namespace["_checkpoint_director_preparation"](
            request_id,
            str(self.root),
            status="completed",
            phase="structure_completed",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual([event[0] for event in events], [
            "checkpoint", "write", "checkpoint", "projects", "retire",
        ])

    def test_preparation_endpoint_issues_id_before_long_music_request(self):
        captured = {}
        project_access_checks = []
        admission_checks = []

        async def body_json():
            return {
                "description": "private concept",
                "reference_image_path": "reference.png",
                "workspace": "project-a",
            }

        def register(_request, body, workspace, project_dir):
            captured.update({
                "body": dict(body),
                "workspace": workspace,
                "project_dir": project_dir,
            })
            return "d" * 32, {"status": "running", "phase": "registered"}

        namespace = {
            "Request": object,
            "_get_active_workspace": lambda: "default",
            "_resolve_authorized_request_media": (
                lambda _request, path, workspace: f"/{workspace}/{path}"
            ),
            "_require_project_access": (
                lambda _request, workspace, *, permission: (
                    project_access_checks.append((workspace, permission))
                    or f"/projects/{workspace}"
                )
            ),
            "_require_remote_visible_models": (
                lambda _request, models: admission_checks.append(
                    ("visibility", list(models))
                )
            ),
            "wgp": types.SimpleNamespace(
                get_model_def=lambda model: (
                    {"name": "ACE-Step"}
                    if model == "ace_step_v1_5_xl_sft_lm_4b" else None
                ),
            ),
            "_require_model_recipe_terms": (
                lambda models: admission_checks.append(("terms", list(models)))
            ),
            "_register_director_preparation": register,
        }
        _launch_functions({"director_preparation_start"}, namespace)
        response = asyncio.run(namespace["director_preparation_start"](
            types.SimpleNamespace(json=body_json),
        ))
        self.assertEqual(response["director_request_id"], "d" * 32)
        self.assertEqual(response["actions"], ["generate_music"])
        self.assertNotIn("description", response)
        self.assertEqual(
            project_access_checks,
            [("project-a", "project.generate")],
        )
        self.assertEqual(admission_checks, [
            ("visibility", ["ace_step_v1_5_xl_sft_lm_4b"]),
            ("terms", ["ace_step_v1_5_xl_sft_lm_4b"]),
        ])
        self.assertEqual(
            captured["body"]["image_paths"],
            ["/project-a/reference.png"],
        )
        self.assertNotIn("reference_image_path", captured["body"])

    def test_child_slot_intent_is_checkpointed_before_submission(self):
        pid = "checkpoint-slot"
        director._pipelines[pid] = {
            "id": pid,
            "status": "running",
            "_recovery": {"children": {}},
        }
        unit = {"kind": "image_keyframe", "variant": 2, "index": 3}
        entry = {"job_id": "child", "state": "submitted"}
        with patch.object(director, "_require_pipeline_checkpoint") as commit:
            director._checkpoint_child_entry(
                pid, unit, entry, boundary="image-keyframe-2-3",
            )
        token = director._child_unit_token(unit)
        self.assertEqual(
            director._pipelines[pid]["_recovery"]["children"][token],
            entry,
        )
        commit.assert_called_once_with(pid, "image-keyframe-2-3")



class DirectorSceneAssemblyTests(unittest.TestCase):
    def setUp(self):
        from services.queue_recovery_runtime import artifact_descriptor, recovery_unit_id, validate_artifact_descriptor
        from services.queue_recovery_runtime import ensure_recovery_staging_directory, promote_recovery_staged_artifact, QueueRecoveryRuntimeError, sha256_file
        from services.queue_recovery import QueueRecoveryJournal
        from services.queue_recovery_adapter import QueueRecoveryCoordinator, owner_principal_digest, project_instance_digest
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan = {"clip_count": 3, "segment_source_indices": [0, 0, 1],
            "clip_frames": [27, 27, 27], "clip_published_frames": [24, 24, 24],
            "clip_trim_tail_frames": [3, 3, 3],
            "segment_models": [{"discard_frames": 0}, {"discard_frames": 17}, {"discard_frames": 0}]}
        self.job = {"id": "a" * 32, "status": "running", "params": {
            "_director_pipeline_id": "pipeline", "_h3_longform": self.plan,
            "multi_clip_audio_start_sec": 2.013}, "recovery_cursor": {"completed_units": []}}
        self.journal = QueueRecoveryJournal(self.root / "queue.jsonl")
        self.coordinator = QueueRecoveryCoordinator(self.journal)
        self.coordinator.register_job(self.job, owner_digest=owner_principal_digest(b"scene-test-secret-long", "owner"),
            project_digest=project_instance_digest(b"scene-test-secret-long", "b" * 32),
            request_manifest={"kind": "scene-test"})
        def checkpoint(job, **updates):
            candidate = {**job, **updates}
            self.coordinator.prospective_transition(types.SimpleNamespace(jobs=(candidate,), tombstones=(), global_state=None))
            job.update(updates)
            return True
        self.calls = []
        def concat(paths, output, audio, **kwargs):
            self.calls.append((list(paths), audio, dict(kwargs)))
            Path(output).write_bytes(b"assembled")
            return True
        self.namespace = _launch_functions({"_assemble_director_h3_scenes", "_h3_true_peak_policy_identity",
            "_queue_recovery_checkpoint_unit", "_queue_recovery_unit_matches", "_h3_dependency_closed_recovery_units",
            "_director_recovery_scene_map", "_director_recovery_verified_child", "_director_recovery_validate_child",
            "_queue_recovery_completed_h3_graph", "_queue_recovery_reconcile_cursor"}, {
            "os": os, "uuid": uuid, "copy": copy, "json": json, "hmac": hmac,
            "_queue_recovery_reconcile_orphan_delivery": lambda *_args: None,
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            "recovery_unit_id": recovery_unit_id, "_recovery_artifact_descriptor": artifact_descriptor,
            "validate_artifact_descriptor": validate_artifact_descriptor,
            "ensure_recovery_staging_directory": ensure_recovery_staging_directory,
            "promote_recovery_staged_artifact": promote_recovery_staged_artifact,
            "_queue_recovery_units": lambda job: job["recovery_cursor"]["completed_units"],
            "_queue_recovery_checkpoint": checkpoint, "_recovery_sha256_file": sha256_file,
            "wgp": types.SimpleNamespace(concatenate_multi_clip_videos=concat),
            "is_cancel_requested": lambda job: job.get("cancel_requested", False),
        })
        self.policy = self.namespace["_h3_true_peak_policy_identity"]()
        self.namespace["_enforce_deferred_h3_final_audio"] = lambda *args, **kwargs: {**self.policy, "verified": True}
        self.cancel_after_sidecar = False
        self.published = []
        for index in range(3):
            name = f"child-{index}.mp4"
            (self.root / name).write_bytes(f"child-{index}".encode())
            settings = {"generated_frames": 27, "published_frames": 24,
                "trim_tail_frames": 3, "discard_prefix_frames": self.plan["segment_models"][index]["discard_frames"]}
            unit_id = recovery_unit_id(self.job["id"], "h3_segment", variant=0, index=index, settings=settings)
            self.sidecars([name], recovery_units={name: {"unit_id": unit_id, "kind": "h3_segment", "variant": 0, "index": index, "settings": settings, "dependencies": []}})
            self.namespace["_queue_recovery_checkpoint_unit"](self.job, kind="h3_segment", variant=0, index=index,
                project_dir=str(self.root), artifact_names=[name], settings=settings)

    def tearDown(self):
        self.temporary.cleanup()

    def sidecars(self, names, *, recovery_units, media_paths=None, **kwargs):
        for name in names:
            producer = recovery_units[name]
            data = Path((media_paths or {}).get(name) or self.root / name).read_bytes()
            metadata = {"job_id": self.job["id"], "workspace": self.job.get("workspace"),
                "producer_unit_id": producer["unit_id"], "producer_unit_kind": producer["kind"],
                "producer_unit_index": producer["index"], "producer_unit_variant": producer["variant"],
                "producer_unit_settings": producer.get("settings", {}),
                "producer_unit_dependencies": producer.get("dependencies", []),
                "producer_unit_artifact_names": names, "producer_media_size": len(data),
                "producer_media_sha256": hashlib.sha256(data).hexdigest(),
                "artifact_class": "final" if producer["kind"] == "h3_concat" else "component",
                "producer_artifact_class": "final" if producer["kind"] == "h3_concat" else "component",
                "params": {"multi_clip_info": {"output_index": 0, "output_total": 1}}, "output_filename": name}
            if producer.get("attestation"):
                metadata["producer_unit_attestation"] = producer["attestation"]
            (self.root / (Path(name).stem + ".meta.json")).write_text(json.dumps(metadata), encoding="utf-8")
            if media_paths:
                self.assertFalse((self.root / name).exists())
                self.assertTrue(Path(media_paths[name]).exists())
        if self.cancel_after_sidecar:
            self.job["cancel_requested"] = True

    def cancel(self):
        if self.job.get("cancel_requested"):
            raise InterruptedError("cancelled")

    def run_assembly(self):
        return self.namespace["_assemble_director_h3_scenes"](self.job, self.plan, str(self.root), variant=0,
            write_sidecars=self.sidecars, cancel_check=self.cancel,
            publish_scene=lambda index, name: self.published.append((index, name)))

    def synthetic_probe(self, path, *, expected_frames, **kwargs):
        data = Path(path).read_bytes()
        return {"validation": "valid", "fps": 24, "frame_count": expected_frames,
                "artifact_size_bytes": len(data), "artifact_sha256": "sha256:" + hashlib.sha256(data).hexdigest()}

    def test_recover_sealed_scenes_without_concat_and_reject_swapped_ownership(self):
        with patch("services.h3_output_integrity.probe_h3_output", side_effect=self.synthetic_probe):
            self.run_assembly()
            first = list(self.published)
            self.published.clear()
            self.run_assembly()
        self.assertEqual(self.published, first)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][2]["clip_start_frames"], [0, 17])
        self.assertEqual(self.calls[1][2]["audio_start_sec"], 4.013)
        self.job.update(status="completed", clip_output_files={str(i): f"child-{i}.mp4" for i in range(3)},
            h3_scene_output_files={str(index): name for index, name in first})
        evidence = self.namespace["_director_recovery_verified_child"](self.job, str(self.root))
        self.assertIsNotNone(evidence)
        self.assertIsNotNone(self.namespace["_director_recovery_validate_child"](str(self.root), evidence))
        restored = self.journal.recover().jobs[self.job["id"]]
        self.assertEqual(len([u for u in restored["recovery_cursor"]["completed_units"] if u["kind"] == "h3_scene"]), 2)
        for mapping in ({"0": first[1][1], "1": first[0][1]}, {"0": "child-0.mp4"},
                        {"01": first[1][1]}, {0: first[0][1], "0": first[0][1]}):
            altered = {**evidence, "h3_scene_output_files": mapping}
            with self.subTest(mapping=mapping):
                self.assertIsNone(self.namespace["_director_recovery_validate_child"](str(self.root), altered))
        (self.root / "child-0.mp4").write_bytes(b"changed")
        self.assertIsNone(self.namespace["_director_recovery_validate_child"](str(self.root), evidence))

    def test_cancel_after_sidecar_never_promotes_or_checkpoints_scene(self):
        self.cancel_after_sidecar = True
        with patch("services.h3_output_integrity.probe_h3_output", side_effect=self.synthetic_probe), self.assertRaises(InterruptedError):
            self.run_assembly()
        self.assertFalse(list(self.root.glob("director_scene_*.mp4")))
        self.assertFalse(self.published)
        self.assertEqual(len(self.job["recovery_cursor"]["completed_units"]), 3)

    def test_scene_receipt_recovers_after_promotion_before_journal(self):
        with patch("services.h3_output_integrity.probe_h3_output", side_effect=self.synthetic_probe):
            self.run_assembly()
        expected = {str(index): name for index, name in self.published}
        self.job["recovery_cursor"]["completed_units"] = [u for u in self.job["recovery_cursor"]["completed_units"]
                                                        if u["kind"] == "h3_segment"]
        self.namespace["_queue_recovery_reconcile_cursor"](self.job, str(self.root))
        self.assertEqual(self.job["h3_scene_output_files"], expected)
        self.assertEqual(len([u for u in self.job["recovery_cursor"]["completed_units"] if u["kind"] == "h3_scene"]), 2)
        self.assertEqual(self.job["output_files"], [])

    def test_completed_film_graph_preserves_sibling_scene_units_without_extra_finals(self):
        with patch("services.h3_output_integrity.probe_h3_output", side_effect=self.synthetic_probe):
            self.run_assembly()
        parents = self.job["recovery_cursor"]["completed_units"][:3]
        settings = {"component_hashes": [u["artifacts"][0]["sha256"] for u in parents],
                    "clip_start_frames": [0, 17, 0], "clip_tail_frames": [3, 3, 3], "source_prefix": None,
                    "h3_audio_true_peak_policy": self.policy}
        dependencies = [unit["unit_id"] for unit in parents]
        unit_id = self.namespace["recovery_unit_id"](self.job["id"], "h3_concat", variant=0, index=0,
                                                   dependencies=dependencies, settings=settings)
        (self.root / "film.mp4").write_bytes(b"film")
        self.sidecars(["film.mp4"], recovery_units={"film.mp4": {"unit_id": unit_id, "kind": "h3_concat",
            "index": 0, "variant": 0, "settings": settings, "dependencies": dependencies}})
        self.namespace["_queue_recovery_checkpoint_unit"](self.job, kind="h3_concat", variant=0, index=0,
            project_dir=str(self.root), artifact_names=["film.mp4"], dependencies=dependencies, settings=settings,
            attestation={"h3_audio_true_peak": {**self.policy, "verified": True}})
        self.job.update(status="completed", h3_scene_output_files={str(index): name for index, name in self.published})
        graph = self.namespace["_queue_recovery_completed_h3_graph"](self.job, str(self.root))
        self.assertIsNotNone(graph)
        self.assertEqual(graph["output_files"], ["film.mp4"])
        self.assertEqual(len(graph["completed_units"]), 6)
        self.job["recovery_cursor"]["completed_units"] = graph["completed_units"]
        self.assertIsNotNone(self.namespace["_director_recovery_verified_child"](self.job, str(self.root)))

    def test_geometry_clock_and_receipt_are_bound_and_future_replan_is_stable(self):
        from services.director_h3_scene_assembly import director_h3_scene_specs, scene_dependency_settings_match, scene_receipt_matches
        units = {unit["index"]: unit for unit in self.job["recovery_cursor"]["completed_units"]}
        specs = director_h3_scene_specs(self.plan, units, audio_start_sec=2.013)
        self.assertEqual([s["settings"]["published_frames"] for s in specs], [48, 24])
        changed = copy.deepcopy(self.plan); changed["clip_frames"][2] = 28; changed["clip_trim_tail_frames"][2] = 4
        changed_units = copy.deepcopy(units); changed_units[2]["settings"].update(generated_frames=28, trim_tail_frames=4)
        later = director_h3_scene_specs(changed, changed_units, audio_start_sec=2.013)
        self.assertEqual(specs[0], later[0])
        for field, value in (("published_start_frame", True), ("assembly_version", True),
                             ("audio_start_sec", float("nan")), ("component_hashes", ["x"])):
            settings = {**specs[0]["settings"], field: value}
            self.assertFalse(scene_dependency_settings_match(settings, [units[0], units[1]]))
        with patch("services.h3_output_integrity.probe_h3_output", side_effect=self.synthetic_probe):
            self.run_assembly()
        scene = self.job["recovery_cursor"]["completed_units"][-1]
        self.assertTrue(scene_receipt_matches(scene))
        for field, value in (("frame_count", 25), ("fps", 30), ("artifact_sha256", "sha256:" + "0" * 64)):
            bad = copy.deepcopy(scene); bad["attestation"]["media"][field] = value
            self.assertFalse(scene_receipt_matches(bad))
        (self.root / "child-2.mp4").write_bytes(b"future child changed")
        self.assertIsNotNone(self.namespace["_queue_recovery_unit_matches"](self.job, kind="h3_scene",
            variant=0, index=0, project_dir=str(self.root), quarantine_invalid=False))
        (self.root / "child-2.mp4").write_bytes(b"child-2")
        bad = copy.deepcopy(scene); bad["settings"]["audio_start_sec"] += 1
        self.job["recovery_cursor"]["completed_units"][-1] = bad
        self.assertIsNone(self.namespace["_queue_recovery_unit_matches"](self.job, kind="h3_scene",
            variant=0, index=1, project_dir=str(self.root), quarantine_invalid=False))

    @unittest.skipUnless(__import__("shutil").which("ffmpeg") and __import__("shutil").which("ffprobe"), "ffmpeg required")
    def test_cpu_media_has_exact_new_frames_and_original_song_window(self):
        import subprocess
        import wave
        import math
        import struct
        from tests.test_h3_native_boundary_conditioning import _load_functions
        from services.h3_audio_safety import enforce_true_peak_safety
        from services.h3_output_integrity import probe_h3_output
        native = _load_functions(APP / "wgp.py", {"PostDecodeStageError", "concatenate_multi_clip_videos"}, {"os": os})
        self.namespace["wgp"].concatenate_multi_clip_videos = native["concatenate_multi_clip_videos"]
        self.namespace["_enforce_deferred_h3_final_audio"] = lambda job, path, **kwargs: enforce_true_peak_safety(path)
        for index, frames in enumerate((24, 41, 24)):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=blue:s=64x64:r=24:d={frames / 24}",
                "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=32000:duration={frames / 24}",
                "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "32000", "-ac", "2",
                str(self.root / f"child-{index}.mp4")], check=True, capture_output=True, timeout=60)
            unit = self.job["recovery_cursor"]["completed_units"][index]
            unit["artifacts"] = [self.namespace["_recovery_artifact_descriptor"](self.root, basename=f"child-{index}.mp4",
                sidecar_basename=f"child-{index}.meta.json", producer_unit_id=unit["unit_id"])]
        source = self.root / "song.wav"
        with wave.open(str(source), "wb") as handle:
            handle.setparams((1, 2, 32000, 0, "NONE", "not compressed"))
            handle.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * (440 if i < 128000 else 880) * i / 32000)))
                for i in range(32000 * 8)))
        self.job["params"]["audio_source"] = str(source)
        self.namespace["_load_h3_mapping_manifest_inputs"] = lambda job: (str(self.root), [{"field": "audio_source:0",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}])
        self.run_assembly()
        for (index, filename), frames, frequency in zip(self.published, (48, 24), (440, 880)):
            output = self.root / filename
            self.assertEqual(probe_h3_output(output, expected_fps=24, expected_frames=frames, require_audio=True)["validation"], "valid")
            audio = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-ss", "0.1", "-t", "0.2", "-f", "s16le", "-ac", "1", "-ar", "32000", "-"],
                check=True, capture_output=True, timeout=60).stdout
            samples = struct.unpack("<" + "h" * (len(audio) // 2), audio)
            crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
            self.assertAlmostEqual(crossings / (len(samples) / 32000), frequency, delta=10)


class DirectorH3RerunSourceTests(unittest.TestCase):
    """Real manifest/media seals with model-free original producer graphs."""

    def setUp(self):
        from tests.test_director_h3_invariants import TestDirectorH3Invariants
        from services.queue_recovery_runtime import (
            QueueRecoveryRuntimeError, atomic_write_request_manifest, load_request_manifest,
            validate_manifest_inputs, artifact_descriptor, validate_artifact_descriptor, recovery_unit_id,
        )
        from services.queue_recovery_adapter import owner_principal_digest
        self.fixture = TestDirectorH3Invariants()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = Path(self.fixture.temp_dir.name)
        self.state, self.prepared = self.fixture._rerun_source_state()
        self.original = self.state["_params_snapshot"]["_h3_longform"]
        self.job_id = next(iter(self.state["recovery"]["children"].values()))["job_id"]
        self.owner = owner_principal_digest(b"rerun-test-session-secret", "owner")
        self.project = "b" * 64
        self.error = QueueRecoveryRuntimeError
        self.namespace = _launch_functions({"_director_recovery_h3_rerun_source",
            "_queue_recovery_completed_h3_graph", "_queue_recovery_unit_matches",
            "_h3_dependency_closed_recovery_units", "_director_recovery_scene_map",
            "_h3_true_peak_policy_identity"}, {
            "os": os, "copy": copy, "hmac": hmac,
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            "owner_principal_digest": owner_principal_digest,
            "_session_secret": lambda: b"rerun-test-session-secret",
            "_existing_workspace_dir": lambda workspace: str(self.root),
            "_queue_recovery_existing_project_identity": lambda root: self.project,
            "load_request_manifest": load_request_manifest,
            "validate_manifest_inputs": validate_manifest_inputs,
            "_queue_recovery_manifest_validator": lambda *args, **kwargs: True,
            "_recovery_artifact_descriptor": artifact_descriptor,
            "validate_artifact_descriptor": validate_artifact_descriptor,
            "recovery_unit_id": recovery_unit_id,
            "_queue_recovery_units": lambda job: job["recovery_cursor"]["completed_units"],
        })
        units = []
        for index, shot in enumerate(self.original["shot_plan"]["shots"]):
            settings = {"generated_frames": self.original["clip_frames"][index],
                "published_frames": self.original["clip_published_frames"][index],
                "trim_tail_frames": self.original["clip_trim_tail_frames"][index],
                "discard_prefix_frames": self.original["segment_models"][index]["discard_frames"],
                "native_boundary_conditioning": True,
                "semantic_execution": {"version": 2,
                    **{key: shot[key] for key in ("semantic_shot_index", "physical_segment_index",
                        "physical_segment_count", "predecessor_segment_index", "execution_cursor_frame")},
                    "start_frame": shot["execution_slice"]["start_frame"],
                    "end_frame_exclusive": shot["execution_slice"]["end_frame_exclusive"]}}
            dependencies = [units[-1]["unit_id"]] if units else []
            if units:
                settings["predecessor_artifact_hashes"] = [units[-1]["artifacts"][0]["sha256"]]
                settings["predecessor_continuation_sha256"] = ""
            unit_id = recovery_unit_id(self.job_id, "h3_segment", variant=3, index=index,
                settings=settings, dependencies=dependencies)
            producer = {"unit_id": unit_id, "kind": "h3_segment", "variant": 3, "index": index,
                "settings": settings, "dependencies": dependencies, "state": "completed",
                "continuation": {"dependency": unit_id, "mode": "prompt_only"}}
            self.write_unit(producer, f"original-{index}.mp4")
            units.append(producer)
        self.policy = self.namespace["_h3_true_peak_policy_identity"]()
        settings = {"component_hashes": [u["artifacts"][0]["sha256"] for u in units],
            "clip_start_frames": [u["settings"]["discard_prefix_frames"] for u in units],
            "clip_tail_frames": self.original["clip_trim_tail_frames"], "source_prefix": None,
            "h3_audio_true_peak_policy": self.policy}
        dependencies = [unit["unit_id"] for unit in units]
        unit_id = recovery_unit_id(self.job_id, "h3_concat", variant=3, index=0,
            settings=settings, dependencies=dependencies)
        final = {"unit_id": unit_id, "kind": "h3_concat", "variant": 3, "index": 0,
            "settings": settings, "dependencies": dependencies, "state": "completed",
            "attestation": {"h3_audio_true_peak": {**self.policy, "verified": True}}}
        self.write_unit(final, "film.mp4")
        params = {"_director_pipeline_id": self.state["pipeline_id"],
                  "_h3_longform": self.original, "multi_clip_audio_start_sec": 2.013}
        pointer = atomic_write_request_manifest(self.root, job_id=self.job_id, params=params, inputs=[])
        self.snapshot = {"id": self.job_id, "status": "completed", "kind": "director_child",
            "workspace": "default", "owner_principal": self.owner, "project_instance": self.project,
            "request_manifest": pointer, "recovery_cursor": {"completed_units": [*units, final]}}
        self.namespace["_queue_recovery_coordinator"] = types.SimpleNamespace(
            read_only_snapshot=lambda: ({self.job_id: copy.deepcopy(self.snapshot)}, frozenset()))

    def write_unit(self, unit, name):
        from services.queue_recovery_runtime import artifact_descriptor
        data = ("sealed:" + name).encode()
        (self.root / name).write_bytes(data)
        role = "final" if unit["kind"] == "h3_concat" else "component"
        meta = {"job_id": self.job_id, "workspace": "default", "output_filename": name,
            "producer_unit_id": unit["unit_id"], "producer_unit_kind": unit["kind"],
            "producer_unit_index": unit["index"], "producer_unit_variant": unit["variant"],
            "producer_unit_settings": unit["settings"], "producer_unit_dependencies": unit["dependencies"],
            "producer_unit_artifact_names": [name], "producer_media_size": len(data),
            "producer_media_sha256": hashlib.sha256(data).hexdigest(),
            "artifact_class": role, "producer_artifact_class": role,
            "params": {"multi_clip_info": {"output_index": 0, "output_total": 1}}}
        if "continuation" in unit:
            meta["producer_unit_continuation"] = unit["continuation"]
        if "attestation" in unit:
            meta["producer_unit_attestation"] = unit["attestation"]
        sidecar = Path(name).stem + ".meta.json"
        (self.root / sidecar).write_text(json.dumps(meta))
        unit["artifacts"] = [artifact_descriptor(self.root, basename=name,
            sidecar_basename=sidecar, producer_unit_id=unit["unit_id"])]

    def resolve(self, **kwargs):
        return self.namespace["_director_recovery_h3_rerun_source"](
            self.state, self.prepared, **({"session_id": "owner", "project_dir": str(self.root)} | kwargs))

    def test_verified_original_variant_and_scene_local_geometry_are_provenance_only(self):
        before = copy.deepcopy(self.snapshot)
        result = self.resolve()
        self.assertEqual(result["original_variant"], 3)
        self.assertEqual(result["physical_indices"], [1, 2])
        self.assertEqual(result["predecessor_index"], 0)
        self.assertEqual(result["predecessor_path"], str(self.root / "original-0.mp4"))
        self.assertEqual(result["predecessor_artifact"]["producer_unit_id"], result["predecessor_unit_id"])
        self.assertNotIn("completed_units", result)
        self.assertEqual(self.snapshot, before)

    def test_opening_scene_has_no_predecessor_but_still_requires_original_authority(self):
        self.prepared = director._prepare_director_h3_scene_rerun({}, committed=self.original,
            scene_index=0, audio_origin_sec=2.013)
        result = self.resolve()
        self.assertIsNone(result["predecessor_path"])
        self.assertIsNone(result["predecessor_artifact"])
        self.assertIsNone(result["boundary"])
        self.snapshot["owner_principal"] = "different-owner"
        with self.assertRaises(self.error): self.resolve()

    def test_recreated_project_changed_owner_and_path_are_rejected(self):
        for field, value in (("owner_principal", "other-owner"), ("project_instance", "other-project"),
            ("workspace", "other"), ("kind", "studio_generation"), ("status", "failed"),
            ("cancel_requested", True)):
            old = copy.deepcopy(self.snapshot)
            self.snapshot[field] = value
            with self.subTest(field=field), self.assertRaises(self.error): self.resolve()
            self.snapshot = old
        with self.assertRaises(self.error): self.resolve(session_id="")
        with self.assertRaises(self.error): self.resolve(project_dir=str(self.root / "other"))

    def test_newly_sealed_wrong_song_origin_cannot_self_authorize(self):
        self.prepared = director._prepare_director_h3_scene_rerun({}, committed=self.original,
            scene_index=1, audio_origin_sec=2.0)
        with self.assertRaises(self.error): self.resolve()

    def test_missing_manifest_changed_media_and_changed_sidecar_fail_without_mutation(self):
        original = copy.deepcopy(self.snapshot)
        for name in ("original-0.mp4", "original-2.meta.json"):
            path = self.root / name; data = path.read_bytes()
            path.write_bytes(data + b"changed")
            with self.subTest(path=name), self.assertRaises(self.error): self.resolve()
            self.assertEqual(self.snapshot, original)
            path.write_bytes(data)
        manifest = self.root / self.snapshot["request_manifest"]["path"]
        manifest.unlink()
        with self.assertRaises(self.error): self.resolve()
        self.assertEqual(self.snapshot, original)

    def test_original_input_validator_is_mandatory_even_for_completed_graph(self):
        from services.queue_recovery_runtime import atomic_write_request_manifest
        params = {"_director_pipeline_id": self.state["pipeline_id"],
                  "_h3_longform": self.original, "multi_clip_audio_start_sec": 2.013}
        self.snapshot["request_manifest"] = atomic_write_request_manifest(self.root,
            job_id=self.job_id, params=params, inputs=[{"field": "original-input"}])
        self.namespace["_queue_recovery_manifest_validator"] = lambda *args, **kwargs: False
        with self.assertRaises(self.error): self.resolve()

    def test_missing_canonical_snapshot_cannot_fall_back_to_live_job_or_saved_filenames(self):
        self.namespace["_queue_recovery_coordinator"] = types.SimpleNamespace(
            read_only_snapshot=lambda: ({}, frozenset({self.job_id})))
        director._jobs[self.job_id] = copy.deepcopy(self.snapshot)
        with self.assertRaises(self.error): self.resolve()

if __name__ == "__main__":
    unittest.main()
