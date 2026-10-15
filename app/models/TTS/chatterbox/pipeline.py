from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional
from shared.utils import files_locator as fl 
import torch

from services.prepared_voice_cache import PreparedVoiceCache

from .mtl_tts import ChatterboxMultilingualTTS, Conditionals, SUPPORTED_LANGUAGES
from .models.t3.modules.cond_enc import T3Cond


class ChatterboxPipeline:
    """
    Thin wrapper around Chatterbox's multilingual TTS to fit WanGP's model API expectations.
    """

    def __init__(
        self,
        ckpt_root: Optional[Path] = None,
        device: Optional[torch.device] = None,
    ) -> None:
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.ckpt_root = Path(ckpt_root) if ckpt_root is not None else None
        self.model = self._load_model()
        self.model.ve._model_dtype = torch.float32
        self.model.s3gen._model_dtype = torch.float32
        self.model.t3._model_dtype = torch.float32
        self.model.conds._model_dtype = torch.float32

        self.sr = getattr(self.model, "sr", 44100)
        self._interrupt = False
        self._prepared_voice_cache = None

    @property
    def prepared_voice_cache(self):
        if self._prepared_voice_cache is not None:
            return self._prepared_voice_cache
        versions = {}
        for package in ("torch", "torchaudio", "librosa", "numpy", "scipy", "soundfile",
                        "soxr", "audioread", "s3tokenizer", "mmgp"):
            try:
                versions[package] = version(package)
            except PackageNotFoundError:
                versions[package] = None
        self._prepared_voice_cache = PreparedVoiceCache(
            family="chatterbox",
            model_files=[
                fl.locate_file(os.path.join("chatterbox", name), error_if_none=False)
                for name in ("ve.safetensors", "t3_mtl23ls_v2.safetensors", "s3gen.pt")
            ],
            implementation_files=sorted(Path(__file__).parent.rglob("*.py")),
            options={"format": 1, "versions": versions},
        )
        return self._prepared_voice_cache

    @property
    def supported_languages(self):
        return SUPPORTED_LANGUAGES

    def _load_model(self):
        return ChatterboxMultilingualTTS.from_local(self.ckpt_root, device=self.device)
        # return ChatterboxMultilingualTTS.from_pretrained(device=self.device)

    def prepare_reference(self, audio_prompt_path: Optional[str], exaggeration: float) -> None:
        if not audio_prompt_path:
            return
        if not os.path.isfile(audio_prompt_path):
            raise FileNotFoundError(f"Audio prompt file '{audio_prompt_path}' not found.")
        def prepare():
            self.model.prepare_conditionals(audio_prompt_path, exaggeration=exaggeration)
            conds = self.model.conds
            return {
                "t3": {name: getattr(conds.t3, name) for name in (
                    "speaker_emb", "clap_emb", "cond_prompt_speech_tokens",
                    "cond_prompt_speech_emb", "emotion_adv",
                )},
                "gen": {name: conds.gen[name] for name in (
                    "prompt_token", "prompt_token_len", "prompt_feat", "prompt_feat_len", "embedding",
                )},
            }

        prepared = self.prepared_voice_cache.get_or_prepare(
            references={"voice": audio_prompt_path},
            settings={
                "exaggeration": exaggeration,
                "sample_rate": self.sr,
                "encoder_samples": self.model.ENC_COND_LEN,
                "decoder_samples": self.model.DEC_COND_LEN,
                "speech_cond_prompt_len": self.model.t3.hp.speech_cond_prompt_len,
                "device": str(self.model.device),
                "dtypes": {
                    name: [str(next(module.parameters()).dtype), str(getattr(module, "_model_dtype", None))]
                    for name, module in (("ve", self.model.ve), ("s3gen", self.model.s3gen), ("t3", self.model.t3))
                },
            },
            prepare=prepare,
        )
        self.model.conds = Conditionals(T3Cond(**prepared["t3"]), prepared["gen"]).to(self.model.device)
        self.model.conds._model_dtype = torch.float32

    def generate(
        self,
        input_prompt: str,
        model_mode: Optional[str],
        audio_guide: Optional[str],
        *,
        temperature: float = 0.8,
        repetition_penalty: float = 2.0,
        min_p: float = 0.05,
        top_p: float = 1.0,
        sampling_steps: int = 10,
        **bkwargs
    ) -> torch.Tensor:
        text = input_prompt
        if not text or not text.strip():
            raise ValueError("Prompt text cannot be empty for Chatterbox generation.")

        if (isinstance(sampling_steps, bool) or not isinstance(sampling_steps, (int, float))
                or not 0 <= sampling_steps <= 100 or int(sampling_steps) != sampling_steps):
            raise ValueError("Chatterbox acoustic steps must be an integer from 1 to 100 (0 uses 10).")
        num_inference_steps = int(sampling_steps) or 10

        language_id = model_mode
        custom_settings = bkwargs.get("custom_settings", None)
        if not isinstance(custom_settings, dict):
            custom_settings = {}
        raw_exaggeration = custom_settings.get("exaggeration", bkwargs.get("exaggeration", 0.5))
        raw_pace = custom_settings.get("pace", bkwargs.get("pace", 0.5))
        try:
            exaggeration = float(raw_exaggeration)
        except (TypeError, ValueError):
            exaggeration = 0.5
        try:
            cfg_weight = float(raw_pace)
        except (TypeError, ValueError):
            cfg_weight = 0.5
        exaggeration = min(2.0, max(0.25, exaggeration))
        cfg_weight = min(1.0, max(0.2, cfg_weight))

        cfg_override = bkwargs.get("cfg_scale", None)
        if cfg_override is not None:
            try:
                cfg_weight = float(cfg_override)
            except (TypeError, ValueError):
                pass
        if language_id:
            language_id = language_id.lower()
            if language_id not in SUPPORTED_LANGUAGES:
                raise ValueError(
                    f"Unsupported language '{language_id}'. "
                    f"Supported languages: {', '.join(sorted(SUPPORTED_LANGUAGES.keys()))}"
                )


        self.prepare_reference(audio_guide, exaggeration)
        wav = self.model.generate(
            text=text,
            language_id=language_id,
            audio_prompt_path=None,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            temperature=temperature,
            repetition_penalty=repetition_penalty,
            min_p=min_p,
            top_p=top_p,
            num_inference_steps=num_inference_steps,
        )
        return {"x": wav, "audio_sampling_rate": self.sr } 

    def release(self) -> None:
        if self._prepared_voice_cache is not None:
            self._prepared_voice_cache.close()
        if hasattr(self.model, "to"):
            self.model.to("cpu")
        self.model = None
        torch.cuda.empty_cache()
