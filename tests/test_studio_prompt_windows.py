"""Studio global-timestamp prompt parsing and exact window mapping tests."""
from __future__ import annotations

import os
import ast
import json
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

# Load the dependency-light module directly. Importing shared.utils first
# executes its package initializer, which intentionally imports Torch-backed
# solver modules that the lightweight CI job does not install.
_PROMPT_PARSER_PATH = Path(APP) / "shared" / "utils" / "prompt_parser.py"
_PROMPT_PARSER_SPEC = importlib.util.spec_from_file_location(
    "maestro_prompt_parser_test", _PROMPT_PARSER_PATH,
)
if _PROMPT_PARSER_SPEC is None or _PROMPT_PARSER_SPEC.loader is None:
    raise RuntimeError("Could not load prompt_parser.py")
_prompt_parser = importlib.util.module_from_spec(_PROMPT_PARSER_SPEC)
_PROMPT_PARSER_SPEC.loader.exec_module(_prompt_parser)
build_global_timeline_clip_prompts = _prompt_parser.build_global_timeline_clip_prompts
build_global_timeline_window_prompts = _prompt_parser.build_global_timeline_window_prompts
classify_timeline_clip_boundaries = _prompt_parser.classify_timeline_clip_boundaries
has_global_timeline = _prompt_parser.has_global_timeline
plan_consecutive_clip_frames = _prompt_parser.plan_consecutive_clip_frames
plan_transition_aware_clip_frames = _prompt_parser.plan_transition_aware_clip_frames
parse_global_timeline_prompt = _prompt_parser.parse_global_timeline_prompt
sliding_window_prompt_ranges = _prompt_parser.sliding_window_prompt_ranges


def _h3_align(value, *, minimum=124, maximum=345):
    value = max(minimum, min(maximum, int(value)))
    delta = (value - 5) % 17
    if delta:
        value += 17 - delta
    if value > maximum:
        value -= 17
    return value


class GlobalTimelineParserTests(unittest.TestCase):
    def test_supported_clock_seconds_and_point_syntax(self):
        prompt = "\n".join([
            "Cinematic natural light; keep the same protagonist.",
            "[00:00-00:12] Establish the station.",
            "(12-24s): Track the protagonist onto the train.",
            "at 00:37: The doors close.",
        ])
        global_lines, events = parse_global_timeline_prompt(prompt)
        self.assertEqual(global_lines, ["Cinematic natural light; keep the same protagonist."])
        self.assertEqual(
            [(event["kind"], event["start"], event["end"]) for event in events],
            [("range", 0.0, 12.0), ("range", 12.0, 24.0), ("point", 37.0, 37.0)],
        )
        self.assertTrue(has_global_timeline(prompt))

    def test_malformed_or_reversed_ranges_are_preserved_as_global_direction(self):
        prompt = "[ten-20s] keep this literal\n[12-4s] reverse intentionally\n1-2 actors cross frame"
        global_lines, events = parse_global_timeline_prompt(prompt)
        self.assertEqual(events, [])
        self.assertEqual(global_lines, prompt.splitlines())
        self.assertFalse(has_global_timeline(prompt))

    def test_h3_sectioned_shot_format_and_comma_timestamp_are_supported(self):
        prompt = "\n".join([
            "subject_definitions:",
            "<Subject 1>: An adult astronaut in an orange flight suit.",
            "summary:",
            "Maintain identity and wardrobe.",
            "detailed_description:",
            "[Shot 1] The astronaut crosses the quiet hangar.",
            "[Shot 2] At 00:15.000, the camera cuts to a close-up.",
            "[Shot 3 | 00:23.500] She closes the helmet visor.",
        ])
        global_lines, events = parse_global_timeline_prompt(prompt)
        self.assertEqual(
            global_lines,
            [
                "subject_definitions:",
                "<Subject 1>: An adult astronaut in an orange flight suit.",
                "summary:",
                "Maintain identity and wardrobe.",
                "detailed_description:",
            ],
        )
        self.assertEqual(
            [
                (event["kind"], event["start"])
                for event in sorted(events, key=lambda item: item["order"])
            ],
            [("shot", 0.0), ("shot", 15.0), ("shot", 23.5)],
        )
        self.assertTrue(has_global_timeline(prompt))

    def test_inline_h3_context_ir_shots_use_the_same_timeline_parser(self):
        prompt = (
            "integrated_multimodal_description: [Shot 1] At 0.00 seconds, "
            "an adult singer enters. [Shot 2] At 00:15.000, cut to the "
            "same singer at the piano. [Shot 3] At 30.00 seconds, she bows."
        )
        global_lines, events = parse_global_timeline_prompt(prompt)
        self.assertEqual(global_lines, ["integrated_multimodal_description:"])
        self.assertEqual(
            [(item["kind"], item["start"]) for item in events],
            [("shot", 0.0), ("shot", 15.0), ("shot", 30.0)],
        )
        self.assertTrue(has_global_timeline(prompt))

    def test_inline_h3_marker_shaped_dialogue_is_not_split_as_a_shot(self):
        dialogue = "<d>[English] I remember [Scene 1] and [Scene 2]</d>"
        prompt = (
            f"[Shot 1] A person says {dialogue} "
            "[Shot 2] At 00:10.000, they leave."
        )
        _, events = parse_global_timeline_prompt(prompt)

        self.assertEqual(len(events), 2)
        ordered = sorted(events, key=lambda event: event["order"])
        self.assertEqual([event["start"] for event in ordered], [0.0, 10.0])
        self.assertIn(dialogue, ordered[0]["text"])

    def test_multiline_dialogue_timeline_markers_remain_opaque(self):
        dialogue = (
            "<d>[English] I remember.\n"
            "[Scene 9] At 00:05.000, these are spoken words.\n"
            "Done.</d>"
        )
        prompt = (
            f"[Shot 1] At 00:00.000, she says {dialogue}\n"
            "[Shot 2] At 00:10.000, she leaves."
        )
        _, events = parse_global_timeline_prompt(prompt)

        self.assertEqual(
            [(event["start"], event["text"]) for event in events],
            [
                (0.0, f"[Shot 1] she says {dialogue}"),
                (10.0, "[Shot 2] she leaves."),
            ],
        )

    def test_untimed_shot_one_remains_literal_without_a_timed_shot(self):
        prompt = "Global direction.\n[Shot 1] One native scene with no timeline."
        global_lines, events = parse_global_timeline_prompt(prompt)
        self.assertEqual(global_lines, prompt.splitlines())
        self.assertEqual(events, [])

    def test_clip_boundaries_distinguish_continuation_cut_and_soft_transition(self):
        self.assertEqual(
            [item["type"] for item in classify_timeline_clip_boundaries(
                "[Shot 1] Start.\n[Shot 2] At 00:10, camera cuts to a close-up.\n"
                "[Shot 3] At 00:20, cross-dissolve to dawn.",
                clip_frame_counts=[240, 240, 240],
                fps=24,
            )],
            ["cut", "transition"],
        )
        self.assertEqual(
            classify_timeline_clip_boundaries(
                "[Shot 1] Start.\n[Shot 2] At 00:10, same shot, camera continues.",
                clip_frame_counts=[240, 240],
                fps=24,
            )[0]["type"],
            "continuous",
        )

    def test_cut_soon_after_join_marks_a_precut_lead_in(self):
        boundary = classify_timeline_clip_boundaries(
            "[Shot 1] Start.\n[Shot 2] At 00:10.5, cut to the reverse angle.",
            clip_frame_counts=[240, 240],
            fps=24,
        )[0]
        self.assertEqual(boundary["type"], "precut")

    def test_transition_aware_plan_places_join_before_authored_cut(self):
        plan = plan_transition_aware_clip_frames(
            480,
            prompt="[Shot 1] Begin.\n[Shot 2] At 00:15, cut to the exterior.",
            fps=24,
            minimum_frames=107,
            maximum_frames=345,
            align_frame_count=lambda value: _h3_align(
                value, minimum=107, maximum=345,
            ),
        )
        self.assertEqual(len(plan), 2)
        self.assertGreaterEqual(sum(plan), 480)
        # 345 frames = 14.375s, the closest legal boundary before a 15s cut.
        self.assertEqual(plan[0], 345)
        boundary = classify_timeline_clip_boundaries(
            "[Shot 1] Begin.\n[Shot 2] At 00:15, cut to the exterior.",
            clip_frame_counts=plan,
            fps=24,
        )[0]
        self.assertEqual(boundary["type"], "precut")


