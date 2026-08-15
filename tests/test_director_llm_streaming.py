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
from services.director.planners.base import BasePlanner  # noqa: E402
from services.director.planners.short_film import ShortFilmPlanner  # noqa: E402
from services.director.schema import CharacterProfile  # noqa: E402
from services.llm_cancellation import (  # noqa: E402
    LlmCancellationHandle,
    LlmRequestCancelled,
)


class _CancellationPlanner(BasePlanner):
    skill_type = "test"

    def plan(self, **kwargs):
        raise NotImplementedError


class DirectorLlmStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.originals = {
            "pipelines": pipeline._pipelines,
            "contexts": pipeline._pipeline_llm_contexts,
            "tokens": pipeline._pipeline_llm_tokens,
            "cancel_handles": pipeline._pipeline_llm_cancel_handles,
            "jobs": pipeline._jobs,
            "active": pipeline._active_gen_states,
            "wgp": pipeline._wgp,
            "gen_lock": pipeline._gen_lock,
            "stream": llm_service._stream_buffer,
            "last_system": llm_service._last_system_prompt,
            "last_user": llm_service._last_user_prompt,
            "last_thinking": llm_service._last_thinking_text,
        }
        pipeline._pipelines = {}
        pipeline._pipeline_llm_contexts = {}
        pipeline._pipeline_llm_tokens = {}
        pipeline._pipeline_llm_cancel_handles = {}
        pipeline._jobs = {}
        pipeline._active_gen_states = {}
        pipeline._wgp = SimpleNamespace(
            save_path=self.temporary.name,
            server_config={"services": {}},
        )
        @contextmanager
        def default_model_lease(**_selection):
            yield ("test-resident",)

        self._model_lease_patcher = mock.patch.object(
            llm_service, "loaded_model_lease", default_model_lease,
        )
        self._model_lease_patcher.start()
        # Deliberately unrelated legacy singleton state. Director must neither
        # read it nor clear it when request-scoped callbacks are active.
        llm_service._stream_buffer = "UNRELATED_SINGLETON_STREAM"
        llm_service._last_system_prompt = "UNRELATED_SINGLETON_SYSTEM"
        llm_service._last_user_prompt = "UNRELATED_SINGLETON_USER"
        llm_service._last_thinking_text = "UNRELATED_SINGLETON_THINKING"

    def tearDown(self):
        self._model_lease_patcher.stop()
        pipeline._pipelines = self.originals["pipelines"]
        pipeline._pipeline_llm_contexts = self.originals["contexts"]
        pipeline._pipeline_llm_tokens = self.originals["tokens"]
        pipeline._pipeline_llm_cancel_handles = self.originals[
            "cancel_handles"
        ]
        pipeline._jobs = self.originals["jobs"]
        pipeline._active_gen_states = self.originals["active"]
        pipeline._wgp = self.originals["wgp"]
        pipeline._gen_lock = self.originals["gen_lock"]
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
        pipeline._pipeline_llm_contexts.setdefault(pid, {
            "selection": {
                "model_id": "test-model", "device": "cpu",
                "provider": "local", "remote_url": "", "api_key": "",
                "local_gguf_path": "", "gguf_file_override": "",
            },
            "response_assist": None,
        })
        return record

    def test_progress_is_bounded_retry_scoped_and_ignores_singleton(self):
        record = self._add_pipeline("scoped")
        pipeline._pipeline_llm_contexts["scoped"].update({
            "response_assist": {"retry_on_refusal": True},
        })

        def fake_generate(*, progress_callback, response_assist, cancel_handle):
            self.assertIsInstance(cancel_handle, LlmCancellationHandle)
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
            def fake(*, progress_callback, cancel_handle):
                self.assertIsInstance(cancel_handle, LlmCancellationHandle)
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
        _old_token, _old_handle, old_callback = pipeline._begin_pipeline_llm_pass(
            "stale", phase="planning", pass_name="old", attempt_limit=1,
        )
        pipeline._finish_pipeline_llm_pass("stale", _old_token)
        _new_token, _new_handle, new_callback = pipeline._begin_pipeline_llm_pass(
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
        _token, _cancel_handle, callback = pipeline._begin_pipeline_llm_pass(
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

    def test_stop_closes_blocked_response_and_rejects_late_chunk(self):
        record = self._add_pipeline("blocked")
        entered = threading.Event()
        closed = threading.Event()
        result = []
        close_lock_free = []

        class BlockingResponse:
            def close(self):
                acquired = pipeline._pipeline_lock.acquire(timeout=0.2)
                close_lock_free.append(acquired)
                if acquired:
                    pipeline._pipeline_lock.release()
                closed.set()

        response = BlockingResponse()

        def fake_generate(*, progress_callback, cancel_handle):
            cancel_handle.register_response(response)
            entered.set()
            self.assertTrue(closed.wait(timeout=2))
            cancel_handle.unregister_response(response)
            progress_callback({
                "phase": "generating", "text": "LATE_CHUNK",
                "attempt": 1, "generated_tokens_approx": 2,
                "elapsed_seconds": 1.0, "live_tps": 2.0,
                "done": False,
            })
            cancel_handle.checkpoint()
            return "late"

        def run():
            try:
                pipeline._pipeline_llm_call(
                    "blocked", "planning", "blocked_response", fake_generate,
                )
            except BaseException as exc:
                result.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(timeout=2))
        self.assertTrue(pipeline.stop_pipeline("blocked"))
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(closed.is_set())
        self.assertEqual(close_lock_free, [True])
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], pipeline._DirectorLlmCancelled)
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["llm_progress"]["activity"], "cancelled")
        self.assertNotIn("LATE_CHUNK", json.dumps(record))
        self.assertNotIn("blocked", pipeline._pipeline_llm_cancel_handles)

    def test_stale_handle_cannot_cancel_successor_or_other_pipeline(self):
        self._add_pipeline("same")
        self._add_pipeline("other")
        old_token, old_handle, _old_callback = (
            pipeline._begin_pipeline_llm_pass(
                "same", phase="planning", pass_name="old", attempt_limit=1,
            )
        )
        pipeline._finish_pipeline_llm_pass("same", old_token)
        new_token, new_handle, _new_callback = (
            pipeline._begin_pipeline_llm_pass(
                "same", phase="planning", pass_name="new", attempt_limit=1,
            )
        )
        other_token, other_handle, _other_callback = (
            pipeline._begin_pipeline_llm_pass(
                "other", phase="planning", pass_name="other", attempt_limit=1,
            )
        )

        old_handle.cancel()

        self.assertTrue(old_handle.cancelled)
        self.assertFalse(new_handle.cancelled)
        self.assertFalse(other_handle.cancelled)
        self.assertIs(
            pipeline._pipeline_llm_cancel_handles["same"][0], new_token,
        )
        self.assertIs(
            pipeline._pipeline_llm_cancel_handles["other"][0], other_token,
        )
        pipeline._finish_pipeline_llm_pass("same", new_token)
        pipeline._finish_pipeline_llm_pass("other", other_token)

    def test_json_grammar_fallback_does_not_swallow_cancellation(self):
        calls = []

        def cancelled_generate(**kwargs):
            calls.append(dict(kwargs))
            raise LlmRequestCancelled("cancelled")

        planner = _CancellationPlanner(llm_generate=cancelled_generate)
        with self.assertRaises(LlmRequestCancelled):
            planner._call_llm_json(
                "request", "system", thinking_budget=0,
                streaming=False, json_schema={"type": "array"},
            )
        self.assertEqual(len(calls), 1)

    def test_h3_optional_llm_passes_do_not_swallow_cancellation(self):
        calls = []

        def cancelled_generate(**kwargs):
            calls.append(dict(kwargs))
            raise LlmRequestCancelled("cancelled")

        planner = ShortFilmPlanner(llm_generate=cancelled_generate)
        character = CharacterProfile(
            id="char_0", display_name="Ari",
            physical_description="a synthetic performer",
        )
        with self.assertRaises(LlmRequestCancelled):
            planner._build_h3_character_voice_bible(
                story_description="A synthetic scene.",
                char_profiles=[character],
            )
        self.assertEqual(len(calls), 1)

        calls.clear()
        with self.assertRaises(LlmRequestCancelled):
            planner._run_h3_character_table_read(
                story_description="A synthetic scene.",
                screenplay='ARI: "Hello."',
                manifest=[{
                    "speaker_name": "Ari", "spoken_text": "Hello.",
                }],
                voice_bible=[],
                max_spoken_words=20,
                maximum_line_words=10,
            )
        self.assertEqual(len(calls), 1)

    def test_polish_exception_does_not_start_later_request(self):
        calls = []

        def cancelled_enhance(**kwargs):
            calls.append(kwargs["mode"])
            raise LlmRequestCancelled("cancelled")

        plans = [{
            "video_prompt": "Synthetic motion.",
            "image_prompt": "Synthetic still.",
        }]
        with mock.patch.object(
            llm_service, "enhance_prompt", side_effect=cancelled_enhance,
        ):
            with self.assertRaises(LlmRequestCancelled):
                prompt_polish.polish_prompts_third_pass(
                    plans,
                    "ltx2_22B_distilled_1_1",
                    "flux2_klein_9b",
                    polish_video_prompts=True,
                    polish_image_prompts=True,
                    cancel_handle=LlmCancellationHandle(),
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

        def fake_generate(*, progress_callback, cancel_handle):
            self.assertIsInstance(cancel_handle, LlmCancellationHandle)
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

    def test_cuda_director_model_work_uses_shared_native_gpu_lane(self):
        from concurrent.futures import ThreadPoolExecutor

        native_gpu = threading.Lock()
        pipeline._gen_lock = threading.Lock()
        release_calls = []

        def acquire_native(cancel_checkpoint=None):
            while True:
                if callable(cancel_checkpoint):
                    cancel_checkpoint()
                if native_gpu.acquire(timeout=0.01):
                    try:
                        if callable(cancel_checkpoint):
                            cancel_checkpoint()
                    except BaseException:
                        native_gpu.release()
                        raise
                    return

        pipeline._wgp = SimpleNamespace(
            save_path=self.temporary.name,
            server_config={"services": {}},
            native_gpu_execution_lock=native_gpu,
            acquire_native_gpu_execution_lock=acquire_native,
            wan_model=object(),
            release_model=lambda: release_calls.append("release"),
        )
        selection = {
            "model_id": "synthetic-model", "device": "cuda",
            "provider": "local", "remote_url": "", "api_key": "",
            "local_gguf_path": "", "gguf_file_override": "",
        }
        self._add_pipeline("cuda")
        pipeline._pipeline_llm_contexts["cuda"] = {
            "selection": selection,
            "response_assist": None,
        }
        inference_entered = threading.Event()

        @contextmanager
        def fake_lease(**_selection):
            yield

        def fake_generate(*, progress_callback, cancel_handle):
            inference_entered.set()
            return "done"

        native_gpu.acquire()
        with mock.patch.object(
            llm_service, "loaded_model_lease", fake_lease,
        ), ThreadPoolExecutor(max_workers=1) as executor:
            request = executor.submit(
                pipeline._pipeline_llm_call,
                "cuda", "planning", "cuda_pass", fake_generate,
            )
            for _ in range(100):
                if pipeline._gen_lock.locked():
                    break
                time.sleep(0.005)
            self.assertTrue(pipeline._gen_lock.locked())
            self.assertFalse(inference_entered.wait(timeout=0.1))
            native_gpu.release()
            self.assertEqual(request.result(timeout=1), "done")
        self.assertFalse(pipeline._gen_lock.locked())

        self._add_pipeline("cancelled_wait")
        pipeline._pipeline_llm_contexts["cancelled_wait"] = {
            "selection": selection,
            "response_assist": None,
        }
        lease_entered = threading.Event()

        @contextmanager
        def blocked_lease(**_selection):
            lease_entered.set()
            yield

        native_gpu.acquire()
        with mock.patch.object(
            llm_service, "loaded_model_lease", blocked_lease,
        ), ThreadPoolExecutor(max_workers=1) as executor:
            blocked = executor.submit(
                pipeline._pipeline_llm_call,
                "cancelled_wait", "planning", "blocked", fake_generate,
            )
            for _ in range(100):
                if pipeline._gen_lock.locked() and (
                    "cancelled_wait" in pipeline._pipeline_llm_cancel_handles
                ):
                    break
                time.sleep(0.005)
            self.assertTrue(pipeline._gen_lock.locked())
            cancel_handle = pipeline._pipeline_llm_cancel_handles[
                "cancelled_wait"
            ][1]
            cancel_handle.cancel()
            with self.assertRaises(pipeline._DirectorLlmCancelled):
                blocked.result(timeout=1)
            self.assertFalse(lease_entered.is_set())
            self.assertFalse(pipeline._gen_lock.locked())
            self.assertTrue(native_gpu.locked())
            native_gpu.release()

        self._add_pipeline("cpu")
        pipeline._pipeline_llm_contexts["cpu"] = {
            "selection": {**selection, "device": "cpu"},
            "response_assist": None,
        }
        inference_entered.clear()
        native_gpu.acquire()
        try:
            with mock.patch.object(
                llm_service, "loaded_model_lease", fake_lease,
            ):
                self.assertEqual(
                    pipeline._pipeline_llm_call(
                        "cpu", "planning", "cpu_pass", fake_generate,
                    ),
                    "done",
                )
            self.assertTrue(inference_entered.is_set())
        finally:
            native_gpu.release()

        release_entered = threading.Event()
        load_entered = threading.Event()
        pipeline._wgp.release_model = lambda: (
            release_calls.append("release"), release_entered.set()
        )
        native_gpu.acquire()
        with mock.patch.object(
            llm_service, "load_model", side_effect=lambda **_kwargs:
            load_entered.set(),
        ), ThreadPoolExecutor(max_workers=1) as executor:
            prepare = executor.submit(
                pipeline._ensure_llm_loaded,
                {
                    "llm_model_id": "synthetic-model",
                    "llm_device": "cuda",
                    "llm_provider": "local",
                },
                lambda: None,
            )
            for _ in range(100):
                if pipeline._gen_lock.locked():
                    break
                time.sleep(0.005)
            self.assertTrue(pipeline._gen_lock.locked())
            self.assertFalse(release_entered.is_set())
            self.assertFalse(load_entered.is_set())
            native_gpu.release()
            self.assertEqual(prepare.result(timeout=1)["device"], "cuda")
        self.assertTrue(release_entered.is_set())
        self.assertTrue(load_entered.is_set())
        self.assertFalse(native_gpu.locked())
        self.assertFalse(pipeline._gen_lock.locked())

    def test_stale_director_cleanup_cannot_unload_successor_identity(self):
        stale_key = ("local", "old-model", "cpu")
        successor_key = ("local", "successor-model", "cuda")
        stale_identity = (stale_key, 17)
        selection = {
            "model_id": "old-model", "device": "cpu",
            "provider": "local", "remote_url": "", "api_key": "",
            "local_gguf_path": "", "gguf_file_override": "",
        }
        with mock.patch.object(
            llm_service, "_lock", threading.RLock(),
        ), mock.patch.object(
            llm_service, "_loaded_model_key", successor_key,
        ), mock.patch.object(
            llm_service, "_runtime_generation", 18,
        ), mock.patch.object(
            llm_service, "unload_model",
        ) as unload:
            self.assertFalse(
                pipeline._unload_director_resident_if_current(
                    selection, stale_identity,
                )
            )
        unload.assert_not_called()

    def test_stale_director_cleanup_cannot_unload_same_key_successor_epoch(self):
        load_key = ("local", "same-model", "cuda")
        selection = {
            "model_id": "same-model", "device": "cuda",
            "provider": "local", "remote_url": "", "api_key": "",
            "local_gguf_path": "", "gguf_file_override": "",
        }
        @contextmanager
        def no_native_slot(*_args, **_kwargs):
            yield False

        with mock.patch.object(
            pipeline, "_DirectorNativeGpuSlot", no_native_slot,
        ), mock.patch.object(
            llm_service, "_lock", threading.RLock(),
        ), mock.patch.object(
            llm_service, "_loaded_model_key", load_key,
        ), mock.patch.object(
            llm_service, "_runtime_generation", 18,
        ), mock.patch.object(
            llm_service, "unload_model",
        ) as unload:
            self.assertFalse(
                pipeline._unload_director_resident_if_current(
                    selection, (load_key, 17),
                )
            )
        unload.assert_not_called()

    def test_remote_director_cleanup_never_unloads_same_key_replacement(self):
        load_key = ("remote", "same-model", "remote", "https://provider", "")
        selection = {
            "model_id": "same-model", "device": "remote",
            "provider": "remote", "remote_url": "https://provider",
            "api_key": "", "local_gguf_path": "",
            "gguf_file_override": "",
        }
        with mock.patch.object(
            llm_service, "_loaded_model_key", load_key,
        ), mock.patch.object(
            llm_service, "_runtime_generation", 0,
        ), mock.patch.object(
            llm_service, "unload_model",
        ) as unload:
            self.assertFalse(
                pipeline._unload_director_resident_if_current(
                    selection, (load_key, 0),
                )
            )
        unload.assert_not_called()

    def test_unscoped_director_llm_call_fails_before_callable(self):
        invoked = mock.Mock(return_value="unsafe")
        with self.assertRaises(pipeline._DirectorLlmCancelled):
            pipeline._pipeline_llm_call(
                "missing", "planning", "unscoped", invoked,
            )
        invoked.assert_not_called()

    def test_live_pipeline_without_exact_context_fails_before_callable(self):
        self._add_pipeline("recovery-without-context")
        pipeline._pipeline_llm_contexts.pop(
            "recovery-without-context", None,
        )
        invoked = mock.Mock(return_value="unsafe singleton result")
        with self.assertRaisesRegex(
            RuntimeError, "context is unavailable",
        ):
            pipeline._pipeline_llm_call(
                "recovery-without-context",
                "reference_style",
                "reference_style_vlm",
                invoked,
            )
        invoked.assert_not_called()

    def test_cancel_after_director_load_cleans_exact_resident(self):
        from concurrent.futures import ThreadPoolExecutor

        record = self._add_pipeline("cancel_after_load")
        native_gpu = threading.Lock()
        pipeline._gen_lock = threading.Lock()

        def acquire_native(cancel_checkpoint=None):
            while True:
                if callable(cancel_checkpoint):
                    cancel_checkpoint()
                if native_gpu.acquire(timeout=0.01):
                    try:
                        if callable(cancel_checkpoint):
                            cancel_checkpoint()
                    except BaseException:
                        native_gpu.release()
                        raise
                    return

        pipeline._wgp = SimpleNamespace(
            save_path=self.temporary.name,
            server_config={"services": {}},
            native_gpu_execution_lock=native_gpu,
            acquire_native_gpu_execution_lock=acquire_native,
        )
        resident_key = ("local", "director-model", "cuda")
        resident_identity = (resident_key, 17)
        selection = pipeline._DirectorLlmSelection({
            "model_id": "director-model", "device": "cuda",
            "provider": "local", "remote_url": "", "api_key": "",
            "local_gguf_path": "", "gguf_file_override": "",
        })
        selection.resident_identity = resident_identity
        lease_entered = threading.Event()

        @contextmanager
        def unexpected_lease(**_selection):
            lease_entered.set()
            yield resident_key

        native_gpu.acquire()
        with mock.patch.object(
            pipeline, "_ensure_llm_loaded", return_value=selection,
        ), mock.patch.object(
            llm_service, "loaded_model_lease", unexpected_lease,
        ), mock.patch.object(
            llm_service, "_lock", threading.RLock(),
        ), mock.patch.object(
            llm_service, "_loaded_model_key", resident_key,
        ), mock.patch.object(
            llm_service, "_runtime_generation", 17,
        ), mock.patch.object(
            llm_service, "unload_model",
        ) as unload, ThreadPoolExecutor(max_workers=1) as executor:
            planning = executor.submit(
                pipeline._run_planning,
                "cancel_after_load",
                {"use_director_v2": True},
                "music_video",
            )
            for _ in range(100):
                if pipeline._gen_lock.locked():
                    break
                time.sleep(0.005)
            self.assertTrue(pipeline._gen_lock.locked())
            with pipeline._pipeline_lock:
                record["status"] = "cancelled"
            # The cancelled outer acquisition unwinds, then exact cleanup
            # waits for the same native lane before unloading its own model.
            time.sleep(0.05)
            self.assertFalse(lease_entered.is_set())
            native_gpu.release()
            with self.assertRaises(pipeline._DirectorLlmCancelled):
                planning.result(timeout=1)
        unload.assert_called_once_with()
        self.assertFalse(native_gpu.locked())
        self.assertFalse(pipeline._gen_lock.locked())

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
        pipeline._pipeline_llm_contexts["legacy"].update({
            "response_assist": {"retry_on_refusal": True},
        })
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
