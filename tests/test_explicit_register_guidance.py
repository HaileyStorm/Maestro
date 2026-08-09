"""Register-fidelity contracts for request-authorized explicit authoring."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.enhance_guides import get_enhance_guide  # noqa: E402
from services.director import nsfw_guidance  # noqa: E402
from services.director import guide_loader as director_guide_loader  # noqa: E402


REGISTER_HEADING = "REGISTER-FIDELITY APPENDIX"
EXPLICIT_GUIDES = (
    APP / "services/llm_guides/enhance/nsfw_shared.md",
    APP / "services/llm_guides/director/nsfw_screenplay_rules.md",
    APP / "services/llm_guides/director/nsfw_video_rules.md",
    APP / "services/llm_guides/director/nsfw_image_rules.md",
)
VIDEO_SHARED = APP / "services/llm_guides/enhance/video_shared.md"
TEN_EROS = APP / "services/llm_guides/prompt_enhancer/10eros_video_rules.md"
H3_BASE = APP / "services/llm_guides/enhance/minimax_h3_video.md"
H3_REF = APP / "services/llm_guides/enhance/minimax_h3_ref2va_video.md"


class ExplicitRegisterGuideTests(unittest.TestCase):
    def test_every_composed_explicit_guide_carries_the_register_appendix(self):
        required = (
            "exact terminology",
            "raw, vulgar, colloquial, graphic, or non-clinical language",
            "clinical anatomy",
            "bland abstractions",
            "vague catch-alls",
            "polite euphemisms",
            "preserve the clinical register",
            "same register fidelity to graphic violence",
            "required specificity concretizes only the mechanics entailed",
            "do not infer or invent a separate act",
            "dialogue, vocalization, or reaction",
            'if the user writes "cock," keep "cock"',
            'if the user writes "penis," keep "penis"',
            'if the user writes "the knife cuts through his cheek,"',
        )
        for path in EXPLICIT_GUIDES:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                lowered = text.casefold()
                self.assertEqual(text.count(REGISTER_HEADING), 1)
                self.assertLess(
                    text.index("THIS REQUEST IS EXPLICIT-AUTHORIZED"),
                    text.index(REGISTER_HEADING),
                )
                sexual_heading = (
                    "SEXUAL REQUESTS"
                    if "SEXUAL REQUESTS" in text
                    else "FOR SEXUAL MATERIAL"
                )
                self.assertLess(
                    text.index(REGISTER_HEADING), text.index(sexual_heading),
                )
                for phrase in required:
                    self.assertIn(phrase, lowered)

    def test_no_component_clinicalizes_register_or_invents_filler(self):
        explicit_texts = [
            path.read_text(encoding="utf-8").casefold()
            for path in EXPLICIT_GUIDES
        ]
        for text in explicit_texts[:3]:
            self.assertIn(
                "interaction or sexual action alone does not imply speech",
                text,
            )
            self.assertIn(
                "only when the user requests them or the request clearly "
                "indicates actual speech or vocalization",
                text,
            )
        joined = "\n".join(explicit_texts)
        for conflict in (
            "clearly implied by the authored sexual action",
            "immediate physical/vocal reaction",
            "immediate physical and spoken reaction",
            "resulting visible injury or damage",
        ):
            self.assertNotIn(conflict, joined)

        shared = VIDEO_SHARED.read_text(encoding="utf-8").casefold()
        self.assertIn("never authorizes clinicalizing", shared)
        self.assertIn("interaction alone does not imply speech", shared)
        self.assertIn("ceilings, never targets", shared)
        self.assertIn("do not fill unused duration with invented dialogue", shared)
        self.assertNotIn("keep something happening at every moment", shared)
        self.assertNotIn("give it enough dialogue", shared)

        ten_eros = TEN_EROS.read_text(encoding="utf-8").casefold()
        self.assertIn("preserve the user's exact terminology", ten_eros)
        self.assertIn("explicitly clinical language stays clinical", ten_eros)
        self.assertIn("never sanitize, clinicalize, euphemize", ten_eros)
        self.assertIn("interaction alone does not imply speech", ten_eros)
        self.assertNotIn("use full, accurate anatomical terminology", ten_eros)

    def test_composition_modes_include_the_appendix_only_when_authorized(self):
        base = "ROLE LINE\n\nROUTE-SPECIFIC RULES"
        for mode, expected_count in (
            ("enhance", 1),
            ("screenplay", 1),
            ("video", 1),
            ("image", 1),
            ("both", 2),
            ("director", 3),
        ):
            with self.subTest(mode=mode):
                self.assertEqual(
                    nsfw_guidance.inject_content_guidance(base, False, mode),
                    base,
                )
                composed = nsfw_guidance.inject_content_guidance(
                    base, True, mode,
                )
                self.assertEqual(composed.count(REGISTER_HEADING), expected_count)
                if mode == "enhance":
                    self.assertLess(
                        composed.index("ROUTE-SPECIFIC RULES"),
                        composed.index(REGISTER_HEADING),
                    )
                else:
                    self.assertLess(
                        composed.index(REGISTER_HEADING),
                        composed.index("ROUTE-SPECIFIC RULES"),
                    )

    def test_real_h3_guide_routes_compose_with_register_appendix(self):
        model_definitions = {
            "minimax_h3_video": {"model_type": "minimax_h3_video"},
            "minimax_h3_ref2va_video": {
                "model_type": "minimax_h3_ref2va_video",
            },
        }
        routes = (
            ("minimax_h3_video", H3_BASE, "interaction alone does not imply speech"),
            (
                "minimax_h3_ref2va_video",
                H3_REF,
                "do not invent an action or reaction merely to fill unused duration",
            ),
        )
        for model_type, path, route_marker in routes:
            with self.subTest(model_type=model_type):
                prior_wgp_present = "wgp" in sys.modules
                prior_wgp = sys.modules.get("wgp")
                enhance_guides_module = sys.modules["services.enhance_guides"]
                wgp_stub = types.ModuleType("wgp")
                wgp_stub.get_model_def = (
                    lambda requested, definitions=model_definitions:
                    definitions.get(requested)
                )
                try:
                    with mock.patch.dict(sys.modules, {"wgp": wgp_stub}):
                        self.assertIs(sys.modules["wgp"], wgp_stub)
                        routed = get_enhance_guide(
                            model_type, "video", has_images=False,
                        )
                finally:
                    self.assertEqual("wgp" in sys.modules, prior_wgp_present)
                    if prior_wgp_present:
                        self.assertIs(sys.modules.get("wgp"), prior_wgp)
                    self.assertIs(
                        sys.modules["services.enhance_guides"],
                        enhance_guides_module,
                    )
                actual = path.read_text(encoding="utf-8").strip()
                self.assertEqual(routed, actual)
                lowered = routed.casefold()
                normalized = " ".join(lowered.split())
                self.assertIn("[shot n] [starts-ends] shot_name:", lowered)
                self.assertIn("dialogue_and_vocalizations:", lowered)
                self.assertIn("invalid -> valid correction", lowered)
                self.assertIn("preserve the user's exact register", normalized)
                self.assertIn(
                    "never sanitize, clinicalize, euphemize, intensify, or escalate",
                    normalized,
                )
                self.assertIn(route_marker, normalized)
                if model_type == "minimax_h3_ref2va_video":
                    self.assertIn(
                        "for a 17.80-second request using two identity pictures, "
                        "a requested quiet laugh, and exact dialogue:",
                        normalized,
                    )
                    self.assertIn(
                        "<subject 1> gives one quiet laugh.", lowered,
                    )

                composed = nsfw_guidance.inject_content_guidance(
                    routed, True, "enhance",
                )
                self.assertTrue(composed.startswith(routed))
                self.assertIn(REGISTER_HEADING, composed)
                self.assertLess(
                    composed.index("Invalid -> valid correction"),
                    composed.index(REGISTER_HEADING),
                )

    def test_real_director_h3_and_shared_video_stacks_retain_precedence(self):
        director_h3 = director_guide_loader.load_guide(
            "minimax_h3_shot_breakdown.md",
        )
        composed_director = nsfw_guidance.inject_content_guidance(
            director_h3, True, "video",
        )
        self.assertIn("VIDEO PROMPT (video_prompt) — for MiniMax H3:", composed_director)
        self.assertIn("MiniMax H3 generates synchronized picture", composed_director)
        self.assertIn("[Shot N] [STARTs-ENDs] shot_name:", composed_director)
        self.assertIn(REGISTER_HEADING, composed_director)
        normalized_director = " ".join(director_h3.casefold().split())
        self.assertIn(
            "after the last spoken line, keep mouths closed and extend or hold "
            "only the requested state and atmosphere. use reactions or motion "
            "only when requested or necessarily entailed by the authored event; "
            "never invent them as filler.",
            normalized_director,
        )
        self.assertNotIn(
            "use visible reactions or motion for remaining time",
            normalized_director,
        )
        self.assertLess(
            composed_director.index("VIDEO PROMPT (video_prompt) — for MiniMax H3:"),
            composed_director.index(REGISTER_HEADING),
        )
        self.assertLess(
            composed_director.index(REGISTER_HEADING),
            composed_director.index("MiniMax H3 generates synchronized picture"),
        )

        shared = VIDEO_SHARED.read_text(encoding="utf-8").strip()
        generic_video = nsfw_guidance.inject_content_guidance(
            "GENERIC VIDEO GUIDE", True, "enhance",
        )
        generic_video = f"{generic_video}\n\n{shared}"
        self.assertLess(
            generic_video.index(REGISTER_HEADING),
            generic_video.index("REGISTER AND SCOPE REMAIN USER-AUTHORITATIVE"),
        )
        self.assertIn("ceilings, never targets", generic_video.casefold())

        ten_eros = TEN_EROS.read_text(encoding="utf-8").strip()
        ten_eros_composed = nsfw_guidance.inject_content_guidance(
            ten_eros, True, "enhance",
        )
        self.assertTrue(ten_eros_composed.startswith(ten_eros))
        self.assertLess(
            ten_eros_composed.index("Never sanitize, clinicalize, euphemize"),
            ten_eros_composed.index(REGISTER_HEADING),
        )

    def test_clean_repo_guard_uses_exact_explicit_guidance_provenance(self):
        from scripts import verify_clean_repo

        expected = {
            "app/services/llm_guides/director/nsfw_image_rules.md",
            "app/services/llm_guides/director/nsfw_screenplay_rules.md",
            "app/services/llm_guides/director/nsfw_video_rules.md",
            "app/services/llm_guides/enhance/nsfw_shared.md",
            "tests/test_explicit_register_guidance.py",
        }
        self.assertEqual(
            set(verify_clean_repo.ALLOWED_EXACT_PATTERN_MATCHES), expected,
        )
        for path in expected:
            with self.subTest(path=path):
                self.assertEqual(
                    verify_clean_repo.ALLOWED_EXACT_PATTERN_MATCHES[path],
                    frozenset({"penis"}),
                )
                self.assertFalse(verify_clean_repo._content_scan_allowed(path))
                self.assertTrue(
                    verify_clean_repo._extended_pattern_allowed(path, "penis"),
                )
                self.assertFalse(
                    verify_clean_repo._extended_pattern_allowed(path, "vagina"),
                )
        for unreviewed in (
            "app/services/llm_guides/director/nsfw_unreviewed_rules.md",
            "app/services/llm_guides/enhance/unreviewed.md",
            "tests/test_unreviewed_explicit_prose.py",
        ):
            with self.subTest(unreviewed=unreviewed):
                self.assertFalse(
                    verify_clean_repo._content_scan_allowed(unreviewed),
                )
                self.assertFalse(
                    verify_clean_repo._extended_pattern_allowed(
                        unreviewed, "penis",
                    ),
                )


if __name__ == "__main__":
    unittest.main()
