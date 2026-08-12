"""Privacy-bounded server facade for Maestro's Support surface.

This module composes the frozen account, provider catalog, contribution, and
responsible-use cores.  It performs no network access and activates no payment
provider.  Every account operation resolves a live ``AccountAuthStore``
session; no public method accepts a client-supplied contribution subject key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .account_auth import (
    AccountAuthError,
    AccountAuthStore,
    resolve_account_capabilities,
)
from .entitlements import (
    FULFILLMENT_STATES,
    MANUAL_CONTRIBUTION_KINDS,
    MANUAL_CONTRIBUTION_SOURCES,
    SUPPORT_PRIORITY_IDENTITY_CONTRACTS,
    ContributionLedger,
    exclusive_file_lease,
    opaque_key,
    support_priority_capability_marker,
)
from .responsible_use import (
    InvalidResponsibleUseAcceptanceError,
    accept_responsible_use,
    normalize_acceptance_record,
    responsible_use_notice,
    responsible_use_status,
)
from .support_catalog import SupportCatalog, load_support_catalog

SUPPORT_PORTAL_SCHEMA_VERSION = 1
RESPONSIBLE_USE_STORE_SCHEMA_VERSION = 1
MAX_RESPONSIBLE_USE_STORE_BYTES = 1024 * 1024
MAX_RESPONSIBLE_USE_ACCOUNTS = 512

_SUBJECT_NAMESPACE = "maestro_account_support"
_SUBJECT_RE = re.compile(r"key_[0-9a-f]{64}\Z")
_ACCOUNT_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_EVENT_ID_RE = re.compile(r"evt_[0-9a-f]{32}\Z")
_FULFILLMENT_ITEM_RE = re.compile(r"[a-z][a-z0-9_]{1,63}\Z")
_FULFILLMENT_REFERENCE_RE = re.compile(r"key_[0-9a-f]{64}\Z")
_STORE_SEAL_DOMAIN = b"maestro-support-responsible-use-store-v1\0"
_ADMIN_CAPABILITIES = frozenset({"accounts.admin", "services.admin"})
_STORE_KEYS = frozenset({"schema_version", "generation", "records", "seal"})


class SupportPortalError(ValueError):
    """A Support facade request or durable record is invalid."""


class SupportAuthorizationError(SupportPortalError):
    """The authenticated account lacks the required server authority."""


class ResponsibleUseStoreIntegrityError(SupportPortalError):
    """The keyed responsible-use store cannot be authenticated."""


def _secret_bytes(value: bytes | str, *, name: str) -> bytes:
    result = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(result, bytes) or len(result) < 32:
        raise SupportPortalError(f"{name} must be at least 32 bytes")
    return result


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError) as error:
        raise ResponsibleUseStoreIntegrityError(
            "Responsible-use store contains invalid JSON data"
        ) from error


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResponsibleUseStoreIntegrityError(
                "Responsible-use store contains duplicate fields"
            )
        result[key] = value
    return result


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as error:
        if os.name != "nt":
            raise SupportPortalError(
                "Responsible-use store directory cannot be synchronized"
            ) from error
        return
    try:
        os.fsync(descriptor)
    except OSError as error:
        if os.name != "nt":
            raise SupportPortalError(
                "Responsible-use store directory cannot be synchronized"
            ) from error
    finally:
        os.close(descriptor)


class ResponsibleUseAcceptanceStore:
    """Atomic, cross-process serialized, HMAC-sealed account acknowledgements."""

    CANONICAL_PATH = (
        Path(__file__).resolve().parents[1]
        / "storage"
        / "support"
        / "responsible_use_acceptances.json"
    )

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        integrity_key: bytes | str,
        allow_test_path: bool = False,
    ) -> None:
        candidate = Path(path or self.CANONICAL_PATH).absolute()
        if candidate != self.CANONICAL_PATH:
            try:
                candidate.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
            except ValueError as error:
                raise SupportPortalError(
                    "Custom responsible-use store paths must be temporary"
                ) from error
            if not allow_test_path:
                raise SupportPortalError(
                    "Custom responsible-use store paths require test approval"
                )
        self.path = candidate
        self.lock_path = candidate.with_suffix(candidate.suffix + ".lock")
        self._integrity_key = _secret_bytes(
            integrity_key, name="responsible-use integrity key"
        )
        self._thread_lock = threading.RLock()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            if os.name != "nt":
                raise
        with self._thread_lock, exclusive_file_lease(self.lock_path):
            yield

    def _seal(self, unsigned: Mapping[str, Any]) -> str:
        return hmac.new(
            self._integrity_key,
            _STORE_SEAL_DOMAIN + _canonical(unsigned),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "schema_version": RESPONSIBLE_USE_STORE_SCHEMA_VERSION,
            "generation": 0,
            "records": {},
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            size = self.path.stat().st_size
            if size <= 0 or size > MAX_RESPONSIBLE_USE_STORE_BYTES:
                raise ResponsibleUseStoreIntegrityError(
                    "Responsible-use store exceeds its storage bound"
                )
            payload = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=_object_without_duplicate_keys,
            )
        except ResponsibleUseStoreIntegrityError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ResponsibleUseStoreIntegrityError(
                "Responsible-use store is unreadable"
            ) from error
        if not isinstance(payload, dict) or set(payload) != _STORE_KEYS:
            raise ResponsibleUseStoreIntegrityError(
                "Responsible-use store shape is invalid"
            )
        seal = payload.get("seal")
        unsigned = {key: value for key, value in payload.items() if key != "seal"}
        if (
            payload.get("schema_version") != RESPONSIBLE_USE_STORE_SCHEMA_VERSION
            or isinstance(payload.get("generation"), bool)
            or not isinstance(payload.get("generation"), int)
            or not 0 <= payload["generation"] <= (1 << 63) - 1
            or not isinstance(payload.get("records"), dict)
            or len(payload["records"]) > MAX_RESPONSIBLE_USE_ACCOUNTS
            or not isinstance(seal, str)
            or re.fullmatch(r"[0-9a-f]{64}", seal) is None
            or not hmac.compare_digest(seal, self._seal(unsigned))
        ):
            raise ResponsibleUseStoreIntegrityError(
                "Responsible-use store integrity check failed"
            )
        records: dict[str, dict[str, Any]] = {}
        for subject_key, record in payload["records"].items():
            if (
                not isinstance(subject_key, str)
                or _SUBJECT_RE.fullmatch(subject_key) is None
            ):
                raise ResponsibleUseStoreIntegrityError(
                    "Responsible-use account binding is invalid"
                )
            try:
                records[subject_key] = normalize_acceptance_record(record)
            except InvalidResponsibleUseAcceptanceError as error:
                raise ResponsibleUseStoreIntegrityError(
                    "Responsible-use acceptance record is invalid"
                ) from error
        return {**unsigned, "records": records}

    def _write_unlocked(self, unsigned: Mapping[str, Any]) -> None:
        payload = {**unsigned, "seal": self._seal(unsigned)}
        encoded = _canonical(payload)
        if len(encoded) > MAX_RESPONSIBLE_USE_STORE_BYTES:
            raise SupportPortalError(
                "Responsible-use store would exceed its storage bound"
            )
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            restrict = getattr(os, "fchmod", None)
            if callable(restrict):
                restrict(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                if os.name != "nt":
                    raise
            _fsync_directory(self.path.parent)
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    @staticmethod
    def _validate_subject(subject_key: str) -> str:
        if not isinstance(subject_key, str) or _SUBJECT_RE.fullmatch(subject_key) is None:
            raise SupportPortalError("Responsible-use account binding is invalid")
        return subject_key

    def status(self, subject_key: str) -> dict[str, Any]:
        selected = self._validate_subject(subject_key)
        with self._locked():
            payload = self._read_unlocked()
        return responsible_use_status(payload["records"].get(selected))

    def accept(
        self,
        subject_key: str,
        *,
        document_version: Any,
        content_sha256: Any,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        selected = self._validate_subject(subject_key)
        with self._locked():
            payload = self._read_unlocked()
            existing = payload["records"].get(selected)
            record = accept_responsible_use(
                existing,
                document_version,
                content_sha256,
                now=now,
            )
            if existing != record:
                if (
                    selected not in payload["records"]
                    and len(payload["records"]) >= MAX_RESPONSIBLE_USE_ACCOUNTS
                ):
                    raise SupportPortalError(
                        "Responsible-use account storage is full"
                    )
                records = dict(payload["records"])
                records[selected] = record
                self._write_unlocked({
                    "schema_version": RESPONSIBLE_USE_STORE_SCHEMA_VERSION,
                    "generation": payload["generation"] + 1,
                    "records": records,
                })
        return responsible_use_status(record)


class SupportPortal:
    """Pure server-side Support catalog, account, and admin facade."""

    def __init__(
        self,
        *,
        account_store: AccountAuthStore,
        ledger: ContributionLedger,
        acceptance_store: ResponsibleUseAcceptanceStore,
        identity_key: bytes | str,
        catalog: SupportCatalog | None = None,
        catalog_loader: Callable[[], SupportCatalog] | None = None,
    ) -> None:
        if not isinstance(account_store, AccountAuthStore):
            raise SupportPortalError("A server account store is required")
        if catalog is not None and catalog_loader is not None:
            raise SupportPortalError(
                "Provide a Support catalog or catalog loader, not both"
            )
        self._account_store = account_store
        self._ledger = ledger
        self._acceptance_store = acceptance_store
        self._identity_key = _secret_bytes(
            identity_key, name="support account identity key"
        )
        self._catalog = catalog
        self._catalog_loader = catalog_loader
        if self._catalog is None and self._catalog_loader is None:
            self._catalog = load_support_catalog()

    def _catalog_snapshot(self) -> SupportCatalog:
        catalog = (
            self._catalog_loader()
            if self._catalog_loader is not None
            else self._catalog
        )
        if not isinstance(catalog, SupportCatalog):
            raise SupportPortalError("Support catalog is unavailable")
        return catalog

    def _subject_key(self, account_id: str) -> str:
        if not isinstance(account_id, str) or _ACCOUNT_ID_RE.fullmatch(account_id) is None:
            raise SupportAuthorizationError("The authenticated account is unavailable")
        return opaque_key(_SUBJECT_NAMESPACE, account_id, self._identity_key)

    def _resolve_access(
        self,
        account_session_id: str,
        *,
        remote: bool,
    ) -> tuple[dict[str, Any], frozenset[str]]:
        if (
            not isinstance(account_session_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", account_session_id) is None
            or type(remote) is not bool
        ):
            raise SupportAuthorizationError("An authenticated account is required")
        principal = self._account_store.resolve_session(account_session_id)
        if principal is None:
            raise SupportAuthorizationError("An authenticated account is required")
        capabilities = resolve_account_capabilities(principal, remote=remote)
        if "account.self" not in capabilities:
            raise SupportAuthorizationError("Account self access is required")
        return principal, capabilities

    @staticmethod
    def _priority_policy() -> dict[str, Any]:
        exclusions = [
            support_priority_capability_marker(capability_id)
            for capability_id in sorted(SUPPORT_PRIORITY_IDENTITY_CONTRACTS)
        ]
        return {
            "scheduler_enforcement_enabled": False,
            "effective_priority_boost": False,
            "state": "not_enabled",
            "exclusions": exclusions,
            "notice": (
                "Some exact models, including Moody, are excluded from any "
                "future support-derived queue priority by their terms or "
                "creator policy. Submission remains available."
            ),
        }

    def public_catalog_projection(self) -> dict[str, Any]:
        catalog = self._catalog_snapshot()
        providers = []
        for status in catalog.providers:
            item = status.public_projection()
            support_url = item["support_url"]
            providers.append({
                "provider_id": item["provider_id"],
                "display_name": item["display_name"],
                "funding_modes": list(item["funding_modes"]),
                "description": item["description"],
                "enabled": item["enabled"],
                "configured": item["configured"],
                "state": item["state"],
                # The catalog already validates HTTPS host/port/userinfo.  A
                # disabled or incomplete provider never gets an actionable URL.
                "support_url": (
                    support_url
                    if item["state"] == "available"
                    else None
                ),
            })
        return {
            "schema_version": SUPPORT_PORTAL_SCHEMA_VERSION,
            "provider_catalog": {
                "schema_version": catalog.schema_version,
                "provider_neutral": True,
                "providers": providers,
            },
            "benefit_availability": {
                "scheduler_enforcement_enabled": False,
                "effective_benefits": [],
                "state": "recorded_not_enforced",
            },
            "support_priority": self._priority_policy(),
        }

    @staticmethod
    def _benefit_projection(recorded: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "state": "recorded_not_enforced",
            "scheduler_enforcement_enabled": False,
            "effective_benefits": [],
            "recorded_eligibility": list(recorded["benefit_eligibility"]),
        }

    def self_projection(
        self,
        account_session_id: str,
        *,
        remote: bool,
    ) -> dict[str, Any]:
        principal, _ = self._resolve_access(account_session_id, remote=remote)
        subject_key = self._subject_key(principal["id"])
        recorded = self._ledger.privacy_safe_user_projection(subject_key)
        return {
            **self.public_catalog_projection(),
            "account_support": {
                "recorded": {
                    key: value
                    for key, value in recorded.items()
                    if key != "benefit_eligibility"
                },
                "benefits": self._benefit_projection(recorded),
            },
            "responsible_use": {
                "notice": responsible_use_notice(),
                "status": self._acceptance_store.status(subject_key),
            },
        }

    def accept_responsible_use(
        self,
        account_session_id: str,
        *,
        remote: bool,
        document_version: Any,
        content_sha256: Any,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        principal, _ = self._resolve_access(account_session_id, remote=remote)
        return self._acceptance_store.accept(
            self._subject_key(principal["id"]),
            document_version=document_version,
            content_sha256=content_sha256,
            now=now,
        )

    def owner_admin_projection(
        self,
        actor_session_id: str,
        *,
        remote: bool,
        target_account_id: str,
    ) -> dict[str, Any]:
        """Return one account's opaque admin view after owner reauthentication.

        The target identifier is resolved against ``AccountAuthStore`` only
        after the actor's live owner capabilities and reauthentication are
        checked.  The facade accepts no raw ledger subject, email, username,
        or provider identity from the request.
        """

        _, subject_key = self._resolve_owner_target(
            actor_session_id,
            remote=remote,
            target_account_id=target_account_id,
        )
        return self._admin_projection_for_subject(subject_key)

    def _resolve_owner_target(
        self,
        actor_session_id: str,
        *,
        remote: bool,
        target_account_id: str,
    ) -> tuple[dict[str, Any], str]:
        actor, capabilities = self._resolve_access(
            actor_session_id, remote=remote,
        )
        if (
            actor.get("role") != "owner"
            or not _ADMIN_CAPABILITIES.issubset(capabilities)
        ):
            raise SupportAuthorizationError("Owner Support access is required")
        if actor.get("recently_reauthenticated") is not True:
            raise SupportAuthorizationError(
                "Recent owner authentication is required"
            )
        if (
            not isinstance(target_account_id, str)
            or _ACCOUNT_ID_RE.fullmatch(target_account_id) is None
        ):
            raise SupportAuthorizationError("The target account is unavailable")
        try:
            accounts = self._account_store.list_accounts(actor_session_id)
        except AccountAuthError as error:
            raise SupportAuthorizationError(
                "Recent owner authentication is required"
            ) from error
        if not any(account.get("id") == target_account_id for account in accounts):
            raise SupportAuthorizationError("The target account is unavailable")
        return actor, self._subject_key(target_account_id)

    def _admin_projection_for_subject(self, subject_key: str) -> dict[str, Any]:
        recorded = self._ledger.reauthenticated_admin_projection(subject_key)
        return {
            "schema_version": SUPPORT_PORTAL_SCHEMA_VERSION,
            "account_support": {
                "recorded": {
                    key: value
                    for key, value in recorded.items()
                    if key != "benefit_eligibility"
                },
                "benefits": self._benefit_projection(recorded),
            },
            "responsible_use": self._acceptance_store.status(subject_key),
            "support_priority": self._priority_policy(),
        }

    def transition_owner_fulfillment(
        self,
        actor_session_id: str,
        *,
        remote: bool,
        target_account_id: str,
        target_event_id: Any,
        item: Any,
        status: Any,
        idempotency_key: Any,
        proof_reference: Any,
    ) -> dict[str, Any]:
        """Append one server-derived fulfillment transition and refresh audit."""

        actor, subject_key = self._resolve_owner_target(
            actor_session_id,
            remote=remote,
            target_account_id=target_account_id,
        )
        if (
            not isinstance(target_event_id, str)
            or _EVENT_ID_RE.fullmatch(target_event_id) is None
            or not isinstance(item, str)
            or _FULFILLMENT_ITEM_RE.fullmatch(item) is None
            or not isinstance(status, str)
            or status not in FULFILLMENT_STATES
            or not isinstance(idempotency_key, str)
            or _FULFILLMENT_REFERENCE_RE.fullmatch(idempotency_key) is None
            or (
                proof_reference is not None
                and (
                    not isinstance(proof_reference, str)
                    or _FULFILLMENT_REFERENCE_RE.fullmatch(proof_reference) is None
                )
            )
        ):
            raise SupportPortalError("Fulfillment transition is invalid")
        self._ledger.transition_fulfillment(
            subject_key=subject_key,
            target_event_id=target_event_id,
            item=item,
            status=status,
            source_event_key=opaque_key(
                "fulfillment_idempotency", idempotency_key, self._identity_key,
            ),
            actor_key=opaque_key(
                "fulfillment_actor", actor["id"], self._identity_key,
            ),
            contract_key=(
                None
                if proof_reference is None
                else proof_reference
            ),
            occurred_at=datetime.now(timezone.utc),
        )
        return self._admin_projection_for_subject(subject_key)

    def record_owner_contribution(
        self,
        actor_session_id: str,
        *,
        remote: bool,
        target_account_id: str,
        source: Any,
        kind: Any,
        amount_minor: Any,
        currency: Any,
        target_event_id: Any,
        idempotency_key: Any,
    ) -> dict[str, Any]:
        """Append one owner-recorded contribution and refresh its audit view."""

        actor, subject_key = self._resolve_owner_target(
            actor_session_id,
            remote=remote,
            target_account_id=target_account_id,
        )
        if (
            not isinstance(source, str)
            or source not in MANUAL_CONTRIBUTION_SOURCES
            or not isinstance(kind, str)
            or kind not in MANUAL_CONTRIBUTION_KINDS
            or not isinstance(amount_minor, int)
            or isinstance(amount_minor, bool)
            or amount_minor < 0
            or amount_minor > 10_000_000_000
            or not isinstance(currency, str)
            or re.fullmatch(r"[A-Z]{3}", currency) is None
            or (
                target_event_id is not None
                and (
                    not isinstance(target_event_id, str)
                    or _EVENT_ID_RE.fullmatch(target_event_id) is None
                )
            )
            or not isinstance(idempotency_key, str)
            or _FULFILLMENT_REFERENCE_RE.fullmatch(idempotency_key) is None
            or (
                kind in {"one_time_contribution", "recurring_started"}
                and (amount_minor <= 0 or target_event_id is not None)
            )
            or (
                kind == "recurring_renewed"
                and (amount_minor <= 0 or target_event_id is None)
            )
            or (
                kind == "recurring_canceled"
                and (amount_minor != 0 or target_event_id is None)
            )
            or (
                kind in {"refund", "chargeback"}
                and (amount_minor <= 0 or target_event_id is None)
            )
        ):
            raise SupportPortalError("Manual contribution is invalid")
        self._ledger.record_manual_contribution(
            subject_key=subject_key,
            source=source,
            kind=kind,
            amount_minor=amount_minor,
            currency=currency,
            target_event_id=target_event_id,
            source_event_key=opaque_key(
                "manual_contribution_idempotency",
                idempotency_key,
                self._identity_key,
            ),
            actor_key=opaque_key(
                "manual_contribution_actor", actor["id"], self._identity_key,
            ),
            occurred_at=datetime.now(timezone.utc),
        )
        return self._admin_projection_for_subject(subject_key)


__all__ = [
    "MAX_RESPONSIBLE_USE_ACCOUNTS",
    "MAX_RESPONSIBLE_USE_STORE_BYTES",
    "RESPONSIBLE_USE_STORE_SCHEMA_VERSION",
    "SUPPORT_PORTAL_SCHEMA_VERSION",
    "ResponsibleUseAcceptanceStore",
    "ResponsibleUseStoreIntegrityError",
    "SupportAuthorizationError",
    "SupportPortal",
    "SupportPortalError",
]
