"""Pure, content-free sealing for one whole-job MiniMax H3 offload plan.

This first contract records the profile that the existing runtime would use;
it deliberately does not tune or apply a different profile.  The sealed plan
is therefore an immutable baseline for later cost/evidence-driven planning and
for exact restart parity, without exposing authored text, paths, hardware, or
calibration evidence.
"""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from copy import deepcopy
import hashlib
import hmac
import json
import math
import re
from typing import Any


H3_OFFLOAD_PLAN_VERSION = 1
H3_OFFLOAD_PLAN_PARAM_KEY = "_h3_offload_plan"

_MAX_SEGMENTS = 256
_MAX_FRAMES = 1_000_000
_MAX_DIMENSION = 65_536
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:+-]{1,128}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ROOT_KEYS = frozenset({
    "version", "mode", "source", "profile", "resolution", "fps",
    "schedule", "schedule_id", "segments", "movements",
    "transition_count", "movement_count", "digest",
})
_SCHEDULE_KEYS = frozenset({
    "sampling_steps", "attention_engine", "sol_tau", "sol_dense_steps",
    "sol_dense_blocks", "sol_min_sequence", "turbo_profile",
    "spectrum_profile", "lightx2v_profile", "multirate_profile",
    "source_audio_mode",
})
_SEGMENT_KEYS = frozenset({
    "index", "generated_frames", "published_frames", "model_type",
    "profile", "schedule_id", "transition",
})
_MOVEMENT_KEYS = frozenset({"segment_index", "kind"})
_SOURCES = frozenset({
    "manual_override", "current_profile_fallback", "recovery_profile",
})
_TRANSITIONS = frozenset({
    "initial_load", "resident_reuse", "model_transition",
})
_ATTENTION_ENGINES = frozenset({"sdpa", "sol_attn", "sage2"})
_TURBO_PROFILES = frozenset({"", "h3_turbo_v4"})
_SPECTRUM_PROFILES = frozenset({"", "spectrum_h3_v1"})
_LIGHTX2V_PROFILES = frozenset({"", "h3_lightx2v_fl2v_4_v1"})
_MULTIRATE_PROFILES = frozenset({"", "t8_4v8a_evidence_v1"})
_SOURCE_AUDIO_MODES = frozenset({
    "native", "lock_source", "remix_source", "reference_only",
})


