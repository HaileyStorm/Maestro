"""Strict webhook adapter boundary for provider-neutral support events.

The deterministic fake adapter exercises the complete ledger path.  The
optional Stripe verifier only proves raw webhook evidence; server-owned
payment-link/price mappings and account links must translate that evidence
before any supporter benefit is recorded.
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
import stat
import tempfile
import threading
from types import MappingProxyType
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
MAX_STRIPE_RUNTIME_CONFIG_BYTES = 256 * 1024
MAX_STRIPE_ASSOCIATION_STATE_BYTES = 4 * 1024 * 1024
MAX_STRIPE_ASSOCIATION_ENTRIES = 50_000
DEFAULT_TIMESTAMP_TOLERANCE_SECONDS = 300
DEFAULT_REPLAY_RETENTION_SECONDS = 24 * 60 * 60
REPLAY_SCHEMA_VERSION = 1
STRIPE_RUNTIME_CONFIG_SCHEMA_VERSION = 1
STRIPE_ASSOCIATION_SCHEMA_VERSION = 1
_SIGNATURE_RE = re.compile(r"v1=([0-9a-f]{64})\Z")
_OPAQUE_KEY_RE = re.compile(r"key_[0-9a-f]{64}\Z")
_STRIPE_ID_RE = re.compile(
    r"(?:evt|cs|cus|plink|price|pi|ch|sub|in|du)_[A-Za-z0-9_]{3,255}\Z"
)
_CURRENCY_RE = re.compile(r"[A-Z]{3}\Z")
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


class StripeAssociationIntegrityError(SupportWebhookError):
    pass


@dataclass(frozen=True, slots=True)
class SupportEvidenceContract:
    """Public-safe contract for a disabled provider evidence adapter."""

    provider_id: str
    enabled_by_default: bool
    positive_event_types: tuple[str, ...]
    reversal_event_types: tuple[str, ...]
    server_mapping_keys: tuple[str, ...]

    def public_projection(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "enabled": self.enabled_by_default,
            "verification": "signed_webhook_required",
            "positive_event_types": list(self.positive_event_types),
            "reversal_event_types": list(self.reversal_event_types),
            "server_mapping_keys": list(self.server_mapping_keys),
            "radar_role": "fraud_screening_only",
            "grants_app_or_account_authorization": False,
            "projects_personal_address_or_phone": False,
            "projects_api_keys_or_provider_subjects": False,
        }


STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT = SupportEvidenceContract(
    provider_id="buy_me_a_coffee_stripe",
    enabled_by_default=False,
    positive_event_types=("checkout.session.completed", "invoice.paid"),
    reversal_event_types=(
        "charge.refunded",
        "charge.dispute.created",
        "customer.subscription.deleted",
    ),
    server_mapping_keys=("payment_link", "price"),
)


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


def _private_runtime_file(
    path: str | os.PathLike[str],
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    candidate = Path(path).absolute()
    descriptor: int | None = None
    try:
        if candidate.resolve(strict=False) != candidate or candidate.is_symlink():
            raise SupportWebhookError(f"{label} path must not use symlinks")
        descriptor = os.open(
            candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        named = candidate.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise SupportWebhookError(f"{label} must be a regular file")
        if os.name != "nt" and stat.S_IMODE(opened.st_mode) != 0o600:
            raise SupportWebhookError(f"{label} permissions must be 0600")
        if not 0 < opened.st_size <= maximum_bytes:
            raise SupportWebhookError(f"{label} size is invalid")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(maximum_bytes + 1)
        if not raw or len(raw) > maximum_bytes:
            raise SupportWebhookError(f"{label} size is invalid")
        return raw
    except SupportWebhookError:
        raise
    except OSError as error:
        raise SupportWebhookError(f"{label} is unreadable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _runtime_secret(
    env: Mapping[str, str],
    name: str,
) -> bytes:
    direct = env.get(name)
    reference = env.get(f"{name}_FILE")
    if (direct is None) == (reference is None):
        raise SupportWebhookError(
            f"configure exactly one of {name} or {name}_FILE"
        )
    if reference is not None:
        raw = _private_runtime_file(
            reference, maximum_bytes=64 * 1024, label=f"{name} secret file",
        )
        try:
            direct = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as error:
            raise SupportWebhookError(f"{name} secret file is invalid") from error
    return _secret_bytes(direct or b"", name=name)


def _runtime_json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw or len(raw) > MAX_STRIPE_RUNTIME_CONFIG_BYTES:
        raise SupportWebhookError(f"{label} size is invalid")
    try:
        return _json_without_duplicate_keys(raw)
    except WebhookPayloadError as error:
        raise SupportWebhookError(f"{label} is invalid") from error


def _stripe_identifier(value: Any, *prefixes: str) -> str:
    if isinstance(value, dict):
        value = value.get("id")
    if (
        not isinstance(value, str)
        or _STRIPE_ID_RE.fullmatch(value) is None
        or not any(value.startswith(f"{prefix}_") for prefix in prefixes)
    ):
        raise WebhookPayloadError("Stripe support identifier is invalid")
    return value


@dataclass(frozen=True, slots=True)
class StripeBmacWebhookConfig:
    """Runtime-only production adapter configuration; all private fields hide."""

    livemode: bool
    payment_links: Mapping[str, str] = field(repr=False)
    prices: Mapping[str, str] = field(repr=False)
    account_links: Mapping[str, str] = field(repr=False)
    signing_secret: bytes = field(repr=False)
    identity_secret: bytes = field(repr=False)
    association_integrity_key: bytes = field(repr=False)
    timestamp_tolerance_seconds: int = DEFAULT_TIMESTAMP_TOLERANCE_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.livemode, bool):
            raise SupportWebhookError("Stripe livemode setting is invalid")
        object.__setattr__(
            self, "signing_secret",
            _secret_bytes(self.signing_secret, name="Stripe webhook secret"),
        )
        object.__setattr__(
            self, "identity_secret",
            _secret_bytes(self.identity_secret, name="Stripe identity key"),
        )
        object.__setattr__(
            self, "association_integrity_key",
            _secret_bytes(
                self.association_integrity_key,
                name="Stripe association integrity key",
            ),
        )
        if not 30 <= self.timestamp_tolerance_seconds <= 3_600:
            raise SupportWebhookError("Stripe timestamp tolerance is invalid")

        def selectors(
            values: Mapping[str, str], prefix: str, label: str,
        ) -> Mapping[str, str]:
            if not isinstance(values, Mapping) or not 1 <= len(values) <= 1_024:
                raise SupportWebhookError(f"{label} mappings are invalid")
            result: dict[str, str] = {}
            for key, currency in values.items():
                try:
                    selected = _stripe_identifier(key, prefix)
                except WebhookPayloadError as error:
                    raise SupportWebhookError(
                        f"{label} mapping identifier is invalid"
                    ) from error
                if not isinstance(currency, str) or _CURRENCY_RE.fullmatch(
                    currency,
                ) is None:
                    raise SupportWebhookError(f"{label} mapping currency is invalid")
                result[selected] = currency
            return MappingProxyType(result)

        object.__setattr__(
            self, "payment_links",
            selectors(self.payment_links, "plink", "Payment Link"),
        )
        object.__setattr__(
            self, "prices", selectors(self.prices, "price", "Price"),
        )
        if (
            not isinstance(self.account_links, Mapping)
            or not 1 <= len(self.account_links) <= 10_000
        ):
            raise SupportWebhookError("Stripe account links are invalid")
        account_links: dict[str, str] = {}
        for customer, subject_key in self.account_links.items():
            try:
                selected = _stripe_identifier(customer, "cus")
            except WebhookPayloadError as error:
                raise SupportWebhookError(
                    "Stripe account link identifier is invalid"
                ) from error
            if (
                not isinstance(subject_key, str)
                or _OPAQUE_KEY_RE.fullmatch(subject_key) is None
            ):
                raise SupportWebhookError(
                    "Stripe account links require existing opaque accounts"
                )
            account_links[selected] = subject_key
        object.__setattr__(self, "account_links", MappingProxyType(account_links))

    @classmethod
    def from_runtime_json(
        cls,
        raw: bytes | str,
        *,
        signing_secret: bytes | str,
        identity_secret: bytes | str,
        association_integrity_key: bytes | str,
    ) -> "StripeBmacWebhookConfig":
        encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
        payload = _runtime_json(encoded, label="Stripe support config")
        if set(payload) != {
            "schema_version", "livemode", "payment_links", "prices",
            "account_links",
        } or payload.get("schema_version") != STRIPE_RUNTIME_CONFIG_SCHEMA_VERSION:
            raise SupportWebhookError("Stripe support config shape is invalid")

        def mappings(name: str) -> dict[str, str]:
            selected = payload.get(name)
            if not isinstance(selected, dict):
                raise SupportWebhookError(f"Stripe support {name} are invalid")
            result: dict[str, str] = {}
            for key, settings in selected.items():
                if not isinstance(settings, dict) or set(settings) != {"currency"}:
                    raise SupportWebhookError(
                        f"Stripe support {name} mapping is invalid"
                    )
                result[key] = settings.get("currency")
            return result

        return cls(
            livemode=payload.get("livemode"),
            payment_links=mappings("payment_links"),
            prices=mappings("prices"),
            account_links=payload.get("account_links"),
            signing_secret=signing_secret,
            identity_secret=identity_secret,
            association_integrity_key=association_integrity_key,
        )

    @classmethod
    def from_runtime_file(
        cls,
        path: str | os.PathLike[str],
        **secrets: bytes | str,
    ) -> "StripeBmacWebhookConfig":
        raw = _private_runtime_file(
            path,
            maximum_bytes=MAX_STRIPE_RUNTIME_CONFIG_BYTES,
            label="Stripe support config file",
        )
        return cls.from_runtime_json(raw, **secrets)

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "StripeBmacWebhookConfig":
        selected = os.environ if env is None else env
        inline = selected.get("MAESTRO_SUPPORT_STRIPE_BMAC_CONFIG_JSON")
        reference = selected.get("MAESTRO_SUPPORT_STRIPE_BMAC_CONFIG_FILE")
        if (inline is None) == (reference is None):
            raise SupportWebhookError(
                "configure exactly one Stripe support JSON source"
            )
        secrets = {
            "signing_secret": _runtime_secret(
                selected, "MAESTRO_SUPPORT_STRIPE_WEBHOOK_SECRET",
            ),
            "identity_secret": _runtime_secret(
                selected, "MAESTRO_SUPPORT_STRIPE_IDENTITY_HMAC_KEY",
            ),
            "association_integrity_key": _runtime_secret(
                selected, "MAESTRO_SUPPORT_STRIPE_ASSOCIATION_HMAC_KEY",
            ),
        }
        if reference is not None:
            return cls.from_runtime_file(reference, **secrets)
        return cls.from_runtime_json(inline or "", **secrets)


@dataclass(frozen=True, slots=True)
class StripeWebhookVerifier:
    """Verify a Stripe event without treating payment or Radar state as auth."""

    signing_secret: bytes = field(repr=False)
    contract: SupportEvidenceContract = STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT
    timestamp_tolerance_seconds: int = DEFAULT_TIMESTAMP_TOLERANCE_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "signing_secret",
            _secret_bytes(self.signing_secret, name="Stripe webhook secret"),
        )
        if not 30 <= self.timestamp_tolerance_seconds <= 3_600:
            raise SupportWebhookError("Stripe timestamp tolerance is invalid")

    def verify_event(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime | str,
    ) -> dict[str, Any]:
        signature_header = _header(headers, "Stripe-Signature")
        if not signature_header or len(signature_header) > 4_096:
            raise WebhookSignatureError("Stripe-Signature header is invalid")
        timestamp_values: list[str] = []
        signatures: list[str] = []
        for component in signature_header.split(","):
            name, separator, value = component.strip().partition("=")
            if separator != "=":
                raise WebhookSignatureError("Stripe-Signature header is invalid")
            if name == "t":
                timestamp_values.append(value)
            elif name == "v1" and re.fullmatch(r"[0-9a-f]{64}", value):
                signatures.append(value)
        if len(timestamp_values) != 1 or not signatures:
            raise WebhookSignatureError("Stripe-Signature header is invalid")
        try:
            timestamp = int(timestamp_values[0])
        except ValueError as error:
            raise WebhookSignatureError(
                "Stripe-Signature timestamp is invalid"
            ) from error
        now = _utc(received_at)
        try:
            signed_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as error:
            raise WebhookSignatureError(
                "Stripe-Signature timestamp is invalid"
            ) from error
        if abs((now - signed_at).total_seconds()) > self.timestamp_tolerance_seconds:
            raise WebhookTimestampError("Stripe webhook timestamp is outside tolerance")
        if not isinstance(raw_body, bytes) or len(raw_body) > MAX_WEBHOOK_BYTES:
            raise WebhookPayloadError("webhook body size is invalid")
        signed = str(timestamp).encode("ascii") + b"." + raw_body
        expected = hmac.new(
            self.signing_secret, signed, hashlib.sha256,
        ).hexdigest()
        if not any(hmac.compare_digest(expected, value) for value in signatures):
            raise WebhookSignatureError("Stripe webhook signature is invalid")

        payload = _json_without_duplicate_keys(raw_body)
        event_id = payload.get("id")
        event_type = payload.get("type")
        data = payload.get("data")
        if (
            not isinstance(event_id, str)
            or re.fullmatch(r"evt_[A-Za-z0-9]{6,255}", event_id) is None
            or event_type not in {
                *self.contract.positive_event_types,
                *self.contract.reversal_event_types,
            }
            or not isinstance(payload.get("livemode"), bool)
            or not isinstance(data, dict)
            or not isinstance(data.get("object"), dict)
        ):
            raise WebhookPayloadError("Stripe support event shape is invalid")
        return payload


_DRAFT_RECORD_KEYS = frozenset({
    "provider", "source_event_key", "subject_key", "kind", "occurred_at",
    "amount_minor", "currency", "contract_key", "related_event_key",
    "fulfillment_item", "fulfillment_status", "actor_key",
})
_TARGET_RECORD_KEYS = frozenset({
    "subject_key", "kind", "amount_minor", "currency", "contract_key",
})
_ASSOCIATION_KINDS = ("charge", "payment_intent", "subscription")


def _draft_record(draft: ContributionEventDraft) -> dict[str, Any]:
    return {
        "provider": draft.provider,
        "source_event_key": draft.source_event_key,
        "subject_key": draft.subject_key,
        "kind": draft.kind,
        "occurred_at": _iso(draft.occurred_at),
        "amount_minor": draft.amount_minor,
        "currency": draft.currency,
        "contract_key": draft.contract_key,
        "related_event_key": draft.related_event_key,
        "fulfillment_item": draft.fulfillment_item,
        "fulfillment_status": draft.fulfillment_status,
        "actor_key": draft.actor_key,
    }


def _draft_from_record(record: Mapping[str, Any]) -> ContributionEventDraft:
    if not isinstance(record, Mapping) or set(record) != _DRAFT_RECORD_KEYS:
        raise StripeAssociationIntegrityError(
            "Stripe association draft shape is invalid"
        )
    try:
        draft = ContributionEventDraft(**record)
        normalized = _draft_record(draft)
    except (TypeError, SupportWebhookError) as error:
        raise StripeAssociationIntegrityError(
            "Stripe association draft is invalid"
        ) from error
    if dict(record) != normalized:
        raise StripeAssociationIntegrityError(
            "Stripe association draft is not canonical"
        )
    return draft


class FileStripeAssociationStore:
    """Integrity-sealed opaque Stripe-to-contribution associations."""

    CANONICAL_PATH = (
        Path(__file__).resolve().parents[1]
        / "storage" / "support" / "stripe_bmac_associations.json"
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
                candidate.resolve(strict=False).relative_to(
                    Path(tempfile.gettempdir()).resolve(),
                )
            except ValueError as error:
                raise SupportWebhookError(
                    "custom Stripe association paths must be temporary"
                ) from error
            if not allow_test_path:
                raise SupportWebhookError(
                    "custom Stripe association paths require explicit test approval"
                )
        if candidate.resolve(strict=False) != candidate:
            raise SupportWebhookError(
                "Stripe association path must not use symlinks"
            )
        self.path = candidate
        self.lock_path = candidate.with_suffix(candidate.suffix + ".lock")
        self._key = _secret_bytes(
            integrity_key, name="Stripe association integrity key",
        )
        self._lock = threading.RLock()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "targets": {},
            "associations": {kind: {} for kind in _ASSOCIATION_KINDS},
            "adjustments": {},
            "event_drafts": {},
        }

    def _seal(self, state: Mapping[str, Any]) -> str:
        return hmac.new(self._key, _canonical(state), hashlib.sha256).hexdigest()

    def _validate_lock_permissions(self) -> None:
        try:
            metadata = self.lock_path.lstat()
        except OSError as error:
            raise SupportWebhookError(
                "Stripe association lock is unreadable"
            ) from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or self.lock_path.is_symlink()
            or (
                os.name != "nt"
                and stat.S_IMODE(metadata.st_mode) != 0o600
            )
        ):
            raise SupportWebhookError("Stripe association lock is unsafe")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            if self.path.is_symlink():
                raise StripeAssociationIntegrityError(
                    "Stripe association path is unsafe"
                )
            return self._empty_state()
        try:
            raw = _private_runtime_file(
                self.path,
                maximum_bytes=MAX_STRIPE_ASSOCIATION_STATE_BYTES,
                label="Stripe association state",
            )
            payload = json.loads(raw.decode("utf-8"))
        except SupportWebhookError as error:
            raise StripeAssociationIntegrityError(
                "Stripe association state is unsafe"
            ) from error
        except (UnicodeError, json.JSONDecodeError) as error:
            raise StripeAssociationIntegrityError(
                "Stripe association state is unreadable"
            ) from error
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "targets", "associations", "adjustments",
            "event_drafts", "state_hmac",
        }:
            raise StripeAssociationIntegrityError(
                "Stripe association state shape is invalid"
            )
        state = {
            "targets": payload.get("targets"),
            "associations": payload.get("associations"),
            "adjustments": payload.get("adjustments"),
            "event_drafts": payload.get("event_drafts"),
        }
        if (
            payload.get("schema_version") != STRIPE_ASSOCIATION_SCHEMA_VERSION
            or not isinstance(payload.get("state_hmac"), str)
            or not hmac.compare_digest(payload["state_hmac"], self._seal(state))
        ):
            raise StripeAssociationIntegrityError(
                "Stripe association state integrity failed"
            )
        targets = state["targets"]
        associations = state["associations"]
        adjustments = state["adjustments"]
        event_drafts = state["event_drafts"]
        if (
            not isinstance(targets, dict)
            or not isinstance(associations, dict)
            or set(associations) != set(_ASSOCIATION_KINDS)
            or not all(isinstance(values, dict) for values in associations.values())
            or not isinstance(adjustments, dict)
            or not isinstance(event_drafts, dict)
        ):
            raise StripeAssociationIntegrityError(
                "Stripe association state collections are invalid"
            )
        entry_count = len(targets) + len(adjustments) + len(event_drafts) + sum(
            len(values) for values in associations.values()
        )
        if entry_count > MAX_STRIPE_ASSOCIATION_ENTRIES:
            raise StripeAssociationIntegrityError(
                "Stripe association state exceeds its entry bound"
            )
        for source_key, record in event_drafts.items():
            if _OPAQUE_KEY_RE.fullmatch(source_key or "") is None:
                raise StripeAssociationIntegrityError(
                    "Stripe association event key is invalid"
                )
            draft = _draft_from_record(record)
            if draft.source_event_key != source_key:
                raise StripeAssociationIntegrityError(
                    "Stripe association event identity is invalid"
                )
        for source_key, target in targets.items():
            if (
                _OPAQUE_KEY_RE.fullmatch(source_key or "") is None
                or not isinstance(target, dict)
                or set(target) != _TARGET_RECORD_KEYS
                or _OPAQUE_KEY_RE.fullmatch(target.get("subject_key") or "") is None
                or target.get("kind") not in {
                    "one_time_contribution", "recurring_started",
                    "recurring_renewed",
                }
                or not isinstance(target.get("amount_minor"), int)
                or isinstance(target.get("amount_minor"), bool)
                or target["amount_minor"] <= 0
                or _CURRENCY_RE.fullmatch(target.get("currency") or "") is None
                or (
                    target.get("contract_key") is not None
                    and _OPAQUE_KEY_RE.fullmatch(target["contract_key"]) is None
                )
            ):
                raise StripeAssociationIntegrityError(
                    "Stripe association target is invalid"
                )
        for values in associations.values():
            if not all(
                _OPAQUE_KEY_RE.fullmatch(key or "") is not None
                and source_key in targets
                for key, source_key in values.items()
            ):
                raise StripeAssociationIntegrityError(
                    "Stripe provider association is invalid"
                )
        for source_key, values in adjustments.items():
            if (
                source_key not in targets
                or not isinstance(values, dict)
                or set(values) != {"refund_minor", "chargeback_minor"}
                or not all(type(value) is int and value >= 0 for value in values.values())
                or sum(values.values()) > targets[source_key]["amount_minor"]
            ):
                raise StripeAssociationIntegrityError(
                    "Stripe adjustment association is invalid"
                )
        return state

    def _write(self, state: Mapping[str, Any]) -> None:
        entry_count = (
            len(state["targets"])
            + len(state["adjustments"])
            + len(state["event_drafts"])
            + sum(len(values) for values in state["associations"].values())
        )
        if entry_count > MAX_STRIPE_ASSOCIATION_ENTRIES:
            raise SupportWebhookError(
                "Stripe association entry bound reached"
            )
        payload = {
            "schema_version": STRIPE_ASSOCIATION_SCHEMA_VERSION,
            **state,
            "state_hmac": self._seal(state),
        }
        encoded = _canonical(payload)
        if len(encoded) > MAX_STRIPE_ASSOCIATION_STATE_BYTES:
            raise SupportWebhookError(
                "Stripe association state would exceed its byte bound"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.resolve(strict=False) != self.path.parent:
            raise SupportWebhookError(
                "Stripe association directory must not use symlinks"
            )
        descriptor, temp_name = tempfile.mkstemp(
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
            if not callable(restrict):
                os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
            if os.name != "nt" and stat.S_IMODE(self.path.stat().st_mode) != 0o600:
                raise SupportWebhookError(
                    "Stripe association permissions are unsafe"
                )
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

    @staticmethod
    def _target_record(draft: ContributionEventDraft) -> dict[str, Any]:
        return {
            "subject_key": draft.subject_key,
            "kind": draft.kind,
            "amount_minor": draft.amount_minor,
            "currency": draft.currency,
            "contract_key": draft.contract_key,
        }

    @staticmethod
    def _resolve_target(
        state: Mapping[str, Any], associations: Mapping[str, str],
    ) -> str:
        resolved = {
            state["associations"][kind].get(key)
            for kind, key in associations.items()
            if state["associations"][kind].get(key) is not None
        }
        if len(resolved) != 1:
            raise WebhookPayloadError(
                "Stripe reversal has no exact original association"
            )
        return next(iter(resolved))

    def record_funding(
        self,
        draft: ContributionEventDraft,
        associations: Mapping[str, str],
    ) -> ContributionEventDraft:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with exclusive_file_lease(self.lock_path):
                self._validate_lock_permissions()
                state = self._read()
                if not associations or not set(associations).issubset(
                    _ASSOCIATION_KINDS,
                ):
                    raise WebhookPayloadError(
                        "Stripe funding associations are invalid"
                    )
                existing = state["event_drafts"].get(draft.source_event_key)
                if existing is not None:
                    stored = _draft_from_record(existing)
                    candidate = _draft_record(draft)
                    if draft.kind == "recurring_renewed":
                        candidate["related_event_key"] = stored.related_event_key
                    if candidate != dict(existing):
                        raise WebhookPayloadError(
                            "Stripe event identity was reused"
                        )
                    for kind, key in associations.items():
                        expected = stored.source_event_key
                        if kind == "subscription" and stored.kind == "recurring_renewed":
                            expected = stored.related_event_key
                        if state["associations"][kind].get(key) != expected:
                            raise WebhookPayloadError(
                                "Stripe event associations changed"
                            )
                    return stored
                selected = draft
                subscription_key = associations.get("subscription")
                subscription_source = (
                    None if subscription_key is None else
                    state["associations"]["subscription"].get(subscription_key)
                )
                if draft.kind == "recurring_started":
                    if subscription_key is None or subscription_source is not None:
                        raise WebhookPayloadError(
                            "Stripe recurring origin association is invalid"
                        )
                elif draft.kind == "recurring_renewed":
                    if subscription_source is None:
                        raise WebhookPayloadError(
                            "Stripe recurring origin association is missing"
                        )
                    origin = state["targets"][subscription_source]
                    if (
                        origin["subject_key"] != draft.subject_key
                        or origin["contract_key"] != draft.contract_key
                        or origin["currency"] != draft.currency
                    ):
                        raise WebhookPayloadError(
                            "Stripe recurring origin association conflicts"
                        )
                    selected = ContributionEventDraft(
                        **{
                            **_draft_record(draft),
                            "related_event_key": subscription_source,
                        }
                    )
                elif subscription_key is not None:
                    raise WebhookPayloadError(
                        "Stripe subscription association is invalid"
                    )
                target = self._target_record(selected)
                if selected.source_event_key in state["targets"]:
                    raise StripeAssociationIntegrityError(
                        "Stripe association target is incomplete"
                    )
                for kind, key in associations.items():
                    current = state["associations"][kind].get(key)
                    if kind == "subscription" and current is not None:
                        continue
                    if current is not None and current != selected.source_event_key:
                        raise WebhookPayloadError(
                            "Stripe provider identifier was already associated"
                        )
                state["targets"][selected.source_event_key] = target
                state["adjustments"][selected.source_event_key] = {
                    "refund_minor": 0,
                    "chargeback_minor": 0,
                }
                state["event_drafts"][selected.source_event_key] = _draft_record(
                    selected,
                )
                for kind, key in associations.items():
                    state["associations"][kind].setdefault(
                        key, selected.source_event_key,
                    )
                self._write(state)
                return selected

    def record_adjustment(
        self,
        *,
        source_event_key: str,
        kind: str,
        occurred_at: datetime | str,
        amount_minor: int,
        currency: str,
        associations: Mapping[str, str],
        cumulative_refund: bool = False,
    ) -> ContributionEventDraft:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with exclusive_file_lease(self.lock_path):
                self._validate_lock_permissions()
                state = self._read()
                existing = state["event_drafts"].get(source_event_key)
                if existing is not None:
                    draft = _draft_from_record(existing)
                    if draft.kind != kind or draft.currency != currency:
                        raise WebhookPayloadError(
                            "Stripe event identity was reused"
                        )
                    return draft
                if kind not in {"refund", "chargeback"} or not associations:
                    raise WebhookPayloadError("Stripe adjustment is invalid")
                target_source = self._resolve_target(state, associations)
                target = state["targets"][target_source]
                for association_kind, key in associations.items():
                    current = state["associations"][association_kind].get(key)
                    if current is not None and current != target_source:
                        raise WebhookPayloadError(
                            "Stripe reversal associations conflict"
                        )
                if target["currency"] != currency:
                    raise WebhookPayloadError(
                        "Stripe reversal currency does not match its origin"
                    )
                totals = state["adjustments"][target_source]
                bucket = "refund_minor" if kind == "refund" else "chargeback_minor"
                if (
                    type(amount_minor) is not int
                    or amount_minor <= 0
                    or amount_minor > target["amount_minor"]
                ):
                    raise WebhookPayloadError("Stripe reversal amount is invalid")
                delta = amount_minor - totals[bucket] if cumulative_refund else amount_minor
                if (
                    delta <= 0
                    or sum(totals.values()) + delta > target["amount_minor"]
                ):
                    raise WebhookPayloadError(
                        "Stripe reversal exceeds its exact origin"
                    )
                totals[bucket] += delta
                draft = ContributionEventDraft(
                    provider=STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT.provider_id,
                    source_event_key=source_event_key,
                    subject_key=target["subject_key"],
                    kind=kind,
                    occurred_at=occurred_at,
                    amount_minor=delta,
                    currency=currency,
                    contract_key=target["contract_key"],
                    related_event_key=target_source,
                )
                state["event_drafts"][source_event_key] = _draft_record(draft)
                for association_kind, key in associations.items():
                    state["associations"][association_kind].setdefault(
                        key, target_source,
                    )
                self._write(state)
                return draft

    def record_cancellation(
        self,
        *,
        source_event_key: str,
        occurred_at: datetime | str,
        subscription_key: str,
    ) -> ContributionEventDraft:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with exclusive_file_lease(self.lock_path):
                self._validate_lock_permissions()
                state = self._read()
                existing = state["event_drafts"].get(source_event_key)
                if existing is not None:
                    draft = _draft_from_record(existing)
                    if draft.kind != "recurring_canceled":
                        raise WebhookPayloadError(
                            "Stripe event identity was reused"
                        )
                    return draft
                target_source = state["associations"]["subscription"].get(
                    subscription_key,
                )
                if target_source is None:
                    raise WebhookPayloadError(
                        "Stripe cancellation has no exact original association"
                    )
                target = state["targets"][target_source]
                if (
                    target["kind"] not in {"recurring_started", "recurring_renewed"}
                    or target["contract_key"] is None
                ):
                    raise WebhookPayloadError(
                        "Stripe cancellation origin is invalid"
                    )
                draft = ContributionEventDraft(
                    provider=STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT.provider_id,
                    source_event_key=source_event_key,
                    subject_key=target["subject_key"],
                    kind="recurring_canceled",
                    occurred_at=occurred_at,
                    currency=target["currency"],
                    contract_key=target["contract_key"],
                    related_event_key=target_source,
                )
                state["event_drafts"][source_event_key] = _draft_record(draft)
                self._write(state)
                return draft


def _stripe_currency(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-z]{3}", value) is None:
        raise WebhookPayloadError("Stripe support currency is invalid")
    return value.upper()


def _stripe_amount(value: Any) -> int:
    if (
        type(value) is not int
        or value <= 0
        or value > 10_000_000_000
    ):
        raise WebhookPayloadError("Stripe support amount is invalid")
    return value


def _stripe_occurred_at(payload: Mapping[str, Any]) -> str:
    created = payload.get("created")
    if type(created) is not int:
        raise WebhookPayloadError("Stripe event creation time is invalid")
    try:
        return _iso(datetime.fromtimestamp(created, tz=timezone.utc))
    except (OverflowError, OSError, ValueError, SupportWebhookError) as error:
        raise WebhookPayloadError("Stripe event creation time is invalid") from error


class StripeBmacSupportWebhookAdapter:
    """Production Stripe/BMaC translation with no payment-derived auth."""

    provider_id = STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT.provider_id
    production_ready = True
    verification_method = "signed_webhook"

    def __init__(
        self,
        config: StripeBmacWebhookConfig,
        *,
        association_path: str | os.PathLike[str] | None = None,
        allow_test_path: bool = False,
    ):
        if not isinstance(config, StripeBmacWebhookConfig):
            raise SupportWebhookError("Stripe support config is required")
        self.config = config
        self._verifier = StripeWebhookVerifier(
            config.signing_secret,
            timestamp_tolerance_seconds=config.timestamp_tolerance_seconds,
        )
        self._associations = FileStripeAssociationStore(
            association_path,
            integrity_key=config.association_integrity_key,
            allow_test_path=allow_test_path,
        )

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "StripeBmacSupportWebhookAdapter":
        return cls(StripeBmacWebhookConfig.from_environment(env))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider_id={self.provider_id!r}, "
            f"livemode={self.config.livemode!r})"
        )

    def _verified_payload(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime | str,
    ) -> dict[str, Any]:
        payload = self._verifier.verify_event(
            raw_body, headers, received_at=received_at,
        )
        if payload.get("livemode") is not self.config.livemode:
            raise WebhookPayloadError("Stripe support mode does not match config")
        return payload

    def _source_event_key(self, payload: Mapping[str, Any]) -> str:
        return opaque_key(
            f"{self.provider_id}_event",
            _stripe_identifier(payload.get("id"), "evt"),
            self.config.identity_secret,
        )

    def _association(self, kind: str, raw_identifier: str) -> str:
        return opaque_key(
            f"stripe_{kind}", raw_identifier, self.config.identity_secret,
        )

    def _subject_for_customer(self, value: Any) -> str:
        customer = _stripe_identifier(value, "cus")
        subject = self.config.account_links.get(customer)
        if subject is None:
            raise WebhookPayloadError(
                "Stripe support customer has no existing opaque account link"
            )
        return subject

    @staticmethod
    def _object(payload: Mapping[str, Any], expected: str) -> dict[str, Any]:
        value = payload.get("data", {}).get("object")
        if not isinstance(value, dict) or value.get("object") != expected:
            raise WebhookPayloadError("Stripe support object type is invalid")
        object_mode = value.get("livemode")
        if object_mode is not None and object_mode is not payload.get("livemode"):
            raise WebhookPayloadError("Stripe support object mode is invalid")
        return value

    @staticmethod
    def _subscription_id(value: Mapping[str, Any]) -> str:
        candidates: set[str] = set()
        direct = value.get("subscription")
        if direct is not None:
            candidates.add(_stripe_identifier(direct, "sub"))
        parent = value.get("parent")
        if isinstance(parent, dict):
            details = parent.get("subscription_details")
            if isinstance(details, dict) and details.get("subscription") is not None:
                candidates.add(
                    _stripe_identifier(details.get("subscription"), "sub")
                )
        if len(candidates) != 1:
            raise WebhookPayloadError("Stripe subscription association is invalid")
        return next(iter(candidates))

    def _invoice_price(self, invoice: Mapping[str, Any]) -> tuple[str, str]:
        lines = invoice.get("lines")
        data = None if not isinstance(lines, dict) else lines.get("data")
        if not isinstance(data, list) or not 1 <= len(data) <= 100:
            raise WebhookPayloadError("Stripe invoice lines are invalid")
        prices: set[str] = set()
        for line in data:
            if not isinstance(line, dict):
                raise WebhookPayloadError("Stripe invoice line is invalid")
            raw_price: Any = line.get("price")
            pricing = line.get("pricing")
            if raw_price is None and isinstance(pricing, dict):
                details = pricing.get("price_details")
                if isinstance(details, dict):
                    raw_price = details.get("price")
            prices.add(_stripe_identifier(raw_price, "price"))
        if len(prices) != 1:
            raise WebhookPayloadError(
                "Stripe invoice must contain one approved Price"
            )
        price = next(iter(prices))
        currency = self.config.prices.get(price)
        if currency is None:
            raise WebhookPayloadError("Stripe Price is not approved for support")
        return price, currency

    def _invoice_payment_associations(
        self, invoice: Mapping[str, Any],
    ) -> dict[str, str]:
        identifiers: dict[str, set[str]] = {
            "charge": set(), "payment_intent": set(),
        }
        prefixes = {"charge": "ch", "payment_intent": "pi"}
        for kind in identifiers:
            raw = invoice.get(kind)
            if raw is not None:
                identifiers[kind].add(_stripe_identifier(raw, prefixes[kind]))
        payments = invoice.get("payments")
        payment_data = None if not isinstance(payments, dict) else payments.get("data")
        if payment_data is not None:
            if not isinstance(payment_data, list) or len(payment_data) > 100:
                raise WebhookPayloadError("Stripe invoice payments are invalid")
            for row in payment_data:
                if not isinstance(row, dict) or row.get("status") != "paid":
                    continue
                payment = row.get("payment")
                if not isinstance(payment, dict):
                    raise WebhookPayloadError("Stripe invoice payment is invalid")
                for kind in identifiers:
                    raw = payment.get(kind)
                    if raw is not None:
                        identifiers[kind].add(
                            _stripe_identifier(raw, prefixes[kind])
                        )
        if any(len(values) > 1 for values in identifiers.values()):
            raise WebhookPayloadError("Stripe invoice payment is ambiguous")
        result = {
            kind: self._association(kind, next(iter(values)))
            for kind, values in identifiers.items() if values
        }
        if not result:
            raise WebhookPayloadError(
                "Stripe invoice has no immutable payment association"
            )
        return result

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
        return self.provider_id, self._source_event_key(payload)

    def verify_and_translate(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        received_at: datetime | str,
        recorded_event: ContributionEvent | None = None,
    ) -> ContributionEventDraft:
        del recorded_event
        payload = self._verified_payload(
            raw_body, headers, received_at=received_at,
        )
        event_type = payload["type"]
        source_event_key = self._source_event_key(payload)
        occurred_at = _stripe_occurred_at(payload)

        if event_type == "checkout.session.completed":
            session = self._object(payload, "checkout.session")
            _stripe_identifier(session.get("id"), "cs")
            if (
                session.get("mode") != "payment"
                or session.get("status") != "complete"
                or session.get("payment_status") != "paid"
            ):
                raise WebhookPayloadError(
                    "Stripe Checkout payment is not complete"
                )
            payment_link = _stripe_identifier(
                session.get("payment_link"), "plink",
            )
            mapped_currency = self.config.payment_links.get(payment_link)
            currency = _stripe_currency(session.get("currency"))
            if mapped_currency is None or mapped_currency != currency:
                raise WebhookPayloadError(
                    "Stripe Payment Link is not approved for support"
                )
            payment_intent = _stripe_identifier(
                session.get("payment_intent"), "pi",
            )
            draft = ContributionEventDraft(
                provider=self.provider_id,
                source_event_key=source_event_key,
                subject_key=self._subject_for_customer(session.get("customer")),
                kind="one_time_contribution",
                occurred_at=occurred_at,
                amount_minor=_stripe_amount(session.get("amount_total")),
                currency=currency,
            )
            return self._associations.record_funding(
                draft,
                {"payment_intent": self._association(
                    "payment_intent", payment_intent,
                )},
            )

        if event_type == "invoice.paid":
            invoice = self._object(payload, "invoice")
            _stripe_identifier(invoice.get("id"), "in")
            if invoice.get("status") != "paid" or invoice.get("paid") is not True:
                raise WebhookPayloadError("Stripe invoice is not paid")
            _, mapped_currency = self._invoice_price(invoice)
            currency = _stripe_currency(invoice.get("currency"))
            if mapped_currency != currency:
                raise WebhookPayloadError(
                    "Stripe invoice currency does not match approved Price"
                )
            billing_reason = invoice.get("billing_reason")
            kind = {
                "subscription_create": "recurring_started",
                "subscription_cycle": "recurring_renewed",
            }.get(billing_reason)
            if kind is None:
                raise WebhookPayloadError(
                    "Stripe invoice billing reason is not approved for support"
                )
            subscription = self._subscription_id(invoice)
            associations = self._invoice_payment_associations(invoice)
            associations["subscription"] = self._association(
                "subscription", subscription,
            )
            draft = ContributionEventDraft(
                provider=self.provider_id,
                source_event_key=source_event_key,
                subject_key=self._subject_for_customer(invoice.get("customer")),
                kind=kind,
                occurred_at=occurred_at,
                amount_minor=_stripe_amount(invoice.get("amount_paid")),
                currency=currency,
                contract_key=self._association("subscription", subscription),
            )
            return self._associations.record_funding(draft, associations)

        if event_type == "charge.refunded":
            charge = self._object(payload, "charge")
            charge_id = _stripe_identifier(charge.get("id"), "ch")
            associations = {
                "charge": self._association("charge", charge_id),
            }
            if charge.get("payment_intent") is not None:
                payment_intent = _stripe_identifier(
                    charge.get("payment_intent"), "pi",
                )
                associations["payment_intent"] = self._association(
                    "payment_intent", payment_intent,
                )
            return self._associations.record_adjustment(
                source_event_key=source_event_key,
                kind="refund",
                occurred_at=occurred_at,
                amount_minor=_stripe_amount(charge.get("amount_refunded")),
                currency=_stripe_currency(charge.get("currency")),
                associations=associations,
                cumulative_refund=True,
            )

        if event_type == "charge.dispute.created":
            dispute = self._object(payload, "dispute")
            _stripe_identifier(dispute.get("id"), "du")
            associations = {
                "charge": self._association(
                    "charge", _stripe_identifier(dispute.get("charge"), "ch"),
                ),
            }
            if dispute.get("payment_intent") is not None:
                payment_intent = _stripe_identifier(
                    dispute.get("payment_intent"), "pi",
                )
                associations["payment_intent"] = self._association(
                    "payment_intent", payment_intent,
                )
            return self._associations.record_adjustment(
                source_event_key=source_event_key,
                kind="chargeback",
                occurred_at=occurred_at,
                amount_minor=_stripe_amount(dispute.get("amount")),
                currency=_stripe_currency(dispute.get("currency")),
                associations=associations,
            )

        if event_type == "customer.subscription.deleted":
            subscription = self._object(payload, "subscription")
            subscription_id = _stripe_identifier(
                subscription.get("id"), "sub",
            )
            if subscription.get("status") != "canceled":
                raise WebhookPayloadError("Stripe subscription is not canceled")
            return self._associations.record_cancellation(
                source_event_key=source_event_key,
                occurred_at=occurred_at,
                subscription_key=self._association(
                    "subscription", subscription_id,
                ),
            )

        raise WebhookPayloadError("Stripe support event type is unsupported")

class SupportWebhookAdapter(Protocol):
    provider_id: str
    production_ready: bool
    verification_method: str

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
    verification_method: str = "signed_webhook"

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
class OwnerAttestedContributionAdapter:
    """Translate owner-attested input; route reauthentication is required."""

    identity_secret: bytes = field(repr=False)
    provider_id: str = "manual"
    production_ready: bool = False
    verification_method: str = "owner_attested"

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


# Compatibility name for existing callers. It remains owner-attested and must
# never be passed to ``process_signed_webhook``.
ManualContributionAdapter = OwnerAttestedContributionAdapter


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

    if getattr(adapter, "verification_method", None) != "signed_webhook":
        raise SupportWebhookError("adapter does not prove signed webhook events")

    provider, source_event_key = adapter.verified_event_identity(
        raw_body, headers, received_at=received_at,
    )
    if provider != getattr(adapter, "provider_id", None):
        raise SupportWebhookError("verified webhook provider changed")
    recorded_event = ledger.event_for_source(provider, source_event_key)
    draft = adapter.verify_and_translate(
        raw_body,
        headers,
        received_at=received_at,
        recorded_event=recorded_event,
    )
    if (
        draft.provider != provider
        or draft.source_event_key != source_event_key
    ):
        raise SupportWebhookError("verified webhook identity changed during translation")
    event = ledger.append(draft, received_at=received_at)
    replay_guard.record(
        draft.provider, draft.source_event_key, now=received_at,
    )
    return event