class SlidingTimelineMappingTests(unittest.TestCase):
    def test_exact_ranges_include_reuse_and_final_partial_tail(self):
        self.assertEqual(
            sliding_window_prompt_ranges(45, 20, discard_last_frames=0, reuse_frames=5),
            [(0, 19), (15, 34), (30, 44)],
        )
        self.assertEqual(
            sliding_window_prompt_ranges(40, 20, discard_last_frames=4, reuse_frames=5),
            [(0, 15), (11, 26), (22, 37), (33, 39)],
        )

    def test_frame_layout_uses_backend_fps_without_frontend_second_rounding(self):
        for fps in (24, 25):
            with self.subTest(fps=fps):
                total = 45 * fps
                ranges = sliding_window_prompt_ranges(
                    total,
                    20 * fps,
                    discard_last_frames=8,
                    reuse_frames=9,
                )
                self.assertEqual(len(ranges), 3)
                self.assertEqual(ranges[0][0], 0)
                self.assertEqual(ranges[-1][1], total - 1)
                self.assertLess(ranges[1][0], ranges[0][1] + 1)
                self.assertLess(ranges[2][0], ranges[1][1] + 1)

    def test_global_ranges_are_clipped_and_rebased_per_backend_window(self):
        prompt = "\n".join([
            "Same character, wardrobe, grade, and location throughout.",
            "[0-12s] Establish the station.",
            "[12-24s] Follow the protagonist onto the train.",
            "[24-37s] Accelerate through the tunnel.",
            "[37-45s] Arrive in morning light.",
        ])
        windows = build_global_timeline_window_prompts(
            prompt,
            total_frames=45,
            fps=1,
            window_size=20,
            discard_last_frames=0,
            reuse_frames=5,
        )
        self.assertEqual(len(windows), 3)
        for window in windows:
            self.assertIn("Same character, wardrobe, grade, and location throughout.", window)
        self.assertIn("[0-12s] Establish the station.", windows[0])
        self.assertIn("[12-20s] Follow the protagonist onto the train.", windows[0])
        self.assertNotIn("tunnel", windows[0])

        self.assertIn("[0-9s] Follow the protagonist onto the train.", windows[1])
        self.assertIn("[9-20s] Accelerate through the tunnel.", windows[1])
        self.assertNotIn("morning light", windows[1])

        self.assertIn("[0-7s] Accelerate through the tunnel.", windows[2])
        self.assertIn("[7-15s] Arrive in morning light.", windows[2])

    def test_exact_boundary_point_moves_to_next_window_local_time(self):
        windows = build_global_timeline_window_prompts(
            "Global camera continuity.\nat 20 seconds: Cut to the platform.",
            total_frames=40,
            fps=1,
            window_size=20,
            discard_last_frames=0,
            reuse_frames=0,
        )
        self.assertNotIn("Cut to the platform", windows[0])
        self.assertIn("(at 0 seconds: Cut to the platform.)", windows[1])

    def test_non_sliding_and_untimed_prompts_keep_legacy_path(self):
        self.assertIsNone(build_global_timeline_window_prompts(
            "[0-20s] One window.", total_frames=20, fps=1, window_size=20,
        ))
        self.assertIsNone(build_global_timeline_window_prompts(
            "First plain line\nSecond plain line",
            total_frames=45,
            fps=1,
            window_size=20,
            reuse_frames=5,
        ))

    def test_out_of_duration_ranges_do_not_leave_a_blank_window_prompt(self):
        windows = build_global_timeline_window_prompts(
            "[90-100s] Future action",
            total_frames=45,
            fps=1,
            window_size=20,
            reuse_frames=5,
        )
        self.assertEqual(len(windows), 3)
        self.assertTrue(all("Continue the established action" in window for window in windows))


