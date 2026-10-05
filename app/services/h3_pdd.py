"""Experimental H3 PDD contract; CPU evidence never authorizes native loading."""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import sys

from services.h3_runtime_binding import (
    _canonical, _hash_file, implementation_sha256, installed_runtime_versions,
)

PDD_PROFILE = "h3_pdd_8_v1"
PDD_SOURCE_COMMIT = "194cd36a40be631885d846f675d8d8dca1047cc1"
PDD_FILES = {
    "minimax_h3": "MiniMax-H3-FL2VA-Acc-8Step.safetensors",
    "minimax_h3_ref2va": "MiniMax-H3-Ref2VA-Acc-8Step.safetensors",
}
PDD_HEAD_KEYS = frozenset((
    "proj_out.weight", "proj_out.bias", "audio_proj_out.weight", "audio_proj_out.bias",
))
_MINT = object()


class H3PDDError(ValueError):
    pass


def _paths(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, (list, tuple)) or any(not isinstance(p, str) for p in value):
        raise H3PDDError("H3 PDD requires one explicit adapter.")
    return list(value)


def pdd_requested(custom_settings=None, activated_loras=None):
    settings = custom_settings if isinstance(custom_settings, dict) else {}
    paths = ([activated_loras] if isinstance(activated_loras, str)
             else activated_loras if isinstance(activated_loras, (list, tuple)) else ())
    return "h3_pdd_profile" in settings or any(
        isinstance(path, str) and Path(path).name in PDD_FILES.values() for path in paths
    )


def validate_pdd_request(*, model_type, custom_settings, activated_loras,
                         loras_multipliers, num_inference_steps,
                         skip_steps_cache_type=None, cumulative=False,
                         native_boundary=False, audio_prompt_type=""):
    settings = custom_settings if isinstance(custom_settings, dict) else {}
    if not pdd_requested(settings, activated_loras):
        return None
    if settings.get("h3_pdd_profile") != PDD_PROFILE or model_type not in PDD_FILES:
        raise H3PDDError("H3 PDD requires its experimental profile and matching native H3 family.")
    paths = _paths(activated_loras)
    if len(paths) != 1 or Path(paths[0]).name != PDD_FILES[model_type]:
        raise H3PDDError("H3 PDD requires its distinct FL2VA or Ref2VA adapter without stacking.")
    if type(num_inference_steps) is not int or num_inference_steps != 8:
        raise H3PDDError("H3 PDD requires exactly eight model evaluations.")
    if isinstance(loras_multipliers, (list, tuple)):
        if len(loras_multipliers) != 1:
            raise H3PDDError("H3 PDD requires one constant adapter strength.")
        loras_multipliers = loras_multipliers[0]
    try:
        if isinstance(loras_multipliers, bool):
            raise ValueError
        strength = float(loras_multipliers)
    except (TypeError, ValueError):
        raise H3PDDError("H3 PDD requires one constant adapter strength.") from None
    if not math.isfinite(strength) or not 0 <= strength <= 2:
        raise H3PDDError("H3 PDD strength must be constant and between zero and two.")
    incompatible = {
        "h3_turbo_profile", "h3_lightx2v_profile", "h3_spectrum_profile",
        "h3_source_audio_mode", "h3_native_boundary_conditioning",
        "h3_ref2va_handoff", "_h3_timeline_still_guide", "_h3_bridge_guides",
        "h3_video_shift", "h3_audio_shift", "sigmas", "audio_sigmas",
    }
    if (any(key in settings for key in incompatible) or skip_steps_cache_type
            or cumulative or native_boundary
            or (audio_prompt_type and (model_type != "minimax_h3_ref2va"
                                      or any(role not in "ABCK" for role in audio_prompt_type)))
            or settings.get("h3_attention_engine") != "sdpa"):
        raise H3PDDError("H3 PDD requires the paired native schedule without other accelerators or continuation.")
    return strength


