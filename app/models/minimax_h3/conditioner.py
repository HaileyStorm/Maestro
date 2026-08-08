"""Qwen3-VL layer-50 conditioning for MiniMax H3."""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoTokenizer, Qwen2VLImageProcessorFast

from models.ideogram4.qwen3_vl_configuration import Qwen3VLConfig, register_qwen3_vl_config
from models.ideogram4.qwen3_vl_transformers import Qwen3VLModel, Qwen3VLTextModel, Qwen3VLVisionModel
from models.krea2.krea2_main import Krea2Qwen3VLProcessor


VISION_START_TOKEN_ID = 151652
VISION_END_TOKEN_ID = 151653
IMAGE_TOKEN_ID = 151655
VIDEO_TOKEN_ID = 151656
TEXT_PAD_TOKEN_ID = 151643
TEXT_ENCODER_LAYERS = 50


def _visual_patches(
    frames: torch.Tensor,
    *,
    video: bool = False,
    patch_size: int = 16,
    temporal_patch_size: int = 2,
    merge_size: int = 2,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Convert one Qwen image/video pair from THWC RGB into visual patches."""

    if frames.shape[0] == 1:
        frames = frames.repeat(2, 1, 1, 1)
    if frames.shape[0] != temporal_patch_size:
        raise ValueError(
            f"Qwen visual blocks require {temporal_patch_size} frames, got {frames.shape[0]}."
        )
    _, height, width, channels = frames.shape
    if channels != 3:
        raise ValueError(f"Qwen visual input must be RGB, got {channels} channels.")
    factor = patch_size * merge_size
    min_pixels, max_pixels = ((4096, 25165824) if video else (65536, 16777216))
    target_height = max(factor, round(height / factor) * factor)
    target_width = max(factor, round(width / factor) * factor)
    if target_height * target_width > max_pixels:
        scale = math.sqrt((height * width) / max_pixels)
        target_height = max(factor, math.floor(height / scale / factor) * factor)
        target_width = max(factor, math.floor(width / scale / factor) * factor)
    elif target_height * target_width < min_pixels:
        scale = math.sqrt(min_pixels / (height * width))
        target_height = math.ceil(height * scale / factor) * factor
        target_width = math.ceil(width * scale / factor) * factor

    images = F.interpolate(
        frames.permute(0, 3, 1, 2),
        size=(target_height, target_width),
        mode="bicubic",
        align_corners=False,
        antialias=True,
    )
    images = images.mul(2.0).sub_(1.0)
    grid_height, grid_width = target_height // patch_size, target_width // patch_size
    patches = images.reshape(
        1,
        temporal_patch_size,
        3,
        grid_height // merge_size,
        merge_size,
        patch_size,
        grid_width // merge_size,
        merge_size,
        patch_size,
    )
    patches = patches.permute(0, 3, 6, 4, 7, 2, 1, 5, 8)
    flattened = patches.reshape(
        grid_height * grid_width,
        3 * temporal_patch_size * patch_size * patch_size,
    )
    grid = torch.tensor(
        [[1, grid_height, grid_width]], dtype=torch.long, device=frames.device
    )
    return flattened, grid


def _multimodal_rope_positions(visuals: list[dict], sequence_length: int, device) -> torch.Tensor | None:
    if not visuals:
        return None
    positions = torch.zeros((3, sequence_length), dtype=torch.long, device=device)
    cursor = offset = 0
    for visual in visuals:
        start, size, grid = visual["index"], visual["size"], visual["grid"]
        if cursor < start:
            positions[:, cursor:start] = torch.arange(cursor + offset, start + offset, device=device)
        end = start + size
        max_grid = int(grid.max()) // 2
        rows = int(grid[0, 1]) // 2
        columns = int(grid[0, 2]) // 2
        positions[0, start:end] = start + offset
        positions[1, start:end] = (
            torch.arange(start + offset, start + rows + offset, device=device)
            .unsqueeze(1)
            .expand(rows, columns)
            .reshape(-1)[:size]
        )
        positions[2, start:end] = (
            torch.arange(start + offset, start + columns + offset, device=device)
            .unsqueeze(0)
            .expand(rows, columns)
            .reshape(-1)[:size]
        )
        offset += max_grid - size
        cursor = end
    if cursor < sequence_length:
        positions[:, cursor:] = torch.arange(
            cursor + offset, sequence_length + offset, device=device
        )
    return positions.unsqueeze(1)


class MiniMaxH3Int8Embedding(nn.Module):
    """Scaled INT8 embedding used by Comfy MiniMax H3 checkpoints.

    Checkpoints may store one tensorwise scale or one scale per vocabulary
    row. The checkpoint adapter normalizes either representation to row-
    addressable storage. Looking up INT8 rows before dequantizing avoids
    materializing the full floating-point table.
    """

    def __init__(
        self,
        num_embeddings: int,
        embedding_dim: int,
        padding_idx: int | None,
        output_dtype: torch.dtype,
    ):
        super().__init__()
        self.num_embeddings = num_embeddings
        self.embedding_dim = embedding_dim
        self.padding_idx = padding_idx
        self.output_dtype = output_dtype
        # MMGP normally requires every unquantized parameter in a model to
        # share its execution dtype.  This module deliberately keeps mixed
        # INT8 weights and FP32 row scales while producing BF16/FP16 output.
        # Locking the storage dtype prevents profiling and later dtype-change
        # passes from converting either checkpoint tensor.
        self._lock_dtype = output_dtype
        self.weight = nn.Parameter(
            torch.empty((num_embeddings, embedding_dim), dtype=torch.int8),
            requires_grad=False,
        )
        self.weight_scale = nn.Parameter(
            torch.empty((num_embeddings, 1), dtype=torch.float32),
            requires_grad=False,
        )

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        quantized_rows = F.embedding(input_ids, self.weight, self.padding_idx)
        row_scales = F.embedding(input_ids, self.weight_scale, self.padding_idx)
        return quantized_rows.to(self.output_dtype) * row_scales.to(self.output_dtype)


class MiniMaxH3PreScaledLinear(nn.Linear):
    """AWQ/NVFP4 linear with the checkpoint's input smoothing scale."""

    def __init__(self, in_features: int, out_features: int, bias: bool, dtype: torch.dtype):
        super().__init__(in_features, out_features, bias=bias, dtype=dtype)
        self.register_buffer("pre_quant_scale", torch.empty(in_features, dtype=dtype), persistent=True)

    def forward(self, input: torch.Tensor) -> torch.Tensor:
        scale = self.pre_quant_scale.to(device=input.device, dtype=input.dtype)
        return F.linear(input * scale, self.weight, self.bias)


class MiniMaxH3Qwen3VL(nn.Module):
    """Checkpoint-shaped Qwen3-VL wrapper.

    The consumer checkpoint uses the top-level prefixes ``model`` and
    ``visual`` and ends after decoder layer 50.  H3 consumes that layer's
    unnormalized output, so the absent final norm is intentionally replaced by
    an identity module.
    """

    def __init__(self, config: Qwen3VLConfig, dtype: torch.dtype | None = None):
        super().__init__()
        self.config = config
        self.visual = Qwen3VLVisionModel._from_config(config.vision_config)
        self.model = Qwen3VLTextModel(config.text_config)
        source_embedding = self.model.embed_tokens
        self.model.embed_tokens = MiniMaxH3Int8Embedding(
            source_embedding.num_embeddings,
            source_embedding.embedding_dim,
            source_embedding.padding_idx,
            output_dtype=dtype or source_embedding.weight.dtype,
        )
        self.model.norm = nn.Identity()
        for layer in self.model.layers:
            down = layer.mlp.down_proj
            layer.mlp.down_proj = MiniMaxH3PreScaledLinear(
                down.in_features,
                down.out_features,
                down.bias is not None,
                down.weight.dtype,
            )
            out = layer.self_attn.o_proj
            layer.self_attn.o_proj = MiniMaxH3PreScaledLinear(
                out.in_features,
                out.out_features,
                out.bias is not None,
                out.weight.dtype,
            )

    get_rope_index = Qwen3VLModel.get_rope_index


def load_h3_qwen_config(config_path: str) -> Qwen3VLConfig:
    register_qwen3_vl_config()
    config = Qwen3VLConfig.from_json_file(config_path)
    config.text_config.num_hidden_layers = TEXT_ENCODER_LAYERS
    return config


def build_h3_processor(config_dir: str):
    tokenizer = AutoTokenizer.from_pretrained(config_dir, trust_remote_code=False)
    image_processor = Qwen2VLImageProcessorFast.from_pretrained(config_dir)
    return tokenizer, Krea2Qwen3VLProcessor(image_processor, tokenizer)


def _tag_vision_spans(input_ids: torch.Tensor) -> torch.Tensor:
    """Return H3 modality tags, including the vision boundary tokens."""

    ids = input_ids[0].tolist()
    tags = torch.ones(len(ids), dtype=torch.long)
    start = None
    for index, token in enumerate(ids):
        if token == VISION_START_TOKEN_ID:
            start = index
        if token == VISION_END_TOKEN_ID and start is not None:
            tags[start : index + 1] = 0
            start = None
    if start is not None:
        tags[start:] = 0
    return tags


class MiniMaxH3Conditioner(nn.Module):
    def __init__(self, qwen: MiniMaxH3Qwen3VL, tokenizer, processor, max_text_tokens: int = 512):
        super().__init__()
        self.qwen = qwen
        self.tokenizer = tokenizer
        self.processor = processor
        self.max_text_tokens = max_text_tokens
        self._interrupt = False

    @property
    def language_model(self):
        return self.qwen.model

    @property
    def visual(self):
        return self.qwen.visual

    def _plain_inputs(self, prompt: str, device: torch.device):
        encoded = self.tokenizer(
            prompt,
            add_special_tokens=False,
            truncation=True,
            max_length=self.max_text_tokens,
            return_tensors="pt",
        )
        input_ids = encoded["input_ids"].to(device)
        # Match the MiniMax/Diffusers presentation exactly: there is no chat
        # template or padding, but Qwen still receives the all-live tokenizer
        # mask and applies its native causal attention internally.
        attention_mask = encoded["attention_mask"].to(device=device, dtype=torch.bool)
        return input_ids, attention_mask, None, encoded

    def _vision_inputs(self, prompt: str, images: list, device: torch.device):
        presentation = "".join(
            f"<Picture {index + 1}>: <|vision_start|><|image_pad|><|vision_end|>"
            for index in range(len(images))
        ) + prompt
        encoded = self.processor(
            text=[presentation],
            images=images,
            add_special_tokens=False,
            padding=False,
            truncation=True,
            max_length=self.max_text_tokens + 4096,
            return_tensors="pt",
        ).to(device)
        input_ids = encoded["input_ids"]
        attention_mask = encoded["attention_mask"].bool()
        return input_ids, attention_mask, None, encoded

    def _presentation_entries(self, prompt: str, presentation: list[dict]) -> list:
        entries: list = []
        counters = {"image": 0, "audio": 0, "video": 0}

        def add_text(text: str) -> None:
            entries.extend(self.tokenizer(text, add_special_tokens=False)["input_ids"])

        def add_visual(frames: torch.Tensor, *, video: bool = False) -> None:
            entries.extend((VISION_START_TOKEN_ID, {"frames": frames, "video": video}, VISION_END_TOKEN_ID))

        for item in presentation:
            kind = item["type"]
            if kind not in counters:
                raise ValueError(f"Unsupported MiniMax H3 presentation item {kind!r}.")
            counters[kind] += 1
            if kind == "image":
                add_text(f"<Picture {counters[kind]}>: ")
                add_visual(item["frames"])
            elif kind == "audio":
                add_text(f"<Audio {counters[kind]}>: ")
            else:
                add_text(f"<Video {counters[kind]}>: ")
                frames = item["frames"]
                timestamps = list(
                    item.get("timestamps", [index / 2.0 for index in range(frames.shape[0])])
                )
                if frames.shape[0] % 2:
                    frames = torch.cat((frames, frames[-1:]), dim=0)
                    timestamps.append(timestamps[-1])
                for index in range(0, frames.shape[0], 2):
                    add_text(f"<{(timestamps[index] + timestamps[index + 1]) / 2.0:.1f} seconds>")
                    add_visual(frames[index : index + 2], video=True)
        add_text(prompt)
        return entries or [TEXT_PAD_TOKEN_ID]

    def _presentation_inputs(self, prompt: str, presentation: list[dict], device: torch.device):
        entries = self._presentation_entries(prompt, presentation)
        token_ids: list[int] = []
        visual_specs: list[dict] = []
        for entry in entries:
            if isinstance(entry, int):
                token_ids.append(entry)
            else:
                visual_specs.append(
                    {
                        "placeholder": len(token_ids),
                        "frames": entry["frames"],
                        "video": entry["video"],
                    }
                )
                token_ids.append(VIDEO_TOKEN_ID if entry["video"] else IMAGE_TOKEN_ID)

        encoded_visuals: list[dict] = []
        for spec in visual_specs:
            if self._interrupt:
                return None
            frames = spec["frames"].to(device=device, dtype=torch.float32)
            flattened, grid = _visual_patches(frames, video=spec["video"])
            merged, deepstack = self.qwen.visual(flattened, grid_thw=grid)
            if merged is None or self._interrupt:
                return None
            encoded_visuals.append(
                {
                    "placeholder": spec["placeholder"],
                    "merged": merged,
                    "deepstack": deepstack,
                    "grid": grid,
                    "video": spec["video"],
                }
            )

        expanded_ids: list[int] = []
        visuals: list[dict] = []
        visual_by_placeholder = {item["placeholder"]: item for item in encoded_visuals}
        for index, token_id in enumerate(token_ids):
            visual = visual_by_placeholder.get(index)
            if visual is None:
                expanded_ids.append(token_id)
                continue
            start = len(expanded_ids)
            size = visual["merged"].shape[0]
            expanded_ids.extend(
                [VIDEO_TOKEN_ID if visual["video"] else IMAGE_TOKEN_ID] * size
            )
            visual["index"] = start
            visual["size"] = size
            visuals.append(visual)

        input_ids = torch.tensor(expanded_ids, dtype=torch.long, device=device).unsqueeze(0)
        inputs_embeds = self.qwen.model.embed_tokens(input_ids)
        visual_mask = torch.zeros((1, inputs_embeds.shape[1]), dtype=torch.bool, device=device)
        tags = torch.ones(inputs_embeds.shape[1], dtype=torch.long, device=device)
        deepstack = None
        for visual in visuals:
            start, end = visual["index"], visual["index"] + visual["size"]
            inputs_embeds[0, start:end] = visual["merged"].to(dtype=inputs_embeds.dtype)
            visual_mask[0, start:end] = True
            tags[max(0, start - 1) : min(tags.shape[0], end + 1)] = 0
            if deepstack is None:
                deepstack = visual["deepstack"]
            else:
                deepstack = [
                    torch.cat((deepstack[index], visual["deepstack"][index]), dim=0)
                    for index in range(len(deepstack))
                ]
        position_ids = _multimodal_rope_positions(visuals, inputs_embeds.shape[1], device)
        attention_mask = torch.ones_like(input_ids, dtype=torch.bool)
        return input_ids, attention_mask, position_ids, inputs_embeds, visual_mask, deepstack, tags

    @torch.inference_mode()
    def forward(
        self,
        prompt: str,
        device: torch.device,
        images: list | None = None,
        presentation: list[dict] | None = None,
    ):
        self.qwen.model._interrupt = self._interrupt
        self.qwen.visual._interrupt = self._interrupt
        if self._interrupt:
            return None, None
        tags = None
        if presentation:
            prepared = self._presentation_inputs(prompt, presentation, device)
            if prepared is None:
                return None, None
            (
                input_ids,
                attention_mask,
                position_ids,
                inputs_embeds,
                visual_mask,
                deepstack,
                tags,
            ) = prepared
        elif images:
            input_ids, attention_mask, position_ids, processor_inputs = self._vision_inputs(prompt, images, device)
            grid = processor_inputs["image_grid_thw"]
            pixels = processor_inputs["pixel_values"].to(device=device, dtype=torch.float32)
            image_embeds, deepstack = self.qwen.visual(pixels, grid_thw=grid)
            if image_embeds is None or self._interrupt:
                return None, None
            inputs_embeds = self.qwen.model.embed_tokens(input_ids)
            visual_mask = input_ids == IMAGE_TOKEN_ID
            inputs_embeds = inputs_embeds.masked_scatter(
                visual_mask.unsqueeze(-1).expand_as(inputs_embeds),
                image_embeds.to(inputs_embeds.dtype),
            )
            position_ids, _ = self.qwen.get_rope_index(
                input_ids,
                image_grid_thw=grid,
                attention_mask=attention_mask,
            )
        else:
            input_ids, attention_mask, position_ids, _ = self._plain_inputs(prompt, device)
            inputs_embeds = visual_mask = deepstack = None

        outputs = self.qwen.model(
            input_ids=input_ids if inputs_embeds is None else None,
            inputs_embeds=inputs_embeds,
            attention_mask=attention_mask,
            position_ids=position_ids,
            use_cache=False,
            visual_pos_masks=visual_mask,
            deepstack_visual_embeds=deepstack,
            return_mid_results_layers=[TEXT_ENCODER_LAYERS - 1],
        )
        if outputs.last_hidden_state is None or not outputs.mid_results:
            return None, None
        # The layer snapshot is taken before the (absent) final norm.
        embeddings = outputs.mid_results[0]
        if tags is None:
            tags = _tag_vision_spans(input_ids)
        return embeddings, tags
