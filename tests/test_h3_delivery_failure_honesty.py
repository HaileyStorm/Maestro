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

from services.h3_dasiwa import H3ExperimentCompatibilityError  # noqa: E402
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

    def test_dasiwa_compatibility_error_keeps_public_contract_copy(self):
        message = "Dasiwa cannot be stacked with another LoRA or accelerator"
        details = build_failure_details(
            H3ExperimentCompatibilityError(message),
            stage="generation",
        )
        self.assertEqual(details["detail"], message)
        self.assertEqual(details["exception_type"], "H3ExperimentCompatibilityError")
        self.assertFalse(details["is_oom"])
        restored = normalize_failure_details(details)
        self.assertEqual(restored["detail"], message)
        self.assertNotIn("synthetic-private", json.dumps(details))

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


class PublicFailureCopyBoundaryTests(unittest.TestCase):
    def test_reviewed_copy_survives_live_and_persisted_boundaries(self):
        from services.public_failure_copy import PUBLIC_CONTRACT_MESSAGES
        from services.planning_failure import (
            normalize_planning_failure_envelope, planning_failure_envelope,
        )
        for message in PUBLIC_CONTRACT_MESSAGES:
            with self.subTest(message=message):
                produced = build_failure_details(ValueError(message), stage='generation')
                self.assertEqual(produced['detail'], message)
                self.assertEqual(normalize_failure_details(produced)['detail'], message)
                planned = planning_failure_envelope(ValueError(message), phase='planning_generation')
                self.assertEqual(planned['message'], message)
                self.assertEqual(normalize_planning_failure_envelope(planned)['message'], message)

    def test_familiar_prefixes_do_not_authorize_private_text(self):
        from services.planning_failure import (
            normalize_planning_failure_envelope, public_planning_failure_message,
        )
        for prefix in ['Dasiwa', 'MiniMax H3', 'H3 Turbo', 'Spectrum Experimental',
                       'Generation parameters', 'Pinned Ref2VA', 'Kijai W4A8']:
            text = prefix + ' synthetic-private /private/prompt.json'
            with self.subTest(prefix=prefix):
                self.assertEqual(public_planning_failure_message(
                    ValueError(text), fallback='Generation planning failed'),
                    'Generation planning failed')
                restored = normalize_failure_details({
                    'detail': text, 'stage': 'audio_mux',
                    'exception_type': 'H3ExperimentCompatibilityError', 'is_oom': False,
                })
                self.assertEqual(restored['detail'], 'The rendered output could not be combined with audio.')
                planned = normalize_planning_failure_envelope({'message': text})
                self.assertEqual(planned['message'], 'Generation planning failed')
                self.assertNotIn('synthetic-private', json.dumps([restored, planned]))

    def test_unknown_fallbacks_and_malformed_copy_are_not_published(self):
        from services.planning_failure import safe_public_contract_message

        class Hostile(str):
            def __str__(self):
                raise AssertionError('must not stringify untrusted objects')
            def __eq__(self, other):
                raise AssertionError('must not compare untrusted subclasses')

        known = 'Dasiwa cannot be stacked with another LoRA or accelerator'
        for text in [None, {}, [], True, 7, Hostile(known), known + '\nprivate',
                     known + '\rprivate', known + '\x00private', known + ' private',
                     'Dasiwa ' + 'x' * 250]:
            self.assertEqual(safe_public_contract_message(
                text, fallback='synthetic-private-fallback'), 'Generation planning failed')

    def test_native_frame_limit_template_is_closed_and_bounded(self):
        from services.planning_failure import safe_public_contract_message
        template = ('MiniMax H3 clips are limited to {} frames each. '
                    'Split oversized Director scenes into consecutive clips; a '
                    'single long Studio prompt is segmented automatically.')
        message = template.format(345)
        self.assertEqual(safe_public_contract_message(
            message, fallback='Generation failed.'), message)
        for value in ['private', '345/private', '345\n', '-1', '1.2', '10000', '0345']:
            self.assertEqual(safe_public_contract_message(
                template.format(value), fallback='Generation failed.'), 'Generation failed.')

    def test_stage_copy_is_canonical_and_preserved_on_restoration(self):
        from services.public_failure_copy import FAILURE_STAGE_DETAILS
        for stage, expected in FAILURE_STAGE_DETAILS.items():
            produced = build_failure_details(RuntimeError('private'), stage=stage)
            self.assertEqual(produced['detail'], expected)
            self.assertEqual(normalize_failure_details(produced)['detail'], expected)


class PublicPreparationTypeErrorTests(unittest.TestCase):
    def test_exact_type_errors_keep_actionable_shape_copy(self):
        from services.planning_failure import planning_failure_envelope
        for message in ['Generation parameters are invalid.',
                        'Generation custom settings are invalid.',
                        'Generation LoRA selection is invalid.']:
            self.assertEqual(planning_failure_envelope(
                TypeError(message), phase='planning_generation')['message'], message)
            produced = build_failure_details(TypeError(message), stage='generation')
            self.assertEqual(normalize_failure_details(produced)['detail'], message)
        self.assertEqual(planning_failure_envelope(
            TypeError('Generation parameters synthetic-private'),
            phase='planning_generation')['message'], 'Generation planning failed')

    def test_stage_copy_cannot_be_mutated_into_public_authority(self):
        from services.public_failure_copy import FAILURE_STAGE_DETAILS
        from services.planning_failure import safe_public_contract_message
        with self.assertRaises(TypeError):
            FAILURE_STAGE_DETAILS['generation'] = 'synthetic-private'
        self.assertEqual(FAILURE_STAGE_DETAILS['generation'], 'Generation failed.')
        self.assertEqual(safe_public_contract_message(
            'synthetic-private', fallback='synthetic-private'), 'Generation planning failed')
