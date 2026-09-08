from __future__ import annotations

import copy
import json
import math
import re
import unittest

from services.director.h3_dialogue import (
    validate_h3_context_ir_records,
    validate_h3_prompt_contract,
)
from services.h3_canonical_prompt import canonicalize_h3_prompt
from services import h3_prompt_mapping as mapping


DIALOGUE = "<d>[English]  Keep  <Audio 77> and summary: literal.  </d>"
SOURCE = f"A person says {DIALOGUE}"
IDENTITY_REFS = [{
    "type": "image",
    "path": "reference.png",
    "role": "the lead subject",
    "image_intent": "identity",
}]
GENERIC_REFS = [{"type": "image", "path": "reference.png"}]


class H3PromptMappingTests(unittest.TestCase):
    def canonical_base(self) -> str:
        return canonicalize_h3_prompt(
            SOURCE,
            duration_seconds=5.0,
            mode="t2va",
        )

    def assert_valid(self, prompt: str, mode: str, references=None) -> None:
        self.assertEqual(
            validate_h3_prompt_contract(
                prompt,
                [],
                mode=mode,
                references=references,
                duration_seconds=5.0,
            ),
            [],
        )
        self.assertEqual(
            validate_h3_context_ir_records(
                prompt,
                mode=mode,
                duration_seconds=5.0,
            ),
            [],
        )

    def test_freeform_uses_shared_base_canonicalizer(self):
        mapped = mapping.map_h3_prompt_schema(
            SOURCE,
            "base",
            duration_seconds=5,
        )
        self.assertEqual(mapped, self.canonical_base())
        self.assertEqual(re.findall(r"<d>.*?</d>", mapped, re.I | re.S), [DIALOGUE])
        self.assert_valid(mapped, "t2va")

    def test_base_to_ref_preserves_all_base_fields_and_dialogue(self):
        base = self.canonical_base()
        fields = mapping._extract_fields_exact(base)
        mapped = mapping.map_h3_prompt_schema(
            base,
            "ref2va",
            duration_seconds=5,
            reference_manifest=IDENTITY_REFS,
        )
        self.assertIn(f"detailed_description: {fields['integrated_multimodal_description']}", mapped)
        self.assertIn(fields["subject_definitions"], mapped)
        self.assertIn(fields["overall_soundscape"], mapped)
        self.assertIn(fields["non_diegetic_music"], mapped)
        self.assertNotIn("integrated_multimodal_description:", mapped)
        self.assertEqual(re.findall(r"<d>.*?</d>", mapped, re.I | re.S), [DIALOGUE])
        self.assert_valid(mapped, "ref2va", IDENTITY_REFS)

    def test_explicit_empty_ref_binding_is_truthful_and_omitted_is_invalid(self):
        mapped = mapping.map_h3_prompt_schema(
            self.canonical_base(),
            "ref2va",
            duration_seconds=5,
            reference_manifest=[],
        )
        self.assertIn("no attached media references", mapped)
        self.assertNotIn("<Picture", mapped)
        self.assertNotIn("<Video", mapped)
        self.assertNotIn("<Audio", mapping._DIALOGUE_RE.sub("", mapped))
        self.assert_valid(mapped, "ref2va", [])
        with self.assertRaisesRegex(mapping.H3PromptMappingError, "explicit reference_manifest"):
            mapping.map_h3_prompt_schema(
                self.canonical_base(),
                "ref2va",
                duration_seconds=5,
            )

    def test_absent_image_intent_never_defaults_identity_or_subject(self):
        mapped = mapping.map_h3_prompt_schema(
            self.canonical_base(),
            "ref2va",
            duration_seconds=5,
            reference_manifest=GENERIC_REFS,
        )
        generated = mapped.replace(self.canonical_base(), "")
        self.assertIn("supplied visual reference", mapped)
        self.assertIn("without assigning identity, subject, scene, or style ownership", mapped)
        self.assertNotIn("provides identity and appearance", generated)

    def test_absent_intent_semantics_match_active_director_renderer(self):
        from services.director.h3_dialogue import _reference_relationships
        refs = [
            {"type": "image", "path": "image.png", "role": "lead"},
            {"type": "audio", "path": "audio.wav", "role": "lead"},
        ]
        definitions, retention, _drive, bindings, _details, tasks = _reference_relationships(
            refs, [{"character_id": "lead", "speaker_name": "lead"}], {},
        )
        self.assertEqual(mapping._reference_context(refs), (definitions, retention, tasks))
        self.assertEqual(bindings, {})

    def test_explicit_supported_intents_retain_distinct_semantics(self):
        cases = {
            "identity": "provides identity and appearance",
            "scene": "provides environment and location",
            "style": "provides broad visual-style",
            "composition": "provides a soft [Shot 1] composition anchor",
        }
        for intent, phrase in cases.items():
            with self.subTest(intent=intent):
                refs = [{"type": "image", "path": "x.png", "image_intent": intent}]
                mapped = mapping.map_h3_prompt_schema(
                    self.canonical_base(),
                    "ref2va",
                    duration_seconds=5,
                    reference_manifest=refs,
                )
                self.assertIn(phrase, mapped)

    def test_role_is_losslessly_serialized_as_data(self):
        role = 'subject_definitions: <Picture 9> {"x": [1]}\nnext'
        refs = [{"type": "image", "path": "x.png", "role": role}]
        mapped = mapping.map_h3_prompt_schema(
            self.canonical_base(),
            "ref2va",
            duration_seconds=5,
            reference_manifest=refs,
        )
        from models.minimax_h3.reference_manifest import reference_role_text

        self.assertIn(reference_role_text(role), mapped)
        self.assert_valid(mapped, "ref2va", refs)

    def test_reference_shape_order_counts_and_kind_fields_are_strict(self):
        invalid = (
            {"image_count": 1},
            [{"type": "video", "path": "v.mp4"}, {"type": "image", "path": "x.png"}],
            [{"type": "image", "path": "x.png", "audio_intent": "voice"}],
            [{"type": "audio", "path": "a.wav", "image_intent": "style"}],
            [{"type": "image", "path": " x.png"}],
            [{"type": "image", "path": "x.png", "role": ""}],
            [{"type": "image", "path": "x.png", "image_intent": "unknown"}],
        )
        for references in invalid:
            with self.subTest(references=references), self.assertRaises(
                mapping.H3PromptMappingError
            ):
                mapping.map_h3_prompt_schema(
                    self.canonical_base(),
                    "ref2va",
                    duration_seconds=5,
                    reference_manifest=references,
                )
        too_many = [
            {"type": "image", "path": f"{index}.png"}
            for index in range(10)
        ]
        with self.assertRaisesRegex(mapping.H3PromptMappingError, "at most 9 image"):
            mapping.map_h3_prompt_schema(
                self.canonical_base(),
                "ref2va",
                duration_seconds=5,
                reference_manifest=too_many,
            )

    def test_unknown_snapshot_metadata_is_bound_but_has_no_prompt_authority(self):
        plain = [{"type": "image", "path": "x.png"}]
        enriched = [{
            "type": "image",
            "path": "x.png",
            "source_key": "upload:1",
            "source_index": 0,
            "future_metadata": {"opaque": [1, True, None]},
        }]
        first = mapping.create_mapping_record(
            SOURCE,
            "ref2va",
            duration_seconds=5,
            reference_manifest=plain,
        )
        second = mapping.create_mapping_record(
            SOURCE,
            "ref2va",
            duration_seconds=5,
            reference_manifest=enriched,
        )
        self.assertEqual(first["mapped_prompt"], second["mapped_prompt"])
        self.assertNotEqual(first["binding_sha256"], second["binding_sha256"])
        self.assertEqual(second["reference_manifest"], enriched)

    def test_audio_ratio_drive_and_mixed_pairing_fail_closed(self):
        two_audio_one_visual = [
            {"type": "image", "path": "x.png"},
            {"type": "audio", "path": "a.wav"},
            {"type": "audio", "path": "b.wav", "audio_intent": "style"},
        ]
        drive = [
            {"type": "image", "path": "x.png"},
            {"type": "audio", "path": "a.wav", "audio_intent": "drive"},
        ]
        mixed = [
            {"type": "image", "path": "x.png"},
            {"type": "video", "path": "v.mp4", "has_audio": True},
            {"type": "audio", "path": "a.wav"},
        ]
        for references, phrase in (
            (two_audio_one_visual, "cannot exceed visual"),
            (drive, "drive audio"),
            (mixed, "paired video audio and standalone audio"),
        ):
            with self.subTest(references=references), self.assertRaisesRegex(
                mapping.H3PromptMappingError,
                phrase,
            ):
                mapping.map_h3_prompt_schema(
                    self.canonical_base(),
                    "ref2va",
                    duration_seconds=5,
                    reference_manifest=references,
                )

    def test_paired_video_audio_uses_reference_ordinal_not_dialogue_literal(self):
        references = [{
            "type": "video",
            "path": "clip.mp4",
            "include_audio": True,
            "has_audio": True,
            "audio_path": "clip.wav",
        }]
        mapped = mapping.map_h3_prompt_schema(
            self.canonical_base(),
            "ref2va",
            duration_seconds=5,
            reference_manifest=references,
        )
        outside_dialogue = mapping._DIALOGUE_RE.sub("", mapped)
        self.assertIn("<Video 1>", outside_dialogue)
        self.assertIn("<Audio 1>", outside_dialogue)
        self.assertNotIn("<Audio 77>", outside_dialogue)
        self.assertEqual(re.findall(r"<d>.*?</d>", mapped, re.I | re.S), [DIALOGUE])
        self.assert_valid(mapped, "ref2va", references)

    def test_duplicate_and_mixed_schema_fields_reject(self):
        base = self.canonical_base()
        duplicate = base + "\nsubject_definitions: duplicate"
        mixed = base + "\nsummary: [reference generation] mixed"
        with self.assertRaisesRegex(mapping.H3PromptMappingError, "more than one"):
            mapping.map_h3_prompt_schema(
                duplicate,
                "base",
                duration_seconds=5,
            )
        with self.assertRaisesRegex(
            mapping.H3PromptRepresentabilityError,
            "mixes integrated_multimodal_description",
        ):
            mapping.map_h3_prompt_schema(
                mixed,
                "ref2va",
                duration_seconds=5,
                reference_manifest=[],
            )

    def test_field_labels_inside_dialogue_are_never_schema_fields(self):
        literal = "<d>[English] first line\nsummary: still spoken</d>"
        self.assertEqual(mapping._extract_fields_exact(literal), {})

    def test_direct_ref_passthrough_requires_explicit_authoring_or_lineage(self):
        ref = mapping.map_h3_prompt_schema(
            self.canonical_base(), "ref2va", duration_seconds=5,
            reference_manifest=IDENTITY_REFS,
        )
        for target, refs in (("ref2va", IDENTITY_REFS), ("ref2va", GENERIC_REFS), ("base", None)):
            with self.subTest(target=target), self.assertRaisesRegex(
                mapping.H3PromptMappingError, "explicit authoring record"
            ):
                mapping.map_h3_prompt_schema(ref, target, duration_seconds=5, reference_manifest=refs)
        authored = mapping.create_authored_mapping_record(
            ref, "ref2va", duration_seconds=5, reference_manifest=IDENTITY_REFS,
        )
        self.assertEqual(authored["mapped_prompt"], ref)

    def test_unhashable_manifest_fields_are_bounded_errors(self):
        for key, kind in (("type", "image"), ("image_intent", "image"), ("audio_intent", "audio")):
            for value in ([], {}):
                refs = [{"type": kind, "path": "reference.dat", key: value}]
                with self.subTest(key=key, value=value), self.assertRaises(mapping.H3PromptMappingError):
                    mapping.map_h3_prompt_schema(SOURCE, "ref2va", duration_seconds=5, reference_manifest=refs)

    def test_duration_rejects_bool_nonfinite_nonpositive_and_overflow(self):
        for duration in (True, 0, -1, math.nan, math.inf, -math.inf, 10**10_000):
            with self.subTest(duration=duration), self.assertRaisesRegex(
                mapping.H3PromptMappingError,
                "positive finite",
            ):
                mapping.map_h3_prompt_schema(
                    SOURCE,
                    "base",
                    duration_seconds=duration,
                )

    def test_real_planner_carry_maps_later_child_to_base_and_ref_losslessly(self):
        from services.h3_adaptive_execution import bind_h3_execution_segment
        from services.h3_shot_planner import plan_h3_native_shots
        from services.h3_visual_continuity import (
            SAME_SOURCE_VISUAL_CARRY_LINE,
            SEGMENT_SEAM_LOCKS_HEADER,
        )

        dialogue = "<d>[English] Keep this exact carry test.</d>"
        plan = plan_h3_native_shots(
            global_prompt=f"A performer crosses the room. {dialogue}",
            clip_frame_counts=[121, 121],
            fps=24,
            source_canonicalization="t2va_template",
            clip_boundaries=[{
                "continuity_mode": "continuous",
                "type": "continuous",
            }],
        )
        before = copy.deepcopy(plan)
        source = plan["clip_prompts"][1]
        carry_line, seam_line = source.splitlines()[:2]
        self.assertEqual(carry_line, SAME_SOURCE_VISUAL_CARRY_LINE)
        self.assertTrue(seam_line.startswith(f"{SEGMENT_SEAM_LOCKS_HEADER} "))
        original_dialogue = mapping._dialogue_literals(
            "\n".join(plan["clip_prompts"])
        )

        for model, target, references in (
            ("minimax_h3", "base", None),
            ("minimax_h3_ref2va", "ref2va", IDENTITY_REFS),
        ):
            with self.subTest(model=model):
                result = bind_h3_execution_segment(
                    plan,
                    segment_index=1,
                    model_type=model,
                    reference_manifest=references,
                )
                self.assertEqual(plan, before)
                self.assertEqual(result["record"]["source_prompt"], source)
                self.assertEqual(result["record"]["source_sha256"], mapping._digest(source))
                fields = mapping._extract_fields_exact(result["record"]["mapped_prompt"])
                visual = fields[
                    "integrated_multimodal_description"
                    if target == "base" else "detailed_description"
                ]
                self.assertEqual(visual.count(carry_line), 1)
                self.assertEqual(visual.count(seam_line), 1)
                self.assertNotIn(carry_line, fields["subject_definitions"])
                self.assertNotIn(seam_line, fields["subject_definitions"])
                self.assertEqual(
                    mapping._dialogue_literals(
                        "\n".join(result["plan"]["clip_prompts"])
                    ),
                    original_dialogue,
                )
                mapping.validate_mapping_record(
                    result["record"],
                    current_reference_manifest=references,
                    current_target_schema=target,
                    duration_seconds=121 / 24,
                )

    def test_carry_fold_does_not_target_quoted_visual_labels(self):
        from services.h3_visual_continuity import opening_carry_prefix

        quoted_dialogue = (
            "<d>[English] Say audiovisual_description: literally.</d>"
        )
        subject = (
            '<Subject 1> is an adult performer whose quoted note says '
            '"audiovisual_description:".'
        )
        body = (
            f"subject_definitions: {subject}\n\n"
            "integrated_multimodal_description: [Shot 1] "
            "[0.000s-5.000s] shot_name: Performer speaks | "
            "audiovisual_description: <Subject 1> speaks. | "
            f"dialogue_and_vocalizations: {quoted_dialogue}\n\n"
            "overall_soundscape: N/A\n\n"
            "non_diegetic_music: N/A"
        )
        prefixed = f"{opening_carry_prefix()}\n{body}"

        base = mapping.create_mapping_record(
            prefixed, "base", duration_seconds=5, reference_manifest=None,
        )
        ref = mapping.create_mapping_record(
            prefixed, "ref2va", duration_seconds=5,
            reference_manifest=[],
        )
        for record, visual_name in (
            (base, "integrated_multimodal_description"),
            (ref, "detailed_description"),
        ):
            with self.subTest(target=record["target_schema"]):
                self.assertEqual(record["source_prompt"], prefixed)
                self.assertEqual(
                    mapping._dialogue_literals(record["mapped_prompt"]),
                    [quoted_dialogue],
                )
                fields = mapping._extract_fields_exact(record["mapped_prompt"])
                self.assertEqual(fields["subject_definitions"], subject)
                self.assertEqual(fields[visual_name].count("OPENING VISUAL CARRY:"), 1)
                self.assertNotIn("OPENING VISUAL CARRY:", fields["subject_definitions"])

    def test_incomplete_or_noncanonical_carry_wrapper_rejects(self):
        from services.h3_visual_continuity import SAME_SOURCE_VISUAL_CARRY_LINE, opening_carry_prefix

        complete = opening_carry_prefix().split("\n", 1)[1]
        invalid = ["SEGMENT SEAM LOCKS: bogus", "SEGMENT SEAM LOCKS: identity=held",
                   complete.replace("identity=", "unknown=", 1),
                   complete.replace("wardrobe=", "identity=", 1),
                   complete.rsplit("; energy=", 1)[0],
                   complete.rsplit("; energy=", 1)[0] + "; energy="]
        for locks in invalid:
            with self.subTest(locks=locks), self.assertRaises(mapping.H3PromptMappingError):
                mapping.create_mapping_record(
                    f"{SAME_SOURCE_VISUAL_CARRY_LINE}\n{locks}\n{self.canonical_base()}",
                    "base", duration_seconds=5)

        with self.assertRaises(mapping.H3PromptMappingError):
            mapping.create_mapping_record(
                f"{SAME_SOURCE_VISUAL_CARRY_LINE}\n{self.canonical_base()}",
                "base",
                duration_seconds=5,
            )
        with self.assertRaises(mapping.H3PromptMappingError):
            mapping.create_mapping_record(
                f"{SAME_SOURCE_VISUAL_CARRY_LINE}\n"
                "SEGMENT SEAM LOCKS: identity=held\nloose body",
                "base",
                duration_seconds=5,
            )


