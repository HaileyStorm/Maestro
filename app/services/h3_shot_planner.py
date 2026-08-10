"""Deterministic shared MiniMax H3 native-shot reconciliation.

Studio and Director author one global narrative, then this module maps that
source onto already model-aligned native segments.  It performs no LLM calls:
recovery can therefore persist and replay the exact resulting contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any


H3_SHOT_PLAN_VERSION = 1
H3_SEMANTIC_PHYSICAL_CONTRACT_VERSION = 1
H3_CONTINUITY_MODES = frozenset({
    "independent", "continuous", "extend_previous",
})
H3_SEGMENT_POLICY_VERSION = 1
_H3_PROFILE_PREFERRED_FRAMES = {
    "draft": 192,
    "fast": 243,
}


class H3ShotPlanError(ValueError):
    """Raised when authored shot semantics cannot be reconciled safely."""


_DIALOGUE_RE = re.compile(r"<d>\s*\[[^\]\r\n]+\]\s+.*?</d>", re.I | re.S)
_DIALOGUE_TOKEN_RE = re.compile(r"<\s*(/?)\s*d\s*>", re.I)
_FINAL_BLOCKING_RE = re.compile(
    r"(?<!\S)FINAL\s+BLOCKING\s*:\s*(.+?)"
    r"(?=(?:\s+\[\s*(?:SHOT|SCENE)\b)|\n\s*[A-Z][A-Z _-]+\s*:|\Z)",
    re.I | re.S,
)
_VISUAL_HEADER_RE = re.compile(
    r"^(?:PROJECT\s+CONTINUITY|VISUAL\s+WORLD|SUBJECT_DEFINITIONS|CAST|"
    r"SETTING|LOCATION|ENVIRONMENT|VISUAL\s+STYLE|LIGHTING)\s*:\s*(.*)$",
    re.I,
)
_NONVISUAL_HEADER_RE = re.compile(r"^[A-Z][A-Z _-]+\s*:")


def _field(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _compact(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{shortened}..." if shortened else text[:limit]


def _dialogue_spans_are_balanced(text: str) -> bool:
    depth = 0
    for token in _DIALOGUE_TOKEN_RE.finditer(text):
        if token.group(1):
            depth -= 1
            if depth < 0:
                return False
        else:
            depth += 1
            if depth > 1:
                return False
    return depth == 0


def _canonical_dialogue(value: Any, language: Any = "English") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if _DIALOGUE_TOKEN_RE.search(raw):
        tokens = list(_DIALOGUE_TOKEN_RE.finditer(raw))
        if (
            not _dialogue_spans_are_balanced(raw)
            or not _DIALOGUE_RE.fullmatch(raw)
            or len(tokens) != 2
            or bool(tokens[0].group(1))
            or not bool(tokens[1].group(1))
        ):
            raise H3ShotPlanError(
                "Structured MiniMax H3 dialogue tags must be one balanced block"
            )
        return raw
    matched = _DIALOGUE_RE.fullmatch(raw)
    if matched:
        return raw
    raw = _DIALOGUE_TOKEN_RE.sub("", raw).strip()
    language_match = re.match(r"^\[([^\]]+)\]\s*(.*)$", raw, re.S)
    if language_match:
        language = language_match.group(1).strip() or language
        raw = language_match.group(2).strip()
    if not raw:
        return ""
    return f"<d>[{str(language or 'English').strip()}] {raw}</d>"


def _extract_explicit_visual_context(prompt: str) -> tuple[str, str]:
    """Remove only explicitly labelled visual context from untimed prose."""

    kept: list[str] = []
    context: list[str] = []
    collecting = False
    for raw_line in str(prompt or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = _VISUAL_HEADER_RE.match(line)
        if header:
            context.append(line)
            label = line.split(":", 1)[0].strip().casefold().replace(" ", "_")
            collecting = (
                label in {"subject_definitions", "cast"}
                and not header.group(1).strip()
            )
            continue
        if collecting and _NONVISUAL_HEADER_RE.match(line):
            collecting = False
        if collecting:
            # Only declaration-shaped lines belong to the labelled cast
            # section. Ordinary prose is authored action and must remain local.
            declaration = re.match(
                r"^(?:<[^>]{1,80}>|[A-Za-z][\w .'-]{0,79})\s*:\s*\S+",
                line,
            )
            if not declaration:
                collecting = False
            else:
                context.append(line)
                continue
        kept.append(line)
    return "\n".join(kept).strip(), "\n".join(context).strip()


def build_h3_visual_context(shot: Any | None) -> str:
    """Build visual-only repeatable context from structured shot fields.

    Plot goals, action beats, dialogue, and ending beats are intentionally
    excluded.  This is stricter than repeating a project synopsis, which can
    leak future events into an earlier native segment.
    """

    if shot is None:
        return ""
    details: list[str] = []
    environment = _compact(_field(shot, "environment"), 320)
    style = _compact(_field(shot, "visual_style"), 220)
    lighting = _compact(_field(shot, "lighting"), 180)
    if environment:
        details.append(f"setting: {environment}")
    if style:
        details.append(f"visual style: {style}")
    if lighting:
        details.append(f"lighting: {lighting}")

    cast: list[str] = []
    for subject in _field(shot, "subjects_on_screen", []) or []:
        name = _compact(_field(subject, "speaker_name"), 80)
        description = _compact(_field(subject, "visual_description"), 180)
        wardrobe = _compact(_field(subject, "wardrobe"), 180)
        parts = [part for part in (name, description) if part]
        if wardrobe:
            parts.append(f"wardrobe: {wardrobe}")
        label = "; ".join(parts)
        if label and label not in cast:
            cast.append(label)
    if cast:
        details.append("visible cast: " + "; ".join(cast))
    if not details:
        return ""
    return (
        "VISUAL CONTINUITY (world, cast, and setting only): "
        + ". ".join(details)
        + "."
    )


def _protect_dialogue(text: str) -> tuple[str, list[tuple[str, str]]]:
    blocks: list[tuple[str, str]] = []
    salt = 0
    prefix = "__H3_DIALOGUE_SLOT_0_"
    while prefix in text:
        salt += 1
        prefix = f"__H3_DIALOGUE_SLOT_{salt}_"

    def replace(match: re.Match[str]) -> str:
        token = f"{prefix}{len(blocks)}__"
        blocks.append((token, match.group(0)))
        return token

    return _DIALOGUE_RE.sub(replace, text), blocks


def _restore_dialogue(text: str, blocks: Sequence[tuple[str, str]]) -> str:
    result = re.sub(r"[ \t]+", " ", text).strip()
    for token, block in blocks:
        result = result.replace(token, block)
    return result


def _restore_dialogue_exact(
    text: str, blocks: Sequence[tuple[str, str]],
) -> str:
    result = text
    for token, block in blocks:
        result = result.replace(token, block)
    return result


def _extract_final_blocking(prompt: str) -> tuple[str, str]:
    """Extract the final-blocking field without interpreting dialogue text."""

    protected, blocks = _protect_dialogue(prompt)
    matches = list(_FINAL_BLOCKING_RE.finditer(protected))
    if not matches:
        return prompt, ""
    blocking = _compact(
        _restore_dialogue_exact(matches[-1].group(1), blocks), 1200,
    )
    without = _restore_dialogue_exact(
        _FINAL_BLOCKING_RE.sub("\n", protected), blocks,
    ).strip()
    return without, blocking


def _untimed_units(prompt: str, required: int) -> tuple[list[str], str, str]:
    if not _dialogue_spans_are_balanced(prompt):
        raise H3ShotPlanError("MiniMax H3 dialogue tags must be balanced before planning")
    without_blocking, final_blocking = _extract_final_blocking(prompt)
    protected_source, source_dialogue = _protect_dialogue(without_blocking)
    action, context = _extract_explicit_visual_context(protected_source)
    action = _restore_dialogue_exact(action, source_dialogue)
    context = _restore_dialogue_exact(context, source_dialogue)
    protected, blocks = _protect_dialogue(action)
    units = [
        item.strip(" \t\n")
        for item in re.split(r"(?<=[.!?])\s+|\n+", protected)
        if item.strip(" \t\n")
    ]
    # Give a single long authored sentence enough chronological boundaries
    # without synthesizing or repeating content. Dialogue placeholders remain
    # indivisible because none of these separators occur inside a placeholder.
    while len(units) < required:
        split_index = next((
            index for index, unit in enumerate(units)
            if re.search(
                r"(?:[,;]\s+|\s+)(?:then|next|after(?:ward| that)?|finally)\b",
                unit,
                re.I,
            )
        ), None)
        if split_index is None:
            break
        pieces = re.split(
            r"(?<=[,;])\s+(?=(?:then|next|after(?:ward| that)?|finally)\b)",
            units[split_index],
            maxsplit=1,
            flags=re.I,
        )
        if len(pieces) != 2:
            break
        units[split_index:split_index + 1] = pieces
    return [_restore_dialogue(unit, blocks) for unit in units], context, final_blocking


def infer_h3_profile_id(params: Mapping[str, Any] | None) -> str:
    """Infer the selected curated profile from persisted generation values."""

    params = params or {}
    custom = params.get("custom_settings")
    custom = custom if isinstance(custom, Mapping) else {}
    explicit = str(
        params.get("h3_performance_profile")
        or params.get("performance_profile")
        or params.get("profile_id")
        or custom.get("h3_performance_profile")
        or ""
    ).strip().casefold()
    if explicit:
        return explicit
    try:
        steps = int(params.get("num_inference_steps") or 20)
    except (TypeError, ValueError):
        steps = 20
    resolution = str(params.get("resolution") or "").strip().casefold()
    turbo = str(custom.get("h3_turbo_profile") or "") == "h3_turbo_v4"
    if turbo and steps == 4 and resolution == "608x352":
        return "draft"
    if turbo and steps == 8 and resolution == "864x480":
        return "fast"
    return "high"


def floor_h3_frame_count(
    value: int,
    *,
    minimum_frames: int,
    maximum_frames: int,
    align_frame_count,
) -> int:
    """Return the greatest legal frame count that does not exceed ``value``."""

    requested = int(value)
    minimum = int(minimum_frames)
    maximum = int(maximum_frames)
    if requested < minimum:
        raise H3ShotPlanError("H3 segment ceiling is below the model minimum")
    requested = min(requested, maximum)
    candidate = requested
    while candidate >= minimum:
        aligned = int(align_frame_count(candidate))
        if minimum <= aligned <= requested:
            return aligned
        candidate -= 1
    raise H3ShotPlanError("H3 segment ceiling has no legal model-grid value")


def plan_h3_clip_frames(
    total_frames: int,
    *,
    prompt: str,
    fps: float,
    minimum_frames: int,
    maximum_frames: int,
    align_frame_count,
    profile_id: str = "high",
    manual_segment_ceiling: bool = False,
    published_total_frames: int | None = None,
) -> tuple[list[int], dict[str, Any]]:
    """Plan legal frames with an untimed-only profile latency preference.

    Draft/Fast prefer more, shorter automatic segments to reduce time to the
    first completed output. This is not a total-runtime speed claim and does
    not reduce the model's legal ceiling. Extra seams and repeated audio
    exposure limit the pressure: timestamps, manual ceilings, and too few
    indivisible authored action/dialogue units retain the ordinary plan.
    """

    from shared.utils.prompt_parser import (
        has_global_timeline,
        parse_global_timeline_prompt,
        plan_consecutive_clip_frames,
        plan_transition_aware_clip_frames,
    )

    normalized_profile = str(profile_id or "high").strip().casefold()
    preferred = _H3_PROFILE_PREFERRED_FRAMES.get(normalized_profile)
    policy = {
        "version": H3_SEGMENT_POLICY_VERSION,
        "id": "native_default",
        "profile_id": normalized_profile,
        "preferred_frames": preferred,
        "applied": False,
        "reason": "native maximum retained",
    }
    ordinary = plan_transition_aware_clip_frames(
        total_frames,
        prompt=prompt,
        fps=fps,
        minimum_frames=minimum_frames,
        maximum_frames=maximum_frames,
        align_frame_count=align_frame_count,
    )

    def density_weighted(plan: list[int]) -> list[int]:
        """Keep semantic units whole while allowing unequal automatic clips."""

        count = len(plan)
        visible_total = int(
            published_total_frames
            if published_total_frames is not None else total_frames
        )
        units, _, _ = _untimed_units(str(prompt or ""), count)
        if count < 2 or len(units) < count:
            return plan
        group_sizes = [len(units) // count] * count
        for index in range(len(units) % count):
            group_sizes[index] += 1
        weights = []
        cursor = 0
        for size in group_sizes:
            group = units[cursor:cursor + size]
            cursor += size
            weights.append(max(1, sum(
                len(re.findall(r"\b\w+\b", unit)) for unit in group
            )))
        available = visible_total - count * int(minimum_frames)
        if available < 0:
            return plan
        weight_total = sum(weights)
        raw_extras = [available * weight / weight_total for weight in weights]
        extras = [int(value) for value in raw_extras]
        remainder = available - sum(extras)
        order = sorted(
            range(count),
            key=lambda index: (raw_extras[index] - extras[index], -index),
            reverse=True,
        )
        for index in order[:remainder]:
            extras[index] += 1
        requested = [
            int(minimum_frames) + extra for extra in extras
        ]
        if any(value > int(maximum_frames) for value in requested):
            return plan
        generation_requests = list(requested)
        generation_requests[-1] += max(0, int(total_frames) - visible_total)
        generated = [int(align_frame_count(value)) for value in generation_requests]
        if any(
            generated_value < requested_value
            or generated_value > int(maximum_frames)
            for generated_value, requested_value in zip(generated, requested)
        ):
            return plan
        policy["clip_requested_frames"] = requested
        policy["density_weighted"] = len(set(requested)) > 1
        return generated

    if has_global_timeline(prompt):
        policy["reason"] = "authored timestamps are authoritative"
        visible_total = int(
            published_total_frames
            if published_total_frames is not None else total_frames
        )
        _, events = parse_global_timeline_prompt(prompt)
        authored_boundaries = sorted({
            round(float(event.get("start") or 0) * float(fps))
            for event in events
            if event.get("kind") in {"shot", "range"}
            and 0 < round(float(event.get("start") or 0) * float(fps)) < visible_total
        })
        requested_sections = [
            end - start
            for start, end in zip(
                [0, *authored_boundaries],
                [*authored_boundaries, visible_total],
            )
        ]
        if authored_boundaries and all(
            section >= int(minimum_frames) for section in requested_sections
        ):
            generated_sections = list(requested_sections)
            generated_sections[-1] += max(0, int(total_frames) - visible_total)
            exact_frames: list[int] = []
            exact_published: list[int] = []
            boundary_after: list[int] = []
            for section_index, (generation_frames, visible_frames) in enumerate(
                zip(generated_sections, requested_sections)
            ):
                section_plan = plan_consecutive_clip_frames(
                    generation_frames,
                    minimum_frames=minimum_frames,
                    maximum_frames=maximum_frames,
                    align_frame_count=align_frame_count,
                )
                section_published = list(section_plan)
                section_published[-1] -= sum(section_plan) - visible_frames
                if section_published[-1] <= 0:
                    exact_frames = []
                    break
                exact_frames.extend(section_plan)
                exact_published.extend(section_published)
                if section_index < len(requested_sections) - 1:
                    boundary_after.append(len(exact_frames) - 1)
            if exact_frames:
                policy.update({
                    "id": "authored_timeline_exact_v1",
                    "clip_requested_frames": exact_published,
                    "authored_boundary_after_clip_indices": boundary_after,
                })
                return exact_frames, policy
        return ordinary, policy
    if preferred is None:
        return density_weighted(ordinary), policy
    if manual_segment_ceiling:
        policy["reason"] = "manual segment ceiling is authoritative"
        return density_weighted(ordinary), policy

    target_count = max(1, (int(total_frames) + preferred - 1) // preferred)
    units, _, _ = _untimed_units(str(prompt or ""), target_count)
    if len(units) < target_count:
        policy["reason"] = "insufficient indivisible authored beats for extra seams"
        return density_weighted(ordinary), policy
    preferred_maximum = min(int(maximum_frames), int(preferred))
    if preferred_maximum < int(minimum_frames):
        policy["reason"] = "preferred target is below the model minimum"
        return density_weighted(ordinary), policy
    preferred_plan = plan_consecutive_clip_frames(
        total_frames,
        minimum_frames=minimum_frames,
        maximum_frames=preferred_maximum,
        align_frame_count=align_frame_count,
    )
    if len(preferred_plan) <= len(ordinary):
        policy["reason"] = "ordinary plan already meets latency preference"
        return density_weighted(ordinary), policy
    policy.update({
        "id": f"{normalized_profile}_shorter_automatic_v1",
        "applied": True,
        "reason": "lower time to first completed segment",
        "ordinary_clip_count": len(ordinary),
        "preferred_clip_count": len(preferred_plan),
    })
    return density_weighted(preferred_plan), policy


def estimate_h3_segment_count(
    total_frames: int,
    *,
    prompt: str,
    fps: float,
    minimum_frames: int,
    maximum_frames: int,
    align_frame_count,
    profile_id: str = "high",
    manual_segment_ceiling: bool = False,
    published_total_frames: int | None = None,
) -> dict[str, Any]:
    """Summarize deterministic H3 geometry without inventing a second plan.

    A present prompt is enough to run the exact shared planner, including its
    authored-timestamp and prompt-beat behavior.  With no prompt there is no
    honest way to know how many later authored beats will justify shorter
    shots, so report the ordinary plan as ``likely`` and the full legal range
    permitted by the model minimum.  The range is derived from model geometry,
    never from a multiplier applied to the likely count.
    """

    planned, policy = plan_h3_clip_frames(
        total_frames,
        prompt=prompt,
        fps=fps,
        minimum_frames=minimum_frames,
        maximum_frames=maximum_frames,
        align_frame_count=align_frame_count,
        profile_id=profile_id,
        manual_segment_ceiling=manual_segment_ceiling,
        published_total_frames=published_total_frames,
    )
    likely = len(planned)
    if str(prompt or "").strip():
        source = (
            "deterministic_authored_timeline"
            if policy.get("reason") == "authored timestamps are authoritative"
            else "deterministic_prompt_beats"
        )
        return {
            "minimum": likely,
            "maximum": likely,
            "likely": likely,
            "source": source,
            "confidence": "high",
            "reason": str(policy.get("reason") or "shared deterministic H3 plan"),
        }

    visible_frames = max(
        1,
        int(
            published_total_frames
            if published_total_frames is not None else total_frames
        ),
    )
    legal_maximum = max(likely, visible_frames // max(1, int(minimum_frames)))
    return {
        "minimum": likely,
        "maximum": legal_maximum,
        "likely": likely,
        "source": "duration_profile_model_grid",
        "confidence": "low",
        "reason": (
            "No prompt beats or timestamps are available yet; the range spans "
            "legal model-grid plans under the selected profile and segment ceiling."
        ),
    }


def _normalize_continuity(value: Any, *, fallback: str) -> str:
    normalized = str(value or fallback).strip().casefold()
    aliases = {
        "cut": "independent",
        "transition": "independent",
        "precut": "extend_previous",
        "same_shot": "continuous",
        "continue": "continuous",
        "extend": "extend_previous",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in H3_CONTINUITY_MODES:
        raise H3ShotPlanError(f"Unknown H3 continuity mode: {normalized}")
    return normalized


def _boundary_mode(boundary: Mapping[str, Any] | None) -> str:
    boundary = boundary or {}
    explicit = boundary.get("continuity_mode") or boundary.get("mode")
    if explicit:
        return _normalize_continuity(explicit, fallback="extend_previous")
    boundary_type = str(boundary.get("type") or "continuous").casefold()
    if boundary_type in {"cut", "transition"}:
        return "independent"
    if boundary_type == "precut":
        return "extend_previous"
    if str(boundary.get("source") or "") in {
        "explicit_continuity", "user_override",
    }:
        return "continuous"
    return "extend_previous"


def _semantic_boundary(
    boundary: Mapping[str, Any] | None,
    *,
    continuity_mode: str,
) -> dict[str, Any]:
    result = dict(boundary or {})
    result["continuity_mode"] = continuity_mode
    if continuity_mode == "independent":
        result["type"] = (
            "transition" if result.get("type") == "transition" else "cut"
        )
    elif continuity_mode == "continuous":
        result["type"] = "continuous"
    else:
        # ``extend_previous`` is a semantic prompt/source contract. Native AV
        # overlap uses the ordinary continuous type unless the timestamp
        # classifier identified a real pre-cut lead-in.
        result["type"] = (
            "precut" if result.get("type") == "precut" else "continuous"
        )
    result.setdefault("source", "shared_h3_shot_plan")
    result.setdefault("event", "")
    return result


def _source_dialogue_beats(shot: Any | None) -> list[dict[str, Any]]:
    beats: list[dict[str, Any]] = []
    source_beats = (
        _field(shot, "dialogue_beats", [])
        or _field(shot, "dialogue_manifest", [])
        or []
    )
    for beat in source_beats:
        exact_block = str(_field(beat, "exact_block", "") or "")
        spoken = _field(beat, "spoken_text", "")
        language = _field(beat, "language", "English")
        block = exact_block or _canonical_dialogue(spoken, language)
        if not block:
            continue
        if exact_block and not _DIALOGUE_RE.fullmatch(exact_block):
            raise H3ShotPlanError(
                "Recovered MiniMax H3 dialogue manifest has an invalid exact block"
            )
        beats.append({
            "exact_block": block,
            "spoken_text": _DIALOGUE_TOKEN_RE.sub("", block).strip(),
            "speaker_id": str(_field(beat, "speaker_id", "") or ""),
            "source": str(
                _field(beat, "source", "structured_dialogue")
                or "structured_dialogue"
            ),
        })
    return beats


def _compile_source_dialogue(
    prompts: list[str],
    *,
    shot: Any | None,
    source_index: int,
) -> list[dict[str, Any]]:
    """Bind authored/structured dialogue once without rewriting its words."""

    manifest: list[dict[str, Any]] = []
    structured = _source_dialogue_beats(shot)
    claimed: dict[str, int] = {}

    def replace_untagged_words(text: str, words: str, block: str) -> tuple[str, bool]:
        """Replace one literal occurrence that is outside every ``<d>`` block."""

        cursor = 0
        for match in _DIALOGUE_RE.finditer(text):
            position = text.find(words, cursor, match.start())
            if position >= 0:
                return (
                    text[:position] + block + text[position + len(words):],
                    True,
                )
            cursor = match.end()
        position = text.find(words, cursor)
        if position < 0:
            return text, False
        return text[:position] + block + text[position + len(words):], True

    def append_to_canonical_vocals(text: str, block: str) -> str:
        """Place recovered dialogue inside the final canonical record."""

        lines = text.splitlines()
        pattern = re.compile(
            r"^(?P<prefix>\[Shot\s+\d+\].*\|\s*"
            r"dialogue_and_vocalizations\s*:\s*)(?P<vocals>.*)$",
            re.IGNORECASE,
        )
        for index in range(len(lines) - 1, -1, -1):
            match = pattern.fullmatch(lines[index].strip())
            if not match:
                continue
            vocals = match.group("vocals").strip()
            lines[index] = (
                match.group("prefix")
                + (block if vocals.casefold() in {"", "none", "n/a"}
                   else f"{vocals} {block}")
            )
            return "\n".join(lines)
        return f"{text}\n{block}".strip()

    for beat_index, beat in enumerate(structured):
        block = beat["exact_block"]
        occurrences = [
            index
            for index, prompt in enumerate(prompts)
            for _ in range(prompt.count(block))
        ]
        claim = claimed.get(block, 0)
        target = occurrences[claim] if claim < len(occurrences) else None
        if target is None:
            # If the renderer left the exact words untagged, replace that one
            # occurrence. Otherwise append the structured beat once, in order.
            words = re.sub(r"^\[[^\]]+\]\s*", "", beat["spoken_text"]).strip()
            replaced = False
            if words:
                for candidate, prompt in enumerate(prompts):
                    updated, replaced = replace_untagged_words(prompt, words, block)
                    if replaced:
                        prompts[candidate] = updated
                        target = candidate
                        break
            if not replaced:
                target = min(
                    len(prompts) - 1,
                    beat_index * len(prompts) // max(1, len(structured)),
                )
                prompts[target] = append_to_canonical_vocals(
                    prompts[target], block,
                )
        claimed[block] = claim + 1
        manifest.append({
            **beat,
            "source_index": source_index,
            "local_segment_index": target,
        })

    structured_counts: dict[str, int] = {}
    for item in manifest:
        block = item["exact_block"]
        structured_counts[block] = structured_counts.get(block, 0) + 1
    seen_authored: dict[str, int] = {}
    for local_index, prompt in enumerate(prompts):
        for match in _DIALOGUE_RE.finditer(prompt):
            block = match.group(0)
            seen = seen_authored.get(block, 0)
            seen_authored[block] = seen + 1
            if seen < structured_counts.get(block, 0):
                continue
            manifest.append({
                "exact_block": block,
                "spoken_text": _DIALOGUE_TOKEN_RE.sub("", block).strip(),
                "speaker_id": "",
                "source_index": source_index,
                "source": "authored_prompt",
                "local_segment_index": local_index,
            })
    # Persist the semantic prompt's literal dialogue order. Structured beats
    # keep their speaker/source metadata, but they must not sort ahead of an
    # earlier authored block or the same fresh plan becomes non-resumable.
    remaining = list(manifest)
    ordered: list[dict[str, Any]] = []
    for prompt in prompts:
        for match in _DIALOGUE_RE.finditer(prompt):
            block = match.group(0)
            match_index = next((
                index for index, item in enumerate(remaining)
                if item["exact_block"] == block
            ), None)
            if match_index is not None:
                ordered.append(remaining.pop(match_index))
    return [*ordered, *remaining]


def _authored_shot_id(shot: Any | None, source_index: int) -> str:
    """Return a stable semantic ID without deriving it from mutable prose."""

    for field in ("authored_shot_id", "shot_id", "id", "stable_id"):
        value = str(_field(shot, field, "") or "").strip()
        if value:
            return value
    return f"h3-authored-shot-{source_index + 1}"


def _semantic_reference_labels(prompt: str) -> list[str]:
    """Record literal Context-IR labels without interpreting their meaning."""

    return list(dict.fromkeys(re.findall(
        r"<(?:Subject|Picture|Video|Audio)\s+[1-9]\d*>",
        str(prompt or ""),
        flags=re.IGNORECASE,
    )))


def plan_h3_native_shots(
    *,
    global_prompt: str,
    clip_frame_counts: Sequence[int],
    fps: float,
    clip_boundaries: Sequence[Mapping[str, Any]] | None = None,
    source_prompts: Sequence[str] | None = None,
    source_indices: Sequence[int] | None = None,
    structured_shots: Sequence[Any] | None = None,
    source_requested_frames: Sequence[int] | None = None,
    clip_requested_frames: Sequence[int] | None = None,
    segment_frames_maximum: int | None = None,
    segment_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile semantic H3 shots into persistent native execution segments.

    Frame geometry is supplied by the caller after applying the selected
    profile/model ceiling. A semantic prompt is compiled once per authored
    source and then reused byte-for-byte by every physical segment assigned to
    that source. Segment-local cursors describe which temporal slice is being
    executed; a model-grid split never requests another LLM rewrite.
    """

    counts = [int(value) for value in clip_frame_counts]
    if not counts or any(value <= 0 for value in counts):
        raise H3ShotPlanError("H3 shot frames must be positive")
    try:
        fps_value = float(fps)
    except (TypeError, ValueError) as exc:
        raise H3ShotPlanError("H3 shot-plan FPS must be positive") from exc
    if fps_value <= 0:
        raise H3ShotPlanError("H3 shot-plan FPS must be positive")

    indices = list(source_indices or [0] * len(counts))
    if len(indices) != len(counts) or any(
        isinstance(value, bool) or int(value) < 0 for value in indices
    ):
        raise H3ShotPlanError("H3 source indices must align with native segments")
    indices = [int(value) for value in indices]
    prompts_by_source = list(source_prompts or [str(global_prompt or "")])
    shots = list(structured_shots or [])
    if max(indices) >= len(prompts_by_source):
        raise H3ShotPlanError("H3 source prompt index is out of range")

    clip_trim_tail_frames = [0] * len(counts)
    if clip_requested_frames is not None:
        requested_by_clip = [int(value) for value in clip_requested_frames]
        if len(requested_by_clip) != len(counts) or any(
            requested <= 0 or requested > generated
            for requested, generated in zip(requested_by_clip, counts)
        ):
            raise H3ShotPlanError(
                "H3 requested clip frames must align with generated clips"
            )
        clip_trim_tail_frames = [
            generated - requested
            for generated, requested in zip(counts, requested_by_clip)
        ]
    elif source_requested_frames is not None:
        requested_by_source = [int(value) for value in source_requested_frames]
        if len(requested_by_source) != len(prompts_by_source) or any(
            value <= 0 for value in requested_by_source
        ):
            raise H3ShotPlanError(
                "H3 requested source frames must align with source prompts"
            )
        for source_index in sorted(set(indices)):
            positions = [
                index for index, value in enumerate(indices)
                if value == source_index
            ]
            planned = sum(counts[index] for index in positions)
            trim = planned - requested_by_source[source_index]
            if trim < 0 or trim >= counts[positions[-1]]:
                raise H3ShotPlanError(
                    f"Unable to publish H3 source {source_index + 1} exactly"
                )
            clip_trim_tail_frames[positions[-1]] = trim
    clip_published_frames = [
        frames - trim
        for frames, trim in zip(counts, clip_trim_tail_frames)
    ]

    prompts = [""] * len(counts)
    dialogue_manifest: list[dict[str, Any]] = []
    source_contracts: list[dict[str, Any]] = []
    seen_authored_shot_ids: set[str] = set()
    for source_index in sorted(set(indices)):
        positions = [
            index for index, value in enumerate(indices) if value == source_index
        ]
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise H3ShotPlanError("H3 source segments must remain chronological")
        source = str(prompts_by_source[source_index] or "").strip()
        if not _dialogue_spans_are_balanced(source):
            raise H3ShotPlanError(
                "MiniMax H3 dialogue tags must be balanced before planning"
            )
        local_published = [clip_published_frames[index] for index in positions]
        shot = shots[source_index] if source_index < len(shots) else None
        authored_shot_id = _authored_shot_id(shot, source_index)
        if authored_shot_id in seen_authored_shot_ids:
            raise H3ShotPlanError(
                f"Duplicate authored H3 shot ID: {authored_shot_id}"
            )
        seen_authored_shot_ids.add(authored_shot_id)

        # All deterministic semantic compilation happens once, before native
        # geometry fans the shot out into physical execution segments.
        semantic_prompts = [source]
        visual_context = build_h3_visual_context(shot)
        if visual_context:
            semantic_prompts = [
                prompt if visual_context in prompt
                else f"{visual_context}\n{prompt}".strip()
                for prompt in semantic_prompts
            ]
        opening_blocking = _compact(_field(shot, "spatial_setup", ""), 600)
        if opening_blocking and not re.search(
            r"\bOPENING\s+BLOCKING\s*:", semantic_prompts[0], re.I,
        ):
            semantic_prompts[0] = (
                f"OPENING BLOCKING: {opening_blocking}\n{semantic_prompts[0]}"
            ).strip()
        final_blocking = _compact(
            _field(shot, "closing_blocking", "")
            or _field(shot, "ending_beat", ""),
            1200,
        )
        if final_blocking and not re.search(
            r"\bFINAL\s+BLOCKING\s*:", semantic_prompts[0], re.I,
        ):
            semantic_prompts[0] = (
                f"{semantic_prompts[0]}\nFINAL BLOCKING: {final_blocking}"
            ).strip()
        source_dialogue = _compile_source_dialogue(
            semantic_prompts,
            shot=shot,
            source_index=source_index,
        )
        for item in source_dialogue:
            item.pop("local_segment_index", None)
            item["authored_shot_id"] = authored_shot_id
            item["semantic_shot_index"] = source_index
            # Dialogue belongs to the semantic shot. This anchor is for
            # recovery/order only; repeated physical prompt bytes do not create
            # duplicate authored dialogue records.
            item["segment_index"] = positions[0]
        dialogue_manifest.extend(source_dialogue)
        semantic_prompt = semantic_prompts[0]
        authored_final_blocking = _extract_final_blocking(source)[1]
        for position in positions:
            prompts[position] = semantic_prompt

        execution_slices: list[dict[str, Any]] = []
        local_cursor = 0
        for local_index, (position, published_frames) in enumerate(
            zip(positions, local_published)
        ):
            end_cursor = local_cursor + published_frames
            execution_slices.append({
                "segment_index": position,
                "physical_segment_index": local_index,
                "start_frame": local_cursor,
                "end_frame_exclusive": end_cursor,
                "start_seconds": local_cursor / fps_value,
                "end_seconds": end_cursor / fps_value,
            })
            local_cursor = end_cursor
        source_contracts.append({
            "source_index": source_index,
            "authored_shot_id": authored_shot_id,
            "semantic_shot_index": source_index,
            "segment_indices": positions,
            "semantic_prompt": semantic_prompt,
            "authored_prompt": source,
            "prompt_changed_before_split": semantic_prompt != source,
            "prompt_rewrite_for_physical_split": False,
            "execution_slices": execution_slices,
            "reference_labels": _semantic_reference_labels(semantic_prompt),
            "dialogue_manifest": [dict(item) for item in source_dialogue],
            "visual_context": visual_context,
            "opening_blocking": opening_blocking,
            "final_blocking": final_blocking,
            "authored_final_blocking": authored_final_blocking,
        })

    raw_boundaries = list(clip_boundaries or [])
    if len(raw_boundaries) > len(counts) - 1:
        raise H3ShotPlanError("H3 boundary count exceeds native joins")
    boundaries: list[dict[str, Any]] = []
    cursor = 0
    published_cursor = 0
    native_shots: list[dict[str, Any]] = []
    contracts_by_source = {
        int(contract["source_index"]): contract
        for contract in source_contracts
    }
    local_positions = {
        (int(contract["source_index"]), int(segment_index)): local_index
        for contract in source_contracts
        for local_index, segment_index in enumerate(contract["segment_indices"])
    }
    for index, (frames, prompt, source_index) in enumerate(
        zip(counts, prompts, indices)
    ):
        source_contract = contracts_by_source[source_index]
        authored_shot_id = str(source_contract["authored_shot_id"])
        physical_segment_index = local_positions[(source_index, index)]
        execution_slice = source_contract["execution_slices"][physical_segment_index]
        physical_segment_id = (
            f"{authored_shot_id}:segment-{physical_segment_index + 1}"
        )
        if index == 0:
            continuity = "independent"
            boundary_before = None
        else:
            existing = raw_boundaries[index - 1] if index - 1 < len(raw_boundaries) else {}
            if str(existing.get("source") or "") == "user_override":
                continuity = _boundary_mode(existing)
            elif source_index != indices[index - 1]:
                source_shot = shots[source_index] if source_index < len(shots) else None
                continuity = _normalize_continuity(
                    _field(source_shot, "continuity_strategy", "independent"),
                    fallback="independent",
                )
            else:
                continuity = _boundary_mode(existing)
            boundary_before = _semantic_boundary(
                existing,
                continuity_mode=continuity,
            )
            boundaries.append(boundary_before)
        native_shots.append({
            "index": index,
            "source_index": source_index,
            "authored_shot_id": authored_shot_id,
            "semantic_shot_index": source_index,
            "physical_segment_id": physical_segment_id,
            "physical_segment_index": physical_segment_index,
            "physical_segment_count": len(source_contract["segment_indices"]),
            "predecessor_segment_index": index - 1 if index else None,
            "predecessor_physical_segment_id": (
                native_shots[-1]["physical_segment_id"] if native_shots else None
            ),
            "predecessor_authored_shot_id": (
                native_shots[-1]["authored_shot_id"] if native_shots else None
            ),
            "execution_cursor_frame": execution_slice["start_frame"],
            "execution_slice": dict(execution_slice),
            "frames": frames,
            "start_frame": cursor,
            "end_frame": cursor + frames - 1,
            "published_frames": clip_published_frames[index],
            "published_start_frame": published_cursor,
            "published_end_frame": (
                published_cursor + clip_published_frames[index] - 1
            ),
            "trim_tail_frames": clip_trim_tail_frames[index],
            "continuity_mode": continuity,
            "boundary_before": boundary_before,
            "prompt": prompt,
            "dialogue_manifest_indices": [
                manifest_index
                for manifest_index, item in enumerate(dialogue_manifest)
                if item["segment_index"] == index
            ],
        })
        cursor += frames
        published_cursor += clip_published_frames[index]

    return {
        "version": H3_SHOT_PLAN_VERSION,
        "semantic_physical_contract_version": (
            H3_SEMANTIC_PHYSICAL_CONTRACT_VERSION
        ),
        "global_prompt": str(global_prompt or ""),
        "fps": fps_value,
        "segment_frames_maximum": (
            int(segment_frames_maximum)
            if segment_frames_maximum is not None else max(counts)
        ),
        "segment_policy": dict(segment_policy or {}),
        "clip_frames": counts,
        "clip_published_frames": clip_published_frames,
        "clip_trim_tail_frames": clip_trim_tail_frames,
        "published_frames": sum(clip_published_frames),
        "clip_prompts": prompts,
        "clip_boundaries": boundaries,
        "source_contracts": source_contracts,
        "semantic_shots": source_contracts,
        "dialogue_manifest": dialogue_manifest,
        "shots": native_shots,
    }


__all__ = [
    "H3_CONTINUITY_MODES",
    "H3_SHOT_PLAN_VERSION",
    "H3_SEGMENT_POLICY_VERSION",
    "H3_SEMANTIC_PHYSICAL_CONTRACT_VERSION",
    "H3ShotPlanError",
    "build_h3_visual_context",
    "floor_h3_frame_count",
    "infer_h3_profile_id",
    "plan_h3_clip_frames",
    "plan_h3_native_shots",
]
