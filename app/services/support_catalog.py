"""Public, provider-neutral support catalog.

The tracked catalog contains display copy and public support destinations only.
Threadspan destinations are built in; hosted providers remain disabled until
an operator opts in through the environment or an ignored local settings file.
Webhook secrets and provider credentials are never part of this projection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from .entitlements import (
    BenefitPolicy,
    DEFAULT_BENEFIT_POLICY,
    EntitlementError,
    TierRule,
    benefit_policy_public_projection,
)
from .support_webhooks import (
    STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT,
    SupportEvidenceContract,
)


CATALOG_CONFIG_SCHEMA_VERSION = 1
CATALOG_SCHEMA_VERSION = 2
DEFAULT_LOCAL_CONFIG = (
    Path(__file__).resolve().parents[1] / "settings" / "support.json"
)
_PUBLIC_CONFIG_KEYS = frozenset({"enabled", "support_url"})
_TIER_POLICY_KEYS = frozenset({
    "currency", "credit_unit", "one_time_bonus_cap",
    "one_time_validity_seconds", "recurring_validity_seconds",
    "promotional_credits_enabled", "one_time_tiers", "recurring_tiers",
})
_TIER_RULE_KEYS = frozenset({
    "tier", "minimum_minor", "promotional_maestro_credits", "benefits",
})


class SupportCatalogError(ValueError):
    """A public support-catalog setting is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class SupportProviderDefinition:
    provider_id: str
    display_name: str
    funding_modes: tuple[str, ...]
    public_home_url: str | None
    allowed_support_hosts: tuple[str, ...]
    description: str
    fixed_destinations: tuple[tuple[str, str], ...] = ()
    enabled_by_default: bool = False
    membership_contract: bool = False
    evidence_contract: SupportEvidenceContract | None = None


@dataclass(frozen=True, slots=True)
class SupportProviderStatus:
    definition: SupportProviderDefinition
    enabled: bool
    configured: bool
    state: str
    support_url: str | None

    def public_projection(
        self,
        *,
        recovery_confirmed: bool = False,
    ) -> dict[str, Any]:
        direct_compute_locked = (
            self.definition.provider_id == "direct_compute_sponsorship"
            and not recovery_confirmed
        )
        return {
            "provider_id": self.definition.provider_id,
            "display_name": self.definition.display_name,
            "funding_modes": list(self.definition.funding_modes),
            "public_home_url": self.definition.public_home_url,
            "description": self.definition.description,
            "enabled": self.enabled,
            "configured": self.configured,
            "state": "locked" if direct_compute_locked else self.state,
            "support_url": (
                self.support_url
                if self.state == "available" and not direct_compute_locked
                else None
            ),
            "destinations": [
                {"network": network, "destination": destination}
                for network, destination in self.definition.fixed_destinations
            ] if self.state == "available" else [],
            "membership_contract": self.definition.membership_contract,
            "support_evidence": (
                None
                if self.definition.evidence_contract is None
                else self.definition.evidence_contract.public_projection()
            ),
        }


@dataclass(frozen=True, slots=True)
class SupportCatalog:
    providers: tuple[SupportProviderStatus, ...]
    benefit_policy: BenefitPolicy = DEFAULT_BENEFIT_POLICY
    schema_version: int = CATALOG_SCHEMA_VERSION

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "providers": [item.public_projection() for item in self.providers],
            "supporter_benefits": benefit_policy_public_projection(
                self.benefit_policy,
            ),
            "provider_neutral": True,
            "paid_capacity_enabled": False,
            "notice": (
                "Support options are optional and provider-neutral. "
                "A listed provider is usable only when it is marked available."
            ),
        }


