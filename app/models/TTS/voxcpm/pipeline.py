"""Runtime wrapper for OpenBMB VoxCPM 2."""

from __future__ import annotations

import gc
import inspect
import json
import math
import os
from functools import wraps
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Any, Optional

import numpy as np
import torch

from shared.utils import files_locator as fl
from services.prepared_voice_cache import PreparedVoiceCache
from ..voxcpm_handler import _get_voxcpm2_checkpoint_root, _validate_voxcpm_reference

from services.tts_script_breakdown import (
    format_voxcpm_turn_text,
    parse_tts_script_turns,
)


VOXCPM2_OUTPUT_SAMPLE_RATE = 48000


def _output_sample_rate(ckpt_root: str, model: Any = None) -> int:
    tts_model = getattr(model, "tts_model", None)
    rate = getattr(tts_model, "sample_rate", None)
    try:
        if rate:
            return int(rate)
    except (TypeError, ValueError):
        pass
    config_path = os.path.join(ckpt_root, "config.json")
    try:
        payload = json.loads(open(config_path, encoding="utf-8").read())
        vae = payload.get("audio_vae_config") or {}
        return int(vae.get("out_sample_rate") or VOXCPM2_OUTPUT_SAMPLE_RATE)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return VOXCPM2_OUTPUT_SAMPLE_RATE


