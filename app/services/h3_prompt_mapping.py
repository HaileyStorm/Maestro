"""Pure H3 Base/Ref2VA prompt mapping with deterministic provenance.

The caller owns media admission and supplies an already ordered structural
reference binding.  This module reads no files, mutates no plans, and grants no
asset authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
from typing import Any

from models.minimax_h3.reference_manifest import (
    MINIMAX_H3_MAX_REFERENCE_AUDIOS,
    MINIMAX_H3_MAX_REFERENCE_IMAGES,
    MINIMAX_H3_MAX_REFERENCE_VIDEOS,
    MINIMAX_H3_MAX_REFERENCES,
)
from services.director.h3_dialogue import (
    _H3_CANONICAL_RECORD_RE,
    validate_h3_context_ir_records,
    validate_h3_prompt_contract,
)
from services.h3_canonical_prompt import canonicalize_h3_prompt


BASE_FIELDS = (
    "subject_definitions",
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
REF2VA_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
REF_ONLY_FIELDS = ("summary", "retention_analysis", "detailed_description")
ALL_FIELDS = tuple(dict.fromkeys((*BASE_FIELDS, *REF2VA_FIELDS)))

_FIELD_RE = re.compile(
    r"(?mi)^[ \t]*(?P<name>"
    + "|".join(re.escape(field) for field in ALL_FIELDS)
    + r")[ \t]*:[ \t]?"
)
_DIALOGUE_RE = re.compile(r"<d>.*?</d>", re.IGNORECASE | re.DOTALL)
_IMAGE_INTENTS = frozenset({"identity", "scene", "style", "composition"})
_AUDIO_INTENTS = frozenset({"voice", "style", "drive"})
_REFERENCE_ORDER = {"image": 0, "video": 1, "audio": 2}

_RECORD_VERSION = 1
_RECIPE_VERSION = "recipe-v1"
_RECORD_FIELDS = frozenset({
    "version",
    "recipe_version",
    "source_prompt",
    "source_sha256",
    "source_schema",
    "source_origin",
    "origin",
    "target_schema",
    "duration_seconds",
    "reference_manifest",
    "binding_sha256",
    "mapped_prompt",
    "mapped_sha256",
})
_PRESERVE_REFERENCES = object()


class H3PromptMappingError(ValueError):
    """A prompt or binding cannot be mapped deterministically."""


class H3PromptRepresentabilityError(H3PromptMappingError):
    """A source field has no exact owner in the requested target schema."""


class H3PromptValidationError(H3PromptMappingError):
    """A compiled or mapped target failed the complete shared contract."""


def _positive_duration(value: object) -> float:
    if isinstance(value, bool):
        raise H3PromptMappingError("duration_seconds must be a positive finite number")
    try:
        duration = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise H3PromptMappingError(
            "duration_seconds must be a positive finite number"
        ) from error
    if not math.isfinite(duration) or duration <= 0:
        raise H3PromptMappingError("duration_seconds must be a positive finite number")
    return duration


def _target_schema(value: object) -> str:
    target = str(value or "").strip().casefold()
    if target not in {"base", "ref2va"}:
        raise H3PromptMappingError("target_schema must be 'base' or 'ref2va'")
    return target


def _field_matches(text: str) -> list[re.Match[str]]:
    dialogue_spans = [match.span() for match in _DIALOGUE_RE.finditer(text)]
    return [
        match
        for match in _FIELD_RE.finditer(text)
        if not any(start <= match.start() < end for start, end in dialogue_spans)
    ]


def _extract_fields_exact(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    matches = _field_matches(text)
    for index, match in enumerate(matches):
        name = match.group("name").casefold()
        if name in fields:
            raise H3PromptMappingError(
                f"source has more than one top-level {name} field"
            )
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        fields[name] = text[match.end():end].strip("\r\n")
    return fields


def _source_family(fields: Mapping[str, str]) -> str:
    has_base_visual = "integrated_multimodal_description" in fields
    present_ref_fields = [field for field in REF_ONLY_FIELDS if field in fields]
    if has_base_visual and present_ref_fields:
        raise H3PromptRepresentabilityError(
            "source mixes integrated_multimodal_description with Ref2VA fields: "
            + ", ".join(present_ref_fields)
        )
    if present_ref_fields:
        return "ref2va"
    return "base" if fields else "freeform"


def _dialogue_literals(text: str) -> list[str]:
    return [match.group(0) for match in _DIALOGUE_RE.finditer(text)]


def _source_literals(text: str, fields: Mapping[str, str]) -> list[str]:
    if fields:
        matches = _field_matches(text)
        prefix = text[:matches[0].start()].strip("\r\n") if matches else text
        return ([prefix] if prefix else []) + [value for value in fields.values() if value]
    literals: list[str] = []
    cursor = 0
    for match in _DIALOGUE_RE.finditer(text):
        prose = text[cursor:match.start()].strip("\r\n")
        if prose:
            literals.append(prose)
        literals.append(match.group(0))
        cursor = match.end()
    prose = text[cursor:].strip("\r\n")
    if prose:
        literals.append(prose)
    return literals


def _require_literal_conservation(
    source: str,
    mapped: str,
    fields: Mapping[str, str],
) -> None:
    if _dialogue_literals(mapped) != _dialogue_literals(source):
        raise H3PromptRepresentabilityError(
            "mapping did not preserve exact dialogue bytes, order, and count"
        )
    for literal in _source_literals(source, fields):
        if mapped.count(literal) < source.count(literal):
            raise H3PromptRepresentabilityError(
                "mapping did not preserve an exact authored source literal"
            )


def _strict_optional_string(item: Mapping[str, Any], key: str, index: int) -> None:
    if key not in item:
        return
    value = item[key]
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise H3PromptMappingError(
            f"reference_manifest item {index} {key} must be a nonblank exact string"
        )


def _validated_reference_binding(reference_manifest: object) -> list[dict[str, Any]]:
    """Validate structural metadata without reading or authorizing any asset."""

    if not isinstance(reference_manifest, list):
        raise H3PromptMappingError(
            "Ref2VA mapping needs an explicit reference_manifest list"
        )
    reference_manifest = _snapshot(reference_manifest)
    if len(reference_manifest) > MINIMAX_H3_MAX_REFERENCES:
        raise H3PromptMappingError(
            f"reference_manifest accepts at most {MINIMAX_H3_MAX_REFERENCES} items"
        )

    normalized: list[dict[str, Any]] = []
    counts = {"image": 0, "video": 0, "audio": 0}
    last_order = -1
    paired_audio_count = 0
    standalone_audio_count = 0
    for index, raw in enumerate(reference_manifest, start=1):
        if type(raw) is not dict:
            raise H3PromptMappingError(
                f"reference_manifest item {index} must be an object"
            )
        kind = raw.get("type")
        if not isinstance(kind, str) or kind not in _REFERENCE_ORDER:
            raise H3PromptMappingError(
                f"reference_manifest item {index} type must be image, video, or audio"
            )
        if _REFERENCE_ORDER[kind] < last_order:
            raise H3PromptMappingError(
                "reference_manifest must be ordered as images, videos, then audio"
            )
        last_order = _REFERENCE_ORDER[kind]
        path = raw.get("path")
        if not isinstance(path, str) or not path.strip() or path != path.strip():
            raise H3PromptMappingError(
                f"reference_manifest item {index} path must be a nonblank exact string"
            )
        _strict_optional_string(raw, "role", index)
        _strict_optional_string(raw, "audio_path", index)
        _strict_optional_string(raw, "source_key", index)
        _strict_optional_string(raw, "audio_source_key", index)
        if "source_index" in raw and (
            type(raw["source_index"]) is not int or raw["source_index"] < 0
        ):
            raise H3PromptMappingError(
                f"reference_manifest item {index} source_index must be a nonnegative integer"
            )
        for key in ("include_audio", "has_audio"):
            if key in raw and type(raw[key]) is not bool:
                raise H3PromptMappingError(
                    f"reference_manifest item {index} {key} must be boolean"
                )

        if kind == "image":
            if set(raw) & {
                "audio_intent", "include_audio", "has_audio", "audio_path",
                "audio_source_key",
            }:
                raise H3PromptMappingError(
                    f"reference_manifest item {index} has fields incompatible with image"
                )
            if "image_intent" in raw and (not isinstance(raw["image_intent"], str) or raw["image_intent"] not in _IMAGE_INTENTS):
                raise H3PromptMappingError(
                    f"reference_manifest item {index} has unsupported image_intent"
                )
        elif kind == "video":
            if set(raw) & {"image_intent", "audio_intent"}:
                raise H3PromptMappingError(
                    f"reference_manifest item {index} has fields incompatible with video"
                )
            paired = bool(
                raw.get("include_audio", True)
                and (raw.get("has_audio") or raw.get("audio_path"))
            )
            paired_audio_count += int(paired)
        else:
            standalone_audio_count += 1
            if set(raw) & {
                "image_intent", "include_audio", "has_audio", "audio_path",
                "audio_source_key",
            }:
                raise H3PromptMappingError(
                    f"reference_manifest item {index} has fields incompatible with audio"
                )
            if "audio_intent" in raw and (not isinstance(raw["audio_intent"], str) or raw["audio_intent"] not in _AUDIO_INTENTS):
                raise H3PromptMappingError(
                    f"reference_manifest item {index} has unsupported audio_intent"
                )
            if raw.get("audio_intent") == "drive":
                raise H3PromptMappingError(
                    "drive audio mapping is unavailable until runtime binding supports it"
                )
        counts[kind] += 1
        normalized.append(raw)

    visual_count = counts["image"] + counts["video"]
    effective_audio_count = paired_audio_count + standalone_audio_count
    if effective_audio_count > visual_count:
        raise H3PromptMappingError(
            "reference_manifest audio count cannot exceed visual reference count"
        )
    if paired_audio_count and standalone_audio_count:
        raise H3PromptMappingError(
            "paired video audio and standalone audio cannot share this runtime binding"
        )
    limits = (
        ("image", counts["image"], MINIMAX_H3_MAX_REFERENCE_IMAGES),
        ("video", counts["video"], MINIMAX_H3_MAX_REFERENCE_VIDEOS),
        ("audio", effective_audio_count, MINIMAX_H3_MAX_REFERENCE_AUDIOS),
    )
    for kind, count, limit in limits:
        if count > limit:
            raise H3PromptMappingError(
                f"reference_manifest accepts at most {limit} {kind} references"
            )
    return normalized


def _manifest_for_target(target: str, reference_manifest: object) -> list[dict[str, Any]] | None:
    if target == "ref2va":
        return _validated_reference_binding(reference_manifest)
    if reference_manifest not in (None, []):
        raise H3PromptMappingError("Base mapping cannot bind semantic references")
    return None


def _reference_context(
    manifest: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    from services.h3_reference_text import reference_relationships

    definitions, retention, _driving, _subjects, _details, tasks = reference_relationships(manifest)
    return definitions, retention, tasks


def _validate_target(
    prompt: str,
    *,
    target_schema: str,
    duration_seconds: float,
    reference_manifest: Sequence[Mapping[str, Any]] | None,
) -> None:
    mode = "ref2va" if target_schema == "ref2va" else "t2va"
    official_errors = validate_h3_prompt_contract(
        prompt,
        [],
        mode=mode,
        references=reference_manifest,
        duration_seconds=duration_seconds,
    )
    record_errors = validate_h3_context_ir_records(
        prompt,
        mode=mode,
        duration_seconds=duration_seconds,
    )
    errors = list(dict.fromkeys((*official_errors, *record_errors)))
    if errors:
        raise H3PromptValidationError(
            f"{target_schema} target validation failed: " + "; ".join(errors)
        )


def _fold_planner_visual_carry(prompt: str, duration_seconds: float) -> str | None:
    """Move the exact known planner carry into the first visual record."""

    from services.h3_visual_continuity import (
        H3_SEAM_LOCK_KEYS,
        SAME_SOURCE_VISUAL_CARRY_LINE,
        SEGMENT_SEAM_LOCKS_HEADER,
        strip_opening_visual_carry,
    )

    body = strip_opening_visual_carry(prompt)
    if body == prompt:
        return None
    if not body or not prompt.endswith(body):
        raise H3PromptRepresentabilityError(
            "planner visual carry has an unsupported body shape"
        )
    prefix = prompt[:-len(body)].rstrip("\n")
    prefix_lines = prefix.split("\n")
    if (
        len(prefix_lines) != 2
        or prefix_lines[0] != SAME_SOURCE_VISUAL_CARRY_LINE
        or not prefix_lines[1].startswith(f"{SEGMENT_SEAM_LOCKS_HEADER} ")
        or not prefix_lines[1][len(SEGMENT_SEAM_LOCKS_HEADER):].strip()
    ):
        raise H3PromptRepresentabilityError(
            "planner visual carry has an unsupported prefix shape"
        )
    carry_payload = " ".join(prefix_lines)
    locks = prefix_lines[1][len(SEGMENT_SEAM_LOCKS_HEADER) + 1:]
    keys = list(re.finditer(r"(?:^|; )(?P<key>[A-Za-z][A-Za-z-]*)=", locks))
    if (
        [match.group("key") for match in keys] != list(H3_SEAM_LOCK_KEYS)
        or not keys or keys[0].start() != 0
        or any(not locks[match.end():keys[index + 1].start() if index + 1 < len(keys) else len(locks)].strip()
               for index, match in enumerate(keys))
        or re.search(r"[\x00-\x1f\x7f]", locks)
    ):
        raise H3PromptRepresentabilityError(
            "planner visual carry has unsupported seam locks"
        )
    if "|" in carry_payload or "\r" in carry_payload:
        raise H3PromptRepresentabilityError(
            "planner visual carry cannot enter a canonical visual record"
        )

    _validate_target(
        body,
        target_schema="base",
        duration_seconds=duration_seconds,
        reference_manifest=None,
    )
    fields = _extract_fields_exact(body)
    if tuple(fields) != BASE_FIELDS:
        raise H3PromptRepresentabilityError(
            "planner visual carry requires an exact canonical Base body"
        )
    matches = _field_matches(body)
    visual_position = next((
        index for index, match in enumerate(matches)
        if match.group("name").casefold()
            == "integrated_multimodal_description"
    ), None)
    if visual_position is None:
        raise H3PromptRepresentabilityError(
            "planner visual carry requires an owned visual payload"
        )
    value_start = matches[visual_position].end()
    value_end = (
        matches[visual_position + 1].start()
        if visual_position + 1 < len(matches) else len(body)
    )
    raw_visual = body[value_start:value_end]
    visual = raw_visual.strip("\r\n")
    lines = visual.splitlines()
    first_index = next((
        index for index, line in enumerate(lines) if line.strip()
    ), None)
    if first_index is None or lines[first_index] != lines[first_index].strip():
        raise H3PromptRepresentabilityError(
            "planner visual carry requires canonical physical records"
        )
    record = _H3_CANONICAL_RECORD_RE.fullmatch(lines[first_index])
    if record is None:
        raise H3PromptRepresentabilityError(
            "planner visual carry requires canonical physical records"
        )
    start, end = record.span("description")
    original_line = lines[first_index]
    lines[first_index] = (
        original_line[:start]
        + carry_payload
        + " "
        + original_line[start:end]
        + original_line[end:]
    )
    augmented_visual = "\n".join(lines)
    leading = raw_visual[:len(raw_visual) - len(raw_visual.lstrip("\r\n"))]
    trailing = raw_visual[len(raw_visual.rstrip("\r\n")):]
    augmented = (
        body[:value_start]
        + leading
        + augmented_visual
        + trailing
        + body[value_end:]
    )
    if _dialogue_literals(augmented) != _dialogue_literals(body):
        raise H3PromptRepresentabilityError(
            "planner visual carry changed exact dialogue bytes"
        )
    _validate_target(
        augmented,
        target_schema="base",
        duration_seconds=duration_seconds,
        reference_manifest=None,
    )
    return augmented


def _base_to_ref2va(
    base: str,
    fields: Mapping[str, str],
    manifest: Sequence[Mapping[str, Any]],
) -> str:
    definitions, retention, task_types = _reference_context(manifest)
    subject_definitions = fields["subject_definitions"]
    if definitions:
        subject_definitions = f"{subject_definitions}\n" + "\n".join(definitions)
    if retention:
        retention_text = " ".join(retention)
        summary_text = (
            f"[{' + '.join(task_types)}] Canonical shot records mapped to "
            "the supplied reference binding."
        )
    else:
        retention_text = (
            "No attached media references are bound; preserve the authored Base "
            "prompt without reference-derived claims."
        )
        summary_text = (
            "[reference generation] Canonical shot records mapped with no "
            "attached media references."
        )
    mapped = (
        f"subject_definitions: {subject_definitions}\n\n"
        f"summary: {summary_text}\n\n"
        f"retention_analysis: {retention_text}\n\n"
        f"detailed_description: {fields['integrated_multimodal_description']}\n\n"
        f"overall_soundscape: {fields['overall_soundscape']}\n\n"
        f"non_diegetic_music: {fields['non_diegetic_music']}"
    )
    _require_literal_conservation(base, mapped, fields)
    return mapped


def _map_h3_prompt_schema(
    prompt: str,
    target_schema: str,
    *,
    duration_seconds: object,
    reference_manifest: object = None,
) -> str:
    """Internal mapper; authored Ref ingress is checked by its record owner."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise H3PromptMappingError("prompt must be a non-empty string")
    target = _target_schema(target_schema)
    duration = _positive_duration(duration_seconds)
    manifest = _manifest_for_target(target, reference_manifest)

    carried_base = _fold_planner_visual_carry(prompt, duration)
    source_fields = _extract_fields_exact(prompt)
    family = _source_family(source_fields)
    if family == "ref2va":
        if target == "base":
            owned = [field for field in REF_ONLY_FIELDS if source_fields.get(field)]
            raise H3PromptRepresentabilityError(
                "Ref2VA-to-Base is incomplete: "
                + (", ".join(owned) if owned else "Ref2VA fields")
                + " has no exact Base field owner"
            )
        _validate_target(
            prompt,
            target_schema=target,
            duration_seconds=duration,
            reference_manifest=manifest,
        )
        return prompt

    base = carried_base if carried_base is not None else prompt
    try:
        _validate_target(
            base,
            target_schema="base",
            duration_seconds=duration,
            reference_manifest=None,
        )
    except H3PromptValidationError:
        try:
            base = canonicalize_h3_prompt(
                prompt,
                duration_seconds=duration,
                mode="t2va",
            )
        except Exception as error:
            raise H3PromptRepresentabilityError(
                f"Base canonicalization failed: {error}"
            ) from error
        if not isinstance(base, str) or not base:
            raise H3PromptRepresentabilityError(
                "Base canonicalization returned no prompt"
            )
        _require_literal_conservation(prompt, base, source_fields)
        _validate_target(
            base,
            target_schema="base",
            duration_seconds=duration,
            reference_manifest=None,
        )

    if target == "base":
        return base
    base_fields = _extract_fields_exact(base)
    if tuple(base_fields) != BASE_FIELDS:
        raise H3PromptValidationError(
            "validated Base target could not be read in exact field order"
        )
    mapped = _base_to_ref2va(base, base_fields, manifest or [])
    _validate_target(
        mapped,
        target_schema="ref2va",
        duration_seconds=duration,
        reference_manifest=manifest,
    )
    return mapped


