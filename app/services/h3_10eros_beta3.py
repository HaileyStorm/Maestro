"""CPU-only admission scaffolding for 10Eros MiniMax H3 Beta3.

Beta3 is a complete TURBO Hybrid transformer checkpoint, not an FL2VA or
Ref2VA model registration.  This module records its immutable public identity,
performs bounded safetensors-header checks, and reuses Maestro's owner-private
one-time checkpoint receipts.  It deliberately exposes no runtime loader or
automatic selection path.
"""

from __future__ import annotations

import json
import os
import re
import stat
import struct
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from services.h3_checkpoint_receipts import (
    H3CheckpointIntegrityError,
    inspect_checkpoint_receipt,
    verify_checkpoint_integrity,
)

TEN_EROS_REPOSITORY = "cicalooo/10Eros-Max-h3-int8-convrot"
TEN_EROS_REPOSITORY_HEAD = "dbdd87944063bc01d8062bae1dba12212ca4061f"

TEN_EROS_BETA3_SKIP_ID = "10eros_beta3_turbo_hybrid_skip_edges"
TEN_EROS_BETA3_FULL_ID = "10eros_beta3_turbo_hybrid_full"
TEN_EROS_BETA3_SKIP_PROFILE_ID = (
    "minimax_h3_10eros_beta3_turbo_hybrid_skip_edges"
)
TEN_EROS_BETA3_FULL_PROFILE_ID = "minimax_h3_10eros_beta3_turbo_hybrid_full"

_HEADER_LIMIT_BYTES = 16 * 1024 * 1024
_MARKER_LIMIT_BYTES = 4096
_DTYPE_BYTES = MappingProxyType({
    "BF16": 2,
    "F32": 4,
    "I8": 1,
    "U8": 1,
})
_BLOCK_PATTERN = re.compile(r"^blocks\.(\d+)\..+\.comfy_quant$")
_MARKER_POLICY = MappingProxyType({
    "format": "int8_tensorwise",
    "convrot": True,
    "convrot_groupsize": 256,
})
_INCOMPATIBLE_STACKS = (
    "maestro_turbo",
    "spectrum",
    "lightx2v",
    "sage_attention",
    "step_cache",
)
_SAMPLER_CANDIDATES = ("er_sde/simple", "multires/simple")
_APP_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ROOT = _APP_ROOT / "ckpts"


class H310ErosBeta3Error(RuntimeError):
    """A Beta3 artifact does not match its immutable scaffold contract."""


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _descriptor(
    *,
    artifact_id: str,
    profile_id: str,
    filename: str,
    revision: str,
    size: int,
    sha256: str,
    priority: int,
    marker_count: int,
    quantized_blocks: tuple[int, ...],
    bf16_edge_blocks: tuple[int, ...],
) -> Mapping[str, Any]:
    return _freeze({
        "artifact_id": artifact_id,
        "profile_id": profile_id,
        "repository": TEN_EROS_REPOSITORY,
        "repository_head": TEN_EROS_REPOSITORY_HEAD,
        "revision": revision,
        "filename": filename,
        "size": size,
        "sha256": sha256,
        "artifact_role": "transformer",
        "family": "minimax_h3",
        "mode": "turbo_hybrid",
        "compatible_model_types": [],
        "explicitly_not_model_types": ["minimax_h3", "minimax_h3_ref2va"],
        "quantization": {
            "format": "int8_tensorwise",
            "scale_method": "per_channel_absmax",
            "convrot": True,
            "convrot_groupsize": 256,
            "source_dtype": "bfloat16",
        },
        "layer_policy": {
            "marker_count": marker_count,
            "quantized_blocks": list(quantized_blocks),
            "bf16_edge_blocks": list(bf16_edge_blocks),
        },
        "maestro_experiment_policy": {
            "evidence_class": "provisional_maestro_experiment_policy",
            "schedule": {
                "steps": 6,
                "sampler_candidates": list(_SAMPLER_CANDIDATES),
            },
            "incompatible_stacking": list(_INCOMPATIBLE_STACKS),
            "priority": priority,
        },
        "execution_available": False,
        "enabled_by_default": False,
        "automatic_fallback": False,
    })


