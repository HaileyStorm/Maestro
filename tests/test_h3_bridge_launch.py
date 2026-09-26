"""CPU checks for the project-bound H3 Bridge submission boundary."""

from __future__ import annotations

import ast
import asyncio
import copy
import hmac
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from contextlib import nullcontext
from dataclasses import replace
from fractions import Fraction
from unittest.mock import patch

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.queue_recovery_runtime import sha256_file
from services.h3_bridge_plan import plan_h3_bridge
from services.h3_bridge_media import SourceProbe


def load_launch_functions(namespace: dict, *names: str) -> None:
    tree = ast.parse((ROOT / "app/launch.py").read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "launch.py", "exec"), namespace)


class H3BridgeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.a = self.root / "a.mp4"
        self.b = self.root / "b.mp4"
        self.a.write_bytes(b"A video bytes")
        self.b.write_bytes(b"B video bytes")
        self.registered = []
        self.ns = {
            "Request": object,
            "HTTPException": HTTPException,
            "asyncio": asyncio,
            "os": os,
            "copy": copy,
            "hmac": hmac,
            "time": time,
            "_request_project_workspace": lambda _request, workspace: workspace,
            "_require_project_access": lambda _request, _workspace, **_kwargs: str(self.root),
            "_require_remote_visible_models": lambda *_args: None,
            "_require_h3_legal_execution": lambda *_args: None,
            "_require_model_recipe_terms": lambda *_args: None,
            "_require_authorized_output": self._authorized_output,
            "_output_revision": lambda _path, _root, name: f"rev-{name}",
            "_recovery_sha256_file": sha256_file,
            "_inherit_media_access_policy": lambda *_args: {
                "private": True, "explicit": True,
            },
            "_http_output_policy_from_request": lambda body, **_kwargs: {
                "private": body["private_output"],
                "explicit": body["explicit_output"],
            },
            "_new_generation_job_id": lambda: "bridge-job",
            "_queue_recovery_register_and_publish": lambda job, **kwargs: self.registered.append((job, kwargs)),
            "_run_h3_bridge_generation": object(),
            "wgp": types.SimpleNamespace(get_default_settings=lambda _model: {
                "resolution": "1344x768", "num_inference_steps": 28,
                "custom_settings": {"h3_attention_engine": "sol_attn"},
            }),
        }
        load_launch_functions(
            self.ns, "_reject_client_h3_internal_state", "h3_bridge_endpoint",
        )

    def _authorized_output(self, _request, _workspace, name):
        if name not in {"a.mp4", "b.mp4"}:
            raise HTTPException(404, "Output file not found")
        return str(self.root), str(self.root / name), {}

    @staticmethod
    def _probe(path):
        return SourceProbe(
            sha256="sha256:" + sha256_file(path)[1],
            fps=Fraction(24, 1), frame_count=100, frames_24fps=100,
            width=608, height=352, has_audio=False,
            audio_channels=None, audio_duration=None,
        )

    def request(self, **changes):
        body = {
            "workspace": "project-a",
            "model_type": "minimax_h3_ref2va",
            "clip_a": {"name": "a.mp4", "revision": "rev-a.mp4"},
            "clip_b": {"name": "b.mp4", "revision": "rev-b.mp4"},
            "prompt": "A contentious fictional argument becomes a dance.",
            "generated_frames": 124,
        }
        body.update(changes)

        async def read():
            return body

        return types.SimpleNamespace(
            json=read,
            state=types.SimpleNamespace(
                maestro_session_id="owner-session", maestro_remote=True,
            ),
        )

    def test_authorized_sources_are_sealed_and_only_server_selects_guides(self):
        with patch("services.h3_bridge_media.probe_source", side_effect=self._probe):
            response = asyncio.run(self.ns["h3_bridge_endpoint"](self.request()))
        self.assertEqual(response["status"], "queued")
        self.assertEqual(len(self.registered), 1)
        job, options = self.registered[0]
        self.assertEqual(options["recovery_kind"], "studio_h3_bridge")
        self.assertEqual(job["workspace"], "project-a")
        self.assertTrue(job["private"])
        self.assertTrue(job["explicit"])
        params = job["params"]
        self.assertEqual(params["video_prompt_type"], "V+-")
        self.assertEqual(params["audio_prompt_type"], "")
        self.assertIs(params["_defer_output_publication"], True)
        self.assertEqual(params["_h3_bridge_source_names"], ["a.mp4", "b.mp4"])
        self.assertEqual(params["_h3_bridge_source_revisions"], ["rev-a.mp4", "rev-b.mp4"])
        self.assertEqual(params["_h3_bridge_plan"]["sources"]["a_tail"]["start_frame"], 44)
        self.assertEqual(params["_h3_bridge_plan"]["sources"]["b_head"]["end_frame_exclusive"], 56)
        self.assertEqual(
            params["_h3_bridge_plan"]["sources"]["a_tail"]["sha256"],
            "sha256:" + sha256_file(self.a)[1],
        )
        self.assertEqual(
            params["_h3_bridge_plan"]["sources"]["b_head"]["sha256"],
            "sha256:" + sha256_file(self.b)[1],
        )
        self.assertEqual(
            params["custom_settings"]["_h3_bridge_guides"],
            [{"slot": 1, "frame_idx": 0, "frames": 22},
             {"slot": 2, "frame_idx": -22, "frames": 22}],
        )
        self.assertEqual(params["prompt"], "A contentious fictional argument becomes a dance.")

    def test_changed_source_and_invalid_cuts_never_queue(self):
        cases = (
            {"clip_a": {"name": "a.mp4", "revision": "old"}},
            {"clip_b": {"name": "b.mp4", "revision": "rev-b.mp4", "start_frame": 45}},
            {"clip_a": {"name": "a.mp4", "revision": "rev-a.mp4", "start_frame": 12}},
            {"clip_a": {"name": "../a.mp4", "revision": "rev-a.mp4"}},
            {"generated_frames": 123},
        )
        with patch("services.h3_bridge_media.probe_source", side_effect=self._probe):
            for changes in cases:
                with self.subTest(changes=changes), self.assertRaises(HTTPException):
                    asyncio.run(self.ns["h3_bridge_endpoint"](self.request(**changes)))
        self.assertEqual(self.registered, [])

    def test_client_cannot_forge_nested_guide_authority(self):
        with patch("services.h3_bridge_media.probe_source", side_effect=self._probe):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(self.ns["h3_bridge_endpoint"](self.request(settings={
                    "custom_settings": {"_h3_bridge_guides": []},
                })))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.registered, [])

    def test_probe_runs_off_the_api_loop_and_unsupported_dimensions_do_not_queue(self):
        loop_thread = threading.get_ident()
        probe_threads = []

        def probe(path):
            probe_threads.append(threading.get_ident())
            result = self._probe(path)
            if Path(path).name == "b.mp4":
                result = replace(result, width=5000)
            return result

        with patch("services.h3_bridge_media.probe_source", side_effect=probe):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(self.ns["h3_bridge_endpoint"](self.request()))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(len(probe_threads), 2)
        self.assertTrue(all(thread != loop_thread for thread in probe_threads))
        self.assertEqual(self.registered, [])