class H3OffloadPlanError(ValueError):
    """Raised when an H3 offload plan is incomplete, changed, or unsafe."""


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _integer(
    value: Any,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise H3OffloadPlanError(
            f"{name} must be an integer from {minimum} through {maximum}."
        )
    return value


def _number(
    value: Any,
    *,
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not minimum <= float(value) <= maximum
    ):
        raise H3OffloadPlanError(
            f"{name} must be a finite number from {minimum} through {maximum}."
        )
    return float(value)


def _identifier(value: Any, *, name: str, allow_empty: bool = False) -> str:
    if allow_empty and value in (None, ""):
        return ""
    if type(value) is not str or _SAFE_ID.fullmatch(value) is None:
        raise H3OffloadPlanError(f"{name} is invalid.")
    return value


def _enum(value: Any, *, name: str, allowed: frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        raise H3OffloadPlanError(f"{name} is invalid.")
    return value


def _parse_resolution(value: Any) -> dict[str, int]:
    if type(value) is not str:
        raise H3OffloadPlanError("H3 offload resolution is invalid.")
    match = re.fullmatch(r"([1-9][0-9]{0,4})[xX]([1-9][0-9]{0,4})", value)
    if match is None:
        raise H3OffloadPlanError("H3 offload resolution is invalid.")
    width = _integer(
        int(match.group(1)), name="resolution.width",
        minimum=1, maximum=_MAX_DIMENSION,
    )
    height = _integer(
        int(match.group(2)), name="resolution.height",
        minimum=1, maximum=_MAX_DIMENSION,
    )
    return {"width": width, "height": height}


def _schedule_from_params(params: Mapping[str, Any]) -> dict[str, Any]:
    custom = params.get("custom_settings")
    custom = custom if isinstance(custom, Mapping) else {}
    return {
        "sampling_steps": _integer(
            int(params.get("num_inference_steps") or 20),
            name="schedule.sampling_steps", minimum=1, maximum=1_000,
        ),
        "attention_engine": _enum(
            str(custom.get("h3_attention_engine") or "sol_attn"),
            name="schedule.attention_engine",
            allowed=_ATTENTION_ENGINES,
        ),
        "sol_tau": _number(
            custom.get("h3_sol_tau", 1.0), name="schedule.sol_tau",
            minimum=0.0, maximum=1_000_000.0,
        ),
        "sol_dense_steps": _integer(
            int(custom.get("h3_sol_dense_steps", 10)),
            name="schedule.sol_dense_steps", minimum=0, maximum=1_000,
        ),
        "sol_dense_blocks": _integer(
            int(custom.get("h3_sol_dense_blocks", 2)),
            name="schedule.sol_dense_blocks", minimum=0, maximum=1_000,
        ),
        "sol_min_sequence": _integer(
            int(custom.get("h3_sol_min_tokens", 4096)),
            name="schedule.sol_min_sequence", minimum=0, maximum=100_000_000,
        ),
        "turbo_profile": _enum(
            str(custom.get("h3_turbo_profile") or ""),
            name="schedule.turbo_profile", allowed=_TURBO_PROFILES,
        ),
        "spectrum_profile": _enum(
            str(custom.get("h3_spectrum_profile") or ""),
            name="schedule.spectrum_profile", allowed=_SPECTRUM_PROFILES,
        ),
        "lightx2v_profile": _enum(
            str(custom.get("h3_lightx2v_profile") or ""),
            name="schedule.lightx2v_profile", allowed=_LIGHTX2V_PROFILES,
        ),
        "multirate_profile": _enum(
            str(custom.get("h3_multirate_profile") or ""),
            name="schedule.multirate_profile", allowed=_MULTIRATE_PROFILES,
        ),
        "source_audio_mode": _enum(
            str(custom.get("h3_source_audio_mode") or "native"),
            name="schedule.source_audio_mode",
            allowed=_SOURCE_AUDIO_MODES,
        ),
    }


def _profile_and_source(
    params: Mapping[str, Any],
    *,
    effective_profile: int,
    source: str | None,
) -> tuple[int, str]:
    current = _integer(
        effective_profile, name="effective_profile", minimum=1, maximum=5,
    )
    raw_override = params.get("override_profile", -1)
    try:
        override = int(raw_override if raw_override is not None else -1)
    except (TypeError, ValueError):
        raise H3OffloadPlanError("override_profile is invalid.") from None
    if isinstance(raw_override, bool) or override not in {-1, 1, 2, 3, 4, 5}:
        raise H3OffloadPlanError("override_profile is invalid.")
    if override != -1:
        if source not in (None, "manual_override", "recovery_profile"):
            raise H3OffloadPlanError("Manual profile authority cannot be replaced.")
        return override, source or "manual_override"
    selected_source = source or "current_profile_fallback"
    if selected_source != "current_profile_fallback":
        raise H3OffloadPlanError("Automatic profile source is invalid.")
    return current, selected_source


def _segment_inputs(
    params: Mapping[str, Any],
) -> tuple[list[int], list[int], list[str], float]:
    longform = params.get("_h3_longform")
    if isinstance(longform, Mapping):
        raw_generated = longform.get("clip_frames")
        raw_published = longform.get("clip_published_frames")
        raw_models = longform.get("segment_models")
        if not isinstance(raw_generated, list) or not raw_generated:
            raise H3OffloadPlanError("H3 segment geometry is unavailable.")
        if not isinstance(raw_models, list) or len(raw_models) != len(raw_generated):
            raise H3OffloadPlanError("H3 segment model geometry is incomplete.")
        if not isinstance(raw_published, list) or len(raw_published) != len(raw_generated):
            raise H3OffloadPlanError(
                "H3 segment publication geometry is incomplete."
            )
        published = raw_published
        models = []
        for index, model in enumerate(raw_models):
            if not isinstance(model, Mapping):
                raise H3OffloadPlanError(
                    f"H3 segment {index + 1} model identity is invalid."
                )
            models.append(_identifier(
                model.get("model_type"),
                name=f"segments[{index}].model_type",
            ))
        generated = [
            _integer(value, name=f"segments[{index}].generated_frames",
                     minimum=1, maximum=_MAX_FRAMES)
            for index, value in enumerate(raw_generated)
        ]
        published_frames = [
            _integer(value, name=f"segments[{index}].published_frames",
                     minimum=1, maximum=_MAX_FRAMES)
            for index, value in enumerate(published)
        ]
        fps = _number(
            longform.get("fps", 24), name="fps", minimum=0.001,
            maximum=1_000.0,
        )
    else:
        generated = [_integer(
            int(params.get("video_length") or 0),
            name="segments[0].generated_frames",
            minimum=1, maximum=_MAX_FRAMES,
        )]
        published_frames = list(generated)
        models = [_identifier(params.get("model_type"), name="model_type")]
        fps = _number(
            params.get("fps", 24), name="fps", minimum=0.001,
            maximum=1_000.0,
        )
    if len(generated) > _MAX_SEGMENTS:
        raise H3OffloadPlanError("H3 offload plan has too many segments.")
    if any(published > generated for published, generated in zip(
        published_frames, generated,
    )):
        raise H3OffloadPlanError(
            "Published H3 segment frames cannot exceed generated frames."
        )
    return generated, published_frames, models, fps


def build_h3_offload_plan(
    params: Mapping[str, Any],
    *,
    effective_profile: int,
    source: str | None = None,
    segment_profiles: list[int] | None = None,
) -> dict[str, Any]:
    """Build the conservative fixed-profile plan from physical job inputs."""
    if not isinstance(params, Mapping):
        raise H3OffloadPlanError("H3 offload parameters are invalid.")
    model_type = str(params.get("model_type") or "")
    if not model_type.startswith("minimax_h3"):
        raise H3OffloadPlanError("H3 offload plans require a MiniMax H3 job.")
    profile, selected_source = _profile_and_source(
        params, effective_profile=effective_profile, source=source,
    )
    generated, published, models, fps = _segment_inputs(params)
    if segment_profiles is None:
        profiles = [profile] * len(generated)
    else:
        if selected_source != "recovery_profile" or len(segment_profiles) != len(generated):
            raise H3OffloadPlanError(
                "Per-segment profiles require an exact recovery plan."
            )
        profiles = [
            _integer(value, name=f"segments[{index}].profile",
                     minimum=1, maximum=5)
            for index, value in enumerate(segment_profiles)
        ]
    schedule = _schedule_from_params(params)
    schedule_id = _digest(schedule)
    segments = []
    movements = []
    previous_identity: tuple[str, int, str] | None = None
    for index, (frame_count, published_count, segment_model, segment_profile) in enumerate(zip(
        generated, published, models, profiles,
    ), start=1):
        identity = (segment_model, segment_profile, schedule_id)
        if previous_identity is None:
            transition = "initial_load"
        elif previous_identity == identity:
            transition = "resident_reuse"
        else:
            transition = "model_transition"
        if transition != "resident_reuse":
            movements.append({"segment_index": index, "kind": transition})
        segments.append({
            "index": index,
            "generated_frames": frame_count,
            "published_frames": published_count,
            "model_type": segment_model,
            "profile": segment_profile,
            "schedule_id": schedule_id,
            "transition": transition,
        })
        previous_identity = identity
    payload = {
        "version": H3_OFFLOAD_PLAN_VERSION,
        "mode": "fixed_profile",
        "source": selected_source,
        "profile": profile,
        "resolution": _parse_resolution(
            params.get("resolution") or "1344x768"
        ),
        "fps": fps,
        "schedule": schedule,
        "schedule_id": schedule_id,
        "segments": segments,
        "movements": movements,
        "transition_count": sum(
            segment["transition"] == "model_transition"
            for segment in segments
        ),
        "movement_count": len(movements),
    }
    payload["digest"] = _digest(payload)
    return payload


def _validate_schedule(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _SCHEDULE_KEYS:
        raise H3OffloadPlanError("H3 offload schedule is invalid.")
    # Reuse the builder's normalization through a synthetic parameter view.
    return {
        "sampling_steps": _integer(
            value["sampling_steps"], name="schedule.sampling_steps",
            minimum=1, maximum=1_000,
        ),
        "attention_engine": _enum(
            value["attention_engine"], name="schedule.attention_engine",
            allowed=_ATTENTION_ENGINES,
        ),
        "sol_tau": _number(
            value["sol_tau"], name="schedule.sol_tau",
            minimum=0.0, maximum=1_000_000.0,
        ),
        "sol_dense_steps": _integer(
            value["sol_dense_steps"], name="schedule.sol_dense_steps",
            minimum=0, maximum=1_000,
        ),
        "sol_dense_blocks": _integer(
            value["sol_dense_blocks"], name="schedule.sol_dense_blocks",
            minimum=0, maximum=1_000,
        ),
        "sol_min_sequence": _integer(
            value["sol_min_sequence"], name="schedule.sol_min_sequence",
            minimum=0, maximum=100_000_000,
        ),
        "turbo_profile": _enum(
            value["turbo_profile"], name="schedule.turbo_profile",
            allowed=_TURBO_PROFILES,
        ),
        "spectrum_profile": _enum(
            value["spectrum_profile"], name="schedule.spectrum_profile",
            allowed=_SPECTRUM_PROFILES,
        ),
        "lightx2v_profile": _enum(
            value["lightx2v_profile"], name="schedule.lightx2v_profile",
            allowed=_LIGHTX2V_PROFILES,
        ),
        "multirate_profile": _enum(
            value["multirate_profile"], name="schedule.multirate_profile",
            allowed=_MULTIRATE_PROFILES,
        ),
        "source_audio_mode": _enum(
            value["source_audio_mode"], name="schedule.source_audio_mode",
            allowed=_SOURCE_AUDIO_MODES,
        ),
    }


def validate_h3_offload_plan(value: Any) -> dict[str, Any]:
    """Return a canonical copy, rejecting mutation and unknown fields."""
    if not isinstance(value, Mapping) or set(value) != _ROOT_KEYS:
        raise H3OffloadPlanError("H3 offload plan fields are invalid.")
    version = _integer(
        value["version"], name="version",
        minimum=H3_OFFLOAD_PLAN_VERSION, maximum=H3_OFFLOAD_PLAN_VERSION,
    )
    if (
        type(value["mode"]) is not str
        or value["mode"] != "fixed_profile"
        or type(value["source"]) is not str
        or value["source"] not in _SOURCES
    ):
        raise H3OffloadPlanError("H3 offload plan mode or source is invalid.")
    profile = _integer(value["profile"], name="profile", minimum=1, maximum=5)
    resolution = value["resolution"]
    if not isinstance(resolution, Mapping) or set(resolution) != {"width", "height"}:
        raise H3OffloadPlanError("H3 offload resolution is invalid.")
    clean_resolution = {
        "width": _integer(
            resolution["width"], name="resolution.width",
            minimum=1, maximum=_MAX_DIMENSION,
        ),
        "height": _integer(
            resolution["height"], name="resolution.height",
            minimum=1, maximum=_MAX_DIMENSION,
        ),
    }
    fps = _number(value["fps"], name="fps", minimum=0.001, maximum=1_000.0)
    schedule = _validate_schedule(value["schedule"])
    schedule_id = value["schedule_id"]
    if type(schedule_id) is not str or _DIGEST.fullmatch(schedule_id) is None:
        raise H3OffloadPlanError("H3 offload schedule identity is invalid.")
    if not hmac.compare_digest(schedule_id, _digest(schedule)):
        raise H3OffloadPlanError("H3 offload schedule identity changed.")
    raw_segments = value["segments"]
    if not isinstance(raw_segments, list) or not 1 <= len(raw_segments) <= _MAX_SEGMENTS:
        raise H3OffloadPlanError("H3 offload segments are invalid.")
    segments = []
    movements = []
    previous_identity: tuple[str, int, str] | None = None
    for offset, raw in enumerate(raw_segments, start=1):
        if not isinstance(raw, Mapping) or set(raw) != _SEGMENT_KEYS:
            raise H3OffloadPlanError(f"H3 offload segment {offset} is invalid.")
        index = _integer(raw["index"], name="segment.index", minimum=1,
                         maximum=_MAX_SEGMENTS)
        if index != offset:
            raise H3OffloadPlanError("H3 offload segment ordering changed.")
        generated = _integer(
            raw["generated_frames"], name="segment.generated_frames",
            minimum=1, maximum=_MAX_FRAMES,
        )
        published = _integer(
            raw["published_frames"], name="segment.published_frames",
            minimum=1, maximum=_MAX_FRAMES,
        )
        if published > generated:
            raise H3OffloadPlanError(
                "Published H3 segment frames exceed generated frames."
            )
        model = _identifier(raw["model_type"], name="segment.model_type")
        segment_profile = _integer(
            raw["profile"], name="segment.profile", minimum=1, maximum=5,
        )
        if (
            (value["source"] != "recovery_profile" and segment_profile != profile)
            or raw["schedule_id"] != schedule_id
        ):
            raise H3OffloadPlanError("H3 offload segment identity changed.")
        identity = (model, segment_profile, schedule_id)
        transition = (
            "initial_load" if previous_identity is None
            else "resident_reuse" if previous_identity == identity
            else "model_transition"
        )
        if raw["transition"] != transition or transition not in _TRANSITIONS:
            raise H3OffloadPlanError("H3 offload transition structure changed.")
        if transition != "resident_reuse":
            movements.append({"segment_index": index, "kind": transition})
        segments.append({
            "index": index,
            "generated_frames": generated,
            "published_frames": published,
            "model_type": model,
            "profile": segment_profile,
            "schedule_id": schedule_id,
            "transition": transition,
        })
        previous_identity = identity
    raw_movements = value["movements"]
    if not isinstance(raw_movements, list) or any(
        not isinstance(item, Mapping) or set(item) != _MOVEMENT_KEYS
        for item in raw_movements
    ):
        raise H3OffloadPlanError("H3 offload movement structure is invalid.")
    if list(raw_movements) != movements:
        raise H3OffloadPlanError("H3 offload movement structure changed.")
    transition_count = sum(
        segment["transition"] == "model_transition" for segment in segments
    )
    supplied_transition_count = _integer(
        value["transition_count"], name="transition_count",
        minimum=0, maximum=_MAX_SEGMENTS - 1,
    )
    supplied_movement_count = _integer(
        value["movement_count"], name="movement_count",
        minimum=1, maximum=_MAX_SEGMENTS,
    )
    if (
        supplied_transition_count != transition_count
        or supplied_movement_count != len(movements)
    ):
        raise H3OffloadPlanError("H3 offload movement counts changed.")
    payload = {
        "version": version,
        "mode": "fixed_profile",
        "source": value["source"],
        "profile": profile,
        "resolution": clean_resolution,
        "fps": fps,
        "schedule": schedule,
        "schedule_id": schedule_id,
        "segments": segments,
        "movements": movements,
        "transition_count": transition_count,
        "movement_count": len(movements),
    }
    supplied_digest = value["digest"]
    if type(supplied_digest) is not str or _DIGEST.fullmatch(supplied_digest) is None:
        raise H3OffloadPlanError("H3 offload plan digest is invalid.")
    expected_digest = _digest(payload)
    if not hmac.compare_digest(supplied_digest, expected_digest):
        raise H3OffloadPlanError("H3 offload plan changed after sealing.")
    payload["digest"] = expected_digest
    return payload


def seal_h3_offload_plan(
    params: MutableMapping[str, Any],
    *,
    effective_profile: int,
    source: str | None = None,
    replace: bool = False,
    segment_profiles: list[int] | None = None,
) -> dict[str, Any]:
    """Seal authoritative params, rejecting a conflicting prior contract."""
    if not isinstance(params, MutableMapping):
        raise H3OffloadPlanError("H3 offload parameters must be mutable.")
    expected = build_h3_offload_plan(
        params,
        effective_profile=effective_profile,
        source=source,
        segment_profiles=segment_profiles,
    )
    existing = params.get(H3_OFFLOAD_PLAN_PARAM_KEY)
    if existing is not None and not replace:
        clean = validate_h3_offload_plan(existing)
        if clean != expected:
            raise H3OffloadPlanError(
                "H3 physical inputs changed after the offload plan was sealed."
            )
        return clean
    params[H3_OFFLOAD_PLAN_PARAM_KEY] = deepcopy(expected)
    return expected


def assert_h3_offload_plan_parity(
    params_plan: Any,
    recovered_plan: Any,
    *,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Require byte-canonical equality between manifest and journal plans."""
    manifest = validate_h3_offload_plan(params_plan)
    recovered = validate_h3_offload_plan(recovered_plan)
    if manifest != recovered:
        raise H3OffloadPlanError(
            "Recovered H3 offload plan does not match the sealed request."
        )
    if params is not None:
        segment_profiles = (
            [int(segment["profile"]) for segment in manifest["segments"]]
            if manifest["source"] == "recovery_profile" else None
        )
        expected = build_h3_offload_plan(
            params,
            effective_profile=int(manifest["profile"]),
            source=str(manifest["source"]),
            segment_profiles=segment_profiles,
        )
        if manifest != expected:
            raise H3OffloadPlanError(
                "H3 physical inputs changed after the offload plan was sealed."
            )
    return manifest


def public_h3_offload_plan(value: Any) -> dict[str, Any] | None:
    """Project only bounded non-sensitive state; never expose identities."""
    try:
        plan = validate_h3_offload_plan(value)
    except H3OffloadPlanError:
        return None
    return {
        "mode": plan["mode"],
        "source": plan["source"],
        "profile": plan["profile"],
        "transition_count": plan["transition_count"],
    }
