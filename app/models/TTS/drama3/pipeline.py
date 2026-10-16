"""Expressive local TTS through Fish's native generation and Maestro's job path."""
from __future__ import annotations

import gc
import copy
from contextlib import nullcontext
import math
import re
import time
from importlib.metadata import distribution, version
from pathlib import Path

import torch

from services.prepared_voice_cache import PreparedVoiceCache

from . import activate_fish_source


def validate_inputs(inputs):
    custom = inputs.get("custom_settings") or {}
    separate_turns = custom.get("drama3_separate_turns", 0)
    if type(separate_turns) is not int or separate_turns not in (0, 1):
        return "Drama 3 separate speaker turns must be 0 or 1."
    budget = custom.get("drama3_gpu_budget_gib", 22.0)
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or not 8 <= budget <= 26:
        return "Drama 3 GPU weights and staging budget must be between 8 and 26 GiB."
    reference_cache = custom.get("drama3_reference_cache", 1)
    if type(reference_cache) is not int or reference_cache not in (0, 1):
        return "Drama 3 reference cache must be 0 or 1."
    for field in ("drama3_restricted_speech_head", "drama3_bulk_expert_pack",
                  "drama3_compile_recurrence", "drama3_gpu_codec", "drama3_gpu_codec_bf16",
                  "drama3_prefix_cache", "drama3_tower_graph"):
        if type(custom.get(field, 0)) is not int or custom.get(field, 0) not in (0, 1):
            return f"{field} must be 0 or 1."
    if custom.get("drama3_gpu_codec_bf16", 0) and not custom.get("drama3_gpu_codec", 0):
        return "Drama 3 BF16 codec requires GPU codec decoding."
    expert_cache = custom.get("drama3_expert_cache_gib", 0.0)
    if (isinstance(expert_cache, bool) or not isinstance(expert_cache, (int, float))
            or not math.isfinite(expert_cache) or not 0 <= expert_cache <= 4):
        return "Drama 3 expert cache must be between 0 and 4 GiB."
    if budget + expert_cache > 26:
        return "Drama 3 GPU weights, staging and expert cache must total at most 26 GiB."
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
        self._backbone = self.codec = self._bf16_codec = None
        self._interrupt = self._early_stop = False
        self._decode_stats = []
        self._budget = None
        self._prefix_cache = None
        self._tower_graph = None
        self._tower_graph_last_stats = None
        source_root = Path(__file__).parent
        vendor = source_root / "vendor/fish_speech"
        self.prepared_voice_cache = PreparedVoiceCache(
            "drama3",
            model_files=[Path(ckpt_root) / "codec.pth"],
            implementation_files=[
                Path(__file__), source_root / "worker.py",
                vendor / "configs/modded_dac_vq.yaml",
                *sorted((vendor / "models/dac").glob("*.py")),
                *[distribution("descript-audio-codec").locate_file(name) for name in (
                    "dac/model/base.py", "dac/nn/layers.py", "dac/nn/quantize.py")],
                distribution("descript-audiotools").locate_file("audiotools/core/audio_signal.py"),
            ],
            options={
                "codec_dtype": "float32", "codec_device": "cpu",
                **{name: version(name) for name in (
                    "torch", "numpy", "torchaudio", "soundfile",
                    "descript-audio-codec", "descript-audiotools")},
            },
        )
        self.reference_cache_stats = self.prepared_voice_cache.stats

    def prefix_cache_stats(self):
        if self._prefix_cache is not None:
            return self._prefix_cache.snapshot()
        return {"enabled": False, "hits": 0, "misses": 0, "restored_bytes": 0,
                "resident_cpu_bytes": 0}

    def tower_graph_stats(self):
        if self._tower_graph is not None:
            return self._tower_graph.snapshot()
        return self._tower_graph_last_stats or {"enabled": False, "prepared": False,
            "setups": 0, "replays": 0, "fallback_calls": 0, "fallback_reasons": {}}

    def _close_tower_graph(self):
        if self._tower_graph is not None:
            self._tower_graph.close()
            self._tower_graph_last_stats = self._tower_graph.snapshot()
            self._tower_graph = None

    def _reset_models(self):
        self._close_tower_graph()
        if self._prefix_cache is not None:
            self._prefix_cache.close()
            self._prefix_cache = None
        self._backbone = self.codec = self._bf16_codec = None
        self._budget = None
        gc.collect()

    def release(self):
        self._reset_models()
        self.prepared_voice_cache.close()

    def _cancelled(self):
        return self._interrupt

    def _encode_reference(self, path, encoder, enabled, label, progress):
        prepare = lambda: _run_timed_phase(label, encoder, path, self.codec, progress=progress)
        if not enabled:
            return prepare()
        if callable(progress):
            progress(label)
        return self.prepared_voice_cache.get_or_prepare(
            references={"reference_audio": path},
            settings={"sample_rate": int(self.codec.sample_rate)},
            prepare=prepare,
        )

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
        expert_cache = float(custom.get("drama3_expert_cache_gib", 0.0))
        reference_cache = custom.get("drama3_reference_cache", 1)
        restricted_head = bool(custom.get("drama3_restricted_speech_head", 0))
        bulk_pack = bool(custom.get("drama3_bulk_expert_pack", 0))
        compile_recurrence = bool(custom.get("drama3_compile_recurrence", 0))
        prefix_cache = bool(custom.get("drama3_prefix_cache", 0))
        tower_graph = bool(custom.get("drama3_tower_graph", 0))
        gpu_codec = bool(custom.get("drama3_gpu_codec", 0))
        bf16_codec = bool(custom.get("drama3_gpu_codec_bf16", 0))
        self._decode_stats = []
        self._interrupt = self._early_stop = False
        if self._budget != (budget, expert_cache, restricted_head, bulk_pack, compile_recurrence, prefix_cache):
            self._reset_models()
        if self._backbone is None:
            if callable(set_progress_status): set_progress_status("Loading Drama 3 FP8 with CPU expert offload")
            self._backbone = load_offloaded_model(self.ckpt_root, gpu_budget_gib=budget,
                expert_cache_gib=expert_cache, cancel_check=self._cancelled,
                restricted_speech_head=restricted_head, bulk_expert_pack=bulk_pack,
                compile_recurrence=compile_recurrence)
            self._budget = (budget, expert_cache, restricted_head, bulk_pack, compile_recurrence, prefix_cache)
        if prefix_cache and self._prefix_cache is None:
            from .prefix_cache import PrefixCache
            self._prefix_cache = PrefixCache(self._backbone, checkpoint_root=self.ckpt_root,
                                             cancel_check=self._cancelled)
        if self._tower_graph is not None and (not tower_graph or self._tower_graph.closed):
            self._close_tower_graph()
        if tower_graph and self._tower_graph is None:
            from .tower_graph import TowerGraph
            self._tower_graph = TowerGraph(self._backbone, cancel_check=self._cancelled,
                prefix_provider=lambda: self._prefix_cache)
        if self.codec is None:
            if callable(set_progress_status): set_progress_status("Loading the local speech codec")
            self.codec = load_codec(Path(self.ckpt_root) / "codec.pth")
        if seed is not None and int(seed) >= 0: torch.manual_seed(int(seed))
        references, transcripts = [], []
        for i, path in enumerate(guides):
            if not path: continue
            if self._cancelled(): raise InterruptedError("Drama 3 generation cancelled")
            transcripts.append(performance_text(custom["drama3_reference_text" + (str(i+1) if i else "")]))
            if not re.search(r"<\|speaker:\d+\|>", transcripts[-1]):
                transcripts[-1] = f"<|speaker:{i}|> " + transcripts[-1]
            references.append(self._encode_reference(path, encode_reference, reference_cache,
                f"Encoding Drama 3 reference {i+1}", set_progress_status))
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
            prefix_context = (self._prefix_cache.generation(prompt, max_new_tokens=cap)
                              if self._prefix_cache is not None else nullcontext())
            tower_context = (self._tower_graph.generation(prefix_enabled=prefix_cache,
                temperature=float(temperature), top_k=int(top_k), top_p=float(top_p))
                if self._tower_graph is not None else nullcontext())
            with prefix_context, tower_context:
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
                decode_stats = {"private_copy_seconds": 0.0}
                decoder = self.codec
                if gpu_codec and bf16_codec:
                    if self._bf16_codec is None:
                        started_copy = time.monotonic()
                        self._bf16_codec = copy.deepcopy(self.codec).to(device="cpu", dtype=torch.bfloat16)
                        decode_stats["private_copy_seconds"] = time.monotonic() - started_copy
                    decoder = self._bf16_codec
                self._decode_stats.append(decode_stats)
                pieces.append(_run_timed_phase(
                    f"Decoding Drama 3 audio · part {index+1}/{len(chunks)}",
                    decode_audio, codes, decoder, use_gpu=gpu_codec,
                    fallback_codec=self.codec, stats=decode_stats,
                    progress=set_progress_status,
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
        if reference_cache or expert_cache:
            print(f"Drama 3 cache counters: references={self.reference_cache_stats}, "
                  f"experts={self._backbone.offload_placement['expert_cache']}", flush=True)
        return {"x": torch.cat(pieces).unsqueeze(0), "audio_sampling_rate": 44100,
                "finish_reason": finish}
