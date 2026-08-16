"""Server-owned MiniMax H3 legal availability and execution policy.

The upstream MiniMax H3 license grants rights only inside its defined
Applicable Territory.  Network-derived location is deliberately irrelevant:
the owner records the country where this host will actually run H3, so a VPN
cannot silently enable or disable the model.  An excluded or unknown country
still fails closed.  A generic model-terms acceptance cannot replace separate
written MiniMax authorization for an excluded country.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

H3_UPSTREAM_REPOSITORY = "MiniMaxAI/MiniMax-H3"
H3_UPSTREAM_REVISION = "42ed227ee7df40d41602854ae760620d6eb651fe"
H3_LICENSE_SHA256 = (
    "59b99642b95ea21630e311198ddbfffbfe05aadba0c2f5d884cbdf4efcc90f44"
)
H3_LICENSE_URL = (
    "https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/"
    f"{H3_UPSTREAM_REVISION}/LICENSE"
)
H3_LICENSE_DATE = "2026-08-02"

H3_LOCATION_SCHEMA_VERSION = 1
H3_LOCATION_SERVICE_KEY = "h3_operating_location"
H3_LOCATION_DECLARATION = (
    "I confirm this is the country where MiniMax H3 will actually run."
)

# ISO 3166-1 alpha-2 codes excluded by the 2026-08-02 MiniMax H3 license:
# United States, United Kingdom, Republic of Korea, and every EU member state.
H3_EXCLUDED_TERRITORIES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GB", "GR", "HU", "IE", "IT", "KR", "LT", "LU", "LV", "MT",
    "NL", "PL", "PT", "RO", "SE", "SI", "SK", "US",
})
H3_RECOGNIZED_TERRITORIES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP
KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY
MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR
PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN
SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW
TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())  # noqa: SIM905 - readable ISO table
_TERRITORY_CODE = re.compile(r"^[A-Z]{2}$")

H3_REGISTERED_MODEL_TYPES = frozenset({
    "minimax_h3",
    "minimax_h3_pinkcherry_fl2va",
    "minimax_h3_w4a8_fl2va",
    "minimax_h3_ref2va",
})
H3_REGISTERED_ARCHITECTURES = frozenset({
    "minimax_h3",
    "minimax_h3_ref2va",
})

H3_LEGAL_BLOCKED_STATUS = "legal_blocked"
H3_LEGAL_BLOCKED_DETAIL = (
    "MiniMax H3 cannot run in the owner-declared operating country under the "
    "current upstream license. Accepting model terms does not grant access. "
    "Separate written MiniMax authorization is required."
)
H3_LOCATION_REQUIRED_DETAIL = (
    "Choose the country where this computer will actually run MiniMax H3. "
    "Maestro does not infer this from an IP address or VPN."
)


class H3LegalAccessError(RuntimeError):
    """Raised before work when a registered H3-family model cannot execute."""


@dataclass(frozen=True)
class H3LegalAccessDecision:
    availability_status: str
    execution_allowed: bool
    detail: str

    def public_projection(self) -> dict[str, object]:
        """Return the bounded, content-free catalog/API projection."""
        return {
            "availability_status": self.availability_status,
            "execution_allowed": self.execution_allowed,
        }


_BLOCKED_DECISION = H3LegalAccessDecision(
    availability_status=H3_LEGAL_BLOCKED_STATUS,
    execution_allowed=False,
    detail=H3_LEGAL_BLOCKED_DETAIL,
)

_LOCATION_REQUIRED_DECISION = H3LegalAccessDecision(
    availability_status="location_declaration_required",
    execution_allowed=False,
    detail=H3_LOCATION_REQUIRED_DETAIL,
)

_AVAILABLE_DECISION = H3LegalAccessDecision(
    availability_status="available",
    execution_allowed=True,
    detail="MiniMax H3 is available in the owner-declared operating country.",
)


class H3LocationDeclarationError(ValueError):
    """Raised when an owner operating-location declaration is not exact."""


def _normalize_territory_code(value: object) -> str:
    if type(value) is not str:
        raise H3LocationDeclarationError("Choose a two-letter country code.")
    code = value.strip().upper()
    if code == "UK":
        code = "GB"
    if _TERRITORY_CODE.fullmatch(code) is None or code not in H3_RECOGNIZED_TERRITORIES:
        raise H3LocationDeclarationError("Choose a two-letter country code.")
    return code


def record_h3_operating_location(
    services: dict[str, object],
    *,
    territory_code: object,
    owner_attested: object,
    license_revision: object,
    license_sha256: object,
    declared_at_unix: int | None = None,
) -> dict[str, object]:
    """Store one exact owner declaration without consulting network location."""
    if type(services) is not dict:
        raise H3LocationDeclarationError("Services configuration is invalid.")
    if owner_attested is not True:
        raise H3LocationDeclarationError(
            "Confirm where this computer will actually run MiniMax H3."
        )
    if license_revision != H3_UPSTREAM_REVISION or license_sha256 != H3_LICENSE_SHA256:
        raise H3LocationDeclarationError(
            "The MiniMax H3 license changed. Review the current license first."
        )
    code = _normalize_territory_code(territory_code)
    now = int(time.time()) if declared_at_unix is None else declared_at_unix
    if type(now) is not int or now < 0:
        raise H3LocationDeclarationError("Declaration time is invalid.")
    record = {
        "schema_version": H3_LOCATION_SCHEMA_VERSION,
        "territory_code": code,
        "owner_attested": True,
        "declaration": H3_LOCATION_DECLARATION,
        "license_revision": H3_UPSTREAM_REVISION,
        "license_sha256": H3_LICENSE_SHA256,
        "declared_at_unix": now,
    }
    services[H3_LOCATION_SERVICE_KEY] = record
    return dict(record)


def h3_operating_location_status(
    services: Mapping[str, object] | None,
) -> dict[str, object]:
    """Return an owner-facing, content-free status for the stored declaration."""
    record = services.get(H3_LOCATION_SERVICE_KEY) if isinstance(services, Mapping) else None
    valid = (
        type(record) is dict
        and set(record) == {
            "schema_version", "territory_code", "owner_attested", "declaration",
            "license_revision", "license_sha256", "declared_at_unix",
        }
        and record.get("schema_version") == H3_LOCATION_SCHEMA_VERSION
        and record.get("owner_attested") is True
        and record.get("declaration") == H3_LOCATION_DECLARATION
        and record.get("license_revision") == H3_UPSTREAM_REVISION
        and record.get("license_sha256") == H3_LICENSE_SHA256
        and type(record.get("declared_at_unix")) is int
        and int(record["declared_at_unix"]) >= 0
        and type(record.get("territory_code")) is str
        and _TERRITORY_CODE.fullmatch(str(record["territory_code"])) is not None
        and record.get("territory_code") in H3_RECOGNIZED_TERRITORIES
    )
    if not valid:
        return {
            "declared": False,
            "territory_code": None,
            **_LOCATION_REQUIRED_DECISION.public_projection(),
        }
    code = str(record["territory_code"])
    decision = _BLOCKED_DECISION if code in H3_EXCLUDED_TERRITORIES else _AVAILABLE_DECISION
    return {
        "declared": True,
        "territory_code": code,
        "declared_at_unix": int(record["declared_at_unix"]),
        **decision.public_projection(),
    }


def is_registered_h3_family(
    model_type: object,
    *,
    model_def: Mapping[str, object] | None = None,
    architecture: object = None,
) -> bool:
    """Classify H3 by registered server identity, never names or file paths."""
    registered_id = str(model_type or "").strip()
    registered_architecture = str(architecture or "").strip()
    if registered_id in H3_REGISTERED_MODEL_TYPES:
        return True
    if registered_architecture in H3_REGISTERED_ARCHITECTURES:
        return True
    if not isinstance(model_def, Mapping):
        return False
    return str(model_def.get("architecture") or "").strip() in (
        H3_REGISTERED_ARCHITECTURES
    )


def h3_legal_access_decision(
    model_type: object,
    *,
    model_def: Mapping[str, object] | None = None,
    architecture: object = None,
    services: Mapping[str, object] | None = None,
) -> H3LegalAccessDecision | None:
    """Return the current decision for one registered model, if it is H3."""
    if not is_registered_h3_family(
        model_type, model_def=model_def, architecture=architecture,
    ):
        return None
    status = h3_operating_location_status(services)
    if status["availability_status"] == "available":
        return _AVAILABLE_DECISION
    if status["availability_status"] == H3_LEGAL_BLOCKED_STATUS:
        return _BLOCKED_DECISION
    return _LOCATION_REQUIRED_DECISION


def h3_public_availability(
    model_type: object,
    *,
    model_def: Mapping[str, object] | None = None,
    architecture: object = None,
    services: Mapping[str, object] | None = None,
) -> dict[str, object]:
    decision = h3_legal_access_decision(
        model_type, model_def=model_def, architecture=architecture,
        services=services,
    )
    return decision.public_projection() if decision is not None else {}


def require_h3_execution_allowed(
    model_types: Iterable[object],
    *,
    model_defs: Mapping[str, Mapping[str, object]] | None = None,
    architectures: Mapping[str, object] | None = None,
    services: Mapping[str, object] | None = None,
) -> None:
    """Reject any registered H3 family member before executable work."""
    definitions = model_defs if isinstance(model_defs, Mapping) else {}
    resolved_architectures = (
        architectures if isinstance(architectures, Mapping) else {}
    )
    for value in model_types:
        model_type = str(value or "").strip()
        model_def = definitions.get(model_type)
        decision = h3_legal_access_decision(
            model_type,
            model_def=model_def if isinstance(model_def, Mapping) else None,
            architecture=resolved_architectures.get(model_type),
            services=services,
        )
        if decision is not None and not decision.execution_allowed:
            raise H3LegalAccessError(decision.detail)
