"""Actor-aware, content-neutral Krea 2 execution policy.

The host owner records one manual-review attestation for the current Krea 2
license.  The server then assigns the license scope from the authenticated
Maestro account role: owners generate noncommercially, while ordinary users
generate under the under-$1M commercial scope.  Prompts and outputs are never
inspected, classified, rewritten, or sent elsewhere by this policy.
"""

from __future__ import annotations

import time
from collections.abc import Mapping

KREA_POLICY_SCHEMA_VERSION = 3
KREA_POLICY_SERVICE_KEY = "krea_owner_policy"
KREA_LICENSE_VERSION = "v1"
KREA_LICENSE_DATE = "2026-06-22"
KREA_LICENSE_URL = "https://www.krea.ai/krea-2-licensing"
KREA_AUP_URL = "https://www.krea.ai/krea-2-use-policy"
KREA_OWNER_DECLARATION = (
    "I accept responsibility for manually reviewing Krea 2 use and outputs "
    "under the Krea 2 Community License and Acceptable Use Policy, and for "
    "deleting any outputs that should not remain."
)
_LEGACY_OWNER_DECLARATION = (
    "I accept responsibility for manually reviewing Krea 2 use and outputs "
    "under the Krea 2 Community License and Acceptable Use Policy."
)
KREA_USE_SCOPES = frozenset({"noncommercial", "commercial_under_1m"})
KREA_ROLE_USE_SCOPES = {
    "owner": "noncommercial",
    "user": "commercial_under_1m",
}
KREA2_ARCHITECTURES = frozenset({
    "krea2_raw",
    "krea2_raw_edit",
    "krea2_turbo",
    "krea2_turbo_edit",
})

_ROLE_RECORD_KEYS = frozenset({
    "schema_version",
    "owner_attested",
    "manual_review_accepted",
    "local_content_stays_local",
    "attribution_accepted",
    "maestro_content_filtering",
    "role_use_scopes",
    "declaration",
    "license_version",
    "license_date",
    "declared_at_unix",
})
_V1_RECORD_KEYS = (_ROLE_RECORD_KEYS - {"role_use_scopes"}) | {"use_scope"}


class KreaOwnerPolicyError(ValueError):
    """Raised when Krea actor policy cannot authorize local execution."""


def _exact_role_scope_map(value: object) -> bool:
    return (
        type(value) is dict
        and set(value) == set(KREA_ROLE_USE_SCOPES)
        and all(type(role) is str for role in value)
        and value == KREA_ROLE_USE_SCOPES
    )


def record_krea_owner_policy(
    services: dict[str, object],
    *,
    owner_attested: object,
    manual_review_accepted: object,
    local_content_stays_local: object,
    attribution_accepted: object,
    role_use_scopes: object,
    schema_version: object,
    declaration: object,
    license_version: object,
    license_date: object,
    declared_at_unix: int | None = None,
) -> dict[str, object]:
    """Store the exact displayed host attestation without creative-content access."""
    if type(services) is not dict:
        raise KreaOwnerPolicyError("Services configuration is invalid.")
    if (
        type(schema_version) is not int
        or schema_version != KREA_POLICY_SCHEMA_VERSION
        or type(declaration) is not str
        or declaration != KREA_OWNER_DECLARATION
    ):
        raise KreaOwnerPolicyError(
            "The Krea manual-review declaration changed. Refresh the settings "
            "and review the current declaration before confirming."
        )
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
    if not _exact_role_scope_map(role_use_scopes):
        raise KreaOwnerPolicyError(
            "Krea role scopes must map owner to noncommercial and user to "
            "commercial under $1M."
        )
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
        "role_use_scopes": dict(KREA_ROLE_USE_SCOPES),
        "declaration": KREA_OWNER_DECLARATION,
        "license_version": KREA_LICENSE_VERSION,
        "license_date": KREA_LICENSE_DATE,
        "declared_at_unix": now,
    }
    services[KREA_POLICY_SERVICE_KEY] = record
    return {**record, "role_use_scopes": dict(KREA_ROLE_USE_SCOPES)}


