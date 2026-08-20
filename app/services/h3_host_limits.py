"""Host-local MiniMax H3 setup limits learned from true denoise failures.

When this computer cannot finish a quality after VRAM has been unwound,
that setup is recorded and withheld here. It is offered again only when
the capability epoch changes: GPU/runtime, a newly available attention
engine, or a VRAM-saving code revision.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .h3_oom_relief import H3_OOM_RELIEF_VERSION


HOST_LIMITS_SCHEMA = 1
# Bump this when H3 denoise/offload/attention code reduces VRAM use.
HOST_LIMITS_CODE_EPOCH = (
    f"h3-denoise-oom-relief-v{H3_OOM_RELIEF_VERSION}+postprocess-yield"
)
HOST_LIMITS_FILENAME = "h3_host_limits.json"
MAX_STATE_BYTES = 256 * 1024
MAX_SETUPS = 256
STEP_CEILING = 50
STEP_FLOOR = 2

USER_SETUP_UNSUPPORTED = (
    "This computer could not finish that MiniMax H3 quality. "
    "It will be offered again if a compatible attention mode or a "
    "memory-saving update comes online."
)

_lock = threading.RLock()


@dataclass(frozen=True)
class HostSetupDecision:
    runnable: bool
    reason: str | None
    max_steps: int | None


def default_store_path() -> Path:
    raw = str(os.environ.get("MAESTRO_H3_HOST_LIMITS_PATH") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1] / "storage" / HOST_LIMITS_FILENAME


def parse_resolution(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip().lower()
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    try:
        width = int(left)
        height = int(right)
    except (TypeError, ValueError):
        return None
    if width < 16 or height < 16:
        return None
    return width, height


def canonical_resolution(value: Any) -> str:
    parsed = parse_resolution(value)
    if parsed is None:
        return ""
    return f"{parsed[0]}x{parsed[1]}"


def orient_resolution(native: str, requested: str) -> str:
    """Keep the native pixel pair, matching the requested orientation."""
    profile = parse_resolution(native)
    current = parse_resolution(requested)
    if profile is None:
        return canonical_resolution(native)
    width, height = profile
    if current is None:
        return f"{width}x{height}"
    req_w, req_h = current
    if (req_h > req_w) != (height > width):
        width, height = height, width
    return f"{width}x{height}"


def _int_in_range(value: Any, *, low: int, high: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < low or number > high:
        return None
    return number


def _attention_engine(value: Any) -> str:
    text = str(value or "sdpa").strip().lower()
    if text in {"sdpa", "sol_attn", "sage2"}:
        return text
    return "sdpa"


def duration_bucket(video_length: Any = None, duration_seconds: Any = None) -> int | None:
    try:
        if duration_seconds not in (None, ""):
            seconds = int(round(float(duration_seconds)))
            if 1 <= seconds <= 3600:
                return seconds
    except (TypeError, ValueError):
        pass
    frames = _int_in_range(video_length, low=1, high=4096)
    if frames is None:
        return None
    return max(1, int(round(frames / 24.0)))


def setup_identity(
    *,
    model_type: Any,
    resolution: Any,
    video_length: Any,
    attention_engine: Any,
    duration_seconds: Any = None,
) -> dict[str, Any] | None:
    model = str(model_type or "").strip()
    if not model.startswith("minimax_h3"):
        return None
    canvas = canonical_resolution(resolution)
    seconds = duration_bucket(video_length, duration_seconds)
    if not canvas or seconds is None:
        return None
    return {
        "model_type": model,
        "resolution": canvas,
        "duration_seconds": seconds,
        "attention_engine": _attention_engine(attention_engine),
    }


def setup_key(identity: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(identity), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def hardware_snapshot() -> dict[str, Any]:
    gpu = "cpu"
    vram_mb = 0
    torch_version = ""
    cuda_version = ""
    try:
        import torch
        torch_version = str(getattr(torch, "__version__", "") or "")
        cuda_version = str(getattr(getattr(torch, "version", None), "cuda", "") or "")
        if torch.cuda.is_available():
            gpu = str(torch.cuda.get_device_name(0) or "cuda")
            try:
                props = torch.cuda.get_device_properties(0)
                vram_mb = int(getattr(props, "total_memory", 0) // (1024 * 1024))
            except Exception:
                vram_mb = 0
    except Exception:
        pass
    return {
        "gpu": gpu,
        "vram_mb": vram_mb,
        "torch": torch_version,
        "cuda": cuda_version,
    }


def attention_snapshot() -> dict[str, Any]:
    sol = False
    sage2 = False
    try:
        from services.h3_acceleration import get_h3_acceleration_status
        status = get_h3_acceleration_status(probe_kernel=False)
        sol = bool((status.get("sol_attn") or {}).get("available"))
        sage2 = bool((status.get("sage2") or {}).get("available"))
    except Exception:
        pass
    return {"sol_attn": sol, "sage2": sage2, "sdpa": True}


def current_epoch(
    *,
    hardware: Mapping[str, Any] | None = None,
    attention: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": HOST_LIMITS_CODE_EPOCH,
        "relief_version": int(H3_OOM_RELIEF_VERSION),
        "hardware": dict(hardware or hardware_snapshot()),
        "attention": dict(attention or attention_snapshot()),
    }


def epoch_token(epoch: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(epoch), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _empty_state(epoch: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": HOST_LIMITS_SCHEMA,
        "epoch_token": epoch_token(epoch),
        "epoch": dict(epoch),
        "setups": {},
    }


def _normalize_steps(values: Any) -> list[int]:
    result: list[int] = []
    if not isinstance(values, list):
        return result
    for item in values:
        number = _int_in_range(item, low=STEP_FLOOR, high=STEP_CEILING)
        if number is not None and number not in result:
            result.append(number)
    result.sort()
    return result


def _load_state(path: Path, epoch: Mapping[str, Any]) -> dict[str, Any]:
    empty = _empty_state(epoch)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return empty
    except OSError:
        return empty
    if not raw or len(raw) > MAX_STATE_BYTES:
        return empty
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return empty
    if not isinstance(payload, dict):
        return empty
    if int(payload.get("schema") or 0) != HOST_LIMITS_SCHEMA:
        return empty
    stored_token = str(payload.get("epoch_token") or "")
    if stored_token != epoch_token(epoch):
        # Capability changed: keep history but do not enforce stale denies.
        return empty
    setups = payload.get("setups")
    if not isinstance(setups, dict):
        return empty
    cleaned: dict[str, Any] = {}
    for key, record in setups.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            continue
        identity = record.get("identity")
        if not isinstance(identity, dict):
            continue
        cleaned[key] = {
            "identity": {
                "model_type": str(identity.get("model_type") or ""),
                "resolution": canonical_resolution(identity.get("resolution")),
                "duration_seconds": duration_bucket(
                    identity.get("video_length"),
                    identity.get("duration_seconds"),
                ) or 0,
                "attention_engine": _attention_engine(
                    identity.get("attention_engine"),
                ),
            },
            "denied_steps": _normalize_steps(record.get("denied_steps")),
            "completed_steps": _normalize_steps(record.get("completed_steps")),
        }
        if len(cleaned) >= MAX_SETUPS:
            break
    empty["setups"] = cleaned
    empty["epoch"] = dict(payload.get("epoch") or epoch)
    return empty


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".h3-host-limits-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _denied_floor(denied: list[int]) -> int | None:
    return min(denied) if denied else None


def steps_blocked(steps: int, denied: list[int], completed: list[int]) -> bool:
    if steps in completed:
        return False
    if steps in denied:
        return True
    floor = _denied_floor(denied)
    return floor is not None and steps >= floor


def max_runnable_steps(denied: list[int], completed: list[int]) -> int | None:
    for candidate in range(STEP_CEILING, STEP_FLOOR - 1, -1):
        if not steps_blocked(candidate, denied, completed):
            return candidate
    return None


def evaluate_setup(
    *,
    model_type: Any,
    resolution: Any,
    num_inference_steps: Any,
    video_length: Any = None,
    duration_seconds: Any = None,
    attention_engine: Any = None,
    path: Path | None = None,
    epoch: Mapping[str, Any] | None = None,
) -> HostSetupDecision:
    identity = setup_identity(
        model_type=model_type,
        resolution=resolution,
        video_length=video_length,
        duration_seconds=duration_seconds,
        attention_engine=attention_engine,
    )
    steps = _int_in_range(
        num_inference_steps, low=STEP_FLOOR, high=STEP_CEILING,
    )
    if identity is None or steps is None:
        return HostSetupDecision(True, None, STEP_CEILING)
    active_epoch = dict(epoch or current_epoch())
    state = _load_state(path or default_store_path(), active_epoch)
    record = state["setups"].get(setup_key(identity)) or {}
    denied = list(record.get("denied_steps") or [])
    completed = list(record.get("completed_steps") or [])
    ceiling = max_runnable_steps(denied, completed)
    if steps_blocked(steps, denied, completed):
        return HostSetupDecision(False, USER_SETUP_UNSUPPORTED, ceiling)
    return HostSetupDecision(True, None, ceiling)


def reason_if_blocked(**kwargs: Any) -> str | None:
    return evaluate_setup(**kwargs).reason


def is_true_denoise_limit(
    *,
    after_unwind: bool = True,
    step_now: Any = None,
) -> bool:
    """Return whether a failure is a mid-denoise host limit.

    Step-0, load, contended, and missing-progress OOMs must not persist a
    setup denial. A withheld quality is only honest when unwind already
    ran and denoising had a positive step index.
    """
    if not after_unwind:
        return False
    try:
        return step_now is not None and int(step_now) > 0
    except (TypeError, ValueError):
        return False


def record_denoise_failure(
    *,
    model_type: Any,
    resolution: Any,
    num_inference_steps: Any,
    video_length: Any = None,
    duration_seconds: Any = None,
    attention_engine: Any = None,
    after_unwind: bool = True,
    exhausted: bool = False,
    intent_steps: Any = None,
    intent_resolution: Any = None,
    step_now: Any = None,
    path: Path | None = None,
    epoch: Mapping[str, Any] | None = None,
) -> None:
    """Persist a true denoise limit. Step-0 / load / contended OOMs are ignored."""
    if not is_true_denoise_limit(after_unwind=after_unwind, step_now=step_now):
        return
    identity = setup_identity(
        model_type=model_type,
        resolution=resolution,
        video_length=video_length,
        duration_seconds=duration_seconds,
        attention_engine=attention_engine,
    )
    steps = _int_in_range(
        num_inference_steps, low=STEP_FLOOR, high=STEP_CEILING,
    )
    if identity is None or steps is None:
        return
    active_epoch = dict(epoch or current_epoch())
    store = path or default_store_path()
    with _lock:
        state = _load_state(store, active_epoch)
        key = setup_key(identity)
        record = state["setups"].setdefault(key, {
            "identity": identity,
            "denied_steps": [],
            "completed_steps": [],
        })
        denied = _normalize_steps(list(record.get("denied_steps") or []) + [steps])
        completed = [
            item for item in _normalize_steps(record.get("completed_steps"))
            if item not in denied
        ]
        if exhausted:
            intent = _int_in_range(
                intent_steps, low=STEP_FLOOR, high=STEP_CEILING,
            )
            if intent is not None:
                denied = _normalize_steps(denied + [intent])
            if intent_resolution:
                intent_identity = setup_identity(
                    model_type=model_type,
                    resolution=intent_resolution,
                    video_length=video_length,
                    duration_seconds=duration_seconds,
                    attention_engine=attention_engine,
                )
                if intent_identity is not None:
                    intent_key = setup_key(intent_identity)
                    intent_record = state["setups"].setdefault(intent_key, {
                        "identity": intent_identity,
                        "denied_steps": [],
                        "completed_steps": [],
                    })
                    extra = intent or steps
                    intent_record["denied_steps"] = _normalize_steps(
                        list(intent_record.get("denied_steps") or []) + [extra],
                    )
        record["denied_steps"] = denied
        record["completed_steps"] = completed
        record["identity"] = identity
        state["epoch"] = active_epoch
        state["epoch_token"] = epoch_token(active_epoch)
        _atomic_write(store, state)
    print(
        "[H3 host limit] Recorded "
        f"{identity['resolution']} / {steps} steps / "
        f"{identity['duration_seconds']}s / {identity['model_type']} / "
        f"{identity['attention_engine']}"
        f"{' (relief exhausted)' if exhausted else ''}"
    )


def record_denoise_success(
    *,
    model_type: Any,
    resolution: Any,
    num_inference_steps: Any,
    video_length: Any = None,
    duration_seconds: Any = None,
    attention_engine: Any = None,
    path: Path | None = None,
    epoch: Mapping[str, Any] | None = None,
) -> None:
    identity = setup_identity(
        model_type=model_type,
        resolution=resolution,
        video_length=video_length,
        duration_seconds=duration_seconds,
        attention_engine=attention_engine,
    )
    steps = _int_in_range(
        num_inference_steps, low=STEP_FLOOR, high=STEP_CEILING,
    )
    if identity is None or steps is None:
        return
    active_epoch = dict(epoch or current_epoch())
    store = path or default_store_path()
    with _lock:
        state = _load_state(store, active_epoch)
        key = setup_key(identity)
        record = state["setups"].setdefault(key, {
            "identity": identity,
            "denied_steps": [],
            "completed_steps": [],
        })
        completed = _normalize_steps(
            list(record.get("completed_steps") or []) + [steps],
        )
        denied = [
            item for item in _normalize_steps(record.get("denied_steps"))
            if item != steps
        ]
        record["completed_steps"] = completed
        record["denied_steps"] = denied
        record["identity"] = identity
        state["epoch"] = active_epoch
        state["epoch_token"] = epoch_token(active_epoch)
        _atomic_write(store, state)


def host_limit_reason_for_profile(
    settings: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    path: Path | None = None,
    epoch: Mapping[str, Any] | None = None,
) -> str | None:
    custom = settings.get("custom_settings")
    if not isinstance(custom, Mapping):
        custom = {}
    context_custom = context.get("custom_settings")
    if not isinstance(context_custom, Mapping):
        context_custom = {}
    oriented = orient_resolution(
        str(settings.get("resolution") or ""),
        str(context.get("resolution") or settings.get("resolution") or ""),
    )
    return reason_if_blocked(
        model_type=settings.get("model_type") or context.get("model_type"),
        resolution=oriented,
        num_inference_steps=settings.get("num_inference_steps"),
        video_length=context.get("video_length"),
        duration_seconds=context.get("duration_seconds"),
        attention_engine=custom.get("h3_attention_engine") or context_custom.get(
            "h3_attention_engine",
        ),
        path=path,
        epoch=epoch,
    )
