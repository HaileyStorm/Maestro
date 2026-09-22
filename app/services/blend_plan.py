"""Pure timing and metadata planning for Studio clip blending."""

from __future__ import annotations

import copy
import math
import os
from collections.abc import Mapping
from typing import Any


BLEND_CONTRACT_VERSION = 1
BLEND_MODES = frozenset({"insert", "overlap"})
DEFAULT_BLEND_DURATION_SEC = 3.0
MAX_BLEND_DURATION_SEC = 60.0
MIN_TRANSITION_FRAMES = 17
TRANSITION_FRAME_STEP = 8
# Frame-lattice rounding can add up to one lattice interval to the requested
# transition. Keep the public request bound at 60s while allowing that small
# effective-duration slack when reading queued v1 metadata.
MAX_EFFECTIVE_DURATION_SLACK_FRAMES = TRANSITION_FRAME_STEP
BLEND_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
})
BLEND_VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm",
})
BLEND_METADATA_INVALID_MESSAGE = (
    "Saved Blend settings are incomplete or invalid. Start a new blend."
)
_BLEND_TEMP_INPUT_KEYS = frozenset({
    "image_start", "image_end", "image_refs", "video_source", "video_end",
    "video_guide", "video_guide2", "video_guide3", "video_mask",
    "audio_guide", "audio_guide2", "audio_guide3", "audio_guide4",
    "audio_guide5", "audio_guide6", "audio_conditioning_guide",
})


def normalize_blend_mode(value: Any) -> str:
    """Return one of the public blend modes or fail closed."""

    if not isinstance(value, str):
        raise ValueError("blend_mode must be 'insert' or 'overlap'")
    mode = value.strip().lower()
    if mode not in BLEND_MODES:
        raise ValueError("blend_mode must be 'insert' or 'overlap'")
    return mode


def validate_blend_duration(value: Any, *, field: str = "transition_sec") -> float:
    """Validate a finite, bounded duration without silently coercing booleans."""

    if isinstance(value, bool):
        raise ValueError(
            f"{field} must be a finite number greater than 0 and at most "
            f"{MAX_BLEND_DURATION_SEC:g} seconds"
        )
    try:
        duration = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"{field} must be a finite number greater than 0 and at most "
            f"{MAX_BLEND_DURATION_SEC:g} seconds"
        ) from error
    if not math.isfinite(duration) or duration <= 0 or duration > MAX_BLEND_DURATION_SEC:
        raise ValueError(
            f"{field} must be a finite number greater than 0 and at most "
            f"{MAX_BLEND_DURATION_SEC:g} seconds"
        )
    return duration


def validate_legacy_blend_duration(
    value: Any,
    *,
    field: str = "_blend_overlap_sec",
) -> float:
    """Validate historical effective durations without imposing v1's cap."""

    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite positive number")
    try:
        duration = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{field} must be a finite positive number") from error
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return duration


def validate_blend_fps(value: Any, *, field: str = "_blend_fps") -> float:
    """Validate the frame rate required to replay a versioned blend."""

    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite positive number")
    try:
        fps = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{field} must be a finite positive number") from error
    if not math.isfinite(fps) or fps <= 0 or fps > 1000:
        raise ValueError(f"{field} must be a finite positive number")
    return fps


