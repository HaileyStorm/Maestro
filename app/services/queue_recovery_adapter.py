"""Lifecycle adapter for Maestro's strict queue-recovery journal.

The journal deliberately accepts only caller-authored JSON snapshots.  This
module is the positive allowlist between mutable runtime jobs and that durable
format, and owns the revision fences needed by lifecycle transition hooks.
It has no dependency on ``launch.py`` so restart behavior remains model-free
and testable.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import math
import os
from pathlib import Path, PurePath, PurePosixPath
import re
import stat
import threading
from typing import Any
import uuid

from services.queue_recovery import QueueRecoveryJournal, RecoverySnapshot


_OWNER_PREFIX = "owner:v1:"
_PROJECT_PREFIX = "project:v1:"
_DIGEST_RE = re.compile(r"^(?:owner|project):v1:[0-9a-f]{64}$")
_MARKER_RE = re.compile(r"^[0-9a-f]{32}$")

# Runtime dictionaries contain prompts, filesystem roots, request objects,
# tensors, callbacks, credentials, and engine internals.  Only these fields
# may cross the persistence boundary automatically.  A launch-owned request
# manifest is accepted separately by ``register_job``.
_JOB_FIELDS = frozenset({
    "id", "status", "kind", "tool", "workspace", "model_type",
    "generation_mode", "profile_id", "resolution", "private",
    "explicit", "explicit_output", "source_remote", "created_at",
    "started_at", "finished_at", "queue_priority", "queue_held",
    "hold_after_output", "pause_queue_after", "requested_outputs",
    "cancel_requested", "message", "phase", "progress", "step",
    "total_steps", "window_current", "window_total", "window_step",
    "window_total_steps", "window_progress", "overall_progress",
    "clip_current", "clip_total", "clip_progress", "h3_segment_plan",
    "current_segment_model", "current_segment_reason",
    "current_segment_boundary", "h3_estimate", "window_index",
    "window_count", "segment_index", "segment_count", "repeat_index",
    "repeat_count", "output_files", "artifact_files", "clip_output_files",
    "join_output_file", "queue_reorder_reason",
    "queue_residency_bypass_count", "queue_residency_bypassed_waiters",
    "residency_base_key", "residency_affinity_key", "_queue_manual_order",
    "recovery_attempt", "recovery_state", "reruns_denoise",
    "recovery_unit", "recovery_cursor",
})
_GLOBAL_FIELDS = frozenset({
    "paused", "pause_after_current", "manual_order_sequence", "queue_order",
})
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_FORBIDDEN_KEY_PARTS = frozenset({
    "authorization", "capability", "cookie", "credential", "credentials",
    "password", "passphrase", "passwd", "secret", "secrets", "session",
    "sessions", "token", "tokens",
})
_FORBIDDEN_KEYS = frozenset({"api_key", "apikey", "private_key"})
_PATH_REDACTABLE_JOB_FIELDS = frozenset({
    "message", "phase", "current_segment_reason", "recovery_state",
})
_MAX_MANUAL_ORDER_SEQUENCE = (1 << 63) - 1


class QueueRecoveryAdapterError(RuntimeError):
    """Raised when runtime state cannot safely cross the durable boundary."""


def _valid_job_id(value: Any) -> bool:
    return (
        type(value) is str
        and bool(value)
        and len(value) <= 256
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and not any(ord(character) < 32 for character in value)
    )


@dataclass(frozen=True)
class RestoredQueueState:
    """Allowlisted restart state returned to launch integration."""

    jobs: dict[str, dict[str, Any]]
    global_state: dict[str, Any]
    epoch: int


def _secret_bytes(secret: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(secret, (bytes, bytearray, memoryview)) or len(secret) < 16:
        raise ValueError("Queue recovery digest secret must contain at least 16 bytes.")
    return bytes(secret)


def owner_principal_digest(secret: bytes, principal: str) -> str:
    """Return an HMAC identity; raw session IDs never enter durable state."""
    if not isinstance(principal, str) or not principal:
        raise ValueError("Queue recovery owner principal is invalid.")
    digest = hmac.new(
        _secret_bytes(secret), b"maestro-owner\0" + principal.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return _OWNER_PREFIX + digest


def project_instance_digest(secret: bytes, marker: str) -> str:
    """Return an opaque project-instance identity from a stable marker."""
    if not isinstance(marker, str) or _MARKER_RE.fullmatch(marker) is None:
        raise ValueError("Queue recovery project marker is invalid.")
    digest = hmac.new(
        _secret_bytes(secret), b"maestro-project\0" + marker.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return _PROJECT_PREFIX + digest


def ensure_project_instance_marker(
    project_directory: os.PathLike[str] | str,
    *,
    marker_name: str = ".maestro-project-instance",
) -> str:
    """Atomically create/read the marker distinguishing project recreation."""
    if (
        not isinstance(marker_name, str)
        or not marker_name.startswith(".")
        or "/" in marker_name
        or "\\" in marker_name
    ):
        raise ValueError("Queue recovery project marker name is invalid.")
    requested_root = Path(os.path.abspath(os.fspath(project_directory)))

    def validate_ancestors() -> os.stat_result:
        current = Path(requested_root.anchor)
        candidates = [current]
        for part in requested_root.parts[1:]:
            current = current / part
            candidates.append(current)
        final: os.stat_result | None = None
        try:
            for index, candidate in enumerate(candidates):
                info = os.lstat(candidate)
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError
                if index < len(candidates) - 1 and not stat.S_ISDIR(info.st_mode):
                    raise ValueError
                final = info
        except (OSError, ValueError):
            raise ValueError("Queue recovery project directory is invalid.") from None
        if final is None or not stat.S_ISDIR(final.st_mode):
            raise ValueError("Queue recovery project directory is invalid.")
        return final

    requested_info = validate_ancestors()
    root_identity = (requested_info.st_dev, requested_info.st_ino)
    marker_path = requested_root / marker_name
    flags_read = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    use_dir_fd = os.name != "nt"
    root_descriptor: int | None = None
    try:
        if use_dir_fd:
            root_descriptor = os.open(
                requested_root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            opened_root = os.fstat(root_descriptor)
            if (
                not stat.S_ISDIR(opened_root.st_mode)
                or (opened_root.st_dev, opened_root.st_ino) != root_identity
            ):
                raise QueueRecoveryAdapterError("Project directory changed during access.")

        def marker_stat() -> os.stat_result:
            if root_descriptor is not None:
                return os.stat(
                    marker_name,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            return os.lstat(marker_path)

        def open_marker(flags: int, mode: int | None = None) -> int:
            target: str | os.PathLike[str] = (
                marker_name if root_descriptor is not None else marker_path
            )
            kwargs = ({"dir_fd": root_descriptor} if root_descriptor is not None else {})
            return (
                os.open(target, flags, mode, **kwargs)
                if mode is not None
                else os.open(target, flags, **kwargs)
            )

        def verify_root_path() -> None:
            final = validate_ancestors()
            if (final.st_dev, final.st_ino) != root_identity:
                raise QueueRecoveryAdapterError("Project directory changed during access.")

        def read_marker() -> str:
            descriptor = open_marker(flags_read)
            try:
                info = os.fstat(descriptor)
                before = marker_stat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_size > 64
                    or not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
                ):
                    raise QueueRecoveryAdapterError("Project instance marker is unsafe.")
                value = os.read(descriptor, 64).decode("ascii").strip()
                refreshed = os.fstat(descriptor)
                after = marker_stat()
                if (
                    not stat.S_ISREG(refreshed.st_mode)
                    or refreshed.st_nlink != 1
                    or refreshed.st_size > 64
                    or not stat.S_ISREG(after.st_mode)
                    or after.st_nlink != 1
                    or (refreshed.st_dev, refreshed.st_ino)
                    != (info.st_dev, info.st_ino)
                    or (after.st_dev, after.st_ino)
                    != (refreshed.st_dev, refreshed.st_ino)
                ):
                    raise QueueRecoveryAdapterError("Project instance marker changed during access.")
            finally:
                os.close(descriptor)
            verify_root_path()
            if _MARKER_RE.fullmatch(value) is None:
                raise QueueRecoveryAdapterError("Project instance marker is invalid.")
            return value

        try:
            return read_marker()
        except FileNotFoundError:
            pass
        marker = uuid.uuid4().hex
        flags_write = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = open_marker(flags_write, 0o600)
        except FileExistsError:
            return read_marker()
        created_identity: tuple[int, int] | None = None
        committed = False
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise QueueRecoveryAdapterError("Project instance marker is unsafe.")
            created_identity = (info.st_dev, info.st_ino)
            payload = (marker + "\n").encode("ascii")
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError("short marker write")
            os.fsync(descriptor)
            current = marker_stat()
            if (current.st_dev, current.st_ino) != created_identity:
                raise QueueRecoveryAdapterError("Project instance marker changed during creation.")
            verify_root_path()
            if root_descriptor is not None:
                os.fsync(root_descriptor)
            committed = True
        finally:
            os.close(descriptor)
            if not committed and created_identity is not None:
                try:
                    current = marker_stat()
                    if (current.st_dev, current.st_ino) == created_identity:
                        if root_descriptor is not None:
                            os.unlink(marker_name, dir_fd=root_descriptor)
                        else:
                            os.unlink(marker_path)
                except OSError:
                    pass
        # Re-open through the anchored directory and revalidate the path after
        # creation; returning the generated value without this check admits a
        # rename/symlink race at the publication boundary.
        return read_marker()
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)


def _is_absolute_path(value: str) -> bool:
    if not value:
        return False
    # PurePath only understands the host syntax; additionally reject Windows
    # drive/UNC forms so journals are safe when moved across platforms.
    return (
        PurePath(value).is_absolute()
        or re.match(r"^[A-Za-z]:[\\/]", value) is not None
        or value.startswith("\\\\")
        or re.search(r'''(?:^|[\s("'=])/[A-Za-z0-9._~-]''', value) is not None
        or re.search(r'''(?:^|[\s("'=])(?:[A-Za-z]:[\\/]|\\\\)''', value) is not None
    )


def _safe_json(value: Any, *, path: str = "value") -> Any:
    """Copy bounded JSON while rejecting raw absolute path strings."""
    if value is None or type(value) in {bool, int, float}:
        return value
    if type(value) is str:
        if _is_absolute_path(value):
            raise QueueRecoveryAdapterError(f"{path} contains an absolute path.")
        return value
    if type(value) is list:
        return [_safe_json(item, path=f"{path}[]") for item in value]
    if type(value) is dict:
        result: dict[str, Any] = {}
        for key, child in value.items():
            if type(key) is not str or not key:
                raise QueueRecoveryAdapterError(f"{path} contains an invalid key.")
            normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
            normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
            normalized = re.sub(
                r"[^a-z0-9]+", "_", normalized.casefold(),
            ).strip("_")
            if (
                normalized in _FORBIDDEN_KEYS
                or normalized.endswith("_api_key")
                or normalized.endswith("_private_key")
                or set(normalized.split("_")).intersection(_FORBIDDEN_KEY_PARTS)
            ):
                raise QueueRecoveryAdapterError(f"{path} contains a credential field.")
            result[key] = _safe_json(child, path=f"{path}.{key}")
        return result
    raise QueueRecoveryAdapterError(f"{path} is not plain JSON.")


def _safe_filename(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    if _is_absolute_path(normalized):
        return None
    if normalized in {".", ".."} or ".." in PurePosixPath(normalized).parts:
        return None
    return normalized


def _redact_runtime_paths(value: Any, *, path: str) -> Any:
    """Keep lifecycle transitions durable while suppressing path-bearing copy."""
    if type(value) is str:
        return "[path redacted]" if _is_absolute_path(value) else value
    if type(value) is list:
        return [
            _redact_runtime_paths(item, path=f"{path}[]") for item in value
        ]
    if type(value) is dict:
        result: dict[str, Any] = {}
        for key, child in value.items():
            # Reuse the credential/key validation without visiting the path-
            # bearing child before it can be redacted.
            _safe_json({key: None}, path=path)
            result[key] = _redact_runtime_paths(child, path=f"{path}.{key}")
        return result
    return _safe_json(value, path=path)


def _safe_h3_segment_plan(value: Any) -> dict[str, Any] | None:
    """Retain restart/UI structure without implicitly persisting prompt previews."""
    if not isinstance(value, Mapping):
        return None
    allowed_top = {
        "kind", "clip_count", "fps", "requested_frames", "planned_frames",
        "published_frames",
        "adaptive_conditioning", "checkpoint_switches",
        "effective_model_count", "effective_models", "segments",
    }
    allowed_segment = {
        "index", "frames", "duration_seconds",
        "generated_frames", "published_frames",
        "generated_duration_seconds", "published_duration_seconds",
        "model_type", "model_reason",
        "edge_anchor_locked", "switch_from_previous", "boundary_from_previous",
    }

    def positive_number(child: Any, *, path: str) -> int | float:
        if type(child) not in {int, float} or not math.isfinite(child) or child <= 0:
            raise QueueRecoveryAdapterError(f"{path} must be a positive finite number.")
        return child

    def positive_integer(child: Any, *, path: str) -> int:
        if type(child) is not int or child <= 0:
            raise QueueRecoveryAdapterError(f"{path} must be a positive integer.")
        return child

    result = {}
    for key, child in value.items():
        if key not in allowed_top or key == "segments":
            continue
        path = f"job.h3_segment_plan.{key}"
        if key in {"fps"}:
            result[key] = positive_number(child, path=path)
        elif key in {"published_frames"}:
            result[key] = positive_integer(child, path=path)
        else:
            result[key] = _safe_json(child, path=path)
    segments = value.get("segments")
    if isinstance(segments, list):
        safe_segments = []
        for segment_index, segment in enumerate(segments):
            if not isinstance(segment, Mapping):
                continue
            safe_segment = {}
            for key, child in segment.items():
                if key not in allowed_segment:
                    continue
                path = f"job.h3_segment_plan.segments[{segment_index}].{key}"
                if key in {"generated_frames", "published_frames"}:
                    safe_segment[key] = positive_integer(child, path=path)
                elif key in {
                    "generated_duration_seconds", "published_duration_seconds",
                }:
                    safe_segment[key] = positive_number(child, path=path)
                else:
                    safe_segment[key] = _safe_json(child, path=path)
            safe_segments.append(safe_segment)
        result["segments"] = safe_segments
    return result


def serialize_job(
    job: Mapping[str, Any],
    *,
    owner_digest: str,
    project_digest: str,
    request_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Serialize one job using the strict runtime-field allowlist."""
    if not isinstance(job, Mapping):
        raise QueueRecoveryAdapterError("Queue recovery job is invalid.")
    job_id = job.get("id")
    if not _valid_job_id(job_id):
        raise QueueRecoveryAdapterError("Queue recovery job ID is invalid.")
    if (
        not isinstance(owner_digest, str)
        or not owner_digest.startswith(_OWNER_PREFIX)
        or _DIGEST_RE.fullmatch(owner_digest) is None
    ):
        raise QueueRecoveryAdapterError("Queue recovery owner digest is invalid.")
    if (
        not isinstance(project_digest, str)
        or not project_digest.startswith(_PROJECT_PREFIX)
        or _DIGEST_RE.fullmatch(project_digest) is None
    ):
        raise QueueRecoveryAdapterError("Queue recovery project digest is invalid.")
    result: dict[str, Any] = {
        "id": job_id,
        "owner_principal": owner_digest,
        "project_instance": project_digest,
        "request_manifest": _safe_json(dict(request_manifest), path="request_manifest"),
    }
    for key in _JOB_FIELDS:
        if key not in job or key == "id":
            continue
        value = job[key]
        if key in {"output_files", "artifact_files"}:
            result[key] = [
                safe for safe in (_safe_filename(item) for item in (value or []))
                if safe is not None
            ]
        elif key == "clip_output_files":
            result[key] = {
                str(index): safe
                for index, item in dict(value or {}).items()
                if (safe := _safe_filename(item)) is not None
            }
        elif key == "join_output_file":
            safe = _safe_filename(value)
            if safe is not None:
                result[key] = safe
        elif key == "workspace":
            if (
                not isinstance(value, str)
                or not value
                or value in {".", ".."}
                or len(value) > 128
                or "/" in value
                or "\\" in value
                or any(ord(character) < 32 for character in value)
            ):
                raise QueueRecoveryAdapterError("job.workspace is invalid.")
            result[key] = value
        elif key == "h3_segment_plan":
            clean_plan = _safe_h3_segment_plan(value)
            if clean_plan is not None:
                result[key] = clean_plan
        elif key in _PATH_REDACTABLE_JOB_FIELDS:
            try:
                result[key] = _safe_json(value, path=f"job.{key}")
            except QueueRecoveryAdapterError as error:
                if "absolute path" not in str(error):
                    raise
                # Do not make a terminal/cancellation transition fail merely
                # because an engine error mentions its local working path.
                result[key] = _redact_runtime_paths(value, path=f"job.{key}")
        else:
            result[key] = _safe_json(value, path=f"job.{key}")
    return result


