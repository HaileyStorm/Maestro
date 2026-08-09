"""Regression tests for content-free BasePlanner JSON diagnostics."""

import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from services.director.planners import base as base_module  # noqa: E402
from services.director.planners.base import BasePlanner  # noqa: E402


class _Planner(BasePlanner):
    skill_type = "test"

    def plan(self, **kwargs):  # pragma: no cover - abstract filler
        raise NotImplementedError


class PlannerOutputPrivacyTests(unittest.TestCase):
    def assert_private_markers_absent(self, captured: str, *markers: str) -> None:
        for marker in markers:
            self.assertNotIn(marker, captured)

    def test_malformed_response_logs_only_reason_codes_and_counts(self):
        response = "PRIVATE_RESPONSE_START {broken PRIVATE_RESPONSE_END"
        output = StringIO()
        with patch.object(base_module, "_HAVE_JSON_REPAIR", False):
            with redirect_stdout(output):
                result = _Planner()._parse_json_response(response)

        self.assertIsNone(result)
        captured = output.getvalue()
        self.assert_private_markers_absent(
            captured,
            "PRIVATE_RESPONSE_START",
            "PRIVATE_RESPONSE_END",
            response,
        )
        self.assertIn("reason=direct_decode_error", captured)
        self.assertIn("reason=all_strategies_exhausted", captured)
        self.assertIn(f"chars={len(response)}", captured)

    def test_regex_salvage_does_not_log_surrounding_or_json_content(self):
        response = (
            "PRIVATE_PROSE_BEFORE\n"
            '[{"scene_goal":"PRIVATE_JSON_VALUE"}]'
            "\nPRIVATE_PROSE_AFTER"
        )
        output = StringIO()
        with redirect_stdout(output):
            result = _Planner()._parse_json_response(response)

        self.assertEqual(result, [{"scene_goal": "PRIVATE_JSON_VALUE"}])
        captured = output.getvalue()
        self.assert_private_markers_absent(
            captured,
            "PRIVATE_PROSE_BEFORE",
            "PRIVATE_JSON_VALUE",
            "PRIVATE_PROSE_AFTER",
        )
        self.assertIn("reason=direct_decode_error", captured)
        self.assertIn("JSON parse OK: 1 items (regex array)", captured)

    def test_malformed_extracted_array_does_not_log_matched_content(self):
        response = (
            "PRIVATE_ARRAY_PROSE_BEFORE "
            '[{"scene_goal":"PRIVATE_BROKEN_ARRAY" trailing}]'
            " PRIVATE_ARRAY_PROSE_AFTER"
        )
        output = StringIO()
        with patch.object(base_module, "_HAVE_JSON_REPAIR", False):
            with redirect_stdout(output):
                result = _Planner()._parse_json_response(response)

        self.assertIsNone(result)
        captured = output.getvalue()
        self.assert_private_markers_absent(
            captured,
            "PRIVATE_ARRAY_PROSE_BEFORE",
            "PRIVATE_BROKEN_ARRAY",
            "PRIVATE_ARRAY_PROSE_AFTER",
        )
        self.assertIn("reason=extracted_array_decode_error", captured)
        self.assertIn("reason=all_strategies_exhausted", captured)

    def test_json_repair_exception_text_is_not_logged(self):
        response = "PRIVATE_REPAIR_INPUT {broken"

        class _FailingRepair:
            @staticmethod
            def loads(_text):
                raise RuntimeError("PRIVATE_EXCEPTION_BODY")

        output = StringIO()
        with patch.object(base_module, "_HAVE_JSON_REPAIR", True):
            with patch.object(base_module, "json_repair", _FailingRepair()):
                with redirect_stdout(output):
                    result = _Planner()._parse_json_response(response)

        self.assertIsNone(result)
        captured = output.getvalue()
        self.assert_private_markers_absent(
            captured,
            "PRIVATE_REPAIR_INPUT",
            "PRIVATE_EXCEPTION_BODY",
        )
        self.assertIn("reason=json_repair_error", captured)

    def test_grammar_rejection_exception_is_redacted_and_retry_is_unchanged(self):
        calls = []

        def generate(**kwargs):
            calls.append(kwargs)
            if "json_schema" in kwargs:
                raise TypeError("PRIVATE_PROVIDER_EXCEPTION")
            return '[{"scene_goal":"PRIVATE_MODEL_VALUE"}]'

        planner = _Planner(llm_generate=generate, llm_generate_streaming=generate)
        output = StringIO()
        with redirect_stdout(output):
            result = planner._call_llm_json(
                "PRIVATE_USER_PROMPT",
                "PRIVATE_SYSTEM_PROMPT",
                thinking_budget=0,
                json_schema={"type": "array", "items": {"type": "object"}},
            )

        self.assertEqual(result, [{"scene_goal": "PRIVATE_MODEL_VALUE"}])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("json_schema", calls[1])
        captured = output.getvalue()
        self.assert_private_markers_absent(
            captured,
            "PRIVATE_PROVIDER_EXCEPTION",
            "PRIVATE_USER_PROMPT",
            "PRIVATE_SYSTEM_PROMPT",
            "PRIVATE_MODEL_VALUE",
        )
        self.assertIn("reason=initial_grammar_rejected", captured)

    def test_retry_grammar_rejection_is_redacted_and_order_is_unchanged(self):
        calls = []

        def generate(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return "PRIVATE_MALFORMED_RESPONSE"
            if "json_schema" in kwargs:
                raise TypeError("PRIVATE_RETRY_PROVIDER_EXCEPTION")
            return '[{"scene_goal":"PRIVATE_RETRY_MODEL_VALUE"}]'

        planner = _Planner(llm_generate=generate, llm_generate_streaming=generate)
        output = StringIO()
        with redirect_stdout(output):
            result = planner._call_llm_json(
                "PRIVATE_RETRY_USER_PROMPT",
                "PRIVATE_RETRY_SYSTEM_PROMPT",
                thinking_budget=4096,
            )

        self.assertEqual(result, [{"scene_goal": "PRIVATE_RETRY_MODEL_VALUE"}])
        self.assertEqual(len(calls), 3)
        self.assertNotIn("json_schema", calls[0])
        self.assertIn("json_schema", calls[1])
        self.assertNotIn("json_schema", calls[2])
        self.assertEqual(calls[2]["thinking_budget"], 2048)
        captured = output.getvalue()
        self.assert_private_markers_absent(
            captured,
            "PRIVATE_MALFORMED_RESPONSE",
            "PRIVATE_RETRY_PROVIDER_EXCEPTION",
            "PRIVATE_RETRY_USER_PROMPT",
            "PRIVATE_RETRY_SYSTEM_PROMPT",
            "PRIVATE_RETRY_MODEL_VALUE",
        )
        self.assertIn("reason=retry_grammar_rejected", captured)


if __name__ == "__main__":
    unittest.main()
