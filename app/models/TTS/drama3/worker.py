"""Codec bridge using the pinned Fish DAC and its official checkpoint layout.

Based on Fish Speech's load_codec_model/encode_audio/decode_to_audio at
541566932abbaebcb0a40e562a1f846738383f38. Built with Fish Audio.
The codec defaults to CPU; optional guarded GPU decoding restores it after each chunk.
"""
from pathlib import Path

import torch

from . import activate_fish_source


@torch.inference_mode()
def load_codec(checkpoint):
    activate_fish_source()
    from hydra.utils import instantiate
    from omegaconf import OmegaConf
    from torch.nn.utils.parametrize import is_parametrized, remove_parametrizations
    config = Path(__file__).parent / "vendor/fish_speech/configs/modded_dac_vq.yaml"
    # Constructors follow Maestro's default device unless explicitly bounded.
    with torch.device("cpu"):
        codec = instantiate(OmegaConf.load(str(config)))
        state = torch.load(checkpoint, map_location="cpu", mmap=True, weights_only=True)
        state = state.get("state_dict", state)
        if any("generator" in k for k in state):
            state = {k.replace("generator.", ""): v for k, v in state.items() if "generator." in k}
        rebuilt = {f"{name}.{b}" if name else b for name, module in codec.named_modules()
                   for b in module._non_persistent_buffers_set}
        codec.load_state_dict({k: v for k, v in state.items() if k not in rebuilt}, strict=True)
        codec.eval()
        for module in list(codec.modules()):
            if is_parametrized(module, "weight"):
                remove_parametrizations(module, "weight", leave_parametrized=True)
            elif hasattr(module, "weight_g"):
                torch.nn.utils.remove_weight_norm(module)
        return codec.to(device="cpu", dtype=torch.float32)


@torch.inference_mode()
def encode_reference(path, codec):
    import soundfile as sf
    import torchaudio.functional as functional
    waveform, rate = sf.read(path, dtype="float32", always_2d=True)
    if len(waveform) < 1 or len(waveform) / rate > 60:
        raise ValueError("Drama 3 reference audio must contain 0–60 seconds of audio.")
    with torch.device("cpu"):
        wav = torch.from_numpy(waveform.mean(axis=1)).unsqueeze(0)
        wav = functional.resample(wav, rate, codec.sample_rate)
        codes, lengths = codec.encode(wav.unsqueeze(0), torch.tensor([wav.shape[-1]]))
        return codes[0, :, :int(lengths[0])].cpu()


@torch.inference_mode()
def decode_audio(codes, codec, *, use_gpu=False, fallback_codec=None, stats=None):
    record = stats if stats is not None else {}
    record.update(frames=int(codes.shape[-1]), device="cpu", precision="float32",
                  fallback_reason=None)
    if use_gpu:
        tensors = {id(t): t for t in [*codec.parameters(), *codec.buffers()]}
        if any(t.device.type != "cpu" for t in tensors.values()):
            raise RuntimeError("Drama codec must start decoding on CPU")
        payload = sum(t.numel() * t.element_size() for t in tensors.values())
        torch.cuda.synchronize()
        torch.cuda.empty_cache()  # Release unused blocks, never live model tensors.
        free, _ = torch.cuda.mem_get_info()
        record.update(codec_tensor_bytes=payload, cuda_free_before_bytes=free,
                      required_free_bytes=3 * 1024**3 + payload)
        if codes.shape[-1] > 256 or free < record["required_free_bytes"]:
            record["fallback_reason"] = "long_chunk" if codes.shape[-1] > 256 else "insufficient_vram"
            print("Drama 3 GPU codec: using original CPU decoding for this chunk ("
                  + record["fallback_reason"] + ").", flush=True)
            codec = fallback_codec if fallback_codec is not None else codec
        else:
            record.update(device="cuda", precision=str(next(codec.parameters()).dtype).removeprefix("torch."))
            previous_tf32 = torch.backends.cuda.matmul.allow_tf32
            try:
                torch.backends.cuda.matmul.allow_tf32 = False
                codec.to(device="cuda")
                with torch.device("cuda"), torch.backends.cudnn.flags(
                        enabled=None, benchmark=None, benchmark_limit=None,
                        deterministic=None, allow_tf32=False, fp32_precision=None):
                    return codec.from_indices(codes.detach().to(device="cuda").unsqueeze(0).clone())[0, 0].float().cpu()
            finally:
                try:
                    codec.to(device="cpu")
                    torch.cuda.synchronize()
                finally:
                    torch.backends.cuda.matmul.allow_tf32 = previous_tf32
    with torch.device("cpu"):
        # The native codec clamps indices in place. Keep the generated codes
        # unchanged for subsequent speech-history conditioning.
        return codec.from_indices(codes.cpu().unsqueeze(0).clone())[0, 0].float().cpu()
