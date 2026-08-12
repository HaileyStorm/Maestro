"""Strict webhook adapter boundary for provider-neutral support events.

Only the deterministic fake adapter is implemented here.  It exercises the
same raw-body HMAC, timestamp, replay, identity-redaction, and ledger path a
future provider adapter must satisfy without implying that Stripe, Patreon,
Buy Me a Coffee, or any other production integration is configured.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable, Protocol

from services.entitlements import (
    ACCOUNT_LINK_KINDS,
    ContributionEvent,
    ContributionEventDraft,
    ContributionLedger,
    EntitlementError,
    exclusive_file_lease,
    opaque_key,
)


MAX_WEBHOOK_BYTES = 256 * 1024
MAX_REPLAY_STATE_BYTES = 16 * 1024 * 1024
MAX_REPLAY_ENTRIES = 100_000
DEFAULT_TIMESTAMP_TOLERANCE_SECONDS = 300
DEFAULT_REPLAY_RETENTION_SECONDS = 24 * 60 * 60
REPLAY_SCHEMA_VERSION = 1
_SIGNATURE_RE = re.compile(r"v1=([0-9a-f]{64})\Z")
_OPAQUE_KEY_RE = re.compile(r"key_[0-9a-f]{64}\Z")
_PAYLOAD_KEYS = frozenset({
    "event_id", "subject_id", "kind", "occurred_at", "amount_minor",
    "currency", "contract_id", "related_event_id", "fulfillment_item",
    "fulfillment_status", "actor_id", "account_id",
})


class SupportWebhookError(ValueError):
    pass


class WebhookSignatureError(SupportWebhookError):
    pass


class WebhookTimestampError(SupportWebhookError):
    pass


class WebhookReplayError(SupportWebhookError):
    pass


class WebhookPayloadError(SupportWebhookError):
    pass


class WebhookReplayIntegrityError(SupportWebhookError):
    pass


def _secret_bytes(value: bytes | str, *, name: str) -> bytes:
    result = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(result, bytes) or len(result) < 32:
        raise SupportWebhookError(f"{name} must be at least 32 bytes")
    return result


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and 1 <= len(value) <= 40:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise SupportWebhookError("timestamp is invalid") from error
    else:
        raise SupportWebhookError("timestamp is invalid")
    if parsed.tzinfo is None:
        raise SupportWebhookError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | str) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def _header(headers: Mapping[str, str], name: str) -> str:
    values = [
        value for key, value in headers.items() if key.lower() == name.lower()
    ]
    if len(values) != 1 or not isinstance(values[0], str):
        raise WebhookSignatureError(f"missing or ambiguous {name} header")
    return values[0]


def _json_without_duplicate_keys(raw_body: bytes) -> dict[str, Any]:
    if not isinstance(raw_body, bytes) or not raw_body or len(raw_body) > MAX_WEBHOOK_BYTES:
        raise WebhookPayloadError("webhook body size is invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise WebhookPayloadError("webhook JSON has duplicate fields")
            result[key] = value
        return result

    try:
        payload = json.loads(raw_body.decode("utf-8"), object_pairs_hook=pairs)
    except WebhookPayloadError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WebhookPayloadError("webhook body is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise WebhookPayloadError("webhook payload must be an object")
    return payload


class SupportWebhookAdapter(Protocol):
    provider_id: str
    production_ready: bool

    def verified_event_identity(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime | str,
    ) -> tuple[str, str]:
        ...

    def verify_and_translate(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime | str,
        recorded_event: ContributionEvent | None = None,
    ) -> ContributionEventDraft:
        ...


class WebhookReplayGuard(Protocol):
    def record(
        self,
        provider: str,
        source_event_key: str,
        *,
        now: datetime | str,
    ) -> None:
        ...


@dataclass(frozen=True, slots=True)
class FakeSignedWebhookAdapter:
    """Deterministic non-production adapter for integration and local tests."""

    signing_secret: bytes = field(repr=False)
    identity_secret: bytes = field(repr=False)
    account_link_resolver: Callable[[str, str, str], str | None] | None = field(
        default=None, repr=False,
    )
    provider_id: str = "fake_support"
    timestamp_tolerance_seconds: int = DEFAULT_TIMESTAMP_TOLERANCE_SECONDS
    production_ready: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "signing_secret",
            _secret_bytes(self.signing_secret, name="webhook signing secret"),
        )
        object.__setattr__(
            self, "identity_secret",
            _secret_bytes(self.identity_secret, name="webhook identity secret"),
        )
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,47}", self.provider_id):
            raise SupportWebhookError("webhook provider identifier is invalid")
        if not 30 <= self.timestamp_tolerance_seconds <= 3_600:
            raise SupportWebhookError("webhook timestamp tolerance is invalid")
        if self.account_link_resolver is not None and not callable(
            self.account_link_resolver
        ):
            raise SupportWebhookError("account link resolver is invalid")

    def signature(self, raw_body: bytes, timestamp: int) -> str:
        signed = str(timestamp).encode("ascii") + b"." + raw_body
        return "v1=" + hmac.new(
            self.signing_secret, signed, hashlib.sha256,
        ).hexdigest()

    def headers(self, raw_body: bytes, timestamp: int) -> dict[str, str]:
        return {
            "x-maestro-support-timestamp": str(timestamp),
            "x-maestro-support-signature": self.signature(raw_body, timestamp),
        }

    def _verified_payload(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime | str,
    ) -> dict[str, Any]:
        if (
            not isinstance(raw_body, bytes)
            or not raw_body
            or len(raw_body) > MAX_WEBHOOK_BYTES
        ):
            raise WebhookPayloadError("webhook body size is invalid")
        received = _utc(received_at)
        timestamp_raw = _header(headers, "x-maestro-support-timestamp")
        signature_raw = _header(headers, "x-maestro-support-signature")
        if not re.fullmatch(r"[0-9]{1,12}", timestamp_raw):
            raise WebhookTimestampError("webhook timestamp header is invalid")
        timestamp = int(timestamp_raw)
        try:
            signed_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as error:
            raise WebhookTimestampError("webhook timestamp is out of range") from error
        if abs((received - signed_at).total_seconds()) > self.timestamp_tolerance_seconds:
            raise WebhookTimestampError("webhook timestamp is outside the accepted window")
        match = _SIGNATURE_RE.fullmatch(signature_raw)
        expected = self.signature(raw_body, timestamp)
        if match is None or not hmac.compare_digest(signature_raw, expected):
            raise WebhookSignatureError("webhook signature is invalid")
        payload = _json_without_duplicate_keys(raw_body)
        if not set(payload).issubset(_PAYLOAD_KEYS) or not {
            "event_id", "subject_id", "kind", "occurred_at",
        }.issubset(payload):
            raise WebhookPayloadError("webhook payload shape is invalid")
        for key in (
            "event_id", "subject_id", "kind", "occurred_at", "contract_id",
            "related_event_id", "fulfillment_item", "fulfillment_status",
            "actor_id", "currency", "account_id",
        ):
            if key in payload and (
                not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 1_024
            ):
                raise WebhookPayloadError(f"webhook {key} is invalid")
        amount = payload.get("amount_minor", 0)
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise WebhookPayloadError("webhook amount_minor is invalid")
        is_account_link = payload["kind"] in ACCOUNT_LINK_KINDS
        if is_account_link != ("account_id" in payload):
            raise WebhookPayloadError(
                "webhook account_id is reserved for account link events"
            )
        return payload

    def verified_event_identity(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime | str,
    ) -> tuple[str, str]:
        payload = self._verified_payload(
            raw_body, headers, received_at=received_at,
        )
        return self.provider_id, opaque_key(
            f"{self.provider_id}_event",
            payload["event_id"],
            self.identity_secret,
        )

    def verify_and_translate(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime | str,
        recorded_event: ContributionEvent | None = None,
    ) -> ContributionEventDraft:
        payload = self._verified_payload(
            raw_body, headers, received_at=received_at,
        )
        amount = payload.get("amount_minor", 0)
        is_account_link = payload["kind"] in ACCOUNT_LINK_KINDS
        account_subject = None
        if is_account_link:
            if (
                recorded_event is not None
                and recorded_event.provider == self.provider_id
                and recorded_event.kind == payload["kind"]
            ):
                account_subject = recorded_event.subject_key
            elif self.account_link_resolver is None:
                raise WebhookPayloadError(
                    "webhook account link requires server verification"
                )
            else:
                try:
                    account_subject = self.account_link_resolver(
                        self.provider_id,
                        payload["subject_id"],
                        payload["account_id"],
                    )
                except Exception as error:
                    raise WebhookPayloadError(
                        "webhook account link verification failed"
                    ) from error
            if (
                not isinstance(account_subject, str)
                or _OPAQUE_KEY_RE.fullmatch(account_subject) is None
            ):
                raise WebhookPayloadError(
                    "webhook account link verification failed"
                )

        def optional_key(namespace: str, field: str) -> str | None:
            value = payload.get(field)
            return None if value is None else opaque_key(
                namespace, value, self.identity_secret,
            )

        try:
            provider_subject = opaque_key(
                f"{self.provider_id}_subject",
                payload["subject_id"],
                self.identity_secret,
            )
            return ContributionEventDraft(
                provider=self.provider_id,
                source_event_key=opaque_key(
                    f"{self.provider_id}_event",
                    payload["event_id"],
                    self.identity_secret,
                ),
                subject_key=(
                    account_subject
                    if is_account_link else provider_subject
                ),
                kind=payload["kind"],
                occurred_at=payload["occurred_at"],
                amount_minor=amount,
                currency=payload.get("currency", "USD"),
                contract_key=(
                    opaque_key(
                        "maestro_account_claim",
                        payload["account_id"],
                        self.identity_secret,
                    )
                    if is_account_link else optional_key(
                        f"{self.provider_id}_contract", "contract_id",
                    )
                ),
                related_event_key=(
                    provider_subject
                    if is_account_link else optional_key(
                        f"{self.provider_id}_event", "related_event_id",
                    )
                ),
                fulfillment_item=payload.get("fulfillment_item"),
                fulfillment_status=payload.get("fulfillment_status"),
                actor_key=optional_key("admin_actor", "actor_id"),
            )
        except EntitlementError as error:
            raise WebhookPayloadError("webhook identity fields are invalid") from error


@dataclass(frozen=True, slots=True)
class ManualContributionAdapter:
    """Translate reauthenticated owner input; route authentication is required."""

    identity_secret: bytes = field(repr=False)
    provider_id: str = "manual"
    production_ready: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "identity_secret",
            _secret_bytes(self.identity_secret, name="manual identity secret"),
        )

    def draft(
        self,
        *,
        event_id: str,
        subject_id: str,
        kind: str,
        occurred_at: datetime | str,
        amount_minor: int = 0,
        currency: str = "USD",
        contract_id: str | None = None,
        related_event_id: str | None = None,
        fulfillment_item: str | None = None,
        fulfillment_status: str | None = None,
        actor_id: str | None = None,
        linked_account_id: str | None = None,
    ) -> ContributionEventDraft:
        def keyed(namespace: str, value: str | None) -> str | None:
            return None if value is None else opaque_key(
                namespace, value, self.identity_secret,
            )

        source = keyed("manual_event", event_id)
        provider_subject = keyed("manual_subject", subject_id)
        is_account_link = kind in ACCOUNT_LINK_KINDS
        if is_account_link != (linked_account_id is not None):
            raise WebhookPayloadError(
                "manual linked account is reserved for account link events"
            )
        subject = (
            keyed("maestro_account_support", linked_account_id)
            if is_account_link else provider_subject
        )
        if (
            source is None
            or subject is None
            or provider_subject is None
            or _OPAQUE_KEY_RE.fullmatch(subject) is None
        ):
            raise WebhookPayloadError("manual contribution identifiers are required")
        return ContributionEventDraft(
            provider=self.provider_id,
            source_event_key=source,
            subject_key=subject,
            kind=kind,
            occurred_at=occurred_at,
            amount_minor=amount_minor,
            currency=currency,
            contract_key=(
                keyed("maestro_account_claim", linked_account_id)
                if is_account_link else keyed("manual_contract", contract_id)
            ),
            related_event_key=(
                provider_subject
                if is_account_link else keyed("manual_event", related_event_id)
            ),
            fulfillment_item=fulfillment_item,
            fulfillment_status=fulfillment_status,
            actor_key=keyed("admin_actor", actor_id),
        )


class FileWebhookReplayGuard:
    """Small integrity-sealed replay registry that survives app restarts."""

    CANONICAL_PATH = (
        Path(__file__).resolve().parents[1]
        / "storage" / "support" / "webhook_replay.json"
    )

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        integrity_key: bytes | str,
        retention_seconds: int = DEFAULT_REPLAY_RETENTION_SECONDS,
        allow_test_path: bool = False,
    ):
        candidate = Path(path or self.CANONICAL_PATH).absolute()
        if candidate != self.CANONICAL_PATH:
            try:
                candidate.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
            except ValueError as error:
                raise SupportWebhookError("custom replay paths must be temporary") from error
            if not allow_test_path:
                raise SupportWebhookError(
                    "custom replay paths require explicit test approval"
                )
        if not 300 <= retention_seconds <= 30 * 24 * 60 * 60:
            raise SupportWebhookError("webhook replay retention is invalid")
        self.path = candidate
        self.lock_path = candidate.with_suffix(candidate.suffix + ".lock")
        self._key = _secret_bytes(integrity_key, name="replay integrity key")
        self.retention_seconds = retention_seconds
        self._lock = threading.RLock()

    def _seal(self, entries: Mapping[str, str]) -> str:
        return hmac.new(self._key, _canonical(entries), hashlib.sha256).hexdigest()

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > MAX_REPLAY_STATE_BYTES:
                raise WebhookReplayIntegrityError(
                    "webhook replay state exceeds its bound"
                )
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except WebhookReplayIntegrityError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise WebhookReplayIntegrityError("webhook replay state is unreadable") from error
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "entries", "state_hmac",
        }:
            raise WebhookReplayIntegrityError("webhook replay state shape is invalid")
        entries = payload.get("entries")
        if (
            payload.get("schema_version") != REPLAY_SCHEMA_VERSION
            or not isinstance(entries, dict)
            or len(entries) > MAX_REPLAY_ENTRIES
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in entries.items())
            or not isinstance(payload.get("state_hmac"), str)
            or not hmac.compare_digest(payload["state_hmac"], self._seal(entries))
        ):
            raise WebhookReplayIntegrityError("webhook replay state integrity failed")
        try:
            for expires_at in entries.values():
                _utc(expires_at)
        except SupportWebhookError as error:
            raise WebhookReplayIntegrityError("webhook replay expiry is invalid") from error
        return dict(entries)

    def _write(self, entries: Mapping[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "entries": dict(entries),
            "state_hmac": self._seal(entries),
        }
        encoded = _canonical(payload)
        if len(encoded) > MAX_REPLAY_STATE_BYTES:
            raise SupportWebhookError(
                "webhook replay state would exceed its byte bound"
            )
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
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def record(
        self,
        provider: str,
        source_event_key: str,
        *,
        now: datetime | str,
    ) -> None:
        current = _utc(now)
        replay_key = hashlib.sha256(
            f"{provider}\0{source_event_key}".encode("ascii")
        ).hexdigest()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with exclusive_file_lease(self.lock_path):
                entries = self._read()
                entries = {
                    key: expires_at for key, expires_at in entries.items()
                    if _utc(expires_at) > current
                }
                if replay_key in entries:
                    raise WebhookReplayError(
                        "webhook event has already been processed"
                    )
                if len(entries) >= MAX_REPLAY_ENTRIES:
                    raise SupportWebhookError(
                        "webhook replay entry bound reached"
                    )
                entries[replay_key] = _iso(
                    current + timedelta(seconds=self.retention_seconds)
                )
                self._write(entries)


def process_signed_webhook(
    adapter: SupportWebhookAdapter,
    ledger: ContributionLedger,
    replay_guard: WebhookReplayGuard,
    raw_body: bytes,
    headers: Mapping[str, str],
    *,
    received_at: datetime | str,
) -> ContributionEvent:
    """Verify, normalize, idempotently append, then persist replay state.

    Appending before the replay record makes a crash safely retryable: the
    ledger returns the existing immutable event, then the replay seal is
    completed.  A completed replay is rejected without duplicating benefits.
    """

    provider, source_event_key = adapter.verified_event_identity(
        raw_body, headers, received_at=received_at,
    )
    recorded_event = ledger.event_for_source(provider, source_event_key)
    draft = adapter.verify_and_translate(
        raw_body,
        headers,
        received_at=received_at,
        recorded_event=recorded_event,
    )
    event = ledger.append(draft, received_at=received_at)
    replay_guard.record(
        draft.provider, draft.source_event_key, now=received_at,
    )
    return event
