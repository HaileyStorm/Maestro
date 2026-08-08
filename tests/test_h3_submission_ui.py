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

    def test_preview_and_submit_share_policy_and_restore_recovers_lock_mode(self):
        source = STORE.read_text(encoding="utf-8")
        generation = source[
            source.index("// Long H3 jobs are expensive"):
            source.index("stopGeneration: (jobId)")
        ]
        self.assertEqual(generation.count("applyH3SegmentCeilingPolicy("), 2)
        self.assertLess(
            generation.index("applyH3SegmentCeilingPolicy("),
            generation.index("api.previewGenerationPlan(params)"),
        )
        self.assertLess(
            generation.rindex("applyH3SegmentCeilingPolicy("),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
