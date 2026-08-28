"""Bounded wheel staging for the isolated H3 prompt-rewriter runtime.

The public plan is path-free and side-effect free.  Explicit execution accepts
an independently reviewed, canonical resolution report, validates the complete
dependency graph before mutation, then downloads exactly one pinned wheel per
resource-limited subprocess.  This module never installs or authorizes runtime
execution.
"""

from __future__ import annotations

import ctypes
import email.parser
import hashlib
import hmac
import json
import os
import re
import resource
import signal
import stat
import subprocess
import time
import urllib.parse
import zipfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from services import h3_prompt_rewriter_dependency_closure as closure

WHEEL_RESOLUTION_PLAN_SCHEMA = "maestro.h3-prompt-rewriter.wheel-plan.v2"
WHEEL_RESOLUTION_REPORT_SCHEMA = "maestro.h3-prompt-rewriter.wheel-report.v1"
WHEEL_MANIFEST_SCHEMA = "maestro.h3-prompt-rewriter.wheel-manifest.v2"
WHEEL_STATE_SCHEMA = "maestro.h3-prompt-rewriter.wheel-state.v2"
PYTORCH_INDEX = "https://download.pytorch.org/whl/cu128"
PYPI_INDEX = "https://pypi.org/simple"
MANIFEST_NAME = "wheel-manifest.json"
DEFAULT_BYTE_CAP = 8 * 1024**3
MAX_BYTE_CAP = 8 * 1024**3
DEFAULT_DEADLINE_SECONDS = 35 * 60
MAX_DEADLINE_SECONDS = 35 * 60
MAX_REPORT_BYTES = 512 * 1024
MAX_RSS_BYTES = 1536 * 1024**2
MAX_CPU_CORES = 2
PROCESS_NICE = 15
IONICE_CLASS = "idle"

_SHA256 = re.compile(r"[0-9a-f]{64}")
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,127}")
_REPORT_KEYS = {"schema", "target", "root_requirements", "packages"}
_PACKAGE_KEYS = {"name", "version", "requirement", "dependencies", "wheel"}
_WHEEL_KEYS = {"filename", "size_bytes", "sha256", "index", "source_url"}
_STATE_KEYS = {"schema", "plan_sha256", "report_sha256", "verified"}
_ALLOWED_STAGE = {".partial", ".wheel-manifest.json.tmp", "wheels", MANIFEST_NAME}
_ALLOWED_PARTIAL = {
    ".state.json.tmp",
    "attempts",
    "execution.lock",
    "home",
    "state.json",
    "tmp",
}
_TARGET = {
    "python_implementation": "cpython",
    "python_version": "3.12",
    "python_abi": "cp312",
    "platform": "manylinux_2_28_x86_64",
    "binary_wheels_only": True,
}


class H3PromptRewriterWheelResolverError(RuntimeError):
    """The wheel-resolution contract could not be satisfied."""


class H3PromptRewriterWheelResolverSecurityError(H3PromptRewriterWheelResolverError):
    """A private-path, report, artifact, or integrity boundary failed."""


class H3PromptRewriterWheelResolverExecutionError(H3PromptRewriterWheelResolverError):
    """Content-free execution failure; private partial state is preserved."""


@dataclass(frozen=True, slots=True, init=False)
class H3PromptRewriterWheelResolutionPlan:
    _encoded: bytes

    @classmethod
    def _from_document(
        cls, document: dict[str, object]
    ) -> H3PromptRewriterWheelResolutionPlan:
        value = object.__new__(cls)
        object.__setattr__(value, "_encoded", _canonical_json(document))
        return value

    @property
    def document(self) -> dict[str, object]:
        return json.loads(self._encoded.decode("ascii"))

    def to_mapping(self) -> dict[str, object]:
        return self.document

    @property
    def sha256(self) -> str:
        return _sha256_bytes(self._encoded)


@dataclass(frozen=True, slots=True)
class _Report:
    payload: bytes
    sha256: str
    document: dict[str, object]
    packages: tuple[dict[str, object], ...]
    closure_input: dict[str, object]
    closure_plan_sha256: str
    inventory_sha256: str


@dataclass(frozen=True, slots=True)
class _WheelBytes:
    filename: str
    name: str
    version: str
    size_bytes: int
    sha256: str
    dependencies: tuple[str, ...]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalize_name(value: str) -> str:
    normalized = re.sub(r"[-_.]+", "-", value).casefold()
    if _NAME.fullmatch(normalized) is None:
        raise H3PromptRewriterWheelResolverSecurityError("package identity is invalid")
    return normalized


def _load_packaging() -> tuple[type, type]:
    try:
        from packaging.requirements import Requirement
        from packaging.version import Version
    except ImportError:
        try:
            from pip._vendor.packaging.requirements import Requirement
            from pip._vendor.packaging.version import Version
        except ImportError as error:
            raise H3PromptRewriterWheelResolverExecutionError(
                "wheel metadata parser is unavailable"
            ) from error
    return Requirement, Version


