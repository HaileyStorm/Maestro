"""CPU evidence for explicit cumulative geometry and semantic prompt replay."""

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from services.h3_cumulative_plan import (
    H3CumulativePlanError,
    plan_h3_cumulative_chain,
    validate_h3_cumulative_plan,
)
from services.h3_native_continuation import (
    H3NativeContinuationStep,
    audio_tick_at_frame,
    is_legal_h3_video_frame_count,
)
from services.h3_shot_planner import (
    _H3_CANONICAL_RECORD_RE,
    _canonical_context_ir_parts,
    _h3_frame_at,
    _protect_dialogue,
    plan_h3_native_shots,
    seal_h3_shot_plan,
)


def _replay(plan, authority=None):
    expected = authority or plan
    return validate_h3_cumulative_plan(
        plan,
        expected_plan_sha256=expected["plan_sha256"],
        expected_source_sha256=expected["source_sha256"],
    )


def _reseal(plan):
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    plan["plan_sha256"] = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _timeline(prompt):
    protected, _blocks = _protect_dialogue(prompt)
    _order, _fields, _visual, events = _canonical_context_ir_parts(protected)
    return [
        (
            _h3_frame_at(event["start"], 24),
            _h3_frame_at(event["end"], 24),
            event["text"],
        )
        for event in events
    ]