def native_pdd_qualification_requirements():
    """The single remaining admission gate, not a caller-controlled override."""
    return {
        "profile": PDD_PROFILE, "source_commit": PDD_SOURCE_COMMIT,
        "families": dict(PDD_FILES), "evaluations": 8,
        "video_shift": 12.0, "audio_shift": 3.0,
        "required": ["pinned adapter size/SHA256 and tensor roster",
                     "exact family/base checkpoint integrity receipt",
                     "verified loaded H3RuntimeBinding",
                     "exact device/runtime qualification receipt"],
        "native_admitted": False,
    }


def enforce_pdd_runtime(**request):
    strength = validate_pdd_request(**request)
    if strength is None:
        return None
    # Neither upstream filenames nor a locally hashed tensor bank establish
    # that its exact base/artifact/device combination has been qualified.
    raise H3PDDError("Experimental H3 PDD is awaiting exact artifact and device qualification.")


@dataclass(frozen=True)
class H3PDDAdmission:
    family: str
    strength: float
    artifact: object
    header_sha256: str
    runtime_sha256: str
    _mint: object

    def verify(self, transformer=None, *, device=None):
        if self._mint is not _MINT:
            raise H3PDDError("H3 PDD admission was not issued by the runtime.")
        self.artifact.verify()
        if device is not None and str(device).split(":", 1)[0] != "cpu":
            raise H3PDDError("CPU PDD qualification cannot authorize a native execution device.")
        if transformer is not None and any(p.device.type != "cpu" for p in transformer.parameters()):
            raise H3PDDError("CPU PDD qualification cannot authorize a native device.")
        if self.runtime_sha256 != _implementation_digest():
            raise H3PDDError("H3 PDD loaded implementation changed.")
        return self

    def identity(self):
        self.verify()
        return hashlib.sha256(_canonical({
            "profile": PDD_PROFILE, "family": self.family, "strength": self.strength,
            "artifact": {"size": self.artifact.size, "sha256": self.artifact.sha256},
            "header": self.header_sha256, "implementation": self.runtime_sha256,
            "schedule": {"evaluations": 8, "intervals": 32, "block": 4,
                         "video_shift": 12.0, "audio_shift": 3.0},
            "evidence": "cpu_only",
        })).hexdigest()

    def __reduce__(self):
        raise TypeError("H3 PDD admissions are process-local execution evidence.")


def _implementation_digest():
    modules = [sys.modules[__name__]] + [
        importlib.import_module("models.minimax_h3." + name)
        for name in ("pdd", "transformer", "lora_affine", "scheduler", "minimax_h3_main")
    ]
    return hashlib.sha256(_canonical({
        "implementation": implementation_sha256(modules),
        "packages": installed_runtime_versions(),
    })).hexdigest()


def qualify_cpu_pdd(path, *, family, strength=1.0, abort_check=None):
    """Seal a local test artifact; this cannot open the native admission gate."""
    if family not in PDD_FILES or Path(path).name != PDD_FILES[family]:
        raise H3PDDError("H3 PDD artifact family does not match the selected model.")
    validate_pdd_request(model_type=family, custom_settings={"h3_pdd_profile": PDD_PROFILE, "h3_attention_engine": "sdpa"},
                         activated_loras=[str(path)], loras_multipliers=strength,
                         num_inference_steps=8)
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise H3PDDError("H3 PDD artifact must be a regular, unlinked owner file.")
    if os.name == "posix" and info.st_uid != os.geteuid():
        raise H3PDDError("H3 PDD artifact owner does not match.")
    evidence = _hash_file(path, abort_check)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        raw_size = source.read(8)
        if len(raw_size) != 8:
            raise H3PDDError("H3 PDD tensor header is missing.")
        size = struct.unpack("<Q", raw_size)[0]
        if not 2 <= size <= 16 * 1024 * 1024:
            raise H3PDDError("H3 PDD tensor header is not bounded.")
        raw = source.read(size)
    try:
        header = json.loads(raw)
        metadata = header.pop("__metadata__", {})
        if metadata.get("pdd_num_steps") != "32" or metadata.get("pdd_block_size") != "4":
            raise ValueError
        for key in PDD_HEAD_KEYS:
            tensor = header[key]
            shape = tensor["shape"]
            if len(shape) != (3 if key.endswith("weight") else 2) or shape[0] != 32:
                raise ValueError
            if tensor["dtype"] not in ("BF16", "F32"):
                raise ValueError
        backbone = set(header) - PDD_HEAD_KEYS
        if not backbone or any(not k.endswith((".lora_down", ".lora_up")) for k in backbone):
            raise ValueError
        for key in backbone:
            companion = key.replace(".lora_down", ".lora_up") if key.endswith(".lora_down") else key.replace(".lora_up", ".lora_down")
            if companion not in backbone or len(header[key]["shape"]) != 2:
                raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise H3PDDError("H3 PDD tensor roster is incompatible.") from None
    evidence.verify()
    return H3PDDAdmission(family, float(strength), evidence,
                          hashlib.sha256(raw).hexdigest(), _implementation_digest(), _MINT)


