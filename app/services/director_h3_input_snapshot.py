"""Private new-job input snapshots for verified Director H3 predecessors.

The caller verifies the original producer graph and current access scope.
These files are inputs, never completed producer units or published outputs.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import uuid
from typing import Mapping

from services.queue_recovery_runtime import (
    MANIFEST_DIRECTORY, QueueRecoveryRuntimeError, _open_private_directory,
    _private_directory_identity, _validated_project_root,
    _verify_directory_identity, ensure_recovery_staging_directory, sha256_file,
)


INPUT_SCOPE = "director_h3_predecessor"
INPUT_FIELD = "_h3_rerun_predecessor_path:0"
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,179}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def snapshot_predecessor(
    project_directory: str,
    *,
    job_id: str,
    source_path: str,
    source_artifact: Mapping,
    owner_digest: str,
    project_digest: str,
) -> dict:
    """Copy exact verified media through one unchanged no-follow handle.

    Invoke under registration's workspace guard, after original producer and
    owner/project verification. The returned descriptor belongs to the new
    job's request manifest. Caller-owned source provenance remains separate.
    """
    root = _validated_project_root(project_directory)
    if (not isinstance(source_artifact, Mapping) or type(source_path) is not str
            or not os.path.isabs(source_path) or os.path.normpath(source_path) != source_path):
        raise QueueRecoveryRuntimeError("Director predecessor snapshot identity is invalid.")
    if not all(operation in os.supports_dir_fd for operation in (os.open, os.stat, os.unlink)):
        # Do not fall back to path-based copying through a replaceable private
        # directory. A platform needs its own equivalent bound-directory API.
        raise QueueRecoveryRuntimeError("Private predecessor snapshots require directory-relative file operations.")
    requested = Path(os.path.abspath(source_path))
    size, digest = source_artifact.get("size"), source_artifact.get("sha256")
    if (type(job_id) is not str or _JOB_ID.fullmatch(job_id) is None
            or type(size) is not int or size < 1 or type(digest) is not str
            or _HASH.fullmatch(digest) is None
            or requested.parent != root or requested.name != source_artifact.get("basename")
            or not requested.name or requested.name.startswith(".")
            or requested.suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov"}
            or type(owner_digest) is not str or not owner_digest
            or type(project_digest) is not str or not project_digest):
        raise QueueRecoveryRuntimeError("Director predecessor snapshot identity is invalid.")
    staging = Path(ensure_recovery_staging_directory(root))
    recovery_identity = _private_directory_identity(root / MANIFEST_DIRECTORY)
    staging_identity = _private_directory_identity(staging)
    source_fd = destination_fd = recovery_fd = staging_fd = -1
    destination = ""
    destination_name = ""
    destination_identity = None
    succeeded = False
    try:
        recovery_fd = _open_private_directory(root / MANIFEST_DIRECTORY)
        staging_fd = os.open("staging", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=recovery_fd)
        opened_staging = os.fstat(staging_fd)
        if ((opened_staging.st_dev, opened_staging.st_ino) != staging_identity
                or not stat.S_ISDIR(opened_staging.st_mode)):
            raise QueueRecoveryRuntimeError("Director predecessor input storage changed.")
        source_fd = os.open(requested, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != size:
            raise QueueRecoveryRuntimeError("Director predecessor media changed.")
        destination_name = f"unit-{job_id}-h3-input-{uuid.uuid4().hex}{requested.suffix.lower()}"
        destination_fd = os.open(destination_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=staging_fd)
        destination = str(staging / destination_name)
        os.fchmod(destination_fd, 0o600)
        created = os.fstat(destination_fd)
        destination_identity = (created.st_dev, created.st_ino)
        copied = hashlib.sha256()
        remaining = size
        while remaining:
            chunk = os.read(source_fd, min(1024 * 1024, remaining))
            if not chunk:
                raise QueueRecoveryRuntimeError("Director predecessor media changed.")
            copied.update(chunk)
            remaining -= len(chunk)
            offset = 0
            while offset < len(chunk):
                written = os.write(destination_fd, chunk[offset:])
                if written <= 0:
                    raise QueueRecoveryRuntimeError("Director predecessor input could not be copied.")
                offset += written
        after = os.fstat(source_fd)
        current = os.lstat(requested)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        if (os.read(source_fd, 1) or identity(before) != identity(after)
                or identity(after) != identity(current) or current.st_nlink != 1
                or not stat.S_ISREG(current.st_mode)
                or not hmac.compare_digest(copied.hexdigest(), digest)):
            raise QueueRecoveryRuntimeError("Director predecessor media changed.")
        os.fsync(destination_fd)
        _verify_directory_identity(root / MANIFEST_DIRECTORY, recovery_identity)
        _verify_directory_identity(staging, staging_identity)
        current_destination = os.stat(destination_name, dir_fd=staging_fd, follow_symlinks=False)
        if ((current_destination.st_dev, current_destination.st_ino) != destination_identity
                or current_destination.st_nlink != 1 or not stat.S_ISREG(current_destination.st_mode)):
            raise QueueRecoveryRuntimeError("Director predecessor input changed during admission.")
        os.fsync(staging_fd)
        result = {"scope": INPUT_SCOPE, "field": INPUT_FIELD,
                  "recovery_job_id": job_id, "owner_principal": owner_digest,
                  "project_instance": project_digest, "path": destination,
                  "size": size, "sha256": digest}
        succeeded = True
        return result
    except OSError:
        raise QueueRecoveryRuntimeError("Director predecessor input could not be copied safely.") from None
    finally:
        for descriptor in (source_fd, destination_fd):
            if descriptor >= 0:
                os.close(descriptor)
        if destination and not succeeded:
            # Cleanup through the same bound directory even if its path moved.
            try:
                remaining_file = os.stat(destination_name, dir_fd=staging_fd, follow_symlinks=False)
                if (remaining_file.st_dev, remaining_file.st_ino) == destination_identity:
                    os.unlink(destination_name, dir_fd=staging_fd)
            except (OSError, QueueRecoveryRuntimeError):
                pass
        for descriptor in (staging_fd, recovery_fd):
            if descriptor >= 0:
                os.close(descriptor)


def validate_predecessor_snapshot(
    descriptor: Mapping,
    *,
    project_directory: str,
    job_id: str,
    owner_digest: str,
    project_digest: str,
) -> bool:
    """Revalidate this new-job input without reopening its original producer."""
    try:
        if not isinstance(descriptor, Mapping):
            return False
        root = _validated_project_root(project_directory)
        staging = root / MANIFEST_DIRECTORY / "staging"
        path = descriptor.get("path")
        if (type(job_id) is not str or _JOB_ID.fullmatch(job_id) is None
                or descriptor.get("scope") != INPUT_SCOPE or descriptor.get("field") != INPUT_FIELD
                or descriptor.get("recovery_job_id") != job_id
                or descriptor.get("owner_principal") != owner_digest
                or descriptor.get("project_instance") != project_digest
                or type(path) is not str or not os.path.isabs(path) or os.path.normpath(path) != path
                or Path(path).parent != staging
                or not Path(path).name.startswith(f"unit-{job_id}-h3-input-")
                or Path(path).suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov"}
                or type(descriptor.get("size")) is not int or descriptor["size"] < 1
                or type(descriptor.get("sha256")) is not str or _HASH.fullmatch(descriptor["sha256"]) is None):
            return False
        recovery_identity = _private_directory_identity(root / MANIFEST_DIRECTORY)
        staging_identity = _private_directory_identity(staging)
        info = os.lstat(path)
        if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            return False
        size, digest = sha256_file(path)
        _verify_directory_identity(root / MANIFEST_DIRECTORY, recovery_identity)
        _verify_directory_identity(staging, staging_identity)
        return size == descriptor["size"] and hmac.compare_digest(digest, descriptor["sha256"])
    except (OSError, ValueError, TypeError, QueueRecoveryRuntimeError):
        return False
