"""Opt-in, transactional updates for versioned Hugging Face checkpoints.

Ordinary model definitions remain immutable and revision-pinned.  A model must
declare ``model_update`` metadata before this module will contact a repository.
The active checkpoint is changed only after the replacement has been fully
downloaded and verified; network failures always leave the last-known-good
manifest and file untouched.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ModelUpdateError(RuntimeError):
    pass


_SAFETENSORS_HEADER_LIMIT = 128 * 1024 * 1024
_COMPATIBILITY_VALUE_BYTES_LIMIT = 8 * 1024 * 1024
_SAFETENSORS_DTYPE_BYTES = {
    "BOOL": 1,
    "U8": 1,
    "I8": 1,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "U16": 2,
    "I16": 2,
    "F16": 2,
    "BF16": 2,
    "U32": 4,
    "I32": 4,
    "F32": 4,
    "U64": 8,
    "I64": 8,
    "F64": 8,
}


def _bounded_compatibility_error(reason: str) -> ModelUpdateError:
    clean = " ".join(str(reason).split())
    if len(clean) > 240:
        clean = clean[:237] + "..."
    return ModelUpdateError(f"H3 checkpoint compatibility rejected: {clean}")


def _read_safetensors_header(path: str | os.PathLike[str]) -> tuple[dict[str, Any], int, int]:
    """Read only the safetensors header and return it with file/data sizes."""
    source = Path(path)
    file_size = source.stat().st_size
    with source.open("rb") as handle:
        raw = handle.read(8)
        if len(raw) != 8:
            raise ModelUpdateError("Downloaded checkpoint has no safetensors header")
        header_size = struct.unpack("<Q", raw)[0]
        if header_size <= 1 or header_size > _SAFETENSORS_HEADER_LIMIT:
            raise ModelUpdateError("Downloaded checkpoint has an invalid safetensors header size")
        if 8 + header_size > file_size:
            raise ModelUpdateError("Downloaded checkpoint has a truncated safetensors header")
        header = handle.read(header_size)
    try:
        parsed = json.loads(header)
    except Exception as error:
        raise ModelUpdateError("Downloaded checkpoint has an invalid safetensors header") from error
    if not isinstance(parsed, dict) or not parsed:
        raise ModelUpdateError("Downloaded checkpoint has an empty safetensors header")
    return parsed, file_size, 8 + header_size


def _tensor_spec(
    header: dict[str, Any], name: str, *, data_size: int,
) -> tuple[str, tuple[int, ...], tuple[int, int]]:
    value = header.get(name)
    if not isinstance(value, dict):
        raise _bounded_compatibility_error(f"required tensor {name!r} is missing")
    dtype = str(value.get("dtype") or "")
    shape_value = value.get("shape")
    offsets_value = value.get("data_offsets")
    if not isinstance(shape_value, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) and item >= 0
        for item in shape_value
    ):
        raise _bounded_compatibility_error(f"tensor {name!r} has an invalid shape")
    if not (
        isinstance(offsets_value, list)
        and len(offsets_value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in offsets_value)
    ):
        raise _bounded_compatibility_error(f"tensor {name!r} has invalid data offsets")
    start, end = offsets_value
    if start < 0 or end < start or end > data_size:
        raise _bounded_compatibility_error(
            f"tensor {name!r} data offsets [{start}, {end}] exceed checkpoint payload"
        )
    dtype_bytes = _SAFETENSORS_DTYPE_BYTES.get(dtype)
    if dtype_bytes is not None:
        elements = 1
        for dimension in shape_value:
            elements *= dimension
        expected_bytes = elements * dtype_bytes
        if end - start != expected_bytes:
            raise _bounded_compatibility_error(
                f"tensor {name!r} layout is {end - start} bytes; expected {expected_bytes}"
            )
    return dtype, tuple(shape_value), (start, end)


def _read_compatibility_tensor_bytes(
    path: str | os.PathLike[str],
    name: str,
    offsets: tuple[int, int],
    *,
    data_start: int,
) -> bytes:
    """Read one explicitly opted-in small tensor value range."""
    start, end = offsets
    size = end - start
    if size > _COMPATIBILITY_VALUE_BYTES_LIMIT:
        raise _bounded_compatibility_error(
            f"tensor {name!r} value validation exceeds the bounded byte limit"
        )
    with Path(path).open("rb") as handle:
        handle.seek(data_start + start)
        payload = handle.read(size)
    if len(payload) != size:
        raise _bounded_compatibility_error(f"tensor {name!r} value payload is truncated")
    return payload


def _validate_h3_quantization_family(
    header: dict[str, Any], family: str, *, data_size: int,
) -> None:
    marker_names = sorted(
        name for name in header if isinstance(name, str) and name.endswith(".comfy_quant")
    )
    w4a8_names = sorted(
        name for name in header if isinstance(name, str) and name.endswith(".weight_s_rel")
    )

    if family == "h3_int8_convrot":
        if not marker_names:
            raise _bounded_compatibility_error("expected INT8 ConvRot markers, but none were found")
        if w4a8_names:
            raise _bounded_compatibility_error("checkpoint mixes W4A8 tensors into INT8 ConvRot")
        for marker_name in marker_names:
            prefix = marker_name.removesuffix(".comfy_quant")
            marker_dtype, _, _ = _tensor_spec(header, marker_name, data_size=data_size)
            weight_dtype, weight_shape, _ = _tensor_spec(
                header, f"{prefix}.weight", data_size=data_size,
            )
            scale_dtype, scale_shape, _ = _tensor_spec(
                header, f"{prefix}.weight_scale", data_size=data_size,
            )
            if marker_dtype != "U8" or weight_dtype != "I8" or scale_dtype != "F32":
                raise _bounded_compatibility_error(
                    f"tensor {prefix!r} is {weight_dtype}/{scale_dtype}; expected I8/F32 ConvRot"
                )
            if len(weight_shape) != 2:
                raise _bounded_compatibility_error(
                    f"tensor {prefix!r} weight has shape {list(weight_shape)}; expected rank 2"
                )
            rows = weight_shape[0]
            if scale_shape not in {(), (1,), (rows,), (rows, 1)}:
                raise _bounded_compatibility_error(
                    f"tensor {prefix!r} scale has shape {list(scale_shape)}; "
                    "expected scalar or per-row F32"
                )
        return

    if family == "h3_w4a8":
        if not w4a8_names:
            raise _bounded_compatibility_error("expected W4A8 scale tensors, but none were found")
        for relative_name in w4a8_names:
            prefix = relative_name.removesuffix(".weight_s_rel")
            weight_dtype, _, _ = _tensor_spec(header, f"{prefix}.weight", data_size=data_size)
            relative_dtype, _, _ = _tensor_spec(header, relative_name, data_size=data_size)
            channel_dtype, _, _ = _tensor_spec(
                header, f"{prefix}.weight_s_channel", data_size=data_size,
            )
            if (
                weight_dtype != "I8"
                or relative_dtype not in {"F8_E4M3", "F8_E5M2"}
                or channel_dtype != "F32"
            ):
                raise _bounded_compatibility_error(
                    f"tensor {prefix!r} is {weight_dtype}/{relative_dtype}/{channel_dtype}; "
                    "expected I8/F8/F32 W4A8"
                )
        return

    if family == "h3_fp8_scaled":
        if not marker_names:
            raise _bounded_compatibility_error("expected scaled-FP8 markers, but none were found")
        if w4a8_names:
            raise _bounded_compatibility_error("checkpoint mixes W4A8 tensors into scaled FP8")
        found_fp8 = False
        for marker_name in marker_names:
            prefix = marker_name.removesuffix(".comfy_quant")
            marker_dtype, _, _ = _tensor_spec(header, marker_name, data_size=data_size)
            weight_dtype, _, _ = _tensor_spec(header, f"{prefix}.weight", data_size=data_size)
            scale_dtype, scale_shape, _ = _tensor_spec(
                header, f"{prefix}.weight_scale", data_size=data_size,
            )
            if marker_dtype != "U8" or weight_dtype not in {"F8_E4M3", "F8_E5M2"}:
                raise _bounded_compatibility_error(
                    f"tensor {prefix!r} is {weight_dtype}; expected a scaled FP8 weight"
                )
            if scale_dtype != "F32" or scale_shape not in {(), (1,)}:
                raise _bounded_compatibility_error(
                    f"tensor {prefix!r} has {scale_dtype}{list(scale_shape)} scale; expected scalar F32"
                )
            found_fp8 = True
        if not found_fp8:
            raise _bounded_compatibility_error("checkpoint contains no scaled-FP8 weights")
        return

    raise ModelUpdateError(f"Unsupported H3 compatibility quantization_family {family!r}")


def validate_safetensors_compatibility(
    path: str | os.PathLike[str], compatibility: dict[str, Any] | None,
) -> None:
    """Validate an opt-in H3 compatibility contract without loading tensors."""
    if compatibility is None:
        return
    if not isinstance(compatibility, dict):
        raise ModelUpdateError("Model update compatibility policy must be an object")
    header, file_size, data_start = _read_safetensors_header(path)
    data_size = file_size - data_start
    family = str(compatibility.get("quantization_family") or "").strip()
    if family:
        _validate_h3_quantization_family(header, family, data_size=data_size)

    required = compatibility.get("required_tensors")
    if required is None:
        required = {}
    if not isinstance(required, dict):
        raise ModelUpdateError("Model update compatibility required_tensors must be an object")

    def parse_rule(
        name: str, rule: dict[str, Any],
    ) -> tuple[list[tuple[int, ...]] | None, list[str] | None]:
        expected_shape = rule.get("shape")
        expected_shapes = rule.get("shapes")
        if expected_shape is not None and expected_shapes is not None:
            raise ModelUpdateError(f"Compatibility rule for {name!r} cannot define shape and shapes")
        if expected_shape is not None:
            expected_shapes = [expected_shape]
        allowed_shapes = None
        if expected_shapes is not None:
            if not (
                isinstance(expected_shapes, list)
                and expected_shapes
                and all(
                    isinstance(candidate, list)
                    and all(
                        isinstance(item, int) and not isinstance(item, bool) and item >= 0
                        for item in candidate
                    )
                    for candidate in expected_shapes
                )
            ):
                raise ModelUpdateError(
                    f"Compatibility shapes for {name!r} must be non-empty lists of non-negative integers"
                )
            allowed_shapes = [tuple(candidate) for candidate in expected_shapes]

        expected_dtypes = rule.get("dtypes", rule.get("dtype"))
        if isinstance(expected_dtypes, str):
            expected_dtypes = [expected_dtypes]
        if expected_dtypes is not None and (
            not isinstance(expected_dtypes, list)
            or not all(isinstance(item, str) and item for item in expected_dtypes)
        ):
            raise ModelUpdateError(f"Compatibility dtypes for {name!r} must be strings")
        return allowed_shapes, expected_dtypes

    def validate_values(
        name: str,
        expectation: dict[str, Any],
        dtype: str,
        offsets: tuple[int, int],
    ) -> None:
        json_fields = expectation.get("json_fields")
        finite_positive = expectation.get("finite_positive")
        if json_fields is None and finite_positive is None:
            return
        payload = _read_compatibility_tensor_bytes(
            path, name, offsets, data_start=data_start,
        )
        if json_fields is not None:
            if not isinstance(json_fields, dict) or not json_fields:
                raise ModelUpdateError(
                    f"Compatibility JSON fields for {name!r} must be a non-empty object"
                )
            try:
                descriptor = json.loads(payload.rstrip(b"\0").decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as error:
                raise _bounded_compatibility_error(
                    f"tensor {name!r} has invalid JSON metadata"
                ) from error
            if not isinstance(descriptor, dict) or any(
                descriptor.get(key) != value for key, value in json_fields.items()
            ):
                raise _bounded_compatibility_error(
                    f"tensor {name!r} JSON metadata does not match the required fields"
                )
        if finite_positive is not None:
            if finite_positive is not True:
                raise ModelUpdateError(
                    f"Compatibility finite_positive for {name!r} must be true"
                )
            if dtype != "F32":
                raise ModelUpdateError(
                    f"Compatibility finite_positive for {name!r} requires F32"
                )
            if not all(
                math.isfinite(value) and value > 0
                for (value,) in struct.iter_unpack("<f", payload)
            ):
                raise _bounded_compatibility_error(
                    f"tensor {name!r} values must be finite and positive"
                )

    for raw_name, expectation in required.items():
        name = str(raw_name)
        if not isinstance(expectation, dict):
            raise ModelUpdateError(f"Compatibility rule for {name!r} must be an object")
        dtype, shape, offsets = _tensor_spec(header, name, data_size=data_size)
        variants = expectation.get("variants")
        if variants is not None:
            if any(key in expectation for key in ("shape", "shapes", "dtype", "dtypes")):
                raise ModelUpdateError(
                    f"Compatibility rule for {name!r} cannot combine variants with shape or dtype"
                )
            if not isinstance(variants, list) or not variants or not all(
                isinstance(variant, dict) for variant in variants
            ):
                raise ModelUpdateError(f"Compatibility variants for {name!r} must be objects")
            parsed_variants = [parse_rule(name, variant) for variant in variants]
            if not any(
                (allowed_shapes is None or shape in allowed_shapes)
                and (allowed_dtypes is None or dtype in allowed_dtypes)
                for allowed_shapes, allowed_dtypes in parsed_variants
            ):
                raise _bounded_compatibility_error(
                    f"tensor {name!r} has layout {dtype}{list(shape)}; "
                    "expected one explicitly supported layout"
                )
            validate_values(name, expectation, dtype, offsets)
            continue

        allowed_shapes, allowed_dtypes = parse_rule(name, expectation)
        if allowed_shapes is not None and shape not in allowed_shapes:
            rendered = " or ".join(str(list(candidate)) for candidate in allowed_shapes)
            raise _bounded_compatibility_error(
                f"tensor {name!r} has shape {list(shape)}; expected {rendered}"
            )
        if allowed_dtypes is not None and dtype not in allowed_dtypes:
            raise _bounded_compatibility_error(
                f"tensor {name!r} has dtype {dtype}; expected {' or '.join(allowed_dtypes)}"
            )
        validate_values(name, expectation, dtype, offsets)


@dataclass(frozen=True)
class HfUpdateCandidate:
    repo_id: str
    revision: str
    path: str
    version: str
    size: int
    sha256: str

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo_id}/resolve/{self.revision}/{self.path}"


def _version_key(value: str) -> tuple[int, ...]:
    numbers = tuple(int(part) for part in re.findall(r"\d+", value))
    if not numbers:
        raise ModelUpdateError(f"Version {value!r} contains no numeric components")
    return numbers


def select_huggingface_candidate(
    metadata: dict[str, Any], policy: dict[str, Any]
) -> HfUpdateCandidate:
    """Select the highest compatible version from one HF model-info payload."""
    repo_id = str(policy.get("repo_id") or "").strip()
    pattern_text = str(policy.get("file_pattern") or "").strip()
    revision = str(metadata.get("sha") or "").strip()
    if not repo_id or not pattern_text or not revision:
        raise ModelUpdateError("Versioned HF policy and metadata are incomplete")
    try:
        pattern = re.compile(pattern_text)
    except re.error as error:
        raise ModelUpdateError(f"Invalid model update file_pattern: {error}") from error

    candidates: list[HfUpdateCandidate] = []
    for sibling in metadata.get("siblings") or []:
        if not isinstance(sibling, dict):
            continue
        path = str(sibling.get("rfilename") or "")
        match = pattern.fullmatch(path)
        if match is None:
            continue
        version = str(match.groupdict().get("version") or "")
        try:
            _version_key(version)
        except ModelUpdateError:
            continue
        lfs = sibling.get("lfs") if isinstance(sibling.get("lfs"), dict) else {}
        size = int(lfs.get("size") or sibling.get("size") or 0)
        sha256 = str(lfs.get("sha256") or "").lower()
        if size <= 0 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            continue
        candidates.append(HfUpdateCandidate(repo_id, revision, path, version, size, sha256))
    if not candidates:
        raise ModelUpdateError("No compatible versioned checkpoint was published")
    return max(candidates, key=lambda item: (_version_key(item.version), item.path))


def fetch_huggingface_candidate(
    policy: dict[str, Any], *, request_get: Callable[..., Any] | None = None
) -> HfUpdateCandidate:
    if str(policy.get("provider") or "huggingface") != "huggingface":
        raise ModelUpdateError("Unsupported model update provider")
    if request_get is None:
        import requests
        request_get = requests.get
    repo_id = str(policy.get("repo_id") or "").strip()
    response = request_get(
        f"https://huggingface.co/api/models/{repo_id}",
        params={"blobs": "true"},
        timeout=(10, 30),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ModelUpdateError("Hugging Face returned invalid model metadata")
    return select_huggingface_candidate(payload, policy)


def select_huggingface_artifact(
    metadata: dict[str, Any], policy: dict[str, Any]
) -> HfUpdateCandidate:
    """Resolve one stable artifact path at the repository's latest revision."""
    repo_id = str(policy.get("repo_id") or "").strip()
    artifact_path = str(policy.get("path") or "").strip().lstrip("/")
    revision = str(metadata.get("sha") or "").strip()
    if not repo_id or not artifact_path or not revision:
        raise ModelUpdateError("Fixed HF artifact policy and metadata are incomplete")
    for sibling in metadata.get("siblings") or []:
        if not isinstance(sibling, dict) or sibling.get("rfilename") != artifact_path:
            continue
        lfs = sibling.get("lfs") if isinstance(sibling.get("lfs"), dict) else {}
        size = int(lfs.get("size") or sibling.get("size") or 0)
        sha256 = str(lfs.get("sha256") or "").lower()
        if size <= 0 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            break
        return HfUpdateCandidate(repo_id, revision, artifact_path, revision, size, sha256)
    raise ModelUpdateError("The configured companion artifact was not published with LFS metadata")


