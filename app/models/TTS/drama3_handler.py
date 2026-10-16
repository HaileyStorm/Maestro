"""Drama 3 catalog: official FP8 experts with eager partial CPU offload."""
from __future__ import annotations

import importlib.util
import os

MODEL_TYPE = "drama3"
REPOSITORY = "fishaudio/drama-3"
REVISION = "e0a9218aa48888270f06ef1ed9ea67622c0e17d2"
FILES = (
    "config.json", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "chat_template.jinja", "LICENSE", "codec.pth",
    "model.safetensors.index.json",
    *(f"model-{i:05d}-of-00008.safetensors" for i in range(1, 9)),
)


def checkpoint_root():
    from shared.utils import files_locator as fl
    folder = fl.locate_folder("drama3", error_if_none=False, required_files=FILES)
    return folder if folder and all(os.path.isfile(os.path.join(folder, f)) for f in FILES) else None


def runtime_status():
    missing = [name for name in ("dac", "audiotools", "hydra", "omegaconf", "triton")
               if importlib.util.find_spec(name) is None]
    return {"installed": not missing, "missing_packages": missing,
            "experimental": True, "precision": "block_fp8_w8a8",
            "offload": "cpu_routed_experts", "cuda_graph": False, "optional_tower_graph": True,
            "next_step": "Install the optional Drama 3 runtime in Pinokio's terminal: python scripts/install_drama3_runtime.py" if missing else "Use an authorized generation job; GPU readiness is checked at execution."}