class VoxCpmPipeline:
    def __init__(self, ckpt_root: Optional[str] = None, device: Optional[str] = None) -> None:
        self.ckpt_root = (
            ckpt_root or _get_voxcpm2_checkpoint_root()
            or fl.get_download_location("voxcpm2")
        )
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.prepared_voice_cache = None
        self._prompt_cache_builder = None

    def load(self) -> "VoxCpmPipeline":
        if self.model is not None:
            return self
        config_path = os.path.join(self.ckpt_root, "config.json")
        weights_path = os.path.join(self.ckpt_root, "model.safetensors")
        if not os.path.isfile(config_path) or not os.path.isfile(weights_path):
            raise RuntimeError(
                "VoxCPM 2 weights are not installed on this Maestro yet."
            )
        try:
            from voxcpm import VoxCPM
        except ImportError as error:
            raise RuntimeError(
                "The VoxCPM Python package is not installed in this Maestro environment."
            ) from error
        self.model = VoxCPM.from_pretrained(
            self.ckpt_root,
            load_denoiser=False,
            device=self.device,
        )
        self._install_prepared_voice_cache()
        return self

    def _install_prepared_voice_cache(self) -> None:
        native = self.model.tts_model
        original = native.build_prompt_cache
        signature = inspect.signature(original)
        package_root = Path(inspect.getfile(type(native))).resolve().parent.parent
        checkpoint = Path(self.ckpt_root)
        asset_names = {
            "config.json", "model.safetensors", "pytorch_model.bin",
            "audiovae.safetensors", "audiovae.pth", "tokenizer.json",
            "tokenizer_config.json", "special_tokens_map.json",
            "tokenization_voxcpm2.py",
        }
        self.prepared_voice_cache = PreparedVoiceCache(
            "voxcpm2",
            model_files=[checkpoint / name for name in sorted(asset_names)
                         if (checkpoint / name).is_file()],
            implementation_files=[
                Path(__file__), *sorted(package_root.rglob("*.py")),
                distribution("librosa").locate_file("librosa/core/audio.py"),
            ],
            options={
                "torch": str(torch.__version__), "librosa": version("librosa"),
                "soundfile": version("soundfile"), "soxr": version("soxr"),
                "numpy": np.__version__, "device": str(native.device),
                "sample_rate": native._encode_sample_rate,
                "patch_size": native.patch_size, "chunk_size": native.chunk_size,
                "vae_dtype": str(next(native.audio_vae.parameters()).dtype),
            },
        )
        cache = self.prepared_voice_cache

        @wraps(original)
        def build_prompt_cache(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            references = {
                role: values[role]
                for role in ("reference_wav_path", "prompt_wav_path")
                if values.get(role) is not None
            }
            if not references or any(not os.path.isfile(path) for path in references.values()):
                return original(*args, **kwargs)
            # Native reference and continuation roles have opposite padding.
            # Cache the original combined result, retaining both role bindings.
            return cache.get_or_prepare(
                references=references,
                settings={name: value for name, value in values.items()
                          if name not in ("reference_wav_path", "prompt_wav_path")},
                prepare=lambda: original(*args, **kwargs),
            )

        self._prompt_cache_builder = original
        native.build_prompt_cache = build_prompt_cache

    def release(self) -> None:
        if self.model is not None and self._prompt_cache_builder is not None:
            self.model.tts_model.build_prompt_cache = self._prompt_cache_builder
        self._prompt_cache_builder = None
        self.model = None
        if self.prepared_voice_cache is not None:
            self.prepared_voice_cache.close()
        gc.collect()

    def generate(
        self,
        input_prompt: str,
        model_mode: Optional[str] = None,
        audio_guide: Optional[str] = None,
        *,
        alt_prompt: str = "",
        temperature: float = 0.8,
        guidance_scale: float = 2.0,
        guide_scale: Optional[float] = None,
        sampling_steps: int = 30,
        custom_settings: Optional[dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        # WanGP's public guidance_scale/num_inference_steps arrive under these
        # production aliases. Keep guidance_scale for direct Python callers.
        cfg_value = float(guidance_scale if guide_scale is None else guide_scale)
        if not math.isfinite(cfg_value) or not 0 < cfg_value <= 20:
            raise ValueError("VoxCPM guidance_scale must be greater than 0 and at most 20.")
        if isinstance(sampling_steps, bool):
            raise ValueError("VoxCPM num_inference_steps must be an integer, not a boolean.")
        steps = 30 if sampling_steps in (None, 0) else sampling_steps
        if isinstance(steps, bool) or int(steps) != steps or not 1 <= int(steps) <= 100:
            raise ValueError("VoxCPM num_inference_steps must be an integer from 1 to 100 (0 uses 30).")
        error = _validate_voxcpm_reference(
            audio_guide, custom_settings, alt_prompt=alt_prompt,
            model_mode=model_mode, input_prompt=input_prompt,
        )
        if error:
            raise ValueError(error)
        settings = custom_settings or {}
        reference_text = settings.get("voxcpm_reference_text", "")
        max_len = settings.get("voxcpm_max_len", 4096)
        retry = settings.get("voxcpm_retry_badcase", 1)
        if isinstance(max_len, bool) or int(max_len) != max_len or not 2 <= int(max_len) <= 4096:
            raise ValueError("voxcpm_max_len must be an integer from 2 to 4096.")
        if isinstance(retry, bool) or retry not in (0, 1):
            raise ValueError("voxcpm_retry_badcase must be 0 (disabled) or 1 (enabled).")
        if self.model is None:
            self.load()
        if not input_prompt or not str(input_prompt).strip():
            raise ValueError("Prompt text cannot be empty for VoxCPM 2.")
        turns = parse_tts_script_turns(input_prompt)
        if not turns:
            turns = [{"speaker": "Speaker 1", "emotion": "", "line": input_prompt.strip()}]
        clone = ""
        if (
            isinstance(audio_guide, str)
            and os.path.isfile(audio_guide)
            and audio_guide.lower().endswith((".wav", ".flac", ".mp3", ".ogg"))
        ):
            clone = audio_guide
        print(f"VoxCPM synthesis: steps={int(steps)}, cfg={cfg_value}, max_len={int(max_len)}, retry_badcase={bool(retry)}")
        pieces: list[np.ndarray] = []
        for turn in turns:
            text = format_voxcpm_turn_text(
                turn["line"],
                emotion=str(turn.get("emotion") or ""),
                alt_prompt=str(alt_prompt or ""),
                model_mode=str(model_mode or ""),
            )
            if not text:
                continue
            generate_kwargs: dict[str, Any] = {
                "text": text,
                "cfg_value": cfg_value,
                "inference_timesteps": int(steps),
                "max_len": int(max_len),
                "normalize": False,
                "denoise": False,
                "retry_badcase": bool(retry),
            }
            if clone:
                generate_kwargs["reference_wav_path"] = clone
                if reference_text:
                    generate_kwargs["prompt_wav_path"] = clone
                    generate_kwargs["prompt_text"] = reference_text
            # VoxCPM tokenizes with torch.LongTensor (always CPU) then
            # torch.empty() for a zero-shot pad, which follows the implicit
            # default device. Maestro often leaves CUDA as that default, so
            # pin constructors to CPU; the model still .to(self.device) later.
            with torch.device("cpu"):
                wav = self.model.generate(**generate_kwargs)
            pieces.append(np.asarray(wav, dtype=np.float32).reshape(-1))
        audio = np.concatenate(pieces) if pieces else np.zeros(4800, dtype=np.float32)
        tensor = torch.from_numpy(audio).float()
        if tensor.ndim == 1:
            tensor = tensor.unsqueeze(0)
        return {
            "x": tensor,
            "audio_sampling_rate": _output_sample_rate(self.ckpt_root, self.model),
        }
