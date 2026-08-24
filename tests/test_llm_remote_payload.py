"""OpenAI-compatible payload translation with content-free failures."""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

import requests


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
_LLM_SERVICE_PATH = os.path.join(_APP, "services", "llm_service.py")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import llm_service


def _unwrapped(function):
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


class _FakeChatResponse:
    def __init__(self):
        self.closed = False

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{
                "message": {"content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    def iter_lines(self, decode_unicode=True):
        del decode_unicode
        yield "data: " + json.dumps({
            "choices": [{"delta": {"content": "ok"}}],
        })
        yield "data: [DONE]"

    def close(self):
        self.closed = True


def _llama_payload():
    return {
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAAA"},
                },
                {"type": "text", "text": "describe"},
            ],
        }],
        "max_tokens": 1024,
        "temperature": 0.7,
        "top_p": 0.9,
        "stop": ["<think>"],
        "seed": 42,
        "cache_prompt": False,
        "top_k": 64,
        "min_p": 0.05,
        "repeat_penalty": 1.1,
        "repeat_last_n": 64,
        "enable_thinking": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


class TestFinalizePayload(unittest.TestCase):
    def setUp(self):
        self.saved = (llm_service._provider, llm_service._model_id)

    def tearDown(self):
        llm_service._provider, llm_service._model_id = self.saved

    def _finalize(self, provider, payload, model_id="remote-model"):
        llm_service._provider = provider
        llm_service._model_id = model_id
        return llm_service._finalize_payload(payload)

    def test_local_payload_is_untouched(self):
        payload = _llama_payload()
        self.assertIs(self._finalize("local", payload), payload)

    def test_remote_adds_model_and_drops_llama_extensions(self):
        output = self._finalize("remote", _llama_payload())
        self.assertEqual(output["model"], "remote-model")
        for field in (
            "cache_prompt",
            "top_k",
            "min_p",
            "repeat_penalty",
            "repeat_last_n",
            "enable_thinking",
            "chat_template_kwargs",
        ):
            with self.subTest(field=field):
                self.assertNotIn(field, output)

    def test_standard_fields_and_multimodal_shape_survive_normalization(self):
        # This proves transport-shape preservation only. Remote/OpenAI model
        # loading remains text-only and advertises no vision capability.
        source = _llama_payload()
        output = self._finalize("remote", source)
        for field in (
            "messages",
            "max_tokens",
            "temperature",
            "top_p",
            "stop",
            "seed",
        ):
            with self.subTest(field=field):
                self.assertEqual(output[field], source[field])
        self.assertEqual(output["messages"][0]["content"][0]["type"], "image_url")

    def test_openai_is_translated_but_anthropic_native_payload_is_not(self):
        payload = _llama_payload()
        openai = self._finalize("openai", payload, "gpt-5")
        self.assertEqual(openai["model"], "gpt-5")
        self.assertNotIn("cache_prompt", openai)
        self.assertIs(self._finalize("anthropic", payload), payload)

    def test_translation_does_not_mutate_the_caller(self):
        payload = _llama_payload()
        self._finalize("remote", payload)
        self.assertIn("cache_prompt", payload)
        self.assertNotIn("model", payload)

    def test_streaming_payload_has_the_same_remote_contract(self):
        payload = _llama_payload()
        payload["stream"] = True
        output = self._finalize("remote", payload)
        self.assertTrue(output["stream"])
        self.assertEqual(output["model"], "remote-model")
        self.assertNotIn("cache_prompt", output)
        self.assertIn("cache_prompt", payload)

    def test_all_openai_compatible_call_paths_finalize_at_transport(self):
        with open(_LLM_SERVICE_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source, filename="llm_service.py")
        expected_calls = {
            "generate_chat": 2,
            "generate": 1,
            "generate_streaming": 1,
        }
        for name, expected in expected_calls.items():
            node = next(
                item for item in tree.body
                if isinstance(item, ast.FunctionDef) and item.name == name
            )
            segment = ast.get_source_segment(source, node) or ""
            with self.subTest(name=name):
                self.assertEqual(segment.count("json=_finalize_payload("), expected)

    def test_all_four_transport_paths_send_normalized_authenticated_payloads(self):
        saved = (
            llm_service._provider,
            llm_service._model_id,
            llm_service._remote_url,
            llm_service._api_key,
            llm_service._vision_available,
        )
        llm_service._provider = "remote"
        llm_service._model_id = "remote-model"
        llm_service._remote_url = "https://llm.example.test"
        llm_service._api_key = "remote-secret"
        llm_service._vision_available = False
        calls = []

        def post(*args, **kwargs):
            calls.append((args, kwargs))
            return _FakeChatResponse()

        sampler_defaults = {
            "sampling_defaults": {
                "top_k": 64,
                "min_p": 0.05,
                "repeat_penalty": 1.1,
                "repeat_last_n": 64,
            },
        }
        try:
            with (
                mock.patch.object(llm_service.requests, "post", side_effect=post),
                mock.patch.object(llm_service, "load_model"),
                mock.patch.object(llm_service, "is_loaded", return_value=True),
                mock.patch.object(llm_service, "_cancel_idle_timer"),
                mock.patch.object(llm_service, "_bind_runtime_request_budget"),
                mock.patch.object(llm_service, "_observe_runtime_output_metrics"),
                mock.patch.object(llm_service, "_record_response_metrics"),
                mock.patch.object(
                    llm_service,
                    "_active_registry_entry",
                    return_value=sampler_defaults,
                ),
            ):
                self.assertEqual(
                    _unwrapped(llm_service.generate_chat)(
                        [{"role": "user", "content": "chat"}],
                        model_id="remote-model",
                        provider="remote",
                        remote_url="https://llm.example.test",
                        api_key="remote-secret",
                        enable_thinking=False,
                    ),
                    "ok",
                )
                self.assertEqual(
                    _unwrapped(llm_service.generate_chat)(
                        [{"role": "user", "content": "streaming chat"}],
                        model_id="remote-model",
                        provider="remote",
                        remote_url="https://llm.example.test",
                        api_key="remote-secret",
                        enable_thinking=False,
                        progress_callback=lambda _event: None,
                    ),
                    "ok",
                )
                self.assertEqual(
                    _unwrapped(llm_service.generate)(
                        "generate",
                        enable_thinking=False,
                    ),
                    "ok",
                )
                self.assertEqual(
                    _unwrapped(llm_service.generate_streaming)(
                        "generate streaming",
                        enable_thinking=False,
                        progress_callback=lambda _event: None,
                    ),
                    "ok",
                )
        finally:
            (
                llm_service._provider,
                llm_service._model_id,
                llm_service._remote_url,
                llm_service._api_key,
                llm_service._vision_available,
            ) = saved

        self.assertEqual(len(calls), 4)
        for _args, kwargs in calls:
            with self.subTest(payload=kwargs["json"]):
                payload = kwargs["json"]
                self.assertEqual(payload["model"], "remote-model")
                self.assertEqual(
                    kwargs["headers"].get("Authorization"),
                    "Bearer remote-secret",
                )
                for field in (
                    "cache_prompt",
                    "top_k",
                    "min_p",
                    "repeat_penalty",
                    "repeat_last_n",
                    "enable_thinking",
                    "chat_template_kwargs",
                ):
                    self.assertNotIn(field, payload)


