"""Pinned-uv producer for an unreviewed H3 wheel resolution report.

Planning is deterministic and performs no network or filesystem mutation.
Execution is separately, hash-bound, writes only owner-private evidence, and
never downloads wheel bytes, installs packages, or authorizes runtime use.
"""

from __future__ import annotations

import ctypes
import fcntl
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
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 application environment
    import tomli as tomllib
from packaging.markers import InvalidMarker, Marker
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import (
    InvalidWheelFilename,
    canonicalize_name,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from services import h3_prompt_rewriter_dependency_closure as closure
from services import h3_prompt_rewriter_wheel_resolver as wheel_resolver

UV_RESOLUTION_PLAN_SCHEMA = "maestro.h3-prompt-rewriter.uv-resolution-plan.v6"
UV_RESOLUTION_PROVENANCE_SCHEMA = (
    "maestro.h3-prompt-rewriter.uv-resolution-provenance.v1"
)
UV_RESOLUTION_FAILURE_SCHEMA = "maestro.h3-prompt-rewriter.uv-resolution-failure.v1"
PINNED_UV_VERSION = "0.9.26"
PINNED_UV_SHA256 = "0650696de7f403348e9dd617e1f65dc32147c106c40129138017efd8f0f01cc8"
PINNED_UV_SIZE_BYTES = 56_224_064
PYLOCK_NAME = "pylock.toml"
PYLOCK_CANDIDATE_NAME = "pylock.candidate.toml"
HASHED_REQUIREMENTS_NAME = "requirements.hashed.txt"
HASHED_REQUIREMENTS_CANDIDATE_NAME = "requirements.hashed.candidate.txt"
REPORT_NAME = "wheel-report.json"
PROVENANCE_NAME = "resolution-provenance.json"
INPUT_NAME = "requirements.in"
FAILURE_NAME = "resolution-failure.json"
MAX_PYLOCK_BYTES = 8 * 1024 * 1024
MAX_HASHED_REQUIREMENTS_BYTES = 512 * 1024
MAX_TOTAL_WHEEL_BYTES = wheel_resolver.MAX_BYTE_CAP
MAX_DEADLINE_SECONDS = wheel_resolver.MAX_DEADLINE_SECONDS
DEFAULT_METADATA_BYTE_CAP = 1024**3
MAX_METADATA_BYTE_CAP = 1024**3
DEFAULT_METADATA_ENTRY_CAP = 50_000
MAX_METADATA_ENTRY_CAP = 50_000
MAX_CHILD_FILE_BYTES = 16 * 1024**2
MAX_UV_INTERNAL_LOCK_BYTES = 4 * 1024
POLL_SECONDS = 0.25
MAX_STATE_DEPTH = 32
MAX_SCAN_RETRIES = 3
MAX_UNATTESTED_STATE_SECONDS = 2
MAX_ADDRESS_SPACE_BYTES = 16 * 1024**3
MAX_RSS_BYTES = 1536 * 1024**2
MAX_PROC_PID_ENTRIES = 1_000_000
MAX_PROC_STAT_BYTES = 4096
MAX_PROC_STATUS_BYTES = 64 * 1024
MAX_CPU_CORES = wheel_resolver.MAX_CPU_CORES
PROCESS_NICE = wheel_resolver.PROCESS_NICE

_SHA256 = re.compile(r"[0-9a-f]{64}")
_UV_INDEX_ID = re.compile(r"[0-9a-f]{16}")
_UV_PACKAGE_COMPONENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_UV_VERSION_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}")
_UV_ARCHIVE_ID = re.compile(r"[A-Za-z0-9_-]{21}")
_ALLOWED_STATE_FILES = {
    INPUT_NAME,
    PYLOCK_NAME,
    REPORT_NAME,
    PROVENANCE_NAME,
    FAILURE_NAME,
    f".{INPUT_NAME}.tmp",
    PYLOCK_CANDIDATE_NAME,
    HASHED_REQUIREMENTS_NAME,
    HASHED_REQUIREMENTS_CANDIDATE_NAME,
    f".{REPORT_NAME}.tmp",
    f".{PROVENANCE_NAME}.tmp",
    f".{FAILURE_NAME}.tmp",
    "cache",
    "home",
    "tmp",
    "execution.lock",
}
_UV_INTERNAL_LOCK_PATHS = frozenset(
    {
        ("cache", ".lock"),
        (
            "home",
            ".local",
            "share",
            "uv",
            "credentials",
            "credentials.toml.lock",
        ),
    }
)
_BINARY_ROOTS = frozenset(
    {"pillow", "safetensors", "tokenizers", "torch", "torchvision"}
)
_TARGET = {
    "python_implementation": "cpython",
    "python_version": "3.12",
    "python_abi": "cp312",
    "platform": "manylinux_2_28_x86_64",
    "binary_wheels_only": True,
}

_MARKER_ENVIRONMENT = {
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
}


class H3PromptRewriterUvResolutionError(RuntimeError):
    """The pinned resolver-report contract could not be satisfied."""


class H3PromptRewriterUvResolutionSecurityError(H3PromptRewriterUvResolutionError):
    """An identity, graph, source, or private-state boundary failed."""


class H3PromptRewriterUvResolutionExecutionError(H3PromptRewriterUvResolutionError):
    """The resolver process failed without exposing private output."""


class _TransientStateChange(RuntimeError):
    """A bounded atomic rename or replacement requires a fresh tree scan."""


class _PrivateStateNotSettled(H3PromptRewriterUvResolutionSecurityError):
    """The private tree remained transient across the strict retry budget."""


@dataclass(frozen=True, slots=True, init=False)
class H3PromptRewriterUvResolutionPlan:
    _encoded: bytes

    @classmethod
    def _from_document(
        cls, document: dict[str, object]
    ) -> H3PromptRewriterUvResolutionPlan:
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
class _UvReceipt:
    path: Path
    sha256: str
    size_bytes: int
    stat_identity: tuple[int, int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _PythonReceipt:
    path: Path
    sha256: str
    size_bytes: int
    version: str
    stat_identity: tuple[int, int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _StateUsage:
    bytes: int
    entries: int


@dataclass(frozen=True, slots=True)
class _UvCacheSymlinkReceipt:
    relative: tuple[str, ...]
    identity: tuple[int, int, int, int, int]
    size_bytes: int
    target_text: str
    target_relative: tuple[str, ...]
    target_identity: tuple[int, int, int, int, int]


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


def _python_path_sha256(receipt: _PythonReceipt) -> str:
    return _sha256_bytes(str(receipt.path).encode("utf-8"))


def _python_stat_sha256(receipt: _PythonReceipt) -> str:
    return _sha256_bytes(_canonical_json(list(receipt.stat_identity)))


def reviewed_requirements_input_bytes() -> bytes:
    return ("\n".join(closure.ROOT_REQUIREMENTS) + "\n").encode("ascii")


def _digest(value: object, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise H3PromptRewriterUvResolutionSecurityError(
            f"{field} must be one lowercase SHA-256"
        )
    return value


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
    )


def _stable_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
    )


def _inspect_uv(value: object) -> _UvReceipt:
    if not isinstance(value, (str, os.PathLike)) or not Path(value).is_absolute():
        raise H3PromptRewriterUvResolutionSecurityError(
            "uv executable must be an absolute path"
        )
    path = Path(value)
    try:
        if path.resolve(strict=True) != path:
            raise H3PromptRewriterUvResolutionSecurityError(
                "uv executable must not traverse links"
            )
        before = path.lstat()
    except OSError as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "uv executable is unavailable"
        ) from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(path, os.X_OK)
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "uv executable identity or mode is invalid"
        )
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if _stat_identity(opened) != _stat_identity(before):
            raise H3PromptRewriterUvResolutionSecurityError(
                "uv executable identity changed"
            )
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    after = path.lstat()
    if _stat_identity(after) != _stat_identity(before):
        raise H3PromptRewriterUvResolutionSecurityError(
            "uv executable changed during hashing"
        )
    receipt = _UvReceipt(
        path=path,
        sha256=digest.hexdigest(),
        size_bytes=before.st_size,
        stat_identity=_stat_identity(before),
    )
    if receipt.sha256 != PINNED_UV_SHA256 or receipt.size_bytes != PINNED_UV_SIZE_BYTES:
        raise H3PromptRewriterUvResolutionSecurityError(
            "uv executable does not match the reviewed 0.9.26 artifact"
        )
    return receipt


def _inspect_python(value: object) -> _PythonReceipt:
    if not isinstance(value, (str, os.PathLike)) or not Path(value).is_absolute():
        raise H3PromptRewriterUvResolutionSecurityError(
            "bootstrap Python executable must be an absolute path"
        )
    path = Path(value)
    try:
        if path.resolve(strict=True) != path:
            raise H3PromptRewriterUvResolutionSecurityError(
                "bootstrap Python executable must not traverse links"
            )
        before = path.lstat()
    except OSError as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "bootstrap Python executable is unavailable"
        ) from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid not in {0, os.getuid()}
        or before.st_nlink != 1
        or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(path, os.X_OK)
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "bootstrap Python executable identity or mode is invalid"
        )
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if _stat_identity(opened) != _stat_identity(before):
            raise H3PromptRewriterUvResolutionSecurityError(
                "bootstrap Python executable identity changed"
            )
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            close_fds=True,
            env={"PYTHONNOUSERSITE": "1", "PYTHONPATH": ""},
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "bootstrap Python version could not be verified"
        ) from error
    output = completed.stdout + completed.stderr
    match = re.fullmatch(rb"Python (3\.12\.[0-9]+)\r?\n?", output)
    if completed.returncode != 0 or len(output) > 64 or match is None:
        raise H3PromptRewriterUvResolutionSecurityError(
            "bootstrap Python version is outside the reviewed contract"
        )
    try:
        after = path.lstat()
    except OSError as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "bootstrap Python executable changed during inspection"
        ) from error
    if _stat_identity(after) != _stat_identity(before):
        raise H3PromptRewriterUvResolutionSecurityError(
            "bootstrap Python executable changed during inspection"
        )
    return _PythonReceipt(
        path=path,
        sha256=digest.hexdigest(),
        size_bytes=before.st_size,
        version=match.group(1).decode("ascii"),
        stat_identity=_stat_identity(before),
    )