def serialize_global_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize prompt/path/credential-free queue controls only."""
    if not isinstance(state, Mapping):
        raise QueueRecoveryAdapterError("Queue recovery global state is invalid.")
    unknown = set(state).difference(_GLOBAL_FIELDS)
    if unknown:
        raise QueueRecoveryAdapterError("Queue recovery global state has unknown fields.")
    result = {key: _safe_json(value, path=f"global.{key}") for key, value in state.items()}
    result.setdefault("paused", False)
    result.setdefault("pause_after_current", False)
    result.setdefault("manual_order_sequence", 0)
    result.setdefault("queue_order", [])
    if type(result["paused"]) is not bool:
        raise QueueRecoveryAdapterError("Queue recovery paused state is invalid.")
    if type(result["pause_after_current"]) is not bool:
        raise QueueRecoveryAdapterError(
            "Queue recovery pause-after-current state is invalid."
        )
    if (
        type(result["manual_order_sequence"]) is not int
        or result["manual_order_sequence"] < 0
        or result["manual_order_sequence"] > _MAX_MANUAL_ORDER_SEQUENCE
    ):
        raise QueueRecoveryAdapterError(
            "Queue recovery manual order sequence is invalid."
        )
    order = result["queue_order"]
    if (
        type(order) is not list
        or not all(_valid_job_id(job_id) for job_id in order)
        or len(set(order)) != len(order)
    ):
        raise QueueRecoveryAdapterError("Queue recovery queue order is invalid.")
    return result


def _validated_recovered_state(
    recovered: RecoverySnapshot,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Re-apply the adapter trust boundary to generic journal records."""
    jobs: dict[str, dict[str, Any]] = {}
    for job_id, raw in recovered.jobs.items():
        if not isinstance(raw, Mapping):
            raise QueueRecoveryAdapterError("Recovered queue job is invalid.")
        owner_digest = raw.get("owner_principal")
        project_digest = raw.get("project_instance")
        manifest = raw.get("request_manifest")
        if not isinstance(manifest, Mapping):
            raise QueueRecoveryAdapterError("Recovered queue manifest is invalid.")
        clean = serialize_job(
            raw,
            owner_digest=owner_digest,
            project_digest=project_digest,
            request_manifest=manifest,
        )
        if clean.get("id") != job_id:
            raise QueueRecoveryAdapterError("Recovered queue identity is invalid.")
        jobs[job_id] = clean
    global_state = serialize_global_state(recovered.global_state or {})
    queued = {
        job_id: snapshot
        for job_id, snapshot in jobs.items()
        if snapshot.get("status") == "queued"
        and not snapshot.get("cancel_requested", False)
    }
    preferred: list[str] = []
    for job_id in global_state.get("queue_order", []):
        if job_id in queued and job_id not in preferred:
            preferred.append(job_id)
    preferred.extend(job_id for job_id in queued if job_id not in preferred)
    sequence = {job_id: index for index, job_id in enumerate(preferred)}
    global_state["queue_order"] = sorted(
        queued,
        key=lambda job_id: _durable_order_key(
            queued[job_id], sequence.get(job_id, len(sequence)),
        ),
    )
    return jobs, global_state


