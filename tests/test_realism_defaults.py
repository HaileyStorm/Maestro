"""Focused contracts for the conditional visual-style default."""
from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import director_pipeline, llm_service  # noqa: E402
from services.director.policies import (  # noqa: E402
    DEFAULT_VISUAL_STYLE,
    anchor_visual_prompt,
    build_camera_style_block,
    build_visual_style_default_block,
    build_visual_style_refinement_block,
    resolve_planned_visual_style,
    resolve_visual_style,
)
from services.director.orchestrator import (  # noqa: E402
    DirectorFlags,
    DirectorOrchestrator,
)
from services.director.h3_dialogue import (  # noqa: E402
    validate_h3_context_ir_records,
)
from services.director.renderers.image_gen import ImageGenRenderer  # noqa: E402
from services.director.renderers.ltx_a2v import LtxA2VRenderer  # noqa: E402
from services.director.renderers.ltx_t2v import LtxT2VRenderer  # noqa: E402
from services.director.planners.podcast import PodcastPlanner  # noqa: E402
from services.director.planners.music_video import MusicVideoPlanner  # noqa: E402
from services.director.planners.short_film import ShortFilmPlanner  # noqa: E402
from services.director.planners.viral_video import (  # noqa: E402
    ViralVideoPlanner,
    _VIRAL_STYLES,
)
from services.director.schema import ShotPlan  # noqa: E402


def _shot(*, visual_style: str = "", image_strategy: str = "fresh_generation") -> ShotPlan:
    return ShotPlan.from_dict({
        "shot_id": "style-default",
        "index": 0,
        "duration_sec": 5.0,
        "skill_type": "short_film",
        "scene_goal": "show the room",
        "subjects_on_screen": [],
        "spatial_setup": "",
        "environment": "a quiet workshop",
        "visual_style": visual_style,
        "lighting": "window light",
        "mood": "calm",
        "action_beats": ["dust hangs in the air"],
        "camera_plan": {"framing": "wide shot"},
        "audio_plan": {"mode": "ambient_only"},
        "ending_beat": "the workbench fills the frame",
        "image_strategy": image_strategy,
    })


@contextmanager
def _live_planning_pipeline(pid: str):
    """Provide the process-local owner required by pipeline LLM calls."""
    selection = {
        "model_id": "synthetic-test-model",
        "device": "cpu",
        "provider": "local",
        "remote_url": "",
        "api_key": "",
        "local_gguf_path": "",
        "gguf_file_override": "",
    }

    @contextmanager
    def model_lease(**_selection):
        yield

    with (
        mock.patch.object(
            director_pipeline,
            "_pipelines",
            {pid: {"id": pid, "status": "planning"}},
        ),
        mock.patch.object(
            director_pipeline,
            "_pipeline_llm_contexts",
            {pid: {"selection": selection, "response_assist": None}},
        ),
        mock.patch.object(director_pipeline, "_pipeline_llm_tokens", {}),
        mock.patch.object(
            director_pipeline,
            "_pipeline_llm_cancel_handles",
            {},
        ),
        mock.patch.object(llm_service, "loaded_model_lease", model_lease),
    ):
        yield


