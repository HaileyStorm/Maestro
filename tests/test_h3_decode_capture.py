"""CPU evidence checks for an opt-in H3 observation, never a quality claim."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.h3_decode_capture import (
    H3DecodeCapture, PLAN_ENV, begin_decode_capture, capture_for_job, notify_decode_capture,
)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifacts = self.root / ".artifacts-temp"
        self.artifacts.mkdir()
        self.params = {"model_type": "minimax_h3", "seed": 17,
                       "resolution": "1344x768", "video_length": 124,
                       "num_inference_steps": 28}
        self.identity = {"job_id": "job-one", "task_index": 0, **self.params}
        self.plan = {"schema_version": 1, "match": self.identity,
                     "directory": ".artifacts-temp/capture", "revision": "revision-one"}
        self.plan_path = self.artifacts / "plan.json"
        self.plan_path.write_text(json.dumps(self.plan))

    def select(self, *, job_id="job-one", task_index=0, params=None, environ=None):
        with patch("services.h3_decode_capture.subprocess.run") as run:
            run.return_value.stdout = "revision-one\n"
            return capture_for_job(root=self.root, job_id=job_id, task_index=task_index,
                                   params=self.params if params is None else params,
                                   environ={PLAN_ENV: str(self.plan_path)} if environ is None else environ)

    def collector(self):
        directory = self.artifacts / "direct"
        directory.mkdir()
        return H3DecodeCapture(directory, self.identity)

    def test_disabled_selector_has_no_filesystem_or_subprocess_work(self):
        with patch("services.h3_decode_capture.subprocess.run") as run:
            self.assertIsNone(capture_for_job(root=self.root, job_id="job-one", task_index=0,
                                             params=self.params, environ={}))
            run.assert_not_called()

    def test_exact_job_task_and_recipe_isolation(self):
        self.assertIsNone(self.select(job_id="other"))
        self.assertIsNone(self.select(task_index=1))
        for key, value in (("seed", 18), ("video_length", 243), ("model_type", "other"),
                           ("resolution", "768x1344"), ("num_inference_steps", 20),
                           ("repeat_generation", 2), ("batch_size", 2)):
            with self.subTest(key=key):
                self.assertIsNone(self.select(params={**self.params, key: value}))
        self.assertIsInstance(self.select(), H3DecodeCapture)
        # Replays cannot overwrite the retained capture.
        self.assertIsNone(self.select())

    def test_stale_revision_and_outside_plan_are_disabled(self):
        self.plan["revision"] = "older"
        self.plan_path.write_text(json.dumps(self.plan))
        self.assertIsNone(self.select())
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(self.plan))
        self.assertIsNone(self.select(environ={PLAN_ENV: str(outside)}))

    def test_output_escape_and_symlink_escape_are_rejected(self):
        for directory in ("outside", ".artifacts-temp/../outside", ".artifacts-temp"):
            self.plan["directory"] = directory
            self.plan_path.write_text(json.dumps(self.plan))
            self.assertIsNone(self.select())
        (self.artifacts / "link").symlink_to(self.root, target_is_directory=True)
        self.plan["directory"] = ".artifacts-temp/link/outside"
        self.plan_path.write_text(json.dumps(self.plan))
        self.assertIsNone(self.select())

    def test_malformed_or_oversized_plan_does_not_raise(self):
        for content in ("not json", "[]", "x" * 8193):
            self.plan_path.write_text(content)
            self.assertIsNone(self.select())

    def test_latent_snapshot_is_independent_cpu_storage(self):
        original = torch.arange(24.).reshape(1, 24, 1, 1, 1)
        before = original.clone()
        seen = []
        def observer(stage, payload):
            seen.append((stage, str(payload["tensor"].device)))
            payload["tensor"].zero_()
        notify_decode_capture(observer, "normalized_latents", original)
        self.assertTrue(torch.equal(before, original))
        self.assertEqual(seen, [("normalized_latents", "cpu")])

    def test_raw_vae_and_encoder_ranges_agree_with_model_pixels(self):
        from models.minimax_h3.packing import MINIMAX_H3_PIXEL_MEAN, MINIMAX_H3_PIXEL_STD
        rgb = torch.full((3, 2, 4, 4), 200 / 255)
        mean = torch.tensor(MINIMAX_H3_PIXEL_MEAN).view(3, 1, 1, 1)
        std = torch.tensor(MINIMAX_H3_PIXEL_STD).view(3, 1, 1, 1)
        reports = []
        callback = lambda stage, payload: reports.append(payload)
        notify_decode_capture(callback, "raw_vae", ((rgb - mean) / std)[None], convention="vae")
        notify_decode_capture(callback, "model_output", rgb.mul(2).sub(1))
        notify_decode_capture(callback, "encoder_input", [torch.full((3, 2, 4, 4), 200, dtype=torch.uint8)], convention="encoder")
        for report in reports:
            self.assertAlmostEqual(report["frames"][0]["rgb_mean_255"], 200, places=3)
            self.assertEqual(report["frames"][0]["below_zero_fraction"], 0)
            self.assertEqual(report["frames"][0]["above_one_fraction"], 0)
            self.assertEqual(report["thumbnails"][0].shape, (36, 64, 3))
        self.assertTrue(torch.equal(rgb, torch.full_like(rgb, 200 / 255)))

    def test_complete_capture_and_retained_latents(self):
        capture = self.collector()
        begin_decode_capture(capture, {"repeat_index": 0, "window_index": 1, "seed": 17,
                                     "frames": 124, "width": 1344, "height": 768, "fps": 24})
        latents = torch.arange(24.).reshape(1, 24, 1, 1, 1)
        notify_decode_capture(capture, "normalized_latents", latents)
        latents.zero_()
        for stage in ("raw_vae", "model_output", "encoder_input"):
            notify_decode_capture(capture, stage, torch.zeros(3, 2, 4, 4))
        capture.finalize()
        manifest = json.loads((capture.directory / "manifest.json").read_text())
        self.assertTrue(manifest["complete"])
        retained = torch.load(capture.directory / "normalized_latents.pt", weights_only=True)
        self.assertEqual(float(retained.sum()), 276)
        self.assertNotIn("prompt", json.dumps(manifest))
        self.assertTrue((capture.directory / "raw_vae.png").exists())

    def test_execution_geometry_or_window_mismatch_invalidates_capture(self):
        capture = self.collector()
        context = {"repeat_index": 0, "window_index": 1, "seed": 17,
                   "frames": 124, "width": 1344, "height": 768, "fps": 24}
        for key, value in (("repeat_index", 1), ("window_index", 2), ("seed", 18),
                           ("frames", 128), ("width", 608)):
            begin_decode_capture(capture, {**context, key: value})
        self.assertEqual(len(capture.manifest["errors"]), 5)
        self.assertNotIn("execution", capture.manifest)

    def test_failed_missing_repeated_or_out_of_order_stage_is_incomplete(self):
        capture = self.collector()
        notify_decode_capture(capture, "raw_vae", torch.zeros(3, 2, 4, 4))
        capture.finalize()
        self.assertFalse(capture.manifest["complete"])
        self.assertEqual(capture.manifest["errors"][0]["stage"], "raw_vae")
        notify_decode_capture(capture, "normalized_latents", torch.zeros(1, 24, 1, 1, 1))
        notify_decode_capture(capture, "normalized_latents", torch.zeros(1, 24, 1, 1, 1))
        capture.finalize()
        self.assertFalse(capture.manifest["complete"])

    def test_disabled_and_failing_hook_leave_native_decode_identical(self):
        from models.minimax_h3.minimax_h3_main import _decode_h3_video_rows
        class VAE:
            def decode(self, tensor, *, return_dict):
                return (torch.zeros(1, 3, 5, 64, 96),)
        arguments = dict(vae=VAE(), device=torch.device("cpu"),
                         packed_rows=torch.zeros(12, 96), latent_frames=2,
                         latent_height=4, latent_width=6, pixel_frames=5,
                         pixel_height=64, pixel_width=96, channels=24, patch_size=(1, 2, 2))
        baseline = _decode_h3_video_rows(**arguments)
        def failing(*_args):
            raise RuntimeError("synthetic capture failure")
        observed = _decode_h3_video_rows(**arguments, observer=failing)
        self.assertTrue(all(torch.equal(left, right) for left, right in zip(baseline, observed)))

    def test_only_final_decode_and_private_worker_receive_hook(self):
        main = ast.parse((ROOT / "app/models/minimax_h3/minimax_h3_main.py").read_text())
        attached = [node for node in ast.walk(main) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "_decode_h3_video_rows"
                    and any(key.arg == "observer" for key in node.keywords)]
        self.assertEqual(len(attached), 1)
        self.assertGreater(attached[0].lineno, 2000)
        wgp = (ROOT / "app/wgp.py").read_text()
        metadata = wgp[wgp.index("inputs = get_function_arguments(generate_video, locals())"):]
        self.assertLess(metadata.index('inputs.update(overridden_inputs)'),
                        metadata.index('inputs.pop("_h3_decode_observer", None)'))
        launch = (ROOT / "app/launch.py").read_text()
        start = launch.index('filtered_params.pop("_h3_decode_observer", None)')
        factory = launch.index('decode_observation = capture_for_job(', start)
        generate = launch.index('lambda: wgp.generate_video(', factory)
        self.assertLess(start, factory)
        self.assertLess(factory, generate)


if __name__ == "__main__":
    unittest.main()
