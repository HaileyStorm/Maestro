"""Model-free Director regressions for MiniMax H3 child generation."""
from __future__ import annotations

import copy
import hashlib
import json
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
from services.director.h3_dialogue import (  # noqa: E402
    validate_h3_context_ir_records,
)
from services.h3_upstream_skills import (  # noqa: E402
    builtin_catalog,
    resolve_h3_style_workflow,
)


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
            "subject_definitions:\n<Subject 1> is the runner: adult runner in a red coat\n"
            "[Shot 1] <Subject 1> (the runner) crosses the station without a cut.\n"
            "[Shot 2] At 00:15.000, cut to a close-up as the train arrives "
            "beside <Subject 1>."
        )
        return (
            [{"video_prompt": prompt, "window_prompts": [], "window_count": 1}],
            [{"start": 0, "end": duration, "duration_sec": duration}],
        )

    @staticmethod
    def _ref_scene(duration: float) -> tuple[list[dict], list[dict]]:
        prompt = f"""subject_definitions: <Subject 1> is Mara from <Picture 1>.
summary: Preserve the authored reference scene.
retention_analysis: Fully preserve <Subject 1> from <Picture 1>.
detailed_description:
[Shot 1] [0.00s-{duration:.2f}s] shot_name: Reference action | audiovisual_description: <Subject 1> waits beside the door. | dialogue_and_vocalizations: none
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
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

    @staticmethod
    def _style_workflow() -> dict:
        catalog = builtin_catalog()
        return resolve_h3_style_workflow(
            catalog["styles"][0]["id"], catalog,
        )

    def test_style_workflow_is_inside_canonical_base_and_sealed_for_recovery(self):
        clips, planned = self._scene(20.0)
        body = self._base_generation_params()
        workflow = self._style_workflow()
        params = {
            "h3_ref2va_terms_accepted": True,
            "h3_style_workflow": workflow,
        }
        plan = pipeline._prepare_director_h3_longform(
            body,
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        self.assertEqual(plan["h3_style_workflow"], workflow)
        self.assertEqual(plan["shot_plan"]["h3_style_workflow"], workflow)
        self.assertEqual(body["h3_style_workflow"], workflow)
        marker = f"H3 workflow guidance [{workflow['id']}]:"
        for prompt, frames in zip(
            body["per_clip_prompts"], plan["clip_published_frames"],
        ):
            visual = prompt.split(
                "integrated_multimodal_description:", 1,
            )[1].split("overall_soundscape:", 1)[0]
            self.assertIn(marker, visual)
            self.assertEqual(
                validate_h3_context_ir_records(
                    prompt, mode="t2va", duration_seconds=frames / 24,
                ),
                [],
            )

        restored_body = self._base_generation_params()
        restored_params = {
            "h3_ref2va_terms_accepted": True,
            "h3_style_workflow": copy.deepcopy(workflow),
            "_h3_longform": copy.deepcopy(plan),
        }
        restored = pipeline._prepare_director_h3_longform(
            restored_body,
            params=restored_params,
            clip_plans=[],
            planned_clips=[],
            fps=24,
        )
        self.assertEqual(restored_body["per_clip_prompts"], body["per_clip_prompts"])
        self.assertEqual(restored_body["h3_style_workflow"], workflow)

        drifted = copy.deepcopy(plan)
        drifted["h3_style_workflow"]["brief_commitment"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "drifted"):
            pipeline._prepare_director_h3_longform(
                self._base_generation_params(),
                params={
                    "h3_ref2va_terms_accepted": True,
                    "h3_style_workflow": workflow,
                    "_h3_longform": drifted,
                },
                clip_plans=[],
                planned_clips=[],
                fps=24,
            )

    def test_style_workflow_is_inside_ref2va_detailed_description(self):
        clips, planned = self._ref_scene(20.0)
        body = self._base_generation_params()
        body.pop("image_start")
        body["image_prompt_type"] = ""
        workflow = self._style_workflow()
        plan = pipeline._prepare_director_h3_longform(
            body,
            params={
                "h3_ref2va_terms_accepted": True,
                "h3_style_workflow": workflow,
            },
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        self.assertIsNotNone(plan)
        marker = f"H3 workflow guidance [{workflow['id']}]:"
        for prompt, frames in zip(
            body["per_clip_prompts"], plan["clip_published_frames"],
        ):
            visual = prompt.split("detailed_description:", 1)[1].split(
                "overall_soundscape:", 1,
            )[0]
            self.assertIn(marker, visual)
            self.assertNotIn(
                "Preserve the authored reference scene.", prompt,
            )
            self.assertRegex(
                prompt,
                r"summary: (?:Execute only the segment-local|Continue only the established)",
            )
            self.assertEqual(
                validate_h3_context_ir_records(
                    prompt, mode="ref2va", duration_seconds=frames / 24,
                ),
                [],
            )

    def test_style_workflow_is_validated_as_director_planning_style_presence(self):
        workflow = self._style_workflow()
        for visual_style in ("", "hand-painted gouache"):
            with self.subTest(visual_style=visual_style):
                self.assertTrue(
                    pipeline._director_h3_style_workflow_present({
                        "video_model": pipeline._H3_BASE_FL2VA_MODEL,
                        "visual_style": visual_style,
                        "h3_style_workflow": copy.deepcopy(workflow),
                    })
                )
        with self.assertRaisesRegex(ValueError, "non-H3"):
            pipeline._director_h3_style_workflow_present({
                "video_model": "other",
                "h3_style_workflow": workflow,
            })

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
                self.assertIn("[15.000s-", plan["global_prompt"])
                semantic = plan["shot_plan"]["semantic_shots"]
                self.assertEqual(len(semantic), 1)
                self.assertEqual(len(set(body["per_clip_prompts"])), plan["clip_count"])
                self.assertTrue(semantic[0]["prompt_rewrite_for_physical_split"])
                for prompt, frames in zip(
                    body["per_clip_prompts"], plan["clip_published_frames"],
                ):
                    self.assertEqual(
                        validate_h3_context_ir_records(
                            prompt,
                            mode="t2va",
                            duration_seconds=frames / 24,
                        ),
                        [],
                    )
                for event_text in ("crosses the station", "train arrives"):
                    event = next(
                        item for item in semantic[0]["event_ownership"]
                        if event_text in item["executable_payload"]
                    )
                    self.assertEqual(
                        sum(
                            event_text in item
                            for item in body["per_clip_prompts"]
                        ),
                        1 + len(event["continuation_slices"]),
                    )
                self.assertEqual(
                    validate_h3_context_ir_records(
                        semantic[0]["semantic_prompt"],
                        mode="t2va",
                        duration_seconds=duration,
                    ),
                    [],
                )
                self.assertTrue(any(
                    boundary["type"] in {"precut", "cut"}
                    for boundary in plan["clip_boundaries"]
                ))

    def test_ref2va_effective_segment_requires_and_carries_terms(self):
        clips, planned = self._ref_scene(20.0)
        body = self._base_generation_params()
        body.pop("image_start")
        body["image_prompt_type"] = ""
        with self.assertRaisesRegex(ValueError, "Ref2VA.*terms"):
            pipeline._prepare_director_h3_longform(
                body,
                params={},
                clip_plans=clips,
                planned_clips=planned,
                fps=24,
            )

        accepted = self._base_generation_params()
        accepted.pop("image_start")
        accepted["image_prompt_type"] = ""
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
        self.assertTrue(all(
            item["model_type"] == pipeline._H3_REF2VA_MODEL
            for item in plan["segment_models"]
        ))
        self.assertTrue(all(
            "detailed_description:" in prompt
            for prompt in plan["shot_plan"]["clip_prompts"]
        ))

        anchored = self._base_generation_params()
        with self.assertRaisesRegex(
            ValueError, "Ref2VA prompt schema cannot be paired",
        ):
            pipeline._prepare_director_h3_longform(
                anchored,
                params={"h3_ref2va_terms_accepted": True},
                clip_plans=clips,
                planned_clips=planned,
                fps=24,
            )

    def test_video_phase_fails_terms_gate_before_child_submission(self):
        clips, planned = self._ref_scene(20.0)
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
                self.assertEqual(
                    validate_h3_context_ir_records(
                        body["per_clip_prompts"][cut_index + 1],
                        mode="t2va",
                        duration_seconds=(
                            plan["clip_published_frames"][cut_index + 1] / 24
                        ),
                    ),
                    [],
                )
                self.assertIn(
                    "cut to a close-up as the train arrives",
                    body["per_clip_prompts"][cut_index + 1],
                )
                if final_frame:
                    self.assertEqual(body["image_end"][-1], final_frame)
                    self.assertEqual(
                        plan["segment_models"][-1]["reason"],
                        "supplied final-frame anchor",
                    )

    def test_base_scenes_keep_schema_compatible_fl2va_routing(self):
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
            pipeline._H3_BASE_FL2VA_MODEL,
        )

    def test_explicit_metadata_does_not_select_pinkcherry_for_director(self):
        clips = [{"video_prompt": "A single continuous shot."}]
        planned = [{"start": 0, "end": 10, "duration_sec": 10}]
        body = self._base_generation_params()
        body.pop("image_start")
        body["image_prompt_type"] = ""
        body["video_length"] = 240
        plan = pipeline._prepare_director_h3_longform(
            body,
            params={"explicit_output": True},
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )

        self.assertIsNone(plan)
        self.assertEqual(body["model_type"], pipeline._H3_BASE_FL2VA_MODEL)
        self.assertEqual(body["per_clip_prompts"], [body["prompt"]])
        self.assertEqual(
            validate_h3_context_ir_records(
                body["prompt"], mode="t2va", duration_seconds=10,
            ),
            [],
        )
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
            shot_id="shot-1",
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
        self.assertEqual(contract["shot_id"], "shot-1")
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
        for semantic in restored["shot_plan"]["semantic_shots"]:
            frames = sum(
                restored["clip_published_frames"][index]
                for index in semantic["segment_indices"]
            )
            self.assertEqual(
                validate_h3_context_ir_records(
                    semantic["semantic_prompt"],
                    mode="t2va",
                    duration_seconds=frames / 24,
                ),
                [],
            )

    def test_committed_semantic_shot_rejects_physical_prompt_drift(self):
        import copy

        clips, planned = self._scene(20.0)
        body = self._base_generation_params()
        params = {
            "h3_ref2va_terms_accepted": True,
            "director_max_shot_frames": 243,
        }
        pipeline._prepare_director_h3_longform(
            body,
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        saved = copy.deepcopy(params["_h3_longform"])
        self.assertGreater(len(saved["shot_plan"]["clip_prompts"]), 1)
        saved["shot_plan"]["clip_prompts"][1] += " changed"
        saved["shot_plan"]["shots"][1]["prompt"] += " changed"

        replay_body = self._base_generation_params()
        with self.assertRaisesRegex(ValueError, "prompt bytes disagree"):
            pipeline._prepare_director_h3_longform(
                replay_body,
                params={
                    "h3_ref2va_terms_accepted": True,
                    "_h3_longform": saved,
                },
                clip_plans=[],
                planned_clips=[],
                fps=24,
            )

    def test_committed_v2_rejects_global_provenance_and_event_owner_drift(self):
        clips, planned = self._scene(20.0)
        params = {
            "h3_ref2va_terms_accepted": True,
            "director_max_shot_frames": 243,
        }
        pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        original = params["_h3_longform"]["shot_plan"]

        changed_global = copy.deepcopy(original)
        changed_global["global_prompt"] += " changed"
        with self.assertRaisesRegex(ValueError, "seal disagrees"):
            pipeline._canonicalize_director_h3_shot_plan(changed_global)

        changed_owner = copy.deepcopy(original)
        event = changed_owner["source_contracts"][0]["event_ownership"][-1]
        event["owner_segment_index"] = 0
        changed_owner["semantic_shots"] = copy.deepcopy(
            changed_owner["source_contracts"]
        )
        changed_owner["event_ownership"] = [
            copy.deepcopy(item)
            for contract in changed_owner["source_contracts"]
            for item in contract["event_ownership"]
        ]
        with self.assertRaisesRegex(ValueError, "event ownership disagrees"):
            pipeline._canonicalize_director_h3_shot_plan(changed_owner)

    def test_committed_v1_plan_replays_without_migration(self):
        clips, planned = self._scene(20.0)
        params = {"h3_ref2va_terms_accepted": True}
        pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        saved = copy.deepcopy(params["_h3_longform"])
        shot_plan = saved["shot_plan"]
        shot_plan["semantic_physical_contract_version"] = 1
        shot_plan.pop("prompt_contract_seal", None)
        shot_plan.pop("event_ownership", None)
        v1_prompts = [""] * len(shot_plan["clip_prompts"])
        for contract in shot_plan["source_contracts"]:
            contract["prompt_rewrite_for_physical_split"] = False
            contract.pop("physical_prompt_compiler_version", None)
            contract.pop("event_ownership", None)
            contract.pop("executable_prompt_sha256", None)
            for position in contract["segment_indices"]:
                v1_prompts[position] = contract["semantic_prompt"]
        shot_plan["clip_prompts"] = v1_prompts
        for index, shot in enumerate(shot_plan["shots"]):
            shot["prompt"] = v1_prompts[index]
        for item in shot_plan["dialogue_manifest"]:
            contract = shot_plan["source_contracts"][item["source_index"]]
            item["segment_index"] = contract["segment_indices"][0]
        for contract in shot_plan["source_contracts"]:
            contract["dialogue_manifest"] = [
                copy.deepcopy(item)
                for item in shot_plan["dialogue_manifest"]
                if item["source_index"] == contract["source_index"]
            ]
        for index, shot in enumerate(shot_plan["shots"]):
            shot["dialogue_manifest_indices"] = [
                manifest_index
                for manifest_index, item in enumerate(shot_plan["dialogue_manifest"])
                if item["segment_index"] == index
            ]
        shot_plan["semantic_shots"] = copy.deepcopy(
            shot_plan["source_contracts"]
        )

        restored = pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params={
                "h3_ref2va_terms_accepted": True,
                "_h3_longform": saved,
            },
            clip_plans=[],
            planned_clips=[],
            fps=24,
        )
        self.assertEqual(
            restored["shot_plan"]["semantic_physical_contract_version"], 1,
        )
        self.assertEqual(
            restored["shot_plan"]["clip_prompts"], v1_prompts,
        )

    def test_committed_semantic_contract_rejects_corrupt_replay_metadata(self):
        clips, planned = self._scene(20.0)
        params = {
            "h3_ref2va_terms_accepted": True,
            "director_max_shot_frames": 243,
        }
        pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        original = params["_h3_longform"]["shot_plan"]

        future = copy.deepcopy(original)
        future["semantic_physical_contract_version"] = 99
        with self.assertRaisesRegex(ValueError, "version is unsupported"):
            pipeline._canonicalize_director_h3_shot_plan(future)

        missing_shot = copy.deepcopy(original)
        missing_shot["shots"].pop()
        with self.assertRaisesRegex(ValueError, "shot records are incomplete"):
            pipeline._canonicalize_director_h3_shot_plan(missing_shot)

        missing_semantic = copy.deepcopy(original)
        missing_semantic["semantic_shots"] = []
        with self.assertRaisesRegex(ValueError, "semantic shot copies disagree"):
            pipeline._canonicalize_director_h3_shot_plan(missing_semantic)

        corruptions = (
            (
                "execution slice geometry disagrees",
                lambda plan: plan["source_contracts"][0]["execution_slices"][0].update(
                    {"start_frame": 777}
                ),
            ),
            (
                "semantic shot is incomplete",
                lambda plan: plan["source_contracts"][0].update(
                    {"source_index": 1, "semantic_shot_index": 1}
                ),
            ),
            (
                "physical segment metadata disagrees",
                lambda plan: plan["shots"][0].update(
                    {"authored_shot_id": "wrong-authored-id"}
                ),
            ),
            (
                "physical segment metadata disagrees",
                lambda plan: plan["shots"][1].update(
                    {"execution_cursor_frame": -999}
                ),
            ),
            (
                "physical segment metadata disagrees",
                lambda plan: plan["shots"][1].update(
                    {"predecessor_physical_segment_id": "wrong-segment"}
                ),
            ),
        )
        for message, mutate in corruptions:
            with self.subTest(message=message):
                saved = copy.deepcopy(original)
                mutate(saved)
                # The persisted semantic_shots copy must match exactly too.
                if saved["semantic_shots"] != saved["source_contracts"]:
                    saved["semantic_shots"] = copy.deepcopy(
                        saved["source_contracts"]
                    )
                with self.assertRaisesRegex(ValueError, message):
                    pipeline._canonicalize_director_h3_shot_plan(saved)

    def test_committed_dialogue_manifest_requires_complete_ordered_coverage(self):
        dialogue = "<d>[English] Ready.</d>"
        prompt = (
            "subject_definitions: <Subject 1> is Mara: adult medic in a blue coat.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0.00s-20.00s] shot_name: Ready | "
            "audiovisual_description: <Subject 1> (Mara) waits by the door. | "
            f"dialogue_and_vocalizations: <Subject 1> (S1) says: {dialogue}\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        params = {
            "h3_ref2va_terms_accepted": True,
            "director_max_shot_frames": 243,
        }
        pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params=params,
            clip_plans=[{"video_prompt": prompt}],
            planned_clips=[{"start": 0, "end": 20, "duration_sec": 20}],
            fps=24,
        )
        original = params["_h3_longform"]["shot_plan"]
        self.assertEqual(len(original["dialogue_manifest"]), 1)

        missing = copy.deepcopy(original)
        missing["dialogue_manifest"] = []
        with self.assertRaisesRegex(ValueError, "manifest coverage disagrees"):
            pipeline._canonicalize_director_h3_shot_plan(missing)

        bad_indices = copy.deepcopy(original)
        bad_indices["shots"][0]["dialogue_manifest_indices"] = []
        with self.assertRaisesRegex(ValueError, "physical segment metadata disagrees"):
            pipeline._canonicalize_director_h3_shot_plan(bad_indices)

    def test_mixed_authored_and_structured_dialogue_round_trips_in_text_order(self):
        from services.h3_shot_planner import plan_h3_native_shots

        authored = "<d>[English] Authored first.</d>"
        structured = "<d>[English] Structured second.</d>"
        prompt = (
            "subject_definitions: <Subject 1> is Mara.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0.00s-20.00s] shot_name: Two lines | "
            "audiovisual_description: <Subject 1> waits by the door. | "
            f"dialogue_and_vocalizations: <Subject 1> says: {authored}\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        shot_plan = plan_h3_native_shots(
            global_prompt=prompt,
            source_prompts=[prompt],
            source_indices=[0, 0],
            structured_shots=[{
                "authored_shot_id": "authored-dialogue",
                "dialogue_beats": [{
                    "speaker_id": "mara",
                    "spoken_text": "Structured second.",
                }],
            }],
            clip_frame_counts=[243, 243],
            clip_requested_frames=[240, 240],
            fps=24,
        )
        self.assertEqual(
            [item["exact_block"] for item in shot_plan["dialogue_manifest"]],
            [authored, structured],
        )
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan),
            shot_plan["clip_prompts"],
        )

    def test_legacy_bare_saved_child_prompts_recompile_to_canonical(self):
        import copy

        clips = [{"video_prompt": "Beat one. Beat two. Beat three."}]
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
        saved = copy.deepcopy(params["_h3_longform"])
        saved["shot_plan"].pop("semantic_physical_contract_version", None)
        saved["shot_plan"].pop("semantic_shots", None)
        legacy = []
        for index, frames in enumerate(saved["clip_published_frames"], start=1):
            prompt = (
                f"[0-{frames / 24:g}s] Saved child {index} keeps the "
                "described adult in place."
            )
            legacy.append(prompt)
            saved["shot_plan"]["shots"][index - 1]["prompt"] = prompt
        saved["shot_plan"]["clip_prompts"] = legacy
        saved["global_prompt"] = "Legacy plain root global scene."
        saved["shot_plan"]["global_prompt"] = (
            "Legacy plain shot-plan global scene."
        )
        replay_params = {
            "h3_ref2va_terms_accepted": True,
            "_h3_longform": saved,
        }
        replay_body = self._base_generation_params()
        restored = pipeline._prepare_director_h3_longform(
            replay_body,
            params=replay_params,
            clip_plans=[],
            planned_clips=[],
            fps=24,
        )

        for index, (prompt, frames) in enumerate(zip(
            replay_body["per_clip_prompts"], restored["clip_published_frames"],
        )):
            self.assertEqual(
                validate_h3_context_ir_records(
                    prompt, mode="t2va", duration_seconds=frames / 24,
                ),
                [],
            )
            self.assertEqual(
                restored["shot_plan"]["shots"][index]["prompt"], prompt,
            )
            self.assertNotRegex(prompt, r"(?m)^\[\d+(?:\.\d+)?-")
        self.assertEqual(
            replay_params["_h3_longform"]["shot_plan"]["clip_prompts"],
            replay_body["per_clip_prompts"],
        )
        canonical_global = pipeline._DIRECTOR_CLIP_SEPARATOR.join(
            replay_body["per_clip_prompts"],
        )
        self.assertEqual(restored["global_prompt"], canonical_global)
        self.assertEqual(
            restored["shot_plan"]["global_prompt"], canonical_global,
        )
        self.assertEqual(
            replay_params["_h3_longform"]["global_prompt"], canonical_global,
        )
        self.assertNotRegex(canonical_global, r"(?m)^\[\d+(?:\.\d+)?-")

    def test_multi_scene_legacy_globals_recompile_from_canonical_children(self):
        import copy

        clips = [
            {"video_prompt": "Legacy scene A keeps the runner in place."},
            {"video_prompt": "Legacy scene B keeps the pilot in place."},
        ]
        planned = [
            {"start": 0, "end": 10, "duration_sec": 10},
            {"start": 10, "end": 20, "duration_sec": 10},
        ]
        body = self._base_generation_params()
        params = {"h3_ref2va_terms_accepted": True}
        pipeline._prepare_director_h3_longform(
            body, params=params, clip_plans=clips, planned_clips=planned, fps=24,
        )
        saved = copy.deepcopy(params["_h3_longform"])
        saved["shot_plan"].pop("semantic_physical_contract_version", None)
        saved["shot_plan"].pop("prompt_contract_seal", None)
        saved["shot_plan"].pop("semantic_shots", None)
        legacy_global = "[0-10s] Legacy scene A.\n\n[0-10s] Legacy scene B."
        saved["global_prompt"] = legacy_global
        saved["shot_plan"]["global_prompt"] = legacy_global
        replay_params = {
            "h3_ref2va_terms_accepted": True,
            "_h3_longform": saved,
        }
        restored = pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params=replay_params,
            clip_plans=[],
            planned_clips=[],
            fps=24,
        )
        canonical_global = pipeline._DIRECTOR_CLIP_SEPARATOR.join(
            restored["shot_plan"]["clip_prompts"],
        )
        self.assertEqual(restored["global_prompt"], canonical_global)
        self.assertEqual(
            restored["shot_plan"]["global_prompt"], canonical_global,
        )
        self.assertIn("runner in place", canonical_global)
        self.assertIn("pilot in place", canonical_global)

    def test_empty_scene_uses_canonical_fallback_and_never_invents_dialogue(self):
        body = self._base_generation_params()
        body["per_clip_prompts"] = ["Fallback keeps the adult at the door."]
        result = pipeline._prepare_director_h3_longform(
            body,
            params={},
            clip_plans=[{"video_prompt": "", "window_prompts": []}],
            planned_clips=[{"start": 0, "end": 10, "duration_sec": 10}],
            fps=24,
        )
        self.assertIsNone(result)
        self.assertIn("Fallback keeps the adult at the door.", body["prompt"])
        self.assertNotIn("audiovisual_description: Dialogue", body["prompt"])
        self.assertEqual(
            validate_h3_context_ir_records(
                body["prompt"], mode="t2va", duration_seconds=10,
            ),
            [],
        )

        empty_body = self._base_generation_params()
        empty_body["prompt"] = ""
        with self.assertRaisesRegex(ValueError, "scene prompt is empty"):
            pipeline._prepare_director_h3_longform(
                empty_body,
                params={},
                clip_plans=[{"video_prompt": "", "window_prompts": []}],
                planned_clips=[{"start": 0, "end": 10, "duration_sec": 10}],
                fps=24,
            )

    def test_invalid_saved_version_and_schema_model_drift_fail_closed(self):
        body = self._base_generation_params()
        with (
            patch(
                "services.h3_shot_planner.plan_h3_native_shots",
                side_effect=AssertionError("invalid saved plan must not replan"),
            ),
            self.assertRaisesRegex(ValueError, "version is unsupported"),
        ):
            pipeline._prepare_director_h3_longform(
                body,
                params={"_h3_longform": {"shot_plan": {"version": 2}}},
                clip_plans=[],
                planned_clips=[],
                fps=24,
            )

        clips, planned = self._scene(20.0)
        params = {"h3_ref2va_terms_accepted": True}
        pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        saved = copy.deepcopy(params["_h3_longform"])
        saved["segment_models"][0]["model_type"] = pipeline._H3_REF2VA_MODEL
        with self.assertRaisesRegex(ValueError, "prompt schema and checkpoint disagree"):
            pipeline._prepare_director_h3_longform(
                self._base_generation_params(),
                params={"_h3_longform": saved},
                clip_plans=[],
                planned_clips=[],
                fps=24,
            )

    def test_legacy_saved_geometry_is_migrated_before_prompt_recompile(self):
        import copy

        clips, planned = self._scene(20.0)
        params = {"h3_ref2va_terms_accepted": True}
        pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        saved = copy.deepcopy(params["_h3_longform"])
        for container in (saved, saved["shot_plan"]):
            container.pop("clip_published_frames", None)
            container.pop("clip_trim_tail_frames", None)
        replay_params = {
            "h3_ref2va_terms_accepted": True,
            "_h3_longform": saved,
        }
        restored = pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params=replay_params,
            clip_plans=[],
            planned_clips=[],
            fps=24,
        )
        self.assertEqual(
            restored["clip_published_frames"],
            restored["shot_plan"]["clip_published_frames"],
        )
        self.assertEqual(
            restored["clip_trim_tail_frames"],
            restored["shot_plan"]["clip_trim_tail_frames"],
        )
        self.assertEqual(
            replay_params["_h3_longform"]["clip_published_frames"],
            restored["clip_published_frames"],
        )

    def test_director_multi_window_scene_prompt_is_canonical(self):
        prompt = pipeline._director_h3_scene_prompt(
            {
                "video_prompt": "",
                "window_prompts": [
                    "Mara waits beside the door.",
                    "Theo replies <d>[English] Keep this exact.</d>",
                ],
            },
            frame_count=480,
            fps=24,
        )
        self.assertEqual(
            validate_h3_context_ir_records(
                prompt, mode="t2va", duration_seconds=20,
            ),
            [],
        )
        self.assertIn("[Shot 1] [0.000s-10.000s]", prompt)
        self.assertIn("[Shot 2] [10.000s-20.000s]", prompt)
        self.assertIn("Mara waits beside the door", prompt)
        self.assertIn("<d>[English] Keep this exact.</d>", prompt)
        self.assertNotRegex(prompt, r"(?m)^\[\d+(?:\.\d+)?-")

    def test_ref2va_scene_prompt_preserves_the_six_field_contract_exactly(self):
        prompt = """subject_definitions: <Subject 1> is Mara from <Picture 1>.
