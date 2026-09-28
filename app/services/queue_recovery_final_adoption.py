"""Fail-closed adoption of producer-attested finals from private quarantine.

The queue journal is not the authority for this recovery boundary.  A complete
producer-attested final set can outlive that journal in the project-local
quarantine.  This module validates the complete dependency closure, writes an
immutable private plan, and publishes the whole job set without overwriting an
existing gallery entry.  It never creates or completes a runtime job.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Any, Callable, Iterator, Mapping

from services.queue_recovery_runtime import (
    MANIFEST_DIRECTORY,
    MAX_MANIFEST_BYTES,
    QueueRecoveryRuntimeError,
    recovery_unit_id,
    sha256_file,
    validate_artifact_descriptor,
)


_SCHEMA = 1
_QUARANTINE_PREFIX = re.compile(r"^[0-9a-f]{32}-(.+)$")
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_UNIT_ID = re.compile(r"^unit:v1:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FINAL_KINDS = {"h3_concat", "h3_delivery", "ordinary_repeat"}
_UNIT_KINDS = _FINAL_KINDS | {"h3_segment"}
_thread_lock = threading.RLock()


def _valid_h3_source_prefix_identity(value: object) -> bool:
    """Permit a one-segment final only with a complete sealed source policy."""
    if not isinstance(value, dict):
        return False
    return bool(
        type(value.get("version")) is int and value["version"] == 1
        and value.get("input_field") == "video_source:0"
        and type(value.get("source_native_frames")) is int
        and value["source_native_frames"] > 0
        and type(value.get("retained_frames")) is int
        and value["retained_frames"] > 0
        and type(value.get("output_fps")) in {int, float}
        and math.isfinite(value["output_fps"])
        and value["output_fps"] > 0
        and value.get("fit") == "contain"
        and value.get("conditioning") == "last_frame"
        and value.get("audio_policy") == "preserve_source_then_generated"
        and type(value.get("sha256")) is str
        and _SHA256.fullmatch(value["sha256"]) is not None
        and type(value.get("size")) is int and value["size"] > 0
        and "path" not in value
    )


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise QueueRecoveryRuntimeError(
            "Final-adoption evidence is not JSON-safe."
        ) from None


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_root(project_directory: os.PathLike[str] | str) -> Path:
    root = Path(os.path.abspath(os.fspath(project_directory)))
    try:
        info = os.lstat(root)
    except OSError:
        raise QueueRecoveryRuntimeError("Final-adoption project is missing.") from None
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise QueueRecoveryRuntimeError("Final-adoption project is unsafe.")
    return root


def _existing_private_directory(path: Path) -> tuple[Path, tuple[int, int]]:
    try:
        info = os.lstat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Final-adoption quarantine is unavailable."
        ) from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
        or (os.name != "nt" and stat.S_IMODE(opened.st_mode) & 0o077)
    ):
        raise QueueRecoveryRuntimeError("Final-adoption quarantine is not private.")
    return path, (info.st_dev, info.st_ino)


def _ensure_private_directory(path: Path) -> tuple[Path, tuple[int, int]]:
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = os.lstat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Final-adoption private state is unavailable."
        ) from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise QueueRecoveryRuntimeError("Final-adoption private state is unsafe.")
    return path, (info.st_dev, info.st_ino)


def _verify_directory(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = os.lstat(path)
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Final-adoption directory changed during access."
        ) from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (info.st_dev, info.st_ino) != identity
    ):
        raise QueueRecoveryRuntimeError(
            "Final-adoption directory changed during access."
        )


def _read_exact(path: Path, *, maximum_bytes: int) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Final-adoption evidence could not be opened safely."
        ) from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            raise QueueRecoveryRuntimeError(
                "Final-adoption evidence has an unsafe size or type."
            )
        chunks: list[bytes] = []
        remaining = int(before.st_size)
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise QueueRecoveryRuntimeError(
                    "Final-adoption evidence ended unexpectedly."
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise QueueRecoveryRuntimeError(
                "Final-adoption evidence changed while it was read."
            )
        after = os.fstat(descriptor)
        current = os.lstat(path)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise QueueRecoveryRuntimeError(
                "Final-adoption evidence changed while it was read."
            )
        return b"".join(chunks), after
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Final-adoption evidence could not be read safely."
        ) from None
    finally:
        os.close(descriptor)


def _create_only(path: Path, payload: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        existing, _info = _read_exact(path, maximum_bytes=MAX_MANIFEST_BYTES)
        if existing != payload:
            raise QueueRecoveryRuntimeError(
                "Final-adoption immutable evidence changed."
            )
        return
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Final-adoption evidence could not be created."
        ) from None
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError
            offset += written
        os.fsync(descriptor)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise QueueRecoveryRuntimeError(
            "Final-adoption evidence could not be persisted."
        ) from None
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


@contextmanager
def _serialized(lock_path: Path) -> Iterator[None]:
    with _thread_lock:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError
            os.fchmod(descriptor, 0o600)
            if info.st_size < 1:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            handle = os.fdopen(descriptor, "r+b", closefd=True)
            descriptor = -1
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
        except QueueRecoveryRuntimeError:
            raise
        except (OSError, EOFError):
            raise QueueRecoveryRuntimeError(
                "Final-adoption locking failed."
            ) from None
        finally:
            if 'descriptor' in locals() and descriptor >= 0:
                os.close(descriptor)


def _direct_name(value: Any) -> str | None:
    if (
        type(value) is str
        and value
        and value == os.path.basename(value)
        and not value.startswith(".")
        and len(value) <= 255
        and not any(ord(character) < 32 for character in value)
    ):
        return value
    return None


def _declared_position(meta: Mapping[str, Any]) -> tuple[int, int] | None:
    params = meta.get("params")
    multi_clip = params.get("multi_clip_info") if isinstance(params, dict) else None
    if not isinstance(multi_clip, dict):
        return None
    total = multi_clip.get("output_total")
    index = multi_clip.get("output_index")
    if (
        type(total) is not int
        or not 1 <= total <= 4096
        or type(index) is not int
        or not 0 <= index < total
    ):
        return None
    return total, index


def _candidate(
    quarantine: Path,
    sidecar_name: str,
    names: set[str],
    *,
    workspace: str,
) -> dict[str, Any] | None:
    match = _QUARANTINE_PREFIX.fullmatch(sidecar_name)
    if match is None or not match.group(1).endswith(".meta.json"):
        return None
    sidecar_bytes, sidecar_info = _read_exact(
        quarantine / sidecar_name,
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    try:
        meta = json.loads(sidecar_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return None
    if not isinstance(meta, dict):
        return None
    output = _direct_name(meta.get("output_filename"))
    if output is None:
        return None
    expected_sidecar = os.path.splitext(output)[0] + ".meta.json"
    if match.group(1) != expected_sidecar:
        return None
    media_matches = sorted(
        name for name in names
        if (
            (parsed := _QUARANTINE_PREFIX.fullmatch(name)) is not None
            and parsed.group(1) == output
        )
    )
    if len(media_matches) != 1:
        return None
    media_name = media_matches[0]
    try:
        media_size, media_sha = sha256_file(quarantine / media_name)
    except QueueRecoveryRuntimeError:
        return None
    job_id = meta.get("job_id")
    unit_id = meta.get("producer_unit_id")
    kind = meta.get("producer_unit_kind")
    variant = meta.get("producer_unit_variant")
    index = meta.get("producer_unit_index")
    dependencies = meta.get("producer_unit_dependencies")
    if dependencies is None and kind == "ordinary_repeat":
        dependencies = []
    settings = meta.get("producer_unit_settings")
    if settings is None and kind == "ordinary_repeat":
        settings = {}
    artifacts = meta.get("producer_unit_artifact_names")
    continuation = meta.get("producer_unit_continuation")
    position = _declared_position(meta)
    if position is None and kind == "ordinary_repeat":
        position = (1, 0)
    expected_role = "component" if kind == "h3_segment" else "final"
    # ``private`` is a Gallery-preview choice, not an authorization boundary.
    # Both boolean values are valid producer evidence; workspace confinement,
    # project-local quarantine, and the sealed artifact graph provide the
    # recovery boundary.
    private_ok = type(meta.get("private")) is bool
    if (
        type(job_id) is not str
        or _JOB_ID.fullmatch(job_id) is None
        or type(unit_id) is not str
        or _UNIT_ID.fullmatch(unit_id) is None
        or kind not in _UNIT_KINDS
        or type(variant) is not int
        or variant < 0
        or type(index) is not int
        or index < 0
        or not isinstance(dependencies, list)
        or any(type(value) is not str or _UNIT_ID.fullmatch(value) is None for value in dependencies)
        or not isinstance(settings, dict)
        or not isinstance(artifacts, list)
        or not artifacts
        or len(artifacts) > 4096
        or len(set(artifacts)) != len(artifacts)
        or any(_direct_name(value) is None for value in artifacts)
        or not private_ok
        or meta.get("workspace") != workspace
        or meta.get("artifact_class") != expected_role
        or meta.get("producer_artifact_class") != expected_role
        or position is None
        or meta.get("producer_media_size") != media_size
        or meta.get("producer_media_sha256") != media_sha
    ):
        return None
    continuation_sha = ""
    if continuation is not None:
        if (
            not isinstance(continuation, dict)
            or continuation.get("dependency") != unit_id
            or _direct_name(continuation.get("basename")) is None
            or continuation.get("storage") != "recovery_staging"
            or type(continuation.get("size")) is not int
            or continuation["size"] < 1
            or type(continuation.get("sha256")) is not str
            or _SHA256.fullmatch(continuation["sha256"]) is None
        ):
            return None
        continuation_sha = continuation["sha256"]
    try:
        expected_id = recovery_unit_id(
            job_id,
            kind,
            variant=variant,
            index=index,
            dependencies=dependencies,
            settings=settings,
        )
    except QueueRecoveryRuntimeError:
        return None
    if unit_id != expected_id:
        return None
    return {
        "artifact_names": tuple(sorted(artifacts)),
        "continuation_sha256": continuation_sha,
        "dependencies": tuple(dependencies),
        "dest_media": output,
        "dest_sidecar": expected_sidecar,
        "explicit": meta.get("explicit"),
        "job_id": job_id,
        "kind": kind,
        "media_sha256": media_sha,
        "media_size": media_size,
        "output_index": position[1],
        "output_total": position[0],
        "settings": settings,
        "sidecar_sha256": hashlib.sha256(sidecar_bytes).hexdigest(),
        "sidecar_size": int(sidecar_info.st_size),
        "source_media": media_name,
        "source_sidecar": sidecar_name,
        "private": meta.get("private"),
        "continuation": continuation,
        "unit_id": unit_id,
        "unit_index": index,
        "unit_variant": variant,
    }


def _discover(quarantine: Path, *, workspace: str) -> tuple[list[dict], int]:
    try:
        names = set(os.listdir(quarantine))
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Final-adoption quarantine could not be listed safely."
        ) from None
    if len(names) > 20_000:
        raise QueueRecoveryRuntimeError(
            "Final-adoption quarantine exceeds the bounded scan limit."
        )
    candidates: list[dict] = []
    rejected = 0
    for name in sorted(names):
        match = _QUARANTINE_PREFIX.fullmatch(name)
        if match is None or not match.group(1).endswith(".meta.json"):
            continue
        try:
            candidate = _candidate(
                quarantine,
                name,
                names,
                workspace=workspace,
            )
        except QueueRecoveryRuntimeError:
            candidate = None
        if candidate is None:
            rejected += 1
        else:
            candidates.append(candidate)
    return candidates, rejected


def _staging_unit(
    unit: Mapping[str, Any],
    *,
    job_id: str,
    kind: str,
) -> dict[str, Any]:
    """Validate the caller-sealed identity fields needed for one H3 unit."""
    if not isinstance(unit, Mapping) or unit.get("state") != "completed" or unit.get("kind") != kind:
        raise QueueRecoveryRuntimeError("H3 final-adoption unit is invalid.")
    variant, index = unit.get("variant"), unit.get("index")
    dependencies = unit.get("dependencies")
    settings = unit.get("settings", {})
    if (
        type(variant) is not int or variant < 0
        or type(index) is not int or index < 0
        or not isinstance(dependencies, list)
        or any(
            type(value) is not str or _UNIT_ID.fullmatch(value) is None
            for value in dependencies
        )
        or not isinstance(settings, Mapping)
    ):
        raise QueueRecoveryRuntimeError("H3 final-adoption unit is invalid.")
    try:
        expected_id = recovery_unit_id(
            job_id, kind, variant=variant, index=index,
            dependencies=dependencies, settings=settings,
        )
    except QueueRecoveryRuntimeError:
        raise QueueRecoveryRuntimeError("H3 final-adoption unit is invalid.") from None
    unit_id = unit.get("unit_id")
    artifacts = unit.get("artifacts")
    if (
        type(unit_id) is not str or _UNIT_ID.fullmatch(unit_id) is None
        or unit_id != expected_id or not isinstance(artifacts, list)
        or len(artifacts) != 1 or not isinstance(artifacts[0], Mapping)
    ):
        raise QueueRecoveryRuntimeError("H3 final-adoption unit is invalid.")
    descriptor = dict(artifacts[0])
    basename = _direct_name(descriptor.get("basename"))
    sidecar_basename = _direct_name(descriptor.get("sidecar_basename"))
    if (
        basename is None or sidecar_basename is None
        or sidecar_basename != os.path.splitext(basename)[0] + ".meta.json"
        or descriptor.get("producer_unit_id") != unit_id
        or type(descriptor.get("size")) is not int or descriptor["size"] < 1
        or type(descriptor.get("sha256")) is not str
        or _SHA256.fullmatch(descriptor["sha256"]) is None
        or type(descriptor.get("sidecar_size")) is not int or descriptor["sidecar_size"] < 1
        or type(descriptor.get("sidecar_sha256")) is not str
        or _SHA256.fullmatch(descriptor["sidecar_sha256"]) is None
    ):
        raise QueueRecoveryRuntimeError("H3 final-adoption artifact is invalid.")
    return {
        "artifacts": [descriptor],
        "dependencies": list(dependencies),
        "index": index,
        "kind": kind,
        "settings": dict(settings),
        "unit_id": unit_id,
        "variant": variant,
        "continuation": unit.get("continuation"),
    }


def _staging_job_policy(
    job: Mapping[str, Any], *, workspace: str,
) -> tuple[str, bool, bool]:
    if not isinstance(job, Mapping):
        raise QueueRecoveryRuntimeError("H3 final-adoption job is invalid.")
    job_id = job.get("id")
    private = job.get("private")
    explicit = job.get("explicit")
    if (
        type(job_id) is not str or _JOB_ID.fullmatch(job_id) is None
        or job.get("workspace") != workspace
        or type(private) is not bool or type(explicit) is not bool
    ):
        raise QueueRecoveryRuntimeError("H3 final-adoption job is invalid.")
    policy = job.get("access_policy")
    if policy is not None and (
        not isinstance(policy, Mapping)
        or policy.get("private") is not private or policy.get("explicit") is not explicit
    ):
        raise QueueRecoveryRuntimeError("H3 final-adoption job policy is invalid.")
    return job_id, private, explicit


def _staging_candidate_matches_unit(
    candidate: Mapping[str, Any],
    unit: Mapping[str, Any],
    *,
    job_id: str,
    private: bool,
    explicit: bool,
) -> bool:
    descriptor = unit["artifacts"][0]
    return (
        candidate.get("job_id") == job_id
        and candidate.get("unit_id") == unit["unit_id"]
        and candidate.get("kind") == unit["kind"]
        and candidate.get("unit_variant") == unit["variant"]
        and candidate.get("unit_index") == unit["index"]
        and candidate.get("dependencies") == tuple(unit["dependencies"])
        and candidate.get("settings") == unit["settings"]
        and candidate.get("artifact_names") == (descriptor["basename"],)
        and candidate.get("dest_media") == descriptor["basename"]
        and candidate.get("media_size") == descriptor.get("size")
        and candidate.get("media_sha256") == descriptor.get("sha256")
        and candidate.get("output_index") == unit["variant"]
        and candidate.get("private") is private
        and candidate.get("explicit") is explicit
        and candidate.get("continuation") == unit.get("continuation")
    )


def _h3_staging_names(
    workspace: str,
    job_id: str,
    unit: Mapping[str, Any],
) -> tuple[str, str]:
    descriptor = unit["artifacts"][0]
    seed = {
        "job_id": job_id,
        "media_sha256": descriptor["sha256"],
        "segment_unit_id": unit["unit_id"],
        "sidecar_sha256": descriptor["sidecar_sha256"],
        "workspace": workspace,
    }
    token = hashlib.sha256(_canonical_json(seed)).hexdigest()[:32]
    return (
        f"{token}-{descriptor['basename']}",
        f"{token}-{descriptor['sidecar_basename']}",
    )


def _check_h3_staging_name_budget(quarantine: Path, names: tuple[str, str]) -> int:
    try:
        name_max = int(os.pathconf(quarantine, "PC_NAME_MAX"))
    except (AttributeError, OSError, TypeError, ValueError):
        # Keep the preflight conservative on platforms without pathconf.
        name_max = 255
    if name_max <= 0:
        name_max = 255
    if any(len(os.fsencode(name)) > name_max for name in names):
        raise QueueRecoveryRuntimeError(
            "H3 segment names exceed the quarantine filesystem limit."
        )
    return name_max


def validate_h3_segment_staging_name_budget(
    project_directory: os.PathLike[str] | str,
    *,
    workspace: str,
    job: Mapping[str, Any],
    segment_unit: Mapping[str, Any],
) -> int:
    """Read-only preflight for the exact quarantine names used by H3 staging."""
    if _direct_name(workspace) is None:
        raise QueueRecoveryRuntimeError("H3 final-adoption workspace is invalid.")
    root = _safe_root(project_directory)
    job_id, _private, _explicit = _staging_job_policy(job, workspace=workspace)
    segment = _staging_unit(segment_unit, job_id=job_id, kind="h3_segment")
    recovery = root / MANIFEST_DIRECTORY
    _existing_private_directory(recovery)
    quarantine, quarantine_identity = _existing_private_directory(recovery / "quarantine")
    _verify_directory(quarantine, quarantine_identity)
    return _check_h3_staging_name_budget(
        quarantine,
        _h3_staging_names(workspace, job_id, segment),
    )


def _stage_file_matches(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    source_identity: tuple[int, int] | None = None,
    file_identity: tuple[int, int] | None = None,
) -> bool:
    try:
        info = os.lstat(path)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1
            or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600)
            or (
                source_identity is not None
                and (info.st_dev, info.st_ino) == source_identity
            )
            or (
                file_identity is not None
                and (info.st_dev, info.st_ino) != file_identity
            )
        ):
            return False
        actual_size, actual_sha256 = sha256_file(path)
        after = os.lstat(path)
    except (OSError, QueueRecoveryRuntimeError):
        return False
    return bool(
        actual_size == expected_size
        and actual_sha256 == expected_sha256
        and (info.st_dev, info.st_ino, info.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_mtime_ns)
    )


def _unlink_staged_if_owned(
    path: Path,
    identity: tuple[int, int],
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    """Remove only a byte-exact single-link entry created by this call."""
    try:
        if _stage_file_matches(
            path,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            file_identity=identity,
        ):
            os.unlink(path)
    except (OSError, QueueRecoveryRuntimeError):
        return


def _copy_stage_file(
    source: Path,
    quarantine: Path,
    destination_name: str,
    *,
    expected_size: int,
    expected_sha256: str,
    quarantine_identity: tuple[int, int],
) -> tuple[int, int]:
    """Copy one no-follow source to a private temp, then publish create-only."""
    source_fd = temp_fd = -1
    temp_path: Path | None = None
    published_identity: tuple[int, int] | None = None
    destination = quarantine / destination_name
    try:
        _verify_directory(quarantine, quarantine_identity)
        source_fd = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        source_before = os.fstat(source_fd)
        if (
            not stat.S_ISREG(source_before.st_mode)
            or source_before.st_nlink != 1
            or source_before.st_size != expected_size
        ):
            raise QueueRecoveryRuntimeError("H3 segment source changed.")
        temp_fd, temporary = tempfile.mkstemp(
            prefix=f".{destination_name}.", suffix=".tmp", dir=quarantine,
        )
        temp_path = Path(temporary)
        os.fchmod(temp_fd, 0o600)
        temp_info = os.fstat(temp_fd)
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > expected_size:
                raise QueueRecoveryRuntimeError("H3 segment source changed.")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(temp_fd, view)
                if written <= 0:
                    raise OSError
                view = view[written:]
        source_after = os.fstat(source_fd)
        current_source = os.lstat(source)
        if (
            copied != expected_size
            or digest.hexdigest() != expected_sha256
            or (source_before.st_dev, source_before.st_ino, source_before.st_size,
                source_before.st_mtime_ns)
            != (source_after.st_dev, source_after.st_ino, source_after.st_size,
                source_after.st_mtime_ns)
            or source_after.st_nlink != 1
            or (current_source.st_dev, current_source.st_ino)
            != (source_after.st_dev, source_after.st_ino)
        ):
            raise QueueRecoveryRuntimeError("H3 segment source changed.")
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = -1
        _verify_directory(quarantine, quarantine_identity)
        published_identity = (temp_info.st_dev, temp_info.st_ino)
        os.link(temp_path, destination, follow_symlinks=False)
        published = os.lstat(destination)
        if (published.st_dev, published.st_ino) != published_identity:
            raise QueueRecoveryRuntimeError("H3 segment copy could not be verified.")
        os.unlink(temp_path)
        temp_path = None
        _fsync_directory(quarantine)
        if not _stage_file_matches(
            destination,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            source_identity=(source_before.st_dev, source_before.st_ino),
            file_identity=published_identity,
        ):
            raise QueueRecoveryRuntimeError("H3 segment copy could not be verified.")
        return published_identity
    except (QueueRecoveryRuntimeError, OSError) as error:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
                temp_path = None
            except OSError:
                pass
        if published_identity is not None:
            _unlink_staged_if_owned(
                destination,
                published_identity,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
            try:
                _fsync_directory(quarantine)
            except OSError:
                pass
        if isinstance(error, QueueRecoveryRuntimeError):
            raise
        raise QueueRecoveryRuntimeError(
            "H3 segment copy could not be published safely."
        ) from None
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _valid_units(candidates: list[dict]) -> dict[str, tuple[dict, ...]]:
    grouped: dict[str, list[dict]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate["unit_id"], []).append(candidate)
    valid: dict[str, tuple[dict, ...]] = {}
    for unit_id, items in grouped.items():
        first = items[0]
        if any(
            item["job_id"] != first["job_id"]
            or item["kind"] != first["kind"]
            or item["dependencies"] != first["dependencies"]
            or item["settings"] != first["settings"]
            or item["artifact_names"] != first["artifact_names"]
            or item["continuation_sha256"] != first["continuation_sha256"]
            for item in items
        ):
            continue
        actual = {item["dest_media"] for item in items}
        if len(actual) != len(items) or actual != set(first["artifact_names"]):
            continue
        valid[unit_id] = tuple(sorted(items, key=lambda item: item["dest_media"]))
    return valid


def _dependency_closed(
    unit_id: str,
    valid_units: Mapping[str, tuple[dict, ...]],
    *,
    active: set[str] | None = None,
    memo: dict[str, bool] | None = None,
) -> bool:
    active = set() if active is None else active
    memo = {} if memo is None else memo
    if unit_id in memo:
        return memo[unit_id]
    if unit_id in active or len(active) >= 128 or unit_id not in valid_units:
        memo[unit_id] = False
        return False
    active.add(unit_id)
    item = valid_units[unit_id][0]
    result = all(
        dependency in valid_units
        and valid_units[dependency][0]["job_id"] == item["job_id"]
        and _dependency_closed(
            dependency,
            valid_units,
            active=active,
            memo=memo,
        )
        for dependency in item["dependencies"]
    )
    active.remove(unit_id)
    memo[unit_id] = result
    return result


def _semantic_dependencies_valid(
    unit_id: str,
    valid_units: Mapping[str, tuple[dict, ...]],
    *,
    _depth: int = 0,
    _memo: dict[str, bool] | None = None,
) -> bool:
    """Bind declared H3 dependency IDs back to their exact artifact hashes."""
    _memo = {} if _memo is None else _memo
    if unit_id in _memo:
        return _memo[unit_id]
    if _depth >= 128:
        _memo[unit_id] = False
        return False
    if not _dependency_closed(unit_id, valid_units):
        _memo[unit_id] = False
        return False
    item = valid_units[unit_id][0]
    dependencies = item["dependencies"]
    settings = item["settings"]
    if any(
        not _semantic_dependencies_valid(
            dependency,
            valid_units,
            _depth=_depth + 1,
            _memo=_memo,
        )
        for dependency in dependencies
    ):
        _memo[unit_id] = False
        return False
    if item["kind"] == "h3_segment" and dependencies:
        predecessor = valid_units[dependencies[-1]]
        actual_hashes = sorted(candidate["media_sha256"] for candidate in predecessor)
        result = bool(
            settings.get("predecessor_artifact_hashes") == actual_hashes
            and str(settings.get("predecessor_continuation_sha256") or "")
            == predecessor[0]["continuation_sha256"]
        )
        _memo[unit_id] = result
        return result
    if item["kind"] == "h3_concat":
        actual_hashes = []
        start_trims = []
        tail_trims = []
        try:
            for dependency in dependencies:
                predecessor = valid_units[dependency]
                actual_hashes.extend(candidate["media_sha256"] for candidate in predecessor)
                predecessor_settings = predecessor[0]["settings"]
                start_trims.append(int(predecessor_settings.get("discard_prefix_frames", 0)))
                tail_trims.append(int(predecessor_settings.get("trim_tail_frames", 0)))
        except (TypeError, ValueError):
            _memo[unit_id] = False
            return False
        result = bool(
            settings.get("component_hashes") == actual_hashes
            and settings.get("clip_start_frames") == start_trims
            and settings.get("clip_tail_frames") == tail_trims
        )
        first_settings = (
            valid_units[dependencies[0]][0]["settings"]
            if dependencies else None
        )
        expected_prefix = (
            first_settings.get("source_prefix")
            if isinstance(first_settings, dict) else None
        )
        result = bool(
            result
            and settings.get("source_prefix") == expected_prefix
            and (
                expected_prefix is None
                or _valid_h3_source_prefix_identity(expected_prefix)
            )
        )
        _memo[unit_id] = result
        return result
    if item["kind"] == "h3_delivery":
        native_hashes = settings.get("native_hashes")
        result = isinstance(native_hashes, list) and len(native_hashes) == len(dependencies)
        remaining: dict[str, Counter[str]] = {}
        if result:
            for dependency, native_hash in zip(dependencies, native_hashes):
                parent = valid_units[dependency]
                if (
                    not isinstance(native_hash, str)
                    or _SHA256.fullmatch(native_hash) is None
                    or parent[0]["kind"] not in {"h3_concat", "ordinary_repeat"}
                ):
                    result = False
                    break
                available = remaining.setdefault(
                    dependency,
                    Counter(candidate["media_sha256"] for candidate in parent),
                )
                if available[native_hash] <= 0:
                    result = False
                    break
                available[native_hash] -= 1
        _memo[unit_id] = result
        return result
    _memo[unit_id] = True
    return True


def stage_quarantined_h3_segment_for_final_adoption(
    project_directory: os.PathLike[str] | str,
    *,
    workspace: str,
    job: Mapping[str, Any],
    segment_unit: Mapping[str, Any],
    final_unit: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy one sealed H3 segment pair into private quarantine for a concat."""
    if _direct_name(workspace) is None:
        raise QueueRecoveryRuntimeError("H3 final-adoption workspace is invalid.")
    root = _safe_root(project_directory)
    job_id, private, explicit = _staging_job_policy(job, workspace=workspace)
    segment = _staging_unit(segment_unit, job_id=job_id, kind="h3_segment")
    final = _staging_unit(final_unit, job_id=job_id, kind="h3_concat")
    if (
        segment["unit_id"] not in final["dependencies"]
        or segment["variant"] != final["variant"]
        or final["dependencies"].index(segment["unit_id"]) != segment["index"]
        or len(set(final["dependencies"])) != len(final["dependencies"])
    ):
        raise QueueRecoveryRuntimeError("H3 final-adoption dependency graph is invalid.")

    from services.h3_audio_safety import DEFAULT_TARGET_DBTP, POLICY_VERSION
    peak_policy = {
        "policy_version": POLICY_VERSION,
        "target_dbtp": DEFAULT_TARGET_DBTP,
    }
    attestation = final_unit.get("attestation")
    true_peak = (
        attestation.get("h3_audio_true_peak")
        if isinstance(attestation, Mapping) else None
    )
    if (
        final["settings"].get("h3_audio_true_peak_policy") != peak_policy
        or not isinstance(true_peak, Mapping)
        or true_peak.get("policy_version") != POLICY_VERSION
        or true_peak.get("target_dbtp") != DEFAULT_TARGET_DBTP
        or true_peak.get("verified") is not True
    ):
        raise QueueRecoveryRuntimeError("H3 final true-peak attestation is invalid.")
    descriptor = segment["artifacts"][0]
    if not validate_artifact_descriptor(root, descriptor, producer_unit_id=segment["unit_id"]):
        raise QueueRecoveryRuntimeError("H3 segment does not match its sealed descriptor.")

    recovery = root / MANIFEST_DIRECTORY
    _existing_private_directory(recovery)
    quarantine, quarantine_identity = _existing_private_directory(recovery / "quarantine")
    adoption, adoption_identity = _ensure_private_directory(recovery / "final-adoption")
    with _serialized(adoption / "adoption.lock"):
        _verify_directory(adoption, adoption_identity)
        _verify_directory(quarantine, quarantine_identity)
        initial_candidates, _rejected = _discover(quarantine, workspace=workspace)
        final_candidates = _valid_units(initial_candidates).get(final["unit_id"])
        if (
            final_candidates is None
            or len(final_candidates) != 1
            or not _staging_candidate_matches_unit(final_candidates[0], final,
                job_id=job_id, private=private, explicit=explicit)
        ):
            raise QueueRecoveryRuntimeError(
                "Quarantined H3 final does not match its sealed unit."
            )
        final_candidate_before = dict(final_candidates[0])
        final_descriptor = final["artifacts"][0]
        staged_media_name, staged_sidecar_name = _h3_staging_names(
            workspace, job_id, segment,
        )
        _check_h3_staging_name_budget(
            quarantine, (staged_media_name, staged_sidecar_name),
        )
        staged_media = quarantine / staged_media_name
        staged_sidecar_path = quarantine / staged_sidecar_name
        source_media = root / descriptor["basename"]
        source_sidecar = root / descriptor["sidecar_basename"]
        media_exists = os.path.lexists(staged_media)
        sidecar_exists = os.path.lexists(staged_sidecar_path)
        if media_exists or sidecar_exists:
            try:
                source_media_info, source_sidecar_info = (
                    os.lstat(source_media), os.lstat(source_sidecar),
                )
            except OSError:
                raise QueueRecoveryRuntimeError("H3 segment source changed.") from None
            if media_exists and not _stage_file_matches(
                staged_media, expected_size=descriptor["size"],
                expected_sha256=descriptor["sha256"],
                source_identity=(source_media_info.st_dev, source_media_info.st_ino),
            ):
                raise QueueRecoveryRuntimeError("H3 segment quarantine name is occupied.")
            if sidecar_exists and not _stage_file_matches(
                staged_sidecar_path, expected_size=descriptor["sidecar_size"],
                expected_sha256=descriptor["sidecar_sha256"],
                source_identity=(source_sidecar_info.st_dev, source_sidecar_info.st_ino),
            ):
                raise QueueRecoveryRuntimeError("H3 segment quarantine name is occupied.")
        idempotent = media_exists and sidecar_exists
        created: list[tuple[Path, tuple[int, int], int, str]] = []
        try:
            for exists, source, destination_name, size_key, digest_key in (
                (media_exists, source_media, staged_media_name, "size", "sha256"),
                (
                    sidecar_exists,
                    source_sidecar,
                    staged_sidecar_name,
                    "sidecar_size",
                    "sidecar_sha256",
                ),
            ):
                if exists:
                    continue
                identity = _copy_stage_file(
                    source,
                    quarantine,
                    destination_name,
                    expected_size=descriptor[size_key],
                    expected_sha256=descriptor[digest_key],
                    quarantine_identity=quarantine_identity,
                )
                created.append((
                    quarantine / destination_name,
                    identity,
                    descriptor[size_key],
                    descriptor[digest_key],
                ))
            _verify_directory(quarantine, quarantine_identity)
            staged_candidates, _rejected = _discover(quarantine, workspace=workspace)
            staged_units = _valid_units(staged_candidates)
            staged_segment = staged_units.get(segment["unit_id"])
            staged_final = staged_units.get(final["unit_id"])
            if (
                staged_segment is None
                or len(staged_segment) != 1
                or not _staging_candidate_matches_unit(staged_segment[0], segment,
                    job_id=job_id, private=private, explicit=explicit)
                or staged_segment[0]["source_media"] != staged_media_name
                or staged_segment[0]["source_sidecar"] != staged_sidecar_name
                or staged_final is None
                or len(staged_final) != 1
                or staged_final[0]["sidecar_sha256"]
                != final_candidate_before["sidecar_sha256"]
                or staged_final[0]["media_sha256"]
                != final_candidate_before["media_sha256"]
                or not _semantic_dependencies_valid(final["unit_id"], staged_units)
            ):
                raise QueueRecoveryRuntimeError(
                    "H3 final dependency graph is not closed by the staged segment."
                )
        except BaseException:
            for path, identity, size, digest in reversed(created):
                _unlink_staged_if_owned(
                    path,
                    identity,
                    expected_size=size,
                    expected_sha256=digest,
                )
            if created:
                try:
                    _fsync_directory(quarantine)
                except OSError:
                    pass
            raise

        _verify_directory(quarantine, quarantine_identity)
        return {
            "schema_version": _SCHEMA,
            "workspace": workspace,
            "job_id": job_id,
            "segment_unit_id": segment["unit_id"],
            "segment_media_basename": staged_media_name,
            "segment_sidecar_basename": staged_sidecar_name,
            "segment_media_sha256": descriptor["sha256"],
            "segment_sidecar_sha256": descriptor["sidecar_sha256"],
            "final_unit_id": final["unit_id"],
            "final_media_sha256": final_candidate_before["media_sha256"],
            "final_sidecar_sha256": final_candidate_before["sidecar_sha256"],
            "final_sidecar_seal_match": (
                final_candidate_before["sidecar_sha256"]
                == final_descriptor["sidecar_sha256"]
            ),
            "idempotent": idempotent,
        }


