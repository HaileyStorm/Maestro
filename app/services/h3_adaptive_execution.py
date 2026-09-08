"""Bind one sealed H3 execution segment to its current model schema.

The caller supplies an already admitted structural reference manifest.  This
module reads no assets and the returned receipt records provenance only; it
does not grant access to a model, plan, or reference.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

from services.h3_execution_contract import (
    rewrite_h3_execution_prompts,
    validate_h3_execution_shots,
)
from services.h3_lora_compat import architecture_for_h3_model
from services.h3_prompt_mapping import (
    H3PromptMappingError,
    create_authored_mapping_record,
    create_mapping_record,
    validate_mapping_record,
)


_RECEIPT_VERSION = 1
_RECEIPT_FIELDS = frozenset({
    "version",
    "source_lineage_sha256",
    "segment_index",
    "model_type",
    "target_schema",
    "frames",
    "published_frames",
    "trim_tail_frames",
    "fps",
    "mapping_record_sha256",
})
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class H3AdaptiveExecutionError(ValueError):
    """A model binding or mapping receipt is incomplete or inconsistent."""


def _snapshot_json(value: object, *, label: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return json.loads(encoded)
    except (OverflowError, RecursionError, TypeError, ValueError) as exc:
        raise H3AdaptiveExecutionError(f"{label} must be finite JSON data") from exc


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _positive_geometry(source_plan: dict, segment_index: int) -> tuple[int, int, int, float]:
    count = len(source_plan["clip_prompts"])
    fields = (
        ("clip_frames", "frames"),
        ("clip_published_frames", "published frames"),
        ("clip_trim_tail_frames", "trim frames"),
    )
    values: list[int] = []
    for key, label in fields:
        sequence = source_plan.get(key)
        if not isinstance(sequence, list) or len(sequence) != count:
            raise H3AdaptiveExecutionError(
                f"H3 execution {label} must align with every segment."
            )
        value = sequence[segment_index]
        if type(value) is not int:
            raise H3AdaptiveExecutionError(
                f"H3 execution {label} must be integer frame counts."
            )
        values.append(value)
    frames, published, trim = values
    if frames <= 0 or published <= 0 or trim < 0 or published + trim != frames:
        raise H3AdaptiveExecutionError("H3 execution frame geometry is inconsistent.")
    fps_value = source_plan.get("fps")
    if isinstance(fps_value, bool) or not isinstance(fps_value, (int, float)):
        raise H3AdaptiveExecutionError("H3 execution FPS must be a positive finite number.")
    fps = float(fps_value)
    if not math.isfinite(fps) or fps <= 0:
        raise H3AdaptiveExecutionError("H3 execution FPS must be a positive finite number.")
    return frames, published, trim, fps


def _segment_source_lineage(
    source_plan: dict,
    shots: list[dict],
    segment_index: int,
) -> str:
    """Digest only the selected child's immutable source and ownership slice."""

    shot = shots[segment_index]
    source_index = shot.get("source_index")
    authored_shot_id = shot.get("authored_shot_id")
    physical_index = shot.get("physical_segment_index")
    execution_slice = shot.get("execution_slice")
    if (
        type(source_index) is not int
        or source_index < 0
        or not isinstance(authored_shot_id, str)
        or not authored_shot_id.strip()
        or type(physical_index) is not int
        or physical_index < 0
        or not isinstance(execution_slice, dict)
    ):
        raise H3AdaptiveExecutionError("H3 execution source lineage is invalid.")
    start = execution_slice.get("start_frame")
    end = execution_slice.get("end_frame_exclusive")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        raise H3AdaptiveExecutionError("H3 execution source interval is invalid.")

    contracts = source_plan.get("source_contracts")
    matches = [
        contract
        for contract in contracts if (
            isinstance(contract, dict)
            and contract.get("source_index") == source_index
            and contract.get("authored_shot_id") == authored_shot_id
        )
    ] if isinstance(contracts, list) else []
    if len(matches) != 1 or not isinstance(matches[0].get("authored_prompt"), str):
        raise H3AdaptiveExecutionError("H3 execution authored source is invalid.")
    contract = matches[0]

    dialogue = []
    manifest = source_plan.get("dialogue_manifest")
    if not isinstance(manifest, list):
        raise H3AdaptiveExecutionError("H3 execution dialogue lineage is invalid.")
    for entry in manifest:
        if not isinstance(entry, dict) or entry.get("segment_index") != segment_index:
            continue
        ordinal = entry.get("semantic_occurrence_index")
        speaker_id = entry.get("speaker_id")
        source = entry.get("source")
        exact_block = entry.get("exact_block")
        spoken_text = entry.get("spoken_text")
        if (
            type(ordinal) is not int
            or ordinal < 0
            or not isinstance(speaker_id, str)
            or not isinstance(source, str)
            or not source
            or not isinstance(exact_block, str)
            or not exact_block
            or not isinstance(spoken_text, str)
            or entry.get("source_index") != source_index
            or entry.get("authored_shot_id") != authored_shot_id
        ):
            raise H3AdaptiveExecutionError("H3 execution dialogue lineage is invalid.")
        dialogue.append({
            "semantic_occurrence_index": ordinal,
            "speaker_id": speaker_id,
            "source": source,
            "exact_block_sha256": hashlib.sha256(
                exact_block.encode("utf-8")
            ).hexdigest(),
            "spoken_text_sha256": hashlib.sha256(
                spoken_text.encode("utf-8")
            ).hexdigest(),
        })

    event_roles = []
    events = contract.get("event_ownership")
    if not isinstance(events, list):
        raise H3AdaptiveExecutionError("H3 execution event lineage is invalid.")
    for event in events:
        if not isinstance(event, dict):
            raise H3AdaptiveExecutionError("H3 execution event lineage is invalid.")
        roles = []
        if event.get("owner_segment_index") == segment_index:
            roles.append("owner")
        continuations = event.get("continuation_slices")
        if not isinstance(continuations, list):
            raise H3AdaptiveExecutionError("H3 execution event lineage is invalid.")
        if any(
            isinstance(part, dict) and part.get("segment_index") == segment_index
            for part in continuations
        ):
            roles.append("continuation")
        for role in roles:
            event_id = event.get("event_id")
            kind = event.get("kind")
            authored_order = event.get("authored_order")
            if (
                not isinstance(event_id, str)
                or not event_id
                or not isinstance(kind, str)
                or not kind
                or type(authored_order) is not int
                or authored_order < 0
            ):
                raise H3AdaptiveExecutionError("H3 execution event lineage is invalid.")
            event_roles.append({
                "event_id": event_id,
                "role": role,
                "kind": kind,
                "authored_order": authored_order,
            })

    lineage = {
        "version": 1,
        "source_index": source_index,
        "authored_shot_id": authored_shot_id,
        "authored_prompt_sha256": hashlib.sha256(
            contract["authored_prompt"].encode("utf-8")
        ).hexdigest(),
        "physical_segment_index": physical_index,
        "execution_interval": {
            "start_frame": start,
            "end_frame_exclusive": end,
        },
        "dialogue_occurrences": dialogue,
        "event_roles": event_roles,
    }
    return _canonical_digest(lineage)