def build_h3_prompt_rewriter_wheel_resolution_plan(
    *,
    byte_cap: object = DEFAULT_BYTE_CAP,
    deadline_seconds: object = DEFAULT_DEADLINE_SECONDS,
) -> H3PromptRewriterWheelResolutionPlan:
    """Return a deterministic plan without filesystem, process, or network I/O."""

    if type(byte_cap) is not int or not 1 <= byte_cap <= MAX_BYTE_CAP:
        raise H3PromptRewriterWheelResolverError(
            "wheel byte cap must be positive and no larger than 8 GiB"
        )
    if (
        type(deadline_seconds) is not int
        or not 1 <= deadline_seconds <= MAX_DEADLINE_SECONDS
    ):
        raise H3PromptRewriterWheelResolverError(
            "wheel deadline must be within the reviewed bound"
        )
    document = {
        "schema": WHEEL_RESOLUTION_PLAN_SCHEMA,
        "mutation": False,
        "execution_requires_explicit_flag": True,
        "installation_authorized": False,
        "runtime_execution_authorized": False,
        "target": dict(_TARGET),
        "byte_cap": byte_cap,
        "deadline_seconds": deadline_seconds,
        "deadline_semantics": "monotonic_hard_ceiling_with_process_group_cleanup",
        "in_flight_wheel_killed_at_deadline": True,
        "subprocess_concurrency": 1,
        "wheel_per_subprocess": 1,
        "resolution_report": {
            "schema": WHEEL_RESOLUTION_REPORT_SCHEMA,
            "canonical_bytes_required": True,
            "exact_expected_sha256_required": True,
            "complete_graph_required_before_download": True,
            "exact_source_url_and_size_required": True,
            "producer_included": False,
            "live_execution_gate": "no_go_until_separate_pinned_uv_report_wave",
        },
        "sources": [
            {
                "index": PYTORCH_INDEX,
                "package_scope": "torch_torchvision_and_nvidia_dependencies",
            },
            {
                "index": PYPI_INDEX,
                "package_scope": "remaining_roots_and_non_nvidia_dependencies",
            },
        ],
        "pip": {
            "isolated": True,
            "config_file": "disabled",
            "environment_inheritance": False,
            "dependencies_per_invocation": False,
            "extra_index": False,
            "find_links": False,
            "index_resolution": False,
            "exact_reviewed_source_url_is_download_target": True,
            "source_build": False,
        },
        "resource_limits": {
            "nice": PROCESS_NICE,
            "ionice": IONICE_CLASS,
            "cpu_cores": MAX_CPU_CORES,
            "address_space_bytes": MAX_RSS_BYTES,
            "rss_bytes": MAX_RSS_BYTES,
            "wheel_file_size_bound_by_report": True,
            "child_umask": "0077",
            "parent_limits_applied_before_report_hash": True,
        },
        "staging": {
            "absolute_private_feature_root_required": True,
            "dedicated_empty_or_resumable_root_required": True,
            "verified_wheel_state_incremental": True,
            "interrupted_complete_wheel_reconciled": True,
            "ambiguous_leftover_requires_owner_review": True,
            "atomic_private_manifest": True,
        },
    }
    encoded = _canonical_json(document).decode("ascii")
    if "/home/" in encoded or "/mnt/" in encoded or "\\" in encoded:
        raise AssertionError("public wheel plan contains a private path")
    return H3PromptRewriterWheelResolutionPlan._from_document(document)


def _exact_mapping(value: object, keys: set[str], field: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise H3PromptRewriterWheelResolverSecurityError(f"{field} is invalid")
    return value


def _bounded_plain_json(value: object) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > closure.MAX_JSON_NODES or depth > closure.MAX_JSON_DEPTH:
            raise H3PromptRewriterWheelResolverSecurityError(
                "resolution report exceeds its structure bound"
            )
        if item is None or type(item) in (bool, int, str):
            continue
        if type(item) is list:
            stack.extend((child, depth + 1) for child in item)
            continue
        if type(item) is dict and all(type(key) is str for key in item):
            stack.extend((child, depth + 1) for child in item.values())
            continue
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report contains an invalid JSON value"
        )


def _source_url(filename: str, index: str, value: object) -> str:
    if type(value) is not str or len(value) > 2048:
        raise H3PromptRewriterWheelResolverSecurityError("wheel source URL is invalid")
    if (
        not value.startswith("https://")
        or re.search(r"%(?:2f|5c|25)", value, re.IGNORECASE) is not None
        or "\\" in value
    ):
        raise H3PromptRewriterWheelResolverSecurityError("wheel source URL is invalid")
    try:
        parsed = urllib.parse.urlsplit(value)
        explicit_port = parsed.port
        decoded_path = urllib.parse.unquote(parsed.path, errors="strict")
    except (UnicodeError, ValueError) as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "wheel source URL is invalid"
        ) from error
    if not decoded_path.isascii() or "\\" in decoded_path:
        raise H3PromptRewriterWheelResolverSecurityError("wheel source URL is invalid")
    components = decoded_path.split("/")
    canonical_path = urllib.parse.quote(decoded_path, safe="/-._~")
    if (
        parsed.scheme != "https"
        or explicit_port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != canonical_path
        or not components
        or components[0] != ""
        or any(
            not component or component in {".", ".."} for component in components[1:]
        )
        or components[-1] != filename
    ):
        raise H3PromptRewriterWheelResolverSecurityError("wheel source URL is invalid")
    if index == PYTORCH_INDEX:
        expected_host = "download.pytorch.org"
        valid = parsed.netloc == expected_host and decoded_path.startswith(
            "/whl/cu128/"
        )
    else:
        expected_host = "files.pythonhosted.org"
        valid = parsed.netloc == expected_host and decoded_path.startswith("/packages/")
    if not valid:
        raise H3PromptRewriterWheelResolverSecurityError(
            "wheel source provenance contradicts its assigned index"
        )
    return value


