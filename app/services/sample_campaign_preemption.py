"""Low-frequency launch policy for yielding one active campaign sample.

The coordinator is deliberately effect-free apart from injected callbacks.  It
does not inspect process names, signal processes, mutate jobs, or own retry
state.  The lifecycle callback remains the sole authority for durably
requesting an abort of the exact registered execution attempt.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any


MINIMUM_POLL_INTERVAL_SECONDS = 15.0
SAMPLE_JOB_KIND = "sample_campaign_generation"
SAMPLE_QUEUE_CLASS = "background_sample"
SAMPLE_QUEUE_PRIORITY = -1000


@dataclass(frozen=True, slots=True)
class SamplePreemptionDecision:
    requested: bool
    reason: str
    job_id: str | None = None
    execution_attempt: int | None = None


def _active_sample(
    jobs: Sequence[Mapping[str, Any]],
    active_states: Mapping[str, MutableMapping[str, Any]],
) -> tuple[Mapping[str, Any], MutableMapping[str, Any], int] | None:
    candidates: list[
        tuple[Mapping[str, Any], MutableMapping[str, Any], int]
    ] = []
    for job in jobs:
        job_id = job.get("id") if isinstance(job, Mapping) else None
        attempt = job.get("execution_attempt") if isinstance(job, Mapping) else None
        state = active_states.get(job_id) if isinstance(job_id, str) else None
        if (
            not isinstance(job, Mapping)
            or not isinstance(job_id, str)
            or not job_id
            or job.get("kind") != SAMPLE_JOB_KIND
            or job.get("queue_class") != SAMPLE_QUEUE_CLASS
            or job.get("queue_priority") != SAMPLE_QUEUE_PRIORITY
            or job.get("status") != "running"
            or job.get("queue_held") is not False
            or job.get("resource_state") != "running"
            or job.get("resource_intent") != "generation"
            or job.get("resource_execution") != "standard"
            or job.get("preemption_mode") != "none"
            or job.get("cancel_requested") is True
            or type(attempt) is not int
            or attempt < 1
            or not isinstance(state, MutableMapping)
            or state.get("abort") is not False
        ):
            continue
        candidates.append((job, state, attempt))
    return candidates[0] if len(candidates) == 1 else None


class SampleCampaignPreemptionCoordinator:
    """Poll exact process-local state and request only positively gated yield."""

    def __init__(
        self,
        *,
        jobs: Callable[[], Sequence[Mapping[str, Any]]],
        active_states: Callable[[], Mapping[str, MutableMapping[str, Any]]],
        urgent_ordinary_work_present: Callable[[], bool],
        capture_foreign_significance: Callable[[], Any],
        request_preemption: Callable[..., Any],
        poll_interval_seconds: float = MINIMUM_POLL_INTERVAL_SECONDS,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not math.isfinite(poll_interval_seconds)
            or poll_interval_seconds < MINIMUM_POLL_INTERVAL_SECONDS
        ):
            raise ValueError("Sample preemption poll interval is too short")
        callbacks = (
            jobs,
            active_states,
            urgent_ordinary_work_present,
            capture_foreign_significance,
            request_preemption,
            monotonic_clock,
        )
        if any(not callable(callback) for callback in callbacks):
            raise ValueError("Sample preemption callback is invalid")
        self._jobs = jobs
        self._active_states = active_states
        self._urgent = urgent_ordinary_work_present
        self._capture = capture_foreign_significance
        self._request = request_preemption
        self.poll_interval_seconds = float(poll_interval_seconds)
        self._monotonic = monotonic_clock

    def poll_once(self) -> SamplePreemptionDecision:
        try:
            selected = _active_sample(
                tuple(self._jobs()), self._active_states(),
            )
        except Exception:
            return SamplePreemptionDecision(False, "active_state_unknown")
        if selected is None:
            return SamplePreemptionDecision(False, "no_active_sample")
        job, _state, attempt = selected
        job_id = str(job["id"])
        try:
            urgent = self._urgent() is True
        except Exception:
            urgent = False
        reason = "urgent_ordinary_work" if urgent else ""
        if not urgent:
            try:
                significance = self._capture()
                external = (
                    getattr(significance, "known", None) is True
                    and getattr(significance, "significant", None) is True
                )
            except Exception:
                external = False
            if not external:
                return SamplePreemptionDecision(
                    False, "no_positive_preemption_evidence", job_id, attempt,
                )
            reason = "significant_external_gpu_work"
        try:
            result = self._request(
                job,
                job_id=job_id,
                expected_execution_attempt=attempt,
            )
        except Exception:
            return SamplePreemptionDecision(
                False, "preemption_request_failed", job_id, attempt,
            )
        changed = getattr(result, "changed", None) is True
        return SamplePreemptionDecision(
            changed,
            reason if changed else "preemption_request_rejected",
            job_id,
            attempt,
        )

    def run(self, stop_event: threading.Event) -> None:
        if (
            not callable(getattr(stop_event, "is_set", None))
            or not callable(getattr(stop_event, "wait", None))
        ):
            raise ValueError("Sample preemption stop event is invalid")
        next_poll = self._monotonic()
        while not stop_event.is_set():
            now = self._monotonic()
            delay = max(0.0, next_poll - now)
            if stop_event.wait(delay):
                return
            self.poll_once()
            next_poll = max(
                next_poll + self.poll_interval_seconds,
                self._monotonic() + self.poll_interval_seconds,
            )


__all__ = [
    "MINIMUM_POLL_INTERVAL_SECONDS",
    "SampleCampaignPreemptionCoordinator",
    "SamplePreemptionDecision",
]
