"""Versioned, content-neutral responsible-use acknowledgement primitives.

The notice and its digest are owned by the server.  This module deliberately
does not associate an acknowledgement with an account, session, project, or
request; a caller may bind the returned public record to its own authorized
principal without adding private data to the record itself.

``record_sha256`` is an unkeyed canonicalization checksum.  It can catch
accidental corruption and mutations where the checksum was not recomputed,
but it is not authenticity or tamper evidence.  The caller must protect the
record and its principal association with authorized, keyed persistence.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any


RESPONSIBLE_USE_DOCUMENT_ID = "maestro_responsible_use"
RESPONSIBLE_USE_ACCEPTANCE_SCHEMA_VERSION = 1
CURRENT_RESPONSIBLE_USE_VERSION = 1
MAX_ACCEPTANCE_RECORD_BYTES = 2 * 1024

_NOTICE_BY_VERSION: dict[int, dict[str, Any]] = {
    1: {
        "document_id": RESPONSIBLE_USE_DOCUMENT_ID,
        "version": 1,
        "title": "Responsible use",
        "paragraphs": [
            (
                "Use Maestro lawfully in your jurisdiction. Make sure you "
                "have the rights and permissions needed for the material "
                "you provide, the work you request, and how you use the "
                "results."
            ),
            (
                "Obtain consent when another person's identity, likeness, "
                "or other protected interests are involved. Follow the "
                "terms shown for each selected model and provider."
            ),
            (
                "Payments and donations do not authorize prohibited content "
                "or use, or change those responsibilities. Mature-content "
                "capability is optional, varies by model and setup, and is "
                "not assumed for every user. Mature examples are shown only "
                "after you choose to view or work with them."
            ),
            (
                "This acknowledgement does not remove legal duties that "
                "remain with Maestro's operator or its providers."
            ),
        ],
    },
}

_PINNED_NOTICE_SHA256_BY_VERSION = {
    1: "16f9456299c1aa9f8219f09e60924fa40f2838c1f14b87dc5b6d5aef5f185985",
}

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z"
)
_ACCEPTANCE_UNSIGNED_KEYS = {
    "schema_version",
    "document_id",
    "document_version",
    "content_sha256",
    "accepted_at",
}
_ACCEPTANCE_KEYS = _ACCEPTANCE_UNSIGNED_KEYS | {"record_sha256"}


class ResponsibleUseError(ValueError):
    """Base class for responsible-use contract validation failures."""


class UnknownResponsibleUseVersionError(ResponsibleUseError):
    """Raised when a version is not in the server-owned notice catalog."""


class StaleResponsibleUseNoticeError(ResponsibleUseError):
    """Raised when acceptance does not bind the exact current notice."""


class InvalidResponsibleUseAcceptanceError(ResponsibleUseError):
    """Raised when a stored acknowledgement is malformed or inconsistent."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _notice_digest(notice: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(notice)).hexdigest()


for _version, _notice in _NOTICE_BY_VERSION.items():
    if _notice_digest(_notice) != _PINNED_NOTICE_SHA256_BY_VERSION[_version]:
        raise RuntimeError(
            "Responsible-use copy changed without an intentional versioned "
            "digest update"
        )

CURRENT_RESPONSIBLE_USE_CONTENT_SHA256 = (
    _PINNED_NOTICE_SHA256_BY_VERSION[CURRENT_RESPONSIBLE_USE_VERSION]
)


def _known_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnknownResponsibleUseVersionError(
            "Responsible-use notice version must be an integer"
        )
    if value not in _NOTICE_BY_VERSION:
        raise UnknownResponsibleUseVersionError(
            "Unknown responsible-use notice version"
        )
    return value


def responsible_use_notice(version: int | None = None) -> dict[str, Any]:
    """Return a detached public copy of one server-owned notice."""

    selected = (
        CURRENT_RESPONSIBLE_USE_VERSION if version is None
        else _known_version(version)
    )
    notice = _NOTICE_BY_VERSION[selected]
    return {
        "document_id": notice["document_id"],
        "version": notice["version"],
        "content_sha256": _PINNED_NOTICE_SHA256_BY_VERSION[selected],
        "digest_algorithm": "sha256",
        "title": notice["title"],
        "paragraphs": list(notice["paragraphs"]),
    }


