"""Normalized AV tensor primitives for experimental H3 cumulative append.

This is independent Maestro code implementing the geometry in
``h3_native_continuation``. It does not sample, decode, cache, persist, or enable
a continuation mode. The retained prefix is never replaced by sampled context.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch

from services.h3_native_continuation import (
    H3NativeContinuationError,
    H3NativeContinuationStep,
    audio_tick_at_frame,
    is_legal_h3_video_frame_count,
    latent_frames_for_video_frames,
)

_DEFAULT_MAX_OUTPUT_BYTES = 512 * 1024 * 1024


def _check_output_budget(output_bytes: int, max_output_bytes: int) -> None:
    if type(max_output_bytes) is not int or max_output_bytes < 1:
        raise H3NativeContinuationError("max_output_bytes must be a positive integer.")
    if output_bytes > max_output_bytes:
        raise H3NativeContinuationError("Cumulative AV state exceeds max_output_bytes.")


def _tensor(value: object, shape: tuple[int, ...], name: str) -> None:
    if (
        not isinstance(value, torch.Tensor)
        or tuple(value.shape) != shape
        or value.layout != torch.strided
        or value.device.type == "meta"
        or not value.is_floating_point()
    ):
        raise H3NativeContinuationError(
            f"{name} must be a real floating-point tensor with shape {shape}."
        )


def _video_shape(video: object, latent_frames: int) -> tuple[int, ...]:
    if not isinstance(video, torch.Tensor) or video.ndim != 5:
        raise H3NativeContinuationError("video must have shape [1, 24, T, H, W].")
    height, width = video.shape[-2:]
    if height < 2 or width < 2 or height % 2 or width % 2:
        raise H3NativeContinuationError(
            "video latent canvas must be positive and even."
        )
    return (1, 24, latent_frames, height, width)


@dataclass(frozen=True)
class H3CumulativeLatents:
    """Complete normalized generated state, with separate publication length.

    Video is [1,24,T,H,W]; stereo audio is [2,32,T]. Tensors are borrowed,
    not made immutable by this record. Operations below do not mutate them.
    A final publication trim retains the whole sampled state and ends the chain.
    """

    video: torch.Tensor
    audio: torch.Tensor
    frame_count: int
    published_frames: int | None = None

    def __post_init__(self) -> None:
        if not is_legal_h3_video_frame_count(self.frame_count):
            raise H3NativeContinuationError(
                "frame_count must satisfy the H3 17*n+5 grid."
            )
        _tensor(
            self.video,
            _video_shape(self.video, latent_frames_for_video_frames(self.frame_count)),
            "video",
        )
        _tensor(self.audio, (2, 32, audio_tick_at_frame(self.frame_count)), "audio")
        published = (
            self.frame_count if self.published_frames is None else self.published_frames
        )
        if type(published) is not int or not 1 <= published <= self.frame_count:
            raise H3NativeContinuationError(
                "published_frames must be within generated frame_count."
            )
        object.__setattr__(self, "published_frames", published)


@dataclass(frozen=True)
class H3CumulativeContext:
    """Detached, independently owned normalized guide tensors for one step."""

    video: torch.Tensor
    audio: torch.Tensor
    step: H3NativeContinuationStep


def _validate_step(
    previous: H3CumulativeLatents, step: H3NativeContinuationStep
) -> None:
    if not isinstance(previous, H3CumulativeLatents) or not isinstance(
        step, H3NativeContinuationStep
    ):
        raise H3NativeContinuationError(
            "previous state and continuation step are required."
        )
    # Borrowed tensors can have been resized since the record was created.
    previous.__post_init__()
    if previous.published_frames != previous.frame_count:
        raise H3NativeContinuationError(
            "A publication-trimmed chain cannot accept another append."
        )
    if step.absolute_publish_start_frame != previous.frame_count:
        raise H3NativeContinuationError(
            "Continuation context must end at the previous frame boundary."
        )
    if step.context_latent_frames > previous.video.shape[2]:
        raise H3NativeContinuationError(
            "Continuation context exceeds retained video state."
        )


@torch.no_grad()
def extract_h3_cumulative_context(
    previous: H3CumulativeLatents,
    step: H3NativeContinuationStep,
    *,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> H3CumulativeContext:
    """Copy the synchronized tail using the step's absolute audio clock."""
    _validate_step(previous, step)
    video = previous.video[:, :, -step.context_latent_frames :]
    audio = previous.audio[..., -step.context_audio_ticks :]
    _check_output_budget(
        video.numel() * video.element_size() + audio.numel() * audio.element_size(),
        max_output_bytes,
    )
    return H3CumulativeContext(
        video=video.detach().clone(),
        audio=audio.detach().clone(),
        step=step,
    )


@torch.no_grad()
def append_h3_cumulative_window(
    previous: H3CumulativeLatents,
    sampled_video: torch.Tensor,
    sampled_audio: torch.Tensor,
    step: H3NativeContinuationStep,
    *,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> H3CumulativeLatents:
    """Discard sampled context and append only the generated AV suffix.

    The byte limit bounds the returned tensors, not total process peak memory.
    Final frame/sample trimming belongs to publication after decoding; it must
    never truncate normalized latent state onto an illegal VAE grid.
    """
    _validate_step(previous, step)
    _tensor(
        sampled_video,
        (1, 24, step.target_latent_frames, *previous.video.shape[-2:]),
        "sampled_video",
    )
    _tensor(sampled_audio, (2, 32, step.target_audio_ticks), "sampled_audio")
    for name, retained, sampled in (
        ("video", previous.video, sampled_video),
        ("audio", previous.audio, sampled_audio),
    ):
        if retained.device != sampled.device or retained.dtype != sampled.dtype:
            raise H3NativeContinuationError(
                f"Sampled {name} must preserve retained device and dtype."
            )
    output_frames = previous.frame_count + step.extension_frames
    video_elements = (
        24
        * latent_frames_for_video_frames(output_frames)
        * previous.video.shape[-2]
        * previous.video.shape[-1]
    )
    audio_elements = 2 * 32 * audio_tick_at_frame(output_frames)
    output_bytes = (
        video_elements * previous.video.element_size()
        + audio_elements * previous.audio.element_size()
    )
    _check_output_budget(output_bytes, max_output_bytes)
    return H3CumulativeLatents(
        video=torch.cat(
            (previous.video, sampled_video[:, :, step.context_latent_frames :]), dim=2
        ),
        audio=torch.cat(
            (previous.audio, sampled_audio[..., step.context_audio_ticks :]), dim=-1
        ),
        frame_count=output_frames,
        published_frames=step.absolute_publish_end_frame,
    )
