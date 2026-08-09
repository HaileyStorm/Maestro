# Copyright 2026 The MiniMax and Hugging Face teams.
# Copyright 2026 Maestro contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""MMGP-native MiniMax H3 transformer for compact and full checkpoints.

The released Comfy-Org checkpoints replace H3's large timestep MLP and AdaLN
inputs with a sampled eight-dimensional curve.  This implementation keeps the
checkpoint's fused QKV and SwiGLU projections intact so Maestro's FP8 loader can
stream them without first expanding or dequantizing the 21 GB transformer.
Full-width community checkpoints retain the original sinusoidal timestep MLP
and 2688-dimensional AdaLN input; that architecture is selected from the
checkpoint header before MMGP allocates the model.

Packing, modality tags, schedules, and rotary coordinates follow the official
Diffusers MiniMax H3 implementation pinned in ``UPSTREAM.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace

import torch
import torch.nn as nn
import torch.nn.functional as F


MODALITY_VIDEO = 0
MODALITY_TEXT = 1
MODALITY_AUDIO = 2
MODALITY_COUNT = 3

# A 10-second 480p H3 request contains well over 100,000 packed tokens.
# Projecting all of those tokens through the fused QKV and 2x-SwiGLU layers
# in one call creates 5-7 GB temporary tensors.  These projections are
# token-wise, so bounded chunks are mathematically equivalent and leave room
# for attention plus MMGP's streamed transformer blocks on consumer GPUs.
MINIMAX_H3_ACTIVATION_CHUNK_TOKENS = 8192


@dataclass
class MiniMaxH3TransformerOutput:
    sample: torch.Tensor
    audio_sample: torch.Tensor


def _weight_dtype(module: nn.Module, fallback: torch.dtype) -> torch.dtype:
    weight = getattr(module, "weight", None)
    dtype = getattr(weight, "dtype", None)
    if dtype is None or dtype == torch.uint8:
        return fallback
    return dtype


def _apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """Apply split-half RoPE to the leading rotary channels."""

    rotary_dim = cos.shape[-1]
    rotary, passthrough = x[..., :rotary_dim], x[..., rotary_dim:]
    first, second = rotary.chunk(2, dim=-1)
    rotated = torch.cat((-second, first), dim=-1)
    cos = cos.to(dtype=x.dtype, device=x.device)[None, :, None]
    sin = sin.to(dtype=x.dtype, device=x.device)[None, :, None]
    rotary = rotary * cos + rotated * sin
    return torch.cat((rotary, passthrough), dim=-1)


def _index_runs(indices: torch.Tensor) -> tuple[tuple[int, int, int], ...]:
    """Compress a token-to-curve map into contiguous broadcastable runs."""

    values, counts = torch.unique_consecutive(indices, return_counts=True)
    values = values.detach().cpu().tolist()
    counts = counts.detach().cpu().tolist()
    cursor = 0
    runs = []
    for value, count in zip(values, counts):
        end = cursor + int(count)
        runs.append((cursor, end, int(value)))
        cursor = end
    return tuple(runs)


def _spectrum_finalize_target_hidden(
    *,
    final_layer: nn.Module,
    target_hidden: torch.Tensor,
    curve: torch.Tensor,
    turbo_silu_t_emb: torch.Tensor | None,
    target_timestep_indices: torch.Tensor,
    num_condition_audio_rows: int,
    num_condition_video_rows: int,
    total_audio_rows: int,
    total_video_rows: int,
    audio_target_rows: int,
    return_dict: bool,
) -> MiniMaxH3TransformerOutput | tuple[torch.Tensor, torch.Tensor]:
    """Run fresh current-coordinate H3 heads on forecast target rows only."""
    from .spectrum import SpectrumStateError

    if target_hidden.shape[1] != (
        total_audio_rows - num_condition_audio_rows
        + total_video_rows - num_condition_video_rows
    ):
        raise SpectrumStateError("Spectrum target hidden rows no longer match H3 layout")
    # H3's inference FinalLayer may modulate its freshly normalized input in
    # place. Replay features include archived actual anchors, so never let a
    # current-coordinate head mutate the sealed hidden-feature history.
    headed = final_layer(
        target_hidden.clone(),
        curve,
        turbo_silu_t_emb,
        _index_runs(target_timestep_indices),
    )
    audio_hidden = headed[:, :audio_target_rows].to(torch.float32)
    video_hidden = headed[:, audio_target_rows:].to(torch.float32)
    audio_target = final_layer.audio_out(audio_hidden)
    video_target = final_layer.video_out(video_hidden)
    audio_output = audio_target.new_zeros(
        (audio_target.shape[0], total_audio_rows, audio_target.shape[-1])
    )
    video_output = video_target.new_zeros(
        (video_target.shape[0], total_video_rows, video_target.shape[-1])
    )
    audio_output[:, num_condition_audio_rows:] = audio_target
    video_output[:, num_condition_video_rows:] = video_target
    if not return_dict:
        return video_output, audio_output
    return MiniMaxH3TransformerOutput(video_output, audio_output)


def _modulate_by_runs(
    hidden_states: torch.Tensor,
    shift: torch.Tensor,
    scale: torch.Tensor,
    runs: tuple[tuple[int, int, int], ...],
) -> torch.Tensor:
    """Apply AdaLN without expanding shift and scale to every token."""

    # Inference owns this freshly-normalized tensor, so updating it in place
    # avoids another sequence x hidden-size allocation.  Keep an autograd-safe
    # path for the small numerical regression tests and downstream training.
    output = hidden_states if not torch.is_grad_enabled() else hidden_states.clone()
    for start, end, value in runs:
        row_scale = scale[value].to(device=output.device, dtype=output.dtype)
        row_shift = shift[value].to(device=output.device, dtype=output.dtype)
        output[:, start:end].mul_(1.0 + row_scale).add_(row_shift)
    return output


