"""Continuum Director pipeline reconnect helpers.

Locks leftover 1.9.0 `get_pipeline_status` / `recovered_from_disk` probes
onto Continuum `get_pipeline`, `load_pipeline_state`, and
`restore_registered_pipeline`. Do not invent the leftover reconnect helper
or restore `get_pipeline_status`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import director_pipeline as pipeline  # noqa: E402


_PIPELINE_PATH = os.path.join(_APP, "services", "director_pipeline.py")
_LEFTOVER_NAMES = (
    "get_pipeline_status",
    "recovered_from_disk",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TestContinuumPipelineStatusGates(unittest.TestCase):
    def tearDown(self):
        pipeline._pipelines.pop("deadbeef", None)

    def test_pipeline_does_not_restore_leftover_status_helper(self):
        source = _read(_PIPELINE_PATH)

        # Leftover 1.9.0 published get_pipeline_status as a live/disk
        # reconnect helper that remapped saved "running" to failed and
        # stamped recovered_from_disk. Continuum dropped that helper.
        self.assertFalse(hasattr(pipeline, "get_pipeline_status"))
        self.assertNotIn("def get_pipeline_status(", source)
        for name in _LEFTOVER_NAMES:
            with self.subTest(leftover=name):
                self.assertNotIn(name, source)

    def test_continuum_helpers_keep_live_and_disk_without_status_remap(self):
        source = _read(_PIPELINE_PATH)
        self.assertIn("def get_pipeline(", source)
        self.assertIn("def load_pipeline_state(", source)
        self.assertIn("def restore_registered_pipeline(", source)
        self.assertIn("_recovered_without_worker", source)
        self.assertNotIn("def get_pipeline_status(", source)

    def test_saved_running_state_stays_on_disk_until_restore(self):
        with tempfile.TemporaryDirectory() as output_dir:
            state_path = os.path.join(
                output_dir,
                pipeline.pipeline_state_filename("deadbeef"),
            )
            saved = {
                "pipeline_id": "deadbeef",
                "status": "running",
                "clips": [{
                    "video_prompt": "A saved shot.",
                    "planned_clip": {
                        "start": 0.0,
                        "end": 5.5,
                        "duration_sec": 5.5,
                        "duration_frames": 132,
                    },
                    "start_image_filename": "",
                    "video_filename": "clip.mp4",
                }],
                "output_files": ["clip.mp4"],
            }
            with open(state_path, "w", encoding="utf-8") as handle:
                json.dump(saved, handle)

            loaded = pipeline.load_pipeline_state(output_dir, "deadbeef")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "running")
        self.assertNotIn("recovered_from_disk", loaded)
        self.assertEqual(loaded["clips"][0]["planned_clip"]["duration_frames"], 132)
        self.assertIsNone(pipeline.get_pipeline("deadbeef"))

    def test_live_pipeline_is_independent_of_saved_disk_snapshot(self):
        live = {
            "id": "deadbeef",
            "status": "running",
            "phase": "generating_video",
            "_planned_clips": [{
                "start": 0.0,
                "end": 10.125,
                "duration_frames": 243,
            }],
        }
        pipeline._pipelines["deadbeef"] = live

        with tempfile.TemporaryDirectory() as output_dir:
            state_path = os.path.join(
                output_dir,
                pipeline.pipeline_state_filename("deadbeef"),
            )
            with open(state_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "pipeline_id": "deadbeef",
                    "status": "failed",
                    "clips": [],
                }, handle)
            loaded = pipeline.load_pipeline_state(output_dir, "deadbeef")
            current = pipeline.get_pipeline("deadbeef")

        self.assertEqual(loaded["status"], "failed")
        self.assertEqual(current["status"], live["status"])
        self.assertEqual(current["_planned_clips"], live["_planned_clips"])
        self.assertIsNot(current, live)
        self.assertNotIn("recovered_from_disk", current)
        self.assertNotIn("planned_clips", current)

    def test_restore_registered_pipeline_uses_continuum_recovery_not_leftover_failed(self):
        with tempfile.TemporaryDirectory() as output_dir:
            state_path = os.path.join(
                output_dir,
                pipeline.pipeline_state_filename("deadbeef"),
            )
            saved = {
                "pipeline_id": "deadbeef",
                "status": "running",
                "workspace": "default",
                "_params_snapshot": {},
                "clips": [{
                    "video_prompt": "A saved shot.",
                    "planned_clip": {
                        "start": 0.0,
                        "end": 5.5,
                        "duration_frames": 132,
                    },
                    "video_filename": "clip.mp4",
                }],
                "output_files": ["clip.mp4"],
            }
            with open(state_path, "w", encoding="utf-8") as handle:
                json.dump(saved, handle)

            with patch.object(pipeline, "_start_pipeline_worker") as worker:
                restored = pipeline.restore_registered_pipeline(
                    saved,
                    state_path,
                    {"id": "parent"},
                    defer_worker=True,
                )
                worker.assert_not_called()

        self.assertEqual(restored["status"], "running")
        self.assertEqual(restored["recovery_state"], "interrupted")
        self.assertTrue(restored["_recovered_without_worker"])
        self.assertNotIn("recovered_from_disk", restored)
        self.assertEqual(restored["_planned_clips"][0]["duration_frames"], 132)
        current = pipeline.get_pipeline("deadbeef")
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["recovery_state"], "interrupted")


if __name__ == "__main__":
    unittest.main()
