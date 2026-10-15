"""Codec bridge using the pinned Fish DAC and its official checkpoint layout.

Based on Fish Speech's load_codec_model/encode_audio/decode_to_audio at
541566932abbaebcb0a40e562a1f846738383f38. Built with Fish Audio.
The codec stays on CPU to leave GPU headroom for expert staging and caches.
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
def decode_audio(codes, codec):
    with torch.device("cpu"):
        # The native codec clamps indices in place. Keep the generated codes
        # unchanged for subsequent speech-history conditioning.
        return codec.from_indices(codes.cpu().unsqueeze(0).clone())[0, 0].float().cpu()
