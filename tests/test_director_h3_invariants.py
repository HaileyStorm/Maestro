"""Model-free Director regressions for MiniMax H3 child generation."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import director_pipeline as pipeline  # noqa: E402
from services import llm_service  # noqa: E402


class _H3Wgp:
    def __init__(self, save_path: str):
        self.save_path = save_path
        self.server_config = {"services": {}}

    @staticmethod
    def get_model_def(model_type: str) -> dict:
        if model_type not in pipeline._H3_VIDEO_MODELS:
            return {}
        reference = model_type == pipeline._H3_REF2VA_MODEL
        return {
            "fps": 24,
            "frames_minimum": 107 if reference else 124,
            "frames_steps": 17,
            "frames_maximum": 345,
            "latent_size": 17,
            "frame_alignment_modulus": 17,
            "frame_alignment_remainder": 5,
            "frame_alignment_mode": "ceil",
            "minimax_h3_reference_mode": reference,
        }

    @classmethod
    def get_model_min_frames_and_step(cls, model_type: str):
        model = cls.get_model_def(model_type)
        return (
            model["frames_minimum"],
            model["frames_steps"],
            model["latent_size"],
        )

    @staticmethod
    def align_model_frame_count(frame_count: int, model_def: dict) -> int:
        minimum = int(model_def["frames_minimum"])
        maximum = int(model_def["frames_maximum"])
        value = max(minimum, min(maximum, int(frame_count)))
        delta = (value - 5) % 17
        if delta:
            value += 17 - delta
        if value > maximum:
            value -= 17
        return value


class TestDirectorH3Invariants(unittest.TestCase):
    def setUp(self):
        self.originals = {
            "wgp": pipeline._wgp,
            "jobs": pipeline._jobs,
            "pipelines": pipeline._pipelines,
            "run_generation": pipeline._run_generation,
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        pipeline._wgp = _H3Wgp(self.temp_dir.name)
        pipeline._jobs = {}
        pipeline._pipelines = {}
        pipeline._run_generation = None
        self.start_image = os.path.join(self.temp_dir.name, "start.png")
        with open(self.start_image, "wb") as handle:
            handle.write(b"image")

    def tearDown(self):
        pipeline._wgp = self.originals["wgp"]
        pipeline._jobs = self.originals["jobs"]
        pipeline._pipelines = self.originals["pipelines"]
        pipeline._run_generation = self.originals["run_generation"]
        self.temp_dir.cleanup()

    @staticmethod
    def _scene(duration: float) -> tuple[list[dict], list[dict]]:
        prompt = (
            "subject_definitions:\n<Subject 1>: a runner in a red coat\n"
            "[Shot 1] The runner crosses the station without a cut.\n"
            "[Shot 2] At 00:15.000, cut to a close-up as the train arrives."
        )
        return (
            [{"video_prompt": prompt, "window_prompts": [], "window_count": 1}],
            [{"start": 0, "end": duration, "duration_sec": duration}],
        )

    def _base_generation_params(self) -> dict:
        return {
            "model_type": pipeline._H3_BASE_FL2VA_MODEL,
            "prompt": "placeholder",
            "image_start": self.start_image,
            "image_prompt_type": "S",
            "multi_prompts_gen_type": 0,
            "video_length": 480,
            "sliding_window_size": 480,
        }

    def test_long_director_scenes_are_split_on_the_h3_grid(self):
        for duration, minimum_segments in ((20.0, 2), (32.0, 3)):
            with self.subTest(duration=duration):
                clips, planned = self._scene(duration)
                body = self._base_generation_params()
                plan = pipeline._prepare_director_h3_longform(
                    body,
                    params={"h3_ref2va_terms_accepted": True},
                    clip_plans=clips,
                    planned_clips=planned,
                    fps=24,
                )

                self.assertIsNotNone(plan)
                self.assertGreaterEqual(plan["clip_count"], minimum_segments)
                self.assertEqual(plan["requested_frames"], round(duration * 24))
                self.assertEqual(body["multi_prompts_gen_type"], 3)
                self.assertEqual(len(body["per_clip_prompts"]), plan["clip_count"])
                self.assertTrue(all(124 <= value <= 345 for value in plan["clip_frames"]))
                self.assertTrue(all(value % 17 == 5 for value in plan["clip_frames"]))
                self.assertIn("At 00:15.000", plan["global_prompt"])
                self.assertTrue(any(
                    boundary["type"] in {"precut", "cut"}
                    for boundary in plan["clip_boundaries"]
                ))

    def test_ref2va_effective_segment_requires_and_carries_terms(self):
        clips, planned = self._scene(20.0)
        body = self._base_generation_params()
        with self.assertRaisesRegex(ValueError, "Ref2VA.*terms"):
            pipeline._prepare_director_h3_longform(
                body,
                params={},
                clip_plans=clips,
                planned_clips=planned,
                fps=24,
            )

        accepted = self._base_generation_params()
        plan = pipeline._prepare_director_h3_longform(
            accepted,
            params={"h3_ref2va_terms_accepted": True},
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        self.assertTrue(accepted["h3_ref2va_terms_accepted"])
        self.assertIn(
            pipeline._H3_REF2VA_MODEL,
            {item["model_type"] for item in plan["segment_models"]},
        )

    def test_video_phase_fails_terms_gate_before_child_submission(self):
        clips, planned = self._scene(20.0)
        params = {
            "video_model": pipeline._H3_BASE_FL2VA_MODEL,
            "video_params": {"resolution": "768x768"},
            "seamless": True,
        }
        with patch.object(pipeline, "_submit_and_wait") as submit:
            with self.assertRaisesRegex(ValueError, "Ref2VA.*terms"):
                pipeline._run_video_generation(
                    "director-terms",
                    params,
                    clips,
                    planned,
                    ["start.png"],
                    out_dir=self.temp_dir.name,
                )
        submit.assert_not_called()

    def test_supplied_final_frame_is_reserved_for_final_fl2va_segment(self):
        clips, planned = self._scene(20.0)
        end_image = os.path.join(self.temp_dir.name, "end.png")
        with open(end_image, "wb") as handle:
            handle.write(b"image")
        body = self._base_generation_params()
        plan = pipeline._prepare_director_h3_longform(
            body,
            params={
                "image_end": end_image,
                "h3_ref2va_terms_accepted": True,
            },
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )

        self.assertEqual(plan["original_image_end"], end_image)
        self.assertEqual(body["image_end"][-1], end_image)
        self.assertEqual(
            plan["segment_models"][-1]["model_type"],
            pipeline._H3_BASE_FL2VA_MODEL,
        )
        self.assertEqual(
            plan["segment_models"][-1]["reason"],
            "supplied final-frame anchor",
        )

    def test_30s_cut_alignment_with_192_frame_ceiling_and_final_anchor(self):
        clips, planned = self._scene(30.0)
        end_image = os.path.join(self.temp_dir.name, "end-30s.png")
        with open(end_image, "wb") as handle:
            handle.write(b"image")

        for final_frame, expected_clips in ((None, 4), (end_image, 4)):
            with self.subTest(final_frame=bool(final_frame)):
                body = self._base_generation_params()
                params = {
                    "director_max_shot_frames": 192,
                    "h3_ref2va_terms_accepted": True,
                    **({"image_end": final_frame} if final_frame else {}),
                }
                plan = pipeline._prepare_director_h3_longform(
                    body,
                    params=params,
                    clip_plans=clips,
                    planned_clips=planned,
                    fps=24,
                )

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
                self.assertIn(
                    "[0-", body["per_clip_prompts"][cut_index + 1],
                )
                self.assertIn(
                    "[Shot 2] cut to a close-up as the train arrives.",
                    body["per_clip_prompts"][cut_index + 1],
                )
                if final_frame:
                    self.assertEqual(body["image_end"][-1], final_frame)
                    self.assertEqual(
                        plan["segment_models"][-1]["reason"],
                        "supplied final-frame anchor",
                    )

    def test_native_scenes_keep_scene_cut_adaptive_routing(self):
        clips = [
            {"video_prompt": "A wide shot in the station."},
            {"video_prompt": "A close-up inside the train."},
        ]
        planned = [
            {"start": 0, "end": 10, "duration_sec": 10},
            {"start": 10, "end": 20, "duration_sec": 10},
        ]
        body = self._base_generation_params()
        body["image_start"] = [self.start_image, self.start_image]
        plan = pipeline._prepare_director_h3_longform(
            body,
            params={"h3_ref2va_terms_accepted": True},
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan["clip_count"], 2)
        self.assertEqual(plan["clip_boundaries"][0]["type"], "cut")
        self.assertEqual(
            plan["segment_models"][1]["model_type"],
            pipeline._H3_REF2VA_MODEL,
        )

    def test_explicit_metadata_does_not_select_pinkcherry_for_director(self):
        clips = [{"video_prompt": "A single continuous shot."}]
        planned = [{"start": 0, "end": 10, "duration_sec": 10}]
        body = self._base_generation_params()
        body.pop("image_start")
        body["image_prompt_type"] = ""
        body["video_length"] = 240
        original = dict(body)

        plan = pipeline._prepare_director_h3_longform(
            body,
            params={"explicit_output": True},
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )

        self.assertIsNone(plan)
        self.assertEqual(body, original)
        self.assertEqual(
            pipeline._director_h3_preferred_fl2va(
                {"explicit_output": True},
                pipeline._H3_BASE_FL2VA_MODEL,
            ),
            pipeline._H3_BASE_FL2VA_MODEL,
        )

    def test_director_adaptive_routing_preserves_manual_pinkcherry_flavor(self):
        models = pipeline._director_h3_segment_models(
            {"explicit_output": True},
            selected=pipeline._H3_EXPLICIT_FL2VA_MODEL,
            boundaries=[{"type": "cut"}, {"type": "continuous"}],
            segment_count=3,
            first_anchor=self.start_image,
            last_anchor=self.start_image,
            semantic_references=False,
        )
        self.assertEqual(models[0]["model_type"], pipeline._H3_EXPLICIT_FL2VA_MODEL)
        self.assertEqual(models[1]["model_type"], pipeline._H3_REF2VA_MODEL)
        self.assertEqual(models[2]["model_type"], pipeline._H3_EXPLICIT_FL2VA_MODEL)

    def test_manual_ref2va_rejects_incompatible_edge_anchors(self):
        for duration in (10.0, 20.0):
            with self.subTest(duration=duration):
                clips, planned = self._scene(duration)
                body = self._base_generation_params()
                body["model_type"] = pipeline._H3_REF2VA_MODEL
                with self.assertRaisesRegex(ValueError, "Manual Ref2VA.*anchors"):
                    pipeline._prepare_director_h3_longform(
                        body,
                        params={
                            "h3_adaptive_conditioning": False,
                            "h3_ref2va_terms_accepted": True,
                        },
                        clip_plans=clips,
                        planned_clips=planned,
                        fps=24,
                    )

    def test_segment_ceiling_and_boundary_override_are_honored(self):
        clips = [{"video_prompt": "One continuous tracking shot."}]
        planned = [{"start": 0, "end": 32, "duration_sec": 32}]
        body = self._base_generation_params()
        plan = pipeline._prepare_director_h3_longform(
            body,
            params={
                "director_max_shot_frames": 200,
                "h3_boundary_overrides": [{"type": "cut"}],
                "h3_ref2va_terms_accepted": True,
            },
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )

        self.assertEqual(plan["segment_frames_maximum"], 192)
        self.assertTrue(plan["manual_segment_ceiling"])
        self.assertTrue(all(value <= 200 for value in plan["clip_frames"]))
        self.assertEqual(plan["clip_boundaries"][0]["type"], "cut")
        self.assertEqual(plan["clip_boundaries"][0]["source"], "user_override")

    def test_structured_h3_shot_contract_keeps_dialogue_and_visual_context(self):
        shot = SimpleNamespace(
            continuity_strategy="extend_previous",
            environment="an amber-lit workshop",
            visual_style="restrained 35mm realism",
            lighting="warm practical lamps",
            spatial_setup="Ada at the left workbench",
            subjects_on_screen=[SimpleNamespace(
                speaker_name="Ada",
                visual_description="an adult mechanic with cropped black hair",
                wardrobe="oil-stained green coveralls",
            )],
            dialogue_beats=[SimpleNamespace(
                speaker_id="ada",
                spoken_text="Keep these words exactly.",
            )],
            ending_beat="Ada closes the steel toolbox",
            audio_plan=None,
            metadata={},
        )
        clip_plans = [{"video_prompt": "Ada works.", "image_prompt": ""}]
        planned = [{"duration_sec": 20.0}]
        pipeline._attach_director_h3_shot_contracts(
            clip_plans, planned, [shot],
        )
        contract = clip_plans[0]["_h3_shot"]
        self.assertEqual(contract["continuity_strategy"], "extend_previous")
        self.assertEqual(
            contract["dialogue_beats"][0]["spoken_text"],
            "Keep these words exactly.",
        )
        self.assertEqual(planned[0]["_h3_shot"], contract)

    def test_committed_director_h3_plan_rehydrates_without_replanning(self):
        clips, planned = self._scene(20.0)
        first_body = self._base_generation_params()
        params = {
            "h3_ref2va_terms_accepted": True,
            "director_max_shot_frames": 243,
        }
        original = pipeline._prepare_director_h3_longform(
            first_body,
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        self.assertIsNotNone(params.get("_h3_longform"))
        self.assertTrue(original["manual_segment_ceiling"])
        fresh_body = self._base_generation_params()
        with patch(
            "services.h3_shot_planner.plan_h3_native_shots",
            side_effect=AssertionError("recovery must not replan"),
        ):
            restored = pipeline._prepare_director_h3_longform(
                fresh_body,
                params=params,
                clip_plans=[],
                planned_clips=[],
                fps=24,
            )
        self.assertEqual(restored, original)
        self.assertEqual(
            fresh_body["per_clip_prompts"],
            original["shot_plan"]["clip_prompts"],
        )
        self.assertEqual(
            restored["segment_policy"], original["segment_policy"],
        )
        self.assertTrue(restored["manual_segment_ceiling"])
        self.assertTrue(fresh_body["h3_ref2va_terms_accepted"])

    def test_corrupt_committed_publication_geometry_is_rejected(self):
        import copy

        clips = [{"video_prompt": (
            "[Shot 1] At 0 seconds, the host enters. "
            "[Shot 2] At 6 seconds, cut to the guest."
        )}]
        planned = [{"start": 0, "end": 20, "duration_sec": 20}]
        body = self._base_generation_params()
        params = {"h3_ref2va_terms_accepted": True}
        pipeline._prepare_director_h3_longform(
            body,
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        corrupt = copy.deepcopy(params["_h3_longform"])
        corrupt["clip_published_frames"][0] += 1
        corrupt["clip_trim_tail_frames"][0] -= 1

        with self.assertRaisesRegex(ValueError, "geometry disagrees"):
            pipeline._prepare_director_h3_longform(
                self._base_generation_params(),
                params={
                    "h3_ref2va_terms_accepted": True,
                    "_h3_longform": corrupt,
                },
                clip_plans=[],
                planned_clips=[],
                fps=24,
            )

    def test_committed_plan_rehydrate_normalizes_h3_keyframes_again(self):
        clips = [{"video_prompt": "Beat one. Beat two. Beat three."}]
        planned = [{"start": 0, "end": 20, "duration_sec": 20}]
        original_body = self._base_generation_params()
        original_body.update({
            "image_refs": ["keyframe-a.png", "keyframe-b.png"],
            "frames_positions": "200 400",
            "video_prompt_type": "KFI",
        })
        params = {"h3_ref2va_terms_accepted": True}
        pipeline._prepare_director_h3_longform(
            original_body,
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )

        recovered_body = self._base_generation_params()
        recovered_body.update({
            "image_refs": ["keyframe-a.png", "keyframe-b.png"],
            "frames_positions": "200 400",
            "video_prompt_type": "KFI",
        })
        pipeline._prepare_director_h3_longform(
            recovered_body,
            params=params,
            clip_plans=[],
            planned_clips=[],
            fps=24,
        )

        self.assertNotIn("frames_positions", recovered_body)
        self.assertNotIn("KFI", recovered_body["video_prompt_type"])
        self.assertEqual(
            recovered_body["custom_settings"]["h3_director_keyframes"],
            "semantic_references",
        )
        self.assertEqual(
            recovered_body["image_refs"],
            ["keyframe-a.png", "keyframe-b.png"],
        )

    def test_registered_restart_preserves_structured_h3_shot_contract(self):
        pid = "director-h3-restore"
        contract = {
            "version": 1,
            "continuity_strategy": "continuous",
            "dialogue_beats": [{"spoken_text": "Exact words."}],
            "closing_blocking": "the host faces camera",
        }
        data = {
            "pipeline_id": pid,
            "status": "running",
            "phase": "committed-h3-shot-plan",
            "_params_snapshot": {"video_model": pipeline._H3_BASE_FL2VA_MODEL},
            "clips": [{
                "image_prompt": "",
                "video_prompt": "The host speaks.",
                "_h3_shot": contract,
            }],
        }
        state_path = os.path.join(
            self.temp_dir.name, pipeline.pipeline_state_filename(pid),
        )
        restored = pipeline.restore_registered_pipeline(
            data,
            state_path,
            {"inputs": []},
            defer_worker=True,
        )

        self.assertEqual(restored["clip_plans"][0]["_h3_shot"], contract)

    def test_untimed_director_uses_same_draft_and_high_segment_pressure(self):
        clips = [{
            "video_prompt": "Beat one. Beat two. Beat three. Beat four.",
        }]
        planned = [{"start": 0, "end": 20, "duration_sec": 20}]
        for profile, settings, expected in (
            ("draft", {
                "num_inference_steps": 4,
                "resolution": "608x352",
                "custom_settings": {"h3_turbo_profile": "h3_turbo_v4"},
            }, 3),
            ("high", {
                "num_inference_steps": 20,
                "resolution": "1344x768",
                "custom_settings": {},
            }, 2),
        ):
            with self.subTest(profile=profile):
                body = self._base_generation_params()
                params = {
                    "h3_ref2va_terms_accepted": True,
                    **settings,
                }
                plan = pipeline._prepare_director_h3_longform(
                    body,
                    params=params,
                    clip_plans=clips,
                    planned_clips=planned,
                    fps=24,
                )
                self.assertEqual(plan["clip_count"], expected)
                self.assertEqual(plan["segment_policy"]["profile_id"], profile)

    def test_director_auto_ignores_model_window_default_and_manual_is_explicit(self):
        clips = [{
            "video_prompt": "Beat one. Beat two. Beat three. Beat four.",
        }]
        planned = [{"start": 0, "end": 20, "duration_sec": 20}]
        draft = {
            "h3_ref2va_terms_accepted": True,
            "num_inference_steps": 4,
            "resolution": "608x352",
            "custom_settings": {"h3_turbo_profile": "h3_turbo_v4"},
            # This is a checkpoint execution default, not Director user intent.
            "video_params": {"sliding_window_size": 345},
        }
        for model_type in (
            pipeline._H3_BASE_FL2VA_MODEL,
            pipeline._H3_REF2VA_MODEL,
        ):
            with self.subTest(model_type=model_type, mode="auto"):
                body = self._base_generation_params()
                body["model_type"] = model_type
                params = dict(draft)
                plan = pipeline._prepare_director_h3_longform(
                    body,
                    params=params,
                    clip_plans=clips,
                    planned_clips=planned,
                    fps=24,
                )
                self.assertFalse(plan["manual_segment_ceiling"])
                self.assertEqual(plan["segment_policy"]["profile_id"], "draft")
                self.assertEqual(plan["clip_count"], 3)
                self.assertTrue(params["_h3_longform"]["segment_policy"]["applied"])

            with self.subTest(model_type=model_type, mode="manual"):
                body = self._base_generation_params()
                body["model_type"] = model_type
                params = {**draft, "director_max_shot_frames": 243}
                plan = pipeline._prepare_director_h3_longform(
                    body,
                    params=params,
                    clip_plans=clips,
                    planned_clips=planned,
                    fps=24,
                )
                self.assertTrue(plan["manual_segment_ceiling"])
                self.assertEqual(plan["segment_frames_maximum"], 243)
                self.assertEqual(plan["clip_count"], 2)
                self.assertFalse(plan["segment_policy"]["applied"])

    def test_two_ten_second_scenes_publish_exact_boundary_without_audio_drift(self):
        clips = [
            {"video_prompt": "The host opens the show."},
            {"video_prompt": "The guest answers."},
        ]
        planned = [
            {"start": 0, "end": 10, "duration_sec": 10},
            {"start": 10, "end": 20, "duration_sec": 10},
        ]
        body = self._base_generation_params()
        plan = pipeline._prepare_director_h3_longform(
            body,
            params={"h3_ref2va_terms_accepted": True},
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )

        self.assertEqual(plan["clip_frames"], [243, 243])
        self.assertEqual(plan["clip_trim_tail_frames"], [3, 3])
        self.assertEqual(plan["clip_published_frames"], [240, 240])
        self.assertEqual(sum(plan["clip_published_frames"]), 480)
        boundary = plan["clip_boundaries"][0]
        self.assertEqual(boundary["at_frame"], 240)
        self.assertEqual(boundary["at_seconds"], 10.0)
        # Runtime audio offsets advance by these post-tail-trim published
        # counts, so scene two begins at frame 240 rather than the 243 grid.
        self.assertEqual(plan["shot_plan"]["shots"][1]["published_start_frame"], 240)

    def test_scene_boundary_override_wins_over_structured_continuity(self):
        planned = [
            {"start": 0, "end": 10, "duration_sec": 10},
            {"start": 10, "end": 20, "duration_sec": 10},
        ]
        for structured, override, expected in (
            ("independent", "continuous", "continuous"),
            ("continuous", "cut", "independent"),
        ):
            with self.subTest(structured=structured, override=override):
                clips = [
                    {
                        "video_prompt": "The host opens the show.",
                        "_h3_shot": {"continuity_strategy": "independent"},
                    },
                    {
                        "video_prompt": "The guest answers.",
                        "_h3_shot": {"continuity_strategy": structured},
                    },
                ]
                body = self._base_generation_params()
                plan = pipeline._prepare_director_h3_longform(
                    body,
                    params={
                        "h3_ref2va_terms_accepted": True,
                        "h3_boundary_overrides": [{"type": override}],
                    },
                    clip_plans=clips,
                    planned_clips=planned,
                    fps=24,
                )

                boundary = plan["clip_boundaries"][0]
                self.assertEqual(boundary["source"], "user_override")
                self.assertEqual(boundary["type"], override)
                self.assertEqual(boundary["continuity_mode"], expected)

    def test_studio_and_director_share_byte_and_boundary_semantics(self):
        from tests.test_studio_prompt_windows import H3LongStudioPlanningTests

        prompt = "Beat one. Beat two. Beat three. Beat four."
        settings = {
            "num_inference_steps": 4,
            "resolution": "608x352",
            "custom_settings": {"h3_turbo_profile": "h3_turbo_v4"},
        }
        prepare_studio = H3LongStudioPlanningTests._load_launch_helpers()[
            "_prepare_h3_long_studio_request"
        ]
        studio_body = {
            "model_type": "minimax_h3",
            "video_length": 480,
            "prompt": prompt,
            **settings,
        }
        studio = prepare_studio(studio_body)

        director_body = self._base_generation_params()
        director = pipeline._prepare_director_h3_longform(
            director_body,
            params={"h3_ref2va_terms_accepted": True, **settings},
            clip_plans=[{"video_prompt": prompt}],
            planned_clips=[{"start": 0, "end": 20, "duration_sec": 20}],
            fps=24,
        )
        self.assertEqual(director["clip_frames"], studio["clip_frames"])
        self.assertEqual(
            director["shot_plan"]["clip_prompts"],
            studio["shot_plan"]["clip_prompts"],
        )
        self.assertEqual(
            director["clip_boundaries"], studio["clip_boundaries"],
        )

    def test_unequal_timed_studio_director_and_recovery_are_exact(self):
        from tests.test_studio_prompt_windows import H3LongStudioPlanningTests

        dialogue = "<d>[English] Keep this exact.</d>"
        prompt = (
            f"[Shot 1] At 0 seconds, the host says {dialogue} "
            "[Shot 2] At 6 seconds, cut to the guest answering. "
            "FINAL BLOCKING: the guest faces camera"
        )
        settings = {
            "num_inference_steps": 4,
            "resolution": "608x352",
            "custom_settings": {"h3_turbo_profile": "h3_turbo_v4"},
        }
        prepare_studio = H3LongStudioPlanningTests._load_launch_helpers()[
            "_prepare_h3_long_studio_request"
        ]
        studio_body = {
            "model_type": "minimax_h3",
            "video_length": 480,
            "prompt": prompt,
            **settings,
        }
        studio = prepare_studio(studio_body)

        director_body = self._base_generation_params()
        params = {"h3_ref2va_terms_accepted": True, **settings}
        director = pipeline._prepare_director_h3_longform(
            director_body,
            params=params,
            clip_plans=[{"video_prompt": prompt}],
            planned_clips=[{"start": 0, "end": 20, "duration_sec": 20}],
            fps=24,
        )
        for plan in (studio, director):
            self.assertEqual(plan["clip_frames"], [158, 345])
            self.assertEqual(plan["clip_published_frames"], [144, 336])
            self.assertEqual(plan["clip_trim_tail_frames"], [14, 9])
            self.assertEqual(plan["clip_boundaries"][0]["at_seconds"], 6.0)
            self.assertEqual(sum(
                item.count(dialogue)
                for item in plan["shot_plan"]["clip_prompts"]
            ), 1)
            self.assertNotIn(
                "guest faces camera", plan["shot_plan"]["clip_prompts"][0],
            )
            self.assertIn(
                "guest faces camera", plan["shot_plan"]["clip_prompts"][1],
            )
        self.assertEqual(
            director["shot_plan"]["clip_prompts"],
            studio["shot_plan"]["clip_prompts"],
        )

        recovered_body = self._base_generation_params()
        with patch(
            "services.h3_shot_planner.plan_h3_clip_frames",
            side_effect=AssertionError("recovery must not rebalance"),
        ), patch(
            "services.h3_shot_planner.plan_h3_native_shots",
            side_effect=AssertionError("recovery must not replan"),
        ):
            recovered = pipeline._prepare_director_h3_longform(
                recovered_body,
                params=params,
                clip_plans=[],
                planned_clips=[],
                fps=24,
            )
        self.assertEqual(recovered["clip_frames"], [158, 345])
        self.assertEqual(recovered["clip_published_frames"], [144, 336])
        self.assertEqual(recovered["clip_trim_tail_frames"], [14, 9])
        self.assertEqual(recovered_body["per_clip_frames"], [158, 345])

    def test_seamless_keyframes_use_supported_ref2va_semantic_conditioning(self):
        clips = [{"video_prompt": "One continuous tracking shot."}]
        planned = [{"start": 0, "end": 20, "duration_sec": 20}]
        body = self._base_generation_params()
        body.update({
            "image_refs": ["keyframe-a.png", "keyframe-b.png"],
            "frames_positions": "200 400",
            "video_prompt_type": "KFI",
        })
        plan = pipeline._prepare_director_h3_longform(
            body,
            params={"h3_ref2va_terms_accepted": True},
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )

        self.assertEqual(
            body["image_refs"], ["keyframe-a.png", "keyframe-b.png"],
        )
        self.assertNotIn("frames_positions", body)
        self.assertEqual(
            body["custom_settings"]["h3_director_keyframes"],
            "semantic_references",
        )
        self.assertNotIn("per_clip_keyframes", body)
        self.assertIn(
            pipeline._H3_REF2VA_MODEL,
            {item["model_type"] for item in plan["segment_models"]},
        )
        self.assertEqual(
            plan["director_keyframe_conditioning"], "semantic_references",
        )

    def test_manual_fl2va_rejects_semantic_references_consistently(self):
        for duration in (10.0, 20.0):
            with self.subTest(duration=duration):
                clips, planned = self._scene(duration)
                body = self._base_generation_params()
                body["image_refs"] = ["character.png"]
                with self.assertRaisesRegex(ValueError, "Manual FL2VA.*semantic"):
                    pipeline._prepare_director_h3_longform(
                        body,
                        params={"h3_adaptive_conditioning": False},
                        clip_plans=clips,
                        planned_clips=planned,
                        fps=24,
                    )

    def test_remote_parent_origin_is_copied_to_child_job(self):
        pid = "remote-director"
        pipeline._pipelines[pid] = {
            "id": pid,
            "status": "running",
            "params": {},
            "source_remote": True,
        }

        def complete(job_id: str) -> None:
            pipeline._jobs[job_id]["status"] = "completed"

        pipeline._run_generation = complete
        outputs = pipeline._submit_and_wait(
            {"_director_pipeline_id": pid}, timeout_s=1,
        )
        self.assertEqual(outputs, [])
        child = next(iter(pipeline._jobs.values()))
        self.assertTrue(child["source_remote"])

    def test_start_pipeline_captures_remote_request_before_worker_thread(self):
        request_remote = ContextVar("test_remote_main", default=False)
        imported_remote = ContextVar("test_remote_import", default=False)
        token = request_remote.set(True)
        fake_launch = SimpleNamespace(_request_remote=imported_remote)
        resolved_dir = os.path.join(self.temp_dir.name, "remote-project")
        main_module = sys.modules["__main__"]
        try:
            with (
                patch.dict(sys.modules, {"launch": fake_launch}),
                patch.object(
                    main_module,
                    "_request_remote",
                    request_remote,
                    create=True,
                ),
                patch.object(
                    main_module,
                    "_workspace_dir",
                    return_value=resolved_dir,
                    create=True,
                ) as workspace_dir,
                patch.object(pipeline, "_start_pipeline_worker") as start_worker,
            ):
                pid = pipeline.start_pipeline({
                    "auto_mode": True,
                    "workspace": "remote-project",
                })
        finally:
            request_remote.reset(token)

        self.assertTrue(pipeline._pipelines[pid]["source_remote"])
        self.assertEqual(pipeline._pipelines[pid]["out_dir"], resolved_dir)
        workspace_dir.assert_called_once_with("remote-project")
        start_worker.assert_called_once_with(pid)

    def test_director_uses_llm_load_models_full_idempotence_key(self):
        pipeline._wgp.server_config = {
            "services": {
                "llm_model_id": "org/model",
                "llm_device": "cuda",
                "llm_provider": "local",
            },
        }
        with patch.object(llm_service, "load_model") as load_model:
            pipeline._ensure_llm_loaded({})

        load_model.assert_called_once_with(
            model_id="org/model",
            device="cuda",
            provider="local",
            remote_url="",
            api_key="",
        )


if __name__ == "__main__":
    unittest.main()
