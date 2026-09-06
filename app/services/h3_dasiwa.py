"""CPU-only artifact identities for experimental MiniMax H3 Ref2VA LoRAs.

This module deliberately does not load tensor payloads. It validates immutable
bytes and the bounded safetensors header, then enforces the selected runtime
settings so callers can expose or run an experiment only when its exact
artifact/base contract is present. The Dasiwa owner override means a sparse
model card is not a gate; the pinned checkpoint identity remains mandatory.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from services.h3_checkpoint_receipts import (
    H3CheckpointIntegrityError,
    inspect_checkpoint_receipt,
    recheck_checkpoint_binding,
    verify_checkpoint_integrity,
)


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
# Exact stat contract of the installed Comfy scaled-FP8 artifact. The author's
# exact 71c6... base has no known filename/size/source and is not fabricated.
DASIWA_SUSPECTED_BASE_SIZE = 20_958_205_608
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


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev), int(value.st_ino), int(value.st_size),
        int(value.st_mtime_ns), int(value.st_ctime_ns),
        int(getattr(value, "st_uid", -1)),
    )


def _same_owner(value: os.stat_result) -> bool:
    return os.name != "posix" or int(value.st_uid) == os.geteuid()


def _open_owned_regular(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if os.name == "posix":
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise H3ExperimentCompatibilityError(
                "This host cannot safely verify H3 LoRA links"
            )
        flags |= nofollow
    try:
        entry_stat = path.lstat()
        if stat.S_ISLNK(entry_stat.st_mode):
            raise H3ExperimentCompatibilityError(
                f"{path.name} cannot be a symbolic link"
            )
        descriptor = os.open(path, flags)
    except H3ExperimentCompatibilityError:
        raise
    except OSError as error:
        raise H3ExperimentCompatibilityError(
            f"{path.name} is unavailable"
        ) from error
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or not _same_owner(opened_stat)
            or _stat_identity(entry_stat) != _stat_identity(opened_stat)
        ):
            raise H3ExperimentCompatibilityError(
                f"{path.name} is not a stable owner file"
            )
        return descriptor, opened_stat
    except BaseException:
        os.close(descriptor)
        raise


def _read_header_descriptor(
    descriptor: int, path: Path, size: int,
) -> tuple[dict[str, Any], int]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    raw_length = os.read(descriptor, 8)
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
    raw_header = bytearray()
    remaining = header_length
    while remaining:
        chunk = os.read(descriptor, min(remaining, 1024 * 1024))
        if not chunk:
            break
        raw_header.extend(chunk)
        remaining -= len(chunk)
    if remaining:
        raise H3ExperimentCompatibilityError(
            f"{path.name} has a truncated safetensors header"
        )
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
    return header, 8 + header_length


def _verified_artifact(
    path: Path, *, expected_size: int, expected_sha256: str,
) -> tuple[dict[str, Any], int, int]:
    descriptor, before = _open_owned_regular(path)
    try:
        if before.st_size != expected_size:
            raise H3ExperimentCompatibilityError(
                f"{path.name} must be exactly {expected_size} bytes"
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise H3ExperimentCompatibilityError(
                f"{path.name} SHA256 does not match the pinned release"
            )
        header, data_start = _read_header_descriptor(
            descriptor, path, expected_size,
        )
        after = os.fstat(descriptor)
        try:
            final_entry = path.lstat()
        except OSError as error:
            raise H3ExperimentCompatibilityError(
                f"{path.name} changed during validation"
            ) from error
        if (
            _stat_identity(before) != _stat_identity(after)
            or not stat.S_ISREG(final_entry.st_mode)
            or not _same_owner(final_entry)
            or _stat_identity(final_entry) != _stat_identity(after)
        ):
            raise H3ExperimentCompatibilityError(
                f"{path.name} changed during validation"
            )
        return header, expected_size, data_start
    finally:
        os.close(descriptor)


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
    header, size, data_start = _verified_artifact(
        source, expected_size=DASIWA_SIZE, expected_sha256=DASIWA_SHA256,
    )
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
    header, size, data_start = _verified_artifact(
        source,
        expected_size=BETTER_MOTION_SIZE,
        expected_sha256=BETTER_MOTION_SHA256,
    )
    tensor_count = _validate_header_extents(source, header, data_start, size)
    return better_motion_identity() | {
        "header_validated": True,
        "tensor_count": tensor_count,
    }


@lru_cache(maxsize=16)
def _cached_validation(
    artifact_id: str,
    path_text: str,
    dev: int,
    ino: int,
    size: int,
    mtime_ns: int,
    ctime_ns: int,
    uid: int,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Cache an immutable-byte validation for status polling only.

    The complete owner/stat identity is part of the key so replacements are
    revalidated. Runtime admission remains responsible for binding the selected
    artifact/checkpoint identity; this cache merely avoids hashing a gigabyte
    of TVBox data on every profile-estimate request.
    """

    del dev, ino, size, mtime_ns, ctime_ns, uid
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
    selected_checkpoint_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a content-free, fail-closed availability record."""
    lora_root = Path(root) if root is not None else _DEFAULT_ROOT
    if artifact_id == DASIWA_ARTIFACT_ID:
        identity = dasiwa_identity()
    elif artifact_id == BETTER_MOTION_ARTIFACT_ID:
        identity = better_motion_identity()
    else:
        raise KeyError(f"Unknown H3 experiment artifact: {artifact_id}")
    path = lora_root / identity["filename"]
    try:
        path_stat = path.lstat()
        safe_regular = (
            stat.S_ISREG(path_stat.st_mode) and _same_owner(path_stat)
        )
    except OSError:
        path_stat = None
        safe_regular = False
    status = {
        "registered": True,
        "downloaded": safe_regular,
        "available": False,
        "download_required": path_stat is None,
        "reason": None,
        "filename": identity["filename"],
        "identity": identity,
    }
    if selected_model_type != "minimax_h3_ref2va":
        status["reason"] = "This experiment supports only MiniMax H3 Ref2VA."
        return status
    if path_stat is not None and not safe_regular:
        status["reason"] = "The selected experiment artifact is not a regular owner file."
        return status
    if path_stat is None:
        status["reason"] = (
            "Better Motion Civitai version 3257589 must be downloaded explicitly."
            if artifact_id == BETTER_MOTION_ARTIFACT_ID
            else "The pinned Dasiwa artifact is not downloaded."
        )
        return status
    if artifact_id == DASIWA_ARTIFACT_ID:
        checkpoint = dict(selected_checkpoint_status or {})
        supplied = str(checkpoint.get("sha256") or "").strip().casefold()
        compatibility = str(checkpoint.get("compatibility") or "")
        allowed_base = checkpoint.get("verified") is True and (
            (
                supplied == DASIWA_COMPATIBLE_BASE_SHA256
                and compatibility == "exact_base"
            )
            or (
                supplied == DASIWA_SUSPECTED_BASE_SHA256
                and compatibility == "suspected_compatible_base"
            )
        )
        if not allowed_base:
            status["reason"] = (
                "Dasiwa requires either its exact compatible Ref2VA checkpoint "
                "or the one explicitly pinned suspected-compatible Ref2VA probe."
            )
            return status
    try:
        artifact_stat = path.lstat()
        valid, validated_identity, validation_error = _cached_validation(
            artifact_id,
            str(path.absolute()),
            artifact_stat.st_dev,
            artifact_stat.st_ino,
            artifact_stat.st_size,
            artifact_stat.st_mtime_ns,
            artifact_stat.st_ctime_ns,
            int(getattr(artifact_stat, "st_uid", -1)),
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
            if supplied == DASIWA_COMPATIBLE_BASE_SHA256
            else "suspected_compatible_base"
        )
    return status


def dasiwa_checkpoint_status(
    selected_checkpoint_path: str | os.PathLike[str] | None,
    *,
    receipt_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Return path-free status for the actual resolved Ref2VA transformer."""
    unavailable = {
        "verified": False,
        "available": False,
        "sha256": None,
        "compatibility": "unavailable",
        "receipt_reused": False,
        "reason": "The selected Ref2VA transformer has no verified Dasiwa contract.",
    }
    if selected_checkpoint_path is None:
        return unavailable
    source = Path(selected_checkpoint_path)
    if source.name != DASIWA_SUSPECTED_BASE_FILENAME:
        return unavailable
    try:
        verified = inspect_checkpoint_receipt(
            source,
            expected_sha256=DASIWA_SUSPECTED_BASE_SHA256,
            expected_size=DASIWA_SUSPECTED_BASE_SIZE,
            compatibility="suspected_compatible_base",
            receipt_root=receipt_root,
        )
    except (OSError, H3CheckpointIntegrityError) as error:
        return {**unavailable, "reason": str(error)}
    if verified is None:
        try:
            candidate_stat = source.lstat()
            candidate = (
                stat.S_ISREG(candidate_stat.st_mode)
                and _same_owner(candidate_stat)
                and candidate_stat.st_size == DASIWA_SUSPECTED_BASE_SIZE
            )
        except OSError:
            candidate = False
        if candidate:
            return {
                **unavailable,
                "candidate": True,
                "preparation_required": True,
                "compatibility": "suspected_compatible_base",
                "reason": (
                    "The installed Ref2VA candidate will be verified once at runtime."
                ),
            }
        return unavailable
    return {**verified, "available": True, "reason": None}


