"""Deterministic H3 authored-source template replay."""

from __future__ import annotations

import copy
import unittest

from services.h3_shot_planner import (
    H3_COMPILER_INPUT_REPLAY_CANONICAL_VERSION,
    H3ShotPlanError,
    h3_effective_source,
    h3_source_compiler_inputs,
    plan_h3_native_shots,
    resolve_h3_source_template,
    validate_h3_shot_plan_seal,
)
from services.h3_canonical_prompt import canonicalize_h3_prompt


DIALOGUE = "<d>[English] Keep {animal} literal.</d>"
SOURCE = f'!{{animal}}="cat"\nA {{animal}} crosses the room. {DIALOGUE}'


class H3SourceTemplateTests(unittest.TestCase):
    def plan(self, *, counts=(240,), replay=None):
        kwargs = {}
        if replay is None:
            kwargs["source_canonicalization"] = "t2va_template"
        else:
            kwargs["source_prompts"] = [SOURCE]
            kwargs["source_compiler_inputs"] = [replay]
        return plan_h3_native_shots(
            global_prompt=SOURCE,
            clip_frame_counts=list(counts),
            fps=24,
            **kwargs,
        )

    def test_template_resolution_precedes_canonicalization_and_preserves_raw_source(self):
        self.assertEqual(
            resolve_h3_source_template(SOURCE),
            f"A cat crosses the room. {DIALOGUE}",
        )
        plan = self.plan()
        contract = plan["source_contracts"][0]
        self.assertEqual(plan["global_prompt"], SOURCE)
        self.assertEqual(contract["authored_prompt"], SOURCE)
        self.assertEqual(contract["source_canonicalization"], {
            "mode": "t2va",
            "recipe_version": 2,
            "duration_seconds": 10.0,
            "fps": 24.0,
            "published_frames": 240,
        })
        self.assertIn("A cat crosses the room.", contract["semantic_prompt"])
        self.assertNotIn("!{animal}", contract["semantic_prompt"])
        self.assertNotIn("A {animal} crosses", contract["semantic_prompt"])
        self.assertEqual(contract["semantic_prompt"].count(DIALOGUE), 1)
        self.assertEqual(contract["dialogue_manifest"][0]["exact_block"], DIALOGUE)
        effective = h3_effective_source(
            SOURCE, contract["source_canonicalization"],
        )
        self.assertIn("A cat crosses the room.", effective)
        self.assertEqual(effective.count(DIALOGUE), 1)
        self.assertNotIn("!{animal}", effective)
        validate_h3_shot_plan_seal(plan)

    def test_recipe_two_compiler_inputs_replay_exact_semantic_source(self):
        original = self.plan()
        contract = original["source_contracts"][0]
        compiler_inputs = h3_source_compiler_inputs(contract)
        self.assertEqual(
            compiler_inputs["version"],
            H3_COMPILER_INPUT_REPLAY_CANONICAL_VERSION,
        )
        self.assertEqual(
            compiler_inputs["source_canonicalization"]["recipe_version"], 2,
        )
        before = copy.deepcopy(compiler_inputs)
        replay = self.plan(counts=(120, 120), replay=compiler_inputs)
        replay_contract = replay["source_contracts"][0]
        self.assertEqual(compiler_inputs, before)
        self.assertEqual(replay_contract["authored_prompt"], SOURCE)
        self.assertEqual(
            replay_contract["source_canonicalization"],
            contract["source_canonicalization"],
        )
        self.assertEqual(replay_contract["semantic_prompt"], contract["semantic_prompt"])
        self.assertEqual(
            h3_source_compiler_inputs(replay_contract), compiler_inputs,
        )
        validate_h3_shot_plan_seal(replay)

    def test_template_only_recipe_resolves_long_base_before_split_and_replay(self):
        frames = (121, 121)
        literal = "<d>[English] Keep {animal} exactly literal.</d>"
        base = canonicalize_h3_prompt(
            f"A {{animal}} crosses the room. {literal}",
            duration_seconds=sum(frames) / 24,
            mode="t2va",
        )
        raw = f'!{{animal}}="cat"\n{base}'
        plan = plan_h3_native_shots(
            global_prompt=raw,
            clip_frame_counts=list(frames),
            fps=24,
            source_canonicalization="template",
        )
        contract = plan["source_contracts"][0]
        self.assertEqual(contract["authored_prompt"], raw)
        self.assertEqual(contract["source_canonicalization"], {
            "mode": "template",
            "recipe_version": 1,
            "duration_seconds": sum(frames) / 24,
            "fps": 24.0,
            "published_frames": sum(frames),
        })
        resolved = resolve_h3_source_template(raw)
        self.assertEqual(
            h3_effective_source(raw, contract["source_canonicalization"]),
            resolved,
        )
        self.assertNotIn("!{animal}", contract["semantic_prompt"])
        self.assertNotIn("A {animal} crosses", contract["semantic_prompt"])
        self.assertTrue(all("cat" in prompt for prompt in plan["clip_prompts"]))
        self.assertEqual(contract["semantic_prompt"].count(literal), 1)
        self.assertEqual(sum(prompt.count(literal) for prompt in plan["clip_prompts"]), 1)

        compiler_inputs = h3_source_compiler_inputs(contract)
        self.assertEqual(
            compiler_inputs["source_canonicalization"],
            contract["source_canonicalization"],
        )
        replay = plan_h3_native_shots(
            global_prompt=raw,
            source_prompts=[raw],
            source_compiler_inputs=[compiler_inputs],
            clip_frame_counts=[125, 117],
            fps=24,
        )
        replay_contract = replay["source_contracts"][0]
        self.assertEqual(replay_contract["authored_prompt"], raw)
        self.assertEqual(replay_contract["semantic_prompt"], contract["semantic_prompt"])
        self.assertEqual(
            replay_contract["source_canonicalization"],
            contract["source_canonicalization"],
        )
        self.assertEqual(sum(prompt.count(literal) for prompt in replay["clip_prompts"]), 1)
        validate_h3_shot_plan_seal(plan)
        validate_h3_shot_plan_seal(replay)

    def test_invalid_template_variable_has_one_bounded_error(self):
        invalid = "A {missing} crosses the room."
        with self.assertRaises(H3ShotPlanError) as direct:
            resolve_h3_source_template(invalid)
        self.assertEqual(str(direct.exception), "H3 source template is invalid")
        with self.assertRaises(H3ShotPlanError) as planned:
            plan_h3_native_shots(
                global_prompt=invalid,
                clip_frame_counts=[240],
                fps=24,
                source_canonicalization="t2va_template",
            )
        self.assertEqual(str(planned.exception), "H3 source template is invalid")
        self.assertNotIn("missing", str(planned.exception))
        self.assertNotIn(invalid, str(planned.exception))

    def test_recipe_one_and_no_recipe_contracts_keep_their_existing_shapes(self):
        raw = "  [0s-10s] A cat crosses the room.  "
        canonical = plan_h3_native_shots(
            global_prompt=raw,
            clip_frame_counts=[240],
            fps=24,
            source_canonicalization="t2va",
        )
        descriptor = canonical["source_contracts"][0]["source_canonicalization"]
        self.assertEqual(descriptor["recipe_version"], 1)
        self.assertEqual(
            h3_source_compiler_inputs(canonical["source_contracts"][0])[
                "source_canonicalization"
            ],
            descriptor,
        )
        effective = h3_effective_source(raw, descriptor)
        self.assertIn("A cat crosses the room.", effective)
        self.assertNotIn("!{", effective)

        legacy = plan_h3_native_shots(
            global_prompt=raw,
            clip_frame_counts=[240],
            fps=24,
        )
        legacy_contract = legacy["source_contracts"][0]
        self.assertNotIn("source_canonicalization", legacy_contract)
        self.assertEqual(legacy_contract["authored_prompt"], raw.strip())


if __name__ == "__main__":
    unittest.main()
