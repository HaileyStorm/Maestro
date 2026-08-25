"""Sealed, tensor-free planning contract for native MiniMax H3 bridges.

The plan commits only content-addressed source ranges and integer timeline
geometry.  It deliberately carries no paths, prompts, decoded media, tensors,
runtime imports, execution claim, or fallback.  A later Director/runtime
binding may consume a validated plan, but importing this module cannot execute
one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from services.h3_native_continuation import (
    H3_AUDIO_TICKS_PER_SECOND,
    H3_NATIVE_FPS,
    H3_VIDEO_FRAME_OFFSET,
    H3_VIDEO_FRAME_STEP,
    audio_tick_at_frame,
    audio_ticks_between_frames,
    is_legal_h3_video_frame_count,
)


H3_BRIDGE_PLAN_KIND: Final = "minimax_h3_bridge"
H3_BRIDGE_PLAN_SCHEMA: Final = "maestro.h3.bridge-plan"
H3_BRIDGE_PLAN_VERSION: Final = 1
H3_BRIDGE_CONDITIONING_FAMILY: Final = "ref2va"
H3_BRIDGE_MIN_GENERATED_FRAMES: Final = 107
H3_BRIDGE_MAX_GENERATED_FRAMES: Final = 345

_MAX_FRAME_POSITION = 10_000_000
_MAX_REROLL_INDEX = 1_000_000
_MAX_JSON_DEPTH = 32
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")

_TOP_LEVEL_FIELDS = frozenset(
    {
        "kind",
        "schema",
        "version",
        "conditioning_family",
        "clock",
        "sources",
        "generation",
        "audio",
        "assembly",
        "execution_available",
        "automatic_fallback",
        "recovery_sha256",
        "reroll",
        "plan_sha256",
    }
)
_CLOCK_FIELDS = frozenset(
    {"fps", "audio_ticks_per_second", "video_frame_step", "video_frame_offset"}
)
_SOURCES_FIELDS = frozenset({"a_tail", "b_head"})
_SOURCE_INPUT_FIELDS = frozenset({"sha256", "start_frame", "end_frame_exclusive"})
_SOURCE_FIELDS = _SOURCE_INPUT_FIELDS | {"frame_count"}
_RANGE_FIELDS = frozenset({"start_frame", "end_frame_exclusive"})
_GENERATION_FIELDS = frozenset(
    {
        "generated_range",
        "generated_frames",
        "generated_audio_ticks",
        "hidden_head_range",
        "hidden_head_audio_ticks",
        "hidden_tail_range",
        "hidden_tail_audio_ticks",
        "published_range",
        "published_frames",
        "published_audio_ticks",
    }
)
_AUDIO_FIELDS = frozenset(
    {"bridge_mode", "drive_track_sha256", "left_seam", "right_seam"}
)
_SEAM_FIELDS = frozenset({"mode", "owner", "overlap_audio_ticks"})
_ASSEMBLY_FIELDS = frozenset(
    {"order", "bridge_generated_range", "bridge_published_range", "hidden_ranges"}
)
_REROLL_FIELDS = frozenset({"scope", "index", "identity_sha256"})

_BRIDGE_AUDIO_MODES = frozenset({"generated", "silent", "drive_track"})
_SEAM_MODES = frozenset({"hard_cut", "crossfade"})
_LEFT_HARD_CUT_OWNERS = frozenset({"clip_a", "bridge", "drive_track"})
_RIGHT_HARD_CUT_OWNERS = frozenset({"bridge", "clip_b", "drive_track"})


class H3BridgePlanError(ValueError):
    """Raised when a bridge plan is noncanonical or contradictory."""


def _require_plain_json(value: object, *, field: str) -> None:
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active: set[int] = set()
    while stack:
        item, depth, exiting = stack.pop()
        if exiting:
            active.remove(id(item))
            continue
        if item is None or type(item) in (str, int, bool):
            continue
        if type(item) not in (dict, list):
            raise H3BridgePlanError(f"{field} must contain exact plain JSON values")
        if depth >= _MAX_JSON_DEPTH:
            raise H3BridgePlanError(f"{field} exceeds the bounded JSON depth")
        if type(item) is dict and not all(type(key) is str for key in item):
            raise H3BridgePlanError(f"{field} must contain exact plain JSON values")
        identity = id(item)
        if identity in active:
            raise H3BridgePlanError(f"{field} cannot contain a JSON cycle")
        active.add(identity)
        stack.append((item, depth, True))
        children = item if type(item) is list else item.values()
        for child in reversed(list(children)):
            stack.append((child, depth + 1, False))


def _canonical_json(value: Mapping[str, object] | Sequence[object]) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise H3BridgePlanError("bridge plan must be canonical plain JSON") from error


def _sha256(value: Mapping[str, object] | Sequence[object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _exact_dict(value: object, fields: frozenset[str], *, field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise H3BridgePlanError(f"{field} fields are not exact")
    return value


def _integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int = _MAX_FRAME_POSITION,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise H3BridgePlanError(
            f"{field} must be an integer from {minimum} through {maximum}"
        )
    return value


def _digest(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise H3BridgePlanError(
            f"{field} must be one lowercase sha256-prefixed digest"
        )
    return value


def _false(value: object, *, field: str) -> bool:
    if value is not False:
        raise H3BridgePlanError(f"{field} must remain false")
    return False


def _range(start: int, end: int) -> dict[str, int]:
    return {"start_frame": start, "end_frame_exclusive": end}


def _validate_range(
    value: object,
    *,
    field: str,
    minimum_start: int = 0,
    maximum_end: int = _MAX_FRAME_POSITION,
    allow_empty: bool = False,
) -> dict[str, int]:
    frame_range = _exact_dict(value, _RANGE_FIELDS, field=field)
    start = _integer(
        frame_range["start_frame"], field=f"{field}.start_frame", minimum=minimum_start
    )
    end_minimum = start if allow_empty else start + 1
    end = _integer(
        frame_range["end_frame_exclusive"],
        field=f"{field}.end_frame_exclusive",
        minimum=end_minimum,
        maximum=maximum_end,
    )
    return _range(start, end)


def _copy_source(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise H3BridgePlanError(f"{field} must be a source mapping")
    try:
        copied = dict(value)
    except (TypeError, ValueError) as error:
        raise H3BridgePlanError(f"{field} could not be copied") from error
    _require_plain_json(copied, field=field)
    source = _exact_dict(copied, _SOURCE_INPUT_FIELDS, field=field)
    commitment = _digest(source["sha256"], field=f"{field}.sha256")
    start = _integer(source["start_frame"], field=f"{field}.start_frame", minimum=0)
    end = _integer(
        source["end_frame_exclusive"],
        field=f"{field}.end_frame_exclusive",
        minimum=start + 1,
    )
    return {
        "sha256": commitment,
        "start_frame": start,
        "end_frame_exclusive": end,
        "frame_count": end - start,
    }


def _validate_source(value: object, *, field: str) -> dict[str, Any]:
    source = _exact_dict(value, _SOURCE_FIELDS, field=field)
    expected = _copy_source(
        {
            "sha256": source["sha256"],
            "start_frame": source["start_frame"],
            "end_frame_exclusive": source["end_frame_exclusive"],
        },
        field=field,
    )
    if source != expected:
        raise H3BridgePlanError(f"{field} half-open range drifted")
    return source


def _generated_frames(value: object) -> int:
    frames = _integer(
        value,
        field="generated_frames",
        minimum=H3_BRIDGE_MIN_GENERATED_FRAMES,
        maximum=H3_BRIDGE_MAX_GENERATED_FRAMES,
    )
    if not is_legal_h3_video_frame_count(frames):
        raise H3BridgePlanError(
            "generated_frames must satisfy the native 17*n+5 H3 frame grid"
        )
    return frames


def _trim(value: object, *, field: str, generated_frames: int) -> int:
    return _integer(value, field=field, minimum=0, maximum=generated_frames - 1)


def _generation(
    generated_frames: int,
    hidden_head_frames: int,
    hidden_tail_frames: int,
) -> dict[str, object]:
    published_start = hidden_head_frames
    published_end = generated_frames - hidden_tail_frames
    if published_start >= published_end:
        raise H3BridgePlanError("hidden trims must leave at least one published frame")
    generated_range = _range(0, generated_frames)
    hidden_head_range = _range(0, hidden_head_frames)
    hidden_tail_range = _range(published_end, generated_frames)
    published_range = _range(published_start, published_end)
    return {
        "generated_range": generated_range,
        "generated_frames": generated_frames,
        "generated_audio_ticks": audio_tick_at_frame(generated_frames),
        "hidden_head_range": hidden_head_range,
        "hidden_head_audio_ticks": audio_ticks_between_frames(0, hidden_head_frames),
        "hidden_tail_range": hidden_tail_range,
        "hidden_tail_audio_ticks": audio_ticks_between_frames(
            published_end, generated_frames
        ),
        "published_range": published_range,
        "published_frames": published_end - published_start,
        "published_audio_ticks": audio_ticks_between_frames(
            published_start, published_end
        ),
    }


def _seam(
    *,
    side: str,
    mode: object,
    owner: object,
    overlap_audio_ticks: object,
    maximum_overlap: int,
) -> dict[str, object]:
    if type(mode) is not str or mode not in _SEAM_MODES:
        raise H3BridgePlanError(f"{side}_seam_mode is not supported")
    overlap = _integer(
        overlap_audio_ticks,
        field=f"{side}_seam_overlap_audio_ticks",
        minimum=0,
        maximum=maximum_overlap,
    )
    allowed = _LEFT_HARD_CUT_OWNERS if side == "left" else _RIGHT_HARD_CUT_OWNERS
    if mode == "hard_cut":
        if type(owner) is not str or owner not in allowed:
            raise H3BridgePlanError(f"{side} hard-cut audio owner is not supported")
        if overlap != 0:
            raise H3BridgePlanError(f"{side} hard-cut seam cannot overlap audio")
    else:
        if owner != "shared":
            raise H3BridgePlanError(f"{side} crossfade audio owner must be shared")
        if overlap < 1:
            raise H3BridgePlanError(f"{side} crossfade requires a positive overlap")
    return {"mode": mode, "owner": owner, "overlap_audio_ticks": overlap}


def _audio(
    *,
    bridge_mode: object,
    drive_track_sha256: object,
    left_seam_mode: object,
    left_seam_owner: object,
    left_seam_overlap_audio_ticks: object,
    right_seam_mode: object,
    right_seam_owner: object,
    right_seam_overlap_audio_ticks: object,
    generation: Mapping[str, object],
) -> dict[str, object]:
    if type(bridge_mode) is not str or bridge_mode not in _BRIDGE_AUDIO_MODES:
        raise H3BridgePlanError("bridge_audio_mode is not supported")
    track = None
    if drive_track_sha256 is not None:
        track = _digest(drive_track_sha256, field="drive_track_sha256")
    left = _seam(
        side="left",
        mode=left_seam_mode,
        owner=left_seam_owner,
        overlap_audio_ticks=left_seam_overlap_audio_ticks,
        maximum_overlap=int(generation["hidden_head_audio_ticks"]),
    )
    right = _seam(
        side="right",
        mode=right_seam_mode,
        owner=right_seam_owner,
        overlap_audio_ticks=right_seam_overlap_audio_ticks,
        maximum_overlap=int(generation["hidden_tail_audio_ticks"]),
    )
    uses_drive_track = (
        bridge_mode == "drive_track"
        or left["owner"] == "drive_track"
        or right["owner"] == "drive_track"
    )
    if uses_drive_track != (track is not None):
        raise H3BridgePlanError(
            "drive_track_sha256 must be present exactly when drive-track audio is used"
        )
    return {
        "bridge_mode": bridge_mode,
        "drive_track_sha256": track,
        "left_seam": left,
        "right_seam": right,
    }


def _assembly(generation: Mapping[str, object]) -> dict[str, object]:
    return {
        "order": ["clip_a", "published_bridge", "clip_b"],
        "bridge_generated_range": dict(generation["generated_range"]),
        "bridge_published_range": dict(generation["published_range"]),
        "hidden_ranges": [
            dict(generation["hidden_head_range"]),
            dict(generation["hidden_tail_range"]),
        ],
    }


def _recovery_payload(plan: Mapping[str, object]) -> dict[str, object]:
    return {
        key: plan[key]
        for key in (
            "kind",
            "schema",
            "version",
            "conditioning_family",
            "clock",
            "sources",
            "generation",
            "audio",
            "assembly",
            "execution_available",
            "automatic_fallback",
        )
    }


def plan_h3_bridge(
    a_tail: object,
    b_head: object,
    *,
    generated_frames: object,
    hidden_head_frames: object = 0,
    hidden_tail_frames: object = 0,
    bridge_audio_mode: object = "generated",
    drive_track_sha256: object = None,
    left_seam_mode: object = "hard_cut",
    left_seam_owner: object = "clip_a",
    left_seam_overlap_audio_ticks: object = 0,
    right_seam_mode: object = "hard_cut",
    right_seam_owner: object = "clip_b",
    right_seam_overlap_audio_ticks: object = 0,
    reroll_index: object = 0,
    conditioning_family: object = H3_BRIDGE_CONDITIONING_FAMILY,
    execution_available: object = False,
    runtime_available: object = False,
    automatic_fallback: object = False,
) -> dict[str, Any]:
    """Build one inert bridge plan with a bridge-only reroll boundary."""

    if conditioning_family != H3_BRIDGE_CONDITIONING_FAMILY or type(conditioning_family) is not str:
        raise H3BridgePlanError("native Bridge requires Ref2VA conditioning")
    _false(execution_available, field="execution_available")
    _false(runtime_available, field="runtime_available")
    _false(automatic_fallback, field="automatic_fallback")
    frames = _generated_frames(generated_frames)
    head = _trim(hidden_head_frames, field="hidden_head_frames", generated_frames=frames)
    tail = _trim(hidden_tail_frames, field="hidden_tail_frames", generated_frames=frames)
    generation = _generation(frames, head, tail)
    audio = _audio(
        bridge_mode=bridge_audio_mode,
        drive_track_sha256=drive_track_sha256,
        left_seam_mode=left_seam_mode,
        left_seam_owner=left_seam_owner,
        left_seam_overlap_audio_ticks=left_seam_overlap_audio_ticks,
        right_seam_mode=right_seam_mode,
        right_seam_owner=right_seam_owner,
        right_seam_overlap_audio_ticks=right_seam_overlap_audio_ticks,
        generation=generation,
    )
    reroll = _integer(
        reroll_index, field="reroll_index", minimum=0, maximum=_MAX_REROLL_INDEX
    )
    document: dict[str, Any] = {
        "kind": H3_BRIDGE_PLAN_KIND,
        "schema": H3_BRIDGE_PLAN_SCHEMA,
        "version": H3_BRIDGE_PLAN_VERSION,
        "conditioning_family": H3_BRIDGE_CONDITIONING_FAMILY,
        "clock": {
            "fps": H3_NATIVE_FPS,
            "audio_ticks_per_second": H3_AUDIO_TICKS_PER_SECOND,
            "video_frame_step": H3_VIDEO_FRAME_STEP,
            "video_frame_offset": H3_VIDEO_FRAME_OFFSET,
        },
        "sources": {
            "a_tail": _copy_source(a_tail, field="a_tail"),
            "b_head": _copy_source(b_head, field="b_head"),
        },
        "generation": generation,
        "audio": audio,
        "assembly": _assembly(generation),
        "execution_available": False,
        "automatic_fallback": False,
    }
    recovery_sha256 = _sha256(_recovery_payload(document))
    document["recovery_sha256"] = recovery_sha256
    document["reroll"] = {
        "scope": "bridge_only",
        "index": reroll,
        "identity_sha256": _sha256(
            {"recovery_sha256": recovery_sha256, "reroll_index": reroll}
        ),
    }
    document["plan_sha256"] = _sha256(document)
    return validate_h3_bridge_plan(document)


def validate_h3_bridge_plan(value: object) -> dict[str, Any]:
    """Validate every field and return an independent canonical plan copy."""

    _require_plain_json(value, field="bridge plan")
    plan = _exact_dict(value, _TOP_LEVEL_FIELDS, field="bridge plan")
    if plan["kind"] != H3_BRIDGE_PLAN_KIND:
        raise H3BridgePlanError("bridge plan kind drifted")
    if plan["schema"] != H3_BRIDGE_PLAN_SCHEMA:
        raise H3BridgePlanError("bridge plan schema drifted")
    if type(plan["version"]) is not int or plan["version"] != H3_BRIDGE_PLAN_VERSION:
        raise H3BridgePlanError("bridge plan version drifted")
    if plan["conditioning_family"] != H3_BRIDGE_CONDITIONING_FAMILY:
        raise H3BridgePlanError("bridge conditioning family drifted")
    clock = _exact_dict(plan["clock"], _CLOCK_FIELDS, field="clock")
    if clock != {
        "fps": H3_NATIVE_FPS,
        "audio_ticks_per_second": H3_AUDIO_TICKS_PER_SECOND,
        "video_frame_step": H3_VIDEO_FRAME_STEP,
        "video_frame_offset": H3_VIDEO_FRAME_OFFSET,
    }:
        raise H3BridgePlanError("bridge clock drifted")
    _false(plan["execution_available"], field="execution_available")
    _false(plan["automatic_fallback"], field="automatic_fallback")

    sources = _exact_dict(plan["sources"], _SOURCES_FIELDS, field="sources")
    _validate_source(sources["a_tail"], field="sources.a_tail")
    _validate_source(sources["b_head"], field="sources.b_head")

    generation = _exact_dict(plan["generation"], _GENERATION_FIELDS, field="generation")
    frames = _generated_frames(generation["generated_frames"])
    generated_range = _validate_range(
        generation["generated_range"], field="generation.generated_range"
    )
    if generated_range != _range(0, frames):
        raise H3BridgePlanError("generated_range drifted")
    head_range = _validate_range(
        generation["hidden_head_range"],
        field="generation.hidden_head_range",
        allow_empty=True,
    )
    tail_range = _validate_range(
        generation["hidden_tail_range"],
        field="generation.hidden_tail_range",
        allow_empty=True,
    )
    published_range = _validate_range(
        generation["published_range"], field="generation.published_range"
    )
    expected_generation = _generation(
        frames,
        head_range["end_frame_exclusive"],
        frames - tail_range["start_frame"],
    )
    if generation != expected_generation:
        raise H3BridgePlanError("bridge generation geometry drifted")

    audio = _exact_dict(plan["audio"], _AUDIO_FIELDS, field="audio")
    left = _exact_dict(audio["left_seam"], _SEAM_FIELDS, field="audio.left_seam")
    right = _exact_dict(audio["right_seam"], _SEAM_FIELDS, field="audio.right_seam")
    expected_audio = _audio(
        bridge_mode=audio["bridge_mode"],
        drive_track_sha256=audio["drive_track_sha256"],
        left_seam_mode=left["mode"],
        left_seam_owner=left["owner"],
        left_seam_overlap_audio_ticks=left["overlap_audio_ticks"],
        right_seam_mode=right["mode"],
        right_seam_owner=right["owner"],
        right_seam_overlap_audio_ticks=right["overlap_audio_ticks"],
        generation=generation,
    )
    if audio != expected_audio:
        raise H3BridgePlanError("bridge audio policy drifted")

    assembly = _exact_dict(plan["assembly"], _ASSEMBLY_FIELDS, field="assembly")
    if assembly != _assembly(generation):
        raise H3BridgePlanError("final A-to-bridge-to-B assembly drifted")

    recovery = _digest(plan["recovery_sha256"], field="recovery_sha256")
    expected_recovery = _sha256(_recovery_payload(plan))
    if not hmac.compare_digest(recovery, expected_recovery):
        raise H3BridgePlanError("bridge recovery digest drifted")
    reroll = _exact_dict(plan["reroll"], _REROLL_FIELDS, field="reroll")
    if reroll["scope"] != "bridge_only":
        raise H3BridgePlanError("reroll scope drifted")
    reroll_index = _integer(
        reroll["index"], field="reroll.index", minimum=0, maximum=_MAX_REROLL_INDEX
    )
    identity = _digest(reroll["identity_sha256"], field="reroll.identity_sha256")
    expected_identity = _sha256(
        {"recovery_sha256": recovery, "reroll_index": reroll_index}
    )
    if not hmac.compare_digest(identity, expected_identity):
        raise H3BridgePlanError("bridge reroll identity drifted")

    digest = _digest(plan["plan_sha256"], field="plan_sha256")
    unsigned = {key: item for key, item in plan.items() if key != "plan_sha256"}
    if not hmac.compare_digest(digest, _sha256(unsigned)):
        raise H3BridgePlanError("bridge plan digest drifted")
    return json.loads(_canonical_json(plan).decode("ascii"))


def canonical_h3_bridge_plan(value: object) -> bytes:
    """Return canonical replay bytes after complete bridge-plan validation."""

    return _canonical_json(validate_h3_bridge_plan(value))


__all__ = [
    "H3_BRIDGE_CONDITIONING_FAMILY",
    "H3_BRIDGE_MAX_GENERATED_FRAMES",
    "H3_BRIDGE_MIN_GENERATED_FRAMES",
    "H3_BRIDGE_PLAN_KIND",
    "H3_BRIDGE_PLAN_SCHEMA",
    "H3_BRIDGE_PLAN_VERSION",
    "H3BridgePlanError",
    "canonical_h3_bridge_plan",
    "plan_h3_bridge",
    "validate_h3_bridge_plan",
]