def _effective_duration_limit(fps: Any = None) -> float:
    """Return the largest duration produced by the frame lattice.

    The API bounds the requested duration at ``MAX_BLEND_DURATION_SEC``.  The
    lattice rounds that request upward, so the effective duration may be a
    little larger.  A queued job carries its source fps; when it does not, the
    conservative lattice interval keeps old metadata readable without making
    the public request validator permissive.
    """

    if fps is None:
        return MAX_BLEND_DURATION_SEC + MAX_EFFECTIVE_DURATION_SLACK_FRAMES
    try:
        fps_value = float(fps)
    except (TypeError, ValueError, OverflowError):
        return MAX_BLEND_DURATION_SEC + MAX_EFFECTIVE_DURATION_SLACK_FRAMES
    if not math.isfinite(fps_value) or fps_value <= 0:
        return MAX_BLEND_DURATION_SEC + MAX_EFFECTIVE_DURATION_SLACK_FRAMES
    max_raw_frames = max(
        MIN_TRANSITION_FRAMES,
        int(round(MAX_BLEND_DURATION_SEC * fps_value)),
    )
    max_transition_frames = (
        MIN_TRANSITION_FRAMES
        + TRANSITION_FRAME_STEP
        * math.ceil((max_raw_frames - MIN_TRANSITION_FRAMES) / TRANSITION_FRAME_STEP)
        if max_raw_frames > MIN_TRANSITION_FRAMES
        else MIN_TRANSITION_FRAMES
    )
    return max_transition_frames / fps_value


def validate_effective_blend_duration(
    value: Any,
    *,
    field: str = "_blend_duration_sec",
    fps: Any = None,
) -> float:
    """Validate an effective (already lattice-rounded) transition duration."""

    if isinstance(value, bool):
        raise ValueError(
            f"{field} must be a finite number greater than 0 and at most "
            f"{_effective_duration_limit(fps):g} seconds"
        )
    try:
        duration = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"{field} must be a finite number greater than 0 and at most "
            f"{_effective_duration_limit(fps):g} seconds"
        ) from error
    limit = _effective_duration_limit(fps)
    if not math.isfinite(duration) or duration <= 0 or duration > limit:
        raise ValueError(
            f"{field} must be a finite number greater than 0 and at most "
            f"{limit:g} seconds"
        )
    return duration


