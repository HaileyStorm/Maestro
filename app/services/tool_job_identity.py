"""Opaque job identities and durable registration for standalone media tools."""

from __future__ import annotations

import uuid
from collections.abc import Container, Mapping
from typing import Any


JOB_ID_HEX_LENGTH = 32
TOOL_JOB_KINDS = frozenset({"tool_upscale", "tool_revoice", "tool_hflip", "tool_editor_export"})
_UNIQUE_ID_ATTEMPTS = 32


def new_unique_job_id(existing: Container[str] | None = None) -> str:
    """Mint an opaque 32-hex job id that is unique in ``existing``.

    The exhausted-collision fallback must still satisfy
    ``is_unique_generation_job_id``. A longer concatenated hex string would
    later fail queue-recovery identity checks, so refuse instead of minting
    a non-contract id.
    """

    occupied = existing if existing is not None else ()
    for _attempt in range(_UNIQUE_ID_ATTEMPTS):
        candidate = uuid.uuid4().hex
        if is_unique_generation_job_id(candidate) and candidate not in occupied:
            return candidate
    raise RuntimeError("unique 32-hex job id unavailable")


def is_unique_generation_job_id(value: Any) -> bool:
    """Return whether ``value`` is a 32-hex opaque generation-style job id."""

    return (
        type(value) is str
        and len(value) == JOB_ID_HEX_LENGTH
        and not set(value) - set("0123456789abcdef")
    )


def tool_job_requires_recovery_registration(job: Mapping[str, Any]) -> bool:
    """Standalone media tools still persist through the ordinary recovery hook."""

    try:
        kind = job.get("kind")
    except Exception:
        return False
    return type(kind) is str and kind in TOOL_JOB_KINDS
