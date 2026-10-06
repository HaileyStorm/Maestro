# Copyright 2026 Alibaba PAI and VideoX-Fun contributors.
# Copyright 2026 Maestro contributors.
# Licensed under the Apache License, Version 2.0.
# https://www.apache.org/licenses/LICENSE-2.0
"""Coupled interval-head PDD math, adapted from upstream/main@194cd36a.

The final projections stay FP32, including their interval fusion. Backbone
LoRAs use Maestro's existing shape/name/affine conversion and MMGP lifecycle.
"""
from __future__ import annotations

import math
import torch
from torch import nn
from torch.nn import functional as F

PDD_NUM_INTERVALS = 32
PDD_BLOCK_SIZE = 4
PDD_NUM_EVALUATIONS = 8
PDD_VIDEO_SHIFT = 12.0
PDD_AUDIO_SHIFT = 3.0


def pdd_sigmas(shift):
    base = torch.linspace(1.0, 0.0, 9, dtype=torch.float64, device="cpu")
    return shift * base / (1 + (shift - 1) * base)


def pdd_sampling_plans_for_sigmas(sigmas, shift):
    actual = torch.as_tensor(sigmas, dtype=torch.float64, device="cpu").flatten()
    if (actual.numel() != 9 or not torch.isfinite(actual).all()
            or actual[0] != 1 or actual[-1] != 0
            or not (actual[:-1] > actual[1:]).all()):
        raise ValueError("PDD requires eight descending sigma intervals from one to zero.")
    fine = torch.linspace(1.0, 0.0, 33, dtype=torch.float64, device="cpu")
    times = 1 - shift * fine / (1 + (shift - 1) * fine)
    runtime_times = 1 - actual
    plans = []
    for start, end in zip(runtime_times[:-1], runtime_times[1:]):
        overlap = (torch.minimum(times[1:], end) - torch.maximum(times[:-1], start)).clamp_min(0)
        plan = overlap / (end - start)
        if not torch.isclose(plan.sum(), torch.tensor(1.0, dtype=torch.float64, device="cpu"), atol=1e-7):
            raise ValueError("PDD runtime interval is not covered by the authored heads.")
        plans.append(plan)
    return torch.stack(plans)


def validate_head_banks(state, final_layer):
    result = {}
    for prefix, head in (("proj_out", final_layer.video_out),
                         ("audio_proj_out", final_layer.audio_out)):
        for suffix, base in (("weight", head.weight), ("bias", head.bias)):
            tensor = state.get(prefix + "." + suffix)
            if (base is None or not isinstance(tensor, torch.Tensor)
                    or tuple(tensor.shape) != (32, *base.shape)
                    or tensor.dtype not in (torch.float32, torch.bfloat16)
                    or not torch.isfinite(tensor).all()):
                raise ValueError("PDD interval-head bank does not match the original output head.")
            result[prefix + "." + suffix] = tensor.detach().cpu().float().clone()
    return result


class PDDParallelHead(nn.Module):
    def __init__(self, base, weight_bank, bias_bank, plans, strength):
        super().__init__()
        self.base = base
        self._lock_dtype = torch.float32
        self.strength = strength
        self.step = None
        self.configure(weight_bank, bias_bank, plans)

    @property
    def weight(self):
        return self.base.weight

    @property
    def bias(self):
        return self.base.bias

    @property
    def in_features(self):
        return self.base.in_features

    @property
    def out_features(self):
        return self.base.out_features

    def configure(self, weights, biases, plans):
        # MMGP may leave CUDA as the default device; authored fusion stays CPU FP32.
        plans = plans.to(device="cpu", dtype=torch.float32)
        weights = weights.to(device="cpu", dtype=torch.float32)
        biases = biases.to(device="cpu", dtype=torch.float32)
        self.fused_weights = torch.einsum("sn,noi->soi", plans, weights)
        self.fused_biases = torch.einsum("sn,no->so", plans, biases)
        self.step = None

    def forward(self, hidden):
        if self.step is None:
            raise RuntimeError("PDD heads require a coupled master-clock step.")
        weight = self.fused_weights[self.step].to(device=hidden.device)
        bias = self.fused_biases[self.step].to(device=hidden.device)
        distilled = F.linear(hidden.float(), weight, bias)
        if self.strength == 1.0:
            return distilled
        original = F.linear(hidden.float(), self.base.weight.float(), self.base.bias.float())
        return torch.lerp(original, distilled, self.strength)


class H3PDDController:
    def __init__(self, transformer, banks, strength):
        self.transformer = transformer
        self.layer = transformer.final_layer
        self.video_original = self.layer.video_out
        self.audio_original = self.layer.audio_out
        self.banks = banks
        self.strength = strength
        self.video = PDDParallelHead(self.video_original, banks["proj_out.weight"],
                                     banks["proj_out.bias"],
                                     pdd_sampling_plans_for_sigmas(pdd_sigmas(12), 12), strength)
        self.audio = PDDParallelHead(self.audio_original, banks["audio_proj_out.weight"],
                                     banks["audio_proj_out.bias"],
                                     pdd_sampling_plans_for_sigmas(pdd_sigmas(3), 3), strength)

    def configure_sigmas(self, video_sigmas, audio_sigmas):
        # The overlap helper supports nonuniform grids for qualification, but
        # production execution must use exactly the published paired recipe.
        for actual, shift in ((video_sigmas, 12), (audio_sigmas, 3)):
            actual = torch.as_tensor(actual, dtype=torch.float64, device="cpu").flatten()
            if actual.shape != (9,) or not torch.allclose(actual, pdd_sigmas(shift), atol=1e-7, rtol=1e-7):
                raise ValueError("PDD execution schedule differs from the published eight-evaluation recipe.")
        self.video.configure(self.banks["proj_out.weight"], self.banks["proj_out.bias"],
                             pdd_sampling_plans_for_sigmas(video_sigmas, 12))
        self.audio.configure(self.banks["audio_proj_out.weight"], self.banks["audio_proj_out.bias"],
                             pdd_sampling_plans_for_sigmas(audio_sigmas, 3))

    def set_step(self, index):
        if type(index) is not int or not 0 <= index < 8:
            raise ValueError("PDD master-clock step is outside its eight evaluations.")
        self.video.step = self.audio.step = index

    def restore(self):
        self.layer.video_out = self.video_original
        self.layer.audio_out = self.audio_original


def install_pdd_heads(transformer, state, *, strength):
    if isinstance(strength, bool) or not math.isfinite(strength) or not 0 <= strength <= 2:
        raise ValueError("PDD strength must be constant and bounded.")
    if getattr(transformer, "_h3_pdd_controller", None) is not None:
        raise ValueError("PDD heads are already installed.")
    banks = validate_head_banks(state, transformer.final_layer)
    controller = H3PDDController(transformer, banks, float(strength))
    try:
        transformer.final_layer.video_out = controller.video
        transformer.final_layer.audio_out = controller.audio
        object.__setattr__(transformer, "_h3_pdd_controller", controller)
    except BaseException:
        controller.restore()
        raise
    return controller
