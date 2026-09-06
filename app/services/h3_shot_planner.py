"""Deterministic shared MiniMax H3 native-shot reconciliation.

Studio and Director author one global narrative, then this module maps that
source onto already model-aligned native segments.  It performs no LLM calls:
recovery can therefore persist and replay the exact resulting contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any


H3_SHOT_PLAN_VERSION = 1
H3_SEMANTIC_PHYSICAL_CONTRACT_VERSION = 2
H3_COMPILER_INPUT_REPLAY_VERSION = 1
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


_DIALOGUE_RE = re.compile(
    r"<d>\s*\["
    r"(?=[^\]\r\n]*[^\s\]\r\n][^\]\r\n]*\])"
    r"[^\]\r\n]+\]\s+(?:.*?\S)\s*</d>",
    re.I | re.S,
)
_DIALOGUE_TOKEN_RE = re.compile(r"<\s*(/?)\s*d\s*>", re.I)
_FINAL_BLOCKING_RE = re.compile(
    r"(?<!\S)FINAL\s+BLOCKING\s*:\s*(.+?)"
    r"(?=(?:\s+\[\s*(?:SHOT|SCENE)\b)|\n\s*[A-Z][A-Z _-]+\s*:|\Z)",
    re.I | re.S,
)
_VISUAL_HEADER_RE = re.compile(
    r"^(?:PROJECT\s+CONTINUITY|"
    r"VISUAL\s+CONTINUITY(?:\s*\([^\)\r\n]+\))?|VISUAL\s+WORLD|"
    r"SUBJECT_DEFINITIONS|CAST|"
    r"SETTING|LOCATION|ENVIRONMENT|VISUAL\s+STYLE|LIGHTING)\s*:\s*(.*)$",
    re.I,
)
_NONVISUAL_HEADER_RE = re.compile(r"^[A-Z][A-Z _-]+\s*:")
_H3_CONTEXT_IR_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_H3_CANONICAL_RECORD_RE = re.compile(
    r"^\[Shot\s+(?P<number>[1-9]\d*)\]\s+"
    r"\[(?P<start>(?:(?:\d{1,2}:){1,2})?\d+(?:\.\d+)?)s-"
    r"(?P<end>(?:(?:\d{1,2}:){1,2})?\d+(?:\.\d+)?)s\]\s+"
    r"(?P<payload>shot_name:\s*[^|\r\n]+\s*\|\s*"
    r"audiovisual_description:\s*[^|\r\n]+\s*\|\s*"
    r"dialogue_and_vocalizations:\s*[^\r\n]+)$",
    re.IGNORECASE,
)
_H3_CANONICAL_EVENT_PAYLOAD_RE = re.compile(
    r"^\[Shot\s+[1-9]\d*\]\s+(?P<payload>shot_name:\s*[^|\r\n]+\s*\|\s*"
    r"audiovisual_description:\s*[^|\r\n]+\s*\|\s*"
    r"dialogue_and_vocalizations:\s*[^\r\n]+)$",
    re.IGNORECASE,
)


def _field(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _compact(
    value: Any,
    limit: int,
    *,
    ignored_tokens: Sequence[str] = (),
) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    clean = _strip_dialogue_occurrence_tokens(text, ignored_tokens)
    dialogue_matches: list[re.Match[str]] = []
    if _DIALOGUE_TOKEN_RE.search(clean):
        _validate_dialogue_spans(clean)
        dialogue_matches = list(_DIALOGUE_RE.finditer(clean))
    if len(clean) <= limit:
        return text
    shortened = clean[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    target_length = len(shortened) if shortened else limit
    if any(match.end() > target_length for match in dialogue_matches):
        raise H3ShotPlanError(
            "MiniMax H3 dialogue blocks cannot be truncated by compaction"
        )
    tagged_cursor = 0
    clean_cursor = 0
    while tagged_cursor < len(text) and clean_cursor < target_length:
        matched_token = next((
            token for token in ignored_tokens
            if text.startswith(token, tagged_cursor)
        ), None)
        if matched_token is not None:
            tagged_cursor += len(matched_token)
            if tagged_cursor < len(text) and text[tagged_cursor] == " ":
                tagged_cursor += 1
            continue
        tagged_cursor += 1
        clean_cursor += 1
    tagged_shortened = text[:tagged_cursor].rstrip(" ,;:-")
    return f"{tagged_shortened}..." if shortened else tagged_shortened


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


def _validate_dialogue_spans(text: str) -> None:
    """Require every authored dialogue tag to match the closed H3 grammar."""

    if not _dialogue_spans_are_balanced(text):
        raise H3ShotPlanError(
            "MiniMax H3 dialogue tags must be balanced before planning"
        )
    unmatched = _DIALOGUE_RE.sub("", text)
    if _DIALOGUE_TOKEN_RE.search(unmatched):
        raise H3ShotPlanError(
            "MiniMax H3 dialogue tags must use canonical <d>[language] text</d> "
            "syntax before planning"
        )


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


def _tag_dialogue_occurrences(
    prompt: str,
    manifest: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    """Carry stable semantic dialogue ordinals through physical localization."""

    matches = list(_DIALOGUE_RE.finditer(prompt))
    if len(matches) != len(manifest):
        raise H3ShotPlanError("H3 semantic dialogue occurrence count disagrees")
    salt = 0
    prefix = "__H3_DIALOGUE_OCCURRENCE_0_"
    while prefix in prompt:
        salt += 1
        prefix = f"__H3_DIALOGUE_OCCURRENCE_{salt}_"
    tokens = [f"{prefix}{index}__" for index in range(len(matches))]
    tagged = prompt
    for index in range(len(matches) - 1, -1, -1):
        match = matches[index]
        block = match.group(0)
        if block != manifest[index].get("exact_block"):
            raise H3ShotPlanError("H3 semantic dialogue occurrence order disagrees")
        tokenized = re.sub(
            r"^(<d>\s*\[[^\]\r\n]+\]\s+)",
            rf"\g<1>{tokens[index]} ",
            block,
            count=1,
            flags=re.IGNORECASE,
        )
        tagged = tagged[:match.start()] + tokenized + tagged[match.end():]
    return tagged, tokens


def _semantic_dialogue_identity(
    exact_block: str,
    *,
    source_index: int,
    semantic_occurrence_index: int,
) -> dict[str, Any]:
    """Return dialogue identity derived only from the immutable prompt bytes.

    Structured dialogue is allowed to author or locate an exact ``<d>`` block,
    but arbitrary structured metadata is not an independently replayable input.
    Persist only values that Director can recompute from ``semantic_prompt``.
    """

    return {
        "semantic_occurrence_index": semantic_occurrence_index,
        "exact_block": exact_block,
        "spoken_text": _DIALOGUE_TOKEN_RE.sub("", exact_block).strip(),
        "speaker_id": "",
        "source": "semantic_prompt",
        "source_index": source_index,
    }


def _strip_dialogue_occurrence_tokens(text: str, tokens: Sequence[str]) -> str:
    result = str(text or "")
    for token in tokens:
        result = result.replace(f"{token} ", "").replace(token, "")
    return result


def _extract_final_blocking(
    prompt: str,
    *,
    dialogue_occurrence_tokens: Sequence[str] = (),
) -> tuple[str, str]:
    """Extract the final-blocking field without interpreting dialogue text."""

    protected, blocks = _protect_dialogue(prompt)
    matches = list(_FINAL_BLOCKING_RE.finditer(protected))
    if not matches:
        return prompt, ""
    blocking = _compact(
        _restore_dialogue_exact(matches[-1].group(1), blocks),
        1200,
        ignored_tokens=dialogue_occurrence_tokens,
    )
    without = _restore_dialogue_exact(
        _FINAL_BLOCKING_RE.sub("\n", protected), blocks,
    ).strip()
    return without, blocking


def _blocking_metadata_ranges(text: str) -> list[tuple[str, int, int]]:
    """Locate non-dialogue opening/final metadata spans in one prompt line."""

    value = str(text or "")

    def dialogue_boundary(dialogue_end: int) -> int:
        """Include sentence punctuation immediately outside a dialogue tag."""

        punctuation = re.match(r"[.!?]+", value[dialogue_end:])
        return (
            dialogue_end + len(punctuation.group(0))
            if punctuation is not None
            else dialogue_end
        )

    dialogue_ranges = [
        (match.start(), match.end()) for match in _DIALOGUE_RE.finditer(value)
    ]
    result: list[tuple[str, int, int]] = []
    for marker in re.finditer(
        r"\b(?P<kind>OPENING|FINAL)\s+BLOCKING\s*:",
        value,
        re.IGNORECASE,
    ):
        if any(start <= marker.start() < end for start, end in dialogue_ranges):
            continue
        kind = marker.group("kind").casefold()
        end = len(value)
        if kind == "opening":
            boundary_candidates = [
                dialogue_boundary(dialogue_end)
                for start, dialogue_end in dialogue_ranges
                if start >= marker.end()
                and (
                    not value[dialogue_end:].strip()
                    or re.match(
                        r"FINAL\s+BLOCKING\s*:",
                        value[dialogue_end:].lstrip(),
                        re.IGNORECASE,
                    )
                    or re.search(
                        r"[.!?]\s*</\s*d\s*>\s*$",
                        value[start:dialogue_end],
                        re.IGNORECASE,
                    )
                )
            ]
            for next_marker in re.finditer(
                r"\b(?:OPENING|FINAL)\s+BLOCKING\s*:",
                value[marker.end():],
                re.IGNORECASE,
            ):
                boundary = marker.end() + next_marker.start()
                if not any(
                    start <= boundary < dialogue_end
                    for start, dialogue_end in dialogue_ranges
                ):
                    boundary_candidates.append(boundary)
            for punctuation in re.finditer(r"[.!?]", value[marker.end():]):
                punctuation_end = marker.end() + punctuation.end()
                dialogue_range = next((
                    (start, dialogue_end)
                    for start, dialogue_end in dialogue_ranges
                    if start < punctuation_end <= dialogue_end
                ), None)
                if dialogue_range is not None:
                    dialogue_end = dialogue_range[1]
                    if re.fullmatch(
                        r"\s*</\s*d\s*>",
                        value[punctuation_end:dialogue_end],
                        re.IGNORECASE,
                    ) and (
                        dialogue_end == len(value)
                        or value[dialogue_end].isspace()
                    ):
                        boundary_candidates.append(
                            dialogue_boundary(dialogue_end)
                        )
                    continue
                if (
                    punctuation_end == len(value)
                    or value[punctuation_end].isspace()
                ):
                    boundary_candidates.append(punctuation_end)
            if boundary_candidates:
                end = min(boundary_candidates)
            for start, dialogue_end in dialogue_ranges:
                if not (marker.end() <= start < end):
                    continue
                trailing_within_opening = value[dialogue_end:end].strip()
                if (
                    not trailing_within_opening
                    or re.fullmatch(r"[.!?]+", trailing_within_opening)
                ):
                    continue
                if not re.search(
                    r"[.!?]\s*</\s*d\s*>\s*$",
                    value[start:dialogue_end],
                    re.IGNORECASE,
                ):
                    raise H3ShotPlanError(
                        "Inline OPENING BLOCKING dialogue requires terminal "
                        "punctuation or a line boundary before following prose"
                    )
        result.append((kind, marker.start(), end))
    return result


def _validate_blocking_metadata_spans(text: str) -> None:
    """Reject ambiguous repeated opening fields inside one executable record."""

    for line in str(text or "").splitlines():
        if sum(
            kind == "opening"
            for kind, _start, _end in _blocking_metadata_ranges(line)
        ) > 1:
            raise H3ShotPlanError(
                "MiniMax H3 executable records cannot contain multiple "
                "OPENING BLOCKING fields"
            )


def _join_final_blocking(candidates: Sequence[str]) -> str:
    """Join distinct terminal instructions without changing authored punctuation."""

    combined: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        identity = normalized.rstrip(" .!?").casefold()
        if normalized and identity not in seen:
            combined.append(normalized)
            seen.add(identity)
    if not combined:
        return ""
    result = combined[0]
    for candidate in combined[1:]:
        separator = " " if result.endswith((".", "!", "?")) else ". "
        result += separator + candidate
    return result


def _untimed_units(
    prompt: str,
    required: int,
    *,
    dialogue_occurrence_tokens: Sequence[str] = (),
) -> tuple[list[str], str, str]:
    _validate_dialogue_spans(prompt)
    without_blocking, final_blocking = _extract_final_blocking(
        prompt,
        dialogue_occurrence_tokens=dialogue_occurrence_tokens,
    )
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
    restored_units: list[str] = []
    for unit in (_restore_dialogue(value, blocks) for value in units):
        if restored_units and _DIALOGUE_RE.fullmatch(unit):
            restored_units[-1] = f"{restored_units[-1]} {unit}".strip()
        else:
            restored_units.append(unit)
    return restored_units, context, final_blocking


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


_H3_TERMINAL_HOLD_FRAMES = 192  # eight seconds at 24 fps


def _fold_short_terminal_clip(
    generated: list[int],
    published: list[int],
    *,
    maximum_frames: int,
    hold_frames: int = _H3_TERMINAL_HOLD_FRAMES,
) -> tuple[list[int], list[int]]:
    """Absorb a short closing remainder into the previous native window.

    Authored last shots that only cover a few seconds otherwise become their
    own 5s native clip. If previous+last still fit the model ceiling, keep
    the published duration exact and avoid that extra seam.
    """
    if len(generated) < 2 or len(published) != len(generated):
        return generated, published
    last_pub = int(published[-1])
    prev_pub = int(published[-2])
    last_gen = int(generated[-1])
    prev_gen = int(generated[-2])
    if (
        last_pub >= int(hold_frames)
        or last_pub < 1
        or prev_pub + last_pub > int(maximum_frames)
        or prev_gen + last_gen > int(maximum_frames)
    ):
        return generated, published
    folded_generated = list(generated[:-1])
    folded_published = list(published[:-1])
    folded_generated[-1] = prev_gen + last_gen
    folded_published[-1] = prev_pub + last_pub
    return folded_generated, folded_published


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
                before = len(exact_frames)
                exact_frames, exact_published = _fold_short_terminal_clip(
                    exact_frames,
                    exact_published,
                    maximum_frames=int(maximum_frames),
                )
                if len(exact_frames) < before and boundary_after:
                    boundary_after = [
                        index for index in boundary_after
                        if index < len(exact_frames) - 1
                    ]
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
        })
    return beats


def _compile_source_dialogue(
    prompts: list[str],
    *,
    shot: Any | None,
) -> list[dict[str, Any]]:
    """Bind authored/structured dialogue once without rewriting its words."""

    manifest: list[dict[str, Any]] = []
    structured = _source_dialogue_beats(shot)
    claimed: dict[str, int] = {}

    def replace_untagged_words(text: str, words: str, block: str) -> tuple[str, bool]:
        """Replace one executable occurrence, never repeatable visual state."""

        offset = 0
        for raw_line in text.splitlines(keepends=True):
            stripped = raw_line.strip()
            eligible_start = 0
            canonical_record = _H3_CANONICAL_RECORD_RE.fullmatch(stripped)
            if canonical_record:
                marker = re.search(
                    r"\bdialogue_and_vocalizations\s*:\s*",
                    raw_line,
                    re.IGNORECASE,
                )
                eligible_start = marker.end() if marker else len(raw_line)
            elif (
                _VISUAL_HEADER_RE.match(stripped)
                or re.match(
                    r"^(?:subject_definitions|summary|retention_analysis|"
                    r"overall_soundscape|non_diegetic_music|opening\s+blocking|"
                    r"final\s+blocking)\s*:",
                    stripped,
                    re.IGNORECASE,
                )
            ):
                offset += len(raw_line)
                continue
            dialogue_ranges = [
                (match.start(), match.end())
                for match in _DIALOGUE_RE.finditer(text)
            ]
            blocking_ranges = (
                [] if canonical_record else [
                    (offset + start, offset + end)
                    for _kind, start, end
                    in _blocking_metadata_ranges(raw_line)
                ]
            )
            candidate = raw_line.find(words, eligible_start)
            while candidate >= 0:
                absolute = offset + candidate
                inside_dialogue = any(
                    start <= absolute < end
                    for start, end in dialogue_ranges
                )
                inside_blocking = any(
                    start <= absolute < end
                    for start, end in blocking_ranges
                )
                if not inside_dialogue and not inside_blocking:
                    return (
                        text[:absolute] + block + text[absolute + len(words):],
                        True,
                    )
                candidate = raw_line.find(words, candidate + len(words))
            offset += len(raw_line)
        return text, False

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
        final_index = next((
            index for index, line in enumerate(lines)
            if re.match(r"^\s*FINAL\s+BLOCKING\s*:", line, re.IGNORECASE)
        ), None)
        if final_index is not None:
            lines.insert(final_index, block)
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
                "local_segment_index": local_index,
            })
    # Preserve the semantic prompt's literal dialogue occurrence order. The
    # replay manifest derives its provenance from those prompt bytes later.
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


def _h3_seconds(value: int, fps: float) -> str:
    seconds = max(0.0, int(value) / float(fps))
    if abs(seconds - round(seconds)) < 0.0005:
        return str(int(round(seconds)))
    return f"{seconds:.3f}".rstrip("0").rstrip(".")


def _h3_frame_at(seconds: Any, fps: float) -> int:
    """Map an authored timestamp to its nearest published frame."""

    return max(0, int(round(float(seconds or 0.0) * float(fps))))


def _context_ir_fields(prompt: str) -> tuple[list[str], dict[str, str]]:
    """Extract exact top-level Context-IR values without importing Director."""

    pattern = re.compile(
        r"(?mi)^\s*(" + "|".join(
            re.escape(field) for field in _H3_CONTEXT_IR_FIELDS
        ) + r")\s*:",
    )
    matches = list(pattern.finditer(str(prompt or "")))
    order: list[str] = []
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1).casefold()
        if name in fields:
            return [], {}
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        order.append(name)
        fields[name] = str(prompt[match.end():end]).strip()
    return order, fields


def _canonical_context_ir_parts(
    prompt: str,
) -> tuple[list[str], dict[str, str], str, list[dict[str, Any]]] | None:
    """Return a canonical Context-IR timeline and its immutable fields."""

    from shared.utils.prompt_parser import parse_global_timeline_prompt

    order, fields = _context_ir_fields(prompt)
    visual_field = (
        "detailed_description"
        if "detailed_description" in fields
        else "integrated_multimodal_description"
    )
    visual = fields.get(visual_field, "")
    record_lines = [line.strip() for line in visual.splitlines() if line.strip()]
    if not order or not record_lines or not all(
        _H3_CANONICAL_RECORD_RE.fullmatch(line) for line in record_lines
    ):
        return None
    globals_, events = parse_global_timeline_prompt(visual)
    if globals_ or len(events) != len(record_lines):
        return None
    cursor = 0.0
    for record_index, (record, event) in enumerate(
        zip(record_lines, events), start=1,
    ):
        match = _H3_CANONICAL_RECORD_RE.fullmatch(record)
        start = float(event.get("start") or 0.0)
        end = float(event.get("end") or start)
        if (
            match is None
            or int(match.group("number")) != record_index
            or abs(start - cursor) > 1e-6
            or end <= start
        ):
            raise H3ShotPlanError(
                "Canonical H3 Context-IR ranges must be ordered, contiguous, "
                "and non-overlapping"
            )
        cursor = end
    return order, fields, visual_field, events


def _merge_canonical_semantic_fields(
    prompt: str,
    *,
    visual_context: str,
    opening_blocking: str,
) -> str:
    """Merge normalized semantic inputs into canonical Context-IR once."""

    canonical = _canonical_context_ir_parts(prompt)
    if canonical is None:
        return prompt
    order, fields, visual_field, _events = canonical
    records = [
        line.strip() for line in fields[visual_field].splitlines() if line.strip()
    ]
    if visual_context and visual_context not in fields.get("subject_definitions", ""):
        fields["subject_definitions"] = " ".join(filter(None, (
            fields.get("subject_definitions", ""), visual_context,
        )))
        if "subject_definitions" not in order:
            order.insert(0, "subject_definitions")

    def merge_action(record: str, action: str, *, prefix: str) -> str:
        if not action:
            return record
        marker = re.search(
            r"(?P<head>\baudiovisual_description\s*:\s*)"
            r"(?P<body>[^|\r\n]+)",
            record,
            re.IGNORECASE,
        )
        if marker is None:
            raise H3ShotPlanError(
                "Canonical H3 Context-IR cannot merge structured blocking"
            )
        body = marker.group("body").strip()
        if re.search(
            r"\bOPENING\s+BLOCKING\s*:", body, re.IGNORECASE,
        ):
            if _authored_opening_contains(body, action):
                return record
            raise H3ShotPlanError(
                "H3 structured opening blocking conflicts with authored "
                "OPENING BLOCKING"
            )
        action_separator = " " if action.endswith((".", "!", "?")) else ". "
        replacement = (
            f"{marker.group('head')}{prefix}: {action}{action_separator}{body}"
        ).strip()
        return (
            record[:marker.start()] + replacement + " "
            + record[marker.end():].lstrip()
        )

    if records:
        records[0] = merge_action(
            records[0], opening_blocking, prefix="Opening blocking",
        )
    fields[visual_field] = "\n".join(records)
    return "\n\n".join(
        f"{name}:\n{fields[name]}" if name == visual_field
        else f"{name}: {fields[name]}"
        for name in order
    ).strip()


def _compile_semantic_prompt(
    authored_prompt: str,
    *,
    visual_context: str,
    opening_blocking: str,
    final_blocking: str,
    structured_dialogue_blocks: Sequence[str],
) -> tuple[str, list[dict[str, Any]]]:
    """Compile exact semantic bytes from explicit replay-authoritative inputs."""

    if opening_blocking:
        _validate_dialogue_spans(opening_blocking)
        protected_opening, _opening_dialogue = _protect_dialogue(
            opening_blocking,
        )
        if re.search(
            r"\b(?:OPENING|FINAL)\s+BLOCKING\s*:",
            protected_opening,
            re.IGNORECASE,
        ):
            raise H3ShotPlanError(
                "H3 structured opening blocking contains a reserved "
                "structural marker"
            )

    source_is_canonical = _canonical_context_ir_parts(authored_prompt) is not None
    if source_is_canonical:
        semantic_prompt = _merge_canonical_semantic_fields(
            authored_prompt,
            visual_context=visual_context,
            opening_blocking=opening_blocking,
        )
    else:
        semantic_prompt = authored_prompt
        if visual_context and visual_context not in semantic_prompt:
            semantic_prompt = f"{visual_context}\n{semantic_prompt}".strip()
        if (
            opening_blocking
            and not re.search(
                r"\bOPENING\s+BLOCKING\s*:", semantic_prompt, re.I,
            )
        ):
            semantic_prompt = (
                f"OPENING BLOCKING: {opening_blocking}\n{semantic_prompt}"
            ).strip()
        if final_blocking:
            without_authored_final, authored_final = _extract_final_blocking(
                semantic_prompt,
            )
            same_final = (
                authored_final.rstrip(" .!?").casefold()
                == final_blocking.rstrip(" .!?").casefold()
            )
            if not authored_final:
                semantic_prompt = (
                    f"{semantic_prompt}\nFINAL BLOCKING: {final_blocking}"
                ).strip()
            elif not same_final:
                semantic_prompt = (
                    f"{without_authored_final}\nFINAL BLOCKING: "
                    f"{_join_final_blocking([final_blocking, authored_final])}"
                ).strip()

    semantic_prompts = [semantic_prompt]
    source_dialogue = _compile_source_dialogue(
        semantic_prompts,
        shot={
            "dialogue_beats": [
                {"exact_block": block}
                for block in structured_dialogue_blocks
            ],
        },
    )
    _validate_dialogue_spans(semantic_prompts[0])
    _validate_blocking_metadata_spans(semantic_prompts[0])
    return semantic_prompts[0], source_dialogue


def _authored_opening_contains(
    authored_prompt: str,
    opening_blocking: str,
) -> bool:
    """Return whether an exact structured opening is authored after its marker."""

    if not opening_blocking:
        return False
    expected = re.sub(r"\s+", " ", opening_blocking).strip().rstrip(".!?")
    expected = expected.strip().casefold()
    authored = _authored_opening_payload(authored_prompt)
    actual = re.sub(r"\s+", " ", authored).strip().rstrip(".!?")
    return bool(authored) and actual.strip().casefold() == expected


def _authored_opening_payload(authored_prompt: str) -> str:
    """Return the exact payload of the first parsed authored opening field."""

    for kind, start, end in _blocking_metadata_ranges(authored_prompt):
        if kind != "opening":
            continue
        span = authored_prompt[start:end]
        marker = re.search(
            r"\bOPENING\s+BLOCKING\s*:", span, re.IGNORECASE,
        )
        if marker is not None:
            return span[marker.end():].strip()
    return ""


def _event_frame_ranges(
    events: Sequence[Mapping[str, Any]],
    *,
    total_frames: int,
    fps: float,
) -> list[tuple[int, int]]:
    """Convert authored events to bounded half-open published-frame ranges."""

    ordered_starts = sorted({
        _h3_frame_at(event.get("start"), fps)
        for event in events
        if str(event.get("kind") or "") == "shot"
    })
    result: list[tuple[int, int]] = []
    for event in events:
        start = _h3_frame_at(event.get("start"), fps)
        kind = str(event.get("kind") or "")
        if kind == "point":
            end = min(int(total_frames), start + 1)
        elif kind == "shot":
            end = next(
                (value for value in ordered_starts if value > start),
                int(total_frames),
            )
        else:
            end = _h3_frame_at(event.get("end"), fps)
        result.append((start, max(start + 1, end)))
    return result


def _canonical_action_parts(
    action: str,
    *,
    opening_blocking: str = "",
    dialogue_occurrence_tokens: Sequence[str] = (),
) -> tuple[str, str, str]:
    """Separate non-repeatable inline blocking from a continuing action."""

    value, dialogue_blocks = _protect_dialogue(str(action or "").strip())
    final_match = re.search(
        r"\bFINAL\s+BLOCKING\s*:\s*(?P<value>.+)$",
        value,
        re.IGNORECASE,
    )
    final = final_match.group("value").strip() if final_match else ""
    if final_match:
        value = (value[:final_match.start()] + value[final_match.end():]).strip()
    owner_action = re.sub(r"\s+", " ", value).strip(" ,:;-")
    exact_opening = _compact(opening_blocking, 600)
    exact_removed = False
    continued_is_restored = False
    if exact_opening:
        protected_opening = exact_opening
        for token, block in dialogue_blocks:
            clean_block = _strip_dialogue_occurrence_tokens(
                block, dialogue_occurrence_tokens,
            )
            protected_opening = protected_opening.replace(clean_block, token, 1)
        continued_action, removed = re.subn(
            r"\bOPENING\s+BLOCKING\s*:\s*"
            + re.escape(protected_opening)
            + r"(?=[.!?\s]|$)[.!?]*\s*",
            "",
            owner_action,
            count=1,
            flags=re.IGNORECASE,
        )
        exact_removed = bool(removed)
    if not exact_removed:
        restored_owner = _restore_dialogue_exact(
            owner_action, dialogue_blocks,
        )
        continued_action = restored_owner
        opening_ranges = [
            (start, end)
            for kind, start, end in _blocking_metadata_ranges(restored_owner)
            if kind == "opening"
        ]
        for start, end in reversed(opening_ranges):
            continued_action = (
                continued_action[:start] + continued_action[end:]
            )
        continued_is_restored = True
    continued_action = re.sub(r"\s+", " ", continued_action).strip(" ,:;-")
    return (
        _restore_dialogue_exact(owner_action, dialogue_blocks),
        (
            continued_action if continued_is_restored
            else _restore_dialogue_exact(continued_action, dialogue_blocks)
        ),
        _restore_dialogue_exact(final.strip(" ."), dialogue_blocks),
    )


def _physical_ranges(published_frames: Sequence[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for value in published_frames:
        end = cursor + int(value)
        ranges.append((cursor, end))
        cursor = end
    return ranges


def _owner_for_frame(frame: int, ranges: Sequence[tuple[int, int]]) -> int:
    for index, (start, end) in enumerate(ranges):
        if start <= int(frame) < end:
            return index
    raise H3ShotPlanError(
        "H3 authored event falls outside the published physical geometry"
    )


def _render_context_ir_segment(
    *,
    order: Sequence[str],
    fields: Mapping[str, str],
    visual_field: str,
    owned_events: Sequence[tuple[Mapping[str, Any], int, int, str, bool]],
    segment_start: int,
    segment_end: int,
    fps: float,
    final_blocking: str = "",
    opening_blocking: str = "",
    dialogue_occurrence_tokens: Sequence[str] = (),
) -> str:
    """Render strict local Context-IR with continuity records filling gaps."""

    duration = segment_end - segment_start
    pieces: list[tuple[int, int, str]] = []
    for event, event_start, event_end, _event_id, continuation in sorted(
        owned_events,
        key=lambda item: (item[1], int(item[0].get("order", 0))),
    ):
        payload_match = _H3_CANONICAL_EVENT_PAYLOAD_RE.fullmatch(
            str(event.get("text") or "").strip()
        )
        if payload_match is None:
            raise H3ShotPlanError(
                "Canonical H3 Context-IR event payload is malformed"
            )
        payload = payload_match.group("payload").strip()
        if continuation:
            shot_name = re.search(
                r"\bshot_name\s*:\s*(?P<value>[^|\r\n]+)",
                payload,
                re.IGNORECASE,
            )
            action = re.search(
                r"\baudiovisual_description\s*:\s*(?P<value>[^|\r\n]+)",
                payload,
                re.IGNORECASE,
            )
            if shot_name is None or action is None:
                raise H3ShotPlanError(
                    "Canonical H3 continuation payload is malformed"
                )
            _owner_action, continued_action, _inline_final = _canonical_action_parts(
                action.group("value"),
                # Canonical structured opening is merged into record zero.
                # Every later record must strip its own parsed opening span;
                # the source-level value is not a cross-record prefix key.
                opening_blocking=(
                    opening_blocking
                    if int(event.get("order", 0)) == 0
                    else ""
                ),
                dialogue_occurrence_tokens=dialogue_occurrence_tokens,
            )
            if not continued_action:
                continued_action = "maintain the established visual state"
            continued_action = re.sub(
                r"\s+", " ", _DIALOGUE_RE.sub("", continued_action),
            ).strip()
            if not continued_action:
                continued_action = "maintain the established visual state"
            payload = (
                f"shot_name: Continuation of {shot_name.group('value').strip()} | "
                "audiovisual_description: Continue the same authored action: "
                f"{continued_action} | "
                "dialogue_and_vocalizations: none"
            )
        else:
            action = re.search(
                r"(?P<head>\baudiovisual_description\s*:\s*)"
                r"(?P<value>[^|\r\n]+)",
                payload,
                re.IGNORECASE,
            )
            if action is None:
                raise H3ShotPlanError(
                    "Canonical H3 Context-IR event action is malformed"
                )
            owner_action, _continued_action, inline_final = (
                _canonical_action_parts(
                    action.group("value"),
                    dialogue_occurrence_tokens=dialogue_occurrence_tokens,
                )
            )
            if inline_final:
                owner_action = (
                    owner_action or "maintain the established visual state"
                )
                replacement = f"{action.group('head')}{owner_action}"
                payload = (
                    payload[:action.start()] + replacement + " "
                    + payload[action.end():].lstrip()
                )
        local_start = max(0, event_start - segment_start)
        local_end = min(duration, max(local_start + 1, event_end - segment_start))
        pieces.append((local_start, local_end, payload))

    if final_blocking:
        blocking_payload = _compact(
            final_blocking,
            1200,
            ignored_tokens=dialogue_occurrence_tokens,
        )
        blocking_sentence = (
            blocking_payload
            if blocking_payload.endswith((".", "!", "?"))
            else f"{blocking_payload}."
        )
        covering_index = next((
            index for index in range(len(pieces) - 1, -1, -1)
            if pieces[index][0] < duration <= pieces[index][1]
        ), None)
        if covering_index is not None:
            start, end, payload = pieces[covering_index]
            marker = re.search(
                r"(?P<head>\baudiovisual_description\s*:\s*)"
                r"(?P<body>[^|\r\n]+)",
                payload,
                re.IGNORECASE,
            )
            if marker is None:
                raise H3ShotPlanError(
                    "Canonical H3 Context-IR cannot merge final blocking"
                )
            body = marker.group("body").strip()
            replacement = (
                f"{marker.group('head')}{body} "
                f"Final blocking: {blocking_sentence}"
            )
            pieces[covering_index] = (
                start,
                end,
                payload[:marker.start()] + replacement + " "
                + payload[marker.end():].lstrip(),
            )
        else:
            pieces.append((
                max(0, duration - 1),
                duration,
                "shot_name: Final blocking | audiovisual_description: "
                f"{blocking_payload} | dialogue_and_vocalizations: none",
            ))

    continuity_subjects = " ".join(dict.fromkeys(re.findall(
        r"<Subject\s+[1-9]\d*>",
        str(fields.get("subject_definitions") or ""),
        flags=re.IGNORECASE,
    )))
    continuity_payload = (
        "shot_name: Visual continuity | audiovisual_description: "
        + (f"{continuity_subjects} " if continuity_subjects else "")
        + "continues in the established visual state and continuity without "
        "repeating an authored action. | dialogue_and_vocalizations: none"
    )
    timeline: list[tuple[int, int, str]] = []
    cursor = 0
    for start, end, payload in pieces:
        start = max(cursor, start)
        if start > cursor:
            timeline.append((cursor, start, continuity_payload))
        if end > start:
            timeline.append((start, end, payload))
            cursor = end
    if cursor < duration:
        timeline.append((cursor, duration, continuity_payload))
    if not timeline:
        timeline = [(0, duration, continuity_payload)]

    records = [
        f"[Shot {index}] [{_h3_seconds(start, fps)}s-"
        f"{_h3_seconds(end, fps)}s] {payload}"
        for index, (start, end, payload) in enumerate(timeline, start=1)
    ]
    output: list[str] = []
    for name in order:
        if name == visual_field:
            value = "\n".join(records)
        elif name == "summary":
            value = (
                "Execute only the segment-local detailed_description records below."
                if owned_events else
                "Continue only the established visual and reference continuity; "
                "no authored event is scheduled in this physical segment."
            )
        else:
            value = str(fields[name])
        output.append(f"{name}: {value}".strip())
    return "\n\n".join(output).strip()


def _compile_segment_local_prompts(
    semantic_prompt: str,
    *,
    segment_positions: Sequence[int],
    published_frames: Sequence[int],
    source_index: int,
    fps: float,
    final_blocking: str = "",
    opening_blocking: str = "",
    dialogue_occurrence_tokens: Sequence[str] = (),
) -> tuple[list[str], list[dict[str, Any]]]:
    """Compile deterministic executable prompts after exact physical geometry."""

    from shared.utils.prompt_parser import parse_global_timeline_prompt

    positions = list(segment_positions)
    local_published = [int(published_frames[position]) for position in positions]
    ranges = _physical_ranges(local_published)
    total_frames = sum(local_published)
    canonical = _canonical_context_ir_parts(semantic_prompt)
    if canonical is not None:
        order, fields, visual_field, parsed_events = canonical
        global_lines: list[str] = []
        events = list(parsed_events)
        inline_final_blocking: list[str] = []
        for event in events:
            payload_match = _H3_CANONICAL_EVENT_PAYLOAD_RE.fullmatch(
                str(event.get("text") or "").strip()
            )
            action_match = re.search(
                r"\baudiovisual_description\s*:\s*(?P<action>[^|\r\n]+)",
                payload_match.group("payload") if payload_match else "",
                re.IGNORECASE,
            )
            if action_match is not None:
                _owner_action, _continued_action, inline_final = (
                    _canonical_action_parts(
                        action_match.group("action"),
                        dialogue_occurrence_tokens=dialogue_occurrence_tokens,
                    )
                )
                if inline_final:
                    inline_final_blocking.append(inline_final)
        final_blocking = _join_final_blocking((
            _compact(final_blocking, 1200), *inline_final_blocking,
        ))
    else:
        global_lines, parsed_events = parse_global_timeline_prompt(semantic_prompt)
        had_parsed_events = bool(parsed_events)
        events = []
        inline_final_blocking: list[str] = []
        for parsed_event in parsed_events:
            event = dict(parsed_event)
            event_text, event_final = _extract_final_blocking(
                str(event.get("text") or ""),
                dialogue_occurrence_tokens=dialogue_occurrence_tokens,
            )
            if event_final:
                inline_final_blocking.append(event_final)
            event_text = event_text.strip()
            if re.fullmatch(
                r"\[\s*(?:shot|scene)\s+\d+(?:\s*[^\]]*)?\]",
                event_text,
                re.IGNORECASE,
            ):
                event_text = ""
            if event_text:
                event["text"] = event_text
                events.append(event)
    events.sort(key=lambda event: (
        int(event.get("order", 0)),
        float(event.get("start", 0.0)),
    ))

    for event in events:
        kind = str(event.get("kind") or "")
        authored_start = _h3_frame_at(event.get("start"), fps)
        if authored_start >= total_frames:
            raise H3ShotPlanError(
                "H3 authored event falls outside the published physical geometry"
            )
        if (
            kind == "range"
            and _h3_frame_at(event.get("end"), fps) > total_frames
        ):
            raise H3ShotPlanError(
                "H3 authored range exceeds the published physical geometry"
            )

    event_ranges = _event_frame_ranges(
        events, total_frames=total_frames, fps=fps,
    ) if events else []
    if canonical is not None:
        frame_cursor = 0
        for event in events:
            start = min(total_frames, _h3_frame_at(event.get("start"), fps))
            end = min(total_frames, _h3_frame_at(event.get("end"), fps))
            if start != frame_cursor or end <= start:
                raise H3ShotPlanError(
                    "Canonical H3 Context-IR timestamps collapse or overlap "
                    "on the published frame grid"
                )
            frame_cursor = end
        if frame_cursor != total_frames:
            raise H3ShotPlanError(
                "Canonical H3 Context-IR timestamps must cover the exact "
                "published physical geometry"
            )
    previous_event_end = 0
    open_shot_start = 0
    open_shot_end = 0
    for event, (event_start, event_end) in zip(events, event_ranges):
        kind = str(event.get("kind") or "")
        raw_start = min(total_frames, _h3_frame_at(event.get("start"), fps))
        raw_end = (
            min(total_frames, _h3_frame_at(event.get("end"), fps))
            if kind == "range" else event_end
        )
        nested_point = (
            kind == "point"
            and open_shot_end > open_shot_start
            and event_start >= open_shot_start
            and event_end <= open_shot_end
        )
        if raw_end <= raw_start or (
            event_start < previous_event_end and not nested_point
        ):
            raise H3ShotPlanError(
                "H3 timed event timestamps collapse or overlap on the "
                "published frame grid"
            )
        if nested_point:
            continue
        previous_event_end = event_end
        if kind == "shot":
            open_shot_start = event_start
            open_shot_end = event_end
    ownership: list[dict[str, Any]] = []
    owned_by_local: list[
        list[tuple[Mapping[str, Any], int, int, str, bool]]
    ] = [
        [] for _ in positions
    ]
    for event_index, (event, (event_start, event_end)) in enumerate(
        zip(events, event_ranges)
    ):
        owner = _owner_for_frame(event_start, ranges)
        event_id = f"h3-source-{source_index + 1}-event-{event_index + 1}"
        owned_by_local[owner].append((
            event, event_start, event_end, event_id, False,
        ))
        segment_start, segment_end = ranges[owner]
        continuation_slices: list[dict[str, Any]] = []
        for continuation_index in range(owner + 1, len(ranges)):
            continuation_start, continuation_end = ranges[continuation_index]
            intersection_start = max(event_start, continuation_start)
            intersection_end = min(event_end, continuation_end)
            if intersection_end <= intersection_start:
                continue
            owned_by_local[continuation_index].append((
                event,
                intersection_start,
                intersection_end,
                event_id,
                True,
            ))
            continuation_slices.append({
                "segment_index": positions[continuation_index],
                "physical_segment_index": continuation_index,
                "source_start_frame": intersection_start,
                "source_end_frame_exclusive": intersection_end,
                "local_start_frame": intersection_start - continuation_start,
                "local_end_frame_exclusive": intersection_end - continuation_start,
            })
        executable_payload = str(event.get("text") or "").strip()
        if canonical is not None:
            payload_match = _H3_CANONICAL_EVENT_PAYLOAD_RE.fullmatch(
                executable_payload
            )
            action_match = re.search(
                r"\baudiovisual_description\s*:\s*(?P<action>[^|\r\n]+)",
                payload_match.group("payload") if payload_match else "",
                re.IGNORECASE,
            )
            if action_match is None:
                raise H3ShotPlanError(
                    "Canonical H3 Context-IR event payload is malformed"
                )
            executable_payload, _continued_action, _inline_final = _canonical_action_parts(
                action_match.group("action"),
                dialogue_occurrence_tokens=dialogue_occurrence_tokens,
            )
            if not executable_payload:
                executable_payload = "maintain the established visual state"
        ownership.append({
            "event_id": event_id,
            "source_index": source_index,
            "authored_order": int(event.get("order", event_index)),
            "kind": str(event.get("kind") or "range"),
            "owner_segment_index": positions[owner],
            "owner_physical_segment_index": owner,
            "source_start_frame": event_start,
            "source_end_frame_exclusive": event_end,
            "local_start_frame": event_start - segment_start,
            "local_end_frame_exclusive": min(
                segment_end, event_end,
            ) - segment_start,
            "continuation_slices": continuation_slices,
            "executable_payload": executable_payload,
        })

    if canonical is not None:
        if final_blocking:
            ownership.append({
                "event_id": f"h3-source-{source_index + 1}-final-blocking",
                "source_index": source_index,
                "authored_order": len(events),
                "kind": "final_blocking",
                "owner_segment_index": positions[-1],
                "owner_physical_segment_index": len(positions) - 1,
                "source_start_frame": None,
                "source_end_frame_exclusive": None,
                "local_start_frame": None,
                "local_end_frame_exclusive": None,
                "continuation_slices": [],
                "executable_payload": final_blocking,
            })
        prompts = [
            _render_context_ir_segment(
                order=order,
                fields=fields,
                visual_field=visual_field,
                owned_events=owned_by_local[local_index],
                segment_start=start,
                segment_end=end,
                fps=fps,
                final_blocking=(
                    final_blocking if local_index == len(ranges) - 1 else ""
                ),
                opening_blocking=opening_blocking,
                dialogue_occurrence_tokens=dialogue_occurrence_tokens,
            )
            for local_index, (start, end) in enumerate(ranges)
        ]
        return prompts, ownership

    # Only explicitly labelled visual state is repeatable. Untimed prose and
    # every dialogue block remain executable events with exactly one owner.
    untimed_source = (
        "\n".join(global_lines).strip()
        if had_parsed_events else semantic_prompt
    )
    units, visual_context, untimed_final_blocking = _untimed_units(
        untimed_source,
        len(positions),
        dialogue_occurrence_tokens=dialogue_occurrence_tokens,
    )
    final_blocking = _join_final_blocking((
        final_blocking,
        *inline_final_blocking,
        untimed_final_blocking,
    ))
    assigned: list[list[tuple[int, str]]] = [[] for _ in positions]
    for unit_index, unit in enumerate(units):
        if len(units) <= 1:
            owner = 0
        elif len(units) <= len(positions):
            owner = round(unit_index * (len(positions) - 1) / (len(units) - 1))
        else:
            owner = unit_index * len(positions) // len(units)
        assigned[owner].append((unit_index, unit))
        event_id = f"h3-source-{source_index + 1}-untimed-{unit_index + 1}"
        ownership.append({
            "event_id": event_id,
            "source_index": source_index,
            "authored_order": unit_index,
            "kind": "untimed",
            "owner_segment_index": positions[owner],
            "owner_physical_segment_index": owner,
            "source_start_frame": None,
            "source_end_frame_exclusive": None,
            "local_start_frame": None,
            "local_end_frame_exclusive": None,
            "continuation_slices": [],
            "executable_payload": unit,
        })

    if final_blocking:
        ownership.append({
            "event_id": f"h3-source-{source_index + 1}-final-blocking",
            "source_index": source_index,
            "authored_order": len(units) + len(events),
            "kind": "final_blocking",
            "owner_segment_index": positions[-1],
            "owner_physical_segment_index": len(positions) - 1,
            "source_start_frame": None,
            "source_end_frame_exclusive": None,
            "local_start_frame": None,
            "local_end_frame_exclusive": None,
            "continuation_slices": [],
            "executable_payload": final_blocking,
        })

    # Timed events are already assigned above; untimed preamble actions are
    # added once. Render their authored times relative to the owning segment.
    prompts: list[str] = []
    for local_index, (start, end) in enumerate(ranges):
        lines = [visual_context] if visual_context else []
        lines.extend(unit for _, unit in assigned[local_index])
        timed_lines: list[tuple[int, int, str, bool]] = []
        for (
            event, event_start, event_end, _event_id, continuation
        ) in owned_by_local[local_index]:
            local_start = max(0, event_start - start)
            local_end = min(end, event_end) - start
            event_text = str(event.get("text") or "").strip()
            if continuation:
                opening_ranges = [
                    (metadata_start, metadata_end)
                    for kind, metadata_start, metadata_end
                    in _blocking_metadata_ranges(event_text)
                    if kind == "opening"
                ]
                for metadata_start, metadata_end in reversed(opening_ranges):
                    event_text = (
                        event_text[:metadata_start]
                        + event_text[metadata_end:]
                    )
                event_text = _DIALOGUE_RE.sub("", event_text)
                event_text = re.sub(
                    r"^\[Shot\s+[1-9]\d*\]\s*", "", event_text,
                    flags=re.IGNORECASE,
                )
                event_text = re.sub(r"\s+", " ", event_text).strip(" ,;:-")
                event_text = (
                    "CONTINUATION OF AUTHORED ACTION: " + event_text
                    if event_text else
                    "CONTINUATION OF AUTHORED NON-DIALOGUE ACTION STATE"
                )
            if str(event.get("kind") or "") == "point":
                rendered = (
                    f"[{_h3_seconds(local_start, fps)}-"
                    f"{_h3_seconds(local_end, fps)}s] {event_text}"
                )
                timed_lines.append((local_start, local_end, rendered, False))
            else:
                rendered = (
                    f"[{_h3_seconds(local_start, fps)}-"
                    f"{_h3_seconds(local_end, fps)}s] "
                    f"{event_text}"
                )
                timed_lines.append((local_start, local_end, rendered, False))
        interval_lines = any(not point for *_range, point in timed_lines)
        if interval_lines:
            cursor = 0
            continuity = (
                "Continue the established visual state and continuity without "
                "repeating an authored action."
            )
            for local_start, local_end, rendered, point in sorted(timed_lines):
                if not point and local_start > cursor:
                    lines.append(
                        f"[{_h3_seconds(cursor, fps)}-"
                        f"{_h3_seconds(local_start, fps)}s] {continuity}"
                    )
                lines.append(rendered)
                if not point:
                    cursor = max(cursor, local_end)
            duration = end - start
            if cursor < duration:
                lines.append(
                    f"[{_h3_seconds(cursor, fps)}-"
                    f"{_h3_seconds(duration, fps)}s] {continuity}"
                )
        else:
            lines.extend(rendered for *_range, rendered, _point in timed_lines)
        if not any(
            assigned[local_index]
            or owned_by_local[local_index]
        ):
            lines.append(
                "Continue the established visual state and continuity without "
                "repeating an authored action."
            )
        if local_index == len(ranges) - 1 and final_blocking:
            lines.append(f"FINAL BLOCKING: {final_blocking}")
        prompts.append("\n".join(line for line in lines if line).strip())
    return prompts, ownership


def _h3_plan_seal_payload(shot_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Project replay-critical prompt data without including its own seal."""

    return {
        "semantic_physical_contract_version": shot_plan.get(
            "semantic_physical_contract_version"
        ),
        "global_prompt": shot_plan.get("global_prompt"),
        "fps": shot_plan.get("fps"),
        "segment_frames_maximum": shot_plan.get("segment_frames_maximum"),
        "segment_policy": shot_plan.get("segment_policy"),
        "clip_frames": shot_plan.get("clip_frames"),
        "clip_published_frames": shot_plan.get("clip_published_frames"),
        "clip_trim_tail_frames": shot_plan.get("clip_trim_tail_frames"),
        "clip_prompts": shot_plan.get("clip_prompts"),
        "clip_boundaries": shot_plan.get("clip_boundaries"),
        "source_contracts": shot_plan.get("source_contracts"),
        "semantic_shots": shot_plan.get("semantic_shots"),
        "event_ownership": shot_plan.get("event_ownership"),
        "dialogue_manifest": shot_plan.get("dialogue_manifest"),
        "shots": shot_plan.get("shots"),
        "h3_style_workflow": shot_plan.get("h3_style_workflow"),
        "director_runtime_contract": shot_plan.get("director_runtime_contract"),
    }


