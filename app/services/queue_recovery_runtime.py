"""Project-local request manifests and recovery-unit artifact validation.

The queue journal stores only an opaque owner/project identity and a relative
pointer to one of these manifests.  Prompts, absolute input paths, and the
remaining generation parameters stay inside the owning project in a mode-0600
file.  This module is deliberately model-free so crash boundaries can be
tested without importing :mod:`launch` or WanGP.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any
import uuid


MANIFEST_SCHEMA = 1
RECOVERY_DESCRIPTOR_SCHEMA = 1
MANIFEST_DIRECTORY = ".maestro-recovery"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_RECOVERY_ATTEMPTS = 3

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNIT_ID_RE = re.compile(r"^unit:v1:[0-9a-f]{64}$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_MANIFEST_REVISION_RE = re.compile(r"^[0-9a-f]{32}$")


class QueueRecoveryRuntimeError(RuntimeError):
    """Raised when project-local recovery evidence is unsafe or invalid."""


def _canonical_json(value: Any) -> bytes:
    """Return deterministic plain JSON, rejecting runtime and non-finite data."""
    active: set[int] = set()
    nodes = 0

    def visit(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > 250_000 or depth > 32:
            raise QueueRecoveryRuntimeError("Recovery manifest is too complex.")
        if item is None or type(item) in {bool, int, str}:
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise QueueRecoveryRuntimeError("Recovery manifest contains a non-finite number.")
            return item
        if type(item) is list:
            identity = id(item)
            if identity in active:
                raise QueueRecoveryRuntimeError("Recovery manifest contains a cycle.")
            active.add(identity)
            try:
                return [visit(child, depth + 1) for child in item]
            finally:
                active.remove(identity)
        if type(item) is dict:
            identity = id(item)
            if identity in active:
                raise QueueRecoveryRuntimeError("Recovery manifest contains a cycle.")
            active.add(identity)
            try:
                result: dict[str, Any] = {}
                for key, child in item.items():
                    if type(key) is not str or not key or len(key) > 256:
                        raise QueueRecoveryRuntimeError("Recovery manifest contains an invalid key.")
                    result[key] = visit(child, depth + 1)
                return result
            finally:
                active.remove(identity)
        raise QueueRecoveryRuntimeError("Recovery manifest contains runtime-only data.")

    clean = visit(value, 0)
    try:
        return json.dumps(
            clean,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, OverflowError):
        raise QueueRecoveryRuntimeError("Recovery manifest is not JSON-safe.") from None


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sha256_file(path: os.PathLike[str] | str) -> tuple[int, str]:
    """Hash one regular single-link file through a no-follow descriptor."""
    requested = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(requested, flags)
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery evidence could not be opened safely.") from None
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise QueueRecoveryRuntimeError("Recovery evidence is not a safe regular file.")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        refreshed = os.fstat(descriptor)
        current = os.lstat(requested)
        if (
            not stat.S_ISREG(refreshed.st_mode)
            or refreshed.st_nlink != 1
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (refreshed.st_dev, refreshed.st_ino, refreshed.st_size, refreshed.st_mtime_ns)
            or (current.st_dev, current.st_ino) != (refreshed.st_dev, refreshed.st_ino)
        ):
            raise QueueRecoveryRuntimeError("Recovery evidence changed while it was hashed.")
        return int(refreshed.st_size), digest.hexdigest()
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery evidence could not be read safely.") from None
    finally:
        os.close(descriptor)


def _read_exact_file(
    path: os.PathLike[str] | str,
    *,
    maximum_bytes: int,
) -> bytes:
    """Read one unchanged regular file through the same no-follow handle."""
    requested = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(requested, flags)
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery evidence could not be opened safely.") from None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size < 1
            or opened.st_size > maximum_bytes
        ):
            raise QueueRecoveryRuntimeError("Recovery evidence has an unsafe size or type.")
        chunks: list[bytes] = []
        remaining = int(opened.st_size)
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise QueueRecoveryRuntimeError("Recovery evidence ended unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise QueueRecoveryRuntimeError("Recovery evidence changed while it was read.")
        refreshed = os.fstat(descriptor)
        current = os.lstat(requested)
        if (
            (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (refreshed.st_dev, refreshed.st_ino, refreshed.st_size, refreshed.st_mtime_ns)
            or (current.st_dev, current.st_ino) != (refreshed.st_dev, refreshed.st_ino)
        ):
            raise QueueRecoveryRuntimeError("Recovery evidence changed while it was read.")
        return b"".join(chunks)
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery evidence could not be read safely.") from None
    finally:
        os.close(descriptor)


def _open_private_directory(path: Path) -> int:
    """Open one unchanged real private directory without following its leaf."""
    try:
        before = os.lstat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Recovery manifest directory is unsafe."
        ) from None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (os.name != "nt" and stat.S_IMODE(opened.st_mode) & 0o077)
        ):
            raise QueueRecoveryRuntimeError(
                "Recovery manifest directory is unsafe."
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _manifest_dir_fd_supported() -> bool:
    supported = getattr(os, "supports_dir_fd", set())
    return all(function in supported for function in (os.open, os.stat, os.unlink))


def _private_directory_identity(path: Path) -> tuple[int, int]:
    """Validate a no-link private directory for platforms without openat."""
    try:
        info = os.lstat(path)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or os.path.normcase(os.path.realpath(path))
            != os.path.normcase(os.path.abspath(path))
            or (os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077)
        ):
            raise QueueRecoveryRuntimeError(
                "Recovery manifest directory is unsafe."
            )
        return info.st_dev, info.st_ino
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Recovery manifest directory is unsafe."
        ) from None


def _read_exact_file_at(
    directory_descriptor: int,
    filename: str,
    *,
    maximum_bytes: int,
) -> bytes:
    """Read one unchanged manifest relative to a no-follow directory handle."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Recovery evidence could not be opened safely."
        ) from None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size < 1
            or opened.st_size > maximum_bytes
        ):
            raise QueueRecoveryRuntimeError(
                "Recovery evidence has an unsafe size or type."
            )
        chunks: list[bytes] = []
        remaining = int(opened.st_size)
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise QueueRecoveryRuntimeError(
                    "Recovery evidence ended unexpectedly."
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise QueueRecoveryRuntimeError(
                "Recovery evidence changed while it was read."
            )
        refreshed = os.fstat(descriptor)
        current = os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (refreshed.st_dev, refreshed.st_ino, refreshed.st_size, refreshed.st_mtime_ns)
            or (current.st_dev, current.st_ino) != (refreshed.st_dev, refreshed.st_ino)
        ):
            raise QueueRecoveryRuntimeError(
                "Recovery evidence changed while it was read."
            )
        return b"".join(chunks)
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Recovery evidence could not be read safely."
        ) from None
    finally:
        os.close(descriptor)


