"""Private crop-video initialization for Base H3; no public media admission.

The long-grid tail and sampler-only frame hold follow H3InjectVideoLatent /
H3PerFrameDenoise in Carasibana/ComfyUI-H3-FaceRefine, nodes.py at
d8521d14fe0d721d80cd9417fff5a559cbc21aba. Native H3 uses t=1-sigma.
The caller owns crop provenance, decoding, residency and GPU authority.
"""

from dataclasses import dataclass
import math
import os

import torch
from torch.nn import functional as F

from .packing import (
    MINIMAX_H3_PIXEL_MEAN, MINIMAX_H3_PIXEL_STD,
    patchify_video_latents, video_latent_num_frames,
)


@dataclass(frozen=True)
class H3FaceRefinePayload:
    # Exact decoded RGB BCTHW, CPU float32 in [0,1]. No resize/pad/trim.
    video: torch.Tensor
    strength: float
    # Explicit reviewed multipliers in decoded frame order; no content inference.
    frame_multipliers: tuple[float, ...]


def validate_face_refine_request(
    payload, *, frame_num, height, width, fps, sampling_steps,
    reference_mode, model_type, custom_settings, conditioning, kwargs,
):
    """Capture an immutable private request before encoding or RNG draws."""
    if os.environ.get("MAESTRO_H3_FACE_REFINE_EXPERIMENTAL") != "1":
        raise ValueError("H3 FaceRefine requires the private experimental sampler gate")
    if type(payload) is not H3FaceRefinePayload:
        raise ValueError("H3 FaceRefine requires the private decoded-media handoff")
    if (reference_mode or model_type not in ("", "minimax_h3") or fps != 24
            or type(custom_settings) is not dict
            or set(custom_settings) != {"h3_attention_engine"}
            or type(custom_settings["h3_attention_engine"]) is not str
            or custom_settings["h3_attention_engine"] != "sdpa"
            or any(value is not None for value in conditioning)
            or any(kwargs.get(key) for key in (
                "video_prompt_type", "audio_prompt_type", "prefix_frames_count",
                "activated_loras", "skip_steps_cache_type", "tea_cache",
                "h3_native_boundary_conditioning",
            ))
            or any(key.startswith("_h3_cumulative_") for key in kwargs)
            or kwargs.get("guidance_scale", 1.0) != 1.0
            or type(kwargs.get("batch_size", 1)) is not int or kwargs.get("batch_size", 1) != 1
            or type(kwargs.get("repeat_generation", 1)) is not int or kwargs.get("repeat_generation", 1) != 1
            or (isinstance(kwargs.get("multi_clip_info"), dict)
                and kwargs["multi_clip_info"].get("total", 1) != 1)):
        raise ValueError("H3 FaceRefine requires an independent dense Base crop request")
    if (type(frame_num) is not int or not 124 <= frame_num <= 345 or frame_num % 17 != 5
            or any(type(size) is not int or not 32 <= size <= 1536 or size % 32
                   for size in (height, width))):
        raise ValueError("H3 FaceRefine requires an exact 17n+5 frame clock and native canvas")
    if type(sampling_steps) is not int or not 2 <= sampling_steps <= 100:
        raise ValueError("H3 FaceRefine requires 2 through 100 model evaluations")
    strength = payload.strength
    if (type(strength) not in (int, float) or not math.isfinite(strength)
            or not sampling_steps / 4096 <= strength <= 1):
        raise ValueError("H3 FaceRefine strength must be positive with a bounded long grid")
    multipliers = payload.frame_multipliers
    if (type(multipliers) is not tuple or len(multipliers) != frame_num
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   or not 0 <= value <= 1 for value in multipliers)):
        raise ValueError("H3 FaceRefine requires one finite unit-range multiplier per frame")
    video = payload.video
    if (not isinstance(video, torch.Tensor) or video.device.type != "cpu"
            or video.dtype != torch.float32 or video.requires_grad
            or tuple(video.shape) != (1, 3, frame_num, height, width)
            or video.numel() * video.element_size() > 512 * 1024**2):
        raise ValueError("H3 FaceRefine requires bounded exact CPU float32 decoded crops")
    if not torch.isfinite(video).all() or video.min() < 0 or video.max() > 1:
        raise ValueError("H3 FaceRefine crops must contain finite unit-range RGB")
    return H3FaceRefinePayload(video.detach().clone(), float(strength),
                               tuple(float(value) for value in multipliers))


