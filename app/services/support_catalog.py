"""Public, provider-neutral support catalog.

The tracked catalog contains display copy and public HTTPS destinations only.
Every provider is disabled until an operator opts in through the environment
or an ignored local settings file.  Webhook secrets and provider credentials
are deliberately outside this module and are never part of its projection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit


CATALOG_SCHEMA_VERSION = 1
DEFAULT_LOCAL_CONFIG = (
    Path(__file__).resolve().parents[1] / "settings" / "support.json"
)
_PUBLIC_CONFIG_KEYS = frozenset({"enabled", "support_url"})
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_DIRECT_COMPUTE_RESERVED_SUFFIXES = (
    "arpa",
    "corp",
    "example",
    "home",
    "internal",
    "invalid",
    "lan",
    "local",
    "localdomain",
    "localhost",
    "onion",
    "private",
    "test",
)


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
    requires_public_dns_host: bool = False


@dataclass(frozen=True, slots=True)
class SupportProviderStatus:
    definition: SupportProviderDefinition
    enabled: bool
    configured: bool
    state: str
    support_url: str | None

    def public_projection(self) -> dict[str, Any]:
        return {
            "provider_id": self.definition.provider_id,
            "display_name": self.definition.display_name,
            "funding_modes": list(self.definition.funding_modes),
            "public_home_url": self.definition.public_home_url,
            "description": self.definition.description,
            "enabled": self.enabled,
            "configured": self.configured,
            "state": self.state,
            "support_url": self.support_url if self.state == "available" else None,
        }


@dataclass(frozen=True, slots=True)
class SupportCatalog:
    providers: tuple[SupportProviderStatus, ...]
    schema_version: int = CATALOG_SCHEMA_VERSION

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "providers": [item.public_projection() for item in self.providers],
            "provider_neutral": True,
            "paid_capacity_enabled": False,
            "notice": (
                "Support options are optional and provider-neutral. "
                "A listed provider is usable only when it is marked available."
            ),
        }


PROVIDER_DEFINITIONS = (
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
    ),
    SupportProviderDefinition(
        provider_id="patreon",
        display_name="Patreon",
        funding_modes=("recurring",),
        public_home_url="https://www.patreon.com/",
        allowed_support_hosts=("patreon.com", "www.patreon.com"),
        description="Recurring support through an operator-configured Patreon page.",
    ),
    SupportProviderDefinition(
        provider_id="direct_compute_sponsorship",
        display_name="Direct compute sponsorship",
        funding_modes=("one_time",),
        public_home_url=None,
        allowed_support_hosts=(),
        description="Sponsor Continuum compute directly.",
        requires_public_dns_host=True,
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


def _is_public_dns_hostname(hostname: str) -> bool:
    """Recognize a public-looking DNS name without resolving it."""

    if (
        not hostname
        or len(hostname) > 253
        or hostname.endswith(".")
        or "." not in hostname
    ):
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return False
    labels = hostname.split(".")
    if (
        any(_DNS_LABEL_RE.fullmatch(label) is None for label in labels)
        or all(label.isdigit() for label in labels)
        or not labels[-1].isalpha()
        or len(labels[-1]) < 2
    ):
        return False
    return not any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in _DIRECT_COMPUTE_RESERVED_SUFFIXES
    )


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
        or (
            definition.requires_public_dns_host
            and not _is_public_dns_hostname(hostname)
        )
        or (
            not definition.requires_public_dns_host
            and hostname not in definition.allowed_support_hosts
        )
    ):
        raise SupportCatalogError(
            f"{provider_id} support_url must use an approved HTTPS provider host"
        )
    return value


def _load_local_public_config(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SupportCatalogError("local support catalog config is unreadable") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "providers",
    }:
        raise SupportCatalogError("local support catalog config has an invalid shape")
    if payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
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
    return result


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
    local = _load_local_public_config(path)
    statuses: list[SupportProviderStatus] = []
    for definition in PROVIDER_DEFINITIONS:
        provider_id = definition.provider_id
        settings = local.get(provider_id, {})
        prefix = f"MAESTRO_SUPPORT_{provider_id.upper()}"
        enabled_value: Any = settings.get("enabled", False)
        support_url_value: Any = settings.get("support_url")
        if f"{prefix}_ENABLED" in selected_env:
            enabled_value = selected_env[f"{prefix}_ENABLED"]
        if f"{prefix}_URL" in selected_env:
            support_url_value = selected_env[f"{prefix}_URL"]
        enabled = _parse_enabled(
            enabled_value, source=f"{provider_id} enabled",
        )
        support_url = _public_support_url(provider_id, support_url_value)
        configured = support_url is not None
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
    return SupportCatalog(tuple(statuses))


def public_support_catalog(**kwargs: Any) -> dict[str, Any]:
    """Return the validated public-only catalog wire object."""

    return load_support_catalog(**kwargs).public_projection()
