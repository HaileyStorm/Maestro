"""Single-GPU Drama 3 inference with CPU-resident block-FP8 experts.

The budget covers resident model tensors and worst-case expert staging. KV caches,
activations, the waveform codec, and kernel workspaces need additional GPU memory.
The optional expert cache has a separate allowance within the 26 GiB total guard.
"""

import json
import math
import os
from collections import OrderedDict
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


class _ExpertCache:
    """Instance-private FP8 LRU; packing uses the existing staging allowance."""

    def __init__(self, capacity: int, staging_bytes: int, bulk_pack: bool = False):
        self.capacity = capacity
        self.staging_bytes = staging_bytes
        self.entries = OrderedDict()
        self.host = None
        self.host_ready = None
        self.stream = None
        self.bulk_pack = bulk_pack
        self.stats = dict(capacity_bytes=capacity, resident_bytes=0, hits=0,
                          misses=0, h2d_bytes=0, fallback_calls=0,
                          pinned_host_bytes=0)

    def pack(self, layer, selected: Tensor, device):
        sources = (layer.w13_weight, layer.w13_scale_inv,
                   layer.w2_weight, layer.w2_scale_inv)
        expert_bytes = sum(t[0].numel() * t.element_size() for t in sources)
        ids = selected.tolist()
        if len(ids) * expert_bytes > self.capacity:
            self.stats["fallback_calls"] += 1
            self.stats["misses"] += len(ids)
            self.stats["h2d_bytes"] += len(ids) * expert_bytes
            return None
        stream = torch.cuda.current_stream(device)
        if self.stream is not None and self.stream != stream:
            self.stream.synchronize()
        self.stream = stream
        keys = [(layer._cache_index, expert) for expert in ids]
        missing = [(row, key) for row, key in enumerate(keys) if key not in self.entries]
        self.stats["hits"] += len(keys) - len(missing)
        self.stats["misses"] += len(missing)
        needed = len(missing) * expert_bytes
        protected = set(keys)
        for key in list(self.entries):
            if self.stats["resident_bytes"] + needed <= self.capacity:
                break
            if key not in protected:
                tensors = self.entries.pop(key)
                self.stats["resident_bytes"] -= sum(t.numel() * t.element_size() for t in tensors)
                del tensors
        if missing:
            if self.host_ready is not None:
                self.host_ready.synchronize()
            if self.host is None:
                host_bytes = sum(t.numel() * t.element_size() for t in sources)
                if host_bytes > self.staging_bytes:
                    raise RuntimeError("Drama 3 pinned expert staging exceeded its bound")
                self.host = tuple(torch.empty_like(t, device="cpu", pin_memory=True) for t in sources)
                self.stats["pinned_host_bytes"] = host_bytes
            if any(h.shape != s.shape or h.dtype != s.dtype for h, s in zip(self.host, sources)):
                raise RuntimeError("Drama 3 cached expert layers must share the staging layout")
            # A single reusable pinned layer avoids pinning the full CPU model
            # or allocating pinned memory on every decoded frame.
            try:
                for row, key in missing:
                    _check_cancel(layer._cancel_check)
                    expert = key[1]
                    for host, source in zip(self.host, sources):
                        host[row].view(torch.uint8).copy_(source[expert].view(torch.uint8))
                    tensors = tuple(torch.empty_like(t[0], device=device) for t in sources)
                    for tensor, host in zip(tensors, self.host):
                        tensor.view(torch.uint8).copy_(host[row].view(torch.uint8), non_blocking=True)
                    self.entries[key] = tensors
                    self.stats["resident_bytes"] += expert_bytes
                    self.stats["h2d_bytes"] += expert_bytes
            finally:
                # Before CPU reuses these rows, all queued H2D reads must finish,
                # including when cancellation interrupts a partially filled set.
                self.host_ready = torch.cuda.Event()
                self.host_ready.record(stream)
        packed = tuple(torch.empty((len(ids), *t.shape[1:]), dtype=t.dtype, device=device)
                       for t in sources)
        if self.bulk_pack:
            _check_cancel(layer._cancel_check)
            for field, destination in enumerate(packed):
                tensors = [self.entries[key][field] for key in keys]
                # Stack byte views to preserve FP8 weights and FP32 scales
                # exactly while batching the per-expert device copies.
                torch.stack([tensor.view(torch.uint8) for tensor in tensors],
                            out=destination.view(torch.uint8))
                for tensor in tensors:
                    tensor.record_stream(stream)
            for key in keys:
                self.entries.move_to_end(key)
            return packed
        for row, key in enumerate(keys):
            _check_cancel(layer._cancel_check)
            for destination, tensor in zip(packed, self.entries[key]):
                destination[row].view(torch.uint8).copy_(tensor.view(torch.uint8))
                tensor.record_stream(stream)
            self.entries.move_to_end(key)
        return packed


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
        self._expert_cache = None
        self._cache_index = None

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
        packed = (self._expert_cache.pack(self, selected, x.device)
                  if self._expert_cache is not None else None)
        if packed is None:
            w13 = select_fp8(self.w13_weight, selected).to(device=x.device)
            s13 = self.w13_scale_inv[selected].to(device=x.device)
            w2 = select_fp8(self.w2_weight, selected).to(device=x.device)
            s2 = self.w2_scale_inv[selected].to(device=x.device)
        else:
            w13, s13, w2, s2 = packed
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
    speech_head_rows: tuple[int, ...] | None = None,
) -> Iterator[tuple[str, Tensor]]:
    for name, tensor in iter_checkpoint_tensors(checkpoint_dir, skip_prefixes=skip_prefixes):
        _check_cancel(cancel_check)
        if speech_head_rows is not None and name == CHECKPOINT_LM_HEAD_KEY:
            # Validate the released full head before selecting original row bytes.
            if tuple(tensor.shape) != (252288, 2048) or tensor.dtype != torch.bfloat16:
                raise ValueError("Drama 3 restricted head requires the original 252288x2048 BF16 head")
            rows = torch.tensor(speech_head_rows, dtype=torch.long, device="cpu")
            tensor = tensor.index_select(0, rows)
        yield name, tensor


