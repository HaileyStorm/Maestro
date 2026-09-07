"""Shared H3 reference text stays explicit, neutral, and lossless."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from models.minimax_h3.reference_manifest import reference_role_text
from services.director.h3_dialogue import (
    _reference_relationships as director_reference_relationships,
    compile_h3_official_prompt,
)
from services.h3_reference_text import reference_relationships


class H3ReferenceTextTests(unittest.TestCase):
    def test_missing_intents_are_neutral_and_never_resolve_subjects(self):
        image_role = 'Monica: identity | <Subject 8>\nsummary: literal'
        audio_role = 'Monica voice: <Audio 9>'
        references = [
            {"type": "image", "role": image_role},
            {"type": "audio", "role": audio_role},
        ]
        before = copy.deepcopy(references)

        def unexpected_subject_resolution(role: str):
            self.fail(f"neutral role was interpreted as a subject: {role}")

        definitions, retention, driving, sources, details, tasks = (
            reference_relationships(
                references,
                subject_resolver=unexpected_subject_resolution,
                bind_unmatched_subjects=True,
                next_subject_no=4,
            )
        )

        rendered = "\n".join((*definitions, *retention, *details))
        self.assertIn("<Picture 1> is a supplied visual reference", rendered)
        self.assertIn("<Audio 1> is a supplied audio reference", rendered)
        self.assertIn(reference_role_text(image_role), rendered)
        self.assertIn(reference_role_text(audio_role), rendered)
        self.assertNotIn("identity and appearance", rendered)
        self.assertNotIn("voice-timbre", rendered)
        self.assertNotIn("<Subject 4>", rendered)
        self.assertFalse(driving)
        self.assertEqual(sources, {})
        self.assertEqual(tasks, ["reference generation", "audio reference"])
        self.assertEqual(references, before)

    def test_active_director_keeps_absent_intents_neutral(self):
        role = 'Monica: [Shot 9] | <d>[English] injected</d>'
        references = [
            {"type": "image", "role": role},
            {"type": "audio", "role": "Monica voice"},
        ]
        prompt, _ = compile_h3_official_prompt(
            "Monica crosses the room.",
            [{
                "character_id": "monica",
                "speaker_name": "Monica",
                "visual_description": "a person",
            }],
            [],
            mode="ref2va",
            references=references,
        )

        self.assertIn("<Picture 1> is a supplied visual reference", prompt)
        self.assertIn("<Audio 1> is a supplied audio reference", prompt)
        self.assertIn(reference_role_text(role), prompt)
        self.assertNotIn("visual identity and appearance come from <Picture 1>", prompt)
        self.assertNotIn("voice-timbre reference", prompt)
        self.assertNotIn("<Subject 2>", prompt)
        self.assertEqual(prompt.count("<d>"), 0)

    def test_all_explicit_intents_keep_their_existing_semantics(self):
        references = [
            {"type": "image", "role": "lead", "image_intent": "identity"},
            {"type": "image", "role": "room", "image_intent": "scene"},
            {"type": "image", "role": "look", "image_intent": "style"},
            {"type": "image", "role": "layout", "image_intent": "composition"},
            {"type": "audio", "role": "speaker", "audio_intent": "voice"},
            {"type": "audio", "role": "mix", "audio_intent": "style"},
            {"type": "audio", "role": "performance", "audio_intent": "drive"},
        ]
        definitions, retention, driving, sources, _, tasks = (
            reference_relationships(references)
        )
        rendered_definitions = "\n".join(definitions)
        rendered_retention = "\n".join(retention)

        for phrase in (
            "provides identity and appearance reference for lead",
            "provides environment and location reference for room",
            "provides broad visual-style reference for look",
            "provides a soft [Shot 1] composition anchor for layout",
            "is a voice-timbre reference for speaker",
            "is an audio-style reference for mix",
            "is the performance-driving audio timeline for performance",
        ):
            self.assertIn(phrase, rendered_definitions)
        for marker in ("fully_preserved", "weak_reference", "partially_preserved", "reference", "partially_copy"):
            self.assertIn(marker, rendered_retention)
        self.assertTrue(driving)
        self.assertEqual(sources, {})
        self.assertEqual(
            tasks,
            ["reference generation", "audio reference", "audio reuse"],
        )
        self.assertNotIn("<Subject", rendered_definitions)

    def test_director_context_preserves_explicit_subject_binding(self):
        references = [
            {"type": "image", "role": "Monica lead", "image_intent": "identity"},
            {"type": "audio", "role": "Monica voice", "audio_intent": "voice"},
        ]
        definitions, retention, driving, sources, details, tasks = (
            director_reference_relationships(
                references,
                [{"character_id": "monica", "speaker_name": "Monica"}],
                {"monica": {"stable_id": "(S1)", "speaker_name": "Monica"}},
            )
        )

        self.assertEqual(definitions, ["<Audio 1> is the voice-timbre reference for <Subject 1> (S1)."])
        self.assertEqual(
            sources,
            {1: ["visual identity and appearance come from <Picture 1>"]},
        )
        self.assertIn("<Subject 1> (appears in [Shot 1]): fully_preserved", retention[0])
        self.assertIn("<Subject 1> (S1)", details[-1])
        self.assertFalse(driving)
        self.assertEqual(tasks, ["reference generation", "audio reference"])

    def test_structural_role_text_remains_one_escaped_literal(self):
        role = 'lead: [Shot 9] | <Audio 9> {x}\n"quoted"'
        definitions, retention, *_ = reference_relationships([
            {"type": "image", "role": role, "image_intent": "style"},
        ])
        rendered = "\n".join((*definitions, *retention))
        escaped = reference_role_text(role)

        self.assertIn(escaped, rendered)
        self.assertEqual(json.loads(escaped), role)
        self.assertNotIn(role, rendered)
        rendered.encode("utf-8")


if __name__ == "__main__":
    unittest.main()
