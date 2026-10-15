"""Expressive local TTS through Fish's native generation and Maestro's job path."""
from __future__ import annotations

import gc
import math
import re
import time

import torch

from . import activate_fish_source


def validate_inputs(inputs):
    custom = inputs.get("custom_settings") or {}
    separate_turns = custom.get("drama3_separate_turns", 0)
    if type(separate_turns) is not int or separate_turns not in (0, 1):
        return "Drama 3 separate speaker turns must be 0 or 1."
    budget = custom.get("drama3_gpu_budget_gib", 22.0)
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or not 8 <= budget <= 26:
        return "Drama 3 GPU weights and staging budget must be between 8 and 26 GiB."
    for i in range(1, 4):
        key = "audio_guide" + (str(i) if i > 1 else "")
        text = custom.get("drama3_reference_text" + (str(i) if i > 1 else ""), "")
        if inputs.get(key) and (not isinstance(text, str) or not text.strip()):
            return f"Supply the actual reference transcript for Speaker {i-1}."
        if isinstance(text, str) and len(text.encode("utf-8")) > 16000:
            return "Drama 3 reference transcripts are limited to 16000 UTF-8 bytes."
    return None


def performance_text(text, instruction=""):
    # Preserve native speaker/cue notation. Ordinary Maestro Speaker N lines
    # map to canonical zero-based Fish speaker tokens.
    text = re.sub(r"(?m)^\s*Speaker\s+([1-9]|1[0-6])(?:\s*\[([^\]]+)\])?\s*:\s*",
                  lambda m: f"<|speaker:{int(m[1])-1}|> " + (f"[{m[2]}] " if m[2] else ""), text)
    if instruction.strip():
        cue = f"[{instruction.strip().strip('[]')}] "
        text = re.sub(r"(<\|speaker:\d+\|>)\s*", lambda m: m[1] + " " + cue, text) if "<|speaker:" in text else cue + text
    return text


def _run_timed_phase(label, operation, *args, progress=None, **kwargs):
    """Report real operation boundaries without recording speech or references."""
    if callable(progress):
        progress(label)
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    returned = False
    try:
        result = operation(*args, **kwargs)
        returned = True
        return result
    finally:
        # Process CPU time includes concurrent request handling; it is not
        # exclusive codec/model time. Exceptions keep their original behavior.
        print(f"Drama 3 phase ended: phase={label}, returned={returned}, "
              f"wall_seconds={time.perf_counter()-wall_start:.3f}, "
              f"process_cpu_seconds={time.process_time()-cpu_start:.3f}", flush=True)


