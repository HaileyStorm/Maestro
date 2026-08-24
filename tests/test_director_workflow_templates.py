from __future__ import annotations

import copy
import os
import sys
import unittest
from unittest.mock import patch


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.director.orchestrator import DirectorFlags, DirectorOrchestrator  # noqa: E402
from services.director.schema import (  # noqa: E402
    AudioPlan,
    CameraPlan,
    DialogueBeat,
    ProductionPlan,
    ShotPlan,
    SubjectRef,
)
from services.director.workflow_templates import (  # noqa: E402
    build_h3_shot_table_template,
)


def _shot(
    shot_id: str,
    index: int,
    duration: float,
    *,
    skill_type: str = "short_film",
    metadata: dict | None = None,
) -> ShotPlan:
    return ShotPlan(
        shot_id=shot_id,
        index=index,
        duration_sec=duration,
        skill_type=skill_type,
        scene_goal=f"Scene {index + 1}",
        subjects_on_screen=[
            SubjectRef(
                character_id="ada",
                visual_description="Ada in a blue coat",
                position_or_relation="screen-left",
            )
        ],
        spatial_setup="Ada faces the workbench",
        environment="a practical workshop",
        visual_style="grounded live action",
        lighting="soft window light",
        mood="focused",
        action_beats=["Ada opens the toolbox"],
        camera_plan=CameraPlan(
            framing="medium shot",
            movement="slow push-in",
        ),
        audio_plan=AudioPlan(
            mode="dialogue_driven",
            ambience="quiet room tone",
            effects=["toolbox click"],
        ),
        dialogue_beats=[
            DialogueBeat(speaker_id="ada", spoken_text="Here it is.")
        ],
        ending_beat="Ada holds the open toolbox",
        metadata=metadata,
    )


