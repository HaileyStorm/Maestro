"""Pure runtime decisions and transitions for recorded support allowances.

This module has no persistence or scheduler wiring.  Its production-safe
default is advisory only: every submission remains available and no allowance
is reserved until a later caller explicitly enables hosted enforcement.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
REALMS = frozenset({"local", "lan", "hosted"})
SOURCE_KINDS = frozenset({"free", "one_time_support", "recurring_support"})
SOURCE_STATUSES = frozenset({
    "active", "inactive", "refunded", "expired", "capped", "canceled",
})
REFUND_STATES = frozenset({"not_applicable", "none", "partial", "full", "excess"})
CAPABILITY_MARKERS = frozenset({
    "standard_support_priority_policy",
    "creator_terms_exclude_support_priority",
})
_ITEM_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_OPAQUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]{7,127}\Z")
_CAPABILITY_RE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")


class CreditRuntimeError(ValueError):
    """Raised when a credit-runtime input violates its narrow contract."""


class CreditTransitionConflict(CreditRuntimeError):
    """Raised when an idempotency key is rebound to a different quote."""


@dataclass(frozen=True, slots=True)
class CreditRuntimePolicy:
    """Explicit opt-in policy; production callers get disabled behavior."""

    enforcement_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.enforcement_enabled) is not bool:
            raise CreditRuntimeError("enforcement_enabled must be boolean")


DEFAULT_CREDIT_RUNTIME_POLICY = CreditRuntimePolicy()


@dataclass(frozen=True, slots=True)
class AllowanceSource:
    source: str
    source_event_id: str | None
    effective_allowance: int
    expires_at: datetime | None

    @property
    def identity(self) -> tuple[str, str | None]:
        return self.source, self.source_event_id


@dataclass(frozen=True, slots=True)
class AllowanceSnapshot:
    unit: str
    as_of: datetime
    effective_allowance: int
    sources: tuple[AllowanceSource, ...]


@dataclass(frozen=True, slots=True)
class CapabilityPriorityPolicy:
    capability_id: str
    support_priority_eligible: bool
    marker: str


@dataclass(frozen=True, slots=True)
class SourceAllocation:
    source: str
    source_event_id: str | None
    units: int
    expires_at: str | None

    @property
    def identity(self) -> tuple[str, str | None]:
        return self.source, self.source_event_id


@dataclass(frozen=True, slots=True)
class CreditReservationQuote:
    schema_version: int
    realm: str
    unit: str
    snapshot_as_of: str
    submission_allowed: bool
    decision: str
    policy_enforcement_enabled: bool
    metering_applied: bool
    capability_priority_eligible: bool
    priority_boost: bool
    reservation_required: bool
    requested_units: int
    reserved_units: int
    allocations: tuple[SourceAllocation, ...]


@dataclass(frozen=True, slots=True)
class CreditReservationState:
    schema_version: int
    reservation_id: str
    quote_fingerprint: str
    unit: str
    status: str
    revision: int
    reserved_units: int
    snapshot_as_of: str
    allocations: tuple[SourceAllocation, ...]


@dataclass(frozen=True, slots=True)
class CreditRevalidationResult:
    reservation_id: str
    quote_fingerprint: str
    state: str
    submission_allowed: bool
    priority_boost_retained: bool
    available_reserved_units: int
    release_recommended: bool
    reason: str


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise CreditRuntimeError(f"{name} schema is invalid")


def _non_negative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CreditRuntimeError(f"{name} must be a non-negative integer")
    return value


def _utc(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CreditRuntimeError(f"{name} must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise CreditRuntimeError(f"{name} must be an ISO UTC timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise CreditRuntimeError(f"{name} must be an ISO UTC timestamp")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_recorded_allowance(value: Mapping[str, Any]) -> AllowanceSnapshot:
    """Validate and normalize the privacy-safe recorded allowance projection."""

    if not isinstance(value, Mapping):
        raise CreditRuntimeError("recorded allowance must be an object")
    _exact_keys(value, {
        "state", "enforcement_enabled", "unit", "as_of",
        "effective_allowance", "sources",
    }, "recorded allowance")
    if value["state"] != "recorded_not_enforced":
        raise CreditRuntimeError("recorded allowance state is unsupported")
    if value["enforcement_enabled"] is not False:
        raise CreditRuntimeError("recorded allowance must remain advisory")
    unit = value["unit"]
    if not isinstance(unit, str) or _ITEM_RE.fullmatch(unit) is None:
        raise CreditRuntimeError("recorded allowance unit is invalid")
    as_of = _utc(value["as_of"], "recorded allowance as_of")
    total = _non_negative_int(value["effective_allowance"], "effective allowance")
    raw_sources = value["sources"]
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
        raise CreditRuntimeError("recorded allowance sources must be an array")

    sources: list[AllowanceSource] = []
    seen: set[tuple[str, str | None]] = set()
    calculated_total = 0
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            raise CreditRuntimeError("recorded allowance source must be an object")
        _exact_keys(raw, {
            "source", "source_event_id", "granted_allowance",
            "effective_allowance", "expires_at", "status", "refund_state",
        }, "recorded allowance source")
        source = raw["source"]
        if not isinstance(source, str) or source not in SOURCE_KINDS:
            raise CreditRuntimeError("recorded allowance source is unsupported")
        event_id = raw["source_event_id"]
        if source == "free":
            if event_id is not None or raw["refund_state"] != "not_applicable":
                raise CreditRuntimeError("free allowance source is invalid")
        elif not isinstance(event_id, str) or _OPAQUE_RE.fullmatch(event_id) is None:
            raise CreditRuntimeError("allowance source event id is invalid")
        granted = _non_negative_int(raw["granted_allowance"], "granted allowance")
        effective = _non_negative_int(raw["effective_allowance"], "source allowance")
        if effective > granted:
            raise CreditRuntimeError("source allowance exceeds its grant")
        status = raw["status"]
        refund_state = raw["refund_state"]
        if (
            not isinstance(status, str)
            or status not in SOURCE_STATUSES
            or not isinstance(refund_state, str)
            or refund_state not in REFUND_STATES
        ):
            raise CreditRuntimeError("allowance source state is invalid")
        if source == "free":
            expected_status = "active" if effective else "inactive"
            if status != expected_status:
                raise CreditRuntimeError("free allowance source state is inconsistent")
        elif refund_state == "not_applicable":
            raise CreditRuntimeError("funded allowance refund state is invalid")
        if status in {"inactive", "refunded", "expired", "canceled"} and effective:
            raise CreditRuntimeError("inactive allowance cannot remain effective")
        if (
            refund_state in {"full", "excess"}
            and (status != "refunded" or effective != 0)
        ):
            raise CreditRuntimeError("fully adjusted allowance source is inconsistent")
        if status == "refunded" and refund_state not in {"full", "excess"}:
            raise CreditRuntimeError("refunded allowance source is inconsistent")
        expires_at = None
        if raw["expires_at"] is not None:
            expires_at = _utc(raw["expires_at"], "allowance source expiry")
            if effective and expires_at <= as_of:
                raise CreditRuntimeError("expired allowance cannot remain effective")
        identity = (source, event_id)
        if identity in seen:
            raise CreditRuntimeError("allowance source identity is duplicated")
        seen.add(identity)
        sources.append(AllowanceSource(source, event_id, effective, expires_at))
        calculated_total += effective
    if calculated_total != total:
        raise CreditRuntimeError("effective allowance total is inconsistent")
    return AllowanceSnapshot(unit, as_of, total, tuple(sources))


def parse_capability_priority(value: Mapping[str, Any]) -> CapabilityPriorityPolicy:
    """Accept an exact server-produced capability marker, never name matching."""

    if not isinstance(value, Mapping):
        raise CreditRuntimeError("capability priority policy must be an object")
    allowed = {
        "capability_id", "support_priority_eligible", "marker", "creator_term",
    }
    if not set(value).issubset(allowed) or not {
        "capability_id", "support_priority_eligible", "marker",
    }.issubset(value):
        raise CreditRuntimeError("capability priority policy schema is invalid")
    capability_id = value["capability_id"]
    eligible = value["support_priority_eligible"]
    marker = value["marker"]
    if not isinstance(capability_id, str) or _CAPABILITY_RE.fullmatch(capability_id) is None:
        raise CreditRuntimeError("capability id is invalid")
    if (
        type(eligible) is not bool
        or not isinstance(marker, str)
        or marker not in CAPABILITY_MARKERS
    ):
        raise CreditRuntimeError("capability priority policy is invalid")
    if eligible:
        if marker != "standard_support_priority_policy" or "creator_term" in value:
            raise CreditRuntimeError("eligible capability marker is invalid")
    else:
        creator_term = value.get("creator_term")
        if (
            marker != "creator_terms_exclude_support_priority"
            or not isinstance(creator_term, str)
            or _ITEM_RE.fullmatch(creator_term) is None
        ):
            raise CreditRuntimeError("excluded capability marker is invalid")
    return CapabilityPriorityPolicy(capability_id, eligible, marker)


def quote_reservation(
    *,
    realm: str,
    requested_units: int,
    recorded_allowance: Mapping[str, Any],
    capability_priority: Mapping[str, Any],
    policy: CreditRuntimePolicy = DEFAULT_CREDIT_RUNTIME_POLICY,
) -> CreditReservationQuote:
    """Return a deterministic decision; lack of credits never rejects work."""

    if not isinstance(realm, str) or realm not in REALMS:
        raise CreditRuntimeError("realm must be local, lan, or hosted")
    requested = _non_negative_int(requested_units, "requested units")
    if not isinstance(policy, CreditRuntimePolicy):
        raise CreditRuntimeError("credit runtime policy is invalid")
    snapshot = parse_recorded_allowance(recorded_allowance)
    capability = parse_capability_priority(capability_priority)
    allocations: list[SourceAllocation] = []
    metering = realm == "hosted" and policy.enforcement_enabled
    decision = "unmetered_realm" if realm != "hosted" else "hosted_baseline"
    priority_boost = False

    if metering and not capability.support_priority_eligible:
        decision = "capability_excluded"
    elif metering and requested > 0 and snapshot.effective_allowance >= requested:
        remaining = requested
        ordered = sorted(
            (source for source in snapshot.sources if source.effective_allowance > 0),
            key=lambda source: (
                source.expires_at is None,
                datetime.max.replace(tzinfo=timezone.utc)
                if source.expires_at is None else source.expires_at,
                source.source,
                source.source_event_id or "",
            ),
        )
        for source in ordered:
            units = min(source.effective_allowance, remaining)
            if units:
                allocations.append(SourceAllocation(
                    source.source,
                    source.source_event_id,
                    units,
                    None if source.expires_at is None else _iso(source.expires_at),
                ))
                remaining -= units
            if remaining == 0:
                break
        priority_boost = remaining == 0
        if priority_boost:
            decision = "hosted_priority_credit"

    reserved = sum(item.units for item in allocations) if priority_boost else 0
    return CreditReservationQuote(
        schema_version=SCHEMA_VERSION,
        realm=realm,
        unit=snapshot.unit,
        snapshot_as_of=_iso(snapshot.as_of),
        submission_allowed=True,
        decision=decision,
        policy_enforcement_enabled=policy.enforcement_enabled,
        metering_applied=metering,
        capability_priority_eligible=capability.support_priority_eligible,
        priority_boost=priority_boost,
        reservation_required=priority_boost and reserved > 0,
        requested_units=requested,
        reserved_units=reserved,
        allocations=tuple(allocations) if priority_boost else (),
    )


def _quote_fingerprint(quote: CreditReservationQuote) -> str:
    payload = {
        "schema_version": quote.schema_version,
        "realm": quote.realm,
        "unit": quote.unit,
        "snapshot_as_of": quote.snapshot_as_of,
        "decision": quote.decision,
        "requested_units": quote.requested_units,
        "reserved_units": quote.reserved_units,
        "allocations": [
            [item.source, item.source_event_id, item.units, item.expires_at]
            for item in quote.allocations
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _validate_reservation_state(state: Any) -> CreditReservationState:
    if not isinstance(state, CreditReservationState):
        raise CreditRuntimeError("reservation state is invalid")
    expected_revision = (
        {"reserved": 1, "released": 2, "consumed": 3}.get(state.status)
        if isinstance(state.status, str)
        else None
    )
    allocations_valid = isinstance(state.allocations, tuple)
    allocation_total = 0
    allocation_ids: set[tuple[str, str | None]] = set()
    if allocations_valid:
        for item in state.allocations:
            if (
                not isinstance(item, SourceAllocation)
                or not isinstance(item.source, str)
                or item.source not in SOURCE_KINDS
                or (
                    item.source == "free" and item.source_event_id is not None
                )
                or (
                    item.source != "free"
                    and (
                        not isinstance(item.source_event_id, str)
                        or _OPAQUE_RE.fullmatch(item.source_event_id) is None
                    )
                )
                or not isinstance(item.units, int)
                or isinstance(item.units, bool)
                or item.units <= 0
                or (
                    item.expires_at is not None
                    and not isinstance(item.expires_at, str)
                )
                or item.identity in allocation_ids
            ):
                allocations_valid = False
                break
            if item.expires_at is not None:
                _utc(item.expires_at, "allocation expiry")
            allocation_ids.add(item.identity)
            allocation_total += item.units
    if (
        not isinstance(state.schema_version, int)
        or isinstance(state.schema_version, bool)
        or state.schema_version != SCHEMA_VERSION
        or not isinstance(state.reservation_id, str)
        or _OPAQUE_RE.fullmatch(state.reservation_id) is None
        or not isinstance(state.quote_fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", state.quote_fingerprint) is None
        or not isinstance(state.unit, str)
        or _ITEM_RE.fullmatch(state.unit) is None
        or expected_revision is None
        or not isinstance(state.revision, int)
        or isinstance(state.revision, bool)
        or state.revision != expected_revision
        or _non_negative_int(state.reserved_units, "reserved units") == 0
        or not allocations_valid
        or allocation_total != state.reserved_units
    ):
        raise CreditRuntimeError("reservation state is invalid")
    _utc(state.snapshot_as_of, "reservation snapshot_as_of")
    return state


def reserve_quote(
    quote: CreditReservationQuote,
    *,
    reservation_id: str,
    current: CreditReservationState | None = None,
) -> CreditReservationState:
    """Create or idempotently replay a content-free logical reservation."""

    if not isinstance(quote, CreditReservationQuote) or not quote.reservation_required:
        raise CreditRuntimeError("quote does not require a reservation")
    if not isinstance(reservation_id, str) or _OPAQUE_RE.fullmatch(reservation_id) is None:
        raise CreditRuntimeError("reservation id must be an opaque token")
    fingerprint = _quote_fingerprint(quote)
    if current is not None:
        _validate_reservation_state(current)
        if current.reservation_id != reservation_id or current.quote_fingerprint != fingerprint:
            raise CreditTransitionConflict("reservation id is already bound")
        if (
            current.schema_version != SCHEMA_VERSION
            or current.unit != quote.unit
            or current.reserved_units != quote.reserved_units
            or current.snapshot_as_of != quote.snapshot_as_of
            or current.allocations != quote.allocations
        ):
            raise CreditTransitionConflict("current reservation state is inconsistent")
        return current
    return CreditReservationState(
        schema_version=SCHEMA_VERSION,
        reservation_id=reservation_id,
        quote_fingerprint=fingerprint,
        unit=quote.unit,
        status="reserved",
        revision=1,
        reserved_units=quote.reserved_units,
        snapshot_as_of=quote.snapshot_as_of,
        allocations=quote.allocations,
    )


def transition_reservation(
    state: CreditReservationState,
    action: str,
) -> CreditReservationState:
    """Apply an idempotent monotonic transition; consume wins ambiguity."""

    _validate_reservation_state(state)
    if not isinstance(action, str) or action not in {"consume", "release"}:
        raise CreditRuntimeError("reservation action is invalid")
    target = "consumed" if action == "consume" or state.status == "consumed" else "released"
    if target == state.status:
        return state
    revision = 3 if target == "consumed" else 2
    return CreditReservationState(
        schema_version=state.schema_version,
        reservation_id=state.reservation_id,
        quote_fingerprint=state.quote_fingerprint,
        unit=state.unit,
        status=target,
        revision=revision,
        reserved_units=state.reserved_units,
        snapshot_as_of=state.snapshot_as_of,
        allocations=state.allocations,
    )


def revalidate_reservation(
    state: CreditReservationState,
    recorded_allowance: Mapping[str, Any],
) -> CreditRevalidationResult:
    """Detect expiry/refund downgrades without blocking the submission."""

    _validate_reservation_state(state)
    snapshot = parse_recorded_allowance(recorded_allowance)
    if snapshot.unit != state.unit or snapshot.as_of < _utc(
        state.snapshot_as_of, "reservation snapshot_as_of",
    ):
        raise CreditRuntimeError("revalidation snapshot is incompatible")
    available = {source.identity: source.effective_allowance for source in snapshot.sources}
    retained = sum(
        min(item.units, available.get(item.identity, 0))
        for item in state.allocations
    )
    valid = retained == state.reserved_units
    if state.status == "released":
        return CreditRevalidationResult(
            state.reservation_id,
            state.quote_fingerprint,
            "released",
            True,
            False,
            retained,
            False,
            "reservation_released",
        )
    return CreditRevalidationResult(
        state.reservation_id,
        state.quote_fingerprint,
        "valid" if valid else "downgraded",
        True,
        valid,
        retained,
        not valid and state.status == "reserved",
        "allowance_current" if valid else "allowance_reduced",
    )


def public_credit_projection(
    quote: CreditReservationQuote,
    *,
    reservation: CreditReservationState | None = None,
    revalidation: CreditRevalidationResult | None = None,
) -> dict[str, Any]:
    """Return a content-free projection with no contribution source identity."""

    if not isinstance(quote, CreditReservationQuote):
        raise CreditRuntimeError("credit quote is invalid")
    if reservation is not None:
        _validate_reservation_state(reservation)
        if reservation.quote_fingerprint != _quote_fingerprint(quote):
            raise CreditRuntimeError("reservation does not match quote")
    if revalidation is not None and (
        not isinstance(revalidation, CreditRevalidationResult)
        or reservation is None
        or revalidation.reservation_id != reservation.reservation_id
        or revalidation.quote_fingerprint != reservation.quote_fingerprint
    ):
        raise CreditRuntimeError("credit revalidation is invalid")
    boost = quote.priority_boost and (
        reservation is None or reservation.status != "released"
    )
    if revalidation is not None:
        boost = revalidation.priority_boost_retained
    return {
        "schema_version": SCHEMA_VERSION,
        "realm": quote.realm,
        "submission_allowed": True,
        "decision": quote.decision,
        "policy_enforcement_enabled": quote.policy_enforcement_enabled,
        "metering_applied": quote.metering_applied,
        "unit": quote.unit,
        "requested_units": quote.requested_units,
        "priority_boost": boost,
        "reservation": None if reservation is None else {
            "state": reservation.status,
            "reserved_units": reservation.reserved_units,
        },
        "revalidation": None if revalidation is None else {
            "state": revalidation.state,
            "release_recommended": revalidation.release_recommended,
            "reason": revalidation.reason,
        },
    }
