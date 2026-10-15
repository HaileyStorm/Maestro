"""Bounded, model-owned CPU reuse of an exact native Drama first prefill chunk."""
from __future__ import annotations

from contextlib import contextmanager
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import torch

CHUNK = 128
MIN_CAPACITY = 1024
CPU_LIMIT = 96 * 1024**2


class _UnsupportedLayout(ValueError):
    pass


def _model_binding(model, checkpoint_root):
    source_root = Path(__file__).parent
    native = source_root / "vendor/fish_speech/models/text2semantic"
    source_files = [source_root / "pipeline.py", source_root / "offload.py", Path(__file__),
                    native / "sampling.py", *sorted((native / "fish_omni").glob("*.py"))]
    sources = {str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in source_files}
    checkpoint = Path(checkpoint_root)
    manifest = checkpoint / "model.safetensors.index.json"
    assets = [checkpoint / "config.json", manifest,
              *[checkpoint / name for name in sorted(set(json.loads(manifest.read_text())["weight_map"].values()))]]
    identities = []
    for path in assets:
        stat = path.stat()
        identities.append((path.name, str(path.resolve()), stat.st_dev, stat.st_ino,
                           stat.st_size, stat.st_mtime_ns))
    # Entries never leave this loaded model. Manifest/source hashes and asset
    # identities identify its construction; no per-generation shard re-reading.
    return dict(version=1, loaded_model=id(model), sources=sources, assets=identities,
                manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                config_sha256=hashlib.sha256((checkpoint / "config.json").read_bytes()).hexdigest())


