"""Private project-local H3 AV checkpoints; no pickle or runtime token storage.

The owning caller supplies verified model-bundle identity and a sealed queue
dependency. This module verifies their binding, not loaded model weights.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np
import torch
from safetensors.torch import load, save

from services.h3_cumulative_latents import H3CumulativeLatents
from services.h3_native_continuation import (
    audio_tick_at_frame,
    is_legal_h3_video_frame_count,
    latent_frames_for_video_frames,
)
from services.queue_recovery_runtime import (
    MANIFEST_DIRECTORY,
    QueueRecoveryRuntimeError,
    _canonical_json,
    _open_private_directory,
    _private_directory_identity,
    _read_exact_file_at,
    _validated_project_root,
    _verify_directory_identity,
    ensure_recovery_staging_directory,
)

_CONTRACT = "h3-native-normalized-av-v1"
_MAX_TENSOR_BYTES = 512 * 1024 * 1024
_MAX_HEADER_BYTES = 65536
_MAX_FILE_BYTES = _MAX_TENSOR_BYTES + _MAX_HEADER_BYTES + 8
_SHA = re.compile(r"[0-9a-f]{64}")
_UNIT = re.compile(r"unit:v1:[0-9a-f]{64}")
_JOB = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_DIRECTORY_HANDLES_SUPPORTED = all(
    function in os.supports_dir_fd
    for function in (os.open, os.link, os.unlink, os.stat)
)


@dataclass(frozen=True)
class H3CumulativeIdentity:
    """Declared owner/project/chain and verified runtime bundle digest.

    runtime_sha256 must cover transformer, conditioner, both VAEs, processor,
    model configuration and normalization contract. The trusted caller must
    establish that this digest describes its actual loaded native model.
    """

    owner_id: str
    project_id: str
    chain_id: str
    job_id: str
    runtime_sha256: str
    width: int
    height: int

    def __post_init__(self):
        if any(
            type(value) is not str or _ID.fullmatch(value) is None
            for value in (
                self.owner_id,
                self.project_id,
                self.chain_id,
            )
        ):
            raise QueueRecoveryRuntimeError(
                "H3 cumulative owner/project/chain identity is invalid."
            )
        if type(self.job_id) is not str or _JOB.fullmatch(self.job_id) is None:
            raise QueueRecoveryRuntimeError("H3 cumulative job identity is invalid.")
        if (
            type(self.runtime_sha256) is not str
            or _SHA.fullmatch(self.runtime_sha256) is None
        ):
            raise QueueRecoveryRuntimeError("H3 cumulative runtime digest is invalid.")
        if any(
            type(value) is not int or not 32 <= value <= 8192 or value % 32
            for value in (
                self.width,
                self.height,
            )
        ):
            raise QueueRecoveryRuntimeError("H3 cumulative canvas is invalid.")


@dataclass(frozen=True)
class H3CumulativeRecovery:
    """Verified serialized AV state, independent of any loaded model token."""

    state: H3CumulativeLatents
    identity: H3CumulativeIdentity
    dependency: str


def _validate(state, identity, dependency):
    if not isinstance(state, H3CumulativeLatents) or not isinstance(
        identity, H3CumulativeIdentity
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint state and identity are required."
        )
    identity.__post_init__()
    state.__post_init__()
    if type(dependency) is not str or _UNIT.fullmatch(dependency) is None:
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint dependency is invalid."
        )
    if any(
        value.device.type != "cpu" or value.dtype != torch.float32
        for value in (state.video, state.audio)
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint requires normalized CPU float32 AV state."
        )
    if tuple(state.video.shape[-2:]) != (identity.height // 16, identity.width // 16):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint canvas does not match its identity."
        )
    if (
        sum(
            value.numel() * value.element_size() for value in (state.video, state.audio)
        )
        > _MAX_TENSOR_BYTES
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint exceeds its tensor byte limit."
        )
    if any(
        not torch.isfinite(value).all().item() for value in (state.video, state.audio)
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint contains non-finite AV values."
        )


def _abort(check):
    if check and check():
        raise InterruptedError("H3 cumulative checkpoint cancelled.")


def _directories(project_directory, *, create):
    if not _DIRECTORY_HANDLES_SUPPORTED:
        raise QueueRecoveryRuntimeError(
            "Private H3 checkpoint storage requires directory-handle operations on this platform."
        )
    root = _validated_project_root(project_directory)
    if create:
        ensure_recovery_staging_directory(root)
    recovery = root / MANIFEST_DIRECTORY
    staging = recovery / "staging"
    paths = (root, recovery, staging)
    identities = []
    for path in paths:
        # The project itself need not be private; the two recovery dirs must be.
        if path == root:
            info = os.lstat(path)
            identities.append((info.st_dev, info.st_ino))
        else:
            identities.append(_private_directory_identity(path))
    return staging, tuple(zip(paths, identities))


def _verify_directories(directories):
    for path, identity in directories:
        _verify_directory_identity(path, identity)


def _entry_is_owned(directory, name, identity):
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity


def _require_owned_entry(directory, name, identity):
    if not _entry_is_owned(directory, name, identity):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint temporary entry changed during publication."
        )


def write_h3_cumulative_checkpoint(
    project_directory,
    state: H3CumulativeLatents,
    identity: H3CumulativeIdentity,
    dependency: str,
    *,
    abort_check: Callable[[], bool] | None = None,
) -> dict:
    """Seal one immutable file before returning a JSON-safe relative receipt.

    The caller exclusively owns the input tensors during serialization. The
    queue may reference the receipt only after its dependency is completed.
    Published checkpoints are complete. A crash may leave an unreferenced
    sealed file or a private partial temporary file; neither is a queue receipt.
    """
    _validate(state, identity, dependency)
    _abort(abort_check)
    metadata = {
        "contract": _CONTRACT,
        "identity": _canonical_json(asdict(identity)).decode("utf-8"),
        "dependency": dependency,
        "frame_count": str(state.frame_count),
        "published_frames": str(state.published_frames),
    }
    payload = save(
        {"video": state.video.contiguous(), "audio": state.audio.contiguous()},
        metadata=metadata,
    )
    if len(payload) > _MAX_FILE_BYTES:
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint exceeds its file byte limit."
        )
    digest = hashlib.sha256(payload).hexdigest()
    basename = f"unit-{identity.job_id}-h3-av-{digest}.safetensors"
    staging, directories = _directories(project_directory, create=True)
    directory = _open_private_directory(staging)
    # The private staging directory hides partial writes. Keep the job prefix
    # so startup can retire a crash-left temporary only after that job retires;
    # live and failed/retryable jobs retain every file with this prefix.
    temporary = f"unit-{identity.job_id}-h3-av-{uuid.uuid4().hex}.tmp"
    handle = -1
    temporary_identity = None
    try:
        _verify_directories(directories)
        handle = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory
        )
        info = os.fstat(handle)
        temporary_identity = (info.st_dev, info.st_ino)
        os.fchmod(handle, 0o600)
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            _abort(abort_check)
            written = os.write(handle, view[offset : offset + 1024 * 1024])
            if written <= 0:
                raise OSError("short checkpoint write")
            offset += written
        os.fsync(handle)
        _abort(abort_check)
        _verify_directories(directories)
        _require_owned_entry(directory, temporary, temporary_identity)
        try:
            # Create-only atomic publication; never overwrite another file.
            os.link(
                temporary,
                basename,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            _require_owned_entry(directory, basename, temporary_identity)
        except FileExistsError:
            existing = _read_exact_file_at(
                directory, basename, maximum_bytes=_MAX_FILE_BYTES
            )
            if existing != payload:
                raise QueueRecoveryRuntimeError(
                    "H3 cumulative checkpoint name collides with different content."
                ) from None
        _require_owned_entry(directory, temporary, temporary_identity)
        os.unlink(temporary, dir_fd=directory)
        temporary = ""
        os.close(handle)
        handle = -1
        os.fsync(directory)
        _verify_directories(directories)
        _abort(abort_check)
    except InterruptedError:
        raise
    except OSError:
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint could not be committed."
        ) from None
    finally:
        if handle >= 0:
            os.close(handle)
        if temporary and temporary_identity is not None:
            # A changed name belongs to its replacement, not this writer. Do
            # not mask an earlier error if private cleanup itself fails.
            try:
                if _entry_is_owned(directory, temporary, temporary_identity):
                    os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
        os.close(directory)
    return {
        "schema": 1,
        "mode": "cumulative_append",
        "storage": "recovery_staging",
        "basename": basename,
        "sha256": digest,
        "size": len(payload),
        "identity": asdict(identity),
        "dependency": dependency,
        "frame_count": state.frame_count,
        "published_frames": state.published_frames,
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate checkpoint header key")
        result[key] = value
    return result


def _validate_header(payload, receipt, identity, dependency):
    if len(payload) < 8:
        raise QueueRecoveryRuntimeError("H3 cumulative checkpoint header is invalid.")
    header_bytes = int.from_bytes(payload[:8], "little")
    if not 1 <= header_bytes <= _MAX_HEADER_BYTES or 8 + header_bytes >= len(payload):
        raise QueueRecoveryRuntimeError("H3 cumulative checkpoint header is invalid.")
    try:
        header = json.loads(
            payload[8 : 8 + header_bytes], object_pairs_hook=_unique_object
        )
    except (ValueError, UnicodeError):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint header is invalid."
        ) from None
    frames = receipt["frame_count"]
    published = receipt["published_frames"]
    if (
        not is_legal_h3_video_frame_count(frames)
        or type(published) is not int
        or not 1 <= published <= frames
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint frame geometry is invalid."
        )
    metadata = {
        "contract": _CONTRACT,
        "identity": _canonical_json(asdict(identity)).decode("utf-8"),
        "dependency": dependency,
        "frame_count": str(frames),
        "published_frames": str(published),
    }
    if (
        type(header) is not dict
        or set(header) != {"video", "audio", "__metadata__"}
        or header["__metadata__"] != metadata
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint metadata does not match its receipt."
        )
    shapes = {
        "video": [
            1,
            24,
            latent_frames_for_video_frames(frames),
            identity.height // 16,
            identity.width // 16,
        ],
        "audio": [2, 32, audio_tick_at_frame(frames)],
    }
    spans = []
    for key, shape in shapes.items():
        entry = header[key]
        if (
            type(entry) is not dict
            or set(entry) != {"dtype", "shape", "data_offsets"}
            or entry["dtype"] != "F32"
            or entry["shape"] != shape
            or any(type(dimension) is not int for dimension in entry["shape"])
        ):
            raise QueueRecoveryRuntimeError(
                "H3 cumulative checkpoint tensor geometry is invalid."
            )
        offsets = entry["data_offsets"]
        size = 4
        for dimension in shape:
            size *= dimension
        if (
            type(offsets) is not list
            or len(offsets) != 2
            or any(type(value) is not int for value in offsets)
            or offsets[1] - offsets[0] != size
        ):
            raise QueueRecoveryRuntimeError(
                "H3 cumulative checkpoint tensor offsets are invalid."
            )
        spans.append(offsets)
    spans.sort()
    if (
        spans[0][0] != 0
        or spans[0][1] != spans[1][0]
        or spans[1][1] != len(payload) - 8 - header_bytes
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint tensor spans do not reconcile."
        )


def validate_h3_cumulative_receipt(
    receipt,
    expected_identity: H3CumulativeIdentity,
    expected_dependency: str,
) -> None:
    """Validate the exact receipt schema without accessing files or tensors."""
    if not isinstance(expected_identity, H3CumulativeIdentity):
        raise QueueRecoveryRuntimeError("H3 cumulative expected identity is required.")
    expected_identity.__post_init__()
    expected_keys = {
        "schema",
        "mode",
        "storage",
        "basename",
        "sha256",
        "size",
        "identity",
        "dependency",
        "frame_count",
        "published_frames",
    }
    if (
        type(receipt) is not dict
        or set(receipt) != expected_keys
        or receipt["schema"] != 1
        or type(receipt["schema"]) is not int
        or receipt["mode"] != "cumulative_append"
        or receipt["storage"] != "recovery_staging"
    ):
        raise QueueRecoveryRuntimeError("H3 cumulative checkpoint receipt is invalid.")
    if (
        receipt["identity"] != asdict(expected_identity)
        or receipt["dependency"] != expected_dependency
        or type(expected_dependency) is not str
        or _UNIT.fullmatch(expected_dependency) is None
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint identity or dependency does not match."
        )
    digest, size = receipt["sha256"], receipt["size"]
    if (
        type(digest) is not str
        or _SHA.fullmatch(digest) is None
        or type(size) is not int
        or not 1 <= size <= _MAX_FILE_BYTES
        or receipt["basename"]
        != f"unit-{expected_identity.job_id}-h3-av-{digest}.safetensors"
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint file receipt is invalid."
        )
    if (
        not is_legal_h3_video_frame_count(receipt["frame_count"])
        or type(receipt["published_frames"]) is not int
        or not 1 <= receipt["published_frames"] <= receipt["frame_count"]
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint frame geometry is invalid."
        )


def _verified_checkpoint_payload(project_directory, receipt, identity, dependency):
    validate_h3_cumulative_receipt(receipt, identity, dependency)
    staging, directories = _directories(project_directory, create=False)
    directory = _open_private_directory(staging)
    try:
        payload = _read_exact_file_at(
            directory, receipt["basename"], maximum_bytes=_MAX_FILE_BYTES
        )
        _verify_directories(directories)
    finally:
        os.close(directory)
    if (
        len(payload) != receipt["size"]
        or hashlib.sha256(payload).hexdigest() != receipt["sha256"]
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint hash or size does not match."
        )
    _validate_header(payload, receipt, identity, dependency)
    # Both arrays are contiguous little-endian float32 spans. Scan bounded
    # views without materializing full tensors during queue skip validation.
    start = 8 + int.from_bytes(payload[:8], "little")
    view = memoryview(payload)
    for offset in range(start, len(payload), 1024 * 1024):
        if not np.isfinite(
            np.frombuffer(view[offset : offset + 1024 * 1024], dtype="<f4")
        ).all():
            raise QueueRecoveryRuntimeError(
                "H3 cumulative checkpoint contains non-finite AV values."
            )
    return payload


def verify_h3_cumulative_checkpoint(
    project_directory, receipt, expected_identity, expected_dependency
):
    """Verify bytes, header and finite AV data without restoring tensor state."""
    _verified_checkpoint_payload(
        project_directory, receipt, expected_identity, expected_dependency
    )


def load_h3_cumulative_checkpoint(
    project_directory,
    receipt: dict,
    expected_identity: H3CumulativeIdentity,
    expected_dependency: str,
) -> H3CumulativeRecovery:
    """Verify one bounded no-follow file before allocating its tensor state."""
    payload = _verified_checkpoint_payload(
        project_directory, receipt, expected_identity, expected_dependency
    )
    try:
        tensors = load(payload)
        state = H3CumulativeLatents(
            tensors["video"],
            tensors["audio"],
            receipt["frame_count"],
            receipt["published_frames"],
        )
    except Exception as error:
        raise QueueRecoveryRuntimeError(
            "H3 cumulative checkpoint tensors could not be restored."
        ) from error
    _validate(state, expected_identity, expected_dependency)
    return H3CumulativeRecovery(state, expected_identity, expected_dependency)