def seal_h3_shot_plan(shot_plan: dict[str, Any]) -> dict[str, Any]:
    """Seal exact executable prompt bytes and ownership for deterministic replay."""

    payload = json.dumps(
        _h3_plan_seal_payload(shot_plan),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    shot_plan["prompt_contract_seal"] = {
        "version": 1,
        "algorithm": "sha256",
        "digest": hashlib.sha256(payload).hexdigest(),
    }
    return shot_plan["prompt_contract_seal"]


def validate_h3_shot_plan_seal(shot_plan: Mapping[str, Any]) -> None:
    """Reject replay when sealed executable bytes or ownership have drifted."""

    seal = shot_plan.get("prompt_contract_seal")
    if not isinstance(seal, Mapping):
        raise H3ShotPlanError("Saved H3 v2 prompt contract seal is missing")
    if seal.get("version") != 1 or seal.get("algorithm") != "sha256":
        raise H3ShotPlanError("Saved H3 v2 prompt contract seal is unsupported")
    payload = json.dumps(
        _h3_plan_seal_payload(shot_plan),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if seal.get("digest") != hashlib.sha256(payload).hexdigest():
        raise H3ShotPlanError("Saved H3 v2 prompt contract seal disagrees")


def plan_h3_native_shots(
    *,
    global_prompt: str,
    clip_frame_counts: Sequence[int],
    fps: float,
    clip_boundaries: Sequence[Mapping[str, Any]] | None = None,
    source_prompts: Sequence[str] | None = None,
    source_indices: Sequence[int] | None = None,
    structured_shots: Sequence[Any] | None = None,
    source_compiler_inputs: Sequence[Mapping[str, Any]] | None = None,
    source_requested_frames: Sequence[int] | None = None,
    clip_requested_frames: Sequence[int] | None = None,
    segment_frames_maximum: int | None = None,
    segment_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile semantic H3 shots into persistent native execution segments.

    Frame geometry is supplied by the caller after applying the selected
    profile/model ceiling. A semantic prompt is preserved once per authored
    source, then deterministically compiled into segment-local executable
    Context-IR. A model-grid split never requests another LLM rewrite.
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
    if sorted(set(indices)) != list(range(max(indices) + 1)):
        raise H3ShotPlanError("H3 source indices must be dense from zero")
    prompts_by_source = list(source_prompts or [str(global_prompt or "")])
    shots = list(structured_shots or [])
    replay_inputs = (
        list(source_compiler_inputs)
        if source_compiler_inputs is not None
        else None
    )
    if replay_inputs is not None:
        if shots:
            raise H3ShotPlanError(
                "H3 replay compiler inputs cannot be combined with structured shots"
            )
        if len(replay_inputs) != len(prompts_by_source):
            raise H3ShotPlanError(
                "H3 replay compiler inputs must align with source prompts"
            )
        if any(not isinstance(value, Mapping) for value in replay_inputs):
            raise H3ShotPlanError(
                "H3 replay compiler inputs are incomplete"
            )
        if len(prompts_by_source) != max(indices) + 1:
            raise H3ShotPlanError(
                "H3 replay compiler inputs must exactly cover used sources"
            )
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
    event_ownership: list[dict[str, Any]] = []
    source_contracts: list[dict[str, Any]] = []
    seen_authored_shot_ids: set[str] = set()
    for source_index in sorted(set(indices)):
        positions = [
            index for index, value in enumerate(indices) if value == source_index
        ]
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise H3ShotPlanError("H3 source segments must remain chronological")
        source = str(prompts_by_source[source_index] or "").strip()
        local_published = [clip_published_frames[index] for index in positions]
        shot = shots[source_index] if source_index < len(shots) else None
        replay_input = (
            replay_inputs[source_index] if replay_inputs is not None else None
        )
        if replay_input is not None:
            required_replay_fields = {
                "version",
                "authored_shot_id",
                "visual_context",
                "opening_blocking",
                "final_blocking",
                "structured_dialogue_blocks",
            }
            if (
                not isinstance(replay_input, Mapping)
                or set(replay_input) != required_replay_fields
                or type(replay_input.get("version")) is not int
                or replay_input.get("version")
                    != H3_COMPILER_INPUT_REPLAY_VERSION
                or not isinstance(replay_input.get("authored_shot_id"), str)
                or not replay_input.get("authored_shot_id", "").strip()
                or not isinstance(replay_input.get("visual_context"), str)
                or not isinstance(replay_input.get("opening_blocking"), str)
                or not isinstance(replay_input.get("final_blocking"), str)
                or not isinstance(
                    replay_input.get("structured_dialogue_blocks"), list,
                )
                or not all(
                    isinstance(block, str)
                    for block in replay_input.get(
                        "structured_dialogue_blocks", [],
                    )
                )
            ):
                raise H3ShotPlanError(
                    "H3 replay compiler inputs are incomplete"
                )
            authored_shot_id = replay_input["authored_shot_id"]
            visual_context = replay_input["visual_context"]
            opening_blocking = replay_input["opening_blocking"]
            raw_final_blocking = replay_input["final_blocking"]
            structured_dialogue_blocks = list(
                replay_input["structured_dialogue_blocks"]
            )
            if (
                authored_shot_id != authored_shot_id.strip()
                or visual_context
                    != re.sub(r"\s+", " ", visual_context).strip()
                or opening_blocking != _compact(opening_blocking, 600)
                or raw_final_blocking != _compact(raw_final_blocking, 1200)
                or any(
                    block != block.strip()
                    or _DIALOGUE_RE.fullmatch(block) is None
                    for block in structured_dialogue_blocks
                )
            ):
                raise H3ShotPlanError(
                    "H3 replay compiler inputs are not canonical"
                )
        else:
            authored_shot_id = _authored_shot_id(shot, source_index)
            visual_context = build_h3_visual_context(shot)
            opening_blocking = _compact(_field(shot, "spatial_setup", ""), 600)
            raw_final_blocking = str(
                _field(shot, "closing_blocking", "")
                or _field(shot, "ending_beat", "")
                or ""
            ).strip()
            structured_dialogue_blocks = [
                item["exact_block"] for item in _source_dialogue_beats(shot)
            ]
        if authored_shot_id in seen_authored_shot_ids:
            raise H3ShotPlanError(
                f"Duplicate authored H3 shot ID: {authored_shot_id}"
            )
        seen_authored_shot_ids.add(authored_shot_id)

        # All deterministic semantic compilation happens once, before native
        # geometry fans the shot out into physical execution segments.
        source_is_canonical_context_ir = _canonical_context_ir_parts(source) is not None
        if "|" in opening_blocking:
            raise H3ShotPlanError(
                "H3 structured opening blocking contains a reserved separator"
            )
        if _DIALOGUE_TOKEN_RE.search(raw_final_blocking):
            raise H3ShotPlanError(
                "H3 structured final blocking cannot contain dialogue; use "
                "dialogue_beats for spoken text"
            )
        final_blocking = _compact(
            raw_final_blocking, 1200,
        )
        if "|" in final_blocking:
            raise H3ShotPlanError(
                "H3 structured final blocking contains a reserved separator"
            )
        semantic_prompt, source_dialogue = _compile_semantic_prompt(
            source,
            visual_context=visual_context,
            opening_blocking=opening_blocking,
            final_blocking=final_blocking,
            structured_dialogue_blocks=structured_dialogue_blocks,
        )
        if visual_context:
            without_visual, _ = _compile_semantic_prompt(
                source,
                visual_context="",
                opening_blocking=opening_blocking,
                final_blocking=final_blocking,
                structured_dialogue_blocks=structured_dialogue_blocks,
            )
            if without_visual == semantic_prompt:
                visual_context = ""
        if final_blocking and not source_is_canonical_context_ir:
            without_final, _ = _compile_semantic_prompt(
                source,
                visual_context=visual_context,
                opening_blocking=opening_blocking,
                final_blocking="",
                structured_dialogue_blocks=structured_dialogue_blocks,
            )
            if without_final == semantic_prompt:
                final_blocking = ""
        if opening_blocking:
            without_opening, _ = _compile_semantic_prompt(
                source,
                visual_context=visual_context,
                opening_blocking="",
                final_blocking=final_blocking,
                structured_dialogue_blocks=structured_dialogue_blocks,
            )
            if without_opening == semantic_prompt:
                if not _authored_opening_contains(source, opening_blocking):
                    raise H3ShotPlanError(
                        "H3 structured opening blocking conflicts with authored "
                        "OPENING BLOCKING"
                    )
                opening_blocking = _authored_opening_payload(source)
        for block_index in range(len(structured_dialogue_blocks) - 1, -1, -1):
            candidate_blocks = [
                block for index, block in enumerate(structured_dialogue_blocks)
                if index != block_index
            ]
            candidate_prompt, _ = _compile_semantic_prompt(
                source,
                visual_context=visual_context,
                opening_blocking=opening_blocking,
                final_blocking=final_blocking,
                structured_dialogue_blocks=candidate_blocks,
            )
            if candidate_prompt == semantic_prompt:
                structured_dialogue_blocks = candidate_blocks
        if replay_input is not None and (
            authored_shot_id != replay_input["authored_shot_id"]
            or visual_context != replay_input["visual_context"]
            or opening_blocking != replay_input["opening_blocking"]
            or final_blocking != replay_input["final_blocking"]
            or structured_dialogue_blocks
                != replay_input["structured_dialogue_blocks"]
        ):
            raise H3ShotPlanError(
                "H3 replay compiler inputs are not canonical"
            )
        rebuilt_semantic_prompt, source_dialogue = _compile_semantic_prompt(
            source,
            visual_context=visual_context,
            opening_blocking=opening_blocking,
            final_blocking=final_blocking,
            structured_dialogue_blocks=structured_dialogue_blocks,
        )
        if rebuilt_semantic_prompt != semantic_prompt:
            raise H3ShotPlanError(
                "H3 semantic compiler inputs are not canonical"
            )
        for semantic_occurrence_index, item in enumerate(source_dialogue):
            item.update(_semantic_dialogue_identity(
                item["exact_block"],
                source_index=source_index,
                semantic_occurrence_index=semantic_occurrence_index,
            ))
        localized_semantic_prompt, dialogue_tokens = _tag_dialogue_occurrences(
            semantic_prompt, source_dialogue,
        )
        authored_final_blocking = _extract_final_blocking(source)[1]
        executable_prompts, source_events = _compile_segment_local_prompts(
            localized_semantic_prompt,
            segment_positions=positions,
            published_frames=clip_published_frames,
            source_index=source_index,
            fps=fps_value,
            final_blocking=(
                final_blocking if source_is_canonical_context_ir else ""
            ),
            opening_blocking=(
                opening_blocking if source_is_canonical_context_ir else ""
            ),
            dialogue_occurrence_tokens=dialogue_tokens,
        )
        tagged_dialogue_occurrences: list[tuple[int, int]] = []
        for position, executable_prompt in zip(positions, executable_prompts):
            for match in _DIALOGUE_RE.finditer(executable_prompt):
                token_indices = [
                    index for index, token in enumerate(dialogue_tokens)
                    if token in match.group(0)
                ]
                if len(token_indices) != 1:
                    raise H3ShotPlanError(
                        "H3 segment-local dialogue occurrence identity is incomplete"
                    )
                tagged_dialogue_occurrences.append((
                    position, token_indices[0],
                ))
        executable_prompts = [
            _strip_dialogue_occurrence_tokens(prompt, dialogue_tokens)
            for prompt in executable_prompts
        ]
        for event in source_events:
            event["executable_payload"] = _strip_dialogue_occurrence_tokens(
                str(event.get("executable_payload") or ""), dialogue_tokens,
            )
        for position, executable_prompt in zip(positions, executable_prompts):
            prompts[position] = executable_prompt

        executable_dialogue: list[dict[str, Any]] = []
        seen_dialogue_indices: set[int] = set()
        for position, item_index in tagged_dialogue_occurrences:
            if (
                item_index in seen_dialogue_indices
                or item_index < 0
                or item_index >= len(source_dialogue)
            ):
                raise H3ShotPlanError(
                    "H3 segment-local dialogue ownership is incomplete"
                )
            seen_dialogue_indices.add(item_index)
            item = source_dialogue[item_index]
            item.pop("local_segment_index", None)
            item["authored_shot_id"] = authored_shot_id
            item["semantic_shot_index"] = source_index
            item["segment_index"] = position
            executable_dialogue.append(item)
        if len(seen_dialogue_indices) != len(source_dialogue):
            raise H3ShotPlanError(
                "H3 segment-local dialogue ownership has unclaimed blocks"
            )
        source_dialogue = executable_dialogue
        dialogue_manifest.extend(source_dialogue)
        source_published_offset = sum(
            clip_published_frames[:positions[0]]
        )
        for event_index, event in enumerate(source_events):
            event["event_id"] = f"{authored_shot_id}:event-{event_index + 1}"
            event["authored_shot_id"] = authored_shot_id
            event["semantic_shot_index"] = source_index
            event["owner_physical_segment_id"] = (
                f"{authored_shot_id}:segment-"
                f"{int(event['owner_physical_segment_index']) + 1}"
            )
            for continuation in event.get("continuation_slices") or []:
                continuation["physical_segment_id"] = (
                    f"{authored_shot_id}:segment-"
                    f"{int(continuation['physical_segment_index']) + 1}"
                )
                continuation["published_start_frame"] = (
                    source_published_offset
                    + int(continuation["source_start_frame"])
                )
                continuation["published_end_frame_exclusive"] = (
                    source_published_offset
                    + int(continuation["source_end_frame_exclusive"])
                )
            if event.get("source_start_frame") is not None:
                event["published_start_frame"] = (
                    source_published_offset + int(event["source_start_frame"])
                )
                event["published_end_frame_exclusive"] = (
                    source_published_offset
                    + int(event["source_end_frame_exclusive"])
                )
            else:
                event["published_start_frame"] = None
                event["published_end_frame_exclusive"] = None
        event_ownership.extend(source_events)

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
            "prompt_rewrite_for_physical_split": True,
            "physical_prompt_compiler_version": 2,
            "execution_slices": execution_slices,
            "reference_labels": _semantic_reference_labels(semantic_prompt),
            "structured_dialogue_blocks": structured_dialogue_blocks,
            "dialogue_manifest": [dict(item) for item in source_dialogue],
            "event_ownership": [dict(item) for item in source_events],
            "executable_prompt_sha256": [
                hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                for prompt in executable_prompts
            ],
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
            "published_end_frame_exclusive": (
                published_cursor + clip_published_frames[index]
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

    result = {
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
        "event_ownership": event_ownership,
        "dialogue_manifest": dialogue_manifest,
        "shots": native_shots,
    }
    if clip_boundaries:
        from services.h3_visual_continuity import apply_visual_carry_to_shot_plan

        apply_visual_carry_to_shot_plan(result)
        # Carry is part of the executable bytes sealed for recovery. Both
        # contract keys share source_contracts during plan construction.
        for shot, prompt in zip(native_shots, result["clip_prompts"]):
            shot["prompt"] = prompt
        for contract in source_contracts:
            contract["executable_prompt_sha256"] = [
                hashlib.sha256(result["clip_prompts"][index].encode("utf-8")).hexdigest()
                for index in contract["segment_indices"]
            ]
    seal_h3_shot_plan(result)
    return result


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
    "seal_h3_shot_plan",
    "validate_h3_shot_plan_seal",
]
