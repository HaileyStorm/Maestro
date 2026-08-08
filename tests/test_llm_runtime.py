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
import types
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from services import llm_service  # noqa: E402


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
        self.assertIn("await asyncio.to_thread(_prepare_enhance_model)", endpoint)
        self.assertIn("await asyncio.to_thread(_ensure_llm_loaded)", endpoint)
        self.assertIn("result = await asyncio.to_thread(", endpoint)

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

    def test_prompt_enhancer_progress_matches_the_requested_model_and_names_runtime_phases(self):
        prompt_input = (
            Path(__file__).resolve().parents[1]
            / "ui" / "src" / "components" / "Sidebar" / "PromptInput.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("loadingId === expectedModelId", prompt_input)
        self.assertIn("llmData.model_id === expectedModelId", prompt_input)
        self.assertIn("Downloading vision projector", prompt_input)
        self.assertIn("Downloading accelerated LLM runtime", prompt_input)
        self.assertIn("Building accelerated LLM runtime", prompt_input)
        self.assertIn("compactBytes(downloaded)", prompt_input)
        self.assertNotIn("if (!llmData.loaded)", prompt_input)

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

    def test_gemma_31b_uses_current_projector_filename(self):
        entry = llm_service.MODEL_REGISTRY[
            "paperscarecrow/Gemma-4-31B-it-abliterated-gguf"
        ]
        self.assertEqual(
            entry["mmproj_file"], "mmproj-gemma-4-31B-it-BF16.gguf"
        )
        self.assertEqual(entry["mmproj_repo"], "ggml-org/gemma-4-31B-it-GGUF")

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
        supergemma = "Jiunsong/supergemma4-26b-uncensored-gguf-v2"
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
            llm_service.load_model(supergemma)
            self.assertEqual(
                downloads,
                [(supergemma, llm_service.MODEL_REGISTRY[supergemma]["gguf_file"])],
            )

            downloads.clear()
            reset_loaded_state()
            llm_service.load_model(custom_repo)
            self.assertEqual(downloads[-1], (custom_repo, llm_service.DEFAULT_MMPROJ_FILE))

        reset_loaded_state()

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
        observed_model = "Abhiray/gemma-4-E4B-it-heretic-GGUF"
        larger_model = "paperscarecrow/Gemma-4-31B-it-abliterated-gguf"
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
