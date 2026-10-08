"""Real CPU sealed-bundle decoding and private WGP boundary checks."""
import ast
import copy
import inspect
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import torch
from services import h3_face_refine as face
from services import h3_face_refine_worker as worker
from services import h3_gallery_av_guide as av


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg required")
class FaceWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        cls.storage = tempfile.TemporaryDirectory(prefix="face-worker-")
        cls.root = Path(cls.storage.name)
        cls.source = cls.root / "source.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "testsrc2=s=96x64:r=24:d=5.167", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=6", "-itsoffset", "0.125",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=6",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-frames:v", "124",
            "-c:v", "ffv1", "-threads", "1", "-c:a", "pcm_s16le", str(cls.source)],
            check=True, capture_output=True, timeout=30)
        observations = dict(shots=[0, 62], boxes=[[16, 16, 48, 48]] * 124,
                            canvas=[64, 64], padding=1, smoothing=3)
        observations["boxes"][5:8] = [None] * 3
        cls.plan = face.prepare_crops(cls.source, observations, cls.root / "prepared")
        cls.crops = cls.root / "prepared/crops.mkv"
        cls.multipliers = tuple(0.0 if rectangle is None else 0.8
                                for rectangle in cls.plan["track"]["rectangles"])

    @classmethod
    def tearDownClass(cls):
        cls.storage.cleanup()
        torch.set_num_threads(cls.old_threads)

    def make(self, **overrides):
        arguments = dict(source=self.source, crops=self.crops, plan=self.plan,
            expected_plan_sha256=self.plan["plan_sha256"], strength=0.5,
            frame_multipliers=self.multipliers, audio_stream=1, sampling_steps=4)
        arguments.update(overrides)
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"):
            return worker.make_face_refine_dispatch(**arguments)

    def test_exact_crop_pixels_selected_audio_delay_and_immutable_source(self):
        source_digest, crop_digest = face._file_digest(self.source), face._file_digest(self.crops)
        dispatch = self.make()
        self.assertEqual(dispatch.payload.video.shape, (1, 3, 124, 64, 64))
        self.assertEqual(dispatch.payload.waveform.shape, (2, 165600))
        expected = torch.from_numpy(face._read_rgb(self.crops,
            dict(width=64, height=64, frame_count=124, fps="24/1"), None).copy()).float().div_(255)
        torch.testing.assert_close(dispatch.payload.video[0].permute(1, 2, 3, 0), expected, rtol=0, atol=0)
        self.assertEqual(dispatch.payload.waveform[:, :3900].abs().max().item(), 0)
        self.assertGreater(dispatch.payload.waveform[:, 4200:5000].abs().max().item(), 0.05)
        first = self.make(audio_stream=0)
        self.assertGreater(first.payload.waveform[:, :3900].abs().max().item(), 0.05)
        self.assertFalse(torch.equal(first.payload.waveform, dispatch.payload.waveform))
        self.assertEqual(dispatch.binding["audio_stream"], 1)
        self.assertNotIn(str(self.root), str(dispatch.binding))
        self.assertEqual(face._file_digest(self.source), source_digest)
        self.assertEqual(face._file_digest(self.crops), crop_digest)

    def test_explicit_no_audio_and_worker_capture_survive_caller_mutation(self):
        dispatch = self.make(audio_stream=None)
        self.assertIsNone(dispatch.payload.waveform)
        self.assertIsNone(dispatch.binding["audio_stream"])
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"):
            captured = worker.validate_face_refine_dispatch(dispatch,
                frame_num=124, height=64, width=64, sampling_steps=4)
        dispatch.payload.video.zero_()
        dispatch.plan["track"]["rectangles"][0][0] += 1
        self.assertGreater(captured.payload.video.max().item(), 0.1)
        self.assertEqual(captured.plan, self.plan)

    def test_changed_source_crops_and_plan_refused_before_pixel_decode(self):
        for field, original in (("source", self.source), ("crops", self.crops)):
            with tempfile.TemporaryDirectory(dir=self.root) as directory:
                changed = Path(directory) / original.name
                changed.write_bytes(original.read_bytes() + b"changed")
                with patch.object(worker, "_decode_video", side_effect=AssertionError("decoded")), \
                        self.assertRaisesRegex(ValueError, "bytes"):
                    self.make(**{field: changed})
        plan = copy.deepcopy(self.plan)
        plan["track"]["rectangles"][0][0] += 1
        with self.assertRaisesRegex(ValueError, "commitment"):
            self.make(plan=plan)
        with self.assertRaisesRegex(ValueError, "pinned"):
            self.make(expected_plan_sha256="sha256:" + "0" * 64)

    def test_selected_audio_missing_and_cancellation_do_not_fallback(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.make(audio_stream=2)
        with self.assertRaises(av.H3GalleryAVGuideCancelled):
            self.make(cancel_check=lambda: True)
        dispatch = self.make()
        mismatched = worker.H3FaceRefineDispatch(dispatch.plan,
            {**dispatch.binding, "audio_stream": None, "audio_sample_count": 0}, dispatch.payload)
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), \
                self.assertRaisesRegex(ValueError, "selected audio"):
            worker.validate_face_refine_dispatch(mismatched,
                frame_num=124, height=64, width=64, sampling_steps=4)

    def test_unresolved_frame_and_combined_memory_limits_precede_model_work(self):
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.make(frame_multipliers=(0.8,) * 124)
        plan = copy.deepcopy(self.plan)
        plan["track"]["canvas"] = [1024, 1024]
        plan["plan_sha256"] = face._seal({k: v for k, v in plan.items() if k != "plan_sha256"})
        with patch.object(worker, "_decode_video", side_effect=AssertionError("decoded")), \
                self.assertRaisesRegex(ValueError, "memory"):
            self.make(plan=plan, expected_plan_sha256=plan["plan_sha256"])

    def test_actual_wgp_boundary_captures_payload_and_forwards_exact_native_frames(self):
        dispatch = self.make()
        tree = ast.parse((ROOT / "app/wgp.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "_generate_video_impl")
        namespace = {argument.arg: None for argument in function.args.args}
        namespace.update(_h3_face_refine_dispatch=dispatch, model_type="minimax_h3",
            image_mode=0, mode="video", resolution="64x64", video_length=124,
            repeat_generation=1, batch_size=1, guidance_scale=1, num_inference_steps=4,
            custom_settings={"h3_attention_engine": "sdpa"})
        guard = next(node for node in function.body if isinstance(node, ast.If)
                     and ast.unparse(node.test) == "_h3_face_refine_dispatch is not None")
        with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"):
            exec(compile(ast.Module(body=[guard], type_ignores=[]), "wgp.py", "exec"), namespace)
        self.assertIsNot(namespace["_h3_face_refine_dispatch"].payload.video, dispatch.payload.video)
        from services.h3_face_refine_job import FaceRefineResultSink
        with tempfile.TemporaryDirectory(dir=self.root) as folder:
            sink = FaceRefineResultSink(Path(folder)/"replacement.mkv", dispatch.binding,124,64,64)
            bad = {**namespace, "_h3_face_refine_output":sink}
            sink.width = 96
            with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), self.assertRaisesRegex(ValueError,"binding"):
                exec(compile(ast.Module(body=[guard], type_ignores=[]), "wgp.py", "exec"), bad)
        for key, value in (("video_length", 125), ("batch_size", 2), ("audio_source", "source"),
                           ("spatial_upsampling", "2x"), ("_h3_control_dispatch", object())):
            bad = {**namespace, key: value}
            with patch.dict(os.environ, MAESTRO_H3_FACE_REFINE_EXPERIMENTAL="1"), self.assertRaises(ValueError):
                exec(compile(ast.Module(body=[guard], type_ignores=[]), "wgp.py", "exec"), bad)
        sampling = next(node for node in ast.walk(function) if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "sampling_frame_num" for target in node.targets))
        align = Mock(side_effect=AssertionError("aligned"))
        namespace.update(current_video_length=124, model_def={}, align_model_frame_count=align)
        exec(compile(ast.Module(body=[sampling], type_ignores=[]), "wgp.py", "exec"), namespace)
        self.assertEqual(namespace["sampling_frame_num"], 124)
        generate = next(node for node in ast.walk(function) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "call_with_lightx2v_cleanup"
            and any(keyword.arg == "frame_num" for keyword in node.keywords))
        forwarded = [keyword.value for keyword in generate.keywords if keyword.arg is None]
        values = {}
        for expression in forwarded:
            if "_h3_face_refine" in ast.unparse(expression):
                values.update(eval(compile(ast.Expression(expression), "wgp.py", "eval"), namespace))
        self.assertIs(values["_h3_face_refine"], namespace["_h3_face_refine_dispatch"].payload)

    def test_actual_wrapper_never_retries_or_calibrates_private_crop_requests(self):
        from services.h3_oom_relief import H3OomReliefRetry
        dispatch = self.make()
        tree = ast.parse((ROOT / "app/wgp.py").read_text())
        wrapper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "generate_video")
        calls = []
        should_fail = [True]
        def implementation(model_type="minimax_h3", resolution="64x64", override_profile=4.5,
                           _h3_face_refine_dispatch=None, _h3_face_refine_output=None):
            calls.append((resolution, _h3_face_refine_dispatch))
            if should_fail[0]:
                raise H3OomReliefRetry({"resolution": "32x32", "num_inference_steps": 2})
            return True
        cleanup = Mock()
        namespace = dict(inspect=inspect, traceback=traceback, _generate_video_impl=implementation,
            _notify_h3_profile_observer=Mock(), _release_failed_generation_resources=cleanup,
            get_default_profile=lambda output: 4.5, get_output_type_for_model=lambda model: "video")
        exec(compile(ast.Module(body=[wrapper], type_ignores=[]), "wgp.py", "exec"), namespace)
        with self.assertRaises(H3OomReliefRetry):
            namespace["generate_video"](resolution="64x64", _h3_face_refine_dispatch=dispatch)
        self.assertEqual(calls, [("64x64", dispatch)])
        cleanup.assert_called_once()
        should_fail[0] = False
        with patch("services.h3_host_limits.record_denoise_success", side_effect=AssertionError("calibrated")):
            self.assertTrue(namespace["generate_video"](_h3_face_refine_dispatch=dispatch))
        names = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "_get_generate_video_param_names")
        namespace["_generate_video_param_names"] = None
        namespace["generate_video"] = implementation
        exec(compile(ast.Module(body=[names], type_ignores=[]), "wgp.py", "exec"), namespace)
        self.assertNotIn("_h3_face_refine_dispatch", namespace["_get_generate_video_param_names"]())
        self.assertNotIn("_h3_face_refine_output", namespace["_get_generate_video_param_names"]())


if __name__ == "__main__":
    unittest.main()