def speech_capabilities():
    return {"experimental": True, "native_qualified": False,
            "runtime": runtime_status(), "weights_installed": checkpoint_root() is not None,
            "performance_direction": True, "direction_field": "alt_prompt",
            "speaker_notation": "<|speaker:0|> through <|speaker:15|>, or Speaker 1: through Speaker 16:",
            "emotion_notation": "Inline [emotion or delivery] cues; model adherence is not guaranteed.",
            "maximum_speakers": 16, "maximum_reference_voices": 3,
            "reference_transcript_required": True, "maximum_reference_seconds": 60,
            "reference_fields": ["audio_guide", "audio_guide2", "audio_guide3"],
            "maximum_duration_seconds": 300, "duration_is_upper_bound": True,
            "history_scope": "Within one generation job; no cross-request history handle.",
            "separate_speaker_turns": {"field": "custom_settings.drama3_separate_turns",
                "default": 0, "values": [0, 1],
                "description": "1 generates each explicitly tagged speaker turn separately with native history. Experimental mitigation for skipped turns; words and quality still require inspection."},
            "precision": "Official block FP8 experts and BF16 backbone; CPU FP32 codec by default, optional guarded GPU FP32/private BF16 decoding; no full Q6 model variant.",
            "gpu_budget_gib": {"minimum": 8, "maximum": 26, "default": 22,
                "scope": "Weights plus worst-case expert staging; KV caches and CUDA overhead need additional VRAM."},
            "reference_cache": {"field": "custom_settings.drama3_reference_cache",
                "default": 1, "values": [0, 1],
                "description": "Automatically save exact reference codes to private disk storage and reuse bounded CPU memory. Explicit 0 disables reuse. Model/codec/source/reference bindings must match. A saved voice remains conditioning, not trained weights."},
            "prefix_cache": {"field": "custom_settings.drama3_prefix_cache",
                "default": 0, "values": [0, 1], "experimental": True,
                "scope": "One loaded model; memory only; at most 96 MiB of CPU state.",
                "description": "Reuse the exact first 128 native prefill rows when the first 129 complete prompt rows and model/cache/math bindings match. Enabled mode reserves at least 1024 cache rows; Model-setting changes or unload discard reuse. No cross-request conversation history or disk persistence."},
            "tower_graph": {"field": "custom_settings.drama3_tower_graph",
                "default": 0, "values": [0, 1], "experimental": True,
                "requires": ["drama3_prefix_cache=1", "drama3_restricted_speech_head=1",
                             "temperature=0.8", "top_k=30", "top_p=0.8"],
                "supported_kv_buckets": [256, 384], "maximum_position_exclusive": 384,
                "description": "Experimental BF16 codebook-tower graphs for the compact 4097-row speech head, 13 tower layers and 10 codebooks. First qualifying call captures; warm calls reuse. Preserves native sampling and all finite checks. Unsupported inputs, longer sequences or changed cache bindings use ordinary decoding; capture or CUDA errors fail the call. Does not enable prefix reuse, compact projection or sampler settings. Postcapture memory growth is limited to 256 MiB; transient and total VRAM peaks can be higher."},
            "expert_cache_gib": {"field": "custom_settings.drama3_expert_cache_gib",
                "minimum": 0, "maximum": 4, "default": 0,
                "maximum_weights_staging_cache_gib": 26,
                "description": "Bounded LRU of original FP8 experts on GPU. Adds up to one expert layer of pinned CPU staging; experimental. Weights, staging and this cache must total at most 26 GiB; other GPU allocations need extra headroom."},
            "restricted_speech_head": {"field": "custom_settings.drama3_restricted_speech_head",
                "default": 0, "values": [0, 1], "experimental": True,
                "description": "Project only the original EOS and semantic-token rows. Original BF16 weights and sampler; smaller matrix multiplication can change rounding."},
            "bulk_expert_pack": {"field": "custom_settings.drama3_bulk_expert_pack",
                "default": 0, "values": [0, 1], "experimental": True,
                "description": "Batch cached expert byte copies. Original FP8 weights/scales and inference math; requires a nonzero expert cache."},
            "compile_recurrence": {"field": "custom_settings.drama3_compile_recurrence",
                "default": 0, "values": [0, 1], "experimental": True,
                "description": "Compile single-token FP32 recurrent math for this model instance, without CUDA graphs. First use compiles; fused arithmetic may change generated delivery."},
            "gpu_codec": {"field": "custom_settings.drama3_gpu_codec", "default": 0,
                "values": [0, 1], "experimental": True, "max_frames_fp32": 600,
                "runtime_reserve_gib": 3,
                "description": "Optional FP32 GPU waveform decoding for up to 600 frames per chunk. Restores codec to CPU each chunk; longer chunks or insufficient VRAM use original CPU decoding. Requires 3 GiB free beyond actual codec tensors."},
            "gpu_codec_bf16": {"field": "custom_settings.drama3_gpu_codec_bf16", "default": 0,
                "values": [0, 1], "experimental": True, "requires": "drama3_gpu_codec=1",
                "max_frames": 256,
                "description": "Use a private BF16 copy for GPU decoding, up to 256 frames per chunk. Longer chunks use original CPU FP32 decoding. Original CPU reference encoder stays FP32. Changes waveform arithmetic; compare retained WAVs."},
            "execution": "Existing project-authorized generation queue; NVIDIA CUDA and Triton required.",
            "terms": "fish_drama3_self_review"}