class H3LongStudioPlanningTests(unittest.TestCase):
    def test_planner_uses_minimum_clip_count_and_h3_17n_plus_5_grid(self):
        plan = plan_consecutive_clip_frames(
            720,
            minimum_frames=124,
            maximum_frames=345,
            align_frame_count=_h3_align,
        )
        self.assertEqual(len(plan), 3)
        self.assertGreaterEqual(sum(plan), 720)
        self.assertLess(sum(plan) - 720, 17 * len(plan))
        self.assertTrue(all(124 <= frames <= 345 for frames in plan))
        self.assertTrue(all(frames % 17 == 5 for frames in plan))

    def test_studio_persists_per_segment_publication_geometry(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        body = {
            "model_type": "minimax_h3",
            "video_length": 480,
            "prompt": "Beat one. Beat two. Beat three.",
        }
        plan = prepare(body)

        self.assertEqual(len(plan["clip_trim_tail_frames"]), plan["clip_count"])
        self.assertEqual(len(plan["clip_published_frames"]), plan["clip_count"])
        self.assertEqual(
            [
                generated - trim
                for generated, trim in zip(
                    plan["clip_frames"], plan["clip_trim_tail_frames"],
                )
            ],
            plan["clip_published_frames"],
        )
        self.assertEqual(sum(plan["clip_published_frames"]), 480)
        self.assertEqual(
            plan["shot_plan"]["clip_trim_tail_frames"],
            plan["clip_trim_tail_frames"],
        )

    def test_global_timestamps_are_consecutive_and_clip_local(self):
        prompts = build_global_timeline_clip_prompts(
            "\n".join([
                "Keep the same protagonist and grade.",
                "[0-12s] Cross the station.",
                "[12-24s] Board and accelerate.",
                "[24-30s] Emerge into daylight.",
            ]),
            clip_frame_counts=[243, 243, 226],
            fps=24,
        )
        self.assertEqual(len(prompts), 3)
        self.assertIn("[0-10.125s] Cross the station.", prompts[0])
        self.assertIn("[0-1.875s] Cross the station.", prompts[1])
        self.assertIn("[1.875-10.125s] Board and accelerate.", prompts[1])
        self.assertIn("[0-3.75s] Board and accelerate.", prompts[2])
        self.assertIn("[3.75-9.417s] Emerge into daylight.", prompts[2])

    def test_h3_shot_start_applies_until_the_next_global_shot(self):
        prompts = build_global_timeline_clip_prompts(
            "\n".join([
                "subject_definitions:",
                "<Subject 1>: An adult pilot.",
                "detailed_description:",
                "[Shot 1] The pilot walks through the hangar.",
                "[Shot 2] At 00:15.000, cut to the cockpit.",
                "[Shot 3] At 00:25.000: launch into daylight.",
            ]),
            clip_frame_counts=[240, 240, 240],
            fps=24,
        )
        self.assertEqual(len(prompts), 3)
        self.assertIn(
            "[0-10s] [Shot 1] The pilot walks through the hangar.",
            prompts[0],
        )
        self.assertIn(
            "[0-5s] [Shot 1] The pilot walks through the hangar.",
            prompts[1],
        )
        self.assertIn("[5-10s] [Shot 2] cut to the cockpit.", prompts[1])
        self.assertIn("[0-5s] [Shot 2] cut to the cockpit.", prompts[2])
        self.assertIn(
            "[5-10s] [Shot 3] launch into daylight.", prompts[2],
        )

    @staticmethod
    def _load_launch_helpers():
        source_path = os.path.join(APP, "launch.py")
        source = Path(source_path).read_text(encoding="utf-8")
        module = ast.parse(source, filename=source_path)
        helper_names = {
            "_apply_h3_adaptive_checkpoint",
            "_trusted_h3_prepared_plan",
            "_h3_preferred_fl2va_model",
            "_prepare_h3_long_studio_request",
            "_public_h3_long_plan",
            "_public_h3_boundary",
            "_h3_estimate_context",
            "_h3_segment_count_estimate",
            "_plan_h3_adaptive_models",
            "_validate_h3_segment_plan",
            "_expand_h3_longform_outputs",
            "_h3_effective_model_types",
            "_ensure_h3_effective_models_current",
            "_h3_checkpoint_downloaded",
            "_h3_checkpoint_options",
            "_h3_generation_requirements",
            "_validate_h3_sampling_steps",
            "_validate_h3_explicit_multiclip_request",
        }
        helpers = [
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name in helper_names
        ]

        class FakeWgp:
            @staticmethod
            def get_model_def(model_type):
                reference = model_type == "minimax_h3_ref2va"
                return {
                    "name": model_type,
                    "URLs": [f"https://models.invalid/{model_type}.safetensors"],
                    "fps": 24,
                    "frames_minimum": 107 if reference else 124,
                    "frames_steps": 17,
                    "frames_maximum": 345,
                    "frame_alignment_modulus": 17,
                    "frame_alignment_remainder": 5,
                    "minimax_h3_reference_mode": reference,
                    "minimax_h3_conditioning_mode": (
                        "semantic_references" if reference
                        else "first_last_frames"
                    ),
                    "nsfw_only": model_type == "minimax_h3_pinkcherry_fl2va",
                }

            @staticmethod
            def align_model_frame_count(value, model_def):
                return _h3_align(
                    value,
                    minimum=model_def["frames_minimum"],
                    maximum=model_def["frames_maximum"],
                )

        namespace = {
            "math": __import__("math"),
            "wgp": FakeWgp,
            "_H3_LONG_STUDIO_MODELS": {
                "minimax_h3", "minimax_h3_pinkcherry_fl2va",
                "minimax_h3_w4a8_fl2va", "minimax_h3_ref2va",
            },
            "_H3_BASE_FL2VA_MODEL": "minimax_h3",
            "_H3_EXPLICIT_FL2VA_MODEL": "minimax_h3_pinkcherry_fl2va",
            "_H3_W4A8_FL2VA_MODEL": "minimax_h3_w4a8_fl2va",
            "_H3_REF2VA_MODEL": "minimax_h3_ref2va",
            "_H3_CHECKPOINT_CATALOG_ORDER": (
                "minimax_h3",
                "minimax_h3_pinkcherry_fl2va",
                "minimax_h3_w4a8_fl2va",
                "minimax_h3_ref2va",
            ),
            "_H3_FL2VA_MODELS": {
                "minimax_h3", "minimax_h3_pinkcherry_fl2va",
                "minimax_h3_w4a8_fl2va",
            },
            "_MULTI_CLIP_SEPARATOR": "\n---CLIP_BOUNDARY---\n",
            "_check_model_downloaded": lambda model_type: model_type != "minimax_h3_ref2va",
            "_variant_group_downloaded": lambda urls: True,
        }
        exec(compile(ast.Module(body=helpers, type_ignores=[]), source_path, "exec"), namespace)
        return namespace

    def test_adaptive_ref2va_round_trip_preserves_selected_w4a8_fl2va(self):
        helpers = self._load_launch_helpers()
        apply_adaptive = helpers["_apply_h3_adaptive_checkpoint"]
        prepare = helpers["_prepare_h3_long_studio_request"]
        body = {
            "model_type": "minimax_h3_w4a8_fl2va",
            "video_length": 700,
            "prompt": "[0-29.167s] Preserve the subject through the sequence.",
            "image_start": "first.png",
            "image_refs": ["subject.png"],
        }
        self.assertEqual(apply_adaptive(body), "minimax_h3_ref2va")
        self.assertEqual(body["_h3_requested_checkpoint"], "minimax_h3_w4a8_fl2va")
        plan = prepare(body)
        self.assertEqual(
            plan["segment_models"][0]["model_type"],
            "minimax_h3_w4a8_fl2va",
        )
        self.assertIn(
            "minimax_h3_ref2va",
            [item["model_type"] for item in plan["segment_models"]],
        )
        public = helpers["_public_h3_long_plan"](plan)
        self.assertEqual(public["clip_count"], len(plan["segment_models"]))
        self.assertEqual(public["fps"], 24)
        self.assertEqual(public["published_frames"], plan["requested_frames"])
        for segment, generated, published in zip(
            public["segments"],
            plan["clip_frames"],
            plan["clip_published_frames"],
        ):
            self.assertEqual(segment["generated_frames"], generated)
            self.assertEqual(segment["published_frames"], published)
            self.assertEqual(
                segment["generated_duration_seconds"], generated / 24,
            )
            self.assertEqual(
                segment["published_duration_seconds"], published / 24,
            )
        self.assertEqual(
            public["effective_model_count"], len(public["effective_models"]),
        )
        self.assertEqual(
            set(public["effective_models"]),
            {"minimax_h3_w4a8_fl2va", "minimax_h3_ref2va"},
        )

    def test_public_legacy_h3_plan_recovers_published_tail_geometry(self):
        helpers = self._load_launch_helpers()
        public = helpers["_public_h3_long_plan"]({
            "clip_count": 2,
            "clip_frames": [124, 124],
            "segment_models": [
                {"model_type": "minimax_h3", "reason": "base"},
                {"model_type": "minimax_h3_ref2va", "reason": "semantic"},
            ],
            "clip_boundaries": [{
                "type": "cut", "source": "explicit_cut",
                "event": "AUTHORED_BOUNDARY_SENTINEL",
            }],
            "clip_prompt_previews": ["one", "two"],
            "requested_frames": 240,
            "planned_frames": 248,
        })
        self.assertEqual(
            [segment["generated_frames"] for segment in public["segments"]],
            [124, 124],
        )
        self.assertEqual(
            [segment["published_frames"] for segment in public["segments"]],
            [124, 116],
        )
        self.assertNotIn("AUTHORED_BOUNDARY_SENTINEL", repr(public))
        self.assertEqual(
            [segment["published_duration_seconds"] for segment in public["segments"]],
            [124 / 24, 116 / 24],
        )

    def test_director_scene_count_estimate_matches_independent_runtime_plans(self):
        helpers = self._load_launch_helpers()
        context = helpers["_h3_estimate_context"]({
            "model_type": "minimax_h3",
            "duration_seconds": 6,
            "window_seconds": 345 / 24,
            "window_overlap": 0,
            "prompt": "",
            "segment_scenes": [
                {"duration_seconds": 3, "prompt": "First short scene."},
                {"duration_seconds": 3, "prompt": "Second short scene."},
            ],
            "num_inference_steps": 20,
            "resolution": "1344x768",
        })
        estimate = helpers["_h3_segment_count_estimate"](context)
        self.assertEqual(
            (estimate["minimum"], estimate["likely"], estimate["maximum"]),
            (2, 2, 2),
        )
        self.assertEqual(estimate["confidence"], "high")
        self.assertEqual(
            estimate["source"], "deterministic_director_scene_aggregate",
        )

    def test_public_estimate_normalizes_manual_window_seconds_as_ceiling(self):
        helpers = self._load_launch_helpers()
        context = helpers["_h3_estimate_context"]({
            "model_type": "minimax_h3",
            "duration_seconds": 30,
            "window_seconds": 124 / 24,
            "window_overlap": 0,
            "prompt": "Six prompt-driven scenes.",
            "manual_segment_ceiling": True,
            "num_inference_steps": 20,
            "resolution": "1344x768",
        })
        self.assertEqual(context["window_seconds"], 124 / 24)
        self.assertEqual(context["window_overlap"], 0)
        estimate = helpers["_h3_segment_count_estimate"](context)
        self.assertEqual(
            (estimate["minimum"], estimate["likely"], estimate["maximum"]),
            (6, 6, 6),
        )

    def test_estimate_adaptive_ref2va_uses_effective_fl2va_geometry(self):
        helpers = self._load_launch_helpers()
        request = {
            "model_type": "minimax_h3_ref2va",
            "duration_seconds": 30,
            "window_seconds": 107 / 24,
            "window_overlap": 0,
            "prompt": "A continuous text-only sequence.",
            "manual_segment_ceiling": True,
            "num_inference_steps": 20,
            "resolution": "1344x768",
        }
        adaptive = helpers["_h3_estimate_context"]({
            **request,
            "h3_adaptive_conditioning": True,
        })
        with self.assertRaisesRegex(ValueError, "below the model minimum"):
            helpers["_h3_segment_count_estimate"](adaptive)

        pinned = helpers["_h3_estimate_context"]({
            **request,
            "h3_adaptive_conditioning": False,
        })
        estimate = helpers["_h3_segment_count_estimate"](pinned)
        self.assertEqual(estimate["likely"], 7)

    def test_explicit_metadata_preserves_base_and_manual_pinkcherry_selection(self):
        helpers = self._load_launch_helpers()
        apply_adaptive = helpers["_apply_h3_adaptive_checkpoint"]
        prepare = helpers["_prepare_h3_long_studio_request"]
        for selected in (
            "minimax_h3",
            "minimax_h3_pinkcherry_fl2va",
        ):
            with self.subTest(selected=selected):
                body = {
                    "model_type": selected,
                    "explicit_output": True,
                    "video_length": 700,
                    "prompt": "[0-29.167s] Preserve the subject through the sequence.",
                    "image_start": "first.png",
                    "image_refs": ["subject.png"],
                }
                self.assertEqual(apply_adaptive(body), "minimax_h3_ref2va")
                self.assertEqual(body["_h3_requested_checkpoint"], selected)
                plan = prepare(body)
                self.assertEqual(plan["segment_models"][0]["model_type"], selected)
                self.assertIn(
                    "minimax_h3_ref2va",
                    [item["model_type"] for item in plan["segment_models"]],
                )

        direct_base = {"model_type": "minimax_h3", "explicit_output": True}
        self.assertEqual(apply_adaptive(direct_base), "minimax_h3")
        self.assertEqual(direct_base["model_type"], "minimax_h3")

    def test_plan_requirements_report_every_effective_model_and_ref2va_terms(self):
        helpers = self._load_launch_helpers()
        requirements = helpers["_h3_generation_requirements"](
            {"model_type": "minimax_h3_ref2va"},
            {"segment_models": [
                {"model_type": "minimax_h3_w4a8_fl2va"},
                {"model_type": "minimax_h3_ref2va"},
            ]},
        )
        self.assertEqual(
            [item["model_type"] for item in requirements["models"]],
            ["minimax_h3_w4a8_fl2va", "minimax_h3_ref2va"],
        )
        self.assertTrue(requirements["ref2va_terms_required"])
        self.assertFalse(requirements["all_downloaded"])

    def test_clean_install_plan_catalogs_all_managed_h3_checkpoints(self):
        helpers = self._load_launch_helpers()
        helpers["_check_model_downloaded"] = lambda _model: False
        requirements = helpers["_h3_generation_requirements"](
            {"model_type": "minimax_h3"},
            {"segment_models": [{"model_type": "minimax_h3"}]},
        )
        options = requirements["checkpoint_options"]
        self.assertEqual(
            [option["model_type"] for option in options],
            [
                "minimax_h3",
                "minimax_h3_pinkcherry_fl2va",
                "minimax_h3_w4a8_fl2va",
                "minimax_h3_ref2va",
            ],
        )
        self.assertTrue(all(not option["is_downloaded"] for option in options))
        self.assertTrue(all(option["auto_download"] for option in options))
        content_labeled = {
            option["model_type"]: option
            for option in helpers["_h3_checkpoint_options"]()
        }["minimax_h3_pinkcherry_fl2va"]
        self.assertTrue(content_labeled["available"])
        self.assertEqual(content_labeled["unavailable_reason"], "")
        public = helpers["_public_h3_long_plan"]({
            "clip_count": 2,
            "clip_frames": [124, 124],
            "segment_models": [
                {"model_type": "minimax_h3", "reason": "base"},
                {"model_type": "minimax_h3_ref2va", "reason": "semantic"},
            ],
            "clip_boundaries": [{"type": "cut"}],
            "clip_prompt_previews": ["one", "two"],
            "requested_frames": 240,
            "planned_frames": 248,
        }, requirements)
        self.assertEqual(public["checkpoint_options"], options)

    def test_h3_download_readiness_includes_conditioning_encoder(self):
        helpers = self._load_launch_helpers()
        original_wgp = helpers["wgp"]

        class ConditionerWgp(original_wgp):
            @staticmethod
            def get_model_def(model_type):
                definition = dict(original_wgp.get_model_def(model_type))
                definition["text_encoder_URLs"] = ["missing-encoder.safetensors"]
                return definition

        helpers["wgp"] = ConditionerWgp
        helpers["_check_model_downloaded"] = lambda _model: True
        seen = {}
        def unavailable(_urls, *, extra_paths=None):
            seen["extra_paths"] = extra_paths
            return False
        helpers["_variant_group_downloaded"] = unavailable
        self.assertFalse(
            helpers["_h3_checkpoint_downloaded"]("minimax_h3")
        )
        self.assertIsNone(seen["extra_paths"])

        class FolderConditionerWgp(ConditionerWgp):
            @staticmethod
            def get_model_def(model_type):
                definition = dict(ConditionerWgp.get_model_def(model_type))
                definition["text_encoder_folder"] = "minimax_h3"
                return definition

        helpers["wgp"] = FolderConditionerWgp
        helpers["_variant_group_downloaded"] = (
            lambda _urls, *, extra_paths=None: extra_paths == "minimax_h3"
        )
        self.assertTrue(
            helpers["_h3_checkpoint_downloaded"]("minimax_h3")
        )

    def test_trusted_persisted_plan_retains_terms_models_and_update_deduplication(self):
        helpers = self._load_launch_helpers()
        body = {
            "model_type": "minimax_h3_ref2va",
            "_h3_longform": {
                "segment_models": [
                    {"model_type": "minimax_h3_w4a8_fl2va"},
                    {"model_type": "minimax_h3_ref2va"},
                    {"model_type": "minimax_h3_w4a8_fl2va"},
                ],
            },
        }
        self.assertIsNone(helpers["_trusted_h3_prepared_plan"](body))
        trusted = helpers["_trusted_h3_prepared_plan"](
            body, allow_server_prepared=True,
        )
        requirements = helpers["_h3_generation_requirements"](body, trusted)
        self.assertEqual(
            [item["model_type"] for item in requirements["models"]],
            ["minimax_h3_w4a8_fl2va", "minimax_h3_ref2va"],
        )
        self.assertTrue(requirements["ref2va_terms_required"])

        calls = []
        original_wgp = helpers["wgp"]

        class UpdatingWgp(original_wgp):
            @staticmethod
            def get_model_def(model_type):
                definition = dict(original_wgp.get_model_def(model_type))
                definition["model_update"] = {"repo": "test"}
                return definition

        helpers["wgp"] = UpdatingWgp
        helpers["_ensure_versioned_model_current"] = lambda model: (
            calls.append(model) or {"status": "current"}
        )
        helpers["_ensure_versioned_model_current"](
            "minimax_h3_w4a8_fl2va"
        )
        results = helpers["_ensure_h3_effective_models_current"](
            body,
            trusted,
            already_checked={"minimax_h3_w4a8_fl2va"},
        )
        self.assertEqual(
            calls,
            ["minimax_h3_w4a8_fl2va", "minimax_h3_ref2va"],
        )
        self.assertEqual(list(results), ["minimax_h3_ref2va"])

    def test_fixed_h3_conditioning_rejects_incompatible_inputs(self):
        apply_adaptive = self._load_launch_helpers()[
            "_apply_h3_adaptive_checkpoint"
        ]
        with self.assertRaisesRegex(ValueError, "Pinned Ref2VA"):
            apply_adaptive({
                "model_type": "minimax_h3_ref2va",
                "h3_adaptive_conditioning": False,
                "image_start": "edge.png",
            })
        with self.assertRaisesRegex(ValueError, "Pinned FL2VA"):
            apply_adaptive({
                "model_type": "minimax_h3",
                "h3_adaptive_conditioning": False,
                "image_refs": ["character.png"],
            })

    def test_segment_override_validation_uses_effective_model_grid(self):
        validate = self._load_launch_helpers()["_validate_h3_segment_plan"]
        with self.assertRaisesRegex(ValueError, "temporal grid"):
            validate(
                {},
                clip_frames=[125],
                segment_models=[{
                    "model_type": "minimax_h3_w4a8_fl2va",
                    "user_override": True,
                }],
                first_anchor=None,
                last_anchor=None,
            )
        with self.assertRaisesRegex(ValueError, "explicitly drop"):
            validate(
                {"image_refs": ["character.png"]},
                clip_frames=[124],
                segment_models=[{
                    "model_type": "minimax_h3_w4a8_fl2va",
                    "user_override": True,
                }],
                first_anchor=None,
                last_anchor=None,
            )

    def test_fl2va_long_request_becomes_native_clips_with_edge_anchors(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        body = {
            "model_type": "minimax_h3",
            "generation_mode": "video",
            "video_length": 720,
            "prompt": "Global continuity.\n[0-15s] First action.\n[15-30s] Second action.",
            "image_start": "first.png",
            "image_end": "last.png",
            "image_prompt_type": "SE",
        }
        plan = prepare(body)
        self.assertEqual(body["multi_prompts_gen_type"], 3)
        self.assertEqual(plan["continuation"], "last_frame")
        self.assertEqual(len(body["per_clip_frames"]), 4)
        self.assertTrue(all(value % 17 == 5 for value in body["per_clip_frames"]))
        self.assertEqual(body["image_start"], ["first.png", None, None, None])
        self.assertEqual(body["image_end"], [None, None, None, "last.png"])
        self.assertGreaterEqual(plan["final_trim_frames"], 17)
        self.assertEqual(
            sum(body["per_clip_frames"]) - plan["final_trim_frames"],
            720,
        )
        self.assertEqual(
            len(body["prompt"].split("\n---CLIP_BOUNDARY---\n")), 4,
        )
        self.assertEqual(len(body["per_clip_prompts"]), 4)

    def test_ref2va_long_request_keeps_semantic_refs_without_fl2va_anchors(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        body = {
            "model_type": "minimax_h3_ref2va",
            "video_length": 700,
            "prompt": "[0-29.167s] Preserve the referenced character.",
            "image_start": "stale-anchor.png",
            "image_end": "stale-end.png",
            "image_refs": ["character.png"],
            "video_guide": "motion-reference.mp4",
            "audio_guide": "voice-reference.wav",
        }
        plan = prepare(body)
        self.assertEqual(plan["continuation"], "semantic_references")
        self.assertTrue(plan["preserve_generated_audio"])
        self.assertNotIn("image_start", body)
        self.assertNotIn("image_end", body)
        self.assertEqual(body["image_refs"], ["character.png"])
        self.assertEqual(body["video_guide"], "motion-reference.mp4")
        self.assertEqual(body["audio_guide"], "voice-reference.wav")
        self.assertEqual(
            [item["model_type"] for item in plan["segment_models"]],
            ["minimax_h3", "minimax_h3_ref2va", "minimax_h3"],
        )
        self.assertEqual(plan["original_image_start"], "stale-anchor.png")
        self.assertEqual(plan["original_image_end"], "stale-end.png")
        self.assertEqual(
            sum(body["per_clip_frames"]) - plan["final_trim_frames"],
            700,
        )

    def test_manual_h3_segment_size_is_the_backend_plan_ceiling(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        for requested in (192, 200):
            with self.subTest(requested=requested):
                body = {
                    "model_type": "minimax_h3",
                    "video_length": 700,
                    "sliding_window_size": requested,
                    "prompt": "[0-29.167s] One continuous action.",
                }
                plan = prepare(body)
                self.assertEqual(plan["segment_frames_maximum"], 192)
                self.assertEqual(plan["clip_count"], 4)
                self.assertTrue(all(
                    frames <= requested for frames in body["per_clip_frames"]
                ))

    def test_locked_ceiling_below_native_maximum_uses_bounded_h3_planner(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        for model_type in ("minimax_h3", "minimax_h3_ref2va"):
            with self.subTest(model_type=model_type, case="above ceiling"):
                body = {
                    "model_type": model_type,
                    "video_length": 300,
                    "sliding_window_size": 192,
                    "prompt": "Opening action. Reaction. Closing action.",
                }
                plan = prepare(body)
                self.assertIsNotNone(plan)
                self.assertIs(plan, body["_h3_longform"])
                self.assertTrue(plan["manual_segment_ceiling"])
                self.assertEqual(plan["segment_frames_maximum"], 192)
                self.assertGreater(plan["clip_count"], 1)
                self.assertEqual(body["multi_prompts_gen_type"], 3)
                self.assertTrue(all(
                    124 <= frames <= 192 and frames % 17 == 5
                    for frames in body["per_clip_frames"]
                ))

            with self.subTest(model_type=model_type, case="at ceiling"):
                body = {
                    "model_type": model_type,
                    "video_length": 192,
                    "sliding_window_size": 192,
                    "prompt": "One native shot.",
                }
                self.assertIsNone(prepare(body))
                self.assertNotIn("_h3_longform", body)
                self.assertNotEqual(body.get("multi_prompts_gen_type"), 3)

    def test_untimed_profile_segment_pressure_reaches_studio_adapter(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        prompt_20 = "Beat one. Beat two. Beat three. Beat four."
        prompt_30 = "Beat one. Beat two. Beat three. Beat four. Beat five."
        profiles = {
            "draft": {
                "num_inference_steps": 4,
                "resolution": "608x352",
                "custom_settings": {"h3_turbo_profile": "h3_turbo_v4"},
            },
            "fast": {
                "num_inference_steps": 8,
                "resolution": "864x480",
                "custom_settings": {"h3_turbo_profile": "h3_turbo_v4"},
            },
            "high": {
                "num_inference_steps": 20,
                "resolution": "1344x768",
                "custom_settings": {},
            },
        }
        expected = {
            480: {"draft": 3, "fast": 2, "high": 2},
            720: {"draft": 4, "fast": 3, "high": 3},
        }
        for frames, prompt in ((480, prompt_20), (720, prompt_30)):
            for profile, settings in profiles.items():
                with self.subTest(frames=frames, profile=profile):
                    body = {
                        "model_type": "minimax_h3",
                        "video_length": frames,
                        "prompt": prompt,
                        **settings,
                    }
                    plan = prepare(body)
                    self.assertEqual(plan["clip_count"], expected[frames][profile])
                    self.assertEqual(plan["segment_policy"]["profile_id"], profile)
                    self.assertEqual(
                        plan["shot_plan"]["segment_policy"],
                        plan["segment_policy"],
                    )

    def test_timestamped_studio_plan_ignores_profile_pressure(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        prompt = "[0-15s] First action.\n[15-30s] Second action."
        plans = []
        for settings in (
            {
                "num_inference_steps": 4,
                "resolution": "608x352",
                "custom_settings": {"h3_turbo_profile": "h3_turbo_v4"},
            },
            {
                "num_inference_steps": 20,
                "resolution": "1344x768",
                "custom_settings": {},
            },
        ):
            body = {
                "model_type": "minimax_h3",
                "video_length": 720,
                "prompt": prompt,
                **settings,
            }
            plan = prepare(body)
            plans.append((plan["clip_frames"], body["per_clip_prompts"]))
        self.assertEqual(plans[0], plans[1])

    def test_30s_cut_alignment_survives_final_frame_tail_reservation(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        prompt = (
            "subject_definitions:\n<Subject 1>: an adult pilot\n"
            "[Shot 1] The pilot crosses the hangar without a cut.\n"
            "[Shot 2] At 00:15.000, cut to the cockpit."
        )
        for final_frame, expected_clips in ((None, 4), ("last.png", 4)):
            with self.subTest(final_frame=bool(final_frame)):
                body = {
                    "model_type": "minimax_h3",
                    "generation_mode": "video",
                    "video_length": 720,
                    "sliding_window_size": 192,
                    "prompt": prompt,
                    **({"image_end": final_frame} if final_frame else {}),
                }
                plan = prepare(body)

                self.assertEqual(plan["clip_count"], expected_clips)
                self.assertEqual(plan["segment_frames_maximum"], 192)
                self.assertTrue(all(
                    124 <= frames <= 192 and frames % 17 == 5
                    for frames in plan["clip_frames"]
                ))
                self.assertEqual(
                    sum(plan["clip_frames"]) - plan["final_trim_frames"],
                    720,
                )
                cut_index, cut_boundary = next(
                    (index, boundary)
                    for index, boundary in enumerate(plan["clip_boundaries"])
                    if boundary["type"] in {"precut", "cut"}
                )
                self.assertEqual(cut_boundary["at_seconds"], 15.0)
                self.assertEqual(cut_boundary["source"], "explicit_cut")
                self.assertEqual(len(body["per_clip_prompts"]), expected_clips)
                self.assertGreater(len(set(body["per_clip_prompts"])), 1)
                events = plan["shot_plan"]["event_ownership"]
                for event_text in ("crosses the hangar", "cut to the cockpit"):
                    event = next(
                        item for item in events
                        if event_text in item["executable_payload"]
                    )
                    self.assertEqual(
                        sum(
                            event_text in item
                            for item in body["per_clip_prompts"]
                        ),
                        1 + len(event["continuation_slices"]),
                    )
                semantic = plan["shot_plan"]["semantic_shots"]
                self.assertEqual(len(semantic), 1)
                self.assertTrue(
                    semantic[0]["prompt_rewrite_for_physical_split"],
                )
                self.assertEqual(
                    [shot["execution_cursor_frame"]
                     for shot in plan["shot_plan"]["shots"]],
                    [0, *[
                        sum(plan["clip_published_frames"][:index])
                        for index in range(1, expected_clips)
                    ]],
                )
                if final_frame:
                    self.assertGreaterEqual(plan["final_trim_frames"], 17)
                    self.assertEqual(body["image_end"][-1], final_frame)
                    self.assertTrue(all(
                        anchor is None for anchor in body["image_end"][:-1]
                    ))
                    self.assertEqual(
                        plan["segment_models"][-1]["reason"],
                        "supplied final-frame anchor",
                    )

    def test_output_count_expands_variant_major_independent_chains(self):
        expand = self._load_launch_helpers()["_expand_h3_longform_outputs"]
        ids = iter(range(100, 200))
        base = []
        for index in range(3):
            base.append({
                "id": index,
                "params": {
                    "seed": 7,
                    "repeat_generation": 2,
                    **({"_continuation": True} if index else {}),
                    "multi_clip_info": {
                        "group_id": "base",
                        "index": index,
                        "total": 3,
                    },
                },
                "plugin_data": {},
            })
        expanded = expand(
            base,
            group_id="base",
            output_count=2,
            base_seed=7,
            allocate_task_id=lambda: next(ids),
        )
        self.assertEqual(len(expanded), 6)
        self.assertEqual(
            [task["params"]["multi_clip_info"]["group_id"] for task in expanded],
            ["base_output_1"] * 3 + ["base_output_2"] * 3,
        )
        self.assertEqual(
            [task["params"]["seed"] for task in expanded],
            [7, 7, 7, 8, 8, 8],
        )
        self.assertTrue(all(task["params"]["repeat_generation"] == 1 for task in expanded))
        self.assertNotIn("_continuation", expanded[0]["params"])
        self.assertTrue(expanded[1]["params"]["_continuation"])
        self.assertNotIn("_continuation", expanded[3]["params"])
        self.assertTrue(expanded[4]["params"]["_continuation"])

    def test_native_length_h3_request_is_untouched(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        body = {
            "model_type": "minimax_h3",
            "video_length": 345,
            "prompt": "One native clip.",
        }
        original = dict(body)
        self.assertIsNone(prepare(body))
        self.assertEqual(body, original)

    def test_director_seamless_long_h3_uses_same_automatic_planner(self):
        prepare = self._load_launch_helpers()["_prepare_h3_long_studio_request"]
        body = {
            "model_type": "minimax_h3",
            "video_length": 600,
            "prompt": "One seamless Director scene.",
            "image_mode": 2,
            "_director_pipeline_id": "director-test",
        }
        plan = prepare(body)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["clip_count"], 2)
        self.assertEqual(body["multi_prompts_gen_type"], 3)

    def test_explicit_director_multiclip_cannot_silently_clamp_h3_scene(self):
        validate = self._load_launch_helpers()[
            "_validate_h3_explicit_multiclip_request"
        ]
        with self.assertRaisesRegex(ValueError, "limited to 345 frames"):
            validate({
                "model_type": "minimax_h3",
                "multi_prompts_gen_type": 3,
                "per_clip_frames": [243, 500],
            })
        validate({
            "model_type": "minimax_h3",
            "multi_prompts_gen_type": 3,
            "per_clip_frames": [243, 345],
        })

    def test_h3_primary_steps_are_validated_before_runtime(self):
        validate = self._load_launch_helpers()["_validate_h3_sampling_steps"]
        for model_type in (
            "minimax_h3",
            "minimax_h3_pinkcherry_fl2va",
            "minimax_h3_w4a8_fl2va",
            "minimax_h3_ref2va",
        ):
            with self.subTest(model_type=model_type):
                with self.assertRaisesRegex(ValueError, "between 2 and 50"):
                    validate({"model_type": model_type, "num_inference_steps": 1})
                for steps in (2, 20, 50):
                    body = {"model_type": model_type, "num_inference_steps": steps}
                    self.assertEqual(validate(body), steps)
                    self.assertEqual(body["num_inference_steps"], steps)
        body = {"model_type": "ltx2_22B_distilled", "num_inference_steps": 1}
        self.assertIsNone(validate(body))
        self.assertEqual(body["num_inference_steps"], 1)

    def test_h3_step_validation_covers_plan_submit_and_worker_ingress(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(launch.count("_validate_h3_sampling_steps("), 3)
        plan = launch[launch.index("async def preview_generation_plan"):]
        submit = launch[launch.index("async def generate(request: Request)"):]
        submission_planner = launch[
            launch.index("def _plan_generation_submission("):
            launch.index('@api.post("/api/v1/generate/plan")')
        ]
        worker_at = launch.index("worker_h3_plan =")
        worker = launch[worker_at - 2500:]
        self.assertIn("_plan_generation_submission(", plan)
        self.assertLess(
            submission_planner.index("_validate_h3_sampling_steps(body)"),
            submission_planner.index("_prepare_h3_long_studio_request(body)"),
        )
        self.assertIn("_plan_generation_submission(", submit)
        self.assertLess(
            submission_planner.index("_validate_h3_sampling_steps(body)"),
            submission_planner.index("_prepare_h3_long_studio_request(body)"),
        )
        self.assertLess(
            worker.index("_validate_h3_sampling_steps(raw_params)"),
            worker.index("_prepare_h3_long_studio_request(raw_params)"),
        )

    def test_runtime_manifest_uses_model_aligner_and_separates_ref2va_rules(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        self.assertIn("_prepare_h3_long_studio_request(body)", launch)
        self.assertIn("_prepare_h3_long_studio_request(raw_params)", launch)
        self.assertIn("wgp.align_model_frame_count(\n                        clip_frames, _mc_model_def", launch)
        self.assertIn('segment_model == "minimax_h3_ref2va"', launch)
        self.assertIn('0 if semantic_h3_refs else cumulative_offset', launch)
        self.assertIn('2 if h3_longform else', launch)
        self.assertIn('if total_trimmed_frames > 0 and not h3_longform', launch)
        self.assertIn('safe_idx = max(0, len(vr) - 1)', launch)
        self.assertIn('"preserve_generated_audio": bool(', launch)
        self.assertIn('clip_current=task_no', launch)
        self.assertIn('clip_total=total_tasks', launch)
        self.assertIn('overall_progress=int(100 * task_idx / total_tasks)', launch)
        self.assertIn("_expand_h3_longform_outputs(", launch)
        self.assertIn("required_h3_continuation", launch)
        self.assertIn("raise RuntimeError(message) from e", launch)
        self.assertIn("first_failure_details = task_failure_details", launch)
        self.assertIn('first_task_error = task_failure_details["detail"]', launch)
        self.assertIn("failure_details=None if success else first_failure_details", launch)
        self.assertIn("Generation failed: {first_task_error}", launch)
        self.assertIn('filename.startswith("_continuation_")', launch)
        self.assertGreaterEqual(launch.count('"clip_current": j.get("clip_current", 0)'), 2)

        wgp = Path(APP, "wgp.py").read_text(encoding="utf-8")
        self.assertIn('multi_clip_info.get("preserve_generated_audio")', wgp)
        self.assertIn('multi_clip_info.get("published_frames")', wgp)
        self.assertIn('concat_configs.pop("audio_guide", None)', wgp)
        self.assertIn('abort_callback=lambda: bool(gen.get("abort", False))', wgp)
        self.assertIn("Unable to concatenate generated segments", wgp)
        self.assertIn(
            "audio_source\n                            if preserve_generated_audio",
            wgp,
        )


class StudioUiContractTests(unittest.TestCase):
    def test_frontend_detects_h3_shot_timestamp_and_global_end(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the Studio UI contract")
        module_uri = Path(ROOT, "ui", "src", "lib", "timelinePrompt.ts").as_uri()
        script = f"""
import {{ hasGlobalTimeline, globalTimelineEndSeconds }} from {json.dumps(module_uri)};
const prompt = `[Shot 1] Establish the room.
[Shot 2] At 00:15.000, cut to a close-up.
[Shot 3 | 00:23.500] The subject turns.`;
process.stdout.write(JSON.stringify([
  hasGlobalTimeline(prompt), globalTimelineEndSeconds(prompt),
]));
"""
        completed = subprocess.run(
            [node, "--experimental-strip-types", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(completed.stdout), [True, 23.5])

    def test_frontend_ignores_reversed_timeline_ranges_for_auto_duration(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the Studio UI contract")
        module_uri = Path(ROOT, "ui", "src", "lib", "timelinePrompt.ts").as_uri()
        script = f"""
import {{ globalTimelineEndSeconds }} from {json.dumps(module_uri)};
process.stdout.write(JSON.stringify([
  globalTimelineEndSeconds('[12-4s] reversed'),
  globalTimelineEndSeconds('[4-12s] valid'),
]));
"""
        completed = subprocess.run(
            [node, "--experimental-strip-types", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(completed.stdout), [None, 12])

    def test_frontend_window_count_matches_backend_quantized_boundary(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the Studio UI contract")
        module_uri = Path(ROOT, "ui", "src", "lib", "timelinePrompt.ts").as_uri()
        script = f"""
import {{ effectiveSlidingWindowGeometry }} from {json.dumps(module_uri)};
const options = {{
  fps: 25, frames_minimum: 1, frames_steps: 8, latent_size: 8,
  sliding_window: true,
  sliding_window_defaults: {{ overlap_default: 9, discard_last_frames: 8 }},
}};
process.stdout.write(JSON.stringify(effectiveSlidingWindowGeometry(116, 20, 9, options)));
"""
        completed = subprocess.run(
            [node, "--experimental-strip-types", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        geometry = json.loads(completed.stdout)
        self.assertEqual(geometry["totalFrames"], 2897)
        self.assertEqual(geometry["windowFrames"], 497)
        self.assertEqual(geometry["windowCount"], 7)

    def test_control_fps_changes_total_frames_without_rescaling_window(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the Studio UI contract")
        module_uri = Path(ROOT, "ui", "src", "lib", "timelinePrompt.ts").as_uri()
        script = f"""
import {{ controlFpsTotalFrames, effectiveSlidingWindowGeometry }} from {json.dumps(module_uri)};
const options = {{
  fps: 16, frames_minimum: 1, frames_steps: 4, latent_size: 4,
  sliding_window: true,
  sliding_window_defaults: {{ overlap_default: 5, discard_last_frames: 0 }},
}};
const totalFrames = controlFpsTotalFrames(10, 'control', 'guide.mp4', 25);
process.stdout.write(JSON.stringify(effectiveSlidingWindowGeometry(10, 5, 5, options, {{ totalFrames }})));
"""
        completed = subprocess.run(
            [node, "--experimental-strip-types", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        geometry = json.loads(completed.stdout)
        self.assertEqual(geometry["totalFrames"], 249)
        self.assertEqual(geometry["windowFrames"], 77)
        self.assertEqual(geometry["windowCount"], 4)

    def test_ui_preserves_detected_global_timeline_as_one_prompt(self):
        with open(os.path.join(ROOT, "ui", "src", "stores", "useStore.ts"), encoding="utf-8") as handle:
            store = handle.read()
        with open(os.path.join(ROOT, "ui", "src", "components", "Sidebar", "PromptInput.tsx"), encoding="utf-8") as handle:
            prompt_ui = handle.read()
        self.assertIn("if (hasGlobalTimeline(prompt))", store)
        self.assertIn("params.multi_prompts_gen_type = 2", store)
        self.assertIn("Full-video timing detected", prompt_ui)
        self.assertIn("[00:00-00:10]", prompt_ui)

    def test_studio_enhancement_is_explicit_and_names_the_effective_model(self):
        with open(os.path.join(ROOT, "ui", "src", "stores", "useStore.ts"), encoding="utf-8") as handle:
            store = handle.read()
        with open(os.path.join(ROOT, "ui", "src", "components", "Sidebar", "PromptInput.tsx"), encoding="utf-8") as handle:
            prompt_ui = handle.read()
        self.assertIn("studioPromptEnhance: false", store)
        generation = store[store.index("startGeneration: async"):store.index("stopGeneration: (jobId)")]
        self.assertIn("enhance_before_generate", generation)
        self.assertIn("enhanceBeforeGenerate", generation)
        self.assertNotIn("get().enhancePrompt()", generation)
        self.assertLess(generation.index("jobs: [newJob, ...s.jobs]"), generation.index("api.submitGeneration(params)"))
        self.assertIn("enhanceBeforeGenerate && s.activeWorkspace === submissionWorkspace", generation)
        self.assertIn("? { studioPromptEnhance: false }", generation)
        self.assertIn("get().activeWorkspace !== submissionWorkspace", generation)
        self.assertIn("reconnectedJobExists", generation)
        self.assertIn("promptPreview: durablePreparationExpected ? ''", generation)
        self.assertIn("usesDedicatedGenerationEndpoint", generation)
        self.assertIn("enhanceRequested && !state.params.prompt.trim()", generation)
        self.assertIn("Enter a prompt before using Enhance before Generate.", generation)
        self.assertIn("Enhance before Generate", prompt_ui)
        self.assertIn("Model: ${enhancerModelLabel}", prompt_ui)
        self.assertIn("global timeline is preserved as authored", prompt_ui)
        self.assertIn("modelOptions?.prompt_enhancer_model", prompt_ui)

    def test_backend_slicing_is_gated_to_structured_studio_prompt_mode(self):
        with open(os.path.join(APP, "wgp.py"), encoding="utf-8") as handle:
            source = handle.read()
        block = source[source.index("# Studio global-timeline prompts"):source.index("first_window_video_length", source.index("# Studio global-timeline prompts"))]
        self.assertIn("if sliding_window and multi_prompts_gen_type == 2", block)
        self.assertIn("build_global_timeline_window_prompts", block)
        self.assertIn("studio_global_timeline = True", block)
        self.assertIn("if not studio_global_timeline and prompt_enhancer_image_caption_model", source)
        with open(os.path.join(APP, "launch.py"), encoding="utf-8") as handle:
            launch = handle.read()
        self.assertIn("_studio_prompt_parser.has_global_timeline", launch)
        self.assertIn('body["multi_prompts_gen_type"] = 2', launch)
        self.assertIn('int(body.get("multi_prompts_gen_type") or 0) != 3', launch)

    def test_long_job_card_exposes_prompt_and_both_progress_levels(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        wgp = Path(APP, "wgp.py").read_text(encoding="utf-8")
        store = Path(
            ROOT, "ui", "src", "stores", "useStore.ts",
        ).read_text(encoding="utf-8")
        card = Path(
            ROOT, "ui", "src", "components", "MainContent", "MainContent.tsx",
        ).read_text(encoding="utf-8")
        for field in (
            '"prompt_preview"', '"active_window_prompt"',
            '"window_current"', '"window_total"',
            '"window_progress"', '"overall_progress"',
        ):
            self.assertIn(field, launch)
        self.assertIn('gen["current_window_prompt"]', wgp)
        self.assertIn("activeWindowPrompt: status.status", store)
        self.assertIn(": status.active_window_prompt", store)
        self.assertIn("windowCurrent: status.window_current", store)
        self.assertIn("overallProgress: status.overall_progress", store)
        self.assertIn("job.modelType?.startsWith('minimax_h3') ? 'Segment' : 'Window'", card)
        self.assertIn("{progressUnit} {job.windowCurrent || 1}/{job.windowTotal}", card)
        self.assertIn("Current {progressUnit.toLowerCase()}", card)
        self.assertIn("job.activeWindowPrompt || job.promptPreview", card)
        self.assertIn("ACTIVE_GENERATION_JOB_STATUSES.has(status.status)", store)
        self.assertIn("Terminal failures stay", store)

    def test_main_studio_window_controls_use_model_effective_values(self):
        duration = Path(
            ROOT, "ui", "src", "components", "Sidebar", "DurationSlider.tsx",
        ).read_text(encoding="utf-8")
        store = Path(ROOT, "ui", "src", "stores", "useStore.ts").read_text(encoding="utf-8")
        timeline = Path(ROOT, "ui", "src", "lib", "timelinePrompt.ts").read_text(encoding="utf-8")
        self.assertIn("Window size", duration)
        self.assertIn("Automatic", duration)
        self.assertIn("if (!modelOptions?.sliding_window) return null", duration)
        self.assertIn("safeOverlapMax", duration)
        self.assertIn("alignStudioTotalFrames(Math.round(s * fps), options)", store)
        self.assertIn("slidingWindowLocked: supportsWindowPlanning", store)
        self.assertIn("default_sliding_window_size", store)
        self.assertIn("export function alignTotalFrames", timeline)
        self.assertIn("export function alignStudioTotalFrames", timeline)
        self.assertIn("export function usesStudioSegments", timeline)
        self.assertIn("Maximum shot length", duration)
        self.assertIn("Maximum section length", duration)
        self.assertIn("Estimated shots", duration)
        self.assertIn("hard maximum", duration)
        self.assertIn("shorter or uneven", duration)

    def test_automatic_h3_output_restore_returns_to_studio_semantics(self):
        store = Path(ROOT, "ui", "src", "stores", "useStore.ts").read_text(encoding="utf-8")
        self.assertIn("const automaticH3Longform", store)
        self.assertIn("h3Longform?.global_prompt", store)
        self.assertIn("h3Longform?.requested_frames", store)
        self.assertIn("h3Longform?.segment_frames_maximum", store)
        self.assertIn("h3Longform?.original_image_start", store)
        self.assertIn("h3Longform?.original_image_end", store)
        self.assertIn("!automaticH3Longform && p.multi_prompts_gen_type === 3", store)
        self.assertIn("singlePromptMode: automaticH3Longform", store)
        self.assertIn('"manual_segment_ceiling": manual_segment_ceiling', Path(APP, "launch.py").read_text(encoding="utf-8"))

    def test_h3_plan_dialog_uses_server_catalog_and_download_state(self):
        dialog = Path(
            os.path.join(ROOT, "ui", "src", "components", "H3GenerationPlanDialog.tsx")
        ).read_text(encoding="utf-8")
        self.assertIn("checkpoint_options", dialog)
        self.assertIn("will auto-download", dialog)
        self.assertIn("Continuum’s built-in model list", dialog)
        self.assertIn("unavailable_reason", dialog)
        self.assertIn("disabled={Boolean(getModelBlockedReason", dialog)

    def test_model_options_exposes_model_native_resolution_objects(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        options = launch[
            launch.index("def get_model_options"):
            launch.index("# ── Generation Presets")
        ]
        self.assertIn('native_resolutions = md.get("resolutions")', options)
        self.assertIn('"label": str(item[0])', options)
        self.assertIn('"value": str(item[1])', options)
        self.assertIn('"resolutions": native_resolutions', options)

    def test_h3_plan_and_job_surfaces_include_privacy_safe_estimate(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        self.assertIn('@api.post("/api/v1/h3/estimate")', launch)
        self.assertIn("Unsupported H3 estimate fields", launch)
        self.assertIn('"h3_estimate": estimate', launch)
        self.assertIn('"h3_estimate": _submitted_h3_estimate', launch)
        self.assertIn('None if recovery_blocked else j.get("h3_estimate")', launch)
        self.assertIn('j.get("h3_estimate") if recovery_blocked else None', launch)
        estimate_block = launch[
            launch.index("async def h3_estimate"):
            launch.index('@api.get("/api/v1/h3/benchmark")')
        ]
        for forbidden in ("prompt", "path", "workspace", "project", "session"):
            self.assertNotIn(f'"{forbidden}"', estimate_block)

    def test_h3_observations_are_content_free_and_exclude_queue_wait(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        signature = launch[
            launch.index("def _h3_benchmark_input_signature"):
            launch.index("def _record_h3_benchmark_observation")
        ]
        self.assertNotIn("hashlib", signature)
        self.assertNotIn("open(", signature)
        self.assertIn('"image_count"', signature)
        timer = launch[
            launch.index("call_started = time.perf_counter()"):
            launch.index("# Process stream", launch.index("call_started = time.perf_counter()"))
        ]
        self.assertIn("wgp.generate_video", timer)
        self.assertIn('time.perf_counter() - call_started', timer)
        self.assertIn("_h3_model_is_resident(call_model)", timer)
        resident = launch[
            launch.index("def _h3_model_is_resident"):
            launch.index("def _h3_estimate_for_context")
        ]
        self.assertIn('getattr(wgp, "wan_model", None) is not None', resident)
        self.assertIn('getattr(wgp, "offloadobj", None) is not None', resident)
        self.assertIn('not bool(getattr(wgp, "reload_needed", False))', resident)
        capture = launch[
            launch.index("from services.h3_benchmark import validate_output_artifacts"):
            launch.index("record = record_observation", launch.index("from services.h3_benchmark import validate_output_artifacts"))
        ]
        self.assertIn("validate_output_artifacts(out_dir, output_files)", capture)

    def test_remote_estimates_hide_residency_and_local_observation_metadata(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        endpoint = launch[
            launch.index("async def h3_estimate"):
            launch.index('@api.get("/api/v1/h3/benchmark")')
        ]
        self.assertIn("include_residency=not bool", endpoint)
        helper = launch[
            launch.index("def _h3_estimate_for_context"):
            launch.index("def _h3_profile_estimate_payload")
        ]
        self.assertIn("if include_residency else []", helper)

    def test_estimator_uses_the_runtime_turbo_compatibility_matrix(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        helper = launch[
            launch.index("def _validate_h3_turbo_estimate_context"):
            launch.index('@api.post("/api/v1/h3/estimate")')
        ]
        self.assertIn("from services.h3_turbo import turbo_requested, validate_turbo_request", helper)
        self.assertIn("selected = _H3_REF2VA_MODEL", helper)
        self.assertNotIn("_H3_EXPLICIT_FL2VA_MODEL", helper)
        self.assertNotIn("explicit_output", helper)
        self.assertIn("activated_loras=context.get", helper)
        self.assertIn("loras_multipliers=context.get", helper)
        self.assertIn("skip_steps_cache_type=context.get", helper)

    def test_profile_estimates_replace_custom_settings_and_ignore_capture_flag(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        helper = launch[
            launch.index("def _h3_profile_estimate_payload"):
            launch.index("def _validate_h3_turbo_estimate_context")
        ]
        self.assertIn('"custom_settings": dict(settings["custom_settings"])', helper)
        self.assertNotIn('**dict(context.get("custom_settings") or {})', helper)
        endpoint = launch[
            launch.index('async def h3_estimate'):
            launch.index('@api.get("/api/v1/h3/benchmark")')
        ]
        self.assertIn('"h3_benchmark_capture"', endpoint)

    def test_confirmed_adaptive_segments_are_aggregated_and_seed_queue_eta(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        context = launch[
            launch.index("def _h3_estimate_context"):
            launch.index("def _h3_model_is_resident")
        ]
        self.assertIn('context["_segment_contexts"] = segment_contexts', context)
        estimator = launch[
            launch.index("def _h3_estimate_for_context"):
            launch.index("def _h3_profile_estimate_payload")
        ]
        self.assertIn("aggregate_h3_estimates(estimates)", estimator)
        self.assertIn("segment_model == previous_model", estimator)
        profiles = launch[
            launch.index("def _h3_profile_estimate_payload"):
            launch.index("def _validate_h3_turbo_estimate_context")
        ]
        self.assertIn('candidate["_segment_contexts"]', profiles)
        self.assertIn('"custom_settings": dict(settings["custom_settings"])', profiles)
        validator = launch[
            launch.index("def _validate_h3_turbo_estimate_context"):
            launch.index('@api.post("/api/v1/h3/estimate")')
        ]
        self.assertIn('segment_contexts = context.get("_segment_contexts")', validator)
        generation = launch[
            launch.index('async def generate(request: Request)'):
            launch.index('@api.post("/api/v1/retake")')
        ]
        self.assertIn("_plan_generation_submission(", generation)
        eta = launch[
            launch.index("def _job_eta_values"):
            launch.index("def _job_owned_by_request")
        ]
        self.assertIn('if status == "queued"', eta)
        self.assertIn('estimate.get("seconds")', eta)

    def test_h3_profile_ui_recognizes_fresh_high_with_default_sol_knobs(self):
        component = Path(
            ROOT, "ui", "src", "components", "Sidebar", "H3PerformanceProfiles.tsx",
        ).read_text(encoding="utf-8")
        store = Path(ROOT, "ui", "src", "stores", "useStore.ts").read_text(encoding="utf-8")
        self.assertIn("h3ProfileMatches", component)
        self.assertIn("profiles.find(profile => h3ProfileMatches(profile", component)
        self.assertIn("function _canonicalH3ProfileCustomSettings", store)
        self.assertIn("h3_sol_dense_steps: 10", store)

    def test_h3_delivery_profiles_use_learned_upscale_then_exact_fit(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        helper = launch[
            launch.index("def _apply_delivery_fit_to_file"):
            launch.index("# ============================================================================", launch.index("def _apply_delivery_fit_to_file"))
        ]
        self.assertIn("1920, 1080", helper)
        self.assertIn("2688, 1536", helper)
        self.assertIn("3840, 2160", helper)
        self.assertIn('delivery_fit not in {"upscale_exact", "center_crop"}', helper)
        self.assertIn("flags=lanczos", helper)
        self.assertIn('container not in {".mp4", ".mkv", ".webm"}', helper)
        postprocess = launch[
            launch.index("if success and pp_spatial_upsampling"):
            launch.index("# Post-generation film grain pass")
        ]
        self.assertIn("_apply_spatial_upsampling_to_file", postprocess)
        self.assertIn("_deliver_h3_outputs_transactionally", postprocess)
        self.assertIn("_authoritative_h3_postprocess_outputs", postprocess)
        self.assertIn("producer_artifact_roles", postprocess)
        self.assertIn("join_output_file=join_output_file", postprocess)
        self.assertIn('finish_job(job, "failed", **failure_updates)', postprocess)

        resolution = Path(
            ROOT, "ui", "src", "components", "Sidebar", "ResolutionPresets.tsx",
        ).read_text(encoding="utf-8")
        self.assertIn("creation size", resolution)
        self.assertIn("FlashVSR", resolution)
        self.assertIn("upscale", resolution)
        self.assertIn("final export", resolution)
        self.assertIn("setH3NativeResolution(event.target.value)", resolution)

    def test_h3_delivery_selects_only_explicit_final_or_joined_video(self):
        source_path = Path(APP, "launch.py")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_authoritative_h3_postprocess_outputs"
        )
        namespace = {"os": os}
        exec(
            compile(ast.Module(body=[node], type_ignores=[]), str(source_path), "exec"),
            namespace,
        )
        select = namespace["_authoritative_h3_postprocess_outputs"]
        files = ["window.mp4", "clip.mp4", "joined.mp4", "notes.json"]
        roles = {
            "window.mp4": "window",
            "clip.mp4": "component",
            "joined.mp4": "final",
        }
        self.assertEqual(
            select(files, roles, is_multiclip=True, join_output_file="joined.mp4"),
            ["joined.mp4"],
        )
        self.assertEqual(
            select(files, roles, is_multiclip=False, join_output_file=None),
            ["joined.mp4"],
        )
        self.assertEqual(
            select(files, roles, is_multiclip=True, join_output_file=None),
            [],
        )

    def test_cancel_boundary_publishes_only_a_sealed_concat(self):
        source_path = Path(APP, "launch.py")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_verified_h3_concat_output_names"
        )
        units = [
            {"kind": "h3_segment", "variant": 0},
            {"kind": "h3_concat", "variant": 0},
            {"kind": "h3_concat", "variant": 1},
        ]

        def match(_job, *, kind, variant, index, project_dir):
            self.assertEqual(kind, "h3_concat")
            self.assertEqual(index, 0)
            self.assertEqual(project_dir, "/project")
            if variant == 0:
                return {"artifacts": [{"basename": "sealed-join.mp4"}]}
            return None

        namespace = {
            "_queue_recovery_units": lambda _job: units,
            "_queue_recovery_unit_matches": match,
        }
        exec(
            compile(ast.Module(body=[node], type_ignores=[]), str(source_path), "exec"),
            namespace,
        )
        verified = namespace["_verified_h3_concat_output_names"](
            {}, "/project",
            ["segment.mp4", "sealed-join.mp4", "unsealed-join.mp4"],
        )
        self.assertEqual(verified, ["sealed-join.mp4"])

    def test_concat_failure_seals_final_segment_before_success_only_branch(self):
        launch = Path(APP, "launch.py").read_text(encoding="utf-8")
        callback_at = launch.index(
            "def _seal_final_h3_segment_before_concat("
        )
        generate_at = launch.index(
            "async_run(make_error_handler", callback_at,
        )
        preconcat_callback = launch[callback_at:generate_at]
        self.assertIn("_write_output_sidecars(", preconcat_callback)
        self.assertIn("_queue_recovery_promote_staged_outputs(", preconcat_callback)
        self.assertIn("_queue_recovery_checkpoint_unit(", preconcat_callback)
        self.assertIn('kind="h3_segment"', preconcat_callback)
        self.assertIn('params["after_segment_output"]', preconcat_callback)
        failure_boundary = launch.index(
            "# WGP renders and registers the last H3 segment"
        )
        success_boundary = launch.index(
            "if not task_error:", failure_boundary,
        )
        failure_checkpoint = launch[failure_boundary:success_boundary]
        self.assertIn('failure_stage in {"concat", "audio_mux"}', failure_checkpoint)
        self.assertIn("failed_segment + 1 != failed_total", failure_checkpoint)
        self.assertIn("sealed_failed_unit = _queue_recovery_unit_matches(", failure_checkpoint)
        self.assertIn("if sealed_failed_unit is None:", failure_checkpoint)
        self.assertIn("_queue_recovery_promote_staged_outputs", failure_checkpoint)
        self.assertIn('kind="h3_segment"', failure_checkpoint)
        self.assertIn("_queue_recovery_checkpoint_unit", failure_checkpoint)
        self.assertIn("_verified_h3_concat_output_names", launch)
        success_checkpoint = launch[success_boundary:launch.index(
            "if concat_names and not task_error:", success_boundary,
        )]
        self.assertIn("sealed_segment_unit = _queue_recovery_unit_matches(", success_checkpoint)
        self.assertIn(
            "if segment_names and sealed_segment_unit is None:",
            success_checkpoint,
        )


if __name__ == "__main__":
    unittest.main()
