"""Single-GPU Drama 3 inference with CPU-resident block-FP8 experts.

The budget covers resident model tensors and worst-case expert staging. KV caches,
activations, the waveform codec, and kernel workspaces need additional GPU memory.
"""

import json
import math
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import torch
from loguru import logger
from tokenizers import Tokenizer
from torch import Tensor

from fish_speech.models.text2semantic.fish_omni.backbone import (
    CHECKPOINT_BACKBONE_PREFIX,
    CHECKPOINT_LM_HEAD_KEY,
    FishOmniBackbone,
    load_backbone_weights,
    module_bytes,
)
from fish_speech.models.text2semantic.fish_omni.config import FishOmniConfig
from fish_speech.models.text2semantic.fish_omni.fp8_moe import (
    fp8_gemm_supported,
    fp8_moe_forward,
)
from fish_speech.models.text2semantic.fish_omni.model import (
    AUDIO_DECODER_PREFIXES,
    FishOmniModel,
    iter_checkpoint_tensors,
)
from fish_speech.models.text2semantic.fish_omni.moe import MoEExperts, select_fp8
from fish_speech.models.text2semantic.fish_omni.tower import (
    FishOmniAudioDecoder,
    load_audio_decoder_weights,
)

CancelCheck = Callable[[], object]
GIB = 1024**3


