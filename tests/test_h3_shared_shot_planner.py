"""Model-free contracts for the shared MiniMax H3 shot planner."""

from __future__ import annotations

import copy
import os
import sys
import unittest


_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.h3_shot_planner import (  # noqa: E402
    H3_COMPILER_INPUT_REPLAY_VERSION,
    H3_SEMANTIC_PHYSICAL_CONTRACT_VERSION,
    H3ShotPlanError,
    build_h3_visual_context,
    estimate_h3_segment_count,
    plan_h3_clip_frames,
    plan_h3_native_shots,
    validate_h3_shot_plan_seal,
)
from shared.utils.prompt_parser import (  # noqa: E402
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

    def test_blank_prompt_estimate_uses_model_grid_range_not_multiplier(self):
        estimate = estimate_h3_segment_count(
            720,
            prompt="",
            fps=24,
            minimum_frames=124,
            maximum_frames=345,
            align_frame_count=self._align,
            profile_id="draft",
            published_total_frames=720,
        )

        self.assertEqual(estimate["likely"], 3)
        self.assertEqual(estimate["minimum"], 3)
        self.assertEqual(estimate["maximum"], 720 // 124)
        self.assertEqual(estimate["confidence"], "low")
        self.assertEqual(estimate["source"], "duration_profile_model_grid")
        self.assertNotEqual(estimate["maximum"], int(estimate["likely"] * 1.5))

    def test_prompt_estimate_is_the_exact_shared_planner_count(self):
        prompt = (
            "[Shot 1] At 0 seconds, begin. "
            "[Shot 2] At 6 seconds, cut. "
            "[Shot 3] At 17 seconds, reveal the final view."
        )
        estimate = estimate_h3_segment_count(
            720,
            prompt=prompt,
            fps=24,
            minimum_frames=124,
            maximum_frames=345,
            align_frame_count=self._align,
            profile_id="draft",
            published_total_frames=720,
        )
        frames, _ = plan_h3_clip_frames(
            720,
            prompt=prompt,
            fps=24,
            minimum_frames=124,
            maximum_frames=345,
            align_frame_count=self._align,
            profile_id="draft",
            published_total_frames=720,
        )

        self.assertEqual(
            (estimate["minimum"], estimate["likely"], estimate["maximum"]),
            (len(frames), len(frames), len(frames)),
        )
        self.assertEqual(estimate["confidence"], "high")
        self.assertEqual(estimate["source"], "deterministic_authored_timeline")

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
        self.assertEqual(shot_plan["global_prompt"], prompt)
        self.assertEqual(
            shot_plan["semantic_physical_contract_version"],
            H3_SEMANTIC_PHYSICAL_CONTRACT_VERSION,
        )
        self.assertNotEqual(
            shot_plan["clip_prompts"][0], shot_plan["clip_prompts"][1],
        )
        self.assertEqual(
            sum(item.count(dialogue) for item in shot_plan["clip_prompts"]), 1,
        )
        self.assertIn("[0-6s]", shot_plan["clip_prompts"][0])
        self.assertIn("[0-14s]", shot_plan["clip_prompts"][1])
        self.assertEqual(
            [item["owner_segment_index"] for item in shot_plan["event_ownership"]],
            [0, 1, 1],
        )
        self.assertEqual(
            [item["local_start_frame"] for item in shot_plan["event_ownership"]],
            [0, 0, None],
        )
        self.assertEqual(
            [item["kind"] for item in shot_plan["event_ownership"]],
            ["shot", "shot", "final_blocking"],
        )
        validate_h3_shot_plan_seal(shot_plan)
        self.assertEqual(len(shot_plan["dialogue_manifest"]), 1)
        self.assertEqual(
            shot_plan["semantic_shots"][0]["execution_slices"],
            [
                {
                    "segment_index": 0, "physical_segment_index": 0,
                    "start_frame": 0, "end_frame_exclusive": 144,
                    "start_seconds": 0.0, "end_seconds": 6.0,
                },
                {
                    "segment_index": 1, "physical_segment_index": 1,
                    "start_frame": 144, "end_frame_exclusive": 480,
                    "start_seconds": 6.0, "end_seconds": 20.0,
                },
            ],
        )

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

    def test_untimed_semantic_prompt_is_partitioned_once_after_geometry(self):
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
        self.assertEqual(plan["global_prompt"], prompt)
        self.assertEqual(len(set(prompts)), 3)
        self.assertTrue(all(
            "SETTING: a rain-dark station with amber lamps" in item
            for item in prompts
        ))
        for action in (
            "Mara enters the station.",
            "Then she crosses the empty platform.",
            "Finally, she boards the train.",
        ):
            self.assertEqual(sum(action in item for item in prompts), 1)
        self.assertEqual(len(plan["semantic_shots"]), 1)
        self.assertTrue(
            plan["semantic_shots"][0]["prompt_rewrite_for_physical_split"],
        )
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
        prompt = (
            "CAST:\nAlice: adult mechanic in green.\n"
            f"{future}\n"
            "[Shot 1] Alice enters. Then she checks the door. Finally she sits."
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[124, 124, 124],
            fps=24,
        )

        self.assertEqual(
            sum(future in item for item in plan["clip_prompts"]), 1,
        )

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

        self.assertEqual(
            sum(prompt.count(dialogue) for prompt in plan["clip_prompts"]), 1,
        )
        self.assertEqual(len(plan["dialogue_manifest"]), 1)
        self.assertEqual(plan["dialogue_manifest"][0]["exact_block"], dialogue)
        self.assertEqual(
            plan["dialogue_manifest"][0]["segment_index"],
            next(
                index for index, prompt in enumerate(plan["clip_prompts"])
                if dialogue in prompt
            ),
        )

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

        self.assertEqual(
            sum(item.count(block) for item in plan["clip_prompts"]), 2,
        )
        self.assertNotEqual(plan["clip_prompts"][0], plan["clip_prompts"][1])
        self.assertNotIn("<d>[English] <d>", "\n".join(plan["clip_prompts"]))
        self.assertEqual(len(plan["dialogue_manifest"]), 2)
        self.assertTrue(all(
            item["exact_block"] == block
            and item["spoken_text"] == "[English] Again."
            and item["speaker_id"] == ""
            and item["source"] == "semantic_prompt"
            for item in plan["dialogue_manifest"]
        ))

    def test_timed_semantic_prompt_is_owned_once_and_rebased(self):
        dialogue = "<d>[English] One exact line.</d>"
        prompt = (
            "Keep the same adult pilot and orange suit.\n"
            f"[0-15s] The pilot walks and says {dialogue}\n"
            "[15-30s] The pilot enters the cockpit."
        )
        counts = [240, 240, 240]
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=counts,
            fps=24,
            clip_boundaries=classify_timeline_clip_boundaries(
                prompt, clip_frame_counts=counts, fps=24,
            ),
        )

        self.assertEqual(plan["global_prompt"], prompt)
        self.assertEqual(len(set(plan["clip_prompts"])), 3)
        self.assertEqual(
            sum(item.count(dialogue) for item in plan["clip_prompts"]), 1,
        )
        self.assertEqual(
            [
                item["owner_segment_index"]
                for item in plan["event_ownership"]
                if item["kind"] != "untimed"
            ],
            [0, 1],
        )
        self.assertEqual(len(plan["dialogue_manifest"]), 1)

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

        self.assertEqual(sum(literal in item for item in plan["clip_prompts"]), 1)
        self.assertEqual(
            sum(dialogue in prompt for prompt in plan["clip_prompts"]), 1,
        )
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
                self.assertEqual(
                    sum(item.count(dialogue) for item in plan["clip_prompts"]), 1,
                )
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
                self.assertEqual(
                    sum(item.count(dialogue) for item in plan["clip_prompts"]), 1,
                )
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
        self.assertEqual(
            sum("crosses the hangar" in item for item in plan["clip_prompts"]), 1,
        )
        self.assertEqual(
            sum("enters the cockpit" in item for item in plan["clip_prompts"]), 1,
        )
        self.assertNotIn(f"FINAL BLOCKING: {blocking}", plan["clip_prompts"][0])
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

        self.assertEqual(plan["global_prompt"], prompt)
        self.assertNotEqual(plan["clip_prompts"][0], plan["clip_prompts"][1])
        self.assertNotIn(blocking, plan["clip_prompts"][0])
        self.assertIn(blocking, plan["clip_prompts"][1])

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
        self.assertTrue(all(
            "VISUAL CONTINUITY" in plan["clip_prompts"][index]
            for index in (0, 1)
        ))
        self.assertEqual(
            sum(
                "<d>[English] Welcome back.</d>" in item
                for item in plan["clip_prompts"]
            ),
            1,
        )
        self.assertIn("FINAL BLOCKING: the host rests both hands on the desk", plan["clip_prompts"][1])
        self.assertIn("FINAL BLOCKING: the guest looks directly into camera", plan["clip_prompts"][2])

    def test_semantic_shot_split_has_stable_ids_cursors_and_predecessors(self):
        first_prompt = (
            "subject_definitions: <Subject 1> is Mara from <Picture 1>.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0.00s-12.00s] shot_name: Exact action | "
            "audiovisual_description: <Subject 1> opens the red door. | "
            "dialogue_and_vocalizations: <Subject 1> (S1) says: "
            "<d>[English] Keep this exact.</d>\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        second_prompt = "A separate authored reaction with <Audio 1>."
        plan = plan_h3_native_shots(
            global_prompt=first_prompt + "\n\n" + second_prompt,
            source_prompts=[first_prompt, second_prompt],
            source_indices=[0, 0, 1],
            structured_shots=[
                {"shot_id": "authored-A"},
                {"shot_id": "authored-B"},
            ],
            clip_frame_counts=[158, 158, 124],
            clip_requested_frames=[144, 144, 120],
            fps=24,
        )

        self.assertEqual(plan["global_prompt"], first_prompt + "\n\n" + second_prompt)
        self.assertNotEqual(plan["clip_prompts"][0], plan["clip_prompts"][1])
        self.assertEqual(plan["clip_prompts"][2], second_prompt)
        self.assertEqual(
            sum("opens the red door" in item for item in plan["clip_prompts"]), 2,
        )
        self.assertEqual(
            len(plan["event_ownership"][0]["continuation_slices"]), 1,
        )
        self.assertEqual(plan["published_frames"], 408)
        self.assertEqual(
            [shot["authored_shot_id"] for shot in plan["shots"]],
            ["authored-A", "authored-A", "authored-B"],
        )
        self.assertEqual(
            [shot["execution_cursor_frame"] for shot in plan["shots"]],
            [0, 144, 0],
        )
        self.assertEqual(
            [shot["predecessor_physical_segment_id"] for shot in plan["shots"]],
            [None, "authored-A:segment-1", "authored-A:segment-2"],
        )
        self.assertEqual(
            plan["semantic_shots"][0]["reference_labels"],
            ["<Subject 1>", "<Picture 1>"],
        )
        self.assertEqual(len(plan["dialogue_manifest"]), 1)
        self.assertEqual(
            plan["dialogue_manifest"][0]["authored_shot_id"],
            "authored-A",
        )
        rebuilt = plan_h3_native_shots(
            global_prompt=plan["global_prompt"],
            source_prompts=[
                contract["semantic_prompt"]
                for contract in plan["semantic_shots"]
            ],
            source_indices=[shot["source_index"] for shot in plan["shots"]],
            structured_shots=plan["semantic_shots"],
            clip_frame_counts=plan["clip_frames"],
            clip_requested_frames=plan["clip_published_frames"],
            fps=24,
        )
        self.assertEqual(
            rebuilt["dialogue_manifest"], plan["dialogue_manifest"],
        )
        self.assertEqual(
            [shot["authored_shot_id"] for shot in rebuilt["shots"]],
            ["authored-A", "authored-A", "authored-B"],
        )

    def test_v2_prompt_and_event_ownership_seal_rejects_drift(self):
        prompt = "[0-6s] A subject opens a door.\n[6-20s] The subject sits."
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[158, 345],
            clip_requested_frames=[144, 336],
            fps=24,
        )
        validate_h3_shot_plan_seal(plan)
        rebuilt = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[158, 345],
            clip_requested_frames=[144, 336],
            fps=24,
        )
        self.assertEqual(rebuilt["clip_prompts"], plan["clip_prompts"])
        self.assertEqual(rebuilt["event_ownership"], plan["event_ownership"])
        self.assertEqual(
            rebuilt["prompt_contract_seal"], plan["prompt_contract_seal"],
        )

        for mutate in (
            lambda value: value["clip_prompts"].__setitem__(0, "changed"),
            lambda value: value["event_ownership"][1].update({
                "owner_segment_index": 0,
            }),
            lambda value: value["semantic_shots"][0].update({
                "authored_prompt": "changed",
            }),
        ):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(plan)
                mutate(changed)
                with self.assertRaisesRegex(H3ShotPlanError, "seal disagrees"):
                    validate_h3_shot_plan_seal(changed)

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

    def test_balanced_noncanonical_dialogue_fails_closed(self):
        for prompt in (
            "[0-4s] An adult host says <d>Ready</d>.",
            "[0-4s] An adult host says <d>[English] </d>.",
            "[0-4s] An adult host says <d>[English] \t\n </d>.",
            "[0-4s] An adult host says <d>[ ] hello</d>.",
            (
                "[0-4s] OPENING BLOCKING: The host says <d>Ready</d>. "
                "The host walks."
            ),
        ):
            with self.subTest(prompt=prompt):
                with self.assertRaisesRegex(
                    H3ShotPlanError, r"canonical <d>\[language\]",
                ):
                    plan_h3_native_shots(
                        global_prompt=prompt,
                        clip_frame_counts=[48, 48],
                        fps=24,
                    )

    def test_dialogue_blocks_cannot_be_compacted_or_hidden_in_final_blocking(self):
        long_dialogue = "<d>[English] " + ("word " * 1300).strip() + "</d>"
        with self.assertRaisesRegex(
            H3ShotPlanError, "structured final blocking cannot contain dialogue",
        ):
            plan_h3_native_shots(
                global_prompt="[0-4s] An adult host walks.",
                structured_shots=[{"closing_blocking": long_dialogue}],
                clip_frame_counts=[48, 48],
                fps=24,
            )

        with self.assertRaisesRegex(
            H3ShotPlanError, "dialogue blocks cannot be truncated",
        ):
            plan_h3_native_shots(
                global_prompt=(
                    "[0-4s] An adult host walks. "
                    f"FINAL BLOCKING: {long_dialogue}"
                ),
                clip_frame_counts=[48, 48],
                fps=24,
            )

    def test_overlapping_canonical_context_ir_fails_closed(self):
        prompt = (
            "subject_definitions: <Subject 1> is an adult traveler.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-10s] shot_name: Door | "
            "audiovisual_description: <Subject 1> opens the door. | "
            "dialogue_and_vocalizations: none\n"
            "[Shot 2] [5s-8s] shot_name: Map | "
            "audiovisual_description: <Subject 1> folds the map. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        with self.assertRaisesRegex(H3ShotPlanError, "non-overlapping"):
            plan_h3_native_shots(
                global_prompt=prompt,
                clip_frame_counts=[240, 240],
                fps=24,
            )

    def test_subframe_canonical_ranges_fail_closed_on_published_grid(self):
        prompt = (
            "subject_definitions: <Subject 1> is an adult traveler.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-0.01s] shot_name: First | "
            "audiovisual_description: <Subject 1> lifts a card. | "
            "dialogue_and_vocalizations: none\n"
            "[Shot 2] [0.01s-1s] shot_name: Second | "
            "audiovisual_description: <Subject 1> lowers the card. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        with self.assertRaisesRegex(H3ShotPlanError, "published frame grid"):
            plan_h3_native_shots(
                global_prompt=prompt,
                clip_frame_counts=[24],
                fps=24,
            )

    def test_subframe_generic_ranges_fail_closed_on_published_grid(self):
        with self.assertRaisesRegex(H3ShotPlanError, "published frame grid"):
            plan_h3_native_shots(
                global_prompt=(
                    "[0-0.01s] An adult traveler lifts a card.\n"
                    "[0.01-1s] The traveler lowers the card."
                ),
                clip_frame_counts=[24],
                fps=24,
            )

    def test_authored_ranges_cannot_extend_past_published_geometry(self):
        canonical = (
            "subject_definitions: <Subject 1> is an adult traveler.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Crossing | "
            "audiovisual_description: <Subject 1> crosses the room. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        for prompt in (canonical, "[0-20s] An adult traveler crosses the room."):
            with self.subTest(canonical=prompt is canonical):
                with self.assertRaisesRegex(
                    H3ShotPlanError, "published physical geometry",
                ):
                    plan_h3_native_shots(
                        global_prompt=prompt,
                        clip_frame_counts=[240],
                        fps=24,
                    )

    def test_canonical_inline_blocking_is_single_owner_across_continuation(self):
        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: OPENING BLOCKING: Cabinet remains. "
            "<Subject 1> studies a ledger. Final blocking: sits beside the desk. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        for structured in (
            {},
            {
                "spatial_setup": "cabinet remains",
                "closing_blocking": "sits beside the desk",
            },
        ):
            with self.subTest(structured=bool(structured)):
                plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    source_prompts=[prompt],
                    source_indices=[0, 0],
                    structured_shots=[structured],
                    clip_frame_counts=[240, 240],
                    fps=24,
                )
                self.assertEqual(
                    sum(
                        "cabinet remains" in item.casefold()
                        for item in plan["clip_prompts"]
                    ),
                    1,
                )
                self.assertEqual(
                    sum(
                        "sits beside the desk" in item
                        for item in plan["clip_prompts"]
                    ),
                    1,
                )
                self.assertEqual(
                    sum("studies a ledger" in item for item in plan["clip_prompts"]),
                    2,
                )
                final_event = next(
                    item for item in plan["event_ownership"]
                    if item["kind"] == "final_blocking"
                )
                self.assertEqual(final_event["owner_segment_index"], 1)

    def test_structured_opening_cannot_claim_later_authored_action(self):
        prompt_without_opening = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: <Subject 1> stands and studies the ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        for opening in ("stands", "Archive"):
            with self.subTest(valid_substring=opening):
                plan = plan_h3_native_shots(
                    global_prompt=prompt_without_opening,
                    structured_shots=[{"spatial_setup": opening}],
                    clip_frame_counts=[240, 240],
                    fps=24,
                )
                self.assertEqual(
                    sum(
                        "opening blocking:" in item.casefold()
                        for item in plan["clip_prompts"]
                    ),
                    1,
                )
                self.assertNotIn(
                    "opening blocking:", plan["clip_prompts"][1].casefold(),
                )

        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: OPENING BLOCKING: The cabinet remains "
            "closed. <Subject 1> studies the ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        for invalid_opening in (
            "<Subject 1> studies the ledger",
            "The cabinet",
            "cabinet remains",
        ):
            with self.subTest(invalid_opening=invalid_opening):
                with self.assertRaisesRegex(
                    H3ShotPlanError, "structured opening blocking conflicts",
                ):
                    plan_h3_native_shots(
                        global_prompt=prompt,
                        structured_shots=[{
                            "spatial_setup": invalid_opening,
                        }],
                        clip_frame_counts=[240, 240],
                        fps=24,
                    )

        final_prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: OPENING BLOCKING: The host waits "
            "FINAL BLOCKING: attacker opening. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        with self.assertRaisesRegex(
            H3ShotPlanError, "structured opening blocking conflicts",
        ):
            plan_h3_native_shots(
                global_prompt=final_prompt,
                structured_shots=[{
                    "spatial_setup": "attacker opening",
                }],
                clip_frame_counts=[240, 240],
                fps=24,
            )

    def test_opening_payload_punctuation_is_canonical_and_multiple_fields_fail(self):
        def prompt(opening: str) -> str:
            return (
                "subject_definitions: <Subject 1> is an adult host.\n\n"
                "integrated_multimodal_description:\n"
                "[Shot 1] [0s-20s] shot_name: Studio | "
                f"audiovisual_description: OPENING BLOCKING: {opening} "
                "<Subject 1> then walks. | dialogue_and_vocalizations: none\n"
                "overall_soundscape: Quiet room tone.\n"
                "non_diegetic_music: N/A"
            )

        for authored, structured in (
            ("The host kneels.", "The host kneels?"),
            ("The host kneels?", "The host kneels."),
            ("The host kneels!", "The host kneels"),
        ):
            with self.subTest(authored=authored, structured=structured):
                plan = plan_h3_native_shots(
                    global_prompt=prompt(authored),
                    structured_shots=[{"spatial_setup": structured}],
                    clip_frame_counts=[240, 240],
                    fps=24,
                )
                self.assertEqual(
                    plan["source_contracts"][0]["opening_blocking"], authored,
                )
                self.assertEqual(
                    [
                        item.casefold().count("opening blocking:")
                        for item in plan["clip_prompts"]
                    ],
                    [1, 0],
                )

        with self.assertRaisesRegex(
            H3ShotPlanError, "multiple OPENING BLOCKING fields",
        ):
            plan_h3_native_shots(
                global_prompt=prompt(
                    "First pose. OPENING BLOCKING: Second pose."
                ),
                clip_frame_counts=[240, 240],
                fps=24,
            )

    def test_canonical_structured_opening_preserves_terminal_punctuation(self):
        prompt = (
            "subject_definitions: <Subject 1> is an adult host.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-4s] shot_name: Studio | "
            "audiovisual_description: <Subject 1> walks forward. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        for opening in ("stand.", "stand!", "stand?"):
            with self.subTest(opening=opening):
                plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    structured_shots=[{"spatial_setup": opening}],
                    clip_frame_counts=[96],
                    fps=24,
                )
                owner = plan["clip_prompts"][0]
                self.assertIn(
                    f"Opening blocking: {opening} <Subject 1> walks forward.",
                    owner,
                )
                self.assertNotIn(f"{opening}.", owner)

    def test_each_canonical_record_owns_its_local_opening_once(self):
        prompt = (
            "subject_definitions: <Subject 1> is an adult host.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-2s] shot_name: Studio wide | "
            "audiovisual_description: <Subject 1> looks around. | "
            "dialogue_and_vocalizations: none\n"
            "[Shot 2] [2s-6s] shot_name: Studio close | "
            "audiovisual_description: OPENING BLOCKING: stands beside the desk. "
            "<Subject 1> walks forward. | dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            structured_shots=[{"spatial_setup": "stands"}],
            clip_frame_counts=[48, 48, 48],
            fps=24,
        )

        self.assertEqual(
            [
                item.casefold().count("opening blocking:")
                for item in plan["clip_prompts"]
            ],
            [1, 1, 0],
        )
        self.assertNotIn("stands beside the desk", plan["clip_prompts"][2].casefold())
        self.assertIn("walks forward", plan["clip_prompts"][2].casefold())

    def test_dialogue_terminated_opening_consumes_outer_punctuation(self):
        dialogue = "<d>[English] Ready!</d>"
        generic = plan_h3_native_shots(
            global_prompt=(
                "[0-4s] OPENING BLOCKING: The host kneels and says "
                f"{dialogue}. The host walks."
            ),
            clip_frame_counts=[48, 48],
            fps=24,
        )
        self.assertIn("The host walks", generic["clip_prompts"][1])
        self.assertNotIn(": . The host walks", generic["clip_prompts"][1])
        self.assertEqual(
            sum(item.count(dialogue) for item in generic["clip_prompts"]),
            1,
        )

        canonical_prompt = (
            "subject_definitions: <Subject 1> is an adult host.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-2s] shot_name: Studio wide | "
            "audiovisual_description: <Subject 1> looks around. | "
            "dialogue_and_vocalizations: none\n"
            "[Shot 2] [2s-6s] shot_name: Studio close | "
            "audiovisual_description: OPENING BLOCKING: The host kneels and says "
            f"{dialogue}. <Subject 1> walks forward. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        canonical = plan_h3_native_shots(
            global_prompt=canonical_prompt,
            structured_shots=[{"spatial_setup": "stands"}],
            clip_frame_counts=[48, 48, 48],
            fps=24,
        )
        self.assertIn("walks forward", canonical["clip_prompts"][2])
        self.assertNotIn(": . <Subject 1>", canonical["clip_prompts"][2])
        self.assertEqual(
            sum(item.count(dialogue) for item in canonical["clip_prompts"]),
            1,
        )

    def test_canonical_structured_blocking_rejects_reserved_separator(self):
        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: <Subject 1> studies a ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        for field in ("spatial_setup", "closing_blocking"):
            for source in (prompt, "An adult archivist studies a ledger."):
                with self.subTest(field=field, canonical=source == prompt):
                    source_indices = [0, 0]
                    source_prompts = [source]
                    clip_frames = [240, 240]
                    with self.assertRaisesRegex(
                        H3ShotPlanError, "reserved separator",
                    ):
                        plan_h3_native_shots(
                            global_prompt=source,
                            source_prompts=source_prompts,
                            source_indices=source_indices,
                            structured_shots=[{field: "left | right"}],
                            clip_frame_counts=clip_frames,
                            fps=24,
                        )

    def test_canonical_structured_fields_merge_schema_natively_once(self):
        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: <Subject 1> studies a ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            source_prompts=[prompt],
            source_indices=[0, 0],
            structured_shots=[{
                "environment": "a neutral red archive",
                "spatial_setup": "the closed cabinet remains at frame left",
                "closing_blocking": "the archivist closes the ledger",
            }],
            clip_frame_counts=[240, 240],
            fps=24,
        )
        aggregate = "\n".join(plan["clip_prompts"])
        self.assertEqual(aggregate.count("neutral red archive"), 2)
        self.assertEqual(aggregate.count("closed cabinet remains"), 1)
        self.assertEqual(aggregate.count("archivist closes the ledger"), 1)
        self.assertNotIn("archivist closes the ledger", plan["clip_prompts"][0])
        self.assertIn("archivist closes the ledger", plan["clip_prompts"][1])
        closing_event = next(
            item for item in plan["event_ownership"]
            if item["kind"] == "final_blocking"
        )
        self.assertEqual(closing_event["owner_segment_index"], 1)
        self.assertNotIn("FINAL BLOCKING:", aggregate)

    def test_structured_opening_rejects_reserved_blocking_markers(self):
        canonical = (
            "subject_definitions: <Subject 1> is an adult host.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-4s] shot_name: Studio | "
            "audiovisual_description: <Subject 1> walks forward. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        for source in (canonical, "[0-4s] An adult host walks forward."):
            for opening in (
                "stands. FINAL BLOCKING: sits",
                "OPENING BLOCKING: sits",
            ):
                with self.subTest(canonical=source == canonical, opening=opening):
                    with self.assertRaisesRegex(
                        H3ShotPlanError, "reserved structural marker",
                    ):
                        plan_h3_native_shots(
                            global_prompt=source,
                            structured_shots=[{"spatial_setup": opening}],
                            clip_frame_counts=[96],
                            fps=24,
                        )

    def test_multisentence_structured_opening_is_first_segment_only(self):
        opening = "The cabinet remains closed. A lamp stays at frame left."
        action = "<Subject 1> studies the ledger."
        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            f"audiovisual_description: {action} | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            source_prompts=[prompt],
            source_indices=[0, 0],
            structured_shots=[{"spatial_setup": opening}],
            clip_frame_counts=[240, 240],
            fps=24,
        )

        self.assertIn(opening, plan["clip_prompts"][0])
        self.assertNotIn("cabinet remains closed", plan["clip_prompts"][1])
        self.assertNotIn("lamp stays at frame left", plan["clip_prompts"][1])
        self.assertEqual(sum(action in item for item in plan["clip_prompts"]), 2)
        self.assertEqual(
            plan["source_contracts"][0]["opening_blocking"], opening,
        )

    def test_multisentence_opening_recovery_preserves_authored_provenance(self):
        opening = (
            "The cabinet stays locked. The warning lamp flickers twice!"
        )
        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: <Subject 1> studies the ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        initial = plan_h3_native_shots(
            global_prompt=prompt,
            structured_shots=[{"spatial_setup": opening}],
            clip_frame_counts=[240, 240],
            fps=24,
        )
        contract = initial["source_contracts"][0]
        compiler_inputs = {
            "version": H3_COMPILER_INPUT_REPLAY_VERSION,
            "authored_shot_id": contract["authored_shot_id"],
            "visual_context": contract["visual_context"],
            "opening_blocking": contract["opening_blocking"],
            "final_blocking": contract["final_blocking"],
            "structured_dialogue_blocks": contract[
                "structured_dialogue_blocks"
            ],
        }
        replay = plan_h3_native_shots(
            global_prompt=contract["authored_prompt"],
            source_prompts=[contract["authored_prompt"]],
            source_compiler_inputs=[compiler_inputs],
            clip_frame_counts=[240, 240],
            fps=24,
        )

        self.assertEqual(
            replay["source_contracts"][0]["semantic_prompt"],
            contract["semantic_prompt"],
        )
        self.assertEqual(
            replay["source_contracts"][0]["opening_blocking"], opening,
        )
        self.assertIn(opening, replay["clip_prompts"][0])
        self.assertNotIn("cabinet stays locked", replay["clip_prompts"][1])
        self.assertNotIn("warning lamp flickers", replay["clip_prompts"][1])
        validate_h3_shot_plan_seal(replay)

        with self.assertRaisesRegex(
            H3ShotPlanError, "structured opening blocking conflicts",
        ):
            plan_h3_native_shots(
                global_prompt=contract["semantic_prompt"],
                source_prompts=[contract["semantic_prompt"]],
                structured_shots=[{
                    "spatial_setup": "The cabinet stays locked. The warning lamp",
                }],
                clip_frame_counts=[240, 240],
                fps=24,
            )

    def test_replay_compiler_inputs_reconstruct_structured_semantics_exactly(self):
        source = "[0-4s] An adult mechanic holds position beside the workbench."
        initial = plan_h3_native_shots(
            global_prompt=source,
            structured_shots=[{
                "shot_id": "shot-replay",
                "environment": "a neutral amber workshop",
                "visual_style": "restrained documentary realism",
                "lighting": "soft practical lamps",
                "subjects_on_screen": [{
                    "speaker_name": "Ada",
                    "visual_description": "an adult mechanic",
                    "wardrobe": "plain green coveralls",
                }],
                "spatial_setup": "Ada remains at the left workbench",
                "closing_blocking": "Ada closes the steel toolbox",
                "dialogue_beats": [{
                    "spoken_text": "Keep these words exactly.",
                }],
            }],
            clip_frame_counts=[48, 48],
            fps=24,
        )
        contract = initial["source_contracts"][0]
        compiler_inputs = {
            "version": H3_COMPILER_INPUT_REPLAY_VERSION,
            "authored_shot_id": contract["authored_shot_id"],
            "visual_context": contract["visual_context"],
            "opening_blocking": contract["opening_blocking"],
            "final_blocking": contract["final_blocking"],
            "structured_dialogue_blocks": list(
                contract["structured_dialogue_blocks"]
            ),
        }
        replay = plan_h3_native_shots(
            global_prompt=contract["authored_prompt"],
            source_prompts=[contract["authored_prompt"]],
            source_compiler_inputs=[compiler_inputs],
            clip_frame_counts=[48, 48],
            fps=24,
        )

        self.assertEqual(
            replay["source_contracts"][0]["semantic_prompt"],
            contract["semantic_prompt"],
        )
        self.assertEqual(replay["clip_prompts"], initial["clip_prompts"])
        self.assertEqual(replay["event_ownership"], initial["event_ownership"])
        self.assertEqual(replay["dialogue_manifest"], initial["dialogue_manifest"])

        with self.assertRaisesRegex(
            H3ShotPlanError, "cannot be combined with structured shots",
        ):
            plan_h3_native_shots(
                global_prompt=contract["authored_prompt"],
                source_compiler_inputs=[compiler_inputs],
                structured_shots=[{"spatial_setup": "different"}],
                clip_frame_counts=[96],
                fps=24,
            )
        incomplete = dict(compiler_inputs)
        incomplete.pop("visual_context")
        with self.assertRaisesRegex(
            H3ShotPlanError, "replay compiler inputs are incomplete",
        ):
            plan_h3_native_shots(
                global_prompt=contract["authored_prompt"],
                source_compiler_inputs=[incomplete],
                clip_frame_counts=[96],
                fps=24,
            )

        with self.assertRaisesRegex(
            H3ShotPlanError, "replay compiler inputs are incomplete",
        ):
            plan_h3_native_shots(
                global_prompt=contract["authored_prompt"],
                source_compiler_inputs=[None],
                clip_frame_counts=[96],
                fps=24,
            )
        second_inputs = dict(compiler_inputs)
        second_inputs["authored_shot_id"] = "shot-replay-2"
        with self.assertRaisesRegex(
            H3ShotPlanError, "replay compiler inputs are incomplete",
        ):
            plan_h3_native_shots(
                global_prompt=contract["authored_prompt"],
                source_prompts=[
                    contract["authored_prompt"],
                    contract["authored_prompt"],
                ],
                source_indices=[0, 1],
                source_compiler_inputs=[compiler_inputs, None],
                clip_frame_counts=[96, 96],
                fps=24,
            )
        with self.assertRaisesRegex(
            H3ShotPlanError, "exactly cover used sources",
        ):
            plan_h3_native_shots(
                global_prompt=contract["authored_prompt"],
                source_prompts=[
                    contract["authored_prompt"],
                    contract["authored_prompt"],
                ],
                source_compiler_inputs=[compiler_inputs, second_inputs],
                clip_frame_counts=[96],
                fps=24,
            )

    def test_replay_compiler_inputs_reject_redundant_semantic_claims(self):
        dialogue = "<d>[English] Hello.</d>"
        dialogue_inputs = {
            "version": H3_COMPILER_INPUT_REPLAY_VERSION,
            "authored_shot_id": "shot-dialogue",
            "visual_context": "",
            "opening_blocking": "",
            "final_blocking": "",
            "structured_dialogue_blocks": [dialogue],
        }
        visual = (
            "VISUAL CONTINUITY (world, cast, and setting only): "
            "setting: neutral studio."
        )
        visual_inputs = {
            "version": H3_COMPILER_INPUT_REPLAY_VERSION,
            "authored_shot_id": "shot-visual",
            "visual_context": visual,
            "opening_blocking": "",
            "final_blocking": "",
            "structured_dialogue_blocks": [],
        }
        for source, compiler_inputs in (
            (f"[0-4s] The host says {dialogue}", dialogue_inputs),
            (f"{visual}\n[0-4s] An adult host waits.", visual_inputs),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    H3ShotPlanError,
                    "replay compiler inputs are not canonical",
                ):
                    plan_h3_native_shots(
                        global_prompt=source,
                        source_compiler_inputs=[compiler_inputs],
                        clip_frame_counts=[96],
                        fps=24,
                    )

    def test_canonical_blank_lines_do_not_change_record_order(self):
        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-10s] shot_name: First | "
            "audiovisual_description: <Subject 1> opens a ledger. | "
            "dialogue_and_vocalizations: none\n\n"
            "[Shot 2] [10s-20s] shot_name: Second | "
            "audiovisual_description: <Subject 1> closes the ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[240, 240],
            fps=24,
        )
        self.assertIn("opens a ledger", plan["clip_prompts"][0])
        self.assertIn("closes the ledger", plan["clip_prompts"][1])

    def test_structured_dialogue_does_not_replace_repeatable_state(self):
        plan = plan_h3_native_shots(
            global_prompt="SETTING: Ready.\nAn adult host waits.",
            structured_shots=[{
                "dialogue_beats": [{"spoken_text": "Ready."}],
            }],
            clip_frame_counts=[124, 124],
            fps=24,
        )
        self.assertTrue(all("SETTING: Ready." in item for item in plan["clip_prompts"]))
        self.assertEqual(
            sum("<d>[English] Ready.</d>" in item for item in plan["clip_prompts"]),
            1,
        )

    def test_final_blocking_has_one_final_segment_owner(self):
        plan = plan_h3_native_shots(
            global_prompt=(
                "An adult traveler enters.\n"
                "FINAL BLOCKING: The traveler sits beside the window."
            ),
            clip_frame_counts=[124, 124],
            fps=24,
        )
        final_event = next(
            item for item in plan["event_ownership"]
            if item["kind"] == "final_blocking"
        )
        self.assertEqual(final_event["owner_segment_index"], 1)
        self.assertEqual(final_event["owner_physical_segment_index"], 1)
        self.assertEqual(
            sum(
                "The traveler sits beside the window." in item
                for item in plan["clip_prompts"]
            ),
            1,
        )

    def test_canonical_action_spans_three_segments_with_dialogue_owned_once(self):
        dialogue = "<d>[English] Continue forward.</d>"
        action = "<Subject 1> carries the sealed case through the long hall."
        prompt = (
            "subject_definitions: <Subject 1> is an adult courier.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Hall crossing | "
            f"audiovisual_description: {action} | "
            "dialogue_and_vocalizations: <Subject 1> says: "
            f"{dialogue}\n"
            "overall_soundscape: Quiet footsteps.\n"
            "non_diegetic_music: N/A"
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[144, 144, 192],
            fps=24,
        )

        self.assertEqual(sum(action in item for item in plan["clip_prompts"]), 3)
        self.assertEqual(
            sum(dialogue in item for item in plan["clip_prompts"]), 1,
        )
        self.assertNotIn("Continuation of", plan["clip_prompts"][0])
        self.assertIn("Continuation of Hall crossing", plan["clip_prompts"][1])
        self.assertIn("Continuation of Hall crossing", plan["clip_prompts"][2])
        event = plan["event_ownership"][0]
        self.assertEqual(event["owner_segment_index"], 0)
        self.assertEqual(event["local_end_frame_exclusive"], 144)
        self.assertEqual(event["continuation_slices"], [
            {
                "segment_index": 1,
                "physical_segment_index": 1,
                "source_start_frame": 144,
                "source_end_frame_exclusive": 288,
                "local_start_frame": 0,
                "local_end_frame_exclusive": 144,
                "physical_segment_id": "h3-authored-shot-1:segment-2",
                "published_start_frame": 144,
                "published_end_frame_exclusive": 288,
            },
            {
                "segment_index": 2,
                "physical_segment_index": 2,
                "source_start_frame": 288,
                "source_end_frame_exclusive": 480,
                "local_start_frame": 0,
                "local_end_frame_exclusive": 192,
                "physical_segment_id": "h3-authored-shot-1:segment-3",
                "published_start_frame": 288,
                "published_end_frame_exclusive": 480,
            },
        ])
        validate_h3_shot_plan_seal(plan)

    def test_generic_timed_action_continues_without_repeating_dialogue(self):
        dialogue = "<d>[English] Keep moving.</d>"
        action = "An adult courier crosses the long hall carrying a sealed case."
        plan = plan_h3_native_shots(
            global_prompt=f"[0-18s] {action} {dialogue}",
            clip_frame_counts=[144, 144, 144],
            fps=24,
        )

        self.assertEqual(sum(action in item for item in plan["clip_prompts"]), 3)
        self.assertEqual(sum(dialogue in item for item in plan["clip_prompts"]), 1)
        self.assertNotIn(
            "CONTINUATION OF AUTHORED ACTION", plan["clip_prompts"][0],
        )
        self.assertTrue(all(
            "CONTINUATION OF AUTHORED ACTION" in plan["clip_prompts"][index]
            for index in (1, 2)
        ))
        self.assertEqual(
            [item["segment_index"] for item in plan["event_ownership"][0][
                "continuation_slices"
            ]],
            [1, 2],
        )

    def test_generic_timed_final_blocking_is_final_segment_owned_once(self):
        action = "An adult traveler walks through the hall."
        blocking = "The traveler sits beside the window."
        plan = plan_h3_native_shots(
            global_prompt=f"[0-4s] {action} FINAL BLOCKING: {blocking}",
            clip_frame_counts=[48, 48],
            fps=24,
        )

        self.assertEqual(sum(action in item for item in plan["clip_prompts"]), 2)
        self.assertEqual(sum(blocking in item for item in plan["clip_prompts"]), 1)
        self.assertNotIn(blocking, plan["clip_prompts"][0])
        self.assertIn(blocking, plan["clip_prompts"][1])
        final_event = next(
            item for item in plan["event_ownership"]
            if item["kind"] == "final_blocking"
        )
        self.assertEqual(final_event["owner_segment_index"], 1)

    def test_timed_final_only_markers_do_not_become_actions(self):
        blocking = "The traveler sits beside the window."
        for prompt in (
            f"[0-4s] FINAL BLOCKING: {blocking}",
            f"[Shot 1] At 4 seconds, FINAL BLOCKING: {blocking}",
        ):
            with self.subTest(prompt=prompt):
                plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    clip_frame_counts=[48, 48],
                    fps=24,
                )
                self.assertEqual(
                    [item["kind"] for item in plan["event_ownership"]],
                    ["final_blocking"],
                )
                self.assertNotIn("[0-4s]", "\n".join(plan["clip_prompts"]))
                self.assertNotIn("[Shot 1]", "\n".join(plan["clip_prompts"]))
                self.assertTrue(
                    plan["clip_prompts"][-1].endswith(
                        f"FINAL BLOCKING: {blocking}"
                    )
                )

    def test_empty_final_segment_keeps_final_blocking_terminal(self):
        blocking = "The host faces camera."
        plan = plan_h3_native_shots(
            global_prompt=(
                "An adult host enters.\n"
                f"FINAL BLOCKING: {blocking}"
            ),
            clip_frame_counts=[124, 124],
            fps=24,
        )
        self.assertTrue(
            plan["clip_prompts"][-1].endswith(f"FINAL BLOCKING: {blocking}")
        )

    def test_distinct_final_sources_preserve_punctuation_without_double_periods(self):
        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-4s] shot_name: Archive | "
            "audiovisual_description: <Subject 1> studies a ledger. "
            "FINAL BLOCKING: The ledger closes. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            structured_shots=[{"closing_blocking": "The lamp dims."}],
            clip_frame_counts=[96],
            fps=24,
        )
        final = next(
            item for item in plan["event_ownership"]
            if item["kind"] == "final_blocking"
        )
        self.assertEqual(
            final["executable_payload"],
            "The lamp dims. The ledger closes",
        )
        self.assertNotIn("..", plan["clip_prompts"][0])

    def test_generic_distinct_final_sources_are_joined_once(self):
        plan = plan_h3_native_shots(
            global_prompt=(
                "An adult host walks. FINAL BLOCKING: Authored ending."
            ),
            structured_shots=[{
                "closing_blocking": "Structured ending.",
            }],
            clip_frame_counts=[48, 48],
            fps=24,
        )
        final = next(
            item for item in plan["event_ownership"]
            if item["kind"] == "final_blocking"
        )
        self.assertEqual(
            final["executable_payload"],
            "Structured ending. Authored ending.",
        )
        self.assertEqual(
            sum(
                "Structured ending. Authored ending." in prompt
                for prompt in plan["clip_prompts"]
            ),
            1,
        )

    def test_generic_duplicate_final_sources_ignore_terminal_punctuation(self):
        for authored, structured in (
            ("Host sits!", "host sits"),
            ("Host sits?", "HOST SITS."),
        ):
            with self.subTest(authored=authored, structured=structured):
                plan = plan_h3_native_shots(
                    global_prompt=(
                        "An adult host walks. "
                        f"FINAL BLOCKING: {authored}"
                    ),
                    structured_shots=[{
                        "closing_blocking": structured,
                    }],
                    clip_frame_counts=[48, 48],
                    fps=24,
                )
                final = next(
                    item for item in plan["event_ownership"]
                    if item["kind"] == "final_blocking"
                )
                self.assertEqual(final["executable_payload"], authored)
                self.assertEqual(
                    sum(
                        authored in prompt
                        for prompt in plan["clip_prompts"]
                    ),
                    1,
                )

    def test_structured_dialogue_does_not_consume_inline_blocking(self):
        blocking = "Ready."
        dialogue = "<d>[English] Ready.</d>"
        for marker in ("OPENING", "FINAL"):
            with self.subTest(marker=marker):
                plan = plan_h3_native_shots(
                    global_prompt=(
                        "[0-4s] An adult host waits. "
                        f"{marker} BLOCKING: {blocking}"
                    ),
                    structured_shots=[{
                        "dialogue_beats": [{"spoken_text": blocking}],
                    }],
                    clip_frame_counts=[48, 48],
                    fps=24,
                )
                self.assertEqual(
                    sum(dialogue in prompt for prompt in plan["clip_prompts"]),
                    1,
                )
                if marker == "FINAL":
                    final = next(
                        item for item in plan["event_ownership"]
                        if item["kind"] == "final_blocking"
                    )
                    self.assertEqual(final["executable_payload"], blocking)
                else:
                    self.assertNotIn(
                        "OPENING BLOCKING", plan["clip_prompts"][1],
                    )

    def test_structured_dialogue_after_inline_opening_is_tagged_in_place(self):
        dialogue = "<d>[English] Hello.</d>"
        plan = plan_h3_native_shots(
            global_prompt=(
                "[0-4s] OPENING BLOCKING: Ready. The host says Hello."
            ),
            structured_shots=[{
                "dialogue_beats": [{"spoken_text": "Hello."}],
            }],
            clip_frame_counts=[48, 48],
            fps=24,
        )
        self.assertEqual(sum(item.count(dialogue) for item in plan["clip_prompts"]), 1)
        self.assertIn(f"The host says {dialogue}", plan["clip_prompts"][0])
        self.assertNotIn("Hello.", plan["clip_prompts"][1])
        self.assertNotIn("OPENING BLOCKING", plan["clip_prompts"][1])

    def test_dialogue_closed_opening_boundary_preserves_following_action(self):
        action_dialogue = "<d>[English] Hello.</d>"
        opening_dialogue = "<d>[English] Ready. Go.</d>"
        plan = plan_h3_native_shots(
            global_prompt=(
                "[0-4s] OPENING BLOCKING: The host says "
                f"{opening_dialogue} The host says Hello."
            ),
            structured_shots=[{
                "dialogue_beats": [{"spoken_text": "Hello."}],
            }],
            clip_frame_counts=[48, 48],
            fps=24,
        )
        self.assertEqual(
            sum(item.count(opening_dialogue) for item in plan["clip_prompts"]),
            1,
        )
        self.assertEqual(
            sum(item.count(action_dialogue) for item in plan["clip_prompts"]),
            1,
        )
        self.assertIn(f"The host says {action_dialogue}", plan["clip_prompts"][0])
        self.assertIn("The host says", plan["clip_prompts"][1])
        self.assertNotIn("OPENING BLOCKING", plan["clip_prompts"][1])
        self.assertNotIn("Ready", plan["clip_prompts"][1])
        self.assertNotIn("Go", plan["clip_prompts"][1])
        self.assertNotIn("Hello.", plan["clip_prompts"][1])
        self.assertNotRegex(plan["clip_prompts"][1], r"<\s*/?\s*d\s*>")

    def test_ambiguous_inline_opening_dialogue_fails_closed(self):
        for suffix in (
            "The host says Hello.",
            "while remaining seated. The host walks.",
            "and keeps watching. The host walks.",
            "then remains seated. The host walks.",
            "“The host says Hello.”",
            "(The host says Hello.)",
            "— The host says Hello.",
            "2 seconds later, the host walks.",
            "Élodie says Hello.",
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(
                    H3ShotPlanError, "requires terminal punctuation",
                ):
                    plan_h3_native_shots(
                        global_prompt=(
                            "[0-4s] OPENING BLOCKING: The host says "
                            f"<d>[English] Ready</d> {suffix}"
                        ),
                        structured_shots=[{
                            "dialogue_beats": [{"spoken_text": "Hello."}],
                        }],
                        clip_frame_counts=[48, 48],
                        fps=24,
                    )

    def test_explicit_final_marker_closes_unpunctuated_opening_dialogue(self):
        opening_dialogue = "<d>[English] Ready</d>"
        blocking = "The host sits."
        plan = plan_h3_native_shots(
            global_prompt=(
                "[0-4s] OPENING BLOCKING: The host says "
                f"{opening_dialogue} FINAL BLOCKING: {blocking}"
            ),
            clip_frame_counts=[48, 48],
            fps=24,
        )
        self.assertEqual(
            sum(item.count(opening_dialogue) for item in plan["clip_prompts"]),
            1,
        )
        self.assertEqual(
            sum(item.count(blocking) for item in plan["clip_prompts"]), 1,
        )
        self.assertNotIn("OPENING BLOCKING", plan["clip_prompts"][1])
        self.assertTrue(
            plan["clip_prompts"][-1].endswith(f"FINAL BLOCKING: {blocking}")
        )

    def test_resolved_opening_does_not_validate_later_dialogue_as_metadata(self):
        for prompt in (
            (
                "[0-4s] OPENING BLOCKING: The host says "
                "<d>[English] Ready</d>. The host walks."
            ),
            (
                "[0-4s] OPENING BLOCKING: The host is seated. "
                "The host says <d>[English] Ready</d> and walks."
            ),
            (
                "[0-4s] OPENING BLOCKING: The host says "
                "<d>[English] Ready.</d> The host says "
                "<d>[English] Hello</d> and walks."
            ),
        ):
            with self.subTest(prompt=prompt):
                plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    clip_frame_counts=[48, 48],
                    fps=24,
                )
                self.assertNotIn(
                    "OPENING BLOCKING", plan["clip_prompts"][1],
                )
                self.assertNotRegex(
                    plan["clip_prompts"][1], r"<\s*/?\s*d\s*>",
                )
                self.assertIn("walks", plan["clip_prompts"][1])

    def test_point_events_cannot_overlap_effective_one_frame_geometry(self):
        for prompt in (
            (
                "At 5 seconds, an adult host waves.\n"
                "At 5 seconds, the host nods."
            ),
            (
                "[0-10s] An adult host walks.\n"
                "At 5 seconds, the host waves."
            ),
        ):
            with self.subTest(prompt=prompt):
                with self.assertRaisesRegex(H3ShotPlanError, "overlap"):
                    plan_h3_native_shots(
                        global_prompt=prompt,
                        clip_frame_counts=[240],
                        fps=24,
                    )

    def test_structured_opening_dialogue_is_removed_exactly_from_continuations(self):
        dialogue = "<d>[English] Ready.</d>"
        opening = (
            f"The host says {dialogue}, then repeats {dialogue}"
        )
        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: <Subject 1> studies a ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            structured_shots=[{"spatial_setup": opening}],
            clip_frame_counts=[240, 240],
            fps=24,
        )
        self.assertEqual(sum(item.count(dialogue) for item in plan["clip_prompts"]), 2)
        self.assertNotIn("Opening blocking", plan["clip_prompts"][1])
        self.assertNotIn("The host says", plan["clip_prompts"][1])

    def test_structured_dialogue_cannot_consume_blocking_metadata(self):
        blocking = "Ready."
        dialogue = "<d>[English] Ready.</d>"
        plan = plan_h3_native_shots(
            global_prompt=(
                "An adult host waits beside the desk.\n"
                f"FINAL BLOCKING: {blocking}"
            ),
            structured_shots=[{
                "dialogue_beats": [{"spoken_text": "Ready."}],
            }],
            clip_frame_counts=[124, 124],
            fps=24,
        )

        self.assertEqual(sum(dialogue in item for item in plan["clip_prompts"]), 1)
        self.assertIn(f"FINAL BLOCKING: {blocking}", plan["clip_prompts"][-1])
        final_event = next(
            item for item in plan["event_ownership"]
            if item["kind"] == "final_blocking"
        )
        self.assertEqual(final_event["executable_payload"], blocking)

    def test_canonical_action_dialogue_markers_are_literal_and_owned_once(self):
        for marker in ("OPENING BLOCKING:", "FINAL BLOCKING:"):
            dialogue = f"<d>[English] {marker} remain literal.</d>"
            prompt = (
                "subject_definitions: <Subject 1> is an adult archivist.\n\n"
                "integrated_multimodal_description:\n"
                "[Shot 1] [0s-20s] shot_name: Archive | "
                "audiovisual_description: <Subject 1> studies a ledger while saying "
                f"{dialogue} | dialogue_and_vocalizations: none\n"
                "overall_soundscape: Quiet room tone.\n"
                "non_diegetic_music: N/A"
            )
            with self.subTest(marker=marker):
                plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    clip_frame_counts=[240, 240],
                    fps=24,
                )
                self.assertEqual(
                    sum(dialogue in item for item in plan["clip_prompts"]), 1,
                )
                self.assertFalse(any(
                    item["kind"] == "final_blocking"
                    for item in plan["event_ownership"]
                ))
                self.assertIn("studies a ledger", plan["clip_prompts"][1])

    def test_sparse_source_indices_fail_before_contract_commit(self):
        with self.assertRaisesRegex(H3ShotPlanError, "dense from zero"):
            plan_h3_native_shots(
                global_prompt="Unused.\nUsed.",
                source_prompts=["Unused.", "Used."],
                source_indices=[1],
                clip_frame_counts=[124],
                fps=24,
            )


if __name__ == "__main__":
    unittest.main()
