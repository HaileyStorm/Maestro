"""Curated, non-locking MiniMax H3 performance setting bundles."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from services.h3_dasiwa import (
    BETTER_MOTION_ARTIFACT_ID,
    BETTER_MOTION_DEFAULT_STRENGTH,
    BETTER_MOTION_FILENAME,
    BETTER_MOTION_PROFILE_ID,
    BETTER_MOTION_SCHEDULER,
    DASIWA_ARTIFACT_ID,
    DASIWA_COMPATIBLE_BASE_SHA256,
    DASIWA_FILENAME,
    DASIWA_PROFILE_ID,
    DASIWA_SCHEDULER,
    DASIWA_STRENGTH,
    DASIWA_SUSPECTED_BASE_FILENAME,
    DASIWA_SUSPECTED_BASE_SHA256,
    INCOMPATIBLE_ACCELERATORS,
    LORA_INSERTION_MODE,
    dasiwa_lora_candidate_status,
    experiment_status,
)


H3_NATIVE_RESOLUTIONS = (
    "1344x768", "768x1344", "1024x768", "768x1024", "768x768",
    "1152x640", "640x1152", "960x544", "544x960", "864x480",
    "480x864", "640x640", "608x352", "352x608",
)

OWNER_DEFAULT_H3_PROFILE_ID = "high"
NON_OWNER_DEFAULT_H3_PROFILE_ID = "quality"
# Compatibility authority for accounts-off and machine-local callers. HTTP
# admission resolves authenticated non-owner defaults explicitly instead of
# changing this long-standing local-owner fallback.
DEFAULT_H3_PROFILE_ID = OWNER_DEFAULT_H3_PROFILE_ID

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
        "description": "Native H3 at a balanced 23-step quality setting.",
        "accelerator": "native",
        "num_inference_steps": 23,
        "resolution": "960x544",
        "attention_engine": "sol_attn",
    },
    {
        "id": "high",
        "label": "High",
        "description": (
            "Maximum native H3 canvas at 28 steps with Sol attention; "
            "no learned upscale or delivery crop."
        ),
        "accelerator": "native",
        "num_inference_steps": 28,
        "resolution": "1344x768",
        "attention_engine": "sol_attn",
    },
    {
        "id": "spectrum_experimental",
        "label": "Spectrum Experimental",
        "description": (
            "Experimental clean-room H3 forecast accelerator: the High 1344x768 "
            "28-step Sol bundle captures 11 paired hidden-feature anchors and nine "
            "causal forecast slots, then replays every step without transformer blocks. "
            "Audio uses local interpolation only; "
            "quality and speed still require live validation."
        ),
        "accelerator": "spectrum",
        "num_inference_steps": 28,
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
        "id": "dasiwa_ref2va_experimental",
        "label": "Dasiwa Ref2VA Experimental",
        "description": (
            "Owner-enabled four-step Ref2VA hybrid LoRA. It requires its pinned "
            "artifact and the exact compatible Ref2VA checkpoint; perceptual GPU "
            "validation remains pending. It never stacks with another accelerator."
        ),
        "accelerator": "dasiwa",
        "artifact_id": DASIWA_ARTIFACT_ID,
        "runtime_profile_id": DASIWA_PROFILE_ID,
        "num_inference_steps": 4,
        "resolution": "608x352",
        "attention_engine": "sdpa",
        "lora_filename": DASIWA_FILENAME,
        "lora_strength": DASIWA_STRENGTH,
        "scheduler": DASIWA_SCHEDULER,
        "insertion_mode": LORA_INSERTION_MODE,
        "incompatible_accelerators": INCOMPATIBLE_ACCELERATORS,
        "fallback_eligible": False,
        "allow_fallback": False,
    },
    {
        "id": "dasiwa_ref2va_suspected_experimental",
        "label": "Dasiwa on Installed Ref2VA (Unverified)",
        "description": (
            "Owner-enabled compatibility probe using the installed scaled-FP8 "
            "Ref2VA checkpoint. Static tensor conversion passes, but this is not "
            "the author's exact base and needs a coherent-output GPU check."
        ),
        "accelerator": "dasiwa_suspected",
        "artifact_id": DASIWA_ARTIFACT_ID,
        "runtime_profile_id": DASIWA_PROFILE_ID + "_suspected_base",
        "num_inference_steps": 4,
        "resolution": "608x352",
        "attention_engine": "sdpa",
        "lora_filename": DASIWA_FILENAME,
        "lora_strength": DASIWA_STRENGTH,
        "scheduler": DASIWA_SCHEDULER,
        "insertion_mode": LORA_INSERTION_MODE,
        "suspected_base_filename": DASIWA_SUSPECTED_BASE_FILENAME,
        "suspected_base_sha256": DASIWA_SUSPECTED_BASE_SHA256,
        "incompatible_accelerators": INCOMPATIBLE_ACCELERATORS,
        "fallback_eligible": False,
        "allow_fallback": False,
    },
    {
        "id": "better_motion_ref2va_experimental",
        "label": "Better Motion Ref2VA Experimental",
        "description": (
            "Optional Ref2VA motion LoRA at strength 0.9. Its pinned Civitai "
            "version must be downloaded explicitly; it never silently "
            "substitutes another asset or accelerator."
        ),
        "accelerator": "better_motion",
        "artifact_id": BETTER_MOTION_ARTIFACT_ID,
        "runtime_profile_id": BETTER_MOTION_PROFILE_ID,
        "num_inference_steps": 28,
        "resolution": "1344x768",
        "attention_engine": "sol_attn",
        "lora_filename": BETTER_MOTION_FILENAME,
        "lora_strength": BETTER_MOTION_DEFAULT_STRENGTH,
        "scheduler": BETTER_MOTION_SCHEDULER,
        "insertion_mode": LORA_INSERTION_MODE,
        "incompatible_accelerators": INCOMPATIBLE_ACCELERATORS,
        "fallback_eligible": False,
        "allow_fallback": False,
    },
    {
        "id": "1080p_delivery",
        "label": "1080p Delivery",
        "description": (
            "A 1344x768 native, 32-step Sol inference above High; "
            "FlashVSR 1.5x reaches 2016x1152, then a small center crop/downsample delivers "
            "exact 1920x1080. The added time/download is delivery-only."
        ),
        "accelerator": "native",
        "num_inference_steps": 32,
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
            "1344x768 native H3 with dense attention and 32 steps, then "
            "FlashVSR shifted two-pass 2x delivery at exactly 2688x1536 (2.7K, not 4K)."
        ),
        "accelerator": "native",
        "num_inference_steps": 32,
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
            "1344x768 native H3 with dense attention and 32 steps; learned "
            "FlashVSR 3x detail synthesis reaches 4032x2304, then an explicit "
            "center crop/downsample delivers exact 3840x2160. Upscaled, not native 4K."
        ),
        "accelerator": "native",
        "num_inference_steps": 32,
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
    elif definition["accelerator"] in {
        "dasiwa", "dasiwa_suspected", "better_motion",
    }:
        filename = str(definition["lora_filename"])
        strength = float(definition["lora_strength"])
        settings["activated_loras"] = [filename]
        settings["loras_multipliers"] = str(strength)
        settings["lora_weights"] = {filename: [strength]}
    return settings


def default_profile_settings(model_type: str = "minimax_h3") -> dict[str, Any]:
    """Return H3's fresh/omitted High bundle for ``model_type``."""
    return profile_settings(model_type, DEFAULT_H3_PROFILE_ID)


