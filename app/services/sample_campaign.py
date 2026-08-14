"""Pure, GPU-free contracts for comparative sample-generation campaigns.

Private manifests retain the prompt and local input paths needed by a later
executor.  Public projections deliberately expose only comparison geometry and
review state.  This module does not queue work, read media, invoke a model, or
claim that a generated pair has been accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA_VERSION = 1
MIN_REVIEW_FRAMES = 2
MAX_REVIEW_FRAMES = 5
MAX_NORMALIZED_FRAME_SPAN = Fraction(1, 4)
MAX_SEED = 2**64 - 1
MAX_GENERATION_STEPS = 100_000
MAX_GENERATION_DIMENSION = 65_536
MAX_GENERATION_FPS = 1_000

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+@~-]{0,255}$")
_INTERVENTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+~-]{0,127}$")
_MAX_SETTINGS_BYTES = 32 * 1024


class SampleCampaignError(ValueError):
    """A comparative campaign contract is incomplete or inconsistent."""


class CampaignArm(str, Enum):
    MAESTRO = "maestro"
    CONTROL = "control"


class EvidenceClass(str, Enum):
    """Highest evidence actually represented by an evaluation state."""

    MANIFEST_ONLY = "manifest_only"
    GENERATED_OUTPUTS = "generated_outputs"
    VLM_REVIEWED = "vlm_reviewed"
    HUMAN_REVIEWED = "human_reviewed"


class ReviewVerdict(str, Enum):
    """Comparative observations only; none of these is an acceptance verdict."""

    NOT_REVIEWED = "not_reviewed"
    PENDING = "pending"
    MAESTRO_PREFERRED = "maestro_preferred"
    CONTROL_PREFERRED = "control_preferred"
    NO_MATERIAL_DIFFERENCE = "no_material_difference"
    INCONCLUSIVE = "inconclusive"


_FINAL_VERDICTS = frozenset({
    ReviewVerdict.MAESTRO_PREFERRED,
    ReviewVerdict.CONTROL_PREFERRED,
    ReviewVerdict.NO_MATERIAL_DIFFERENCE,
    ReviewVerdict.INCONCLUSIVE,
})


def _bounded_id(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise SampleCampaignError(f"Campaign {field} is invalid.")
    return value


def _canonical_json(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise SampleCampaignError("Campaign settings must be a mapping.")

    def normalize(item: Any, *, depth: int = 0) -> Any:
        if depth > 12:
            raise SampleCampaignError("Campaign settings are too deeply nested.")
        if item is None or isinstance(item, (str, bool)):
            return item
        if type(item) is int:
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise SampleCampaignError("Campaign settings contain a non-finite number.")
            return item
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise SampleCampaignError("Campaign setting names must be strings.")
            return {
                key: normalize(item[key], depth=depth + 1)
                for key in sorted(item)
            }
        if isinstance(item, (list, tuple)):
            return [normalize(child, depth=depth + 1) for child in item]
        raise SampleCampaignError("Campaign settings must contain only JSON values.")

    encoded = json.dumps(
        normalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > _MAX_SETTINGS_BYTES:
        raise SampleCampaignError("Campaign settings are too large.")
    return encoded


def _validate_generation_settings(value: Mapping[str, Any]) -> None:
    """Require the dimensions that make a matched generation reproducible."""

    steps = value.get("steps")
    resolution = value.get("resolution")
    fps = value.get("fps")
    if type(steps) is not int or not 0 < steps <= MAX_GENERATION_STEPS:
        raise SampleCampaignError("Campaign settings require positive steps.")
    if (
        not isinstance(resolution, Mapping)
        or set(resolution) != {"width", "height"}
        or any(
            type(resolution.get(field)) is not int
            or not 0 < resolution[field] <= MAX_GENERATION_DIMENSION
            for field in ("width", "height")
        )
    ):
        raise SampleCampaignError(
            "Campaign settings require a positive width and height.",
        )
    if (
        type(fps) not in (int, float)
        or (type(fps) is float and not math.isfinite(fps))
        or not 0 < fps <= MAX_GENERATION_FPS
    ):
        raise SampleCampaignError("Campaign settings require a positive FPS.")


@dataclass(frozen=True, slots=True)
class ImmutableSettings:
    """Canonical, immutable JSON settings retained only in the private manifest."""

    canonical_json: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_json, str):
            raise SampleCampaignError("Campaign settings are invalid.")
        try:
            decoded = json.loads(self.canonical_json)
        except (TypeError, ValueError):
            raise SampleCampaignError("Campaign settings are invalid.") from None
        if not isinstance(decoded, dict) or _canonical_json(decoded) != self.canonical_json:
            raise SampleCampaignError("Campaign settings are not canonical.")
        _validate_generation_settings(decoded)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ImmutableSettings":
        return cls(_canonical_json(value))

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            b"maestro-sample-settings-v1\0" + self.canonical_json.encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Return a detached mutable copy for a private executor."""

        return json.loads(self.canonical_json)


