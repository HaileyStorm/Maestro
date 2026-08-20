"""Model-free regressions for Director project revisions and held queue."""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_APP_DIR = os.path.join(_ROOT, "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import director_pipeline as pipeline  # noqa: E402
from services.job_lifecycle import (  # noqa: E402
    _reset_queue_state_for_tests,
    set_job_hold,
    try_requeue,
    try_start,
)
from services.tool_job_identity import (  # noqa: E402
    JOB_ID_HEX_LENGTH,
    is_unique_generation_job_id,
    new_unique_job_id,
)

_THIRTY_TWO_HEX = "c" * 32
_HEX_ID_RE = r"^[0-9a-f]+$"
_THIRTY_TWO_HEX_RE = r"^[0-9a-f]{32}$"


def _parse_launch() -> ast.Module:
    path = os.path.join(_APP_DIR, "launch.py")
    with open(path, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename="app/launch.py")


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Function {name!r} not found")


def _load_isolated_function(name: str, namespace: dict):
    function = _function(_parse_launch(), name)
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "app/launch.py", "exec"), namespace)
    return namespace[name]


class TestDirectorProjectRevisionsAndQueue(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.originals = {
            "pipelines": pipeline._pipelines,
            "wgp": pipeline._wgp,
            "queue_state": pipeline._director_queue_state,
            "queue_base": pipeline._director_queue_base,
            "queue_worker": pipeline._director_queue_worker,
        }
        pipeline._pipelines = {}
        pipeline._wgp = SimpleNamespace(save_path=self.temp_dir.name)
        pipeline._director_queue_state = None
        pipeline._director_queue_base = None
        pipeline._director_queue_worker = None

    def tearDown(self):
        pipeline._pipelines = self.originals["pipelines"]
        pipeline._wgp = self.originals["wgp"]
        pipeline._director_queue_state = self.originals["queue_state"]
        pipeline._director_queue_base = self.originals["queue_base"]
        pipeline._director_queue_worker = self.originals["queue_worker"]
        self.temp_dir.cleanup()

    def _asset(self, name: str = "reference.png") -> str:
        path = os.path.join(self.temp_dir.name, name)
        with open(path, "wb") as handle:
            handle.write(b"director-test-asset")
        return path

    def _forget_director_queue_cache(self) -> None:
        pipeline._director_queue_state = None
        pipeline._director_queue_base = None
        pipeline._director_queue_worker = None

    def _queue_path(self) -> str:
        return os.path.join(self.temp_dir.name, "_director_queue.json")

    def _read_queue_file(self) -> dict:
        with open(self._queue_path(), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_start_persists_complete_revision_before_worker(self):
        reference = self._asset()
        params = {
            "pipeline_type": "music_video",
            "scene_description": "Editable project",
            "reference_image_path": reference,
            "image_model": "image-test",
            "video_model": "video-test",
            "prepared_clip_plans": [{
                "image_prompt": "same opening image",
                "video_prompt": "same reviewed video prompt",
            }],
            "prepared_planned_clips": [{
                "start": 0.0,
                "end": 5.0,
                "section_label": "scene",
                "energy": 0.5,
                "suggested_prompt_hint": "",
                "beat_count": 4,
                "duration_frames": 81,
            }],
            "director_ui_snapshot": {
                "snapshot_version": 1,
                "directorSongStyle": "dark synthwave",
            },
        }

        with (
            patch.object(pipeline, "_validate_director_models"),
            patch.object(
                pipeline,
                "_create_director_video_execution_profile",
                return_value={"is_minimax_h3": False},
            ),
            patch.object(pipeline, "_start_pipeline_worker") as start_worker,
        ):
            pid = pipeline.start_pipeline(params)

        start_worker.assert_called_once_with(pid)
        state_path = os.path.join(
            self.temp_dir.name, f"_director_pipeline_{pid}.json",
        )
        self.assertTrue(os.path.isfile(state_path))
        with open(state_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)

        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["project_id"], pid)
        self.assertEqual(saved["director_ui_snapshot"]["directorSongStyle"], "dark synthwave")
        self.assertEqual(saved["clips"][0]["video_prompt"], "same reviewed video prompt")
        owned_reference = saved["_params_snapshot"]["reference_image_path"]
        self.assertTrue(os.path.isfile(owned_reference))
        self.assertIn(os.path.join("_director_assets", pid), owned_reference)
        self.assertEqual(
            saved["asset_manifest"]["reference_image_path"]["serve_path"],
            os.path.relpath(owned_reference, self.temp_dir.name).replace(os.sep, "/"),
        )

    def test_held_queue_owns_assets_reorders_and_removes(self):
        first = pipeline.enqueue_director_pipeline(self.temp_dir.name, {
            "scene_description": "First project",
            "pipeline_type": "music_video",
            "reference_image_path": self._asset("first.png"),
            "video_model": "video-a",
        })
        second = pipeline.enqueue_director_pipeline(self.temp_dir.name, {
            "scene_description": "Second project",
            "pipeline_type": "short_film_story",
            "reference_image_path": self._asset("second.png"),
            "video_model": "video-b",
        })

        self.assertTrue(first["paused"])
        self.assertEqual([entry["status"] for entry in second["entries"]], ["held", "held"])
        first_id, second_id = [entry["id"] for entry in second["entries"]]
        detail = pipeline.get_director_queue_entry(self.temp_dir.name, first_id)
        self.assertIsNotNone(detail)
        self.assertTrue(detail["params"]["auto_mode"])
        owned = detail["params"]["reference_image_path"]
        self.assertTrue(os.path.isfile(owned))
        self.assertIn(os.path.join("_director_queue_assets", first_id), owned)
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir.name, "_director_queue.json")))

        reordered = pipeline.reorder_director_queue(
            self.temp_dir.name, [second_id, first_id],
        )
        self.assertEqual([entry["id"] for entry in reordered["entries"]], [second_id, first_id])
        edited = pipeline.update_director_queue_entry(
            self.temp_dir.name,
            second_id,
            {
                "scene_description": "Second project, edited",
                "reference_image_path": self._asset("second-edited.png"),
            },
        )
        edited_entry = next(item for item in edited["entries"] if item["id"] == second_id)
        self.assertEqual(edited_entry["scene_description"], "Second project, edited")
        edited_detail = pipeline.get_director_queue_entry(self.temp_dir.name, second_id)
        self.assertEqual(edited_detail["params"]["scene_description"], "Second project, edited")
        self.assertIn(
            os.path.join("_director_queue_assets", second_id),
            edited_detail["params"]["reference_image_path"],
        )
        self.assertTrue(pipeline.remove_director_queue_entry(self.temp_dir.name, first_id))
        remaining = pipeline.list_director_queue(self.temp_dir.name)
        self.assertEqual([entry["id"] for entry in remaining["entries"]], [second_id])

    def test_restart_returns_running_entry_to_held_queue(self):
        path = self._queue_path()
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({
                "version": 1,
                "paused": False,
                "running": True,
                "entries": [{
                    "id": _THIRTY_TWO_HEX,
                    "status": "running",
                    "message": "Rendering",
                    "pipeline_id": "live-worker",
                    "params": {"scene_description": "Overnight project"},
                }],
            }, handle)

        self._forget_director_queue_cache()
        restored = pipeline.list_director_queue(self.temp_dir.name)
        self.assertTrue(restored["paused"])
        self.assertFalse(restored["running"])
        self.assertEqual(restored["entries"][0]["id"], _THIRTY_TWO_HEX)
        self.assertEqual(restored["entries"][0]["status"], "held")
        self.assertIn("Interrupted", restored["entries"][0]["message"])
        self.assertIsNone(restored["entries"][0].get("pipeline_id"))

        persisted = self._read_queue_file()
        self.assertTrue(persisted["paused"])
        self.assertFalse(persisted["running"])
        self.assertEqual(persisted["entries"][0]["id"], _THIRTY_TWO_HEX)
        self.assertEqual(persisted["entries"][0]["status"], "held")
        self.assertIsNone(persisted["entries"][0]["pipeline_id"])

        self._forget_director_queue_cache()
        reloaded = pipeline.list_director_queue(self.temp_dir.name)
        self.assertTrue(reloaded["paused"])
        self.assertFalse(reloaded["running"])
        self.assertEqual(reloaded["entries"][0]["status"], "held")
        self.assertEqual(reloaded["entries"][0]["id"], _THIRTY_TWO_HEX)

    def test_enqueue_persists_complete_project_across_reload(self):
        queued = pipeline.enqueue_director_pipeline(self.temp_dir.name, {
            "scene_description": "Frozen overnight project",
            "pipeline_type": "music_video",
            "reference_image_path": self._asset("persist.png"),
        })
        entry_id = queued["entries"][0]["id"]
        self.assertRegex(entry_id, _HEX_ID_RE)
        self.assertTrue(queued["paused"])
        self.assertEqual(queued["entries"][0]["status"], "held")
        self.assertTrue(os.path.isfile(self._queue_path()))

        persisted = self._read_queue_file()
        self.assertEqual(persisted["entries"][0]["id"], entry_id)
        self.assertEqual(persisted["entries"][0]["status"], "held")
        self.assertEqual(
            persisted["entries"][0]["params"]["scene_description"],
            "Frozen overnight project",
        )

        self._forget_director_queue_cache()
        restored = pipeline.list_director_queue(self.temp_dir.name)
        self.assertEqual([entry["id"] for entry in restored["entries"]], [entry_id])
        self.assertEqual(restored["entries"][0]["status"], "held")
        detail = pipeline.get_director_queue_entry(self.temp_dir.name, entry_id)
        self.assertEqual(detail["params"]["scene_description"], "Frozen overnight project")

    def test_start_director_queue_persists_held_projects_as_queued(self):
        queued = pipeline.enqueue_director_pipeline(self.temp_dir.name, {
            "scene_description": "Start this batch",
        })
        entry_id = queued["entries"][0]["id"]
        fake_thread = SimpleNamespace(is_alive=lambda: False, start=lambda: None)

        with patch.object(pipeline.threading, "Thread", return_value=fake_thread):
            started = pipeline.start_director_queue(self.temp_dir.name)

        self.assertFalse(started["paused"])
        self.assertTrue(started["running"])
        self.assertEqual(started["entries"][0]["id"], entry_id)
        self.assertEqual(started["entries"][0]["status"], "queued")
        persisted = self._read_queue_file()
        self.assertFalse(persisted["paused"])
        self.assertEqual(persisted["entries"][0]["status"], "queued")
        self.assertEqual(persisted["entries"][0]["id"], entry_id)

    def test_queue_dispatches_complete_projects_sequentially(self):
        pipeline.enqueue_director_pipeline(self.temp_dir.name, {
            "scene_description": "One",
        })
        pipeline.enqueue_director_pipeline(self.temp_dir.name, {
            "scene_description": "Two",
        })
        with pipeline._director_queue_lock:
            state = pipeline._load_director_queue_locked(self.temp_dir.name)
            state["paused"] = False
            for entry in state["entries"]:
                entry["status"] = "queued"

        started = []

        def fake_start(params):
            pid = f"render-{len(started) + 1}"
            started.append((pid, params["scene_description"]))
            return pid

        with (
            patch.object(pipeline, "start_pipeline", side_effect=fake_start),
            patch.object(
                pipeline,
                "get_pipeline",
                side_effect=lambda pid: {"status": "completed", "error": None},
            ),
        ):
            pipeline._run_director_queue(self.temp_dir.name)

        self.assertEqual(started, [("render-1", "One"), ("render-2", "Two")])
        queue = pipeline.list_director_queue(self.temp_dir.name)
        self.assertFalse(queue["running"])
        self.assertEqual(
            [entry["status"] for entry in queue["entries"]],
            ["completed", "completed"],
        )

    def test_queue_waits_behind_direct_pipeline_before_dispatch(self):
        queued = pipeline.enqueue_director_pipeline(self.temp_dir.name, {
            "scene_description": "Next revision",
        })
        entry_id = queued["entries"][0]["id"]
        with pipeline._director_queue_lock:
            state = pipeline._load_director_queue_locked(self.temp_dir.name)
            state["paused"] = False
            state["entries"][0]["status"] = "queued"
        pipeline._pipelines["manual-run"] = {"status": "running"}
        started = []

        def finish_active(_seconds):
            self.assertEqual(started, [])
            pipeline._pipelines["manual-run"]["status"] = "completed"

        with (
            patch.object(pipeline.time, "sleep", side_effect=finish_active),
            patch.object(
                pipeline,
                "start_pipeline",
                side_effect=lambda params: started.append(params["scene_description"]) or "queued-run",
            ),
            patch.object(
                pipeline,
                "get_pipeline",
                return_value={"status": "completed", "error": None},
            ),
        ):
            pipeline._run_director_queue(self.temp_dir.name)

        self.assertEqual(started, ["Next revision"])
        detail = pipeline.get_director_queue_entry(self.temp_dir.name, entry_id)
        self.assertEqual(detail["status"], "completed")

    def test_queue_surfaces_pipeline_gpu_wait_message(self):
        queued = pipeline.enqueue_director_pipeline(self.temp_dir.name, {
            "scene_description": "Director after Studio",
        })
        entry_id = queued["entries"][0]["id"]
        with pipeline._director_queue_lock:
            state = pipeline._load_director_queue_locked(self.temp_dir.name)
            state["paused"] = False
            state["entries"][0]["status"] = "queued"

        statuses = iter([
            {
                "status": "running",
                "progress": {
                    "message": "Waiting for GPU (generation queue)...",
                },
            },
            {"status": "completed", "error": None},
        ])

        def observe_wait(_seconds):
            detail = pipeline.get_director_queue_entry(
                self.temp_dir.name, entry_id,
            )
            self.assertEqual(
                detail["message"],
                "Waiting for GPU (generation queue)...",
            )

        with (
            patch.object(pipeline, "start_pipeline", return_value="queued-run"),
            patch.object(pipeline, "get_pipeline", side_effect=lambda _pid: next(statuses)),
            patch.object(pipeline.time, "sleep", side_effect=observe_wait),
        ):
            pipeline._run_director_queue(self.temp_dir.name)

        detail = pipeline.get_director_queue_entry(self.temp_dir.name, entry_id)
        self.assertEqual(detail["status"], "completed")

    def test_prepared_queue_revision_reaches_gpu_wait(self):
        """A frozen Director revision can enter the normal GPU waiter."""

        pid = "prepared-queue-run"
        pipeline._pipelines[pid] = {
            "id": pid,
            "status": "running",
            "phase": "planning",
            "params": {
                "pipeline_type": "music_video",
                "prepared_clip_plans": [{
                    "image_prompt": "reviewed opening frame",
                    "video_prompt": "reviewed Tom Cruise shot",
                }],
                "prepared_planned_clips": [{
                    "start": 0.0,
                    "end": 5.0,
                    "duration_frames": 81,
                }],
            },
            "out_dir": self.temp_dir.name,
            "workspace": "default",
            "clip_plans": [],
            "clip_images": [],
            "output_files": [],
            "progress": {},
        }

        # Returning False models cancellation after entering the normal GPU
        # waiter and deliberately stops before LLM or model work. The frozen
        # prepared plans must be copied successfully before that point.
        with patch.object(pipeline, "_wait_for_gpu", return_value=False) as wait:
            pipeline._run_pipeline(pid)

        wait.assert_called_once_with(pid)
        self.assertNotEqual(pipeline._pipelines[pid]["status"], "failed")
        self.assertIsNone(pipeline._pipelines[pid].get("error"))

    def test_gpu_wait_blocks_until_studio_generation_finishes(self):
        pid = "director-behind-studio"
        pipeline._pipelines[pid] = {
            "status": "running",
            "progress": {},
        }
        studio_job = {"status": "running"}

        def finish_studio(_seconds):
            studio_job["status"] = "completed"

        with (
            patch.object(pipeline, "_jobs", {"studio-job": studio_job}),
            patch.object(pipeline.time, "sleep", side_effect=finish_studio) as sleep,
        ):
            ready = pipeline._wait_for_gpu(pid, poll_interval=0.01)

        self.assertTrue(ready)
        sleep.assert_called_once_with(0.01)
        self.assertEqual(
            pipeline._pipelines[pid]["progress"]["message"],
            "Waiting for GPU (generation queue)...",
        )


