"""Focused contracts for Maestro's project-scoped LLM chat backend."""
from __future__ import annotations

import ast
import asyncio
import ipaddress
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH_PATH = APP / "launch.py"
LLM_CHAT_UI_PATH = ROOT / "ui" / "src" / "components" / "LlmChat.tsx"
PROMPT_POLISH_PATH = APP / "services" / "director" / "prompt_polish.py"
sys.path.insert(0, str(APP))

from services import llm_service  # noqa: E402


class _ChatResponse:
    def __init__(self, text="answer"):
        self._text = text

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{
                "message": {"content": self._text},
                "finish_reason": "stop",
            }],
        }


class ChatPolicyTests(unittest.TestCase):
    def test_message_validation_preserves_roles_and_enforces_bounds(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "follow-up"},
        ]
        self.assertEqual(llm_service.validate_chat_messages(messages), messages)

        invalid = (
            [],
            [{"role": "assistant", "content": "wrong start"}],
            [{"role": "user", "content": "one"},
             {"role": "user", "content": "two"}],
            [{"role": "user", "content": "ok", "path": "/tmp/private"}],
            [{"role": "user", "content": "   "}],
            [{"role": "user", "content": "u"},
             {"role": "assistant", "content": "unfinished"}],
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                llm_service.validate_chat_messages(value)

        with self.assertRaises(ValueError):
            llm_service.validate_chat_messages([
                {"role": "user", "content": "x" * (
                    llm_service.CHAT_MAX_MESSAGE_CHARS + 1
                )},
            ])
        with self.assertRaises(ValueError):
            llm_service.validate_chat_messages(
                [{"role": "user", "content": "u"}]
                * (llm_service.CHAT_MAX_MESSAGES + 1)
            )

    def test_guides_are_server_owned_deduplicated_and_bounded(self):
        with mock.patch(
            "services.guide_loader.load_guide",
            side_effect=lambda category, name: f"{category}/{name}",
        ):
            selected, prompt = llm_service.load_chat_guides([
                "minimax_h3", "minimax_h3", "flux_image",
            ])
        self.assertEqual(selected, ["minimax_h3", "flux_image"])
        self.assertIn("enhance/minimax_h3_video", prompt)
        self.assertIn("enhance/flux_image", prompt)
        with self.assertRaises(ValueError):
            llm_service.load_chat_guides(["../../private"])
        with self.assertRaises(ValueError):
            llm_service.load_chat_guides(["minimax_h3"] * 5)

    def test_video_guide_catalog_prioritizes_specific_targets(self):
        guides = llm_service.get_chat_guides()
        by_id = {guide["id"]: guide for guide in guides}
        ids = [guide["id"] for guide in guides]

        self.assertLess(ids.index("minimax_h3_ref2va"), ids.index("minimax_h3"))
        self.assertEqual(by_id["minimax_h3_ref2va"]["target_mode"], "video")
        self.assertEqual(
            by_id["minimax_h3_ref2va"]["target_model_prefixes"],
            ["minimax_h3_ref2va"],
        )
        self.assertIn("minimax_h3", by_id["minimax_h3"]["target_model_prefixes"])
        self.assertIn("ltx2", by_id["ltx2_video"]["target_model_prefixes"])
        self.assertIn("i2v", by_id["wan_video"]["target_model_prefixes"])
        self.assertTrue(set(ids).issubset(llm_service.CHAT_GUIDES))

    def test_chat_composer_owns_per_message_guide_controls_and_snapshot(self):
        source = LLM_CHAT_UI_PATH.read_text(encoding="utf-8")
        composer = source.index('className="border-t border-border')
        guide_control = source.index("Add a prompting guide to this message")
        send_button = source.index('onClick={() => void send()}', guide_control)

        self.assertGreater(guide_control, composer)
        self.assertGreater(send_button, guide_control)
        self.assertIn("target_model_prefixes", source)
        self.assertIn("prefix.length > best.prefixLength", source)
        self.assertIn("guideTargetOverridden: guideTargetOverridden.current", source)
        self.assertIn("modelId,", source)
        self.assertIn("customModel,", source)
        self.assertIn("images: requestImages", source)
        self.assertIn("retainedHistory,", source)
        self.assertNotIn("setModelId(response.model_id", source)

    def test_chat_project_instance_races_reset_and_guard_every_completion(self):
        source = LLM_CHAT_UI_PATH.read_text(encoding="utf-8")
        adoption_start = source.index("const adoptProjectInstance")
        adoption_end = source.index("useEffect(() =>", adoption_start)
        adoption = source[adoption_start:adoption_end]

        self.assertIn("pending.controller.abort()", adoption)
        self.assertIn("requestRef.current = null", adoption)
        self.assertIn("setDraft('')", adoption)
        self.assertIn("setUseGuide(false)", adoption)
        self.assertIn("setSelectedImages([])", adoption)
        self.assertGreaterEqual(source.count("pendingStillOwnsProject()"), 4)
        self.assertIn(
            "projectInstanceRef.current === pending.projectInstance",
            source,
        )
        self.assertIn("pending.guideTargetOverridden\n          ? pending.guideId", source)
        self.assertIn("setUseGuide(false)\n      setGuideId", source)

    def test_chat_picker_and_provider_disclosure_are_persistent_and_accessible(self):
        source = LLM_CHAT_UI_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "return `${modelPickerSpeedMeta(model)} · ${model.label}`",
            source,
        )
        self.assertIn('aria-describedby="llm-model-details chat-data-disclosure"', source)
        self.assertIn('id="llm-model-details"', source)
        self.assertIn('id="chat-data-disclosure"', source)
        self.assertEqual(source.count("Conversation history is stored in this browser"), 1)
        self.assertIn("OpenAI external provider", source)
        self.assertIn("Anthropic external provider", source)
        self.assertIn("configured external provider", source)
        self.assertIn("local provider on this machine", source)
        disclosure = source.index('id="chat-data-disclosure"')
        composer = source.index('className="border-t border-border')
        self.assertGreater(disclosure, composer)

    def test_prompt_polish_prefers_ref2va_specific_video_guide(self):
        source = PROMPT_POLISH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(PROMPT_POLISH_PATH))
        mapping = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_VIDEO_ARCH_MAP"
                for target in node.targets
            )
        )
        matcher = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_match_arch"
        )
        getter = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "get_video_guide"
        )
        namespace = {
            "os": os,
            "_ENHANCE_DIR": "enhance",
            "_DIALECT_DIR": "dialect",
        }
        module = ast.fix_missing_locations(ast.Module(
            body=[mapping, matcher, getter], type_ignores=[],
        ))
        exec(compile(module, str(PROMPT_POLISH_PATH), "exec"), namespace)

        self.assertEqual(
            namespace["_match_arch"](
                "minimax_h3_ref2va", namespace["_VIDEO_ARCH_MAP"],
            ),
            "minimax_h3_ref2va_video",
        )
        self.assertEqual(
            namespace["_match_arch"](
                "minimax_h3_w4a8_fl2va", namespace["_VIDEO_ARCH_MAP"],
            ),
            "minimax_h3_video",
        )
        namespace["_load_file"] = lambda path: (
            "ref2va rules"
            if path == os.path.join(
                "enhance", "minimax_h3_ref2va_video.md",
            ) else None
        )
        self.assertEqual(
            namespace["get_video_guide"]("minimax_h3_ref2va", mode="light"),
            "ref2va rules",
        )

    def test_hugging_face_sources_accept_ids_and_main_gguf_urls_only(self):
        self.assertEqual(
            llm_service.normalize_hf_model_source("owner/model-GGUF"),
            ("owner/model-GGUF", None),
        )
        self.assertEqual(
            llm_service.normalize_hf_model_source(
                "https://huggingface.co/owner/model/resolve/main/sub/model.Q4.gguf"
            ),
            ("owner/model", "sub/model.Q4.gguf"),
        )
        rejected = (
            "http://huggingface.co/owner/model",
            "https://huggingface.co:443/owner/model",
            "https://user@huggingface.co/owner/model",
            "https://huggingface.co/owner/model?download=1",
            "https://huggingface.co/owner/model/resolve/dev/model.gguf",
            "https://huggingface.co/owner/model/resolve/main/model.bin",
            "https://huggingface.co/owner/model/resolve/main/%2e%2e/model.gguf",
            "https://huggingface.co/owner/model/resolve/main/bad%5cname.gguf",
            "https://huggingface.co/owner/model/resolve/main/bad%00name.gguf",
        )
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(ValueError):
                llm_service.normalize_hf_model_source(value)

    def test_linked_discovery_is_opaque_bounded_and_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "nested" / "chat.gguf"
            model.parent.mkdir()
            model.write_bytes(b"gguf")
            (root / "mmproj-F16.gguf").write_bytes(b"projector")
            symlink = root / "outside.gguf"
            try:
                symlink.symlink_to(model)
            except OSError:
                pass

            catalog = llm_service.discover_gguf_models([tmp])
            self.assertEqual(len(catalog), 1)
            opaque = catalog[0]["id"]
            self.assertTrue(opaque.startswith("gguf:"))
            self.assertNotIn(tmp, repr(catalog))
            self.assertEqual(
                llm_service.resolve_discovered_gguf(opaque, [tmp]),
                str(model.resolve()),
            )
            self.assertIsNone(
                llm_service.resolve_discovered_gguf("gguf:" + "0" * 24, [tmp])
            )


