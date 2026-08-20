"""Owner-only, test-only credit accounting built on the durable credit journal.

The ledger never affects admission, priority, or real support allowances. It
exists so an operator can exercise the same debit/refund semantics while owner
jobs remain exempt from hosted scheduling policy.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from services.credit_accounting import (
    CreditAccountingError,
    CreditAccountingJournal,
    CreditAccountingPolicy,
    CreditSourceBalance,
)


class OwnerTestCreditError(ValueError):
    """The isolated owner test ledger could not complete an operation."""


@dataclass(frozen=True, slots=True)
class OwnerTestCreditReservation:
    reservation_id: str
    reservation_revision: int
    requested_units: int

    def private_mapping(self) -> dict[str, int | str]:
        return {
            "reservation_id": self.reservation_id,
            "reservation_revision": self.reservation_revision,
            "requested_units": self.requested_units,
        }


def _opaque(prefix: str, *parts: object) -> str:
    payload = "\0".join((prefix, *(str(part) for part in parts)))
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _iso_timestamp(value: float | None = None) -> str:
    instant = datetime.now(timezone.utc) if value is None else datetime.fromtimestamp(
        float(value), timezone.utc,
    )
    return instant.isoformat().replace("+00:00", "Z")


class OwnerTestCreditLedger:
    """Auto-refilling test wallet with real reservation settlement semantics."""

    def __init__(
        self,
        path: str | Path,
        *,
        integrity_key: bytes | str,
        target_balance: int = 1_000,
    ) -> None:
        if (
            not isinstance(target_balance, int)
            or isinstance(target_balance, bool)
            or not 1 <= target_balance <= 1_000_000
        ):
            raise OwnerTestCreditError("test target balance is invalid")
        self.target_balance = target_balance
        try:
            self._journal = CreditAccountingJournal(
                path,
                integrity_key=integrity_key,
                policy=CreditAccountingPolicy(enforcement_enabled=True),
            )
        except CreditAccountingError as error:
            raise OwnerTestCreditError("test credit ledger is unavailable") from error

    def public_projection(self, account_key: str) -> dict[str, object]:
        try:
            state = self._journal.public_projection(account_key)
        except (CreditAccountingError, OSError) as error:
            raise OwnerTestCreditError("test credit ledger is unavailable") from error
        initialized = state.get("state") == "durable_accounting"
        return {
            "schema_version": 1,
            "state": "active",
            "test_only": True,
            "auto_top_up": True,
            "unit": "maestro_test_credits",
            "target_balance": self.target_balance,
            "available_units": (
                int(state.get("available_units") or 0)
                if initialized else self.target_balance
            ),
            "reserved_units": int(state.get("reserved_units") or 0),
            "used_units": int(state.get("consumed_units") or 0),
            "pending_reservations": int(
                state.get("pending_reservations") or 0
            ),
            "last_activity_at": state.get("clock_high_water"),
        }

    def record_dispatch(
        self,
        *,
        account_key: str,
        source_key: str,
        job_key: str,
        requested_units: int,
        as_of: str | None = None,
    ) -> OwnerTestCreditReservation:
        if (
            not isinstance(requested_units, int)
            or isinstance(requested_units, bool)
            or requested_units <= 0
        ):
            raise OwnerTestCreditError("test credit request is invalid")
        timestamp = as_of or _iso_timestamp()
        try:
            current = self._journal.public_projection(account_key)
            available = int(current.get("available_units") or 0)
            reserved = int(current.get("reserved_units") or 0)
            consumed = int(current.get("consumed_units") or 0)
            effective_units = available + reserved + consumed
            if current.get("state") != "durable_accounting" or available < requested_units:
                effective_units = consumed + reserved + max(
                    self.target_balance, requested_units,
                )
            reconcile_id = _opaque(
                "operation", "owner-test-reconcile", job_key, effective_units,
            )
            self._journal.reconcile(
                account_key=account_key,
                operation_id=reconcile_id,
                unit="maestro_test_credits",
                sources=(CreditSourceBalance(source_key, effective_units),),
                as_of=timestamp,
            )
            reservation_id = _opaque(
                "reservation", "owner-test", account_key, job_key,
            )
            reserved_receipt = self._journal.reserve(
                account_key=account_key,
                reservation_id=reservation_id,
                operation_id=_opaque(
                    "operation", "owner-test-reserve", account_key, job_key,
                ),
                requested_units=requested_units,
                as_of=timestamp,
            )
            if (
                reserved_receipt.reservation_status != "pending"
                or reserved_receipt.fully_funded is not True
                or type(reserved_receipt.reservation_revision) is not int
            ):
                raise OwnerTestCreditError("test credit reservation failed")
            consumed_receipt = self._journal.consume(
                account_key=account_key,
                reservation_id=reservation_id,
                operation_id=_opaque(
                    "operation", "owner-test-consume", account_key, job_key,
                ),
                expected_revision=reserved_receipt.reservation_revision,
                as_of=timestamp,
            )
            if (
                consumed_receipt.reservation_status != "consumed"
                or type(consumed_receipt.reservation_revision) is not int
            ):
                raise OwnerTestCreditError("test credit consumption failed")
            return OwnerTestCreditReservation(
                reservation_id=reservation_id,
                reservation_revision=consumed_receipt.reservation_revision,
                requested_units=requested_units,
            )
        except OwnerTestCreditError:
            raise
        except (CreditAccountingError, OSError) as error:
            raise OwnerTestCreditError("test credit dispatch failed") from error

    def settle(
        self,
        *,
        account_key: str,
        job_key: str,
        reservation_id: str,
        expected_revision: int,
        terminal_status: str,
        started_at: float | None,
        finished_at: float,
    ) -> dict[str, object]:
        billable_units = None
        if terminal_status == "cancelled":
            if started_at is None:
                raise OwnerTestCreditError(
                    "cancelled test credit settlement has no start time"
                )
            billable_units = math.ceil(max(0.0, finished_at - started_at))
        try:
            receipt = self._journal.settle(
                account_key=account_key,
                reservation_id=reservation_id,
                operation_id=_opaque(
                    "operation", "owner-test-settle", account_key, job_key,
                    terminal_status,
                ),
                terminal_status=terminal_status,
                server_billable_units=billable_units,
                expected_revision=expected_revision,
                as_of=_iso_timestamp(finished_at),
            )
        except (CreditAccountingError, OSError, ValueError) as error:
            raise OwnerTestCreditError("test credit settlement failed") from error
        if receipt.reservation_status != "settled":
            raise OwnerTestCreditError("test credit settlement was incomplete")
        return self.public_projection(account_key)


__all__ = [
    "OwnerTestCreditError",
    "OwnerTestCreditLedger",
    "OwnerTestCreditReservation",
]