def _validated_project_root(project_directory: os.PathLike[str] | str) -> Path:
    root = Path(os.path.abspath(os.fspath(project_directory)))
    try:
        info = os.lstat(root)
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery project is missing.") from None
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise QueueRecoveryRuntimeError("Recovery project is unsafe.")
    return root


def _ensure_private_directory(path: Path) -> tuple[Path, tuple[int, int]]:
    """Create/open one real mode-0700 directory and return its identity."""
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise QueueRecoveryRuntimeError("Recovery directory is unsafe.")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise QueueRecoveryRuntimeError("Recovery directory changed during access.")
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
        return path, (info.st_dev, info.st_ino)
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery directory is unavailable.") from None


def _verify_directory_identity(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = os.lstat(path)
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery directory changed during access.") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (info.st_dev, info.st_ino) != identity
    ):
        raise QueueRecoveryRuntimeError("Recovery directory changed during access.")


def _manifest_relative_path(
    job_id: str,
    revision: str | None = None,
) -> str:
    if not isinstance(job_id, str) or _JOB_ID_RE.fullmatch(job_id) is None:
        raise QueueRecoveryRuntimeError("Recovery job identity is invalid.")
    if revision is None:
        filename = f"{job_id}.request.json"
    elif (
        not isinstance(revision, str)
        or _MANIFEST_REVISION_RE.fullmatch(revision) is None
    ):
        raise QueueRecoveryRuntimeError("Recovery manifest revision is invalid.")
    else:
        filename = f"{job_id}.{revision}.request.json"
    return f"{MANIFEST_DIRECTORY}/{filename}"


def _pointer_relative_path(
    pointer: Mapping[str, Any],
    *,
    expected_job_id: str,
) -> str:
    """Accept the initial pointer or one immutable versioned successor."""
    relative = pointer.get("path") if isinstance(pointer, Mapping) else None
    if not isinstance(relative, str):
        raise QueueRecoveryRuntimeError("Recovery manifest pointer is invalid.")
    path = PurePosixPath(relative)
    if path.parent != PurePosixPath(MANIFEST_DIRECTORY):
        raise QueueRecoveryRuntimeError("Recovery manifest pointer is invalid.")
    initial = _manifest_relative_path(expected_job_id)
    if relative == initial:
        return relative
    prefix = f"{expected_job_id}."
    suffix = ".request.json"
    name = path.name
    if not name.startswith(prefix) or not name.endswith(suffix):
        raise QueueRecoveryRuntimeError("Recovery manifest pointer is invalid.")
    revision = name[len(prefix):-len(suffix)]
    if _MANIFEST_REVISION_RE.fullmatch(revision) is None:
        raise QueueRecoveryRuntimeError("Recovery manifest pointer is invalid.")
    return relative


