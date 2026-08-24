"""Experimental MiniMax H3 cumulative-append continuation geometry.

Contract sources (independently reimplemented; no source code copied):

* ComfyUI ``MiniMaxH3AddGuide`` at revision
  https://github.com/Comfy-Org/ComfyUI/blob/e01fb4c56b7a88149d469b99cbbfe3223d715054/comfy_extras/nodes_minimax_h3.py
* ``ComfyUI-Minimax-H3-Continuation`` at revision
  https://github.com/ttulttul/ComfyUI-Minimax-H3-Continuation/blob/e1768d5fdfc6f9519d2090dcf78458c2d9625f80/continuation_nodes.py

This module plans integer geometry only.  It does not build masks, move
tensors, decode media, or modify Maestro's separate 18-frame decoded-boundary
policy.  Audio spans use cumulative absolute boundaries; they must not be
substituted for a chunked-context contract that rounds each clip locally.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Final, Literal


H3_NATIVE_CONTINUATION_MODE: Final = "cumulative_append"
H3_NATIVE_FPS: Final = 24
H3_AUDIO_TICKS_PER_SECOND: Final = 40
H3_VIDEO_FRAME_STEP: Final = 17
H3_VIDEO_FRAME_OFFSET: Final = 5
H3_VIDEO_LATENT_STEP: Final = 5
H3_VIDEO_LATENT_OFFSET: Final = 2
H3_DEFAULT_CONTEXT_FRAMES: Final = 22
H3_DEFAULT_MAX_EXTENSION_FRAMES: Final = 119
H3_DEFAULT_MAX_WINDOW_FRAMES: Final = 345

_MAX_FRAME_POSITION: Final = 10_000_000

ContinuationMode = Literal["cumulative_append"]


class H3NativeContinuationError(ValueError):
    """Raised when experimental continuation geometry is contradictory."""


def _integer(
    value: object,
    *,
    name: str,
    minimum: int,
    maximum: int = _MAX_FRAME_POSITION,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise H3NativeContinuationError(
            f"{name} must be an integer from {minimum} through {maximum}."
        )
    return value


def is_legal_h3_video_frame_count(value: object) -> bool:
    """Return whether ``value`` is on the native ``17*n+5`` video grid."""

    return (
        type(value) is int
        and H3_VIDEO_FRAME_OFFSET <= value <= _MAX_FRAME_POSITION
        and value % H3_VIDEO_FRAME_STEP == H3_VIDEO_FRAME_OFFSET
    )


def _context_frames(value: object) -> int:
    if not is_legal_h3_video_frame_count(value):
        raise H3NativeContinuationError(
            "context_frames must be at least 5 and satisfy the 17*n+5 "
            "native H3 video grid."
        )
    return int(value)


def _extension_frames(value: object, *, name: str = "extension_frames") -> int:
    if (
        type(value) is not int
        or value < H3_VIDEO_FRAME_STEP
        or value > _MAX_FRAME_POSITION
        or value % H3_VIDEO_FRAME_STEP
    ):
        raise H3NativeContinuationError(
            f"{name} must be at least 17 and a multiple of 17."
        )
    return value


def _absolute_context_start_frame(value: object) -> int:
    start = _integer(
        value,
        name="absolute_context_start_frame",
        minimum=0,
    )
    if start % H3_VIDEO_FRAME_STEP:
        raise H3NativeContinuationError(
            "absolute_context_start_frame must preserve the native 17-frame "
            "cumulative cycle."
        )
    return start


def _max_window_frames(value: object) -> int:
    if (
        not is_legal_h3_video_frame_count(value)
        or int(value) < H3_VIDEO_FRAME_OFFSET + H3_VIDEO_FRAME_STEP
    ):
        raise H3NativeContinuationError(
            "max_window_frames must be at least 22 and satisfy the 17*n+5 "
            "native H3 video grid."
        )
    return int(value)


def _opening_guide(value: object) -> bool:
    if type(value) is not bool:
        raise H3NativeContinuationError("opening_guide_present must be a boolean.")
    if value:
        raise H3NativeContinuationError(
            "opening guide contradicts native continuation context."
        )
    return value


def latent_frames_for_video_frames(frame_count: object) -> int:
    """Map one legal ``17*n+5`` pixel-frame count to ``5*n+2`` latents."""

    if not is_legal_h3_video_frame_count(frame_count):
        raise H3NativeContinuationError(
            "frame_count must be at least 5 and satisfy the 17*n+5 native "
            "H3 video grid."
        )
    chunks = (int(frame_count) - H3_VIDEO_FRAME_OFFSET) // H3_VIDEO_FRAME_STEP
    return H3_VIDEO_LATENT_OFFSET + chunks * H3_VIDEO_LATENT_STEP


def audio_tick_at_frame(frame_position: object) -> int:
    """Round one absolute 24-fps frame boundary onto H3's 40-Hz clock."""

    frame = _integer(frame_position, name="frame_position", minimum=0)
    return round(Fraction(frame * H3_AUDIO_TICKS_PER_SECOND, H3_NATIVE_FPS))