def fetch_huggingface_artifact(
    policy: dict[str, Any], *, request_get: Callable[..., Any] | None = None
) -> HfUpdateCandidate:
    if str(policy.get("provider") or "huggingface") != "huggingface":
        raise ModelUpdateError("Unsupported companion update provider")
    if request_get is None:
        import requests
        request_get = requests.get
    repo_id = str(policy.get("repo_id") or "").strip()
    response = request_get(
        f"https://huggingface.co/api/models/{repo_id}",
        params={"blobs": "true"},
        timeout=(10, 30),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ModelUpdateError("Hugging Face returned invalid companion metadata")
    return select_huggingface_artifact(payload, policy)


def validate_safetensors(
    path: str | os.PathLike[str],
    candidate: HfUpdateCandidate,
    compatibility: dict[str, Any] | None = None,
) -> None:
    """Validate exact LFS size/hash and the safetensors JSON header."""
    source = Path(path)
    if source.stat().st_size != candidate.size:
        raise ModelUpdateError("Downloaded checkpoint size does not match Hugging Face metadata")
    _read_safetensors_header(source)
    # Reject an incompatible multi-gigabyte checkpoint before spending time
    # hashing its payload.  This reads only the bounded JSON header.
    validate_safetensors_compatibility(source, compatibility)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != candidate.sha256:
        raise ModelUpdateError("Downloaded checkpoint SHA-256 does not match Hugging Face metadata")


class VersionedModelUpdater:
    """Per-model serialized updater with a durable last-known-good manifest."""

    def __init__(
        self,
        checkpoint_root: str | os.PathLike[str],
        *,
        locate_file: Callable[[str], str | None],
        protected_path: Callable[[str], bool] | None = None,
        candidate_fetcher: Callable[[dict[str, Any]], HfUpdateCandidate] = fetch_huggingface_candidate,
        artifact_fetcher: Callable[[dict[str, Any]], HfUpdateCandidate] = fetch_huggingface_artifact,
        downloader: Callable[[HfUpdateCandidate, Path], str] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.root = Path(checkpoint_root).resolve()
        self.state_root = self.root / ".maestro-model-updates"
        self.stage_root = self.root / ".maestro-model-update-staging"
        self.locate_file = locate_file
        self.protected_path = protected_path or (lambda _path: False)
        self.candidate_fetcher = candidate_fetcher
        self.artifact_fetcher = artifact_fetcher
        self.downloader = downloader or self._hf_download
        self.clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @staticmethod
    def _safe_id(model_type: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", model_type)[:160]

    def _lock(self, model_type: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(model_type, threading.Lock())

    def _manifest_path(self, model_type: str) -> Path:
        return self.state_root / f"{self._safe_id(model_type)}.json"

    def _component_key(self, model_type: str, policy: dict[str, Any]) -> str:
        component_id = str(policy.get("id") or policy.get("url_field") or "component")
        return f"{model_type}--{component_id}"

    def _read_manifest(self, model_type: str) -> dict[str, Any] | None:
        try:
            value = json.loads(self._manifest_path(model_type).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _sync_directory(path: Path) -> None:
        """Best-effort directory sync for platforms that support it."""
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @staticmethod
    def _cleanup_path(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _stage_manifest(self, model_type: str, value: dict[str, Any], suffix: str) -> Path:
        self.state_root.mkdir(parents=True, exist_ok=True)
        destination = self._manifest_path(model_type)
        temporary = destination.with_name(f".{destination.name}.manifest-stage-{suffix}")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._sync_directory(temporary.parent)
            return temporary
        except Exception:
            self._cleanup_path(temporary)
            raise

    @staticmethod
    def _durable_copy(source: Path, destination: Path) -> None:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())

    def _write_manifest(self, model_type: str, value: dict[str, Any]) -> None:
        destination = self._manifest_path(model_type)
        suffix = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
        temporary = self._stage_manifest(model_type, value, suffix)
        try:
            os.replace(temporary, destination)
            self._sync_directory(destination.parent)
        finally:
            self._cleanup_path(temporary)

    def _publish_transaction(
        self,
        model_type: str,
        staged: str | os.PathLike[str],
        destination: Path,
        manifest: dict[str, Any],
        *,
        active_path: str | os.PathLike[str] | None,
    ) -> dict[str, Any]:
        """Atomically publish model bytes and their matching manifest.

        Large existing checkpoints are parked with a same-filesystem rename,
        not copied.  The small manifest is copied so it can be restored even
        when a replacement implementation raises after committing its rename.
        """
        source = Path(staged)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as handle:
            os.fsync(handle.fileno())

        previous_manifest = self._read_manifest(model_type)
        previous_candidate = (
            previous_manifest.get("candidate")
            if isinstance(previous_manifest, dict)
            and isinstance(previous_manifest.get("candidate"), dict)
            else None
        )
        active_name = Path(active_path).name if active_path else None
        manifest = dict(manifest)
        manifest["rollback_provenance"] = {
            "previous_candidate": previous_candidate,
            "previous_file": active_name,
            "replacement_file": destination.name,
            "same_filename": bool(active_name and active_name == destination.name),
        }

        suffix = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
        manifest_destination = self._manifest_path(model_type)
        manifest_stage = self._stage_manifest(model_type, manifest, suffix)
        manifest_existed = manifest_destination.is_file()
        destination_existed = destination.is_file()
        manifest_backup = manifest_destination.with_name(
            f".{manifest_destination.name}.manifest-backup-{suffix}"
        )
        destination_backup = destination.with_name(
            f".{destination.name}.maestro-rollback-{suffix}"
        )
        manifest_backup_ready = False
        destination_publish_attempted = False
        manifest_commit_attempted = False
        retain_backups = False

        try:
            if manifest_existed:
                self._durable_copy(manifest_destination, manifest_backup)
                self._sync_directory(manifest_backup.parent)
                manifest_backup_ready = True
            if destination_existed:
                os.replace(destination, destination_backup)
                self._sync_directory(destination.parent)

            destination_publish_attempted = True
            os.replace(source, destination)
            self._sync_directory(destination.parent)

            manifest_commit_attempted = True
            os.replace(manifest_stage, manifest_destination)
            self._sync_directory(manifest_destination.parent)

            self._cleanup_path(destination_backup)
            self._cleanup_path(manifest_backup)
            self._sync_directory(destination.parent)
            self._sync_directory(manifest_destination.parent)
            return manifest
        except Exception as error:
            rollback_errors: list[str] = []
            try:
                if destination_backup.exists():
                    os.replace(destination_backup, destination)
                elif not destination_existed and destination_publish_attempted:
                    destination.unlink(missing_ok=True)
                self._sync_directory(destination.parent)
            except Exception as rollback_error:  # noqa: BLE001 - preserve any publication failure
                rollback_errors.append(f"checkpoint: {rollback_error}")
            try:
                if manifest_commit_attempted:
                    if manifest_backup_ready and manifest_backup.exists():
                        os.replace(manifest_backup, manifest_destination)
                    elif manifest_existed:
                        raise ModelUpdateError("previous manifest backup is unavailable")
                    else:
                        manifest_destination.unlink(missing_ok=True)
                self._sync_directory(manifest_destination.parent)
            except Exception as rollback_error:  # noqa: BLE001 - preserve any publication failure
                rollback_errors.append(f"manifest: {rollback_error}")
            self._cleanup_path(manifest_stage)
            if rollback_errors:
                retain_backups = True
                retained = [
                    path.name
                    for path in (destination_backup, manifest_backup)
                    if path.exists()
                ]
                raise ModelUpdateError(
                    "Model publication failed and rollback was incomplete ("
                    + "; ".join(rollback_errors)
                    + "); rollback provenance retained in "
                    + (", ".join(retained) if retained else "the original exception chain")
                ) from error
            self._cleanup_path(destination_backup)
            self._cleanup_path(manifest_backup)
            raise ModelUpdateError(
                "Model publication failed; restored last-known-good bytes and manifest"
            ) from error
        finally:
            self._cleanup_path(manifest_stage)
            if not retain_backups:
                self._cleanup_path(destination_backup)
                self._cleanup_path(manifest_backup)

    def apply_recorded(self, model_type: str, model_def: dict[str, Any]) -> bool:
        manifest = self._read_manifest(model_type)
        candidate_data = manifest.get("candidate") if manifest else None
        if not isinstance(candidate_data, dict):
            return False
        try:
            candidate = HfUpdateCandidate(**candidate_data)
        except TypeError:
            return False
        located = self.locate_file(candidate.filename)
        if not located or not os.path.isfile(located):
            return False
        policy = model_def.get("model_update")
        compatibility = policy.get("compatibility") if isinstance(policy, dict) else None
        validate_safetensors_compatibility(located, compatibility)
        model_def["URLs"] = [candidate.url]
        return True

    @staticmethod
    def _set_component_url(model_def: dict[str, Any], policy: dict[str, Any], url: str) -> None:
        field = str(policy.get("url_field") or "").strip()
        if not field or field in {"URLs", "URLs2"}:
            raise ModelUpdateError("Companion artifact url_field is invalid")
        current = model_def.get(field)
        if current is not None and not isinstance(current, list):
            raise ModelUpdateError("Companion artifact URL field must be a list")
        model_def[field] = [url]

    def apply_recorded_components(self, model_type: str, model_def: dict[str, Any]) -> bool:
        applied = False
        for policy in model_def.get("component_updates") or []:
            if not isinstance(policy, dict) or policy.get("enabled", True) is False:
                continue
            manifest = self._read_manifest(self._component_key(model_type, policy))
            candidate_data = manifest.get("candidate") if manifest else None
            if not isinstance(candidate_data, dict):
                continue
            try:
                candidate = HfUpdateCandidate(**candidate_data)
            except TypeError:
                continue
            located = self.locate_file(candidate.filename)
            if located and os.path.isfile(located):
                validate_safetensors_compatibility(
                    located, policy.get("compatibility"),
                )
                self._set_component_url(model_def, policy, candidate.url)
                applied = True
        return applied

    def _due(self, manifest: dict[str, Any] | None, policy: dict[str, Any], force: bool) -> bool:
        if force or not manifest:
            return True
        interval = max(1.0, float(policy.get("check_interval_hours") or 24.0)) * 3600.0
        return self.clock() - float(manifest.get("last_checked") or 0) >= interval

    @staticmethod
    def _hf_download(candidate: HfUpdateCandidate, stage: Path) -> str:
        from huggingface_hub import hf_hub_download
        return hf_hub_download(
            repo_id=candidate.repo_id,
            revision=candidate.revision,
            filename=candidate.path,
            local_dir=str(stage),
        )

    def ensure_latest(
        self, model_type: str, model_def: dict[str, Any], *, force: bool = False
    ) -> dict[str, Any]:
        policy = model_def.get("model_update")
        if not isinstance(policy, dict) or policy.get("enabled", True) is False:
            return {"status": "pinned"}
        with self._lock(model_type):
            self.apply_recorded(model_type, model_def)
            manifest = self._read_manifest(model_type)
            if not self._due(manifest, policy, force):
                return {"status": "current", "manifest": manifest}
            try:
                candidate = (
                    self.artifact_fetcher(policy)
                    if policy.get("path") else self.candidate_fetcher(policy)
                )
            except Exception as error:  # noqa: BLE001 - provider transports vary
                return {"status": "offline", "error": str(error), "manifest": manifest}

            active_data = manifest.get("candidate") if manifest else None
            active_path = None
            if isinstance(active_data, dict):
                active_path = self.locate_file(str(active_data.get("path") or "").split("/")[-1])
            if not active_path:
                for url in model_def.get("URLs") or []:
                    if isinstance(url, str):
                        active_path = self.locate_file(os.path.basename(url))
                        if active_path:
                            break
            if isinstance(active_data, dict) and active_data.get("sha256") == candidate.sha256 and active_path:
                manifest["last_checked"] = self.clock()
                self._write_manifest(model_type, manifest)
                model_def["URLs"] = [candidate.url]
                return {"status": "current", "manifest": manifest}

            # A model downloaded before the updater manifest was introduced
            # can already be the latest version. Adopt it without downloading
            # or hashing the same 30+ GiB file again; immutable HF LFS handled
            # transfer integrity, and finalize_download verifies size/header.
            if (
                not manifest
                and active_path
                and os.path.basename(active_path) == candidate.filename
                and (
                    not policy.get("path")
                    or any(
                        f"/resolve/{candidate.revision}/" in str(url)
                        for url in model_def.get("URLs") or []
                    )
                )
            ):
                self.finalize_download(model_type, model_def, asdict(candidate))
                return {"status": "current", "manifest": self._read_manifest(model_type)}

            # First installation: point the ordinary downloader at the latest
            # immutable revision. finalize_download() records it afterwards.
            if not active_path:
                model_def["URLs"] = [candidate.url]
                return {"status": "download_required", "candidate": asdict(candidate)}

            stage = self.stage_root / f"{self._safe_id(model_type)}-{os.getpid()}-{threading.get_ident()}"
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True, exist_ok=False)
            try:
                staged = self.downloader(candidate, stage)
                validate_safetensors(staged, candidate, policy.get("compatibility"))
                destination = self.root / candidate.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                new_manifest = {
                    "schema_version": 1,
                    "model_type": model_type,
                    "candidate": asdict(candidate),
                    "last_checked": self.clock(),
                    "installed_at": self.clock(),
                }
                new_manifest = self._publish_transaction(
                    model_type,
                    staged,
                    destination,
                    new_manifest,
                    active_path=active_path,
                )
                model_def["URLs"] = [candidate.url]
                if (
                    active_path
                    and os.path.abspath(active_path) != os.path.abspath(destination)
                    and not self.protected_path(active_path)
                ):
                    try:
                        os.remove(active_path)
                    except OSError:
                        pass
                return {"status": "updated", "manifest": new_manifest}
            finally:
                shutil.rmtree(stage, ignore_errors=True)

    def _ensure_component_latest(
        self,
        model_type: str,
        model_def: dict[str, Any],
        policy: dict[str, Any],
        *,
        force: bool,
    ) -> dict[str, Any]:
        key = self._component_key(model_type, policy)
        with self._lock(key):
            self.apply_recorded_components(model_type, model_def)
            manifest = self._read_manifest(key)
            if not self._due(manifest, policy, force):
                return {"status": "current", "manifest": manifest}
            try:
                candidate = self.artifact_fetcher(policy)
            except Exception as error:  # noqa: BLE001 - provider transports vary
                return {"status": "offline", "error": str(error), "manifest": manifest}
            field = str(policy.get("url_field") or "")
            urls = model_def.get(field) if isinstance(model_def.get(field), list) else []
            active_path = self.locate_file(candidate.filename)
            active_data = manifest.get("candidate") if manifest else None
            if isinstance(active_data, dict) and active_data.get("sha256") == candidate.sha256 and active_path:
                manifest["last_checked"] = self.clock()
                self._write_manifest(key, manifest)
                self._set_component_url(model_def, policy, candidate.url)
                return {"status": "current", "manifest": manifest}

            # A revision-pinned URL proves an existing same-name artifact came
            # from the currently published revision; adopt it without hashing
            # a multi-gigabyte conditioner again.
            pinned_current = any(f"/resolve/{candidate.revision}/" in str(url) for url in urls)
            if not manifest and active_path and pinned_current and Path(active_path).stat().st_size == candidate.size:
                self._record_component(model_type, model_def, policy, candidate)
                return {"status": "current", "manifest": self._read_manifest(key)}

            if not active_path:
                self._set_component_url(model_def, policy, candidate.url)
                return {"status": "download_required", "candidate": asdict(candidate)}

            stage = self.stage_root / f"{self._safe_id(key)}-{os.getpid()}-{threading.get_ident()}"
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True, exist_ok=False)
            try:
                staged = self.downloader(candidate, stage)
                validate_safetensors(staged, candidate, policy.get("compatibility"))
                destination = self.root / candidate.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                new_manifest = {
                    "schema_version": 1,
                    "model_type": model_type,
                    "component_id": str(
                        policy.get("id") or policy.get("url_field") or "component"
                    ),
                    "candidate": asdict(candidate),
                    "last_checked": self.clock(),
                    "installed_at": self.clock(),
                }
                new_manifest = self._publish_transaction(
                    key,
                    staged,
                    destination,
                    new_manifest,
                    active_path=active_path,
                )
                self._set_component_url(model_def, policy, candidate.url)
                return {"status": "updated", "manifest": new_manifest}
            finally:
                shutil.rmtree(stage, ignore_errors=True)

    def ensure_components_latest(
        self, model_type: str, model_def: dict[str, Any], *, force: bool = False
    ) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for policy in model_def.get("component_updates") or []:
            if not isinstance(policy, dict) or policy.get("enabled", True) is False:
                continue
            component_id = str(policy.get("id") or policy.get("url_field") or "component")
            results[component_id] = self._ensure_component_latest(
                model_type, model_def, policy, force=force,
            )
        return results

    def _record_component(
        self,
        model_type: str,
        model_def: dict[str, Any],
        policy: dict[str, Any],
        candidate: HfUpdateCandidate,
    ) -> None:
        key = self._component_key(model_type, policy)
        manifest = {
            "schema_version": 1,
            "model_type": model_type,
            "component_id": str(policy.get("id") or policy.get("url_field") or "component"),
            "candidate": asdict(candidate),
            "last_checked": self.clock(),
            "installed_at": self.clock(),
        }
        self._write_manifest(key, manifest)
        self._set_component_url(model_def, policy, candidate.url)

    def finalize_component_downloads(
        self,
        model_type: str,
        model_def: dict[str, Any],
        candidates: dict[str, dict[str, Any]] | None,
    ) -> None:
        if not candidates:
            return
        for policy in model_def.get("component_updates") or []:
            if not isinstance(policy, dict):
                continue
            component_id = str(policy.get("id") or policy.get("url_field") or "component")
            candidate_data = candidates.get(component_id)
            if not isinstance(candidate_data, dict):
                continue
            candidate = HfUpdateCandidate(**candidate_data)
            located = self.locate_file(candidate.filename)
            if not located:
                raise ModelUpdateError("Downloaded companion artifact cannot be located")
            source = Path(located)
            if source.stat().st_size != candidate.size:
                raise ModelUpdateError("Downloaded companion size does not match its manifest")
            _read_safetensors_header(source)
            validate_safetensors_compatibility(source, policy.get("compatibility"))
            self._record_component(model_type, model_def, policy, candidate)

    def finalize_download(
        self, model_type: str, model_def: dict[str, Any], candidate_data: dict[str, Any] | None
    ) -> None:
        if not candidate_data:
            return
        candidate = HfUpdateCandidate(**candidate_data)
        located = self.locate_file(candidate.filename)
        if not located:
            raise ModelUpdateError("Downloaded versioned checkpoint cannot be located")
        # The HF downloader already validates its immutable LFS object. Verify
        # the exact size and safetensors header here without re-hashing a fresh
        # 30+ GiB initial install; replacement updates use full SHA-256 above.
        source = Path(located)
        if source.stat().st_size != candidate.size:
            raise ModelUpdateError("Downloaded checkpoint size does not match its manifest")
        _read_safetensors_header(source)
        policy = model_def.get("model_update")
        compatibility = policy.get("compatibility") if isinstance(policy, dict) else None
        validate_safetensors_compatibility(source, compatibility)
        manifest = {
            "schema_version": 1,
            "model_type": model_type,
            "candidate": asdict(candidate),
            "last_checked": self.clock(),
            "installed_at": self.clock(),
        }
        self._write_manifest(model_type, manifest)
        model_def["URLs"] = [candidate.url]


__all__ = [
    "HfUpdateCandidate",
    "ModelUpdateError",
    "VersionedModelUpdater",
    "fetch_huggingface_artifact",
    "fetch_huggingface_candidate",
    "select_huggingface_artifact",
    "select_huggingface_candidate",
    "validate_safetensors",
    "validate_safetensors_compatibility",
]
