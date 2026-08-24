"""Maestro family handler for MiniMax H3 FL2VA and Ref2VA."""

from __future__ import annotations

import os

import torch


_MODEL_TYPE = "minimax_h3"
_REF2VA_MODEL_TYPE = "minimax_h3_ref2va"
_COMFY_REPO = "Comfy-Org/MiniMax-H3"
_COMFY_REVISION = "0543966fbdce5ba05709a8f2031c94bdba629b4a"
_COMFY_REF2VA_REVISION = "eb8a16107c595128b3a578f82d2ce2f75920c355"
_OFFICIAL_REPO = "MiniMaxAI/MiniMax-H3"
_OFFICIAL_REVISION = "5d9b308a59ab12e67147f191e184baf704185bd1"
_ASSETS_ROOT = "minimax_h3"

_TRANSFORMER = "minimax_h3_fl2va_pruned_fp8_scaled.safetensors"
_REF2VA_TRANSFORMER = "minimax_h3_ref2va_pruned_fp8_scaled.safetensors"
_TEXT_ENCODER = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
_VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
_PROCESSOR_FILES = (
    "chat_template.json",
    "merges.txt",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "video_preprocessor_config.json",
    "vocab.json",
)
_REQUIRED_RUNTIME_ASSETS = {
    "video_vae": os.path.join(_ASSETS_ROOT, "vae", _VIDEO_VAE),
    "audio_vae": os.path.join(_ASSETS_ROOT, "vae", _AUDIO_VAE),
    "text_encoder_config": os.path.join(
        _ASSETS_ROOT, "text_encoder", "config.json"
    ),
    "processor": tuple(
        os.path.join(_ASSETS_ROOT, "processor", filename)
        for filename in _PROCESSOR_FILES
    ),
}
_WANGP_TEXT_ENCODER_FOLDER = "Qwen3-VL-32B-Instruct"
_WANGP_FL2VA_PRUNED_TRANSFORMER = (
    "MiniMax-H3-FL2VA-pruned_rank8_int8_convrot.safetensors"
)
_WANGP_REF2VA_PRUNED_TRANSFORMER = (
    "MiniMax-H3-Ref2VA-pruned_rank8_int8_convrot.safetensors"
)
_WANGP_FL2VA_PRUNED_BF16_TRANSFORMER = (
    "MiniMax-H3-FL2VA-pruned_rank8_bf16.safetensors"
)
_WANGP_REF2VA_PRUNED_BF16_TRANSFORMER = (
    "MiniMax-H3-Ref2VA-pruned_rank8_bf16.safetensors"
)

# H3 packs video, audio, and text into one unusually long transformer
# sequence.  At 480p / 10 seconds the token-wise activations alone need
# several gigabytes, so MMGP must not treat its model-weight safety cap as
# the entire available VRAM budget.  ``workingVRAM`` reserves this amount
# independently of the user's card size; MMGP streams more transformer
# blocks on smaller cards instead of starving the first denoising step.
_TRANSFORMER_WORKING_VRAM_MB = 10 * 1024

_REF2VA_LIMITS = {
    "image_count": 9,
    "video_count": 3,
    "audio_count": 3,
    "mixed_file_count": 12,
    "output_duration_seconds": {"min": 4, "max": 15},
    "reference_video_duration_seconds": {"min": 2, "max": 15, "total_max": 15},
    "reference_audio_duration_seconds": {"min": 2, "max": 15, "total_max": 15},
}


def _is_reference_mode(base_model_type: str) -> bool:
    return base_model_type == _REF2VA_MODEL_TYPE


def _hf_url(repo_id: str, revision: str, *parts: str) -> str:
    path = "/".join(part.strip("/\\") for part in parts if part)
    return f"https://huggingface.co/{repo_id}/resolve/{revision}/{path}"


def _required_runtime_asset_manifest() -> dict:
    """Return the one canonical H3 auxiliary-asset contract."""

    return {
        role: list(paths) if isinstance(paths, tuple) else paths
        for role, paths in _REQUIRED_RUNTIME_ASSETS.items()
    }


