"""Runtime wrapper for OpenBMB VoxCPM 2."""

from __future__ import annotations

import json
import math
import os
from typing import Any, Optional

import numpy as np
import torch

from shared.utils import files_locator as fl
from ..voxcpm_handler import _get_voxcpm2_checkpoint_root

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

    def load(self) -> "VoxCpmPipeline":
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
        return self

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
        settings = custom_settings or {}
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
