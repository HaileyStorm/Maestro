"""Shared, non-mutating inputs for the legacy multi-clip manifest grammar."""
from __future__ import annotations


def multiclip_prompt_inputs(params: dict, *, separator: str) -> tuple[list[str], list, list]:
    prompt_text = params.get("prompt", "")
    planned_prompts = params.get("per_clip_prompts")
    if isinstance(planned_prompts, list) and planned_prompts:
        prompt_lines = [str(prompt) for prompt in planned_prompts]
    elif separator in prompt_text:
        prompt_lines = [part.strip() for part in prompt_text.split(separator) if part.strip()]
    else:
        prompt_lines = [line.strip() for line in prompt_text.split("\n") if line.strip()]
    image_starts = params.get("image_start", [])
    if not isinstance(image_starts, list):
        image_starts = [image_starts] if image_starts else []
    image_ends = params.get("image_end", [])
    if not isinstance(image_ends, list):
        image_ends = [image_ends] if image_ends else []
    return prompt_lines, image_starts, image_ends
