"""Race and lifecycle tests for the cooperative local-LLM CPU runtime."""

import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from services import llm_service  # noqa: E402


class _HealthyResponse:
    status_code = 200
    text = '{"status":"ok"}'

    @staticmethod
    def json():
        return {"status": "ok"}


class _ChatResponse:
    @staticmethod
    def raise_for_status():
        return None

    @staticmethod
    def json():
        return {
            "choices": [{
                "message": {"content": "synthetic answer"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2},
        }


class _StreamResponse:
    @staticmethod
    def raise_for_status():
        return None

    @staticmethod
    def iter_lines(decode_unicode=False):
        del decode_unicode
        yield (
            'data: {"choices":[{"delta":{"content":"ok"}}],'
            '"usage":{"completion_tokens":3}}'
        )
        yield "data: [DONE]"


class _Timer:
    def __init__(self, _interval, _function, args=(), kwargs=None):
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = False

    def start(self):
        return None

    def cancel(self):
        return None


class _Process:
    def __init__(
        self,
        *,
        terminate_exits=True,
        kill_exits=True,
        terminate_entered=None,
        release_terminate=None,
    ):
        self.returncode = None
        self.stdout = io.BytesIO()
        self.terminate_exits = terminate_exits
        self.kill_exits = kill_exits
        self.terminate_entered = terminate_entered
        self.release_terminate = release_terminate
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        if self.terminate_entered is not None:
            self.terminate_entered.set()
        if self.release_terminate is not None:
            self.release_terminate.wait(timeout=2)
        if self.terminate_exits:
            self.returncode = 0

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("llama-server", timeout)
        return self.returncode

    def kill(self):
        self.kill_calls += 1
        if self.kill_exits:
            self.returncode = -9


class LocalCpuRuntimeTests(unittest.TestCase):
    def tearDown(self):
        with llm_service._runtime_status_lock:
            process = llm_service._process
            if process is not None and hasattr(process, "returncode"):
                process.returncode = 0
        with llm_service._lock:
            llm_service._unload_inner()

    def _load(self, process, model_path):
        with mock.patch.object(
            llm_service, "_get_server_exe", return_value="llama-server",
        ), mock.patch.object(
            llm_service, "_find_free_port", return_value=54321,
        ), mock.patch.object(
            llm_service.subprocess, "Popen", return_value=process,
        ), mock.patch.object(
            llm_service.requests, "get", return_value=_HealthyResponse(),
        ), mock.patch.object(
            llm_service.threading, "Timer", _Timer,
        ):
            llm_service.load_model(
                "linked:model",
                device="cuda",
                local_gguf_path=str(model_path),
                cpu_coexistence=True,
            )

    def test_cpu_coexistence_profile_is_bounded_without_changing_gpu_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            hardware = {
                "logical_threads": 64,
                "physical_threads": 32,
                "gpu_vram_gb": 32.0,
            }
            registry = {"runtime_profile": {
                "threads": 24,
                "threads_batch": 48,
                "batch_size": 2048,
                "ubatch_size": 512,
            }}
            with mock.patch.object(
                llm_service, "_hardware_profile", return_value=hardware,
            ):
                standard = llm_service._runtime_profile_for(
                    str(model), None, "cuda", {"backend": "cuda"}, registry,
                )
                coexistence = llm_service._runtime_profile_for(
                    str(model), None, "cpu", {"backend": "cpu"}, registry,
                    cpu_coexistence=True,
                )

        self.assertEqual(standard["backend"], "cuda")
        self.assertEqual(standard["threads"], 24)
        self.assertEqual(standard["batch_size"], 2048)
        self.assertNotIn("execution", standard)
        self.assertEqual(coexistence["backend"], "cpu")
        self.assertEqual(coexistence["gpu_layers"], 0)
        self.assertEqual(coexistence["threads"], 8)
        self.assertEqual(coexistence["threads_batch"], 8)
        self.assertEqual(coexistence["batch_size"], 256)
        self.assertEqual(coexistence["ubatch_size"], 64)
        self.assertEqual(
            coexistence["execution"], llm_service.CPU_COEXISTENCE_MODE,
        )
        self.assertTrue(coexistence["abort_capable"])
        self.assertFalse(coexistence["preemptible"])
        self.assertTrue(
            coexistence["preemption_requires_decision_evidence"],
        )
        self.assertEqual(
            llm_service.get_cpu_coexistence_defaults(),
            {
                "execution": "cooperative_cpu",
                "max_threads": 8,
                "max_batch_threads": 8,
                "batch_size": 256,
                "ubatch_size": 64,
                "abort_capable": True,
                "preemptible": False,
                "preemption_requires_decision_evidence": True,
                "slots": 1,
            },
        )

    def test_remaining_projection_uses_only_exact_attempt_evidence(self):
        process = _Process()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            with llm_service._runtime_status_lock:
                llm_service._runtime_timings = {
                    "predicted_per_second": 4.0,
                }
                llm_service._runtime_timings_multimodal = False
            generation = llm_service.get_local_runtime_control()["generation"]
            with llm_service.local_runtime_attempt(generation) as attempt_id:
                before_budget = llm_service.get_local_runtime_control()
                self.assertTrue(before_budget["abort_capable"])
                self.assertFalse(before_budget["preemptible"])
                self.assertEqual(
                    before_budget["remaining"]["reason"],
                    "request_budget_unbound",
                )

                self.assertTrue(llm_service._bind_runtime_request_budget(
                    100, multimodal=False, request_pass=1,
                ))
                self.assertTrue(llm_service._observe_runtime_output_metrics(
                    {"usage": {"completion_tokens": 25}}, request_pass=1,
                ))
                control = llm_service.get_local_runtime_control()
                remaining = control["remaining"]

                self.assertEqual(control["attempt_id"], attempt_id)
                self.assertEqual(remaining["state"], "budget_projection")
                self.assertEqual(remaining["reason"], "terminal_length_unknown")
                self.assertFalse(remaining["decision_eligible"])
                self.assertEqual(remaining["runtime_generation"], generation)
                self.assertEqual(remaining["attempt_id"], attempt_id)
                self.assertEqual(remaining["request_pass"], 1)
                self.assertEqual(remaining["output_token_limit"], 100)
                self.assertEqual(remaining["observed_output_tokens"], 25)
                self.assertEqual(remaining["remaining_budget_tokens"], 75)
                self.assertEqual(remaining["measured_tokens_per_second"], 4.0)
                self.assertEqual(remaining["budget_projection_seconds"], 18.75)
                self.assertIsNone(control["remaining_estimate_seconds"])
                self.assertFalse(control["preemptible"])

            finished = llm_service.get_local_runtime_control()
            self.assertEqual(finished["remaining"]["state"], "unavailable")
            self.assertEqual(
                finished["remaining"]["reason"], "request_finished",
            )
            self.assertIsNone(finished["remaining"]["output_token_limit"])
            self.assertFalse(finished["abort_capable"])

    def test_remaining_projection_requires_same_selection_rate(self):
        process = _Process()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            with llm_service._runtime_status_lock:
                llm_service._runtime_timings = {
                    "predicted_per_second": 12.0,
                }
                llm_service._runtime_timings_multimodal = True
            generation = llm_service.get_local_runtime_control()["generation"]
            with llm_service.local_runtime_attempt(generation):
                self.assertTrue(llm_service._bind_runtime_request_budget(
                    64, multimodal=False, request_pass=1,
                ))
                remaining = llm_service.get_local_runtime_control()["remaining"]

                self.assertEqual(remaining["state"], "unavailable")
                self.assertEqual(
                    remaining["reason"], "same_selection_rate_unavailable",
                )
                self.assertIsNone(remaining["measured_tokens_per_second"])
                self.assertIsNone(remaining["budget_projection_seconds"])

    def test_retry_resets_progress_and_stale_pass_cannot_update_it(self):
        process = _Process()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            with llm_service._runtime_status_lock:
                llm_service._runtime_timings = {
                    "predicted_per_second": 10.0,
                }
                llm_service._runtime_timings_multimodal = False
            generation = llm_service.get_local_runtime_control()["generation"]
            with llm_service.local_runtime_attempt(generation):
                self.assertTrue(llm_service._bind_runtime_request_budget(
                    100, multimodal=False, request_pass=1,
                ))
                self.assertTrue(llm_service._observe_runtime_output_metrics(
                    {"timings": {"predicted_n": 40}}, request_pass=1,
                ))
                self.assertTrue(llm_service._bind_runtime_request_budget(
                    80, multimodal=False, request_pass=2,
                ))
                self.assertFalse(llm_service._observe_runtime_output_metrics(
                    {"usage": {"completion_tokens": 60}}, request_pass=1,
                ))
                remaining = llm_service.get_local_runtime_control()["remaining"]

                self.assertEqual(remaining["request_pass"], 2)
                self.assertEqual(remaining["output_token_limit"], 80)
                self.assertEqual(remaining["observed_output_tokens"], 0)
                self.assertEqual(remaining["remaining_budget_tokens"], 80)
                self.assertEqual(remaining["budget_projection_seconds"], 8.0)

    def test_projection_is_clamped_and_never_infers_tokens_from_text(self):
        process = _Process()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            with llm_service._runtime_status_lock:
                llm_service._runtime_timings = {
                    "predicted_per_second": 0.01,
                }
                llm_service._runtime_timings_multimodal = False
            generation = llm_service.get_local_runtime_control()["generation"]
            with llm_service.local_runtime_attempt(generation):
                self.assertTrue(llm_service._bind_runtime_request_budget(
                    1_000_000, multimodal=False, request_pass=1,
                ))
                self.assertFalse(llm_service._observe_runtime_output_metrics(
                    {"choices": [{"delta": {"content": "not token evidence"}}]},
                    request_pass=1,
                ))
                self.assertFalse(llm_service._observe_runtime_output_metrics(
                    {
                        "usage": {"completion_tokens": 2},
                        "timings": {"predicted_n": 3},
                    },
                    request_pass=1,
                ))
                self.assertFalse(llm_service._observe_runtime_output_metrics(
                    {"usage": {"completion_tokens": 2.0}}, request_pass=1,
                ))
                remaining = llm_service.get_local_runtime_control()["remaining"]

                self.assertEqual(remaining["observed_output_tokens"], 0)
                self.assertEqual(remaining["budget_projection_seconds"], 86_400.0)
                self.assertFalse(remaining["decision_eligible"])

    def test_streaming_public_path_publishes_exact_progress_then_invalidates(self):
        process = _Process()
        snapshots = []
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            with llm_service._runtime_status_lock:
                llm_service._runtime_timings = {
                    "predicted_per_second": 2.0,
                }
                llm_service._runtime_timings_multimodal = False

            def capture(_progress):
                snapshots.append(llm_service.get_local_runtime_control())

            with mock.patch.object(
                llm_service.requests, "post", return_value=_StreamResponse(),
            ):
                answer = llm_service.generate_streaming(
                    "content-free-test",
                    max_new_tokens=11,
                    progress_callback=capture,
                )

        self.assertEqual(answer, "ok")
        projected = [
            snapshot["remaining"] for snapshot in snapshots
            if snapshot["remaining"]["state"] == "budget_projection"
        ]
        self.assertTrue(projected)
        self.assertEqual(projected[-1]["output_token_limit"], 11)
        self.assertEqual(projected[-1]["observed_output_tokens"], 3)
        self.assertEqual(projected[-1]["remaining_budget_tokens"], 8)
        self.assertEqual(projected[-1]["budget_projection_seconds"], 4.0)
        self.assertFalse(projected[-1]["decision_eligible"])
        terminal = llm_service.get_local_runtime_control()
        self.assertEqual(terminal["remaining"]["state"], "unavailable")
        self.assertEqual(terminal["remaining"]["reason"], "request_finished")
        self.assertIsNone(terminal["remaining_estimate_seconds"])

    def test_active_attempt_abort_bypasses_model_lease_and_reports_release(self):
        process = _Process()
        attempt_ready = threading.Event()
        release_worker = threading.Event()
        token = {}

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)

            def hold_attempt():
                control = llm_service.get_local_runtime_control()
                token["generation"] = control["generation"]
                with llm_service.local_runtime_attempt(
                    control["generation"],
                ) as attempt_id:
                    token["attempt_id"] = attempt_id
                    llm_service._bind_runtime_request_budget(
                        128, multimodal=False, request_pass=1,
                    )
                    attempt_ready.set()
                    release_worker.wait(timeout=2)

            worker = threading.Thread(target=hold_attempt)
            worker.start()
            self.assertTrue(attempt_ready.wait(timeout=1))
            started = time.monotonic()
            result = llm_service.abort_local_cpu_runtime(
                token["generation"], token["attempt_id"],
                terminate_timeout=0.01, kill_timeout=0.01,
            )
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.5)
            self.assertTrue(worker.is_alive(), "abort waited on the model lease")
            self.assertTrue(result["matched"])
            self.assertTrue(result["resources_released"])
            self.assertEqual(process.terminate_calls, 1)
            self.assertEqual(process.kill_calls, 0)
            control = llm_service.get_local_runtime_control()
            self.assertIsNone(control["generation"])
            self.assertTrue(control["resources_released"])
            self.assertEqual(control["remaining"]["state"], "unavailable")
            self.assertEqual(
                control["remaining"]["reason"], "runtime_released",
            )
            self.assertEqual(
                control["last_release"]["generation"], token["generation"],
            )
            release_worker.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())

    def test_abort_intent_rejects_a_duplicate_terminator(self):
        terminate_entered = threading.Event()
        release_terminate = threading.Event()
        process = _Process(
            terminate_entered=terminate_entered,
            release_terminate=release_terminate,
        )
        results = []
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            generation = llm_service.get_local_runtime_control()["generation"]
            with llm_service.local_runtime_attempt(generation) as attempt_id:
                aborter = threading.Thread(target=lambda: results.append(
                    llm_service.abort_local_cpu_runtime(
                        generation, attempt_id,
                        terminate_timeout=0.01, kill_timeout=0.01,
                    )
                ))
                aborter.start()
                self.assertTrue(terminate_entered.wait(timeout=1))
                duplicate = llm_service.abort_local_cpu_runtime(
                    generation, attempt_id,
                    terminate_timeout=0.01, kill_timeout=0.01,
                )
                self.assertFalse(duplicate["matched"])
                self.assertEqual(process.terminate_calls, 1)
                release_terminate.set()
                aborter.join(timeout=2)

        self.assertFalse(aborter.is_alive())
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["resources_released"])
        self.assertEqual(process.terminate_calls, 1)

    def test_abort_requires_exact_attempt_for_new_request_same_generation(self):
        process = _Process()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            generation = llm_service.get_local_runtime_control()["generation"]
            with llm_service.local_runtime_attempt(generation) as prior_attempt:
                pass

            with llm_service.local_runtime_attempt(generation) as active_attempt:
                self.assertGreater(active_attempt, prior_attempt)
                missing = llm_service.abort_local_cpu_runtime(
                    generation, None,
                    terminate_timeout=0.01, kill_timeout=0.01,
                )
                stale = llm_service.abort_local_cpu_runtime(
                    generation, prior_attempt,
                    terminate_timeout=0.01, kill_timeout=0.01,
                )
                control = llm_service.get_local_runtime_control()
                self.assertFalse(missing["matched"])
                self.assertFalse(stale["matched"])
                self.assertEqual(control["attempt_id"], active_attempt)
                self.assertEqual(control["phase"], "requesting")
                self.assertTrue(control["abort_capable"])
                self.assertEqual(process.terminate_calls, 0)
                self.assertEqual(process.kill_calls, 0)

    def test_generate_surfaces_intentional_abort_identity(self):
        process = _Process()
        request_started = threading.Event()
        token_ready = threading.Event()
        token = {}
        errors = []

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)

            def post(*_args, **_kwargs):
                request_started.set()
                for _ in range(200):
                    if process.poll() is not None:
                        break
                    time.sleep(0.001)
                raise llm_service.requests.ConnectionError("synthetic")

            def run_request():
                try:
                    generation = llm_service.get_local_runtime_control()[
                        "generation"
                    ]
                    with llm_service.local_runtime_attempt(
                        generation,
                    ) as attempt_id:
                        token.update({
                            "generation": generation,
                            "attempt_id": attempt_id,
                        })
                        token_ready.set()
                        llm_service.generate("content-free-test")
                except Exception as error:  # asserted below
                    errors.append(error)

            with mock.patch.object(
                llm_service.requests, "post", side_effect=post,
            ):
                worker = threading.Thread(target=run_request)
                worker.start()
                self.assertTrue(token_ready.wait(timeout=1))
                self.assertTrue(request_started.wait(timeout=1))
                result = llm_service.abort_local_cpu_runtime(
                    token["generation"], token["attempt_id"],
                    terminate_timeout=0.01, kill_timeout=0.01,
                )
                worker.join(timeout=2)

            self.assertTrue(result["resources_released"])
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(
                errors[0], llm_service.LocalRuntimeAbortedError,
            )
            self.assertEqual(errors[0].runtime_generation, token["generation"])
            self.assertEqual(errors[0].attempt_id, token["attempt_id"])
            self.assertTrue(errors[0].resources_released)

    def test_cold_generate_chat_binds_one_post_load_attempt(self):
        process = _Process()
        observed = {}
        before_attempt = llm_service._runtime_attempt_counter
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")

            def post(*_args, **_kwargs):
                observed.update(llm_service.get_local_runtime_control())
                return _ChatResponse()

            with mock.patch.object(
                llm_service, "_get_server_exe", return_value="llama-server",
            ), mock.patch.object(
                llm_service, "_find_free_port", return_value=54321,
            ), mock.patch.object(
                llm_service.subprocess, "Popen", return_value=process,
            ), mock.patch.object(
                llm_service.requests, "get", return_value=_HealthyResponse(),
            ), mock.patch.object(
                llm_service.requests, "post", side_effect=post,
            ), mock.patch.object(
                llm_service.threading, "Timer", _Timer,
            ):
                answer = llm_service.generate_chat(
                    [{"role": "user", "content": "synthetic request"}],
                    model_id="linked:model",
                    device="cuda",
                    local_gguf_path=str(model),
                    cpu_coexistence=True,
                )

        self.assertEqual(answer, "synthetic answer")
        self.assertEqual(
            llm_service._runtime_attempt_counter, before_attempt + 1,
        )
        self.assertIsNotNone(observed["generation"])
        self.assertIsNotNone(observed["attempt_id"])
        self.assertEqual(observed["phase"], "requesting")
        self.assertEqual(observed["execution"], "cooperative_cpu")
        self.assertTrue(observed["abort_capable"])
        self.assertFalse(observed["preemptible"])
        self.assertEqual(
            observed["remaining"]["reason"],
            "same_selection_rate_unavailable",
        )
        self.assertIsNone(
            llm_service.get_local_runtime_control()["attempt_id"],
        )

    def test_switching_generate_chat_binds_only_the_replacement_attempt(self):
        first = _Process()
        second = _Process()
        observed = {}
        with tempfile.TemporaryDirectory() as tmp:
            first_model = Path(tmp) / "first.gguf"
            second_model = Path(tmp) / "second.gguf"
            first_model.write_bytes(b"first")
            second_model.write_bytes(b"second")
            self._load(first, first_model)
            first_generation = llm_service.get_local_runtime_control()[
                "generation"
            ]
            before_attempt = llm_service._runtime_attempt_counter

            def post(*_args, **_kwargs):
                observed.update(llm_service.get_local_runtime_control())
                return _ChatResponse()

            with mock.patch.object(
                llm_service, "_get_server_exe", return_value="llama-server",
            ), mock.patch.object(
                llm_service, "_find_free_port", return_value=54322,
            ), mock.patch.object(
                llm_service.subprocess, "Popen", return_value=second,
            ), mock.patch.object(
                llm_service.requests, "get", return_value=_HealthyResponse(),
            ), mock.patch.object(
                llm_service.requests, "post", side_effect=post,
            ), mock.patch.object(
                llm_service.threading, "Timer", _Timer,
            ):
                answer = llm_service.generate_chat(
                    [{"role": "user", "content": "synthetic request"}],
                    model_id="linked:replacement",
                    device="cuda",
                    local_gguf_path=str(second_model),
                    cpu_coexistence=True,
                )

        self.assertEqual(answer, "synthetic answer")
        self.assertNotEqual(observed["generation"], first_generation)
        self.assertIsNotNone(observed["attempt_id"])
        self.assertEqual(observed["phase"], "requesting")
        self.assertEqual(
            llm_service._runtime_attempt_counter, before_attempt + 1,
        )
        self.assertEqual(first.terminate_calls, 1)
        self.assertEqual(second.terminate_calls, 0)

    def test_abort_intent_precedes_release_and_cannot_clear_replacement(self):
        first = _Process()
        second = _Process()
        request_started = threading.Event()
        exit_confirmed = threading.Event()
        release_abort_finalizer = threading.Event()
        token_ready = threading.Event()
        worker_errors = []
        abort_results = []
        token = {}

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(first, model)

            def post(*_args, **_kwargs):
                request_started.set()
                exit_confirmed.wait(timeout=2)
                raise llm_service.requests.ConnectionError("synthetic")

            def run_request():
                try:
                    generation = llm_service.get_local_runtime_control()[
                        "generation"
                    ]
                    with llm_service.local_runtime_attempt(
                        generation,
                    ) as attempt_id:
                        token.update({
                            "generation": generation,
                            "attempt_id": attempt_id,
                        })
                        token_ready.set()
                        llm_service.generate("content-free-test")
                except Exception as error:  # asserted below
                    worker_errors.append(error)

            first_termination = True

            def delayed_confirm(process, **_kwargs):
                nonlocal first_termination
                if first_termination:
                    first_termination = False
                    process.returncode = 0
                    exit_confirmed.set()
                    release_abort_finalizer.wait(timeout=2)
                return True, False

            with mock.patch.object(
                llm_service.requests, "post", side_effect=post,
            ), mock.patch.object(
                llm_service, "_terminate_process_and_confirm",
                side_effect=delayed_confirm,
            ):
                worker = threading.Thread(target=run_request)
                worker.start()
                self.assertTrue(token_ready.wait(timeout=1))
                self.assertTrue(request_started.wait(timeout=1))
                aborter = threading.Thread(target=lambda: abort_results.append(
                    llm_service.abort_local_cpu_runtime(
                        token["generation"], token["attempt_id"],
                        terminate_timeout=0.01, kill_timeout=0.01,
                    )
                ))
                aborter.start()
                self.assertTrue(exit_confirmed.wait(timeout=1))
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(worker_errors), 1)
                self.assertIsInstance(
                    worker_errors[0], llm_service.LocalRuntimeAbortedError,
                )
                self.assertFalse(worker_errors[0].resources_released)
                aborting = llm_service.get_local_runtime_control()
                self.assertEqual(aborting["phase"], "abort_requested")
                self.assertEqual(
                    aborting["remaining"]["reason"], "abort_requested",
                )
                self.assertFalse(aborting["abort_capable"])

                self._load(second, model)
                replacement_generation = llm_service.get_local_runtime_control()[
                    "generation"
                ]
                release_abort_finalizer.set()
                aborter.join(timeout=2)

            self.assertFalse(aborter.is_alive())
            self.assertEqual(len(abort_results), 1)
            self.assertTrue(abort_results[0]["resources_released"])
            self.assertEqual(
                llm_service.get_local_runtime_control()["generation"],
                replacement_generation,
            )
            self.assertTrue(llm_service.is_loaded())
            self.assertEqual(second.terminate_calls, 0)

    def test_stale_generation_cannot_abort_replacement_runtime(self):
        first = _Process()
        second = _Process()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(first, model)
            first_generation = llm_service.get_local_runtime_control()["generation"]
            with llm_service.local_runtime_attempt(first_generation) as first_attempt:
                pass
            llm_service.unload_model()
            self._load(second, model)
            second_generation = llm_service.get_local_runtime_control()["generation"]

            with llm_service.local_runtime_attempt(second_generation) as second_attempt:
                wrong_attempt = llm_service.abort_local_cpu_runtime(
                    second_generation, second_attempt + 1,
                    terminate_timeout=0.01, kill_timeout=0.01,
                )
                stale = llm_service.abort_local_cpu_runtime(
                    first_generation, first_attempt,
                    terminate_timeout=0.01, kill_timeout=0.01,
                )
                current = llm_service.get_local_runtime_control()
                self.assertEqual(current["generation"], second_generation)
                self.assertEqual(current["attempt_id"], second_attempt)

            self.assertFalse(wrong_attempt["matched"])
            self.assertFalse(stale["matched"])
            self.assertFalse(stale["resources_released"])
            self.assertEqual(second.terminate_calls, 0)
            self.assertTrue(llm_service.is_loaded())

    def test_late_attempt_finalizer_cannot_mutate_replacement_runtime(self):
        first = _Process()
        second = _Process()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(first, model)
            old_token = llm_service._begin_runtime_attempt()
            self.assertIsNotNone(old_token)
            released = llm_service.abort_local_cpu_runtime(
                old_token[0], old_token[1],
                terminate_timeout=0.01, kill_timeout=0.01,
            )
            self.assertTrue(released["resources_released"])

            self._load(second, model)
            replacement = llm_service.get_local_runtime_control()
            llm_service._end_runtime_attempt(old_token)
            after_late_finalize = llm_service.get_local_runtime_control()

            self.assertEqual(
                after_late_finalize["generation"], replacement["generation"],
            )
            self.assertEqual(after_late_finalize["phase"], "ready")
            self.assertTrue(llm_service.is_loaded())
            self.assertEqual(second.terminate_calls, 0)

    def test_unload_waits_after_kill_before_clearing_residency(self):
        process = _Process(terminate_exits=False, kill_exits=True)
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            llm_service.unload_model()

        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(len(process.wait_calls), 2)
        self.assertFalse(llm_service.is_loaded())
        self.assertTrue(
            llm_service.get_local_runtime_control()["resources_released"],
        )

    def test_abort_escalates_to_kill_and_waits_for_confirmed_exit(self):
        process = _Process(terminate_exits=False, kill_exits=True)
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            generation = llm_service.get_local_runtime_control()["generation"]
            with llm_service.local_runtime_attempt(generation) as attempt_id:
                result = llm_service.abort_local_cpu_runtime(
                    generation, attempt_id,
                    terminate_timeout=0.0, kill_timeout=0.0,
                )

        self.assertTrue(result["matched"])
        self.assertTrue(result["resources_released"])
        self.assertTrue(result["escalated_to_kill"])
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(len(process.wait_calls), 2)

    def test_failed_abort_keeps_residency_and_release_failure_visible(self):
        process = _Process(terminate_exits=False, kill_exits=False)
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(process, model)
            generation = llm_service.get_local_runtime_control()["generation"]
            with llm_service.local_runtime_attempt(generation) as attempt_id:
                result = llm_service.abort_local_cpu_runtime(
                    generation, attempt_id,
                    terminate_timeout=0.0, kill_timeout=0.0,
                )
                self.assertFalse(result["resources_released"])
                self.assertTrue(llm_service.is_loaded())
            control = llm_service.get_local_runtime_control()
            self.assertEqual(control["phase"], "release_failed")
            self.assertFalse(control["resources_released"])
            process.returncode = -9

    def test_failed_kill_wait_keeps_runtime_visible_and_blocks_replacement(self):
        stubborn = _Process(terminate_exits=False, kill_exits=False)
        replacement = _Process()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            self._load(stubborn, model)
            with mock.patch.object(
                llm_service, "_PROCESS_TERMINATE_TIMEOUT_SEC", 0.0,
            ), mock.patch.object(
                llm_service, "_PROCESS_KILL_TIMEOUT_SEC", 0.0,
            ):
                with self.assertRaisesRegex(RuntimeError, "did not exit"):
                    llm_service.unload_model()

                self.assertTrue(llm_service.is_loaded())
                self.assertEqual(
                    llm_service.get_local_runtime_control()["phase"],
                    "release_failed",
                )
                with mock.patch.object(
                    llm_service, "_get_server_exe", return_value="llama-server",
                ), mock.patch.object(
                    llm_service, "_find_free_port", return_value=54321,
                ), mock.patch.object(
                    llm_service.subprocess, "Popen", return_value=replacement,
                ) as popen:
                    with self.assertRaisesRegex(RuntimeError, "did not exit"):
                        llm_service.load_model(
                            "linked:model",
                            device="cpu",
                            local_gguf_path=str(model),
                            cpu_coexistence=True,
                            force_reload=True,
                        )
                    popen.assert_not_called()

            stubborn.returncode = -9


if __name__ == "__main__":
    unittest.main()
