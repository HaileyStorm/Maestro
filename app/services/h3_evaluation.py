"""Deterministic, model-free MiniMax H3 evaluation manifests and reports.

This module deliberately does not import the H3 runtime.  It captures the
released geometry and pinned component provenance needed to hand a portable
manifest to an optional executor.  With no executor, evaluation is an honest
offline preflight: execution is skipped and observed metrics are unavailable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import ntpath
import posixpath
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any


SCHEMA_VERSION = 1
CATALOG_PINNED_AS_OF = "2026-08-06"
EXPERIMENTAL_CATALOG_UPDATED_AS_OF = "2026-08-25"

MINIMAX_H3_FL2VA_ID = "minimax_h3"
MINIMAX_H3_REF2VA_ID = "minimax_h3_ref2va"
MINIMAX_H3_10EROS_BETA3_SKIP_ID = (
    "minimax_h3_10eros_beta3_turbo_hybrid_skip_edges"
)
MINIMAX_H3_10EROS_BETA3_FULL_ID = (
    "minimax_h3_10eros_beta3_turbo_hybrid_full"
)

MINIMAX_H3_COMFY_FL2VA_REVISION = "0543966fbdce5ba05709a8f2031c94bdba629b4a"
MINIMAX_H3_COMFY_REF2VA_REVISION = "eb8a16107c595128b3a578f82d2ce2f75920c355"
MINIMAX_H3_OFFICIAL_REVISION = "5d9b308a59ab12e67147f191e184baf704185bd1"
MINIMAX_H3_DIFFUSERS_REVISION = "abc5e9bf71fd38f53cd471bc3acaa84bc5ecbfdc"
MINIMAX_H3_WAN2GP_REF2VA_REVISION = "fa79896eadbcb048dc13e76233b3b72486b522a8"

MINIMAX_H3_EXPERIMENTAL_REVISION = "8b48334e6263a39b34eef85f9f5e271ba4506945"
MINIMAX_H3_HERETIC_ENCODER_REVISION = "e8967f6a39ea5b4939a1aff81be3e8706490c0e8"

NATIVE_FPS = 24
CONDITIONING_ENCODE_SEED = 42
VIDEO_SCHEDULER_SHIFT = 12.0
AUDIO_SCHEDULER_SHIFT = 3.0
FRAME_GRID_MODULUS = 17
FRAME_GRID_REMAINDER = 5
MAX_SEED = 2**32 - 1

LEGAL_CANVASES = (
    "1344x768",
    "768x1344",
    "1024x768",
    "768x1024",
    "768x768",
    "1152x640",
    "640x1152",
    "960x544",
    "544x960",
    "864x480",
    "480x864",
    "640x640",
    "608x352",
    "352x608",
)

_MODE_LIMITS = {
    MINIMAX_H3_FL2VA_ID: {
        "minimum_frames": 124,
        "maximum_frames": 345,
        "minimum_duration_seconds": 5.0,
        "maximum_duration_seconds": 15.0,
        "conditioning_mode": "first_last_frames",
    },
    MINIMAX_H3_REF2VA_ID: {
        "minimum_frames": 107,
        "maximum_frames": 345,
        "minimum_duration_seconds": 4.0,
        "maximum_duration_seconds": 15.0,
        "conditioning_mode": "semantic_references",
        "reference_limits": {
            "image_count": 9,
            "video_count": 3,
            "audio_count": 3,
            "mixed_file_count": 12,
            "reference_video_duration_seconds": {
                "minimum": 2.0,
                "maximum": 15.0,
                "total_maximum": 15.0,
            },
            "reference_audio_duration_seconds": {
                "minimum": 2.0,
                "maximum": 15.0,
                "total_maximum": 15.0,
            },
        },
    },
    MINIMAX_H3_10EROS_BETA3_SKIP_ID: {
        "minimum_frames": 124,
        "maximum_frames": 345,
        "minimum_duration_seconds": 5.0,
        "maximum_duration_seconds": 15.0,
        "conditioning_mode": "scaffold_only",
    },
    MINIMAX_H3_10EROS_BETA3_FULL_ID: {
        "minimum_frames": 124,
        "maximum_frames": 345,
        "minimum_duration_seconds": 5.0,
        "maximum_duration_seconds": 15.0,
        "conditioning_mode": "scaffold_only",
    },
}

_EXPERIMENTAL_W4A8_ID = "kijai_minimax_h3_w4a8_convrot"
_EXPERIMENTAL_ENCODER_ID = "ethanfel_qwen3vl_32b_ultra_heretic_h3_int8_convrot"

_PROFILE_CATALOG = {
    MINIMAX_H3_FL2VA_ID: {
        "id": MINIMAX_H3_FL2VA_ID,
        "label": "MiniMax H3 Base (FL2VA)",
        "component_role": "video_model",
        "model_type": MINIMAX_H3_FL2VA_ID,
        "experimental": False,
        "enabled_by_default": True,
        "repositories": [
            {
                "repository": "Comfy-Org/MiniMax-H3",
                "revision": MINIMAX_H3_COMFY_FL2VA_REVISION,
                "artifacts": [
                    "diffusion_models/minimax_h3_fl2va_pruned_fp8_scaled.safetensors",
                    "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                    "vae/minimax_h3_video_vae_fp16.safetensors",
                    "vae/minimax_h3_audio_vae_fp32.safetensors",
                ],
            },
            {
                "repository": "MiniMaxAI/MiniMax-H3",
                "revision": MINIMAX_H3_OFFICIAL_REVISION,
                "purpose": "processor and text-encoder configuration",
            },
            {
                "repository": "huggingface/diffusers",
                "revision": MINIMAX_H3_DIFFUSERS_REVISION,
                "purpose": "video/audio VAE and scheduler implementation derivation",
            },
        ],
        "source_anchors": [
            "app/models/minimax_h3/minimax_h3_handler.py",
            "app/models/minimax_h3/UPSTREAM.md",
        ],
    },
    MINIMAX_H3_REF2VA_ID: {
        "id": MINIMAX_H3_REF2VA_ID,
        "label": "MiniMax H3 Base (Ref2VA)",
        "component_role": "video_model",
        "model_type": MINIMAX_H3_REF2VA_ID,
        "experimental": False,
        "enabled_by_default": True,
        "repositories": [
            {
                "repository": "Comfy-Org/MiniMax-H3",
                "revision": MINIMAX_H3_COMFY_REF2VA_REVISION,
                "artifacts": [
                    "diffusion_models/minimax_h3_ref2va_pruned_fp8_scaled.safetensors",
                ],
            },
            {
                "repository": "Comfy-Org/MiniMax-H3",
                "revision": MINIMAX_H3_COMFY_FL2VA_REVISION,
                "purpose": "shared conditioner and video/audio VAEs",
            },
            {
                "repository": "MiniMaxAI/MiniMax-H3",
                "revision": MINIMAX_H3_OFFICIAL_REVISION,
                "purpose": "processor and text-encoder configuration",
            },
            {
                "repository": "deepbeepmeep/Wan2GP",
                "revision": MINIMAX_H3_WAN2GP_REF2VA_REVISION,
                "purpose": "Ref2VA mixed-media and packed-geometry reference",
            },
            {
                "repository": "huggingface/diffusers",
                "revision": MINIMAX_H3_DIFFUSERS_REVISION,
                "purpose": "video/audio VAE and scheduler implementation derivation",
            },
        ],
        "source_anchors": [
            "app/models/minimax_h3/minimax_h3_handler.py",
            "app/models/minimax_h3/UPSTREAM.md",
        ],
    },
    _EXPERIMENTAL_W4A8_ID: {
        "id": _EXPERIMENTAL_W4A8_ID,
        "label": "Kijai MiniMax H3 experimental W4A8 + ConvRot VAE",
        "component_role": "video_model_bundle",
        "model_type": MINIMAX_H3_FL2VA_ID,
        "experimental": True,
        "enabled_by_default": False,
        "repository": "Kijai/MiniMax-H3-experimental",
        "revision": MINIMAX_H3_EXPERIMENTAL_REVISION,
        "artifacts": [
            {
                "path": "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors",
                "role": "W4A8 FL2VA video model",
            },
            {
                "path": "minimax_h3_video_vae_int8_convrot.safetensors",
                "role": "INT8 ConvRot video VAE",
            },
        ],
        "license": "not_declared_in_model_card",
        "upstream_pr_notes": [
            {
                "url": "https://github.com/Comfy-Org/comfy-kitchen/pull/90",
                "note": "The pinned model card says W4A8 is work in progress and for testing only.",
            },
            {
                "url": "https://github.com/Comfy-Org/ComfyUI/pull/15334",
                "note": "The pinned model card says the INT8 ConvRot VAE currently requires this PR.",
            },
        ],
    },
    _EXPERIMENTAL_ENCODER_ID: {
        "id": _EXPERIMENTAL_ENCODER_ID,
        "label": "Qwen3-VL-32B Ultra Heretic H3 INT8 ConvRot encoder",
        "component_role": "conditioning_encoder_only",
        "model_type": None,
        "video_model": False,
        "experimental": True,
        "enabled_by_default": False,
        "repository": "ethanfel/Qwen3-VL-32B-Ultra-Heretic-H3-ComfyUI-INT8-ConvRot",
        "revision": MINIMAX_H3_HERETIC_ENCODER_REVISION,
        "artifact": "qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot.safetensors",
        "license": "Apache-2.0",
        "notes": [
            "Encoder option only; it is not a MiniMax H3 video model.",
            "The pinned model card describes this as an abliterated/uncensored Qwen3-VL derivative.",
        ],
        "upstream_pr_notes": [],
    },
    MINIMAX_H3_10EROS_BETA3_SKIP_ID: {
        "id": MINIMAX_H3_10EROS_BETA3_SKIP_ID,
        "label": "10Eros H3 Beta3 TURBO Hybrid INT8 ConvRot (skip edges)",
        "component_role": "video_model_bundle",
        "model_type": MINIMAX_H3_10EROS_BETA3_SKIP_ID,
        "experimental": True,
        "enabled_by_default": False,
        "execution_available": False,
        "automatic_fallback": False,
        "scaffold_only": True,
        "repository": "cicalooo/10Eros-Max-h3-int8-convrot",
        "pinned_as_of": EXPERIMENTAL_CATALOG_UPDATED_AS_OF,
        "repository_head": "dbdd87944063bc01d8062bae1dba12212ca4061f",
        "revision": "09beb98782a6feb2f44c39c46179743ca8607c6c",
        "artifact": (
            "10Eros_Max_h3_TURBO-hybrid_beta3_int8_convrot_"
            "skip_edges.safetensors"
        ),
        "artifact_size_bytes": 22_513_576_472,
        "artifact_sha256": (
            "a5ae4559cf19b0830adc1de6e8355d10eaf10524f78e9851a189a80990e6963a"
        ),
        "mode": "turbo_hybrid",
        "quantization": {
            "format": "int8_tensorwise",
            "scale_method": "per_channel_absmax",
            "convrot": True,
            "convrot_groupsize": 256,
            "source_dtype": "bfloat16",
        },
        "layer_policy": {
            "marker_count": 184,
            "quantized_blocks": list(range(2, 48)),
            "bf16_edge_blocks": [0, 1, 48, 49],
        },
        "maestro_experiment_policy": {
            "evidence_class": "provisional_maestro_experiment_policy",
            "schedule": {
                "steps": 6,
                "sampler_candidates": ["er_sde/simple", "multires/simple"],
            },
            "incompatible_stacking": [
                "maestro_turbo", "spectrum", "lightx2v", "sage_attention",
                "step_cache",
            ],
            "priority": 1,
        },
    },
    MINIMAX_H3_10EROS_BETA3_FULL_ID: {
        "id": MINIMAX_H3_10EROS_BETA3_FULL_ID,
        "label": "10Eros H3 Beta3 TURBO Hybrid INT8 ConvRot (full)",
        "component_role": "video_model_bundle",
        "model_type": MINIMAX_H3_10EROS_BETA3_FULL_ID,
        "experimental": True,
        "enabled_by_default": False,
        "execution_available": False,
        "automatic_fallback": False,
        "scaffold_only": True,
        "repository": "cicalooo/10Eros-Max-h3-int8-convrot",
        "pinned_as_of": EXPERIMENTAL_CATALOG_UPDATED_AS_OF,
        "repository_head": "dbdd87944063bc01d8062bae1dba12212ca4061f",
        "revision": "84ea7a6ec06e0cb5f2f35615e25e3529c5ec6c02",
        "artifact": "10Eros_Max_h3_TURBO-hybrid_beta3_int8_convrot.safetensors",
        "artifact_size_bytes": 20_973_147_816,
        "artifact_sha256": (
            "ebd0cb25273253213028bea0289da4c5c94929027ed9191fbb24fc924d4a8f0d"
        ),
        "mode": "turbo_hybrid",
        "quantization": {
            "format": "int8_tensorwise",
            "scale_method": "per_channel_absmax",
            "convrot": True,
            "convrot_groupsize": 256,
            "source_dtype": "bfloat16",
        },
        "layer_policy": {
            "marker_count": 200,
            "quantized_blocks": list(range(50)),
            "bf16_edge_blocks": [],
        },
        "maestro_experiment_policy": {
            "evidence_class": "provisional_maestro_experiment_policy",
            "schedule": {
                "steps": 6,
                "sampler_candidates": ["er_sde/simple", "multires/simple"],
            },
            "incompatible_stacking": [
                "maestro_turbo", "spectrum", "lightx2v", "sage_attention",
                "step_cache",
            ],
            "priority": 2,
        },
    },
}

_METRIC_TYPES = {
    "generation_wall_time_seconds": "nonnegative_number",
    "peak_gpu_memory_bytes": "nonnegative_integer",
    "output_video_frames": "positive_integer",
    "output_video_fps": "positive_number",
    "output_duration_seconds": "positive_number",
    "output_audio_sample_rate_hz": "positive_integer",
    "output_audio_channels": "positive_integer",
    "artifact_size_bytes": "nonnegative_integer",
    "artifact_sha256": "sha256",
}

_LINEAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class H3EvaluationError(ValueError):
    """Raised when an H3 evaluation manifest or executor result is invalid."""


def get_h3_profile_catalog() -> dict[str, Any]:
    """Return the base pin plus isolated, dated experimental additions."""
    return {
        "pinned_as_of": CATALOG_PINNED_AS_OF,
        "experimental_updated_as_of": EXPERIMENTAL_CATALOG_UPDATED_AS_OF,
        "profiles": copy.deepcopy(_PROFILE_CATALOG),
    }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _fingerprint(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical_bytes(value)).hexdigest()[:24]}"


def _require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise H3EvaluationError(f"{name} must be a boolean")
    return value


def _validate_lineage_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _LINEAGE_PATTERN.fullmatch(value):
        raise H3EvaluationError(
            f"{name} must be a stable 1-128 character identifier without path separators"
        )
    return value


def _relative_path(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise H3EvaluationError(f"{name} must be a non-empty relative path")
    if ntpath.isabs(value) or posixpath.isabs(value) or ntpath.splitdrive(value)[0]:
        raise H3EvaluationError(f"{name} must not be an absolute path")
    normalized = value.replace("\\", "/")
    if normalized != posixpath.normpath(normalized):
        raise H3EvaluationError(f"{name} must be a normalized relative path")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise H3EvaluationError(f"{name} must stay within its relative artifact root")
    return normalized


def _path_list(values: Any, name: str) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise H3EvaluationError(f"{name} must be a sequence of relative paths")
    return [_relative_path(value, f"{name}[{index}]") for index, value in enumerate(values)]


def _timed_reference_list(values: Any, name: str) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise H3EvaluationError(f"{name} must be a sequence of reference objects")
    result = []
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            raise H3EvaluationError(f"{name}[{index}] must be an object")
        if set(item) != {"path", "duration_seconds"}:
            raise H3EvaluationError(
                f"{name}[{index}] must contain only path and duration_seconds"
            )
        duration = item["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise H3EvaluationError(f"{name}[{index}].duration_seconds must be numeric")
        duration = float(duration)
        if not math.isfinite(duration):
            raise H3EvaluationError(f"{name}[{index}].duration_seconds must be finite")
        result.append({
            "path": _relative_path(item["path"], f"{name}[{index}].path"),
            "duration_seconds": duration,
        })
    return result


def _normalize_conditioning(model_type: str, conditioning: Any) -> dict[str, Any]:
    raw = {} if conditioning is None else conditioning
    if not isinstance(raw, Mapping):
        raise H3EvaluationError("conditioning must be an object")
    mode = _MODE_LIMITS[model_type]["conditioning_mode"]
    if mode == "scaffold_only":
        if raw:
            raise H3EvaluationError(
                "10Eros Beta3 scaffold manifests do not claim a conditioning contract"
            )
        return {"mode": mode, "encode_seed": CONDITIONING_ENCODE_SEED}
    if model_type == MINIMAX_H3_FL2VA_ID:
        allowed = {"first_frame", "last_frame"}
        unknown = set(raw) - allowed
        if unknown:
            raise H3EvaluationError(
                "FL2VA accepts only first_frame/last_frame conditioning; "
                f"unexpected fields: {sorted(unknown)}"
            )
        return {
            "mode": mode,
            "first_frame": (
                _relative_path(raw["first_frame"], "conditioning.first_frame")
                if raw.get("first_frame") is not None else None
            ),
            "last_frame": (
                _relative_path(raw["last_frame"], "conditioning.last_frame")
                if raw.get("last_frame") is not None else None
            ),
            "encode_seed": CONDITIONING_ENCODE_SEED,
        }

    allowed = {"images", "videos", "audio", "use_video_soundtracks"}
    unknown = set(raw) - allowed
    if unknown:
        raise H3EvaluationError(
            "Ref2VA accepts only semantic reference conditioning; "
            f"unexpected fields: {sorted(unknown)}"
        )
    images = _path_list(raw.get("images"), "conditioning.images")
    videos = _timed_reference_list(raw.get("videos"), "conditioning.videos")
    audio = _timed_reference_list(raw.get("audio"), "conditioning.audio")
    soundtracks = _require_bool(
        raw.get("use_video_soundtracks", False),
        "conditioning.use_video_soundtracks",
    )
    limits = _MODE_LIMITS[model_type]["reference_limits"]
    if len(images) > limits["image_count"]:
        raise H3EvaluationError("Ref2VA accepts at most 9 reference images")
    if len(videos) > limits["video_count"]:
        raise H3EvaluationError("Ref2VA accepts at most 3 reference videos")
    if len(audio) > limits["audio_count"]:
        raise H3EvaluationError("Ref2VA accepts at most 3 reference audio clips")
    if soundtracks and audio:
        raise H3EvaluationError(
            "use_video_soundtracks and separate audio references are mutually exclusive"
        )
    audio_count = len(videos) if soundtracks else len(audio)
    visual_count = len(images) + len(videos)
    if audio_count > visual_count:
        raise H3EvaluationError(
            "Ref2VA requires at least as many visual references as audio references"
        )
    mixed_count = visual_count + (0 if soundtracks else audio_count)
    if mixed_count > limits["mixed_file_count"]:
        raise H3EvaluationError("Ref2VA accepts at most 12 mixed reference files")
    for kind, items in (("video", videos), ("audio", audio)):
        duration_limits = limits[f"reference_{kind}_duration_seconds"]
        for item in items:
            duration = item["duration_seconds"]
            if not duration_limits["minimum"] <= duration <= duration_limits["maximum"]:
                raise H3EvaluationError(
                    f"Each Ref2VA {kind} reference must be 2-15 seconds"
                )
        if sum(item["duration_seconds"] for item in items) > duration_limits["total_maximum"]:
            raise H3EvaluationError(
                f"Ref2VA {kind} references may total at most 15 seconds"
            )
    return {
        "mode": mode,
        "images": images,
        "videos": videos,
        "audio": audio,
        "use_video_soundtracks": soundtracks,
        "limits": copy.deepcopy(limits),
        "encode_seed": CONDITIONING_ENCODE_SEED,
    }


def build_h3_evaluation_manifest(
    *,
    project_id: str,
    job_id: str,
    model_type: str,
    resolved_seed: int,
    prompt: str,
    frame_count: int = 124,
    resolution: str = "864x480",
    fps: int = NATIVE_FPS,
    sampling_steps: int = 20,
    conditioning: Mapping[str, Any] | None = None,
    output_artifacts: Sequence[str] = (),
    explicit: bool = False,
    private: bool | None = None,
    profile_id: str | None = None,
    encoder_profile_id: str | None = None,
    allow_experimental: bool = False,
    sampler_candidate: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic and JSON-serializable H3 evaluation manifest."""
    project_id = _validate_lineage_id(project_id, "project_id")
    job_id = _validate_lineage_id(job_id, "job_id")
    if model_type not in _MODE_LIMITS:
        raise H3EvaluationError(
            f"model_type must preserve an official H3 ID: {sorted(_MODE_LIMITS)}"
        )
    if (
        isinstance(resolved_seed, bool)
        or not isinstance(resolved_seed, int)
        or not 0 <= resolved_seed <= MAX_SEED
    ):
        raise H3EvaluationError(
            "resolved_seed must be an explicit integer from 0 through 4294967295; -1/unresolved is invalid"
        )
    if not isinstance(prompt, str) or not prompt.strip():
        raise H3EvaluationError("prompt must be a non-empty string")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps != NATIVE_FPS:
        raise H3EvaluationError(f"MiniMax H3 evaluation requires its native {NATIVE_FPS} fps")
    if isinstance(frame_count, bool) or not isinstance(frame_count, int):
        raise H3EvaluationError("frame_count must be an integer on the H3 frame grid")
    limits = _MODE_LIMITS[model_type]
    if not limits["minimum_frames"] <= frame_count <= limits["maximum_frames"]:
        raise H3EvaluationError(
            f"{model_type} frame_count must be {limits['minimum_frames']}-{limits['maximum_frames']}"
        )
    if frame_count % FRAME_GRID_MODULUS != FRAME_GRID_REMAINDER:
        raise H3EvaluationError("frame_count must satisfy the native 17*n+5 H3 frame grid")
    if resolution not in LEGAL_CANVASES:
        raise H3EvaluationError(f"resolution must be one of the legal H3 canvases: {LEGAL_CANVASES}")
    if isinstance(sampling_steps, bool) or not isinstance(sampling_steps, int) or sampling_steps < 2:
        raise H3EvaluationError("sampling_steps must provide at least two scheduler grid points")
    explicit = _require_bool(explicit, "explicit")
    allow_experimental = _require_bool(allow_experimental, "allow_experimental")
    if private is None:
        private = explicit
    private = _require_bool(private, "private")

    selected_profile_id = profile_id or model_type
    profile = _PROFILE_CATALOG.get(selected_profile_id)
    if profile is None or profile.get("component_role") not in {"video_model", "video_model_bundle"}:
        raise H3EvaluationError("profile_id must select an H3 video-model profile")
    if profile.get("model_type") != model_type:
        raise H3EvaluationError("profile_id is incompatible with model_type")
    if profile.get("experimental") and not allow_experimental:
        raise H3EvaluationError("experimental profiles require allow_experimental=True")
    scaffold_only = bool(profile.get("scaffold_only", False))
    if scaffold_only:
        if sampling_steps != 6:
            raise H3EvaluationError(
                "10Eros Beta3 scaffold manifests require exactly six sampling steps"
            )
        sampler_candidates = profile["maestro_experiment_policy"]["schedule"][
            "sampler_candidates"
        ]
        if sampler_candidate not in sampler_candidates:
            raise H3EvaluationError(
                "10Eros Beta3 sampler_candidate must be er_sde/simple or multires/simple"
            )
    elif sampler_candidate is not None:
        raise H3EvaluationError(
            "sampler_candidate is reserved for scaffold-only experimental profiles"
        )

    encoder_profile = None
    if encoder_profile_id is not None:
        encoder_profile = _PROFILE_CATALOG.get(encoder_profile_id)
        if encoder_profile is None or encoder_profile.get("component_role") != "conditioning_encoder_only":
            raise H3EvaluationError("encoder_profile_id must select an encoder-only option")
        if encoder_profile.get("experimental") and not allow_experimental:
            raise H3EvaluationError("experimental encoder profiles require allow_experimental=True")

    width, height = (int(part) for part in resolution.split("x"))
    request = {
        "prompt": prompt,
        "resolved_seed": resolved_seed,
        "conditioning_encode_seed": CONDITIONING_ENCODE_SEED,
        "sampling_steps": sampling_steps,
    }
    if scaffold_only:
        request["sampler_candidate"] = sampler_candidate
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "minimax_h3_evaluation_manifest",
        "catalog_pinned_as_of": CATALOG_PINNED_AS_OF,
        "lineage": {"project_id": project_id, "job_id": job_id},
        "profile": {
            "id": selected_profile_id,
            "model_type": model_type,
            "experimental": bool(profile["experimental"]),
            "scaffold_only": bool(profile.get("scaffold_only", False)),
            "execution_available": bool(profile.get("execution_available", True)),
            "revision_provenance": copy.deepcopy(profile),
            "encoder_option": copy.deepcopy(encoder_profile),
        },
        "request": request,
        "geometry": {
            "fps": NATIVE_FPS,
            "frame_count": frame_count,
            "duration_seconds": frame_count / NATIVE_FPS,
            "resolution": resolution,
            "width": width,
            "height": height,
            "frame_grid": {
                "modulus": FRAME_GRID_MODULUS,
                "remainder": FRAME_GRID_REMAINDER,
            },
            "mode_limits": copy.deepcopy(limits),
            "legal_canvases": list(LEGAL_CANVASES),
        },
        "conditioning": _normalize_conditioning(model_type, conditioning),
        "scheduler": {
            "kind": "rectified_flow_euler_exponential_sigma_shift",
            "video_shift": VIDEO_SCHEDULER_SHIFT,
            "audio_shift": AUDIO_SCHEDULER_SHIFT,
        },
        "artifact_policy": {
            "private": private,
            "explicit": explicit,
            "outputs": _path_list(output_artifacts, "output_artifacts"),
        },
    }
    manifest["manifest_id"] = _fingerprint("h3m", manifest)
    return manifest