def normalize_blend_request(body: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the request duration while retaining legacy overlap clients."""

    mode = normalize_blend_mode(body.get("blend_mode", "overlap"))
    if mode == "insert":
        # Older clients sent only overlap_sec. Keep those requests usable, but
        # make transition_sec the authoritative field whenever it is present.
        field = "transition_sec"
        raw_duration = (
            body["transition_sec"]
            if "transition_sec" in body
            else body.get("overlap_sec", DEFAULT_BLEND_DURATION_SEC)
        )
    else:
        field = "overlap_sec"
        raw_duration = body.get("overlap_sec", DEFAULT_BLEND_DURATION_SEC)
    return {
        "mode": mode,
        "duration_sec": validate_blend_duration(raw_duration, field=field),
        "duration_field": field,
    }


def build_blend_plan(mode: Any, duration_sec: Any, fps: Any) -> dict[str, Any]:
    """Compute the model frame lattice and its effective duration."""

    mode = normalize_blend_mode(mode)
    duration = validate_blend_duration(duration_sec)
    fps_value = validate_blend_fps(fps, field="blend fps")
    raw_frames = max(MIN_TRANSITION_FRAMES, int(round(duration * fps_value)))
    transition_frames = (
        MIN_TRANSITION_FRAMES
        + TRANSITION_FRAME_STEP
        * math.ceil((raw_frames - MIN_TRANSITION_FRAMES) / TRANSITION_FRAME_STEP)
        if raw_frames > MIN_TRANSITION_FRAMES
        else MIN_TRANSITION_FRAMES
    )
    return {
        "contract_version": BLEND_CONTRACT_VERSION,
        "mode": mode,
        "requested_duration_sec": duration,
        "transition_frames": transition_frames,
        "effective_duration_sec": transition_frames / fps_value,
    }


def build_blend_assembly(
    mode: Any,
    duration_sec: Any,
    duration_a_sec: Any,
    duration_b_sec: Any,
    *,
    fps: Any = None,
    legacy: bool = False,
) -> dict[str, Any]:
    """Describe source trims for the final concat without touching media."""

    mode = normalize_blend_mode(mode)
    if legacy:
        duration = validate_legacy_blend_duration(
            duration_sec,
            field="_blend_overlap_sec",
        )
    else:
        duration = validate_effective_blend_duration(
            duration_sec,
            field="_blend_duration_sec",
            fps=fps,
        )
    try:
        duration_a = float(duration_a_sec)
        duration_b = float(duration_b_sec)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("blend source durations must be finite numbers") from error
    if not math.isfinite(duration_a) or not math.isfinite(duration_b):
        raise ValueError("blend source durations must be finite numbers")
    duration_a = max(0.0, duration_a)
    duration_b = max(0.0, duration_b)

    if mode == "insert":
        a_start = 0.0
        a_duration = duration_a
        b_start = 0.0
        b_duration = duration_b
    else:
        a_start = 0.0
        a_duration = max(0.0, duration_a - duration)
        b_start = duration
        b_duration = max(0.0, duration_b - duration)
    return {
        "contract_version": BLEND_CONTRACT_VERSION,
        "mode": mode,
        "duration_sec": duration,
        "a_start_sec": a_start,
        "a_duration_sec": a_duration,
        "b_start_sec": b_start,
        "b_duration_sec": b_duration,
    }


def _temp_path_relation(value: Any, temp_dir: Any) -> str | None:
    """Return ``exact``/``child`` only for paths in the owned temp folder."""

    if not isinstance(value, str) or not value or not isinstance(temp_dir, str) or not temp_dir:
        return None
    try:
        root = os.path.realpath(os.path.abspath(temp_dir))
        candidate = os.path.realpath(os.path.abspath(value))
        if candidate == root:
            return "exact"
        if os.path.commonpath((root, candidate)) == root:
            return "child"
    except (OSError, TypeError, ValueError):
        return None
    return None


def rewrite_blend_sidecar(
    sidecar: Mapping[str, Any],
    *,
    output_filename: str,
    temp_dir: str,
) -> dict[str, Any]:
    """Rebind a final blend sidecar while removing dead temp inputs only.

    The transition sidecar is copied before its owned temporary directory is
    removed.  Source/audit fields remain untouched; only known conditioning
    inputs (and upload entries tied to those inputs) inside that exact temp
    directory are pruned.  The returned mapping is safe to pass to the
    launcher's atomic JSON writer.
    """

    if not isinstance(sidecar, Mapping):
        raise ValueError("blend sidecar is not a mapping")
    result = copy.deepcopy(dict(sidecar))
    result["output_filename"] = os.path.basename(output_filename)

    original_params = result.get("params")
    changed_params: dict[str, tuple[Any, Any]] = {}
    if isinstance(original_params, Mapping):
        clean_params = dict(original_params)
        source_filenames = {}
        for role, key in (("clip_a", "_blend_clip_a"), ("clip_b", "_blend_clip_b")):
            original_name = clean_params.pop(
                f"_blend_original_{role}_name", None,
            )
            source_path = clean_params.pop(key, None)
            if isinstance(source_path, str) and source_path:
                filename = os.path.basename(source_path)
                if (
                    isinstance(original_name, str)
                    and original_name
                    and os.path.basename(original_name) == original_name
                ):
                    filename = original_name
                source_filenames[role] = {"filename": filename}
                changed_params[key] = (source_path, None)
        for key, value in list(clean_params.items()):
            relation = _temp_path_relation(value, temp_dir)
            if key == "_blend_temp_dir":
                if relation in {"exact", "child"}:
                    clean_params.pop(key, None)
                    changed_params[key] = (value, None)
                continue
            if key not in _BLEND_TEMP_INPUT_KEYS:
                continue
            if relation in {"exact", "child"}:
                clean_params.pop(key, None)
                changed_params[key] = (value, None)
            elif isinstance(value, list):
                cleaned = [
                    item for item in value
                    if _temp_path_relation(item, temp_dir) not in {"exact", "child"}
                ]
                if cleaned != value:
                    if cleaned:
                        clean_params[key] = cleaned
                        changed_params[key] = (value, cleaned)
                    else:
                        clean_params.pop(key, None)
                        changed_params[key] = (value, None)
        result["params"] = clean_params
        metadata = resolve_blend_metadata(original_params)
        result["blend_contract"] = {
            "version": 0 if metadata["legacy"] else metadata["contract_version"],
            "legacy": metadata["legacy"],
            "mode": metadata["mode"],
            "requested_duration_sec": (
                None
                if metadata["legacy"]
                else clean_params.get("_blend_requested_duration_sec")
            ),
            "effective_duration_sec": metadata["duration_sec"],
            "fps": metadata["fps"],
            "sources": source_filenames,
        }

    upload_filenames = result.get("upload_filenames")
    if isinstance(upload_filenames, Mapping):
        clean_uploads = {}
        for key, value in upload_filenames.items():
            relation = _temp_path_relation(value, temp_dir)
            if relation in {"exact", "child"}:
                continue
            change = changed_params.get(key)
            original_value = change[0] if change is not None else None
            if change is not None and change[1] is None:
                continue
            if isinstance(value, list) and isinstance(original_value, list):
                cleaned_values = [
                    item for index, item in enumerate(value)
                    if index >= len(original_value)
                    or _temp_path_relation(original_value[index], temp_dir)
                    not in {"exact", "child"}
                ]
                if cleaned_values or not value:
                    clean_uploads[key] = cleaned_values
                continue
            if isinstance(value, list):
                filtered = [
                    item for item in value
                    if _temp_path_relation(item, temp_dir) not in {"exact", "child"}
                ]
                if filtered or not value:
                    clean_uploads[key] = filtered
                continue
            clean_uploads[key] = value
        result["upload_filenames"] = clean_uploads
    return result


def resolve_blend_metadata(params: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve queued metadata, preserving the pre-contract overlap behavior."""

    if not isinstance(params, Mapping):
        raise ValueError("blend metadata is not a mapping")
    version = params.get("_blend_contract_version")
    if version is None:
        # Before contract v1, `_blend_mode` was recorded but ignored and every
        # job assembled as overlap. Do not reinterpret those queued jobs.
        raw_duration = params.get("_blend_overlap_sec", DEFAULT_BLEND_DURATION_SEC)
        legacy_fps = None
        if "_blend_fps" in params:
            try:
                candidate_fps = float(params["_blend_fps"])
            except (TypeError, ValueError, OverflowError):
                candidate_fps = None
            if candidate_fps is not None and math.isfinite(candidate_fps) and candidate_fps > 0:
                legacy_fps = candidate_fps
        return {
            "contract_version": None,
            "legacy": True,
            "mode": "overlap",
            "duration_sec": validate_legacy_blend_duration(
                raw_duration, field="_blend_overlap_sec"
            ),
            "fps": legacy_fps,
        }
    if isinstance(version, bool) or not isinstance(version, int) or version != BLEND_CONTRACT_VERSION:
        raise ValueError("unsupported blend metadata contract version")
    if "_blend_mode" not in params:
        raise ValueError("blend metadata is missing effective mode")
    mode = normalize_blend_mode(params["_blend_mode"])
    if "_blend_fps" not in params:
        raise ValueError("blend metadata is missing frame rate")
    fps = validate_blend_fps(params["_blend_fps"])
    if "_blend_duration_sec" not in params:
        raise ValueError("blend metadata is missing effective transition duration")
    duration = validate_effective_blend_duration(
        params["_blend_duration_sec"],
        field="_blend_duration_sec",
        fps=fps,
    )
    if "_blend_requested_duration_sec" not in params:
        raise ValueError("blend metadata is missing requested transition duration")
    requested_duration = validate_blend_duration(
        params["_blend_requested_duration_sec"],
        field="_blend_requested_duration_sec",
    )
    expected_duration = build_blend_plan(mode, requested_duration, fps)[
        "effective_duration_sec"
    ]
    if not math.isclose(duration, expected_duration, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("blend metadata effective duration does not match its frame lattice")
    return {
        "contract_version": version,
        "legacy": False,
        "mode": mode,
        "duration_sec": duration,
        "fps": fps,
    }