def model_definition():
    return {
        "audio_only": True, "returns_audio": True, "image_outputs": False,
        "sliding_window": False, "guidance_max_phases": 0,
        "no_negative_prompt": True, "inference_steps": False, "temperature": True,
        "top_k_slider": True, "top_p_slider": True, "compile": False, "dtype": "bf16",
        "source": True, "image_prompt_types_allowed": "", "supports_early_stop": True,
        "profiles_dir": [MODEL_TYPE], "parent_model_type": MODEL_TYPE,
        "automatic_routing": False, "verified": False,
        "compatible_model_paths": {FILES[-8]: ["drama3/" + FILES[-8]]},
        "required_runtime_assets": {"processor": ["drama3/" + f for f in FILES]},
        "duration_slider": {"label": "Maximum speech duration (seconds)", "min": 1,
                            "max": 300, "increment": 1, "default": 20},
        "any_audio_prompt": True, "max_voice_count": 3, "one_speaker_only": True,
        "audio_guide_label": "Speaker 0 reference audio (optional; transcript required)",
        "audio_prompt_type_sources": {"selection": ["", "A", "AB", "ABC"],
            "labels": {"": "Text only", "A": "One reference voice", "AB": "Two reference voices",
                       "ABC": "Three reference voices"}, "letters_filter": "ABC", "default": ""},
        "alt_prompt": {"label": "Performance direction", "placeholder": "whispering, tense",
                       "lines": 2},
        "custom_settings": [
            {"id": "drama3_separate_turns", "label": "Generate each marked speaker turn separately (0 or 1)",
             "type": "int", "default": 0},
            {"id": "drama3_gpu_budget_gib", "label": "GPU weights and staging budget (GiB)",
             "type": "float", "default": 22.0},
            {"id": "drama3_reference_cache", "label": "Save and reuse prepared voices (0 or 1)",
             "type": "int", "default": 1},
            {"id": "drama3_prefix_cache", "label": "Reuse exact speech prefix in memory (0 or 1; experimental)",
             "type": "int", "default": 0},
            {"id": "drama3_tower_graph", "label": "Experimental tower graphs (0 or 1; requires prefix, compact head and 0.8/30/0.8 sampling)",
             "type": "int", "default": 0},
            {"id": "drama3_expert_cache_gib", "label": "FP8 expert cache (0–4 GiB; total GPU budget at most 26 GiB; experimental)",
             "type": "float", "default": 0.0},
            {"id": "drama3_restricted_speech_head", "label": "Compact speech projection (0 or 1; experimental)",
             "type": "int", "default": 0},
            {"id": "drama3_bulk_expert_pack", "label": "Batch cached expert copies (0 or 1; experimental)",
             "type": "int", "default": 0},
            {"id": "drama3_compile_recurrence", "label": "Compile recurrent decoding (0 or 1; first use compiles; experimental)",
             "type": "int", "default": 0},
            {"id": "drama3_gpu_codec", "label": "GPU waveform decoding (0 or 1; experimental)",
             "type": "int", "default": 0},
            {"id": "drama3_gpu_codec_bf16", "label": "BF16 GPU decoder (0 or 1; requires GPU decoding; experimental)",
             "type": "int", "default": 0},
            *[{"id": "drama3_reference_text" + (str(i) if i > 1 else ""),
               "label": f"Speaker {i-1} reference transcript", "type": "text", "default": ""}
              for i in range(1, 4)],
        ],
    }


class family_handler:
    @staticmethod
    def query_supported_types(): return [MODEL_TYPE]
    @staticmethod
    def query_family_maps(): return {}, {}
    @staticmethod
    def query_model_family(): return "tts"
    @staticmethod
    def query_family_infos(): return {"tts": (200, "TTS")}
    @staticmethod
    def get_lora_dir(base_model_type, args, lora_root): return os.path.join(lora_root, MODEL_TYPE)
    @staticmethod
    def query_model_def(base_model_type, model_def): return model_definition()
    @staticmethod
    def query_model_files(computeList, base_model_type, model_def=None):
        return {"repoId": REPOSITORY, "revision": REVISION, "sourceFolderList": [""],
                "targetFolderList": [MODEL_TYPE], "fileList": [list(FILES)]}
    @staticmethod
    def validate_generative_settings(base_model_type, model_def, inputs):
        status = runtime_status()
        if not status["installed"]: return status["next_step"]
        from .drama3.pipeline import validate_inputs
        return validate_inputs(inputs)
    @staticmethod
    def load_model(model_filename, model_type, base_model_type, model_def, **kwargs):
        from .drama3.pipeline import Drama3Pipeline
        if checkpoint_root() is None:
            raise RuntimeError("Download the complete Drama 3 model before generating speech.")
        # The pipeline owns expert placement. MMGP must not move its FP8 buffers.
        return Drama3Pipeline(checkpoint_root()), {}
    @staticmethod
    def fresh_default_settings(base_model_type, model_def): return {"audio_prompt_type": ""}
    @staticmethod
    def update_default_settings(base_model_type, model_def, ui_defaults):
        ui_defaults.update(audio_prompt_type="", repeat_generation=1, video_length=0,
                           num_inference_steps=0, negative_prompt="", temperature=0.7,
                           top_p=0.7, top_k=30, guidance_scale=1.0,
                           multi_prompts_gen_type=2, duration_seconds=20)
