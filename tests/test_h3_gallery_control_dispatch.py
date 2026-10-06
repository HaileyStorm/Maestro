"""Private Control authorization, actual CPU worker transport and failure proof."""
from __future__ import annotations

import ast
import asyncio
import copy
from contextlib import redirect_stderr, redirect_stdout
import inspect
import io
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
import traceback
import types
from typing import Any, Mapping
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
import test_h3_gallery_control as control_fixtures
from test_h3_gallery_still_guide import (
    FakePreparationRequest, load_launch_functions, load_nested_launch_function,
)
import test_h3_gallery_av_dispatch as av_dispatch_fixtures
from services import upload_usage
from services import h3_gallery_control as control
from services.h3_oom_relief import H3OomReliefRetry

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg required")
class ControlDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        control_fixtures.GalleryControlTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        control_fixtures.GalleryControlTests.tearDownClass()

    def setUp(self):
        self.fixture = control_fixtures.GalleryControlTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.out = self.fixture.directory
        self.video = self.out / "prepared.mp4"
        shutil.copyfile(self.fixture.video, self.video)
        self.write_sidecar()
        self.queued, self.authorized = [], []
        self.token = object()
        async def generate(request):
            self.assertIs(request.state._maestro_h3_gallery_still_guide_token, self.token)
            self.queued.append(await request.json())
            self.prepared = request
            return {"job_id": "control-job", "status": "preparing"}
        self.ns = {
            "Request": object, "Mapping": Mapping, "Any": Any, "HTTPException": HTTPException,
            "copy": copy, "os": os, "math": math, "upload_usage": upload_usage,
            "_H3_GALLERY_STILL_GUIDE_REQUEST_TOKEN": self.token,
            "_GenerationPreparationRequest": FakePreparationRequest,
            "_request_project_workspace": lambda _request, workspace: workspace,
            "_require_project_access": Mock(return_value=str(self.out)),
            "_require_remote_visible_models": Mock(), "_require_h3_legal_execution": Mock(),
            "_require_model_recipe_terms": Mock(), "_require_authorized_output": self.authorize,
            "_output_revision": lambda *_args: "r1", "_inherit_media_access_policy": lambda *_args: {},
            "generate": generate, "wgp": types.SimpleNamespace(get_default_settings=lambda _model: {
                "resolution": "64x32", "num_inference_steps": 28, "video_source": "stale",
                "_h3_control_dispatch": "forged", "custom_settings": {"_h3_forged": True},
            }),
        }
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        media = next(node for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "_GENERATION_MEDIA_INPUTS"
                             for target in node.targets))
        exec(compile(ast.Module(body=[media], type_ignores=[]), "launch.py", "exec"), self.ns)
        load_launch_functions(self.ns, "_h3_gallery_control_available", "_h3_gallery_av_source_state",
            "h3_gallery_control_endpoint", "_validate_h3_gallery_control_job", "_decode_h3_gallery_control_job",
            "_validate_h3_gallery_still_guide_job", "_reject_client_h3_internal_state")

    def write_sidecar(self, **changes):
        metadata = dict(workspace="project", output_filename="prepared.mp4", artifact_class="final",
                        private=True, explicit=True)
        metadata.update(changes)
        (self.out / "prepared.meta.json").write_text(json.dumps(metadata))

    def authorize(self, _request, workspace, name):
        self.authorized.append((workspace, name))
        if workspace != "project" or name != self.video.name:
            raise HTTPException(404, "Output file not found")
        return str(self.out), str(self.video), {}

    def request(self, **changes):
        body = {"workspace": "project", "model_type": "minimax_h3", "prompt": "violent adult fictional scene",
                "settings": {"resolution": "64x32", "seed": 42, "num_inference_steps": 2},
                "control": {"name": "prepared.mp4", "revision": "r1", "kind": "depth", "strength": 0.6}}
        body.update(changes)
        request = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_session_id="session", maestro_remote=True, maestro_account_session_id="account"))
        async def read():
            return copy.deepcopy(body)
        request.json = read
        return request

    def queue(self):
        response = asyncio.run(self.ns["h3_gallery_control_endpoint"](self.request()))
        params = self.queued[-1]
        job = dict(params=params, workspace="project", out_dir=str(self.out), private=True, explicit=True)
        return response, job

    def test_authorized_sensitive_prompt_queues_exact_source_and_no_private_paths(self):
        response, job = self.queue()
        self.assertEqual(job["params"]["prompt"], "violent adult fictional scene")
        self.assertEqual(response["h3_control_execution"]["target_frames"], 22)
        self.assertTrue(job["params"]["private_output"])
        self.assertTrue(job["params"]["explicit_output"])
        self.assertNotIn(str(self.out), json.dumps(job["params"]))
        self.assertNotIn("_h3_control_dispatch", job["params"])
        self.assertEqual(self.prepared.state.maestro_account_session_id, "")
        self.assertEqual(self.ns["_validate_h3_gallery_still_guide_job"](job)["path"], str(self.video))

    def test_path_injection_and_unauthorized_source_cause_no_probe_or_queue(self):
        with patch("services.h3_gallery_av_guide.probe_gallery_av") as probe:
            for changes in (
                {"settings": {"resolution": "64x32", "_h3_control_checkpoint": "/private"}},
                {"control": {"name": "../prepared.mp4", "revision": "r1", "kind": "depth", "strength": 0.6}},
                {"settings": {"resolution": "64x32", "seed": True}},
                {"control": {"name": "prepared.mp4", "revision": "r1", "kind": [], "strength": 0.6}},
            ):
                with self.subTest(changes=changes), self.assertRaises(HTTPException):
                    asyncio.run(self.ns["h3_gallery_control_endpoint"](self.request(**changes)))
            probe.assert_not_called()
        self.assertFalse(self.queued)
        with self.assertRaises(HTTPException):
            self.ns["_reject_client_h3_internal_state"]({"_h3_control_gallery_source": {}})

    def test_changed_source_privacy_plan_or_asset_blocks_replay(self):
        _, job = self.queue()
        self.write_sidecar(private=False)
        with self.assertRaisesRegex(ValueError, "privacy"):
            self.ns["_validate_h3_gallery_control_job"](job)
        self.write_sidecar()
        self.video.write_bytes(self.video.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "bytes|geometry"):
            self.ns["_validate_h3_gallery_control_job"](job)
        shutil.copyfile(self.fixture.video, self.video)
        self.fixture.control.write_bytes(b"changed asset")
        with self.assertRaises(ValueError):
            self.ns["_validate_h3_gallery_control_job"](job)

    def test_actual_worker_injects_unit_range_handoff_without_persistence(self):
        _, job = self.queue()
        before = copy.deepcopy(job)
        calls, commands = [], []
        def generate_video(task, send_cmd, plugin_data, model_type, resolution, _h3_control_dispatch=None):
            calls.append(_h3_control_dispatch)
        self.ns.update({
            "inspect": inspect, "time": time, "traceback": traceback, "job": job, "queue": [{}], "gen": {},
            "worker_start_lock": threading.Lock(), "worker_start_state": {"cancelled": False},
            "worker_started": threading.Event(), "task_h3_turbo_validation_authorized": False,
            "_H3_LONG_STUDIO_MODELS": set(), "is_cancel_requested": lambda _job: False,
            "task_resource_failure": {}, "_safe_failure_updates": lambda error, _job: {"failure_details": str(error)},
            "_run_generation_task_with_llm_exclusion": lambda _model, _send, call: call(),
        })
        self.ns["wgp"].generate_video = generate_video
        load_nested_launch_function(self.ns, "_run_generation", "make_error_handler")
        task = {"params": copy.deepcopy(job["params"])}
        handler = self.ns["make_error_handler"](task, {**job["params"], "_h3_control_dispatch": "forged"},
            lambda *args: commands.append(args), {})
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            handler()
        self.assertEqual(commands, [("exit", None)])
        self.assertEqual(len(calls), 1)
        self.assertIs(type(calls[0]), control.H3GalleryControlDispatch)
        self.assertEqual(tuple(calls[0].video.shape), (1, 3, 22, 32, 64))
        self.assertGreaterEqual(calls[0].video.min().item(), 0)
        self.assertLessEqual(calls[0].video.max().item(), 1)
        self.assertEqual(job, before)
        self.assertNotIn("_h3_control_dispatch", task["params"])
        self.ns["gen"]["abort"] = True
        calls.clear()
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            handler()
        self.assertFalse(calls)

    def test_publication_strips_binding_and_reports_precomputed_control(self):
        _, job = self.queue()
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        writer = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == "_write_output_sidecars")
        guards = [node for node in writer.body if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Call) and isinstance(node.test.func, ast.Name)
                  and node.test.func.id == "isinstance" and isinstance(node.test.args[0], ast.Name)
                  and node.test.args[0].id == "guide_source"
                  and not any(isinstance(item, ast.Try) for item in node.body)]
        namespace = {"guide_source": job["params"]["_h3_control_gallery_source"], "job": job,
                     "sidecar_params": {**copy.deepcopy(job["params"]), "_h3_control_dispatch": object()}, "sidecar": {}}
        exec(compile(ast.Module(body=guards, type_ignores=[]), "launch.py", "exec"), namespace)
        self.assertTrue(namespace["sidecar"]["h3_control_execution"]["precomputed_control"])
        self.assertNotIn("h3_guide_execution", namespace["sidecar"])
        self.assertFalse(any(key.startswith("_h3_control") for key in namespace["sidecar_params"]))

    def test_actual_wgp_private_boundary_keeps_legal_prefix_and_rejects_extra_inputs(self):
        _, job = self.queue()
        dispatch = self.ns["_decode_h3_gallery_control_job"](job, cancel_check=lambda: False)
        tree = ast.parse((ROOT / "app/wgp.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "_generate_video_impl")
        namespace = {argument.arg: None for argument in function.args.args}
        namespace.update({"os": os, "_h3_control_dispatch": dispatch, "model_type": "minimax_h3",
                          "mode": "video", "resolution": job["params"]["resolution"], "video_length": 22,
                          "repeat_generation": 1, "batch_size": 1, "guidance_scale": 1,
                          "custom_settings": {"h3_attention_engine": "sdpa"}})
        guard = compile(ast.Module(body=[function.body[0]], type_ignores=[]), "wgp.py", "exec")
        exec(guard, namespace)
        namespace["image_start"] = object()
        with self.assertRaisesRegex(ValueError, "independent"):
            exec(guard, namespace)
        alignment = next(node for node in function.body if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == "video_length" for target in node.targets)
                         and isinstance(node.value, ast.IfExp))
        align = Mock(return_value=124)
        namespace.update(align_model_frame_count=align, model_def={}, video_length=22)
        exec(compile(ast.Module(body=[alignment], type_ignores=[]), "wgp.py", "exec"), namespace)
        self.assertEqual(namespace["video_length"], 22)
        align.assert_not_called()
        namespace["_h3_control_dispatch"] = None
        exec(compile(ast.Module(body=[alignment], type_ignores=[]), "wgp.py", "exec"), namespace)
        self.assertEqual(namespace["video_length"], 124)

        sampling = next(node for node in ast.walk(function) if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "sampling_frame_num"
                                for target in node.targets))
        generate = next(node for node in ast.walk(function) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == "call_with_lightx2v_cleanup"
                        and any(keyword.arg == "frame_num" for keyword in node.keywords))
        native_frames = next(keyword.value for keyword in generate.keywords if keyword.arg == "frame_num")
        decode = next(node for node in ast.walk(function) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name) and node.func.id == "begin_decode_capture")
        decode_frames = next(value for key, value in zip(decode.args[1].keys, decode.args[1].values)
                             if isinstance(key, ast.Constant) and key.value == "frames")
        namespace.update(_h3_cumulative_dispatch=None)
        for frames in (5, 22, 39, 124):
            with self.subTest(control_frames=frames):
                align.reset_mock()
                namespace.update(_h3_control_dispatch=dispatch, current_video_length=frames)
                exec(compile(ast.Module(body=[sampling], type_ignores=[]), "wgp.py", "exec"), namespace)
                self.assertEqual(eval(compile(ast.Expression(native_frames), "wgp.py", "eval"), namespace), frames)
                self.assertEqual(eval(compile(ast.Expression(decode_frames), "wgp.py", "eval"), namespace), frames)
                align.assert_not_called()
        namespace.update(_h3_control_dispatch=None, current_video_length=22)
        exec(compile(ast.Module(body=[sampling], type_ignores=[]), "wgp.py", "exec"), namespace)
        self.assertEqual(namespace["sampling_frame_num"], 124)
        align.assert_called_once_with(22, {}, for_generation=True)


