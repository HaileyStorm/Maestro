"""Server-side Director pipeline.

Orchestrates the full Director flow (LLM planning → image gen → video gen)
in a background thread so it can run without the browser being open.

Supports two planning backends:
  - Legacy: direct calls to llm_service (old monolithic approach)
  - New:    DirectorOrchestrator with layered architecture (planners → renderers → validators)

Controlled by feature flags in params or server config.
"""

import os
import re
import sys
import time
import json
import uuid
import math
import hashlib
import threading
import traceback
from functools import wraps
from typing import Optional

from services.job_lifecycle import (
    GENERATED_MEDIA_EXTENSIONS,
    request_cancel,
    snapshot_job,
)
from services.director_model_compat import (
    DIRECTOR_PIPELINE_TYPES,
    assess_director_model,
)
from services.director_video_strategy import (
    SHOT_IMAGE_GENERATE,
    SHOT_IMAGE_PROMPT_ONLY,
    SHOT_IMAGE_POLICIES,
    SHOT_IMAGES_DIRECT_REFERENCES,
    resolve_shot_image_policy,
    shot_images_required,
)

# These will be set by launch.py on startup
_jobs: dict = None          # reference to launch._jobs
_run_generation = None      # reference to launch._run_generation
_wgp = None                 # reference to wgp module
_gen_lock = None            # reference to launch._gen_lock
_active_gen_states = None   # reference to launch._active_gen_states (abort signaling)
_recovery_register_parent = None
_recovery_prepare_parent_state = None
_recovery_checkpoint_parent = None
_recovery_prepare_parent_delete = None
_recovery_remove_parent = None
_recovery_submit_child = None
_recovery_verify_child = None
_recovery_validate_child = None
_runtime_admission = None

_pipelines: dict = {}
_pipeline_lock = threading.Lock()
_pipeline_file_lock = threading.RLock()
_pipeline_threads: dict[str, threading.Thread] = {}
_pipeline_child_jobs: dict[str, set[str]] = {}
_pipeline_starting: set[str] = set()
_pipeline_operations: set[str] = set()
_pipeline_deleting: set[str] = set()
_pipeline_repairs: dict[str, dict] = {}
# Ephemeral only: these objects are deliberately excluded from pipeline JSON,
# recovery journals, and public state.  A pass token binds callbacks to the
# exact live pipeline generation that created them, so a late callback from an
# older request cannot publish into a resumed/replaced pipeline with the same
# short id.
_pipeline_llm_contexts: dict[str, dict] = {}
_pipeline_llm_tokens: dict[str, object] = {}
_REPAIR_ACTIVE_STATUSES = {"queued", "running", "cancelling"}
_GENERATION_SETTLE_GRACE_S = 10.0
_DIRECTOR_LLM_PARTIAL_LIMIT = 8192
_DIRECTOR_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_CANCELLED_ARTIFACT_FIELDS = {
    "output_files",
    "clip_images",
    "_clip_keyframes",
    "_clip_video_files",
    "_clip_timings",
}
_DIRECTOR_PIPELINE_FAILED_CODE = "director_pipeline_failed"
_DIRECTOR_PIPELINE_FAILED_MESSAGE = "Director generation stopped after an internal error."
_DIRECTOR_REPAIR_FAILED_CODE = "director_repair_failed"
_DIRECTOR_REPAIR_FAILED_MESSAGE = "Director repair stopped after an internal error."
_DIRECTOR_WORKER_FAILED_CODE = "director_worker_start_failed"
_DIRECTOR_WORKER_FAILED_MESSAGE = "Director could not start its worker."


class _DirectorLlmCancelled(RuntimeError):
    """Internal content-free signal for Stop winning an LLM pass."""


def _fresh_explicit_guidance_decision(params: dict) -> bool:
    """Authorize explicit LLM guidance for one new Director request.

    The decision is made once before the first durable state write. Recovery
    reuses the persisted literal boolean instead of mixing a saved request with
    whatever Mature Mode/provider is configured after a restart.
    """
    if params.get("explicit_output") is not True or _wgp is None:
        return False
    from services.mature_policy import mature_mode_allowed
    services = getattr(_wgp, "server_config", {}).get("services", {})
    return mature_mode_allowed(services)


def _normalize_explicit_guidance_snapshot(params: dict) -> dict:
    """Fail closed for missing, legacy, or non-boolean recovery metadata."""
    from services.director.nsfw_guidance import EXPLICIT_GUIDANCE_SNAPSHOT_KEY
    params[EXPLICIT_GUIDANCE_SNAPSHOT_KEY] = (
        params.get(EXPLICIT_GUIDANCE_SNAPSHOT_KEY) is True
    )
    return params


def _explicit_guidance_from_snapshot(params: dict) -> bool:
    from services.director.nsfw_guidance import EXPLICIT_GUIDANCE_SNAPSHOT_KEY
    return params.get(EXPLICIT_GUIDANCE_SNAPSHOT_KEY) is True


def _director_failure_details(exc: BaseException, *, code: str) -> dict:
    """Build a path/content-free failure record for persisted public state."""
    try:
        from services.oom_detect import (
            build_failure_details,
            normalize_failure_details,
        )
        embedded = getattr(exc, "failure_details", None)
        if isinstance(embedded, dict):
            return normalize_failure_details(embedded)
        return build_failure_details(exc, stage="generation", code=code)
    except Exception:
        return {
            "code": code,
            "stage": "generation",
            "detail": "Generation failed.",
            "exception_type": "Exception",
            "is_oom": False,
        }


class PipelineBusyError(RuntimeError):
    """Raised when a Dashboard mutation conflicts with active pipeline work."""


class DirectorModelCompatibilityError(ValueError):
    """Raised before Director submits work to an incompatible model."""


class DirectorChildGenerationError(RuntimeError):
    """Safe child failure retaining its structured resource diagnosis."""

    def __init__(
        self,
        message: str,
        *,
        failure_details: dict | None = None,
        oom_info: dict | None = None,
    ):
        super().__init__(message)
        self.failure_details = (
            dict(failure_details) if isinstance(failure_details, dict) else None
        )
        self.oom_info = dict(oom_info) if isinstance(oom_info, dict) else None


def _director_model_assessment(model_type: str) -> tuple[dict, dict] | None:
    """Resolve a model and its data-driven Director capabilities."""
    getter = getattr(_wgp, "get_model_def", None)
    if not callable(getter):
        return None
    model_def = getter(model_type)
    if not model_def:
        raise DirectorModelCompatibilityError(
            f"Director model '{model_type}' is not available. Choose another model.",
        )
    family_getter = getattr(_wgp, "get_model_family", None)
    architecture_getter = getattr(_wgp, "get_base_model_type", None)
    try:
        family = family_getter(model_type, for_ui=True) if callable(family_getter) else ""
    except Exception:
        family = ""
    try:
        architecture = architecture_getter(model_type) if callable(architecture_getter) else ""
    except Exception:
        architecture = ""
    return model_def, assess_director_model(
        model_type,
        model_def,
        family=family,
        architecture=architecture,
    )


def _director_visual_reference_paths(params: dict) -> list[str]:
    """Return user-supplied visual references in stable manifest order."""
    paths: list[str] = []
    primary = str(params.get("reference_image_path") or "").strip()
    if primary:
        paths.append(primary)
    for key in ("character_ref_paths", "location_ref_paths"):
        for value in params.get(key) or []:
            candidate = str(value or "").strip()
            if candidate:
                paths.append(candidate)
    return paths


def _director_has_visual_references(
    params: dict,
    *,
    existing_only: bool = False,
) -> bool:
    paths = _director_visual_reference_paths(params)
    if not existing_only:
        return bool(paths)
    return any(os.path.isfile(path) for path in paths)


def _director_effective_shot_image_policy(params: dict) -> str:
    """Return a resolved policy, retaining generated images as legacy default."""
    saved = str(params.get("_director_shot_image_policy") or "").strip()
    if saved in SHOT_IMAGE_POLICIES:
        return saved
    return SHOT_IMAGE_GENERATE


def _resolve_fresh_shot_image_policy(params: dict) -> str:
    """Resolve a fresh request against the selected video's capabilities."""
    getter = getattr(_wgp, "get_model_def", None)
    if not callable(getter):
        return SHOT_IMAGE_GENERATE
    video_model = params.get("video_model") or "ltx2_22B_distilled_1_1"
    return resolve_shot_image_policy(
        getter(video_model) or {},
        params.get("shot_image_guidance"),
        has_visual_references=_director_has_visual_references(params),
    )


def _saved_pipeline_shot_image_policy(state: dict) -> str:
    """Read a persisted policy; pre-feature projects required start images."""
    saved = str(state.get("shot_image_policy") or "").strip()
    if saved in SHOT_IMAGE_POLICIES:
        return saved
    snapshot = state.get("_params_snapshot") or {}
    saved = str(snapshot.get("_director_shot_image_policy") or "").strip()
    if saved in SHOT_IMAGE_POLICIES:
        return saved
    return SHOT_IMAGE_GENERATE


def _director_uses_image_roles(params: dict) -> bool:
    return any(
        key in params
        for key in (
            "image_creator_model", "image_editor_model",
            "image_creator_loras", "image_editor_loras",
        )
    )


def _director_image_role_model(params: dict, role: str) -> str:
    if not _director_uses_image_roles(params):
        return params.get("image_model") or "flux2_klein_9b"
    key = "image_creator_model" if role == "creator" else "image_editor_model"
    value = str(params.get(key) or "").strip()
    if not value:
        raise DirectorModelCompatibilityError(
            f"Director image {role} model is unavailable."
        )
    return value


def _director_image_role_loras(params: dict, role: str) -> dict:
    if not _director_uses_image_roles(params):
        return dict(params.get("image_loras") or {})
    resolved = params.get("_director_image_role_loras")
    selections = (
        resolved.get(role, []) if isinstance(resolved, dict) else []
    )
    return {
        "activated_loras": [
            item["id"] for item in selections if isinstance(item, dict)
        ],
        "loras_multipliers": " ".join(
            str(item["multiplier"])
            for item in selections if isinstance(item, dict)
        ),
        "parameter_expansions": [
            expansion
            for item in selections if isinstance(item, dict)
            for expansion in item.get("parameter_expansions", [])
            if isinstance(expansion, dict)
        ],
    }


def _director_role_prompt(prompt: str, loras: dict, role: str) -> str:
    """Append server-resolved LoRA fragments for exactly one image role."""
    scope = "generation" if role == "creator" else "editing"
    fragments = [
        str(item.get("text") or "").strip()
        for item in loras.get("parameter_expansions", [])
        if scope in (item.get("scopes") or ())
        and str(item.get("text") or "").strip()
    ]
    if not fragments:
        return prompt
    return ", ".join([prompt, *fragments])


def _director_image_params(params: dict, model_type: str) -> dict:
    """Use each role model's defaults, then deliberate shared overrides."""
    defaults = {}
    getter = getattr(_wgp, "get_default_settings", None)
    if _director_uses_image_roles(params) and callable(getter):
        try:
            loaded = getter(model_type)
            if isinstance(loaded, dict):
                defaults = dict(loaded)
        except Exception:
            defaults = {}
    defaults.update(dict(params.get("image_params") or {}))
    return defaults


def _validate_director_models(
    params: dict,
    *,
    stages: tuple[str, ...] = ("image", "video"),
) -> None:
    """Reject model/workflow combinations Director cannot drive safely."""
    registry_methods = (
        "get_model_def",
        "get_model_family",
        "get_base_model_type",
    )
    if not all(callable(getattr(_wgp, name, None)) for name in registry_methods):
        return

    effective_policy = _director_effective_shot_image_policy(params)
    validate_image_stage = "image" in stages and (
        "video" not in stages or shot_images_required(effective_policy)
    )
    if validate_image_stage:
        roles = (
            ("creator", "editor")
            if _director_uses_image_roles(params) else ("legacy",)
        )
        for role in roles:
            image_model = _director_image_role_model(
                params, "creator" if role == "legacy" else role,
            )
            resolved = _director_model_assessment(image_model)
            if resolved is not None:
                model_def, assessment = resolved
                capability = (
                    assessment["image"]
                    if role == "legacy" else assessment["image"][role]
                )
                if not capability["compatible"]:
                    name = model_def.get("name", image_model)
                    reason = (
                        capability.get("reason")
                        or "; ".join(capability.get("reasons") or ())
                    )
                    raise DirectorModelCompatibilityError(
                        f"{name} cannot be used as Director's image {role}: "
                        f"{reason} Choose a compatible image model.",
                    )

    if "video" not in stages:
        return
    pipeline_type = params.get("pipeline_type") or "music_video"
    if pipeline_type not in DIRECTOR_PIPELINE_TYPES:
        raise DirectorModelCompatibilityError(
            f"Unknown Director workflow '{pipeline_type}'.",
        )
    video_model = params.get("video_model") or "ltx2_22B_distilled_1_1"
    resolved = _director_model_assessment(video_model)
    if resolved is None:
        return
    model_def, assessment = resolved
    capability = assessment["video"][pipeline_type]
    name = model_def.get("name", video_model)
    workflow_labels = {
        "music_video": "Music Video",
        "short_film_audio": "audio-driven Short Film",
        "short_film_story": "story-driven Short Film",
    }
    if not capability["compatible"]:
        raise DirectorModelCompatibilityError(
            f"{name} cannot be used for Director {workflow_labels[pipeline_type]}: "
            f"{capability['reason']} Choose a compatible video model.",
        )
    if params.get("seamless"):
        seamless = assessment["video"]["seamless"]
        if not seamless["compatible"]:
            raise DirectorModelCompatibilityError(
                f"{name} cannot be used with Director Seamless: "
                f"{seamless['reason']} Turn off Seamless or choose another model.",
            )
    if params.get("voice_reference") and not assessment["supports_voice_reference"]:
        raise DirectorModelCompatibilityError(
            f"{name} does not support Director Voice Reference. "
            "Remove the voice reference or choose a compatible model.",
        )
    if (
        effective_policy == SHOT_IMAGES_DIRECT_REFERENCES
        and not _director_has_visual_references(params, existing_only=True)
    ):
        raise DirectorModelCompatibilityError(
            f"{name} needs at least one valid main, character, or location "
            "image when Director uses references directly. Add a visual "
            "reference, choose Generate shot images, or use a prompt-only model."
        )


def _director_params_from_saved_state(state: dict) -> dict:
    """Reconstruct compatibility-relevant params from a saved pipeline."""
    params = dict(state.get("_params_snapshot") or {})
    for key in (
        "pipeline_type", "seamless", "image_model", "video_model",
        "image_creator_model", "image_editor_model",
        "image_creator_loras", "image_editor_loras",
        "_director_image_role_loras", "_director_image_role_selection",
    ):
        if state.get(key) is not None:
            params[key] = state[key]
    params["_director_shot_image_policy"] = _saved_pipeline_shot_image_policy(state)
    return _normalize_explicit_guidance_snapshot(params)


def _limit_director_image_refs(
    model_type: str,
    refs: list[str],
    *,
    pid: str,
) -> list[str]:
    """Honor an image editor's reference limit, preserving source first."""
    try:
        resolved = _director_model_assessment(model_type)
    except DirectorModelCompatibilityError:
        return refs
    if resolved is None:
        return refs
    _, assessment = resolved
    maximum = assessment.get("max_image_refs")
    if not isinstance(maximum, int) or maximum <= 0 or len(refs) <= maximum:
        return refs
    print(
        f"[Pipeline {pid}] {model_type} accepts {maximum} image reference(s); "
        f"using the source plus the first {max(0, maximum - 1)} supplemental "
        f"reference(s) and skipping {len(refs) - maximum}.",
    )
    return refs[:maximum]


def _has_runtime_model_registry() -> bool:
    return all(
        callable(getattr(_wgp, name, None))
        for name in ("get_model_def", "get_model_family", "get_base_model_type")
    )


def _director_supports_frame_injection(model_type: str) -> bool:
    """Whether Director may generate and submit intermediate keyframes."""
    if not _has_runtime_model_registry():
        return True
    try:
        model_def = _wgp.get_model_def(model_type) or {}
    except Exception:
        return False
    return bool(model_def.get("custom_frames_injection"))


class _RepairCancelledError(RuntimeError):
    """Internal control-flow exception for a server-owned repair batch."""


def _claim_pipeline_operation_locked(pid: str) -> bool:
    """Reserve a terminal pipeline while ``_pipeline_lock`` is held."""
    if (
        pid in _pipeline_threads
        or bool(_pipeline_child_jobs.get(pid))
        or pid in _pipeline_starting
        or pid in _pipeline_operations
        or pid in _pipeline_deleting
        or _pipelines.get(pid, {}).get("status") in {
            "queued", "planning", "running", "paused",
        }
    ):
        return False
    _pipeline_operations.add(pid)
    return True


def _claim_pipeline_operation(pid: str) -> bool:
    """Reserve a terminal pipeline for one Dashboard mutation."""
    with _pipeline_lock:
        return _claim_pipeline_operation_locked(pid)


def _release_pipeline_operation(pid: str) -> None:
    with _pipeline_lock:
        _pipeline_operations.discard(pid)


def _claim_pipeline_delete(pid: str) -> bool:
    """Reserve deletion before taking the state-file lock."""
    with _pipeline_lock:
        pipeline = _pipelines.get(pid)
        if (
            pid in _pipeline_threads
            or bool(_pipeline_child_jobs.get(pid))
            or pid in _pipeline_starting
            or pid in _pipeline_operations
            or pid in _pipeline_deleting
            or (
                pipeline
                and pipeline.get("status") in {
                    "queued", "planning", "running", "paused",
                }
            )
        ):
            return False
        _pipeline_deleting.add(pid)
        return True


def _release_pipeline_delete(pid: str) -> None:
    with _pipeline_lock:
        _pipeline_deleting.discard(pid)


def _exclusive_pipeline_operation(function):
    """Keep delete/resume/live saves away from a Dashboard media mutation."""
    @wraps(function)
    def wrapped(out_dir: str, pid: str, *args, **kwargs):
        if not _claim_pipeline_operation(pid):
            raise PipelineBusyError(
                "Pipeline is still active; try again shortly.",
            )
        try:
            return function(out_dir, pid, *args, **kwargs)
        finally:
            _release_pipeline_operation(pid)
    return wrapped


# ── Reference art-style lock ────────────────────────────────────────────
# Flux Klein only honors a reference's art style when the MEDIUM IS NAMED
# AT THE START of the prompt ("Maintain the same black and white hand
# drawn art style. ..."). A trailing referential anchor ("...preserve the
# art style of the reference image") demonstrably does NOT hold it — the
# output comes back photorealistic. So the pipeline asks the vision LLM
# once per run to NAME the reference's medium concretely, and the phrase
# is prepended to every image prompt deterministically at generation time
# (instead of trusting the 4B planner to follow a guide rule, which it
# provably doesn't do reliably).

_STYLE_DESCRIBE_PROMPT = (
    "Name the visual medium and art style of this image in one short phrase "
    "of 3 to 8 words. Examples: 'black and white hand-drawn pencil sketch', "
    "'watercolor illustration', 'flat-color anime', 'oil painting', "
    "'photorealistic photograph'. Reply with ONLY the phrase, nothing else."
)


def _normalize_style_phrase(raw: str) -> str:
    """Reduce the vision LLM's style answer to a clean, prefix-able phrase.

    Returns "" for photographic references (photorealism is the image
    model's default — a prefix would add nothing) and for answers that
    don't look like a short phrase (refusals, prose, thinking spill).
    """
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.splitlines()[0].strip()
    s = s.strip('"').strip("'").lstrip("-*# ").rstrip(".").strip()
    if not s or len(s) > 80:
        return ""
    low = s.lower()
    if "photo" in low or "realistic" in low:
        return ""
    # Avoid "...style art style" when composing the prefix sentence.
    for suffix in (" art style", " style"):
        if low.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    # Mid-sentence position: "Maintain the same simple black line..." —
    # the vision model tends to capitalize its answer.
    if s and s[0].isupper() and (len(s) < 2 or not s[1].isupper()):
        s = s[0].lower() + s[1:]
    return s


def _style_prefix_for(style: str) -> str:
    """The exact lead sentence validated to hold Klein to a medium."""
    style = (style or "").strip()
    return f"Maintain the same {style} art style. " if style else ""


# Motion-photography effects have no place in a START-FRAME prompt — the
# frame must be sharp for the video model to animate from. The music-video
# planner still writes them ("A strong motion blur effect is present on
# the background...") because its energy-focused rules leak into image
# prompts, and Klein complies with an image-wrecking smear. Deterministic
# strip, same philosophy as the style prefix: don't trust the 4B.
_MOTION_EFFECT_RE = re.compile(
    r"motion[- ]?blur|speed[- ]?lines|long[- ]?exposure|camera shake|blur effect",
    re.IGNORECASE,
)


def _strip_motion_effects(prompt: str) -> str:
    """Drop sentences/clauses that request motion-photography effects."""
    if not prompt or not _MOTION_EFFECT_RE.search(prompt):
        return prompt
    parts = re.split(r"(?<=[.;!?])\s+", prompt)
    kept = [s for s in parts if not _MOTION_EFFECT_RE.search(s)]
    cleaned = " ".join(kept).strip()
    return cleaned if cleaned else prompt

# ── Pipeline State Persistence ─────────────────────────────────────────────

PIPELINE_STATE_VERSION = 1
_PIPELINE_FILE_PREFIX = "_director_pipeline_"
_PIPELINE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def pipeline_state_filename(pid: str) -> str:
    """Return the one canonical direct-child Director state filename."""
    if not isinstance(pid, str) or _PIPELINE_ID_RE.fullmatch(pid) is None:
        raise ValueError("Invalid Director pipeline id")
    return f"{_PIPELINE_FILE_PREFIX}{pid}.json"


def _pipeline_state_descriptor(out_dir: str, pid: str) -> dict:
    """Seal the current semantic-authority JSON with a relative pointer."""
    filename = pipeline_state_filename(pid)
    path = os.path.join(out_dir, filename)
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return {
        "path": filename,
        "sha256": digest.hexdigest(),
        "size": size,
    }


def _pipeline_state_payload(state: dict) -> bytes:
    return json.dumps(
        state, indent=2, ensure_ascii=False, default=str,
    ).encode("utf-8")