def atomic_write_request_manifest(
    project_directory: os.PathLike[str] | str,
    *,
    job_id: str,
    params: Mapping[str, Any],
    inputs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Write a project-local mode-0600 manifest and return its safe pointer."""
    return _write_request_manifest(
        project_directory,
        job_id=job_id,
        params=params,
        inputs=inputs,
        revision=None,
        create_only=False,
    )


def write_sealed_request_manifest(
    project_directory: os.PathLike[str] | str,
    *,
    job_id: str,
    params: Mapping[str, Any],
    inputs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create one immutable private manifest version for prepared content."""
    return _write_request_manifest(
        project_directory,
        job_id=job_id,
        params=params,
        inputs=inputs,
        revision=uuid.uuid4().hex,
        create_only=True,
    )


def _write_request_manifest(
    project_directory: os.PathLike[str] | str,
    *,
    job_id: str,
    params: Mapping[str, Any],
    inputs: Sequence[Mapping[str, Any]],
    revision: str | None,
    create_only: bool,
) -> dict[str, Any]:
    root = _validated_project_root(project_directory)
    relative = _manifest_relative_path(job_id, revision)
    recovery_dir = root / MANIFEST_DIRECTORY
    try:
        recovery_dir, recovery_identity = _ensure_private_directory(recovery_dir)
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery manifest directory is unavailable.") from None

    payload = {
        "inputs": [dict(item) for item in inputs],
        "job_id": job_id,
        "params": dict(params),
        "schema": MANIFEST_SCHEMA,
    }
    encoded = _canonical_json(payload)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise QueueRecoveryRuntimeError("Recovery manifest exceeds its size limit.")
    destination = root / PurePosixPath(relative)
    descriptor = -1
    temporary = ""
    replaced = False
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{job_id}.", suffix=".tmp", dir=recovery_dir,
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short manifest write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _verify_directory_identity(recovery_dir, recovery_identity)
        if create_only:
            # The version name is random and must never replace prior sealed
            # content. Linking a fully fsynced temporary file publishes it
            # atomically; an unreferenced crash orphan is cleaned on restart.
            os.link(temporary, destination)
            os.unlink(temporary)
            temporary = ""
        else:
            os.replace(temporary, destination)
            temporary = ""
        replaced = True
        os.chmod(destination, 0o600)
        _fsync_directory(recovery_dir)
    except (OSError, QueueRecoveryRuntimeError):
        raise QueueRecoveryRuntimeError("Recovery manifest could not be committed.") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary and not replaced:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return {
        "path": relative,
        "schema": MANIFEST_SCHEMA,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size": len(encoded),
    }


def load_request_manifest(
    project_directory: os.PathLike[str] | str,
    pointer: Mapping[str, Any],
    *,
    expected_job_id: str,
) -> dict[str, Any]:
    """Load one exact manifest after relative path, size, hash, and schema checks."""
    root = _validated_project_root(project_directory)
    if not isinstance(pointer, Mapping) or set(pointer) != {"path", "schema", "sha256", "size"}:
        raise QueueRecoveryRuntimeError("Recovery manifest pointer is invalid.")
    expected_relative = _pointer_relative_path(
        pointer,
        expected_job_id=expected_job_id,
    )
    if pointer.get("schema") != MANIFEST_SCHEMA:
        raise QueueRecoveryRuntimeError("Recovery manifest pointer is invalid.")
    digest = pointer.get("sha256")
    size = pointer.get("size")
    if (
        not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
        or type(size) is not int
        or size < 1
        or size > MAX_MANIFEST_BYTES
    ):
        raise QueueRecoveryRuntimeError("Recovery manifest pointer is invalid.")
    filename = PurePosixPath(expected_relative).name
    recovery_directory = root / MANIFEST_DIRECTORY
    if _manifest_dir_fd_supported():
        directory_descriptor = _open_private_directory(recovery_directory)
        try:
            raw = _read_exact_file_at(
                directory_descriptor,
                filename,
                maximum_bytes=MAX_MANIFEST_BYTES,
            )
        finally:
            os.close(directory_descriptor)
    else:
        identity = _private_directory_identity(recovery_directory)
        raw = _read_exact_file(
            recovery_directory / filename,
            maximum_bytes=MAX_MANIFEST_BYTES,
        )
        _verify_directory_identity(recovery_directory, identity)
    actual_size = len(raw)
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_size != size or actual_digest != digest:
        raise QueueRecoveryRuntimeError("Recovery manifest hash does not match.")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        raise QueueRecoveryRuntimeError("Recovery manifest is malformed.") from None
    if (
        type(manifest) is not dict
        or set(manifest) != {"inputs", "job_id", "params", "schema"}
        or manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("job_id") != expected_job_id
        or type(manifest.get("params")) is not dict
        or type(manifest.get("inputs")) is not list
        or any(type(item) is not dict for item in manifest["inputs"])
        or _canonical_json(manifest) != raw
    ):
        raise QueueRecoveryRuntimeError("Recovery manifest schema is invalid.")
    return manifest


def discover_request_manifest_pointers(
    project_directory: os.PathLike[str] | str,
    *,
    expected_sha256: str = "",
    maximum_candidates: int = 128,
) -> list[dict[str, Any]]:
    """Return bounded content-free pointers for valid sealed manifests.

    This is a host-local recovery primitive, not a public listing API.  It
    never returns params or inputs, and every candidate is reloaded through
    the same no-follow/hash/schema validation as a journal-owned pointer.
    """
    root = _validated_project_root(project_directory)
    recovery_directory = root / MANIFEST_DIRECTORY
    expected = str(expected_sha256 or "")
    if expected and _SHA256_RE.fullmatch(expected) is None:
        raise QueueRecoveryRuntimeError(
            "Recovery manifest assertion is invalid."
        )
    limit = max(1, min(1024, int(maximum_candidates)))
    if not os.path.lexists(recovery_directory):
        return []
    directory_descriptor = None
    identity = None
    try:
        if _manifest_dir_fd_supported():
            directory_descriptor = _open_private_directory(
                recovery_directory
            )
            names = sorted(os.listdir(directory_descriptor))
        else:
            identity = _private_directory_identity(recovery_directory)
            names = sorted(os.listdir(recovery_directory))
            _verify_directory_identity(recovery_directory, identity)
    except OSError:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
            directory_descriptor = None
        raise QueueRecoveryRuntimeError(
            "Recovery manifest discovery is unavailable."
        ) from None
    manifest_names = [
        name for name in names
        if isinstance(name, str)
        and name.endswith(".request.json")
        and name == os.path.basename(name)
    ]
    if len(manifest_names) > limit:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
            directory_descriptor = None
        raise QueueRecoveryRuntimeError(
            "Recovery manifest candidate set is too large."
        )
    discovered: list[dict[str, Any]] = []
    try:
        for name in manifest_names:
            raw = (
                _read_exact_file_at(
                    directory_descriptor, name,
                    maximum_bytes=MAX_MANIFEST_BYTES,
                )
                if directory_descriptor is not None
                else _read_exact_file(
                    recovery_directory / name,
                    maximum_bytes=MAX_MANIFEST_BYTES,
                )
            )
            if identity is not None:
                _verify_directory_identity(recovery_directory, identity)
            size = len(raw)
            digest = hashlib.sha256(raw).hexdigest()
            if expected and digest != expected:
                continue
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError, TypeError):
                raise QueueRecoveryRuntimeError(
                    "Recovery manifest discovery found malformed evidence."
                ) from None
            job_id = payload.get("job_id") if isinstance(payload, dict) else None
            pointer = {
                "path": f"{MANIFEST_DIRECTORY}/{name}",
                "schema": MANIFEST_SCHEMA,
                "sha256": digest,
                "size": size,
            }
            if (
                not isinstance(job_id, str)
                or type(payload) is not dict
                or set(payload) != {"inputs", "job_id", "params", "schema"}
                or payload.get("schema") != MANIFEST_SCHEMA
                or type(payload.get("params")) is not dict
                or type(payload.get("inputs")) is not list
                or any(type(item) is not dict for item in payload["inputs"])
                or _canonical_json(payload) != raw
            ):
                raise QueueRecoveryRuntimeError(
                    "Recovery manifest discovery found invalid evidence."
                )
            _pointer_relative_path(pointer, expected_job_id=job_id)
            discovered.append({"job_id": job_id, "pointer": pointer})
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
    return discovered