def build_h3_prompt_rewriter_uv_resolution_plan(
    uv_executable: object,
    python_executable: object,
    *,
    deadline_seconds: object = MAX_DEADLINE_SECONDS,
    metadata_byte_cap: object = DEFAULT_METADATA_BYTE_CAP,
    metadata_entry_cap: object = DEFAULT_METADATA_ENTRY_CAP,
) -> H3PromptRewriterUvResolutionPlan:
    """Bind the reviewed roots and pinned uv bytes without network or mutation."""

    if (
        type(deadline_seconds) is not int
        or not 1 <= deadline_seconds <= MAX_DEADLINE_SECONDS
    ):
        raise H3PromptRewriterUvResolutionError(
            "resolution deadline must be within the reviewed bound"
        )
    if (
        type(metadata_byte_cap) is not int
        or not 1 <= metadata_byte_cap <= MAX_METADATA_BYTE_CAP
    ):
        raise H3PromptRewriterUvResolutionError(
            "metadata byte cap must be positive and no larger than 1 GiB"
        )
    if (
        type(metadata_entry_cap) is not int
        or not 1 <= metadata_entry_cap <= MAX_METADATA_ENTRY_CAP
    ):
        raise H3PromptRewriterUvResolutionError(
            "metadata entry cap is outside the reviewed bound"
        )
    uv = _inspect_uv(uv_executable)
    python = _inspect_python(python_executable)
    input_sha = _sha256_bytes(reviewed_requirements_input_bytes())
    document = {
        "schema": UV_RESOLUTION_PLAN_SCHEMA,
        "planning_mutation": False,
        "planning_network": False,
        "execution_requires_network": True,
        "execution_writes_private_state": True,
        "execution_requires_explicit_flag": True,
        "execution_requires_expected_plan_sha256": True,
        "execution_requires_expected_input_sha256": True,
        "execution_requires_expected_uv_sha256": True,
        "execution_requires_expected_python_sha256": True,
        "installation_authorized": False,
        "runtime_execution_authorized": False,
        "target": {**_TARGET, "python_full_version": "3.12.14"},
        "root_requirements": list(closure.ROOT_REQUIREMENTS),
        "requirements_input_sha256": input_sha,
        "uv": {
            "version": PINNED_UV_VERSION,
            "sha256": uv.sha256,
            "size_bytes": uv.size_bytes,
            "executable_path_disclosed": False,
            "stat_identity_rechecked_at_execution": True,
        },
        "bootstrap_python": {
            "implementation": "cpython",
            "version": python.version,
            "sha256": python.sha256,
            "size_bytes": python.size_bytes,
            "canonical_path_sha256": _python_path_sha256(python),
            "stat_identity_sha256": _python_stat_sha256(python),
            "executable_path_disclosed": False,
            "stat_identity_rechecked_at_execution": True,
        },
        "resolver": {
            "command": "uv pip compile",
            "sequential_invocations": 2,
            "formats": ["pylock.toml", "requirements.txt+hashes+annotations"],
            "candidate_output_names": [
                PYLOCK_CANDIDATE_NAME,
                HASHED_REQUIREMENTS_CANDIDATE_NAME,
            ],
            "canonical_output_names": [PYLOCK_NAME, HASHED_REQUIREMENTS_NAME],
            "no_config": True,
            "no_python_downloads": True,
            "no_managed_python": True,
            "only_binary": ":all:",
            "no_build_flag_used": False,
            "source_distributions_permitted": False,
            "builds_permitted": False,
            "prerelease": "disallow",
            "index_strategy": "first-index",
            "pytorch_index": wheel_resolver.PYTORCH_INDEX,
            "default_index": wheel_resolver.PYPI_INDEX,
            "exact_size_evidence": "pylock_or_serial_https_head_content_length",
            "head_redirects": "same_artifact_reviewed_hosts_only",
        },
        "resources": {
            "subprocess_concurrency": 1,
            "nice": PROCESS_NICE,
            "ionice": "idle",
            "cpu_cores": MAX_CPU_CORES,
            "address_space_bytes": MAX_ADDRESS_SPACE_BYTES,
            "address_space_enforcement": "linux_rlimit_as_hard",
            "rss_bytes": MAX_RSS_BYTES,
            "rss_enforcement": "sampled_process_group_hard_stop_best_effort",
            "rss_proc_pid_entry_cap": MAX_PROC_PID_ENTRIES,
            "rss_proc_stat_byte_cap": MAX_PROC_STAT_BYTES,
            "rss_proc_status_byte_cap": MAX_PROC_STATUS_BYTES,
            "deadline_seconds": deadline_seconds,
            "metadata_byte_cap": metadata_byte_cap,
            "metadata_entry_cap": metadata_entry_cap,
            "child_file_size_bytes": MAX_CHILD_FILE_BYTES,
            "poll_seconds": POLL_SECONDS,
            "state_depth_cap": MAX_STATE_DEPTH,
            "scan_retry_cap": MAX_SCAN_RETRIES,
            "state_scan_max_unattested_seconds": MAX_UNATTESTED_STATE_SECONDS,
            "state_scan_quiescent_after_child": True,
            "process_group_cleanup": True,
            "recursive_private_state_monitor": True,
            "uv_internal_lock_compatibility": {
                "relative_paths": sorted(
                    "/".join(path) for path in _UV_INTERNAL_LOCK_PATHS
                ),
                "mode": "0666",
                "maximum_bytes": MAX_UV_INTERNAL_LOCK_BYTES,
                "regular_single_link_owner_only": True,
                "owner_private_ancestors": True,
            },
            "uv_cache_symlink_contract": {
                "schema": "wheels-v5-to-archive-v0.v1",
                "source": "cache/wheels-v5/index/<hex16>/<package>/<version-tag>",
                "source_mode": "0777",
                "target": "cache/archive-v0/<base64url21>",
                "target_text": "exact_absolute_same_state_root",
                "target_type": "owner_private_directory",
                "follow_during_traversal": False,
                "opened_target_identity_reconciled": True,
                "link_text_bytes_accounted": True,
            },
            "uv_archive_executable_compatibility": {
                "relative_root": "cache/archive-v0/<base64url21>/**",
                "mode": "0711",
                "regular_single_link_owner_only": True,
                "owner_private_ancestors": True,
                "bytes_accounted": True,
            },
        },
        "outputs": {
            "pylock": "private_candidate",
            "hashed_requirements": "private_candidate",
            "wheel_report_schema": wheel_resolver.WHEEL_RESOLUTION_REPORT_SCHEMA,
            "provenance": "private_unreviewed_candidate",
            "atomic_mode": "0600",
        },
    }
    encoded = _canonical_json(document).decode("ascii")
    if str(uv.path) in encoded or "/home/" in encoded or "/mnt/" in encoded:
        raise AssertionError("public uv plan contains a private path")
    return H3PromptRewriterUvResolutionPlan._from_document(document)


def _validate_plan_resource_contract(
    plan: H3PromptRewriterUvResolutionPlan,
) -> None:
    document = plan.document
    resources = document.get("resources")
    expected = {
        "address_space_bytes": MAX_ADDRESS_SPACE_BYTES,
        "address_space_enforcement": "linux_rlimit_as_hard",
        "rss_bytes": MAX_RSS_BYTES,
        "rss_enforcement": "sampled_process_group_hard_stop_best_effort",
        "rss_proc_pid_entry_cap": MAX_PROC_PID_ENTRIES,
        "rss_proc_stat_byte_cap": MAX_PROC_STAT_BYTES,
        "rss_proc_status_byte_cap": MAX_PROC_STATUS_BYTES,
        "poll_seconds": POLL_SECONDS,
        "state_scan_max_unattested_seconds": MAX_UNATTESTED_STATE_SECONDS,
        "state_scan_quiescent_after_child": True,
        "uv_archive_executable_compatibility": {
            "relative_root": "cache/archive-v0/<base64url21>/**",
            "mode": "0711",
            "regular_single_link_owner_only": True,
            "owner_private_ancestors": True,
            "bytes_accounted": True,
        },
    }
    if (
        document.get("schema") != UV_RESOLUTION_PLAN_SCHEMA
        or not isinstance(resources, dict)
        or any(resources.get(key) != value for key, value in expected.items())
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "resolution resource contract changed"
        )


def _private_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "private resolution directory is unavailable"
        ) from error
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "private resolution directory identity or mode is invalid"
        )


def _private_file(
    path: Path, maximum: int, *, allow_empty: bool = False
) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "private resolution file is unavailable"
        ) from error
    minimum = 0 if allow_empty else 1
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or not minimum <= info.st_size <= maximum
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "private resolution file identity, mode, or size is invalid"
        )
    return info


def _mkdir(path: Path) -> None:
    if path.exists():
        _private_directory(path)
        return
    path.mkdir(mode=0o700)
    _private_directory(path)


def _layout(feature_value: object, state_value: object) -> tuple[Path, Path]:
    if not isinstance(feature_value, (str, os.PathLike)) or not isinstance(
        state_value, (str, os.PathLike)
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "private roots must be absolute paths"
        )
    feature = Path(feature_value)
    state_root = Path(state_value)
    if not feature.is_absolute() or not state_root.is_absolute():
        raise H3PromptRewriterUvResolutionSecurityError(
            "private roots must be absolute paths"
        )
    try:
        if feature.resolve(strict=True) != feature:
            raise H3PromptRewriterUvResolutionSecurityError(
                "private feature root must be canonical"
            )
        resolved_parent = state_root.parent.resolve(strict=True)
    except OSError as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "private roots cannot be resolved"
        ) from error
    if resolved_parent != feature and feature not in resolved_parent.parents:
        raise H3PromptRewriterUvResolutionSecurityError(
            "resolution state must be beneath the private feature root"
        )
    _private_directory(feature)
    _mkdir(state_root)
    if state_root.resolve(strict=True) != state_root:
        raise H3PromptRewriterUvResolutionSecurityError(
            "resolution state must not traverse links"
        )
    names = {item.name for item in os.scandir(state_root)}
    if not names <= _ALLOWED_STATE_FILES:
        raise H3PromptRewriterUvResolutionSecurityError(
            "resolution state contains foreign files"
        )
    for name in ("cache", "home", "tmp"):
        _mkdir(state_root / name)
    return feature, state_root


def _uv_cache_symlink_source(relative: tuple[str, ...]) -> bool:
    return (
        len(relative) == 6
        and relative[:3] == ("cache", "wheels-v5", "index")
        and _UV_INDEX_ID.fullmatch(relative[3]) is not None
        and _UV_PACKAGE_COMPONENT.fullmatch(relative[4]) is not None
        and _UV_VERSION_TAG.fullmatch(relative[5]) is not None
    )


def _uv_cache_symlink_target(state_root: Path, target_text: str) -> tuple[str, ...]:
    archive_root = state_root / "cache" / "archive-v0"
    target = Path(target_text)
    try:
        relative = target.relative_to(archive_root)
    except ValueError as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "uv cache link target leaves its private archive"
        ) from error
    if (
        not target.is_absolute()
        or len(relative.parts) != 1
        or _UV_ARCHIVE_ID.fullmatch(relative.parts[0]) is None
        or target_text != str(archive_root / relative.parts[0])
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "uv cache link target shape is invalid"
        )
    return ("cache", "archive-v0", relative.parts[0])