class H3CumulativePlanTests(unittest.TestCase):
    def plan(
        self, source="An adult courier crosses the long hall.", frames=500, **kwargs
    ):
        return plan_h3_cumulative_chain(
            global_prompt=source, requested_frames=frames, **kwargs
        )

    def test_geometry_separates_sampling_publication_and_full_outputs(self):
        plan = self.plan()
        self.assertEqual(plan["generated_piece_frames"], [345, 119, 51])
        self.assertEqual(plan["published_piece_frames"], [345, 119, 36])
        self.assertEqual([w["sampler_frames"] for w in plan["windows"]], [345, 141, 73])
        self.assertEqual(
            [w["cumulative_generated_frames"] for w in plan["windows"]], [345, 464, 515]
        )
        self.assertEqual(
            [w["cumulative_published_frames"] for w in plan["windows"]], [345, 464, 500]
        )
        self.assertEqual(plan["publication_trim_frames"], 15)
        self.assertEqual(plan["shot_plan"]["clip_trim_tail_frames"], [0, 0, 15])
        self.assertEqual(plan["shot_plan"]["clip_published_frames"], [345, 119, 36])
        slices = plan["shot_plan"]["source_contracts"][0]["execution_slices"]
        self.assertEqual(
            [(s["start_frame"], s["end_frame_exclusive"]) for s in slices],
            [(0, 345), (345, 464), (464, 500)],
        )
        self.assertEqual(_replay(json.loads(json.dumps(plan))), plan)

    def test_rounding_boundaries_and_selected_ceilings(self):
        for requested in (
            22,
            23,
            39,
            40,
            123,
            124,
            344,
            345,
            346,
            464,
            465,
            500,
            515,
            1000,
        ):
            with self.subTest(requested=requested):
                plan = self.plan(
                    frames=requested, first_window_frames=243, max_extension_frames=34
                )
                self.assertEqual(plan["published_frames"], requested)
                self.assertTrue(0 <= plan["publication_trim_frames"] <= 16)
                self.assertTrue(
                    is_legal_h3_video_frame_count(plan["windows"][0]["sampler_frames"])
                )
                self.assertLessEqual(plan["windows"][0]["sampler_frames"], 243)
                cursor = 0
                for index, window in enumerate(plan["windows"]):
                    self.assertEqual(window["absolute_publish_start_frame"], cursor)
                    cursor = window["absolute_publish_end_frame"]
                    self.assertTrue(
                        is_legal_h3_video_frame_count(window["sampler_frames"])
                    )
                    self.assertEqual(
                        window["cumulative_published_audio_ticks"],
                        audio_tick_at_frame(cursor),
                    )
                    self.assertEqual(
                        window["cumulative_generated_audio_ticks"],
                        audio_tick_at_frame(window["cumulative_generated_frames"]),
                    )
                    if index:
                        step = H3NativeContinuationStep(**window["step"])
                        self.assertEqual(step.target_frames, window["sampler_frames"])
                        self.assertEqual(
                            step.absolute_publish_start_frame,
                            window["absolute_publish_start_frame"],
                        )
                        self.assertLessEqual(step.extension_frames, 34)
                    records = _timeline(window["sampler_prompt"])
                    self.assertEqual(records[0][0], 0)
                    self.assertEqual(records[-1][1], window["sampler_frames"])
                    self.assertEqual(
                        [r[1] for r in records[:-1]], [r[0] for r in records[1:]]
                    )
                self.assertEqual(cursor, requested)

    def test_single_capture_keeps_publication_prompt_and_no_append_step(self):
        plan = self.plan(frames=141)
        self.assertEqual(len(plan["windows"]), 1)
        self.assertEqual(plan["windows"][0]["kind"], "capture")
        self.assertIsNone(plan["windows"][0]["step"])
        self.assertEqual(plan["windows"][0]["history_context_frames"], 0)
        self.assertEqual(
            plan["windows"][0]["sampler_prompt"], plan["shot_plan"]["clip_prompts"][0]
        )

    def test_exact_existing_publication_contract_is_preserved(self):
        source = (
            "[0s-20s] The adult courier crosses the hall. <d>[English] Keep moving.</d>"
        )
        plan = self.plan(source)
        ordinary = plan_h3_native_shots(
            global_prompt=source,
            clip_frame_counts=[345, 119, 51],
            clip_requested_frames=[345, 119, 36],
            fps=24,
            source_canonicalization="t2va",
        )
        self.assertEqual(plan["shot_plan"], ordinary)
        self.assertEqual(
            sum(
                w["sampler_prompt"].count("<d>[English] Keep moving.</d>")
                for w in plan["windows"]
            ),
            1,
        )
        self.assertEqual(
            plan["shot_plan"]["source_contracts"][0]["authored_prompt"], source
        )

    def test_sampler_rebase_preserves_all_publication_payload_bytes(self):
        plan = self.plan(
            "[0s-10s] The courier opens a case. <d>[English]  Look. </d>\n"
            "[10s-20.833s] The courier closes the case."
        )
        for prompt, window in zip(plan["shot_plan"]["clip_prompts"], plan["windows"]):
            # Protect dialogue only for parsing; compare the complete restored
            # payload text, including exact dialogue whitespace and spelling.
            def payloads(text):
                protected, blocks = _protect_dialogue(text)
                _order, fields, visual, _events = _canonical_context_ir_parts(protected)
                result = []
                for line in fields[visual].splitlines():
                    value = _H3_CANONICAL_RECORD_RE.fullmatch(line.strip()).group(
                        "payload"
                    )
                    for token, block in blocks:
                        value = value.replace(token, block)
                    result.append(value)
                return result

            sampled = payloads(window["sampler_prompt"])
            if window["history_context_frames"]:
                sampled = sampled[1:]
            if window["publication_trim_frames"]:
                sampled = sampled[:-1]
            self.assertEqual(sampled, payloads(prompt))

    def test_completed_history_action_is_not_replayed(self):
        dialogue = "<d>[English] The seal is intact.</d>"
        plan = self.plan(
            f"[0s-14.375s] The adult courier taps the seal. {dialogue}\n"
            "[14.375s-20.833s] The courier turns toward the door."
        )
        window = plan["windows"][1]
        history = _timeline(window["sampler_prompt"])[0]
        self.assertEqual(history[:2], (0, 22))
        self.assertIn("Retained history", history[2])
        self.assertIn("dialogue_and_vocalizations: none", history[2])
        self.assertNotIn("taps the seal", window["sampler_prompt"])
        self.assertNotIn(dialogue, window["sampler_prompt"])
        self.assertIn("turns toward the door", window["sampler_prompt"])
        self.assertEqual(_timeline(window["sampler_prompt"])[1][0], 22)

    def test_literal_dialogue_structure_remains_exact(self):
        dialogue = "<d>[Speaker 1]  Literal [Shot 9] [0s-1s] summary: Keep  this. </d>"
        source = f"[0s-20.833s] The adult courier walks. {dialogue}"
        plan = self.plan(source)
        self.assertEqual(
            sum(w["sampler_prompt"].count(dialogue) for w in plan["windows"]), 1
        )
        self.assertEqual(
            plan["shot_plan"]["dialogue_manifest"][0]["exact_block"], dialogue
        )
        self.assertEqual(_replay(plan), plan)

    def test_existing_multiline_dialogue_limit_surfaces_without_rewriting(self):
        source = "The courier says <d>[English] Keep\nthis wording.</d>"
        with self.assertRaisesRegex(
            H3CumulativePlanError, "dialogue block on one line"
        ):
            self.plan(source)

    def test_final_blocking_stays_before_unpublished_padding(self):
        plan = self.plan(
            "The adult traveler enters.\nFINAL BLOCKING: The traveler sits beside the window."
        )
        final = plan["windows"][-1]
        records = _timeline(final["sampler_prompt"])
        blocking = [
            record for record in records if "sits beside the window" in record[2]
        ]
        self.assertEqual(len(blocking), 1)
        self.assertLessEqual(blocking[0][1], 22 + final["published_tail_frames"])
        self.assertEqual(
            records[-1][:2],
            (22 + final["published_tail_frames"], final["sampler_frames"]),
        )
        self.assertIn("Unpublished padding", records[-1][2])
        self.assertNotIn("sits beside the window", records[-1][2])
        self.assertEqual(
            sum(
                w["sampler_prompt"].count("sits beside the window")
                for w in plan["windows"]
            ),
            1,
        )

    def test_sensitive_authorized_subjects_follow_identical_compiler_path(self):
        for source in (
            "Adults discuss sex.",
            "A violent battle.",
            "A controversial political speech.",
        ):
            with self.subTest(source=source):
                plan = self.plan(source)
                self.assertEqual(plan["global_prompt"], source)
                self.assertIn(
                    source, plan["shot_plan"]["source_contracts"][0]["semantic_prompt"]
                )
                self.assertEqual(_replay(plan), plan)

    def test_invalid_inputs_and_planning_limits_fail_before_compilation(self):
        for kwargs in (
            {"frames": True},
            {"frames": 21},
            {"frames": 22.0},
            {"first_window_frames": 123},
            {"first_window_frames": 362},
            {"max_extension_frames": 18},
            {"max_extension_frames": True},
            {"max_extension_frames": 136},
            {"frames": 10_000_000},
            {"source": "x" * (256 * 1024 + 1)},
            {"source": "\ud800"},
        ):
            with (
                self.subTest(
                    kwargs={
                        key: (len(value) if isinstance(value, str) else value)
                        for key, value in kwargs.items()
                    }
                ),
                self.assertRaises(H3CumulativePlanError),
            ):
                self.plan(**kwargs)

    def test_replay_rejects_drift_even_after_journal_reseal(self):
        original = self.plan()
        mutations = (
            lambda p: p["windows"][1].update(history_context_frames=21),
            lambda p: p["windows"][1].update(sampler_frames=124),
            lambda p: p["windows"][1].update(cumulative_published_audio_ticks=1),
            lambda p: p["windows"][1].update(sampler_prompt="A replacement prompt."),
            lambda p: p.update(fps=True),
            lambda p: p.update(unsupported_reference="image.png"),
            lambda p: p["shot_plan"]["event_ownership"][0].update(
                owner_segment_index=1
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(original)
                mutation(changed)
                seal_h3_shot_plan(changed["shot_plan"])
                _reseal(changed)
                # Even a self-supplied replacement hash cannot make the invalid
                # derived fields pass deterministic replay.
                with self.assertRaises(H3CumulativePlanError):
                    _replay(changed)
                with self.assertRaisesRegex(H3CumulativePlanError, "trusted"):
                    _replay(changed, original)

    def test_trusted_hashes_reject_wholly_different_valid_plan(self):
        original = self.plan()
        replacement = self.plan("A different authorized narrative.")
        with self.assertRaisesRegex(H3CumulativePlanError, "trusted"):
            _replay(replacement, original)

    def test_size_gate_includes_seal_and_matches_replay_boundary(self):
        plan = self.plan()
        final_size = len(
            json.dumps(
                plan,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        )
        with patch("services.h3_cumulative_plan._MAX_PLAN_BYTES", final_size - 1):
            with self.assertRaisesRegex(H3CumulativePlanError, "byte limit"):
                self.plan()
            with self.assertRaisesRegex(H3CumulativePlanError, "byte limit"):
                _replay(plan)
        with patch("services.h3_cumulative_plan._MAX_PLAN_BYTES", final_size):
            self.assertEqual(self.plan(), plan)
            self.assertEqual(_replay(plan), plan)

    def test_replay_returns_unaliased_plan_and_rejects_legacy_or_nonfinite_json(self):
        original = self.plan()
        returned = _replay(original)
        returned["shot_plan"]["source_contracts"][0]["authored_prompt"] = "Changed"
        self.assertNotEqual(returned, original)
        legacy = original["shot_plan"]
        with self.assertRaises(H3CumulativePlanError):
            _replay(legacy, original)
        original["windows"][0]["sampler_frames"] = float("nan")
        with self.assertRaisesRegex(H3CumulativePlanError, "finite JSON"):
            _replay(original)


if __name__ == "__main__":
    unittest.main()