def _check_cancel(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None and cancel_check() is True:
        raise InterruptedError("Drama 3 generation cancelled")


class _CPUExperts(MoEExperts):
    """Keep the official loader's buffers, staging only the routed expert subset."""

    def __init__(self, original: MoEExperts, cancel_check: CancelCheck | None):
        super().__init__(
            original.num_experts,
            original.hidden,
            original.intermediate,
            fp8=True,
            device="meta",
        )
        self._cancel_check = cancel_check

    def forward(self, x: Tensor, topk_ids: Tensor, topk_weights: Tensor) -> Tensor:
        _check_cancel(self._cancel_check)
        if not x.is_cuda or self.w13_weight.device.type != "cpu":
            raise RuntimeError(
                "Drama 3 offloaded experts require CPU weights and CUDA activations"
            )
        if x.shape[0] == 0:
            return torch.zeros_like(x)
        # Host routing introduces synchronization, so this path cannot be graphed.
        ids = topk_ids.to(device="cpu")
        selected, remapped = torch.unique(ids, sorted=True, return_inverse=True)
        _check_cancel(self._cancel_check)
        w13 = select_fp8(self.w13_weight, selected).to(device=x.device)
        s13 = self.w13_scale_inv[selected].to(device=x.device)
        w2 = select_fp8(self.w2_weight, selected).to(device=x.device)
        s2 = self.w2_scale_inv[selected].to(device=x.device)
        _check_cancel(self._cancel_check)
        return fp8_moe_forward(
            x,
            remapped.reshape_as(topk_ids).to(device=x.device),
            topk_weights,
            w13,
            s13,
            w2,
            s2,
        )


def _checked_tensors(
    checkpoint_dir: Path,
    skip_prefixes: tuple[str, ...],
    cancel_check: CancelCheck | None,
) -> Iterator[tuple[str, Tensor]]:
    for name, tensor in iter_checkpoint_tensors(checkpoint_dir, skip_prefixes=skip_prefixes):
        _check_cancel(cancel_check)
        yield name, tensor


@torch.no_grad()
def load_offloaded_model(
    checkpoint_dir: str | Path,
    device: str = "cuda:0",
    gpu_budget_gib: float = 22.0,
    cancel_check: CancelCheck | None = None,
) -> FishOmniModel:
    """Load official Fish inference math with eager, partial FP8 expert offload.

    Caller owns serialization, GPU lease, codec loading, and runtime headroom.
    Do not subsequently call model.to()/to_empty(): placement is already final.
    """
    if isinstance(gpu_budget_gib, bool) or not isinstance(gpu_budget_gib, (int, float)):
        raise ValueError("Drama 3 GPU budget must be a positive number of GiB")
    if not math.isfinite(gpu_budget_gib) or gpu_budget_gib <= 0:
        raise ValueError("Drama 3 GPU budget must be a positive finite number of GiB")
    target = torch.device(device)
    if target.type != "cuda" or not fp8_gemm_supported([target]):
        raise ValueError("Drama 3 partial offload requires an SM89+ NVIDIA CUDA GPU and Triton")
    if target.index is None:
        target = torch.device("cuda", torch.cuda.current_device())
    budget = int(gpu_budget_gib * GIB)
    if budget > torch.cuda.get_device_properties(target).total_memory:
        raise ValueError("Drama 3 GPU weight budget exceeds device memory")
    _check_cancel(cancel_check)
    checkpoint_dir = Path(checkpoint_dir)
    with (checkpoint_dir / "config.json").open(encoding="utf-8") as source:
        config = FishOmniConfig.from_dict(json.load(source))
    if not config.fp8_experts:
        raise ValueError("Drama 3 partial offload requires the official block-FP8 checkpoint")
    if os.environ.get("FISH_DRAMA3_FP8_EXPERTS", "").strip() == "0":
        raise ValueError(
            "Drama 3 partial offload requires FP8 experts; FISH_DRAMA3_FP8_EXPERTS=0 conflicts"
        )

    with torch.device("meta"):
        backbone = FishOmniBackbone(config.text, fp8_experts=True, dtype=torch.bfloat16)
        tower = FishOmniAudioDecoder(config, dtype=torch.bfloat16)
    layers = backbone.model.layers
    expert_sizes = [module_bytes(layer.mlp.experts) for layer in layers]
    if not expert_sizes:
        raise ValueError("Drama 3 backbone has no expert layers")
    fixed_bytes = module_bytes(backbone) + module_bytes(tower) - sum(expert_sizes)
    # A prefill chunk can route to every expert; reserve that full worst case.
    staging_bytes = (
        max(expert_sizes) if fixed_bytes + sum(expert_sizes) > budget else 0
    )
    if fixed_bytes + staging_bytes > budget:
        raise ValueError(
            f"Drama 3 needs at least {(fixed_bytes + staging_bytes) / GIB:.2f} GiB "
            "for non-expert tensors and worst-case FP8 staging"
        )
    remaining = budget - fixed_bytes - staging_bytes
    resident_layers = []
    offloaded_modules = set()
    for index, (layer, size) in enumerate(zip(layers, expert_sizes)):
        if size <= remaining:
            resident_layers.append(index)
            remaining -= size
        else:
            layer.mlp.experts = _CPUExperts(layer.mlp.experts, cancel_check)
            offloaded_modules.add(layer.mlp.experts)
    if not offloaded_modules:
        staging_bytes = 0

    def allocate(module: torch.nn.Module) -> None:
        _check_cancel(cancel_check)
        destination = "cpu" if module in offloaded_modules else target
        module.to_empty(device=destination, recurse=False)
        for child in module.children():
            allocate(child)

    allocate(backbone)
    allocate(tower)
    load_backbone_weights(
        backbone, _checked_tensors(checkpoint_dir, AUDIO_DECODER_PREFIXES, cancel_check)
    )
    load_audio_decoder_weights(
        tower,
        _checked_tensors(
            checkpoint_dir,
            (CHECKPOINT_BACKBONE_PREFIX, CHECKPOINT_LM_HEAD_KEY),
            cancel_check,
        ),
    )
    _check_cancel(cancel_check)
    model = FishOmniModel(
        config, backbone, tower, Tokenizer.from_file(str(checkpoint_dir / "tokenizer.json"))
    ).eval()
    tensors = [*model.parameters(), *model.buffers()]
    if any(tensor.is_meta for tensor in tensors):
        raise RuntimeError("Drama 3 loader left uninitialized meta tensors")
    gpu_bytes = sum(t.numel() * t.element_size() for t in tensors if t.device == target)
    cpu_bytes = sum(t.numel() * t.element_size() for t in tensors if t.device.type == "cpu")
    if gpu_bytes + staging_bytes > budget:
        raise RuntimeError("Drama 3 placement exceeded the requested weight budget")
    model.offload_placement = {
        "device": str(target),
        "gpu_budget_bytes": budget,
        "gpu_resident_bytes": gpu_bytes,
        "cpu_resident_bytes": cpu_bytes,
        "expert_staging_reserve_bytes": staging_bytes,
        "gpu_expert_layers": resident_layers,
        "cpu_expert_layers": [i for i in range(len(layers)) if i not in resident_layers],
        "cuda_graph": False,
    }
    if cancel_check is not None:
        def check_before_layer(_module, _args):
            _check_cancel(cancel_check)

        for layer in layers:
            layer.register_forward_pre_hook(check_before_layer)
    os.environ["FISH_DRAMA3_CUDA_GRAPH"] = "0"
    logger.info(
        f"Drama 3 FP8 placement: {gpu_bytes / GIB:.2f} GiB on {target}, "
        f"{cpu_bytes / GIB:.2f} GiB on CPU, {staging_bytes / GIB:.2f} GiB staging reserve; "
        f"{len(resident_layers)} GPU / {len(offloaded_modules)} CPU expert layers; "
        "KV caches, activations, codec and kernel workspace require extra headroom"
    )
    return model