def audio_ticks_between_frames(start_frame: object, end_frame: object) -> int:
    """Return an exact audio span by subtracting rounded absolute boundaries."""

    start = _integer(start_frame, name="start_frame", minimum=0)
    end = _integer(end_frame, name="end_frame", minimum=start)
    return audio_tick_at_frame(end) - audio_tick_at_frame(start)


@dataclass(frozen=True)
class H3NativeContinuationStep:
    """One legal cumulative-append sampling step and its publication tail."""

    index: int
    absolute_context_start_frame: int
    context_frames: int
    extension_frames: int
    target_frames: int
    context_latent_frames: int
    extension_latent_frames: int
    target_latent_frames: int
    context_audio_ticks: int
    extension_audio_ticks: int
    target_audio_ticks: int
    absolute_publish_start_frame: int
    absolute_publish_end_frame: int
    published_frames: int
    published_audio_ticks: int
    publication_trim_frames: int = 0
    max_window_frames: int = H3_DEFAULT_MAX_WINDOW_FRAMES
    mode: ContinuationMode = H3_NATIVE_CONTINUATION_MODE

    def __post_init__(self) -> None:
        index = _integer(self.index, name="step index", minimum=1)
        context_start = _absolute_context_start_frame(
            self.absolute_context_start_frame
        )
        context = _context_frames(self.context_frames)
        extension = _extension_frames(self.extension_frames)
        target = context + extension
        if self.target_frames != target:
            raise H3NativeContinuationError(
                "target_frames must equal context_frames plus extension_frames."
            )
        window_cap = _max_window_frames(self.max_window_frames)
        if target > window_cap:
            raise H3NativeContinuationError(
                "target_frames must not exceed max_window_frames."
            )

        context_latents = latent_frames_for_video_frames(context)
        target_latents = latent_frames_for_video_frames(target)
        extension_latents = (extension // H3_VIDEO_FRAME_STEP) * H3_VIDEO_LATENT_STEP
        if (
            self.context_latent_frames != context_latents
            or self.extension_latent_frames != extension_latents
            or self.target_latent_frames != target_latents
            or context_latents + extension_latents != target_latents
        ):
            raise H3NativeContinuationError(
                "video latent overlap and appended tail do not reconcile exactly."
            )

        publish_start = context_start + context
        target_end = context_start + target
        context_audio = audio_ticks_between_frames(context_start, publish_start)
        extension_audio = audio_ticks_between_frames(publish_start, target_end)
        target_audio = audio_ticks_between_frames(context_start, target_end)
        if (
            self.context_audio_ticks != context_audio
            or self.extension_audio_ticks != extension_audio
            or self.target_audio_ticks != target_audio
            or context_audio + extension_audio != target_audio
        ):
            raise H3NativeContinuationError(
                "audio overlap and appended tail do not reconcile on the absolute clock."
            )

        trim = _integer(
            self.publication_trim_frames,
            name="publication_trim_frames",
            minimum=0,
            maximum=extension - 1,
        )
        published = extension - trim
        publish_end = publish_start + published
        if (
            self.absolute_publish_start_frame != publish_start
            or self.absolute_publish_end_frame != publish_end
            or self.published_frames != published
            or self.published_audio_ticks
            != audio_ticks_between_frames(publish_start, publish_end)
        ):
            raise H3NativeContinuationError(
                "publication tail does not match its absolute frame boundaries."
            )
        if self.mode != H3_NATIVE_CONTINUATION_MODE:
            raise H3NativeContinuationError(
                "native continuation mode must be cumulative_append."
            )
        object.__setattr__(self, "index", index)

    @property
    def discard_video_latent_frames(self) -> int:
        """Exact sampled head to discard before appending the video suffix."""

        return self.context_latent_frames

    @property
    def append_video_latent_frames(self) -> int:
        """Exact generated video suffix to append."""

        return self.extension_latent_frames

    @property
    def discard_audio_ticks(self) -> int:
        """Exact sampled audio head to discard before appending the suffix."""

        return self.context_audio_ticks

    @property
    def append_audio_ticks(self) -> int:
        """Exact generated audio suffix to append."""

        return self.extension_audio_ticks


@dataclass(frozen=True)
class H3NativeContinuationPlan:
    """A bounded sequence of legal steps plus an explicit final trim."""

    requested_extension_frames: int
    generated_extension_frames: int
    publication_trim_frames: int
    context_frames: int
    max_extension_frames: int
    absolute_context_start_frame: int
    steps: tuple[H3NativeContinuationStep, ...]
    max_window_frames: int = H3_DEFAULT_MAX_WINDOW_FRAMES
    mode: ContinuationMode = H3_NATIVE_CONTINUATION_MODE

    def __post_init__(self) -> None:
        requested = _integer(
            self.requested_extension_frames,
            name="requested_extension_frames",
            minimum=1,
        )
        context = _context_frames(self.context_frames)
        maximum = _extension_frames(
            self.max_extension_frames,
            name="max_extension_frames",
        )
        window_cap = _max_window_frames(self.max_window_frames)
        context_start = _absolute_context_start_frame(
            self.absolute_context_start_frame
        )
        steps = tuple(self.steps)
        if not steps:
            raise H3NativeContinuationError(
                "native continuation plan must contain at least one step."
            )
        if any(step.mode != H3_NATIVE_CONTINUATION_MODE for step in steps):
            raise H3NativeContinuationError(
                "all native continuation steps must use cumulative_append."
            )
        expected_start = context_start
        for position, step in enumerate(steps, start=1):
            if (
                step.index != position
                or step.context_frames != context
                or step.extension_frames > maximum
                or step.max_window_frames != window_cap
                or step.absolute_context_start_frame != expected_start
            ):
                raise H3NativeContinuationError(
                    "native continuation steps are not a contiguous legal sequence."
                )
            expected_start += step.extension_frames

        generated = sum(step.extension_frames for step in steps)
        published = sum(step.published_frames for step in steps)
        trim = generated - published
        if (
            self.generated_extension_frames != generated
            or self.publication_trim_frames != trim
            or published != requested
            or any(step.publication_trim_frames for step in steps[:-1])
            or steps[-1].publication_trim_frames != trim
        ):
            raise H3NativeContinuationError(
                "generated extension and final publication trim do not reconcile."
            )
        if self.mode != H3_NATIVE_CONTINUATION_MODE:
            raise H3NativeContinuationError(
                "native continuation mode must be cumulative_append."
            )


def _build_step(
    context_frames: int,
    extension_frames: int,
    *,
    index: int,
    absolute_context_start_frame: int,
    publication_trim_frames: int,
    max_window_frames: int,
) -> H3NativeContinuationStep:
    context = _context_frames(context_frames)
    extension = _extension_frames(extension_frames)
    context_start = _absolute_context_start_frame(absolute_context_start_frame)
    trim = _integer(
        publication_trim_frames,
        name="publication_trim_frames",
        minimum=0,
        maximum=extension - 1,
    )
    window_cap = _max_window_frames(max_window_frames)
    target = context + extension
    if target > window_cap:
        raise H3NativeContinuationError(
            "target_frames must not exceed max_window_frames."
        )
    context_latents = latent_frames_for_video_frames(context)
    target_latents = latent_frames_for_video_frames(target)
    extension_latents = target_latents - context_latents
    publish_start = context_start + context
    target_end = context_start + target
    publish_end = target_end - trim
    return H3NativeContinuationStep(
        index=index,
        absolute_context_start_frame=context_start,
        context_frames=context,
        extension_frames=extension,
        target_frames=target,
        context_latent_frames=context_latents,
        extension_latent_frames=extension_latents,
        target_latent_frames=target_latents,
        context_audio_ticks=audio_ticks_between_frames(context_start, publish_start),
        extension_audio_ticks=audio_ticks_between_frames(publish_start, target_end),
        target_audio_ticks=audio_ticks_between_frames(context_start, target_end),
        absolute_publish_start_frame=publish_start,
        absolute_publish_end_frame=publish_end,
        published_frames=extension - trim,
        published_audio_ticks=audio_ticks_between_frames(publish_start, publish_end),
        publication_trim_frames=trim,
        max_window_frames=window_cap,
    )


def plan_h3_native_continuation_step(
    context_frames: object,
    extension_frames: object,
    *,
    absolute_context_start_frame: object = 0,
    opening_guide_present: object = False,
    max_window_frames: object = H3_DEFAULT_MAX_WINDOW_FRAMES,
) -> H3NativeContinuationStep:
    """Plan one exact cumulative-append step without aligning either input."""

    _opening_guide(opening_guide_present)
    context = _context_frames(context_frames)
    extension = _extension_frames(extension_frames)
    context_start = _absolute_context_start_frame(absolute_context_start_frame)
    window_cap = _max_window_frames(max_window_frames)
    return _build_step(
        context,
        extension,
        index=1,
        absolute_context_start_frame=context_start,
        publication_trim_frames=0,
        max_window_frames=window_cap,
    )


def plan_h3_native_continuation_tail(
    requested_extension_frames: object,
    *,
    context_frames: object = H3_DEFAULT_CONTEXT_FRAMES,
    max_extension_frames: object = H3_DEFAULT_MAX_EXTENSION_FRAMES,
    absolute_context_start_frame: object = 0,
    opening_guide_present: object = False,
    max_window_frames: object = H3_DEFAULT_MAX_WINDOW_FRAMES,
) -> H3NativeContinuationPlan:
    """Plan legal steps covering an arbitrary published extension length.

    ``requested_extension_frames`` is only the new suffix to publish; it
    excludes the already-existing context and is not a final movie duration.
    Only that requested publication length may be off-grid.  Sampling is
    rounded up once to the next 17-frame extension boundary, and the excess is
    represented as the final step's explicit publication trim.
    """

    _opening_guide(opening_guide_present)
    requested = _integer(
        requested_extension_frames,
        name="requested_extension_frames",
        minimum=1,
    )
    context = _context_frames(context_frames)
    maximum = _extension_frames(
        max_extension_frames,
        name="max_extension_frames",
    )
    window_cap = _max_window_frames(max_window_frames)
    available_extension = window_cap - context
    if available_extension < H3_VIDEO_FRAME_STEP:
        raise H3NativeContinuationError(
            "context_frames leaves no legal extension inside max_window_frames."
        )
    effective_maximum = min(maximum, available_extension)
    context_start = _absolute_context_start_frame(absolute_context_start_frame)

    generated = (
        (requested + H3_VIDEO_FRAME_STEP - 1) // H3_VIDEO_FRAME_STEP
    ) * H3_VIDEO_FRAME_STEP
    trim = generated - requested
    remaining = generated
    extensions: list[int] = []
    while remaining > effective_maximum:
        extensions.append(effective_maximum)
        remaining -= effective_maximum
    extensions.append(remaining)

    steps: list[H3NativeContinuationStep] = []
    step_context_start = context_start
    for position, extension in enumerate(extensions, start=1):
        step_trim = trim if position == len(extensions) else 0
        steps.append(
            _build_step(
                context,
                extension,
                index=position,
                absolute_context_start_frame=step_context_start,
                publication_trim_frames=step_trim,
                max_window_frames=window_cap,
            )
        )
        step_context_start += extension

    return H3NativeContinuationPlan(
        requested_extension_frames=requested,
        generated_extension_frames=generated,
        publication_trim_frames=trim,
        context_frames=context,
        max_extension_frames=effective_maximum,
        absolute_context_start_frame=context_start,
        steps=tuple(steps),
        max_window_frames=window_cap,
    )


__all__ = [
    "H3NativeContinuationError",
    "H3NativeContinuationPlan",
    "H3NativeContinuationStep",
    "H3_AUDIO_TICKS_PER_SECOND",
    "H3_DEFAULT_CONTEXT_FRAMES",
    "H3_DEFAULT_MAX_EXTENSION_FRAMES",
    "H3_DEFAULT_MAX_WINDOW_FRAMES",
    "H3_NATIVE_CONTINUATION_MODE",
    "H3_NATIVE_FPS",
    "audio_tick_at_frame",
    "audio_ticks_between_frames",
    "is_legal_h3_video_frame_count",
    "latent_frames_for_video_frames",
    "plan_h3_native_continuation_step",
    "plan_h3_native_continuation_tail",
]