class VisualStylePolicyTests(unittest.TestCase):
    def test_shared_guidance_is_conditional_and_keeps_authored_style_first(self):
        block = build_visual_style_default_block()
        self.assertIn("explicitly authored by the user", block)
        self.assertIn("supplied visual reference", block)
        self.assertIn(DEFAULT_VISUAL_STYLE, block)
        self.assertIn("Never replace an authored stylized medium", block)
        self.assertIn(block, build_camera_style_block())

    def test_structured_style_wins_exactly_and_reference_style_is_not_replaced(self):
        authored = "  hand-painted gouache  "
        self.assertEqual(resolve_visual_style(authored), authored)
        self.assertEqual(
            resolve_visual_style("", has_visual_reference=True),
            "",
        )
        self.assertEqual(resolve_visual_style(""), DEFAULT_VISUAL_STYLE)
        self.assertEqual(
            resolve_planned_visual_style(
                "",
                "watercolor",
                has_visual_reference=True,
                planned_style_source="authored_request",
            ),
            "watercolor",
        )
        self.assertEqual(
            resolve_planned_visual_style(
                "",
                "cinematic",
                has_visual_reference=True,
                planned_style_source="planner_default",
            ),
            "",
        )
        self.assertEqual(
            anchor_visual_prompt("scene", authored),
            f"scene VISUAL STYLE: {authored}.",
        )
        self.assertEqual(
            anchor_visual_prompt(
                "scene", "", has_visual_reference=True,
            ),
            "scene",
        )

    def test_h3_workflow_style_presence_suppresses_only_implicit_defaults(self):
        structured_block = build_visual_style_default_block(
            structured_style_present=True,
        )
        self.assertNotIn(DEFAULT_VISUAL_STYLE, structured_block)
        self.assertIn("structured model workflow", structured_block)

        # Workflow + blank generic style carries no competing planner default.
        self.assertEqual(
            resolve_planned_visual_style(
                "",
                DEFAULT_VISUAL_STYLE,
                planned_style_source="planner_default",
                structured_style_present=True,
            ),
            "",
        )
        # Workflow + explicit generic style preserves that independent authority.
        self.assertEqual(
            resolve_planned_visual_style(
                "hand-painted gouache",
                DEFAULT_VISUAL_STYLE,
                planned_style_source="planner_default",
                structured_style_present=True,
            ),
            "hand-painted gouache",
        )
        # An explicit style recovered from authored freeform remains authoritative.
        self.assertEqual(
            resolve_planned_visual_style(
                "",
                "ink wash",
                planned_style_source="authored_request",
                structured_style_present=True,
            ),
            "ink wash",
        )

    def test_fresh_renderers_apply_default_but_explicit_and_reference_paths_do_not(self):
        fresh = _shot()
        explicit = _shot(visual_style="stop-motion clay")
        referenced = _shot(image_strategy="reference_edit")

        fresh_image = ImageGenRenderer().render(fresh)
        explicit_image = ImageGenRenderer().render(explicit)
        reference_image = ImageGenRenderer().render(referenced)
        self.assertIn(DEFAULT_VISUAL_STYLE, fresh_image)
        self.assertIn("stop-motion clay", explicit_image)
        self.assertNotIn(DEFAULT_VISUAL_STYLE, explicit_image)
        self.assertNotIn(DEFAULT_VISUAL_STYLE, reference_image)

        self.assertIn(DEFAULT_VISUAL_STYLE, LtxT2VRenderer().render(fresh))
        self.assertNotIn(
            DEFAULT_VISUAL_STYLE,
            LtxT2VRenderer().render(fresh, has_reference=True),
        )
        self.assertIn(DEFAULT_VISUAL_STYLE, LtxA2VRenderer().render(fresh))

    def test_upcoming_skill_defaults_do_not_reintroduce_cinematic_style(self):
        viral_default = ViralVideoPlanner.plan.__defaults__
        podcast_default = PodcastPlanner.plan.__defaults__
        self.assertIsNotNone(viral_default)
        self.assertIsNotNone(podcast_default)
        self.assertEqual(viral_default[-1], "")
        self.assertIsNone(podcast_default[-3])
        self.assertIn("realistic", _VIRAL_STYLES)

    def test_planner_output_cannot_replace_authored_or_reference_style(self):
        viral = ViralVideoPlanner()
        viral._call_llm_json = lambda **_kwargs: [{
            "duration_sec": 5,
            "visual_style": "cinematic",
            "camera_plan": {"framing": "medium shot"},
            "audio_plan": {"mode": "generated_audio"},
        }]
        viral_plan = viral.plan("synthetic", style="stop-motion clay")
        self.assertEqual(viral_plan.shots[0].visual_style, "stop-motion clay")
        referenced_viral = viral.plan(
            "synthetic", reference_image_path="synthetic.png",
        )
        self.assertEqual(
            referenced_viral.shots[0].visual_style,
            "",
        )
        viral._call_llm_json = lambda **_kwargs: [{
            "duration_sec": 5,
            "visual_style": "hand-drawn anime",
            "camera_plan": {"framing": "medium shot"},
            "audio_plan": {"mode": "generated_audio"},
        }]
        freeform_viral = viral.plan(
            "a hand-drawn anime comedy",
        )
        self.assertEqual(
            freeform_viral.shots[0].visual_style,
            "hand-drawn anime",
        )
        viral._call_llm_json = lambda **_kwargs: [{
            "duration_sec": 5,
            "visual_style": "hand-drawn anime",
            "visual_style_source": "authored_request",
            "camera_plan": {"framing": "medium shot"},
            "audio_plan": {"mode": "generated_audio"},
        }]
        referenced_freeform_viral = viral.plan(
            "change the reference to hand-drawn anime",
            reference_image_path="synthetic.png",
        )
        self.assertEqual(
            referenced_freeform_viral.shots[0].visual_style,
            "hand-drawn anime",
        )
        structured_viral = viral.plan(
            "synthetic",
            visual_style="stop-motion clay",
        )
        self.assertEqual(
            structured_viral.shots[0].visual_style,
            "stop-motion clay",
        )

        podcast = PodcastPlanner()
        podcast._call_llm_json = lambda **_kwargs: [{
            "visual_style": "cinematic",
            "camera_plan": {"framing": "medium shot"},
            "audio_plan": {"mode": "dialogue_driven"},
        }]
        podcast_plan = podcast.plan(
            clips=[{"start": 0, "end": 5}],
            visual_style="hand-painted gouache",
        )
        self.assertEqual(
            podcast_plan.shots[0].visual_style,
            "hand-painted gouache",
        )
        referenced_podcast = podcast.plan(
            clips=[{"start": 0, "end": 5}],
            reference_image_path="synthetic.png",
        )
        self.assertEqual(
            referenced_podcast.shots[0].visual_style,
            "",
        )

    def test_music_and_short_film_planners_keep_structured_style_authoritative(self):
        music = MusicVideoPlanner()
        music._plan_with_llm = lambda **_kwargs: [{
            "visual_style": "cinematic",
            "camera_plan": {"framing": "medium shot"},
            "audio_plan": {"mode": "music_driven"},
        }]
        music_plan = music.plan(
            [{"start": 0.0, "end": 5.0, "label": "verse"}],
            "synthetic performance",
            visual_style="stop-motion clay",
        )
        self.assertEqual(
            music_plan.shots[0].visual_style,
            "stop-motion clay",
        )
        music._plan_with_llm = lambda **_kwargs: [{
            "visual_style": "watercolor",
            "camera_plan": {"framing": "medium shot"},
            "audio_plan": {"mode": "music_driven"},
        }]
        freeform_music_plan = music.plan(
            [{"start": 0.0, "end": 5.0, "label": "verse"}],
            "an authored watercolor performance",
        )
        self.assertEqual(
            freeform_music_plan.shots[0].visual_style,
            "watercolor",
        )
        music._plan_with_llm = lambda **_kwargs: [{}]
        missing_music_plan = music.plan(
            [{"start": 0.0, "end": 5.0, "label": "verse"}],
            "synthetic performance",
        )
        self.assertEqual(
            missing_music_plan.shots[0].visual_style,
            DEFAULT_VISUAL_STYLE,
        )
        music._plan_with_llm = lambda **_kwargs: [{
            "visual_style": "watercolor",
            "visual_style_source": "authored_request",
        }]
        referenced_freeform_music = music.plan(
            [{"start": 0.0, "end": 5.0, "label": "verse"}],
            "change the supplied reference to watercolor",
            reference_image_path="synthetic.png",
        )
        self.assertEqual(
            referenced_freeform_music.shots[0].visual_style,
            "watercolor",
        )
        music._plan_with_llm = lambda **_kwargs: [{
            "visual_style": "cinematic",
            "visual_style_source": "planner_default",
        }]
        referenced_planner_style = music.plan(
            [{"start": 0.0, "end": 5.0, "label": "verse"}],
            "synthetic performance",
            reference_image_path="synthetic.png",
        )
        self.assertEqual(
            referenced_planner_style.shots[0].visual_style,
            "",
        )

        short = ShortFilmPlanner()
        short._authored_visual_style = "hand-painted gouache"
        short._video_model = ""
        audio_shots = short._convert_audio_shots(
            [{"visual_style": "cinematic"}],
            [{"start": 0.0, "end": 5.0}],
            [],
            False,
        )
        story_shots = short._convert_story_shots(
            [{"duration_sec": 5.0, "visual_style": "cinematic"}],
            [],
            False,
            24,
            8,
            41,
        )
        self.assertEqual(
            audio_shots[0].visual_style,
            "hand-painted gouache",
        )
        self.assertEqual(
            story_shots[0].visual_style,
            "hand-painted gouache",
        )

        short._authored_visual_style = ""
        freeform_story = short._convert_story_shots(
            [{"duration_sec": 5.0, "visual_style": "watercolor"}],
            [],
            False,
            24,
            8,
            41,
        )
        missing_story = short._convert_story_shots(
            [{"duration_sec": 5.0, "visual_style": ""}],
            [],
            False,
            24,
            8,
            41,
        )
        self.assertEqual(freeform_story[0].visual_style, "watercolor")
        self.assertEqual(missing_story[0].visual_style, DEFAULT_VISUAL_STYLE)
        referenced_authored_story = short._convert_story_shots(
            [{
                "duration_sec": 5.0,
                "visual_style": "watercolor",
                "visual_style_source": "authored_request",
            }],
            [],
            True,
            24,
            8,
            41,
        )
        referenced_planner_story = short._convert_story_shots(
            [{
                "duration_sec": 5.0,
                "visual_style": "cinematic",
                "visual_style_source": "planner_default",
            }],
            [],
            True,
            24,
            8,
            41,
        )
        self.assertEqual(
            referenced_authored_story[0].visual_style,
            "watercolor",
        )
        self.assertEqual(referenced_planner_story[0].visual_style, "")

    def test_direct_planner_prompts_receive_structural_style_anchor(self):
        flags = DirectorFlags()
        flags.use_prompt_validation = False
        flags.use_prompt_compression = False
        director = DirectorOrchestrator(flags=flags)

        fresh = _shot()
        fresh.image_prompt = "a still workshop frame"
        fresh.video_prompt = (
            '[0.00s-5.00s] Ada says "Keep these words exactly."'
        )
        image_prompt = director.render_shot(fresh, mode="image_gen")
        video_prompt = director.render_shot(fresh, mode="t2v")
        self.assertTrue(image_prompt.endswith(
            f"VISUAL STYLE: {DEFAULT_VISUAL_STYLE}."
        ))
        self.assertTrue(video_prompt.endswith(
            f"VISUAL STYLE: {DEFAULT_VISUAL_STYLE}."
        ))
        self.assertIn("[0.00s-5.00s]", video_prompt)
        self.assertIn('Ada says "Keep these words exactly."', video_prompt)

        explicit = _shot(visual_style="stop-motion clay")
        explicit.image_prompt = "a still workshop frame"
        explicit_prompt = director.render_shot(explicit, mode="image_gen")
        self.assertTrue(explicit_prompt.endswith(
            "VISUAL STYLE: stop-motion clay."
        ))
        self.assertNotIn(DEFAULT_VISUAL_STYLE, explicit_prompt)

        referenced = _shot(image_strategy="reference_edit")
        referenced.image_prompt = "edit the attached frame"
        self.assertEqual(
            director.render_shot(
                referenced, mode="image_gen", has_reference=True,
            ),
            "edit the attached frame",
        )

        qwen_edit = _shot(
            visual_style="hand-painted watercolor",
            image_strategy="reference_edit",
        )
        qwen_edit.image_prompt = (
            "create new scene, a quiet gallery. Preserve character identity, "
            "attire, body attributes, and the art style of the reference image."
        )
        qwen_prompt = director.render_shot(
            qwen_edit,
            mode="image_gen",
            has_reference=True,
            image_model="qwen_image_edit_2509",
        )
        self.assertTrue(qwen_prompt.startswith("create new scene"))
        self.assertTrue(qwen_prompt.endswith(
            "Preserve character identity, attire, and body attributes from "
            "the reference image."
        ))
        self.assertIn(
            "Apply the authored visual style: hand-painted watercolor.",
            qwen_prompt,
        )
        self.assertNotIn(
            "art style of the reference image",
            qwen_prompt,
        )
        self.assertEqual(qwen_edit.visual_style, "hand-painted watercolor")

    def test_canonical_h3_direct_planner_prompt_is_never_wrapped(self):
        canonical = (
            "subject_definitions: No separately named subjects were authored; "
            "shot records carry only the request's explicitly described visible "
            "action and setting.\n\n"
            "integrated_multimodal_description:\n"
            "[Shot 1] [0.00s-5.00s] shot_name: Exact line | "
            "audiovisual_description: A realistically lit speaker faces camera. | "
            "dialogue_and_vocalizations: The speaker (S1) says: "
            "<d>[English] Keep every word exactly.</d>\n\n"
            "overall_soundscape: Quiet room tone.\n\n"
            "non_diegetic_music: N/A"
        )
        shot = _shot()
        shot.video_prompt = canonical
        flags = DirectorFlags()
        flags.use_prompt_validation = False
        flags.use_prompt_compression = False
        rendered = DirectorOrchestrator(flags=flags).render_shot(
            shot,
            mode="t2v",
            video_model="minimax_h3_fl2va",
        )

        self.assertEqual(rendered, canonical)
        self.assertTrue(rendered.startswith("subject_definitions:"))
        self.assertEqual(
            validate_h3_context_ir_records(
                rendered,
                mode="t2va",
                duration_seconds=5.0,
            ),
            [],
        )

    def test_legacy_pipeline_no_longer_supplies_cinematic_style_default(self):
        source = (APP / "services" / "director_pipeline.py").read_text(
            encoding="utf-8",
        )
        self.assertIn('"style": params.get("style") or ""', source)
        self.assertNotIn('params.get("style", "cinematic")', source)

    def test_durable_v2_and_legacy_planners_forward_structured_style(self):
        v2_calls = []

        class FakePlan:
            shots = []

            @staticmethod
            def to_dict():
                return {}

        class FakeDirector:
            def __init__(self, **_kwargs):
                pass

            def plan(self, skill_type, **kwargs):
                v2_calls.append((skill_type, kwargs))
                return FakePlan()

            @staticmethod
            def render_plan(*_args, **_kwargs):
                return []

            @staticmethod
            def plan_to_clip_plans(rendered):
                return rendered

        pipeline_types = (
            "music_video",
            "short_film_audio",
            "short_film_story",
            "podcast",
            "viral_video",
        )
        params = {
            "scene_description": "synthetic",
            "planned_clips": [{"start": 0.0, "end": 5.0}],
            "visual_style": "paper-cut collage",
        }
        with mock.patch(
            "services.director.orchestrator.DirectorOrchestrator",
            FakeDirector,
        ), mock.patch.object(director_pipeline, "_update_pipeline"):
            for pipeline_type in pipeline_types:
                director_pipeline._run_planning_v2(
                    "synthetic-pipeline",
                    dict(params),
                    pipeline_type,
                )

        self.assertEqual(len(v2_calls), len(pipeline_types))
        for _skill_type, kwargs in v2_calls:
            self.assertEqual(kwargs["visual_style"], "paper-cut collage")

        legacy_calls = []

        def fake_pipeline_call(_pid, _phase, name, _operation, **kwargs):
            legacy_calls.append((name, kwargs))
            if name == "legacy_short_film_story":
                return {"clips": [], "clip_plans": []}
            return []

        with mock.patch.object(
            director_pipeline,
            "_pipeline_llm_call",
            side_effect=fake_pipeline_call,
        ):
            for pipeline_type in (
                "music_video",
                "short_film_audio",
                "short_film_story",
            ):
                director_pipeline._run_planning_legacy(
                    "synthetic-pipeline",
                    dict(params),
                    pipeline_type,
                )

        self.assertEqual(len(legacy_calls), 3)
        for _name, kwargs in legacy_calls:
            self.assertEqual(kwargs["visual_style"], "paper-cut collage")

    def test_public_director_v2_route_threads_model_dialects(self):
        launch = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(launch)
        route = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "director_v2_plan"
        )
        planner_key_loop = next(
            node for node in ast.walk(route)
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "key"
            and isinstance(node.iter, ast.List)
        )
        planner_keys = {
            item.value for item in planner_key_loop.iter.elts
            if isinstance(item, ast.Constant)
            and isinstance(item.value, str)
        }
        self.assertIn("video_model", planner_keys)
        self.assertIn("image_model", planner_keys)

        render_call = next(
            node for node in ast.walk(route)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "render_plan"
        )
        render_keywords = {keyword.arg for keyword in render_call.keywords}
        self.assertIn("video_model", render_keywords)
        self.assertIn("image_model", render_keywords)