def _uv_archive_executable(relative: tuple[str, ...]) -> bool:
    return (
        len(relative) >= 4
        and relative[:2] == ("cache", "archive-v0")
        and _UV_ARCHIVE_ID.fullmatch(relative[2]) is not None
    )


def _scan_private_state_once(
    state_root: Path, *, byte_cap: int, entry_cap: int
) -> _StateUsage:
    """Count a private tree through no-follow directory descriptors."""

    _private_directory(state_root)
    totals = [0, 0]
    visited_directories: dict[tuple[str, ...], tuple[int, int, int, int, int]] = {}
    symlink_receipts: list[_UvCacheSymlinkReceipt] = []

    def account(info: os.stat_result, *, charge_link_text: bool = False) -> None:
        totals[1] += 1
        if totals[1] > entry_cap:
            raise H3PromptRewriterUvResolutionSecurityError(
                "private resolution state exceeds its entry cap"
            )
        if stat.S_ISREG(info.st_mode) or charge_link_text:
            totals[0] += info.st_size
            if totals[0] > byte_cap:
                raise H3PromptRewriterUvResolutionSecurityError(
                    "private resolution state exceeds its byte cap"
                )

    def open_private_target(
        relative: tuple[str, ...], root_descriptor: int
    ) -> tuple[
        int,
        os.stat_result,
        tuple[
            tuple[tuple[str, ...], tuple[int, int, int, int, int]],
            ...,
        ],
    ]:
        current = os.dup(root_descriptor)
        try:
            prefix: tuple[str, ...] = ()
            chain = [(prefix, _stable_identity(os.fstat(current)))]
            for component in relative:
                try:
                    child = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=current,
                    )
                except FileNotFoundError:
                    raise
                except OSError as error:
                    raise H3PromptRewriterUvResolutionSecurityError(
                        "uv cache link target cannot be opened safely"
                    ) from error
                os.close(current)
                current = child
                info = os.fstat(current)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                ):
                    raise H3PromptRewriterUvResolutionSecurityError(
                        "uv cache link target directory is not private"
                    )
                prefix = (*prefix, component)
                chain.append((prefix, _stable_identity(info)))
            return current, os.fstat(current), tuple(chain)
        except Exception:
            os.close(current)
            raise

    def read_link(name: str, *, descriptor: int) -> str:
        try:
            return os.readlink(name, dir_fd=descriptor)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise H3PromptRewriterUvResolutionSecurityError(
                "uv cache link text cannot be read safely"
            ) from error

    def reconcile_opened_chain(
        chain: tuple[
            tuple[tuple[str, ...], tuple[int, int, int, int, int]],
            ...,
        ],
        *,
        root_identity: tuple[int, int, int, int, int],
    ) -> None:
        for prefix, identity in chain:
            expected = root_identity if not prefix else visited_directories.get(prefix)
            if identity != expected:
                raise _TransientStateChange(
                    "uv cache directory chain changed during reconciliation"
                )

    def walk(
        descriptor: int, *, top: bool, depth: int, relative: tuple[str, ...]
    ) -> None:
        if depth > MAX_STATE_DEPTH:
            raise H3PromptRewriterUvResolutionSecurityError(
                "private resolution state exceeds its depth cap"
            )
        with os.scandir(descriptor) as entries:
            for entry in entries:
                entry_relative = (*relative, entry.name)
                if top and entry.name not in _ALLOWED_STATE_FILES:
                    raise H3PromptRewriterUvResolutionSecurityError(
                        "resolution state contains a foreign entry"
                    )
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    if (
                        not _uv_cache_symlink_source(entry_relative)
                        or info.st_uid != os.getuid()
                        or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o777
                    ):
                        raise H3PromptRewriterUvResolutionSecurityError(
                            "resolution state contains an unapproved link"
                        )
                    target_text = read_link(entry.name, descriptor=descriptor)
                    after_link = os.stat(
                        entry.name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        _stable_identity(after_link) != _stable_identity(info)
                        or after_link.st_size != info.st_size
                        or info.st_size != len(os.fsencode(target_text))
                    ):
                        raise _TransientStateChange("uv cache link changed")
                    target_relative = _uv_cache_symlink_target(state_root, target_text)
                    target_descriptor, target_info, _target_chain = open_private_target(
                        target_relative, root_descriptor
                    )
                    try:
                        followed = os.stat(
                            entry.name,
                            dir_fd=descriptor,
                            follow_symlinks=True,
                        )
                        if _stable_identity(followed) != _stable_identity(target_info):
                            raise _TransientStateChange("uv cache link target changed")
                    finally:
                        os.close(target_descriptor)
                    account(info, charge_link_text=True)
                    symlink_receipts.append(
                        _UvCacheSymlinkReceipt(
                            relative=entry_relative,
                            identity=_stable_identity(info),
                            size_bytes=info.st_size,
                            target_text=target_text,
                            target_relative=target_relative,
                            target_identity=_stable_identity(target_info),
                        )
                    )
                    continue
                mode = stat.S_IMODE(info.st_mode)
                uv_internal_lock = entry_relative in _UV_INTERNAL_LOCK_PATHS
                compatible_uv_internal_lock = (
                    uv_internal_lock
                    and stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                    and mode == 0o666
                    and info.st_size <= MAX_UV_INTERNAL_LOCK_BYTES
                )
                compatible_uv_archive_executable = (
                    _uv_archive_executable(entry_relative)
                    and stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                    and mode == 0o711
                )
                if info.st_uid != os.getuid() or (
                    mode & 0o077
                    and not compatible_uv_internal_lock
                    and not compatible_uv_archive_executable
                ):
                    raise H3PromptRewriterUvResolutionSecurityError(
                        "resolution state contains a linked or non-private entry"
                    )
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=descriptor,
                    )
                    try:
                        opened = os.fstat(child)
                        if _stable_identity(opened) != _stable_identity(info):
                            raise _TransientStateChange(
                                "resolution directory identity changed"
                            )
                        visited_directories[entry_relative] = _stable_identity(opened)
                        account(opened)
                        walk(
                            child,
                            top=False,
                            depth=depth + 1,
                            relative=entry_relative,
                        )
                        current = os.stat(
                            entry.name,
                            dir_fd=descriptor,
                            follow_symlinks=False,
                        )
                        if _stable_identity(current) != _stable_identity(info):
                            raise _TransientStateChange(
                                "resolution directory changed during scan"
                            )
                    finally:
                        os.close(child)
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise H3PromptRewriterUvResolutionSecurityError(
                        "resolution state contains a special or linked file"
                    )
                file_descriptor = os.open(
                    entry.name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=descriptor,
                )
                try:
                    opened = os.fstat(file_descriptor)
                    if _stable_identity(opened) != _stable_identity(info):
                        raise _TransientStateChange("resolution file identity changed")
                    if (
                        compatible_uv_internal_lock
                        and stat.S_IMODE(opened.st_mode) == 0o666
                        and opened.st_size > MAX_UV_INTERNAL_LOCK_BYTES
                    ):
                        raise H3PromptRewriterUvResolutionSecurityError(
                            "uv internal lock exceeds its compatibility bound"
                        )
                    account(opened)
                finally:
                    os.close(file_descriptor)

    root_descriptor = os.open(
        state_root,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened_root = os.fstat(root_descriptor)
        current_root = state_root.lstat()
        root_identity = _stable_identity(opened_root)
        if root_identity != _stable_identity(current_root):
            raise _TransientStateChange("resolution state root identity changed")
        walk(root_descriptor, top=True, depth=0, relative=())
        for receipt in symlink_receipts:
            if (
                visited_directories.get(receipt.target_relative)
                != receipt.target_identity
            ):
                raise _TransientStateChange(
                    "uv cache link target was not traversed canonically"
                )
            source_parent = receipt.relative[:-1]
            expected_parent_identity = visited_directories.get(source_parent)
            if expected_parent_identity is None:
                raise _TransientStateChange(
                    "uv cache link parent was not traversed canonically"
                )
            parent_descriptor, parent_info, parent_chain = open_private_target(
                source_parent, root_descriptor
            )
            try:
                reconcile_opened_chain(parent_chain, root_identity=root_identity)
                if _stable_identity(parent_info) != expected_parent_identity:
                    raise _TransientStateChange(
                        "uv cache link parent changed after traversal"
                    )
                source_name = receipt.relative[-1]
                final_link = os.stat(
                    source_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    _stable_identity(final_link) != receipt.identity
                    or final_link.st_size != receipt.size_bytes
                ):
                    raise _TransientStateChange("uv cache link changed after traversal")
                final_target_text = read_link(source_name, descriptor=parent_descriptor)
                if (
                    final_target_text != receipt.target_text
                    or _uv_cache_symlink_target(state_root, final_target_text)
                    != receipt.target_relative
                ):
                    raise _TransientStateChange(
                        "uv cache link target text changed after traversal"
                    )
                target_descriptor, target_info, target_chain = open_private_target(
                    receipt.target_relative, root_descriptor
                )
                try:
                    reconcile_opened_chain(
                        target_chain,
                        root_identity=root_identity,
                    )
                    followed = os.stat(
                        source_name,
                        dir_fd=parent_descriptor,
                        follow_symlinks=True,
                    )
                    if (
                        _stable_identity(target_info) != receipt.target_identity
                        or _stable_identity(followed) != receipt.target_identity
                    ):
                        raise _TransientStateChange(
                            "uv cache link target changed after traversal"
                        )
                finally:
                    os.close(target_descriptor)
                after_link = os.stat(
                    source_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    _stable_identity(after_link) != receipt.identity
                    or after_link.st_size != receipt.size_bytes
                ):
                    raise _TransientStateChange(
                        "uv cache link changed during final reconciliation"
                    )
            finally:
                os.close(parent_descriptor)
            reopened_parent, reopened_parent_info, reopened_parent_chain = (
                open_private_target(source_parent, root_descriptor)
            )
            try:
                reconcile_opened_chain(
                    reopened_parent_chain,
                    root_identity=root_identity,
                )
                if _stable_identity(reopened_parent_info) != expected_parent_identity:
                    raise _TransientStateChange(
                        "uv cache link ancestor changed during reconciliation"
                    )
            finally:
                os.close(reopened_parent)
            reopened_target, reopened_target_info, reopened_target_chain = (
                open_private_target(receipt.target_relative, root_descriptor)
            )
            try:
                reconcile_opened_chain(
                    reopened_target_chain,
                    root_identity=root_identity,
                )
                if _stable_identity(reopened_target_info) != receipt.target_identity:
                    raise _TransientStateChange(
                        "uv cache target ancestor changed during reconciliation"
                    )
            finally:
                os.close(reopened_target)
        final_root = state_root.lstat()
        if _stable_identity(final_root) != _stable_identity(opened_root):
            raise _TransientStateChange("resolution state root changed during scan")
    finally:
        os.close(root_descriptor)
    return _StateUsage(bytes=totals[0], entries=totals[1])


def _scan_private_state(
    state_root: Path, *, byte_cap: int, entry_cap: int
) -> _StateUsage:
    for attempt in range(MAX_SCAN_RETRIES):
        try:
            return _scan_private_state_once(
                state_root,
                byte_cap=byte_cap,
                entry_cap=entry_cap,
            )
        except (FileNotFoundError, _TransientStateChange):
            if attempt + 1 == MAX_SCAN_RETRIES:
                raise _PrivateStateNotSettled(
                    "private resolution state changed too often during scan"
                ) from None
        except RecursionError:
            raise H3PromptRewriterUvResolutionSecurityError(
                "private resolution state exceeds its depth cap"
            ) from None
    raise AssertionError("bounded private-state scan exhausted unexpectedly")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if path.exists() and not temporary.exists():
        _private_file(path, max(len(payload), 1) + 4096, allow_empty=True)
        if path.read_bytes() != payload:
            raise H3PromptRewriterUvResolutionSecurityError(
                "preserved evidence conflicts with the new canonical payload"
            )
        return
    if temporary.exists():
        _private_file(temporary, max(len(payload), 1) + 4096, allow_empty=True)
        if path.exists():
            _private_file(path, max(len(payload), 1) + 4096, allow_empty=True)
            if temporary.read_bytes() != path.read_bytes():
                raise H3PromptRewriterUvResolutionSecurityError(
                    "ambiguous atomic leftover requires owner review"
                )
            temporary.unlink()
        elif temporary.read_bytes() == payload:
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return
        else:
            raise H3PromptRewriterUvResolutionSecurityError(
                "ambiguous atomic leftover requires owner review"
            )
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
            raise H3PromptRewriterUvResolutionSecurityError(
                "atomic temporary identity is invalid"
            )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _private_file(temporary, max(len(payload), 1) + 4096, allow_empty=True)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write_failure_receipt(
    state_root: Path,
    plan: H3PromptRewriterUvResolutionPlan,
    diagnostic: dict[str, object],
    error: Exception,
    *,
    input_sha256: str,
    uv_sha256: str,
    python_sha256: str,
) -> None:
    if isinstance(error, H3PromptRewriterUvResolutionSecurityError):
        category = "security_boundary"
    elif isinstance(error, H3PromptRewriterUvResolutionExecutionError):
        category = "execution_boundary"
    else:
        category = "external_boundary"
    returncode = diagnostic.get("returncode")
    if returncode is not None and type(returncode) is not int:
        returncode = None
    document = {
        "schema": UV_RESOLUTION_FAILURE_SCHEMA,
        "status": "failed",
        "terminal": True,
        "installation_authorized": False,
        "runtime_execution_authorized": False,
        "retry_authorized": False,
        "phase": diagnostic.get("phase", "pre_spawn"),
        "failure_category": category,
        "process_spawned": diagnostic.get("process_spawned") is True,
        "returncode": returncode,
        "validated_pylock_candidate_observed": (
            diagnostic.get("validated_pylock_candidate") is True
        ),
        "peak_rss_bytes": diagnostic.get("peak_rss_bytes", 0),
        "plan_sha256": plan.sha256,
        "requirements_input_sha256": input_sha256,
        "uv_sha256": uv_sha256,
        "bootstrap_python_sha256": python_sha256,
    }
    _atomic_write(state_root / FAILURE_NAME, _canonical_json(document) + b"\n")


@contextmanager
def _execution_lock(state_root: Path):
    path = state_root / "execution.lock"
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise H3PromptRewriterUvResolutionSecurityError(
                "resolution execution lock identity is invalid"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise H3PromptRewriterUvResolutionSecurityError(
                "another resolution process owns the private state"
            ) from error
        yield
    finally:
        os.close(descriptor)


def _reconcile_pylock_pair(state_root: Path) -> None:
    final = state_root / PYLOCK_NAME
    temporary = state_root / PYLOCK_CANDIDATE_NAME
    if not temporary.exists():
        return
    _private_file(temporary, MAX_PYLOCK_BYTES)
    if not final.exists():
        raise H3PromptRewriterUvResolutionSecurityError(
            "interrupted pylock candidate requires owner-reviewed retry"
        )
    _private_file(final, MAX_PYLOCK_BYTES)
    if temporary.read_bytes() != final.read_bytes():
        raise H3PromptRewriterUvResolutionSecurityError(
            "conflicting pylock candidates require owner review"
        )
    temporary.unlink()


def _hash_private_file(path: Path, maximum: int) -> tuple[int, str, bytes]:
    before = _private_file(path, maximum)
    payload = path.read_bytes()
    after = path.lstat()
    if (
        _stat_identity(before) != _stat_identity(after)
        or len(payload) != before.st_size
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "private resolution file changed while read"
        )
    return len(payload), _sha256_bytes(payload), payload


def _manylinux_floor(platform: str) -> int | None:
    if platform == "manylinux2014_x86_64":
        return 17
    match = re.fullmatch(r"manylinux_2_([0-9]+)_x86_64", platform)
    return int(match.group(1)) if match else None


def _wheel_priority(filename: str, package: str, version: str) -> tuple[int, int, int]:
    try:
        distribution, wheel_version, _build, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock contains an invalid wheel filename"
        ) from error
    if canonicalize_name(distribution) != package or str(wheel_version) != version:
        raise H3PromptRewriterUvResolutionSecurityError(
            "wheel filename contradicts package identity"
        )
    priorities: list[tuple[int, int, int]] = []
    for tag in tags:
        if tag.interpreter == "py3" and tag.abi == "none" and tag.platform == "any":
            if package not in _BINARY_ROOTS and not package.startswith("nvidia-"):
                priorities.append((1, 0, 0))
            continue
        floor = _manylinux_floor(tag.platform)
        if floor is None:
            continue
        if floor > 28:
            continue
        if package.startswith("nvidia-"):
            if tag.interpreter == "py3" and tag.abi == "none":
                priorities.append((2, floor, 0))
            continue
        if tag.interpreter == "cp312" and tag.abi == "cp312":
            priorities.append((4, floor, 12))
            continue
        abi3 = re.fullmatch(r"cp3([0-9]+)", tag.interpreter)
        if tag.abi == "abi3" and abi3 and int(abi3.group(1)) <= 12:
            priorities.append((3, floor, int(abi3.group(1))))
    return max(priorities, default=(0, 0, 0))


def _source_url(value: object, filename: str, package: str, selected_index: str) -> str:
    if type(value) is not str or len(value) > 2048:
        raise H3PromptRewriterUvResolutionSecurityError("wheel URL is invalid")
    parsed = urllib.parse.urlsplit(value)
    try:
        decoded_path = urllib.parse.unquote(parsed.path, errors="strict")
    except UnicodeError as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "wheel URL is invalid"
        ) from error
    canonical_path = urllib.parse.quote(decoded_path, safe="/-._~")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != canonical_path
        or decoded_path.split("/")[-1] != filename
    ):
        raise H3PromptRewriterUvResolutionSecurityError("wheel URL is invalid")
    if parsed.port is not None or parsed.netloc != parsed.hostname:
        raise H3PromptRewriterUvResolutionSecurityError("wheel URL is invalid")
    if selected_index == wheel_resolver.PYPI_INDEX:
        valid = parsed.hostname == "files.pythonhosted.org" and parsed.path.startswith(
            "/packages/"
        )
    elif selected_index == wheel_resolver.PYTORCH_INDEX:
        valid = (
            (
                parsed.hostname in {"download.pytorch.org", "download-r2.pytorch.org"}
                and parsed.path.startswith("/whl/")
            )
            or (
                parsed.hostname == "files.pythonhosted.org"
                and parsed.path.startswith("/packages/")
            )
            or (
                package.startswith("nvidia-")
                and parsed.hostname == "pypi.nvidia.com"
                and parsed.path.startswith(f"/{package}/")
            )
        )
    else:
        valid = False
    if package in {"torch", "torchvision"} or package.startswith("nvidia-"):
        valid = valid and selected_index == wheel_resolver.PYTORCH_INDEX
    if not valid:
        raise H3PromptRewriterUvResolutionSecurityError(
            "wheel URL violates its registry partition"
        )
    return value