def fresh_profile_id_for_account_role(account_role: str | None) -> str:
    """Resolve a fresh role-aware H3 bundle without replacing authored state."""
    return (
        NON_OWNER_DEFAULT_H3_PROFILE_ID
        if str(account_role or "").strip().casefold() == "user"
        else OWNER_DEFAULT_H3_PROFILE_ID
    )


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
    dasiwa_checkpoint_status: Mapping[str, Any] | None = None,
    dasiwa_status: Mapping[str, Any] | None = None,
    better_motion_status: Mapping[str, Any] | None = None,
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
    dasiwa_checkpoint_status = dict(dasiwa_checkpoint_status or {})
    if dasiwa_status is None:
        dasiwa_status = dasiwa_lora_candidate_status()
    if better_motion_status is None:
        better_motion_status = experiment_status(
            BETTER_MOTION_ARTIFACT_ID,
            selected_model_type=selected,
        )
    experiment_statuses = {
        "dasiwa": dict(dasiwa_status),
        "dasiwa_suspected": dict(dasiwa_status),
        "better_motion": {
            "downloaded": False,
            "download_required": True,
            "reason": "The pinned Better Motion artifact must be downloaded explicitly.",
            **dict(better_motion_status),
        },
    }
    turbo_registered = bool(turbo_status.get("registered"))
    result: list[dict[str, Any]] = []
    for definition in profile_definitions():
        turbo = definition["accelerator"] == "turbo"
        spectrum = definition["accelerator"] == "spectrum"
        lightx2v = definition["accelerator"] == "lightx2v"
        experiment = definition["accelerator"] in experiment_statuses
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
        if experiment:
            status = experiment_statuses[definition["accelerator"]]
            checkpoint_compatibility = str(
                dasiwa_checkpoint_status.get("compatibility") or ""
            )
            checkpoint_sha256 = str(
                dasiwa_checkpoint_status.get("sha256") or ""
            ).strip().casefold()
            if selected != "minimax_h3_ref2va":
                available = False
                reason = f"{definition['label']} supports only MiniMax H3 Ref2VA."
            elif (
                definition["accelerator"] == "dasiwa"
                and not (
                    dasiwa_checkpoint_status.get("verified") is True
                    and checkpoint_compatibility == "exact_base"
                    and checkpoint_sha256 == DASIWA_COMPATIBLE_BASE_SHA256
                )
            ):
                available = False
                reason = (
                    "Dasiwa exact-base mode is unavailable until the selected "
                    "transformer matches its verified exact checkpoint contract."
                )
            elif (
                definition["accelerator"] == "dasiwa_suspected"
                and not (
                    (
                        dasiwa_checkpoint_status.get("verified") is True
                        and checkpoint_compatibility == "suspected_compatible_base"
                        and checkpoint_sha256 == DASIWA_SUSPECTED_BASE_SHA256
                    )
                    or (
                        dasiwa_checkpoint_status.get("candidate") is True
                        and dasiwa_checkpoint_status.get("preparation_required") is True
                        and checkpoint_compatibility == "suspected_compatible_base"
                    )
                )
            ):
                available = False
                reason = (
                    "The installed Ref2VA compatibility probe is unavailable "
                    "until its actual transformer passes the local integrity receipt."
                )
            elif (
                status.get("available") is not True
                and not (
                    definition["accelerator"] == "dasiwa_suspected"
                    and status.get("candidate") is True
                    and status.get("preparation_required") is True
                )
            ):
                available = False
                reason = str(
                    status.get("reason")
                    or f"{definition['label']} has not passed its exact artifact compatibility gate."
                )
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
        if available:
            try:
                from services.h3_host_limits import host_limit_reason_for_profile
                host_reason = host_limit_reason_for_profile(settings, context)
            except Exception:
                host_reason = None
            if host_reason:
                available = False
                reason = host_reason
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
            "preparation_required": bool(
                definition["accelerator"] == "dasiwa_suspected"
                and (
                    dasiwa_checkpoint_status.get("preparation_required") is True
                    or experiment_statuses["dasiwa_suspected"].get(
                        "preparation_required"
                    ) is True
                )
            ),
            "preparation_reason": (
                str(
                    dasiwa_checkpoint_status.get("reason")
                    or experiment_statuses["dasiwa_suspected"].get("reason")
                    or "Runtime verification is required."
                )
                if definition["accelerator"] == "dasiwa_suspected"
                and (
                    dasiwa_checkpoint_status.get("preparation_required") is True
                    or experiment_statuses["dasiwa_suspected"].get(
                        "preparation_required"
                    ) is True
                ) else None
            ),
            "fallback_reason": reason,
            "fallback_profile_id": None,
            "download_required": bool(
                (
                    experiment
                    and experiment_statuses[definition["accelerator"]].get(
                        "download_required"
                    ) is True
                )
                or available and (
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
                    (
                        definition.get("label", "H3 experiment") + " artifact",
                        experiment
                        and experiment_statuses[definition["accelerator"]].get(
                            "download_required"
                        ) is True,
                    ),
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
        if profile["available"] or profile.get("allow_fallback") is False:
            continue
        profile["fallback_profile_id"] = next(
            (
                candidate["id"]
                for candidate in result[index + 1:]
                if candidate["available"] and candidate.get("fallback_eligible") is not False
            ),
            None,
        )
    return result


__all__ = [
    "DEFAULT_H3_PROFILE_ID", "OWNER_DEFAULT_H3_PROFILE_ID",
    "NON_OWNER_DEFAULT_H3_PROFILE_ID", "H3_NATIVE_RESOLUTIONS",
    "build_profile_options", "default_profile_settings",
    "fresh_profile_id_for_account_role", "profile_definition",
    "profile_definitions", "profile_settings",
]