PROVIDER_DEFINITIONS = (
    SupportProviderDefinition(
        provider_id="threadspan",
        display_name="Threadspan",
        funding_modes=("crypto",),
        public_home_url=None,
        allowed_support_hosts=(),
        description=(
            "Direct Threadspan support using a published cryptocurrency "
            "destination; no contribution is automatically verified."
        ),
        fixed_destinations=(
            ("BTC", "1K628QLEh3sS8sEdzZfvuqqHRecVckSgaJ"),
            (
                "ADA",
                "addr1q9fd05jktgv49094z8hvjp6cqvn7npt8hfzjna4dvhezmvpgl92x5cevqghl4ng0we2es4xjp59gvm3nttdzwf9ym6lqr3628x",
            ),
            ("ETH", "0x78b6adac22415568A7F725a865206ccFd1a82F4c"),
        ),
        enabled_by_default=True,
    ),
    SupportProviderDefinition(
        provider_id="buy_me_a_coffee",
        display_name="Buy Me a Coffee",
        funding_modes=("one_time", "recurring"),
        public_home_url="https://www.buymeacoffee.com/",
        allowed_support_hosts=(
            "buymeacoffee.com",
            "www.buymeacoffee.com",
        ),
        description="One-time or recurring support through Buy Me a Coffee.",
        evidence_contract=STRIPE_BMAC_SUPPORT_EVIDENCE_CONTRACT,
    ),
    SupportProviderDefinition(
        provider_id="direct_compute_sponsorship",
        display_name="Vast.ai compute sponsorship",
        funding_modes=("one_time",),
        public_home_url=None,
        allowed_support_hosts=("vast.ai", "www.vast.ai", "cloud.vast.ai"),
        description=(
            "Optional direct-compute sponsorship through an operator-configured "
            "Vast.ai destination. Maestro does not process the payment, convert "
            "dollars into credits, or guarantee compute or service."
        ),
    ),
    SupportProviderDefinition(
        provider_id="patreon",
        display_name="Patreon",
        funding_modes=("recurring",),
        public_home_url="https://www.patreon.com/",
        allowed_support_hosts=("patreon.com", "www.patreon.com"),
        description=(
            "Disabled-by-default recurring membership contract through an "
            "operator-configured Patreon page. Membership is owner-attested "
            "unless a future signed adapter proves provider events."
        ),
        membership_contract=True,
    ),
)
PROVIDER_BY_ID = MappingProxyType({
    provider.provider_id: provider for provider in PROVIDER_DEFINITIONS
})


def _parse_enabled(value: Any, *, source: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise SupportCatalogError(f"{source} must be a boolean")


def _public_support_url(provider_id: str, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value or len(value) > 2_048:
        raise SupportCatalogError(f"{provider_id} support_url must be a public URL")
    if (
        value != value.strip()
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127
               for character in value)
    ):
        raise SupportCatalogError(f"{provider_id} support_url must be a public URL")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise SupportCatalogError(
            f"{provider_id} support_url is malformed"
        ) from error
    definition = PROVIDER_BY_ID[provider_id]
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as error:
        raise SupportCatalogError(
            f"{provider_id} support_url has an invalid port"
        ) from error
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or hostname not in definition.allowed_support_hosts
    ):
        raise SupportCatalogError(
            f"{provider_id} support_url must use an approved HTTPS provider host"
        )
    return value


def _tier_policy(value: Any) -> BenefitPolicy:
    if value is None:
        return DEFAULT_BENEFIT_POLICY
    if not isinstance(value, dict) or set(value) != _TIER_POLICY_KEYS:
        raise SupportCatalogError("supporter tier config has an invalid shape")

    def rules(name: str) -> tuple[TierRule, ...]:
        raw_rules = value.get(name)
        if (
            not isinstance(raw_rules, list)
            or not raw_rules
            or len(raw_rules) > 32
        ):
            raise SupportCatalogError("supporter tier rules are invalid")
        projected = []
        for raw in raw_rules:
            if not isinstance(raw, dict) or set(raw) != _TIER_RULE_KEYS:
                raise SupportCatalogError("supporter tier rule has an invalid shape")
            benefits = raw.get("benefits")
            if (
                not isinstance(benefits, list)
                or not benefits
                or not all(isinstance(item, str) for item in benefits)
            ):
                raise SupportCatalogError("supporter tier benefits are invalid")
            projected.append(TierRule(
                tier=raw.get("tier"),
                minimum_minor=raw.get("minimum_minor"),
                promotional_maestro_credits=raw.get(
                    "promotional_maestro_credits",
                ),
                benefits=tuple(benefits),
            ))
        return tuple(projected)

    policy = BenefitPolicy(
        currency=value.get("currency"),
        credit_unit=value.get("credit_unit"),
        one_time_bonus_cap=value.get("one_time_bonus_cap"),
        one_time_validity_seconds=value.get("one_time_validity_seconds"),
        recurring_validity_seconds=value.get("recurring_validity_seconds"),
        promotional_credits_enabled=value.get("promotional_credits_enabled"),
        one_time_rules=rules("one_time_tiers"),
        recurring_rules=rules("recurring_tiers"),
    )
    try:
        benefit_policy_public_projection(policy)
    except EntitlementError as error:
        raise SupportCatalogError("supporter tier config is invalid") from error
    return policy


