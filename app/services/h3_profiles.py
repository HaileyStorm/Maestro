"""Curated, non-locking MiniMax H3 performance setting bundles."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping


H3_NATIVE_RESOLUTIONS = (
    "1344x768", "768x1344", "1024x768", "768x1024", "768x768",
    "1152x640", "640x1152", "960x544", "544x960", "864x480",
    "480x864", "640x640", "608x352", "352x608",
)

DEFAULT_H3_PROFILE_ID = "high"

_PROFILES = (
    {
        "id": "draft",
        "label": "Draft",
        "description": "Minimum native canvas, four Turbo steps, and validated Sage2 on Base H3.",
        "accelerator": "turbo",
        "num_inference_steps": 4,
        "resolution": "608x352",
        "attention_engine": "sage2",
    },
    {
        "id": "fast",
        "label": "Fast",
        "description": "A larger working canvas with eight Turbo steps and release-validated Sage2 on Base H3.",
        "accelerator": "turbo",
        "num_inference_steps": 8,
        "resolution": "864x480",
        "attention_engine": "sage2",
    },
    {
        "id": "quality",
        "label": "Quality",
        "description": "Native H3 at its standard 20-step quality setting.",
        "accelerator": "native",
        "num_inference_steps": 20,
        "resolution": "960x544",
        "attention_engine": "sol_attn",
    },
    {
        "id": "high",
        "label": "High",
        "description": (
            "Maximum native H3 canvas at 20 steps with Sol attention; "
            "no learned upscale or delivery crop."
        ),
        "accelerator": "native",
        "num_inference_steps": 20,
        "resolution": "1344x768",
        "attention_engine": "sol_attn",
    },
    {
        "id": "spectrum_experimental",
        "label": "Spectrum Experimental",
        "description": (
            "Experimental clean-room H3 forecast accelerator: the High 1344x768 "
            "20-step Sol bundle captures 11 paired hidden-feature anchors and nine "
            "causal forecast slots, then replays every step without transformer blocks. "
            "Audio uses local interpolation only; "
            "quality and speed still require live validation."
        ),
        "accelerator": "spectrum",
        "num_inference_steps": 20,
        "resolution": "1344x768",
        "attention_engine": "sol_attn",
    },
    {
        "id": "lightx2v_experimental",
        "label": "LightX2V Experimental",
        "description": (
            "Manual Base FL2VA four-evaluation adapter at 608x352 with Dense SDPA. "
            "It is separate from Draft/Fast and requires its pinned managed asset."
        ),
        "accelerator": "lightx2v",
        "num_inference_steps": 4,
        "resolution": "608x352",
        "attention_engine": "sdpa",
    },
    {
        "id": "1080p_delivery",
        "label": "1080p Delivery",
        "description": (
            "The same 1344x768 native, 20-step Sol inference as High; "
            "FlashVSR 1.5x reaches 2016x1152, then a small center crop/downsample delivers "
            "exact 1920x1080. The added time/download is delivery-only."
        ),
        "accelerator": "native",
        "num_inference_steps": 20,
        "resolution": "1344x768",
        "attention_engine": "sol_attn",
        "spatial_upsampling": "flashvsr1.5",
        "delivery_resolution": "1920x1080",
        "delivery_fit": "center_crop",
    },
    {
        "id": "ultra",
        "label": "Ultra",
        "description": (
            "1344x768 native H3 with dense attention and 30 steps, then "
            "FlashVSR shifted two-pass 2x delivery at exactly 2688x1536 (2.7K, not 4K)."
        ),
        "accelerator": "native",
        "num_inference_steps": 30,
        "resolution": "1344x768",
        "attention_engine": "sdpa",
        "spatial_upsampling": "flashvsr2pass2",
        "delivery_resolution": "2688x1536",
        "delivery_fit": "upscale_exact",
    },
    {
        "id": "4k_delivery",
        "label": "4K Delivery",
        "description": (
            "1344x768 native H3 with dense attention and 30 steps; learned "
            "FlashVSR 3x detail synthesis reaches 4032x2304, then an explicit "
            "center crop/downsample delivers exact 3840x2160. Upscaled, not native 4K."
        ),
        "accelerator": "native",
        "num_inference_steps": 30,
        "resolution": "1344x768",
        "attention_engine": "sdpa",
        "spatial_upsampling": "flashvsr3",
        "delivery_resolution": "3840x2160",
        "delivery_fit": "center_crop",
    },
)


def profile_definitions() -> list[dict[str, Any]]:
    return deepcopy(list(_PROFILES))


def profile_definition(profile_id: str) -> dict[str, Any]:
    """Return one curated profile definition by stable id."""
    for definition in _PROFILES:
        if definition["id"] == profile_id:
            return deepcopy(definition)
    raise KeyError(f"Unknown MiniMax H3 performance profile: {profile_id}")


def profile_settings(
    model_type: str,
    profile_id: str = DEFAULT_H3_PROFILE_ID,
) -> dict[str, Any]:
    """Build the editable setting bundle for one selected H3 checkpoint.

    This is the single authority used by both the profile API and H3's
    omitted/fresh defaults.  Policy, prompts, references, and routing are
    intentionally outside the bundle.
    """
    definition = profile_definition(profile_id)
    attention_engine = definition["attention_engine"]
    # The release-bound Sage evidence covers only Base FL2VA. Other H3
    # checkpoints retain their conservative dense setting even when the user
    # chooses the same speed/geometry bundle.
    if attention_engine == "sage2" and model_type != "minimax_h3":
        attention_engine = "sdpa"
    settings = {
        "model_type": str(model_type or "minimax_h3"),
        "num_inference_steps": definition["num_inference_steps"],
        "resolution": definition["resolution"],
        "custom_settings": {
            "h3_attention_engine": attention_engine,
        },
        "tea_cache": 0,
        "activated_loras": [],
        "loras_multipliers": "",
        "lora_weights": {},
        "spatial_upsampling": str(definition.get("spatial_upsampling") or ""),
        "delivery_resolution": str(definition.get("delivery_resolution") or ""),
        "delivery_fit": str(definition.get("delivery_fit") or ""),
    }
    if definition["accelerator"] == "turbo":
        settings["custom_settings"]["h3_turbo_profile"] = "h3_turbo_v4"
    elif definition["accelerator"] == "spectrum":
        settings["custom_settings"]["h3_spectrum_profile"] = "spectrum_h3_v1"
    elif definition["accelerator"] == "lightx2v":
        settings["custom_settings"]["h3_lightx2v_profile"] = "h3_lightx2v_fl2v_4_v1"
    return settings


def default_profile_settings(model_type: str = "minimax_h3") -> dict[str, Any]:
    """Return H3's fresh/omitted High bundle for ``model_type``."""
    return profile_settings(model_type, DEFAULT_H3_PROFILE_ID)