class Drama3Pipeline:
    def __init__(self, ckpt_root):
        self.ckpt_root = ckpt_root
        # WanGP sets cache state before calling generate(). Keep its execution
        # surface available while the custom offloader lazily owns the backbone.
        # This empty module has no weights for MMGP to relocate.
        self.model = torch.nn.Module()
        self.model.cache = None
        self._backbone = self.codec = None
        self._interrupt = self._early_stop = False
        self._budget = None

    def release(self):
        self._backbone = self.codec = None
        self._budget = None
        gc.collect()

    def _cancelled(self):
        return self._interrupt

    @torch.inference_mode()
    def generate(self, input_prompt, *, alt_prompt="", audio_guide=None,
                 audio_guide2=None, audio_guide3=None, duration_seconds=20,
                 temperature=0.7, top_p=0.7, top_k=30, seed=-1,
                 custom_settings=None, callback=None, set_progress_status=None, **kwargs):
        custom = custom_settings or {}
        guides = [audio_guide, audio_guide2, audio_guide3]
        error = validate_inputs({"custom_settings": custom,
            **{"audio_guide" + (str(i+1) if i else ""): path for i, path in enumerate(guides)}})
        if error: raise ValueError(error)
        if not isinstance(input_prompt, str) or not input_prompt.strip():
            raise ValueError("Drama 3 needs nonempty speech text.")
        if not 1 <= float(duration_seconds) <= 300:
            raise ValueError("Drama 3 maximum speech duration must be 1–300 seconds.")
        if not (0 < float(temperature) <= 2 and 0 < float(top_p) <= 1 and 1 <= int(top_k) <= 4096):
            raise ValueError("Drama 3 expects temperature >0 and <=2, top_p >0 and <=1, top_k 1–4096.")
        activate_fish_source()
        from .offload import load_offloaded_model
        from .worker import load_codec, encode_reference, decode_audio
        from fish_speech.models.text2semantic.fish_omni.generate import (
            build_drama3_prompt, generate_drama3, Drama3PromptTooLongError,
        )
        from fish_speech.models.text2semantic.fish_omni.chunking import (
            split_drama3_text, parse_speaker_segments, Drama3History,
        )
        budget = float(custom.get("drama3_gpu_budget_gib", 22.0))
        self._interrupt = self._early_stop = False
        if self._budget != budget:
            self.release()
        if self._backbone is None:
            if callable(set_progress_status): set_progress_status("Loading Drama 3 FP8 with CPU expert offload")
            self._backbone = load_offloaded_model(self.ckpt_root, gpu_budget_gib=budget, cancel_check=self._cancelled)
            self._budget = budget
        if self.codec is None:
            if callable(set_progress_status): set_progress_status("Loading the local speech codec")
            from pathlib import Path
            self.codec = load_codec(Path(self.ckpt_root) / "codec.pth")
        if seed is not None and int(seed) >= 0: torch.manual_seed(int(seed))
        references, transcripts = [], []
        for i, path in enumerate(guides):
            if not path: continue
            if self._cancelled(): raise InterruptedError("Drama 3 generation cancelled")
            transcripts.append(performance_text(custom["drama3_reference_text" + (str(i+1) if i else "")]))
            if not re.search(r"<\|speaker:\d+\|>", transcripts[-1]):
                transcripts[-1] = f"<|speaker:{i}|> " + transcripts[-1]
            references.append(_run_timed_phase(
                f"Encoding Drama 3 reference {i+1}", encode_reference,
                path, self.codec, progress=set_progress_status,
            ))
        # Split speech before adding the global direction. A long protected cue
        # can otherwise fill a native chunk by itself and generate unintended
        # speech, which then contaminates continuation history.
        directed_text = performance_text(input_prompt)
        if custom.get("drama3_separate_turns", 0):
            # Keep explicitly authored turns separate, including a repeated
            # speaker with a new delivery cue. Native batching may otherwise
            # combine them into one generation and omit a later turn.
            chunks = [chunk for speaker, body in parse_speaker_segments(directed_text)
                      for chunk in split_drama3_text(f"<|speaker:{speaker}|> {body}")]
        else:
            chunks = split_drama3_text(directed_text)
        history = Drama3History()
        remaining = max(1, int(float(duration_seconds) * 44100 / 2048))
        pieces = []
        finish = "eos"
        for index, text in enumerate(chunks):
            text = performance_text(text, str(alt_prompt or ""))
            if self._cancelled(): raise InterruptedError("Drama 3 generation cancelled")
            if remaining <= 0:
                finish = "length"
                break
            if callable(set_progress_status): set_progress_status(f"Generating Drama 3 speech · part {index+1}/{len(chunks)}")
            error = None
            for prior in history.candidates():
                try:
                    # WanGP sets a process-wide CUDA default device. Fish's
                    # prompt rows and retained codes must share the CPU device.
                    with torch.device("cpu"):
                        prompt = build_drama3_prompt(text, self._backbone.tokenizer, self._backbone.config,
                                                    transcripts, references, history=prior)
                    break
                except Drama3PromptTooLongError as e: error = e
            else: raise error
            # Same official chunk budget; the overall duration is a frame cap.
            cap = min(remaining, 600, self._backbone.config.text.max_position_embeddings-len(prompt))
            if cap < 1: raise ValueError("Drama 3 prompt leaves no room for speech; shorten references or text.")
            print(f"Drama 3 synthesis: part={index+1}/{len(chunks)}, prompt_rows={len(prompt)}, "
                  f"frame_budget={cap}, temperature={float(temperature)}, "
                  f"top_p={float(top_p)}, top_k={int(top_k)}")
            frames, finish = _run_timed_phase(
                f"Generating Drama 3 speech · part {index+1}/{len(chunks)}",
                generate_drama3, self._backbone, prompt, max_new_tokens=cap,
                temperature=float(temperature), top_p=float(top_p), top_k=int(top_k),
                progress=set_progress_status,
            )
            print(f"Drama 3 synthesis finished: part={index+1}/{len(chunks)}, "
                  f"generated_frames={len(frames)}, finish_reason={finish}")
            if self._cancelled(): raise InterruptedError("Drama 3 generation cancelled")
            if len(frames):
                codes = frames[:, 1:].T.contiguous()
                pieces.append(_run_timed_phase(
                    f"Decoding Drama 3 audio · part {index+1}/{len(chunks)}",
                    decode_audio, codes, self.codec, progress=set_progress_status,
                ))
                history.push(text, codes)
                remaining -= len(frames)
            if callable(callback): callback(index+1, None, force_refresh=True,
                                            override_num_inference_steps=len(chunks))
            if self._early_stop:
                finish = "early_stop"
                break
            if finish != "eos":
                # Do not silently skip the unspoken remainder of a capped chunk.
                break
        if not pieces: raise RuntimeError("Drama 3 produced no audio; the model may have reached EOS immediately.")
        if finish == "length": print("Drama 3 reached the maximum duration/chunk frame budget; speech may be truncated.")
        return {"x": torch.cat(pieces).unsqueeze(0), "audio_sampling_rate": 44100,
                "finish_reason": finish}