def validate_h3_evaluation_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate a manifest by reproducing its canonical deterministic form."""
    if not isinstance(manifest, Mapping):
        raise H3EvaluationError("manifest must be an object")
    try:
        profile = manifest["profile"]
        request = manifest["request"]
        geometry = manifest["geometry"]
        policy = manifest["artifact_policy"]
        lineage = manifest["lineage"]
        conditioning = dict(manifest["conditioning"])
        conditioning.pop("mode", None)
        conditioning.pop("limits", None)
        conditioning.pop("encode_seed", None)
        encoder = profile.get("encoder_option")
        rebuilt = build_h3_evaluation_manifest(
            project_id=lineage["project_id"],
            job_id=lineage["job_id"],
            model_type=profile["model_type"],
            resolved_seed=request["resolved_seed"],
            prompt=request["prompt"],
            frame_count=geometry["frame_count"],
            resolution=geometry["resolution"],
            fps=geometry["fps"],
            sampling_steps=request["sampling_steps"],
            conditioning=conditioning,
            output_artifacts=policy["outputs"],
            explicit=policy["explicit"],
            private=policy["private"],
            profile_id=profile["id"],
            encoder_profile_id=encoder["id"] if encoder else None,
            allow_experimental=bool(profile["experimental"] or encoder),
            sampler_candidate=request.get("sampler_candidate"),
        )
    except (KeyError, TypeError) as error:
        raise H3EvaluationError("manifest is missing required canonical fields") from error
    if dict(manifest) != rebuilt:
        raise H3EvaluationError("manifest is not the canonical deterministic H3 manifest")


def _metric_value(name: str, value: Any) -> Any:
    expected = _METRIC_TYPES[name]
    if expected == "sha256":
        if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
            raise H3EvaluationError(f"observed_metrics.{name} must be a lowercase SHA-256")
        return value
    if isinstance(value, bool):
        raise H3EvaluationError(f"observed_metrics.{name} must be numeric")
    if expected.endswith("integer"):
        if not isinstance(value, int):
            raise H3EvaluationError(f"observed_metrics.{name} must be an integer")
    elif not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise H3EvaluationError(f"observed_metrics.{name} must be a finite number")
    if expected.startswith("positive") and value <= 0:
        raise H3EvaluationError(f"observed_metrics.{name} must be positive")
    if expected.startswith("nonnegative") and value < 0:
        raise H3EvaluationError(f"observed_metrics.{name} must be nonnegative")
    return value


def _unavailable_metrics(reason: str) -> dict[str, dict[str, Any]]:
    return {
        name: {"status": "unavailable", "value": None, "reason": reason}
        for name in _METRIC_TYPES
    }


def build_h3_evaluation_report(
    manifest: dict[str, Any],
    executor: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return an offline report, optionally using one executor observation.

    The exact manifest object is passed to ``executor`` and then checked for
    mutation.  Executor results may report only the factual observed metric
    allow-list; absent measurements remain unavailable rather than becoming
    estimates or zero-valued placeholders.
    """
    validate_h3_evaluation_manifest(manifest)
    if manifest["profile"].get("scaffold_only") and executor is not None:
        raise H3EvaluationError(
            "10Eros Beta3 scaffold manifests cannot be passed to an executor"
        )
    metrics = _unavailable_metrics(
        "executor_not_provided" if executor is None else "not_reported_by_executor"
    )
    artifacts: list[str] = []
    if executor is None:
        execution = {"status": "skipped", "reason": "executor_not_provided"}
    else:
        before = _canonical_bytes(manifest)
        try:
            result = executor(manifest)
        except Exception as error:  # executor failures are factual report state
            if _canonical_bytes(manifest) != before:
                raise H3EvaluationError("executor mutated the evaluation manifest") from error
            execution = {
                "status": "failed",
                "reason": "executor_raised",
                "error_type": type(error).__name__,
            }
        else:
            if _canonical_bytes(manifest) != before:
                raise H3EvaluationError("executor mutated the evaluation manifest")
            if not isinstance(result, Mapping):
                raise H3EvaluationError("executor must return an object")
            allowed_result_fields = {"status", "reason", "observed_metrics", "artifacts"}
            unknown = set(result) - allowed_result_fields
            if unknown:
                raise H3EvaluationError(f"executor returned unsupported fields: {sorted(unknown)}")
            status = result.get("status", "completed")
            if status not in {"completed", "failed", "skipped"}:
                raise H3EvaluationError("executor status must be completed, failed, or skipped")
            reason = result.get("reason")
            if reason is not None and (not isinstance(reason, str) or not reason):
                raise H3EvaluationError("executor reason must be a non-empty string when present")
            execution = {"status": status}
            if reason is not None:
                execution["reason"] = reason
            observed = result.get("observed_metrics", {})
            if not isinstance(observed, Mapping):
                raise H3EvaluationError("observed_metrics must be an object")
            unknown_metrics = set(observed) - set(_METRIC_TYPES)
            if unknown_metrics:
                raise H3EvaluationError(
                    f"unsupported observed metrics: {sorted(unknown_metrics)}"
                )
            for name, value in observed.items():
                metrics[name] = {
                    "status": "available",
                    "value": _metric_value(name, value),
                    "source": "executor_observation",
                }
            artifacts = _path_list(result.get("artifacts"), "executor.artifacts")

    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": "minimax_h3_evaluation_report",
        "manifest_id": manifest["manifest_id"],
        "lineage": copy.deepcopy(manifest["lineage"]),
        "execution": execution,
        "configuration_facts": {
            "model_type": manifest["profile"]["model_type"],
            "resolved_seed": manifest["request"]["resolved_seed"],
            "conditioning_encode_seed": CONDITIONING_ENCODE_SEED,
            "fps": NATIVE_FPS,
            "frame_count": manifest["geometry"]["frame_count"],
            "duration_seconds": manifest["geometry"]["duration_seconds"],
            "video_scheduler_shift": VIDEO_SCHEDULER_SHIFT,
            "audio_scheduler_shift": AUDIO_SCHEDULER_SHIFT,
        },
        "metrics": metrics,
        "artifacts": artifacts,
        "artifact_policy": copy.deepcopy(manifest["artifact_policy"]),
        "ranking": {"status": "not_performed"},
    }
    if manifest["profile"].get("scaffold_only"):
        report["configuration_facts"]["sampler_candidate"] = manifest[
            "request"
        ]["sampler_candidate"]
    report["report_id"] = _fingerprint("h3r", report)
    return report


# Concise aliases for callers that already know they are in the H3 service.
build_evaluation_manifest = build_h3_evaluation_manifest
build_evaluation_report = build_h3_evaluation_report
evaluate_h3_manifest = build_h3_evaluation_report


__all__ = [
    "AUDIO_SCHEDULER_SHIFT",
    "CATALOG_PINNED_AS_OF",
    "CONDITIONING_ENCODE_SEED",
    "EXPERIMENTAL_CATALOG_UPDATED_AS_OF",
    "H3EvaluationError",
    "LEGAL_CANVASES",
    "MINIMAX_H3_10EROS_BETA3_FULL_ID",
    "MINIMAX_H3_10EROS_BETA3_SKIP_ID",
    "MINIMAX_H3_FL2VA_ID",
    "MINIMAX_H3_REF2VA_ID",
    "NATIVE_FPS",
    "VIDEO_SCHEDULER_SHIFT",
    "build_evaluation_manifest",
    "build_evaluation_report",
    "build_h3_evaluation_manifest",
    "build_h3_evaluation_report",
    "evaluate_h3_manifest",
    "get_h3_profile_catalog",
    "validate_h3_evaluation_manifest",
]