class H3PromptMappingProvenanceTests(unittest.TestCase):
    def record(self, references=IDENTITY_REFS):
        return mapping.create_mapping_record(
            SOURCE,
            "ref2va",
            duration_seconds=5,
            reference_manifest=references,
        )

    def test_record_round_trip_and_returned_copies_do_not_alias(self):
        refs = copy.deepcopy(IDENTITY_REFS)
        record = self.record(refs)
        refs[0]["role"] = "later mutation"
        self.assertEqual(record["reference_manifest"], IDENTITY_REFS)
        saved = json.loads(json.dumps(record))
        validated = mapping.validate_mapping_record(
            saved,
            current_target_schema="ref2va", current_reference_manifest=IDENTITY_REFS,
            duration_seconds=5,
        )
        validated["reference_manifest"][0]["role"] = "returned mutation"
        self.assertEqual(record["reference_manifest"], IDENTITY_REFS)
        self.assertEqual(mapping.rebuild_mapping_record(record), record)

    def test_base_ref_base_ref_switch_regenerates_from_original_source(self):
        ref = self.record()
        base = mapping.rebuild_mapping_record(ref, target_schema="base")
        self.assertEqual(base["source_prompt"], SOURCE)
        self.assertEqual(base["target_schema"], "base")
        self.assertIsNone(base["reference_manifest"])
        outside_dialogue = mapping._DIALOGUE_RE.sub("", base["mapped_prompt"])
        self.assertNotIn("\nsummary:", outside_dialogue)
        again = mapping.rebuild_mapping_record(
            base,
            target_schema="ref2va",
            reference_manifest=IDENTITY_REFS,
        )
        self.assertEqual(again, ref)
        self.assertEqual(again["mapped_prompt"].count(DIALOGUE), 1)

    def test_empty_binding_can_be_rebound_for_base_origin(self):
        empty = self.record([])
        self.assertEqual(mapping.rebuild_mapping_record(empty), empty)
        rebound = mapping.rebuild_mapping_record(
            empty,
            reference_manifest=IDENTITY_REFS,
        )
        self.assertEqual(rebound["source_prompt"], SOURCE)
        self.assertNotEqual(rebound["binding_sha256"], empty["binding_sha256"])
        self.assertIn("<Picture 1>", rebound["mapped_prompt"])

    def test_ref_rebuild_distinguishes_omitted_from_explicit_none(self):
        record = self.record()
        self.assertEqual(mapping.rebuild_mapping_record(record), record)
        with self.assertRaisesRegex(mapping.H3PromptMappingError, "explicit reference_manifest"):
            mapping.rebuild_mapping_record(record, reference_manifest=None)

    def test_changed_reference_regenerates_without_mutating_old_record(self):
        old = self.record()
        before = copy.deepcopy(old)
        changed = [{
            "type": "image",
            "path": "other.png",
            "role": "the location",
            "image_intent": "scene",
        }]
        new = mapping.rebuild_mapping_record(old, reference_manifest=changed)
        self.assertEqual(old, before)
        self.assertEqual(new["source_prompt"], SOURCE)
        self.assertNotEqual(new["binding_sha256"], old["binding_sha256"])
        self.assertIn("the location", new["mapped_prompt"])
        self.assertNotIn("the lead subject", new["mapped_prompt"])
        self.assertEqual(new["mapped_prompt"].count(DIALOGUE), 1)

    def test_path_changes_binding_even_when_generated_prompt_is_identical(self):
        old = self.record(GENERIC_REFS)
        changed = [{"type": "image", "path": "other.png"}]
        new = mapping.rebuild_mapping_record(old, reference_manifest=changed)
        self.assertEqual(new["mapped_prompt"], old["mapped_prompt"])
        self.assertNotEqual(new["binding_sha256"], old["binding_sha256"])

    def test_current_duration_and_reference_binding_are_required(self):
        record = self.record()
        changed = copy.deepcopy(IDENTITY_REFS)
        changed[0]["role"] = "changed"
        for references, duration in ((changed, 5), (IDENTITY_REFS, 6)):
            with self.subTest(references=references, duration=duration), self.assertRaisesRegex(
                mapping.H3PromptMappingError,
                "current references or duration",
            ):
                mapping.validate_mapping_record(
                    record,
                    current_target_schema="ref2va", current_reference_manifest=references,
                    duration_seconds=duration,
                )

    def test_rehashed_output_and_source_tampering_cannot_bypass_reproduction(self):
        output_tamper = self.record()
        output_tamper["mapped_prompt"] += "\nAdditional text."
        output_tamper["mapped_sha256"] = mapping._digest(output_tamper["mapped_prompt"])
        source_tamper = self.record()
        source_tamper["source_prompt"] = "Different authored source"
        source_tamper["source_sha256"] = mapping._digest(source_tamper["source_prompt"])
        for record in (output_tamper, source_tamper):
            with self.subTest(record=record["source_prompt"]), self.assertRaises(
                mapping.H3PromptMappingError
            ):
                mapping.validate_mapping_record(
                    record,
                    current_target_schema="ref2va", current_reference_manifest=IDENTITY_REFS,
                    duration_seconds=5,
                )

    def test_rehashed_binding_change_cannot_keep_stale_generated_output(self):
        record = self.record()
        record["reference_manifest"][0]["role"] = "changed"
        binding = {
            key: record[key]
            for key in ("target_schema", "duration_seconds", "reference_manifest")
        }
        record["binding_sha256"] = mapping._digest(mapping._json(binding))
        with self.assertRaises(mapping.H3PromptMappingError):
            mapping.validate_mapping_record(
                record,
                current_target_schema="ref2va", current_reference_manifest=record["reference_manifest"],
                duration_seconds=5,
            )

    def test_bare_ref_source_rejects_but_explicit_authoring_ingress_is_retained(self):
        generated = self.record()["mapped_prompt"]
        with self.assertRaisesRegex(mapping.H3PromptMappingError, "no verified mapping lineage"):
            mapping.create_mapping_record(
                generated,
                "ref2va",
                duration_seconds=5,
                reference_manifest=IDENTITY_REFS,
            )
        authored = mapping.create_authored_mapping_record(
            generated,
            "ref2va",
            duration_seconds=5,
            reference_manifest=IDENTITY_REFS,
        )
        self.assertEqual(authored["mapped_prompt"], generated)
        self.assertEqual(mapping.rebuild_mapping_record(authored), authored)
        changed = copy.deepcopy(IDENTITY_REFS)
        changed[0]["path"] = "different.png"
        with self.assertRaisesRegex(
            mapping.H3PromptRepresentabilityError,
            "source ownership",
        ):
            mapping.rebuild_mapping_record(
                authored,
                reference_manifest=changed,
            )
        with self.assertRaisesRegex(
            mapping.H3PromptRepresentabilityError,
            "no exact Base field owner",
        ):
            mapping.rebuild_mapping_record(authored, target_schema="base")

    def test_record_shape_version_recipe_and_origin_are_strict(self):
        mutations = (
            lambda value: value.update(version=True),
            lambda value: value.update(version=2),
            lambda value: value.update(recipe_version="future"),
            lambda value: value.update(source_origin="invented"),
            lambda value: value.update(source_origin=[]),
            lambda value: value.pop("source_sha256"),
            lambda value: value.update(extra=True),
        )
        for mutate in mutations:
            record = self.record()
            mutate(record)
            with self.subTest(record=record), self.assertRaises(
                mapping.H3PromptMappingError
            ):
                mapping.validate_mapping_record(
                    record,
                    current_target_schema="ref2va", current_reference_manifest=IDENTITY_REFS,
                    duration_seconds=5,
                )

    def test_current_target_schema_is_required_even_with_empty_references(self):
        base = mapping.create_mapping_record(SOURCE, "base", duration_seconds=5)
        ref = mapping.create_mapping_record(SOURCE, "ref2va", duration_seconds=5, reference_manifest=[])
        for record, target in ((base, "ref2va"), (ref, "base")):
            with self.subTest(target=target), self.assertRaisesRegex(mapping.H3PromptMappingError, "current target schema"):
                mapping.validate_mapping_record(record, current_target_schema=target,
                                                current_reference_manifest=[], duration_seconds=5)

    def test_base_record_rejects_nonempty_current_reference_binding(self):
        record = mapping.create_mapping_record(
            SOURCE,
            "base",
            duration_seconds=5,
        )
        with self.assertRaisesRegex(mapping.H3PromptMappingError, "cannot bind semantic"):
            mapping.validate_mapping_record(
                record,
                current_target_schema="base", current_reference_manifest=IDENTITY_REFS,
                duration_seconds=5,
            )


if __name__ == "__main__":
    unittest.main()
