"""
OOM detection for the Performance Auto-Tune OOM recovery banner.

Single entry point: `detect_oom(exception, current_coefficient)` returns
either None (not an OOM failure) or a dict with structured info the UI
can use to suggest a fix.

Used by:
  - app/launch.py (Studio job exception handlers)
  - app/services/director_pipeline.py (Director pipeline exception handler)

Both surfaces attach the returned dict (when non-None) to the failure
payload as `oom_info`. The frontend OomRecoveryBanner watches for this
field and surfaces a "Lower VRAM headroom?" banner with a one-click
permanent-fix button.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional


# Only device-qualified allocator signatures are accepted.  A bare "out of
# memory" can describe CPU RAM, a child ffmpeg process, or an application
# message and must never turn into a VRAM diagnosis.
_GPU_OOM_SIGNATURES = (
    "cuda out of memory",
    "cuda error: out of memory",
    "hip out of memory",
    "cublas_status_alloc_failed",
    "cudnn_status_alloc_failed",
)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_GPU_OOM_CODES = frozenset({"cuda_oom", "hip_oom"})
_FAILURE_STAGES = {
    "model_load", "denoise", "vae_decode", "segment_checkpoint", "concat",
    "audio_mux", "postprocess", "flashvsr", "delivery", "publication",
    "generation",
}
from services.public_failure_copy import FAILURE_STAGE_DETAILS as _STAGE_DETAILS


def _suggest_lower_coefficient(current: float) -> Optional[float]:
    """Return the suggested next-lower coefficient, or None if already at floor.

    Drop by 0.10 with a floor of 0.50 (the slider's minimum). Below
    that, the user genuinely needs a different solution (smaller
    model, lower resolution, etc.) — coefficient can't help anymore.
    """
    if current <= 0.50:
        return None
    suggested = round(current - 0.10, 2)
    return max(suggested, 0.50)


def is_oom(exception: Exception) -> bool:
    """Return whether ``exception`` has a device-qualified OOM signature."""
    current = exception
    seen: set[int] = set()
    for _ in range(8):
        if not isinstance(current, BaseException) or id(current) in seen:
            break
        seen.add(id(current))
        err_str = str(current).lower()
        err_type = type(current).__name__.lower()
        err_module = type(current).__module__.lower()
        if any(signature in err_str for signature in _GPU_OOM_SIGNATURES):
            return True
        if (
            err_type == "outofmemoryerror"
            and (
                err_module.startswith("torch")
                or "cuda" in err_str
                or "hip" in err_str
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def safe_allocator_facts() -> dict:
    """Return path-free current CUDA allocator counters when available."""
    try:
        import torch

        if not torch.cuda.is_available():
            return {}
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        facts = {
            "device_type": "cuda",
            "free_bytes": int(free_bytes),
            "total_bytes": int(total_bytes),
            "allocated_bytes": int(torch.cuda.memory_allocated()),
            "reserved_bytes": int(torch.cuda.memory_reserved()),
        }
        if any(value < 0 for key, value in facts.items() if key.endswith("_bytes")):
            return {}
        return facts
    except Exception:
        return {}


def _honest_failure_code(code: str, *, is_oom: bool, stage: str) -> str:
    """Keep GPU OOM codes aligned with the detected ``is_oom`` boolean.

    Callers and exception attributes may pass ``cuda_oom`` for host, ffmpeg, or
    generic delivery failures. Remote status must not advertise VRAM exhaustion
    unless ``is_oom`` is actually true.
    """
    if is_oom:
        return "cuda_oom"
    if _SAFE_TOKEN_RE.fullmatch(code) is None or code in _GPU_OOM_CODES:
        return f"{stage}_failed"
    return code


def _position(current, total, *, variant=None) -> Optional[dict]:
    try:
        current_value = max(0, int(current or 0))
        total_value = max(0, int(total or 0))
    except (TypeError, ValueError):
        return None
    if current_value <= 0 and total_value <= 0:
        return None
    result = {"current": current_value, "total": total_value}
    if variant is not None:
        try:
            result["variant"] = max(1, int(variant or 1))
        except (TypeError, ValueError):
            result["variant"] = 1
    return result


def build_failure_details(
    exception: Exception,
    *,
    stage: str = "generation",
    code: str | None = None,
    segment: Mapping[str, object] | None = None,
    window: Mapping[str, object] | None = None,
    step: Mapping[str, object] | None = None,
    allocator: Mapping[str, object] | None = None,
) -> dict:
    """Build one remotely safe, path/content-free failure envelope.

    Detail is reviewed contract copy or the stage fallback; arbitrary exception
    text and the complete traceback stay machine-local. Remote status also carries
    a class token, stable stage/code, bounded progress, and allocator counters.
    """
    declared_stage = getattr(exception, "stage", stage)
    normalized_stage = (
        str(declared_stage)
        if str(declared_stage) in _FAILURE_STAGES else "generation"
    )
    detected_oom = is_oom(exception)
    declared_code = str(getattr(exception, "code", code or ""))
    normalized_code = _honest_failure_code(
        declared_code, is_oom=detected_oom, stage=normalized_stage,
    )
    identity = exception
    seen: set[int] = set()
    for _ in range(8):
        child = identity.__cause__ or identity.__context__
        if not isinstance(child, BaseException) or id(child) in seen:
            break
        seen.add(id(identity))
        identity = child
    exception_type = type(identity).__name__
    if _SAFE_TOKEN_RE.fullmatch(exception_type) is None:
        exception_type = "Exception"
    from services.planning_failure import (
        public_planning_failure_message,
        safe_public_contract_message,
    )

    stage_detail = _STAGE_DETAILS[normalized_stage]
    details = {
        "code": normalized_code,
        "stage": normalized_stage,
        "detail": safe_public_contract_message(
            public_planning_failure_message(exception, fallback=stage_detail),
            fallback=stage_detail,
        ),
        "exception_type": exception_type,
        "is_oom": detected_oom,
    }
    for name, value in (("segment", segment), ("window", window), ("step", step)):
        if not isinstance(value, Mapping):
            continue
        position = _position(
            value.get("current"), value.get("total"),
            variant=value.get("variant") if name == "segment" else None,
        )
        if position is not None:
            details[name] = position
    allocator_value = dict(allocator or {}) if detected_oom else {}
    if detected_oom and not allocator_value:
        allocator_value = safe_allocator_facts()
    safe_allocator = {
        key: value
        for key, value in allocator_value.items()
        if (
            key == "device_type" and value in {"cuda", "hip"}
        ) or (
            key in {
                "free_bytes", "total_bytes", "allocated_bytes", "reserved_bytes",
            }
            and type(value) is int and value >= 0
        )
    }
    if safe_allocator:
        details["allocator"] = safe_allocator
    return details


def oom_info_from_failure_details(
    details: Mapping[str, object],
    current_coefficient: float,
) -> Optional[dict]:
    """Return the legacy OOM banner shape from a confident safe envelope."""
    if details.get("is_oom") is not True:
        return None
    suggested = _suggest_lower_coefficient(current_coefficient)
    result = {
        "is_oom": True,
        "stage": str(details.get("stage") or "generation"),
        "current_coefficient": current_coefficient,
        "suggested_coefficient": suggested,
        "message": "The operation ran out of GPU memory.",
    }
    allocator = details.get("allocator")
    if isinstance(allocator, Mapping):
        result["allocator"] = dict(allocator)
    return result


def normalize_failure_details(
    value: Mapping[str, object],
    *,
    segment: Mapping[str, object] | None = None,
    window: Mapping[str, object] | None = None,
    step: Mapping[str, object] | None = None,
) -> dict:
    """Validate an internal producer envelope before remote publication."""
    stage = str(value.get("stage") or "generation")
    if stage not in _FAILURE_STAGES:
        stage = "generation"
    is_oom_value = value.get("is_oom") is True
    code = _honest_failure_code(
        str(value.get("code") or ""), is_oom=is_oom_value, stage=stage,
    )
    exception_type = str(value.get("exception_type") or "Exception")
    if _SAFE_TOKEN_RE.fullmatch(exception_type) is None:
        exception_type = "Exception"
    from services.planning_failure import safe_public_contract_message

    details = {
        "code": code,
        "stage": stage,
        "detail": safe_public_contract_message(
            value.get("detail"), fallback=_STAGE_DETAILS[stage],
        ),
        "exception_type": exception_type,
        "is_oom": is_oom_value,
    }
    for name, fallback in (
        ("segment", segment), ("window", window), ("step", step),
    ):
        raw = value.get(name)
        raw = raw if isinstance(raw, Mapping) else fallback
        if isinstance(raw, Mapping):
            position = _position(
                raw.get("current"), raw.get("total"),
                variant=raw.get("variant") if name == "segment" else None,
            )
            if position is not None:
                details[name] = position
    allocator = value.get("allocator")
    if is_oom_value and isinstance(allocator, Mapping):
        safe_allocator = {
            key: item
            for key, item in allocator.items()
            if (
                key == "device_type" and item in {"cuda", "hip"}
            ) or (
                key in {
                    "free_bytes", "total_bytes", "allocated_bytes", "reserved_bytes",
                }
                and type(item) is int and item >= 0
            )
        }
        if safe_allocator:
            details["allocator"] = safe_allocator
    return details


def detect_oom(exception: Exception, current_coefficient: float = 0.80) -> Optional[dict]:
    """Inspect an exception and return OOM info if it looks like a CUDA OOM.

    Returns None when the exception is not OOM-related. When OOM is
    detected, returns a dict suitable for JSON serialization:
      {
        "is_oom": True,
        "current_coefficient": 0.80,
        "suggested_coefficient": 0.70,  # or None if already at floor
        "message": "The operation ran out of GPU memory.",
      }

    The current_coefficient parameter should be sourced from
    server_config["vram_safety_coefficient"] at the call site so the
    UI can display the actual current value.
    """
    if not is_oom(exception):
        return None

    suggested = _suggest_lower_coefficient(current_coefficient)
    return {
        "is_oom": True,
        "current_coefficient": current_coefficient,
        "suggested_coefficient": suggested,
        # Raw exceptions can contain local paths, arguments, and credentials.
        # The machine-local traceback is retained by the caller.
        "message": "The operation ran out of GPU memory.",
    }


def delivery_oom_info(
    exception: Exception,
    current_coefficient: float,
    *,
    requested_target: str,
    native_available: bool,
    retry_count: int,
    actions: Iterable[str] = (),
) -> Optional[dict]:
    """Build path-free, remotely safe recovery facts for delivery OOMs.

    Delivery failures deliberately do not echo ``str(exception)``. ffmpeg,
    CUDA, and bridge exceptions may contain host paths; status responses are a
    remote multi-user surface. The retained native artifact is described only
    as an availability boolean and remains server-owned.
    """
    if not is_oom(exception):
        return None
    suggested = _suggest_lower_coefficient(current_coefficient)
    target = str(requested_target or "").strip().lower()
    if target not in {"1920x1080", "2688x1536", "3840x2160"}:
        target = ""
    return {
        "is_oom": True,
        "stage": "h3_delivery",
        "requested_target": target,
        "native_available": bool(native_available),
        "retry_count": max(0, min(1, int(retry_count))),
        "recoverable": bool(native_available),
        "actions": [
            str(action)[:64]
            for action in actions
            if isinstance(action, str) and action
        ][:8],
        "current_coefficient": current_coefficient,
        "suggested_coefficient": suggested,
        "message": (
            "Delivery ran out of GPU memory after native generation completed. "
            "The private native result is still available for recovery."
            if native_available
            else "Delivery ran out of GPU memory."
        ),
    }
