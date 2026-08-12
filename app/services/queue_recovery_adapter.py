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
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path, PurePath, PurePosixPath
import re
import stat
import threading
from typing import Any
import uuid

from services.queue_recovery import QueueRecoveryJournal, RecoverySnapshot
from services.h3_offload_plan import (
    H3OffloadPlanError,
    validate_h3_offload_plan,
)
from services.oom_detect import (
    normalize_failure_details,
    oom_info_from_failure_details,
)


_OWNER_PREFIX = "owner:v1:"
_PROJECT_PREFIX = "project:v1:"
_DIGEST_RE = re.compile(r"^(?:owner|project):v1:[0-9a-f]{64}$")
_MARKER_RE = re.compile(r"^[0-9a-f]{32}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_CREDIT_TRANSITION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]{7,127}\Z")
_CREDIT_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CREDIT_TRANSITION_HISTORY = 32

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
    "h3_offload_plan",
    "current_segment_model", "current_segment_reason",
    "current_segment_boundary", "h3_estimate", "window_index",
    "window_count", "segment_index", "segment_count", "repeat_index",
    "repeat_count", "output_files", "artifact_files", "clip_output_files",
    "join_output_file", "queue_reorder_reason",
    "plan_review_required", "plan_review_deadline",
    "plan_review_terms_required",
    "resource_intent", "resource_execution", "preemption_mode",
    "resource_state", "execution_attempt", "parent_job_id",
    "resource_retry_attempt", "resource_retry_limit",
    "resource_retry_phase", "resource_retry_reason",
    "logical_job_kind",
    "failure_details", "oom_info", "failed_child_job_id", "failed_child_status",
    "failed_child_reason",
    "queue_residency_bypass_count", "queue_residency_bypassed_waiters",
    "residency_base_key", "residency_affinity_key", "_queue_manual_order",
    "recovery_attempt", "recovery_state", "reruns_denoise",
    "recovery_unit", "recovery_cursor", "_recovery_reason_code",
    "credit_queue",
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
_RESOURCE_INTENTS = frozenset({"generation", "text"})
_RESOURCE_EXECUTIONS = frozenset({"standard", "cpu"})
_PREEMPTION_MODES = frozenset({"none", "discard_restart"})
_RESOURCE_STATES = frozenset({
    "queued", "admitted", "running", "preemption_requested",
    "resources_releasing", "restarting_on_accelerator", "blocked",
    "released",
})
_FAILED_CHILD_STATUSES = frozenset({"failed", "cancelled", "blocked"})
_LOGICAL_JOB_KINDS = frozenset({
    "reference_pack_parent", "reference_pack_child",
})
_MAX_EXECUTION_ATTEMPT = 1_000_000
_MAX_RESOURCE_RETRY_ATTEMPT = 8
_RESOURCE_RETRY_PHASES = frozenset({
    "model_load", "generation", "finalization",
})
_RESOURCE_RETRY_REASONS = frozenset({
    "host_memory_pressure", "generation_oom", "finalization_oom",
})
_CREDIT_QUEUE_DECISIONS = frozenset({
    "unmetered_realm",
    "hosted_baseline",
    "hosted_priority_credit",
    "capability_excluded",
})
_CREDIT_RESERVATION_STATES = frozenset({"reserved", "released", "consumed"})
_CREDIT_REVALIDATION_STATES = frozenset({"valid", "downgraded", "released"})
_CREDIT_ACCOUNTING_RESERVATION_RE = re.compile(
    r"reservation_[0-9a-f]{32,64}\Z",
)


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


def _safe_h3_boundary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    boundary_type = str(value.get("type") or "")
    if boundary_type in {"continuous", "precut", "cut", "transition"}:
        result["type"] = boundary_type
    source = str(value.get("source") or "")
    if source in {
        "model_grid", "explicit_continuity", "explicit_transition",
        "explicit_cut", "precut_lead_in", "user_override",
        "shared_h3_shot_plan",
    }:
        result["source"] = source
    continuity = str(value.get("continuity_mode") or "")
    if continuity in {"continuous", "extend_previous", "independent"}:
        result["continuity_mode"] = continuity
    at_seconds = value.get("at_seconds")
    if (
        type(at_seconds) in {int, float}
        and math.isfinite(at_seconds)
        and at_seconds >= 0
    ):
        result["at_seconds"] = float(at_seconds)
    return result or None