def _head_content_length(
    url: str,
    package: str,
    selected_index: str,
    filename: str,
    *,
    timeout: float,
) -> int:
    if timeout <= 0:
        raise H3PromptRewriterUvResolutionExecutionError(
            "wheel size lookup crossed its deadline"
        )
    _source_url(url, filename, package, selected_index)
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "Maestro-H3-metadata-canary/1"},
    )

    class ReviewedRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _source_url(newurl, filename, package, selected_index)
            prior_host = urllib.parse.urlsplit(req.full_url).hostname
            new_host = urllib.parse.urlsplit(newurl).hostname
            if not (
                prior_host == "download.pytorch.org"
                and new_host == "download-r2.pytorch.org"
            ):
                raise H3PromptRewriterUvResolutionSecurityError(
                    "wheel size lookup redirected outside its reviewed artifact host"
                )
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), ReviewedRedirectHandler()
    )
    try:
        with opener.open(request, timeout=min(timeout, 5.0)) as response:
            final_url = response.geturl()
            _source_url(final_url, filename, package, selected_index)
            original_host = urllib.parse.urlsplit(url).hostname
            final_host = urllib.parse.urlsplit(final_url).hostname
            if final_url != url and not (
                original_host == "download.pytorch.org"
                and final_host == "download-r2.pytorch.org"
            ):
                raise H3PromptRewriterUvResolutionSecurityError(
                    "wheel size lookup redirected outside its reviewed artifact host"
                )
            value = response.headers.get("Content-Length")
    except H3PromptRewriterUvResolutionError:
        raise
    except (OSError, ValueError) as error:
        raise H3PromptRewriterUvResolutionExecutionError(
            "wheel size metadata could not be read"
        ) from error
    if value is None or not value.isascii() or not value.isdigit():
        raise H3PromptRewriterUvResolutionExecutionError(
            "wheel size metadata is unavailable"
        )
    size = int(value)
    if not 1 <= size <= MAX_TOTAL_WHEEL_BYTES:
        raise H3PromptRewriterUvResolutionSecurityError(
            "wheel size metadata is outside its bound"
        )
    return size


