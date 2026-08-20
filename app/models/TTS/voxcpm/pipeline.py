"""Runtime wrapper for OpenBMB VoxCPM 2."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import numpy as np
import torch

from shared.utils import files_locator as fl

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
        self.ckpt_root = ckpt_root or os.path.join(fl.get_download_location(), "voxcpm2")
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
        **_kwargs: Any,
    ) -> dict[str, Any]:
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
        try:
            cfg_value = float(guidance_scale)
        except (TypeError, ValueError):
            cfg_value = 2.0
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
                "inference_timesteps": 10,
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