def _load_report(payload: object, expected_sha256: object) -> _Report:
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_REPORT_BYTES:
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report bytes are outside their bound"
        )
    if type(expected_sha256) is not str or _SHA256.fullmatch(expected_sha256) is None:
        raise H3PromptRewriterWheelResolverSecurityError(
            "expected resolution report SHA-256 is invalid"
        )
    report_sha = _sha256_bytes(payload)
    if not hmac.compare_digest(report_sha, expected_sha256):
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report does not match its expected SHA-256"
        )
    try:
        value = json.loads(payload.decode("ascii"))
        _bounded_plain_json(value)
        canonical_payload = _canonical_json(value) + b"\n"
    except (RecursionError, UnicodeError, ValueError) as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report is invalid"
        ) from error
    if payload != canonical_payload:
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report is not canonical"
        )
    document = _exact_mapping(value, _REPORT_KEYS, "resolution report")
    if (
        document["schema"] != WHEEL_RESOLUTION_REPORT_SCHEMA
        or document["target"] != _TARGET
    ):
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report target is invalid"
        )
    if document["root_requirements"] != list(closure.ROOT_REQUIREMENTS):
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report roots are invalid"
        )
    raw_packages = document["packages"]
    if (
        type(raw_packages) is not list
        or not 1 <= len(raw_packages) <= closure.MAX_PACKAGES
    ):
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report package inventory is invalid"
        )
    packages: list[dict[str, object]] = []
    closure_packages: list[dict[str, object]] = []
    names: list[str] = []
    total = 0
    for raw in raw_packages:
        item = _exact_mapping(raw, _PACKAGE_KEYS, "resolution package")
        name = item["name"]
        version = item["version"]
        requirement = item["requirement"]
        dependencies = item["dependencies"]
        if (
            type(name) is not str
            or _normalize_name(name) != name
            or type(version) is not str
            or requirement != f"{name}=={version}"
            or type(dependencies) is not list
            or any(type(dep) is not str for dep in dependencies)
        ):
            raise H3PromptRewriterWheelResolverSecurityError(
                "resolution package identity or dependencies are invalid"
            )
        if dependencies != sorted(set(dependencies)):
            raise H3PromptRewriterWheelResolverSecurityError(
                "resolution package dependencies are not canonical"
            )
        _Requirement, Version = _load_packaging()
        try:
            parsed_version = Version(version)
        except Exception as error:
            raise H3PromptRewriterWheelResolverSecurityError(
                "resolution package version is invalid"
            ) from error
        if parsed_version.is_prerelease or parsed_version.is_devrelease:
            raise H3PromptRewriterWheelResolverSecurityError(
                "pre-release wheels are forbidden"
            )
        wheel = _exact_mapping(item["wheel"], _WHEEL_KEYS, "resolution wheel")
        filename = wheel["filename"]
        size = wheel["size_bytes"]
        digest = wheel["sha256"]
        index = wheel["index"]
        expected_index = (
            PYTORCH_INDEX
            if name in {"torch", "torchvision"} or name.startswith("nvidia-")
            else PYPI_INDEX
        )
        if (
            type(filename) is not str
            or type(size) is not int
            or not 1 <= size <= MAX_BYTE_CAP
            or type(digest) is not str
            or _SHA256.fullmatch(digest) is None
            or index != expected_index
        ):
            raise H3PromptRewriterWheelResolverSecurityError(
                "resolution wheel evidence is invalid"
            )
        _source_url(filename, index, wheel["source_url"])
        total += size
        names.append(name)
        packages.append(dict(item))
        closure_packages.append(
            {
                "name": name,
                "version": version,
                "requirement": requirement,
                "dependencies": list(dependencies),
                "dependency_metadata_complete": True,
                "wheel_candidates": [
                    {
                        "wheel_name": filename,
                        "sha256": digest,
                        "size_bytes": size,
                        "provenance": "unreviewed_candidate",
                    }
                ],
            }
        )
    if names != sorted(names) or len(names) != len(set(names)):
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution packages are not unique canonical order"
        )
    closure_input = {
        "schema": closure.DEPENDENCY_INPUT_SCHEMA,
        "runtime_target": dict(closure.RUNTIME_TARGET),
        "model_receipt_dependencies": {
            "adapter": closure.rewriter.adapter_descriptor(),
            "base": closure.rewriter.base_descriptor(),
        },
        "root_requirements": list(closure.ROOT_REQUIREMENTS),
        "packages": closure_packages,
        "resolution_claim": {
            "transitive_complete": False,
            "resolver": None,
            "resolver_version": None,
            "resolver_report_sha256": None,
            "resolver_inventory_sha256": None,
            "offline_replay_sha256": None,
            "offline_replay_inventory_sha256": None,
        },
    }
    closure_payload = _canonical_json(closure_input) + b"\n"
    try:
        closure_plan = closure.build_h3_prompt_rewriter_dependency_closure_plan(
            closure_payload
        )
        bound = closure.build_h3_prompt_rewriter_dependency_closure_plan(
            closure_payload,
            expected_input_sha256=closure_plan.document["input_sha256"],
        )
    except closure.H3PromptRewriterDependencyClosureError as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report dependency closure was rejected"
        ) from error
    return _Report(
        payload=payload,
        sha256=report_sha,
        document=dict(document),
        packages=tuple(packages),
        closure_input=closure_input,
        closure_plan_sha256=bound.sha256,
        inventory_sha256=bound.document["environment_candidates"]["inventory_sha256"],
    )