def _scale_by_runs(
    hidden_states: torch.Tensor,
    scale: torch.Tensor,
    runs: tuple[tuple[int, int, int], ...],
) -> torch.Tensor:
    """Apply a per-curve residual gate without a token-sized index_select."""

    output = hidden_states if not torch.is_grad_enabled() else hidden_states.clone()
    for start, end, value in runs:
        row_scale = scale[value].to(device=output.device, dtype=output.dtype)
        output[:, start:end].mul_(row_scale)
    return output


def _h3_turbo_lora_delta(
    lora_a: torch.Tensor,
    lora_b: torch.Tensor,
    silu_t_emb: torch.Tensor,
    *,
    device: torch.device,
) -> torch.Tensor:
    """Return the upstream curve-mode update ``(B @ A @ silu(t_emb).T).T``."""

    # CUDA's BF16 matmuls preserve the authored LoRA precision without
    # materializing each very tall B matrix in FP32. CPU is only used by
    # model-free regressions and lacks consistent BF16 GEMM support.
    compute_dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    a = lora_a.to(device=device, dtype=compute_dtype)
    b = lora_b.to(device=device, dtype=compute_dtype)
    temb = silu_t_emb.to(device=device, dtype=compute_dtype)
    from services.h3_turbo import h3_turbo_adaln_delta

    return h3_turbo_adaln_delta(a, b, temb)


def _make_h3_turbo_residual_hook(lora_a: torch.Tensor, lora_b: torch.Tensor):
    """Activation-space LoRA that is independent of packed/INT8 base weights."""
    def hook(_module, args, output):
        if not args or not torch.is_tensor(output):
            raise RuntimeError("H3 Turbo residual hook expected one tensor input/output")
        input_tensor = args[0]
        compute_dtype = torch.bfloat16 if output.device.type == "cuda" else torch.float32
        from services.h3_turbo import h3_turbo_residual_delta

        delta = h3_turbo_residual_delta(
            input_tensor.to(dtype=compute_dtype),
            lora_a.to(device=output.device, dtype=compute_dtype),
            lora_b.to(device=output.device, dtype=compute_dtype),
        )
        return output + delta.to(device=output.device, dtype=output.dtype)

    return hook