@torch.no_grad()
def load_offloaded_model(
    checkpoint_dir: str | Path,
    device: str = "cuda:0",
    gpu_budget_gib: float = 22.0,
    cancel_check: CancelCheck | None = None,
    expert_cache_gib: float = 0.0,
    restricted_speech_head: bool = False,
    bulk_expert_pack: bool = False,
    compile_recurrence: bool = False,
) -> FishOmniModel:
    """Load official Fish inference math with eager, partial FP8 expert offload.

    Caller owns serialization, GPU lease, codec loading, and runtime headroom.
    Do not subsequently call model.to()/to_empty(): placement is already final.
    """
    if type(restricted_speech_head) is not bool:
        raise ValueError("Drama 3 restricted speech head must be a boolean")
    if type(bulk_expert_pack) is not bool:
        raise ValueError("Drama 3 bulk expert packing must be a boolean")
    if type(compile_recurrence) is not bool:
        raise ValueError("Drama 3 recurrent compilation must be a boolean")
    if isinstance(gpu_budget_gib, bool) or not isinstance(gpu_budget_gib, (int, float)):
        raise ValueError("Drama 3 GPU budget must be a positive number of GiB")
    if not math.isfinite(gpu_budget_gib) or gpu_budget_gib <= 0:
        raise ValueError("Drama 3 GPU budget must be a positive finite number of GiB")
    if (isinstance(expert_cache_gib, bool) or not isinstance(expert_cache_gib, (int, float))
            or not math.isfinite(expert_cache_gib) or not 0 <= expert_cache_gib <= 4):
        raise ValueError("Drama 3 expert cache must be between 0 and 4 GiB")
    if gpu_budget_gib + expert_cache_gib > 26:
        raise ValueError("Drama 3 weights, staging and expert cache must total at most 26 GiB")
    target = torch.device(device)
    if target.type != "cuda" or not fp8_gemm_supported([target]):
        raise ValueError("Drama 3 partial offload requires an SM89+ NVIDIA CUDA GPU and Triton")
    if target.index is None:
        target = torch.device("cuda", torch.cuda.current_device())
    budget = int(gpu_budget_gib * GIB)
    cache_bytes = int(expert_cache_gib * GIB)
    if budget + cache_bytes > torch.cuda.get_device_properties(target).total_memory:
        raise ValueError("Drama 3 GPU weight and expert cache budgets exceed device memory")
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

    speech_head_rows = None
    tokenizer = None
    if restricted_speech_head:
        actual = (config.text.vocab_size, config.text.hidden_size,
                  config.eos_token_id, config.semantic_start_token_id,
                  config.semantic_end_token_id, config.audio_decoder.vocab_size,
                  config.audio_decoder.num_codebooks)
        if actual != (252288, 2048, 248046, 248099, 252194, 4096, 10):
            raise ValueError("Drama 3 restricted speech head requires the released vocabulary and ten codebooks")
        tokenizer = Tokenizer.from_file(str(checkpoint_dir / "tokenizer.json"))
        for token, expected in (("<|im_end|>", config.eos_token_id),
                                ("<|semantic:0|>", config.semantic_start_token_id),
                                ("<|semantic:4095|>", config.semantic_end_token_id)):
            if tokenizer.token_to_id(token) != expected:
                raise ValueError(f"tokenizer/config mismatch for {token}")
        speech_head_rows = tuple(sorted((config.eos_token_id, *range(
            config.semantic_start_token_id, config.semantic_end_token_id + 1))))
    with torch.device("meta"):
        backbone = FishOmniBackbone(config.text, fp8_experts=True, dtype=torch.bfloat16)
        tower = FishOmniAudioDecoder(config, dtype=torch.bfloat16)
        if speech_head_rows is not None:
            backbone.lm_head = torch.nn.Linear(
                config.text.hidden_size, len(speech_head_rows),
                bias=False, dtype=torch.bfloat16,
            )
            tower.main_logits_are_candidates = True
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
        backbone, _checked_tensors(
            checkpoint_dir, AUDIO_DECODER_PREFIXES, cancel_check, speech_head_rows
        )
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
        config, backbone, tower,
        tokenizer if tokenizer is not None else Tokenizer.from_file(str(checkpoint_dir / "tokenizer.json"))
    ).eval()
    if compile_recurrence:
        from fish_speech.models.text2semantic.fish_omni.layers import (
            GatedDeltaNet, recurrent_gated_delta_rule,
        )
        # Bind to this model only; preserve other instances and prefill math.
        # No CUDA graphs: recurrent-state mutation retains its ordinary lifetime.
        compiled_rule = torch.compile(
            recurrent_gated_delta_rule, fullgraph=True, dynamic=False,
            options={"triton.cudagraphs": False},
        )
        for module in backbone.modules():
            _check_cancel(cancel_check)
            if isinstance(module, GatedDeltaNet):
                module._recurrent_rule = compiled_rule
    tensors = [*model.parameters(), *model.buffers()]
    if any(tensor.is_meta for tensor in tensors):
        raise RuntimeError("Drama 3 loader left uninitialized meta tensors")
    gpu_bytes = sum(t.numel() * t.element_size() for t in tensors if t.device == target)
    cpu_bytes = sum(t.numel() * t.element_size() for t in tensors if t.device.type == "cpu")
    if gpu_bytes + staging_bytes > budget:
        raise RuntimeError("Drama 3 placement exceeded the requested weight budget")
    cache = _ExpertCache(cache_bytes, staging_bytes, bulk_expert_pack) if cache_bytes and offloaded_modules else None
    for index, layer in enumerate(layers):
        if layer.mlp.experts in offloaded_modules:
            layer.mlp.experts._expert_cache = cache
            layer.mlp.experts._cache_index = index
    model.offload_placement = {
        "device": str(target),
        "gpu_budget_bytes": budget,
        "gpu_resident_bytes": gpu_bytes,
        "cpu_resident_bytes": cpu_bytes,
        "expert_staging_reserve_bytes": staging_bytes,
        "gpu_expert_layers": resident_layers,
        "cpu_expert_layers": [i for i in range(len(layers)) if i not in resident_layers],
        "cuda_graph": False,
        "restricted_speech_head": restricted_speech_head,
        "bulk_expert_pack": bulk_expert_pack,
        "compile_recurrence": compile_recurrence,
        "speech_head_rows": len(speech_head_rows) if speech_head_rows is not None else config.text.vocab_size,
        "expert_cache": cache.stats if cache is not None else {"capacity_bytes": 0},
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
        f"{cache_bytes / GIB if cache is not None else 0:.2f} GiB expert cache allowance; "
        "KV caches, activations, codec and kernel workspace require extra headroom"
    )
    return model