def _complete_groups(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    valid_units = _valid_units(candidates)
    by_job: dict[str, list[dict]] = {}
    for unit_id, items in valid_units.items():
        if items[0]["kind"] in _FINAL_KINDS:
            by_job.setdefault(items[0]["job_id"], []).extend(items)
    complete: list[dict] = []
    incomplete: list[dict] = []
    for job_id, raw_items in sorted(by_job.items()):
        preferred = "h3_delivery" if any(
            item["kind"] == "h3_delivery" for item in raw_items
        ) else "h3_concat" if any(
            item["kind"] == "h3_concat" for item in raw_items
        ) else "ordinary_repeat"
        items = [item for item in raw_items if item["kind"] == preferred]
        totals = {item["output_total"] for item in items}
        total = next(iter(totals)) if len(totals) == 1 else 0
        positions = {item["output_index"] for item in items}
        outputs = {item["dest_media"] for item in items}
        units = {item["unit_id"] for item in items}
        exact_shape = bool(
            total
            and len(items) == total
            and positions == set(range(total))
            and len(outputs) == total
        )
        if preferred == "h3_concat":
            exact_shape = exact_shape and all(
                item["artifact_names"] == (item["dest_media"],)
                and item["unit_variant"] == item["output_index"]
                for item in items
            )
        else:
            exact_shape = exact_shape and len(units) == 1 and all(
                set(item["artifact_names"]) == outputs
                and item["unit_variant"] == 0
                and item["unit_index"] == 0
                for item in items
            )
        if preferred == "ordinary_repeat":
            closed = exact_shape and all(
                not item["dependencies"]
                or _semantic_dependencies_valid(item["unit_id"], valid_units)
                for item in items
            )
        else:
            closed = exact_shape and all(
                item["dependencies"]
                and _semantic_dependencies_valid(item["unit_id"], valid_units)
                for item in items
            )
        group = {
            "job_id": job_id,
            "kind": preferred,
            "output_total": total,
            "items": sorted(items, key=lambda item: item["output_index"]),
        }
        (complete if closed else incomplete).append(group)
    return complete, incomplete


def _entry_hash(path: Path, *, size: int, digest: str) -> bool:
    if not path.exists() or path.is_symlink():
        return False
    try:
        actual_size, actual_digest = sha256_file(path)
    except QueueRecoveryRuntimeError:
        return False
    return actual_size == size and actual_digest == digest


def _destination_mode(group: Mapping[str, Any], root: Path) -> str:
    """Classify one whole group as absent or byte-exact preexisting."""
    present = 0
    expected = len(group.get("items") or []) * 2
    for item in group.get("items") or []:
        for kind in ("sidecar", "media"):
            destination = root / item[f"dest_{kind}"]
            exists = destination.exists() or destination.is_symlink()
            if not exists:
                continue
            present += 1
            if not _entry_hash(
                destination,
                size=item[f"{kind}_size"],
                digest=item[f"{kind}_sha256"],
            ):
                raise QueueRecoveryRuntimeError(
                    "Final-adoption destination collision blocked the group."
                )
    if present == 0:
        return "publish"
    if present == expected:
        return "preexisting_exact"
    raise QueueRecoveryRuntimeError(
        "Final-adoption destination collision blocked the group."
    )


def _validate_preexisting_plan(
    plan: Mapping[str, Any],
    root: Path,
    quarantine: Path,
    *,
    fsync: bool = False,
) -> None:
    """Validate exact independent copies without consuming rollback evidence."""
    if plan.get("publication_mode") != "preexisting_exact":
        raise QueueRecoveryRuntimeError(
            "Final-adoption preexisting plan mode is invalid."
        )
    for item in plan.get("items") or []:
        for kind in ("sidecar", "media"):
            source = quarantine / item[f"source_{kind}"]
            destination = root / item[f"dest_{kind}"]
            size = item[f"{kind}_size"]
            digest = item[f"{kind}_sha256"]
            if (
                not _entry_hash(source, size=size, digest=digest)
                or not _entry_hash(destination, size=size, digest=digest)
            ):
                raise QueueRecoveryRuntimeError(
                    "Final-adoption exact preexisting evidence changed."
                )
            try:
                source_info = os.lstat(source)
                destination_info = os.lstat(destination)
            except OSError:
                raise QueueRecoveryRuntimeError(
                    "Final-adoption exact preexisting evidence changed."
                ) from None
            if (source_info.st_dev, source_info.st_ino) == (
                destination_info.st_dev,
                destination_info.st_ino,
            ):
                raise QueueRecoveryRuntimeError(
                    "Final-adoption exact preexisting evidence is not an independent copy."
                )
            if fsync:
                try:
                    descriptor = os.open(
                        destination,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    )
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                except OSError:
                    raise QueueRecoveryRuntimeError(
                        "Final-adoption exact preexisting evidence is not durable."
                    ) from None
    if fsync:
        _fsync_directory(root)


def _exact_pending_hardlink_pair(
    source: Path,
    destination: Path,
    *,
    size: int,
    digest: str,
) -> bool:
    """Validate the one crash window where link exists before source unlink."""
    try:
        source_info = os.lstat(source)
        destination_info = os.lstat(destination)
        if (
            not stat.S_ISREG(source_info.st_mode)
            or not stat.S_ISREG(destination_info.st_mode)
            or (source_info.st_dev, source_info.st_ino)
            != (destination_info.st_dev, destination_info.st_ino)
            or source_info.st_nlink != 2
            or destination_info.st_nlink != 2
            or source_info.st_size != size
        ):
            return False
        descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            actual = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                actual.update(chunk)
            refreshed = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current_source = os.lstat(source)
        current_destination = os.lstat(destination)
    except OSError:
        return False
    identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    return bool(
        identity
        == (refreshed.st_dev, refreshed.st_ino, refreshed.st_size, refreshed.st_mtime_ns)
        == (
            current_source.st_dev,
            current_source.st_ino,
            current_source.st_size,
            current_source.st_mtime_ns,
        )
        == (
            current_destination.st_dev,
            current_destination.st_ino,
            current_destination.st_size,
            current_destination.st_mtime_ns,
        )
        and refreshed.st_nlink == 2
        and current_source.st_nlink == 2
        and current_destination.st_nlink == 2
        and actual.hexdigest() == digest
    )


def _rollback(plan: Mapping[str, Any], root: Path, quarantine: Path) -> None:
    blocked = False
    for item in reversed(list(plan.get("items") or [])):
        for kind in ("media", "sidecar"):
            source = quarantine / str(item.get(f"source_{kind}") or "")
            destination = root / str(item.get(f"dest_{kind}") or "")
            size = item.get(f"{kind}_size")
            digest = item.get(f"{kind}_sha256")
            if type(size) is not int or type(digest) is not str or _SHA256.fullmatch(digest) is None:
                blocked = True
                continue
            source_exists = source.exists() and not source.is_symlink()
            destination_exists = destination.exists() and not destination.is_symlink()
            if source_exists and destination_exists:
                if not _exact_pending_hardlink_pair(
                    source,
                    destination,
                    size=size,
                    digest=digest,
                ):
                    blocked = True
                    continue
                try:
                    os.unlink(destination)
                except OSError:
                    blocked = True
                continue
            if source_exists and not _entry_hash(source, size=size, digest=digest):
                blocked = True
                continue
            if destination_exists and not _entry_hash(destination, size=size, digest=digest):
                blocked = True
                continue
            if destination_exists:
                try:
                    os.link(destination, source, follow_symlinks=False)
                    os.unlink(destination)
                except OSError:
                    blocked = True
            elif not source_exists:
                blocked = True
    _fsync_directory(root)
    _fsync_directory(quarantine)
    if blocked:
        raise QueueRecoveryRuntimeError(
            "Final-adoption rollback retained conflicting evidence."
        )


def _publish_one(
    source: Path,
    destination: Path,
    *,
    size: int,
    digest: str,
    linked_hook: Callable[[], None] | None = None,
) -> None:
    if destination.exists() or destination.is_symlink():
        raise QueueRecoveryRuntimeError("Final-adoption destination already exists.")
    if not _entry_hash(source, size=size, digest=digest):
        raise QueueRecoveryRuntimeError("Final-adoption source changed before publication.")
    try:
        os.link(source, destination, follow_symlinks=False)
        descriptor = os.open(
            destination,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if callable(linked_hook):
            linked_hook()
        os.unlink(source)
    except FileExistsError:
        raise QueueRecoveryRuntimeError(
            "Final-adoption destination already exists."
        ) from None
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Final-adoption publication failed."
        ) from None


def _plan_for(
    group: Mapping[str, Any],
    *,
    workspace: str,
    publication_mode: str,
) -> dict[str, Any]:
    items = []
    for candidate in group["items"]:
        items.append({
            key: candidate[key]
            for key in (
                "dest_media", "dest_sidecar", "media_sha256", "media_size",
                "output_index", "sidecar_sha256", "sidecar_size",
                "source_media", "source_sidecar", "unit_id",
            )
        })
    return {
        "schema_version": _SCHEMA,
        "workspace": workspace,
        "job_id": group["job_id"],
        "kind": group["kind"],
        "output_total": group["output_total"],
        "publication_mode": publication_mode,
        "items": items,
    }


def _validate_plan(
    plan: Mapping[str, Any], *, allowed_kinds: set[str] | frozenset[str] = _FINAL_KINDS,
) -> None:
    if (
        set(plan) != {
            "schema_version", "workspace", "job_id", "kind",
            "output_total", "publication_mode", "items",
        }
        or plan.get("schema_version") != _SCHEMA
        or _direct_name(plan.get("workspace")) is None
        or type(plan.get("job_id")) is not str
        or _JOB_ID.fullmatch(plan["job_id"]) is None
        or plan.get("kind") not in allowed_kinds
        or plan.get("publication_mode") not in {"publish", "preexisting_exact"}
        or type(plan.get("output_total")) is not int
        or not 1 <= plan["output_total"] <= 4096
        or not isinstance(plan.get("items"), list)
        or len(plan["items"]) != plan["output_total"]
    ):
        raise QueueRecoveryRuntimeError("Final-adoption immutable plan is invalid.")
    positions = set()
    destinations = set()
    sources = set()
    expected_keys = {
        "dest_media", "dest_sidecar", "media_sha256", "media_size",
        "output_index", "sidecar_sha256", "sidecar_size",
        "source_media", "source_sidecar", "unit_id",
    }
    for item in plan["items"]:
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise QueueRecoveryRuntimeError("Final-adoption immutable plan is invalid.")
        direct_names = [
            item.get(key)
            for key in ("dest_media", "dest_sidecar", "source_media", "source_sidecar")
        ]
        if any(_direct_name(name) is None for name in direct_names):
            raise QueueRecoveryRuntimeError("Final-adoption immutable plan is invalid.")
        source_media = _QUARANTINE_PREFIX.fullmatch(item["source_media"])
        source_sidecar = _QUARANTINE_PREFIX.fullmatch(item["source_sidecar"])
        if (
            source_media is None
            or source_sidecar is None
            or source_media.group(1) != item["dest_media"]
            or source_sidecar.group(1) != item["dest_sidecar"]
            or type(item.get("unit_id")) is not str
            or _UNIT_ID.fullmatch(item["unit_id"]) is None
            or type(item.get("output_index")) is not int
            or not 0 <= item["output_index"] < plan["output_total"]
            or any(
                type(item.get(key)) is not int or item[key] < 1
                for key in ("media_size", "sidecar_size")
            )
            or any(
                type(item.get(key)) is not str or _SHA256.fullmatch(item[key]) is None
                for key in ("media_sha256", "sidecar_sha256")
            )
        ):
            raise QueueRecoveryRuntimeError("Final-adoption immutable plan is invalid.")
        positions.add(item["output_index"])
        destinations.update((item["dest_media"], item["dest_sidecar"]))
        sources.update((item["source_media"], item["source_sidecar"]))
    if (
        positions != set(range(plan["output_total"]))
        or len(destinations) != plan["output_total"] * 2
        or len(sources) != plan["output_total"] * 2
    ):
        raise QueueRecoveryRuntimeError("Final-adoption immutable plan is invalid.")


def _load_private_json(path: Path) -> tuple[dict[str, Any], bytes]:
    payload, _info = _read_exact(path, maximum_bytes=MAX_MANIFEST_BYTES)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        raise QueueRecoveryRuntimeError(
            "Final-adoption private evidence is invalid."
        ) from None
    if not isinstance(value, dict):
        raise QueueRecoveryRuntimeError("Final-adoption private evidence is invalid.")
    return value, payload


def _binding_name(workspace: str, job_id: str) -> str:
    digest = hashlib.sha256(_canonical_json({
        "job_id": job_id,
        "workspace": workspace,
    })).hexdigest()
    return f"{digest}.json"


def _binding_for_plan(plan: Mapping[str, Any], plan_sha: str) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA,
        "workspace": plan["workspace"],
        "job_id": plan["job_id"],
        "plan_sha256": plan_sha,
    }


