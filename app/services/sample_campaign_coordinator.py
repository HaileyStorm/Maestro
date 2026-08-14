"""Atomic, model-free submission for held comparative sample pairs.

The coordinator deliberately owns no HTTP or generation-engine policy.  Its
caller supplies two requests authorized and normalized by the launch-owned
sample-submission boundary; this service does not claim `/generate` parity.
It then enforces the private pair contract, writes both private request
manifests, commits both held jobs in one recovery-journal transaction, and
only then publishes the pair to the live registry.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from typing import Any

from services.sample_campaign import (
    ArmManifest,
    CampaignArm,
    ComparativePairManifest,
    ImmutableSettings,
    InterventionDelta,
    SampleCampaignError,
    normalized_input_digest,
    normalized_prompt_digest,
    pair_manifest_digest,
    validate_pair_manifest,
)


QUEUE_CLASS = "background_sample"
QUEUE_PRIORITY = -1000
LINKAGE_SCHEMA = 1
PRIVATE_MANIFEST_KEY = "_sample_campaign_private"
SAMPLE_JOB_KIND = "sample_campaign_generation"


class SampleCampaignSubmissionError(ValueError):
    """A private pair cannot be submitted without weakening its contract."""


@dataclass(frozen=True, slots=True)
class SampleArmSubmission:
    """One authorized ordinary job and its exact private campaign arm."""

    arm: CampaignArm
    manifest: ArmManifest
    job: Mapping[str, Any]
    project_directory: str
    owner_digest: str
    project_digest: str
    request_inputs: tuple[Mapping[str, Any], ...]
    generation_settings: Mapping[str, Any]


def _require_exact_keys(
    value: object,
    expected: frozenset[str],
    *,
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise SampleCampaignSubmissionError(f"Campaign {field} is invalid.")
    return value


def _string_sequence(value: object, *, field: str) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or any(not isinstance(item, str) for item in value)
    ):
        raise SampleCampaignSubmissionError(f"Campaign {field} is invalid.")
    return tuple(value)


def parse_private_pair_manifest(value: object) -> ComparativePairManifest:
    """Parse one strict private pair payload without accepting hidden fields."""

    raw = _require_exact_keys(
        value,
        frozenset({
            "schema_version", "pair_id", "case_id", "maestro", "control",
            "intervention_delta",
        }),
        field="pair manifest",
    )
    arm_keys = frozenset({
        "arm", "raw_prompt", "prompt_digest", "private_input_paths",
        "input_fingerprints", "input_digest", "model_revision", "settings",
        "seed", "output_index", "interventions",
    })

    def arm(name: str, expected: CampaignArm) -> ArmManifest:
        item = _require_exact_keys(raw.get(name), arm_keys, field=f"{name} arm")
        try:
            parsed_arm = CampaignArm(item.get("arm"))
            settings = ImmutableSettings.from_mapping(item.get("settings"))
            manifest = ArmManifest(
                arm=parsed_arm,
                raw_prompt=item.get("raw_prompt"),
                prompt_digest=item.get("prompt_digest"),
                private_input_paths=_string_sequence(
                    item.get("private_input_paths"), field="private input paths",
                ),
                input_fingerprints=_string_sequence(
                    item.get("input_fingerprints"), field="input fingerprints",
                ),
                input_digest=item.get("input_digest"),
                model_revision=item.get("model_revision"),
                settings=settings,
                seed=item.get("seed"),
                output_index=item.get("output_index"),
                interventions=_string_sequence(
                    item.get("interventions"), field="interventions",
                ),
            )
        except (SampleCampaignError, TypeError, ValueError) as error:
            raise SampleCampaignSubmissionError(
                f"Campaign {name} arm is invalid."
            ) from error
        if manifest.arm is not expected:
            raise SampleCampaignSubmissionError(
                f"Campaign {name} arm identity is invalid."
            )
        return manifest

    delta_raw = _require_exact_keys(
        raw.get("intervention_delta"),
        frozenset({"maestro_only", "control_only"}),
        field="intervention delta",
    )
    try:
        pair = ComparativePairManifest(
            pair_id=raw.get("pair_id"),
            case_id=raw.get("case_id"),
            maestro=arm("maestro", CampaignArm.MAESTRO),
            control=arm("control", CampaignArm.CONTROL),
            intervention_delta=InterventionDelta(
                maestro_only=_string_sequence(
                    delta_raw.get("maestro_only"), field="Maestro intervention delta",
                ),
                control_only=_string_sequence(
                    delta_raw.get("control_only"), field="control intervention delta",
                ),
            ),
            schema_version=raw.get("schema_version"),
        )
    except (SampleCampaignError, TypeError, ValueError) as error:
        raise SampleCampaignSubmissionError("Campaign pair manifest is invalid.") from error
    validate_pair_manifest(pair)
    return pair


def private_pair_manifest_payload(
    pair: ComparativePairManifest,
) -> dict[str, Any]:
    """Serialize the complete strict pair for one private sealed manifest."""

    validate_pair_manifest(pair)

    def arm_payload(arm: ArmManifest) -> dict[str, Any]:
        return {
            "arm": arm.arm.value,
            "raw_prompt": arm.raw_prompt,
            "prompt_digest": arm.prompt_digest,
            "private_input_paths": list(arm.private_input_paths),
            "input_fingerprints": list(arm.input_fingerprints),
            "input_digest": arm.input_digest,
            "model_revision": arm.model_revision,
            "settings": arm.settings.to_dict(),
            "seed": arm.seed,
            "output_index": arm.output_index,
            "interventions": list(arm.interventions),
        }

    return {
        "schema_version": pair.schema_version,
        "pair_id": pair.pair_id,
        "case_id": pair.case_id,
        "maestro": arm_payload(pair.maestro),
        "control": arm_payload(pair.control),
        "intervention_delta": {
            "maestro_only": list(pair.intervention_delta.maestro_only),
            "control_only": list(pair.intervention_delta.control_only),
        },
    }


def sample_arm_job_id(manifest_digest: str, arm: CampaignArm) -> str:
    """Return the durable idempotency fence for one exact pair arm."""

    if (
        not isinstance(manifest_digest, str)
        or len(manifest_digest) != 64
        or any(character not in "0123456789abcdef" for character in manifest_digest)
        or not isinstance(arm, CampaignArm)
    ):
        raise SampleCampaignSubmissionError("Campaign pair digest is invalid.")
    suffix = hashlib.sha256(
        b"maestro-sample-job-v1\0"
        + manifest_digest.encode("ascii")
        + b"\0"
        + arm.value.encode("ascii")
    ).hexdigest()[:24]
    return f"sample-{arm.value}-{suffix}"


def validate_private_arm_request(
    manifest: ArmManifest,
    request_params: Mapping[str, Any],
    *,
    server_model_revision: str,
    generation_settings: Mapping[str, Any],
    authorized_input_paths: Sequence[str],
    input_fingerprints: Sequence[str],
    output_index: int,
) -> None:
    """Bind an authorized ordinary request to its asserted private manifest."""

    if not isinstance(manifest, ArmManifest) or not isinstance(request_params, Mapping):
        raise SampleCampaignSubmissionError("Campaign arm request is invalid.")
    try:
        if normalized_prompt_digest(request_params.get("prompt")) != manifest.prompt_digest:
            raise SampleCampaignSubmissionError("Campaign arm prompt does not match.")
        if server_model_revision != manifest.model_revision:
            raise SampleCampaignSubmissionError("Campaign arm model revision does not match.")
        if request_params.get("seed") != manifest.seed:
            raise SampleCampaignSubmissionError("Campaign arm seed does not match.")
        if output_index != manifest.output_index:
            raise SampleCampaignSubmissionError("Campaign arm output index does not match.")
        try:
            requested_outputs = max(
                1, int(request_params.get("repeat_generation", 1) or 1),
            )
        except (TypeError, ValueError):
            raise SampleCampaignSubmissionError(
                "Campaign arm output count is invalid."
            ) from None
        if not 0 <= output_index < requested_outputs:
            raise SampleCampaignSubmissionError(
                "Campaign arm output index is unavailable."
            )
        settings = manifest.settings.to_dict()
        if not isinstance(generation_settings, Mapping) or dict(
            generation_settings
        ) != settings:
            raise SampleCampaignSubmissionError("Campaign arm settings do not match.")
        paths = tuple(authorized_input_paths)
        fingerprints = tuple(str(value).lower() for value in input_fingerprints)
        if paths != manifest.private_input_paths:
            raise SampleCampaignSubmissionError("Campaign arm inputs do not match.")
        if fingerprints != manifest.input_fingerprints:
            raise SampleCampaignSubmissionError(
                "Campaign arm input fingerprints do not match."
            )
        if normalized_input_digest(fingerprints) != manifest.input_digest:
            raise SampleCampaignSubmissionError("Campaign arm input digest does not match.")
    except SampleCampaignError as error:
        raise SampleCampaignSubmissionError("Campaign arm request is invalid.") from error


class HeldSamplePairCoordinator:
    """Commit and publish two held ordinary jobs, or publish neither."""

    def __init__(
        self,
        *,
        write_manifest: Callable[..., Mapping[str, Any]],
        remove_manifest: Callable[[str, Mapping[str, Any]], bool],
        register_jobs_atomic: Callable[..., None],
        rollback_jobs_atomic: Callable[[Sequence[str]], None],
        publish_jobs_atomic: Callable[[Sequence[tuple[str, dict[str, Any]]]], None],
        global_state_for_jobs: Callable[[Sequence[dict[str, Any]]], Mapping[str, Any]],
    ) -> None:
        self._write_manifest = write_manifest
        self._remove_manifest = remove_manifest
        self._register_jobs_atomic = register_jobs_atomic
        self._rollback_jobs_atomic = rollback_jobs_atomic
        self._publish_jobs_atomic = publish_jobs_atomic
        self._global_state_for_jobs = global_state_for_jobs

    def submit(
        self,
        pair: ComparativePairManifest,
        submissions: Sequence[SampleArmSubmission],
    ) -> dict[str, Any]:
        validate_pair_manifest(pair)
        if (
            isinstance(submissions, (str, bytes))
            or not isinstance(submissions, Sequence)
            or len(submissions) != 2
            or any(not isinstance(item, SampleArmSubmission) for item in submissions)
        ):
            raise SampleCampaignSubmissionError(
                "Campaign submission requires exactly two arms."
            )
        by_arm = {item.arm: item for item in submissions}
        if set(by_arm) != {CampaignArm.MAESTRO, CampaignArm.CONTROL}:
            raise SampleCampaignSubmissionError(
                "Campaign submission requires unique Maestro and control arms."
            )
        expected = {
            CampaignArm.MAESTRO: pair.maestro,
            CampaignArm.CONTROL: pair.control,
        }
        if any(item.manifest != expected[item.arm] for item in submissions):
            raise SampleCampaignSubmissionError(
                "Campaign submission arm does not match the pair manifest."
            )
        project_directories = {item.project_directory for item in submissions}
        owners = {item.owner_digest for item in submissions}
        projects = {item.project_digest for item in submissions}
        if len(project_directories) != 1 or len(owners) != 1 or len(projects) != 1:
            raise SampleCampaignSubmissionError(
                "Campaign arms must share one owner and project."
            )
        if dict(by_arm[CampaignArm.MAESTRO].generation_settings) != dict(
            by_arm[CampaignArm.CONTROL].generation_settings
        ):
            raise SampleCampaignSubmissionError(
                "Campaign arm generation requests must match exactly."
            )

        pair_digest = pair_manifest_digest(pair)
        pair_payload = private_pair_manifest_payload(pair)

        jobs: dict[CampaignArm, dict[str, Any]] = {}
        job_ids: set[str] = set()
        for arm in (CampaignArm.MAESTRO, CampaignArm.CONTROL):
            submission = by_arm[arm]
            if not isinstance(submission.job, Mapping):
                raise SampleCampaignSubmissionError("Campaign arm job is invalid.")
            job = deepcopy(dict(submission.job))
            job_id = job.get("id")
            if (
                not isinstance(job_id, str)
                or job_id != sample_arm_job_id(pair_digest, arm)
                or job_id in job_ids
            ):
                raise SampleCampaignSubmissionError(
                    "Campaign arm job identifiers must be unique."
                )
            if job.get("status") != "queued":
                raise SampleCampaignSubmissionError(
                    "Campaign arm must be an ordinary queued job."
                )
            if job.get("kind") != SAMPLE_JOB_KIND:
                raise SampleCampaignSubmissionError(
                    "Campaign arm recovery kind is invalid."
                )
            job_ids.add(job_id)
            jobs[arm] = job

        for arm, peer in (
            (CampaignArm.MAESTRO, CampaignArm.CONTROL),
            (CampaignArm.CONTROL, CampaignArm.MAESTRO),
        ):
            job = jobs[arm]
            linkage = {
                "schema": LINKAGE_SCHEMA,
                "pair_id": pair.pair_id,
                "pair_manifest_digest": pair_digest,
                "arm": arm.value,
                "peer_job_id": jobs[peer]["id"],
            }
            params = deepcopy(dict(job.get("params") or {}))
            if PRIVATE_MANIFEST_KEY in params:
                raise SampleCampaignSubmissionError(
                    "Campaign private recovery state is server-owned."
                )
            job.update({
                "params": params,
                "queue_class": QUEUE_CLASS,
                "queue_priority": QUEUE_PRIORITY,
                "queue_held": True,
                "prompt_preview": "",
                "recovery_cursor": {"sample_campaign": deepcopy(linkage)},
            })

        pointers: list[tuple[str, Mapping[str, Any]]] = []
        registered = False
        try:
            registrations = []
            ordered_jobs = []
            for arm in (CampaignArm.MAESTRO, CampaignArm.CONTROL):
                submission = by_arm[arm]
                job = jobs[arm]
                manifest_params = deepcopy(job["params"])
                manifest_params[PRIVATE_MANIFEST_KEY] = {
                    "pair_manifest": deepcopy(pair_payload),
                    "pair_manifest_digest": pair_digest,
                    "linkage": deepcopy(
                        job["recovery_cursor"]["sample_campaign"]
                    ),
                }
                pointer = self._write_manifest(
                    submission.project_directory,
                    job_id=job["id"],
                    params=manifest_params,
                    inputs=tuple(dict(item) for item in submission.request_inputs),
                )
                pointers.append((submission.project_directory, pointer))
                registrations.append((
                    job,
                    submission.owner_digest,
                    submission.project_digest,
                    pointer,
                ))
                ordered_jobs.append(job)
            self._register_jobs_atomic(
                tuple(registrations),
                global_state=self._global_state_for_jobs(tuple(ordered_jobs)),
            )
            registered = True
            for arm, job, (_project_directory, pointer) in zip(
                (CampaignArm.MAESTRO, CampaignArm.CONTROL),
                ordered_jobs,
                pointers,
            ):
                job["_recovery_manifest_pointer"] = dict(pointer)
                job["_recovery_owner_digest"] = by_arm[arm].owner_digest
                job["_recovery_project_digest"] = by_arm[arm].project_digest
            self._publish_jobs_atomic(tuple(
                (job["id"], job) for job in ordered_jobs
            ))
        except Exception:
            cleanup = not registered
            if registered:
                try:
                    self._rollback_jobs_atomic(tuple(
                        jobs[arm]["id"]
                        for arm in (CampaignArm.MAESTRO, CampaignArm.CONTROL)
                    ))
                except Exception:
                    # The durable held pair still owns both private manifests.
                    # Preserve that complete recovery evidence and keep the
                    # live registry unpublished rather than creating orphans.
                    cleanup = False
                else:
                    cleanup = True
            if cleanup:
                for project_directory, pointer in reversed(pointers):
                    self._remove_manifest(project_directory, pointer)
            raise

        return {
            "pair_id": pair.pair_id,
            "status": "held",
            "arms": [
                {"arm": arm.value, "job_id": jobs[arm]["id"]}
                for arm in (CampaignArm.MAESTRO, CampaignArm.CONTROL)
            ],
        }


__all__ = [
    "HeldSamplePairCoordinator",
    "LINKAGE_SCHEMA",
    "PRIVATE_MANIFEST_KEY",
    "QUEUE_CLASS",
    "QUEUE_PRIORITY",
    "SAMPLE_JOB_KIND",
    "SampleArmSubmission",
    "SampleCampaignSubmissionError",
    "parse_private_pair_manifest",
    "private_pair_manifest_payload",
    "sample_arm_job_id",
    "validate_private_arm_request",
]
