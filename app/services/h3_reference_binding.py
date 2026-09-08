"""Bind and pin selected H3 references after caller-owned media admission.

The binding builder performs no filesystem access. The caller supplies a
validator whose exact ``True`` result authorizes each selected descriptor.
Materializers consume only those returned bindings: images become detached PIL
pixels, while video/audio bytes are copied into an identity-tracked private
directory. A hard crash before an empty destination identity is journaled can
leave only that zero-byte hidden staging temporary; it cannot strand earlier
copied media or authorize its removal. Paths and descriptor metadata never
assign semantic roles.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import hmac
import io
import json
import os
import re
import stat
from typing import Any, Callable
import uuid

from models.minimax_h3.reference_manifest import (
    MINIMAX_H3_MAX_REFERENCE_AUDIOS,
    MINIMAX_H3_MAX_REFERENCE_IMAGES,
    MINIMAX_H3_MAX_REFERENCE_VIDEOS,
    MINIMAX_H3_MAX_REFERENCES,
)
from services.h3_reference_inputs import VIDEO_KEYS, selected_h3_video_slots


_AUDIO_KEYS = ("audio_guide", "audio_guide2", "audio_guide3")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_REFERENCE_IMAGE_BYTES = 500 * 1024 * 1024
_SAFE_MEDIA_SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,10}\Z")
_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_H3_REFERENCE_JOURNAL_RE = re.compile(
    r"\.h3ref-(?P<job>[A-Za-z0-9][A-Za-z0-9._-]{0,255})"
    r"-(?P<nonce>[0-9a-f]{32})\.json\Z"
)
_MAX_REFERENCE_JOURNAL_BYTES = 64 * 1024
_REFERENCE_JOURNAL_SCHEMA = 1
_POSIX_REFERENCE_FILE_SUPPORT = os.name == "posix" and hasattr(os, "fchmod")
_DIRECT_LATE_DEPENDENCY_KEYS = (
    "dependency",
    "mode",
    "video_slot",
    "boundary_type",
    "fps",
    "audio_sample_rate",
    "audio_channels",
    "overlap_frames",
    "discard_frames",
)


class H3ReferenceBindingError(ValueError):
    """Selected reference inputs do not have one exact authorized binding."""


def _snapshot_json_object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise H3ReferenceBindingError(f"{label} must be an object.")
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        snapshot = json.loads(encoded)
    except (RecursionError, TypeError, ValueError, OverflowError) as error:
        raise H3ReferenceBindingError(f"{label} must be finite JSON metadata.") from error
    if type(snapshot) is not dict:
        raise H3ReferenceBindingError(f"{label} must be an object.")
    return snapshot


def _strict_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise H3ReferenceBindingError(
            f"Selected H3 reference {field} must be a nonblank exact path."
        )
    return value


def _descriptor_index(descriptors: object) -> dict[str, list[Mapping[str, Any]]]:
    if not isinstance(descriptors, Sequence) or isinstance(descriptors, (str, bytes)):
        raise H3ReferenceBindingError("H3 reference descriptors must be a sequence.")
    by_field: dict[str, list[Mapping[str, Any]]] = {}
    for ordinal, descriptor in enumerate(descriptors, start=1):
        if not isinstance(descriptor, Mapping):
            raise H3ReferenceBindingError(
                f"H3 reference descriptor {ordinal} must be an object."
            )
        field = descriptor.get("field")
        if not isinstance(field, str) or not field.strip() or field != field.strip():
            raise H3ReferenceBindingError(
                f"H3 reference descriptor {ordinal} has an invalid field."
            )
        by_field.setdefault(field, []).append(descriptor)
    return by_field


def _late_dependency_snapshot(descriptor: Mapping[str, Any]) -> dict[str, Any] | None:
    nested = descriptor.get("late_dependency")
    direct = {
        key: descriptor[key]
        for key in _DIRECT_LATE_DEPENDENCY_KEYS
        if key in descriptor
    }
    if nested is not None and direct:
        raise H3ReferenceBindingError(
            "A selected H3 reference descriptor has ambiguous late dependency metadata."
        )
    if nested is not None:
        snapshot = _snapshot_json_object(
            nested, label="H3 late dependency metadata"
        )
        return snapshot
    if direct:
        return _snapshot_json_object(
            direct, label="H3 late dependency metadata"
        )
    return None


def _bind_descriptor(
    field: str,
    path: object,
    *,
    by_field: Mapping[str, list[Mapping[str, Any]]],
    validate_descriptor: Callable[[Mapping[str, Any]], object],
) -> dict[str, Any]:
    selected_path = _strict_path(path, field=field)
    matches = by_field.get(field, [])
    if not matches:
        raise H3ReferenceBindingError(
            f"Selected H3 reference {field} has no frozen input descriptor."
        )
    if len(matches) != 1:
        raise H3ReferenceBindingError(
            f"Selected H3 reference {field} has ambiguous input descriptors."
        )
    descriptor = _snapshot_json_object(
        matches[0], label=f"Selected H3 reference {field} descriptor"
    )
    descriptor_path = descriptor.get("path")
    if descriptor_path != selected_path:
        raise H3ReferenceBindingError(
            f"Selected H3 reference {field} no longer matches its frozen path."
        )
    digest = descriptor.get("sha256")
    size = descriptor.get("size")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise H3ReferenceBindingError(
            f"Selected H3 reference {field} has an invalid SHA-256 identity."
        )
    if type(size) is not int or size < 0:
        raise H3ReferenceBindingError(
            f"Selected H3 reference {field} has an invalid byte size."
        )
    late_dependency = _late_dependency_snapshot(descriptor)
    validation_copy = _snapshot_json_object(
        descriptor, label=f"Selected H3 reference {field} descriptor"
    )
    try:
        authorized = validate_descriptor(validation_copy)
    except InterruptedError:
        raise
    except Exception as error:
        raise H3ReferenceBindingError(
            f"Selected H3 reference {field} could not be revalidated."
        ) from error
    if authorized is not True:
        raise H3ReferenceBindingError(
            f"Selected H3 reference {field} is no longer authorized."
        )
    result: dict[str, Any] = {
        "path": selected_path,
        "source_key": field,
        "sha256": digest,
        "size": size,
    }
    if late_dependency is not None:
        result["late_dependency"] = late_dependency
    return result


def _raw_mapping(raw_inputs: object) -> Mapping[str, Any]:
    if not isinstance(raw_inputs, Mapping):
        raise H3ReferenceBindingError("H3 reference inputs must be an object.")
    return raw_inputs


def _selection_flags(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise H3ReferenceBindingError(f"{key} must be text.")
    return value


def build_h3_reference_binding(
    raw_inputs: object,
    *,
    descriptors: object,
    validate_descriptor: Callable[[Mapping[str, Any]], object],
    has_audio: Callable[[str], object],
) -> list[dict[str, Any]]:
    """Return the ordered mapper manifest for exact authorized raw inputs.

    Images retain list order. Videos retain native selected physical-slot order.
    In soundtrack mode, each selected video owns one paired audio reference and
    stale standalone audio guides are suppressed. Otherwise A/B/C select the
    three standalone audio fields. No role or intent is inferred.
    """

    raw = _raw_mapping(raw_inputs)
    if not callable(validate_descriptor) or not callable(has_audio):
        raise H3ReferenceBindingError("H3 reference validators must be callable.")
    source_audio_requested = raw.get("source_audio_requested", False)
    if type(source_audio_requested) is not bool:
        raise H3ReferenceBindingError("source_audio_requested must be boolean.")
    if source_audio_requested:
        raise H3ReferenceBindingError(
            "Experimental source audio cannot be bound without explicit runtime support."
        )

    descriptor_map = _descriptor_index(descriptors)
    images = raw.get("image_refs", [])
    if images is None:
        images = []
    if type(images) is not list:
        raise H3ReferenceBindingError("image_refs must be a list of exact paths.")

    manifest: list[dict[str, Any]] = []
    for index, path in enumerate(images):
        item = _bind_descriptor(
            f"image_refs:{index}", path,
            by_field=descriptor_map,
            validate_descriptor=validate_descriptor,
        )
        manifest.append({"type": "image", **item})

    video_flags = _selection_flags(raw, "video_prompt_type")
    video_values = tuple(raw.get(key) for key in VIDEO_KEYS)
    selected_videos = selected_h3_video_slots(video_flags, video_values)
    for slot, path in selected_videos:
        field = f"{VIDEO_KEYS[slot - 1]}:0"
        item = _bind_descriptor(
            field, path,
            by_field=descriptor_map,
            validate_descriptor=validate_descriptor,
        )
        manifest.append({"type": "video", **item})

    audio_flags = _selection_flags(raw, "audio_prompt_type")
    if "K" in audio_flags:
        if not selected_videos:
            raise H3ReferenceBindingError(
                "Reference-video soundtrack mode needs a selected video."
            )
        for item in manifest:
            if item["type"] != "video":
                continue
            try:
                soundtrack_present = has_audio(item["path"])
            except InterruptedError:
                raise
            except Exception as error:
                raise H3ReferenceBindingError(
                    "A selected reference video soundtrack could not be verified."
                ) from error
            if soundtrack_present is not True:
                raise H3ReferenceBindingError(
                    "A selected reference video has no authorized soundtrack."
                )
            item["include_audio"] = True
            item["has_audio"] = True
    else:
        for letter, key in zip("ABC", _AUDIO_KEYS):
            if letter not in audio_flags:
                continue
            item = _bind_descriptor(
                f"{key}:0", raw.get(key),
                by_field=descriptor_map,
                validate_descriptor=validate_descriptor,
            )
            manifest.append({"type": "audio", **item})

    counts = {
        kind: sum(item["type"] == kind for item in manifest)
        for kind in ("image", "video", "audio")
    }
    effective_audio_count = (
        counts["video"] if "K" in audio_flags else counts["audio"]
    )
    visual_count = counts["image"] + counts["video"]
    if counts["image"] > MINIMAX_H3_MAX_REFERENCE_IMAGES:
        raise H3ReferenceBindingError("H3 accepts at most 9 image references.")
    if counts["video"] > MINIMAX_H3_MAX_REFERENCE_VIDEOS:
        raise H3ReferenceBindingError("H3 accepts at most 3 video references.")
    if effective_audio_count > MINIMAX_H3_MAX_REFERENCE_AUDIOS:
        raise H3ReferenceBindingError("H3 accepts at most 3 audio references.")
    if len(manifest) > MINIMAX_H3_MAX_REFERENCES:
        raise H3ReferenceBindingError("H3 accepts at most 12 references.")
    if effective_audio_count > visual_count:
        raise H3ReferenceBindingError(
            "H3 audio references cannot exceed visual references."
        )
    return manifest


def _stable_file_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def materialize_h3_reference_image(binding: object):
    """Decode one already-authorized image binding into detached PIL pixels.

    This function grants no media access. The caller must first obtain
    ``binding`` from :func:`build_h3_reference_binding` using its authoritative
    descriptor validator. The file is then read once through a no-follow file
    descriptor and checked against that binding before any bytes are decoded.
    """

    if not isinstance(binding, Mapping) or binding.get("type") != "image":
        raise H3ReferenceBindingError(
            "An authorized H3 image binding is required for materialization."
        )
    path = _strict_path(binding.get("path"), field="image")
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise H3ReferenceBindingError(
            "The authorized H3 image binding path must be canonical."
        )
    digest = binding.get("sha256")
    size = binding.get("size")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise H3ReferenceBindingError(
            "The authorized H3 image binding has an invalid SHA-256 identity."
        )
    if (
        type(size) is not int
        or size < 1
        or size > _MAX_REFERENCE_IMAGE_BYTES
    ):
        raise H3ReferenceBindingError(
            "The authorized H3 image binding has an invalid byte size."
        )

    descriptor = -1
    try:
        path_before = os.lstat(path)
        if (
            not stat.S_ISREG(path_before.st_mode)
            or stat.S_ISLNK(path_before.st_mode)
            or path_before.st_nlink != 1
            or path_before.st_size != size
        ):
            raise H3ReferenceBindingError(
                "The selected H3 reference image changed before materialization."
            )
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size != size
            or _stable_file_identity(opened) != _stable_file_identity(path_before)
        ):
            raise H3ReferenceBindingError(
                "The selected H3 reference image changed while opening it."
            )

        chunks = []
        remaining = size + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        if (
            len(payload) != size
            or _stable_file_identity(after) != _stable_file_identity(opened)
            or not stat.S_ISREG(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or current.st_nlink != 1
            or _stable_file_identity(current) != _stable_file_identity(opened)
            or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), digest)
        ):
            raise H3ReferenceBindingError(
                "The selected H3 reference image changed during materialization."
            )
    except InterruptedError:
        raise
    except Exception as error:
        raise H3ReferenceBindingError(
            "The selected H3 reference image could not be materialized."
        ) from error
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except InterruptedError:
                raise
            except Exception as error:
                raise H3ReferenceBindingError(
                    "The selected H3 reference image could not be materialized."
                ) from error

    try:
        from PIL import Image as PILImage

        with io.BytesIO(payload) as encoded_image:
            with PILImage.open(encoded_image) as source:
                source.load()
                materialized = source.copy()
        materialized.load()
        return materialized
    except InterruptedError:
        raise
    except Exception as error:
        raise H3ReferenceBindingError(
            "The selected H3 reference image could not be decoded."
        ) from error


def _validated_stream_binding(binding: object) -> tuple[str, str, str, str, int, str]:
    if not isinstance(binding, Mapping):
        raise H3ReferenceBindingError(
            "H3 reference file bindings must be objects."
        )
    kind = binding.get("type")
    if kind not in {"video", "audio"}:
        raise H3ReferenceBindingError(
            "Only authorized H3 video and audio bindings can be copied."
        )
    source_key = binding.get("source_key")
    if (
        not isinstance(source_key, str)
        or not source_key.strip()
        or source_key != source_key.strip()
    ):
        raise H3ReferenceBindingError(
            "An H3 reference file binding has no exact source key."
        )
    path = _strict_path(binding.get("path"), field=source_key)
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise H3ReferenceBindingError(
            "An H3 reference file binding path must be canonical."
        )
    digest = binding.get("sha256")
    size = binding.get("size")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise H3ReferenceBindingError(
            "An H3 reference file binding has an invalid SHA-256 identity."
        )
    if type(size) is not int or size < 1:
        raise H3ReferenceBindingError(
            "An H3 reference file binding has an invalid byte size."
        )
    suffix = os.path.splitext(path)[1]
    if suffix and _SAFE_MEDIA_SUFFIX_RE.fullmatch(suffix) is None:
        raise H3ReferenceBindingError(
            "An H3 reference file binding has an unsafe extension."
        )
    return kind, source_key, path, digest, size, suffix


def _validated_private_staging_directory(staging_directory: object) -> tuple[str, os.stat_result]:
    try:
        directory = os.fspath(staging_directory)
    except TypeError as error:
        raise H3ReferenceBindingError(
            "H3 reference staging must be an existing private directory."
        ) from error
    if (
        not isinstance(directory, str)
        or not directory
        or not os.path.isabs(directory)
        or os.path.normpath(directory) != directory
        or os.path.realpath(directory) != directory
    ):
        raise H3ReferenceBindingError(
            "H3 reference staging must be an existing canonical directory."
        )
    try:
        info = os.lstat(directory)
    except InterruptedError:
        raise
    except Exception as error:
        raise H3ReferenceBindingError(
            "H3 reference staging is unavailable."
        ) from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o700)
    ):
        raise H3ReferenceBindingError(
            "H3 reference staging is not a private directory."
        )
    return directory, info


def _directory_identity(info: os.stat_result) -> dict[str, int]:
    return {"device": int(info.st_dev), "inode": int(info.st_ino)}


def _same_device_inode(info: os.stat_result, identity: Mapping[str, Any]) -> bool:
    return (
        type(identity.get("device")) is int
        and type(identity.get("inode")) is int
        and info.st_dev == identity["device"]
        and info.st_ino == identity["inode"]
    )


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short H3 reference snapshot write")
        offset += written


def _reference_token_projection(token: Mapping[str, Any]) -> dict[str, Any]:
    identity = token["identity"]
    return {
        "directory": token["directory"],
        "identity": {
            "device": identity["device"],
            "inode": identity["inode"],
            "files": identity["files"],
        },
        "paths": token["paths"],
    }


def _reference_journal_payload(
    token: Mapping[str, Any], *, sealed: bool, cleanup_started: bool,
) -> dict[str, Any]:
    identity = token["identity"]
    return {
        "schema": _REFERENCE_JOURNAL_SCHEMA,
        "job_id": identity["job_id"],
        "nonce": identity["nonce"],
        "sealed": sealed,
        "cleanup_started": cleanup_started,
        "token": _reference_token_projection(token),
    }


def _reference_journal_bytes(payload: object) -> bytes:
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, OverflowError) as error:
        raise H3ReferenceBindingError(
            "H3 reference cleanup metadata is invalid."
        ) from error
    if not 1 <= len(encoded) <= _MAX_REFERENCE_JOURNAL_BYTES:
        raise H3ReferenceBindingError(
            "H3 reference cleanup metadata is too large."
        )
    return encoded


def _read_reference_journal(
    path: str,
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    descriptor = -1
    try:
        path_before = os.lstat(path)
        if (
            not stat.S_ISREG(path_before.st_mode)
            or stat.S_ISLNK(path_before.st_mode)
            or path_before.st_nlink != 1
            or not 1 <= path_before.st_size <= _MAX_REFERENCE_JOURNAL_BYTES
            or stat.S_IMODE(path_before.st_mode) != 0o600
            or (
                expected_identity is not None
                and not _same_device_inode(path_before, expected_identity)
            )
        ):
            raise H3ReferenceBindingError(
                "H3 reference cleanup metadata is unsafe."
            )
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _stable_file_identity(opened) != _stable_file_identity(path_before)
        ):
            raise H3ReferenceBindingError(
                "H3 reference cleanup metadata changed while opening."
            )
        remaining = opened.st_size + 1
        chunks = []
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        if (
            len(raw) != opened.st_size
            or _stable_file_identity(after) != _stable_file_identity(opened)
            or not stat.S_ISREG(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or current.st_nlink != 1
            or _stable_file_identity(current) != _stable_file_identity(opened)
        ):
            raise H3ReferenceBindingError(
                "H3 reference cleanup metadata changed while reading."
            )
        payload = json.loads(raw.decode("utf-8"))
        if type(payload) is not dict or _reference_journal_bytes(payload) != raw:
            raise H3ReferenceBindingError(
                "H3 reference cleanup metadata is malformed."
            )
        return payload, {
            "name": os.path.basename(path),
            "device": int(opened.st_dev),
            "inode": int(opened.st_ino),
        }
    except InterruptedError:
        raise
    except H3ReferenceBindingError:
        raise
    except Exception as error:
        raise H3ReferenceBindingError(
            "H3 reference cleanup metadata is unavailable."
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_reference_directory(directory: str) -> None:
    descriptor = os.open(
        directory,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise H3ReferenceBindingError(
                "H3 reference staging changed during cleanup journaling."
            )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_reference_journal(
    token: dict[str, Any],
    *,
    sealed: bool,
    cleanup_started: bool = False,
) -> None:
    identity = token["identity"]
    directory = token["directory"]
    staging = os.path.dirname(directory)
    journal_name = f".h3ref-{identity['job_id']}-{identity['nonce']}.json"
    journal_path = os.path.join(staging, journal_name)
    payload = _reference_journal_payload(
        token, sealed=sealed, cleanup_started=cleanup_started,
    )
    encoded = _reference_journal_bytes(payload)
    previous = identity.get("journal")
    if previous is None:
        if os.path.lexists(journal_path):
            raise H3ReferenceBindingError(
                "H3 reference cleanup journal already exists."
            )
    else:
        old_payload, _old_identity = _read_reference_journal(
            journal_path, expected_identity=previous,
        )
        if not _journal_matches_token(old_payload, token):
            raise H3ReferenceBindingError(
                "H3 reference cleanup journal changed before replacement."
            )

    temporary_name = f".{journal_name}.{uuid.uuid4().hex}.tmp"
    temporary_path = os.path.join(staging, temporary_name)
    descriptor = -1
    temporary_identity = None
    try:
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        written = os.fstat(descriptor)
        if (
            not stat.S_ISREG(written.st_mode)
            or written.st_nlink != 1
            or written.st_size != len(encoded)
            or stat.S_IMODE(written.st_mode) != 0o600
        ):
            raise H3ReferenceBindingError(
                "H3 reference cleanup journal could not be written safely."
            )
        temporary_identity = _directory_identity(written)
        os.close(descriptor)
        descriptor = -1
        if previous is not None:
            current = os.lstat(journal_path)
            if not _same_device_inode(current, previous):
                raise H3ReferenceBindingError(
                    "H3 reference cleanup journal changed before replacement."
                )
        os.replace(temporary_path, journal_path)
        identity["journal"] = {
            "name": journal_name,
            **temporary_identity,
        }
        current = os.lstat(journal_path)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or not _same_device_inode(current, temporary_identity)
        ):
            raise H3ReferenceBindingError(
                "H3 reference cleanup journal changed during replacement."
            )
        _fsync_reference_directory(staging)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_identity is not None and os.path.lexists(temporary_path):
            try:
                current = os.lstat(temporary_path)
                if _same_device_inode(current, temporary_identity):
                    os.unlink(temporary_path)
                    _fsync_reference_directory(staging)
            except OSError:
                pass


def _copy_stream_binding(
    item: tuple[str, str, str, str, int, str],
    *,
    ordinal: int,
    directory: str,
    token: dict[str, Any],
) -> None:
    kind, source_key, source_path, expected_digest, expected_size, suffix = item
    name = f"{ordinal:02d}-{kind}{suffix}"
    destination_path = os.path.join(directory, name)
    staging = os.path.dirname(directory)
    temporary_path = ""
    temporary_identity: dict[str, int] | None = None
    source_descriptor = -1
    destination_descriptor = -1
    try:
        source_path_before = os.lstat(source_path)
        if (
            not stat.S_ISREG(source_path_before.st_mode)
            or stat.S_ISLNK(source_path_before.st_mode)
            or source_path_before.st_nlink != 1
            or source_path_before.st_size != expected_size
        ):
            raise H3ReferenceBindingError(
                "An H3 reference source changed before it was copied."
            )
        source_descriptor = os.open(
            source_path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        source_opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(source_opened.st_mode)
            or source_opened.st_nlink != 1
            or source_opened.st_size != expected_size
            or _stable_file_identity(source_opened)
                != _stable_file_identity(source_path_before)
        ):
            raise H3ReferenceBindingError(
                "An H3 reference source changed while it was opened."
            )

        for _attempt in range(8):
            temporary_name = (
                f".h3ref-copy-{token['identity']['nonce']}-{ordinal:02d}-"
                f"{uuid.uuid4().hex}.tmp"
            )
            temporary_path = os.path.join(staging, temporary_name)
            try:
                destination_descriptor = os.open(
                    temporary_path,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                break
            except FileExistsError:
                temporary_path = ""
        if destination_descriptor < 0:
            raise H3ReferenceBindingError(
                "An H3 reference snapshot destination could not be allocated."
            )
        destination_opened = os.fstat(destination_descriptor)
        if (
            not stat.S_ISREG(destination_opened.st_mode)
            or destination_opened.st_nlink != 1
            or destination_opened.st_size != 0
        ):
            raise H3ReferenceBindingError(
                "An H3 reference snapshot destination is unsafe."
            )
        file_identity = {
            "name": name,
            "device": int(destination_opened.st_dev),
            "inode": int(destination_opened.st_ino),
        }
        temporary_identity = _directory_identity(destination_opened)
        token["identity"]["files"][source_key] = file_identity
        token["paths"][source_key] = destination_path
        _write_reference_journal(token, sealed=False)
        if os.path.lexists(destination_path):
            raise H3ReferenceBindingError(
                "An H3 reference snapshot destination already exists."
            )
        os.rename(temporary_path, destination_path)
        temporary_path = ""
        destination_current = os.lstat(destination_path)
        if (
            not stat.S_ISREG(destination_current.st_mode)
            or stat.S_ISLNK(destination_current.st_mode)
            or destination_current.st_nlink != 1
            or not _same_device_inode(destination_current, file_identity)
        ):
            raise H3ReferenceBindingError(
                "An H3 reference snapshot destination changed during placement."
            )
        _fsync_reference_directory(directory)
        _fsync_reference_directory(staging)

        remaining = expected_size
        copied_digest = hashlib.sha256()
        while remaining:
            chunk = os.read(source_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise H3ReferenceBindingError(
                    "An H3 reference source ended before its admitted size."
                )
            copied_digest.update(chunk)
            _write_all(destination_descriptor, chunk)
            remaining -= len(chunk)
        if os.read(source_descriptor, 1):
            raise H3ReferenceBindingError(
                "An H3 reference source exceeded its admitted size."
            )
        os.fsync(destination_descriptor)
        source_after = os.fstat(source_descriptor)
        source_current = os.lstat(source_path)
        destination_after = os.fstat(destination_descriptor)
        destination_current = os.lstat(destination_path)
        if (
            _stable_file_identity(source_after)
                != _stable_file_identity(source_opened)
            or not stat.S_ISREG(source_current.st_mode)
            or stat.S_ISLNK(source_current.st_mode)
            or source_current.st_nlink != 1
            or _stable_file_identity(source_current)
                != _stable_file_identity(source_opened)
            or not hmac.compare_digest(
                copied_digest.hexdigest(), expected_digest
            )
            or not stat.S_ISREG(destination_after.st_mode)
            or destination_after.st_nlink != 1
            or destination_after.st_size != expected_size
            or not _same_device_inode(destination_after, file_identity)
            or not stat.S_ISREG(destination_current.st_mode)
            or stat.S_ISLNK(destination_current.st_mode)
            or destination_current.st_nlink != 1
            or _stable_file_identity(destination_current)
                != _stable_file_identity(destination_after)
        ):
            raise H3ReferenceBindingError(
                "An H3 reference source changed while it was copied."
            )
        os.fchmod(destination_descriptor, 0o400)
        if os.name != "nt" and stat.S_IMODE(os.fstat(destination_descriptor).st_mode) != 0o400:
            raise H3ReferenceBindingError(
                "An H3 reference snapshot could not be made read-only."
            )
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if temporary_path and temporary_identity is not None:
            try:
                current = os.lstat(temporary_path)
                if (
                    stat.S_ISREG(current.st_mode)
                    and not stat.S_ISLNK(current.st_mode)
                    and current.st_nlink == 1
                    and _same_device_inode(current, temporary_identity)
                    and current.st_size == 0
                ):
                    os.unlink(temporary_path)
                    _fsync_reference_directory(staging)
            except OSError:
                pass


def _reference_file_token_shape(
    token: object,
) -> tuple[str, Mapping[str, Any], Mapping[str, str]] | None:
    if not isinstance(token, Mapping) or set(token) != {"directory", "identity", "paths"}:
        return None
    directory = token.get("directory")
    identity = token.get("identity")
    paths = token.get("paths")
    if directory is None and identity is None and paths == {}:
        return "", {}, {}
    if (
        not isinstance(directory, str)
        or not os.path.isabs(directory)
        or os.path.normpath(directory) != directory
        or not isinstance(identity, Mapping)
        or set(identity) != {
            "device", "inode", "files", "job_id", "nonce", "journal",
        }
        or type(identity.get("device")) is not int
        or type(identity.get("inode")) is not int
        or not isinstance(identity.get("files"), Mapping)
        or len(identity["files"])
            > MINIMAX_H3_MAX_REFERENCE_VIDEOS + MINIMAX_H3_MAX_REFERENCE_AUDIOS
        or not isinstance(identity.get("job_id"), str)
        or _JOB_ID_RE.fullmatch(identity["job_id"]) is None
        or not isinstance(identity.get("nonce"), str)
        or re.fullmatch(r"[0-9a-f]{32}", identity["nonce"]) is None
        or not isinstance(identity.get("journal"), Mapping)
        or set(identity["journal"]) != {"name", "device", "inode"}
        or type(identity["journal"].get("device")) is not int
        or type(identity["journal"].get("inode")) is not int
        or not isinstance(paths, Mapping)
        or set(identity["files"]) != set(paths)
    ):
        return None
    expected_journal = f".h3ref-{identity['job_id']}-{identity['nonce']}.json"
    if (
        identity["journal"].get("name") != expected_journal
        or os.path.basename(directory) != f".h3-reference-{identity['nonce']}"
    ):
        return None
    return directory, identity, paths


def _journal_matches_token(payload: object, token: Mapping[str, Any]) -> bool:
    identity = token["identity"]
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {
            "schema", "job_id", "nonce", "sealed", "cleanup_started", "token",
        }
        or payload.get("schema") != _REFERENCE_JOURNAL_SCHEMA
        or payload.get("job_id") != identity["job_id"]
        or payload.get("nonce") != identity["nonce"]
        or type(payload.get("sealed")) is not bool
        or type(payload.get("cleanup_started")) is not bool
    ):
        return False
    current = _reference_token_projection(token)
    journal_token = payload.get("token")
    if payload["sealed"] or payload["cleanup_started"]:
        return journal_token == current
    if (
        not isinstance(journal_token, Mapping)
        or set(journal_token) != {"directory", "identity", "paths"}
        or journal_token.get("directory") != current["directory"]
        or not isinstance(journal_token.get("identity"), Mapping)
        or set(journal_token["identity"]) != {"device", "inode", "files"}
        or journal_token["identity"].get("device")
            != current["identity"]["device"]
        or journal_token["identity"].get("inode")
            != current["identity"]["inode"]
        or not isinstance(journal_token["identity"].get("files"), Mapping)
        or not isinstance(journal_token.get("paths"), Mapping)
    ):
        return False
    journal_files = journal_token["identity"]["files"]
    journal_paths = journal_token["paths"]
    return (
        set(journal_files) == set(journal_paths)
        and set(journal_files).issubset(current["identity"]["files"])
        and all(
            journal_files[key] == current["identity"]["files"][key]
            and journal_paths[key] == current["paths"][key]
            for key in journal_files
        )
    )


def _remove_reference_journal(
    token: Mapping[str, Any], *, sealed: bool,
) -> bool:
    identity = token["identity"]
    staging = os.path.dirname(token["directory"])
    journal_path = os.path.join(staging, identity["journal"]["name"])
    try:
        payload, _current_identity = _read_reference_journal(
            journal_path, expected_identity=identity["journal"],
        )
        if (
            not _journal_matches_token(payload, token)
            or payload["sealed"] is not sealed
            or payload["cleanup_started"] is not True
        ):
            return False
        journal_current = os.lstat(journal_path)
        if not _same_device_inode(journal_current, identity["journal"]):
            return False
        os.unlink(journal_path)
        _fsync_reference_directory(staging)
        return True
    except (H3ReferenceBindingError, OSError, TypeError, ValueError):
        return False


def _cleanup_h3_reference_files(token: object, *, sealed: bool) -> bool:
    shaped = _reference_file_token_shape(token)
    if shaped is None:
        return False
    directory, identity, paths = shaped
    if not directory:
        return True
    if not _POSIX_REFERENCE_FILE_SUPPORT:
        return False
    staging = os.path.dirname(directory)
    journal_path = os.path.join(staging, identity["journal"]["name"])
    try:
        _validated_private_staging_directory(staging)
        journal_payload, _journal_identity = _read_reference_journal(
            journal_path, expected_identity=identity["journal"],
        )
        if (
            not _journal_matches_token(journal_payload, token)
            or journal_payload["sealed"] is not sealed
        ):
            return False
        if not journal_payload["cleanup_started"]:
            _write_reference_journal(
                token, sealed=sealed, cleanup_started=True,
            )
    except (H3ReferenceBindingError, OSError, TypeError, ValueError):
        return False
    directory_descriptor = -1
    opened_files: list[tuple[int, str, str]] = []
    try:
        try:
            before = os.lstat(directory)
        except FileNotFoundError:
            return _remove_reference_journal(token, sealed=sealed)
        if (
            not stat.S_ISDIR(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or not _same_device_inode(before, identity)
            or stat.S_IMODE(before.st_mode)
                not in {0o500, 0o700}
        ):
            return False
        directory_descriptor = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_directory = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(opened_directory.st_mode)
            or not _same_device_inode(opened_directory, identity)
        ):
            return False
        files = identity["files"]
        expected_names = set()
        for source_key, record in files.items():
            if (
                not isinstance(source_key, str)
                or not isinstance(record, Mapping)
                or set(record) != {"name", "device", "inode"}
                or not isinstance(record.get("name"), str)
                or os.path.basename(record["name"]) != record["name"]
                or record["name"] in {"", ".", ".."}
                or type(record.get("device")) is not int
                or type(record.get("inode")) is not int
                or not isinstance(paths.get(source_key), str)
                or paths[source_key] != os.path.join(directory, record["name"])
            ):
                return False
            if record["name"] in expected_names:
                return False
            expected_names.add(record["name"])
        actual_names = set(os.listdir(directory))
        if not actual_names.issubset(expected_names):
            return False
        for source_key, record in files.items():
            path = paths[source_key]
            if record["name"] not in actual_names:
                continue
            current = os.lstat(path)
            if (
                not stat.S_ISREG(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or current.st_nlink != 1
                or not _same_device_inode(current, record)
                or stat.S_IMODE(current.st_mode)
                    not in {0o400, 0o600}
            ):
                return False
            file_descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            opened = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or not _same_device_inode(opened, record)
            ):
                os.close(file_descriptor)
                return False
            opened_files.append((file_descriptor, path, source_key))

        directory_current = os.lstat(directory)
        if (
            not stat.S_ISDIR(directory_current.st_mode)
            or stat.S_ISLNK(directory_current.st_mode)
            or not _same_device_inode(directory_current, identity)
            or not set(os.listdir(directory)).issubset(expected_names)
            or stat.S_IMODE(directory_current.st_mode)
                not in {0o500, 0o700}
        ):
            return False
        os.fchmod(directory_descriptor, 0o700)
        for file_descriptor, path, source_key in opened_files:
            os.fchmod(file_descriptor, 0o600)
            current = os.lstat(path)
            if not _same_device_inode(current, files[source_key]):
                return False
        for _file_descriptor, path, _source_key in opened_files:
            os.unlink(path)
        os.fsync(directory_descriptor)
    except (OSError, TypeError, ValueError):
        return False
    finally:
        for file_descriptor, _path, _source_key in opened_files:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        if directory_descriptor >= 0:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass
    try:
        current = os.lstat(directory)
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or not _same_device_inode(current, identity)
            or os.listdir(directory)
        ):
            return False
        os.rmdir(directory)
    except OSError:
        return False
    try:
        return _remove_reference_journal(token, sealed=sealed)
    except (OSError, TypeError, ValueError):
        return False


def cleanup_h3_reference_files(token: object) -> bool:
    """Remove only an unchanged materialization directory and its own files."""

    return _cleanup_h3_reference_files(token, sealed=True)


def _remove_unjournaled_empty_directory(token: Mapping[str, Any]) -> None:
    directory = token.get("directory")
    identity = token.get("identity")
    if not isinstance(directory, str) or not isinstance(identity, Mapping):
        return
    try:
        current = os.lstat(directory)
        if (
            stat.S_ISDIR(current.st_mode)
            and not stat.S_ISLNK(current.st_mode)
            and _same_device_inode(current, identity)
            and not os.listdir(directory)
        ):
            os.rmdir(directory)
    except OSError:
        pass


def materialize_h3_reference_files(
    bindings: object,
    staging_directory: object,
    job_id: object,
) -> dict[str, Any]:
    """Pin authorized video/audio bytes in a guarded private subdirectory.

    Images are intentionally omitted because
    :func:`materialize_h3_reference_image` owns their eager decoding. Video and
    audio inputs are streamed up to their already-admitted size, without a
    separate image-sized cap or any media decoding.
    """

    if (
        not isinstance(job_id, str)
        or _JOB_ID_RE.fullmatch(job_id) is None
        or len(os.fsencode(f".h3ref-{job_id}-{'0' * 32}.json")) > 255
    ):
        raise H3ReferenceBindingError(
            "H3 reference materialization requires a safe job ID."
        )
    if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)):
        raise H3ReferenceBindingError("H3 reference bindings must be a sequence.")
    selected = []
    seen_source_keys = set()
    for binding in bindings:
        if not isinstance(binding, Mapping) or binding.get("type") not in {
            "image", "video", "audio",
        }:
            raise H3ReferenceBindingError(
                "H3 reference bindings contain an unsupported item."
            )
        if binding.get("type") == "image":
            continue
        item = _validated_stream_binding(binding)
        if item[1] in seen_source_keys:
            raise H3ReferenceBindingError(
                "H3 reference file bindings contain a duplicate source key."
            )
        seen_source_keys.add(item[1])
        selected.append(item)
    if not selected:
        return {"directory": None, "identity": None, "paths": {}}
    if not _POSIX_REFERENCE_FILE_SUPPORT:
        raise H3ReferenceBindingError(
            "H3 video and audio reference materialization requires POSIX support."
        )

    staging, staging_info = _validated_private_staging_directory(staging_directory)
    directory = ""
    token: dict[str, Any] | None = None
    try:
        nonce = ""
        for _attempt in range(8):
            nonce = uuid.uuid4().hex
            directory = os.path.join(staging, f".h3-reference-{nonce}")
            try:
                os.mkdir(directory, 0o700)
                break
            except FileExistsError:
                directory = ""
        if not directory:
            raise H3ReferenceBindingError(
                "A unique H3 reference snapshot directory could not be allocated."
            )
        created = os.lstat(directory)
        staging_current = os.lstat(staging)
        if (
            os.path.dirname(directory) != staging
            or not stat.S_ISDIR(created.st_mode)
            or stat.S_ISLNK(created.st_mode)
            or not stat.S_ISDIR(staging_current.st_mode)
            or stat.S_ISLNK(staging_current.st_mode)
            or (staging_current.st_dev, staging_current.st_ino)
                != (staging_info.st_dev, staging_info.st_ino)
        ):
            raise H3ReferenceBindingError(
                "H3 reference snapshot directory changed during creation."
            )
        os.chmod(directory, 0o700)
        created = os.lstat(directory)
        if os.name != "nt" and stat.S_IMODE(created.st_mode) != 0o700:
            raise H3ReferenceBindingError(
                "H3 reference snapshot directory is not private."
            )
        token = {
            "directory": directory,
            "identity": {
                **_directory_identity(created),
                "files": {},
                "job_id": job_id,
                "nonce": nonce,
                "journal": None,
            },
            "paths": {},
        }
        _write_reference_journal(token, sealed=False)
        for ordinal, item in enumerate(selected, start=1):
            _copy_stream_binding(
                item, ordinal=ordinal, directory=directory, token=token,
            )
        directory_descriptor = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(directory_descriptor)
            if not _same_device_inode(opened, token["identity"]):
                raise H3ReferenceBindingError(
                    "H3 reference snapshot directory changed while sealing."
                )
            os.fsync(directory_descriptor)
            os.fchmod(directory_descriptor, 0o500)
            if os.name != "nt" and stat.S_IMODE(os.fstat(directory_descriptor).st_mode) != 0o500:
                raise H3ReferenceBindingError(
                    "H3 reference snapshot directory could not be sealed."
                )
        finally:
            os.close(directory_descriptor)
        _write_reference_journal(token, sealed=True)
        return token
    except (InterruptedError, KeyboardInterrupt, SystemExit):
        if token is not None:
            cleaned = _cleanup_h3_reference_files(token, sealed=False)
            if not cleaned and token["identity"].get("journal") is None:
                _remove_unjournaled_empty_directory(token)
        raise
    except Exception as error:
        if token is not None:
            cleaned = _cleanup_h3_reference_files(token, sealed=False)
            if not cleaned and token["identity"].get("journal") is None:
                _remove_unjournaled_empty_directory(token)
        elif directory:
            try:
                os.rmdir(directory)
            except OSError:
                pass
        raise H3ReferenceBindingError(
            "H3 reference files could not be materialized."
        ) from error


def cleanup_orphan_h3_reference_files(
    staging_directory: object,
    live_job_ids: object,
    maximum_removals: int = 64,
) -> int:
    """Remove identity-proven H3 snapshots for jobs absent from ``live_job_ids``."""

    if not _POSIX_REFERENCE_FILE_SUPPORT:
        return 0
    if (
        type(maximum_removals) is not int
        or not isinstance(live_job_ids, Sequence)
        or isinstance(live_job_ids, (str, bytes))
    ):
        return 0
    live = set()
    for job_id in live_job_ids:
        if not isinstance(job_id, str) or _JOB_ID_RE.fullmatch(job_id) is None:
            return 0
        live.add(job_id)
    try:
        staging, staging_info = _validated_private_staging_directory(
            staging_directory
        )
        entries = sorted(os.scandir(staging), key=lambda entry: entry.name)
    except InterruptedError:
        raise
    except (H3ReferenceBindingError, OSError, TypeError, ValueError):
        return 0

    removed = 0
    limit = max(0, min(1024, maximum_removals))
    for entry in entries:
        if removed >= limit:
            break
        match = _H3_REFERENCE_JOURNAL_RE.fullmatch(entry.name)
        if match is None or match.group("job") in live:
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            journal_path = os.path.join(staging, entry.name)
            payload, journal_identity = _read_reference_journal(journal_path)
            if (
                set(payload) != {
                    "schema", "job_id", "nonce", "sealed", "cleanup_started",
                    "token",
                }
                or payload.get("schema") != _REFERENCE_JOURNAL_SCHEMA
                or payload.get("job_id") != match.group("job")
                or payload.get("nonce") != match.group("nonce")
                or type(payload.get("sealed")) is not bool
                or type(payload.get("cleanup_started")) is not bool
                or not isinstance(payload.get("token"), Mapping)
            ):
                continue
            projection = payload["token"]
            if (
                set(projection) != {"directory", "identity", "paths"}
                or not isinstance(projection.get("identity"), Mapping)
            ):
                continue
            runtime_token = {
                "directory": projection.get("directory"),
                "identity": {
                    **dict(projection["identity"]),
                    "job_id": payload["job_id"],
                    "nonce": payload["nonce"],
                    "journal": journal_identity,
                },
                "paths": projection.get("paths"),
            }
            shaped = _reference_file_token_shape(runtime_token)
            if shaped is None or os.path.dirname(shaped[0]) != staging:
                continue
            staging_current = os.lstat(staging)
            if (
                not stat.S_ISDIR(staging_current.st_mode)
                or stat.S_ISLNK(staging_current.st_mode)
                or not _same_device_inode(
                    staging_current, _directory_identity(staging_info)
                )
            ):
                return removed
            if _cleanup_h3_reference_files(
                runtime_token, sealed=payload["sealed"],
            ):
                removed += 1
        except InterruptedError:
            raise
        except (H3ReferenceBindingError, OSError, TypeError, ValueError):
            continue
    return removed


__all__ = [
    "H3ReferenceBindingError",
    "build_h3_reference_binding",
    "cleanup_h3_reference_files",
    "cleanup_orphan_h3_reference_files",
    "materialize_h3_reference_image",
    "materialize_h3_reference_files",
]
