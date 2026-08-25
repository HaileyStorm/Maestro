"""CPU-only artifact identities for experimental MiniMax H3 Ref2VA LoRAs.

This module deliberately does not load tensor payloads or select a runtime
profile.  It validates immutable bytes and the bounded safetensors header so
callers can expose an experiment only when its exact artifact/base contract is
present.  The Dasiwa owner override means a sparse model card is not a gate;
the pinned checkpoint identity remains mandatory.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


DASIWA_ARTIFACT_ID = "dasiwa_ref2va_hybrid_v1_4step"
DASIWA_PROFILE_ID = "h3_dasiwa_ref2va_hybrid_v1_4step"
DASIWA_REPOSITORY = "t8star/Minimax-H3-Dasiwa-V1-Hybird-4steps"
DASIWA_REVISION = "da516a7394d11bc5264375697848ca8fe52ba406"
DASIWA_REMOTE_FILENAME = (
    "minimax_h3_turbo_4步加速_"
    "DasiwaREF2VAHybridV1_curveproj1025_compat_v001-T8.safetensors"
)
DASIWA_FILENAME = "dasiwa_ref2va_hybrid_v1_4step.safetensors"
DASIWA_SIZE = 794_888_664
DASIWA_SHA256 = "d2a9a723d97520232f17b6fec33335f9e94b03b2c67b56f91f16780355479274"
DASIWA_COMPATIBLE_BASE_SHA256 = (
    "71c61492faf65b410d0726840ac3b27b017fcfeb76b16ae11589223d81b7121c"
)
DASIWA_SUSPECTED_BASE_FILENAME = (
    "minimax_h3_ref2va_pruned_fp8_scaled.safetensors"
)
DASIWA_SUSPECTED_BASE_SHA256 = (
    "f86f2f79ebd2d76eb8eeb46091e83982e6ff51d255747e7b16e92834b392b8e9"
)
DASIWA_AUTHORED_STEPS = 4
DASIWA_STRENGTH = 1.0
DASIWA_SCHEDULER = "dasiwa_ref2va_native_4step_v1"

BETTER_MOTION_ARTIFACT_ID = "h3_better_nsfw_motion_v1"
BETTER_MOTION_PROFILE_ID = "h3_better_motion_ref2va_v1"
BETTER_MOTION_CIVITAI_MODEL_ID = 2_344_781
BETTER_MOTION_CIVITAI_VERSION_ID = 3_257_589
BETTER_MOTION_FILENAME = "h3_Better_NSFW_Motion_V1.safetensors"
BETTER_MOTION_SIZE = 298_261_888
BETTER_MOTION_SHA256 = "15615bf5aef77b974dba6cd109c547fb8a9a5d36a68fd38b3bd3578e59d3545a"
BETTER_MOTION_DEFAULT_STRENGTH = 0.9
BETTER_MOTION_BENCHMARK_STRENGTHS = (0.5, 0.7, 0.9, 1.0)
BETTER_MOTION_SCHEDULER = "minimax_h3_ref2va_native_v1"

INCOMPATIBLE_ACCELERATORS = (
    "turbo", "lightx2v", "spectrum", "sla", "matlow",
)
LORA_INSERTION_MODE = "ordinary_lora_model_only"

_HEADER_LIMIT = 16 * 1024 * 1024
_APP_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ROOT = _APP_ROOT / "loras" / "minimax_h3"


class H3ExperimentCompatibilityError(RuntimeError):
    """An artifact or selected base does not match its immutable contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_header(path: Path) -> tuple[dict[str, Any], int, int]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw_length = handle.read(8)
        if len(raw_length) != 8:
            raise H3ExperimentCompatibilityError(
                f"{path.name} has no safetensors header"
            )
        header_length = struct.unpack("<Q", raw_length)[0]
        if (
            header_length <= 1
            or header_length > _HEADER_LIMIT
            or 8 + header_length > size
        ):
            raise H3ExperimentCompatibilityError(
                f"{path.name} has an invalid safetensors header size"
            )
        raw_header = handle.read(header_length)
    try:
        header = json.loads(raw_header)
    except Exception as error:
        raise H3ExperimentCompatibilityError(
            f"{path.name} has invalid safetensors JSON"
        ) from error
    if not isinstance(header, dict):
        raise H3ExperimentCompatibilityError(
            f"{path.name} has a non-object safetensors header"
        )
    return header, size, 8 + header_length