def validate_manifest_inputs(
    manifest: Mapping[str, Any],
    validator: Callable[[Mapping[str, Any]], bool],
) -> None:
    """Fail closed unless every recorded input still has matching owner evidence."""
    inputs = manifest.get("inputs") if isinstance(manifest, Mapping) else None
    if not isinstance(inputs, list):
        raise QueueRecoveryRuntimeError("Recovery input manifest is invalid.")
    for descriptor in inputs:
        if not isinstance(descriptor, Mapping) or not validator(descriptor):
            raise QueueRecoveryRuntimeError("A recovery input is missing or no longer authorized.")


def cleanup_orphan_request_manifests(
    project_directory: os.PathLike[str] | str,
    live_relative_paths: Sequence[str],
    *,
    maximum_removals: int = 64,
) -> int:
    """Bound cleanup to unreferenced request manifests in one project."""
    root = _validated_project_root(project_directory)
    recovery_dir = root / MANIFEST_DIRECTORY
    if not os.path.lexists(recovery_dir):
        return 0
    if not _manifest_dir_fd_supported():
        return 0
    live = {
        value
        for value in live_relative_paths
        if isinstance(value, str)
        and value.startswith(MANIFEST_DIRECTORY + "/")
        and PurePosixPath(value).parent == PurePosixPath(MANIFEST_DIRECTORY)
    }
    removed = 0
    directory_descriptor = None
    try:
        directory_descriptor = _open_private_directory(recovery_dir)
        names = sorted(os.listdir(directory_descriptor))
    except (OSError, QueueRecoveryRuntimeError):
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        return 0
    try:
        for name in names:
            if removed >= max(0, min(1024, int(maximum_removals))):
                break
            relative = f"{MANIFEST_DIRECTORY}/{name}"
            if relative in live or not name.endswith(".request.json"):
                continue
            try:
                info = os.stat(
                    name, dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    continue
                os.unlink(name, dir_fd=directory_descriptor)
                removed += 1
            except OSError:
                continue
        if removed:
            os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return removed


def remove_request_manifest(
    project_directory: os.PathLike[str] | str,
    pointer: Mapping[str, Any],
) -> bool:
    """Remove one exact unneeded manifest without following a replacement."""
    root = _validated_project_root(project_directory)
    relative = pointer.get("path") if isinstance(pointer, Mapping) else None
    if (
        not isinstance(relative, str)
        or not relative.startswith(MANIFEST_DIRECTORY + "/")
        or PurePosixPath(relative).parent != PurePosixPath(MANIFEST_DIRECTORY)
        or not PurePosixPath(relative).name.endswith(".request.json")
    ):
        return False
    filename = PurePosixPath(relative).name
    recovery_directory = root / MANIFEST_DIRECTORY
    if not _manifest_dir_fd_supported():
        # A path-based unlink can be redirected through a parent junction
        # between validation and deletion. Leave the immutable orphan for a
        # platform cleanup path rather than risk deleting outside the project.
        return False
    try:
        directory_descriptor = _open_private_directory(recovery_directory)
    except QueueRecoveryRuntimeError:
        return False
    try:
        info = os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return False
        os.unlink(filename, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
        return True
    except OSError:
        return False
    finally:
        os.close(directory_descriptor)


def ensure_recovery_staging_directory(
    project_directory: os.PathLike[str] | str,
) -> str:
    """Return the real private staging directory for native unit outputs."""
    root = _validated_project_root(project_directory)
    recovery_dir, _recovery_identity = _ensure_private_directory(
        root / MANIFEST_DIRECTORY,
    )
    staging_dir, _staging_identity = _ensure_private_directory(
        recovery_dir / "staging",
    )
    return str(staging_dir)


def promote_recovery_staged_artifact(
    project_directory: os.PathLike[str] | str,
    *,
    staged_path: os.PathLike[str] | str,
    output_basename: str,
) -> str:
    """Fsync and atomically promote one private native to a stable final.

    The caller commits the public sidecar first.  A crash before this replace
    therefore leaves only hidden media; a crash after it leaves a complete
    media+sidecar pair that startup reconciliation can adopt.
    """
    root = _validated_project_root(project_directory)
    recovery_dir, _recovery_identity = _ensure_private_directory(
        root / MANIFEST_DIRECTORY,
    )
    staging_dir, staging_identity = _ensure_private_directory(
        recovery_dir / "staging",
    )
    source = Path(os.path.abspath(os.fspath(staged_path)))
    if (
        source.parent != staging_dir
        or source.name != os.path.basename(source.name)
        or not source.name
        or not isinstance(output_basename, str)
        or output_basename != os.path.basename(output_basename)
        or output_basename.startswith(".")
    ):
        raise QueueRecoveryRuntimeError("Recovery staged artifact identity is invalid.")
    sha256_file(source)
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    destination = root / output_basename
    try:
        _verify_directory_identity(staging_dir, staging_identity)
        os.replace(source, destination)
        _fsync_directory(root)
        companion = staging_dir / f"{source.stem}.json"
        try:
            companion_info = os.lstat(companion)
            if stat.S_ISREG(companion_info.st_mode) and companion_info.st_nlink == 1:
                os.unlink(companion)
                _fsync_directory(staging_dir)
        except OSError:
            pass
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Recovery staged artifact could not be promoted."
        ) from None
    return output_basename


def cleanup_orphan_staged_outputs(
    project_directory: os.PathLike[str] | str,
    live_job_ids: Sequence[str],
    *,
    maximum_removals: int = 64,
) -> int:
    """Bound cleanup of stable native staging files for terminal jobs."""
    root = _validated_project_root(project_directory)
    recovery_dir = root / MANIFEST_DIRECTORY
    if not os.path.lexists(recovery_dir) or not _manifest_dir_fd_supported():
        return 0
    live_prefixes = tuple(
        f"unit-{job_id}-"
        for job_id in live_job_ids
        if isinstance(job_id, str) and _JOB_ID_RE.fullmatch(job_id) is not None
    )
    limit = max(0, min(1024, int(maximum_removals)))
    removed = 0
    recovery_descriptor = None
    staging_descriptor = None
    try:
        recovery_descriptor = _open_private_directory(recovery_dir)
        staging_descriptor = os.open(
            "staging",
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=recovery_descriptor,
        )
        staging_info = os.fstat(staging_descriptor)
        if (
            not stat.S_ISDIR(staging_info.st_mode)
            or (os.name != "nt" and stat.S_IMODE(staging_info.st_mode) & 0o077)
        ):
            raise QueueRecoveryRuntimeError(
                "Recovery staging directory is unsafe."
            )
        names = sorted(os.listdir(staging_descriptor))
    except (OSError, QueueRecoveryRuntimeError):
        if staging_descriptor is not None:
            os.close(staging_descriptor)
            staging_descriptor = None
        return 0
    finally:
        if recovery_descriptor is not None:
            os.close(recovery_descriptor)
    try:
        for name in names:
            if removed >= limit:
                break
            if (
                not name.startswith("unit-")
                or any(name.startswith(prefix) for prefix in live_prefixes)
            ):
                continue
            try:
                info = os.stat(
                    name, dir_fd=staging_descriptor,
                    follow_symlinks=False,
                )
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    continue
                os.unlink(name, dir_fd=staging_descriptor)
                removed += 1
            except OSError:
                continue
        if removed:
            os.fsync(staging_descriptor)
    finally:
        if staging_descriptor is not None:
            os.close(staging_descriptor)
    return removed


def recovery_unit_id(
    job_id: str,
    kind: str,
    *,
    variant: int = 0,
    index: int = 0,
    dependencies: Sequence[str] = (),
    settings: Mapping[str, Any] | None = None,
) -> str:
    """Return a prompt/path-free deterministic identity for one safe unit."""
    _manifest_relative_path(job_id)
    if not isinstance(kind, str) or not kind or len(kind) > 64:
        raise QueueRecoveryRuntimeError("Recovery unit kind is invalid.")
    if type(variant) is not int or variant < 0 or type(index) is not int or index < 0:
        raise QueueRecoveryRuntimeError("Recovery unit position is invalid.")
    clean_dependencies = list(dependencies)
    if any(not isinstance(value, str) or _UNIT_ID_RE.fullmatch(value) is None for value in clean_dependencies):
        raise QueueRecoveryRuntimeError("Recovery unit dependency is invalid.")
    encoded = _canonical_json({
        "dependencies": clean_dependencies,
        "index": index,
        "job_id": job_id,
        "kind": kind,
        "settings": dict(settings or {}),
        "variant": variant,
    })
    return "unit:v1:" + hashlib.sha256(encoded).hexdigest()


def artifact_descriptor(
    project_directory: os.PathLike[str] | str,
    *,
    basename: str,
    sidecar_basename: str,
    producer_unit_id: str,
) -> dict[str, Any]:
    """Seal one direct-child media+sidecar pair for durable publication."""
    root = _validated_project_root(project_directory)
    if (
        not isinstance(basename, str)
        or basename != os.path.basename(basename)
        or basename.startswith(".")
        or not isinstance(sidecar_basename, str)
        or sidecar_basename != os.path.basename(sidecar_basename)
        or sidecar_basename.startswith(".")
        or _UNIT_ID_RE.fullmatch(str(producer_unit_id)) is None
    ):
        raise QueueRecoveryRuntimeError("Recovery artifact identity is invalid.")
    media = root / basename
    sidecar = root / sidecar_basename
    media_size, media_sha = sha256_file(media)
    sidecar_size, sidecar_sha = sha256_file(sidecar)
    try:
        sidecar_value = json.loads(
            _read_exact_file(sidecar, maximum_bytes=MAX_MANIFEST_BYTES).decode("utf-8")
        )
    except (UnicodeDecodeError, ValueError, TypeError):
        raise QueueRecoveryRuntimeError("Recovery artifact sidecar is invalid.") from None
    if (
        not isinstance(sidecar_value, dict)
        or sidecar_value.get("producer_unit_id") != producer_unit_id
    ):
        raise QueueRecoveryRuntimeError("Recovery artifact producer evidence is invalid.")
    for path in (media, sidecar):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {
        "basename": basename,
        "producer_unit_id": producer_unit_id,
        "sha256": media_sha,
        "sidecar_basename": sidecar_basename,
        "sidecar_sha256": sidecar_sha,
        "sidecar_size": sidecar_size,
        "size": media_size,
    }


def validate_artifact_descriptor(
    project_directory: os.PathLike[str] | str,
    descriptor: Mapping[str, Any],
    *,
    producer_unit_id: str | None = None,
) -> bool:
    """Validate a direct-child artifact without trusting filename discovery."""
    if not isinstance(descriptor, Mapping):
        return False
    expected = producer_unit_id or descriptor.get("producer_unit_id")
    if not isinstance(expected, str) or _UNIT_ID_RE.fullmatch(expected) is None:
        return False
    try:
        rebuilt = artifact_descriptor(
            project_directory,
            basename=descriptor.get("basename"),
            sidecar_basename=descriptor.get("sidecar_basename"),
            producer_unit_id=expected,
        )
    except QueueRecoveryRuntimeError:
        return False
    return all(rebuilt.get(key) == descriptor.get(key) for key in rebuilt)


def protected_artifact_descriptor(
    project_directory: os.PathLike[str] | str,
    *,
    basename: str,
    sidecar_basename: str,
    original_basename: str,
    producer_unit_id: str,
) -> dict[str, Any]:
    """Seal one hidden native retained solely for a pending delivery unit."""
    root = _validated_project_root(project_directory)
    expected_prefix = ".maestro-delivery-"
    if (
        not isinstance(basename, str)
        or basename != os.path.basename(basename)
        or not basename.startswith(expected_prefix)
        or not basename.endswith((".mp4", ".mkv", ".webm", ".mov"))
        or not isinstance(sidecar_basename, str)
        or sidecar_basename != os.path.basename(sidecar_basename)
        or not sidecar_basename.startswith(expected_prefix)
        or not sidecar_basename.endswith(".meta.json")
        or not isinstance(original_basename, str)
        or original_basename != os.path.basename(original_basename)
        or original_basename.startswith(".")
        or _UNIT_ID_RE.fullmatch(str(producer_unit_id)) is None
    ):
        raise QueueRecoveryRuntimeError("Protected recovery artifact identity is invalid.")
    media_size, media_sha = sha256_file(root / basename)
    sidecar_size, sidecar_sha = sha256_file(root / sidecar_basename)
    return {
        "basename": basename,
        "original_basename": original_basename,
        "producer_unit_id": producer_unit_id,
        "sha256": media_sha,
        "sidecar_basename": sidecar_basename,
        "sidecar_sha256": sidecar_sha,
        "sidecar_size": sidecar_size,
        "size": media_size,
    }


def validate_protected_artifact_descriptor(
    project_directory: os.PathLike[str] | str,
    descriptor: Mapping[str, Any],
    *,
    producer_unit_id: str | None = None,
) -> bool:
    """Validate a hidden delivery native and its owner-private sidecar."""
    if not isinstance(descriptor, Mapping):
        return False
    expected = producer_unit_id or descriptor.get("producer_unit_id")
    try:
        rebuilt = protected_artifact_descriptor(
            project_directory,
            basename=descriptor.get("basename"),
            sidecar_basename=descriptor.get("sidecar_basename"),
            original_basename=descriptor.get("original_basename"),
            producer_unit_id=expected,
        )
    except QueueRecoveryRuntimeError:
        return False
    return all(rebuilt.get(key) == descriptor.get(key) for key in rebuilt)


def quarantine_artifact(
    project_directory: os.PathLike[str] | str,
    descriptor: Mapping[str, Any],
) -> None:
    """Move incomplete direct-child evidence out of gallery publication."""
    root = _validated_project_root(project_directory)
    quarantine = root / MANIFEST_DIRECTORY / "quarantine"
    recovery_dir, _recovery_identity = _ensure_private_directory(
        root / MANIFEST_DIRECTORY,
    )
    quarantine, quarantine_identity = _ensure_private_directory(
        recovery_dir / "quarantine",
    )
    for key in ("basename", "sidecar_basename"):
        name = descriptor.get(key) if isinstance(descriptor, Mapping) else None
        if not isinstance(name, str) or name != os.path.basename(name) or name.startswith("."):
            continue
        source = root / name
        if not source.exists():
            continue
        try:
            _verify_directory_identity(quarantine, quarantine_identity)
            os.replace(source, quarantine / f"{uuid.uuid4().hex}-{name}")
        except OSError:
            pass
    _fsync_directory(quarantine)


def next_recovery_attempt(job: Mapping[str, Any]) -> tuple[int, bool]:
    """Return the bounded restart attempt and whether automatic work may run."""
    try:
        previous = max(0, int(job.get("recovery_attempt", 0) or 0))
    except (TypeError, ValueError):
        previous = 0
    attempt = previous + 1
    return attempt, attempt <= MAX_RECOVERY_ATTEMPTS


def replay_concat_to_stable_output(
    project_directory: os.PathLike[str] | str,
    *,
    component_basenames: Sequence[str],
    output_basename: str,
    concatenate: Callable[[list[str], str], bool],
) -> str:
    """Rerun concat only, using hidden staging and one stable final name.

    The caller must first validate the component recovery descriptors. A crash
    after promotion but before journaling is idempotent: the next call replaces
    the same direct-child output and can never create a second gallery final.
    """
    root = _validated_project_root(project_directory)
    if (
        not component_basenames
        or not isinstance(output_basename, str)
        or output_basename != os.path.basename(output_basename)
        or output_basename.startswith(".")
    ):
        raise QueueRecoveryRuntimeError("Recovery concat identity is invalid.")
    components = []
    for basename in component_basenames:
        if (
            not isinstance(basename, str)
            or basename != os.path.basename(basename)
            or basename.startswith(".")
        ):
            raise QueueRecoveryRuntimeError("Recovery concat component is invalid.")
        path = root / basename
        sha256_file(path)
        components.append(str(path))
    recovery_dir, _recovery_identity = _ensure_private_directory(
        root / MANIFEST_DIRECTORY,
    )
    staging_dir, staging_identity = _ensure_private_directory(
        recovery_dir / "staging",
    )
    suffix = Path(output_basename).suffix or ".mp4"
    staging = staging_dir / f".{uuid.uuid4().hex}.concat{suffix}"
    try:
        if concatenate(components, str(staging)) is not True:
            raise QueueRecoveryRuntimeError("Recovery concat failed.")
        sha256_file(staging)
        descriptor = os.open(
            staging, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        destination = root / output_basename
        _verify_directory_identity(staging_dir, staging_identity)
        os.replace(staging, destination)
        _fsync_directory(root)
        return output_basename
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery concat could not be promoted.") from None
    finally:
        try:
            if staging.exists():
                staging.unlink()
        except OSError:
            pass


def replay_delivery_from_protected_native(
    project_directory: os.PathLike[str] | str,
    *,
    protected_descriptor: Mapping[str, Any],
    work_basename: str,
    deliver: Callable[[str, str], bool],
) -> str:
    """Rerun only delivery from a verified native into stable hidden work.

    Delivery implementations may use GPU upscaling, but this crash boundary is
    model-free: the native is validated before the callback, output is staged
    below the private recovery directory, and promotion replaces one stable
    hidden work basename. No denoising or concat callable is accepted here.
    """
    root = _validated_project_root(project_directory)
    if not validate_protected_artifact_descriptor(root, protected_descriptor):
        raise QueueRecoveryRuntimeError("Protected delivery native no longer matches.")
    if (
        not isinstance(work_basename, str)
        or work_basename != os.path.basename(work_basename)
        or not work_basename.startswith(".maestro-delivery-")
        or not work_basename.endswith((".mp4", ".mkv", ".webm", ".mov"))
    ):
        raise QueueRecoveryRuntimeError("Recovery delivery work identity is invalid.")
    source = root / str(protected_descriptor["basename"])
    recovery_dir, _recovery_identity = _ensure_private_directory(
        root / MANIFEST_DIRECTORY,
    )
    staging_dir, staging_identity = _ensure_private_directory(
        recovery_dir / "staging",
    )
    suffix = Path(work_basename).suffix or ".mp4"
    staging = staging_dir / f".{uuid.uuid4().hex}.delivery{suffix}"
    try:
        if deliver(str(source), str(staging)) is not True:
            raise QueueRecoveryRuntimeError("Recovery delivery failed.")
        sha256_file(staging)
        descriptor = os.open(staging, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        destination = root / work_basename
        _verify_directory_identity(staging_dir, staging_identity)
        os.replace(staging, destination)
        _fsync_directory(root)
        return work_basename
    except OSError:
        raise QueueRecoveryRuntimeError("Recovery delivery could not be promoted.") from None
    finally:
        try:
            if staging.exists():
                staging.unlink()
        except OSError:
            pass


__all__ = [
    "MANIFEST_DIRECTORY",
    "MANIFEST_SCHEMA",
    "MAX_RECOVERY_ATTEMPTS",
    "QueueRecoveryRuntimeError",
    "artifact_descriptor",
    "atomic_write_request_manifest",
    "cleanup_orphan_request_manifests",
    "cleanup_orphan_staged_outputs",
    "discover_request_manifest_pointers",
    "ensure_recovery_staging_directory",
    "load_request_manifest",
    "next_recovery_attempt",
    "protected_artifact_descriptor",
    "promote_recovery_staged_artifact",
    "quarantine_artifact",
    "remove_request_manifest",
    "replay_concat_to_stable_output",
    "replay_delivery_from_protected_native",
    "recovery_unit_id",
    "sha256_file",
    "validate_artifact_descriptor",
    "validate_manifest_inputs",
    "validate_protected_artifact_descriptor",
]
