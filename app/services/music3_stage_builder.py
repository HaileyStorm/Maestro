"""Fail-closed, plan-only staging contract for the MiniMax Music 3 runtime.

This module deliberately contains no downloader, package installer, compiler, or
publisher.  It accepts one independently hash-reviewed build input, derives one
network-capable fetch phase and one network-disabled final-path stage phase, and
verifies bytes produced by a future executor.  Publication remains exclusively
owned by :mod:`services.music3_runtime`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from services import music3_runtime as runtime

BUILDER_INPUT_SCHEMA = "maestro.music3.stage-builder-input.v1"
BUILDER_PLAN_SCHEMA = "maestro.music3.stage-builder-plan.v1"
PARTIAL_DOWNLOAD_SCHEMA = "maestro.music3.stage-builder-partial.v1"
RESUME_SCHEMA = "maestro.music3.stage-builder-resume.v1"

MAX_INPUT_BYTES = 512 * 1024
MAX_ARTIFACTS = 4096
MAX_ARTIFACT_BYTES = 2 * 1024**4
MAX_TOTAL_REQUIRED_BYTES = 16 * 1024**4
MIN_FREE_AFTER_STAGE_BYTES = 50 * 1024**3
REVIEWED_INPUT_DIRECTORY = "reviewed-build-inputs"
DOWNLOAD_DIRECTORY = "stage-builder-downloads"
RESUME_DIRECTORY = "stage-builder-resume"

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,239}")
_VERSION = re.compile(r"[0-9][A-Za-z0-9.!+_-]{0,63}")
_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ASCII_ETAG = re.compile(r"[\x21-\x7e]{1,512}")
_PYTHON_VERSION = re.compile(r"3\.(?:10|11|12)\.[0-9]+")
_PYTHON_ABI = re.compile(r"cp3(?:10|11|12)")
_CUDA_VERSION = re.compile(r"[0-9]{1,2}\.[0-9]{1,2}(?:\.[0-9]{1,2})?")
_WHEEL_NAME_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]*")
_WHEEL_TAG = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
_WHEEL_BUILD_TAG = re.compile(r"[0-9][A-Za-z0-9_]*")
_LINUX_X86_64_PLATFORM_TAG = re.compile(
    r"(?:linux|manylinux(?:1|2010|2014|_[0-9]+_[0-9]+)|musllinux_[0-9]+_[0-9]+)_x86_64"
)

_ARTIFACT_ROLES = frozenset({
    "python-runtime",
    "cuda-runtime",
    "sglang-source",
    "ucx-source",
    "wheel",
    "model",
})
_SINGLETON_ROLES = frozenset({
    "python-runtime",
    "cuda-runtime",
    "sglang-source",
    "ucx-source",
})
_TREE_DIGEST_KEYS = frozenset({
    "runtime_executable_sha256",
    "runtime_source_tree_sha256",
    "dependency_lock_sha256",
    "environment_tree_sha256",
    "ucx_info_sha256",
    "ucx_build_record_sha256",
    "ucx_probe_sha256",
    "model_snapshot_sha256",
})
_DISK_BUDGET_KEYS = frozenset({
    "installed_environment_bytes",
    "model_tree_bytes",
    "source_tree_bytes",
    "ucx_prefix_bytes",
    "scratch_bytes",
})
_REQUIRED_CAPABILITIES = frozenset({
    "cross_process_flock",
    "directory_fsync",
    "executable_mode",
    "atomic_same_filesystem_replace",
    "symlink_detection",
})
_RESUME_PHASES = frozenset({"fetching", "fetched", "staging", "staged"})


class Music3StageBuilderError(RuntimeError):
    """A content-free stage-builder contract failure."""


class Music3StageBuilderBlocked(Music3StageBuilderError):
    """Reviewed inputs or host evidence are not sufficient to stage."""


class Music3StageBuilderSecurityError(Music3StageBuilderError):
    """A path, artifact, or manifest could not be proven safe."""


@dataclass(frozen=True, slots=True, init=False)
class Music3StagePlan:
    """One immutable fetch/stage plan; it is not executable by this module."""

    _encoded: bytes

    def __init__(self, document: Mapping[str, object]) -> None:
        object.__setattr__(self, "_encoded", _canonical_json(document))

    @property
    def document(self) -> dict[str, object]:
        return json.loads(self._encoded.decode("ascii"))

    def to_mapping(self) -> dict[str, object]:
        return self.document

    @property
    def sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self._encoded).hexdigest()

    @property
    def ready(self) -> bool:
        return self.document.get("status") == "ready"


def _canonical_json(value: Mapping[str, object] | Sequence[object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _mapping_sha256(value: Mapping[str, object] | Sequence[object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _constant_time_equal(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
    except (AttributeError, UnicodeError):
        return False


def _digest(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise Music3StageBuilderError(f"{field} must be one lowercase SHA-256 digest")
    return value


def _plain_integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise Music3StageBuilderError(f"{field} is outside its reviewed bound")
    return value


def _exact_keys(value: object, keys: set[str] | frozenset[str], *, field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys):
        raise Music3StageBuilderError(f"{field} fields are not exact")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Music3StageBuilderSecurityError("reviewed input contains duplicate fields")
        result[key] = value
    return result


def _bounded_plain_json(value: object, *, depth: int = 0) -> None:
    if depth > 8:
        raise Music3StageBuilderSecurityError("reviewed input is too deeply nested")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        if len(value) > 2048:
            raise Music3StageBuilderSecurityError("reviewed input contains an oversized string")
        return
    if type(value) is list:
        if len(value) > MAX_ARTIFACTS:
            raise Music3StageBuilderSecurityError("reviewed input contains an oversized list")
        for item in value:
            _bounded_plain_json(item, depth=depth + 1)
        return
    if type(value) is dict:
        if len(value) > MAX_ARTIFACTS:
            raise Music3StageBuilderSecurityError("reviewed input contains too many fields")
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 128:
                raise Music3StageBuilderSecurityError("reviewed input contains an invalid field")
            _bounded_plain_json(item, depth=depth + 1)
        return
    raise Music3StageBuilderSecurityError("reviewed input must contain plain JSON values")


def _assert_runtime_child(layout: runtime.Music3RuntimeLayout, path: Path) -> None:
    if not path.is_absolute() or (path != layout.root and layout.root not in path.parents):
        raise Music3StageBuilderSecurityError("stage-builder path escaped the runtime root")
    try:
        runtime._assert_runtime_path(layout, path, allow_missing=True)
    except runtime.Music3RuntimeError as error:
        raise Music3StageBuilderSecurityError("stage-builder path is not safe") from error


def _safe_regular_file(path: Path, *, expected_device: int) -> os.stat_result:
    try:
        before = os.lstat(path)
    except OSError as error:
        raise Music3StageBuilderSecurityError("stage-builder artifact is unavailable") from error
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or before.st_dev != expected_device
        or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise Music3StageBuilderSecurityError("stage-builder artifact ownership is ambiguous")
    return before


def _read_regular(path: Path, *, expected_device: int, limit: int) -> bytes:
    before = _safe_regular_file(path, expected_device=expected_device)
    if before.st_size > limit:
        raise Music3StageBuilderSecurityError("stage-builder artifact exceeds its size bound")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_uid != os.geteuid()
                or opened.st_nlink != 1
                or not stat.S_ISREG(opened.st_mode)
            ):
                raise Music3StageBuilderSecurityError("stage-builder artifact changed during opening")
            payload = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
        current = os.lstat(path)
    except Music3StageBuilderSecurityError:
        raise
    except OSError as error:
        raise Music3StageBuilderSecurityError("stage-builder artifact could not be read") from error
    if (
        len(payload) > limit
        or after.st_nlink != 1
        or after.st_uid != os.geteuid()
        or not stat.S_ISREG(after.st_mode)
        or after.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or current.st_nlink != 1
        or current.st_uid != os.geteuid()
        or not stat.S_ISREG(current.st_mode)
        or current.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise Music3StageBuilderSecurityError("stage-builder artifact changed during reading")
    return payload


def _regular_file_sha256(path: Path, *, expected_device: int, expected_size: int) -> str:
    before = _safe_regular_file(path, expected_device=expected_device)
    if before.st_size != expected_size:
        raise Music3StageBuilderSecurityError("downloaded artifact size does not match")
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_nlink != 1
                or opened.st_uid != os.geteuid()
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise Music3StageBuilderSecurityError("downloaded artifact changed during opening")
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        current = os.lstat(path)
    except Music3StageBuilderSecurityError:
        raise
    except OSError as error:
        raise Music3StageBuilderSecurityError("downloaded artifact could not be hashed") from error
    if (
        after.st_nlink != 1
        or after.st_uid != os.geteuid()
        or not stat.S_ISREG(after.st_mode)
        or after.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or current.st_nlink != 1
        or current.st_uid != os.geteuid()
        or not stat.S_ISREG(current.st_mode)
        or current.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or current.st_dev != expected_device
        or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise Music3StageBuilderSecurityError("downloaded artifact changed during hashing")
    return "sha256:" + digest.hexdigest()


def _load_json_file(
    layout: runtime.Music3RuntimeLayout,
    path: str | os.PathLike[str],
    *,
    limit: int = MAX_INPUT_BYTES,
) -> dict[str, Any]:
    candidate = Path(path)
    _assert_runtime_child(layout, candidate)
    payload = _read_regular(
        candidate,
        expected_device=layout.pinokio_root.stat().st_dev,
        limit=limit,
    )
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"), object_pairs_hook=_unique_object)
    except Music3StageBuilderSecurityError:
        raise
    except (RecursionError, UnicodeError, ValueError) as error:
        raise Music3StageBuilderSecurityError("stage-builder JSON is invalid") from error
    _bounded_plain_json(value)
    if type(value) is not dict:
        raise Music3StageBuilderSecurityError("stage-builder JSON must be an object")
    return value


def _canonical_https_url(value: object) -> str:
    if type(value) is not str or len(value) > 2048:
        raise Music3StageBuilderError("artifact URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise Music3StageBuilderError("artifact URL is invalid") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.startswith("/")
        or parsed.path.endswith("/")
        or parsed.query
        or parsed.fragment
        or parsed.hostname != parsed.hostname.casefold()
    ):
        raise Music3StageBuilderError("artifact URL is not a canonical secret-free HTTPS URL")
    return value


def _normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def _validate_wheel_filename(
    filename: str,
    *,
    name: str,
    version: str,
    python_abi: str,
) -> None:
    if not filename.endswith(".whl"):
        raise Music3StageBuilderError("wheel lock permits no source distribution fallback")
    components = filename[:-4].split("-")
    if len(components) not in {5, 6}:
        raise Music3StageBuilderError("wheel filename is not canonical")
    distribution, wheel_version = components[:2]
    if (
        _WHEEL_NAME_COMPONENT.fullmatch(distribution) is None
        or _WHEEL_NAME_COMPONENT.fullmatch(wheel_version) is None
        or (len(components) == 6 and _WHEEL_BUILD_TAG.fullmatch(components[2]) is None)
    ):
        raise Music3StageBuilderError("wheel filename is not canonical")
    python_tag, abi_tag, platform_tag = components[-3:]
    if any(_WHEEL_TAG.fullmatch(tag) is None for tag in (python_tag, abi_tag, platform_tag)):
        raise Music3StageBuilderError("wheel filename is not canonical")
    if (
        _normalize_package(distribution) != _normalize_package(name)
        or wheel_version.replace("_", "-") != version.replace("_", "-")
    ):
        raise Music3StageBuilderError("wheel filename does not bind its package and version")
    python_tags = set(python_tag.split("."))
    abi_tags = set(abi_tag.split("."))
    platforms = set(platform_tag.split("."))
    pure_python = python_tags in ({"py3"}, {"py2", "py3"}) and abi_tags == {"none"}
    exact_cpython = python_abi in python_tags and python_abi in abi_tags
    compatible_abi3 = (
        "abi3" in abi_tags
        and any(
            (match := re.fullmatch(r"cp3([0-9]+)", tag)) is not None
            and 2 <= int(match.group(1)) <= int(python_abi[3:])
            for tag in python_tags
        )
    )
    if not (pure_python or exact_cpython or compatible_abi3):
        raise Music3StageBuilderError("wheel tags do not match the reviewed Python ABI")
    if platforms == {"any"}:
        if not pure_python:
            raise Music3StageBuilderError("compiled wheel cannot use the any platform tag")
    elif not all(_LINUX_X86_64_PLATFORM_TAG.fullmatch(platform) for platform in platforms):
        raise Music3StageBuilderError("wheel platform is not reviewed Linux x86_64")


def _validate_artifact(value: object) -> dict[str, object]:
    item = _exact_keys(
        value,
        {"artifact_id", "role", "filename", "url", "sha256", "size", "etag"},
        field="artifact",
    )
    artifact_id = item["artifact_id"]
    role = item["role"]
    filename = item["filename"]
    etag = item["etag"]
    if type(artifact_id) is not str or _IDENTIFIER.fullmatch(artifact_id) is None:
        raise Music3StageBuilderError("artifact ID is invalid")
    if role not in _ARTIFACT_ROLES:
        raise Music3StageBuilderError("artifact role is not reviewed")
    if (
        type(filename) is not str
        or _FILENAME.fullmatch(filename) is None
        or Path(filename).name != filename
    ):
        raise Music3StageBuilderError("artifact filename is invalid")
    if type(etag) is not str or _ASCII_ETAG.fullmatch(etag) is None:
        raise Music3StageBuilderError("artifact ETag is invalid")
    return {
        "artifact_id": artifact_id,
        "role": role,
        "filename": filename,
        "url": _canonical_https_url(item["url"]),
        "sha256": _digest(item["sha256"], field="artifact digest"),
        "size": _plain_integer(
            item["size"], field="artifact size", minimum=1, maximum=MAX_ARTIFACT_BYTES,
        ),
        "etag": etag,
    }


def _validate_reviewed_input(
    value: object,
    *,
    provision_plan: runtime.Music3ProvisionPlan,
) -> dict[str, object]:
    document = _exact_keys(
        value,
        {
            "schema",
            "generation_id",
            "runtime_plan_sha256",
            "pins",
            "artifacts",
            "wheel_lock",
            "tree_expectations",
            "disk_budget",
        },
        field="reviewed build input",
    )
    if document["schema"] != BUILDER_INPUT_SCHEMA:
        raise Music3StageBuilderError("reviewed build input schema is unsupported")
    generation_id = document["generation_id"]
    if type(generation_id) is not str or runtime._GENERATION_ID.fullmatch(generation_id) is None:
        raise Music3StageBuilderError("generation ID is invalid")
    if document["runtime_plan_sha256"] != provision_plan.sha256:
        raise Music3StageBuilderError("reviewed input is bound to another runtime plan")

    pins = _exact_keys(
        document["pins"],
        {
            "model_id",
            "model_revision",
            "sglang_source_revision",
            "ucx_version",
            "ucx_source_revision",
            "ucx_source_tarball_sha256",
            "ucx_source_tarball_size",
            "ucx_configure_flags",
            "python_runtime",
            "cuda_runtime",
        },
        field="reviewed pins",
    )
    fixed_pins = {
        "model_id": runtime.MUSIC3_MODEL_ID,
        "model_revision": runtime.PINNED_MODEL_REVISION,
        "sglang_source_revision": runtime.PINNED_SGLANG_SOURCE_REVISION,
        "ucx_version": runtime.PINNED_UCX_VERSION,
        "ucx_source_revision": runtime.PINNED_UCX_SOURCE_REVISION,
        "ucx_source_tarball_sha256": runtime.PINNED_UCX_TARBALL_SHA256,
        "ucx_source_tarball_size": runtime.PINNED_UCX_TARBALL_SIZE,
        "ucx_configure_flags": list(runtime.PINNED_UCX_CONFIGURE_FLAGS),
    }
    for key, expected in fixed_pins.items():
        if type(pins.get(key)) is not type(expected) or pins.get(key) != expected:
            raise Music3StageBuilderError("reviewed source, model, or UCX pin is not exact")

    python_pin = _exact_keys(
        pins["python_runtime"],
        {"implementation", "version", "abi", "artifact_id"},
        field="Python runtime pin",
    )
    if (
        python_pin["implementation"] != "cpython"
        or type(python_pin["version"]) is not str
        or _PYTHON_VERSION.fullmatch(python_pin["version"]) is None
        or type(python_pin["abi"]) is not str
        or _PYTHON_ABI.fullmatch(python_pin["abi"]) is None
        or type(python_pin["artifact_id"]) is not str
    ):
        raise Music3StageBuilderError("Python runtime pin is not exact")
    python_parts = python_pin["version"].split(".")
    if python_pin["abi"] != f"cp{python_parts[0]}{python_parts[1]}":
        raise Music3StageBuilderError("Python version and ABI pins do not match")
    cuda_pin = _exact_keys(
        pins["cuda_runtime"],
        {"version", "architecture", "artifact_id"},
        field="CUDA runtime pin",
    )
    if (
        type(cuda_pin["version"]) is not str
        or _CUDA_VERSION.fullmatch(cuda_pin["version"]) is None
        or cuda_pin["architecture"] != "linux-x86_64"
        or type(cuda_pin["artifact_id"]) is not str
    ):
        raise Music3StageBuilderError("CUDA runtime pin is not exact")

    raw_artifacts = document["artifacts"]
    if type(raw_artifacts) is not list or not 1 <= len(raw_artifacts) <= MAX_ARTIFACTS:
        raise Music3StageBuilderError("artifact inventory is outside its reviewed bound")
    artifacts = [_validate_artifact(item) for item in raw_artifacts]
    artifact_by_id = {item["artifact_id"]: item for item in artifacts}
    if len(artifact_by_id) != len(artifacts):
        raise Music3StageBuilderError("artifact inventory contains duplicate IDs")
    if len({item["filename"] for item in artifacts}) != len(artifacts):
        raise Music3StageBuilderError("artifact inventory contains duplicate filenames")
    if len({item["sha256"] for item in artifacts}) != len(artifacts):
        raise Music3StageBuilderError("artifact inventory contains duplicate content addresses")
    for role in _SINGLETON_ROLES:
        if sum(item["role"] == role for item in artifacts) != 1:
            raise Music3StageBuilderError("artifact inventory misses an exact singleton role")
    if not any(item["role"] == "wheel" for item in artifacts):
        raise Music3StageBuilderError("artifact inventory has no complete wheel closure")
    if not any(item["role"] == "model" for item in artifacts):
        raise Music3StageBuilderError("artifact inventory has no model snapshot")
    if artifact_by_id.get(python_pin["artifact_id"], {}).get("role") != "python-runtime":
        raise Music3StageBuilderError("Python pin is not bound to its runtime artifact")
    if artifact_by_id.get(cuda_pin["artifact_id"], {}).get("role") != "cuda-runtime":
        raise Music3StageBuilderError("CUDA pin is not bound to its runtime artifact")
    ucx_artifact = next(item for item in artifacts if item["role"] == "ucx-source")
    if (
        ucx_artifact["sha256"] != runtime.PINNED_UCX_TARBALL_SHA256
        or ucx_artifact["size"] != runtime.PINNED_UCX_TARBALL_SIZE
    ):
        raise Music3StageBuilderError("UCX source artifact does not match the reviewed tarball")

    raw_lock = document["wheel_lock"]
    if type(raw_lock) is not list or not 1 <= len(raw_lock) <= MAX_ARTIFACTS:
        raise Music3StageBuilderError("wheel lock is outside its reviewed bound")
    lock: list[dict[str, object]] = []
    names: set[str] = set()
    locked_artifacts: set[str] = set()
    for raw_entry in raw_lock:
        entry = _exact_keys(
            raw_entry,
            {
                "name",
                "version",
                "requirement",
                "artifact_id",
                "filename",
                "sha256",
                "size",
            },
            field="wheel lock entry",
        )
        name = entry["name"]
        version = entry["version"]
        requirement = entry["requirement"]
        artifact_id = entry["artifact_id"]
        if type(name) is not str or _PACKAGE.fullmatch(name) is None:
            raise Music3StageBuilderError("wheel package name is invalid")
        if type(version) is not str or _VERSION.fullmatch(version) is None:
            raise Music3StageBuilderError("wheel version is not one exact pin")
        if (
            type(requirement) is not str
            or runtime._EXACT_REQUIREMENT.fullmatch(requirement) is None
            or _normalize_package(requirement.partition("==")[0].partition("[")[0])
            != _normalize_package(name)
            or requirement.partition("==")[2] != version
        ):
            raise Music3StageBuilderError("wheel requirement is not one exact matching pin")
        normalized = _normalize_package(name)
        if normalized in names:
            raise Music3StageBuilderError("wheel lock contains duplicate package pins")
        names.add(normalized)
        if type(artifact_id) is not str or artifact_id in locked_artifacts:
            raise Music3StageBuilderError("wheel lock contains duplicate artifact pins")
        artifact = artifact_by_id.get(artifact_id)
        if artifact is None or artifact["role"] != "wheel":
            raise Music3StageBuilderError("wheel lock references a non-wheel artifact")
        _validate_wheel_filename(
            str(artifact["filename"]),
            name=name,
            version=version,
            python_abi=str(python_pin["abi"]),
        )
        expected_fields = {
            "filename": artifact["filename"],
            "sha256": artifact["sha256"],
            "size": artifact["size"],
        }
        if any(entry[key] != expected for key, expected in expected_fields.items()):
            raise Music3StageBuilderError("wheel lock does not exactly bind its artifact")
        locked_artifacts.add(artifact_id)
        lock.append({
            "name": name,
            "version": version,
            "requirement": requirement,
            "artifact_id": artifact_id,
            **expected_fields,
        })
    wheel_artifacts = {item["artifact_id"] for item in artifacts if item["role"] == "wheel"}
    if locked_artifacts != wheel_artifacts:
        raise Music3StageBuilderError("wheel lock is not the complete wheel artifact closure")
    required_pins = {
        _normalize_package(line.partition("==")[0].partition("[")[0]): line.partition("==")[2]
        for line in runtime.REQUIRED_RUNTIME_LOCK_LINES
    }
    actual_pins = {_normalize_package(str(item["name"])): item["version"] for item in lock}
    requirements = {str(item["requirement"]) for item in lock}
    if (
        any(actual_pins.get(name) != version for name, version in required_pins.items())
        or not runtime.REQUIRED_RUNTIME_LOCK_LINES.issubset(requirements)
    ):
        raise Music3StageBuilderError("wheel lock misses a runtime-required exact pin")

    tree_expectations = _exact_keys(
        document["tree_expectations"], _TREE_DIGEST_KEYS, field="tree expectations",
    )
    trees = {
        key: _digest(tree_expectations[key], field=key)
        for key in sorted(_TREE_DIGEST_KEYS)
    }
    dependency_lock_bytes = ("\n".join(sorted(requirements)) + "\n").encode("utf-8")
    dependency_lock_sha256 = "sha256:" + hashlib.sha256(dependency_lock_bytes).hexdigest()
    if trees["dependency_lock_sha256"] != dependency_lock_sha256:
        raise Music3StageBuilderError(
            "dependency lock digest does not bind the complete exact wheel requirements"
        )
    disk_budget = _exact_keys(
        document["disk_budget"], _DISK_BUDGET_KEYS, field="disk budget",
    )
    budget = {
        key: _plain_integer(
            disk_budget[key], field=key, minimum=0, maximum=MAX_ARTIFACT_BYTES,
        )
        for key in sorted(_DISK_BUDGET_KEYS)
    }
    if not all(budget[key] > 0 for key in _DISK_BUDGET_KEYS - {"scratch_bytes"}):
        raise Music3StageBuilderError("disk budget omits an installed tree")
    role_bytes = {
        role: sum(int(item["size"]) for item in artifacts if item["role"] == role)
        for role in _ARTIFACT_ROLES
    }
    budget_floors = {
        "installed_environment_bytes": (
            role_bytes["python-runtime"]
            + role_bytes["cuda-runtime"]
            + role_bytes["wheel"]
        ),
        "model_tree_bytes": role_bytes["model"],
        "source_tree_bytes": role_bytes["sglang-source"],
        "ucx_prefix_bytes": role_bytes["ucx-source"],
        "scratch_bytes": max(int(item["size"]) for item in artifacts),
    }
    if any(budget[key] < floor for key, floor in budget_floors.items()):
        raise Music3StageBuilderError("disk budget is below its reviewed artifact floor")
    required_bytes = sum(int(item["size"]) for item in artifacts) + sum(budget.values())
    if required_bytes > MAX_TOTAL_REQUIRED_BYTES:
        raise Music3StageBuilderError("stage disk budget exceeds its hard limit")

    return {
        "schema": BUILDER_INPUT_SCHEMA,
        "generation_id": generation_id,
        "runtime_plan_sha256": provision_plan.sha256,
        "pins": {
            **fixed_pins,
            "python_runtime": dict(python_pin),
            "cuda_runtime": dict(cuda_pin),
        },
        "artifacts": sorted(artifacts, key=lambda item: str(item["artifact_id"])),
        "wheel_lock": sorted(lock, key=lambda item: _normalize_package(str(item["name"]))),
        "tree_expectations": trees,
        "disk_budget": budget,
    }


def load_reviewed_music3_build_input(
    pinokio_root: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
    *,
    expected_manifest_sha256: object,
) -> tuple[dict[str, object], str, runtime.Music3ProvisionPlan]:
    """Load one runtime-root-local manifest only after its digest is supplied."""

    expected = _digest(
        expected_manifest_sha256,
        field="independently reviewed build-input manifest digest",
    )
    provision_plan = runtime.build_music3_provision_plan(
        pinokio_root,
        runtime_source_revision=runtime.PINNED_SGLANG_SOURCE_REVISION,
        ucx_version=runtime.PINNED_UCX_VERSION,
        ucx_source_revision=runtime.PINNED_UCX_SOURCE_REVISION,
    )
    value = _load_json_file(provision_plan.layout, manifest_path)
    actual = _mapping_sha256(value)
    if not _constant_time_equal(expected, actual):
        raise Music3StageBuilderSecurityError(
            "build input is not the independently reviewed manifest"
        )
    return _validate_reviewed_input(value, provision_plan=provision_plan), actual, provision_plan


def _validated_filesystem_capability(value: object) -> dict[str, object]:
    evidence = _exact_keys(
        value,
        {"schema", "filesystem_type", *_REQUIRED_CAPABILITIES},
        field="runtime filesystem capability evidence",
    )
    if evidence["schema"] != "maestro.music3.filesystem-capability.v1":
        raise Music3StageBuilderSecurityError("runtime filesystem capability schema is unsupported")
    filesystem_type = evidence["filesystem_type"]
    if type(filesystem_type) is not str or not filesystem_type or len(filesystem_type) > 64:
        raise Music3StageBuilderSecurityError("runtime filesystem type is invalid")
    if any(evidence[key] is not True for key in _REQUIRED_CAPABILITIES):
        raise Music3StageBuilderBlocked("runtime filesystem capabilities are incomplete")
    return dict(evidence)


def blocked_music3_stage_plan(
    pinokio_root: str | os.PathLike[str],
) -> Music3StagePlan:
    """Return the truthful pre-review state without capability/free-space probes."""

    provision_plan = runtime.Music3ProvisionPlan(
        layout=runtime.resolve_music3_runtime_layout(pinokio_root),
        runtime_source_revision=runtime.PINNED_SGLANG_SOURCE_REVISION,
        ucx_version=runtime.PINNED_UCX_VERSION,
        ucx_source_revision=runtime.PINNED_UCX_SOURCE_REVISION,
    )
    return Music3StagePlan({
        "schema": BUILDER_PLAN_SCHEMA,
        "status": "blocked",
        "mutation": False,
        "runtime_plan_sha256": provision_plan.sha256,
        "runtime_root": str(provision_plan.layout.root),
        "blockers": [
            "reviewed_python_runtime_artifact_missing",
            "reviewed_cuda_runtime_artifact_missing",
            "complete_hashed_transitive_wheel_lock_missing",
            "independent_build_input_manifest_sha256_missing",
        ],
        "network_phases": 0,
        "stage_execution_available": False,
        "publication_owner": "services.music3_runtime",
    })


def _download_descriptor(
    layout: runtime.Music3RuntimeLayout,
    artifact: Mapping[str, object],
) -> dict[str, object]:
    hexadecimal = str(artifact["sha256"]).removeprefix("sha256:")
    directory = layout.cache / DOWNLOAD_DIRECTORY / "sha256" / hexadecimal
    completed = directory / str(artifact["filename"])
    partial = directory / f".{artifact['filename']}.part"
    partial_record = directory / f".{artifact['filename']}.part.json"
    for candidate in (directory, completed, partial, partial_record):
        _assert_runtime_child(layout, candidate)
    return {
        "artifact_id": artifact["artifact_id"],
        "role": artifact["role"],
        "url": artifact["url"],
        "expected_sha256": artifact["sha256"],
        "expected_size": artifact["size"],
        "expected_etag": artifact["etag"],
        "completed_path": str(completed),
        "partial_path": str(partial),
        "partial_record_path": str(partial_record),
    }


def build_music3_stage_plan(
    pinokio_root: str | os.PathLike[str],
    *,
    reviewed_manifest_path: str | os.PathLike[str] | None = None,
    expected_reviewed_manifest_sha256: object | None = None,
    available_bytes: int | None = None,
    filesystem_capability_provider: Callable[[runtime.Music3RuntimeLayout], Mapping[str, object]] | None = None,
) -> Music3StagePlan:
    """Derive one deterministic plan; never fetch, build, stage, or publish."""

    if reviewed_manifest_path is None and expected_reviewed_manifest_sha256 is None:
        return blocked_music3_stage_plan(pinokio_root)
    if reviewed_manifest_path is None or expected_reviewed_manifest_sha256 is None:
        raise Music3StageBuilderBlocked(
            "reviewed manifest path and independent digest must be supplied together"
        )
    reviewed, reviewed_sha256, provision_plan = load_reviewed_music3_build_input(
        pinokio_root,
        reviewed_manifest_path,
        expected_manifest_sha256=expected_reviewed_manifest_sha256,
    )
    layout = provision_plan.layout
    if not layout.root.is_dir():
        raise Music3StageBuilderBlocked("runtime root must exist before filesystem acceptance")
    provider = filesystem_capability_provider or runtime._filesystem_capability_evidence
    filesystem_capability = _validated_filesystem_capability(provider(layout))

    downloads = [
        _download_descriptor(layout, artifact)
        for artifact in reviewed["artifacts"]
    ]
    required_bytes = (
        sum(int(item["expected_size"]) for item in downloads)
        + sum(int(value) for value in reviewed["disk_budget"].values())
    )
    observed_free = shutil.disk_usage(layout.pinokio_root).free if available_bytes is None else available_bytes
    _plain_integer(
        observed_free,
        field="available runtime storage",
        minimum=0,
        maximum=1 << 63,
    )
    if observed_free < required_bytes + MIN_FREE_AFTER_STAGE_BYTES:
        raise Music3StageBuilderBlocked("runtime storage does not satisfy the reviewed disk budget")

    generation_id = str(reviewed["generation_id"])
    generation = layout.generations / generation_id
    _assert_runtime_child(layout, generation)
    generation_lock_name = provision_plan.to_mapping()["paths"].get("generation_lock_name")
    if generation_lock_name != runtime.GENERATION_LOCK_NAME:
        raise Music3StageBuilderSecurityError("runtime generation-lock contract is not exact")
    generation_lock = generation / generation_lock_name
    _assert_runtime_child(layout, generation_lock)
    trees = reviewed["tree_expectations"]
    artifact_by_id = {
        item["artifact_id"]: item
        for item in reviewed["artifacts"]
    }
    python_pin = reviewed["pins"]["python_runtime"]
    python_artifact = artifact_by_id[python_pin["artifact_id"]]
    python_runtime = {
        "implementation": python_pin["implementation"],
        "version": python_pin["version"],
        "abi": python_pin["abi"],
        "artifact_filename": python_artifact["filename"],
        "artifact_sha256": python_artifact["sha256"],
        "artifact_size": python_artifact["size"],
    }
    cuda_pin = reviewed["pins"]["cuda_runtime"]
    cuda_artifact = artifact_by_id[cuda_pin["artifact_id"]]
    cuda_runtime = {
        "version": cuda_pin["version"],
        "architecture": cuda_pin["architecture"],
        "artifact_filename": cuda_artifact["filename"],
        "artifact_sha256": cuda_artifact["sha256"],
        "artifact_size": cuda_artifact["size"],
    }
    stage_manifest = runtime.build_music3_stage_manifest(
        provision_plan,
        generation_id=generation_id,
        runtime_executable_sha256=trees["runtime_executable_sha256"],
        runtime_source_tree_sha256=trees["runtime_source_tree_sha256"],
        dependency_lock_sha256=trees["dependency_lock_sha256"],
        environment_tree_sha256=trees["environment_tree_sha256"],
        ucx_info_sha256=trees["ucx_info_sha256"],
        ucx_build_record_sha256=trees["ucx_build_record_sha256"],
        ucx_probe_sha256=trees["ucx_probe_sha256"],
        model_snapshot_sha256=trees["model_snapshot_sha256"],
        python_runtime=python_runtime,
        cuda_runtime=cuda_runtime,
    )
    stage_manifest_sha256 = _mapping_sha256(stage_manifest)
    resume_identity = _mapping_sha256({
        "reviewed_manifest_sha256": reviewed_sha256,
        "runtime_plan_sha256": provision_plan.sha256,
        "filesystem_capability_sha256": _mapping_sha256(filesystem_capability),
        "generation_path": str(generation),
        "downloads": downloads,
        "runtime_stage_manifest_sha256": stage_manifest_sha256,
    })
    resume_record = layout.state / RESUME_DIRECTORY / f"{generation_id}.json"
    _assert_runtime_child(layout, resume_record)
    wheel_paths = [
        item["completed_path"]
        for item in downloads
        if item["role"] == "wheel"
    ]
    requirements_lock_lines = sorted(
        str(item["requirement"])
        for item in reviewed["wheel_lock"]
    )
    document = {
        "schema": BUILDER_PLAN_SCHEMA,
        "status": "ready",
        "mutation": False,
        "execution_implemented": False,
        "runtime_plan_sha256": provision_plan.sha256,
        "reviewed_manifest_sha256": reviewed_sha256,
        "filesystem_capability": filesystem_capability,
        "filesystem_capability_sha256": _mapping_sha256(filesystem_capability),
        "required_bytes": required_bytes,
        "disk_budget": reviewed["disk_budget"],
        "minimum_free_after_stage_bytes": MIN_FREE_AFTER_STAGE_BYTES,
        "disk_budget_satisfied": True,
        "generation_id": generation_id,
        "final_generation_path": str(generation),
        "resume_identity": resume_identity,
        "resume_record_path": str(resume_record),
        "fetch_phase": {
            "network_allowed": True,
            "phase_count": 1,
            "downloads": downloads,
            "partial_binding_fields": [
                "url",
                "expected_size",
                "expected_etag",
                "expected_sha256",
                "partial_sha256",
            ],
        },
        "stage_phase": {
            "network_allowed": False,
            "final_path_install_required": True,
            "final_generation_path": str(generation),
            "generation_lock": str(generation_lock),
            "ucx_prefix": str(generation / "env"),
            "ucx_info_path": str(generation / "env" / "bin" / "ucx_info"),
            "ucx_library_path": str(generation / "env" / "lib"),
            "python_runtime_record": {
                "path": str(generation / runtime.PYTHON_RUNTIME_RECORD),
                "mode": "0600",
                "value": python_runtime,
            },
            "cuda_runtime_record": {
                "path": str(generation / runtime.CUDA_RUNTIME_RECORD),
                "mode": "0600",
                "value": cuda_runtime,
            },
            "wheel_paths": wheel_paths,
            "requirements_lock_lines": requirements_lock_lines,
            "wheel_install_policy": {
                "no_index": True,
                "no_deps": True,
                "require_hashes": True,
                "editable": False,
                "sdist_fallback": False,
            },
            "environment": {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PIP_NO_INDEX": "1",
                "NO_PROXY": "*",
                "no_proxy": "*",
            },
            "write_seal_protocol": {
                "lock_name": generation_lock_name,
                "lock_mode": "0600",
                "lock_must_be_empty": True,
                "exclusive_flock_required": True,
                "revalidate_resume_identity_after_lock": True,
                "hold_through_tree_hash_manifest_write_and_fsync": True,
                "post_seal_writes_allowed": False,
            },
        },
        "runtime_stage_manifest": stage_manifest,
        "runtime_stage_manifest_sha256": stage_manifest_sha256,
        "publication": {
            "owner": "services.music3_runtime",
            "builder_may_switch_current": False,
            "builder_may_switch_previous": False,
            "independent_expected_stage_manifest_sha256_required": True,
        },
    }
    return Music3StagePlan(document)


def _download_by_id(plan: Music3StagePlan, artifact_id: str) -> dict[str, object]:
    if not plan.ready:
        raise Music3StageBuilderBlocked("stage plan is not ready")
    downloads = plan.document["fetch_phase"]["downloads"]
    matches = [item for item in downloads if item["artifact_id"] == artifact_id]
    if len(matches) != 1:
        raise Music3StageBuilderError("artifact ID is not present exactly once")
    return matches[0]


def _layout_from_plan(plan: Music3StagePlan) -> runtime.Music3RuntimeLayout:
    generation = Path(str(plan.document.get("final_generation_path", "")))
    if len(generation.parents) < 5:
        raise Music3StageBuilderSecurityError("final generation path is invalid")
    layout = runtime.resolve_music3_runtime_layout(generation.parents[4])
    if generation.parent != layout.generations:
        raise Music3StageBuilderSecurityError("final generation path is not runtime-rooted")
    return layout


def _tree_total_bytes(root: Path, *, expected_device: int) -> int:
    runtime._safe_tree(root, expected_device=expected_device)
    total = 0
    for path in root.rglob("*"):
        info = os.lstat(path)
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def verify_music3_download_cache(plan: Music3StagePlan) -> dict[str, object]:
    """Verify every completed content-addressed download without executing it."""

    if not plan.ready:
        raise Music3StageBuilderBlocked("stage plan is not ready")
    layout = _layout_from_plan(plan)
    expected_device = layout.pinokio_root.stat().st_dev
    verified: list[dict[str, object]] = []
    for item in plan.document["fetch_phase"]["downloads"]:
        completed = Path(str(item["completed_path"]))
        partial = Path(str(item["partial_path"]))
        partial_record = Path(str(item["partial_record_path"]))
        for candidate in (completed, partial, partial_record):
            _assert_runtime_child(layout, candidate)
        if partial.exists() or partial.is_symlink() or partial_record.exists() or partial_record.is_symlink():
            raise Music3StageBuilderBlocked("download cache contains an unfinished partial")
        actual = _regular_file_sha256(
            completed,
            expected_device=expected_device,
            expected_size=int(item["expected_size"]),
        )
        if not _constant_time_equal(str(item["expected_sha256"]), actual):
            raise Music3StageBuilderSecurityError("downloaded artifact digest does not match")
        verified.append({
            "artifact_id": item["artifact_id"],
            "sha256": actual,
            "size": item["expected_size"],
        })
    return {
        "verified": True,
        "network_used": False,
        "resume_identity": plan.document["resume_identity"],
        "artifacts": verified,
    }


def validate_music3_partial_download(
    plan: Music3StagePlan,
    artifact_id: str,
) -> dict[str, object]:
    """Validate resumable partial identity; ambiguous partials fail closed."""

    item = _download_by_id(plan, artifact_id)
    layout = _layout_from_plan(plan)
    expected_device = layout.pinokio_root.stat().st_dev
    partial = Path(str(item["partial_path"]))
    record_path = Path(str(item["partial_record_path"]))
    completed_path = Path(str(item["completed_path"]))
    if completed_path.exists() or completed_path.is_symlink():
        raise Music3StageBuilderBlocked("completed and partial download state overlap")
    info = _safe_regular_file(partial, expected_device=expected_device)
    if info.st_size > int(item["expected_size"]):
        raise Music3StageBuilderSecurityError("partial download exceeds its expected length")
    partial_sha256 = _regular_file_sha256(
        partial,
        expected_device=expected_device,
        expected_size=info.st_size,
    )
    record = _load_json_file(layout, record_path)
    expected = {
        "schema": PARTIAL_DOWNLOAD_SCHEMA,
        "artifact_id": item["artifact_id"],
        "url": item["url"],
        "expected_size": item["expected_size"],
        "expected_etag": item["expected_etag"],
        "expected_sha256": item["expected_sha256"],
        "bytes_present": info.st_size,
        "partial_sha256": partial_sha256,
        "resume_identity": plan.document["resume_identity"],
    }
    if record != expected:
        raise Music3StageBuilderSecurityError("partial download identity is stale or ambiguous")
    return expected


def validate_music3_resume_record(
    plan: Music3StagePlan,
    record: Mapping[str, object],
) -> dict[str, object]:
    """Validate an in-memory crash-recovery record against one exact plan."""

    value = _exact_keys(
        record,
        {
            "schema",
            "resume_identity",
            "plan_sha256",
            "phase",
            "generation_id",
            "final_generation_path",
            "reviewed_manifest_sha256",
            "runtime_stage_manifest_sha256",
        },
        field="resume record",
    )
    expected = {
        "schema": RESUME_SCHEMA,
        "resume_identity": plan.document.get("resume_identity"),
        "plan_sha256": plan.sha256,
        "phase": value.get("phase"),
        "generation_id": plan.document.get("generation_id"),
        "final_generation_path": plan.document.get("final_generation_path"),
        "reviewed_manifest_sha256": plan.document.get("reviewed_manifest_sha256"),
        "runtime_stage_manifest_sha256": plan.document.get("runtime_stage_manifest_sha256"),
    }
    if value.get("phase") not in _RESUME_PHASES or dict(value) != expected:
        raise Music3StageBuilderSecurityError("resume record does not match the reviewed plan")
    return expected


def load_music3_resume_record(
    plan: Music3StagePlan,
    record_path: str | os.PathLike[str],
) -> dict[str, object]:
    if Path(record_path) != Path(str(plan.document.get("resume_record_path"))):
        raise Music3StageBuilderSecurityError("resume record path is not exact")
    layout = _layout_from_plan(plan)
    return validate_music3_resume_record(plan, _load_json_file(layout, record_path))


def music3_stage_recovery_status(
    plan: Music3StagePlan,
    record: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Classify crash-window state without deleting, moving, or publishing bytes."""

    if not plan.ready:
        raise Music3StageBuilderBlocked("stage plan is not ready")
    layout = _layout_from_plan(plan)
    generation = Path(str(plan.document["final_generation_path"]))
    if layout.current_marker.exists() or layout.current_marker.is_symlink():
        try:
            _runtime_plan, current = runtime._load_current_plan(layout.pinokio_root)
        except runtime.Music3RuntimeError as error:
            raise Music3StageBuilderSecurityError(
                "runtime generation ownership marker is invalid"
            ) from error
        for key in ("current", "previous"):
            descriptor = current.get(key)
            if type(descriptor) is dict and descriptor.get("path") == str(generation):
                raise Music3StageBuilderBlocked("generation is already runtime-owned")
    generation_present = generation.exists() or generation.is_symlink()
    if generation_present:
        try:
            _assert_runtime_child(layout, generation)
            runtime._existing_path_is_safe(generation, directory=True)
            with runtime._generation_lock(layout, generation, exclusive=False):
                runtime._safe_tree(
                    generation,
                    expected_device=layout.pinokio_root.stat().st_dev,
                )
        except Music3StageBuilderError:
            raise
        except runtime.Music3RuntimeError as error:
            raise Music3StageBuilderSecurityError(
                "final generation is not safe for recovery"
            ) from error
    if record is None:
        if generation_present:
            raise Music3StageBuilderSecurityError(
                "unbound final-generation bytes require explicit recovery review"
            )
        return {
            "state": "fresh",
            "resume_identity": plan.document["resume_identity"],
            "next_phase": "fetching",
            "mutation": False,
        }
    validated = validate_music3_resume_record(plan, record)
    phase = str(validated["phase"])
    if phase in {"fetching", "fetched"} and generation_present:
        raise Music3StageBuilderSecurityError(
            "pre-stage resume state overlaps final-generation bytes"
        )
    if phase == "staged" and not generation_present:
        raise Music3StageBuilderSecurityError("staged resume state has no final generation")
    return {
        "state": f"resume_{phase}",
        "resume_identity": plan.document["resume_identity"],
        "next_phase": {
            "fetching": "fetching",
            "fetched": "staging",
            "staging": "staging",
            "staged": "verify-stage",
        }[phase],
        "mutation": False,
    }