def _wheel_row(
    raw: object,
    package: str,
    version: str,
    selected_index: str,
    requirement_hashes: frozenset[str],
) -> tuple[tuple[int, int, int], dict[str, object]] | None:
    if type(raw) is not dict or not set(raw) <= {
        "url",
        "size",
        "hashes",
        "upload-time",
    }:
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock wheel fields are invalid"
        )
    if not {"url", "hashes"} <= set(raw):
        raise H3PromptRewriterUvResolutionSecurityError("pylock wheel is incomplete")
    if type(raw["url"]) is not str:
        raise H3PromptRewriterUvResolutionSecurityError("wheel URL is invalid")
    filename = urllib.parse.unquote(urllib.parse.urlsplit(raw["url"]).path).split("/")[
        -1
    ]
    hashes = raw["hashes"]
    size = raw.get("size")
    if (
        type(filename) is not str
        or (
            size is not None
            and (type(size) is not int or not 1 <= size <= MAX_TOTAL_WHEEL_BYTES)
        )
        or type(hashes) is not dict
        or not set(hashes) <= {"sha256"}
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock wheel byte evidence is invalid"
        )
    if "sha256" in hashes:
        digest = _digest(hashes["sha256"], "wheel digest")
        if digest not in requirement_hashes:
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock wheel digest contradicts hashed requirements"
            )
    elif len(requirement_hashes) == 1:
        digest = next(iter(requirement_hashes))
    else:
        raise H3PromptRewriterUvResolutionSecurityError(
            "selected wheel digest is ambiguous"
        )
    url = _source_url(raw["url"], filename, package, selected_index)
    priority = _wheel_priority(filename, package, version)
    if priority == (0, 0, 0):
        return None
    return priority, {
        "filename": filename,
        "size_bytes": size,
        "sha256": digest,
        "index": selected_index,
        "source_url": url,
    }


def _parse_hashed_requirements(
    payload: object, *, requirements_reference: str
) -> dict[str, dict[str, object]]:
    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_HASHED_REQUIREMENTS_BYTES
        or type(requirements_reference) is not str
        or not requirements_reference.startswith("/")
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "hashed requirements bytes are outside their bound"
        )
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "hashed requirements are invalid"
        ) from error
    if (
        len(lines) < 3
        or lines[0] != "# This file was autogenerated by uv via the following command:"
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "hashed requirements header is invalid"
        )
    lines = lines[2:]
    rows: dict[str, dict[str, object]] = {}
    position = 0
    header = re.compile(r"([a-z0-9][a-z0-9._-]{0,127})==([^ \\]+) \\")
    hash_line = re.compile(r"    --hash=sha256:([0-9a-f]{64})( \\)?")
    while position < len(lines):
        match = header.fullmatch(lines[position])
        if match is None:
            raise H3PromptRewriterUvResolutionSecurityError(
                "hashed requirements package line is invalid"
            )
        name = canonicalize_name(match.group(1))
        version = match.group(2)
        if name != match.group(1) or name in rows:
            raise H3PromptRewriterUvResolutionSecurityError(
                "hashed requirements package identity is invalid"
            )
        try:
            parsed_version = Version(version)
        except InvalidVersion as error:
            raise H3PromptRewriterUvResolutionSecurityError(
                "hashed requirements version is invalid"
            ) from error
        if str(parsed_version) != version or parsed_version.is_prerelease:
            raise H3PromptRewriterUvResolutionSecurityError(
                "hashed requirements version is invalid"
            )
        position += 1
        hashes: list[str] = []
        while position < len(lines) and (
            digest := hash_line.fullmatch(lines[position])
        ):
            hashes.append(digest.group(1))
            position += 1
        if not hashes or hashes != sorted(set(hashes)):
            raise H3PromptRewriterUvResolutionSecurityError(
                "hashed requirements digests are invalid"
            )
        parents: list[str] = []
        if position >= len(lines) or not lines[position].startswith("    # via"):
            raise H3PromptRewriterUvResolutionSecurityError(
                "hashed requirements dependency provenance is missing"
            )
        via = lines[position][len("    # via") :].strip()
        position += 1
        if via:
            parents.append(via)
        else:
            while position < len(lines) and lines[position].startswith("    #   "):
                parents.append(lines[position][len("    #   ") :])
                position += 1
        if position >= len(lines) or not lines[position].startswith("    # from "):
            raise H3PromptRewriterUvResolutionSecurityError(
                "hashed requirements registry annotation is missing"
            )
        index = lines[position][len("    # from ") :]
        position += 1
        if index not in {wheel_resolver.PYPI_INDEX, wheel_resolver.PYTORCH_INDEX}:
            raise H3PromptRewriterUvResolutionSecurityError(
                "hashed requirements registry annotation is invalid"
            )
        canonical_parents: list[str] = []
        root = False
        for parent in parents:
            if parent == f"-r {requirements_reference}":
                root = True
                continue
            if parent.startswith("-r "):
                raise H3PromptRewriterUvResolutionSecurityError(
                    "hashed requirements references an unknown input"
                )
            canonical = canonicalize_name(parent)
            if canonical != parent:
                raise H3PromptRewriterUvResolutionSecurityError(
                    "hashed requirements dependency parent is noncanonical"
                )
            canonical_parents.append(canonical)
        if canonical_parents != sorted(set(canonical_parents)):
            raise H3PromptRewriterUvResolutionSecurityError(
                "hashed requirements dependency parents are invalid"
            )
        rows[name] = {
            "version": version,
            "hashes": frozenset(hashes),
            "parents": tuple(canonical_parents),
            "root": root,
            "index": index,
        }
    if list(rows) != sorted(rows):
        raise H3PromptRewriterUvResolutionSecurityError(
            "hashed requirements packages are not canonical order"
        )
    return rows


def parse_uv_resolution_evidence_to_wheel_report(
    pylock_payload: object,
    hashed_requirements_payload: object,
    *,
    requirements_reference: str,
    size_resolver: Callable[[str, str, str, str], int],
) -> dict[str, object]:
    """Join pinned uv's selected-artifact and dependency-graph evidence."""

    payload = pylock_payload
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PYLOCK_BYTES:
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock bytes are outside their bound"
        )
    try:
        document = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock TOML is invalid"
        ) from error
    allowed_top = {
        "lock-version",
        "created-by",
        "requires-python",
        "environments",
        "extras",
        "dependency-groups",
        "default-groups",
        "packages",
    }
    if type(document) is not dict or not set(document) <= allowed_top:
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock top-level fields are invalid"
        )
    if (
        document.get("lock-version") != "1.0"
        or document.get("created-by") != "uv"
        or type(document.get("requires-python")) is not str
    ):
        raise H3PromptRewriterUvResolutionSecurityError("pylock header is invalid")
    try:
        if Version("3.12.14") not in SpecifierSet(document["requires-python"]):
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock does not admit exact CPython 3.12.14"
            )
    except InvalidSpecifier as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock Python requirement is invalid"
        ) from error
    for field in ("environments", "extras", "dependency-groups", "default-groups"):
        if document.get(field) not in (None, []):
            raise H3PromptRewriterUvResolutionSecurityError(
                "multi-environment, extras, and dependency groups are forbidden"
            )
    raw_packages = document.get("packages")
    if (
        type(raw_packages) is not list
        or not 1 <= len(raw_packages) <= closure.MAX_PACKAGES
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock package inventory is invalid"
        )
    requirements = _parse_hashed_requirements(
        hashed_requirements_payload, requirements_reference=requirements_reference
    )
    pending: list[tuple[str, str, dict[str, object]]] = []
    seen: set[str] = set()
    for raw in raw_packages:
        allowed_package = {
            "name",
            "version",
            "requires-python",
            "wheels",
            "marker",
        }
        if (
            type(raw) is not dict
            or not set(raw) <= allowed_package
            or not {"name", "version", "wheels"} <= set(raw)
        ):
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock package fields are invalid or contain a non-registry source"
            )
        name_value, version_value = raw["name"], raw["version"]
        if type(name_value) is not str or type(version_value) is not str:
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock package identity is invalid"
            )
        name = canonicalize_name(name_value)
        try:
            parsed_version = Version(version_value)
        except InvalidVersion as error:
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock package version is invalid"
            ) from error
        if (
            name_value != name
            or str(parsed_version) != version_value
            or parsed_version.is_prerelease
            or parsed_version.is_devrelease
            or name in seen
        ):
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock package identity is noncanonical, duplicate, or prerelease"
            )
        seen.add(name)
        requirement_row = requirements.get(name)
        if requirement_row is None or requirement_row["version"] != version_value:
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock and hashed requirements inventories disagree"
            )
        marker = raw.get("marker")
        if marker is not None:
            if type(marker) is not str:
                raise H3PromptRewriterUvResolutionSecurityError(
                    "pylock package marker is invalid"
                )
            try:
                selected = Marker(marker).evaluate(_MARKER_ENVIRONMENT)
            except InvalidMarker as error:
                raise H3PromptRewriterUvResolutionSecurityError(
                    "pylock package marker is invalid"
                ) from error
            if not selected:
                raise H3PromptRewriterUvResolutionSecurityError(
                    "pylock contains a package outside the reviewed target"
                )
        if "requires-python" in raw:
            if type(raw["requires-python"]) is not str:
                raise H3PromptRewriterUvResolutionSecurityError(
                    "package Python requirement is invalid"
                )
            try:
                compatible_python = Version("3.12.14") in SpecifierSet(
                    raw["requires-python"]
                )
            except InvalidSpecifier as error:
                raise H3PromptRewriterUvResolutionSecurityError(
                    "package Python requirement is invalid"
                ) from error
            if not compatible_python:
                raise H3PromptRewriterUvResolutionSecurityError(
                    "package does not admit exact CPython 3.12.14"
                )
        raw_wheels = raw["wheels"]
        if type(raw_wheels) is not list or not raw_wheels:
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock package has no wheel candidates"
            )
        compatible = [
            row
            for item in raw_wheels
            if (
                row := _wheel_row(
                    item,
                    name,
                    version_value,
                    str(requirement_row["index"]),
                    requirement_row["hashes"],
                )
            )
            is not None
        ]
        if not compatible:
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock has no compatible target wheel"
            )
        best_priority = max(priority for priority, _wheel in compatible)
        selected_wheels = [
            wheel for priority, wheel in compatible if priority == best_priority
        ]
        if len(selected_wheels) != 1:
            raise H3PromptRewriterUvResolutionSecurityError(
                "pylock target wheel selection is ambiguous"
            )
        pending.append((name, version_value, selected_wheels[0]))
    if set(requirements) != seen:
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock and hashed requirements inventories disagree"
        )
    versions = {name: version for name, version, _wheel in pending}
    roots = {
        canonicalize_name(name): version for name, version in closure.ROOT_PACKAGE_PINS
    }
    if any(versions.get(name) != version for name, version in roots.items()):
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock misses an exact reviewed root"
        )
    root_rows = {name for name, row in requirements.items() if row["root"]}
    if root_rows != set(roots):
        raise H3PromptRewriterUvResolutionSecurityError(
            "hashed requirements roots disagree with the reviewed input"
        )
    adjacency = {
        name: sorted(
            dependency
            for dependency, row in requirements.items()
            if name in row["parents"]
        )
        for name in requirements
    }
    if any(
        parent not in requirements
        for row in requirements.values()
        for parent in row["parents"]
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "hashed requirements contains an unresolved dependency parent"
        )
    if (
        sum(len(dependencies) for dependencies in adjacency.values())
        > closure.MAX_EDGES
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "hashed requirements dependency graph exceeds its edge bound"
        )
    state = {name: 0 for name in adjacency}
    for root in sorted(roots):
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            name, exiting = stack.pop()
            if exiting:
                state[name] = 2
                continue
            if state[name] == 1:
                raise H3PromptRewriterUvResolutionSecurityError(
                    "pylock dependency graph contains a cycle"
                )
            if state[name] == 2:
                continue
            state[name] = 1
            stack.append((name, True))
            for dependency in reversed(adjacency[name]):
                if state[dependency] == 1:
                    raise H3PromptRewriterUvResolutionSecurityError(
                        "pylock dependency graph contains a cycle"
                    )
                if state[dependency] == 0:
                    stack.append((dependency, False))
    if any(status != 2 for status in state.values()):
        raise H3PromptRewriterUvResolutionSecurityError(
            "pylock contains unreachable packages"
        )
    rows: list[dict[str, object]] = []
    total = 0
    for name, version, wheel in pending:
        if wheel["size_bytes"] is None:
            wheel["size_bytes"] = size_resolver(
                str(wheel["source_url"]),
                name,
                str(wheel["index"]),
                str(wheel["filename"]),
            )
        if type(wheel["size_bytes"]) is not int:
            raise H3PromptRewriterUvResolutionSecurityError(
                "wheel size evidence is invalid"
            )
        total += int(wheel["size_bytes"])
        if total > MAX_TOTAL_WHEEL_BYTES:
            raise H3PromptRewriterUvResolutionSecurityError(
                "resolved wheel inventory exceeds 8 GiB"
            )
        rows.append(
            {
                "name": name,
                "version": version,
                "requirement": f"{name}=={version}",
                "dependencies": [
                    f"{dependency}=={versions[dependency]}"
                    for dependency in adjacency[name]
                ],
                "wheel": wheel,
            }
        )
    rows.sort(key=lambda item: str(item["name"]))
    report = {
        "schema": wheel_resolver.WHEEL_RESOLUTION_REPORT_SCHEMA,
        "target": dict(_TARGET),
        "root_requirements": list(closure.ROOT_REQUIREMENTS),
        "packages": rows,
    }
    # Reuse the downstream parser as the final schema/closure compatibility gate.
    report_payload = _canonical_json(report) + b"\n"
    try:
        wheel_resolver._load_report(report_payload, _sha256_bytes(report_payload))
    except (
        wheel_resolver.H3PromptRewriterWheelResolverError,
        closure.H3PromptRewriterDependencyClosureError,
    ) as error:
        raise H3PromptRewriterUvResolutionSecurityError(
            "wheel report failed the downstream compatibility gate"
        ) from error
    return report


