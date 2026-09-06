"""MiniMax H3 LoRA architecture contracts and per-shot selection.

Ordinary user LoRAs can be affine-adapted across FL2VA and Ref2VA, so they
may appear in both pickers. Named experiment and accelerator files stay on
their authored architecture. Exclusive files (Dasiwa, Turbo SLA) cannot
share a picker with another LoRA; they may still coexist across FL and Ref
shots because each shot loads only its own list.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from services.h3_dasiwa import (
    BETTER_MOTION_FILENAME,
    DASIWA_FILENAME,
    H3ExperimentCompatibilityError,
    validate_dasiwa_request,
)


H3_FL2VA_MODELS: Final[frozenset[str]] = frozenset({
    "minimax_h3",
    "minimax_h3_pinkcherry_fl2va",
    "minimax_h3_w4a8_fl2va",
})
H3_REF2VA_MODELS: Final[frozenset[str]] = frozenset({
    "minimax_h3_ref2va",
})
H3_LORA_ARCHITECTURES: Final[dict[str, str]] = {
    "minimax_h3": "fl2va",
    "minimax_h3_pinkcherry_fl2va": "fl2va",
    "minimax_h3_w4a8_fl2va": "fl2va",
    "minimax_h3_ref2va": "ref2va",
}
_SLA_TURBO_FILENAME = "minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors"

_KNOWN: Final[dict[str, dict[str, Any]]] = {
    DASIWA_FILENAME: {
        "architectures": frozenset({"ref2va"}),
        "exclusive_stack": True,
        "kind": "experiment",
        "label": "Dasiwa",
    },
    BETTER_MOTION_FILENAME: {
        "architectures": frozenset({"ref2va"}),
        "exclusive_stack": False,
        "kind": "experiment",
        "label": "Better Motion",
    },
    _SLA_TURBO_FILENAME: {
        "architectures": frozenset({"fl2va"}),
        "exclusive_stack": True,
        "kind": "accelerator",
        "label": "Turbo SLA",
    },
}
_ORDINARY = {
    "architectures": frozenset({"fl2va", "ref2va"}),
    "exclusive_stack": False,
    "kind": "ordinary",
    "label": "ordinary",
}


def _filename(value: object) -> str:
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1]


def h3_lora_contract(filename: object) -> dict[str, Any]:
    """Return the authored architecture contract for one LoRA filename."""
    known = _KNOWN.get(_filename(filename))
    return dict(known or _ORDINARY)


def public_h3_lora_contract(filename: object) -> dict[str, Any]:
    """JSON-safe LoRA contract for picker and details responses."""
    contract = h3_lora_contract(filename)
    return {
        "h3_architectures": sorted(contract["architectures"]),
        "h3_exclusive_stack": bool(contract["exclusive_stack"]),
        "h3_kind": str(contract["kind"]),
    }


def architecture_for_h3_model(model_type: object) -> str | None:
    """Return fl2va, ref2va, or None for non-H3 models."""
    return H3_LORA_ARCHITECTURES.get(str(model_type or ""))






def _activated_pairs(activated_loras: object, multipliers: object) -> list[tuple[str, str]]:
    if activated_loras is None or activated_loras == "":
        names = []
    elif isinstance(activated_loras, str):
        names = [activated_loras]
    elif isinstance(activated_loras, (list, tuple)):
        names = list(activated_loras)
    else:
        raise ValueError("MiniMax H3 LoRA selections must be a list of asset names.")
    if any(type(name) is not str or not name.strip() for name in names):
        raise ValueError("MiniMax H3 LoRA selections require non-empty asset names.")
    if len(set(names)) != len(names):
        raise ValueError("MiniMax H3 LoRA selections must not contain duplicates.")
    if multipliers is not None and not isinstance(multipliers, str):
        raise ValueError("MiniMax H3 LoRA multipliers must be text.")
    weights = (multipliers or "").split()
    if len(weights) > len(names):
        raise ValueError("MiniMax H3 LoRA multipliers exceed the selected asset count.")
    # Basenames classify compatibility only; retain the selected asset identity.
    return [(name, weights[index] if index < len(weights) else "1.00")
            for index, name in enumerate(names)]


def loras_for_h3_model(
    *,
    model_type: object,
    activated_loras: object,
    loras_multipliers: object = "",
) -> tuple[list[str], str]:
    """Keep only LoRAs authored for this checkpoint's architecture."""
    architecture = architecture_for_h3_model(model_type)
    if architecture is None:
        pairs = _activated_pairs(activated_loras, loras_multipliers)
        return [name for name, _weight in pairs], " ".join(
            weight for _name, weight in pairs
        )
    kept = [
        (name, weight)
        for name, weight in _activated_pairs(activated_loras, loras_multipliers)
        if architecture in h3_lora_contract(name)["architectures"]
    ]
    return [name for name, _weight in kept], " ".join(
        weight for _name, weight in kept
    )