def dasiwa_lora_candidate_status(
    *, root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Return cheap path-free candidate status without reading LoRA contents."""
    path = (Path(root) if root is not None else _DEFAULT_ROOT) / DASIWA_FILENAME
    try:
        value = path.lstat()
        candidate = (
            stat.S_ISREG(value.st_mode)
            and _same_owner(value)
            and value.st_size == DASIWA_SIZE
        )
    except OSError:
        candidate = False
    return {
        "registered": True,
        "downloaded": candidate,
        "available": False,
        "candidate": candidate,
        "preparation_required": candidate,
        "download_required": not candidate,
        "filename": DASIWA_FILENAME,
        "reason": (
            "The installed Dasiwa candidate will be verified once at runtime."
            if candidate else "The pinned Dasiwa artifact is not downloaded."
        ),
    }


def recheck_dasiwa_checkpoint_admission(
    admission: Mapping[str, Any] | None,
    selected_checkpoint_path: str | os.PathLike[str] | None,
    *,
    receipt_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Rebind an earlier path-free admission to the loader's resolved path."""
    expected = dict(admission or {})
    del receipt_root
    binding = expected.get("_checkpoint_binding")
    if (
        expected.get("verified") is not True
        or not recheck_checkpoint_binding(selected_checkpoint_path, binding)
    ):
        raise H3ExperimentCompatibilityError(
            "The Dasiwa checkpoint identity changed before model loading"
        )
    return expected


def _dasiwa_multiplier(value: Any) -> float | None:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = [value]
    if len(parts) != 1:
        return None
    try:
        return float(parts[0])
    except (TypeError, ValueError):
        return None


def _dasiwa_lora_binding(path: Path) -> dict[str, Any]:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or not _same_owner(value):
        raise H3ExperimentCompatibilityError(
            "The selected Dasiwa artifact is not a regular owner file"
        )
    return {
        "contract_revision": "minimax-h3-dasiwa-lora-v1",
        "family": "minimax_h3",
        "role": "lora",
        "expected_sha256": DASIWA_SHA256,
        "expected_size": DASIWA_SIZE,
        "path_digest": hashlib.sha256(
            os.fsencode(os.path.normcase(os.path.realpath(path)))
        ).hexdigest(),
        "identity": list(_stat_identity(value)),
    }


def recheck_dasiwa_lora_admission(
    admission: Mapping[str, Any] | None,
    selected_lora_path: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    """Recheck the LoRA path/stat identity without status-cache authority."""
    expected = dict(admission or {})
    binding = expected.get("_lora_binding")
    if selected_lora_path is None or not isinstance(binding, dict):
        raise H3ExperimentCompatibilityError(
            "The Dasiwa LoRA identity changed before model consumption"
        )
    try:
        current = _dasiwa_lora_binding(Path(selected_lora_path))
    except (OSError, H3ExperimentCompatibilityError) as error:
        raise H3ExperimentCompatibilityError(
            "The Dasiwa LoRA identity changed before model consumption"
        ) from error
    if current != binding:
        raise H3ExperimentCompatibilityError(
            "The Dasiwa LoRA identity changed before model consumption"
        )
    return expected


def admitted_dasiwa_lora_path(
    admission: Mapping[str, Any] | None,
    requested_lora: str | os.PathLike[str],
    admitted_path: str | os.PathLike[str] | None,
) -> str:
    """Return only the already-admitted path for the Dasiwa filename."""
    if Path(str(requested_lora)).name != DASIWA_FILENAME or admitted_path is None:
        raise H3ExperimentCompatibilityError(
            "The admitted Dasiwa LoRA selection changed before loading"
        )
    recheck_dasiwa_lora_admission(admission, admitted_path)
    return os.fspath(admitted_path)


def validate_dasiwa_request(
    *,
    model_types: Any,
    activated_loras: Any,
    loras_multipliers: Any,
    num_inference_steps: Any,
    custom_settings: Mapping[str, Any] | None,
    skip_steps_cache_type: Any = "",
) -> bool:
    """Return True when Dasiwa is selected. Raise if that selection is illegal."""
    if isinstance(activated_loras, str):
        loras = [activated_loras]
    elif isinstance(activated_loras, (list, tuple)):
        loras = list(activated_loras)
    else:
        loras = []
    selected = [item for item in loras if Path(str(item)).name == DASIWA_FILENAME]
    if not selected:
        return False
    if len(selected) != 1 or len(loras) != 1:
        raise H3ExperimentCompatibilityError(
            "Dasiwa cannot be stacked with another LoRA or accelerator"
        )
    if isinstance(model_types, str):
        types = [model_types]
    elif isinstance(model_types, (list, tuple)):
        types = [str(item or "") for item in model_types] or [""]
    else:
        types = [""]
    if any(item != "minimax_h3_ref2va" for item in types):
        raise H3ExperimentCompatibilityError(
            "Dasiwa requires MiniMax H3 Ref2VA"
            + (" for every planned shot" if len(types) > 1 else "")
        )
    if type(num_inference_steps) is not int or num_inference_steps != DASIWA_AUTHORED_STEPS:
        raise H3ExperimentCompatibilityError("Dasiwa requires exactly four sampling steps")
    if _dasiwa_multiplier(loras_multipliers) != DASIWA_STRENGTH:
        raise H3ExperimentCompatibilityError("Dasiwa requires LoRA strength 1.0")
    if str(skip_steps_cache_type or ""):
        raise H3ExperimentCompatibilityError("Dasiwa cannot use a step cache")
    settings = dict(custom_settings or {})
    if any(
        str(key).startswith("h3_")
        and str(key).endswith("_profile")
        and value not in (None, "", False, 0)
        for key, value in settings.items()
    ):
        raise H3ExperimentCompatibilityError(
            "Dasiwa cannot be stacked with another accelerator"
        )
    return True


def enforce_dasiwa_runtime(
    *,
    model_type: str,
    activated_loras: Any,
    loras_multipliers: Any,
    num_inference_steps: Any,
    custom_settings: Mapping[str, Any] | None,
    skip_steps_cache_type: Any,
    selected_checkpoint_path: str | os.PathLike[str] | None,
    selected_lora_path: str | os.PathLike[str] | None,
    receipt_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Fail closed when the Dasiwa filename activates outside its contract."""
    if not validate_dasiwa_request(
        model_types=[str(model_type or "")],
        activated_loras=activated_loras,
        loras_multipliers=loras_multipliers,
        num_inference_steps=num_inference_steps,
        custom_settings=custom_settings,
        skip_steps_cache_type=skip_steps_cache_type,
    ):
        return None
    if (
        selected_checkpoint_path is None
        or Path(selected_checkpoint_path).name != DASIWA_SUSPECTED_BASE_FILENAME
    ):
        raise H3ExperimentCompatibilityError(
            "The selected Ref2VA transformer has no verified Dasiwa contract."
        )
    try:
        checkpoint = verify_checkpoint_integrity(
            selected_checkpoint_path,
            expected_sha256=DASIWA_SUSPECTED_BASE_SHA256,
            expected_size=DASIWA_SUSPECTED_BASE_SIZE,
            compatibility="suspected_compatible_base",
            receipt_root=receipt_root,
            include_private_binding=True,
        )
    except H3CheckpointIntegrityError as error:
        raise H3ExperimentCompatibilityError(str(error)) from error
    if selected_lora_path is None:
        raise H3ExperimentCompatibilityError("The pinned Dasiwa artifact is unavailable")
    lora_path = Path(selected_lora_path)
    try:
        lora_stat = lora_path.lstat()
        if not stat.S_ISREG(lora_stat.st_mode) or not _same_owner(lora_stat):
            raise H3ExperimentCompatibilityError(
                "The selected Dasiwa artifact is not a regular owner file"
            )
        valid, _identity_record, validation_error = _cached_validation(
            DASIWA_ARTIFACT_ID,
            str(lora_path.absolute()),
            lora_stat.st_dev,
            lora_stat.st_ino,
            lora_stat.st_size,
            lora_stat.st_mtime_ns,
            lora_stat.st_ctime_ns,
            int(getattr(lora_stat, "st_uid", -1)),
        )
    except (OSError, H3ExperimentCompatibilityError) as error:
        raise H3ExperimentCompatibilityError(str(error)) from error
    if not valid:
        raise H3ExperimentCompatibilityError(
            validation_error or "Dasiwa artifact verification failed"
        )
    lora_binding = _dasiwa_lora_binding(lora_path)
    if lora_binding.get("identity") != list(_stat_identity(lora_stat)):
        raise H3ExperimentCompatibilityError(
            "The Dasiwa LoRA identity changed after integrity validation"
        )
    checkpoint["_lora_binding"] = lora_binding
    return checkpoint


__all__ = [name for name in globals() if name.startswith(("DASIWA_", "BETTER_MOTION_"))] + [
    "H3ExperimentCompatibilityError", "INCOMPATIBLE_ACCELERATORS",
    "LORA_INSERTION_MODE", "better_motion_identity", "dasiwa_identity",
    "admitted_dasiwa_lora_path", "dasiwa_checkpoint_status",
    "dasiwa_lora_candidate_status", "enforce_dasiwa_runtime", "experiment_status",
    "recheck_dasiwa_lora_admission",
    "recheck_dasiwa_checkpoint_admission",
    "validate_better_motion_lora", "validate_dasiwa_lora",
]