def _normalize_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise SampleCampaignError("Campaign prompt is invalid.")
    normalized = " ".join(unicodedata.normalize("NFC", prompt).split())
    if not normalized:
        raise SampleCampaignError("Campaign prompt is empty.")
    return normalized


def normalized_prompt_digest(prompt: str) -> str:
    return hashlib.sha256(
        b"maestro-sample-prompt-v1\0" + _normalize_prompt(prompt).encode("utf-8")
    ).hexdigest()


def _normalize_fingerprints(fingerprints: Sequence[str]) -> tuple[str, ...]:
    if isinstance(fingerprints, (str, bytes)) or not isinstance(fingerprints, Sequence):
        raise SampleCampaignError("Campaign input fingerprints are invalid.")
    normalized: list[str] = []
    for value in fingerprints:
        if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value.lower()):
            raise SampleCampaignError("Campaign input fingerprint is invalid.")
        normalized.append(value.lower())
    return tuple(normalized)


def normalized_input_digest(fingerprints: Sequence[str]) -> str:
    """Hash ordered, path-independent input content fingerprints."""

    normalized = _normalize_fingerprints(fingerprints)
    encoded = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(
        b"maestro-sample-inputs-v1\0" + encoded.encode("ascii")
    ).hexdigest()


def _normalize_interventions(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise SampleCampaignError("Campaign interventions are invalid.")
    source = tuple(values)
    if any(
        not isinstance(value, str) or not _INTERVENTION_RE.fullmatch(value)
        for value in source
    ):
        raise SampleCampaignError("Campaign intervention identifier is invalid.")
    result = tuple(sorted(source))
    if len(set(result)) != len(result):
        raise SampleCampaignError("Campaign interventions must be unique.")
    return result


@dataclass(frozen=True, slots=True)
class ArmManifest:
    """One immutable private arm of a locked comparative generation pair."""

    arm: CampaignArm
    raw_prompt: str
    prompt_digest: str
    private_input_paths: tuple[str, ...]
    input_fingerprints: tuple[str, ...]
    input_digest: str
    model_revision: str
    settings: ImmutableSettings
    seed: int
    output_index: int
    interventions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.arm, CampaignArm):
            raise SampleCampaignError("Campaign arm is invalid.")
        if self.prompt_digest != normalized_prompt_digest(self.raw_prompt):
            raise SampleCampaignError("Campaign prompt digest does not match its prompt.")
        if not isinstance(self.private_input_paths, tuple) or any(
            not isinstance(path, str) or not path for path in self.private_input_paths
        ):
            raise SampleCampaignError("Campaign private input paths are invalid.")
        if not isinstance(self.input_fingerprints, tuple):
            raise SampleCampaignError("Campaign input fingerprints must be immutable.")
        normalized_fingerprints = _normalize_fingerprints(self.input_fingerprints)
        if normalized_fingerprints != self.input_fingerprints:
            raise SampleCampaignError("Campaign input fingerprints are not normalized.")
        if len(self.private_input_paths) != len(self.input_fingerprints):
            raise SampleCampaignError("Campaign input paths and fingerprints do not align.")
        if self.input_digest != normalized_input_digest(self.input_fingerprints):
            raise SampleCampaignError("Campaign input digest does not match its inputs.")
        _bounded_id(self.model_revision, field="model revision")
        if not isinstance(self.settings, ImmutableSettings):
            raise SampleCampaignError("Campaign settings are not immutable.")
        if type(self.seed) is not int or not 0 <= self.seed <= MAX_SEED:
            raise SampleCampaignError("Campaign seed is invalid.")
        if type(self.output_index) is not int or self.output_index < 0:
            raise SampleCampaignError("Campaign output index is invalid.")
        if not isinstance(self.interventions, tuple):
            raise SampleCampaignError("Campaign interventions must be immutable.")
        if _normalize_interventions(self.interventions) != self.interventions:
            raise SampleCampaignError("Campaign interventions are not normalized.")