def h3_request_loras_for_model(
    body: Mapping[str, Any],
    model_type: object,
) -> tuple[list[str], str]:
    """Absent/null split lists inherit shared choices; an empty list clears one side."""
    architecture = architecture_for_h3_model(model_type)
    for side in ("fl2va", "ref2va"):
        value = body.get(f"h3_{side}_loras")
        if value is not None and not isinstance(value, (list, tuple)):
            raise ValueError("MiniMax H3 architecture LoRA selections must be lists.")
    if architecture and body.get(f"h3_{architecture}_loras") is not None:
        pairs = _activated_pairs(body[f"h3_{architecture}_loras"],
                                 body.get(f"h3_{architecture}_loras_multipliers"))
        if any(architecture not in h3_lora_contract(name)["architectures"] for name, _ in pairs):
            raise ValueError("MiniMax H3 LoRA selection is incompatible with its architecture.")
        return [name for name, _ in pairs], " ".join(weight for _, weight in pairs)
    return loras_for_h3_model(
        model_type=model_type,
        activated_loras=body.get("activated_loras"),
        loras_multipliers=body.get("loras_multipliers"),
    )


def h3_lora_block_reason(
    filename: object,
    *,
    architecture: object,
    activated_loras: object = (),
) -> str | None:
    """Return why this LoRA cannot join one architecture picker, or None."""
    name = _filename(filename)
    if not name:
        return None
    contract = h3_lora_contract(name)
    wanted = str(architecture or "")
    if wanted and wanted not in contract["architectures"]:
        return f"{contract['label']} is not compatible with {wanted}."
    if isinstance(activated_loras, str):
        activated_values = [activated_loras]
    elif isinstance(activated_loras, (list, tuple)):
        activated_values = list(activated_loras)
    else:
        activated_values = []
    activated = [
        _filename(item) for item in activated_values if str(item or "").strip()
    ]
    if name in activated:
        return None
    exclusive = [
        item for item in activated
        if h3_lora_contract(item)["exclusive_stack"]
    ]
    if exclusive:
        label = h3_lora_contract(exclusive[0])["label"]
        return f"{label} cannot be stacked with another LoRA or accelerator"
    if contract["exclusive_stack"] and activated:
        return (
            f"{contract['label']} cannot be stacked with another LoRA or "
            "accelerator"
        )
    return None


def validate_h3_lora_selection(
    *,
    model_type: object,
    activated_loras: object = (),
    loras_multipliers: object = "",
    num_inference_steps: object = None,
    custom_settings: Mapping[str, Any] | None = None,
    skip_steps_cache_type: object = "",
    planned_model_types: Sequence[str] | None = None,
    fl2va_loras: object = None,
    fl2va_loras_multipliers: object = "",
    ref2va_loras: object = None,
    ref2va_loras_multipliers: object = "",
) -> None:
    """Reject LoRA sets the split pickers should have made unselectable."""
    planned = [
        str(item or "")
        for item in (planned_model_types or [str(model_type or "")])
    ]
    if not planned:
        planned = [str(model_type or "")]
    request = {
        "activated_loras": activated_loras,
        "loras_multipliers": loras_multipliers,
        "h3_fl2va_loras": fl2va_loras,
        "h3_fl2va_loras_multipliers": fl2va_loras_multipliers,
        "h3_ref2va_loras": ref2va_loras,
        "h3_ref2va_loras_multipliers": ref2va_loras_multipliers,
    }
    planned_architectures = {
        architecture_for_h3_model(value) for value in planned
        if architecture_for_h3_model(value) is not None
    }
    if any(request.get(f"h3_{side}_loras") is None for side in planned_architectures):
        for name, _weight in _activated_pairs(activated_loras, loras_multipliers):
            if not planned_architectures.intersection(h3_lora_contract(name)["architectures"]):
                raise ValueError("MiniMax H3 LoRA selection is incompatible with the planned models.")
    for segment_model in planned:
        names, multipliers = h3_request_loras_for_model(request, segment_model)
        exclusive = [name for name in names if h3_lora_contract(name)["exclusive_stack"]]
        if exclusive and len(names) > 1:
            label = h3_lora_contract(exclusive[0])["label"]
            raise ValueError(f"MiniMax H3 LoRA: {label} cannot be stacked with another LoRA or accelerator")
        try:
            validate_dasiwa_request(
                model_types=[segment_model],
                activated_loras=[_filename(name) for name in names],
                loras_multipliers=multipliers,
                num_inference_steps=num_inference_steps,
                custom_settings=custom_settings,
                skip_steps_cache_type=skip_steps_cache_type,
            )
        except H3ExperimentCompatibilityError as error:
            raise ValueError(str(error)) from error


__all__ = [
    "architecture_for_h3_model",
    "h3_lora_block_reason",
    "h3_lora_contract",
    "h3_request_loras_for_model",
    "loras_for_h3_model",
    "public_h3_lora_contract",
    "validate_h3_lora_selection",
]
