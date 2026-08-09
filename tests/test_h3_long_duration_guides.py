"""Model-free regressions for MiniMax H3 long-duration prompt contracts."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import llm_service  # noqa: E402
from services.director.h3_dialogue import (  # noqa: E402
    compile_h3_clip_plans,
    compile_h3_official_prompt,
    validate_h3_context_ir_records,
)
from services.director import prompt_polish  # noqa: E402
from services.director.planners.short_film import (  # noqa: E402
    ShortFilmPlanner,
    _call_h3_audio_format_repair_once,
    _h3_audio_repair_lock_errors,
)


_PROMPT_PARSER_PATH = Path(_APP_DIR) / "shared" / "utils" / "prompt_parser.py"
_PROMPT_PARSER_SPEC = importlib.util.spec_from_file_location(
    "h3_guide_contract_prompt_parser", _PROMPT_PARSER_PATH,
)
if _PROMPT_PARSER_SPEC is None or _PROMPT_PARSER_SPEC.loader is None:
    raise RuntimeError("Could not load prompt_parser.py")
_prompt_parser = importlib.util.module_from_spec(_PROMPT_PARSER_SPEC)
_PROMPT_PARSER_SPEC.loader.exec_module(_prompt_parser)
parse_global_timeline_prompt = _prompt_parser.parse_global_timeline_prompt


_FORBIDDEN_EXECUTION_TERMS = (
    "window", "segment", "chunk", "split", "stitch", "checkpoint",
    "native", "overlap", "model limit",
)

_BASE_60S = """integrated_multimodal_description:
[Shot 1] [0.00s-00:30.000s] shot_name: Opening address | audiovisual_description: A singer in a red coat faces camera. | dialogue_and_vocalizations: The singer (S1) says: <d>[English] Keep every word exactly.</d>
[Shot 2] [00:30.000s-60.00s] shot_name: Stage crossing | audiovisual_description: Cut to the same singer crossing the stage; she stops at the end. | dialogue_and_vocalizations: none

overall_soundscape: Audience room tone and footsteps.

