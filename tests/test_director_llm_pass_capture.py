"""Continuum Director LLM pass helpers.

Locks leftover 1.9.0 `_capture_llm_pass` probes onto Continuum
`_begin_pipeline_llm_pass` and `_finish_pipeline_llm_pass`. Do not
invent leftover singleton prompt/response logs or restore that helper.
"""
from __future__ import annotations

import os
import sys
import unittest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import director_pipeline as pipeline  # noqa: E402
from services.llm_cancellation import LlmRequestCancelled  # noqa: E402


_PIPELINE_PATH = os.path.join(_APP, "services", "director_pipeline.py")
_LEFTOVER_HELPERS = (
    "_capture_llm_pass",
)
_LEFTOVER_RECONNECTS = (
    "_llm_passes",
    "_last_system_prompt",
    "_last_user_prompt",
    "_last_thinking_text",
    "thinking_text",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _pass_helpers_source() -> str:
    source = _read(_PIPELINE_PATH)
    start = source.index("def _begin_pipeline_llm_pass(")
    end = source.index("\ndef _director_pipeline_cancel_checkpoint(", start)
    return source[start:end]


class TestContinuumDirectorLlmPassCaptureGates(unittest.TestCase):
    def setUp(self):
        self._original_pipelines = pipeline._pipelines
        self._original_tokens = pipeline._pipeline_llm_tokens
        self._original_handles = pipeline._pipeline_llm_cancel_handles
        pipeline._pipelines = {}
        pipeline._pipeline_llm_tokens = {}
        pipeline._pipeline_llm_cancel_handles = {}

    def tearDown(self):
        pipeline._pipelines = self._original_pipelines
        pipeline._pipeline_llm_tokens = self._original_tokens
        pipeline._pipeline_llm_cancel_handles = self._original_handles

    def test_pipeline_does_not_restore_leftover_llm_pass_capture(self):
        source = _read(_PIPELINE_PATH)

        # Leftover 1.9.0 copied llm_service singleton prompts into
        # pipeline["_llm_passes"] after each planning pass. Continuum
        # dropped that reconnect and keeps process-memory progress only.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(pipeline, name))
                self.assertNotIn(f"def {name}(", source)

    def test_continuum_helpers_keep_progress_not_leftover_pass_log(self):
        source = _read(_PIPELINE_PATH)
        helpers = _pass_helpers_source()
        self.assertIn("def _begin_pipeline_llm_pass(", source)
        self.assertIn("def _finish_pipeline_llm_pass(", source)
        self.assertIn("process-memory-only LLM progress stream", helpers)
        for leftover in _LEFTOVER_HELPERS + _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=leftover):
                self.assertNotIn(leftover, helpers)

    def test_begin_pass_publishes_progress_without_leftover_capture(self):
        record = {"id": "pid-lock", "status": "planning"}
        pipeline._pipelines["pid-lock"] = record
        token, _handle, publish = pipeline._begin_pipeline_llm_pass(
            "pid-lock",
            phase="planning",
            pass_name="third_pass_polish",
            attempt_limit=1,
        )
        publish({
            "phase": "generating",
            "text": "visible tail",
            "attempt": 1,
            "generated_tokens_approx": 3,
            "elapsed_seconds": 0.2,
            "live_tps": 10.0,
            "done": False,
        })
        progress = record["llm_progress"]
        self.assertEqual(progress["pass"], "third_pass_polish")
        self.assertEqual(progress["partial_text"], "visible tail")
        self.assertNotIn("_llm_passes", record)
        self.assertNotIn("system_prompt", progress)
        self.assertNotIn("user_prompt", progress)
        self.assertNotIn("thinking_text", progress)
        self.assertFalse(hasattr(pipeline, "_capture_llm_pass"))
        pipeline._finish_pipeline_llm_pass("pid-lock", token)
        self.assertTrue(record["llm_progress"]["done"])
        self.assertEqual(record["llm_progress"]["partial_text"], "")
        self.assertNotIn("_llm_passes", record)

    def test_finish_does_not_stamp_leftover_pass_log(self):
        record = {"id": "pid-finish", "status": "running"}
        pipeline._pipelines["pid-finish"] = record
        token, _handle, _publish = pipeline._begin_pipeline_llm_pass(
            "pid-finish",
            phase="planning",
            pass_name="generate_1",
            attempt_limit=1,
        )
        pipeline._finish_pipeline_llm_pass("pid-finish", token)
        self.assertEqual(record["llm_progress"]["activity"], "complete")
        self.assertNotIn("_llm_passes", record)
        self.assertFalse(hasattr(pipeline, "_capture_llm_pass"))

    def test_begin_fail_closed_without_leftover_capture(self):
        with self.assertRaises(LlmRequestCancelled):
            pipeline._begin_pipeline_llm_pass(
                "missing-pid",
                phase="planning",
                pass_name="streaming_1",
                attempt_limit=1,
            )
        self.assertEqual(pipeline._pipelines, {})
        self.assertFalse(hasattr(pipeline, "_capture_llm_pass"))


if __name__ == "__main__":
    unittest.main()