class PrefixCache:
    def __init__(self, model, *, checkpoint_root, cancel_check=None):
        self.model = model
        self.cancel_check = cancel_check
        self.binding = _model_binding(model, checkpoint_root)
        self.entry = None
        self.active = None
        self.owner_thread = None
        self.closed = False
        self.lock = threading.Lock()
        self.original_forward = model.backbone.forward
        self.original_prefill = model.audio_decoder.prefill
        self.stats = dict(eligible_generations=0, hits=0, misses=0, ineligible=0,
                          snapshots=0, restored_bytes=0, restored_state_bytes=0, restored_hidden_bytes=0, restored_tensors=0,
                          resident_cpu_bytes=0, snapshot_seconds=0.0, restore_seconds=0.0,
                          saved_hidden_bytes=0, capacity_misses=0, skipped_backbone_chunks=0,
                          skipped_tower_chunks=0)

        def forward(*args, **kwargs):
            return self._forward(*args, **kwargs)

        def prefill(*args, **kwargs):
            return self._prefill(*args, **kwargs)

        self.forward_hook, self.prefill_hook = forward, prefill
        model.backbone.forward = forward
        model.audio_decoder.prefill = prefill

    def _check_cancel(self):
        if self.cancel_check is not None and self.cancel_check() is True:
            raise InterruptedError("Drama 3 generation cancelled")

    def _buffers(self):
        values = []
        for index, layer in enumerate(self.model.backbone.model.layers):
            if layer.layer_type == "full_attention":
                for name in ("k_cache", "v_cache"):
                    value = getattr(layer.self_attn, name)
                    if not isinstance(value, torch.Tensor):
                        raise _UnsupportedLayout("Prefix attention state is unallocated")
                    values.append((f"backbone.{index}.{name}", value[..., :CHUNK, :]))
            else:
                for name in ("conv_state", "recurrent_state"):
                    values.append((f"backbone.{index}.{name}", getattr(layer.linear_attn, name)))
        tower = self.model.audio_decoder
        for index, layer in enumerate((*tower.shared_layers, *tower.residual_layers)):
            attention = layer.transformer_layer.attention
            if attention.static_kv_len is not None:
                raise _UnsupportedLayout("Prefix reuse excludes static whole-decode graphs")
            for name in ("k_cache", "v_cache"):
                value = getattr(attention, name)
                if not isinstance(value, torch.Tensor):
                    raise _UnsupportedLayout("Prefix tower state is unallocated")
                values.append((f"tower.{index}.{name}", value[..., :CHUNK, :]))
        if len(values) != 106:
            raise _UnsupportedLayout("Prefix reuse requires the released 40-layer/13-tower cache layout")
        return values

    def _signature(self, prompt):
        model = self.model
        if model.decode_graphs is not None or os.environ.get("FISH_DRAMA3_CUDA_GRAPH") != "0":
            raise _UnsupportedLayout("Prefix reuse requires the native eager generation path")
        buffers = self._buffers()
        layouts = []
        for name, value in buffers:
            if not isinstance(value, torch.Tensor) or value.is_meta:
                raise _UnsupportedLayout("Prefix cache state is unallocated")
            layouts.append((name, list(value.shape), list(value.stride()), str(value.dtype), str(value.device)))
        placement = model.offload_placement
        static_placement = {key: placement.get(key) for key in (
            "device", "gpu_budget_bytes", "gpu_resident_bytes", "cpu_resident_bytes",
            "expert_staging_reserve_bytes", "gpu_expert_layers", "cpu_expert_layers",
            "restricted_speech_head", "bulk_expert_pack", "compile_recurrence", "speech_head_rows")}
        static_placement["expert_cache_capacity"] = placement.get("expert_cache", {}).get("capacity_bytes")
        signature = dict(binding=self.binding, config=dataclasses.asdict(model.config),
            placement=static_placement, capacity=model.cache_capacity,
            backbone_capacity=model.backbone.max_seq_len, batch=model.backbone.max_batch_size,
            layouts=layouts, chunk_size=CHUNK, model_dtype=str(model.dtype),
            training=[model.training, model.backbone.training, model.audio_decoder.training],
            inference_mode=torch.is_inference_mode_enabled(),
            torch=str(torch.__version__), cuda=torch.version.cuda,
            capability=list(torch.cuda.get_device_capability(model.device)),
            gpu_name=torch.cuda.get_device_name(model.device),
            float32_matmul_precision=torch.get_float32_matmul_precision(),
            matmul_tf32=torch.backends.cuda.matmul.allow_tf32,
            cudnn_tf32=torch.backends.cudnn.allow_tf32,
            bf16_reduced_precision=torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction,
            fp16_reduced_precision=torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction,
            sdp=[torch.backends.cuda.flash_sdp_enabled(), torch.backends.cuda.mem_efficient_sdp_enabled(),
                 torch.backends.cuda.math_sdp_enabled(), torch.backends.cuda.cudnn_sdp_enabled()],
            cudnn_deterministic=torch.backends.cudnn.deterministic,
            cudnn_benchmark=torch.backends.cudnn.benchmark,
            deterministic=torch.are_deterministic_algorithms_enabled())
        digest = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(",", ":")).encode())
        prefix = prompt[:CHUNK + 1].detach().contiguous()
        digest.update(str((str(prefix.dtype), tuple(prefix.shape))).encode())
        digest.update(prefix.numpy().tobytes())
        return digest.hexdigest(), buffers

    @contextmanager
    def generation(self, prompt, *, max_new_tokens, prefill_chunk_size=CHUNK):
        if self.closed or not self.lock.acquire(blocking=False):
            raise RuntimeError("Prefix cache generation is closed or concurrent")
        try:
            self._check_cancel()
            from fish_speech.models.text2semantic.fish_omni.generate import ensure_drama3_caches
            ensure_drama3_caches(self.model, max(MIN_CAPACITY, len(prompt) + max_new_tokens))
            eligible = (prefill_chunk_size == CHUNK and isinstance(prompt, torch.Tensor)
                        and prompt.device.type == "cpu" and prompt.dtype == torch.long
                        and prompt.ndim == 2 and prompt.shape[1] == 11 and len(prompt) >= CHUNK + 1
                        and self.model.config.text.hidden_size == 2048
                        and self.model.config.audio_decoder.num_codebooks == 10
                        and self.model.cache_capacity >= len(prompt) + max_new_tokens)
            if not eligible:
                self.stats["ineligible"] += 1
                self.active = None
                yield
                return
            try:
                key, buffers = self._signature(prompt)
            except _UnsupportedLayout:
                self.entry = None
                self.stats["resident_cpu_bytes"] = 0
                self.stats["ineligible"] += 1
                yield
                return
            hit = self.entry is not None and self.entry["key"] == key
            self.stats["eligible_generations"] += 1
            self.stats["hits" if hit else "misses"] += 1
            if not hit:
                if self.entry is not None and self.entry["capacity"] != self.model.cache_capacity:
                    self.stats["capacity_misses"] += 1
                self.entry = None
                self.stats["resident_cpu_bytes"] = 0
            self.owner_thread = threading.get_ident()
            self.active = dict(key=key, hit=hit, buffers=buffers, prompt=prompt,
                               forward_seen=False, prefill_seen=False, hidden=None)
            yield
            if not self.active["forward_seen"] or not self.active["prefill_seen"]:
                raise RuntimeError("Original first-chunk hook sequence did not execute")
        except BaseException:
            self.entry = None
            self.stats["resident_cpu_bytes"] = 0
            raise
        finally:
            self.active = None
            self.owner_thread = None
            self.lock.release()

    def _forward(self, *args, **kwargs):
        active = self.active
        if active is not None and self.owner_thread != threading.get_ident():
            raise RuntimeError("Concurrent use of an active Drama prefix cache")
        if active is None or active["forward_seen"]:
            return self.original_forward(*args, **kwargs)
        self._check_cancel()
        embeddings, positions = kwargs.get("inputs_embeds"), kwargs.get("input_pos")
        if (args or embeddings is None or tuple(embeddings.shape) != (1, CHUNK, 2048)
                or positions is None or positions.detach().cpu().tolist() != list(range(CHUNK))
                or kwargs.get("static_kv_len") is not None):
            raise RuntimeError("Original first backbone chunk contract changed")
        active["forward_seen"] = True
        if active["hit"]:
            entry = self.entry
            started = time.monotonic()
            restored = 0
            with torch.no_grad():
                for (name, destination), (saved_name, source) in zip(active["buffers"], entry["buffers"], strict=True):
                    if name != saved_name or destination.shape != source.shape or destination.dtype != source.dtype:
                        raise RuntimeError("Prefix state layout changed before restore")
                    destination.copy_(source)
                    restored += source.numel() * source.element_size()
                hidden = entry["hidden"].to(device=embeddings.device, copy=True)
            torch.cuda.synchronize(self.model.device)
            self._check_cancel()
            self.stats["restore_seconds"] += time.monotonic() - started
            hidden_bytes = entry["hidden"].numel() * entry["hidden"].element_size()
            self.stats["restored_bytes"] += restored + hidden_bytes
            self.stats["restored_state_bytes"] += restored
            self.stats["restored_hidden_bytes"] += hidden_bytes
            self.stats["restored_tensors"] += len(entry["buffers"])
            self.stats["skipped_backbone_chunks"] += 1
            return hidden
        hidden = self.original_forward(*args, **kwargs)
        if tuple(hidden.shape) != (1, CHUNK, 2048) or hidden.dtype != torch.bfloat16 or not hidden.is_contiguous():
            raise RuntimeError("Original full first-chunk hidden layout unsupported")
        active["hidden"] = hidden
        return hidden

    def _prefill(self, hidden, positions, frames, next_frames):
        active = self.active
        if active is not None and self.owner_thread != threading.get_ident():
            raise RuntimeError("Concurrent use of an active Drama prefix cache")
        if active is None or active["prefill_seen"]:
            return self.original_prefill(hidden, positions, frames, next_frames)
        if (not active["forward_seen"] or tuple(hidden.shape) != (CHUNK, 2048)
                or positions.detach().cpu().tolist() != list(range(CHUNK))
                or not torch.equal(frames.cpu(), active["prompt"][:CHUNK])
                or not torch.equal(next_frames.cpu(), active["prompt"][1:CHUNK + 1])):
            raise RuntimeError("Original next-row teacher-force label binding changed")
        active["prefill_seen"] = True
        if active["hit"]:
            self.stats["skipped_tower_chunks"] += 1
            return None
        result = self.original_prefill(hidden, positions, frames, next_frames)
        self._check_cancel()
        original_hidden = active["hidden"]
        size = sum(value.numel() * value.element_size() for _, value in active["buffers"])
        size += original_hidden.numel() * original_hidden.element_size()
        if size > CPU_LIMIT:
            self.stats["ineligible"] += 1
            return result
        torch.cuda.synchronize(self.model.device)
        started = time.monotonic()
        copies = [(name, value.detach().to(device="cpu", copy=True).contiguous()) for name, value in active["buffers"]]
        saved_hidden = original_hidden.detach().to(device="cpu", copy=True).contiguous()
        self._check_cancel()
        self.entry = dict(key=active["key"], capacity=self.model.cache_capacity,
                          buffers=copies, hidden=saved_hidden)
        self.stats["snapshots"] += 1
        self.stats["snapshot_seconds"] += time.monotonic() - started
        self.stats["resident_cpu_bytes"] = size
        self.stats["saved_hidden_bytes"] = saved_hidden.numel() * saved_hidden.element_size()
        return result

    def snapshot(self):
        return dict(enabled=not self.closed, maximum_cpu_bytes=CPU_LIMIT, **self.stats)

    def close(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Cannot close an active Drama prefix cache")
        try:
            if self.closed:
                return
            if (self.model.backbone.forward is not self.forward_hook
                    or self.model.audio_decoder.prefill is not self.prefill_hook):
                raise RuntimeError("Prefix hooks changed owner before close")
            self.model.backbone.forward = self.original_forward
            self.model.audio_decoder.prefill = self.original_prefill
            self.entry = None
            self.stats["resident_cpu_bytes"] = 0
            self.closed = True
            self.model = None
            self.original_forward = self.original_prefill = None
        finally:
            self.lock.release()