def _private_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "private staging directory is unavailable"
        ) from error
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise H3PromptRewriterWheelResolverSecurityError(
            "private staging directory identity or mode is invalid"
        )


def _private_file(path: Path, maximum: int) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "private staging file is unavailable"
        ) from error
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or not 1 <= info.st_size <= maximum
    ):
        raise H3PromptRewriterWheelResolverSecurityError(
            "private staging file identity, mode, or size is invalid"
        )
    return info


def _mkdir(path: Path) -> None:
    if path.exists():
        _private_directory(path)
        return
    path.mkdir(mode=0o700)
    _private_directory(path)


def _layout(
    feature_value: object, stage_value: object
) -> tuple[Path, Path, Path, Path]:
    if not isinstance(feature_value, (str, os.PathLike)) or not isinstance(
        stage_value, (str, os.PathLike)
    ):
        raise H3PromptRewriterWheelResolverSecurityError(
            "private roots must be absolute paths"
        )
    feature = Path(feature_value)
    stage = Path(stage_value)
    if not feature.is_absolute() or not stage.is_absolute():
        raise H3PromptRewriterWheelResolverSecurityError(
            "private roots must be absolute paths"
        )
    try:
        resolved_feature = feature.resolve(strict=True)
        resolved_parent = stage.parent.resolve(strict=True)
    except OSError as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "private roots cannot be resolved"
        ) from error
    if feature != resolved_feature or (
        resolved_parent != resolved_feature
        and resolved_feature not in resolved_parent.parents
    ):
        raise H3PromptRewriterWheelResolverSecurityError(
            "staging root must be beneath the private feature root"
        )
    _private_directory(feature)
    _mkdir(stage)
    if stage.resolve(strict=True) != stage:
        raise H3PromptRewriterWheelResolverSecurityError(
            "staging root must not traverse links"
        )
    names = {item.name for item in os.scandir(stage)}
    if not names <= _ALLOWED_STAGE:
        raise H3PromptRewriterWheelResolverSecurityError(
            "staging root contains foreign files"
        )
    partial = stage / ".partial"
    wheels = stage / "wheels"
    for directory in (
        partial,
        partial / "home",
        partial / "tmp",
        partial / "attempts",
        wheels,
    ):
        _mkdir(directory)
    partial_names = {item.name for item in os.scandir(partial)}
    if not partial_names <= _ALLOWED_PARTIAL:
        raise H3PromptRewriterWheelResolverSecurityError(
            "partial state contains foreign files"
        )
    return stage, partial, wheels, stage / MANIFEST_NAME


def _executable(value: object) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not Path(value).is_absolute():
        raise H3PromptRewriterWheelResolverSecurityError(
            "caller executable must be absolute"
        )
    try:
        path = Path(value).resolve(strict=True)
        info = path.stat()
    except OSError as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "caller executable is unavailable"
        ) from error
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(path, os.X_OK)
    ):
        raise H3PromptRewriterWheelResolverSecurityError(
            "caller executable identity or mode is invalid"
        )
    return path


def _pip_prefix(
    python_executable: object | None, pip_executable: object | None
) -> tuple[str, ...]:
    if (python_executable is None) == (pip_executable is None):
        raise H3PromptRewriterWheelResolverSecurityError(
            "exactly one Python or pip executable is required"
        )
    if python_executable is not None:
        return (str(_executable(python_executable)), "-m", "pip")
    return (str(_executable(pip_executable)),)


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        _private_file(temporary, max(len(payload), 1) + 4096)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise H3PromptRewriterWheelResolverSecurityError(
                "atomic temporary file identity is invalid"
            )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _private_file(temporary, max(len(payload), 1) + 4096)
    _replace_private(temporary, path, max(len(payload), 1) + 4096)


def _fsync_directory(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _replace_private(source: Path, destination: Path, maximum: int) -> None:
    _private_file(source, maximum)
    os.replace(source, destination)
    _fsync_directory(destination.parent)
    _private_file(destination, maximum)


def _load_state(partial: Path, plan_sha: str, report_sha: str) -> dict[str, object]:
    path = partial / "state.json"
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.is_symlink() or path.is_symlink():
        raise H3PromptRewriterWheelResolverSecurityError(
            "atomic state path must not be a symlink"
        )
    if temporary.exists():
        _private_file(temporary, 2 * 1024 * 1024)
        if path.exists():
            _private_file(path, 2 * 1024 * 1024)
            if temporary.read_bytes() != path.read_bytes():
                raise H3PromptRewriterWheelResolverSecurityError(
                    "ambiguous atomic state leftover requires owner-reviewed removal"
                )
            temporary.unlink()
            _fsync_directory(path.parent)
        else:
            _replace_private(temporary, path, 2 * 1024 * 1024)
    if not path.exists():
        return {
            "schema": WHEEL_STATE_SCHEMA,
            "plan_sha256": plan_sha,
            "report_sha256": report_sha,
            "verified": {},
        }
    _private_file(path, 2 * 1024 * 1024)
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("ascii"))
    except (OSError, UnicodeError, ValueError) as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "partial state is invalid"
        ) from error
    if (
        type(value) is not dict
        or set(value) != _STATE_KEYS
        or value["schema"] != WHEEL_STATE_SCHEMA
        or value["plan_sha256"] != plan_sha
        or value["report_sha256"] != report_sha
        or type(value["verified"]) is not dict
        or payload != _canonical_json(value) + b"\n"
    ):
        raise H3PromptRewriterWheelResolverSecurityError("partial state is invalid")
    return value