def _valid_common_record(
    record: object, *, declaration: str = KREA_OWNER_DECLARATION,
) -> bool:
    return (
        type(record) is dict
        and record.get("owner_attested") is True
        and record.get("manual_review_accepted") is True
        and record.get("local_content_stays_local") is True
        and record.get("attribution_accepted") is True
        and record.get("maestro_content_filtering") is False
        and type(record.get("declaration")) is str
        and record.get("declaration") == declaration
        and record.get("license_version") == KREA_LICENSE_VERSION
        and record.get("license_date") == KREA_LICENSE_DATE
        and type(record.get("declared_at_unix")) is int
        and int(record["declared_at_unix"]) >= 0
    )


def krea_owner_policy_status(
    services: Mapping[str, object] | None,
) -> dict[str, object]:
    """Project current status without silently upgrading earlier declarations."""
    record = services.get(KREA_POLICY_SERVICE_KEY) if isinstance(services, Mapping) else None
    if (
        type(record) is dict
        and type(record.get("schema_version")) is int
        and _valid_common_record(record, declaration=_LEGACY_OWNER_DECLARATION)
        and (
            (record.get("schema_version") == 1
             and set(record) == set(_V1_RECORD_KEYS)
             and record.get("use_scope") in KREA_USE_SCOPES)
            or (record.get("schema_version") == 2
                and set(record) == set(_ROLE_RECORD_KEYS)
                and _exact_role_scope_map(record.get("role_use_scopes")))
        )
    ):
        return {
            "attested": False,
            "availability_status": "owner_policy_migration_required",
            "migration_required": True,
            "local_execution_allowed": False,
            "hosted_execution_allowed": False,
            "maestro_content_filtering": False,
        }
    valid = (
        type(record) is dict
        and set(record) == set(_ROLE_RECORD_KEYS)
        and type(record.get("schema_version")) is int
        and record.get("schema_version") == KREA_POLICY_SCHEMA_VERSION
        and _valid_common_record(record)
        and _exact_role_scope_map(record.get("role_use_scopes"))
    )
    if not valid:
        return {
            "attested": False,
            "availability_status": "owner_attestation_required",
            "migration_required": False,
            "local_execution_allowed": False,
            "hosted_execution_allowed": False,
            "maestro_content_filtering": False,
        }
    return {
        "attested": True,
        "availability_status": "license_conditions_recorded",
        "migration_required": False,
        "local_execution_allowed": True,
        "hosted_execution_allowed": False,
        "maestro_content_filtering": False,
        "manual_owner_review": True,
        "role_use_scopes": dict(KREA_ROLE_USE_SCOPES),
        "declared_at_unix": int(record["declared_at_unix"]),
    }


def resolve_krea_actor_scope(
    services: Mapping[str, object] | None,
    principal_role: object,
) -> str:
    """Return the current server-owned scope for one exact account role."""
    if type(principal_role) is not str or principal_role not in KREA_ROLE_USE_SCOPES:
        raise KreaOwnerPolicyError("An authenticated Maestro account role is required.")
    status = krea_owner_policy_status(services)
    if status.get("migration_required") is True:
        raise KreaOwnerPolicyError(
            "The saved Krea policy needs the current manual-review confirmation."
        )
    if (
        status.get("attested") is not True
        or status.get("local_execution_allowed") is not True
    ):
        raise KreaOwnerPolicyError(
            "The owner must confirm the current Krea manual-review policy first."
        )
    role_scopes = status.get("role_use_scopes")
    if not _exact_role_scope_map(role_scopes):
        raise KreaOwnerPolicyError("The saved Krea role policy is invalid.")
    return str(role_scopes[principal_role])


def is_registered_krea2_model(
    model_type: object,
    model_definitions: Mapping[str, object] | None,
) -> bool:
    """Classify only registered models with an exact Krea 2 architecture."""
    if type(model_type) is not str or not isinstance(model_definitions, Mapping):
        return False
    definition = model_definitions.get(model_type)
    if type(definition) is not dict:
        return False
    architecture = definition.get("architecture")
    return type(architecture) is str and architecture in KREA2_ARCHITECTURES
