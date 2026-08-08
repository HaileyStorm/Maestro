"""Model-free regressions for MiniMax H3 long-duration prompt contracts."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import llm_service  # noqa: E402
from services.director import prompt_polish  # noqa: E402


_FORBIDDEN_EXECUTION_TERMS = (
    "window", "segment", "chunk", "split", "stitch", "checkpoint",
    "native", "overlap", "model limit",
)

_BASE_60S = """integrated_multimodal_description: [Shot 1] At 0.00 seconds, a singer in a red coat (S1) faces camera and says: <d>[English] Keep every word exactly.</d> [Shot 2] At 00:30.000, cut to the same singer crossing the stage. At 60.00 seconds, she stops.

overall_soundscape: Audience room tone and footsteps.

non_diegetic_music: A restrained piano theme continues throughout."""

_REF_30S = """subject_definitions: <Subject 1> is the singer from <Picture 1>.
summary: [reference generation] Preserve <Subject 1>.
retention_analysis: Fully preserve <Subject 1>'s identity and red coat.
detailed_description: [Shot 1] At 0.00 seconds, <Subject 1> (S1) says: <d>[English] Keep every word exactly.</d> [Shot 2] At 00:15.000, cut to <Subject 1> at the piano. At 30.00 seconds, the performance ends.
overall_soundscape: Quiet room tone and piano-key sounds.
non_diegetic_music: N/A"""


class TestH3LongDurationGuides(unittest.TestCase):
    def _assert_no_execution_terms(self, text: str) -> None:
        lowered = text.lower()
        for term in _FORBIDDEN_EXECUTION_TERMS:
            with self.subTest(term=term):
                self.assertNotIn(term, lowered)

    def test_chat_base_and_ref_guides_accept_30s_and_60s_global_timelines(self):
        selected, combined = llm_service.load_chat_guides([
            "minimax_h3", "minimax_h3_ref2va",
        ])
        compact = " ".join(combined.split())

        self.assertEqual(selected, ["minimax_h3", "minimax_h3_ref2va"])
        self.assertIn("durations longer than 15", compact)
        self.assertIn("30 or 60 seconds", compact)
        self.assertIn("one coherent global timeline", compact)
        self.assertIn("literal dialogue", compact)
        self.assertIn("not a metronome", compact)
        self.assertIn("Approximate, irregular boundaries are valid", compact)
        self.assertIn("never permits changing a timestamp the user supplied", compact)
        self.assertNotIn("automatic long-video segment", compact)
        self._assert_no_execution_terms(combined)

    def test_all_h3_guides_prefer_natural_unequal_inferred_timing(self):
        guide_paths = (
            os.path.join(
                _APP_DIR, "services", "llm_guides", "enhance",
                "minimax_h3_video.md",
            ),
            os.path.join(
                _APP_DIR, "services", "llm_guides", "enhance",
                "minimax_h3_ref2va_video.md",
            ),
            os.path.join(
                _APP_DIR, "services", "llm_guides", "dialect",
                "minimax_h3_video.md",
            ),
        )

        for path in guide_paths:
            with self.subTest(path=path):
                with open(path, "r", encoding="utf-8") as handle:
                    guide = handle.read()
                lowered = " ".join(guide.lower().split())
                self.assertIn("chronological narrative anchors", lowered)
                self.assertIn("not a metronome", lowered)
                self.assertIn("approximate, irregular boundaries are valid", lowered)
                self.assertIn("action, dialogue, reactions, and visual rhythm", lowered)
                self.assertIn("exact 5, 10, 15, or 30-second intervals", lowered)
                self.assertRegex(
                    lowered,
                    r"(?:never permits changing|never alter) a (?:timestamp|user-supplied timestamp)",
                )
                self._assert_no_execution_terms(guide)

    def test_studio_base_and_ref_suppress_legacy_paragraph_instructions(self):
        cases = (
            ("minimax_h3_fl2va", 60, 4, _BASE_60S),
            ("minimax_h3_ref2va", 30, 2, _REF_30S),
        )
        for model_type, duration, count, source in cases:
            with self.subTest(model_type=model_type, duration=duration):
                captured = {}

                def fake_generate(**kwargs):
                    captured.update(kwargs)
                    return source

                guide_name = (
                    "minimax_h3_ref2va_video.md"
                    if "ref2va" in model_type else "minimax_h3_video.md"
                )
                guide_path = os.path.join(
                    _APP_DIR, "services", "llm_guides", "enhance", guide_name,
                )
                with open(guide_path, "r", encoding="utf-8") as handle:
                    guide = handle.read()
                fake_guides = SimpleNamespace(
                    get_enhance_guide=lambda *_args, **_kwargs: guide,
                )
                with (
                    patch.dict(sys.modules, {"services.enhance_guides": fake_guides}),
                    patch.object(llm_service, "generate", side_effect=fake_generate),
                ):
                    result = llm_service.enhance_prompt(
                            source,
                            mode="video",
                            model_type=model_type,
                            duration_seconds=duration,
                            window_count=count,
                            window_size_seconds=15,
                        )

                self.assertEqual(result, source)
                self.assertIn(f"Duration: {duration} seconds", captured["prompt"])
                self.assertIn("one coherent global timeline", captured["prompt"])
                self.assertNotIn("Write EXACTLY", captured["prompt"])
                self.assertNotIn("paragraph", captured["prompt"].lower())
                self.assertIn("LONG-DURATION H3 CONTRACT", captured["system_prompt"])
                compact_system = " ".join(captured["system_prompt"].split())
                self.assertIn("not a metronome", compact_system)
                self.assertIn("irregular boundaries", compact_system)
                self._assert_no_execution_terms(captured["prompt"])
                self._assert_no_execution_terms(captured["system_prompt"])
                expected_minimum = 1440 if duration == 30 else 2400
                self.assertGreaterEqual(captured["max_new_tokens"], expected_minimum)

    def test_director_full_and_light_guides_use_longest_h3_prefix(self):
        base_full = prompt_polish.get_video_guide("minimax_h3_fl2va", "full")
        base_light = prompt_polish.get_video_guide("minimax_h3_fl2va", "light")
        # The exact Ref2VA model ID matches both H3 prefixes; the longer
        # ``minimax_h3_ref2va`` key must win over the base ``minimax_h3`` key.
        ref_full = prompt_polish.get_video_guide("minimax_h3_ref2va", "full")
        ref_light = prompt_polish.get_video_guide("minimax_h3_ref2va", "light")

        self.assertIn("integrated_multimodal_description", base_full)
        self.assertIn("MINIMAX H3 CONTEXT-IR RULES", base_light)
        self.assertIn("subject_definitions", ref_full)
        self.assertEqual(ref_light, ref_full)
        for guide in (base_full, base_light, ref_full, ref_light):
            self.assertIn("30", guide)
            self.assertIn("60", guide)
            self.assertIn("global timeline", guide)
            self._assert_no_execution_terms(guide)

    def test_h3_director_third_pass_preserves_context_ir_and_omits_meta_prefix(self):
        calls = []

        def fake_enhance(**kwargs):
            calls.append(kwargs)
            return kwargs["prompt"]

        plans = [{
            "duration_sec": 60,
            "video_prompt": _BASE_60S,
            "image_prompt": "",
            "window_prompts": [],
        }]
        with patch.object(llm_service, "enhance_prompt", side_effect=fake_enhance):
            prompt_polish.polish_prompts_third_pass(
                plans, "minimax_h3_fl2va", "flux", characters=[],
            )

        self.assertEqual(calls, [])
        self.assertEqual(plans[0]["video_prompt"], _BASE_60S)
        self.assertNotIn("[Window", plans[0]["video_prompt"])

    def test_h3_director_legacy_multi_prompt_shape_never_adds_meta_prefix(self):
        calls = []

        def fake_enhance(**kwargs):
            calls.append(kwargs)
            return kwargs["prompt"]

        plans = [{
            "duration_sec": 30,
            "video_prompt": "",
            "image_prompt": "",
            "window_prompts": [_REF_30S, _REF_30S],
        }]
        with patch.object(llm_service, "enhance_prompt", side_effect=fake_enhance):
            prompt_polish.polish_prompts_third_pass(
                plans, "minimax_h3_ref2va", "flux", characters=[],
            )

        self.assertEqual(calls, [])
        self.assertEqual(plans[0]["window_prompts"], [_REF_30S, _REF_30S])

    def test_h3_director_guard_rejects_timing_or_dialogue_drift(self):
        drifts = (
            _BASE_60S.replace("00:30.000", "00:31.000"),
            _BASE_60S.replace("Keep every word exactly.", "Changed words."),
            _BASE_60S.replace("cut to", "move to"),
            _BASE_60S.replace("singer in a red coat", "dancer in a blue coat"),
            _BASE_60S.replace("Audience room tone", "Ocean surf"),
            _BASE_60S.replace("restrained piano", "loud brass"),
            _BASE_60S.replace("[Shot 2]", "[Shot 9]"),
            _BASE_60S + "\n[Window 1]",
        )
        for drifted in drifts:
            with self.subTest(drifted=drifted[:80]):
                with patch.object(llm_service, "generate", return_value=drifted):
                    result = llm_service.enhance_prompt(
                        _BASE_60S,
                        mode="video",
                        model_type="minimax_h3_fl2va",
                        duration_seconds=60,
                        window_count=4,
                        window_size_seconds=15,
                        system_override="H3 Context-IR refinement",
                    )

                self.assertEqual(result, _BASE_60S)

    def test_h3_director_guard_rejects_reference_label_drift(self):
        drifted = _REF_30S.replace("<Subject 1>", "<Subject 2>", 1)
        with patch.object(llm_service, "generate", return_value=drifted):
            result = llm_service.enhance_prompt(
                _REF_30S,
                mode="video",
                model_type="minimax_h3_ref2va",
                duration_seconds=30,
                system_override="H3 Context-IR refinement",
            )

        self.assertEqual(result, _REF_30S)

    def test_h3_director_override_scales_budget_for_complete_duration(self):
        captured = {}

        def fake_generate(**kwargs):
            captured.update(kwargs)
            return _BASE_60S

        with patch.object(llm_service, "generate", side_effect=fake_generate):
            result = llm_service.enhance_prompt(
                _BASE_60S,
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=60,
                window_count=4,
                window_size_seconds=15,
                system_override="H3 Context-IR refinement",
            )

        self.assertEqual(result, _BASE_60S)
        self.assertGreaterEqual(captured["max_new_tokens"], 2400)
        self.assertIn("Duration: 60 seconds", captured["prompt"])
        self.assertIn("one coherent global timeline", captured["prompt"])
        self.assertNotIn("Write EXACTLY", captured["prompt"])

    def test_raw_h3_uses_one_complete_duration_call(self):
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return _BASE_60S

        with patch.object(llm_service, "generate", side_effect=fake_generate):
            result = llm_service.enhance_prompt(
                "First authored line.\nSecond authored line.",
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=30,
                window_count=2,
                window_size_seconds=15,
                raw_enhancer_mode=True,
            )

        self.assertEqual(result, _BASE_60S)
        self.assertEqual(len(calls), 1)
        self.assertIn("Duration: 30 seconds", calls[0]["prompt"])
        self.assertIn("one coherent global timeline", calls[0]["prompt"])
        self.assertNotIn("Write EXACTLY", calls[0]["prompt"])
        self.assertGreaterEqual(calls[0]["max_new_tokens"], 1200)

    def test_raw_60s_h3_uses_duration_scaled_budget(self):
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return _BASE_60S

        with patch.object(llm_service, "generate", side_effect=fake_generate):
            llm_service.enhance_prompt(
                "One complete authored request.",
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=60,
                window_count=4,
                window_size_seconds=15,
                raw_enhancer_mode=True,
            )

        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(calls[0]["max_new_tokens"], 2400)

    def test_raw_ref2va_retains_1200_token_minimum(self):
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return _REF_30S

        with patch.object(llm_service, "generate", side_effect=fake_generate):
            llm_service.enhance_prompt(
                "One complete authored request.",
                mode="video",
                model_type="minimax_h3_ref2va",
                duration_seconds=16,
                raw_enhancer_mode=True,
            )

        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(calls[0]["max_new_tokens"], 1200)

    def test_h3_missing_guide_uses_structured_long_duration_fallback(self):
        captured = {}
        fake_guides = SimpleNamespace(
            get_enhance_guide=lambda *_args, **_kwargs: "",
        )

        def fake_generate(**kwargs):
            captured.update(kwargs)
            return _BASE_60S

        with (
            patch.dict(sys.modules, {"services.enhance_guides": fake_guides}),
            patch.object(llm_service, "generate", side_effect=fake_generate),
        ):
            result = llm_service.enhance_prompt(
                _BASE_60S,
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=60,
            )

        self.assertEqual(result, _BASE_60S)
        self.assertIn("integrated_multimodal_description:", captured["system_prompt"])
        self.assertIn("one coherent global timeline", captured["system_prompt"])
        self.assertNotIn("Keep under 150 words", captured["system_prompt"])
        self._assert_no_execution_terms(captured["system_prompt"])

    def test_h3_guide_system_exit_uses_structured_fallback(self):
        captured = {}

        def fail_lookup(*_args, **_kwargs):
            raise SystemExit("optional runtime unavailable")

        fake_guides = SimpleNamespace(get_enhance_guide=fail_lookup)

        def fake_generate(**kwargs):
            captured.update(kwargs)
            return _BASE_60S

        with (
            patch.dict(sys.modules, {"services.enhance_guides": fake_guides}),
            patch.object(llm_service, "generate", side_effect=fake_generate),
        ):
            result = llm_service.enhance_prompt(
                _BASE_60S,
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=60,
            )

        self.assertEqual(result, _BASE_60S)
        self.assertIn("integrated_multimodal_description:", captured["system_prompt"])
        self.assertNotIn("Keep under 150 words", captured["system_prompt"])

    def test_h3_cleanup_preserves_markup_like_literal_dialogue(self):
        source = _BASE_60S.replace(
            "Keep every word exactly.", "Say **this** exactly.",
        )
        guide_path = os.path.join(
            _APP_DIR, "services", "llm_guides", "enhance",
            "minimax_h3_video.md",
        )
        with open(guide_path, "r", encoding="utf-8") as handle:
            guide = handle.read()
        fake_guides = SimpleNamespace(
            get_enhance_guide=lambda *_args, **_kwargs: guide,
        )
        with (
            patch.dict(sys.modules, {"services.enhance_guides": fake_guides}),
            patch.object(llm_service, "generate", return_value=source),
        ):
            result = llm_service.enhance_prompt(
                source,
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=60,
            )

        self.assertIn("<d>[English] Say **this** exactly.</d>", result)

    def test_h3_director_does_not_strip_literal_dialogue_after_guard(self):
        source = _BASE_60S.replace(
            "Keep every word exactly.", "Say **this** exactly.",
        )
        calls = []

        def fake_enhance(**kwargs):
            calls.append(kwargs)
            return kwargs["prompt"]

        plans = [{
            "duration_sec": 60,
            "video_prompt": source,
            "image_prompt": "",
            "window_prompts": [],
        }]
        with patch.object(llm_service, "enhance_prompt", side_effect=fake_enhance):
            prompt_polish.polish_prompts_third_pass(
                plans, "minimax_h3_fl2va", "flux", characters=[],
            )

        self.assertEqual(calls, [])
        self.assertIn("<d>[English] Say **this** exactly.</d>", plans[0]["video_prompt"])

    def test_h3_director_passthrough_skips_video_lora_discovery(self):
        fake_wgp = SimpleNamespace(
            get_lora_dir=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                SystemExit("H3 passthrough must not inspect video LoRAs")
            ),
            get_model_def=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("H3 passthrough must not inspect video LoRAs")
            ),
        )
        plans = [{
            "duration_sec": 60,
            "video_prompt": _BASE_60S,
            "image_prompt": "",
            "window_prompts": [],
        }]

        with patch.dict(sys.modules, {"wgp": fake_wgp}):
            result = prompt_polish.polish_prompts_third_pass(
                plans,
                "minimax_h3_fl2va",
                "flux",
                video_loras=["unused.safetensors"],
                characters=[],
            )

        self.assertEqual(result[0]["video_prompt"], _BASE_60S)

    def test_ltx_legacy_window_contract_is_byte_for_byte_unchanged(self):
        built = llm_service._build_enhance_user_prompt(
            "A continuous performance.", "video", 60, 4, 15,
        )
        self.assertEqual(
            built,
            "[Duration: 60 seconds, 4 sliding windows of ~15s each, "
            "Write EXACTLY 4 paragraphs (one per window), separated by newlines]"
            "\n\nA continuous performance.",
        )

        calls = []

        def fake_enhance(**kwargs):
            calls.append(kwargs)
            return kwargs["prompt"]

        plans = [{
            "duration_sec": 30,
            "video_prompt": "",
            "image_prompt": "",
            "window_prompts": ["First passage.", "Second passage."],
        }]
        with patch.object(llm_service, "enhance_prompt", side_effect=fake_enhance):
            prompt_polish.polish_prompts_third_pass(
                plans, "ltx2_22B_distilled", "flux", characters=[],
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("[Window 1 of 2 in a continuous scene.", calls[0]["prompt"])
        self.assertIn("[Window 2 of 2 in a continuous scene.", calls[1]["prompt"])
        self.assertFalse(calls[0]["preserve_global_timeline"])

    def test_current_release_docs_describe_the_shipped_h3_contract(self):
        def read(relative_path):
            with open(
                os.path.join(_REPO_DIR, relative_path), "r", encoding="utf-8",
            ) as handle:
                return handle.read()

        readme = read("README.md")
        changelog = read("CHANGELOG.md")
        research = read("docs/development/minimax-h3-fast-runtime-research.md")
        readme_current = readme.split(
            "### v1.6.5 (2026-08-08)", 1,
        )[1].split("### v1.6.1 (2026-08-06)", 1)[0]
        changelog_current = changelog.split(
            "## [1.6.5] - 2026-08-08", 1,
        )[1].split("## [1.6.1] - 2026-08-06", 1)[0]
        readme_h3_overview = readme.split(
            "### 🤖 LLM Chat and prompting", 1,
        )[0]

        for current_notes in (readme_current, changelog_current):
            with self.subTest(document=current_notes[:32]):
                for label in ("Draft", "Fast", "Quality", "High", "Delivery"):
                    self.assertIn(label, current_notes)
                self.assertIn("four-step Turbo", current_notes)
                self.assertIn("eight-step Turbo", current_notes)
                self.assertIn("server", current_notes.lower())
                self.assertNotIn("Full 33B", current_notes)
                self.assertNotIn("Full checkpoint", current_notes)
                self.assertNotIn("window-local storyboard", current_notes)

        self.assertIn("one coherent global prompt", readme_h3_overview)
        self.assertIn("deterministic planner", readme_h3_overview)
        self.assertIn(
            "does not expose First Block Cache",
            " ".join(readme_h3_overview.split()),
        )
        self.assertIn("Historical Snapshot", research)
        self.assertIn("supersedes the product recommendations below", research)


if __name__ == "__main__":
    unittest.main()
