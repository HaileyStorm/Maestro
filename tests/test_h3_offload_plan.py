"""Model-free tests for the sealed whole-job H3 offload contract."""
from __future__ import annotations

import copy
import json
import unittest

from services.h3_offload_plan import (
    H3OffloadPlanError,
    H3_OFFLOAD_PLAN_PARAM_KEY,
    assert_h3_offload_plan_parity,
    build_h3_offload_plan,
    public_h3_offload_plan,
    seal_h3_offload_plan,
    validate_h3_offload_plan,
)


class H3OffloadPlanTests(unittest.TestCase):
    def _params(self):
        return {
            "model_type": "minimax_h3_ref2va",
            "resolution": "1344x768",
            "num_inference_steps": 20,
            "override_profile": -1,
            "prompt": "PRIVATE_AUTHORED_SENTINEL",
            "image_start": "/private/reference.png",
            "custom_settings": {
                "h3_attention_engine": "sol_attn",
                "h3_sol_tau": 1.0,
                "h3_sol_dense_steps": 10,
                "h3_sol_dense_blocks": 2,
                "h3_sol_min_tokens": 4096,
                "h3_source_audio_mode": "native",
            },
            "_h3_longform": {
                "fps": 24,
                "clip_frames": [124, 141, 158],
                "clip_published_frames": [124, 141, 150],
                "segment_models": [
                    {"model_type": "minimax_h3_ref2va"},
                    {"model_type": "minimax_h3_ref2va"},
                    {"model_type": "minimax_h3"},
                ],
                "global_prompt": "PRIVATE_LONGFORM_SENTINEL",
            },
        }

    def test_identity_changes_with_geometry_model_profile_and_schedule(self):
        base = self._params()
        digest = build_h3_offload_plan(
            base, effective_profile=4,
        )["digest"]
        variants = []

        geometry = copy.deepcopy(base)
        geometry["_h3_longform"]["clip_frames"][0] = 125
        variants.append(geometry)

        model = copy.deepcopy(base)
        model["_h3_longform"]["segment_models"][1]["model_type"] = (
            "minimax_h3_w4a8_fl2va"
        )
        variants.append(model)

        profile = copy.deepcopy(base)
        profile["override_profile"] = 5
        variants.append(profile)

        schedule = copy.deepcopy(base)
        schedule["num_inference_steps"] = 8
        variants.append(schedule)

        self.assertTrue(all(
            build_h3_offload_plan(candidate, effective_profile=4)["digest"]
            != digest
            for candidate in variants
        ))

    def test_manual_override_is_authoritative(self):
        params = self._params()
        params["override_profile"] = 3
        plan = build_h3_offload_plan(params, effective_profile=5)
        self.assertEqual(plan["profile"], 3)
        self.assertEqual(plan["source"], "manual_override")
        self.assertEqual({item["profile"] for item in plan["segments"]}, {3})

    def test_unknown_evidence_uses_current_profile_without_adapting(self):
        params = self._params()
        params["model_type"] = "minimax_h3_future_variant"
        params["_h3_longform"]["segment_models"] = [
            {"model_type": "minimax_h3_future_variant"}
            for _ in range(3)
        ]
        plan = build_h3_offload_plan(params, effective_profile=5)
        self.assertEqual(plan["profile"], 5)
        self.assertEqual(plan["source"], "current_profile_fallback")
        self.assertEqual(plan["transition_count"], 0)

    def test_multi_model_plan_records_only_real_movements(self):
        plan = build_h3_offload_plan(self._params(), effective_profile=4)
        self.assertEqual(
            [item["transition"] for item in plan["segments"]],
            ["initial_load", "resident_reuse", "model_transition"],
        )
        self.assertEqual(plan["transition_count"], 1)
        self.assertEqual(plan["movement_count"], 2)
        self.assertEqual(plan["movements"], [
            {"segment_index": 1, "kind": "initial_load"},
            {"segment_index": 3, "kind": "model_transition"},
        ])

    def test_recovery_plan_preserves_completed_prefix_profiles(self):
        params = self._params()
        params["override_profile"] = 5
        plan = build_h3_offload_plan(
            params,
            effective_profile=5,
            source="recovery_profile",
            segment_profiles=[4, 4, 5],
        )
        self.assertEqual(
            [item["profile"] for item in plan["segments"]], [4, 4, 5]
        )
        self.assertEqual(plan["transition_count"], 1)

    def test_seal_is_idempotent_but_rejects_changed_inputs(self):
        params = self._params()
        first = seal_h3_offload_plan(params, effective_profile=4)
        second = seal_h3_offload_plan(params, effective_profile=4)
        self.assertEqual(first, second)
        params["_h3_longform"]["clip_frames"][0] += 1
        with self.assertRaises(H3OffloadPlanError):
            seal_h3_offload_plan(params, effective_profile=4)

    def test_serialization_and_parity_reject_tampering(self):
        params = self._params()
        plan = seal_h3_offload_plan(params, effective_profile=4)
        round_trip = json.loads(json.dumps(plan))
        self.assertEqual(validate_h3_offload_plan(round_trip), plan)
        self.assertEqual(assert_h3_offload_plan_parity(plan, round_trip), plan)

        tampered = copy.deepcopy(round_trip)
        tampered["segments"][0]["generated_frames"] += 1
        with self.assertRaises(H3OffloadPlanError):
            validate_h3_offload_plan(tampered)
        with self.assertRaises(H3OffloadPlanError):
            assert_h3_offload_plan_parity(plan, tampered)

        unknown = copy.deepcopy(round_trip)
        unknown["private_text"] = "must reject"
        with self.assertRaises(H3OffloadPlanError):
            validate_h3_offload_plan(unknown)

        changed_params = copy.deepcopy(params)
        changed_params["_h3_longform"]["clip_published_frames"][-1] -= 1
        with self.assertRaises(H3OffloadPlanError):
            assert_h3_offload_plan_parity(
                plan, round_trip, params=changed_params,
            )

    def test_schedule_fields_are_closed_and_publication_geometry_is_required(self):
        params = self._params()
        params["custom_settings"]["h3_attention_engine"] = (
            "PRIVATE_AUTHORED_SENTINEL"
        )
        with self.assertRaises(H3OffloadPlanError):
            build_h3_offload_plan(params, effective_profile=4)

        params = self._params()
        del params["_h3_longform"]["clip_published_frames"]
        with self.assertRaises(H3OffloadPlanError):
            build_h3_offload_plan(params, effective_profile=4)

    def test_public_projection_is_bounded_and_private_text_free(self):
        params = self._params()
        plan = seal_h3_offload_plan(params, effective_profile=4)
        public = public_h3_offload_plan(plan)
        self.assertEqual(set(public), {
            "mode", "source", "profile", "transition_count",
        })
        rendered = json.dumps(plan)
        self.assertNotIn(params["prompt"], rendered)
        self.assertNotIn(params["image_start"], rendered)
        self.assertNotIn(
            params["_h3_longform"]["global_prompt"], rendered,
        )
        self.assertNotIn("digest", json.dumps(public))
        self.assertIn(H3_OFFLOAD_PLAN_PARAM_KEY, params)


if __name__ == "__main__":
    unittest.main()