class ControlRetryTests(unittest.TestCase):
    def test_ordinary_control_ordinary_transition_loads_separate_graphs(self):
        source = (ROOT / "app/wgp.py").read_text()
        tree = ast.parse(source)
        implementation = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                              and node.name == "_generate_video_impl")
        force = next(node for node in implementation.body if isinstance(node, ast.If)
                     and ast.get_source_segment(source, node.test) == "_h3_control_dispatch is not None"
                     and any(isinstance(item, ast.Assign) and any(isinstance(target, ast.Name)
                         and target.id == "reload_needed" for target in item.targets) for item in node.body))
        boundary = next(node for node in implementation.body if isinstance(node, ast.If)
                        and "model_type != transformer_type" in ast.get_source_segment(source, node.test))
        namespace = {"math": math}
        helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in ("_model_load_configuration_matches", "_release_for_model_reprofile")]
        exec(compile(ast.Module(body=helpers, type_ignores=[]), "wgp.py", "exec"), namespace)
        loaded, released = [], []
        def release():
            released.append(namespace["wan_model"])
            namespace["wan_model"] = None
            namespace["reload_needed"] = True
        def load(model_type, override_profile, **kwargs):
            loaded.append(kwargs)
            # The private loader invalidates the ordinary configuration on exit.
            namespace["configuration"] = None if kwargs.get("_h3_control_checkpoint") else (0.8, 4)
            return object(), object()
        original = object()
        namespace.update({
            "_h3_control_dispatch": types.SimpleNamespace(base_checkpoint="/owned/base", control_checkpoint="/owned/control"),
            "wan_model": original, "model_type": "minimax_h3", "transformer_type": "minimax_h3",
            "reload_needed": False, "profile": 4, "loaded_profile": 4,
            "configuration_reprofiled": False, "release_model": release, "get_model_name": lambda *_: "H3",
            "get_model_def": lambda *_: {}, "args": types.SimpleNamespace(save_quantized=False),
            "get_model_filename": lambda **_: [], "transformer_quantization": "", "transformer_dtype_policy": "",
            "send_cmd": Mock(), "override_profile": 4, "output_type": "video", "gen": {},
            "requested_residency_evidence_context": {}, "dasiwa_checkpoint_admission": None,
            "resolution": "64x32", "model_kwargs": {}, "load_models": load, "get_unique_id": lambda: "id",
        })
        code = compile(ast.Module(body=[force, boundary], type_ignores=[]), "wgp.py", "exec")
        exec(code, namespace)
        self.assertEqual(released, [original])
        self.assertEqual(loaded[0]["_h3_control_checkpoint"], "/owned/control")
        self.assertEqual(loaded[0]["_h3_control_base_checkpoint"], "/owned/base")
        private = namespace["wan_model"]
        namespace["_h3_control_dispatch"] = None
        namespace["configuration_reprofiled"] = namespace["_release_for_model_reprofile"](
            private, namespace["configuration"], (0.8, 4), release,
        )
        exec(code, namespace)
        self.assertEqual(released, [original, private])
        self.assertEqual(len(loaded), 2)
        self.assertNotIn("_h3_control_checkpoint", loaded[1])

    def test_saved_private_control_is_rejected_and_null_signature_default_is_valid(self):
        from models.minimax_h3.minimax_h3_handler import family_handler
        validate = family_handler.validate_generative_settings
        self.assertIsNone(validate("minimax_h3", {}, {"_h3_control_dispatch": None}))
        for value in ({"_h3_control_dispatch": object()}, {"_h3_control_checkpoint": "/private"},
                      {"custom_settings": {"_h3_control": None}}):
            with self.subTest(value=value):
                self.assertIn("private worker handoff", validate("minimax_h3", {}, value))

    def test_control_oom_has_one_invocation_and_does_not_change_settings(self):
        seen = []
        def implementation(task, model_type, resolution, override_profile, _h3_control_dispatch=None):
            seen.append((resolution, override_profile))
            raise H3OomReliefRetry({"resolution": "32x32", "override_profile": 5})
        cleanup = Mock()
        wrapper = av_dispatch_fixtures.IntervalRetryTests().wrapper(implementation, cleanup)
        with self.assertRaises(H3OomReliefRetry):
            wrapper({}, "minimax_h3", "64x32", 4, object())
        self.assertEqual(seen, [("64x32", 4)])
        cleanup.assert_called_once()

    def test_control_success_does_not_calibrate_ordinary_base(self):
        def implementation(task, model_type, resolution, override_profile, _h3_control_dispatch=None):
            return True
        wrapper = av_dispatch_fixtures.IntervalRetryTests().wrapper(implementation, Mock())
        with patch("services.h3_host_limits.record_denoise_success") as record:
            self.assertTrue(wrapper({}, "minimax_h3", "64x32", 4, object()))
            record.assert_not_called()
            self.assertTrue(wrapper({}, "minimax_h3", "64x32", 4))
            record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
