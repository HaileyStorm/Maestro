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
lighting, camera-world, motion energy, ambient bed, audio bed, and the
feeling of one film.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
import re
from typing import Any


H3_TEMPORAL_TAIL_BOUNDARIES = frozenset({
    "continuous", "precut", "cut", "transition",
})

H3_SEAM_LOCK_KEYS = (
    "identity",
    "wardrobe",
    "location",
    "camera-world",
    "audio",
    "motion",
    "ambient",
    "energy",
)

SAME_SOURCE_VISUAL_CARRY_LINE = (
    "OPENING VISUAL CARRY: begin on the previous clip's last published "
    "frame. This is a segment seam in one film — keep identity, wardrobe, "
    "location, lighting, camera-world, motion energy, ambient bed, audio bed, "
    "and one-film energy; then perform only this clip's authored camera change."
)

SEGMENT_SEAM_LOCKS_HEADER = "SEGMENT SEAM LOCKS:"

_SHOT_MARKER_RE = re.compile(r"\[Shot\s+[1-9]\d*\]", re.IGNORECASE)

_LABELED_FIELD_RE = re.compile(
    r"(?im)^(?P<key>subject_definitions|overall_soundscape|"
    r"non_diegetic_music|location|setting|environment|lighting|"
    r"visual\s+world|visual\s+continuity|project\s+continuity|"
    r"pacing)\s*:\s*(?P<value>.+)$"
)

_SUBJECT_RE = re.compile(r"<Subject\s+[1-9]\d*>", re.IGNORECASE)

_DEFAULT_SEAM_LOCKS = {
    "identity": "keep established subjects across this authored cut",
    "wardrobe": "keep established wardrobe and carried objects",
    "location": "keep established location unless this clip authors a move",
    "camera-world": (
        "keep the same film-world; perform only this clip's authored "
        "camera change"
    ),
    "audio": (
        "continue the previous clip's audio bed; do not hard-reset "
        "dialogue space or effects"
    ),
    "motion": (
        "continue motion energy through the cut; do not freeze into a "
        "new first frame"
    ),
    "ambient": "continue the ambient bed without restarting",
    "energy": "keep one-film energy across the segment seam",
}

_MOTION_CUES = (
    "tracking", "chase", "running", "walking", "orbit", "push-in",
    "dolly", "handheld",
)

_ENERGY_CUES = (
    "pace lifts", "crowded", "quiet", "slow motion", "frantic", "hushed",
)


def ref2va_handoff_uses_temporal_tail(boundary_type: object) -> bool:
    """Authored cuts still need a time-ordered tail for identity at the join."""

    return str(boundary_type or "").strip().casefold() in H3_TEMPORAL_TAIL_BOUNDARIES


def same_source_visual_carry_line() -> str:
    return SAME_SOURCE_VISUAL_CARRY_LINE


def authored_shot_markers(text: object) -> tuple[str, ...]:
    """Return authored ``[Shot N]`` markers in source order."""

    return tuple(_SHOT_MARKER_RE.findall(str(text or "")))


def shot_markers_preserved(original: object, updated: object) -> bool:
    """True when every original shot marker still appears, in order."""

    original_markers = authored_shot_markers(original)
    if not original_markers:
        return True
    updated_markers = authored_shot_markers(updated)
    cursor = 0
    for marker in original_markers:
        try:
            found = updated_markers.index(marker, cursor)
        except ValueError:
            return False
        cursor = found + 1
    return True


