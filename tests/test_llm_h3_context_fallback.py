"""Continuum H3 dialogue-contract helpers.

Locks leftover 1.9.0 `_build_h3_context_fallback` /
`_build_h3_ref2va_tagged_fallback` probes onto Continuum
`_build_h3_dialogue_requirement` and `_h3_dialogue_contract_satisfied`.
Do not invent leftover official three/six-field fallbacks, and do not
restore those helpers.
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
    "_build_h3_context_fallback",
    "_build_h3_ref2va_tagged_fallback",
    "_enforce_h3_music_request",
    "_has_complete_h3_context_structure",
)
_LEFTOVER_RECONNECTS = (
    "deterministic structured fallback",
    "_H3_CONTEXT_FIELDS",
    "_H3_REF2VA_FIELDS",
    "subject_definitions:",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _hook_source() -> str:
    source = _read(_LLM_SERVICE_PATH)
    start = source.index("def _build_h3_timed_silence_clause(")
    end = source.index("\ndef _h3_visual_category_count(", start)
    return source[start:end]


class TestContinuumLlmH3ContextFallbackGates(unittest.TestCase):
    def test_llm_service_does_not_restore_leftover_official_fallbacks(self):
        source = _read(_LLM_SERVICE_PATH)

        # Leftover 1.9.0 invented official Context-IR / Ref2VA fields
        # through `_build_h3_context_fallback` and
        # `_build_h3_ref2va_tagged_fallback` when the LLM looped, then
        # rewrote music/soundscape onto that invented prompt.
        # Continuum dropped those constructors.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(llm_service, name))
                self.assertNotIn(f"def {name}(", source)

    def test_continuum_helpers_keep_dialogue_contract_not_leftover_fallback(self):
        source = _read(_LLM_SERVICE_PATH)
        hook = _hook_source()
        self.assertIn("def _build_h3_dialogue_requirement(", source)
        self.assertIn("def _h3_dialogue_contract_satisfied(", source)
        self.assertIn("def _build_h3_timed_silence_clause(", source)
        self.assertNotIn("def _build_h3_context_fallback(", source)
        self.assertNotIn("def _build_h3_ref2va_tagged_fallback(", source)
        for leftover in _LEFTOVER_HELPERS + _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=leftover):
                self.assertNotIn(leftover, hook)

    def test_dialogue_requirement_fail_closed_without_leftover_official_fields(self):
        # Leftover fallback stamped integrated_multimodal_description /
        # overall_soundscape / non_diegetic_music even for silent shots.
        # Continuum keeps an empty contract instead of inventing fields.
        requirement = llm_service._build_h3_dialogue_requirement(
            "A closed door.",
            duration_seconds=4.0,
        )
        self.assertEqual(requirement, "")
        for leftover in (
            "integrated_multimodal_description:",
            "overall_soundscape:",
            "non_diegetic_music:",
            "subject_definitions:",
        ):
            self.assertNotIn(leftover, requirement)
        self.assertFalse(hasattr(llm_service, "_build_h3_context_fallback"))

    def test_quoted_speech_stays_a_contract_not_leftover_ref2va_fallback(self):
        prompt = 'A woman says, "Hello."'
        requirement = llm_service._build_h3_dialogue_requirement(
            prompt,
            duration_seconds=8.0,
        )
        self.assertIn("IMMUTABLE H3 DIALOGUE CONTRACT", requirement)
        self.assertIn("<d>[English] Hello.</d>", requirement)
        self.assertNotIn("subject_definitions:", requirement)
        self.assertNotIn("retention_analysis:", requirement)
        self.assertNotIn("non_diegetic_music:", requirement)
        self.assertFalse(hasattr(llm_service, "_build_h3_ref2va_tagged_fallback"))
        self.assertFalse(hasattr(llm_service, "_enforce_h3_music_request"))

    def test_dialogue_contract_fail_closed_without_leftover_structure_checker(self):
        prompt = 'A woman says, "Hello."'
        # Leftover `_has_complete_h3_context_structure` accepted any
        # three-field stamp. Continuum requires the verbatim line.
        self.assertFalse(
            llm_service._h3_dialogue_contract_satisfied(
                prompt,
                "integrated_multimodal_description: [Shot 1] A woman talks.\n"
                "overall_soundscape: Room tone.\n"
                "non_diegetic_music: N/A",
            )
        )
        self.assertTrue(
            llm_service._h3_dialogue_contract_satisfied(
                prompt,
                "Woman (S1): <d>[English] Hello.</d>",
            )
        )
        self.assertFalse(hasattr(llm_service, "_has_complete_h3_context_structure"))
        self.assertFalse(hasattr(llm_service, "_h3_timed_silence_contract_satisfied"))

    def test_visual_anchor_fail_closed_without_leftover_dialogue_inject(self):
        # Leftover `_inject_missing_h3_dialogue` appended speech onto
        # official fields. Continuum's visual hook leaves empty text alone.
        self.assertEqual(
            llm_service._inject_h3_visual_anchor("", "A navy wool jacket."),
            "",
        )
        self.assertFalse(hasattr(llm_service, "_inject_missing_h3_dialogue"))
        self.assertFalse(hasattr(llm_service, "_inject_h3_generated_dialogue"))


if __name__ == "__main__":
    unittest.main()
