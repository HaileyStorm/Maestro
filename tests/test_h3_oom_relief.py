"""Quality-preserving H3 denoise OOM relief contracts."""
from __future__ import annotations

import os
import sys
import unittest

APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP not in sys.path:
    sys.path.insert(0, APP)

from services.h3_oom_relief import (
    H3_BASELINE_OFFLOAD_PROFILE,
    H3_OOM_RELIEF_MAX_ATTEMPTS,
    apply_h3_baseline_offload_profile,
    decide_h3_oom_relief,
    next_h3_denoise_relief,
    next_offload_profile,
    step_nibble_ladder,
)


class H3OomReliefTests(unittest.TestCase):
    def test_standing_baseline_is_profile_4_plus(self):
        self.assertEqual(H3_BASELINE_OFFLOAD_PROFILE, 4.5)
        self.assertEqual(apply_h3_baseline_offload_profile(4, "minimax_h3_ref2va"), 4.5)
        self.assertEqual(apply_h3_baseline_offload_profile(3, "minimax_h3_ref2va"), 4.5)
        self.assertEqual(apply_h3_baseline_offload_profile(1, "minimax_h3"), 4.5)
        self.assertEqual(apply_h3_baseline_offload_profile(4.5, "minimax_h3_ref2va"), 4.5)
        self.assertEqual(apply_h3_baseline_offload_profile(5, "minimax_h3_ref2va"), 5)
        self.assertEqual(apply_h3_baseline_offload_profile(3, "wan_2_2"), 3)
        self.assertEqual(next_offload_profile(4.5), 5.0)
        self.assertIsNone(next_offload_profile(5))

    def test_step_ladder_is_two_step_nibbles_to_floor(self):
        self.assertEqual(
            step_nibble_ladder(),
            (50, 48, 46, 44, 42, 40, 38, 36, 34, 32, 30, 28, 26, 24, 23),
        )

    def test_first_attempt_step_zero_retries_same_setup(self):
        nxt = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=50,
            intent_steps=50,
            attempt=0,
            step_now=0,
            same_setup_retries=0,
            offload_profile=4.5,
        )
        self.assertEqual(nxt["resolution"], "768x1344")
        self.assertEqual(nxt["num_inference_steps"], 50)
        self.assertEqual(nxt["reason"], "same_setup_after_unwind")
        self.assertFalse(nxt["record_denial"])

    def test_user_already_at_23_retries_before_canvas_drop(self):
        first = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=23,
            intent_steps=23,
            attempt=0,
            step_now=0,
            same_setup_retries=0,
            offload_profile=4.5,
        )
        self.assertEqual(first["resolution"], "768x1344")
        self.assertEqual(first["num_inference_steps"], 23)
        self.assertEqual(first["reason"], "same_setup_after_unwind")

        second = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=23,
            intent_steps=23,
            attempt=1,
            step_now=0,
            same_setup_retries=1,
            offload_profile=4.5,
        )
        self.assertEqual(second["resolution"], "768x1344")
        self.assertEqual(second["num_inference_steps"], 23)
        self.assertEqual(second["reason"], "same_setup_after_unwind")

        offload = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=23,
            intent_steps=23,
            attempt=2,
            step_now=0,
            same_setup_retries=2,
            offload_profile=4.5,
        )
        self.assertEqual(offload["reason"], "escalate_offload")
        self.assertEqual(offload["override_profile"], 5.0)
        self.assertEqual(offload["resolution"], "768x1344")
        self.assertEqual(offload["num_inference_steps"], 23)

        still_step0 = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=23,
            intent_steps=23,
            attempt=3,
            step_now=0,
            same_setup_retries=2,
            offload_profile=5,
        )
        self.assertIsNone(still_step0)

        mid_denoise = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=23,
            intent_steps=23,
            attempt=3,
            step_now=12,
            same_setup_retries=2,
            offload_profile=5,
        )
        self.assertEqual(mid_denoise["resolution"], "640x1152")
        self.assertEqual(mid_denoise["num_inference_steps"], 23)
        self.assertEqual(mid_denoise["reason"], "next_native_canvas")

    def test_mid_denoise_near_miss_nibbles_after_same_setup_and_offload(self):
        same = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=50,
            intent_steps=50,
            attempt=0,
            step_now=49,
            same_setup_retries=0,
            offload_profile=4.5,
        )
        self.assertEqual(same["reason"], "same_setup_after_unwind")
        self.assertEqual(same["num_inference_steps"], 50)

        offload = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=50,
            intent_steps=50,
            attempt=1,
            step_now=49,
            same_setup_retries=1,
            offload_profile=4.5,
        )
        self.assertEqual(offload["reason"], "escalate_offload")
        self.assertEqual(offload["override_profile"], 5.0)
        self.assertEqual(offload["num_inference_steps"], 50)

        nibble = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=50,
            intent_steps=50,
            attempt=2,
            step_now=49,
            same_setup_retries=1,
            offload_profile=5,
        )
        self.assertEqual(nibble["resolution"], "768x1344")
        self.assertEqual(nibble["num_inference_steps"], 48)
        self.assertEqual(nibble["reason"], "keep_canvas_fewer_steps")

        next_nibble = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=48,
            intent_steps=50,
            attempt=3,
            step_now=47,
            same_setup_retries=1,
            offload_profile=5,
        )
        self.assertEqual(next_nibble["resolution"], "768x1344")
        self.assertEqual(next_nibble["num_inference_steps"], 46)

    def test_step_zero_after_nibble_does_not_drop_canvas(self):
        nxt = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=48,
            intent_steps=50,
            attempt=3,
            step_now=0,
            same_setup_retries=0,
            offload_profile=5,
        )
        self.assertEqual(nxt["resolution"], "768x1344")
        self.assertEqual(nxt["num_inference_steps"], 48)
        self.assertEqual(nxt["reason"], "same_setup_after_unwind")
        self.assertFalse(nxt["record_denial"])

        after_same = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=48,
            intent_steps=50,
            attempt=4,
            step_now=0,
            same_setup_retries=1,
            offload_profile=5,
        )
        self.assertEqual(after_same["resolution"], "768x1344")
        self.assertEqual(after_same["num_inference_steps"], 46)

    def test_portrait_nibbles_fine_steps_before_dropping_native_size(self):
        current = {"resolution": "768x1344", "num_inference_steps": 50}
        seen = []
        for attempt in range(H3_OOM_RELIEF_MAX_ATTEMPTS + 2):
            nxt = next_h3_denoise_relief(
                resolution=current["resolution"],
                num_inference_steps=current["num_inference_steps"],
                intent_steps=50,
                attempt=attempt,
            )
            if nxt is None:
                break
            seen.append((nxt["resolution"], nxt["num_inference_steps"], nxt["reason"]))
            current = nxt
        self.assertIn(("768x1344", 48, "keep_canvas_fewer_steps"), seen)
        self.assertIn(("768x1344", 46, "keep_canvas_fewer_steps"), seen)
        self.assertIn(("768x1344", 44, "keep_canvas_fewer_steps"), seen)
        self.assertIn(("768x1344", 42, "keep_canvas_fewer_steps"), seen)
        self.assertIn(("768x1344", 40, "keep_canvas_fewer_steps"), seen)
        self.assertIn(("768x1344", 23, "keep_canvas_fewer_steps"), seen)
        first_canvas = next(
            item for item in seen if item[2] == "next_native_canvas"
        )
        self.assertEqual(first_canvas[0], "640x1152")
        self.assertEqual(first_canvas[1], 50)

    def test_near_miss_does_not_force_smaller_canvas(self):
        nxt = next_h3_denoise_relief(
            resolution="768x1344",
            num_inference_steps=50,
            intent_steps=50,
            attempt=0,
            force_smaller_canvas=False,
        )
        self.assertEqual(nxt["resolution"], "768x1344")
        self.assertEqual(nxt["num_inference_steps"], 48)

    def test_force_smaller_canvas_is_not_used_for_step_zero_policy(self):
        nxt = decide_h3_oom_relief(
            resolution="768x1344",
            num_inference_steps=48,
            intent_steps=50,
            attempt=1,
            step_now=0,
            same_setup_retries=1,
            offload_profile=5,
        )
        self.assertEqual(nxt["resolution"], "768x1344")
        self.assertNotEqual(nxt["reason"], "next_native_canvas")

    def test_never_introduces_turbo_or_delivery_crops(self):
        current = {"resolution": "768x1344", "num_inference_steps": 50}
        seen = []
        for attempt in range(H3_OOM_RELIEF_MAX_ATTEMPTS + 2):
            nxt = next_h3_denoise_relief(
                resolution=current["resolution"],
                num_inference_steps=current["num_inference_steps"],
                intent_steps=50,
                attempt=attempt,
            )
            if nxt is None:
                break
            seen.append((nxt["resolution"], nxt["num_inference_steps"]))
            current = nxt
        self.assertTrue(seen)
        self.assertTrue(all(steps >= 23 for _res, steps in seen))
        self.assertTrue(all("x" in res and "1920" not in res for res, _steps in seen))
        self.assertTrue(all(res != "768x1024" for res, _steps in seen))

    def test_exhausted_relief_returns_none(self):
        self.assertIsNone(
            next_h3_denoise_relief(
                resolution="352x608",
                num_inference_steps=23,
                intent_steps=50,
                attempt=0,
            )
        )
        self.assertIsNone(
            next_h3_denoise_relief(
                resolution="768x1344",
                num_inference_steps=50,
                attempt=H3_OOM_RELIEF_MAX_ATTEMPTS,
            )
        )


if __name__ == "__main__":
    unittest.main()
