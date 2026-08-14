"""Fail-closed release policy for already registered sample-campaign pairs.

This module deliberately owns no HTTP, telemetry, persistence, thread, or
generation-engine implementation.  The launch boundary supplies those effects
after authenticating the local owner.  The coordinator selects at most one arm,
requires a fresh readiness decision, durably clears its hold, and restores the
hold if worker publication cannot be proven.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


SAMPLE_JOB_KIND = "sample_campaign_generation"
SAMPLE_QUEUE_CLASS = "background_sample"
SAMPLE_QUEUE_PRIORITY = -1000
SAMPLE_LINKAGE_SCHEMA = 1


class SampleCampaignReleaseError(ValueError):
    """The requested pair cannot be released without weakening its contract."""


@dataclass(frozen=True, slots=True)
class SampleReleaseResult:
    status: str
    reason: str
    pair_id: str
    arm: str | None = None
    job_id: str | None = None

    def public_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "reason": self.reason,
            "pair_id": self.pair_id,
        }
        if self.arm is not None:
            payload["arm"] = self.arm
        if self.job_id is not None:
            payload["job_id"] = self.job_id
        return payload


def _linkage(job: Mapping[str, Any]) -> Mapping[str, Any] | None:
    cursor = job.get("recovery_cursor")
    value = cursor.get("sample_campaign") if isinstance(cursor, Mapping) else None
    return value if isinstance(value, Mapping) else None


def _pair_jobs(
    pair_id: str,
    jobs: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(pair_id, str) or not pair_id:
        raise SampleCampaignReleaseError("Campaign pair identity is invalid.")
    candidates = [
        job for job in jobs
        if isinstance(job, Mapping)
        and job.get("kind") == SAMPLE_JOB_KIND
        and (_linkage(job) or {}).get("pair_id") == pair_id
    ]
    if len(candidates) != 2:
        raise SampleCampaignReleaseError("Campaign pair is incomplete.")
    by_arm = {(_linkage(job) or {}).get("arm"): job for job in candidates}
    if set(by_arm) != {"maestro", "control"}:
        raise SampleCampaignReleaseError("Campaign pair arms are invalid.")
    maestro, control = by_arm["maestro"], by_arm["control"]
    maestro_link = _linkage(maestro) or {}
    control_link = _linkage(control) or {}
    shared = ("pair_manifest_digest", "schema")
    digest = maestro_link.get("pair_manifest_digest")
    if (
        maestro.get("id") == control.get("id")
        or maestro_link.get("peer_job_id") != control.get("id")
        or control_link.get("peer_job_id") != maestro.get("id")
        or any(maestro_link.get(key) != control_link.get(key) for key in shared)
        or maestro_link.get("schema") != SAMPLE_LINKAGE_SCHEMA
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or any(
            not isinstance(maestro.get(field), str)
            or not maestro.get(field)
            or maestro.get(field) != control.get(field)
            for field in (
                "_recovery_owner_digest", "_recovery_project_digest", "workspace",
            )
        )
        or any(
            job.get("queue_class") != SAMPLE_QUEUE_CLASS
            or job.get("kind") != SAMPLE_JOB_KIND
            or job.get("queue_priority") != SAMPLE_QUEUE_PRIORITY
            for job in candidates
        )
    ):
        raise SampleCampaignReleaseError("Campaign pair linkage is invalid.")
    return maestro, control


def _next_arm(
    maestro: Mapping[str, Any],
    control: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any]] | None:
    maestro_status = str(maestro.get("status") or "").casefold()
    control_status = str(control.get("status") or "").casefold()
    if (
        maestro_status == "queued"
        and maestro.get("queue_held") is True
        and control_status == "queued"
        and control.get("queue_held") is True
    ):
        return "maestro", maestro
    if (
        maestro_status == "completed"
        and control_status == "queued"
        and control.get("queue_held") is True
    ):
        return "control", control
    if maestro_status == "completed" and control_status == "completed":
        return None
    raise SampleCampaignReleaseError(
        "Campaign pair is not at a safe sequential release boundary."
    )


class SampleCampaignReleaseCoordinator:
    """Release one reciprocal arm only after every fail-closed gate passes."""

    def __init__(
        self,
        *,
        ordinary_work_present: Callable[[], bool],
        another_sample_active: Callable[[str], bool],
        validate_pair_requests: Callable[
            [Mapping[str, Any], Mapping[str, Any]], bool
        ],
        readiness: Callable[[Mapping[str, Any]], tuple[bool, str]],
        persist_hold: Callable[[Mapping[str, Any], bool, str], bool],
        force_hold: Callable[[Mapping[str, Any]], None],
        start_worker: Callable[[str], None],
    ) -> None:
        self._ordinary_work_present = ordinary_work_present
        self._another_sample_active = another_sample_active
        self._validate_pair_requests = validate_pair_requests
        self._readiness = readiness
        self._persist_hold = persist_hold
        self._force_hold = force_hold
        self._start_worker = start_worker

    def release_one(
        self,
        pair_id: str,
        jobs: Sequence[Mapping[str, Any]],
    ) -> SampleReleaseResult:
        maestro, control = _pair_jobs(pair_id, jobs)
        selected = _next_arm(maestro, control)
        if selected is None:
            return SampleReleaseResult("complete", "pair_complete", pair_id)
        arm, job = selected
        job_id = job.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise SampleCampaignReleaseError("Campaign job identity is invalid.")
        if self._ordinary_work_present():
            return SampleReleaseResult("held", "ordinary_work_waiting", pair_id)
        if self._another_sample_active(pair_id):
            return SampleReleaseResult("held", "sample_arm_active", pair_id)
        try:
            private_valid = self._validate_pair_requests(maestro, control)
        except Exception:
            private_valid = False
        if private_valid is not True:
            self._force_hold(job)
            return SampleReleaseResult("held", "private_evidence_invalid", pair_id)
        try:
            ready, reason = self._readiness(job)
        except Exception:
            ready, reason = False, "readiness_unknown"
        if ready is not True:
            self._force_hold(job)
            return SampleReleaseResult("held", str(reason or "readiness_unknown"), pair_id)
        try:
            persisted = self._persist_hold(job, False, "sample_campaign_released")
        except Exception:
            persisted = False
        if persisted is not True:
            self._force_hold(job)
            return SampleReleaseResult("held", "release_persistence_failed", pair_id)
        try:
            self._start_worker(job_id)
        except Exception:
            try:
                self._persist_hold(job, True, "sample_campaign_held")
            except Exception:
                pass
            self._force_hold(job)
            return SampleReleaseResult("held", "worker_start_failed", pair_id)
        return SampleReleaseResult("released", "worker_started", pair_id, arm, job_id)


__all__ = [
    "SAMPLE_JOB_KIND",
    "SAMPLE_QUEUE_CLASS",
    "SampleCampaignReleaseCoordinator",
    "SampleCampaignReleaseError",
    "SampleReleaseResult",
]
