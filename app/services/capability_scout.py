"""Pure, local state core for Maestro capability scouting.

This module owns metadata only.  It does not research, download, benchmark,
invoke a model, or modify an implementation.  Callers must supply exact
provenance and benchmark receipts produced by separately authorized code.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit
import uuid


SCHEMA_VERSION = 1
BENCHMARK_RECEIPT_SCHEMA_VERSION = 1
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_CANDIDATES = 256
MAX_EVIDENCE_CLAIMS = 64
MAX_BENCHMARK_RECEIPTS = 32
MAX_PARENT_RELATIONS = 32
MAX_ROLLBACK_RECORDS = 64
MAX_ACTIONS = 512
MAX_HISTORY = 512
MAX_NONCES = 128
MAX_ARCHIVE_FILES = 4_096
MAX_AGGREGATE_FIXTURES = 128

CANDIDATE_KINDS = frozenset({
    "runtime_speedup", "model_generation", "model", "tune", "lora",
    "config", "workflow", "style",
})
LIFECYCLE_STATES = frozenset({
    "discovered", "researching", "evidence_ready", "watching", "rejected",
    "benchmark_queued", "recommended", "benchmarked", "selection_ready",
    "owner_selected", "implementing", "verification_required",
    "promotion_ready", "promoted", "action_required",
})
LIFECYCLE_TRANSITIONS = {
    "discovered": frozenset({"researching"}),
    "researching": frozenset({"evidence_ready"}),
    "evidence_ready": frozenset({
        "watching", "rejected", "benchmark_queued", "recommended",
    }),
    "watching": frozenset({"researching", "rejected"}),
    "benchmark_queued": frozenset({"benchmarked", "rejected"}),
    "recommended": frozenset({"benchmark_queued", "selection_ready", "rejected"}),
    "benchmarked": frozenset({"selection_ready", "rejected"}),
    "selection_ready": frozenset({"owner_selected", "rejected"}),
    "owner_selected": frozenset({"implementing", "rejected"}),
    "implementing": frozenset({"verification_required"}),
    "verification_required": frozenset({"promotion_ready", "implementing"}),
    "promotion_ready": frozenset({"promoted", "verification_required"}),
    "rejected": frozenset(),
    "promoted": frozenset(),
    "action_required": frozenset(),
}

PRE_TEST_GATE_FIELDS = (
    "evaluation_rights", "authorization", "privacy_outbound",
    "provider_terms", "spend_approval", "download_approval",
)
POST_TEST_GATE_FIELDS = (
    "hosting", "commercial", "redistribution", "license_terms",
    "applicable_terms", "compatibility", "owner_selected_revision",
    "regression",
)
GATE_STATUSES = frozenset({"unknown", "pending", "approved", "not_required", "denied"})
PASSING_GATE_STATUSES = frozenset({"approved", "not_required"})

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TEMP_RE = re.compile(r"\.state\.json\.[0-9a-f]{32}\.tmp\Z")
_PRIVATE_ARTIFACT_REF_RE = re.compile(
    r"(?:artifact|local-private):[A-Za-z0-9][A-Za-z0-9._~-]{0,191}\Z"
)
_PROTECTED_TRANSITION_TARGETS = frozenset({"benchmarked", "owner_selected", "promoted"})
_SECURE_ROOTED_IO_SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and all(
        operation in os.supports_dir_fd
        for operation in (os.open, os.mkdir, os.stat, os.unlink, os.link)
    )
)


class CapabilityScoutError(RuntimeError):
    """Base capability-scout state error."""


class CapabilityScoutCorrupt(CapabilityScoutError):
    """Durable state was malformed, tampered with, or unsafe to read."""


class CapabilityScoutConflict(CapabilityScoutError):
    """The caller used a stale revision or an invalid lifecycle transition."""


class CapabilityScoutLocked(CapabilityScoutError):
    """Another writer is active, or an abandoned writer needs review."""


class CapabilityGateBlocked(CapabilityScoutError):
    """An operation is blocked by one or more explicit gate decisions."""

    def __init__(self, stage: str, blockers: Sequence[str]):
        self.stage = stage
        self.blockers = tuple(blockers)
        super().__init__(f"{stage} gate blocked: {', '.join(self.blockers)}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime | str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise CapabilityScoutCorrupt("stored timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise CapabilityScoutCorrupt("stored timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _clean_text(value: Any, *, limit: int, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    result = " ".join(value.split())
    if (not result and not allow_empty) or len(result) > limit:
        raise ValueError(f"{field} must contain at most {limit} characters")
    return result


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} is not a valid opaque identifier")
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _private_artifact_ref(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _PRIVATE_ARTIFACT_REF_RE.fullmatch(value) is None
    ):
        raise ValueError(
            "private_artifact_ref must be an opaque artifact or local-private reference"
        )
    return value


def _canonical_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _digest(value: Mapping[str, Any] | Sequence[Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _strict_json(raw: bytes) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CapabilityScoutCorrupt("stored JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityScoutCorrupt("capability scout state is not valid JSON") from error
    if not isinstance(value, dict):
        raise CapabilityScoutCorrupt("capability scout state must be an object")
    return value


def _bounded_concrete_sequence(value: Any, *, limit: int, field: str) -> list[Any]:
    if type(value) not in {list, tuple}:
        raise ValueError(f"{field} must be a concrete list or tuple")
    if len(value) > limit:
        raise ValueError(f"{field} exceeds its {limit}-item limit")
    return list(value)


def _authority_commitment(owner_key: Any, session_key: Any, authority_key: Any) -> str:
    values = {
        "owner_key": _clean_text(owner_key, limit=256, field="owner_key"),
        "session_key": _clean_text(session_key, limit=256, field="session_key"),
        "authority_key": _clean_text(authority_key, limit=256, field="authority_key"),
    }
    return _digest(values)


def _empty_gate(fields: Sequence[str], *, implementation_bound: bool = False) -> dict[str, Any]:
    return {
        "revision": 0,
        "decisions": {
            field: {
                "status": "unknown",
                "basis_revision": None,
                **({"implementation_binding_revision": None} if implementation_bound else {}),
            }
            for field in fields
        },
    }


def _evidence_revision(claims: Sequence[Mapping[str, Any]]) -> str:
    return _digest([
        {key: value for key, value in claim.items() if key != "private_notes"}
        for claim in claims
    ])


def _default_state() -> dict[str, Any]:
    state = {
        "schema_version": SCHEMA_VERSION,
        "store_revision": 0,
        "updated_at": None,
        "candidates": {},
        "actions": {},
        "selection_nonces": {},
        "history": [],
    }
    return _with_integrity(state)


def _with_integrity(state: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(state))
    result.pop("integrity", None)
    result["integrity"] = {"algorithm": "sha256", "digest": _digest(result)}
    return result


def _expect_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise CapabilityScoutCorrupt(f"{label} has an invalid shape")


def _validate_gate(
    value: Any,
    fields: Sequence[str],
    label: str,
    *,
    implementation_bound: bool = False,
) -> None:
    if not isinstance(value, Mapping):
        raise CapabilityScoutCorrupt(f"{label} must be an object")
    _expect_keys(value, {"revision", "decisions"}, label)
    if not isinstance(value["revision"], int) or isinstance(value["revision"], bool) or value["revision"] < 0:
        raise CapabilityScoutCorrupt(f"{label} revision is invalid")
    decisions = value["decisions"]
    if not isinstance(decisions, Mapping) or set(decisions) != set(fields):
        raise CapabilityScoutCorrupt(f"{label} decisions are incomplete")
    for field in fields:
        decision = decisions[field]
        if not isinstance(decision, Mapping):
            raise CapabilityScoutCorrupt(f"{label}.{field} must be an object")
        expected = {"status", "basis_revision"}
        if implementation_bound:
            expected.add("implementation_binding_revision")
        _expect_keys(decision, expected, f"{label}.{field}")
        if decision["status"] not in GATE_STATUSES:
            raise CapabilityScoutCorrupt(f"{label}.{field} status is invalid")
        basis = decision["basis_revision"]
        if basis is not None:
            try:
                _identifier(basis, f"{label}.{field}.basis_revision")
            except ValueError as error:
                raise CapabilityScoutCorrupt(str(error)) from error
        if decision["status"] in PASSING_GATE_STATUSES and basis is None:
            raise CapabilityScoutCorrupt(f"{label}.{field} passing status needs a basis revision")
        if implementation_bound:
            binding = decision["implementation_binding_revision"]
            if binding is not None:
                try:
                    _sha256(binding, f"{label}.{field}.implementation_binding_revision")
                except ValueError as error:
                    raise CapabilityScoutCorrupt(str(error)) from error
            if decision["status"] in PASSING_GATE_STATUSES and binding is None:
                raise CapabilityScoutCorrupt(f"{label}.{field} passing status needs an implementation binding")


def _public_source_uri(value: str) -> str:
    """Return origin plus a conservative public path, or an opaque label."""
    opaque = f"provenance:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return opaque
        host = parsed.hostname.encode("idna").decode("ascii")
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        origin = urlunsplit((parsed.scheme, host, "", "", ""))
        # Paths can contain repository-private identifiers even when they look
        # syntactically ordinary.  Never project them; the digest preserves an
        # exact correlatable provenance label without disclosure.
        return f"{origin} [{opaque}]"
    except (UnicodeError, ValueError):
        return opaque


def _build_benchmark_requirement(candidate: Mapping[str, Any]) -> dict[str, Any]:
    matrix = []
    for dimension in candidate["capability_dimensions"]:
        for fixture_id in dimension["fixture_ids"]:
            matrix.append({
                "dimension_id": dimension["dimension_id"],
                "fixture_id": fixture_id,
                "required_metrics": deepcopy(dimension["required_metrics"]),
            })
    if not matrix or any(not item["required_metrics"] for item in matrix):
        raise CapabilityScoutConflict(
            "benchmark admission requires fixtures and metrics for every declared dimension"
        )
    requirement = {
        "candidate_revision": candidate["revision"],
        "evidence_set_revision": candidate["evidence_set_revision"],
        "pre_test_gate_revision": candidate["pre_test_gate"]["revision"],
        "pre_test_gate_sha256": _digest(candidate["pre_test_gate"]),
        "fixture_matrix": matrix,
    }
    requirement["requirement_revision"] = _digest(requirement)
    return requirement


def _validate_benchmark_requirement(value: Any, candidate: Mapping[str, Any]) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise CapabilityScoutCorrupt("benchmark requirement is invalid")
    _expect_keys(value, {
        "candidate_revision", "evidence_set_revision", "pre_test_gate_revision",
        "pre_test_gate_sha256", "fixture_matrix", "requirement_revision",
    }, "benchmark requirement")
    if (
        not isinstance(value["candidate_revision"], int)
        or isinstance(value["candidate_revision"], bool)
        or value["candidate_revision"] < 1
        or not isinstance(value["pre_test_gate_revision"], int)
        or isinstance(value["pre_test_gate_revision"], bool)
        or value["pre_test_gate_revision"] < 1
    ):
        raise CapabilityScoutCorrupt("benchmark requirement revisions are invalid")
    try:
        _sha256(value["evidence_set_revision"], "benchmark evidence revision")
        _sha256(value["pre_test_gate_sha256"], "benchmark gate digest")
        _sha256(value["requirement_revision"], "benchmark requirement revision")
    except ValueError as error:
        raise CapabilityScoutCorrupt(str(error)) from error
    matrix = value["fixture_matrix"]
    if not isinstance(matrix, list) or not 1 <= len(matrix) <= 128:
        raise CapabilityScoutCorrupt("benchmark fixture matrix is invalid")
    pairs: set[tuple[str, str]] = set()
    for item in matrix:
        if not isinstance(item, Mapping):
            raise CapabilityScoutCorrupt("benchmark fixture matrix item is invalid")
        _expect_keys(item, {"dimension_id", "fixture_id", "required_metrics"}, "fixture matrix item")
        try:
            dimension_id = _identifier(item["dimension_id"], "matrix dimension_id")
            fixture_id = _identifier(item["fixture_id"], "matrix fixture_id")
            metric_specs = item["required_metrics"]
        except (TypeError, ValueError) as error:
            raise CapabilityScoutCorrupt("benchmark fixture matrix values are invalid") from error
        if not isinstance(metric_specs, list) or not 1 <= len(metric_specs) <= 64:
            raise CapabilityScoutCorrupt("benchmark required metrics are invalid")
        metrics = []
        for metric in metric_specs:
            if not isinstance(metric, Mapping):
                raise CapabilityScoutCorrupt("benchmark required metric is invalid")
            _expect_keys(metric, {"metric_id", "unit"}, "benchmark required metric")
            try:
                metrics.append((
                    _identifier(metric["metric_id"], "required metric_id"),
                    _identifier(metric["unit"], "required metric unit"),
                ))
            except ValueError as error:
                raise CapabilityScoutCorrupt(str(error)) from error
        if len(set(metrics)) != len(metrics) or len({item[0] for item in metrics}) != len(metrics):
            raise CapabilityScoutCorrupt("benchmark required metrics must be unique")
        if (dimension_id, fixture_id) in pairs:
            raise CapabilityScoutCorrupt("benchmark fixture matrix entries must be unique")
        pairs.add((dimension_id, fixture_id))
    expected = {key: item for key, item in value.items() if key != "requirement_revision"}
    if value["requirement_revision"] != _digest(expected):
        raise CapabilityScoutCorrupt("benchmark requirement digest does not match")
    if value["evidence_set_revision"] != candidate["evidence_set_revision"]:
        raise CapabilityScoutCorrupt("benchmark requirement evidence binding is stale")
    expected_matrix = [
        {
            "dimension_id": dimension["dimension_id"],
            "fixture_id": fixture_id,
            "required_metrics": deepcopy(dimension["required_metrics"]),
        }
        for dimension in candidate["capability_dimensions"]
        for fixture_id in dimension["fixture_ids"]
    ]
    if matrix != expected_matrix:
        raise CapabilityScoutCorrupt(
            "benchmark requirement is not the exact declared capability matrix"
        )


def _implementation_binding(candidate: Mapping[str, Any]) -> str | None:
    selection = candidate.get("owner_selection")
    rollbacks = candidate.get("rollback_lineage") or []
    if selection is None or not rollbacks:
        return None
    current = rollbacks[-1]
    return _digest({
        "candidate_revision": selection["candidate_revision"],
        "evidence_set_revision": selection["evidence_set_revision"],
        "identity_sha256": selection["identity_sha256"],
        "owner_audit_commitment": selection["owner_audit_commitment"],
        "implementation_revision": current["implementation_revision"],
        "rollback_record_revision": current["record_revision"],
    })


def _validate_candidate(candidate: Any) -> None:
    if not isinstance(candidate, Mapping):
        raise CapabilityScoutCorrupt("candidate must be an object")
    _expect_keys(candidate, {
        "schema_version", "candidate_id", "kind", "identity", "revision",
        "lifecycle", "evaluation_disposition", "evaluation_disposition_binding",
        "capability_dimensions", "evidence_claims",
        "evidence_set_revision", "pre_test_gate", "post_test_gate",
        "benchmark_requirement", "benchmark_receipts", "owner_selection", "action_required",
        "action_history", "rollback_lineage", "private_notes",
    }, "candidate")
    if candidate["schema_version"] != SCHEMA_VERSION or candidate["kind"] not in CANDIDATE_KINDS:
        raise CapabilityScoutCorrupt("candidate version or kind is invalid")
    try:
        _identifier(candidate["candidate_id"], "candidate_id")
    except ValueError as error:
        raise CapabilityScoutCorrupt(str(error)) from error
    if not isinstance(candidate["revision"], int) or isinstance(candidate["revision"], bool) or candidate["revision"] < 1:
        raise CapabilityScoutCorrupt("candidate revision is invalid")
    if candidate["lifecycle"] not in LIFECYCLE_STATES:
        raise CapabilityScoutCorrupt("candidate lifecycle is invalid")
    if candidate["evaluation_disposition"] not in {
        "undetermined", "evidence_sufficient", "benchmark_required",
    }:
        raise CapabilityScoutCorrupt("candidate evaluation disposition is invalid")
    identity = candidate["identity"]
    if not isinstance(identity, Mapping):
        raise CapabilityScoutCorrupt("candidate identity is invalid")
    _expect_keys(identity, {
        "canonical_id", "artifact_id", "source_uri", "source_revision",
        "identity_sha256", "parent_relations",
    }, "candidate identity")
    try:
        _identifier(identity["canonical_id"], "canonical_id")
        _identifier(identity["artifact_id"], "artifact_id")
        _identifier(identity["source_revision"], "source_revision")
        _sha256(identity["identity_sha256"], "identity_sha256")
        _clean_text(identity["source_uri"], limit=500, field="source_uri")
    except ValueError as error:
        raise CapabilityScoutCorrupt(str(error)) from error
    parents = identity["parent_relations"]
    if not isinstance(parents, list) or len(parents) > MAX_PARENT_RELATIONS:
        raise CapabilityScoutCorrupt("candidate parent relations are invalid")
    for relation in parents:
        if not isinstance(relation, Mapping):
            raise CapabilityScoutCorrupt("candidate parent relation is invalid")
        _expect_keys(relation, {"relation", "candidate_id", "candidate_revision"}, "parent relation")
        try:
            _identifier(relation["relation"], "parent relation")
            _identifier(relation["candidate_id"], "parent candidate_id")
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if not isinstance(relation["candidate_revision"], int) or isinstance(relation["candidate_revision"], bool) or relation["candidate_revision"] < 1:
            raise CapabilityScoutCorrupt("parent candidate revision is invalid")
    identity_without_digest = {key: value for key, value in identity.items() if key != "identity_sha256"}
    if identity["identity_sha256"] != _digest(identity_without_digest):
        raise CapabilityScoutCorrupt("candidate identity digest does not match")

    disposition_binding = candidate["evaluation_disposition_binding"]
    if candidate["evaluation_disposition"] == "undetermined":
        if disposition_binding is not None:
            raise CapabilityScoutCorrupt(
                "undetermined evaluation disposition cannot have a transition binding"
            )
    else:
        if not isinstance(disposition_binding, Mapping):
            raise CapabilityScoutCorrupt(
                "evaluation disposition transition binding is missing"
            )
        _expect_keys(disposition_binding, {
            "disposition", "transition_target", "transition_revision",
            "evidence_set_revision", "identity_sha256", "binding_revision",
        }, "evaluation disposition binding")
        try:
            _sha256(
                disposition_binding["evidence_set_revision"],
                "disposition evidence revision",
            )
            _sha256(
                disposition_binding["identity_sha256"],
                "disposition identity digest",
            )
            _sha256(
                disposition_binding["binding_revision"],
                "disposition binding revision",
            )
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        expected_target = {
            "evidence_sufficient": "recommended",
            "benchmark_required": "benchmark_queued",
        }[candidate["evaluation_disposition"]]
        transition_revision = disposition_binding["transition_revision"]
        if (
            disposition_binding["disposition"]
            != candidate["evaluation_disposition"]
            or disposition_binding["transition_target"] != expected_target
            or not isinstance(transition_revision, int)
            or isinstance(transition_revision, bool)
            or not 1 <= transition_revision <= candidate["revision"]
            or disposition_binding["evidence_set_revision"]
            != candidate["evidence_set_revision"]
            or disposition_binding["identity_sha256"]
            != identity["identity_sha256"]
        ):
            raise CapabilityScoutCorrupt(
                "evaluation disposition transition binding does not match"
            )
        expected_binding = {
            key: value
            for key, value in disposition_binding.items()
            if key != "binding_revision"
        }
        if disposition_binding["binding_revision"] != _digest(expected_binding):
            raise CapabilityScoutCorrupt(
                "evaluation disposition binding revision does not match"
            )

    dimensions = candidate["capability_dimensions"]
    if not isinstance(dimensions, list) or not 1 <= len(dimensions) <= 32:
        raise CapabilityScoutCorrupt("capability dimensions are invalid")
    dimension_ids: set[str] = set()
    aggregate_fixture_count = 0
    for dimension in dimensions:
        if not isinstance(dimension, Mapping):
            raise CapabilityScoutCorrupt("capability dimension is invalid")
        _expect_keys(
            dimension,
            {"dimension_id", "fixture_ids", "required_metrics"},
            "capability dimension",
        )
        try:
            dimension_id = _identifier(dimension["dimension_id"], "dimension_id")
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if dimension_id in dimension_ids:
            raise CapabilityScoutCorrupt("capability dimensions must be unique")
        dimension_ids.add(dimension_id)
        fixtures = dimension["fixture_ids"]
        if not isinstance(fixtures, list) or len(fixtures) > 64:
            raise CapabilityScoutCorrupt("fixture IDs are invalid")
        try:
            validated_fixtures = [_identifier(item, "fixture_id") for item in fixtures]
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if len(set(validated_fixtures)) != len(validated_fixtures):
            raise CapabilityScoutCorrupt("fixture IDs must be unique")
        aggregate_fixture_count += len(validated_fixtures)
        metrics = dimension["required_metrics"]
        if not isinstance(metrics, list) or len(metrics) > 64:
            raise CapabilityScoutCorrupt("required metrics are invalid")
        validated_metrics = []
        for metric in metrics:
            if not isinstance(metric, Mapping):
                raise CapabilityScoutCorrupt("required metric is invalid")
            _expect_keys(metric, {"metric_id", "unit"}, "required metric")
            try:
                validated_metrics.append((
                    _identifier(metric["metric_id"], "required metric_id"),
                    _identifier(metric["unit"], "required metric unit"),
                ))
            except ValueError as error:
                raise CapabilityScoutCorrupt(str(error)) from error
        if (
            len(set(validated_metrics)) != len(validated_metrics)
            or len({item[0] for item in validated_metrics}) != len(validated_metrics)
        ):
            raise CapabilityScoutCorrupt("required metrics must be unique")
    if aggregate_fixture_count > MAX_AGGREGATE_FIXTURES:
        raise CapabilityScoutCorrupt(
            "candidate aggregate fixture matrix exceeds the benchmark limit"
        )

    claims = candidate["evidence_claims"]
    if not isinstance(claims, list) or len(claims) > MAX_EVIDENCE_CLAIMS:
        raise CapabilityScoutCorrupt("evidence claims are invalid")
    claim_ids: set[str] = set()
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise CapabilityScoutCorrupt("evidence claim is invalid")
        _expect_keys(claim, {
            "claim_id", "claim_type", "summary", "source_uri",
            "source_revision", "evidence_revision", "source_sha256",
            "observed_at", "private_notes",
        }, "evidence claim")
        try:
            claim_id = _identifier(claim["claim_id"], "claim_id")
            _identifier(claim["claim_type"], "claim_type")
            _identifier(claim["source_revision"], "evidence source_revision")
            _identifier(claim["evidence_revision"], "evidence_revision")
            _sha256(claim["source_sha256"], "source_sha256")
            _clean_text(claim["source_uri"], limit=500, field="evidence source_uri")
            _clean_text(claim["summary"], limit=500, field="claim summary")
            _clean_text(claim["private_notes"], limit=1000, field="private_notes", allow_empty=True)
            _parse_utc(claim["observed_at"])
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if claim_id in claim_ids:
            raise CapabilityScoutCorrupt("evidence claim IDs must be unique")
        claim_ids.add(claim_id)
    try:
        _sha256(candidate["evidence_set_revision"], "evidence_set_revision")
        _clean_text(candidate["private_notes"], limit=2000, field="private_notes", allow_empty=True)
    except ValueError as error:
        raise CapabilityScoutCorrupt(str(error)) from error
    if candidate["evidence_set_revision"] != _evidence_revision(claims):
        raise CapabilityScoutCorrupt("evidence set revision does not match")
    _validate_gate(candidate["pre_test_gate"], PRE_TEST_GATE_FIELDS, "pre_test_gate")
    _validate_gate(
        candidate["post_test_gate"],
        POST_TEST_GATE_FIELDS,
        "post_test_gate",
        implementation_bound=True,
    )
    _validate_benchmark_requirement(candidate["benchmark_requirement"], candidate)

    receipts = candidate["benchmark_receipts"]
    if not isinstance(receipts, list) or len(receipts) > MAX_BENCHMARK_RECEIPTS:
        raise CapabilityScoutCorrupt("benchmark receipts are invalid")
    for receipt in receipts:
        _validate_benchmark_receipt(receipt, candidate["candidate_id"])
    receipt_ids = [receipt["receipt_id"] for receipt in receipts]
    if len(set(receipt_ids)) != len(receipt_ids):
        raise CapabilityScoutCorrupt("benchmark receipt IDs must be unique")
    selection = candidate["owner_selection"]
    if selection is not None:
        if not isinstance(selection, Mapping):
            raise CapabilityScoutCorrupt("owner selection is invalid")
        _expect_keys(selection, {
            "selection_id", "candidate_revision", "evidence_set_revision",
            "identity_sha256", "owner_audit_commitment", "selected_at",
            "binding_revision",
        }, "owner selection")
        try:
            _identifier(selection["selection_id"], "selection_id")
            _sha256(selection["evidence_set_revision"], "selected evidence revision")
            _sha256(selection["identity_sha256"], "selected identity digest")
            _sha256(selection["owner_audit_commitment"], "owner audit commitment")
            _sha256(selection["binding_revision"], "selection binding revision")
            _parse_utc(selection["selected_at"])
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if not isinstance(selection["candidate_revision"], int) or isinstance(selection["candidate_revision"], bool) or selection["candidate_revision"] < 1:
            raise CapabilityScoutCorrupt("selected candidate revision is invalid")
        expected_binding = _digest({key: value for key, value in selection.items() if key != "binding_revision"})
        if selection["binding_revision"] != expected_binding:
            raise CapabilityScoutCorrupt("owner selection binding does not match")
        if selection["evidence_set_revision"] != candidate["evidence_set_revision"] or selection["identity_sha256"] != identity["identity_sha256"]:
            raise CapabilityScoutCorrupt("owner selection no longer matches the candidate")

    action_required = candidate["action_required"]
    if action_required is not None:
        if candidate["lifecycle"] != "action_required" or not isinstance(action_required, Mapping):
            raise CapabilityScoutCorrupt("action-required lifecycle record is invalid")
        _expect_keys(action_required, {"action_id", "from_state", "resume_state", "blocker_kind"}, "action-required record")
        try:
            _identifier(action_required["action_id"], "action_id")
            _identifier(action_required["blocker_kind"], "blocker_kind")
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if action_required["from_state"] not in LIFECYCLE_STATES - {"action_required"} or action_required["resume_state"] not in LIFECYCLE_STATES - {"action_required"}:
            raise CapabilityScoutCorrupt("action-required resume state is invalid")
        if action_required["resume_state"] in _PROTECTED_TRANSITION_TARGETS:
            raise CapabilityScoutCorrupt(
                "action-required resume cannot bypass a dedicated protected operation"
            )
        if (
            action_required["resume_state"] != action_required["from_state"]
            and action_required["resume_state"]
            not in LIFECYCLE_TRANSITIONS[action_required["from_state"]]
        ):
            raise CapabilityScoutCorrupt(
                "action-required resume is not reachable from its recorded state"
            )
    elif candidate["lifecycle"] == "action_required":
        raise CapabilityScoutCorrupt("action-required lifecycle lacks its blocker record")
    action_history = candidate["action_history"]
    if not isinstance(action_history, list) or len(action_history) > MAX_ACTIONS:
        raise CapabilityScoutCorrupt("candidate action history is invalid")
    try:
        action_ids = [_identifier(item, "action history ID") for item in action_history]
    except ValueError as error:
        raise CapabilityScoutCorrupt(str(error)) from error
    if len(set(action_ids)) != len(action_ids):
        raise CapabilityScoutCorrupt("candidate action history must be unique")
    rollbacks = candidate["rollback_lineage"]
    if not isinstance(rollbacks, list) or len(rollbacks) > MAX_ROLLBACK_RECORDS:
        raise CapabilityScoutCorrupt("rollback lineage is invalid")
    for index, record in enumerate(rollbacks):
        if not isinstance(record, Mapping):
            raise CapabilityScoutCorrupt("rollback record is invalid")
        _expect_keys(record, {
            "implementation_revision", "previous_revision", "artifact_ids",
            "candidate_revision", "evidence_set_revision",
            "owner_selection_binding_revision", "record_revision", "recorded_at",
            "reason", "private_notes",
        }, "rollback record")
        try:
            _identifier(record["implementation_revision"], "implementation_revision")
            _identifier(record["previous_revision"], "previous_revision")
            _sha256(record["evidence_set_revision"], "rollback evidence revision")
            _sha256(record["owner_selection_binding_revision"], "rollback owner binding")
            _sha256(record["record_revision"], "rollback record revision")
            _clean_text(record["reason"], limit=500, field="rollback reason")
            _clean_text(record["private_notes"], limit=1000, field="private_notes", allow_empty=True)
            _parse_utc(record["recorded_at"])
            artifact_ids = [_identifier(item, "rollback artifact_id") for item in record["artifact_ids"]]
        except (TypeError, ValueError) as error:
            raise CapabilityScoutCorrupt("rollback record values are invalid") from error
        if not artifact_ids or len(artifact_ids) > 32 or len(set(artifact_ids)) != len(artifact_ids):
            raise CapabilityScoutCorrupt("rollback artifact IDs are invalid")
        if record["implementation_revision"] == record["previous_revision"]:
            raise CapabilityScoutCorrupt("rollback revision cannot point to itself")
        if (
            index > 0
            and record["previous_revision"]
            != rollbacks[index - 1]["implementation_revision"]
        ):
            raise CapabilityScoutCorrupt("rollback lineage is not continuous")
        if not isinstance(record["candidate_revision"], int) or isinstance(record["candidate_revision"], bool) or record["candidate_revision"] < 1:
            raise CapabilityScoutCorrupt("rollback candidate revision is invalid")
        expected_record = {key: value for key, value in record.items() if key not in {"record_revision", "private_notes"}}
        if record["record_revision"] != _digest(expected_record):
            raise CapabilityScoutCorrupt("rollback record revision does not match")
        selection = candidate["owner_selection"]
        if (
            selection is None
            or record["candidate_revision"] != selection["candidate_revision"]
            or record["evidence_set_revision"] != selection["evidence_set_revision"]
            or record["owner_selection_binding_revision"] != selection["binding_revision"]
        ):
            raise CapabilityScoutCorrupt("rollback record binding does not match owner selection")
    _validate_candidate_semantics(candidate)


def _validate_benchmark_receipt(receipt: Any, candidate_id: str) -> None:
    if not isinstance(receipt, Mapping):
        raise CapabilityScoutCorrupt("benchmark receipt is invalid")
    _expect_keys(receipt, {
        "schema_version", "receipt_id", "candidate_id", "candidate_revision",
        "evidence_set_revision", "requirement_revision",
        "pre_test_gate_revision", "pre_test_gate_sha256",
        "environment_revision", "fixture_results", "started_at",
        "completed_at", "result_sha256", "private_artifact_ref",
    }, "benchmark receipt")
    try:
        if receipt["schema_version"] != BENCHMARK_RECEIPT_SCHEMA_VERSION:
            raise ValueError("benchmark receipt version is invalid")
        _identifier(receipt["receipt_id"], "receipt_id")
        if receipt["candidate_id"] != candidate_id:
            raise ValueError("benchmark receipt candidate does not match")
        _sha256(receipt["evidence_set_revision"], "receipt evidence revision")
        _sha256(receipt["requirement_revision"], "requirement_revision")
        _sha256(receipt["pre_test_gate_sha256"], "pre_test_gate_sha256")
        _identifier(receipt["environment_revision"], "environment_revision")
        _sha256(receipt["result_sha256"], "result_sha256")
        _private_artifact_ref(receipt["private_artifact_ref"])
        started = _parse_utc(receipt["started_at"])
        completed = _parse_utc(receipt["completed_at"])
    except (TypeError, ValueError) as error:
        raise CapabilityScoutCorrupt("benchmark receipt values are invalid") from error
    if completed < started:
        raise CapabilityScoutCorrupt("benchmark receipt timing is invalid")
    if (
        not isinstance(receipt["candidate_revision"], int)
        or isinstance(receipt["candidate_revision"], bool)
        or receipt["candidate_revision"] < 1
        or not isinstance(receipt["pre_test_gate_revision"], int)
        or isinstance(receipt["pre_test_gate_revision"], bool)
        or receipt["pre_test_gate_revision"] < 1
    ):
        raise CapabilityScoutCorrupt("benchmark receipt candidate revision is invalid")
    results = receipt["fixture_results"]
    if not isinstance(results, list) or not 1 <= len(results) <= 128:
        raise CapabilityScoutCorrupt("benchmark fixture results are invalid")
    result_pairs: set[tuple[str, str]] = set()
    for result in results:
        if not isinstance(result, Mapping):
            raise CapabilityScoutCorrupt("benchmark fixture result is invalid")
        _expect_keys(result, {"dimension_id", "fixture_id", "metrics"}, "benchmark fixture result")
        try:
            dimension_id = _identifier(result["dimension_id"], "result dimension_id")
            fixture_id = _identifier(result["fixture_id"], "result fixture_id")
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if (dimension_id, fixture_id) in result_pairs:
            raise CapabilityScoutCorrupt("benchmark fixture results must be unique")
        result_pairs.add((dimension_id, fixture_id))
        metrics = result["metrics"]
        if not isinstance(metrics, list) or not 1 <= len(metrics) <= 64:
            raise CapabilityScoutCorrupt("benchmark receipt metrics are invalid")
        metric_ids: set[str] = set()
        for metric in metrics:
            if not isinstance(metric, Mapping):
                raise CapabilityScoutCorrupt("benchmark metric is invalid")
            _expect_keys(metric, {"metric_id", "unit", "value", "sample_count"}, "benchmark metric")
            try:
                metric_id = _identifier(metric["metric_id"], "metric_id")
                _identifier(metric["unit"], "metric unit")
            except ValueError as error:
                raise CapabilityScoutCorrupt(str(error)) from error
            if metric_id in metric_ids:
                raise CapabilityScoutCorrupt("benchmark metric IDs must be unique")
            metric_ids.add(metric_id)
            if (
                not isinstance(metric["value"], (int, float))
                or isinstance(metric["value"], bool)
                or not math.isfinite(metric["value"])
            ):
                raise CapabilityScoutCorrupt("benchmark metric value is invalid")
            if not isinstance(metric["sample_count"], int) or isinstance(metric["sample_count"], bool) or not 1 <= metric["sample_count"] <= 10_000:
                raise CapabilityScoutCorrupt("benchmark sample count is invalid")


def _benchmark_blockers(candidate: Mapping[str, Any]) -> list[str]:
    requirement = candidate["benchmark_requirement"]
    if requirement is None:
        return ["benchmark_requirement"]
    blockers: list[str] = []
    if requirement["pre_test_gate_revision"] != candidate["pre_test_gate"]["revision"]:
        blockers.append("pre_test_gate_revision")
    if requirement["pre_test_gate_sha256"] != _digest(candidate["pre_test_gate"]):
        blockers.append("pre_test_gate_sha256")
    if requirement["evidence_set_revision"] != candidate["evidence_set_revision"]:
        blockers.append("evidence_set_revision")
    required = {
        (item["dimension_id"], item["fixture_id"]): {
            metric["metric_id"]: metric["unit"]
            for metric in item["required_metrics"]
        }
        for item in requirement["fixture_matrix"]
    }
    observed: dict[tuple[str, str], set[str]] = {}
    for receipt in candidate["benchmark_receipts"]:
        if (
            receipt["candidate_revision"] != requirement["candidate_revision"]
            or receipt["evidence_set_revision"] != requirement["evidence_set_revision"]
            or receipt["requirement_revision"] != requirement["requirement_revision"]
            or receipt["pre_test_gate_revision"] != requirement["pre_test_gate_revision"]
            or receipt["pre_test_gate_sha256"] != requirement["pre_test_gate_sha256"]
        ):
            # Preserve superseded attempt provenance, but only the exact
            # currently frozen requirement can satisfy completion.
            continue
        for result in receipt["fixture_results"]:
            pair = (result["dimension_id"], result["fixture_id"])
            metrics = {
                metric["metric_id"]: metric["unit"]
                for metric in result["metrics"]
            }
            if pair not in required:
                blockers.append(f"unexpected_fixture:{result['fixture_id']}")
            elif pair in observed:
                blockers.append(f"duplicate_fixture:{result['fixture_id']}")
            elif metrics != required[pair]:
                blockers.append(f"metric_matrix:{result['fixture_id']}")
            else:
                observed[pair] = metrics
    for pair in required:
        if pair not in observed:
            blockers.append(f"missing_fixture:{pair[1]}")
    return blockers


def _gate_blockers_for(candidate: Mapping[str, Any], stage: str) -> list[str]:
    if stage == "pre_test":
        gate = candidate["pre_test_gate"]
        fields = PRE_TEST_GATE_FIELDS
    elif stage == "post_test":
        gate = candidate["post_test_gate"]
        fields = POST_TEST_GATE_FIELDS
    else:
        raise ValueError("unknown gate stage")
    blockers = [
        field for field in fields
        if gate["decisions"][field]["status"] not in PASSING_GATE_STATUSES
    ]
    if stage == "post_test":
        selection = candidate["owner_selection"]
        implementation = _implementation_binding(candidate)
        if selection is None:
            blockers.append("owner_selection")
        for field in fields:
            decision = gate["decisions"][field]
            if (
                decision["status"] in PASSING_GATE_STATUSES
                and decision["implementation_binding_revision"] != implementation
                and field not in blockers
            ):
                blockers.append(field)
        if selection is not None:
            owner_basis = gate["decisions"]["owner_selected_revision"]
            if owner_basis["status"] != "approved" or owner_basis["basis_revision"] != selection["binding_revision"]:
                if "owner_selected_revision" not in blockers:
                    blockers.append("owner_selected_revision")
            regression = gate["decisions"]["regression"]
            if regression["status"] != "approved" and "regression" not in blockers:
                blockers.append("regression")
    return blockers


def _validate_candidate_semantics(candidate: Mapping[str, Any]) -> None:
    lifecycle = candidate["lifecycle"]
    logical = lifecycle
    if lifecycle == "action_required":
        logical = candidate["action_required"]["from_state"]
    evidence_required_states = {
        "evidence_ready", "watching", "benchmark_queued", "recommended",
        "benchmarked", "selection_ready", "owner_selected", "implementing",
        "verification_required", "promotion_ready", "promoted",
    }
    if logical in evidence_required_states and not candidate["evidence_claims"]:
        raise CapabilityScoutCorrupt("candidate lifecycle requires exact evidence claims")
    if candidate["benchmark_receipts"] and candidate["benchmark_requirement"] is None:
        raise CapabilityScoutCorrupt("benchmark receipts lack their frozen requirement")
    if (
        candidate["evaluation_disposition"] == "evidence_sufficient"
        and (
            candidate["benchmark_requirement"] is not None
            or candidate["benchmark_receipts"]
        )
    ):
        raise CapabilityScoutCorrupt(
            "evidence-sufficient disposition cannot retain benchmark provenance"
        )
    if candidate["evaluation_disposition"] == "benchmark_required":
        requirement = candidate["benchmark_requirement"]
        binding = candidate["evaluation_disposition_binding"]
        if (
            requirement is None
            or binding is None
            or binding["transition_revision"]
            != requirement["candidate_revision"] + 1
        ):
            raise CapabilityScoutCorrupt(
                "benchmark-required disposition lacks its exact admission revision"
            )
    if logical in {"benchmark_queued", "benchmarked"} and candidate["evaluation_disposition"] != "benchmark_required":
        raise CapabilityScoutCorrupt("benchmark lifecycle lacks its required evaluation disposition")
    benchmark_complete_states = {
        "benchmarked", "selection_ready", "owner_selected", "implementing",
        "verification_required", "promotion_ready", "promoted",
    }
    if (
        logical in benchmark_complete_states
        and candidate["evaluation_disposition"] == "benchmark_required"
        and _benchmark_blockers(candidate)
    ):
        raise CapabilityScoutCorrupt("candidate lifecycle requires a complete exact benchmark matrix")
    if logical in benchmark_complete_states and candidate["evaluation_disposition"] == "undetermined":
        raise CapabilityScoutCorrupt("candidate lifecycle lacks an evaluation disposition")
    selection_required_states = {
        "owner_selected", "implementing", "verification_required",
        "promotion_ready", "promoted",
    }
    if logical in selection_required_states and candidate["owner_selection"] is None:
        raise CapabilityScoutCorrupt("candidate lifecycle requires exact owner selection")
    implementation_required_states = {"verification_required", "promotion_ready", "promoted"}
    if logical in implementation_required_states and not candidate["rollback_lineage"]:
        raise CapabilityScoutCorrupt("candidate lifecycle requires implementation rollback provenance")
    any_post_passing = any(
        decision["status"] in PASSING_GATE_STATUSES
        for decision in candidate["post_test_gate"]["decisions"].values()
    )
    if any_post_passing and logical not in {"verification_required", "promotion_ready", "promoted"}:
        raise CapabilityScoutCorrupt("post-test decisions were recorded before verification")
    if any_post_passing and _implementation_binding(candidate) is None:
        raise CapabilityScoutCorrupt("post-test decisions lack an implementation binding")
    if logical == "promoted" and _gate_blockers_for(candidate, "post_test"):
        raise CapabilityScoutCorrupt("promoted candidate does not satisfy exact post-test gates")


def _validate_state(state: Any) -> None:
    if not isinstance(state, Mapping):
        raise CapabilityScoutCorrupt("capability scout state must be an object")
    _expect_keys(state, {
        "schema_version", "store_revision", "updated_at", "candidates",
        "actions", "selection_nonces", "history", "integrity",
    }, "capability scout state")
    if state["schema_version"] != SCHEMA_VERSION:
        raise CapabilityScoutCorrupt("unsupported capability scout schema version")
    if not isinstance(state["store_revision"], int) or isinstance(state["store_revision"], bool) or state["store_revision"] < 0:
        raise CapabilityScoutCorrupt("store revision is invalid")
    if state["updated_at"] is not None:
        _parse_utc(state["updated_at"])
    integrity = state["integrity"]
    if not isinstance(integrity, Mapping):
        raise CapabilityScoutCorrupt("state integrity record is missing")
    _expect_keys(integrity, {"algorithm", "digest"}, "state integrity")
    without_integrity = {key: value for key, value in state.items() if key != "integrity"}
    if integrity.get("algorithm") != "sha256" or integrity.get("digest") != _digest(without_integrity):
        raise CapabilityScoutCorrupt("capability scout state integrity check failed")
    candidates = state["candidates"]
    if not isinstance(candidates, Mapping) or len(candidates) > MAX_CANDIDATES:
        raise CapabilityScoutCorrupt("candidate collection is invalid")
    for candidate_id, candidate in candidates.items():
        _validate_candidate(candidate)
        if candidate_id != candidate["candidate_id"]:
            raise CapabilityScoutCorrupt("candidate index key does not match")
    actions = state["actions"]
    if not isinstance(actions, Mapping) or len(actions) > MAX_ACTIONS:
        raise CapabilityScoutCorrupt("admin action collection is invalid")
    for action_id, action in actions.items():
        if not isinstance(action, Mapping):
            raise CapabilityScoutCorrupt("admin action is invalid")
        _expect_keys(action, {
            "action_id", "candidate_id", "blocker_kind", "from_state",
            "resume_state", "status", "detail", "private_detail",
            "created_at", "resolved_at", "resolution",
        }, "admin action")
        try:
            _identifier(action_id, "action_id")
            _identifier(action["action_id"], "action_id")
            _identifier(action["candidate_id"], "action candidate_id")
            _identifier(action["blocker_kind"], "blocker_kind")
            _clean_text(action["detail"], limit=500, field="action detail")
            _clean_text(action["private_detail"], limit=1000, field="private_detail", allow_empty=True)
            _parse_utc(action["created_at"])
            if action["resolved_at"] is not None:
                _parse_utc(action["resolved_at"])
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if action_id != action["action_id"] or action["candidate_id"] not in candidates:
            raise CapabilityScoutCorrupt("admin action identity does not match")
        if action["from_state"] not in LIFECYCLE_STATES - {"action_required"} or action["resume_state"] not in LIFECYCLE_STATES - {"action_required"}:
            raise CapabilityScoutCorrupt("admin action lifecycle values are invalid")
        if action["resume_state"] in _PROTECTED_TRANSITION_TARGETS:
            raise CapabilityScoutCorrupt("admin action resume target is protected")
        if (
            action["resume_state"] != action["from_state"]
            and action["resume_state"] not in LIFECYCLE_TRANSITIONS[action["from_state"]]
        ):
            raise CapabilityScoutCorrupt("admin action resume target is unreachable")
        if action["status"] not in {"open", "resolved", "declined"} or action["resolution"] not in {None, "resolved", "declined"}:
            raise CapabilityScoutCorrupt("admin action status is invalid")
        if (action["status"] == "open") != (action["resolved_at"] is None and action["resolution"] is None):
            raise CapabilityScoutCorrupt("admin action resolution is inconsistent")
    open_actions: dict[str, str] = {}
    for action_id, action in actions.items():
        if action["status"] == "open":
            if action["candidate_id"] in open_actions:
                raise CapabilityScoutCorrupt("candidate has more than one open admin action")
            open_actions[action["candidate_id"]] = action_id
    for candidate_id, candidate in candidates.items():
        for action_id in candidate["action_history"]:
            action = actions.get(action_id)
            if action is None or action["candidate_id"] != candidate_id:
                raise CapabilityScoutCorrupt("candidate action history is not referentially intact")
        active = candidate["action_required"]
        if active is None:
            if candidate_id in open_actions:
                raise CapabilityScoutCorrupt("open admin action lacks an active candidate blocker")
        elif open_actions.get(candidate_id) != active["action_id"]:
            raise CapabilityScoutCorrupt("active candidate blocker does not match its admin action")
    nonces = state["selection_nonces"]
    if not isinstance(nonces, Mapping) or len(nonces) > MAX_NONCES:
        raise CapabilityScoutCorrupt("selection nonce collection is invalid")
    for token_hash, nonce in nonces.items():
        try:
            _sha256(token_hash, "nonce hash")
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if not isinstance(nonce, Mapping):
            raise CapabilityScoutCorrupt("selection nonce is invalid")
        _expect_keys(nonce, {
            "token_hash", "candidate_id", "candidate_revision",
            "evidence_set_revision", "identity_sha256",
            "owner_audit_commitment", "issued_at", "expires_at",
        }, "selection nonce")
        try:
            _sha256(nonce["token_hash"], "nonce token_hash")
            _identifier(nonce["candidate_id"], "nonce candidate_id")
            _sha256(nonce["evidence_set_revision"], "nonce evidence revision")
            _sha256(nonce["identity_sha256"], "nonce identity digest")
            _sha256(nonce["owner_audit_commitment"], "nonce owner audit commitment")
            issued = _parse_utc(nonce["issued_at"])
            expires = _parse_utc(nonce["expires_at"])
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if token_hash != nonce["token_hash"] or nonce["candidate_id"] not in candidates or expires <= issued:
            raise CapabilityScoutCorrupt("selection nonce binding is invalid")
        if not isinstance(nonce["candidate_revision"], int) or isinstance(nonce["candidate_revision"], bool) or nonce["candidate_revision"] < 1:
            raise CapabilityScoutCorrupt("selection nonce candidate revision is invalid")
    history = state["history"]
    if not isinstance(history, list) or len(history) > MAX_HISTORY:
        raise CapabilityScoutCorrupt("capability scout history is invalid")
    for event in history:
        if not isinstance(event, Mapping):
            raise CapabilityScoutCorrupt("history event is invalid")
        _expect_keys(event, {"event_type", "candidate_id", "candidate_revision", "at"}, "history event")
        try:
            _identifier(event["event_type"], "history event_type")
            if event["candidate_id"] is not None:
                _identifier(event["candidate_id"], "history candidate_id")
            _parse_utc(event["at"])
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        revision = event["candidate_revision"]
        if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 1):
            raise CapabilityScoutCorrupt("history candidate revision is invalid")


def _validate_archived_action(
    action: Any,
    *,
    expected_candidate_id: str,
) -> None:
    if not isinstance(action, Mapping):
        raise CapabilityScoutCorrupt("archived admin action is invalid")
    _expect_keys(action, {
        "action_id", "candidate_id", "blocker_kind", "from_state",
        "resume_state", "status", "detail", "private_detail",
        "created_at", "resolved_at", "resolution",
    }, "archived admin action")
    try:
        _identifier(action["action_id"], "archived action_id")
        _identifier(action["candidate_id"], "archived action candidate_id")
        _identifier(action["blocker_kind"], "archived blocker_kind")
        _clean_text(action["detail"], limit=500, field="archived action detail")
        _clean_text(
            action["private_detail"], limit=1000,
            field="archived private_detail", allow_empty=True,
        )
        _parse_utc(action["created_at"])
        _parse_utc(action["resolved_at"])
    except (TypeError, ValueError) as error:
        raise CapabilityScoutCorrupt("archived admin action values are invalid") from error
    if action["candidate_id"] != expected_candidate_id:
        raise CapabilityScoutCorrupt("archived admin action candidate does not match")
    if (
        action["from_state"] not in LIFECYCLE_STATES - {"action_required"}
        or action["resume_state"] not in LIFECYCLE_STATES - {"action_required"}
        or action["resume_state"] in _PROTECTED_TRANSITION_TARGETS
        or (
            action["resume_state"] != action["from_state"]
            and action["resume_state"]
            not in LIFECYCLE_TRANSITIONS[action["from_state"]]
        )
    ):
        raise CapabilityScoutCorrupt("archived admin action lifecycle is invalid")
    if (
        action["status"] not in {"resolved", "declined"}
        or action["resolution"] != action["status"]
    ):
        raise CapabilityScoutCorrupt("archived admin action is not durably closed")


def _validate_archive_document(
    document: Any,
    *,
    archive_name: str,
) -> str:
    if not isinstance(document, Mapping):
        raise CapabilityScoutCorrupt("archive document is invalid")
    integrity = document.get("integrity")
    retained = {
        key: value for key, value in document.items() if key != "integrity"
    }
    if (
        not isinstance(integrity, Mapping)
        or set(integrity) != {"algorithm", "digest"}
        or integrity.get("algorithm") != "sha256"
        or integrity.get("digest") != _digest(retained)
    ):
        raise CapabilityScoutCorrupt("archive integrity check failed")
    if "candidate" in document:
        _expect_keys(document, {
            "schema_version", "archived_at", "candidate", "actions", "integrity",
        }, "candidate archive")
        if document["schema_version"] != SCHEMA_VERSION:
            raise CapabilityScoutCorrupt("candidate archive schema version is invalid")
        _parse_utc(document["archived_at"])
        _validate_candidate(document["candidate"])
        candidate = document["candidate"]
        if (
            candidate["lifecycle"] not in {"promoted", "rejected"}
            or candidate["action_required"] is not None
        ):
            raise CapabilityScoutCorrupt("candidate archive is not terminal")
        actions = document["actions"]
        if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
            raise CapabilityScoutCorrupt("candidate archive actions are invalid")
        for action in actions:
            _validate_archived_action(
                action, expected_candidate_id=candidate["candidate_id"],
            )
        if [action["action_id"] for action in actions] != candidate["action_history"]:
            raise CapabilityScoutCorrupt(
                "candidate archive action history is not exact"
            )
        expected_name = (
            "candidate-"
            + hashlib.sha256(candidate["candidate_id"].encode("utf-8")).hexdigest()
            + f"-{candidate['revision']}.json"
        )
        if archive_name != expected_name:
            raise CapabilityScoutCorrupt("candidate archive name binding is invalid")
        return "candidate"
    if "action" in document:
        _expect_keys(document, {
            "schema_version", "archived_at", "action", "candidate_binding",
            "integrity",
        }, "action archive")
        if document["schema_version"] != SCHEMA_VERSION:
            raise CapabilityScoutCorrupt("action archive schema version is invalid")
        _parse_utc(document["archived_at"])
        binding = document["candidate_binding"]
        if not isinstance(binding, Mapping):
            raise CapabilityScoutCorrupt("archived candidate binding is invalid")
        _expect_keys(binding, {
            "candidate_id", "candidate_revision", "identity_sha256",
            "evidence_set_revision",
        }, "archived candidate binding")
        try:
            _identifier(binding["candidate_id"], "archived candidate_id")
            _sha256(binding["identity_sha256"], "archived identity digest")
            _sha256(
                binding["evidence_set_revision"],
                "archived evidence revision",
            )
        except ValueError as error:
            raise CapabilityScoutCorrupt(str(error)) from error
        if (
            not isinstance(binding["candidate_revision"], int)
            or isinstance(binding["candidate_revision"], bool)
            or binding["candidate_revision"] < 1
        ):
            raise CapabilityScoutCorrupt("archived candidate revision is invalid")
        _validate_archived_action(
            document["action"], expected_candidate_id=binding["candidate_id"],
        )
        expected_name = (
            "action-"
            + hashlib.sha256(
                document["action"]["action_id"].encode("utf-8")
            ).hexdigest()
            + ".json"
        )
        if archive_name != expected_name:
            raise CapabilityScoutCorrupt("action archive name binding is invalid")
        return "action"
    raise CapabilityScoutCorrupt("archive entry type is invalid")


class CapabilityScoutStore:
    """Strict, restart-safe capability candidate metadata store."""

    CANONICAL_ROOT = Path(__file__).resolve().parents[1] / "storage" / "capability_scout"

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        allow_test_root: bool = False,
        token_factory: Callable[[], str] | None = None,
    ):
        if not _SECURE_ROOTED_IO_SUPPORTED:
            raise CapabilityScoutError(
                "capability scout persistence requires POSIX rooted dir_fd I/O "
                "with no-follow support"
            )
        candidate = Path(os.path.abspath(self.CANONICAL_ROOT if root is None else root))
        if candidate != self.CANONICAL_ROOT:
            temporary = Path(os.path.abspath(tempfile.gettempdir()))
            try:
                candidate.relative_to(temporary)
            except ValueError as error:
                raise CapabilityScoutError("non-canonical root must be beneath the temporary directory") from error
            if not allow_test_root:
                raise CapabilityScoutError("non-canonical roots require explicit test approval")
        self.root = candidate
        self.state_path = self.root / "state.json"
        self.archive_path = self.root / "archive"
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._thread_lock = threading.RLock()

    @contextmanager
    def _root_fd(self, *, create: bool) -> Iterator[int]:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(os.sep, flags)
        try:
            parts = self.root.parts[1:] if self.root.is_absolute() else self.root.parts
            for part in parts:
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        except FileNotFoundError:
            os.close(descriptor)
            raise
        except OSError as error:
            os.close(descriptor)
            raise CapabilityScoutCorrupt(
                "capability scout path contains an unsafe component"
            ) from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise CapabilityScoutCorrupt(
                    "capability scout root is not a directory"
                )
            if create:
                os.fchmod(descriptor, 0o700)
                metadata = os.fstat(descriptor)
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                raise CapabilityScoutCorrupt(
                    "capability scout root permissions are not private"
                )
        except BaseException:
            os.close(descriptor)
            raise
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _subdir_fd(root_fd: int, name: str, *, create: bool) -> int:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            try:
                descriptor = os.open(name, flags, dir_fd=root_fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(name, mode=0o700, dir_fd=root_fd)
                descriptor = os.open(name, flags, dir_fd=root_fd)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise CapabilityScoutCorrupt(
                "capability scout subdirectory path is unsafe"
            ) from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise CapabilityScoutCorrupt(
                    "capability scout subdirectory is unsafe"
                )
            os.fchmod(descriptor, 0o700)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @contextmanager
    def _writer(self) -> Iterator[int]:
        with self._thread_lock, self._root_fd(create=True) as root_fd:
            try:
                descriptor = os.open(
                    ".state.lock",
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=root_fd,
                )
            except FileExistsError as error:
                raise CapabilityScoutLocked(
                    "capability scout writer is active or needs review"
                ) from error
            try:
                os.write(descriptor, uuid.uuid4().hex.encode("ascii"))
                os.fsync(descriptor)
                lock_identity = (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino)
            finally:
                os.close(descriptor)
            try:
                yield root_fd
            finally:
                try:
                    current = os.stat(
                        ".state.lock", dir_fd=root_fd, follow_symlinks=False,
                    )
                    if (
                        not stat.S_ISLNK(current.st_mode)
                        and (current.st_dev, current.st_ino) == lock_identity
                    ):
                        os.unlink(".state.lock", dir_fd=root_fd)
                        os.fsync(root_fd)
                except (FileNotFoundError, OSError):
                    pass

    def _load_from_root_fd(self, root_fd: int, *, recover_temps: bool) -> dict[str, Any]:
        temp_names = [
            name for name in os.listdir(root_fd) if _TEMP_RE.fullmatch(name)
        ]
        try:
            descriptor = os.open(
                "state.json",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            if temp_names:
                raise CapabilityScoutCorrupt(
                    "incomplete initial capability scout write needs review"
                )
            return _default_state()
        except OSError as error:
            raise CapabilityScoutCorrupt(
                "capability scout state cannot be opened safely"
            ) from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_STATE_BYTES:
                raise CapabilityScoutCorrupt("capability scout state type or size is invalid")
            if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600:
                raise CapabilityScoutCorrupt("capability scout state permissions are not private")
            chunks: list[bytes] = []
            remaining = MAX_STATE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(raw) > MAX_STATE_BYTES:
            raise CapabilityScoutCorrupt("capability scout state exceeds its byte limit")
        state = _strict_json(raw)
        _validate_state(state)
        if recover_temps:
            for name in temp_names:
                metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise CapabilityScoutCorrupt(
                        "unsafe interrupted-write artifact needs review"
                    )
                os.unlink(name, dir_fd=root_fd)
            os.fsync(root_fd)
        return state

    def _load_unlocked(
        self,
        *,
        recover_temps: bool = False,
        root_fd: int | None = None,
    ) -> dict[str, Any]:
        if root_fd is not None:
            return self._load_from_root_fd(root_fd, recover_temps=recover_temps)
        try:
            with self._root_fd(create=False) as opened:
                return self._load_from_root_fd(opened, recover_temps=recover_temps)
        except FileNotFoundError:
            return _default_state()

    def load_state(self) -> dict[str, Any]:
        return deepcopy(self._load_unlocked())

    def recover(self) -> dict[str, Any]:
        """Discard only orphaned temp writes when a valid committed state exists."""
        with self._writer() as root_fd:
            return deepcopy(self._load_unlocked(recover_temps=True, root_fd=root_fd))

    def _save_unlocked(self, state: Mapping[str, Any], *, root_fd: int) -> None:
        persisted = _with_integrity(state)
        _validate_state(persisted)
        payload = json.dumps(persisted, indent=2, sort_keys=True, ensure_ascii=True).encode("utf-8") + b"\n"
        if len(payload) > MAX_STATE_BYTES:
            raise CapabilityScoutError("capability scout state exceeds its byte limit")
        temporary = f".state.json.{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=root_fd,
            )
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(descriptor, view):]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(
                temporary, "state.json", src_dir_fd=root_fd, dst_dir_fd=root_fd,
            )
            os.fsync(root_fd)
        except Exception:
            try:
                os.unlink(temporary, dir_fd=root_fd)
            except FileNotFoundError:
                pass
            raise

    def _write_archive_document_unlocked(
        self,
        archive_name: str,
        document: Mapping[str, Any],
        *,
        root_fd: int,
    ) -> None:
        if re.fullmatch(r"[a-z0-9.-]{1,180}", archive_name) is None:
            raise CapabilityScoutError("archive name is invalid")
        archive_fd = self._subdir_fd(root_fd, "archive", create=True)
        try:
            self._write_archive_document_to_fd(
                archive_fd, archive_name, document,
            )
        finally:
            os.close(archive_fd)

    @staticmethod
    def _write_archive_document_to_fd(
        archive_fd: int,
        archive_name: str,
        document: Mapping[str, Any],
    ) -> None:
        payload = json.dumps(
            document, indent=2, sort_keys=True, ensure_ascii=True,
        ).encode("utf-8") + b"\n"
        if len(payload) > MAX_STATE_BYTES:
            raise CapabilityScoutError("candidate archive exceeds its byte limit")
        names = os.listdir(archive_fd)
        destination_exists = archive_name in names
        if not destination_exists and len([name for name in names if name.endswith(".json")]) >= MAX_ARCHIVE_FILES:
            raise CapabilityScoutError("bounded archive catalog is full")
        if destination_exists:
            metadata = os.stat(archive_name, dir_fd=archive_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > MAX_STATE_BYTES
                or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600)
            ):
                raise CapabilityScoutCorrupt("existing candidate archive is unsafe")
            descriptor = os.open(
                archive_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=archive_fd,
            )
            try:
                existing = _strict_json(os.read(descriptor, MAX_STATE_BYTES + 1))
            finally:
                os.close(descriptor)
            integrity = existing.get("integrity")
            retained = {key: value for key, value in existing.items() if key != "integrity"}
            if (
                not isinstance(integrity, Mapping)
                or integrity.get("algorithm") != "sha256"
                or integrity.get("digest") != _digest(retained)
                or {
                    key: value for key, value in existing.items()
                    if key not in {"integrity", "archived_at"}
                } != {
                    key: value for key, value in document.items()
                    if key not in {"integrity", "archived_at"}
                }
            ):
                raise CapabilityScoutCorrupt("existing candidate archive does not match")
        else:
            temporary = f".archive.{uuid.uuid4().hex}.tmp"
            try:
                descriptor = os.open(
                    temporary,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=archive_fd,
                )
                try:
                    view = memoryview(payload)
                    while view:
                        view = view[os.write(descriptor, view):]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.link(
                    temporary,
                    archive_name,
                    src_dir_fd=archive_fd,
                    dst_dir_fd=archive_fd,
                    follow_symlinks=False,
                )
            finally:
                try:
                    os.unlink(temporary, dir_fd=archive_fd)
                except FileNotFoundError:
                    pass
            os.fsync(archive_fd)

    def _archive_resolved_action_unlocked(
        self,
        state: dict[str, Any],
        stamp: str,
        *,
        root_fd: int,
        preferred_candidate_id: str | None = None,
    ) -> str | None:
        candidates = sorted(
            (
                action_id,
                action,
            )
            for action_id, action in state["actions"].items()
            if action["status"] in {"resolved", "declined"}
        )
        if preferred_candidate_id is not None:
            candidates.sort(
                key=lambda item: item[1]["candidate_id"] != preferred_candidate_id
            )
        if not candidates:
            return None
        action_id, action = candidates[0]
        candidate = state["candidates"][action["candidate_id"]]
        document = _with_integrity({
            "schema_version": SCHEMA_VERSION,
            "archived_at": stamp,
            "action": deepcopy(action),
            "candidate_binding": {
                "candidate_id": candidate["candidate_id"],
                "candidate_revision": candidate["revision"],
                "identity_sha256": candidate["identity"]["identity_sha256"],
                "evidence_set_revision": candidate["evidence_set_revision"],
            },
        })
        archive_name = (
            "action-"
            + hashlib.sha256(action_id.encode("utf-8")).hexdigest()
            + ".json"
        )
        self._write_archive_document_unlocked(
            archive_name, document, root_fd=root_fd,
        )
        del state["actions"][action_id]
        candidate["action_history"].remove(action_id)
        candidate["revision"] += 1
        self._event(state, "action_archived", candidate, stamp)
        return action_id

    def _seen_identity_conflict(
        self,
        root_fd: int,
        candidate_id: str,
        identity_sha256: str,
    ) -> bool:
        try:
            seen_fd = self._subdir_fd(root_fd, "seen", create=False)
        except FileNotFoundError:
            return False
        try:
            names = set(os.listdir(seen_fd))
            return (
                "id-" + hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"
                in names
                or "identity-" + identity_sha256 + ".json" in names
            )
        finally:
            os.close(seen_fd)

    def _write_seen_tombstones_unlocked(
        self,
        root_fd: int,
        candidate: Mapping[str, Any],
    ) -> None:
        seen_fd = self._subdir_fd(root_fd, "seen", create=True)
        try:
            document = _with_integrity({
                "schema_version": SCHEMA_VERSION,
                "candidate_id": candidate["candidate_id"],
                "identity_sha256": candidate["identity"]["identity_sha256"],
            })
            payload = json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode("utf-8") + b"\n"
            names = (
                "id-" + hashlib.sha256(candidate["candidate_id"].encode("utf-8")).hexdigest() + ".json",
                "identity-" + candidate["identity"]["identity_sha256"] + ".json",
            )
            existing_names = set(os.listdir(seen_fd))
            if len(existing_names | set(names)) > MAX_ARCHIVE_FILES * 2:
                raise CapabilityScoutError("bounded seen-identity catalog is full")
            for name in names:
                if name in existing_names:
                    descriptor = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=seen_fd,
                    )
                    try:
                        existing = _strict_json(os.read(descriptor, 16_384))
                    finally:
                        os.close(descriptor)
                    if existing != document:
                        raise CapabilityScoutCorrupt(
                            "seen-identity tombstone does not match archived provenance"
                        )
                    continue
                temporary = f".seen.{uuid.uuid4().hex}.tmp"
                try:
                    descriptor = os.open(
                        temporary,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=seen_fd,
                    )
                    try:
                        view = memoryview(payload)
                        while view:
                            view = view[os.write(descriptor, view):]
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    os.link(
                        temporary,
                        name,
                        src_dir_fd=seen_fd,
                        dst_dir_fd=seen_fd,
                        follow_symlinks=False,
                    )
                finally:
                    try:
                        os.unlink(temporary, dir_fd=seen_fd)
                    except FileNotFoundError:
                        pass
            os.fsync(seen_fd)
        finally:
            os.close(seen_fd)

    def _archive_terminal_unlocked(
        self, state: dict[str, Any], stamp: str, *, root_fd: int,
    ) -> str | None:
        terminal = sorted(
            candidate_id
            for candidate_id, candidate in state["candidates"].items()
            if candidate["lifecycle"] in {"promoted", "rejected"}
            and candidate["action_required"] is None
        )
        if not terminal:
            return None
        candidate_id = terminal[0]
        candidate = state["candidates"][candidate_id]
        actions = [
            deepcopy(state["actions"][action_id])
            for action_id in candidate["action_history"]
        ]
        document = _with_integrity({
            "schema_version": SCHEMA_VERSION,
            "archived_at": stamp,
            "candidate": deepcopy(candidate),
            "actions": actions,
        })
        archive_name = (
            "candidate-"
            + hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()
            + f"-{candidate['revision']}.json"
        )
        self._write_archive_document_unlocked(
            archive_name, document, root_fd=root_fd,
        )
        self._write_seen_tombstones_unlocked(root_fd, candidate)
        for action_id in candidate["action_history"]:
            state["actions"].pop(action_id, None)
        state["selection_nonces"] = {
            key: value
            for key, value in state["selection_nonces"].items()
            if value["candidate_id"] != candidate_id
        }
        self._event(state, "candidate_archived", candidate, stamp)
        del state["candidates"][candidate_id]
        return candidate_id

    @staticmethod
    def _event(state: dict[str, Any], event_type: str, candidate: Mapping[str, Any] | None, at: str) -> None:
        state["history"].append({
            "event_type": _identifier(event_type, "event_type"),
            "candidate_id": None if candidate is None else candidate["candidate_id"],
            "candidate_revision": None if candidate is None else candidate["revision"],
            "at": at,
        })
        state["history"] = state["history"][-MAX_HISTORY:]

    def _mutate(
        self,
        callback: Callable[[dict[str, Any], int], Any],
        *,
        now: datetime | str | None = None,
    ) -> Any:
        stamp = _iso_utc(now or _utc_now())
        with self._writer() as root_fd:
            state = self._load_unlocked(recover_temps=True, root_fd=root_fd)
            result = callback(state, root_fd)
            state["store_revision"] += 1
            state["updated_at"] = stamp
            self._save_unlocked(state, root_fd=root_fd)
            return deepcopy(result)

    @staticmethod
    def _candidate(state: Mapping[str, Any], candidate_id: str, expected_revision: int | None = None) -> dict[str, Any]:
        candidate_id = _identifier(candidate_id, "candidate_id")
        candidate = state["candidates"].get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        if expected_revision is not None and candidate["revision"] != expected_revision:
            raise CapabilityScoutConflict("candidate revision is stale")
        return candidate

    def add_candidate(
        self,
        *,
        candidate_id: str,
        kind: str,
        canonical_id: str,
        artifact_id: str,
        source_uri: str,
        source_revision: str,
        parent_relations: Sequence[Mapping[str, Any]] = (),
        capability_dimensions: Sequence[Mapping[str, Any]],
        private_notes: str = "",
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        candidate_id = _identifier(candidate_id, "candidate_id")
        if kind not in CANDIDATE_KINDS:
            raise ValueError("candidate kind is invalid")
        parent_values = _bounded_concrete_sequence(
            parent_relations, limit=MAX_PARENT_RELATIONS, field="parent_relations",
        )
        if any(type(item) is not dict for item in parent_values):
            raise ValueError("parent relations must be concrete objects")
        dimension_values = _bounded_concrete_sequence(
            capability_dimensions, limit=32, field="capability_dimensions",
        )
        if not dimension_values:
            raise ValueError("capability_dimensions cannot be empty")
        identity = {
            "canonical_id": _identifier(canonical_id, "canonical_id"),
            "artifact_id": _identifier(artifact_id, "artifact_id"),
            "source_uri": _clean_text(source_uri, limit=500, field="source_uri"),
            "source_revision": _identifier(source_revision, "source_revision"),
            "parent_relations": [
                {
                    "relation": _identifier(item.get("relation"), "parent relation"),
                    "candidate_id": _identifier(item.get("candidate_id"), "parent candidate_id"),
                    "candidate_revision": item.get("candidate_revision"),
                }
                for item in parent_values
            ],
        }
        identity["identity_sha256"] = _digest(identity)
        dimensions = []
        for item in dimension_values:
            if type(item) is not dict:
                raise ValueError("capability dimension must be a concrete object")
            fixtures = _bounded_concrete_sequence(
                item.get("fixture_ids", ()), limit=64, field="fixture_ids",
            )
            metrics = _bounded_concrete_sequence(
                item.get("required_metrics", ()), limit=64,
                field="required_metrics",
            )
            if any(type(metric) is not dict for metric in metrics):
                raise ValueError("required metrics must be concrete objects")
            dimensions.append({
                "dimension_id": _identifier(item.get("dimension_id"), "dimension_id"),
                "fixture_ids": [_identifier(value, "fixture_id") for value in fixtures],
                "required_metrics": [
                    {
                        "metric_id": _identifier(metric.get("metric_id"), "required metric_id"),
                        "unit": _identifier(metric.get("unit"), "required metric unit"),
                    }
                    for metric in metrics
                ],
            })
        if sum(len(item["fixture_ids"]) for item in dimensions) > MAX_AGGREGATE_FIXTURES:
            raise ValueError("candidate aggregate fixture matrix exceeds the benchmark limit")
        stamp = _iso_utc(now or _utc_now())
        candidate = {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "kind": kind,
            "identity": identity,
            "revision": 1,
            "lifecycle": "discovered",
            "evaluation_disposition": "undetermined",
            "evaluation_disposition_binding": None,
            "capability_dimensions": dimensions,
            "evidence_claims": [],
            "evidence_set_revision": _evidence_revision([]),
            "pre_test_gate": _empty_gate(PRE_TEST_GATE_FIELDS),
            "post_test_gate": _empty_gate(
                POST_TEST_GATE_FIELDS, implementation_bound=True,
            ),
            "benchmark_requirement": None,
            "benchmark_receipts": [],
            "owner_selection": None,
            "action_required": None,
            "action_history": [],
            "rollback_lineage": [],
            "private_notes": _clean_text(private_notes, limit=2000, field="private_notes", allow_empty=True),
        }
        _validate_candidate(candidate)

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            if candidate_id in state["candidates"]:
                raise CapabilityScoutConflict("candidate identity already exists")
            if any(
                existing["identity"]["identity_sha256"] == identity["identity_sha256"]
                for existing in state["candidates"].values()
            ):
                raise CapabilityScoutConflict("exact artifact identity already has a candidate")
            if self._seen_identity_conflict(
                root_fd, candidate_id, identity["identity_sha256"],
            ):
                raise CapabilityScoutConflict(
                    "candidate ID or exact artifact identity was already archived"
                )
            if len(state["candidates"]) >= MAX_CANDIDATES:
                if self._archive_terminal_unlocked(
                    state, stamp, root_fd=root_fd,
                ) is None:
                    raise CapabilityScoutError(
                        "active candidate limit reached with nothing terminal to archive"
                    )
            for relation in identity["parent_relations"]:
                parent = state["candidates"].get(relation["candidate_id"])
                if parent is None or parent["revision"] != relation["candidate_revision"]:
                    raise CapabilityScoutConflict("parent relation does not match an exact candidate revision")
            state["candidates"][candidate_id] = deepcopy(candidate)
            self._event(state, "candidate_added", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    def add_evidence_claim(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        claim_id: str,
        claim_type: str,
        summary: str,
        source_uri: str,
        source_revision: str,
        evidence_revision: str,
        source_sha256: str,
        observed_at: datetime | str,
        private_notes: str = "",
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _iso_utc(now or _utc_now())
        claim = {
            "claim_id": _identifier(claim_id, "claim_id"),
            "claim_type": _identifier(claim_type, "claim_type"),
            "summary": _clean_text(summary, limit=500, field="summary"),
            "source_uri": _clean_text(source_uri, limit=500, field="source_uri"),
            "source_revision": _identifier(source_revision, "source_revision"),
            "evidence_revision": _identifier(evidence_revision, "evidence_revision"),
            "source_sha256": _sha256(source_sha256, "source_sha256"),
            "observed_at": _iso_utc(observed_at),
            "private_notes": _clean_text(private_notes, limit=1000, field="private_notes", allow_empty=True),
        }

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, expected_revision)
            if candidate["lifecycle"] not in {"discovered", "researching", "evidence_ready", "watching"}:
                raise CapabilityScoutConflict("evidence is frozen after recommendation or benchmarking")
            if len(candidate["evidence_claims"]) >= MAX_EVIDENCE_CLAIMS:
                raise CapabilityScoutError("evidence claim limit reached")
            if any(item["claim_id"] == claim["claim_id"] for item in candidate["evidence_claims"]):
                raise CapabilityScoutConflict("evidence claim identity already exists")
            candidate["evidence_claims"].append(deepcopy(claim))
            candidate["evidence_set_revision"] = _evidence_revision(candidate["evidence_claims"])
            candidate["revision"] += 1
            self._event(state, "evidence_added", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    @staticmethod
    def _gate_blockers(candidate: Mapping[str, Any], stage: str) -> list[str]:
        return _gate_blockers_for(candidate, stage)

    @staticmethod
    def _record_evaluation_disposition(
        candidate: dict[str, Any], disposition: str, transition_target: str,
    ) -> None:
        binding = {
            "disposition": disposition,
            "transition_target": transition_target,
            "transition_revision": candidate["revision"] + 1,
            "evidence_set_revision": candidate["evidence_set_revision"],
            "identity_sha256": candidate["identity"]["identity_sha256"],
        }
        binding["binding_revision"] = _digest(binding)
        candidate["evaluation_disposition"] = disposition
        candidate["evaluation_disposition_binding"] = binding

    @classmethod
    def _prepare_target(cls, candidate: dict[str, Any], target: str) -> None:
        if target == "evidence_ready" and not candidate["evidence_claims"]:
            raise CapabilityScoutConflict(
                "evidence_ready requires at least one exact evidence claim"
            )
        if target == "recommended":
            cls._record_evaluation_disposition(
                candidate, "evidence_sufficient", "recommended",
            )
        if target == "benchmark_queued":
            blockers = _gate_blockers_for(candidate, "pre_test")
            if blockers:
                raise CapabilityGateBlocked("pre_test", blockers)
            candidate["benchmark_requirement"] = _build_benchmark_requirement(candidate)
            cls._record_evaluation_disposition(
                candidate, "benchmark_required", "benchmark_queued",
            )
        if target in {
            "benchmarked", "selection_ready", "owner_selected", "implementing",
            "verification_required", "promotion_ready", "promoted",
        } and (
            candidate["evaluation_disposition"] == "benchmark_required"
            and _benchmark_blockers(candidate)
        ):
            raise CapabilityScoutConflict(
                "target lifecycle requires the complete frozen benchmark matrix"
            )
        if target == "verification_required" and not candidate["rollback_lineage"]:
            raise CapabilityScoutConflict(
                "verification requires exact implementation rollback provenance"
            )

    def update_gate(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        stage: str,
        decisions: Mapping[str, Mapping[str, Any]],
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if type(decisions) is not dict:
            raise ValueError("decisions must be a concrete object")
        if stage == "pre_test":
            gate_key, fields = "pre_test_gate", PRE_TEST_GATE_FIELDS
        elif stage == "post_test":
            gate_key, fields = "post_test_gate", POST_TEST_GATE_FIELDS
        else:
            raise ValueError("stage must be pre_test or post_test")
        unknown = set(decisions) - set(fields)
        if unknown or not decisions:
            raise ValueError("gate decision fields are invalid")
        normalized: dict[str, dict[str, Any]] = {}
        for field, decision in decisions.items():
            if type(decision) is not dict:
                raise ValueError(f"gate decision for {field} must be a concrete object")
            if set(decision) != {"status", "basis_revision"} or decision["status"] not in GATE_STATUSES:
                raise ValueError(f"gate decision for {field} is invalid")
            basis = decision["basis_revision"]
            if basis is not None:
                basis = _identifier(basis, f"{field} basis_revision")
            if decision["status"] in PASSING_GATE_STATUSES and basis is None:
                raise ValueError(f"passing gate decision for {field} needs a basis revision")
            normalized[field] = {"status": decision["status"], "basis_revision": basis}
        stamp = _iso_utc(now or _utc_now())

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, expected_revision)
            logical_state = candidate["lifecycle"]
            if logical_state == "action_required":
                logical_state = candidate["action_required"]["from_state"]
            if stage == "pre_test" and logical_state in {
                "benchmarked", "selection_ready", "owner_selected", "implementing",
                "verification_required", "promotion_ready", "promoted", "rejected",
            }:
                raise CapabilityScoutConflict("pre-test gate is frozen after benchmarking")
            if stage == "post_test" and logical_state not in {
                "verification_required", "promotion_ready",
            }:
                raise CapabilityScoutConflict(
                    "post-test gate requires an implemented candidate in verification"
                )
            staged = deepcopy(normalized)
            if stage == "post_test":
                binding = _implementation_binding(candidate)
                if binding is None:
                    raise CapabilityScoutConflict(
                        "post-test gate requires exact implementation rollback binding"
                    )
                for decision in staged.values():
                    decision["implementation_binding_revision"] = binding
            gate = candidate[gate_key]
            gate["decisions"].update(staged)
            gate["revision"] += 1
            candidate["revision"] += 1
            self._event(state, f"{stage}_gate_updated", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    def transition(
        self,
        candidate_id: str,
        target: str,
        *,
        expected_revision: int,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if target not in LIFECYCLE_STATES or target == "action_required":
            raise ValueError("target lifecycle is invalid for direct transition")
        stamp = _iso_utc(now or _utc_now())

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, expected_revision)
            current = candidate["lifecycle"]
            if target not in LIFECYCLE_TRANSITIONS[current]:
                raise CapabilityScoutConflict(f"cannot transition {current} to {target}")
            self._prepare_target(candidate, target)
            if target == "benchmarked":
                raise CapabilityScoutConflict(
                    "benchmarked is entered only by complete receipt attachment"
                )
            if target == "owner_selected":
                raise CapabilityScoutConflict("owner selection requires a bound nonce")
            if target == "promoted":
                raise CapabilityScoutConflict("promotion requires the post-test promotion gate")
            if target == "implementing":
                candidate["post_test_gate"] = _empty_gate(
                    POST_TEST_GATE_FIELDS, implementation_bound=True,
                )
            candidate["lifecycle"] = target
            candidate["revision"] += 1
            self._event(state, "lifecycle_transition", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    def require_action(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        blocker_kind: str,
        resume_state: str,
        detail: str,
        private_detail: str = "",
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        blocker_kind = _identifier(blocker_kind, "blocker_kind")
        if resume_state not in LIFECYCLE_STATES - {"action_required"}:
            raise ValueError("resume_state is invalid")
        stamp = _iso_utc(now or _utc_now())
        action_id = f"action-{uuid.uuid4().hex}"

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, expected_revision)
            current = candidate["lifecycle"]
            if current in {"rejected", "promoted", "action_required"}:
                raise CapabilityScoutConflict("candidate cannot enter action_required")
            if resume_state in _PROTECTED_TRANSITION_TARGETS:
                raise CapabilityScoutConflict("resume state requires its dedicated receipt, nonce, or gate operation")
            if resume_state != current and resume_state not in LIFECYCLE_TRANSITIONS[current]:
                raise CapabilityScoutConflict("resume state is not reachable from current lifecycle")
            while (
                len(state["actions"]) >= MAX_ACTIONS
                or len(candidate["action_history"]) >= MAX_ACTIONS
            ):
                if self._archive_resolved_action_unlocked(
                    state, stamp,
                    root_fd=root_fd,
                    preferred_candidate_id=candidate["candidate_id"],
                ) is not None:
                    continue
                if self._archive_terminal_unlocked(
                    state, stamp, root_fd=root_fd,
                ) is None:
                    raise CapabilityScoutError(
                        "active admin action limit reached with nothing resolved or terminal to archive"
                    )
            action = {
                "action_id": action_id,
                "candidate_id": candidate["candidate_id"],
                "blocker_kind": blocker_kind,
                "from_state": current,
                "resume_state": resume_state,
                "status": "open",
                "detail": _clean_text(detail, limit=500, field="detail"),
                "private_detail": _clean_text(private_detail, limit=1000, field="private_detail", allow_empty=True),
                "created_at": stamp,
                "resolved_at": None,
                "resolution": None,
            }
            state["actions"][action_id] = action
            candidate["lifecycle"] = "action_required"
            candidate["action_required"] = {
                "action_id": action_id,
                "from_state": current,
                "resume_state": resume_state,
                "blocker_kind": blocker_kind,
            }
            candidate["action_history"].append(action_id)
            candidate["revision"] += 1
            self._event(state, "action_required", candidate, stamp)
            return action

        return self._mutate(mutate, now=stamp)

    def freeze_benchmark_requirement(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Freeze a fresh exact matrix after queued approvals were revised."""
        stamp = _iso_utc(now or _utc_now())

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, expected_revision)
            if candidate["lifecycle"] != "benchmark_queued":
                raise CapabilityScoutConflict("candidate is not benchmark_queued")
            blockers = self._gate_blockers(candidate, "pre_test")
            if blockers:
                raise CapabilityGateBlocked("pre_test", blockers)
            candidate["benchmark_requirement"] = _build_benchmark_requirement(candidate)
            self._record_evaluation_disposition(
                candidate, "benchmark_required", "benchmark_queued",
            )
            candidate["revision"] += 1
            self._event(state, "benchmark_requirement_frozen", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    def resolve_action(
        self,
        action_id: str,
        *,
        resolution: str,
        expected_candidate_revision: int,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        action_id = _identifier(action_id, "action_id")
        if resolution not in {"resolved", "declined"}:
            raise ValueError("resolution must be resolved or declined")
        stamp = _iso_utc(now or _utc_now())

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            action = state["actions"].get(action_id)
            if action is None:
                raise KeyError(action_id)
            if action["status"] != "open":
                raise CapabilityScoutConflict("admin action is already closed")
            candidate = self._candidate(state, action["candidate_id"], expected_candidate_revision)
            required = candidate["action_required"]
            if required is None or required["action_id"] != action_id:
                raise CapabilityScoutConflict("admin action is not the candidate's active blocker")
            target = "rejected" if resolution == "declined" else action["resume_state"]
            if resolution == "resolved":
                self._prepare_target(candidate, target)
                if target == "implementing":
                    candidate["post_test_gate"] = _empty_gate(
                        POST_TEST_GATE_FIELDS, implementation_bound=True,
                    )
            action["status"] = resolution
            action["resolution"] = resolution
            action["resolved_at"] = stamp
            candidate["lifecycle"] = target
            candidate["action_required"] = None
            candidate["revision"] += 1
            self._event(state, "action_resolved", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    def issue_owner_selection_nonce(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        owner_key: str,
        session_key: str,
        authority_key: str,
        ttl_seconds: int = 300,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 900:
            raise ValueError("nonce ttl must be between 1 and 900 seconds")
        current = _parse_utc(_iso_utc(now or _utc_now()))
        stamp = _iso_utc(current)
        expires_at = _iso_utc(current + timedelta(seconds=ttl_seconds))
        token = _clean_text(self._token_factory(), limit=256, field="nonce")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        owner_commitment = _authority_commitment(owner_key, session_key, authority_key)

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, expected_revision)
            if candidate["lifecycle"] != "selection_ready":
                raise CapabilityScoutConflict("candidate is not ready for owner selection")
            state["selection_nonces"] = {
                key: value for key, value in state["selection_nonces"].items()
                if _parse_utc(value["expires_at"]) > current
            }
            if len(state["selection_nonces"]) >= MAX_NONCES:
                raise CapabilityScoutError("selection nonce limit reached")
            if token_hash in state["selection_nonces"]:
                raise CapabilityScoutConflict("selection nonce token was already issued")
            state["selection_nonces"][token_hash] = {
                "token_hash": token_hash,
                "candidate_id": candidate["candidate_id"],
                "candidate_revision": candidate["revision"],
                "evidence_set_revision": candidate["evidence_set_revision"],
                "identity_sha256": candidate["identity"]["identity_sha256"],
                "owner_audit_commitment": owner_commitment,
                "issued_at": stamp,
                "expires_at": expires_at,
            }
            self._event(state, "selection_nonce_issued", candidate, stamp)
            return {
                "nonce": token,
                "candidate_id": candidate["candidate_id"],
                "candidate_revision": candidate["revision"],
                "evidence_set_revision": candidate["evidence_set_revision"],
                "owner_audit_commitment": owner_commitment,
                "expires_at": expires_at,
            }

        return self._mutate(mutate, now=stamp)

    def select_candidate(
        self,
        candidate_id: str,
        *,
        nonce: str,
        candidate_revision: int,
        evidence_set_revision: str,
        owner_key: str,
        session_key: str,
        authority_key: str,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        token = _clean_text(nonce, limit=256, field="nonce")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        evidence_set_revision = _sha256(evidence_set_revision, "evidence_set_revision")
        current = _parse_utc(_iso_utc(now or _utc_now()))
        stamp = _iso_utc(current)
        owner_commitment = _authority_commitment(owner_key, session_key, authority_key)

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, candidate_revision)
            record = state["selection_nonces"].pop(token_hash, None)
            if record is None:
                raise CapabilityScoutConflict("owner selection nonce is unknown or already used")
            if _parse_utc(record["expires_at"]) <= current:
                raise CapabilityScoutConflict("owner selection nonce expired")
            if (
                candidate["lifecycle"] != "selection_ready"
                or (
                    candidate["evaluation_disposition"] == "benchmark_required"
                    and _benchmark_blockers(candidate)
                )
                or record["candidate_id"] != candidate["candidate_id"]
                or record["candidate_revision"] != candidate_revision
                or record["evidence_set_revision"] != evidence_set_revision
                or record["evidence_set_revision"] != candidate["evidence_set_revision"]
                or record["identity_sha256"] != candidate["identity"]["identity_sha256"]
                or record["owner_audit_commitment"] != owner_commitment
            ):
                raise CapabilityScoutConflict("owner selection nonce binding does not match")
            selection = {
                "selection_id": f"selection-{uuid.uuid4().hex}",
                "candidate_revision": candidate_revision,
                "evidence_set_revision": evidence_set_revision,
                "identity_sha256": candidate["identity"]["identity_sha256"],
                "owner_audit_commitment": owner_commitment,
                "selected_at": stamp,
            }
            selection["binding_revision"] = _digest(selection)
            candidate["owner_selection"] = selection
            candidate["lifecycle"] = "owner_selected"
            candidate["revision"] += 1
            self._event(state, "owner_selected", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    def attach_benchmark_receipt(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        receipt: Mapping[str, Any],
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _iso_utc(now or _utc_now())
        if type(receipt) is not dict:
            raise ValueError("receipt must be a concrete object")
        if type(receipt.get("fixture_results")) is not list:
            raise ValueError("receipt fixture_results must be a concrete list")
        if len(receipt["fixture_results"]) > 128:
            raise ValueError("receipt fixture_results exceed the limit")
        for result in receipt["fixture_results"]:
            if type(result) is not dict or type(result.get("metrics")) is not list:
                raise ValueError("receipt fixture result and metrics must be concrete")
            if len(result["metrics"]) > 64 or any(
                type(metric) is not dict for metric in result["metrics"]
            ):
                raise ValueError("receipt metrics exceed the concrete input contract")
        normalized = deepcopy(receipt)
        _validate_benchmark_receipt(normalized, candidate_id)

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, expected_revision)
            if candidate["lifecycle"] != "benchmark_queued":
                raise CapabilityScoutConflict("candidate is not benchmark_queued")
            gate_blockers = self._gate_blockers(candidate, "pre_test")
            if gate_blockers:
                raise CapabilityGateBlocked("pre_test", gate_blockers)
            requirement = candidate["benchmark_requirement"]
            if requirement is None:
                raise CapabilityScoutConflict("benchmark requirement is not frozen")
            if (
                requirement["pre_test_gate_revision"] != candidate["pre_test_gate"]["revision"]
                or requirement["pre_test_gate_sha256"] != _digest(candidate["pre_test_gate"])
            ):
                raise CapabilityGateBlocked(
                    "pre_test", ("pre_test_gate_revision", "pre_test_gate_sha256"),
                )
            exact_receipt_binding = {
                "candidate_revision": requirement["candidate_revision"],
                "evidence_set_revision": requirement["evidence_set_revision"],
                "requirement_revision": requirement["requirement_revision"],
                "pre_test_gate_revision": requirement["pre_test_gate_revision"],
                "pre_test_gate_sha256": requirement["pre_test_gate_sha256"],
            }
            if any(normalized[key] != value for key, value in exact_receipt_binding.items()):
                raise CapabilityScoutConflict("benchmark receipt revision binding does not match")
            required = {
                (item["dimension_id"], item["fixture_id"]): {
                    metric["metric_id"]: metric["unit"]
                    for metric in item["required_metrics"]
                }
                for item in requirement["fixture_matrix"]
            }
            prior_pairs = {
                (result["dimension_id"], result["fixture_id"])
                for prior in candidate["benchmark_receipts"]
                if prior["requirement_revision"] == requirement["requirement_revision"]
                for result in prior["fixture_results"]
            }
            for result in normalized["fixture_results"]:
                pair = (result["dimension_id"], result["fixture_id"])
                metrics = {
                    metric["metric_id"]: metric["unit"]
                    for metric in result["metrics"]
                }
                if pair not in required or metrics != required[pair]:
                    raise CapabilityScoutConflict(
                        "benchmark receipt does not match the exact fixture and metric matrix"
                    )
                if pair in prior_pairs:
                    raise CapabilityScoutConflict("benchmark fixture was already receipted")
            if any(item["receipt_id"] == normalized["receipt_id"] for item in candidate["benchmark_receipts"]):
                raise CapabilityScoutConflict("benchmark receipt identity already exists")
            if len(candidate["benchmark_receipts"]) >= MAX_BENCHMARK_RECEIPTS:
                raise CapabilityScoutError("benchmark receipt limit reached")
            candidate["benchmark_receipts"].append(normalized)
            if not _benchmark_blockers(candidate):
                candidate["lifecycle"] = "benchmarked"
            candidate["revision"] += 1
            self._event(state, "benchmark_receipt_attached", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    def add_rollback_lineage(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        implementation_revision: str,
        previous_revision: str,
        artifact_ids: Sequence[str],
        reason: str,
        private_notes: str = "",
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _iso_utc(now or _utc_now())
        artifacts = _bounded_concrete_sequence(
            artifact_ids, limit=32, field="artifact_ids",
        )
        record = {
            "implementation_revision": _identifier(implementation_revision, "implementation_revision"),
            "previous_revision": _identifier(previous_revision, "previous_revision"),
            "artifact_ids": [_identifier(item, "artifact_id") for item in artifacts],
            "recorded_at": stamp,
            "reason": _clean_text(reason, limit=500, field="reason"),
            "private_notes": _clean_text(private_notes, limit=1000, field="private_notes", allow_empty=True),
        }

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, expected_revision)
            if candidate["lifecycle"] not in {"implementing", "verification_required"}:
                raise CapabilityScoutConflict(
                    "rollback lineage requires an actual implementation or verification stage"
                )
            selection = candidate["owner_selection"]
            if selection is None:
                raise CapabilityScoutConflict("rollback lineage requires exact owner selection")
            if len(candidate["rollback_lineage"]) >= MAX_ROLLBACK_RECORDS:
                raise CapabilityScoutError("rollback lineage limit reached")
            if (
                candidate["rollback_lineage"]
                and previous_revision
                != candidate["rollback_lineage"][-1]["implementation_revision"]
            ):
                raise CapabilityScoutConflict("rollback lineage must extend the current implementation")
            if implementation_revision == previous_revision:
                raise CapabilityScoutConflict("implementation revision cannot equal its rollback revision")
            bound_record = {
                **deepcopy(record),
                "candidate_revision": selection["candidate_revision"],
                "evidence_set_revision": selection["evidence_set_revision"],
                "owner_selection_binding_revision": selection["binding_revision"],
            }
            bound_record["record_revision"] = _digest({
                key: value
                for key, value in bound_record.items()
                if key != "private_notes"
            })
            candidate["rollback_lineage"].append(bound_record)
            candidate["post_test_gate"] = _empty_gate(
                POST_TEST_GATE_FIELDS, implementation_bound=True,
            )
            candidate["revision"] += 1
            self._event(state, "rollback_lineage_added", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    def promote(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        stamp = _iso_utc(now or _utc_now())

        def mutate(state: dict[str, Any], root_fd: int) -> dict[str, Any]:
            candidate = self._candidate(state, candidate_id, expected_revision)
            if candidate["lifecycle"] != "promotion_ready":
                raise CapabilityScoutConflict("candidate is not promotion_ready")
            benchmark_blockers = _benchmark_blockers(candidate)
            if (
                candidate["evaluation_disposition"] == "benchmark_required"
                and benchmark_blockers
            ):
                raise CapabilityScoutConflict(
                    "promotion requires a complete exact benchmark matrix"
                )
            blockers = self._gate_blockers(candidate, "post_test")
            if blockers:
                raise CapabilityGateBlocked("post_test", blockers)
            if not candidate["rollback_lineage"]:
                raise CapabilityScoutConflict("promotion requires rollback lineage")
            candidate["lifecycle"] = "promoted"
            candidate["revision"] += 1
            self._event(state, "promoted", candidate, stamp)
            return candidate

        return self._mutate(mutate, now=stamp)

    @staticmethod
    def _public_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
        identity = deepcopy(candidate["identity"])
        identity["source_uri"] = _public_source_uri(identity["source_uri"])
        return {
            "candidate_id": candidate["candidate_id"],
            "kind": candidate["kind"],
            "identity": identity,
            "revision": candidate["revision"],
            "lifecycle": candidate["lifecycle"],
            "evaluation_disposition": candidate["evaluation_disposition"],
            "capability_dimensions": [
                {"dimension_id": item["dimension_id"]}
                for item in candidate["capability_dimensions"]
            ],
            "evidence_set_revision": candidate["evidence_set_revision"],
            "evidence_claim_count": len(candidate["evidence_claims"]),
            "benchmark_receipt_count": len(candidate["benchmark_receipts"]),
        }

    def public_projection(self) -> dict[str, Any]:
        state = self.load_state()
        return {
            "schema_version": SCHEMA_VERSION,
            "store_revision": state["store_revision"],
            "updated_at": state["updated_at"],
            "candidates": [
                self._public_candidate(state["candidates"][candidate_id])
                for candidate_id in sorted(state["candidates"])
            ],
        }

    def archive_projection(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded private-safe provenance page from immutable archives."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("archive projection limit must be between 1 and 100")
        if cursor is not None and (
            not isinstance(cursor, str)
            or re.fullmatch(r"[a-z0-9.-]{1,180}", cursor) is None
        ):
            raise ValueError("archive projection cursor is invalid")
        try:
            with self._root_fd(create=False) as root_fd:
                try:
                    archive_fd = self._subdir_fd(root_fd, "archive", create=False)
                except FileNotFoundError:
                    return {"schema_version": SCHEMA_VERSION, "items": [], "next_cursor": None}
                try:
                    names = sorted(
                        name for name in os.listdir(archive_fd)
                        if name.endswith(".json")
                    )
                    if len(names) > MAX_ARCHIVE_FILES:
                        raise CapabilityScoutCorrupt("archive catalog exceeds its bound")
                    start = 0
                    if cursor is not None:
                        try:
                            start = names.index(cursor) + 1
                        except ValueError as error:
                            raise CapabilityScoutConflict("archive cursor is stale") from error
                    selected = names[start:start + limit]
                    items = []
                    for name in selected:
                        descriptor = os.open(
                            name,
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=archive_fd,
                        )
                        try:
                            metadata = os.fstat(descriptor)
                            if (
                                not stat.S_ISREG(metadata.st_mode)
                                or metadata.st_size > MAX_STATE_BYTES
                                or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600)
                            ):
                                raise CapabilityScoutCorrupt("archive entry is unsafe")
                            raw = os.read(descriptor, MAX_STATE_BYTES + 1)
                        finally:
                            os.close(descriptor)
                        document = _strict_json(raw)
                        archive_kind = _validate_archive_document(
                            document, archive_name=name,
                        )
                        if archive_kind == "candidate":
                            items.append({
                                "archive_id": name,
                                "archived_at": document["archived_at"],
                                "kind": "candidate",
                                "candidate": self._public_candidate(document["candidate"]),
                            })
                        else:
                            items.append({
                                "archive_id": name,
                                "archived_at": document["archived_at"],
                                "kind": "action",
                                "action": {
                                    key: value
                                    for key, value in document["action"].items()
                                    if key != "private_detail"
                                },
                                "candidate_binding": deepcopy(document["candidate_binding"]),
                            })
                    next_cursor = None
                    if start + len(selected) < len(names) and selected:
                        next_cursor = selected[-1]
                    return {
                        "schema_version": SCHEMA_VERSION,
                        "items": items,
                        "next_cursor": next_cursor,
                    }
                finally:
                    os.close(archive_fd)
        except FileNotFoundError:
            return {"schema_version": SCHEMA_VERSION, "items": [], "next_cursor": None}

    def admin_projection(self) -> dict[str, Any]:
        state = self.load_state()
        candidates = []
        for candidate_id in sorted(state["candidates"]):
            candidate = state["candidates"][candidate_id]
            projected = self._public_candidate(candidate)
            projected.update({
                "evidence_claims": [
                    {
                        key: (
                            _public_source_uri(value)
                            if key == "source_uri"
                            else value
                        )
                        for key, value in claim.items()
                        if key != "private_notes"
                    }
                    for claim in candidate["evidence_claims"]
                ],
                "pre_test_gate": deepcopy(candidate["pre_test_gate"]),
                "post_test_gate": deepcopy(candidate["post_test_gate"]),
                "owner_selection": deepcopy(candidate["owner_selection"]),
                "action_required": deepcopy(candidate["action_required"]),
                "benchmark_receipts": [
                    {
                        key: value for key, value in receipt.items()
                        if key not in {"fixture_results", "private_artifact_ref"}
                    }
                    for receipt in candidate["benchmark_receipts"]
                ],
                "rollback_lineage": [
                    {key: value for key, value in record.items() if key != "private_notes"}
                    for record in candidate["rollback_lineage"]
                ],
            })
            candidates.append(projected)
        return {
            "schema_version": SCHEMA_VERSION,
            "store_revision": state["store_revision"],
            "updated_at": state["updated_at"],
            "candidates": candidates,
            "actions": [
                {
                    key: value
                    for key, value in state["actions"][action_id].items()
                    if key != "private_detail"
                }
                for action_id in sorted(state["actions"])
            ],
        }


__all__ = [
    "BENCHMARK_RECEIPT_SCHEMA_VERSION",
    "CANDIDATE_KINDS",
    "CapabilityGateBlocked",
    "CapabilityScoutConflict",
    "CapabilityScoutCorrupt",
    "CapabilityScoutError",
    "CapabilityScoutLocked",
    "CapabilityScoutStore",
    "LIFECYCLE_STATES",
    "LIFECYCLE_TRANSITIONS",
    "POST_TEST_GATE_FIELDS",
    "PRE_TEST_GATE_FIELDS",
    "SCHEMA_VERSION",
]
