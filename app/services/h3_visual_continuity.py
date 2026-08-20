"""Visual join policy for MiniMax H3 long-form clips.

Timing/fold (``_fold_short_terminal_clip``) is already landed. Museum-style
shot lists still look disjoint at the published join because:

1. ``classify_timeline_clip_boundaries`` treats every ``[Shot N]`` marker as
   a cut unless the text says "same shot" / "camera continues".
2. ``_attach_h3_ref2va_handoff`` (launch.py, Ember-reserved) only attaches a
   temporal video tail for ``continuous`` / ``precut``. Authored cuts get a
   last-frame still in ``image_refs``, which does not lock identity or camera
   world across the concat.
3. Same-source clip prompts do not carry the previous clip's last published
   frame as an opening visual constraint.

Museum (and authored shorts) are one film with first-class camera cuts
(macro, wide, chase, new viewpoint). Hard ``[Shot N]`` cuts must work well
— do not flatten them, and do not tell rewriters to avoid shot markers.
The join still needs a temporal tail plus a prompt carry so the next native
window starts on the previous published frame. Segment seams are broader
than matching end/start frames or soundwaves: identity, wardrobe, location,
lighting, camera-world, motion energy, ambient bed, and the feeling of one
film.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any


H3_TEMPORAL_TAIL_BOUNDARIES = frozenset({
    "continuous", "precut", "cut", "transition",
})

SAME_SOURCE_VISUAL_CARRY_LINE = (
    "OPENING VISUAL CARRY: begin on the previous clip's last published "
    "frame. This is a segment seam in one film — keep identity, wardrobe, "
    "location, lighting, camera-world, motion energy, and ambient bed; "
    "then perform only this clip's authored camera change."
)


def ref2va_handoff_uses_temporal_tail(boundary_type: object) -> bool:
    """Authored cuts still need a time-ordered tail for identity at the join."""

    return str(boundary_type or "").strip().casefold() in H3_TEMPORAL_TAIL_BOUNDARIES


def same_source_visual_carry_line() -> str:
    return SAME_SOURCE_VISUAL_CARRY_LINE


def apply_same_source_visual_carry(
    clip_prompts: Sequence[str],
    *,
    clip_boundaries: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Prefix non-first same-source clips with a last-frame visual carry.

    Does not rewrite the first clip. Does not inspect creative subject matter
    beyond the already-compiled executable prompt bytes.
    """

    prompts = [str(item or "") for item in clip_prompts]
    if len(prompts) < 2:
        return prompts
    boundaries = list(clip_boundaries or [])
    updated: list[str] = []
    for index, prompt in enumerate(prompts):
        if index == 0 or SAME_SOURCE_VISUAL_CARRY_LINE in prompt:
            updated.append(prompt)
            continue
        boundary = boundaries[index - 1] if index - 1 < len(boundaries) else {}
        if str(boundary.get("continuity_mode") or "") == "independent" and (
            str(boundary.get("type") or "") not in H3_TEMPORAL_TAIL_BOUNDARIES
        ):
            updated.append(prompt)
            continue
        prefix = SAME_SOURCE_VISUAL_CARRY_LINE
        updated.append(f"{prefix}\n{prompt}".strip() if prompt else prefix)
    return updated


def apply_visual_carry_to_shot_plan(
    shot_plan: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Apply the carry to a compiled shot plan's executable clip prompts."""

    prompts = shot_plan.get("clip_prompts")
    if not isinstance(prompts, list):
        return shot_plan
    shot_plan["clip_prompts"] = apply_same_source_visual_carry(
        prompts,
        clip_boundaries=shot_plan.get("clip_boundaries") or [],
    )
    return shot_plan
