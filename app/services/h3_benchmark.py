"""Small, repeatable, honest same-PC MiniMax H3 benchmark records."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import statistics
import tempfile
import threading
import time
import re
import subprocess
from typing import Any, Callable, Mapping


SCHEMA_VERSION = 4
LEGACY_SCHEMA_VERSIONS = {1, 2, 3, SCHEMA_VERSION}
CASE_IDS = ("text_only", "first_frame", "first_last", "ref2va")
QUICK_TASK = {
    "width": 608,
    "height": 352,
    "frame_count": 124,
    "fps": 24,
    "sampling_steps": 4,
    "warmup_runs": 0,
    "measured_runs": 1,
}
PUBLISHED_EXTERNAL = [
    {
        "source": "NVIDIA Sol-Engine H3 OnDevice",
        "url": "https://nvlabs.github.io/Sana/Sol-Engine/H3-OnDevice/",
        "reported_speedup": "4.52x on RTX 5090 for NVIDIA's tested stack",
        "comparable_to_maestro_quick_task": False,
    },
    {
        "source": "NVIDIA Sana sol-engine branch",
        "url": "https://github.com/NVlabs/Sana/tree/sol-engine",
        "reported_speedup": "3.97x on 8x GB200 for the documented distributed recipe",
        "comparable_to_maestro_quick_task": False,
    },
]


class H3BenchmarkError(ValueError):
    pass


_SAFE_SPEC_FIELDS = {
    "hardware": {"gpu", "compute_capability", "vram_gb", "power_limit_watts"},
    "runtime": {"torch", "cuda", "triton", "model_load_state"},
    "model": {"id", "family", "quantization", "accelerator", "accelerator_version"},
    "engine": {"id", "effective_id", "tau", "dense_steps", "dense_blocks", "min_tokens"},
    "encoder": {"id", "quantization"},
}
_SAFE_PHASE_FIELDS = {
    "generation", "model_load", "spectrum_anchor_capture",
    "spectrum_offline_replay", "postprocess",
}


def _safe_mapping(group: str, value: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    return {
        key: source[key]
        for key in _SAFE_SPEC_FIELDS[group]
        if key in source and isinstance(source[key], (str, int, float, bool, type(None)))
    }


def _safe_input_shape(value: Mapping[str, Any] | None) -> dict[str, int | bool]:
    source = dict(value or {})
    result: dict[str, int | bool] = {}
    for key in ("has_start", "has_end"):
        if key in source:
            result[key] = bool(source[key])
    aliases = {
        "image_count": ("image_count", "file_count"),
        "video_count": ("video_count",),
        "audio_count": ("audio_count",),
    }
    for target, candidates in aliases.items():
        for key in candidates:
            if key in source:
                try:
                    result[target] = max(0, int(source[key] or 0))
                except (TypeError, ValueError):
                    pass
                break
    return result


def _cache_key_for_spec(spec: Mapping[str, Any]) -> str:
    # A configuration key, never a media/content hash. Python's stable JSON
    # string is retained in storage so records remain understandable.
    identity = {
        "case_id": str(spec.get("case_id") or "text_only"),
        "hardware": dict(spec.get("hardware") or {}),
        "runtime": dict(spec.get("runtime") or {}),
        "model": dict(spec.get("model") or {}),
        "engine": dict(spec.get("engine") or {}),
        "encoder": dict(spec.get("encoder") or {}),
        "input_shape": dict(spec.get("input_shape") or {}),
        "task": dict(spec.get("task") or {}),
    }
    return "h3:" + json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )


def build_benchmark_spec(
    *,
    case_id: str,
    hardware: Mapping[str, Any],
    runtime: Mapping[str, Any],
    model: Mapping[str, Any],
    engine: Mapping[str, Any],
    encoder: Mapping[str, Any],
    input_signature: Mapping[str, Any] | None = None,
    task: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if case_id not in CASE_IDS:
        raise H3BenchmarkError(f"Unknown H3 benchmark case: {case_id}")
    raw_task = {**QUICK_TASK, **dict(task or {})}
    resolved_task = {
        key: raw_task[key]
        for key in (
            "profile", "width", "height", "frame_count", "fps",
            "sampling_steps", "window_count", "processed_frame_count",
            "lora_count", "cache_enabled", "warmup_runs", "measured_runs",
            "source_audio_mode", "audio_algorithm_version",
            "video_evaluations", "audio_evaluations", "multirate_profile",
        )
        if key in raw_task and isinstance(raw_task[key], (str, int, float, bool))
    }
    profile = str(resolved_task.get("profile") or "quick")
    steps = int(resolved_task["sampling_steps"])
    frames = int(resolved_task["frame_count"])
    if steps < 2 or steps > (8 if profile == "quick" else 50):
        raise H3BenchmarkError("Benchmark sampling_steps are outside the selected profile")
    if profile == "quick" and frames != 124:
        raise H3BenchmarkError("Quick benchmark is fixed at 124 H3-grid frames")
    if profile != "quick" and frames < 1:
        raise H3BenchmarkError("Observed H3 jobs must contain at least one frame")
    signature = _safe_input_shape(input_signature)
    if case_id != "text_only" and not signature:
        raise H3BenchmarkError(f"{case_id} requires a content-free reference shape")
    source_audio_mode = str(resolved_task.get("source_audio_mode") or "native")
    if source_audio_mode not in {
        "native", "lock_source", "remix_source", "reference_only",
    }:
        raise H3BenchmarkError("Unknown H3 source-audio benchmark mode")
    audio_algorithm_version = str(
        resolved_task.get("audio_algorithm_version") or ""
    )
    if source_audio_mode == "native":
        if audio_algorithm_version:
            raise H3BenchmarkError(
                "Native H3 benchmark records cannot carry a source-audio algorithm identity"
            )
    elif audio_algorithm_version != "maestro_h3_source_audio_v1":
        raise H3BenchmarkError("Invalid H3 source-audio algorithm identity")
    multirate = str(resolved_task.get("multirate_profile") or "")
    if multirate:
        try:
            multirate_valid = (
                multirate == "t8_4v8a_evidence_v1"
                and int(resolved_task.get("video_evaluations") or 0) == 4
                and int(resolved_task.get("audio_evaluations") or 0) == 8
            )
        except (TypeError, ValueError):
            multirate_valid = False
        if not multirate_valid:
            raise H3BenchmarkError("Invalid H3 multirate benchmark identity")
    spec = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "hardware": _safe_mapping("hardware", hardware),
        "runtime": _safe_mapping("runtime", runtime),
        "model": _safe_mapping("model", model),
        "engine": _safe_mapping("engine", engine),
        "encoder": _safe_mapping("encoder", encoder),
        "input_shape": signature,
        "task": resolved_task,
    }
    spec["cache_key"] = _cache_key_for_spec(spec)
    return spec


def measure_benchmark(
    spec: Mapping[str, Any],
    executor: Callable[[dict[str, Any]], Mapping[str, Any]],
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    immutable_spec = json.loads(json.dumps(dict(spec)))
    started = clock()
    observed = dict(executor(json.loads(json.dumps(immutable_spec))))
    wall = clock() - started
    frames = int(observed.get("output_frames") or immutable_spec["task"]["frame_count"])
    if wall <= 0 or frames <= 0:
        raise H3BenchmarkError("Benchmark executor returned invalid timing/frame data")
    if observed.get("output_valid") is not True:
        raise H3BenchmarkError("Benchmark output did not pass finite/artifact validation")
    peak = observed.get("peak_gpu_memory_bytes")
    if peak is not None and int(peak) < 0:
        raise H3BenchmarkError("peak_gpu_memory_bytes cannot be negative")
    return record_observation(
        immutable_spec,
        wall_time_seconds=wall,
        output_frames=frames,
        output_valid=observed.get("output_valid") is True,
        peak_gpu_memory_bytes=observed.get("peak_gpu_memory_bytes"),
        phase_times_seconds=observed.get("phase_times_seconds"),
        actual_transformer_calls=observed.get("actual_transformer_calls"),
        forecast_transformer_calls=observed.get("forecast_transformer_calls"),
        replay_transformer_calls=observed.get("replay_transformer_calls"),
        average_power_watts=observed.get("average_power_watts"),
        energy_joules=observed.get("energy_joules"),
    )


def record_observation(
    spec: Mapping[str, Any],
    *,
    wall_time_seconds: float,
    output_frames: int,
    output_valid: bool,
    peak_gpu_memory_bytes: int | None = None,
    phase_times_seconds: Mapping[str, Any] | None = None,
    actual_transformer_calls: int | None = None,
    forecast_transformer_calls: int | None = None,
    replay_transformer_calls: int | None = None,
    average_power_watts: float | None = None,
    energy_joules: float | None = None,
) -> dict[str, Any]:
    immutable_spec = json.loads(json.dumps(dict(spec)))
    wall = float(wall_time_seconds)
    frames = int(output_frames)
    if wall <= 0 or frames <= 0:
        raise H3BenchmarkError("Benchmark executor returned invalid timing/frame data")
    if output_valid is not True:
        raise H3BenchmarkError("Benchmark output did not pass finite/artifact validation")
    if peak_gpu_memory_bytes is not None and int(peak_gpu_memory_bytes) < 0:
        raise H3BenchmarkError("peak_gpu_memory_bytes cannot be negative")
    optional_metrics: dict[str, int | float] = {}
    for key, value in (
        ("actual_transformer_calls", actual_transformer_calls),
        ("forecast_transformer_calls", forecast_transformer_calls),
        ("replay_transformer_calls", replay_transformer_calls),
    ):
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not float(value).is_integer()
            or int(value) < 0
        ):
            raise H3BenchmarkError(f"{key} must be a non-negative whole number")
        optional_metrics[key] = int(value)
    for key, value in (
        ("average_power_watts", average_power_watts),
        ("energy_joules", energy_joules),
    ):
        if value is None:
            continue
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise H3BenchmarkError(f"{key} must be a finite non-negative number")
        optional_metrics[key] = numeric
    return {
        "schema_version": SCHEMA_VERSION,
        "cache_key": immutable_spec["cache_key"],
        "spec": immutable_spec,
        "measured_local": True,
        "observed_day_utc": time.strftime("%Y-%m-%d", time.gmtime()),
        "generation_wall_time_seconds": wall,
        "effective_output_fps": frames / wall,
        "peak_gpu_memory_bytes": None if peak_gpu_memory_bytes is None else int(peak_gpu_memory_bytes),
        "phase_times_seconds": {
            key: float(value)
            for key, value in dict(phase_times_seconds or {}).items()
            if key in _SAFE_PHASE_FIELDS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0
        },
        "output_frames": frames,
        "output_valid": True,
        "sample_count": 1,
        **optional_metrics,
    }


def _sanitize_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Strip legacy media hashes, URLs, paths, seeds, and exact timestamps."""
    source = json.loads(json.dumps(dict(record)))
    raw_spec = source.get("spec")
    if not isinstance(raw_spec, dict):
        return None
    raw_task = dict(raw_spec.get("task") or {})
    safe_task = {
        key: raw_task[key]
        for key in (
            "profile", "width", "height", "frame_count", "fps",
            "sampling_steps", "window_count", "processed_frame_count",
            "lora_count", "cache_enabled", "warmup_runs", "measured_runs",
            "source_audio_mode", "audio_algorithm_version",
            "video_evaluations", "audio_evaluations", "multirate_profile",
        )
        if key in raw_task and isinstance(raw_task[key], (str, int, float, bool))
    }
    source_audio_mode = str(safe_task.get("source_audio_mode") or "native")
    audio_algorithm_version = str(safe_task.get("audio_algorithm_version") or "")
    if source_audio_mode not in {
        "native", "lock_source", "remix_source", "reference_only",
    }:
        return None
    if source_audio_mode == "native":
        if audio_algorithm_version:
            return None
    elif audio_algorithm_version != "maestro_h3_source_audio_v1":
        return None
    multirate = str(safe_task.get("multirate_profile") or "")
    if multirate and not (
        multirate == "t8_4v8a_evidence_v1"
        and safe_task.get("video_evaluations") == 4
        and safe_task.get("audio_evaluations") == 8
    ):
        return None
    safe_spec = {
        "schema_version": SCHEMA_VERSION,
        "case_id": str(raw_spec.get("case_id") or "text_only"),
        "hardware": _safe_mapping("hardware", raw_spec.get("hardware")),
        "runtime": _safe_mapping("runtime", raw_spec.get("runtime")),
        "model": _safe_mapping("model", raw_spec.get("model")),
        "engine": _safe_mapping("engine", raw_spec.get("engine")),
        "encoder": _safe_mapping("encoder", raw_spec.get("encoder")),
        "input_shape": _safe_input_shape(
            raw_spec.get("input_shape") or raw_spec.get("input_signature")
        ),
        "task": safe_task,
    }
    safe_spec["cache_key"] = _cache_key_for_spec(safe_spec)
    try:
        wall = float(source.get("generation_wall_time_seconds"))
        frames = int(source.get("output_frames") or safe_task.get("frame_count") or 0)
    except (TypeError, ValueError):
        return None
    if wall <= 0 or frames <= 0 or source.get("output_valid") is not True:
        return None
    peak = source.get("peak_gpu_memory_bytes")
    try:
        sample_count = max(1, int(source.get("sample_count") or 1))
    except (TypeError, ValueError):
        sample_count = 1
    observed_day = str(source.get("observed_day_utc") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", observed_day) is None:
        observed_day = "legacy"
    safe_metrics: dict[str, int | float] = {}
    for key in (
        "actual_transformer_calls", "forecast_transformer_calls",
        "replay_transformer_calls", "average_power_watts", "energy_joules",
    ):
        value = source.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            continue
        if key.endswith("_calls"):
            if not numeric.is_integer():
                continue
            safe_metrics[key] = int(numeric)
        else:
            safe_metrics[key] = numeric
    return {
        "schema_version": SCHEMA_VERSION,
        "cache_key": safe_spec["cache_key"],
        "spec": safe_spec,
        "measured_local": True,
        "observed_day_utc": observed_day,
        "generation_wall_time_seconds": wall,
        "effective_output_fps": frames / wall,
        "peak_gpu_memory_bytes": (
            int(peak) if isinstance(peak, (int, float)) and peak >= 0 else None
        ),
        "phase_times_seconds": {
            key: float(value)
            for key, value in dict(source.get("phase_times_seconds") or {}).items()
            if key in _SAFE_PHASE_FIELDS
            and isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)) and value >= 0
        },
        "output_frames": frames,
        "output_valid": True,
        "sample_count": sample_count,
        **safe_metrics,
    }