_ARTIFACT_CATALOG = MappingProxyType({
    TEN_EROS_BETA3_SKIP_ID: _descriptor(
        artifact_id=TEN_EROS_BETA3_SKIP_ID,
        profile_id=TEN_EROS_BETA3_SKIP_PROFILE_ID,
        filename=(
            "10Eros_Max_h3_TURBO-hybrid_beta3_int8_convrot_"
            "skip_edges.safetensors"
        ),
        revision="09beb98782a6feb2f44c39c46179743ca8607c6c",
        size=22_513_576_472,
        sha256="a5ae4559cf19b0830adc1de6e8355d10eaf10524f78e9851a189a80990e6963a",
        priority=1,
        marker_count=184,
        quantized_blocks=tuple(range(2, 48)),
        bf16_edge_blocks=(0, 1, 48, 49),
    ),
    TEN_EROS_BETA3_FULL_ID: _descriptor(
        artifact_id=TEN_EROS_BETA3_FULL_ID,
        profile_id=TEN_EROS_BETA3_FULL_PROFILE_ID,
        filename="10Eros_Max_h3_TURBO-hybrid_beta3_int8_convrot.safetensors",
        revision="84ea7a6ec06e0cb5f2f35615e25e3529c5ec6c02",
        size=20_973_147_816,
        sha256="ebd0cb25273253213028bea0289da4c5c94929027ed9191fbb24fc924d4a8f0d",
        priority=2,
        marker_count=200,
        quantized_blocks=tuple(range(50)),
        bf16_edge_blocks=(),
    ),
})


def _copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_copy(item) for item in value]
    return value


def get_10eros_beta3_catalog() -> dict[str, Any]:
    """Return the two isolated, skip-first Beta3 scaffold descriptors."""
    return {
        "repository": TEN_EROS_REPOSITORY,
        "repository_head": TEN_EROS_REPOSITORY_HEAD,
        "artifacts": [_copy(item) for item in _ARTIFACT_CATALOG.values()],
    }


def _artifact(artifact_id: str) -> Mapping[str, Any]:
    try:
        return _ARTIFACT_CATALOG[artifact_id]
    except KeyError as error:
        raise KeyError(f"Unknown 10Eros Beta3 artifact: {artifact_id}") from error


def _same_owner(value: os.stat_result) -> bool:
    return os.name != "posix" or int(value.st_uid) == os.geteuid()


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
        int(getattr(value, "st_uid", -1)),
    )