def validate_h3_mapping_receipt(receipt: object) -> dict[str, Any]:
    """Validate one closed structural receipt and return an isolated JSON copy."""

    if type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS:
        raise H3AdaptiveExecutionError("H3 mapping receipt is incomplete or unsupported.")
    value = _snapshot_json(receipt, label="H3 mapping receipt")
    if type(value.get("version")) is not int or value["version"] != _RECEIPT_VERSION:
        raise H3AdaptiveExecutionError("H3 mapping receipt is incomplete or unsupported.")

    lineage_digest = value.get("source_lineage_sha256")
    if (
        not isinstance(lineage_digest, str)
        or _SHA256_RE.fullmatch(lineage_digest) is None
    ):
        raise H3AdaptiveExecutionError("H3 mapping receipt source lineage is invalid.")

    segment_index = value.get("segment_index")
    if type(segment_index) is not int or segment_index < 0:
        raise H3AdaptiveExecutionError("H3 mapping receipt segment index is invalid.")
    model_type = value.get("model_type")
    if (
        not isinstance(model_type, str)
        or not model_type.strip()
        or model_type != model_type.strip()
    ):
        raise H3AdaptiveExecutionError("H3 mapping receipt model is invalid.")
    architecture = architecture_for_h3_model(model_type)
    expected_target = {"fl2va": "base", "ref2va": "ref2va"}.get(architecture)
    if expected_target is None or value.get("target_schema") != expected_target:
        raise H3AdaptiveExecutionError("H3 mapping receipt model target is invalid.")

    frames = value.get("frames")
    published = value.get("published_frames")
    trim = value.get("trim_tail_frames")
    if (
        type(frames) is not int
        or type(published) is not int
        or type(trim) is not int
        or frames <= 0
        or published <= 0
        or trim < 0
        or published + trim != frames
    ):
        raise H3AdaptiveExecutionError("H3 mapping receipt frame geometry is invalid.")
    fps_value = value.get("fps")
    if (
        isinstance(fps_value, bool)
        or not isinstance(fps_value, (int, float))
        or not math.isfinite(float(fps_value))
        or float(fps_value) <= 0
    ):
        raise H3AdaptiveExecutionError("H3 mapping receipt FPS is invalid.")
    value["fps"] = float(fps_value)

    digest = value.get("mapping_record_sha256")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise H3AdaptiveExecutionError("H3 mapping receipt record digest is invalid.")
    return copy.deepcopy(value)