def _safe_h3_checkpoint_options(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {
        "model_type", "name", "conditioning_mode", "is_downloaded",
        "managed_download", "auto_download", "terms_required", "available",
        "unavailable_reason",
    }
    result = []
    for index, option in enumerate(value[:32]):
        if not isinstance(option, Mapping):
            continue
        clean = {
            key: _safe_json(child, path=f"job.h3_segment_plan.checkpoint_options[{index}].{key}")
            for key, child in option.items()
            if key in allowed
        }
        if isinstance(clean.get("model_type"), str):
            result.append(clean)
    return result


def _safe_h3_duration_plan(value: Any) -> dict[str, Any]:
    """Retain only the content-free, server-owned duration approval envelope."""
    expected = {
        "revision", "target_published_frames", "current_published_frames",
        "current_generated_frames", "fps", "snap_candidates", "segments",
        "redistribution_mode", "outcome", "reason",
        "residual_published_frames",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise QueueRecoveryAdapterError("job.h3_segment_plan.duration_plan is invalid.")

    def positive_integer(child: Any, path: str) -> int:
        if type(child) is not int or child <= 0:
            raise QueueRecoveryAdapterError(f"{path} must be a positive integer.")
        return child

    revision = value.get("revision")
    if not isinstance(revision, str) or re.fullmatch(r"h3dp1_[0-9a-f]{64}", revision) is None:
        raise QueueRecoveryAdapterError("duration plan revision is invalid.")
    fps = value.get("fps")
    if type(fps) not in {int, float} or not math.isfinite(fps) or fps <= 0:
        raise QueueRecoveryAdapterError("duration plan fps is invalid.")
    result = {
        "revision": revision,
        "target_published_frames": positive_integer(
            value.get("target_published_frames"), "duration target"
        ),
        "current_published_frames": positive_integer(
            value.get("current_published_frames"), "duration current"
        ),
        "current_generated_frames": positive_integer(
            value.get("current_generated_frames"), "duration generated"
        ),
        "fps": fps,
    }
    candidates = value.get("snap_candidates")
    if not isinstance(candidates, Mapping) or set(candidates) != {"nearest", "down"}:
        raise QueueRecoveryAdapterError("duration snap candidates are invalid.")
    safe_candidates = {}
    candidate_fields = {
        "requested_published_frames", "candidate_published_frames",
        "segment_count", "generated_frames", "segment_published_frames",
        "confidence", "applied", "reason",
    }
    for mode in ("nearest", "down"):
        candidate = candidates.get(mode)
        if not isinstance(candidate, Mapping) or set(candidate) != candidate_fields:
            raise QueueRecoveryAdapterError("duration snap candidate is invalid.")
        requested = positive_integer(
            candidate.get("requested_published_frames"),
            f"duration {mode} requested frames",
        )
        selected = candidate.get("candidate_published_frames")
        count = candidate.get("segment_count")
        if selected is not None:
            selected = positive_integer(selected, f"duration {mode} candidate frames")
        if count is not None:
            count = positive_integer(count, f"duration {mode} segment count")
        generated = candidate.get("generated_frames")
        published = candidate.get("segment_published_frames")
        if not isinstance(generated, list) or not isinstance(published, list):
            raise QueueRecoveryAdapterError("duration snap geometry is invalid.")
        generated = [
            positive_integer(child, f"duration {mode} generated frame")
            for child in generated
        ]
        published = [
            positive_integer(child, f"duration {mode} published frame")
            for child in published
        ]
        if len(generated) != len(published):
            raise QueueRecoveryAdapterError("duration snap geometry is invalid.")
        confidence = candidate.get("confidence")
        if confidence not in {"high", "low", "unavailable"}:
            raise QueueRecoveryAdapterError("duration snap confidence is invalid.")
        if not isinstance(candidate.get("applied"), bool):
            raise QueueRecoveryAdapterError("duration snap applied flag is invalid.")
        safe_candidates[mode] = {
            "requested_published_frames": requested,
            "candidate_published_frames": selected,
            "segment_count": count,
            "generated_frames": generated,
            "segment_published_frames": published,
            "confidence": confidence,
            "applied": candidate["applied"],
            "reason": _safe_json(
                candidate.get("reason"), path=f"duration.{mode}.reason"
            ),
        }
    result["snap_candidates"] = safe_candidates

    duration_segments = value.get("segments")
    segment_fields = {
        "index", "published_frames", "min_published_frames",
        "max_published_frames", "grid_step", "grid_offset",
        "authored_locked", "completed_locked", "lock_reason",
    }
    if not isinstance(duration_segments, list):
        raise QueueRecoveryAdapterError("duration plan segments are invalid.")
    safe_segments = []
    for segment in duration_segments:
        if not isinstance(segment, Mapping) or set(segment) != segment_fields:
            raise QueueRecoveryAdapterError("duration plan segment is invalid.")
        lock_reason = segment.get("lock_reason")
        if lock_reason not in {None, "authored", "completed", "authored, completed"}:
            raise QueueRecoveryAdapterError("duration lock reason is invalid.")
        if not isinstance(segment.get("authored_locked"), bool) or not isinstance(
            segment.get("completed_locked"), bool
        ):
            raise QueueRecoveryAdapterError("duration lock flag is invalid.")
        offset = segment.get("grid_offset")
        if type(offset) is not int or offset < 0:
            raise QueueRecoveryAdapterError("duration grid offset is invalid.")
        safe_segments.append({
            "index": positive_integer(segment.get("index"), "duration segment index"),
            "published_frames": positive_integer(
                segment.get("published_frames"), "duration segment frames"
            ),
            "min_published_frames": positive_integer(
                segment.get("min_published_frames"), "duration segment minimum"
            ),
            "max_published_frames": positive_integer(
                segment.get("max_published_frames"), "duration segment maximum"
            ),
            "grid_step": positive_integer(segment.get("grid_step"), "duration grid step"),
            "grid_offset": offset,
            "authored_locked": segment["authored_locked"],
            "completed_locked": segment["completed_locked"],
            "lock_reason": lock_reason,
        })
    result["segments"] = safe_segments
    redistribution = value.get("redistribution_mode")
    if redistribution not in {"none", "next", "future"}:
        raise QueueRecoveryAdapterError("duration redistribution is invalid.")
    outcome = value.get("outcome")
    if outcome not in {"exact", "acceptable", "insufficient_capacity"}:
        raise QueueRecoveryAdapterError("duration outcome is invalid.")
    residual = value.get("residual_published_frames")
    if type(residual) is not int:
        raise QueueRecoveryAdapterError("duration residual is invalid.")
    result.update({
        "redistribution_mode": redistribution,
        "outcome": outcome,
        "reason": _safe_json(value.get("reason"), path="duration.reason"),
        "residual_published_frames": residual,
    })
    return result


def _safe_h3_segment_plan(value: Any) -> dict[str, Any] | None:
    """Retain restart/UI structure without implicitly persisting prompt previews."""
    if not isinstance(value, Mapping):
        return None
    allowed_top = {
        "kind", "clip_count", "fps", "requested_frames", "planned_frames",
        "published_frames",
        "adaptive_conditioning", "checkpoint_switches",
        "effective_model_count", "effective_models", "segments",
        "checkpoint_options", "duration_plan",
    }
    allowed_segment = {
        "index", "frames", "duration_seconds",
        "generated_frames", "published_frames",
        "generated_duration_seconds", "published_duration_seconds",
        "model_type", "model_reason",
        "edge_anchor_locked", "switch_from_previous", "boundary_from_previous",
        "duration_min_published_frames", "duration_max_published_frames",
        "duration_grid_step", "duration_grid_offset", "authored_locked",
        "completed_locked", "lock_reason",
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
        if key == "checkpoint_options":
            result[key] = _safe_h3_checkpoint_options(child)
        elif key == "duration_plan":
            result[key] = _safe_h3_duration_plan(child)
        elif key in {"fps"}:
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
                elif key == "boundary_from_previous":
                    safe_segment[key] = _safe_h3_boundary(child)
                elif key in {
                    "generated_duration_seconds", "published_duration_seconds",
                }:
                    safe_segment[key] = positive_number(child, path=path)
                else:
                    safe_segment[key] = _safe_json(child, path=path)
            safe_segments.append(safe_segment)
        result["segments"] = safe_segments
    return result


def _safe_credit_queue(value: Any) -> dict[str, Any]:
    """Validate the exact content-free scheduler decision envelope."""
    if not isinstance(value, Mapping):
        raise QueueRecoveryAdapterError("job.credit_queue schema is invalid.")
    schema_version = value.get("schema_version")
    expected = {
        "schema_version",
        "realm",
        "enforcement_enabled",
        "metering_applied",
        "decision",
        "requested_units_positive",
        "queue_band",
        "reservation_state",
        "reservation_revision",
        "revalidation_state",
        "allowance_revision",
        "allowance_observed_at",
        "transition_id",
        "transition_history",
    }
    if schema_version == 2:
        expected.update({
            "accounting_reservation_id",
            "accounting_reservation_revision",
        })
    if set(value) != expected:
        raise QueueRecoveryAdapterError("job.credit_queue schema is invalid.")
    decision = value["decision"]
    requested_units_positive = value["requested_units_positive"]
    queue_band = value["queue_band"]
    reservation_state = value["reservation_state"]
    reservation_revision = value["reservation_revision"]
    revalidation_state = value["revalidation_state"]
    allowance_revision = value["allowance_revision"]
    allowance_observed_at = value["allowance_observed_at"]
    transition_id = value["transition_id"]
    transition_history = value["transition_history"]
    if (
        type(schema_version) is not int
        or schema_version not in {1, 2}
        or not isinstance(value["realm"], str)
        or value["realm"] not in {"local", "lan", "hosted"}
        or type(value["enforcement_enabled"]) is not bool
        or type(value["metering_applied"]) is not bool
        or not isinstance(decision, str)
        or decision not in _CREDIT_QUEUE_DECISIONS
        or type(requested_units_positive) is not bool
        or type(queue_band) is not int
        or queue_band not in {-1, 0, 1}
        or (
            reservation_state is not None
            and (
                not isinstance(reservation_state, str)
                or reservation_state not in _CREDIT_RESERVATION_STATES
            )
        )
        or (
            reservation_revision is not None
            and (
                not isinstance(reservation_revision, str)
                or _CREDIT_FINGERPRINT_RE.fullmatch(reservation_revision) is None
            )
        )
        or (reservation_state is None) != (reservation_revision is None)
        or (
            revalidation_state is not None
            and (
                not isinstance(revalidation_state, str)
                or revalidation_state not in _CREDIT_REVALIDATION_STATES
            )
        )
        or not isinstance(allowance_revision, str)
        or _CREDIT_FINGERPRINT_RE.fullmatch(allowance_revision) is None
        or _safe_credit_allowance_observed_at(allowance_observed_at) is None
        or not isinstance(transition_id, str)
        or _CREDIT_TRANSITION_RE.fullmatch(transition_id) is None
        or not isinstance(transition_history, list)
        or not 1 <= len(transition_history) <= _MAX_CREDIT_TRANSITION_HISTORY
    ):
        raise QueueRecoveryAdapterError("job.credit_queue is invalid.")
    if schema_version == 2:
        accounting_reservation_id = value["accounting_reservation_id"]
        accounting_reservation_revision = value[
            "accounting_reservation_revision"
        ]
        if (
            not isinstance(accounting_reservation_id, str)
            or _CREDIT_ACCOUNTING_RESERVATION_RE.fullmatch(
                accounting_reservation_id,
            ) is None
            or type(accounting_reservation_revision) is not int
            or accounting_reservation_revision < 1
            or reservation_state is None
        ):
            raise QueueRecoveryAdapterError(
                "job.credit_queue accounting linkage is invalid.",
            )

    seen_transition_ids: set[str] = set()
    for item in transition_history:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or _CREDIT_TRANSITION_RE.fullmatch(item[0]) is None
            or not isinstance(item[1], str)
            or _CREDIT_FINGERPRINT_RE.fullmatch(item[1]) is None
            or item[0] in seen_transition_ids
        ):
            raise QueueRecoveryAdapterError(
                "job.credit_queue transition history is invalid.",
            )
        seen_transition_ids.add(item[0])
    if transition_history[-1][0] != transition_id:
        raise QueueRecoveryAdapterError(
            "job.credit_queue transition history is inconsistent.",
        )

    if value["metering_applied"] is False:
        valid = (
            queue_band == 0
            and reservation_state is None
            and revalidation_state is None
            and (
                (
                    value["realm"] in {"local", "lan"}
                    and decision == "unmetered_realm"
                )
                or (
                    value["realm"] == "hosted"
                    and value["enforcement_enabled"] is False
                    and decision == "hosted_baseline"
                )
            )
        )
    elif (
        value["realm"] != "hosted"
        or value["enforcement_enabled"] is not True
        or value["metering_applied"] is not True
    ):
        valid = False
    elif decision == "capability_excluded":
        valid = (
            queue_band == 0
            and reservation_state is None
            and revalidation_state is None
        )
    elif decision == "hosted_baseline":
        valid = (
            queue_band == (-1 if requested_units_positive else 0)
            and reservation_state is None
            and revalidation_state is None
        )
    else:
        active = (
            requested_units_positive
            and reservation_state in {"reserved", "consumed"}
            and revalidation_state in {None, "valid"}
        )
        released = (
            reservation_state == "released"
            and revalidation_state in {None, "released"}
        )
        downgraded = (
            reservation_state in {"reserved", "consumed"}
            and revalidation_state == "downgraded"
        )
        valid = requested_units_positive and (active or released or downgraded)
        if valid:
            valid = queue_band == (1 if active else -1)
    if not valid:
        raise QueueRecoveryAdapterError(
            "job.credit_queue decision is inconsistent.",
        )
    fingerprint_payload = {
        key: value[key]
        for key in (
            "schema_version",
            "realm",
            "enforcement_enabled",
            "metering_applied",
            "decision",
            "requested_units_positive",
            "queue_band",
            "reservation_state",
            "reservation_revision",
            "revalidation_state",
            "allowance_revision",
            "allowance_observed_at",
        )
    }
    if schema_version == 2:
        fingerprint_payload.update({
            "accounting_reservation_id": value["accounting_reservation_id"],
            "accounting_reservation_revision": value[
                "accounting_reservation_revision"
            ],
        })
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii"),
    ).hexdigest()
    if transition_history[-1][1] != fingerprint:
        raise QueueRecoveryAdapterError(
            "job.credit_queue transition fingerprint is inconsistent.",
        )
    return dict(value)


def _safe_credit_allowance_observed_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)


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
        if key in {
            "failure_details", "oom_info", "failed_child_job_id",
            "failed_child_status", "failed_child_reason",
        } and value is None:
            continue
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
        elif key == "h3_offload_plan":
            try:
                result[key] = validate_h3_offload_plan(value)
            except H3OffloadPlanError as error:
                raise QueueRecoveryAdapterError(
                    "job.h3_offload_plan is invalid."
                ) from error
        elif key == "credit_queue":
            result[key] = _safe_credit_queue(value)
        elif key == "resource_intent":
            if value not in _RESOURCE_INTENTS:
                raise QueueRecoveryAdapterError(
                    "job.resource_intent is invalid."
                )
            result[key] = value
        elif key == "resource_execution":
            if value not in _RESOURCE_EXECUTIONS:
                raise QueueRecoveryAdapterError(
                    "job.resource_execution is invalid."
                )
            result[key] = value
        elif key == "preemption_mode":
            if value not in _PREEMPTION_MODES:
                raise QueueRecoveryAdapterError(
                    "job.preemption_mode is invalid."
                )
            result[key] = value
        elif key == "resource_state":
            if value not in _RESOURCE_STATES:
                raise QueueRecoveryAdapterError(
                    "job.resource_state is invalid."
                )
            result[key] = value
        elif key == "execution_attempt":
            if (
                type(value) is not int
                or not 1 <= value <= _MAX_EXECUTION_ATTEMPT
            ):
                raise QueueRecoveryAdapterError(
                    "job.execution_attempt is invalid."
                )
            result[key] = value
        elif key in {"resource_retry_attempt", "resource_retry_limit"}:
            minimum = 0 if key == "resource_retry_attempt" else 1
            if (
                type(value) is not int
                or not minimum <= value <= _MAX_RESOURCE_RETRY_ATTEMPT
            ):
                raise QueueRecoveryAdapterError(f"job.{key} is invalid.")
            result[key] = value
        elif key == "resource_retry_phase":
            if value not in _RESOURCE_RETRY_PHASES:
                raise QueueRecoveryAdapterError(
                    "job.resource_retry_phase is invalid."
                )
            result[key] = value
        elif key == "resource_retry_reason":
            if value not in _RESOURCE_RETRY_REASONS:
                raise QueueRecoveryAdapterError(
                    "job.resource_retry_reason is invalid."
                )
            result[key] = value
        elif key == "parent_job_id":
            if not _valid_job_id(value):
                raise QueueRecoveryAdapterError(
                    "job.parent_job_id is invalid."
                )
            result[key] = value
        elif key == "logical_job_kind":
            if value not in _LOGICAL_JOB_KINDS:
                raise QueueRecoveryAdapterError(
                    "job.logical_job_kind is invalid."
                )
            result[key] = value
        elif key == "failure_details":
            if not isinstance(value, Mapping):
                raise QueueRecoveryAdapterError(
                    "job.failure_details is invalid."
                )
            result[key] = normalize_failure_details(value)
        elif key == "oom_info":
            # Reconstructed below only after the canonical failure envelope is
            # available; raw OOM strings and unknown fields never cross.
            continue
        elif key == "failed_child_job_id":
            if not _valid_job_id(value) or value == job_id:
                raise QueueRecoveryAdapterError(
                    "job.failed_child_job_id is invalid."
                )
            result[key] = value
        elif key == "failed_child_status":
            if value not in _FAILED_CHILD_STATUSES:
                raise QueueRecoveryAdapterError(
                    "job.failed_child_status is invalid."
                )
            result[key] = value
        elif key == "failed_child_reason":
            if type(value) is not str or _SAFE_TOKEN_RE.fullmatch(value) is None:
                raise QueueRecoveryAdapterError(
                    "job.failed_child_reason is invalid."
                )
            result[key] = value
        elif key == "current_segment_boundary":
            result[key] = _safe_h3_boundary(value)
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
    child_fields = {
        "failed_child_job_id", "failed_child_status", "failed_child_reason",
    }
    present_child_fields = child_fields.intersection(result)
    resource_retry_fields = {
        "resource_retry_attempt", "resource_retry_limit",
        "resource_retry_phase", "resource_retry_reason",
    }
    present_resource_retry_fields = resource_retry_fields.intersection(result)
    if present_resource_retry_fields and (
        present_resource_retry_fields != resource_retry_fields
        or result["resource_retry_attempt"] < 1
        or result["resource_retry_attempt"] > result["resource_retry_limit"]
    ):
        raise QueueRecoveryAdapterError(
            "job resource retry state is incomplete."
        )
    if present_resource_retry_fields == resource_retry_fields:
        expected_reason = {
            "model_load": "host_memory_pressure",
            "generation": "generation_oom",
            "finalization": "finalization_oom",
        }[result["resource_retry_phase"]]
        if result["resource_retry_reason"] != expected_reason:
            raise QueueRecoveryAdapterError(
                "job resource retry phase and reason disagree."
            )
    logical_kind = result.get("logical_job_kind")
    if (
        logical_kind == "reference_pack_parent"
        and "parent_job_id" in result
    ) or (
        logical_kind == "reference_pack_child"
        and (
            "parent_job_id" not in result
            or result["parent_job_id"] == result["id"]
        )
    ):
        raise QueueRecoveryAdapterError(
            "job logical Reference relation is invalid."
        )
    if present_child_fields and (
        present_child_fields != child_fields or result.get("status") != "failed"
    ):
        raise QueueRecoveryAdapterError(
            "job failed-child relation is incomplete."
        )
    has_gpu_resource_retry = (
        present_resource_retry_fields == resource_retry_fields
        and result["resource_retry_reason"] in {
            "generation_oom", "finalization_oom",
        }
    )
    has_raw_oom = "oom_info" in job and job.get("oom_info") is not None
    if (
        has_raw_oom
        and present_resource_retry_fields == resource_retry_fields
        and not has_gpu_resource_retry
    ):
        raise QueueRecoveryAdapterError(
            "job.oom_info is invalid for this resource retry."
        )
    if has_raw_oom and (
        present_child_fields == child_fields or has_gpu_resource_retry
    ):
        raw_oom = job["oom_info"]
        details = result.get("failure_details")
        if (
            not isinstance(raw_oom, Mapping)
            or raw_oom.get("is_oom") is not True
            or not isinstance(details, Mapping)
            or details.get("is_oom") is not True
        ):
            raise QueueRecoveryAdapterError("job.oom_info is invalid.")
        coefficient = raw_oom.get("current_coefficient")
        if (
            type(coefficient) not in {int, float}
            or not math.isfinite(coefficient)
            or not 0.0 < coefficient <= 1.0
        ):
            raise QueueRecoveryAdapterError("job.oom_info is invalid.")
        oom_details = normalize_failure_details({
            **details,
            "allocator": raw_oom.get("allocator", details.get("allocator")),
            "is_oom": True,
        })
        safe_oom = oom_info_from_failure_details(
            oom_details, float(coefficient),
        )
        if safe_oom is None:
            raise QueueRecoveryAdapterError("job.oom_info is invalid.")
        result["oom_info"] = safe_oom
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
        if clean.get("status") not in _TERMINAL:
            # Process-local leases never survive restart. Pre-contract and
            # explicit CPU snapshots both reacquire from the standard queued
            # lane; the attempt counter remains the stale-result fence.
            clean.setdefault("resource_intent", "generation")
            clean["resource_execution"] = "standard"
            clean["preemption_mode"] = "none"
            clean["resource_state"] = "queued"
            clean.setdefault("execution_attempt", 1)
            credit_queue = clean.get("credit_queue")
            if (
                isinstance(credit_queue, Mapping)
                and credit_queue.get("schema_version") in {1, 2}
                and credit_queue.get("queue_band") == 1
            ):
                clean["_credit_revalidation_required"] = True
        elif "resource_intent" in clean:
            clean["resource_execution"] = "standard"
            clean["preemption_mode"] = "none"
            clean["resource_state"] = "released"
            clean.setdefault("execution_attempt", 1)
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
) -> tuple[bool, int, int, int, int, float, int]:
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
    credit_queue = snapshot.get("credit_queue")
    credit_band = (
        0
        if snapshot.get("_credit_revalidation_required") is True
        else int(credit_queue.get("queue_band", 0))
        if isinstance(credit_queue, Mapping)
        else 0
    )
    remote = bool(snapshot.get("source_remote", False))
    if manual:
        return (remote, 0, 0, -priority, -manual, created, sequence)
    return (remote, 1, -credit_band, -priority, 0, created, sequence)


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
        manifest_updates = dict(
            getattr(proposal, "request_manifests", None) or {}
        )
        serialized: dict[str, dict[str, Any]] = {}
        accepted_manifests: dict[str, dict[str, Any]] = {}
        with self._lock:
            job_ids = {str(job.get("id") or "") for job in jobs}
            if set(manifest_updates).difference(job_ids):
                raise QueueRecoveryAdapterError(
                    "Queue recovery manifest update has no matching job transition."
                )
            for job in jobs:
                job_id = str(job.get("id") or "")
                identity = self._identities.get(job_id)
                manifest = manifest_updates.get(
                    job_id, self._manifests.get(job_id),
                )
                if identity is None or manifest is None:
                    raise QueueRecoveryAdapterError(
                        "Queue recovery job must be registered before transition."
                    )
                clean_manifest = _safe_json(
                    dict(manifest), path="request_manifest",
                )
                serialized[job_id] = serialize_job(
                    job,
                    owner_digest=identity[0],
                    project_digest=identity[1],
                    request_manifest=clean_manifest,
                )
                accepted_manifests[job_id] = clean_manifest
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
            self._manifests.update(deepcopy(accepted_manifests))
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