class TestProviderEndpointsAndLogging(unittest.TestCase):
    def setUp(self):
        self.saved = (
            llm_service._provider,
            llm_service._remote_url,
            llm_service._server_port,
        )

    def tearDown(self):
        (
            llm_service._provider,
            llm_service._remote_url,
            llm_service._server_port,
        ) = self.saved

    def test_openai_blank_url_uses_official_default(self):
        llm_service._provider = "openai"
        llm_service._remote_url = ""
        self.assertEqual(llm_service._server_url(), "https://api.openai.com")
        llm_service._remote_url = "https://gateway.example.test/"
        self.assertEqual(
            llm_service._server_url(),
            "https://gateway.example.test",
        )

    def test_remote_blank_url_retains_loopback_behavior(self):
        llm_service._provider = "remote"
        llm_service._remote_url = ""
        llm_service._server_port = 43210
        self.assertEqual(llm_service._server_url(), "http://127.0.0.1:43210")

    def test_openai_model_discovery_uses_default_when_url_is_blank(self):
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {"data": [{"id": "gpt-test"}]}
        with (
            mock.patch.object(llm_service, "get_model_dir", return_value="/missing"),
            mock.patch.object(llm_service.requests, "get", return_value=response) as get,
        ):
            models = llm_service.get_available_models(
                provider=" OpenAI ",
                remote_url="",
                api_key="openai-secret",
            )
        get.assert_called_once_with(
            "https://api.openai.com/v1/models",
            headers={"Authorization": "Bearer openai-secret"},
            timeout=10,
        )
        discovered = next(model for model in models if model["id"] == "gpt-test")
        self.assertFalse(discovered["vision_capable"])

    def test_anthropic_catalog_is_normalized_and_text_only(self):
        with mock.patch.object(
            llm_service,
            "get_model_dir",
            return_value="/missing",
        ):
            models = llm_service.get_available_models(
                provider=" Anthropic ",
                api_key="anthropic-secret",
            )
        anthropic = [model for model in models if model.get("provider") == "anthropic"]
        self.assertTrue(anthropic)
        self.assertTrue(all(model["vision_capable"] is False for model in anthropic))

    def test_success_output_summary_never_prints_provider_content(self):
        secret = "private provider response text"
        output = io.StringIO()
        with redirect_stdout(output):
            llm_service._log_provider_output_summary("Story-plan output", secret)
        rendered = output.getvalue()
        self.assertNotIn(secret, rendered)
        self.assertIn(f"{len(secret)} chars received", rendered)

        prompt_output = io.StringIO()
        with redirect_stdout(prompt_output):
            llm_service._log_provider_input_summary("Prompt", secret)
        prompt_rendered = prompt_output.getvalue()
        self.assertNotIn(secret, prompt_rendered)
        self.assertIn(f"{len(secret)} chars prepared", prompt_rendered)

        with open(_LLM_SERVICE_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn('print(f"[LLM] Output:\\n{raw}")', source)
        self.assertNotIn('print(f"[LLM] Story plan output:\\n{raw}")', source)
        self.assertNotIn('print(f"[LLM] Raw classification output:\\n{raw}")', source)
        self.assertNotIn('print(f"[LLM] Rewrite output:\\n{rewrite_raw}")', source)
        tree = ast.parse(source, filename="llm_service.py")
        print_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        for call_node in print_calls:
            call = ast.get_source_segment(source, call_node) or ""
            referenced_names = {
                node.id for node in ast.walk(call_node)
                if isinstance(node, ast.Name)
            }
            for sensitive_name in (
                "transcript_text",
                "user_prompt",
                "story_description",
                "rewrite_raw",
                "raw",
            ):
                with self.subTest(call=call, sensitive_name=sensitive_name):
                    self.assertNotIn(sensitive_name, referenced_names)

    def test_remote_load_identity_changes_only_with_url_or_key(self):
        def fake_unload():
            llm_service._provider = "local"
            llm_service._model_id = ""
            llm_service._remote_url = ""
            llm_service._api_key = ""
            llm_service._loaded_model_key = None
            llm_service._process = None

        with (
            mock.patch.multiple(
                llm_service,
                _provider="local",
                _model_id="",
                _remote_url="",
                _api_key="",
                _loaded_model_key=None,
                _process=None,
                _device="",
                _requested_device="",
                _vision_available=False,
            ),
            mock.patch.object(llm_service, "_reset_idle_timer"),
            mock.patch.object(
                llm_service,
                "_unload_inner",
                side_effect=fake_unload,
            ) as unload,
        ):
            llm_service.load_model(
                model_id="remote-model",
                provider="remote",
                remote_url="https://one.example.test/",
                api_key="secret-one",
            )
            first = llm_service._loaded_model_key
            self.assertNotIn("secret-one", repr(first))

            llm_service.load_model(
                model_id="remote-model",
                provider="remote",
                remote_url="https://one.example.test",
                api_key="secret-one",
            )
            self.assertEqual(llm_service._loaded_model_key, first)
            unload.assert_not_called()

            llm_service.load_model(
                model_id="remote-model",
                provider="remote",
                remote_url="https://two.example.test",
                api_key="secret-one",
            )
            second = llm_service._loaded_model_key
            self.assertNotEqual(second, first)
            self.assertEqual(unload.call_count, 1)

            llm_service.load_model(
                model_id="remote-model",
                provider="remote",
                remote_url="https://two.example.test",
                api_key="secret-two",
            )
            third = llm_service._loaded_model_key
            self.assertNotEqual(third, second)
            self.assertEqual(unload.call_count, 2)


class TestRemoteFailureDetail(unittest.TestCase):
    def setUp(self):
        self.saved = (llm_service._provider, llm_service._process)
        llm_service._provider = "remote"
        llm_service._process = None

    def tearDown(self):
        llm_service._provider, llm_service._process = self.saved

    @staticmethod
    def _error_for(status, body):
        response = requests.Response()
        response.status_code = status
        response.url = "https://api.example.com/v1/chat/completions"
        response._content = body
        try:
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            return llm_service._diagnose_llm_request_failure(exc)
        raise AssertionError("expected raise_for_status to fail")

    def test_endpoint_response_body_is_not_leaked(self):
        secret = "Unrecognized request argument: cache_prompt"
        message = str(self._error_for(
            400,
            b'{"error":{"message":"%s"}}' % secret.encode("ascii"),
        ))
        self.assertIn("LLM request failed:", message)
        self.assertNotIn(secret, message)
        self.assertNotIn("cache_prompt", message)
        self.assertNotIn(secret.split(":")[0], message)

    def test_long_response_is_not_copied_into_the_error(self):
        blob = "x" * 5000
        message = str(self._error_for(400, blob.encode("ascii")))
        self.assertIn("LLM request failed:", message)
        self.assertNotIn(blob[:80], message)
        self.assertLess(len(message), 1200)

    def test_connection_error_without_response_is_safe(self):
        error = requests.exceptions.ConnectionError("connection refused")
        message = str(llm_service._diagnose_llm_request_failure(error))
        self.assertIn("connection refused", message)
        self.assertIn("LLM request failed:", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
