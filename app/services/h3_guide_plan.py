"""Tensor-free planning contract for MiniMax H3 arbitrary-frame guides.

The geometry in this module is independently derived from ComfyUI's
``MiniMaxH3AddGuide`` implementation at revision
``e01fb4c56b7a88149d469b99cbbfe3223d715054``.  It plans committed image,
video, and audio sources on the native H3 timeline without loading media or
claiming that Maestro can execute the resulting conditioning yet.

Only source digests and integer geometry enter the plan.  Paths, prompts,
tensors, decoded content, and semantic roles are deliberately outside this
contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any, Final

from services.h3_native_continuation import (
    audio_tick_at_frame,
    is_legal_h3_video_frame_count,
)


H3_GUIDE_PLAN_KIND: Final = "minimax_h3_add_guide"
H3_GUIDE_PLAN_SCHEMA: Final = "maestro.h3.guide-plan"
H3_GUIDE_PLAN_VERSION: Final = 1
H3_GUIDE_SOURCE_REVISION: Final = (
    "e01fb4c56b7a88149d469b99cbbfe3223d715054"
)
H3_GUIDE_CONDITIONING_FAMILY: Final = "fl2va_timeline"
H3_GUIDE_MAX_TARGET_FRAMES: Final = 345

_MAX_GUIDES = 64
_MAX_SOURCE_UNITS = 10_000_000
_MAX_JSON_DEPTH = 32
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")

_TOP_LEVEL_FIELDS = frozenset(
    {
        "kind",
        "schema",
        "version",
        "source",
        "conditioning_family",
        "target_frames",
        "target_audio_ticks",
        "execution_available",
        "automatic_fallback",
        "continuation_composition_available",
        "guides",
        "plan_sha256",
    }
)
_SOURCE_FIELDS = frozenset({"component", "revision"})
_INPUT_FIELDS = frozenset({"frame_idx", "visual", "audio"})
_INPUT_MEDIA_FIELDS = frozenset({"sha256", "count"})
_GUIDE_FIELDS = frozenset(
    {
        "sequence_index",
        "authored_frame_idx",
        "resolved_frame_idx",
        "visual",
        "audio",
    }
)
_VISUAL_FIELDS = frozenset(
    {
        "sha256",
        "original_frame_count",
        "used_frame_count",
        "selection",
        "end_frame_exclusive",
    }
)
_AUDIO_FIELDS = frozenset(
    {
        "sha256",
        "original_tick_count",
        "capacity_tick_count",
        "used_tick_count",
    }
)


class H3GuidePlanError(ValueError):
    """Raised when an arbitrary-frame guide plan is not source-exact."""


def _require_plain_json(value: object, *, field: str) -> None:
    """Reject scalar/container subclasses before semantic comparisons."""

    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    while stack:
        item, depth, exiting = stack.pop()
        if exiting:
            active_containers.remove(id(item))
            continue
        if item is None or type(item) in (str, int, bool):
            continue
        if type(item) not in (list, dict):
            raise H3GuidePlanError(f"{field} must contain exact plain JSON values")
        if depth >= _MAX_JSON_DEPTH:
            raise H3GuidePlanError(f"{field} exceeds the bounded JSON depth")
        if type(item) is dict and not all(type(key) is str for key in item):
            raise H3GuidePlanError(f"{field} must contain exact plain JSON values")
        identity = id(item)
        if identity in active_containers:
            raise H3GuidePlanError(f"{field} cannot contain a JSON cycle")
        active_containers.add(identity)
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
        raise H3GuidePlanError("guide plan must be canonical plain JSON") from error


def _sha256(value: Mapping[str, object] | Sequence[object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _exact_dict(
    value: object,
    fields: frozenset[str],
    *,
    field: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise H3GuidePlanError(f"{field} fields are not exact")
    return value


def _integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int = _MAX_SOURCE_UNITS,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise H3GuidePlanError(
            f"{field} must be an integer from {minimum} through {maximum}"
        )
    return value


def _digest(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise H3GuidePlanError(
            f"{field} must be one lowercase sha256-prefixed digest"
        )
    return value


def _false(value: object, *, field: str) -> bool:
    if value is not False:
        raise H3GuidePlanError(f"{field} must remain false")
    return False


def _target_frames(value: object) -> int:
    target = _integer(
        value,
        field="target_frames",
        minimum=5,
        maximum=H3_GUIDE_MAX_TARGET_FRAMES,
    )
    if not is_legal_h3_video_frame_count(target):
        raise H3GuidePlanError(
            "target_frames must satisfy the native 17*n+5 H3 frame grid"
        )
    return target


def _resolve_frame_idx(value: object, *, target_frames: int) -> int:
    authored = _integer(
        value,
        field="frame_idx",
        minimum=-target_frames,
        maximum=target_frames - 1,
    )
    return target_frames + authored if authored < 0 else authored


def _visual_used_frames(original_frame_count: int) -> tuple[int, str]:
    if original_frame_count < 5:
        return 1, "first_image"
    return 5 + 17 * ((original_frame_count - 5) // 17), "legal_prefix"


def _audio_capacity_ticks(*, target_audio_ticks: int, frame_idx: int) -> int:
    # This is intentionally the source expression, not a subtraction between
    # two independently rounded H3 audio boundaries.
    remaining = Fraction(target_audio_ticks, 1) - Fraction(5 * frame_idx, 3)
    return remaining.numerator // remaining.denominator


def _input_media(
    value: object,
    *,
    field: str,
) -> tuple[str, int] | None:
    if value is None:
        return None
    media = _exact_dict(value, _INPUT_MEDIA_FIELDS, field=field)
    return (
        _digest(media["sha256"], field=f"{field}.sha256"),
        _integer(media["count"], field=f"{field}.count", minimum=1),
    )


def _copy_inputs(value: object) -> list[dict[str, object]] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise H3GuidePlanError("inputs must be a sequence of guide mappings")
    if len(value) == 0:
        return None
    if len(value) > _MAX_GUIDES:
        raise H3GuidePlanError(f"inputs cannot contain more than {_MAX_GUIDES} guides")
    copied: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise H3GuidePlanError(f"inputs[{index}] must be a guide mapping")
        try:
            copied.append(dict(item))
        except (TypeError, ValueError) as error:
            raise H3GuidePlanError(f"inputs[{index}] could not be copied") from error
    return copied


def _planned_guide(
    value: object,
    *,
    sequence_index: int,
    target_frames: int,
    target_audio_ticks: int,
) -> dict[str, object]:
    guide = _exact_dict(value, _INPUT_FIELDS, field=f"inputs[{sequence_index}]")
    authored = _integer(
        guide["frame_idx"],
        field=f"inputs[{sequence_index}].frame_idx",
        minimum=-target_frames,
        maximum=target_frames - 1,
    )
    resolved = _resolve_frame_idx(authored, target_frames=target_frames)
    visual_source = _input_media(
        guide["visual"], field=f"inputs[{sequence_index}].visual"
    )
    audio_source = _input_media(
        guide["audio"], field=f"inputs[{sequence_index}].audio"
    )
    if visual_source is None and audio_source is None:
        raise H3GuidePlanError(
            f"inputs[{sequence_index}] requires a visual and/or audio commitment"
        )

    visual: dict[str, object] | None = None
    if visual_source is not None:
        commitment, original_frames = visual_source
        used_frames, selection = _visual_used_frames(original_frames)
        end = resolved + used_frames
        if end > target_frames:
            raise H3GuidePlanError(
                f"inputs[{sequence_index}] visual span exceeds target_frames"
            )
        visual = {
            "sha256": commitment,
            "original_frame_count": original_frames,
            "used_frame_count": used_frames,
            "selection": selection,
            "end_frame_exclusive": end,
        }

    audio: dict[str, object] | None = None
    if audio_source is not None:
        commitment, original_ticks = audio_source
        capacity = _audio_capacity_ticks(
            target_audio_ticks=target_audio_ticks,
            frame_idx=resolved,
        )
        if capacity <= 0:
            raise H3GuidePlanError(
                f"inputs[{sequence_index}] leaves no audio capacity"
            )
        audio = {
            "sha256": commitment,
            "original_tick_count": original_ticks,
            "capacity_tick_count": capacity,
            "used_tick_count": min(original_ticks, capacity),
        }

    return {
        "sequence_index": sequence_index,
        "authored_frame_idx": authored,
        "resolved_frame_idx": resolved,
        "visual": visual,
        "audio": audio,
    }


def _validate_planned_guide(
    value: object,
    *,
    sequence_index: int,
    target_frames: int,
    target_audio_ticks: int,
) -> dict[str, Any]:
    guide = _exact_dict(value, _GUIDE_FIELDS, field=f"guides[{sequence_index}]")
    if guide["sequence_index"] != sequence_index:
        raise H3GuidePlanError("guide sequence order drifted")
    source_input: dict[str, object] = {
        "frame_idx": guide["authored_frame_idx"],
        "visual": None,
        "audio": None,
    }
    visual = guide["visual"]
    if visual is not None:
        planned_visual = _exact_dict(
            visual, _VISUAL_FIELDS, field=f"guides[{sequence_index}].visual"
        )
        source_input["visual"] = {
            "sha256": planned_visual["sha256"],
            "count": planned_visual["original_frame_count"],
        }
    audio = guide["audio"]
    if audio is not None:
        planned_audio = _exact_dict(
            audio, _AUDIO_FIELDS, field=f"guides[{sequence_index}].audio"
        )
        source_input["audio"] = {
            "sha256": planned_audio["sha256"],
            "count": planned_audio["original_tick_count"],
        }
    expected = _planned_guide(
        source_input,
        sequence_index=sequence_index,
        target_frames=target_frames,
        target_audio_ticks=target_audio_ticks,
    )
    if guide != expected:
        raise H3GuidePlanError(f"guides[{sequence_index}] geometry drifted")
    return guide


def plan_h3_guide_inputs(
    target_frames: object,
    inputs: object = None,
    *,
    conditioning_family: object = H3_GUIDE_CONDITIONING_FAMILY,
    execution_available: object = False,
    runtime_available: object = False,
    automatic_fallback: object = False,
    continuation_composition_available: object = False,
    opening_guide_present: object = False,
) -> dict[str, Any] | None:
    """Build one inert arbitrary-frame guide plan, or ``None`` when unused.

    Returning ``None`` for no inputs keeps AddGuide optional: legacy H3 jobs do
    not gain a new plan, conditioning family, or execution claim merely by
    importing this module.
    """

    copied_inputs = _copy_inputs(inputs)
    if copied_inputs is None:
        return None
    _require_plain_json(copied_inputs, field="inputs")
    target = _target_frames(target_frames)
    if (
        type(conditioning_family) is not str
        or conditioning_family != H3_GUIDE_CONDITIONING_FAMILY
    ):
        raise H3GuidePlanError(
            "arbitrary-frame guides require fl2va_timeline conditioning; "
            "Ref2VA is not supported"
        )
    _false(execution_available, field="execution_available")
    _false(runtime_available, field="runtime_available")
    _false(automatic_fallback, field="automatic_fallback")
    _false(
        continuation_composition_available,
        field="continuation_composition_available",
    )
    _false(opening_guide_present, field="opening_guide_present")

    target_audio_ticks = audio_tick_at_frame(target)
    document: dict[str, Any] = {
        "kind": H3_GUIDE_PLAN_KIND,
        "schema": H3_GUIDE_PLAN_SCHEMA,
        "version": H3_GUIDE_PLAN_VERSION,
        "source": {
            "component": "MiniMaxH3AddGuide",
            "revision": H3_GUIDE_SOURCE_REVISION,
        },
        "conditioning_family": H3_GUIDE_CONDITIONING_FAMILY,
        "target_frames": target,
        "target_audio_ticks": target_audio_ticks,
        "execution_available": False,
        "automatic_fallback": False,
        "continuation_composition_available": False,
        "guides": [
            _planned_guide(
                item,
                sequence_index=index,
                target_frames=target,
                target_audio_ticks=target_audio_ticks,
            )
            for index, item in enumerate(copied_inputs)
        ],
    }
    document["plan_sha256"] = _sha256(document)
    return validate_h3_guide_plan(document)


def validate_h3_guide_plan(value: object) -> dict[str, Any]:
    """Validate and return an independent canonical copy of a guide plan."""

    _require_plain_json(value, field="guide plan")
    plan = _exact_dict(value, _TOP_LEVEL_FIELDS, field="guide plan")
    if plan["kind"] != H3_GUIDE_PLAN_KIND:
        raise H3GuidePlanError("guide plan kind drifted")
    if plan["schema"] != H3_GUIDE_PLAN_SCHEMA:
        raise H3GuidePlanError("guide plan schema drifted")
    if type(plan["version"]) is not int or plan["version"] != H3_GUIDE_PLAN_VERSION:
        raise H3GuidePlanError("guide plan version drifted")
    source = _exact_dict(plan["source"], _SOURCE_FIELDS, field="source")
    if source != {
        "component": "MiniMaxH3AddGuide",
        "revision": H3_GUIDE_SOURCE_REVISION,
    }:
        raise H3GuidePlanError("guide plan source drifted")
    if plan["conditioning_family"] != H3_GUIDE_CONDITIONING_FAMILY:
        raise H3GuidePlanError("guide plan conditioning family drifted")
    _false(plan["execution_available"], field="execution_available")
    _false(plan["automatic_fallback"], field="automatic_fallback")
    _false(
        plan["continuation_composition_available"],
        field="continuation_composition_available",
    )

    target = _target_frames(plan["target_frames"])
    target_audio_ticks = audio_tick_at_frame(target)
    if plan["target_audio_ticks"] != target_audio_ticks:
        raise H3GuidePlanError("target_audio_ticks drifted")
    guides = plan["guides"]
    if type(guides) is not list or not 1 <= len(guides) <= _MAX_GUIDES:
        raise H3GuidePlanError("guides must be a non-empty bounded list")
    for index, guide in enumerate(guides):
        _validate_planned_guide(
            guide,
            sequence_index=index,
            target_frames=target,
            target_audio_ticks=target_audio_ticks,
        )

    _digest(plan["plan_sha256"], field="plan_sha256")
    unsigned = {key: item for key, item in plan.items() if key != "plan_sha256"}
    expected = _sha256(unsigned)
    if not hmac.compare_digest(plan["plan_sha256"], expected):
        raise H3GuidePlanError("guide plan digest drifted")
    return json.loads(_canonical_json(plan).decode("ascii"))


def canonical_h3_guide_plan(value: object) -> bytes:
    """Return canonical replay bytes after complete guide-plan validation."""

    return _canonical_json(validate_h3_guide_plan(value))


__all__ = [
    "H3_GUIDE_CONDITIONING_FAMILY",
    "H3_GUIDE_MAX_TARGET_FRAMES",
    "H3_GUIDE_PLAN_KIND",
    "H3_GUIDE_PLAN_SCHEMA",
    "H3_GUIDE_PLAN_VERSION",
    "H3_GUIDE_SOURCE_REVISION",
    "H3GuidePlanError",
    "canonical_h3_guide_plan",
    "plan_h3_guide_inputs",
    "validate_h3_guide_plan",
]
