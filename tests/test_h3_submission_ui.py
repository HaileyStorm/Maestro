"""Behavioral contracts for H3 Studio segment-ceiling submission policy."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "ui/src/lib/h3Submission.ts"
STORE = ROOT / "ui/src/stores/useStore.ts"
DURATION = ROOT / "ui/src/components/Sidebar/DurationSlider.tsx"
DIRECTOR = ROOT / "ui/src/components/Sidebar/DirectorChat.tsx"
PLAN_DIALOG = ROOT / "ui/src/components/H3GenerationPlanDialog.tsx"
QUEUE_CARD = ROOT / "ui/src/components/MainContent/MainContent.tsx"
RECOVERY_ADAPTER = ROOT / "app/services/queue_recovery_adapter.py"


class H3SubmissionUiTests(unittest.TestCase):
    def test_preview_and_generate_payloads_omit_auto_ceiling_but_keep_lock(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is unavailable")
        helper_uri = HELPER.resolve().as_uri()
        script = f"""
import {{ applyH3SegmentCeilingPolicy }} from {json.dumps(helper_uri)};
const cases = [
  ['preview-base-auto', {{ model_type: 'minimax_h3', sliding_window_size: 124 }}, false],
  ['generate-base-auto', {{ model_type: 'minimax_h3', sliding_window_size: 243 }}, false],
  ['preview-ref2va-auto', {{ model_type: 'minimax_h3_ref2va', sliding_window_size: 345 }}, false],
  ['generate-ref2va-auto', {{ model_type: 'minimax_h3_ref2va', sliding_window_size: 124 }}, false],
  ['preview-base-locked', {{ model_type: 'minimax_h3', sliding_window_size: 192 }}, true],
  ['generate-ref2va-locked', {{ model_type: 'minimax_h3_ref2va', sliding_window_size: 192 }}, true],
];
process.stdout.write(JSON.stringify(cases.map(([name, payload, locked]) => [
  name, applyH3SegmentCeilingPolicy(payload, locked),
])));
"""
        completed = subprocess.run(
            [node, "--experimental-strip-types", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payloads = dict(json.loads(completed.stdout))
        for name in (
            "preview-base-auto", "generate-base-auto",
            "preview-ref2va-auto", "generate-ref2va-auto",
        ):
            self.assertNotIn("sliding_window_size", payloads[name])
        self.assertEqual(payloads["preview-base-locked"]["sliding_window_size"], 192)
        self.assertEqual(payloads["generate-ref2va-locked"]["sliding_window_size"], 192)

    def test_submit_applies_segment_policy_and_restore_recovers_lock_mode(self):
        source = STORE.read_text(encoding="utf-8")
        generation = source[
            source.index("const enhanceBeforeGenerate"):
            source.index("stopGeneration: (jobId)")
        ]
        self.assertEqual(generation.count("applyH3SegmentCeilingPolicy("), 1)
        self.assertNotIn("api.previewGenerationPlan(params)", generation)
        self.assertLess(
            generation.index("applyH3SegmentCeilingPolicy("),
            generation.index("api.submitGeneration(params)"),
        )
        self.assertLess(
            generation.index("jobs: [newJob, ...s.jobs]"),
            generation.index("api.submitGeneration(params)"),
        )
        restore = source[source.index("const automaticH3Longform"):source.index("// Derive resolution preset")]
        self.assertIn("hasManualH3SegmentCeiling(", restore)
        self.assertIn("timingState.slidingWindowLocked = restoredManualH3SegmentCeiling", restore)

    def test_ref2va_fresh_segment_default_is_native_max_not_duration_default(self):
        defaults = json.loads(
            (ROOT / "app/defaults/minimax_h3_ref2va.json").read_text(encoding="utf-8")
        )
        self.assertEqual(defaults["video_length"], 124)
        self.assertEqual(defaults["sliding_window_size"], 345)

    def test_studio_and_director_present_ceiling_and_estimate_semantics(self):
        duration = DURATION.read_text(encoding="utf-8")
        director = DIRECTOR.read_text(encoding="utf-8")
        self.assertIn("Maximum shot length", duration)
        self.assertIn("Maximum section length", duration)
        self.assertIn("Estimated shots", duration)
        self.assertIn("hard maximum", duration)
        self.assertIn("shorter or uneven", duration)
        self.assertIn("Maximum segment length", director)
        self.assertIn("Estimated segments", director)
        self.assertIn("longest allowed segment, not a target", director)
        self.assertIn("shorter segments of different lengths", director)
        self.assertIn("manual_segment_ceiling: state.slidingWindowLocked", STORE.read_text(encoding="utf-8"))
        self.assertIn("prompt: String(state.params.prompt || '')", STORE.read_text(encoding="utf-8"))
        self.assertIn("delete directorVideoParams.sliding_window_size", STORE.read_text(encoding="utf-8"))
        self.assertIn("const sequence = ++h3EstimateSequence.current", director)
        self.assertIn("window.setTimeout", director)
        self.assertIn("segment_scenes: directorEstimateScenes.length", director)
        estimate_endpoint = (ROOT / "app/launch.py").read_text(encoding="utf-8")
        estimate_endpoint = estimate_endpoint[
            estimate_endpoint.index("async def h3_estimate"):
            estimate_endpoint.index('@api.get("/api/v1/h3/benchmark")')
        ]
        self.assertIn("context = _h3_estimate_context(body)", estimate_endpoint)
        self.assertIn("_h3_profile_estimate_payload(\n            context,", estimate_endpoint)
        self.assertIn("invalidateH3Estimates()", duration)
        estimate_effect = duration[
            duration.index("const prompt"):
            duration.index("// Auto-track")
        ]
        for dependency in (
            "duration", "windowSize", "overlap", "h3ReferenceShapeKey",
            "h3AdaptiveConditioning",
        ):
            self.assertIn(dependency, estimate_effect)

    def test_postplan_surfaces_use_authoritative_count_time_and_geometry(self):
        dialog = PLAN_DIALOG.read_text(encoding="utf-8")
        card = QUEUE_CARD.read_text(encoding="utf-8")
        launch = (ROOT / "app/launch.py").read_text(encoding="utf-8")
        self.assertIn("{plan.clip_count} segment", dialog)
        self.assertIn("Planned time", dialog)
        self.assertIn("publishedFrames", dialog)
        self.assertIn("generatedFrames", dialog)
        self.assertIn("Planned segments {job.h3SegmentPlan.clip_count}", card)
        self.assertIn("Planned time", card)
        estimate_context = launch[
            launch.index("def _h3_estimate_context"):
            launch.index("def _h3_model_is_resident")
        ]
        self.assertIn('"generated_frames": max(1, int(frame_count))', estimate_context)
        self.assertIn('"published_frames": max(1, published_frame_count)', estimate_context)
        estimator = launch[
            launch.index("def _h3_estimate_for_context"):
            launch.index("def _h3_profile_estimate_payload")
        ]
        self.assertEqual(estimator.count("add_h3_postprocess_estimate("), 1)

    def test_h3_public_status_and_recovery_never_expose_authored_prompt_text(self):
        launch = (ROOT / "app/launch.py").read_text(encoding="utf-8")
        dialog = PLAN_DIALOG.read_text(encoding="utf-8")
        card = QUEUE_CARD.read_text(encoding="utf-8")
        public_plan = launch[
            launch.index("def _public_h3_long_plan"):
            launch.index("def _h3_effective_model_types")
        ]
        self.assertNotIn('"prompt_preview"', public_plan)
        prompt_projection = launch[
            launch.index("def _public_job_prompt_fields"):
            launch.index('@api.get("/api/v1/status/{job_id}")')
        ]
        self.assertIn('startswith("minimax_h3")', prompt_projection)
        self.assertIn('return {"prompt_preview": "", "active_window_prompt": ""}', prompt_projection)
        self.assertNotIn("segment.prompt_preview", dialog)
        self.assertIn("!job.modelType?.startsWith('minimax_h3')", card)

    def test_recovery_allowlist_preserves_strict_h3_public_geometry(self):
        recovery = RECOVERY_ADAPTER.read_text(encoding="utf-8")
        helper = recovery[
            recovery.index("def _safe_h3_segment_plan"):
            recovery.index("def serialize_job")
        ]
        for field in (
            '"fps"', '"generated_frames"', '"published_frames"',
            '"generated_duration_seconds"', '"published_duration_seconds"',
        ):
            self.assertIn(field, helper)
        self.assertIn("math.isfinite", helper)
        self.assertIn("type(child) is not int", helper)


if __name__ == "__main__":
    unittest.main(verbosity=2)
