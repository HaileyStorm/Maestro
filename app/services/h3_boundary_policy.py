"""Pure MiniMax H3 long-form boundary policy.

Wan2GP 12.44 can condition a native H3 window on 18 decoded frames.  The
model consumes the first 17 frames as temporal history and the eighteenth as
the target's first-frame anchor.  Maestro keeps that behavior opt-in until a
fixed-seed live comparison has passed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from typing import Mapping


H3_NATIVE_FPS = 24
H3_NATIVE_AUDIO_SAMPLE_RATE = 32_000
H3_NATIVE_OVERLAP_FRAMES = 18
H3_NATIVE_HISTORY_FRAMES = H3_NATIVE_OVERLAP_FRAMES - 1
H3_NATIVE_BOUNDARY_TYPES = frozenset({"continuous", "precut"})
H3_CUT_BOUNDARY_TYPES = frozenset({"cut", "transition"})
H3_FL2VA_MODELS = frozenset({
    "minimax_h3",
    "minimax_h3_w4a8_fl2va",
    "minimax_h3_pinkcherry_fl2va",
})
H3_REF2VA_MODEL = "minimax_h3_ref2va"


@dataclass(frozen=True)
class H3BoundaryDecision:
    """One segment's effective checkpoint and predecessor handoff."""

    model_type: str
    reason: str
    temporal_overlap: bool = False
    predecessor_semantic_still: bool = False
    overlap_frames: int = 0
    discard_frames: int = 0

    def as_dict(self) -> dict:
        return {
            "model_type": self.model_type,
            "reason": self.reason,
            "temporal_overlap": self.temporal_overlap,
            "predecessor_semantic_still": self.predecessor_semantic_still,
            "overlap_frames": self.overlap_frames,
            "discard_frames": self.discard_frames,
        }


def normalize_boundary_type(value: object) -> str:
    boundary = str(value or "continuous").strip().casefold()
    if boundary not in H3_NATIVE_BOUNDARY_TYPES | H3_CUT_BOUNDARY_TYPES:
        raise ValueError(f"Unknown H3 boundary type: {boundary}")
    return boundary


def has_semantic_references(params: Mapping[str, object]) -> bool:
    """Return whether the request carries Ref2VA semantic media."""

    return bool(params.get("image_refs")) or any(
        params.get(key)
        for key in (
            "video_guide", "video_guide2", "video_guide3",
            "audio_guide", "audio_guide2", "audio_guide3",
        )
    ) or any(
        letter in str(params.get("audio_prompt_type") or "")
        for letter in "ABCK"
    )


def decide_h3_boundary(
    *,
    segment_index: int,
    boundary_type: object,
    semantic_references: bool,
    preferred_fl2va_model: str,
) -> H3BoundaryDecision:
    """Apply the opt-in Studio/Director H3 conditioning table.

    The first segment has no predecessor.  Continuous and pre-cut boundaries
    use native AV history on the checkpoint selected by the request's semantic
    inputs.  Cuts deliberately remove temporal history and use Ref2VA; when
    the user supplied no semantic reference, the predecessor's final frame is
    added later as the sole rolling semantic reference.
    """

    index = int(segment_index)
    if index < 0:
        raise ValueError("H3 segment index cannot be negative")
    fl2va = str(preferred_fl2va_model or "minimax_h3")
    if fl2va not in H3_FL2VA_MODELS:
        raise ValueError(f"Unknown preferred H3 FL2VA checkpoint: {fl2va}")

    if index == 0:
        return H3BoundaryDecision(
            model_type=H3_REF2VA_MODEL if semantic_references else fl2va,
            reason=(
                "supplied semantic references"
                if semantic_references else "selected FL2VA quality profile"
            ),
        )

    boundary = normalize_boundary_type(boundary_type)
    if boundary in H3_NATIVE_BOUNDARY_TYPES:
        return H3BoundaryDecision(
            model_type=H3_REF2VA_MODEL if semantic_references else fl2va,
            reason=(
                "semantic references plus native AV boundary history"
                if semantic_references
                else "native AV boundary history on the selected FL2VA checkpoint"
            ),
            temporal_overlap=True,
            overlap_frames=H3_NATIVE_OVERLAP_FRAMES,
            discard_frames=H3_NATIVE_HISTORY_FRAMES,
        )
    return H3BoundaryDecision(
        model_type=H3_REF2VA_MODEL,
        reason="semantic reset across an authored cut or transition",
        predecessor_semantic_still=not semantic_references,
    )


def generation_frames_for_segment(
    published_frames: int,
    decision: H3BoundaryDecision | Mapping[str, object],
) -> int:
    """Return model frames, including the 17 returned history frames."""

    frames = int(published_frames)
    if frames < 1:
        raise ValueError("H3 published segment frames must be positive")
    discard = (
        decision.discard_frames
        if isinstance(decision, H3BoundaryDecision)
        else int(decision.get("discard_frames") or 0)
    )
    return frames + discard


def attest_boundary_file(path: str) -> dict:
    """Return a content identity for one already-private boundary artifact."""

    candidate = os.path.abspath(os.fspath(path))
    digest = hashlib.sha256()
    size = 0
    with open(candidate, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return {"path": candidate, "size": size, "sha256": digest.hexdigest()}


def verify_boundary_file(descriptor: Mapping[str, object]) -> str:
    """Verify one runtime descriptor before decoding any boundary media."""

    path = descriptor.get("path")
    expected_size = descriptor.get("size")
    expected_digest = descriptor.get("sha256")
    if (
        not isinstance(path, str)
        or not path
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 1
        or not isinstance(expected_digest, str)
        or len(expected_digest) != 64
    ):
        raise ValueError("H3 native boundary descriptor is invalid")
    actual = attest_boundary_file(path)
    if actual["size"] != expected_size or actual["sha256"] != expected_digest:
        raise ValueError("H3 native boundary artifact failed hash attestation")
    return actual["path"]


__all__ = [
    "H3BoundaryDecision",
    "H3_CUT_BOUNDARY_TYPES",
    "H3_FL2VA_MODELS",
    "H3_NATIVE_AUDIO_SAMPLE_RATE",
    "H3_NATIVE_BOUNDARY_TYPES",
    "H3_NATIVE_FPS",
    "H3_NATIVE_HISTORY_FRAMES",
    "H3_NATIVE_OVERLAP_FRAMES",
    "H3_REF2VA_MODEL",
    "attest_boundary_file",
    "decide_h3_boundary",
    "generation_frames_for_segment",
    "has_semantic_references",
    "normalize_boundary_type",
    "verify_boundary_file",
]
