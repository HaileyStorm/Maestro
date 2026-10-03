"""Private server-worker H3 cumulative admission and task contracts.

No public control is introduced. Source authority comes from the authorized
job's private request manifest; media/AV authority still requires the physical
project and actual loaded runtime. No tensor enters these task dictionaries.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

from services.h3_cumulative_dispatch import validate_h3_cumulative_settings
from services.h3_cumulative_plan import plan_h3_cumulative_chain
from services.h3_cumulative_queue import H3CumulativeQueueDispatch, H3QueueAuthority
from services.h3_cumulative_recovery import _MAX_HEADER_BYTES
from services.h3_native_continuation import (
    H3NativeContinuationStep,
    latent_frames_for_video_frames,
)
from services.queue_recovery_runtime import (
    MANIFEST_DIRECTORY,
    MAX_MANIFEST_BYTES,
    QueueRecoveryRuntimeError,
    _private_directory_identity,
    _read_exact_file,
    _validated_project_root,
    _verify_directory_identity,
    ensure_recovery_staging_directory,
    replay_concat_to_stable_output,
    sha256_file,
)


def staged_h3_cumulative_descriptor(project, basename, producer_unit_id):
    """Verify a private staged component against its direct-child sidecar.

    This transient descriptor has the ordinary media receipt shape. It must be
    rebuilt against promoted media before entering a durable recovery cursor.
    """
    root = _validated_project_root(project)
    if (
        type(basename) is not str
        or not basename.startswith("unit-")
        or Path(basename).name != basename
        or Path(basename).suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov"}
        or type(producer_unit_id) is not str
        or re.fullmatch(r"unit:v1:[0-9a-f]{64}", producer_unit_id) is None
    ):
        raise QueueRecoveryRuntimeError("Staged H3 component identity is invalid.")
    recovery = root / MANIFEST_DIRECTORY
    staging = recovery / "staging"
    identities = [
        (path, _private_directory_identity(path)) for path in (recovery, staging)
    ]
    sidecar_name = str(Path(basename).with_suffix(".meta.json"))
    sidecar = root / sidecar_name
    raw = _read_exact_file(sidecar, maximum_bytes=MAX_MANIFEST_BYTES)
    try:
        meta = json.loads(raw)
    except ValueError:
        raise QueueRecoveryRuntimeError(
            "Staged H3 component sidecar is invalid."
        ) from None
    size, digest = sha256_file(staging / basename)
    if (
        type(meta) is not dict
        or meta.get("output_filename") != basename
        or meta.get("producer_unit_id") != producer_unit_id
        or meta.get("producer_unit_kind") != "h3_segment"
        or meta.get("producer_unit_artifact_names") != [basename]
        or meta.get("producer_artifact_class") != "component"
        or meta.get("artifact_class") != "component"
        or meta.get("private") is not True
        or meta.get("producer_media_size") != size
        or meta.get("producer_media_sha256") != digest
    ):
        raise QueueRecoveryRuntimeError("Staged H3 component evidence changed.")
    for path, identity in identities:
        _verify_directory_identity(path, identity)
    return {
        "basename": basename,
        "producer_unit_id": producer_unit_id,
        "sha256": digest,
        "sidecar_basename": sidecar_name,
        "sidecar_sha256": hashlib.sha256(raw).hexdigest(),
        "sidecar_size": len(raw),
        "size": size,
    }


def validate_staged_h3_cumulative_descriptor(project, descriptor, *, producer_unit_id):
    if type(descriptor) is not dict:
        return False
    try:
        rebuilt = staged_h3_cumulative_descriptor(
            project, descriptor.get("basename"), producer_unit_id
        )
    except (OSError, QueueRecoveryRuntimeError):
        return False
    return descriptor == rebuilt


def h3_cumulative_request(params):
    if "_h3_cumulative_append" not in params:
        return False
    if type(params["_h3_cumulative_append"]) is not bool:
        raise ValueError("Private H3 cumulative selection must be a boolean.")
    return params["_h3_cumulative_append"]


def prepare_h3_cumulative_request(params, *, require_gate=True):
    """Derive a plan from trusted source parameters without changing them."""
    if not h3_cumulative_request(params):
        return None
    if require_gate and os.environ.get("MAESTRO_H3_CUMULATIVE_EXPERIMENTAL") != "1":
        raise ValueError("Private H3 cumulative generation is disabled.")
    if (
        params.get("model_type") != "minimax_h3"
        or params.get("sfx_mode")
        or params.get("generation_mode", "video") != "video"
    ):
        raise ValueError("Private cumulative generation requires native FL2VA video.")
    resolution = params.get("resolution")
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", resolution or "")
    if match is None:
        raise ValueError(
            "Private cumulative generation requires an explicit video canvas."
        )
    width, height = map(int, match.groups())
    if any(value % 32 or not 32 <= value <= 8192 for value in (width, height)):
        raise ValueError(
            "Private cumulative canvas must use multiples of 32 through 8192."
        )
    repeats = params.get("repeat_generation", 1)
    if type(repeats) is not int or not 1 <= repeats <= 32:
        raise ValueError("Private cumulative output count must be from 1 through 32.")
    if params.get("h3_native_boundary_conditioning") or params.get("_h3_longform"):
        raise ValueError(
            "Private cumulative generation cannot reuse another H3 continuation plan."
        )
    # Recovery uses the same exclusions without enabling the generation gate.
    admission = dict(
        params,
        video_length=22,
        repeat_generation=1,
        prompt="admission",
        multi_prompts_gen_type=2,
    )
    admission.setdefault("batch_size", 1)
    validate_h3_cumulative_settings(admission, frames=22)
    if params.get("prompt_enhancer") not in (None, ""):
        raise ValueError(
            "Private cumulative generation requires the sealed authored prompt."
        )
    if (
        params.get("voice_clone_enabled")
        or params.get("tts_dynaudnorm")
        or params.get("delivery_resolution")
    ):
        raise ValueError(
            "Private cumulative generation requires unchanged native AV delivery."
        )
    custom = params.get("custom_settings")
    if custom is not None and type(custom) is not dict:
        raise ValueError("Private cumulative custom settings must be an object.")
    if (custom or {}).get("h3_native_boundary_conditioning"):
        raise ValueError(
            "Private cumulative generation cannot use a decoded boundary guide."
        )
    plan = plan_h3_cumulative_chain(
        global_prompt=params.get("prompt"),
        requested_frames=params.get("video_length"),
        first_window_frames=params.get("sliding_window_size") or 345,
    )
    # Mirror the loaded native model's existing output-allocation limits before
    # task creation/model loading. Full cumulative geometry includes terminal
    # grid padding, even though publication trims that padding after decoding.
    # These counts do not estimate VAE activations or total process/device peak.
    for window in plan["windows"]:
        frames = window["cumulative_generated_frames"]
        audio_ticks = window["cumulative_generated_audio_ticks"]
        retained_bytes = 4 * (
            24 * latent_frames_for_video_frames(frames) * (height // 16) * (width // 16)
            + 2 * 32 * audio_ticks
        )
        if retained_bytes > 512 * 1024 * 1024:
            raise ValueError(
                "H3 cumulative retained AV state exceeds the 512 MiB allocation limit."
            )
        decoded_bytes = 4 * (3 * frames * height * width + 2 * audio_ticks * 800)
        if decoded_bytes > 2 * 1024 * 1024 * 1024:
            raise ValueError(
                "H3 cumulative full decode exceeds the private 2 GiB output limit. "
                "Use a shorter chain or a smaller canvas."
            )
    return plan


def require_h3_cumulative_checkpoint_capacity(
    project_directory, plan, authority, *, window_index, remaining_variants
):
    """Screen remaining checkpoint writes; free space is not a reservation.

    The worker supplies its first unverified window after skipping verified
    completed units. Earlier windows/variants already occupy disk and need no
    new allocation. Future windows/variants are conservatively counted in full.
    Encoded media, final copies and retained crash/retry orphans are not estimated.
    """
    windows = plan["windows"]
    if (
        type(window_index) is not int
        or not 0 <= window_index < len(windows)
        or type(remaining_variants) is not int
        or not 1 <= remaining_variants <= 32
    ):
        raise QueueRecoveryRuntimeError(
            "H3 cumulative pending checkpoint range is invalid."
        )
    sizes = [
        4
        * (
            24
            * latent_frames_for_video_frames(window["cumulative_generated_frames"])
            * (authority.height // 16)
            * (authority.width // 16)
            + 2 * 32 * window["cumulative_generated_audio_ticks"]
        )
        + _MAX_HEADER_BYTES
        + 8
        for window in windows
    ]
    required = sum(sizes[window_index:]) + (remaining_variants - 1) * sum(sizes)
    staging = Path(ensure_recovery_staging_directory(project_directory))
    identity = _private_directory_identity(staging)
    try:
        available = shutil.disk_usage(staging).free
        _verify_directory_identity(staging, identity)
    except OSError:
        raise QueueRecoveryRuntimeError(
            "Project storage capacity could not be checked. "
            "Make the project storage available and retry."
        ) from None
    if available < required:
        missing_mib = (required - available + 1024**2 - 1) // 1024**2
        raise ValueError(
            "There is not enough free space for the remaining generation "
            f"checkpoints. Free at least {missing_mib} MiB more in the project's "
            "storage before retrying; encoded media needs additional space."
        )
    return {"required_checkpoint_bytes": required, "available_bytes": available}


def h3_cumulative_authority(job, plan, variant):
    if type(variant) is not int or not 0 <= variant < job["params"].get(
        "repeat_generation", 1
    ):
        raise QueueRecoveryRuntimeError("H3 cumulative output variant is invalid.")
    width, height = map(int, job["params"]["resolution"].split("x"))
    chain = hashlib.sha256(
        json.dumps(
            [job["id"], plan["plan_sha256"], variant],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return H3QueueAuthority(
        job.get("_recovery_owner_digest"),
        job.get("_recovery_project_digest"),
        chain,
        job["id"],
        width,
        height,
    )


def h3_cumulative_settings(plan, authority, index):
    if type(index) is not int or not 0 <= index < len(plan["windows"]):
        raise QueueRecoveryRuntimeError("H3 cumulative window index is invalid.")
    window = plan["windows"][index]
    return {
        "discard_prefix_frames": 0,
        "native_boundary_conditioning": False,
        "generated_frames": window["cumulative_generated_frames"],
        "published_frames": window["cumulative_published_frames"],
        "trim_tail_frames": window["publication_trim_frames"],
        "cumulative_append": {
            "chain_id": authority.chain_id,
            "width": authority.width,
            "height": authority.height,
        },
        "cumulative_plan_sha256": plan["plan_sha256"],
        "cumulative_sampler_frames": window["sampler_frames"],
        "published_tail_frames": window["published_tail_frames"],
        "publication_slice": {
            "start_frame": window["absolute_publish_start_frame"],
            "end_frame_exclusive": window["absolute_publish_end_frame"],
        },
        "publication_prompt_sha256": window["publication_prompt_sha256"],
        "sampler_prompt_sha256": window["sampler_prompt_sha256"],
    }


def build_h3_cumulative_tasks(job, plan, allocate_task_id, *, execution_params=None):
    tasks = []
    source = job["params"]
    for variant in range(source.get("repeat_generation", 1)):
        authority = h3_cumulative_authority(job, plan, variant)
        for window in plan["windows"]:
            params = copy.deepcopy(
                source if execution_params is None else execution_params
            )
            for key in (
                "_h3_cumulative_append",
                "_h3_cumulative_plan",
                "per_clip_frames",
                "per_clip_prompts",
                "image_start",
                "image_end",
            ):
                params.pop(key, None)
            params.update(
                prompt=window["sampler_prompt"],
                video_length=window["sampler_frames"],
                multi_prompts_gen_type=2,
                repeat_generation=1,
                batch_size=1,
                sliding_window_size=window["sampler_frames"],
                trim_tail_frames=0,
                h3_adaptive_conditioning=False,
            )
            seed = source.get("seed", -1)
            if type(seed) is int and seed >= 0:
                params["seed"] = seed + variant
            params["multi_clip_info"] = {
                "group_id": f"h3_cumulative_{authority.chain_id}",
                "index": window["index"],
                "total": len(plan["windows"]),
                "output_index": variant,
                "output_total": source.get("repeat_generation", 1),
                "defer_concat": True,
                "progress_label": "Window",
                "cumulative_plan_sha256": plan["plan_sha256"],
                "generated_frames": window["cumulative_generated_frames"],
                "published_frames": window["cumulative_published_frames"],
                "trim_tail_frames": window["publication_trim_frames"],
            }
            tasks.append(
                {"id": allocate_task_id(), "params": params, "plugin_data": {}}
            )
    return tasks


def create_h3_cumulative_dispatch(
    plan, authority, index, project_directory, predecessor
):
    window = plan["windows"][index]
    if index:
        prior_settings = (
            dict(predecessor.get("settings") or {})
            if isinstance(predecessor, dict)
            else {}
        )
        for key in ("predecessor_artifact_hashes", "predecessor_continuation_sha256"):
            prior_settings.pop(key, None)
        if prior_settings != h3_cumulative_settings(plan, authority, index - 1):
            raise QueueRecoveryRuntimeError(
                "H3 cumulative predecessor disagrees with the trusted plan."
            )
        receipt = predecessor.get("continuation")
        dependency = predecessor.get("unit_id")
        step = H3NativeContinuationStep(**window["step"])
    else:
        if predecessor is not None:
            raise QueueRecoveryRuntimeError(
                "H3 cumulative capture cannot have a predecessor."
            )
        receipt = dependency = step = None
    return H3CumulativeQueueDispatch(
        frames=window["sampler_frames"],
        project_directory=project_directory,
        authority=authority,
        previous_receipt=receipt,
        previous_dependency=dependency,
        step=step,
    )


def copy_h3_cumulative_final(
    job,
    plan,
    variant,
    project_directory,
    terminal,
    *,
    enforce_audio,
    abort_check,
    prepare_publication=None,
):
    """Copy one already cumulative container, then verify audio on that copy.

    The common stable-output helper supplies atomic promotion; its callback
    performs no concatenation. The immutable snapshot and its seal survive
    audio-policy failure so recovery can retry publication without denoising.
    """
    artifacts = terminal.get("artifacts") or []
    if len(artifacts) != 1 or terminal.get("index") != len(plan["windows"]) - 1:
        raise QueueRecoveryRuntimeError("H3 cumulative terminal media is ambiguous.")
    artifact = artifacts[0]
    extension = os.path.splitext(artifact["basename"])[1].lower()
    if extension not in {".mp4", ".webm", ".mkv", ".mov"}:
        raise QueueRecoveryRuntimeError("H3 cumulative terminal media is not video.")
    # The stable name uses a content-free digest, never an authored prompt.
    tag = hashlib.sha256(str(job["id"]).encode()).hexdigest()[:24]
    output = f"cumulative_{tag}_v{variant + 1}{extension}"
    stats = {}

    def copy_container(paths, staging):
        import shutil

        abort_check()
        shutil.copyfile(paths[0], staging)
        size, digest = sha256_file(staging)
        if size != artifact["size"] or digest != artifact["sha256"]:
            raise QueueRecoveryRuntimeError(
                "H3 cumulative terminal media changed during copy."
            )
        abort_check()
        observed = enforce_audio(staging)
        if not isinstance(observed, dict) or observed.get("verified") is not True:
            raise QueueRecoveryRuntimeError("H3 cumulative final audio is unverified.")
        stats.update(observed)
        abort_check()
        if prepare_publication is not None:
            prepare_publication(output, staging, dict(stats))
        abort_check()
        return True

    class CopyCancelled(RuntimeError):
        pass

    def atomic_callback(paths, staging):
        try:
            return copy_container(paths, staging)
        except InterruptedError as error:
            # The common atomic helper translates OSError, which also includes
            # InterruptedError. Keep cancellation distinct from copy failure.
            raise CopyCancelled("H3 cumulative publication was cancelled") from error

    try:
        replay_concat_to_stable_output(
            project_directory,
            component_basenames=[artifact["basename"]],
            output_basename=output,
            concatenate=atomic_callback,
        )
    except CopyCancelled as error:
        raise InterruptedError(str(error)) from error
    return output, stats
