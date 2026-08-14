from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.sample_campaign import (  # noqa: E402
    ArmOutputEvidence,
    ArmManifest,
    CampaignArm,
    ComparativePairManifest,
    EvidenceClass,
    EvaluationFrameSelection,
    EvaluationReceipt,
    HumanReviewEvidence,
    ImmutableSettings,
    InterventionDelta,
    ReviewVerdict,
    SampleCampaignError,
    VlmReviewEvidence,
    build_arm_manifest,
    build_pair_manifest,
    frame_selection_digest,
    human_evidence_digest,
    normalized_input_digest,
    normalized_prompt_digest,
    pair_manifest_digest,
    public_pair_projection,
    review_input_digest,
    select_evaluation_frames,
    validate_evaluation_transition,
    validate_frame_selection,
    validate_pair_manifest,
    vlm_evidence_digest,
)


INPUT_A = "a" * 64
INPUT_B = "b" * 64
PRIVATE_PROMPT = "PRIVATE_PROMPT_SENTINEL: a dancer crosses the amber room"
PRIVATE_MAESTRO_PATH = "/private/maestro/reference.png"
PRIVATE_CONTROL_PATH = "/private/control/reference.png"


def make_arm(
    arm: CampaignArm,
    *,
    raw_prompt: str = PRIVATE_PROMPT,
    fingerprint: str = INPUT_A,
    model_revision: str = "model-revision-001",
    settings: dict | None = None,
    seed: int = 42,
    output_index: int = 0,
    interventions: tuple[str, ...] | None = None,
) -> ArmManifest:
    if interventions is None:
        interventions = (
            ("maestro.temporal_guidance", "maestro.workflow_lock")
            if arm is CampaignArm.MAESTRO
            else ()
        )
    path = PRIVATE_MAESTRO_PATH if arm is CampaignArm.MAESTRO else PRIVATE_CONTROL_PATH
    return build_arm_manifest(
        arm=arm,
        raw_prompt=raw_prompt,
        private_input_paths=(path,),
        input_fingerprints=(fingerprint,),
        model_revision=model_revision,
        settings=settings or {
            "steps": 20,
            "resolution": {"width": 1280, "height": 720},
            "fps": 24,
            "sampler": "euler",
            "nested": {"schedule": [1, 2, 3]},
            "private_note": "PRIVATE_SETTING_SENTINEL",
        },
        seed=seed,
        output_index=output_index,
        interventions=interventions,
    )


def make_pair() -> ComparativePairManifest:
    return build_pair_manifest(
        pair_id="motion-study-001",
        case_id="temporal-coherence",
        maestro=make_arm(CampaignArm.MAESTRO),
        control=make_arm(
            CampaignArm.CONTROL,
            raw_prompt=f"  {PRIVATE_PROMPT}\n",
        ),
        intervention_delta=InterventionDelta(
            maestro_only=("maestro.temporal_guidance", "maestro.workflow_lock"),
        ),
    )


def make_receipts() -> tuple[
    ComparativePairManifest,
    EvaluationReceipt,
    EvaluationReceipt,
    EvaluationReceipt,
]:
    pair = make_pair()
    selection = select_evaluation_frames(
        maestro_frame_count=121,
        control_frame_count=81,
    )
    maestro_output = ArmOutputEvidence(
        CampaignArm.MAESTRO,
        "private-maestro-output",
        "c" * 64,
        ("d" * 64, "e" * 64, "f" * 64),
    )
    control_output = ArmOutputEvidence(
        CampaignArm.CONTROL,
        "private-control-output",
        "1" * 64,
        ("2" * 64, "3" * 64, "4" * 64),
    )
    manifest_sha = pair_manifest_digest(pair)
    generated = EvaluationReceipt(
        manifest_sha,
        selection,
        maestro_output,
        control_output,
    )
    review_sha = review_input_digest(
        manifest_digest=manifest_sha,
        selection=selection,
        maestro_output=maestro_output,
        control_output=control_output,
    )
    vlm_values = {
        "private_report_ref": "private-vlm-report",
        "reviewer_revision": "vlm-revision-1",
        "request_sha256": "5" * 64,
        "report_sha256": "6" * 64,
        "review_input_sha256": review_sha,
        "verdict": ReviewVerdict.MAESTRO_PREFERRED,
    }
    vlm_evidence = VlmReviewEvidence(
        **vlm_values,
        evidence_sha256=vlm_evidence_digest(**vlm_values),
    )
    vlm = replace(generated, vlm_review=vlm_evidence)
    human = replace(
        vlm,
        human_review=HumanReviewEvidence(
            decision_id="decision-1",
            reviewer_ref="owner-reviewer",
            decision_sha256="7" * 64,
            vlm_report_sha256=vlm_evidence.report_sha256,
            verdict=ReviewVerdict.MAESTRO_PREFERRED,
            evidence_sha256=human_evidence_digest(
                decision_id="decision-1",
                reviewer_ref="owner-reviewer",
                decision_sha256="7" * 64,
                vlm_report_sha256=vlm_evidence.report_sha256,
                verdict=ReviewVerdict.MAESTRO_PREFERRED,
            ),
        ),
    )
    return pair, generated, vlm, human