def prepare_pdd_runtime(transformer, admission, *, device=None):
    clear_pdd_runtime(transformer)
    if type(admission) is not H3PDDAdmission:
        raise H3PDDError("H3 PDD requires exact runtime-issued admission.")
    admission.verify(transformer, device=device)
    if getattr(transformer, "_h3_turbo_prepared", False):
        raise H3PDDError("H3 PDD cannot share a managed adapter stack.")
    object.__setattr__(transformer, "_h3_pdd_admission", admission)
    object.__setattr__(transformer, "_h3_pdd_backbone_seen", False)


def preprocess_pdd_backbone(transformer, model_type, state_dict):
    admission = transformer._h3_pdd_admission
    admission.verify(transformer)
    if model_type != admission.family or transformer._h3_pdd_backbone_seen:
        raise H3PDDError("H3 PDD backbone must be loaded exactly once for its admitted family.")
    from models.minimax_h3.pdd import validate_head_banks
    validate_head_banks(state_dict, transformer.final_layer)
    backbone = {}
    for key, value in state_dict.items():
        if key in PDD_HEAD_KEYS:
            continue
        if key.endswith(".lora_down"):
            target = key.replace(".lora_down", ".lora_A.weight")
        elif key.endswith(".lora_up"):
            target = key.replace(".lora_up", ".lora_B.weight")
        else:
            raise H3PDDError("H3 PDD backbone contains an unsupported tensor.")
        backbone[target] = value
    normalized = transformer._preprocess_ordinary_lora(model_type, backbone)
    object.__setattr__(transformer, "_h3_pdd_backbone_seen", True)
    return normalized


def activate_pdd_runtime(transformer):
    admission = getattr(transformer, "_h3_pdd_admission", None)
    if type(admission) is not H3PDDAdmission or not getattr(transformer, "_h3_pdd_backbone_seen", False):
        raise H3PDDError("H3 PDD backbone was not consumed by the adapter loader.")
    admission.verify(transformer)
    from safetensors.torch import load_file
    from models.minimax_h3.pdd import install_pdd_heads
    try:
        state = load_file(str(admission.artifact.resolved), device="cpu")
        admission.verify(transformer)
        controller = install_pdd_heads(transformer, state, strength=admission.strength)
        object.__setattr__(transformer, "_h3_pdd_identity", admission.identity())
        return controller
    except BaseException:
        clear_pdd_runtime(transformer)
        raise


def clear_pdd_runtime(transformer):
    if transformer is None:
        return
    controller = getattr(transformer, "_h3_pdd_controller", None)
    if controller is not None:
        controller.restore()
    for name in ("_h3_pdd_admission", "_h3_pdd_backbone_seen", "_h3_pdd_identity", "_h3_pdd_controller"):
        if hasattr(transformer, name):
            object.__delattr__(transformer, name)


def pdd_generation_cleanup(generate):
    @wraps(generate)
    def execute(self, *args, **kwargs):
        transformer = getattr(self, "transformer", None)
        try:
            return generate(self, *args, **kwargs)
        finally:
            if getattr(transformer, "_h3_pdd_admission", None) is not None:
                clear_pdd_runtime(transformer)
    return execute