def _compact(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{shortened}..." if shortened else text[:limit]


def _labeled_fields(prompt: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _LABELED_FIELD_RE.finditer(prompt):
        key = re.sub(r"\s+", "_", match.group("key").strip().casefold())
        value = _compact(match.group("value"), 160)
        if value:
            fields[key] = value
    return fields


def _cue_phrase(prompt: str, cues: Sequence[str]) -> str:
    lowered = prompt.casefold()
    found = [cue for cue in cues if cue in lowered]
    if not found:
        return ""
    return _compact(", ".join(found), 80)


def extract_segment_seam_locks(previous_prompt: object) -> dict[str, str]:
    """Build required seam locks from the previous clip's prompt bytes.

    Copies structured IR fields when present. Does not inspect creative
    subject matter beyond those already-compiled executable bytes, and does
    not rewrite or drop ``[Shot N]`` markers.
    """

    previous = str(previous_prompt or "")
    fields = _labeled_fields(previous)
    locks = dict(_DEFAULT_SEAM_LOCKS)

    subjects = ", ".join(dict.fromkeys(_SUBJECT_RE.findall(previous)))
    identity_bits = [
        part for part in (subjects, fields.get("subject_definitions")) if part
    ]
    if identity_bits:
        locks["identity"] = _compact(
            f"{locks['identity']}: {'; '.join(identity_bits)}",
            200,
        )

    location = (
        fields.get("location")
        or fields.get("setting")
        or fields.get("environment")
    )
    if location:
        locks["location"] = _compact(
            f"{locks['location']}: {location}",
            200,
        )

    world_bits = [
        part for part in (
            fields.get("lighting"),
            fields.get("visual_world"),
            fields.get("visual_continuity"),
            fields.get("project_continuity"),
        ) if part
    ]
    if world_bits:
        locks["camera-world"] = _compact(
            f"{locks['camera-world']}; incoming: {'; '.join(world_bits)}",
            220,
        )

    soundscape = fields.get("overall_soundscape")
    music = fields.get("non_diegetic_music")
    if soundscape:
        locks["audio"] = _compact(
            f"{locks['audio']}: {soundscape}",
            220,
        )
        locks["ambient"] = _compact(
            f"{locks['ambient']}: {soundscape}",
            220,
        )
    if music:
        locks["audio"] = _compact(
            f"{locks['audio']}; music: {music}",
            240,
        )

    motion_cues = _cue_phrase(previous, _MOTION_CUES)
    if motion_cues:
        locks["motion"] = _compact(
            f"{locks['motion']}: {motion_cues}",
            200,
        )

    energy_cues = _cue_phrase(previous, _ENERGY_CUES)
    pacing = fields.get("pacing")
    energy_bits = [part for part in (energy_cues, pacing) if part]
    if energy_bits:
        locks["energy"] = _compact(
            f"{locks['energy']}: {'; '.join(energy_bits)}",
            200,
        )

    return {key: locks[key] for key in H3_SEAM_LOCK_KEYS}


def format_segment_seam_locks(locks: Mapping[str, str]) -> str:
    """Stable ``key=value`` line. Tests fail if any required key is dropped."""

    parts: list[str] = []
    for key in H3_SEAM_LOCK_KEYS:
        value = _compact(locks.get(key) or _DEFAULT_SEAM_LOCKS[key], 240)
        parts.append(f"{key}={value}")
    return f"{SEGMENT_SEAM_LOCKS_HEADER} {'; '.join(parts)}"


def opening_carry_prefix(previous_prompt: object = "") -> str:
    """Carry line plus previous-clip seam locks. Body is not rewritten."""

    locks = extract_segment_seam_locks(previous_prompt)
    return f"{SAME_SOURCE_VISUAL_CARRY_LINE}\n{format_segment_seam_locks(locks)}"


def _prefix_without_rewriting_body(prefix: str, prompt: str) -> str:
    body = str(prompt or "").strip()
    if not body:
        return prefix
    return f"{prefix}\n{body}"


def apply_same_source_visual_carry(
    clip_prompts: Sequence[str],
    *,
    clip_boundaries: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Prefix non-first same-source clips with a last-frame visual carry.

    Prefix-only: the original clip body, including every ``[Shot N]`` marker,
    is appended unchanged. Does not inspect creative subject matter beyond
    the already-compiled executable prompt bytes.
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
        prefix = opening_carry_prefix(prompts[index - 1])
        carried = _prefix_without_rewriting_body(prefix, prompt)
        if not shot_markers_preserved(prompt, carried):
            missing = [
                marker for marker in authored_shot_markers(prompt)
                if marker not in authored_shot_markers(carried)
            ]
            carried = f"{carried}\n{' '.join(missing)}".strip()
        updated.append(carried)
    return updated


def apply_visual_carry_to_shot_plan(
    shot_plan: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Apply the carry to a compiled shot plan's executable clip prompts."""

    prompts = shot_plan.get("clip_prompts")
    if not isinstance(prompts, list):
        return shot_plan
    boundaries = shot_plan.get("clip_boundaries") or []
    carried = apply_same_source_visual_carry(
        prompts,
        clip_boundaries=boundaries,
    )
    shot_plan["clip_prompts"] = carried
    seam_locks: list[dict[str, str] | None] = []
    for index, prompt in enumerate(carried):
        if index == 0 or SAME_SOURCE_VISUAL_CARRY_LINE not in str(prompt or ""):
            seam_locks.append(None)
            continue
        previous = prompts[index - 1] if index - 1 < len(prompts) else ""
        seam_locks.append(extract_segment_seam_locks(previous))
    shot_plan["clip_seam_locks"] = seam_locks
    return shot_plan
