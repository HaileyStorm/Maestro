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
        "guidance_max_phases": 0,
        "no_negative_prompt": True,
        "inference_steps": False,
        "temperature": True,
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
        "custom_settings": [
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
                "num_inference_steps": 0,
                "negative_prompt": "",
                "temperature": 0.8,
                "guidance_scale": 1.0,
                "multi_prompts_gen_type": 2,
                "duration_seconds": 20,
            }
        )