class TestH3ShotTableTemplate(unittest.TestCase):
    def test_projects_canonical_rows_without_mutating_plan(self):
        first = _shot(
            "shot-a",
            0,
            2.5,
            metadata={
                "opening_blocking": "Ada stands beside the bench",
                "closing_blocking": "Ada leans over the open toolbox",
                "timed_cues": [{"time_sec": 1.0, "cue": "toolbox opens"}],
                "reference_anchor_ids": ["ada-front"],
                "asset_lineage": {"ada-front": "asset-7"},
            },
        )
        second = _shot("shot-b", 1, 3.0)
        second.continuity_strategy = "continuous"
        first.video_prompt = "Authored video prompt stays on the shot."
        first.image_prompt = "Authored image prompt stays on the shot."
        plan = ProductionPlan(skill_type="short_film", shots=[first, second])
        before = copy.deepcopy(plan.to_dict())

        template = build_h3_shot_table_template(plan)

        self.assertEqual(plan.to_dict(), before)
        self.assertEqual(build_h3_shot_table_template(plan), template)
        self.assertEqual(len(template["shots"]), 2)
        self.assertEqual(
            [
                (row["start_sec"], row["end_sec"], row["duration_sec"])
                for row in template["shots"]
            ],
            [(0.0, 2.5, 2.5), (2.5, 5.5, 3.0)],
        )

        row = template["shots"][0]
        self.assertEqual(row["scene"], first.scene_goal)
        self.assertEqual(row["subjects"], [first.subjects_on_screen[0].to_dict()])
        self.assertEqual(row["spatial"], first.spatial_setup)
        self.assertEqual(row["environment"], first.environment)
        self.assertEqual(row["lighting"], first.lighting)
        self.assertEqual(row["action"], first.action_beats)
        self.assertEqual(row["camera"], first.camera_plan.to_dict())
        self.assertEqual(row["audio"]["mode"], "dialogue_driven")
        self.assertEqual(
            row["audio"]["dialogue_beats"][0]["spoken_text"],
            "Here it is.",
        )
        self.assertEqual(row["handoff_in"], "Ada stands beside the bench")
        self.assertEqual(
            row["handoff_out"], "Ada leans over the open toolbox"
        )
        self.assertEqual(row["reference_anchor_ids"], ["ada-front"])
        self.assertEqual(row["asset_lineage"], {"ada-front": "asset-7"})
        self.assertNotIn("video_prompt", row)
        self.assertNotIn("image_prompt", row)
        self.assertNotIn("shot_records", row)

        sparse = template["shots"][1]
        self.assertEqual(
            sparse["handoff_in"], "Ada leans over the open toolbox"
        )
        self.assertEqual(sparse["handoff_out"], second.ending_beat)
        self.assertEqual(sparse["timed_cues"], [])
        self.assertNotIn("reference_anchor_ids", sparse)
        self.assertNotIn("asset_lineage", sparse)
        self.assertNotIn("shot_records", template)
        self.assertEqual(template["surface"], "api_persisted_plan")
        self.assertEqual(template["authority"], "advisory")
        self.assertTrue(template["qc_checklist"])
        self.assertEqual(
            {item["status"] for item in template["qc_checklist"]},
            {"pending"},
        )
        self.assertEqual(
            template["fallback_policy"]["latest_approved_asset_fallback"],
            "explicit_only",
        )
        self.assertIs(
            template["fallback_policy"]["reuse_exact_reference_anchors_first"],
            True,
        )

        row["audio"]["effects"].append("wind")
        self.assertEqual(first.audio_plan.effects, ["toolbox click"])

        independent = _shot("cutaway", 1, 1.0)
        cut_rows = build_h3_shot_table_template(
            ProductionPlan(skill_type="short_film", shots=[first, independent])
        )["shots"]
        self.assertIsNone(cut_rows[1]["handoff_in"])

        grouped_first = _shot(
            "group-a",
            0,
            1.0,
            metadata={
                "continuity_group": "a",
                "closing_blocking": "Ada remains at the bench",
            },
        )
        grouped_second = _shot(
            "group-b",
            1,
            1.0,
            metadata={"continuity_group": "b"},
        )
        grouped_second.continuity_strategy = "continuous"
        grouped_rows = build_h3_shot_table_template(
            ProductionPlan(
                skill_type="short_film",
                shots=[grouped_first, grouped_second],
            )
        )["shots"]
        self.assertIsNone(grouped_rows[1]["handoff_in"])

    def test_music_rows_use_exact_authored_clip_ranges(self):
        plan = ProductionPlan(
            skill_type="music_video",
            shots=[
                _shot(
                    "verse",
                    0,
                    99.0,
                    skill_type="music_video",
                    metadata={
                        "clip_start": 12.25,
                        "clip_end": 15.75,
                        "bpm": 97.5,
                    },
                ),
                _shot(
                    "chorus",
                    1,
                    99.0,
                    skill_type="music_video",
                    metadata={"clip_start": 20, "clip_end": 24.5},
                ),
            ],
        )
        rows = build_h3_shot_table_template(plan)["shots"]

        self.assertEqual(
            [
                (row["start_sec"], row["end_sec"], row["duration_sec"])
                for row in rows
            ],
            [(12.25, 15.75, 3.5), (20.0, 24.5, 4.5)],
        )
        self.assertEqual(
            rows[0]["music_metadata"],
            {"clip_start": 12.25, "clip_end": 15.75, "bpm": 97.5},
        )

        partial = ProductionPlan(
            skill_type="music_video",
            shots=[
                _shot(
                    "partial",
                    0,
                    4.0,
                    skill_type="music_video",
                    metadata={"clip_start": 2.0},
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "clip_start and clip_end"):
            build_h3_shot_table_template(partial)

    def test_short_film_audio_preserves_authored_absolute_gaps(self):
        plan = ProductionPlan(
            skill_type="short_film",
            shots=[
                _shot(
                    "dialogue-a",
                    0,
                    20.0,
                    metadata={
                        "clip_start": 3.0,
                        "clip_end": 5.25,
                        "closing_blocking": "Ada crosses to the window",
                    },
                ),
                _shot(
                    "dialogue-b",
                    1,
                    20.0,
                    metadata={"clip_start": 8.0, "clip_end": 10.0},
                ),
            ],
        )
        plan.shots[1].continuity_strategy = "continuous"

        rows = build_h3_shot_table_template(plan)["shots"]

        self.assertEqual(
            [(row["start_sec"], row["end_sec"]) for row in rows],
            [(3.0, 5.25), (8.0, 10.0)],
        )
        self.assertEqual(rows[1]["handoff_in"], "Ada crosses to the window")

        partial = ProductionPlan(
            skill_type="short_film",
            shots=[
                _shot(
                    "partial-dialogue",
                    0,
                    4.0,
                    metadata={"clip_end": 4.0},
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "clip_start and clip_end"):
            build_h3_shot_table_template(partial)

    def test_production_plan_omits_none_and_round_trips_present_template(self):
        plan = ProductionPlan(skill_type="short_film", shots=[])
        self.assertNotIn("workflow_template", plan.to_dict())
        self.assertIsNone(ProductionPlan.from_dict(plan.to_dict()).workflow_template)

        canonical = build_h3_shot_table_template(plan)
        plan.workflow_template = canonical
        persisted = plan.to_dict()
        restored = ProductionPlan.from_dict(persisted)

        self.assertEqual(restored.workflow_template, canonical)
        self.assertEqual(restored.to_dict()["workflow_template"], canonical)

        persisted["workflow_template"]["shots"] = [{"scene": "stale"}]
        replayed = ProductionPlan.from_dict(persisted)
        self.assertEqual(replayed.workflow_template, canonical)

        future = {
            "type": "minimax_h3_shot_table",
            "version": 2,
            "surface": "api_persisted_plan",
            "authority": "advisory",
            "future_extension": {"keep": True},
        }
        plan.workflow_template = future
        self.assertEqual(plan.to_dict()["workflow_template"], future)
        self.assertEqual(
            ProductionPlan.from_dict(plan.to_dict()).workflow_template,
            future,
        )


class _RecordingPlanner:
    last_kwargs: dict | None = None

    def __init__(self, **_kwargs):
        pass

    def plan(self, **kwargs) -> ProductionPlan:
        type(self).last_kwargs = dict(kwargs)
        return ProductionPlan(
            skill_type="short_film",
            shots=[_shot("planned", 0, 1.0)],
            total_duration_sec=1.0,
        )


class _NormalizingPlanner:
    def __init__(self, **_kwargs):
        pass

    def plan(self, **_kwargs) -> ProductionPlan:
        shot = _shot("normalize", 0, 1.0)
        shot.camera_plan.framing = ""
        shot.camera_plan.movement_intensity = "extreme"
        shot.audio_plan.mode = "unknown"
        return ProductionPlan(
            skill_type="short_film",
            shots=[shot],
            total_duration_sec=1.0,
        )


class TestH3ShotTableOrchestration(unittest.TestCase):
    def setUp(self):
        flags = DirectorFlags()
        flags.use_prompt_validation = False
        self.director = DirectorOrchestrator(flags=flags)

    def test_opt_in_is_removed_before_planner_and_attached_only_for_h3(self):
        from services.director import orchestrator as orchestrator_module

        with patch.dict(
            orchestrator_module._PLANNER_MAP,
            {"short_film": _RecordingPlanner},
        ):
            h3_plan = self.director.plan(
                "short_film",
                video_model="minimax_h3_ref2va",
                include_h3_shot_table_template=True,
                scene_description="A workshop",
            )
            self.assertNotIn(
                "include_h3_shot_table_template", _RecordingPlanner.last_kwargs
            )
            self.assertEqual(
                h3_plan.workflow_template["type"], "minimax_h3_shot_table"
            )

            non_h3_plan = self.director.plan(
                "short_film",
                video_model="ltx2_25_dev",
                include_h3_shot_table_template=True,
            )
            self.assertIsNone(non_h3_plan.workflow_template)

            default_plan = self.director.plan(
                "short_film",
                video_model="minimax_h3_ref2va",
            )
            self.assertIsNone(default_plan.workflow_template)

            string_flag_plan = self.director.plan(
                "short_film",
                video_model="minimax_h3_ref2va",
                include_h3_shot_table_template="true",
            )
            self.assertIsNone(string_flag_plan.workflow_template)
            self.assertNotIn(
                "include_h3_shot_table_template", _RecordingPlanner.last_kwargs
            )

            selected_workflow_plan = self.director.plan(
                "short_film",
                video_model="minimax_h3_ref2va",
                h3_style_workflow_present=True,
            )
            self.assertIsNotNone(selected_workflow_plan.workflow_template)
            self.assertIs(
                _RecordingPlanner.last_kwargs["h3_style_workflow_present"], True
            )

    def test_template_projects_validator_normalized_final_shot(self):
        from services.director import orchestrator as orchestrator_module

        flags = DirectorFlags()
        flags.log_validation_details = False
        director = DirectorOrchestrator(flags=flags)
        with patch.dict(
            orchestrator_module._PLANNER_MAP,
            {"short_film": _NormalizingPlanner},
        ):
            plan = director.plan(
                "short_film",
                video_model="minimax_h3_ref2va",
                include_h3_shot_table_template=True,
            )

        shot = plan.shots[0]
        row = plan.workflow_template["shots"][0]
        self.assertEqual(shot.camera_plan.framing, "medium shot")
        self.assertEqual(shot.camera_plan.movement_intensity, "subtle")
        self.assertEqual(shot.audio_plan.mode, "ambient_only")
        self.assertEqual(row["camera"], shot.camera_plan.to_dict())
        self.assertEqual(row["audio"]["mode"], shot.audio_plan.mode)


if __name__ == "__main__":
    unittest.main()