class ChatRuntimeTests(unittest.TestCase):
    def tearDown(self):
        llm_service.unload_model()

    def test_generate_chat_preserves_the_complete_role_sequence(self):
        captured = {}

        def post(_url, **kwargs):
            captured.update(kwargs)
            return _ChatResponse(" final answer ")

        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "second"},
        ]
        with mock.patch.object(llm_service.requests, "post", side_effect=post):
            answer = llm_service.generate_chat(
                messages,
                model_id="remote-chat-model",
                provider="remote",
                remote_url="http://remote.invalid",
                system_prompt="server-owned guide",
                max_new_tokens=128,
            )

        self.assertEqual(answer, "final answer")
        self.assertEqual(captured["json"]["messages"], [
            {"role": "system", "content": "server-owned guide"},
            *messages,
        ])
        self.assertEqual(captured["json"]["model"], "remote-chat-model")

    def test_generate_chat_serializes_complete_turns(self):
        active = 0
        maximum = 0
        state_lock = threading.Lock()

        def post(_url, **_kwargs):
            nonlocal active, maximum
            with state_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.04)
            with state_lock:
                active -= 1
            return _ChatResponse()

        errors = []

        def run():
            try:
                llm_service.generate_chat(
                    [{"role": "user", "content": "hello"}],
                    model_id="remote-chat-model",
                    provider="remote",
                    remote_url="http://remote.invalid",
                )
            except Exception as error:  # pragma: no cover - assertion below
                errors.append(error)

        with mock.patch.object(llm_service.requests, "post", side_effect=post):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(maximum, 1)

    def test_stale_idle_callback_cannot_unload_after_active_turn_resets_timer(self):
        llm_service.load_model(
            "remote-chat-model", provider="remote",
            remote_url="http://remote.invalid",
        )
        stale_generation = llm_service._idle_timer_generation
        llm_service._reset_idle_timer()

        llm_service._auto_unload(stale_generation)

        self.assertTrue(llm_service.is_loaded())
        self.assertEqual(llm_service.get_status()["model_id"], "remote-chat-model")

    def test_exact_gguf_filename_participates_in_model_identity(self):
        class FakeResponse:
            status_code = 200
            text = '{"status":"ok"}'

            @staticmethod
            def json():
                return {"status": "ok"}

        class FakeProcess:
            def __init__(self):
                self.returncode = None
                self.stdout = io.BytesIO()

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

            def kill(self):
                self.returncode = -9

        commands = []

        def popen(command, **_kwargs):
            commands.append(command)
            return FakeProcess()

        def download(_repo, filename, cache_dir):
            path = Path(cache_dir) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"model")
            return str(path)

        repo = "unsloth/Qwen3.5-2B-GGUF"  # registered text-only model
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            llm_service, "get_model_dir", return_value=tmp,
        ), mock.patch.object(
            llm_service, "_download_gguf", side_effect=download,
        ), mock.patch.object(
            llm_service, "_get_server_exe", return_value="llama-server",
        ), mock.patch.object(
            llm_service, "_find_free_port", return_value=54321,
        ), mock.patch.object(
            llm_service.subprocess, "Popen", side_effect=popen,
        ), mock.patch.object(
            llm_service.requests, "get", return_value=FakeResponse(),
        ), mock.patch.object(llm_service, "_start_log_reader"):
            llm_service.load_model(repo, gguf_file_override="first.gguf")
            llm_service.load_model(repo, gguf_file_override="second.gguf")

        self.assertEqual(len(commands), 2)
        first_model = commands[0][commands[0].index("--model") + 1]
        second_model = commands[1][commands[1].index("--model") + 1]
        self.assertTrue(first_model.endswith("first.gguf"))
        self.assertTrue(second_model.endswith("second.gguf"))