def _pipeline_payload_descriptor(pid: str, payload: bytes) -> dict:
    return {
        "path": pipeline_state_filename(pid),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _write_pipeline_payload_unlocked(filepath: str, payload: bytes) -> None:
    """Atomically replace one pipeline JSON payload under the file lock."""
    temp_filepath = (
        f"{filepath}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with open(temp_filepath, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_filepath, filepath)
        if os.name != "nt":
            directory_fd = os.open(
                os.path.dirname(filepath) or ".",
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.isfile(temp_filepath):
            try:
                os.remove(temp_filepath)
            except OSError:
                pass


def _write_pipeline_json_unlocked(filepath: str, state: dict) -> None:
    """Atomically replace one pipeline JSON file while its file lock is held."""
    _write_pipeline_payload_unlocked(filepath, _pipeline_state_payload(state))


def _commit_pipeline_json_unlocked(
    filepath: str,
    pid: str,
    state: dict,
    *,
    recovery_required: bool = False,
) -> dict:
    """Two-phase commit semantic JSON against its journal-owned descriptor."""
    payload = _pipeline_state_payload(state)
    descriptor = _pipeline_payload_descriptor(pid, payload)
    previous_payload = None
    try:
        with open(filepath, "rb") as handle:
            previous_payload = handle.read()
    except FileNotFoundError:
        pass
    prepared = False
    prepare_available = callable(_recovery_prepare_parent_state)
    if prepare_available:
        prepared = bool(
            _recovery_prepare_parent_state(pid, state, descriptor)
        )
    if recovery_required and prepare_available and not prepared:
        raise RuntimeError("Director recovery parent is unavailable")
    _write_pipeline_payload_unlocked(filepath, payload)
    if prepared or (
        recovery_required and callable(_recovery_checkpoint_parent)
    ):
        if not callable(_recovery_checkpoint_parent):
            raise RuntimeError("Director recovery checkpoint is unavailable")
        try:
            _recovery_checkpoint_parent(pid, state, descriptor)
        except BaseException:
            # A rejected final cursor commit must not leave the caller's live
            # rollback contradicted by newer semantic bytes. The already
            # durable pending descriptor still makes a true process crash
            # between replace and this rollback adoptable exactly once.
            if previous_payload is not None:
                _write_pipeline_payload_unlocked(filepath, previous_payload)
            raise
    return descriptor


def _remove_pipeline_state_file(out_dir: str, pid: str) -> None:
    """Remove an unregistered state file and durably record its absence."""
    filepath = os.path.join(out_dir, pipeline_state_filename(pid))
    try:
        os.remove(filepath)
    except FileNotFoundError:
        return
    if os.name != "nt":
        directory_fd = os.open(
            out_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _map_completed_clip_videos(
    output_files: list[str], clip_count: int,
) -> list[Optional[str]]:
    """Map an unambiguous multi-clip output prefix to its planned clips."""
    if clip_count <= 0:
        return []
    video_exts = {".mp4", ".webm", ".mkv", ".mov"}
    clips = [
        filename for filename in output_files
        if os.path.splitext(filename)[1].lower() in video_exts
        and "_multiclip" not in os.path.splitext(filename)[0].lower()
    ]
    if not clips or len(clips) > clip_count:
        return []
    return clips + [None] * (clip_count - len(clips))


def _clip_video_slots(
    output_files: list[str], clip_count: int,
) -> list[Optional[str]]:
    """Preserve explicit sparse clip indices, with legacy prefix fallback."""
    indexed = getattr(output_files, "clip_output_files", None)
    if isinstance(indexed, dict) and indexed and clip_count > 0:
        slots: list[Optional[str]] = [None] * clip_count
        for index, filename in indexed.items():
            try:
                position = int(index)
            except (TypeError, ValueError):
                continue
            if 0 <= position < clip_count and filename:
                slots[position] = filename
        if any(slots):
            return slots
    return _map_completed_clip_videos(output_files, clip_count)


def _save_pipeline_state(pid: str) -> bool:
    """Serialize one live pipeline snapshot without racing other writers."""
    with _pipeline_file_lock:
        return _save_pipeline_state_locked(pid)


def _write_initial_pipeline_state(pid: str, pipeline: dict) -> tuple[dict, dict]:
    """Commit a recoverable parent before it becomes observable in memory."""
    params = dict(pipeline.get("params") or {})
    out_dir = str(pipeline.get("out_dir") or "")
    if not out_dir or not os.path.isdir(out_dir):
        if callable(_recovery_register_parent):
            raise RuntimeError("Director project is no longer available")
        # Compatibility for isolated legacy/unit-test wiring. Production
        # launch always supplies the recovery registrar and never recreates a
        # missing project here.
        os.makedirs(out_dir, exist_ok=True)
    state = {
        "version": PIPELINE_STATE_VERSION,
        "pipeline_id": pid,
        "created_at": pipeline.get("created_at"),
        "completed_at": None,
        "status": "queued",
        "workspace": pipeline.get("workspace") or "default",
        "source_remote": bool(pipeline.get("source_remote", False)),
        "pipeline_type": params.get("pipeline_type", "music_video"),
        "shot_image_policy": _director_effective_shot_image_policy(params),
        "shot_image_guidance": params.get("shot_image_guidance", "auto"),
        "scene_description": params.get("scene_description", ""),
        "reference_image_path": params.get("reference_image_path"),
        "generated_reference_image_filename": None,
        "character_ref_paths": params.get("character_ref_paths", []),
        "location_ref_paths": params.get("location_ref_paths", []),
        "auto_mode": params.get("auto_mode", True),
        "seamless": params.get("seamless", True),
        "video_model": params.get("video_model", ""),
        **({
            "image_creator_model": params.get("image_creator_model"),
            "image_editor_model": params.get("image_editor_model"),
            "image_creator_loras": params.get("image_creator_loras", []),
            "image_editor_loras": params.get("image_editor_loras", []),
        } if _director_uses_image_roles(params) else {
            "image_model": params.get("image_model", ""),
            "image_loras": params.get("image_loras", {}),
        }),
        "video_loras": params.get("video_loras", {}),
        "image_params": params.get("image_params", {}),
        "video_params": params.get("video_params", {}),
        "llm_log": None,
        "clips": [],
        "output_files": [],
        "total_time_sec": 0,
        "_params_snapshot": params,
        "recovery": {},
    }
    filepath = os.path.join(out_dir, pipeline_state_filename(pid))
    with _pipeline_file_lock:
        _write_pipeline_json_unlocked(filepath, state)
        descriptor = _pipeline_state_descriptor(out_dir, pid)
    return state, descriptor


def _require_pipeline_checkpoint(pid: str, boundary: str) -> None:
    """Make a safe-unit durability failure stop later work immediately."""
    if not _save_pipeline_state(pid):
        raise RuntimeError(
            f"Director could not commit its {boundary} recovery checkpoint"
        )


def _save_pipeline_state_locked(pid: str) -> bool:
    """Serialize pipeline state to JSON on disk. Called at phase boundaries."""
    with _pipeline_lock:
        p = _pipelines.get(pid)
        if not p:
            return False
        p = dict(p)  # shallow copy for safe access outside lock

    out_dir = p.get("out_dir") or (_wgp.save_path if _wgp else "outputs")
    params = p.get("params", {})

    # Build per-clip state
    clip_plans = p.get("clip_plans", [])
    clip_images = p.get("clip_images", [])
    pre_polish = p.get("_clip_plans_pre_polish", [])
    clip_timings = p.get("_clip_timings", {})

    # Per-clip video filenames. Multi-clip output files are emitted in clip
    # order, followed by the optional *_multiclip join. Preserve a completed
    # prefix after cancellation so the Dashboard can rerun/rejoin those clips.
    clip_videos = p.get("_clip_video_files") or []
    if not clip_videos and not params.get("seamless", True):
        clip_videos = _clip_video_slots(
            p.get("output_files") or [], len(clip_plans),
        )

    clips = []
    for i, plan in enumerate(clip_plans):
        clip_state = {
            "index": i,
            "planned_clip": p.get("_planned_clips", [{}] * (i + 1))[i] if i < len(p.get("_planned_clips", [])) else None,
            "image_prompt": plan.get("image_prompt", ""),
            "video_prompt": plan.get("video_prompt", ""),
            "visual_changes": plan.get("visual_changes", []) or [],
            "image_source": plan.get("image_source", "original"),
            "keyframe_prompts": plan.get("keyframe_prompts", []) or [],
            "window_prompts": plan.get("window_prompts", []) or [],
            "window_count": plan.get("window_count", 1),
            "image_prompt_pre_polish": pre_polish[i].get("image_prompt", "") if i < len(pre_polish) else None,
            "video_prompt_pre_polish": pre_polish[i].get("video_prompt", "") if i < len(pre_polish) else None,
            # Per-window and per-keyframe pre-polish snapshots so the
            # Dashboard can show before/after diffs for windowed shots
            # (≥21s) and for keyframe prompts. Without these, windowed
            # shots showed no polish diff because video_prompt is
            # skipped by Pass 3 when window_prompts exist (its content
            # is unused at generation time anyway).
            "window_prompts_pre_polish": pre_polish[i].get("window_prompts", []) if i < len(pre_polish) else None,
            "keyframe_prompts_pre_polish": pre_polish[i].get("keyframe_prompts", []) if i < len(pre_polish) else None,
            "start_image_filename": clip_images[i] if i < len(clip_images) else None,
            "keyframe_filenames": (p.get("_clip_keyframes", []) or [])[i] if i < len(p.get("_clip_keyframes", [])) else [],
            "video_filename": clip_videos[i] if i < len(clip_videos) else None,
            "video_stale": False,
            "tag": (p.get("_clip_tags", []) or [])[i] if i < len(p.get("_clip_tags", [])) else None,
            "image_gen_time_sec": clip_timings.get(f"image_{i}"),
            "video_gen_time_sec": clip_timings.get(f"video_{i}"),
        }
        if isinstance(plan.get("_h3_shot"), dict):
            # Versioned H3-only structured shot input. It is source-authored
            # planning data, not runtime media, and lets recovery replay the
            # committed shared shot plan without another LLM/planner pass.
            clip_state["_h3_shot"] = plan["_h3_shot"]
        clips.append(clip_state)

    state = {
        "version": PIPELINE_STATE_VERSION,
        "pipeline_id": pid,
        "created_at": p.get("created_at"),
        "completed_at": p.get("_completed_at"),
        "status": p.get("status", "unknown"),
        "phase": p.get("phase", p.get("status", "unknown")),
        "pause_reason": p.get("pause_reason"),
        "workspace": p.get("workspace") or "default",
        "source_remote": bool(p.get("source_remote", False)),
        "pipeline_type": params.get("pipeline_type", "music_video"),
        "shot_image_policy": _director_effective_shot_image_policy(params),
        "shot_image_guidance": params.get("shot_image_guidance", "auto"),
        "scene_description": params.get("scene_description", ""),
        "reference_image_path": params.get("reference_image_path"),
        # A no-reference run creates its own visual anchor inside the output
        # directory.  Keep the basename separate from the user's input path so
        # reruns and resume can reuse it without pretending the user uploaded
        # a reference image.
        "generated_reference_image_filename": (
            params.get("generated_reference_image_filename")
            or p.get("generated_reference_image_filename")
        ),
        "character_ref_paths": params.get("character_ref_paths", []),
        "location_ref_paths": params.get("location_ref_paths", []),
        "auto_mode": params.get("auto_mode", True),
        "seamless": params.get("seamless", True),
        "video_model": params.get("video_model", ""),
        **({
            "image_creator_model": params.get("image_creator_model"),
            "image_editor_model": params.get("image_editor_model"),
            "image_creator_loras": params.get("image_creator_loras", []),
            "image_editor_loras": params.get("image_editor_loras", []),
        } if _director_uses_image_roles(params) else {
            "image_model": params.get("image_model", ""),
            "image_loras": params.get("image_loras", {}),
        }),
        "video_loras": params.get("video_loras", {}),
        "image_params": params.get("image_params", {}),
        "video_params": params.get("video_params", {}),
        # Raw LLM requests/responses are transient inference material. New
        # checkpoints never persist them; untouched historical files remain
        # readable but are filtered from public responses by launch.py.
        "llm_log": None,
        "clips": clips,
        "output_files": p.get("output_files", []),
        # Failure state is already normalized before it reaches the live
        # pipeline. Persist only that stable envelope; raw exceptions remain
        # traceback-only and never become Dashboard/recovery content.
        "error": p.get("error"),
        "error_code": p.get("error_code"),
        "failure_details": p.get("failure_details"),
        "oom_info": p.get("oom_info"),
        "total_time_sec": (time.time() - p["created_at"]) if p.get("created_at") else None,
        # Full original request params, verbatim (it's the JSON dict the
        # endpoint received, so it's serializable). This is what makes a
        # crashed pipeline faithfully resumable — music-video mode in
        # particular depends on the analyzed audio track, character list, and
        # per-clip frame counts that the flattened per-clip state above does
        # not carry. resume_pipeline() rehydrates from here.
        "_params_snapshot": params,
        # Director JSON remains the semantic authority.  This bounded block
        # records only scheduling identities and already-verified child
        # artifact descriptors; prompts and request paths stay in the
        # params snapshot above and never enter the queue journal.
        "recovery": p.get("_recovery") or {},
    }

    try:
        os.makedirs(out_dir, exist_ok=True)
        filepath = os.path.join(out_dir, pipeline_state_filename(pid))
        _commit_pipeline_json_unlocked(
            filepath,
            pid,
            state,
            recovery_required=bool(p.get("_recovery_parent")),
        )
        return True
    except Exception as e:
        print(f"[Pipeline] Failed to save state for {pid}: {e}")
        return False


def _normalize_interrupted_repair(state: dict, pid: str) -> bool:
    """Mark a persisted active repair interrupted when its worker is gone.

    Browser reloads leave the non-daemon worker registered, so they continue
    normally.  A Maestro process restart removes the registry; changing the
    saved status makes that distinction visible and leaves Repair available as
    an idempotent resume-from-disk operation.
    """
    repair = state.get("repair")
    if not isinstance(repair, dict):
        return False
    if repair.get("status") not in _REPAIR_ACTIVE_STATUSES:
        return False
    operation_id = repair.get("operation_id")
    with _pipeline_lock:
        control = _pipeline_repairs.get(pid)
        worker_present = bool(
            control
            and control.get("operation_id") == operation_id
        )
    if worker_present:
        return False

    now = time.time()
    repair.update({
        "status": "interrupted",
        "phase": "interrupted",
        "clip_index": None,
        "message": "Repair was interrupted when Maestro stopped. Start Repair again to continue.",
        "error": "Maestro stopped before the repair finished.",
        "updated_at": now,
        "completed_at": now,
    })
    return True


def list_pipeline_states(out_dir: str) -> list[dict]:
    """Scan directory for saved pipeline state files. Returns summary list."""
    results = []
    if not os.path.isdir(out_dir):
        return results
    # Scan top-level and workspace subdirectories
    dirs_to_scan = [out_dir]
    for name in os.listdir(out_dir):
        sub = os.path.join(out_dir, name)
        if os.path.isdir(sub):
            dirs_to_scan.append(sub)

    for scan_dir in dirs_to_scan:
        for fname in os.listdir(scan_dir):
            if fname.startswith(_PIPELINE_FILE_PREFIX) and fname.endswith(".json"):
                try:
                    filepath = os.path.join(scan_dir, fname)
                    with _pipeline_file_lock:
                        with open(filepath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        # Normalize and replace the exact snapshot read while
                        # retaining the file lock. Releasing it between read
                        # and write let a repair worker publish newer progress
                        # that this stale list snapshot then overwrote.
                        pid = data.get("pipeline_id", "")
                        changed = _normalize_interrupted_repair(data, pid)

                        # Detect stale "running" pipelines while retaining the
                        # same serialization boundary as repair normalization.
                        status = data.get("status", "unknown")
                        with _pipeline_lock:
                            pipeline_present = pid in _pipelines
                        if status in {"queued", "planning", "running"} and not pipeline_present:
                            data["status"] = "crashed"
                            status = "crashed"
                            changed = True
                        if changed:
                            _commit_pipeline_json_unlocked(
                                filepath, pid, data,
                            )
                    results.append({
                        "id": pid,
                        "status": status,
                        "pipeline_type": data.get("pipeline_type", ""),
                        "created_at": data.get("created_at"),
                        "clip_count": len(data.get("clips", [])),
                        "output_count": len(data.get("output_files", [])),
                        "scene_description": (data.get("scene_description", "") or "")[:100],
                        "workspace": os.path.basename(scan_dir) if scan_dir != out_dir else "default",
                        "repair_status": (data.get("repair") or {}).get("status"),
                        "_filepath": filepath,
                    })
                except Exception:
                    pass
    results.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return results


def _backfill_clip_video_filenames(state: dict, state_dir: str) -> dict:
    """Derive per-clip video filenames from output_files when absent.

    Multi-clip (non-seamless) runs produce one video per clip, in clip
    order, plus a trailing *_multiclip.mp4 join — but the runtime never
    recorded them per clip (_clip_video_files was a dead key), leaving
    every clip's video_filename null. That made the Dashboard count all
    clips as "missing" and broke Rejoin (needs >= 2 per-clip files).
    Fill only null entries (a rerun clip's filename must survive), only
    when the per-clip count matches exactly, and only for files that
    still exist next to the pipeline file. Seamless runs (one combined
    output) never match the count and are left untouched.
    """
    clips = state.get("clips") or []
    outputs = [
        filename for filename in (state.get("output_files") or [])
        if "_multiclip" not in os.path.splitext(filename)[0].lower()
    ]
    if not clips or len(outputs) != len(clips):
        return state
    for i, clip in enumerate(clips):
        if not clip.get("video_filename") and os.path.isfile(os.path.join(state_dir, outputs[i])):
            clip["video_filename"] = outputs[i]
    return state


_SAVED_MEDIA_EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".webp"},
    "video": {".mkv", ".mov", ".mp4", ".webm"},
}


def _invalid_saved_media_numbers(
    filenames: list,
    expected_count: int,
    output_dir: str,
    media_kind: str,
) -> list[int]:
    """Return 1-based slots without a non-empty direct-child media file."""
    allowed_extensions = _SAVED_MEDIA_EXTENSIONS.get(media_kind)
    if allowed_extensions is None:
        raise ValueError(f"Unsupported saved media kind: {media_kind}")
    output_root = os.path.realpath(os.path.abspath(output_dir))
    normalized_root = os.path.normcase(output_root)
    invalid = []
    for index in range(expected_count):
        filename = filenames[index] if index < len(filenames) else ""
        if (
            not isinstance(filename, str)
            or not filename
            or os.path.basename(filename) != filename
        ):
            invalid.append(index + 1)
            continue
        candidate = os.path.realpath(os.path.join(output_root, filename))
        if (
            os.path.normcase(os.path.dirname(candidate)) != normalized_root
            or os.path.splitext(filename)[1].lower() not in allowed_extensions
            or not os.path.isfile(candidate)
        ):
            invalid.append(index + 1)
            continue
        try:
            if os.path.getsize(candidate) <= 0:
                invalid.append(index + 1)
        except OSError:
            invalid.append(index + 1)
    return invalid


def _require_video_start_images(
    clip_images: list,
    clip_count: int,
    output_dir: str,
) -> None:
    """Stop the video phase rather than silently falling back to T2V."""
    invalid = _invalid_saved_media_numbers(
        clip_images, clip_count, output_dir, "image",
    )
    if not invalid:
        return
    invalid_labels = ", ".join(str(index) for index in invalid)
    raise RuntimeError(
        "Start-image generation did not produce valid recorded files for "
        f"shot(s) {invalid_labels}; video generation was not started. "
        "Use the Dashboard to regenerate the missing images."
    )


def load_pipeline_state(out_dir: str, pid: str) -> Optional[dict]:
    """Load a saved state while serialized against deletion/replacement."""
    with _pipeline_file_lock:
        return _load_pipeline_state_locked(out_dir, pid)


def _load_pipeline_state_locked(out_dir: str, pid: str) -> Optional[dict]:
    """Load a saved pipeline state by ID. Searches out_dir and subdirectories."""
    target = f"{_PIPELINE_FILE_PREFIX}{pid}.json"
    # Search top-level
    filepath = os.path.join(out_dir, target)
    if os.path.isfile(filepath):
        with _pipeline_file_lock:
            with open(filepath, "r", encoding="utf-8") as f:
                state = json.load(f)
            if _normalize_interrupted_repair(state, pid):
                _commit_pipeline_json_unlocked(filepath, pid, state)
            return _backfill_clip_video_filenames(state, out_dir)
    # Search subdirectories (workspaces)
    if os.path.isdir(out_dir):
        for name in os.listdir(out_dir):
            sub = os.path.join(out_dir, name, target)
            if os.path.isfile(sub):
                with _pipeline_file_lock:
                    with open(sub, "r", encoding="utf-8") as f:
                        state = json.load(f)
                    if _normalize_interrupted_repair(state, pid):
                        _commit_pipeline_json_unlocked(sub, pid, state)
                    return _backfill_clip_video_filenames(
                        state, os.path.join(out_dir, name),
                    )
    return None


def update_clip_tag(out_dir: str, pid: str, clip_index: int, tag: Optional[str]) -> bool:
    if not _claim_pipeline_operation(pid):
        raise PipelineBusyError("Pipeline is still active; try again shortly.")
    try:
        with _pipeline_file_lock:
            return _update_clip_tag_locked(out_dir, pid, clip_index, tag)
    finally:
        _release_pipeline_operation(pid)


def _update_clip_tag_locked(out_dir: str, pid: str, clip_index: int, tag: Optional[str]) -> bool:
    """Update the tag on a specific clip in a saved pipeline state."""
    state = load_pipeline_state(out_dir, pid)
    if not state:
        return False
    clips = state.get("clips", [])
    if clip_index < 0 or clip_index >= len(clips):
        return False
    clips[clip_index]["tag"] = tag

    # Find and overwrite the file
    target = f"{_PIPELINE_FILE_PREFIX}{pid}.json"
    for search_dir in [out_dir] + [os.path.join(out_dir, d) for d in os.listdir(out_dir) if os.path.isdir(os.path.join(out_dir, d))]:
        filepath = os.path.join(search_dir, target)
        if os.path.isfile(filepath):
            _commit_pipeline_json_unlocked(filepath, pid, state)
            return True
    return False


def _find_pipeline_file(out_dir: str, pid: str) -> Optional[str]:
    """Find the JSON file path for a saved pipeline."""
    target = f"{_PIPELINE_FILE_PREFIX}{pid}.json"
    filepath = os.path.join(out_dir, target)
    if os.path.isfile(filepath):
        return filepath
    if os.path.isdir(out_dir):
        for name in os.listdir(out_dir):
            sub = os.path.join(out_dir, name, target)
            if os.path.isfile(sub):
                return sub
    return None


def _update_saved_pipeline(out_dir: str, pid: str, updater) -> Optional[dict]:
    with _pipeline_file_lock:
        return _update_saved_pipeline_locked(out_dir, pid, updater)


def _update_saved_pipeline_locked(out_dir: str, pid: str, updater) -> Optional[dict]:
    """Load a saved pipeline, apply an updater function, save back, and return the state."""
    filepath = _find_pipeline_file(out_dir, pid)
    if not filepath:
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        state = _backfill_clip_video_filenames(
            json.load(f), os.path.dirname(filepath),
        )
    updater(state)
    _commit_pipeline_json_unlocked(filepath, pid, state)
    return state


# Pipeline statuses whose run thread is (or may become) alive — a paused
# pipeline is blocked in _wait_for_resume and resurrects its state file
# on resume, so deletion must refuse these, not just "running".
_ACTIVE_PIPELINE_STATUSES = ("queued", "planning", "running", "paused")


def any_pipeline_active() -> bool:
    """True when any in-memory pipeline has a live (or resumable-in-place)
    run thread. Used by workspace deletion: between generation jobs a
    pipeline holds no _jobs entry yet will recreate its workspace folder
    on its next step."""
    with _pipeline_lock:
        return bool(
            _pipeline_threads
            or _pipeline_child_jobs
            or _pipeline_starting
            or _pipeline_operations
            or _pipeline_deleting
        ) or any(
            p.get("status") in _ACTIVE_PIPELINE_STATUSES
            for p in _pipelines.values()
        )


def delete_pipeline(out_dir: str, pid: str) -> dict:
    """Serialize deletion against every pipeline-state reader and writer."""
    if not _claim_pipeline_delete(pid):
        return {"ok": False, "error": "running"}
    try:
        with _pipeline_file_lock:
            return _delete_pipeline_locked(out_dir, pid)
    finally:
        _release_pipeline_delete(pid)


def _delete_pipeline_locked(out_dir: str, pid: str) -> dict:
    """Delete a saved pipeline and every media file it produced.

    Refuses while the pipeline is running OR paused in memory: its state
    file is re-written at phase boundaries (and on resume) and would
    resurrect mid-delete, and popping a paused pipeline's entry crashes
    its blocked run thread. The media set is the union of filenames the
    state JSON references (start images, keyframes, clip videos,
    joins/rejoins) and any media in the same folder whose .meta.json
    sidecar carries this pipeline's id stamp — the second set catches
    superseded rerun files the JSON no longer points at. Shared inputs
    in uploads/ (the song, character and location refs) are absolute
    paths outside the pipeline folder and are never touched.
    """
    with _pipeline_lock:
        mem = _pipelines.get(pid)
        if (
            pid in _pipeline_threads
            or bool(_pipeline_child_jobs.get(pid))
            or pid in _pipeline_starting
            or pid in _pipeline_operations
            or (
                mem and mem.get("status") in _ACTIVE_PIPELINE_STATUSES
            )
        ):
            return {"ok": False, "error": "running"}
    filepath = _find_pipeline_file(out_dir, pid)
    if not filepath:
        return {"ok": False, "error": "not_found"}
    pipeline_dir = os.path.dirname(filepath)

    state = None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            state = _backfill_clip_video_filenames(json.load(f), pipeline_dir)
    except Exception:
        pass

    recovery_delete_prepared = False
    if callable(_recovery_prepare_parent_delete):
        recovery_delete_prepared = bool(
            _recovery_prepare_parent_delete(pid)
        )

    names = set()
    if state:
        for clip in state.get("clips", []) or []:
            if clip.get("start_image_filename"):
                names.add(clip["start_image_filename"])
            for kf in clip.get("keyframe_filenames") or []:
                if kf:
                    names.add(kf)
            if clip.get("video_filename"):
                names.add(clip["video_filename"])
        for out in state.get("output_files", []) or []:
            if out:
                names.add(out)
    try:
        dir_entries = os.listdir(pipeline_dir)
    except OSError:
        dir_entries = []
    # Sidecar names strip the media extension ("clip_0.mp4" ->
    # "clip_0.meta.json"), so map extensionless base -> real media file
    # before sweeping; adding the bare base would silently no-op.
    base_to_media = {}
    ambiguous_media_bases = set()
    for entry in dir_entries:
        if entry.endswith(".meta.json") or entry.startswith(_PIPELINE_FILE_PREFIX):
            continue
        stem, extension = os.path.splitext(entry)
        if extension.lower() not in GENERATED_MEDIA_EXTENSIONS:
            continue
        existing = base_to_media.setdefault(stem, entry)
        if existing != entry:
            ambiguous_media_bases.add(stem)
    for fname in dir_entries:
        if not fname.endswith(".meta.json"):
            continue
        try:
            with open(os.path.join(pipeline_dir, fname), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        if meta.get("director_pipeline_id") == pid:
            sidecar_stem = fname[: -len(".meta.json")]
            media = meta.get("output_filename")
            if not (
                isinstance(media, str)
                and media == os.path.basename(media)
                and os.path.splitext(media)[0] == sidecar_stem
                and os.path.splitext(media)[1].lower()
                    in GENERATED_MEDIA_EXTENSIONS
                and os.path.isfile(os.path.join(pipeline_dir, media))
            ):
                media = (
                    None if sidecar_stem in ambiguous_media_bases
                    else base_to_media.get(sidecar_stem)
                )
            if media:
                names.add(media)
            else:
                # Orphan sidecar (media already gone) — remove it directly.
                try:
                    os.remove(os.path.join(pipeline_dir, fname))
                except OSError:
                    pass

    from services.win_safe_files import safe_delete, safe_join_under, favorites_lock
    deleted = 0
    deferred = 0
    errors = []
    cleanup_blocked = False
    for name in sorted(names):
        # State filenames are relative; contain them to the pipeline folder
        # (symlink-resolving join) so a tampered state file cannot reach
        # outside it.
        target = safe_join_under(pipeline_dir, name)
        if target is None:
            errors.append(f"skipped suspicious path: {name}")
            cleanup_blocked = True
            continue
        # retries=1: bulk sweep — locked files go straight to the
        # trash-rename path instead of sleeping through backoff per file.
        result = safe_delete(target, retries=1)
        if result.get("deferred"):
            deferred += 1
        elif result.get("deleted"):
            deleted += 1
        elif result.get("reason") == "locked":
            errors.append(name)
            cleanup_blocked = True
            # Preserve ownership companions so a later retry can still find
            # and safely remove this media.
            continue
        elif not result.get("deleted") and result.get("reason") != "not_found":
            errors.append(name)
            cleanup_blocked = True
            continue
        artifact_base = os.path.splitext(target)[0]
        # WGP may write metadata JSON or an alpha-frame ZIP beside the media
        # without registering those companions in its gallery list. Removing
        # them with their owned media prevents cancelled window artifacts from
        # accumulating invisibly.
        for companion_ext in (".meta.json", ".json", ".zip"):
            companion = artifact_base + companion_ext
            companion_result = safe_delete(companion, retries=1)
            if companion_result.get("reason") == "locked":
                errors.append(os.path.basename(companion))
                cleanup_blocked = True

    # Un-favorite everything that vanished (per-workspace .favorites.json).
    # Lock shared with launch.py's favorites endpoints — both sides do
    # read-modify-write on the same file from threadpool handlers.
    with favorites_lock:
        fav_path = os.path.join(pipeline_dir, ".favorites.json")
        if os.path.isfile(fav_path):
            try:
                with open(fav_path, "r", encoding="utf-8") as f:
                    favs = json.load(f)
                if isinstance(favs, list):
                    kept = [n for n in favs if n not in names]
                    if len(kept) != len(favs):
                        with open(fav_path, "w", encoding="utf-8") as f:
                            json.dump(sorted(kept), f)
            except Exception:
                pass

    # Current rerun slices are unique and cleaned in rerun_clip_video. Sweep
    # any historical/crash leftovers only when this was the folder's last
    # pipeline, because older names were not pipeline-scoped.
    try:
        others = [n for n in os.listdir(pipeline_dir)
                  if n.startswith(_PIPELINE_FILE_PREFIX) and n.endswith(".json")
                  and n != os.path.basename(filepath)]
        if not others:
            for n in os.listdir(pipeline_dir):
                if n.startswith("_rerun_audio_") and n.endswith(".wav"):
                    safe_delete(os.path.join(pipeline_dir, n))
    except OSError:
        pass

    delete_error = None
    if cleanup_blocked:
        # The state file is the recovery marker for retrying a partial delete.
        # Never erase it while owned media or companions are still locked.
        state_removed = False
        delete_error = "media_locked"
    else:
        state_result = safe_delete(filepath, retries=1)
        state_removed = bool(state_result.get("deleted")) or (
            state_result.get("reason") == "not_found"
        )
        if not state_removed:
            errors.append("state file is locked")
            delete_error = "state_file_locked"
    if state_removed:
        if recovery_delete_prepared:
            if not callable(_recovery_remove_parent):
                raise RuntimeError(
                    "Director recovery removal callback is unavailable"
                )
            _recovery_remove_parent(pid, pipeline_dir)
        with _pipeline_lock:
            _pipelines.pop(pid, None)

    try:
        from services.search_index import get_search_index
        get_search_index().invalidate()
    except Exception:
        pass

    return {
        "ok": state_removed,
        **({"error": delete_error} if delete_error else {}),
        "dir": pipeline_dir, "media_total": len(names),
        "media_deleted": deleted, "media_deferred": deferred, "errors": errors,
    }


@_exclusive_pipeline_operation
def rerun_clip_image(out_dir: str, pid: str, clip_index: int, prompt_override: str = None) -> dict:
    return _rerun_clip_image_impl(out_dir, pid, clip_index, prompt_override)


def _rerun_clip_image_impl(out_dir: str, pid: str, clip_index: int, prompt_override: str = None) -> dict:
    """Re-generate the start image for a single clip. Returns {job_id, filename} or raises."""
    state = load_pipeline_state(out_dir, pid)
    if not state:
        raise ValueError(f"Pipeline {pid} not found")
    clips = state.get("clips", [])
    if clip_index < 0 or clip_index >= len(clips):
        raise ValueError(f"Clip index {clip_index} out of range (0-{len(clips)-1})")

    clip = clips[clip_index]
    prompt = prompt_override or clip.get("image_prompt", "")
    if not prompt:
        raise ValueError("No image prompt for this clip")

    # Reference art-style lock: reruns re-apply the detected style prefix
    # (the pipeline prepends it at generation time, so the saved
    # image_prompt does not carry it). Motion-effect strip mirrors
    # _gen_image for the same reason.
    prompt = _strip_motion_effects(prompt)
    _style_prefix = _style_prefix_for((state.get("_params_snapshot") or {}).get("_reference_style") or "")
    if _style_prefix and not prompt.lower().startswith("maintain the same"):
        prompt = _style_prefix + prompt
    validation_params = _director_params_from_saved_state(state)
    _validate_director_models(validation_params, stages=("image",))

    # Determine the output directory before resolving the generated anchor:
    # unlike the user's upload path, that anchor is stored as a basename in
    # the pipeline workspace so saved pipelines remain portable.
    pipeline_file = _find_pipeline_file(out_dir, pid)
    clip_out_dir = os.path.dirname(pipeline_file) if pipeline_file else out_dir

    user_ref_path = state.get("reference_image_path") or ""
    ref_path = user_ref_path if os.path.isfile(user_ref_path) else ""
    persisted_anchor = state.get("generated_reference_image_filename") or ""
    anchor_to_persist = ""
    if (
        not ref_path
        and persisted_anchor
        and os.path.basename(persisted_anchor) == persisted_anchor
    ):
        candidate = os.path.join(clip_out_dir, persisted_anchor)
        if os.path.isfile(candidate):
            ref_path = candidate

    # Backward-compatible recovery for pipelines saved before generated
    # anchors were persisted: a valid first clip image is the safest visual
    # identity reference available.
    if not ref_path:
        for saved_clip in clips:
            saved_start = saved_clip.get("start_image_filename") or ""
            if not saved_start or os.path.basename(saved_start) != saved_start:
                continue
            candidate = os.path.join(clip_out_dir, saved_start)
            if os.path.isfile(candidate):
                ref_path = candidate
                anchor_to_persist = saved_start
                break

    # Build refs: main + character + location
    all_refs = []
    seen_refs = set()
    if ref_path:
        resolved_ref = os.path.normcase(os.path.realpath(ref_path))
        seen_refs.add(resolved_ref)
        all_refs.append(ref_path)
    for cp in (state.get("character_ref_paths") or []):
        resolved = os.path.normcase(os.path.realpath(cp)) if cp else ""
        if cp and os.path.isfile(cp) and resolved not in seen_refs:
            seen_refs.add(resolved)
            all_refs.append(cp)
    for lp in (state.get("location_ref_paths") or []):
        resolved = os.path.normcase(os.path.realpath(lp)) if lp else ""
        if lp and os.path.isfile(lp) and resolved not in seen_refs:
            seen_refs.add(resolved)
            all_refs.append(lp)
    role = "editor" if all_refs else "creator"
    image_model = _director_image_role_model(validation_params, role)
    image_loras = _director_image_role_loras(validation_params, role)
    image_params = _director_image_params(validation_params, image_model)
    all_refs = _limit_director_image_refs(image_model, all_refs, pid=pid)
    prompt = _director_role_prompt(prompt, image_loras, role)

    gen_params = {
        "model_type": image_model,
        "prompt": prompt,
        "image_refs": all_refs,
        "image_mode": 1,
        "image_prompt_type": "",
        "num_inference_steps": image_params.get("num_inference_steps", 8),
        "guidance_scale": image_params.get("guidance_scale", 1),
        # A legacy no-reference pipeline must bootstrap with plain T2I.  Once
        # this image is saved below it becomes the durable anchor for every
        # later clip rerun.
        "video_prompt_type": "KI" if all_refs else "",
        "resolution": image_params.get("resolution", "1280x720"),
        "seed": -1,
        "settings_version": 2.52,
        "generation_mode": "image",
        "repeat_generation": 1,
        "negative_prompt": "",
        "video_length": 1,
        "activated_loras": image_loras.get("activated_loras", []),
        "loras_multipliers": " ".join(
            m.split(";")[0] for m in (image_loras.get("loras_multipliers", "") or "").split(" ") if m
        ),
        "_director_pipeline_id": pid,
        "_director_detached_operation": True,
    }
    with _pipeline_lock:
        repair_control = _pipeline_repairs.get(pid)
        repair_operation_id = (
            repair_control.get("operation_id") if repair_control else None
        )
    if repair_operation_id:
        gen_params["_director_repair_operation_id"] = repair_operation_id

    output_files = _submit_and_wait(gen_params, timeout_s=600, out_dir=clip_out_dir)
    new_filename = output_files[0] if output_files else ""

    if not new_filename:
        raise RuntimeError(
            "Start-image generation completed without a recorded output."
        )

    if not ref_path:
        anchor_to_persist = new_filename

    # Update the saved pipeline state
    def _update(s):
        s["clips"][clip_index]["start_image_filename"] = new_filename
        # A video generated from the previous start image is still useful
        # history, but it no longer represents this clip's current inputs.
        # Keep its filename for playback/ownership and mark it for regeneration.
        s["clips"][clip_index]["video_stale"] = bool(
            s["clips"][clip_index].get("video_filename")
        )
        if prompt_override:
            s["clips"][clip_index]["image_prompt"] = prompt_override
        if anchor_to_persist:
            s["generated_reference_image_filename"] = anchor_to_persist
            snapshot = s.get("_params_snapshot")
            if isinstance(snapshot, dict):
                snapshot["generated_reference_image_filename"] = (
                    anchor_to_persist
                )
    _update_saved_pipeline(out_dir, pid, _update)

    return {"filename": new_filename, "clip_index": clip_index}


def _slice_audio_segment(src_path: str, start_sec: float, duration_sec: float, dst_path: str) -> None:
    """Cut [start, start+duration] out of the source audio with ffmpeg.

    Mirrors shared/utils/audio_video.py's plain-subprocess ffmpeg usage.
    Output is normalized wav so the generation's audio loader never has to
    care what container the song came in.
    """
    import subprocess
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{max(0.0, float(start_sec)):.3f}",
        "-t", f"{max(0.1, float(duration_sec)):.3f}",
        "-i", src_path,
        "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
        dst_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _audio_timeline_start(planned_clips: list[dict]) -> float:
    """Return the source-audio time represented by video frame zero."""
    if not planned_clips:
        return 0.0
    try:
        start_sec = float((planned_clips[0] or {}).get("start", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(start_sec) or start_sec <= 0:
        return 0.0
    return start_sec


def _quantize_clip_frame_schedule(
    requested_frames: list[float], min_frames: int, latent_size: int,
) -> list[int]:
    """Match Director's carried rounding for a sequence of clip lengths."""
    latent_size = max(1, int(latent_size or 1))
    min_frames = max(1, int(min_frames or 1))
    carried: list[int] = []
    carry = 0.0
    for frame_count in requested_frames:
        target = float(frame_count) + carry
        quantized = max(
            round((target - 1) / latent_size) * latent_size + 1,
            min_frames,
        )
        carry = target - quantized
        carried.append(int(quantized))
    return carried


@_exclusive_pipeline_operation
def rerun_clip_video(out_dir: str, pid: str, clip_index: int, prompt_override: str = None) -> dict:
    return _rerun_clip_video_impl(out_dir, pid, clip_index, prompt_override)


def _rerun_clip_video_impl(out_dir: str, pid: str, clip_index: int, prompt_override: str = None) -> dict:
    """Re-generate the video for a single clip. Returns {job_id, filename} or raises."""
    state = load_pipeline_state(out_dir, pid)
    if not state:
        raise ValueError(f"Pipeline {pid} not found")
    clips = state.get("clips", [])
    if clip_index < 0 or clip_index >= len(clips):
        raise ValueError(f"Clip index {clip_index} out of range (0-{len(clips)-1})")

    clip = clips[clip_index]
    prompt = prompt_override or clip.get("video_prompt", "")
    if not prompt:
        raise ValueError("No video prompt for this clip")
    snapshot = state.get("_params_snapshot") or {}
    video_model = state.get("video_model") or "ltx2_22B_distilled_1_1"
    video_loras = state.get("video_loras") or {}
    video_params = state.get("video_params") or {}
    uses_shot_images = shot_images_required(
        _saved_pipeline_shot_image_policy(state)
    )
    validation_params = _director_params_from_saved_state(state)
    validation_params["video_model"] = video_model
    _validate_director_models(validation_params, stages=("video",))

    # Determine the output directory
    pipeline_file = _find_pipeline_file(out_dir, pid)
    clip_out_dir = os.path.dirname(pipeline_file) if pipeline_file else out_dir

    # Build start image path
    start_path = ""
    if uses_shot_images:
        start_img = clip.get("start_image_filename")
        if _invalid_saved_media_numbers(
            [start_img], 1, clip_out_dir, "image",
        ):
            raise ValueError(
                "This clip has no valid start image. Regenerate its start image "
                "before regenerating video."
            )
        start_path = os.path.join(clip_out_dir, start_img)

    # Reconstruct the SAME carried frame schedule used by a full Director run.
    # Generators only accept lengths on a model-specific latent lattice. A
    # standalone rerun previously floored this one clip independently, losing
    # as many as latent_size-1 frames every time (over a second on a 32-frame
    # lattice). Those losses shifted every later cut against the soundtrack.
    fps = snapshot.get("fps", 16)
    try:
        model_def = _wgp.get_model_def(video_model)
        if model_def and model_def.get("fps"):
            fps = model_def["fps"]
    except Exception:
        pass
    try:
        fps = float(fps)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("invalid fps")
    except (TypeError, ValueError):
        fps = 16.0
    try:
        min_frames, _, latent_size = _wgp.get_model_min_frames_and_step(video_model)
    except Exception:
        min_frames, latent_size = 17, 8

    requested_frames = []
    planned_clips = []
    for saved_clip in clips:
        saved_plan = saved_clip.get("planned_clip") or {}
        planned_clips.append(saved_plan)
        try:
            saved_duration = float(saved_plan.get("duration_sec") or 0)
        except (TypeError, ValueError):
            saved_duration = 0.0
        if saved_duration <= 0:
            try:
                saved_duration = float(saved_plan.get("end", 0) or 0) - float(
                    saved_plan.get("start", 0) or 0
                )
            except (TypeError, ValueError):
                saved_duration = 0.0
        if saved_duration > 0:
            frame_count = round(saved_duration * fps)
        else:
            try:
                frame_count = int(saved_plan.get("duration_frames") or 0)
            except (TypeError, ValueError):
                frame_count = 0
            if frame_count <= 0:
                frame_count = round(20 * fps)
        requested_frames.append(max(
            frame_count, round(5 * fps),
        ))
    frame_schedule = _quantize_clip_frame_schedule(
        requested_frames, min_frames, latent_size,
    )
    video_length = frame_schedule[clip_index]
    print(
        f"[Pipeline {pid}] Clip {clip_index} rerun frame budget: "
        f"{video_length} frames at {fps:g} fps ({video_length / fps:.3f}s)"
    )

    gen_params = {
        "model_type": video_model,
        "prompt": prompt,
        "image_mode": 0,
        "image_prompt_type": "S" if start_path else "",
        "num_inference_steps": video_params.get("num_inference_steps", 8),
        "guidance_scale": video_params.get("guidance_scale", 1),
        "resolution": video_params.get("resolution", "1280x720"),
        "video_length": video_length,
        # One clip = ONE window — same convention as the original pipeline
        # (see the sliding_window_frames comment there): the window must be
        # STRICTLY greater than the clip's frame count after wgp's latent
        # quantization, or wgp splits the clip into multiple windows saved
        # as SEPARATE files and this rerun records only the first one (a
        # 13s clip came back as its first 5s, shifting every later clip in
        # the rejoined video and breaking lip sync). Without this key the
        # primary-settings default (129 frames) applied.
        "sliding_window_size": video_length + latent_size + 1,
        "seed": -1,
        "settings_version": 2.52,
        "generation_mode": "video",
        "repeat_generation": 1,
        "negative_prompt": "",
        "activated_loras": video_loras.get("activated_loras", []),
        "loras_multipliers": " ".join(
            m.split(";")[0] for m in (video_loras.get("loras_multipliers", "") or "").split(" ") if m
        ),
        "_director_pipeline_id": pid,
        "_director_detached_operation": True,
    }
    with _pipeline_lock:
        repair_control = _pipeline_repairs.get(pid)
        repair_operation_id = (
            repair_control.get("operation_id") if repair_control else None
        )
    if repair_operation_id:
        gen_params["_director_repair_operation_id"] = repair_operation_id
    if start_path:
        gen_params["image_start"] = start_path
    elif (
        _saved_pipeline_shot_image_policy(state)
        == SHOT_IMAGES_DIRECT_REFERENCES
    ):
        direct_refs = [
            path for path in _director_visual_reference_paths(validation_params)
            if os.path.isfile(path)
        ]
        if direct_refs:
            gen_params["image_refs"] = direct_refs

    # Soundtrack conditioning. The original pipeline run passes the FULL
    # song as audio_guide (audio_prompt_type "A") and wgp slices it across
    # clips internally — a single-clip rerun gets none of that context, so
    # without this block the model invents its own audio and the
    # regenerated clip no longer matches the music video's soundtrack.
    # Slice the song to this clip's window and condition on it, mirroring
    # the segment the clip was originally generated against.
    pipeline_type = state.get("pipeline_type") or snapshot.get("pipeline_type") or "music_video"
    audio_path = snapshot.get("audio_path") or ""
    audio_origin_frames = round(_audio_timeline_start(planned_clips) * fps)
    clip_start = (
        audio_origin_frames + sum(frame_schedule[:clip_index])
    ) / fps
    clip_duration_sec = video_length / fps
    slice_path = None
    if pipeline_type != "short_film_story" and audio_path and os.path.isfile(audio_path):
        pid_token = re.sub(r"[^A-Za-z0-9_-]", "_", pid)[:32]
        slice_path = os.path.join(
            clip_out_dir,
            f"_rerun_audio_{pid_token}_c{clip_index}_{uuid.uuid4().hex[:8]}.wav",
        )
        try:
            _slice_audio_segment(
                audio_path, clip_start, clip_duration_sec, slice_path,
            )
            gen_params["audio_prompt_type"] = "A"
            gen_params["audio_guide"] = slice_path
            if snapshot.get("audio_scale") is not None:
                gen_params["audio_scale"] = snapshot["audio_scale"]
            print(f"[Pipeline {pid}] Clip {clip_index} rerun conditioned on song segment "
                  f"{float(clip_start):.3f}s-"
                  f"{float(clip_start) + float(clip_duration_sec):.3f}s")
        except Exception as e:
            print(f"[Pipeline {pid}] Clip {clip_index} audio slice failed; "
                  f"regenerating without soundtrack conditioning: {e}")

    try:
        output_files = _submit_and_wait(
            gen_params, timeout_s=3600, out_dir=clip_out_dir,
        )
    finally:
        if slice_path and os.path.isfile(slice_path):
            try:
                os.remove(slice_path)
            except OSError:
                pass
    # Sliding-window generations save CUMULATIVE progress files (each save
    # is the video so far) — the LAST file is the complete clip. With the
    # single-window sizing above there is normally exactly one file, but
    # taking the last is correct in every case; taking the first recorded
    # a 5s preview of a 13s clip.
    new_filename = output_files[-1] if output_files else ""

    if not new_filename:
        raise RuntimeError(
            "Video generation completed without a recorded output."
        )

    def _update(s):
        s["clips"][clip_index]["video_filename"] = new_filename
        s["clips"][clip_index]["video_stale"] = False
        if new_filename not in s.get("output_files", []):
            s.setdefault("output_files", []).append(new_filename)
        if prompt_override:
            s["clips"][clip_index]["video_prompt"] = prompt_override
    _update_saved_pipeline(out_dir, pid, _update)

    return {"filename": new_filename, "clip_index": clip_index}


@_exclusive_pipeline_operation
def rejoin_clips(out_dir: str, pid: str) -> dict:
    return _rejoin_clips_impl(out_dir, pid)


def _director_rejoin_model_identity(state: dict) -> dict | None:
    """Resolve mutually compatible saved Director video-model declarations."""
    snapshot = state.get("_params_snapshot")
    declarations = []
    for value in (
        state.get("video_model"),
        snapshot.get("video_model") if isinstance(snapshot, dict) else None,
    ):
        model_type = str(value or "").strip()
        if model_type and model_type not in declarations:
            declarations.append(model_type)
    if not declarations:
        return None
    resolved = []
    for model_type in declarations:
        try:
            base_model_type = str(
                _wgp.get_base_model_type(model_type) or ""
            ).strip()
        except Exception as exc:
            raise ValueError(
                "Saved pipeline video model metadata is ambiguous."
            ) from exc
        if not base_model_type:
            raise ValueError(
                "Saved pipeline video model metadata is ambiguous."
            )
        resolved.append((model_type, base_model_type))
    base_models = sorted({base for _, base in resolved})
    if len(base_models) != 1:
        raise ValueError("Saved pipeline uses mixed video model families.")
    source_models = sorted({model for model, _ in resolved})
    base_model_type = base_models[0]
    return {
        "model_type": (
            source_models[0] if len(source_models) == 1 else base_model_type
        ),
        "source_model_types": source_models,
        "base_model_type": base_model_type,
        "requires_h3_audio_safety": base_model_type in {
            "minimax_h3", "minimax_h3_ref2va",
        },
    }


def _director_rejoin_h3_adoption_verified(
    sidecar: dict, model_identity: dict | None,
) -> bool:
    """Require current H3 policy and model provenance before adoption."""
    if (
        not isinstance(model_identity, dict)
        or model_identity.get("requires_h3_audio_safety") is not True
    ):
        return True
    if not isinstance(sidecar, dict):
        return False
    from services.h3_audio_safety import DEFAULT_TARGET_DBTP, POLICY_VERSION

    params = sidecar.get("params")
    stats = sidecar.get("h3_audio_true_peak")
    return bool(
        isinstance(params, dict)
        and isinstance(stats, dict)
        and sidecar.get("model_type") == model_identity.get("model_type")
        and params.get("model_type") == model_identity.get("model_type")
        and params.get("base_model_type")
            == model_identity.get("base_model_type")
        and params.get("source_model_types")
            == model_identity.get("source_model_types")
        and params.get("h3_audio_true_peak") == stats
        and stats.get("policy_version") == POLICY_VERSION
        and stats.get("target_dbtp") == DEFAULT_TARGET_DBTP
        and stats.get("verified") is True
    )


def _enforce_director_rejoin_h3_final_audio(
    output_path: str, model_identity: dict | None,
) -> dict | None:
    """Verify a staged H3 Director rejoin and clean it on failure."""
    if (
        not isinstance(model_identity, dict)
        or model_identity.get("requires_h3_audio_safety") is not True
    ):
        return None
    try:
        stats = _wgp.enforce_h3_final_audio_safety(
            output_path,
            str(model_identity.get("base_model_type") or ""),
        )
        if not isinstance(stats, dict) or stats.get("verified") is not True:
            raise RuntimeError("H3 true-peak attestation was not verified")
    except Exception:
        _wgp.remove_failed_h3_final_output(output_path)
        raise
    return dict(stats)


def _rejoin_clips_impl(out_dir: str, pid: str) -> dict:
    """Re-join all clips from a saved pipeline using current best versions. Returns {filename}."""
    state = load_pipeline_state(out_dir, pid)
    if not state:
        raise ValueError(f"Pipeline {pid} not found")
    model_identity = _director_rejoin_model_identity(state)

    pipeline_file = _find_pipeline_file(out_dir, pid)
    clip_out_dir = os.path.dirname(pipeline_file) if pipeline_file else out_dir

    clips = state.get("clips", [])
    stale_clip_numbers = [
        str(index + 1)
        for index, clip in enumerate(clips)
        if clip.get("video_stale")
    ]
    if stale_clip_numbers:
        raise ValueError(
            "Regenerate stale video clip(s) "
            f"{', '.join(stale_clip_numbers)} before rejoining."
        )

    invalid_start_numbers = (
        _invalid_saved_media_numbers(
            [clip.get("start_image_filename") for clip in clips],
            len(clips),
            clip_out_dir,
            "image",
        )
        if shot_images_required(_saved_pipeline_shot_image_policy(state))
        else []
    )
    if invalid_start_numbers:
        invalid_labels = ", ".join(
            str(index) for index in invalid_start_numbers
        )
        raise ValueError(
            "Regenerate missing or invalid start image(s) for clip(s) "
            f"{invalid_labels} before rejoining."
        )

    invalid_video_numbers = _invalid_saved_media_numbers(
        [clip.get("video_filename") for clip in clips],
        len(clips),
        clip_out_dir,
        "video",
    )
    if invalid_video_numbers:
        invalid_labels = ", ".join(
            str(index) for index in invalid_video_numbers
        )
        raise ValueError(
            "Regenerate missing or invalid video clip(s) "
            f"{invalid_labels} before rejoining."
        )

    video_files = [
        os.path.join(clip_out_dir, clip["video_filename"])
        for clip in clips
    ]

    if len(video_files) < 2:
        raise ValueError(f"Need at least 2 video clips to rejoin, found {len(video_files)}")

    # Lay the pristine source song over the rejoined video, exactly like the
    # original pipeline's multiclip join does — per-clip embedded audio is a
    # windowed generation, the full track is the real soundtrack. Story-mode
    # pipelines (no song) concat with the clips' own audio.
    snapshot = state.get("_params_snapshot") or {}
    audio_path = snapshot.get("audio_path") or None
    if audio_path and not os.path.isfile(audio_path):
        raise ValueError(
            "The original Director audio input is missing; restore the exact "
            "file before rejoining."
        )
    audio_descriptor = None
    current_audio_descriptor = None
    if audio_path:
        audio_digest = hashlib.sha256()
        audio_size = 0
        with open(audio_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                audio_size += len(chunk)
                audio_digest.update(chunk)
        current_audio_descriptor = {
            "basename": os.path.basename(audio_path),
            "sha256": audio_digest.hexdigest(),
            "size": audio_size,
        }
        for descriptor in (state.get("recovery") or {}).get("inputs") or []:
            if (
                isinstance(descriptor, dict)
                and descriptor.get("field") == "audio_path:0"
            ):
                audio_descriptor = descriptor
                break
        if isinstance(audio_descriptor, dict):
            if (
                audio_size != audio_descriptor.get("size")
                or current_audio_descriptor["sha256"]
                    != audio_descriptor.get("sha256")
            ):
                raise ValueError(
                    "The original Director audio input changed; restore the "
                    "exact file before rejoining."
                )
    audio_start_sec = _audio_timeline_start([
        clip.get("planned_clip") or {} for clip in clips
    ]) if audio_path else 0.0

    component_descriptors = []
    for path in video_files:
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        component_descriptors.append({
            "basename": os.path.basename(path),
            "sha256": digest.hexdigest(),
            "size": size,
        })
    join_identity = hashlib.sha256(json.dumps({
        "audio_start_ms": round(audio_start_sec * 1000),
        "audio": current_audio_descriptor or "",
        "components": component_descriptors,
        "pipeline_id": pid,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    producer_unit_id = f"unit:v1:{join_identity}"
    output_name = f"director_rejoin_{pid}_{join_identity[:16]}.mp4"
    output_path = os.path.join(clip_out_dir, output_name)
    sidecar_path = os.path.splitext(output_path)[0] + ".meta.json"

    if os.path.isfile(output_path) and os.path.isfile(sidecar_path):
        try:
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                existing_sidecar = json.load(handle)
            size = os.path.getsize(output_path)
            digest = hashlib.sha256()
            with open(output_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if (
                existing_sidecar.get("producer_unit_id") == producer_unit_id
                and existing_sidecar.get("producer_media_size") == size
                and existing_sidecar.get("producer_media_sha256") == digest.hexdigest()
                and _director_rejoin_h3_adoption_verified(
                    existing_sidecar, model_identity,
                )
            ):
                return {"filename": output_name, "adopted": True}
        except (OSError, ValueError, TypeError):
            pass
        for stale_path in (sidecar_path, output_path):
            try:
                os.remove(stale_path)
            except OSError:
                pass

    staging_path = os.path.join(
        clip_out_dir,
        f".{output_name}.{uuid.uuid4().hex[:8]}.staging.mp4",
    )
    temp_sidecar = None
    promoted = False
    try:
        # concatenate_multi_clip_videos is the join the original pipeline
        # uses (ffmpeg concat FILTER, re-encodes to a uniform format). The
        # previously-called wgp.concatenate_videos never existed — this path
        # was unreachable until the video_filename backfill fix, so the
        # AttributeError only surfaced now.
        ok = _wgp.concatenate_multi_clip_videos(
            video_files,
            staging_path,
            audio_path,
            audio_start_sec=audio_start_sec,
        )
        if not ok or not os.path.isfile(staging_path):
            raise RuntimeError("ffmpeg concatenation failed (see server log for the clip that broke it)")
        audio_safety_stats = _enforce_director_rejoin_h3_final_audio(
            staging_path, model_identity,
        )
        os.replace(staging_path, output_path)
        promoted = True
        media_size = os.path.getsize(output_path)
        media_digest = hashlib.sha256()
        with open(output_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                media_digest.update(chunk)
        policy = snapshot.get("_maestro_access_policy")
        if not isinstance(policy, dict):
            policy = {
                "private": bool(snapshot.get("private_output", False)),
                "explicit": bool(snapshot.get("explicit_output", False)),
                "owner_session_id": snapshot.get("_maestro_session_id"),
            }
        sidecar = {
            "params": {"_director_pipeline_id": pid},
            "director_pipeline_id": pid,
            "artifact_class": "final",
            "generation_mode": "video",
            "output_filename": output_name,
            "created_at": time.time(),
            "producer_unit_id": producer_unit_id,
            "producer_unit_kind": "director_rejoin",
            "producer_unit_dependencies": [
                item["sha256"] for item in component_descriptors
            ],
            "producer_media_sha256": media_digest.hexdigest(),
            "producer_media_size": media_size,
        }
        if isinstance(model_identity, dict):
            sidecar["model_type"] = model_identity["model_type"]
            sidecar["params"].update({
                "model_type": model_identity["model_type"],
                "source_model_types": list(
                    model_identity["source_model_types"]
                ),
                "base_model_type": model_identity["base_model_type"],
            })
        if isinstance(audio_safety_stats, dict):
            sidecar["h3_audio_true_peak"] = dict(audio_safety_stats)
            sidecar["params"]["h3_audio_true_peak"] = dict(
                audio_safety_stats
            )
        from services.output_access import stamp_sidecar_policy
        stamp_sidecar_policy(
            sidecar, policy, workspace=state.get("workspace") or "default",
        )
        temp_sidecar = f"{sidecar_path}.{uuid.uuid4().hex[:8]}.tmp"
        with open(temp_sidecar, "w", encoding="utf-8") as handle:
            json.dump(sidecar, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_sidecar, sidecar_path)
        print(f"[Pipeline] Rejoined {len(video_files)} clips -> {output_name}")

        # Update pipeline state
        def _update(s):
            if output_name not in s.get("output_files", []):
                s.setdefault("output_files", []).append(output_name)
        _update_saved_pipeline(out_dir, pid, _update)

        return {"filename": output_name}
    except Exception as e:
        for failed_path in (
            temp_sidecar,
            staging_path,
            sidecar_path if promoted else None,
            output_path if promoted else None,
        ):
            if not failed_path:
                continue
            try:
                os.remove(failed_path)
            except OSError:
                pass
        raise RuntimeError(f"Rejoin failed: {e}")


def _plan_pipeline_repair(out_dir: str, pid: str, state: dict) -> dict:
    """Build a deterministic repair plan from recorded files on disk."""
    pipeline_file = _find_pipeline_file(out_dir, pid)
    if not pipeline_file:
        raise ValueError(f"Pipeline {pid} not found")
    clip_out_dir = os.path.dirname(pipeline_file)
    clips = state.get("clips") or []
    requires_shot_images = shot_images_required(
        _saved_pipeline_shot_image_policy(state)
    )
    invalid_images = (
        {
            number - 1
            for number in _invalid_saved_media_numbers(
                [clip.get("start_image_filename") for clip in clips],
                len(clips),
                clip_out_dir,
                "image",
            )
        }
        if requires_shot_images
        else set()
    )
    invalid_videos = {
        number - 1
        for number in _invalid_saved_media_numbers(
            [clip.get("video_filename") for clip in clips],
            len(clips),
            clip_out_dir,
            "video",
        )
    }
    image_indices = sorted(invalid_images)
    video_indices = sorted(
        invalid_videos
        | invalid_images
        | {
            index
            for index, clip in enumerate(clips)
            if clip.get("video_stale")
        }
    )

    missing_image_prompts = [
        index + 1 for index in image_indices
        if not str(clips[index].get("image_prompt") or "").strip()
    ]
    if missing_image_prompts:
        labels = ", ".join(str(index) for index in missing_image_prompts)
        raise ValueError(
            f"Missing image prompt for repair clip(s) {labels}."
        )
    missing_video_prompts = [
        index + 1 for index in video_indices
        if not str(clips[index].get("video_prompt") or "").strip()
    ]
    if missing_video_prompts:
        labels = ", ".join(str(index) for index in missing_video_prompts)
        raise ValueError(
            f"Missing video prompt for repair clip(s) {labels}."
        )

    should_rejoin = len(clips) >= 2
    return {
        "image_indices": image_indices,
        "video_indices": video_indices,
        "should_rejoin": should_rejoin,
        "clip_count": len(clips),
        "total": (
            len(image_indices)
            + len(video_indices)
            + (1 if should_rejoin else 0)
        ),
    }


def _repair_queue_message(plan: dict) -> str:
    parts = []
    image_count = len(plan["image_indices"])
    video_count = len(plan["video_indices"])
    if image_count:
        parts.append(f"{image_count} image{'s' if image_count != 1 else ''}")
    if video_count:
        parts.append(f"{video_count} video{'s' if video_count != 1 else ''}")
    if plan["should_rejoin"]:
        parts.append("final join")
    return "Queued " + (", ".join(parts) if parts else "repair check")


def _persist_repair_state_unlocked(
    out_dir: str,
    pid: str,
    control: dict,
    *,
    replace: bool = False,
    **updates,
) -> Optional[dict]:
    """Persist repair status while the caller holds control['state_lock']."""
    operation_id = control["operation_id"]
    now = time.time()

    def _update(state):
        existing = state.get("repair")
        if (
            not replace
            and isinstance(existing, dict)
            and existing.get("operation_id") != operation_id
        ):
            return
        repair = {} if replace else dict(existing or {})
        repair.update(updates)
        repair["operation_id"] = operation_id
        repair["updated_at"] = now
        state["repair"] = repair

    saved = _update_saved_pipeline(out_dir, pid, _update)
    repair = (saved or {}).get("repair")
    if not isinstance(repair, dict) or repair.get("operation_id") != operation_id:
        return None
    snapshot = dict(repair)
    with _pipeline_lock:
        current = _pipeline_repairs.get(pid)
        if current is control:
            current["snapshot"] = snapshot
    return snapshot


def _persist_repair_state(
    out_dir: str,
    pid: str,
    control: dict,
    *,
    replace: bool = False,
    **updates,
) -> Optional[dict]:
    with control["state_lock"]:
        return _persist_repair_state_unlocked(
            out_dir, pid, control, replace=replace, **updates,
        )


def _raise_if_repair_cancelled(control: dict) -> None:
    if control["cancel_event"].is_set():
        raise _RepairCancelledError("Repair cancelled")


def _finish_pipeline_repair(
    out_dir: str,
    pid: str,
    control: dict,
    *,
    status: str,
    phase: str,
    current: int,
    total: int,
    message: str,
    error: Optional[str] = None,
    error_code: Optional[str] = None,
    failure_details: Optional[dict] = None,
    result_filename: Optional[str] = None,
) -> Optional[dict]:
    with control["state_lock"]:
        # Decide completion-versus-cancellation while holding the same lock
        # used by cancel_pipeline_repair. Whichever path enters first wins:
        # completion marks the control as finishing, while cancellation sets
        # the absorbing event before a terminal snapshot can be chosen.
        with _pipeline_lock:
            current_control = _pipeline_repairs.get(pid)
            if current_control is control:
                current_control["finishing"] = True
            cancel_requested = control["cancel_event"].is_set()
        if status == "completed" and cancel_requested:
            status = "cancelled"
            phase = "cancelled"
            message = "Repair cancelled"
            error = None
        return _persist_repair_state_unlocked(
            out_dir,
            pid,
            control,
            status=status,
            phase=phase,
            current=current,
            total=total,
            clip_index=None,
            message=message,
            error=error,
            error_code=error_code,
            failure_details=failure_details,
            cancel_requested=cancel_requested,
            completed_at=time.time(),
            result_filename=result_filename,
        )


def _run_pipeline_repair(
    out_dir: str,
    pid: str,
    control: dict,
    plan: dict,
) -> None:
    """Run one full Dashboard repair independently of the browser."""
    current = 0
    total = plan["total"]
    clip_count = plan["clip_count"]
    result_filename = None
    try:
        _raise_if_repair_cancelled(control)
        _persist_repair_state(
            out_dir,
            pid,
            control,
            status="running",
            phase="images" if plan["image_indices"] else "videos",
            current=current,
            total=total,
            clip_index=None,
            message="Starting repair",
            error=None,
        )

        for clip_index in plan["image_indices"]:
            _raise_if_repair_cancelled(control)
            _persist_repair_state(
                out_dir,
                pid,
                control,
                status="running",
                phase="images",
                current=current,
                total=total,
                clip_index=clip_index,
                message=f"Generating start image for clip {clip_index + 1} of {clip_count}",
                error=None,
            )
            _rerun_clip_image_impl(out_dir, pid, clip_index)
            _raise_if_repair_cancelled(control)
            current += 1
            _persist_repair_state(
                out_dir,
                pid,
                control,
                status="running",
                phase="images",
                current=current,
                total=total,
                clip_index=clip_index,
                message=f"Finished start image for clip {clip_index + 1}",
                error=None,
            )

        for clip_index in plan["video_indices"]:
            _raise_if_repair_cancelled(control)
            _persist_repair_state(
                out_dir,
                pid,
                control,
                status="running",
                phase="videos",
                current=current,
                total=total,
                clip_index=clip_index,
                message=f"Generating video for clip {clip_index + 1} of {clip_count}",
                error=None,
            )
            _rerun_clip_video_impl(out_dir, pid, clip_index)
            _raise_if_repair_cancelled(control)
            current += 1
            _persist_repair_state(
                out_dir,
                pid,
                control,
                status="running",
                phase="videos",
                current=current,
                total=total,
                clip_index=clip_index,
                message=f"Finished video for clip {clip_index + 1}",
                error=None,
            )

        if plan["should_rejoin"]:
            _raise_if_repair_cancelled(control)
            _persist_repair_state(
                out_dir,
                pid,
                control,
                status="running",
                phase="rejoin",
                current=current,
                total=total,
                clip_index=None,
                message=f"Joining {clip_count} repaired clips",
                error=None,
            )
            result = _rejoin_clips_impl(out_dir, pid)
            result_filename = result.get("filename")
            _raise_if_repair_cancelled(control)
            current += 1

        _finish_pipeline_repair(
            out_dir,
            pid,
            control,
            status="completed",
            phase="completed",
            current=current,
            total=total,
            message=(
                "Repair complete and clips joined"
                if plan["should_rejoin"]
                else "Repair complete"
            ),
            result_filename=result_filename,
        )
    except (GenerationCancelledError, _RepairCancelledError):
        _finish_pipeline_repair(
            out_dir,
            pid,
            control,
            status="cancelled",
            phase="cancelled",
            current=current,
            total=total,
            message="Repair cancelled",
        )
    except Exception as exc:
        print(f"[Pipeline {pid}] Repair failed; see traceback below")
        traceback.print_exc()
        failure_details = _director_failure_details(
            exc, code=_DIRECTOR_REPAIR_FAILED_CODE,
        )
        _finish_pipeline_repair(
            out_dir,
            pid,
            control,
            status="failed",
            phase="failed",
            current=current,
            total=total,
            message=_DIRECTOR_REPAIR_FAILED_MESSAGE,
            error=_DIRECTOR_REPAIR_FAILED_MESSAGE,
            error_code=str(failure_details.get("code") or _DIRECTOR_REPAIR_FAILED_CODE),
            failure_details=failure_details,
        )
    finally:
        with _pipeline_lock:
            if _pipeline_repairs.get(pid) is control:
                _pipeline_repairs.pop(pid, None)
        _release_pipeline_operation(pid)


def _run_pipeline_repair_after_ready(
    out_dir: str,
    pid: str,
    control: dict,
    plan: dict,
) -> None:
    """Keep even a zero-unit worker alive until start publication finishes."""
    ready_event = control.get("ready_event")
    if ready_event is not None:
        ready_event.wait()
    # The starter owns cleanup when publication itself failed. In the rare
    # case a Thread implementation began running before start() raised, do
    # not let that worker execute a repair after the failed reservation.
    if control.get("start_error") is not None:
        return
    _run_pipeline_repair(out_dir, pid, control, plan)


def _repair_start_result(pid: str, control: dict) -> dict:
    """Wait for an atomic start reservation to publish its first snapshot."""
    ready_event = control.get("ready_event")
    if ready_event is not None:
        ready_event.wait()
    start_error = control.get("start_error")
    if start_error is not None:
        raise start_error
    return {
        "pipeline_id": pid,
        "repair": dict(control.get("snapshot") or {}),
    }


def start_pipeline_repair(out_dir: str, pid: str) -> dict:
    """Start or reconnect to a server-owned repair batch."""
    with _pipeline_lock:
        existing = _pipeline_repairs.get(pid)
        if existing is not None:
            control = existing
            starter = False
        else:
            # Claim the operation and publish a reservation in one critical
            # section. A simultaneous duplicate now waits for this starter's
            # persisted snapshot instead of falling into the claim gap and
            # receiving a spurious busy response.
            if not _claim_pipeline_operation_locked(pid):
                raise PipelineBusyError(
                    "Pipeline is still active; try again shortly."
                )
            operation_id = uuid.uuid4().hex[:12]
            control = {
                "operation_id": operation_id,
                "snapshot": {},
                "cancel_event": threading.Event(),
                "state_lock": threading.Lock(),
                "finishing": False,
                "thread": None,
                "ready_event": threading.Event(),
                "start_error": None,
            }
            _pipeline_repairs[pid] = control
            starter = True

    if not starter:
        return _repair_start_result(pid, control)

    try:
        state = load_pipeline_state(out_dir, pid)
        if not state:
            raise ValueError(f"Pipeline {pid} not found")
        plan = _plan_pipeline_repair(out_dir, pid, state)
        started_at = time.time()
        initial = {
            "operation_id": control["operation_id"],
            "status": "queued",
            "phase": "queued",
            "current": 0,
            "total": plan["total"],
            "clip_index": None,
            "message": _repair_queue_message(plan),
            "error": None,
            "error_code": None,
            "failure_details": None,
            "cancel_requested": False,
            "started_at": started_at,
            "updated_at": started_at,
            "completed_at": None,
            "result_filename": None,
        }
        with _pipeline_lock:
            if _pipeline_repairs.get(pid) is control:
                control["snapshot"] = dict(initial)

        persisted = _persist_repair_state(
            out_dir, pid, control, replace=True, **initial,
        )
        if not persisted:
            raise RuntimeError("Could not persist repair status")

        thread = threading.Thread(
            target=_run_pipeline_repair_after_ready,
            args=(out_dir, pid, control, plan),
            daemon=False,
            name=f"director-repair-{pid}",
        )
        with _pipeline_lock:
            control["thread"] = thread
        thread.start()
        control["ready_event"].set()
        return {"pipeline_id": pid, "repair": persisted}
    except BaseException as exc:
        try:
            failure_details = _director_failure_details(
                exc, code=_DIRECTOR_REPAIR_FAILED_CODE,
            )
            _finish_pipeline_repair(
                out_dir,
                pid,
                control,
                status="failed",
                phase="failed",
                current=0,
                total=(control.get("snapshot") or {}).get("total", 0),
                message=_DIRECTOR_REPAIR_FAILED_MESSAGE,
                error=_DIRECTOR_REPAIR_FAILED_MESSAGE,
                error_code=str(
                    failure_details.get("code") or _DIRECTOR_REPAIR_FAILED_CODE
                ),
                failure_details=failure_details,
            )
        except Exception:
            traceback.print_exc()
        with _pipeline_lock:
            control["start_error"] = exc
            if _pipeline_repairs.get(pid) is control:
                _pipeline_repairs.pop(pid, None)
        control["ready_event"].set()
        _release_pipeline_operation(pid)
        raise


def cancel_pipeline_repair(out_dir: str, pid: str) -> Optional[dict]:
    """Request cancellation and abort the repair's in-flight child job."""
    with _pipeline_lock:
        control = _pipeline_repairs.get(pid)
        if not control:
            return None

    # A newly reserved repair has not persisted its operation snapshot yet.
    # Wait outside both locks so the starter can publish (or fail), then
    # revalidate the exact control below. The worker uses the same gate, so
    # cancel never acts on an old/no repair record during this handshake.
    ready_event = control.get("ready_event")
    if ready_event is not None:
        ready_event.wait()

    with control["state_lock"]:
        with _pipeline_lock:
            current = _pipeline_repairs.get(pid)
            if current is not control or current.get("finishing"):
                return dict(control.get("snapshot") or {})
            control["cancel_event"].set()
            # Keep the registry lock through job selection and abort. Without
            # this boundary the old repair could tear down, a successor could
            # register the same pid, and this late abort would cancel the
            # successor's child job instead.
            _abort_pipeline_jobs(pid)
        snapshot = _persist_repair_state_unlocked(
            out_dir,
            pid,
            control,
            status="cancelling",
            message="Cancelling repair after the current model step",
            cancel_requested=True,
        )
    return snapshot


def init(
    jobs_dict,
    run_gen_fn,
    wgp_module,
    gen_lock=None,
    active_gen_states=None,
    *,
    recovery_register_parent=None,
    recovery_prepare_parent_state=None,
    recovery_checkpoint_parent=None,
    recovery_prepare_parent_delete=None,
    recovery_remove_parent=None,
    recovery_submit_child=None,
    recovery_verify_child=None,
    recovery_validate_child=None,
    runtime_admission=None,
):
    """Called by launch.py to wire up shared references."""
    global _jobs, _run_generation, _wgp, _gen_lock, _active_gen_states
    global _recovery_register_parent, _recovery_prepare_parent_state
    global _recovery_checkpoint_parent, _recovery_prepare_parent_delete
    global _recovery_remove_parent
    global _recovery_submit_child, _recovery_verify_child
    global _recovery_validate_child, _runtime_admission
    _jobs = jobs_dict
    _run_generation = run_gen_fn
    _wgp = wgp_module
    _gen_lock = gen_lock
    _active_gen_states = active_gen_states
    _recovery_register_parent = recovery_register_parent
    _recovery_prepare_parent_state = recovery_prepare_parent_state
    _recovery_checkpoint_parent = recovery_checkpoint_parent
    _recovery_prepare_parent_delete = recovery_prepare_parent_delete
    _recovery_remove_parent = recovery_remove_parent
    _recovery_submit_child = recovery_submit_child
    _recovery_verify_child = recovery_verify_child
    _recovery_validate_child = recovery_validate_child
    _runtime_admission = runtime_admission


class _DirectorOutputs(list):
    """List-compatible outputs that retain exact Director clip ownership."""

    def __init__(self, values, clip_output_files=None):
        super().__init__(values)
        self.clip_output_files = dict(clip_output_files or {})


class _GenerationTimeoutError(RuntimeError):
    def __init__(self, output_files: _DirectorOutputs):
        super().__init__("Generation timed out")
        self.output_files = output_files


class GenerationCancelledError(RuntimeError):
    """A detached Dashboard generation was cancelled after settling."""

    def __init__(self, output_files: _DirectorOutputs):
        super().__init__("Re-run cancelled")
        self.output_files = output_files


def _director_job_outputs(job: dict) -> _DirectorOutputs:
    """Collapse multi-window files to the final output for each clip."""
    snapshot = snapshot_job(job)
    output_files = list(snapshot.get("output_files") or [])
    clip_outputs = snapshot.get("clip_output_files") or {}
    if not isinstance(clip_outputs, dict) or not clip_outputs:
        return _DirectorOutputs(output_files)

    indexed = []
    for index, filename in clip_outputs.items():
        try:
            indexed.append((int(index), filename))
        except (TypeError, ValueError):
            continue
    indexed.sort(key=lambda item: item[0])
    collapsed = [filename for _, filename in indexed if filename]
    join_output = snapshot.get("join_output_file")
    if join_output and join_output not in collapsed:
        collapsed.append(join_output)
    return _DirectorOutputs(
        collapsed or output_files,
        {index: filename for index, filename in indexed if filename},
    )


def _normalized_child_unit(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "")
    try:
        variant = int(value.get("variant", 0) or 0)
        index = int(value.get("index", 0) or 0)
    except (TypeError, ValueError):
        return None
    if (
        not kind
        or len(kind) > 64
        or re.fullmatch(r"[A-Za-z0-9_.-]+", kind) is None
        or variant < 0
        or index < 0
    ):
        return None
    return {"kind": kind, "variant": variant, "index": index}


def _child_unit_token(unit: dict) -> str:
    payload = json.dumps(unit, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _director_child_job_id(pid: str, unit: dict, attempt: int) -> str:
    payload = json.dumps(
        {"parent": pid, "unit": unit, "attempt": attempt},
        sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"director-{pid}-{attempt}-{digest}"


def _recovered_child_outputs(
    pid: str, unit: dict, out_dir: str,
) -> _DirectorOutputs | None:
    if not callable(_recovery_validate_child):
        return None
    token = _child_unit_token(unit)
    with _pipeline_lock:
        pipeline = _pipelines.get(pid) or {}
        recovery = pipeline.get("_recovery") or {}
        entry = dict((recovery.get("children") or {}).get(token) or {})
    if entry.get("state") != "completed":
        return None
    verified = _recovery_validate_child(out_dir, entry.get("evidence"))
    if not isinstance(verified, dict):
        return None
    outputs = verified.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return None
    return _DirectorOutputs(
        outputs,
        verified.get("clip_output_files") or {},
    )


def _recovered_image_slots_complete(
    pid: str,
    out_dir: str,
    params: dict,
    clip_plans: list[dict],
    clip_images,
    clip_keyframes,
) -> bool:
    """Require exact cardinality and sealed evidence for every image slot."""
    if not isinstance(clip_images, list) or len(clip_images) != len(clip_plans):
        return False
    if (
        not isinstance(clip_keyframes, list)
        or len(clip_keyframes) != len(clip_plans)
    ):
        return False

    def _matches(unit: dict, filename: str) -> bool:
        if not filename or not os.path.isfile(os.path.join(out_dir, filename)):
            return False
        recovered = _recovered_child_outputs(pid, unit, out_dir)
        return recovered is not None and filename in recovered

    generated_anchor = str(
        params.get("generated_reference_image_filename") or ""
    )
    if generated_anchor and not _matches(
        {"kind": "image_anchor", "variant": 0, "index": 0},
        generated_anchor,
    ):
        return False

    for index, (plan, filename) in enumerate(zip(clip_plans, clip_images)):
        if not _matches(
            {"kind": "image_start", "variant": 0, "index": index},
            str(filename or ""),
        ):
            return False
        expected_keyframe_indices = []
        for keyframe_index, prompt in enumerate(
            plan.get("keyframe_prompts", []) or []
        ):
            if isinstance(prompt, dict):
                prompt = prompt.get(
                    "prompt", prompt.get("image_prompt", str(prompt)),
                )
            elif not isinstance(prompt, str):
                prompt = str(prompt)
            if prompt and prompt.strip():
                expected_keyframe_indices.append(keyframe_index)
        saved_keyframes = clip_keyframes[index]
        if (
            not isinstance(saved_keyframes, list)
            or len(saved_keyframes) != len(expected_keyframe_indices)
        ):
            return False
        for keyframe_index, keyframe_filename in zip(
            expected_keyframe_indices, saved_keyframes,
        ):
            if not _matches(
                {
                    "kind": "image_keyframe",
                    "variant": index,
                    "index": keyframe_index,
                },
                str(keyframe_filename or ""),
            ):
                return False
    return True


def _checkpoint_child_entry(
    pid: str, unit: dict, entry: dict, *, boundary: str,
) -> None:
    token = _child_unit_token(unit)
    with _pipeline_lock:
        pipeline = _pipelines.get(pid)
        if pipeline is None:
            raise RuntimeError("Director parent disappeared")
        recovery = dict(pipeline.get("_recovery") or {})
        children = dict(recovery.get("children") or {})
        children[token] = dict(entry)
        recovery["children"] = children
        pipeline["_recovery"] = recovery
    _require_pipeline_checkpoint(pid, boundary)


def _submit_and_wait(params: dict, timeout_s: float = 600, workspace: str = None, out_dir: str = None) -> list[str]:
    """Submit a generation job and block until it completes.

    Returns list of output filenames. Raises on failure/timeout.
    """
    # Read the operation identity from the caller-owned mapping before taking
    # our private copy.  Detached repair cancellation uses this read as the
    # registration handshake while deliberately holding ``_pipeline_lock``;
    # delaying it until after other recovery work can recreate the historical
    # lock inversion where neither cancellation nor child registration wins.
    pipeline_id = params.get("_director_pipeline_id")
    _detached_operation = bool(params.get("_director_detached_operation"))
    _repair_operation_id = params.get("_director_repair_operation_id")
    params = dict(params)
    recovery_unit = _normalized_child_unit(
        params.pop("_director_recovery_unit", None)
    )
    external_parent_id = str(
        params.pop("_director_recovery_parent_id", "") or ""
    )
    external_job_id = str(
        params.pop("_director_recovery_job_id", "") or ""
    )
    try:
        external_attempt = max(
            0, int(params.pop("_director_recovery_attempt", 0) or 0),
        )
    except (TypeError, ValueError):
        external_attempt = 0
    recovery_entry = None
    recovery_attempt = 0
    if pipeline_id and recovery_unit and out_dir:
        recovered = _recovered_child_outputs(
            pipeline_id, recovery_unit, out_dir,
        )
        if recovered is not None:
            return recovered
        token = _child_unit_token(recovery_unit)
        with _pipeline_lock:
            pipeline_recovery = (
                (_pipelines.get(pipeline_id) or {}).get("_recovery") or {}
            )
            recovery_entry = dict(
                (pipeline_recovery.get("children") or {}).get(token) or {}
            )
        try:
            recovery_attempt = max(
                0, int(recovery_entry.get("attempt", 0) or 0),
            )
        except (TypeError, ValueError):
            recovery_attempt = 0
        job_id = str(recovery_entry.get("job_id") or "") or (
            _director_child_job_id(
                pipeline_id, recovery_unit, recovery_attempt,
            )
        )
        recovery_entry.update({
            "attempt": recovery_attempt,
            "job_id": job_id,
            "state": "submitted",
            "unit": recovery_unit,
        })
        _checkpoint_child_entry(
            pipeline_id, recovery_unit, recovery_entry,
            boundary=f"{recovery_unit['kind']}-intent",
        )
    elif external_parent_id and recovery_unit:
        recovery_attempt = external_attempt
        job_id = external_job_id or _director_child_job_id(
            external_parent_id, recovery_unit, recovery_attempt,
        )
    else:
        job_id = uuid.uuid4().hex[:8]
    pipeline_params = {}
    pipeline_snapshot = {}
    if pipeline_id:
        # Do not wait on _pipeline_lock here. Detached-repair cancellation
        # deliberately holds that lock while waiting for child registration;
        # blocking before registration creates a lock inversion. A shallow
        # GIL-protected snapshot is sufficient for immutable access-policy
        # fields, and the child registration path remains fully locked below.
        pipeline_snapshot = _pipelines.get(pipeline_id) or {}
        pipeline_params = dict(pipeline_snapshot.get("params") or {})
    access_policy = params.pop("_maestro_access_policy", None) or pipeline_params.get("_maestro_access_policy")
    owner_session_id = params.pop("_maestro_session_id", None) or pipeline_params.get("_maestro_session_id")
    source_remote = bool(pipeline_snapshot.get("source_remote", False))
    job = {
        "id": job_id,
        "status": "queued",
        "phase": "registered",
        "pause_reason": None,
        "progress": 0,
        "step": 0,
        "total_steps": 0,
        "phase": "",
        "message": "Queued",
        "created_at": time.time(),
        "params": params,
        "output_files": [],
        "error": None,
        "workspace": workspace,
        "out_dir": out_dir,
        "session_id": owner_session_id,
        "access_policy": access_policy,
        "private": bool((access_policy or {}).get("private", False)),
        "explicit": bool((access_policy or {}).get("explicit", False)),
        # The request context does not propagate into this background
        # Director thread.  Carry the captured parent origin explicitly so
        # local queue priority still applies to every child generation.
        "source_remote": source_remote,
    }
    if recovery_unit:
        job["recovery_unit"] = {
            **recovery_unit,
            "state": "submitted",
        }
    _dir_pid = pipeline_id
    _skip_generation = False

    def _run_tracked_generation() -> None:
        try:
            # A repair cancellation may win before this newly published
            # child thread begins executing. Do not invoke generation for a
            # detached repair child that registration already made terminal.
            # Ordinary pipeline cancellation still enters _run_generation so
            # its existing settle path can publish already-produced outputs.
            if _skip_generation:
                return
            _run_generation(job_id)
        finally:
            if _dir_pid:
                with _pipeline_lock:
                    child_jobs = _pipeline_child_jobs.get(_dir_pid)
                    if child_jobs is not None:
                        child_jobs.discard(job_id)
                        if not child_jobs:
                            _pipeline_child_jobs.pop(_dir_pid, None)

    # Run generation in a separate thread (it acquires _gen_lock internally).
    # The child lease outlives this waiter if cancellation cannot settle
    # promptly, keeping destructive Dashboard actions away from a live writer.
    # Non-daemon so the process stays alive if browser disconnects mid-generation.
    thread = threading.Thread(target=_run_tracked_generation, daemon=False)
    recovery_managed = bool(
        (pipeline_id or external_parent_id)
        and recovery_unit
        and callable(_recovery_submit_child)
    )
    try:
        if recovery_managed:
            attached = _recovery_submit_child(
                job, pipeline_id or external_parent_id,
                recovery_unit, recovery_attempt,
            )
            if not isinstance(attached, dict):
                raise RuntimeError("Director child recovery registration failed")
            job = attached
            job_id = str(job.get("id") or job_id)
            thread = None
            if _dir_pid:
                with _pipeline_lock:
                    _pipeline_child_jobs.setdefault(_dir_pid, set()).add(job_id)
        elif _dir_pid:
            # Publish, lease, recheck repair cancellation, and start under one
            # registry boundary. If cancel scanned before this child existed,
            # its operation-scoped event is observed here before generation;
            # if it scans after, the job is already visible to that scan.
            with _pipeline_lock:
                _jobs[job_id] = job
                _pipeline_child_jobs.setdefault(_dir_pid, set()).add(job_id)
                if _detached_operation and _repair_operation_id:
                    repair_control = _pipeline_repairs.get(_dir_pid)
                    if (
                        repair_control is not None
                        and repair_control.get("operation_id")
                            == _repair_operation_id
                        and repair_control["cancel_event"].is_set()
                    ):
                        request_cancel(job)
                        _skip_generation = True
                elif not _detached_operation:
                    pipeline_cancelled = (
                        _pipelines.get(_dir_pid, {}).get("status")
                        == "cancelled"
                    )
                    if pipeline_cancelled:
                        request_cancel(job)
                thread.start()
        else:
            _jobs[job_id] = job
            thread.start()
    except BaseException:
        if _dir_pid:
            with _pipeline_lock:
                child_jobs = _pipeline_child_jobs.get(_dir_pid)
                if child_jobs is not None:
                    child_jobs.discard(job_id)
                    if not child_jobs:
                        _pipeline_child_jobs.pop(_dir_pid, None)
        raise

    # Wait for completion, mirroring job progress to pipeline status
    deadline = time.time() + timeout_s
    _abort_signalled = False
    _resource_retry_seen = 0

    def _release_managed_child() -> None:
        if not recovery_managed or not _dir_pid:
            return
        with _pipeline_lock:
            child_jobs = _pipeline_child_jobs.get(_dir_pid)
            if child_jobs is not None:
                child_jobs.discard(job_id)
                if not child_jobs:
                    _pipeline_child_jobs.pop(_dir_pid, None)

    while True:
        j = _jobs.get(job_id)
        if not j:
            raise RuntimeError("Job disappeared")
        try:
            resource_retry_attempt = max(
                0, int(j.get("resource_retry_attempt", 0) or 0),
            )
        except (TypeError, ValueError):
            resource_retry_attempt = 0
        if resource_retry_attempt > _resource_retry_seen:
            _resource_retry_seen = resource_retry_attempt
            # The child remains the same durable unit/job. Grant its bounded
            # resource re-admission a fresh wait window without creating a
            # second Director child or resetting parent progress.
            deadline = max(deadline, time.time() + timeout_s)
        if j["status"] == "completed":
            outputs = _director_job_outputs(j)
            if recovery_managed:
                verified = _recovery_verify_child(j, out_dir)
                if not isinstance(verified, dict):
                    _release_managed_child()
                    if recovery_attempt >= 2:
                        raise RuntimeError(
                            "Director child completed without valid recovery evidence"
                        )
                    retry_entry = dict(recovery_entry or {})
                    retry_entry.update({
                        "attempt": recovery_attempt + 1,
                        "job_id": _director_child_job_id(
                            pipeline_id or external_parent_id,
                            recovery_unit, recovery_attempt + 1,
                        ),
                        "state": "invalid",
                    })
                    if pipeline_id:
                        _checkpoint_child_entry(
                            pipeline_id, recovery_unit, retry_entry,
                            boundary=f"{recovery_unit['kind']}-retry",
                        )
                    retry_params = dict(params)
                    if access_policy:
                        retry_params["_maestro_access_policy"] = dict(
                            access_policy
                        )
                    if owner_session_id:
                        retry_params["_maestro_session_id"] = owner_session_id
                    retry_params["_director_recovery_unit"] = recovery_unit
                    retry_params["_director_recovery_parent_id"] = (
                        external_parent_id
                    )
                    retry_params["_director_recovery_attempt"] = (
                        recovery_attempt + 1
                    )
                    retry_params["_director_recovery_job_id"] = retry_entry[
                        "job_id"
                    ]
                    return _submit_and_wait(
                        retry_params,
                        timeout_s=timeout_s,
                        workspace=workspace,
                        out_dir=out_dir,
                    )
                completed_outputs = verified.get("outputs")
                if not isinstance(completed_outputs, list) or not completed_outputs:
                    raise RuntimeError(
                        "Director child recovery evidence has no terminal output"
                    )
                completed_entry = dict(recovery_entry or {})
                completed_entry.update({
                    "evidence": verified,
                    "state": "completed",
                })
                if pipeline_id:
                    _checkpoint_child_entry(
                        pipeline_id, recovery_unit, completed_entry,
                        boundary=f"{recovery_unit['kind']}-completed",
                    )
                _release_managed_child()
                return _DirectorOutputs(
                    completed_outputs,
                    verified.get("clip_output_files") or {},
                )
            return outputs
        if j["status"] == "cancelled":
            # Keep whatever clips finished before the abort (multi-clip
            # jobs accrue output_files per clip) — callers tolerate a
            # partial or empty list and check the pipeline status.
            print(f"[Pipeline] Job {job_id} cancelled")
            # Cancellation is published immediately. Settle the child only in
            # this background pipeline thread so it can publish files that
            # completed before the abort took effect.
            if thread is not None:
                thread.join(timeout=_GENERATION_SETTLE_GRACE_S)
            if thread is not None and thread.is_alive():
                print(
                    f"[Pipeline] Job {job_id} is still shutting down; "
                    "pipeline remains busy"
                )
            settled = _jobs.get(job_id) or j
            settled_outputs = _director_job_outputs(settled)
            _release_managed_child()
            if _detached_operation:
                raise GenerationCancelledError(settled_outputs)
            return settled_outputs
        if j["status"] == "failed":
            err = j.get("error") or "Generation failed"
            print(f"[Pipeline] Job {job_id} failed: {err}")
            _release_managed_child()
            raise DirectorChildGenerationError(
                err,
                failure_details=j.get("failure_details"),
                oom_info=j.get("oom_info"),
            )
        # Reaching the old deadline is not sufficient to cancel: the retry
        # counter above is re-read in this same iteration first, so a durable
        # requeue committed at the edge receives its complete fresh window.
        if time.time() >= deadline:
            cancel_result = request_cancel(
                job,
                job_id=job_id,
                active_states=_active_gen_states or {},
                expected_resource_retry_attempt=_resource_retry_seen,
            )
            if not cancel_result.changed:
                refreshed = _jobs.get(job_id)
                if refreshed is not None:
                    try:
                        refreshed_retry_attempt = max(
                            0,
                            int(
                                refreshed.get(
                                    "resource_retry_attempt", 0,
                                ) or 0
                            ),
                        )
                    except (TypeError, ValueError):
                        refreshed_retry_attempt = _resource_retry_seen
                    if refreshed_retry_attempt > _resource_retry_seen:
                        _resource_retry_seen = refreshed_retry_attempt
                        deadline = time.time() + timeout_s
                        continue
                    if refreshed.get("status") in {
                        "completed", "failed", "cancelled",
                    }:
                        continue
            break
        # Backstop for stop_pipeline's abort: if the pipeline was cancelled
        # while this job runs (e.g. the job was submitted in the window
        # after the stop endpoint scanned _jobs), signal abort from here.
        if _dir_pid and not _detached_operation and not _abort_signalled:
            with _pipeline_lock:
                _cancelled = _pipelines.get(_dir_pid, {}).get("status") == "cancelled"
            if _cancelled:
                _abort_pipeline_jobs(_dir_pid)
                _abort_signalled = True
        # Mirror every child phase, including step-zero preparation and the
        # indeterminate resets between segments. Preserve current/total for
        # Director's pipeline-level clip counts.
        if _dir_pid and j.get("status") in {"queued", "running"}:
            with _pipeline_lock:
                p = _pipelines.get(_dir_pid)
                if p and "progress" in p:
                    p["progress"]["step"] = j.get("step", 0)
                    p["progress"]["total_steps"] = j.get("total_steps", 0)
                    p["progress"]["message"] = j.get("phase") or j.get("message") or "Generating..."
                    p["progress"]["indeterminate"] = bool(
                        j.get("status") == "running"
                        and j.get("progress_indeterminate", False)
                    )
                    p["progress"]["window_current"] = j.get("window_current", 0)
                    p["progress"]["window_total"] = j.get("window_total", 0)
                    p["progress"]["window_step"] = j.get("window_step", 0)
                    p["progress"]["window_total_steps"] = j.get("window_total_steps", 0)
                    p["progress"]["window_progress"] = j.get("window_progress", 0)
                    p["progress"]["overall_progress"] = j.get(
                        "overall_progress", j.get("progress", 0),
                    )
        time.sleep(min(1.0, max(0.01, deadline - time.time())))

    if thread is not None:
        thread.join(timeout=_GENERATION_SETTLE_GRACE_S)
    if thread is not None and thread.is_alive():
        print(
            f"[Pipeline] Timed-out job {job_id} is still shutting down; "
            "pipeline remains busy"
        )
    settled = _jobs.get(job_id) or job
    raise _GenerationTimeoutError(_director_job_outputs(settled))


def _director_llm_number(value, *, minimum: float = 0.0):
    """Return one finite non-negative telemetry number or ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        return None
    return number


def _begin_pipeline_llm_pass(
    pid: str,
    *,
    phase: str,
    pass_name: str,
    attempt_limit: int,
):
    """Publish one pipeline-bound, process-memory-only LLM progress stream."""
    token = object()
    with _pipeline_lock:
        pipeline = _pipelines.get(pid)
        if (
            not pipeline
            or pipeline.get("status") in _DIRECTOR_TERMINAL_STATUSES
        ):
            raise _DirectorLlmCancelled("Director LLM pass is no longer active")
        _pipeline_llm_tokens[pid] = token
        pipeline["llm_progress"] = {
            "phase": str(phase or "planning")[:64],
            "pass": str(pass_name or "llm")[:96],
            "activity": "starting",
            "partial_text": "",
            "attempt": 1,
            "attempt_limit": max(1, min(2, int(attempt_limit))),
            "generated_tokens_approx": 0,
            "elapsed_seconds": 0.0,
            "live_tps": None,
            "average_tps": None,
            "done": False,
        }

    def publish(event: dict) -> None:
        if not isinstance(event, dict):
            return
        with _pipeline_lock:
            pipeline = _pipelines.get(pid)
            if (
                not pipeline
                or _pipeline_llm_tokens.get(pid) is not token
                or pipeline.get("status") in _DIRECTOR_TERMINAL_STATUSES
            ):
                return
            current = pipeline.get("llm_progress")
            if not isinstance(current, dict):
                return
            raw_attempt = event.get("attempt")
            attempt = (
                raw_attempt
                if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool)
                else current.get("attempt", 1)
            )
            attempt = max(1, min(current["attempt_limit"], int(attempt)))
            activity = str(event.get("phase") or "generating")[:32]
            new_attempt = attempt > int(current.get("attempt") or 1)
            retrying = activity == "retrying"
            text = event.get("text")
            partial = text if isinstance(text, str) else ""
            # The runtime emits cumulative visible text.  Retain only a
            # bounded tail and immediately discard a rejected attempt.
            if new_attempt or retrying or event.get("done") is True:
                partial = ""
            elif len(partial) > _DIRECTOR_LLM_PARTIAL_LIMIT:
                partial = partial[-_DIRECTOR_LLM_PARTIAL_LIMIT:]
            generated = event.get("generated_tokens_approx")
            generated = (
                generated
                if isinstance(generated, int)
                and not isinstance(generated, bool)
                and generated >= 0
                else 0
            )
            elapsed = _director_llm_number(event.get("elapsed_seconds"))
            live_tps = _director_llm_number(event.get("live_tps"))
            average_tps = _director_llm_number(event.get("average_tps"))
            current.update({
                "activity": activity,
                "partial_text": partial,
                "attempt": attempt,
                "generated_tokens_approx": generated,
                "elapsed_seconds": elapsed if elapsed is not None else 0.0,
                "live_tps": live_tps,
            })
            if average_tps is not None:
                current["average_tps"] = average_tps

    return token, publish


def _finish_pipeline_llm_pass(
    pid: str,
    token: object,
    *,
    failed: bool = False,
) -> None:
    """Finalize metrics without allowing a late pass to overwrite Stop."""
    with _pipeline_lock:
        if _pipeline_llm_tokens.get(pid) is not token:
            return
        _pipeline_llm_tokens.pop(pid, None)
        pipeline = _pipelines.get(pid)
        if (
            not pipeline
            or pipeline.get("status") in _DIRECTOR_TERMINAL_STATUSES
        ):
            return
        progress = pipeline.get("llm_progress")
        if not isinstance(progress, dict):
            return
        progress.update({
            "activity": "failed" if failed else "complete",
            "partial_text": "",
            "live_tps": None,
            "done": True,
        })


def _pipeline_llm_pass_active(pid: str, token: object) -> bool:
    """Return whether a composite pass may start another model request."""
    with _pipeline_lock:
        pipeline = _pipelines.get(pid)
        return bool(
            pipeline
            and _pipeline_llm_tokens.get(pid) is token
            and pipeline.get("status") not in _DIRECTOR_TERMINAL_STATUSES
        )


def _pipeline_llm_call(
    pid: str,
    phase: str,
    pass_name: str,
    function,
    /,
    *args,
    allow_response_assist: bool = True,
    liveness_kwarg: Optional[str] = None,
    **kwargs,
):
    """Run one inference call with pipeline-scoped progress and finality."""
    with _pipeline_lock:
        pipeline_present = pid in _pipelines
        context = dict(_pipeline_llm_contexts.get(pid) or {})
    # Keep direct unit/legacy helper calls that do not own a live pipeline
    # source-compatible. Production Director calls always have a pipeline.
    if not pipeline_present:
        return function(*args, **kwargs)
    response_assist = (
        context.get("response_assist")
        if allow_response_assist and kwargs.get("json_schema") is None
        else None
    )
    attempt_limit = 2 if (
        isinstance(response_assist, dict)
        and response_assist.get("retry_on_refusal") is True
    ) else 1
    token, callback = _begin_pipeline_llm_pass(
        pid,
        phase=phase,
        pass_name=pass_name,
        attempt_limit=attempt_limit,
    )
    kwargs["progress_callback"] = callback
    if liveness_kwarg:
        kwargs[liveness_kwarg] = lambda: _pipeline_llm_pass_active(pid, token)
    if response_assist:
        kwargs["response_assist"] = response_assist
    failed = True
    try:
        selection = context.get("selection")
        if isinstance(selection, dict) and selection:
            from services import llm_service
            with llm_service.loaded_model_lease(**selection):
                result = function(*args, **kwargs)
        else:
            result = function(*args, **kwargs)
        with _pipeline_lock:
            pipeline = _pipelines.get(pid)
            cancelled = (
                not pipeline or pipeline.get("status") == "cancelled"
            )
        if cancelled:
            raise _DirectorLlmCancelled("Director LLM pass was cancelled")
        failed = False
        return result
    finally:
        _finish_pipeline_llm_pass(pid, token, failed=failed)


def _cancel_pipeline_llm_progress(pid: str) -> None:
    """Make the active callback inert at the same lock boundary as Stop."""
    _pipeline_llm_tokens.pop(pid, None)
    progress = (_pipelines.get(pid) or {}).get("llm_progress")
    if isinstance(progress, dict):
        progress.update({
            "activity": "cancelled",
            "partial_text": "",
            "live_tps": None,
            "done": True,
        })


def _update_pipeline(pid: str, **kwargs):
    """Thread-safe update; cancellation is an absorbing terminal state."""
    with _pipeline_lock:
        pipeline = _pipelines.get(pid)
        if not pipeline:
            return False
        if pipeline.get("status") == "cancelled":
            # Finished clips may still be reported after an in-flight abort,
            # but no later phase, completion, or failure may replace Stop.
            if set(kwargs) - _CANCELLED_ARTIFACT_FIELDS:
                return False
        pipeline.update(kwargs)
        return True


def _start_pipeline_worker(pid: str, *, resume: bool = False) -> None:
    """Start and track a Director worker until its ``finally`` completes."""
    thread = threading.Thread(
        target=_run_pipeline,
        args=(pid,),
        kwargs={"resume": resume},
        daemon=False,
    )
    with _pipeline_lock:
        if pid in _pipeline_threads:
            raise RuntimeError(f"Pipeline {pid} already has a worker")
        if _pipeline_child_jobs.get(pid):
            raise RuntimeError(
                f"Pipeline {pid} still has a generation child"
            )
        _pipeline_threads[pid] = thread
    try:
        thread.start()
    except BaseException as exc:
        with _pipeline_lock:
            if _pipeline_threads.get(pid) is thread:
                _pipeline_threads.pop(pid, None)
            pipeline = _pipelines.get(pid)
            if pipeline and pipeline.get("status") not in {
                "completed", "failed", "cancelled",
            }:
                failure_details = _director_failure_details(
                    exc, code=_DIRECTOR_WORKER_FAILED_CODE,
                )
                pipeline["status"] = "failed"
                pipeline["phase"] = "failed"
                pipeline["error"] = _DIRECTOR_WORKER_FAILED_MESSAGE
                pipeline["error_code"] = str(
                    failure_details.get("code") or _DIRECTOR_WORKER_FAILED_CODE
                )
                pipeline["failure_details"] = failure_details
                pipeline["_completed_at"] = time.time()
                pipeline["progress"] = {
                    "current": 0,
                    "total": 0,
                    "message": _DIRECTOR_WORKER_FAILED_MESSAGE,
                    "step": 0,
                    "total_steps": 0,
                }
        _save_pipeline_state(pid)
        raise


def start_pipeline(params: dict) -> str:
    """Start a new director pipeline. Returns pipeline_id."""
    # Internal resume metadata must never be accepted from a fresh API request.
    # Otherwise a caller could nominate unrelated workspace media as this
    # pipeline's generated anchor and later influence repair/cleanup behavior.
    params.pop("generated_reference_image_filename", None)
    params.pop("_director_shot_image_policy", None)
    from services.director.nsfw_guidance import EXPLICIT_GUIDANCE_SNAPSHOT_KEY
    # A caller may not nominate this private decision bit. Recompute it from
    # the literal request flag and authoritative consent/provider policy, then
    # persist the result in the initial Director state snapshot.
    params.pop(EXPLICIT_GUIDANCE_SNAPSHOT_KEY, None)
    explicit_guidance = _fresh_explicit_guidance_decision(params)
    params[EXPLICIT_GUIDANCE_SNAPSHOT_KEY] = explicit_guidance
    if explicit_guidance:
        # Bind the authorized request to the same non-public provider that
        # passed the gate. This prevents a client provider override—and a
        # later settings change during recovery—from sending explicit guidance
        # to a public provider.
        services = getattr(_wgp, "server_config", {}).get("services", {})
        params["llm_provider"] = str(
            services.get("llm_provider") or "local"
        ).strip().lower()
    params["_director_shot_image_policy"] = _resolve_fresh_shot_image_policy(params)
    if callable(_runtime_admission):
        _runtime_admission(params, source_remote=False)
    _validate_director_models(params)
    pid = uuid.uuid4().hex[:8]

    # Capture workspace at submission time — not at execution time
    workspace = params.pop("workspace", None)
    # Capture this before spawning the Director worker. Pinokio starts
    # ``python launch.py``, so the live ContextVar belongs to ``__main__``;
    # importing ``launch`` here would create a second module whose default is
    # always local. Module-import test/dev servers instead expose ``launch``.
    source_remote = False
    live_launch_module = None
    for module_name in ("__main__", "launch"):
        module = sys.modules.get(module_name)
        request_remote = getattr(module, "_request_remote", None)
        if request_remote is None:
            continue
        live_launch_module = module
        try:
            source_remote = bool(request_remote.get())
        except (AttributeError, LookupError):
            source_remote = False
        break

    if workspace:
        # Resolve the output directory now, while we know the intended workspace
        workspace_dir = getattr(live_launch_module, "_workspace_dir", None)
        if not callable(workspace_dir):
            raise RuntimeError("Director workspace resolver is unavailable")
        out_dir = workspace_dir(workspace)
        print(f"[Pipeline] Workspace={workspace}, out_dir={out_dir}, wgp.save_path={_wgp.save_path}")
    else:
        out_dir = _wgp.save_path
        workspace = None
        print(f"[Pipeline] No workspace, using wgp.save_path={out_dir}")

    pipeline = {
        "id": pid,
        "status": "queued",
        "phase": "registered",
        "auto_mode": params.get("auto_mode", True),
        "progress": {"current": 0, "total": 0, "message": "Starting...", "step": 0, "total_steps": 0},
        "clip_plans": [],
        "clip_images": [],         # filenames of generated start images
        "output_files": [],
        "error": None,
        "created_at": time.time(),
        "params": params,
        "pause_reason": None,
        "workspace": workspace,
        "out_dir": out_dir,
        "source_remote": source_remote,
        "llm_progress": None,
    }

    # The parent JSON and queue identity must both be durable before the
    # pipeline is published, its worker starts, or the route can acknowledge
    # the id.  The callback writes no prompt/path material to the journal.
    initial_state, state_descriptor = _write_initial_pipeline_state(
        pid, pipeline,
    )
    if callable(_recovery_register_parent):
        try:
            recovery_parent = _recovery_register_parent(
                pid, pipeline, initial_state, state_descriptor,
            )
            if not isinstance(recovery_parent, dict):
                raise RuntimeError(
                    "Director recovery parent registration failed"
                )
        except BaseException:
            _remove_pipeline_state_file(out_dir, pid)
            raise
        pipeline["_recovery_parent"] = recovery_parent
        pipeline["_recovery"] = {
            "parent_job_id": recovery_parent.get("id"),
            "children": {},
            "inputs": list(recovery_parent.get("inputs") or []),
        }

    with _pipeline_lock:
        _pipelines[pid] = pipeline

    # Non-daemon so pipeline survives browser disconnect during overnight runs.
    _start_pipeline_worker(pid)

    return pid


def get_pipeline(pid: str) -> Optional[dict]:
    with _pipeline_lock:
        p = _pipelines.get(pid)
        return dict(p) if p else None


def continue_pipeline(pid: str, updates: Optional[dict] = None):
    """Resume a paused pipeline, optionally with updated clip_plans."""
    start_recovered = False
    rollback = None
    with _pipeline_lock:
        p = _pipelines.get(pid)
        if not p or p["status"] != "paused":
            return False
        if callable(_runtime_admission):
            _runtime_admission(
                p.get("params") or {},
                source_remote=bool(p.get("source_remote", False)),
            )
        rollback = {
            "clip_plans": p.get("clip_plans"),
            "status": p.get("status"),
            "pause_reason": p.get("pause_reason"),
            "resume_present": "_resume_after_pause" in p,
            "resume_after_pause": p.get("_resume_after_pause"),
            "recovered_present": "_recovered_without_worker" in p,
            "recovered_without_worker": p.get("_recovered_without_worker"),
        }
        if updates:
            if "clip_plans" in updates:
                p["clip_plans"] = updates["clip_plans"]
        p["status"] = "running"
        p["_resume_after_pause"] = p.get("pause_reason")
        p["pause_reason"] = None
        start_recovered = bool(p.pop("_recovered_without_worker", False))
    if start_recovered:
        try:
            _require_pipeline_checkpoint(pid, "review-continue")
        except BaseException:
            with _pipeline_lock:
                current = _pipelines.get(pid)
                if current is p:
                    current["clip_plans"] = rollback["clip_plans"]
                    current["status"] = rollback["status"]
                    current["pause_reason"] = rollback["pause_reason"]
                    if rollback["resume_present"]:
                        current["_resume_after_pause"] = rollback[
                            "resume_after_pause"
                        ]
                    else:
                        current.pop("_resume_after_pause", None)
                    if rollback["recovered_present"]:
                        current["_recovered_without_worker"] = rollback[
                            "recovered_without_worker"
                        ]
                    else:
                        current.pop("_recovered_without_worker", None)
            raise
        _start_pipeline_worker(pid, resume=True)
    return True


def _find_pipeline_state_file(pid: str, out_dir: str) -> Optional[str]:
    """Locate a saved pipeline JSON by id under out_dir or a workspace subdir."""
    try:
        fname = pipeline_state_filename(pid)
    except ValueError:
        return None
    candidates = [os.path.join(out_dir, fname)]
    try:
        for name in os.listdir(out_dir):
            sub = os.path.join(out_dir, name)
            if os.path.isdir(sub):
                candidates.append(os.path.join(sub, fname))
    except OSError:
        pass
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def resume_pipeline(
    pid: str,
    out_dir: str,
    *,
    verified_state: Optional[dict] = None,
) -> tuple[bool, str]:
    """Rehydrate a crashed pipeline from disk and re-run it.

    Reuses committed planning plus image/video child results whose exact
    recovery evidence still verifies. Invalid or absent children are attached
    or resubmitted by their stable unit keys. Returns (ok, message). Requires
    a state file carrying the full params snapshot — older crash files cannot
    be resumed faithfully and report so.
    """
    with _pipeline_lock:
        existing = _pipelines.get(pid)
        if (
            pid in _pipeline_threads
            or bool(_pipeline_child_jobs.get(pid))
            or pid in _pipeline_starting
            or pid in _pipeline_operations
            or pid in _pipeline_deleting
            or (
                existing
                and existing.get("status") in (
                    "running", "queued", "planning",
                )
            )
        ):
            return False, "Pipeline is already running."
        existing_status = str((existing or {}).get("status") or "")
        if existing_status in {"completed", "failed", "cancelled"}:
            return False, "Terminal pipelines cannot be resumed; use repair."
        if existing_status == "paused":
            return False, "Paused pipelines must be continued explicitly."
        if (
            existing_status == "blocked"
            and (existing or {}).get("recovery_state")
                != "blocked_remote_reauth"
        ):
            return False, (
                "Recovery is blocked because its saved request or inputs "
                "could not be validated."
            )
        _pipeline_starting.add(pid)
    try:
        if verified_state is None:
            return _resume_pipeline_reserved(pid, out_dir)
        return _resume_pipeline_reserved(
            pid, out_dir, verified_state=verified_state,
        )
    finally:
        with _pipeline_lock:
            _pipeline_starting.discard(pid)


def _resume_pipeline_reserved(
    pid: str,
    out_dir: str,
    *,
    verified_state: Optional[dict] = None,
) -> tuple[bool, str]:
    """Resume implementation after ``pid`` has been atomically reserved."""
    if isinstance(verified_state, dict):
        data = dict(verified_state)
        state_path = os.path.join(out_dir, pipeline_state_filename(pid))
    else:
        state_path = _find_pipeline_state_file(pid, out_dir)
        if not state_path:
            return False, "No saved state found for this pipeline."
        try:
            with _pipeline_file_lock:
                with open(state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception as e:
            return False, f"Could not read saved pipeline state: {e}"
    if data.get("pipeline_id") != pid:
        return False, "Saved pipeline identity is invalid."

    saved_status = str(data.get("status") or "").casefold()
    if saved_status in {"completed", "failed", "cancelled", "canceled"}:
        return False, "Terminal pipelines cannot be resumed; use repair."
    if saved_status == "paused":
        return False, "Paused pipelines must be continued explicitly."
    if not isinstance(data.get("_params_snapshot"), dict):
        return False, (
            "This pipeline was created before resume support and can't be "
            "resumed — start a new generation."
        )
    params = _director_params_from_saved_state(data)
    if callable(_runtime_admission):
        try:
            _runtime_admission(
                params,
                source_remote=bool(data.get("source_remote", False)),
            )
        except Exception:
            return False, "Saved Director models or LoRAs are not ready."
    try:
        _validate_director_models(params, stages=("video",))
    except DirectorModelCompatibilityError as exc:
        return False, str(exc)

    # Rebuild the generation-driving structures from the saved per-clip state.
    saved_clips = data.get("clips", []) or []
    clip_plans = [{
        "image_prompt": c.get("image_prompt", ""),
        "video_prompt": c.get("video_prompt", ""),
        "visual_changes": c.get("visual_changes", []) or [],
        "image_source": c.get("image_source", "original"),
        "keyframe_prompts": c.get("keyframe_prompts", []) or [],
        "window_prompts": c.get("window_prompts", []) or [],
        "window_count": c.get("window_count", 1),
        "_h3_shot": c.get("_h3_shot"),
    } for c in saved_clips]
    planned_clips = [c.get("planned_clip") for c in saved_clips]
    clip_images = [c.get("start_image_filename") for c in saved_clips]
    clip_keyframes = [c.get("keyframe_filenames", []) or [] for c in saved_clips]

    workspace = data.get("workspace") if data.get("workspace") not in ("default", None) else None
    resume_out_dir = os.path.dirname(state_path)

    pipeline = {
        "id": pid,
        "status": "running",
        "phase": "resuming",
        "auto_mode": params.get("auto_mode", True),
        "progress": {"current": 0, "total": 0, "message": "Resuming…", "step": 0, "total_steps": 0},
        "clip_plans": clip_plans,
        "_planned_clips": planned_clips,
        "clip_images": clip_images,
        "_clip_keyframes": clip_keyframes,
        "_clip_video_files": [
            c.get("video_filename") for c in saved_clips
        ],
        "output_files": data.get("output_files", []) or [],
        "error": None,
        "created_at": data.get("created_at") or time.time(),
        "params": params,
        "pause_reason": None,
        "workspace": workspace,
        "out_dir": resume_out_dir,
        "source_remote": bool(data.get("source_remote", False)),
        # Interrupted inference is never resumable state. A resumed pipeline
        # starts with no partial text or stale meters.
        "llm_progress": None,
    }
    with _pipeline_lock:
        previous = _pipelines.get(pid) or {}
        recovery_parent = previous.get("_recovery_parent")
        recovery_state = previous.get("_recovery")
    if isinstance(recovery_parent, dict):
        pipeline["_recovery_parent"] = dict(recovery_parent)
    if isinstance(recovery_state, dict):
        pipeline["_recovery"] = dict(recovery_state)
    elif isinstance(data.get("recovery"), dict):
        pipeline["_recovery"] = dict(data["recovery"])
    pipeline["recovery_state"] = "retrying"
    pipeline["recovery_actions"] = []
    with _pipeline_lock:
        _pipelines[pid] = pipeline

    _start_pipeline_worker(pid, resume=True)
    return True, "resumed"


def restore_registered_pipeline(
    data: dict,
    state_path: str,
    recovery_parent: dict,
    *,
    blocked_remote: bool = False,
    blocked_reason: str = "",
    defer_worker: bool = False,
) -> dict:
    """Reconstruct one journal-owned Director parent before workers start."""
    pid = str(data.get("pipeline_id") or "")
    if not pid or pipeline_state_filename(pid) != os.path.basename(state_path):
        raise ValueError("Director recovery state identity is invalid")
    raw_params = data.get("_params_snapshot")
    if not isinstance(raw_params, dict):
        raise ValueError("Director recovery request is unavailable")
    params = _normalize_explicit_guidance_snapshot(dict(raw_params))
    saved_status = str(data.get("status") or "queued")
    terminal = saved_status in {"completed", "failed", "cancelled"}
    paused = saved_status == "paused"
    blocked_manual = bool(blocked_reason) and not terminal
    can_start_worker = not (
        terminal or paused or blocked_remote or blocked_manual
    )
    if can_start_worker and callable(_runtime_admission):
        _runtime_admission(
            params,
            source_remote=bool(data.get("source_remote", False)),
        )
    saved_clips = data.get("clips", []) or []
    clip_plans = [{
        "image_prompt": clip.get("image_prompt", ""),
        "video_prompt": clip.get("video_prompt", ""),
        "visual_changes": clip.get("visual_changes", []) or [],
        "image_source": clip.get("image_source", "original"),
        "keyframe_prompts": clip.get("keyframe_prompts", []) or [],
        "window_prompts": clip.get("window_prompts", []) or [],
        "window_count": clip.get("window_count", 1),
        "_h3_shot": clip.get("_h3_shot"),
    } for clip in saved_clips]
    runtime_status = (
        saved_status if terminal
        else "blocked" if blocked_remote or blocked_manual
        else saved_status if paused
        else "running"
    )
    restored_recovery = dict(data.get("recovery") or {})
    restored_recovery.setdefault(
        "inputs", list(recovery_parent.get("inputs") or []),
    )
    restored_error_code = str(data.get("error_code") or "")
    restored_error_messages = {
        _DIRECTOR_PIPELINE_FAILED_CODE: _DIRECTOR_PIPELINE_FAILED_MESSAGE,
        _DIRECTOR_WORKER_FAILED_CODE: _DIRECTOR_WORKER_FAILED_MESSAGE,
        "cuda_oom": "Director generation stopped after a GPU memory error.",
    }
    if saved_status == "failed" and restored_error_code not in restored_error_messages:
        restored_error_code = _DIRECTOR_PIPELINE_FAILED_CODE
    restored_error = (
        restored_error_messages.get(restored_error_code)
        if saved_status == "failed" else None
    )
    restored_failure_details = None
    if saved_status == "failed":
        raw_failure_details = data.get("failure_details")
        if (
            isinstance(raw_failure_details, dict)
            and raw_failure_details.get("code") == restored_error_code
        ):
            try:
                from services.oom_detect import normalize_failure_details
                restored_failure_details = normalize_failure_details(
                    raw_failure_details,
                )
            except Exception:
                restored_failure_details = None
        if restored_failure_details is None:
            restored_failure_details = _director_failure_details(
                RuntimeError("restored Director failure"),
                code=restored_error_code or _DIRECTOR_PIPELINE_FAILED_CODE,
            )
    restored_oom_info = None
    if isinstance(restored_failure_details, dict):
        try:
            from services.oom_detect import oom_info_from_failure_details
            coefficient = float(
                getattr(_wgp, "server_config", {}).get(
                    "vram_safety_coefficient", 0.80,
                )
            )
            restored_oom_info = oom_info_from_failure_details(
                restored_failure_details, coefficient,
            )
        except Exception:
            restored_oom_info = None
    pipeline = {
        "id": pid,
        "status": runtime_status,
        "phase": (
            "blocked_input_changed" if blocked_manual
            else "blocked_remote_reauth" if blocked_remote
            else data.get("phase") or saved_status
        ),
        "auto_mode": params.get("auto_mode", True),
        "progress": {
            "current": 0, "total": 0,
            "message": (
                blocked_reason
                if blocked_manual
                else "Owner reauthentication is required to resume"
                if blocked_remote else "Recovered after restart"
            ),
            "step": 0, "total_steps": 0,
        },
        "clip_plans": clip_plans,
        "_planned_clips": [
            clip.get("planned_clip") for clip in saved_clips
        ],
        "clip_images": [
            clip.get("start_image_filename") for clip in saved_clips
        ],
        "_clip_keyframes": [
            clip.get("keyframe_filenames", []) or [] for clip in saved_clips
        ],
        "_clip_video_files": [
            clip.get("video_filename") for clip in saved_clips
        ],
        "output_files": data.get("output_files", []) or [],
        "error": restored_error,
        "error_code": restored_error_code or None,
        "failure_details": restored_failure_details,
        "oom_info": restored_oom_info,
        "created_at": data.get("created_at") or time.time(),
        "params": params,
        "pause_reason": data.get("pause_reason"),
        "workspace": (
            None if data.get("workspace") in (None, "default")
            else data.get("workspace")
        ),
        "out_dir": os.path.dirname(state_path),
        "source_remote": bool(data.get("source_remote", False)),
        "llm_progress": None,
        "_recovery_parent": dict(recovery_parent),
        "_recovery_owner_digest": recovery_parent.get("owner_digest"),
        "_recovery_project_digest": recovery_parent.get("project_digest"),
        "_recovery": restored_recovery,
        "_recovered_without_worker": bool(
            paused or blocked_remote or blocked_manual or defer_worker
        ),
        "_recovery_block_reason": blocked_reason or None,
        "_recovery_saved_phase": data.get("phase") or saved_status,
        "recovery_state": (
            "blocked_input_changed" if blocked_manual
            else "blocked_remote_reauth" if blocked_remote
            else "paused" if paused
            else "terminal" if terminal
            else "interrupted"
        ),
        "recovery_actions": (
            ["resume"]
            if blocked_remote and not blocked_manual
            else ["continue"] if paused and not blocked_manual else []
        ),
    }
    with _pipeline_lock:
        if pid in _pipelines:
            return dict(_pipelines[pid])
        _pipelines[pid] = pipeline
    if (
        not terminal
        and not paused
        and not blocked_remote
        and not blocked_manual
        and not defer_worker
    ):
        _start_pipeline_worker(pid, resume=True)
    return dict(pipeline)


def block_pipeline_recovery(pid: str, reason: str) -> Optional[dict]:
    """Make a route-time validation failure authoritative before any action."""
    with _pipeline_lock:
        pipeline = _pipelines.get(pid)
        if not pipeline:
            return None
        if pipeline.get("status") in {"completed", "failed", "cancelled"}:
            return dict(pipeline)
        pipeline.update({
            "status": "blocked",
            "phase": "blocked_input_changed",
            "recovery_state": "blocked_input_changed",
            "recovery_actions": [],
            "_recovery_block_reason": reason,
            "_recovered_without_worker": True,
            "progress": {
                "current": 0,
                "total": 0,
                "message": reason,
                "step": 0,
                "total_steps": 0,
            },
        })
        return dict(pipeline)


def reauthorize_paused_pipeline(pid: str) -> bool:
    """Turn an exactly reauthenticated remote review pause back into Pause."""
    rollback = None
    with _pipeline_lock:
        pipeline = _pipelines.get(pid)
        if (
            not pipeline
            or pipeline.get("status") != "blocked"
            or pipeline.get("recovery_state") != "blocked_remote_reauth"
            or not pipeline.get("pause_reason")
        ):
            return False
        rollback = {
            key: pipeline.get(key)
            for key in (
                "status", "phase", "recovery_state", "recovery_actions",
                "_recovered_without_worker", "progress",
            )
        }
        pipeline.update({
            "status": "paused",
            "phase": pipeline.get("_recovery_saved_phase") or "paused",
            "recovery_state": "paused",
            "recovery_actions": ["continue"],
            "_recovered_without_worker": True,
            "progress": {
                "current": 0,
                "total": 0,
                "message": "Review and continue the recovered pipeline",
                "step": 0,
                "total_steps": 0,
            },
        })
    try:
        _require_pipeline_checkpoint(pid, "remote-review-reauthorized")
    except BaseException:
        with _pipeline_lock:
            current = _pipelines.get(pid)
            if current is pipeline:
                current.update(rollback)
        raise
    return True


def start_restored_pipeline(pid: str) -> bool:
    """Start one validated parent only after startup cleanup has completed."""
    with _pipeline_lock:
        pipeline = _pipelines.get(pid)
        if (
            not pipeline
            or pipeline.get("status") != "running"
            or not pipeline.pop("_recovered_without_worker", False)
        ):
            return False
        params = pipeline.get("params")
        source_remote = bool(pipeline.get("source_remote", False))
    if callable(_runtime_admission):
        try:
            _runtime_admission(params, source_remote=source_remote)
        except Exception:
            block_pipeline_recovery(
                pid,
                "Recovery models or LoRAs are not ready.",
            )
            return False
    _start_pipeline_worker(pid, resume=True)
    return True


def _abort_pipeline_jobs(pid: str):
    """Signal wgp abort for this pipeline's queued/running generation jobs.

    Mirrors the Studio cancel endpoint (launch.cancel_job): flip the job's
    gen-state abort flag and the model's _interrupt so the denoise loop
    stops within a step. Without this, Stop only takes effect at the next
    phase/clip boundary — the in-flight clip runs to completion, 10+
    minutes of GPU work after the user pressed Stop on slower cards.
    """
    if not _jobs:
        return
    for job_id, job in list(_jobs.items()):
        params = job.get("params") or {}
        if params.get("_director_pipeline_id") != pid:
            continue
        result = request_cancel(
            job,
            job_id=job_id,
            active_states=_active_gen_states or {},
        )
        if result.abort_signalled:
            print(f"[Pipeline {pid}] Abort signalled for in-flight job {job_id}")


def stop_pipeline(pid: str) -> bool:
    with _pipeline_lock:
        p = _pipelines.get(pid)
        if not p or p.get("status") in ("completed", "failed", "cancelled"):
            return False
        p["status"] = "cancelled"
        p["phase"] = "cancelled"
        p["pause_reason"] = None
        p["_completed_at"] = time.time()
        p["progress"] = {
            "current": 0,
            "total": 0,
            "message": "Cancelled",
            "step": 0,
            "total_steps": 0,
        }
        _cancel_pipeline_llm_progress(pid)
        _pipeline_llm_contexts.pop(pid, None)
    _abort_pipeline_jobs(pid)
    persisted = _save_pipeline_state(pid)
    with _pipeline_lock:
        current = _pipelines.get(pid)
        if current is not None:
            current["_state_persisted"] = persisted
    return True


def _run_pipeline(pid: str, resume: bool = False):
    """Main pipeline thread — runs the full Director flow.

    When resume=True the pipeline was rehydrated from a crashed state
    (see resume_pipeline): committed planning/polish is retained, while image
    and video slots are reused only when their sealed child evidence verifies.
    Missing units submit-or-attach by stable keys, and verified final rejoins
    are adopted without creating timestamped duplicates.
    """
    try:
        with _pipeline_lock:
            p = _pipelines.get(pid)
            if not p or p.get("status") == "cancelled":
                return
        _update_pipeline(pid, status="running", phase="planning")
        if p.get("_recovery_parent"):
            _require_pipeline_checkpoint(pid, "worker-start")
        params = p["params"]
        pipeline_out_dir = p.get("out_dir") or _wgp.save_path
        pipeline_workspace = p.get("workspace")
        shot_image_policy = _director_effective_shot_image_policy(params)
        requires_shot_images = shot_images_required(shot_image_policy)

        # Work already completed before a crash (empty on a fresh run).
        resume_plans = (p.get("clip_plans") or None) if resume else None
        resume_images = (p.get("clip_images") or None) if resume else None
        resume_after_pause = str(p.get("_resume_after_pause") or "")

        pipeline_type = params.get("pipeline_type", "music_video")  # music_video | short_film_audio | short_film_story
        auto_mode = params.get("auto_mode", True)

        # ── Disk preflight ─────────────────────────────────────────────
        # A Director run writes gigabytes (per-clip images + video + the
        # final concat). Fail fast with a clear message instead of dying
        # halfway through with a truncated "No space left on device" write.
        try:
            import shutil as _shutil
            free_gb = _shutil.disk_usage(pipeline_out_dir).free / (1024 ** 3)
            if free_gb < 3:
                raise RuntimeError(
                    f"Only {free_gb:.1f} GB free on the output drive — not "
                    f"enough for a Director run. Free up space and try again."
                )
        except RuntimeError:
            raise
        except Exception:
            pass  # disk_usage can fail on odd mounts; don't block on the check itself

        # ── Wait for GPU if jobs are running ────────────────────────────
        # LLM needs GPU (CUDA), so we must wait for generation queue to drain.
        # In auto mode this is expected (fire-and-forget). In non-auto mode
        # the user is waiting interactively, so we still wait but they can cancel.
        if not _wait_for_gpu(pid):
            return  # cancelled while waiting

        # ── Phase 1: LLM Planning ──────────────────────────────────────
        _update_pipeline(
            pid,
            phase="planning",
            progress={
                "current": 0, "total": 1,
                "message": "Planning with LLM...",
                "step": 0, "total_steps": 0,
            },
        )

        planning_start = time.time()
        if resume_plans:
            # Reuse the planning that already succeeded before the crash.
            clip_plans = resume_plans
            planned_clips = p.get("_planned_clips") or []
            print(f"[Pipeline {pid}] Resume: reusing {len(clip_plans)} planned clips — skipping LLM planning + polish")
        else:
            try:
                clip_plans, planned_clips = _run_planning(pid, params, pipeline_type)
            except _DirectorLlmCancelled:
                return
            except Exception:
                print("[Pipeline] Planning failed")
                raise
        planning_time = time.time() - planning_start

        if _pipelines.get(pid, {}).get("status") == "cancelled":
            return

        if not clip_plans:
            raise RuntimeError("Planning produced no clip plans")

        # Store planned clips for persistence
        _update_pipeline(pid, _planned_clips=planned_clips)

        # Only content-free terminal timing is retained in memory. Raw system,
        # user, thinking, and response text are neither captured nor durable.
        _update_pipeline(pid, llm_planning_time_sec=round(planning_time, 2))

        # ── Optional: Third-pass prompt polish ────────────────────────
        services = _wgp.server_config.get("services", {}) if _wgp else {}
        # Default "third_pass" — Pass 3 polish runs each generated prompt
        # through a model-specific dialect pass after planning, which
        # produces materially better output than relying on Pass 2 alone
        # with a single hardcoded dialect.
        polish_mode = services.get("director_prompt_polish", "third_pass")

        # Snapshot pre-polish prompts for comparison
        import copy
        _update_pipeline(pid, _clip_plans_pre_polish=copy.deepcopy(clip_plans))

        # On resume the saved clip_plans are ALREADY polished — re-polishing
        # would compound edits and drift the prompts, so skip the whole block.
        if resume_plans:
            pass
        elif polish_mode == "third_pass" and clip_plans:
            try:
                from services.director.prompt_polish import (
                    polish_prompts_third_pass,
                    should_polish_director_video_prompts,
                )
                nsfw = _explicit_guidance_from_snapshot(params)
                video_model = params.get("video_model", "")
                image_model = _director_image_role_model(params, "editor")
                polish_video_prompts = should_polish_director_video_prompts(
                    video_model,
                )
                polish_image_prompts = bool(requires_shot_images)
                if polish_video_prompts or polish_image_prompts:
                    polish_label = (
                        "Polishing generated image prompts..."
                        if not polish_video_prompts
                        else "Polishing prompts (3rd pass)..."
                    )
                    _update_pipeline(
                        pid,
                        phase="polishing_prompts",
                        progress={
                            "current": 0,
                            "total": len(clip_plans),
                            "message": polish_label,
                            "step": 0,
                            "total_steps": 0,
                        },
                    )
                else:
                    _update_pipeline(
                        pid,
                        _polish_mode_used="h3_native_preflight",
                    )
                video_loras = (params.get("video_loras") or {}).get("activated_loras", [])
                image_loras = _director_image_role_loras(
                    params, "editor",
                ).get("activated_loras", [])
                ref_paths = []
                rip = params.get("reference_image_path")
                if rip:
                    ref_paths.append(rip)
                for cp in (params.get("character_ref_paths") or []):
                    if cp:
                        ref_paths.append(cp)
                # Pass character profiles into polish so the LLM has a
                # definitive name → descriptor mapping. Without this, polish
                # silently substitutes generic "the woman" / "the man" for
                # any character name it encounters — catastrophic for
                # non-human characters (Lumi the unicorn became "the woman"
                # in test 03). characters comes from params.characters,
                # the same list passed to the planner.
                characters = params.get("characters", []) or []
                if polish_video_prompts or polish_image_prompts:
                    clip_plans = _pipeline_llm_call(
                        pid,
                        "polishing_prompts",
                        "third_pass_polish",
                        polish_prompts_third_pass,
                        clip_plans, video_model, image_model, nsfw,
                        video_loras=video_loras, image_loras=image_loras,
                        image_paths=ref_paths or None,
                        characters=characters,
                        preserve_video_character_names=(
                            str(video_model).lower().startswith("minimax_h3")
                            and shot_image_policy in {
                                SHOT_IMAGE_PROMPT_ONLY,
                                SHOT_IMAGES_DIRECT_REFERENCES,
                            }
                        ),
                        polish_video_prompts=polish_video_prompts,
                        polish_image_prompts=polish_image_prompts,
                        liveness_kwarg="is_active",
                    )
                    print(
                        "[Pipeline] Model-aware third-pass polish completed "
                        f"for {len(clip_plans)} clips"
                    )
            except _DirectorLlmCancelled:
                return
            except Exception:
                print("[Pipeline] Prompt polish failed (non-fatal)")
        elif polish_mode in ("full_guide", "light_guide"):
            # For inject modes, polish happened inside the planner — note it in the log
            _update_pipeline(pid, _polish_mode_used=polish_mode)

        _update_pipeline(pid, clip_plans=clip_plans)
        _require_pipeline_checkpoint(pid, "committed-plan")

        # Check cancellation
        if _pipelines[pid]["status"] == "cancelled":
            return

        # In non-auto mode, pause for user review after planning
        if not auto_mode and resume_after_pause not in {
            "review_prompts", "review_images",
        }:
            _update_pipeline(pid, status="paused", pause_reason="review_prompts",
                             progress={"current": 1, "total": 3, "message": "Review prompts", "step": 0, "total_steps": 0})
            _require_pipeline_checkpoint(pid, "prompt-review-pause")
            _wait_for_resume(pid)
            if _pipelines[pid]["status"] == "cancelled":
                return
            # Reload clip_plans in case user edited them
            clip_plans = _pipelines[pid]["clip_plans"]

        # ── Phase 2: Generate Start Images ──────────────────────────────
        if requires_shot_images:
            _update_pipeline(pid, phase="generating_images",
                             progress={"current": 0, "total": len(clip_plans), "message": "Generating start images...", "step": 0, "total_steps": 0})
        else:
            guidance_label = (
                "direct references"
                if shot_image_policy == SHOT_IMAGES_DIRECT_REFERENCES
                else "video prompts"
            )
            _update_pipeline(pid, phase="preparing_video",
                             progress={"current": 0, "total": len(clip_plans), "message": f"Using {guidance_label}; no shot images needed", "step": 0, "total_steps": 0})

        # ── Detect the reference's art style while the LLM is still up ──
        # One vision call naming the medium concretely; the phrase gets
        # prepended to every image prompt in _run_image_generation (see
        # the module-level "Reference art-style lock" note). Skipped when
        # already detected (resume) or the reference is photographic.
        from services import llm_service
        _style_ref = params.get("reference_image_path") or ""
        if (
            requires_shot_images
            and "_reference_style" not in params
            and _style_ref
            and os.path.isfile(_style_ref)
        ):
            _style_phrase = ""
            try:
                if llm_service.is_loaded() and getattr(llm_service, "_vision_available", False):
                    _style_raw = _pipeline_llm_call(
                        pid,
                        "reference_style",
                        "reference_style_vlm",
                        llm_service.generate,
                        _STYLE_DESCRIBE_PROMPT,
                        max_new_tokens=48,
                        temperature=0.1,
                        image_paths=[_style_ref],
                        enable_thinking=False,
                    )
                    _style_phrase = _normalize_style_phrase(_style_raw)
                    print(
                        f"[Pipeline {pid}] Reference art style detection "
                        f"completed (recognized={bool(_style_phrase)})"
                    )
            except _DirectorLlmCancelled:
                return
            except Exception:
                print(
                    f"[Pipeline {pid}] Reference art style detection "
                    "skipped (non-fatal)"
                )
            # Record even when empty ("" = photographic / undetected) so
            # resume doesn't re-run the detection.
            params["_reference_style"] = _style_phrase
            _update_pipeline(pid, _reference_style=_style_phrase)

        # Unload LLM to free VRAM
        try:
            if llm_service.is_loaded():
                llm_service.unload_model()
        except Exception as e:
            print(f"[Pipeline] LLM unload warning (non-fatal): {e}")

        # Recovery-era parents require exact child-sidecar evidence for every
        # saved image slot. Legacy states retain existence/cardinality checks.
        # Missing recovery slots are attached or regenerated individually by
        # the stable child unit keys in _run_image_generation.
        if not requires_shot_images:
            _resume_imgs_ok = False
        elif p.get("_recovery_parent"):
            _resume_imgs_ok = _recovered_image_slots_complete(
                pid,
                pipeline_out_dir,
                params,
                clip_plans,
                resume_images,
                p.get("_clip_keyframes"),
            )
        else:
            legacy_keyframes = p.get("_clip_keyframes") or []
            _resume_imgs_ok = (
                isinstance(resume_images, list)
                and len(resume_images) == len(clip_plans)
                and isinstance(legacy_keyframes, list)
                and len(legacy_keyframes) == len(clip_plans)
                and all(
                    filename
                    and os.path.isfile(
                        os.path.join(pipeline_out_dir, filename),
                    )
                    for filename in resume_images
                )
                and all(
                    isinstance(saved, list)
                    and len(saved) == len(plan.get("keyframe_prompts", []) or [])
                    and all(
                        filename
                        and os.path.isfile(
                            os.path.join(pipeline_out_dir, filename),
                        )
                        for filename in saved
                    )
                    for plan, saved in zip(clip_plans, legacy_keyframes)
                )
            )
        if not requires_shot_images:
            clip_images = [""] * len(clip_plans)
            clip_keyframes = [[] for _ in clip_plans]
            print(
                f"[Pipeline {pid}] Shot images skipped by saved policy "
                f"'{shot_image_policy}'."
            )
        elif _resume_imgs_ok:
            clip_images = resume_images
            clip_keyframes = p.get("_clip_keyframes") or [[] for _ in clip_images]
            print(f"[Pipeline {pid}] Resume: reusing {len(clip_images)} start images — skipping image generation")
        else:
            if resume_images:
                print(f"[Pipeline {pid}] Resume: saved start images missing on disk — regenerating")
            clip_images, clip_keyframes = _run_image_generation(pid, params, clip_plans, out_dir=pipeline_out_dir, workspace=pipeline_workspace)

        _update_pipeline(pid, clip_images=clip_images, _clip_keyframes=clip_keyframes)
        _require_pipeline_checkpoint(pid, "committed-images")

        if _pipelines[pid]["status"] == "cancelled":
            return

        if requires_shot_images:
            _require_video_start_images(
                clip_images, len(clip_plans), pipeline_out_dir,
            )

        # In non-auto mode, pause for image review
        if (
            not auto_mode
            and requires_shot_images
            and resume_after_pause != "review_images"
        ):
            _update_pipeline(pid, status="paused", pause_reason="review_images",
                             progress={"current": 2, "total": 3, "message": "Review images", "step": 0, "total_steps": 0})
            _require_pipeline_checkpoint(pid, "image-review-pause")
            _wait_for_resume(pid)
            if _pipelines[pid]["status"] == "cancelled":
                return

            # Review can be open for hours; a gallery cleanup or manual rename
            # during that pause must not silently turn a planned I2V shot into
            # unconditioned T2V.
            _require_video_start_images(
                clip_images, len(clip_plans), pipeline_out_dir,
            )

        # ── Phase 3: Generate Video ─────────────────────────────────────
        _update_pipeline(pid, phase="generating_video",
                         progress={"current": 0, "total": 1, "message": "Generating video...", "step": 0, "total_steps": 0})

        output_files = _run_video_generation(pid, params, clip_plans, planned_clips, clip_images, clip_keyframes, out_dir=pipeline_out_dir, workspace=pipeline_workspace)

        # A Stop during the video phase lands here after the abort. Record
        # whatever clips finished (the Dashboard can rerun/rejoin them),
        # but don't overwrite the cancelled status with "completed".
        if _pipelines[pid]["status"] == "cancelled":
            print(f"[Pipeline {pid}] Cancelled during video generation — keeping {len(output_files or [])} finished clip(s)")
            artifacts = {"output_files": output_files or []}
            if not params.get("seamless", True):
                clip_videos = _clip_video_slots(
                    output_files or [], len(clip_plans),
                )
                if clip_videos:
                    artifacts["_clip_video_files"] = clip_videos
            _update_pipeline(pid, **artifacts)
            _save_pipeline_state(pid)
            return

        completed_clip_videos = []
        if not params.get("seamless", True):
            completed_clip_videos = _clip_video_slots(
                output_files or [], len(clip_plans),
            )
        completed = _update_pipeline(
            pid,
            status="completed",
            phase="completed",
            output_files=output_files,
            _clip_video_files=completed_clip_videos,
            _completed_at=time.time(),
            progress={
                "current": 3, "total": 3, "message": "Done!",
                "step": 0, "total_steps": 0,
            },
        )
        if not completed:
            _update_pipeline(
                pid,
                output_files=output_files or [],
                _clip_video_files=completed_clip_videos,
            )
        _require_pipeline_checkpoint(pid, "completed")

    except Exception as e:
        if isinstance(e, _DirectorLlmCancelled):
            with _pipeline_lock:
                cancelled = (
                    (_pipelines.get(pid) or {}).get("status") == "cancelled"
                )
            if cancelled:
                _save_pipeline_state(pid)
                return
        import traceback
        partial_outputs = getattr(e, "output_files", None)
        if partial_outputs:
            artifact_updates = {"output_files": partial_outputs}
            with _pipeline_lock:
                current_pipeline = _pipelines.get(pid) or {}
                current_plans = current_pipeline.get("clip_plans") or []
                current_params = current_pipeline.get("params") or {}
            if not current_params.get("seamless", True):
                clip_slots = _clip_video_slots(
                    partial_outputs, len(current_plans),
                )
                if clip_slots:
                    artifact_updates["_clip_video_files"] = clip_slots
            _update_pipeline(pid, **artifact_updates)
        traceback.print_exc()
        # Tag with OOM info if applicable so the UI can surface the
        # OOM recovery banner. detect_oom returns None for non-OOM
        # failures, in which case oom_info stays absent.
        _oom_info = (
            dict(e.oom_info)
            if isinstance(
                getattr(e, "oom_info", None), dict,
            ) else None
        )
        try:
            from services.oom_detect import detect_oom
            if _oom_info is None:
                _coef = float(
                    getattr(_wgp, "server_config", {}).get(
                        "vram_safety_coefficient", 0.80,
                    )
                )
                _oom_info = detect_oom(e, _coef)
        except Exception:
            pass  # Never fail a failure handler
        _failure_details = _director_failure_details(
            e, code=_DIRECTOR_PIPELINE_FAILED_CODE,
        )
        _update_pipeline(pid, status="failed",
                         error=_DIRECTOR_PIPELINE_FAILED_MESSAGE,
                         error_code=str(
                             _failure_details.get("code")
                             or _DIRECTOR_PIPELINE_FAILED_CODE
                         ),
                         failure_details=_failure_details,
                         oom_info=_oom_info,
                         _completed_at=time.time(),
                         progress={"current": 0, "total": 0,
                                   "message": _DIRECTOR_PIPELINE_FAILED_MESSAGE,
                                   "step": 0, "total_steps": 0})
        _save_pipeline_state(pid)  # Save on failure too
    finally:
        with _pipeline_lock:
            _pipeline_llm_tokens.pop(pid, None)
            _pipeline_llm_contexts.pop(pid, None)
            current = _pipeline_threads.get(pid)
            if current is threading.current_thread():
                _pipeline_threads.pop(pid, None)


def _wait_for_resume(pid: str, poll_interval: float = 1.0):
    """Block until pipeline is resumed, cancelled, or removed."""
    while True:
        with _pipeline_lock:
            p = _pipelines.get(pid)
            if not p:
                return
            if p["status"] != "paused":
                return
        time.sleep(poll_interval)


def _wait_for_gpu(pid: str, poll_interval: float = 2.0):
    """Block until no generation jobs are actively running on GPU.

    Checks both _gen_lock availability and active job statuses.
    Returns False if pipeline was cancelled while waiting.
    """
    _update_pipeline(pid, progress={
        "current": 0, "total": 1,
        "message": "Waiting for GPU (generation queue)...",
        "step": 0, "total_steps": 0,
    })

    while True:
        if _pipelines.get(pid, {}).get("status") == "cancelled":
            return False

        # Check if any jobs are currently running
        active_jobs = [j for j in _jobs.values()
                       if j.get("status") in ("queued", "running")]
        if not active_jobs:
            return True

        time.sleep(poll_interval)


# ── Planning Phase ──────────────────────────────────────────────────────

def _director_llm_selection(params: dict) -> dict:
    """Resolve one exact server-owned model/provider identity for this run."""
    services_cfg = _wgp.server_config.get("services", {}) if _wgp else {}
    desired_model = params.get("llm_model_id") or services_cfg.get("llm_model_id", "Abhiray/gemma-4-E4B-it-heretic-GGUF")
    desired_device = params.get("llm_device") or services_cfg.get("llm_device", "cpu")
    desired_provider = params.get("llm_provider") or services_cfg.get("llm_provider", "local")
    desired_remote_url = services_cfg.get("llm_remote_url", "")
    desired_api_key = ""
    if desired_provider == "openai":
        desired_api_key = services_cfg.get("openai_api_key", "")
    elif desired_provider == "anthropic":
        desired_api_key = services_cfg.get("anthropic_api_key", "")
    return {
        "model_id": desired_model,
        "device": desired_device,
        "provider": desired_provider,
        "remote_url": desired_remote_url,
        "api_key": desired_api_key,
        "local_gguf_path": "",
        "gguf_file_override": "",
    }


def _resolve_pipeline_llm_context(
    pid: str,
    params: dict,
    selection: dict,
) -> dict:
    """Freeze response assistance only after the exact local lease is held."""
    response_assist = None
    if (
        _explicit_guidance_from_snapshot(params)
        and str(selection.get("provider") or "local").strip().lower() == "local"
        and not str(selection.get("remote_url") or "").strip()
        and not str(selection.get("api_key") or "").strip()
    ):
        try:
            from services.llm_response_assist import (
                build_server_response_assist,
                response_assist_corpus_snapshot,
            )
            corpus_snapshot = response_assist_corpus_snapshot()
            response_assist = build_server_response_assist(
                corpus_snapshot=corpus_snapshot,
            )
        except Exception:
            # Response assistance is best-effort and must fail open.
            response_assist = None
    context = {
        "selection": dict(selection),
        "response_assist": response_assist,
    }
    with _pipeline_lock:
        pipeline = _pipelines.get(pid)
        if (
            not pipeline
            or pipeline.get("status") in _DIRECTOR_TERMINAL_STATUSES
        ):
            raise _DirectorLlmCancelled("Director LLM context is no longer active")
        _pipeline_llm_contexts[pid] = context
    return context


def _ensure_llm_loaded(params: dict) -> dict:
    """Load the exact Director selection and return its reusable lease args."""
    from services import llm_service

    selection = _director_llm_selection(params)
    desired_provider = selection["provider"]
    desired_device = selection["device"]

    # Free GPU memory before running a local CUDA LLM. Director planning
    # fires right after image edits / audio analysis: memory profiles keep
    # the last generation model resident, and torch's caching allocator
    # holds whatever Whisper / the vocal separator reserved — none of it
    # available to the llama-server SUBPROCESS. The server then loads its
    # weights fine but aborts (CUDA OOM → connection reset by peer) when
    # the vision encode spikes during the first planning request; the
    # identical request verified fine on a free GPU. Guarded by _gen_lock
    # so an active generation is never released mid-run; wgp reloads the
    # gen model transparently on its next job (reload_needed).
    if desired_provider == "local" and desired_device == "cuda" and _wgp is not None:
        acquired = _gen_lock.acquire(blocking=False) if _gen_lock is not None else True
        if acquired:
            try:
                if getattr(_wgp, "wan_model", None) is not None:
                    print("[Pipeline] Releasing generation model VRAM before LLM planning")
                    _wgp.release_model()
                else:
                    import gc
                    import torch
                    if torch.cuda.is_available():
                        gc.collect()
                        torch.cuda.empty_cache()
            except Exception as e:
                print(f"[Pipeline] Pre-LLM VRAM release skipped: {e}")
            finally:
                if _gen_lock is not None:
                    _gen_lock.release()
        else:
            print("[Pipeline] Generation in progress — skipping pre-LLM VRAM release")

    # load_model owns the complete idempotence key: device, runtime profile,
    # selected GGUF/projector file identity, provider URL, and credentials.
    # A model/provider-only shortcut here left stale devices and mmproj files
    # resident after settings or downloaded artifacts changed.
    llm_service.load_model(
        model_id=selection["model_id"],
        device=selection["device"],
        provider=selection["provider"],
        remote_url=selection["remote_url"],
        api_key=selection["api_key"],
    )
    return selection


def _run_planning(pid: str, params: dict, pipeline_type: str):
    """Run LLM planning and return (clip_plans, planned_clips).

    Uses the new DirectorOrchestrator when use_director_v2 flag is set,
    otherwise falls back to legacy llm_service calls.
    """
    from services import llm_service

    selection = _ensure_llm_loaded(params)

    # Default v2 — see launch.py services-config comment for rationale.
    # The params dict is built from servicesConfig in the frontend, so
    # this default only fires for direct API callers that didn't pass
    # the flag at all. Keeping it consistent with the services-config
    # default here so the legacy path isn't accidentally hit.
    use_v2 = params.get("use_director_v2", True)

    # Hold the exact provider/model identity across every coherent planning
    # call. The lock is re-entrant inside generate(), so concurrent Chat or
    # enhancer requests cannot swap the singleton between Director passes.
    with llm_service.loaded_model_lease(**selection):
        _resolve_pipeline_llm_context(pid, params, selection)
        if use_v2:
            return _run_planning_v2(pid, params, pipeline_type)
        return _run_planning_legacy(pid, params, pipeline_type)


def _director_h3_style_workflow_present(params: dict) -> bool:
    """Validate the sealed workflow before using it as a planning signal."""
    from services.h3_upstream_skills import validate_resolved_h3_style_workflow

    workflow = validate_resolved_h3_style_workflow(
        params.get("h3_style_workflow"),
    )
    if workflow is None:
        return False
    if str(params.get("video_model") or "") not in _H3_VIDEO_MODELS:
        raise ValueError("Resolved H3 style workflow has a non-H3 Director model")
    return True


def _run_planning_v2(pid: str, params: dict, pipeline_type: str):
    """New architecture: DirectorOrchestrator with planners + renderers."""
    from services import llm_service
    from services.director.orchestrator import DirectorOrchestrator, DirectorFlags

    # Build feature flags from params
    flags_dict = params.get("director_flags", {})
    flags = DirectorFlags.from_dict(flags_dict) if flags_dict else DirectorFlags()

    # Wrap every planner call in a pipeline-bound progress token. Structured
    # JSON calls intentionally bypass response assistance while retaining the
    # same request-scoped streaming/TPS telemetry.
    _pass_counter = [0]
    def _pipeline_generate(*args, **kwargs):
        _pass_counter[0] += 1
        return _pipeline_llm_call(
            pid,
            "planning",
            f"generate_{_pass_counter[0]}",
            llm_service.generate,
            *args,
            **kwargs,
        )

    def _pipeline_streaming(*args, **kwargs):
        _pass_counter[0] += 1
        return _pipeline_llm_call(
            pid,
            "planning",
            f"streaming_{_pass_counter[0]}",
            llm_service.generate_streaming,
            *args,
            **kwargs,
        )

    # Create orchestrator with logged LLM functions
    director = DirectorOrchestrator(
        llm_generate=_pipeline_generate,
        llm_generate_streaming=_pipeline_streaming,
        flags=flags,
    )

    # Map pipeline_type to skill_type
    skill_map = {
        "music_video": "music_video",
        "short_film_audio": "short_film",
        "short_film_story": "short_film",
        "podcast": "podcast",
        "viral_video": "viral_video",
    }
    skill_type = skill_map.get(pipeline_type, "music_video")

    # Build planner kwargs
    scene_description = params.get("scene_description", "")
    reference_image_path = params.get("reference_image_path")
    planned_clips = params.get("planned_clips", [])

    # Reuse the server-authorized request-local decision persisted before the
    # worker started. A restart or settings change cannot mix prompt modes.
    services_cfg = _wgp.server_config.get("services", {}) if _wgp else {}
    nsfw = _explicit_guidance_from_snapshot(params)
    # Multi-shot LoRA mode — passes through to Pass 2 so it can emit
    # storyboard-format video_prompts for medium-length shots. See
    # the toggle's comment in launch.py for behavior details.
    multishot_lora_mode = services_cfg.get("director_multishot_lora_mode", False)

    seamless = params.get("seamless", True)
    # Pass video_model and image_model to every planner so Pass 2 can
    # route its prompt guides correctly. Previously these only flowed
    # into polish_block construction (when polish_mode was on); now the
    # planner gets them unconditionally so it can pick the right
    # dialect-aware guide files (ltx2_shot_breakdown.md for LTX-2,
    # flux_image_edit_pass2.md for Flux.2 Klein, etc.).
    planner_kwargs = {
        "reference_image_path": reference_image_path,
        "speaker_mappings": params.get("speaker_mappings"),
        "characters": params.get("characters", []),
        "nsfw": nsfw,
        "seamless": seamless,
        "video_model": params.get("video_model", ""),
        "image_model": _director_image_role_model(params, "editor"),
        "visual_style": params.get("visual_style", ""),
        "h3_style_workflow_present": _director_h3_style_workflow_present(params),
        "multishot_lora_mode": multishot_lora_mode,
    }

    if pipeline_type == "short_film_story":
        planner_kwargs.update({
            "story_description": scene_description,
            "target_duration": params.get("target_duration", 60),
            "target_scenes": params.get("target_scenes"),
            "narrative_mode": params.get("narrative_mode", False),
            "fps": params.get("fps", 16),
            "frames_steps": params.get("frames_steps", 8),
            "frames_minimum": params.get("frames_minimum", 41),
        })
    elif pipeline_type == "short_film_audio":
        planner_kwargs.update({
            "clips": planned_clips,
            "story_description": scene_description,
            "audio_path": params.get("audio_path"),
            "lyrics": params.get("lyrics"),
        })
    elif pipeline_type in ("podcast", "viral_video"):
        planner_kwargs.update({
            "clips": planned_clips if planned_clips else None,
            "transcript": params.get("lyrics"),
            "audio_path": params.get("audio_path"),
            "concept": scene_description,
            "target_duration": params.get("target_duration", 30),
            "platform": params.get("platform", "general"),
            "style": params.get("style") or "",
        })
    else:
        # Music video
        planner_kwargs.update({
            "clips": planned_clips,
            "scene_description": scene_description,
            "lyrics": params.get("lyrics"),
            "bpm": params.get("bpm", 120),
        })

    # Inject LoRA guides + model dialect guides into the planner only for
    # the full/light_guide inject modes (legacy paths). Default mode
    # "third_pass" deliberately skips this — model dialect is applied
    # per-prompt after planning by polish_prompts_third_pass(), which
    # avoids stacking conflicting dialect guidance into Pass 2's already
    # crowded system prompt.
    polish_mode = services_cfg.get("director_prompt_polish", "third_pass")
    if polish_mode in ("full_guide", "light_guide"):
        from services.director.prompt_polish import build_polish_block
        guide_mode = "full" if polish_mode == "full_guide" else "light"
        video_model = params.get("video_model", "")
        image_model = _director_image_role_model(params, "editor")
        video_loras = (params.get("video_loras") or {}).get("activated_loras", [])
        image_loras = _director_image_role_loras(
            params, "editor",
        ).get("activated_loras", [])
        polish_block = build_polish_block(video_model, image_model, guide_mode,
                                          video_loras=video_loras, image_loras=image_loras)
        if polish_block:
            planner_kwargs["polish_block"] = polish_block
            print(f"[Pipeline {pid}] Injected {guide_mode} polish block ({len(polish_block)} chars)")

    # Also pass character/location ref labels and paths for image prompt rules
    planner_kwargs["character_ref_paths"] = params.get("character_ref_paths", [])
    planner_kwargs["character_ref_labels"] = params.get("character_ref_labels", [])
    planner_kwargs["location_ref_paths"] = params.get("location_ref_paths", [])
    planner_kwargs["location_ref_labels"] = params.get("location_ref_labels", [])

    # Plan
    print(f"[Pipeline {pid}] Planning with DirectorOrchestrator (skill={skill_type})...")
    plan = director.plan(skill_type, **planner_kwargs)

    # Store the production plan in pipeline state for later reference
    _update_pipeline(pid, production_plan=plan.to_dict())

    # Render prompts
    has_reference = bool(reference_image_path)
    rendered = director.render_plan(plan, prompt_type="both", has_reference=has_reference)
    clip_plans = director.plan_to_clip_plans(rendered)

    # Build planned_clips from shot data (for story mode which creates clips)
    if pipeline_type == "short_film_story":
        cumulative = 0.0
        # Get FPS from model definition for accurate frame count
        fps = params.get("fps", 16)
        try:
            vm = params.get("video_model", "")
            md = _wgp.get_model_def(vm) if vm else None
            if md and md.get("fps"):
                fps = md["fps"]
        except Exception:
            pass
        new_clips = []
        for shot in plan.shots:
            duration_frames = shot.metadata.get("duration_frames") if shot.metadata else int(shot.duration_sec * fps)
            new_clips.append({
                "start": cumulative,
                "end": cumulative + shot.duration_sec,
                "duration_sec": shot.duration_sec,
                "duration_frames": duration_frames,
                "label": shot.narrative_role or shot.scene_type or "scene",
                "beat_count": 0,
            })
            cumulative += shot.duration_sec
        planned_clips = new_clips

    # Normalize
    if clip_plans and isinstance(clip_plans[0], str):
        clip_plans = [{"video_prompt": p, "image_prompt": ""} for p in clip_plans]

    if str(params.get("video_model") or "").casefold().startswith("minimax_h3"):
        _attach_director_h3_shot_contracts(
            clip_plans,
            planned_clips,
            plan.shots,
        )

    # Debug: log shot structure
    for idx, cp in enumerate(clip_plans):
        kf_count = len(cp.get("keyframe_prompts", []) or [])
        wc = cp.get("window_count", 1)
        pc = planned_clips[idx] if idx < len(planned_clips) else {}
        dur = pc.get("duration_sec", pc.get("duration_frames", "?"))
        print(f"[Pipeline] Shot {idx+1}: duration={dur}s, windows={wc}, keyframes={kf_count}, prompt_len={len(cp.get('video_prompt',''))}")

    return clip_plans, planned_clips


def _run_planning_legacy(pid: str, params: dict, pipeline_type: str):
    """Legacy planning: direct calls to llm_service functions."""
    from services import llm_service

    scene_description = params.get("scene_description", "")
    reference_image_path = params.get("reference_image_path")
    speaker_mappings = params.get("speaker_mappings", [])
    characters = params.get("characters", [])
    audio_path = params.get("audio_path")
    planned_clips = params.get("planned_clips", [])
    fps = params.get("fps", 16)
    frames_steps = params.get("frames_steps", 8)
    frames_minimum = params.get("frames_minimum", 41)
    explicit_guidance = _explicit_guidance_from_snapshot(params)

    if pipeline_type == "short_film_story":
        # Path C: Full story-based planning
        target_duration = params.get("target_duration", 60)
        narrative_mode = params.get("narrative_mode", False)

        result = _pipeline_llm_call(
            pid, "planning", "legacy_short_film_story",
            llm_service.plan_short_film_from_story,
            story_description=scene_description,
            characters=characters,
            reference_image_path=reference_image_path,
            target_duration=target_duration,
            narrative_mode=narrative_mode,
            fps=fps,
            frames_steps=frames_steps,
            frames_minimum=frames_minimum,
            visual_style=params.get("visual_style"),
            h3_style_workflow_present=_director_h3_style_workflow_present(params),
            nsfw=explicit_guidance,
            allow_response_assist=False,
        )
        planned_clips = result.get("clips", [])
        clip_plans = result.get("clip_plans", [])

    elif pipeline_type == "short_film_audio":
        # Path B: Short film with uploaded dialogue audio
        result = _pipeline_llm_call(
            pid, "planning", "legacy_short_film_audio",
            llm_service.plan_short_film_prompts,
            clips=planned_clips,
            scene_description=scene_description,
            lyrics=params.get("lyrics", ""),
            reference_image_path=reference_image_path,
            speaker_mappings=speaker_mappings,
            characters=characters,
            prompt_type="both",
            visual_style=params.get("visual_style"),
            h3_style_workflow_present=_director_h3_style_workflow_present(params),
            nsfw=explicit_guidance,
            allow_response_assist=False,
        )
        clip_plans = result if isinstance(result, list) else result.get("clip_plans", [])

    else:
        # Music video flow
        result = _pipeline_llm_call(
            pid, "planning", "legacy_music_video",
            llm_service.plan_clip_prompts_and_images,
            clips=planned_clips,
            scene_description=scene_description,
            lyrics=params.get("lyrics", ""),
            bpm=params.get("bpm"),
            reference_image_path=reference_image_path,
            speaker_mappings=speaker_mappings,
            prompt_type="both",
            visual_style=params.get("visual_style"),
            h3_style_workflow_present=_director_h3_style_workflow_present(params),
            nsfw=explicit_guidance,
            allow_response_assist=False,
        )
        clip_plans = result if isinstance(result, list) else result.get("clip_plans", [])

    # Normalize clip_plans to list of dicts
    if clip_plans and isinstance(clip_plans[0], str):
        clip_plans = [{"video_prompt": p, "image_prompt": ""} for p in clip_plans]

    return clip_plans, planned_clips


# ── Image Generation Phase ──────────────────────────────────────────────

def _run_image_generation(pid: str, params: dict, clip_plans: list[dict], out_dir: str = None, workspace: str = None) -> tuple[list[str], list[list[str]]]:
    """Generate start images and keyframe images per clip.

    Returns:
        (clip_images, clip_keyframes) where:
        - clip_images[i] = start image filename for clip i
        - clip_keyframes[i] = list of keyframe image filenames for clip i (may be empty)
    """
    _validate_director_models(params, stages=("image",))
    ref_image_path = params.get("reference_image_path")
    character_ref_paths = params.get("character_ref_paths", []) or []
    location_ref_paths = params.get("location_ref_paths", []) or []
    legacy_image_model = params.get("image_model", "flux2_klein_9b")
    legacy_image_loras = params.get("image_loras", {})
    video_model = params.get("video_model") or "ltx2_22B_distilled_1_1"
    supports_frame_injection = _director_supports_frame_injection(video_model)

    # Diagnostic-only log: report what the frontend sent so a future
    # "I selected N LoRAs but only K were applied" report has data we
    # can correlate against the [LoRA] Loading line wgp prints.
    _activated_in = list(legacy_image_loras.get("activated_loras", []) or [])
    _mults_in = legacy_image_loras.get("loras_multipliers", "") or ""
    if _activated_in:
        print(
            f"[Pipeline {pid}] Image LoRAs received: {len(_activated_in)} | "
            f"model={legacy_image_model} | "
            f"names={[os.path.basename(n) for n in _activated_in]} | "
            f"multipliers={_mults_in!r}"
        )

    # ── Filter image LoRAs to those that exist in the image model's dir ──
    # The frontend's DirectorLoraSelector filters available LoRAs by
    # model directory, but `savedLoraPerMode.image` persists across
    # sessions and can hold stale activations from a previous model
    # selection (e.g. an LTX-2 LoRA name that's never been in the
    # flux2_klein_9b/ directory). Without this filter, wgp.validate_task
    # rejects the entire task with "The following Loras files are missing
    # or invalid: [...]" and image gen never starts.
    #
    # This is a file-EXISTENCE check only — no architecture detection,
    # no dim peeking. Just: is the .safetensors actually in the right
    # directory? If not, drop it with a clear warning so the user knows
    # to re-select their image LoRAs for the active model.
    try:
        if _activated_in and not _director_uses_image_roles(params):
            try:
                _lora_dir = _wgp.get_lora_dir(legacy_image_model)
            except Exception:
                _lora_dir = ""
            if _lora_dir and os.path.isdir(_lora_dir):
                _existing = {
                    f for f in os.listdir(_lora_dir)
                    if f.lower().endswith((".safetensors", ".sft"))
                }
                _mult_tokens = _mults_in.split()
                _kept: list[str] = []
                _kept_mults: list[str] = []
                _skipped: list[str] = []
                for _idx, _name in enumerate(_activated_in):
                    _basename = os.path.basename(_name)
                    if _basename in _existing:
                        _kept.append(_name)
                        if _idx < len(_mult_tokens):
                            _kept_mults.append(_mult_tokens[_idx])
                    else:
                        _skipped.append(_basename)
                if _skipped:
                    _warn = (
                        f"Skipped {len(_skipped)} image LoRA(s) not present in "
                        f"{os.path.basename(_lora_dir)}/: {_skipped}. These were "
                        f"likely activated when a different image model was selected, "
                        f"and the saved selection persisted across sessions. Re-select "
                        f"the LoRAs you want for {legacy_image_model} in the Director image "
                        f"LoRA panel to clear the stale entries."
                    )
                    print(f"[Pipeline {pid}] {_warn}")
                    _existing_warnings = _pipelines.get(pid, {}).get("lora_warnings", []) or []
                    _update_pipeline(pid, lora_warnings=[*_existing_warnings, _warn])
                _activated_in = _kept
                _mults_in = " ".join(_kept_mults)
                legacy_image_loras = {
                    "activated_loras": _activated_in,
                    "loras_multipliers": _mults_in,
                }
                print(
                    f"[Pipeline {pid}] Image LoRAs after existence filter: "
                    f"{len(_kept)} kept, {len(_skipped)} skipped"
                )
    except Exception as _e:
        print(f"[Pipeline {pid}] LoRA file-existence filter skipped: {_e}")

    if not out_dir:
        out_dir = _wgp.save_path

    # Resume and Dashboard repairs can carry a generated anchor even though
    # the user-facing reference path is intentionally still empty.
    if not (ref_image_path and os.path.isfile(ref_image_path)):
        generated_anchor = params.get(
            "generated_reference_image_filename", "",
        )
        if (
            generated_anchor
            and os.path.basename(generated_anchor) == generated_anchor
        ):
            generated_anchor_path = os.path.join(out_dir, generated_anchor)
            if os.path.isfile(generated_anchor_path):
                ref_image_path = generated_anchor_path

    # Build full refs list: main scene + character refs + location refs. Keep
    # character and location refs separate so a generated identity anchor can
    # use the former without allowing location imagery to dominate the cast.
    valid_character_refs = [
        p for p in character_ref_paths if p and os.path.isfile(p)
    ]
    valid_location_refs = [
        p for p in location_ref_paths if p and os.path.isfile(p)
    ]
    extra_refs = valid_character_refs + valid_location_refs
    print(f"[Pipeline {pid}] Image refs: main={ref_image_path}, chars={len(character_ref_paths)}, locs={len(location_ref_paths)}, extra_valid={len(extra_refs)}")

    # Count total images to generate (start images + keyframes)
    total_images = len(clip_plans)
    planned_keyframes = sum(
        len(plan.get("keyframe_prompts", []) or [])
        for plan in clip_plans
    )
    if supports_frame_injection:
        total_images += planned_keyframes
    elif planned_keyframes:
        print(
            f"[Pipeline {pid}] {video_model} does not support injected "
            f"keyframes; skipping {planned_keyframes} intermediate image(s)."
        )

    clip_images: list[str] = []
    clip_keyframes: list[list[str]] = []
    image_count = 0

    # Reference art-style lock: the exact lead sentence validated to hold
    # Klein to a stylized medium. Applied to EVERY image prompt (start
    # images, keyframes, anchor) at generation time — after polish, and
    # regardless of whether the planner remembered to name the medium.
    _style_prefix = _style_prefix_for(params.get("_reference_style") or "")

    def _gen_image(
        prompt: str,
        source_ref: str,
        include_extra_refs: bool = True,
        supplemental_refs: Optional[list[str]] = None,
        *,
        recovery_kind: str,
        recovery_variant: int = 0,
        recovery_index: int = 0,
    ) -> str:
        """Generate a single image using source_ref + optional extra refs."""
        nonlocal image_count
        _pre_strip = prompt
        prompt = _strip_motion_effects(prompt or "")
        if prompt != _pre_strip:
            print(f"[Pipeline {pid}] Stripped motion-effect language from image prompt")
        if _style_prefix and not prompt.lower().startswith("maintain the same"):
            prompt = _style_prefix + prompt
        all_refs = []
        seen_refs = set()
        selected_extra_refs = (
            extra_refs if supplemental_refs is None else supplemental_refs
        )
        for candidate in [source_ref] + (
            selected_extra_refs if include_extra_refs else []
        ):
            if not candidate or not os.path.isfile(candidate):
                continue
            resolved = os.path.normcase(os.path.realpath(candidate))
            if resolved in seen_refs:
                continue
            seen_refs.add(resolved)
            all_refs.append(candidate)
        role = "editor" if all_refs else "creator"
        image_model = _director_image_role_model(params, role)
        image_loras = (
            _director_image_role_loras(params, role)
            if _director_uses_image_roles(params)
            else legacy_image_loras
        )
        image_params = _director_image_params(params, image_model)
        resolution = image_params.get("resolution", "1280x720")
        steps = image_params.get("num_inference_steps", 8)
        guidance = image_params.get("guidance_scale", 1)
        all_refs = _limit_director_image_refs(
            image_model,
            all_refs,
            pid=pid,
        )
        print(f"[Pipeline {pid}] _gen_image: {len(all_refs)} refs: {[os.path.basename(r) for r in all_refs]}")
        prompt = _director_role_prompt(prompt, image_loras, role)
        gen_params: dict = {
            "model_type": image_model,
            "prompt": prompt,
            "image_refs": all_refs,
            "image_mode": 1,
            "image_prompt_type": "",
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            # 'I' carries an image reference; a ref-less anchor is plain T2I.
            "video_prompt_type": "KI" if all_refs else "",
            "resolution": resolution,
            "seed": -1,
            "settings_version": 2.52,
            "generation_mode": "image",
            "repeat_generation": 1,
            "negative_prompt": "",
            "video_length": 1,
            "activated_loras": image_loras.get("activated_loras", []),
            "loras_multipliers": image_loras.get("loras_multipliers", ""),
            "_director_pipeline_id": pid,
            "_director_recovery_unit": {
                "kind": recovery_kind,
                "variant": recovery_variant,
                "index": recovery_index,
            },
        }
        output_files = _submit_and_wait(gen_params, timeout_s=600, workspace=workspace, out_dir=out_dir)
        if not output_files or not output_files[0]:
            raise RuntimeError(
                "Image generation completed without a recorded output."
            )
        image_count += 1
        return output_files[0]

    # If no reference image was provided, generate a single establishing /
    # "anchor" image from the scene description and adopt it as the shared
    # reference, so every clip's start image keeps a consistent look instead of
    # each being generated independently with no visual through-line.
    if not (ref_image_path and os.path.isfile(ref_image_path)):
        scene_desc = (params.get("scene_description") or "").strip()
        first_shot_prompt = (
            clip_plans[0].get("image_prompt", "") if clip_plans else ""
        ).strip()
        anchor_subject = first_shot_prompt or scene_desc or (
            "cinematic establishing shot"
        )
        anchor_prompt = (
            "Create a definitive cinematic character anchor for visual "
            "continuity. Clearly establish the recurring subject or people, "
            "especially faces, hair, wardrobe, body attributes, and overall "
            f"design. {anchor_subject}"
        )
        character_profiles = []
        for character in params.get("characters", []) or []:
            if not isinstance(character, dict):
                continue
            name = str(
                character.get("name")
                or character.get("display_name")
                or ""
            ).strip()
            description = str(
                character.get("description")
                or character.get("physical_description")
                or character.get("visual_description")
                or ""
            ).strip()
            wardrobe = str(character.get("wardrobe") or "").strip()
            profile = ": ".join(part for part in (name, description) if part)
            if wardrobe:
                profile = f"{profile}; wardrobe: {wardrobe}" if profile else wardrobe
            if profile:
                character_profiles.append(profile)
        if character_profiles:
            anchor_prompt += (
                " Recurring character profiles: "
                + " | ".join(character_profiles)
                + "."
            )
        if valid_character_refs:
            anchor_prompt += (
                " Use the provided character reference image(s) as the "
                "definitive identity and appearance source."
            )
        if scene_desc and scene_desc.lower() not in anchor_subject.lower():
            anchor_prompt += f" Project concept: {scene_desc}"
        total_images += 1
        _update_pipeline(pid, progress={
            "current": 0,
            "total": total_images,
            "message": "Generating establishing image",
            "step": 0, "total_steps": 0,
        })
        print(f"[Pipeline {pid}] No reference image — generating establishing/anchor image first.")
        anchor_file = _gen_image(
            anchor_prompt,
            "",
            supplemental_refs=valid_character_refs,
            recovery_kind="image_anchor",
        )
        anchor_path = os.path.realpath(os.path.join(out_dir, anchor_file))
        output_root = os.path.realpath(os.path.abspath(out_dir))
        if (
            os.path.normcase(os.path.dirname(anchor_path))
                != os.path.normcase(output_root)
            or not os.path.isfile(anchor_path)
        ):
            raise RuntimeError(
                "The generated Director anchor could not be found in the "
                "pipeline output directory; video generation was not started."
            )
        ref_image_path = anchor_path
        params["generated_reference_image_filename"] = anchor_file
        _update_pipeline(
            pid, generated_reference_image_filename=anchor_file,
        )
        _require_pipeline_checkpoint(pid, "image-anchor")
        print(f"[Pipeline {pid}] Adopted establishing image as shared reference: {anchor_file}")

    for i, plan in enumerate(clip_plans):
        if _pipelines[pid]["status"] == "cancelled":
            return clip_images, clip_keyframes

        # ── Determine image source: original reference or previous scene's output ──
        image_source = plan.get("image_source", "original")
        source_ref = ref_image_path  # default: user's original reference

        if image_source == "previous" and i > 0 and clip_images[i - 1]:
            prev_img_path = os.path.join(out_dir, clip_images[i - 1])
            if os.path.isfile(prev_img_path):
                source_ref = prev_img_path
                print(f"[Pipeline {pid}] Shot {i+1}: using previous scene output as source ({clip_images[i-1]})")

        _update_pipeline(pid, progress={
            "current": image_count,
            "total": total_images,
            "message": f"Shot {i + 1}: generating start image ({image_source})",
            "step": 0, "total_steps": 0,
        })

        prompt = plan.get("image_prompt", "")
        ref_exists = os.path.isfile(source_ref) if source_ref else False
        print(f"[Pipeline {pid}] Shot {i+1} start image: source={image_source}, ref={source_ref} (exists={ref_exists}), prompt='{prompt[:60]}...'")

        img_t0 = time.time()
        try:
            if image_source == "previous" and source_ref != ref_image_path:
                # Dual reference: previous scene output as primary + original reference for character identity
                # _gen_image puts source_ref first, then extra_refs (which includes character/location refs).
                # We temporarily prepend the original ref to extra_refs so the model sees both.
                saved_extras = extra_refs[:]
                extra_refs.insert(0, ref_image_path)
                start_img = _gen_image(
                    prompt, source_ref, include_extra_refs=True,
                    recovery_kind="image_start", recovery_index=i,
                )
                extra_refs[:] = saved_extras  # restore
            else:
                start_img = _gen_image(
                    prompt, ref_image_path,
                    recovery_kind="image_start", recovery_index=i,
                )
            clip_images.append(start_img)
            _update_pipeline(
                pid,
                clip_images=list(clip_images),
                _clip_keyframes=list(clip_keyframes),
            )
            _require_pipeline_checkpoint(pid, f"image-start-{i}")
        except _GenerationTimeoutError:
            raise
        except Exception as e:
            print(f"[Pipeline {pid}] Shot {i+1} start image failed: {e}")
            clip_images.append("")
        # Record per-clip image timing
        timings = _pipelines.get(pid, {}).get("_clip_timings", {})
        timings[f"image_{i}"] = round(time.time() - img_t0, 2)
        _update_pipeline(pid, _clip_timings=timings)

        # ── Generate keyframes (chained from previous output) ──
        keyframe_prompts = plan.get("keyframe_prompts", []) or []
        shot_keyframes: list[str] = []

        if supports_frame_injection and keyframe_prompts and clip_images[-1]:
            # Chain: each keyframe edits from the previous image
            chain_ref = os.path.join(out_dir, clip_images[-1])  # start from the start image

            for ki, kf_prompt in enumerate(keyframe_prompts):
                if _pipelines[pid]["status"] == "cancelled":
                    break

                # Ensure kf_prompt is a string (LLM may return dicts or other types)
                if isinstance(kf_prompt, dict):
                    kf_prompt = kf_prompt.get("prompt", kf_prompt.get("image_prompt", str(kf_prompt)))
                elif not isinstance(kf_prompt, str):
                    kf_prompt = str(kf_prompt)
                if not kf_prompt or not kf_prompt.strip():
                    continue

                _update_pipeline(pid, progress={
                    "current": image_count,
                    "total": total_images,
                    "message": f"Shot {i + 1}: keyframe {ki + 1}/{len(keyframe_prompts)}",
                    "step": 0, "total_steps": 0,
                })

                print(f"[Pipeline {pid}] Shot {i+1} keyframe {ki+1}: chain_ref='{os.path.basename(chain_ref)}', prompt='{str(kf_prompt)[:60]}...'")

                try:
                    kf_img = _gen_image(
                        kf_prompt, chain_ref,
                        recovery_kind="image_keyframe",
                        recovery_variant=i,
                        recovery_index=ki,
                    )
                    shot_keyframes.append(kf_img)
                    _update_pipeline(
                        pid,
                        clip_images=list(clip_images),
                        _clip_keyframes=[
                            *[list(items) for items in clip_keyframes],
                            list(shot_keyframes),
                        ],
                    )
                    _require_pipeline_checkpoint(
                        pid, f"image-keyframe-{i}-{ki}",
                    )
                    # Chain: next keyframe edits from this one
                    if kf_img:
                        chain_ref = os.path.join(out_dir, kf_img)
                except _GenerationTimeoutError:
                    raise
                except Exception as e:
                    print(f"[Pipeline {pid}] Shot {i+1} keyframe {ki+1} failed: {e}")
                    shot_keyframes.append("")

        clip_keyframes.append(shot_keyframes)

    _update_pipeline(pid, progress={
        "current": total_images,
        "total": total_images,
        "message": "All images generated",
        "step": 0, "total_steps": 0,
    })

    return clip_images, clip_keyframes


# ── Video Generation Phase ──────────────────────────────────────────────

_H3_BASE_FL2VA_MODEL = "minimax_h3"
_H3_EXPLICIT_FL2VA_MODEL = "minimax_h3_pinkcherry_fl2va"
_H3_W4A8_FL2VA_MODEL = "minimax_h3_w4a8_fl2va"
_H3_REF2VA_MODEL = "minimax_h3_ref2va"
_H3_FL2VA_MODELS = {
    _H3_BASE_FL2VA_MODEL,
    _H3_EXPLICIT_FL2VA_MODEL,
    _H3_W4A8_FL2VA_MODEL,
}
_H3_VIDEO_MODELS = _H3_FL2VA_MODELS | {_H3_REF2VA_MODEL}
_DIRECTOR_CLIP_SEPARATOR = "\n---CLIP_BOUNDARY---\n"
_DIRECTOR_H3_RECORD_PAYLOAD_RE = re.compile(
    r"^(?:\[Shot\s+\d+\]\s*)?"
    r"shot_name:\s*(?P<name>[^|\r\n]+?)\s*\|\s*"
    r"audiovisual_description:\s*(?P<description>[^|\r\n]+?)\s*"
    r"(?:\|\s*dialogue_and_vocalizations:\s*"
    r"(?P<vocals>[^\r\n]+?))?\s*$",
)
_DIRECTOR_H3_DIALOGUE_RE = re.compile(
    r"<d>\s*\[[^\]\r\n]+\]\s+.*?</d>", re.IGNORECASE | re.DOTALL,
)


def _director_h3_json_value(value):
    """Return one structured Director value without importing a serializer."""
    if callable(getattr(value, "to_dict", None)):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return value


def _attach_director_h3_shot_contracts(
    clip_plans: list[dict],
    planned_clips: list[dict],
    shots,
) -> None:
    """Keep H3 story semantics beside prompts through save/resume."""
    for index, shot in enumerate(shots or []):
        metadata = getattr(shot, "metadata", None) or {}
        audio_plan = getattr(shot, "audio_plan", None)
        contract = {
            "shot_id": str(getattr(shot, "shot_id", "") or ""),
            "continuity_strategy": str(
                getattr(shot, "continuity_strategy", "independent")
                or "independent"
            ),
            "environment": str(getattr(shot, "environment", "") or ""),
            "visual_style": str(getattr(shot, "visual_style", "") or ""),
            "lighting": str(getattr(shot, "lighting", "") or ""),
            "spatial_setup": str(getattr(shot, "spatial_setup", "") or ""),
            "subjects_on_screen": [
                _director_h3_json_value(subject)
                for subject in (getattr(shot, "subjects_on_screen", None) or [])
            ],
            "dialogue_beats": [
                _director_h3_json_value(beat)
                for beat in (getattr(shot, "dialogue_beats", None) or [])
            ],
            "closing_blocking": str(
                metadata.get("closing_blocking")
                or getattr(shot, "ending_beat", "")
                or ""
            ),
            "audio_plan": (
                _director_h3_json_value(audio_plan) if audio_plan else {}
            ),
        }
        if index < len(clip_plans):
            clip_plans[index]["_h3_shot"] = contract
        if index < len(planned_clips) and isinstance(planned_clips[index], dict):
            planned_clips[index]["_h3_shot"] = contract


def _director_h3_time_token(seconds: float) -> str:
    """Use a stable frame-precise token for deterministic Director ranges."""
    return f"{max(0.0, float(seconds)):.3f}"


def _director_h3_record_payload(text: str, number: int) -> tuple[str, str, str]:
    """Return canonical fields without interpreting creative subject matter."""
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    compact = re.sub(r"^\[Shot\s+\d+\]\s*", "", compact)
    exact = _DIRECTOR_H3_RECORD_PAYLOAD_RE.fullmatch(compact)
    if exact:
        return (
            exact.group("name").strip(),
            exact.group("description").strip(),
            (exact.group("vocals") or "none").strip(),
        )
    if "|" in compact or re.search(
        r"\b(?:shot_name|audiovisual_description|"
        r"dialogue_and_vocalizations)\s*:", compact,
    ):
        raise ValueError(
            "Director H3 record labels are malformed; exact mapping is unavailable"
        )
    dialogue = _DIRECTOR_H3_DIALOGUE_RE.findall(compact)
    description = _DIRECTOR_H3_DIALOGUE_RE.sub(" ", compact)
    description = re.sub(r"\s+", " ", description).strip()
    if not description:
        description = "Dialogue"
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", description)
    shot_name = " ".join(words[:8]) or f"Shot {number}"
    return shot_name, description, " ".join(dialogue) if dialogue else "none"


def _director_h3_canonical_prompt(
    prompt: str,
    *,
    duration_seconds: float,
    events: list[dict] | None = None,
    mode: str | None = None,
) -> str:
    """Map one Director source/segment to strict physical Context-IR records."""
    from services.director.h3_dialogue import (
        _H3_NO_SUBJECT_DEFINITIONS,
        _h3_subject_identity_aliases,
        _parse_h3_subject_definitions,
        _extract_h3_fields,
        validate_h3_context_ir_records,
    )
    from shared.utils.prompt_parser import parse_global_timeline_prompt

    source = str(prompt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    requested_mode = str(mode or "").strip().casefold()
    if requested_mode not in {"", "t2va", "ref2va"}:
        raise ValueError("Director H3 Context-IR mode is unsupported")
    if not requested_mode:
        requested_mode = (
            "ref2va"
            if re.search(
                r"(?mi)^\s*(?:summary|retention_analysis|detailed_description)\s*:",
                source,
            )
            else "t2va"
        )
    # The shared splitter may carry FINAL BLOCKING onto its own line between
    # a record's visual and vocal fields. Rejoin only that known deterministic
    # shape before parsing; both literal payloads remain unchanged.
    source = re.sub(
        r"(?m)^(?P<record>\[[^\r\n]+\|\s*"
        r"audiovisual_description:[^\r\n|]+)\s*$\n"
        r"FINAL BLOCKING:\s*(?P<blocking>[^\r\n|]+?)\s*\|\s*"
        r"dialogue_and_vocalizations:\s*(?P<vocals>[^\r\n]+)\s*$",
        lambda match: (
            f"{match.group('record')} | dialogue_and_vocalizations: "
            f"{match.group('vocals').strip()}\n"
            f"FINAL BLOCKING: {match.group('blocking').strip()}"
        ),
        source,
    )
    duration = float(duration_seconds)
    if duration <= 0:
        raise ValueError("Director H3 prompt duration must be positive")
    existing_errors = validate_h3_context_ir_records(
        source, mode=requested_mode, duration_seconds=duration,
    )
    if not existing_errors:
        return source
    if requested_mode == "ref2va":
        # Ref2VA has its own six-field schema. Mapping an invalid reference
        # prompt through the Base compiler would erase retention/provenance
        # semantics, so fail closed instead of manufacturing Base fields.
        raise ValueError(
            "Director H3 Ref2VA prompt validation failed: "
            + "; ".join(existing_errors)
        )

    source_fields = _extract_h3_fields(source)
    raw_subject_definitions = str(
        source_fields.get("subject_definitions") or ""
    ).strip()
    # Legacy Director sources sometimes put bare timeline records between the
    # subject namespace and the first Context-IR field. They are not entity
    # definitions even though the generic field extractor includes them.
    subject_definition_lines: list[str] = []
    for raw_line in raw_subject_definitions.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\[\s*(?:Shot|Scene)\s+\d+", line, re.IGNORECASE):
            break
        if parse_global_timeline_prompt(line)[1]:
            break
        subject_definition_lines.append(line)
    subject_definitions = "\n".join(subject_definition_lines).strip()
    first_field = re.search(
        r"(?mi)^\s*integrated_multimodal_description\s*:", source,
    )
    if first_field and source[:first_field.start()].strip():
        prefix = source[:first_field.start()].strip()
        subject_prefix = re.fullmatch(
            r"(?is)subject_definitions\s*:(?P<body>.*)", prefix,
        )
        prefix_body = (
            " ".join(subject_prefix.group("body").split())
            if subject_prefix is not None else ""
        )
        definitions_compact = " ".join(subject_definitions.split())
        prefix_suffix = (
            prefix_body[len(definitions_compact):].strip()
            if definitions_compact
            and prefix_body.startswith(definitions_compact)
            else prefix_body
        )
        prefix_suffix_is_timeline = not prefix_suffix or bool(
            re.match(r"^(?:\[?\s*(?:Shot|Scene)\s+\d+|\d+(?:\.\d+)?\s*s?)\b", prefix_suffix, re.IGNORECASE)
        )
        if subject_prefix is None or (
            prefix_body != definitions_compact
            and not (definitions_compact and prefix_suffix_is_timeline)
        ):
            raise ValueError(
                "Director H3 prompt contains wrapper text; exact mapping is unavailable"
            )

    soundscape = "N/A"
    music = "N/A"
    definition_lines = {
        " ".join(line.split()).casefold()
        for line in subject_definitions.splitlines()
        if line.strip()
    }
    context_lines: list[str] = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        sound_match = re.fullmatch(
            r"overall_soundscape\s*:\s*(.+)", line, re.IGNORECASE,
        )
        music_match = re.fullmatch(
            r"non_diegetic_music\s*:\s*(.+)", line, re.IGNORECASE,
        )
        if sound_match:
            soundscape = sound_match.group(1).strip()
            continue
        if music_match:
            music = music_match.group(1).strip()
            continue
        if re.match(r"subject_definitions\s*:", line, re.IGNORECASE):
            continue
        if " ".join(line.split()).casefold() in definition_lines:
            continue
        if re.fullmatch(
            r"integrated_multimodal_description\s*:\s*", line,
            re.IGNORECASE,
        ):
            continue
        if parse_global_timeline_prompt(line)[1]:
            continue
        legacy_context = re.match(
            r"^(?:subject_definitions|cast|setting|location|environment|"
            r"visual_style|lighting)\s*:\s*(.*)$",
            line,
            re.IGNORECASE,
        )
        if not first_field and legacy_context:
            if legacy_context.group(1).strip():
                context_lines.append(legacy_context.group(1).strip())
            continue
        if first_field and re.match(
            r"^[A-Za-z][A-Za-z0-9_ ]{1,64}\s*:", line,
        ) and not re.match(
            r"^(?:VISUAL CONTINUITY|OPENING BLOCKING|FINAL BLOCKING)\s*:",
            line,
            re.IGNORECASE,
        ):
            raise ValueError(
                "Director H3 prompt has an unexpected field; exact mapping is unavailable"
            )
        context_lines.append(line)

    parsed_globals, parsed_events = parse_global_timeline_prompt(source)
    mapped_events = list(events if events is not None else parsed_events)
    # Native-shot partitioning can retain both the source timeline line and
    # the already structured Context-IR record for the same range. Prefer the
    # structured form when present so rehydration does not duplicate records.
    structured_events = [
        item for item in mapped_events
        if re.search(
            r"\bshot_name\s*:|\baudiovisual_description\s*:",
            str(item.get("text") or ""),
            re.IGNORECASE,
        )
    ]
    if structured_events:
        mapped_events = structured_events
    if not mapped_events:
        body = " ".join(context_lines).strip() or source
        mapped_events = [{
            "kind": "range",
            "start": 0.0,
            "end": duration,
            "text": body,
            "order": 0,
        }]
        context_lines = []
    else:
        event_texts = {
            re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
            for item in mapped_events
        }
        globals_from_parser = {
            re.sub(r"\s+", " ", str(line or "")).strip()
            for line in parsed_globals
        }
        context_lines = [
            line for line in context_lines
            if re.sub(r"\s+", " ", line).strip() not in event_texts
            and re.sub(r"\s+", " ", line).strip() not in globals_from_parser
        ] + [
            str(line).strip() for line in parsed_globals
            if str(line).strip()
            and " ".join(str(line).split()).casefold() not in definition_lines
            and not re.match(
                r"^(?:integrated_multimodal_description|overall_soundscape|"
                r"non_diegetic_music|subject_definitions|cast|setting|"
                r"location|environment|visual_style|lighting)\s*:",
                str(line).strip(),
                re.IGNORECASE,
            )
        ]

    ordered = sorted(mapped_events, key=lambda item: int(item.get("order", 0)))
    records: list[str] = []
    ranges: list[tuple[float, float]] = []
    for index, event in enumerate(ordered):
        start = float(event.get("start", 0.0))
        end = float(event.get("end", start))
        if event.get("kind") == "shot" or end <= start:
            end = (
                float(ordered[index + 1].get("start", duration))
                if index + 1 < len(ordered) else duration
            )
        if start < 0 or end <= start or end > duration + 0.01:
            raise ValueError(
                "Director H3 timeline cannot be mapped to exact positive ranges"
            )
        ranges.append((start, min(end, duration)))

    if (
        not ranges
        or abs(ranges[0][0]) > 1e-6
        or abs(ranges[-1][1] - duration) > 0.01
        or any(abs(left[1] - right[0]) > 1e-6 for left, right in zip(ranges, ranges[1:]))
    ):
        raise ValueError(
            "Director H3 timeline is not contiguous; exact mapping is unavailable"
        )

    opening = " ".join(
        line for line in context_lines
        if not re.match(r"^FINAL BLOCKING\s*:", line, re.IGNORECASE)
    ).strip()
    closing = " ".join(
        line for line in context_lines
        if re.match(r"^FINAL BLOCKING\s*:", line, re.IGNORECASE)
    ).strip()
    entity_definitions = _parse_h3_subject_definitions(subject_definitions)

    def strip_repeated_entity_definition(value: str) -> str:
        result = str(value or "")
        for entry in entity_definitions:
            description = " ".join(str(entry.get("description") or "").split())
            if len(description.split()) < 4:
                continue
            label = re.escape(str(entry.get("label") or ""))
            result = re.sub(
                rf"({label})\s*[:\-–—]?\s*{re.escape(description)}",
                r"\1",
                result,
                count=1,
                flags=re.IGNORECASE,
            )
        return result.strip()

    for index, (event, (start, end)) in enumerate(zip(ordered, ranges), start=1):
        name, description, vocals = _director_h3_record_payload(
            str(event.get("text") or ""), index,
        )
        description = strip_repeated_entity_definition(description)
        if index == 1 and opening:
            description = f"{opening} {description}".strip()
        if index == len(ordered) and closing:
            description = f"{description} {closing}".strip()
        if "|" in description or "|" in vocals:
            raise ValueError(
                "Director H3 record payload contains an ambiguous separator"
            )
        records.append(
            f"[Shot {index}] [{_director_h3_time_token(start)}s-"
            f"{_director_h3_time_token(end)}s] shot_name: {name} | "
            f"audiovisual_description: {description} | "
            f"dialogue_and_vocalizations: {vocals}"
        )

    result = (
        f"subject_definitions: {subject_definitions or _H3_NO_SUBJECT_DEFINITIONS}\n"
        "\nintegrated_multimodal_description:\n"
        + "\n".join(records)
        + f"\noverall_soundscape: {soundscape}"
        + f"\nnon_diegetic_music: {music}"
    )
    errors = validate_h3_context_ir_records(
        result, mode="t2va", duration_seconds=duration,
    )
    if errors:
        raise ValueError(
            "Director H3 canonical prompt validation failed: " + "; ".join(errors)
        )
    return result


def _director_h3_scene_prompt(
    plan: dict, *, frame_count: int, fps: float, mode: str | None = None,
) -> str:
    """Preserve one scene as canonical physical H3 records."""
    fps_value = float(fps)
    total = max(1, int(frame_count))
    duration = total / fps_value
    windows = [
        str(item.get("prompt", item.get("text", "")))
        if isinstance(item, dict) else str(item)
        for item in (plan.get("window_prompts") or [])
    ]
    windows = [item.strip() for item in windows if item.strip()]
    if not windows:
        raw_prompt = str(plan.get("video_prompt") or "").strip()
        if not raw_prompt:
            return ""
        return _director_h3_canonical_prompt(
            raw_prompt,
            duration_seconds=duration,
            mode=mode,
        )

    if str(mode or "").strip().casefold() == "ref2va":
        raise ValueError(
            "Director H3 Ref2VA window prompts cannot be merged without "
            "rewriting their six-field reference contract"
        )

    events: list[dict] = []
    cursor = 0
    for index, window in enumerate(windows):
        end = total if index == len(windows) - 1 else round(
            total * (index + 1) / len(windows)
        )
        events.append({
            "kind": "range",
            "start": cursor / fps_value,
            "end": end / fps_value,
            "text": window,
            "order": index,
        })
        cursor = end
    return _director_h3_canonical_prompt(
        "", duration_seconds=duration, events=events, mode=mode,
    )


def _director_h3_preferred_fl2va(params: dict, selected: str) -> str:
    """Preserve the caller-selected FL2VA flavor across adaptive routing."""
    requested = str(params.get("_h3_requested_checkpoint") or selected or "")
    if requested in _H3_FL2VA_MODELS:
        return requested
    return _H3_BASE_FL2VA_MODEL


def _director_h3_prompt_schema(prompt: str) -> str:
    """Return the authored H3 Context-IR schema without model inference."""

    return (
        "ref2va"
        if re.search(
            r"(?mi)^\s*(?:summary|retention_analysis|detailed_description)\s*:",
            str(prompt or ""),
        )
        else "base"
    )


def _director_h3_segment_models(
    params: dict,
    *,
    selected: str,
    boundaries: list[dict],
    segment_count: int,
    first_anchor,
    last_anchor,
    semantic_references: bool,
) -> list[dict]:
    """Mirror Studio's cut-aware FL2VA/Ref2VA routing for Director."""
    adaptive = params.get("h3_adaptive_conditioning", True) is not False
    fl2va_model = _director_h3_preferred_fl2va(params, selected)
    if not adaptive:
        if (
            selected == _H3_REF2VA_MODEL
            and (first_anchor or last_anchor)
            and params.get("h3_native_boundary_conditioning") is not True
        ):
            raise ValueError(
                "Manual Ref2VA cannot honor Director first/end-frame anchors. "
                "Enable adaptive H3 conditioning or select an FL2VA checkpoint."
            )
        models = [{
            "model_type": selected,
            "reason": "manual checkpoint override",
        } for _ in range(segment_count)]
    elif params.get("h3_native_boundary_conditioning") is True:
        from services.h3_boundary_policy import decide_h3_boundary

        models = []
        for index in range(segment_count):
            boundary = boundaries[index - 1] if index > 0 else {}
            models.append(decide_h3_boundary(
                segment_index=index,
                boundary_type=boundary.get("type"),
                semantic_references=semantic_references,
                preferred_fl2va_model=fl2va_model,
            ).as_dict())
    else:
        models = []
        semantic_run = semantic_references
        for index in range(segment_count):
            boundary = boundaries[index - 1] if index > 0 else {}
            boundary_type = str(boundary.get("type") or "continuous")
            if index == 0 and first_anchor:
                model_type = fl2va_model
                reason = "Director start-frame anchor"
            elif semantic_run:
                model_type = _H3_REF2VA_MODEL
                reason = (
                    "supplied semantic references"
                    if semantic_references else "semantic continuity after cut"
                )
            elif index > 0 and boundary_type in {"cut", "transition", "precut"}:
                semantic_run = True
                model_type = _H3_REF2VA_MODEL
                reason = "semantic continuity across Director scene boundary"
            else:
                model_type = fl2va_model
                reason = "hard frame continuity for continuous action"
            models.append({"model_type": model_type, "reason": reason})
        if last_anchor:
            models[-1] = {
                "model_type": fl2va_model,
                "reason": "supplied final-frame anchor",
            }

    overrides = params.get("h3_segment_overrides")
    if overrides is not None and not isinstance(overrides, list):
        raise ValueError("h3_segment_overrides must be a list")
    if isinstance(overrides, list):
        for index, override in enumerate(overrides[:segment_count]):
            if not isinstance(override, dict) or not override.get("model_type"):
                continue
            model_type = str(override["model_type"])
            if model_type not in _H3_VIDEO_MODELS:
                raise ValueError(f"Unknown H3 segment model override: {model_type}")
            if (
                params.get("h3_native_boundary_conditioning") is not True
                and model_type == _H3_REF2VA_MODEL
                and (
                (index == 0 and first_anchor)
                or (index == segment_count - 1 and last_anchor)
                )
            ):
                raise ValueError(
                    f"Director segment {index + 1} must use FL2VA to honor its frame anchor"
                )
            if (
                model_type in _H3_FL2VA_MODELS
                and semantic_references
                and not bool(override.get("drop_semantic_refs"))
            ):
                raise ValueError(
                    f"Director segment {index + 1} needs drop_semantic_refs=true "
                    "before FL2VA can replace supplied semantic references"
                )
            drop_semantic_refs = bool(override.get("drop_semantic_refs"))
            if params.get("h3_native_boundary_conditioning") is True:
                from services.h3_boundary_policy import decide_h3_boundary

                boundary = boundaries[index - 1] if index > 0 else {}
                preferred = (
                    model_type if model_type in _H3_FL2VA_MODELS else fl2va_model
                )
                decision = decide_h3_boundary(
                    segment_index=index,
                    boundary_type=boundary.get("type"),
                    semantic_references=semantic_references and not drop_semantic_refs,
                    preferred_fl2va_model=preferred,
                )
                if model_type != decision.model_type:
                    raise ValueError(
                        f"Director segment {index + 1} override conflicts with the native H3 boundary policy ({decision.model_type} required)"
                    )
                models[index] = decision.as_dict()
                models[index].update({
                    "reason": str(override.get("reason") or "user plan override"),
                    "drop_semantic_refs": drop_semantic_refs,
                    "user_override": True,
                })
            else:
                models[index] = {
                    "model_type": model_type,
                    "reason": str(override.get("reason") or "user plan override"),
                    "drop_semantic_refs": drop_semantic_refs,
                    "user_override": True,
                }

    for index, model in enumerate(models):
        model["index"] = index
        model["switch_from_previous"] = bool(
            index and models[index - 1]["model_type"] != model["model_type"]
        )
    return models


def _director_h3_edge_anchor(value, *, last: bool = False):
    if isinstance(value, (list, tuple)):
        present = [item for item in value if item]
        if not present:
            return None
        return present[-1] if last else present[0]
    return value or None


def _director_merge_h3_keyframe_refs(
    image_refs: list | tuple | None,
    per_clip_keyframes: list | tuple | None,
) -> list:
    """Merge Director H3 ref sources, de-duplicating exact path strings."""
    flattened = list(image_refs or ())
    flattened.extend(
        path
        for keyframes in (per_clip_keyframes or ())
        if isinstance(keyframes, list)
        for path in keyframes
        if path
    )
    combined = []
    seen_paths = set()
    for reference in flattened:
        if isinstance(reference, str):
            if reference in seen_paths:
                continue
            seen_paths.add(reference)
        combined.append(reference)
    return combined


def _normalize_director_h3_keyframe_refs(gen_params: dict) -> list:
    """Convert unsupported H3 KFI timing inputs to semantic references."""

    global_keyframe_refs = (
        list(gen_params.get("image_refs") or [])
        if gen_params.get("frames_positions") else []
    )
    per_clip_keyframe_refs = _director_merge_h3_keyframe_refs(
        [], gen_params.get("per_clip_keyframes"),
    )
    director_keyframe_refs = _director_merge_h3_keyframe_refs(
        global_keyframe_refs, [per_clip_keyframe_refs],
    )
    if not director_keyframe_refs:
        return []

    existing_refs = [] if global_keyframe_refs else list(
        gen_params.get("image_refs") or []
    )
    combined_refs = _director_merge_h3_keyframe_refs(
        existing_refs, [director_keyframe_refs],
    )
    if len(combined_refs) > 9:
        raise ValueError(
            "Director H3 keyframes exceed Ref2VA's nine-image semantic "
            "reference limit"
        )
    gen_params["image_refs"] = combined_refs
    gen_params.pop("frames_positions", None)
    gen_params.pop("per_clip_keyframes", None)
    gen_params["video_prompt_type"] = str(
        gen_params.get("video_prompt_type") or ""
    ).replace("KFI", "")
    custom = dict(gen_params.get("custom_settings") or {})
    custom["h3_director_keyframes"] = "semantic_references"
    gen_params["custom_settings"] = custom
    return director_keyframe_refs


def _canonicalize_director_h3_v2_shot_plan(
    shot_plan: dict,
    *,
    prompts: list[str],
    published: list[int],
    fps: float,
    compile_workflow,
) -> list[str]:
    """Validate and canonicalize sealed segment-local H3 prompt contracts."""
    from services.h3_shot_planner import (
        _compile_semantic_prompt,
        _compile_segment_local_prompts,
        _authored_opening_contains,
        _authored_opening_payload,
        _canonical_context_ir_parts,
        _extract_final_blocking,
        _semantic_dialogue_identity,
        _strip_dialogue_occurrence_tokens,
        _tag_dialogue_occurrences,
        _validate_dialogue_spans,
        validate_h3_shot_plan_seal,
    )

    contracts = shot_plan.get("source_contracts")
    semantic_shots = shot_plan.get("semantic_shots")
    shots = shot_plan.get("shots")
    boundaries = shot_plan.get("clip_boundaries")
    clip_frames = shot_plan.get("clip_frames")
    clip_published = shot_plan.get("clip_published_frames")
    clip_trims = shot_plan.get("clip_trim_tail_frames")
    if not isinstance(contracts, list) or not contracts:
        raise ValueError("Saved Director H3 semantic shots are incomplete")
    if not isinstance(semantic_shots, list) or semantic_shots != contracts:
        raise ValueError("Saved Director H3 semantic shot copies disagree")
    if not isinstance(shots, list) or len(shots) != len(prompts):
        raise ValueError("Saved Director H3 shot records are incomplete")
    if not all(isinstance(shot, dict) for shot in shots):
        raise ValueError("Saved Director H3 shot record is invalid")
    if not isinstance(boundaries, list) or len(boundaries) != len(prompts) - 1:
        raise ValueError("Saved Director H3 continuity metadata is incomplete")
    if not all(isinstance(value, list) for value in (
        clip_frames, clip_published, clip_trims,
    )) or not (
        len(clip_frames) == len(clip_published) == len(clip_trims) == len(prompts)
        and [int(value) for value in clip_published] == [
            int(value) for value in published
        ]
    ):
        raise ValueError("Saved Director H3 physical geometry is incomplete")

    canonical = [""] * len(prompts)
    covered: set[int] = set()
    mapping: dict[int, tuple[dict, int, dict, str, str]] = {}
    authored_ids: set[str] = set()
    nested_events: list[dict] = []
    expected_dialogue_ordinals: list[tuple[int, int]] = []
    for expected_source_index, contract in enumerate(contracts):
        if not isinstance(contract, dict):
            raise ValueError("Saved Director H3 semantic shot is invalid")
        positions = contract.get("segment_indices")
        semantic_prompt = contract.get("semantic_prompt")
        authored_prompt = contract.get("authored_prompt")
        source_index = contract.get("source_index")
        semantic_index = contract.get("semantic_shot_index")
        authored_shot_id = contract.get("authored_shot_id")
        if any(field in contract for field in (
            "semantic_dialogue_provenance",
            "semantic_dialogue_provenance_sha256",
        )):
            raise ValueError(
                "Saved Director H3 obsolete dialogue provenance is unsupported"
            )
        if (
            not isinstance(positions, list)
            or not positions
            or not isinstance(semantic_prompt, str)
            or not semantic_prompt.strip()
            or not isinstance(authored_prompt, str)
            or isinstance(source_index, bool)
            or not isinstance(source_index, int)
            or source_index != expected_source_index
            or isinstance(semantic_index, bool)
            or not isinstance(semantic_index, int)
            or semantic_index != expected_source_index
            or not isinstance(authored_shot_id, str)
            or not authored_shot_id.strip()
            or authored_shot_id in authored_ids
            or contract.get("prompt_rewrite_for_physical_split") is not True
            or contract.get("physical_prompt_compiler_version") != 2
        ):
            raise ValueError("Saved Director H3 semantic shot is incomplete")
        _validate_dialogue_spans(semantic_prompt)
        visual_context = contract.get("visual_context")
        opening_blocking = contract.get("opening_blocking")
        final_blocking = contract.get("final_blocking")
        structured_dialogue_blocks = contract.get(
            "structured_dialogue_blocks"
        )
        if (
            not isinstance(visual_context, str)
            or not isinstance(opening_blocking, str)
            or not isinstance(final_blocking, str)
            or not isinstance(structured_dialogue_blocks, list)
            or not all(
                isinstance(block, str) for block in structured_dialogue_blocks
            )
        ):
            raise ValueError(
                "Saved Director H3 semantic compiler inputs are incomplete"
            )
        rebuilt_semantic_prompt, _rebuilt_dialogue = _compile_semantic_prompt(
            authored_prompt,
            visual_context=visual_context,
            opening_blocking=opening_blocking,
            final_blocking=final_blocking,
            structured_dialogue_blocks=structured_dialogue_blocks,
        )
        if rebuilt_semantic_prompt != semantic_prompt:
            raise ValueError(
                "Saved Director H3 semantic prompt provenance disagrees"
            )
        if visual_context:
            without_visual, _ = _compile_semantic_prompt(
                authored_prompt,
                visual_context="",
                opening_blocking=opening_blocking,
                final_blocking=final_blocking,
                structured_dialogue_blocks=structured_dialogue_blocks,
            )
            if without_visual == semantic_prompt:
                raise ValueError(
                    "Saved Director H3 semantic compiler inputs are not canonical"
                )
        source_is_canonical = (
            _canonical_context_ir_parts(authored_prompt) is not None
        )
        if final_blocking and not source_is_canonical:
            without_final, _ = _compile_semantic_prompt(
                authored_prompt,
                visual_context=visual_context,
                opening_blocking=opening_blocking,
                final_blocking="",
                structured_dialogue_blocks=structured_dialogue_blocks,
            )
            if without_final == semantic_prompt:
                raise ValueError(
                    "Saved Director H3 semantic compiler inputs are not canonical"
                )
        if opening_blocking:
            without_opening, _ = _compile_semantic_prompt(
                authored_prompt,
                visual_context=visual_context,
                opening_blocking="",
                final_blocking=final_blocking,
                structured_dialogue_blocks=structured_dialogue_blocks,
            )
            if without_opening == semantic_prompt:
                authored_opening = _authored_opening_payload(authored_prompt)
                if (
                    not _authored_opening_contains(
                        authored_prompt, opening_blocking,
                    )
                    or opening_blocking != authored_opening
                ):
                    raise ValueError(
                        "Saved Director H3 semantic compiler inputs are not canonical"
                    )
        for block_index in range(len(structured_dialogue_blocks)):
            candidate_blocks = [
                block
                for index, block in enumerate(structured_dialogue_blocks)
                if index != block_index
            ]
            without_block, _ = _compile_semantic_prompt(
                authored_prompt,
                visual_context=visual_context,
                opening_blocking=opening_blocking,
                final_blocking=final_blocking,
                structured_dialogue_blocks=candidate_blocks,
            )
            if without_block == semantic_prompt:
                raise ValueError(
                    "Saved Director H3 semantic compiler inputs are not canonical"
                )
        prompt_changed = contract.get("prompt_changed_before_split")
        authored_final_blocking = contract.get("authored_final_blocking")
        if (
            type(prompt_changed) is not bool
            or prompt_changed != (semantic_prompt != authored_prompt)
            or not isinstance(authored_final_blocking, str)
            or authored_final_blocking
                != _extract_final_blocking(authored_prompt)[1]
        ):
            raise ValueError(
                "Saved Director H3 authored prompt provenance disagrees"
            )
        authored_ids.add(authored_shot_id)
        normalized_positions: list[int] = []
        for value in positions:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("Saved Director H3 segment mapping is invalid")
            if value < 0 or value >= len(prompts) or value in covered:
                raise ValueError("Saved Director H3 segment mapping is incomplete")
            normalized_positions.append(value)
            covered.add(value)
        if normalized_positions != list(range(
            normalized_positions[0], normalized_positions[-1] + 1,
        )):
            raise ValueError("Saved Director H3 semantic segments are not contiguous")

        reference_labels = list(dict.fromkeys(re.findall(
            r"<(?:Subject|Picture|Video|Audio)\s+[1-9]\d*>",
            semantic_prompt,
            flags=re.IGNORECASE,
        )))
        if contract.get("reference_labels") != reference_labels:
            raise ValueError("Saved Director H3 semantic reference mapping disagrees")
        slices = contract.get("execution_slices")
        digests = contract.get("executable_prompt_sha256")
        if (
            not isinstance(slices, list)
            or len(slices) != len(normalized_positions)
            or not isinstance(digests, list)
            or len(digests) != len(normalized_positions)
        ):
            raise ValueError("Saved Director H3 execution slices are incomplete")
        local_cursor = 0
        for local_index, position in enumerate(normalized_positions):
            end_cursor = local_cursor + int(published[position])
            expected_slice = {
                "segment_index": position,
                "physical_segment_index": local_index,
                "start_frame": local_cursor,
                "end_frame_exclusive": end_cursor,
                "start_seconds": local_cursor / fps,
                "end_seconds": end_cursor / fps,
            }
            if slices[local_index] != expected_slice:
                raise ValueError("Saved Director H3 execution slice geometry disagrees")
            original_prompt = str(prompts[position])
            if digests[local_index] != hashlib.sha256(
                original_prompt.encode("utf-8")
            ).hexdigest():
                raise ValueError("Saved Director H3 physical prompt bytes disagree")
            physical_id = f"{authored_shot_id}:segment-{local_index + 1}"
            mapping[position] = (
                contract, local_index, expected_slice, physical_id,
                original_prompt,
            )
            mode = (
                "ref2va"
                if _director_h3_prompt_schema(original_prompt) == "ref2va"
                else "t2va"
            )
            canonical[position] = compile_workflow(
                _director_h3_canonical_prompt(
                    original_prompt,
                    duration_seconds=int(published[position]) / fps,
                    mode=mode,
                )
            )
            local_cursor = end_cursor
        contract_events = contract.get("event_ownership")
        if not isinstance(contract_events, list):
            raise ValueError("Saved Director H3 event ownership is incomplete")
        nested_manifest = contract.get("dialogue_manifest")
        if not isinstance(nested_manifest, list) or not all(
            isinstance(item, dict) for item in nested_manifest
        ):
            raise ValueError("Saved Director H3 semantic dialogue is incomplete")
        if any(
            isinstance(item.get("semantic_occurrence_index"), bool)
            or not isinstance(item.get("semantic_occurrence_index"), int)
            for item in nested_manifest
        ):
            raise ValueError("Saved Director H3 semantic dialogue identity disagrees")
        semantic_manifest = sorted(
            nested_manifest,
            key=lambda item: item.get("semantic_occurrence_index", -1),
        )
        if [
            item.get("semantic_occurrence_index") for item in semantic_manifest
        ] != list(range(len(semantic_manifest))):
            raise ValueError("Saved Director H3 semantic dialogue identity disagrees")
        semantic_blocks = [
            match.group(0) for match in re.finditer(
                r"<d>.*?</d>", semantic_prompt,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        if len(semantic_manifest) != len(semantic_blocks):
            raise ValueError("Saved Director H3 semantic dialogue identity disagrees")
        for ordinal, (item, block) in enumerate(zip(
            semantic_manifest, semantic_blocks,
        )):
            expected_identity = _semantic_dialogue_identity(
                block,
                source_index=source_index,
                semantic_occurrence_index=ordinal,
            )
            if (
                isinstance(item.get("source_index"), bool)
                or not isinstance(item.get("source_index"), int)
                or any(
                    item.get(field) != value
                    for field, value in expected_identity.items()
                )
            ):
                raise ValueError(
                    "Saved Director H3 semantic dialogue provenance disagrees"
                )
        localized_semantic_prompt, dialogue_tokens = _tag_dialogue_occurrences(
            semantic_prompt, semantic_manifest,
        )
        _expected_prompts, expected_events = _compile_segment_local_prompts(
            localized_semantic_prompt,
            segment_positions=normalized_positions,
            published_frames=published,
            source_index=source_index,
            fps=fps,
            final_blocking=(
                str(contract.get("final_blocking") or "")
                if source_is_canonical else ""
            ),
            opening_blocking=(
                str(contract.get("opening_blocking") or "")
                if source_is_canonical else ""
            ),
            dialogue_occurrence_tokens=dialogue_tokens,
        )
        localized_ordinals: set[int] = set()
        for local_index, expected_prompt in enumerate(_expected_prompts):
            position = normalized_positions[local_index]
            for match in re.finditer(
                r"<d>.*?</d>", expected_prompt,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                ordinal_matches = [
                    ordinal for ordinal, token in enumerate(dialogue_tokens)
                    if token in match.group(0)
                ]
                if len(ordinal_matches) != 1:
                    raise ValueError(
                        "Saved Director H3 dialogue occurrence identity disagrees"
                    )
                ordinal = ordinal_matches[0]
                if ordinal in localized_ordinals:
                    raise ValueError(
                        "Saved Director H3 dialogue occurrence identity disagrees"
                    )
                localized_ordinals.add(ordinal)
                expected_dialogue_ordinals.append((position, ordinal))
        if localized_ordinals != set(range(len(semantic_manifest))):
            raise ValueError("Saved Director H3 dialogue occurrence identity disagrees")
        _expected_prompts = [
            _strip_dialogue_occurrence_tokens(prompt, dialogue_tokens)
            for prompt in _expected_prompts
        ]
        for expected_event in expected_events:
            expected_event["executable_payload"] = (
                _strip_dialogue_occurrence_tokens(
                    str(expected_event.get("executable_payload") or ""),
                    dialogue_tokens,
                )
            )
        for local_index, position in enumerate(normalized_positions):
            expected_prompt = _expected_prompts[local_index]
            expected_mode = (
                "ref2va"
                if _director_h3_prompt_schema(expected_prompt) == "ref2va"
                else "t2va"
            )
            expected_canonical = compile_workflow(
                _director_h3_canonical_prompt(
                    expected_prompt,
                    duration_seconds=int(published[position]) / fps,
                    mode=expected_mode,
                )
            )
            if canonical[position] != expected_canonical:
                raise ValueError(
                    "Saved Director H3 physical prompt semantics disagree"
                )
        if len(contract_events) != len(expected_events):
            raise ValueError("Saved Director H3 event ownership coverage disagrees")
        source_published_offset = sum(
            int(value) for value in published[:normalized_positions[0]]
        )
        for event_index, (event, expected_event) in enumerate(zip(
            contract_events, expected_events,
        )):
            if not isinstance(event, dict):
                raise ValueError("Saved Director H3 event ownership is invalid")
            expected_event = dict(expected_event)
            expected_event["continuation_slices"] = [
                {
                    **dict(continuation),
                    "physical_segment_id": (
                        f"{authored_shot_id}:segment-"
                        f"{int(continuation['physical_segment_index']) + 1}"
                    ),
                    "published_start_frame": (
                        source_published_offset
                        + int(continuation["source_start_frame"])
                    ),
                    "published_end_frame_exclusive": (
                        source_published_offset
                        + int(continuation["source_end_frame_exclusive"])
                    ),
                }
                for continuation in expected_event.get("continuation_slices") or []
            ]
            expected_event.update({
                "event_id": f"{authored_shot_id}:event-{event_index + 1}",
                "authored_shot_id": authored_shot_id,
                "semantic_shot_index": source_index,
                "owner_physical_segment_id": (
                    f"{authored_shot_id}:segment-"
                    f"{int(expected_event['owner_physical_segment_index']) + 1}"
                ),
                "published_start_frame": (
                    source_published_offset
                    + int(expected_event["source_start_frame"])
                    if expected_event.get("source_start_frame") is not None
                    else None
                ),
                "published_end_frame_exclusive": (
                    source_published_offset
                    + int(expected_event["source_end_frame_exclusive"])
                    if expected_event.get("source_end_frame_exclusive") is not None
                    else None
                ),
            })
            if any(
                event.get(field) != value
                for field, value in expected_event.items()
            ):
                raise ValueError("Saved Director H3 event ownership disagrees")
            owner = event.get("owner_segment_index")
            local_owner = event.get("owner_physical_segment_index")
            if (
                isinstance(owner, bool)
                or not isinstance(owner, int)
                or owner not in normalized_positions
                or isinstance(local_owner, bool)
                or not isinstance(local_owner, int)
                or local_owner < 0
                or local_owner >= len(normalized_positions)
                or normalized_positions[local_owner] != owner
                or event.get("owner_physical_segment_id")
                    != f"{authored_shot_id}:segment-{local_owner + 1}"
                or event.get("authored_shot_id") != authored_shot_id
                or event.get("semantic_shot_index") != expected_source_index
            ):
                raise ValueError("Saved Director H3 event ownership disagrees")
            owner_slice = slices[local_owner]
            executable_payload = event.get("executable_payload")
            if (
                not isinstance(executable_payload, str)
                or not executable_payload.strip()
                or executable_payload not in prompts[owner]
            ):
                raise ValueError(
                    "Saved Director H3 event payload and owner prompt disagree"
                )
            source_start = event.get("source_start_frame")
            source_end = event.get("source_end_frame_exclusive")
            if source_start is None or source_end is None:
                if any(event.get(field) is not None for field in (
                    "local_start_frame", "local_end_frame_exclusive",
                    "published_start_frame", "published_end_frame_exclusive",
                )):
                    raise ValueError("Saved Director H3 event frame ownership disagrees")
            elif (
                isinstance(source_start, bool)
                or not isinstance(source_start, int)
                or isinstance(source_end, bool)
                or not isinstance(source_end, int)
                or not (
                    int(owner_slice["start_frame"])
                    <= source_start
                    < int(owner_slice["end_frame_exclusive"])
                )
                or source_end <= source_start
                or event.get("local_start_frame")
                    != source_start - int(owner_slice["start_frame"])
                or event.get("local_end_frame_exclusive")
                    != min(source_end, int(owner_slice["end_frame_exclusive"]))
                    - int(owner_slice["start_frame"])
                or event.get("published_start_frame")
                    != sum(int(value) for value in published[:normalized_positions[0]])
                    + source_start
                or event.get("published_end_frame_exclusive")
                    != sum(int(value) for value in published[:normalized_positions[0]])
                    + source_end
            ):
                raise ValueError("Saved Director H3 event frame ownership disagrees")
        nested_events.extend(contract_events)

    if covered != set(range(len(prompts))) or any(not item for item in canonical):
        raise ValueError("Saved Director H3 segment mapping is incomplete")
    if shot_plan.get("event_ownership") != nested_events:
        raise ValueError("Saved Director H3 event ownership copies disagree")

    manifest = shot_plan.get("dialogue_manifest")
    if not isinstance(manifest, list):
        raise ValueError("Saved Director H3 dialogue manifest is incomplete")
    expected_dialogue = [
        (position, match.group(0))
        for position, prompt in enumerate(prompts)
        for match in re.finditer(
            r"<d>.*?</d>", prompt, flags=re.IGNORECASE | re.DOTALL,
        )
    ]
    if (
        len(manifest) != len(expected_dialogue)
        or len(manifest) != len(expected_dialogue_ordinals)
    ):
        raise ValueError("Saved Director H3 dialogue manifest coverage disagrees")
    for item, (position, block), (ordinal_position, ordinal) in zip(
        manifest, expected_dialogue, expected_dialogue_ordinals,
    ):
        contract = mapping[position][0]
        expected_identity = _semantic_dialogue_identity(
            block,
            source_index=contract["source_index"],
            semantic_occurrence_index=ordinal,
        )
        if (
            not isinstance(item, dict)
            or ordinal_position != position
            or isinstance(item.get("semantic_occurrence_index"), bool)
            or not isinstance(item.get("semantic_occurrence_index"), int)
            or isinstance(item.get("source_index"), bool)
            or not isinstance(item.get("source_index"), int)
            or any(
                item.get(field) != value
                for field, value in expected_identity.items()
            )
            or not isinstance(item.get("authored_shot_id"), str)
            or item.get("authored_shot_id") != contract["authored_shot_id"]
            or isinstance(item.get("semantic_shot_index"), bool)
            or not isinstance(item.get("semantic_shot_index"), int)
            or item.get("semantic_shot_index") != contract["semantic_shot_index"]
            or isinstance(item.get("segment_index"), bool)
            or not isinstance(item.get("segment_index"), int)
            or item.get("segment_index") != position
        ):
            raise ValueError("Saved Director H3 dialogue association disagrees")
    for contract in contracts:
        nested_manifest = contract.get("dialogue_manifest")
        expected_nested = [
            dict(item) for item in manifest
            if item["source_index"] == contract["source_index"]
        ]
        if nested_manifest != expected_nested:
            raise ValueError("Saved Director H3 semantic dialogue association disagrees")

    generated_cursor = 0
    published_cursor = 0
    for index, shot in enumerate(shots):
        contract, local_index, expected_slice, physical_id, original_prompt = mapping[index]
        expected_boundary = boundaries[index - 1] if index else None
        expected_values = {
            "index": index,
            "source_index": contract["source_index"],
            "authored_shot_id": contract["authored_shot_id"],
            "semantic_shot_index": contract["semantic_shot_index"],
            "physical_segment_id": physical_id,
            "physical_segment_index": local_index,
            "physical_segment_count": len(contract["segment_indices"]),
            "predecessor_segment_index": index - 1 if index else None,
            "predecessor_physical_segment_id": mapping[index - 1][3] if index else None,
            "predecessor_authored_shot_id": (
                mapping[index - 1][0]["authored_shot_id"] if index else None
            ),
            "execution_cursor_frame": expected_slice["start_frame"],
            "execution_slice": expected_slice,
            "frames": int(clip_frames[index]),
            "start_frame": generated_cursor,
            "end_frame": generated_cursor + int(clip_frames[index]) - 1,
            "published_frames": int(clip_published[index]),
            "published_start_frame": published_cursor,
            "published_end_frame": published_cursor + int(clip_published[index]) - 1,
            "published_end_frame_exclusive": (
                published_cursor + int(clip_published[index])
            ),
            "trim_tail_frames": int(clip_trims[index]),
            "continuity_mode": (
                str(expected_boundary.get("continuity_mode") or "")
                if isinstance(expected_boundary, dict) else "independent"
            ),
            "boundary_before": expected_boundary,
            "prompt": original_prompt,
            "dialogue_manifest_indices": [
                manifest_index
                for manifest_index, item in enumerate(manifest)
                if item["segment_index"] == index
            ],
        }
        if any(shot.get(key) != value for key, value in expected_values.items()):
            raise ValueError("Saved Director H3 physical segment metadata disagrees")
        generated_cursor += int(clip_frames[index])
        published_cursor += int(clip_published[index])

    try:
        validate_h3_shot_plan_seal(shot_plan)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    for contract in contracts:
        contract["executable_prompt_sha256"] = [
            hashlib.sha256(canonical[position].encode("utf-8")).hexdigest()
            for position in contract["segment_indices"]
        ]
    for index, prompt in enumerate(canonical):
        shots[index]["prompt"] = prompt
    shot_plan["clip_prompts"] = canonical
    shot_plan["semantic_shots"] = contracts
    return canonical


def _canonicalize_director_h3_shot_plan(
    shot_plan: dict,
    *,
    published_frames: list[int] | None = None,
    h3_style_workflow: dict | None = None,
) -> list[str]:
    """Canonicalize and validate every persistent Director child prompt."""
    from services.h3_upstream_skills import (
        compile_h3_style_workflow,
        validate_resolved_h3_style_workflow,
    )

    saved_workflow = shot_plan.get("h3_style_workflow")
    if h3_style_workflow is None:
        workflow = validate_resolved_h3_style_workflow(saved_workflow)
    else:
        workflow = validate_resolved_h3_style_workflow(h3_style_workflow)
        if saved_workflow is not None and saved_workflow != workflow:
            raise ValueError("Saved Director H3 style workflow disagrees")

    def compile_workflow(prompt: str) -> str:
        compiled, _schema = compile_h3_style_workflow(prompt, workflow)
        return compiled

    prompts = list(shot_plan.get("clip_prompts") or [])
    published = list(
        published_frames
        if published_frames is not None
        else (shot_plan.get("clip_published_frames") or [])
    )
    try:
        fps = float(shot_plan.get("fps") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Saved Director H3 shot-plan FPS is invalid") from exc
    if not prompts or fps <= 0 or len(prompts) != len(published):
        raise ValueError("Saved Director H3 child prompts are incomplete")
    shots = shot_plan.get("shots")
    if not isinstance(shots, list) or len(shots) != len(prompts):
        raise ValueError("Saved Director H3 shot records are incomplete")
    if not all(isinstance(shot, dict) for shot in shots):
        raise ValueError("Saved Director H3 shot record is invalid")

    raw_contract_version = shot_plan.get("semantic_physical_contract_version")
    if raw_contract_version is not None and (
        isinstance(raw_contract_version, bool)
        or not isinstance(raw_contract_version, int)
        or raw_contract_version not in {0, 1, 2}
    ):
        raise ValueError(
            "Saved Director H3 semantic/physical contract version is unsupported"
        )
    semantic_contract = int(raw_contract_version or 0)
    if semantic_contract == 2:
        canonical = _canonicalize_director_h3_v2_shot_plan(
            shot_plan,
            prompts=prompts,
            published=published,
            fps=fps,
            compile_workflow=compile_workflow,
        )
        if workflow is not None:
            shot_plan["h3_style_workflow"] = dict(workflow)
        from services.h3_shot_planner import seal_h3_shot_plan

        seal_h3_shot_plan(shot_plan)
        return canonical
    if semantic_contract == 1:
        contracts = shot_plan.get("source_contracts")
        if not isinstance(contracts, list) or not contracts:
            raise ValueError("Saved Director H3 semantic shots are incomplete")
        semantic_shots = shot_plan.get("semantic_shots")
        if not isinstance(semantic_shots, list) or semantic_shots != contracts:
            raise ValueError("Saved Director H3 semantic shot copies disagree")
        boundaries = shot_plan.get("clip_boundaries")
        if not isinstance(boundaries, list) or len(boundaries) != len(prompts) - 1:
            raise ValueError("Saved Director H3 continuity metadata is incomplete")
        clip_frames = shot_plan.get("clip_frames")
        clip_published = shot_plan.get("clip_published_frames")
        clip_trims = shot_plan.get("clip_trim_tail_frames")
        if not all(isinstance(value, list) for value in (
            clip_frames, clip_published, clip_trims,
        )) or not (
            len(clip_frames) == len(clip_published) == len(clip_trims) == len(prompts)
            and [int(value) for value in clip_published] == [
                int(value) for value in published
            ]
        ):
            raise ValueError("Saved Director H3 physical geometry is incomplete")

        canonical = [""] * len(prompts)
        covered: set[int] = set()
        mapping: dict[int, tuple[dict, int, dict, str, str]] = {}
        authored_ids: set[str] = set()
        for expected_source_index, contract in enumerate(contracts):
            if not isinstance(contract, dict):
                raise ValueError("Saved Director H3 semantic shot is invalid")
            positions = contract.get("segment_indices")
            semantic_prompt = contract.get("semantic_prompt")
            source_index = contract.get("source_index")
            semantic_index = contract.get("semantic_shot_index")
            authored_shot_id = contract.get("authored_shot_id")
            if (
                not isinstance(positions, list)
                or not positions
                or not isinstance(semantic_prompt, str)
                or not semantic_prompt.strip()
                or isinstance(source_index, bool)
                or not isinstance(source_index, int)
                or source_index != expected_source_index
                or semantic_index != expected_source_index
                or not isinstance(authored_shot_id, str)
                or not authored_shot_id.strip()
                or authored_shot_id in authored_ids
                or contract.get("prompt_rewrite_for_physical_split") is not False
            ):
                raise ValueError("Saved Director H3 semantic shot is incomplete")
            authored_ids.add(authored_shot_id)
            normalized_positions: list[int] = []
            for value in positions:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("Saved Director H3 segment mapping is invalid")
                position = value
                if (
                    position < 0
                    or position >= len(prompts)
                    or position in covered
                ):
                    raise ValueError(
                        "Saved Director H3 segment mapping is incomplete"
                    )
                normalized_positions.append(position)
                covered.add(position)
            if normalized_positions != list(range(
                normalized_positions[0], normalized_positions[-1] + 1,
            )):
                raise ValueError(
                    "Saved Director H3 semantic segments are not contiguous"
                )
            if any(
                prompts[position] != semantic_prompt
                for position in normalized_positions
            ):
                raise ValueError(
                    "Saved Director H3 physical prompt bytes disagree with "
                    "their semantic shot"
                )
            reference_labels = list(dict.fromkeys(re.findall(
                r"<(?:Subject|Picture|Video|Audio)\s+[1-9]\d*>",
                semantic_prompt,
                flags=re.IGNORECASE,
            )))
            if contract.get("reference_labels") != reference_labels:
                raise ValueError(
                    "Saved Director H3 semantic reference mapping disagrees"
                )
            slices = contract.get("execution_slices")
            if not isinstance(slices, list) or len(slices) != len(normalized_positions):
                raise ValueError("Saved Director H3 execution slices are incomplete")
            local_cursor = 0
            for local_index, position in enumerate(normalized_positions):
                end_cursor = local_cursor + int(published[position])
                expected_slice = {
                    "segment_index": position,
                    "physical_segment_index": local_index,
                    "start_frame": local_cursor,
                    "end_frame_exclusive": end_cursor,
                    "start_seconds": local_cursor / fps,
                    "end_seconds": end_cursor / fps,
                }
                if slices[local_index] != expected_slice:
                    raise ValueError(
                        "Saved Director H3 execution slice geometry disagrees"
                    )
                physical_id = f"{authored_shot_id}:segment-{local_index + 1}"
                mapping[position] = (
                    contract, local_index, expected_slice, physical_id,
                    semantic_prompt,
                )
                local_cursor = end_cursor
            contract["execution_slices"] = [
                dict(mapping[position][2]) for position in normalized_positions
            ]
            semantic_duration = sum(
                int(published[position]) for position in normalized_positions
            ) / fps
            compiled = compile_workflow(_director_h3_canonical_prompt(
                semantic_prompt,
                duration_seconds=semantic_duration,
            ))
            contract["semantic_prompt"] = compiled
            contract["prompt_rewrite_for_physical_split"] = False
            for position in normalized_positions:
                canonical[position] = compiled
        if covered != set(range(len(prompts))) or any(not item for item in canonical):
            raise ValueError("Saved Director H3 segment mapping is incomplete")

        manifest = shot_plan.get("dialogue_manifest")
        if not isinstance(manifest, list):
            raise ValueError("Saved Director H3 dialogue manifest is incomplete")
        expected_dialogue: list[tuple[dict, str]] = []
        for contract in contracts:
            expected_dialogue.extend(
                (contract, match.group(0))
                for match in re.finditer(
                    r"<d>.*?</d>",
                    contract["semantic_prompt"],
                    flags=re.IGNORECASE | re.DOTALL,
                )
            )
        if len(manifest) != len(expected_dialogue):
            raise ValueError(
                "Saved Director H3 dialogue manifest coverage disagrees"
            )
        for item, expected in zip(manifest, expected_dialogue):
            contract, block = expected
            if (
                not isinstance(item, dict)
                or item.get("exact_block") != block
                or item.get("source_index") != contract["source_index"]
                or item.get("authored_shot_id") != contract["authored_shot_id"]
                or item.get("semantic_shot_index") != contract["semantic_shot_index"]
                or item.get("segment_index") != contract["segment_indices"][0]
            ):
                raise ValueError(
                    "Saved Director H3 dialogue association disagrees"
                )
        for contract in contracts:
            nested_manifest = contract.get("dialogue_manifest")
            expected_nested = [
                dict(item) for item in manifest
                if item["source_index"] == contract["source_index"]
            ]
            if nested_manifest != expected_nested:
                raise ValueError(
                    "Saved Director H3 semantic dialogue association disagrees"
                )

        generated_cursor = 0
        published_cursor = 0
        for index, shot in enumerate(shots):
            contract, local_index, expected_slice, physical_id, original_prompt = (
                mapping[index]
            )
            expected_predecessor = index - 1 if index else None
            expected_previous_physical = (
                mapping[index - 1][3] if index else None
            )
            expected_previous_authored = (
                mapping[index - 1][0]["authored_shot_id"] if index else None
            )
            expected_boundary = boundaries[index - 1] if index else None
            expected_continuity = (
                str(expected_boundary.get("continuity_mode") or "")
                if isinstance(expected_boundary, dict) else "independent"
            )
            expected_values = {
                "index": index,
                "source_index": contract["source_index"],
                "authored_shot_id": contract["authored_shot_id"],
                "semantic_shot_index": contract["semantic_shot_index"],
                "physical_segment_id": physical_id,
                "physical_segment_index": local_index,
                "physical_segment_count": len(contract["segment_indices"]),
                "predecessor_segment_index": expected_predecessor,
                "predecessor_physical_segment_id": expected_previous_physical,
                "predecessor_authored_shot_id": expected_previous_authored,
                "execution_cursor_frame": expected_slice["start_frame"],
                "execution_slice": expected_slice,
                "frames": int(clip_frames[index]),
                "start_frame": generated_cursor,
                "end_frame": generated_cursor + int(clip_frames[index]) - 1,
                "published_frames": int(clip_published[index]),
                "published_start_frame": published_cursor,
                "published_end_frame": (
                    published_cursor + int(clip_published[index]) - 1
                ),
                "trim_tail_frames": int(clip_trims[index]),
                "continuity_mode": expected_continuity,
                "boundary_before": expected_boundary,
                "prompt": original_prompt,
                "dialogue_manifest_indices": [
                    manifest_index
                    for manifest_index, item in enumerate(manifest)
                    if item["segment_index"] == index
                ],
            }
            if any(shot.get(key) != value for key, value in expected_values.items()):
                raise ValueError(
                    "Saved Director H3 physical segment metadata disagrees"
                )
            for key, value in expected_values.items():
                shot[key] = dict(value) if key == "execution_slice" else value
            generated_cursor += int(clip_frames[index])
            published_cursor += int(clip_published[index])

        shot_plan["semantic_shots"] = contracts
    else:
        # Compatibility for committed v1 plans whose child prompts were
        # physically rebased before the semantic/physical contract existed.
        canonical = [
            compile_workflow(_director_h3_canonical_prompt(
                prompt,
                duration_seconds=int(frames) / fps,
            ))
            for prompt, frames in zip(prompts, published)
        ]
    if workflow is not None:
        shot_plan["h3_style_workflow"] = dict(workflow)
    shot_plan["clip_prompts"] = canonical
    for index, prompt in enumerate(canonical):
        shots[index]["prompt"] = prompt
    return canonical


_DIRECTOR_H3_RUNTIME_CONTRACT_FIELDS = (
    "model_type",
    "requested_frames",
    "planned_frames",
    "published_frames",
    "final_trim_frames",
    "clip_count",
    "clip_frames",
    "clip_published_frames",
    "clip_trim_tail_frames",
    "segment_frames_maximum",
    "manual_segment_ceiling",
    "continuation",
    "clip_boundaries",
    "segment_policy",
    "segment_models",
    "segment_source_indices",
    "director_keyframe_conditioning",
    "adaptive_conditioning",
    "native_boundary_conditioning",
    "preserve_generated_audio",
    "global_prompt",
    "original_image_start",
    "original_image_end",
)


def _bind_director_h3_runtime_contract(plan: dict) -> None:
    """Seal every outer field that can change a v2 replay invocation."""

    import copy

    shot_plan = plan.get("shot_plan")
    if (
        not isinstance(shot_plan, dict)
        or shot_plan.get("semantic_physical_contract_version") != 2
    ):
        return
    shot_plan["director_runtime_contract"] = {
        field: copy.deepcopy(plan.get(field))
        for field in _DIRECTOR_H3_RUNTIME_CONTRACT_FIELDS
    }
    from services.h3_shot_planner import seal_h3_shot_plan

    seal_h3_shot_plan(shot_plan)


def _validate_director_h3_runtime_contract(plan: dict, shot_plan: dict) -> None:
    """Reject v2 replay when an outer runtime control escaped the shot seal."""

    if shot_plan.get("semantic_physical_contract_version") != 2:
        return
    from services.h3_shot_planner import validate_h3_shot_plan_seal

    validate_h3_shot_plan_seal(shot_plan)
    saved = shot_plan.get("director_runtime_contract")
    expected = {
        field: plan.get(field)
        for field in _DIRECTOR_H3_RUNTIME_CONTRACT_FIELDS
    }
    if not isinstance(saved, dict) or saved != expected:
        raise ValueError("Saved Director H3 runtime contract disagrees")


def _rehydrate_director_h3_longform(
    gen_params: dict,
    plan: dict,
    *,
    h3_style_workflow: dict | None = None,
) -> bool:
    """Restore a committed Director H3 plan without invoking planning."""
    import copy
    from services.h3_upstream_skills import validate_resolved_h3_style_workflow

    plan = copy.deepcopy(plan)
    shot_plan = plan.get("shot_plan")
    if not isinstance(shot_plan, dict) or int(shot_plan.get("version") or 0) != 1:
        return False
    expected_workflow = validate_resolved_h3_style_workflow(
        h3_style_workflow,
    )
    saved_workflow = validate_resolved_h3_style_workflow(
        plan.get("h3_style_workflow"),
    )
    shot_workflow = validate_resolved_h3_style_workflow(
        shot_plan.get("h3_style_workflow"),
    )
    if saved_workflow != expected_workflow or shot_workflow != saved_workflow:
        raise ValueError("Saved Director H3 style workflow drifted")
    root_frames = plan.get("clip_frames")
    shot_frames = shot_plan.get("clip_frames")
    frames = list(root_frames or shot_frames or [])
    root_published = plan.get("clip_published_frames")
    shot_published = shot_plan.get("clip_published_frames")
    root_trims = plan.get("clip_trim_tail_frames")
    shot_trims = shot_plan.get("clip_trim_tail_frames")
    modern_geometry = any(
        value is not None for value in (
            root_published, shot_published, root_trims, shot_trims,
        )
    )
    if modern_geometry:
        if not all(isinstance(value, list) for value in (
            root_frames, shot_frames, root_published, shot_published,
            root_trims, shot_trims,
        )) or not (
            root_frames == shot_frames
            and root_published == shot_published
            and root_trims == shot_trims
        ):
            raise ValueError("Saved Director H3 publication geometry disagrees")
        published = list(root_published)
        trims = list(root_trims)
    else:
        # Compatibility for committed v1 plans written before per-source trim
        # geometry existed. Their sole aggregate trim was always on the tail.
        trims = [0] * len(frames)
        trims[-1] = int(plan.get("final_trim_frames") or 0)
        published = [
            int(generated) - int(trim)
            for generated, trim in zip(frames, trims)
        ]
    if (
        len(published) != len(frames)
        or len(trims) != len(frames)
        or any(
            int(generated) - int(trim) != int(visible)
            or int(trim) < 0
            or int(trim) >= int(generated)
            for generated, visible, trim in zip(frames, published, trims)
        )
    ):
        raise ValueError("Saved Director H3 publication geometry is incomplete")
    planned_total = int(plan.get("planned_frames") or 0)
    requested_total = int(plan.get("requested_frames") or 0)
    published_total = int(plan.get("published_frames") or requested_total)
    final_trim = int(plan.get("final_trim_frames") or 0)
    if (
        sum(int(value) for value in frames) != planned_total
        or sum(int(value) for value in published) != requested_total
        or published_total != requested_total
        or sum(int(value) for value in trims) != final_trim
        or final_trim != planned_total - requested_total
        or (
            modern_geometry
            and int(shot_plan.get("published_frames") or 0) != requested_total
        )
    ):
        raise ValueError("Saved Director H3 publication totals disagree")
    # Upgrade pre-publication-geometry v1 plans before prompt migration so
    # every subsequently persisted replay uses one complete modern contract.
    plan["clip_frames"] = list(frames)
    plan["clip_published_frames"] = list(published)
    plan["clip_trim_tail_frames"] = list(trims)
    plan["published_frames"] = requested_total
    shot_plan["clip_frames"] = list(frames)
    shot_plan["clip_published_frames"] = list(published)
    shot_plan["clip_trim_tail_frames"] = list(trims)
    shot_plan["published_frames"] = requested_total
    if shot_plan.get("semantic_physical_contract_version") == 2 and (
        not isinstance(plan.get("global_prompt"), str)
        or plan.get("global_prompt") != shot_plan.get("global_prompt")
    ):
        raise ValueError("Saved Director H3 global prompt provenance disagrees")
    prompts = _canonicalize_director_h3_shot_plan(
        shot_plan,
        published_frames=published,
        h3_style_workflow=saved_workflow,
    )
    if len(prompts) != len(frames):
        raise ValueError("Saved Director H3 shot plan is incomplete")
    if shot_plan.get("semantic_physical_contract_version") in {1, 2}:
        saved_models = plan.get("segment_models")
        if not isinstance(saved_models, list) or len(saved_models) != len(prompts):
            raise ValueError("Saved Director H3 segment models are incomplete")
        for index, (prompt, model) in enumerate(zip(prompts, saved_models)):
            if not isinstance(model, dict):
                raise ValueError("Saved Director H3 segment model is invalid")
            ref_schema = _director_h3_prompt_schema(prompt) == "ref2va"
            ref_model = str(model.get("model_type") or "") == _H3_REF2VA_MODEL
            if ref_schema != ref_model:
                raise ValueError(
                    f"Saved Director H3 segment {index + 1} prompt schema and "
                    "checkpoint disagree"
                )
    _validate_director_h3_runtime_contract(plan, shot_plan)
    # Legacy committed plans may duplicate a pre-Context-IR source in either
    # global prompt field. Preserve already-canonical multi-scene provenance;
    # migrate only fields whose parsed events include bare range records.
    from services.director.h3_dialogue import _H3_CANONICAL_RECORD_RE
    from shared.utils.prompt_parser import parse_global_timeline_prompt

    for container in (
        () if shot_plan.get("semantic_physical_contract_version") == 2
        else (shot_plan, plan)
    ):
        global_prompt = str(container.get("global_prompt") or "").strip()
        if not global_prompt:
            continue
        _, global_events = parse_global_timeline_prompt(global_prompt)
        canonical_records = sum(
            1 for line in global_prompt.splitlines()
            if _H3_CANONICAL_RECORD_RE.fullmatch(line.strip())
        )
        if not global_events and not canonical_records:
            container["global_prompt"] = (
                _director_h3_canonical_prompt(
                    global_prompt,
                    duration_seconds=requested_total / float(shot_plan["fps"]),
                )
                if len(prompts) == 1
                else _DIRECTOR_CLIP_SEPARATOR.join(prompts)
            )
        elif global_events and canonical_records != len(global_events):
            contiguous = (
                abs(float(global_events[0].get("start", 0.0))) <= 1e-6
                and all(
                    abs(
                        float(left.get("end", left.get("start", 0.0)))
                        - float(right.get("start", 0.0))
                    ) <= 1e-6
                    for left, right in zip(global_events, global_events[1:])
                )
            )
            if contiguous:
                container["global_prompt"] = _director_h3_canonical_prompt(
                    global_prompt,
                    duration_seconds=requested_total / float(shot_plan["fps"]),
                )
            else:
                # Multi-scene legacy aggregates restart local time at zero;
                # the canonical child records are the only exact, already
                # validated mapping for that persisted aggregate.
                container["global_prompt"] = _DIRECTOR_CLIP_SEPARATOR.join(prompts)
    plan["clip_prompt_previews"] = [prompt[:240] for prompt in prompts]
    first_anchor = plan.get("original_image_start")
    last_anchor = plan.get("original_image_end")
    native = plan.get("native_boundary_conditioning") is True
    gen_params.update({
        "prompt": _DIRECTOR_CLIP_SEPARATOR.join(prompts),
        "per_clip_prompts": prompts,
        "per_clip_frames": frames,
        "video_length": int(plan.get("requested_frames") or 0),
        "sliding_window_size": int(
            plan.get("segment_frames_maximum") or max(frames)
        ),
        "multi_prompts_gen_type": 3,
        "h3_native_boundary_conditioning": native,
        "_h3_longform": copy.deepcopy(plan),
    })
    if saved_workflow is not None:
        gen_params["h3_style_workflow"] = dict(saved_workflow)
    if plan.get("continuation") == "semantic_references" and not native:
        gen_params.pop("image_start", None)
        gen_params.pop("image_end", None)
        gen_params["image_prompt_type"] = ""
    else:
        gen_params["image_start"] = [first_anchor] + [None] * (len(frames) - 1)
        gen_params["image_end"] = [None] * (len(frames) - 1) + [last_anchor]
        gen_params["image_prompt_type"] = (
            "SE" if first_anchor and last_anchor
            else "S" if first_anchor
            else "E" if last_anchor
            else ""
        )
    return True


def _prepare_director_h3_longform(
    gen_params: dict,
    *,
    params: dict,
    clip_plans: list[dict],
    planned_clips: list[dict],
    fps: float,
) -> dict | None:
    """Normalize long Director H3 scenes to native, timestamped clips."""
    import copy

    selected = str(gen_params.get("model_type") or "")
    if selected not in _H3_VIDEO_MODELS:
        return None
    from services.h3_upstream_skills import validate_resolved_h3_style_workflow
    h3_style_workflow = validate_resolved_h3_style_workflow(
        params.get("h3_style_workflow"),
    )

    # Director's frame-position KFI representation is not accepted by H3.
    # Normalize it before either fresh planning or committed-plan replay so a
    # restart cannot restore unsupported runtime inputs or lose the marker.
    director_keyframe_refs = _normalize_director_h3_keyframe_refs(gen_params)

    persisted = params.get("_h3_longform")
    if isinstance(persisted, dict):
        if not _rehydrate_director_h3_longform(
            gen_params,
            persisted,
            h3_style_workflow=h3_style_workflow,
        ):
            raise ValueError("Saved Director H3 plan version is unsupported")
        restored_plan = gen_params["_h3_longform"]
        restored_models = [
            str(item.get("model_type") or "")
            for item in (restored_plan.get("segment_models") or [])
            if isinstance(item, dict)
        ]
        restored_uses_ref2va = (
            str(restored_plan.get("model_type") or "") == _H3_REF2VA_MODEL
            or _H3_REF2VA_MODEL in restored_models
        )
        if (
            restored_uses_ref2va
            and params.get("h3_ref2va_terms_accepted") is not True
        ):
            raise ValueError(
                "This saved Director generation uses the separately licensed "
                "MiniMax H3 Ref2VA checkpoint. Review and accept its model "
                "terms before submitting."
            )
        params["_h3_longform"] = copy.deepcopy(gen_params["_h3_longform"])
        gen_params["h3_ref2va_terms_accepted"] = bool(
            params.get("h3_ref2va_terms_accepted") is True
        )
        return gen_params["_h3_longform"]

    model_def = _wgp.get_model_def(selected) or {}
    maximum = int(model_def.get("frames_maximum") or 0)
    minimum = int(model_def.get("frames_minimum") or 1)
    if maximum <= 0:
        return None
    if params.get("h3_adaptive_conditioning", True) is not False:
        fl2va_def = _wgp.get_model_def(_H3_BASE_FL2VA_MODEL) or {}
        minimum = max(minimum, int(fl2va_def.get("frames_minimum") or minimum))
    video_params = params.get("video_params") or {}
    # Director owns a distinct expert control. Model defaults commonly carry
    # ``video_params.sliding_window_size`` for ordinary WGP execution; treating
    # that default as user intent suppresses automatic Draft/Fast shot pressure
    # and can revive the legacy rolling-window path for bounded H3.
    manual_segment_ceiling = params.get("director_max_shot_frames") not in (
        None, "",
    )
    try:
        requested_maximum = int(
            params.get("director_max_shot_frames") or maximum
        )
    except (TypeError, ValueError):
        requested_maximum = maximum
    from services.h3_shot_planner import floor_h3_frame_count
    if manual_segment_ceiling:
        segment_maximum = floor_h3_frame_count(
            requested_maximum,
            minimum_frames=minimum,
            maximum_frames=maximum,
            align_frame_count=lambda value: _wgp.align_model_frame_count(
                value, model_def,
            ),
        )
    else:
        segment_maximum = int(_wgp.align_model_frame_count(
            maximum, model_def,
        ))
    segment_maximum = max(minimum, min(maximum, segment_maximum))

    from shared.utils.prompt_parser import (
        classify_timeline_clip_boundaries,
    )
    from services.h3_shot_planner import (
        infer_h3_profile_id,
        plan_h3_clip_frames,
        plan_h3_native_shots,
    )

    first_anchor = _director_h3_edge_anchor(gen_params.get("image_start"))
    last_anchor = _director_h3_edge_anchor(
        params.get("image_end") or gen_params.get("image_end"), last=True,
    )
    if (
        params.get("h3_adaptive_conditioning", True) is False
        and selected == _H3_REF2VA_MODEL
        and (first_anchor or last_anchor)
        and params.get("h3_native_boundary_conditioning") is not True
    ):
        raise ValueError(
            "Manual Ref2VA cannot honor Director first/end-frame anchors. "
            "Enable adaptive H3 conditioning or select an FL2VA checkpoint."
        )
    semantic_image_refs = list(gen_params.get("image_refs") or [])
    semantic_references = bool(semantic_image_refs) or any(
        gen_params.get(key)
        for key in (
            "video_guide", "video_guide2", "video_guide3",
            "audio_guide", "audio_guide2", "audio_guide3",
        )
    ) or any(
        letter in str(gen_params.get("audio_prompt_type") or "")
        for letter in "ABCK"
    )
    if (
        params.get("h3_adaptive_conditioning", True) is False
        and selected in _H3_FL2VA_MODELS
        and semantic_references
    ):
        raise ValueError(
            "Manual FL2VA cannot consume Director semantic references. "
            "Enable adaptive H3 conditioning, select Ref2VA, or remove the references."
        )

    requested_scene_frames: list[int] = []
    scene_prompts: list[str] = []
    scene_geometry_prompts: list[str] = []
    fallback_prompts = list(gen_params.get("per_clip_prompts") or [])
    # Schema follows the authored Context-IR fields, not the eventual adaptive
    # checkpoint. A Base scene may route to Ref2VA for conditioning, while a
    # six-field reference scene must remain Ref2VA even under adaptive routing.
    selected_prompt_mode = None
    for index, plan in enumerate(clip_plans):
        planned = planned_clips[index] if index < len(planned_clips) else {}
        try:
            duration = float(
                planned.get("duration_sec")
                or (float(planned.get("end", 0)) - float(planned.get("start", 0)))
            )
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0:
            duration = 20.0
        frame_count = max(1, round(duration * float(fps)))
        requested_scene_frames.append(frame_count)
        scene_prompt = _director_h3_scene_prompt(
            plan, frame_count=frame_count, fps=fps, mode=selected_prompt_mode,
        )
        if not scene_prompt and index < len(fallback_prompts):
            fallback = str(fallback_prompts[index] or "").strip()
            if fallback:
                scene_prompt = _director_h3_canonical_prompt(
                    fallback,
                    duration_seconds=frame_count / float(fps),
                    mode=selected_prompt_mode,
                )
        if not scene_prompt:
            fallback = str(gen_params.get("prompt") or "").strip()
            if fallback:
                scene_prompt = _director_h3_canonical_prompt(
                    fallback,
                    duration_seconds=frame_count / float(fps),
                    mode=selected_prompt_mode,
                )
        if not scene_prompt:
            raise ValueError("Director H3 scene prompt is empty")
        scene_prompts.append(scene_prompt)
        raw_windows = [
            str(item.get("prompt", item.get("text", "")))
            if isinstance(item, dict) else str(item)
            for item in (plan.get("window_prompts") or [])
        ]
        raw_windows = [item.strip() for item in raw_windows if item.strip()]
        raw_prompt = (
            "\n".join(raw_windows)
            if raw_windows else str(plan.get("video_prompt") or "").strip()
        )
        if not raw_prompt and index < len(fallback_prompts):
            raw_prompt = str(fallback_prompts[index])
        # Window boundaries are authored structure; otherwise retain the raw
        # prompt solely for geometry/profile decisions so deterministic
        # canonical wrappers do not turn untimed prose into authored timing.
        scene_geometry_prompts.append(
            scene_prompt if raw_windows else raw_prompt or scene_prompt
        )

    if not requested_scene_frames:
        requested_scene_frames = [max(1, int(gen_params.get("video_length") or 1))]
        raw_prompt = str(gen_params.get("prompt") or "").strip()
        if not raw_prompt:
            raise ValueError("Director H3 scene prompt is empty")
        scene_geometry_prompts = [raw_prompt]
        scene_prompts = [_director_h3_canonical_prompt(
            raw_prompt,
            duration_seconds=requested_scene_frames[0] / float(fps),
            mode=selected_prompt_mode,
        )]

    # H3 FL2VA cannot consume arbitrary KFI timing. The normalization above
    # preserves Director keyframes as documented Ref2VA semantic references;
    # the original timing intent remains in the committed long-form metadata.
    requested_frames = sum(requested_scene_frames)
    end_anchor_tail = int(model_def.get("frames_steps") or 0) if last_anchor else 0
    generation_scene_frames = list(requested_scene_frames)
    generation_scene_frames[-1] += end_anchor_tail

    segment_frames: list[int] = []
    segment_prompts: list[str] = []
    segment_boundaries: list[dict] = []
    segment_source_indices: list[int] = []
    segment_requested_frames: list[int] = []
    source_segment_policies: list[dict] = []
    profile_context = dict(video_params)
    for key in (
        "profile_id", "performance_profile", "h3_performance_profile",
        "num_inference_steps", "resolution", "custom_settings",
    ):
        if key in params:
            profile_context[key] = params[key]
    profile_id = infer_h3_profile_id(profile_context)
    for scene_index, (scene_frames, scene_prompt, geometry_prompt) in enumerate(
        zip(generation_scene_frames, scene_prompts, scene_geometry_prompts)
    ):
        planned_frames, scene_policy = plan_h3_clip_frames(
            scene_frames,
            prompt=geometry_prompt,
            fps=fps,
            minimum_frames=minimum,
            maximum_frames=segment_maximum,
            align_frame_count=lambda value: _wgp.align_model_frame_count(
                value, model_def,
            ),
            profile_id=profile_id,
            manual_segment_ceiling=manual_segment_ceiling,
            published_total_frames=requested_scene_frames[scene_index],
        )
        scene_requested = list(
            scene_policy.get("clip_requested_frames") or planned_frames
        )
        if "clip_requested_frames" not in scene_policy:
            scene_requested[-1] -= (
                sum(planned_frames) - requested_scene_frames[scene_index]
            )
        source_segment_policies.append(scene_policy)
        boundaries = classify_timeline_clip_boundaries(
            scene_prompt,
            clip_frame_counts=scene_requested,
            fps=fps,
        )
        if segment_frames:
            segment_boundaries.append({
                "type": "cut",
                "source": "director_scene_boundary",
                "event": f"Director scene {scene_index + 1}",
                "at_frame": sum(requested_scene_frames[:scene_index]),
                "at_seconds": sum(requested_scene_frames[:scene_index]) / float(fps),
            })
        segment_frames.extend(planned_frames)
        segment_requested_frames.extend(scene_requested)
        segment_source_indices.extend([scene_index] * len(planned_frames))
        segment_boundaries.extend(boundaries)

    boundary_overrides = params.get("h3_boundary_overrides")
    if boundary_overrides is not None and not isinstance(boundary_overrides, list):
        raise ValueError("h3_boundary_overrides must be a list")
    if isinstance(boundary_overrides, list):
        for index, override in enumerate(boundary_overrides[:len(segment_boundaries)]):
            if not isinstance(override, dict) or not override.get("type"):
                continue
            boundary_type = str(override["type"])
            if boundary_type not in {"continuous", "precut", "cut", "transition"}:
                raise ValueError(f"Unknown H3 boundary override: {boundary_type}")
            segment_boundaries[index] = {
                **segment_boundaries[index],
                "type": boundary_type,
                "source": "user_override",
            }

    segment_policy = {
        "version": 1,
        "id": "director_source_aggregate_v1",
        "profile_id": profile_id,
        "applied": any(item.get("applied") for item in source_segment_policies),
        "source_policies": source_segment_policies,
    }
    structured_shots = [
        plan.get("_h3_shot") if isinstance(plan, dict) else None
        for plan in clip_plans
    ]
    shot_plan = plan_h3_native_shots(
        global_prompt="\n\n".join(scene_prompts),
        clip_frame_counts=segment_frames,
        fps=fps,
        clip_boundaries=segment_boundaries,
        source_prompts=scene_prompts,
        source_indices=segment_source_indices,
        structured_shots=structured_shots,
        clip_requested_frames=segment_requested_frames,
        segment_frames_maximum=segment_maximum,
        segment_policy=segment_policy,
    )
    segment_prompts = _canonicalize_director_h3_shot_plan(
        shot_plan,
        h3_style_workflow=h3_style_workflow,
    )
    segment_boundaries = list(shot_plan["clip_boundaries"])
    clip_published_frames = list(shot_plan["clip_published_frames"])
    clip_trim_tail_frames = list(shot_plan["clip_trim_tail_frames"])

    if len(segment_frames) == 1 and len(requested_scene_frames) == 1:
        # Native-sized Director clips need no automatic long-form contract.
        # Terms are still enforced below for a directly selected Ref2VA job.
        effective = selected
        if params.get("h3_adaptive_conditioning", True) is not False:
            effective = (
                _H3_REF2VA_MODEL if semantic_references
                else _director_h3_preferred_fl2va(params, selected)
            )
        prompt_schema = _director_h3_prompt_schema(segment_prompts[0])
        if prompt_schema == "ref2va":
            if params.get("h3_ref2va_terms_accepted") is not True:
                raise ValueError(
                    "This Director generation uses the separately licensed "
                    "MiniMax H3 Ref2VA checkpoint. Review and accept its model "
                    "terms before submitting."
                )
            if (
                (first_anchor or last_anchor)
                and params.get("h3_native_boundary_conditioning") is not True
            ):
                raise ValueError(
                    "Director Ref2VA prompt schema cannot be paired with "
                    "native first/end-frame anchors"
                )
            effective = _H3_REF2VA_MODEL
        elif effective == _H3_REF2VA_MODEL:
            raise ValueError(
                "Director Base prompt schema cannot be paired with a Ref2VA "
                "checkpoint; supply the six-field Ref2VA Context-IR"
            )
        effective_models = [effective]
        if (
            _H3_REF2VA_MODEL in effective_models
            and params.get("h3_ref2va_terms_accepted") is not True
        ):
            raise ValueError(
                "This Director generation uses the separately licensed "
                "MiniMax H3 Ref2VA checkpoint. Review and accept its model "
                "terms before submitting."
            )
        if params.get("h3_ref2va_terms_accepted") is True:
            gen_params["h3_ref2va_terms_accepted"] = True
        if effective != selected:
            gen_params["model_type"] = effective
        if h3_style_workflow is not None:
            gen_params["h3_style_workflow"] = dict(h3_style_workflow)
        gen_params["prompt"] = segment_prompts[0]
        gen_params["per_clip_prompts"] = list(segment_prompts)
        if (
            effective == _H3_REF2VA_MODEL
            and params.get("h3_native_boundary_conditioning") is not True
        ):
            # Ref2VA's images are semantic references rather than native
            # first/last-frame controls.
            gen_params.pop("image_start", None)
            gen_params.pop("image_end", None)
            gen_params["image_prompt_type"] = ""
        return None
    segment_models = _director_h3_segment_models(
        params,
        selected=selected,
        boundaries=segment_boundaries,
        segment_count=len(segment_frames),
        first_anchor=first_anchor,
        last_anchor=last_anchor,
        semantic_references=semantic_references,
    )
    prompt_schemas = [
        _director_h3_prompt_schema(prompt) for prompt in segment_prompts
    ]
    if (
        any(schema == "ref2va" for schema in prompt_schemas)
        and params.get("h3_ref2va_terms_accepted") is not True
    ):
        raise ValueError(
            "This Director H3 plan requires the separately licensed Ref2VA "
            "checkpoint for its six-field prompt schema. Review and accept "
            "its model terms before submitting."
        )
    if any(schema == "ref2va" for schema in prompt_schemas) and (
        (first_anchor or last_anchor)
        and params.get("h3_native_boundary_conditioning") is not True
    ):
        raise ValueError(
            "Director Ref2VA prompt schema cannot be paired with native "
            "first/end-frame anchors"
        )
    fl2va_model = _director_h3_preferred_fl2va(params, selected)
    for index, (schema, model) in enumerate(zip(
        prompt_schemas, segment_models,
    )):
        if schema == "ref2va":
            if model.get("user_override") and model["model_type"] != _H3_REF2VA_MODEL:
                raise ValueError(
                    f"Director segment {index + 1} Ref2VA prompt schema conflicts "
                    "with its Base checkpoint override"
                )
            model.update({
                "model_type": _H3_REF2VA_MODEL,
                "reason": "six-field Ref2VA prompt schema",
            })
        elif model["model_type"] == _H3_REF2VA_MODEL:
            if semantic_references and not model.get("drop_semantic_refs"):
                raise ValueError(
                    f"Director segment {index + 1} Base prompt schema cannot "
                    "carry Ref2VA semantic references; supply the six-field "
                    "Ref2VA Context-IR"
                )
            if model.get("user_override"):
                raise ValueError(
                    f"Director segment {index + 1} Base prompt schema conflicts "
                    "with its Ref2VA checkpoint override"
                )
            model.update({
                "model_type": fl2va_model,
                "reason": "Base Context-IR prompt schema",
            })
    for index, model in enumerate(segment_models):
        model["switch_from_previous"] = bool(
            index
            and segment_models[index - 1]["model_type"] != model["model_type"]
        )
    if (
        any(item["model_type"] == _H3_REF2VA_MODEL for item in segment_models)
        and params.get("h3_ref2va_terms_accepted") is not True
    ):
        raise ValueError(
            "This Director H3 plan requires the separately licensed Ref2VA "
            "checkpoint for semantic references or a scene transition. "
            "Review and accept its model terms before submitting."
        )

    planned_frames = sum(segment_frames)
    final_trim = sum(clip_trim_tail_frames)
    if (
        sum(clip_published_frames) != requested_frames
        or final_trim != planned_frames - requested_frames
    ):
        raise ValueError("Unable to preserve the requested Director duration on the H3 frame grid")

    gen_params.update({
        "prompt": _DIRECTOR_CLIP_SEPARATOR.join(segment_prompts),
        "per_clip_prompts": segment_prompts,
        "per_clip_frames": segment_frames,
        "video_length": requested_frames,
        "sliding_window_size": segment_maximum,
        "multi_prompts_gen_type": 3,
        "image_start": [first_anchor] + [None] * (len(segment_frames) - 1),
        "image_end": [None] * (len(segment_frames) - 1) + [last_anchor],
        "h3_ref2va_terms_accepted": bool(
            params.get("h3_ref2va_terms_accepted") is True
        ),
        "h3_native_boundary_conditioning": bool(
            params.get("h3_native_boundary_conditioning") is True
        ),
        "_h3_longform": {
            "model_type": selected,
            **(
                {"h3_style_workflow": dict(h3_style_workflow)}
                if h3_style_workflow is not None else {}
            ),
            "requested_frames": requested_frames,
            "planned_frames": planned_frames,
            "published_frames": requested_frames,
            "final_trim_frames": final_trim,
            "clip_count": len(segment_frames),
            "clip_frames": segment_frames,
            "clip_published_frames": clip_published_frames,
            "clip_trim_tail_frames": clip_trim_tail_frames,
            "clip_prompt_previews": [prompt[:240] for prompt in segment_prompts],
            "segment_frames_maximum": segment_maximum,
            "manual_segment_ceiling": manual_segment_ceiling,
            "continuation": (
                "semantic_references" if semantic_references else "last_frame"
            ),
            "clip_boundaries": segment_boundaries,
            "shot_plan": shot_plan,
            "segment_policy": segment_policy,
            "segment_models": segment_models,
            "segment_source_indices": segment_source_indices,
            "director_keyframe_conditioning": (
                "semantic_references" if director_keyframe_refs else None
            ),
            "adaptive_conditioning": params.get(
                "h3_adaptive_conditioning", True,
            ) is not False,
            "native_boundary_conditioning": bool(
                params.get("h3_native_boundary_conditioning") is True
            ),
            "preserve_generated_audio": any(
                item["model_type"] == _H3_REF2VA_MODEL
                for item in segment_models
            ) or params.get("h3_native_boundary_conditioning") is True,
            "global_prompt": "\n\n".join(scene_prompts),
            "original_image_start": first_anchor,
            "original_image_end": last_anchor,
        },
    })
    _bind_director_h3_runtime_contract(gen_params["_h3_longform"])
    if h3_style_workflow is not None:
        gen_params["h3_style_workflow"] = dict(h3_style_workflow)
    gen_params["image_prompt_type"] = (
        "SE" if first_anchor and last_anchor
        else "S" if first_anchor
        else "E" if last_anchor
        else ""
    )
    params["_h3_longform"] = copy.deepcopy(gen_params["_h3_longform"])
    return gen_params["_h3_longform"]

def _run_video_generation(pid: str, params: dict, clip_plans: list[dict],
                          planned_clips: list[dict], clip_images: list[str],
                          clip_keyframes: Optional[list[list[str]]] = None,
                          out_dir: str = None, workspace: str = None) -> list[str]:
    """Generate multi-clip video with optional keyframe injection. Returns list of output filenames."""
    _validate_director_models(params, stages=("video",))
    video_model = params.get("video_model")
    if not video_model:
        # Fallback: use first available video model from server config
        available = _wgp.get_models_list() if _wgp else []
        video_models = [m for m in available if m.get("is_t2v") or m.get("is_i2v")]
        video_model = video_models[0]["model_type"] if video_models else "ltx2_22B_distilled"
        print(f"[Pipeline] No video_model in params, using fallback: {video_model}")
    video_params = params.get("video_params", {})
    video_loras = params.get("video_loras", {})
    # Mirror of the image-LoRA file-existence filter — see _run_image_generation
    # for the rationale. Filter video_loras to those actually present in
    # video_model's LoRA directory so a stale activation from a different
    # video model doesn't crash wgp validation upfront.
    try:
        _vid_activated = list(video_loras.get("activated_loras", []) or [])
        _vid_mults = video_loras.get("loras_multipliers", "") or ""
        if _vid_activated:
            print(
                f"[Pipeline {pid}] Video LoRAs received: {len(_vid_activated)} | "
                f"model={video_model} | "
                f"names={[os.path.basename(n) for n in _vid_activated]} | "
                f"multipliers={_vid_mults!r}"
            )
            try:
                _vid_lora_dir = _wgp.get_lora_dir(video_model)
            except Exception:
                _vid_lora_dir = ""
            if _vid_lora_dir and os.path.isdir(_vid_lora_dir):
                _vid_existing = {
                    f for f in os.listdir(_vid_lora_dir)
                    if f.lower().endswith((".safetensors", ".sft"))
                }
                _vid_mult_tokens = _vid_mults.split()
                _vid_kept: list[str] = []
                _vid_kept_mults: list[str] = []
                _vid_skipped: list[str] = []
                for _idx, _name in enumerate(_vid_activated):
                    _basename = os.path.basename(_name)
                    if _basename in _vid_existing:
                        _vid_kept.append(_name)
                        if _idx < len(_vid_mult_tokens):
                            _vid_kept_mults.append(_vid_mult_tokens[_idx])
                    else:
                        _vid_skipped.append(_basename)
                if _vid_skipped:
                    _warn = (
                        f"Skipped {len(_vid_skipped)} video LoRA(s) not present in "
                        f"{os.path.basename(_vid_lora_dir)}/: {_vid_skipped}. These "
                        f"were likely activated when a different video model was "
                        f"selected. Re-select your video LoRAs for {video_model}."
                    )
                    print(f"[Pipeline {pid}] {_warn}")
                    _exw = _pipelines.get(pid, {}).get("lora_warnings", []) or []
                    _update_pipeline(pid, lora_warnings=[*_exw, _warn])
                video_loras = {
                    "activated_loras": _vid_kept,
                    "loras_multipliers": " ".join(_vid_kept_mults),
                }
                print(
                    f"[Pipeline {pid}] Video LoRAs after existence filter: "
                    f"{len(_vid_kept)} kept, {len(_vid_skipped)} skipped"
                )
    except Exception as _e:
        print(f"[Pipeline {pid}] Video LoRA file-existence filter skipped: {_e}")

    audio_path = params.get("audio_path")
    seamless = params.get("seamless", True)
    pipeline_type = params.get("pipeline_type", "music_video")
    # Get FPS from model definition (reliable) — don't trust frontend default of 16
    fps = params.get("fps", 16)
    try:
        model_def = _wgp.get_model_def(video_model)
        if model_def and model_def.get("fps"):
            fps = model_def["fps"]
    except Exception:
        pass
    print(f"[Pipeline] Video gen: fps={fps}, video_model={video_model}")

    resolution = video_params.get("resolution", "1280x720")
    steps = video_params.get("num_inference_steps", 8)
    guidance = video_params.get("guidance_scale", 1)
    spatial_upsampling = params.get("video_spatial_upsampling", "")
    film_grain_intensity = params.get("video_film_grain_intensity", 0)
    film_grain_saturation = params.get("video_film_grain_saturation", 0.5)
    self_refiner = params.get("video_self_refiner", 0)

    if not out_dir:
        out_dir = _wgp.save_path

    # Quantize helper
    try:
        _min_f, _fs, _latent = _wgp.get_model_min_frames_and_step(video_model)
    except Exception:
        _min_f, _fs, _latent = 17, 8, 8

    def _quantize_frames(cf):
        return max((cf - 1) // _latent * _latent + 1, _min_f)

    # ── SEAMLESS MODE: one continuous rolling window generation ──────
    # Instead of separate per-clip jobs, build ONE generation that looks like
    # Studio mode: rolling windows with per-window prompts + keyframe injection.
    if seamless:
        window_prompts_all = []  # One prompt per rolling window
        keyframe_images = []     # All keyframe images in order
        keyframe_frame_positions = []  # Absolute frame numbers (1-indexed for wgp parser)

        # Track cumulative frame position as we go through scenes
        cumulative_frames = 0

        for i, plan in enumerate(clip_plans):
            pc = planned_clips[i] if i < len(planned_clips) else {}
            dur_sec = pc.get("duration_sec", pc.get("end", 0) - pc.get("start", 0))
            if dur_sec <= 0:
                dur_sec = 20
            scene_frames = round(dur_sec * fps)

            wp = plan.get("window_prompts") or []
            wp = [w.get("prompt", w.get("text", str(w))) if isinstance(w, dict) else str(w) for w in wp]
            if len(wp) > 1:
                for w_prompt in wp:
                    window_prompts_all.append(w_prompt)
            else:
                vp = plan.get("video_prompt", "")
                if vp:
                    window_prompts_all.append(vp)

            # Mid-scene keyframes from the LLM (injected at mid-point of this scene)
            if clip_keyframes and i < len(clip_keyframes):
                kf_list = clip_keyframes[i]
                if kf_list:
                    # Distribute mid-scene keyframes evenly across the scene
                    num_kf = len(kf_list)
                    for ki, kf_file in enumerate(kf_list):
                        if kf_file:
                            kf_path = os.path.join(out_dir, kf_file)
                            if os.path.isfile(kf_path):
                                # Position: evenly spaced within the scene
                                kf_pos = cumulative_frames + int(scene_frames * (ki + 1) / (num_kf + 1))
                                keyframe_images.append(kf_path)
                                keyframe_frame_positions.append(kf_pos + 1)  # 1-indexed for wgp parser

            # Scene boundary keyframe: inject next scene's start image at the end of this scene
            if i < len(clip_plans) - 1:
                next_img = clip_images[i + 1] if i + 1 < len(clip_images) else ""
                if next_img:
                    next_path = os.path.join(out_dir, next_img)
                    if os.path.isfile(next_path):
                        boundary_frame = cumulative_frames + scene_frames
                        keyframe_images.append(next_path)
                        keyframe_frame_positions.append(boundary_frame)  # 1-indexed (approx)

            cumulative_frames += scene_frames

        total_frames = _quantize_frames(cumulative_frames)
        sliding_window_frames = _quantize_frames(round(20 * fps))

        # First scene's start image
        first_start = ""
        if clip_images and clip_images[0]:
            first_path = os.path.join(out_dir, clip_images[0])
            if os.path.isfile(first_path):
                first_start = first_path

        prompt_text = "\n".join(window_prompts_all)

        print(f"[Pipeline {pid}] Seamless mode: {len(window_prompts_all)} windows, "
              f"{len(keyframe_images)} keyframes at frames {keyframe_frame_positions}, "
              f"{total_frames} total frames ({total_frames/fps:.1f}s)")

    # ── STANDARD MODE: separate per-clip generation ─────────────────
    else:
        prompts = []
        image_start_paths = []
        image_end_paths = []
        per_clip_frames = []
        has_sliding_window = False

        for i, plan in enumerate(clip_plans):
            wp = plan.get("window_prompts") or []
            wp = [w.get("prompt", w.get("text", str(w))) if isinstance(w, dict) else str(w) for w in wp]
            if len(wp) > 1:
                prompts.append("\n".join(wp))
            else:
                vp = plan.get("video_prompt", "")
                pc = planned_clips[i] if i < len(planned_clips) else {}
                dur = pc.get("duration_sec", pc.get("end", 0) - pc.get("start", 0))
                if dur > 32 and vp:
                    print(f"[Pipeline] WARNING: Clip {i+1} is {dur:.0f}s but has no window_prompts")
                prompts.append(vp)

            img_file = clip_images[i] if i < len(clip_images) else ""
            if img_file:
                img_path = os.path.join(out_dir, img_file)
                image_start_paths.append(img_path if os.path.isfile(img_path) else "")
            else:
                image_start_paths.append("")
            image_end_paths.append("")

            pc = planned_clips[i] if i < len(planned_clips) else {}
            window_prompts = plan.get("window_prompts", []) or []
            window_count = plan.get("window_count", 1) or 1
            if len(window_prompts) > 1 and window_count <= 1:
                window_count = len(window_prompts)
            has_keyframes = bool(plan.get("keyframe_prompts"))
            num_keyframes = len(plan.get("keyframe_prompts", []) or [])

            if window_count > 1 or has_keyframes:
                shot_duration = pc.get("duration_sec", pc.get("end", 0) - pc.get("start", 0))
                if shot_duration <= 0:
                    shot_duration = 20 * max(window_count, num_keyframes + 1)
                clip_frames = max(round(shot_duration * fps), round(5 * fps))
                per_clip_frames.append(clip_frames)
                has_sliding_window = True
            else:
                # SECONDS are the fps-agnostic ground truth. planned_clips
                # from plan_clip_structure carry start/end (+duration_frames)
                # but NO duration_sec — the old `get("duration_sec", 0)`
                # fell straight through to duration_frames, which the
                # frontend may have had computed at the WRONG model's fps
                # (modelOptions belongs to the Studio-selected model, e.g.
                # ACE-Step right after generating the track → fps 16). A
                # 26s clip became 26x16=416 frames, rendered at LTX-2's 25
                # fps = 16.6s — every music-video clip silently shortened
                # by 16/25.
                dur_sec = pc.get("duration_sec") or (pc.get("end", 0) - pc.get("start", 0))
                clip_frames = round(dur_sec * fps) if dur_sec > 0 else pc.get("duration_frames", round(20 * fps))
                if clip_frames > round(32 * fps):
                    has_sliding_window = True
                per_clip_frames.append(max(clip_frames, round(5 * fps)))

        # Quantize to the model's (latent*n + 1) frame lattice WITHOUT letting
        # the error compound. Floor-snapping each clip independently lost 0-7
        # frames per clip (an 8s clip = 200 frames @25fps floors to 193 —
        # exactly the "7 frames short" the user measured), while the song
        # plays on at true time — so cuts drifted off the planned musical
        # break points by seconds near the end of a song. Instead, round each
        # clip to the NEAREST valid length and carry the residual into the
        # next clip: every cumulative boundary stays within half a latent
        # step (±4 frames ≈ 0.16s) of the planned beat, forever.
        per_clip_frames = _quantize_clip_frame_schedule(
            per_clip_frames, _min_f, _latent,
        )
        total_frames = sum(per_clip_frames)
        max_clip_frames = max(per_clip_frames) if per_clip_frames else round(5 * fps)
        # Single-window case: sliding_window_frames must be STRICTLY
        # greater than max_clip_frames after wgp's internal quantization
        # (line ~6725 of wgp.py), or wgp interprets `video_length >
        # sliding_window_size` and splits the clip into multiple
        # windows. Add `_latent + 1` frames of safety margin — one full
        # latent step plus one to guarantee strict-greater after the
        # `(x - 1) // latent * latent + 1` rounding. Multi-window
        # case (has_sliding_window=True) stays at 20s because the
        # whole point is to slide.
        #
        # Single-window clips are allowed up to 32s (was 22s): LTX-2.3
        # holds up well past its nominal ~20s window — user-validated at
        # 26s with the window sized to the clip — and one window beats
        # mid-clip window seams for music sync. plan_clip_structure caps
        # planned clips at MAX_CLIP_SECONDS=26 (the 75%-merge rule can
        # stretch a section to ~32s, hence the threshold).
        sliding_window_frames = (
            round(20 * fps) if has_sliding_window
            else max_clip_frames + _latent + 1
        )

        for ci, cf in enumerate(per_clip_frames):
            wp_count = len((clip_plans[ci].get("window_prompts") or []) if ci < len(clip_plans) else [])
            wc = clip_plans[ci].get("window_count", 1) if ci < len(clip_plans) else 1
            print(f"[Pipeline {pid}] Clip {ci+1}: {cf} frames ({cf/fps:.1f}s), windows={wc}, window_prompts={wp_count}")

    # Build audio params
    audio_params: dict = {}
    audio_start_sec = (
        _audio_timeline_start(planned_clips)
        if pipeline_type != "short_film_story" and audio_path
        else 0.0
    )
    if pipeline_type == "short_film_story":
        audio_params["audio_prompt_type"] = ""
    elif audio_path:
        audio_params["audio_prompt_type"] = "A"
        audio_params["audio_guide"] = audio_path
        # Music analysis may intentionally omit a silent intro. Align model
        # conditioning to the source-audio time represented by video frame 0.
        audio_params["audio_frame_offset"] = round(audio_start_sec * fps)
        audio_scale = params.get("audio_scale")
        if audio_scale is not None:
            audio_params["audio_scale"] = audio_scale

    # ── Build gen_params based on mode ──────────────────────────────
    lora_params = {
        "activated_loras": video_loras.get("activated_loras", []),
        "loras_multipliers": " ".join(
            m.split(";")[0] for m in (video_loras.get("loras_multipliers", "") or "").split(" ") if m
        ),
    }

    if seamless:
        # Seamless: ONE generation job with rolling windows + keyframe injection
        gen_params: dict = {
            "model_type": video_model,
            "prompt": prompt_text,
            "image_mode": 0,
            "multi_prompts_gen_type": 0,  # Rolling window mode (one prompt per window)
            "image_prompt_type": "S" if first_start else "",
            "video_prompt_type": "",
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            "resolution": resolution,
            "video_length": total_frames,
            "sliding_window_size": sliding_window_frames,
            "seed": -1,
            "settings_version": 2.52,
            "generation_mode": "video",
            "repeat_generation": 1,
            "negative_prompt": "",
            "self_refiner_setting": self_refiner,
            "_director_pipeline_id": pid,
            "_director_final_video_postprocess": 1,
            **lora_params,
            **audio_params,
        }
        if first_start:
            gen_params["image_start"] = first_start
        # Keyframe injection via image_refs + frames_positions (numeric absolute positions)
        if keyframe_images:
            gen_params["image_refs"] = keyframe_images
            gen_params["frames_positions"] = " ".join(str(p) for p in keyframe_frame_positions)
            existing_vpt = gen_params.get("video_prompt_type", "")
            if "KFI" not in existing_vpt:
                gen_params["video_prompt_type"] = existing_vpt + "KFI"
            print(f"[Pipeline {pid}] Seamless keyframes: {len(keyframe_images)} images at frames {keyframe_frame_positions}")

    else:
        # Standard: separate per-clip generation jobs
        CLIP_SEPARATOR = "\n---CLIP_BOUNDARY---\n"
        prompt_text = CLIP_SEPARATOR.join(prompts)

        has_any_start = any(p for p in image_start_paths)
        has_any_end = any(p for p in image_end_paths)
        if not has_any_start:
            image_start_paths = []
        if not has_any_end:
            image_end_paths = []

        ipt = "SE" if has_any_start and has_any_end else ("S" if has_any_start else "")

        gen_params: dict = {
            "model_type": video_model,
            "prompt": prompt_text,
            "image_mode": 0,
            "multi_prompts_gen_type": 3,  # Multi-clip mode
            "image_prompt_type": ipt,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            "resolution": resolution,
            "video_length": total_frames,
            "sliding_window_size": sliding_window_frames,
            "per_clip_frames": per_clip_frames,
            "multi_clip_audio_start_sec": audio_start_sec,
            "seed": -1,
            "settings_version": 2.52,
            "generation_mode": "video",
            "repeat_generation": 1,
            "negative_prompt": "",
            "self_refiner_setting": self_refiner,
            "_director_pipeline_id": pid,
            "_director_final_video_postprocess": 1,
            **lora_params,
            **audio_params,
        }
        if has_any_start:
            gen_params["image_start"] = image_start_paths
        if has_any_end:
            gen_params["image_end"] = image_end_paths
        # Per-clip keyframe injection
        if clip_keyframes:
            per_clip_kf_paths: list[list[str]] = []
            for i, kf_list in enumerate(clip_keyframes):
                paths = []
                for kf_file in kf_list:
                    if kf_file:
                        kf_path = os.path.join(out_dir, kf_file)
                        if os.path.isfile(kf_path):
                            paths.append(kf_path)
                per_clip_kf_paths.append(paths)
            if any(paths for paths in per_clip_kf_paths):
                gen_params["per_clip_keyframes"] = per_clip_kf_paths
                print(f"[Pipeline {pid}] Keyframe injection: {[len(p) for p in per_clip_kf_paths]} keyframes per clip")

    if (
        _director_effective_shot_image_policy(params)
        == SHOT_IMAGES_DIRECT_REFERENCES
    ):
        direct_refs = [
            path for path in _director_visual_reference_paths(params)
            if os.path.isfile(path)
        ]
        if direct_refs:
            gen_params["image_refs"] = direct_refs

    h3_longform = _prepare_director_h3_longform(
        gen_params,
        params=params,
        clip_plans=clip_plans,
        planned_clips=planned_clips,
        fps=fps,
    )
    if h3_longform:
        print(
            f"[Pipeline {pid}] H3 Director plan: "
            f"{h3_longform['requested_frames']} requested frames -> "
            f"{h3_longform['clip_count']} native segments / "
            f"{h3_longform['planned_frames']} aligned frames"
        )
        # The parent snapshot is committed before the child job can begin.
        # Resume restores this exact plan and never reruns shot reconciliation.
        _update_pipeline(pid, params=params)
        _require_pipeline_checkpoint(pid, "committed-h3-shot-plan")

    # Common params
    voice_ref = params.get("voice_reference")
    if voice_ref:
        gen_params["voice_reference"] = voice_ref
        gen_params["identity_guidance_scale"] = params.get("identity_guidance_scale", 3.0)
        print(f"[Pipeline {pid}] Voice reference: {voice_ref}, identity_scale={gen_params['identity_guidance_scale']}")
    if spatial_upsampling:
        gen_params["spatial_upsampling"] = spatial_upsampling
    if film_grain_intensity > 0:
        gen_params["film_grain_intensity"] = film_grain_intensity
        gen_params["film_grain_saturation"] = film_grain_saturation

    # Track progress by monitoring the generation job
    gen_params["_director_recovery_unit"] = {
        "kind": "video_generation",
        "variant": 0,
        "index": 0,
    }
    output_files = _submit_and_wait(gen_params, timeout_s=7200, workspace=workspace, out_dir=out_dir)  # 2hr timeout for long videos
    return output_files
