"""Retain server-authorized style images independently of a child job.

Caller-owned admission seals these descriptors. File presence never establishes
project access, source terms, or permission to apply a native reference.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import uuid
from collections.abc import Mapping
from contextlib import contextmanager

from services.queue_recovery_runtime import (
    MANIFEST_DIRECTORY, QueueRecoveryRuntimeError, _private_directory_identity,
    _validated_project_root, _verify_directory_identity,
)


INPUT_SCOPE = "director_custom_style_image"
MAX_IMAGE_BYTES = 64 * 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}")
_OWNER = re.compile(r"owner:v1:[0-9a-f]{64}")
_PROJECT = re.compile(r"project:v1:[0-9a-f]{64}")
_RETENTION = re.compile(r"[0-9a-f]{32}")
_FIELD = re.compile(r"_director_custom_style_image_paths:[0-7]")
_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_KEYS = {"scope", "retention_id", "field", "owner_principal", "project_instance",
         "style_snapshot_digest", "path", "size", "sha256", "storage_identity"}


def _require_platform():
    if (not all(operation in os.supports_dir_fd for operation in (os.open, os.stat, os.unlink, os.mkdir))
            or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")):
        raise QueueRecoveryRuntimeError("Private style retention requires directory-relative no-follow operations.")


def _absolute(value):
    try:
        path = os.fspath(value)
    except TypeError:
        raise QueueRecoveryRuntimeError("Style input path is invalid.") from None
    if (type(path) is not str or not os.path.isabs(path) or os.path.normpath(path) != path
            or "\0" in path):
        raise QueueRecoveryRuntimeError("Style input path is invalid.")
    return Path(path)


def _bindings(retention_id, owner_digest, project_digest, style_snapshot_digest, field):
    if (type(retention_id) is not str or _RETENTION.fullmatch(retention_id) is None
            or type(field) is not str or _FIELD.fullmatch(field) is None
            or type(owner_digest) is not str or _OWNER.fullmatch(owner_digest) is None
            or type(project_digest) is not str or _PROJECT.fullmatch(project_digest) is None
            or type(style_snapshot_digest) is not str or _HASH.fullmatch(style_snapshot_digest) is None):
        raise QueueRecoveryRuntimeError("Style retention identity is invalid.")


def _inode(info):
    return info.st_dev, info.st_ino


def _file_identity(info):
    return (*_inode(info), info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _open_chain(path):
    chain = []
    try:
        current = Path(path.anchor)
        fd = os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        chain.append((current, fd, _inode(os.fstat(fd))))
        for name in path.parts[1:]:
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            current = current / name
            chain.append((current, child, _inode(os.fstat(child))))
            if not stat.S_ISDIR(before.st_mode) or _inode(before) != chain[-1][2]:
                raise QueueRecoveryRuntimeError("Style input directory changed.")
            fd = child
        _verify_chain(chain)
        return chain
    except Exception:
        _close_chain(chain)
        raise


def _verify_chain(chain):
    for path, fd, identity in chain:
        current, opened = os.lstat(path), os.fstat(fd)
        if (not stat.S_ISDIR(current.st_mode) or not stat.S_ISDIR(opened.st_mode)
                or _inode(current) != identity or _inode(opened) != identity):
            raise QueueRecoveryRuntimeError("Style input directory changed.")


def _close_chain(chain):
    for _path, fd, _identity in reversed(chain):
        os.close(fd)


def _open_private_child(parent_fd, parent_path, name, *, create):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        identity = _private_directory_identity(parent_path / name)
        if _inode(os.fstat(fd)) != identity:
            raise QueueRecoveryRuntimeError("Style retention directory changed.")
        if create:
            os.fsync(parent_fd)
        return fd, identity
    except Exception:
        os.close(fd)
        raise


def _verify_storage(root, chain, recovery_identity, staging_identity):
    _verify_chain(chain)
    recovery = root / MANIFEST_DIRECTORY
    _verify_directory_identity(recovery, recovery_identity)
    _verify_directory_identity(recovery / "staging", staging_identity)
    if (_private_directory_identity(recovery) != recovery_identity
            or _private_directory_identity(recovery / "staging") != staging_identity):
        raise QueueRecoveryRuntimeError("Style retention directory changed.")


def _hash_handle(fd, size):
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:
            raise QueueRecoveryRuntimeError("Style image bytes changed.")
        remaining -= len(chunk)
        digest.update(chunk)
    if os.read(fd, 1):
        raise QueueRecoveryRuntimeError("Style image bytes changed.")
    return digest.hexdigest()


def snapshot_style_image(project_directory, *, retention_id, source_path, source_root,
                         expected_size, expected_sha256, owner_digest, project_digest,
                         style_snapshot_digest, field):
    """Copy pinned bytes under a caller-held project admission reservation."""
    _require_platform()
    _bindings(retention_id, owner_digest, project_digest, style_snapshot_digest, field)
    root = _validated_project_root(_absolute(project_directory))
    source, authorized_root = _absolute(source_path), _absolute(source_root)
    if (authorized_root not in source.parents or source.suffix.lower() not in _EXTENSIONS
            or type(expected_size) is not int or not 1 <= expected_size <= MAX_IMAGE_BYTES
            or type(expected_sha256) is not str or _HASH.fullmatch(expected_sha256) is None):
        raise QueueRecoveryRuntimeError("Style input source identity is invalid.")
    source_chain = project_chain = []
    source_fd = output_fd = recovery_fd = staging_fd = -1
    output_name = ""
    created_identity = None
    succeeded = False
    try:
        source_chain = _open_chain(source.parent)
        source_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_chain[-1][1])
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != expected_size:
            raise QueueRecoveryRuntimeError("Style image source changed.")
        project_chain = _open_chain(root)
        recovery_fd, recovery_identity = _open_private_child(
            project_chain[-1][1], root, MANIFEST_DIRECTORY, create=True)
        staging = root / MANIFEST_DIRECTORY / "staging"
        staging_fd, staging_identity = _open_private_child(
            recovery_fd, root / MANIFEST_DIRECTORY, "staging", create=True)
        _verify_storage(root, project_chain, recovery_identity, staging_identity)
        output_name = f"unit-style-{retention_id}-input-{uuid.uuid4().hex}{source.suffix.lower()}"
        output_fd = os.open(output_name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=staging_fd)
        created_identity = _inode(os.fstat(output_fd))
        os.fchmod(output_fd, 0o600)
        copied = hashlib.sha256()
        remaining = expected_size
        while remaining:
            chunk = os.read(source_fd, min(1024 * 1024, remaining))
            if not chunk:
                raise QueueRecoveryRuntimeError("Style image source changed.")
            copied.update(chunk)
            remaining -= len(chunk)
            offset = 0
            while offset < len(chunk):
                written = os.write(output_fd, chunk[offset:])
                if written <= 0:
                    raise QueueRecoveryRuntimeError("Style input could not be copied.")
                offset += written
        after = os.fstat(source_fd)
        current = os.stat(source.name, dir_fd=source_chain[-1][1], follow_symlinks=False)
        if (os.read(source_fd, 1) or _file_identity(before) != _file_identity(after)
                or _file_identity(after) != _file_identity(current)
                or not hmac.compare_digest(copied.hexdigest(), expected_sha256)):
            raise QueueRecoveryRuntimeError("Style image source changed.")
        _verify_chain(source_chain)
        os.fsync(output_fd)
        output_before = os.fstat(output_fd)
        os.lseek(output_fd, 0, os.SEEK_SET)
        if not hmac.compare_digest(_hash_handle(output_fd, expected_size), expected_sha256):
            raise QueueRecoveryRuntimeError("Style retained image changed.")
        _verify_storage(root, project_chain, recovery_identity, staging_identity)
        output = os.stat(output_name, dir_fd=staging_fd, follow_symlinks=False)
        if (_file_identity(output_before) != _file_identity(os.fstat(output_fd))
                or _file_identity(output_before) != _file_identity(output)
                or _inode(output) != created_identity or not stat.S_ISREG(output.st_mode)
                or output.st_nlink != 1 or output.st_size != expected_size
                or stat.S_IMODE(output.st_mode) != 0o600):
            raise QueueRecoveryRuntimeError("Style retained image changed.")
        os.fsync(staging_fd)
        result = {"scope": INPUT_SCOPE, "retention_id": retention_id, "field": field,
                  "owner_principal": owner_digest, "project_instance": project_digest,
                  "style_snapshot_digest": style_snapshot_digest, "path": str(staging / output_name),
                  "size": expected_size, "sha256": expected_sha256,
                  "storage_identity": {"project": list(project_chain[-1][2]),
                                       "recovery": list(recovery_identity), "staging": list(staging_identity),
                                       "file": list(created_identity)}}
        succeeded = True
        return result
    except OSError:
        raise QueueRecoveryRuntimeError("Style input could not be retained safely.") from None
    finally:
        for fd in (source_fd, output_fd):
            if fd >= 0:
                os.close(fd)
        if output_name and created_identity is not None and not succeeded:
            try:
                current = os.stat(output_name, dir_fd=staging_fd, follow_symlinks=False)
                if _inode(current) == created_identity:
                    os.unlink(output_name, dir_fd=staging_fd)
            except OSError:
                pass
        for fd in (staging_fd, recovery_fd):
            if fd >= 0:
                os.close(fd)
        _close_chain(project_chain)
        _close_chain(source_chain)


@contextmanager
def _verified_snapshot(descriptor, *, project_directory, retention_id,
                       owner_digest, project_digest, style_snapshot_digest, field):
    chain = []
    recovery_fd = staging_fd = input_fd = -1
    try:
        _require_platform()
        _bindings(retention_id, owner_digest, project_digest, style_snapshot_digest, field)
        if not isinstance(descriptor, Mapping) or set(descriptor) != _KEYS:
            raise QueueRecoveryRuntimeError("Style retained input binding changed.")
        root = _validated_project_root(_absolute(project_directory))
        path = _absolute(descriptor["path"])
        if (descriptor["scope"] != INPUT_SCOPE or descriptor["retention_id"] != retention_id
                or descriptor["field"] != field or descriptor["owner_principal"] != owner_digest
                or descriptor["project_instance"] != project_digest
                or descriptor["style_snapshot_digest"] != style_snapshot_digest
                or path.parent != root / MANIFEST_DIRECTORY / "staging"
                or re.fullmatch(f"unit-style-{retention_id}-input-[0-9a-f]{{32}}\\.(png|jpg|jpeg|webp)", path.name) is None
                or type(descriptor["size"]) is not int or not 1 <= descriptor["size"] <= MAX_IMAGE_BYTES
                or type(descriptor["sha256"]) is not str or _HASH.fullmatch(descriptor["sha256"]) is None):
            raise QueueRecoveryRuntimeError("Style retained input binding changed.")
        identity = descriptor["storage_identity"]
        if (type(identity) is not dict or set(identity) != {"project", "recovery", "staging", "file"}
                or any(type(value) is not list or len(value) != 2
                       or any(type(number) is not int or number < 0 for number in value)
                       for value in identity.values())):
            raise QueueRecoveryRuntimeError("Style retained input binding changed.")
        chain = _open_chain(root)
        recovery_fd, recovery_identity = _open_private_child(chain[-1][1], root, MANIFEST_DIRECTORY, create=False)
        staging_fd, staging_identity = _open_private_child(recovery_fd, root / MANIFEST_DIRECTORY, "staging", create=False)
        if (list(chain[-1][2]) != identity["project"] or list(recovery_identity) != identity["recovery"]
                or list(staging_identity) != identity["staging"]):
            raise QueueRecoveryRuntimeError("Style retained input binding changed.")
        input_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=staging_fd)
        before = os.fstat(input_fd)
        if (list(_inode(before)) != identity["file"] or not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1 or before.st_size != descriptor["size"]
                or stat.S_IMODE(before.st_mode) != 0o600):
            raise QueueRecoveryRuntimeError("Style retained input binding changed.")
        digest = _hash_handle(input_fd, descriptor["size"])
        after = os.fstat(input_fd)
        current = os.stat(path.name, dir_fd=staging_fd, follow_symlinks=False)
        _verify_storage(root, chain, recovery_identity, staging_identity)
        if (_file_identity(before) != _file_identity(after) or _file_identity(after) != _file_identity(current)
                or not hmac.compare_digest(digest, descriptor["sha256"])):
            raise QueueRecoveryRuntimeError("Style retained image changed.")
        yield staging_fd, path, after, root, chain, recovery_identity, staging_identity
    finally:
        for fd in (input_fd, staging_fd, recovery_fd):
            if fd >= 0:
                os.close(fd)
        _close_chain(chain)



def validate_style_image_snapshot(descriptor, *, project_directory, retention_id,
                                  owner_digest, project_digest, style_snapshot_digest, field):
    """Validate sealed input bindings and bytes without the original source."""
    try:
        with _verified_snapshot(descriptor, project_directory=project_directory, retention_id=retention_id,
                                owner_digest=owner_digest, project_digest=project_digest,
                                style_snapshot_digest=style_snapshot_digest, field=field):
            return True
    except (OSError, ValueError, TypeError, QueueRecoveryRuntimeError):
        return False


def cleanup_uncommitted_style_image_snapshot(descriptor, *, project_directory, retention_id,
                                             owner_digest, project_digest, style_snapshot_digest, field):
    """Remove one exact failed-admission input under its caller-held reservation.

    Only the admission owner may call this for a newly created, unpublished
    descriptor. Accepted or persisted bundles retain their separate lifecycle.
    """
    try:
        with _verified_snapshot(descriptor, project_directory=project_directory, retention_id=retention_id,
                                owner_digest=owner_digest, project_digest=project_digest,
                                style_snapshot_digest=style_snapshot_digest, field=field) as bound:
            staging_fd, path, info, root, chain, recovery_identity, staging_identity = bound
            _verify_storage(root, chain, recovery_identity, staging_identity)
            current = os.stat(path.name, dir_fd=staging_fd, follow_symlinks=False)
            if _file_identity(current) != _file_identity(info):
                return False
            os.unlink(path.name, dir_fd=staging_fd)
            os.fsync(staging_fd)
            return True
    except (OSError, ValueError, TypeError, QueueRecoveryRuntimeError):
        return False
