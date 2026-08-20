"""Host-local MiniMax H3 setup limits."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP not in sys.path:
    sys.path.insert(0, APP)

from services.h3_host_limits import (  # noqa: E402
    HOST_LIMITS_CODE_EPOCH,
    evaluate_setup,
    host_limit_reason_for_profile,
    orient_resolution,
    record_denoise_failure,
    record_denoise_success,
)
from services.h3_profiles import build_profile_options  # noqa: E402


FIXED_EPOCH = {
    "code": HOST_LIMITS_CODE_EPOCH,
    "relief_version": 3,
    "hardware": {"gpu": "test", "vram_mb": 32000, "torch": "2.0", "cuda": "12.8"},
    "attention": {"sdpa": True, "sol_attn": False, "sage2": False},
}


class H3HostLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "h3_host_limits.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_orient_resolution_swaps_high_to_portrait(self):
        self.assertEqual(orient_resolution("1344x768", "768x1344"), "768x1344")
        self.assertEqual(orient_resolution("1344x768", "1344x768"), "1344x768")

    def test_denies_failed_steps_and_higher_not_lower(self):
        record_denoise_failure(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=50,
            video_length=241,
            attention_engine="sdpa",
            after_unwind=True,
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        denied_50 = evaluate_setup(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=50,
            video_length=241,
            attention_engine="sdpa",
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        allowed_40 = evaluate_setup(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=40,
            duration_seconds=10,
            attention_engine="sdpa",
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        self.assertFalse(denied_50.runnable)
        self.assertEqual(denied_50.max_steps, 49)
        self.assertTrue(allowed_40.runnable)
        self.assertIn("could not finish", denied_50.reason)

    def test_lower_denied_step_blocks_higher(self):
        record_denoise_failure(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=40,
            duration_seconds=10,
            attention_engine="sdpa",
            after_unwind=True,
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        high = evaluate_setup(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=50,
            duration_seconds=10,
            attention_engine="sdpa",
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        self.assertFalse(high.runnable)
        self.assertEqual(high.max_steps, 39)

    def test_forty_eight_denial_does_not_hide_floor_or_force_640(self):
        record_denoise_success(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=50,
            duration_seconds=10,
            attention_engine="sol_attn",
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        record_denoise_failure(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=48,
            duration_seconds=10,
            attention_engine="sol_attn",
            after_unwind=True,
            step_now=47,
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        offered_50 = evaluate_setup(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=50,
            duration_seconds=10,
            attention_engine="sol_attn",
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        offered_23 = evaluate_setup(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=23,
            duration_seconds=10,
            attention_engine="sol_attn",
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        denied_48 = evaluate_setup(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=48,
            duration_seconds=10,
            attention_engine="sol_attn",
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        self.assertTrue(offered_50.runnable)
        self.assertTrue(offered_23.runnable)
        self.assertFalse(denied_48.runnable)

    def test_ignores_step_zero_even_after_unwind_flag(self):
        record_denoise_failure(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=48,
            duration_seconds=10,
            after_unwind=True,
            step_now=0,
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        decision = evaluate_setup(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=48,
            duration_seconds=10,
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        self.assertTrue(decision.runnable)

    def test_ignores_pre_unwind_step_zero(self):
        record_denoise_failure(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=50,
            duration_seconds=10,
            after_unwind=False,
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        decision = evaluate_setup(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=50,
            duration_seconds=10,
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        self.assertTrue(decision.runnable)

    def test_success_clears_that_step_and_epoch_change_reopens(self):
        kwargs = dict(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=48,
            duration_seconds=10,
            attention_engine="sdpa",
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        record_denoise_failure(after_unwind=True, **kwargs)
        self.assertFalse(evaluate_setup(**kwargs).runnable)
        record_denoise_success(**kwargs)
        self.assertTrue(evaluate_setup(**kwargs).runnable)

        record_denoise_failure(after_unwind=True, **kwargs)
        stale = dict(FIXED_EPOCH)
        stale["attention"] = {"sdpa": True, "sol_attn": True, "sage2": False}
        reopened = evaluate_setup(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=48,
            duration_seconds=10,
            attention_engine="sdpa",
            path=self.path,
            epoch=stale,
        )
        self.assertTrue(reopened.runnable)

    def test_profile_options_hide_denied_portrait_high(self):
        record_denoise_failure(
            model_type="minimax_h3_ref2va",
            resolution="768x1344",
            num_inference_steps=28,
            duration_seconds=10,
            attention_engine="sol_attn",
            after_unwind=True,
            path=self.path,
            epoch=FIXED_EPOCH,
        )
        context = {
            "model_type": "minimax_h3_ref2va",
            "resolution": "768x1344",
            "duration_seconds": 10,
            "video_length": 241,
            "custom_settings": {"h3_attention_engine": "sol_attn"},
            "reference_shape": {"image_count": 2},
        }
        with mock.patch(
            "services.h3_host_limits.default_store_path",
            return_value=self.path,
        ), mock.patch(
            "services.h3_host_limits.current_epoch",
            return_value=FIXED_EPOCH,
        ):
            options = build_profile_options(
                context,
                model_exists=lambda _model: True,
                model_downloaded=lambda _model: True,
                sage2_status={"available": False, "validated": False},
                upscale_status={"enabled": True, "downloaded": True},
            )
        by_id = {item["id"]: item for item in options}
        self.assertFalse(by_id["high"]["available"])
        self.assertIn("could not finish", by_id["high"]["fallback_reason"])
        self.assertTrue(by_id["quality"]["available"])
        self.assertEqual(
            host_limit_reason_for_profile(
                by_id["high"]["settings"],
                context,
                path=self.path,
                epoch=FIXED_EPOCH,
            ).count("could not finish"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
