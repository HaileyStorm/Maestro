"""Bind one server-owned H3 task after source resolution, before WGP validation.

The caller owns request/continuation admission. Runtime image object identity
connects the preparse source snapshot to the already materialized task without
serializing images or treating a new pathname as access authority.
"""
from __future__ import annotations

import copy
import os
import re

from services.h3_adaptive_execution import bind_h3_execution_segment
from services.h3_prompt_mapping import source_prompt_schema
from services.h3_reference_binding import build_h3_reference_binding
from services.h3_reference_inputs import VIDEO_KEYS
from services.queue_recovery_runtime import QueueRecoveryRuntimeError


def has_h3_source_macros(source):
    """Detect WGP macro instructions outside balanced literal dialogue."""
    outside_dialogue = re.sub(r"<d>.*?</d>", "", str(source), flags=re.DOTALL)
    return any(line.strip().startswith("!") for line in outside_dialogue.splitlines())


def h3_source_templates_resolved(plan):
    """Read template resolution from a caller-validated source plan."""
    contracts = plan.get("source_contracts") if isinstance(plan, dict) else None
    if not isinstance(contracts, list) or not contracts:
        return False
    for contract in contracts:
        recipe = contract.get("source_canonicalization") if isinstance(contract, dict) else None
        if not isinstance(recipe, dict) or not (
            (recipe.get("mode") == "template" and recipe.get("recipe_version") == 1)
            or (recipe.get("mode") == "t2va" and recipe.get("recipe_version") == 2)
        ):
            return False
    return True


def require_h3_mapping_audio_roles(inputs):
    from services.h3_audio import source_audio_requested
    if source_audio_requested(inputs.get("custom_settings")):
        raise QueueRecoveryRuntimeError("Adaptive prompt mapping needs explicit support for experimental source-audio roles. Use a manual checkpoint for this audio mode.")
    if os.name != "posix":
        from services.h3_reference_inputs import selected_h3_video_slots
        audio_flags = inputs.get("audio_prompt_type") or ""
        selected_audio = "K" not in audio_flags and any(
            flag in audio_flags and inputs.get(key)
            for flag, key in zip("ABC", ("audio_guide", "audio_guide2", "audio_guide3"))
        )
        selected_video = selected_h3_video_slots(
            inputs.get("video_prompt_type"), [inputs.get(key) for key in VIDEO_KEYS],
        )
        if selected_audio or selected_video:
            raise QueueRecoveryRuntimeError("Adaptive prompt mapping with video or audio references is not available on this platform yet. Use a manual checkpoint for these references.")


def resolve_h3_mapping_source_plan(longform):
    """Validate optional execution formatting against the immutable source plan."""
    from services.h3_execution_contract import rewrite_h3_execution_prompts
    if not isinstance(longform, dict) or "prompt_mapping_version" not in longform:
        return None
    if type(longform["prompt_mapping_version"]) is not int or longform["prompt_mapping_version"] != 1:
        raise QueueRecoveryRuntimeError("H3 prompt mapping version is unsupported.")
    source = longform.get("shot_plan")
    if not isinstance(source, dict):
        raise QueueRecoveryRuntimeError("H3 mapping source plan is missing.")
    derived = longform.get("prompt_mapping_source_plan")
    if derived is None:
        return source
    if not isinstance(derived, dict):
        raise QueueRecoveryRuntimeError("H3 mapping execution source is invalid.")
    expected = rewrite_h3_execution_prompts(
        source, derived.get("clip_prompts"), validate_prompt=lambda _index, _prompt: None,
    )
    if expected != derived:
        raise QueueRecoveryRuntimeError("H3 mapping execution source disagrees with the authored plan.")
    return expected