def _durable_order_key(
    snapshot: Mapping[str, Any], sequence: int,
) -> tuple[bool, int, int, float, int]:
    try:
        priority = int(snapshot.get("queue_priority", 0) or 0)
    except (TypeError, ValueError):
        priority = 0
    try:
        manual = max(0, int(snapshot.get("_queue_manual_order", 0) or 0))
    except (TypeError, ValueError):
        manual = 0
    try:
        created = float(snapshot.get("created_at", 0) or 0)
    except (TypeError, ValueError):
        created = 0.0
    return (
        bool(snapshot.get("source_remote", False)),
        -priority,
        -manual,
        created,
        sequence,
    )


class QueueRecoveryCoordinator:
    """Revision-aware bridge bound to lifecycle prospective transitions."""

    def __init__(self, journal: QueueRecoveryJournal) -> None:
        self.journal = journal
        self._lock = threading.RLock()
        recovered = journal.recover()
        clean_jobs, clean_global = _validated_recovered_state(recovered)
        self._epoch = recovered.epoch
        self._job_revisions = dict(recovered.job_revisions)
        self._global_revision = recovered.global_revision
        self._snapshots = clean_jobs
        self._global_state = clean_global
        self._identities: dict[str, tuple[str, str]] = {
            job_id: (
                str(snapshot.get("owner_principal", "")),
                str(snapshot.get("project_instance", "")),
            )
            for job_id, snapshot in clean_jobs.items()
        }
        self._manifests: dict[str, dict[str, Any]] = {
            job_id: deepcopy(snapshot.get("request_manifest", {}))
            for job_id, snapshot in clean_jobs.items()
        }

    def _canonical_global_state(
        self,
        incoming: Mapping[str, Any],
        *,
        job_updates: Mapping[str, Mapping[str, Any]] | None = None,
        tombstones: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Merge queue controls without dropping not-yet-waiting registrations."""
        clean = serialize_global_state(incoming)
        prospective = dict(self._snapshots)
        prospective.update({
            key: dict(value) for key, value in (job_updates or {}).items()
        })
        for job_id in tombstones:
            prospective.pop(job_id, None)
        queued = {
            job_id: snapshot
            for job_id, snapshot in prospective.items()
            if snapshot.get("status") == "queued"
            and not snapshot.get("cancel_requested", False)
        }
        current_order = [
            job_id for job_id in self._global_state.get("queue_order", [])
            if job_id in queued
        ]
        current_order.extend(
            job_id
            for job_id, snapshot in self._snapshots.items()
            if job_id in queued
            and snapshot.get("status") == "queued"
            and not snapshot.get("cancel_requested", False)
            and job_id not in current_order
        )
        incoming_order = [
            job_id for job_id in clean.get("queue_order", [])
            if job_id in queued
        ]
        prior_queued = {
            job_id for job_id, snapshot in self._snapshots.items()
            if snapshot.get("status") == "queued"
            and not snapshot.get("cancel_requested", False)
            and job_id not in tombstones
        }
        if prior_queued.issubset(set(incoming_order)):
            preferred = list(incoming_order)
        else:
            preferred = list(current_order)
            preferred.extend(
                job_id for job_id in incoming_order if job_id not in preferred
            )
        preferred.extend(job_id for job_id in queued if job_id not in preferred)
        sequence = {job_id: index for index, job_id in enumerate(preferred)}
        clean["queue_order"] = sorted(
            queued,
            key=lambda job_id: _durable_order_key(
                queued[job_id], sequence.get(job_id, len(sequence)),
            ),
        )
        return serialize_global_state(clean)

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def register_job(
        self,
        job: Mapping[str, Any],
        *,
        owner_digest: str,
        project_digest: str,
        request_manifest: Mapping[str, Any],
        global_state: Mapping[str, Any] | None = None,
    ) -> None:
        """Durably register a new/recovered runtime job before scheduling."""
        job_id = str(job.get("id") or "")
        snapshot = serialize_job(
            job,
            owner_digest=owner_digest,
            project_digest=project_digest,
            request_manifest=request_manifest,
        )
        with self._lock:
            if job_id in self._identities:
                raise QueueRecoveryAdapterError("Queue recovery job is already registered.")
            clean_global = (
                None
                if global_state is None
                else self._canonical_global_state(
                    global_state, job_updates={job_id: snapshot},
                )
            )
            receipt = self.journal.commit_state(
                jobs={job_id: snapshot},
                global_state=clean_global,
                expected_job_revisions={job_id: self._job_revisions.get(job_id, 0)},
                expected_global_revision=(self._global_revision if clean_global is not None else None),
                expected_epoch=self._epoch,
            )
            self._identities[job_id] = (owner_digest, project_digest)
            self._manifests[job_id] = deepcopy(snapshot["request_manifest"])
            self._snapshots[job_id] = deepcopy(snapshot)
            if clean_global is not None:
                self._global_state = deepcopy(clean_global)
            self._accept_receipt(receipt)

    def prospective_transition(self, proposal: Any) -> None:
        """Persist a lifecycle ``DurableTransition`` before memory mutation."""
        jobs = tuple(getattr(proposal, "jobs", ()) or ())
        tombstones = tuple(getattr(proposal, "tombstones", ()) or ())
        global_state = getattr(proposal, "global_state", None)
        serialized: dict[str, dict[str, Any]] = {}
        with self._lock:
            for job in jobs:
                job_id = str(job.get("id") or "")
                identity = self._identities.get(job_id)
                manifest = self._manifests.get(job_id)
                if identity is None or manifest is None:
                    raise QueueRecoveryAdapterError(
                        "Queue recovery job must be registered before transition."
                    )
                serialized[job_id] = serialize_job(
                    job,
                    owner_digest=identity[0],
                    project_digest=identity[1],
                    request_manifest=manifest,
                )
            clean_global = (
                None
                if global_state is None
                else self._canonical_global_state(
                    global_state,
                    job_updates=serialized,
                    tombstones=tombstones,
                )
            )
            changed_ids = set(serialized).union(tombstones)
            receipt = self.journal.commit_state(
                jobs=serialized,
                tombstones=tombstones,
                global_state=clean_global,
                expected_job_revisions={
                    job_id: self._job_revisions.get(job_id, 0)
                    for job_id in changed_ids
                },
                expected_global_revision=(self._global_revision if clean_global is not None else None),
                expected_epoch=self._epoch,
            )
            self._accept_receipt(receipt)
            self._snapshots.update(deepcopy(serialized))
            if clean_global is not None:
                self._global_state = deepcopy(clean_global)
            for job_id in tombstones:
                self._identities.pop(job_id, None)
                self._manifests.pop(job_id, None)
                self._snapshots.pop(job_id, None)

    def _accept_receipt(self, receipt: Any) -> None:
        self._epoch = receipt.epoch
        self._job_revisions.update(receipt.job_revisions)
        self._global_revision = receipt.global_revision

    def restore(self) -> RestoredQueueState:
        """Re-read and return safe state without holding the coordinator lock."""
        with self._lock:
            recovered = self.journal.recover()
            clean_jobs, clean_global = _validated_recovered_state(recovered)
            self._epoch = recovered.epoch
            self._job_revisions = dict(recovered.job_revisions)
            self._global_revision = recovered.global_revision
            self._snapshots = clean_jobs
            self._global_state = clean_global
            self._identities = {
                job_id: (
                    str(snapshot.get("owner_principal", "")),
                    str(snapshot.get("project_instance", "")),
                )
                for job_id, snapshot in clean_jobs.items()
            }
            self._manifests = {
                job_id: deepcopy(snapshot.get("request_manifest", {}))
                for job_id, snapshot in clean_jobs.items()
            }
            jobs = deepcopy(clean_jobs)
            global_state = deepcopy(clean_global)
            epoch = recovered.epoch
        return RestoredQueueState(jobs=jobs, global_state=global_state, epoch=epoch)

    def tombstone_terminal(self, job_id: str) -> None:
        """Remove one terminal snapshot while retaining its revision fence."""
        with self._lock:
            recovered = self.journal.recover()
            clean_jobs, _clean_global = _validated_recovered_state(recovered)
            snapshot = clean_jobs.get(job_id)
            if snapshot is None:
                return
            if str(snapshot.get("status", "")).casefold() not in _TERMINAL:
                raise QueueRecoveryAdapterError("Only terminal jobs may be tombstoned.")
            clean_global = self._canonical_global_state(
                self._global_state, tombstones=(job_id,),
            )
            receipt = self.journal.commit_state(
                tombstones=(job_id,),
                global_state=clean_global,
                expected_job_revisions={
                    job_id: self._job_revisions.get(job_id, 0),
                },
                expected_global_revision=self._global_revision,
                expected_epoch=self._epoch,
            )
            self._accept_receipt(receipt)
            self._identities.pop(job_id, None)
            self._manifests.pop(job_id, None)
            self._snapshots.pop(job_id, None)
            self._global_state = clean_global

    def compact(self) -> RecoverySnapshot:
        """Sanitize active state, then compact and advance the writer epoch."""
        with self._lock:
            before = self.journal.recover()
            clean_before_jobs, clean_before_global = _validated_recovered_state(before)
            # Supply sanitized state directly to the atomic replacement. This
            # works even when no append event/byte capacity remains.
            compacted = self.journal.compact(
                drop_terminal=True,
                replacement_jobs=clean_before_jobs,
                replacement_global_state=(
                    clean_before_global if before.global_state is not None else None
                ),
                expected_job_revisions=dict(before.job_revisions),
                expected_global_revision=before.global_revision,
                expected_epoch=before.epoch,
            )
            clean_jobs, clean_global = _validated_recovered_state(compacted)
            self._epoch = compacted.epoch
            self._job_revisions = dict(compacted.job_revisions)
            self._global_revision = compacted.global_revision
            self._snapshots = clean_jobs
            self._global_state = clean_global
            live = set(clean_jobs)
            self._identities = {key: value for key, value in self._identities.items() if key in live}
            self._manifests = {key: value for key, value in self._manifests.items() if key in live}
            return RecoverySnapshot(
                jobs=deepcopy(clean_jobs),
                job_revisions=dict(compacted.job_revisions),
                epoch=compacted.epoch,
                global_state=deepcopy(clean_global),
                global_revision=compacted.global_revision,
                last_sequence=compacted.last_sequence,
                event_count=compacted.event_count,
                discarded_torn_tail=compacted.discarded_torn_tail,
            )
