"""Private H3 cumulative windows over an unchanged publication contract.

This is a deterministic, CPU-only compiler. The v2 shot plan describes disjoint
published pieces; the separate windows describe sampling, retained context and
full cumulative outputs. A digest detects drift, not authority: callers must
bind the plan to their independently authorized job. Existing saved v2 plans
are never implicitly upgraded to this mode.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from services.h3_execution_contract import validate_h3_execution_shots
from services.h3_native_continuation import (
    H3_DEFAULT_CONTEXT_FRAMES,
    H3_DEFAULT_MAX_EXTENSION_FRAMES,
    H3_DEFAULT_MAX_WINDOW_FRAMES,
    H3_NATIVE_CONTINUATION_MODE,
    H3_NATIVE_FPS,
    audio_tick_at_frame,
    is_legal_h3_video_frame_count,
    plan_h3_native_continuation_tail,
)
from services.h3_shot_planner import (
    _H3_CANONICAL_RECORD_RE,
    H3ShotPlanError,
    _canonical_context_ir_parts,
    _h3_frame_at,
    _h3_seconds,
    _protect_dialogue,
    _restore_dialogue_exact,
    plan_h3_native_shots,
)
from services.queue_recovery_runtime import (
    MAX_MANIFEST_BYTES,
    QueueRecoveryRuntimeError,
)

H3_CUMULATIVE_PLAN_VERSION = 1
_MAX_WINDOWS = 256
_MAX_SOURCE_BYTES = 256 * 1024
# Leave space for the enclosing job parameters and input evidence. The actual
# manifest writer still checks its entire payload before queue admission.
_MAX_PLAN_BYTES = MAX_MANIFEST_BYTES // 2


class H3CumulativePlanError(ValueError):
    """Raised when publication, sampling or replay evidence disagrees."""


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise H3CumulativePlanError("H3 cumulative plan must be finite JSON.") from exc


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sampler_prompt(prompt: str, *, context: int, published: int, padding: int) -> str:
    """Rebase generated canonical headers, preserving every payload byte.

    Dialogue is protected before parsing, so literal shot labels, timecodes,
    field labels and line breaks inside speech cannot become structure. Hidden
    history contains no authored event; retained AV supplies that history.
    """
    protected, dialogue = _protect_dialogue(prompt)
    try:
        canonical = _canonical_context_ir_parts(protected)
    except H3ShotPlanError as exc:
        raise H3CumulativePlanError(str(exc)) from exc
    if canonical is None:
        raise H3CumulativePlanError(
            "Cumulative publication prompt must be canonical Context-IR."
        )
    order, fields, visual_field, events = canonical
    lines = [line.strip() for line in fields[visual_field].splitlines() if line.strip()]
    records: list[tuple[int, int, str]] = []
    if context:
        records.append(
            (
                0,
                context,
                (
                    "shot_name: Retained history | audiovisual_description: Preserve "
                    "the already generated visual and audio state; no new authored "
                    "action or speech is scheduled in these context frames. | "
                    "dialogue_and_vocalizations: none"
                ),
            )
        )
    cursor = 0
    for line, event in zip(lines, events):
        match = _H3_CANONICAL_RECORD_RE.fullmatch(line)
        start = _h3_frame_at(event["start"], H3_NATIVE_FPS)
        end = _h3_frame_at(event["end"], H3_NATIVE_FPS)
        if match is None or start != cursor or not start < end <= published:
            raise H3CumulativePlanError(
                "Cumulative publication prompt frame coverage disagrees."
            )
        records.append((start + context, end + context, match.group("payload")))
        cursor = end
    if cursor != published:
        raise H3CumulativePlanError(
            "Cumulative publication prompt must cover its exact tail."
        )
    if padding:
        records.append(
            (
                context + published,
                context + published + padding,
                (
                    "shot_name: Unpublished padding | audiovisual_description: Hold "
                    "the established visual and audio state without a new authored "
                    "action or speech. | dialogue_and_vocalizations: none"
                ),
            )
        )
    fields[visual_field] = "\n".join(
        f"[Shot {index}] [{_h3_seconds(start, H3_NATIVE_FPS)}s-"
        f"{_h3_seconds(end, H3_NATIVE_FPS)}s] {payload}"
        for index, (start, end, payload) in enumerate(records, 1)
    )
    # Keep the publication compiler's other fields, including its local summary.
    return _restore_dialogue_exact(
        "\n\n".join(f"{name}: {fields[name]}".strip() for name in order),
        dialogue,
    )


def plan_h3_cumulative_chain(
    *,
    global_prompt: str,
    requested_frames: int,
    first_window_frames: int = H3_DEFAULT_MAX_WINDOW_FRAMES,
    max_extension_frames: int = H3_DEFAULT_MAX_EXTENSION_FRAMES,
) -> dict[str, Any]:
    """Compile one authored narrative for private native FL2VA cumulative AV.

    The first window is floored to the legal grid within the requested duration
    and caller-selected ceiling. Later tails are rounded once, with padding
    only on the terminal tail. Context is the retained 22-frame native cycle.
    This API does not admit references, opening guides, LoRAs or other model
    variants; their existing independent-segment contracts remain separate.
    """
    if type(global_prompt) is not str or not global_prompt.strip():
        raise H3CumulativePlanError("H3 cumulative source must be nonempty text.")
    try:
        source_bytes = global_prompt.encode("utf-8")
    except UnicodeError as exc:
        raise H3CumulativePlanError(
            "H3 cumulative source must be valid UTF-8."
        ) from exc
    if len(source_bytes) > _MAX_SOURCE_BYTES:
        raise H3CumulativePlanError(
            "H3 cumulative source exceeds the compiler byte limit."
        )
    if type(requested_frames) is not int or not 22 <= requested_frames <= 10_000_000:
        raise H3CumulativePlanError(
            "H3 cumulative duration must be an integer of at least 22 frames."
        )
    if (
        not is_legal_h3_video_frame_count(first_window_frames)
        or not 22 <= first_window_frames <= H3_DEFAULT_MAX_WINDOW_FRAMES
    ):
        raise H3CumulativePlanError(
            "H3 first-window ceiling must be legal and from 22 through 345 frames."
        )
    if (
        type(max_extension_frames) is not int
        or not 17 <= max_extension_frames <= H3_DEFAULT_MAX_EXTENSION_FRAMES
        or max_extension_frames % 17
    ):
        raise H3CumulativePlanError(
            "H3 extension ceiling must be a multiple of 17 through 119 frames."
        )
    first = min(first_window_frames, requested_frames)
    first -= (first - 5) % 17
    remaining = requested_frames - first
    # Bound planning before constructing potentially millions of step records.
    if (
        remaining + max_extension_frames - 1
    ) // max_extension_frames + 1 > _MAX_WINDOWS:
        raise H3CumulativePlanError(
            "H3 cumulative plan exceeds the bounded window count."
        )
    steps = (
        plan_h3_native_continuation_tail(
            remaining,
            context_frames=H3_DEFAULT_CONTEXT_FRAMES,
            max_extension_frames=max_extension_frames,
            absolute_context_start_frame=first - H3_DEFAULT_CONTEXT_FRAMES,
        ).steps
        if remaining
        else ()
    )
    generated = [first, *(step.extension_frames for step in steps)]
    published = [first, *(step.published_frames for step in steps)]
    try:
        # These counts are publication pieces, NOT sampler windows. The shared
        # compiler's event/dialogue ownership and source seal stay unchanged.
        shot_plan = plan_h3_native_shots(
            global_prompt=global_prompt,
            clip_frame_counts=generated,
            clip_requested_frames=published,
            fps=H3_NATIVE_FPS,
            source_canonicalization="t2va",
        )
        validate_h3_execution_shots(
            {"shot_plan": shot_plan},
            shot_plan["clip_prompts"],
            len(generated),
        )
    except (ValueError, QueueRecoveryRuntimeError) as exc:
        raise H3CumulativePlanError(str(exc)) from exc
    windows = []
    end = 0
    for index, (piece, output, prompt) in enumerate(
        zip(
            generated,
            published,
            shot_plan["clip_prompts"],
        )
    ):
        start = end
        end += output
        step = steps[index - 1] if index else None
        context = step.context_frames if step else 0
        trim = piece - output
        sampler_frames = context + piece
        window_prompt = _sampler_prompt(
            prompt, context=context, published=output, padding=trim
        )
        windows.append(
            {
                "index": index,
                "kind": "append" if step else "capture",
                "step": asdict(step) if step else None,
                "history_context_frames": context,
                "sampler_frames": sampler_frames,
                "generated_piece_frames": piece,
                "published_tail_frames": output,
                "publication_trim_frames": trim,
                "absolute_context_start_frame": start - context,
                "absolute_publish_start_frame": start,
                "absolute_publish_end_frame": end,
                "cumulative_generated_frames": end + trim,
                "cumulative_published_frames": end,
                "cumulative_generated_audio_ticks": audio_tick_at_frame(end + trim),
                "cumulative_published_audio_ticks": audio_tick_at_frame(end),
                "publication_prompt_sha256": _sha(prompt.encode("utf-8")),
                "sampler_prompt": window_prompt,
                "sampler_prompt_sha256": _sha(window_prompt.encode("utf-8")),
            }
        )
    plan = {
        "version": H3_CUMULATIVE_PLAN_VERSION,
        "mode": H3_NATIVE_CONTINUATION_MODE,
        "model_family": "fl2va",
        "fps": H3_NATIVE_FPS,
        "global_prompt": global_prompt,
        "source_sha256": _sha(source_bytes),
        "requested_frames": requested_frames,
        "first_window_frames": first_window_frames,
        "max_extension_frames": max_extension_frames,
        "history_context_frames": H3_DEFAULT_CONTEXT_FRAMES,
        "generated_piece_frames": generated,
        "published_piece_frames": published,
        "generated_frames": sum(generated),
        "published_frames": sum(published),
        "publication_trim_frames": sum(generated) - sum(published),
        "shot_plan": shot_plan,
        "windows": windows,
    }
    payload = _json_bytes(plan)
    plan["plan_sha256"] = _sha(payload)
    if len(_json_bytes(plan)) > _MAX_PLAN_BYTES:
        raise H3CumulativePlanError(
            "H3 cumulative plan exceeds the compiler byte limit."
        )
    return plan


def validate_h3_cumulative_plan(
    plan: dict[str, Any],
    *,
    expected_plan_sha256: str,
    expected_source_sha256: str,
) -> dict[str, Any]:
    """Replay exact bytes against independently supplied source and plan hashes.

    Return a new plan; never retain mutable input borrowed from a journal. A
    caller passing hashes read from that same journal provides no authority.
    Regenerating the entire expected contract also rejects re-sealed changes to
    geometry, prompt timing, ownership, source descriptors and unsupported keys.
    """
    if type(plan) is not dict:
        raise H3CumulativePlanError(
            "H3 cumulative plan must be a versioned JSON object."
        )
    payload = _json_bytes(plan)
    if len(payload) > _MAX_PLAN_BYTES:
        raise H3CumulativePlanError(
            "H3 cumulative plan exceeds the compiler byte limit."
        )
    if (
        type(expected_plan_sha256) is not str
        or type(expected_source_sha256) is not str
        or plan.get("plan_sha256") != expected_plan_sha256
        or plan.get("source_sha256") != expected_source_sha256
    ):
        raise H3CumulativePlanError(
            "H3 cumulative plan disagrees with trusted source or plan evidence."
        )
    required = {
        "global_prompt",
        "requested_frames",
        "first_window_frames",
        "max_extension_frames",
    }
    if not required <= plan.keys():
        raise H3CumulativePlanError("H3 cumulative compiler inputs are missing.")
    rebuilt = plan_h3_cumulative_chain(**{key: plan[key] for key in required})
    if payload != _json_bytes(rebuilt):
        raise H3CumulativePlanError(
            "H3 cumulative plan disagrees with deterministic replay."
        )
    return rebuilt