def _required_asset_filenames(folder: str) -> list[str]:
    prefix = os.path.join(_ASSETS_ROOT, folder) + os.sep
    return [
        os.path.relpath(path, prefix)
        for value in _REQUIRED_RUNTIME_ASSETS.values()
        for path in (value if isinstance(value, tuple) else (value,))
        if path.startswith(prefix)
    ]


_RESOLUTIONS = [
    ("1920x1088 (16:9 experimental)", "1920x1088"),
    ("1088x1920 (9:16 experimental)", "1088x1920"),
    ("1440x1088 (4:3 experimental)", "1440x1088"),
    ("1088x1440 (3:4 experimental)", "1088x1440"),
    ("1088x1088 (1:1 experimental)", "1088x1088"),
    ("1344x768 (16:9 native)", "1344x768"),
    ("768x1344 (9:16 native)", "768x1344"),
    ("1024x768 (4:3 native)", "1024x768"),
    ("768x1024 (3:4 native)", "768x1024"),
    ("768x768 (1:1 native)", "768x768"),
    ("1280x704 (16:9 720p)", "1280x704"),
    ("704x1280 (9:16 720p)", "704x1280"),
    ("928x704 (4:3 720p)", "928x704"),
    ("704x928 (3:4 720p)", "704x928"),
    ("704x704 (1:1 720p)", "704x704"),
    ("1152x640 (16:9)", "1152x640"),
    ("640x1152 (9:16)", "640x1152"),
    ("960x544 (16:9)", "960x544"),
    ("544x960 (9:16)", "544x960"),
    ("736x544 (4:3)", "736x544"),
    ("544x736 (3:4)", "544x736"),
    ("736x736 (1:1)", "736x736"),
    ("864x480 (16:9 low VRAM)", "864x480"),
    ("480x864 (9:16 low VRAM)", "480x864"),
    ("640x480 (4:3 low VRAM)", "640x480"),
    ("480x640 (3:4 low VRAM)", "480x640"),
    ("640x640 (1:1 low VRAM)", "640x640"),
    ("608x352 (16:9 minimum)", "608x352"),
    ("352x608 (9:16 minimum)", "352x608"),
]

_RESOLUTION_PRESETS = {
    "480p": {
        "label": "480p",
        "values": {
            "auto": "auto_480p",
            "16:9": "864x480",
            "9:16": "480x864",
            "1:1": "640x640",
            "4:3": "640x480",
            "3:4": "480x640",
        },
    },
    "540p": {
        "label": "540p",
        "values": {
            "auto": "auto_540p",
            "16:9": "960x544",
            "9:16": "544x960",
            "1:1": "736x736",
            "4:3": "736x544",
            "3:4": "544x736",
        },
    },
    "720p": {
        "label": "720p",
        "hint": "Uses a model-aligned 1280x704 canvas for faster H3 generation.",
        "values": {
            "auto": "auto_720p",
            "16:9": "1280x704",
            "9:16": "704x1280",
            "1:1": "704x704",
            "4:3": "928x704",
            "3:4": "704x928",
        },
    },
    # Hidden compatibility tier for saved settings and output recovery.
    "768p": {
        "label": "768p High",
        "hint": "H3's released 1344x768 native canvas family.",
        "values": {
            "auto": "auto_768p",
            "16:9": "1344x768",
            "9:16": "768x1344",
            "1:1": "768x768",
            "4:3": "1024x768",
            "3:4": "768x1024",
        },
    },
    "1080p": {
        "label": "1080p",
        "experimental": True,
        "hint": "Experimental high-resolution H3 generation; shorter windows may be required.",
        "values": {
            "auto": "auto_1080p",
            "16:9": "1920x1088",
            "9:16": "1088x1920",
            "1:1": "1088x1088",
            "4:3": "1440x1088",
            "3:4": "1088x1440",
        },
    },
}
_RESOLUTION_PRESET_ORDER = ["480p", "540p", "720p", "1080p"]
_AUTO_RESOLUTION_BUDGETS = {
    "auto": 1280 * 704,
    "auto_480p": 864 * 480,
    "auto_540p": 960 * 544,
    "auto_720p": 1280 * 704,
    "auto_768p": 1344 * 768,
    "auto_1080p": 1920 * 1088,
}
_AUTO_RESOLUTION_FALLBACKS = {
    "auto": "1280x704",
    "auto_480p": "864x480",
    "auto_540p": "960x544",
    "auto_720p": "1280x704",
    "auto_768p": "1344x768",
    "auto_1080p": "1920x1088",
}
_LEGACY_RESOLUTION_ALIASES = {
    "848x480": "864x480", "480x848": "480x864", "672x672": "640x640",
    "832x608": "736x544", "608x832": "544x736",
    "1280x720": "1280x704", "720x1280": "704x1280",
    "1024x1024": "768x768", "1104x832": "1024x768",
    "832x1104": "768x1024",
}


