"""Original H3 Fun Union control branch, before media/runtime admission.

Architecture: VideoX-Fun b0acf916c215705ac212fc51b2d9007bbc6df51b,
``minimax_h3_transformer3d_control.py`` (Apache-2.0).
Checkpoint: alibaba-pai/MiniMax-H3-Fun-Controlnet-Union,
6419c27ece80f330826ae4439fa9c5910c475ccf.

The released branch requires the base model's full timestep embedding. It
cannot consume a compact AdaLN curve or reconstruct one. This module does not
download weights, activate a profile, or advertise public Control execution.
"""

from collections.abc import Callable, Mapping
import math

import torch
from torch import nn
from torch.nn import functional as F

from .transformer import MiniMaxH3AdaLNProjection, MiniMaxH3Block


ORIGINAL_CONTROL_SHA256 = "919a48acb525dc8fc70287fcd94ec1f5e5e289a77f1df14d01099c6ce204eb02"
ORIGINAL_CONTROL_BYTES = 6_806_843_904


def load_original_control_branch(filename, *, interrupted: Callable[[], bool] | None = None):
    """Load an already acquired original checkpoint through native MMGP.

    Acquisition, model terms, GPU authority, base compatibility and residency
    remain the caller's responsibility. No URL resolution or download occurs.
    Identity capture and unchanged-file verification bracket the loader; the
    complete native adapter rejects missing, extra and incompatible tensors.
    """
    import os
    from pathlib import Path
    from accelerate import init_empty_weights
    from mmgp import offload
    from services.h3_runtime_binding import _hash_file

    def check_cancelled():
        if interrupted is not None and interrupted():
            raise InterruptedError("H3 Control checkpoint loading was cancelled")

    check_cancelled()
    path = Path(filename).resolve(strict=True)
    if (path.suffix != ".safetensors" or "-of-" in path.name
            or path.stat().st_size != ORIGINAL_CONTROL_BYTES):
        raise ValueError("H3 Control requires the pinned original safetensors checkpoint")

    def reject_external_map():
        # MMGP otherwise discovers this unsealed input beside the checkpoint.
        if os.path.lexists(str(path.with_suffix("")) + "_map.json"):
            raise ValueError("Original H3 Control does not accept an adjacent quantization map")

    reject_external_map()
    evidence = _hash_file(filename, interrupted)
    if evidence.sha256 != ORIGINAL_CONTROL_SHA256:
        raise ValueError("H3 Control checkpoint does not match the pinned original bytes")
    check_cancelled()
    with init_empty_weights(include_buffers=True):
        branch = MiniMaxH3ControlBranch(dtype=torch.bfloat16)

    def preprocess(state_dict, quantization_map, tied_weights_map):
        check_cancelled()
        reject_external_map()
        if quantization_map is not None or tied_weights_map is not None:
            raise ValueError("Original H3 Control does not accept quantization or tied-weight maps")
        return branch.adapt_original_state_dict(state_dict)

    def before_assignment(_model):
        check_cancelled()
        reject_external_map()

    offload.load_model_data(
        branch, evidence.resolved, writable_tensors=False,
        preprocess_sd=preprocess, default_dtype=torch.bfloat16,
        pre_load_callback=before_assignment,
    )
    check_cancelled()
    reject_external_map()
    evidence.verify()
    branch._model_dtype = torch.bfloat16
    branch.h3_control_checkpoint_sha256 = evidence.sha256
    return branch.eval().requires_grad_(False)


class _OriginalControlAdaLN(MiniMaxH3AdaLNProjection):
    """Keep the original branch's activate-FP32, project-BF16 arithmetic."""

    def forward(self, curve, turbo_silu_t_emb=None):
        if turbo_silu_t_emb is not None:
            raise ValueError("Original H3 Control does not accept Turbo timestep grids")
        projected = self.linear(F.silu(curve.float()).to(self.linear.weight.dtype))
        return projected.view(-1, self.outputs * self.hidden_size).chunk(self.outputs, dim=-1)