def _validate_binding(binding: Mapping[str, Any], *, filename: str) -> None:
    if (
        set(binding) != {"schema_version", "workspace", "job_id", "plan_sha256"}
        or binding.get("schema_version") != _SCHEMA
        or _direct_name(binding.get("workspace")) is None
        or type(binding.get("job_id")) is not str
        or _JOB_ID.fullmatch(binding["job_id"]) is None
        or type(binding.get("plan_sha256")) is not str
        or _SHA256.fullmatch(binding["plan_sha256"]) is None
        or filename != _binding_name(binding["workspace"], binding["job_id"])
    ):
        raise QueueRecoveryRuntimeError("Final-adoption job binding is invalid.")


def _recover_pending(
    plans: Path,
    receipts: Path,
    root: Path,
    quarantine: Path | None,
    *,
    allowed_kinds: set[str] | frozenset[str] = _FINAL_KINDS,
) -> None:
    plan_paths = sorted(plans.glob("*.json"))
    if len(plan_paths) > 4096:
        raise QueueRecoveryRuntimeError("Final-adoption plan limit was exceeded.")
    for plan_path in plan_paths:
        if plan_path.is_symlink() or _SHA256.fullmatch(plan_path.stem) is None:
            raise QueueRecoveryRuntimeError("Final-adoption plan directory is unsafe.")
        plan, payload = _load_private_json(plan_path)
        _validate_plan(plan, allowed_kinds=allowed_kinds)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != plan_path.stem or payload != _canonical_json(plan):
            raise QueueRecoveryRuntimeError("Final-adoption immutable plan changed.")
        receipt_path = receipts / plan_path.name
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt, receipt_payload = _load_private_json(receipt_path)
            if (
                receipt_path.is_symlink()
                or receipt_payload != _canonical_json(receipt)
                or receipt != {
                    "schema_version": _SCHEMA,
                    "plan_sha256": digest,
                    "state": "completed",
                }
            ):
                raise QueueRecoveryRuntimeError("Final-adoption receipt changed.")
            continue
        if quarantine is None:
            raise QueueRecoveryRuntimeError(
                "Final-adoption quarantine is unavailable for a pending plan."
            )
        if plan.get("publication_mode") == "preexisting_exact":
            _validate_preexisting_plan(plan, root, quarantine)
        else:
            _rollback(plan, root, quarantine)