def _save_state(partial: Path, state: dict[str, object]) -> None:
    _atomic_write(partial / "state.json", _canonical_json(state) + b"\n")


def _hash_file(path: Path, maximum: int) -> tuple[int, str]:
    info = _private_file(path, maximum)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return info.st_size, digest.hexdigest()


def _wheel_bytes(
    path: Path,
    package: Mapping[str, object],
    selected_versions: Mapping[str, str],
) -> _WheelBytes:
    expected = package["wheel"]
    if path.name != expected["filename"]:
        raise H3PromptRewriterWheelResolverSecurityError(
            "downloaded wheel filename contradicts the report"
        )
    size, digest = _hash_file(path, expected["size_bytes"])
    if size != expected["size_bytes"] or digest != expected["sha256"]:
        raise H3PromptRewriterWheelResolverSecurityError(
            "downloaded wheel bytes contradict the report"
        )
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            metadata = [
                item
                for item in members
                if item.filename.count("/") == 1
                and item.filename.endswith(".dist-info/METADATA")
            ]
            if len(metadata) != 1 or metadata[0].file_size > 2 * 1024 * 1024:
                raise H3PromptRewriterWheelResolverSecurityError(
                    "wheel metadata inventory is invalid"
                )
            for member in members:
                if (
                    member.filename.startswith(("/", "\\"))
                    or "\\" in member.filename
                    or ".." in Path(member.filename).parts
                    or stat.S_ISLNK(member.external_attr >> 16)
                ):
                    raise H3PromptRewriterWheelResolverSecurityError(
                        "wheel archive contains an unsafe member"
                    )
            message = email.parser.BytesParser().parsebytes(archive.read(metadata[0]))
    except H3PromptRewriterWheelResolverError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "wheel archive is invalid"
        ) from error
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if (
        len(names) != 1
        or _normalize_name(names[0]) != package["name"]
        or len(versions) != 1
        or versions[0] != package["version"]
    ):
        raise H3PromptRewriterWheelResolverSecurityError(
            "wheel metadata identity contradicts the report"
        )
    Requirement, Version = _load_packaging()
    marker_environment = {
        "implementation_name": "cpython",
        "implementation_version": "3.12.14",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": "Linux",
        "platform_version": "",
        "python_full_version": "3.12.14",
        "python_version": "3.12",
        "sys_platform": "linux",
        "extra": "",
    }
    dependencies: list[str] = []
    try:
        requirements = [
            Requirement(value) for value in message.get_all("Requires-Dist", [])
        ]
    except Exception as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "wheel dependency metadata is invalid"
        ) from error
    for requirement in requirements:
        if requirement.url is not None:
            raise H3PromptRewriterWheelResolverSecurityError(
                "wheel metadata contains a direct URL dependency"
            )
        if requirement.marker is not None and not requirement.marker.evaluate(
            marker_environment
        ):
            continue
        name = _normalize_name(requirement.name)
        selected = selected_versions.get(name)
        if selected is None or (
            requirement.specifier
            and not requirement.specifier.contains(Version(selected), prereleases=False)
        ):
            raise H3PromptRewriterWheelResolverSecurityError(
                "wheel dependency metadata is unresolved"
            )
        dependencies.append(f"{name}=={selected}")
    dependencies = sorted(set(dependencies))
    if dependencies != package["dependencies"]:
        raise H3PromptRewriterWheelResolverSecurityError(
            "wheel resolved metadata contradicts the report"
        )
    return _WheelBytes(
        filename=path.name,
        name=package["name"],
        version=package["version"],
        size_bytes=size,
        sha256=digest,
        dependencies=tuple(dependencies),
    )


def _resource_support() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "setpriority")
        or not hasattr(os, "sched_setaffinity")
        or not hasattr(resource, "RLIMIT_AS")
    ):
        raise H3PromptRewriterWheelResolverExecutionError(
            "reviewed subprocess resource controls are unavailable"
        )


def _set_ioprio_idle() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(251, 1, 0, 3 << 13)
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, "ioprio_set failed")


def apply_h3_prompt_rewriter_parent_limits() -> None:
    """Apply the reviewed outer boundary before report reading or hashing."""

    _resource_support()
    os.setpriority(os.PRIO_PROCESS, 0, PROCESS_NICE)
    available = sorted(os.sched_getaffinity(0))
    if not available:
        raise H3PromptRewriterWheelResolverExecutionError(
            "reviewed parent CPU boundary is unavailable"
        )
    os.sched_setaffinity(0, set(available[:MAX_CPU_CORES]))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_RSS_BYTES, MAX_RSS_BYTES))
    if hasattr(resource, "RLIMIT_RSS"):
        resource.setrlimit(resource.RLIMIT_RSS, (MAX_RSS_BYTES, MAX_RSS_BYTES))
    try:
        _set_ioprio_idle()
    except OSError as error:
        raise H3PromptRewriterWheelResolverExecutionError(
            "reviewed parent I/O boundary is unavailable"
        ) from error


