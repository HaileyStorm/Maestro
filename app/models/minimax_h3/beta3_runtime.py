"""Model-free admission seam for exact 10Eros MiniMax H3 Beta3 artifacts.

This module validates an authored six-evaluation experiment and binds it to an
owner-private checkpoint receipt. It does not register a loader, wire WGP or
the family handler, or make Beta3 executable.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import os
import stat
import tempfile
from types import MappingProxyType
from typing import Any

from services.h3_10eros_beta3 import get_10eros_beta3_catalog
from services.h3_checkpoint_receipts import (
    CHECKPOINT_CONTRACT_REVISION,
    CHECKPOINT_RECEIPT_SCHEMA_VERSION,
    H3CheckpointIntegrityError,
    recheck_checkpoint_binding,
    verify_checkpoint_integrity,
)


_COMPATIBILITY = "10eros_beta3_turbo_hybrid_runtime_admission"
_PUBLIC_RECEIPT_KEYS = frozenset({
    "verified",
    "sha256",
    "size",
    "family",
    "role",
    "contract_revision",
    "compatibility",
    "receipt_reused",
})
_PRIVATE_BINDING_KEYS = frozenset({
    "schema_version",
    "contract_revision",
    "family",
    "role",
    "expected_sha256",
    "expected_size",
    "path_digest",
    "dev",
    "ino",
    "size",
    "mtime_ns",
    "ctime_ns",
    "uid",
})
_RECEIPT_KEYS = _PUBLIC_RECEIPT_KEYS | {"_checkpoint_binding"}
_BINDING_STRING_KEYS = (
    "contract_revision",
    "family",
    "role",
    "expected_sha256",
    "path_digest",
)
_BINDING_INTEGER_KEYS = (
    "schema_version",
    "expected_size",
    "dev",
    "ino",
    "size",
    "mtime_ns",
    "ctime_ns",
    "uid",
)


class H310ErosBeta3RuntimeAdmissionError(RuntimeError):
    """A Beta3 request does not match its exact unwired runtime contract."""


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _copy(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_copy(item) for item in value]
    return value


def _normalize_pathlike(value: object, *, field: str) -> str:
    """Resolve a PathLike exactly once and reject bytes or exotic primitives."""

    try:
        supplied = os.fspath(value)
        if type(supplied) is not str:
            raise TypeError("path must resolve to an exact string")
        return os.path.abspath(supplied)
    except (TypeError, ValueError, OSError) as error:
        raise H310ErosBeta3RuntimeAdmissionError(
            f"The Beta3 {field} path is invalid"
        ) from error


@dataclass(frozen=True, slots=True)
class Beta3RuntimeRequest:
    """Content-free settings required to admit one exact Beta3 experiment."""

    artifact_id: str
    profile_id: str
    filename: str
    authored_evaluations: int
    sampler: str
    attention_engine: str
    image_references: tuple[object, ...] = ()
    video_references: tuple[object, ...] = ()
    audio_references: tuple[object, ...] = ()
    start_keyframe: object | None = None
    end_keyframe: object | None = None
    activated_loras: tuple[str, ...] = ()
    managed_turbo_profile: str | None = None
    spectrum_profile: str | None = None
    lightx2v_profile: str | None = None
    step_cache: str | None = None
    automatic_fallback: bool = False


class Beta3RuntimeAdmission:
    """Opaque path-free evidence with an in-process-only checkpoint binding."""

    __slots__ = ("__public", "__checkpoint_binding")

    def __init__(
        self, *, public: Mapping[str, Any], checkpoint_binding: Mapping[str, Any],
    ) -> None:
        object.__setattr__(self, "_Beta3RuntimeAdmission__public", _freeze(dict(public)))
        object.__setattr__(
            self,
            "_Beta3RuntimeAdmission__checkpoint_binding",
            _freeze(dict(checkpoint_binding)),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Beta3 runtime admissions are immutable")

    def __repr__(self) -> str:
        return "<Beta3RuntimeAdmission path-free>"

    def public_projection(self) -> dict[str, Any]:
        """Return a serializable projection containing no private path identity."""

        return _copy(self.__public)


def _admission_data(
    admission: object,
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    if type(admission) is not Beta3RuntimeAdmission:
        return None
    return (
        object.__getattribute__(admission, "_Beta3RuntimeAdmission__public"),
        object.__getattribute__(
            admission, "_Beta3RuntimeAdmission__checkpoint_binding",
        ),
    )


def _artifact(request: Beta3RuntimeRequest) -> dict[str, Any]:
    artifacts = {
        item["artifact_id"]: item
        for item in get_10eros_beta3_catalog()["artifacts"]
    }
    artifact = artifacts.get(request.artifact_id)
    if artifact is None:
        raise H310ErosBeta3RuntimeAdmissionError(
            "The Beta3 artifact ID is not registered"
        )
    if request.profile_id != artifact["profile_id"]:
        raise H310ErosBeta3RuntimeAdmissionError(
            "The Beta3 profile does not match the selected artifact"
        )
    if request.filename != artifact["filename"]:
        raise H310ErosBeta3RuntimeAdmissionError(
            "The Beta3 filename does not match the selected artifact"
        )
    return artifact


def _require_exact_request_types(request: Beta3RuntimeRequest) -> None:
    string_fields = (
        "artifact_id", "profile_id", "filename", "sampler", "attention_engine",
    )
    if any(type(getattr(request, field)) is not str for field in string_fields):
        raise H310ErosBeta3RuntimeAdmissionError(
            "Beta3 identity and runtime settings require exact string values"
        )
    if type(request.authored_evaluations) is not int:
        raise H310ErosBeta3RuntimeAdmissionError(
            "Beta3 authored evaluations require an exact integer"
        )
    tuple_fields = (
        "image_references", "video_references", "audio_references",
        "activated_loras",
    )
    if any(type(getattr(request, field)) is not tuple for field in tuple_fields):
        raise H310ErosBeta3RuntimeAdmissionError(
            "Beta3 reference and LoRA settings require exact tuple containers"
        )
    optional_string_fields = (
        "managed_turbo_profile", "spectrum_profile", "lightx2v_profile",
        "step_cache",
    )
    if any(
        value is not None and type(value) is not str
        for value in (getattr(request, field) for field in optional_string_fields)
    ):
        raise H310ErosBeta3RuntimeAdmissionError(
            "Beta3 accelerator settings require exact strings or null"
        )
    if type(request.automatic_fallback) is not bool:
        raise H310ErosBeta3RuntimeAdmissionError(
            "Beta3 automatic fallback requires an exact boolean"
        )


def _validate_request(request: Beta3RuntimeRequest, artifact: Mapping[str, Any]) -> None:
    _require_exact_request_types(request)
    if request.authored_evaluations != 6:
        raise H310ErosBeta3RuntimeAdmissionError(
            "10Eros Beta3 requires exactly six authored evaluations"
        )
    candidates = artifact["maestro_experiment_policy"]["schedule"][
        "sampler_candidates"
    ]
    if request.sampler not in candidates:
        raise H310ErosBeta3RuntimeAdmissionError(
            "10Eros Beta3 requires er_sde/simple or multires/simple"
        )
    if request.attention_engine != "sdpa":
        raise H310ErosBeta3RuntimeAdmissionError(
            "10Eros Beta3 runtime admission is SDPA-only"
        )
    if any((
        request.image_references,
        request.video_references,
        request.audio_references,
        request.start_keyframe is not None,
        request.end_keyframe is not None,
    )):
        raise H310ErosBeta3RuntimeAdmissionError(
            "10Eros Beta3 has no admitted reference or keyframe contract"
        )
    if request.activated_loras:
        raise H310ErosBeta3RuntimeAdmissionError(
            "10Eros Beta3 runtime admission rejects LoRAs"
        )
    accelerators = {
        "managed Turbo": request.managed_turbo_profile,
        "Spectrum": request.spectrum_profile,
        "LightX2V": request.lightx2v_profile,
        "step cache": request.step_cache,
    }
    selected = [name for name, value in accelerators.items() if value is not None]
    if selected:
        raise H310ErosBeta3RuntimeAdmissionError(
            f"10Eros Beta3 rejects incompatible {selected[0]} stacking"
        )
    if request.automatic_fallback is not False:
        raise H310ErosBeta3RuntimeAdmissionError(
            "10Eros Beta3 runtime admission forbids automatic fallback"
        )


def _validate_receipt(
    receipt: object, artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(receipt) is not dict or set(receipt) != _RECEIPT_KEYS:
        raise H310ErosBeta3RuntimeAdmissionError(
            "The Beta3 checkpoint receipt has an unexpected shape"
        )
    public = {key: receipt[key] for key in _PUBLIC_RECEIPT_KEYS}
    if (
        type(public["verified"]) is not bool
        or type(public["receipt_reused"]) is not bool
        or type(public["size"]) is not int
        or any(type(public[key]) is not str for key in (
            "sha256", "family", "role", "contract_revision", "compatibility",
        ))
        or public["verified"] is not True
        or public["sha256"] != artifact["sha256"]
        or public["size"] != artifact["size"]
        or public["family"] != "minimax_h3"
        or public["role"] != "transformer"
        or public["contract_revision"] != CHECKPOINT_CONTRACT_REVISION
        or public["compatibility"] != _COMPATIBILITY
    ):
        raise H310ErosBeta3RuntimeAdmissionError(
            "The Beta3 checkpoint receipt is incomplete or mismatched"
        )
    binding = receipt["_checkpoint_binding"]
    if (
        type(binding) is not dict
        or set(binding) != _PRIVATE_BINDING_KEYS
        or any(type(binding[key]) is not str for key in _BINDING_STRING_KEYS)
        or any(type(binding[key]) is not int for key in _BINDING_INTEGER_KEYS)
        or binding["schema_version"] != CHECKPOINT_RECEIPT_SCHEMA_VERSION
        or binding["contract_revision"] != CHECKPOINT_CONTRACT_REVISION
        or binding["family"] != "minimax_h3"
        or binding["role"] != "transformer"
        or binding["expected_sha256"] != artifact["sha256"]
        or binding["expected_size"] != artifact["size"]
        or binding["size"] != artifact["size"]
        or any(binding[key] < 0 for key in (
            "dev", "ino", "size", "mtime_ns", "ctime_ns",
        ))
        or binding["uid"] < -1
        or len(binding["path_digest"]) != 64
        or any(character not in "0123456789abcdef" for character in binding["path_digest"])
    ):
        raise H310ErosBeta3RuntimeAdmissionError(
            "The Beta3 private checkpoint binding is incomplete or mismatched"
        )
    return public, binding


def admit_beta3_runtime(
    path: str | os.PathLike[str],
    request: Beta3RuntimeRequest,
    *,
    receipt_root: str | os.PathLike[str] | None = None,
) -> Beta3RuntimeAdmission:
    """Verify and bind one exact artifact without enabling runtime execution."""

    if type(request) is not Beta3RuntimeRequest:
        raise H310ErosBeta3RuntimeAdmissionError(
            "An exact typed Beta3 runtime request is required"
        )
    _require_exact_request_types(request)
    artifact = _artifact(request)
    normalized_path = _normalize_pathlike(path, field="checkpoint")
    if os.path.basename(normalized_path) != artifact["filename"]:
        raise H310ErosBeta3RuntimeAdmissionError(
            "The admitted Beta3 path has the wrong filename"
        )
    normalized_receipt_root = (
        None if receipt_root is None else _normalize_pathlike(
            receipt_root, field="receipt root",
        )
    )
    _validate_request(request, artifact)
    try:
        receipt = verify_checkpoint_integrity(
            normalized_path,
            expected_sha256=artifact["sha256"],
            expected_size=artifact["size"],
            compatibility=_COMPATIBILITY,
            family="minimax_h3",
            role="transformer",
            receipt_root=normalized_receipt_root,
            include_private_binding=True,
        )
    except H3CheckpointIntegrityError as error:
        raise H310ErosBeta3RuntimeAdmissionError(str(error)) from error
    public_receipt, binding = _validate_receipt(receipt, artifact)
    try:
        current = recheck_checkpoint_binding(normalized_path, binding)
    except (OSError, TypeError, ValueError):
        current = False
    if current is not True:
        raise H310ErosBeta3RuntimeAdmissionError(
            "The Beta3 checkpoint binding was not current after verification"
        )
    public = {
        "artifact_id": artifact["artifact_id"],
        "profile_id": artifact["profile_id"],
        "filename": artifact["filename"],
        "repository": artifact["repository"],
        "repository_head": artifact["repository_head"],
        "revision": artifact["revision"],
        "mode": artifact["mode"],
        "authored_evaluations": request.authored_evaluations,
        "sampler": request.sampler,
        "attention_engine": request.attention_engine,
        "checkpoint": public_receipt,
        "runtime_admission_ready": True,
        "execution_available": False,
        "enabled_by_default": False,
        "automatic_fallback": False,
        "wgp_wired": False,
        "handler_wired": False,
        "reason": (
            "Exact checkpoint admission is ready for held-descriptor consumption; "
            "WGP and the MiniMax H3 handler remain unwired, so execution is unavailable."
        ),
    }
    return Beta3RuntimeAdmission(
        public=public,
        checkpoint_binding=binding,
    )


def _recheck_normalized_path(normalized_path: str, admission: object) -> bool:
    data = _admission_data(admission)
    if data is None:
        return False
    public, binding = data
    if os.path.basename(normalized_path) != public["filename"]:
        return False
    try:
        return recheck_checkpoint_binding(normalized_path, _copy(binding)) is True
    except (OSError, TypeError, ValueError):
        return False


def recheck_beta3_admission(
    path: str | os.PathLike[str] | None,
    admission: object,
) -> bool:
    """Return status only; loading authority requires hold_beta3_checkpoint()."""

    if path is None:
        return False
    try:
        normalized_path = _normalize_pathlike(path, field="checkpoint")
    except H310ErosBeta3RuntimeAdmissionError:
        return False
    return _recheck_normalized_path(normalized_path, admission)


def _binding_matches_stat(binding: Mapping[str, Any], value: os.stat_result) -> bool:
    return (
        stat.S_ISREG(value.st_mode)
        and int(value.st_dev) == binding["dev"]
        and int(value.st_ino) == binding["ino"]
        and int(value.st_size) == binding["size"]
        and int(value.st_mtime_ns) == binding["mtime_ns"]
        and int(value.st_ctime_ns) == binding["ctime_ns"]
        and int(getattr(value, "st_uid", -1)) == binding["uid"]
    )


def _same_owner(value: os.stat_result) -> bool:
    return os.name != "posix" or int(getattr(value, "st_uid", -1)) == os.geteuid()


def _stat_identity(value: os.stat_result) -> tuple[int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(getattr(value, "st_uid", -1)),
    )


def _bridge_link_matches(
    directory_descriptor: int,
    filename: str,
    *,
    link_identity: tuple[int, int, int],
    target: str,
) -> bool:
    try:
        current = os.lstat(filename, dir_fd=directory_descriptor)
        return (
            stat.S_ISLNK(current.st_mode)
            and _same_owner(current)
            and _stat_identity(current) == link_identity
            and os.readlink(filename, dir_fd=directory_descriptor) == target
        )
    except OSError:
        return False


def _attach_cleanup_failure(
    primary_error: BaseException,
    cleanup_error: BaseException,
) -> None:
    message = f"Beta3 descriptor bridge cleanup also failed: {cleanup_error}"
    add_note = getattr(primary_error, "add_note", None)
    if callable(add_note):  # pragma: no cover - Python 3.11+
        add_note(message)
    try:
        primary_error.__context__ = cleanup_error
    except (AttributeError, TypeError):  # pragma: no cover - exotic exceptions
        pass


@contextmanager
def hold_beta3_checkpoint(
    path: str | os.PathLike[str], admission: object,
) -> Iterator[str]:
    """Hold and yield a suffix-preserving path for a future in-process loader."""

    normalized_path = _normalize_pathlike(path, field="checkpoint")
    data = _admission_data(admission)
    if data is None or not _recheck_normalized_path(normalized_path, admission):
        raise H310ErosBeta3RuntimeAdmissionError(
            "The Beta3 checkpoint binding changed after admission"
        )
    public, binding = data
    if os.name != "posix":  # pragma: no cover - Linux runtime is the admitted host
        raise H310ErosBeta3RuntimeAdmissionError(
            "This host has no admitted same-descriptor Beta3 path contract"
        )
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:  # pragma: no cover - fail closed on unusual POSIX hosts
        raise H310ErosBeta3RuntimeAdmissionError(
            "This host cannot safely hold the Beta3 checkpoint"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow
    try:
        descriptor = os.open(normalized_path, flags)
    except OSError as error:
        raise H310ErosBeta3RuntimeAdmissionError(
            "The admitted Beta3 checkpoint could not be held"
        ) from error
    bridge_directory: str | None = None
    directory_descriptor: int | None = None
    bridge_filename = public["filename"]
    link_identity: tuple[int, int, int] | None = None
    directory_identity: tuple[int, int, int] | None = None
    descriptor_target = f"/proc/self/fd/{descriptor}"
    primary_error: BaseException | None = None
    try:
        try:
            if not _binding_matches_stat(binding, os.fstat(descriptor)):
                raise H310ErosBeta3RuntimeAdmissionError(
                    "The Beta3 checkpoint changed while acquiring its descriptor"
                )
            if not os.path.exists(descriptor_target):
                raise H310ErosBeta3RuntimeAdmissionError(
                    "This host cannot expose a stable Beta3 descriptor path"
                )
            bridge_directory = tempfile.mkdtemp(prefix=".maestro-beta3-held-")
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | nofollow
            )
            directory_descriptor = os.open(bridge_directory, directory_flags)
            directory_lstat = os.lstat(bridge_directory)
            directory_fstat = os.fstat(directory_descriptor)
            directory_identity = _stat_identity(directory_fstat)
            if (
                not stat.S_ISDIR(directory_fstat.st_mode)
                or not _same_owner(directory_fstat)
                or stat.S_IMODE(directory_fstat.st_mode) != 0o700
                or _stat_identity(directory_lstat) != directory_identity
            ):
                raise H310ErosBeta3RuntimeAdmissionError(
                    "The Beta3 descriptor bridge directory is not private"
                )
            try:
                os.symlink(
                    descriptor_target,
                    bridge_filename,
                    dir_fd=directory_descriptor,
                )
            except OSError as error:
                raise H310ErosBeta3RuntimeAdmissionError(
                    "The Beta3 descriptor bridge path was not exclusively created"
                ) from error
            link_stat = os.lstat(bridge_filename, dir_fd=directory_descriptor)
            link_identity = _stat_identity(link_stat)
            proc_directory = f"/proc/self/fd/{directory_descriptor}"
            bridge_path = f"{proc_directory}/{bridge_filename}"
            if (
                not _bridge_link_matches(
                    directory_descriptor,
                    bridge_filename,
                    link_identity=link_identity,
                    target=descriptor_target,
                )
                or _stat_identity(os.stat(proc_directory)) != directory_identity
                or not _binding_matches_stat(binding, os.stat(bridge_path))
            ):
                raise H310ErosBeta3RuntimeAdmissionError(
                    "The Beta3 descriptor bridge does not resolve to the admitted file"
                )
            os.fchmod(directory_descriptor, 0o500)
            if stat.S_IMODE(os.fstat(directory_descriptor).st_mode) != 0o500:
                raise H310ErosBeta3RuntimeAdmissionError(
                    "The Beta3 descriptor bridge could not be sealed"
                )
            yield bridge_path
            followed = os.stat(bridge_path)
            if (
                not _bridge_link_matches(
                    directory_descriptor,
                    bridge_filename,
                    link_identity=link_identity,
                    target=descriptor_target,
                )
                or not stat.S_ISREG(followed.st_mode)
                or int(followed.st_dev) != binding["dev"]
                or int(followed.st_ino) != binding["ino"]
            ):
                raise H310ErosBeta3RuntimeAdmissionError(
                    "The Beta3 descriptor bridge changed during consumption"
                )
        except BaseException as error:
            primary_error = error
            raise
    finally:
        cleanup_error: BaseException | None = None
        if directory_descriptor is not None:
            try:
                os.fchmod(directory_descriptor, 0o700)
                if link_identity is not None and _bridge_link_matches(
                    directory_descriptor,
                    bridge_filename,
                    link_identity=link_identity,
                    target=descriptor_target,
                ):
                    os.unlink(bridge_filename, dir_fd=directory_descriptor)
            except OSError as error:
                cleanup_error = error
            finally:
                try:
                    os.close(directory_descriptor)
                except OSError as error:
                    cleanup_error = cleanup_error or error
        if bridge_directory is not None:
            try:
                current_directory = os.lstat(bridge_directory)
                if (
                    directory_identity is None
                    or not stat.S_ISDIR(current_directory.st_mode)
                    or not _same_owner(current_directory)
                    or _stat_identity(current_directory) != directory_identity
                ):
                    raise H310ErosBeta3RuntimeAdmissionError(
                        "The Beta3 bridge directory path was substituted; foreign state was left intact"
                    )
                os.rmdir(bridge_directory)
            except OSError as error:
                cleanup_error = cleanup_error or error
            except H310ErosBeta3RuntimeAdmissionError as error:
                cleanup_error = cleanup_error or error
        try:
            os.close(descriptor)
        except OSError as error:
            cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            wrapped_cleanup = H310ErosBeta3RuntimeAdmissionError(
                "The Beta3 descriptor bridge could not be cleaned"
            )
            wrapped_cleanup.__cause__ = cleanup_error
            if primary_error is not None:
                _attach_cleanup_failure(primary_error, wrapped_cleanup)
            else:
                raise wrapped_cleanup


__all__ = [
    "Beta3RuntimeAdmission",
    "Beta3RuntimeRequest",
    "H310ErosBeta3RuntimeAdmissionError",
    "admit_beta3_runtime",
    "hold_beta3_checkpoint",
    "recheck_beta3_admission",
]