def source_prompt_schema(prompt: str, *, strict: bool = True) -> str:
    """Read authored field structure without assigning reference semantics."""
    if not isinstance(prompt, str) or not prompt.strip():
        if not strict:
            return "opaque"
        raise H3PromptMappingError("prompt must be a non-empty string")
    try:
        return _source_family(_extract_fields_exact(prompt))
    except H3PromptMappingError:
        if not strict:
            return "opaque"
        raise


def map_h3_prompt_schema(
    prompt: str, target_schema: str, *, duration_seconds: object,
    reference_manifest: object = None,
) -> str:
    """Map Base/freeform source; Ref source needs explicit authoring or lineage."""
    if isinstance(prompt, str) and _source_family(_extract_fields_exact(prompt)) == "ref2va":
        raise H3PromptMappingError(
            "Ref2VA source needs an explicit authoring record or retained mapping lineage"
        )
    return _map_h3_prompt_schema(
        prompt, target_schema, duration_seconds=duration_seconds,
        reference_manifest=reference_manifest,
    )


def _digest(text: str) -> str:
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as error:
        raise H3PromptMappingError("mapping text must be valid UTF-8 data") from error


def _json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (RecursionError, TypeError, ValueError, OverflowError) as error:
        raise H3PromptMappingError("mapping metadata must be finite JSON data") from error


