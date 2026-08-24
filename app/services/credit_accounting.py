"""Durable, content-free accounting for recorded credit reservations.

This module is deliberately library-only and disabled by default.  It stores
only opaque keyed identifiers, integer unit counts, timestamps, and lifecycle
state.  No prompt, job, media, provider, or account profile data is accepted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from services.credit_runtime import terminal_credit_settlement
from services.entitlements import exclusive_file_lease

SCHEMA_VERSION = 3
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_ACCOUNTS = 10_000
MAX_SOURCES_PER_ACCOUNT = 1_024
MAX_RESERVATIONS_PER_ACCOUNT = 10_000
MAX_OPERATIONS = 50_000
_KEY_RE = re.compile(r"key_[0-9a-f]{64}\Z")
_RESERVATION_RE = re.compile(r"reservation_[0-9a-f]{32,64}\Z")
_OPERATION_RE = re.compile(r"operation_[0-9a-f]{32,64}\Z")
_UNIT_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_UTC_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z",
)
_STATUSES = frozenset({
    "pending", "consumed", "settled", "released", "invalidated",
})
_RECEIPT_STATES = frozenset({
    "reconciled", "reserved", "revalidated",
    "consumed", "settled", "released", "terminal_satisfied",
})
_RECEIPT_STATE_STATUSES = {
    "reconciled": frozenset({None}),
    "reserved": frozenset({"pending", "invalidated"}),
    "revalidated": _STATUSES,
    "consumed": frozenset({"consumed"}),
    "settled": frozenset({"settled"}),
    "released": frozenset({"released"}),
    "terminal_satisfied": frozenset({"released", "invalidated"}),
}


class CreditAccountingError(ValueError):
    """Base error for invalid accounting requests."""


class CreditAccountingConflict(CreditAccountingError):
    """An opaque operation or reservation identifier was rebound."""


class CreditAccountingIntegrityError(CreditAccountingError):
    """Persisted accounting state was unsafe, corrupt, or unauthenticated."""


@dataclass(frozen=True, slots=True)
class CreditAccountingPolicy:
    """Explicit future opt-in; production callers currently inherit off."""

    enforcement_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.enforcement_enabled) is not bool:
            raise CreditAccountingError("enforcement_enabled must be boolean")


DEFAULT_CREDIT_ACCOUNTING_POLICY = CreditAccountingPolicy()


@dataclass(frozen=True, slots=True)
class CreditSourceBalance:
    """Authoritative units for one opaque, non-monetary source grant.

    Supporter tier bonuses reach accounting only after server policy converts
    the promotional grant to bounded units. Currency, purchase price, cash
    value, transfer rights, and service guarantees are intentionally absent.
    """

    source_key: str
    effective_units: int
    expires_at: datetime | str | None = None


@dataclass(frozen=True, slots=True)
class CreditAccountingReceipt:
    """Content-free result suitable for a caller-owned private association."""

    state: str
    reservation_status: str | None
    requested_units: int
    affected_units: int
    reservation_revision: int | None
    fully_funded: bool | None
    allocation_satisfied: bool | None
    terminal_satisfied: bool | None
    clock_high_water: str


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def _secret(value: bytes | str) -> bytes:
    result = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(result, bytes) or len(result) < 32:
        raise CreditAccountingError("integrity key must be at least 32 bytes")
    return result


def _opaque(value: Any, pattern: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CreditAccountingError(f"{name} must be an opaque keyed identifier")
    return value


def _units(value: Any, name: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise CreditAccountingError(f"{name} must be a {qualifier} integer")
    return value


def _utc(value: datetime | str, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and 1 <= len(value) <= 40:
        if _UTC_TIMESTAMP_RE.fullmatch(value) is None:
            raise CreditAccountingError(f"{name} must be a canonical UTC timestamp")
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            raise CreditAccountingError(f"{name} is invalid") from None
    else:
        raise CreditAccountingError(f"{name} is invalid")
    if parsed.tzinfo is None:
        raise CreditAccountingError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | str) -> str:
    return _utc(value, "timestamp").isoformat().replace(
        "+00:00", "Z",
    )


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "clock_high_water": None,
        "next_sequence": 1,
        "accounts": {},
        "operations": {},
    }


def _normalize_source_balances(
    sources: Sequence[CreditSourceBalance],
) -> list[dict[str, Any]]:
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        raise CreditAccountingError("sources must be a sequence")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, CreditSourceBalance):
            raise CreditAccountingError("source balance is invalid")
        source_key = _opaque(source.source_key, _KEY_RE, "source key")
        if source_key in seen:
            raise CreditAccountingError("source key is duplicated")
        seen.add(source_key)
        normalized.append({
            "source_key": source_key,
            "effective_units": _units(source.effective_units, "effective units"),
            "expires_at": (
                None if source.expires_at is None else _iso(source.expires_at)
            ),
        })
    if len(normalized) > MAX_SOURCES_PER_ACCOUNT:
        raise CreditAccountingError("source bound reached")
    normalized.sort(key=lambda item: item["source_key"])
    return normalized


class CreditAccountingJournal:
    """Integrity-sealed, cross-process atomic source-lot reservation journal."""

    _locks_guard = threading.Lock()
    _path_locks: ClassVar[dict[str, threading.RLock]] = {}

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        integrity_key: bytes | str,
        policy: CreditAccountingPolicy = DEFAULT_CREDIT_ACCOUNTING_POLICY,
        max_state_bytes: int = MAX_STATE_BYTES,
    ) -> None:
        if not isinstance(policy, CreditAccountingPolicy):
            raise CreditAccountingError("accounting policy is invalid")
        if not isinstance(max_state_bytes, int) or max_state_bytes < 4_096:
            raise CreditAccountingError("state byte bound is invalid")
        candidate = Path(path).expanduser().absolute()
        try:
            if candidate.parent.is_symlink() or not candidate.parent.is_dir():
                raise ValueError
            parent = candidate.parent.resolve(strict=True)
        except (OSError, ValueError):
            raise CreditAccountingError("accounting path is invalid") from None
        self.path = parent / candidate.name
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._integrity_key = _secret(integrity_key)
        self.policy = policy
        self.max_state_bytes = max_state_bytes
        path_key = os.path.normcase(str(self.path))
        with self._locks_guard:
            self._thread_lock = self._path_locks.setdefault(
                path_key, threading.RLock(),
            )

    def _seal(self, state: Mapping[str, Any]) -> str:
        return hmac.new(
            self._integrity_key, _canonical(state), hashlib.sha256,
        ).hexdigest()

    def _safe_existing_file(self) -> os.stat_result | None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CreditAccountingIntegrityError("accounting path is unsafe")
        return info

    def _read_unlocked(self) -> dict[str, Any]:
        before = self._safe_existing_file()
        if before is None:
            return _empty_state()
        if before.st_size > self.max_state_bytes:
            raise CreditAccountingIntegrityError("accounting state exceeds its bound")
        descriptor = -1
        try:
            descriptor = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = os.fstat(descriptor)
            after = self.path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
            ):
                raise CreditAccountingIntegrityError("accounting state changed unsafely")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read(self.max_state_bytes + 1)
            if len(raw) > self.max_state_bytes:
                raise CreditAccountingIntegrityError(
                    "accounting state exceeds its bound",
                )
            payload = json.loads(raw.decode("utf-8"))
        except CreditAccountingIntegrityError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise CreditAccountingIntegrityError("accounting state is unreadable") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(payload, dict) or set(payload) != {"state", "state_hmac"}:
            raise CreditAccountingIntegrityError("accounting envelope is invalid")
        state = payload.get("state")
        supplied = payload.get("state_hmac")
        if (
            not isinstance(state, dict)
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, self._seal(state))
        ):
            raise CreditAccountingIntegrityError("accounting integrity check failed")
        self._validate_state(state)
        return state

    def _write_unlocked(self, state: Mapping[str, Any]) -> None:
        self._validate_state(state)
        encoded = _canonical({"state": state, "state_hmac": self._seal(state)})
        if len(encoded) > self.max_state_bytes:
            raise CreditAccountingError("accounting state would exceed its bound")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        try:
            restrict = getattr(os, "fchmod", None)
            if callable(restrict):
                restrict(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._safe_existing_file()
            os.replace(temporary, self.path)
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
                os.unlink(temporary)
            except OSError:
                pass

    def _locked_state(self) -> tuple[Any, dict[str, Any]]:
        lease = exclusive_file_lease(self.lock_path)
        lease.__enter__()
        try:
            return lease, self._read_unlocked()
        except BaseException:
            lease.__exit__(None, None, None)
            raise

    def probe_readiness(self) -> None:
        """Validate existing state and destination access without publishing it."""
        with self._thread_lock:
            try:
                with exclusive_file_lease(self.lock_path):
                    self._read_unlocked()
            except CreditAccountingError:
                raise
            except (OSError, ValueError) as error:
                raise CreditAccountingIntegrityError(
                    "accounting destination is unavailable",
                ) from error
            if not os.access(self.path.parent, os.W_OK | os.X_OK):
                raise CreditAccountingIntegrityError(
                    "accounting destination is unavailable",
                )

    def _close_locked_state(
        self, lease: Any, state: dict[str, Any], *, write: bool,
    ) -> None:
        try:
            if write:
                self._write_unlocked(state)
        finally:
            lease.__exit__(None, None, None)

    @staticmethod
    def _new_account(unit: str) -> dict[str, Any]:
        return {"unit": unit, "sources": {}, "reservations": {}}

    def _validate_state(self, state: Mapping[str, Any]) -> None:
        try:
            if set(state) != {
                "schema_version", "clock_high_water", "next_sequence",
                "accounts", "operations",
            } or state["schema_version"] != SCHEMA_VERSION:
                raise ValueError
            high_water = state["clock_high_water"]
            if high_water is not None:
                _iso(high_water)
            _units(state["next_sequence"], "next sequence", positive=True)
            accounts = state["accounts"]
            operations = state["operations"]
            if (
                not isinstance(accounts, dict)
                or len(accounts) > MAX_ACCOUNTS
                or not isinstance(operations, dict)
                or len(operations) > MAX_OPERATIONS
            ):
                raise ValueError
            sequence_values: set[int] = set()
            for account_key, account in accounts.items():
                _opaque(account_key, _KEY_RE, "account key")
                if not isinstance(account, dict) or set(account) != {
                    "unit", "sources", "reservations",
                }:
                    raise ValueError
                if not isinstance(account["unit"], str) or _UNIT_RE.fullmatch(
                    account["unit"],
                ) is None:
                    raise ValueError
                sources = account["sources"]
                reservations = account["reservations"]
                if (
                    not isinstance(sources, dict)
                    or len(sources) > MAX_SOURCES_PER_ACCOUNT
                    or not isinstance(reservations, dict)
                    or len(reservations) > MAX_RESERVATIONS_PER_ACCOUNT
                ):
                    raise ValueError
                consumed_by_source = dict.fromkeys(sources, 0)
                pending_by_source = dict.fromkeys(sources, 0)
                for source_key, source in sources.items():
                    _opaque(source_key, _KEY_RE, "source key")
                    if not isinstance(source, dict) or set(source) != {
                        "effective_units", "expires_at", "consumed_units",
                    }:
                        raise ValueError
                    effective = _units(
                        source["effective_units"], "effective units",
                    )
                    consumed = _units(source["consumed_units"], "consumed units")
                    if consumed > effective and source["expires_at"] is None:
                        # A refund may lower effective units below prior consumption.
                        pass
                    if source["expires_at"] is not None:
                        _iso(source["expires_at"])
                for reservation_key, reservation in reservations.items():
                    _opaque(reservation_key, _RESERVATION_RE, "reservation id")
                    if not isinstance(reservation, dict) or set(reservation) != {
                        "status", "created_sequence", "requested_units",
                        "revision", "allocations",
                    }:
                        raise ValueError
                    if reservation["status"] not in _STATUSES:
                        raise ValueError
                    sequence = _units(
                        reservation["created_sequence"],
                        "reservation sequence", positive=True,
                    )
                    if sequence in sequence_values:
                        raise ValueError
                    sequence_values.add(sequence)
                    requested = _units(
                        reservation["requested_units"],
                        "reservation requested units", positive=True,
                    )
                    _units(
                        reservation["revision"],
                        "reservation revision", positive=True,
                    )
                    allocations = reservation["allocations"]
                    if not isinstance(allocations, list):
                        raise TypeError
                    seen_sources: set[str] = set()
                    for allocation in allocations:
                        if not isinstance(allocation, dict) or set(allocation) != {
                            "source_key", "units",
                        }:
                            raise ValueError
                        source_key = _opaque(
                            allocation["source_key"], _KEY_RE, "source key",
                        )
                        if source_key not in sources or source_key in seen_sources:
                            raise ValueError
                        seen_sources.add(source_key)
                        allocated = _units(
                            allocation["units"], "allocated units", positive=True,
                        )
                        if reservation["status"] in {"consumed", "settled"}:
                            consumed_by_source[source_key] += allocated
                        elif reservation["status"] == "pending":
                            pending_by_source[source_key] += allocated
                    if reservation["status"] == "pending" and not allocations:
                        raise ValueError
                    if reservation["status"] == "invalidated" and allocations:
                        raise ValueError
                    if reservation["status"] in {"consumed", "released"} and not allocations:
                        raise ValueError
                    if sum(item["units"] for item in allocations) > requested:
                        raise ValueError
                for source_key, source in sources.items():
                    if consumed_by_source[source_key] != source["consumed_units"]:
                        raise ValueError
                    if pending_by_source[source_key] > max(
                        0,
                        source["effective_units"] - source["consumed_units"],
                    ):
                        raise ValueError
            for operation_id, operation in operations.items():
                _opaque(operation_id, _OPERATION_RE, "operation id")
                if not isinstance(operation, dict) or set(operation) != {
                    "fingerprint", "receipt",
                }:
                    raise ValueError
                if (
                    not isinstance(operation["fingerprint"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", operation["fingerprint"]) is None
                ):
                    raise ValueError
                self._receipt_from_mapping(operation["receipt"])
            if sequence_values and state["next_sequence"] <= max(sequence_values):
                raise ValueError
        except (CreditAccountingError, KeyError, TypeError, ValueError):
            raise CreditAccountingIntegrityError("accounting state schema is invalid") from None

    @staticmethod
    def _receipt_from_mapping(value: Any) -> CreditAccountingReceipt:
        if not isinstance(value, Mapping) or set(value) != {
            "state", "reservation_status", "requested_units",
            "affected_units", "reservation_revision", "fully_funded",
            "allocation_satisfied", "terminal_satisfied", "clock_high_water",
        }:
            raise CreditAccountingError("operation receipt is invalid")
        if value["state"] not in _RECEIPT_STATES or value["reservation_status"] not in (
            _STATUSES | {None}
        ):
            raise CreditAccountingError("operation receipt state is invalid")
        revision = value["reservation_revision"]
        if revision is not None:
            revision = _units(revision, "reservation revision", positive=True)
        fully_funded = value["fully_funded"]
        if fully_funded is not None and type(fully_funded) is not bool:
            raise CreditAccountingError("fully funded state is invalid")
        allocation_satisfied = value["allocation_satisfied"]
        if allocation_satisfied is not None and type(allocation_satisfied) is not bool:
            raise CreditAccountingError("allocation satisfied state is invalid")
        terminal_satisfied = value["terminal_satisfied"]
        if terminal_satisfied is not None and type(terminal_satisfied) is not bool:
            raise CreditAccountingError("terminal satisfied state is invalid")
        requested_units = _units(value["requested_units"], "requested units")
        affected_units = _units(value["affected_units"], "affected units")
        status = value["reservation_status"]
        if status not in _RECEIPT_STATE_STATUSES[value["state"]]:
            raise CreditAccountingError("operation receipt state is inconsistent")
        if (status is None) is not (revision is None):
            raise CreditAccountingError("reservation status and revision are inconsistent")
        if status is None:
            if any(item is not None for item in (
                fully_funded, allocation_satisfied, terminal_satisfied,
            )):
                raise CreditAccountingError("reservation receipt signals are invalid")
        else:
            if requested_units <= 0 or affected_units > requested_units:
                raise CreditAccountingError("reservation receipt units are inconsistent")
            if (
                fully_funded
                is not (status == "pending" and affected_units == requested_units)
                or allocation_satisfied
                is not (status == "consumed" and affected_units == requested_units)
                or terminal_satisfied
                is not (status in {"settled", "released", "invalidated"})
            ):
                raise CreditAccountingError(
                    "reservation receipt signals are inconsistent",
                )
        return CreditAccountingReceipt(
            state=value["state"],
            reservation_status=status,
            requested_units=requested_units,
            affected_units=affected_units,
            reservation_revision=revision,
            fully_funded=fully_funded,
            allocation_satisfied=allocation_satisfied,
            terminal_satisfied=terminal_satisfied,
            clock_high_water=_iso(value["clock_high_water"]),
        )

    @staticmethod
    def _receipt_mapping(receipt: CreditAccountingReceipt) -> dict[str, Any]:
        return {
            "state": receipt.state,
            "reservation_status": receipt.reservation_status,
            "requested_units": receipt.requested_units,
            "affected_units": receipt.affected_units,
            "reservation_revision": receipt.reservation_revision,
            "fully_funded": receipt.fully_funded,
            "allocation_satisfied": receipt.allocation_satisfied,
            "terminal_satisfied": receipt.terminal_satisfied,
            "clock_high_water": receipt.clock_high_water,
        }

    def _advance_clock(self, state: dict[str, Any], as_of: datetime | str) -> str:
        candidate = _utc(as_of, "as_of")
        previous_raw = state["clock_high_water"]
        if previous_raw is not None:
            candidate = max(candidate, _utc(previous_raw, "clock high-water"))
        high_water = _iso(candidate)
        state["clock_high_water"] = high_water
        for account in state["accounts"].values():
            for source in account["sources"].values():
                expiry = source["expires_at"]
                if expiry is not None and _utc(expiry, "source expiry") <= candidate:
                    source["effective_units"] = 0
            self._trim_pending(account)
        return high_water

    @staticmethod
    def _pending_by_source(account: Mapping[str, Any]) -> dict[str, int]:
        totals = {source_key: 0 for source_key in account["sources"]}
        for reservation in account["reservations"].values():
            if reservation["status"] != "pending":
                continue
            for allocation in reservation["allocations"]:
                totals[allocation["source_key"]] += allocation["units"]
        return totals

    def _trim_pending(self, account: dict[str, Any]) -> None:
        remaining = {
            source_key: max(0, source["effective_units"] - source["consumed_units"])
            for source_key, source in account["sources"].items()
        }
        ordered = sorted(
            account["reservations"].values(),
            key=lambda item: item["created_sequence"],
        )
        for reservation in ordered:
            if reservation["status"] != "pending":
                continue
            previous_allocations = reservation["allocations"]
            previous_status = reservation["status"]
            kept: list[dict[str, Any]] = []
            for allocation in reservation["allocations"]:
                source_key = allocation["source_key"]
                retained = min(allocation["units"], remaining[source_key])
                if retained:
                    kept.append({"source_key": source_key, "units": retained})
                    remaining[source_key] -= retained
            reservation["allocations"] = kept
            if not kept:
                reservation["status"] = "invalidated"
            if (
                reservation["allocations"] != previous_allocations
                or reservation["status"] != previous_status
            ):
                reservation["revision"] += 1

    def _replay_or_conflict(
        self, state: Mapping[str, Any], operation_id: str, fingerprint: str,
    ) -> CreditAccountingReceipt | None:
        existing = state["operations"].get(operation_id)
        if existing is None:
            return None
        if not hmac.compare_digest(existing["fingerprint"], fingerprint):
            raise CreditAccountingConflict(
                "operation id was reused with different accounting data",
            )
        return self._receipt_from_mapping(existing["receipt"])

    def _record_operation(
        self,
        state: dict[str, Any],
        operation_id: str,
        fingerprint: str,
        receipt: CreditAccountingReceipt,
    ) -> None:
        if len(state["operations"]) >= MAX_OPERATIONS:
            raise CreditAccountingError("accounting operation bound reached")
        state["operations"][operation_id] = {
            "fingerprint": fingerprint,
            "receipt": self._receipt_mapping(receipt),
        }

    @staticmethod
    def _require_operation_capacity(state: Mapping[str, Any]) -> None:
        if len(state["operations"]) >= MAX_OPERATIONS:
            raise CreditAccountingError("accounting operation bound reached")

    def _apply_source_reconciliation(
        self,
        account: dict[str, Any],
        normalized: Sequence[Mapping[str, Any]],
        high_water: str,
    ) -> None:
        prior_sources = account["sources"]
        for current in prior_sources.values():
            current["effective_units"] = 0
        now = _utc(high_water, "clock high-water")
        for source in normalized:
            existing = prior_sources.get(source["source_key"])
            if existing is None:
                existing = {
                    "effective_units": 0,
                    "expires_at": None,
                    "consumed_units": 0,
                }
                prior_sources[source["source_key"]] = existing
            effective = source["effective_units"]
            expiry = source["expires_at"]
            if expiry is not None and _utc(expiry, "source expiry") <= now:
                effective = 0
            existing["effective_units"] = effective
            existing["expires_at"] = expiry
        self._trim_pending(account)

    @staticmethod
    def _reservation_receipt(
        state_name: str,
        reservation: Mapping[str, Any],
        high_water: str,
    ) -> CreditAccountingReceipt:
        affected = sum(
            allocation["units"] for allocation in reservation["allocations"]
        )
        requested = reservation["requested_units"]
        return CreditAccountingReceipt(
            state_name,
            reservation["status"],
            requested,
            affected,
            reservation["revision"],
            reservation["status"] == "pending" and affected == requested,
            reservation["status"] == "consumed" and affected == requested,
            reservation["status"] in {"settled", "released", "invalidated"},
            high_water,
        )

    def reconcile(
        self,
        *,
        account_key: str,
        operation_id: str,
        unit: str,
        sources: Sequence[CreditSourceBalance],
        as_of: datetime | str,
    ) -> CreditAccountingReceipt:
        """Reconcile source truth, trimming pending units before consumed units."""

        account_key = _opaque(account_key, _KEY_RE, "account key")
        operation_id = _opaque(operation_id, _OPERATION_RE, "operation id")
        if not isinstance(unit, str) or _UNIT_RE.fullmatch(unit) is None:
            raise CreditAccountingError("unit is invalid")
        normalized = _normalize_source_balances(sources)
        if not self.policy.enforcement_enabled:
            return CreditAccountingReceipt(
                "disabled", None, 0, 0, None, None, None, None, _iso(as_of),
            )
        payload = {
            "kind": "reconcile", "account_key": account_key,
            "unit": unit, "sources": normalized, "as_of": _iso(as_of),
        }
        fingerprint = _fingerprint(payload)
        with self._thread_lock:
            lease, state = self._locked_state()
            write = False
            try:
                replay = self._replay_or_conflict(state, operation_id, fingerprint)
                if replay is not None:
                    return replay
                high_water = self._advance_clock(state, as_of)
                accounts = state["accounts"]
                account = accounts.get(account_key)
                if account is None:
                    if len(accounts) >= MAX_ACCOUNTS:
                        raise CreditAccountingError("account bound reached")
                    account = self._new_account(unit)
                    accounts[account_key] = account
                elif account["unit"] != unit:
                    raise CreditAccountingConflict("account unit cannot change")
                self._apply_source_reconciliation(account, normalized, high_water)
                affected = sum(
                    max(0, source["effective_units"] - source["consumed_units"])
                    for source in account["sources"].values()
                )
                receipt = CreditAccountingReceipt(
                    "reconciled", None, 0, affected, None, None, None, None,
                    high_water,
                )
                self._record_operation(state, operation_id, fingerprint, receipt)
                write = True
                return receipt
            finally:
                self._close_locked_state(lease, state, write=write)

    def reserve(
        self,
        *,
        account_key: str,
        reservation_id: str,
        operation_id: str,
        requested_units: int,
        as_of: datetime | str,
    ) -> CreditAccountingReceipt:
        """Atomically reserve available lots in earliest-expiry order."""

        account_key = _opaque(account_key, _KEY_RE, "account key")
        reservation_id = _opaque(
            reservation_id, _RESERVATION_RE, "reservation id",
        )
        operation_id = _opaque(operation_id, _OPERATION_RE, "operation id")
        requested = _units(requested_units, "requested units", positive=True)
        normalized_as_of = _iso(as_of)
        if not self.policy.enforcement_enabled:
            return CreditAccountingReceipt(
                "disabled", None, requested, 0, None, None, None, None,
                normalized_as_of,
            )
        payload = {
            "kind": "reserve", "account_key": account_key,
            "reservation_id": reservation_id,
            "requested_units": requested, "as_of": normalized_as_of,
        }
        fingerprint = _fingerprint(payload)
        with self._thread_lock:
            lease, state = self._locked_state()
            write = False
            try:
                replay = self._replay_or_conflict(state, operation_id, fingerprint)
                if replay is not None:
                    return replay
                high_water = self._advance_clock(state, as_of)
                account = state["accounts"].get(account_key)
                if account is None:
                    raise CreditAccountingError("account has not been reconciled")
                if reservation_id in account["reservations"]:
                    raise CreditAccountingConflict("reservation id already exists")
                pending = self._pending_by_source(account)
                sources = sorted(
                    account["sources"].items(),
                    key=lambda item: (
                        item[1]["expires_at"] is None,
                        (
                            datetime.max.replace(tzinfo=timezone.utc)
                            if item[1]["expires_at"] is None
                            else _utc(item[1]["expires_at"], "source expiry")
                        ),
                        item[0],
                    ),
                )
                remaining = requested
                allocations: list[dict[str, Any]] = []
                for source_key, source in sources:
                    available = max(
                        0,
                        source["effective_units"]
                        - source["consumed_units"]
                        - pending[source_key],
                    )
                    allocated = min(available, remaining)
                    if allocated:
                        allocations.append({
                            "source_key": source_key, "units": allocated,
                        })
                        remaining -= allocated
                    if not remaining:
                        break
                if len(account["reservations"]) >= MAX_RESERVATIONS_PER_ACCOUNT:
                    raise CreditAccountingError("reservation bound reached")
                sequence = state["next_sequence"]
                state["next_sequence"] = sequence + 1
                status = "pending" if allocations else "invalidated"
                account["reservations"][reservation_id] = {
                    "status": status,
                    "created_sequence": sequence,
                    "requested_units": requested,
                    "revision": 1,
                    "allocations": allocations,
                }
                reserved = requested - remaining
                receipt = CreditAccountingReceipt(
                    "reserved", status, requested, reserved, 1,
                    reserved == requested, False, status == "invalidated",
                    high_water,
                )
                self._record_operation(state, operation_id, fingerprint, receipt)
                write = True
                return receipt
            finally:
                self._close_locked_state(lease, state, write=write)

    def revalidate_reservation(
        self,
        *,
        account_key: str,
        reservation_id: str,
        operation_id: str,
        unit: str,
        sources: Sequence[CreditSourceBalance],
        as_of: datetime | str,
    ) -> CreditAccountingReceipt:
        """Atomically reconcile source truth and report one reservation state."""

        account_key = _opaque(account_key, _KEY_RE, "account key")
        reservation_id = _opaque(
            reservation_id, _RESERVATION_RE, "reservation id",
        )
        operation_id = _opaque(operation_id, _OPERATION_RE, "operation id")
        if not isinstance(unit, str) or _UNIT_RE.fullmatch(unit) is None:
            raise CreditAccountingError("unit is invalid")
        normalized = _normalize_source_balances(sources)
        normalized_as_of = _iso(as_of)
        if not self.policy.enforcement_enabled:
            return CreditAccountingReceipt(
                "disabled", None, 0, 0, None, None, None, None,
                normalized_as_of,
            )
        payload = {
            "kind": "revalidate", "account_key": account_key,
            "reservation_id": reservation_id, "unit": unit,
            "sources": normalized, "as_of": normalized_as_of,
        }
        fingerprint = _fingerprint(payload)
        with self._thread_lock:
            lease, state = self._locked_state()
            write = False
            try:
                replay = self._replay_or_conflict(state, operation_id, fingerprint)
                if replay is not None:
                    return replay
                high_water = self._advance_clock(state, as_of)
                account = state["accounts"].get(account_key)
                if account is None:
                    raise CreditAccountingError("account has not been reconciled")
                if account["unit"] != unit:
                    raise CreditAccountingConflict("account unit cannot change")
                reservation = account["reservations"].get(reservation_id)
                if reservation is None:
                    raise CreditAccountingError("reservation does not exist")
                self._apply_source_reconciliation(account, normalized, high_water)
                receipt = self._reservation_receipt(
                    "revalidated", reservation, high_water,
                )
                self._record_operation(state, operation_id, fingerprint, receipt)
                write = True
                return receipt
            finally:
                self._close_locked_state(lease, state, write=write)

    def _transition(
        self,
        *,
        kind: str,
        account_key: str,
        reservation_id: str,
        operation_id: str,
        as_of: datetime | str,
        expected_revision: int | None = None,
    ) -> CreditAccountingReceipt:
        account_key = _opaque(account_key, _KEY_RE, "account key")
        reservation_id = _opaque(
            reservation_id, _RESERVATION_RE, "reservation id",
        )
        operation_id = _opaque(operation_id, _OPERATION_RE, "operation id")
        if kind in {"consume", "release"}:
            expected_revision = _units(
                expected_revision, "expected reservation revision", positive=True,
            )
        else:
            raise CreditAccountingError("transition is invalid")
        normalized_as_of = _iso(as_of)
        if not self.policy.enforcement_enabled:
            return CreditAccountingReceipt(
                "disabled", None, 0, 0, None, None, None, None,
                normalized_as_of,
            )
        payload = {
            "kind": kind, "account_key": account_key,
            "reservation_id": reservation_id, "as_of": normalized_as_of,
            "expected_revision": expected_revision,
        }
        fingerprint = _fingerprint(payload)
        with self._thread_lock:
            lease, state = self._locked_state()
            write = False
            try:
                replay = self._replay_or_conflict(state, operation_id, fingerprint)
                if replay is not None:
                    return replay
                high_water = self._advance_clock(state, as_of)
                # Clock/expiry observations are authoritative even when the
                # requested transition is subsequently rejected.
                write = True
                account = state["accounts"].get(account_key)
                reservation = (
                    None if account is None
                    else account["reservations"].get(reservation_id)
                )
                if reservation is None:
                    raise CreditAccountingError("reservation does not exist")
                current_revision = reservation["revision"]
                if kind == "release":
                    if expected_revision > current_revision:
                        raise CreditAccountingConflict(
                            "reservation revision is ahead of durable state",
                        )
                    if reservation["status"] in {"consumed", "settled"}:
                        raise CreditAccountingConflict(
                            "consumed or settled reservation cannot be released",
                        )
                    if reservation["status"] in {"released", "invalidated"}:
                        self._require_operation_capacity(state)
                        receipt = self._reservation_receipt(
                            "terminal_satisfied", reservation, high_water,
                        )
                        self._record_operation(
                            state, operation_id, fingerprint, receipt,
                        )
                        return receipt
                elif reservation["status"] != "pending":
                    raise CreditAccountingConflict(
                        "consume requires a pending reservation",
                    )
                affected = sum(
                    allocation["units"]
                    for allocation in reservation["allocations"]
                )
                if affected <= 0:
                    raise CreditAccountingConflict(
                        f"{kind} requires a funded pending reservation",
                    )
                self._require_operation_capacity(state)
                if kind == "consume":
                    if reservation["revision"] != expected_revision:
                        raise CreditAccountingConflict(
                            "reservation revision is stale",
                        )
                    if affected != reservation["requested_units"]:
                        raise CreditAccountingConflict(
                            "reservation is not fully funded",
                        )
                    for allocation in reservation["allocations"]:
                        account["sources"][allocation["source_key"]][
                            "consumed_units"
                        ] += allocation["units"]
                    reservation["status"] = "consumed"
                    reservation["revision"] += 1
                elif kind == "release":
                    reservation["status"] = "released"
                    reservation["revision"] += 1
                receipt = self._reservation_receipt(
                    kind + "d", reservation, high_water,
                )
                self._record_operation(state, operation_id, fingerprint, receipt)
                write = True
                return receipt
            finally:
                self._close_locked_state(lease, state, write=write)

    def consume(
        self,
        *,
        account_key: str,
        reservation_id: str,
        operation_id: str,
        as_of: datetime | str,
        expected_revision: int,
    ) -> CreditAccountingReceipt:
        """Consume a fully funded pending reservation at an exact revision."""

        return self._transition(
            kind="consume",
            account_key=account_key,
            reservation_id=reservation_id,
            operation_id=operation_id,
            as_of=as_of,
            expected_revision=expected_revision,
        )

    def release(
        self,
        *,
        account_key: str,
        reservation_id: str,
        operation_id: str,
        as_of: datetime | str,
        expected_revision: int,
    ) -> CreditAccountingReceipt:
        """Release a pending reservation back to its exact lots."""

        return self._transition(
            kind="release",
            account_key=account_key,
            reservation_id=reservation_id,
            operation_id=operation_id,
            as_of=as_of,
            expected_revision=expected_revision,
        )

    def settle(
        self,
        *,
        account_key: str,
        reservation_id: str,
        operation_id: str,
        terminal_status: str,
        server_billable_units: int | None,
        as_of: datetime | str,
        expected_revision: int,
    ) -> CreditAccountingReceipt:
        """Atomically finalize a consumed reservation and refund its remainder.

        The first accepted terminal observation is immutable. A retry of its
        stable operation ID returns that receipt even if a later wall-clock
        sample changed after callback success but before job persistence.
        """

        account_key = _opaque(account_key, _KEY_RE, "account key")
        reservation_id = _opaque(
            reservation_id, _RESERVATION_RE, "reservation id",
        )
        operation_id = _opaque(operation_id, _OPERATION_RE, "operation id")
        expected_revision = _units(
            expected_revision, "expected reservation revision", positive=True,
        )
        normalized_as_of = _iso(as_of)
        if not self.policy.enforcement_enabled:
            return CreditAccountingReceipt(
                "disabled", None, 0, 0, None, None, None, None,
                normalized_as_of,
            )
        # Measurement and observation time are deliberately absent from the
        # replay fingerprint so one terminal outcome can survive a changed
        # wall-clock sample. Terminal status remains immutable.
        payload = {
            "kind": "settle",
            "account_key": account_key,
            "reservation_id": reservation_id,
            "expected_revision": expected_revision,
            "terminal_status": terminal_status,
        }
        fingerprint = _fingerprint(payload)
        with self._thread_lock:
            lease, state = self._locked_state()
            write = False
            try:
                replay = self._replay_or_conflict(
                    state, operation_id, fingerprint,
                )
                if replay is not None:
                    return replay
                high_water = self._advance_clock(state, as_of)
                write = True
                account = state["accounts"].get(account_key)
                reservation = (
                    None if account is None
                    else account["reservations"].get(reservation_id)
                )
                if reservation is None:
                    raise CreditAccountingError("reservation does not exist")
                if reservation["status"] != "consumed":
                    raise CreditAccountingConflict(
                        "settlement requires a consumed reservation",
                    )
                if reservation["revision"] != expected_revision:
                    raise CreditAccountingConflict(
                        "reservation revision is stale",
                    )
                reserved = sum(
                    allocation["units"]
                    for allocation in reservation["allocations"]
                )
                settlement = terminal_credit_settlement(
                    terminal_status=terminal_status,
                    reserved_units=reserved,
                    server_billable_units=server_billable_units,
                )
                self._require_operation_capacity(state)
                remaining = settlement.settled_units
                kept: list[dict[str, Any]] = []
                for allocation in reservation["allocations"]:
                    retained = min(allocation["units"], remaining)
                    refunded = allocation["units"] - retained
                    source = account["sources"][allocation["source_key"]]
                    source["consumed_units"] -= refunded
                    if retained:
                        kept.append({
                            "source_key": allocation["source_key"],
                            "units": retained,
                        })
                        remaining -= retained
                reservation["allocations"] = kept
                reservation["status"] = "settled"
                reservation["revision"] += 1
                receipt = self._reservation_receipt(
                    "settled", reservation, high_water,
                )
                self._record_operation(
                    state, operation_id, fingerprint, receipt,
                )
                return receipt
            finally:
                self._close_locked_state(lease, state, write=write)

    def public_projection(self, account_key: str) -> dict[str, Any]:
        """Return aggregate content-free accounting state without opaque IDs."""

        account_key = _opaque(account_key, _KEY_RE, "account key")
        if not self.policy.enforcement_enabled:
            return {
                "schema_version": SCHEMA_VERSION,
                "state": "disabled",
                "enforcement_enabled": False,
                "unit": None,
                "clock_high_water": None,
                "available_units": 0,
                "reserved_units": 0,
                "consumed_units": 0,
                "pending_reservations": 0,
            }
        with self._thread_lock:
            lease, state = self._locked_state()
            try:
                account = state["accounts"].get(account_key)
                if account is None:
                    return {
                        "schema_version": SCHEMA_VERSION,
                        "state": "uninitialized",
                        "enforcement_enabled": self.policy.enforcement_enabled,
                        "unit": None,
                        "clock_high_water": state["clock_high_water"],
                        "available_units": 0,
                        "reserved_units": 0,
                        "consumed_units": 0,
                        "pending_reservations": 0,
                    }
                pending = self._pending_by_source(account)
                available = sum(
                    max(
                        0,
                        source["effective_units"]
                        - source["consumed_units"]
                        - pending[source_key],
                    )
                    for source_key, source in account["sources"].items()
                )
                return {
                    "schema_version": SCHEMA_VERSION,
                    "state": "durable_accounting",
                    "enforcement_enabled": self.policy.enforcement_enabled,
                    "unit": account["unit"],
                    "clock_high_water": state["clock_high_water"],
                    "available_units": available,
                    "reserved_units": sum(pending.values()),
                    "consumed_units": sum(
                        source["consumed_units"]
                        for source in account["sources"].values()
                    ),
                    "pending_reservations": sum(
                        reservation["status"] == "pending"
                        for reservation in account["reservations"].values()
                    ),
                }
            finally:
                self._close_locked_state(lease, state, write=False)
