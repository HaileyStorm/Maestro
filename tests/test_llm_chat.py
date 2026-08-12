"""Focused contracts for Maestro's project-scoped LLM chat backend."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import ipaddress
import io
import json
import os
from pathlib import Path
import re
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
LLM_CHAT_UI_PATH = ROOT / "ui" / "src" / "components" / "LlmChat.tsx"
PROMPT_POLISH_PATH = APP / "services" / "director" / "prompt_polish.py"
sys.path.insert(0, str(APP))

from services import (  # noqa: E402
    llm_operations,
    llm_refusal_corpus,
    llm_response_assist,
    llm_service,
)


def _jsx_div_element(source: str, marker: str) -> tuple[int, str, str]:
    start = source.index(marker)
    opening = source[start:source.index(">", start) + 1]
    depth = 0
    for match in re.finditer(r"<(/?)div(?:\s|>)", source[start:]):
        depth += -1 if match.group(1) else 1
        if depth == 0:
            return start, opening, source[start:start + match.end()]
    raise AssertionError(f"Unclosed JSX div for {marker!r}")


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


class _ChatTimer:
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
        composer_start, opening, composer = _jsx_div_element(
            source,
            "<div data-chat-composer",
        )
        guide_control = composer.index("Add a prompting guide to this message")
        send_button = composer.index('onClick={() => void send()}', guide_control)

        self.assertLess(source.index("data-chat-transcript"), composer_start)
        self.assertIn("max-h-[46%]", opening)
        self.assertIn("overflow-y-auto overscroll-contain", opening)
        self.assertIn("lg:max-h-none lg:overflow-visible", opening)
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
            "return `${model.label} · ${speedLabel}`",
            source,
        )
        picker = source[
            source.index("function modelPickerLabel"):
            source.index("function providerDisplayName")
        ]
        technical = source[
            source.index("function modelTechnicalMeta"):
            source.index("function chatProgressStep")
        ]
        self.assertNotIn("speedMeta(model)", picker)
        self.assertIn("speedMeta(model)", technical)
        self.assertIn(">Technical details</summary>", source)
        self.assertIn('aria-describedby="llm-model-details chat-data-disclosure"', source)
        self.assertIn('id="llm-model-details"', source)
        self.assertIn('id="chat-data-disclosure"', source)
        self.assertEqual(source.count("Conversation history is stored in this browser"), 1)
        self.assertIn("OpenAI external provider", source)
        self.assertIn("Anthropic external provider", source)
        self.assertIn("configured external provider", source)
        self.assertIn("local provider on the Maestro computer", source)
        _, _, composer = _jsx_div_element(source, "<div data-chat-composer")
        self.assertIn('id="chat-data-disclosure"', composer)

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

    def test_all_generation_entrypoints_finalize_idle_timer_on_success_and_failure(self):
        class StreamingResponse:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_lines(decode_unicode=True):
                return iter((
                    'data: {"choices":[{"delta":{"content":"answer"}}]}',
                    "data: [DONE]",
                ))

        _ChatTimer.instances.clear()
        with mock.patch.object(llm_service.threading, "Timer", _ChatTimer):
            llm_service.load_model(
                "remote-chat-model", provider="remote",
                remote_url="http://remote.invalid",
            )

            successful_calls = (
                lambda: llm_service.generate("prompt"),
                lambda: llm_service.generate_streaming(
                    "prompt", enable_thinking=False,
                ),
                lambda: llm_service.generate_chat(
                    [{"role": "user", "content": "hello"}],
                    model_id="remote-chat-model", provider="remote",
                    remote_url="http://remote.invalid",
                ),
            )
            successful_responses = (
                _ChatResponse(), StreamingResponse(), _ChatResponse(),
            )
            for call, response in zip(successful_calls, successful_responses):
                previous = llm_service._idle_timer
                with mock.patch.object(
                    llm_service.requests, "post", return_value=response,
                ):
                    self.assertEqual(call(), "answer")
                self.assertIsNot(llm_service._idle_timer, previous)
                self.assertTrue(llm_service._idle_timer.started)

            failing_calls = (
                lambda: llm_service.generate("prompt"),
                lambda: llm_service.generate_streaming(
                    "prompt", enable_thinking=False,
                ),
                lambda: llm_service.generate_chat(
                    [{"role": "user", "content": "hello"}],
                    model_id="remote-chat-model", provider="remote",
                    remote_url="http://remote.invalid",
                ),
            )
            for call in failing_calls:
                previous = llm_service._idle_timer
                with mock.patch.object(
                    llm_service.requests, "post",
                    side_effect=llm_service.requests.ConnectionError("synthetic"),
                ):
                    with self.assertRaises(RuntimeError):
                        call()
                self.assertIsNot(llm_service._idle_timer, previous)
                self.assertTrue(llm_service._idle_timer.started)
                self.assertTrue(llm_service.is_loaded())

    def test_loaded_model_lease_blocks_exact_identity_switch_until_callback_exits(self):
        lease_entered = threading.Event()
        release_lease = threading.Event()
        switch_started = threading.Event()
        switch_done = threading.Event()
        identities = []
        errors = []

        def hold_lease():
            try:
                with llm_service.loaded_model_lease(
                    model_id="remote-a", provider="remote",
                    remote_url="http://remote-a.invalid",
                ) as identity:
                    identities.append(identity)
                    lease_entered.set()
                    release_lease.wait(timeout=2)
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)

        def switch_model():
            switch_started.set()
            llm_service.load_model(
                "remote-b", provider="remote",
                remote_url="http://remote-b.invalid",
            )
            switch_done.set()

        with mock.patch.object(llm_service.threading, "Timer", _ChatTimer):
            holder = threading.Thread(target=hold_lease)
            holder.start()
            self.assertTrue(lease_entered.wait(timeout=1))
            switcher = threading.Thread(target=switch_model)
            switcher.start()
            self.assertTrue(switch_started.wait(timeout=1))
            self.assertFalse(switch_done.wait(timeout=0.05))
            self.assertEqual(llm_service._loaded_model_key, identities[0])

            release_lease.set()
            holder.join(timeout=2)
            switcher.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertTrue(switch_done.is_set())
        self.assertEqual(llm_service.get_status()["model_id"], "remote-b")
        self.assertNotEqual(llm_service._loaded_model_key, identities[0])

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
        "_emit_llm_progress",
        "_llm_chat_sampling_options",
        "_validate_llm_chat_request",
        "_resolved_local_response_assist",
        "_llm_route_progress_callback",
        "_run_llm_route_operation",
        "_normalize_llm_chat_request_id",
        "_llm_chat_request_digest",
        "_explicit_llm_guidance_allowed",
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
        "hashlib": hashlib,
        "hmac": hmac,
        "ipaddress": ipaddress,
        "json": json,
        "math": __import__("math"),
        "uuid": uuid,
        "JSONResponse": __import__(
            "fastapi.responses", fromlist=["JSONResponse"],
        ).JSONResponse,
        "traceback": types.SimpleNamespace(print_exc=lambda: None),
        "_request_external_origins": lambda _request: ["http://127.0.0.1"],
        "_approved_local_origin": lambda origin: origin == "http://127.0.0.1",
        "_run_llm_with_selection": (
            lambda _selection, operation, *args, **kwargs:
            operation(*args, **kwargs)
        ),
        "_run_authorized_llm_with_selection": (
            lambda _request, _selection, operation, *args, **kwargs:
            operation(*args, **kwargs)
        ),
        "_resolve_llm_chat_images": lambda *_args: [],
        "_llm_operation_scope": lambda *_args: ("owner", "project"),
        "_session_secret": lambda: b"test-session-secret",
        "_llm_chat_admission": threading.BoundedSemaphore(1),
        **overrides,
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    return namespace


class ChatRouteTests(unittest.TestCase):
    def test_response_assist_requires_literal_opt_in_current_consent_and_locality(self):
        namespace = _launch_chat_namespace()
        namespace["_explicit_llm_guidance_allowed"] = (
            lambda body: body.get("consent") is True
        )
        resolve = namespace["_resolved_local_response_assist"]
        body = {
            "explicit_output": True,
            "consent": True,
        }
        local = {
            "provider": "local", "remote_url": "", "api_key": "",
        }
        allowed = resolve(body, local)
        self.assertEqual(
            allowed, llm_response_assist.build_server_response_assist(),
        )
        self.assertTrue(allowed["retry_on_refusal"])

        for changed_body, changed_selection in (
            ({**body, "explicit_output": 1}, local),
            ({**body, "consent": False}, local),
            (body, {**local, "provider": "openai"}),
            (body, {**local, "provider": "remote"}),
            (body, {**local, "remote_url": "https://provider.invalid"}),
            (body, {**local, "api_key": "secret"}),
        ):
            with self.subTest(body=changed_body, selection=changed_selection):
                self.assertIsNone(resolve(changed_body, changed_selection))

    def test_response_assist_is_server_owned_and_body_cannot_override_it(self):
        namespace = _launch_chat_namespace()
        namespace["_explicit_llm_guidance_allowed"] = lambda _body: True
        resolve = namespace["_resolved_local_response_assist"]
        selection = {
            "provider": "local", "remote_url": "", "api_key": "",
        }
        expected = llm_response_assist.build_server_response_assist()
        self.assertEqual(resolve({"explicit_output": True}, selection), expected)
        self.assertEqual(resolve({
            "explicit_output": True,
            "response_assist": {
                "assistant_prefill": "malicious override",
                "refusal_profile": "disabled",
                "retry_on_refusal": False,
            },
        }, selection), expected)

    def test_eligible_chat_passes_assist_and_disables_thinking_for_prefill(self):
        captured = {}
        progress = []
        service = types.SimpleNamespace(
            CHAT_MAX_NEW_TOKENS=8192,
            validate_chat_messages=lambda value: value,
            load_chat_guides=lambda _ids: ([], "base guide"),
            generate_chat=lambda *_args, **kwargs: (
                captured.update(kwargs) or "answer"
            ),
        )
        namespace = _launch_chat_namespace()
        namespace["_resolve_llm_chat_model"] = lambda *_args: {
                "model_id": "local/model",
                "response_model_id": "local/model",
                "device": "cuda",
                "provider": "local",
                "remote_url": "",
                "api_key": "",
                "local_gguf_path": "",
                "gguf_file_override": "",
                "vision_capable": True,
            }
        namespace["_explicit_llm_guidance_allowed"] = lambda _body: True
        body = {
            "model_id": "local/model",
            "messages": [{"role": "user", "content": "hello"}],
            "explicit_output": True,
        }
        with mock.patch.object(sys.modules["services"], "llm_service", service):
            result = asyncio.run(namespace["_execute_llm_chat"](
                types.SimpleNamespace(), body, [], progress.append,
            ))

        self.assertEqual(result["text"], "answer")
        self.assertEqual(
            captured["response_assist"],
            llm_response_assist.build_server_response_assist(),
        )
        self.assertIs(captured["enable_thinking"], False)
        self.assertTrue(callable(captured["progress_callback"]))
        self.assertEqual(progress[-1]["attempt_cap"], 2)

    def test_direct_llm_route_injects_server_assist_and_request_progress(self):
        namespace = _launch_chat_namespace()
        namespace["_explicit_llm_guidance_allowed"] = lambda _body: True
        events = []
        captured = {}
        callback = events.append
        request = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_llm_progress_callback=callback,
        ))

        result = namespace["_run_llm_route_operation"](
            request,
            {"explicit_output": True},
            {"provider": "local", "remote_url": "", "api_key": ""},
            lambda **kwargs: captured.update(kwargs) or "answer",
        )

        self.assertEqual(result, "answer")
        self.assertEqual(
            captured["response_assist"],
            llm_response_assist.build_server_response_assist(),
        )
        self.assertIs(captured["progress_callback"], callback)

    def test_request_digest_binds_every_public_inference_option(self):
        namespace = _launch_chat_namespace()
        digest = namespace["_llm_chat_request_digest"]
        base = {
            "model_id": "curated/model",
            "messages": [{"role": "user", "content": "hello"}],
            "guide_ids": [],
            "max_new_tokens": 64,
            "temperature": 0.7,
            "top_p": 0.9,
            "explicit_output": True,
        }
        original = digest(base, workspace="project", image_paths=[])
        mutations = (
            {"max_new_tokens": 65},
            {"temperature": 0.8},
            {"top_p": 0.8},
            {"explicit_output": False},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertNotEqual(
                    original,
                    digest(
                        {**base, **mutation},
                        workspace="project", image_paths=[],
                    ),
                )
        self.assertEqual(
            original,
            digest(
                {**base, "response_assist": {"assistant_prefill": "ignored"}},
                workspace="project", image_paths=[],
            ),
        )
        with mock.patch.object(
            llm_response_assist,
            "SERVER_RESPONSE_ASSIST_IDENTITY",
            {"version": "future-test-version", "profile": "high_confidence"},
        ):
            self.assertNotEqual(
                original,
                digest(base, workspace="project", image_paths=[]),
            )

    def test_request_digest_binds_corpus_revision_but_never_literal_text(self):
        namespace = _launch_chat_namespace()
        digest = namespace["_llm_chat_request_digest"]
        body = {
            "model_id": "curated/model",
            "messages": [{"role": "user", "content": "hello"}],
            "explicit_output": True,
        }
        revision_one = llm_refusal_corpus.RefusalCorpusSnapshot(
            revision=1, literals=("first private literal",),
        )
        same_revision = llm_refusal_corpus.RefusalCorpusSnapshot(
            revision=1, literals=("different private literal",),
        )
        revision_two = llm_refusal_corpus.RefusalCorpusSnapshot(
            revision=2, literals=("first private literal",),
        )
        first = digest(
            body, workspace="project", image_paths=[],
            response_assist_snapshot=revision_one,
        )
        self.assertEqual(first, digest(
            body, workspace="project", image_paths=[],
            response_assist_snapshot=same_revision,
        ))
        self.assertNotEqual(first, digest(
            body, workspace="project", image_paths=[],
            response_assist_snapshot=revision_two,
        ))

    def test_in_flight_chat_uses_frozen_corpus_and_next_resolution_uses_latest(self):
        captured = {}
        service = types.SimpleNamespace(
            CHAT_MAX_NEW_TOKENS=8192,
            validate_chat_messages=lambda value: value,
            load_chat_guides=lambda _ids: ([], "base guide"),
            generate_chat=lambda *_args, **kwargs: (
                captured.update(kwargs) or "answer"
            ),
        )
        namespace = _launch_chat_namespace()
        namespace["_resolve_llm_chat_model"] = lambda *_args: {
            "model_id": "local/model",
            "response_model_id": "local/model",
            "device": "cuda",
            "provider": "local",
            "remote_url": "",
            "api_key": "",
            "local_gguf_path": "",
            "gguf_file_override": "",
            "vision_capable": True,
        }
        namespace["_explicit_llm_guidance_allowed"] = lambda _body: True
        body = {
            "model_id": "local/model",
            "messages": [{"role": "user", "content": "hello"}],
            "explicit_output": True,
        }
        frozen = llm_refusal_corpus.RefusalCorpusSnapshot(
            revision=1, literals=("old literal",),
        )
        latest = llm_refusal_corpus.RefusalCorpusSnapshot(
            revision=2, literals=("new literal",),
        )
        with mock.patch.object(sys.modules["services"], "llm_service", service):
            asyncio.run(namespace["_execute_llm_chat"](
                types.SimpleNamespace(),
                body,
                [],
                response_assist_snapshot=frozen,
            ))
        self.assertEqual(captured["response_assist"]["refusal_literals"], [
            "old literal",
        ])
        with mock.patch.object(
            llm_response_assist,
            "response_assist_corpus_snapshot",
            return_value=latest,
        ):
            next_options = namespace["_resolved_local_response_assist"](
                body,
                namespace["_resolve_llm_chat_model"](None, None),
            )
        self.assertEqual(next_options["refusal_literals"], ["new literal"])

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
        with mock.patch.dict(sys.modules, {
            "services": fake_package,
            "services.llm_operations": llm_operations,
        }):
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
                    "request_id": "11111111-1111-4111-8111-111111111111",
                }

        generated_kwargs = {}

        def generate_chat(*_args, **kwargs):
            generated_kwargs.update(kwargs)
            events.append("generate")
            return "answer"

        fake_service = types.SimpleNamespace(
            CHAT_MAX_NEW_TOKENS=8192,
            validate_chat_messages=lambda value: events.append("validate") or value,
            load_chat_guides=lambda _ids: (["flux_image"], "guide"),
            generate_chat=generate_chat,
        )
        fake_package = types.SimpleNamespace(llm_service=fake_service)

        def authorize(*_args, **kwargs):
            self.assertEqual(kwargs.get("permission"), "project.generate")
            events.append("authorize")

        namespace = _launch_chat_namespace(
            HTTPException=FakeHTTPException,
            _require_project_access=authorize,
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
        manager = llm_operations.LlmChatOperationManager(ttl_seconds=60)

        async def exercise():
            response = await namespace["llm_chat"](FakeRequest())
            for _ in range(100):
                status = manager.status(
                    "11111111111141118111111111111111",
                    owner_key="owner", project_key="project",
                )
                if status and status["status"] == "completed":
                    return response, status
                await asyncio.sleep(0.001)
            self.fail("Chat operation did not complete")

        with mock.patch.dict(sys.modules, {
            "services": fake_package,
            "services.llm_operations": llm_operations,
        }), mock.patch.object(
            llm_operations, "llm_chat_operation_manager", manager,
        ):
            response, result = asyncio.run(exercise())

        self.assertEqual(events, ["authorize", "validate", "resolve", "generate"])
        self.assertEqual(response.status_code, 202)
        self.assertEqual(result["result"], {
            "text": "answer", "model_id": "curated/model",
            "guide_ids": ["flux_image"],
        })
        self.assertIsNone(generated_kwargs["response_assist"])
        self.assertTrue(callable(generated_kwargs["progress_callback"]))
        self.assertEqual(generated_kwargs["temperature"], 0.7)
        self.assertEqual(generated_kwargs["top_p"], 0.9)

    def test_chat_rejects_a_second_queued_turn_with_429(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        class FakeRequest:
            state = types.SimpleNamespace(maestro_remote=False)

            async def json(self):
                return {
                    "workspace": "project",
                    "request_id": "22222222-2222-4222-8222-222222222222",
                }

        admission = threading.BoundedSemaphore(1)
        self.assertTrue(admission.acquire(blocking=False))
        namespace = _launch_chat_namespace(
            HTTPException=FakeHTTPException,
            _require_project_access=lambda *_args, **_kwargs: None,
            _llm_chat_admission=admission,
        )
        namespace["_validate_llm_chat_request"] = lambda *_args: {
            "messages": [],
            "guide_ids": [],
            "system_prompt": "",
            "selection": {},
            "max_new_tokens": 1,
            "temperature": 0.7,
            "top_p": 0.9,
        }
        manager = llm_operations.LlmChatOperationManager(ttl_seconds=60)
        with mock.patch.object(
            llm_operations, "llm_chat_operation_manager", manager,
        ), self.assertRaises(FakeHTTPException) as raised:
            asyncio.run(namespace["llm_chat"](FakeRequest()))
        admission.release()
        self.assertEqual(raised.exception.status_code, 429)

    def test_invalid_inputs_are_rejected_before_chat_operation_admission(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        class FakeRequest:
            state = types.SimpleNamespace(maestro_remote=False)

            def __init__(self, body):
                self.body = body

            async def json(self):
                return dict(self.body)

        admitted = []
        manager = types.SimpleNamespace(
            submit=lambda **_kwargs: admitted.append(True),
        )

        def validate_messages(value):
            if value == "invalid":
                raise ValueError("invalid messages")
            return value

        def load_guides(value):
            if value == ["invalid"]:
                raise ValueError("invalid guide")
            return value, "guide"

        service = types.SimpleNamespace(
            CHAT_MAX_NEW_TOKENS=8192,
            validate_chat_messages=validate_messages,
            load_chat_guides=load_guides,
        )
        package = types.SimpleNamespace(llm_service=service)
        namespace = _launch_chat_namespace(
            HTTPException=FakeHTTPException,
            _require_project_access=lambda *_args, **_kwargs: None,
            _resolve_llm_chat_images=(
                lambda _request, body, _workspace:
                ["/authorized/image.png"] if body.get("image_paths") else []
            ),
        )

        def resolve_model(_request, model_id):
            if model_id == "invalid/model":
                raise ValueError("invalid model")
            return {
                "model_id": model_id,
                "response_model_id": model_id,
                "device": "cpu",
                "provider": "local",
                "remote_url": "",
                "api_key": "",
                "local_gguf_path": "",
                "gguf_file_override": "",
                "vision_capable": model_id != "text/model",
            }

        namespace["_resolve_llm_chat_model"] = resolve_model
        base = {
            "workspace": "project",
            "request_id": "33333333-3333-4333-8333-333333333333",
            "model_id": "vision/model",
            "messages": [{"role": "user", "content": "hello"}],
            "guide_ids": [],
            "max_new_tokens": 64,
            "temperature": 0.7,
            "top_p": 0.9,
        }
        invalid_requests = (
            {**base, "messages": "invalid"},
            {**base, "guide_ids": ["invalid"]},
            {**base, "model_id": "invalid/model"},
            {**base, "model_id": "text/model", "image_paths": ["image"]},
            {**base, "max_new_tokens": 0},
            {**base, "temperature": float("nan")},
            {**base, "top_p": 0},
        )
        with mock.patch.dict(sys.modules, {
            "services": package,
            "services.llm_operations": types.SimpleNamespace(
                ChatAdmissionError=llm_operations.ChatAdmissionError,
                ChatRequestMismatchError=llm_operations.ChatRequestMismatchError,
                LlmOperationCapacityError=llm_operations.LlmOperationCapacityError,
                llm_chat_operation_manager=manager,
                run_blocking_shielded=llm_operations.run_blocking_shielded,
            ),
        }):
            for invalid in invalid_requests:
                with self.subTest(invalid=invalid), self.assertRaises(
                    FakeHTTPException,
                ) as raised:
                    asyncio.run(namespace["llm_chat"](FakeRequest(invalid)))
                self.assertEqual(raised.exception.status_code, 400)

        self.assertEqual(admitted, [])

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
        self.assertIn("await run_blocking_shielded(", execute_source)
        self.assertIn("_validate_llm_chat_request", chat_source)
        access_call = next(
            item for item in ast.walk(chat)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id == "_require_project_access"
        )
        permission = next(
            keyword.value for keyword in access_call.keywords
            if keyword.arg == "permission"
        )
        self.assertIsInstance(permission, ast.Constant)
        self.assertEqual(permission.value, "project.generate")
        self.assertLess(
            chat_source.index("_validate_llm_chat_request"),
            chat_source.index("llm_chat_operation_manager.submit"),
        )
        self.assertIn("status_code=429", chat_source)
        self.assertLess(
            chat_source.index("_require_project_access"),
            chat_source.index("_execute_llm_chat"),
        )


if __name__ == "__main__":
    unittest.main()