def parse_uv_pylock_to_wheel_report(payload: object) -> dict[str, object]:
    """Reject the former single-document claim; uv 0.9.26 omits required evidence."""

    raise H3PromptRewriterUvResolutionSecurityError(
        "uv pylock requires separate hash, graph, and size evidence"
    )


def _apply_child_limits() -> None:
    os.umask(0o077)
    os.nice(PROCESS_NICE)
    available = sorted(os.sched_getaffinity(0))
    if len(available) > MAX_CPU_CORES:
        os.sched_setaffinity(0, set(available[:MAX_CPU_CORES]))
    resource.setrlimit(
        resource.RLIMIT_AS,
        (MAX_ADDRESS_SPACE_BYTES, MAX_ADDRESS_SPACE_BYTES),
    )
    resource.setrlimit(
        resource.RLIMIT_FSIZE, (MAX_CHILD_FILE_BYTES, MAX_CHILD_FILE_BYTES)
    )
    try:
        syscall = ctypes.CDLL(None, use_errno=True).syscall
        # Linux ioprio_set(IOPRIO_WHO_PROCESS=1, self=0, class idle=3).
        if syscall(251, 1, 0, 3 << 13) != 0:
            raise OSError(ctypes.get_errno(), "ioprio_set failed")
    except Exception as error:
        raise OSError("ionice idle could not be applied") from error


def _read_bounded_proc_file(
    directory_fd: int,
    name: str,
    byte_cap: int,
) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        payload = os.read(descriptor, byte_cap + 1)
    finally:
        os.close(descriptor)
    if len(payload) > byte_cap:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process metadata exceeded its bound"
        )
    return payload


def _parse_proc_stat(payload: bytes, expected_pid: int) -> tuple[bytes, int, int]:
    prefix = f"{expected_pid} (".encode("ascii")
    closing = payload.rfind(b")")
    if (
        not payload.startswith(prefix)
        or closing < len(prefix)
        or closing + 2 > len(payload)
    ):
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process metadata is malformed"
        )
    fields = payload[closing + 2 :].split()
    if len(fields) < 20:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process metadata is malformed"
        )
    try:
        process_state = fields[0]
        process_group = int(fields[2])
        start_time = int(fields[19])
    except (TypeError, ValueError) as error:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process metadata is malformed"
        ) from error
    if (
        len(process_state) != 1
        or process_state not in b"RSDZTtWXxKPI"
        or process_group < 0
        or start_time < 0
    ):
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process metadata is malformed"
        )
    return process_state, process_group, start_time


def _parse_proc_status_rss(payload: bytes) -> int:
    values: list[int] = []
    for line in payload.splitlines():
        match = re.fullmatch(rb"VmRSS:[ \t]+([0-9]+)[ \t]+kB", line)
        if match is not None:
            values.append(int(match.group(1)))
    if len(values) != 1:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process RSS metadata is malformed"
        )
    rss_bytes = values[0] * 1024
    if rss_bytes < 0 or rss_bytes > MAX_ADDRESS_SPACE_BYTES:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process RSS metadata is outside its bound"
        )
    return rss_bytes


def _sample_process_group_rss(
    process_group: int,
    *,
    proc_root: Path = Path("/proc"),
) -> int:
    """Sum stable VmRSS observations for every visible member of one group."""

    if type(process_group) is not int or process_group <= 1:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process group identity is invalid"
        )
    for _attempt in range(MAX_SCAN_RETRIES):
        transient = False
        total = 0
        root_fd = -1
        try:
            root_fd = os.open(
                proc_root,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            names = os.listdir(root_fd)
            numeric_names = sorted(
                name for name in names if name.isascii() and name.isdigit()
            )
            if len(numeric_names) > MAX_PROC_PID_ENTRIES:
                raise H3PromptRewriterUvResolutionExecutionError(
                    "resolver process census exceeded its entry bound"
                )
            for name in numeric_names:
                pid = int(name)
                pid_fd = -1
                try:
                    pid_fd = os.open(
                        name,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=root_fd,
                    )
                    before = _parse_proc_stat(
                        _read_bounded_proc_file(
                            pid_fd,
                            "stat",
                            MAX_PROC_STAT_BYTES,
                        ),
                        pid,
                    )
                    if before[1] != process_group:
                        continue
                    if before[0] in {b"Z", b"X", b"x"}:
                        after = _parse_proc_stat(
                            _read_bounded_proc_file(
                                pid_fd,
                                "stat",
                                MAX_PROC_STAT_BYTES,
                            ),
                            pid,
                        )
                        if after[1:] != before[1:] or after[0] not in {
                            b"Z",
                            b"X",
                            b"x",
                        }:
                            transient = True
                            break
                        continue
                    try:
                        rss_bytes = _parse_proc_status_rss(
                            _read_bounded_proc_file(
                                pid_fd,
                                "status",
                                MAX_PROC_STATUS_BYTES,
                            )
                        )
                    except H3PromptRewriterUvResolutionExecutionError:
                        after = _parse_proc_stat(
                            _read_bounded_proc_file(
                                pid_fd,
                                "stat",
                                MAX_PROC_STAT_BYTES,
                            ),
                            pid,
                        )
                        if after[1:] != before[1:]:
                            transient = True
                            break
                        if after[0] not in {b"Z", b"X", b"x"}:
                            raise
                        final = _parse_proc_stat(
                            _read_bounded_proc_file(
                                pid_fd,
                                "stat",
                                MAX_PROC_STAT_BYTES,
                            ),
                            pid,
                        )
                        if final[1:] != before[1:] or final[0] not in {
                            b"Z",
                            b"X",
                            b"x",
                        }:
                            transient = True
                            break
                        continue
                    after = _parse_proc_stat(
                        _read_bounded_proc_file(
                            pid_fd,
                            "stat",
                            MAX_PROC_STAT_BYTES,
                        ),
                        pid,
                    )
                    if after[1:] != before[1:]:
                        transient = True
                        break
                    total += rss_bytes
                except FileNotFoundError:
                    transient = True
                    break
                finally:
                    if pid_fd >= 0:
                        os.close(pid_fd)
            if not transient:
                return total
        except H3PromptRewriterUvResolutionError:
            raise
        except (OSError, ValueError) as error:
            raise H3PromptRewriterUvResolutionExecutionError(
                "resolver process census could not be read"
            ) from error
        finally:
            if root_fd >= 0:
                os.close(root_fd)
    raise H3PromptRewriterUvResolutionExecutionError(
        "resolver process census changed too often"
    )


def _child_environment(state_root: Path) -> dict[str, str]:
    return {
        "HOME": str(state_root / "home"),
        "TMPDIR": str(state_root / "tmp"),
        "UV_CACHE_DIR": str(state_root / "cache"),
        "UV_NO_CONFIG": "1",
        "UV_NO_MANAGED_PYTHON": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_KEYRING_PROVIDER": "disabled",
        "CUDA_VISIBLE_DEVICES": "",
        "HIP_VISIBLE_DEVICES": "",
        "ROCR_VISIBLE_DEVICES": "",
        "NVIDIA_VISIBLE_DEVICES": "void",
    }


def _command(
    uv: Path, python: Path, state_root: Path, *, hashed_requirements: bool = False
) -> list[str]:
    command = [
        str(uv),
        "pip",
        "compile",
        str(state_root / INPUT_NAME),
        "--python",
        str(python),
        "--output-file",
        str(
            state_root
            / (
                HASHED_REQUIREMENTS_CANDIDATE_NAME
                if hashed_requirements
                else PYLOCK_CANDIDATE_NAME
            )
        ),
        "--python-version",
        "3.12.14",
        "--python-platform",
        "x86_64-manylinux_2_28",
        "--no-config",
        "--no-python-downloads",
        "--no-managed-python",
        "--only-binary",
        ":all:",
        "--prerelease",
        "disallow",
        "--no-sources",
        "--index",
        wheel_resolver.PYTORCH_INDEX,
        "--default-index",
        wheel_resolver.PYPI_INDEX,
        "--index-strategy",
        "first-index",
        "--keyring-provider",
        "disabled",
        "--no-progress",
        "--color",
        "never",
    ]
    if hashed_requirements:
        command.extend(["--generate-hashes", "--emit-index-annotation"])
    else:
        command.extend(["--format", "pylock.toml"])
    return command


def _process_group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except OSError as error:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process group could not be probed"
        ) from error
    return True


