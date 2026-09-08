"""CPU coverage for fresh/recovered H3 handoff compatibility and cancellation."""
import ast
import copy
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    ensure_recovery_staging_directory,
)


class H3HandoffLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        cls.runner = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_generation")
        cls.nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"_attach_h3_ref2va_handoff", "_apply_h3_recovered_continuation",
                                       "_queue_recovery_continuation_path", "_prepare_task_continuation"}]
        cls.frames = next(ast.literal_eval(node.value) for node in tree.body
                          if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                          and target.id == "_H3_REF2VA_HANDOFF_FRAMES" for target in node.targets))

    def helpers(self, *, video=False, image=True, create_tail=None):
        def set_tail(params, path, *, slot):
            params["video_guide" if slot == 1 else f"video_guide{slot}"] = path
        ns = dict(os=os, QueueRecoveryRuntimeError=QueueRecoveryRuntimeError,
                  ensure_recovery_staging_directory=ensure_recovery_staging_directory,
                  _H3_REF2VA_HANDOFF_FRAMES=self.frames,
                  _h3_ref2va_reference_capacity=lambda *args, **kwargs: {
                      "video": video, "image": image, "video_slot": 3},
                  _create_h3_ref2va_tail_video=create_tail,
                  _set_h3_ref2va_tail=set_tail)
        exec(compile(ast.Module(body=self.nodes, type_ignores=[]), "active-h3-handoff", "exec"), ns)
        return ns

    @staticmethod
    def decoder():
        import numpy as np
        class Frame:
            def asnumpy(self):
                return np.zeros((4, 4, 3), dtype=np.uint8)
        class Reader:
            def __init__(self, _path):
                pass
            def __len__(self):
                return 1
            def __getitem__(self, _index):
                return Frame()
        return types.SimpleNamespace(VideoReader=Reader)

    def test_cut_preserves_supplied_references_and_replays_generated_still(self):
        with tempfile.TemporaryDirectory() as project:
            staging = Path(ensure_recovery_staging_directory(project))
            frame = staging / "unit-example-continuation.png"
            from PIL import Image
            Image.new("RGB", (4, 4), "red").save(frame)
            initial = dict(image_refs=["selected.png"], _ref2va_continuation="cut")
            fresh = copy.deepcopy(initial)
            ns = self.helpers()
            with patch.dict(sys.modules, {"decord": self.decoder()}):
                result = ns["_prepare_task_continuation"](
                    {"params": {"multi_clip_info": {"automatic_h3_longform": True}}},
                    {"params": fresh}, "previous.mp4", out_dir=project, task_no=1,
                    recovery_staging_dir=str(staging), recovery_output_prefix="unit-example")
            self.assertEqual(result["mode"], "semantic_still")
            self.assertEqual(fresh["image_refs"], ["selected.png", str(frame)])
            unit_id = "unit:v1:" + "a" * 64
            unit = dict(unit_id=unit_id, continuation=dict(mode=result["mode"],
                dependency=unit_id, basename=frame.name, storage="recovery_staging"))
            restored = dict(params=copy.deepcopy(initial))
            ns["_apply_h3_recovered_continuation"](unit, restored, project)
            self.assertEqual(restored["params"]["image_refs"], fresh["image_refs"])
            self.assertNotIn("_ref2va_continuation", restored["params"])
            self.assertEqual(restored["params"]["custom_settings"], fresh["custom_settings"])
            self.assertEqual(restored["params"], fresh)

    def test_full_reference_capacity_uses_existing_prompt_only_recovery_mode(self):
        ns = self.helpers(image=False)
        params = dict(image_refs=["selected.png"])
        result = ns["_attach_h3_ref2va_handoff"](params,
            latest_video="previous.mp4", last_frame_path="frame.png", out_dir="unused",
            task_no=1, boundary_type="cut")
        self.assertEqual(result["mode"], "prompt_only")
        self.assertEqual(params["image_refs"], ["selected.png"])
        unit_id = "unit:v1:" + "b" * 64
        unit = dict(unit_id=unit_id, continuation=dict(mode=result["mode"], dependency=unit_id))
        next_task = dict(params=dict(image_refs=["selected.png"], _ref2va_continuation="cut"))
        ns["_apply_h3_recovered_continuation"](unit, next_task, "unused")
        self.assertEqual(next_task["params"], dict(image_refs=["selected.png"]))
        self.assertEqual(next_task["params"], params)

    def test_staged_prepare_cancellation_removes_still_and_partial_tail(self):
        with tempfile.TemporaryDirectory() as project:
            staging = Path(ensure_recovery_staging_directory(project))
            error = InterruptedError("cancelled")
            def create_tail(source, destination):
                Path(destination).write_bytes(b"partial")
                raise error
            ns = self.helpers(video=True, create_tail=create_tail)
            params = dict(image_refs=["selected.png"], _ref2va_continuation="continuous")
            before = copy.deepcopy(params)
            with patch.dict(sys.modules, {"decord": self.decoder()}), self.assertRaises(InterruptedError) as caught:
                ns["_prepare_task_continuation"](
                    {"params": {"multi_clip_info": {"automatic_h3_longform": True}}},
                    {"params": params}, "previous.mp4", out_dir=project, task_no=1,
                    recovery_staging_dir=str(staging), recovery_output_prefix="unit-cancel")
            self.assertIs(caught.exception, error)
            self.assertEqual(params, before)
            self.assertEqual(list(staging.iterdir()), [])

    def test_partial_still_save_failure_removes_owned_file(self):
        from PIL import Image
        for error in (OSError("save failed"), InterruptedError("cancelled"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as project:
                staging = Path(ensure_recovery_staging_directory(project))
                def partial_save(_image, destination):
                    Path(destination).write_bytes(b"partial PNG")
                    raise error
                params = dict(image_refs=["selected.png"], _ref2va_continuation="cut")
                before = copy.deepcopy(params)
                with patch.dict(sys.modules, {"decord": self.decoder()}), patch.object(
                    Image.Image, "save", partial_save
                ), self.assertRaises(type(error)) as caught:
                    self.helpers()["_prepare_task_continuation"](
                        {"params": {}}, {"params": params}, "previous.mp4",
                        out_dir=project, task_no=1, recovery_staging_dir=str(staging),
                        recovery_output_prefix="unit-partial")
                self.assertIs(caught.exception, error)
                self.assertEqual(params, before)
                self.assertEqual(list(staging.iterdir()), [])

    def test_prompt_only_prepare_removes_unused_staged_still(self):
        with tempfile.TemporaryDirectory() as project:
            staging = Path(ensure_recovery_staging_directory(project))
            ns = self.helpers(image=False)
            params = dict(image_refs=["selected.png"], _ref2va_continuation="cut")
            with patch.dict(sys.modules, {"decord": self.decoder()}):
                result = ns["_prepare_task_continuation"](
                    {"params": {"multi_clip_info": {"automatic_h3_longform": True}}},
                    {"params": params}, "previous.mp4", out_dir=project, task_no=1,
                    recovery_staging_dir=str(staging), recovery_output_prefix="unit-full")
            self.assertEqual(result, {"mode": "prompt_only", "path": None})
            self.assertEqual(params, {"image_refs": ["selected.png"]})
            self.assertEqual(list(staging.iterdir()), [])

    def test_fresh_and_recovered_worker_callers_do_not_fail_or_retry_cancellation(self):
        calls = [node for node in ast.walk(self.runner) if isinstance(node, ast.Assign)
                 and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                 and node.value.func.id == "_prepare_task_continuation"]
        fresh = next(node for node in ast.walk(self.runner) if isinstance(node, ast.Try)
                     and any(isinstance(child, ast.Assign) and child in calls for child in node.body))
        recovered = next(node for node in calls if any(isinstance(target, ast.Name)
                         and target.id == "handoff" for target in node.targets))
        outer = next(node for node in ast.walk(self.runner) if isinstance(node, ast.Try)
                     and any("_try_automatic_resource_retry" in ast.unparse(handler)
                             for handler in node.handlers))
        for label, statement in (("fresh", fresh), ("recovered", recovered)):
            for required in (True, False):
                with self.subTest(caller=label, required=required):
                    error = InterruptedError("cancelled")
                    def prepare(*args, **kwargs):
                        raise error
                    def forbidden(*args, **kwargs):
                        self.fail("Cancellation reached failure or retry handling")
                    ns = dict(_prepare_task_continuation=prepare, task={}, next_task={}, handoff_target={},
                              latest_video="video.mp4", video_path="video.mp4", out_dir="unused", task_no=1,
                              recovery_staging_dir="staging", params={}, job_id="example", task_idx=0,
                              required_h3_continuation=required, next_params={"_ref2va_continuation": "cut"},
                              ensure_recovery_staging_directory=lambda path: "staging",
                              finish_job=forbidden, _safe_failure_updates=forbidden,
                              _try_automatic_resource_retry=forbidden, job={"status": "cancelled"})
                    if label == "fresh":
                        with self.assertRaises(InterruptedError) as caught:
                            exec(compile(ast.Module(body=[copy.deepcopy(statement)], type_ignores=[]), "fresh-caller", "exec"), ns)
                        self.assertIs(caught.exception, error)
                    probe = ast.parse("def probe():\n    pass\n").body[0]
                    probe.body = [ast.Try(body=[copy.deepcopy(statement)], handlers=copy.deepcopy(outer.handlers),
                                          orelse=[], finalbody=[])]
                    module = ast.fix_missing_locations(ast.Module(body=[probe], type_ignores=[]))
                    exec(compile(module, "worker-cancellation", "exec"), ns)
                    self.assertIs(ns["probe"](), False)
                    self.assertEqual(ns["job"]["status"], "cancelled")

    def test_tail_cancellation_removes_partial_file_without_fallback(self):
        for error in (InterruptedError("cancelled"), KeyboardInterrupt(), SystemExit(4)):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as project:
                outputs = []
                def create_tail(source, destination):
                    Path(destination).write_bytes(b"partial")
                    outputs.append(Path(destination))
                    raise error
                ns = self.helpers(video=True, create_tail=create_tail)
                params = dict(image_refs=["selected.png"])
                before = copy.deepcopy(params)
                with self.assertRaises(type(error)) as caught:
                    ns["_attach_h3_ref2va_handoff"](params,
                        latest_video="previous.mp4", last_frame_path="frame.png", out_dir=project,
                        task_no=1, boundary_type="continuous")
                self.assertIs(caught.exception, error)
                self.assertEqual(params, before)
                self.assertEqual(len(outputs), 1)
                self.assertFalse(outputs[0].exists())

    def test_tail_failure_has_bounded_warning_and_supported_still_fallback(self):
        with tempfile.TemporaryDirectory() as project:
            outputs = []
            def create_tail(source, destination):
                Path(destination).write_bytes(b"partial")
                outputs.append(Path(destination))
                raise RuntimeError("private /operator/path credential-like-value")
            ns = self.helpers(video=True, create_tail=create_tail)
            params = dict(image_refs=["selected.png"])
            result = ns["_attach_h3_ref2va_handoff"](params,
                latest_video="previous.mp4", last_frame_path="frame.png", out_dir=project,
                task_no=1, boundary_type="continuous")
            self.assertEqual(result["mode"], "semantic_still")
            self.assertNotIn("private", result["warning"])
            self.assertNotIn("/operator", result["warning"])
            self.assertFalse(outputs[0].exists())
            self.assertEqual(params["image_refs"], ["selected.png", "frame.png"])

    def test_missing_still_does_not_mutate_reference_inputs(self):
        ns = self.helpers()
        params = dict(image_refs=["selected.png"])
        before = copy.deepcopy(params)
        with self.assertRaisesRegex(QueueRecoveryRuntimeError, "frame is missing"):
            ns["_attach_h3_ref2va_handoff"](params,
                latest_video="previous.mp4", last_frame_path=None, out_dir="unused",
                task_no=1, boundary_type="cut")
        self.assertEqual(params, before)


if __name__ == "__main__":
    unittest.main()