def encode_face_refine_rows(payload, *, device, encode_mode, latents_mean,
                            latents_std, interrupted):
    """Deterministic loaded-VAE mode, using its statistics and exact lattice."""
    if interrupted():
        return None
    video = payload.video.to(device=device)
    mean = torch.as_tensor(latents_mean, dtype=torch.float32, device=device)
    std = torch.as_tensor(latents_std, dtype=torch.float32, device=device)
    if (mean.shape != (24,) or std.shape != (24,) or not torch.isfinite(mean).all()
            or not torch.isfinite(std).all() or not (std > 0).all()):
        raise ValueError("H3 FaceRefine requires finite native 24-channel VAE statistics")
    pixel_mean = torch.tensor(MINIMAX_H3_PIXEL_MEAN, device=device).view(1, 3, 1, 1, 1)
    pixel_std = torch.tensor(MINIMAX_H3_PIXEL_STD, device=device).view(1, 3, 1, 1, 1)
    latent = encode_mode((video - pixel_mean) / pixel_std)
    if interrupted():
        return None
    frames, height, width = video.shape[2:]
    grid = (video_latent_num_frames(frames), height // 16, width // 16)
    if (not isinstance(latent, torch.Tensor) or tuple(latent.shape) != (1, 24, *grid)
            or not latent.is_floating_point() or not torch.isfinite(latent).all()):
        raise ValueError("H3 FaceRefine VAE mode must match the exact latent clock/canvas")
    latent = (latent.to(device=device, dtype=torch.float32)
              - mean.view(1, 24, 1, 1, 1)) / std.view(1, 24, 1, 1, 1)
    return patchify_video_latents(latent, (1, 2, 2))


def configure_face_refine_schedule(scheduler, *, steps, strength, device):
    """Build a longer full shifted grid, then keep N evaluations plus zero.

    Slicing a short shifted grid gives excessive noise even at low strength.
    Only the video clock changes; independently generated audio keeps its
    ordinary full grid and is discarded by the source-audio compositor.
    """
    full_steps = int(steps / strength)
    scheduler.set_timesteps(full_steps + 1, device=device)
    sigmas = scheduler.sigmas[-(steps + 1):].detach().clone()
    if len(sigmas) != steps + 1:
        raise ValueError("H3 FaceRefine long grid lost required evaluations")
    scheduler.set_timesteps(sigmas=sigmas, device=device)
    return sigmas


def face_refine_row_multipliers(payload, *, device):
    # Upstream explicitly maps decoded -> latent frames linearly, including
    # endpoints. H3 packing is frame-major; one scalar covers each spatial row.
    frames, height, width = payload.video.shape[2:]
    latent_frames = video_latent_num_frames(frames)
    values = torch.tensor(payload.frame_multipliers, dtype=torch.float32,
                          device=device).view(1, 1, -1)
    values = F.interpolate(values, size=latent_frames, mode="linear", align_corners=True)
    spatial_rows = (height // 32) * (width // 32)
    return values.reshape(-1, 1).repeat_interleave(spatial_rows, dim=0)


def hold_face_refine_rows(proposed, *, clean, noise, multipliers, next_sigma):
    """Keep source rows at the actual next clock, not at conditioning time."""
    if (proposed.shape != clean.shape or noise.shape != clean.shape
            or multipliers.shape != (clean.shape[0], 1)):
        raise RuntimeError("H3 FaceRefine source hold changed target-row geometry")
    held = (1 - next_sigma) * clean + next_sigma * noise
    # torch.where makes hard holds and full edits exact, including endpoints.
    blended = multipliers * proposed + (1 - multipliers) * held
    return torch.where(multipliers == 0, held,
                       torch.where(multipliers == 1, proposed, blended))
