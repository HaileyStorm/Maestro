"""Capability-gated optional acceleration for the native MiniMax H3 path."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import importlib
import hashlib
import json
import logging
from pathlib import Path
import platform
import subprocess
import sys
import threading
from types import ModuleType
from typing import Any
from urllib.parse import unquote, urlparse

import torch


KIJAI_SOL_REPOSITORY = "https://github.com/Kijai/ComfyUI-SolAttn_triton.git"
KIJAI_SOL_REVISION = "0e334dc981cfe3b0ed926ee13ad43f64914b7f5b"
SAGEATTENTION_REPOSITORY = "https://github.com/thu-ml/SageAttention.git"
SAGEATTENTION_VERSION = "2.2.0"
SAGEATTENTION_REVISION = "eb615cf6cf4d221338033340ee2de1c37fbdba4a"
SAGE2_VALIDATION_RECORD = Path(__file__).with_name("h3_sage2_validation.json")
SAGE2_VALIDATION_RECORD_SHA256 = "e0ac9b6b415d8029f077bc6dc11e9b9e22f612405bf824b6c2694328dd802029"
SAGE2_BASE_MODEL_REVISION = "0543966fbdce5ba05709a8f2031c94bdba629b4a"
KIJAI_W4A8_REPOSITORY = "Kijai/MiniMax-H3-experimental"
KIJAI_W4A8_REVISION = "8b48334e6263a39b34eef85f9f5e271ba4506945"
from services.h3_w4a8_provenance import RUNTIME_REVISION as COMFY_KITCHEN_W4A8_REVISION
SOL_CHECKOUT = Path(__file__).with_name("sol_attn_kijai")
SAGEATTENTION_CHECKOUT = Path(__file__).with_name("sageattention_thu_ml")

_load_lock = threading.RLock()
_sol_kernel = None
_sol_error: str | None = None
_sage2_kernel = None
_sage2_error: str | None = None
_sage2_last_fallback_reason: str | None = None
_warned: set[str] = set()
_stats = {
    "sol_calls": 0,
    "sage2_calls": 0,
    "dense_policy": 0,
    "dense_fallback": 0,
    "sage2_fallback": 0,
    "errors": 0,
}


def _checkout_revision(checkout: Path = SOL_CHECKOUT) -> str | None:
    if not (checkout / ".git").is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _checkout_source_clean(checkout: Path) -> bool:
    try:
        status = subprocess.run(
            ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return status.returncode == 0 and not status.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False


def _sage2_distribution_provenance() -> tuple[str | None, Path | None, str | None]:
    try:
        distribution = importlib.metadata.distribution("sageattention")
        version = distribution.version
        direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
        parsed = urlparse(str(direct_url.get("url") or ""))
        source = None
        if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
            source = Path(unquote(parsed.path)).resolve()
        files = sorted(distribution.files or (), key=lambda item: str(item))
        prefix = Path(sys.prefix).resolve()
        digest = hashlib.sha256()
        for relative in files:
            path = Path(distribution.locate_file(relative)).resolve()
            if not path.is_file() or not path.is_relative_to(prefix):
                return version, source, None
            digest.update(str(relative).encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return version, source, digest.hexdigest() if files else None
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        return None, None, None


def _sage2_distribution_files() -> frozenset[Path]:
    try:
        distribution = importlib.metadata.distribution("sageattention")
        prefix = Path(sys.prefix).resolve()
        files = frozenset(
            Path(distribution.locate_file(relative)).resolve()
            for relative in (distribution.files or ())
        )
        if not files or any(not path.is_file() or not path.is_relative_to(prefix) for path in files):
            return frozenset()
        return files
    except (OSError, importlib.metadata.PackageNotFoundError):
        return frozenset()


def _sage2_validation_record_status(
    record_path: Path = SAGE2_VALIDATION_RECORD,
) -> dict[str, Any]:
    try:
        raw = record_path.read_bytes()
        record = json.loads(raw)
    except (OSError, ValueError):
        return {"passed": False, "reason": "release-bound Sage2 validation record is missing or invalid"}
    record_digest = hashlib.sha256(raw).hexdigest()
    if record_digest != SAGE2_VALIDATION_RECORD_SHA256:
        return {"passed": False, "reason": "release-bound Sage2 validation record hash mismatch"}
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        return {"passed": False, "reason": "release-bound Sage2 validation schema is unsupported"}

    installed_version, _installed_source, installed_digest = _sage2_distribution_provenance()
    engine = record.get("engine")
    expected_engine = {
        "repository": SAGEATTENTION_REPOSITORY,
        "version": SAGEATTENTION_VERSION,
        "revision": SAGEATTENTION_REVISION,
        "distribution_sha256": installed_digest,
    }
    if installed_version != SAGEATTENTION_VERSION or engine != expected_engine:
        return {"passed": False, "reason": "Sage2 validation does not match the installed source build"}

    try:
        runtime = {
            "torch": str(torch.__version__),
            "torch_cuda": str(getattr(torch.version, "cuda", "") or ""),
            "triton": importlib.metadata.version("triton"),
            "gpu": torch.cuda.get_device_name(0),
            "compute_capability": list(torch.cuda.get_device_capability(0)),
        }
    except Exception as error:
        return {"passed": False, "reason": f"could not bind Sage2 validation to this runtime: {error}"}
    if record.get("runtime") != runtime:
        return {"passed": False, "reason": "Sage2 validation does not match this Torch/CUDA/Triton/GPU runtime"}

    if record.get("scope") != {
        "model_types": ["minimax_h3"],
        "profiles": ["draft", "fast"],
        "evidence_resolutions": {
            "draft": "608x352",
            "fast": "864x480",
        },
        "frame_count": 124,
        "audio_required": True,
    }:
        return {"passed": False, "reason": "Sage2 validation scope is not the approved Base profile envelope"}
    if record.get("model") != {
        "repository": "Comfy-Org/MiniMax-H3",
        "revision": SAGE2_BASE_MODEL_REVISION,
        "checkpoint": "minimax_h3_fl2va_pruned_fp8_scaled.safetensors",
    }:
        return {"passed": False, "reason": "Sage2 validation is not bound to the approved Base checkpoint provenance"}

    try:
        from services.h3_turbo import (
            H3_TURBO_GRID_SHA256,
            H3_TURBO_LORA_SHA256,
            H3_TURBO_PROFILE_ID,
        )
    except Exception as error:
        return {"passed": False, "reason": f"could not inspect the validated H3 Turbo release: {error}"}
    if record.get("turbo") != {
        "profile_id": H3_TURBO_PROFILE_ID,
        "lora_sha256": H3_TURBO_LORA_SHA256,
        "grid_sha256": H3_TURBO_GRID_SHA256,
    }:
        return {"passed": False, "reason": "Sage2 validation does not match the managed H3 Turbo release"}

    execution = record.get("kernel_execution")
    if not isinstance(execution, dict) or not (
        execution.get("effective_engine") == "sage2"
        and int(execution.get("sage2_calls") or 0) >= 3100
        and execution.get("fallbacks") == 0
        and execution.get("errors") == 0
    ):
        return {"passed": False, "reason": "Sage2 validation lacks proven kernel execution without fallbacks/errors"}
    review = record.get("review")
    if not isinstance(review, dict) or not (
        review.get("method") == "explicit_human_visual_audio_review"
        and review.get("output_success_alone_sufficient") is False
        and review.get("visual_coherence") is True
        and review.get("audio_valid") is True
    ):
        return {"passed": False, "reason": "Sage2 validation lacks explicit visual/audio review"}

    cases = record.get("cases")
    required_cases = {
        "native_20": ("native", 20, 1, "608x352"),
        "turbo_4": ("turbo", 4, 2, "608x352"),
        "turbo_8": ("turbo", 8, 2, "608x352"),
        "fast_864_turbo_8": ("turbo", 8, 1, "864x480"),
    }
    if not isinstance(cases, dict):
        return {"passed": False, "reason": "Sage2 validation cases are missing"}
    for case_id, (accelerator, steps, samples, resolution) in required_cases.items():
        case = cases.get(case_id)
        ssim = case.get("representative_frame_ssim") if isinstance(case, dict) else None
        if not isinstance(case, dict) or not isinstance(ssim, dict) or not (
            case.get("passed") is True
            and case.get("accelerator") == accelerator
            and case.get("steps") == steps
            and int(case.get("samples") or 0) >= samples
            and case.get("resolution") == resolution
            and case.get("frame_count") == 124
            and case.get("audio_valid") is True
            and float(
                case.get("resident_seconds")
                or case.get("resident_generation_seconds")
                or 0
            ) > 0
            and float(
                case.get("baseline_seconds")
                or case.get("baseline_generation_seconds")
                or 0
            ) > 0
            and 0 <= float(ssim.get("minimum") or -1) <= float(ssim.get("maximum") or -1) <= 1
            and len(str(case.get("benchmark_spec_sha256") or "")) == 64
        ):
            return {"passed": False, "reason": f"Sage2 validation case {case_id} is incomplete"}
    fast = cases["fast_864_turbo_8"]
    fast_review = fast.get("visual_review")
    if not isinstance(fast_review, dict) or not (
        fast.get("baseline_model_load_state") == "cold"
        and fast.get("timing_comparable") is False
        and fast_review.get("coherent_start_middle_end") is True
        and fast_review.get("stable_robot_colors_and_motion") is True
        and fast_review.get("no_collapse_or_artifact") is True
    ):
        return {"passed": False, "reason": "Fast Sage2 validation misstates its cold baseline or visual review"}
    return {
        "passed": True,
        "reason": None,
        "recorded_at": record.get("recorded_at"),
        "record_sha256": record_digest,
        "validated_profiles": list(record["scope"]["profiles"]),
        "validated_model_types": ["minimax_h3"],
    }


def sage2_validation_status() -> dict[str, Any]:
    """Return the explicit release/runtime-bound Base validation gate."""
    available, reason, _revision = _sage2_capability()
    if not available:
        return {"passed": False, "reason": reason}
    return _sage2_validation_record_status()


def _load_sol_kernel():
    """Load Kijai's kernel without importing its ComfyUI-only package root."""
    global _sol_kernel, _sol_error
    with _load_lock:
        if _sol_kernel is not None:
            return _sol_kernel
        if _sol_error is not None:
            return None
        revision = _checkout_revision()
        if revision != KIJAI_SOL_REVISION:
            _sol_error = (
                "Pinned Kijai Sol-Attn checkout is missing"
                if revision is None
                else f"Kijai Sol-Attn revision mismatch ({revision[:12]})"
            )
            return None
        try:
            package_name = "_maestro_kijai_sol_attn"
            package = sys.modules.get(package_name)
            if package is None:
                package = ModuleType(package_name)
                package.__path__ = [str(SOL_CHECKOUT)]
                package.__package__ = package_name
                sys.modules[package_name] = package
            module_name = f"{package_name}._tri_fwd"
            spec = importlib.util.spec_from_file_location(
                module_name,
                SOL_CHECKOUT / "_tri_fwd.py",
                submodule_search_locations=None,
            )
            if spec is None or spec.loader is None:
                raise ImportError("could not build the Sol-Attn module spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            _sol_kernel = module.sol_attn
            return _sol_kernel
        except Exception as error:
            _sol_error = f"{type(error).__name__}: {error}"
            return None


def _cuda_version_tuple(value: Any) -> tuple[int, int]:
    try:
        major, minor, *_ = str(value or "").split(".")
        return int(major), int(minor)
    except (TypeError, ValueError):
        return (0, 0)


def _sage2_capability() -> tuple[bool, str, str | None]:
    revision = _checkout_revision(SAGEATTENTION_CHECKOUT)
    if platform.system() != "Linux":
        return False, "SageAttention2++ is source-built only on Linux; select Dense SDPA", revision
    if not torch.cuda.is_available():
        return False, "SageAttention2++ requires CUDA; select Dense SDPA", revision
    try:
        capability = tuple(torch.cuda.get_device_capability())
    except Exception as error:
        return False, f"could not inspect the CUDA device: {error}", revision
    if capability != (12, 0):
        return False, "H3 SageAttention2++ is currently gated to NVIDIA SM120", revision
    if _cuda_version_tuple(getattr(torch.version, "cuda", None)) < (12, 8):
        return False, "H3 SageAttention2++ on SM120 requires a CUDA 12.8+ PyTorch runtime", revision
    if revision != SAGEATTENTION_REVISION:
        reason = (
            "pinned official SageAttention checkout is missing"
            if revision is None
            else f"official SageAttention revision mismatch ({revision[:12]})"
        )
        return False, reason, revision
    if not _checkout_source_clean(SAGEATTENTION_CHECKOUT):
        return False, "official SageAttention checkout has local source changes; run Pinokio Update", revision
    marker_path = Path(sys.prefix) / ".maestro_h3_sage2.json"
    expected_marker = {
        "revision": SAGEATTENTION_REVISION,
        "version": SAGEATTENTION_VERSION,
        "torch": str(torch.__version__),
        "torch_cuda": str(getattr(torch.version, "cuda", "") or ""),
        "compute_capability": [12, 0],
    }
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "SageAttention2++ has not been source-built and verified; run Pinokio Update", revision
    if any(marker.get(key) != value for key, value in expected_marker.items()):
        return False, "SageAttention2++ build is stale for the current Torch/CUDA runtime; run Pinokio Update", revision
    installed_version, installed_source, installed_digest = _sage2_distribution_provenance()
    if installed_version is None:
        return False, "official SageAttention2++ source build is not installed", revision
    if installed_version != SAGEATTENTION_VERSION:
        return False, f"installed SageAttention version is {installed_version}, expected {SAGEATTENTION_VERSION}", revision
    if installed_source != SAGEATTENTION_CHECKOUT.resolve():
        return False, "installed SageAttention package did not come from the pinned official checkout", revision
    if installed_digest is None or marker.get("distribution_sha256") != installed_digest:
        return False, "installed SageAttention package hash does not match the verified build; run Pinokio Update", revision
    return True, "official v2.2.0 source build is installed for Linux SM120", revision


def _load_sage2_kernel():
    """Load the pinned official v2.2.0 dispatcher after capability checks."""
    global _sage2_kernel, _sage2_error
    with _load_lock:
        if _sage2_kernel is not None:
            return _sage2_kernel
        if _sage2_error is not None:
            return None
        available, reason, _revision = _sage2_capability()
        if not available:
            _sage2_error = reason
            return None
        try:
            module = importlib.import_module("sageattention")
            module_path = Path(str(getattr(module, "__file__", ""))).resolve()
            if module_path not in _sage2_distribution_files():
                raise ImportError(
                    "resolved sageattention module is outside the verified installed distribution"
                )
            kernel = getattr(module, "sageattn", None)
            if not callable(kernel):
                raise ImportError("verified sageattention package does not expose callable sageattn")
            _sage2_kernel = kernel
            return _sage2_kernel
        except Exception as error:
            _sage2_error = f"{type(error).__name__}: {error}"
            return None


def _sage2_fallback(reason: str) -> None:
    global _sage2_last_fallback_reason
    _sage2_last_fallback_reason = reason
    _stats["sage2_fallback"] += 1
    if reason not in _warned:
        _warned.add(reason)
        logging.warning("[MiniMax H3] SageAttention2++ unavailable for this call (%s); using dense SDPA", reason)


def _sage2_unavailable(reason: str, *, allow_sdpa_fallback: bool) -> None:
    global _sage2_last_fallback_reason
    if allow_sdpa_fallback:
        _sage2_fallback(reason)
        return
    _sage2_last_fallback_reason = reason
    _stats["errors"] += 1
    rejected = f"sage2-rejected: {reason}"
    if rejected not in _warned:
        _warned.add(rejected)
        logging.warning("[MiniMax H3] SageAttention2++ request rejected (%s)", reason)
    raise RuntimeError(
        f"MiniMax H3 SageAttention2++ cannot execute: {reason}. "
        "Select Dense SDPA explicitly to continue."
    )


def _w4a8_capability() -> tuple[bool, str]:
    from services.h3_w4a8_provenance import locate_pinned_package, marker_package_matches
    try:
        package_root, _digest = locate_pinned_package()
        import comfy_kitchen
        if Path(comfy_kitchen.__file__).parent.resolve() != package_root.resolve():
            return False, "W4A8 package location changed; run Pinokio Update"
    except Exception:
        return False, "The pinned W4A8 runtime is not installed; run Pinokio Update"
    if not all(callable(getattr(comfy_kitchen, name, None)) for name in (
        "quantize_w4a8_int8_weight", "w4a8_int8_linear",
    )):
        return False, "installed comfy-kitchen lacks merged asym_w4a8_int8 support"
    backends = comfy_kitchen.list_backends()
    triton_ready = bool((backends.get("triton") or {}).get("available"))
    eager_ready = bool((backends.get("eager") or {}).get("available"))
    if not (triton_ready or eager_ready):
        return False, "no comfy-kitchen W4A8 backend is available"
    marker_path = Path(sys.prefix) / ".maestro_h3_w4a8_validated.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if not marker_package_matches(marker, package_root):
            return False, "W4A8 package validation is stale; run Pinokio Update"
        import triton
        expected = {
            "runtime_revision": COMFY_KITCHEN_W4A8_REVISION,
            "gpu": torch.cuda.get_device_name(0),
            "compute_capability": list(torch.cuda.get_device_capability(0)),
            "torch": str(torch.__version__),
            "triton": str(triton.__version__),
        }
        if any(marker.get(key) != value for key, value in expected.items()):
            return False, "W4A8 validation is stale for the current GPU/runtime; run Pinokio Update"
    except Exception:
        return False, "W4A8 finite-output validation has not passed; run Pinokio Update"
    return True, "merged W4A8 runtime installed; base FL2VA only, opt-in"


def get_h3_acceleration_status(*, probe_kernel: bool = False) -> dict[str, Any]:
    revision = _checkout_revision()
    cuda = bool(torch.cuda.is_available())
    capability = torch.cuda.get_device_capability() if cuda else (0, 0)
    hardware_ok = cuda and capability[0] >= 8
    if probe_kernel and hardware_ok and revision == KIJAI_SOL_REVISION:
        _load_sol_kernel()
    sage2_runtime, sage2_reason, sage2_revision = _sage2_capability()
    sage2_validation = (
        _sage2_validation_record_status()
        if sage2_runtime
        else {"passed": False, "reason": sage2_reason}
    )
    if probe_kernel and sage2_runtime:
        _load_sage2_kernel()
    sage2_available = bool(sage2_runtime and (_sage2_kernel is not None or _sage2_error is None))
    if _sage2_error:
        sage2_reason = _sage2_error
    w4a8_runtime, w4a8_reason = _w4a8_capability()
    w4a8_available = bool(hardware_ok and w4a8_runtime)
    if w4a8_runtime and not hardware_ok:
        w4a8_reason = "W4A8 requires an NVIDIA SM80+ GPU"
    return {
        "dense_sdpa": {
            "available": True,
            "default": False,
            "quality": "lossless_reference_path",
        },
        "sol_attn": {
            "available": bool(
                hardware_ok
                and revision == KIJAI_SOL_REVISION
                and (_sol_kernel is not None or (not probe_kernel and _sol_error is None))
            ),
            "default": bool(hardware_ok and revision == KIJAI_SOL_REVISION),
            "approximate": True,
            "repository": KIJAI_SOL_REPOSITORY,
            "required_revision": KIJAI_SOL_REVISION,
            "installed_revision": revision,
            "hardware_ok": hardware_ok,
            "error": _sol_error,
        },
        "sage2": {
            "available": sage2_available,
            "default": False,
            "approximate": True,
            "validated": bool(sage2_runtime and sage2_validation.get("passed")),
            "repository": SAGEATTENTION_REPOSITORY,
            "version": SAGEATTENTION_VERSION,
            "required_revision": SAGEATTENTION_REVISION,
            "installed_revision": sage2_revision,
            "hardware_ok": bool(
                platform.system() == "Linux"
                and cuda
                and capability == (12, 0)
                and _cuda_version_tuple(getattr(torch.version, "cuda", None)) >= (12, 8)
            ),
            "reason": (
                "validated for Base Draft and Fast at their exact recorded geometries"
                if sage2_runtime and sage2_validation.get("passed")
                else sage2_reason
            ),
            "validation_reason": sage2_validation.get("reason"),
            "validation_record_sha256": sage2_validation.get("record_sha256"),
            "validated_profiles": sage2_validation.get("validated_profiles", []),
            "validated_model_types": sage2_validation.get("validated_model_types", []),
            "last_unavailable_reason": _sage2_last_fallback_reason,
            "model_status": {
                "minimax_h3": (
                    "validated_draft_fast_exact_geometries"
                    if sage2_validation.get("passed")
                    else "live_visual_validation_required"
                ),
                "minimax_h3_w4a8_fl2va": "structurally_reachable_unvalidated",
                "minimax_h3_pinkcherry_fl2va": "structurally_reachable_unvalidated",
                "minimax_h3_ref2va": "structurally_reachable_unvalidated",
            },
            "turbo_status": (
                "validated_base_draft_fast"
                if sage2_validation.get("passed")
                else "ready_for_live_4_8_validation"
            ),
        },
        "w4a8": {
            "available": w4a8_available,
            "default": False,
            "experimental": True,
            "repository": KIJAI_W4A8_REPOSITORY,
            "revision": KIJAI_W4A8_REVISION,
            "runtime_revision": COMFY_KITCHEN_W4A8_REVISION,
            "compatible_models": ["minimax_h3_w4a8_fl2va"],
            "conditioning_mode": "first_last_frames",
            "reason": w4a8_reason,
        },
        "stats": dict(_stats),
    }


def maybe_sage2_attention(
    query: torch.Tensor,
    key: torch.Tensor,
    value: torch.Tensor,
    *,
    attention_mask: torch.Tensor | None,
    tensor_layout: str,
    is_causal: bool,
    allow_sdpa_fallback: bool = False,
) -> torch.Tensor | None:
    """Return NHD SageAttention2++ output; optional callers may request SDPA fallback."""
    if tensor_layout != "NHD":
        _sage2_unavailable(
            f"unsupported tensor layout {tensor_layout!r}; H3 requires NHD",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    if is_causal:
        _sage2_unavailable(
            "the H3 SageAttention2++ seam is noncausal only",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    if attention_mask is not None:
        _sage2_unavailable(
            "packed padding masks are unsupported",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    if not all(torch.is_tensor(item) and item.ndim == 4 for item in (query, key, value)):
        _sage2_unavailable("Q/K/V must be rank-4 tensors", allow_sdpa_fallback=allow_sdpa_fallback)
        return None
    if query.shape != key.shape or query.shape != value.shape:
        _sage2_unavailable("Q/K/V shapes differ", allow_sdpa_fallback=allow_sdpa_fallback)
        return None
    if query.shape[-1] != 128:
        _sage2_unavailable(
            f"unsupported H3 head dimension {query.shape[-1]}; expected 128",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    if query.dtype not in (torch.bfloat16, torch.float16):
        _sage2_unavailable(
            f"unsupported dtype {query.dtype}; expected BF16 or FP16",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    if key.dtype != query.dtype or value.dtype != query.dtype:
        _sage2_unavailable("Q/K/V dtypes differ", allow_sdpa_fallback=allow_sdpa_fallback)
        return None
    if query.device.type != "cuda" or key.device != query.device or value.device != query.device:
        _sage2_unavailable(
            "Q/K/V must share one CUDA device", allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    try:
        capability = tuple(torch.cuda.get_device_capability(query.device))
    except Exception as error:
        _sage2_unavailable(
            f"could not inspect the CUDA device: {error}",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    if platform.system() != "Linux" or capability != (12, 0):
        _sage2_unavailable(
            "the validated build envelope is Linux NVIDIA SM120",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    if _cuda_version_tuple(getattr(torch.version, "cuda", None)) < (12, 8):
        _sage2_unavailable(
            "the SM120 build requires a CUDA 12.8+ PyTorch runtime",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    kernel = _load_sage2_kernel()
    if kernel is None:
        _sage2_unavailable(
            _sage2_error or "official SageAttention2++ kernel unavailable",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None
    try:
        output = kernel(
            query,
            key,
            value,
            tensor_layout="NHD",
            is_causal=False,
            return_lse=False,
        )
        if not torch.is_tensor(output) or output.shape != query.shape:
            raise RuntimeError("SageAttention2++ returned an invalid tensor shape")
        if output.device != query.device or output.dtype != query.dtype:
            raise RuntimeError("SageAttention2++ returned an invalid tensor device or dtype")
        _stats["sage2_calls"] += 1
        return output
    except Exception as error:
        _sage2_unavailable(
            f"{type(error).__name__}: {error}",
            allow_sdpa_fallback=allow_sdpa_fallback,
        )
        return None


def maybe_sol_attention(
    query: torch.Tensor,
    key: torch.Tensor,
    value: torch.Tensor,
    *,
    attention_mask: torch.Tensor | None,
    step_index: int,
    block_index: int,
    tau: float,
    dense_steps: int,
    dense_blocks: int,
    min_tokens: int,
    sink_tokens: int,
) -> torch.Tensor | None:
    """Return BTHD Sol output, or ``None`` for the exact dense fallback."""
    if step_index < max(0, int(dense_steps)) or block_index < max(0, int(dense_blocks)):
        _stats["dense_policy"] += 1
        return None
    eligible = (
        attention_mask is None
        and query.device.type == "cuda"
        and query.dtype == torch.bfloat16
        and query.shape == key.shape == value.shape
        and query.shape[-1] == 128
        and query.shape[1] >= max(64, int(min_tokens))
    )
    if not eligible:
        _stats["dense_fallback"] += 1
        return None
    kernel = _load_sol_kernel()
    if kernel is None:
        _stats["dense_fallback"] += 1
        reason = _sol_error or "Sol-Attn kernel unavailable"
        if reason not in _warned:
            _warned.add(reason)
            logging.warning("[MiniMax H3] %s; using dense SDPA", reason)
        return None
    sink_end = max(0, (int(sink_tokens) + 63) // 64)
    try:
        output = kernel(
            query,
            key,
            value,
            tau=float(tau),
            sink_blocks=(0, sink_end),
            sink_q=(0, sink_end),
            use_tma=False,
        )
        if output.shape != query.shape:
            raise RuntimeError("Sol-Attn returned an invalid tensor shape")
        _stats["sol_calls"] += 1
        return output
    except Exception as error:
        _stats["errors"] += 1
        message = f"{type(error).__name__}: {error}"
        if message not in _warned:
            _warned.add(message)
            logging.exception("[MiniMax H3] Sol-Attn failed; using dense SDPA")
        return None


__all__ = [
    "KIJAI_SOL_REPOSITORY",
    "KIJAI_SOL_REVISION",
    "SAGEATTENTION_REPOSITORY",
    "SAGEATTENTION_REVISION",
    "SAGEATTENTION_VERSION",
    "COMFY_KITCHEN_W4A8_REVISION",
    "get_h3_acceleration_status",
    "maybe_sage2_attention",
    "sage2_validation_status",
    "maybe_sol_attention",
]
