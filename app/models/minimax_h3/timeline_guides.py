"""Native conditioning for source-bound arbitrary-frame H3 guides.

Geometry and condition-row noise follow MiniMaxH3AddGuide at ComfyUI commit
e01fb4c56b7a88149d469b99cbbfe3223d715054. This private tensor handoff is
separate from semantic Ref2VA references and from the public Gallery adapter.
It does not read paths, decode files, or confer project/GPU authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import torch

from services.h3_guide_plan import validate_h3_guide_plan
from .packing import (
    MINIMAX_H3_KEYFRAME_NOISE_AUG,
    patchify_video_latents,
    video_latent_num_frames,
)


@dataclass(frozen=True)
class H3TimelineGuideMedia:
    # RGB CTHW in [-1, 1] and stereo 32 kHz waveform, both CPU float32.
    visual: torch.Tensor | None = None
    waveform: torch.Tensor | None = None


@dataclass(frozen=True)
class H3TimelineGuidePayload:
    plan: dict
    media: tuple[H3TimelineGuideMedia, ...]


@dataclass(frozen=True)
class H3TimelineGuideRows:
    video: torch.Tensor | None
    audio: torch.Tensor | None
    video_anchors: tuple
    audio_anchors: tuple
    condition_order: tuple[tuple[int, int], ...]


def validate_timeline_guide_payload(
    payload: H3TimelineGuidePayload,
    *,
    frame_num: int,
    height: int,
    width: int,
) -> dict:
    """Validate decoded shapes against the exact immutable geometry plan."""
    if type(payload) is not H3TimelineGuidePayload:
        raise ValueError("H3 timeline guides require the private decoded-media handoff")
    plan = validate_h3_guide_plan(payload.plan)
    if plan["target_frames"] != frame_num:
        raise ValueError("H3 timeline guide target differs from the sampling request")
    if type(payload.media) is not tuple or len(payload.media) != len(plan["guides"]):
        raise ValueError("H3 timeline guide media order differs from the plan")
    if type(height) is not int or type(width) is not int or min(height, width) < 32 or height % 32 or width % 32:
        raise ValueError("H3 timeline guide canvas must use positive multiples of 32")
    total_bytes = 0
    for guide, media in zip(plan["guides"], payload.media):
        if type(media) is not H3TimelineGuideMedia:
            raise ValueError("H3 timeline guide media handoff is invalid")
        for name, tensor in (("visual", media.visual), ("audio", media.waveform)):
            expected = guide[name]
            if (tensor is None) != (expected is None):
                raise ValueError("H3 timeline guide modalities differ from the plan")
            if tensor is None:
                continue
            if not isinstance(tensor, torch.Tensor) or tensor.device.type != "cpu" or tensor.dtype != torch.float32:
                raise ValueError("H3 timeline guides require CPU float32 decoded media")
            total_bytes += tensor.numel() * tensor.element_size()
            if total_bytes > 2 * 1024**3:
                raise ValueError("H3 timeline guide decoded media exceeds the 2 GiB limit")
            if name == "visual":
                if tuple(tensor.shape) != (3, expected["original_frame_count"], height, width):
                    raise ValueError("H3 timeline guide visual shape differs from its plan or canvas")
            elif tensor.ndim != 2 or tensor.shape[0] != 2 or tensor.shape[-1] < 1 or (tensor.shape[-1] + 799) // 800 != expected["original_tick_count"]:
                raise ValueError("H3 timeline guide waveform differs from its 32 kHz stereo plan")
            if not torch.isfinite(tensor).all() or tensor.abs().max() > 1:
                raise ValueError("H3 timeline guide decoded media must be finite and normalized")
    return plan


def encode_timeline_guides(
    payload: H3TimelineGuidePayload,
    *,
    frame_num: int,
    height: int,
    width: int,
    patch_size: tuple[int, int, int],
    seed: int,
    device: torch.device,
    encode_video: Callable,
    encode_audio: Callable,
    interrupted: Callable[[], bool],
) -> H3TimelineGuideRows:
    """Encode legal source prefixes and return fixed conditioning rows.

Overlapping guides remain separate ordered conditions, as upstream does.
They never replace or lock target pixels/audio. Each visual guide restarts
the same CPU RNG stream; target-noise and legacy condition RNGs are untouched.
Audio guide rows are clean, at timestep 1.0.
"""
    plan = validate_timeline_guide_payload(payload, frame_num=frame_num, height=height, width=width)
    if patch_size != (1, 2, 2):
        raise ValueError("H3 timeline guides require the native 1x2x2 patch")
    videos, audios, video_anchors, audio_anchors, condition_order = [], [], [], [], []
    for guide, media in zip(plan["guides"], payload.media):
        if interrupted():
            raise InterruptedError("H3 timeline guide encoding was cancelled")
        frame_index = guide["resolved_frame_idx"]
        expected_latents = ticks = 0
        if media.visual is not None:
            frames = guide["visual"]["used_frame_count"]
            pixels = media.visual[:, :frames].clone()
            latent = encode_video(pixels, keep_all_latents=True)
            expected_latents = 1 if frames == 1 else video_latent_num_frames(frames)
            if tuple(latent.shape) != (1, 24, expected_latents, height // 16, width // 16):
                raise ValueError("H3 timeline guide video VAE geometry differs from the plan")
            rows = patchify_video_latents(latent.float().cpu(), patch_size)
            noise = torch.randn(rows.shape, generator=torch.Generator("cpu").manual_seed(seed), device="cpu", dtype=torch.float32)
            rows = MINIMAX_H3_KEYFRAME_NOISE_AUG * rows + (1.0 - MINIMAX_H3_KEYFRAME_NOISE_AUG) * noise
            videos.append(rows.to(device))
            video_anchors.append(("clip", expected_latents, frame_index))
        if interrupted():
            raise InterruptedError("H3 timeline guide encoding was cancelled")
        if media.waveform is not None:
            latent = encode_audio(media.waveform.clone())
            original_ticks = guide["audio"]["original_tick_count"]
            if tuple(latent.shape) != (2, 32, original_ticks):
                raise ValueError("H3 timeline guide audio VAE geometry differs from the plan")
            ticks = guide["audio"]["used_tick_count"]
            # Channel-major rows, preserving each channel's full interval.
            rows = latent[..., :ticks].permute(0, 2, 1).reshape(2 * ticks, 32)
            audios.append(rows.float().to(device))
            audio_anchors.append(("frame", ticks, frame_index))
        condition_order.append((expected_latents, ticks))
    if interrupted():
        raise InterruptedError("H3 timeline guide encoding was cancelled")
    return H3TimelineGuideRows(
        torch.cat(videos) if videos else None,
        torch.cat(audios) if audios else None,
        tuple(video_anchors), tuple(audio_anchors),
        tuple(condition_order),
    )