def _validate_header_extents(
    path: Path, header: Mapping[str, Any], data_start: int, file_size: int,
) -> int:
    tensor_count = 0
    intervals: list[tuple[int, int]] = []
    payload_size = file_size - data_start
    for name, spec in header.items():
        if name == "__metadata__":
            continue
        tensor_count += 1
        if not isinstance(spec, Mapping):
            raise H3ExperimentCompatibilityError(f"{path.name}:{name} is invalid")
        offsets = spec.get("data_offsets")
        if not (
            isinstance(offsets, list)
            and len(offsets) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in offsets)
        ):
            raise H3ExperimentCompatibilityError(
                f"{path.name}:{name} has invalid offsets"
            )
        start, end = offsets
        if start < 0 or end < start or end > payload_size:
            raise H3ExperimentCompatibilityError(
                f"{path.name}:{name} exceeds the safetensors payload"
            )
        intervals.append((start, end))
    if not tensor_count:
        raise H3ExperimentCompatibilityError(f"{path.name} has no tensors")
    previous_end = 0
    for start, end in sorted(intervals):
        if start < previous_end:
            raise H3ExperimentCompatibilityError(
                f"{path.name} has overlapping tensor payloads"
            )
        previous_end = end
    return tensor_count


def dasiwa_identity() -> dict[str, Any]:
    return {
        "artifact_id": DASIWA_ARTIFACT_ID,
        "profile_id": DASIWA_PROFILE_ID,
        "source": "huggingface",
        "repository": DASIWA_REPOSITORY,
        "revision": DASIWA_REVISION,
        "remote_filename": DASIWA_REMOTE_FILENAME,
        "filename": DASIWA_FILENAME,
        "size": DASIWA_SIZE,
        "sha256": DASIWA_SHA256,
        "compatible_model_type": "minimax_h3_ref2va",
        "compatible_base_sha256": DASIWA_COMPATIBLE_BASE_SHA256,
        "authored_steps": DASIWA_AUTHORED_STEPS,
        "strength": DASIWA_STRENGTH,
        "scheduler": DASIWA_SCHEDULER,
        "insertion_mode": LORA_INSERTION_MODE,
        "incompatible_accelerators": list(INCOMPATIBLE_ACCELERATORS),
        "model_card_gate": False,
    }


def better_motion_identity() -> dict[str, Any]:
    return {
        "artifact_id": BETTER_MOTION_ARTIFACT_ID,
        "profile_id": BETTER_MOTION_PROFILE_ID,
        "source": "civitai",
        "model_id": BETTER_MOTION_CIVITAI_MODEL_ID,
        "version_id": BETTER_MOTION_CIVITAI_VERSION_ID,
        "filename": BETTER_MOTION_FILENAME,
        "size": BETTER_MOTION_SIZE,
        "sha256": BETTER_MOTION_SHA256,
        "compatible_model_type": "minimax_h3_ref2va",
        "strength": BETTER_MOTION_DEFAULT_STRENGTH,
        "benchmark_strengths": list(BETTER_MOTION_BENCHMARK_STRENGTHS),
        "scheduler": BETTER_MOTION_SCHEDULER,
        "insertion_mode": LORA_INSERTION_MODE,
        "incompatible_accelerators": list(INCOMPATIBLE_ACCELERATORS),
    }


def validate_dasiwa_lora(path: str | os.PathLike[str]) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file() or source.stat().st_size != DASIWA_SIZE:
        raise H3ExperimentCompatibilityError(
            f"{source.name} must be exactly {DASIWA_SIZE} bytes"
        )
    if _sha256(source) != DASIWA_SHA256:
        raise H3ExperimentCompatibilityError(
            "Dasiwa SHA256 does not match the pinned release"
        )
    header, size, data_start = _read_header(source)
    metadata = header.get("__metadata__")
    if not isinstance(metadata, Mapping):
        raise H3ExperimentCompatibilityError("Dasiwa metadata is unavailable")
    exact = {
        "compatible_main_sha256": DASIWA_COMPATIBLE_BASE_SHA256,
        "compatibility_scope": "exact_checkpoint_sha256_only",
        "sampler_steps": str(DASIWA_AUTHORED_STEPS),
        "tensor_count": "569",
        "validation_status": "static_projection_validated; perceptual_render_pending",
    }
    if any(str(metadata.get(key) or "") != value for key, value in exact.items()):
        raise H3ExperimentCompatibilityError(
            "Dasiwa metadata does not match the pinned Ref2VA release"
        )
    tensor_count = _validate_header_extents(source, header, data_start, size)
    if tensor_count != 569:
        raise H3ExperimentCompatibilityError(
            "Dasiwa tensor count does not match the pinned release"
        )
    return dasiwa_identity() | {
        "header_validated": True,
        "tensor_count": tensor_count,
        "perceptual_render_pending": True,
    }


