"""Consent and provider-locality checks for opt-in explicit prompt guidance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.host_terms import LAWFUL_USE_TERM, host_term_accepted


PUBLIC_LLM_PROVIDERS = frozenset({"openai", "anthropic"})


def provider_is_public(provider: Any) -> bool:
    return str(provider or "local").strip().lower() in PUBLIC_LLM_PROVIDERS


def mature_mode_allowed(services: Mapping[str, Any] | None) -> bool:
    """Return whether the host authorized explicit prompt-authoring guidance.

    The legacy function name remains for compatibility. This decision does not
    inspect creative content or gate model, LoRA, recipe, or output catalogs.
    """
    config = services if isinstance(services, Mapping) else {}
    return (
        config.get("nsfw_mode") is True
        and host_term_accepted(config, LAWFUL_USE_TERM)
        and not provider_is_public(config.get("llm_provider"))
    )


__all__ = [
    "PUBLIC_LLM_PROVIDERS",
    "mature_mode_allowed",
    "provider_is_public",
]
