"""Focused unit coverage for Maestro's bundled llama.cpp runtime handling."""
import ast
import asyncio
import io
import json
import multiprocessing
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import types
import unittest
import urllib.request
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


class _FakeLlamaProcess:
    def __init__(self, *, returncode=None, stdout=None):
        self.returncode = returncode
        self.stdout = stdout if stdout is not None else io.BytesIO()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.returncode = -9


class _RecordingTimer:
    instances = []

    def __init__(self, interval, function, args=(), kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = False
        self.started = False
        self.cancelled = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def _tar_bytes(entries):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, content, linkname in entries:
            info = tarfile.TarInfo(name)
            if linkname is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = linkname
                archive.addfile(info)
                continue
            data = content.encode()
            info.size = len(data)
            info.mode = 0o755 if name.endswith("llama-server") else 0o644
            archive.addfile(info, io.BytesIO(data))
    return payload.getvalue()


def _zip_bytes(entries):
    import zipfile

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, mode="w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return payload.getvalue()


def _speed_writer_process(store_path, start_gate):
    """Spawn-safe worker for the shared calibration merge regression."""
    llm_service._speed_observation_path = lambda: store_path
    llm_service._speed_hardware_identity = lambda _backend: (
        "a" * 64,
        {"physical_threads": 8, "logical_threads": 16, "gpu_vram_gb": 0},
    )
    llm_service._speed_observation_cache = None
    llm_service._speed_observation_cache_identity = None
    llm_service._provider = "local"
    llm_service._model_id = "shared-model"
    llm_service._runtime_backend = "cpu"
    llm_service._runtime_model_size_gb = 4.0
    llm_service._runtime_speed_variant_digest = "b" * 64
    start_gate.wait(timeout=10)
    llm_service._record_response_metrics({
        "timings": {
            "prompt_per_second": 100.0,
            "predicted_per_second": 20.0,
        },
    })


class LlmRuntimeTests(unittest.TestCase):
    def tearDown(self):
        llm_service._hardware_cache = None
        llm_service._speed_observation_cache = None
        llm_service._speed_observation_cache_identity = None
        llm_service._speed_hardware_identity_cache.clear()
        llm_service._CUDA_BUILD_ATTEMPTED = False

    def test_enhancer_blocking_work_leaves_async_status_polling_responsive(self):
        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(launch_path))
        wrapper = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_enhance_with_wangp"
        )
        started = threading.Event()
        release = threading.Event()

        def blocking_enhancer(*_args):
            started.set()
            release.wait(timeout=2)
            return {"enhanced": "done"}

        namespace = {
            "asyncio": asyncio,
            "_enhance_with_wangp_sync": blocking_enhancer,
        }
        module = ast.Module(body=[wrapper], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(launch_path), "exec"), namespace)

        async def exercise():
            task = asyncio.create_task(namespace["_enhance_with_wangp"]("prompt", "video", 1))
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.001)
            self.assertTrue(started.is_set())
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            self.assertEqual(await task, {"enhanced": "done"})

        asyncio.run(exercise())
        endpoint = ast.get_source_segment(source, next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "llm_enhance_prompt"
        ))
        self.assertIn("result = await run_blocking_shielded(", endpoint)
        self.assertIn("_run_authorized_llm_with_selection", endpoint)
        self.assertNotIn("llm_service.load_model(", endpoint)

    def _load_wangp_sync_for_test(self, generate_cinematic_prompt):
        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(launch_path))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_enhance_with_wangp_sync"
        )

        class OffloadState:
            def __init__(self):
                self.unloads = 0

            def unload_all(self):
                self.unloads += 1

        offload_state = OffloadState()
        prompt_enhancer_lock = threading.RLock()
        native_gpu_execution_lock = threading.Lock()

        def acquire_prompt_enhancer_lock(cancel_checkpoint=None):
            while True:
                if callable(cancel_checkpoint):
                    cancel_checkpoint()
                if prompt_enhancer_lock.acquire(timeout=0.01):
                    try:
                        if callable(cancel_checkpoint):
                            cancel_checkpoint()
                    except BaseException:
                        prompt_enhancer_lock.release()
                        raise
                    return

        def acquire_native_gpu_execution_lock(cancel_checkpoint=None):
            while True:
                if callable(cancel_checkpoint):
                    cancel_checkpoint()
                if native_gpu_execution_lock.acquire(timeout=0.01):
                    try:
                        if callable(cancel_checkpoint):
                            cancel_checkpoint()
                    except BaseException:
                        native_gpu_execution_lock.release()
                        raise
                    return

        fake_wgp = types.SimpleNamespace(
            enhancer_offloadobj=offload_state,
            prompt_enhancer_lock=prompt_enhancer_lock,
            acquire_prompt_enhancer_lock=acquire_prompt_enhancer_lock,
            native_gpu_execution_lock=native_gpu_execution_lock,
            acquire_native_gpu_execution_lock=(
                acquire_native_gpu_execution_lock
            ),
            prompt_enhancer_llm_model=object(),
            prompt_enhancer_llm_tokenizer=object(),
            prompt_enhancer_image_caption_model=object(),
            prompt_enhancer_image_caption_processor=object(),
            server_config={
                "prompt_enhancer_temperature": 0.6,
                "prompt_enhancer_top_p": 0.9,
                "prompt_enhancer_randomize_seed": False,
            },
        )
        fake_pil = types.ModuleType("PIL")
        fake_pil.Image = types.SimpleNamespace(
            open=lambda _path: self.fail("unexpected image open"),
        )
        fake_prompt = types.ModuleType(
            "shared.prompt_enhancer.prompt_enhance_utils",
        )
        fake_prompt.generate_cinematic_prompt = generate_cinematic_prompt
        fake_mmgp = types.ModuleType("mmgp")
        fake_mmgp.offload = types.SimpleNamespace(
            profile=lambda *_args, **_kwargs: offload_state,
        )
        namespace = {
            "os": os,
            "wgp": fake_wgp,
            "_gen_lock": threading.Lock(),
        }
        module_patches = {
            "PIL": fake_pil,
            "shared.prompt_enhancer.prompt_enhance_utils": fake_prompt,
            "mmgp": fake_mmgp,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[function], type_ignores=[],
        )), str(launch_path), "exec"), namespace)
        return (
            namespace["_enhance_with_wangp_sync"],
            namespace,
            offload_state,
            module_patches,
        )

    def test_wangp_requests_serialize_global_state_and_cleanup_each_pass(self):
        from concurrent.futures import ThreadPoolExecutor

        state_lock = threading.Lock()
        first_entered = threading.Event()
        release_first = threading.Event()
        state = {"active": 0, "maximum": 0, "calls": 0}

        def generate(*_args, **_kwargs):
            with state_lock:
                state["calls"] += 1
                call = state["calls"]
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            try:
                if call == 1:
                    first_entered.set()
                    release_first.wait(timeout=2)
                return [f"enhanced-{call}"]
            finally:
                with state_lock:
                    state["active"] -= 1

        enhance, _namespace, offload_state, modules = (
            self._load_wangp_sync_for_test(generate)
        )
        with mock.patch.dict(sys.modules, modules), ThreadPoolExecutor(
            max_workers=2,
        ) as executor:
            first = executor.submit(enhance, "first", "video", 1)
            self.assertTrue(first_entered.wait(timeout=1))
            second = executor.submit(enhance, "second", "video", 1)
            self.assertFalse(second.done())
            self.assertEqual(state["calls"], 1)
            release_first.set()
            self.assertEqual(first.result(timeout=2)["enhanced"], "enhanced-1")
            self.assertEqual(second.result(timeout=2)["enhanced"], "enhanced-2")
        self.assertEqual(state["maximum"], 1)
        self.assertEqual(offload_state.unloads, 2)

    def test_fastapi_and_classic_wangp_enhance_share_one_process_lane(self):
        from concurrent.futures import ThreadPoolExecutor

        classic_entered = threading.Event()
        release_classic = threading.Event()
        inference_entered = threading.Event()

        def generate(*_args, **_kwargs):
            inference_entered.set()
            return ["api result"]

        enhance, namespace, _offload_state, modules = (
            self._load_wangp_sync_for_test(generate)
        )
        fake_wgp = namespace["wgp"]

        def classic_enhance():
            fake_wgp.acquire_prompt_enhancer_lock()
            try:
                classic_entered.set()
                release_classic.wait(timeout=2)
            finally:
                fake_wgp.prompt_enhancer_lock.release()

        with mock.patch.dict(sys.modules, modules), ThreadPoolExecutor(
            max_workers=2,
        ) as executor:
            classic = executor.submit(classic_enhance)
            self.assertTrue(classic_entered.wait(timeout=1))
            api = executor.submit(enhance, "prompt", "video", 1)
            self.assertFalse(inference_entered.wait(timeout=0.1))
            release_classic.set()
            classic.result(timeout=2)
            self.assertEqual(api.result(timeout=2)["enhanced"], "api result")
        self.assertTrue(inference_entered.is_set())

        wgp_source = (Path(__file__).resolve().parents[1] / "app" / "wgp.py").read_text(
            encoding="utf-8",
        )
        wgp_tree = ast.parse(wgp_source)
        classic_wrapper = next(
            node for node in wgp_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "enhance_prompt"
        )
        wrapper_source = ast.get_source_segment(wgp_source, classic_wrapper) or ""
        self.assertIn("acquire_prompt_enhancer_lock()", wrapper_source)
        self.assertIn("prompt_enhancer_lock.release()", wrapper_source)

    def test_fastapi_wangp_cancel_while_waiting_for_classic_releases_gen_lane(self):
        from concurrent.futures import ThreadPoolExecutor
        from services.llm_cancellation import (
            LlmCancellationHandle,
            LlmRequestCancelled,
        )

        enhance, namespace, offload_state, modules = (
            self._load_wangp_sync_for_test(
                lambda *_args, **_kwargs: self.fail("inference must not start")
            )
        )
        fake_wgp = namespace["wgp"]
        fake_wgp.prompt_enhancer_lock.acquire()
        cancellation = LlmCancellationHandle()
        try:
            with mock.patch.dict(sys.modules, modules), ThreadPoolExecutor(
                max_workers=1,
            ) as executor:
                future = executor.submit(
                    enhance, "prompt", "video", 1, None, cancellation,
                )
                for _ in range(100):
                    if namespace["_gen_lock"].locked():
                        break
                    time.sleep(0.005)
                self.assertTrue(namespace["_gen_lock"].locked())
                cancellation.cancel()
                with self.assertRaises(LlmRequestCancelled):
                    future.result(timeout=2)
        finally:
            fake_wgp.prompt_enhancer_lock.release()
        self.assertEqual(offload_state.unloads, 0)
        self.assertTrue(namespace["_gen_lock"].acquire(blocking=False))
        namespace["_gen_lock"].release()

    def test_api_cancel_while_generation_enhancer_owns_lane_does_not_cleanup_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        from services.llm_cancellation import (
            LlmCancellationHandle,
            LlmRequestCancelled,
        )

        enhance, api_namespace, offload_state, modules = (
            self._load_wangp_sync_for_test(
                lambda *_args, **_kwargs: self.fail("API inference must not start")
            )
        )
        fake_wgp = api_namespace["wgp"]
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        wgp_source = wgp_path.read_text(encoding="utf-8")
        wgp_tree = ast.parse(wgp_source)
        generation_node = next(
            node for node in wgp_tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_process_prompt_enhancer_for_generation"
        )
        generation_entered = threading.Event()
        release_generation = threading.Event()
        generation_unloads = []

        def generation_process(*_args, **_kwargs):
            fake_wgp.acquire_prompt_enhancer_lock()
            try:
                generation_entered.set()
                release_generation.wait(timeout=2)
                return ["generation result"]
            finally:
                fake_wgp.prompt_enhancer_lock.release()

        def generation_unload():
            fake_wgp.acquire_prompt_enhancer_lock()
            try:
                generation_unloads.append(True)
            finally:
                fake_wgp.prompt_enhancer_lock.release()

        generation_namespace = {
            "acquire_prompt_enhancer_lock": (
                fake_wgp.acquire_prompt_enhancer_lock
            ),
            "prompt_enhancer_lock": fake_wgp.prompt_enhancer_lock,
            "process_prompt_enhancer": generation_process,
            "unload_prompt_enhancer_runtime": generation_unload,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[generation_node], type_ignores=[],
        )), str(wgp_path), "exec"), generation_namespace)
        generation_enhance = generation_namespace[
            "_process_prompt_enhancer_for_generation"
        ]
        cancellation = LlmCancellationHandle()

        with mock.patch.dict(sys.modules, modules), ThreadPoolExecutor(
            max_workers=2,
        ) as executor:
            generation = executor.submit(
                generation_enhance,
                {}, "T", ["prompt"], None, None, False, False, 0,
            )
            self.assertTrue(generation_entered.wait(timeout=1))
            api = executor.submit(
                enhance, "api prompt", "video", 1, None, cancellation,
            )
            for _ in range(100):
                if api_namespace["_gen_lock"].locked():
                    break
                time.sleep(0.005)
            self.assertTrue(api_namespace["_gen_lock"].locked())
            cancellation.cancel()
            with self.assertRaises(LlmRequestCancelled):
                api.result(timeout=2)
            self.assertEqual(offload_state.unloads, 0)
            self.assertEqual(generation_unloads, [])
            release_generation.set()
            self.assertEqual(generation.result(timeout=2), ["generation result"])

        self.assertEqual(generation_unloads, [True])
        self.assertTrue(api_namespace["_gen_lock"].acquire(blocking=False))
        api_namespace["_gen_lock"].release()

        for function_name in (
            "setup_prompt_enhancer",
            "process_prompt_enhancer",
            "unload_prompt_enhancer_runtime",
        ):
            function_node = next(
                node for node in wgp_tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == function_name
            )
            function_source = (
                ast.get_source_segment(wgp_source, function_node) or ""
            )
            self.assertIn("acquire_prompt_enhancer_lock", function_source)
            self.assertIn("prompt_enhancer_lock.release()", function_source)

    def test_fastapi_wangp_waits_for_full_classic_native_generation_lifetime(self):
        from concurrent.futures import ThreadPoolExecutor

        inference_entered = threading.Event()

        def generate(*_args, **_kwargs):
            inference_entered.set()
            return ["api result"]

        enhance, namespace, _offload_state, modules = (
            self._load_wangp_sync_for_test(generate)
        )
        fake_wgp = namespace["wgp"]
        fake_wgp.native_gpu_execution_lock.acquire()
        try:
            with mock.patch.dict(sys.modules, modules), ThreadPoolExecutor(
                max_workers=1,
            ) as executor:
                api = executor.submit(enhance, "prompt", "video", 1)
                for _ in range(100):
                    if namespace["_gen_lock"].locked():
                        break
                    time.sleep(0.005)
                self.assertTrue(namespace["_gen_lock"].locked())
                self.assertFalse(inference_entered.wait(timeout=0.1))
                fake_wgp.native_gpu_execution_lock.release()
                self.assertEqual(
                    api.result(timeout=2)["enhanced"], "api result",
                )
        finally:
            if not inference_entered.is_set():
                fake_wgp.native_gpu_execution_lock.release()
        self.assertTrue(inference_entered.is_set())

    def test_fastapi_generation_and_model_release_share_classic_native_lane(self):
        from concurrent.futures import ThreadPoolExecutor

        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        slot_node = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "_WgpNativeGpuExecutionSlot"
        )
        release_helper_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_release_wgp_model_with_native_gpu_exclusion"
        )
        release_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "system_release_model"
        )
        release_node.decorator_list = []
        generation_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_run_generation"
        )
        generation_source = ast.get_source_segment(
            source, generation_node,
        ) or ""
        self.assertIn("generation_slot(", generation_source)
        self.assertIn("_WgpNativeGpuExecutionSlot(\n        acquired,", generation_source)
        self.assertIn(
            "cancel_checkpoint=lambda: "
            "_generation_native_gpu_cancel_checkpoint(",
            generation_source,
        )
        self.assertIn("_recover_dead_async_listener(Listener)", generation_source)
        self.assertIn("worker_started.wait(timeout=2.0)", generation_source)
        wgp_source = (
            Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        ).read_text(encoding="utf-8")
        wgp_tree = ast.parse(wgp_source)
        for function_name in (
            "release_RAM",
            "preload_model_when_switching",
            "unload_model_if_needed",
        ):
            function_node = next(
                node for node in wgp_tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == function_name
            )
            function_source = ast.get_source_segment(
                wgp_source, function_node,
            ) or ""
            self.assertIn("native_gpu_execution_lock", function_source)

        native_execution = threading.Lock()
        release_calls = []
        fake_wgp = types.SimpleNamespace(
            native_gpu_execution_lock=native_execution,
            acquire_native_gpu_execution_lock=native_execution.acquire,
            wan_model=object(),
            offloadobj=None,
            release_model=lambda: release_calls.append("released"),
        )
        slot_local = threading.local()
        namespace = {
            "wgp": fake_wgp,
            "_wgp_native_gpu_slot_state": slot_local,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[slot_node, release_helper_node], type_ignores=[],
        )), str(launch_path), "exec"), namespace)
        slot_class = namespace["_WgpNativeGpuExecutionSlot"]
        generation_entered = threading.Event()
        release_generation = threading.Event()

        def run_generation():
            with slot_class() as acquired:
                generation_entered.set()
                release_generation.wait(timeout=1)
                return acquired

        native_execution.acquire()
        with ThreadPoolExecutor(max_workers=1) as executor:
            generation = executor.submit(run_generation)
            self.assertFalse(generation_entered.wait(timeout=0.1))
            native_execution.release()
            self.assertTrue(generation_entered.wait(timeout=1))
            release_generation.set()
            self.assertTrue(generation.result(timeout=1))

        class HttpError(Exception):
            def __init__(self, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        release_namespace = {
            "_jobs": {},
            "_gen_lock": threading.Lock(),
            "wgp": fake_wgp,
            "HTTPException": HttpError,
            "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(
                is_available=lambda: False,
                empty_cache=lambda: None,
            )),
            "_WgpNativeGpuExecutionSlot": (
                namespace["_WgpNativeGpuExecutionSlot"]
            ),
            "_release_wgp_model_with_native_gpu_exclusion": (
                namespace["_release_wgp_model_with_native_gpu_exclusion"]
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[release_node], type_ignores=[],
        )), str(launch_path), "exec"), release_namespace)
        director_pipeline = types.ModuleType("services.director_pipeline")
        director_pipeline._pipelines = {}
        native_execution.acquire()
        with mock.patch.dict(
            sys.modules,
            {"services.director_pipeline": director_pipeline},
        ):
            with self.assertRaises(HttpError) as raised:
                release_namespace["system_release_model"]()
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(release_calls, [])
            self.assertTrue(
                release_namespace["_gen_lock"].acquire(blocking=False)
            )
            release_namespace["_gen_lock"].release()
            native_execution.release()
            self.assertEqual(
                release_namespace["system_release_model"](),
                {"released": ["generation model"]},
            )
        self.assertEqual(release_calls, ["released"])

    def test_safe_output_yield_releases_native_before_generation_slot(self):
        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        nodes = [
            node for node in tree.body
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "_WgpNativeGpuExecutionSlot"
            ) or (
                isinstance(node, ast.FunctionDef)
                and node.name == "_yield_current_native_gpu_slot"
            )
        ]
        native_gpu = threading.Lock()
        gen_lock = threading.Lock()
        namespace = {
            "threading": threading,
            "wgp": types.SimpleNamespace(
                native_gpu_execution_lock=native_gpu,
                acquire_native_gpu_execution_lock=native_gpu.acquire,
            ),
            "_wgp_native_gpu_slot_state": threading.local(),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(launch_path), "exec"), namespace)
        slot = namespace["_WgpNativeGpuExecutionSlot"]()
        gen_lock.acquire()
        self.assertTrue(slot.__enter__())

        follower_entered = threading.Event()
        release_follower = threading.Event()

        def follower():
            gen_lock.acquire()
            native_gpu.acquire()
            follower_entered.set()
            release_follower.wait(timeout=1)
            native_gpu.release()
            gen_lock.release()

        thread = threading.Thread(target=follower)
        thread.start()
        yielded = namespace["_yield_current_native_gpu_slot"]()
        self.assertIs(yielded, slot)
        self.assertFalse(native_gpu.locked())
        gen_lock.release()
        self.assertTrue(follower_entered.wait(timeout=1))
        release_follower.set()
        gen_lock.acquire()
        self.assertTrue(yielded.acquire())
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(gen_lock.locked())
        self.assertTrue(native_gpu.locked())
        slot.__exit__(None, None, None)
        gen_lock.release()
        self.assertFalse(native_gpu.locked())

        generation_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_run_generation"
        )
        generation_source = ast.get_source_segment(
            source, generation_node,
        ) or ""
        self.assertEqual(
            generation_source.count("_yield_current_native_gpu_slot()"), 2,
        )
        self.assertEqual(
            generation_source.count(
                "yield_generation_slot_after_output(_gen_lock, job)"
            ),
            1,
        )
        for marker in (
            "resumed = yield_generation_slot_after_output(_gen_lock, job)",
            "if not yield_generation_slot_after_output(\n"
            "                                _gen_lock, job,",
        ):
            yield_at = generation_source.index(marker)
            release_at = generation_source.rfind(
                "_yield_current_native_gpu_slot()", 0, yield_at,
            )
            resume_at = generation_source.index(
                "yielded_native_slot.acquire()", yield_at,
            )
            self.assertGreaterEqual(release_at, 0)
            self.assertLess(release_at, yield_at)
            self.assertLess(yield_at, resume_at)

    def test_cancelled_api_waiter_releases_generation_lock_before_native_lane(self):
        from concurrent.futures import ThreadPoolExecutor

        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_WgpNativeGpuWaitCancelled",
            "_WgpNativeGpuExecutionSlot",
        }
        nodes = [
            node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            and node.name in wanted
        ]
        native_gpu = threading.Lock()
        gen_lock = threading.Lock()
        cancelled = threading.Event()

        def checkpoint():
            if cancelled.is_set():
                raise namespace["_WgpNativeGpuWaitCancelled"]("cancelled")

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

        namespace = {
            "threading": threading,
            "wgp": types.SimpleNamespace(
                native_gpu_execution_lock=native_gpu,
                acquire_native_gpu_execution_lock=acquire_native,
            ),
            "_wgp_native_gpu_slot_state": threading.local(),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(launch_path), "exec"), namespace)

        native_gpu.acquire()

        def wait_for_native():
            with gen_lock:
                with namespace["_WgpNativeGpuExecutionSlot"](
                    cancel_checkpoint=checkpoint,
                ) as acquired:
                    return acquired

        with ThreadPoolExecutor(max_workers=1) as executor:
            waiting = executor.submit(wait_for_native)
            for _ in range(100):
                if gen_lock.locked():
                    break
                time.sleep(0.005)
            self.assertTrue(gen_lock.locked())
            cancelled.set()
            self.assertFalse(waiting.result(timeout=1))
        self.assertFalse(gen_lock.locked())
        self.assertTrue(native_gpu.locked())
        native_gpu.release()

        generation_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_run_generation"
        )
        generation_source = ast.get_source_segment(
            source, generation_node,
        ) or ""
        self.assertIn(
            "cancel_checkpoint=lambda: "
            "_generation_native_gpu_cancel_checkpoint(",
            generation_source,
        )
        self.assertIn("as native_acquired", generation_source)

    def test_cuda_llm_waits_for_classic_native_lane_but_cpu_does_not(self):
        from concurrent.futures import ThreadPoolExecutor

        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_WgpNativeGpuExecutionSlot",
            "_local_llm_uses_native_gpu",
            "_run_llm_with_selection",
        }
        nodes = [
            node for node in tree.body
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef))
                and node.name in wanted
            )
        ]
        native_gpu = threading.Lock()
        fake_wgp = types.SimpleNamespace(
            native_gpu_execution_lock=native_gpu,
            acquire_native_gpu_execution_lock=native_gpu.acquire,
            transformer_type="",
            wan_model=None,
            offloadobj=None,
        )
        namespace = {
            "Any": __import__("typing").Any,
            "Mapping": __import__("typing").Mapping,
            "threading": threading,
            "wgp": fake_wgp,
            "_wgp_native_gpu_slot_state": threading.local(),
            "_gen_lock": threading.Lock(),
            "_release_wgp_model_with_native_gpu_exclusion": (
                lambda: self.fail("unexpected model release")
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(launch_path), "exec"), namespace)

        class LoadedLease:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        fake_llm = types.ModuleType("services.llm_service")
        fake_llm.loaded_model_lease = lambda **_kwargs: LoadedLease()
        services = types.ModuleType("services")
        services.llm_service = fake_llm
        operation_entered = threading.Event()

        def operation():
            operation_entered.set()
            return "done"

        cuda_selection = {
            "provider": "local", "device": "cuda", "model_id": "m",
        }
        native_gpu.acquire()
        with mock.patch.dict(sys.modules, {
            "services": services,
            "services.llm_service": fake_llm,
        }), ThreadPoolExecutor(max_workers=1) as executor:
            cuda = executor.submit(
                namespace["_run_llm_with_selection"],
                cuda_selection,
                operation,
            )
            for _ in range(100):
                if namespace["_gen_lock"].locked():
                    break
                time.sleep(0.005)
            self.assertTrue(namespace["_gen_lock"].locked())
            self.assertFalse(operation_entered.wait(timeout=0.1))
            native_gpu.release()
            self.assertEqual(cuda.result(timeout=1), "done")

            operation_entered.clear()
            native_gpu.acquire()
            cpu = executor.submit(
                namespace["_run_llm_with_selection"],
                {**cuda_selection, "device": "cpu"},
                operation,
            )
            self.assertEqual(cpu.result(timeout=1), "done")
            self.assertTrue(operation_entered.is_set())
            native_gpu.release()

        unload_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "llm_unload"
        )
        unload_node.decorator_list = []
        namespace.update({
            "Request": object,
            "_require_local_llm_control": lambda _request: None,
        })
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[unload_node], type_ignores=[],
        )), str(launch_path), "exec"), namespace)

        runtime_device = {"value": "cpu"}
        status_read = threading.Event()
        unloaded = threading.Event()

        def get_status():
            status_read.set()
            return {
                "provider": "local",
                "device": runtime_device["value"],
            }

        fake_llm.get_status = get_status
        fake_llm.unload_model = unloaded.set
        namespace["_gen_lock"].acquire()
        native_gpu.acquire()
        with mock.patch.dict(sys.modules, {
            "services": services,
            "services.llm_service": fake_llm,
        }), ThreadPoolExecutor(max_workers=1) as executor:
            unload = executor.submit(
                namespace["llm_unload"], object(),
            )
            self.assertFalse(status_read.wait(timeout=0.1))
            runtime_device["value"] = "cuda"
            namespace["_gen_lock"].release()
            self.assertTrue(status_read.wait(timeout=1))
            self.assertFalse(unloaded.wait(timeout=0.1))
            native_gpu.release()
            self.assertEqual(unload.result(timeout=1), {"status": "ok"})
        self.assertTrue(unloaded.is_set())

    def test_direct_local_llm_callers_use_configured_gpu_guard(self):
        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name in (
            "_generate_and_save_lora_guide",
            "_generate_and_save_checkpoint_guide",
            "blender_director_plan",
            "blender_director_finalize",
        ):
            function_node = next(
                node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function_name
            )
            function_source = ast.get_source_segment(
                source, function_node,
            ) or ""
            self.assertIn(
                "_run_configured_llm_operation", function_source,
                function_name,
            )
            self.assertNotIn(
                "llm_service.generate(", function_source,
                function_name,
            )

        helper_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_run_configured_llm_operation"
        )
        calls = []
        namespace = {
            "_configured_llm_selection": lambda: {
                "provider": "local", "device": "cuda", "model_id": "m",
            },
            "_run_llm_with_selection": (
                lambda selection, operation, *args, **kwargs:
                calls.append((selection, operation, args, kwargs)) or "done"
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[helper_node], type_ignores=[],
        )), str(launch_path), "exec"), namespace)
        operation = object()
        self.assertEqual(
            namespace["_run_configured_llm_operation"](
                operation, "prompt", temperature=0.2,
            ),
            "done",
        )
        self.assertEqual(calls[0][0]["device"], "cuda")
        self.assertIs(calls[0][1], operation)
        self.assertEqual(calls[0][2], ("prompt",))
        self.assertEqual(calls[0][3], {"temperature": 0.2})

    def test_blender_render_only_uses_shared_native_gpu_lane(self):
        from concurrent.futures import ThreadPoolExecutor

        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"_WgpNativeGpuExecutionSlot", "_BlenderGpuRenderSlot"}
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name in wanted
        ]
        native_gpu = threading.Lock()
        namespace = {
            "threading": threading,
            "wgp": types.SimpleNamespace(
                native_gpu_execution_lock=native_gpu,
                acquire_native_gpu_execution_lock=native_gpu.acquire,
            ),
            "_gen_lock": threading.Lock(),
            "_wgp_native_gpu_slot_state": threading.local(),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(launch_path), "exec"), namespace)

        render_entered = threading.Event()

        def run_render():
            with namespace["_BlenderGpuRenderSlot"]() as acquired:
                self.assertTrue(acquired)
                render_entered.set()

        native_gpu.acquire()
        with ThreadPoolExecutor(max_workers=1) as executor:
            render = executor.submit(run_render)
            for _ in range(100):
                if namespace["_gen_lock"].locked():
                    break
                time.sleep(0.005)
            self.assertTrue(namespace["_gen_lock"].locked())
            self.assertFalse(render_entered.wait(timeout=0.1))
            native_gpu.release()
            render.result(timeout=1)
        self.assertFalse(native_gpu.locked())
        self.assertFalse(namespace["_gen_lock"].locked())

        native_gpu.acquire()
        try:
            with namespace["_BlenderGpuRenderSlot"](False) as acquired:
                self.assertFalse(acquired)
                self.assertFalse(namespace["_gen_lock"].locked())
        finally:
            native_gpu.release()

        for function_name in (
            "_invoke_blender_project_tool",
            "blender_render_preview",
            "blender_director_finalize",
        ):
            function_node = next(
                node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function_name
            )
            function_source = ast.get_source_segment(
                source, function_node,
            ) or ""
            self.assertIn("_BlenderGpuRenderSlot", function_source)
        generic_source = ast.get_source_segment(source, next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_invoke_blender_project_tool"
        )) or ""
        self.assertIn('tool == "render_preview"', generic_source)
        finalize_source = ast.get_source_segment(source, next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "blender_director_finalize"
        )) or ""
        self.assertIn(
            'with _BlenderGpuRenderSlot():\n'
            '                    rendered = service.invoke("render_animation"',
            finalize_source,
        )

    def test_native_gpu_lease_outlives_closed_generation_iterator(self):
        from concurrent.futures import ThreadPoolExecutor

        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"_NativeGpuExecutionLease", "process_tasks"}
        nodes = [
            node for node in tree.body
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef))
                and node.name in wanted
            )
        ]
        inference_entered = threading.Event()

        def generate(*_args, **_kwargs):
            inference_entered.set()
            return ["api result"]

        enhance, api_namespace, _offload_state, modules = (
            self._load_wangp_sync_for_test(generate)
        )
        native_execution = (
            api_namespace["wgp"].native_gpu_execution_lock
        )
        worker_started = threading.Event()
        release_worker = threading.Event()
        worker_released = threading.Event()
        owner_releases = []
        workers = []

        def fake_inner(state, lease):
            self.assertTrue(lease.start_worker())

            def worker():
                worker_started.set()
                release_worker.wait(timeout=2)
                lease.release(
                    lambda: owner_releases.append(state),
                )
                worker_released.set()

            thread = threading.Thread(target=worker)
            workers.append(thread)
            thread.start()
            yield "started"

        namespace = {
            "threading": threading,
            "native_gpu_execution_lock": native_execution,
            "acquire_native_gpu_execution_lock": (
                lambda _checkpoint=None: native_execution.acquire()
            ),
            "_next_native_gpu_execution_owner_token": lambda: 17,
            "_begin_native_generation_request": lambda *_args: None,
            "_checkpoint_native_generation_request": lambda *_args: None,
            "_release_native_generation_request": lambda *_args: None,
            "_NativeGpuExecutionCancelled": InterruptedError,
            "_process_tasks_with_native_gpu": fake_inner,
            "_release_native_generation_owner": (
                lambda state, _owner_token: owner_releases.append(state)
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(wgp_path), "exec"), namespace)

        state = {"gen": {}}
        iterator = namespace["process_tasks"](state)
        self.assertEqual(next(iterator), "started")
        self.assertTrue(worker_started.wait(timeout=1))
        iterator.close()
        self.assertTrue(native_execution.locked())
        self.assertEqual(owner_releases, [])

        with mock.patch.dict(sys.modules, modules), ThreadPoolExecutor(
            max_workers=1,
        ) as executor:
            api = executor.submit(enhance, "prompt", "video", 1)
            for _ in range(100):
                if api_namespace["_gen_lock"].locked():
                    break
                time.sleep(0.005)
            self.assertTrue(api_namespace["_gen_lock"].locked())
            self.assertFalse(inference_entered.wait(timeout=0.1))
            release_worker.set()
            self.assertTrue(worker_released.wait(timeout=1))
            self.assertEqual(
                api.result(timeout=2)["enhanced"], "api result",
            )
        workers[0].join(timeout=1)
        self.assertFalse(native_execution.locked())
        self.assertEqual(owner_releases, [state])

    def test_native_gpu_lease_unlocks_when_owner_cleanup_raises(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lease_node = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "_NativeGpuExecutionLease"
        )

        for method_name, transfer in (
            ("release", True),
            ("release_if_untransferred", False),
        ):
            with self.subTest(method=method_name):
                native_execution = threading.Lock()
                namespace = {
                    "threading": threading,
                    "native_gpu_execution_lock": native_execution,
                    "acquire_native_gpu_execution_lock": (
                        native_execution.acquire
                    ),
                    "_next_native_gpu_execution_owner_token": lambda: 17,
                }
                exec(compile(ast.fix_missing_locations(ast.Module(
                    body=[lease_node], type_ignores=[],
                )), str(wgp_path), "exec"), namespace)
                lease = namespace["_NativeGpuExecutionLease"]()
                if transfer:
                    self.assertTrue(lease.start_worker())

                def fail_cleanup():
                    raise RuntimeError("cleanup failed")

                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    getattr(lease, method_name)(fail_cleanup)
                self.assertFalse(native_execution.locked())
                self.assertFalse(lease.release())

    def test_native_worker_dispatch_failures_never_wedge_gpu_lease(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_NativeGpuExecutionLease",
            "_recover_dead_async_listener",
            "_schedule_native_gpu_worker",
        }
        nodes = [
            node for node in tree.body
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef))
                and node.name in wanted
            )
        ]

        class DeadThread:
            ident = 123

            @staticmethod
            def is_alive():
                return False

        class UnstartedThread:
            ident = None

            @staticmethod
            def is_alive():
                return False

        class Listener:
            lock = threading.Lock()
            thread = DeadThread()

        native_execution = threading.Lock()
        namespace = {
            "threading": threading,
            "time": time,
            "native_gpu_execution_lock": native_execution,
            "acquire_native_gpu_execution_lock": native_execution.acquire,
            "_next_native_gpu_execution_owner_token": lambda: 17,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(wgp_path), "exec"), namespace)
        lease_class = namespace["_NativeGpuExecutionLease"]
        schedule = namespace["_schedule_native_gpu_worker"]
        cleanup = []

        failed_lease = lease_class()

        def fail_start(_worker, **_kwargs):
            Listener.thread = UnstartedThread()
            raise RuntimeError("thread start failed")

        with self.assertRaisesRegex(RuntimeError, "thread start failed"):
            schedule(
                fail_start,
                Listener,
                lambda: None,
                failed_lease,
                lambda: cleanup.append("failed"),
                start_timeout=0.01,
            )
        self.assertFalse(native_execution.locked())
        self.assertIsNone(Listener.thread)
        self.assertEqual(cleanup, ["failed"])

        unstarted = UnstartedThread()
        Listener.thread = unstarted
        self.assertFalse(
            namespace["_recover_dead_async_listener"](Listener)
        )
        self.assertIs(Listener.thread, unstarted)

        timed_out_lease = lease_class()
        with self.assertRaisesRegex(
            RuntimeError, "native generation worker did not start",
        ):
            schedule(
                lambda _worker, **_kwargs: None,
                Listener,
                lambda: None,
                timed_out_lease,
                lambda: cleanup.append("timed_out"),
                start_timeout=0.01,
            )
        self.assertFalse(native_execution.locked())
        self.assertIsNone(Listener.thread)

        Listener.thread = DeadThread()
        recovered_lease = lease_class()

        def run_after_recovery(worker, **_kwargs):
            self.assertIsNone(Listener.thread)
            worker()

        def worker():
            self.assertTrue(recovered_lease.start_worker())
            recovered_lease.release(
                lambda: cleanup.append("recovered"),
            )

        schedule(
            run_after_recovery,
            Listener,
            worker,
            recovered_lease,
            lambda: cleanup.append("unexpected"),
            start_timeout=0.01,
        )
        self.assertFalse(native_execution.locked())
        self.assertEqual(
            cleanup, ["failed", "timed_out", "recovered"],
        )

    def test_detached_classic_start_failure_and_iterator_close_clear_busy_epoch(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_NativeGpuExecutionLease",
            "_recover_dead_async_listener",
            "_schedule_native_gpu_worker",
            "_release_native_generation_owner",
            "process_tasks",
        }
        nodes = [
            node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            and node.name in wanted
        ]
        native_gpu = threading.Lock()
        namespace = {
            "threading": threading,
            "time": time,
            "native_gpu_execution_lock": native_gpu,
            "acquire_native_gpu_execution_lock": (
                lambda _checkpoint=None: native_gpu.acquire()
            ),
            "_next_native_gpu_execution_owner_token": iter((17, 18)).__next__,
            "get_gen_info": lambda state: state["gen"],
            "gen_lock": threading.Lock(),
            "gen_in_progress": False,
            "_NATIVE_GENERATION_OWNER_KEY": (
                "_maestro_native_generation_owner"
            ),
            "_NATIVE_GENERATION_REQUEST_KEY": (
                "_maestro_native_generation_request"
            ),
            "_begin_native_generation_request": lambda *_args: None,
            "_checkpoint_native_generation_request": lambda *_args: None,
            "_release_native_generation_request": lambda *_args: None,
            "_NativeGpuExecutionCancelled": InterruptedError,
        }

        def fake_inner(state, lease):
            state["gen"].update({
                "_maestro_native_generation_owner": lease.owner_token,
                "process_status": "process:main",
                "in_progress": True,
            })
            namespace["gen_in_progress"] = True
            yield "started"

        namespace["_process_tasks_with_native_gpu"] = fake_inner
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(wgp_path), "exec"), namespace)

        closed_state = {"gen": {}}
        iterator = namespace["process_tasks"](closed_state)
        self.assertEqual(next(iterator), "started")
        self.assertTrue(closed_state["gen"]["in_progress"])
        self.assertTrue(namespace["gen_in_progress"])
        iterator.close()
        self.assertNotIn("in_progress", closed_state["gen"])
        self.assertFalse(namespace["gen_in_progress"])
        self.assertIsNone(
            closed_state["gen"]["_maestro_native_generation_owner"]
        )
        self.assertFalse(native_gpu.locked())

        failed_state = {"gen": {
            "_maestro_native_generation_owner": 18,
            "process_status": "process:main",
            "in_progress": True,
        }}
        namespace["gen_in_progress"] = True
        failed_lease = namespace["_NativeGpuExecutionLease"]()

        class Listener:
            lock = threading.Lock()
            thread = None

        with self.assertRaisesRegex(RuntimeError, "start failed"):
            namespace["_schedule_native_gpu_worker"](
                lambda _worker, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("start failed")
                ),
                Listener,
                lambda: None,
                failed_lease,
                lambda: namespace["_release_native_generation_owner"](
                    failed_state, 18,
                ),
                start_timeout=0.01,
            )
        self.assertNotIn("in_progress", failed_state["gen"])
        self.assertFalse(namespace["gen_in_progress"])
        self.assertIsNone(
            failed_state["gen"]["_maestro_native_generation_owner"]
        )
        self.assertFalse(native_gpu.locked())

    def test_late_classic_finalizer_cannot_clear_successor_busy_epoch(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "finalize_generation"
        )
        function_source = ast.get_source_segment(
            source, function_node,
        ) or ""
        self.assertNotIn("gen_in_progress", function_source)
        self.assertNotIn('del gen["in_progress"]', function_source)

        ui = lambda *args, **kwargs: (args, kwargs)
        namespace = {
            "get_gen_info": lambda state: state["gen"],
            "gr": types.SimpleNamespace(
                Tabs=ui, Gallery=ui, update=ui, Button=ui,
                Column=ui, HTML=ui,
            ),
            "time": types.SimpleNamespace(sleep=lambda _seconds: None),
            "pack_audio_gallery_state": lambda *_args: (),
            "gen_in_progress": True,
            "_claim_native_generation_finalizer": lambda _state: False,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[function_node], type_ignores=[],
        )), str(wgp_path), "exec"), namespace)
        successor = {"gen": {
            "_maestro_native_generation_owner": 19,
            "process_status": "process:main",
            "in_progress": True,
            "file_list": [],
            "audio_file_list": [],
        }}
        namespace["finalize_generation"](successor)
        self.assertTrue(namespace["gen_in_progress"])
        self.assertTrue(successor["gen"]["in_progress"])
        self.assertEqual(
            successor["gen"]["_maestro_native_generation_owner"], 19,
        )

    def test_manual_cleanup_never_restores_exited_native_generation_owner(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_release_native_generation_owner",
            "_release_prompt_enhancer_gpu_resources",
        }
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        state = {"gen": {
            "process_status": "request:prompt_enhancer",
            "process_hierarchy": {"prompt_enhancer": "process:main"},
            "_maestro_native_generation_owner": 17,
        }}
        native_release_observations = []
        process_lock = threading.Lock()

        def native_release(current_state, process_id):
            # Exact shared/utils/process_locks.py release semantics.
            current = current_state["gen"]
            with process_lock:
                hierarchy = current.get("process_hierarchy", {})
                current["process_status"] = hierarchy.get(process_id, None)
                native_release_observations.append(current["process_status"])

        namespace = {
            "_NATIVE_GENERATION_OWNER_KEY": (
                "_maestro_native_generation_owner"
            ),
            "_NATIVE_GENERATION_REQUEST_KEY": (
                "_maestro_native_generation_request"
            ),
            "gen_lock": process_lock,
            "get_gen_info": lambda current_state: current_state["gen"],
            "release_GPU_ressources": native_release,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(wgp_path), "exec"), namespace)

        namespace["_release_native_generation_owner"](state, 17)
        self.assertIsNone(
            state["gen"]["_maestro_native_generation_owner"]
        )
        self.assertEqual(
            state["gen"]["process_status"], "process:prompt_enhancer",
        )
        namespace["_release_prompt_enhancer_gpu_resources"](state)
        self.assertEqual(native_release_observations, [None])
        self.assertIsNone(state["gen"]["process_status"])
        self.assertIsNone(
            state["gen"]["process_hierarchy"]["prompt_enhancer"]
        )

    def test_late_predecessor_cleanup_cannot_clear_successor_generation(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_release_native_generation_owner"
        )
        state = {"gen": {
            "process_status": "process:main",
            "_maestro_native_generation_owner": 18,
        }}
        namespace = {
            "_NATIVE_GENERATION_OWNER_KEY": (
                "_maestro_native_generation_owner"
            ),
            "_NATIVE_GENERATION_REQUEST_KEY": (
                "_maestro_native_generation_request"
            ),
            "gen_lock": threading.Lock(),
            "get_gen_info": lambda current_state: current_state["gen"],
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), str(wgp_path), "exec"), namespace)

        self.assertFalse(
            namespace["_release_native_generation_owner"](state, 17)
        )
        self.assertEqual(state["gen"]["process_status"], "process:main")
        self.assertEqual(
            state["gen"]["_maestro_native_generation_owner"], 18,
        )
        self.assertTrue(
            namespace["_release_native_generation_owner"](state, 18)
        )
        self.assertIsNone(state["gen"]["process_status"])

    def test_predecessor_post_worker_epoch_cannot_mutate_pending_successor(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_native_generation_request_is_current",
            "_release_native_generation_owner",
            "_process_tasks_with_native_gpu",
        }
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        state = {"gen": {
            "process_status": "process:main",
            "in_progress": True,
            "_maestro_native_generation_owner": 17,
            "_maestro_native_generation_request": 18,
            "status": "successor waiting",
        }}
        namespace = {
            "_NATIVE_GENERATION_OWNER_KEY": (
                "_maestro_native_generation_owner"
            ),
            "_NATIVE_GENERATION_REQUEST_KEY": (
                "_maestro_native_generation_request"
            ),
            "gen_lock": threading.Lock(),
            "gen_in_progress": True,
            "get_gen_info": lambda current_state: current_state["gen"],
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes[:2], type_ignores=[],
        )), str(wgp_path), "exec"), namespace)

        self.assertFalse(
            namespace["_native_generation_request_is_current"](state, 17)
        )
        self.assertTrue(
            namespace["_release_native_generation_owner"](state, 17)
        )
        self.assertTrue(state["gen"]["in_progress"])
        self.assertEqual(
            state["gen"]["_maestro_native_generation_request"], 18,
        )
        self.assertEqual(state["gen"]["status"], "successor waiting")
        worker_source = ast.get_source_segment(
            source,
            next(node for node in nodes if node.name == "_process_tasks_with_native_gpu"),
        ) or ""
        self.assertIn(
            "_mutate_native_generation_if_current(", worker_source,
        )
        self.assertIn(
            "if not _native_generation_request_is_current(", worker_source,
        )

    def test_pending_successor_stop_survives_predecessor_worker_and_finalizer(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_begin_native_generation_request",
            "_native_generation_request_is_current",
            "_checkpoint_native_generation_request",
            "_release_native_generation_request",
            "_release_native_generation_owner",
            "_claim_native_generation_finalizer",
            "_settle_native_generation_task_if_current",
            "_mutate_native_generation_if_current",
        }
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        namespace = {
            "_NATIVE_GENERATION_OWNER_KEY": (
                "_maestro_native_generation_owner"
            ),
            "_NATIVE_GENERATION_REQUEST_KEY": (
                "_maestro_native_generation_request"
            ),
            "_NATIVE_GENERATION_ABORT_REQUEST_KEY": (
                "_maestro_native_generation_abort_request"
            ),
            "_NATIVE_GENERATION_EARLY_STOP_REQUEST_KEY": (
                "_maestro_native_generation_early_stop_request"
            ),
            "_NATIVE_GENERATION_FINALIZER_QUEUE_KEY": (
                "_maestro_native_generation_finalizer_queue"
            ),
            "_NATIVE_GENERATION_LAST_SETTLED_KEY": (
                "_maestro_native_generation_last_settled"
            ),
            "gen_lock": threading.Lock(),
            "gen_in_progress": False,
            "get_gen_info": lambda current_state: current_state["gen"],
            "_NativeGpuExecutionCancelled": InterruptedError,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(wgp_path), "exec"), namespace)
        state = {"gen": {"process_status": "process:main"}}
        namespace["_begin_native_generation_request"](state, 17)
        state["gen"]["_maestro_native_generation_owner"] = 17
        settle_result = []
        settle_started = threading.Event()
        namespace["gen_lock"].acquire()
        try:
            def settle_predecessor():
                settle_started.set()
                settle_result.append(
                    namespace[
                        "_settle_native_generation_task_if_current"
                    ](state, 17)
                )

            settle_thread = threading.Thread(target=settle_predecessor)
            settle_thread.start()
            self.assertTrue(settle_started.wait(timeout=1))
            # Deterministically place successor registration + Stop inside
            # the same critical section that the predecessor must recheck.
            state["gen"].update({
                "_maestro_native_generation_request": 18,
                "_maestro_native_generation_abort_request": 18,
                "_maestro_native_generation_early_stop_request": 18,
                "abort": False,
                "early_stop": False,
                "early_stop_forwarded": False,
                "extra_orders": 3,
                "in_progress": True,
            })
            state["gen"][
                "_maestro_native_generation_finalizer_queue"
            ].append(18)
        finally:
            namespace["gen_lock"].release()
        settle_thread.join(timeout=1)
        self.assertFalse(settle_thread.is_alive())
        self.assertEqual(settle_result, [None])

        self.assertTrue(
            namespace["_release_native_generation_owner"](state, 17)
        )
        self.assertFalse(
            namespace["_native_generation_request_is_current"](state, 17)
        )
        self.assertFalse(
            namespace["_claim_native_generation_finalizer"](state)
        )
        self.assertEqual(
            state["gen"]["_maestro_native_generation_abort_request"], 18,
        )
        self.assertEqual(
            state["gen"][
                "_maestro_native_generation_early_stop_request"
            ],
            18,
        )
        self.assertEqual(state["gen"]["extra_orders"], 3)
        with self.assertRaises(InterruptedError):
            namespace["_checkpoint_native_generation_request"](state, 18)

        state["gen"]["_maestro_native_generation_owner"] = 18
        state["gen"]["process_status"] = "process:main"
        self.assertTrue(
            namespace["_release_native_generation_owner"](state, 18)
        )
        self.assertTrue(
            namespace["_release_native_generation_request"](state, 18)
        )
        self.assertTrue(
            namespace["_claim_native_generation_finalizer"](state)
        )

        abort_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "abort_generation"
        )

        class _GrStub:
            @staticmethod
            def Info(_message):
                return None

            @staticmethod
            def Button(**kwargs):
                return kwargs

        abort_namespace = {
            "get_gen_info": lambda current_state: current_state["gen"],
            "gen_lock": threading.Lock(),
            "wan_model": None,
            "gr": _GrStub,
            "_NATIVE_GENERATION_OWNER_KEY": (
                "_maestro_native_generation_owner"
            ),
            "_NATIVE_GENERATION_REQUEST_KEY": (
                "_maestro_native_generation_request"
            ),
            "_NATIVE_GENERATION_ABORT_REQUEST_KEY": (
                "_maestro_native_generation_abort_request"
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[abort_node], type_ignores=[],
        )), str(wgp_path), "exec"), abort_namespace)
        stopped_state = {"gen": {
            "in_progress": True,
            "_maestro_native_generation_owner": 17,
            "_maestro_native_generation_request": 18,
        }}
        abort_namespace["abort_generation"](stopped_state)
        self.assertEqual(
            stopped_state["gen"][
                "_maestro_native_generation_abort_request"
            ],
            18,
        )
        self.assertFalse(stopped_state["gen"]["abort"])

        command_state = {"gen": {
            "_maestro_native_generation_request": 17,
            "status": "predecessor",
            "queue": [{"id": "successor"}],
        }}
        command_results = []
        command_started = threading.Event()
        namespace["gen_lock"].acquire()
        try:
            def publish_predecessor_error():
                command_started.set()

                def mutation(current_gen):
                    current_gen["queue"].clear()
                    current_gen["status"] = "predecessor error"

                command_results.append(
                    namespace[
                        "_mutate_native_generation_if_current"
                    ](command_state, 17, mutation)
                )

            command_thread = threading.Thread(
                target=publish_predecessor_error
            )
            command_thread.start()
            self.assertTrue(command_started.wait(timeout=1))
            command_state["gen"].update({
                "_maestro_native_generation_request": 18,
                "status": "successor running",
            })
        finally:
            namespace["gen_lock"].release()
        command_thread.join(timeout=1)
        self.assertFalse(command_thread.is_alive())
        self.assertEqual(command_results, [False])
        self.assertEqual(
            command_state["gen"]["status"], "successor running",
        )
        self.assertEqual(
            command_state["gen"]["queue"], [{"id": "successor"}],
        )

        worker_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_process_tasks_with_native_gpu"
        )
        worker_source = ast.get_source_segment(source, worker_node) or ""
        self.assertIn(
            "abort = _settle_native_generation_task_if_current(",
            worker_source,
        )
        self.assertIn(
            "_mutate_native_generation_if_current(", worker_source,
        )
        tail_at = worker_source.rindex("with gen_lock:")
        self.assertIn("_NATIVE_GENERATION_REQUEST_KEY", worker_source[tail_at:])

    def test_classic_stop_cancels_request_waiting_for_native_lane(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_NativeGpuExecutionCancelled",
            "_NativeGpuExecutionLease",
            "_begin_native_generation_request",
            "_checkpoint_native_generation_request",
            "_release_native_generation_request",
            "process_tasks",
        }
        nodes = [
            node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            and node.name in wanted
        ]
        native_gpu = threading.Lock()

        def acquire_native(cancel_checkpoint=None):
            while True:
                if callable(cancel_checkpoint):
                    cancel_checkpoint()
                if native_gpu.acquire(timeout=0.01):
                    return

        namespace = {
            "threading": threading,
            "native_gpu_execution_lock": native_gpu,
            "acquire_native_gpu_execution_lock": acquire_native,
            "_next_native_gpu_execution_owner_token": lambda: 17,
            "_NATIVE_GENERATION_OWNER_KEY": (
                "_maestro_native_generation_owner"
            ),
            "_NATIVE_GENERATION_REQUEST_KEY": (
                "_maestro_native_generation_request"
            ),
            "_NATIVE_GENERATION_ABORT_REQUEST_KEY": (
                "_maestro_native_generation_abort_request"
            ),
            "_NATIVE_GENERATION_EARLY_STOP_REQUEST_KEY": (
                "_maestro_native_generation_early_stop_request"
            ),
            "_NATIVE_GENERATION_FINALIZER_QUEUE_KEY": (
                "_maestro_native_generation_finalizer_queue"
            ),
            "_NATIVE_GENERATION_LAST_SETTLED_KEY": (
                "_maestro_native_generation_last_settled"
            ),
            "get_gen_info": lambda current_state: current_state["gen"],
            "gen_lock": threading.Lock(),
            "gen_in_progress": False,
            "_process_tasks_with_native_gpu": lambda *_args: iter(()),
            "_release_native_generation_owner": lambda *_args: False,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(wgp_path), "exec"), namespace)
        state = {"gen": {"queue": [{"id": "waiting"}]}}
        native_gpu.acquire()
        iterator = namespace["process_tasks"](state)
        outcome = []

        def wait_for_request():
            try:
                next(iterator)
            except StopIteration:
                outcome.append("cancelled")

        worker = threading.Thread(target=wait_for_request)
        worker.start()
        for _ in range(100):
            if state["gen"].get("in_progress"):
                break
            time.sleep(0.005)
        self.assertTrue(state["gen"].get("in_progress"))
        state["gen"]["abort"] = True
        worker.join(timeout=1)
        native_gpu.release()
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, ["cancelled"])
        self.assertNotIn("in_progress", state["gen"])
        self.assertNotIn(
            "_maestro_native_generation_request", state["gen"],
        )

    def test_classic_manual_enhance_waits_for_native_gpu_before_prompt_lane(self):
        from concurrent.futures import ThreadPoolExecutor

        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"enhance_prompt", "_process_prompt_enhancer_for_generation"}
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        prompt_lane = threading.RLock()
        native_execution = threading.Lock()
        process_state_lock = threading.Lock()
        manual_waiting_for_gpu = threading.Event()
        manual_entered_prompt = threading.Event()
        ordering = []
        state = {"gen": {"process_status": "process:main"}}

        def acquire_prompt(cancel_checkpoint=None):
            while True:
                if callable(cancel_checkpoint):
                    cancel_checkpoint()
                if prompt_lane.acquire(timeout=0.01):
                    return

        def acquire_gpu(current_state, process_id, _process_name):
            with process_state_lock:
                self.assertEqual(
                    current_state["gen"]["process_status"], None,
                )
                current_state["gen"]["process_status"] = (
                    f"process:{process_id}"
                )
            manual_waiting_for_gpu.set()

        def release_gpu(current_state, _process_id):
            with process_state_lock:
                current_state["gen"]["process_status"] = None

        def generation_process(*_args, **_kwargs):
            ordering.append("generation")
            return ["generation enhanced"]

        def manual_process(*_args, **_kwargs):
            ordering.append("manual")
            manual_entered_prompt.set()
            return ("manual enhanced", "manual enhanced")

        namespace = {
            "gr": types.SimpleNamespace(Progress=lambda: object()),
            "acquire_GPU_ressources": acquire_gpu,
            "_release_prompt_enhancer_gpu_resources": (
                lambda current_state: release_gpu(
                    current_state, "prompt_enhancer",
                )
            ),
            "native_gpu_execution_lock": native_execution,
            "acquire_native_gpu_execution_lock": native_execution.acquire,
            "acquire_prompt_enhancer_lock": acquire_prompt,
            "prompt_enhancer_lock": prompt_lane,
            "prompt_enhancer_classic_entry_lock": threading.Lock(),
            "_enhance_prompt_locked": manual_process,
            "process_prompt_enhancer": generation_process,
            "unload_prompt_enhancer_runtime": lambda: None,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), str(wgp_path), "exec"), namespace)

        native_execution.acquire()
        with ThreadPoolExecutor(max_workers=2) as executor:
            manual = executor.submit(
                namespace["enhance_prompt"],
                state, "prompt", "T", 0, 0, -1,
            )
            time.sleep(0.1)
            self.assertFalse(manual_waiting_for_gpu.is_set())
            self.assertFalse(manual_entered_prompt.is_set())
            self.assertTrue(prompt_lane.acquire(blocking=False))
            prompt_lane.release()

            generation = executor.submit(
                namespace["_process_prompt_enhancer_for_generation"],
                {}, "T", ["prompt"], None, None, False, False, 0,
            )
            self.assertEqual(
                generation.result(timeout=1), ["generation enhanced"],
            )
            with process_state_lock:
                state["gen"]["process_status"] = None
            native_execution.release()
            self.assertTrue(manual_waiting_for_gpu.wait(timeout=1))
            self.assertEqual(
                manual.result(timeout=1),
                ("manual enhanced", "manual enhanced"),
            )

        self.assertEqual(ordering, ["generation", "manual"])
        self.assertIsNone(state["gen"]["process_status"])
        wrapper = ast.get_source_segment(source, next(
            node for node in nodes if node.name == "enhance_prompt"
        )) or ""
        self.assertLess(
            wrapper.index("acquire_GPU_ressources("),
            wrapper.index("acquire_prompt_enhancer_lock("),
        )

    def test_two_classic_manual_enhancers_do_not_alias_native_process_owner(self):
        from concurrent.futures import ThreadPoolExecutor

        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wrapper_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "enhance_prompt"
        )
        native_lock = threading.Lock()
        prompt_lane = threading.RLock()
        native_execution = threading.Lock()
        first_inside = threading.Event()
        release_first = threading.Event()
        native = {
            "status": None,
            "active": 0,
            "maximum": 0,
            "acquires": 0,
        }
        manual_calls = 0

        def acquire_gpu(_state, process_id, _process_name):
            with native_lock:
                native["acquires"] += 1
                if native["status"] is None:
                    native["status"] = f"process:{process_id}"
                else:
                    # Mirror the native helper's process-ID reentrancy rule.
                    self.assertEqual(
                        native["status"], f"process:{process_id}",
                    )
                native["active"] += 1
                native["maximum"] = max(
                    native["maximum"], native["active"],
                )

        def release_gpu(_state, _process_id):
            with native_lock:
                native["active"] -= 1
                native["status"] = None

        def acquire_prompt(_cancel_checkpoint=None):
            prompt_lane.acquire()

        def run_manual(*_args, **_kwargs):
            nonlocal manual_calls
            manual_calls += 1
            if manual_calls == 1:
                first_inside.set()
                release_first.wait(timeout=2)
            return (f"manual-{manual_calls}", f"manual-{manual_calls}")

        namespace = {
            "gr": types.SimpleNamespace(Progress=lambda: object()),
            "acquire_GPU_ressources": acquire_gpu,
            "_release_prompt_enhancer_gpu_resources": (
                lambda current_state: release_gpu(
                    current_state, "prompt_enhancer",
                )
            ),
            "native_gpu_execution_lock": native_execution,
            "acquire_native_gpu_execution_lock": native_execution.acquire,
            "acquire_prompt_enhancer_lock": acquire_prompt,
            "prompt_enhancer_lock": prompt_lane,
            "prompt_enhancer_classic_entry_lock": threading.Lock(),
            "_enhance_prompt_locked": run_manual,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[wrapper_node], type_ignores=[],
        )), str(wgp_path), "exec"), namespace)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                namespace["enhance_prompt"],
                {}, "first", "T", 0, 0, -1,
            )
            self.assertTrue(first_inside.wait(timeout=1))
            second = executor.submit(
                namespace["enhance_prompt"],
                {}, "second", "T", 0, 0, -1,
            )
            time.sleep(0.1)
            with native_lock:
                self.assertEqual(native["acquires"], 1)
                self.assertEqual(native["maximum"], 1)
                self.assertEqual(native["status"], "process:prompt_enhancer")
            release_first.set()
            first.result(timeout=1)
            second.result(timeout=1)

        with native_lock:
            self.assertEqual(native["acquires"], 2)
            self.assertEqual(native["maximum"], 1)
            self.assertEqual(native["active"], 0)
            self.assertIsNone(native["status"])

    def test_successful_wangp_image_enhancement_closes_loaded_pil_image(self):
        closed = []
        observed_images = []

        class LoadedImage:
            def load(self):
                return None

            def close(self):
                closed.append(True)

        loaded_image = LoadedImage()

        def generate(*args, **_kwargs):
            observed_images.extend(args[5] or [])
            return ["enhanced with image"]

        enhance, _namespace, offload_state, modules = (
            self._load_wangp_sync_for_test(generate)
        )
        modules["PIL"].Image.open = lambda _path: loaded_image
        with tempfile.TemporaryDirectory() as root:
            image_path = os.path.join(root, "owned.png")
            Path(image_path).write_bytes(b"image-bytes")
            with mock.patch.dict(sys.modules, modules):
                result = enhance(
                    "prompt", "video", 1, [image_path], None,
                )
        self.assertEqual(result["enhanced"], "enhanced with image")
        self.assertEqual(observed_images, [loaded_image])
        self.assertEqual(closed, [True])
        self.assertEqual(offload_state.unloads, 1)

    def test_wangp_admitted_image_decode_failure_never_runs_inference(self):
        inference = mock.Mock(return_value=["must not run"])
        enhance, _namespace, offload_state, modules = (
            self._load_wangp_sync_for_test(inference)
        )

        class CorruptImage:
            def __init__(self):
                self.closed = False

            def load(self):
                raise OSError("private decoder detail")

            def close(self):
                self.closed = True

        corrupt = CorruptImage()
        modules["PIL"].Image.open = lambda _path: corrupt
        with tempfile.TemporaryDirectory() as root:
            image_path = os.path.join(root, "owned.png")
            Path(image_path).write_bytes(b"not-an-image")
            with mock.patch.dict(sys.modules, modules):
                with self.assertRaisesRegex(
                    RuntimeError, "could not be decoded",
                ):
                    enhance("prompt", "video", 1, [image_path], None)
        inference.assert_not_called()
        self.assertTrue(corrupt.closed)
        self.assertEqual(offload_state.unloads, 1)

    def test_classic_prompt_enhancer_closes_only_images_it_opened(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_process_prompt_enhancer_locked"
        )

        class TrackedImage:
            def __init__(self):
                self.closes = 0

            def close(self):
                self.closes += 1

        caller_image = TrackedImage()
        opened = []
        observed = []
        should_fail = {"value": False}

        def open_image(_path):
            image = TrackedImage()
            opened.append(image)
            return image

        def generate(*args, **_kwargs):
            observed.append(list(args[5] or []))
            if should_fail["value"]:
                raise RuntimeError("synthetic enhancer failure")
            return ["enhanced"]

        prompt_module = types.ModuleType(
            "shared.prompt_enhancer.prompt_enhance_utils"
        )
        prompt_module.generate_cinematic_prompt = generate
        namespace = {
            "re": __import__("re"),
            "Image": types.SimpleNamespace(open=open_image),
            "server_config": {
                "prompt_enhancer_temperature": 0.6,
                "prompt_enhancer_top_p": 0.9,
                "prompt_enhancer_randomize_seed": False,
            },
            "enhancer_offloadobj": None,
            "prompt_enhancer_image_caption_model": object(),
            "prompt_enhancer_image_caption_processor": object(),
            "prompt_enhancer_llm_model": object(),
            "prompt_enhancer_llm_tokenizer": object(),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[function_node], type_ignores=[],
        )), str(wgp_path), "exec"), namespace)

        with mock.patch.dict(sys.modules, {
            "shared.prompt_enhancer.prompt_enhance_utils": prompt_module,
        }):
            self.assertEqual(
                namespace["_process_prompt_enhancer_locked"](
                    {}, "TI", ["prompt"], ["owned-path.png"],
                    [caller_image], False, False, 1,
                ),
                ["enhanced"],
            )
            self.assertEqual(opened[-1].closes, 1)
            self.assertEqual(caller_image.closes, 0)
            self.assertEqual(observed[-1], [opened[-1], caller_image])

            should_fail["value"] = True
            with self.assertRaisesRegex(
                RuntimeError, "synthetic enhancer failure",
            ):
                namespace["_process_prompt_enhancer_locked"](
                    {}, "TI", ["prompt"], ["replacement.png"],
                    [caller_image], False, False, 1,
                )
            self.assertEqual(opened[-1].closes, 1)
            self.assertEqual(caller_image.closes, 0)

    def test_manual_enhance_closes_only_helper_created_conversions(self):
        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_enhance_prompt_locked"
        )

        class TrackedImage:
            def __init__(self, label):
                self.label = label
                self.closes = 0

            def close(self):
                self.closes += 1

        class GrError(RuntimeError):
            pass

        caller_start = TrackedImage("caller-start")
        caller_ref = TrackedImage("caller-ref")
        converted = []
        observed = []
        should_fail = {"value": False}

        def convert(source):
            image = TrackedImage("converted-" + source.label)
            converted.append(image)
            return image

        def process(*args, **_kwargs):
            observed.append((list(args[3] or []), list(args[4] or [])))
            if should_fail["value"]:
                raise RuntimeError("synthetic failure")
            return ["enhanced"]

        class Offload:
            @staticmethod
            def unload_all():
                return None

        namespace = {
            "gr": types.SimpleNamespace(
                Info=lambda *_args: None,
                update=lambda: None,
                Error=GrError,
            ),
            "prompt_parser": types.SimpleNamespace(
                process_template=lambda value, **_kwargs: (value, ""),
            ),
            "get_state_model_type": lambda _state: "model",
            "get_model_settings": lambda *_args: {
                "prompt": "prompt",
                "image_prompt_type": "S",
                "video_prompt_type": "I",
                "image_start": [(caller_start,)],
                "image_end": None,
                "image_refs": [(caller_ref,)],
                "image_mode": 0,
                "seed": 1,
            },
            "convert_image": convert,
            "reset_prompt_enhancer_if_requested": lambda: None,
            "enhancer_offloadobj": Offload(),
            "get_model_def": lambda _model: {},
            "set_seed": lambda seed: seed,
            "process_prompt_enhancer": process,
            "unload_prompt_enhancer_runtime": lambda: None,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[function_node], type_ignores=[],
        )), str(wgp_path), "exec"), namespace)
        progress = lambda *_args, **_kwargs: None

        result = namespace["_enhance_prompt_locked"](
            {}, "prompt", "TI", 0, 0, -1, progress,
        )
        self.assertEqual(result, ("#!PROMPT!: prompt\nenhanced",) * 2)
        self.assertEqual([image.closes for image in converted], [1, 1])
        self.assertEqual(caller_start.closes, 0)
        self.assertEqual(caller_ref.closes, 0)
        self.assertEqual(observed[-1], ([converted[0]], [converted[1]]))

        converted.clear()
        should_fail["value"] = True
        with self.assertRaises(GrError):
            namespace["_enhance_prompt_locked"](
                {}, "prompt", "TI", 0, 0, -1, progress,
            )
        self.assertEqual([image.closes for image in converted], [1, 1])
        self.assertEqual(caller_start.closes, 0)
        self.assertEqual(caller_ref.closes, 0)

    def test_convert_image_closes_owned_real_pil_sources_and_intermediates(self):
        from PIL import Image as PilImage
        from PIL import ImageOps

        wgp_path = Path(__file__).resolve().parents[1] / "app" / "wgp.py"
        source = wgp_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "convert_image"
        )
        namespace = {"Image": PilImage}
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[function_node], type_ignores=[],
        )), str(wgp_path), "exec"), namespace)
        convert = namespace["convert_image"]

        with tempfile.TemporaryDirectory() as root:
            source_path = os.path.join(root, "source.png")
            moved_path = os.path.join(root, "moved.png")
            PilImage.new("RGBA", (2, 2), (1, 2, 3, 255)).save(source_path)
            caller_image = PilImage.open(source_path)
            intermediates = []
            real_transpose = ImageOps.exif_transpose

            def capture_transpose(image):
                intermediates.append(image)
                return real_transpose(image)

            with mock.patch.object(
                ImageOps, "exif_transpose", side_effect=capture_transpose,
            ):
                result = convert(caller_image)
            self.assertEqual(caller_image.getpixel((0, 0)), (1, 2, 3, 255))
            self.assertEqual(result.getpixel((0, 0)), (1, 2, 3))
            with self.assertRaises(ValueError):
                intermediates[-1].getpixel((0, 0))
            result.close()
            caller_image.close()

            intermediates.clear()
            with mock.patch.object(
                ImageOps, "exif_transpose", side_effect=capture_transpose,
            ):
                result = convert(source_path)
            os.replace(source_path, moved_path)
            self.assertEqual(result.getpixel((0, 0)), (1, 2, 3))
            with self.assertRaises(ValueError):
                intermediates[-1].getpixel((0, 0))
            result.close()

            caller_image = PilImage.open(moved_path)
            failed_intermediate = []

            def fail_transpose(image):
                failed_intermediate.append(image)
                raise RuntimeError("synthetic transpose failure")

            with mock.patch.object(
                ImageOps, "exif_transpose", side_effect=fail_transpose,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "synthetic transpose failure",
                ):
                    convert(caller_image)
            self.assertEqual(caller_image.getpixel((0, 0)), (1, 2, 3, 255))
            with self.assertRaises(ValueError):
                failed_intermediate[-1].getpixel((0, 0))
            caller_image.close()

    def test_wangp_cancel_after_blocked_inference_unloads_and_releases_lane(self):
        from concurrent.futures import ThreadPoolExecutor
        from services.llm_cancellation import (
            LlmCancellationHandle,
            LlmRequestCancelled,
        )

        entered = threading.Event()
        release = threading.Event()

        def generate(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return ["late result"]

        enhance, namespace, offload_state, modules = (
            self._load_wangp_sync_for_test(generate)
        )
        cancellation = LlmCancellationHandle()
        with mock.patch.dict(sys.modules, modules), ThreadPoolExecutor(
            max_workers=1,
        ) as executor:
            future = executor.submit(
                enhance, "prompt", "video", 1, None, cancellation,
            )
            self.assertTrue(entered.wait(timeout=1))
            cancellation.cancel()
            release.set()
            with self.assertRaises(LlmRequestCancelled):
                future.result(timeout=2)
        self.assertEqual(offload_state.unloads, 1)
        self.assertTrue(namespace["_gen_lock"].acquire(blocking=False))
        namespace["_gen_lock"].release()

    def test_second_wangp_request_cancels_while_waiting_without_inference(self):
        from concurrent.futures import ThreadPoolExecutor
        from services.llm_cancellation import (
            LlmCancellationHandle,
            LlmRequestCancelled,
        )

        first_entered = threading.Event()
        release_first = threading.Event()
        calls = []

        def generate(*_args, **_kwargs):
            calls.append(True)
            first_entered.set()
            release_first.wait(timeout=2)
            return ["first result"]

        enhance, _namespace, offload_state, modules = (
            self._load_wangp_sync_for_test(generate)
        )
        second_cancel = LlmCancellationHandle()
        with mock.patch.dict(sys.modules, modules), ThreadPoolExecutor(
            max_workers=2,
        ) as executor:
            first = executor.submit(enhance, "first", "video", 1)
            self.assertTrue(first_entered.wait(timeout=1))
            second = executor.submit(
                enhance, "second", "video", 1, None, second_cancel,
            )
            self.assertFalse(second.done())
            second_cancel.cancel()
            with self.assertRaises(LlmRequestCancelled):
                second.result(timeout=1)
            self.assertEqual(len(calls), 1)
            release_first.set()
            self.assertEqual(first.result(timeout=2)["enhanced"], "first result")
        self.assertEqual(offload_state.unloads, 1)

    def test_scoped_enhance_passes_exact_cancel_handle_and_progress_callback(self):
        from fastapi import HTTPException
        from services.llm_cancellation import LlmCancellationHandle

        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(launch_path))
        route = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "llm_enhance_prompt"
        )
        route.decorator_list = []
        observed = {}
        progress_events = []
        cancel_handle = LlmCancellationHandle()

        def enhance_prompt(**kwargs):
            observed.update(kwargs)
            kwargs["cancel_handle"].checkpoint()
            kwargs["progress_callback"]({
                "phase": "generating", "text": "scoped partial",
            })
            return "enhanced result"

        fake_service = types.SimpleNamespace(
            MODEL_REGISTRY={},
            get_model_capabilities=lambda *_args, **_kwargs: {
                "vision_capable": False,
            },
            enhance_prompt=enhance_prompt,
        )
        fake_wgp = types.SimpleNamespace(server_config={
            "enhancer_enabled": 0,
            "services": {},
        })
        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "JSONResponse": object,
            "copy": __import__("copy"),
            "hmac": __import__("hmac"),
            "wgp": fake_wgp,
            "_promote_external_llm_request": lambda _request: None,
            "_request_project_workspace": lambda _request, value: value,
            "_require_project_access": lambda *_args, **_kwargs: None,
            "_resolve_prompt_enhancement_images": lambda *_args: [],
            "_explicit_llm_guidance_allowed": lambda _body: False,
            "_llm_route_progress_callback": (
                lambda request: request.state.maestro_llm_progress_callback
            ),
            "_emit_llm_progress": (
                lambda callback, event: callback(event) if callback else None
            ),
            "_resolve_prompt_enhancer_selection": (
                lambda *_args, **_kwargs: ("local-enhancer", "cpu", False)
            ),
            "_resolve_prompt_enhancer_runtime_selection": (
                lambda *_args, **_kwargs: ({
                    "model_id": "local-enhancer",
                    "device": "cpu",
                    "provider": "local",
                    "remote_url": "",
                    "api_key": "",
                    "local_gguf_path": "",
                    "gguf_file_override": "",
                }, False)
            ),
            "_run_authorized_llm_with_selection": (
                lambda _request, _selection, operation: operation()
            ),
            "_resolved_local_response_assist": lambda *_args, **_kwargs: None,
            "_validate_standalone_enhanced_prompt_cardinality": (
                lambda _body, _model_type, result: result
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[route], type_ignores=[],
        )), str(launch_path), "exec"), namespace)

        class Request:
            state = types.SimpleNamespace(
                maestro_session_id="owner",
                maestro_remote=False,
                maestro_llm_cancel_handle=cancel_handle,
                maestro_llm_progress_callback=progress_events.append,
            )

            async def json(self):
                return {"workspace": "project", "prompt": "private prompt"}

        with mock.patch.object(
            sys.modules["services"], "llm_service", fake_service,
        ):
            result = asyncio.run(namespace["llm_enhance_prompt"](Request()))

        self.assertEqual(
            result, {"original": "private prompt", "enhanced": "enhanced result"},
        )
        self.assertIs(observed["cancel_handle"], cancel_handle)
        self.assertIs(
            observed["progress_callback"],
            Request.state.maestro_llm_progress_callback,
        )
        self.assertIn("scoped partial", repr(progress_events))

    def test_gguf_download_is_visible_through_llm_status(self):
        observed = {}

        def fake_download(**kwargs):
            observed.update(llm_service.get_status())
            return os.path.join(kwargs["local_dir"], kwargs["filename"])

        fake_hf = types.SimpleNamespace(hf_hub_download=fake_download)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            sys.modules, {"huggingface_hub": fake_hf}
        ):
            result = llm_service._download_gguf(
                "example/model", "nested/enhancer.gguf", tmp,
            )

        self.assertTrue(observed["loading"])
        self.assertEqual(observed["loading_phase"], "downloading")
        self.assertEqual(observed["download"]["model_id"], "example/model")
        self.assertEqual(observed["download"]["filename"], "enhancer.gguf")
        self.assertTrue(result.endswith("nested/enhancer.gguf"))
        self.assertFalse(llm_service.get_status()["loading"])

    def test_prompt_enhancer_opens_queue_before_enqueueing_each_request(self):
        prompt_input = (
            Path(__file__).resolve().parents[1]
            / "ui" / "src" / "components" / "Sidebar" / "PromptInput.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "import { requestQueueView } from '../../lib/mainViewNavigation'",
            prompt_input,
        )
        tts_enhancement = prompt_input[
            prompt_input.index("const runTtsEnhancement ="):
            prompt_input.index("const runEnhancement =")
        ]
        enhancement = prompt_input[
            prompt_input.index("const runEnhancement ="):
            prompt_input.index("// grow shrink-0")
        ]
        self.assertLess(
            tts_enhancement.index("requestQueueView()"),
            tts_enhancement.index("void enhancePrompt(mode)"),
        )
        self.assertLess(
            enhancement.index("requestQueueView()"),
            enhancement.index("void enhancePrompt()"),
        )
        self.assertIn("onClick={runEnhancement}", prompt_input)
        self.assertIn("onClick={() => runTtsEnhancement(defaultMode)}", prompt_input)

    def test_runtime_build_status_ignores_unrelated_safe_download(self):
        from services import safe_download

        with llm_service._download_state_lock:
            llm_service._download_state.update({
                "model_id": "example/model",
                "filename": "llama-server b10289 CUDA",
                "phase": "building_runtime",
                "downloaded_bytes": 0,
                "total_bytes": None,
            })
        try:
            with mock.patch.object(safe_download, "get_active_downloads", return_value=[{
                "filename": "unrelated-video-model.safetensors",
                "downloaded_bytes": 999,
                "total_bytes": 1000,
                "seconds_since_progress": 1,
            }]):
                status = llm_service.get_status()
        finally:
            with llm_service._download_state_lock:
                llm_service._download_state.clear()

        self.assertEqual(status["loading_phase"], "building_runtime")
        self.assertEqual(status["download"]["downloaded_bytes"], 0)
        self.assertIsNone(status["download"]["total_bytes"])

    def test_model_loading_remains_attributed_after_download_finishes(self):
        previous = llm_service._loading_model_id
        self.addCleanup(
            lambda: setattr(llm_service, "_loading_model_id", previous)
        )
        with llm_service._download_state_lock:
            previous_download = dict(llm_service._download_state)
            llm_service._download_state.clear()

        def restore_download():
            with llm_service._download_state_lock:
                llm_service._download_state.clear()
                llm_service._download_state.update(previous_download)

        self.addCleanup(restore_download)
        llm_service._loading_model_id = "example/catalog-model"

        status = llm_service.get_status()

        self.assertTrue(status["loading"])
        self.assertEqual(status["loading_model_id"], "example/catalog-model")
        self.assertEqual(status["loading_phase"], "loading model")
        self.assertIsNone(status["download"])

    def test_ready_runtime_suppresses_stale_loading_marker(self):
        previous = llm_service._loading_model_id
        self.addCleanup(
            lambda: setattr(llm_service, "_loading_model_id", previous)
        )
        llm_service._loading_model_id = "example/catalog-model"

        with mock.patch.object(llm_service, "is_loaded", return_value=True), mock.patch.object(
            llm_service, "get_local_runtime_control", return_value={"phase": "ready"},
        ):
            status = llm_service.get_status()

        self.assertTrue(status["loaded"])
        self.assertFalse(status["loading"])
        self.assertIsNone(status["loading_model_id"])
        self.assertIsNone(status["loading_phase"])

    def test_global_timeline_enhancement_locks_timestamps_not_window_paragraphs(self):
        built = llm_service._build_enhance_user_prompt(
            "[00:00-00:15] opening\nAt 00:15.000, cut",
            "video",
            30,
            2,
            15,
            True,
        )
        self.assertIn("keep every timestamp token exactly unchanged", built)
        self.assertNotIn("Write EXACTLY 2 paragraphs", built)

    def test_gemma_31b_heavy_default_is_exact_text_only_q4(self):
        entry = llm_service.MODEL_REGISTRY[
            "MoonRide/gemma-4-31B-it-heretic-ara-GGUF"
        ]
        self.assertEqual(
            entry["gguf_file"], "gemma-4-31B-it-heretic-ara-Q4_K_M.gguf"
        )
        self.assertIsNone(entry["mmproj_file"])
        self.assertEqual(
            llm_service.DEFAULT_HF_REPO,
            "MoonRide/gemma-4-31B-it-heretic-ara-GGUF",
        )

    def test_31b_vision_refinement_model_is_public_optional_and_not_default(self):
        model_id = "paperscarecrow/Gemma-4-31B-it-abliterated-gguf"
        entry = llm_service.MODEL_REGISTRY[model_id]
        self.assertEqual(
            entry["gguf_file"], "gemma-4-31b-abliterated-Q4_K_M.gguf",
        )
        self.assertEqual(
            entry["mmproj_file"], "mmproj-gemma-4-31B-it-BF16.gguf",
        )
        self.assertEqual(
            entry["mmproj_repo"], "ggml-org/gemma-4-31B-it-GGUF",
        )
        self.assertIn(model_id, llm_service._PUBLIC_MODEL_ORDER)
        catalog = llm_service.get_available_models(provider="local")
        published = next(item for item in catalog if item["id"] == model_id)
        self.assertTrue(published["vision_capable"])
        self.assertNotIn(model_id, llm_service.RETIRED_MODEL_IDS)
        self.assertEqual(llm_service._migrate_retired_model_id(model_id), model_id)
        self.assertEqual(
            llm_service.DEFAULT_HF_REPO,
            "MoonRide/gemma-4-31B-it-heretic-ara-GGUF",
        )

    def test_registered_text_model_skips_projector_but_legacy_custom_repo_probes(self):
        downloads = []

        class FakeResponse:
            status_code = 200
            text = '{"status":"ok"}'

            @staticmethod
            def json():
                return {"status": "ok"}

        class FakeProcess:
            returncode = None
            stdout = io.BytesIO()

            @staticmethod
            def poll():
                return None

        def fake_download(repo_id, filename, cache_dir):
            downloads.append((repo_id, filename))
            path = Path(cache_dir) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"model")
            return str(path)

        def reset_loaded_state():
            llm_service._process = None
            llm_service._model_id = ""
            llm_service._vision_available = False

        registered_without_key = "unsloth/Qwen3.5-2B-GGUF"
        heavy_text = "MoonRide/gemma-4-31B-it-heretic-ara-GGUF"
        private_vision = "paperscarecrow/Gemma-4-31B-it-abliterated-gguf"
        custom_repo = "example/legacy-custom-GGUF"
        self.addCleanup(reset_loaded_state)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            llm_service, "get_model_dir", return_value=tmp
        ), mock.patch.object(
            llm_service, "_download_gguf", side_effect=fake_download
        ), mock.patch.object(
            llm_service, "_get_server_exe", return_value="llama-server"
        ), mock.patch.object(
            llm_service, "_find_free_port", return_value=54321
        ), mock.patch.object(
            llm_service.subprocess, "Popen", return_value=FakeProcess()
        ), mock.patch.object(
            llm_service.requests, "get", return_value=FakeResponse()
        ), mock.patch.object(llm_service, "_start_log_reader"):
            reset_loaded_state()
            llm_service.load_model(registered_without_key)
            self.assertEqual(
                downloads,
                [(
                    registered_without_key,
                    llm_service.MODEL_REGISTRY[registered_without_key]["gguf_file"],
                )],
            )

            downloads.clear()
            reset_loaded_state()
            llm_service.load_model(heavy_text)
            self.assertEqual(
                downloads,
                [(
                    heavy_text,
                    llm_service.MODEL_REGISTRY[heavy_text]["gguf_file"],
                )],
            )

            downloads.clear()
            reset_loaded_state()
            llm_service.load_model(private_vision)
            self.assertEqual(downloads, [
                (
                    private_vision,
                    llm_service.MODEL_REGISTRY[private_vision]["gguf_file"],
                ),
                (
                    "ggml-org/gemma-4-31B-it-GGUF",
                    llm_service.MODEL_REGISTRY[private_vision]["mmproj_file"],
                ),
            ])
            self.assertTrue(llm_service._vision_available)

            downloads.clear()
            reset_loaded_state()
            llm_service.load_model(custom_repo)
            self.assertEqual(downloads[-1], (custom_repo, llm_service.DEFAULT_MMPROJ_FILE))

        reset_loaded_state()

    def test_concurrent_same_key_cold_load_is_single_flight(self):
        processes = []
        errors = []
        first_health_entered = threading.Event()
        release_first_health = threading.Event()
        second_attempting = threading.Event()

        def popen(*_args, **_kwargs):
            process = _FakeLlamaProcess()
            processes.append(process)
            return process

        def health(*_args, **_kwargs):
            if not first_health_entered.is_set():
                first_health_entered.set()
                release_first_health.wait(timeout=2)
            return _HealthyResponse()

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            with mock.patch.object(
                llm_service, "_get_server_exe", return_value="llama-server",
            ), mock.patch.object(
                llm_service, "_find_free_port", return_value=54321,
            ), mock.patch.object(
                llm_service.subprocess, "Popen", side_effect=popen,
            ), mock.patch.object(
                llm_service.requests, "get", side_effect=health,
            ), mock.patch.object(
                llm_service.threading, "Timer", _RecordingTimer,
            ):
                def load(attempting=None):
                    try:
                        if attempting is not None:
                            attempting.set()
                        llm_service.load_model(
                            "linked:model", local_gguf_path=str(model),
                        )
                    except Exception as error:  # pragma: no cover - asserted
                        errors.append(error)

                workers = [
                    threading.Thread(target=load),
                    threading.Thread(target=load, args=(second_attempting,)),
                ]
                workers[0].start()
                self.assertTrue(first_health_entered.wait(timeout=1))
                workers[1].start()
                self.assertTrue(second_attempting.wait(timeout=1))
                time.sleep(0.03)
                self.assertTrue(workers[1].is_alive())
                self.assertEqual(len(processes), 1)
                release_first_health.set()
                for worker in workers:
                    worker.join(timeout=2)

                self.assertFalse(any(worker.is_alive() for worker in workers))
                self.assertEqual(errors, [])
                self.assertEqual(len(processes), 1)
                self.assertTrue(llm_service.is_loaded())
                self.assertIsNotNone(llm_service._idle_timer)
                llm_service.unload_model()

    def test_failed_cold_load_cleans_state_and_retries(self):
        class DelayedStream(io.BytesIO):
            def readline(self, size=-1):
                # Delay every read, including the final EOF observation. This
                # forces the exit path to use the explicit completion event
                # rather than a fixed join that happens to cover one chunk.
                time.sleep(0.35)
                return super().readline(size)

        failed = _FakeLlamaProcess(
            returncode=7,
            stdout=DelayedStream(
                b"private prompt-shaped first chunk\n"
                b"private second chunk; CUDA fatal error\n"
            ),
        )
        healthy = _FakeLlamaProcess()
        processes = iter((failed, OSError("synthetic spawn failure"), healthy))

        def popen(*_args, **_kwargs):
            result = next(processes)
            if isinstance(result, Exception):
                raise result
            return result

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            with mock.patch.object(
                llm_service, "_get_server_exe", return_value="llama-server",
            ), mock.patch.object(
                llm_service, "_find_free_port", return_value=54321,
            ), mock.patch.object(
                llm_service.subprocess, "Popen", side_effect=popen,
            ) as popen, mock.patch.object(
                llm_service.requests, "get", return_value=_HealthyResponse(),
            ), mock.patch.object(
                llm_service.threading, "Timer", _RecordingTimer,
            ):
                with self.assertRaisesRegex(RuntimeError, "exited with code 7") as caught:
                    llm_service.load_model(
                        "linked:model", local_gguf_path=str(model),
                    )
                diagnostic = str(caught.exception)
                self.assertNotIn("private prompt-shaped", diagnostic)
                self.assertNotIn("private second chunk", diagnostic)
                self.assertIn("chunk 1:", diagnostic)
                self.assertIn("chunk 2:", diagnostic)
                self.assertIn("signals=cuda,failure", diagnostic)
                self.assertFalse(llm_service.is_loaded())
                self.assertEqual(llm_service._loaded_model_key, ())
                self.assertIsNone(llm_service._idle_timer)

                with self.assertRaisesRegex(OSError, "spawn failure"):
                    llm_service.load_model(
                        "linked:model", local_gguf_path=str(model),
                    )
                self.assertFalse(llm_service.is_loaded())
                self.assertEqual(llm_service._loaded_model_key, ())
                self.assertEqual(llm_service._loading_model_id, "")

                llm_service.load_model(
                    "linked:model", local_gguf_path=str(model),
                )

                self.assertEqual(popen.call_count, 3)
                self.assertTrue(llm_service.is_loaded())
                llm_service.unload_model()

    def test_download_failure_clears_loading_state_and_allows_retry(self):
        calls = 0

        def download(_repo, filename, cache_dir):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("synthetic download failure")
            path = Path(cache_dir) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"model")
            return str(path)

        repo = "unsloth/Qwen3.5-2B-GGUF"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            llm_service, "get_model_dir", return_value=tmp,
        ), mock.patch.object(
            llm_service, "_download_gguf", side_effect=download,
        ), mock.patch.object(
            llm_service, "_get_server_exe", return_value="llama-server",
        ), mock.patch.object(
            llm_service, "_find_free_port", return_value=54321,
        ), mock.patch.object(
            llm_service.subprocess, "Popen", return_value=_FakeLlamaProcess(),
        ), mock.patch.object(
            llm_service.requests, "get", return_value=_HealthyResponse(),
        ), mock.patch.object(
            llm_service.threading, "Timer", _RecordingTimer,
        ):
            with self.assertRaisesRegex(OSError, "download failure"):
                llm_service.load_model(repo)
            status = llm_service.get_status()
            self.assertFalse(status["loaded"])
            self.assertFalse(status["loading"])
            self.assertEqual(llm_service._loaded_model_key, ())
            self.assertIsNone(llm_service._idle_timer)

            llm_service.load_model(repo)
            self.assertTrue(llm_service.is_loaded())
            llm_service.unload_model()

    def test_cold_load_starts_one_content_free_drain_before_health(self):
        drain_started = threading.Event()

        class SignallingStream(io.BytesIO):
            def readline(self, size=-1):
                drain_started.set()
                return super().readline(size)

        process = _FakeLlamaProcess(
            stdout=SignallingStream(b"private model output\nCUDA fatal error\n"),
        )
        reader_generation = llm_service._log_reader_generation

        def health(*_args, **_kwargs):
            self.assertTrue(drain_started.wait(timeout=1))
            return _HealthyResponse()

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"model")
            with mock.patch.object(
                llm_service, "_get_server_exe", return_value="llama-server",
            ), mock.patch.object(
                llm_service, "_find_free_port", return_value=54321,
            ), mock.patch.object(
                llm_service.subprocess, "Popen", return_value=process,
            ), mock.patch.object(
                llm_service.requests, "get", side_effect=health,
            ), mock.patch.object(
                llm_service.threading, "Timer", _RecordingTimer,
            ):
                llm_service.load_model(
                    "linked:model", local_gguf_path=str(model),
                )
                llm_service._log_reader.join(timeout=1)
                tail = llm_service._server_log_tail()

                self.assertEqual(
                    llm_service._log_reader_generation, reader_generation + 1,
                )
                self.assertIn("bytes", tail)
                self.assertIn("signals=cuda,failure", tail)
                self.assertNotIn("private model output", tail)
                self.assertNotIn("CUDA fatal error", tail)
                llm_service.unload_model()

    def test_terminal_generation_failure_rearms_only_the_same_identity(self):
        _RecordingTimer.instances.clear()
        with mock.patch.object(
            llm_service.threading, "Timer", _RecordingTimer,
        ):
            llm_service.load_model(
                "remote-a", provider="remote",
                remote_url="http://remote-a.invalid",
            )
            identity_a = llm_service._loaded_model_key
            with mock.patch.object(
                llm_service.requests, "post",
                side_effect=llm_service.requests.ConnectionError("synthetic"),
            ):
                with self.assertRaises(RuntimeError):
                    llm_service.generate("prompt")
            failed_request_timer = llm_service._idle_timer
            self.assertTrue(failed_request_timer.started)

            llm_service.load_model(
                "remote-b", provider="remote",
                remote_url="http://remote-b.invalid",
            )
            model_b_timer = llm_service._idle_timer

            self.assertFalse(llm_service._finish_model_activity(identity_a))
            self.assertIs(llm_service._idle_timer, model_b_timer)
            llm_service.unload_model()

    def test_nested_loaded_model_lease_arms_one_timer_per_terminal_outcome(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "choices": [{
                        "message": {"content": "answer"},
                        "finish_reason": "stop",
                    }],
                }

        for should_fail in (False, True):
            with self.subTest(should_fail=should_fail), mock.patch.object(
                llm_service.threading, "Timer", _RecordingTimer,
            ):
                llm_service.unload_model()
                _RecordingTimer.instances.clear()
                post_result = (
                    llm_service.requests.ConnectionError("synthetic")
                    if should_fail else Response()
                )
                with mock.patch.object(
                    llm_service.requests, "post",
                    side_effect=post_result if should_fail else None,
                    return_value=None if should_fail else post_result,
                ):
                    if should_fail:
                        with self.assertRaises(RuntimeError):
                            with llm_service.loaded_model_lease(
                                model_id="remote-a", provider="remote",
                                remote_url="http://remote-a.invalid",
                            ):
                                llm_service.generate("prompt")
                    else:
                        with llm_service.loaded_model_lease(
                            model_id="remote-a", provider="remote",
                            remote_url="http://remote-a.invalid",
                        ):
                            self.assertEqual(
                                llm_service.generate("prompt"), "answer",
                            )

                self.assertEqual(len(_RecordingTimer.instances), 1)
                self.assertTrue(_RecordingTimer.instances[0].started)
                self.assertIs(
                    llm_service._idle_timer, _RecordingTimer.instances[0],
                )
                llm_service.unload_model()

        with mock.patch.object(
            llm_service.threading, "Timer", _RecordingTimer,
        ):
            _RecordingTimer.instances.clear()
            llm_service.load_model(
                "remote-load-only", provider="remote",
                remote_url="http://remote-load-only.invalid",
            )
            self.assertEqual(len(_RecordingTimer.instances), 1)
            llm_service.unload_model()

    def test_generate_lease_blocks_unload_until_failure_finalizes(self):
        request_started = threading.Event()
        release_request = threading.Event()
        unload_done = threading.Event()
        errors = []

        def post(*_args, **_kwargs):
            request_started.set()
            release_request.wait(timeout=2)
            raise llm_service.requests.ConnectionError("synthetic")

        with mock.patch.object(
            llm_service.threading, "Timer", _RecordingTimer,
        ):
            llm_service.load_model(
                "remote-a", provider="remote",
                remote_url="http://remote-a.invalid",
            )

            def generate():
                try:
                    llm_service.generate("prompt")
                except RuntimeError as error:
                    errors.append(error)

            def unload():
                llm_service.unload_model()
                unload_done.set()

            with mock.patch.object(llm_service.requests, "post", side_effect=post):
                generation = threading.Thread(target=generate)
                generation.start()
                self.assertTrue(request_started.wait(timeout=1))
                unloading = threading.Thread(target=unload)
                unloading.start()
                self.assertFalse(unload_done.wait(timeout=0.05))
                release_request.set()
                generation.join(timeout=2)
                unloading.join(timeout=2)

            self.assertEqual(len(errors), 1)
            self.assertTrue(unload_done.is_set())
            self.assertFalse(llm_service.is_loaded())
            self.assertIsNone(llm_service._idle_timer)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux runtime test")
    def test_linux_tar_extraction_preserves_only_safe_relative_symlinks(self):
        archive_bytes = _tar_bytes([
            ("build/bin/llama-server", "server", None),
            ("build/bin/libllama.so.0.0.100", "library", None),
            ("build/bin/libllama.so.0", None, "libllama.so.0.0.100"),
            ("build/bin/unsafe-relative", None, "../../../outside"),
            ("build/bin/unsafe-absolute", None, "/tmp/outside"),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with tarfile.open(
                fileobj=io.BytesIO(archive_bytes), mode="r:gz"
            ) as archive:
                llm_service._extract_linux_tar(archive, tmp)

            self.assertEqual((tmp_path / "llama-server").read_text(), "server")
            self.assertTrue((tmp_path / "libllama.so.0").is_symlink())
            self.assertEqual(
                os.readlink(tmp_path / "libllama.so.0"),
                "libllama.so.0.0.100",
            )
            self.assertFalse(os.path.lexists(tmp_path / "unsafe-relative"))
            self.assertFalse(os.path.lexists(tmp_path / "unsafe-absolute"))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux runtime test")
    def test_soname_repair_uses_newest_versioned_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "libllama-common.so.0.0.9999").write_bytes(b"old")
            (tmp_path / "libllama-common.so.0.0.10000").write_bytes(b"new")
            (tmp_path / "libggml-base.so.0.18.1").write_bytes(b"ggml")

            repaired = llm_service._repair_linux_soname_links(tmp)

            self.assertEqual(
                set(repaired), {"libllama-common.so.0", "libggml-base.so.0"}
            )
            self.assertEqual(
                os.readlink(tmp_path / "libllama-common.so.0"),
                "libllama-common.so.0.0.10000",
            )
            self.assertEqual(
                os.readlink(tmp_path / "libggml-base.so.0"),
                "libggml-base.so.0.18.1",
            )
            self.assertEqual(llm_service._repair_linux_soname_links(tmp), [])

    def test_version_probe_distinguishes_unparseable_runtime_from_exit_127(self):
        results = iter([
            subprocess.CompletedProcess(
                [], 0, stdout="custom llama runtime", stderr=""
            ),
            subprocess.CompletedProcess(
                [], 127, stdout="", stderr="error while loading shared libraries"
            ),
            subprocess.CompletedProcess([], 0, stdout="version: 10289", stderr=""),
        ])
        with mock.patch.object(
            subprocess, "run", side_effect=lambda *args, **kwargs: next(results)
        ):
            self.assertEqual(
                llm_service._llama_server_probe("llama-server"), (None, True)
            )
            self.assertEqual(
                llm_service._llama_server_probe("llama-server"), (None, False)
            )
            self.assertEqual(
                llm_service._llama_server_probe("llama-server"), (10289, True)
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux runtime test")
    def test_existing_linux_install_self_heals_before_version_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "llama-server").write_bytes(b"server")
            (tmp_path / "libllama-common.so.0.0.10289").write_bytes(b"library")

            def probe_after_repair(exe_path):
                self.assertTrue((tmp_path / "libllama-common.so.0").is_symlink())
                return None, True

            with mock.patch.object(sys, "platform", "linux"), mock.patch.object(
                llm_service, "_llama_server_probe", side_effect=probe_after_repair
            ), mock.patch.object(
                urllib.request,
                "urlopen",
                side_effect=AssertionError(
                    "a repaired runnable install must not download"
                ),
            ):
                llm_service._ensure_llama_server(tmp)

            self.assertEqual(
                os.readlink(tmp_path / "libllama-common.so.0"),
                "libllama-common.so.0.0.10289",
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux runtime test")
    def test_exit_127_install_is_replaced_from_mocked_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "llama-server").write_bytes(b"broken")
            archive_bytes = _tar_bytes([
                ("build/bin/llama-server", "repaired", None),
                ("build/bin/libllama.so.0.0.10300", "library", None),
                ("build/bin/libllama.so.0", None, "libllama.so.0.0.10300"),
            ])
            asset_url = (
                "https://example.invalid/llama-b10300-bin-ubuntu-x64.tar.gz"
            )
            release = json.dumps({
                "tag_name": "b10300",
                "assets": [{
                    "name": "llama-b10300-bin-ubuntu-x64.tar.gz",
                    "browser_download_url": asset_url,
                }],
            }).encode()
            requested = []

            class _Response(io.BytesIO):
                headers = {}

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    self.close()

            def fake_urlopen(request, timeout):
                url = request.full_url if hasattr(request, "full_url") else request
                requested.append(url)
                return _Response(release if "api.github.com" in url else archive_bytes)

            def runtime_probe(path, **_kwargs):
                try:
                    payload = Path(path).read_bytes()
                except OSError:
                    payload = b""
                if payload == b"broken":
                    return None, False
                return 10300, True

            with mock.patch.object(sys, "platform", "linux"), mock.patch.object(
                llm_service,
                "_llama_server_probe",
                side_effect=runtime_probe,
            ), mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
                llm_service._ensure_llama_server(tmp)

            self.assertEqual(requested[-1], asset_url)
            self.assertEqual((tmp_path / "llama-server").read_bytes(), b"repaired")
            self.assertEqual(
                os.readlink(tmp_path / "libllama.so.0"),
                "libllama.so.0.0.10300",
            )

    def test_windows_two_archive_runtime_is_staged_and_swapped_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "llama-server.exe").write_bytes(b"old")
            server_url = "https://example.invalid/llama-win.zip"
            cudart_url = "https://example.invalid/cudart-win.zip"
            release = json.dumps({
                "tag_name": "b10300",
                "assets": [
                    {
                        "name": "llama-b10300-bin-win-cuda-12.4-x64.zip",
                        "browser_download_url": server_url,
                    },
                    {
                        "name": "cudart-llama-bin-win-cuda-12.4-x64.zip",
                        "browser_download_url": cudart_url,
                    },
                ],
            }).encode()
            archives = {
                server_url: _zip_bytes([
                    ("build/bin/llama-server.exe", b"new"),
                    ("build/bin/ggml.dll", b"ggml"),
                ]),
                cudart_url: _zip_bytes([
                    ("cudart64_12.dll", b"cuda"),
                ]),
            }

            class _Response(io.BytesIO):
                headers = {}

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    self.close()

            def fake_urlopen(request, timeout):
                url = request.full_url if hasattr(request, "full_url") else request
                return _Response(
                    release if "api.github.com" in url else archives[url]
                )

            def capabilities(path, **_kwargs):
                payload = Path(path).read_bytes() if Path(path).is_file() else b""
                return {
                    "build": 10300 if payload == b"new" else None,
                    "runnable": payload == b"new",
                    "backend": "cuda" if payload == b"new" else "cpu",
                    "devices": ["CUDA0: Test"] if payload == b"new" else [],
                }

            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
                llm_service, "_llama_server_probe", return_value=(None, False),
            ), mock.patch.object(
                llm_service, "_llama_server_capabilities", side_effect=capabilities,
            ), mock.patch.object(
                urllib.request, "urlopen", side_effect=fake_urlopen,
            ):
                result = llm_service._ensure_llama_server(tmp, "cuda")

            self.assertEqual(result["backend"], "cuda")
            self.assertEqual((tmp_path / "llama-server.exe").read_bytes(), b"new")
            self.assertEqual((tmp_path / "ggml.dll").read_bytes(), b"ggml")
            self.assertEqual((tmp_path / "cudart64_12.dll").read_bytes(), b"cuda")

    def test_windows_second_archive_failure_preserves_existing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_server = tmp_path / "llama-server.exe"
            old_server.write_bytes(b"old")
            server_url = "https://example.invalid/llama-win.zip"
            cudart_url = "https://example.invalid/cudart-win.zip"
            release = json.dumps({
                "tag_name": "b10300",
                "assets": [
                    {
                        "name": "llama-b10300-bin-win-cuda-12.4-x64.zip",
                        "browser_download_url": server_url,
                    },
                    {
                        "name": "cudart-llama-bin-win-cuda-12.4-x64.zip",
                        "browser_download_url": cudart_url,
                    },
                ],
            }).encode()
            server_archive = _zip_bytes([
                ("build/bin/llama-server.exe", b"new"),
            ])

            class _Response(io.BytesIO):
                headers = {}

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    self.close()

            def fake_urlopen(request, timeout):
                url = request.full_url if hasattr(request, "full_url") else request
                if "api.github.com" in url:
                    return _Response(release)
                if url == server_url:
                    return _Response(server_archive)
                raise OSError("synthetic second archive failure")

            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
                llm_service, "_llama_server_probe", return_value=(None, False),
            ), mock.patch.object(
                urllib.request, "urlopen", side_effect=fake_urlopen,
            ):
                with self.assertRaisesRegex(OSError, "second archive failure"):
                    llm_service._ensure_llama_server(tmp, "cuda")

            self.assertEqual(old_server.read_bytes(), b"old")

    def test_cuda_command_uses_fast_profile_and_one_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            projector = Path(tmp) / "mmproj-F16.gguf"
            model.write_bytes(b"m" * 32)
            projector.write_bytes(b"p" * 16)
            with mock.patch.object(llm_service, "_hardware_profile", return_value={
                "logical_threads": 32,
                "physical_threads": 16,
                "gpu_vram_gb": 32.0,
            }):
                profile = llm_service._runtime_profile_for(
                    str(model), str(projector), "cuda",
                    {"backend": "cuda"},
                    {
                        "extra_flags": ["-c", "32768", "-fa", "on"],
                        "runtime_profile": {"context_size": 4096},
                    },
                )
            command = llm_service._build_llama_server_command(
                "llama-server", str(model), 54321, profile,
                extra_flags=[
                    "-c", "32768", "-fa", "on", "-np", "3",
                    "--no-cache-prompt",
                ],
                mmproj_path=str(projector),
            )

        self.assertEqual(profile["backend"], "cuda")
        self.assertEqual(profile["context_size"], 4096)
        self.assertEqual(profile["slots"], 1)
        self.assertEqual(command[command.index("--parallel") + 1], "1")
        self.assertNotIn("-np", command)
        self.assertNotIn("-c", command)
        self.assertNotIn("--no-cache-prompt", command)
        self.assertEqual(command[command.index("--ctx-size") + 1], "4096")
        self.assertEqual(command[command.index("--n-gpu-layers") + 1], "-1")
        self.assertEqual(command[command.index("--batch-size") + 1], "2048")
        self.assertEqual(command[command.index("--ubatch-size") + 1], "512")
        self.assertIn("--threads-batch", command)
        self.assertIn("--cache-prompt", command)
        self.assertIn("--perf", command)
        self.assertIn("--mmproj", command)

    def test_runtime_probe_reports_actual_cuda_or_cpu_backend(self):
        cuda_devices = subprocess.CompletedProcess(
            [], 0,
            stdout="Available devices:\n  CUDA0: NVIDIA Test GPU (24576 MiB)\n",
            stderr="",
        )
        cpu_devices = subprocess.CompletedProcess(
            [], 0, stdout="Available devices:\n  (none)\n", stderr="",
        )
        with mock.patch.object(
            llm_service, "_llama_server_probe", return_value=(10289, True),
        ), mock.patch.object(
            llm_service.subprocess, "run", side_effect=[cuda_devices, cpu_devices],
        ):
            cuda = llm_service._llama_server_capabilities("llama-server")
            cpu = llm_service._llama_server_capabilities("llama-server")

        self.assertEqual(cuda["backend"], "cuda")
        self.assertEqual(cuda["devices"], ["CUDA0: NVIDIA Test GPU (24576 MiB)"])
        self.assertEqual(cpu["backend"], "cpu")
        self.assertEqual(cpu["devices"], [])

    def test_linux_cuda_build_failure_falls_back_once_to_truthful_cpu(self):
        observed_status = {}

        def failed_build(*_args):
            observed_status.update(llm_service.get_status())
            raise RuntimeError("bounded build failure")

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "llama-server").write_bytes(b"cpu")
            cpu = {
                "build": 10289, "runnable": True,
                "backend": "cpu", "devices": [],
            }
            with mock.patch.object(sys, "platform", "linux"), mock.patch.object(
                llm_service, "_discover_nvcc", return_value="/managed/nvcc",
            ), mock.patch.object(
                llm_service, "_llama_server_capabilities", return_value=cpu,
            ), mock.patch.object(
                llm_service, "_llama_server_probe", return_value=(10289, True),
            ), mock.patch.object(
                llm_service, "_build_linux_cuda_runtime",
                side_effect=failed_build,
            ) as build:
                result = llm_service._ensure_llama_server(tmp, "cuda")
                again = llm_service._ensure_llama_server(tmp, "cuda")

        self.assertEqual(result["backend"], "cpu")
        self.assertEqual(again["backend"], "cpu")
        self.assertEqual(build.call_count, 1)
        self.assertEqual(observed_status["loading_phase"], "building_runtime")
        self.assertFalse(llm_service.get_status()["loading"])
        self.assertIn("using CPU", llm_service._runtime_fallback_reason)

    def test_cuda_source_build_is_pinned_and_requests_ggml_cuda(self):
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        capabilities = {
            "build": 10289, "runnable": True,
            "backend": "cuda", "devices": ["CUDA0: Test"],
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            llm_service.subprocess, "run", side_effect=fake_run,
        ), mock.patch.object(
            llm_service, "_cuda_architecture", return_value="120",
        ), mock.patch.object(
            llm_service, "_copy_cuda_runtime",
        ), mock.patch.object(
            llm_service, "_llama_server_capabilities", return_value=capabilities,
        ), mock.patch.object(
            llm_service, "_atomic_install_runtime",
        ):
            result = llm_service._build_linux_cuda_runtime(
                os.path.join(tmp, "bin"), "/managed/cuda/bin/nvcc",
            )

        clone = calls[0]
        configure = calls[1]
        self.assertIn(llm_service.LLAMA_SERVER_VERSION, clone)
        self.assertIn("https://github.com/ggml-org/llama.cpp.git", clone)
        self.assertIn("-DGGML_CUDA=ON", configure)
        self.assertIn("-DLLAMA_BUILD_UI=OFF", configure)
        self.assertIn(
            f"-DLLAMA_BUILD_NUMBER={llm_service.LLAMA_SERVER_BUILD}", configure,
        )
        self.assertIn("-DCMAKE_CUDA_ARCHITECTURES=120", configure)
        self.assertIn("-DCMAKE_CUDA_COMPILER=/managed/cuda/bin/nvcc", configure)
        self.assertEqual(result["backend"], "cuda")

    def test_cuda_runtime_environment_includes_staged_library_directory(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/existing"}):
                env = llm_service._cuda_process_env(
                    "/managed/cuda/bin/nvcc", runtime_dir,
                )

        self.assertEqual(
            env["LD_LIBRARY_PATH"].split(os.pathsep)[0],
            os.path.realpath(runtime_dir),
        )
        self.assertIn("/existing", env["LD_LIBRARY_PATH"].split(os.pathsep))

    def test_linked_projector_association_is_contained_deterministic_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "gemma-4-q4_k_m.gguf"
            projector = root / "mmproj-gemma-4-f16.gguf"
            model.write_bytes(b"model")
            projector.write_bytes(b"projector")

            self.assertEqual(
                llm_service._find_sibling_mmproj(str(model)), str(projector),
            )
            discovered = llm_service.discover_gguf_models([tmp])
            self.assertEqual(len(discovered), 1)
            self.assertTrue(discovered[0]["projector_available"])
            self.assertNotIn("path", discovered[0])
            self.assertNotIn(str(root), repr(discovered[0]))

            unrelated_model = root / "qwen-vision-q4_k_m.gguf"
            unrelated_model.write_bytes(b"other model")
            self.assertIsNone(
                llm_service._find_sibling_mmproj(str(unrelated_model))
            )

            second = root / "mmproj-qwen-f16.gguf"
            second.write_bytes(b"other")
            self.assertEqual(
                llm_service._find_sibling_mmproj(str(model)), str(projector),
            )

            projector.unlink()
            second.unlink()
            outside = root.parent / f"{root.name}-outside-mmproj.gguf"
            outside.write_bytes(b"outside")
            try:
                try:
                    projector.symlink_to(outside)
                except OSError:
                    return
                self.assertIsNone(llm_service._find_sibling_mmproj(str(model)))
            finally:
                outside.unlink(missing_ok=True)

    def test_linked_model_named_projector_sidecar_is_not_selectable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "gemma-4-31b-it-heretic-ara-q4_k_m.gguf"
            projector = root / "gemma-4-31b-it-heretic-ara.mmproj-f16.gguf"
            model.write_bytes(b"model")
            projector.write_bytes(b"projector")

            discovered = llm_service.discover_gguf_models([tmp])

            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["label"], model.stem)
            self.assertTrue(discovered[0]["vision_capable"])
            self.assertTrue(discovered[0]["projector_available"])
            self.assertEqual(
                llm_service._find_sibling_mmproj(str(model)), str(projector),
            )
            self.assertEqual(
                llm_service.resolve_discovered_gguf(discovered[0]["id"], [tmp]),
                str(model),
            )

    def test_generate_chat_sends_multimodal_payload_and_prompt_cache(self):
        captured = {}

        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "choices": [{"message": {"content": "answer"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 2},
                }

        def post(_url, **kwargs):
            captured.update(kwargs["json"])
            return Response()

        previous = (
            llm_service._provider, llm_service._vision_available,
            llm_service._model_id,
        )
        self.addCleanup(
            lambda: setattr(llm_service, "_provider", previous[0])
        )
        self.addCleanup(
            lambda: setattr(llm_service, "_vision_available", previous[1])
        )
        self.addCleanup(
            lambda: setattr(llm_service, "_model_id", previous[2])
        )
        with mock.patch.object(llm_service, "load_model"), mock.patch.object(
            llm_service, "is_loaded", return_value=True,
        ), mock.patch.object(
            llm_service, "_image_to_data_url",
            return_value="data:image/jpeg;base64,c3ludGhldGlj",
        ), mock.patch.object(
            llm_service.requests, "post", side_effect=post,
        ), mock.patch.object(llm_service, "_reset_idle_timer"):
            llm_service._provider = "local"
            llm_service._vision_available = True
            llm_service._model_id = "linked"
            result = llm_service.generate_chat(
                [{"role": "user", "content": "what is shown?"}],
                model_id="linked", image_paths=["authorized.png"],
            )
            multimodal_payload = dict(captured)
            captured.clear()
            text_result = llm_service.generate_chat(
                [{"role": "user", "content": "text only"}],
                model_id="linked",
            )

        self.assertEqual(result, "answer")
        self.assertEqual(text_result, "answer")
        self.assertFalse(multimodal_payload["cache_prompt"])
        self.assertTrue(captured["cache_prompt"])
        parts = multimodal_payload["messages"][-1]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "what is shown?"})
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/"))
        self.assertNotIn("authorized.png", repr(multimodal_payload))

    def test_projector_replacement_changes_reload_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            projector = Path(tmp) / "mmproj-F16.gguf"
            projector.write_bytes(b"first")
            first = llm_service._safe_file_identity(str(projector))
            replacement = Path(tmp) / "replacement.gguf"
            replacement.write_bytes(b"first")
            os.replace(replacement, projector)
            second = llm_service._safe_file_identity(str(projector))

        self.assertNotEqual(first, second)

    def test_runtime_launch_identity_tracks_binary_and_command_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "llama-server"
            server.write_bytes(b"first")
            first = llm_service._runtime_launch_identity(
                str(server), ["--cont-batching"], False,
            )
            replacement = Path(tmp) / "replacement"
            replacement.write_bytes(b"first")
            os.replace(replacement, server)
            replaced = llm_service._runtime_launch_identity(
                str(server), ["--cont-batching"], False,
            )
            changed_flags = llm_service._runtime_launch_identity(
                str(server), ["--no-cont-batching"], True,
            )

        self.assertNotEqual(first, replaced)
        self.assertNotEqual(replaced, changed_flags)

    def test_unload_clears_remote_and_runtime_state(self):
        previous = {
            name: getattr(llm_service, name)
            for name in (
                "_provider", "_remote_url", "_api_key", "_requested_device",
                "_runtime_backend", "_runtime_build", "_runtime_devices",
                "_runtime_profile", "_runtime_timings", "_runtime_fallback_reason",
            )
        }
        self.addCleanup(
            lambda: [setattr(llm_service, key, value) for key, value in previous.items()]
        )
        llm_service._provider = "openai"
        llm_service._remote_url = "https://example.invalid"
        llm_service._api_key = "not-a-real-key"
        llm_service._requested_device = "openai"
        llm_service._runtime_backend = "openai"
        llm_service._runtime_build = 123
        llm_service._runtime_devices = ["hidden"]
        llm_service._runtime_profile = {"context_size": 1}
        llm_service._runtime_timings = {"prompt_tokens": 1}
        llm_service._runtime_fallback_reason = "fallback"

        llm_service._unload_inner()

        status = llm_service.get_status()
        self.assertEqual(status["provider"], "local")
        self.assertIsNone(status["requested_device"])
        self.assertIsNone(status["backend"])
        self.assertEqual(status["runtime"]["effective_profile"], {})
        self.assertEqual(status["runtime"]["timings"], {})
        self.assertIsNone(status["runtime"]["fallback_reason"])
        self.assertEqual(llm_service._remote_url, "")
        self.assertEqual(llm_service._api_key, "")

    def test_speed_observations_are_content_free_and_calibrate_catalog(self):
        observed_model = "unsloth/Qwen3.5-4B-GGUF"
        larger_model = "MoonRide/gemma-4-31B-it-heretic-ara-GGUF"
        state_names = (
            "_provider", "_model_id", "_runtime_backend",
            "_runtime_model_size_gb", "_runtime_timings",
            "_runtime_timings_multimodal", "_runtime_speed_variant_digest",
            "_speed_observation_cache",
        )
        previous = {
            name: getattr(llm_service, name) for name in state_names
        }

        def restore():
            for name, value in previous.items():
                setattr(llm_service, name, value)

        self.addCleanup(restore)
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "runtime-speed.json"
            with mock.patch.object(
                llm_service, "_speed_observation_path", return_value=str(store),
            ), mock.patch.object(
                llm_service, "_hardware_profile", return_value={
                    "logical_threads": 32,
                    "physical_threads": 16,
                    "gpu_vram_gb": 32.0,
                },
            ):
                llm_service._provider = "local"
                llm_service._model_id = observed_model
                llm_service._runtime_backend = "cuda"
                llm_service._runtime_model_size_gb = 5.0
                llm_service._runtime_timings = {}
                llm_service._runtime_timings_multimodal = False
                llm_service._runtime_speed_variant_digest = ""
                llm_service._speed_observation_cache = None
                for _ in range(3):
                    llm_service._record_response_metrics({
                        "timings": {
                            "prompt_per_second": 200.0,
                            "predicted_per_second": 40.0,
                        },
                    })

                persisted = store.read_text(encoding="utf-8")
                self.assertNotIn(observed_model, persisted)
                self.assertNotIn(str(store), persisted)
                self.assertNotIn('"messages"', persisted.lower())
                self.assertNotIn('"content"', persisted.lower())
                self.assertNotIn('"path"', persisted.lower())

                llm_service._model_id = ""
                llm_service._runtime_timings = {}
                exact = llm_service.get_model_speed_estimate(
                    observed_model, device="cuda",
                )
                scaled = llm_service.get_model_speed_estimate(
                    larger_model, device="cuda",
                )
                changed_quant = llm_service.get_model_speed_estimate(
                    observed_model,
                    gguf_file_override="different-quant.gguf",
                    device="cuda",
                )

        self.assertEqual(exact["source"], "calibrated")
        self.assertEqual(exact["confidence"], "high")
        self.assertEqual(exact["prompt_tokens_per_second"], 200.0)
        self.assertEqual(exact["generation_tokens_per_second"], 40.0)
        self.assertEqual(scaled["source"], "calibrated")
        self.assertEqual(scaled["confidence"], "medium")
        self.assertEqual(changed_quant["confidence"], "medium")
        self.assertLess(
            scaled["generation_tokens_per_second"],
            exact["generation_tokens_per_second"],
        )
        self.assertNotIn(str(store), repr(scaled))

    def test_runtime_speed_prefers_latest_measurement_and_heuristic_is_complete(self):
        state_names = (
            "_provider", "_model_id", "_runtime_backend", "_requested_device",
            "_runtime_model_size_gb", "_runtime_timings",
            "_runtime_timings_multimodal", "_runtime_speed_variant_digest",
            "_speed_observation_cache",
        )
        previous = {
            name: getattr(llm_service, name) for name in state_names
        }

        def restore():
            for name, value in previous.items():
                setattr(llm_service, name, value)

        self.addCleanup(restore)
        with mock.patch.object(llm_service, "_hardware_profile", return_value={
            "logical_threads": 32,
            "physical_threads": 16,
            "gpu_vram_gb": 32.0,
        }):
            llm_service._provider = "local"
            llm_service._model_id = "unsloth/Qwen3.5-2B-GGUF"
            llm_service._runtime_backend = "cuda"
            llm_service._requested_device = "cuda"
            llm_service._runtime_model_size_gb = 1.13
            llm_service._runtime_timings = {
                "prompt_per_second": 321.25,
                "predicted_per_second": 98.75,
            }
            llm_service._runtime_timings_multimodal = False
            measured = llm_service.get_status()["runtime"]["speed"]
            llm_service._speed_observation_cache = {}
            wrong_modality = llm_service.get_model_speed_estimate(
                "unsloth/Qwen3.5-2B-GGUF",
                device="cuda",
                multimodal=True,
            )

            llm_service._model_id = ""
            llm_service._runtime_timings = {}
            llm_service._speed_observation_cache = {}
            heuristic = llm_service.get_model_speed_estimate(
                "unsloth/Qwen3.5-4B-GGUF", device="cpu",
            )

        self.assertEqual(measured["source"], "measured")
        self.assertEqual(measured["confidence"], "measured")
        self.assertEqual(measured["prompt_tokens_per_second"], 321.2)
        self.assertEqual(measured["generation_tokens_per_second"], 98.8)
        self.assertNotEqual(wrong_modality["source"], "measured")
        self.assertEqual(heuristic["source"], "heuristic")
        self.assertEqual(heuristic["confidence"], "low")
        self.assertGreater(heuristic["prompt_tokens_per_second"], 0)
        self.assertGreater(heuristic["generation_tokens_per_second"], 0)
        self.assertEqual(heuristic["backend"], "cpu")

    def test_invalid_speed_store_root_falls_back_without_breaking_status(self):
        previous_cache = llm_service._speed_observation_cache
        self.addCleanup(
            lambda: setattr(
                llm_service, "_speed_observation_cache", previous_cache,
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "runtime-speed.json"
            with mock.patch.object(
                llm_service, "_speed_observation_path", return_value=str(store),
            ), mock.patch.object(
                llm_service, "_hardware_profile", return_value={
                    "logical_threads": 8,
                    "physical_threads": 4,
                    "gpu_vram_gb": 0.0,
                },
            ):
                for invalid_payload in (
                    "[]",
                    (
                        '{"version":2,"observations":[{'
                        f'"key":"{"a" * 64}","model":"{"b" * 64}",'
                        f'"hardware":"{"c" * 64}","variant":"{"d" * 64}",'
                        '"backend":"cpu","prompt_tps":1,'
                        '"prompt_samples":1e309}]}'
                    ),
                ):
                    store.write_text(invalid_payload, encoding="utf-8")
                    llm_service._speed_observation_cache = None
                    estimate = llm_service.get_model_speed_estimate(
                        "unsloth/Qwen3.5-2B-GGUF", device="cpu",
                    )
                    self.assertEqual(estimate["source"], "heuristic")

    def test_speed_variant_changes_for_same_size_artifact_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "custom-Q4_K_M.gguf"
            model.write_bytes(b"same-size")
            with mock.patch.object(llm_service, "_hardware_profile", return_value={
                "logical_threads": 8,
                "physical_threads": 4,
                "gpu_vram_gb": 0.0,
            }):
                first = llm_service._speed_variant_digest(
                    "linked:opaque", local_gguf_path=str(model), device="cpu",
                )
                replacement = Path(tmp) / "replacement.gguf"
                replacement.write_bytes(b"same-size")
                os.replace(replacement, model)
                second = llm_service._speed_variant_digest(
                    "linked:opaque", local_gguf_path=str(model), device="cpu",
                )

        self.assertNotEqual(first, second)

    def test_speed_hardware_identity_distinguishes_same_vram_gpu_models(self):
        profile = {
            "logical_threads": 32,
            "physical_threads": 16,
            "gpu_vram_gb": 24.0,
        }
        first_gpu = subprocess.CompletedProcess(
            [], 0, "0, GPU-a, Model A, 24576, 600.1\n", "",
        )
        second_gpu = subprocess.CompletedProcess(
            [], 0, "0, GPU-b, Model B, 24576, 600.1\n", "",
        )
        with mock.patch.object(
            llm_service, "_hardware_profile", return_value=profile,
        ), mock.patch.object(
            llm_service.subprocess, "run", return_value=first_gpu,
        ):
            llm_service._speed_hardware_identity_cache.clear()
            first, _ = llm_service._speed_hardware_identity("cuda")
        with mock.patch.object(
            llm_service, "_hardware_profile", return_value=profile,
        ), mock.patch.object(
            llm_service.subprocess, "run", return_value=second_gpu,
        ):
            llm_service._speed_hardware_identity_cache.clear()
            second, _ = llm_service._speed_hardware_identity("cuda")

        self.assertNotEqual(first, second)

    def test_speed_observations_merge_across_concurrent_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = str(Path(tmp) / "runtime-speed.json")
            context = multiprocessing.get_context("spawn")
            start_gate = context.Event()
            workers = [
                context.Process(
                    target=_speed_writer_process,
                    args=(store, start_gate),
                )
                for _ in range(2)
            ]
            for worker in workers:
                worker.start()
            start_gate.set()
            for worker in workers:
                worker.join(timeout=15)
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)
                self.assertEqual(worker.exitcode, 0)
            payload = json.loads(Path(store).read_text(encoding="utf-8"))

        self.assertEqual(len(payload["observations"]), 1)
        row = payload["observations"][0]
        self.assertEqual(row["prompt_samples"], 2)
        self.assertEqual(row["generation_samples"], 2)

    def test_stream_speed_is_recorded_once_after_complete_metrics_merge(self):
        timing_chunk = json.dumps({
            "timings": {
                "prompt_per_second": 100.0,
                "predicted_per_second": 25.0,
            },
            "choices": [{"delta": {"content": "done"}}],
        })
        usage_chunk = json.dumps({
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "total_tokens": 11,
            },
            "choices": [{"delta": {}}],
        })

        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_lines(decode_unicode=True):
                return iter((
                    f"data: {timing_chunk}",
                    f"data: {usage_chunk}",
                    "data: [DONE]",
                ))

        captured = []
        previous = {
            name: getattr(llm_service, name)
            for name in ("_provider", "_model_id", "_vision_available")
        }

        def restore():
            for name, value in previous.items():
                setattr(llm_service, name, value)

        self.addCleanup(restore)
        llm_service._provider = "local"
        llm_service._model_id = "synthetic-model"
        llm_service._vision_available = False
        with mock.patch.object(
            llm_service, "is_loaded", return_value=True,
        ), mock.patch.object(
            llm_service.requests, "post", return_value=Response(),
        ), mock.patch.object(
            llm_service, "_record_response_metrics",
            side_effect=lambda data, **kwargs: captured.append((data, kwargs)),
        ), mock.patch.object(
            llm_service, "_cancel_idle_timer",
        ), mock.patch.object(
            llm_service, "_reset_idle_timer",
        ):
            result = llm_service.generate_streaming(
                "prompt", enable_thinking=False,
            )

        self.assertEqual(result, "done")
        self.assertEqual(len(captured), 1)
        self.assertIn("timings", captured[0][0])
        self.assertIn("usage", captured[0][0])
        self.assertFalse(captured[0][1]["multimodal"])

    def test_measured_speed_does_not_cross_same_id_artifact_variants(self):
        state_names = (
            "_model_id", "_runtime_backend", "_runtime_timings",
            "_runtime_timings_multimodal", "_runtime_speed_variant_digest",
        )
        previous = {
            name: getattr(llm_service, name) for name in state_names
        }

        def restore():
            for name, value in previous.items():
                setattr(llm_service, name, value)

        self.addCleanup(restore)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            llm_service, "_hardware_profile", return_value={
                "logical_threads": 8,
                "physical_threads": 4,
                "gpu_vram_gb": 0.0,
            },
        ):
            first = Path(tmp) / "model-q4.gguf"
            second = Path(tmp) / "model-q8.gguf"
            first.write_bytes(b"q4")
            second.write_bytes(b"q8")
            llm_service._model_id = "linked:same-id"
            llm_service._runtime_backend = "cpu"
            llm_service._runtime_timings = {
                "prompt_per_second": 100.0,
                "predicted_per_second": 20.0,
            }
            llm_service._runtime_timings_multimodal = False
            llm_service._runtime_speed_variant_digest = (
                llm_service._speed_variant_digest(
                    "linked:same-id",
                    local_gguf_path=str(first),
                    device="cpu",
                )
            )
            llm_service._speed_observation_cache = {}

            requested_other = llm_service.get_model_speed_estimate(
                "linked:same-id",
                local_gguf_path=str(second),
                device="cpu",
            )

        self.assertNotEqual(requested_other["source"], "measured")

    def test_status_speed_uses_one_coherent_runtime_snapshot(self):
        state_names = (
            "_provider", "_model_id", "_runtime_backend", "_requested_device",
            "_runtime_timings", "_runtime_timings_multimodal",
        )
        previous = {
            name: getattr(llm_service, name) for name in state_names
        }

        def restore():
            with llm_service._runtime_status_lock:
                for name, value in previous.items():
                    setattr(llm_service, name, value)

        self.addCleanup(restore)
        with llm_service._runtime_status_lock:
            llm_service._provider = "remote"
            llm_service._model_id = "old-model"
            llm_service._runtime_backend = "remote"
            llm_service._requested_device = "remote"
            llm_service._runtime_timings = {
                "prompt_per_second": 100.0,
                "predicted_per_second": 20.0,
            }
            llm_service._runtime_timings_multimodal = False

        snapshot_taken = threading.Event()
        release = threading.Event()
        result = {}

        def speed_from_snapshot(snapshot):
            snapshot_taken.set()
            release.wait(timeout=2)
            return {
                "backend": snapshot["backend"],
                "generation_tokens_per_second": snapshot["timings"][
                    "predicted_per_second"
                ],
            }

        def read_status():
            result.update(llm_service.get_status())

        with mock.patch.object(
            llm_service, "_current_runtime_speed", side_effect=speed_from_snapshot,
        ):
            reader = threading.Thread(target=read_status)
            reader.start()
            self.assertTrue(snapshot_taken.wait(timeout=2))
            with llm_service._runtime_status_lock:
                llm_service._model_id = "new-model"
                llm_service._runtime_backend = "new-provider"
                llm_service._runtime_timings = {
                    "predicted_per_second": 999.0,
                }
            release.set()
            reader.join(timeout=2)

        self.assertFalse(reader.is_alive())
        self.assertEqual(result["model_id"], "old-model")
        self.assertEqual(result["runtime"]["backend"], "remote")
        self.assertEqual(
            result["runtime"]["speed"]["generation_tokens_per_second"], 20.0,
        )

    def test_usage_only_event_retains_latest_measured_speed(self):
        previous = {
            name: getattr(llm_service, name)
            for name in (
                "_provider", "_runtime_timings", "_runtime_backend",
                "_model_id",
            )
        }

        def restore():
            for name, value in previous.items():
                setattr(llm_service, name, value)

        self.addCleanup(restore)
        llm_service._provider = "remote"
        llm_service._model_id = "remote-model"
        llm_service._runtime_backend = "remote"
        llm_service._runtime_timings = {
            "prompt_per_second": 123.0,
            "predicted_per_second": 45.0,
        }

        llm_service._record_response_metrics({
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        })

        self.assertEqual(llm_service._runtime_timings["prompt_per_second"], 123.0)
        self.assertEqual(llm_service._runtime_timings["predicted_per_second"], 45.0)
        speed = llm_service.get_status()["runtime"]["speed"]
        self.assertEqual(speed["source"], "measured")
        self.assertEqual(speed["generation_tokens_per_second"], 45.0)

    def test_symlink_target_replacement_changes_reload_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "cached-blob.gguf"
            link = root / "model.gguf"
            target.write_bytes(b"same-size")
            try:
                link.symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")
            first = llm_service._safe_file_identity(str(link))
            replacement = root / "replacement.gguf"
            replacement.write_bytes(b"same-size")
            os.replace(replacement, target)
            second = llm_service._safe_file_identity(str(link))

        self.assertNotEqual(first, second)

    def test_cuda_architecture_covers_visible_heterogeneous_gpus(self):
        result = subprocess.CompletedProcess(
            [], 0,
            stdout=(
                "0, GPU-aaaa, 8.9\n"
                "1, GPU-bbbb, 12.0\n"
                "2, GPU-cccc, 9.0\n"
            ),
            stderr="",
        )
        with mock.patch.dict(
            os.environ, {"CUDA_VISIBLE_DEVICES": "GPU-aaaa,2"}, clear=False,
        ), mock.patch.object(llm_service.subprocess, "run", return_value=result):
            architectures = llm_service._cuda_architecture()

        self.assertEqual(architectures, "89;90")

    def test_hardware_profile_uses_conservative_visible_gpu_memory(self):
        result = subprocess.CompletedProcess(
            [], 0,
            stdout=(
                "0, GPU-large, 49152\n"
                "1, GPU-small, 12288\n"
            ),
            stderr="",
        )
        with mock.patch.dict(
            os.environ, {"CUDA_VISIBLE_DEVICES": "1"}, clear=False,
        ), mock.patch.object(llm_service.subprocess, "run", return_value=result):
            profile = llm_service._hardware_profile()

        self.assertEqual(profile["gpu_vram_gb"], 12.0)


if __name__ == "__main__":
    unittest.main()