def _reference_count(reference_shape: Mapping[str, Any]) -> int:
    total = int(bool(reference_shape.get("has_start")))
    total += int(bool(reference_shape.get("has_end")))
    for key in ("image_count", "video_count", "audio_count"):
        try:
            total += max(0, int(reference_shape.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return total


def _sage2_base_context_reason(
    context: Mapping[str, Any],
    reference_shape: Mapping[str, Any],
) -> str | None:
    if str(context.get("model_type") or "minimax_h3") != "minimax_h3":
        return "SageAttention2++ profiles are validated only for Base H3."
    if any(int(reference_shape.get(key) or 0) > 0 for key in (
        "image_count", "video_count", "audio_count",
    )):
        return "SageAttention2++ profiles cannot use semantic routing to Ref2VA."
    segments = context.get("_segment_contexts")
    if isinstance(segments, list) and any(
        not isinstance(segment, Mapping)
        or str(segment.get("model_type") or "") != "minimax_h3"
        for segment in segments
    ):
        return "SageAttention2++ profiles require every planned segment to remain on Base H3."
    return None


def _spectrum_context_reason(
    context: Mapping[str, Any],
    reference_shape: Mapping[str, Any],
) -> str | None:
    if str(context.get("model_type") or "minimax_h3") != "minimax_h3":
        return "Spectrum Experimental currently supports only MiniMax H3 Base FL2VA."
    if any(int(reference_shape.get(key) or 0) > 0 for key in (
        "image_count", "video_count", "audio_count",
    )):
        return "Spectrum Experimental cannot use semantic routing to Ref2VA."
    segments = context.get("_segment_contexts")
    if isinstance(segments, list) and any(
        not isinstance(segment, Mapping)
        or str(segment.get("model_type") or "") != "minimax_h3"
        for segment in segments
    ):
        return "Spectrum Experimental requires every planned segment to remain on Base FL2VA."
    return None


def build_profile_options(
    context: Mapping[str, Any],
    *,
    model_exists: Callable[[str], bool],
    model_downloaded: Callable[[str], bool],
    turbo_status: Mapping[str, Any] | None = None,
    turbo_compatibility: Callable[[Mapping[str, Any]], tuple[bool, str | None]] | None = None,
    spectrum_compatibility: Callable[[Mapping[str, Any]], tuple[bool, str | None]] | None = None,
    lightx2v_status: Mapping[str, Any] | None = None,
    lightx2v_compatibility: Callable[[Mapping[str, Any]], tuple[bool, str | None]] | None = None,
    sage2_status: Mapping[str, Any] | None = None,
    upscale_status: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return editable bundles without mutating policy or reference controls."""
    selected = str(context.get("model_type") or "minimax_h3")
    reference_shape = context.get("reference_shape")
    if not isinstance(reference_shape, Mapping):
        reference_shape = {}
    turbo_status = dict(turbo_status or {})
    sage2_status = dict(sage2_status or {})
    upscale_status = dict(upscale_status or {})
    lightx2v_status = dict(lightx2v_status or {})
    turbo_registered = bool(turbo_status.get("registered"))
    result: list[dict[str, Any]] = []
    for definition in profile_definitions():
        turbo = definition["accelerator"] == "turbo"
        spectrum = definition["accelerator"] == "spectrum"
        lightx2v = definition["accelerator"] == "lightx2v"
        available = True
        reason = None
        # Profiles tune the selected checkpoint. Checkpoint/conditioning
        # eligibility belongs to the runtime compatibility validator.
        model_type = selected
        if turbo:
            if not turbo_registered:
                available = False
                reason = "Turbo adapter support is not registered in this Maestro installation yet."

        # These are ordinary setting writes. Deliberately omit explicit,
        # privacy, adaptive-routing, prompt, duration, and every reference.
        settings = profile_settings(model_type, definition["id"])
        if spectrum:
            reason = _spectrum_context_reason(context, reference_shape)
            available = reason is None
            if available and spectrum_compatibility is not None:
                available, reason = spectrum_compatibility(settings)
        if lightx2v:
            if selected != "minimax_h3":
                available = False
                reason = "LightX2V Experimental supports only MiniMax H3 Base FL2VA."
            elif lightx2v_compatibility is not None:
                available, reason = lightx2v_compatibility(settings)
        sage2_context_reason = (
            _sage2_base_context_reason(context, reference_shape)
            if settings["custom_settings"].get("h3_attention_engine") == "sage2"
            else None
        )
        if (
            available
            and settings["custom_settings"].get("h3_attention_engine") == "sage2"
            and (
                sage2_context_reason is not None
                or not (
                    sage2_status.get("available") is True
                    and sage2_status.get("validated") is True
                    and definition["id"] in set(sage2_status.get("validated_profiles") or ())
                )
            )
        ):
            available = False
            profile_gate_reason = None
            if (
                sage2_context_reason is None
                and sage2_status.get("available") is True
                and sage2_status.get("validated") is True
                and definition["id"] not in set(sage2_status.get("validated_profiles") or ())
            ):
                profile_gate_reason = (
                    f"SageAttention2++ {definition['label']} exact geometry gate is pending."
                )
            reason = str(
                sage2_context_reason
                or profile_gate_reason
                or sage2_status.get("validation_reason")
                or sage2_status.get("reason")
                or f"SageAttention2++ {definition['label']} geometry validation is unavailable on this runtime."
            )
        if turbo:
            if available and turbo_compatibility is not None:
                available, reason = turbo_compatibility(settings)
        upscale = str(settings.get("spatial_upsampling") or "")
        if (
            available and upscale and upscale_status
            and upscale_status.get("enabled") is not True
        ):
            available = False
            reason = str(
                upscale_status.get("reason")
                or "FlashVSR spatial upscaling is disabled."
            )
        description = definition["description"]
        if definition["attention_engine"] == "sage2" and (
            settings["custom_settings"]["h3_attention_engine"] != "sage2"
        ):
            description = (
                f"{definition['label']} geometry and Turbo steps with dense SDPA "
                "for this unvalidated checkpoint."
            )
        result.append({
            **definition,
            # Keep the advertised engine identical to the actual model-aware
            # settings (non-Base H3 bundles conservatively substitute SDPA).
            "attention_engine": settings["custom_settings"]["h3_attention_engine"],
            "description": description,
            "available": available,
            "fallback_reason": reason,
            "fallback_profile_id": None,
            "download_required": bool(
                available and (
                    (turbo and not bool(turbo_status.get("downloaded")))
                    or (lightx2v and not bool(lightx2v_status.get("downloaded")))
                    or (model_exists(model_type) and not model_downloaded(model_type))
                    or (upscale and not bool(upscale_status.get("downloaded")))
                )
            ),
            "download_components": [
                component
                for component, required in (
                    ("H3 checkpoint", model_exists(model_type) and not model_downloaded(model_type)),
                    ("Turbo adapter", turbo and not bool(turbo_status.get("downloaded"))),
                    ("LightX2V adapter", lightx2v and not bool(lightx2v_status.get("downloaded"))),
                    ("FlashVSR", bool(upscale) and not bool(upscale_status.get("downloaded"))),
                )
                if required
            ],
            "settings": settings,
            "matched_reference_count": _reference_count(reference_shape),
        })
    # Fallbacks are catalog policy, not UI policy. For an unavailable bundle,
    # select the first higher-quality compatible bundle in canonical order.
    # Never jump backward to a faster profile or guess a hard-coded default.
    for index, profile in enumerate(result):
        if profile["available"]:
            continue
        profile["fallback_profile_id"] = next(
            (
                candidate["id"]
                for candidate in result[index + 1:]
                if candidate["available"]
            ),
            None,
        )
    return result


__all__ = [
    "DEFAULT_H3_PROFILE_ID", "H3_NATIVE_RESOLUTIONS", "build_profile_options",
    "default_profile_settings", "profile_definition", "profile_definitions",
    "profile_settings",
]