class MiniMaxH3ControlBranch(nn.Module):
    """Produce skips for injection *after* the addressed base blocks.

    Control rows must already be normalized, patchified and padded to the
    original 49-channel training layout. Audio rows receive no direct skip;
    later base attention can still mix video/text changes into audio.
    """

    def __init__(
        self, *, hidden_size=5376, num_attention_heads=56,
        attention_head_dim=128, ffn_dim=14336, time_embed_dim=2688,
        control_in_dim=49, patch_size=(1, 2, 2),
        control_blocks_places=(0, 10, 20, 30, 40),
        dtype=torch.bfloat16,
    ):
        super().__init__()
        places = tuple(control_blocks_places)
        if not places or any(type(p) is not int or p < 0 for p in places):
            raise ValueError("Control block positions must be nonnegative integers")
        if tuple(sorted(set(places))) != places:
            raise ValueError("Control block positions must be unique and increasing")
        self.control_blocks_places = places
        self.hidden_size = hidden_size
        self.time_embed_dim = time_embed_dim
        self.control_patch_dim = control_in_dim * math.prod(patch_size)
        self.control_proj_in = nn.Linear(self.control_patch_dim, hidden_size, dtype=torch.float32)
        self.control_proj_in._lock_dtype = torch.float32
        blocks = []
        for index in range(len(places)):
            block = MiniMaxH3Block(
                hidden_size, num_attention_heads, attention_head_dim,
                ffn_dim, time_embed_dim, 1e-5, dtype, dtype, True,
            )
            block.adaln_proj = _OriginalControlAdaLN(
                time_embed_dim, hidden_size, 6, 3, dtype, apply_silu=True,
            )
            if index == 0:
                block.before_proj = nn.Linear(hidden_size, hidden_size, dtype=dtype)
                nn.init.zeros_(block.before_proj.weight)
                nn.init.zeros_(block.before_proj.bias)
            block.after_proj = nn.Linear(hidden_size, hidden_size, dtype=dtype)
            nn.init.zeros_(block.after_proj.weight)
            nn.init.zeros_(block.after_proj.bias)
            blocks.append(block)
        self.control_blocks = nn.ModuleList(blocks)

    def adapt_original_state_dict(self, state_dict: Mapping[str, torch.Tensor]):
        """Map the complete original donor branch; reject 2.0/drift/missing data.

        Original Q/K/V are separate contiguous projections, never the head
        interleaving used by some base checkpoints. Do not mutate the input.
        """
        remaining = dict(state_dict)
        mapped = {}

        def take(key):
            try:
                value = remaining.pop(key)
            except KeyError as error:
                raise ValueError(f"Missing original H3 Control tensor: {key}") from error
            if not isinstance(value, torch.Tensor):
                raise ValueError(f"H3 Control tensor is not a Tensor: {key}")
            return value

        for suffix in ("weight", "bias"):
            key = f"control_proj_in.{suffix}"
            mapped[key] = take(key)
        for index in range(len(self.control_blocks)):
            prefix = f"control_blocks.{index}."
            direct = [
                "norm1.weight", "norm2.weight", "adaln_proj.linear.weight",
                "adaln_proj.linear.bias", "after_proj.weight", "after_proj.bias",
            ]
            if index == 0:
                direct += ["before_proj.weight", "before_proj.bias"]
            for suffix in direct:
                mapped[prefix + suffix] = take(prefix + suffix)
            for target, source in (
                ("attn.q_norm.weight", "attn.norm_q.weight"),
                ("attn.k_norm.weight", "attn.norm_k.weight"),
                ("attn.out_proj.weight", "attn.to_out.0.weight"),
                ("mlp.fc2.weight", "ff.net.2.weight"),
            ):
                mapped[prefix + target] = take(prefix + source)
            # Diffusers SwiGLU stores [value, gate]; Maestro stores [gate,
            # value]. A rename without the half swap changes the model.
            donor_fc1 = take(prefix + "ff.net.0.proj.weight")
            if donor_fc1.shape != self.control_blocks[index].mlp.fc1.weight.shape:
                raise ValueError(f"H3 Control shape mismatch for {prefix}SwiGLU")
            value, gate = donor_fc1.chunk(2, dim=0)
            mapped[prefix + "mlp.fc1.weight"] = torch.cat((gate, value), dim=0)
            qkv = [take(prefix + f"attn.to_{part}.weight") for part in ("q", "k", "v")]
            expected_qkv = self.control_blocks[index].attn.qkv_proj.weight.shape
            part_shape = (expected_qkv[0] // 3, expected_qkv[1])
            if any(tuple(part.shape) != part_shape for part in qkv):
                raise ValueError(f"H3 Control shape mismatch for {prefix}attn Q/K/V")
            mapped[prefix + "attn.qkv_proj.weight"] = torch.cat(qkv, dim=0)
        if remaining:
            raise ValueError(f"Unexpected original H3 Control tensors: {sorted(remaining)}")
        expected = self.state_dict()
        for key, tensor in mapped.items():
            if tensor.shape != expected[key].shape:
                raise ValueError(f"H3 Control shape mismatch for {key}: {tuple(tensor.shape)}")
        return mapped

    def forward(
        self, packed, control_rows, video_indices, audio_indices,
        curve, adaln_runs, rotary, attention_mask=None, *,
        interrupted: Callable[[], bool] | None = None, offload_hints=False,
    ):
        """Return an invocation-local hint map, or None on cancellation."""
        if packed.ndim != 3 or packed.shape[0] != 1 or packed.shape[-1] != self.hidden_size:
            raise ValueError("H3 Control requires one packed sequence with matching hidden width")
        if curve.ndim != 2 or curve.shape[-1] != self.time_embed_dim:
            raise ValueError("H3 Control requires the full base timestep embedding")
        for name, indices in (("video", video_indices), ("audio", audio_indices)):
            if indices.ndim != 1 or indices.dtype != torch.long:
                raise ValueError(f"H3 Control {name} indices must be a one-dimensional long tensor")
            if indices.numel() and (indices.min() < 0 or indices.max() >= packed.shape[1]):
                raise ValueError(f"H3 Control {name} indices are outside the packed sequence")
            if indices.unique().numel() != indices.numel():
                raise ValueError(f"H3 Control {name} indices contain duplicates")
        if torch.isin(video_indices, audio_indices).any():
            raise ValueError("H3 Control video and audio rows overlap")
        if control_rows.shape != (1, video_indices.numel(), self.control_patch_dim):
            raise ValueError("H3 Control rows must cover every packed video row in training layout")
        if interrupted is not None and interrupted():
            return None
        embeds = self.control_proj_in(control_rows.to(dtype=self.control_proj_in.weight.dtype))
        # index_copy returns a separate buffer: native inference blocks mutate
        # their input in place, so sharing the base packed buffer is unsafe.
        stream = packed.index_copy(1, video_indices, embeds.to(packed.dtype))
        hints = {}
        for index, block in enumerate(self.control_blocks):
            if interrupted is not None and interrupted():
                return None
            if index == 0:
                stream = block.before_proj(stream) + packed
            stream = block(stream, curve, None, adaln_runs, rotary, attention_mask)
            if interrupted is not None and interrupted():
                return None
            hint = block.after_proj(stream)
            hint = hint.index_fill(1, audio_indices, 0)
            if offload_hints:
                hint = hint.to("cpu")
            hints[self.control_blocks_places[index]] = hint
        return hints


def add_control_hint(hidden_states, hints, block_index, *, strength):
    """Add a control skip after one base block; zero strength is identity."""
    if isinstance(strength, bool) or not isinstance(strength, (int, float)):
        raise ValueError("H3 Control strength must be a finite number from 0 through 1")
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("H3 Control strength must be a finite number from 0 through 1")
    if strength == 0 or block_index not in hints:
        return hidden_states
    hint = hints[block_index]
    if hint.shape != hidden_states.shape:
        raise ValueError("H3 Control hint does not match the base packed sequence")
    return hidden_states + hint.to(device=hidden_states.device, dtype=hidden_states.dtype) * strength


@torch.inference_mode()
def encode_control_rows(
    control_video, *, encode_mode: Callable[[torch.Tensor], torch.Tensor],
    latents_mean, latents_std, height, width, num_frames,
    mask_video=None, inpaint_video=None, interrupted: Callable[[], bool] | None = None,
):
    """Prepare original Union's 196-column target rows from unit-range pixels.

    The caller owns VAE residency and the live GPU lease. ``encode_mode`` must
    encode the normalized RGB input and return the raw posterior *mode* using
    that owned VAE; sampled keyframe/reference encoders are incompatible.
    No asset acquisition, model loading, cropping of latents, or RNG occurs.
    This text+control preparation covers target video rows only. Keyframes,
    timeline guides and semantic references require separate qualification.
    """
    from .packing import (
        MINIMAX_H3_PIXEL_MEAN, MINIMAX_H3_PIXEL_STD,
        patchify_video_latents, video_latent_num_frames,
    )

    if type(num_frames) is not int or not 5 <= num_frames <= 345 or num_frames % 17 != 5:
        raise ValueError("Original H3 Control frames must follow 17*n+5, from 5 through 345")
    if any(type(size) is not int or size < 32 or size % 32 for size in (height, width)):
        raise ValueError("Original H3 Control canvas must have positive multiples of 32")
    if inpaint_video is not None and mask_video is None:
        raise ValueError("H3 Control inpaint source requires a mask")
    if not callable(encode_mode):
        raise ValueError("H3 Control requires an owned deterministic mode encoder")

    def validate_pixels(pixels, channels, name):
        if not isinstance(pixels, torch.Tensor) or pixels.ndim != 5:
            raise ValueError(f"H3 Control {name} must be a BCTHW tensor")
        if pixels.shape[:2] != (1, channels) or any(size == 0 for size in pixels.shape[2:]):
            raise ValueError(f"H3 Control {name} has invalid batch/channel/video geometry")
        if not pixels.is_floating_point() or not torch.isfinite(pixels).all():
            raise ValueError(f"H3 Control {name} must contain finite unit-range float pixels")
        if pixels.min() < 0 or pixels.max() > 1:
            raise ValueError(f"H3 Control {name} pixels must be from 0 through 1")

    validate_pixels(control_video, 3, "video")
    if mask_video is not None:
        validate_pixels(mask_video, 1, "mask")
    if inpaint_video is not None:
        validate_pixels(inpaint_video, 3, "inpaint source")
    device = control_video.device
    mean = torch.as_tensor(latents_mean, device=device, dtype=torch.float32)
    std = torch.as_tensor(latents_std, device=device, dtype=torch.float32)
    if mean.shape != (24,) or std.shape != (24,) or not torch.isfinite(mean).all():
        raise ValueError("H3 Control requires the VAE's finite 24-channel latent statistics")
    if not torch.isfinite(std).all() or not (std > 0).all():
        raise ValueError("H3 Control latent standard deviations must be finite and positive")
    mean, std = mean.view(1, 24, 1, 1, 1), std.view(1, 24, 1, 1, 1)
    pixel_mean = torch.tensor(MINIMAX_H3_PIXEL_MEAN, device=device).view(1, 3, 1, 1, 1)
    pixel_std = torch.tensor(MINIMAX_H3_PIXEL_STD, device=device).view(1, 3, 1, 1, 1)
    expected_grid = (video_latent_num_frames(num_frames), height // 16, width // 16)

    def cancelled():
        return interrupted is not None and interrupted()

    def fit(pixels):
        pixels = pixels.to(device=device, dtype=torch.float32)
        if pixels.shape[2] < num_frames:
            tail = pixels[:, :, -1:].expand(-1, -1, num_frames - pixels.shape[2], -1, -1)
            pixels = torch.cat((pixels, tail), dim=2)
        else:
            pixels = pixels[:, :, :num_frames]
        if pixels.shape[-2:] != (height, width):
            frames = F.interpolate(pixels[0].permute(1, 0, 2, 3),
                                   size=(height, width), mode="bilinear", align_corners=False)
            pixels = frames.permute(1, 0, 2, 3)[None]
        return pixels

    def encode(pixels):
        latent = encode_mode((pixels - pixel_mean) / pixel_std)
        if not isinstance(latent, torch.Tensor) or latent.shape != (1, 24, *expected_grid):
            raise ValueError("H3 Control VAE mode does not match the target latent clock/canvas")
        if not latent.is_floating_point() or not torch.isfinite(latent).all():
            raise ValueError("H3 Control VAE mode must contain finite float latents")
        return (latent.to(device=device, dtype=torch.float32) - mean) / std

    if cancelled():
        return None
    control_latents = encode(fit(control_video))
    if cancelled():
        return None
    control_rows = patchify_video_latents(control_latents, (1, 2, 2))
    if mask_video is None:
        # Training's pure-generation layout has 25 zero latent channels.
        return F.pad(control_rows, (0, 100)).unsqueeze(0)
    # Harden both before and after bilinear fitting, as in the training path.
    mask = (fit((mask_video > 0.5).float()) > 0.5).float()
    visible = 1 - mask
    masked_pixels = (fit(inpaint_video) * visible if inpaint_video is not None
                     else torch.zeros_like(visible.expand(-1, 3, -1, -1, -1)))
    if cancelled():
        return None
    masked_latents = encode(masked_pixels)
    if cancelled():
        return None
    visibility = F.interpolate(visible, size=expected_grid, mode="trilinear", align_corners=False)
    visibility_rows = patchify_video_latents(visibility, (1, 2, 2))
    masked_rows = patchify_video_latents(masked_latents, (1, 2, 2))
    return torch.cat((control_rows, visibility_rows, masked_rows), dim=-1).unsqueeze(0)