def _normalize_resolution(value) -> str:
    resolution = str(value or "864x480").strip().lower()
    if resolution in _AUTO_RESOLUTION_BUDGETS:
        return resolution
    if resolution in _LEGACY_RESOLUTION_ALIASES:
        return _LEGACY_RESOLUTION_ALIASES[resolution]
    if resolution in {item for _, item in _RESOLUTIONS}:
        return resolution
    return "864x480"


class family_handler:
    @staticmethod
    def _apply_fresh_profile_defaults(ui_defaults):
        """Hydrate omitted H3 controls from the curated High profile."""
        from services.h3_profiles import default_profile_settings

        settings = default_profile_settings()
        # The caller already owns checkpoint identity. LoRA phase weights are
        # frontend state; the backend default only needs the empty stack.
        settings.pop("model_type", None)
        settings.pop("lora_weights", None)
        ui_defaults.update(settings)

    @staticmethod
    def query_supported_types():
        return [_MODEL_TYPE, _REF2VA_MODEL_TYPE]

    @staticmethod
    def query_family_maps():
        return {_REF2VA_MODEL_TYPE: _MODEL_TYPE}, {}

    @staticmethod
    def query_model_family():
        return "minimax_h3"

    @staticmethod
    def query_family_infos():
        return {"minimax_h3": (55, "MiniMax H3")}

    @staticmethod
    def query_model_def(base_model_type, model_def):
        reference_mode = _is_reference_mode(base_model_type)
        native_boundary_enabled = (
            os.environ.get("MAESTRO_H3_NATIVE_BOUNDARY_EXPERIMENTAL") == "1"
        )
        result = {
            "dtype": "bf16",
            "fps": 24,
            # H3's video VAE accepts only 17*n+5 frames.  124 is the first
            # valid count at or above five seconds; 345 is the last at or
            # below fifteen seconds.
            "frames_minimum": 107 if reference_mode else 124,
            "frames_steps": 17,
            "frames_maximum": 345,
            "latent_size": 17,
            "frame_alignment_modulus": 17,
            "frame_alignment_remainder": 5,
            "frame_alignment_mode": "ceil",
            "sliding_window": False,
            "t2v_class": True,
            "i2v_class": not reference_mode,
            "image_prompt_types_allowed": "T" if reference_mode else "TSE",
            "end_frames_always_enabled": not reference_mode,
            "returns_audio": True,
            "multimedia_generation": True,
            "no_negative_prompt": True,
            "guidance_max_phases": 0,
            "visible_phases": 0,
            "compile": False,
            "resolutions": _RESOLUTIONS,
            "resolution_presets": _RESOLUTION_PRESETS,
            "resolution_preset_order": _RESOLUTION_PRESET_ORDER,
            "supports_auto_aspect": True,
            "auto_resolution_budgets": _AUTO_RESOLUTION_BUDGETS,
            "auto_resolution_fallbacks": _AUTO_RESOLUTION_FALLBACKS,
            "profiles_dir": ["minimax_h3"],
            "minimax_h3_assets_root": _ASSETS_ROOT,
            "required_runtime_assets": _required_runtime_asset_manifest(),
            "minimax_h3_reference_mode": reference_mode,
            # The two H3 checkpoints accept different kinds of conditioning.
            # Keep this machine-readable so Studio can render semantic refs
            # without treating them as timeline/keyframe anchors.
            "minimax_h3_conditioning_mode": (
                "semantic_references" if reference_mode else "first_last_frames"
            ),
            "minimax_h3_conditioning_modes_mutually_exclusive": True,
            "text_encoder_folder": _ASSETS_ROOT,
            "text_encoder_quantization": "int8",
            "text_encoder_URLs": [
                _hf_url(_COMFY_REPO, _COMFY_REVISION, "text_encoders", _TEXT_ENCODER)
            ],
            # The runtime supports both Comfy's scaled-FP8 export and WanGP's
            # pruned INT8 ConvRot export. Keep these exact names as declared,
            # bidirectional read aliases without changing the download target.
            "compatible_model_paths": {
                (
                    _REF2VA_TRANSFORMER if reference_mode else _TRANSFORMER
                ): [
                    _WANGP_REF2VA_PRUNED_TRANSFORMER
                    if reference_mode
                    else _WANGP_FL2VA_PRUNED_TRANSFORMER
                ],
                (
                    _WANGP_REF2VA_PRUNED_TRANSFORMER
                    if reference_mode
                    else _WANGP_FL2VA_PRUNED_TRANSFORMER
                ): [
                    _REF2VA_TRANSFORMER if reference_mode else _TRANSFORMER
                ],
            },
            "compatible_model_qkv_layouts": {
                (
                    _WANGP_REF2VA_PRUNED_TRANSFORMER
                    if reference_mode
                    else _WANGP_FL2VA_PRUNED_TRANSFORMER
                ): "interleaved",
                (
                    _WANGP_REF2VA_PRUNED_BF16_TRANSFORMER
                    if reference_mode
                    else _WANGP_FL2VA_PRUNED_BF16_TRANSFORMER
                ): "interleaved",
            },
            # WanGP stores the same published Qwen weight in its upstream
            # folder, while Maestro keeps the canonical file under H3 assets.
            "compatible_text_encoder_paths": {
                _TEXT_ENCODER: [
                    os.path.join(_WANGP_TEXT_ENCODER_FOLDER, _TEXT_ENCODER)
                ]
            },
            "runtime_custom_settings": [
                "h3_attention_engine",
                "h3_sol_tau",
                "h3_sol_dense_steps",
                "h3_sol_dense_blocks",
                "h3_sol_min_tokens",
                "h3_benchmark_capture",
                "h3_turbo_profile",
                "h3_spectrum_profile",
                "h3_lightx2v_profile",
                "h3_native_boundary_conditioning",
                "h3_source_audio_mode",
                "h3_primary_audio_ordinal",
                "h3_audio_remix_strength",
            ],
            "h3_native_boundary_conditioning": native_boundary_enabled,
            "minimax_h3_native_boundary_image_prompt_types_allowed": (
                "TSE" if native_boundary_enabled else "T"
            ),
        }
        if reference_mode:
            result.update(
                {
                    "reference_image_enabled": True,
                    "return_image_refs_tensor": False,
                    "no_processing_on_last_images_refs": 9,
                    "no_background_removal": True,
                    "any_image_refs_relative_size": True,
                    "image_refs_relative_size": {"min": 50, "max": 400, "step": 1},
                    "image_ref_choices": {
                        "choices": [
                            ("Generate without Reference Images", ""),
                            ("Use Reference Images", "I"),
                            (
                                "First Reference Image Defines Output Dimensions",
                                "KI",
                            ),
                        ],
                        "letters_filter": "KI",
                        "default": "",
                        "label": "Reference Images",
                    },
                    "guide_custom_choices": {
                        "choices": [
                            ("Generate without Reference Video", ""),
                            ("Use One Reference Video", "V-"),
                            ("Use Two Reference Videos", "V+-"),
                            ("Use Three Reference Videos", "V++-"),
                        ],
                        "letters_filter": "V+-",
                        "default": "",
                        "label": "Reference Videos",
                    },
                    "preprocess_video_guide2": True,
                    "reference_video_max_frames": 15 * 24,
                    "reference_video_max_size": (768, 1344),
                    "control_video_trim_disabled": True,
                    "video_guide_label": "Reference Video 1",
                    "video_guide2_label": "Reference Video 2",
                    "any_audio_prompt": True,
                    "audio_prompt_choices": True,
                    "audio_guide_label": "Audio Reference 1",
                    "audio_guide2_label": "Audio Reference 2",
                    "audio_guide3_label": "Audio Reference 3",
                    "audio_prompt_type_sources": {
                        "selection": ["", "A", "AB", "ABC", "K"],
                        "labels": {
                            "": "Generate without an Audio Reference",
                            "A": "Use One Audio Reference",
                            "AB": "Use Two Audio References",
                            "ABC": "Use Three Audio References",
                            "K": "Use Reference-Video Soundtrack(s)",
                        },
                        "letters_filter": "ABCK",
                        "label": "Audio References",
                        "show_label": True,
                        "default": "",
                    },
                    "audio_guide_window_slicing": True,
                    "video_length_not_limited_by_audio": True,
                    "reference_image_max_count": 9,
                    "reference_video_max_count": 3,
                    "reference_audio_max_count": 3,
                    "mixed_reference_max_count": 12,
                    "semantic_reference_limits": dict(_REF2VA_LIMITS),
                    "runtime_custom_settings": result["runtime_custom_settings"] + [
                        "h3_ref2va_chain_id",
                        "h3_ref2va_handoff",
                        "h3_ref2va_handoff_video_slot",
                        "h3_ref2va_handoff_frames",
                        "h3_ref2va_handoff_audio",
                    ],
                }
            )
        return result

    @staticmethod
    def validate_generative_settings(base_model_type, model_def, inputs):
        custom_settings = inputs.get("custom_settings")
        from services.h3_audio import (
            H3AudioCompatibilityError,
            H3MediaMapError,
            remap_prompt_audio_ordinals,
            resolve_h3_audio_roles,
            source_audio_requested,
            validate_prompt_media_ordinals,
        )

        custom_for_audio = custom_settings if isinstance(custom_settings, dict) else {}
        audio_prompt_type = inputs.get("audio_prompt_type") or ""
        video_prompt_type = inputs.get("video_prompt_type") or ""
        image_refs = inputs.get("image_refs") or []
        try:
            experimental_source_audio = source_audio_requested(custom_for_audio)
            semantic_references = (
                bool(image_refs)
                or "V" in video_prompt_type
                or (
                    not experimental_source_audio
                    and any(letter in audio_prompt_type for letter in "ABCK")
                )
            )
            audio_roles = resolve_h3_audio_roles(
                selected_model_type=str(base_model_type or ""),
                model_def=model_def or {},
                custom_settings=custom_for_audio,
                sampling_steps=inputs.get("num_inference_steps"),
                attention_engine=str(
                    custom_for_audio.get("h3_attention_engine") or "sol_attn"
                ),
                audio_prompt_type=audio_prompt_type,
                audio_guides=tuple(
                    inputs.get(key)
                    for key in ("audio_guide", "audio_guide2", "audio_guide3")
                ),
                final_audio=inputs.get("audio_source"),
                semantic_references=semantic_references,
                multisegment=(
                    isinstance(inputs.get("multi_clip_info"), dict)
                    and int(inputs["multi_clip_info"].get("total", 1) or 1) > 1
                ),
                activated_loras=inputs.get("activated_loras"),
                loras_multipliers=inputs.get("loras_multipliers"),
                skip_steps_cache_type=(
                    inputs.get("skip_steps_cache_type") or inputs.get("tea_cache")
                ),
                native_boundary=bool(
                    custom_for_audio.get("h3_native_boundary_conditioning")
                    or inputs.get("h3_native_boundary_conditioning")
                ),
            )
            prompt_for_ordinals = str(inputs.get("prompt") or "")
            if audio_roles.audio_ordinal_remap:
                prompt_for_ordinals = remap_prompt_audio_ordinals(
                    prompt_for_ordinals,
                    dict(audio_roles.audio_ordinal_remap),
                )
            picture_count = (
                len(image_refs)
                if _is_reference_mode(base_model_type)
                else sum(inputs.get(key) is not None for key in ("image_start", "image_end"))
            )
            selected_video_slots = []
            if "V" in video_prompt_type:
                selected_video_slots.append(inputs.get("video_guide"))
                if "+" in video_prompt_type:
                    selected_video_slots.append(inputs.get("video_guide2"))
                if "++" in video_prompt_type or inputs.get("video_guide3") is not None:
                    selected_video_slots.append(inputs.get("video_guide3"))
            video_count_for_ordinals = sum(
                slot is not None for slot in selected_video_slots
            )
            legacy_audio_count = (
                video_count_for_ordinals
                if "K" in audio_prompt_type
                else sum(
                    letter in audio_prompt_type and inputs.get(key) is not None
                    for letter, key in (
                        ("A", "audio_guide"),
                        ("B", "audio_guide2"),
                        ("C", "audio_guide3"),
                    )
                )
            )
            validate_prompt_media_ordinals(
                prompt_for_ordinals,
                picture_count=picture_count,
                video_count=video_count_for_ordinals,
                audio_count=(
                    len(audio_roles.reference_audios)
                    if audio_roles.mode == "reference_only"
                    else legacy_audio_count
                ),
            )
        except (H3AudioCompatibilityError, H3MediaMapError) as error:
            return str(error)
        if isinstance(custom_settings, dict) and custom_settings.get("h3_spectrum_profile"):
            from .spectrum import SpectrumCompatibilityError, validate_spectrum_request

            try:
                validate_spectrum_request(
                    selected_model_type=str(base_model_type or ""),
                    model_def=model_def,
                    reference_mode=bool(
                        (model_def or {}).get("minimax_h3_reference_mode")
                    ),
                    sampling_steps=inputs.get("num_inference_steps"),
                    attention_engine=str(
                        custom_settings.get("h3_attention_engine") or "sol_attn"
                    ),
                    custom_settings=custom_settings,
                    activated_loras=inputs.get("activated_loras"),
                    loras_multipliers=inputs.get("loras_multipliers"),
                    skip_steps_cache_type=(
                        inputs.get("skip_steps_cache_type")
                        or inputs.get("tea_cache")
                    ),
                    native_boundary=bool(
                        custom_settings.get("h3_native_boundary_conditioning")
                        or inputs.get("h3_native_boundary_conditioning")
                    ),
                )
            except SpectrumCompatibilityError as error:
                return str(error)
        if isinstance(custom_settings, dict) and custom_settings.get("h3_lightx2v_profile"):
            from services.h3_lightx2v import H3LightX2VCompatibilityError, validate_lightx2v_request
            try:
                validate_lightx2v_request(
                    selected_model_type=str(base_model_type or ""), model_def=model_def or {},
                    custom_settings=custom_settings,
                    authored_steps=inputs.get("num_inference_steps"),
                    semantic_references=bool((model_def or {}).get("minimax_h3_reference_mode")),
                    multisegment=(
                        isinstance(inputs.get("multi_clip_info"), dict)
                        and int(inputs["multi_clip_info"].get("total", 1) or 1) > 1
                    ),
                    activated_loras=inputs.get("activated_loras"),
                    loras_multipliers=inputs.get("loras_multipliers"),
                    skip_steps_cache_type=inputs.get("skip_steps_cache_type") or inputs.get("tea_cache"),
                    native_boundary=bool(custom_settings.get("h3_native_boundary_conditioning")),
                )
            except H3LightX2VCompatibilityError as error:
                return str(error)
        if isinstance(custom_settings, dict) and custom_settings.get("h3_turbo_profile"):
            from services.h3_turbo import H3TurboCompatibilityError, validate_turbo_request

            try:
                validate_turbo_request(
                    base_model_type=base_model_type,
                    model_def=model_def,
                    custom_settings=custom_settings,
                    authored_steps=inputs.get("num_inference_steps"),
                    activated_loras=inputs.get("activated_loras"),
                    loras_multipliers=inputs.get("loras_multipliers"),
                    skip_steps_cache_type=inputs.get("skip_steps_cache_type"),
                    _h3_turbo_validation_authorized=(
                        inputs.get("_h3_turbo_validation_authorized") is True
                    ),
                )
            except H3TurboCompatibilityError as error:
                return str(error)
        # Experimental source-audio modes intentionally reuse the existing
        # audio-guide slots.  Those slots are drive/reference audio for the
        # Base pipeline, not a request to switch to Ref2VA.
        has_semantic_references = semantic_references
        if (
            inputs.get("h3_native_boundary_conditioning") is True
            and os.environ.get("MAESTRO_H3_NATIVE_BOUNDARY_EXPERIMENTAL") != "1"
        ):
            return (
                "MiniMax H3 native boundary conditioning is disabled until "
                "its live acceptance matrix passes"
            )
        if not _is_reference_mode(base_model_type):
            if has_semantic_references:
                return (
                    "MiniMax H3 semantic image, video, and audio references require the "
                    "Ref2VA checkpoint; FL2VA accepts only first/last-frame keyframes"
                )
            return None
        if (
            (inputs.get("image_start") is not None or inputs.get("image_end") is not None)
            and inputs.get("h3_native_boundary_conditioning") is not True
        ):
            return (
                "MiniMax H3 first/last-frame keyframes require the FL2VA checkpoint; "
                "Ref2VA accepts semantic references instead"
            )
        image_count = len(image_refs)
        # WanGP currently exposes two named video-guide slots, while the model
        # contract supports three.  Count a future third slot when supplied and
        # otherwise derive the requested slot count from the selector flags.
        named_video_count = sum(
            inputs.get(key) is not None
            for key in ("video_guide", "video_guide2", "video_guide3")
        )
        selected_video_count = int("V" in video_prompt_type) + video_prompt_type.count("+")
        video_count = max(named_video_count, selected_video_count)
        audio_count = video_count if "K" in audio_prompt_type else sum(
            letter in audio_prompt_type for letter in "ABC"
        )
        if image_count > 9:
            return "MiniMax H3 Ref2VA accepts at most 9 reference images"
        if video_count > 3:
            return "MiniMax H3 Ref2VA accepts at most 3 reference videos"
        if audio_count > 3:
            return "MiniMax H3 Ref2VA accepts at most 3 reference audio clips"
        visual_count = image_count + video_count
        if audio_count > visual_count:
            return "MiniMax H3 Ref2VA requires at least as many visual references as audio references"
        mixed_count = visual_count + (0 if "K" in audio_prompt_type else audio_count)
        if mixed_count > 12:
            return "MiniMax H3 Ref2VA accepts at most 12 mixed reference files"
        return None

    @staticmethod
    def register_lora_cli_args(parser, lora_root):
        parser.add_argument(
            "--lora-dir-minimax-h3",
            type=str,
            default=None,
            help=(
                "Path to a directory that contains MiniMax H3 LoRAs "
                f"(default: {os.path.join(lora_root, 'minimax_h3')})"
            ),
        )

    @staticmethod
    def get_lora_dir(base_model_type, args, lora_root):
        return getattr(args, "lora_dir_minimax_h3", None) or os.path.join(lora_root, "minimax_h3")

    @staticmethod
    def get_vae_block_size(base_model_type):
        return 32

    @staticmethod
    def query_model_files(computeList, base_model_type, model_def=None):
        return [
            {
                "repoId": _COMFY_REPO,
                "revision": _COMFY_REVISION,
                "sourceFolderList": ["vae"],
                "targetFolderList": [_ASSETS_ROOT],
                "fileList": [_required_asset_filenames("vae")],
            },
            {
                "repoId": _OFFICIAL_REPO,
                "revision": _OFFICIAL_REVISION,
                "sourceFolderList": ["processor", "text_encoder"],
                "targetFolderList": [_ASSETS_ROOT, _ASSETS_ROOT],
                "fileList": [
                    _required_asset_filenames("processor"),
                    _required_asset_filenames("text_encoder"),
                ],
            },
        ]

    @staticmethod
    def load_model(
        model_filename,
        model_type=None,
        base_model_type=None,
        model_def=None,
        dtype=torch.bfloat16,
        text_encoder_filename=None,
        **kwargs,
    ):
        from .minimax_h3_main import MiniMaxH3Model

        model = MiniMaxH3Model(
            model_filename=model_filename,
            model_def=model_def or {},
            text_encoder_filename=text_encoder_filename,
            dtype=dtype,
            load_status_callback=kwargs.get("load_status_callback"),
            selected_model_type=str(model_type or base_model_type or ""),
        )
        pipe = {
            "transformer": model.transformer,
            # Keep the wrapper top-level so MMGP's forward hook moves both
            # the truncated language model and vision tower together.
            "text_encoder": model.conditioner,
            "vae": model.vae,
            "audio_vae": model.audio_vae,
        }
        return model, {
            "pipe": pipe,
            "workingVRAM": {
                "transformer": _TRANSFORMER_WORKING_VRAM_MB,
            },
        }

    @staticmethod
    def update_default_settings(base_model_type, model_def, ui_defaults):
        reference_mode = _is_reference_mode(base_model_type)
        family_handler._apply_fresh_profile_defaults(ui_defaults)
        ui_defaults.update(
            {
                "video_length": 124,
                "guidance_scale": 1.0,
                "image_prompt_type": "",
                "h3_native_boundary_conditioning": False,
            }
        )
        if reference_mode:
            ui_defaults.update(
                {
                    "video_prompt_type": "",
                    "audio_prompt_type": "",
                    "image_mode": 0,
                    "image_refs_relative_size": 100,
                    # Segment planning defaults to Ref2VA's full native
                    # envelope; ordinary fresh video duration stays 124.
                    "sliding_window_size": 345,
                    "sliding_window_overlap": 0,
                }
            )

    @staticmethod
    def fix_settings(base_model_type, settings_version, model_def, ui_defaults):
        # Saved settings created before this family existed cannot need a
        # migration, but imported presets still need valid H3 geometry.
        from .packing import align_num_frames

        minimum_frames = 107 if _is_reference_mode(base_model_type) else 124
        try:
            requested_frames = int(ui_defaults.get("video_length", minimum_frames))
        except (TypeError, ValueError):
            requested_frames = minimum_frames
        aligned_frames = align_num_frames(max(1, requested_frames))
        ui_defaults["video_length"] = min(345, max(minimum_frames, aligned_frames))
        ui_defaults["resolution"] = _normalize_resolution(
            ui_defaults.get("resolution", "864x480")
        )
        ui_defaults["guidance_scale"] = 1.0
        custom_settings = ui_defaults.get("custom_settings")
        if not isinstance(custom_settings, dict):
            custom_settings = {}
            ui_defaults["custom_settings"] = custom_settings
        custom_settings.setdefault("h3_attention_engine", "sol_attn")
        custom_settings.setdefault("h3_sol_tau", 1.0)
        custom_settings.setdefault("h3_sol_dense_steps", 10)
        custom_settings.setdefault("h3_sol_dense_blocks", 2)
        custom_settings.setdefault("h3_sol_min_tokens", 4096)
        # Cached model defaults do not carry model_type, while live requests,
        # saved presets, and output sidecars do. Refresh only the former so a
        # newly selected/fresh H3 model tracks the curated High bundle
        # without rewriting reproducible or manually overridden settings.
        if "model_type" not in ui_defaults:
            family_handler._apply_fresh_profile_defaults(ui_defaults)
