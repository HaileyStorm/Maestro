"""Thread-safe state transitions for Maestro's in-process generation jobs.

Jobs are mutable dictionaries shared by API handlers and background workers.
This module keeps terminal-state changes and abort-state registration atomic so
that cancellation cannot be lost to a late ``completed``/``failed`` write.
It deliberately has no dependency on ``launch.py`` or model code, which keeps
the race behavior testable without loading the generation engine.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections.abc import (
    Callable,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
)
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_lifecycle_lock = threading.RLock()
_registrations: dict[
    str,
    tuple[
        MutableMapping[str, Any],
        MutableMapping[str, Any],
        Callable[[], None] | None,
    ],
] = {}
_queue_condition = threading.Condition(threading.RLock())
_queue_waiters: dict[int, tuple[int, MutableMapping[str, Any]]] = {}
_queue_sequence = 0
_queue_manual_order_sequence = 0
_queue_paused = False
_pause_after_current = False
_resident_base_key: str | None = None
_resident_affinity_key: str | None = None
_MAX_JOB_EVENTS = 250
MAX_RESIDENCY_BYPASSES = 2
RESIDENCY_AGE_CEILING_SECONDS = 120.0
MAX_RECORDED_RESIDENCY_BYPASSED_WAITERS = 10_000
_OPAQUE_RESIDENCY_KEY_PREFIX = "r1:"
RESOURCE_INTENT_GENERATION = "generation"
RESOURCE_INTENT_TEXT = "text"
_RESOURCE_INTENTS = frozenset({
    RESOURCE_INTENT_GENERATION,
    RESOURCE_INTENT_TEXT,
})
RESOURCE_EXECUTION_STANDARD = "standard"
RESOURCE_EXECUTION_CPU = "cpu"
_RESOURCE_EXECUTIONS = frozenset({
    RESOURCE_EXECUTION_STANDARD,
    RESOURCE_EXECUTION_CPU,
})
PREEMPTION_MODE_NONE = "none"
PREEMPTION_MODE_DISCARD_RESTART = "discard_restart"
_PREEMPTION_MODES = frozenset({
    PREEMPTION_MODE_NONE,
    PREEMPTION_MODE_DISCARD_RESTART,
})
RESOURCE_STATES = frozenset({
    "queued",
    "admitted",
    "running",
    "preemption_requested",
    "resources_releasing",
    "restarting_on_accelerator",
    "blocked",
    "released",
})
MAX_EXECUTION_ATTEMPT = 1_000_000
_durability_hook: Callable[["DurableTransition"], None] | None = None

# Any transition that changes both scheduler-visible queue membership and job
# lifecycle state acquires these locks in this order: ``_queue_condition``
# first, then ``_lifecycle_lock``.  Queue selection already owns the condition
# while inspecting lifecycle state, so the reverse order can deadlock and can
# also publish a queued/running state that disagrees with waiter membership.


@dataclass(frozen=True)
class DurableTransition:
    """Prospective durable state published before its in-memory mutation."""

    name: str
    jobs: tuple[Mapping[str, Any], ...] = ()
    global_state: Mapping[str, Any] | None = None
    tombstones: tuple[str, ...] = ()
    request_manifests: Mapping[str, Mapping[str, Any]] | None = None


def configure_durability_hook(
    hook: Callable[[DurableTransition], None] | None,
) -> None:
    """Bind one process-wide prospective persistence hook.

    ``None`` restores the historical no-op behavior used by isolated tests.
    Launch must bind before accepting jobs and must not replace a live hook.
    """
    global _durability_hook
    if hook is not None and not callable(hook):
        raise TypeError("durability hook must be callable or None")
    with _queue_condition, _lifecycle_lock:
        if (
            _durability_hook is not None
            and hook is not None
            and hook != _durability_hook
        ):
            raise RuntimeError("durability hook is already configured")
        _durability_hook = hook


def _copy_job_for_transition(job: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return deepcopy(dict(job))
    except Exception:
        # Runtime-only values can be deliberately non-copyable. Copy ordinary
        # containers where lifecycle transitions mutate them and leave opaque
        # values by identity; the adapter's positive allowlist never persists
        # those values.
        result = dict(job)
        for key, value in tuple(result.items()):
            if isinstance(value, (dict, list, tuple)):
                try:
                    result[key] = deepcopy(value)
                except Exception:
                    pass
        return result


def _global_state_unlocked(
    *,
    paused: bool | None = None,
    pause_after_current: bool | None = None,
    manual_order_sequence: int | None = None,
    replacements: Mapping[int, Mapping[str, Any]] | None = None,
    additions: Iterable[tuple[int, Mapping[str, Any]]] = (),
) -> dict[str, Any]:
    """Return restart-safe scheduler controls without monotonic timestamps."""
    replacements = replacements or {}
    entries: list[tuple[int, Mapping[str, Any]]] = []
    for identity, (sequence, job) in _queue_waiters.items():
        candidate = replacements.get(identity, job)
        if (
            candidate.get("status") == "queued"
            and not candidate.get("cancel_requested", False)
        ):
            entries.append((sequence, candidate))
    next_sequence = max(
        [sequence for sequence, _ in entries] + [_queue_sequence],
    )
    durable_ids = {str(job.get("id")) for _, job in entries if job.get("id")}
    for identity, candidate in replacements.items():
        durable_id = str(candidate.get("id") or "")
        if (
            identity not in _queue_waiters
            and durable_id
            and durable_id not in durable_ids
            and candidate.get("status") == "queued"
            and not candidate.get("cancel_requested", False)
        ):
            next_sequence += 1
            entries.append((next_sequence, candidate))
            durable_ids.add(durable_id)
    existing_identities = {identity for identity in _queue_waiters}
    for sequence, candidate in additions:
        durable_id = str(candidate.get("id") or "")
        if id(candidate) in existing_identities or durable_id in durable_ids:
            continue
        if (
            candidate.get("status") == "queued"
            and not candidate.get("cancel_requested", False)
        ):
            entries.append((sequence, candidate))
            if durable_id:
                durable_ids.add(durable_id)
    entries.sort(key=_queue_order_key)
    return {
        "paused": _queue_paused if paused is None else bool(paused),
        "pause_after_current": (
            _pause_after_current
            if pause_after_current is None
            else bool(pause_after_current)
        ),
        "manual_order_sequence": (
            _queue_manual_order_sequence
            if manual_order_sequence is None
            else max(0, int(manual_order_sequence))
        ),
        "queue_order": [
            str(job.get("id")) for _, job in entries if job.get("id")
        ],
    }


def _persist_prospective_unlocked(
    name: str,
    *,
    jobs: Iterable[Mapping[str, Any]] = (),
    global_state: Mapping[str, Any] | None = None,
    tombstones: Iterable[str] = (),
    request_manifests: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    hook = _durability_hook
    if hook is None:
        return
    hook(DurableTransition(
        name=name,
        jobs=tuple(jobs),
        global_state=(None if global_state is None else dict(global_state)),
        tombstones=tuple(tombstones),
        request_manifests=(
            None if request_manifests is None else dict(request_manifests)
        ),
    ))


def _publish_job_unlocked(
    job: MutableMapping[str, Any], candidate: Mapping[str, Any],
) -> None:
    job.clear()
    job.update(candidate)


def restore_scheduler_state(
    jobs: Iterable[MutableMapping[str, Any]],
    state: Mapping[str, Any] | None,
) -> None:
    """Restore pause/manual order and restart ordering without wall/mono reuse.

    Launch calls this once with reconstructed non-terminal jobs before worker
    threads start. ``acquire_generation_slot`` consumes the private restore
    sequence so thread startup order cannot reorder recovered work.
    """
    global _queue_sequence, _queue_manual_order_sequence
    global _queue_paused, _pause_after_current
    state = state or {}
    candidates = {str(job.get("id")): job for job in jobs if job.get("id")}
    raw_order = state.get("queue_order")
    order = raw_order if isinstance(raw_order, list) else []
    with _queue_condition, _lifecycle_lock:
        _queue_waiters.clear()
        _queue_sequence = 0
        restored_ids: set[str] = set()
        for index, job_id in enumerate(order, start=1):
            job = candidates.get(str(job_id))
            if job is None or job.get("status") != "queued":
                continue
            _queue_sequence = index
            restored_ids.add(str(job_id))
            job["_queue_restore_sequence"] = index
            # Fresh process-local starvation age: monotonic values are never
            # serialized or restored across boots.
            job["_queue_enqueued_monotonic"] = time.monotonic()
            _queue_waiters[id(job)] = (index, job)
        missing = [
            job for job_id, job in candidates.items()
            if job_id not in restored_ids
            and job.get("status") == "queued"
            and not job.get("cancel_requested", False)
        ]
        missing.sort(key=lambda job: _queue_order_key((_queue_sequence + 1, job)))
        for job in missing:
            _queue_sequence += 1
            job["_queue_restore_sequence"] = _queue_sequence
            job["_queue_enqueued_monotonic"] = time.monotonic()
            _queue_waiters[id(job)] = (_queue_sequence, job)
        _queue_manual_order_sequence = max(
            int(state.get("manual_order_sequence", 0) or 0),
            max((_queue_manual_order(job) for job in candidates.values()), default=0),
        )
        _queue_paused = bool(state.get("paused", False))
        _pause_after_current = bool(state.get("pause_after_current", False))
        _queue_condition.notify_all()


def durable_queue_state(
    *,
    additions: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return the strict global snapshot used for initial job registration."""
    with _queue_condition, _lifecycle_lock:
        base_sequence = _queue_sequence
        prospective = tuple(
            (base_sequence + index, job)
            for index, job in enumerate(additions, start=1)
        )
        return _global_state_unlocked(additions=prospective)