def _resolution(value: Any) -> tuple[int, int]:
    try:
        width, height = str(value).lower().split("x", 1)
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        raise H3BenchmarkError("H3 resolution must be WIDTHxHEIGHT")
    if width < 1 or height < 1:
        raise H3BenchmarkError("H3 resolution must be positive")
    return width, height


def normalize_estimate_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a request to non-content timing factors only."""
    width, height = _resolution(context.get("resolution") or "1344x768")
    raw_steps = context.get("num_inference_steps", 20)
    try:
        duration = float(context.get("duration_seconds") or 5)
        steps = int(raw_steps)
        window_seconds = float(context.get("window_seconds") or 15)
        overlap_frames = max(0.0, float(context.get("window_overlap") or 0))
    except (TypeError, ValueError) as exc:
        raise H3BenchmarkError("Invalid H3 duration, window, or step value") from exc
    if isinstance(raw_steps, bool) or (
        isinstance(raw_steps, float) and not raw_steps.is_integer()
    ):
        raise H3BenchmarkError("H3 estimate steps must be a whole number")
    if not 1 / 24 <= duration <= 3600:
        raise H3BenchmarkError("H3 estimate duration must be between one frame and one hour")
    if not 2 <= steps <= 50:
        raise H3BenchmarkError("H3 estimate steps must be between 2 and 50")
    if not 1 <= window_seconds <= 15:
        raise H3BenchmarkError("H3 estimate window must be between 1 and 15 seconds")
    overlap = min(overlap_frames / 24.0, window_seconds - 1 / 24)
    window_count = max(1, math.ceil(max(0.0, duration - overlap) / (window_seconds - overlap)))
    processed_seconds = duration + overlap * max(0, window_count - 1)
    custom = context.get("custom_settings")
    if not isinstance(custom, Mapping):
        custom = {}
    spectrum_profile = str(custom.get("h3_spectrum_profile") or "")
    spectrum_version = ""
    lightx2v_profile = str(custom.get("h3_lightx2v_profile") or "")
    source_audio_mode = str(custom.get("h3_source_audio_mode") or "native")
    if source_audio_mode not in {
        "native", "lock_source", "remix_source", "reference_only",
    }:
        raise H3BenchmarkError("Unknown H3 source-audio estimate mode")
    multirate = str(custom.get("h3_multirate_profile") or "")
    if multirate:
        # The dual-clock lane has no generation path or honest runtime ETA.
        raise H3BenchmarkError(
            "H3 multirate timing is unavailable until live benchmark acceptance"
        )
    if spectrum_profile == "spectrum_h3_v1":
        from models.minimax_h3.spectrum import SPECTRUM_ALGORITHM_VERSION
        spectrum_version = SPECTRUM_ALGORITHM_VERSION
    reference = context.get("reference_shape")
    if not isinstance(reference, Mapping):
        reference = {}
    safe_reference = _safe_input_shape(reference)
    semantic_count = sum(int(safe_reference.get(key) or 0) for key in (
        "image_count", "video_count", "audio_count",
    ))
    edge_count = int(bool(safe_reference.get("has_start"))) + int(bool(safe_reference.get("has_end")))
    activated = context.get("activated_loras")
    lora_count = len(activated) if isinstance(activated, (list, tuple)) else 0
    engine_id = str(custom.get("h3_attention_engine") or "sol_attn")
    if engine_id not in {"sdpa", "sol_attn", "sage2"}:
        raise H3BenchmarkError(f"Unknown H3 attention engine: {engine_id}")
    if lightx2v_profile and engine_id != "sdpa":
        raise H3BenchmarkError("LightX2V H3 estimates require Dense SDPA")
    if source_audio_mode != "native":
        from services.h3_audio import (
            H3AudioCompatibilityError,
            resolve_h3_audio_roles,
        )

        try:
            primary = int(custom.get("h3_primary_audio_ordinal", 1) or 1)
        except (TypeError, ValueError):
            primary = 1
        structural_guides = tuple(
            f"audio-slot-{index}" for index in range(1, max(1, primary) + 1)
        )
        try:
            resolve_h3_audio_roles(
                selected_model_type=str(context.get("model_type") or "minimax_h3"),
                model_def={
                    "minimax_h3_reference_mode": str(
                        context.get("model_type") or "minimax_h3"
                    ) == "minimax_h3_ref2va",
                },
                custom_settings=custom,
                sampling_steps=steps,
                attention_engine=engine_id,
                audio_prompt_type="".join(
                    "ABC"[index] for index in range(min(3, len(structural_guides)))
                ),
                audio_guides=structural_guides,
                semantic_references=semantic_count > 0,
                multisegment=window_count > 1,
                activated_loras=activated,
                loras_multipliers=context.get("loras_multipliers"),
                skip_steps_cache_type=context.get("tea_cache"),
                native_boundary=bool(
                    custom.get("h3_native_boundary_conditioning")
                ),
            )
        except H3AudioCompatibilityError as exc:
            raise H3BenchmarkError(str(exc)) from exc
    engine_signature: dict[str, Any] = {"id": engine_id}
    if engine_id == "sol_attn":
        try:
            engine_signature.update({
                "tau": float(custom.get("h3_sol_tau", 1.0)),
                "dense_steps": int(custom.get("h3_sol_dense_steps", 10)),
                "dense_blocks": int(custom.get("h3_sol_dense_blocks", 2)),
                "min_tokens": int(custom.get("h3_sol_min_tokens", 4096)),
            })
        except (TypeError, ValueError) as exc:
            raise H3BenchmarkError("Invalid H3 Sol-Attn estimator settings") from exc
    spatial_upsampling = str(context.get("spatial_upsampling") or "").strip().lower()
    upscale_width, upscale_height = width, height
    postprocess_passes = 0
    postprocess_kind = "none"
    if spatial_upsampling:
        flash_match = re.fullmatch(r"flashvsr(2pass)?(1(?:\.5)?|2(?:\.5)?|3(?:\.5)?|4)", spatial_upsampling)
        lanczos_match = re.fullmatch(r"lanczos(1\.5|2)", spatial_upsampling)
        if flash_match:
            scale = float(flash_match.group(2))
            postprocess_passes = 2 if flash_match.group(1) else 1
            postprocess_kind = "flashvsr"
        elif lanczos_match:
            scale = float(lanczos_match.group(1))
            postprocess_passes = 1
            postprocess_kind = "lanczos"
        else:
            raise H3BenchmarkError("Unsupported H3 spatial upsampling method")
        upscale_width = max(1, int(width * scale))
        upscale_height = max(1, int(height * scale))
    delivery_width, delivery_height = upscale_width, upscale_height
    delivery_resolution = str(context.get("delivery_resolution") or "").strip().lower()
    delivery_fit = str(context.get("delivery_fit") or "").strip().lower()
    if delivery_resolution:
        delivery_width, delivery_height = _resolution(delivery_resolution)
        if not spatial_upsampling:
            raise H3BenchmarkError("H3 delivery resolution requires spatial upsampling")
        if delivery_fit not in {"center_crop", "upscale_exact"}:
            raise H3BenchmarkError("Unsupported H3 delivery fit")
        if delivery_fit == "upscale_exact" and (
            delivery_width != upscale_width or delivery_height != upscale_height
        ):
            raise H3BenchmarkError("H3 upscale_exact delivery must equal the upscaled canvas")
        if delivery_width > upscale_width or delivery_height > upscale_height:
            raise H3BenchmarkError("H3 delivery target cannot exceed the upscaled canvas")
    elif delivery_fit:
        raise H3BenchmarkError("H3 delivery fit requires a delivery resolution")
    return {
        "model_type": str(context.get("model_type") or "minimax_h3"),
        "duration_seconds": duration,
        "processed_frame_count": max(1, round(processed_seconds * 24)),
        "width": width,
        "height": height,
        "sampling_steps": steps,
        "window_count": window_count,
        "engine_id": engine_id,
        "engine_signature": engine_signature,
        "reference_case": (
            "ref2va" if semantic_count else "first_last" if edge_count == 2
            else "first_frame" if edge_count else "text_only"
        ),
        "reference_count": semantic_count + edge_count,
        "lora_count": lora_count,
        "cache_enabled": bool(context.get("tea_cache")),
        "accelerator": (
            "spectrum"
            if spectrum_profile == "spectrum_h3_v1"
            else "lightx2v"
            if lightx2v_profile == "h3_lightx2v_fl2v_4_v1"
            else "turbo"
            if str(custom.get("h3_turbo_profile") or "") == "h3_turbo_v4"
            else "native"
        ),
        "accelerator_version": spectrum_version or lightx2v_profile,
        "source_audio_mode": source_audio_mode,
        "audio_algorithm_version": (
            "maestro_h3_source_audio_v1"
            if source_audio_mode != "native" else ""
        ),
        "spatial_upsampling": spatial_upsampling,
        "delivery_width": delivery_width,
        "delivery_height": delivery_height,
        "upscale_width": upscale_width,
        "upscale_height": upscale_height,
        "delivery_fit": delivery_fit,
        "postprocess_passes": postprocess_passes,
        "postprocess_kind": postprocess_kind,
    }


def add_h3_postprocess_estimate(
    estimate: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Add an explicit delivery-stage estimate to generation-only timing."""
    target = normalize_estimate_context(context)
    result = dict(estimate)
    generation_seconds = float(result.get("generation_seconds") or result.get("seconds") or 0)
    method = target["spatial_upsampling"]
    postprocess_seconds = 0.0
    if target["postprocess_kind"] == "flashvsr":
        # Conservative fixed-machine seed until privacy-safe completed-stage
        # observations exist. Cost follows delivered pixel-frames and counts
        # both shifted inference passes explicitly.
        output_megapixels = (
            target["upscale_width"] * target["upscale_height"] / 1_000_000
        )
        postprocess_seconds = (
            output_megapixels
            * target["processed_frame_count"]
            * target["postprocess_passes"]
            * 0.30
        )
    elif target["postprocess_kind"] == "lanczos":
        output_megapixels = (
            target["delivery_width"] * target["delivery_height"] / 1_000_000
        )
        postprocess_seconds = output_megapixels * target["processed_frame_count"] * 0.003
    if target["delivery_fit"] == "center_crop":
        postprocess_seconds += (
            target["delivery_width"] * target["delivery_height"] / 1_000_000
            * target["processed_frame_count"] * 0.003
        )
    result["generation_seconds"] = round(max(1.0, generation_seconds))
    result["postprocess_seconds"] = round(max(0.0, postprocess_seconds))
    result["delivery_resolution"] = (
        f"{target['delivery_width']}x{target['delivery_height']}"
    )
    result["postprocess_method"] = method or None
    if postprocess_seconds <= 0:
        return result
    base_range = dict(result.get("range_seconds") or {})
    base_low = float(base_range.get("low") or generation_seconds * 0.5)
    base_high = float(base_range.get("high") or generation_seconds * 1.5)
    result["seconds"] = round(generation_seconds + postprocess_seconds)
    result["range_seconds"] = {
        "low": max(1, round(base_low + postprocess_seconds * 0.50)),
        "high": max(1, round(base_high + postprocess_seconds * 1.75)),
    }
    result["confidence"] = "low"
    result["source"] = str(result.get("source") or "estimate") + "+flashvsr_baseline"
    result["matched_factors"] = [
        *list(result.get("matched_factors") or []),
        (
            f"{target['width']}x{target['height']} native -> "
            f"{target['upscale_width']}x{target['upscale_height']} learned upscale -> "
            f"{target['delivery_width']}x{target['delivery_height']} delivery; "
            f"FlashVSR {target['postprocess_passes']}-pass"
            + (" + center crop/downsample" if target["delivery_fit"] == "center_crop" else "")
        ),
    ]
    result["uncertainty_reasons"] = [
        *list(result.get("uncertainty_reasons") or []),
        (
            "FlashVSR delivery time is a fixed-PC pixel-frame baseline until "
            "a completed local upscale observation is available; first download time is separate."
        ),
    ]
    return result