class H3BridgeWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.a = self.root / "a.mp4"
        self.b = self.root / "b.mp4"
        self.a.write_bytes(b"source-a")
        self.b.write_bytes(b"source-b")
        digest_a = "sha256:" + sha256_file(self.a)[1]
        digest_b = "sha256:" + sha256_file(self.b)[1]
        self.plan = plan_h3_bridge(
            {"sha256": digest_a, "start_frame": 44, "end_frame_exclusive": 100},
            {"sha256": digest_b, "start_frame": 0, "end_frame_exclusive": 56},
            generated_frames=124, hidden_head_frames=22, hidden_tail_frames=22,
        )
        self.job = {
            "id": "a" * 32, "status": "queued", "workspace": "project-a",
            "out_dir": str(self.root), "output_files": [],
            "private": True, "explicit": False,
            "access_policy": {"private": True, "explicit": False,
                              "owner_session_id": "owner"},
            "params": {
                "_h3_bridge_plan": self.plan,
                "_h3_bridge_clip_a": str(self.a),
                "_h3_bridge_clip_b": str(self.b),
                "_h3_bridge_source_names": ["a.mp4", "b.mp4"],
                "_h3_bridge_source_revisions": ["rev-a.mp4", "rev-b.mp4"],
            },
        }
        self.generated = False
        self.ns = {
            "_jobs": {self.job["id"]: self.job},
            "os": os, "hmac": hmac, "json": json, "logging": logging,
            "copy": copy,
            "time": time, "uuid": uuid,
            "_output_revision": lambda _path, _dir, name: f"rev-{name}",
            "_recovery_sha256_file": sha256_file,
            "is_cancel_requested": lambda _job: False,
            "_run_generation": self._generate,
            "register_abort_state": lambda *_args: True,
            "unregister_abort_state": lambda *_args: None,
            "update_job": lambda *_args, **_kwargs: True,
            "finish_job": self._finish,
            "record_job_outputs": lambda *_args, **_kwargs: None,
            "_active_gen_states": {},
            "_reserve_workspace_operations": lambda *_args: nullcontext(),
            "_output_lineage_mutation_guard": lambda *_args: nullcontext(),
            "_existing_workspace_dir": lambda _workspace: str(self.root),
            "_atomic_write_json": lambda path, value: Path(path).write_text(json.dumps(value)),
            "stamp_sidecar_policy": lambda value, policy, *, workspace: value.update({
                "workspace": workspace,
                "private": policy["private"],
                "explicit": policy["explicit"],
            }),
            "recovery_unit_id": lambda *_args, **_kwargs: "unit:v1:" + "a" * 64,
        }
        load_launch_functions(self.ns, "_run_h3_bridge_generation")

    def _generate(self, job_id, **_kwargs):
        self.generated = True
        self.assertEqual(self.job["_h3_bridge_project_out_dir"], str(self.root))
        path = Path(self.job["out_dir"]) / "generated.mp4"
        path.write_bytes(b"generated-transition")
        self.job["_internal_output_files"] = ["generated.mp4"]
        return True

    def _finish(self, job, status, **changes):
        job.update(changes)
        job["status"] = status
        return True

    @staticmethod
    def _prepare(_a, _b, _plan, temp_dir, **_kwargs):
        left = Path(temp_dir) / "left.mp4"
        right = Path(temp_dir) / "right.mp4"
        left.write_bytes(b"left")
        right.write_bytes(b"right")
        return left, right

    def _assemble(self, _a, _b, generated, _plan, staging, **_kwargs):
        self.assertEqual(Path(staging).parent.parent, self.root)
        self.assertNotEqual(Path(generated).parent, self.root)
        Path(staging).write_bytes(b"final-assembly")
        return {"frames": 200, "fps": 24}

    def test_final_publication_uses_path_free_sidecar_and_removes_transition(self):
        with patch("services.h3_bridge_media.prepare_guides", side_effect=self._prepare), patch(
            "services.h3_bridge_media.assemble_bridge", side_effect=self._assemble,
        ):
            self.ns["_run_h3_bridge_generation"](self.job["id"])
        self.assertTrue(self.generated)
        self.assertEqual(self.job["status"], "completed")
        final_name = self.job["output_files"][0]
        self.assertTrue((self.root / final_name).is_file())
        sidecar = json.loads((self.root / final_name.replace(".mp4", ".meta.json")).read_text())
        self.assertEqual(sidecar["h3_bridge"]["plan_sha256"], self.plan["plan_sha256"])
        self.assertEqual(sidecar["producer_media_sha256"], sha256_file(self.root / final_name)[1])
        self.assertNotIn("h3-bridge-", json.dumps(sidecar))
        self.assertFalse((self.root / "generated.mp4").exists())
        self.assertEqual(self.job["out_dir"], str(self.root))
        self.assertNotIn("_h3_bridge_project_out_dir", self.job)

    def test_source_change_during_assembly_prevents_final_publication(self):
        def mutate(*args, **kwargs):
            result = self._assemble(*args, **kwargs)
            self.a.write_bytes(b"modified-source-a")
            return result

        with patch("services.h3_bridge_media.prepare_guides", side_effect=self._prepare), patch(
            "services.h3_bridge_media.assemble_bridge", side_effect=mutate,
        ):
            self.ns["_run_h3_bridge_generation"](self.job["id"])
        self.assertEqual(self.job["status"], "failed")
        self.assertEqual(list(self.root.glob("*h3_bridge.mp4")), [])

    def test_complete_published_final_is_adopted_without_regeneration(self):
        with patch("services.h3_bridge_media.prepare_guides", side_effect=self._prepare), patch(
            "services.h3_bridge_media.assemble_bridge", side_effect=self._assemble,
        ):
            self.ns["_run_h3_bridge_generation"](self.job["id"])
        final_name = self.job["output_files"][0]
        self.assertTrue((self.root / final_name).is_file())
        self.generated = False
        self.job["status"] = "queued"
        self.job["output_files"] = []
        self.ns["_run_h3_bridge_generation"](self.job["id"])
        self.assertFalse(self.generated)
        self.assertEqual(self.job["status"], "completed")
        self.assertEqual(self.job["output_files"], [final_name])

    def test_incomplete_published_pair_is_preserved_without_regeneration(self):
        final = self.root / f"{self.job['id']}_h3_bridge.mp4"
        final.write_bytes(b"orphaned-final")
        self.ns["_run_h3_bridge_generation"](self.job["id"])
        self.assertFalse(self.generated)
        self.assertEqual(self.job["status"], "failed")
        self.assertTrue(final.is_file())