def _reap(process: object, timeout: float) -> int | None:
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.SubprocessError) as error:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process could not be reaped"
        ) from error


def _cleanup_process_group(process: object) -> None:
    """Leave neither the resolver parent nor any process-group descendant."""

    pid = process.pid
    if type(pid) is not int or pid <= 1:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolver process identity is invalid"
        )
    failure: H3PromptRewriterUvResolutionExecutionError | None = None
    try:
        exists = _process_group_exists(pid)
    except H3PromptRewriterUvResolutionExecutionError as error:
        failure = error
        exists = True
    if exists:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            failure = H3PromptRewriterUvResolutionExecutionError(
                "resolver process group could not be terminated"
            )
            failure.__cause__ = error
    try:
        _reap(process, 5)
    except H3PromptRewriterUvResolutionExecutionError as error:
        failure = failure or error
    try:
        exists = _process_group_exists(pid)
    except H3PromptRewriterUvResolutionExecutionError as error:
        failure = failure or error
        exists = True
    if exists:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            failure = failure or H3PromptRewriterUvResolutionExecutionError(
                "resolver process group could not be killed"
            )
            failure.__cause__ = error
    try:
        _reap(process, 5)
    except H3PromptRewriterUvResolutionExecutionError as error:
        failure = failure or error
    try:
        exists = _process_group_exists(pid)
    except H3PromptRewriterUvResolutionExecutionError as error:
        failure = failure or error
        exists = True
    if exists:
        failure = failure or H3PromptRewriterUvResolutionExecutionError(
            "resolver process group survived cleanup"
        )
    try:
        final_reap = _reap(process, 0)
    except H3PromptRewriterUvResolutionExecutionError as error:
        failure = failure or error
        final_reap = None
    if final_reap is None:
        failure = failure or H3PromptRewriterUvResolutionExecutionError(
            "resolver parent survived final reap"
        )
    if failure is not None:
        raise failure


