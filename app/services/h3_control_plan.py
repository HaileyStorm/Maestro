"""Sealed, tensor-free request plans for MiniMax H3 Fun ControlNet Union.

The contract records one committed control video and deterministic H3 geometry.
It deliberately does not load media, models, tensors, paths, or prompts, and it
does not claim that Maestro can execute the request yet.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final


H3_CONTROL_PLAN_KIND: Final = "minimax_h3_fun_control_union"
H3_CONTROL_PLAN_SCHEMA: Final = "maestro.h3.control-plan"
H3_CONTROL_PLAN_VERSION: Final = 1
H3_CONTROL_SOURCE_REVISION: Final = (
    "6419c27ece80f330826ae4439fa9c5910c475ccf"
)
H3_CONTROL_BASE_FAMILY: Final = "minimax_h3"
H3_CONTROL_FPS: Final = 24
H3_CONTROL_BLOCKS: Final = (0, 10, 20, 30, 40)
H3_CONTROL_IN_DIM: Final = 49
H3_CONTROL_MAX_FRAMES: Final = 345
H3_CONTROL_KINDS: Final = frozenset(
    {"canny", "depth", "hed", "mlsd", "pose", "inpaint"}
)

_MAX_DIMENSION = 32_768
_MAX_SOURCE_FRAMES = 10_000_000
_MAX_JSON_DEPTH = 24
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")

_TOP_LEVEL_FIELDS = frozenset(
    {
        "kind",
        "schema",
        "version",
        "implementation",
        "base_family",
        "fps",
        "execution_available",
        "automatic_fallback",
        "geometry",
        "control",
        "plan_sha256",
    }
)
_IMPLEMENTATION_FIELDS = frozenset({"component", "revision"})
_GEOMETRY_FIELDS = frozenset(
    {
        "source_width",
        "source_height",
        "source_frame_count",
        "target_width_bound",
        "target_height_bound",
        "width",
        "height",
        "frame_count",
        "frame_selection",
        "aspect_fit",
    }
)
_CONTROL_FIELDS = frozenset(
    {
        "version",
        "kind",
        "source_sha256",
        "mask_sha256",
        "strength",
        "guidance_scale",
        "control_blocks",
        "control_in_dim",
        "control_apply_audio",
    }
)


class H3ControlPlanError(ValueError):
    """Raised when an H3 control request is not canonical and source-exact."""


def _require_plain_json(value: object, *, field: str) -> None:
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active: set[int] = set()
    while stack:
        item, depth, exiting = stack.pop()
        if exiting:
            active.remove(id(item))
            continue
        if item is None or type(item) in (str, int, float, bool):
            if type(item) is float and not math.isfinite(item):
                raise H3ControlPlanError(f"{field} must contain finite JSON numbers")
            continue
        if type(item) not in (list, dict):
            raise H3ControlPlanError(f"{field} must contain exact plain JSON values")
        if depth >= _MAX_JSON_DEPTH:
            raise H3ControlPlanError(f"{field} exceeds the bounded JSON depth")
        if type(item) is dict and not all(type(key) is str for key in item):
            raise H3ControlPlanError(f"{field} must contain exact plain JSON values")
        identity = id(item)
        if identity in active:
            raise H3ControlPlanError(f"{field} cannot contain a JSON cycle")
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
        raise H3ControlPlanError("control plan must be canonical plain JSON") from error


def _sha256(value: Mapping[str, object] | Sequence[object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _exact_dict(
    value: object,
    fields: frozenset[str],
    *,
    field: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise H3ControlPlanError(f"{field} fields are not exact")
    return value


def _integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise H3ControlPlanError(
            f"{field} must be an integer from {minimum} through {maximum}"
        )
    return value


def _multiple_of_32(value: object, *, field: str) -> int:
    dimension = _integer(
        value,
        field=field,
        minimum=32,
        maximum=_MAX_DIMENSION,
    )
    if dimension % 32:
        raise H3ControlPlanError(f"{field} must be a multiple of 32")
    return dimension


def _digest(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise H3ControlPlanError(
            f"{field} must be one lowercase sha256-prefixed digest"
        )
    return value


def _false(value: object, *, field: str) -> bool:
    if value is not False:
        raise H3ControlPlanError(f"{field} must remain false")
    return False


def _strength(value: object) -> float:
    if type(value) not in (int, float):
        raise H3ControlPlanError("strength must be a finite number from 0 through 1")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise H3ControlPlanError("strength must be a finite number from 0 through 1")
    return normalized


def _control_kind(value: object) -> str:
    if type(value) is not str or value not in H3_CONTROL_KINDS:
        raise H3ControlPlanError(
            "control_kind must be exactly canny, depth, hed, mlsd, pose, or inpaint"
        )
    return value


def _legal_frame_prefix(value: object) -> tuple[int, int]:
    source = _integer(
        value,
        field="source_frame_count",
        minimum=5,
        maximum=_MAX_SOURCE_FRAMES,
    )
    bounded = min(source, H3_CONTROL_MAX_FRAMES)
    return source, 5 + 17 * ((bounded - 5) // 17)


def _aspect_fit(
    source_width: object,
    source_height: object,
    target_width: object,
    target_height: object,
) -> tuple[int, int, int, int, int, int]:
    source_w = _integer(
        source_width,
        field="source_width",
        minimum=1,
        maximum=_MAX_DIMENSION,
    )
    source_h = _integer(
        source_height,
        field="source_height",
        minimum=1,
        maximum=_MAX_DIMENSION,
    )
    bound_w = _multiple_of_32(target_width, field="target_width")
    bound_h = _multiple_of_32(target_height, field="target_height")

    if bound_w * source_h <= bound_h * source_w:
        width = bound_w
        height = (
            (bound_w * source_h + source_w * 16) // (source_w * 32)
        ) * 32
    else:
        height = bound_h
        width = (
            (bound_h * source_w + source_h * 16) // (source_h * 32)
        ) * 32
    if width < 32 or height < 32:
        raise H3ControlPlanError(
            "source aspect cannot fit the target bounds at a 32-pixel minimum"
        )
    return source_w, source_h, bound_w, bound_h, width, height


def _mask(kind: str, value: object) -> str | None:
    if kind == "inpaint":
        return _digest(value, field="mask_sha256")
    if value is not None:
        raise H3ControlPlanError("mask_sha256 is only valid for inpaint control")
    return None


def plan_h3_control_request(
    *,
    control_kind: object,
    source_sha256: object,
    source_width: object,
    source_height: object,
    source_frame_count: object,
    target_width: object,
    target_height: object,
    strength: object,
    mask_sha256: object = None,
    base_family: object = H3_CONTROL_BASE_FAMILY,
    fps: object = H3_CONTROL_FPS,
    execution_available: object = False,
    automatic_fallback: object = False,
) -> dict[str, Any]:
    """Build one inert v1 H3 control request and seal its canonical geometry."""

    kind = _control_kind(control_kind)
    source_digest = _digest(source_sha256, field="source_sha256")
    mask_digest = _mask(kind, mask_sha256)
    normalized_strength = _strength(strength)
    if type(base_family) is not str or base_family != H3_CONTROL_BASE_FAMILY:
        raise H3ControlPlanError("control requests require base family minimax_h3")
    if type(fps) is not int or fps != H3_CONTROL_FPS:
        raise H3ControlPlanError("control requests require exactly 24 fps")
    _false(execution_available, field="execution_available")
    _false(automatic_fallback, field="automatic_fallback")

    source_w, source_h, bound_w, bound_h, width, height = _aspect_fit(
        source_width,
        source_height,
        target_width,
        target_height,
    )
    original_frames, frame_count = _legal_frame_prefix(source_frame_count)
    document: dict[str, Any] = {
        "kind": H3_CONTROL_PLAN_KIND,
        "schema": H3_CONTROL_PLAN_SCHEMA,
        "version": H3_CONTROL_PLAN_VERSION,
        "implementation": {
            "component": "MiniMax-H3-Fun-Controlnet-Union",
            "revision": H3_CONTROL_SOURCE_REVISION,
        },
        "base_family": H3_CONTROL_BASE_FAMILY,
        "fps": H3_CONTROL_FPS,
        "execution_available": False,
        "automatic_fallback": False,
        "geometry": {
            "source_width": source_w,
            "source_height": source_h,
            "source_frame_count": original_frames,
            "target_width_bound": bound_w,
            "target_height_bound": bound_h,
            "width": width,
            "height": height,
            "frame_count": frame_count,
            "frame_selection": "legal_prefix",
            "aspect_fit": "contain_nearest_32",
        },
        "control": {
            "version": 1,
            "kind": kind,
            "source_sha256": source_digest,
            "mask_sha256": mask_digest,
            "strength": normalized_strength,
            "guidance_scale": 1.0,
            "control_blocks": list(H3_CONTROL_BLOCKS),
            "control_in_dim": H3_CONTROL_IN_DIM,
            "control_apply_audio": False,
        },
    }
    document["plan_sha256"] = _sha256(document)
    return validate_h3_control_plan(document)


def validate_h3_control_plan(value: object) -> dict[str, Any]:
    """Validate and return an independent canonical copy of a control plan."""

    _require_plain_json(value, field="control plan")
    plan = _exact_dict(value, _TOP_LEVEL_FIELDS, field="control plan")
    if plan["kind"] != H3_CONTROL_PLAN_KIND:
        raise H3ControlPlanError("control plan kind drifted")
    if plan["schema"] != H3_CONTROL_PLAN_SCHEMA:
        raise H3ControlPlanError("control plan schema drifted")
    if type(plan["version"]) is not int or plan["version"] != H3_CONTROL_PLAN_VERSION:
        raise H3ControlPlanError("control plan version drifted")
    implementation = _exact_dict(
        plan["implementation"],
        _IMPLEMENTATION_FIELDS,
        field="implementation",
    )
    if implementation != {
        "component": "MiniMax-H3-Fun-Controlnet-Union",
        "revision": H3_CONTROL_SOURCE_REVISION,
    }:
        raise H3ControlPlanError("control plan implementation drifted")
    if type(plan["base_family"]) is not str or plan["base_family"] != H3_CONTROL_BASE_FAMILY:
        raise H3ControlPlanError("control plan base family drifted")
    if type(plan["fps"]) is not int or plan["fps"] != H3_CONTROL_FPS:
        raise H3ControlPlanError("control plan fps drifted")
    _false(plan["execution_available"], field="execution_available")
    _false(plan["automatic_fallback"], field="automatic_fallback")

    geometry = _exact_dict(plan["geometry"], _GEOMETRY_FIELDS, field="geometry")
    source_w, source_h, bound_w, bound_h, width, height = _aspect_fit(
        geometry["source_width"],
        geometry["source_height"],
        geometry["target_width_bound"],
        geometry["target_height_bound"],
    )
    original_frames, frame_count = _legal_frame_prefix(
        geometry["source_frame_count"]
    )
    expected_geometry = {
        "source_width": source_w,
        "source_height": source_h,
        "source_frame_count": original_frames,
        "target_width_bound": bound_w,
        "target_height_bound": bound_h,
        "width": width,
        "height": height,
        "frame_count": frame_count,
        "frame_selection": "legal_prefix",
        "aspect_fit": "contain_nearest_32",
    }
    if geometry != expected_geometry:
        raise H3ControlPlanError("control plan geometry drifted")

    control = _exact_dict(plan["control"], _CONTROL_FIELDS, field="control")
    if type(control["version"]) is not int or control["version"] != 1:
        raise H3ControlPlanError("control version drifted")
    control_kind = _control_kind(control["kind"])
    _digest(control["source_sha256"], field="source_sha256")
    _mask(control_kind, control["mask_sha256"])
    if type(control["strength"]) is not float:
        raise H3ControlPlanError("canonical strength must be a float")
    _strength(control["strength"])
    if type(control["guidance_scale"]) is not float or control["guidance_scale"] != 1.0:
        raise H3ControlPlanError("guidance_scale must remain 1.0")
    if type(control["control_blocks"]) is not list or control["control_blocks"] != list(H3_CONTROL_BLOCKS):
        raise H3ControlPlanError("control_blocks drifted")
    if type(control["control_in_dim"]) is not int or control["control_in_dim"] != H3_CONTROL_IN_DIM:
        raise H3ControlPlanError("control_in_dim must remain 49")
    _false(control["control_apply_audio"], field="control_apply_audio")

    _digest(plan["plan_sha256"], field="plan_sha256")
    unsigned = {key: item for key, item in plan.items() if key != "plan_sha256"}
    expected_digest = _sha256(unsigned)
    if not hmac.compare_digest(plan["plan_sha256"], expected_digest):
        raise H3ControlPlanError("control plan digest drifted")
    return json.loads(_canonical_json(plan).decode("ascii"))


def canonical_h3_control_plan(value: object) -> bytes:
    """Return canonical replay bytes after complete control-plan validation."""

    return _canonical_json(validate_h3_control_plan(value))


__all__ = [
    "H3_CONTROL_BASE_FAMILY",
    "H3_CONTROL_BLOCKS",
    "H3_CONTROL_FPS",
    "H3_CONTROL_IN_DIM",
    "H3_CONTROL_KINDS",
    "H3_CONTROL_MAX_FRAMES",
    "H3_CONTROL_PLAN_KIND",
    "H3_CONTROL_PLAN_SCHEMA",
    "H3_CONTROL_PLAN_VERSION",
    "H3_CONTROL_SOURCE_REVISION",
    "H3ControlPlanError",
    "canonical_h3_control_plan",
    "plan_h3_control_request",
    "validate_h3_control_plan",
]
