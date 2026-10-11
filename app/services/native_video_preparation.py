"""Shared native window decisions, using already prepared media facts.

These are the existing WanGP calculations. They do not read model-call locals,
decode media, load models, grant execution authority, or attest an output.
Callers remain responsible for retaining the task, media and repeat seeds.
"""
from __future__ import annotations

import math
import os


def compute_sliding_window_no(current_video_length, sliding_window_size, discard_last_frames, reuse_frames):
    left_after_first_window = current_video_length - sliding_window_size + discard_last_frames
    return 1 + math.ceil(left_after_first_window / (sliding_window_size - discard_last_frames - reuse_frames))


def _validate_enhanced_prompt_cardinality(prompt, marker, authoritative_windows):
    """Validate one server-sealed enhanced prompt without exposing content."""
    valid_marker = (
        isinstance(marker, dict)
        and set(marker) == {"version", "projected_windows"}
        and marker.get("version") == 1
        and type(marker.get("projected_windows")) is int
        and marker["projected_windows"] > 1
    )
    if not valid_marker:
        raise ValueError("Enhanced prompt window contract is invalid")
    if type(authoritative_windows) is not int or authoritative_windows < 1:
        raise ValueError("Enhanced prompt window geometry is invalid")
    raw_lines = str(prompt or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blank_lines = sum(not line.strip() for line in raw_lines)
    received_windows = len(raw_lines) - blank_lines
    projected_windows = marker["projected_windows"]
    if projected_windows != authoritative_windows or received_windows != authoritative_windows or blank_lines:
        raise ValueError(
            "Enhanced prompt window count mismatch: "
            f"planned {projected_windows}, expected {authoritative_windows}, "
            f"received {received_windows}, blank {blank_lines}"
        )
    return [line.strip() for line in raw_lines]


def quantize_window_settings(size, overlap, discard, *, latent_size, overlap_default=0, cumulative_frames=None):
    if cumulative_frames is not None:
        size = cumulative_frames
    elif size != 0:
        size = (size - 1) // latent_size * latent_size + 1
    if overlap != 0 and overlap_default != overlap:
        overlap = (overlap - 1) // latent_size * latent_size + 1
    if discard != 0:
        discard = discard // latent_size * latent_size
    return size, overlap, discard


def resolve_fit_policy(fit_canvas, *, outpainting, progressive):
    if fit_canvas == 0:
        fit_canvas = 1
    fit_crop = fit_canvas == 2
    if fit_crop and outpainting:
        fit_crop = False
        fit_canvas = 1
    if progressive:
        fit_canvas = None
        fit_crop = False
    return fit_canvas, fit_crop


def adopt_media_canvas(current, fit_canvas, prepared, *, enabled=True):
    """Consume automatic canvas fitting at the first eligible prepared media."""
    if fit_canvas is not None and enabled:
        return prepared, None
    return current, fit_canvas


def source_window_geometry(length, size, overlap, *, latent_size, capable, has_source):
    reuse = min(size - latent_size, overlap) if capable else 0
    if capable and has_source:
        length += overlap - 1
    return length, reuse


def cap_length_to_audio(length, *, fps, duration, latent_size, unlimited):
    if unlimited:
        return length
    return min(int(fps * duration // latent_size) * latent_size + latent_size + 1, length)


def window_media_offsets(guide_start, length, *, window_index, source_overlap,
                         reuse, source_frames, timeline_aligned):
    window_start = guide_start - (reuse if window_index > 1 else source_overlap)
    guide_end = guide_start + length - (source_overlap if window_index == 1 else reuse)
    shift = source_frames if timeline_aligned else 0
    return window_start, guide_end, shift, guide_start - shift, guide_end - shift, window_start - shift


def select_sliding_window(current_video_length, size, *, capable):
    if capable:
        return current_video_length > size, size
    return False, current_video_length


def initial_window_geometry(length, size, discard, reuse, *, sliding):
    if sliding:
        return compute_sliding_window_no(length, size, discard, reuse), size
    return 1, length


def prepare_window_prompts(prompt, prompts, *, sliding, prompt_mode, base_model_type,
                           enhanced_marker, total_windows, total_frames, fps, size, discard, reuse):
    if enhanced_marker is not None:
        if not sliding or prompt_mode != 1 or str(base_model_type).startswith("minimax_h3"):
            raise ValueError("Enhanced prompt window contract is out of scope")
        prompts = _validate_enhanced_prompt_cardinality(prompt, enhanced_marker, total_windows)
    timeline = False
    if sliding and prompt_mode == 2:
        from shared.utils.prompt_parser import build_global_timeline_window_prompts
        mapped = build_global_timeline_window_prompts(
            prompt, total_frames=total_frames, fps=fps, window_size=size,
            discard_last_frames=discard, reuse_frames=reuse,
        )
        if mapped is not None:
            prompts = mapped
            timeline = True
    return prompts, timeline


def select_window_prompt(prompts, index, *, strict):
    if strict:
        return prompts[index]
    return prompts[index] if index < len(prompts) else prompts[-1]


def next_window_frame_count(current, *, sliding, window_index, requested, generated,
                            size, discard, reuse, latent_size):
    """Return None at the existing latent-tail stop, otherwise native frames."""
    if sliding and window_index > 0:
        if requested - generated < latent_size:
            return None
        return min(size, ((requested - generated + reuse + discard) // latent_size) * latent_size + 1)
    return current


def control_window_geometry(current, control_frames, *, window_index, overlap, cat_prefix, trim):
    """Apply the actual prepared control's availability and short-tail rules."""
    if control_frames is None or window_index > 1 and control_frames <= overlap and cat_prefix:
        return None, False
    if trim and control_frames < current:
        return control_frames, True
    return current, False


def prepared_window_result(length, canvas, *, repeat_index, window_index, seed,
                           fps, prompt, direct_frames, align_frames, cumulative_frames=None):
    """Freeze the real preparation result before adapter load and denoising.

    Canvas and length already include decoded controls, fitted stills, audio
    caps and predecessor overlap. This makes the existing final sampler-frame
    decision once; it does not predict future generated-prefix windows.
    """
    frames = (length if direct_frames else align_frames(length)
              if cumulative_frames is None else cumulative_frames(length))
    return {"repeat_index": repeat_index, "window_index": window_index,
            "seed": seed, "frames": frames, "height": canvas[0],
            "width": canvas[1], "fps": fps, "prompt": prompt}


def native_lora_phases(model_def, guidance_phases):
    """Use the native model capability for scheduled multiplier parsing."""
    guidance_phases = max(guidance_phases, 1)
    return guidance_phases, max(int(model_def.get("guidance_max_phases", guidance_phases) or 1), guidance_phases)


def append_native_lora_group(paths, strengths, *, steps, phases, model_switch_phase,
                             schedule=None, label="Loras"):
    """Merge one already selected adapter group with the installed parser.

    Callers keep catalog, handler, user and managed groups in their native
    order. Selection, file acquisition, loading and authority remain with the
    existing owners; this function only parses the complete strength schedule.
    """
    from shared.utils.loras_mutipliers import parse_loras_multipliers

    choices, schedule, errors = parse_loras_multipliers(
        strengths, len(paths), steps, nb_phases=phases,
        merge_slist=schedule, model_switch_phase=model_switch_phase,
    )
    if len(errors) > 0:
        raise ValueError(f"Error parsing {label}: {errors}")
    return choices, schedule


def normalize_outpainting_dims(outpainting_dims) -> list[float] | None:
    if outpainting_dims is None:
        return None
    if isinstance(outpainting_dims, str):
        outpainting_dims = outpainting_dims.strip()
        if not outpainting_dims or outpainting_dims.startswith("#"):
            return None
        outpainting_dims = outpainting_dims.split()
    if (
        not isinstance(outpainting_dims, (list, tuple))
        or len(outpainting_dims) != 4
    ):
        return None
    dims = [max(0.0, float(value)) for value in outpainting_dims]
    return dims if any(dims) else None


def select_wan_transformer_loras(get_model_recursive_prop, locate_file, base_model_type, model_type, video_prompt_type, model_mode, **kwargs):
    """Select the native Wan extras without constructing a model wrapper."""
    if base_model_type == "animate":
        if "#" in video_prompt_type and "1" in video_prompt_type:
            preloadURLs = get_model_recursive_prop(model_type,  "preload_URLs")
            if len(preloadURLs) > 0:
                return [locate_file(os.path.basename(preloadURLs[0]))] , [1]
    elif base_model_type == "vace_ditto_14B":
        preloadURLs = get_model_recursive_prop(model_type,  "preload_URLs")
        model_mode = int(model_mode)
        if len(preloadURLs) > model_mode:
            return [locate_file(os.path.basename(preloadURLs[model_mode]))] , [1]
    return [], []


def select_ltx_video_transformer_loras(get_model_recursive_prop, locate_file, model_type, video_prompt_type, **kwargs):
    """Select the native LTX-video control adapters in their existing order."""
    map = {
        "P" : "pose",
        "D" : "depth",
        "E" : "canny",
    }
    loras = []
    preloadURLs = get_model_recursive_prop(model_type,  "preload_URLs")
    lora_file_name = ""
    for letter, signature in map.items():
        if letter in video_prompt_type:
            for file_name in preloadURLs:
                if signature in file_name:
                    loras += [ locate_file(os.path.basename(file_name))]
                    break
    loras_mult = [1.] * len(loras)
    return loras, loras_mult


def select_ltx2_transformer_loras(get_model_recursive_prop, model_type, video_prompt_type,
                                 *, base_model_type, model_def, arch_spec, locate_file,
                                 resolve_lora_directory_file, resolve_id_lora_file,
                                 resolve_outpaint_lora_file, **kwargs):
    """Select native LTX2 extras with caller-owned dependency resolution.

    Runtime callers supply the existing acquisition functions; planning callers
    supply local resolution only. Neither route needs a loaded transformer.
    """
    control_map = {
        "O": "pose_align",
        "P": "pose",
        "D": "depth",
        "T": "depth_temporal",
        "E": "canny",
    }
    loras = []
    loras_mult = []
    video_prompt_type = video_prompt_type or ""
    resolved_base_model_type = base_model_type
    official_outpaint_stack = bool(
        kwargs.get("outpaint_official_stack", False)
    )

    if (
        official_outpaint_stack
        and model_def.get("ltx2_pipeline", "two_stage")
        != "distilled"
    ):
        distilled_filename = (
            "ltx-2.3-22b-distilled-lora-384-1.1.safetensors"
        )
        distilled_path = resolve_lora_directory_file(
            distilled_filename,
            "Lightricks/LTX-2.3",
        )
        if distilled_path and os.path.isfile(distilled_path):
            loras.append(distilled_path)
            loras_mult.append("0.5;0.5")
            print(
                "[LTX2] Official Outpaint stack: loaded Distilled "
                f"1.1 LoRA {distilled_path} "
                "(strength=0.5 in both passes)."
            )
        else:
            raise FileNotFoundError(
                "The official LTX-2.3 Outpaint workflow requires "
                f"{distilled_filename}."
            )

    # ID-LoRA: voice-identity LoRA — auto-load when the caller provided a
    # voice_reference. Lives BEFORE the distilled-only early return so it
    # works on dev models too (which return early below with no other
    # auto-loaded LoRAs). Per upstream WanGP v11.77, the CelebVHQ ID-LoRA
    # works on both dev (two-stage) and distilled pipelines despite being
    # trained primarily on dev.
    #
    # Multiplier convention matches upstream:
    #   - distilled (single-phase): scalar 1.0
    #   - dev (two-phase): "1;0" — phase 1 only. Phase 2 in two_stage
    #     doesn't process audio so the LoRA has nothing to amplify there.
    voice_reference = kwargs.get("voice_reference")
    if voice_reference:
        spec = arch_spec
        id_lora_filename = spec.get("id_lora", "")
        if id_lora_filename:
            id_lora_path = resolve_id_lora_file(id_lora_filename)
            if id_lora_path and os.path.isfile(id_lora_path):
                is_distilled = model_def.get("ltx2_pipeline", "two_stage") == "distilled"
                id_lora_mult = 1.0 if is_distilled else "1;0"
                loras.append(id_lora_path)
                loras_mult.append(id_lora_mult)
                print(f"[LTX2] Auto-loaded ID-LoRA: {id_lora_path} "
                      f"(multiplier={id_lora_mult}, pipeline={'distilled' if is_distilled else 'two_stage'})")
            else:
                print(f"[LTX2] WARNING: ID-LoRA unavailable — voice-reference will produce noise without it")

    pending_outpaint = str(
        kwargs.get("video_guide_outpainting") or ""
    ).strip()
    dev_outpaint_active = False
    if pending_outpaint and not pending_outpaint.startswith("#"):
        try:
            pending_dims = [
                float(value)
                for value in pending_outpaint.split()
            ]
            dev_outpaint_active = (
                len(pending_dims) == 4
                and any(value > 0 for value in pending_dims)
            )
        except (TypeError, ValueError):
            dev_outpaint_active = False
    if (
        model_def.get("ltx2_pipeline", "two_stage") != "distilled"
        and not dev_outpaint_active
    ):
        # Keep Dev's existing auto-LoRA behavior, except that Outpaint
        # must load its official system LoRA on both LTX-2.3 pipelines.
        return loras, loras_mult
    preload_urls = get_model_recursive_prop(model_type, "preload_URLs") or []

    # Decide whether outpaint LoRA should load. Trigger: non-empty outpainting
    # dims (either passed explicitly or parsed from the video_guide_outpainting
    # "top bottom left right" percentage string). Replaces the old "O" letter
    # signal so outpainting works through the standard masked-gen pipeline.
    def _outpaint_dims_active():
        dims = kwargs.get("outpainting_dims")
        if normalize_outpainting_dims(dims) is not None:
            return True
        vgo = str(kwargs.get("video_guide_outpainting") or "").strip()
        if not vgo or vgo.startswith("#"):
            return False
        try:
            parts = [float(v) for v in vgo.split()]
        except (TypeError, ValueError):
            return False
        return len(parts) == 4 and any(p > 0 for p in parts)

    mask_preserving_outpaint = bool(
        kwargs.get("outpaint_mask_preserve", False)
    )
    if resolved_base_model_type == "ltx2_22B":
        if any(letter in video_prompt_type for letter in control_map):
            for file_name in preload_urls:
                if "union-control" in os.path.basename(file_name):
                    loras.append(locate_file(os.path.basename(file_name)))
                    loras_mult.append(1.0)
                    break
        if _outpaint_dims_active():
            spec = arch_spec
            if mask_preserving_outpaint:
                outpaint_filename = spec.get("inpaint_ic_lora", "")
                outpaint_repo = (
                    "Lightricks/"
                    "LTX-2.3-22b-IC-LoRA-In-Outpainting"
                )
                outpaint_label = "mask-preserving in/outpainting"
                outpaint_multiplier = 1.0
            else:
                outpaint_filename = spec.get("outpaint_ic_lora", "")
                outpaint_repo = (
                    "oumoumad/LTX-2.3-22b-IC-LoRA-Outpaint"
                )
                outpaint_label = "legacy outpaint"
                try:
                    outpaint_multiplier = float(
                        kwargs.get("outpaint_lora_strength", 1.0)
                        or 1.0
                    )
                except (TypeError, ValueError):
                    outpaint_multiplier = 1.0
                outpaint_multiplier = max(
                    0.0,
                    min(2.0, outpaint_multiplier),
                )
            if outpaint_filename:
                outpaint_path = resolve_outpaint_lora_file(
                    outpaint_filename, outpaint_repo, outpaint_label, mask_preserving_outpaint)
                if outpaint_path and os.path.isfile(outpaint_path):
                    _op_mult = (
                        # Lightricks' current two-stage graph keeps the
                        # In/Outpaint IC-LoRA on the transformer while
                        # refining the decoded-pixel handoff. Guide tokens
                        # are cropped before pass two; the adapter remains.
                        "1;1"
                        if official_outpaint_stack
                        else outpaint_multiplier
                    )
                    loras.append(outpaint_path)
                    loras_mult.append(_op_mult)
                    print(
                        f"[LTX2] Loaded {outpaint_label} IC-LoRA: "
                        f"{outpaint_path} (strength={_op_mult})"
                    )
                else:
                    message = (
                        f"{outpaint_label.title()} IC-LoRA "
                        f"{outpaint_filename} is unavailable."
                    )
                    if official_outpaint_stack:
                        raise FileNotFoundError(message)
                    print(
                        f"[LTX2] WARNING: {message} "
                        "Generation will proceed without it."
                    )
    else:
        for letter, signature in control_map.items():
            if letter in video_prompt_type:
                for file_name in preload_urls:
                    if signature in file_name:
                        loras.append(locate_file(os.path.basename(file_name)))
                        loras_mult.append(1.0)
                        break
    return loras, loras_mult


def select_existing_ltx2_transformer_loras(get_model_recursive_prop, model_type,
                                         video_prompt_type, *, base_model_type,
                                         model_def, arch_spec, lora_directory,
                                         locate_file, resolve_lora_path, **kwargs):
    """Resolve the same LTX2 stack from dependencies already installed.

    Missing selected dependencies need their normal acquisition owner before
    planning can resume. This path never downloads or migrates a model file.
    ID-LoRA stays in the primary LoRA directory, as required by native injection.
    """
    def require_file(path):
        if not path or not os.path.isfile(path):
            raise FileNotFoundError("A selected LTX2 adapter is not installed.")
        return path

    return select_ltx2_transformer_loras(
        get_model_recursive_prop, model_type, video_prompt_type,
        base_model_type=base_model_type, model_def=model_def, arch_spec=arch_spec,
        locate_file=lambda filename: require_file(locate_file(filename)),
        resolve_lora_directory_file=lambda filename, repo: require_file(
            resolve_lora_path(model_type, filename)),
        resolve_id_lora_file=lambda filename: require_file(
            os.path.join(lora_directory, filename)),
        resolve_outpaint_lora_file=lambda filename, repo, label, mask: require_file(
            locate_file(filename, error_if_none=False)), **kwargs)
