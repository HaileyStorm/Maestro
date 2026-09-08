"""Transactional H3 per-segment adaptive prompt binding."""

from __future__ import annotations

import copy
import json
import math
import unittest

from services.h3_adaptive_execution import (
    H3AdaptiveExecutionError,
    bind_h3_execution_segment,
    validate_h3_mapping_receipt,
)
from services.h3_canonical_prompt import canonicalize_h3_prompt
from services.h3_prompt_mapping import H3PromptMappingError, create_mapping_record
from services.h3_shot_planner import plan_h3_native_shots, validate_h3_shot_plan_seal
from services.queue_recovery_runtime import QueueRecoveryRuntimeError


DIALOGUE = "<d>[English]  Keep  these exact words.  </d>"
REFERENCES = [{
    "type": "image",
    "path": "reference.png",
    "source_key": "upload:1",
    "image_intent": "identity",
}]


class H3AdaptiveExecutionTests(unittest.TestCase):
    def plan(self, *, frames=(121, 125)):
        prompts = [
            canonicalize_h3_prompt(
                (
                    f"A person speaks {DIALOGUE}"
                    if index == 0
                    else f"Scene {index + 1} continues with a distinct action."
                ),
                duration_seconds=count / 24,
                mode="t2va",
            )
            for index, count in enumerate(frames)
        ]
        return plan_h3_native_shots(
            global_prompt=prompts[0],
            clip_frame_counts=list(frames),
            fps=24,
            source_prompts=prompts,
            source_indices=list(range(len(prompts))),
        )

    def bind(self, plan, index=0, model="minimax_h3_ref2va", references=REFERENCES, **kwargs):
        return bind_h3_execution_segment(
            plan,
            segment_index=index,
            model_type=model,
            reference_manifest=references,
            **kwargs,
        )

    def continuation_plan(self, counts):
        source = canonicalize_h3_prompt(
            "A person crosses the room.",
            duration_seconds=sum(counts) / 24,
            mode="t2va",
        )
        return plan_h3_native_shots(
            global_prompt=source,
            clip_frame_counts=list(counts),
            fps=24,
            source_prompts=[source],
            source_indices=[0] * len(counts),
        )

    def test_ref_binding_preserves_source_events_dialogue_geometry_and_other_prompts(self):
        plan = self.plan()
        before = copy.deepcopy(plan)
        result = self.bind(plan)
        self.assertEqual(plan, before)
        self.assertEqual(result["record"]["source_prompt"], plan["clip_prompts"][0])
        self.assertEqual(result["record"]["target_schema"], "ref2va")
        self.assertEqual(result["plan"]["clip_prompts"][1], plan["clip_prompts"][1])
        self.assertEqual(result["plan"]["event_ownership"], plan["event_ownership"])
        self.assertEqual(result["plan"]["dialogue_manifest"], plan["dialogue_manifest"])
        self.assertEqual(result["plan"]["clip_frames"], plan["clip_frames"])
        self.assertEqual(result["plan"]["clip_published_frames"], plan["clip_published_frames"])
        self.assertEqual(result["plan"]["clip_trim_tail_frames"], plan["clip_trim_tail_frames"])
        self.assertEqual(result["plan"]["clip_prompts"][0].count(DIALOGUE), 1)
        validate_h3_shot_plan_seal(result["plan"])
        self.assertNotEqual(
            result["plan"]["prompt_contract_seal"], plan["prompt_contract_seal"]
        )
        self.assertRegex(result["receipt"]["source_lineage_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(result["receipt"]["frames"], 121)
        self.assertEqual(result["receipt"]["published_frames"], 121)
        self.assertEqual(result["receipt"]["trim_tail_frames"], 0)
        self.assertEqual(result["receipt"]["fps"], 24.0)

    def test_switching_to_base_always_maps_from_original_source_plan(self):
        plan = self.plan(frames=(121,))
        before = copy.deepcopy(plan)
        ref = self.bind(plan)
        base_first = self.bind(plan, model="minimax_h3", references=None)
        base_again = self.bind(plan, model="minimax_h3", references=[])
        self.assertEqual(plan, before)
        self.assertEqual(base_first, base_again)
        expected = create_mapping_record(
            plan["clip_prompts"][0], "base", duration_seconds=121 / 24,
            reference_manifest=None,
        )
        self.assertEqual(base_first["record"], expected)
        self.assertEqual(base_first["plan"]["clip_prompts"][0], expected["mapped_prompt"])
        self.assertNotEqual(
            base_first["plan"]["clip_prompts"][0], ref["plan"]["clip_prompts"][0]
        )
        self.assertNotIn("retention_analysis:", base_first["plan"]["clip_prompts"][0])

    def test_target_references_and_geometry_change_receipt_identity(self):
        plan = self.plan(frames=(121,))
        base = self.bind(plan, model="minimax_h3", references=None)
        ref = self.bind(plan)
        changed_references = copy.deepcopy(REFERENCES)
        changed_references[0]["path"] = "other.png"
        rebound = self.bind(plan, references=changed_references)
        changed_geometry = self.bind(self.plan(frames=(125,)))
        self.assertNotEqual(base["receipt"], ref["receipt"])
        self.assertNotEqual(
            ref["receipt"]["mapping_record_sha256"],
            rebound["receipt"]["mapping_record_sha256"],
        )
        self.assertNotEqual(ref["receipt"], changed_geometry["receipt"])
        self.assertEqual(changed_geometry["receipt"]["frames"], 125)

    def test_mapping_duration_uses_published_frames_while_receipt_binds_padding(self):
        prompt = canonicalize_h3_prompt(
            "A person crosses the room.", duration_seconds=10, mode="t2va"
        )
        plan = plan_h3_native_shots(
            global_prompt=prompt,
            clip_frame_counts=[256],
            clip_requested_frames=[240],
            fps=24,
            source_prompts=[prompt],
            source_indices=[0],
        )
        result = self.bind(plan)
        self.assertEqual(result["record"]["duration_seconds"], 10.0)
        self.assertEqual(result["receipt"]["frames"], 256)
        self.assertEqual(result["receipt"]["published_frames"], 240)
        self.assertEqual(result["receipt"]["trim_tail_frames"], 16)
        validate_h3_shot_plan_seal(result["plan"])

    def test_completed_prefix_receipt_ignores_a_replanned_unrelated_suffix(self):
        first = self.continuation_plan((121, 121, 125))
        replanned = self.continuation_plan((121, 125, 121))
        self.assertNotEqual(
            first["prompt_contract_seal"], replanned["prompt_contract_seal"]
        )
        self.assertEqual(first["clip_prompts"][0], replanned["clip_prompts"][0])
        self.assertEqual(self.bind(first)["receipt"], self.bind(replanned)["receipt"])

    def test_current_authored_occurrence_and_execution_interval_change_lineage(self):
        original = self.plan(frames=(121,))
        changed_dialogue_prompt = canonicalize_h3_prompt(
            "A person speaks <d>[English] Different exact words.</d>",
            duration_seconds=121 / 24,
            mode="t2va",
        )
        changed_dialogue = plan_h3_native_shots(
            global_prompt=changed_dialogue_prompt,
            clip_frame_counts=[121],
            fps=24,
            source_prompts=[changed_dialogue_prompt],
            source_indices=[0],
        )
        self.assertNotEqual(
            self.bind(original)["receipt"]["source_lineage_sha256"],
            self.bind(changed_dialogue)["receipt"]["source_lineage_sha256"],
        )

        early = self.bind(self.continuation_plan((121, 121, 125)), index=1)
        shifted = self.bind(self.continuation_plan((125, 121, 121)), index=1)
        self.assertEqual(early["receipt"]["frames"], shifted["receipt"]["frames"])
        self.assertNotEqual(
            early["receipt"]["source_lineage_sha256"],
            shifted["receipt"]["source_lineage_sha256"],
        )

    def test_stale_source_plan_seal_rejects_without_mutation(self):
        plan = self.plan(frames=(121,))
        plan["clip_prompts"][0] += " changed after sealing"
        before = copy.deepcopy(plan)
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.bind(plan)
        self.assertEqual(plan, before)

    def test_bool_indices_unknown_models_and_invalid_attestation_reject(self):
        plan = self.plan(frames=(121,))
        for index in (True, False, -1, 1):
            with self.subTest(index=index), self.assertRaises(H3AdaptiveExecutionError):
                self.bind(plan, index=index)
        with self.assertRaisesRegex(H3AdaptiveExecutionError, "architecture"):
            self.bind(plan, model="other_model")
        with self.assertRaisesRegex(H3AdaptiveExecutionError, "attestation"):
            self.bind(plan, authored_ref=1)

    def test_authored_ref_requires_explicit_caller_attestation(self):
        seed = self.plan(frames=(121,))
        mapped = self.bind(seed)
        generated = mapped["record"]["mapped_prompt"]
        authored_plan = mapped["plan"]
        before = copy.deepcopy(authored_plan)
        with self.assertRaisesRegex(H3PromptMappingError, "verified mapping lineage"):
            self.bind(authored_plan)
        result = self.bind(authored_plan, authored_ref=True)
        self.assertEqual(authored_plan, before)
        self.assertEqual(result["record"]["source_origin"], "explicit_authoring")
        self.assertEqual(result["record"]["origin"], "authored_passthrough")
        self.assertEqual(result["record"]["mapped_prompt"], generated)

    def test_receipt_validation_is_closed_relational_and_copy_isolated(self):
        receipt = self.bind(self.plan(frames=(121,)))["receipt"]
        saved = json.loads(json.dumps(receipt))
        validated = validate_h3_mapping_receipt(saved)
        validated["source_lineage_sha256"] = "0" * 64
        self.assertEqual(receipt, saved)
        mutations = (
            lambda value: value.update(extra=True),
            lambda value: value.update(version=True),
            lambda value: value.update(segment_index=True),
            lambda value: value.update(target_schema="base"),
            lambda value: value.update(published_frames=value["published_frames"] - 1),
            lambda value: value.update(fps=math.inf),
            lambda value: value.update(mapping_record_sha256="invalid"),
            lambda value: value.update(source_lineage_sha256="A" * 64),
        )
        for mutate in mutations:
            tampered = copy.deepcopy(receipt)
            mutate(tampered)
            with self.subTest(tampered=tampered), self.assertRaises(
                H3AdaptiveExecutionError
            ):
                validate_h3_mapping_receipt(tampered)


if __name__ == "__main__":
    unittest.main()