non_diegetic_music: A restrained piano theme continues throughout."""

_REF_30S = """subject_definitions: <Subject 1> is the singer from <Picture 1>.
summary: [reference generation] Preserve <Subject 1>.
retention_analysis: Fully preserve <Subject 1>'s identity and red coat.
detailed_description:
[Shot 1] [0.00s-00:15.000s] shot_name: Opening line | audiovisual_description: <Subject 1> faces camera. | dialogue_and_vocalizations: <Subject 1> (S1) says: <d>[English] Keep every word exactly.</d>
[Shot 2] [00:15.000s-30.00s] shot_name: Piano ending | audiovisual_description: Cut to <Subject 1> at the piano; the performance ends. | dialogue_and_vocalizations: none
overall_soundscape: Quiet room tone and piano-key sounds.
non_diegetic_music: N/A"""

_BASE_IRREGULAR = """integrated_multimodal_description:
[Shot 1] [0.00s-7.35s] shot_name: Mara's doorway pause | audiovisual_description: Mara waits at the doorway while Theo turns from the desk. | dialogue_and_vocalizations: Mara gives one soft gasp.
[Shot 2] [7.35s-18.20s] shot_name: Theo's exact reply | audiovisual_description: Theo crosses to screen-left and Mara holds position. | dialogue_and_vocalizations: Theo (S1) says: <d>[English] Keep this exact.</d>
overall_soundscape: Quiet room tone and one synchronized footstep.
non_diegetic_music: N/A"""

_REF_IRREGULAR = """subject_definitions: <Subject 1> is Mara from <Picture 1>; <Subject 2> is Theo from <Picture 2>.
summary: [reference generation] Mara hears Theo's answer.
retention_analysis: <Subject 1>: fully_preserved - identity from <Picture 1>. <Subject 2>: fully_preserved - identity from <Picture 2>.
detailed_description:
[Shot 1] [0.00s-6.125s] shot_name: Mara listens | audiovisual_description: <Subject 1> remains screen-right while <Subject 2> enters screen-left. | dialogue_and_vocalizations: <Subject 1> gives one quiet laugh.
[Shot 2] [6.125s-17.80s] shot_name: Theo answers | audiovisual_description: <Subject 2> stops beside <Subject 1> as the camera settles. | dialogue_and_vocalizations: <Subject 2> (S2) says: <d>[English] I brought the reference.</d>
overall_soundscape: Soft room ambience and synchronized footsteps.
non_diegetic_music: N/A"""

_CANONICAL_TIME = r"(?:(?:\d{1,2}:){1,2})?\d+(?:\.\d+)?"
_SHOT_RECORD_RE = re.compile(
    r"^\[Shot (?P<number>[1-9]\d*)\] "
    rf"\[(?P<start>{_CANONICAL_TIME})s-(?P<end>{_CANONICAL_TIME})s\] "
    r"shot_name: (?P<name>[^|\n]+) \| "
    r"audiovisual_description: (?P<description>[^|\n]+) \| "
    r"dialogue_and_vocalizations: (?P<vocals>[^\n]+)$"
)

_H3_GUIDE_PATHS = (
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
    os.path.join(
        _APP_DIR, "services", "llm_guides", "director",
        "minimax_h3_shot_breakdown.md",
    ),
)


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
        self.assertIn("HIGHEST PRIORITY REQUEST-FACT LEDGER", combined)
        self.assertIn("yellow leaf", combined)
        self.assertIn("chipped blue cup", combined)
        self.assertNotIn("automatic long-video segment", compact)
        self._assert_no_execution_terms(combined)

    def test_all_h3_guides_require_discrete_parseable_shot_records(self):
        required_shape = (
            "[Shot N] [STARTs-ENDs] shot_name: SHORT NAME | "
            "audiovisual_description:"
        )
        for path in _H3_GUIDE_PATHS:
            with self.subTest(path=path):
                with open(path, "r", encoding="utf-8") as handle:
                    guide = handle.read()
                compact = " ".join(guide.split())
                self.assertIn(required_shape, compact)
                self.assertIn("dialogue_and_vocalizations:", guide)
                self.assertRegex(
                    compact, r"one (?:physical-line record|physical line)",
                )
                self.assertIn("Never bury a shot boundary in prose", compact)
                self.assertIn("numeric value, precision, order", compact)
                self.assertIn("canonical trailing `s` unit wrapper", compact)
                self.assertIn(
                    "Every user-authored event timestamp is a mandatory "
                    "record boundary",
                    compact,
                )
                self.assertIn(
                    "even when it marks an action, line, sound, or state "
                    "change rather than a camera cut",
                    compact,
                )
                self.assertIn(
                    "The preceding record must end at that exact value and "
                    "the next record must start at that exact value",
                    compact,
                )
                self.assertIn(
                    "A record boundary does not imply a visual cut",
                    compact,
                )
                self.assertIn(
                    "Never omit or round the timestamp, and never invent a "
                    "cut, event, or timestamp",
                    compact,
                )
                self.assertIn("At 3.125 seconds", compact)
                self.assertIn("At 9.50 seconds", compact)
                self.assertIn("Duration 12.75 seconds", compact)
                self.assertIn("HIGHEST PRIORITY REQUEST-FACT LEDGER", guide)
                self.assertIn(
                    "every authored identity; concrete object and its "
                    "qualifiers or color; action; location; event; sound; "
                    "requested silence; and music",
                    compact,
                )
                self.assertIn(
                    "Never output, label, quote, or mention this ledger",
                    compact,
                )
                self.assertIn(
                    "format repair may change only canonical wrappers and "
                    "record-boundary syntax",
                    compact,
                )
                self.assertIn(
                    "preserve every request fact and association unchanged",
                    compact,
                )
                self.assertIn(
                    "Never invent a fact, qualifier, identity, relationship, "
                    "action, intensity, or escalation",
                    compact,
                )
                self.assertIn("yellow leaf", guide)
                self.assertIn("chipped blue cup", guide)
                self.assertIn("15.00-40.00:", guide)
                self.assertIn("[15.00s-40.00s]", guide)
                self.assertIn("Invalid -> valid correction", compact)
                self.assertIn("omit the shot marker", compact)
                self.assertNotIn("as the camera moves closer", guide)
                expected_authored_action = (
                    "audiovisual_description: <Subject 1> enters. |"
                    if path == _H3_GUIDE_PATHS[1]
                    else "audiovisual_description: The door opens. |"
                )
                self.assertIn(expected_authored_action, guide)
                self.assertIn("strictly ordered, contiguous, disjoint", compact)
                self.assertRegex(
                    compact,
                    r"(?:The next record's|each next) START equals the previous "
                    r"(?:record's )?END",
                )
                self._assert_no_execution_terms(guide)

        with open(_H3_GUIDE_PATHS[0], "r", encoding="utf-8") as handle:
            base_guide = handle.read()
        with open(_H3_GUIDE_PATHS[1], "r", encoding="utf-8") as handle:
            ref_guide = handle.read()
        with open(_H3_GUIDE_PATHS[2], "r", encoding="utf-8") as handle:
            dialect_guide = handle.read()
        with open(_H3_GUIDE_PATHS[3], "r", encoding="utf-8") as handle:
            director_guide = handle.read()
        self.assertIn("[Shot 1] [0.00s-6.125s]", base_guide)
        self.assertIn("[Shot 2] [6.125s-18.20s]", base_guide)
        self.assertIn("Mara gives one soft gasp", base_guide)
        self.assertIn("Preserve the user's exact register", base_guide)
        self.assertIn(
            "Interaction alone does not imply speech or a vocal reaction",
            base_guide,
        )
        self.assertIn("without inventing a new action or reaction", base_guide)
        self.assertNotIn(
            "assign the remaining seconds to concrete reactions or movement",
            base_guide,
        )
        self.assertIn("<Subject 1> is Mara from <Picture 1>", ref_guide)
        self.assertIn("[Shot 2] [6.125s-17.80s]", ref_guide)
        self.assertIn("<d>[English] I brought the reference.</d>", ref_guide)
        self.assertIn("Preserve the user's exact register", ref_guide)
        self.assertIn(
            "do not invent an action or reaction merely to fill unused duration",
            " ".join(ref_guide.split()),
        )
        self.assertIn("a requested quiet laugh", " ".join(ref_guide.split()))
        self.assertIn(
            "Use reactions or motion only when requested or necessarily "
            "entailed by the authored event; never invent them as filler",
            " ".join(director_guide.split()),
        )
        self.assertNotIn(
            "After the last spoken line, use visible reactions or motion",
            director_guide,
        )
        normalized_dialect = " ".join(dialect_guide.split())
        self.assertIn(
            "After the final line, keep mouths closed and extend or hold only "
            "the requested state and atmosphere. Use reactions or movement "
            "only when requested or necessarily entailed by the authored "
            "event; never invent them as filler",
            normalized_dialect,
        )
        self.assertNotIn(
            "After the final line, use visible reactions or movement",
            dialect_guide,
        )

    def test_shot_record_contract_rejects_prose_only_buried_timing(self):
        valid_lines = (
            "[Shot 1] [0.00s-7.35s] shot_name: Doorway pause | "
            "audiovisual_description: Mara waits at the doorway. | "
            "dialogue_and_vocalizations: none",
            "[Shot 2] [7.35s-18.20s] shot_name: Quiet reply | "
            "audiovisual_description: Mara crosses the room. | "
            "dialogue_and_vocalizations: Mara (S1) says: "
            "<d>[English] Keep this exact.</d>",
        )
        parsed = [_SHOT_RECORD_RE.fullmatch(line) for line in valid_lines]
        self.assertTrue(all(parsed))
        self.assertEqual(
            [int(match.group("number")) for match in parsed if match],
            [1, 2],
        )
        self.assertEqual(float(parsed[0].group("start")), 0.0)
        self.assertEqual(parsed[0].group("end"), parsed[1].group("start"))
        self.assertGreater(
            float(parsed[1].group("end")),
            float(parsed[1].group("start")),
        )

        invalid_lines = (
            "[Shot 1] Mara waits. At 7.35 seconds, [Shot 2] she crosses "
            "the room and says: <d>[English] Keep this exact.</d>",
            "15.00-40.00: Mara crosses the room.",
            "[15.00s-40.00s] Mara crosses the room.",
        )
        for line in invalid_lines:
            with self.subTest(line=line):
                self.assertIsNone(_SHOT_RECORD_RE.fullmatch(line))

        _, plain_events = parse_global_timeline_prompt(invalid_lines[1])
        self.assertEqual(plain_events, [])
        _, generic_range_events = parse_global_timeline_prompt(invalid_lines[2])
        self.assertEqual(len(generic_range_events), 1)
        self.assertEqual(generic_range_events[0]["kind"], "range")
        self.assertNotIn("[Shot ", generic_range_events[0]["text"])

    def test_production_parser_accepts_exact_canonical_base_and_ref_records(self):
        cases = (
            (
                _BASE_IRREGULAR,
                (0.0, 7.35, 18.2),
                ("Mara's doorway pause", "Theo's exact reply"),
                ("one soft gasp", "<d>[English] Keep this exact.</d>"),
            ),
            (
                _REF_IRREGULAR,
                (0.0, 6.125, 17.8),
                ("Mara listens", "Theo answers"),
                ("one quiet laugh", "<d>[English] I brought the reference.</d>"),
            ),
        )
        for prompt, boundaries, names, vocal_content in cases:
            with self.subTest(names=names):
                _, events = parse_global_timeline_prompt(prompt)
                self.assertEqual(len(events), 2)
                self.assertEqual(events[0]["kind"], "range")
                self.assertEqual(events[1]["kind"], "range")
                self.assertEqual(events[0]["start"], boundaries[0])
                self.assertEqual(events[0]["end"], boundaries[1])
                self.assertEqual(events[1]["start"], boundaries[1])
                self.assertEqual(events[1]["end"], boundaries[2])
                self.assertEqual(events[-1]["end"], boundaries[-1])
                for expected, event in zip(names, events):
                    self.assertIn(f"shot_name: {expected}", event["text"])
                for expected, event in zip(vocal_content, events):
                    self.assertIn(expected, event["text"])

        for prompt in (_BASE_60S, _REF_30S):
            with self.subTest(clock_precision=prompt[:32]):
                record_lines = [
                    line for line in prompt.splitlines()
                    if line.startswith("[Shot ")
                ]
                self.assertTrue(record_lines)
                self.assertTrue(all(
                    _SHOT_RECORD_RE.fullmatch(line) for line in record_lines
                ))
        self.assertIn("00:30.000s", _BASE_60S)
        self.assertIn("00:15.000s", _REF_30S)

    def test_authored_event_examples_keep_exact_precision_as_parser_boundaries(self):
        expected_ranges = (
            ("0.00", "3.125"),
            ("3.125", "9.50"),
            ("9.50", "12.75"),
        )
        expected_content = (
            ("Mara waits by the door", "yellow leaf"),
            ("Mara opens the door", "yellow leaf", "chipped blue cup"),
            ("yellow leaf", "chipped blue cup", "<d>[English] Ready.</d>"),
        )

        for path in _H3_GUIDE_PATHS:
            with self.subTest(path=path):
                with open(path, "r", encoding="utf-8") as handle:
                    guide = handle.read()
                records = []
                for line in guide.splitlines():
                    stripped = line.strip()
                    if (
                        stripped.startswith("`[Shot ")
                        and stripped.endswith("`")
                        and any(
                            f"[{start}s-{end}s]" in stripped
                            for start, end in expected_ranges
                        )
                    ):
                        records.append(stripped[1:-1])

                self.assertEqual(len(records), 3)
                matches = [_SHOT_RECORD_RE.fullmatch(line) for line in records]
                self.assertTrue(all(matches))
                self.assertEqual(
                    tuple(
                        (match.group("start"), match.group("end"))
                        for match in matches if match
                    ),
                    expected_ranges,
                )
                self.assertEqual(
                    [int(match.group("number")) for match in matches if match],
                    [1, 2, 3],
                )
                for expected_items, record in zip(expected_content, records):
                    for expected in expected_items:
                        self.assertIn(expected, record)
                self.assertNotIn("chipped blue cup", records[0])

                _, events = parse_global_timeline_prompt("\n".join(records))
                self.assertEqual(len(events), 3)
                self.assertEqual(
                    [(event["start"], event["end"]) for event in events],
                    [(0.0, 3.125), (3.125, 9.5), (9.5, 12.75)],
                )

    def test_actual_base_and_ref_system_composition_keeps_examples_and_explicit_appendix(self):
        cases = (
            (
                "minimax_h3_fl2va", 18.2, _BASE_IRREGULAR,
                _H3_GUIDE_PATHS[0],
            ),
            (
                "minimax_h3_ref2va", 17.8, _REF_IRREGULAR,
                _H3_GUIDE_PATHS[1],
            ),
        )
        missing = object()
        prior_wgp = sys.modules.get("wgp", missing)
        no_override_wgp = SimpleNamespace(
            get_model_def=lambda *_args, **_kwargs: None,
        )
        with patch.dict(sys.modules, {"wgp": no_override_wgp}):
            for model_type, duration, response, guide_path in cases:
                captured = {}

                def fake_generate(**kwargs):
                    captured.update(kwargs)
                    return response

                with patch.object(
                    llm_service, "generate", side_effect=fake_generate,
                ):
                    result = llm_service.enhance_prompt(
                        "synthetic authorized request",
                        mode="video",
                        model_type=model_type,
                        duration_seconds=duration,
                        nsfw=True,
                    )

                self.assertTrue(result)
                system = captured["system_prompt"]
                actual_guide = Path(guide_path).read_text(
                    encoding="utf-8",
                ).strip()
                self.assertTrue(system.startswith(actual_guide))
                self.assertIn("[Shot N] [STARTs-ENDs]", system)
                self.assertIn("Invalid -> valid correction", system)
                self.assertIn("[Shot 1] [0.00s-6.125s]", system)
                self.assertIn("EXPLICIT CONTENT AUTHORING", system)
                self.assertIn("dialogue_and_vocalizations:", system)
                self.assertIn("HIGHEST PRIORITY REQUEST-FACT LEDGER", system)
                self.assertIn("yellow leaf", system)
                self.assertIn("chipped blue cup", system)

        if prior_wgp is missing:
            self.assertNotIn("wgp", sys.modules)
        else:
            self.assertIs(sys.modules.get("wgp"), prior_wgp)

    def test_director_record_contract_stays_inside_existing_video_prompt(self):
        with open(_H3_GUIDE_PATHS[-1], "r", encoding="utf-8") as handle:
            guide = handle.read()
        compact = " ".join(guide.split())

        self.assertIn(
            "inside the existing video_prompt string and does not add or "
            "change any Director JSON field",
            compact,
        )
        self.assertNotIn('"shot_records"', guide)

    def test_audio_driven_h3_and_ltx_system_prompts_keep_model_contracts_separate(self):
        clip = {"start": 0.0, "end": 18.2, "label": "reply"}

        def row(video_prompt, window_prompts):
            return {
                "scene_goal": "Mara receives the answer.",
                "scene_type": "dialogue",
                "subjects_on_screen": [{
                    "visual_description": "Mara in a blue coat",
                    "position_or_relation": "screen-right foreground",
                }],
                "spatial_setup": "Mara stands screen-right.",
                "environment": "Quiet office",
                "visual_style": "Natural live action",
                "lighting": "Soft daylight",
                "mood": "Measured",
                "action_beats": ["Mara turns toward Theo."],
                "dialogue_beats": [{
                    "speaker_id": "char_0",
                    "spoken_text": "Keep this exact.",
                    "delivery": "quietly",
                    "physical_cue": "Mara holds eye contact.",
                }],
                "camera_plan": {
                    "framing": "medium shot",
                    "movement": "slow push in",
                },
                "audio_plan": {
                    "mode": "dialogue_driven",
                    "lip_sync_critical": True,
                },
                "ending_beat": "Mara remains still.",
                "video_prompt": video_prompt,
                "window_prompts": window_prompts,
            }

        canonical = """integrated_multimodal_description:
[Shot 1] [0.00s-18.20s] shot_name: Exact reply | audiovisual_description: Mara stands screen-right as the camera pushes in. | dialogue_and_vocalizations: Mara (S1) says: <d>[English] Keep this exact.</d>
overall_soundscape: Quiet office room tone.
non_diegetic_music: N/A"""
        cases = (
            ("minimax_h3_fl2va", row(canonical, [])),
            (
                "ltx2_22B_distilled",
                row(
                    'Medium shot. Mara says "Keep this exact."',
                    ["First twenty-second passage."],
                ),
            ),
        )
        captured = {}
        returned = {}
        for model, response_row in cases:
            planner = ShortFilmPlanner()
            planner._video_model = model
            planner._image_model = "flux"
            planner._uses_generated_shot_images = False
            planner._build_all_image_paths = lambda *_args, **_kwargs: []
            calls = []

            def fake_call(**kwargs):
                calls.append(kwargs)
                return [response_row]

            planner._call_llm_json = fake_call
            returned[model] = planner._plan_audio_driven(
                [clip],
                "Mara receives an exact answer.",
                lyrics=None,
                speaker_mappings=None,
                reference_image_path=None,
                char_profiles=[],
                has_reference=False,
            )
            captured[model] = calls

        h3_call = captured["minimax_h3_fl2va"][0]
        h3_system = h3_call["system_prompt"]
        self.assertIn("follow the MiniMax H3 Context-IR guide", h3_system)
        self.assertIn("[Shot N] [STARTs-ENDs]", h3_system)
        self.assertIn("Invalid -> valid correction", h3_system)
        self.assertIn("Compact canonical record", h3_system)
        self.assertIn("HIGHEST PRIORITY REQUEST-FACT LEDGER", h3_system)
        self.assertIn("yellow leaf", h3_system)
        self.assertIn("chipped blue cup", h3_system)
        self.assertNotIn("One single flowing paragraph", h3_system)
        self.assertNotIn("Dialogue: in quotes", h3_system)
        self.assertNotIn("WINDOW PROMPTS vs VIDEO PROMPT", h3_system)
        self.assertNotIn("(OPTIONAL) Window 1", h3_system)
        self.assertEqual(
            h3_call["json_schema"]["items"]["properties"]
            ["window_prompts"]["maxItems"],
            0,
        )
        self.assertEqual(returned["minimax_h3_fl2va"][0].window_prompts, [])

        ltx_call = captured["ltx2_22B_distilled"][0]
        ltx_system = ltx_call["system_prompt"]
        self.assertIn("One single flowing paragraph", ltx_system)
        self.assertIn("Dialogue: in quotes", ltx_system)
        self.assertIn("WINDOW PROMPTS vs VIDEO PROMPT", ltx_system)
        self.assertIn("(OPTIONAL) Window 1", ltx_system)
        self.assertNotIn(
            "maxItems",
            ltx_call["json_schema"]["items"]["properties"]["window_prompts"],
        )
        self.assertEqual(
            returned["ltx2_22B_distilled"][0].window_prompts,
            ["First twenty-second passage."],
        )

    def test_audio_driven_h3_retries_invalid_record_shape_once(self):
        clip = {"start": 0.0, "end": 18.2, "label": "reply"}
        canonical = """integrated_multimodal_description:
[Shot 1] [0.00s-18.20s] shot_name: Mara stands screen-right | audiovisual_description: Mara stands screen-right as the camera pushes in while (S1) says <d>[English] Keep this exact.</d> | dialogue_and_vocalizations: none
overall_soundscape: Quiet office room tone.
non_diegetic_music: N/A"""

        def row(video_prompt):
            return {
                "scene_goal": "Mara receives the answer.",
                "scene_type": "dialogue",
                "subjects_on_screen": [{
                    "visual_description": "Mara in a blue coat",
                }],
                "spatial_setup": "Mara stands screen-right.",
                "environment": "Quiet office",
                "visual_style": "Natural live action",
                "lighting": "Soft daylight",
                "mood": "Measured",
                "action_beats": ["Mara turns toward Theo."],
                "dialogue_beats": [{
                    "speaker_id": "char_0",
                    "spoken_text": "Keep this exact.",
                    "delivery": "quietly",
                    "physical_cue": "Mara holds eye contact.",
                }],
                "camera_plan": {"framing": "medium shot"},
                "audio_plan": {
                    "mode": "dialogue_driven",
                    "lip_sync_critical": True,
                },
                "ending_beat": "Mara remains still.",
                "video_prompt": video_prompt,
                "window_prompts": [],
            }

        planner = ShortFilmPlanner()
        planner._video_model = "minimax_h3_ref2va"
        planner._image_model = "flux"
        planner._uses_generated_shot_images = False
        planner._build_all_image_paths = lambda *_args, **_kwargs: []
        authoritative = row("""integrated_multimodal_description:
0.00-18.20: Mara stands screen-right as the camera pushes in while (S1) says <d>[English] Keep this exact.</d>
overall_soundscape: Quiet office room tone.
non_diegetic_music: N/A""")
        mutated_repair = row(canonical)
        mutated_repair["scene_goal"] = "MUTATED GOAL"
        mutated_repair["action_beats"] = ["MUTATED ACTION"]
        mutated_repair["dialogue_beats"][0]["spoken_text"] = "Mutated words."
        mutated_repair["subjects_on_screen"][0]["visual_description"] = (
            "MUTATED IDENTITY"
        )
        calls = []
        repair_calls = []

        def fake_call(**kwargs):
            calls.append(kwargs)
            return [authoritative]

        def fake_generate(**kwargs):
            repair_calls.append(kwargs)
            return json.dumps([mutated_repair])

        planner._call_llm_json = fake_call
        planner._generate = fake_generate
        with patch.object(llm_service, "_provider", "local"):
            shots = planner._plan_audio_driven(
                [clip],
                "Mara receives an exact answer.",
                lyrics=None,
                speaker_mappings=None,
                reference_image_path=None,
                char_profiles=[],
                has_reference=False,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(repair_calls), 1)
        self.assertIn("FORMAT-ONLY H3 REPAIR", repair_calls[0]["prompt"])
        self.assertIn("Rewrite only each video_prompt", repair_calls[0]["prompt"])
        self.assertEqual(repair_calls[0]["thinking_budget"], 0)
        self.assertEqual(repair_calls[0]["temperature"], 0.2)
        self.assertFalse(repair_calls[0]["enable_thinking"])
        self.assertEqual(shots[0].scene_goal, "Mara receives the answer.")
        self.assertEqual(shots[0].action_beats, ["Mara turns toward Theo."])
        self.assertEqual(
            shots[0].subjects_on_screen[0].visual_description,
            "Mara in a blue coat",
        )
        self.assertEqual(
            shots[0].dialogue_beats[0].spoken_text,
            "Keep this exact.",
        )
        self.assertEqual(shots[0].video_prompt, canonical)
        self.assertEqual(shots[0].window_prompts, [])

    def test_audio_h3_repair_lock_rejects_mutation_swap_wrapper_and_no_range(self):
        source = """integrated_multimodal_description:
0.00-7.350: Mara (S1) waits beside <Subject 1> and gives one soft gasp.
7.350-18.20: Theo (S2) opens the scarlet door and says <d>[English] Keep this exact.</d>
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        exact = """integrated_multimodal_description:
[Shot 1] [0.00s-7.350s] shot_name: Mara waits | audiovisual_description: Mara (S1) waits beside <Subject 1> and gives one soft gasp. | dialogue_and_vocalizations: none
[Shot 2] [7.350s-18.20s] shot_name: Theo opens scarlet door | audiovisual_description: Theo (S2) opens the scarlet door and says <d>[English] Keep this exact.</d> | dialogue_and_vocalizations: none
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        self.assertEqual(_h3_audio_repair_lock_errors(source, exact), [])

        mutations = {
            "mutated": exact.replace("scarlet door", "blue door"),
            "swapped": exact.replace(
                "[Shot 1] [0.00s-7.350s]", "[Shot 1] [7.350s-18.20s]",
            ).replace(
                "[Shot 2] [7.350s-18.20s]", "[Shot 2] [0.00s-7.350s]",
            ),
            "precision": exact.replace("7.350s", "7.35s"),
            "wrapper": "NOTE: hidden wrapper\n" + exact,
        }
        for label, candidate in mutations.items():
            with self.subTest(label=label):
                errors = [
                    *_h3_audio_repair_lock_errors(source, candidate),
                    *validate_h3_context_ir_records(
                        candidate, mode="t2va", duration_seconds=18.2,
                    ),
                ]
                self.assertTrue(errors)

        no_range = """integrated_multimodal_description:
Mara waits beside the scarlet door.
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        self.assertTrue(_h3_audio_repair_lock_errors(no_range, exact))

        canonical_source = exact
        renumbered = exact.replace("[Shot 1]", "[Shot 9]", 1)
        self.assertIn(
            "format repair changed canonical shot numbers",
            _h3_audio_repair_lock_errors(canonical_source, renumbered),
        )

    def test_audio_h3_format_repair_helper_never_physically_retries(self):
        calls = []

        def malformed(**kwargs):
            calls.append(kwargs)
            return "not JSON"

        planner = ShortFilmPlanner(llm_generate=malformed)
        repaired = _call_h3_audio_format_repair_once(
            planner,
            user_prompt="format-only repair fixture",
            system_prompt="return JSON",
            max_tokens=256,
            image_paths=[],
            json_schema={"type": "array", "items": {"type": "object"}},
        )
        self.assertEqual(repaired, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["temperature"], 0.2)

    def test_audio_h3_non_object_authoritative_row_fails_closed(self):
        planner = ShortFilmPlanner(llm_generate=lambda **_kwargs: "[]")
        planner._video_model = "minimax_h3"
        planner._image_model = "flux"
        planner._uses_generated_shot_images = False
        planner._build_all_image_paths = lambda *_args, **_kwargs: []
        planner._call_llm_json = lambda **_kwargs: [None]

        with (
            patch.object(llm_service, "_provider", "local"),
            self.assertRaisesRegex(RuntimeError, "authoritative shot is not an object"),
        ):
            planner._plan_audio_driven(
                [{"start": 0.0, "end": 10.0, "label": "wait"}],
                "Safe fixture scene.",
                lyrics=None,
                speaker_mappings=None,
                reference_image_path=None,
                char_profiles=[],
                has_reference=False,
            )

    def test_audio_h3_format_repair_requires_the_selected_local_provider(self):
        invalid = """integrated_multimodal_description:
0.00-10.00: Mara waits beside the scarlet door.
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        row = {
            "scene_goal": "Mara waits.",
            "scene_type": "dialogue",
            "subjects_on_screen": [{"visual_description": "Mara"}],
            "spatial_setup": "Mara stands screen-right.",
            "environment": "Quiet office",
            "visual_style": "Natural live action",
            "lighting": "Soft daylight",
            "mood": "Measured",
            "action_beats": ["Mara waits."],
            "dialogue_beats": [],
            "camera_plan": {"framing": "medium shot"},
            "audio_plan": {"mode": "dialogue_driven"},
            "ending_beat": "Mara remains still.",
            "video_prompt": invalid,
            "window_prompts": [],
        }
        planner = ShortFilmPlanner()
        planner._video_model = "minimax_h3"
        planner._image_model = "flux"
        planner._uses_generated_shot_images = False
        planner._build_all_image_paths = lambda *_args, **_kwargs: []
        calls = []
        planner._call_llm_json = lambda **kwargs: calls.append(kwargs) or [row]

        with (
            patch.object(llm_service, "_provider", "remote"),
            self.assertRaisesRegex(RuntimeError, "local format repair is unavailable"),
        ):
            planner._plan_audio_driven(
                [{"start": 0.0, "end": 10.0, "label": "wait"}],
                "Mara waits.",
                lyrics=None,
                speaker_mappings=None,
                reference_image_path=None,
                char_profiles=[],
                has_reference=False,
            )
        self.assertEqual(len(calls), 1)

    def test_all_h3_guides_prefer_natural_unequal_inferred_timing(self):
        for path in _H3_GUIDE_PATHS:
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
            self.assertIn("HIGHEST PRIORITY REQUEST-FACT LEDGER", guide)
            self.assertIn("yellow leaf", guide)
            self.assertIn("chipped blue cup", guide)
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
                duration_seconds=60,
                window_count=4,
                window_size_seconds=15,
                raw_enhancer_mode=True,
            )

        self.assertEqual(result, _BASE_60S)
        self.assertEqual(len(calls), 1)
        self.assertIn("Duration: 60 seconds", calls[0]["prompt"])
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
                duration_seconds=30,
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

    def test_h3_repeated_record_labels_bypass_generic_loop_truncation(self):
        repeated = _REF_30S.replace(
            "Cut to <Subject 1> at the piano; the performance ends.",
            "<Subject 1> keeps the scarlet coat; <Picture 1> remains the exact "
            "identity reference while <Subject 1> crosses to the piano.",
        )
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return repeated

        with patch.object(llm_service, "generate", side_effect=fake_generate):
            result = llm_service.enhance_prompt(
                "Preserve <Subject 1> from <Picture 1> and the exact dialogue.",
                mode="video",
                model_type="minimax_h3_ref2va",
                duration_seconds=30,
                raw_enhancer_mode=True,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result, repeated)
        self.assertEqual(result.count("[Shot "), 2)
        self.assertIn("the exact identity reference", result)

    def test_h3_bare_range_gets_one_format_only_repair(self):
        invalid = """integrated_multimodal_description:
0.00-40.00: Mara (S1) pulls the scarlet cord and says <d>[English] Keep this exact.</d>
overall_soundscape: Rope strain and quiet room tone.
non_diegetic_music: N/A"""
        repaired = """integrated_multimodal_description:
[Shot 1] [0.00s-40.00s] shot_name: Scarlet cord | audiovisual_description: Mara (S1) pulls the scarlet cord and says <d>[English] Keep this exact.</d> | dialogue_and_vocalizations: none
overall_soundscape: Rope strain and quiet room tone.
non_diegetic_music: N/A"""
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return invalid if len(calls) == 1 else repaired

        with patch.object(llm_service, "generate", side_effect=fake_generate):
            result = llm_service.enhance_prompt(
                "Mara pulls the scarlet cord from 0.00 through 40.00 seconds.",
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=40,
            )

        self.assertEqual(result, repaired)
        self.assertEqual(len(calls), 2)
        self.assertIn("FORMAT-ONLY MINIMAX H3 BASE REPAIR", calls[1]["system_prompt"])
        self.assertEqual(calls[1]["thinking_budget"], 0)
        self.assertEqual(calls[1]["temperature"], 0.2)
        self.assertNotIn("image_paths", calls[1])

    def test_h3_invalid_repair_fails_closed_after_exactly_one_pass(self):
        invalid = """integrated_multimodal_description:
15.00-40.00: Mara crosses the room.
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return invalid

        with (
            patch.object(llm_service, "generate", side_effect=fake_generate),
            self.assertRaisesRegex(ValueError, "after one format-only repair"),
        ):
            llm_service.enhance_prompt(
                "Mara crosses the room during a 40-second shot.",
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=40,
            )

        self.assertEqual(len(calls), 2)

    def test_h3_unparseable_repair_cannot_replace_visual_semantics(self):
        invalid = """integrated_multimodal_description:
Shot 1 0.00-10.00 Scarlet cord; Mara waits under amber light.
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        mutated = """integrated_multimodal_description:
[Shot 1] [0.00s-10.00s] shot_name: Blue door | audiovisual_description: Theo runs beneath green light. | dialogue_and_vocalizations: none
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return invalid if len(calls) == 1 else mutated

        with (
            patch.object(llm_service, "generate", side_effect=fake_generate),
            self.assertRaisesRegex(ValueError, "format repair rewrote visual content"),
        ):
            llm_service.enhance_prompt(
                "A ten-second shot.",
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=10,
            )

        self.assertEqual(len(calls), 2)

    def test_h3_format_repair_cannot_expand_while_retaining_source_text(self):
        invalid = """integrated_multimodal_description:
Shot 1 0.00-10.00 Scarlet cord; Mara waits under amber light.
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        expanded = """integrated_multimodal_description:
[Shot 1] [0.00s-10.00s] shot_name: Theo under green light | audiovisual_description: Scarlet cord; Mara waits under amber light. Theo runs through a blue door. | dialogue_and_vocalizations: (S9) says <d>[English] Invented words.</d>
overall_soundscape: Quiet room tone plus thunder.
non_diegetic_music: N/A with drums"""
        calls = []
        def fake_generate(**kwargs):
            calls.append(kwargs)
            return invalid if len(calls) == 1 else expanded
        with (
            patch.object(llm_service, "generate", side_effect=fake_generate),
            self.assertRaisesRegex(ValueError, "added|invented|changed"),
        ):
            llm_service.enhance_prompt(
                "A ten-second shot.", mode="video",
                model_type="minimax_h3_fl2va", duration_seconds=10,
            )
        self.assertEqual(len(calls), 2)

    def test_h3_format_repair_fails_closed_without_recoverable_source_range(self):
        invalid = """integrated_multimodal_description:
Shot 1 Scarlet cord; Mara waits under amber light.
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        expanded = """integrated_multimodal_description:
[Shot 1] [0.00s-10.00s] shot_name: Theo green door | audiovisual_description: Shot 1 Scarlet cord; Mara waits under amber light. Theo runs through a blue door. | dialogue_and_vocalizations: none
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return invalid if len(calls) == 1 else expanded

        with (
            patch.object(llm_service, "generate", side_effect=fake_generate),
            self.assertRaisesRegex(ValueError, "cannot prove an exact record mapping"),
        ):
            llm_service.enhance_prompt(
                "A ten-second shot.",
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=10,
            )

        self.assertEqual(len(calls), 2)

    def test_h3_repair_cannot_swap_subject_speaker_time_associations(self):
        invalid = """integrated_multimodal_description:
0.00-5.00: <Subject 1> (S1) waits under amber light.
5.00-10.00: <Subject 2> (S2) opens the scarlet door.
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        swapped = """integrated_multimodal_description:
[Shot 1] [0.00s-5.00s] shot_name: Scarlet door | audiovisual_description: <Subject 2> (S2) opens the scarlet door. | dialogue_and_vocalizations: none
[Shot 2] [5.00s-10.00s] shot_name: Amber wait | audiovisual_description: <Subject 1> (S1) waits under amber light. | dialogue_and_vocalizations: none
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        calls = []

        def fake_generate(**kwargs):
            calls.append(kwargs)
            return invalid if len(calls) == 1 else swapped

        with (
            patch.object(llm_service, "generate", side_effect=fake_generate),
            self.assertRaisesRegex(ValueError, "association changed"),
        ):
            llm_service.enhance_prompt(
                "Keep <Subject 1> (S1) and <Subject 2> (S2) in authored order.",
                mode="video",
                model_type="minimax_h3_fl2va",
                duration_seconds=10,
            )

        self.assertEqual(len(calls), 2)

    def test_h3_whole_document_grammar_rejects_wrappers_and_trailing_fields(self):
        prefixed = "NOTE: hidden metadata\n" + _BASE_IRREGULAR
        suffixed = _BASE_IRREGULAR + "\nprivate_debug_payload: authored secret"

        prefix_errors = validate_h3_context_ir_records(
            prefixed, mode="t2va", duration_seconds=18.2,
        )
        suffix_errors = validate_h3_context_ir_records(
            suffixed, mode="t2va", duration_seconds=18.2,
        )

        self.assertTrue(any("wrapper text" in error for error in prefix_errors))
        self.assertTrue(any("unexpected top-level field NOTE" in error for error in prefix_errors))
        self.assertTrue(any("unexpected top-level field private_debug_payload" in error for error in suffix_errors))
        self.assertTrue(any("exactly one physical line" in error for error in suffix_errors))

    def test_director_compile_preserves_canonical_physical_records(self):
        compiled, _ = compile_h3_official_prompt(
            _BASE_IRREGULAR,
            [],
            [],
            mode="t2va",
            duration_seconds=18.2,
        )
        fields = re.search(
            r"(?ms)^integrated_multimodal_description:\s*(.*?)"
            r"^overall_soundscape:",
            compiled,
        )
        self.assertIsNotNone(fields)
        records = [line for line in fields.group(1).splitlines() if line.strip()]
        self.assertEqual(len(records), 2)
        self.assertTrue(all(_SHOT_RECORD_RE.fullmatch(line) for line in records))
        self.assertIn("[0.00s-7.35s]", records[0])
        self.assertIn("[7.35s-18.20s]", records[1])
        self.assertIn("Mara's doorway pause", compiled)
        self.assertIn("Theo's exact reply", compiled)
        self.assertIn("<d>[English] Keep this exact.</d>", compiled)
        self.assertEqual(
            validate_h3_context_ir_records(
                compiled, mode="t2va", duration_seconds=18.2,
            ),
            [],
        )
        _, events = parse_global_timeline_prompt(fields.group(1))
        self.assertEqual(len(events), 2)

    def test_director_clip_compile_keeps_ref2va_record_metadata(self):
        plans = [{
            "video_prompt": _REF_IRREGULAR,
            "_director_h3_prompt_mode": "ref2va",
            "_director_h3_model_family": "ref2va",
            "_director_duration_sec": 17.8,
            "_director_dialogue_beats": [],
            "_director_subjects_on_screen": [],
        }]

        compile_h3_clip_plans(plans)

        result = plans[0]["video_prompt"]
        self.assertEqual(result.count("[Shot "), 2)
        self.assertIn("Mara listens", result)
        self.assertIn("Theo answers", result)
        self.assertIn("[6.125s-17.80s]", result)
        self.assertIn("<Subject 2> (S2) says", result)
        self.assertIn("<d>[English] I brought the reference.</d>", result)
        self.assertEqual(
            validate_h3_context_ir_records(
                result, mode="ref2va", duration_seconds=17.8,
            ),
            [],
        )

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