def validate_better_motion_lora(path: str | os.PathLike[str]) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file() or source.stat().st_size != BETTER_MOTION_SIZE:
        raise H3ExperimentCompatibilityError(
            f"{source.name} must be exactly {BETTER_MOTION_SIZE} bytes"
        )
    if _sha256(source) != BETTER_MOTION_SHA256:
        raise H3ExperimentCompatibilityError(
            "Better Motion SHA256 does not match Civitai version 3257589"
        )
    header, size, data_start = _read_header(source)
    tensor_count = _validate_header_extents(source, header, data_start, size)
    return better_motion_identity() | {
        "header_validated": True,
        "tensor_count": tensor_count,
    }


@lru_cache(maxsize=16)
def _cached_validation(
    artifact_id: str,
    path_text: str,
    size: int,
    mtime_ns: int,
    ctime_ns: int,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Cache an immutable-byte validation for status polling only.

    Size and nanosecond mtime are part of the key so a normal replacement is
    revalidated. Runtime admission remains responsible for binding the selected
    artifact/checkpoint identity; this cache merely avoids hashing a gigabyte
    of TVBox data on every profile-estimate request.
    """

    del size, mtime_ns, ctime_ns
    validator = (
        validate_dasiwa_lora
        if artifact_id == DASIWA_ARTIFACT_ID
        else validate_better_motion_lora
    )
    try:
        return True, validator(path_text), None
    except (OSError, H3ExperimentCompatibilityError) as error:
        return False, None, str(error)


def experiment_status(
    artifact_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
    selected_model_type: str = "minimax_h3_ref2va",
    selected_base_sha256: str | None = None,
    allow_suspected_base: bool = False,
) -> dict[str, Any]:
    """Return a content-free, fail-closed availability record."""
    if type(allow_suspected_base) is not bool:
        raise TypeError("allow_suspected_base must be a boolean")
    lora_root = Path(root) if root is not None else _DEFAULT_ROOT
    if artifact_id == DASIWA_ARTIFACT_ID:
        identity = dasiwa_identity()
        validator = validate_dasiwa_lora
    elif artifact_id == BETTER_MOTION_ARTIFACT_ID:
        identity = better_motion_identity()
        validator = validate_better_motion_lora
    else:
        raise KeyError(f"Unknown H3 experiment artifact: {artifact_id}")
    path = lora_root / identity["filename"]
    status = {
        "registered": True,
        "downloaded": path.is_file(),
        "available": False,
        "download_required": not path.is_file(),
        "reason": None,
        "filename": identity["filename"],
        "identity": identity,
    }
    if selected_model_type != "minimax_h3_ref2va":
        status["reason"] = "This experiment supports only MiniMax H3 Ref2VA."
        return status
    if not path.is_file():
        status["reason"] = (
            "Better Motion Civitai version 3257589 must be downloaded explicitly."
            if artifact_id == BETTER_MOTION_ARTIFACT_ID
            else "The pinned Dasiwa artifact is not downloaded."
        )
        return status
    if artifact_id == DASIWA_ARTIFACT_ID:
        supplied = str(selected_base_sha256 or "").strip().casefold()
        allowed_base = supplied == DASIWA_COMPATIBLE_BASE_SHA256 or (
            allow_suspected_base and supplied == DASIWA_SUSPECTED_BASE_SHA256
        )
        if not allowed_base:
            status["reason"] = (
                "Dasiwa requires either its exact compatible Ref2VA checkpoint "
                "or the one explicitly pinned suspected-compatible Ref2VA probe."
            )
            return status
    try:
        stat = path.stat()
        valid, validated_identity, validation_error = _cached_validation(
            artifact_id,
            str(path.resolve()),
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )
    except (OSError, H3ExperimentCompatibilityError) as error:
        status["reason"] = str(error)
        return status
    if not valid or validated_identity is None:
        status["reason"] = validation_error or "Artifact validation failed."
        return status
    status["validated_identity"] = dict(validated_identity)
    status["available"] = True
    if artifact_id == DASIWA_ARTIFACT_ID:
        status["compatibility"] = (
            "exact_base"
            if str(selected_base_sha256 or "").strip().casefold()
            == DASIWA_COMPATIBLE_BASE_SHA256
            else "suspected_compatible_base"
        )
    return status


__all__ = [name for name in globals() if name.startswith(("DASIWA_", "BETTER_MOTION_"))] + [
    "H3ExperimentCompatibilityError", "INCOMPATIBLE_ACCELERATORS",
    "LORA_INSERTION_MODE", "better_motion_identity", "dasiwa_identity",
    "experiment_status", "validate_better_motion_lora", "validate_dasiwa_lora",
]
