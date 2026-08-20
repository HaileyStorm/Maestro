"""Continuum H3 dialogue-contract helpers.

Locks leftover 1.9.0 `_h3_timed_silence_contract_satisfied` probes onto
Continuum `_h3_dialogue_contract_satisfied` and
`_build_h3_timed_silence_clause`. Do not invent leftover interval/mouth
regex checkers, and do not restore that helper.
"""
from __future__ import annotations

import os
import sys
import unittest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import llm_service  # noqa: E402


_LLM_SERVICE_PATH = os.path.join(_APP, "services", "llm_service.py")
_LEFTOVER_HELPERS = (
    "_h3_timed_silence_contract_satisfied",
)
_LEFTOVER_RECONNECTS = (
    "has_opening_interval",
    "has_closed_mouths",
    "has_remaining_interval",
    "explicit non-vocal time allocation",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _hook_source() -> str:
    source = _read(_LLM_SERVICE_PATH)
    start = source.index("def _h3_dialogue_contract_satisfied(")
    end = source.index("\ndef _h3_visual_category_count(", start)
    return source[start:end]


class TestContinuumLlmH3TimedSilenceContractGates(unittest.TestCase):
    def test_llm_service_does_not_restore_leftover_timed_silence_checker(self):
        source = _read(_LLM_SERVICE_PATH)

        # Leftover 1.9.0 treated any opening-interval / closed-mouth /
        # remaining-interval regex stamp as a complete speech contract
        # through `_h3_timed_silence_contract_satisfied`. Continuum dropped
        # that checker.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(llm_service, name))
                self.assertNotIn(f"def {name}(", source)

    def test_continuum_helpers_keep_dialogue_contract_not_leftover_intervals(self):
        source = _read(_LLM_SERVICE_PATH)
        hook = _hook_source()
        self.assertIn("def _h3_dialogue_contract_satisfied(", source)
        self.assertIn("def _build_h3_timed_silence_clause(", source)
        self.assertIn("def _h3_dialogue_schedule(", source)
        self.assertNotIn("def _h3_timed_silence_contract_satisfied(", source)
        for leftover in _LEFTOVER_HELPERS + _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=leftover):
                self.assertNotIn(leftover, hook)

    def test_interval_prose_fail_closed_without_leftover_silence_checker(self):
        # Leftover `_h3_timed_silence_contract_satisfied` accepted this
        # interval/mouth-closed stamp without tagged dialogue. Continuum
        # still requires the verbatim tagged line.
        prompt = 'A woman says, "Hello."'
        leftover_intervals = (
            "From 0 to 2 seconds mouths stay closed with no voices. "
            "From 6 to 8 seconds the room stays quiet."
        )
        self.assertFalse(
            llm_service._h3_dialogue_contract_satisfied(prompt, leftover_intervals)
        )
        self.assertTrue(
            llm_service._h3_dialogue_contract_satisfied(
                prompt,
                "Woman (S1): <d>[English] Hello.</d>",
            )
        )
        self.assertFalse(
            hasattr(llm_service, "_h3_timed_silence_contract_satisfied")
        )

    def test_timed_clause_stays_requirement_text_not_leftover_result_checker(self):
        # Continuum still describes the nonverbal windows on the
        # requirement. That is not a leftover result checker, and silent
        # shots stay empty instead of inventing interval stamps.
        silent = llm_service._build_h3_timed_silence_clause(
            "A closed door.",
            4.0,
        )
        self.assertEqual(silent, "")
        self.assertNotIn("From 0", silent)

        spoken = llm_service._build_h3_timed_silence_clause(
            'A woman says, "Hello."',
            8.0,
        )
        self.assertIn("From 0.00 to", spoken)
        self.assertIn("every mouth stays completely closed", spoken)
        self.assertNotIn("has_opening_interval", spoken)
        self.assertFalse(
            hasattr(llm_service, "_h3_timed_silence_contract_satisfied")
        )

    def test_enhance_errors_fail_closed_without_leftover_interval_gate(self):
        # Leftover enhance retries called
        # `_h3_timed_silence_contract_satisfied` on generated output.
        # Continuum's contract errors stay on official-record validation
        # plus the dialogue contract, not that leftover regex gate.
        source = _read(_LLM_SERVICE_PATH)
        start = source.index("def _h3_enhance_contract_errors(")
        end = source.index("\ndef _finalize_h3_enhance_output(", start)
        hook = source[start:end]
        self.assertIn("validate_h3_prompt_contract", hook)
        self.assertNotIn("_h3_timed_silence_contract_satisfied", hook)
        prompt = 'A woman says, "Hello."'
        leftover_intervals = (
            "From 0 to 2 seconds mouths stay closed with no voices. "
            "From 6 to 8 seconds the room stays quiet."
        )
        self.assertFalse(
            llm_service._h3_dialogue_contract_satisfied(prompt, leftover_intervals)
        )


if __name__ == "__main__":
    unittest.main()