def _snapshot(value: object) -> Any:
    return json.loads(_json(value))


def _create_mapping_record(
    source_prompt: str,
    target_schema: str,
    *,
    duration_seconds: object,
    reference_manifest: object,
    source_origin: str,
) -> dict[str, Any]:
    if not isinstance(source_origin, str) or source_origin not in {"base_source", "explicit_authoring"}:
        raise H3PromptMappingError("unsupported mapping source origin")
    if not isinstance(source_prompt, str) or not source_prompt.strip():
        raise H3PromptMappingError("prompt must be a non-empty string")
    source_schema = _source_family(_extract_fields_exact(source_prompt))
    if source_schema == "ref2va" and source_origin != "explicit_authoring":
        raise H3PromptMappingError("bare Ref2VA input has no verified mapping lineage")
    target = _target_schema(target_schema)
    duration = _positive_duration(duration_seconds)
    manifest = _snapshot(_manifest_for_target(target, reference_manifest))
    mapped = _map_h3_prompt_schema(
        source_prompt,
        target,
        duration_seconds=duration,
        reference_manifest=manifest,
    )
    binding = {
        "target_schema": target,
        "duration_seconds": duration,
        "reference_manifest": manifest,
    }
    return {
        "version": _RECORD_VERSION,
        "recipe_version": _RECIPE_VERSION,
        "source_prompt": source_prompt,
        "source_sha256": _digest(source_prompt),
        "source_schema": source_schema,
        "source_origin": source_origin,
        "origin": "authored_passthrough" if mapped == source_prompt else "generated_mapping",
        **binding,
        "binding_sha256": _digest(_json(binding)),
        "mapped_prompt": mapped,
        "mapped_sha256": _digest(mapped),
    }