class H3BridgeQueueVisibilityTests(unittest.TestCase):
    def test_remote_owner_can_follow_staged_bridge_only_in_active_project(self):
        with tempfile.TemporaryDirectory() as root:
            job = {
                "kind": "studio_h3_bridge", "workspace": "project-a",
                "out_dir": str(Path(root) / "private-staging"),
                "_h3_bridge_project_out_dir": root,
            }
            active = {"owner-session": "project-a"}
            namespace = {
                "os": os, "hmac": hmac, "HTTPException": HTTPException,
                "QueueRecoveryAdapterError": ValueError,
                "_existing_workspace_dir": lambda _workspace: root,
                "_require_account_project_permission": lambda *_args: object(),
                "_remote_active_projects_lock": threading.RLock(),
                "_remote_active_projects": active,
            }
            load_launch_functions(namespace, "_recovered_job_remote_project_accessible")
            request = types.SimpleNamespace(state=types.SimpleNamespace(
                maestro_session_id="owner-session", maestro_remote=True,
            ))
            visible = namespace["_recovered_job_remote_project_accessible"]
            self.assertTrue(visible(job, request))
            active["owner-session"] = "other-project"
            self.assertFalse(visible(job, request))
            active["owner-session"] = "project-a"
            job["kind"] = "studio_generation"
            self.assertFalse(visible(job, request))


if __name__ == "__main__":
    unittest.main()