class SampleCampaignManifestTests(unittest.TestCase):
    def test_prompt_digest_normalizes_unicode_and_whitespace_but_not_case(self):
        composed = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}   motion\n study"
        decomposed = "  Cafe\N{COMBINING ACUTE ACCENT} motion study  "
        self.assertEqual(
            normalized_prompt_digest(composed),
            normalized_prompt_digest(decomposed),
        )
        self.assertNotEqual(
            normalized_prompt_digest(composed),
            normalized_prompt_digest(composed.upper()),
        )

    def test_input_digest_is_path_independent_ordered_and_normalized(self):
        self.assertEqual(
            normalized_input_digest((INPUT_A.upper(), INPUT_B)),
            normalized_input_digest((INPUT_A, INPUT_B)),
        )
        self.assertNotEqual(
            normalized_input_digest((INPUT_A, INPUT_B)),
            normalized_input_digest((INPUT_B, INPUT_A)),
        )
        with self.assertRaises(SampleCampaignError):
            normalized_input_digest(("not-a-content-fingerprint",))

    def test_private_pair_is_deeply_immutable_and_settings_are_detached(self):
        source_settings = {
            "steps": 20,
            "resolution": {"width": 1280, "height": 720},
            "fps": 24,
            "nested": {"schedule": [1, 2, 3]},
        }
        arm = make_arm(CampaignArm.MAESTRO, settings=source_settings)
        before = arm.settings.canonical_json
        source_settings["nested"]["schedule"].append(4)
        detached = arm.settings.to_dict()
        detached["nested"]["schedule"].append(5)

        self.assertEqual(arm.settings.canonical_json, before)
        self.assertEqual(arm.settings.to_dict()["nested"]["schedule"], [1, 2, 3])
        with self.assertRaises(FrozenInstanceError):
            arm.seed = 99  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            arm.settings.canonical_json = "{}"  # type: ignore[misc]

    def test_pair_accepts_private_paths_that_differ_when_content_fingerprints_match(self):
        pair = make_pair()
        self.assertNotEqual(
            pair.maestro.private_input_paths,
            pair.control.private_input_paths,
        )
        self.assertEqual(pair.maestro.input_digest, pair.control.input_digest)
        self.assertEqual(pair.maestro.settings, pair.control.settings)
        validate_pair_manifest(pair)

    def test_pair_rejects_every_uncontrolled_generation_difference(self):
        maestro = make_arm(CampaignArm.MAESTRO)
        base_control = make_arm(CampaignArm.CONTROL)
        mismatches = {
            "prompt_digest": make_arm(CampaignArm.CONTROL, raw_prompt="different prompt"),
            "input_digest": make_arm(CampaignArm.CONTROL, fingerprint=INPUT_B),
            "model_revision": make_arm(CampaignArm.CONTROL, model_revision="model-revision-002"),
            "settings": make_arm(
                CampaignArm.CONTROL,
                settings={
                    "steps": 21,
                    "resolution": {"width": 1280, "height": 720},
                    "fps": 24,
                },
            ),
            "seed": make_arm(CampaignArm.CONTROL, seed=43),
            "output_index": make_arm(CampaignArm.CONTROL, output_index=1),
        }
        for expected_field, control in mismatches.items():
            with self.subTest(expected_field=expected_field):
                with self.assertRaisesRegex(SampleCampaignError, expected_field):
                    build_pair_manifest(
                        pair_id="locked-pair",
                        case_id="locked-case",
                        maestro=maestro,
                        control=control,
                        intervention_delta=InterventionDelta(
                            maestro_only=(
                                "maestro.temporal_guidance",
                                "maestro.workflow_lock",
                            ),
                        ),
                    )
        self.assertEqual(base_control.arm, CampaignArm.CONTROL)

    def test_pair_requires_correct_roles_and_exact_nonempty_intervention_delta(self):
        maestro = make_arm(CampaignArm.MAESTRO)
        control = make_arm(CampaignArm.CONTROL)
        correct = InterventionDelta(
            maestro_only=("maestro.temporal_guidance", "maestro.workflow_lock"),
        )
        with self.assertRaisesRegex(SampleCampaignError, "Maestro arm"):
            ComparativePairManifest(
                pair_id="pair",
                case_id="case",
                maestro=control,
                control=control,
                intervention_delta=correct,
            )
        with self.assertRaisesRegex(SampleCampaignError, "explicit intervention"):
            InterventionDelta((), ())
        with self.assertRaisesRegex(SampleCampaignError, "identifier"):
            build_arm_manifest(
                arm=CampaignArm.CONTROL,
                raw_prompt=PRIVATE_PROMPT,
                private_input_paths=(PRIVATE_CONTROL_PATH,),
                input_fingerprints=(INPUT_A,),
                model_revision="model-revision-001",
                settings={
                    "steps": 20,
                    "resolution": {"width": 1280, "height": 720},
                    "fps": 24,
                },
                seed=42,
                output_index=0,
                interventions=("valid", 3),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(SampleCampaignError, "does not match"):
            build_pair_manifest(
                pair_id="pair",
                case_id="case",
                maestro=maestro,
                control=control,
                intervention_delta=InterventionDelta(
                    maestro_only=("maestro.unrelated_tweak",),
                ),
            )

    def test_pair_supports_an_explicit_control_only_intervention(self):
        pair = build_pair_manifest(
            pair_id="control-intervention-pair",
            case_id="control-intervention-case",
            maestro=make_arm(
                CampaignArm.MAESTRO,
                interventions=("shared.base",),
            ),
            control=make_arm(
                CampaignArm.CONTROL,
                interventions=("control.raw_path", "shared.base"),
            ),
            intervention_delta=InterventionDelta(
                maestro_only=(),
                control_only=("control.raw_path",),
            ),
        )
        self.assertEqual(
            pair.intervention_delta.public_projection()["control_only"],
            ["control.raw_path"],
        )

    def test_arm_constructor_rechecks_private_manifest_integrity(self):
        arm = make_arm(CampaignArm.MAESTRO)
        with self.assertRaisesRegex(SampleCampaignError, "prompt digest"):
            replace(arm, prompt_digest="0" * 64)
        with self.assertRaisesRegex(SampleCampaignError, "input digest"):
            replace(arm, input_digest="0" * 64)
        with self.assertRaisesRegex(SampleCampaignError, "align"):
            replace(arm, private_input_paths=())
        with self.assertRaisesRegex(SampleCampaignError, "immutable"):
            ArmManifest(
                arm=arm.arm,
                raw_prompt=arm.raw_prompt,
                prompt_digest=arm.prompt_digest,
                private_input_paths=arm.private_input_paths,
                input_fingerprints=arm.input_fingerprints,
                input_digest=arm.input_digest,
                model_revision=arm.model_revision,
                settings=ImmutableSettings.from_mapping({
                    "steps": 20,
                    "resolution": {"width": 1280, "height": 720},
                    "fps": 24,
                }).to_dict(),  # type: ignore[arg-type]
                seed=arm.seed,
                output_index=arm.output_index,
                interventions=arm.interventions,
            )

    def test_public_projection_is_content_free_and_omits_all_private_digests(self):
        pair = make_pair()
        projection = public_pair_projection(pair)
        rendered = json.dumps(projection, sort_keys=True)

        for private in (
            PRIVATE_PROMPT,
            PRIVATE_MAESTRO_PATH,
            PRIVATE_CONTROL_PATH,
            INPUT_A,
            pair.maestro.prompt_digest,
            pair.maestro.input_digest,
            pair.maestro.settings.digest,
            "PRIVATE_SETTING_SENTINEL",
        ):
            self.assertNotIn(private, rendered)
        self.assertNotIn("raw_prompt", rendered)
        self.assertNotIn("private_input_paths", rendered)
        self.assertNotIn("fingerprint", rendered)
        self.assertNotIn("digest", rendered)
        self.assertEqual(
            projection["intervention_delta"]["maestro_only"],
            ["maestro.temporal_guidance", "maestro.workflow_lock"],
        )
        self.assertTrue(projection["shared_generation"]["same_settings"])

    def test_settings_reject_noncanonical_or_mutable_payload_shapes(self):
        with self.assertRaises(SampleCampaignError):
            ImmutableSettings(
                '{"b":1,"fps":24,"resolution":{"height":720,"width":1280},"steps":20,"a":2}'
            )
        with self.assertRaises(SampleCampaignError):
            ImmutableSettings.from_mapping({
                "steps": 20,
                "resolution": {"width": 1280, "height": 720},
                "fps": 24,
                "bad": float("nan"),
            })
        with self.assertRaises(SampleCampaignError):
            ImmutableSettings.from_mapping({
                "steps": 20,
                "resolution": {"width": 1280, "height": 720},
                "fps": 24,
                "bad": {1, 2},
            })

    def test_settings_require_reproducible_steps_resolution_and_fps(self):
        valid = {
            "steps": 20,
            "resolution": {"width": 1280, "height": 720},
            "fps": 24,
        }
        self.assertEqual(ImmutableSettings.from_mapping(valid).to_dict(), valid)
        invalid = (
            {"resolution": valid["resolution"], "fps": 24},
            {"steps": 20, "fps": 24},
            {"steps": 20, "resolution": valid["resolution"]},
            {**valid, "steps": True},
            {**valid, "steps": 0},
            {**valid, "steps": 100_001},
            {**valid, "resolution": {"width": 1280}},
            {**valid, "resolution": {"width": 1280, "height": 0}},
            {**valid, "resolution": {"width": 65_537, "height": 720}},
            {**valid, "fps": True},
            {**valid, "fps": float("nan")},
            {**valid, "fps": 0},
            {**valid, "fps": 10**1000},
        )
        for settings in invalid:
            with self.subTest(settings=settings):
                with self.assertRaises(SampleCampaignError):
                    ImmutableSettings.from_mapping(settings)

    def test_public_identifiers_reject_posix_and_windows_path_shapes(self):
        for revision in ("/private/model/revision", "C:/private/model/revision"):
            with self.subTest(revision=revision):
                with self.assertRaisesRegex(SampleCampaignError, "model revision"):
                    make_arm(CampaignArm.MAESTRO, model_revision=revision)


class SampleCampaignFrameSelectionTests(unittest.TestCase):
    def test_selector_is_deterministic_for_two_to_five_frames_and_unequal_arms(self):
        for sample_count in range(2, 6):
            with self.subTest(sample_count=sample_count):
                first = select_evaluation_frames(
                    maestro_frame_count=121,
                    control_frame_count=81,
                    sample_count=sample_count,
                )
                second = select_evaluation_frames(
                    maestro_frame_count=121,
                    control_frame_count=81,
                    sample_count=sample_count,
                )
                self.assertEqual(first, second)
                self.assertEqual(len(first.normalized_positions), sample_count)
                self.assertTrue(all(
                    right - left >= 2
                    for indices in (first.maestro_indices, first.control_indices)
                    for left, right in zip(indices, indices[1:])
                ))
                self.assertLessEqual(
                    first.normalized_positions[-1] - first.normalized_positions[0],
                    Fraction(1, 4),
                )
                for position, maestro_index, control_index in zip(
                    first.normalized_positions,
                    first.maestro_indices,
                    first.control_indices,
                ):
                    self.assertLessEqual(
                        abs(Fraction(maestro_index, 120) - position),
                        Fraction(1, 240),
                    )
                    self.assertLessEqual(
                        abs(Fraction(control_index, 80) - position),
                        Fraction(1, 160),
                    )

    def test_selector_rejects_invalid_sample_counts_and_clips_too_short(self):
        for sample_count in (1, 6, True):
            with self.subTest(sample_count=sample_count):
                with self.assertRaises(SampleCampaignError):
                    select_evaluation_frames(
                        maestro_frame_count=121,
                        control_frame_count=81,
                        sample_count=sample_count,
                    )
        with self.assertRaisesRegex(SampleCampaignError, "too short"):
            select_evaluation_frames(
                maestro_frame_count=9,
                control_frame_count=12,
                sample_count=5,
            )

    def test_validation_rejects_far_spread_random_and_adjacent_only_sets(self):
        with self.assertRaisesRegex(SampleCampaignError, "far spread"):
            EvaluationFrameSelection(
                maestro_frame_count=101,
                control_frame_count=81,
                normalized_positions=(Fraction(1, 10), Fraction(9, 10)),
                maestro_indices=(10, 90),
                control_indices=(8, 72),
            )

        with self.assertRaisesRegex(SampleCampaignError, "not deterministic"):
            EvaluationFrameSelection(
                maestro_frame_count=101,
                control_frame_count=81,
                normalized_positions=(
                    Fraction(2, 5),
                    Fraction(9, 20),
                    Fraction(1, 2),
                ),
                maestro_indices=(40, 45, 50),
                control_indices=(32, 36, 40),
            )

        valid = select_evaluation_frames(
            maestro_frame_count=101,
            control_frame_count=81,
            sample_count=3,
        )
        with self.assertRaisesRegex(SampleCampaignError, "non-adjacent"):
            replace(valid, maestro_indices=(45, 46, 47))

    def test_validation_rejects_unordered_or_mismatched_arm_geometry(self):
        valid = select_evaluation_frames(
            maestro_frame_count=101,
            control_frame_count=81,
            sample_count=3,
        )
        with self.assertRaisesRegex(SampleCampaignError, "sequential"):
            replace(valid, normalized_positions=tuple(reversed(valid.normalized_positions)))
        with self.assertRaisesRegex(SampleCampaignError, "same position count"):
            replace(valid, control_indices=valid.control_indices[:-1])
        with self.assertRaisesRegex(SampleCampaignError, "do not match"):
            replace(
                valid,
                control_indices=(
                    valid.control_indices[0],
                    valid.control_indices[1] + 1,
                    valid.control_indices[2],
                ),
            )
        validate_frame_selection(valid)


class SampleCampaignEvaluationReceiptTests(unittest.TestCase):
    def test_receipt_derives_monotonic_evidence_without_acceptance_claims(self):
        pair, generated, vlm, human = make_receipts()
        cases = (
            (None, EvidenceClass.MANIFEST_ONLY),
            (generated, EvidenceClass.GENERATED_OUTPUTS),
            (vlm, EvidenceClass.VLM_REVIEWED),
            (human, EvidenceClass.HUMAN_REVIEWED),
        )
        for receipt, expected in cases:
            with self.subTest(expected=expected):
                projection = public_pair_projection(pair, receipt=receipt)
                rendered = json.dumps(projection, sort_keys=True)
                self.assertNotIn("accepted", rendered)
                self.assertNotIn("acceptance", rendered)
                self.assertEqual(
                    projection["evaluation"]["evidence_class"],
                    expected.value,
                )
                if receipt is None:
                    self.assertNotIn("frame_selection", projection)
                else:
                    self.assertEqual(projection["frame_selection"]["sample_count"], 3)

    def test_receipt_requires_exact_pair_output_frame_and_report_bindings(self):
        pair, generated, vlm, _human = make_receipts()
        with self.assertRaisesRegex(SampleCampaignError, "lowercase"):
            replace(generated.maestro_output, output_sha256="A" * 64)
        with self.assertRaisesRegex(SampleCampaignError, "align"):
            replace(
                generated,
                maestro_output=replace(
                    generated.maestro_output,
                    selected_frame_sha256s=("d" * 64, "e" * 64),
                ),
            )
        with self.assertRaisesRegex(SampleCampaignError, "different review inputs"):
            rebound_values = {
                "private_report_ref": vlm.vlm_review.private_report_ref,
                "reviewer_revision": vlm.vlm_review.reviewer_revision,
                "request_sha256": vlm.vlm_review.request_sha256,
                "report_sha256": vlm.vlm_review.report_sha256,
                "review_input_sha256": "8" * 64,
                "verdict": vlm.vlm_review.verdict,
            }
            replace(
                generated,
                vlm_review=VlmReviewEvidence(
                    **rebound_values,
                    evidence_sha256=vlm_evidence_digest(**rebound_values),
                ),
            )
        with self.assertRaisesRegex(SampleCampaignError, "different pair manifest"):
            public_pair_projection(
                pair,
                receipt=replace(generated, pair_manifest_sha256="9" * 64),
            )
        other_selection = select_evaluation_frames(
            maestro_frame_count=121,
            control_frame_count=81,
            sample_count=4,
        )
        self.assertNotEqual(
            frame_selection_digest(generated.frame_selection),
            frame_selection_digest(other_selection),
        )
        reordered = replace(
            generated.maestro_output,
            selected_frame_sha256s=tuple(
                reversed(generated.maestro_output.selected_frame_sha256s)
            ),
        )
        self.assertNotEqual(
            review_input_digest(
                manifest_digest=generated.pair_manifest_sha256,
                selection=generated.frame_selection,
                maestro_output=generated.maestro_output,
                control_output=generated.control_output,
            ),
            review_input_digest(
                manifest_digest=generated.pair_manifest_sha256,
                selection=generated.frame_selection,
                maestro_output=reordered,
                control_output=generated.control_output,
            ),
        )

    def test_human_evidence_requires_and_binds_completed_vlm_report(self):
        _pair, generated, vlm, human = make_receipts()
        with self.assertRaisesRegex(SampleCampaignError, "requires completed VLM"):
            replace(generated, human_review=human.human_review)
        with self.assertRaisesRegex(SampleCampaignError, "different VLM report"):
            rebound_values = {
                "decision_id": human.human_review.decision_id,
                "reviewer_ref": human.human_review.reviewer_ref,
                "decision_sha256": human.human_review.decision_sha256,
                "vlm_report_sha256": "a" * 64,
                "verdict": human.human_review.verdict,
            }
            replace(
                vlm,
                human_review=HumanReviewEvidence(
                    **rebound_values,
                    evidence_sha256=human_evidence_digest(**rebound_values),
                ),
            )

    def test_evidence_transition_rejects_downgrade_rebinding_and_replacement(self):
        _pair, generated, vlm, human = make_receipts()
        validate_evaluation_transition(generated, vlm)
        validate_evaluation_transition(vlm, human)
        with self.assertRaisesRegex(SampleCampaignError, "downgraded"):
            validate_evaluation_transition(vlm, generated)
        with self.assertRaisesRegex(SampleCampaignError, "rebound"):
            validate_evaluation_transition(
                generated,
                replace(generated, pair_manifest_sha256="b" * 64),
            )
        with self.assertRaisesRegex(SampleCampaignError, "cannot be replaced"):
            replacement_values = {
                "private_report_ref": vlm.vlm_review.private_report_ref,
                "reviewer_revision": vlm.vlm_review.reviewer_revision,
                "request_sha256": "a" * 64,
                "report_sha256": vlm.vlm_review.report_sha256,
                "review_input_sha256": vlm.vlm_review.review_input_sha256,
                "verdict": vlm.vlm_review.verdict,
            }
            validate_evaluation_transition(
                vlm,
                replace(
                    vlm,
                    vlm_review=VlmReviewEvidence(
                        **replacement_values,
                        evidence_sha256=vlm_evidence_digest(**replacement_values),
                    ),
                ),
            )

    def test_review_verdict_and_provenance_are_bound_to_evidence_digests(self):
        _pair, _generated, vlm, human = make_receipts()
        with self.assertRaisesRegex(SampleCampaignError, "digest does not match"):
            replace(vlm.vlm_review, verdict=ReviewVerdict.CONTROL_PREFERRED)
        with self.assertRaisesRegex(SampleCampaignError, "digest does not match"):
            replace(vlm.vlm_review, reviewer_revision="vlm-revision-2")
        with self.assertRaisesRegex(SampleCampaignError, "digest does not match"):
            replace(human.human_review, verdict=ReviewVerdict.CONTROL_PREFERRED)
        with self.assertRaisesRegex(SampleCampaignError, "digest does not match"):
            replace(human.human_review, reviewer_ref="different-owner")
        with self.assertRaisesRegex(SampleCampaignError, "final verdict"):
            replace(
                vlm.vlm_review,
                verdict=ReviewVerdict.MAESTRO_PREFERRED.value,  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(SampleCampaignError, "final verdict"):
            replace(
                human.human_review,
                verdict=ReviewVerdict.MAESTRO_PREFERRED.value,  # type: ignore[arg-type]
            )

    def test_public_projection_omits_private_receipt_provenance(self):
        pair, _generated, _vlm, human = make_receipts()
        rendered = json.dumps(public_pair_projection(pair, receipt=human), sort_keys=True)
        for private in (
            human.pair_manifest_sha256,
            human.maestro_output.private_output_ref,
            human.maestro_output.output_sha256,
            human.vlm_review.private_report_ref,
            human.vlm_review.report_sha256,
            human.human_review.decision_id,
            human.human_review.decision_sha256,
        ):
            self.assertNotIn(private, rendered)
        self.assertNotIn("sha256", rendered)
        self.assertNotIn("private", rendered)

if __name__ == "__main__":
    unittest.main()
