"""Blocked, source-only runtime admission for the H3 prompt rewriter.

This module binds a canonical dependency input to passive candidate metadata
and owner-private directory identities.  Candidate names, sizes, and bounded
sidecars are not artifact-byte verification.  No durable byte receipt or
launch-time byte recheck exists in this slice, so execution, GPU acceptance,
process lifecycle, and cancellation remain unavailable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
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
