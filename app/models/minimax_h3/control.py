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
