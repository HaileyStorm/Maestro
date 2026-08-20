"""Continuum remote LLM payloads stay llama-local and content-free on failure."""

from __future__ import annotations

import os
import sys
import unittest

import requests


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
_LLM_SERVICE_PATH = os.path.join(_APP, "services", "llm_service.py")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import llm_service


class TestRemotePayloadContract(unittest.TestCase):
    def test_continuum_has_no_openai_field_stripper(self):
        self.assertFalse(hasattr(llm_service, "_finalize_payload"))
        self.assertFalse(hasattr(llm_service, "_OPENAI_CHAT_FIELDS"))

    def test_cache_prompt_is_local_only(self):
        with open(_LLM_SERVICE_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('payload["cache_prompt"] = not bool(image_paths)', source)
        self.assertIn('if _provider == "local":', source)
        self.assertGreater(
            source.count('if _provider == "local":\n        payload["cache_prompt"]'),
            0,
        )
        self.assertNotIn("_finalize_payload", source)


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