class MiniMaxH3RotaryEmbedding(nn.Module):
    def __init__(self, freq_dim: int = 16, theta: float = 10000.0):
        super().__init__()
        inv_freq = 1.0 / (theta ** (torch.arange(0, 2 * freq_dim, 2, dtype=torch.float32) / (2 * freq_dim)))
        # Consumer checkpoints include this tensor, so keep it persistent.
        self.register_buffer("inv_freq", inv_freq, persistent=True)

    def forward(self, positions: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        positions = positions.to(device=self.inv_freq.device, dtype=torch.float32)
        angles = positions.unsqueeze(-1) * self.inv_freq.view(1, 1, -1)
        temporal, vertical, horizontal = angles.unbind(dim=1)
        angles = torch.cat((temporal, vertical, horizontal), dim=-1)
        angles = torch.cat((angles, angles), dim=-1)
        return angles.cos(), angles.sin()


class MiniMaxH3TimeEmbedder(nn.Module):
    """Original full-width H3 sinusoidal timestep projection."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int,
        output_dim: int,
        *,
        dtype: torch.dtype = torch.float32,
    ):
        super().__init__()
        self.input_dim = input_dim
        self.proj_in = nn.Linear(input_dim, hidden_dim, bias=True, dtype=dtype)
        self.proj_out = nn.Linear(hidden_dim, output_dim, bias=True, dtype=dtype)
        self.proj_in._lock_dtype = dtype
        self.proj_out._lock_dtype = dtype

    def forward(self, timestep: torch.Tensor) -> torch.Tensor:
        half = self.input_dim // 2
        frequencies = torch.exp(
            -math.log(10000.0)
            * torch.arange(half, dtype=torch.float32, device=timestep.device)
            / half
        )
        angles = timestep.to(torch.float32).unsqueeze(1) * frequencies.unsqueeze(0)
        embedding = torch.cat((angles.cos(), angles.sin()), dim=-1)
        return self.proj_out(F.silu(self.proj_in(embedding)))


class MiniMaxH3Attention(nn.Module):
    def __init__(self, hidden_size: int, heads: int, head_dim: int, eps: float, dtype: torch.dtype):
        super().__init__()
        self.heads = heads
        self.head_dim = head_dim
        inner = heads * head_dim
        self.qkv_proj = nn.Linear(hidden_size, inner * 3, bias=False, dtype=dtype)
        self.q_norm = nn.RMSNorm(head_dim, eps=eps, dtype=dtype)
        self.k_norm = nn.RMSNorm(head_dim, eps=eps, dtype=dtype)
        self.out_proj = nn.Linear(inner, hidden_size, bias=False, dtype=dtype)

    def forward(
        self,
        hidden_states: torch.Tensor,
        rotary: tuple[torch.Tensor, torch.Tensor] | None = None,
        attention_mask: torch.Tensor | None = None,
        acceleration: dict | None = None,
    ) -> torch.Tensor:
        batch, length, _ = hidden_states.shape
        chunk_size = max(1, int(MINIMAX_H3_ACTIVATION_CHUNK_TOKENS))
        if length <= chunk_size:
            qkv = self.qkv_proj(hidden_states)
            query, key, value = qkv.chunk(3, dim=-1)
            query = self.q_norm(query.view(batch, length, self.heads, self.head_dim))
            key = self.k_norm(key.view(batch, length, self.heads, self.head_dim))
            value = value.view(batch, length, self.heads, self.head_dim)
            if rotary is not None:
                query = _apply_rope(query, *rotary)
                key = _apply_rope(key, *rotary)
        else:
            # Keep only Q/K/V themselves resident.  The fused projection,
            # normalization, and RoPE temporaries are bounded to one chunk.
            shape = (batch, length, self.heads, self.head_dim)
            query = key = value = None
            for start in range(0, length, chunk_size):
                end = min(length, start + chunk_size)
                qkv = self.qkv_proj(hidden_states[:, start:end])
                q_chunk, k_chunk, v_chunk = qkv.chunk(3, dim=-1)
                chunk_length = end - start
                q_chunk = self.q_norm(
                    q_chunk.view(batch, chunk_length, self.heads, self.head_dim)
                )
                k_chunk = self.k_norm(
                    k_chunk.view(batch, chunk_length, self.heads, self.head_dim)
                )
                v_chunk = v_chunk.view(batch, chunk_length, self.heads, self.head_dim)
                if rotary is not None:
                    cos, sin = rotary
                    q_chunk = _apply_rope(q_chunk, cos[start:end], sin[start:end])
                    k_chunk = _apply_rope(k_chunk, cos[start:end], sin[start:end])
                if query is None:
                    query = torch.empty(shape, device=q_chunk.device, dtype=q_chunk.dtype)
                    key = torch.empty(shape, device=k_chunk.device, dtype=k_chunk.dtype)
                    value = torch.empty(shape, device=v_chunk.device, dtype=v_chunk.dtype)
                query[:, start:end].copy_(q_chunk)
                key[:, start:end].copy_(k_chunk)
                value[:, start:end].copy_(v_chunk)
            assert query is not None and key is not None and value is not None
            qkv = q_chunk = k_chunk = v_chunk = None
        attended = None
        engine = acceleration.get("engine") if isinstance(acceleration, dict) else None
        if engine == "sage2":
            from services.h3_acceleration import maybe_sage2_attention
            attended = maybe_sage2_attention(
                query,
                key,
                value,
                attention_mask=attention_mask,
                tensor_layout="NHD",
                is_causal=False,
                allow_sdpa_fallback=False,
            )
        elif engine == "sol_attn":
            from services.h3_acceleration import maybe_sol_attention
            attended = maybe_sol_attention(
                query, key, value,
                attention_mask=attention_mask,
                step_index=int(acceleration.get("step_index", 0)),
                block_index=int(acceleration.get("block_index", 0)),
                tau=float(acceleration.get("tau", 1.0)),
                dense_steps=int(acceleration.get("dense_steps", 10)),
                dense_blocks=int(acceleration.get("dense_blocks", 2)),
                min_tokens=int(acceleration.get("min_tokens", 4096)),
                sink_tokens=int(acceleration.get("sink_tokens", 0)),
            )
        if attended is None:
            query = query.transpose(1, 2)
            key = key.transpose(1, 2)
            value = value.transpose(1, 2)
            if attention_mask is not None:
                attention_mask = attention_mask[None, None].to(device=query.device)
            attended = F.scaled_dot_product_attention(
                query,
                key,
                value,
                attn_mask=attention_mask,
                dropout_p=0.0,
                is_causal=False,
            )
            attended = attended.transpose(1, 2)
        query = key = value = qkv = None
        attended = attended.reshape(batch, length, self.heads * self.head_dim)
        return self.out_proj(attended)


class MiniMaxH3MLP(nn.Module):
    def __init__(self, hidden_size: int, ffn_dim: int, dtype: torch.dtype):
        super().__init__()
        self.fc1 = nn.Linear(hidden_size, ffn_dim * 2, bias=False, dtype=dtype)
        self.fc2 = nn.Linear(ffn_dim, hidden_size, bias=False, dtype=dtype)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        # The released H3/Comfy checkpoint stores the fused projection as
        # [gate, value].  Keeping that native order avoids a 14k x 10k tensor
        # rewrite while loading the quantized transformer.
        def project(rows: torch.Tensor) -> torch.Tensor:
            gate, value = self.fc1(rows).chunk(2, dim=-1)
            if not torch.is_grad_enabled():
                gate = F.silu(gate, inplace=True)
                gate.mul_(value)
                return self.fc2(gate)
            return self.fc2(value * F.silu(gate))

        length = hidden_states.shape[1]
        chunk_size = max(1, int(MINIMAX_H3_ACTIVATION_CHUNK_TOKENS))
        if length <= chunk_size:
            return project(hidden_states)

        output = torch.empty_like(hidden_states)
        for start in range(0, length, chunk_size):
            end = min(length, start + chunk_size)
            output[:, start:end].copy_(project(hidden_states[:, start:end]))
        return output


class MiniMaxH3AdaLNProjection(nn.Module):
    def __init__(
        self,
        curve_dim: int,
        hidden_size: int,
        outputs: int,
        modalities: int,
        dtype: torch.dtype,
        apply_silu: bool = False,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.outputs = outputs
        self.modalities = modalities
        self.apply_silu = apply_silu
        self.linear = nn.Linear(curve_dim, outputs * modalities * hidden_size, bias=True, dtype=dtype)
        # The compact curve checkpoint stores these projections in FP16, but
        # Comfy's reference curve path evaluates them in FP32.  Preserve the
        # compact storage dtype for MMGP and upcast only the tiny projection
        # while it is active; doing the multiply in FP16 compounds rounding
        # error coherently through all 50 transformer blocks.
        self.linear._lock_dtype = dtype

    def forward(
        self,
        curve: torch.Tensor,
        turbo_silu_t_emb: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, ...]:
        if self.apply_silu:
            curve = F.silu(curve)
        # Full-width INT8 ConvRot checkpoints replace this nn.Linear with a
        # specialized kernel module.  Calling it is essential: directly
        # passing its packed INT8 weight to F.linear is neither correct nor
        # supported. Compact curve checkpoints retain the precision-island
        # FP32 math below.
        if not isinstance(self.linear, nn.Linear):
            projected = self.linear(curve)
            projected = projected.view(
                curve.shape[0] * self.modalities,
                self.outputs * self.hidden_size,
            )
            return projected.chunk(self.outputs, dim=-1)
        weight = self.linear.weight.to(device=curve.device, dtype=torch.float32)
        bias = self.linear.bias
        if bias is not None:
            bias = bias.to(device=curve.device, dtype=torch.float32)
        projected = F.linear(curve.to(dtype=torch.float32), weight, bias)
        turbo_lora = getattr(self, "_h3_turbo_lora", None)
        if turbo_lora is not None:
            if turbo_silu_t_emb is None:
                raise RuntimeError("H3 Turbo AdaLN weights are active without the timestep grid")
            lora_a, lora_b = turbo_lora
            projected.add_(
                _h3_turbo_lora_delta(
                    lora_a,
                    lora_b,
                    turbo_silu_t_emb,
                    device=projected.device,
                ).to(dtype=projected.dtype)
            )
        projected = projected.view(curve.shape[0] * self.modalities, self.outputs * self.hidden_size)
        return projected.chunk(self.outputs, dim=-1)


class MiniMaxH3RefinerBlock(nn.Module):
    def __init__(self, hidden_size: int, heads: int, head_dim: int, ffn_dim: int, eps: float, dtype: torch.dtype):
        super().__init__()
        self.norm1 = nn.RMSNorm(hidden_size, eps=eps, dtype=dtype)
        self.norm2 = nn.RMSNorm(hidden_size, eps=eps, dtype=dtype)
        self.attn = MiniMaxH3Attention(hidden_size, heads, head_dim, eps, dtype)
        self.mlp = MiniMaxH3MLP(hidden_size, ffn_dim, dtype)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        hidden_states = hidden_states + self.attn(self.norm1(hidden_states))
        return hidden_states + self.mlp(self.norm2(hidden_states))


class MiniMaxH3TokenRefiner(nn.Module):
    def __init__(
        self,
        layers: int,
        hidden_size: int,
        heads: int,
        head_dim: int,
        ffn_dim: int,
        eps: float,
        dtype: torch.dtype,
    ):
        super().__init__()
        self.blocks = nn.ModuleList(
            [MiniMaxH3RefinerBlock(hidden_size, heads, head_dim, ffn_dim, eps, dtype) for _ in range(layers)]
        )
        self.final_norm = nn.RMSNorm(hidden_size, eps=eps, dtype=dtype)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        for block in self.blocks:
            hidden_states = block(hidden_states)
        return self.final_norm(hidden_states)


class MiniMaxH3Block(nn.Module):
    def __init__(
        self,
        hidden_size: int,
        heads: int,
        head_dim: int,
        ffn_dim: int,
        curve_dim: int,
        eps: float,
        dtype: torch.dtype,
        adaln_dtype: torch.dtype = torch.float16,
        apply_adaln_silu: bool = False,
    ):
        super().__init__()
        self.norm1 = nn.RMSNorm(hidden_size, eps=eps, dtype=dtype)
        self.norm2 = nn.RMSNorm(hidden_size, eps=eps, dtype=dtype)
        self.attn = MiniMaxH3Attention(hidden_size, heads, head_dim, eps, dtype)
        self.mlp = MiniMaxH3MLP(hidden_size, ffn_dim, dtype)
        self.adaln_proj = MiniMaxH3AdaLNProjection(
            curve_dim, hidden_size, 6, MODALITY_COUNT, adaln_dtype,
            apply_silu=apply_adaln_silu,
        )

    def forward(
        self,
        hidden_states: torch.Tensor,
        curve: torch.Tensor,
        turbo_silu_t_emb: torch.Tensor | None,
        adaln_runs: tuple[tuple[int, int, int], ...],
        rotary: tuple[torch.Tensor, torch.Tensor],
        attention_mask: torch.Tensor | None,
        acceleration: dict | None = None,
    ) -> torch.Tensor:
        shift_attn, scale_attn, gate_attn, shift_mlp, scale_mlp, gate_mlp = self.adaln_proj(
            curve, turbo_silu_t_emb
        )
        normed = _modulate_by_runs(self.norm1(hidden_states), shift_attn, scale_attn, adaln_runs)
        attn_output = _scale_by_runs(
            self.attn(normed, rotary, attention_mask, acceleration),
            gate_attn,
            adaln_runs,
        )
        if not torch.is_grad_enabled():
            hidden_states.add_(attn_output)
        else:
            hidden_states = hidden_states + attn_output
        del normed, attn_output
        normed = _modulate_by_runs(self.norm2(hidden_states), shift_mlp, scale_mlp, adaln_runs)
        mlp_output = _scale_by_runs(self.mlp(normed), gate_mlp, adaln_runs)
        if not torch.is_grad_enabled():
            hidden_states.add_(mlp_output)
            return hidden_states
        return hidden_states + mlp_output


class MiniMaxH3FinalLayer(nn.Module):
    def __init__(
        self,
        hidden_size: int,
        curve_dim: int,
        video_dim: int,
        audio_dim: int,
        eps: float,
        dtype: torch.dtype,
        adaln_dtype: torch.dtype = torch.float16,
        apply_adaln_silu: bool = False,
    ):
        super().__init__()
        self.norm = nn.RMSNorm(hidden_size, eps=eps, dtype=dtype)
        self.adaln_proj = MiniMaxH3AdaLNProjection(
            curve_dim, hidden_size, 2, 1, adaln_dtype,
            apply_silu=apply_adaln_silu,
        )
        self.video_out = nn.Linear(hidden_size, video_dim, bias=True, dtype=torch.float32)
        self.audio_out = nn.Linear(hidden_size, audio_dim, bias=True, dtype=torch.float32)
        # The output heads are the checkpoint's FP32 precision island.
        self.video_out._lock_dtype = torch.float32
        self.audio_out._lock_dtype = torch.float32

    def forward(
        self,
        hidden_states: torch.Tensor,
        curve: torch.Tensor,
        turbo_silu_t_emb: torch.Tensor | None,
        timestep_runs: tuple[tuple[int, int, int], ...],
    ) -> torch.Tensor:
        shift, scale = self.adaln_proj(curve, turbo_silu_t_emb)
        normed = self.norm(hidden_states)
        return _modulate_by_runs(normed, shift, scale, timestep_runs)


class MiniMaxH3Transformer(nn.Module):
    """MiniMax H3 transformer with checkpoint-selected timestep geometry."""

    def __init__(
        self,
        hidden_size: int = 5376,
        num_layers: int = 50,
        token_refiner_layers: int = 2,
        num_attention_heads: int = 56,
        attention_head_dim: int = 128,
        ffn_dim: int = 14336,
        video_channels: int = 24,
        audio_channels: int = 32,
        patch_size: tuple[int, int, int] = (1, 2, 2),
        text_dim: int = 5120,
        curve_grid: int | None = 1025,
        curve_dim: int = 8,
        timestep_input_dim: int = 256,
        time_embed_hidden_size: int = 5376,
        rope_freq_dim: int = 16,
        eps: float = 1e-5,
        dtype: torch.dtype = torch.bfloat16,
    ):
        super().__init__()
        video_patch_dim = video_channels * math.prod(patch_size)
        self.config = SimpleNamespace(
            hidden_size=hidden_size,
            num_layers=num_layers,
            patch_size=patch_size,
            in_channels=video_channels,
            audio_in_channels=audio_channels,
            text_dim=text_dim,
            curve_grid=curve_grid,
            curve_dim=curve_dim,
            full_timestep=curve_grid is None,
        )
        self.video_patch_proj = nn.Linear(video_patch_dim, hidden_size, bias=True, dtype=torch.float32)
        self.audio_patch_proj = nn.Linear(audio_channels, hidden_size, bias=True, dtype=torch.float32)
        # Input projections are also released and evaluated in FP32.
        self.video_patch_proj._lock_dtype = torch.float32
        self.audio_patch_proj._lock_dtype = torch.float32
        self.condition_proj = nn.Linear(text_dim, hidden_size, bias=True, dtype=dtype)
        self.use_adaln_curves = curve_grid is not None
        if self.use_adaln_curves:
            self.register_buffer(
                "adaln_t_table",
                torch.empty(curve_grid, curve_dim, dtype=torch.float32),
                persistent=True,
            )
        else:
            self.time_embedder = MiniMaxH3TimeEmbedder(
                timestep_input_dim,
                time_embed_hidden_size,
                curve_dim,
                dtype=torch.float32,
            )
        self.rope = MiniMaxH3RotaryEmbedding(rope_freq_dim)
        self.token_refiner = MiniMaxH3TokenRefiner(
            token_refiner_layers,
            hidden_size,
            num_attention_heads,
            attention_head_dim,
            ffn_dim,
            eps,
            dtype,
        )
        adaln_dtype = torch.float16 if self.use_adaln_curves else dtype
        apply_adaln_silu = not self.use_adaln_curves
        self.blocks = nn.ModuleList(
            [
                MiniMaxH3Block(
                    hidden_size,
                    num_attention_heads,
                    attention_head_dim,
                    ffn_dim,
                    curve_dim,
                    eps,
                    dtype,
                    adaln_dtype,
                    apply_adaln_silu,
                )
                for _ in range(num_layers)
            ]
        )
        self.final_layer = MiniMaxH3FinalLayer(
            hidden_size,
            curve_dim,
            video_patch_dim,
            audio_channels,
            eps,
            dtype,
            adaln_dtype,
            apply_adaln_silu,
        )
        self._interrupt = False
        # Plain attributes intentionally keep the managed LoRA/grid outside the
        # module state tree. MMGP owns backbone adapter streaming; registering
        # these large AdaLN B matrices would make profile unload restore paths
        # that do not exist on the compact base.
        object.__setattr__(self, "_h3_turbo_prepared", False)
        object.__setattr__(self, "_h3_turbo_active", False)
        object.__setattr__(self, "_h3_turbo_grid", None)
        object.__setattr__(self, "_h3_turbo_pending_adaln", None)
        object.__setattr__(self, "_h3_turbo_managed_lora_index", 0)
        object.__setattr__(self, "_h3_turbo_preprocess_call_index", 0)
        object.__setattr__(self, "_h3_turbo_managed_seen", False)
        object.__setattr__(self, "_h3_turbo_backbone_mode", "mmgp")
        object.__setattr__(self, "_h3_turbo_pending_residual", None)
        object.__setattr__(self, "_h3_turbo_residual_handles", [])

    def clear_h3_turbo(self) -> None:
        for handle in getattr(self, "_h3_turbo_residual_handles", []):
            handle.remove()
        for module in self.modules():
            if isinstance(module, MiniMaxH3AdaLNProjection):
                object.__setattr__(module, "_h3_turbo_lora", None)
        object.__setattr__(self, "_h3_turbo_prepared", False)
        object.__setattr__(self, "_h3_turbo_active", False)
        object.__setattr__(self, "_h3_turbo_grid", None)
        object.__setattr__(self, "_h3_turbo_pending_adaln", None)
        object.__setattr__(self, "_h3_turbo_managed_lora_index", 0)
        object.__setattr__(self, "_h3_turbo_preprocess_call_index", 0)
        object.__setattr__(self, "_h3_turbo_managed_seen", False)
        object.__setattr__(self, "_h3_turbo_backbone_mode", "mmgp")
        object.__setattr__(self, "_h3_turbo_pending_residual", None)
        object.__setattr__(self, "_h3_turbo_residual_handles", [])

    def prepare_h3_turbo(
        self,
        grid_path: str,
        *,
        lora_path: str | None = None,
        backbone_mode: str = "mmgp",
        managed_lora_index: int = 0,
    ) -> None:
        self.clear_h3_turbo()
        if backbone_mode not in {"mmgp", "residual_output"}:
            raise RuntimeError(f"Unknown H3 Turbo backbone mode: {backbone_mode}")
        if managed_lora_index < 0:
            raise RuntimeError("H3 Turbo managed LoRA index must be non-negative")
        object.__setattr__(self, "_h3_turbo_backbone_mode", backbone_mode)
        object.__setattr__(self, "_h3_turbo_managed_lora_index", int(managed_lora_index))
        if self.use_adaln_curves:
            from safetensors.torch import load_file

            grid_state = load_file(grid_path, device="cpu")
            if set(grid_state) != {"silu_t_emb_grid"}:
                raise RuntimeError("H3 Turbo timestep grid tensor key is invalid")
            grid = grid_state["silu_t_emb_grid"]
            if grid.dtype != torch.bfloat16 or tuple(grid.shape) != (1025, 2688):
                raise RuntimeError("H3 Turbo timestep grid must be BF16 [1025, 2688]")
            object.__setattr__(self, "_h3_turbo_grid", grid)
        if backbone_mode == "residual_output":
            if not lora_path:
                raise RuntimeError("H3 Turbo residual-output mode requires the managed LoRA path")
            try:
                from mmgp import safetensors2
                state_dict = safetensors2.torch_load_file(lora_path, writable_tensors=False)
            except ImportError:
                from safetensors.torch import load_file
                state_dict = load_file(lora_path, device="cpu")
            from services.h3_turbo import strip_and_capture_adaln, validate_runtime_state_dict

            validate_runtime_state_dict(state_dict)
            captured_adaln = strip_and_capture_adaln(state_dict) if self.use_adaln_curves else None
            residual = {}
            module_names = sorted({name.rsplit(".lora_", 1)[0] for name in state_dict})
            for module_name in module_names:
                residual[module_name] = (
                    state_dict.pop(f"{module_name}.lora_A.weight"),
                    state_dict.pop(f"{module_name}.lora_B.weight"),
                )
            if state_dict:
                raise RuntimeError("H3 Turbo residual-output capture left unexpected tensors")
            object.__setattr__(self, "_h3_turbo_pending_adaln", captured_adaln)
            object.__setattr__(self, "_h3_turbo_pending_residual", residual)
            object.__setattr__(self, "_h3_turbo_managed_seen", True)
        object.__setattr__(self, "_h3_turbo_prepared", True)

    def _ordinary_lora_module_specs(self):
        from .lora_affine import LoraModuleSpec

        specs = {}
        for name, module in self.named_modules():
            in_features = getattr(module, "in_features", None)
            out_features = getattr(module, "out_features", None)
            if not name or in_features is None or out_features is None:
                continue
            specs[name] = LoraModuleSpec(
                out_features=int(out_features),
                in_features=int(in_features),
                has_bias=getattr(module, "bias", None) is not None,
            )
        return specs

    def _preprocess_ordinary_lora(self, model_type: str, state_dict: dict) -> dict:
        from .lora_affine import normalize_h3_lora_state_dict

        return normalize_h3_lora_state_dict(
            model_type,
            state_dict,
            target_table=self.adaln_t_table if self.use_adaln_curves else None,
            module_specs=self._ordinary_lora_module_specs(),
        )

    def preprocess_loras(self, model_type: str, state_dict: dict) -> dict:
        if self._h3_turbo_prepared:
            if self._h3_turbo_backbone_mode == "residual_output":
                # Residual-output Turbo owns the complete managed LoRA itself;
                # request validation forbids stacking ordinary adapters here.
                return state_dict
            call_index = self._h3_turbo_preprocess_call_index
            object.__setattr__(self, "_h3_turbo_preprocess_call_index", call_index + 1)
            if call_index == self._h3_turbo_managed_lora_index:
                # This exact managed branch intentionally precedes all ordinary
                # name/shape conversion. Its 518-key validator and compact
                # AdaLN capture must observe the authored Turbo state verbatim.
                from services.h3_turbo import (
                    strip_and_capture_adaln,
                    validate_runtime_state_dict,
                )

                try:
                    validate_runtime_state_dict(state_dict)
                    captured = strip_and_capture_adaln(state_dict) if self.use_adaln_curves else None
                except Exception:
                    self.clear_h3_turbo()
                    raise
                object.__setattr__(self, "_h3_turbo_pending_adaln", captured)
                object.__setattr__(self, "_h3_turbo_managed_seen", True)
                return state_dict
        return self._preprocess_ordinary_lora(model_type, state_dict)

    def activate_h3_turbo(self) -> None:
        pending = self._h3_turbo_pending_adaln
        if not self._h3_turbo_prepared or not self._h3_turbo_managed_seen:
            self.clear_h3_turbo()
            raise RuntimeError("The managed H3 Turbo LoRA was not seen by the MMGP loader")
        if self._h3_turbo_backbone_mode == "residual_output":
            residual = self._h3_turbo_pending_residual
            if not residual:
                self.clear_h3_turbo()
                raise RuntimeError("H3 Turbo residual-output tensors are missing")
            modules = dict(self.named_modules())
            handles = []
            for module_name, (lora_a, lora_b) in residual.items():
                module = modules.get(module_name)
                if module is None:
                    for handle in handles:
                        handle.remove()
                    self.clear_h3_turbo()
                    raise RuntimeError(f"H3 Turbo residual target is missing: {module_name}")
                handles.append(module.register_forward_hook(_make_h3_turbo_residual_hook(lora_a, lora_b)))
            object.__setattr__(self, "_h3_turbo_residual_handles", handles)
        if not self.use_adaln_curves:
            # Original/full H3 keeps 2688-wide AdaLN projections. MMGP can own
            # all 259 pairs on ordinary weights; quantized residual-output mode
            # instead hooks each linear, including AdaLN. Neither needs the grid.
            object.__setattr__(self, "_h3_turbo_active", True)
            return
        if self._h3_turbo_grid is None or not pending:
            self.clear_h3_turbo()
            raise RuntimeError("H3 Turbo compact AdaLN tensors or timestep grid are missing")
        modules = dict(self.named_modules())
        for linear_name, weights in pending.items():
            projection_name = linear_name.removesuffix(".linear")
            projection = modules.get(projection_name)
            if not isinstance(projection, MiniMaxH3AdaLNProjection):
                self.clear_h3_turbo()
                raise RuntimeError(f"H3 Turbo AdaLN target is missing: {projection_name}")
            object.__setattr__(projection, "_h3_turbo_lora", weights)
        object.__setattr__(self, "_h3_turbo_active", True)

    def h3_turbo_runtime_state(self) -> dict[str, int | bool]:
        attached = sum(
            isinstance(module, MiniMaxH3AdaLNProjection)
            and getattr(module, "_h3_turbo_lora", None) is not None
            for module in self.modules()
        )
        return {
            "prepared": bool(self._h3_turbo_prepared),
            "active": bool(self._h3_turbo_active),
            "adaln_modules": int(attached),
            "curve_mode": bool(self.use_adaln_curves),
            "backbone_mode": str(self._h3_turbo_backbone_mode),
            "residual_modules": len(getattr(self, "_h3_turbo_residual_handles", [])),
        }

    def _curve_at(self, timestep: torch.Tensor, device: torch.device) -> torch.Tensor:
        if not self.use_adaln_curves:
            return self.time_embedder(timestep.to(device=device))
        table = self.adaln_t_table.to(device=device, dtype=torch.float32)
        position = timestep.to(device=device, dtype=torch.float32).clamp_(0.0, 1.0) * (table.shape[0] - 1)
        lower = position.floor().long().clamp_(max=table.shape[0] - 2)
        fraction = (position - lower).unsqueeze(-1)
        return torch.lerp(table.index_select(0, lower), table.index_select(0, lower + 1), fraction)

    def _turbo_silu_t_emb_at(
        self,
        timestep: torch.Tensor,
        device: torch.device,
    ) -> torch.Tensor | None:
        if not self._h3_turbo_active:
            return None
        if not self.use_adaln_curves:
            return None
        grid = self._h3_turbo_grid
        if grid is None:
            raise RuntimeError("H3 Turbo is active without its pinned timestep grid")
        # LarryVRH's companion grid is sampled at linspace(0, 1, 1025).
        # Interpolate in FP32 exactly like the pinned upstream node, then cast
        # only the few selected rows to the LoRA's authored BF16 precision.
        grid = grid.to(device=device)
        position = timestep.to(device=device, dtype=torch.float32).clamp(0.0, 1.0) * (grid.shape[0] - 1)
        lower = position.floor().long().clamp(max=grid.shape[0] - 2)
        fraction = (position - lower).unsqueeze(-1)
        selected = torch.lerp(
            grid.index_select(0, lower).float(),
            grid.index_select(0, lower + 1).float(),
            fraction,
        )
        return selected.to(torch.bfloat16)

    def forward(
        self,
        hidden_states: torch.Tensor,
        audio_hidden_states: torch.Tensor,
        encoder_hidden_states: torch.Tensor,
        timestep: torch.Tensor,
        timestep_indices: torch.Tensor,
        token_tags: torch.Tensor,
        position_ids: torch.Tensor,
        video_indices: torch.Tensor,
        audio_indices: torch.Tensor,
        text_indices: torch.Tensor,
        return_dict: bool = True,
        **_kwargs,
    ) -> MiniMaxH3TransformerOutput | tuple[torch.Tensor, torch.Tensor] | None:
        if self._interrupt:
            return None
        if hidden_states.shape[0] != 1:
            raise ValueError("MiniMax H3 currently supports batch size 1.")
        sequence_length = position_ids.shape[0]
        if position_ids.shape != (sequence_length, 3):
            raise ValueError("MiniMax H3 position_ids must have shape [sequence, 3].")
        device = hidden_states.device
        video_indices = video_indices.to(device=device, dtype=torch.long)
        audio_indices = audio_indices.to(device=device, dtype=torch.long)
        text_indices = text_indices.to(device=device, dtype=torch.long)
        timestep_indices = timestep_indices.to(device=device, dtype=torch.long)
        token_tags = token_tags.to(device=device, dtype=torch.long)

        spectrum_controller = _kwargs.get("h3_spectrum_controller")
        spectrum_phase = str(_kwargs.get("h3_spectrum_phase") or "")
        spectrum_step = int(_kwargs.get("h3_step_index") or 0)
        spectrum_context = tuple(_kwargs.get("h3_spectrum_context_signature") or ())
        spectrum_step_signature = tuple(
            _kwargs.get("h3_spectrum_step_signature") or ()
        )
        num_condition_video_rows = int(
            _kwargs.get("h3_spectrum_num_condition_video_rows") or 0
        )
        num_condition_audio_rows = int(
            _kwargs.get("h3_spectrum_num_condition_audio_rows") or 0
        )
        forecast_hidden = None
        if spectrum_controller is not None:
            target_audio_indices = audio_indices[num_condition_audio_rows:]
            target_video_indices = video_indices[num_condition_video_rows:]
            target_indices = torch.cat((target_audio_indices, target_video_indices))
            target_timestep_indices = timestep_indices.index_select(0, target_indices)
            if spectrum_phase == "replay":
                forecast_hidden = spectrum_controller.replay_feature(
                    spectrum_step,
                    context_signature=spectrum_context,
                    step_signature=spectrum_step_signature,
                )
            elif spectrum_phase == "capture" and not spectrum_controller.requires_actual(
                spectrum_step
            ):
                forecast_hidden = spectrum_controller.capture_feature(
                    spectrum_step,
                    context_signature=spectrum_context,
                    step_signature=spectrum_step_signature,
                    actual_call=None,
                )
            elif spectrum_phase != "capture":
                from .spectrum import SpectrumStateError
                raise SpectrumStateError("Unknown Spectrum H3 transformer phase")
        if forecast_hidden is not None:
            curve = self._curve_at(timestep, device)
            turbo_silu_t_emb = self._turbo_silu_t_emb_at(timestep, device)
            return _spectrum_finalize_target_hidden(
                final_layer=self.final_layer,
                target_hidden=forecast_hidden,
                curve=curve,
                turbo_silu_t_emb=turbo_silu_t_emb,
                target_timestep_indices=target_timestep_indices,
                num_condition_audio_rows=num_condition_audio_rows,
                num_condition_video_rows=num_condition_video_rows,
                total_audio_rows=audio_indices.numel(),
                total_video_rows=video_indices.numel(),
                audio_target_rows=target_audio_indices.numel(),
                return_dict=return_dict,
            )

        video_dtype = _weight_dtype(self.video_patch_proj, torch.float32)
        audio_dtype = _weight_dtype(self.audio_patch_proj, torch.float32)
        text_dtype = _weight_dtype(self.condition_proj, torch.bfloat16)
        video_embeds = self.video_patch_proj(hidden_states.to(dtype=video_dtype))
        audio_embeds = self.audio_patch_proj(audio_hidden_states.to(dtype=audio_dtype))
        text_embeds = self.condition_proj(encoder_hidden_states.to(dtype=text_dtype))
        text_embeds = self.token_refiner(text_embeds)

        packed = text_embeds.new_zeros((1, sequence_length, text_embeds.shape[-1]))
        packed.index_copy_(1, text_indices, text_embeds)
        packed.index_copy_(1, video_indices, video_embeds.to(packed.dtype))
        packed.index_copy_(1, audio_indices, audio_embeds.to(packed.dtype))

        curve = self._curve_at(timestep, device)
        turbo_silu_t_emb = self._turbo_silu_t_emb_at(timestep, device)
        adaln_indices = timestep_indices * MODALITY_COUNT + token_tags.clamp_min(0)
        adaln_runs = _index_runs(adaln_indices)
        timestep_runs = _index_runs(timestep_indices)
        rotary = self.rope(position_ids.to(device))
        attention_mask = None
        padding = token_tags < 0
        if bool(padding.any()):
            attention_mask = padding[:, None] == padding[None, :]

        acceleration = None
        attention_engine = str(_kwargs.get("h3_attention_engine") or "sdpa")
        if attention_engine == "sage2":
            acceleration = {"engine": "sage2"}
        elif attention_engine == "sol_attn":
            acceleration = {
                "engine": "sol_attn",
                "step_index": int(_kwargs.get("h3_step_index") or 0),
                "tau": float(_kwargs.get("h3_sol_tau") or 1.0),
                "dense_steps": int(_kwargs.get("h3_sol_dense_steps") or 10),
                "dense_blocks": int(_kwargs.get("h3_sol_dense_blocks") or 2),
                "min_tokens": int(_kwargs.get("h3_sol_min_tokens") or 4096),
                "sink_tokens": int(_kwargs.get("h3_sol_sink_tokens") or 0),
            }

        for block_index, block in enumerate(self.blocks):
            if self._interrupt:
                return None
            if acceleration is not None:
                acceleration["block_index"] = block_index
            packed = block(
                packed,
                curve,
                turbo_silu_t_emb,
                adaln_runs,
                rotary,
                attention_mask,
                acceleration,
            )

        if spectrum_controller is not None:
            target_hidden = torch.cat(
                (
                    packed.index_select(1, target_audio_indices),
                    packed.index_select(1, target_video_indices),
                ),
                dim=1,
            )
            spectrum_controller.capture_feature(
                spectrum_step,
                context_signature=spectrum_context,
                step_signature=spectrum_step_signature,
                actual_call=lambda: target_hidden,
            )

        packed = self.final_layer(packed, curve, turbo_silu_t_emb, timestep_runs)
        video_activations = packed.index_select(1, video_indices).to(torch.float32)
        audio_activations = packed.index_select(1, audio_indices).to(torch.float32)
        video_output = self.final_layer.video_out(video_activations)
        audio_output = self.final_layer.audio_out(audio_activations)
        if not return_dict:
            return video_output, audio_output
        return MiniMaxH3TransformerOutput(video_output, audio_output)
