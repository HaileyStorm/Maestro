"""Bounded public projection for exact comparative sample pairs.

The queue view is deliberately content-free.  Callers authenticate and load
the sealed private manifests, while this module validates reciprocal runtime
linkage and projects only the already-public pair contract plus closed job
state.  Evaluation receipts are intentionally absent until their durable
store exists, so completed outputs remain ``outputs_unbound`` rather than
becoming review evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
import re
from typing import Any

from services.sample_campaign import (
    ComparativePairManifest,
    EvidenceClass,
    MAX_SEED,
    pair_manifest_digest,
    public_pair_projection,
)


QUEUE_SCHEMA_VERSION = 1
MAX_PUBLIC_PAIRS = 100
SAMPLE_JOB_KIND = "sample_campaign_generation"
SAMPLE_QUEUE_CLASS = "background_sample"
SAMPLE_QUEUE_PRIORITY = -1000
SAMPLE_LINKAGE_SCHEMA = 1

_SEQUENTIAL_ACTIVE_BOUNDARIES = frozenset({
    (("queued", True), ("queued", True)),
    (("queued", False), ("queued", True)),
    (("running", False), ("queued", True)),
    (("completed", False), ("queued", True)),
    (("completed", False), ("queued", False)),
    (("completed", False), ("running", False)),
    (("completed", False), ("completed", False)),
})

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}\Z")
_RECOVERY_STATES = frozenset({
    "sample_campaign_held",
    "sample_campaign_released",
    "terminal",
    "blocked",
})
_RESOURCE_STATES = frozenset({
    "queued",
    "running",
    "preemption_requested",
    "released",
    "blocked",
})


def _linkage(job: Mapping[str, Any]) -> Mapping[str, Any] | None:
    cursor = job.get("recovery_cursor")
    value = cursor.get("sample_campaign") if isinstance(cursor, Mapping) else None
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "pair_id", "pair_manifest_digest", "arm", "peer_job_id",
    }:
        return None
    return value


def _identity(
    job: Mapping[str, Any],
    runtime_key: str,
    durable_key: str,
) -> str | None:
    runtime = job.get(runtime_key)
    durable = job.get(durable_key)
    values = [
        value for value in (runtime, durable)
        if isinstance(value, str) and value
    ]
    if not values or any(value != values[0] for value in values[1:]):
        return None
    return values[0]


def exact_reciprocal_sample_pairs(
    jobs: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Mapping[str, Any], Mapping[str, Any]], ...]:
    """Return only exact reciprocal two-arm groups."""

    if isinstance(jobs, (str, bytes)) or not isinstance(jobs, Sequence):
        return ()
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    job_id_counts: dict[str, int] = {}
    for job in jobs:
        if not isinstance(job, Mapping) or job.get("kind") != SAMPLE_JOB_KIND:
            continue
        job_id = job.get("id")
        if isinstance(job_id, str):
            job_id_counts[job_id] = job_id_counts.get(job_id, 0) + 1
        linkage = _linkage(job)
        if linkage is None:
            continue
        pair_id = linkage.get("pair_id")
        digest = linkage.get("pair_manifest_digest")
        if (
            linkage.get("schema") != SAMPLE_LINKAGE_SCHEMA
            or not isinstance(pair_id, str)
            or not pair_id
            or not isinstance(digest, str)
            or _DIGEST_RE.fullmatch(digest) is None
        ):
            continue
        grouped.setdefault((pair_id, digest), []).append(job)

    valid = []
    for (_pair_id, _digest), candidates in grouped.items():
        if len(candidates) != 2:
            continue
        by_arm: dict[str, Mapping[str, Any]] = {}
        for candidate in candidates:
            linkage = _linkage(candidate)
            arm = linkage.get("arm") if linkage is not None else None
            if arm not in {"maestro", "control"} or arm in by_arm:
                by_arm = {}
                break
            by_arm[arm] = candidate
        if set(by_arm) != {"maestro", "control"}:
            continue
        maestro = by_arm["maestro"]
        control = by_arm["control"]
        maestro_link = _linkage(maestro) or {}
        control_link = _linkage(control) or {}
        maestro_id = maestro.get("id")
        control_id = control.get("id")
        owner = _identity(maestro, "_recovery_owner_digest", "owner_principal")
        project = _identity(
            maestro, "_recovery_project_digest", "project_instance",
        )
        if (
            not isinstance(maestro_id, str)
            or _SAFE_JOB_ID_RE.fullmatch(maestro_id) is None
            or job_id_counts.get(maestro_id) != 1
            or not isinstance(control_id, str)
            or _SAFE_JOB_ID_RE.fullmatch(control_id) is None
            or job_id_counts.get(control_id) != 1
            or maestro_id == control_id
            or maestro_link.get("peer_job_id") != control_id
            or control_link.get("peer_job_id") != maestro_id
            or owner is None
            or owner != _identity(
                control, "_recovery_owner_digest", "owner_principal",
            )
            or project is None
            or project != _identity(
                control, "_recovery_project_digest", "project_instance",
            )
            or not isinstance(maestro.get("workspace"), str)
            or not maestro.get("workspace")
            or maestro.get("workspace") != control.get("workspace")
            or any(
                candidate.get("queue_class") != SAMPLE_QUEUE_CLASS
                or candidate.get("queue_priority") != SAMPLE_QUEUE_PRIORITY
                or type(candidate.get("queue_held")) is not bool
                for candidate in candidates
            )
        ):
            continue
        valid.append((maestro, control))
    return tuple(valid)


def valid_sample_pair_lifecycle(
    maestro: Mapping[str, Any],
    control: Mapping[str, Any],
    *,
    include_terminal_failures: bool = False,
) -> bool:
    """Validate the one-arm-at-a-time sequence, with bounded failure states."""

    states = (
        (
            str(maestro.get("status") or "").casefold(),
            maestro.get("queue_held") is True,
        ),
        (
            str(control.get("status") or "").casefold(),
            control.get("queue_held") is True,
        ),
    )
    if states in _SEQUENTIAL_ACTIVE_BOUNDARIES:
        return True
    if not include_terminal_failures:
        return False
    maestro_status, control_status = states[0][0], states[1][0]
    return "failed" in {maestro_status, control_status}


def _closed_recovery_state(value: object) -> str | None:
    if value is None:
        return None
    if value in _RECOVERY_STATES:
        return str(value)
    return "blocked"


def _closed_resource_state(value: object) -> str:
    return str(value) if value in _RESOURCE_STATES else "blocked"


def _arm_projection(job: Mapping[str, Any], arm: str) -> dict[str, Any]:
    raw_status = str(job.get("status") or "").casefold()
    held = job.get("queue_held")
    recovery = _closed_recovery_state(job.get("recovery_state"))
    resource = _closed_resource_state(job.get("resource_state"))
    progress = job.get("progress")
    outputs = job.get("output_files")
    valid_outputs = bool(
        isinstance(outputs, list)
        and len(outputs) <= 1_000
        and all(isinstance(item, str) and item for item in outputs)
    )
    output_count = len(outputs) if valid_outputs else 0
    public_progress = (
        min(100.0, max(0.0, float(progress)))
        if type(progress) in {int, float} and math.isfinite(float(progress))
        else 0.0
    )
    status = "failed"
    if raw_status == "queued" and resource == "queued" and (
        held is True
        and recovery in {
            None, "sample_campaign_held", "sample_campaign_released",
        }
        or held is False and recovery == "sample_campaign_released"
    ):
        status = "queued"
    elif (
        raw_status == "running"
        and held is False
        and recovery == "sample_campaign_released"
        and resource in {"running", "preemption_requested"}
    ):
        status = "running"
    elif (
        raw_status == "completed"
        and held is False
        and recovery == "terminal"
        and resource == "released"
        and output_count > 0
    ):
        status = "completed"
    if status == "failed" and recovery not in {"terminal", "blocked"}:
        recovery = "blocked"
    if status == "failed" and resource not in {"released", "blocked"}:
        resource = "blocked"
    return {
        "job_id": str(job["id"]),
        "arm": arm,
        "status": status,
        "queue_held": held,
        "recovery_state": recovery,
        "resource_state": resource,
        "progress": public_progress,
        "output_available": output_count > 0,
        "output_count": output_count,
    }


def _queue_state(
    maestro: Mapping[str, Any],
    control: Mapping[str, Any],
) -> str:
    statuses = (maestro["status"], control["status"])
    if "failed" in statuses:
        return "blocked"
    if statuses == ("completed", "completed"):
        return "outputs_unbound"
    if statuses == ("queued", "queued") and maestro["queue_held"] is True:
        return "held"
    if statuses == ("completed", "queued") and control["queue_held"] is True:
        return "held"
    return "running_arm"


def project_sample_campaign_queue(
    jobs: Sequence[Mapping[str, Any]],
    *,
    load_pair_manifest: Callable[[Mapping[str, Any]], ComparativePairManifest],
    limit: int = MAX_PUBLIC_PAIRS,
) -> dict[str, Any]:
    """Build the closed queue response; malformed/private groups disappear."""

    if (
        not callable(load_pair_manifest)
        or type(limit) is not int
        or not 1 <= limit <= MAX_PUBLIC_PAIRS
    ):
        raise ValueError("Sample campaign queue projection is invalid.")
    if isinstance(jobs, (str, bytes)) or not isinstance(jobs, Sequence):
        return {"schema_version": QUEUE_SCHEMA_VERSION, "pairs": []}
    pair_id_digests: dict[str, set[str]] = {}
    for job in jobs:
        if not isinstance(job, Mapping) or job.get("kind") != SAMPLE_JOB_KIND:
            continue
        linkage = _linkage(job)
        pair_id = linkage.get("pair_id") if linkage is not None else None
        digest = (
            linkage.get("pair_manifest_digest")
            if linkage is not None else None
        )
        if (
            linkage is not None
            and linkage.get("schema") == SAMPLE_LINKAGE_SCHEMA
            and isinstance(pair_id, str)
            and pair_id
            and isinstance(digest, str)
            and _DIGEST_RE.fullmatch(digest) is not None
        ):
            pair_id_digests.setdefault(pair_id, set()).add(digest)
    conflicting_pair_ids = {
        pair_id for pair_id, digests in pair_id_digests.items()
        if len(digests) != 1
    }
    groups = exact_reciprocal_sample_pairs(jobs)
    pairs = []
    for maestro, control in groups:
        try:
            linkage = _linkage(maestro) or {}
            if str(linkage.get("pair_id") or "") in conflicting_pair_ids:
                continue
            maestro_pair = load_pair_manifest(maestro)
            control_pair = load_pair_manifest(control)
            if (
                not isinstance(maestro_pair, ComparativePairManifest)
                or maestro_pair != control_pair
            ):
                continue
            if (
                maestro_pair.pair_id != linkage.get("pair_id")
                or pair_manifest_digest(maestro_pair)
                    != linkage.get("pair_manifest_digest")
            ):
                continue
            pair_projection = public_pair_projection(maestro_pair)
            shared_generation = pair_projection.get("shared_generation")
            seed = (
                shared_generation.get("seed")
                if isinstance(shared_generation, dict) else None
            )
            if type(seed) is not int or not 0 <= seed <= MAX_SEED:
                continue
            # JSON numbers cannot preserve the full uint64 range in browser
            # consumers. Keep the shared projection unchanged globally and
            # use one canonical decimal string on this queue wire contract.
            shared_generation["seed"] = str(seed)
            if pair_projection.get("evaluation") != {
                "evidence_class": EvidenceClass.MANIFEST_ONLY.value,
                "vlm_verdict": "not_reviewed",
                "human_verdict": "not_reviewed",
            }:
                continue
            maestro_public = _arm_projection(maestro, "maestro")
            control_public = _arm_projection(control, "control")
            if not valid_sample_pair_lifecycle(
                maestro_public,
                control_public,
                include_terminal_failures=True,
            ):
                continue
            pairs.append({
                "pair": pair_projection,
                "queue_state": _queue_state(maestro_public, control_public),
                "arms": [maestro_public, control_public],
            })
        except Exception:
            continue
    pairs.sort(key=lambda item: (
        str(item["pair"].get("case_id") or ""),
        str(item["pair"].get("pair_id") or ""),
    ))
    return {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "pairs": pairs[:limit],
    }


__all__ = [
    "MAX_PUBLIC_PAIRS",
    "QUEUE_SCHEMA_VERSION",
    "exact_reciprocal_sample_pairs",
    "project_sample_campaign_queue",
    "valid_sample_pair_lifecycle",
]