def bind_h3_execution_segment(
    source_plan: dict,
    *,
    segment_index: int,
    model_type: object,
    reference_manifest: object,
    authored_ref: bool = False,
) -> dict[str, dict[str, Any]]:
    """Map one segment from an immutable sealed source plan to its current model."""

    if type(segment_index) is not int:
        raise H3AdaptiveExecutionError("H3 execution segment index must be an integer.")
    if type(authored_ref) is not bool:
        raise H3AdaptiveExecutionError("H3 authored Ref2VA attestation must be boolean.")
    if not isinstance(source_plan, dict):
        raise H3AdaptiveExecutionError("H3 execution source plan must be an object.")
    prompt_lines = source_plan.get("clip_prompts")
    if not isinstance(prompt_lines, list):
        raise H3AdaptiveExecutionError("H3 execution source prompts are invalid.")

    # Validate the complete sealed plan before selecting a child prompt.
    shots = validate_h3_execution_shots(
        {"shot_plan": source_plan}, prompt_lines, len(prompt_lines)
    )
    if (
        shots is None
        or type(source_plan.get("semantic_physical_contract_version")) is not int
        or source_plan["semantic_physical_contract_version"] != 2
    ):
        raise H3AdaptiveExecutionError("H3 execution needs a sealed v2 source plan.")
    if segment_index < 0 or segment_index >= len(prompt_lines):
        raise H3AdaptiveExecutionError("H3 execution segment index is out of range.")

    architecture = architecture_for_h3_model(model_type)
    target_schema = {"fl2va": "base", "ref2va": "ref2va"}.get(architecture)
    if target_schema is None:
        raise H3AdaptiveExecutionError("H3 execution model architecture is unsupported.")
    model_name = str(model_type or "")
    frames, published, trim, fps = _positive_geometry(source_plan, segment_index)
    source_lineage_sha256 = _segment_source_lineage(
        source_plan, shots, segment_index
    )
    duration_seconds = published / fps
    source_prompt = prompt_lines[segment_index]
    factory = create_authored_mapping_record if authored_ref else create_mapping_record
    record = factory(
        source_prompt,
        target_schema,
        duration_seconds=duration_seconds,
        reference_manifest=reference_manifest,
    )
    mapped_prompts = list(prompt_lines)
    mapped_prompts[segment_index] = record["mapped_prompt"]

    def validate_target(index: int, prompt: str) -> None:
        if index != segment_index:
            if prompt != prompt_lines[index]:
                raise H3PromptMappingError(
                    "prompt mapping changed an unselected execution segment"
                )
            return None
        current = validate_mapping_record(
            record,
            current_reference_manifest=reference_manifest,
            current_target_schema=target_schema,
            duration_seconds=duration_seconds,
        )
        if prompt != current["mapped_prompt"]:
            raise H3PromptMappingError("mapped execution prompt disagrees with its record")
        return None

    mapped_plan = rewrite_h3_execution_prompts(
        source_plan,
        mapped_prompts,
        validate_prompt=validate_target,
    )
    receipt = validate_h3_mapping_receipt({
        "version": _RECEIPT_VERSION,
        "source_lineage_sha256": source_lineage_sha256,
        "segment_index": segment_index,
        "model_type": model_name,
        "target_schema": target_schema,
        "frames": frames,
        "published_frames": published,
        "trim_tail_frames": trim,
        "fps": fps,
        "mapping_record_sha256": _canonical_digest(record),
    })
    return {
        "plan": mapped_plan,
        "record": copy.deepcopy(record),
        "receipt": receipt,
    }


__all__ = [
    "H3AdaptiveExecutionError",
    "bind_h3_execution_segment",
    "validate_h3_mapping_receipt",
]
