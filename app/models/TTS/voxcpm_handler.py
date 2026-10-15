"""Catalog and settings for OpenBMB VoxCPM 2 speech."""

from __future__ import annotations

import os

from .prompt_enhancers import VOXCPM_SCRIPT_PROMPT


VOXCPM2_MODEL_TYPE = "voxcpm2"
VOXCPM2_REPO = "openbmb/VoxCPM2"
VOXCPM2_FOLDER = "voxcpm2"
VOXCPM2_FILES = (
    "model.safetensors",
    "audiovae.pth",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "tokenization_voxcpm2.py",
)


def _validate_voxcpm_reference(audio_guide, custom_settings=None, *,
                              alt_prompt="", model_mode="", input_prompt=""):
    settings = {} if custom_settings is None else custom_settings
    if not isinstance(settings, dict):
        return "VoxCPM custom settings must be an object."
    transcript = settings.get("voxcpm_reference_text", "")
    if not isinstance(transcript, str):
        return "VoxCPM reference transcript must be a string."
    try:
        size = len(transcript.encode("utf-8"))
    except UnicodeEncodeError:
        return "VoxCPM reference transcript must contain valid UTF-8 text."
    if size > 16000:
        return "VoxCPM reference transcript is limited to 16000 UTF-8 bytes."
    if transcript:
        if not transcript.strip():
            return "VoxCPM reference transcript must contain text or be empty to disable it."
        if not (isinstance(audio_guide, str) and os.path.isfile(audio_guide)
                and audio_guide.lower().endswith((".wav", ".flac", ".mp3", ".ogg"))):
            return "VoxCPM reference transcript requires an existing WAV, FLAC, MP3 or OGG reference audio file."
        from services.tts_script_breakdown import parse_tts_script_turns
        if (str(alt_prompt or "").strip() or str(model_mode or "").strip()
                or any(turn.get("emotion") for turn in parse_tts_script_turns(input_prompt))):
            return (
                "VoxCPM audio-plus-transcript cloning requires empty voice directions "
                "(alt_prompt, model_mode and speaker emotion tags): this mode can read "
                "them aloud. Use the reference recording's delivery, or clear the "
                "reference transcript to use directed reference-only cloning."
            )
    return None


def _get_voxcpm2_checkpoint_root():
    from shared.utils import files_locator as fl

    folder = fl.locate_folder(
        VOXCPM2_FOLDER, error_if_none=False, required_files=VOXCPM2_FILES,
    )
    # The shared locator returns a partial folder as a best-effort fallback.
    # VoxCPM needs its weights, codec and tokenizer together in one folder.
    if folder and all(os.path.isfile(os.path.join(folder, name)) for name in VOXCPM2_FILES):
        return folder
    return None


def _get_voxcpm2_model_def() -> dict:
    return {
        "audio_only": True,
        "image_outputs": False,
        "sliding_window": False,
        "guidance_max_phases": 1,
        "no_negative_prompt": True,
        "inference_steps": True,
        "image_prompt_types_allowed": "",
        "supports_early_stop": True,
        "profiles_dir": [VOXCPM2_FOLDER],
        "duration_slider": {
            "label": "Max duration (seconds)",
            "min": 1,
            "max": 300,
            "increment": 1,
            "default": 20,
        },
        "audio_guide_label": "Voice to clone (optional)",
        "any_audio_prompt": True,
        "audio_prompt_type_sources": {
            "selection": ["", "A"],
            "labels": {"": "Text only", "A": "Voice cloning (1 reference audio)"},
            "letters_filter": "A",
            "default": "",
        },
        "max_voice_count": 1,
        "alt_prompt": {
            "label": "Voice / emotion instruction",
            "placeholder": "warm, unhurried, slightly amused",
            "lines": 2,
        },
        "reference_text_cloning": {
            "field": "custom_settings.voxcpm_reference_text",
            "default": "",
            "maximum_utf8_bytes": 16000,
            "audio_field": "audio_guide",
            "mode": "reference_and_continuation",
            "description": "An exact reference transcript enables cloning with the same clip as voice reference and continuation audio. Empty keeps ordinary cloning or text-only speech.",
            "direction_limitation": "Separate voice directions are unsupported with audio continuation and are rejected because they can be spoken aloud. The reference supplies its delivery.",
            "experimental": True,
            "direction_supported": False,
        },
        "custom_settings": [
            {
                "id": "voxcpm_reference_text",
                "label": "Reference audio transcript (optional; enables continuation cloning)",
                "name": "Reference transcript",
                "type": "text", "default": "",
                "description": "Exact words in the reference clip, up to 16000 UTF-8 bytes. Requires reference audio and empty separate voice directions; delivery comes from the recording.",
            },
            {
                "id": "voxcpm_max_len", "label": "Speech token limit (2-4096)",
                "name": "Speech token limit", "type": "int", "default": 4096,
            },
            {
                "id": "voxcpm_retry_badcase", "label": "Automatic bad-case retries (0=off, 1=on)",
                "name": "Automatic retries", "type": "int", "default": 1,
            },
            {
                "id": "voxcpm_speaker_count",
                "label": "Speakers (1-4)",
                "name": "Speakers",
                "type": "int",
                "default": 1,
            },
        ],
        "text_prompt_enhancer_instructions": VOXCPM_SCRIPT_PROMPT,
        "text_prompt_enhancer_max_tokens": 768,
        "prompt_enhancer_button_label": "Write speakers and emotion",
        "parent_model_type": VOXCPM2_MODEL_TYPE,
    }