class TestContinuumHoldQueueContracts(unittest.TestCase):
    """Studio/Continuum holds use queue_held, not leftover status==held."""

    def setUp(self):
        _reset_queue_state_for_tests()

    def tearDown(self):
        _reset_queue_state_for_tests()

    def _queued_hold(self, job_id: str | None = None) -> dict:
        return {
            "id": job_id or new_unique_job_id(),
            "status": "queued",
            "queue_held": True,
            "message": "Ready - waiting for Start Queue",
            "created_at": 10,
        }

    def test_generation_job_ids_are_32_hex(self):
        minted = new_unique_job_id()
        self.assertTrue(is_unique_generation_job_id(minted))
        self.assertRegex(minted, _THIRTY_TWO_HEX_RE)
        self.assertEqual(len(minted), JOB_ID_HEX_LENGTH)
        self.assertFalse(is_unique_generation_job_id("deadbeef"))
        self.assertFalse(is_unique_generation_job_id("held"))

        mint = _function(_parse_launch(), "_new_generation_job_id")
        launch_path = os.path.join(_APP_DIR, "launch.py")
        with open(launch_path, encoding="utf-8") as handle:
            launch_source = handle.read()
        source = ast.get_source_segment(launch_source, mint)
        self.assertIsNotNone(source)
        self.assertIn("uuid.uuid4().hex", source)
        self.assertNotIn("status == \"held\"", source)

    def test_set_job_hold_keeps_queued_status_and_toggles_queue_held(self):
        job = self._queued_hold()
        self.assertEqual(set_job_hold(job, True), "held")
        self.assertEqual(job["status"], "queued")
        self.assertTrue(job["queue_held"])
        self.assertNotEqual(job["status"], "held")

        self.assertEqual(set_job_hold(job, False), "resumed")
        self.assertEqual(job["status"], "queued")
        self.assertFalse(job["queue_held"])
        self.assertNotEqual(job["status"], "held")

    def test_restart_converts_running_work_to_queued_hold(self):
        job = {
            "id": new_unique_job_id(),
            "status": "queued",
            "queue_held": False,
            "message": "Queued",
        }
        self.assertTrue(try_start(job))
        self.assertEqual(job["status"], "running")
        self.assertTrue(try_requeue(job, queue_held=True, message="Interrupted"))
        self.assertEqual(job["status"], "queued")
        self.assertTrue(job["queue_held"])
        self.assertNotEqual(job["status"], "held")
        self.assertRegex(job["id"], _THIRTY_TWO_HEX_RE)

    def test_start_studio_queue_releases_32_hex_holds_via_set_job_hold(self):
        earlier = self._queued_hold()
        later = self._queued_hold()
        later["created_at"] = 20
        running = {
            "id": new_unique_job_id(),
            "status": "running",
            "queue_held": False,
            "created_at": 5,
        }
        leftover_status = {
            "id": new_unique_job_id(),
            "status": "held",
            "queue_held": False,
            "created_at": 1,
        }
        jobs = {
            earlier["id"]: earlier,
            later["id"]: later,
            running["id"]: running,
            leftover_status["id"]: leftover_status,
        }
        released_ids = []

        def set_hold(job, held):
            if job.get("queue_held") is not True or held is not False:
                return None
            if job.get("status") == "held":
                raise AssertionError("Start Queue must not key off status==held")
            job["queue_held"] = False
            released_ids.append(job["id"])
            return "resumed"

        start_queue = _load_isolated_function(
            "start_studio_queue",
            {
                "api": SimpleNamespace(
                    post=lambda *_args, **_kwargs: (lambda function: function),
                ),
                "Request": object,
                "Response": object,
                "_jobs": jobs,
                "_set_recovery_no_store": lambda _response: None,
                "_require_remote_queue_project": lambda _request: None,
                "_require_generic_queue_control_job": (
                    lambda job_id, _request: jobs[job_id]
                ),
                "_queue_recovery_delivery_pending": lambda _job: None,
                "_require_job_runtime_model_admission": lambda _job: None,
                "set_job_hold": set_hold,
                "HTTPException": RuntimeError,
            },
        )

        result = start_queue(SimpleNamespace(), SimpleNamespace())
        self.assertEqual(set(result["released"]), {earlier["id"], later["id"]})
        self.assertEqual(set(result["job_ids"]), {earlier["id"], later["id"]})
        self.assertTrue(all(is_unique_generation_job_id(job_id) for job_id in result["job_ids"]))
        self.assertFalse(earlier["queue_held"])
        self.assertFalse(later["queue_held"])
        self.assertEqual(earlier["status"], "queued")
        self.assertEqual(later["status"], "queued")
        self.assertEqual(running["status"], "running")
        self.assertEqual(leftover_status["status"], "held")
        self.assertNotIn(leftover_status["id"], released_ids)
        self.assertNotIn(running["id"], released_ids)

    def test_start_studio_queue_source_calls_set_job_hold(self):
        start_queue = _function(_parse_launch(), "start_studio_queue")
        called = set()
        for child in ast.walk(start_queue):
            if not isinstance(child, ast.Call):
                continue
            if isinstance(child.func, ast.Name):
                called.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                called.add(child.func.attr)
        self.assertIn("set_job_hold", called)
        self.assertNotIn("release_held", called)
        constants = {
            node.value
            for node in ast.walk(start_queue)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertIn("queue_held", constants)
        self.assertNotIn("release_held", constants)


if __name__ == "__main__":
    unittest.main()
