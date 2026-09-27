"""Pure decoding for serialized control-video prompt flags."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecodedVideoPromptType:
    """Meaningful parts of a serialized ``video_prompt_type`` value."""

    control_flags: str
    timeline_aligned: bool
    uses_temporal_depth: bool


def _serialized_flags(value: object) -> tuple[str, bool]:
    """Return flag text and whether its ordering has meaning."""

    if isinstance(value, set):
        # Sets were accepted by the preflight helper, but their iteration
        # order cannot establish whether a final T is the alignment marker.
        # Keep them deterministic and treat T as a control flag.
        return "".join(sorted(str(item or "") for item in value)), False
    if isinstance(value, (list, tuple)):
        return "".join(str(item or "") for item in value), True
    return str(value or ""), True


def decode_video_prompt_type(value: object) -> DecodedVideoPromptType:
    """Decode the trailing ``T`` alignment marker without losing depth flags.

    In the documented serialized convention, one final uppercase ``T`` means
    timeline alignment. Earlier ``T`` letters remain temporal-depth controls.
    For example, ``PTVGT`` is aligned temporal depth while ``PVGT`` is aligned
    pose/video guidance without temporal depth. Unordered sets cannot express
    a trailing marker, so their ``T`` keeps its historical control meaning.
    """

    serialized, ordered = _serialized_flags(value)
    timeline_aligned = ordered and serialized.endswith("T")
    control_flags = serialized[:-1] if timeline_aligned else serialized
    normalized = control_flags.casefold()
    return DecodedVideoPromptType(
        control_flags=control_flags,
        timeline_aligned=timeline_aligned,
        uses_temporal_depth=("T" in control_flags or "depth_temporal" in normalized),
    )


def encode_video_prompt_type(
    control_flags: str,
    *,
    timeline_aligned: bool,
) -> str:
    """Serialize control flags, adding at most the trailing alignment marker."""

    return f"{control_flags or ''}{'T' if timeline_aligned else ''}"


def update_video_prompt_type_flags(
    value: object,
    *,
    remove_flags: str = "",
    add_flags: str = "",
    timeline_aligned: bool | None = None,
) -> str:
    """Change control letters while keeping alignment as a trailing marker.

    A newly added temporal-depth ``T`` is placed before the other control
    letters. This keeps it distinct from the reserved final alignment marker.
    """

    decoded = decode_video_prompt_type(value)
    removed = set(remove_flags)
    control_flags = "".join(
        flag for flag in decoded.control_flags if flag not in removed
    )
    add_flags = str(add_flags or "")
    original_control_flags = control_flags
    if "T" in add_flags and "T" not in original_control_flags:
        control_flags = "T" + control_flags
    for flag in add_flags:
        # Match add_to_sequence's membership behavior for non-T flags while
        # keeping the reserved alignment suffix out of the control text.
        if flag != "T" and flag not in original_control_flags:
            control_flags += flag
    return encode_video_prompt_type(
        control_flags,
        timeline_aligned=(
            decoded.timeline_aligned
            if timeline_aligned is None
            else timeline_aligned
        ),
    )


def set_video_prompt_type_alignment(value: object, alignment_value: object) -> str:
    """Update the alignment dropdown while preserving internal control flags."""

    return update_video_prompt_type_flags(
        value,
        timeline_aligned=alignment_value == "T",
    )
