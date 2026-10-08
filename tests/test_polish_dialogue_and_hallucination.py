"""
Regression tests for the polish-layer fixes for the two production
incidents the user reported after the first round of polish fixes:

INCIDENT 1: Dialogue with contraction apostrophes got split into
multiple fake spans by the old _quoted_re. When polish converted
single-to-double quotes the count mismatched and the dialogue revert
silently bailed, so "Cathy" was mangled to "woman in white with
massive breasts" inside quoted dialogue and the corruption shipped.

INCIDENT 2: Polish LLM hallucinated names (e.g. "Blaine") into
narrative prose where the input had only descriptors ("the strong
man in black"). The character_reference_block in the system prompt
listed the name → descriptor mapping even when the input never
mentioned the name; the LLM used the mapping in reverse. Pronouns
("his logic") got replaced with possessive name forms ("the
Blaine's logic").

The fixes ship across four layers: apostrophe-aware quoted-span
helper, input-filtered character block, strengthened anti-hallucination
system-prompt rules, and a post-process hallucination stripper. Dialogue
regressions invoke the real third-pass pipeline with the LLM response
mocked; they do not prove live model or generated-video behavior.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.prompt_polish import (  # noqa: E402
    _strip_hallucinated_names,
    polish_prompts_third_pass,
)


def _polish(before, after, *, field="video_prompt", video_model="ltx2", **kwargs):
    """Run the real third-pass pipeline with only its LLM response mocked."""
    plans = [{field: before}]
    with mock.patch("services.llm_service.enhance_prompt", return_value=after) as enhance:
        result = polish_prompts_third_pass(
            plans, video_model=video_model, image_model="flux",
            polish_video_prompts=field == "video_prompt",
            polish_image_prompts=field == "image_prompt", **kwargs,
        )
    return result[0][field], enhance


class TestAuthoredDialoguePreservation(unittest.TestCase):
    def test_changed_quote_count_keeps_complete_authored_prompt(self):
        # Include names and duplicate articles that later cleanup would alter:
        # falling back must bypass every remaining post-processing step.
        before = "  Cathy waits by the the door. She says 'It's me.' He says 'Don't go.'  "
        outputs = [
            'She says "An altered line."',
            'She says "One." He says "Two." She adds "Three."',
            'She speaks without any quotation marks.',
            'She says "An unfinished line.',
        ]
        characters = [{"display_name": "Cathy", "physical_description": "woman in white"}]
        for field in ("video_prompt", "image_prompt"):
            for after in outputs:
                with self.subTest(field=field, after=after):
                    actual, enhance = _polish(before, after, field=field, characters=characters)
                    self.assertEqual(actual, before)
                    enhance.assert_called_once()

    def test_new_quoted_turns_keep_unquoted_input(self):
        before = "A woman waves silently."
        for field in ("video_prompt", "image_prompt"):
            with self.subTest(field=field):
                actual, _ = _polish(before, 'A woman says "Hello."', field=field)
                self.assertEqual(actual, before)

    def test_equal_count_restores_contractions_and_keeps_polished_prose(self):
        before = "She says 'You know, it's ridiculous.' He replies 'I've missed you.'"
        after = 'She leans forward and says "Changed." He whispers "Altered."'
        actual, enhance = _polish(before, after)
        self.assertEqual(actual, 'She leans forward and says "You know, it\'s ridiculous." He whispers "I\'ve missed you."')
        enhance.assert_called_once()

    def test_unquoted_prose_still_polishes(self):
        actual, enhance = _polish("A woman waves.", "A woman slowly waves by the door.")
        self.assertEqual(actual, "A woman slowly waves by the door.")
        enhance.assert_called_once()

    def test_window_fallback_never_publishes_enhancer_context(self):
        windows = [
            "  She says 'It's me.' He replies 'Don't go.'  ",
            "  He says 'Stay.' She replies 'I will.'  ",
        ]
        for unchanged in (False, True):
            with self.subTest(unchanged=unchanged):
                plans = [{"video_prompt": "unused", "window_prompts": list(windows)}]
                response = (lambda **kw: kw["prompt"]) if unchanged else None
                with mock.patch(
                    "services.llm_service.enhance_prompt", side_effect=response,
                    return_value='She says "An altered line."',
                ) as enhance:
                    actual = polish_prompts_third_pass(
                        plans, video_model="ltx2", image_model="flux",
                        polish_video_prompts=True, polish_image_prompts=False,
                    )
                self.assertEqual(actual[0]["window_prompts"], windows)
                self.assertEqual(enhance.call_count, 2)
                self.assertTrue(enhance.call_args_list[1].kwargs["prompt"].startswith("[Window 2"))

    def test_h3_and_storyboard_remain_exact_without_llm_calls(self):
        cases = [
            ("minimax_h3", "Duration: 6s. She says 'It's me.'"),
            ("ltx2", "Shot 1 (Medium, 8s): She says 'Don't go.'"),
        ]
        for model, before in cases:
            with self.subTest(model=model):
                actual, enhance = _polish(before, 'She says "Altered."', video_model=model)
                self.assertEqual(actual, before)
                enhance.assert_not_called()


class TestStripHallucinatedNames(unittest.TestCase):
    """Incident 2 root cause: polish LLM invented names that weren't
    in input, like "the Blaine does not turn..."."""

    def test_strip_basic_hallucinated_name_with_article(self):
        # The user-reported bug: input has "the strong man in black",
        # polish output has "The Blaine".
        input_text = "The strong man in black does not turn immediately; he lifts one hand."
        output_text = "The Blaine does not turn immediately; he lifts one hand."
        cleaned, hallucinated = _strip_hallucinated_names(
            input_text, output_text,
            name_to_descriptor={},
            fallback_descriptors=["strong man in black"],
        )
        self.assertIn("Blaine", hallucinated)
        self.assertNotIn("Blaine", cleaned)
        self.assertNotIn("the Blaine", cleaned.lower())
        self.assertIn("strong man in black", cleaned)

    def test_strip_possessive_hallucinated_name(self):
        # "his logic" → "the Blaine's logic" in polish output.
        input_text = "her refusal to accept his logic"
        output_text = "her refusal to accept the Blaine's logic"
        cleaned, hallucinated = _strip_hallucinated_names(
            input_text, output_text,
            name_to_descriptor={},
            fallback_descriptors=["strong man in black"],
        )
        self.assertIn("Blaine", hallucinated)
        # Possessive form should become "their" (gender-neutral).
        self.assertIn("their logic", cleaned)
        self.assertNotIn("Blaine", cleaned)

    def test_known_name_in_map_not_stripped(self):
        # If "Blaine" is a real character in our map, it must NOT be
        # stripped — the user genuinely named the character that.
        input_text = "the strong man in black walks in"
        output_text = "Blaine walks in"
        cleaned, hallucinated = _strip_hallucinated_names(
            input_text, output_text,
            name_to_descriptor={"Blaine": "strong man in black"},
            fallback_descriptors=["strong man in black"],
        )
        self.assertEqual(hallucinated, [],
                         "Names in name_to_descriptor must not be flagged")

    def test_name_present_in_input_not_stripped(self):
        # If the input already mentioned "Blaine", polish keeping it
        # is fine — not a hallucination.
        input_text = "Blaine reaches for the gun."
        output_text = "Blaine reaches for the gun, his hand steady."
        cleaned, hallucinated = _strip_hallucinated_names(
            input_text, output_text,
            name_to_descriptor={},
            fallback_descriptors=["strong man in black"],
        )
        self.assertEqual(hallucinated, [])
        self.assertIn("Blaine", cleaned)

    def test_multiple_hallucinated_names(self):
        input_text = "The woman in white speaks to the strong man in black."
        output_text = "Cathy speaks to Blaine, her hand on his arm."
        cleaned, hallucinated = _strip_hallucinated_names(
            input_text, output_text,
            name_to_descriptor={},
            fallback_descriptors=["woman in white"],
        )
        # Both names should be flagged as hallucinations.
        self.assertEqual(set(hallucinated), {"Cathy", "Blaine"})
        # Names should be substituted with the fallback (since both go
        # to the same fallback in this test, output will be redundant —
        # acceptable degradation given we can't disambiguate gender
        # without a richer signal).
        self.assertNotIn("Cathy", cleaned)
        self.assertNotIn("Blaine", cleaned)

    def test_no_fallback_keeps_name_unchanged_for_bare_form(self):
        # When no fallback descriptor is available, bare-name occurrences
        # should be left as-is (substituting with nothing would corrupt
        # the sentence). Possessives still get → "their" because that's
        # always a safe rewrite.
        input_text = "the strong man speaks"
        output_text = "Blaine speaks. Blaine's voice is low."
        cleaned, hallucinated = _strip_hallucinated_names(
            input_text, output_text,
            name_to_descriptor={},
            fallback_descriptors=[],
        )
        self.assertIn("Blaine", hallucinated)
        # Possessive "Blaine's" → "their"
        self.assertIn("their voice", cleaned)
        # Bare "Blaine speaks" stays put because we have no better option.
        self.assertIn("Blaine speaks", cleaned)

    def test_stoplist_protects_common_words(self):
        # The function uses _NAME_HARVEST_STOPLIST. Words like "The",
        # "She", "He", "Today" must NOT be flagged as hallucinations
        # even when they appear capitalized at sentence start.
        input_text = "the strong man in black walks in"
        output_text = "The strong man walks. Today the rain falls."
        cleaned, hallucinated = _strip_hallucinated_names(
            input_text, output_text,
            name_to_descriptor={},
            fallback_descriptors=["strong man in black"],
        )
        # "The" and "Today" must not be flagged.
        self.assertNotIn("The", hallucinated)
        self.assertNotIn("Today", hallucinated)


class TestEndToEndIncidentReproduction(unittest.TestCase):
    """End-to-end reproductions of the user-reported polish output."""

    def test_incident_1_dialogue_mangle_post_apostrophe_fix(self):
        before = (
            "She leans in slightly and speaks, her voice breathy with anticipation, "
            "'You know, it's ridiculous how much you've been on my mind since. since the shower.' "
            "The strong man in black does not turn immediately; he lifts one hand and gently brushes "
            "a piece of hair from his forehead while his jaw tightens, responding with a low, "
            "guarded tone: 'What about me, Cathy? My terrible taste in soap?'"
        )
        after = (
            'She leans in slightly and speaks, her voice breathy with anticipation, '
            '"You know, it\'s ridiculous how much you\'ve been on my mind since. since the shower." '
            'The Blaine does not turn immediately; he lifts one hand to gently brush '
            'a piece of hair from his forehead while his jaw tightens, responding with a low, '
            'guarded tone: "What about me, woman in white with massive breasts? My terrible taste in soap?"'
        )
        actual, enhance = _polish(before, after)
        self.assertIn('"What about me, Cathy? My terrible taste in soap?"', actual)
        self.assertIn("it's ridiculous how much you've been on my mind", actual)
        self.assertNotIn("massive breasts", actual)
        enhance.assert_called_once()

    def test_incident_2_blaine_hallucination_strip(self):
        # End-to-end: "the strong man in black" + "his logic" → polish
        # produces "The Blaine" + "the Blaine's logic" → strip restores.
        input_text = (
            "He brings his right hand up and places it firmly on the woman in white's "
            "shoulder. her refusal to accept his logic"
        )
        output_text = (
            "He raises his right hand and places it firmly on the woman in white's "
            "shoulder, anchoring her. her refusal to accept the Blaine's logic"
        )
        cleaned, hallucinated = _strip_hallucinated_names(
            input_text, output_text,
            name_to_descriptor={},
            fallback_descriptors=["strong man in black"],
        )
        self.assertIn("Blaine", hallucinated)
        self.assertIn("their logic", cleaned)
        self.assertNotIn("Blaine", cleaned)


if __name__ == "__main__":
    unittest.main(verbosity=2)