def build_arm_manifest(
    *,
    arm: CampaignArm,
    raw_prompt: str,
    private_input_paths: Sequence[str],
    input_fingerprints: Sequence[str],
    model_revision: str,
    settings: Mapping[str, Any],
    seed: int,
    output_index: int,
    interventions: Sequence[str],
) -> ArmManifest:
    fingerprints = _normalize_fingerprints(input_fingerprints)
    return ArmManifest(
        arm=arm,
        raw_prompt=raw_prompt,
        prompt_digest=normalized_prompt_digest(raw_prompt),
        private_input_paths=tuple(private_input_paths),
        input_fingerprints=fingerprints,
        input_digest=normalized_input_digest(fingerprints),
        model_revision=model_revision,
        settings=ImmutableSettings.from_mapping(settings),
        seed=seed,
        output_index=output_index,
        interventions=_normalize_interventions(interventions),
    )


@dataclass(frozen=True, slots=True)
class InterventionDelta:
    """The exact named behavior removed from or added to the control arm."""

    maestro_only: tuple[str, ...]
    control_only: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.maestro_only, tuple) or not isinstance(self.control_only, tuple):
            raise SampleCampaignError("Campaign intervention delta must be immutable.")
        if _normalize_interventions(self.maestro_only) != self.maestro_only:
            raise SampleCampaignError("Maestro intervention delta is not normalized.")
        if _normalize_interventions(self.control_only) != self.control_only:
            raise SampleCampaignError("Control intervention delta is not normalized.")
        if not self.maestro_only and not self.control_only:
            raise SampleCampaignError("Campaign pair requires an explicit intervention delta.")
        if set(self.maestro_only) & set(self.control_only):
            raise SampleCampaignError("Campaign intervention delta is contradictory.")

    def public_projection(self) -> dict[str, list[str]]:
        return {
            "maestro_only": list(self.maestro_only),
            "control_only": list(self.control_only),
        }


@dataclass(frozen=True, slots=True)
class ComparativePairManifest:
    """Immutable private pair whose only permitted difference is intervention."""

    pair_id: str
    case_id: str
    maestro: ArmManifest
    control: ArmManifest
    intervention_delta: InterventionDelta
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_pair_manifest(self)


def validate_pair_manifest(pair: ComparativePairManifest) -> None:
    if pair.schema_version != SCHEMA_VERSION:
        raise SampleCampaignError("Campaign pair schema version is unsupported.")
    _bounded_id(pair.pair_id, field="pair identifier")
    _bounded_id(pair.case_id, field="case identifier")
    if not isinstance(pair.maestro, ArmManifest) or pair.maestro.arm is not CampaignArm.MAESTRO:
        raise SampleCampaignError("Campaign pair requires exactly one Maestro arm.")
    if not isinstance(pair.control, ArmManifest) or pair.control.arm is not CampaignArm.CONTROL:
        raise SampleCampaignError("Campaign pair requires exactly one control arm.")
    if not isinstance(pair.intervention_delta, InterventionDelta):
        raise SampleCampaignError("Campaign pair intervention delta is invalid.")

    shared_fields = (
        "prompt_digest",
        "input_digest",
        "model_revision",
        "settings",
        "seed",
        "output_index",
    )
    for field in shared_fields:
        if getattr(pair.maestro, field) != getattr(pair.control, field):
            raise SampleCampaignError(f"Campaign pair has mismatched {field}.")

    maestro_only = tuple(sorted(set(pair.maestro.interventions) - set(pair.control.interventions)))
    control_only = tuple(sorted(set(pair.control.interventions) - set(pair.maestro.interventions)))
    if pair.intervention_delta != InterventionDelta(maestro_only, control_only):
        raise SampleCampaignError("Campaign intervention delta does not match its arms.")