def make_residency_key(*components: Any) -> str:
    """Return a prompt/path-safe opaque fingerprint for stable load inputs."""
    encoded = json.dumps(
        components,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _OPAQUE_RESIDENCY_KEY_PREFIX + hashlib.sha256(encoded).hexdigest()


def _is_opaque_residency_key(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith(_OPAQUE_RESIDENCY_KEY_PREFIX):
        return False
    digest = value[len(_OPAQUE_RESIDENCY_KEY_PREFIX):]
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def stamp_job_residency(
    job: MutableMapping[str, Any],
    base_key: str,
    affinity_key: str | None = None,
) -> None:
    """Server-stamp opaque requested residency identities on a queued job."""
    if not _is_opaque_residency_key(base_key):
        raise ValueError("base_key must be an opaque Maestro residency key")
    if affinity_key is not None and not _is_opaque_residency_key(affinity_key):
        raise ValueError("affinity_key must be an opaque Maestro residency key")
    with _queue_condition, _lifecycle_lock:
        if job.get("status") != "queued" or is_cancel_requested(job):
            raise ValueError("residency can only be stamped on a queued job")
        job["residency_base_key"] = base_key
        if affinity_key is None:
            job.pop("residency_affinity_key", None)
        else:
            job["residency_affinity_key"] = affinity_key
        _queue_condition.notify_all()


def clear_job_residency(job: MutableMapping[str, Any]) -> bool:
    """Clear a queued job's requested identity after derivation fails."""
    with _queue_condition, _lifecycle_lock:
        if job.get("status") != "queued" or is_cancel_requested(job):
            return False
        job.pop("residency_base_key", None)
        job.pop("residency_affinity_key", None)
        _queue_condition.notify_all()
        return True


@contextmanager
def residency_configuration_update() -> Iterator[None]:
    """Serialize load-setting mutation/restamping with queue admission."""
    with _queue_condition:
        yield


def note_residency_state(
    base_key: str | None,
    affinity_key: str | None = None,
) -> None:
    """Publish the currently resident opaque identities to the scheduler."""
    global _resident_base_key, _resident_affinity_key
    if base_key is not None and not _is_opaque_residency_key(base_key):
        raise ValueError("base_key must be an opaque Maestro residency key")
    if affinity_key is not None and not _is_opaque_residency_key(affinity_key):
        raise ValueError("affinity_key must be an opaque Maestro residency key")
    if base_key is None and affinity_key is not None:
        raise ValueError("affinity_key requires a resident base_key")
    with _queue_condition:
        _resident_base_key = base_key
        _resident_affinity_key = affinity_key
        _queue_condition.notify_all()


def invalidate_residency_state() -> None:
    """Forget resident model affinity after unload, failure, or device reset."""
    note_residency_state(None)


def _append_job_event_unlocked(job: MutableMapping[str, Any], **values: Any) -> None:
    event = {
        "at": time.time(),
        "status": values.get("status", job.get("status", "")),
        "message": values.get("message", job.get("message", "")),
        "phase": values.get("phase", job.get("phase", "")),
        "progress": values.get("progress", job.get("progress", 0)),
        "step": values.get("step", job.get("step", 0)),
        "total_steps": values.get("total_steps", job.get("total_steps", 0)),
    }
    events = list(job.get("events") or [])
    previous = events[-1] if events else None
    comparable = {key: value for key, value in event.items() if key != "at"}
    if previous and all(previous.get(key) == value for key, value in comparable.items()):
        return
    events.append(event)
    job["events"] = events[-_MAX_JOB_EVENTS:]


def job_events(job: MutableMapping[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    """Return a bounded copy of one job's newest lifecycle/progress events."""
    safe_limit = max(1, min(_MAX_JOB_EVENTS, int(limit or 100)))
    with _lifecycle_lock:
        return [dict(event) for event in list(job.get("events") or [])[-safe_limit:]]


def update_requested_outputs(
    job: MutableMapping[str, Any],
    count: int,
    *,
    active_state: MutableMapping[str, Any] | None = None,
) -> bool:
    """Change output count while queued or during a repeat-capable run."""
    target = max(1, min(25, int(count)))
    with _lifecycle_lock:
        status = job.get("status")
        candidate = _copy_job_for_transition(job)
        params = candidate.get("params")
        if not isinstance(params, MutableMapping):
            return False
        if status == "queued":
            params["repeat_generation"] = target
            candidate["requested_outputs"] = target
            _append_job_event_unlocked(candidate, message=f"Output count changed to {target}")
            _persist_prospective_unlocked("output_count", jobs=(candidate,))
            _publish_job_unlocked(job, candidate)
            return True
        if status != "running" or active_state is None:
            return False
        # H3 expands complete segment chains before inference; mutating WGP's
        # per-task repeat counter would repeat every segment independently and
        # break continuity. Queued H3 plans remain fully editable.
        if isinstance(params.get("_h3_longform"), dict):
            return False
        try:
            completed = max(0, int(active_state.get("repeat_no") or 0))
            current_total = max(
                completed,
                int(active_state.get("total_generation") or job.get("requested_outputs") or 1),
            )
        except (TypeError, ValueError):
            return False
        if target < completed:
            return False
        # WGP consumes and clears this delta at the top of its next repeat.
        params["repeat_generation"] = target
        candidate["requested_outputs"] = target
        _append_job_event_unlocked(candidate, message=f"Output count changed to {target}")
        _persist_prospective_unlocked("output_count", jobs=(candidate,))
        active_state["extra_orders"] = target - current_total
        _publish_job_unlocked(job, candidate)
        return True


@dataclass(frozen=True)
class CancelResult:
    """Result of a cancellation request."""

    changed: bool
    was_running: bool
    abort_signalled: bool


def _queue_priority(job: Mapping[str, Any]) -> int:
    try:
        return int(job.get("queue_priority", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _queue_manual_order(job: Mapping[str, Any]) -> int:
    try:
        return max(0, int(job.get("_queue_manual_order", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _queue_tier_key(
    entry: tuple[int, MutableMapping[str, Any]],
) -> tuple[bool, int, int]:
    _, job = entry
    return (
        bool(job.get("source_remote", False)),
        _queue_priority(job),
        _queue_manual_order(job),
    )


def _queue_order_key(entry: tuple[int, MutableMapping[str, Any]]) -> tuple:
    sequence, job = entry
    priority = _queue_priority(job)
    manual_order = _queue_manual_order(job)
    try:
        created_at = float(job.get("created_at", 0) or 0)
    except (TypeError, ValueError):
        created_at = 0.0
    # Local submissions always wait ahead of Cloudflare submissions, but this
    # key is consulted only before admission: an already-running remote job is
    # never preempted or paused.
    remote = bool(job.get("source_remote", False))
    return (remote, -priority, -manual_order, created_at, sequence)


def _eligible_queue_entries() -> list[tuple[int, MutableMapping[str, Any]]]:
    return [
        entry for entry in _queue_waiters.values()
        if entry[1].get("status") == "queued"
        and not entry[1].get("queue_held", False)
        and not is_cancel_requested(entry[1])
    ]


def _residency_match_reason(job: Mapping[str, Any]) -> str | None:
    if _resident_base_key is None:
        return None
    if job.get("residency_base_key") != _resident_base_key:
        return None
    if (
        _resident_affinity_key is not None
        and job.get("residency_affinity_key") == _resident_affinity_key
    ):
        return "resident_affinity"
    return "resident_base"


def _residency_bypass_count(job: Mapping[str, Any]) -> int:
    try:
        count = int(job.get("_residency_bypass_count", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    return max(0, min(MAX_RESIDENCY_BYPASSES, count))


def resource_descriptor(job: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return one bounded, content-free execution descriptor.

    The descriptor deliberately exposes no device identifier, lease token,
    model identity, residency key, hardware measurement, or fairness state.
    Missing fields retain conservative legacy generation semantics.
    """
    intent = job.get("resource_intent")
    if intent not in _RESOURCE_INTENTS:
        return None
    execution = job.get("resource_execution", RESOURCE_EXECUTION_STANDARD)
    if execution not in _RESOURCE_EXECUTIONS:
        return None
    preemption_mode = job.get("preemption_mode", PREEMPTION_MODE_NONE)
    if preemption_mode not in _PREEMPTION_MODES:
        return None
    state = job.get("resource_state")
    if state not in RESOURCE_STATES:
        status = str(job.get("status") or "")
        state = (
            "running" if status in {"running", "preparing"}
            else "released" if status in TERMINAL_STATUSES
            else "queued"
        )
    try:
        execution_attempt = int(job.get("execution_attempt", 1) or 1)
    except (TypeError, ValueError):
        return None
    if not 1 <= execution_attempt <= MAX_EXECUTION_ATTEMPT:
        return None
    preemptible = bool(
        intent == RESOURCE_INTENT_TEXT
        and execution == RESOURCE_EXECUTION_CPU
        and preemption_mode == PREEMPTION_MODE_DISCARD_RESTART
        and job.get("_resource_preemption_eligible") is True
        and state in {
            "admitted", "running", "preemption_requested",
            "resources_releasing",
        }
        and str(job.get("status") or "") not in TERMINAL_STATUSES
        and not is_cancel_requested(job)
    )
    return {
        "intent": intent,
        "execution": execution,
        "preemptible": preemptible,
        "preemption_mode": preemption_mode,
        "state": state,
        "execution_attempt": execution_attempt,
    }


def _valid_expected_execution_attempt(
    job: Mapping[str, Any], expected: int | None,
) -> bool:
    """Return whether one optional internal attempt fence is current."""
    if expected is None:
        return True
    return (
        type(expected) is int
        and 1 <= expected <= MAX_EXECUTION_ATTEMPT
        and job.get("execution_attempt", 1) == expected
    )


def transition_resource_execution(
    job: MutableMapping[str, Any],
    *,
    expected_execution_attempt: int | None = None,
    intent: str | None = None,
    execution: str | None = None,
    preemption_mode: str | None = None,
    state: str,
    increment_attempt: bool = False,
    reset_progress: bool = False,
    **updates: Any,
) -> int | None:
    """Durably publish one bounded resource/attempt transition.

    The returned positive attempt is the only token allowed to publish later
    progress or results.  A stale attempt, cancellation, terminal job, invalid
    enum, or restart-cap overflow returns ``None`` without mutation.
    """
    if state not in RESOURCE_STATES:
        raise ValueError("Invalid resource execution state")
    if intent is not None and intent not in _RESOURCE_INTENTS:
        raise ValueError("Invalid resource intent")
    if execution is not None and execution not in _RESOURCE_EXECUTIONS:
        raise ValueError("Invalid resource execution")
    if preemption_mode is not None and preemption_mode not in _PREEMPTION_MODES:
        raise ValueError("Invalid resource preemption mode")
    if "status" in updates or "execution_attempt" in updates:
        raise ValueError("Lifecycle and attempt fields are transition-owned")
    with _queue_condition, _lifecycle_lock:
        if (
            is_cancel_requested(job)
            or str(job.get("status") or "") in TERMINAL_STATUSES
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return None
        try:
            attempt = int(job.get("execution_attempt", 1) or 1)
        except (TypeError, ValueError):
            return None
        if not 1 <= attempt <= MAX_EXECUTION_ATTEMPT:
            return None
        if increment_attempt:
            if attempt >= MAX_EXECUTION_ATTEMPT:
                return None
            attempt += 1
        candidate = _copy_job_for_transition(job)
        candidate.update(updates)
        candidate["resource_intent"] = intent or str(
            candidate.get("resource_intent") or RESOURCE_INTENT_GENERATION
        )
        candidate["resource_execution"] = execution or str(
            candidate.get("resource_execution") or RESOURCE_EXECUTION_STANDARD
        )
        candidate["preemption_mode"] = preemption_mode or str(
            candidate.get("preemption_mode") or PREEMPTION_MODE_NONE
        )
        candidate["resource_state"] = state
        candidate["execution_attempt"] = attempt
        if reset_progress:
            candidate.update({
                "progress": 0,
                "step": 0,
                "overall_progress": 0,
                "phase_started_at": time.time(),
            })
        _append_job_event_unlocked(
            candidate,
            resource_state=state,
            resource_execution=candidate["resource_execution"],
            execution_attempt=attempt,
        )
        _persist_prospective_unlocked(
            "resource_execution", jobs=(candidate,),
            global_state=_global_state_unlocked(
                replacements={id(job): candidate},
            ),
        )
        if (
            is_cancel_requested(job)
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return None
        _publish_job_unlocked(job, candidate)
        _queue_condition.notify_all()
        return attempt


def _select_next_waiter(
    eligible: list[tuple[int, MutableMapping[str, Any]]],
    *,
    bypass_counts: Mapping[int, int] | None = None,
    now: float | None = None,
) -> tuple[
    tuple[int, MutableMapping[str, Any]] | None,
    str | None,
    list[tuple[int, MutableMapping[str, Any]]],
]:
    """Select without mutation; affinity can only reorder the head's tier."""
    if not eligible:
        return None, None, []
    ordered = sorted(eligible, key=_queue_order_key)
    head = ordered[0]
    head_tier = _queue_tier_key(head)
    tier = [entry for entry in ordered if _queue_tier_key(entry) == head_tier]
    match = next(
        (entry for entry in tier if _residency_match_reason(entry[1]) == "resident_affinity"),
        None,
    )
    if match is None:
        match = next(
            (entry for entry in tier if _residency_match_reason(entry[1]) == "resident_base"),
            None,
        )
    if match is None or match is head:
        return head, "queue_order", []

    match_index = tier.index(match)
    skipped = tier[:match_index]
    check_time = time.monotonic() if now is None else now
    for entry in skipped:
        job = entry[1]
        count = (
            bypass_counts.get(id(job), 0)
            if bypass_counts is not None
            else _residency_bypass_count(job)
        )
        try:
            enqueued_at = float(
                job.get("_queue_enqueued_monotonic", check_time) or check_time,
            )
        except (TypeError, ValueError):
            enqueued_at = check_time
        if (
            count >= MAX_RESIDENCY_BYPASSES
            or check_time - enqueued_at >= RESIDENCY_AGE_CEILING_SECONDS
        ):
            return head, "starvation_guard", []
    return match, _residency_match_reason(match[1]), skipped


def _record_queue_admission(
    selected: MutableMapping[str, Any],
    reason: str,
    skipped: list[tuple[int, MutableMapping[str, Any]]],
) -> None:
    previous_bypasses = _residency_bypass_count(selected)
    selected["queue_reorder_reason"] = reason
    selected["queue_residency_bypass_count"] = previous_bypasses
    selected["queue_residency_bypassed_waiters"] = min(
        MAX_RECORDED_RESIDENCY_BYPASSED_WAITERS, len(skipped),
    )
    selected["_residency_bypass_count"] = 0
    selected.pop("_queue_enqueued_monotonic", None)
    for _, skipped_job in skipped:
        bypasses = _residency_bypass_count(skipped_job)
        skipped_job["_residency_bypass_count"] = min(
            MAX_RESIDENCY_BYPASSES, bypasses + 1,
        )


def update_queue_job(
    job: MutableMapping[str, Any],
    *,
    priority: int | None = None,
    held: bool | None = None,
) -> bool:
    """Update scheduling metadata for a job that has not started."""
    with _queue_condition, _lifecycle_lock:
        if job.get("status") != "queued" or is_cancel_requested(job):
            return False
        candidate = _copy_job_for_transition(job)
        if priority is not None:
            candidate["queue_priority"] = max(-1_000_000, min(1_000_000, int(priority)))
        if held is not None:
            candidate["queue_held"] = bool(held)
            candidate["message"] = "Held" if held else "Queued"
        _append_job_event_unlocked(candidate)
        global_state = _global_state_unlocked(replacements={id(job): candidate})
        _persist_prospective_unlocked(
            "queue_job", jobs=(candidate,), global_state=global_state,
        )
        _publish_job_unlocked(job, candidate)
        _queue_condition.notify_all()
        return True


def set_job_hold(
    job: MutableMapping[str, Any],
    held: bool,
) -> str | None:
    """Hold queued work now, or schedule running work to hold at an output.

    Returns ``"held"`` for an immediately held queued job,
    ``"after_output"`` for a running job whose safe hold was scheduled,
    ``"resumed"`` when either form of hold was cleared, and ``None`` when
    the job can no longer be controlled.
    """
    with _queue_condition, _lifecycle_lock:
        status = job.get("status")
        if status == "queued" and not is_cancel_requested(job):
            candidate = _copy_job_for_transition(job)
            candidate["queue_held"] = bool(held)
            candidate["hold_after_output"] = False
            candidate["message"] = "Held" if held else "Queued"
            _append_job_event_unlocked(candidate)
            global_state = _global_state_unlocked(replacements={id(job): candidate})
            _persist_prospective_unlocked(
                "job_hold", jobs=(candidate,), global_state=global_state,
            )
            _publish_job_unlocked(job, candidate)
            _queue_condition.notify_all()
            return "held" if held else "resumed"
        if status == "running" and not is_cancel_requested(job):
            candidate = _copy_job_for_transition(job)
            if held:
                candidate["hold_after_output"] = True
                candidate["message"] = "Will hold after the current output"
                result = "after_output"
            elif job.get("hold_after_output", False):
                candidate["hold_after_output"] = False
                candidate["message"] = "Running"
                result = "resumed"
            else:
                return None
            _append_job_event_unlocked(candidate)
            _persist_prospective_unlocked("job_hold", jobs=(candidate,))
            _publish_job_unlocked(job, candidate)
            _queue_condition.notify_all()
            return result
        return None


def promote_queued_job(job: MutableMapping[str, Any]) -> bool:
    """Make one queued job eligible and first in line, resuming admission.

    This is the backend primitive for a user-facing ``Start next`` action. It
    never steals the generation lock from an active job; it only clears a
    global pause/per-job hold and assigns the highest supported priority so
    the selected job is admitted at the next safe boundary.
    """
    global _queue_paused, _pause_after_current, _queue_manual_order_sequence
    with _queue_condition, _lifecycle_lock:
        if job.get("status") != "queued" or is_cancel_requested(job):
            return False
        candidate = _copy_job_for_transition(job)
        next_manual_order = _queue_manual_order_sequence + 1
        candidate["queue_held"] = False
        candidate["queue_priority"] = 1_000_000
        candidate["_queue_manual_order"] = next_manual_order
        candidate["message"] = "Queued — starting next"
        _append_job_event_unlocked(candidate)
        global_state = _global_state_unlocked(
            paused=False,
            pause_after_current=False,
            manual_order_sequence=next_manual_order,
            replacements={id(job): candidate},
        )
        _persist_prospective_unlocked(
            "start_next", jobs=(candidate,), global_state=global_state,
        )
        _publish_job_unlocked(job, candidate)
        _queue_manual_order_sequence = next_manual_order
        _queue_paused = False
        _pause_after_current = False
        _queue_condition.notify_all()
        return True


def queue_wait_reason(
    job: MutableMapping[str, Any],
    *,
    generation_busy: bool = False,
    active_other_user: bool = False,
    position: int | None = None,
    position_resolved: bool = False,
) -> str | None:
    """Return a stable, prompt-free explanation for a job's queue wait."""
    with _queue_condition:
        if job.get("status") == "running":
            return "running"
        if job.get("status") == "preparing":
            if job.get("resource_state") in {"queued", "blocked"}:
                return "resource_wait"
            return "preparing"
        if job.get("status") == "waiting_for_plan_approval":
            return (
                "waiting_for_plan_terms"
                if job.get("plan_review_terms_required", False)
                else "waiting_for_plan_approval"
            )
        if job.get("status") != "queued" or is_cancel_requested(job):
            return None
        if job.get("queue_held", False):
            return "held"
        if _queue_paused:
            return "queue_paused"
        if not position_resolved:
            position = queue_position(job)
        if position is None:
            return "registering"
        if position > 1:
            return "waiting_for_turn"
        if (
            generation_busy
            and job.get("resource_intent") == RESOURCE_INTENT_GENERATION
        ):
            return "resource_wait"
        if generation_busy:
            if active_other_user:
                return "waiting_for_other_user"
            return "waiting_for_active_generation"
        return "ready"


def set_queue_pause_after_current(enabled: bool) -> dict[str, bool]:
    """Latch a global pause that takes effect after the active job releases."""
    global _pause_after_current
    with _queue_condition:
        global_state = _global_state_unlocked(pause_after_current=bool(enabled))
        _persist_prospective_unlocked(
            "pause_after_current", global_state=global_state,
        )
        _pause_after_current = bool(enabled)
        _queue_condition.notify_all()
        return queue_control_state()


def set_queue_paused(paused: bool) -> dict[str, bool]:
    """Pause or resume admission of queued GPU jobs."""
    global _queue_paused, _pause_after_current
    with _queue_condition:
        prospective_paused = bool(paused)
        prospective_after = _pause_after_current if prospective_paused else False
        global_state = _global_state_unlocked(
            paused=prospective_paused,
            pause_after_current=prospective_after,
        )
        _persist_prospective_unlocked("queue_pause", global_state=global_state)
        _queue_paused = bool(paused)
        if not _queue_paused:
            _pause_after_current = False
        _queue_condition.notify_all()
        return queue_control_state()


def queue_control_state() -> dict[str, bool]:
    with _queue_condition:
        return {
            "paused": _queue_paused,
            "pause_after_current": _pause_after_current,
        }


def _queue_positions_unlocked() -> dict[int, int]:
    """Return one stable simulated admission order keyed by job identity."""
    remaining = _eligible_queue_entries()
    simulated_bypasses = {
        id(candidate): _residency_bypass_count(candidate)
        for _, candidate in remaining
    }
    check_time = time.monotonic()
    positions: dict[int, int] = {}
    for index in range(1, len(remaining) + 1):
        selected, _, skipped = _select_next_waiter(
            remaining,
            bypass_counts=simulated_bypasses,
            now=check_time,
        )
        if selected is None:
            break
        positions[id(selected[1])] = index
        for _, skipped_job in skipped:
            key = id(skipped_job)
            simulated_bypasses[key] = min(
                MAX_RESIDENCY_BYPASSES,
                simulated_bypasses.get(key, 0) + 1,
            )
        remaining.remove(selected)
    return positions


def queue_scheduler_snapshot(
    jobs: Iterable[MutableMapping[str, Any]],
    *,
    generation_busy: bool = False,
) -> dict[str, Any]:
    """Snapshot global anonymous counts and dynamic positions atomically.

    The queue lock is always acquired before the lifecycle lock, matching the
    scheduler's existing lock order. ``jobs`` is materialized first so callers
    can pass a registry view without holding a mutable-dictionary iterator
    while the locks are acquired.
    """
    candidates = list(jobs)
    with _queue_condition, _lifecycle_lock:
        positions = _queue_positions_unlocked()
        running = 0
        held = 0
        registering = 0
        preparing = 0
        approval_waiting = 0
        seen: set[int] = set()
        states: dict[int, dict[str, Any]] = {}
        for job in candidates:
            identity = id(job)
            if identity in seen:
                continue
            seen.add(identity)
            state = dict(job)
            states[identity] = state
            status = state.get("status")
            if status == "running":
                running += 1
            elif status == "preparing":
                preparing += 1
            elif status == "waiting_for_plan_approval":
                approval_waiting += 1
            elif status == "queued" and not state.get("cancel_requested", False):
                if state.get("queue_held", False):
                    held += 1
                elif identity not in positions:
                    registering += 1

        waiting = len(positions)
        summary = {
            "running": running,
            "waiting": waiting,
            "held": held,
            "registering": registering,
            "preparing": preparing,
            "approval_waiting": approval_waiting,
            "active_total": (
                running + waiting + held + registering
                + preparing + approval_waiting
            ),
        }
        running_states = [
            state for state in states.values()
            if state.get("status") == "running"
        ]
        wait_reasons: dict[int, str | None] = {}
        for identity, state in states.items():
            status = state.get("status")
            position = positions.get(identity)
            if status == "running":
                reason = "running"
            elif status == "preparing":
                reason = (
                    "resource_wait"
                    if state.get("resource_state") in {"queued", "blocked"}
                    else "preparing"
                )
            elif status == "waiting_for_plan_approval":
                reason = (
                    "waiting_for_plan_terms"
                    if state.get("plan_review_terms_required", False)
                    else "waiting_for_plan_approval"
                )
            elif status != "queued" or state.get("cancel_requested", False):
                reason = None
            elif state.get("queue_held", False):
                reason = "held"
            elif _queue_paused:
                reason = "queue_paused"
            elif position is None:
                reason = "registering"
            elif position > 1:
                reason = "waiting_for_turn"
            elif (
                generation_busy
                and state.get("resource_intent")
                == RESOURCE_INTENT_GENERATION
            ):
                reason = "resource_wait"
            elif running_states:
                owner = state.get("session_id")
                active_other_user = owner is not None and any(
                    active.get("session_id") is not None
                    and active.get("session_id") != owner
                    for active in running_states
                )
                reason = (
                    "waiting_for_other_user"
                    if active_other_user
                    else "waiting_for_active_generation"
                )
            else:
                reason = "ready"
            wait_reasons[identity] = reason
        return {
            "paused": _queue_paused,
            "pause_after_current": _pause_after_current,
            "summary": summary,
            "positions": positions,
            "states": states,
            "wait_reasons": wait_reasons,
        }


_PUBLIC_ACTIVE_STATUSES = frozenset({
    "preparing", "waiting_for_plan_approval", "queued", "running",
})


def _reference_child_needs_public_row(
    child: Mapping[str, Any], parent: Mapping[str, Any],
) -> bool:
    """Keep exceptional children explicit; ordinary internal work is folded."""
    state = str(child.get("recovery_state") or "")
    actions = child.get("recovery_actions")
    if (
        child.get("status") == "waiting_for_plan_approval"
        or child.get("resource_state") == "blocked"
        or state == "interrupted"
        or state.startswith("blocked")
        or child.get("recovery_actionable") is True
        or (isinstance(actions, (list, tuple)) and bool(actions))
    ):
        return True
    if child.get("status") == "failed":
        # A failed child is safely represented by its strict reverse relation
        # on the parent.  Otherwise retain the row as an actionable orphan.
        return str(parent.get("failed_child_job_id") or "") != str(
            child.get("id") or ""
        )
    return False


def authorized_logical_queue_projection(
    jobs: Iterable[MutableMapping[str, Any]],
    scheduler: Mapping[str, Any],
) -> dict[str, Any]:
    """Fold internal Reference children after caller authorization.

    ``jobs`` must already contain only rows authorized for one requester.  The
    returned summary is derived solely from those logical roots, never from the
    scheduler's host-global summary.  Physical rows remain available to the
    caller so queue controls and recovery can target an authoritative child.
    """
    physical = list(jobs)
    scheduler_states = scheduler.get("states")
    if not isinstance(scheduler_states, Mapping):
        scheduler_states = {}
    states: dict[int, dict[str, Any]] = {}
    by_public_id: dict[str, MutableMapping[str, Any]] = {}
    for job in physical:
        snapshot = scheduler_states.get(id(job))
        state = dict(snapshot) if isinstance(snapshot, Mapping) else dict(job)
        states[id(job)] = state
        job_id = state.get("id")
        if isinstance(job_id, str) and job_id:
            by_public_id[job_id] = job

    folded_child_ids: set[str] = set()
    representative_by_parent: dict[str, MutableMapping[str, Any]] = {}
    for child in physical:
        child_state = states[id(child)]
        parent_id = child_state.get("parent_job_id")
        child_id = child_state.get("id")
        if (
            not isinstance(parent_id, str)
            or not parent_id
            or not isinstance(child_id, str)
            or not child_id
            or parent_id == child_id
        ):
            continue
        parent = by_public_id.get(parent_id)
        if parent is None:
            continue
        parent_state = states[id(parent)]
        if (
            parent_state.get("logical_job_kind") != "reference_pack_parent"
            or child_state.get("logical_job_kind") != "reference_pack_child"
            or child_state.get("resource_intent") != RESOURCE_INTENT_GENERATION
            or parent_state.get("session_id") != child_state.get("session_id")
            or _reference_child_needs_public_row(child_state, parent_state)
        ):
            continue
        folded_child_ids.add(child_id)
        if child_state.get("status") in _PUBLIC_ACTIVE_STATUSES:
            representative_by_parent[parent_id] = child

    logical_roots = [
        job for job in physical
        if str(states[id(job)].get("id") or "") not in folded_child_ids
    ]
    positions = scheduler.get("positions")
    if not isinstance(positions, Mapping):
        positions = {}
    public_positions = {
        id(job): index
        for index, job in enumerate(
            sorted(
                (
                    job for job in physical
                    if id(job) in positions
                ),
                key=lambda job: positions[id(job)],
            ),
            start=1,
        )
    }
    summary = {
        "running": 0,
        "waiting": 0,
        "held": 0,
        "registering": 0,
        "preparing": 0,
        "approval_waiting": 0,
        "active_total": 0,
    }
    representative_job_ids: dict[str, str] = {}
    for root in logical_roots:
        root_state = states[id(root)]
        root_id = str(root_state.get("id") or "")
        representative = representative_by_parent.get(root_id, root)
        state = states[id(representative)]
        representative_id = str(state.get("id") or root_id)
        representative_job_ids[root_id] = representative_id
        status = state.get("status")
        if status not in _PUBLIC_ACTIVE_STATUSES:
            continue
        if status == "queued" and state.get("cancel_requested", False):
            continue
        summary["active_total"] += 1
        if status == "running":
            summary["running"] += 1
        elif status == "preparing":
            summary["preparing"] += 1
        elif status == "waiting_for_plan_approval":
            summary["approval_waiting"] += 1
        elif state.get("queue_held", False):
            summary["held"] += 1
        elif id(representative) not in positions:
            summary["registering"] += 1
        else:
            summary["waiting"] += 1

    return {
        "physical_jobs": physical,
        "logical_jobs": logical_roots,
        "folded_child_ids": frozenset(folded_child_ids),
        "representative_job_ids": representative_job_ids,
        "public_positions": public_positions,
        "summary": summary,
    }


def queue_position(job: MutableMapping[str, Any]) -> int | None:
    """Return the one-based admission position among eligible waiters."""
    with _queue_condition:
        return _queue_positions_unlocked().get(id(job))


def _reset_queue_state_for_tests() -> None:
    """Reset process-global scheduler state (test isolation only)."""
    global _queue_sequence, _queue_manual_order_sequence
    global _queue_paused, _pause_after_current
    global _resident_base_key, _resident_affinity_key
    global _durability_hook
    with _queue_condition:
        _queue_waiters.clear()
        _queue_sequence = 0
        _queue_manual_order_sequence = 0
        _queue_paused = False
        _pause_after_current = False
        _resident_base_key = None
        _resident_affinity_key = None
        _durability_hook = None
        _queue_condition.notify_all()


GENERATED_MEDIA_EXTENSIONS = frozenset({
    ".aac", ".flac", ".gif", ".jpeg", ".jpg", ".m4a", ".mkv", ".mov",
    ".mp3", ".mp4", ".ogg", ".png", ".wav", ".webm", ".webp",
})


def collect_job_outputs(
    gen: Mapping[str, Any],
    out_dir: str,
    before: set[str] | None = None,
    *,
    allow_legacy_fallback: bool = False,
) -> list[str]:
    """Return only files explicitly registered by this generation state.

    WGP records generated media in ``file_list`` and ``audio_file_list``.
    Those lists are job-local, unlike a directory before/after scan that can
    accidentally claim a concurrent pipeline operation's output.  A guarded
    one-file fallback remains for older non-Director generators that do not
    register their result.
    """
    output_root = os.path.normcase(os.path.realpath(os.path.abspath(out_dir)))
    owned: list[str] = []
    seen: set[str] = set()
    for list_name in ("artifact_list", "file_list", "audio_file_list"):
        values = gen.get(list_name) or []
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            if isinstance(value, tuple):
                value = value[0] if value else None
            if not isinstance(value, str) or not value:
                continue
            # WGP registers both bare filenames (``clip.jpg``) and paths that
            # are already rooted at a relative output directory
            # (``outputs/clip.jpg``).  Blindly joining every relative value to
            # ``out_dir`` turns the latter into ``outputs/outputs/clip.jpg``
            # and silently loses the generated artifact.  Try the two exact
            # interpretations, accepting only an existing direct child of the
            # resolved output root so the ownership boundary remains strict.
            candidates = [value] if os.path.isabs(value) else [
                value,
                os.path.join(out_dir, value),
            ]
            candidate = None
            checked: set[str] = set()
            for registered_path in candidates:
                resolved = os.path.realpath(os.path.abspath(registered_path))
                normalized = os.path.normcase(resolved)
                if normalized in checked:
                    continue
                checked.add(normalized)
                if (
                    os.path.normcase(os.path.dirname(resolved)) == output_root
                    and os.path.isfile(resolved)
                ):
                    candidate = resolved
                    break
            if candidate is None:
                continue
            filename = os.path.basename(candidate)
            if (
                filename.startswith("_continuation_")
                or filename in seen
            ):
                continue
            seen.add(filename)
            owned.append(filename)

    if owned or not allow_legacy_fallback:
        return owned

    # Legacy fallback: accept exactly one newly-created, non-temporary media
    # file.  Ambiguity is safer to report as no output than to claim another
    # operation's artifact and stamp it with this job's metadata.
    try:
        candidates = []
        for filename in sorted(set(os.listdir(out_dir)) - set(before or ())):
            extension = os.path.splitext(filename)[1].lower()
            if (
                extension not in GENERATED_MEDIA_EXTENSIONS
                or filename.startswith("_")
                or not os.path.isfile(os.path.join(out_dir, filename))
            ):
                continue
            candidates.append(filename)
    except OSError:
        return []
    return candidates if len(candidates) == 1 else []


def call_with_sticky_interrupt(
    abort_state: Mapping[str, Any],
    model: Any,
    callable_: Callable[..., Any],
    *args: Any,
    poll_interval: float = 0.02,
    **kwargs: Any,
) -> Any:
    """Run a model call while making a durable abort survive model resets.

    Several model wrappers clear ``_interrupt`` at the beginning of their
    ``generate`` method.  A cancellation can land just before that reset, so
    relay the durable job abort flag until the call exits.  The normal direct
    interrupt remains the fast path; this closes the reset race.
    """

    def _reassert_interrupt() -> None:
        try:
            setattr(model, "_interrupt", True)
        except Exception:
            pass

    if abort_state.get("abort", False):
        _reassert_interrupt()
        return None

    stopped = threading.Event()

    def _relay() -> None:
        while not stopped.wait(poll_interval):
            if abort_state.get("abort", False):
                _reassert_interrupt()

    relay = threading.Thread(
        target=_relay,
        daemon=True,
        name="maestro_abort_relay",
    )
    relay.start()
    try:
        # Close the window between the first check and relay startup.
        if abort_state.get("abort", False):
            _reassert_interrupt()
            return None
        return callable_(*args, **kwargs)
    finally:
        if abort_state.get("abort", False):
            _reassert_interrupt()
        stopped.set()
        relay.join(timeout=max(0.1, poll_interval * 2))


def is_cancel_requested(job: MutableMapping[str, Any]) -> bool:
    """Return whether cancellation is durable for ``job``."""
    with _lifecycle_lock:
        return bool(job.get("cancel_requested")) or job.get("status") == "cancelled"


def snapshot_job(job: MutableMapping[str, Any]) -> dict[str, Any]:
    """Return a consistent shallow snapshot for API polling."""
    with _lifecycle_lock:
        snapshot = dict(job)
        if isinstance(snapshot.get("output_files"), list):
            snapshot["output_files"] = list(snapshot["output_files"])
        if isinstance(snapshot.get("artifact_files"), list):
            snapshot["artifact_files"] = list(snapshot["artifact_files"])
        if isinstance(snapshot.get("clip_output_files"), dict):
            snapshot["clip_output_files"] = dict(snapshot["clip_output_files"])
        if isinstance(snapshot.get("events"), list):
            snapshot["events"] = [dict(event) for event in snapshot["events"]]
        return snapshot


def _merge_unique_filenames(
    existing: Any,
    filenames: Any,
) -> list[str]:
    merged = [
        filename for filename in (existing or [])
        if isinstance(filename, str) and filename
    ]
    for filename in filenames or []:
        if (
            isinstance(filename, str)
            and filename
            and filename not in merged
        ):
            merged.append(filename)
    return merged


def _segmented_final_outputs(
    job: MutableMapping[str, Any],
    output_files: list[str],
    *,
    join_output_file: str | None = None,
    final_output_files: list[str] | None = None,
) -> list[str]:
    """Return the user-complete outputs from one artifact publication.

    ``collect_job_outputs`` intentionally returns every artifact owned by the
    worker.  Automatic H3 long-form jobs, however, publish segment clips,
    native/sliding windows, and generated audio before their requested variant
    is complete.  WGP's durable user output is the group concatenation.  Its
    producer supplies ``join_output_file`` for the first group and uses the
    stable ``*_multiclip`` output contract for every additional H3 variant.

    Callers with richer producer information can pass ``final_output_files``
    explicitly; this keeps finality producer-owned rather than forcing later
    consumers to rediscover it from the artifact set.
    """
    if final_output_files is not None:
        candidates = final_output_files
    else:
        params = job.get("params")
        h3_plan = (
            params.get("_h3_longform")
            if isinstance(params, Mapping)
            else None
        )
        try:
            h3_longform = (
                isinstance(h3_plan, Mapping)
                and int(h3_plan.get("clip_count") or 0) > 1
            )
        except (TypeError, ValueError):
            h3_longform = False
        # Clip indexing is also used by Director partial-output persistence;
        # it does not by itself prove that a final concatenation is expected.
        segmented = h3_longform or bool(join_output_file)
        if not segmented:
            return _merge_unique_filenames([], output_files)

        joined = {join_output_file} if join_output_file else set()
        for filename in output_files:
            if not isinstance(filename, str) or not filename:
                continue
            stem = os.path.splitext(os.path.basename(filename))[0].casefold()
            if "_multiclip" in stem:
                joined.add(filename)
        candidates = [
            filename for filename in output_files
            if filename in joined
        ]
    available = set(output_files)
    return _merge_unique_filenames(
        [],
        [filename for filename in candidates if filename in available],
    )


def _replace_job_outputs_unlocked(
    job: MutableMapping[str, Any],
    output_files: list[str],
) -> None:
    """Replace public finals while retaining all observed artifact lineage."""
    prior_outputs = list(job.get("output_files") or [])
    job["artifact_files"] = _merge_unique_filenames(
        _merge_unique_filenames(job.get("artifact_files"), prior_outputs),
        output_files,
    )
    # A direct lifecycle replacement is producer-authored, unlike the broad
    # discovery list accepted by ``record_job_outputs``.  Preserve that
    # explicit finality even when a postprocessor gives the output a name that
    # does not retain WGP's multiclip suffix.
    job["output_files"] = _segmented_final_outputs(
        job,
        output_files,
        final_output_files=output_files,
    )


def record_job_outputs(
    job: MutableMapping[str, Any],
    output_files: list[str],
    *,
    clip_output_files: Mapping[int | str, str] | None = None,
    join_output_file: str | None = None,
    final_output_files: list[str] | None = None,
) -> list[str]:
    """Merge final outputs and complete artifact lineage without status changes."""
    with _lifecycle_lock:
        candidate = _copy_job_for_transition(job)
        candidate["artifact_files"] = _merge_unique_filenames(
            _merge_unique_filenames(
                candidate.get("artifact_files"), candidate.get("output_files"),
            ),
            output_files,
        )
        final_files = _segmented_final_outputs(
            candidate,
            output_files,
            join_output_file=join_output_file,
            final_output_files=final_output_files,
        )
        candidate["output_files"] = _merge_unique_filenames(
            candidate.get("output_files"), final_files,
        )
        if clip_output_files:
            clip_outputs = dict(candidate.get("clip_output_files") or {})
            for index, filename in clip_output_files.items():
                try:
                    key = str(int(index))
                except (TypeError, ValueError):
                    continue
                if filename:
                    clip_outputs[key] = filename
            candidate["clip_output_files"] = clip_outputs
        if join_output_file:
            candidate["join_output_file"] = join_output_file
        _persist_prospective_unlocked("record_outputs", jobs=(candidate,))
        _publish_job_unlocked(job, candidate)
        return list(candidate["output_files"])


def update_preparation_job(
    job: MutableMapping[str, Any],
    *,
    expected_execution_attempt: int | None = None,
    **updates: Any,
) -> bool:
    """Durably publish content-free preparation progress before GPU admission."""
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    with _queue_condition, _lifecycle_lock:
        if (
            is_cancel_requested(job)
            or job.get("status") != "preparing"
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return False
        candidate = _copy_job_for_transition(job)
        candidate.update(updates)
        _append_job_event_unlocked(candidate, **updates)
        _persist_prospective_unlocked("preparation_progress", jobs=(candidate,))
        if (
            is_cancel_requested(job)
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return False
        _publish_job_unlocked(job, candidate)
        _queue_condition.notify_all()
        return True


def checkpoint_recovery_job(
    job: MutableMapping[str, Any],
    **updates: Any,
) -> bool:
    """Durably checkpoint recovery without reviving a terminal winner."""
    with _queue_condition, _lifecycle_lock:
        current_status = str(job.get("status") or "")
        target_status = str(updates.get("status") or current_status)
        if (
            current_status in TERMINAL_STATUSES
            and target_status != current_status
        ) or (
            is_cancel_requested(job) and target_status != "cancelled"
        ):
            return False
        candidate = _copy_job_for_transition(job)
        candidate.update(updates)
        _persist_prospective_unlocked("recovery_checkpoint", jobs=(candidate,))
        # A durability hook may synchronously deliver cancellation (tests and
        # adapters can re-enter through the RLock). Preserve that later winner
        # instead of publishing this older recovery candidate in memory.
        if is_cancel_requested(job) and target_status != "cancelled":
            return False
        _publish_job_unlocked(job, candidate)
        _queue_condition.notify_all()
        return True


def block_generation_recovery(
    job: MutableMapping[str, Any],
    *,
    request_manifest: Mapping[str, Any] | None = None,
    **updates: Any,
) -> bool:
    """Durably park incomplete generation without reviving a cancel winner.

    This transition is used only after verified safe-unit reconciliation.  It
    deliberately leaves the job in scheduler membership but held, so restart
    and an owner-scoped recovery action can continue the same job without an
    automatic denoise retry.
    """
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    with _queue_condition, _lifecycle_lock:
        if is_cancel_requested(job) or str(job.get("status") or "") not in {
            "queued", "running", "failed",
        }:
            return False
        candidate = _copy_job_for_transition(job)
        candidate.update(updates)
        candidate.update({
            "status": "queued",
            "queue_held": True,
            "plan_review_required": False,
            "plan_review_terms_required": False,
            "plan_review_deadline": None,
        })
        _append_job_event_unlocked(
            candidate,
            status="queued",
            recovery_state=str(candidate.get("recovery_state") or "blocked"),
        )
        manifests = None
        if request_manifest is not None:
            manifests = {
                str(candidate.get("id") or ""): dict(request_manifest),
            }
        _persist_prospective_unlocked(
            "generation_recovery_blocked",
            jobs=(candidate,),
            global_state=_global_state_unlocked(
                replacements={id(job): candidate},
            ),
            request_manifests=manifests,
        )
        # Durability adapters may synchronously re-enter cancellation.  The
        # later terminal transition is authoritative in both memory and disk.
        if is_cancel_requested(job):
            return False
        _publish_job_unlocked(job, candidate)
        _queue_condition.notify_all()
        return True


def complete_preparation(
    job: MutableMapping[str, Any],
    *,
    request_manifest: Mapping[str, Any],
    waiting_for_approval: bool,
    plan_review_deadline: float | None = None,
    plan_review_terms_required: bool = False,
    expected_execution_attempt: int | None = None,
    **updates: Any,
) -> bool:
    """Commit sealed parameters and leave preparation without losing cancel."""
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    if waiting_for_approval:
        if plan_review_terms_required:
            if plan_review_deadline is not None:
                raise ValueError("terms-blocked plans cannot have a deadline")
        elif (
            type(plan_review_deadline) not in {int, float}
            or not math.isfinite(plan_review_deadline)
            or plan_review_deadline <= 0
        ):
            raise ValueError("waiting plans require a finite review deadline")
    with _queue_condition, _lifecycle_lock:
        if (
            is_cancel_requested(job)
            or job.get("status") != "preparing"
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return False
        candidate = _copy_job_for_transition(job)
        candidate.update(updates)
        candidate["status"] = (
            "waiting_for_plan_approval" if waiting_for_approval else "queued"
        )
        candidate["plan_review_required"] = bool(waiting_for_approval)
        candidate["plan_review_terms_required"] = bool(
            waiting_for_approval and plan_review_terms_required
        )
        candidate["plan_review_deadline"] = (
            float(plan_review_deadline)
            if waiting_for_approval and plan_review_deadline is not None
            else None
        )
        _append_job_event_unlocked(candidate, status=candidate["status"])
        _persist_prospective_unlocked(
            "preparation_complete",
            jobs=(candidate,),
            global_state=_global_state_unlocked(
                replacements={id(job): candidate},
            ),
            request_manifests={
                str(candidate.get("id") or ""): dict(request_manifest),
            },
        )
        if (
            is_cancel_requested(job)
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return False
        _publish_job_unlocked(job, candidate)
        _queue_condition.notify_all()
        return True


def arm_prepared_job_plan_review(
    job: MutableMapping[str, Any],
    *,
    plan_review_deadline: float,
    **updates: Any,
) -> bool:
    """Durably start review after host terms unblock a frozen plan."""
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    if (
        type(plan_review_deadline) not in {int, float}
        or not math.isfinite(plan_review_deadline)
        or plan_review_deadline <= 0
    ):
        raise ValueError("plan review requires a finite deadline")
    with _queue_condition, _lifecycle_lock:
        if (
            is_cancel_requested(job)
            or job.get("status") != "waiting_for_plan_approval"
            or job.get("plan_review_required") is not True
            or job.get("plan_review_terms_required") is not True
            or job.get("plan_review_deadline") is not None
        ):
            return False
        candidate = _copy_job_for_transition(job)
        candidate.update(updates)
        candidate["plan_review_terms_required"] = False
        candidate["plan_review_deadline"] = float(plan_review_deadline)
        _append_job_event_unlocked(
            candidate,
            phase=str(candidate.get("phase") or "awaiting_plan_approval"),
            message=str(candidate.get("message") or "Review the generation plan"),
        )
        _persist_prospective_unlocked("plan_review_armed", jobs=(candidate,))
        # Durability hooks may synchronously re-enter cancellation.  Preserve
        # that later terminal winner rather than publishing this stale wait.
        if is_cancel_requested(job):
            return False
        _publish_job_unlocked(job, candidate)
        _queue_condition.notify_all()
        return True


def approve_prepared_job(
    job: MutableMapping[str, Any],
    *,
    request_manifest: Mapping[str, Any],
    **updates: Any,
) -> bool:
    """Atomically promote an approved sealed plan into GPU queue ordering."""
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    with _queue_condition, _lifecycle_lock:
        if (
            is_cancel_requested(job)
            or job.get("status") != "waiting_for_plan_approval"
        ):
            return False
        candidate = _copy_job_for_transition(job)
        candidate.update(updates)
        candidate["status"] = "queued"
        if candidate.get("resource_intent") in _RESOURCE_INTENTS:
            candidate["resource_state"] = "queued"
        candidate["plan_review_required"] = False
        candidate["plan_review_terms_required"] = False
        candidate["plan_review_deadline"] = None
        _append_job_event_unlocked(candidate, status="queued")
        _persist_prospective_unlocked(
            "plan_approved",
            jobs=(candidate,),
            global_state=_global_state_unlocked(
                replacements={id(job): candidate},
            ),
            request_manifests={
                str(candidate.get("id") or ""): dict(request_manifest),
            },
        )
        if is_cancel_requested(job):
            return False
        _publish_job_unlocked(job, candidate)
        _queue_condition.notify_all()
        return True


def block_resource_admission_failure(
    job: MutableMapping[str, Any],
) -> bool:
    """Fail closed when durable resource admission cannot start a worker.

    Returns ``True`` when the blocked state was persisted. If persistence is
    still unavailable, the same bounded state is nevertheless published in
    memory and removed from admission eligibility so the process cannot spin
    or strand an ordinary queued job. A later successful checkpoint/startup
    preserves the held ``blocked_preparation`` state.
    """
    with _queue_condition, _lifecycle_lock:
        if is_cancel_requested(job) or job.get("status") != "queued":
            return False
        candidate = _copy_job_for_transition(job)
        message = "Resource admission failed; resubmit this request"
        candidate.update({
            "status": "queued",
            "queue_held": True,
            "recovery_state": "blocked_preparation",
            "reruns_denoise": False,
            "_recovery_reason_code": "preparation_must_resubmit",
            "phase": "resource_admission_failed",
            "message": message,
            "error": message,
        })
        if candidate.get("resource_intent") in _RESOURCE_INTENTS:
            candidate["resource_execution"] = RESOURCE_EXECUTION_STANDARD
            candidate["preemption_mode"] = PREEMPTION_MODE_NONE
            candidate["resource_state"] = "blocked"
        _append_job_event_unlocked(
            candidate,
            status="queued",
            phase="resource_admission_failed",
            message=message,
        )
        persisted = True
        try:
            _persist_prospective_unlocked(
                "resource_admission_blocked",
                jobs=(candidate,),
                global_state=_global_state_unlocked(
                    replacements={id(job): candidate},
                ),
            )
        except Exception:
            # Admission safety is the one transition that must fail closed in
            # memory even if the durable writer is unavailable. The original
            # registered snapshot remains the recovery source of truth.
            persisted = False
        if is_cancel_requested(job):
            return False
        _publish_job_unlocked(job, candidate)
        _queue_waiters.pop(id(job), None)
        _queue_condition.notify_all()
        return persisted


def fail_preparation(
    job: MutableMapping[str, Any],
    *,
    expected_execution_attempt: int | None = None,
    **updates: Any,
) -> bool:
    """Terminalize mandatory preparation unless cancellation already won."""
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    with _queue_condition, _lifecycle_lock:
        if (
            is_cancel_requested(job)
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return False
        if job.get("status") not in {
            "preparing", "waiting_for_plan_approval",
        }:
            return False
        candidate = _copy_job_for_transition(job)
        candidate.update(updates)
        candidate["status"] = "failed"
        candidate["plan_review_required"] = False
        candidate["plan_review_terms_required"] = False
        candidate["plan_review_deadline"] = None
        if candidate.get("resource_intent") in _RESOURCE_INTENTS:
            candidate["resource_state"] = "released"
            candidate["preemption_mode"] = PREEMPTION_MODE_NONE
        candidate.setdefault("finished_at", time.time())
        _append_job_event_unlocked(candidate, status="failed", **updates)
        _persist_prospective_unlocked(
            "preparation_failed",
            jobs=(candidate,),
            global_state=_global_state_unlocked(
                replacements={id(job): candidate},
            ),
        )
        if (
            is_cancel_requested(job)
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return False
        _publish_job_unlocked(job, candidate)
        _queue_waiters.pop(id(job), None)
        _queue_condition.notify_all()
        return True


def try_start(
    job: MutableMapping[str, Any],
    *,
    generation_lock: threading.Lock | None = None,
    poll_interval: float = 0.1,
    expected_execution_attempt: int | None = None,
    block_on_persistence_failure: bool = False,
    **updates: Any,
) -> bool:
    """Atomically move an admitted queued job to running.

    When an admitted job is held or the queue is paused before this transition,
    relinquish its generation slot and re-enter scheduler admission.  Callers
    that own a generation slot must pass ``generation_lock`` so that a hold
    racing the acquire/start boundary cannot leak through into model work.
    """
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    while True:
        with _queue_condition, _lifecycle_lock:
            if is_cancel_requested(job):
                candidate = _copy_job_for_transition(job)
                candidate["status"] = "cancelled"
                candidate["message"] = "Cancelled"
                if candidate.get("resource_intent") in _RESOURCE_INTENTS:
                    candidate["resource_state"] = "released"
                    candidate["preemption_mode"] = PREEMPTION_MODE_NONE
                _persist_prospective_unlocked(
                    "start_cancelled",
                    jobs=(candidate,),
                    global_state=_global_state_unlocked(
                        replacements={id(job): candidate},
                    ),
                )
                _publish_job_unlocked(job, candidate)
                _queue_waiters.pop(id(job), None)
                _queue_condition.notify_all()
                return False
            if job.get("status") != "queued":
                return False
            if not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            ):
                return False
            admission_blocked = bool(
                job.get("queue_held", False) or _queue_paused
            )
            if not admission_blocked:
                candidate = _copy_job_for_transition(job)
                candidate.update(updates)
                candidate["status"] = "running"
                if candidate.get("resource_intent") in _RESOURCE_INTENTS:
                    candidate["resource_state"] = "running"
                candidate.setdefault("started_at", time.time())
                candidate.setdefault("phase_started_at", candidate["started_at"])
                _append_job_event_unlocked(candidate, status="running", **updates)
                try:
                    _persist_prospective_unlocked(
                        "start",
                        jobs=(candidate,),
                        global_state=_global_state_unlocked(
                            replacements={id(job): candidate},
                        ),
                    )
                except Exception:
                    if not block_on_persistence_failure:
                        raise
                    block_resource_admission_failure(job)
                    return False
                _publish_job_unlocked(job, candidate)
                _queue_condition.notify_all()
                return True
            if generation_lock is None or not job.get(
                "_generation_slot_owned", False,
            ):
                return False
            job["_generation_slot_owned"] = False
            generation_lock.release()
            _queue_condition.notify_all()

        acquired = acquire_generation_slot(
            generation_lock, job, poll_interval=poll_interval,
        )
        if not acquired:
            return False
        with _queue_condition, _lifecycle_lock:
            job["_generation_slot_owned"] = True


def try_requeue(job: MutableMapping[str, Any], **updates: Any) -> bool:
    """Return a multi-phase job to queued unless cancellation won first."""
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    with _queue_condition, _lifecycle_lock:
        if is_cancel_requested(job):
            candidate = _copy_job_for_transition(job)
            candidate["status"] = "cancelled"
            candidate["message"] = "Cancelled"
            if candidate.get("resource_intent") in _RESOURCE_INTENTS:
                candidate["resource_state"] = "released"
                candidate["preemption_mode"] = PREEMPTION_MODE_NONE
            _persist_prospective_unlocked(
                "requeue_cancelled",
                jobs=(candidate,),
                global_state=_global_state_unlocked(
                    replacements={id(job): candidate},
                ),
            )
            _publish_job_unlocked(job, candidate)
            _queue_waiters.pop(id(job), None)
            _queue_condition.notify_all()
            return False
        if job.get("status") != "running":
            return False
        candidate = _copy_job_for_transition(job)
        candidate.update(updates)
        candidate["status"] = "queued"
        if candidate.get("resource_intent") in _RESOURCE_INTENTS:
            candidate["resource_state"] = "queued"
        _append_job_event_unlocked(candidate, status="queued", **updates)
        _persist_prospective_unlocked(
            "requeue",
            jobs=(candidate,),
            global_state=_global_state_unlocked(
                replacements={id(job): candidate},
            ),
        )
        _publish_job_unlocked(job, candidate)
        _queue_condition.notify_all()
        return True


def update_job(
    job: MutableMapping[str, Any],
    *,
    expected_execution_attempt: int | None = None,
    **updates: Any,
) -> bool:
    """Update a live job without replacing a terminal/cancelled message."""
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    with _lifecycle_lock:
        if (
            is_cancel_requested(job)
            or job.get("status") != "running"
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return False
        replacement_outputs = updates.pop("output_files", None)
        if replacement_outputs is not None:
            candidate = _copy_job_for_transition(job)
            next_phase = updates.get("phase")
            if next_phase is not None and next_phase != candidate.get("phase"):
                candidate["phase_started_at"] = time.time()
            candidate.update(updates)
            _replace_job_outputs_unlocked(candidate, replacement_outputs)
            _append_job_event_unlocked(candidate, **updates)
            _persist_prospective_unlocked("replace_outputs", jobs=(candidate,))
            _publish_job_unlocked(job, candidate)
            return True
        next_phase = updates.get("phase")
        if next_phase is not None and next_phase != job.get("phase"):
            job["phase_started_at"] = time.time()
        job.update(updates)
        _append_job_event_unlocked(job, **updates)
        return True


def register_abort_state(
    job: MutableMapping[str, Any],
    job_id: str,
    active_states: MutableMapping[str, MutableMapping[str, Any]],
    state: MutableMapping[str, Any],
    *,
    interrupt_model: Callable[[], None] | None = None,
) -> bool:
    """Register a worker's abort dictionary unless cancellation won first.

    Dummy states for non-Wan tools still receive ``abort=True`` but have no
    interrupt callback. Callback ownership is tracked separately by state
    identity so an old worker cannot interrupt or unregister a newer phase.
    """
    with _lifecycle_lock:
        state.setdefault("abort", False)
        if is_cancel_requested(job) or job.get("status") != "running":
            state["abort"] = True
            return False
        active_states[job_id] = state
        _registrations[job_id] = (job, state, interrupt_model)
        return True


def unregister_abort_state(
    job_id: str,
    active_states: MutableMapping[str, MutableMapping[str, Any]],
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Remove only the abort state owned by the finishing worker."""
    with _lifecycle_lock:
        current = active_states.get(job_id)
        if current is not None and (state is None or current is state):
            active_states.pop(job_id, None)
        registration = _registrations.get(job_id)
        if registration is not None and (
            state is None or registration[1] is state
        ):
            _registrations.pop(job_id, None)


def request_cancel(
    job: MutableMapping[str, Any],
    *,
    job_id: str | None = None,
    active_states: MutableMapping[str, MutableMapping[str, Any]] | None = None,
) -> CancelResult:
    """Atomically cancel lifecycle state and remove scheduler membership."""
    with _queue_condition, _lifecycle_lock:
        status = job.get("status")
        if status in TERMINAL_STATUSES:
            return CancelResult(False, False, False)

        was_running = status == "running"
        candidate = _copy_job_for_transition(job)
        candidate["cancel_requested"] = True
        candidate["message"] = "Cancelled"
        candidate["status"] = "cancelled"
        candidate["plan_review_required"] = False
        candidate["plan_review_terms_required"] = False
        candidate["plan_review_deadline"] = None
        if candidate.get("resource_intent") in _RESOURCE_INTENTS:
            candidate["resource_state"] = "released"
            candidate["preemption_mode"] = PREEMPTION_MODE_NONE
        _append_job_event_unlocked(
            candidate, status="cancelled", message="Cancelled",
        )
        _persist_prospective_unlocked(
            "cancel",
            jobs=(candidate,),
            global_state=_global_state_unlocked(
                replacements={id(job): candidate},
            ),
        )

        # Publish the durable winner before invoking a model callback. Some
        # wrappers re-enter lifecycle helpers from their interrupt hook; they
        # must observe cancellation, never the prior running state.
        _publish_job_unlocked(job, candidate)
        _queue_waiters.pop(id(job), None)

        abort_signalled = False
        state = active_states.get(job_id) if active_states is not None and job_id else None
        registration = _registrations.get(job_id) if job_id else None
        if (
            was_running
            and state is not None
            and registration is not None
            and registration[0] is job
            and registration[1] is state
        ):
            state["abort"] = True
            abort_signalled = True
            if registration[2] is not None:
                try:
                    registration[2]()
                except Exception:
                    pass
        _queue_condition.notify_all()

        return CancelResult(True, was_running, abort_signalled)


def finish_job(
    job: MutableMapping[str, Any],
    status: str,
    *,
    expected_execution_attempt: int | None = None,
    **updates: Any,
) -> bool:
    """Publish a completed/failed result unless cancellation already won."""
    if status not in {"completed", "failed"}:
        raise ValueError(f"Invalid terminal job status: {status}")
    if "status" in updates:
        raise ValueError("status must be changed through a lifecycle transition")
    with _queue_condition, _lifecycle_lock:
        if is_cancel_requested(job):
            candidate = _copy_job_for_transition(job)
            candidate["status"] = "cancelled"
            candidate["message"] = "Cancelled"
            if candidate.get("resource_intent") in _RESOURCE_INTENTS:
                candidate["resource_state"] = "released"
                candidate["preemption_mode"] = PREEMPTION_MODE_NONE
            _persist_prospective_unlocked(
                "finish_cancelled",
                jobs=(candidate,),
                global_state=_global_state_unlocked(
                    replacements={id(job): candidate},
                ),
            )
            _publish_job_unlocked(job, candidate)
            _queue_waiters.pop(id(job), None)
            _queue_condition.notify_all()
            return False
        if (
            job.get("status") != "running"
            or not _valid_expected_execution_attempt(
                job, expected_execution_attempt,
            )
        ):
            return False
        candidate = _copy_job_for_transition(job)
        replacement_outputs = updates.pop("output_files", None)
        candidate.update(updates)
        if replacement_outputs is not None:
            _replace_job_outputs_unlocked(candidate, replacement_outputs)
        candidate["status"] = status
        if candidate.get("resource_intent") in _RESOURCE_INTENTS:
            candidate["resource_state"] = "released"
            candidate["preemption_mode"] = PREEMPTION_MODE_NONE
        _append_job_event_unlocked(candidate, status=status, **updates)
        _persist_prospective_unlocked(
            "finish",
            jobs=(candidate,),
            global_state=_global_state_unlocked(
                replacements={id(job): candidate},
            ),
        )
        _publish_job_unlocked(job, candidate)
        _queue_waiters.pop(id(job), None)
        _queue_condition.notify_all()
        return True


def acquire_generation_slot(
    generation_lock: threading.Lock,
    job: MutableMapping[str, Any],
    *,
    poll_interval: float = 0.1,
) -> bool:
    """Wait for ordered GPU admission while supporting hold/pause/cancel."""
    global _queue_sequence
    waiter_key = id(job)
    with _queue_condition:
        with _lifecycle_lock:
            existing = _queue_waiters.get(waiter_key)
            if existing is not None:
                waiter_sequence = existing[0]
                job.pop("_queue_restore_sequence", None)
            else:
                try:
                    restored_sequence = int(job.get("_queue_restore_sequence", 0) or 0)
                except (TypeError, ValueError):
                    restored_sequence = 0
                prospective_sequence = max(_queue_sequence + 1, restored_sequence)
                waiter_sequence = restored_sequence or prospective_sequence
                global_state = _global_state_unlocked(
                    additions=((waiter_sequence, job),),
                )
                _persist_prospective_unlocked(
                    "queue_register", global_state=global_state,
                )
                _queue_sequence = prospective_sequence
                job.pop("_queue_restore_sequence", None)
                job.setdefault("_queue_enqueued_monotonic", time.monotonic())
                _queue_waiters[waiter_key] = (waiter_sequence, job)
        try:
            while not is_cancel_requested(job):
                selected, reason, skipped = _select_next_waiter(
                    _eligible_queue_entries(),
                )
                is_next = bool(selected and selected[1] is job)
                if is_next and not _queue_paused and generation_lock.acquire(blocking=False):
                    with _lifecycle_lock:
                        if (
                            is_cancel_requested(job)
                            or job.get("status") != "queued"
                            or job.get("queue_held", False)
                        ):
                            generation_lock.release()
                            if is_cancel_requested(job):
                                return False
                            continue
                        _record_queue_admission(
                            job, reason or "queue_order", skipped,
                        )
                        _queue_waiters.pop(waiter_key, None)
                        return True
                _queue_condition.wait(timeout=max(0.01, poll_interval))
            return False
        finally:
            _queue_waiters.pop(waiter_key, None)


def yield_generation_slot_after_output(
    generation_lock: threading.Lock,
    job: MutableMapping[str, Any],
    *,
    poll_interval: float = 0.1,
) -> bool:
    """Cooperatively yield a held/paused running job at a safe output edge.

    The caller must invoke this only after its model task has completely
    returned.  The job becomes queued while waiting, so ordinary local-first
    scheduling and cancellation continue to apply.  A per-job hold requires
    that job to be resumed; the global pause requires the queue to be resumed.
    """
    global _queue_paused, _pause_after_current
    with _queue_condition, _lifecycle_lock:
        per_job_hold = bool(job.get("hold_after_output", False))
        global_pause = bool(_pause_after_current)
        if not per_job_hold and not global_pause:
            return True
        if job.get("status") != "running" or is_cancel_requested(job):
            return False
        candidate = _copy_job_for_transition(job)
        prospective_paused = _queue_paused
        prospective_after = _pause_after_current
        if global_pause:
            prospective_paused = True
            prospective_after = False
        candidate["status"] = "queued"
        if candidate.get("resource_intent") in _RESOURCE_INTENTS:
            candidate["resource_state"] = "queued"
        candidate["queue_held"] = per_job_hold
        candidate["hold_after_output"] = False
        candidate["message"] = (
            "Held after completed output"
            if per_job_hold
            else "Queue paused after completed output"
        )
        candidate["_generation_slot_owned"] = False
        _append_job_event_unlocked(candidate)
        _persist_prospective_unlocked(
            "yield_after_output",
            jobs=(candidate,),
            global_state=_global_state_unlocked(
                paused=prospective_paused,
                pause_after_current=prospective_after,
                replacements={id(job): candidate},
            ),
        )
        _publish_job_unlocked(job, candidate)
        _queue_paused = prospective_paused
        _pause_after_current = prospective_after
        generation_lock.release()
        _queue_condition.notify_all()

    acquired = acquire_generation_slot(
        generation_lock, job, poll_interval=poll_interval,
    )
    if not acquired:
        return False
    with _queue_condition, _lifecycle_lock:
        job["_generation_slot_owned"] = True
    return try_start(
        job,
        generation_lock=generation_lock,
        poll_interval=poll_interval,
        message="Resuming after output boundary…",
    )


@contextmanager
def generation_slot(
    generation_lock: threading.Lock,
    job: MutableMapping[str, Any],
    *,
    poll_interval: float = 0.1,
    block_on_persistence_failure: bool = False,
) -> Iterator[bool]:
    """Context manager form of :func:`acquire_generation_slot`."""
    try:
        acquired = acquire_generation_slot(
            generation_lock, job, poll_interval=poll_interval,
        )
    except Exception:
        if not block_on_persistence_failure:
            raise
        block_resource_admission_failure(job)
        acquired = False
    if acquired:
        with _queue_condition, _lifecycle_lock:
            job["_generation_slot_owned"] = True
    try:
        yield acquired
    finally:
        global _queue_paused, _pause_after_current
        with _queue_condition, _lifecycle_lock:
            owns_slot = bool(job.get("_generation_slot_owned", False))
            if acquired and owns_slot:
                prospective_paused = bool(
                    _pause_after_current or job.get("pause_queue_after", False)
                )
                persistence_succeeded = False
                try:
                    if prospective_paused:
                        _persist_prospective_unlocked(
                            "slot_pause_after_current",
                            global_state=_global_state_unlocked(
                                paused=True, pause_after_current=False,
                            ),
                        )
                    persistence_succeeded = True
                finally:
                    # Slot ownership is runtime-only and must never leak when
                    # the durable pause write fails. Preserve the prior global
                    # controls unless the prospective write succeeded.
                    job.pop("_generation_slot_owned", None)
                    generation_lock.release()
                    if prospective_paused and persistence_succeeded:
                        _queue_paused = True
                        _pause_after_current = False
                    _queue_condition.notify_all()
