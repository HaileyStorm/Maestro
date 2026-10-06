"""CPU route, replay and actual worker handoff checks; no model qualification."""
from __future__ import annotations

import ast
import asyncio
import copy
from contextlib import redirect_stderr, redirect_stdout
import gc
import io
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import traceback
import types
import unittest
from typing import Any, Mapping
from unittest.mock import Mock, patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services import h3_gallery_av_guide as av, upload_usage
from services.h3_oom_relief import H3OomReliefRetry
import test_h3_gallery_av_guide as media_fixtures
from test_h3_gallery_still_guide import (
    FakePreparationRequest, load_launch_functions, load_nested_launch_function,
)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg required")
class GalleryAVDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        media_fixtures.GalleryAVGuideTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.storage.cleanup()

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="h3-av-route-")
        self.addCleanup(directory.cleanup)
        self.out = Path(directory.name)
        for source in (self.red, self.audio):
            shutil.copyfile(source, self.out / source.name)
            self.write_sidecar(source.name, private=source == self.audio, explicit=source == self.audio)
        self.revision = "r1"
        self.queued = []
        self.authorized = []
        self.admission = []
        self.token = object()
        self.defaults = {"resolution": "64x32", "num_inference_steps": 28,
                         "video_guide": "stale", "audio_path": "stale", "input_waveform": "stale",
                         "_h3_longform": {"stale": True}, "_h3_timeline_guides": "forged",
                         "custom_settings": {"_h3_forged": True}}

        async def generate(request):
            self.assertIs(request.state._maestro_h3_gallery_still_guide_token, self.token)
            self.admission.append(request.state.maestro_account_session_id)
            self.queued.append(await request.json())
            self.prepared = request
            return {"job_id": "av-job", "status": "preparing"}

        self.ns = {
            "Request": object, "Mapping": Mapping, "Any": Any, "HTTPException": HTTPException,
            "copy": copy, "os": os, "upload_usage": upload_usage,
            "_H3_GALLERY_STILL_GUIDE_REQUEST_TOKEN": self.token,
            "_GenerationPreparationRequest": FakePreparationRequest,
            "_request_project_workspace": lambda _request, workspace: workspace,
            "_require_project_access": Mock(return_value=str(self.out)),
            "_require_remote_visible_models": Mock(), "_require_h3_legal_execution": Mock(),
            "_require_model_recipe_terms": Mock(),
            "_require_authorized_output": self.authorize,
            "_output_revision": lambda *_args: self.revision,
            "_inherit_media_access_policy": lambda *_args: {}, "generate": generate,
            "wgp": types.SimpleNamespace(
                get_model_def=lambda _model: {},
                align_model_frame_count=lambda frames, _model: frames,
                get_default_settings=lambda _model: self.defaults,
            ),
        }
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        media = next(node for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "_GENERATION_MEDIA_INPUTS"
                             for target in node.targets))
        exec(compile(ast.Module(body=[media], type_ignores=[]), "launch.py", "exec"), self.ns)
        load_launch_functions(self.ns, "_h3_gallery_av_source_state", "h3_gallery_av_guide_endpoint",
                              "_validate_h3_gallery_av_guide_job", "_decode_h3_gallery_av_guide_job",
                              "_validate_h3_gallery_still_guide_job", "_reject_client_h3_internal_state",
                              "_h3_estimate_for_context")
        self.flag = patch.dict(os.environ, {"MAESTRO_H3_TIMELINE_GUIDES_EXPERIMENTAL": "1"})
        self.flag.start()
        self.addCleanup(self.flag.stop)

    def write_sidecar(self, name, **changes):
        metadata = dict(workspace="project", output_filename=name, artifact_class="final",
                        private=False, explicit=False)
        metadata.update(changes)
        (self.out / (Path(name).stem + ".meta.json")).write_text(json.dumps(metadata))

    def authorize(self, _request, workspace, name):
        self.authorized.append((workspace, name))
        if workspace != "project" or name not in (self.red.name, self.audio.name):
            raise HTTPException(404, "Output file not found")
        return str(self.out), str(self.out / name), {}

    def request(self, **changes):
        body = {"workspace": "project", "model_type": "minimax_h3", "prompt": "violent adult fictional scene",
                "settings": {"video_length": 124, "resolution": "64x32", "seed": 42},
                "guides": [{"name": self.audio.name, "revision": "r1", "kind": "audio", "frame_index": -2},
                           {"name": self.red.name, "revision": "r1", "kind": "video", "frame_index": 0},
                           {"name": self.red.name, "revision": "r1", "kind": "video", "frame_index": 0}]}
        body.update(changes)
        request = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_session_id="session", maestro_remote=True, maestro_account_session_id="account"))
        async def read():
            return copy.deepcopy(body)
        request.json = read
        return request

    def queue(self):
        response = asyncio.run(self.ns["h3_gallery_av_guide_endpoint"](self.request()))
        params = self.queued[-1]
        job = {"workspace": "project", "out_dir": str(self.out), "params": params,
               "private": params["private_output"], "explicit": params["explicit_output"]}
        return response, job

    def test_authorized_ordered_mixed_guides_seal_without_paths_and_inherit_privacy(self):
        response, job = self.queue()
        params = job["params"]
        self.assertEqual(response["h3_guide_execution"]["frame_indices"], [122, 0, 0])
        self.assertEqual(self.authorized, [("project", self.audio.name), ("project", self.red.name),
                                          ("project", self.red.name)])
        self.assertEqual(self.admission, ["account"])
        self.assertEqual(self.prepared.state.maestro_account_session_id, "")
        self.ns["_require_project_access"].assert_called_once_with(
            unittest.mock.ANY, "project", permission="project.generate")
        for name in ("_require_remote_visible_models", "_require_h3_legal_execution", "_require_model_recipe_terms"):
            self.ns[name].assert_called_once()
        self.assertTrue(job["private"] and job["explicit"])
        self.assertEqual(params["prompt"], "violent adult fictional scene")
        self.assertNotIn(str(self.out), json.dumps(params))
        self.assertNotIn("_h3_timeline_guides", params)
        self.assertNotIn("_h3_longform", params)
        self.assertEqual(params["custom_settings"], {"h3_attention_engine": "sdpa"})
        self.assertTrue(all(not params[key] for key in self.ns["_GENERATION_MEDIA_INPUTS"]))
        self.assertIsNone(params["input_waveform"])
        self.assertIsNone(params["audio_path"])
        self.assertEqual(self.defaults["_h3_timeline_guides"], "forged")
        with self.assertRaises(HTTPException):
            self.ns["_reject_client_h3_internal_state"](params)
        self.ns["_reject_client_h3_internal_state"](params, allow_gallery_still_guide=True)
        verified = self.ns["_validate_h3_gallery_still_guide_job"](job)
        self.assertEqual(len(verified["paths"]), 3)

    def test_disabled_or_unauthorized_route_cannot_probe_or_queue(self):
        with patch.dict(os.environ, {"MAESTRO_H3_TIMELINE_GUIDES_EXPERIMENTAL": "0"}), \
                patch.object(av, "probe_gallery_av", side_effect=AssertionError("probed")):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(self.ns["h3_gallery_av_guide_endpoint"](self.request()))
            self.assertEqual(raised.exception.status_code, 409)
        self.ns["_require_project_access"].side_effect = HTTPException(403, "Access denied")
        with patch.object(av, "probe_gallery_av", side_effect=AssertionError("probed")):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(self.ns["h3_gallery_av_guide_endpoint"](self.request()))
            self.assertEqual(raised.exception.status_code, 403)
        self.assertFalse(self.queued)

    def test_replay_and_publication_reject_changed_privacy_revision_bytes_and_mixed_state(self):
        _, job = self.queue()
        validate = self.ns["_validate_h3_gallery_still_guide_job"]
        self.write_sidecar(self.audio.name, private=False, explicit=False)
        with self.assertRaisesRegex(ValueError, "privacy"):
            validate(job)
        self.write_sidecar(self.audio.name, private=True, explicit=True)
        self.revision = "r2"
        with self.assertRaisesRegex(ValueError, "changed"):
            validate(job)
        self.revision = "r1"
        with patch.object(av, "probe_gallery_av", wraps=av.probe_gallery_av) as probe:
            job["params"]["_h3_timeline_still_guide_source"] = {}
            with self.assertRaisesRegex(ValueError, "combined"):
                validate(job)
            probe.assert_not_called()
        job["params"].pop("_h3_timeline_still_guide_source")
        source = self.out / self.red.name
        shutil.copyfile(self.blue, source)
        with self.assertRaisesRegex(ValueError, "bytes"):
            validate(job)

    def test_source_privacy_changed_during_probe_is_rejected(self):
        _, job = self.queue()
        probe = av.probe_gallery_av
        def change(path, kind, **kwargs):
            result = probe(path, kind, **kwargs)
            self.write_sidecar(Path(path).name, private=False, explicit=False)
            return result
        with patch.object(av, "probe_gallery_av", side_effect=change):
            with self.assertRaisesRegex(ValueError, "privacy"):
                self.ns["_validate_h3_gallery_av_guide_job"](job)

    def test_actual_worker_injects_decoded_payload_without_persisting_it(self):
        _, job = self.queue()
        before = copy.deepcopy(job)
        calls, commands = [], []
        def generate_video(task, send_cmd, plugin_data, model_type, resolution, _h3_timeline_guides=None):
            calls.append(_h3_timeline_guides)
        self.ns.update({
            "inspect": inspect, "time": time, "traceback": traceback, "job": job,
            "queue": [{}], "gen": {}, "worker_start_lock": threading.Lock(),
            "worker_start_state": {"cancelled": False}, "worker_started": threading.Event(),
            "task_h3_turbo_validation_authorized": False, "_H3_LONG_STUDIO_MODELS": set(),
            "is_cancel_requested": lambda _job: False, "task_resource_failure": {},
            "_safe_failure_updates": lambda error, _job: {"failure_details": str(error)},
            "_run_generation_task_with_llm_exclusion": lambda _model, _send, call: call(),
        })
        self.ns["wgp"].generate_video = generate_video
        load_nested_launch_function(self.ns, "_run_generation", "make_error_handler")
        task = {"params": copy.deepcopy(job["params"])}
        handler = self.ns["make_error_handler"](
            task, {**job["params"], "_h3_timeline_guides": "forged"},
            lambda *args: commands.append(args), {})
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            handler()
        self.assertEqual(commands, [("exit", None)])
        self.assertEqual(len(calls), 1)
        payload = calls[0]
        self.assertEqual(len(payload.media), 3)
        self.assertEqual(tuple(payload.media[1].visual.shape), (3, 12, 32, 64))
        self.assertEqual(tuple(payload.media[0].waveform.shape)[0], 2)
        self.assertEqual(job, before)
        self.assertNotIn("_h3_timeline_guides", task["params"])
        calls.clear()
        self.ns["gen"]["abort"] = True
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            handler()
        self.assertFalse(calls)
        self.assertEqual(commands[-2][0], "error")
        self.assertIn("cancelled", commands[-2][1])

    def test_interval_estimate_does_not_read_ordinary_calibration(self):
        self.ns["_get_h3_benchmark_cache"] = Mock(side_effect=AssertionError("ordinary cache"))
        self.assertIsNone(self.ns["_h3_estimate_for_context"]({"_uncalibrated_interval_guides": True}))

    def test_shared_replay_cancellation_reaps_real_probe_child(self):
        _, job = self.queue()
        stopped, children = threading.Event(), []
        original = av.subprocess.Popen
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            stopped.set()
            return child
        with patch.object(av.subprocess, "Popen", side_effect=spawn):
            with self.assertRaisesRegex(InterruptedError, "cancelled"):
                self.ns["_validate_h3_gallery_still_guide_job"](job, cancel_check=stopped.is_set)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        snapshot = Path(children[0].args[children[0].args.index("-i") + 1])
        self.assertFalse(snapshot.exists())
        self.assertTrue((self.out / self.audio.name).exists())

    def test_publication_reports_resolved_av_intervals_and_strips_private_handoff(self):
        _, job = self.queue()
        params = job["params"]
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        writer = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == "_write_output_sidecars")
        guards = [node for node in writer.body if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Call) and isinstance(node.test.func, ast.Name)
                  and node.test.func.id == "isinstance" and isinstance(node.test.args[0], ast.Name)
                  and node.test.args[0].id == "guide_source"
                  and not any(isinstance(item, ast.Try) for item in node.body)]
        namespace = {"guide_source": params["_h3_timeline_av_guide_source"], "job": job,
                     "sidecar_params": {**copy.deepcopy(params), "_h3_timeline_guides": object()}, "sidecar": {}}
        exec(compile(ast.Module(body=guards, type_ignores=[]), "launch.py", "exec"), namespace)
        self.assertEqual(namespace["sidecar"]["h3_guide_execution"], {
            "capability": "gallery_av_fl2va_experimental", "frame_index": 122,
            "frame_indices": [122, 0, 0], "target_frames": 124, "guide_count": 3,
            "audio_guides": 1, "video_guides": 2,
        })
        published = namespace["sidecar_params"]
        self.assertNotIn("_h3_timeline_guides", published)
        self.assertNotIn("_h3_timeline_av_guide_source", published)
        self.assertNotIn("_h3_timeline_av_guide_plan", published)
        self.assertNotIn(str(self.out), json.dumps(published))