def prepare_h3_single_mapping_source(body, model_def, *, align_frame_count=None):
    """Freeze source only when an adaptive native task crosses prompt schemas."""
    from services.h3_lora_compat import architecture_for_h3_model
    from services.h3_shot_planner import plan_h3_native_shots, resolve_h3_source_template

    architecture = architecture_for_h3_model(body.get("model_type"))
    if architecture not in {"fl2va", "ref2va"} or body.get("h3_adaptive_conditioning", True) is False:
        return
    saved = body.get("_h3_prompt_mapping_source_plan")
    if saved is not None:
        from services.h3_execution_contract import validate_h3_execution_shots
        if validate_h3_execution_shots({"shot_plan": saved}, [body.get("prompt")], 1) is None:
            raise QueueRecoveryRuntimeError("H3 mapping source plan is invalid.")
        if body.get("video_length") != saved["clip_frames"][0]:
            raise QueueRecoveryRuntimeError("H3 mapping source frame geometry changed.")
        if not callable(align_frame_count) or align_frame_count(body["video_length"], model_def) != body["video_length"]:
            raise QueueRecoveryRuntimeError("H3 mapping source no longer matches the runtime frame grid.")
        return
    source = body.get("prompt")
    if source is None or (isinstance(source, str) and not source.strip()):
        return
    raw_family = source_prompt_schema(source, strict=False)
    # Macro definitions can generate the field names themselves. Dialogue is
    # literal; keep this detection tolerant so unchanged legacy text does not
    # acquire the mapper's stricter source validation.
    has_macros = has_h3_source_macros(source)
    if not has_macros and ((architecture == "fl2va" and raw_family != "ref2va") or (architecture == "ref2va" and raw_family == "ref2va")):
        return
    requested = architecture_for_h3_model(body.get("_h3_requested_checkpoint") or body.get("model_type"))
    if not has_macros and raw_family == "opaque" and requested == architecture:
        return
    resolved_source = resolve_h3_source_template(source)
    family = source_prompt_schema(resolved_source, strict=False)
    if family == "opaque":
        if requested == architecture:
            return
        raise QueueRecoveryRuntimeError("Adaptive H3 mapping needs unambiguous prompt fields; use a manual checkpoint or clarify the field structure.")
    if family == "ref2va" and architecture == "fl2va":
        raise QueueRecoveryRuntimeError("Authored Ref2VA text cannot be mapped to Base without its original Base source. Select Ref2VA or supply that source.")
    if (architecture == "fl2va" and family != "ref2va") or (architecture == "ref2va" and family == "ref2va"):
        return
    require_h3_mapping_audio_roles(body)
    frames = body.get("video_length")
    if type(frames) is not int or frames <= 0:
        raise QueueRecoveryRuntimeError("H3 mapping frame geometry is invalid.")
    if not callable(align_frame_count):
        raise QueueRecoveryRuntimeError("H3 mapping needs the runtime frame alignment contract.")
    frames = align_frame_count(frames, model_def)
    if type(frames) is not int or frames <= 0:
        raise QueueRecoveryRuntimeError("H3 mapping aligned frame geometry is invalid.")
    plan = plan_h3_native_shots(
        global_prompt=source, clip_frame_counts=[frames],
        fps=float(model_def.get("fps") or 24),
        source_canonicalization="t2va_template" if family != "ref2va" else None,
    )
    body["_h3_prompt_mapping_source_plan"] = plan
    body["video_length"] = frames
    body["prompt"] = plan["clip_prompts"][0]


