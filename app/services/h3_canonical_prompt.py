"""Deterministic H3 physical-record canonicalization shared by request planners."""

from __future__ import annotations

import math
import re


_H3_RECORD_PAYLOAD_RE = re.compile(
    r"^(?:\[Shot\s+\d+\]\s*)?"
    r"shot_name:\s*(?P<name>[^|\r\n]+?)\s*\|\s*"
    r"audiovisual_description:\s*(?P<description>[^|\r\n]+?)\s*"
    r"(?:\|\s*dialogue_and_vocalizations:\s*"
    r"(?P<vocals>[^\r\n]+?))?\s*$",
)


_H3_DIALOGUE_RE = re.compile(
    r"<d>\s*\[[^\]\r\n]+\]\s+.*?</d>", re.IGNORECASE | re.DOTALL,
)


def h3_time_token(seconds: float) -> str:
    """Use a stable frame-precise token for deterministic Director ranges."""
    return f"{max(0.0, float(seconds)):.3f}"


def h3_record_payload(text: str, number: int) -> tuple[str, str, str]:
    """Return canonical fields without interpreting creative subject matter."""
    source = str(text or "")
    # Normalize record layout around authored dialogue, never inside it.
    pieces = []
    cursor = 0
    for match in _H3_DIALOGUE_RE.finditer(source):
        pieces.append(re.sub(r"\s+", " ", source[cursor:match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(re.sub(r"\s+", " ", source[cursor:]))
    compact = "".join(pieces).strip()
    compact = re.sub(r"^\[Shot\s+\d+\]\s*", "", compact)
    exact = _H3_RECORD_PAYLOAD_RE.fullmatch(compact)
    if exact:
        return (
            exact.group("name").strip(),
            exact.group("description").strip(),
            (exact.group("vocals") or "none").strip(),
        )
    if "|" in compact or re.search(
        r"\b(?:shot_name|audiovisual_description|"
        r"dialogue_and_vocalizations)\s*:", compact,
    ):
        raise ValueError(
            "Director H3 record labels are malformed; exact mapping is unavailable"
        )
    dialogue = _H3_DIALOGUE_RE.findall(compact)
    description = _H3_DIALOGUE_RE.sub(" ", compact)
    description = re.sub(r"\s+", " ", description).strip()
    if not description:
        description = "Dialogue"
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", description)
    shot_name = " ".join(words[:8]) or f"Shot {number}"
    return shot_name, description, " ".join(dialogue) if dialogue else "none"


def canonicalize_h3_prompt(
    prompt: str,
    *,
    duration_seconds: float,
    events: list[dict] | None = None,
    mode: str | None = None,
) -> str:
    """Map one Director source/segment to strict physical Context-IR records."""
    from services.director.h3_dialogue import (
        _H3_NO_SUBJECT_DEFINITIONS,
        _parse_h3_subject_definitions,
        _extract_h3_fields,
        validate_h3_context_ir_records,
    )
    from shared.utils.prompt_parser import parse_global_timeline_prompt

    if events is not None and (
        not isinstance(events, list)
        or any(not isinstance(event, dict)
               or not isinstance(event.get("text"), str)
               or not event["text"].strip() for event in events)
    ):
        raise ValueError("H3 prompt events must contain nonblank text records")
    if not isinstance(prompt, str) or (not prompt.strip() and not events):
        raise ValueError("H3 prompt must contain text")
    if isinstance(duration_seconds, bool):
        raise ValueError("H3 prompt duration must be a positive finite number")
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("H3 prompt duration must be a positive finite number") from error
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("H3 prompt duration must be a positive finite number")

    if any("\n" in match.group(0) or "\r" in match.group(0)
           for match in _H3_DIALOGUE_RE.finditer(str(prompt or ""))):
        raise ValueError(
            "Director H3 physical shot records require each dialogue block on one line; "
            "preserve the authored wording when preparing the shot record"
        )
    source = str(prompt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    requested_mode = str(mode or "").strip().casefold()
    if requested_mode not in {"", "t2va", "ref2va"}:
        raise ValueError("Director H3 Context-IR mode is unsupported")
    if not requested_mode:
        requested_mode = (
            "ref2va"
            if re.search(
                r"(?mi)^\s*(?:summary|retention_analysis|detailed_description)\s*:",
                source,
            )
            else "t2va"
        )
    # The shared splitter may carry FINAL BLOCKING onto its own line between
    # a record's visual and vocal fields. Rejoin only that known deterministic
    # shape before parsing; both literal payloads remain unchanged.
    source = re.sub(
        r"(?m)^(?P<record>\[[^\r\n]+\|\s*"
        r"audiovisual_description:[^\r\n|]+)\s*$\n"
        r"FINAL BLOCKING:\s*(?P<blocking>[^\r\n|]+?)\s*\|\s*"
        r"dialogue_and_vocalizations:\s*(?P<vocals>[^\r\n]+)\s*$",
        lambda match: (
            f"{match.group('record')} | dialogue_and_vocalizations: "
            f"{match.group('vocals').strip()}\n"
            f"FINAL BLOCKING: {match.group('blocking').strip()}"
        ),
        source,
    )
    existing_errors = validate_h3_context_ir_records(
        source, mode=requested_mode, duration_seconds=duration,
    )
    if not existing_errors:
        return source
    if requested_mode == "ref2va":
        # Ref2VA has its own six-field schema. Mapping an invalid reference
        # prompt through the Base compiler would erase retention/provenance
        # semantics, so fail closed instead of manufacturing Base fields.
        raise ValueError(
            "Director H3 Ref2VA prompt validation failed: "
            + "; ".join(existing_errors)
        )

    source_fields = _extract_h3_fields(source)
    raw_subject_definitions = str(
        source_fields.get("subject_definitions") or ""
    ).strip()
    # Legacy Director sources sometimes put bare timeline records between the
    # subject namespace and the first Context-IR field. They are not entity
    # definitions even though the generic field extractor includes them.
    subject_definition_lines: list[str] = []
    for raw_line in raw_subject_definitions.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\[\s*(?:Shot|Scene)\s+\d+", line, re.IGNORECASE):
            break
        if parse_global_timeline_prompt(line)[1]:
            break
        subject_definition_lines.append(line)
    subject_definitions = "\n".join(subject_definition_lines).strip()
    first_field = re.search(
        r"(?mi)^\s*integrated_multimodal_description\s*:", source,
    )
    if first_field and source[:first_field.start()].strip():
        prefix = source[:first_field.start()].strip()
        subject_prefix = re.fullmatch(
            r"(?is)subject_definitions\s*:(?P<body>.*)", prefix,
        )
        prefix_body = (
            " ".join(subject_prefix.group("body").split())
            if subject_prefix is not None else ""
        )
        definitions_compact = " ".join(subject_definitions.split())
        prefix_suffix = (
            prefix_body[len(definitions_compact):].strip()
            if definitions_compact
            and prefix_body.startswith(definitions_compact)
            else prefix_body
        )
        prefix_suffix_is_timeline = not prefix_suffix or bool(
            re.match(r"^(?:\[?\s*(?:Shot|Scene)\s+\d+|\d+(?:\.\d+)?\s*s?)\b", prefix_suffix, re.IGNORECASE)
        )
        if subject_prefix is None or (
            prefix_body != definitions_compact
            and not (definitions_compact and prefix_suffix_is_timeline)
        ):
            raise ValueError(
                "Director H3 prompt contains wrapper text; exact mapping is unavailable"
            )

    soundscape = "N/A"
    music = "N/A"
    definition_lines = {
        " ".join(line.split()).casefold()
        for line in subject_definitions.splitlines()
        if line.strip()
    }
    context_lines: list[str] = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        sound_match = re.fullmatch(
            r"overall_soundscape\s*:\s*(.+)", line, re.IGNORECASE,
        )
        music_match = re.fullmatch(
            r"non_diegetic_music\s*:\s*(.+)", line, re.IGNORECASE,
        )
        if sound_match:
            soundscape = sound_match.group(1).strip()
            continue
        if music_match:
            music = music_match.group(1).strip()
            continue
        if re.match(r"subject_definitions\s*:", line, re.IGNORECASE):
            continue
        if " ".join(line.split()).casefold() in definition_lines:
            continue
        if re.fullmatch(
            r"integrated_multimodal_description\s*:\s*", line,
            re.IGNORECASE,
        ):
            continue
        if parse_global_timeline_prompt(line)[1]:
            continue
        legacy_context = re.match(
            r"^(?:subject_definitions|cast|setting|location|environment|"
            r"visual_style|lighting)\s*:\s*(.*)$",
            line,
            re.IGNORECASE,
        )
        if not first_field and legacy_context:
            if legacy_context.group(1).strip():
                context_lines.append(legacy_context.group(1).strip())
            continue
        if first_field and re.match(
            r"^[A-Za-z][A-Za-z0-9_ ]{1,64}\s*:", line,
        ) and not re.match(
            r"^(?:VISUAL CONTINUITY|OPENING BLOCKING|FINAL BLOCKING)\s*:",
            line,
            re.IGNORECASE,
        ):
            raise ValueError(
                "Director H3 prompt has an unexpected field; exact mapping is unavailable"
            )
        context_lines.append(line)

    parsed_globals, parsed_events = parse_global_timeline_prompt(source)
    mapped_events = list(events if events is not None else parsed_events)
    # Native-shot partitioning can retain both the source timeline line and
    # the already structured Context-IR record for the same range. Prefer the
    # structured form when present so rehydration does not duplicate records.
    structured_events = [
        item for item in mapped_events
        if re.search(
            r"\bshot_name\s*:|\baudiovisual_description\s*:",
            str(item.get("text") or ""),
            re.IGNORECASE,
        )
    ]
    if structured_events:
        mapped_events = structured_events
    if not mapped_events:
        body = " ".join(context_lines).strip() or source
        mapped_events = [{
            "kind": "range",
            "start": 0.0,
            "end": duration,
            "text": body,
            "order": 0,
        }]
        context_lines = []
    else:
        event_texts = {
            re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
            for item in mapped_events
        }
        globals_from_parser = {
            re.sub(r"\s+", " ", str(line or "")).strip()
            for line in parsed_globals
        }
        context_lines = [
            line for line in context_lines
            if re.sub(r"\s+", " ", line).strip() not in event_texts
            and re.sub(r"\s+", " ", line).strip() not in globals_from_parser
        ] + [
            str(line).strip() for line in parsed_globals
            if str(line).strip()
            and " ".join(str(line).split()).casefold() not in definition_lines
            and not re.match(
                r"^(?:integrated_multimodal_description|overall_soundscape|"
                r"non_diegetic_music|subject_definitions|cast|setting|"
                r"location|environment|visual_style|lighting)\s*:",
                str(line).strip(),
                re.IGNORECASE,
            )
        ]

    ordered = sorted(mapped_events, key=lambda item: int(item.get("order", 0)))
    records: list[str] = []
    ranges: list[tuple[float, float]] = []
    for index, event in enumerate(ordered):
        start = float(event.get("start", 0.0))
        end = float(event.get("end", start))
        if event.get("kind") == "shot" or end <= start:
            end = (
                float(ordered[index + 1].get("start", duration))
                if index + 1 < len(ordered) else duration
            )
        if start < 0 or end <= start or end > duration + 0.01:
            raise ValueError(
                "Director H3 timeline cannot be mapped to exact positive ranges"
            )
        ranges.append((start, min(end, duration)))

    if (
        not ranges
        or abs(ranges[0][0]) > 1e-6
        or abs(ranges[-1][1] - duration) > 0.01
        or any(abs(left[1] - right[0]) > 1e-6 for left, right in zip(ranges, ranges[1:]))
    ):
        raise ValueError(
            "Director H3 timeline is not contiguous; exact mapping is unavailable"
        )

    opening = " ".join(
        line for line in context_lines
        if not re.match(r"^FINAL BLOCKING\s*:", line, re.IGNORECASE)
    ).strip()
    closing = " ".join(
        line for line in context_lines
        if re.match(r"^FINAL BLOCKING\s*:", line, re.IGNORECASE)
    ).strip()
    entity_definitions = _parse_h3_subject_definitions(subject_definitions)

    def strip_repeated_entity_definition(value: str) -> str:
        result = str(value or "")
        for entry in entity_definitions:
            description = " ".join(str(entry.get("description") or "").split())
            if len(description.split()) < 4:
                continue
            label = re.escape(str(entry.get("label") or ""))
            result = re.sub(
                rf"({label})\s*[:\-–—]?\s*{re.escape(description)}",
                r"\1",
                result,
                count=1,
                flags=re.IGNORECASE,
            )
        return result.strip()

    for index, (event, (start, end)) in enumerate(zip(ordered, ranges), start=1):
        name, description, vocals = h3_record_payload(
            str(event.get("text") or ""), index,
        )
        description = strip_repeated_entity_definition(description)
        if index == 1 and opening:
            description = f"{opening} {description}".strip()
        if index == len(ordered) and closing:
            description = f"{description} {closing}".strip()
        if "|" in description or "|" in vocals:
            raise ValueError(
                "Director H3 record payload contains an ambiguous separator"
            )
        records.append(
            f"[Shot {index}] [{h3_time_token(start)}s-"
            f"{h3_time_token(end)}s] shot_name: {name} | "
            f"audiovisual_description: {description} | "
            f"dialogue_and_vocalizations: {vocals}"
        )

    result = (
        f"subject_definitions: {subject_definitions or _H3_NO_SUBJECT_DEFINITIONS}\n"
        "\nintegrated_multimodal_description:\n"
        + "\n".join(records)
        + f"\noverall_soundscape: {soundscape}"
        + f"\nnon_diegetic_music: {music}"
    )
    errors = validate_h3_context_ir_records(
        result, mode="t2va", duration_seconds=duration,
    )
    if errors:
        raise ValueError(
            "Director H3 canonical prompt validation failed: " + "; ".join(errors)
        )
    return result