def _receipt_jobs(
    receipts: Path,
    plans: Path,
    bindings: Path,
    root: Path,
    *,
    allowed_kinds: set[str] | frozenset[str] = _FINAL_KINDS,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    binding_paths = sorted(bindings.glob("*.json"))
    if len(binding_paths) > 4096:
        raise QueueRecoveryRuntimeError("Final-adoption binding limit was exceeded.")
    bound_plan_shas: set[str] = set()
    for binding_path in binding_paths:
        if binding_path.is_symlink() or _SHA256.fullmatch(binding_path.stem) is None:
            raise QueueRecoveryRuntimeError("Final-adoption binding directory is unsafe.")
        binding, binding_payload = _load_private_json(binding_path)
        _validate_binding(binding, filename=binding_path.name)
        if binding_payload != _canonical_json(binding):
            raise QueueRecoveryRuntimeError("Final-adoption job binding changed.")
        bound_plan_shas.add(binding["plan_sha256"])
        receipt_path = receipts / f"{binding['plan_sha256']}.json"
        if receipt_path.is_symlink():
            raise QueueRecoveryRuntimeError(
                "Final-adoption receipt directory is unsafe."
            )
        if not receipt_path.exists():
            continue
        receipt, receipt_payload = _load_private_json(receipt_path)
        if (
            receipt_payload != _canonical_json(receipt)
            or receipt.get("plan_sha256") != receipt_path.stem
            or receipt.get("state") != "completed"
            or receipt.get("schema_version") != _SCHEMA
        ):
            raise QueueRecoveryRuntimeError("Final-adoption receipt changed.")
        plan, plan_payload = _load_private_json(
            plans / f"{binding['plan_sha256']}.json"
        )
        _validate_plan(plan, allowed_kinds=allowed_kinds)
        if (
            hashlib.sha256(plan_payload).hexdigest() != receipt_path.stem
            or plan.get("workspace") != binding["workspace"]
            or plan.get("job_id") != binding["job_id"]
        ):
            raise QueueRecoveryRuntimeError("Final-adoption receipt plan changed.")
        exact = all(
            _entry_hash(
                root / item[f"dest_{kind}"],
                size=item[f"{kind}_size"],
                digest=item[f"{kind}_sha256"],
            )
            for item in plan.get("items") or []
            for kind in ("sidecar", "media")
        )
        total = int(plan.get("output_total", 0) or 0)
        jobs.append({
            "job_id": str(plan.get("job_id") or ""),
            "state": "adopted" if exact else "missing",
            "declared": total,
            "adopted": total if exact else 0,
            "missing": 0 if exact else total,
            "quarantined": 0,
            "output_files": (
                sorted(str(item["dest_media"]) for item in plan.get("items") or [])
                if exact else []
            ),
        })
    receipt_paths = sorted(receipts.glob("*.json"))
    if len(receipt_paths) > 4096 or any(
        path.is_symlink()
        or _SHA256.fullmatch(path.stem) is None
        or path.stem not in bound_plan_shas
        for path in receipt_paths
    ):
        raise QueueRecoveryRuntimeError("Final-adoption receipt is not job-bound.")
    return jobs


def _component_group_for_adopted_final(
    job: Mapping[str, Any],
    *,
    root: Path,
    plans: Path,
    bindings: Path,
    valid_units: Mapping[str, tuple[dict, ...]],
    workspace: str,
) -> dict[str, Any] | None:
    """Bind quarantined clips to every byte-exact published H3 concat."""
    if job.get("state") != "adopted":
        return None
    job_id = str(job.get("job_id") or "")
    binding, _payload = _load_private_json(
        bindings / _binding_name(workspace, job_id)
    )
    _validate_binding(binding, filename=_binding_name(workspace, job_id))
    plan, _payload = _load_private_json(
        plans / f"{binding['plan_sha256']}.json"
    )
    _validate_plan(plan)
    if plan["kind"] != "h3_concat":
        return None
    components: list[dict[str, Any]] = []
    seen: set[str] = set()
    for final in sorted(plan["items"], key=lambda item: item["output_index"]):
        sidecar_bytes, _info = _read_exact(
            root / final["dest_sidecar"], maximum_bytes=MAX_MANIFEST_BYTES,
        )
        if hashlib.sha256(sidecar_bytes).hexdigest() != final["sidecar_sha256"]:
            return None
        try:
            meta = json.loads(sidecar_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            return None
        if not isinstance(meta, dict):
            return None
        dependencies = meta.get("producer_unit_dependencies")
        settings = meta.get("producer_unit_settings")
        if (
            meta.get("job_id") != job_id
            or meta.get("workspace") != workspace
            or meta.get("producer_unit_kind") != "h3_concat"
            or meta.get("producer_unit_id") != final["unit_id"]
            or meta.get("producer_unit_variant") != final["output_index"]
            or not isinstance(dependencies, list)
            or not dependencies
            or (
                len(dependencies) == 1
                and not _valid_h3_source_prefix_identity(
                    settings.get("source_prefix")
                    if isinstance(settings, dict) else None
                )
            )
            or not isinstance(settings, dict)
        ):
            return None
        closure = dict(valid_units)
        closure[final["unit_id"]] = ({
            "job_id": job_id,
            "kind": "h3_concat",
            "dependencies": tuple(dependencies),
            "settings": settings,
        },)
        if not _semantic_dependencies_valid(final["unit_id"], closure):
            return None
        for index, unit_id in enumerate(dependencies):
            unit = valid_units.get(unit_id)
            if (
                unit_id in seen
                or unit is None
                or len(unit) != 1
                or unit[0]["kind"] != "h3_segment"
                or unit[0]["job_id"] != job_id
                or unit[0]["unit_variant"] != final["output_index"]
                or unit[0]["unit_index"] != index
            ):
                return None
            seen.add(unit_id)
            components.append(dict(unit[0], output_index=len(components)))
    if not components or len(components) > 4096:
        return None
    return {
        "job_id": job_id,
        "kind": "h3_segment",
        "output_total": len(components),
        "items": components,
    }


def _adopt_components_for_receipted_finals(
    *,
    root: Path,
    quarantine: Path | None,
    recovery: Path,
    workspace: str,
    final_jobs: list[dict],
    final_plans: Path,
    final_bindings: Path,
) -> None:
    """Publish only the complete component closure of an adopted final."""
    component_adoption = recovery / "component-adoption"
    if quarantine is None and not component_adoption.exists():
        return
    if (
        not component_adoption.exists()
        and not any(job.get("state") == "adopted" for job in final_jobs)
    ):
        return
    if not component_adoption.exists() and quarantine is not None:
        initial_candidates, _rejected = _discover(
            quarantine, workspace=workspace,
        )
        if not any(item["kind"] == "h3_segment" for item in initial_candidates):
            return
    adoption, _identity = _ensure_private_directory(component_adoption)
    plans, _identity = _ensure_private_directory(adoption / "plans")
    receipts, _identity = _ensure_private_directory(adoption / "receipts")
    bindings, _identity = _ensure_private_directory(adoption / "bindings")
    _recover_pending(
        plans, receipts, root, quarantine,
        allowed_kinds={"h3_segment"},
    )
    # A receipt verifies the published clip bytes on every scan. Missing or
    # changed clips remain missing; retained evidence never silently replays.
    _receipt_jobs(
        receipts, plans, bindings, root,
        allowed_kinds={"h3_segment"},
    )
    if quarantine is None or not any(
        job.get("state") == "adopted" for job in final_jobs
    ):
        return
    candidates, _rejected = _discover(quarantine, workspace=workspace)
    valid_units = _valid_units(candidates)
    for job in final_jobs:
        group = _component_group_for_adopted_final(
            job,
            root=root,
            plans=final_plans,
            bindings=final_bindings,
            valid_units=valid_units,
            workspace=workspace,
        )
        if group is None:
            continue
        binding_path = bindings / _binding_name(workspace, group["job_id"])
        existing_binding = None
        if binding_path.exists() or binding_path.is_symlink():
            existing_binding, payload = _load_private_json(binding_path)
            _validate_binding(existing_binding, filename=binding_path.name)
            if binding_path.is_symlink() or payload != _canonical_json(existing_binding):
                raise QueueRecoveryRuntimeError("Component-adoption job binding changed.")
            if (receipts / f"{existing_binding['plan_sha256']}.json").exists():
                continue
        publication_mode = _destination_mode(group, root)
        plan = _plan_for(
            group, workspace=workspace, publication_mode=publication_mode,
        )
        _validate_plan(plan, allowed_kinds={"h3_segment"})
        plan_payload = _canonical_json(plan)
        plan_sha = hashlib.sha256(plan_payload).hexdigest()
        binding = _binding_for_plan(plan, plan_sha)
        if existing_binding is not None and existing_binding != binding:
            continue
        if existing_binding is None:
            _create_only(binding_path, _canonical_json(binding))
        _create_only(plans / f"{plan_sha}.json", plan_payload)
        if publication_mode == "preexisting_exact":
            _validate_preexisting_plan(plan, root, quarantine, fsync=True)
        else:
            try:
                for kind in ("sidecar", "media"):
                    for item in plan["items"]:
                        _publish_one(
                            quarantine / item[f"source_{kind}"],
                            root / item[f"dest_{kind}"],
                            size=item[f"{kind}_size"],
                            digest=item[f"{kind}_sha256"],
                        )
                        _fsync_directory(root)
                        _fsync_directory(quarantine)
            except BaseException:
                _rollback(plan, root, quarantine)
                raise
        _create_only(receipts / f"{plan_sha}.json", _canonical_json({
            "schema_version": _SCHEMA,
            "plan_sha256": plan_sha,
            "state": "completed",
        }))


def adopt_quarantined_final_groups(
    project_directory: os.PathLike[str] | str,
    *,
    workspace: str,
    _publish_hook: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """Adopt every complete attested final group without creating a job.

    The returned ``jobs`` field is private integration data.  Public callers
    must project only its content-free state and counts.
    """
    if _direct_name(workspace) is None:
        raise QueueRecoveryRuntimeError("Final-adoption workspace is invalid.")
    root = _safe_root(project_directory)
    recovery = root / MANIFEST_DIRECTORY
    quarantine_path = recovery / "quarantine"
    adoption_path = recovery / "final-adoption"
    quarantine_present = quarantine_path.exists() or quarantine_path.is_symlink()
    adoption_present = adoption_path.exists() or adoption_path.is_symlink()
    if not quarantine_present and not adoption_present:
        return {
            "schema_version": _SCHEMA,
            "declared_groups": 0,
            "adopted_groups": 0,
            "missing_groups": 0,
            "quarantined_groups": 0,
            "rejected_artifacts": 0,
            "jobs": [],
        }
    _existing_private_directory(recovery)
    quarantine = None
    quarantine_identity = None
    if quarantine_present:
        quarantine, quarantine_identity = _existing_private_directory(quarantine_path)
    if quarantine_present:
        adoption, _adoption_identity = _ensure_private_directory(adoption_path)
        plans, plans_identity = _ensure_private_directory(adoption / "plans")
        receipts, receipts_identity = _ensure_private_directory(adoption / "receipts")
        bindings, bindings_identity = _ensure_private_directory(adoption / "bindings")
    elif adoption_present:
        adoption, _adoption_identity = _existing_private_directory(adoption_path)
        plans, plans_identity = _existing_private_directory(adoption / "plans")
        receipts, receipts_identity = _existing_private_directory(adoption / "receipts")
        bindings, bindings_identity = _existing_private_directory(adoption / "bindings")
    with _serialized(adoption / "adoption.lock"):
        if quarantine is not None and quarantine_identity is not None:
            _verify_directory(quarantine, quarantine_identity)
        _verify_directory(plans, plans_identity)
        _verify_directory(receipts, receipts_identity)
        _verify_directory(bindings, bindings_identity)
        _recover_pending(plans, receipts, root, quarantine)
        candidates, rejected = (
            _discover(quarantine, workspace=workspace)
            if quarantine is not None else ([], 0)
        )
        complete, incomplete = _complete_groups(candidates)
        conflicting: list[dict] = []
        for group in complete:
            binding_path = bindings / _binding_name(workspace, group["job_id"])
            existing_binding = None
            if binding_path.exists() or binding_path.is_symlink():
                existing_binding, existing_payload = _load_private_json(binding_path)
                _validate_binding(existing_binding, filename=binding_path.name)
                if (
                    binding_path.is_symlink()
                    or existing_payload != _canonical_json(existing_binding)
                ):
                    raise QueueRecoveryRuntimeError(
                        "Final-adoption job binding changed."
                    )
                committed_receipt = receipts / (
                    f"{existing_binding['plan_sha256']}.json"
                )
                if committed_receipt.exists() or committed_receipt.is_symlink():
                    # A committed job is projected only from its immutable
                    # receipt. Retained quarantine copies cannot authorize a
                    # second plan or turn missing output into a collision.
                    continue
            publication_mode = _destination_mode(group, root)
            plan = _plan_for(
                group,
                workspace=workspace,
                publication_mode=publication_mode,
            )
            plan_payload = _canonical_json(plan)
            plan_sha = hashlib.sha256(plan_payload).hexdigest()
            plan_path = plans / f"{plan_sha}.json"
            receipt_path = receipts / f"{plan_sha}.json"
            binding = _binding_for_plan(plan, plan_sha)
            if existing_binding is not None:
                if existing_binding != binding:
                    conflicting.append(group)
                    continue
            else:
                _create_only(binding_path, _canonical_json(binding))
            _create_only(plan_path, plan_payload)
            if receipt_path.exists():
                continue
            if publication_mode == "preexisting_exact":
                _validate_preexisting_plan(
                    plan,
                    root,
                    quarantine,
                    fsync=True,
                )
                if callable(_publish_hook):
                    _publish_hook("preexisting_validated", 0)
                receipt = {
                    "schema_version": _SCHEMA,
                    "plan_sha256": plan_sha,
                    "state": "completed",
                }
                _create_only(receipt_path, _canonical_json(receipt))
                continue
            try:
                operation_index = 0
                for kind in ("sidecar", "media"):
                    for item in plan["items"]:
                        _publish_one(
                            quarantine / item[f"source_{kind}"],
                            root / item[f"dest_{kind}"],
                            size=item[f"{kind}_size"],
                            digest=item[f"{kind}_sha256"],
                            linked_hook=(
                                lambda kind=kind, operation_index=operation_index:
                                _publish_hook(f"{kind}_linked", operation_index)
                            ) if callable(_publish_hook) else None,
                        )
                        _fsync_directory(root)
                        _fsync_directory(quarantine)
                        if callable(_publish_hook):
                            _publish_hook(kind, operation_index)
                        operation_index += 1
                receipt = {
                    "schema_version": _SCHEMA,
                    "plan_sha256": plan_sha,
                    "state": "completed",
                }
                _create_only(receipt_path, _canonical_json(receipt))
            except BaseException:
                _rollback(plan, root, quarantine)
                raise
        jobs = _receipt_jobs(receipts, plans, bindings, root)
        _adopt_components_for_receipted_finals(
            root=root,
            quarantine=quarantine,
            recovery=recovery,
            workspace=workspace,
            final_jobs=jobs,
            final_plans=plans,
            final_bindings=bindings,
        )
        receipt_ids = {job["job_id"] for job in jobs}
        for group in incomplete + conflicting:
            if group["job_id"] in receipt_ids:
                continue
            total = int(group.get("output_total", 0) or 0)
            present = len(group.get("items") or [])
            jobs.append({
                "job_id": group["job_id"],
                "state": "quarantined",
                "declared": total,
                "adopted": 0,
                "missing": max(0, total - present),
                "quarantined": present,
                "output_files": [],
            })
        return {
            "schema_version": _SCHEMA,
            "declared_groups": len(jobs),
            "adopted_groups": sum(job["state"] == "adopted" for job in jobs),
            "missing_groups": sum(job["state"] == "missing" for job in jobs),
            "quarantined_groups": sum(job["state"] == "quarantined" for job in jobs),
            "rejected_artifacts": rejected,
            "jobs": sorted(jobs, key=lambda job: job["job_id"]),
        }


__all__ = [
    "adopt_quarantined_final_groups",
    "stage_quarantined_h3_segment_for_final_adoption",
    "validate_h3_segment_staging_name_budget",
]
