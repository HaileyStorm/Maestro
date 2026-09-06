"""Structured H3 delivery failures must not invent GPU exhaustion."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.oom_detect import (  # noqa: E402
    build_failure_details,
    delivery_oom_info,
    detect_oom,
    normalize_failure_details,
)


class DeliveryStructuredFailureHonestyTests(unittest.TestCase):
    def test_host_and_ffmpeg_failures_cannot_keep_gpu_oom_codes(self):
        error = RuntimeError(
            "ffmpeg exited after host out of memory at /private/output.mp4"
        )
        error.code = "cuda_oom"
        error.stage = "delivery"
        details = build_failure_details(
            error, stage="delivery", code="cuda_oom",
        )
        self.assertFalse(details["is_oom"])
        self.assertEqual(details["code"], "delivery_failed")
        self.assertEqual(details["stage"], "delivery")
        self.assertNotIn("allocator", details)
        self.assertIsNone(detect_oom(error, 0.8))
        self.assertIsNone(delivery_oom_info(
            error,
            0.8,
            requested_target="3840x2160",
            native_available=True,
            retry_count=1,
        ))
        public = json.dumps(details)
        self.assertNotIn("/private", public)
        self.assertNotIn("cuda_oom", public)

    def test_normalizer_strips_gpu_oom_codes_when_is_oom_is_false(self):
        for stage, code in (
            ("delivery", "cuda_oom"),
            ("publication", "hip_oom"),
            ("concat", "cuda_oom"),
        ):
            with self.subTest(stage=stage, code=code):
                details = normalize_failure_details({
                    "stage": stage,
                    "code": code,
                    "exception_type": "RuntimeError",
                    "is_oom": False,
                    "allocator": {
                        "device_type": "cuda",
                        "free_bytes": 1024,
                        "total_bytes": 8192,
                    },
                })
                self.assertEqual(details["stage"], stage)
                self.assertEqual(details["code"], f"{stage}_failed")
                self.assertFalse(details["is_oom"])
                self.assertNotIn("allocator", details)
                self.assertNotIn("cuda_oom", json.dumps(details))
                self.assertNotIn("hip_oom", json.dumps(details))

    def test_segment_checkpoint_error_is_not_audio_mux(self):
        from services.queue_recovery_runtime import QueueRecoveryRuntimeError

        error = QueueRecoveryRuntimeError(
            "H3 segment predecessor is not durably checkpointed."
        )
        error.stage = "segment_checkpoint"
        error.code = "segment_checkpoint_failed"
        details = build_failure_details(
            error, stage="audio_mux", code="audio_mux_failed",
        )
        self.assertEqual(details["stage"], "segment_checkpoint")
        self.assertEqual(details["code"], "segment_checkpoint_failed")
        self.assertEqual(
            details["detail"],
            "The rendered segment could not be sealed for recovery.",
        )
        self.assertFalse(details["is_oom"])
        self.assertNotIn("audio", details["detail"].casefold())


    def test_real_cuda_oom_still_publishes_confident_gpu_code(self):
        error = RuntimeError("CUDA out of memory while decoding delivery")
        details = build_failure_details(
            error, stage="delivery", code="delivery_failed",
        )
        self.assertTrue(details["is_oom"])
        self.assertEqual(details["code"], "cuda_oom")
        self.assertEqual(details["stage"], "delivery")
        self.assertIsNotNone(detect_oom(error, 0.8))
        self.assertIsNotNone(delivery_oom_info(
            error,
            0.8,
            requested_target="3840x2160",
            native_available=True,
            retry_count=1,
        ))
        restored = normalize_failure_details({
            "stage": "delivery",
            "code": "delivery_failed",
            "is_oom": True,
            "exception_type": "RuntimeError",
        })
        self.assertTrue(restored["is_oom"])
        self.assertEqual(restored["code"], "cuda_oom")