def verify_music3_offline_stage(
    plan: Music3StagePlan,
    *,
    expected_stage_manifest_sha256: object,
    ucx_probe_output: bytes,
) -> dict[str, object]:
    """Verify staged bytes using already-captured UCX evidence; execute nothing."""

    expected = _digest(
        expected_stage_manifest_sha256,
        field="independently reviewed runtime stage manifest digest",
    )
    planned = str(plan.document.get("runtime_stage_manifest_sha256", ""))
    if not _constant_time_equal(expected, planned):
        raise Music3StageBuilderSecurityError(
            "runtime stage manifest digest was not independently approved"
        )
    if type(ucx_probe_output) is not bytes or len(ucx_probe_output) > runtime.MAX_JSON_BYTES:
        raise Music3StageBuilderSecurityError("UCX probe evidence is invalid")
    root = Path(str(plan.document["final_generation_path"]))
    provision_plan = runtime._build_plan(
        _layout_from_plan(plan).pinokio_root,
        runtime_source_revision=runtime.PINNED_SGLANG_SOURCE_REVISION,
        ucx_version=runtime.PINNED_UCX_VERSION,
        ucx_source_revision=runtime.PINNED_UCX_SOURCE_REVISION,
        check_free_space=False,
    )
    with runtime._generation_lock(provision_plan.layout, root, exclusive=False):
        document, actual = runtime._validate_stage(
            provision_plan,
            root,
            expected_stage_manifest_sha256=expected,
            ucx_probe=lambda _path: ucx_probe_output,
        )
        expected_device = provision_plan.layout.pinokio_root.stat().st_dev
        observed_tree_bytes = {
            "source_tree_bytes": _tree_total_bytes(
                root / "source", expected_device=expected_device,
            ),
            "model_tree_bytes": _tree_total_bytes(
                root / "model", expected_device=expected_device,
            ),
            "installed_environment_bytes": _tree_total_bytes(
                root / "env", expected_device=expected_device,
            ),
        }
        budgets = plan.document["disk_budget"]
        if any(observed > int(budgets[key]) for key, observed in observed_tree_bytes.items()):
            raise Music3StageBuilderSecurityError("staged tree exceeds its reviewed disk budget")
    if document != plan.document["runtime_stage_manifest"] or actual != planned:
        raise Music3StageBuilderSecurityError("offline stage differs from the reviewed plan")
    return {
        "verified": True,
        "network_used": False,
        "published": False,
        "runtime_stage_manifest_sha256": actual,
        "resume_identity": plan.document["resume_identity"],
        "observed_tree_bytes": observed_tree_bytes,
    }


__all__ = [
    "BUILDER_INPUT_SCHEMA",
    "BUILDER_PLAN_SCHEMA",
    "PARTIAL_DOWNLOAD_SCHEMA",
    "RESUME_SCHEMA",
    "Music3StageBuilderBlocked",
    "Music3StageBuilderError",
    "Music3StageBuilderSecurityError",
    "Music3StagePlan",
    "blocked_music3_stage_plan",
    "build_music3_stage_plan",
    "load_music3_resume_record",
    "load_reviewed_music3_build_input",
    "music3_stage_recovery_status",
    "validate_music3_partial_download",
    "validate_music3_resume_record",
    "verify_music3_download_cache",
    "verify_music3_offline_stage",
]
