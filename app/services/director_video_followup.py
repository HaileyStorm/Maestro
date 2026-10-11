"""Retain authored image context while planning matching video clips."""
from copy import deepcopy
import json


def _validated_prompts(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 96:
        raise ValueError("existing_image_prompts requires one to 96 entries")
    size = 0
    for prompt in value:
        if not isinstance(prompt, str) or len(prompt) > 16384:
            raise ValueError("existing image prompts must be strings of at most 16384 characters")
        try:
            size += len(prompt.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ValueError("existing image prompts must be valid UTF-8 text") from error
        if size > 1024 * 1024:
            raise ValueError("existing image prompt context exceeds one MiB")
    return list(value)


def validate_existing_image_prompts(body):
    """Validate the optional v2 field without interpreting creative subject matter."""
    if not isinstance(body, dict):
        raise ValueError("Director request must be an object")
    if "existing_image_prompts" not in body:
        return None
    skill = body.get("skill_type", body.get("pipeline_type", "music_video"))
    if skill in ("short_film_audio", "short_film_story"):
        skill = "short_film"
    if skill not in ("music_video", "short_film") or body.get("prompt_type") != "video":
        raise ValueError("existing image context is only supported for music video or short film video follow-up")
    prompts = _validated_prompts(body["existing_image_prompts"])
    clips = body.get("clips")
    if not isinstance(clips, list) or not clips or len(clips) != len(prompts):
        raise ValueError("existing image prompts must match the nonempty clip list exactly")
    return prompts


def with_existing_image_prompt_context(generate, existing_image_prompts):
    """Bind frozen ordered context to either model callable; preserve its result."""
    if existing_image_prompts is None:
        return generate
    prompts = _validated_prompts(existing_image_prompts)
    if generate is None:
        return None
    context = (
        "EXISTING IMAGE CONTEXT FOR VIDEO-ONLY FOLLOW-UP:\n"
        "The retained image prompts below are in exact clip order. Use each as the visual context for its matching "
        "video clip. Preserve the authored artistic medium, shot intent, character identities, wardrobe, and any "
        "frozen custom visual style borrow guidance. Do not replace these image prompts or invent additional clips.\n"
        + json.dumps([{"clip_number": index + 1, "image_prompt": prompt}
                      for index, prompt in enumerate(prompts)], ensure_ascii=False)
    )

    def bound_generate(**kwargs):
        system = kwargs.get("system_prompt")
        if system is not None and not isinstance(system, str):
            raise ValueError("model system prompt must be text")
        kwargs["system_prompt"] = f"{system or ''}\n\n{context}"
        return generate(**kwargs)

    return bound_generate


def preserve_existing_image_prompts(clip_plans, existing_image_prompts):
    """Keep original image prompts only after the full output count is proven."""
    if existing_image_prompts is None:
        return clip_plans
    prompts = _validated_prompts(existing_image_prompts)
    if (not isinstance(clip_plans, list) or len(clip_plans) != len(prompts)
            or any(not isinstance(clip, dict) for clip in clip_plans)):
        raise ValueError("video follow-up must produce exactly one plan per retained image prompt")
    retained = deepcopy(clip_plans)
    for clip, prompt in zip(retained, prompts):
        clip["image_prompt"] = prompt
    return retained
