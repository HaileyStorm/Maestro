"""Server-wide Krea 2 owner-responsibility attestation.

Krea's community license permits a manual human-review process as one form of
content-filter measure.  Maestro therefore records the owner's responsibility
without inspecting prompts or outputs, adding a classifier, or sending local
content to a third party.  This policy is only a license-condition gate; model
artifacts, runtime readiness, LoRA terms, and project authorization remain
separate execution gates.
"""

from __future__ import annotations

import time
from collections.abc import Mapping

KREA_POLICY_SCHEMA_VERSION = 1
KREA_POLICY_SERVICE_KEY = "krea_owner_policy"
KREA_LICENSE_VERSION = "v1"
KREA_LICENSE_DATE = "2026-06-22"
KREA_LICENSE_URL = "https://www.krea.ai/krea-2-licensing"
KREA_AUP_URL = "https://www.krea.ai/krea-2-use-policy"
KREA_OWNER_DECLARATION = (
    "I accept responsibility for manually reviewing Krea 2 use and outputs "
    "under the Krea 2 Community License and Acceptable Use Policy."
)
KREA_USE_SCOPES = frozenset({"noncommercial", "commercial_under_1m"})


class KreaOwnerPolicyError(ValueError):
    """Raised when the owner attestation is incomplete or stale."""


def record_krea_owner_policy(
    services: dict[str, object],
    *,
    owner_attested: object,
    manual_review_accepted: object,
    local_content_stays_local: object,
    attribution_accepted: object,
    use_scope: object,
    license_version: object,
    license_date: object,
    declared_at_unix: int | None = None,
) -> dict[str, object]:
    """Store one exact owner attestation; never inspect creative content."""
    if type(services) is not dict:
        raise KreaOwnerPolicyError("Services configuration is invalid.")
    if not all(
        value is True
        for value in (
            owner_attested,
            manual_review_accepted,
            local_content_stays_local,
            attribution_accepted,
        )
    ):
        raise KreaOwnerPolicyError(
            "Accept the current Krea license, manual-review responsibility, "
            "attribution, and local-content terms."
        )
    if license_version != KREA_LICENSE_VERSION or license_date != KREA_LICENSE_DATE:
        raise KreaOwnerPolicyError(
            "The Krea 2 license changed. Review the current license first."
        )
    if type(use_scope) is not str or use_scope not in KREA_USE_SCOPES:
        raise KreaOwnerPolicyError("Choose noncommercial or under-$1M commercial use.")
    now = int(time.time()) if declared_at_unix is None else declared_at_unix
    if type(now) is not int or now < 0:
        raise KreaOwnerPolicyError("Declaration time is invalid.")
    record = {
        "schema_version": KREA_POLICY_SCHEMA_VERSION,
        "owner_attested": True,
        "manual_review_accepted": True,
        "local_content_stays_local": True,
        "attribution_accepted": True,
        "maestro_content_filtering": False,
        "use_scope": use_scope,
        "declaration": KREA_OWNER_DECLARATION,
        "license_version": KREA_LICENSE_VERSION,
        "license_date": KREA_LICENSE_DATE,
        "declared_at_unix": now,
    }
    services[KREA_POLICY_SERVICE_KEY] = record
    return dict(record)


def krea_owner_policy_status(
    services: Mapping[str, object] | None,
) -> dict[str, object]:
    """Project a bounded decision without creative content or private evidence."""
    record = services.get(KREA_POLICY_SERVICE_KEY) if isinstance(services, Mapping) else None
    valid = (
        type(record) is dict
        and set(record) == {
            "schema_version", "owner_attested", "manual_review_accepted",
            "local_content_stays_local", "attribution_accepted",
            "maestro_content_filtering", "use_scope", "declaration",
            "license_version", "license_date", "declared_at_unix",
        }
        and record.get("schema_version") == KREA_POLICY_SCHEMA_VERSION
        and record.get("owner_attested") is True
        and record.get("manual_review_accepted") is True
        and record.get("local_content_stays_local") is True
        and record.get("attribution_accepted") is True
        and record.get("maestro_content_filtering") is False
        and record.get("use_scope") in KREA_USE_SCOPES
        and record.get("declaration") == KREA_OWNER_DECLARATION
        and record.get("license_version") == KREA_LICENSE_VERSION
        and record.get("license_date") == KREA_LICENSE_DATE
        and type(record.get("declared_at_unix")) is int
        and int(record["declared_at_unix"]) >= 0
    )
    if not valid:
        return {
            "attested": False,
            "availability_status": "owner_attestation_required",
            "local_execution_allowed": False,
            "hosted_execution_allowed": False,
            "maestro_content_filtering": False,
        }
    return {
        "attested": True,
        "availability_status": "license_conditions_recorded",
        "local_execution_allowed": True,
        "hosted_execution_allowed": False,
        "maestro_content_filtering": False,
        "manual_owner_review": True,
        "use_scope": str(record["use_scope"]),
        "declared_at_unix": int(record["declared_at_unix"]),
    }
