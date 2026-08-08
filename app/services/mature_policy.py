"""Central server-side classification for explicit models, LoRAs, and audio."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


PUBLIC_LLM_PROVIDERS = frozenset({"openai", "anthropic"})
_MATURE_WORDS = frozenset({
    "nsfw", "nude", "naked", "sex", "breast", "oral", "doggy",
    "xxx", "porn", "hentai", "uncensored", "unchained",
})
_MATURE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(value) for value in sorted(_MATURE_WORDS)) + r")\b",
    re.IGNORECASE,
)


def provider_is_public(provider: Any) -> bool:
    return str(provider or "local").strip().lower() in PUBLIC_LLM_PROVIDERS


def mature_mode_allowed(services: Mapping[str, Any] | None) -> bool:
    """Return whether mature UI/catalog features should be visible."""
    config = services if isinstance(services, Mapping) else {}
    accepted_at = config.get("nsfw_accepted_at")
    return (
        config.get("nsfw_mode") is True
        and isinstance(accepted_at, str)
        and bool(accepted_at.strip())
        and not provider_is_public(config.get("llm_provider"))
    )


def model_is_mature(model_definition: Mapping[str, Any] | None) -> bool:
    return bool(
        isinstance(model_definition, Mapping)
        and model_definition.get("nsfw_only") is True
    )


def lora_is_mature(
    *,
    filename: str = "",
    display_name: str = "",
    metadata: Mapping[str, Any] | None = None,
    guide_text: str = "",
) -> bool:
    """Classify a LoRA with authoritative override/sidecar precedence."""
    sidecar = metadata if isinstance(metadata, Mapping) else {}
    override = sidecar.get("nsfw_override")
    if isinstance(override, bool):
        return override
    if isinstance(sidecar.get("nsfw"), bool):
        return sidecar["nsfw"]
    blobs = [filename, display_name, guide_text]
    tags = sidecar.get("tags")
    if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes)):
        blobs.extend(str(tag) for tag in tags)
    for field in ("description", "versionDescription"):
        if isinstance(sidecar.get(field), str):
            blobs.append(sidecar[field])
    normalized = re.sub(r"[_-]", " ", " ".join(str(value or "") for value in blobs))
    return _MATURE_RE.search(normalized) is not None


def request_is_mature(
    *,
    model_definition: Mapping[str, Any] | None = None,
    loras: Sequence[Mapping[str, Any]] = (),
    mmaudio_variant: str = "",
) -> bool:
    """Classify explicit components without inspecting or rejecting content."""
    requested = model_is_mature(model_definition)
    requested = requested or str(mmaudio_variant or "").strip().lower() == "nsfw"
    requested = requested or any(
        lora_is_mature(
            filename=str(item.get("filename") or ""),
            display_name=str(item.get("name") or ""),
            metadata=item.get("metadata") if isinstance(item.get("metadata"), Mapping) else item,
            guide_text=str(item.get("guide") or ""),
        )
        for item in loras
        if isinstance(item, Mapping)
    )
    return requested


__all__ = [
    "PUBLIC_LLM_PROVIDERS",
    "lora_is_mature",
    "mature_mode_allowed",
    "model_is_mature",
    "provider_is_public",
    "request_is_mature",
]