def _launch_chat_namespace(**overrides):
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    wanted = {
        "_llm_provider_api_key",
        "_llm_linked_model_roots",
        "_llm_chat_request_is_external",
        "_llm_model_catalog",
        "_resolve_llm_chat_model",
        "_execute_llm_chat",
        "llm_chat",
    }
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            node.decorator_list = []
            body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Request": object,
        "asyncio": asyncio,
        "ipaddress": ipaddress,
        "traceback": types.SimpleNamespace(print_exc=lambda: None),
        "_request_external_origins": lambda _request: ["http://127.0.0.1"],
        "_approved_local_origin": lambda origin: origin == "http://127.0.0.1",
        "_llm_chat_admission": threading.BoundedSemaphore(1),
        **overrides,
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    return namespace


class ChatRouteTests(unittest.TestCase):
    def test_same_origin_lan_client_is_external_for_chat(self):
        namespace = _launch_chat_namespace(
            _request_external_origins=lambda _request: ["http://192.168.1.20:7860"],
            _approved_local_origin=lambda _origin: False,
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )
        self.assertTrue(namespace["_llm_chat_request_is_external"](request))

    def test_lan_peer_cannot_spoof_loopback_host_headers_for_chat(self):
        namespace = _launch_chat_namespace(
            _request_external_origins=lambda _request: ["http://127.0.0.1"],
            _approved_local_origin=lambda _origin: True,
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
            client=types.SimpleNamespace(host="192.168.1.40"),
        )
        self.assertTrue(namespace["_llm_chat_request_is_external"](request))

    def test_remote_catalog_uses_no_provider_secret_and_marks_current(self):
        calls = []
        fake_service = types.SimpleNamespace(
            get_available_models=lambda **kwargs: (
                calls.append(kwargs)
                or [{
                    "id": "curated/model", "label": "Curated",
                    "provider": "local", "downloaded": False,
                }]
            ),
            discover_gguf_models=lambda _roots: [],
            get_status=lambda: {
                "loaded": True, "model_id": "curated/model",
                "provider": "local",
            },
        )
        fake_package = types.SimpleNamespace(llm_service=fake_service)
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=True),
        )
        namespace = _launch_chat_namespace(
            wgp=types.SimpleNamespace(server_config={"services": {
                "llm_provider": "openai", "openai_api_key": "secret",
                "llm_remote_url": "https://provider.invalid",
            }}),
            _get_linked_model_folders=lambda: ["/linked"],
            _llm_default_device=lambda: "cpu",
        )
        with mock.patch.dict(sys.modules, {"services": fake_package}):
            catalog = namespace["_llm_model_catalog"](request, "openai")

        self.assertEqual(calls, [{
            "provider": "local", "remote_url": "", "api_key": "",
        }])
        self.assertTrue(catalog[0]["current"])

    def test_remote_selection_is_curated_or_server_resolved_opaque_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            gguf = Path(tmp) / "linked.gguf"
            gguf.write_bytes(b"gguf")
            opaque = llm_service.discover_gguf_models([tmp])[0]["id"]
            request = types.SimpleNamespace(
                state=types.SimpleNamespace(maestro_remote=True),
            )
            namespace = _launch_chat_namespace(
                wgp=types.SimpleNamespace(server_config={"services": {}}),
                _get_linked_model_folders=lambda: [tmp],
                _llm_default_device=lambda: "cpu",
            )
            selected = namespace["_resolve_llm_chat_model"](request, opaque)
            self.assertEqual(selected["local_gguf_path"], str(gguf.resolve()))
            self.assertEqual(selected["response_model_id"], opaque)
            with self.assertRaises(ValueError):
                namespace["_resolve_llm_chat_model"](
                    request, "private-owner/private-model",
                )

    def test_local_selection_accepts_hf_file_url_without_exposing_a_path(self):
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )
        namespace = _launch_chat_namespace(
            wgp=types.SimpleNamespace(server_config={"services": {}}),
            _get_linked_model_folders=lambda: [],
            _llm_default_device=lambda: "cpu",
        )
        url = "https://huggingface.co/owner/repo/resolve/main/quant/model.gguf"
        selected = namespace["_resolve_llm_chat_model"](request, url)
        self.assertEqual(selected["model_id"], "owner/repo")
        self.assertEqual(selected["gguf_file_override"], "quant/model.gguf")
        self.assertEqual(selected["response_model_id"], url)

    def test_chat_authorizes_project_before_model_or_generation(self):
        events = []

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        class FakeRequest:
            state = types.SimpleNamespace(maestro_remote=False)

            async def json(self):
                return {
                    "workspace": "project",
                    "model_id": "curated/model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "guide_ids": ["flux_image"],
                    "max_new_tokens": 64,
                }

        fake_service = types.SimpleNamespace(
            CHAT_MAX_NEW_TOKENS=8192,
            validate_chat_messages=lambda value: events.append("validate") or value,
            load_chat_guides=lambda _ids: (["flux_image"], "guide"),
            generate_chat=lambda *_args, **_kwargs: events.append("generate") or "answer",
        )
        fake_package = types.SimpleNamespace(llm_service=fake_service)
        namespace = _launch_chat_namespace(
            HTTPException=FakeHTTPException,
            _require_project_access=lambda *_args: events.append("authorize"),
        )
        namespace["_resolve_llm_chat_model"] = (
            lambda *_args: events.append("resolve") or {
                "model_id": "curated/model",
                "response_model_id": "curated/model",
                "device": "cpu", "provider": "local", "remote_url": "",
                "api_key": "", "local_gguf_path": "",
                "gguf_file_override": "",
            }
        )
        with mock.patch.dict(sys.modules, {"services": fake_package}):
            result = asyncio.run(namespace["llm_chat"](FakeRequest()))

        self.assertEqual(events, ["authorize", "validate", "resolve", "generate"])
        self.assertEqual(result, {
            "text": "answer", "model_id": "curated/model",
            "guide_ids": ["flux_image"],
        })

    def test_chat_rejects_a_second_queued_turn_with_429(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        class FakeRequest:
            state = types.SimpleNamespace(maestro_remote=False)

            async def json(self):
                return {"workspace": "project"}

        admission = threading.BoundedSemaphore(1)
        self.assertTrue(admission.acquire(blocking=False))
        namespace = _launch_chat_namespace(
            HTTPException=FakeHTTPException,
            _require_project_access=lambda *_args: None,
            _llm_chat_admission=admission,
        )
        with self.assertRaises(FakeHTTPException) as raised:
            asyncio.run(namespace["llm_chat"](FakeRequest()))
        admission.release()
        self.assertEqual(raised.exception.status_code, 429)

    def test_route_source_keeps_models_and_guides_contract(self):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(LAUNCH_PATH))
        listing = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "list_llm_models"
        )
        chat = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "llm_chat"
        )
        execute = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_execute_llm_chat"
        )
        listing_source = ast.get_source_segment(source, listing)
        chat_source = ast.get_source_segment(source, chat)
        execute_source = ast.get_source_segment(source, execute)
        self.assertIn('"models": _llm_model_catalog', listing_source)
        self.assertIn('"guides": llm_service.get_chat_guides()', listing_source)
        self.assertIn("await asyncio.to_thread(", execute_source)
        self.assertIn("status_code=429", chat_source)
        self.assertLess(
            chat_source.index("_require_project_access"),
            chat_source.index("_execute_llm_chat"),
        )


if __name__ == "__main__":
    unittest.main()
