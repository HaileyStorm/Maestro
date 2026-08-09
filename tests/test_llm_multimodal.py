"""Privacy and capability contracts for multimodal project Chat."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH_PATH = APP / "launch.py"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.output_access import (  # noqa: E402
    can_access_upload,
    write_upload_access_sidecar,
)
from services import llm_operations  # noqa: E402


class _HTTPException(Exception):
    def __init__(self, *, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _launch_namespace(names: set[str], **overrides):
    if "_execute_llm_chat" in names:
        names = {
            *names,
            "_emit_llm_progress",
            "_explicit_llm_guidance_allowed",
            "_llm_chat_sampling_options",
            "_validate_llm_chat_request",
            "_resolved_local_response_assist",
        }
    if "llm_chat" in names:
        names = {
            *names,
            "_normalize_llm_chat_request_id",
            "_llm_chat_request_digest",
            "_llm_chat_sampling_options",
            "_validate_llm_chat_request",
        }
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    body = []
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ):
            node.decorator_list = []
            body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Request": object,
        "UploadFile": object,
        "File": lambda *args, **kwargs: None,
        "os": os,
        "hmac": hmac,
        "hashlib": hashlib,
        "json": json,
        "math": math,
        "time": time,
        "uuid": uuid,
        "can_access_upload": can_access_upload,
        "HTTPException": _HTTPException,
        "asyncio": asyncio,
        "threading": threading,
        "traceback": types.SimpleNamespace(print_exc=lambda: None),
        "_LLM_CHAT_MAX_IMAGES": 4,
        "_LLM_CHAT_MAX_IMAGE_BYTES": 32 * 1024 * 1024,
        "_LLM_CHAT_IMAGE_EXTENSIONS": {
            ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp",
        },
        "_LLM_CHAT_UPLOAD_MARKER_SUFFIX": ".llm-chat-upload.json",
        "_LLM_CHAT_UPLOAD_TTL_SECONDS": 24 * 60 * 60,
        "_DEFAULT_LLM_REPO": "MoonRide/gemma-4-31B-it-heretic-ara-GGUF",
        "_llm_chat_upload_lock": threading.RLock(),
        "_llm_project_instance_lock": threading.Lock(),
        "_llm_chat_admission": threading.BoundedSemaphore(1),
        "_run_llm_with_selection": (
            lambda _selection, operation, *args, **kwargs:
            operation(*args, **kwargs)
        ),
        "_run_authorized_llm_with_selection": (
            lambda _request, _selection, operation, *args, **kwargs:
            operation(*args, **kwargs)
        ),
        "_llm_operation_scope": lambda *_args: ("owner", "project"),
        "_session_secret": lambda: b"test-session-secret",
        "JSONResponse": __import__(
            "fastapi.responses", fromlist=["JSONResponse"],
        ).JSONResponse,
        **overrides,
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    return namespace


class MultimodalChatRouteTests(unittest.TestCase):
    def test_chat_upload_redacts_path_and_promotes_lan_client(self):
        events = []
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )
        class File:
            content_type = "image/png"
            filename = "safe.png"
            size = 16

            async def read(self, _size=-1):
                return b"\x89PNG\r\n\x1a\n" + b"0" * 8

            async def seek(self, _offset):
                return None

        file = File()

        async def upload_image(upload_request, upload_file, private):
            events.append(("upload", upload_request.state.maestro_remote, private))
            self.assertIs(upload_file, file)
            return {
                "filename": "safe.png",
                "path": "/private/server/uploads/safe.png",
                "url": "/api/v1/uploads/safe.png",
            }

        namespace = _launch_namespace(
            {"_llm_chat_image_signature", "llm_chat_upload"},
            _llm_chat_request_is_external=lambda _request: True,
            _require_project_access=lambda *_args: events.append(("authorize",)),
            _prune_stale_llm_chat_uploads=lambda: events.append(("prune",)),
            _register_llm_chat_upload=lambda _request, workspace, filename: (
                events.append(("register", workspace, filename))
            ),
            upload_image=upload_image,
        )
        result = asyncio.run(namespace["llm_chat_upload"](
            request, "project", file,
        ))
        self.assertEqual(events, [
            ("authorize",),
            ("prune",),
            ("upload", True, True),
            ("register", "project", "safe.png"),
        ])
        self.assertEqual(result, {
            "filename": "safe.png",
            "url": "/api/v1/uploads/safe.png",
        })
        self.assertNotIn("/private", repr(result))

    def test_falsey_malformed_image_paths_are_rejected(self):
        namespace = _launch_namespace(
            {"_resolve_llm_chat_images"},
            _resolve_authorized_request_media=lambda *_args: None,
        )
        request = types.SimpleNamespace(state=types.SimpleNamespace())
        for value in ("", False, 0, {}):
            with self.subTest(value=value), self.assertRaises(_HTTPException) as raised:
                namespace["_resolve_llm_chat_images"](
                    request, {"image_paths": value}, "project",
                )
            self.assertEqual(raised.exception.status_code, 400)

    def test_chat_upload_rejects_oversize_and_spoofed_images(self):
        namespace = _launch_namespace(
            {"_llm_chat_image_signature", "llm_chat_upload"},
            _llm_chat_request_is_external=lambda _request: False,
            _require_project_access=lambda *_args: None,
            _prune_stale_llm_chat_uploads=lambda: None,
            _register_llm_chat_upload=lambda *_args: self.fail(
                "invalid content reached Chat upload registration"
            ),
            upload_image=lambda *_args, **_kwargs: self.fail(
                "invalid content reached the generic uploader"
            ),
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )

        class File:
            content_type = "image/png"
            filename = "image.png"
            size = 32 * 1024 * 1024 + 1

            async def read(self, _size=-1):
                return b"not-an-image"

            async def seek(self, _offset):
                return None

        with self.assertRaises(_HTTPException) as oversized:
            asyncio.run(namespace["llm_chat_upload"](
                request, "project", File(),
            ))
        self.assertEqual(oversized.exception.status_code, 413)

        spoofed = File()
        spoofed.size = 12
        with self.assertRaises(_HTTPException) as invalid:
            asyncio.run(namespace["llm_chat_upload"](
                request, "project", spoofed,
            ))
        self.assertEqual(invalid.exception.status_code, 400)

    def test_private_upload_reference_resolves_only_for_owning_session(self):
        namespace = _launch_namespace(
            {"_resolve_authorized_request_media", "_resolve_llm_chat_images"},
            os=os,
            can_access_upload=can_access_upload,
            _get_active_workspace=lambda: "project",
            _require_authorized_output=lambda *_args: (_ for _ in ()).throw(
                _HTTPException(status_code=404, detail="not found")
            ),
        )
        resolve = namespace["_resolve_llm_chat_images"]
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                uploads = Path(directory) / "uploads"
                (uploads / "audio").mkdir(parents=True)
                image = uploads / "private.png"
                image.write_bytes(b"image")
                owner_id = "a" * 32
                foreign_id = "b" * 32
                write_upload_access_sidecar(str(image), owner_id, private=True)

                owner = types.SimpleNamespace(state=types.SimpleNamespace(
                    maestro_session_id=owner_id, maestro_remote=False,
                ))
                foreign = types.SimpleNamespace(state=types.SimpleNamespace(
                    maestro_session_id=foreign_id, maestro_remote=True,
                ))
                self.assertEqual(
                    resolve(owner, {"image_paths": ["private.png"]}, "project"),
                    [str(image)],
                )
                with self.assertRaises(_HTTPException) as raised:
                    resolve(
                        foreign,
                        {"image_paths": ["private.png"]},
                        "project",
                    )
                self.assertEqual(raised.exception.status_code, 404)
            finally:
                os.chdir(previous)

    def test_chat_upload_cleanup_requires_matching_marker_owner_and_project(self):
        names = {
            "_llm_chat_upload_marker_path",
            "_read_llm_chat_upload_marker",
            "_register_llm_chat_upload",
            "_cleanup_llm_chat_uploads",
        }
        namespace = _launch_namespace(names)
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                uploads = Path(directory) / "uploads"
                uploads.mkdir()
                image = uploads / "single-use.png"
                image.write_bytes(b"\x89PNG\r\n\x1a\n")
                owner_id = "a" * 32
                foreign_id = "b" * 32
                write_upload_access_sidecar(str(image), owner_id, private=True)
                owner = types.SimpleNamespace(state=types.SimpleNamespace(
                    maestro_session_id=owner_id,
                ))
                foreign = types.SimpleNamespace(state=types.SimpleNamespace(
                    maestro_session_id=foreign_id,
                ))

                namespace["_register_llm_chat_upload"](
                    owner, "project", image.name,
                )
                marker = Path(namespace["_llm_chat_upload_marker_path"](str(image)))
                sidecar = Path(f"{image}.access.json")
                self.assertTrue(marker.is_file())
                self.assertEqual(
                    namespace["_cleanup_llm_chat_uploads"](
                        foreign, "project", [str(image)],
                    ),
                    [],
                )
                self.assertEqual(
                    namespace["_cleanup_llm_chat_uploads"](
                        owner, "other-project", [str(image)],
                    ),
                    [],
                )
                self.assertTrue(image.is_file())

                self.assertEqual(
                    namespace["_cleanup_llm_chat_uploads"](
                        owner, "project", [str(image)],
                    ),
                    [image.name],
                )
                self.assertFalse(image.exists())
                self.assertFalse(sidecar.exists())
                self.assertFalse(marker.exists())
            finally:
                os.chdir(previous)

    def test_same_session_upload_reference_reaches_chat_once_authorized(self):
        events = []
        captured = {}

        class Request:
            state = types.SimpleNamespace(
                maestro_remote=False,
                maestro_session_id="owner",
            )

            async def json(self):
                return {
                    "workspace": "project",
                    "model_id": "vision/model",
                    "messages": [{"role": "user", "content": "What is shown?"}],
                    "guide_ids": [],
                    "image_paths": ["upload.png"],
                    "request_id": "44444444-4444-4444-8444-444444444444",
                }

        def resolve_media(request, raw, workspace):
            events.append("resolve-image")
            if (
                request.state.maestro_session_id == "owner"
                and raw == "upload.png"
                and workspace == "project"
            ):
                return "/authorized/uploads/upload.png"
            return None

        def generate_chat(*_args, **kwargs):
            events.append("generate")
            captured.update(kwargs)
            return "answer"

        service = types.SimpleNamespace(
            CHAT_MAX_NEW_TOKENS=8192,
            validate_chat_messages=lambda value: value,
            load_chat_guides=lambda _value: ([], ""),
            generate_chat=generate_chat,
        )
        namespace = _launch_namespace(
            {
                "_resolve_llm_chat_images", "_execute_llm_chat", "llm_chat",
            },
            _llm_chat_request_is_external=lambda _request: False,
            _require_project_access=lambda *_args: events.append("authorize"),
            _resolve_authorized_request_media=resolve_media,
            _cleanup_llm_chat_uploads=lambda _request, workspace, paths: (
                events.append(("cleanup", workspace, paths)) or ["upload.png"]
            ),
            _resolve_llm_chat_model=lambda *_args: {
                "model_id": "vision/model",
                "response_model_id": "vision/model",
                "device": "cuda",
                "provider": "local",
                "remote_url": "",
                "api_key": "",
                "local_gguf_path": "",
                "gguf_file_override": "",
                "vision_capable": True,
            },
        )
        manager = llm_operations.LlmChatOperationManager(ttl_seconds=60)

        async def exercise():
            response = await namespace["llm_chat"](Request())
            for _ in range(100):
                status = manager.status(
                    "44444444444444448444444444444444",
                    owner_key="owner", project_key="project",
                )
                if status and status["status"] == "completed":
                    return response, status
                await asyncio.sleep(0.001)
            self.fail("Chat operation did not complete")

        with mock.patch.object(
            sys.modules["services"], "llm_service", service,
            create=True,
        ), mock.patch.dict(sys.modules, {
            "services.llm_operations": llm_operations,
        }), mock.patch.object(
            llm_operations, "llm_chat_operation_manager", manager,
        ):
            response, result = asyncio.run(exercise())

        self.assertEqual(events, [
            "authorize",
            "resolve-image",
            "generate",
            ("cleanup", "project", ["/authorized/uploads/upload.png"]),
        ])
        self.assertEqual(
            captured["image_paths"], ["/authorized/uploads/upload.png"],
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(result["result"]["text"], "answer")

    def test_foreign_upload_reference_is_denied_before_generation(self):
        generated = []

        class Request:
            state = types.SimpleNamespace(
                maestro_remote=True,
                maestro_session_id="foreign",
            )

            async def json(self):
                return {
                    "workspace": "project",
                    "model_id": "vision/model",
                    "messages": [{"role": "user", "content": "Inspect"}],
                    "image_paths": ["private.png"],
                }

        namespace = _launch_namespace(
            {"_resolve_llm_chat_images", "llm_chat"},
            _llm_chat_request_is_external=lambda _request: True,
            _require_project_access=lambda *_args: None,
            _resolve_authorized_request_media=lambda *_args: None,
            _execute_llm_chat=lambda *_args: generated.append(True),
        )
        with self.assertRaises(_HTTPException) as raised:
            asyncio.run(namespace["llm_chat"](Request()))
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(generated, [])

    def test_submit_returns_while_chat_cleanup_stays_attached_to_server_work(self):
        entered = asyncio.Event()
        finish = asyncio.Event()
        cleaned = []

        class Request:
            state = types.SimpleNamespace(
                maestro_remote=False,
                maestro_session_id="owner",
            )

            async def json(self):
                return {
                    "workspace": "project",
                    "model_id": "vision/model",
                    "messages": [{"role": "user", "content": "Inspect"}],
                    "image_paths": ["upload.png"],
                    "request_id": "33333333-3333-4333-8333-333333333333",
                }

        async def execute(*_args):
            entered.set()
            await finish.wait()
            return {"text": "answer"}

        namespace = _launch_namespace(
            {"_resolve_llm_chat_images", "llm_chat"},
            _llm_chat_request_is_external=lambda _request: False,
            _require_project_access=lambda *_args: None,
            _resolve_authorized_request_media=(
                lambda *_args: "/authorized/uploads/upload.png"
            ),
            _cleanup_llm_chat_uploads=lambda *_args: cleaned.append(True),
            _execute_llm_chat=execute,
        )
        namespace["_validate_llm_chat_request"] = lambda *_args: {
            "messages": [{"role": "user", "content": "Inspect"}],
            "guide_ids": [],
            "system_prompt": "",
            "selection": {"vision_capable": True},
            "max_new_tokens": 2048,
            "temperature": 0.7,
            "top_p": 0.9,
        }

        manager = llm_operations.LlmChatOperationManager(ttl_seconds=60)

        async def exercise():
            response = await namespace["llm_chat"](Request())
            self.assertEqual(response.status_code, 202)
            await entered.wait()
            self.assertEqual(cleaned, [])
            finish.set()
            for _ in range(100):
                status = manager.status(
                    "33333333333343338333333333333333",
                    owner_key="owner", project_key="project",
                )
                if status and status["status"] == "completed":
                    return
                await asyncio.sleep(0.001)
            self.fail("Chat operation did not complete")

        with mock.patch.object(
            llm_operations, "llm_chat_operation_manager", manager,
        ):
            asyncio.run(exercise())
        self.assertEqual(cleaned, [True])

    def test_known_text_only_model_rejects_images_before_inference(self):
        generated = []
        service = types.SimpleNamespace(
            CHAT_MAX_NEW_TOKENS=8192,
            validate_chat_messages=lambda value: value,
            load_chat_guides=lambda _value: ([], ""),
            generate_chat=lambda *_args, **_kwargs: generated.append(True),
        )
        package = types.SimpleNamespace(llm_service=service)
        namespace = _launch_namespace(
            {"_execute_llm_chat"},
            _resolve_llm_chat_model=lambda *_args: {
                "vision_capable": False,
            },
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )
        with mock.patch.dict(sys.modules, {"services": package}):
            with self.assertRaises(_HTTPException) as raised:
                asyncio.run(namespace["_execute_llm_chat"](
                    request,
                    {
                        "model_id": "text/model",
                        "messages": [{"role": "user", "content": "Inspect"}],
                    },
                    ["/authorized/image.png"],
                ))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("text only", raised.exception.detail)
        self.assertEqual(generated, [])


class MultimodalCatalogTests(unittest.TestCase):
    def test_project_authorization_precedes_catalog_work(self):
        catalog_calls = []
        namespace = _launch_namespace(
            {"list_llm_models"},
            _llm_project_instance_id=lambda *_args: (_ for _ in ()).throw(
                _HTTPException(status_code=423, detail="locked")
            ),
            _llm_model_catalog=lambda *_args: catalog_calls.append(True),
        )
        package = types.SimpleNamespace(
            llm_service=types.SimpleNamespace(get_chat_guides=lambda: []),
        )
        with mock.patch.dict(sys.modules, {"services": package}):
            with self.assertRaises(_HTTPException) as raised:
                namespace["list_llm_models"](
                    types.SimpleNamespace(), workspace="locked-project",
                )
        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(catalog_calls, [])

    def test_catalog_exposes_capability_and_runtime_without_paths(self):
        service = types.SimpleNamespace(
            get_available_models=lambda **_kwargs: [{
                "id": "vision/model",
                "label": "Vision",
                "size_hint": "8 GB",
                "provider": "local",
                "source": "Maestro catalog",
                "downloaded": True,
                "vision_capable": True,
                "projector_available": True,
                "path": "/secret/model.gguf",
                "runtime_profile": {
                    "backend": "llama.cpp",
                    "device": "cuda",
                    "gpu_layers": -1,
                    "model_path": "/secret/model.gguf",
                },
            }],
            discover_gguf_models=lambda _roots: [],
            get_model_speed_estimate=lambda *_args, **_kwargs: {
                "prompt_tokens_per_second": 123.456,
                "generation_tokens_per_second": 17.89,
                "source": "calibrated",
                "confidence": "medium",
                "reason": "Same-PC observations for a similar model-size bucket.",
                "sample_count": 3,
                "backend": "cuda",
            },
            get_status=lambda: {
                "loaded": True,
                "model_id": "vision/model",
                "provider": "local",
                "device": "cuda",
                "loading": True,
                "loading_model_id": "vision/model",
                "loading_phase": "downloading projector",
                "download": {
                    "filename": "/secret/mmproj.gguf",
                    "downloaded_bytes": 10,
                    "total_bytes": 100,
                },
            },
        )
        package = types.SimpleNamespace(llm_service=service)
        namespace = _launch_namespace(
            {
                "_truthful_llm_status",
                "_safe_llm_speed",
                "_llm_provider_api_key",
                "_llm_model_catalog",
            },
            _llm_chat_request_is_external=lambda _request: False,
            _llm_linked_model_roots=lambda: ["/secret/linked"],
            _llm_default_device=lambda: "cuda",
            wgp=types.SimpleNamespace(server_config={"services": {
                "llm_model_id": "vision/model",
                "llm_provider": "local",
            }}),
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )
        with mock.patch.dict(sys.modules, {"services": package}):
            catalog = namespace["_llm_model_catalog"](request)

        self.assertEqual(len(catalog), 1)
        model = catalog[0]
        self.assertTrue(model["vision_capable"])
        self.assertTrue(model["projector_available"])
        self.assertTrue(model["current"])
        self.assertTrue(model["configured"])
        self.assertFalse(model["vision_available"])
        self.assertEqual(model["effective_device"], "cuda")
        self.assertEqual(model["runtime_profile"]["gpu_layers"], -1)
        self.assertEqual(model["download"]["downloaded_bytes"], 10)
        self.assertEqual(model["speed"]["source"], "calibrated")
        self.assertEqual(model["speed"]["sample_count"], 3)
        self.assertAlmostEqual(
            model["speed"]["generation_tokens_per_second"], 17.89,
        )
        self.assertNotIn("/secret", repr(catalog))
        self.assertNotIn("filename", repr(catalog))

    def test_project_instance_changes_when_same_name_is_recreated(self):
        namespace = _launch_namespace(
            {"_llm_project_instance_id"},
            _require_project_access=lambda *_args: None,
            _session_secret=lambda: b"server-secret",
        )
        request = types.SimpleNamespace(state=types.SimpleNamespace())
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            namespace["_existing_workspace_dir"] = lambda _name: str(project)
            first = namespace["_llm_project_instance_id"](
                request, "project",
            )
            marker = project / ".llm-chat-instance"
            self.assertTrue(marker.is_file())
            (project / "ordinary-output.txt").write_text("content")
            self.assertEqual(
                namespace["_llm_project_instance_id"](request, "project"),
                first,
            )
            (project / "ordinary-output.txt").unlink()
            marker.unlink()
            project.rmdir()
            time.sleep(0.002)
            project.mkdir()
            second = namespace["_llm_project_instance_id"](
                request, "project",
            )

        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, second)
        self.assertNotIn(directory, first)


class LlmChatUiContractTests(unittest.TestCase):
    def test_history_is_project_instance_scoped_and_selection_prefers_loaded(self):
        source = (
            ROOT / "ui" / "src" / "components" / "LlmChat.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("storageKey(workspace, projectInstance)", source)
        self.assertIn("data.project_instance", source)
        selection = source[source.index("function preferredModelId"):]
        loaded = selection.index("models.find(model => model.current)")
        configured = selection.index("models.find(model => model.configured)")
        recommended = selection.index("includes('recommended')")
        self.assertLess(loaded, configured)
        self.assertLess(configured, recommended)
        self.assertIn("modelSelectionTouched.current = true", source)
        self.assertIn("prompt_tokens_per_second", source)
        self.assertIn("generation_tokens_per_second", source)
        self.assertIn("Speed basis:", source)


class TransientLlmStatusTests(unittest.TestCase):
    def test_local_process_is_loading_until_model_identity_is_committed(self):
        namespace = _launch_namespace({"_truthful_llm_status"})
        service = types.SimpleNamespace(get_status=lambda: {
            "loaded": True,
            "model_id": None,
            "device": "",
            "provider": "local",
            "vision_available": True,
            "loading": False,
            "loading_model_id": "vision/model",
            "loading_phase": None,
        })

        status = namespace["_truthful_llm_status"](service)

        self.assertFalse(status["loaded"])
        self.assertIsNone(status["device"])
        self.assertFalse(status["vision_available"])
        self.assertTrue(status["loading"])
        self.assertEqual(status["loading_model_id"], "vision/model")
        self.assertEqual(status["loading_phase"], "loading model")

    def test_ready_and_remote_statuses_are_not_rewritten(self):
        namespace = _launch_namespace({"_truthful_llm_status"})
        statuses = (
            {
                "loaded": True, "model_id": "local/model", "device": "cuda",
                "provider": "local", "vision_available": True,
                "loading": False,
            },
            {
                "loaded": True, "model_id": "remote/model", "device": "",
                "provider": "remote", "vision_available": False,
                "loading": False,
            },
        )
        for expected in statuses:
            with self.subTest(provider=expected["provider"]):
                service = types.SimpleNamespace(
                    get_status=lambda value=expected: dict(value),
                )
                self.assertEqual(
                    namespace["_truthful_llm_status"](service),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
