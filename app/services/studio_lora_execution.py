"""CPU-only projection of the plain Studio video LoRA execution contract."""
import json
import math


def studio_video_lora_model_applicable(model_type, model_def):
    return bool(
        isinstance(model_def, dict)
        and model_def.get("t2v_class") is True
        and model_def.get("sliding_window") is False
        and not any(model_def.get(key) for key in (
            "image_outputs", "audio_only", "any_audio_prompt", "i2v_class",
        ))
        and not str(model_type).startswith("minimax_h3")
        and not str(model_def.get("architecture", "")).startswith("minimax_h3")
    )


def project_studio_video_lora_execution(params, *, model_def, base_model_type, aligned_frames, validated=False):
    """Project caller-normalized settings; do not reproduce WanGP's parser."""
    if (not studio_video_lora_model_applicable(params.get("model_type"), model_def)
            or str(base_model_type).startswith("minimax_h3")):
        raise ValueError("This model does not support plain Studio video LoRA parameters")
    if type(params.get("image_mode")) is not int or params["image_mode"] != 0:
        raise ValueError("Video LoRA parameters require text-to-video generation")
    split = params.get("multi_prompts_gen_type", 0)
    if type(split) is not int or split not in (0, 2):
        raise ValueError("Video LoRA parameters require independent prompts or one multiline prompt")
    if any(params.get(key) for key in (
        "mode", "prompt_enhancer", "progressive_pipeline", "single_stage_pipeline", "reference_pipeline",
        "image_start", "image_end", "image_refs", "project_asset_refs", "video_source", "video_end", "video_guide",
        "image_guide", "image_mask", "video_mask", "custom_guide", "video_guide2", "video_guide3",
        "audio_guide", "audio_guide2", "audio_guide3", "audio_guide4", "audio_guide5", "audio_guide6", "audio_source",
        "audio_conditioning_guide", "voice_reference", "retake_video", "retake_masks_path",
        "image_prompt_type", "video_prompt_type", "audio_prompt_type", "multi_clip_info",
        "h3_native_boundary_conditioning", "_continuation",
        "_ref2va_continuation", "per_clip_frames", "_shot_prompts",
    )):
        raise ValueError("Video LoRA parameters currently require plain text-only inputs")
    numeric = {}
    for key in ("video_length", "num_inference_steps", "guidance_phases", "model_switch_phase"):
        value = params.get(key)
        if type(value) is not int or value < 0 or value > 10000:
            raise ValueError(f"Invalid video LoRA execution setting: {key}")
        numeric[key] = value
    if numeric["video_length"] < 1 or numeric["num_inference_steps"] < 1:
        raise ValueError("Video LoRA parameters require positive frames and inference steps")
    if type(aligned_frames) is not int or aligned_frames < 1:
        raise ValueError("Invalid aligned video frame count")
    maximum = model_def.get("guidance_max_phases", 0)
    if type(maximum) is not int or not 0 <= maximum <= 3 or numeric["guidance_phases"] > 3:
        raise ValueError("Invalid video LoRA phase capability")
    phases = numeric["guidance_phases"]
    validation_phases = (
        maximum if model_def.get("lock_guidance_phases") else min(numeric["guidance_phases"], maximum)
    )
    for key in ("switch_threshold", "switch_threshold2", "guidance_scale", "guidance2_scale", "guidance3_scale"):
        value = params.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"Invalid video LoRA execution setting: {key}")
        numeric[key] = value
    # validate_settings clamps a local phase count but does not return it in
    # override_inputs. Native dispatch retains the requested phase count.
    if not validated and validation_phases != 3:
        numeric["model_switch_phase"] = 1
    contract = {key: model_def.get(key) for key in (
        "architecture", "t2v_class", "sliding_window", "guidance_max_phases",
        "lock_guidance_phases", "multiple_submodels", "frames_minimum", "frames_maximum",
        "frames_steps", "latent_size", "frame_alignment_modulus", "frame_alignment_remainder",
        "frame_alignment_mode", "custom_settings",
    )}
    return json.loads(json.dumps({
        "version": 1, "model_type": params["model_type"], "base_model_type": base_model_type,
        "image_mode": 0, "multi_prompts_gen_type": split,
        "settings": numeric, "aligned_frames": aligned_frames, "window_count": 1,
        "validation_guidance_phases": validation_phases,
        "native_guidance_phases": max(phases, 1),
        "native_multiplier_phases": max(maximum or 1, phases, 1),
        "activated_loras": params.get("activated_loras"),
        "loras_multipliers": params.get("loras_multipliers", ""),
        "custom_settings": params.get("custom_settings", {}), "model_contract": contract,
    }, allow_nan=False))
