"""Plan an audited, CPU-only reseal of a held H3 final sidecar.

The old descriptor remains in the durable cursor as evidence.  This module
does not make a held job runnable; final-adoption must still validate and
receipt the complete artifact graph before startup can mark it complete.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_HISTORY = 8


class FinalSidecarResealError(ValueError):
    """The requested final re-attestation is not supported by the evidence."""


def _canonical_digest(value: Any) -> str:
    try:
        payload = json.dumps(
            value, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise FinalSidecarResealError("Final recovery evidence is invalid.") from None
    return hashlib.sha256(payload).hexdigest()


def plan_h3_final_sidecar_reseal(
    snapshot: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    private: bool,
    explicit: bool,
) -> dict[str, Any]:
    """Return a CAS-ready snapshot after strict independent evidence checks.

    ``candidate`` is the final-adoption scanner's validated, content-free
    quarantined-final record.  The caller must additionally validate the
    project identity, request manifest, segment bytes, media structure, and
    stopped-service boundary before committing the returned snapshot.
    """
    if not isinstance(snapshot, Mapping) or not isinstance(candidate, Mapping):
        raise FinalSidecarResealError("Final recovery evidence is invalid.")
    unit = snapshot.get("recovery_unit")
    cursor = snapshot.get("recovery_cursor")
    artifacts = unit.get("artifacts") if isinstance(unit, dict) else None
    if (
        snapshot.get("status") != "queued"
        or snapshot.get("queue_held") is not True
        or snapshot.get("_recovery_reason_code")
            != "final_output_recovery_incomplete"
        or snapshot.get("reruns_denoise") is not False
        or snapshot.get("cancel_requested") is True
        or not isinstance(unit, dict)
        or unit.get("kind") != "h3_concat"
        or unit.get("state") != "completed"
        or not isinstance(artifacts, list)
        or len(artifacts) != 1
        or not isinstance(artifacts[0], dict)
        or not isinstance(cursor, dict)
        or not isinstance(cursor.get("completed_units"), list)
        or type(private) is not bool
        or type(explicit) is not bool
        or snapshot.get("private") is not private
        or snapshot.get("explicit") is not explicit
    ):
        raise FinalSidecarResealError("Job is not held for exact H3 final recovery.")
    artifact = artifacts[0]
    sidecar_sha = candidate.get("sidecar_sha256")
    sidecar_size = candidate.get("sidecar_size")
    if (
        candidate.get("job_id") != snapshot.get("id")
        or candidate.get("kind") != "h3_concat"
        or candidate.get("unit_id") != unit.get("unit_id")
        or candidate.get("unit_variant") != unit.get("variant")
        or candidate.get("unit_index") != unit.get("index")
        or list(candidate.get("dependencies") or ()) != unit.get("dependencies")
        or candidate.get("settings") != unit.get("settings")
        or candidate.get("dest_media") != artifact.get("basename")
        or candidate.get("dest_sidecar") != artifact.get("sidecar_basename")
        or candidate.get("media_sha256") != artifact.get("sha256")
        or candidate.get("media_size") != artifact.get("size")
        or artifact.get("producer_unit_id") != unit.get("unit_id")
        or type(sidecar_sha) is not str
        or _SHA256.fullmatch(sidecar_sha) is None
        or type(sidecar_size) is not int
        or sidecar_size < 1
        or sidecar_sha == artifact.get("sidecar_sha256")
    ):
        raise FinalSidecarResealError("Final media or producer evidence changed.")
    completed = cursor["completed_units"]
    dependencies = unit["dependencies"]
    if (
        len(dependencies) != 1
        or len(completed) != 1
        or not isinstance(completed[0], dict)
        or completed[0].get("state") != "completed"
        or completed[0].get("kind") != "h3_segment"
        or completed[0].get("unit_id") != dependencies[0]
        or completed[0].get("index") != 0
        or completed[0].get("variant") != unit.get("variant")
    ):
        raise FinalSidecarResealError("Sealed segment dependency is incomplete.")
    history = cursor.get("final_sidecar_reseal_history", [])
    if not isinstance(history, list) or len(history) >= _MAX_HISTORY:
        raise FinalSidecarResealError("Final recovery history is unavailable.")
    prior_digest = _canonical_digest(unit)
    updated = deepcopy(dict(snapshot))
    new_unit = deepcopy(unit)
    new_unit["artifacts"][0]["sidecar_sha256"] = sidecar_sha
    new_unit["artifacts"][0]["sidecar_size"] = sidecar_size
    new_cursor = deepcopy(cursor)
    new_cursor["completed_units"] = [deepcopy(completed[0]), deepcopy(new_unit)]
    new_cursor["final_sidecar_reseal_history"] = [
        *deepcopy(history),
        {
            "version": 1,
            "reason": "sidecar_changed_media_unchanged",
            "prior_unit": deepcopy(unit),
            "prior_unit_sha256": prior_digest,
            "new_unit_sha256": _canonical_digest(new_unit),
        },
    ]
    updated["recovery_unit"] = new_unit
    updated["recovery_cursor"] = new_cursor
    return updated


__all__ = [
    "FinalSidecarResealError", "ResealInspection",
    "plan_h3_final_sidecar_reseal", "inspect_h3_final_sidecar_reseal",
    "prepare_h3_final_sidecar_recovery", "main",
]


@dataclass(frozen=True)
class ResealInspection:
    app_directory: Path
    journal: Any
    sequence: int
    epoch: int
    revision: int
    job_id: str
    workspace: str
    project: Path
    snapshot: dict[str, Any]
    candidate: dict[str, Any]
    proposed: dict[str, Any]
    needs_commit: bool


def _read_existing_secret(path: Path) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 32
            or before.st_size > 4096
            or (os.name != "nt" and stat.S_IMODE(before.st_mode) & 0o077)
        ):
            raise FinalSidecarResealError("Project identity is unavailable.")
        secret = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        if (
            len(secret) != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (current.st_dev, current.st_ino)
                != (after.st_dev, after.st_ino)
        ):
            raise FinalSidecarResealError("Project identity changed.")
        return secret
    except OSError:
        raise FinalSidecarResealError("Project identity is unavailable.") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def inspect_h3_final_sidecar_reseal(
    app_directory: Path, job_id: str,
) -> ResealInspection:
    """Read strict journal, project and media evidence without publishing."""
    from services import queue_recovery_final_adoption as adoption
    from services.h3_audio_safety import DEFAULT_TARGET_DBTP, POLICY_VERSION
    from services.h3_output_integrity import (
        expected_h3_final_frames, probe_h3_output,
    )
    from services.queue_recovery import QueueRecoveryJournal
    from services.queue_recovery_adapter import project_instance_digest
    from services.queue_recovery_runtime import (
        load_request_manifest, validate_artifact_descriptor,
    )
    from services.win_safe_files import is_safe_workspace_name, safe_join_under

    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", job_id):
        raise FinalSidecarResealError("Job identity is invalid.")
    app_directory = Path(app_directory).resolve(strict=True)
    journal = QueueRecoveryJournal(
        app_directory / "storage" / "queue-recovery" / "studio-queue.jsonl",
    )
    recovered = journal.recover()
    snapshot = recovered.jobs.get(job_id)
    if not isinstance(snapshot, dict):
        raise FinalSidecarResealError("Held job is unavailable.")
    workspace = snapshot.get("workspace")
    if not is_safe_workspace_name(workspace):
        raise FinalSidecarResealError("Project identity is invalid.")
    with open(app_directory / "wgp_config.json", "r", encoding="utf-8") as handle:
        config = json.load(handle)
    save_path = config.get("save_path") if isinstance(config, dict) else None
    if not isinstance(save_path, str) or not os.path.isabs(save_path):
        raise FinalSidecarResealError("Configured project root is invalid.")
    base = Path(os.path.realpath(save_path))
    project_name = "" if workspace == "default" else workspace
    joined = safe_join_under(str(base), project_name)
    if joined is None:
        raise FinalSidecarResealError("Project identity is invalid.")
    project = Path(joined)
    if (
        not project.is_dir()
        or stat.S_ISLNK(os.lstat(project).st_mode)
        or not (project / ".maestro-project-instance").is_file()
    ):
        raise FinalSidecarResealError("Project is unavailable.")
    marker_path = project / ".maestro-project-instance"
    marker_descriptor = -1
    try:
        marker_descriptor = os.open(
            marker_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        marker_stat = os.fstat(marker_descriptor)
        if not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_nlink != 1 or marker_stat.st_size > 64:
            raise FinalSidecarResealError("Project identity is invalid.")
        marker = os.read(marker_descriptor, 65).decode("ascii").strip()
        refreshed_marker = os.fstat(marker_descriptor)
        current_marker = os.lstat(marker_path)
        if (
            (marker_stat.st_dev, marker_stat.st_ino, marker_stat.st_size,
             marker_stat.st_mtime_ns)
            != (refreshed_marker.st_dev, refreshed_marker.st_ino,
                refreshed_marker.st_size, refreshed_marker.st_mtime_ns)
            or (current_marker.st_dev, current_marker.st_ino)
                != (marker_stat.st_dev, marker_stat.st_ino)
        ):
            raise FinalSidecarResealError("Project identity changed.")
    except (OSError, UnicodeError):
        raise FinalSidecarResealError("Project identity is unavailable.") from None
    finally:
        if marker_descriptor >= 0:
            os.close(marker_descriptor)
    secret = _read_existing_secret(app_directory / ".maestro-session-secret")
    if not hmac.compare_digest(
        project_instance_digest(secret, marker),
        str(snapshot.get("project_instance") or ""),
    ):
        raise FinalSidecarResealError("Project identity changed.")
    load_request_manifest(
        project, snapshot.get("request_manifest"), expected_job_id=job_id,
    )
    unit = snapshot.get("recovery_unit")
    attestation = unit.get("attestation") if isinstance(unit, dict) else None
    peak = (
        attestation.get("h3_audio_true_peak")
        if isinstance(attestation, dict) else None
    )
    settings = unit.get("settings") if isinstance(unit, dict) else None
    policy = settings.get("h3_audio_true_peak_policy") if isinstance(settings, dict) else None
    expected_policy = {
        "policy_version": POLICY_VERSION, "target_dbtp": DEFAULT_TARGET_DBTP,
    }
    if (
        policy != expected_policy
        or not isinstance(peak, dict)
        or peak.get("verified") is not True
        or peak.get("policy_version") != POLICY_VERSION
        or peak.get("target_dbtp") != DEFAULT_TARGET_DBTP
    ):
        raise FinalSidecarResealError("Final audio policy is not attested.")
    cursor = snapshot.get("recovery_cursor")
    completed = cursor.get("completed_units") if isinstance(cursor, dict) else None
    segment = completed[0] if isinstance(completed, list) and completed else None
    adoption._staging_job_policy(snapshot, workspace=workspace)
    adoption._staging_unit(segment, job_id=job_id, kind="h3_segment")
    adoption._staging_unit(unit, job_id=job_id, kind="h3_concat")
    if not isinstance(segment, dict) or not all(
        validate_artifact_descriptor(
            project, descriptor, producer_unit_id=segment.get("unit_id"),
        ) for descriptor in segment.get("artifacts", [])
    ) or not segment.get("artifacts"):
        raise FinalSidecarResealError("Sealed segment bytes changed.")
    quarantine = project / ".maestro-recovery" / "quarantine"
    adoption._existing_private_directory(quarantine)
    adoption.validate_h3_segment_staging_name_budget(
        project, workspace=workspace, job=snapshot, segment_unit=segment,
    )
    discovered, _rejected = adoption._discover(quarantine, workspace=workspace)
    candidates = [
        candidate
        for candidate in discovered
        if candidate.get("job_id") == job_id
        and candidate.get("kind") == "h3_concat"
    ]
    if len(candidates) != 1:
        raise FinalSidecarResealError("Exact quarantined final is unavailable.")
    candidate = candidates[0]
    raw, _info = adoption._read_exact(
        quarantine / candidate["source_sidecar"],
        maximum_bytes=adoption.MAX_MANIFEST_BYTES,
    )
    if hashlib.sha256(raw).hexdigest() != candidate["sidecar_sha256"]:
        raise FinalSidecarResealError("Final sidecar changed during inspection.")
    meta = json.loads(raw)
    if not isinstance(meta, dict):
        raise FinalSidecarResealError("Final sidecar is invalid.")
    current_artifacts = unit.get("artifacts") if isinstance(unit, dict) else None
    current_sidecar_sha = (
        current_artifacts[0].get("sidecar_sha256")
        if isinstance(current_artifacts, list) and len(current_artifacts) == 1
        and isinstance(current_artifacts[0], dict) else None
    )
    if current_sidecar_sha == candidate["sidecar_sha256"]:
        history = cursor.get("final_sidecar_reseal_history") if isinstance(cursor, dict) else None
        if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
            raise FinalSidecarResealError("Final reseal history is missing.")
        previous = deepcopy(snapshot)
        previous["recovery_unit"] = deepcopy(history[-1].get("prior_unit"))
        previous_cursor = deepcopy(cursor)
        previous_cursor["completed_units"] = [deepcopy(segment)]
        previous_cursor["final_sidecar_reseal_history"] = deepcopy(history[:-1])
        previous["recovery_cursor"] = previous_cursor
        reconstructed = plan_h3_final_sidecar_reseal(
            previous, candidate=candidate,
            private=meta.get("private"), explicit=meta.get("explicit"),
        )
        if reconstructed != snapshot:
            raise FinalSidecarResealError("Final reseal history changed.")
        proposed = deepcopy(snapshot)
        needs_commit = False
    else:
        proposed = plan_h3_final_sidecar_reseal(
            snapshot, candidate=candidate,
            private=meta.get("private"), explicit=meta.get("explicit"),
        )
        needs_commit = True
    plan = snapshot.get("h3_segment_plan")
    if not isinstance(plan, dict):
        raise FinalSidecarResealError("H3 final frame plan is unavailable.")
    expected_frames = expected_h3_final_frames(
        plan.get("published_frames"),
        public_source_prefix=plan.get("source_prefix"),
        recovery_cursor=snapshot.get("recovery_cursor"),
        recovery_final_unit=unit,
        require_recovery_evidence=True,
    )
    fps = plan.get("fps")
    if type(fps) not in {int, float} or fps <= 0:
        raise FinalSidecarResealError("H3 final frame plan is invalid.")
    integrity = probe_h3_output(
        quarantine / candidate["source_media"],
        expected_fps=float(fps), expected_frames=expected_frames,
        require_audio=True,
    )
    if integrity.get("validation") != "valid":
        raise FinalSidecarResealError("Final media integrity is unverified.")
    binding = quarantine.parent / "final-adoption" / "bindings" / adoption._binding_name(workspace, job_id)
    if binding.exists() or binding.is_symlink():
        raise FinalSidecarResealError("Final adoption already has a job binding.")
    return ResealInspection(
        app_directory=app_directory, journal=journal,
        sequence=recovered.last_sequence, epoch=recovered.epoch,
        revision=recovered.job_revisions[job_id], job_id=job_id,
        workspace=workspace, project=project, snapshot=snapshot,
        candidate=candidate, proposed=proposed, needs_commit=needs_commit,
    )


def prepare_h3_final_sidecar_recovery(
    inspection: ResealInspection,
    *,
    expected_sequence: int,
) -> dict[str, Any]:
    """CAS the reseal, then close the quarantine graph for startup adoption.

    A failure after the CAS leaves the job held.  Reinspection verifies the
    audit history and can resume create-only dependency staging.
    """
    from services import queue_recovery_final_adoption as adoption

    if type(expected_sequence) is not int or expected_sequence != inspection.sequence:
        raise FinalSidecarResealError("Queue state changed; inspect it again.")
    current = inspect_h3_final_sidecar_reseal(
        inspection.app_directory, inspection.job_id,
    )
    if (
        current.sequence != inspection.sequence
        or current.snapshot != inspection.snapshot
        or current.candidate != inspection.candidate
        or current.proposed != inspection.proposed
    ):
        raise FinalSidecarResealError("Recovery evidence changed; inspect it again.")
    if inspection.needs_commit:
        inspection.journal.commit_state(
            jobs={inspection.job_id: inspection.proposed},
            expected_job_revisions={inspection.job_id: inspection.revision},
            expected_epoch=inspection.epoch,
        )
    segment = inspection.proposed["recovery_cursor"]["completed_units"][0]
    result = adoption.stage_quarantined_h3_segment_for_final_adoption(
        inspection.project,
        workspace=inspection.workspace,
        job=inspection.proposed,
        segment_unit=segment,
        final_unit=inspection.proposed["recovery_unit"],
    )
    if (
        result.get("final_sidecar_sha256")
        != inspection.proposed["recovery_unit"]["artifacts"][0]["sidecar_sha256"]
        or result.get("final_sidecar_seal_match") is not True
    ):
        raise FinalSidecarResealError(
            "Final evidence changed after reseal; the job remains held."
        )
    quarantine = inspection.project / ".maestro-recovery" / "quarantine"
    candidates, _rejected = adoption._discover(
        quarantine, workspace=inspection.workspace,
    )
    complete, _incomplete = adoption._complete_groups(candidates)
    if not any(
        group.get("job_id") == inspection.job_id for group in complete
    ):
        raise FinalSidecarResealError(
            "Final adoption graph is incomplete; the job remains held."
        )
    return {
        "state": "prepared_for_startup_adoption",
        "journal_sequence": inspection.sequence + int(inspection.needs_commit),
        "job_held": True,
    }


def main(argv: list[str] | None = None) -> int:
    from services.queue_recovery import QueueRecoveryError
    from services.queue_recovery_runtime import QueueRecoveryRuntimeError

    parser = argparse.ArgumentParser(
        description="Prepare one held H3 final for CPU-only startup adoption.",
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--expected-sequence", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--offline-confirmed", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and (
        not args.offline_confirmed or args.expected_sequence is None
    ):
        parser.error(
            "--apply requires --offline-confirmed and --expected-sequence"
        )
    try:
        inspection = inspect_h3_final_sidecar_reseal(
            Path(__file__).resolve().parents[1], args.job_id,
        )
        if not args.apply:
            result = {
                "state": "ready_for_offline_preparation",
                "journal_sequence": inspection.sequence,
                "needs_reseal": inspection.needs_commit,
                "job_held": True,
            }
        else:
            result = prepare_h3_final_sidecar_recovery(
                inspection, expected_sequence=args.expected_sequence,
            )
    except (
        FinalSidecarResealError, QueueRecoveryError,
        QueueRecoveryRuntimeError, OSError, ValueError, TypeError, KeyError,
    ) as error:
        message = (
            str(error)
            if isinstance(error, FinalSidecarResealError)
            else "Final recovery evidence is unavailable or changed."
        )
        print(json.dumps({"state": "held", "message": message}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