def _open_owned_regular(path: Path) -> tuple[int, os.stat_result]:
    try:
        entry = path.lstat()
    except OSError as error:
        raise H310ErosBeta3Error("The selected Beta3 artifact is unavailable") from error
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
        raise H310ErosBeta3Error(
            "The selected Beta3 artifact must be a regular owner file"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if os.name == "posix":
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise H310ErosBeta3Error("This host cannot safely inspect Beta3 links")
        flags |= nofollow
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise H310ErosBeta3Error("The selected Beta3 artifact is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_owner(opened)
            or _identity(entry) != _identity(opened)
        ):
            raise H310ErosBeta3Error(
                "The selected Beta3 artifact must be a stable owner file"
            )
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def _read_exact(descriptor: int, offset: int, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        if hasattr(os, "pread"):
            chunk = os.pread(descriptor, size - len(result), offset + len(result))
        else:  # pragma: no cover - exercised by Windows acceptance
            os.lseek(descriptor, offset + len(result), os.SEEK_SET)
            chunk = os.read(descriptor, size - len(result))
        if not chunk:
            break
        result.extend(chunk)
    return bytes(result)


def _tensor_extent(
    *, name: str, spec: object, payload_size: int,
) -> tuple[int, int]:
    if not isinstance(spec, Mapping):
        raise H310ErosBeta3Error(f"Invalid Beta3 tensor descriptor: {name}")
    offsets = spec.get("data_offsets")
    if not (
        isinstance(offsets, list)
        and len(offsets) == 2
        and all(type(item) is int for item in offsets)
    ):
        raise H310ErosBeta3Error(f"Invalid Beta3 tensor offsets: {name}")
    start, end = offsets
    if start < 0 or end < start or end > payload_size:
        raise H310ErosBeta3Error(f"Beta3 tensor exceeds its payload: {name}")
    dtype = spec.get("dtype")
    shape = spec.get("shape")
    if dtype not in _DTYPE_BYTES or not (
        isinstance(shape, list)
        and len(shape) <= 16
        and all(type(dimension) is int and dimension >= 0 for dimension in shape)
    ):
        raise H310ErosBeta3Error(f"Invalid Beta3 tensor dtype/shape: {name}")
    elements = 1
    for dimension in shape:
        elements *= dimension
    expected_bytes = elements * _DTYPE_BYTES[dtype]
    if end - start != expected_bytes:
        raise H310ErosBeta3Error(
            f"Beta3 tensor byte length does not match dtype/shape: {name}"
        )
    return start, end


def _validate_marker_tensor(
    descriptor: int,
    *,
    data_start: int,
    name: str,
    spec: Mapping[str, Any],
) -> None:
    start, end = spec["data_offsets"]
    marker_size = end - start
    if (
        spec.get("dtype") != "U8"
        or spec.get("shape") != [marker_size]
        or not 1 <= marker_size <= _MARKER_LIMIT_BYTES
    ):
        raise H310ErosBeta3Error(f"Invalid Beta3 ConvRot marker tensor: {name}")
    raw = _read_exact(descriptor, data_start + start, marker_size)
    if len(raw) != marker_size:
        raise H310ErosBeta3Error(f"Truncated Beta3 ConvRot marker tensor: {name}")
    try:
        policy = json.loads(raw.rstrip(b"\0").decode("utf-8"))
    except Exception as error:
        raise H310ErosBeta3Error(
            f"Invalid Beta3 ConvRot marker JSON: {name}"
        ) from error
    if policy != dict(_MARKER_POLICY):
        raise H310ErosBeta3Error(f"Unexpected Beta3 ConvRot marker policy: {name}")


def _validate_scale_pair(
    header: Mapping[str, Any], *, marker_name: str,
) -> None:
    base = marker_name.removesuffix(".comfy_quant")
    weight = header.get(f"{base}.weight")
    scale = header.get(f"{base}.weight_scale")
    if not isinstance(weight, Mapping) or not isinstance(scale, Mapping):
        raise H310ErosBeta3Error(
            f"Beta3 marker has no per-channel weight/scale pair: {marker_name}"
        )
    weight_shape = weight.get("shape")
    if not (
        weight.get("dtype") == "I8"
        and isinstance(weight_shape, list)
        and len(weight_shape) == 2
        and all(type(item) is int and item > 0 for item in weight_shape)
        and scale.get("dtype") == "F32"
        and scale.get("shape") == [weight_shape[0], 1]
    ):
        raise H310ErosBeta3Error(
            f"Beta3 marker is not native per-channel INT8: {marker_name}"
        )


def validate_10eros_beta3_header(
    path: str | os.PathLike[str], artifact_id: str,
) -> dict[str, Any]:
    """Validate bounded header/marker metadata without loading model tensors."""
    artifact = _artifact(artifact_id)
    source = Path(path)
    if source.name != artifact["filename"]:
        raise H310ErosBeta3Error("The selected Beta3 filename does not match its contract")
    descriptor, opened = _open_owned_regular(source)
    try:
        if opened.st_size != artifact["size"]:
            raise H310ErosBeta3Error("The selected Beta3 size does not match its contract")
        raw_length = _read_exact(descriptor, 0, 8)
        if len(raw_length) != 8:
            raise H310ErosBeta3Error("The selected Beta3 artifact has no header")
        header_length = struct.unpack("<Q", raw_length)[0]
        if (
            header_length <= 1
            or header_length > _HEADER_LIMIT_BYTES
            or 8 + header_length > opened.st_size
        ):
            raise H310ErosBeta3Error("The selected Beta3 header size is invalid")
        raw_header = _read_exact(descriptor, 8, header_length)
        if len(raw_header) != header_length:
            raise H310ErosBeta3Error("The selected Beta3 header is truncated")
        try:
            header = json.loads(raw_header)
        except Exception as error:
            raise H310ErosBeta3Error("The selected Beta3 header JSON is invalid") from error
        if not isinstance(header, dict):
            raise H310ErosBeta3Error("The selected Beta3 header must be an object")

        data_start = 8 + header_length
        payload_size = opened.st_size - data_start
        markers: list[tuple[str, Mapping[str, Any], int]] = []
        intervals: list[tuple[int, int]] = []
        for name, spec in header.items():
            if name == "__metadata__":
                continue
            start, end = _tensor_extent(name=name, spec=spec, payload_size=payload_size)
            intervals.append((start, end))
            if name.endswith(".comfy_quant"):
                match = _BLOCK_PATTERN.fullmatch(name)
                if match is None:
                    raise H310ErosBeta3Error(
                        f"Beta3 marker is outside a numbered block: {name}"
                    )
                markers.append((name, spec, int(match.group(1))))
        previous_end = 0
        for start, end in sorted(intervals):
            if start < previous_end:
                raise H310ErosBeta3Error("The selected Beta3 tensor extents overlap")
            previous_end = end

        expected_policy = artifact["layer_policy"]
        expected_blocks = set(expected_policy["quantized_blocks"])
        observed_blocks = {block for _, _, block in markers}
        if len(markers) != expected_policy["marker_count"]:
            raise H310ErosBeta3Error("The selected Beta3 marker count is invalid")
        if observed_blocks != expected_blocks:
            raise H310ErosBeta3Error("The selected Beta3 quantized block policy is invalid")
        per_block = {
            block: sum(marker_block == block for _, _, marker_block in markers)
            for block in observed_blocks
        }
        if any(count != 4 for count in per_block.values()):
            raise H310ErosBeta3Error("The selected Beta3 marker distribution is invalid")

        for name, spec, _block in markers:
            _validate_scale_pair(header, marker_name=name)
            _validate_marker_tensor(
                descriptor, data_start=data_start, name=name, spec=spec,
            )

        edge_blocks = tuple(expected_policy["bf16_edge_blocks"])
        for block in edge_blocks:
            block_tensors = {
                name: spec for name, spec in header.items()
                if name.startswith(f"blocks.{block}.")
                and name != "__metadata__"
            }
            if not block_tensors or any(
                not isinstance(spec, Mapping) or spec.get("dtype") != "BF16"
                for spec in block_tensors.values()
            ):
                raise H310ErosBeta3Error(
                    f"Beta3 skip-edge block {block} is not entirely BF16"
                )

        after = os.fstat(descriptor)
        try:
            final_entry = source.lstat()
        except OSError as error:
            raise H310ErosBeta3Error(
                "The selected Beta3 artifact changed during header validation"
            ) from error
        if (
            _identity(opened) != _identity(after)
            or _identity(after) != _identity(final_entry)
        ):
            raise H310ErosBeta3Error(
                "The selected Beta3 artifact changed during header validation"
            )
        return {
            "header_validated": True,
            "marker_policy_validated": True,
            "per_channel_scale_contract_validated": True,
            "marker_count": len(markers),
            "quantized_blocks": sorted(observed_blocks),
            "bf16_edge_blocks": list(edge_blocks),
        }
    finally:
        os.close(descriptor)


def beta3_candidate_status(
    artifact_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
    receipt_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Return path-free local status without hashing or creating state."""
    artifact = _artifact(artifact_id)
    source = (Path(root) if root is not None else _DEFAULT_ROOT) / artifact["filename"]
    present = False
    candidate = False
    reason = "The pinned Beta3 artifact is not downloaded."
    try:
        entry = source.lstat()
        present = (
            stat.S_ISREG(entry.st_mode)
            and not stat.S_ISLNK(entry.st_mode)
            and _same_owner(entry)
        )
        candidate = present and entry.st_size == artifact["size"]
        reason = (
            "The installed Beta3 candidate requires explicit one-time verification."
            if candidate else "The local Beta3 candidate does not match its passive contract."
        )
    except OSError:
        pass
    receipt = None
    if candidate:
        try:
            receipt = inspect_checkpoint_receipt(
                source,
                expected_sha256=artifact["sha256"],
                expected_size=artifact["size"],
                compatibility="10eros_beta3_turbo_hybrid_scaffold",
                family="minimax_h3",
                role="transformer",
                receipt_root=receipt_root,
            )
        except (OSError, H3CheckpointIntegrityError):
            receipt = None
    verified = receipt is not None
    if verified:
        reason = "The exact Beta3 checkpoint receipt is ready for a future runtime gate."
    return {
        "artifact_id": artifact["artifact_id"],
        "filename": artifact["filename"],
        "registered": True,
        "present": present,
        "candidate": candidate,
        "downloaded": verified,
        "installed": verified,
        "verified": verified,
        "receipt_reused": bool(receipt and receipt.get("receipt_reused")),
        "execution_available": False,
        "enabled_by_default": False,
        "automatic_fallback": False,
        "reason": reason,
    }


def verify_10eros_beta3_artifact(
    path: str | os.PathLike[str],
    artifact_id: str,
    *,
    receipt_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Explicitly validate Beta3 and create/reuse its one-time receipt."""
    artifact = _artifact(artifact_id)
    header = validate_10eros_beta3_header(path, artifact_id)
    try:
        receipt = verify_checkpoint_integrity(
            path,
            expected_sha256=artifact["sha256"],
            expected_size=artifact["size"],
            compatibility="10eros_beta3_turbo_hybrid_scaffold",
            family="minimax_h3",
            role="transformer",
            receipt_root=receipt_root,
        )
    except H3CheckpointIntegrityError as error:
        raise H310ErosBeta3Error(str(error)) from error
    return {
        "artifact": _copy(artifact),
        "validation": header,
        "receipt": dict(receipt),
        "execution_available": False,
    }


__all__ = [
    "TEN_EROS_BETA3_FULL_ID",
    "TEN_EROS_BETA3_FULL_PROFILE_ID",
    "TEN_EROS_BETA3_SKIP_ID",
    "TEN_EROS_BETA3_SKIP_PROFILE_ID",
    "TEN_EROS_REPOSITORY",
    "TEN_EROS_REPOSITORY_HEAD",
    "H310ErosBeta3Error",
    "beta3_candidate_status",
    "get_10eros_beta3_catalog",
    "validate_10eros_beta3_header",
    "verify_10eros_beta3_artifact",
]