summary: Preserve the authored reference scene.
retention_analysis: Fully preserve <Subject 1> from <Picture 1>.
detailed_description:
[Shot 1] [0.00s-30.00s] shot_name: Reference hold | audiovisual_description: <Subject 1> waits by the door. | dialogue_and_vocalizations: none
overall_soundscape: Quiet room tone.
non_diegetic_music: N/A"""
        self.assertEqual(
            pipeline._director_h3_scene_prompt(
                {"video_prompt": prompt, "window_prompts": []},
                frame_count=720,
                fps=24,
                mode="ref2va",
            ),
            prompt,
        )
        self.assertEqual(
            pipeline._director_h3_scene_prompt(
                {"video_prompt": prompt, "window_prompts": []},
                frame_count=720,
                fps=24,
            ),
            prompt,
        )

    def test_director_h3_rejects_wrapper_around_canonical_context_ir(self):
        canonical = pipeline._director_h3_scene_prompt(
            {"video_prompt": "Mara waits.", "window_prompts": []},
            frame_count=240,
            fps=24,
        )
        with self.assertRaisesRegex(ValueError, "wrapper text"):
            pipeline._director_h3_canonical_prompt(
                "NOTE: hidden wrapper\n" + canonical,
                duration_seconds=10,
            )

    def test_non_h3_longform_control_is_unchanged(self):
        body = {"model_type": "ltx2_22B_distilled", "prompt": "Keep me."}
        original = dict(body)
        self.assertIsNone(pipeline._prepare_director_h3_longform(
            body,
            params={},
            clip_plans=[{"video_prompt": "Keep me."}],
            planned_clips=[{"duration_sec": 10}],
            fps=24,
        ))
        self.assertEqual(body, original)

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

    def test_base_schema_rejects_semantic_keyframes_before_commit(self):
        clips = [{"video_prompt": "Beat one. Beat two. Beat three."}]
        planned = [{"start": 0, "end": 20, "duration_sec": 20}]
        original_body = self._base_generation_params()
        original_body.update({
            "image_refs": ["keyframe-a.png", "keyframe-b.png"],
            "frames_positions": "200 400",
            "video_prompt_type": "KFI",
        })
        with self.assertRaisesRegex(
            ValueError, "Base prompt schema cannot carry Ref2VA semantic references",
        ):
            pipeline._prepare_director_h3_longform(
                original_body,
                params={"h3_ref2va_terms_accepted": True},
                clip_plans=clips,
                planned_clips=planned,
                fps=24,
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

    def test_studio_and_director_share_geometry_while_director_is_canonical(self):
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
            director["clip_boundaries"], studio["clip_boundaries"],
        )
        for semantic in director["shot_plan"]["semantic_shots"]:
            frames = sum(
                director["clip_published_frames"][index]
                for index in semantic["segment_indices"]
            )
            self.assertEqual(
                validate_h3_context_ir_records(
                    semantic["semantic_prompt"],
                    mode="t2va",
                    duration_seconds=frames / 24,
                ),
                [],
            )
            self.assertNotRegex(
                semantic["semantic_prompt"], r"(?m)^\[\d+(?:\.\d+)?-",
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
            self.assertNotEqual(
                plan["shot_plan"]["clip_prompts"][0],
                plan["shot_plan"]["clip_prompts"][1],
            )
            self.assertEqual(
                sum(
                    item.count(dialogue)
                    for item in plan["shot_plan"]["clip_prompts"]
                ),
                1,
            )
            self.assertNotIn(
                "guest faces camera", plan["shot_plan"]["clip_prompts"][0],
            )
            self.assertIn(
                "guest faces camera", plan["shot_plan"]["clip_prompts"][1],
            )
        for semantic in director["shot_plan"]["semantic_shots"]:
            frames = sum(
                director["clip_published_frames"][index]
                for index in semantic["segment_indices"]
            )
            self.assertEqual(
                validate_h3_context_ir_records(
                    semantic["semantic_prompt"],
                    mode="t2va",
                    duration_seconds=frames / 24,
                ),
                [],
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
        self.assertEqual(
            recovered_body["per_clip_prompts"],
            director["shot_plan"]["clip_prompts"],
        )

    def test_seamless_keyframes_require_ref2va_prompt_schema(self):
        clips = [{"video_prompt": "One continuous tracking shot."}]
        planned = [{"start": 0, "end": 20, "duration_sec": 20}]
        body = self._base_generation_params()
        body.update({
            "image_refs": ["keyframe-a.png", "keyframe-b.png"],
            "frames_positions": "200 400",
            "video_prompt_type": "KFI",
        })
        with self.assertRaisesRegex(
            ValueError, "Base prompt schema cannot carry Ref2VA semantic references",
        ):
            pipeline._prepare_director_h3_longform(
                body,
                params={"h3_ref2va_terms_accepted": True},
                clip_plans=clips,
                planned_clips=planned,
                fps=24,
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

    def test_v2_replay_rejects_mutable_outer_runtime_controls(self):
        clips, planned = self._scene(20.0)
        params = {"h3_ref2va_terms_accepted": True}
        pipeline._prepare_director_h3_longform(
            self._base_generation_params(),
            params=params,
            clip_plans=clips,
            planned_clips=planned,
            fps=24,
        )
        original = params["_h3_longform"]
        for field, value in (
            ("segment_frames_maximum", 1),
            ("continuation", "semantic_references"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed[field] = value
                with self.assertRaisesRegex(ValueError, "runtime contract disagrees"):
                    pipeline._prepare_director_h3_longform(
                        self._base_generation_params(),
                        params={
                            "h3_ref2va_terms_accepted": True,
                            "_h3_longform": changed,
                        },
                        clip_plans=[],
                        planned_clips=[],
                        fps=24,
                    )

    def test_v2_resealed_event_owner_must_match_executable_payload(self):
        from services.h3_shot_planner import plan_h3_native_shots, seal_h3_shot_plan

        shot_plan = plan_h3_native_shots(
            global_prompt="An adult host enters. The host waits by the desk.",
            clip_frame_counts=[124, 124],
            fps=24,
        )
        event = shot_plan["source_contracts"][0]["event_ownership"][0]
        event.update({
            "owner_segment_index": 1,
            "owner_physical_segment_index": 1,
            "owner_physical_segment_id": "h3-authored-shot-1:segment-2",
        })
        shot_plan["semantic_shots"] = copy.deepcopy(
            shot_plan["source_contracts"]
        )
        shot_plan["event_ownership"] = [
            copy.deepcopy(item)
            for contract in shot_plan["source_contracts"]
            for item in contract["event_ownership"]
        ]
        seal_h3_shot_plan(shot_plan)

        with self.assertRaisesRegex(ValueError, "event ownership disagrees"):
            pipeline._canonicalize_director_h3_shot_plan(shot_plan)

    def test_v2_continuation_slices_round_trip_and_reject_resealed_drift(self):
        import hashlib
        from services.h3_shot_planner import plan_h3_native_shots, seal_h3_shot_plan

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
        shot_plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[144, 144, 192],
            fps=24,
        )
        expected = list(shot_plan["clip_prompts"])
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan), expected,
        )
        self.assertEqual(sum(dialogue in item for item in expected), 1)
        self.assertEqual(sum(action in item for item in expected), 3)

        changed = copy.deepcopy(shot_plan)
        continuation = changed["source_contracts"][0]["event_ownership"][0][
            "continuation_slices"
        ][0]
        continuation["source_end_frame_exclusive"] -= 1
        changed["semantic_shots"] = copy.deepcopy(changed["source_contracts"])
        changed["event_ownership"] = [
            copy.deepcopy(item)
            for contract in changed["source_contracts"]
            for item in contract["event_ownership"]
        ]
        seal_h3_shot_plan(changed)
        with self.assertRaisesRegex(ValueError, "event ownership disagrees"):
            pipeline._canonicalize_director_h3_shot_plan(changed)

        for injected in (
            "An extra action occurs. ",
            "<d>[English] Extra words.</d> ",
        ):
            with self.subTest(injected=injected):
                changed = copy.deepcopy(shot_plan)
                changed_prompt = changed["clip_prompts"][1].replace(
                    " | dialogue_and_vocalizations:",
                    f" {injected}| dialogue_and_vocalizations:",
                    1,
                )
                changed["clip_prompts"][1] = changed_prompt
                changed["shots"][1]["prompt"] = changed_prompt
                changed["source_contracts"][0]["executable_prompt_sha256"][1] = (
                    hashlib.sha256(changed_prompt.encode("utf-8")).hexdigest()
                )
                changed["semantic_shots"] = copy.deepcopy(
                    changed["source_contracts"]
                )
                seal_h3_shot_plan(changed)
                with self.assertRaisesRegex(
                    ValueError, "physical prompt semantics disagree",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(changed)

    def test_v2_untimed_and_final_blocking_events_replay(self):
        from services.h3_shot_planner import plan_h3_native_shots

        for prompt in (
            "An adult host enters. The host waits beside the desk.",
            "An adult host enters.\nFINAL BLOCKING: The host faces camera.",
            "[0-4s] An adult host enters. FINAL BLOCKING: The host faces camera.",
        ):
            with self.subTest(prompt=prompt):
                shot_plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    clip_frame_counts=[124, 124],
                    fps=24,
                )
                self.assertTrue(all(
                    item["continuation_slices"] == []
                    for item in shot_plan["event_ownership"]
                ))
                self.assertEqual(
                    pipeline._canonicalize_director_h3_shot_plan(shot_plan),
                    shot_plan["clip_prompts"],
                )

    def test_v2_partial_timed_action_fills_local_gaps_and_replays(self):
        from services.h3_shot_planner import plan_h3_native_shots

        action = "An adult courier carries a sealed case."
        shot_plan = plan_h3_native_shots(
            global_prompt=f"[5-18s] {action}",
            clip_frame_counts=[240, 240],
            fps=24,
        )
        self.assertIn("[0-5s] Continue the established", shot_plan["clip_prompts"][0])
        self.assertIn("[8-10s] Continue the established", shot_plan["clip_prompts"][1])
        self.assertEqual(sum(action in item for item in shot_plan["clip_prompts"]), 2)
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan),
            shot_plan["clip_prompts"],
        )

    def test_v2_point_actions_use_one_frame_half_open_geometry_and_replay(self):
        from services.h3_shot_planner import plan_h3_native_shots

        for timestamp, expected_range in ((0, (0, 1)), (5, (120, 121))):
            with self.subTest(timestamp=timestamp):
                shot_plan = plan_h3_native_shots(
                    global_prompt=(
                        f"At {timestamp} seconds, an adult host waves."
                    ),
                    clip_frame_counts=[240],
                    fps=24,
                )
                event = shot_plan["event_ownership"][0]
                self.assertEqual(event["kind"], "point")
                self.assertEqual(
                    (
                        event["source_start_frame"],
                        event["source_end_frame_exclusive"],
                    ),
                    expected_range,
                )
                self.assertIn(
                    "[0-0.042s]" if timestamp == 0 else "[5-5.042s]",
                    shot_plan["clip_prompts"][0],
                )
                self.assertEqual(
                    pipeline._canonicalize_director_h3_shot_plan(shot_plan),
                    shot_plan["clip_prompts"],
                )

        boundary_plan = plan_h3_native_shots(
            global_prompt=(
                "[0-5s] An adult host crosses the room.\n"
                "At 5 seconds, the host waves."
            ),
            clip_frame_counts=[120, 120],
            fps=24,
        )
        point = next(
            item for item in boundary_plan["event_ownership"]
            if item["kind"] == "point"
        )
        self.assertEqual(point["owner_segment_index"], 1)
        self.assertEqual(point["owner_physical_segment_index"], 1)
        self.assertEqual(point["local_start_frame"], 0)
        self.assertEqual(point["local_end_frame_exclusive"], 1)
        self.assertIn("[0-0.042s]", boundary_plan["clip_prompts"][1])
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(boundary_plan),
            boundary_plan["clip_prompts"],
        )

    def test_v2_ambiguous_inline_opening_dialogue_fails_before_sealing(self):
        from services.h3_shot_planner import H3ShotPlanError, plan_h3_native_shots

        for suffix in (
            "The host says Hello.",
            "while remaining seated. The host walks.",
            "“The host says Hello.”",
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
                        clip_frame_counts=[48, 48],
                        fps=24,
                    )

    def test_v2_exact_structured_opening_dialogue_without_punctuation_replays(self):
        from services.h3_shot_planner import plan_h3_native_shots

        opening_dialogue = "<d>[English] Ready</d>"
        prompt = (
            "subject_definitions: <Subject 1> is an adult host.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Studio | "
            "audiovisual_description: <Subject 1> studies a ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        shot_plan = plan_h3_native_shots(
            global_prompt=prompt,
            structured_shots=[{
                "spatial_setup": f"The host says {opening_dialogue}",
            }],
            clip_frame_counts=[240, 240],
            fps=24,
        )
        self.assertEqual(
            sum(
                item.count(opening_dialogue)
                for item in shot_plan["clip_prompts"]
            ),
            1,
        )
        self.assertNotIn("Opening blocking", shot_plan["clip_prompts"][1])
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan),
            shot_plan["clip_prompts"],
        )

    def test_v2_line_closed_opening_preserves_later_dialogue_association(self):
        from services.h3_shot_planner import plan_h3_native_shots

        opening_dialogue = "<d>[English] Ready</d>"
        later_dialogue = "<d>[English] Hello</d>"
        shot_plan = plan_h3_native_shots(
            global_prompt=(
                "[0-4s] OPENING BLOCKING: The host says "
                f"{opening_dialogue}\n"
                f"The host says {later_dialogue} and walks."
            ),
            clip_frame_counts=[48, 48],
            fps=24,
        )
        self.assertEqual(
            [
                item["exact_block"]
                for item in shot_plan["dialogue_manifest"]
            ],
            [later_dialogue, opening_dialogue],
        )
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan),
            shot_plan["clip_prompts"],
        )

    def test_v2_replay_rejects_balanced_noncanonical_dialogue_tags(self):
        from services.h3_shot_planner import plan_h3_native_shots, seal_h3_shot_plan

        for dialogue in (
            "<d>Ready</d>",
            "<d>[English] </d>",
            "<d>[English] \t\n </d>",
            "<d>[ ] hello</d>",
        ):
            with self.subTest(dialogue=dialogue):
                shot_plan = plan_h3_native_shots(
                    global_prompt="[0-4s] An adult host says TOKEN_READY.",
                    clip_frame_counts=[48, 48],
                    fps=24,
                )
                shot_plan["source_contracts"][0]["semantic_prompt"] = (
                    f"[0-4s] An adult host says {dialogue}."
                )
                shot_plan["semantic_shots"] = copy.deepcopy(
                    shot_plan["source_contracts"]
                )
                seal_h3_shot_plan(shot_plan)
                with self.assertRaisesRegex(
                    ValueError, r"canonical <d>\[language\]",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(shot_plan)

    def test_v2_replay_rejects_resealed_semantic_prompt_field_tamper(self):
        from services.h3_shot_planner import plan_h3_native_shots, seal_h3_shot_plan

        clips, _planned = self._ref_scene(20.0)
        prompt = clips[0]["video_prompt"]
        shot_plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[240, 240],
            fps=24,
        )
        shot_plan["source_contracts"][0]["semantic_prompt"] = (
            shot_plan["source_contracts"][0]["semantic_prompt"].replace(
                "summary: Preserve the authored reference scene.",
                "summary: TAMPERED summary.",
            )
        )
        shot_plan["semantic_shots"] = copy.deepcopy(
            shot_plan["source_contracts"]
        )
        seal_h3_shot_plan(shot_plan)
        with self.assertRaisesRegex(
            ValueError, "semantic prompt provenance disagrees",
        ):
            pipeline._canonicalize_director_h3_shot_plan(shot_plan)

        pristine = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[240, 240],
            fps=24,
        )
        for field, value in (
            ("prompt_changed_before_split", "yes"),
            (
                "prompt_changed_before_split",
                not pristine["source_contracts"][0][
                    "prompt_changed_before_split"
                ],
            ),
            ("authored_final_blocking", "The host sits."),
        ):
            with self.subTest(field=field, value=value):
                tampered = copy.deepcopy(pristine)
                tampered["source_contracts"][0][field] = value
                tampered["semantic_shots"] = copy.deepcopy(
                    tampered["source_contracts"]
                )
                seal_h3_shot_plan(tampered)
                with self.assertRaisesRegex(
                    ValueError, "authored prompt provenance disagrees",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(tampered)

    def test_v2_replay_rejects_masked_semantic_compiler_inputs(self):
        from services.h3_shot_planner import plan_h3_native_shots, seal_h3_shot_plan

        dialogue = "<d>[English] Ready.</d>"
        cases = (
            (
                "[0-4s] An adult host waits.",
                "visual_context",
                "An adult host",
            ),
            (
                "[0-4s] OPENING BLOCKING: Ready. The host walks.",
                "opening_blocking",
                "attacker opening",
            ),
            (
                f"[0-4s] The host says {dialogue}",
                "structured_dialogue_blocks",
                [dialogue],
            ),
        )
        for prompt, field, value in cases:
            with self.subTest(field=field):
                shot_plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    clip_frame_counts=[48, 48],
                    fps=24,
                )
                shot_plan["source_contracts"][0][field] = value
                shot_plan["semantic_shots"] = copy.deepcopy(
                    shot_plan["source_contracts"]
                )
                seal_h3_shot_plan(shot_plan)
                with self.assertRaisesRegex(
                    ValueError, "semantic compiler inputs are not canonical",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(shot_plan)

    def test_v2_replay_rejects_opening_claimed_from_later_action(self):
        from services.h3_shot_planner import plan_h3_native_shots, seal_h3_shot_plan

        prompt_without_opening = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: <Subject 1> stands and studies the ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        valid = plan_h3_native_shots(
            global_prompt=prompt_without_opening,
            structured_shots=[{"spatial_setup": "stands"}],
            clip_frame_counts=[240, 240],
            fps=24,
        )
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(valid),
            valid["clip_prompts"],
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
                shot_plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    clip_frame_counts=[240, 240],
                    fps=24,
                )
                shot_plan["source_contracts"][0]["opening_blocking"] = (
                    invalid_opening
                )
                shot_plan["semantic_shots"] = copy.deepcopy(
                    shot_plan["source_contracts"]
                )
                seal_h3_shot_plan(shot_plan)
                with self.assertRaisesRegex(
                    ValueError,
                    "(?:semantic compiler inputs are not canonical|"
                    "structured opening blocking conflicts)",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(shot_plan)

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
        shot_plan = plan_h3_native_shots(
            global_prompt=final_prompt,
            clip_frame_counts=[240, 240],
            fps=24,
        )
        shot_plan["source_contracts"][0]["opening_blocking"] = (
            "attacker opening"
        )
        shot_plan["semantic_shots"] = copy.deepcopy(
            shot_plan["source_contracts"]
        )
        seal_h3_shot_plan(shot_plan)
        with self.assertRaisesRegex(
            ValueError,
            "(?:semantic compiler inputs are not canonical|"
            "structured opening blocking conflicts)",
        ):
            pipeline._canonicalize_director_h3_shot_plan(shot_plan)

    def test_v2_opening_punctuation_replays_and_multiple_fields_reject(self):
        from services.h3_shot_planner import plan_h3_native_shots, seal_h3_shot_plan

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
            with self.subTest(authored=authored):
                shot_plan = plan_h3_native_shots(
                    global_prompt=prompt(authored),
                    structured_shots=[{"spatial_setup": structured}],
                    clip_frame_counts=[240, 240],
                    fps=24,
                )
                self.assertEqual(
                    pipeline._canonicalize_director_h3_shot_plan(shot_plan),
                    shot_plan["clip_prompts"],
                )

        shot_plan = plan_h3_native_shots(
            global_prompt=prompt("First pose."),
            clip_frame_counts=[240, 240],
            fps=24,
        )
        multiple = prompt(
            "First pose. OPENING BLOCKING: Second pose."
        )
        shot_plan["source_contracts"][0]["authored_prompt"] = multiple
        shot_plan["source_contracts"][0]["semantic_prompt"] = multiple
        shot_plan["source_contracts"][0]["prompt_changed_before_split"] = False
        shot_plan["semantic_shots"] = copy.deepcopy(
            shot_plan["source_contracts"]
        )
        seal_h3_shot_plan(shot_plan)
        with self.assertRaisesRegex(
            ValueError, "multiple OPENING BLOCKING fields",
        ):
            pipeline._canonicalize_director_h3_shot_plan(shot_plan)

    def test_v2_canonical_structured_opening_punctuation_replays(self):
        from services.h3_shot_planner import plan_h3_native_shots

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
                shot_plan = plan_h3_native_shots(
                    global_prompt=prompt,
                    structured_shots=[{"spatial_setup": opening}],
                    clip_frame_counts=[96],
                    fps=24,
                )
                self.assertIn(
                    f"Opening blocking: {opening} <Subject 1> walks forward.",
                    shot_plan["clip_prompts"][0],
                )
                self.assertEqual(
                    pipeline._canonicalize_director_h3_shot_plan(shot_plan),
                    shot_plan["clip_prompts"],
                )

    def test_v2_each_canonical_record_replays_its_local_opening_once(self):
        from services.h3_shot_planner import plan_h3_native_shots

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
        shot_plan = plan_h3_native_shots(
            global_prompt=prompt,
            structured_shots=[{"spatial_setup": "stands"}],
            clip_frame_counts=[48, 48, 48],
            fps=24,
        )
        self.assertEqual(
            [
                item.casefold().count("opening blocking:")
                for item in shot_plan["clip_prompts"]
            ],
            [1, 1, 0],
        )
        self.assertNotIn(
            "stands beside the desk",
            shot_plan["clip_prompts"][2].casefold(),
        )
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan),
            shot_plan["clip_prompts"],
        )

    def test_v2_dialogue_terminated_opening_outer_punctuation_replays(self):
        from services.h3_shot_planner import plan_h3_native_shots

        dialogue = "<d>[English] Ready!</d>"
        generic = plan_h3_native_shots(
            global_prompt=(
                "[0-4s] OPENING BLOCKING: The host kneels and says "
                f"{dialogue}. The host walks."
            ),
            clip_frame_counts=[48, 48],
            fps=24,
        )
        self.assertNotIn(": . The host walks", generic["clip_prompts"][1])
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(generic),
            generic["clip_prompts"],
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
        self.assertNotIn(": . <Subject 1>", canonical["clip_prompts"][2])
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(canonical),
            canonical["clip_prompts"],
        )

    def test_v2_multisentence_opening_recovery_preserves_authored_provenance(self):
        from services.h3_shot_planner import (
            H3_COMPILER_INPUT_REPLAY_VERSION,
            plan_h3_native_shots,
            seal_h3_shot_plan,
        )

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
            pipeline._canonicalize_director_h3_shot_plan(replay),
            replay["clip_prompts"],
        )

        for changed in (
            opening.lower(),
            opening.replace(" ", "  ", 1),
            opening + " <Subject 1> studies",
        ):
            with self.subTest(changed=changed):
                tampered = copy.deepcopy(initial)
                tampered["source_contracts"][0]["opening_blocking"] = changed
                tampered["semantic_shots"] = copy.deepcopy(
                    tampered["source_contracts"]
                )
                seal_h3_shot_plan(tampered)
                with self.assertRaisesRegex(
                    ValueError, "semantic prompt provenance disagrees",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(tampered)

    def test_v2_exact_compiler_inputs_round_trip_visual_and_dialogue(self):
        from services.h3_shot_planner import (
            H3_COMPILER_INPUT_REPLAY_VERSION,
            plan_h3_native_shots,
            seal_h3_shot_plan,
        )

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

        self.assertEqual(replay["clip_prompts"], initial["clip_prompts"])
        self.assertEqual(replay["event_ownership"], initial["event_ownership"])
        self.assertEqual(replay["dialogue_manifest"], initial["dialogue_manifest"])
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(replay),
            replay["clip_prompts"],
        )

        for field, value in (
            ("visual_context", contract["visual_context"] + " attacker"),
            (
                "structured_dialogue_blocks",
                contract["structured_dialogue_blocks"]
                + ["<d>[English] Changed.</d>"],
            ),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(replay)
                tampered["source_contracts"][0][field] = value
                tampered["semantic_shots"] = copy.deepcopy(
                    tampered["source_contracts"]
                )
                seal_h3_shot_plan(tampered)
                with self.assertRaisesRegex(
                    ValueError,
                    "(?:semantic prompt provenance disagrees|"
                    "semantic compiler inputs are not canonical)",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(tampered)

    def test_v2_structured_opening_reserved_markers_reject(self):
        from services.h3_shot_planner import (
            plan_h3_native_shots,
            seal_h3_shot_plan,
        )

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
            shot_plan = plan_h3_native_shots(
                global_prompt=source,
                structured_shots=[{"spatial_setup": "stands"}],
                clip_frame_counts=[96],
                fps=24,
            )
            for opening in (
                "stands. FINAL BLOCKING: sits",
                "OPENING BLOCKING: sits",
            ):
                with self.subTest(canonical=source == canonical, opening=opening):
                    tampered = copy.deepcopy(shot_plan)
                    tampered["source_contracts"][0]["opening_blocking"] = opening
                    tampered["semantic_shots"] = copy.deepcopy(
                        tampered["source_contracts"]
                    )
                    seal_h3_shot_plan(tampered)
                    with self.assertRaisesRegex(
                        ValueError, "reserved structural marker",
                    ):
                        pipeline._canonicalize_director_h3_shot_plan(tampered)

    def test_v2_generic_distinct_final_sources_replay_exactly(self):
        from services.h3_shot_planner import plan_h3_native_shots

        shot_plan = plan_h3_native_shots(
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
            item for item in shot_plan["event_ownership"]
            if item["kind"] == "final_blocking"
        )
        self.assertEqual(
            final["executable_payload"],
            "Structured ending. Authored ending.",
        )
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan),
            shot_plan["clip_prompts"],
        )

    def test_v2_generic_duplicate_final_punctuation_replays_once(self):
        from services.h3_shot_planner import plan_h3_native_shots

        for authored, structured in (
            ("Host sits!", "host sits"),
            ("Host sits?", "HOST SITS."),
        ):
            with self.subTest(authored=authored, structured=structured):
                shot_plan = plan_h3_native_shots(
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
                    item for item in shot_plan["event_ownership"]
                    if item["kind"] == "final_blocking"
                )
                self.assertEqual(final["executable_payload"], authored)
                self.assertEqual(
                    pipeline._canonicalize_director_h3_shot_plan(shot_plan),
                    shot_plan["clip_prompts"],
                )

    def test_v2_repeated_dialogue_keeps_occurrence_provenance_after_reordering(self):
        from services.h3_shot_planner import plan_h3_native_shots, seal_h3_shot_plan

        dialogue = "<d>[English] Ready</d>"
        shot_plan = plan_h3_native_shots(
            global_prompt=(
                "[0-4s] OPENING BLOCKING: The host says "
                f"{dialogue}\n"
                f"The guest repeats {dialogue}."
            ),
            structured_shots=[{
                "dialogue_beats": [{
                    "exact_block": dialogue,
                    "speaker_id": "host-structured",
                }],
            }],
            clip_frame_counts=[48, 48],
            fps=24,
        )
        self.assertEqual(
            [item["exact_block"] for item in shot_plan["dialogue_manifest"]],
            [dialogue, dialogue],
        )
        self.assertEqual(
            [item["source"] for item in shot_plan["dialogue_manifest"]],
            ["semantic_prompt", "semantic_prompt"],
        )
        self.assertEqual(
            [item["speaker_id"] for item in shot_plan["dialogue_manifest"]],
            ["", ""],
        )
        self.assertEqual(
            [
                item["semantic_occurrence_index"]
                for item in shot_plan["dialogue_manifest"]
            ],
            [1, 0],
        )
        self.assertNotIn(
            "semantic_dialogue_provenance", shot_plan["source_contracts"][0],
        )
        changed = copy.deepcopy(shot_plan)
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan),
            shot_plan["clip_prompts"],
        )

        pristine = copy.deepcopy(changed)
        changed["dialogue_manifest"][:2] = reversed(
            changed["dialogue_manifest"][:2]
        )
        changed["source_contracts"][0]["dialogue_manifest"][:2] = reversed(
            changed["source_contracts"][0]["dialogue_manifest"][:2]
        )
        changed["semantic_shots"] = copy.deepcopy(changed["source_contracts"])
        seal_h3_shot_plan(changed)
        with self.assertRaisesRegex(ValueError, "dialogue"):
            pipeline._canonicalize_director_h3_shot_plan(changed)

        for field, value in (
            ("speaker_id", "attacker-voice"),
            ("source", "attacker_source"),
            ("spoken_text", "[English] Changed"),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(pristine)
                for manifest in (
                    tampered["dialogue_manifest"],
                    tampered["source_contracts"][0]["dialogue_manifest"],
                ):
                    item = next(
                        entry for entry in manifest
                        if entry["semantic_occurrence_index"] == 0
                    )
                    item[field] = value
                tampered["semantic_shots"] = copy.deepcopy(
                    tampered["source_contracts"]
                )
                seal_h3_shot_plan(tampered)
                with self.assertRaisesRegex(
                    ValueError, "dialogue (?:provenance|association)",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(tampered)

        coherently_tampered = copy.deepcopy(pristine)
        for manifest in (
            coherently_tampered["dialogue_manifest"],
            coherently_tampered["source_contracts"][0]["dialogue_manifest"],
        ):
            item = next(
                entry for entry in manifest
                if entry["semantic_occurrence_index"] == 0
            )
            item.update({
                "speaker_id": "attacker-voice",
                "source": "attacker_source",
                "spoken_text": "[English] Changed",
            })
        contract = coherently_tampered["source_contracts"][0]
        former_projection = [
            {
                field: item[field]
                for field in (
                    "semantic_occurrence_index", "exact_block", "spoken_text",
                    "speaker_id", "source", "source_index",
                )
            }
            for item in sorted(
                contract["dialogue_manifest"],
                key=lambda item: item["semantic_occurrence_index"],
            )
        ]
        contract["semantic_dialogue_provenance"] = former_projection
        contract["semantic_dialogue_provenance_sha256"] = hashlib.sha256(
            json.dumps(
                former_projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        coherently_tampered["semantic_shots"] = copy.deepcopy(
            coherently_tampered["source_contracts"]
        )
        seal_h3_shot_plan(coherently_tampered)
        with self.assertRaisesRegex(ValueError, "dialogue provenance"):
            pipeline._canonicalize_director_h3_shot_plan(coherently_tampered)

        for obsolete in (
            {"semantic_dialogue_provenance": {"not": "a list"}},
            {"semantic_dialogue_provenance_sha256": "bogus"},
        ):
            with self.subTest(obsolete=next(iter(obsolete))):
                tampered = copy.deepcopy(pristine)
                tampered["source_contracts"][0].update(obsolete)
                tampered["semantic_shots"] = copy.deepcopy(
                    tampered["source_contracts"]
                )
                seal_h3_shot_plan(tampered)
                with self.assertRaisesRegex(
                    ValueError, "obsolete dialogue provenance",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(tampered)

        for field, value in (
            ("speaker_id", 123),
            ("source", []),
            ("spoken_text", None),
        ):
            with self.subTest(field=field, invalid_type=True):
                tampered = copy.deepcopy(pristine)
                for manifest in (
                    tampered["dialogue_manifest"],
                    tampered["source_contracts"][0]["dialogue_manifest"],
                ):
                    item = next(
                        entry for entry in manifest
                        if entry["semantic_occurrence_index"] == 0
                    )
                    item[field] = value
                tampered["semantic_shots"] = copy.deepcopy(
                    tampered["source_contracts"]
                )
                seal_h3_shot_plan(tampered)
                with self.assertRaisesRegex(ValueError, "dialogue"):
                    pipeline._canonicalize_director_h3_shot_plan(tampered)

        for field in ("source_index", "semantic_shot_index", "segment_index"):
            with self.subTest(field=field, boolean=True):
                tampered = copy.deepcopy(pristine)
                for manifest in (
                    tampered["dialogue_manifest"],
                    tampered["source_contracts"][0]["dialogue_manifest"],
                ):
                    item = next(
                        entry for entry in manifest
                        if entry["semantic_occurrence_index"] == 0
                    )
                    item[field] = False
                tampered["semantic_shots"] = copy.deepcopy(
                    tampered["source_contracts"]
                )
                seal_h3_shot_plan(tampered)
                with self.assertRaisesRegex(ValueError, "dialogue"):
                    pipeline._canonicalize_director_h3_shot_plan(tampered)

        for ordinals in ((False, True), (0.0, 1.0)):
            with self.subTest(ordinals=ordinals):
                tampered = copy.deepcopy(pristine)
                for manifest in (
                    tampered["dialogue_manifest"],
                    tampered["source_contracts"][0]["dialogue_manifest"],
                ):
                    for item in manifest:
                        semantic_index = item["semantic_occurrence_index"]
                        item["semantic_occurrence_index"] = ordinals[
                            semantic_index
                        ]
                tampered["semantic_shots"] = copy.deepcopy(
                    tampered["source_contracts"]
                )
                seal_h3_shot_plan(tampered)
                with self.assertRaisesRegex(
                    ValueError, "dialogue identity",
                ):
                    pipeline._canonicalize_director_h3_shot_plan(tampered)

    def test_v2_dialogue_ordinals_do_not_consume_final_blocking_limit(self):
        from services.h3_shot_planner import plan_h3_native_shots

        dialogue = "<d>[English] Ready.</d>"
        blocking = f"{dialogue} " + ("state " * 192).strip()
        self.assertLess(len(blocking), 1200)
        shot_plan = plan_h3_native_shots(
            global_prompt=(
                "[0-4s] The host walks. "
                f"FINAL BLOCKING: {blocking}"
            ),
            clip_frame_counts=[48, 48],
            fps=24,
        )
        final = next(
            item for item in shot_plan["event_ownership"]
            if item["kind"] == "final_blocking"
        )
        self.assertEqual(final["executable_payload"], blocking)
        self.assertFalse(final["executable_payload"].endswith("..."))
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan),
            shot_plan["clip_prompts"],
        )

    def test_structured_canonical_blocking_remains_valid_context_ir(self):
        from services.h3_shot_planner import plan_h3_native_shots

        prompt = (
            "subject_definitions: <Subject 1> is an adult archivist.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0s-20s] shot_name: Archive | "
            "audiovisual_description: OPENING BLOCKING: the closed cabinet "
            "remains at frame left. <Subject 1> studies a ledger. "
            "Final blocking: the archivist closes the ledger. | "
            "dialogue_and_vocalizations: none\n"
            "overall_soundscape: Quiet room tone.\n"
            "non_diegetic_music: N/A"
        )
        shot_plan = plan_h3_native_shots(
            global_prompt=prompt,
            source_prompts=[prompt],
            source_indices=[0, 0],
            structured_shots=[{
                "spatial_setup": "the closed cabinet remains at frame left",
                "closing_blocking": "the archivist closes the ledger",
            }],
            clip_frame_counts=[240, 240],
            fps=24,
        )
        for prompt_bytes, frames in zip(
            shot_plan["clip_prompts"], shot_plan["clip_published_frames"],
        ):
            self.assertEqual(
                validate_h3_context_ir_records(
                    prompt_bytes,
                    mode="t2va",
                    duration_seconds=frames / 24,
                ),
                [],
            )
        self.assertEqual(
            sum(
                "closed cabinet remains" in item
                for item in shot_plan["clip_prompts"]
            ),
            1,
        )
        self.assertEqual(
            sum(
                "archivist closes the ledger" in item
                for item in shot_plan["clip_prompts"]
            ),
            1,
        )
        self.assertEqual(
            pipeline._canonicalize_director_h3_shot_plan(shot_plan),
            shot_plan["clip_prompts"],
        )


if __name__ == "__main__":
    unittest.main()