def _wait_for_resolution(
    process: object,
    state_root: Path,
    plan: H3PromptRewriterUvResolutionPlan,
    *,
    started: float,
    monotonic: Callable[[], float],
    diagnostic: dict[str, object],
    rss_sampler: Callable[[int], int] | None,
) -> int:
    resources = plan.document["resources"]
    deadline = float(resources["deadline_seconds"])
    byte_cap = int(resources["metadata_byte_cap"])
    entry_cap = int(resources["metadata_entry_cap"])
    rss_cap = int(resources["rss_bytes"])
    sampler = rss_sampler or _sample_process_group_rss
    last_state_attestation = started
    while True:
        try:
            _scan_private_state(
                state_root,
                byte_cap=byte_cap,
                entry_cap=entry_cap,
            )
        except _PrivateStateNotSettled:
            scan_finished_at = monotonic()
            if scan_finished_at - last_state_attestation > MAX_UNATTESTED_STATE_SECONDS:
                raise H3PromptRewriterUvResolutionExecutionError(
                    "private resolution state did not settle within its monitor bound"
                ) from None
        else:
            scan_finished_at = monotonic()
            last_state_attestation = scan_finished_at
        try:
            observed_rss = sampler(process.pid)
        except H3PromptRewriterUvResolutionError:
            raise
        except (OSError, ValueError) as error:
            raise H3PromptRewriterUvResolutionExecutionError(
                "resolver process RSS could not be sampled"
            ) from error
        if type(observed_rss) is not int or observed_rss < 0:
            raise H3PromptRewriterUvResolutionExecutionError(
                "resolver process RSS sample is invalid"
            )
        previous_peak = diagnostic.get("peak_rss_bytes", 0)
        if type(previous_peak) is not int or previous_peak < 0:
            raise H3PromptRewriterUvResolutionExecutionError(
                "resolver process RSS peak is invalid"
            )
        diagnostic["peak_rss_bytes"] = max(previous_peak, observed_rss)
        if observed_rss > rss_cap:
            raise H3PromptRewriterUvResolutionExecutionError(
                "resolver process group exceeded its resident-memory cap"
            )
        remaining = deadline - (scan_finished_at - started)
        if remaining <= 0:
            raise H3PromptRewriterUvResolutionExecutionError(
                "uv resolution exceeded its plan-bound deadline"
            )
        try:
            returncode = process.wait(timeout=min(POLL_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            continue
        except (OSError, subprocess.SubprocessError) as error:
            raise H3PromptRewriterUvResolutionExecutionError(
                "uv resolution wait failed"
            ) from error
        if monotonic() - started > deadline:
            raise H3PromptRewriterUvResolutionExecutionError(
                "uv resolution crossed its plan-bound deadline"
            )
        return returncode


def _execute_h3_prompt_rewriter_uv_resolution_unlocked(
    plan: H3PromptRewriterUvResolutionPlan,
    *,
    expected_plan_sha256: object,
    expected_input_sha256: object,
    expected_uv_sha256: object,
    expected_python_sha256: object,
    uv_executable: object,
    python_executable: object,
    private_feature_root: object,
    state_root: object,
    process_factory: Callable[..., object] = subprocess.Popen,
    monotonic: Callable[[], float] = time.monotonic,
    rss_sampler: Callable[[int], int] | None = None,
    diagnostic: dict[str, object],
) -> dict[str, object]:
    """Run two sequential hash-bound uv views and emit private candidate evidence."""

    diagnostic.update(
        {
            "phase": "pre_spawn",
            "process_spawned": False,
            "returncode": None,
            "validated_pylock_candidate": False,
            "peak_rss_bytes": 0,
        }
    )

    if not isinstance(plan, H3PromptRewriterUvResolutionPlan):
        raise H3PromptRewriterUvResolutionSecurityError("resolution plan is invalid")
    expected_plan = _digest(expected_plan_sha256, "expected plan digest")
    expected_input = _digest(expected_input_sha256, "expected input digest")
    expected_uv = _digest(expected_uv_sha256, "expected uv digest")
    expected_python = _digest(expected_python_sha256, "expected Python digest")
    if not hmac.compare_digest(plan.sha256, expected_plan):
        raise H3PromptRewriterUvResolutionSecurityError(
            "resolution plan digest changed"
        )
    input_payload = reviewed_requirements_input_bytes()
    input_sha = _sha256_bytes(input_payload)
    if (
        not hmac.compare_digest(input_sha, expected_input)
        or plan.document["requirements_input_sha256"] != input_sha
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "requirements input digest changed"
        )
    uv = _inspect_uv(uv_executable)
    python = _inspect_python(python_executable)
    if (
        not hmac.compare_digest(uv.sha256, expected_uv)
        or plan.document["uv"]["sha256"] != uv.sha256
        or not hmac.compare_digest(python.sha256, expected_python)
        or plan.document["bootstrap_python"]["sha256"] != python.sha256
        or plan.document["bootstrap_python"]["size_bytes"] != python.size_bytes
        or plan.document["bootstrap_python"]["version"] != python.version
        or plan.document["bootstrap_python"]["canonical_path_sha256"]
        != _python_path_sha256(python)
        or plan.document["bootstrap_python"]["stat_identity_sha256"]
        != _python_stat_sha256(python)
    ):
        raise H3PromptRewriterUvResolutionSecurityError(
            "resolver executable binding changed"
        )
    _feature, state = _layout(private_feature_root, state_root)
    _atomic_write(state / INPUT_NAME, input_payload)
    started = monotonic()
    process = None
    try:
        for ordinal, hashed_requirements in enumerate((False, True), start=1):
            command = _command(
                uv.path,
                python.path,
                state,
                hashed_requirements=hashed_requirements,
            )
            suffix = "" if ordinal == 1 else f"_{ordinal}"
            diagnostic["phase"] = f"spawn{suffix}"
            diagnostic["returncode"] = None
            try:
                process = process_factory(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    close_fds=True,
                    start_new_session=True,
                    cwd=state,
                    env=_child_environment(state),
                    preexec_fn=_apply_child_limits,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise H3PromptRewriterUvResolutionExecutionError(
                    "uv resolution process could not be started"
                ) from error
            diagnostic["process_spawned"] = True
            diagnostic["phase"] = f"process_monitor{suffix}"
            returncode = _wait_for_resolution(
                process,
                state,
                plan,
                started=started,
                monotonic=monotonic,
                diagnostic=diagnostic,
                rss_sampler=rss_sampler,
            )
            diagnostic["returncode"] = returncode
            if returncode != 0:
                diagnostic["phase"] = f"process_nonzero{suffix}"
                raise H3PromptRewriterUvResolutionExecutionError(
                    "uv resolution failed; private temporary state was preserved"
                )
            diagnostic["phase"] = f"process_cleanup{suffix}"
            _cleanup_process_group(process)
            process = None
            diagnostic["phase"] = f"state_attestation{suffix}"
            resources = plan.document["resources"]
            _scan_private_state(
                state,
                byte_cap=int(resources["metadata_byte_cap"]),
                entry_cap=int(resources["metadata_entry_cap"]),
            )
        resources = plan.document["resources"]
        _scan_private_state(
            state,
            byte_cap=int(resources["metadata_byte_cap"]),
            entry_cap=int(resources["metadata_entry_cap"]),
        )
        diagnostic["phase"] = "pylock_schema"
        temporary_pylock = state / PYLOCK_CANDIDATE_NAME
        _private_file(temporary_pylock, MAX_PYLOCK_BYTES)
        diagnostic["validated_pylock_candidate"] = True
        final_pylock = state / PYLOCK_NAME
        if final_pylock.exists():
            _private_file(final_pylock, MAX_PYLOCK_BYTES)
            if final_pylock.read_bytes() != temporary_pylock.read_bytes():
                raise H3PromptRewriterUvResolutionSecurityError(
                    "new pylock conflicts with preserved resolution evidence"
                )
            temporary_pylock.unlink()
        else:
            os.replace(temporary_pylock, final_pylock)
        pylock_size, pylock_sha, pylock_payload = _hash_private_file(
            final_pylock, MAX_PYLOCK_BYTES
        )
        temporary_requirements = state / HASHED_REQUIREMENTS_CANDIDATE_NAME
        _private_file(temporary_requirements, MAX_HASHED_REQUIREMENTS_BYTES)
        final_requirements = state / HASHED_REQUIREMENTS_NAME
        if final_requirements.exists():
            _private_file(final_requirements, MAX_HASHED_REQUIREMENTS_BYTES)
            if final_requirements.read_bytes() != temporary_requirements.read_bytes():
                raise H3PromptRewriterUvResolutionSecurityError(
                    "new hashed requirements conflict with preserved evidence"
                )
            temporary_requirements.unlink()
        else:
            os.replace(temporary_requirements, final_requirements)
        requirements_size, requirements_sha, requirements_payload = _hash_private_file(
            final_requirements, MAX_HASHED_REQUIREMENTS_BYTES
        )
        deadline = float(plan.document["resources"]["deadline_seconds"])

        def size_resolver(url: str, package: str, index: str, filename: str) -> int:
            remaining = deadline - (monotonic() - started)
            return _head_content_length(
                url,
                package,
                index,
                filename,
                timeout=remaining,
            )

        report = parse_uv_resolution_evidence_to_wheel_report(
            pylock_payload,
            requirements_payload,
            requirements_reference=str(state / INPUT_NAME),
            size_resolver=size_resolver,
        )
        diagnostic["phase"] = "evidence_write"
        report_payload = _canonical_json(report) + b"\n"
        _atomic_write(state / REPORT_NAME, report_payload)
        report_sha = _sha256_bytes(report_payload)
        total_bytes = sum(
            int(item["wheel"]["size_bytes"]) for item in report["packages"]
        )
        provenance = {
            "schema": UV_RESOLUTION_PROVENANCE_SCHEMA,
            "status": "unreviewed_candidate",
            "installation_authorized": False,
            "runtime_execution_authorized": False,
            "plan_sha256": plan.sha256,
            "requirements_input_sha256": input_sha,
            "uv": {
                "version": PINNED_UV_VERSION,
                "sha256": uv.sha256,
                "size_bytes": uv.size_bytes,
                "path": str(uv.path),
                "stat_identity": list(uv.stat_identity),
            },
            "bootstrap_python": {
                "version": python.version,
                "sha256": python.sha256,
                "size_bytes": python.size_bytes,
                "path": str(python.path),
                "stat_identity": list(python.stat_identity),
            },
            "pylock": {
                "path": str(final_pylock),
                "sha256": pylock_sha,
                "size_bytes": pylock_size,
            },
            "hashed_requirements": {
                "path": str(final_requirements),
                "sha256": requirements_sha,
                "size_bytes": requirements_size,
            },
            "wheel_report": {
                "path": str(state / REPORT_NAME),
                "sha256": report_sha,
                "package_count": len(report["packages"]),
                "total_candidate_bytes": total_bytes,
            },
            "resource_contract": plan.document["resources"],
            "peak_rss_bytes": diagnostic["peak_rss_bytes"],
        }
        provenance_payload = _canonical_json(provenance) + b"\n"
        _atomic_write(state / PROVENANCE_NAME, provenance_payload)
        diagnostic["phase"] = "complete"
        return {
            "report": report,
            "report_sha256": report_sha,
            "pylock_sha256": pylock_sha,
            "provenance_sha256": _sha256_bytes(provenance_payload),
            "package_count": len(report["packages"]),
            "total_candidate_bytes": total_bytes,
            "peak_rss_bytes": diagnostic["peak_rss_bytes"],
        }
    finally:
        if process is not None:
            _cleanup_process_group(process)


def _execute_h3_prompt_rewriter_uv_resolution_bound(
    plan: H3PromptRewriterUvResolutionPlan,
    *,
    expected_plan_sha256: object,
    expected_input_sha256: object,
    expected_uv_sha256: object,
    expected_python_sha256: object,
    uv_executable: object,
    python_executable: object,
    private_feature_root: object,
    state_root: object,
    process_factory: Callable[..., object] = subprocess.Popen,
    monotonic: Callable[[], float] = time.monotonic,
    rss_sampler: Callable[[int], int] | None = None,
) -> dict[str, object]:
    """Serialize a fully bound resolution against its private state root."""

    if not isinstance(plan, H3PromptRewriterUvResolutionPlan):
        raise H3PromptRewriterUvResolutionSecurityError("resolution plan is invalid")
    expected_plan = _digest(expected_plan_sha256, "expected plan digest")
    expected_input = _digest(expected_input_sha256, "expected input digest")
    expected_uv = _digest(expected_uv_sha256, "expected uv digest")
    expected_python = _digest(expected_python_sha256, "expected Python digest")
    input_sha = _sha256_bytes(reviewed_requirements_input_bytes())
    _feature, state = _layout(private_feature_root, state_root)
    with _execution_lock(state):
        failure_receipt = state / FAILURE_NAME
        if failure_receipt.exists():
            _private_file(failure_receipt, 16 * 1024)
            raise H3PromptRewriterUvResolutionSecurityError(
                "terminal resolution failure requires a fresh private state root"
            )
        _validate_plan_resource_contract(plan)
        uv = _inspect_uv(uv_executable)
        python = _inspect_python(python_executable)
        if (
            not hmac.compare_digest(plan.sha256, expected_plan)
            or not hmac.compare_digest(input_sha, expected_input)
            or not hmac.compare_digest(uv.sha256, expected_uv)
            or not hmac.compare_digest(python.sha256, expected_python)
            or plan.document["requirements_input_sha256"] != input_sha
            or plan.document["uv"]["sha256"] != uv.sha256
            or plan.document["bootstrap_python"]["sha256"] != python.sha256
            or plan.document["bootstrap_python"]["size_bytes"] != python.size_bytes
            or plan.document["bootstrap_python"]["version"] != python.version
            or plan.document["bootstrap_python"]["canonical_path_sha256"]
            != _python_path_sha256(python)
            or plan.document["bootstrap_python"]["stat_identity_sha256"]
            != _python_stat_sha256(python)
        ):
            raise H3PromptRewriterUvResolutionSecurityError(
                "resolution execution bindings changed"
            )
        diagnostic: dict[str, object] = {
            "phase": "state_reconcile",
            "process_spawned": False,
            "returncode": None,
            "validated_pylock_candidate": False,
            "peak_rss_bytes": 0,
        }
        try:
            _reconcile_pylock_pair(state)
            return _execute_h3_prompt_rewriter_uv_resolution_unlocked(
                plan,
                expected_plan_sha256=expected_plan_sha256,
                expected_input_sha256=expected_input_sha256,
                expected_uv_sha256=expected_uv_sha256,
                expected_python_sha256=expected_python_sha256,
                uv_executable=uv_executable,
                python_executable=python_executable,
                private_feature_root=private_feature_root,
                state_root=state_root,
                process_factory=process_factory,
                monotonic=monotonic,
                rss_sampler=rss_sampler,
                diagnostic=diagnostic,
            )
        except Exception as error:
            try:
                _write_failure_receipt(
                    state,
                    plan,
                    diagnostic,
                    error,
                    input_sha256=input_sha,
                    uv_sha256=uv.sha256,
                    python_sha256=python.sha256,
                )
            except (
                H3PromptRewriterUvResolutionError,
                OSError,
                subprocess.SubprocessError,
            ) as receipt_error:
                raise H3PromptRewriterUvResolutionExecutionError(
                    "private resolution failure receipt could not be written"
                ) from receipt_error
            raise


def execute_h3_prompt_rewriter_uv_resolution(
    plan: H3PromptRewriterUvResolutionPlan,
    *,
    expected_plan_sha256: object,
    expected_input_sha256: object,
    expected_uv_sha256: object,
    expected_python_sha256: object,
    uv_executable: object,
    python_executable: object,
    private_feature_root: object,
    state_root: object,
    process_factory: Callable[..., object] = subprocess.Popen,
    monotonic: Callable[[], float] = time.monotonic,
    rss_sampler: Callable[[int], int] | None = None,
) -> dict[str, object]:
    """Translate all external failures into the content-free local contract."""

    try:
        return _execute_h3_prompt_rewriter_uv_resolution_bound(
            plan,
            expected_plan_sha256=expected_plan_sha256,
            expected_input_sha256=expected_input_sha256,
            expected_uv_sha256=expected_uv_sha256,
            expected_python_sha256=expected_python_sha256,
            uv_executable=uv_executable,
            python_executable=python_executable,
            private_feature_root=private_feature_root,
            state_root=state_root,
            process_factory=process_factory,
            monotonic=monotonic,
            rss_sampler=rss_sampler,
        )
    except H3PromptRewriterUvResolutionError:
        raise
    except (
        OSError,
        subprocess.SubprocessError,
        wheel_resolver.H3PromptRewriterWheelResolverError,
        closure.H3PromptRewriterDependencyClosureError,
    ) as error:
        raise H3PromptRewriterUvResolutionExecutionError(
            "resolution execution failed at a private external boundary"
        ) from error


__all__ = [
    "HASHED_REQUIREMENTS_CANDIDATE_NAME",
    "HASHED_REQUIREMENTS_NAME",
    "PINNED_UV_SHA256",
    "PINNED_UV_SIZE_BYTES",
    "PINNED_UV_VERSION",
    "UV_RESOLUTION_PLAN_SCHEMA",
    "UV_RESOLUTION_PROVENANCE_SCHEMA",
    "H3PromptRewriterUvResolutionError",
    "H3PromptRewriterUvResolutionExecutionError",
    "H3PromptRewriterUvResolutionPlan",
    "H3PromptRewriterUvResolutionSecurityError",
    "build_h3_prompt_rewriter_uv_resolution_plan",
    "execute_h3_prompt_rewriter_uv_resolution",
    "parse_uv_pylock_to_wheel_report",
    "parse_uv_resolution_evidence_to_wheel_report",
    "reviewed_requirements_input_bytes",
]