def _load_local_public_config(
    path: Path | None,
) -> tuple[dict[str, dict[str, Any]], BenefitPolicy]:
    if path is None or not path.exists():
        return {}, DEFAULT_BENEFIT_POLICY
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SupportCatalogError("local support catalog config is unreadable") from error
    if (
        not isinstance(payload, dict)
        or not {"schema_version", "providers"}.issubset(payload)
        or not set(payload).issubset({
            "schema_version", "providers", "supporter_tiers",
        })
    ):
        raise SupportCatalogError("local support catalog config has an invalid shape")
    if payload.get("schema_version") != CATALOG_CONFIG_SCHEMA_VERSION:
        raise SupportCatalogError("local support catalog schema is unsupported")
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        raise SupportCatalogError("local support providers must be an object")
    result: dict[str, dict[str, Any]] = {}
    for provider_id, settings in providers.items():
        if provider_id not in PROVIDER_BY_ID:
            raise SupportCatalogError(f"unknown support provider: {provider_id}")
        if not isinstance(settings, dict) or not set(settings).issubset(
            _PUBLIC_CONFIG_KEYS
        ):
            raise SupportCatalogError(
                f"{provider_id} config may contain public settings only"
            )
        result[provider_id] = dict(settings)
    return result, _tier_policy(payload.get("supporter_tiers"))


def load_support_catalog(
    *,
    env: Mapping[str, str] | None = None,
    local_config_path: str | os.PathLike[str] | None = DEFAULT_LOCAL_CONFIG,
) -> SupportCatalog:
    """Load public enablement without ever reading or returning secrets.

    Environment values override the ignored local settings file.  The only
    recognized environment names are ``MAESTRO_SUPPORT_<ID>_ENABLED`` and
    ``MAESTRO_SUPPORT_<ID>_URL``; webhook credentials are intentionally not
    accepted here.
    """

    selected_env = os.environ if env is None else env
    path = None if local_config_path is None else Path(local_config_path)
    local, benefit_policy = _load_local_public_config(path)
    if "MAESTRO_SUPPORTER_TIERS_JSON" in selected_env:
        try:
            raw_policy = json.loads(selected_env["MAESTRO_SUPPORTER_TIERS_JSON"])
        except (TypeError, json.JSONDecodeError) as error:
            raise SupportCatalogError(
                "supporter tier environment config is invalid"
            ) from error
        benefit_policy = _tier_policy(raw_policy)
    statuses: list[SupportProviderStatus] = []
    for definition in PROVIDER_DEFINITIONS:
        provider_id = definition.provider_id
        settings = local.get(provider_id, {})
        prefix = f"MAESTRO_SUPPORT_{provider_id.upper()}"
        enabled_value: Any = settings.get(
            "enabled", definition.enabled_by_default,
        )
        support_url_value: Any = settings.get("support_url")
        if f"{prefix}_ENABLED" in selected_env:
            enabled_value = selected_env[f"{prefix}_ENABLED"]
        if f"{prefix}_URL" in selected_env:
            support_url_value = selected_env[f"{prefix}_URL"]
        enabled = _parse_enabled(
            enabled_value, source=f"{provider_id} enabled",
        )
        support_url = _public_support_url(provider_id, support_url_value)
        configured = bool(support_url is not None or definition.fixed_destinations)
        state = "available" if enabled and configured else (
            "unconfigured" if enabled else "disabled"
        )
        statuses.append(SupportProviderStatus(
            definition=definition,
            enabled=enabled,
            configured=configured,
            state=state,
            support_url=support_url,
        ))
    return SupportCatalog(tuple(statuses), benefit_policy=benefit_policy)


def public_support_catalog(**kwargs: Any) -> dict[str, Any]:
    """Return the validated public-only catalog wire object."""

    return load_support_catalog(**kwargs).public_projection()