def responsible_use_binding(version: int | None = None) -> dict[str, Any]:
    """Return the immutable fields an acknowledgement must bind."""

    notice = responsible_use_notice(version)
    return {
        "document_id": notice["document_id"],
        "document_version": notice["version"],
        "content_sha256": notice["content_sha256"],
    }


def _canonical_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise InvalidResponsibleUseAcceptanceError(
            "Acceptance time must be a timezone-aware datetime"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidResponsibleUseAcceptanceError(
            "Acceptance time must include a timezone"
        )
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _validate_stored_timestamp(value: Any) -> str:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise InvalidResponsibleUseAcceptanceError(
            "Stored acceptance time is not canonical UTC"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidResponsibleUseAcceptanceError(
            "Stored acceptance time is invalid"
        ) from None
    if _canonical_timestamp(parsed) != value:
        raise InvalidResponsibleUseAcceptanceError(
            "Stored acceptance time is not canonical UTC"
        )
    return value


def _record_sha256(unsigned: Mapping[str, Any]) -> str:
    # This deliberately unkeyed checksum stabilizes restart serialization; it
    # does not authenticate the record or the principal associated elsewhere.
    return hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()


def normalize_acceptance_record(record: Any) -> dict[str, Any]:
    """Validate and detach a durable acknowledgement record.

    Exact keys prevent account identifiers or creative material from leaking
    into this public record.  The unkeyed checksum detects only accidental
    corruption or mutation without checksum recomputation.  It provides no
    authenticity or tamper evidence; authorized, keyed protection of both the
    record and its external principal binding remains a caller duty.
    """

    if not isinstance(record, Mapping) or set(record) != _ACCEPTANCE_KEYS:
        raise InvalidResponsibleUseAcceptanceError(
            "Responsible-use acceptance has an invalid schema"
        )
    if (
        isinstance(record.get("schema_version"), bool)
        or record.get("schema_version")
        != RESPONSIBLE_USE_ACCEPTANCE_SCHEMA_VERSION
    ):
        raise InvalidResponsibleUseAcceptanceError(
            "Responsible-use acceptance schema is unsupported"
        )
    if record.get("document_id") != RESPONSIBLE_USE_DOCUMENT_ID:
        raise InvalidResponsibleUseAcceptanceError(
            "Responsible-use acceptance document binding is invalid"
        )

    try:
        version = _known_version(record.get("document_version"))
    except UnknownResponsibleUseVersionError:
        raise InvalidResponsibleUseAcceptanceError(
            "Responsible-use acceptance version binding is invalid"
        ) from None
    content_sha256 = record.get("content_sha256")
    expected_content_sha256 = _PINNED_NOTICE_SHA256_BY_VERSION[version]
    if (
        not isinstance(content_sha256, str)
        or _SHA256_RE.fullmatch(content_sha256) is None
        or not hmac.compare_digest(content_sha256, expected_content_sha256)
    ):
        raise InvalidResponsibleUseAcceptanceError(
            "Responsible-use acceptance content binding is invalid"
        )
    accepted_at = _validate_stored_timestamp(record.get("accepted_at"))

    unsigned = {
        "schema_version": RESPONSIBLE_USE_ACCEPTANCE_SCHEMA_VERSION,
        "document_id": RESPONSIBLE_USE_DOCUMENT_ID,
        "document_version": version,
        "content_sha256": expected_content_sha256,
        "accepted_at": accepted_at,
    }
    record_sha256 = record.get("record_sha256")
    expected_record_sha256 = _record_sha256(unsigned)
    if (
        not isinstance(record_sha256, str)
        or _SHA256_RE.fullmatch(record_sha256) is None
        or not hmac.compare_digest(record_sha256, expected_record_sha256)
    ):
        raise InvalidResponsibleUseAcceptanceError(
            "Responsible-use acceptance checksum is invalid"
        )
    return {**unsigned, "record_sha256": expected_record_sha256}


def _require_current_binding(version: Any, content_sha256: Any) -> None:
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != CURRENT_RESPONSIBLE_USE_VERSION
        or not isinstance(content_sha256, str)
        or not hmac.compare_digest(
            content_sha256,
            CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
        )
    ):
        raise StaleResponsibleUseNoticeError(
            "The responsible-use notice changed; review the current version"
        )


def create_acceptance_record(
    version: Any,
    content_sha256: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a public record for the exact current server notice."""

    _require_current_binding(version, content_sha256)
    accepted_at = _canonical_timestamp(now or datetime.now(timezone.utc))
    unsigned = {
        "schema_version": RESPONSIBLE_USE_ACCEPTANCE_SCHEMA_VERSION,
        "document_id": RESPONSIBLE_USE_DOCUMENT_ID,
        "document_version": CURRENT_RESPONSIBLE_USE_VERSION,
        "content_sha256": CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
        "accepted_at": accepted_at,
    }
    return {**unsigned, "record_sha256": _record_sha256(unsigned)}


def accept_responsible_use(
    existing_record: Any,
    version: Any,
    content_sha256: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Accept the current notice, preserving an existing current record.

    A structurally valid older record can be replaced after a future notice
    version is added.  A malformed or checksum-inconsistent record fails
    closed instead of being silently overwritten.  Authenticity must be
    established by the caller's authorized, keyed persistence boundary.
    """

    _require_current_binding(version, content_sha256)
    if existing_record is not None:
        normalized = normalize_acceptance_record(existing_record)
        if normalized["document_version"] == CURRENT_RESPONSIBLE_USE_VERSION:
            return normalized
    return create_acceptance_record(version, content_sha256, now=now)


def responsible_use_status(record: Any) -> dict[str, Any]:
    """Return a privacy-free status for an optional durable record."""

    binding = responsible_use_binding()
    status = {
        **binding,
        "accepted": False,
        "accepted_at": None,
        "state": "not_accepted",
    }
    if record is None:
        return status
    try:
        normalized = normalize_acceptance_record(record)
    except InvalidResponsibleUseAcceptanceError:
        status["state"] = "invalid"
        return status
    status["accepted_at"] = normalized["accepted_at"]
    if normalized["document_version"] == CURRENT_RESPONSIBLE_USE_VERSION:
        status["accepted"] = True
        status["state"] = "accepted"
    else:
        status["state"] = "stale"
    return status


def serialize_acceptance_record(record: Any) -> str:
    """Serialize one validated record deterministically for durable storage."""

    normalized = normalize_acceptance_record(record)
    return _canonical_bytes(normalized).decode("utf-8")


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidResponsibleUseAcceptanceError(
                "Responsible-use acceptance contains duplicate fields"
            )
        result[key] = value
    return result


def deserialize_acceptance_record(payload: Any) -> dict[str, Any]:
    """Load one bounded, deterministic record after a process restart."""

    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise InvalidResponsibleUseAcceptanceError(
                "Responsible-use acceptance is not UTF-8"
            ) from None
    elif isinstance(payload, str):
        text = payload
    else:
        raise InvalidResponsibleUseAcceptanceError(
            "Responsible-use acceptance must be JSON text"
        )
    if not text or len(text.encode("utf-8")) > MAX_ACCEPTANCE_RECORD_BYTES:
        raise InvalidResponsibleUseAcceptanceError(
            "Responsible-use acceptance exceeds the storage limit"
        )
    try:
        value = json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
    except InvalidResponsibleUseAcceptanceError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise InvalidResponsibleUseAcceptanceError(
            "Responsible-use acceptance JSON is invalid"
        ) from None
    return normalize_acceptance_record(value)


__all__ = [
    "CURRENT_RESPONSIBLE_USE_CONTENT_SHA256",
    "CURRENT_RESPONSIBLE_USE_VERSION",
    "InvalidResponsibleUseAcceptanceError",
    "MAX_ACCEPTANCE_RECORD_BYTES",
    "RESPONSIBLE_USE_ACCEPTANCE_SCHEMA_VERSION",
    "RESPONSIBLE_USE_DOCUMENT_ID",
    "ResponsibleUseError",
    "StaleResponsibleUseNoticeError",
    "UnknownResponsibleUseVersionError",
    "accept_responsible_use",
    "create_acceptance_record",
    "deserialize_acceptance_record",
    "normalize_acceptance_record",
    "responsible_use_binding",
    "responsible_use_notice",
    "responsible_use_status",
    "serialize_acceptance_record",
]
