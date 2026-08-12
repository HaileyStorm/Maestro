"""Persistent, content-free evidence for model residency decisions.

This module deliberately stops at evidence and policy seams.  It does not load,
unload, profile, or retry a model; runtime owners decide where those actions are
safe.  Exact observations remain reusable across process restarts, while nearby
estimates are restricted to an explicitly compatible metadata region.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Any, Callable, Mapping, Sequence, TypeVar


SCHEMA_VERSION = 1
SHIPPED_EVIDENCE_VERSION = 1
DEFAULT_MAX_RECORDS = 512
DEFAULT_OOM_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_STORE_BYTES = 2 * 1024 * 1024

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+~-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PHASES = frozenset({"model_load", "generation", "finalization"})
_GROUP_FIELDS = {
    "model": {
        "artifact_id": "token",
        "artifact_revision": "token",
        "family": "token",
        "quantization": "token",
    },
    "runtime": {
        "runtime_id": "token",
        "runtime_version": "token",
        "build_id": "token",
        "driver_version": "token",
    },
    "hardware": {
        "accelerator": "token",
        "total_vram_gib": "number",
        "total_host_ram_gib": "number",
    },
    "workload": {
        "kind": "token",
        "width": "integer",
        "height": "integer",
        "frame_count": "integer",
        "steps": "integer",
        "reference_count": "integer",
        "lora_count": "integer",
        "stage_count": "integer",
    },
    "settings": {
        "offload_profile": "number",
        "resident_budget_gib": "number",
        "attention_backend": "token",
        "cache_mode": "token",
        "weight_quantization": "token",
    },
    "condition": {
        "free_vram_band_gib": "number",
        "free_host_ram_band_gib": "number",
        "residency_epoch_band": "integer",
    },
}
_REQUIRED_FIELDS = {
    group: set(fields) for group, fields in _GROUP_FIELDS.items()
}
_COMPATIBLE_WORKLOAD_FIELDS = {
    "kind", "reference_count", "lora_count", "stage_count",
}
_COMPATIBLE_SETTINGS_FIELDS = set(_GROUP_FIELDS["settings"]) - {
    "resident_budget_gib",
}


class ModelResidencyError(RuntimeError):
    """Base error for residency evidence and policy operations."""


class ModelResidencyValidationError(ModelResidencyError, ValueError):
    """Caller-supplied metadata is not a bounded content-free identity."""


class ModelResidencyPersistenceError(ModelResidencyError):
    """The durable evidence store cannot be read or committed safely."""


def _token_digest(group: str, name: str, value: str) -> str:
    return hashlib.sha256(
        f"residency-token-v{SCHEMA_VERSION}\0{group}\0{name}\0{value}".encode(
            "utf-8"
        )
    ).hexdigest()


def _normalize_group(
    group: str,
    value: Mapping[str, Any],
    *,
    tokens_are_digests: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelResidencyValidationError(
            f"Residency {group} metadata must be a mapping."
        )
    source = dict(value)
    allowed = _GROUP_FIELDS[group]
    if set(source) - set(allowed):
        raise ModelResidencyValidationError(
            f"Residency {group} metadata contains an unsupported field."
        )
    if not _REQUIRED_FIELDS[group].issubset(source):
        raise ModelResidencyValidationError(
            f"Residency {group} metadata is incomplete."
        )
    result: dict[str, Any] = {}
    for name in allowed:
        if name not in source:
            continue
        item = source[name]
        kind = allowed[name]
        if kind == "token":
            pattern = _DIGEST_RE if tokens_are_digests else _TOKEN_RE
            if not isinstance(item, str) or pattern.fullmatch(item) is None:
                raise ModelResidencyValidationError(
                    f"Residency {group} metadata contains an invalid token."
                )
            result[name] = item if tokens_are_digests else _token_digest(
                group, name, item,
            )
            continue
        if isinstance(item, bool):
            raise ModelResidencyValidationError(
                f"Residency {group} metadata contains an invalid number."
            )
        try:
            number = float(item)
        except (TypeError, ValueError):
            raise ModelResidencyValidationError(
                f"Residency {group} metadata contains an invalid number."
            ) from None
        if not math.isfinite(number) or number < 0 or number > 1_000_000:
            raise ModelResidencyValidationError(
                f"Residency {group} metadata contains an invalid number."
            )
        if kind == "integer":
            if not number.is_integer():
                raise ModelResidencyValidationError(
                    f"Residency {group} metadata contains a fractional integer."
                )
            result[name] = int(number)
        else:
            result[name] = number
    return result


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _key_from_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    compatibility = {
        "model": identity["model"],
        "runtime": identity["runtime"],
        "hardware": identity["hardware"],
        "workload": {
            name: identity["workload"][name]
            for name in sorted(_COMPATIBLE_WORKLOAD_FIELDS)
        },
        "settings": {
            name: identity["settings"][name]
            for name in sorted(_COMPATIBLE_SETTINGS_FIELDS)
        },
        # Residency epochs are categorical resource states, not a numeric
        # distance axis.  Interpolation never crosses an epoch boundary.
        "condition": {
            "residency_epoch_band": identity["condition"][
                "residency_epoch_band"
            ],
        },
        "policy_revision": identity["policy_revision"],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "exact_key": f"residency:v{SCHEMA_VERSION}:{_digest(identity)}",
        "compatibility_key": (
            f"residency-compatible:v{SCHEMA_VERSION}:{_digest(compatibility)}"
        ),
        "identity": dict(identity),
    }


def build_residency_key(
    *,
    model: Mapping[str, Any],
    runtime: Mapping[str, Any],
    hardware: Mapping[str, Any],
    workload: Mapping[str, Any],
    settings: Mapping[str, Any],
    condition: Mapping[str, Any],
    policy_revision: int,
) -> dict[str, Any]:
    """Build one exact, content-free residency identity.

    All exact fields are mandatory.  Allowed token values are domain-hashed
    before they enter the returned identity, so even a misplaced private token
    is never persisted in plaintext.
    """
    if type(policy_revision) is not int:
        raise ModelResidencyValidationError("Residency policy revision is invalid.")
    revision = policy_revision
    if revision < 1 or revision > 1_000_000:
        raise ModelResidencyValidationError("Residency policy revision is invalid.")
    identity = {
        "model": _normalize_group("model", model),
        "runtime": _normalize_group("runtime", runtime),
        "hardware": _normalize_group("hardware", hardware),
        "workload": _normalize_group("workload", workload),
        "settings": _normalize_group("settings", settings),
        "condition": _normalize_group("condition", condition),
        "policy_revision": revision,
    }
    return _key_from_identity(identity)


def _normalize_key(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "exact_key", "compatibility_key", "identity",
    }:
        raise ModelResidencyValidationError("Residency evidence key is invalid.")
    identity = value.get("identity")
    if not isinstance(identity, Mapping) or set(identity) != {
        "model", "runtime", "hardware", "workload", "settings", "condition",
        "policy_revision",
    }:
        raise ModelResidencyValidationError("Residency evidence key is invalid.")
    revision = identity.get("policy_revision")
    if type(revision) is not int or not 1 <= revision <= 1_000_000:
        raise ModelResidencyValidationError("Residency evidence key is invalid.")
    normalized_identity = {
        group: _normalize_group(
            group, identity[group], tokens_are_digests=True,
        )
        for group in _GROUP_FIELDS
    }
    normalized_identity["policy_revision"] = revision
    rebuilt = _key_from_identity(normalized_identity)
    if dict(value) != rebuilt:
        raise ModelResidencyValidationError("Residency evidence key is invalid.")
    return rebuilt


def _point(key: Mapping[str, Any]) -> tuple[float, float, float]:
    identity = key["identity"]
    workload = identity["workload"]
    condition = identity["condition"]
    compute = max(
        1.0,
        float(workload["width"])
        * float(workload["height"])
        * float(workload["frame_count"])
        * float(workload["steps"]),
    )
    return (
        math.log(compute),
        float(condition["free_vram_band_gib"]),
        float(condition["free_host_ram_band_gib"]),
    )


def _distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    a_compute, a_vram, a_host = _point(left)
    b_compute, b_vram, b_host = _point(right)
    return math.sqrt(
        ((a_compute - b_compute) / math.log(2.0)) ** 2
        + ((a_vram - b_vram) / 4.0) ** 2
        + ((a_host - b_host) / 16.0) ** 2
    )


def _record_id(key: Mapping[str, Any], outcome: str, phase: str) -> str:
    return hashlib.sha256(
        f"{key['exact_key']}\0{outcome}\0{phase}".encode("ascii")
    ).hexdigest()


class ModelResidencyEvidenceStore:
    """Bounded prior-run evidence overlaid on immutable shipped seeds."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        shipped_evidence: Sequence[Mapping[str, Any]] = (),
        max_records: int = DEFAULT_MAX_RECORDS,
        oom_ttl_seconds: float = DEFAULT_OOM_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(max_records, bool) or isinstance(oom_ttl_seconds, bool):
            raise ModelResidencyValidationError(
                "Residency evidence policy is invalid."
            )
        try:
            record_bound = int(max_records)
            oom_ttl = float(oom_ttl_seconds)
        except (TypeError, ValueError):
            raise ModelResidencyValidationError(
                "Residency evidence policy is invalid."
            ) from None
        if not 1 <= record_bound <= 4096:
            raise ModelResidencyValidationError("Residency evidence bound is invalid.")
        if not math.isfinite(oom_ttl) or not 60 <= oom_ttl <= 90 * 24 * 60 * 60:
            raise ModelResidencyValidationError("Residency OOM TTL is invalid.")
        from services.generation_names import GenerationNameRegistry
        self._storage_guard = GenerationNameRegistry(path)
        self.path = self._storage_guard.path
        self.max_records = record_bound
        self.oom_ttl_seconds = oom_ttl
        self._clock = clock
        self._lock = threading.RLock()
        self._shipped = tuple(
            self._normalize_shipped_record(item) for item in shipped_evidence
        )

    @staticmethod
    def _normalize_shipped_record(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {
            "evidence_version", "key", "sample_count",
        }:
            raise ModelResidencyValidationError("Shipped residency evidence is invalid.")
        if value.get("evidence_version") != SHIPPED_EVIDENCE_VERSION:
            raise ModelResidencyValidationError(
                "Shipped residency evidence version is invalid."
            )
        try:
            sample_count = int(value["sample_count"])
        except (TypeError, ValueError):
            raise ModelResidencyValidationError(
                "Shipped residency evidence sample count is invalid."
            ) from None
        if not 1 <= sample_count <= 1_000_000:
            raise ModelResidencyValidationError(
                "Shipped residency evidence sample count is invalid."
            )
        key = _normalize_key(value["key"])
        return {
            "record_id": _record_id(key, "success", ""),
            "key": key,
            "outcome": "success",
            "phase": "",
            "first_observed_at": 0.0,
            "observed_at": 0.0,
            "sample_count": sample_count,
            "required_margin_gib": 0.0,
            "source": "shipped",
            "evidence_version": SHIPPED_EVIDENCE_VERSION,
        }

    def _empty_document(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "records": {}}

    def _read(self, directory_descriptor: int | None) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        before = None
        try:
            if directory_descriptor is None:
                # Windows has no O_NOFOLLOW.  Match the reviewed predecessor
                # storage guard: reject a target reparse/symlink before open,
                # then bind the opened handle to that exact lstat identity.
                before = os.lstat(self.path)
                if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(
                    before.st_mode
                ):
                    raise ModelResidencyPersistenceError(
                        "Residency evidence file is unsafe."
                    )
                descriptor = os.open(self.path, flags)
            else:
                descriptor = os.open(
                    self.path.name, flags, dir_fd=directory_descriptor,
                )
        except FileNotFoundError:
            return self._empty_document()
        except ModelResidencyPersistenceError:
            raise
        except OSError as error:
            raise ModelResidencyPersistenceError(
                "Residency evidence is unavailable."
            ) from error
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size > MAX_STORE_BYTES
                or (os.name != "nt" and stat.S_IMODE(opened.st_mode) != 0o600)
                or (
                    before is not None
                    and (
                        opened.st_dev != before.st_dev
                        or opened.st_ino != before.st_ino
                    )
                )
            ):
                raise ModelResidencyPersistenceError(
                    "Residency evidence file is unsafe."
                )
            chunks: list[bytes] = []
            remaining = MAX_STORE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if before is not None:
                after = os.lstat(self.path)
                if (
                    stat.S_ISLNK(after.st_mode)
                    or not stat.S_ISREG(after.st_mode)
                    or after.st_dev != opened.st_dev
                    or after.st_ino != opened.st_ino
                ):
                    raise ModelResidencyPersistenceError(
                        "Residency evidence file changed while reading."
                    )
            payload = json.loads(b"".join(chunks).decode("ascii"))
        except ModelResidencyPersistenceError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ModelResidencyPersistenceError(
                "Residency evidence is unavailable."
            ) from error
        finally:
            os.close(descriptor)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "records"}
            or payload.get("schema_version") != SCHEMA_VERSION
            or not isinstance(payload.get("records"), dict)
        ):
            raise ModelResidencyPersistenceError("Residency evidence is invalid.")
        clean: dict[str, dict[str, Any]] = {}
        for record_id, raw in payload["records"].items():
            try:
                record = self._normalize_prior_record(raw)
            except ModelResidencyValidationError:
                continue
            if record_id == record["record_id"]:
                clean[record_id] = record
        if len(clean) > self.max_records:
            ordered = sorted(
                clean.items(),
                key=lambda item: (float(item[1]["observed_at"]), item[0]),
            )
            clean = dict(ordered[-self.max_records:])
        return {"schema_version": SCHEMA_VERSION, "records": clean}

    @staticmethod
    def _normalize_prior_record(value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "record_id", "key", "outcome", "phase", "first_observed_at",
            "observed_at", "sample_count", "required_margin_gib", "source",
            "evidence_version",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ModelResidencyValidationError("Residency observation is invalid.")
        key = _normalize_key(value["key"])
        outcome = value["outcome"]
        phase = value["phase"]
        if outcome not in {"success", "oom"}:
            raise ModelResidencyValidationError("Residency observation is invalid.")
        if (
            outcome == "success" and phase != ""
        ) or (
            outcome == "oom" and phase not in _PHASES
        ):
            raise ModelResidencyValidationError("Residency observation is invalid.")
        if value["source"] != "prior_run" or value["evidence_version"] != SCHEMA_VERSION:
            raise ModelResidencyValidationError("Residency observation is invalid.")
        try:
            first = float(value["first_observed_at"])
            observed = float(value["observed_at"])
            count = int(value["sample_count"])
            margin = float(value["required_margin_gib"])
        except (TypeError, ValueError):
            raise ModelResidencyValidationError("Residency observation is invalid.") from None
        expected_id = _record_id(key, outcome, phase)
        if (
            value["record_id"] != expected_id
            or not all(math.isfinite(item) for item in (first, observed, margin))
            or first < 0 or observed < first or margin < 0 or margin > 1024
            or not 1 <= count <= 1_000_000
        ):
            raise ModelResidencyValidationError("Residency observation is invalid.")
        return {
            "record_id": expected_id, "key": key, "outcome": outcome,
            "phase": phase, "first_observed_at": first,
            "observed_at": observed, "sample_count": count,
            "required_margin_gib": margin, "source": "prior_run",
            "evidence_version": SCHEMA_VERSION,
        }

    def _write(
        self,
        payload: Mapping[str, Any],
        directory_descriptor: int | None,
    ) -> None:
        from services.generation_names import _windows_replace_write_through
        encoded = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        if len(encoded) > MAX_STORE_BYTES:
            raise ModelResidencyPersistenceError("Residency evidence is too large.")
        temporary_name = f".{self.path.name}.{os.urandom(16).hex()}.tmp"
        temporary = self.path.parent / temporary_name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        try:
            descriptor = (
                os.open(temporary, flags, 0o600)
                if directory_descriptor is None else os.open(
                    temporary_name, flags, 0o600, dir_fd=directory_descriptor,
                )
            )
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise ModelResidencyPersistenceError(
                        "Residency evidence write made no progress."
                    )
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            if directory_descriptor is None:
                _windows_replace_write_through(temporary, self.path)
            else:
                os.replace(
                    temporary_name, self.path.name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                )
                os.fsync(directory_descriptor)
        except ModelResidencyPersistenceError:
            raise
        except OSError as error:
            raise ModelResidencyPersistenceError(
                "Residency evidence could not be committed."
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                if directory_descriptor is None:
                    os.unlink(temporary)
                else:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
            except OSError:
                pass

    def _record(
        self,
        key: Mapping[str, Any],
        *,
        outcome: str,
        phase: str,
        required_margin_gib: float,
        observed_at: float | None,
    ) -> dict[str, Any]:
        clean_key = _normalize_key(key)
        if outcome not in {"success", "oom"} or (
            outcome == "success" and phase != ""
        ) or (outcome == "oom" and phase not in _PHASES):
            raise ModelResidencyValidationError("Residency observation is invalid.")
        try:
            margin = float(required_margin_gib)
            timestamp = float(self._clock() if observed_at is None else observed_at)
        except (TypeError, ValueError):
            raise ModelResidencyValidationError("Residency observation is invalid.") from None
        if (
            not math.isfinite(margin) or margin < 0 or margin > 1024
            or not math.isfinite(timestamp) or timestamp < 0
        ):
            raise ModelResidencyValidationError("Residency observation is invalid.")
        record_id = _record_id(clean_key, outcome, phase)
        try:
            with self._lock, self._storage_guard._cross_process_lock_locked() as descriptor:
                payload = self._read(descriptor)
                previous = payload["records"].get(record_id)
                previous_observed = float(
                    previous["observed_at"] if previous else timestamp
                )
                record = {
                    "record_id": record_id,
                    "key": clean_key,
                    "outcome": outcome,
                    "phase": phase,
                    "first_observed_at": min(
                        float(previous["first_observed_at"])
                        if previous else timestamp,
                        timestamp,
                    ),
                    "observed_at": max(previous_observed, timestamp),
                    "sample_count": min(
                        1_000_000,
                        int(previous["sample_count"] if previous else 0) + 1,
                    ),
                    # An older severe OOM must not become a permanent margin
                    # after newer condition-local evidence replaces it.
                    "required_margin_gib": (
                        margin if timestamp >= previous_observed else
                        float(previous["required_margin_gib"])
                    ),
                    "source": "prior_run",
                    "evidence_version": SCHEMA_VERSION,
                }
                payload["records"][record_id] = record
                if len(payload["records"]) > self.max_records:
                    ordered = sorted(
                        payload["records"].items(),
                        key=lambda item: (
                            float(item[1]["observed_at"]), item[0],
                        ),
                    )
                    payload["records"] = dict(ordered[-self.max_records:])
                self._write(payload, descriptor)
                return json.loads(json.dumps(record))
        except ModelResidencyError:
            raise
        except Exception as error:
            from services.generation_names import GenerationNameStorageError
            if isinstance(error, GenerationNameStorageError):
                raise ModelResidencyPersistenceError(
                    "Residency evidence storage is unavailable."
                ) from error
            raise

    def record_success(
        self,
        key: Mapping[str, Any],
        *,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        return self._record(
            key, outcome="success", phase="", required_margin_gib=0.0,
            observed_at=observed_at,
        )

    def record_oom(
        self,
        key: Mapping[str, Any],
        *,
        phase: str,
        required_margin_gib: float = 1.0,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        return self._record(
            key, outcome="oom", phase=phase,
            required_margin_gib=required_margin_gib,
            observed_at=observed_at,
        )

    def _prior_records(self) -> list[dict[str, Any]]:
        try:
            with self._lock, self._storage_guard._cross_process_lock_locked() as descriptor:
                return list(self._read(descriptor)["records"].values())
        except ModelResidencyError:
            raise
        except Exception as error:
            from services.generation_names import GenerationNameStorageError
            if isinstance(error, GenerationNameStorageError):
                raise ModelResidencyPersistenceError(
                    "Residency evidence storage is unavailable."
                ) from error
            raise

    def snapshot(self) -> dict[str, Any]:
        """Return content-free counts, never private source material."""
        records = self._prior_records()
        return {
            "schema_version": SCHEMA_VERSION,
            "shipped_evidence_version": SHIPPED_EVIDENCE_VERSION,
            "shipped_records": len(self._shipped),
            "prior_run_records": len(records),
            "success_records": sum(item["outcome"] == "success" for item in records),
            "oom_records": sum(item["outcome"] == "oom" for item in records),
        }

    def recommend(
        self,
        key: Mapping[str, Any],
        *,
        now: float | None = None,
        max_neighbors: int = 4,
        max_distance: float = 2.5,
    ) -> dict[str, Any]:
        """Return exact evidence or one conservative compatible estimate."""
        clean_key = _normalize_key(key)
        timestamp = float(self._clock() if now is None else now)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ModelResidencyValidationError("Residency recommendation time is invalid.")
        try:
            neighbor_bound = int(max_neighbors)
            distance_bound = float(max_distance)
        except (TypeError, ValueError):
            raise ModelResidencyValidationError(
                "Residency neighbor policy is invalid."
            ) from None
        if (
            not 1 <= neighbor_bound <= 16
            or not math.isfinite(distance_bound)
            or not 0 < distance_bound <= 10
        ):
            raise ModelResidencyValidationError("Residency neighbor policy is invalid.")
        prior = self._prior_records()
        all_records = prior + list(self._shipped)
        active_ooms = [
            item for item in prior
            if item["outcome"] == "oom"
            and item["key"]["exact_key"] == clean_key["exact_key"]
            and 0 <= timestamp - float(item["observed_at"]) <= self.oom_ttl_seconds
        ]
        oom_margin = max(
            (float(item["required_margin_gib"]) for item in active_ooms),
            default=0.0,
        )
        exact_successes = [
            item for item in all_records
            if item["outcome"] == "success"
            and item["key"]["exact_key"] == clean_key["exact_key"]
        ]
        for source, confidence in (("prior_run", "high"), ("shipped", "medium")):
            chosen = next(
                (item for item in exact_successes if item["source"] == source),
                None,
            )
            if chosen is None:
                continue
            requested = float(clean_key["identity"]["settings"]["resident_budget_gib"])
            return {
                "status": "supported",
                "resident_budget_gib": max(0.0, requested - oom_margin),
                "confidence": "low" if active_ooms else confidence,
                "provenance": {
                    "kind": "exact",
                    "source": source,
                    "sample_count": int(chosen["sample_count"]),
                    "supporting_keys": [clean_key["exact_key"]],
                },
                "uncertainty": {
                    "resident_budget_gib": oom_margin,
                    "basis": (
                        "active_condition_oom" if active_ooms else "exact_observation"
                    ),
                },
                "active_oom_count": len(active_ooms),
                "oom_phases": sorted({item["phase"] for item in active_ooms}),
                "profile_advisory": "cost_policy" if active_ooms else "reuse",
            }

        successes = [
            item for item in all_records
            if item["outcome"] == "success"
            and item["key"]["compatibility_key"] == clean_key["compatibility_key"]
        ]
        nearby: list[tuple[float, dict[str, Any]]] = []
        for item in successes:
            distance = _distance(clean_key, item["key"])
            if 0 < distance <= distance_bound:
                nearby.append((distance, item))
        nearby.sort(key=lambda item: (item[0], item[1]["key"]["exact_key"]))
        for source in ("prior_run", "shipped"):
            candidates = [item for item in nearby if item[1]["source"] == source]
            if not candidates:
                continue
            candidates = candidates[:neighbor_bound]
            farthest = max(item[0] for item in candidates)
            uncertainty = 0.5 + 0.75 * farthest
            known_budgets = [
                float(item[1]["key"]["identity"]["settings"]["resident_budget_gib"])
                for item in candidates
            ]
            requested = float(clean_key["identity"]["settings"]["resident_budget_gib"])
            points = [_point(item[1]["key"]) for item in candidates]
            target = _point(clean_key)
            bracketed = len(points) >= 2 and all(
                min(point[index] for point in points) <= target[index]
                <= max(point[index] for point in points)
                for index in range(3)
            )
            confidence = "medium" if bracketed and farthest <= 1.5 else "low"
            return {
                "status": "supported",
                "resident_budget_gib": max(
                    0.0,
                    min(requested, min(known_budgets)) - uncertainty - oom_margin,
                ),
                "confidence": "low" if active_ooms else confidence,
                "provenance": {
                    "kind": "interpolation" if bracketed else "extrapolation",
                    "source": source,
                    "sample_count": sum(
                        int(item[1]["sample_count"]) for item in candidates
                    ),
                    "supporting_keys": [
                        item[1]["key"]["exact_key"] for item in candidates
                    ],
                },
                "uncertainty": {
                    "resident_budget_gib": uncertainty + oom_margin,
                    "basis": "nearby_compatible_points",
                },
                "active_oom_count": len(active_ooms),
                "oom_phases": sorted({item["phase"] for item in active_ooms}),
                "profile_advisory": "cost_policy",
            }
        return {
            "status": "unsupported",
            "resident_budget_gib": None,
            "confidence": "insufficient",
            "provenance": {
                "kind": "none", "source": "none", "sample_count": 0,
                "supporting_keys": [],
            },
            "uncertainty": {
                "resident_budget_gib": None,
                "basis": "unsupported_region",
            },
            "active_oom_count": len(active_ooms),
            "oom_phases": sorted({item["phase"] for item in active_ooms}),
            "profile_advisory": "profile",
        }


def choose_profile_action(
    recommendation: Mapping[str, Any],
    *,
    profiling_cost_seconds: float,
    recovery_cost_seconds: float,
    failure_probability: float | None = None,
) -> dict[str, Any]:
    """Compare profiling wall time with risk-adjusted exact-job recovery cost."""
    try:
        profile_cost = float(profiling_cost_seconds)
        recovery_cost = float(recovery_cost_seconds)
    except (TypeError, ValueError):
        raise ModelResidencyValidationError("Residency cost policy is invalid.") from None
    if (
        not math.isfinite(profile_cost) or profile_cost < 0
        or not math.isfinite(recovery_cost) or recovery_cost < 0
    ):
        raise ModelResidencyValidationError("Residency cost policy is invalid.")
    status = str(recommendation.get("status") or "")
    confidence = str(recommendation.get("confidence") or "insufficient")
    if failure_probability is None:
        probability = {
            "high": 0.02, "medium": 0.08, "low": 0.20,
            "insufficient": 1.0,
        }.get(confidence, 1.0)
        if int(recommendation.get("active_oom_count") or 0) > 0:
            probability = max(probability, 0.35)
    else:
        try:
            probability = float(failure_probability)
        except (TypeError, ValueError):
            raise ModelResidencyValidationError("Residency cost policy is invalid.") from None
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ModelResidencyValidationError("Residency cost policy is invalid.")
    expected_failure_cost = probability * recovery_cost
    if status != "supported":
        decision = "profile"
        reason = "unsupported_region"
    elif profile_cost < expected_failure_cost:
        decision = "profile"
        reason = "profiling_cost_below_expected_recovery"
    else:
        decision = "use_estimate"
        reason = "estimate_cost_below_profiling"
    return {
        "decision": decision,
        "reason": reason,
        "profiling_cost_seconds": profile_cost,
        "failure_probability": probability,
        "expected_failure_cost_seconds": expected_failure_cost,
    }


T = TypeVar("T")


@dataclass
class _Flight:
    event: threading.Event = field(default_factory=threading.Event)
    owner_thread_id: int = field(default_factory=threading.get_ident)
    result: Any = None
    error: BaseException | None = None


class ResidencySingleflight:
    """Coalesce concurrent work for one exact residency key in one process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}

    def run(self, exact_key: str, operation: Callable[[], T]) -> T:
        if (
            not isinstance(exact_key, str)
            or not exact_key.startswith(f"residency:v{SCHEMA_VERSION}:")
            or len(exact_key) > 128
        ):
            raise ModelResidencyValidationError("Residency singleflight key is invalid.")
        if not callable(operation):
            raise ModelResidencyValidationError("Residency singleflight operation is invalid.")
        with self._lock:
            flight = self._flights.get(exact_key)
            leader = flight is None
            if leader:
                flight = _Flight()
                self._flights[exact_key] = flight
            elif flight.owner_thread_id == threading.get_ident():
                raise ModelResidencyError(
                    "Residency singleflight cannot re-enter the same exact key."
                )
        assert flight is not None
        if not leader:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return flight.result
        try:
            flight.result = operation()
            return flight.result
        except BaseException as error:
            flight.error = error
            raise
        finally:
            flight.event.set()
            with self._lock:
                if self._flights.get(exact_key) is flight:
                    del self._flights[exact_key]


__all__ = [
    "DEFAULT_MAX_RECORDS", "DEFAULT_OOM_TTL_SECONDS", "SCHEMA_VERSION",
    "SHIPPED_EVIDENCE_VERSION", "ModelResidencyError",
    "ModelResidencyEvidenceStore", "ModelResidencyPersistenceError",
    "ModelResidencyValidationError", "ResidencySingleflight",
    "build_residency_key", "choose_profile_action",
]
