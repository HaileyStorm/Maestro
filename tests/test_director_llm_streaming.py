"""Model-free tests for pipeline-scoped Director LLM/VLM telemetry."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import director_pipeline as pipeline  # noqa: E402
from services import llm_service  # noqa: E402
from services.director import prompt_polish  # noqa: E402


class DirectorLlmStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.originals = {
            "pipelines": pipeline._pipelines,
            "contexts": pipeline._pipeline_llm_contexts,
            "tokens": pipeline._pipeline_llm_tokens,
            "jobs": pipeline._jobs,
            "active": pipeline._active_gen_states,
            "wgp": pipeline._wgp,
            "stream": llm_service._stream_buffer,
            "last_system": llm_service._last_system_prompt,
            "last_user": llm_service._last_user_prompt,
            "last_thinking": llm_service._last_thinking_text,
        }
        pipeline._pipelines = {}
        pipeline._pipeline_llm_contexts = {}
        pipeline._pipeline_llm_tokens = {}
        pipeline._jobs = {}
        pipeline._active_gen_states = {}
        pipeline._wgp = SimpleNamespace(
            save_path=self.temporary.name,
            server_config={"services": {}},
        )
        # Deliberately unrelated legacy singleton state. Director must neither
        # read it nor clear it when request-scoped callbacks are active.
        llm_service._stream_buffer = "UNRELATED_SINGLETON_STREAM"
        llm_service._last_system_prompt = "UNRELATED_SINGLETON_SYSTEM"
        llm_service._last_user_prompt = "UNRELATED_SINGLETON_USER"
        llm_service._last_thinking_text = "UNRELATED_SINGLETON_THINKING"

    def tearDown(self):
        pipeline._pipelines = self.originals["pipelines"]
        pipeline._pipeline_llm_contexts = self.originals["contexts"]
        pipeline._pipeline_llm_tokens = self.originals["tokens"]
        pipeline._jobs = self.originals["jobs"]
        pipeline._active_gen_states = self.originals["active"]
        pipeline._wgp = self.originals["wgp"]
        llm_service._stream_buffer = self.originals["stream"]
        llm_service._last_system_prompt = self.originals["last_system"]
        llm_service._last_user_prompt = self.originals["last_user"]
        llm_service._last_thinking_text = self.originals["last_thinking"]
        self.temporary.cleanup()

    def _add_pipeline(self, pid: str, *, status: str = "running") -> dict:
        record = {
            "id": pid,
            "status": status,
            "phase": "planning",
            "progress": {
                "current": 0, "total": 1, "message": "Planning",
                "step": 0, "total_steps": 0,
            },
            "clip_plans": [],
            "clip_images": [],
            "output_files": [],
            "created_at": time.time(),
            "params": {"pipeline_type": "music_video"},
            "pause_reason": None,
            "out_dir": self.temporary.name,
        }
        pipeline._pipelines[pid] = record
        return record

    def test_progress_is_bounded_retry_scoped_and_ignores_singleton(self):
        record = self._add_pipeline("scoped")
        pipeline._pipeline_llm_contexts["scoped"] = {
            "response_assist": {"retry_on_refusal": True},
        }

        def fake_generate(*, progress_callback, response_assist):
            self.assertTrue(response_assist["retry_on_refusal"])
            progress_callback({
                "phase": "generating", "text": "a" * 9000,
                "attempt": 1, "generated_tokens_approx": 2250,
                "elapsed_seconds": 2.0, "live_tps": 20.0,
                "average_tps": None, "done": False,
            })
            self.assertEqual(
                len(record["llm_progress"]["partial_text"]),
                pipeline._DIRECTOR_LLM_PARTIAL_LIMIT,
            )
            progress_callback({
                "phase": "retrying", "text": "",
                "attempt": 2, "generated_tokens_approx": 0,
                "elapsed_seconds": 2.1, "live_tps": None,
                "average_tps": None, "done": False,
            })
            self.assertEqual(record["llm_progress"]["partial_text"], "")
            self.assertEqual(record["llm_progress"]["generated_tokens_approx"], 0)
            progress_callback({
                "phase": "generating", "text": "accepted",
                "attempt": 2, "generated_tokens_approx": 2,
                "elapsed_seconds": 3.0, "live_tps": 8.0,
                "average_tps": None, "done": False,
            })
            progress_callback({
                "phase": "complete", "text": "accepted",
                "attempt": 2, "generated_tokens_approx": 2,
                "elapsed_seconds": 4.0, "live_tps": None,
                "average_tps": 7.5, "done": True,
            })
            return "accepted"

        result = pipeline._pipeline_llm_call(
            "scoped", "planning", "pass_1", fake_generate,
        )
        self.assertEqual(result, "accepted")
        progress = record["llm_progress"]
        self.assertEqual(progress["pass"], "pass_1")
        self.assertEqual(progress["attempt"], 2)
        self.assertEqual(progress["attempt_limit"], 2)
        self.assertEqual(progress["average_tps"], 7.5)
        self.assertEqual(progress["partial_text"], "")
        self.assertTrue(progress["done"])
        self.assertEqual(llm_service._stream_buffer, "UNRELATED_SINGLETON_STREAM")
        self.assertNotIn("UNRELATED_SINGLETON", json.dumps(record))

    def test_concurrent_pipelines_do_not_cross_publish(self):
        first = self._add_pipeline("first")
        second = self._add_pipeline("second")
        barrier = threading.Barrier(2)

        def run(pid: str, marker: str):
            def fake(*, progress_callback):
                barrier.wait(timeout=2)
                progress_callback({
                    "phase": "generating", "text": marker,
                    "attempt": 1, "generated_tokens_approx": 2,
                    "elapsed_seconds": 0.2, "live_tps": 10.0,
                    "done": False,
                })
                time.sleep(0.01)
                return marker
            pipeline._pipeline_llm_call(
                pid, "planning", f"{pid}_pass", fake,
            )

        threads = [
            threading.Thread(target=run, args=("first", "FIRST_ONLY")),
            threading.Thread(target=run, args=("second", "SECOND_ONLY")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(first["llm_progress"]["pass"], "first_pass")
        self.assertEqual(second["llm_progress"]["pass"], "second_pass")
        self.assertNotIn("SECOND_ONLY", json.dumps(first))
        self.assertNotIn("FIRST_ONLY", json.dumps(second))

    def test_stale_callback_and_terminal_pipeline_updates_are_rejected(self):
        record = self._add_pipeline("stale")
        _old_token, old_callback = pipeline._begin_pipeline_llm_pass(
            "stale", phase="planning", pass_name="old", attempt_limit=1,
        )
        _new_token, new_callback = pipeline._begin_pipeline_llm_pass(
            "stale", phase="planning", pass_name="new", attempt_limit=1,
        )
        old_callback({
            "phase": "generating", "text": "STALE_TEXT", "attempt": 1,
            "generated_tokens_approx": 3, "elapsed_seconds": 1,
            "live_tps": 3, "done": False,
        })
        self.assertEqual(record["llm_progress"]["pass"], "new")
        self.assertEqual(record["llm_progress"]["partial_text"], "")
        record["status"] = "completed"
        new_callback({
            "phase": "generating", "text": "LATE_TEXT", "attempt": 1,
            "generated_tokens_approx": 3, "elapsed_seconds": 1,
            "live_tps": 3, "done": False,
        })
        self.assertEqual(record["llm_progress"]["partial_text"], "")

    def test_stop_clears_partial_and_makes_callback_inert(self):
        record = self._add_pipeline("stop")
        _token, callback = pipeline._begin_pipeline_llm_pass(
            "stop", phase="planning", pass_name="active", attempt_limit=1,
        )
        callback({
            "phase": "generating", "text": "VISIBLE_BEFORE_STOP",
            "attempt": 1, "generated_tokens_approx": 4,
            "elapsed_seconds": 0.5, "live_tps": 8.0, "done": False,
        })
        self.assertTrue(pipeline.stop_pipeline("stop"))
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["llm_progress"]["activity"], "cancelled")
        self.assertEqual(record["llm_progress"]["partial_text"], "")
        callback({
            "phase": "generating", "text": "LATE_AFTER_STOP",
            "attempt": 1, "generated_tokens_approx": 8,
            "elapsed_seconds": 1.0, "live_tps": 8.0, "done": False,
        })
        self.assertNotIn("LATE_AFTER_STOP", json.dumps(record))

    def test_stop_prevents_next_request_in_composite_polish(self):
        self._add_pipeline("stop-composite")
        calls = []

        def fake_enhance_prompt(**kwargs):
            calls.append(kwargs["mode"])
            if len(calls) == 1:
                self.assertTrue(pipeline.stop_pipeline("stop-composite"))
            return kwargs["prompt"]

        clip_plans = [{
            "video_prompt": "synthetic video prompt",
            "image_prompt": "synthetic image prompt",
        }]
        with mock.patch.object(
            llm_service, "enhance_prompt", side_effect=fake_enhance_prompt,
        ):
            with self.assertRaises(pipeline._DirectorLlmCancelled):
                pipeline._pipeline_llm_call(
                    "stop-composite",
                    "polishing_prompts",
                    "third_pass_polish",
                    prompt_polish.polish_prompts_third_pass,
                    clip_plans,
                    "ltx2_22B_distilled_1_1",
                    "flux2_klein_9b",
                    polish_video_prompts=True,
                    polish_image_prompts=True,
                    liveness_kwarg="is_active",
                )

        self.assertEqual(calls, ["video"])

    def test_checkpoint_omits_transient_and_raw_llm_content(self):
        record = self._add_pipeline("durable")
        record["llm_progress"] = {
            "partial_text": "EPHEMERAL_PARTIAL", "done": False,
        }
        record["_llm_log"] = {
            "system_prompt": "RAW_SYSTEM", "response_text": "RAW_RESPONSE",
        }
        self.assertTrue(pipeline._save_pipeline_state("durable"))
        state_path = Path(self.temporary.name) / pipeline.pipeline_state_filename(
            "durable",
        )
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertNotIn("llm_progress", saved)
        self.assertIsNone(saved["llm_log"])
        serialized = json.dumps(saved)
        self.assertNotIn("EPHEMERAL_PARTIAL", serialized)
        self.assertNotIn("RAW_SYSTEM", serialized)
        self.assertNotIn("RAW_RESPONSE", serialized)

    def test_restored_pipeline_starts_with_empty_transient_state(self):
        data = {
            "pipeline_id": "restored",
            "status": "paused",
            "phase": "paused",
            "created_at": time.time(),
            "workspace": "default",
            "_params_snapshot": {"auto_mode": False},
            "clips": [],
            "output_files": [],
            "llm_log": {"response_text": "HISTORICAL_RAW"},
            "llm_progress": {"partial_text": "STALE_PARTIAL"},
        }
        state_path = Path(self.temporary.name) / pipeline.pipeline_state_filename(
            "restored",
        )
        state_path.write_text(json.dumps(data), encoding="utf-8")
        restored = pipeline.restore_registered_pipeline(
            data,
            str(state_path),
            {"inputs": []},
            defer_worker=True,
        )
        self.assertIsNone(restored["llm_progress"])
        self.assertNotIn("_llm_log", restored)
        self.assertNotIn("HISTORICAL_RAW", json.dumps(restored))
        self.assertNotIn("STALE_PARTIAL", json.dumps(restored))

    def test_exact_selection_lease_wraps_pipeline_call(self):
        self._add_pipeline("lease")
        selection = {
            "model_id": "synthetic-model", "device": "cpu",
            "provider": "local", "remote_url": "", "api_key": "",
            "local_gguf_path": "", "gguf_file_override": "",
        }
        pipeline._pipeline_llm_contexts["lease"] = {
            "selection": selection,
            "response_assist": None,
        }
        observed = []

        @contextmanager
        def fake_lease(**kwargs):
            observed.append(dict(kwargs))
            yield ("synthetic",)

        def fake_generate(*, progress_callback):
            progress_callback({
                "phase": "complete", "text": "done", "attempt": 1,
                "generated_tokens_approx": 1, "elapsed_seconds": 0.1,
                "average_tps": 10.0, "done": True,
            })
            return "done"

        with mock.patch.object(llm_service, "loaded_model_lease", fake_lease):
            self.assertEqual(
                pipeline._pipeline_llm_call(
                    "lease", "planning", "leased_pass", fake_generate,
                ),
                "done",
            )
        self.assertEqual(observed, [selection])

    def test_third_pass_forwards_callback_and_response_assist(self):
        calls = []
        callback = mock.Mock()

        def fake_enhance(**kwargs):
            calls.append(kwargs)
            return kwargs["prompt"]

        plans = [{
            "video_prompt": "A synthetic subject moves.",
            "image_prompt": "A synthetic still frame.",
        }]
        with mock.patch.object(
            llm_service, "enhance_prompt", side_effect=fake_enhance,
        ):
            result = prompt_polish.polish_prompts_third_pass(
                plans,
                "ltx2_22B_distilled_1_1",
                "flux2_klein_9b",
                response_assist={"retry_on_refusal": True},
                progress_callback=callback,
            )
        self.assertIs(result, plans)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIs(call["progress_callback"], callback)
            self.assertEqual(
                call["response_assist"], {"retry_on_refusal": True},
            )

    def test_legacy_planner_forwards_progress_but_bypasses_assistance(self):
        record = self._add_pipeline("legacy")
        pipeline._pipeline_llm_contexts["legacy"] = {
            "response_assist": {"retry_on_refusal": True},
        }
        observed = {}

        def fake_plan(**kwargs):
            observed.update(kwargs)
            kwargs["progress_callback"]({
                "phase": "complete", "text": "[]", "attempt": 1,
                "generated_tokens_approx": 1, "elapsed_seconds": 0.1,
                "average_tps": 10.0, "done": True,
            })
            return []

        with mock.patch.object(
            llm_service, "plan_clip_prompts_and_images", side_effect=fake_plan,
        ):
            result, _clips = pipeline._run_planning_legacy(
                "legacy",
                {
                    "scene_description": "synthetic",
                    "planned_clips": [{"start": 0, "end": 1}],
                },
                "music_video",
            )
        self.assertEqual(result, [])
        self.assertTrue(callable(observed.get("progress_callback")))
        self.assertNotIn("response_assist", observed)
        self.assertTrue(record["llm_progress"]["done"])

    def test_source_has_no_director_singleton_stream_reads(self):
        source = (APP / "services" / "director_pipeline.py").read_text(
            encoding="utf-8",
        )
        for obsolete in (
            "_stream_buffer", "_last_system_prompt", "_last_user_prompt",
            "_last_thinking_text", "_llm_passes", "llm_streaming",
        ):
            self.assertNotIn(obsolete, source)
        launch_source = (APP / "launch.py").read_text(encoding="utf-8")
        self.assertIn('public["llm_log"] = None', launch_source)
        self.assertIn('/api/v1/director/pipeline/', launch_source)


if __name__ == "__main__":
    unittest.main()