def _apply_child_limits(maximum_file_bytes: int) -> None:
    """Child-only resource boundary; an error aborts subprocess creation."""

    os.umask(0o077)
    os.setpriority(os.PRIO_PROCESS, 0, PROCESS_NICE)
    available = sorted(os.sched_getaffinity(0))
    if not available:
        raise OSError("CPU affinity is unavailable")
    os.sched_setaffinity(0, set(available[:MAX_CPU_CORES]))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_RSS_BYTES, MAX_RSS_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (maximum_file_bytes, maximum_file_bytes))
    if hasattr(resource, "RLIMIT_RSS"):
        resource.setrlimit(resource.RLIMIT_RSS, (MAX_RSS_BYTES, MAX_RSS_BYTES))
    _set_ioprio_idle()


def _child_limit_callback(maximum_file_bytes: int) -> Callable[[], None]:
    def apply() -> None:
        _apply_child_limits(maximum_file_bytes)

    apply.__name__ = "_apply_child_limits"
    return apply


def _download_command(
    prefix: tuple[str, ...], package: Mapping[str, object], destination: Path
) -> list[str]:
    return [
        *prefix,
        "--isolated",
        "download",
        "--disable-pip-version-check",
        "--no-input",
        "--no-cache-dir",
        "--only-binary=:all:",
        "--platform",
        "manylinux_2_28_x86_64",
        "--python-version",
        "3.12",
        "--implementation",
        "cp",
        "--abi",
        "cp312",
        "--no-index",
        "--dest",
        str(destination),
        "--no-deps",
        package["wheel"]["source_url"],
    ]


def _environment(prefix: tuple[str, ...], partial: Path) -> dict[str, str]:
    return {
        "PATH": os.pathsep.join(
            dict.fromkeys((str(Path(prefix[0]).parent), "/usr/bin", "/bin"))
        ),
        "HOME": str(partial / "home"),
        "TMPDIR": str(partial / "tmp"),
        "PIP_CONFIG_FILE": os.devnull,
    }