def create_mapping_record(
    source_prompt: str,
    target_schema: str,
    *,
    duration_seconds: object,
    reference_manifest: object = None,
) -> dict[str, Any]:
    return _create_mapping_record(
        source_prompt,
        target_schema,
        duration_seconds=duration_seconds,
        reference_manifest=reference_manifest,
        source_origin="base_source",
    )


def create_authored_mapping_record(
    source_prompt: str,
    target_schema: str,
    *,
    duration_seconds: object,
    reference_manifest: object = None,
) -> dict[str, Any]:
    """Record explicit authoring ingress; this does not infer recovery authority."""

    return _create_mapping_record(
        source_prompt,
        target_schema,
        duration_seconds=duration_seconds,
        reference_manifest=reference_manifest,
        source_origin="explicit_authoring",
    )


def _validate_record_consistency(record: object) -> dict[str, Any]:
    if type(record) is not dict or set(record) != _RECORD_FIELDS:
        raise H3PromptMappingError("unsupported or incomplete prompt mapping record")
    snapshot = _snapshot(record)
    if type(snapshot.get("version")) is not int or snapshot["version"] != _RECORD_VERSION:
        raise H3PromptMappingError("unsupported or incomplete prompt mapping record")
    if snapshot.get("recipe_version") != _RECIPE_VERSION:
        raise H3PromptMappingError(
            "unsupported mapping recipe; preserve historical exact replay"
        )
    expected = _create_mapping_record(
        snapshot["source_prompt"],
        snapshot["target_schema"],
        duration_seconds=snapshot["duration_seconds"],
        reference_manifest=snapshot["reference_manifest"],
        source_origin=snapshot["source_origin"],
    )
    if _json(snapshot) != _json(expected):
        raise H3PromptMappingError(
            "prompt mapping record does not reproduce its source and binding"
        )
    return _snapshot(expected)


