"""Provider-neutral contribution ledger and derived entitlement projections.

The ledger is append-only at the semantic layer: corrections are represented
by compensating events, never mutation.  Persisted identities are opaque HMAC
keys.  The schema has no fields for prompts, jobs, media, outputs, logs, email
addresses, display names, or other creative/user content.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from types import MappingProxyType
from typing import Any

from services.host_terms import (
    KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
    KREA2_MOODY_CUTIE_V4_RECIPE_ID,
    KREA2_MOODY_MIX_V7_CREATOR_TERM,
    KREA2_MOODY_MIX_V7_RECIPE_ID,
)


LEDGER_SCHEMA_VERSION = 1
MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 50_000
GENESIS_HMAC = "0" * 64
EVENT_KINDS = frozenset({
    "one_time_contribution",
    "recurring_started",
    "recurring_renewed",
    "refund",
    "chargeback",
    "recurring_canceled",
    "fulfillment_set",
    "account_link_verified",
    "account_link_revoked",
})
FUNDING_KINDS = frozenset({
    "one_time_contribution", "recurring_started", "recurring_renewed",
})
ADJUSTMENT_KINDS = frozenset({"refund", "chargeback"})
RECURRING_KINDS = frozenset({
    "recurring_started", "recurring_renewed", "recurring_canceled",
})
FULFILLMENT_STATES = frozenset({"pending", "complete", "declined"})
ACCOUNT_LINK_KINDS = frozenset({
    "account_link_verified", "account_link_revoked",
})
_OPAQUE_KEY_RE = re.compile(r"key_[0-9a-f]{64}\Z")
_PROVIDER_RE = re.compile(r"[a-z][a-z0-9_]{1,47}\Z")
_ITEM_RE = re.compile(r"[a-z][a-z0-9_]{1,63}\Z")


class EntitlementError(ValueError):
    pass


class LedgerIntegrityError(EntitlementError):
    pass


class ContributionConflict(EntitlementError):
    pass


class ExclusiveLeaseError(EntitlementError):
    pass


def _acquire_native_lock(
    descriptor: int,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError as error:
            raise ExclusiveLeaseError(
                "Windows file locking is unavailable"
            ) from error
        if os.fstat(descriptor).st_size < 1:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                return "windows"
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise ExclusiveLeaseError(
                        "exclusive file lock is held by another process"
                    ) from error
                time.sleep(poll_seconds)
    try:
        import fcntl
    except ImportError as error:
        raise ExclusiveLeaseError("POSIX file locking is unavailable") from error
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return "posix"
        except BlockingIOError as error:
            if time.monotonic() >= deadline:
                raise ExclusiveLeaseError(
                    "exclusive file lock is held by another process"
                ) from error
            time.sleep(poll_seconds)
        except OSError as error:
            raise ExclusiveLeaseError("exclusive file lock cannot be acquired") from error


def _release_native_lock(descriptor: int, backend: str) -> None:
    if backend == "windows":
        try:
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
        except (ImportError, OSError) as error:
            raise ExclusiveLeaseError(
                "Windows file lock cannot be released"
            ) from error
    if backend == "posix":
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return
        except (ImportError, OSError) as error:
            raise ExclusiveLeaseError(
                "POSIX file lock cannot be released"
            ) from error
    raise ExclusiveLeaseError("exclusive file lock backend is invalid")


@contextmanager
def exclusive_file_lease(
    path: str | os.PathLike[str],
    *,
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.025,
) -> Iterator[None]:
    """Cross-platform native lock; process exit releases it automatically."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(target, flags, 0o600)
        opened = os.fstat(descriptor)
        named = target.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ExclusiveLeaseError("exclusive lock path is unsafe")
    except ExclusiveLeaseError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise ExclusiveLeaseError("exclusive lock path cannot be opened") from error
    backend: str | None = None
    try:
        backend = _acquire_native_lock(
            descriptor,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
        yield
    finally:
        try:
            if backend is not None:
                _release_native_lock(descriptor, backend)
        finally:
            os.close(descriptor)


def _secret_bytes(value: bytes | str, *, name: str) -> bytes:
    result = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(result, bytes) or len(result) < 32:
        raise EntitlementError(f"{name} must be at least 32 bytes")
    return result


def opaque_key(namespace: str, raw_value: str, secret: bytes | str) -> str:
    """Create a stable non-reversible key for provider and actor identities."""

    if not isinstance(namespace, str) or not _ITEM_RE.fullmatch(namespace):
        raise EntitlementError("opaque key namespace is invalid")
    if not isinstance(raw_value, str) or not raw_value or len(raw_value) > 1_024:
        raise EntitlementError("opaque key source is invalid")
    key = _secret_bytes(secret, name="identity secret")
    digest = hmac.new(
        key, f"{namespace}\0{raw_value}".encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return f"key_{digest}"


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and 1 <= len(value) <= 40:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise EntitlementError("event timestamp is invalid") from error
    else:
        raise EntitlementError("event timestamp is invalid")
    if parsed.tzinfo is None:
        raise EntitlementError("event timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime | str) -> str:
    return _as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


@dataclass(frozen=True, slots=True)
class TierRule:
    tier: str
    minimum_minor: int
    benefits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AllowanceRule:
    minimum_minor: int
    allowance_units: int


@dataclass(frozen=True, slots=True)
class RecordedAllowancePolicy:
    unit: str
    free_allowance_units: int
    one_time_rules: tuple[AllowanceRule, ...]
    recurring_rules: tuple[AllowanceRule, ...]
    one_time_cap_units: int
    one_time_validity_seconds: int
    recurring_validity_seconds: int


DEFAULT_RECORDED_ALLOWANCE_POLICY = RecordedAllowancePolicy(
    unit="compute_seconds",
    free_allowance_units=0,
    one_time_rules=(),
    recurring_rules=(),
    one_time_cap_units=0,
    one_time_validity_seconds=0,
    recurring_validity_seconds=0,
)


@dataclass(frozen=True, slots=True)
class BenefitPolicy:
    currency: str
    one_time_rules: tuple[TierRule, ...]
    recurring_rules: tuple[TierRule, ...]
    allowance_policy: RecordedAllowancePolicy = DEFAULT_RECORDED_ALLOWANCE_POLICY


DEFAULT_BENEFIT_POLICY = BenefitPolicy(
    currency="USD",
    one_time_rules=(
        TierRule("supporter", 500, ("supporter_record",)),
        TierRule(
            "backer", 2_500,
            ("supporter_record", "one_time_credit_eligibility"),
        ),
        TierRule(
            "sponsor", 10_000,
            (
                "supporter_record",
                "one_time_credit_eligibility",
                "retention_eligibility",
            ),
        ),
    ),
    recurring_rules=(
        TierRule("member", 300, ("recurring_supporter_record",)),
        TierRule(
            "sustainer", 1_000,
            ("recurring_supporter_record", "periodic_credit_eligibility"),
        ),
        TierRule(
            "patron", 2_500,
            (
                "recurring_supporter_record",
                "periodic_credit_eligibility",
                "retention_eligibility",
            ),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class SupportPriorityIdentityContract:
    capability_id: str
    creator_term: str
    support_priority_eligible: bool
    marker: str


SUPPORT_PRIORITY_IDENTITY_CONTRACTS = MappingProxyType({
    KREA2_MOODY_MIX_V7_RECIPE_ID: SupportPriorityIdentityContract(
        capability_id=KREA2_MOODY_MIX_V7_RECIPE_ID,
        creator_term=KREA2_MOODY_MIX_V7_CREATOR_TERM,
        support_priority_eligible=False,
        marker="creator_terms_exclude_support_priority",
    ),
    KREA2_MOODY_CUTIE_V4_RECIPE_ID: SupportPriorityIdentityContract(
        capability_id=KREA2_MOODY_CUTIE_V4_RECIPE_ID,
        creator_term=KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
        support_priority_eligible=False,
        marker="creator_terms_exclude_support_priority",
    ),
})


def support_priority_capability_marker(capability_id: str) -> dict[str, Any]:
    """Return a content-neutral marker for a canonical server capability ID."""

    contract = SUPPORT_PRIORITY_IDENTITY_CONTRACTS.get(capability_id)
    if contract is None:
        return {
            "capability_id": capability_id,
            "support_priority_eligible": True,
            "marker": "standard_support_priority_policy",
        }
    return {
        "capability_id": contract.capability_id,
        "support_priority_eligible": contract.support_priority_eligible,
        "marker": contract.marker,
        "creator_term": contract.creator_term,
    }


@dataclass(frozen=True, slots=True)
class ContributionEventDraft:
    provider: str
    source_event_key: str
    subject_key: str
    kind: str
    occurred_at: datetime | str
    amount_minor: int = 0
    currency: str = "USD"
    contract_key: str | None = None
    related_event_key: str | None = None
    fulfillment_item: str | None = None
    fulfillment_status: str | None = None
    actor_key: str | None = None


_STORED_EVENT_KEYS = frozenset({
    "sequence", "event_id", "provider", "source_event_key", "subject_key",
    "kind", "occurred_at", "received_at", "amount_minor", "currency",
    "contract_key", "related_event_key", "fulfillment_item",
    "fulfillment_status", "actor_key", "previous_hmac", "event_hmac",
})


@dataclass(frozen=True, slots=True)
class ContributionEvent:
    sequence: int
    event_id: str
    provider: str
    source_event_key: str
    subject_key: str
    kind: str
    occurred_at: str
    received_at: str
    amount_minor: int
    currency: str
    contract_key: str | None
    related_event_key: str | None
    fulfillment_item: str | None
    fulfillment_status: str | None
    actor_key: str | None
    previous_hmac: str
    event_hmac: str

    def record(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _STORED_EVENT_KEYS}


def _validate_opaque(value: str | None, *, name: str, required: bool) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or _OPAQUE_KEY_RE.fullmatch(value) is None:
        raise EntitlementError(f"{name} must be an opaque key")


def _normalize_draft(draft: ContributionEventDraft) -> dict[str, Any]:
    if not isinstance(draft, ContributionEventDraft):
        raise EntitlementError("contribution event must use the frozen draft schema")
    if (
        not isinstance(draft.provider, str)
        or _PROVIDER_RE.fullmatch(draft.provider) is None
    ):
        raise EntitlementError("provider identifier is invalid")
    _validate_opaque(draft.source_event_key, name="source event", required=True)
    _validate_opaque(draft.subject_key, name="subject", required=True)
    if draft.kind not in EVENT_KINDS:
        raise EntitlementError("contribution event kind is unsupported")
    if (
        not isinstance(draft.amount_minor, int)
        or isinstance(draft.amount_minor, bool)
        or draft.amount_minor < 0
        or draft.amount_minor > 10_000_000_000
    ):
        raise EntitlementError("amount_minor is invalid")
    if not isinstance(draft.currency, str) or not re.fullmatch(
        r"[A-Z]{3}", draft.currency,
    ):
        raise EntitlementError("currency must be a three-letter uppercase code")
    _validate_opaque(draft.contract_key, name="contract", required=False)
    _validate_opaque(draft.related_event_key, name="related event", required=False)
    _validate_opaque(draft.actor_key, name="actor", required=False)
    if draft.kind in FUNDING_KINDS and draft.amount_minor <= 0:
        raise EntitlementError("funding events require a positive amount")
    if draft.kind in ADJUSTMENT_KINDS:
        if draft.amount_minor <= 0 or draft.related_event_key is None:
            raise EntitlementError("adjustments require an amount and related event")
    if draft.kind in RECURRING_KINDS and draft.contract_key is None:
        raise EntitlementError("recurring events require an opaque contract key")
    if draft.kind in {
        "recurring_canceled", "fulfillment_set", *ACCOUNT_LINK_KINDS,
    } and draft.amount_minor:
        raise EntitlementError(f"{draft.kind} cannot carry money")
    if draft.kind in ACCOUNT_LINK_KINDS:
        if (
            draft.related_event_key is None
            or draft.contract_key is None
            or draft.fulfillment_item is not None
            or draft.fulfillment_status is not None
            or draft.actor_key is not None
        ):
            raise EntitlementError(
                "account link events require opaque provider and account subjects"
            )
    elif draft.kind == "fulfillment_set":
        if (
            not isinstance(draft.fulfillment_item, str)
            or _ITEM_RE.fullmatch(draft.fulfillment_item) is None
            or draft.fulfillment_status not in FULFILLMENT_STATES
            or draft.related_event_key is None
            or draft.actor_key is None
        ):
            raise EntitlementError("fulfillment events require item, state, actor, and target")
    elif any((draft.fulfillment_item, draft.fulfillment_status, draft.actor_key)):
        raise EntitlementError("fulfillment fields are reserved for fulfillment events")
    return {
        "provider": draft.provider,
        "source_event_key": draft.source_event_key,
        "subject_key": draft.subject_key,
        "kind": draft.kind,
        "occurred_at": _iso_utc(draft.occurred_at),
        "amount_minor": draft.amount_minor,
        "currency": draft.currency,
        "contract_key": draft.contract_key,
        "related_event_key": draft.related_event_key,
        "fulfillment_item": draft.fulfillment_item,
        "fulfillment_status": draft.fulfillment_status,
        "actor_key": draft.actor_key,
    }


def _tier(rules: Sequence[TierRule], amount_minor: int) -> TierRule | None:
    selected = None
    for rule in rules:
        if amount_minor >= rule.minimum_minor:
            selected = rule
    return selected


def _allowance_units(
    rules: Sequence[AllowanceRule], amount_minor: int,
) -> int:
    selected = 0
    for rule in rules:
        if amount_minor >= rule.minimum_minor:
            selected = rule.allowance_units
    return selected


def _events_for_subject(
    events: Sequence[ContributionEvent],
    subject_key: str,
    *,
    as_of: datetime,
) -> tuple[ContributionEvent, ...]:
    """Resolve provider subjects to one current account without rewriting history."""

    link_events: dict[tuple[str, str], list[ContributionEvent]] = defaultdict(list)
    for event in events:
        if (
            event.kind not in ACCOUNT_LINK_KINDS
            or event.related_event_key is None
            or _as_utc(event.occurred_at) > as_of
        ):
            continue
        link_events[(event.provider, event.related_event_key)].append(event)
    active_links: dict[tuple[str, str], ContributionEvent] = {}
    for key, items in link_events.items():
        current: ContributionEvent | None = None
        for event in sorted(
            items, key=lambda item: (_as_utc(item.occurred_at), item.sequence),
        ):
            if event.kind == "account_link_verified":
                # Transfers require an explicit revocation of the current owner.
                if current is None or current.subject_key == event.subject_key:
                    current = event
            elif current is not None and current.subject_key == event.subject_key:
                current = None
        if current is not None:
            active_links[key] = current
    active_provider_subjects = {
        key for key, event in active_links.items()
        if event.subject_key == subject_key
    }
    selected = {
        event.event_id: event
        for event in events
        if event.subject_key == subject_key
        and _as_utc(event.occurred_at) <= as_of
    }
    selected.update({
        event.event_id: event
        for event in events
        if event.kind not in ACCOUNT_LINK_KINDS
        and (event.provider, event.subject_key) in active_provider_subjects
        and _as_utc(event.occurred_at) <= as_of
    })
    return tuple(sorted(selected.values(), key=lambda event: event.sequence))


def _validate_link_transitions(events: Sequence[ContributionEvent]) -> None:
    grouped: dict[tuple[str, str], list[ContributionEvent]] = defaultdict(list)
    for event in events:
        if event.kind in ACCOUNT_LINK_KINDS and event.related_event_key:
            grouped[(event.provider, event.related_event_key)].append(event)
    for items in grouped.values():
        owner: str | None = None
        for event in sorted(
            items, key=lambda item: (_as_utc(item.occurred_at), item.sequence),
        ):
            if event.kind == "account_link_verified":
                if owner is not None and owner != event.subject_key:
                    raise EntitlementError(
                        "account link transfer requires an owner revocation"
                    )
                owner = event.subject_key
            elif owner != event.subject_key:
                raise EntitlementError(
                    "account link revocation must match the current owner"
                )
            else:
                owner = None


def _recorded_allowance_projection(
    events: Sequence[ContributionEvent],
    *,
    policy: RecordedAllowancePolicy,
    currency: str,
    as_of: datetime,
) -> dict[str, Any]:
    numeric_values = (
        policy.free_allowance_units,
        policy.one_time_cap_units,
        policy.one_time_validity_seconds,
        policy.recurring_validity_seconds,
    )
    rules = (*policy.one_time_rules, *policy.recurring_rules)
    if (
        not isinstance(policy.unit, str)
        or _ITEM_RE.fullmatch(policy.unit) is None
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in numeric_values
        )
        or any(
            not isinstance(rule, AllowanceRule)
            or not isinstance(rule.minimum_minor, int)
            or isinstance(rule.minimum_minor, bool)
            or rule.minimum_minor <= 0
            or not isinstance(rule.allowance_units, int)
            or isinstance(rule.allowance_units, bool)
            or rule.allowance_units < 0
            for rule in rules
        )
    ):
        raise EntitlementError("recorded allowance policy is invalid")

    funding = {
        (event.provider, event.source_event_key): event
        for event in events
        if event.kind in FUNDING_KINDS
        and event.currency == currency
        and _as_utc(event.occurred_at) <= as_of
    }
    adjustments: dict[tuple[str, str], int] = defaultdict(int)
    for event in events:
        if event.kind not in ADJUSTMENT_KINDS or _as_utc(event.occurred_at) > as_of:
            continue
        target_key = (event.provider, event.related_event_key or "")
        target = funding.get(target_key)
        if (
            target is not None
            and target.subject_key == event.subject_key
            and target.currency == event.currency
        ):
            adjustments[target_key] += event.amount_minor

    sources: list[dict[str, Any]] = [{
        "source": "free",
        "source_event_id": None,
        "granted_allowance": policy.free_allowance_units,
        "effective_allowance": policy.free_allowance_units,
        "expires_at": None,
        "status": (
            "active" if policy.free_allowance_units > 0 else "inactive"
        ),
        "refund_state": "not_applicable",
    }]
    one_time_remaining = policy.one_time_cap_units
    one_time_events = sorted(
        (
            event for event in funding.values()
            if event.kind == "one_time_contribution"
        ),
        key=lambda event: (_as_utc(event.occurred_at), event.sequence),
    )
    for event in one_time_events:
        key = (event.provider, event.source_event_key)
        adjusted = adjustments.get(key, 0)
        net_minor = max(0, event.amount_minor - adjusted)
        granted = _allowance_units(policy.one_time_rules, event.amount_minor)
        refundable = _allowance_units(policy.one_time_rules, net_minor)
        expires = (
            _as_utc(event.occurred_at)
            + timedelta(seconds=policy.one_time_validity_seconds)
            if policy.one_time_validity_seconds > 0
            else None
        )
        before_cap = refundable if expires is None or as_of < expires else 0
        effective = min(before_cap, one_time_remaining)
        one_time_remaining -= effective
        if granted == 0:
            status = "inactive"
        elif adjusted >= event.amount_minor:
            status = "refunded"
        elif expires is not None and as_of >= expires:
            status = "expired"
        elif effective < before_cap:
            status = "capped"
        else:
            status = "active"
        sources.append({
            "source": "one_time_support",
            "source_event_id": event.event_id,
            "granted_allowance": granted,
            "effective_allowance": effective,
            "expires_at": None if expires is None else _iso_utc(expires),
            "status": status,
            "refund_state": (
                "none" if adjusted == 0
                else "partial" if adjusted < event.amount_minor
                else "full" if adjusted == event.amount_minor
                else "excess"
            ),
        })

    contract_events: dict[tuple[str, str], list[ContributionEvent]] = defaultdict(list)
    for event in events:
        if (
            event.kind in RECURRING_KINDS
            and event.contract_key
            and (
                event.kind == "recurring_canceled"
                or event.currency == currency
            )
            and _as_utc(event.occurred_at) <= as_of
        ):
            contract_events[(event.provider, event.contract_key)].append(event)
    for items in contract_events.values():
        latest = max(
            items, key=lambda event: (_as_utc(event.occurred_at), event.sequence),
        )
        funding_events = [event for event in items if event.kind in FUNDING_KINDS]
        if not funding_events:
            continue
        source = max(
            funding_events,
            key=lambda event: (_as_utc(event.occurred_at), event.sequence),
        )
        key = (source.provider, source.source_event_key)
        adjusted = adjustments.get(key, 0)
        net_minor = max(0, source.amount_minor - adjusted)
        granted = _allowance_units(policy.recurring_rules, source.amount_minor)
        refundable = _allowance_units(policy.recurring_rules, net_minor)
        expires = (
            _as_utc(source.occurred_at)
            + timedelta(seconds=policy.recurring_validity_seconds)
            if policy.recurring_validity_seconds > 0
            else None
        )
        canceled = latest.kind == "recurring_canceled"
        effective = (
            0
            if canceled or (expires is not None and as_of >= expires)
            else refundable
        )
        if granted == 0:
            status = "inactive"
        elif adjusted >= source.amount_minor:
            status = "refunded"
        elif canceled:
            status = "canceled"
        elif expires is not None and as_of >= expires:
            status = "expired"
        else:
            status = "active"
        sources.append({
            "source": "recurring_support",
            "source_event_id": source.event_id,
            "granted_allowance": granted,
            "effective_allowance": effective,
            "expires_at": None if expires is None else _iso_utc(expires),
            "status": status,
            "refund_state": (
                "none" if adjusted == 0
                else "partial" if adjusted < source.amount_minor
                else "full" if adjusted == source.amount_minor
                else "excess"
            ),
        })

    return {
        "state": "recorded_not_enforced",
        "enforcement_enabled": False,
        "unit": policy.unit,
        "as_of": _iso_utc(as_of),
        "effective_allowance": sum(
            source["effective_allowance"] for source in sources
        ),
        "sources": sources,
    }


class ContributionLedger:
    """Integrity-sealed local event ledger with atomic append publication."""

    CANONICAL_PATH = (
        Path(__file__).resolve().parents[1]
        / "storage" / "support" / "contributions.json"
    )

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        integrity_key: bytes | str,
        allow_test_path: bool = False,
    ):
        candidate = Path(path or self.CANONICAL_PATH).absolute()
        if candidate != self.CANONICAL_PATH:
            try:
                candidate.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
            except ValueError as error:
                raise EntitlementError(
                    "custom contribution ledger paths must be temporary"
                ) from error
            if not allow_test_path:
                raise EntitlementError(
                    "custom contribution ledger paths require explicit test approval"
                )
        self.path = candidate
        self.lock_path = candidate.with_suffix(candidate.suffix + ".lock")
        self._integrity_key = _secret_bytes(
            integrity_key, name="ledger integrity key",
        )
        self._thread_lock = threading.RLock()

    @classmethod
    def from_environment(
        cls,
        *,
        path: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "ContributionLedger":
        selected = os.environ if env is None else env
        secret = selected.get("MAESTRO_SUPPORT_LEDGER_HMAC_KEY")
        if secret is None:
            raise EntitlementError("support ledger integrity key is not configured")
        return cls(path, integrity_key=secret)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._thread_lock:
            with exclusive_file_lease(self.lock_path):
                yield

    def _ledger_hmac(self, records: Sequence[Mapping[str, Any]]) -> str:
        return hmac.new(
            self._integrity_key, _canonical(list(records)), hashlib.sha256,
        ).hexdigest()

    def _event_hmac(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(
            self._integrity_key, _canonical(payload), hashlib.sha256,
        ).hexdigest()

    def _read_unlocked(self) -> tuple[ContributionEvent, ...]:
        if not self.path.exists():
            return ()
        try:
            size = self.path.stat().st_size
            if size > MAX_LEDGER_BYTES:
                raise LedgerIntegrityError("contribution ledger exceeds its bound")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except LedgerIntegrityError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise LedgerIntegrityError("contribution ledger is unreadable") from error
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "events", "ledger_hmac",
        }:
            raise LedgerIntegrityError("contribution ledger shape is invalid")
        records = payload.get("events")
        if (
            payload.get("schema_version") != LEDGER_SCHEMA_VERSION
            or not isinstance(records, list)
            or len(records) > MAX_EVENTS
            or not isinstance(payload.get("ledger_hmac"), str)
            or not hmac.compare_digest(
                payload["ledger_hmac"], self._ledger_hmac(records),
            )
        ):
            raise LedgerIntegrityError("contribution ledger integrity check failed")
        events: list[ContributionEvent] = []
        previous = GENESIS_HMAC
        identities: set[tuple[str, str]] = set()
        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict) or set(record) != _STORED_EVENT_KEYS:
                raise LedgerIntegrityError("stored contribution event shape is invalid")
            try:
                event = ContributionEvent(**record)
            except TypeError as error:
                raise LedgerIntegrityError("stored contribution event is invalid") from error
            if event.sequence != index or event.previous_hmac != previous:
                raise LedgerIntegrityError("contribution event chain order is invalid")
            unsigned = dict(record)
            supplied_hmac = unsigned.pop("event_hmac")
            if not isinstance(supplied_hmac, str) or not hmac.compare_digest(
                supplied_hmac, self._event_hmac(unsigned),
            ):
                raise LedgerIntegrityError("contribution event chain is invalid")
            try:
                normalized = _normalize_draft(ContributionEventDraft(
                    provider=event.provider,
                    source_event_key=event.source_event_key,
                    subject_key=event.subject_key,
                    kind=event.kind,
                    occurred_at=event.occurred_at,
                    amount_minor=event.amount_minor,
                    currency=event.currency,
                    contract_key=event.contract_key,
                    related_event_key=event.related_event_key,
                    fulfillment_item=event.fulfillment_item,
                    fulfillment_status=event.fulfillment_status,
                    actor_key=event.actor_key,
                ))
            except EntitlementError as error:
                raise LedgerIntegrityError("stored contribution event is malformed") from error
            expected_id = "evt_" + hashlib.sha256(
                f"{event.provider}\0{event.source_event_key}".encode("ascii")
            ).hexdigest()[:32]
            try:
                normalized_received = _iso_utc(event.received_at)
            except EntitlementError as error:
                raise LedgerIntegrityError(
                    "stored contribution receipt timestamp is invalid"
                ) from error
            if (
                event.event_id != expected_id
                or event.received_at != normalized_received
                or any(getattr(event, key) != value for key, value in normalized.items())
                or (event.provider, event.source_event_key) in identities
            ):
                raise LedgerIntegrityError("stored contribution event contract is invalid")
            identities.add((event.provider, event.source_event_key))
            previous = event.event_hmac
            events.append(event)
        try:
            _validate_link_transitions(events)
        except EntitlementError as error:
            raise LedgerIntegrityError(
                "stored account link transition is invalid"
            ) from error
        return tuple(events)

    def _write_unlocked(self, events: Sequence[ContributionEvent]) -> None:
        records = [event.record() for event in events]
        payload = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "events": records,
            "ledger_hmac": self._ledger_hmac(records),
        }
        encoded = _canonical(payload)
        if len(encoded) > MAX_LEDGER_BYTES:
            raise EntitlementError("contribution ledger would exceed its byte bound")
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        try:
            restrict_permissions = getattr(os, "fchmod", None)
            if callable(restrict_permissions):
                restrict_permissions(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            try:
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def events(self) -> tuple[ContributionEvent, ...]:
        with self._locked():
            return self._read_unlocked()

    def event_for_source(
        self, provider: str, source_event_key: str,
    ) -> ContributionEvent | None:
        if not isinstance(provider, str) or _PROVIDER_RE.fullmatch(provider) is None:
            raise EntitlementError("provider identifier is invalid")
        _validate_opaque(source_event_key, name="source event", required=True)
        with self._locked():
            return next((
                event for event in self._read_unlocked()
                if event.provider == provider
                and event.source_event_key == source_event_key
            ), None)

    def append(
        self,
        draft: ContributionEventDraft,
        *,
        received_at: datetime | str | None = None,
    ) -> ContributionEvent:
        normalized = _normalize_draft(draft)
        received = _iso_utc(received_at or datetime.now(timezone.utc))
        with self._locked():
            events = list(self._read_unlocked())
            for existing in events:
                if (
                    existing.provider == normalized["provider"]
                    and existing.source_event_key == normalized["source_event_key"]
                ):
                    if all(
                        getattr(existing, key) == value
                        for key, value in normalized.items()
                    ):
                        return existing
                    raise ContributionConflict(
                        "provider event key was reused with different contribution data"
                    )
            if len(events) >= MAX_EVENTS:
                raise EntitlementError("contribution ledger event bound reached")
            sequence = len(events) + 1
            previous = events[-1].event_hmac if events else GENESIS_HMAC
            event_id = "evt_" + hashlib.sha256(
                f"{normalized['provider']}\0{normalized['source_event_key']}".encode(
                    "ascii"
                )
            ).hexdigest()[:32]
            unsigned = {
                "sequence": sequence,
                "event_id": event_id,
                **normalized,
                "received_at": received,
                "previous_hmac": previous,
            }
            event = ContributionEvent(
                **unsigned,
                event_hmac=self._event_hmac(unsigned),
            )
            events.append(event)
            _validate_link_transitions(events)
            self._write_unlocked(events)
            return event

    def privacy_safe_user_projection(
        self,
        subject_key: str,
        *,
        policy: BenefitPolicy = DEFAULT_BENEFIT_POLICY,
        as_of: datetime | str | None = None,
    ) -> dict[str, Any]:
        _validate_opaque(subject_key, name="subject", required=True)
        projected_at = _as_utc(as_of or datetime.now(timezone.utc))
        events = _events_for_subject(
            self.events(), subject_key, as_of=projected_at,
        )
        return _project_events(
            events,
            policy=policy,
            admin=False,
            as_of=projected_at,
            projection_subject_key=subject_key,
        )

    def reauthenticated_admin_projection(
        self,
        subject_key: str,
        *,
        policy: BenefitPolicy = DEFAULT_BENEFIT_POLICY,
        as_of: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Return opaque reconciliation fields after route-level reauth."""

        _validate_opaque(subject_key, name="subject", required=True)
        projected_at = _as_utc(as_of or datetime.now(timezone.utc))
        events = _events_for_subject(
            self.events(), subject_key, as_of=projected_at,
        )
        return _project_events(
            events,
            policy=policy,
            admin=True,
            as_of=projected_at,
            projection_subject_key=subject_key,
        )


def _project_events(
    events: Sequence[ContributionEvent],
    *,
    policy: BenefitPolicy,
    admin: bool,
    as_of: datetime,
    projection_subject_key: str,
) -> dict[str, Any]:
    funding = {
        (event.provider, event.source_event_key): event
        for event in events if event.kind in FUNDING_KINDS
    }
    adjustments: dict[tuple[str, str], int] = defaultdict(int)
    unresolved: list[dict[str, str]] = []
    for event in events:
        if event.kind not in ADJUSTMENT_KINDS:
            continue
        target_key = (event.provider, event.related_event_key or "")
        target = funding.get(target_key)
        if (
            target is None
            or target.subject_key != event.subject_key
            or target.currency != event.currency
        ):
            unresolved.append({
                "event_id": event.event_id,
                "reason": "unresolved_or_mismatched_adjustment",
            })
            continue
        adjustments[target_key] += event.amount_minor
    totals: dict[str, int] = defaultdict(int)
    one_time_total = 0
    net_by_source: dict[tuple[str, str], int] = {}
    for source_key, event in funding.items():
        adjusted = adjustments.get(source_key, 0)
        net = max(0, event.amount_minor - adjusted)
        if adjusted > event.amount_minor:
            unresolved.append({
                "event_id": event.event_id,
                "reason": "adjustments_exceed_contribution",
            })
        net_by_source[source_key] = net
        totals[event.currency] += net
        if event.kind == "one_time_contribution" and event.currency == policy.currency:
            one_time_total += net

    contract_events: dict[tuple[str, str], list[ContributionEvent]] = defaultdict(list)
    for event in events:
        if event.kind in RECURRING_KINDS and event.contract_key:
            contract_events[(event.provider, event.contract_key)].append(event)
    active_contracts: list[ContributionEvent] = []
    for items in contract_events.values():
        latest = max(items, key=lambda item: (_as_utc(item.occurred_at), item.sequence))
        if latest.kind != "recurring_canceled":
            active_contracts.append(latest)
    recurring_amount = max(
        (
            net_by_source.get(
                (event.provider, event.source_event_key), event.amount_minor,
            )
            for event in active_contracts
            if event.currency == policy.currency
        ),
        default=0,
    )
    one_time_rule = _tier(policy.one_time_rules, one_time_total)
    recurring_rule = _tier(policy.recurring_rules, recurring_amount)
    benefits = tuple(dict.fromkeys(
        (*(() if one_time_rule is None else one_time_rule.benefits),
         *(() if recurring_rule is None else recurring_rule.benefits))
    ))

    event_by_source = {
        (event.provider, event.source_event_key): event for event in events
    }
    fulfillment_latest: dict[tuple[str, str, str], ContributionEvent] = {}
    for event in events:
        if event.kind == "fulfillment_set" and event.related_event_key:
            key = (
                event.provider,
                event.related_event_key,
                event.fulfillment_item or "",
            )
            previous = fulfillment_latest.get(key)
            if previous is None or (
                _as_utc(event.occurred_at), event.sequence
            ) > (
                _as_utc(previous.occurred_at), previous.sequence
            ):
                fulfillment_latest[key] = event
    fulfillment = []
    for (provider, related_key, item), event in sorted(
        fulfillment_latest.items()
    ):
        target = event_by_source.get((provider, related_key))
        projected = {
            "target_event_id": target.event_id if target else None,
            "item": item,
            "status": event.fulfillment_status,
        }
        if admin:
            projected.update({
                "audit_event_id": event.event_id,
                "actor_key": event.actor_key,
                "changed_at": event.occurred_at,
            })
        fulfillment.append(projected)

    result: dict[str, Any] = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "currency_totals_minor": dict(sorted(totals.items())),
        "one_time_tier": None if one_time_rule is None else one_time_rule.tier,
        "recurring_tier": None if recurring_rule is None else recurring_rule.tier,
        "active_recurring_count": len(active_contracts),
        "benefit_eligibility": list(benefits),
        "recorded_allowance": _recorded_allowance_projection(
            events,
            policy=policy.allowance_policy,
            currency=policy.currency,
            as_of=as_of,
        ),
        "fulfillment": fulfillment,
        "event_count": len(events),
    }
    if admin:
        result.update({
            "subject_key": projection_subject_key,
            "unresolved": unresolved,
            "audit": [
                {
                    "sequence": event.sequence,
                    "event_id": event.event_id,
                    "provider": event.provider,
                    "source_event_key": event.source_event_key,
                    "kind": event.kind,
                    "occurred_at": event.occurred_at,
                    "received_at": event.received_at,
                    "amount_minor": event.amount_minor,
                    "currency": event.currency,
                    "contract_key": event.contract_key,
                    "related_event_key": event.related_event_key,
                    "fulfillment_item": event.fulfillment_item,
                    "fulfillment_status": event.fulfillment_status,
                    "actor_key": event.actor_key,
                }
                for event in events
            ],
        })
    return result