class StudioEnhanceStyleTests(unittest.TestCase):
    def test_image_and_video_enhancers_receive_shared_conditional_guidance(self):
        for mode in ("image", "video"):
            captured = {}

            def generate(**kwargs):
                captured.update(kwargs)
                return "unchanged synthetic result"

            with self.subTest(mode=mode), mock.patch.object(
                llm_service, "generate", side_effect=generate,
            ):
                llm_service.enhance_prompt(
                    "an authored watercolor scene",
                    mode=mode,
                )

            system = captured["system_prompt"]
            self.assertIn("VISUAL STYLE DEFAULT", system)
            self.assertIn("explicitly authored by the user", system)
            self.assertIn(DEFAULT_VISUAL_STYLE, system)
            self.assertIn("an authored watercolor scene", captured["prompt"])

    def test_audio_enhancement_does_not_receive_visual_style_guidance(self):
        captured = {}

        def generate(**kwargs):
            captured.update(kwargs)
            return "unchanged synthetic result"

        with mock.patch.object(llm_service, "generate", side_effect=generate):
            llm_service.enhance_prompt("a warm voice", mode="audio")

        self.assertNotIn("VISUAL STYLE DEFAULT", captured["system_prompt"])

    def test_h3_workflow_suppresses_enhancer_fallback_in_normal_and_raw_modes(self):
        for raw_mode, visual_style in (
            (False, ""),
            (True, "hand-painted gouache"),
        ):
            captured = {}

            def generate(**kwargs):
                captured.update(kwargs)
                return "synthetic H3 Context-IR"

            with self.subTest(raw_mode=raw_mode), mock.patch.object(
                llm_service, "generate", side_effect=generate,
            ), mock.patch.object(
                llm_service,
                "_finalize_h3_enhance_output",
                side_effect=lambda candidate, *_args, **_kwargs: candidate,
            ):
                llm_service.enhance_prompt(
                    "a stylized paper construction",
                    mode="video",
                    model_type="minimax_h3",
                    raw_enhancer_mode=raw_mode,
                    visual_style=visual_style,
                    h3_style_workflow_present=True,
                )

            system = captured["system_prompt"]
            self.assertIn("structured model workflow", system)
            self.assertNotIn(DEFAULT_VISUAL_STYLE, system)
            if visual_style:
                self.assertIn("AUTHORITATIVE VISUAL STYLE", system)
                self.assertIn(visual_style, system)
            else:
                self.assertNotIn("AUTHORITATIVE VISUAL STYLE", system)

    def test_director_refinement_gets_shared_policy_and_style_lock(self):
        captured = {}

        def generate(**kwargs):
            captured.update(kwargs)
            return "VISUAL STYLE: stop-motion clay. unchanged scene"

        with mock.patch.object(llm_service, "generate", side_effect=generate):
            result = llm_service.enhance_prompt(
                "VISUAL STYLE: stop-motion clay. unchanged scene",
                mode="video",
                system_override="Refine without inventing content.",
            )

        self.assertEqual(
            result,
            "VISUAL STYLE: stop-motion clay. unchanged scene",
        )
        self.assertIn(
            build_visual_style_refinement_block(),
            captured["system_prompt"],
        )
        self.assertIn("REFINEMENT STYLE LOCK", captured["system_prompt"])
        self.assertIn(
            "VISUAL STYLE: stop-motion clay. unchanged scene",
            captured["prompt"],
        )

    def test_active_director_v2_video_planner_gets_shared_style_default(self):
        captured = {}

        def generate(**kwargs):
            captured.update(kwargs)
            return "1. a sufficiently detailed synthetic motion prompt"

        clip = {"start": 0.0, "end": 5.0, "section_label": "verse"}
        with mock.patch.object(llm_service, "generate", side_effect=generate):
            result = llm_service.plan_clip_prompts_and_images(
                [clip],
                "an authored watercolor performance",
                prompt_type="video",
                visual_style="hand-painted watercolor",
            )

        self.assertEqual(
            result,
            [{"video_prompt": "a sufficiently detailed synthetic motion prompt"}],
        )
        self.assertIn("VISUAL STYLE DEFAULT", captured["system_prompt"])
        self.assertIn(DEFAULT_VISUAL_STYLE, captured["system_prompt"])
        self.assertIn(
            "Use exactly this visual medium/style: hand-painted watercolor",
            captured["system_prompt"],
        )
        self.assertIn(
            "explicitly authored by the user",
            captured["system_prompt"],
        )
        self.assertIn(
            "an authored watercolor performance",
            captured["prompt"],
        )

    def test_legacy_short_film_helpers_receive_structured_style(self):
        clip = {"start": 0.0, "end": 5.0, "section_label": "scene"}
        captured = []

        def generate(**kwargs):
            captured.append(kwargs)
            return "1. a sufficiently detailed synthetic motion prompt"

        with mock.patch.object(llm_service, "generate", side_effect=generate):
            llm_service.plan_short_film_prompts(
                [clip],
                "synthetic story",
                prompt_type="video",
                visual_style="paper-cut collage",
            )

        with mock.patch.object(
            llm_service,
            "generate_streaming",
            side_effect=generate,
        ):
            llm_service.plan_short_film_from_story(
                "synthetic story",
                target_duration=30,
                visual_style="paper-cut collage",
            )

        self.assertEqual(len(captured), 2)
        for call in captured:
            self.assertIn(
                "Use exactly this visual medium/style: paper-cut collage",
                call["system_prompt"],
            )

        launch_source = (APP / "launch.py").read_text(encoding="utf-8")
        for route_name in (
            "director_plan_prompts_and_images",
            "director_plan_short_film_prompts",
            "director_plan_short_film_script",
        ):
            tree = ast.parse(launch_source)
            route = next(
                node for node in tree.body
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == route_name
            )
            source = ast.get_source_segment(launch_source, route) or ""
            self.assertIn(
                'visual_style=body.get("visual_style")',
                source,
            )

    def test_legacy_h3_pipeline_helpers_accept_workflow_style_presence(self):
        from services.h3_upstream_skills import (
            builtin_catalog,
            resolve_h3_style_workflow,
        )

        catalog = builtin_catalog()
        workflow = resolve_h3_style_workflow(
            catalog["styles"][0]["id"], catalog,
        )
        captured_systems = []

        def generate(**kwargs):
            captured_systems.append(kwargs["system_prompt"])
            return "1V. a complete synthetic motion prompt\n1I. a complete starting frame"

        def generate_streaming(**kwargs):
            captured_systems.append(kwargs["system_prompt"])
            return (
                '[{"title":"Scene","duration":5,"dialogue":[],'
                '"scene_type":"action","video_prompt":"complete motion",'
                '"image_prompt":"complete starting frame"}]'
            )

        params = {
            "video_model": "minimax_h3",
            "scene_description": "synthetic scene",
            "planned_clips": [{
                "start": 0.0, "end": 5.0, "section_label": "scene",
            }],
            "h3_style_workflow": workflow,
            "visual_style": "",
            "target_duration": 5,
        }
        with _live_planning_pipeline("legacy-h3-style"), mock.patch.object(
            llm_service, "generate", side_effect=generate,
        ), mock.patch.object(
            llm_service, "generate_streaming", side_effect=generate_streaming,
        ):
            for pipeline_type in (
                "music_video", "short_film_audio", "short_film_story",
            ):
                director_pipeline._run_planning_legacy(
                    "legacy-h3-style",
                    dict(params),
                    pipeline_type,
                )

        self.assertEqual(len(captured_systems), 3)
        for system_prompt in captured_systems:
            self.assertIn("structured model workflow", system_prompt)
            self.assertNotIn(DEFAULT_VISUAL_STYLE, system_prompt)

    def test_podcast_h3_workflow_threads_style_presence_into_system_prompt(self):
        captured = []
        podcast = PodcastPlanner()

        def plan_json(**kwargs):
            captured.append(kwargs)
            return [{
                "camera_plan": {"framing": "medium shot"},
                "audio_plan": {"mode": "dialogue_driven"},
            }]

        podcast._call_llm_json = plan_json
        blank = podcast.plan(
            clips=[{"start": 0.0, "end": 5.0}],
            visual_style="",
            h3_style_workflow_present=True,
        )
        explicit = podcast.plan(
            clips=[{"start": 0.0, "end": 5.0}],
            visual_style="hand-painted gouache",
            h3_style_workflow_present=True,
        )

        self.assertEqual(blank.shots[0].visual_style, "")
        self.assertEqual(explicit.shots[0].visual_style, "hand-painted gouache")
        self.assertEqual(len(captured), 2)
        for call in captured:
            self.assertIn("structured model workflow", call["system_prompt"])
            self.assertNotIn(DEFAULT_VISUAL_STYLE, call["system_prompt"])
        self.assertIn("Visual Style: hand-painted gouache", captured[1]["user_prompt"])


if __name__ == "__main__":
    unittest.main()