def _get_voxcpm2_download_def() -> dict:
    return {
        "repoId": VOXCPM2_REPO,
        "sourceFolderList": [""],
        "targetFolderList": [VOXCPM2_FOLDER],
        "fileList": [list(VOXCPM2_FILES)],
    }


class family_handler:
    @staticmethod
    def query_supported_types():
        return [VOXCPM2_MODEL_TYPE]

    @staticmethod
    def query_family_maps():
        return {}, {}

    @staticmethod
    def query_model_family():
        return "tts"

    @staticmethod
    def query_family_infos():
        return {"tts": (200, "TTS")}

    @staticmethod
    def register_lora_cli_args(parser, lora_root):
        parser.add_argument(
            "--lora-dir-voxcpm2",
            type=str,
            default=None,
            help=(
                "Path to a directory that contains VoxCPM 2 settings "
                f"(default: {os.path.join(lora_root, VOXCPM2_FOLDER)})"
            ),
        )

    @staticmethod
    def get_lora_dir(base_model_type, args, lora_root):
        return getattr(args, "lora_dir_voxcpm2", None) or os.path.join(
            lora_root, VOXCPM2_FOLDER,
        )

    @staticmethod
    def query_model_def(base_model_type, model_def):
        return _get_voxcpm2_model_def()

    @staticmethod
    def query_model_files(computeList, base_model_type, model_def=None):
        return _get_voxcpm2_download_def()

    @staticmethod
    def validate_generative_settings(base_model_type, model_def, inputs):
        error = _validate_voxcpm_reference(
            inputs.get("audio_guide"), inputs.get("custom_settings"),
            alt_prompt=inputs.get("alt_prompt"), model_mode=inputs.get("model_mode"),
            input_prompt=inputs.get("prompt", ""),
        )
        if error:
            return error
        if not _get_voxcpm2_checkpoint_root():
            return (
                "VoxCPM 2 is listed, but its weights are not installed on this "
                "Maestro yet. Download VoxCPM 2 from the model list, then try again."
            )
        return None

    @staticmethod
    def load_model(
        model_filename,
        model_type,
        base_model_type,
        model_def,
        quantizeTransformer=False,
        text_encoder_quantization=None,
        dtype=None,
        VAE_dtype=None,
        mixed_precision_transformer=False,
        save_quantized=False,
        submodel_no_list=None,
        text_encoder_filename=None,
        profile=0,
        **kwargs,
    ):
        ckpt_root = _get_voxcpm2_checkpoint_root()
        if ckpt_root is None:
            raise RuntimeError("VoxCPM 2 weights are not installed on this Maestro yet.")
        from .voxcpm.pipeline import VoxCpmPipeline

        pipeline = VoxCpmPipeline(ckpt_root=ckpt_root).load()
        return pipeline, {"voxcpm": pipeline.model}

    @staticmethod
    def fresh_default_settings(base_model_type, model_def):
        return {"audio_prompt_type": ""}

    @staticmethod
    def update_default_settings(base_model_type, model_def, ui_defaults):
        ui_defaults.update(
            {
                "audio_prompt_type": "",
                "repeat_generation": 1,
                "video_length": 0,
                "num_inference_steps": 30,
                "negative_prompt": "",
                "temperature": 0.8,
                "guidance_scale": 2.0,
                "multi_prompts_gen_type": 2,
                "duration_seconds": 20,
            }
        )
