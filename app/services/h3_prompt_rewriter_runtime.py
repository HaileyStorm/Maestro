"""Passive compatibility admission and explicit offline H3 child execution.

This module binds a canonical dependency input to passive candidate metadata
and owner-private directory identities.  Candidate names, sizes, and bounded
sidecars are not artifact-byte verification.  No durable byte receipt or
launch-time byte recheck exists in that compatibility representation.  The
separate execution interface requires reviewed byte seals and an exact runtime;
it does not make the passive status executable or imply GPU/human acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
import uuid
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from services import h3_prompt_rewriter as rewriter
from services import h3_prompt_rewriter_dependency_closure as dependency_closure


RUNTIME_SCHEMA = "maestro.h3-prompt-rewriter.runtime-admission.v1"
RUNTIME_ROOT_NAME = "h3-prompt-rewriter"
SUPPORTED_MODES = rewriter.SUPPORTED_MODES
PROCESS_LIFECYCLE_SUPPORTED = False
CANCELLATION_SUPPORTED = False

_LAYOUT_DIRECTORY_NAMES = (
    "generations",
    "staging",
    "state",
    "cache",
    "tmp",
    "home",
)
_PROJECTED_ENVIRONMENT_KEYS = frozenset({
    "PATH",
    "HOME",
    "HF_HOME",
    "HF_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "XDG_CACHE_HOME",
    "TMPDIR",
    "TORCH_HOME",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONPYCACHEPREFIX",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "CUDA_VISIBLE_DEVICES",
    "HIP_VISIBLE_DEVICES",
    "ROCR_VISIBLE_DEVICES",
    "NVIDIA_VISIBLE_DEVICES",
})
_FORBIDDEN_AMBIENT_ENVIRONMENT = re.compile(
    r"(?:^|_)(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)(?:$|_)",
    re.IGNORECASE,
)
_PROVIDER_AMBIENT_ENVIRONMENT = re.compile(
    r"^(?:ANTHROPIC|AWS|AZURE|FAL|GOOGLE|HF|HUGGINGFACE|OPENAI|REPLICATE|"
    r"RUNPOD|TOGETHER)(?:_|$)",
    re.IGNORECASE,
)
_PUBLIC_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:path|filepath|directory|cwd|url|uri|prompt|text|content|image)"
    r"(?:$|_)",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+)"
)
_UNIX_ABSOLUTE_PATH = re.compile(r"(?:^|[\s\"'=])/(?:[^/\s]+(?:/[^/\s]+)*)?")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BLOCKER = re.compile(r"[a-z][a-z0-9_]{0,127}")


class H3PromptRewriterRuntimeError(RuntimeError):
    """A runtime admission input does not satisfy the reviewed contract."""


class H3PromptRewriterRuntimeSecurityError(H3PromptRewriterRuntimeError):
    """A filesystem, environment, or public-projection boundary failed."""


@dataclass(frozen=True, slots=True)
class H3PromptRewriterRuntimeLayout:
    """Private absolute layout for the feature-specific future process."""

    root: Path
    generations: Path
    staging: Path
    state: Path
    cache: Path
    temporary: Path
    home: Path


@dataclass(frozen=True, slots=True)
class H3PromptRewriterPathIdentity:
    """Private owner and inode identity for one admitted directory."""

    role: str
    path: Path
    dev: int
    inode: int
    mode: int
    uid: int


@dataclass(frozen=True, slots=True)
class H3PromptRewriterRuntimePrivateReceipt:
    """Private path-bearing receipt; never a byte or execution receipt."""

    layout: H3PromptRewriterRuntimeLayout
    artifact_trust_root: Path
    adapter_directory: Path
    base_directory: Path
    root_owned_sticky_temp_ancestor_allowed: bool
    identities: tuple[H3PromptRewriterPathIdentity, ...]


class H3PromptRewriterRuntimeAdmission:
    """Opaque blocked admission with an explicit private identity receipt."""

    __slots__ = ("__environment", "__private_receipt", "__public")

    def __init__(
        self,
        *,
        private_receipt: H3PromptRewriterRuntimePrivateReceipt,
        environment: Mapping[str, str],
        public: Mapping[str, object],
    ) -> None:
        object.__setattr__(
            self,
            "_H3PromptRewriterRuntimeAdmission__private_receipt",
            private_receipt,
        )
        object.__setattr__(
            self,
            "_H3PromptRewriterRuntimeAdmission__environment",
            _canonical_json(dict(environment)),
        )
        object.__setattr__(
            self,
            "_H3PromptRewriterRuntimeAdmission__public",
            _canonical_json(dict(public)),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("H3 prompt-rewriter runtime admissions are immutable")

    def __repr__(self) -> str:
        return "<H3PromptRewriterRuntimeAdmission path-free blocked>"

    def public_status(self) -> dict[str, object]:
        """Return the exact path-free, content-free public status."""

        return json.loads(self.__public.decode("ascii"))

    def private_receipt(self) -> H3PromptRewriterRuntimePrivateReceipt:
        """Return private paths only with their immutable stat identities."""

        return self.__private_receipt

    def child_environment(self) -> dict[str, str]:
        """Return a fresh copy of the private, GPU-masked environment data."""

        return json.loads(self.__environment.decode("ascii"))


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sha256_mapping(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _exact_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise H3PromptRewriterRuntimeSecurityError(
            f"{field} must be one lowercase SHA-256 digest"
        )
    return value


def _canonical_existing_directory(value: object, *, field: str) -> Path:
    try:
        supplied = os.fspath(value)
    except (TypeError, ValueError, OSError) as error:
        raise H3PromptRewriterRuntimeSecurityError(
            f"{field} must be one canonical absolute directory"
        ) from error
    if type(supplied) is not str:
        raise H3PromptRewriterRuntimeSecurityError(
            f"{field} must be one canonical absolute directory"
        )
    path = Path(supplied)
    if not path.is_absolute():
        raise H3PromptRewriterRuntimeSecurityError(
            f"{field} must be one canonical absolute directory"
        )
    _assert_no_symlink_components(path)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise H3PromptRewriterRuntimeSecurityError(
            f"{field} is unavailable"
        ) from error
    if resolved != path:
        raise H3PromptRewriterRuntimeSecurityError(
            f"{field} must be canonical and contain no aliases"
        )
    return path


def _assert_no_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = os.lstat(current)
        except OSError as error:
            raise H3PromptRewriterRuntimeSecurityError(
                "runtime directory chain is unavailable"
            ) from error
        if stat.S_ISLNK(info.st_mode):
            raise H3PromptRewriterRuntimeSecurityError(
                "runtime directory chain contains a symlink"
            )


def _validate_safe_ancestor_chain(
    root: Path,
    *,
    allow_root_owned_sticky_temp_ancestor: bool,
) -> tuple[H3PromptRewriterPathIdentity, ...]:
    """Validate ancestors above a private root without making them trust roots.

    The sole permissive-mode exception is an explicit dry-test opt-in for the
    exact conventional ``/tmp`` or ``/var/tmp`` directory when it is root-owned
    and sticky.  The dedicated feature root beneath it must still be owned by
    the caller and mode 0700.  This exception is not a deployment authority.
    """

    if type(allow_root_owned_sticky_temp_ancestor) is not bool:
        raise H3PromptRewriterRuntimeSecurityError(
            "sticky temporary ancestor allowance must be one exact boolean"
        )
    allowed_sticky_paths = {Path("/tmp"), Path("/var/tmp")}
    identities = []
    for path in reversed(root.parents):
        try:
            info = os.lstat(path)
        except OSError as error:
            raise H3PromptRewriterRuntimeSecurityError(
                "runtime ancestor identity is unavailable"
            ) from error
        mode = stat.S_IMODE(info.st_mode)
        expected_owner = info.st_uid in {0, os.geteuid()}
        searchable = bool(
            mode & (
                stat.S_IXUSR
                if info.st_uid == os.geteuid()
                else stat.S_IXOTH
            )
        )
        writable_by_others = bool(mode & (stat.S_IWGRP | stat.S_IWOTH))
        sticky_test_exception = (
            allow_root_owned_sticky_temp_ancestor
            and path in allowed_sticky_paths
            and info.st_uid == 0
            and bool(mode & stat.S_ISVTX)
        )
        if (
            os.name != "posix"
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or not expected_owner
            or not searchable
            or (writable_by_others and not sticky_test_exception)
        ):
            raise H3PromptRewriterRuntimeSecurityError(
                "runtime ancestor chain is not safely owned and searchable"
            )
        identities.append(
            H3PromptRewriterPathIdentity(
                role=f"layout-ancestor:{path}",
                path=path,
                dev=int(info.st_dev),
                inode=int(info.st_ino),
                mode=mode,
                uid=int(info.st_uid),
            )
        )
    return tuple(identities)


def _capture_directory_identity(
    path: Path,
    *,
    role: str,
) -> H3PromptRewriterPathIdentity:
    try:
        info = os.lstat(path)
    except OSError as error:
        raise H3PromptRewriterRuntimeSecurityError(
            "runtime directory identity is unavailable"
        ) from error
    mode = stat.S_IMODE(info.st_mode)
    if (
        os.name != "posix"
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or mode != 0o700
    ):
        raise H3PromptRewriterRuntimeSecurityError(
            "runtime directory chain is not owner-private and searchable"
        )
    return H3PromptRewriterPathIdentity(
        role=role,
        path=path,
        dev=int(info.st_dev),
        inode=int(info.st_ino),
        mode=mode,
        uid=int(info.st_uid),
    )


def _directory_chain(boundary: Path, target: Path) -> tuple[Path, ...]:
    try:
        relative = target.relative_to(boundary)
    except ValueError as error:
        raise H3PromptRewriterRuntimeSecurityError(
            "runtime directory escaped its explicit trust boundary"
        ) from error
    current = boundary
    result = [boundary]
    for component in relative.parts:
        current /= component
        result.append(current)
    return tuple(result)


def _validate_directory_chain(
    boundary: Path,
    target: Path,
    *,
    role_prefix: str,
) -> tuple[H3PromptRewriterPathIdentity, ...]:
    identities = []
    for path in _directory_chain(boundary, target):
        _assert_no_symlink_components(path)
        relative = "." if path == boundary else path.relative_to(boundary).as_posix()
        identities.append(
            _capture_directory_identity(
                path,
                role=f"{role_prefix}:{relative}",
            )
        )
    return tuple(identities)


def _deduplicate_identities(
    identities: tuple[H3PromptRewriterPathIdentity, ...],
) -> tuple[H3PromptRewriterPathIdentity, ...]:
    by_path: dict[Path, H3PromptRewriterPathIdentity] = {}
    for identity in identities:
        prior = by_path.get(identity.path)
        if prior is not None and (
            prior.dev,
            prior.inode,
            prior.mode,
            prior.uid,
        ) != (
            identity.dev,
            identity.inode,
            identity.mode,
            identity.uid,
        ):
            raise H3PromptRewriterRuntimeSecurityError(
                "runtime directory identity changed during validation"
            )
        by_path.setdefault(identity.path, identity)
    return tuple(by_path[path] for path in sorted(by_path, key=lambda item: str(item)))


def _expected_layout(root: Path) -> H3PromptRewriterRuntimeLayout:
    return H3PromptRewriterRuntimeLayout(
        root=root,
        generations=root / "generations",
        staging=root / "staging",
        state=root / "state",
        cache=root / "cache",
        temporary=root / "tmp",
        home=root / "home",
    )


def _validate_layout(
    layout: H3PromptRewriterRuntimeLayout,
    *,
    allow_root_owned_sticky_temp_ancestor: bool = False,
) -> tuple[H3PromptRewriterPathIdentity, ...]:
    if type(layout) is not H3PromptRewriterRuntimeLayout:
        raise H3PromptRewriterRuntimeSecurityError(
            "the private runtime layout has an unexpected type"
        )
    canonical_root = _canonical_existing_directory(
        layout.root,
        field="dedicated feature runtime root",
    )
    if canonical_root.name != RUNTIME_ROOT_NAME:
        raise H3PromptRewriterRuntimeSecurityError(
            "runtime root is not dedicated to the H3 prompt rewriter"
        )
    if layout != _expected_layout(canonical_root):
        raise H3PromptRewriterRuntimeSecurityError(
            "the private runtime layout escaped its dedicated feature root"
        )
    identities = _validate_safe_ancestor_chain(
        canonical_root,
        allow_root_owned_sticky_temp_ancestor=(
            allow_root_owned_sticky_temp_ancestor
        ),
    )
    for name in _LAYOUT_DIRECTORY_NAMES:
        target = layout.temporary if name == "tmp" else getattr(layout, name)
        identities += _validate_directory_chain(
            canonical_root,
            target,
            role_prefix="layout",
        )
    return _deduplicate_identities(identities)


def resolve_h3_prompt_rewriter_runtime_layout(
    runtime_root: str | os.PathLike[str],
    *,
    allow_root_owned_sticky_temp_ancestor: bool = False,
) -> H3PromptRewriterRuntimeLayout:
    """Validate a direct feature root without treating PINOKIO_HOME as trust."""

    root = _canonical_existing_directory(
        runtime_root,
        field="dedicated feature runtime root",
    )
    layout = _expected_layout(root)
    _validate_layout(
        layout,
        allow_root_owned_sticky_temp_ancestor=(
            allow_root_owned_sticky_temp_ancestor
        ),
    )
    return layout


def _validate_artifact_directories(
    artifact_trust_root: object,
    adapter_directory: object,
    base_directory: object,
    *,
    allow_root_owned_sticky_temp_ancestor: bool = False,
) -> tuple[
    Path,
    Path,
    Path,
    tuple[H3PromptRewriterPathIdentity, ...],
]:
    trust_root = _canonical_existing_directory(
        artifact_trust_root,
        field="artifact trust root",
    )
    adapter = _canonical_existing_directory(
        adapter_directory,
        field="adapter candidate directory",
    )
    base = _canonical_existing_directory(
        base_directory,
        field="base candidate directory",
    )
    if adapter == base:
        raise H3PromptRewriterRuntimeSecurityError(
            "adapter and base candidate directories must be distinct"
        )
    identities = _validate_safe_ancestor_chain(
        trust_root,
        allow_root_owned_sticky_temp_ancestor=(
            allow_root_owned_sticky_temp_ancestor
        ),
    ) + _validate_directory_chain(
        trust_root,
        adapter,
        role_prefix="artifact-adapter",
    ) + _validate_directory_chain(
        trust_root,
        base,
        role_prefix="artifact-base",
    )
    return trust_root, adapter, base, _deduplicate_identities(identities)


def build_h3_prompt_rewriter_child_environment(
    layout: H3PromptRewriterRuntimeLayout,
    *,
    ambient_environment: Mapping[str, str] | None = None,
    allow_root_owned_sticky_temp_ancestor: bool = False,
) -> dict[str, str]:
    """Build fixed offline, GPU-masked child-process data only."""

    _validate_layout(
        layout,
        allow_root_owned_sticky_temp_ancestor=(
            allow_root_owned_sticky_temp_ancestor
        ),
    )
    ambient: Mapping[str, str] = {} if ambient_environment is None else ambient_environment
    if not isinstance(ambient, Mapping):
        raise H3PromptRewriterRuntimeSecurityError(
            "ambient environment must be one string mapping"
        )
    for raw_name, raw_value in ambient.items():
        if (
            type(raw_name) is not str
            or not raw_name
            or "\x00" in raw_name
            or "=" in raw_name
            or type(raw_value) is not str
            or "\x00" in raw_value
        ):
            raise H3PromptRewriterRuntimeSecurityError(
                "ambient environment contains an invalid string entry"
            )
        name = raw_name.upper()
        if name == "PYTHONPATH":
            raise H3PromptRewriterRuntimeSecurityError(
                "ambient PYTHONPATH is forbidden"
            )
        if (
            _FORBIDDEN_AMBIENT_ENVIRONMENT.search(name)
            or _PROVIDER_AMBIENT_ENVIRONMENT.search(name)
        ):
            raise H3PromptRewriterRuntimeSecurityError(
                "ambient provider or secret environment is forbidden"
            )

    cache = layout.cache
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(layout.home),
        "HF_HOME": str(cache / "huggingface"),
        "HF_HUB_CACHE": str(cache / "huggingface" / "hub"),
        "TRANSFORMERS_CACHE": str(cache / "huggingface" / "transformers"),
        "XDG_CACHE_HOME": str(cache / "xdg"),
        "TMPDIR": str(layout.temporary),
        "TORCH_HOME": str(cache / "torch"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(cache / "pycache"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "CUDA_VISIBLE_DEVICES": "",
        "HIP_VISIBLE_DEVICES": "",
        "ROCR_VISIBLE_DEVICES": "",
        "NVIDIA_VISIBLE_DEVICES": "void",
    }
    if set(environment) != _PROJECTED_ENVIRONMENT_KEYS:
        raise H3PromptRewriterRuntimeSecurityError(
            "child environment does not match its exact allowlist"
        )
    for key in (
        "HOME",
        "HF_HOME",
        "HF_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "TORCH_HOME",
        "PYTHONPYCACHEPREFIX",
    ):
        path = Path(environment[key])
        if path != layout.root and layout.root not in path.parents:
            raise H3PromptRewriterRuntimeSecurityError(
                "child environment escaped the dedicated feature root"
            )
    return environment


def _mode(value: object) -> str:
    if type(value) is not str or value not in SUPPORTED_MODES:
        raise H3PromptRewriterRuntimeError(
            f"mode must be exactly one of {SUPPORTED_MODES}; Ref2VA is unsupported"
        )
    return value


def _validated_candidate_metadata_status(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise H3PromptRewriterRuntimeError(
            "candidate metadata status is malformed"
        )
    try:
        rewriter.canonical_public_projection(value)
    except (TypeError, ValueError) as error:
        raise H3PromptRewriterRuntimeError(
            "candidate metadata status is malformed"
        ) from error
    return dict(value)


def _validated_dependency_plan(
    payload: object,
    *,
    expected_input_sha256: object,
) -> dependency_closure.H3PromptRewriterDependencyClosurePlan:
    expected = _exact_sha256(
        expected_input_sha256,
        field="expected dependency input",
    )
    try:
        plan = dependency_closure.build_h3_prompt_rewriter_dependency_closure_plan(
            payload,
            expected_input_sha256=expected,
        )
    except dependency_closure.H3PromptRewriterDependencyClosureError as error:
        raise H3PromptRewriterRuntimeError(
            "the exact H3 prompt-rewriter dependency closure was rejected"
        ) from error
    document = plan.document
    expected_receipts = {
        "adapter": rewriter.adapter_descriptor(),
        "base": rewriter.base_descriptor(),
    }
    if (
        document.get("schema") != dependency_closure.DEPENDENCY_PLAN_SCHEMA
        or document.get("status") != "blocked"
        or document.get("mutation") is not False
        or document.get("installability_claimed") is not False
        or document.get("installation_authorized") is not False
        or document.get("execution_authorized") is not False
        or document.get("runtime_accepted") is not False
        or document.get("gpu_accepted") is not False
        or document.get("input_integrity_bound") is not True
        or document.get("input_sha256") != expected
        or document.get("model_receipt_dependencies") != expected_receipts
        or document.get("model_receipts_in_environment_candidates") is not False
    ):
        raise H3PromptRewriterRuntimeError(
            "the dependency plan is not the exact blocked runtime contract"
        )
    return plan


def _public_value_is_path_free(value: object) -> bool:
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str or _PUBLIC_FORBIDDEN_KEY.search(key):
                return False
            if not _public_value_is_path_free(child):
                return False
        return True
    if type(value) in (list, tuple):
        return all(_public_value_is_path_free(item) for item in value)
    if type(value) is str:
        return (
            "/" not in value
            and "\\" not in value
            and _WINDOWS_ABSOLUTE_PATH.search(value) is None
            and _UNIX_ABSOLUTE_PATH.search(value) is None
        )
    return value is None or type(value) in (bool, int)


def _assert_public_status(value: Mapping[str, object]) -> None:
    expected_keys = {
        "schema",
        "mode",
        "state",
        "reason",
        "layout_private",
        "private_identity_recheck_available",
        "offline_environment_projected",
        "candidate_metadata_compatible",
        "artifact_bytes_verified",
        "exact_byte_receipts_available",
        "launch_time_byte_recheck_available",
        "admission_complete",
        "expected_adapter_identity_sha256",
        "expected_base_identity_sha256",
        "candidate_metadata_status_sha256",
        "dependency_plan_sha256",
        "dependency_input_sha256",
        "dependency_blockers",
        "runtime_admission_ready",
        "execution_available",
        "runtime_accepted",
        "gpu_accepted",
        "human_accepted",
        "automatic_fallback",
        "provider_fallback",
        "fallback_used",
        "spawn_supported",
        "cancellation_supported",
        "process_lifecycle_supported",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise H3PromptRewriterRuntimeError("public runtime status is malformed")
    if (
        value["schema"] != RUNTIME_SCHEMA
        or value["mode"] not in SUPPORTED_MODES
        or value["state"] != "blocked"
        or value["reason"] not in {
            "artifact_byte_receipts_missing",
            "candidate_metadata_incomplete",
        }
    ):
        raise H3PromptRewriterRuntimeError(
            "public runtime status identity is malformed"
        )
    for field in (
        "layout_private",
        "private_identity_recheck_available",
        "offline_environment_projected",
    ):
        if value[field] is not True:
            raise H3PromptRewriterRuntimeError(
                "public runtime status private evidence is malformed"
            )
    if type(value["candidate_metadata_compatible"]) is not bool:
        raise H3PromptRewriterRuntimeError(
            "candidate metadata compatibility must be one concrete boolean"
        )
    expected_reason = (
        "artifact_byte_receipts_missing"
        if value["candidate_metadata_compatible"]
        else "candidate_metadata_incomplete"
    )
    if value["reason"] != expected_reason:
        raise H3PromptRewriterRuntimeError(
            "candidate metadata and blocked reason contradict each other"
        )
    for field in (
        "artifact_bytes_verified",
        "exact_byte_receipts_available",
        "launch_time_byte_recheck_available",
        "admission_complete",
        "runtime_admission_ready",
        "execution_available",
        "runtime_accepted",
        "gpu_accepted",
        "human_accepted",
        "automatic_fallback",
        "provider_fallback",
        "fallback_used",
        "spawn_supported",
        "cancellation_supported",
        "process_lifecycle_supported",
    ):
        if value[field] is not False:
            raise H3PromptRewriterRuntimeError(
                "public runtime status must remain concretely blocked"
            )
    for field in (
        "expected_adapter_identity_sha256",
        "expected_base_identity_sha256",
        "candidate_metadata_status_sha256",
        "dependency_plan_sha256",
        "dependency_input_sha256",
    ):
        _exact_sha256(value[field], field=field)
    blockers = value["dependency_blockers"]
    if (
        type(blockers) is not list
        or not blockers
        or blockers != sorted(blockers)
        or len(blockers) != len(set(blockers))
        or any(type(item) is not str or _BLOCKER.fullmatch(item) is None for item in blockers)
        or "durable_reviewed_artifact_receipts_missing" not in blockers
    ):
        raise H3PromptRewriterRuntimeError(
            "dependency blockers must be exact sorted identifiers"
        )
    if not _public_value_is_path_free(value):
        raise H3PromptRewriterRuntimeSecurityError(
            "public runtime status contains private paths or content fields"
        )


def build_h3_prompt_rewriter_runtime_admission(
    runtime_root: str | os.PathLike[str],
    *,
    mode: object,
    artifact_trust_root: str | os.PathLike[str],
    adapter_directory: str | os.PathLike[str],
    base_directory: str | os.PathLike[str],
    dependency_payload: object,
    expected_dependency_input_sha256: object,
    ambient_environment: Mapping[str, str] | None = None,
    allow_root_owned_sticky_temp_ancestor: bool = False,
) -> H3PromptRewriterRuntimeAdmission:
    """Build one blocked admission without reading model bytes or spawning."""

    selected_mode = _mode(mode)
    plan = _validated_dependency_plan(
        dependency_payload,
        expected_input_sha256=expected_dependency_input_sha256,
    )
    layout = resolve_h3_prompt_rewriter_runtime_layout(
        runtime_root,
        allow_root_owned_sticky_temp_ancestor=(
            allow_root_owned_sticky_temp_ancestor
        ),
    )
    layout_identities = _validate_layout(
        layout,
        allow_root_owned_sticky_temp_ancestor=(
            allow_root_owned_sticky_temp_ancestor
        ),
    )
    trust_root, adapter, base, artifact_identities = (
        _validate_artifact_directories(
            artifact_trust_root,
            adapter_directory,
            base_directory,
            allow_root_owned_sticky_temp_ancestor=(
                allow_root_owned_sticky_temp_ancestor
            ),
        )
    )
    candidate_status = _validated_candidate_metadata_status(
        rewriter.inspect_local_candidate(adapter, base)
    )
    environment = build_h3_prompt_rewriter_child_environment(
        layout,
        ambient_environment=ambient_environment,
        allow_root_owned_sticky_temp_ancestor=(
            allow_root_owned_sticky_temp_ancestor
        ),
    )
    final_layout_identities = _validate_layout(
        layout,
        allow_root_owned_sticky_temp_ancestor=(
            allow_root_owned_sticky_temp_ancestor
        ),
    )
    _, _, _, final_artifact_identities = _validate_artifact_directories(
        trust_root,
        adapter,
        base,
        allow_root_owned_sticky_temp_ancestor=(
            allow_root_owned_sticky_temp_ancestor
        ),
    )
    if (
        layout_identities != final_layout_identities
        or artifact_identities != final_artifact_identities
    ):
        raise H3PromptRewriterRuntimeSecurityError(
            "private directory identities changed during admission"
        )
    private_receipt = H3PromptRewriterRuntimePrivateReceipt(
        layout=layout,
        artifact_trust_root=trust_root,
        adapter_directory=adapter,
        base_directory=base,
        root_owned_sticky_temp_ancestor_allowed=(
            allow_root_owned_sticky_temp_ancestor
        ),
        identities=_deduplicate_identities(
            final_layout_identities + final_artifact_identities
        ),
    )
    candidate_metadata_compatible = all(
        candidate_status[field] is True
        for field in (
            "adapter_metadata_compatible",
            "base_metadata_compatible",
            "base_shards_compatible",
        )
    )
    dependency_document = plan.document
    public = {
        "schema": RUNTIME_SCHEMA,
        "mode": selected_mode,
        "state": "blocked",
        "reason": (
            "artifact_byte_receipts_missing"
            if candidate_metadata_compatible
            else "candidate_metadata_incomplete"
        ),
        "layout_private": True,
        "private_identity_recheck_available": True,
        "offline_environment_projected": True,
        "candidate_metadata_compatible": candidate_metadata_compatible,
        "artifact_bytes_verified": False,
        "exact_byte_receipts_available": False,
        "launch_time_byte_recheck_available": False,
        "admission_complete": False,
        "expected_adapter_identity_sha256": _sha256_mapping(
            rewriter.adapter_descriptor()
        ),
        "expected_base_identity_sha256": _sha256_mapping(
            rewriter.base_descriptor()
        ),
        "candidate_metadata_status_sha256": _sha256_mapping(candidate_status),
        "dependency_plan_sha256": plan.sha256,
        "dependency_input_sha256": dependency_document["input_sha256"],
        "dependency_blockers": list(dependency_document["blockers"]),
        "runtime_admission_ready": False,
        "execution_available": False,
        "runtime_accepted": False,
        "gpu_accepted": False,
        "human_accepted": False,
        "automatic_fallback": False,
        "provider_fallback": False,
        "fallback_used": False,
        "spawn_supported": PROCESS_LIFECYCLE_SUPPORTED,
        "cancellation_supported": CANCELLATION_SUPPORTED,
        "process_lifecycle_supported": PROCESS_LIFECYCLE_SUPPORTED,
    }
    _assert_public_status(public)
    return H3PromptRewriterRuntimeAdmission(
        private_receipt=private_receipt,
        environment=environment,
        public=public,
    )


def recheck_h3_prompt_rewriter_runtime_admission(admission: object) -> bool:
    """Recheck private directory identities; this is not a byte receipt."""

    if type(admission) is not H3PromptRewriterRuntimeAdmission:
        return False
    receipt = object.__getattribute__(
        admission,
        "_H3PromptRewriterRuntimeAdmission__private_receipt",
    )
    try:
        layout_identities = _validate_layout(
            receipt.layout,
            allow_root_owned_sticky_temp_ancestor=(
                receipt.root_owned_sticky_temp_ancestor_allowed
            ),
        )
        trust_root, adapter, base, artifact_identities = (
            _validate_artifact_directories(
                receipt.artifact_trust_root,
                receipt.adapter_directory,
                receipt.base_directory,
                allow_root_owned_sticky_temp_ancestor=(
                    receipt.root_owned_sticky_temp_ancestor_allowed
                ),
            )
        )
        current = H3PromptRewriterRuntimePrivateReceipt(
            layout=receipt.layout,
            artifact_trust_root=trust_root,
            adapter_directory=adapter,
            base_directory=base,
            root_owned_sticky_temp_ancestor_allowed=(
                receipt.root_owned_sticky_temp_ancestor_allowed
            ),
            identities=_deduplicate_identities(
                layout_identities + artifact_identities
            ),
        )
    except (OSError, H3PromptRewriterRuntimeError):
        return False
    return current == receipt


def h3_prompt_rewriter_runtime_status(
    runtime_root: str | os.PathLike[str],
    **kwargs: object,
) -> dict[str, object]:
    """Return only the passive public status for one blocked admission."""

    return build_h3_prompt_rewriter_runtime_admission(
        runtime_root,
        **kwargs,
    ).public_status()


__all__ = [
    "CANCELLATION_SUPPORTED",
    "PROCESS_LIFECYCLE_SUPPORTED",
    "RUNTIME_ROOT_NAME",
    "RUNTIME_SCHEMA",
    "SUPPORTED_MODES",
    "H3PromptRewriterPathIdentity",
    "H3PromptRewriterRuntimeAdmission",
    "H3PromptRewriterRuntimeError",
    "H3PromptRewriterRuntimeLayout",
    "H3PromptRewriterRuntimePrivateReceipt",
    "H3PromptRewriterRuntimeSecurityError",
    "build_h3_prompt_rewriter_child_environment",
    "build_h3_prompt_rewriter_runtime_admission",
    "h3_prompt_rewriter_runtime_status",
    "recheck_h3_prompt_rewriter_runtime_admission",
    "resolve_h3_prompt_rewriter_runtime_layout",
]


EXECUTION_SCHEMA = "maestro.h3-prompt-rewriter.executed.v2"
_EXECUTION_PACKAGE_PINS = dict(dependency_closure.ROOT_PACKAGE_PINS)
_TEMPLATE_SHA256 = "0442bbd10deb1119d3a4043b7ce405ece67019235d51d282beb48de9cde13066"
_EXECUTION_TOKEN = object()
_MINTED_RESULTS = weakref.WeakSet()
_MAX_PROTOCOL_BYTES = 262144


class H3PromptRewriterCancelled(H3PromptRewriterRuntimeError):
    """The owned child was stopped and reaped; no candidate is accepted."""


class H3PromptRewriterExecutionResult:
    """Completed, runtime-minted candidates; never a caller text wrapper."""

    __slots__ = ("__document", "__weakref__")

    def __init__(self, token: object, document: Mapping[str, object]) -> None:
        if token is not _EXECUTION_TOKEN:
            raise H3PromptRewriterRuntimeError("execution result requires runtime provenance")
        object.__setattr__(self, "_H3PromptRewriterExecutionResult__document", _canonical_json(document))
        _MINTED_RESULTS.add(self)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("execution results are immutable")

    def __repr__(self) -> str:
        return "H3PromptRewriterExecutionResult(completed=True)"

    def _value(self, key: str):
        return json.loads(self.__document)[key]

    @property
    def request_commitment(self) -> str:
        return self._value("request_commitment")

    @property
    def deterministic_candidate(self) -> str:
        return self._value("deterministic_candidate")

    @property
    def base_candidate(self) -> str:
        return self._value("base_candidate")

    @property
    def adapted_candidate(self) -> str:
        return self._value("adapted_candidate")

    @property
    def execution_receipt_sha256(self) -> str:
        return _sha256_mapping(self.public_receipt())

    def public_receipt(self) -> dict[str, object]:
        return self._value("receipt")


def validate_h3_prompt_rewriter_execution_result(result: object, *, request_commitment: str) -> H3PromptRewriterExecutionResult:
    if type(result) is not H3PromptRewriterExecutionResult or result not in _MINTED_RESULTS or result.request_commitment != request_commitment:
        raise H3PromptRewriterRuntimeError("completed execution does not bind this request")
    receipt = result.public_receipt()
    if receipt.get("schema") != EXECUTION_SCHEMA or receipt.get("completed") is not True:
        raise H3PromptRewriterRuntimeError("execution is not completed")
    if not _public_value_is_path_free(receipt):
        raise H3PromptRewriterRuntimeSecurityError("execution receipt is not content-free")
    return result


def _execution_boundary(cancel_check=None, execution_guard=None) -> None:
    if cancel_check is not None and cancel_check():
        raise H3PromptRewriterCancelled("H3 prompt rewrite cancelled")
    if execution_guard is not None and execution_guard() is not True:
        raise H3PromptRewriterRuntimeSecurityError("execution authority unavailable")


def _owned_child(command, *, cwd: Path, environment: Mapping[str, str], cancel_check=None,
                 execution_guard=None, timeout_seconds=600.0, stop_seconds=2.0,
                 child_reaped_observer=None) -> int:
    """Signal the owned child and confirm its wait before reporting reaping."""
    _execution_boundary(cancel_check, execution_guard)
    if not 0 < timeout_seconds <= 1800 or not 0 < stop_seconds <= 10:
        raise H3PromptRewriterRuntimeError("invalid child deadline")
    # Persist uncertainty before spawning. A failed checkpoint cannot create a
    # child, and a failed final wait must never publish a positive reap receipt.
    if child_reaped_observer is not None:
        child_reaped_observer(False)
    try:
        _execution_boundary(cancel_check, execution_guard)
    except BaseException:
        # The checkpoint may block. Recheck before entering the constructor;
        # failure here proves this child was never spawned.
        if child_reaped_observer is not None:
            child_reaped_observer(True)
        raise
    # Popen can raise after fork. Constructor failure leaves the durable False
    # receipt intact because it does not prove that no child exists.
    child = subprocess.Popen(command, cwd=cwd, env=dict(environment), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    deadline = time.monotonic() + timeout_seconds
    try:
        while child.poll() is None:
            _execution_boundary(cancel_check, execution_guard)
            if time.monotonic() >= deadline:
                raise H3PromptRewriterRuntimeError("H3 prompt-rewriter child timed out")
            time.sleep(0.05)
        _execution_boundary(cancel_check, execution_guard)
        return child.returncode
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=stop_seconds)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=stop_seconds)
        else:
            child.wait()
        if child_reaped_observer is not None:
            child_reaped_observer(True)


def _sealed_file(path: Path, expected: Mapping[str, object], *, cancel_check=None) -> dict[str, object]:
    _assert_no_symlink_components(path)
    if set(expected) != {"size_bytes", "sha256"}:
        raise H3PromptRewriterRuntimeSecurityError("invalid asset seal")
    digest = _exact_sha256(expected["sha256"], field="asset digest")
    if type(expected["size_bytes"]) is not int or expected["size_bytes"] < 1:
        raise H3PromptRewriterRuntimeSecurityError("invalid asset size")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid not in {0, os.getuid()} or before.st_mode & 0o022:
            raise H3PromptRewriterRuntimeSecurityError("unsafe sealed asset")
        if before.st_size != expected["size_bytes"]:
            raise H3PromptRewriterRuntimeSecurityError("asset size changed")
        observed = hashlib.sha256()
        while True:
            _execution_boundary(cancel_check)
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            observed.update(block)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields) or observed.hexdigest() != digest or path.stat().st_ino != before.st_ino:
            raise H3PromptRewriterRuntimeSecurityError("asset bytes changed")
        return {"path": str(path), "size_bytes": before.st_size, "sha256": digest,
                "dev": before.st_dev, "inode": before.st_ino, "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns}
    finally:
        os.close(fd)


def _write_private_protocol(path: Path, document: Mapping[str, object]) -> None:
    data = _canonical_json(document)
    if len(data) > _MAX_PROTOCOL_BYTES:
        raise H3PromptRewriterRuntimeError("private request exceeds bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _read_private_protocol(path: Path) -> dict[str, object]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > _MAX_PROTOCOL_BYTES:
            raise H3PromptRewriterRuntimeSecurityError("unsafe private result")
        data = stream.read(_MAX_PROTOCOL_BYTES + 1)
    result = json.loads(data)
    if type(result) is not dict:
        raise H3PromptRewriterRuntimeError("invalid child result")
    return result


class H3PromptRewriterExecutionAdmission:
    __slots__ = ("__passive", "__configuration")

    def __init__(self, token, passive, configuration):
        if token is not _EXECUTION_TOKEN:
            raise H3PromptRewriterRuntimeError("execution admission requires verification")
        object.__setattr__(self, "_H3PromptRewriterExecutionAdmission__passive", passive)
        object.__setattr__(self, "_H3PromptRewriterExecutionAdmission__configuration", _canonical_json(configuration))

    def __setattr__(self, name, value):
        raise AttributeError("execution admissions are immutable")

    def __repr__(self):
        return "H3PromptRewriterExecutionAdmission(offline=True)"


def _child_exchange(passive, configuration, operation, *, cancel_check=None, execution_guard=None, timeout_seconds=600, result_consumer=None, child_reaped_observer=None):
    if not recheck_h3_prompt_rewriter_runtime_admission(passive):
        raise H3PromptRewriterRuntimeSecurityError("private runtime identity changed")
    stage = passive.private_receipt().layout.staging / uuid.uuid4().hex
    stage.mkdir(mode=0o700)
    request_path, result_path = stage / "request.json", stage / "result.json"
    successful = False
    try:
        payload = dict(configuration, operation=operation, parent_pid=os.getpid())
        _write_private_protocol(request_path, payload)
        environment = passive.child_environment()
        if configuration.get("device") == "cuda":
            if execution_guard is None:
                raise H3PromptRewriterRuntimeSecurityError("GPU execution requires exact authority guard")
            environment["CUDA_VISIBLE_DEVICES"] = configuration["cuda_visible_devices"]
        code = _owned_child([configuration["python_executable"], "-I", "-B", configuration["worker"], str(request_path), str(result_path)],
                            cwd=stage, environment=environment, cancel_check=cancel_check,
                            execution_guard=execution_guard, timeout_seconds=timeout_seconds,
                            child_reaped_observer=child_reaped_observer)
        if code != 0:
            raise H3PromptRewriterRuntimeError("H3 prompt-rewriter child failed")
        result = _read_private_protocol(result_path)
        if result.get("operation") != operation or result.get("nonce") != configuration["nonce"] or result.get("state") != "completed":
            raise H3PromptRewriterRuntimeError("H3 prompt-rewriter child did not complete")
        value = result if result_consumer is None else result_consumer(result)
        _execution_boundary(cancel_check, execution_guard)
        successful = True
        return value
    except BaseException as error:
        try:
            _write_private_protocol(stage / "parent-outcome.json", {
                "state": "cancelled" if isinstance(error, H3PromptRewriterCancelled) else "failed",
                "operation": operation, "nonce": configuration["nonce"],
                "private_diagnostic": {
                    "exception_type": type(error).__name__[:256],
                    "exception_message": str(error).encode("utf-8", "replace")[:8192].decode("utf-8", "ignore"),
                },
            })
        except (OSError, ValueError, H3PromptRewriterRuntimeError):
            pass
        if isinstance(error, H3PromptRewriterRuntimeError) or not isinstance(error, Exception):
            raise
        raise H3PromptRewriterRuntimeError("H3 prompt-rewriter private protocol failed") from None
    finally:
        if successful:
            # Unexpected members survive even after a completed exchange.
            for path in (request_path, result_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            try:
                stage.rmdir()
            except OSError:
                pass


def build_h3_prompt_rewriter_execution_admission(passive, *, python_executable, interpreter_seal,
        asset_seals, template_path, runtime_receipt_sha256, runtime_inventory, device="cpu", cuda_visible_devices="", cancel_check=None, child_reaped_observer=None):
    """Admit installed bytes and metadata, without importing or loading a model.

    asset_seals maps every regular file relative to adapter/base directories to
    a reviewed size/hash seal. Runtime receipt is the controller's separately
    qualified environment receipt, not an installation or GPU grant.
    """
    if type(passive) is not H3PromptRewriterRuntimeAdmission or not recheck_h3_prompt_rewriter_runtime_admission(passive):
        raise H3PromptRewriterRuntimeSecurityError("private runtime admission unavailable")
    if passive.public_status()["candidate_metadata_compatible"] is not True:
        raise H3PromptRewriterRuntimeError("candidate metadata incomplete")
    if device not in {"cpu", "cuda"} or (device == "cuda" and not re.fullmatch(r"[0-9]+", cuda_visible_devices)):
        raise H3PromptRewriterRuntimeError("invalid explicit execution device")
    # Preserve the venv invocation path; resolving its symlink loses its prefix.
    python_path = Path(python_executable).absolute()
    executable = _sealed_file(python_path.resolve(strict=True), interpreter_seal, cancel_check=cancel_check)
    if type(runtime_inventory) is not dict or not runtime_inventory or any(
        type(name) is not str or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None
        or type(version) is not str or re.fullmatch(r"[0-9A-Za-z.+!_-]{1,128}", version) is None
        for name, version in runtime_inventory.items()
    ) or any(runtime_inventory.get(name) != version for name, version in _EXECUTION_PACKAGE_PINS.items()):
        raise H3PromptRewriterRuntimeError("qualified runtime inventory does not match selected pins")
    receipt = passive.private_receipt()
    seals = []
    model_directories = []
    expected_weights = {"adapter/" + rewriter.ADAPTER_FILENAME: {"size_bytes": rewriter.ADAPTER_SIZE_BYTES, "sha256": rewriter.ADAPTER_SHA256}}
    expected_weights.update({"base/" + name: {"size_bytes": size, "sha256": digest} for name, size, digest in rewriter.BASE_SHARDS})
    if type(asset_seals) is not dict or any(asset_seals.get(name) != seal for name, seal in expected_weights.items()):
        raise H3PromptRewriterRuntimeSecurityError("reviewed model weight identity missing")
    observed_names = set()
    for label, directory in (("adapter", receipt.adapter_directory), ("base", receipt.base_directory)):
        directories = [directory] + [path for path in sorted(directory.rglob("*")) if path.is_dir() and not path.is_symlink()]
        for path in directories:
            identity = _capture_directory_identity(path, role="model-directory")
            model_directories.append({"path": str(path), "dev": identity.dev, "inode": identity.inode,
                                      "mode": identity.mode, "uid": identity.uid})
        for path in sorted(directory.rglob("*")):
            if path.is_dir() and not path.is_symlink():
                continue
            name = label + "/" + path.relative_to(directory).as_posix()
            observed_names.add(name)
            if name not in asset_seals:
                raise H3PromptRewriterRuntimeSecurityError("unsealed model member")
            seals.append(dict(_sealed_file(path, asset_seals[name], cancel_check=cancel_check), asset_id=name))
    if observed_names != set(asset_seals):
        raise H3PromptRewriterRuntimeSecurityError("asset manifest does not match installed files")
    template = Path(template_path).absolute()
    if not template.is_relative_to(receipt.adapter_directory):
        raise H3PromptRewriterRuntimeSecurityError("template outside reviewed adapter")
    template_seal = next((seal for seal in seals if seal["path"] == str(template)), None)
    if template_seal is None or template_seal["sha256"] != _TEMPLATE_SHA256:
        raise H3PromptRewriterRuntimeSecurityError("reviewed prompt template missing")
    worker = Path(__file__).with_name("h3_prompt_rewriter_worker.py").resolve()
    configuration = {"nonce": uuid.uuid4().hex, "python_executable": str(python_path), "executable": executable,
                     "worker": str(worker), "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
                     "runtime_receipt_sha256": _exact_sha256(runtime_receipt_sha256, field="runtime receipt"),
                     "package_pins": _EXECUTION_PACKAGE_PINS, "runtime_inventory": runtime_inventory, "assets": seals,
                     "model_directories": model_directories,
                     "base_directory": str(receipt.base_directory), "adapter_directory": str(receipt.adapter_directory),
                     "template_path": str(template), "device": device, "cuda_visible_devices": cuda_visible_devices}
    # The metadata probe stays GPU-masked even for a future CUDA admission.
    probe_config = dict(configuration, device="cpu")
    configuration["runtime_metadata_sha256"] = _child_exchange(
        passive, probe_config, "probe", cancel_check=cancel_check, timeout_seconds=30,
        child_reaped_observer=child_reaped_observer,
        result_consumer=lambda result: _sha256_mapping(result["runtime"]))
    return H3PromptRewriterExecutionAdmission(_EXECUTION_TOKEN, passive, configuration)


def execute_h3_prompt_rewrite(admission, request, *, image_bindings=(), duration=10, resolution=None,
        max_new_tokens=4096, min_pixels=65536, max_pixels=1048576, seed=42, greedy=True,
        cancel_check=None, execution_guard=None, timeout_seconds=600, child_reaped_observer=None):
    """Execute explicit base/adapted comparisons; never apply or fall back."""
    if type(admission) is not H3PromptRewriterExecutionAdmission:
        raise H3PromptRewriterRuntimeError("verified execution admission required")
    canonical = rewriter.validate_rewrite_request(request)
    passive = admission._H3PromptRewriterExecutionAdmission__passive
    configuration = json.loads(admission._H3PromptRewriterExecutionAdmission__configuration)
    if canonical["mode"] != passive.public_status()["mode"]:
        raise H3PromptRewriterRuntimeError("request mode differs from admission")
    if type(duration) is not int or not 4 <= duration <= 15 or type(max_new_tokens) is not int or not 1 <= max_new_tokens <= 4096 or type(seed) is not int or not 0 <= seed < 2**32 or type(greedy) is not bool:
        raise H3PromptRewriterRuntimeError("invalid frozen generation controls")
    expected_resolution = "16:9" if canonical["mode"] == "t2va" else "adaptive"
    if resolution not in {None, expected_resolution} or (min_pixels, max_pixels) != (65536, 1048576):
        raise H3PromptRewriterRuntimeError("unsupported image controls")
    if len(image_bindings) != len(canonical["image_roles"]):
        raise H3PromptRewriterRuntimeSecurityError("ordered authorized images required")
    images = []
    for role, binding in zip(canonical["image_roles"], image_bindings):
        if set(binding) != {"input_id", "path", "trust_root", "seal"} or binding["input_id"] != role["input_id"]:
            raise H3PromptRewriterRuntimeSecurityError("image request binding differs")
        path = Path(binding["path"]).absolute()
        trust_root = _canonical_existing_directory(binding["trust_root"], field="image trust root")
        _capture_directory_identity(trust_root, role="authorized-image-root")
        if not path.is_relative_to(trust_root):
            raise H3PromptRewriterRuntimeSecurityError("image outside authorized trust root")
        images.append(dict(_sealed_file(path, binding["seal"], cancel_check=cancel_check), input_id=binding["input_id"]))
    _execution_boundary(cancel_check, execution_guard)
    configuration.update(request=canonical, images=images, controls={"duration": duration, "resolution": expected_resolution,
                         "max_new_tokens": max_new_tokens, "min_pixels": min_pixels, "max_pixels": max_pixels,
                         "seed": seed, "greedy": greedy, "temperature": 0.7, "top_p": 0.8})
    def completed_result(result):
        if result.get("request_commitment") != canonical["commitment"] or result.get("runtime_metadata_sha256") != configuration["runtime_metadata_sha256"]:
            raise H3PromptRewriterRuntimeSecurityError("execution result binding differs")
        for key in ("base_candidate", "adapted_candidate"):
            if type(result.get(key)) is not str or not result[key].strip() or len(result[key].encode()) > 65536:
                raise H3PromptRewriterRuntimeError("child candidate invalid")
        receipt = {"schema": EXECUTION_SCHEMA, "completed": True, "base_executed": True, "adapter_executed": True,
                   "fallback_used": False, "gpu_accepted": False, "human_accepted": False,
                   "request_commitment": canonical["commitment"], "runtime_metadata_sha256": configuration["runtime_metadata_sha256"],
                   "runtime_receipt_sha256": configuration["runtime_receipt_sha256"], "worker_sha256": configuration["worker_sha256"],
                   "asset_manifest_sha256": _sha256_mapping([{ "asset_id": seal["asset_id"], "size_bytes": seal["size_bytes"], "sha256": seal["sha256"]} for seal in configuration["assets"]]),
                   "reference_manifest_sha256": _sha256_mapping([{ "input_id": seal["input_id"], "size_bytes": seal["size_bytes"], "sha256": seal["sha256"]} for seal in images]),
                   "controls_sha256": _sha256_mapping(configuration["controls"]), "device": configuration["device"]}
        _execution_boundary(cancel_check, execution_guard)
        return H3PromptRewriterExecutionResult(_EXECUTION_TOKEN, {"request_commitment": canonical["commitment"],
            "deterministic_candidate": canonical["original_prompt"], "base_candidate": result["base_candidate"],
            "adapted_candidate": result["adapted_candidate"], "receipt": receipt})

    return _child_exchange(passive, configuration, "rewrite", cancel_check=cancel_check,
                           execution_guard=execution_guard, timeout_seconds=timeout_seconds,
                           child_reaped_observer=child_reaped_observer,
                           result_consumer=completed_result)


__all__ += ["EXECUTION_SCHEMA", "H3PromptRewriterCancelled", "H3PromptRewriterExecutionAdmission",
            "H3PromptRewriterExecutionResult", "build_h3_prompt_rewriter_execution_admission",
            "execute_h3_prompt_rewrite", "validate_h3_prompt_rewriter_execution_result"]
