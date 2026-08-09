"""Versioned, host-wide acknowledgement records.

These records describe only which published notice version was accepted and
when.  They never carry an accepting identity, project, prompt, or media data.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from datetime import datetime, timezone
from typing import Any


LAWFUL_USE_TERM = "lawful_use"
REF2VA_TERM = "minimax_h3_ref2va"
HOST_TERMS_CONFIG_KEY = "host_terms_acceptance"
CURRENT_HOST_TERM_VERSIONS = {
    LAWFUL_USE_TERM: 1,
    REF2VA_TERM: 1,
}


class UnknownHostTermError(ValueError):
    """Raised when a client names a document Maestro does not publish."""


class StaleHostTermVersionError(ValueError):
    """Raised when a client did not accept the exact current version."""


def _nonempty_timestamp(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _stored_record(services: Mapping[str, Any], term: str) -> Mapping[str, Any]:
    records = services.get(HOST_TERMS_CONFIG_KEY)
    if not isinstance(records, Mapping):
        return {}
    record = records.get(term)
    return record if isinstance(record, Mapping) else {}


def _accepted_record(services: Mapping[str, Any], term: str) -> tuple[int | None, str | None]:
    record = _stored_record(services, term)
    version = record.get("version")
    accepted_at = _nonempty_timestamp(record.get("accepted_at"))
    if isinstance(version, bool) or not isinstance(version, int) or accepted_at is None:
        version = None
        accepted_at = None

    # The predecessor lawful-use notice stored only this timestamp.  It maps
    # specifically to v1, never to whatever version may be current later.
    if term == LAWFUL_USE_TERM and accepted_at is None:
        accepted_at = _nonempty_timestamp(services.get("nsfw_accepted_at"))
        if accepted_at is not None:
            version = 1
    return version, accepted_at


def host_terms_status(services: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    config = services if isinstance(services, Mapping) else {}
    status: dict[str, dict[str, Any]] = {}
    for term, current_version in CURRENT_HOST_TERM_VERSIONS.items():
        accepted_version, accepted_at = _accepted_record(config, term)
        status[term] = {
            "current_version": current_version,
            "accepted_version": accepted_version,
            "accepted_at": accepted_at,
            "accepted": accepted_version == current_version and accepted_at is not None,
        }
    return status


def host_term_accepted(services: Mapping[str, Any] | None, term: str) -> bool:
    document = host_terms_status(services).get(term)
    return bool(document and document["accepted"] is True)


def _materialize_legacy_lawful_use(services: MutableMapping[str, Any]) -> None:
    legacy_at = _nonempty_timestamp(services.get("nsfw_accepted_at"))
    records = services.setdefault(HOST_TERMS_CONFIG_KEY, {})
    if not isinstance(records, MutableMapping):
        records = {}
        services[HOST_TERMS_CONFIG_KEY] = records
    if legacy_at is not None and not isinstance(records.get(LAWFUL_USE_TERM), Mapping):
        records[LAWFUL_USE_TERM] = {"version": 1, "accepted_at": legacy_at}


def accept_host_term(
    services: MutableMapping[str, Any],
    term: str,
    version: Any,
    *,
    accepted_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    current_version = CURRENT_HOST_TERM_VERSIONS.get(term)
    if current_version is None:
        raise UnknownHostTermError("Unknown host terms document")
    if isinstance(version, bool) or not isinstance(version, int) or version != current_version:
        raise StaleHostTermVersionError(
            "The notice changed; review and accept the current version"
        )

    _materialize_legacy_lawful_use(services)
    records = services.setdefault(HOST_TERMS_CONFIG_KEY, {})
    if not isinstance(records, MutableMapping):
        records = {}
        services[HOST_TERMS_CONFIG_KEY] = records

    existing_version, existing_at = _accepted_record(services, term)
    if existing_version != current_version or existing_at is None:
        timestamp = accepted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        records[term] = {"version": current_version, "accepted_at": timestamp}
        if term == LAWFUL_USE_TERM:
            # Keep the predecessor field as a read-only compatibility mirror.
            services["nsfw_accepted_at"] = timestamp
    return host_terms_status(services)


__all__ = [
    "CURRENT_HOST_TERM_VERSIONS",
    "HOST_TERMS_CONFIG_KEY",
    "LAWFUL_USE_TERM",
    "REF2VA_TERM",
    "StaleHostTermVersionError",
    "UnknownHostTermError",
    "accept_host_term",
    "host_term_accepted",
    "host_terms_status",
]
