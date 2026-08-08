"""Model-free contracts for the shared MiniMax H3 shot planner."""

from __future__ import annotations

import os
import sys
import unittest


_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.h3_shot_planner import (  # noqa: E402
    H3ShotPlanError,
    build_h3_visual_context,
    plan_h3_clip_frames,
    plan_h3_native_shots,
)
from shared.utils.prompt_parser import (  # noqa: E402
    build_global_timeline_clip_prompts,
    classify_timeline_clip_boundaries,
)


class H3SharedShotPlannerTests(unittest.TestCase):
    @staticmethod
    def _align(value: int) -> int:
        value = max(124, min(345, int(value)))
        remainder = (value - 5) % 17
        if remainder:
            value += 17 - remainder
        return value if value <= 345 else 345

    def test_profile_pressure_is_soft_untimed_and_persistable(self):
        prompts = {
            480: "Beat one. Beat two. Beat three. Beat four.",
            720: "Beat one. Beat two. Beat three. Beat four. Beat five.",
        }
        expected = {
            480: {"draft": 3, "fast": 2, "high": 2},
            720: {"draft": 4, "fast": 3, "high": 3},
        }
        for frames, profile_counts in expected.items():
            for profile, count in profile_counts.items():
                with self.subTest(frames=frames, profile=profile):
                    planned, policy = plan_h3_clip_frames(
                        frames,
                        prompt=prompts[frames],
                        fps=24,
                        minimum_frames=124,
                        maximum_frames=345,
                        align_frame_count=self._align,
                        profile_id=profile,
                    )
                    self.assertEqual(len(planned), count)
                    self.assertGreaterEqual(sum(planned), frames)
                    self.assertTrue(all(
                        124 <= item <= 345 and item % 17 == 5
                        for item in planned
                    ))
                    self.assertEqual(policy["profile_id"], profile)

    def test_explicit_timeline_and_manual_ceiling_override_profile_pressure(self):
        timed = "[0-10s] First action.\n[10-20s] Second action."
        plans = []
        for profile in ("draft", "fast", "high"):
            planned, policy = plan_h3_clip_frames(
                480,
                prompt=timed,
                fps=24,
                minimum_frames=124,
                maximum_frames=345,
                align_frame_count=self._align,
                profile_id=profile,
            )
            plans.append(planned)
            if profile != "high":
                self.assertEqual(policy["reason"], "authored timestamps are authoritative")
        self.assertEqual(plans[0], plans[1])
        self.assertEqual(plans[1], plans[2])

        manual, policy = plan_h3_clip_frames(
            720,
            prompt="One. Two. Three. Four. Five.",
            fps=24,
            minimum_frames=124,
            maximum_frames=192,
            align_frame_count=self._align,
            profile_id="fast",
            manual_segment_ceiling=True,
        )
        self.assertEqual(len(manual), 4)
        self.assertTrue(all(item <= 192 for item in manual))
        self.assertEqual(policy["reason"], "manual segment ceiling is authoritative")

        inline = (
            "integrated_multimodal_description: [Shot 1] At 0.00 seconds, begin. "
            "[Shot 2] At 15.00 seconds, cut to the next action."
        )
        _, inline_policy = plan_h3_clip_frames(
            720,
            prompt=inline,
            fps=24,
            minimum_frames=124,
            maximum_frames=345,
            align_frame_count=self._align,
            profile_id="draft",
        )
        self.assertEqual(
            inline_policy["reason"], "authored timestamps are authoritative",
        )

    def test_pressure_does_not_cut_an_indivisible_dialogue_action(self):
        planned, policy = plan_h3_clip_frames(
            480,
            prompt="The actor delivers <d>[English] One uninterrupted exact sentence.</d>",
            fps=24,
            minimum_frames=124,
            maximum_frames=345,
            align_frame_count=self._align,
            profile_id="draft",
        )
        self.assertEqual(len(planned), 2)
        self.assertFalse(policy["applied"])
        self.assertIn("indivisible", policy["reason"])

    def test_per_source_publication_geometry_preserves_exact_boundaries(self):
        plan = plan_h3_native_shots(
            global_prompt="First scene.\n\nSecond scene.",
            source_prompts=["First scene.", "Second scene."],
            source_indices=[0, 1],
            source_requested_frames=[240, 240],
            clip_frame_counts=[243, 243],
            clip_boundaries=[{
                "type": "cut",
                "source": "director_scene_boundary",
                "at_frame": 240,
                "at_seconds": 10.0,
            }],
            fps=24,
        )

        self.assertEqual(plan["clip_trim_tail_frames"], [3, 3])
        self.assertEqual(plan["clip_published_frames"], [240, 240])
        self.assertEqual(plan["published_frames"], 480)
        self.assertEqual(plan["shots"][1]["published_start_frame"], 240)
        self.assertEqual(plan["clip_boundaries"][0]["at_frame"], 240)
        self.assertEqual(plan["clip_boundaries"][0]["at_seconds"], 10.0)

    def test_authored_unequal_timeline_is_exact_and_profile_independent(self):
        dialogue = "<d>[English] Keep this exact.</d>"
        prompt = (
            f"[Shot 1] At 0 seconds, the host says {dialogue} "
            "[Shot 2] At 6 seconds, cut to the guest answering. "
            "FINAL BLOCKING: the guest faces camera"
        )
        plans = []
        for profile in ("draft", "fast", "high"):
            frames, policy = plan_h3_clip_frames(
                480,
                prompt=prompt,
                fps=24,
                minimum_frames=124,
                maximum_frames=345,
                align_frame_count=self._align,
                profile_id=profile,
                published_total_frames=480,
            )
            plans.append((frames, policy["clip_requested_frames"]))
            self.assertEqual(policy["reason"], "authored timestamps are authoritative")
        self.assertEqual(plans, [([158, 345], [144, 336])] * 3)

        shot_plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=plans[0][0],
            clip_requested_frames=plans[0][1],
            fps=24,
            clip_boundaries=[{
                "type": "cut", "at_frame": 144, "at_seconds": 6.0,
            }],
        )
        self.assertEqual(shot_plan["clip_trim_tail_frames"], [14, 9])
        self.assertEqual(shot_plan["clip_published_frames"], [144, 336])
        self.assertEqual(sum(
            item.count(dialogue) for item in shot_plan["clip_prompts"]
        ), 1)
        self.assertNotIn("guest faces camera", shot_plan["clip_prompts"][0])
        self.assertIn("guest faces camera", shot_plan["clip_prompts"][1])

        irregular_prompt = (
            "[Shot 1] At 0 seconds, the host enters. "
            "[Shot 2] At 7.25 seconds, cut to an overhead view."
        )
        irregular, irregular_policy = plan_h3_clip_frames(
            480,
            prompt=irregular_prompt,
            fps=24,
            minimum_frames=124,
            maximum_frames=345,
            align_frame_count=self._align,
            profile_id="draft",
            published_total_frames=480,
        )
        self.assertEqual(irregular, [175, 311])
        self.assertEqual(
            irregular_policy["clip_requested_frames"], [174, 306],
        )

    def test_untimed_action_density_can_choose_unequal_native_lengths(self):
        prompt = (
            "A nods. The performer crosses the room slowly and deliberately "
            "while inspecting every window and door in sequence."
        )
        frames, policy = plan_h3_clip_frames(
            480,
            prompt=prompt,
            fps=24,
            minimum_frames=124,
            maximum_frames=345,
            align_frame_count=self._align,
            profile_id="draft",
            published_total_frames=480,
        )

        self.assertEqual(frames, [158, 345])
        self.assertEqual(policy["clip_requested_frames"], [150, 330])
        self.assertTrue(policy["density_weighted"])

    def test_untimed_prose_is_partitioned_chronologically_without_repetition(self):
        prompt = (
            "SETTING: a rain-dark station with amber lamps\n"
            "Mara enters the station. Then she crosses the empty platform. "
            "Finally, she boards the train."
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[124, 124, 124],
            fps=24,
        )

        prompts = plan["clip_prompts"]
        self.assertTrue(all("SETTING: a rain-dark station" in item for item in prompts))
        for authored in (
            "Mara enters the station.",
            "Then she crosses the empty platform.",
            "Finally, she boards the train.",
        ):
            self.assertEqual(sum(authored in item for item in prompts), 1)
        self.assertEqual(
            [shot["continuity_mode"] for shot in plan["shots"]],
            ["independent", "extend_previous", "extend_previous"],
        )
        self.assertEqual(
            [item["type"] for item in plan["clip_boundaries"]],
            ["continuous", "continuous"],
        )

    def test_cast_section_does_not_repeat_future_action_as_visual_context(self):
        future = "Alice later opens the vault and reveals the ending."
        plan = plan_h3_native_shots(
            global_prompt=(
                "CAST:\nAlice: adult mechanic in green.\n"
                f"{future}\n"
                "[Shot 1] Alice enters. Then she checks the door. Finally she sits."
            ),
            clip_frame_counts=[124, 124, 124],
            fps=24,
        )

        self.assertTrue(all(
            "Alice: adult mechanic in green." in prompt
            for prompt in plan["clip_prompts"]
        ))
        self.assertEqual(sum(
            future in prompt for prompt in plan["clip_prompts"]
        ), 1)

    def test_dialogue_is_atomic_exact_and_owned_once(self):
        dialogue = "<d>[English] Keep  **every**\nword, exactly.</d>"
        plan = plan_h3_native_shots(
            global_prompt=(
                "An adult actor enters. " + dialogue
                + " Then the actor closes the door."
            ),
            clip_frame_counts=[124, 124],
            fps=24,
        )

        self.assertEqual(sum(
            prompt.count(dialogue) for prompt in plan["clip_prompts"]
        ), 1)
        self.assertEqual(len(plan["dialogue_manifest"]), 1)
        self.assertEqual(plan["dialogue_manifest"][0]["exact_block"], dialogue)

    def test_repeated_identical_structured_dialogue_is_not_nested_or_invented(self):
        block = "<d>[English] Again.</d>"
        plan = plan_h3_native_shots(
            global_prompt=f"The host speaks. {block}",
            structured_shots=[{
                "dialogue_beats": [
                    {"spoken_text": "Again."},
                    {"spoken_text": "Again."},
                ],
            }],
            clip_frame_counts=[124, 124],
            fps=24,
        )

        self.assertEqual(sum(item.count(block) for item in plan["clip_prompts"]), 2)
        self.assertNotIn("<d>[English] <d>", "\n".join(plan["clip_prompts"]))
        self.assertEqual(len(plan["dialogue_manifest"]), 2)
        self.assertTrue(all(
            item["exact_block"] == block
            and item["source"] == "structured_dialogue"
            for item in plan["dialogue_manifest"]
        ))

    def test_timed_mapping_matches_existing_mapper_but_dialogue_does_not_duplicate(self):
        dialogue = "<d>[English] One exact line.</d>"
        prompt = (
            "Keep the same adult pilot and orange suit.\n"
            f"[0-15s] The pilot walks and says {dialogue}\n"
            "[15-30s] The pilot enters the cockpit."
        )
        counts = [240, 240, 240]
        expected = build_global_timeline_clip_prompts(
            prompt, clip_frame_counts=counts, fps=24,
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=counts,
            fps=24,
            clip_boundaries=classify_timeline_clip_boundaries(
                prompt, clip_frame_counts=counts, fps=24,
            ),
        )

        # All non-dialogue timestamp clipping remains byte-for-byte identical.
        scrub = lambda value: value.replace(dialogue, "").replace("  ", " ").strip()
        self.assertEqual(
            [scrub(item) for item in plan["clip_prompts"]],
            [scrub(item) for item in expected],
        )
        self.assertEqual(sum(
            item.count(dialogue) for item in plan["clip_prompts"]
        ), 1)

    def test_inline_timed_marker_shaped_dialogue_remains_atomic(self):
        dialogue = "<d>[English] I remember [Scene 1] and [Scene 2]</d>"
        plan = plan_h3_native_shots(
            global_prompt=(
                f"[Shot 1] A person says {dialogue} "
                "[Shot 2] At 00:10.000, they leave."
            ),
            clip_frame_counts=[240, 240],
            fps=24,
        )

        self.assertEqual(
            sum(prompt.count(dialogue) for prompt in plan["clip_prompts"]), 1,
        )
        self.assertEqual(len(plan["dialogue_manifest"]), 1)
        for prompt in plan["clip_prompts"]:
            stripped = prompt.replace(dialogue, "")
            self.assertNotRegex(stripped, r"<\s*/?\s*d\s*>")

    def test_literal_dialogue_sentinel_cannot_relocate_speech(self):
        literal = "__H3_TIMELINE_DIALOGUE_SLOT_0_0__"
        dialogue = "<d>[English] Hello.</d>"
        plan = plan_h3_native_shots(
            global_prompt=(
                f"Literal {literal}. [Shot 1] At 0 seconds, waits. "
                f"[Shot 2] At 10 seconds, says {dialogue}"
            ),
            clip_frame_counts=[240, 240],
            fps=24,
        )

        self.assertTrue(all(literal in item for item in plan["clip_prompts"]))
        self.assertNotIn(dialogue, plan["clip_prompts"][0])
        self.assertIn(dialogue, plan["clip_prompts"][1])
        self.assertEqual(len(plan["dialogue_manifest"]), 1)

    def test_final_blocking_words_inside_dialogue_are_not_metadata(self):
        dialogue = "<d>[English] FINAL BLOCKING: leave now.</d>"
        for prompt in (
            f"She says {dialogue} Then she exits.",
            (
                f"[Shot 1] At 00:00.000, she says {dialogue} "
                "[Shot 2] At 00:10.000, she exits."
            ),
        ):
            with self.subTest(prompt=prompt):
                plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    clip_frame_counts=[240, 240],
                    fps=24,
                )
                self.assertEqual(sum(
                    item.count(dialogue) for item in plan["clip_prompts"]
                ), 1)
                self.assertTrue(all(
                    not source["authored_final_blocking"]
                    for source in plan["source_contracts"]
                ))
                for item in plan["clip_prompts"]:
                    stripped = item.replace(dialogue, "")
                    self.assertNotRegex(stripped, r"<\s*/?\s*d\s*>")

    def test_multiline_dialogue_headers_and_timestamps_remain_opaque(self):
        dialogue = (
            "<d>[English] first line.\n"
            "SETTING: this is spoken, not metadata.\n"
            "[Scene 9] At 00:05.000, these are spoken words.\n"
            "last line.</d>"
        )
        for prompt in (
            f"She says {dialogue} Then she exits.",
            (
                f"[Shot 1] At 00:00.000, she says {dialogue}\n"
                "[Shot 2] At 00:10.000, she exits."
            ),
        ):
            with self.subTest(prompt=prompt):
                plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    clip_frame_counts=[240, 240],
                    fps=24,
                )
                self.assertEqual(sum(
                    item.count(dialogue) for item in plan["clip_prompts"]
                ), 1)
                self.assertEqual(len(plan["dialogue_manifest"]), 1)
                without_dialogue = "\n".join(plan["clip_prompts"]).replace(
                    dialogue, "",
                )
                self.assertNotIn("this is spoken, not metadata", without_dialogue)
                self.assertNotIn("[Scene 9]", without_dialogue)

    def test_timed_final_blocking_belongs_only_to_the_final_segment(self):
        blocking = "the adult pilot closes the visor and faces camera"
        plan = plan_h3_native_shots(
            global_prompt=(
                "[0-10s] The pilot crosses the hangar.\n"
                "[10-20s] The pilot enters the cockpit.\n"
                f"FINAL BLOCKING: {blocking}"
            ),
            clip_frame_counts=[240, 240],
            fps=24,
        )
        self.assertNotIn(blocking, plan["clip_prompts"][0])
        self.assertIn(f"FINAL BLOCKING: {blocking}", plan["clip_prompts"][1])

    def test_inline_context_ir_final_blocking_belongs_only_to_final_segment(self):
        blocking = "the adult pilot closes the visor and faces camera"
        prompt = (
            "integrated_multimodal_description: [Shot 1] At 0.00 seconds, "
            "the pilot crosses the hangar. [Shot 2] At 10.00 seconds, the "
            f"pilot enters the cockpit. FINAL BLOCKING: {blocking}"
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[240, 240],
            fps=24,
        )

        self.assertNotIn(blocking, plan["clip_prompts"][0])
        self.assertEqual(
            sum(item.count(blocking) for item in plan["clip_prompts"]), 1,
        )
        self.assertIn(f"FINAL BLOCKING: {blocking}", plan["clip_prompts"][1])

    def test_structured_director_context_excludes_plot_and_future_dialogue(self):
        shot = {
            "environment": "an amber-lit workshop",
            "visual_style": "restrained 35mm realism",
            "lighting": "warm practical lamps",
            "spatial_setup": "Ada at the left workbench",
            "subjects_on_screen": [{
                "speaker_name": "Ada",
                "visual_description": "an adult mechanic with cropped black hair",
                "wardrobe": "oil-stained green coveralls",
            }],
            "action_beats": ["Ada discovers the hidden transmitter"],
            "dialogue_beats": [{"spoken_text": "Do not repeat this."}],
            "ending_beat": "Ada closes the steel toolbox",
        }
        context = build_h3_visual_context(shot)
        self.assertIn("amber-lit workshop", context)
        self.assertIn("oil-stained green coveralls", context)
        self.assertNotIn("hidden transmitter", context)
        self.assertNotIn("Do not repeat", context)
        self.assertNotIn("closes the steel toolbox", context)

    def test_structured_boundaries_dialogue_and_final_blocking_are_persisted(self):
        first = {
            "continuity_strategy": "independent",
            "environment": "a quiet studio",
            "dialogue_beats": [{
                "speaker_id": "host",
                "spoken_text": "Welcome back.",
            }],
            "ending_beat": "the host rests both hands on the desk",
        }
        second = {
            "continuity_strategy": "continuous",
            "environment": "the same quiet studio",
            "ending_beat": "the guest looks directly into camera",
        }
        plan = plan_h3_native_shots(
            global_prompt="Director story",
            source_prompts=["The host speaks.", "The guest answers silently."],
            source_indices=[0, 0, 1],
            structured_shots=[first, second],
            clip_frame_counts=[124, 124, 124],
            fps=24,
            clip_boundaries=[{"type": "continuous"}, {"type": "cut"}],
            segment_frames_maximum=192,
        )

        self.assertEqual(plan["segment_frames_maximum"], 192)
        self.assertEqual(plan["clip_boundaries"][0]["continuity_mode"], "extend_previous")
        self.assertEqual(plan["clip_boundaries"][1]["continuity_mode"], "continuous")
        self.assertEqual(plan["clip_boundaries"][1]["type"], "continuous")
        self.assertEqual(
            plan["dialogue_manifest"][0]["exact_block"],
            "<d>[English] Welcome back.</d>",
        )
        self.assertIn("FINAL BLOCKING: the host rests both hands on the desk", plan["clip_prompts"][1])
        self.assertIn("FINAL BLOCKING: the guest looks directly into camera", plan["clip_prompts"][2])

    def test_unbalanced_dialogue_fails_closed(self):
        for prompt in (
            "Actor says <d>[English] unfinished",
            "[0-20s] Actor says <d>[English] unfinished",
            "[0-20s] Actor says <d>[English] nested <d>[English] bad.</d></d>",
        ):
            with self.subTest(prompt=prompt):
                with self.assertRaisesRegex(H3ShotPlanError, "balanced"):
                    plan_h3_native_shots(
                        global_prompt=prompt,
                        clip_frame_counts=[124, 124],
                        fps=24,
                    )

        for spoken in (
            "<d>[English] missing close",
            "<d>[English] outer <d>[English] nested</d></d>",
            "<d>[English] one.</d><d>[English] two.</d>",
            "prefix <d>[English] tagged.</d>",
        ):
            with self.subTest(structured=spoken):
                with self.assertRaisesRegex(H3ShotPlanError, "balanced block"):
                    plan_h3_native_shots(
                        global_prompt="The actor speaks.",
                        structured_shots=[{
                            "dialogue_beats": [{"spoken_text": spoken}],
                        }],
                        clip_frame_counts=[124, 124],
                        fps=24,
                    )


if __name__ == "__main__":
    unittest.main()
