"""Quality-preserving MiniMax H3 denoise OOM relief.

Single-window H3 jobs currently have no launch-side resource retry boundary.
This ladder is the last-resort inner retry: keep aspect, references, and
upscale intent. Offload escalates before scheduler steps; native canvas
drops only after the same-canvas step ladder is exhausted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


H3_OOM_RELIEF_VERSION = 3
# Same-setup + offload + 2-step nibble 50→23 + a few canvas drops.
H3_OOM_RELIEF_MAX_ATTEMPTS = 36

# Standing default for H3 on a 32 GB class GPU: Profile 4+ (LowRAM_LowVRAM+).
# Profile 4 keeps more weights resident; 4.5 is the in-tree MMGP variant that
# disables asyncTransfers so occupancy stays lower without changing output.
H3_BASELINE_OFFLOAD_PROFILE = 4.5
# Relief-only further escalation after the baseline is already applied.
H3_RELIEF_OFFLOAD_LADDER = (4.5, 5.0)
_OFFLOAD_RANK = {
    1.0: 0,
    2.0: 1,
    3.0: 2,
    3.5: 1.5,
    4.0: 3,
    4.5: 4,
    5.0: 5,
}

SAME_SETUP_RETRIES = 1
SAME_SETUP_RETRIES_AT_FLOOR = 2


class H3OomReliefRetry(Exception):
    """Unwind generate_video so leaked denoise tensors can free before retry."""

    def __init__(self, relief: dict[str, Any]) -> None:
        super().__init__(str((relief or {}).get("reason") or "h3_oom_relief"))
        self.relief = dict(relief or {})


_PORTRAIT_NATIVE = (
    "768x1344", "640x1152", "544x960", "480x864", "352x608",
)
_LANDSCAPE_NATIVE = (
    "1344x768", "1024x768", "1152x640", "960x544", "864x480", "608x352",
)
_SQUARE_NATIVE = ("768x768", "640x640")
_STEP_FLOOR = 23
# Two-step nibbles stay on the same canvas. 24 then lands on the H3 floor 23.
_STEP_LADDER = tuple(list(range(50, 23, -2)) + [_STEP_FLOOR])


def is_h3_model(model_type: Any) -> bool:
    return str(model_type or "").startswith("minimax_h3")


def _as_profile(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number not in _OFFLOAD_RANK:
        return None
    return number


def offload_rank(profile: Any) -> float:
    parsed = _as_profile(profile)
    if parsed is None:
        return -1.0
    return float(_OFFLOAD_RANK[parsed])


def apply_h3_baseline_offload_profile(
    profile: Any,
    model_type: Any = None,
) -> float | Any:
    """Standing H3 default: never keep HighVRAM profiles for H3 denoise."""
    if model_type is not None and not is_h3_model(model_type):
        return profile
    current = _as_profile(profile)
    if current is None:
        return H3_BASELINE_OFFLOAD_PROFILE
    if offload_rank(current) < offload_rank(H3_BASELINE_OFFLOAD_PROFILE):
        return H3_BASELINE_OFFLOAD_PROFILE
    return current


def next_offload_profile(current: Any) -> float | None:
    parsed = _as_profile(current)
    if parsed is None:
        parsed = H3_BASELINE_OFFLOAD_PROFILE
    for candidate in H3_RELIEF_OFFLOAD_LADDER:
        if offload_rank(candidate) > offload_rank(parsed):
            return float(candidate)
    return None


def _parse_resolution(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip().lower()
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    try:
        width = int(left)
        height = int(right)
    except (TypeError, ValueError):
        return None
    if width < 16 or height < 16:
        return None
    return width, height


def _native_ladder(resolution: str) -> tuple[str, ...]:
    parsed = _parse_resolution(resolution)
    if parsed is None:
        return _LANDSCAPE_NATIVE
    width, height = parsed
    if height > width:
        return _PORTRAIT_NATIVE
    if width == height:
        return _SQUARE_NATIVE
    return _LANDSCAPE_NATIVE


def step_nibble_ladder() -> tuple[int, ...]:
    return _STEP_LADDER


def _lower_steps(steps: int) -> int | None:
    for candidate in _STEP_LADDER:
        if candidate < int(steps) and candidate >= _STEP_FLOOR:
            return candidate
    return None


def next_h3_denoise_relief(
    *,
    resolution: str,
    num_inference_steps: int,
    intent_steps: int | None = None,
    attempt: int = 0,
    force_smaller_canvas: bool = False,
) -> dict[str, Any] | None:
    """Return the next safer native settings, or None when relief is exhausted.

    Order for this primitive: nibble scheduler steps on the current canvas
    (50→48→46…→23), then drop one native resolution step while restoring the
    original step intent. force_smaller_canvas skips remaining step rungs.
    The policy wrapper decide_h3_oom_relief must not set that flag for
    step-0 / load / first-attempt OOMs. Never introduces Turbo/Draft or
    1080p/4K delivery crops.
    """
    if attempt >= H3_OOM_RELIEF_MAX_ATTEMPTS:
        return None
    try:
        steps = int(num_inference_steps)
    except (TypeError, ValueError):
        return None
    if steps < 2:
        return None
    original_steps = int(intent_steps or steps)
    lower = None if force_smaller_canvas else _lower_steps(steps)
    if lower is not None:
        return {
            "version": H3_OOM_RELIEF_VERSION,
            "resolution": str(resolution),
            "num_inference_steps": lower,
            "reason": "keep_canvas_fewer_steps",
        }
    ladder = _native_ladder(str(resolution))
    current = str(resolution).strip()
    if current not in ladder:
        parsed = _parse_resolution(current)
        if parsed is None:
            return None
        width, height = parsed
        current = next(
            (
                item for item in ladder
                if _parse_resolution(item) is not None
                and _parse_resolution(item)[0] * _parse_resolution(item)[1]
                <= width * height
            ),
            ladder[-1],
        )
    try:
        index = ladder.index(current)
    except ValueError:
        return None
    if index + 1 >= len(ladder):
        return None
    return {
        "version": H3_OOM_RELIEF_VERSION,
        "resolution": ladder[index + 1],
        "num_inference_steps": max(_STEP_FLOOR, original_steps),
        "reason": "next_native_canvas",
    }


def decide_h3_oom_relief(
    *,
    resolution: str,
    num_inference_steps: int,
    intent_steps: int | None = None,
    attempt: int = 0,
    step_now: int = 0,
    same_setup_retries: int = 0,
    offload_profile: Any = H3_BASELINE_OFFLOAD_PROFILE,
    model_type: Any = "minimax_h3_ref2va",
) -> dict[str, Any] | None:
    """Choose the next relief action for one H3 denoise OOM.

    Retry order:
    1. same-setup after unwind (step-0 / contention / first attempt)
    2. escalate in-tree MMGP offload (4.5 → 5)
    3. two-step nibble on the same canvas
    4. 9:16 native canvas drop, only after mid-denoise at the step floor
    """
    if attempt >= H3_OOM_RELIEF_MAX_ATTEMPTS:
        return None
    try:
        steps = int(num_inference_steps)
        step_index = int(step_now)
        same_retries = int(same_setup_retries)
    except (TypeError, ValueError):
        return None
    if steps < 2:
        return None
    original_steps = int(intent_steps or steps)
    at_floor = _lower_steps(steps) is None
    max_same = SAME_SETUP_RETRIES_AT_FLOOR if at_floor else SAME_SETUP_RETRIES
    step0 = step_index <= 0
    current_profile = apply_h3_baseline_offload_profile(
        offload_profile, model_type,
    )

    def _pack(payload: dict[str, Any], *, record_denial: bool = False) -> dict[str, Any]:
        packed = {
            "version": H3_OOM_RELIEF_VERSION,
            "resolution": str(resolution),
            "num_inference_steps": steps,
            "override_profile": current_profile,
            "record_denial": record_denial,
            **payload,
        }
        return packed

    if same_retries == 0 or (step0 and same_retries < max_same):
        return _pack({"reason": "same_setup_after_unwind"}, record_denial=False)

    stronger = next_offload_profile(current_profile)
    if stronger is not None:
        return _pack({
            "reason": "escalate_offload",
            "override_profile": stronger,
        }, record_denial=False)

    nibble = next_h3_denoise_relief(
        resolution=str(resolution),
        num_inference_steps=steps,
        intent_steps=original_steps,
        attempt=attempt,
        force_smaller_canvas=False,
    )
    if nibble is None:
        return None
    if nibble.get("reason") == "next_native_canvas" and step0:
        # Canvas drop is last resort and only after a mid-denoise failure.
        return None
    nibble = dict(nibble)
    nibble["override_profile"] = current_profile
    nibble["record_denial"] = (not step0)
    return nibble


def relief_from_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    params = params if isinstance(params, Mapping) else {}
    try:
        attempt = int(params.get("_h3_oom_relief_attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    try:
        intent = int(params.get("_h3_oom_relief_intent_steps") or 0) or None
    except (TypeError, ValueError):
        intent = None
    try:
        same_retries = int(params.get("_h3_same_setup_retries") or 0)
    except (TypeError, ValueError):
        same_retries = 0
    try:
        step_now = int(params.get("_h3_oom_step_now") or 0)
    except (TypeError, ValueError):
        step_now = 0
    return decide_h3_oom_relief(
        resolution=str(params.get("resolution") or ""),
        num_inference_steps=int(params.get("num_inference_steps") or 0),
        intent_steps=intent,
        attempt=attempt,
        step_now=step_now,
        same_setup_retries=same_retries,
        offload_profile=params.get("_h3_relief_offload_profile"),
        model_type=params.get("model_type"),
    )
