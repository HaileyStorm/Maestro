"""Closed, content-free diagnostics for generation preparation failures."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import PurePosixPath
import uuid
from typing import Final


PLANNING_FAILURE_REASON_CODES: Final[frozenset[str]] = frozenset({
    "planning_authority_rejected",
    "planning_contract_mismatch",
    "planning_dependency_missing",
    "planning_dependency_unavailable",
    "planning_io_failed",
    "planning_manifest_cleanup_failed",
    "planning_memory_unavailable",
    "planning_runtime_failed",
    "planning_timeout",
    "planning_unclassified",
    "planning_validation_rejected",
})

_HTTP_STATUS_REASONS: Final[dict[int, str]] = {
    400: "planning_validation_rejected",
    401: "planning_authority_rejected",
    403: "planning_authority_rejected",
    404: "planning_dependency_missing",
    408: "planning_timeout",
    409: "planning_validation_rejected",
    422: "planning_validation_rejected",
    423: "planning_dependency_unavailable",
    429: "planning_dependency_unavailable",
    451: "planning_authority_rejected",
    500: "planning_runtime_failed",
    502: "planning_dependency_unavailable",
    503: "planning_dependency_unavailable",
    504: "planning_timeout",
}

_SAFE_PHASES: Final[frozenset[str]] = frozenset({
    "enhancing_prompt",
    "planning_generation",
})

_PUBLIC_FAILURE_TYPES: Final[tuple[type[BaseException], ...]] = (
    ValueError,
)

_PUBLIC_FAILURE_PREFIXES: Final[tuple[str, ...]] = (
    "H3 Turbo",
    "MiniMax H3",
    "Spectrum Experimental",
    "Pinned Ref2VA",
    "Pinned FL2VA",
    "Native MiniMax",
    "Kijai W4A8",
    "Generation planning mode",
    "Generation parameters",
    "Generation custom settings",
    "Generation LoRA",
)


def classify_planning_failure(error: BaseException) -> str:
    """Classify without reading exception text, args, trace, or payload data."""

    try:
        try:
            status_code = getattr(error, "status_code", None)
        except BaseException:
            status_code = None
        if type(status_code) is int and status_code in _HTTP_STATUS_REASONS:
            return _HTTP_STATUS_REASONS[status_code]
        if isinstance(error, (AttributeError, KeyError)):
            return "planning_contract_mismatch"
        if isinstance(error, ImportError):
            return "planning_dependency_missing"
        if isinstance(error, TimeoutError):
            return "planning_timeout"
        if isinstance(error, MemoryError):
            return "planning_memory_unavailable"
        if isinstance(error, OSError):
            return "planning_io_failed"
        if isinstance(error, (TypeError, ValueError, OverflowError)):
            return "planning_validation_rejected"
        if isinstance(error, RuntimeError):
            return "planning_runtime_failed"
    except BaseException:
        pass
    return "planning_unclassified"


def public_planning_failure_message(
    error: BaseException,
    *,
    fallback: str,
) -> str:
    """Return a prompt-free user message for known planner contracts."""

    try:
        error_type = type(error)
        type_name = getattr(error_type, "__name__", "")
        if error_type not in _PUBLIC_FAILURE_TYPES and type_name not in {
            "H3TurboCompatibilityError",
            "SpectrumCompatibilityError",
            "H3Lightx2vCompatibilityError",
        }:
            return fallback
        text = error.args[0] if error.args else ""
        if type(text) is not str or not text or len(text) > 240 or "\n" in text:
            return fallback
        if not text.startswith(_PUBLIC_FAILURE_PREFIXES):
            return fallback
        return text
    except BaseException:
        return fallback


def planning_failure_event(error: BaseException, *, phase: str) -> tuple[str, str]:
    """Return one bounded internal reason and a safe content-free event line."""

    try:
        reason = classify_planning_failure(error)
        if reason not in PLANNING_FAILURE_REASON_CODES:
            reason = "planning_unclassified"
        safe_phase = (
            phase
            if type(phase) is str and phase in _SAFE_PHASES
            else "preparation"
        )
        return reason, f"phase={safe_phase} reason={reason}"
    except BaseException:
        return (
            "planning_unclassified",
            "phase=preparation reason=planning_unclassified",
        )


def remove_exact_request_manifest(
    project_directory,
    pointer,
    *,
    expected_job_id: str,
) -> bool:
    """Retire only the exact manifest bytes named by one complete pointer.

    The shared recovery remover intentionally accepts path-only cleanup. A
    preparation failure needs a stronger compare-and-remove boundary because
    the initial job path is replaceable. Move the current name aside first,
    revalidate the moved object, and delete it only when schema, hash, size,
    and embedded job identity still match. A raced replacement is restored.
    """

    from services import queue_recovery_runtime as runtime

    try:
        if (
            not isinstance(pointer, dict)
            or set(pointer) != {"path", "schema", "sha256", "size"}
            or not isinstance(expected_job_id, str)
            or not expected_job_id
        ):
            return False
        # Validate the complete pointer and embedded job identity before any
        # namespace mutation. No manifest content leaves this helper.
        runtime.load_request_manifest(
            project_directory,
            pointer,
            expected_job_id=expected_job_id,
        )
        if not runtime._manifest_dir_fd_supported():
            return False
        root = runtime._validated_project_root(project_directory)
        relative = str(pointer["path"])
        path = PurePosixPath(relative)
        if path.parent != PurePosixPath(runtime.MANIFEST_DIRECTORY):
            return False
        filename = path.name
        recovery_directory = root / runtime.MANIFEST_DIRECTORY
        directory_descriptor = runtime._open_private_directory(
            recovery_directory,
        )
    except BaseException:
        return False

    quarantine = f".planning-cleanup.{uuid.uuid4().hex}.tmp"
    moved = False

    def restore_moved() -> None:
        nonlocal moved
        if not moved:
            return
        try:
            os.link(
                quarantine,
                filename,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except BaseException:
            return
        try:
            os.unlink(quarantine, dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
            moved = False
        except BaseException:
            pass

    try:
        os.rename(
            filename,
            quarantine,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        moved = True
        raw = runtime._read_exact_file_at(
            directory_descriptor,
            quarantine,
            maximum_bytes=runtime.MAX_MANIFEST_BYTES,
        )
        if (
            len(raw) != pointer["size"]
            or hashlib.sha256(raw).hexdigest() != pointer["sha256"]
        ):
            restore_moved()
            return False
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, TypeError, ValueError):
            restore_moved()
            return False
        if (
            type(payload) is not dict
            or payload.get("schema") != pointer["schema"]
            or payload.get("job_id") != expected_job_id
            or runtime._canonical_json(payload) != raw
        ):
            restore_moved()
            return False
        os.unlink(quarantine, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
        moved = False
        return True
    except BaseException:
        restore_moved()
        return False
    finally:
        try:
            os.close(directory_descriptor)
        except BaseException:
            pass


__all__ = [
    "PLANNING_FAILURE_REASON_CODES",
    "classify_planning_failure",
    "planning_failure_event",
    "public_planning_failure_message",
    "remove_exact_request_manifest",
]
