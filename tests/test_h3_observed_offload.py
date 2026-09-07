"""Recovery must distinguish loaded MMGP state from requested policy."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from services.h3_oom_relief import H3OomReliefRetry, decide_h3_oom_relief


class ObservedOffloadTests(unittest.TestCase):
    def test_loaded_profile_is_not_raised_by_standing_policy(self):
        for canvas in ("1344x768", "768x1344", "960x544"):
            with self.subTest(canvas=canvas):
                args = dict(resolution=canvas, num_inference_steps=23,
                            offload_profile=4.5, step_now=0)
                same = decide_h3_oom_relief(**args)
                self.assertEqual(same["override_profile"], 4.5)
                stronger = decide_h3_oom_relief(
                    **args, attempt=2, same_setup_retries=2,
                )
                self.assertEqual(stronger["reason"], "escalate_offload")
                self.assertEqual(stronger["override_profile"], 5.0)
                self.assertEqual(stronger["resolution"], canvas)
                self.assertEqual(stronger["num_inference_steps"], 23)
                self.assertFalse(stronger["record_denial"])

    def test_omitted_profile_does_not_fabricate_loaded_state(self):
        self.assertIsNone(decide_h3_oom_relief(
            resolution="1344x768", num_inference_steps=50, step_now=12,
        ))

    def test_unknown_profile_does_not_invent_a_retry(self):
        for profile in (None, -1, "unknown", float("nan"), float("inf")):
            with self.subTest(profile=profile):
                self.assertIsNone(decide_h3_oom_relief(
                    resolution="1344x768", num_inference_steps=50,
                    offload_profile=profile, step_now=12,
                ))


class WgpObservedOffloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((APP / "wgp.py").read_text())
        blocks = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Name) and node.test.id == "h3_oom"]
        assert len(blocks) == 1
        cls.code = compile(ast.Module(body=blocks, type_ignores=[]),
                           str(APP / "wgp.py"), "exec")

    def run_handler(self, loaded, *, attempt=2, same=2, step=12):
        params = {"_h3_oom_relief_attempt": attempt,
                  "_h3_same_setup_retries": same,
                  "_h3_relief_offload_profile": 5}
        namespace = dict(h3_oom=True, loaded_profile=loaded,
                         task={"params": params}, num_inference_steps=23,
                         resolution="768x1344", model_type="minimax_h3_ref2va",
                         override_profile=5, failure_details={"step": {"current": step}},
                         custom_settings={}, video_length=129, duration_seconds=5)
        limits = types.ModuleType("services.h3_host_limits")
        limits.record_denoise_failure = Mock()
        relief = None
        with patch.dict(sys.modules, {"services.h3_host_limits": limits}), patch("builtins.print"):
            try:
                exec(self.code, namespace)
            except H3OomReliefRetry as error:
                relief = error.relief
        return relief, params, limits.record_denoise_failure

    def test_loaded_state_wins_over_requested_profile(self):
        relief, params, record = self.run_handler(4.5)
        self.assertEqual(relief["reason"], "escalate_offload")
        self.assertEqual(relief["override_profile"], 5)
        self.assertEqual(params["_h3_same_setup_retries"], 0)
        record.assert_not_called()

    def test_same_setup_preserves_loaded_profile_and_retry_count(self):
        relief, params, record = self.run_handler(4.5, attempt=0, same=0)
        self.assertEqual(relief["override_profile"], 4.5)
        self.assertEqual(params["_h3_same_setup_retries"], 1)
        record.assert_not_called()

    def test_unknown_or_unexhausted_profile_cannot_calibrate_host_limit(self):
        for profile in (None, -1, "unknown", 4.5):
            with self.subTest(profile=profile):
                relief, _, record = self.run_handler(profile, attempt=36)
                self.assertIsNone(relief)
                record.assert_not_called()

    def test_known_max_profile_retains_mid_denoise_calibration(self):
        relief, _, record = self.run_handler(5, attempt=36)
        self.assertIsNone(relief)
        record.assert_called_once()
        self.assertTrue(record.call_args.kwargs["exhausted"])

    def test_step_zero_does_not_calibrate_even_at_max_profile(self):
        _, _, record = self.run_handler(5, attempt=36, step=0)
        record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