class IntervalRetryTests(unittest.TestCase):
    def test_saved_payload_rejected_while_ordinary_null_default_remains_valid(self):
        from models.minimax_h3.minimax_h3_handler import family_handler
        validate = family_handler.validate_generative_settings
        self.assertIsNone(validate("minimax_h3", {}, {"_h3_timeline_guides": None}))
        self.assertIn("private worker handoff", validate(
            "minimax_h3", {}, {"_h3_timeline_guides": object()}))
        self.assertIn("private worker handoff", validate(
            "minimax_h3", {}, {"custom_settings": {"_h3_timeline_guides": None}}))

    def wrapper(self, implementation, cleanup):
        tree = ast.parse((ROOT / "app/wgp.py").read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"generate_video", "_notify_h3_profile_observer"}]
        namespace = {"inspect": inspect, "gc": gc, "traceback": traceback,
                     "_generate_video_impl": implementation, "_release_failed_generation_resources": cleanup,
                     "get_default_profile": lambda _kind: 4, "get_output_type_for_model": lambda _model: "video",
                     "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "wgp.py", "exec"), namespace)
        return namespace["generate_video"]

    def test_immutable_interval_oom_is_not_resent_or_rewritten_even_if_cleanup_fails(self):
        calls = []
        failure = H3OomReliefRetry({"resolution": "864x480", "override_profile": 5.0, "num_inference_steps": 18})
        def implementation(task, model_type, resolution, override_profile, _h3_timeline_guides):
            calls.append((resolution, override_profile))
            raise failure
        cleanup = Mock(side_effect=RuntimeError("cleanup"))
        wrapper = self.wrapper(implementation, cleanup)
        task = {"params": {"resolution": "960x544", "num_inference_steps": 28}}
        before = copy.deepcopy(task)
        with self.assertRaises(H3OomReliefRetry) as raised:
            wrapper(task, "minimax_h3", "960x544", 4, object())
        self.assertIs(raised.exception, failure)
        self.assertEqual(calls, [("960x544", 4.5)])
        self.assertEqual(task, before)
        cleanup.assert_called_once()

    def test_interval_success_does_not_contaminate_ordinary_host_calibration(self):
        def implementation(task, model_type, resolution, override_profile, _h3_timeline_guides=None):
            return True
        wrapper = self.wrapper(implementation, Mock())
        with patch("services.h3_host_limits.record_denoise_success") as record:
            self.assertTrue(wrapper({}, "minimax_h3", "64x32", 4, object()))
            record.assert_not_called()
            self.assertTrue(wrapper({}, "minimax_h3", "64x32", 4))
            record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