def bind_h3_mapping_task(
    task, *, source_plan, segment_index, source_snapshot, initial_images,
    descriptors, validate_descriptor, has_audio, continuation=None,
    continuation_path=None, materialize_image=None,
    materialize_files=None, cleanup_files=None,
):
    """Return task and serializable sidecar copies after a complete transaction.

    ``continuation`` must already have been verified by the caller, including
    its predecessor dependency and exact content digest. The descriptor
    validator is still called for its projected reference input.
    """
    params = task.get("params")
    if not isinstance(params, dict) or not isinstance(source_snapshot, dict):
        raise QueueRecoveryRuntimeError("H3 mapping task source is missing.")
    if type(segment_index) is not int or segment_index < 0:
        raise QueueRecoveryRuntimeError("H3 mapping segment position is invalid.")
    prompts = source_plan.get("clip_prompts") if isinstance(source_plan, dict) else None
    if not isinstance(prompts, list) or segment_index >= len(prompts):
        raise QueueRecoveryRuntimeError("H3 mapping source plan is missing.")
    original = prompts[segment_index]
    if task.get("prompt") != original or params.get("prompt") != original:
        raise QueueRecoveryRuntimeError("H3 mapping task no longer matches its source prompt.")
    frames = source_plan.get("clip_frames")
    if not isinstance(frames, list) or type(params.get("video_length")) is not int or params["video_length"] != frames[segment_index]:
        raise QueueRecoveryRuntimeError("H3 mapping task frame geometry changed.")

    raw = copy.deepcopy(source_snapshot)
    baseline_images = raw.get("image_refs") or []
    current_images = params.get("image_refs") or []
    if not isinstance(baseline_images, list) or not isinstance(current_images, list):
        raise QueueRecoveryRuntimeError("H3 mapping image references are invalid.")
    if len(initial_images) != len(baseline_images) or len(current_images) < len(initial_images):
        raise QueueRecoveryRuntimeError("H3 mapping image references changed after loading.")
    if any(current_images[index] is not value for index, value in enumerate(initial_images)):
        raise QueueRecoveryRuntimeError("H3 mapping image references changed after loading.")

    # All non-image reference values remain paths across WGP materialization.
    for key in (*VIDEO_KEYS, "audio_guide", "audio_guide2", "audio_guide3",
                "video_prompt_type", "audio_prompt_type", "custom_settings"):
        raw[key] = copy.deepcopy(params.get(key))
    selected_descriptors = list(descriptors)
    mode = continuation.get("mode") if isinstance(continuation, dict) else None
    if mode == "semantic_still":
        if not continuation_path or current_images[len(initial_images):] != [continuation_path]:
            raise QueueRecoveryRuntimeError("H3 mapping continuation image changed.")
        raw["image_refs"] = [*baseline_images, continuation_path]
        late_field = f"image_refs:{len(baseline_images)}"
    elif mode == "temporal_tail":
        if len(current_images) != len(initial_images):
            raise QueueRecoveryRuntimeError("H3 mapping received an unexpected image reference.")
        slot = continuation.get("video_slot")
        if type(slot) is not int or slot not in {1, 2, 3}:
            raise QueueRecoveryRuntimeError("H3 mapping continuation video slot is missing.")
        key = VIDEO_KEYS[slot - 1]
        if not continuation_path or raw.get(key) != continuation_path or source_snapshot.get(key) not in (None, ""):
            raise QueueRecoveryRuntimeError("H3 mapping continuation video changed.")
        late_field = f"{key}:0"
    else:
        if len(current_images) != len(initial_images):
            raise QueueRecoveryRuntimeError("H3 mapping received an unverified image reference.")
        late_field = None
    if mode == "last_frame":
        if not continuation_path or params.get("image_start") != continuation_path:
            raise QueueRecoveryRuntimeError("H3 mapping continuation frame changed.")
        raw["image_start"] = continuation_path
        raw["image_prompt_type"] = params.get("image_prompt_type")
    elif mode == "native_av_overlap":
        native = params.get("_h3_native_boundary")
        if not isinstance(native, dict) or any(native.get(key) != value for key, value in (
            ("path", continuation_path), ("sha256", continuation.get("sha256")),
            ("size", continuation.get("size")),
        )):
            raise QueueRecoveryRuntimeError("H3 mapping native continuation changed.")
        raw["_h3_native_boundary"] = copy.deepcopy(native)
    for key in ("_continuation", "_ref2va_continuation", "_h3_native_boundary_request"):
        if key not in params:
            raw.pop(key, None)
    if late_field is not None:
        selected_descriptors.append({
            "field": late_field, "path": continuation_path,
            "sha256": continuation.get("sha256"), "size": continuation.get("size"),
            "dependency": continuation.get("dependency"),
        })
    # Match the canonical path stored by the existing admission descriptor;
    # resolving it here never replaces validation of that exact descriptor.
    raw["image_refs"] = [os.path.realpath(path) for path in (raw.get("image_refs") or [])]
    for key in (*VIDEO_KEYS, "audio_guide", "audio_guide2", "audio_guide3"):
        if isinstance(raw.get(key), str) and raw[key]:
            raw[key] = os.path.realpath(raw[key])
    references = build_h3_reference_binding(
        raw, descriptors=selected_descriptors,
        validate_descriptor=validate_descriptor, has_audio=has_audio,
    )
    image_bindings = [item for item in references if item["type"] == "image"]
    if image_bindings and not callable(materialize_image):
        raise QueueRecoveryRuntimeError("H3 mapping needs content-pinned reference images.")
    pinned_images = [materialize_image(item) for item in image_bindings]
    mapped = bind_h3_execution_segment(
        source_plan, segment_index=segment_index,
        model_type=params.get("model_type"), reference_manifest=references,
        authored_ref=source_prompt_schema(original) == "ref2va",
    )
    prompt = mapped["record"]["mapped_prompt"]
    updated_params = dict(params)
    updated_params["prompt"] = prompt
    if image_bindings:
        updated_params["image_refs"] = pinned_images
    clip_info = params.get("multi_clip_info")
    if isinstance(clip_info, dict):
        updated_params["multi_clip_info"] = {
            **copy.deepcopy(clip_info), "prompt_mapping": copy.deepcopy(mapped["receipt"]),
        }
    updated = {**task, "prompt": prompt, "params": updated_params}
    if image_bindings and params.get("image_start") is None:
        updated["start_image_data"] = pinned_images
    if references:
        # Either preview can contain a reference image or video thumbnail
        # encoded before descriptor validation. Discard both cached views.
        for key in ("start_image_data_base64", "end_image_data_base64",
                    "start_image_labels", "end_image_labels"):
            updated[key] = None
    sidecar = {
        **raw, "prompt": prompt,
        "_h3_prompt_mapping_record": copy.deepcopy(mapped["record"]),
        "_h3_prompt_mapping_receipt": copy.deepcopy(mapped["receipt"]),
    }
    if isinstance(clip_info, dict):
        sidecar["multi_clip_info"] = copy.deepcopy(updated_params["multi_clip_info"])
    file_bindings = [item for item in references if item["type"] in {"video", "audio"}]
    if file_bindings:
        if not callable(materialize_files) or not callable(cleanup_files):
            raise QueueRecoveryRuntimeError("H3 mapping needs content-pinned reference files.")
        token = materialize_files(file_bindings)
        try:
            paths = token["paths"]
            if set(paths) != {item["source_key"] for item in file_bindings}:
                raise QueueRecoveryRuntimeError("H3 reference copies do not match the admitted inputs.")
            for item in file_bindings:
                path = paths[item["source_key"]]
                key, ordinal = item["source_key"].rsplit(":", 1)
                if ordinal != "0" or key not in (*VIDEO_KEYS, "audio_guide", "audio_guide2", "audio_guide3"):
                    raise QueueRecoveryRuntimeError("H3 reference copy slot is invalid.")
                if item.get("include_audio") is True:
                    try:
                        soundtrack_present = has_audio(path)
                    except InterruptedError:
                        raise
                    except Exception as error:
                        raise QueueRecoveryRuntimeError("Copied H3 reference soundtrack could not be verified.") from error
                    if soundtrack_present is not True:
                        raise QueueRecoveryRuntimeError("Copied H3 reference video has no verified soundtrack.")
                updated_params[key] = path
            updated["_h3_reference_files"] = token
        except BaseException:
            cleanup_files(token)
            raise
    return updated, sidecar
