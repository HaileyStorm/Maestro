"""Opaque job IDs and the queue-recovery contract for standalone tools.

Generate already mints 32-hex unique IDs via ``launch._new_generation_job_id``
and registers them with queue recovery before any lifecycle transition.
``/api/v1/tools/upscale`` and ``/api/v1/tools/revoice`` still mint 8-hex IDs
and assign ``_jobs[job_id]`` directly. The first ``try_start`` then dies with
``Queue recovery job must be registered before transition``.

This module is the unreserved helper those owners should call. Do not edit
``launch.py`` or ``queue_recovery_adapter.py`` from this wave.
"""

from __future__ import annotations

import uuid
from collections.abc import Container, Mapping
from typing import Any


JOB_ID_HEX_LENGTH = 32
TOOL_JOB_KINDS = frozenset({"tool_upscale", "tool_revoice"})
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
    """Standalone GPU tools still persist through the ordinary recovery hook."""

    try:
        kind = job.get("kind")
    except Exception:
        return False
    return type(kind) is str and kind in TOOL_JOB_KINDS