def build_pair_manifest(
    *,
    pair_id: str,
    case_id: str,
    maestro: ArmManifest,
    control: ArmManifest,
    intervention_delta: InterventionDelta,
) -> ComparativePairManifest:
    return ComparativePairManifest(
        pair_id=pair_id,
        case_id=case_id,
        maestro=maestro,
        control=control,
        intervention_delta=intervention_delta,
    )


def _validate_frame_count(value: int) -> int:
    if type(value) is not int or value < 2:
        raise SampleCampaignError("Campaign arm frame count is invalid.")
    return value


def _round_fraction(value: Fraction) -> int:
    quotient, remainder = divmod(value.numerator, value.denominator)
    if remainder * 2 >= value.denominator:
        quotient += 1
    return quotient


def _indices_for_positions(positions: tuple[Fraction, ...], frame_count: int) -> tuple[int, ...]:
    last_index = frame_count - 1
    return tuple(_round_fraction(position * last_index) for position in positions)


def _deterministic_positions(
    maestro_frame_count: int,
    control_frame_count: int,
    sample_count: int,
) -> tuple[Fraction, ...]:
    if type(sample_count) is not int or not MIN_REVIEW_FRAMES <= sample_count <= MAX_REVIEW_FRAMES:
        raise SampleCampaignError("Campaign review requires two to five frames.")
    shortest_last_index = min(maestro_frame_count, control_frame_count) - 1
    target_stride = max(2, (shortest_last_index + 10) // 20)
    span = target_stride * (sample_count - 1)
    if span * MAX_NORMALIZED_FRAME_SPAN.denominator > (
        shortest_last_index * MAX_NORMALIZED_FRAME_SPAN.numerator
    ):
        raise SampleCampaignError(
            "Campaign clips are too short for nearby non-adjacent review frames."
        )
    start = (shortest_last_index - span) // 2
    return tuple(
        Fraction(start + index * target_stride, shortest_last_index)
        for index in range(sample_count)
    )


@dataclass(frozen=True, slots=True)
class EvaluationFrameSelection:
    """One common normalized motion window mapped onto both output arms."""

    maestro_frame_count: int
    control_frame_count: int
    normalized_positions: tuple[Fraction, ...]
    maestro_indices: tuple[int, ...]
    control_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        validate_frame_selection(self)

    def public_projection(self) -> dict[str, Any]:
        return {
            "sample_count": len(self.normalized_positions),
            "normalized_positions": [float(value) for value in self.normalized_positions],
            "maestro_indices": list(self.maestro_indices),
            "control_indices": list(self.control_indices),
        }


def validate_frame_selection(selection: EvaluationFrameSelection) -> None:
    maestro_count = _validate_frame_count(selection.maestro_frame_count)
    control_count = _validate_frame_count(selection.control_frame_count)
    positions = selection.normalized_positions
    if not isinstance(positions, tuple) or not (
        MIN_REVIEW_FRAMES <= len(positions) <= MAX_REVIEW_FRAMES
    ):
        raise SampleCampaignError(
            "Campaign review requires two to five normalized positions."
        )
    if any(
        not isinstance(value, Fraction) or value < 0 or value > 1
        for value in positions
    ):
        raise SampleCampaignError("Campaign review positions are invalid.")
    if any(left >= right for left, right in zip(positions, positions[1:])):
        raise SampleCampaignError("Campaign review positions must be sequential.")
    if positions[-1] - positions[0] > MAX_NORMALIZED_FRAME_SPAN:
        raise SampleCampaignError("Campaign review positions are too far spread.")
    if not isinstance(selection.maestro_indices, tuple) or not isinstance(
        selection.control_indices, tuple
    ):
        raise SampleCampaignError("Campaign review indices must be immutable.")
    if len(selection.maestro_indices) != len(positions) or len(
        selection.control_indices
    ) != len(positions):
        raise SampleCampaignError("Campaign review arms must use the same position count.")
    for indices, frame_count in (
        (selection.maestro_indices, maestro_count),
        (selection.control_indices, control_count),
    ):
        if any(
            type(index) is not int or not 0 <= index < frame_count
            for index in indices
        ):
            raise SampleCampaignError("Campaign review frame index is invalid.")
        if any(right - left < 2 for left, right in zip(indices, indices[1:])):
            raise SampleCampaignError(
                "Campaign review frames must be sequential and non-adjacent."
            )
        if indices != _indices_for_positions(positions, frame_count):
            raise SampleCampaignError(
                "Campaign review indices do not match normalized positions."
            )
    expected = _deterministic_positions(maestro_count, control_count, len(positions))
    if positions != expected:
        raise SampleCampaignError("Campaign review selection is not deterministic.")


def select_evaluation_frames(
    *,
    maestro_frame_count: int,
    control_frame_count: int,
    sample_count: int = 3,
) -> EvaluationFrameSelection:
    maestro_count = _validate_frame_count(maestro_frame_count)
    control_count = _validate_frame_count(control_frame_count)
    positions = _deterministic_positions(maestro_count, control_count, sample_count)
    return EvaluationFrameSelection(
        maestro_frame_count=maestro_count,
        control_frame_count=control_count,
        normalized_positions=positions,
        maestro_indices=_indices_for_positions(positions, maestro_count),
        control_indices=_indices_for_positions(positions, control_count),
    )


def _sha256_digest(value: str, *, field: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise SampleCampaignError(f"Campaign {field} must be a lowercase SHA-256 digest.")
    return value


def _domain_digest(domain: bytes, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(domain + b"\0" + encoded).hexdigest()


def pair_manifest_digest(pair: ComparativePairManifest) -> str:
    """Bind all private matched-pair inputs without exposing them publicly."""

    validate_pair_manifest(pair)

    def arm_value(arm: ArmManifest) -> dict[str, Any]:
        return {
            "arm": arm.arm.value,
            "prompt_digest": arm.prompt_digest,
            "input_digest": arm.input_digest,
            "model_revision": arm.model_revision,
            "settings_digest": arm.settings.digest,
            "seed": arm.seed,
            "output_index": arm.output_index,
            "interventions": list(arm.interventions),
        }

    return _domain_digest(
        b"maestro-sample-pair-manifest-v1",
        {
            "schema_version": pair.schema_version,
            "pair_id": pair.pair_id,
            "case_id": pair.case_id,
            "maestro": arm_value(pair.maestro),
            "control": arm_value(pair.control),
            "intervention_delta": pair.intervention_delta.public_projection(),
        },
    )


def frame_selection_digest(selection: EvaluationFrameSelection) -> str:
    """Bind exact rational positions and their arm-specific frame indices."""

    validate_frame_selection(selection)
    return _domain_digest(
        b"maestro-sample-frame-selection-v1",
        {
            "maestro_frame_count": selection.maestro_frame_count,
            "control_frame_count": selection.control_frame_count,
            "normalized_positions": [
                [position.numerator, position.denominator]
                for position in selection.normalized_positions
            ],
            "maestro_indices": list(selection.maestro_indices),
            "control_indices": list(selection.control_indices),
        },
    )


@dataclass(frozen=True, slots=True)
class ArmOutputEvidence:
    """Private output and selected-frame evidence for one generated arm."""

    arm: CampaignArm
    private_output_ref: str
    output_sha256: str
    selected_frame_sha256s: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.arm, CampaignArm):
            raise SampleCampaignError("Campaign output arm is invalid.")
        _bounded_id(self.private_output_ref, field="private output reference")
        _sha256_digest(self.output_sha256, field="output digest")
        if (
            not isinstance(self.selected_frame_sha256s, tuple)
            or not self.selected_frame_sha256s
        ):
            raise SampleCampaignError("Campaign selected-frame evidence is missing.")
        for digest in self.selected_frame_sha256s:
            _sha256_digest(digest, field="selected-frame digest")


def review_input_digest(
    *,
    manifest_digest: str,
    selection: EvaluationFrameSelection,
    maestro_output: ArmOutputEvidence,
    control_output: ArmOutputEvidence,
) -> str:
    """Bind the exact pair, motion window, outputs, and reviewed frames."""

    _sha256_digest(manifest_digest, field="pair manifest digest")
    if not isinstance(maestro_output, ArmOutputEvidence) or (
        maestro_output.arm is not CampaignArm.MAESTRO
    ):
        raise SampleCampaignError("Campaign review requires Maestro output evidence.")
    if not isinstance(control_output, ArmOutputEvidence) or (
        control_output.arm is not CampaignArm.CONTROL
    ):
        raise SampleCampaignError("Campaign review requires control output evidence.")
    count = len(selection.normalized_positions)
    if any(
        len(output.selected_frame_sha256s) != count
        for output in (maestro_output, control_output)
    ):
        raise SampleCampaignError(
            "Campaign output evidence does not align with the frame selection.",
        )
    return _domain_digest(
        b"maestro-sample-review-input-v1",
        {
            "pair_manifest_digest": manifest_digest,
            "frame_selection_digest": frame_selection_digest(selection),
            "maestro_output_sha256": maestro_output.output_sha256,
            "control_output_sha256": control_output.output_sha256,
            "maestro_selected_frames": list(
                maestro_output.selected_frame_sha256s,
            ),
            "control_selected_frames": list(
                control_output.selected_frame_sha256s,
            ),
        },
    )


def vlm_evidence_digest(
    *,
    private_report_ref: str,
    reviewer_revision: str,
    request_sha256: str,
    report_sha256: str,
    review_input_sha256: str,
    verdict: ReviewVerdict,
) -> str:
    """Bind a VLM verdict to the exact reviewed artifacts and reviewer."""

    return _domain_digest(
        b"maestro-sample-vlm-evidence-v1",
        {
            "private_report_ref": private_report_ref,
            "reviewer_revision": reviewer_revision,
            "request_sha256": request_sha256,
            "report_sha256": report_sha256,
            "review_input_sha256": review_input_sha256,
            "verdict": verdict.value if isinstance(verdict, ReviewVerdict) else verdict,
        },
    )


@dataclass(frozen=True, slots=True)
class VlmReviewEvidence:
    """Private provenance for one completed comparative VLM review."""

    private_report_ref: str
    reviewer_revision: str
    request_sha256: str
    report_sha256: str
    review_input_sha256: str
    verdict: ReviewVerdict
    evidence_sha256: str

    def __post_init__(self) -> None:
        _bounded_id(self.private_report_ref, field="private VLM report reference")
        _bounded_id(self.reviewer_revision, field="VLM reviewer revision")
        _sha256_digest(self.request_sha256, field="VLM request digest")
        _sha256_digest(self.report_sha256, field="VLM report digest")
        _sha256_digest(self.review_input_sha256, field="VLM review-input digest")
        if not isinstance(self.verdict, ReviewVerdict) or self.verdict not in _FINAL_VERDICTS:
            raise SampleCampaignError("Campaign VLM evidence requires a final verdict.")
        _sha256_digest(self.evidence_sha256, field="VLM evidence digest")
        if self.evidence_sha256 != vlm_evidence_digest(
            private_report_ref=self.private_report_ref,
            reviewer_revision=self.reviewer_revision,
            request_sha256=self.request_sha256,
            report_sha256=self.report_sha256,
            review_input_sha256=self.review_input_sha256,
            verdict=self.verdict,
        ):
            raise SampleCampaignError("Campaign VLM evidence digest does not match.")


def human_evidence_digest(
    *,
    decision_id: str,
    reviewer_ref: str,
    decision_sha256: str,
    vlm_report_sha256: str,
    verdict: ReviewVerdict,
) -> str:
    """Bind the human verdict to its decision and reviewed VLM report."""

    return _domain_digest(
        b"maestro-sample-human-evidence-v1",
        {
            "decision_id": decision_id,
            "reviewer_ref": reviewer_ref,
            "decision_sha256": decision_sha256,
            "vlm_report_sha256": vlm_report_sha256,
            "verdict": verdict.value if isinstance(verdict, ReviewVerdict) else verdict,
        },
    )


@dataclass(frozen=True, slots=True)
class HumanReviewEvidence:
    """Private provenance for the owner's final comparative observation."""

    decision_id: str
    reviewer_ref: str
    decision_sha256: str
    vlm_report_sha256: str
    verdict: ReviewVerdict
    evidence_sha256: str

    def __post_init__(self) -> None:
        _bounded_id(self.decision_id, field="human decision identifier")
        _bounded_id(self.reviewer_ref, field="human reviewer reference")
        _sha256_digest(self.decision_sha256, field="human decision digest")
        _sha256_digest(self.vlm_report_sha256, field="bound VLM report digest")
        if not isinstance(self.verdict, ReviewVerdict) or self.verdict not in _FINAL_VERDICTS:
            raise SampleCampaignError("Campaign human evidence requires a final verdict.")
        _sha256_digest(self.evidence_sha256, field="human evidence digest")
        if self.evidence_sha256 != human_evidence_digest(
            decision_id=self.decision_id,
            reviewer_ref=self.reviewer_ref,
            decision_sha256=self.decision_sha256,
            vlm_report_sha256=self.vlm_report_sha256,
            verdict=self.verdict,
        ):
            raise SampleCampaignError("Campaign human evidence digest does not match.")


@dataclass(frozen=True, slots=True)
class _EvaluationState:
    """Digest-free public state derived only from a private receipt."""

    evidence_class: EvidenceClass = EvidenceClass.MANIFEST_ONLY
    vlm_verdict: ReviewVerdict = ReviewVerdict.NOT_REVIEWED
    human_verdict: ReviewVerdict = ReviewVerdict.NOT_REVIEWED

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_class, EvidenceClass):
            raise SampleCampaignError("Campaign evidence class is invalid.")
        if not isinstance(self.vlm_verdict, ReviewVerdict) or not isinstance(
            self.human_verdict, ReviewVerdict
        ):
            raise SampleCampaignError("Campaign review verdict is invalid.")

        if self.evidence_class is EvidenceClass.MANIFEST_ONLY:
            if (
                self.vlm_verdict is not ReviewVerdict.NOT_REVIEWED
                or self.human_verdict is not ReviewVerdict.NOT_REVIEWED
            ):
                raise SampleCampaignError(
                    "Manifest-only evidence cannot contain review activity."
                )
        elif self.evidence_class is EvidenceClass.GENERATED_OUTPUTS:
            if (
                self.vlm_verdict is not ReviewVerdict.NOT_REVIEWED
                or self.human_verdict is not ReviewVerdict.NOT_REVIEWED
            ):
                raise SampleCampaignError(
                    "Generated-output evidence cannot contain review activity."
                )
        elif self.evidence_class is EvidenceClass.VLM_REVIEWED:
            if self.vlm_verdict not in _FINAL_VERDICTS:
                raise SampleCampaignError(
                    "VLM-reviewed evidence requires a completed VLM verdict."
                )
            if self.human_verdict is not ReviewVerdict.NOT_REVIEWED:
                raise SampleCampaignError(
                    "VLM-reviewed evidence cannot contain human review activity."
                )
        elif self.evidence_class is EvidenceClass.HUMAN_REVIEWED:
            if self.vlm_verdict not in _FINAL_VERDICTS:
                raise SampleCampaignError(
                    "Human-reviewed evidence requires a completed VLM verdict."
                )
            if self.human_verdict not in _FINAL_VERDICTS:
                raise SampleCampaignError(
                    "Human-reviewed evidence requires a completed human verdict."
                )

@dataclass(frozen=True, slots=True)
class EvaluationReceipt:
    """Immutable private evidence whose presence determines the public class."""

    pair_manifest_sha256: str
    frame_selection: EvaluationFrameSelection
    maestro_output: ArmOutputEvidence
    control_output: ArmOutputEvidence
    vlm_review: VlmReviewEvidence | None = None
    human_review: HumanReviewEvidence | None = None

    def __post_init__(self) -> None:
        _sha256_digest(self.pair_manifest_sha256, field="pair manifest digest")
        if not isinstance(self.frame_selection, EvaluationFrameSelection):
            raise SampleCampaignError("Campaign receipt frame selection is invalid.")
        expected_input = review_input_digest(
            manifest_digest=self.pair_manifest_sha256,
            selection=self.frame_selection,
            maestro_output=self.maestro_output,
            control_output=self.control_output,
        )
        if self.vlm_review is not None:
            if not isinstance(self.vlm_review, VlmReviewEvidence):
                raise SampleCampaignError("Campaign VLM evidence is invalid.")
            if self.vlm_review.review_input_sha256 != expected_input:
                raise SampleCampaignError(
                    "Campaign VLM evidence is bound to different review inputs.",
                )
        if self.human_review is not None:
            if not isinstance(self.human_review, HumanReviewEvidence):
                raise SampleCampaignError("Campaign human evidence is invalid.")
            if self.vlm_review is None:
                raise SampleCampaignError(
                    "Campaign human evidence requires completed VLM evidence.",
                )
            if self.human_review.vlm_report_sha256 != self.vlm_review.report_sha256:
                raise SampleCampaignError(
                    "Campaign human evidence is bound to a different VLM report.",
                )

    def _evaluation_state(self) -> _EvaluationState:
        if self.human_review is not None:
            assert self.vlm_review is not None
            return _EvaluationState(
                EvidenceClass.HUMAN_REVIEWED,
                self.vlm_review.verdict,
                self.human_review.verdict,
            )
        if self.vlm_review is not None:
            return _EvaluationState(
                EvidenceClass.VLM_REVIEWED,
                self.vlm_review.verdict,
                ReviewVerdict.NOT_REVIEWED,
            )
        return _EvaluationState(EvidenceClass.GENERATED_OUTPUTS)


_EVIDENCE_RANK = {
    EvidenceClass.GENERATED_OUTPUTS: 1,
    EvidenceClass.VLM_REVIEWED: 2,
    EvidenceClass.HUMAN_REVIEWED: 3,
}


def validate_evaluation_transition(
    previous: EvaluationReceipt,
    current: EvaluationReceipt,
) -> None:
    """Require append-only evidence without downgrade or provenance rebinding."""

    if not isinstance(previous, EvaluationReceipt) or not isinstance(
        current, EvaluationReceipt,
    ):
        raise SampleCampaignError("Campaign evaluation transition is invalid.")
    if (
        previous.pair_manifest_sha256 != current.pair_manifest_sha256
        or previous.frame_selection != current.frame_selection
        or previous.maestro_output != current.maestro_output
        or previous.control_output != current.control_output
    ):
        raise SampleCampaignError("Campaign evaluation provenance cannot be rebound.")
    previous_class = previous._evaluation_state().evidence_class
    current_class = current._evaluation_state().evidence_class
    if _EVIDENCE_RANK[current_class] < _EVIDENCE_RANK[previous_class]:
        raise SampleCampaignError("Campaign evaluation evidence cannot be downgraded.")
    if previous.vlm_review is not None and current.vlm_review != previous.vlm_review:
        raise SampleCampaignError("Campaign VLM evidence cannot be replaced.")
    if previous.human_review is not None and current.human_review != previous.human_review:
        raise SampleCampaignError("Campaign human evidence cannot be replaced.")


def public_pair_projection(
    pair: ComparativePairManifest,
    *,
    receipt: EvaluationReceipt | None = None,
) -> dict[str, Any]:
    """Return bounded state with no prompt, input path, fingerprint, or digest."""

    validate_pair_manifest(pair)
    if receipt is not None and not isinstance(receipt, EvaluationReceipt):
        raise SampleCampaignError("Campaign evaluation receipt is invalid.")
    if receipt is not None and receipt.pair_manifest_sha256 != pair_manifest_digest(pair):
        raise SampleCampaignError(
            "Campaign evaluation receipt is bound to a different pair manifest.",
        )
    state = _EvaluationState() if receipt is None else receipt._evaluation_state()
    result: dict[str, Any] = {
        "schema_version": pair.schema_version,
        "pair_id": pair.pair_id,
        "case_id": pair.case_id,
        "arms": [CampaignArm.MAESTRO.value, CampaignArm.CONTROL.value],
        "shared_generation": {
            "same_normalized_prompt": True,
            "same_normalized_inputs": True,
            "same_model_revision": True,
            "same_settings": True,
            "same_seed": True,
            "same_output_index": True,
            "model_revision": pair.maestro.model_revision,
            "seed": pair.maestro.seed,
            "output_index": pair.maestro.output_index,
            "input_count": len(pair.maestro.input_fingerprints),
        },
        "intervention_delta": pair.intervention_delta.public_projection(),
        "evaluation": {
            "evidence_class": state.evidence_class.value,
            "vlm_verdict": state.vlm_verdict.value,
            "human_verdict": state.human_verdict.value,
        },
    }
    if receipt is not None:
        result["frame_selection"] = receipt.frame_selection.public_projection()
    return result