def estimate_h3_output(
    context: Mapping[str, Any],
    records: list[Mapping[str, Any]],
    *,
    model_resident: bool | None = None,
) -> dict[str, Any]:
    """Estimate generation time from safe same-PC observations or a baseline."""
    target = normalize_estimate_context(context)
    estimates: list[float] = []
    compatible_estimates: list[float] = []
    exact = 0
    same_model = 0
    observed_samples = 0
    compatible_samples = 0
    compatible_exact_case = 0
    for raw in records:
        record = _sanitize_record(raw)
        if record is None:
            continue
        spec = record["spec"]
        task = spec["task"]
        # The generation call timer includes a cold model load. Until loader
        # phase instrumentation can split it exactly, cold samples are useful
        # for load telemetry but are never folded into denoise/output time.
        if str(spec.get("runtime", {}).get("model_load_state") or "unknown") != "resident":
            continue
        try:
            source_pixels = int(task["width"]) * int(task["height"])
            source_frames = int(task.get("processed_frame_count") or task["frame_count"])
            source_steps = int(task["sampling_steps"])
            wall = float(record["generation_wall_time_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if source_pixels <= 0 or source_frames <= 0 or source_steps <= 0 or wall <= 0:
            continue
        model_match = str(spec.get("model", {}).get("id") or "") == target["model_type"]
        accelerator_match = str(
            spec.get("model", {}).get("accelerator") or "native"
        ) == target["accelerator"]
        if accelerator_match and target["accelerator"] in {"spectrum", "lightx2v"}:
            accelerator_match = str(
                spec.get("model", {}).get("accelerator_version") or ""
            ) == target["accelerator_version"]
        case_match = str(spec.get("case_id") or "text_only") == target["reference_case"]
        source_engine = dict(spec.get("engine") or {})
        source_engine_id = str(
            source_engine.get("effective_id") or source_engine.get("id") or ""
        )
        declared_engine_id = str(source_engine.get("id") or source_engine_id)
        engine_match = source_engine_id == target["engine_id"]
        if engine_match and target["engine_id"] == "sol_attn":
            engine_match = all(
                source_engine.get(key) == target["engine_signature"].get(key)
                for key in ("tau", "dense_steps", "dense_blocks", "min_tokens")
            )
        # A Sol run whose dense-step policy covers the entire schedule executes
        # the exact dense SDPA fallback at every block. It is useful as a
        # conservative local upper bound for another Sol policy or SDPA, but
        # remains explicitly non-exact because wrapper/config overhead and the
        # requested policy differ.
        try:
            source_dense_sol = (
                declared_engine_id == "sol_attn"
                and int(source_engine.get("dense_steps") or 0) >= source_steps
            )
        except (TypeError, ValueError):
            source_dense_sol = False
        compatible_engine = (
            source_dense_sol
            and target["engine_id"] in {"sol_attn", "sdpa"}
        )
        cache_match = bool(task.get("cache_enabled")) == target["cache_enabled"]
        source_audio_mode_match = str(
            task.get("source_audio_mode") or "native"
        ) == target["source_audio_mode"]
        if not (
            model_match and accelerator_match and cache_match
            and source_audio_mode_match
            and (engine_match or compatible_engine)
        ):
            continue
        count = max(1, int(record.get("sample_count") or 1))
        weight = 2.0 if case_match else 1.0
        scaled = wall
        scaled *= ((target["width"] * target["height"]) / source_pixels) ** 0.90
        scaled *= target["processed_frame_count"] / source_frames
        scaled *= target["sampling_steps"] / source_steps
        source_shape = spec.get("input_shape") or {}
        source_refs = sum(int(source_shape.get(key) or 0) for key in (
            "image_count", "video_count", "audio_count",
        )) + int(bool(source_shape.get("has_start"))) + int(bool(source_shape.get("has_end")))
        scaled *= (
            1.0 + min(0.40, target["reference_count"] * 0.08)
        ) / (1.0 + min(0.40, source_refs * 0.08))
        source_loras = max(0, int(task.get("lora_count") or 0))
        scaled *= (
            1.0 + min(0.25, target["lora_count"] * 0.05)
        ) / (1.0 + min(0.25, source_loras * 0.05))
        weighted = [scaled] * max(1, round(weight * min(count, 8)))
        if engine_match:
            observed_samples += count
            same_model += count
            exact += count * int(case_match)
            estimates.extend(weighted)
        else:
            compatible_samples += count
            compatible_exact_case += count * int(case_match)
            compatible_estimates.extend(weighted)

    matched_factors = [
        f"{target['width']}x{target['height']}",
        f"{target['sampling_steps']} steps",
        f"{target['window_count']} native window" + ("s" if target["window_count"] != 1 else ""),
        target["engine_id"],
        target["reference_case"],
        f"{target['lora_count']} LoRA" + ("s" if target["lora_count"] != 1 else ""),
        "cache on" if target["cache_enabled"] else "cache off",
        f"{target['accelerator']} accelerator",
        f"{target['source_audio_mode']} source-audio mode",
    ]
    if target["accelerator_version"]:
        matched_factors.append(target["accelerator_version"])
    if target["engine_id"] == "sol_attn":
        matched_factors.append(
            "Sol " + ", ".join(
                f"{key}={target['engine_signature'][key]}"
                for key in ("tau", "dense_steps", "dense_blocks", "min_tokens")
            )
        )
    uncertainty: list[str] = []
    if estimates:
        seconds = statistics.median(estimates)
        source = "local_observations"
        sample_count = observed_samples
        # A different reference-conditioning case is an extrapolation even
        # when the checkpoint/engine match. It must not become medium merely
        # because many text-only runs exist (or vice versa).
        confidence = "high" if exact >= 3 else "medium" if exact else "low"
        spread = 0.18 if confidence == "high" else 0.32 if confidence == "medium" else 0.50
        if exact < 3:
            uncertainty.append("Few exact same-model, engine, reference, and canvas observations.")
        if exact == 0 and same_model:
            uncertainty.append("Reference-conditioning timing is extrapolated from a different case.")
    elif compatible_estimates:
        seconds = statistics.median(compatible_estimates)
        source = "local_compatible_upper_bound"
        sample_count = compatible_samples
        confidence = "low"
        spread = 0.50
        uncertainty.append(
            "No exact engine-policy observation is available; this uses a compatible all-dense local run as a conservative upper bound."
        )
        if compatible_exact_case == 0:
            uncertainty.append("Reference-conditioning timing is extrapolated from a different case.")
    else:
        # Fixed-PC RTX 5090 fallback anchor, calibrated from the validated
        # resident all-dense 608x352 / 124-frame / 20-step H3 run. The old
        # 160-second four-step seed counted cold-load work and inflated a
        # fresh High estimate into tens of minutes. Exact privacy-safe local
        # observations still replace this conservative seed automatically.
        seconds = 45.2
        seconds *= ((target["width"] * target["height"]) / (608 * 352)) ** 0.90
        seconds *= target["processed_frame_count"] / 124
        seconds *= target["sampling_steps"] / 20
        if target["accelerator"] == "turbo":
            # Managed Turbo has some fixed adapter/scheduler overhead, so
            # scaling only by authored step count underestimates its short
            # four/eight-step runs.
            seconds *= 1.40
        seconds *= 1.0 + min(0.40, target["reference_count"] * 0.08)
        seconds *= 1.0 + min(0.25, target["lora_count"] * 0.05)
        if target["cache_enabled"]:
            seconds *= 0.80
        source = "rtx_5090_baseline"
        sample_count = 0
        confidence = "low"
        spread = 0.55
        uncertainty.append("No compatible same-PC observation is available yet.")
        if target["engine_id"] == "sage2":
            uncertainty.append("SageAttention2++ has no live MiniMax H3 visual validation yet.")
        if target["accelerator"] == "spectrum":
            uncertainty.append(
                "Spectrum Experimental has no same-PC acceptance record; this baseline assumes no speedup."
            )
        if target["accelerator"] == "lightx2v":
            uncertainty.append(
                "LightX2V Experimental has no same-PC acceptance record; this baseline assumes native per-step cost."
            )
    if target["window_count"] > 1:
        uncertainty.append("Window transitions and checkpoint switches can add variance.")
    if target["source_audio_mode"] != "native":
        confidence = "low"
        uncertainty.append(
            "Experimental source-audio encode/conditioning time is uncalibrated; "
            "non-native observations are excluded until exact mode identity is captured."
        )
    model_load_state = "resident" if model_resident is True else "cold" if model_resident is False else "unknown"
    model_load_seconds = 0 if model_resident is True else 150 if model_resident is False else None
    seconds = max(1.0, seconds)
    estimate = {
        "seconds": round(seconds),
        "range_seconds": {
            "low": max(1, round(seconds * (1.0 - spread))),
            "high": max(1, round(seconds * (1.0 + spread))),
        },
        "confidence": confidence,
        "sample_count": sample_count,
        "source": source,
        "model_load_seconds": model_load_seconds,
        "model_load_state": model_load_state,
        "download_seconds": None,
        "matched_factors": matched_factors,
        "uncertainty_reasons": uncertainty,
    }
    return add_h3_postprocess_estimate(estimate, context)


def aggregate_h3_estimates(estimates: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Sum content-free estimates for a confirmed multi-segment plan."""
    if not estimates:
        raise H3BenchmarkError("A segment aggregate requires at least one estimate")
    confidence_rank = {"calibrating": 0, "low": 1, "medium": 2, "high": 3}
    confidence = min(
        (str(item.get("confidence") or "low") for item in estimates),
        key=lambda value: confidence_rank.get(value, 0),
    )
    load_values = [item.get("model_load_seconds") for item in estimates]
    load_seconds = (
        round(sum(float(value) for value in load_values if value is not None))
        if any(value is not None for value in load_values) else None
    )
    load_states = {str(item.get("model_load_state") or "unknown") for item in estimates}
    load_state = (
        "cold" if "cold" in load_states
        else "unknown" if "unknown" in load_states
        else "resident"
    )
    reasons: list[str] = []
    for item in estimates:
        for reason in item.get("uncertainty_reasons") or []:
            safe_reason = str(reason)
            if safe_reason not in reasons:
                reasons.append(safe_reason)
    reasons.append("Confirmed long-form segment estimates are summed; checkpoint switches can add variance.")
    return {
        "seconds": round(sum(float(item.get("seconds") or 0) for item in estimates)),
        "range_seconds": {
            "low": max(1, round(sum(float((item.get("range_seconds") or {}).get("low") or 0) for item in estimates))),
            "high": max(1, round(sum(float((item.get("range_seconds") or {}).get("high") or 0) for item in estimates))),
        },
        "confidence": confidence,
        "sample_count": sum(int(item.get("sample_count") or 0) for item in estimates),
        "source": "confirmed_segment_aggregate",
        "model_load_seconds": load_seconds,
        "model_load_state": load_state,
        "download_seconds": None,
        "matched_factors": [f"{len(estimates)} confirmed planned segments"],
        "uncertainty_reasons": reasons,
        "generation_seconds": round(sum(
            float(item.get("generation_seconds") or item.get("seconds") or 0)
            for item in estimates
        )),
        "postprocess_seconds": round(sum(
            float(item.get("postprocess_seconds") or 0) for item in estimates
        )),
    }


def validate_output_artifacts(
    out_dir: str | os.PathLike[str],
    output_files: list[str],
    *,
    probe_runner: Callable[..., Any] = subprocess.run,
) -> bool:
    """Validate a direct generated media child without retaining its path."""
    resolved_out_dir = os.path.realpath(os.fspath(out_dir))
    for name in output_files:
        if os.path.basename(str(name)) != str(name):
            continue
        candidate = os.path.realpath(os.path.join(resolved_out_dir, str(name)))
        if (
            os.path.dirname(candidate) != resolved_out_dir
            or not os.path.isfile(candidate)
            or os.path.getsize(candidate) <= 0
        ):
            continue
        try:
            probe = probe_runner(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "stream=codec_type", "-of", "csv=p=0", candidate,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0 and any(
            value in {"video", "audio"} for value in str(probe.stdout).split()
        ):
            return True
    return False


def build_benchmark_report(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [json.loads(json.dumps(dict(record))) for record in records]
    for row in rows:
        wall = float(row["generation_wall_time_seconds"])
        spec = row.get("spec", {})
        baseline = next((
            item for item in rows
            if item.get("spec", {}).get("case_id") == "text_only"
            and item.get("spec", {}).get("engine", {}).get("id") == "sdpa"
            and item.get("spec", {}).get("hardware") == spec.get("hardware")
            and item.get("spec", {}).get("runtime") == spec.get("runtime")
            and item.get("spec", {}).get("model") == spec.get("model")
            and item.get("spec", {}).get("encoder") == spec.get("encoder")
            and item.get("spec", {}).get("task") == spec.get("task")
        ), None)
        baseline_wall = (
            float(baseline["generation_wall_time_seconds"]) if baseline else None
        )
        row["normalized_speed_index"] = (
            None if not baseline_wall else 100.0 * baseline_wall / wall
        )
        comparable_text = next((
            item for item in rows
            if item.get("spec", {}).get("case_id") == "text_only"
            and item.get("spec", {}).get("hardware") == row.get("spec", {}).get("hardware")
            and item.get("spec", {}).get("runtime") == row.get("spec", {}).get("runtime")
            and item.get("spec", {}).get("model") == row.get("spec", {}).get("model")
            and item.get("spec", {}).get("engine") == row.get("spec", {}).get("engine")
            and item.get("spec", {}).get("encoder") == row.get("spec", {}).get("encoder")
            and item.get("spec", {}).get("task") == row.get("spec", {}).get("task")
        ), None)
        text_wall = float(comparable_text["generation_wall_time_seconds"]) if comparable_text else None
        row["reference_overhead_percent"] = (
            None if not text_wall else 100.0 * (wall / text_wall - 1.0)
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "quick_task": dict(QUICK_TASK),
        "normalization": "Dense-SDPA text-only on the same captured hardware/runtime = 100",
        "records": rows,
        "published_external": list(PUBLISHED_EXTERNAL),
    }


class H3BenchmarkCache:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> tuple[list[dict[str, Any]], bool]:
        if not self.path.is_file():
            return [], False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return [], False
        if not isinstance(payload, dict) or payload.get("schema_version") not in LEGACY_SCHEMA_VERSIONS:
            return [], False
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            return [], False
        records = [safe for item in raw_records if isinstance(item, dict) and (safe := _sanitize_record(item))]
        changed = payload.get("schema_version") != SCHEMA_VERSION or records != raw_records
        return records, changed

    def _write(self, records: list[Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": SCHEMA_VERSION, "records": records}, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            records, changed = self._read()
            if changed:
                self._write(records)
            return records

    def put(self, record: Mapping[str, Any]) -> None:
        with self._lock:
            safe = _sanitize_record(record)
            if safe is None:
                raise H3BenchmarkError("Unsafe or invalid H3 benchmark record")
            records, _ = self._read()
            previous = next(
                (item for item in records if item.get("cache_key") == safe["cache_key"]),
                None,
            )
            records = [item for item in records if item.get("cache_key") != safe["cache_key"]]
            if previous is not None:
                old_count = max(1, int(previous.get("sample_count") or 1))
                new_count = max(1, int(safe.get("sample_count") or 1))
                total = old_count + new_count
                safe["generation_wall_time_seconds"] = (
                    float(previous["generation_wall_time_seconds"]) * old_count
                    + float(safe["generation_wall_time_seconds"]) * new_count
                ) / total
                safe["sample_count"] = total
                safe["effective_output_fps"] = (
                    int(safe["output_frames"]) / safe["generation_wall_time_seconds"]
                )
                for key in (
                    "actual_transformer_calls", "forecast_transformer_calls",
                    "replay_transformer_calls", "average_power_watts", "energy_joules",
                ):
                    old_value = previous.get(key)
                    new_value = safe.get(key)
                    if not isinstance(old_value, (int, float)) or isinstance(old_value, bool):
                        continue
                    if not isinstance(new_value, (int, float)) or isinstance(new_value, bool):
                        continue
                    if key.endswith("_calls"):
                        if int(old_value) == int(new_value):
                            safe[key] = int(new_value)
                        else:
                            # A call count is structural, not a noisy metric.
                            # Conflicting values under one configuration key
                            # are not averaged into a fictitious fractional run.
                            safe.pop(key, None)
                    else:
                        safe[key] = (
                            float(old_value) * old_count + float(new_value) * new_count
                        ) / total
            records.append(safe)
            self._write(records)


__all__ = [
    "CASE_IDS", "QUICK_TASK", "PUBLISHED_EXTERNAL", "H3BenchmarkCache",
    "H3BenchmarkError", "build_benchmark_spec", "measure_benchmark",
    "record_observation", "build_benchmark_report", "estimate_h3_output",
    "normalize_estimate_context", "validate_output_artifacts",
]
