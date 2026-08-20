"""Continuum H3 dialogue-contract helpers.

Locks leftover 1.9.0 `_has_complete_h3_ref2va_structure` /
`_enforce_h3_soundscape_silence` probes onto Continuum
`_h3_dialogue_contract_satisfied` and `_build_h3_timed_silence_clause`.
Do not invent leftover official six-field checkers or soundscape rewriters,
and do not restore those helpers.
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
    "_has_complete_h3_ref2va_structure",
    "_enforce_h3_soundscape_silence",
    "_h3_voice_binding_contract_satisfied",
    "_strip_h3_untagged_dialogue_duplicates",
)
_LEFTOVER_RECONNECTS = (
    "the scripted line",
    "intent=VOICE REFERENCE",
    "overall_soundscape",
    "subject_definitions:",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _hook_source() -> str:
    source = _read(_LLM_SERVICE_PATH)
    start = source.index("def _h3_enhance_contract_errors(")
    end = source.index("\ndef _finalize_h3_enhance_output(", start)
    return source[start:end]


class TestContinuumLlmH3OfficialStructureGates(unittest.TestCase):
    def test_llm_service_does_not_restore_leftover_official_checkers(self):
        source = _read(_LLM_SERVICE_PATH)

        # Leftover 1.9.0 treated any ordered six-field Ref2VA stamp as
        # complete through `_has_complete_h3_ref2va_structure`, then rewrote
        # overall_soundscape and Omni voice bindings onto that stamp.
        # Continuum dropped those checkers.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertFalse(hasattr(llm_service, name))
                self.assertNotIn(f"def {name}(", source)

    def test_continuum_helpers_keep_dialogue_contract_not_leftover_structure(self):
        source = _read(_LLM_SERVICE_PATH)
        hook = _hook_source()
        self.assertIn("def _h3_dialogue_contract_satisfied(", source)
        self.assertIn("def _build_h3_timed_silence_clause(", source)
        self.assertIn("def _compile_h3_explicit_dialogue(", source)
        self.assertIn("def _h3_enhance_contract_errors(", source)
        self.assertNotIn("def _has_complete_h3_ref2va_structure(", source)
        self.assertNotIn("def _enforce_h3_soundscape_silence(", source)
        for leftover in _LEFTOVER_HELPERS + _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=leftover):
                self.assertNotIn(leftover, hook)

    def test_six_field_stamp_fail_closed_without_leftover_ref2va_checker(self):
        # Leftover `_has_complete_h3_ref2va_structure` accepted any ordered
        # six-field stamp. Continuum still requires the verbatim tagged line.
        prompt = 'A woman says, "Hello."'
        leftover_stamp = (
            "subject_definitions: <Subject 1> Woman (S1)\n"
            "summary: A woman talks.\n"
            "retention_analysis: Keep the speaker.\n"
            "detailed_description: A woman talks.\n"
            "overall_soundscape: Room tone.\n"
            "non_diegetic_music: N/A"
        )
        self.assertFalse(
            llm_service._h3_dialogue_contract_satisfied(prompt, leftover_stamp)
        )
        self.assertTrue(
            llm_service._h3_dialogue_contract_satisfied(
                prompt,
                "Woman (S1): <d>[English] Hello.</d>",
            )
        )
        self.assertFalse(hasattr(llm_service, "_has_complete_h3_ref2va_structure"))
        self.assertFalse(hasattr(llm_service, "_H3_REF2VA_FIELDS"))

    def test_timed_silence_fail_closed_without_leftover_soundscape_rewrite(self):
        # Leftover `_enforce_h3_soundscape_silence` rewrote overall_soundscape
        # onto an invented official field. Continuum keeps a timed clause and
        # leaves silent shots empty.
        silent = llm_service._build_h3_timed_silence_clause(
            "A closed door.",
            4.0,
        )
        self.assertEqual(silent, "")
        self.assertNotIn("overall_soundscape:", silent)

        spoken = llm_service._build_h3_timed_silence_clause(
            'A woman says, "Hello."',
            8.0,
        )
        self.assertIn("no voices, whispers, grunts", spoken)
        self.assertNotIn("overall_soundscape:", spoken)
        self.assertNotIn("subject_definitions:", spoken)
        self.assertFalse(hasattr(llm_service, "_enforce_h3_soundscape_silence"))

    def test_voice_labels_fail_closed_without_leftover_subject_binding(self):
        # Leftover `_h3_voice_binding_contract_satisfied` required Omni
        # VOICE REFERENCE labels inside subject_definitions. Continuum
        # still needs the tagged line, not that official binding.
        prompt = 'A woman says, "Hello."'
        leftover_binding = (
            "subject_definitions: <Audio 1> Woman (S1) intent=VOICE REFERENCE\n"
            "summary: A woman talks."
        )
        self.assertFalse(
            llm_service._h3_dialogue_contract_satisfied(prompt, leftover_binding)
        )
        self.assertFalse(
            hasattr(llm_service, "_h3_voice_binding_contract_satisfied")
        )

    def test_quoted_speech_compiles_without_leftover_scripted_line_stripper(self):
        # Leftover `_strip_h3_untagged_dialogue_duplicates` replaced leftover
        # quotes with "the scripted line". Continuum compiles the user quote
        # into a tagged line instead of rewriting generated output.
        compiled = llm_service._compile_h3_explicit_dialogue(
            'A woman says, "Hello."'
        )
        self.assertIn("(S1) <d>[English] Hello.</d>", compiled)
        self.assertNotIn("the scripted line", compiled)
        requirement = llm_service._build_h3_dialogue_requirement(
            'A woman says, "Hello."',
            duration_seconds=8.0,
        )
        self.assertIn("Never repeat these words as ordinary quoted text", requirement)
        self.assertNotIn("the scripted line", requirement)
        self.assertFalse(
            hasattr(llm_service, "_strip_h3_untagged_dialogue_duplicates")
        )


if __name__ == "__main__":
    unittest.main()