def _run_one(
    command: list[str],
    *,
    environment: Mapping[str, str],
    process_factory: Callable[..., object],
    maximum_file_bytes: int,
    timeout_seconds: float,
    kill_process_group: Callable[[int, int], None],
    sleep: Callable[[float], None],
) -> None:
    try:
        process = process_factory(
            command,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            preexec_fn=_child_limit_callback(maximum_file_bytes),
            start_new_session=True,
            close_fds=True,
        )
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        raise H3PromptRewriterWheelResolverExecutionError(
            "wheel download subprocess failed"
        ) from error
    try:
        process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        try:
            kill_process_group(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            kill_process_group(process.pid, 0)
            group_alive = True
        except ProcessLookupError:
            group_alive = False
        except OSError as probe_error:
            raise H3PromptRewriterWheelResolverExecutionError(
                "timed out wheel process group could not be probed"
            ) from probe_error
        if group_alive:
            try:
                kill_process_group(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if getattr(process, "returncode", None) is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as cleanup_error:
                raise H3PromptRewriterWheelResolverExecutionError(
                    "timed out wheel process group could not be reaped"
                ) from cleanup_error
        for _attempt in range(50):
            try:
                kill_process_group(process.pid, 0)
            except ProcessLookupError:
                break
            except OSError as probe_error:
                raise H3PromptRewriterWheelResolverExecutionError(
                    "timed out wheel process group could not be probed"
                ) from probe_error
            sleep(0.1)
        else:
            raise H3PromptRewriterWheelResolverExecutionError(
                "timed out wheel process group survived cleanup"
            )
        raise H3PromptRewriterWheelResolverExecutionError(
            "wheel download reached the hard deadline"
        ) from error
    if type(getattr(process, "returncode", None)) is not int or process.returncode != 0:
        raise H3PromptRewriterWheelResolverExecutionError(
            "wheel download subprocess failed"
        )


@contextmanager
def _execution_lock(partial: Path):
    try:
        import fcntl

        descriptor = os.open(
            partial / "execution.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise OSError("invalid execution lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError) as error:
        if "descriptor" in locals():
            os.close(descriptor)
        raise H3PromptRewriterWheelResolverExecutionError(
            "another wheel staging execution may be active"
        ) from error
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _verified_record(
    wheel: _WheelBytes, package: Mapping[str, object]
) -> dict[str, object]:
    return {
        "filename": wheel.filename,
        "size_bytes": wheel.size_bytes,
        "sha256": wheel.sha256,
        "index": package["wheel"]["index"],
        "source_url": package["wheel"]["source_url"],
    }


def _verify_record(
    wheels: Path,
    package: Mapping[str, object],
    raw: object,
    selected_versions: Mapping[str, str],
) -> _WheelBytes:
    if type(raw) is not dict or set(raw) != {
        "filename",
        "size_bytes",
        "sha256",
        "index",
        "source_url",
    }:
        raise H3PromptRewriterWheelResolverSecurityError(
            "verified wheel state is invalid"
        )
    expected = package["wheel"]
    if raw != {
        "filename": expected["filename"],
        "size_bytes": expected["size_bytes"],
        "sha256": expected["sha256"],
        "index": expected["index"],
        "source_url": expected["source_url"],
    }:
        raise H3PromptRewriterWheelResolverSecurityError(
            "verified wheel state contradicts the report"
        )
    return _wheel_bytes(wheels / expected["filename"], package, selected_versions)


def _attempt_wheel(
    attempt: Path,
    package: Mapping[str, object],
    selected_versions: Mapping[str, str],
) -> Path | None:
    _mkdir(attempt)
    entries = list(os.scandir(attempt))
    if not entries:
        return None
    if len(entries) != 1 or entries[0].name != package["wheel"]["filename"]:
        raise H3PromptRewriterWheelResolverSecurityError(
            "ambiguous interrupted wheel output requires owner-reviewed removal"
        )
    path = Path(entries[0].path)
    _wheel_bytes(path, package, selected_versions)
    return path


def _check_deadline(now: Callable[[], float], deadline: float) -> None:
    if now() >= deadline:
        raise H3PromptRewriterWheelResolverExecutionError(
            "wheel staging deadline was reached between wheels"
        )


def _manifest(
    plan: H3PromptRewriterWheelResolutionPlan,
    report: _Report,
    verified: Mapping[str, object],
) -> dict[str, object]:
    wheels = []
    total = 0
    for package in report.packages:
        record = verified[package["name"]]
        total += record["size_bytes"]
        wheels.append(
            {
                "name": package["name"],
                "version": package["version"],
                **record,
                "provenance": "sha_bound_reviewed_resolution_report",
            }
        )
    return {
        "schema": WHEEL_MANIFEST_SCHEMA,
        "plan_sha256": plan.sha256,
        "resolution_report_sha256": report.sha256,
        "target": plan.document["target"],
        "byte_cap": plan.document["byte_cap"],
        "deadline_seconds": plan.document["deadline_seconds"],
        "total_size_bytes": total,
        "wheel_count": len(wheels),
        "wheels": wheels,
        "dependency_input": report.closure_input,
        "dependency_inventory_sha256": report.inventory_sha256,
        "dependency_plan_sha256": report.closure_plan_sha256,
        "installation_authorized": False,
        "runtime_execution_authorized": False,
    }


def _publish_manifest(
    path: Path, payload: bytes, *, expected_existing: bool = False
) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.is_symlink() or path.is_symlink():
        raise H3PromptRewriterWheelResolverSecurityError(
            "atomic manifest path must not be a symlink"
        )
    if temporary.exists():
        _private_file(temporary, 4 * 1024 * 1024)
        if temporary.read_bytes() != payload:
            raise H3PromptRewriterWheelResolverSecurityError(
                "ambiguous atomic manifest leftover requires owner-reviewed removal"
            )
        if not path.exists():
            _replace_private(temporary, path, 4 * 1024 * 1024)
            return
    if expected_existing and path.exists():
        _private_file(path, 4 * 1024 * 1024)
        if path.read_bytes() != payload:
            raise H3PromptRewriterWheelResolverSecurityError(
                "existing manifest contradicts verified wheel state"
            )
        if temporary.exists():
            temporary.unlink()
            _fsync_directory(path.parent)
        return
    _atomic_write(path, payload)


def execute_h3_prompt_rewriter_wheel_resolution(
    plan: object,
    *,
    expected_plan_sha256: object,
    resolution_report_payload: object,
    expected_resolution_report_sha256: object,
    private_feature_root: object,
    staging_root: object,
    python_executable: object | None = None,
    pip_executable: object | None = None,
    process_factory: Callable[..., object] = subprocess.Popen,
    monotonic: Callable[[], float] = time.monotonic,
    apply_parent_limits: Callable[[], None] = (apply_h3_prompt_rewriter_parent_limits),
    kill_process_group: Callable[[int, int], None] = os.killpg,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Stage an exact report one wheel at a time after all preflight gates."""

    if type(plan) is not H3PromptRewriterWheelResolutionPlan:
        raise H3PromptRewriterWheelResolverError("wheel plan type is invalid")
    if (
        type(expected_plan_sha256) is not str
        or _SHA256.fullmatch(expected_plan_sha256) is None
        or not hmac.compare_digest(plan.sha256, expected_plan_sha256)
    ):
        raise H3PromptRewriterWheelResolverSecurityError(
            "wheel plan does not match its exact expected SHA-256"
        )
    try:
        apply_parent_limits()
    except H3PromptRewriterWheelResolverError:
        raise
    except (OSError, ValueError) as error:
        raise H3PromptRewriterWheelResolverExecutionError(
            "reviewed parent resource boundary could not be applied"
        ) from error
    deadline = monotonic() + plan.document["deadline_seconds"]
    try:
        report = _load_report(
            resolution_report_payload, expected_resolution_report_sha256
        )
    except H3PromptRewriterWheelResolverError:
        raise
    except (
        closure.H3PromptRewriterDependencyClosureError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise H3PromptRewriterWheelResolverSecurityError(
            "resolution report was rejected"
        ) from error
    report_total = sum(package["wheel"]["size_bytes"] for package in report.packages)
    if report_total > plan.document["byte_cap"]:
        raise H3PromptRewriterWheelResolverSecurityError(
            "reviewed wheel sizes exceed the byte cap before download"
        )
    _resource_support()
    prefix = _pip_prefix(python_executable, pip_executable)
    _stage, partial, wheels, manifest_path = _layout(private_feature_root, staging_root)
    environment = _environment(prefix, partial)
    selected_versions = {
        package["name"]: package["version"] for package in report.packages
    }
    with _execution_lock(partial):
        state = _load_state(partial, plan.sha256, report.sha256)
        verified = state["verified"]
        if set(verified) - set(selected_versions):
            raise H3PromptRewriterWheelResolverSecurityError(
                "partial state contains a foreign verified package"
            )
        report_names = set(selected_versions)
        attempts = partial / "attempts"
        for entry in os.scandir(attempts):
            if entry.name not in report_names:
                raise H3PromptRewriterWheelResolverSecurityError(
                    "partial attempts contain a foreign package"
                )
            _private_directory(Path(entry.path))
        expected_wheel_files = {
            record["filename"]
            for record in verified.values()
            if type(record) is dict and type(record.get("filename")) is str
        }
        actual_wheel_files = {entry.name for entry in os.scandir(wheels)}
        if actual_wheel_files != expected_wheel_files:
            raise H3PromptRewriterWheelResolverSecurityError(
                "wheel directory contradicts verified state"
            )
        verified_total = 0
        for package in report.packages:
            name = package["name"]
            if name in verified:
                wheel = _verify_record(
                    wheels, package, verified[name], selected_versions
                )
                verified_total += wheel.size_bytes
        if verified_total > plan.document["byte_cap"]:
            raise H3PromptRewriterWheelResolverSecurityError(
                "verified wheel state exceeds the byte cap"
            )

        for package in report.packages:
            name = package["name"]
            if name in verified:
                continue
            expected_size = package["wheel"]["size_bytes"]
            if verified_total + expected_size > plan.document["byte_cap"]:
                raise H3PromptRewriterWheelResolverSecurityError(
                    "next reviewed wheel exceeds the remaining byte cap"
                )
            _check_deadline(monotonic, deadline)
            attempt = partial / "attempts" / name
            candidate = _attempt_wheel(attempt, package, selected_versions)
            execution_error: H3PromptRewriterWheelResolverExecutionError | None = None
            if candidate is None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise H3PromptRewriterWheelResolverExecutionError(
                        "wheel staging reached the hard deadline"
                    )
                try:
                    _run_one(
                        _download_command(prefix, package, attempt),
                        environment=environment,
                        process_factory=process_factory,
                        maximum_file_bytes=expected_size,
                        timeout_seconds=remaining,
                        kill_process_group=kill_process_group,
                        sleep=sleep,
                    )
                except H3PromptRewriterWheelResolverExecutionError as error:
                    execution_error = error
                candidate = _attempt_wheel(attempt, package, selected_versions)
                if candidate is None:
                    if execution_error is not None:
                        raise execution_error
                    raise H3PromptRewriterWheelResolverSecurityError(
                        "wheel subprocess produced no exact artifact"
                    )
            destination = wheels / package["wheel"]["filename"]
            if destination.exists():
                raise H3PromptRewriterWheelResolverSecurityError(
                    "wheel destination collision detected"
                )
            wheel = _wheel_bytes(candidate, package, selected_versions)
            os.replace(candidate, destination)
            _wheel_bytes(destination, package, selected_versions)
            verified[name] = _verified_record(wheel, package)
            verified_total += wheel.size_bytes
            _save_state(partial, state)
            if execution_error is not None:
                raise execution_error
            _check_deadline(monotonic, deadline)

        if set(verified) != set(selected_versions):
            raise H3PromptRewriterWheelResolverSecurityError(
                "verified wheel inventory is incomplete"
            )
        manifest = _manifest(plan, report, verified)
        payload = _canonical_json(manifest) + b"\n"
        if manifest_path.exists():
            _publish_manifest(manifest_path, payload, expected_existing=True)
        else:
            _publish_manifest(manifest_path, payload)
        return manifest


__all__ = [
    "DEFAULT_BYTE_CAP",
    "DEFAULT_DEADLINE_SECONDS",
    "IONICE_CLASS",
    "MANIFEST_NAME",
    "MAX_BYTE_CAP",
    "MAX_CPU_CORES",
    "MAX_DEADLINE_SECONDS",
    "MAX_REPORT_BYTES",
    "MAX_RSS_BYTES",
    "PROCESS_NICE",
    "PYPI_INDEX",
    "PYTORCH_INDEX",
    "WHEEL_MANIFEST_SCHEMA",
    "WHEEL_RESOLUTION_PLAN_SCHEMA",
    "WHEEL_RESOLUTION_REPORT_SCHEMA",
    "H3PromptRewriterWheelResolutionPlan",
    "H3PromptRewriterWheelResolverError",
    "H3PromptRewriterWheelResolverExecutionError",
    "H3PromptRewriterWheelResolverSecurityError",
    "apply_h3_prompt_rewriter_parent_limits",
    "build_h3_prompt_rewriter_wheel_resolution_plan",
    "execute_h3_prompt_rewriter_wheel_resolution",
]