def validate_mapping_record(
    record: object,
    *,
    current_reference_manifest: object,
    current_target_schema: str,
    duration_seconds: object,
) -> dict[str, Any]:
    expected = _validate_record_consistency(record)
    if _target_schema(current_target_schema) != expected["target_schema"]:
        raise H3PromptMappingError("prompt mapping does not match the current target schema")
    current_manifest = _manifest_for_target(
        expected["target_schema"], current_reference_manifest
    )
    if (
        _json(current_manifest) != _json(expected["reference_manifest"])
        or _positive_duration(duration_seconds) != expected["duration_seconds"]
    ):
        raise H3PromptMappingError(
            "prompt mapping does not match current references or duration"
        )
    return _snapshot(expected)


def rebuild_mapping_record(
    record: object,
    *,
    target_schema: str | None = None,
    reference_manifest: object = _PRESERVE_REFERENCES,
) -> dict[str, Any]:
    """Regenerate from original source, optionally changing the target binding."""

    previous = _validate_record_consistency(record)
    target = (
        previous["target_schema"]
        if target_schema is None
        else _target_schema(target_schema)
    )
    if reference_manifest is _PRESERVE_REFERENCES:
        manifest = (
            previous["reference_manifest"]
            if target == "ref2va" and previous["target_schema"] == "ref2va"
            else None
        )
    else:
        manifest = reference_manifest
    normalized_manifest = _manifest_for_target(target, manifest)

    if previous["source_schema"] == "ref2va":
        if target == "base":
            raise H3PromptRepresentabilityError(
                "authored Ref2VA fields have no exact Base field owner"
            )
        if _json(normalized_manifest) != _json(previous["reference_manifest"]):
            raise H3PromptRepresentabilityError(
                "authored Ref2VA fields need explicit source ownership before reference rebinding"
            )
    return _create_mapping_record(
        previous["source_prompt"],
        target,
        duration_seconds=previous["duration_seconds"],
        reference_manifest=normalized_manifest,
        source_origin=previous["source_origin"],
    )


__all__ = [
    "H3PromptMappingError",
    "H3PromptRepresentabilityError",
    "H3PromptValidationError",
    "create_authored_mapping_record",
    "create_mapping_record",
    "map_h3_prompt_schema",
    "rebuild_mapping_record",
    "validate_mapping_record",
]
