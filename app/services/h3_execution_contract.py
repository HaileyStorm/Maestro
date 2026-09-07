"""Shared H3 semantic-to-physical execution contract validation."""
from __future__ import annotations

import copy
import hashlib
import json

from services.queue_recovery_runtime import QueueRecoveryRuntimeError


def validate_h3_execution_shots(
    longform: dict,
    prompt_lines: list[str],
    clip_count: int,
) -> list[dict] | None:
    """Validate and return the semantic-to-physical child contract."""
    shot_plan = longform.get("shot_plan") if isinstance(longform, dict) else None
    if not isinstance(shot_plan, dict):
        return None
    raw_contract_version = shot_plan.get(
        "semantic_physical_contract_version"
    )
    if raw_contract_version is None:
        return None
    if (
        isinstance(raw_contract_version, bool)
        or not isinstance(raw_contract_version, int)
        or raw_contract_version not in {1, 2}
    ):
        raise QueueRecoveryRuntimeError(
            "H3 semantic execution contract version is unsupported."
        )
    contract_version = raw_contract_version
    if contract_version == 2:
        from services.h3_shot_planner import validate_h3_shot_plan_seal

        try:
            validate_h3_shot_plan_seal(shot_plan)
        except ValueError as exc:
            raise QueueRecoveryRuntimeError(str(exc)) from exc
    shots = shot_plan.get("shots")
    semantic = shot_plan.get("semantic_shots")
    source_contracts = shot_plan.get("source_contracts")
    if (
        not isinstance(shots, list) or len(shots) != clip_count
        or not isinstance(semantic, list) or not semantic
        or len(prompt_lines) != clip_count
        or (
            contract_version == 2
            and (
                source_contracts != semantic
                or shot_plan.get("clip_prompts") != prompt_lines
            )
        )
    ):
        raise QueueRecoveryRuntimeError(
            "H3 semantic execution slice count is invalid."
        )
    partition = []
    nested_events = []
    mappings = {}
    for contract in semantic:
        if not isinstance(contract, dict):
            raise QueueRecoveryRuntimeError(
                "H3 semantic execution contract is invalid."
            )
        indices = contract.get("segment_indices")
        slices = contract.get("execution_slices")
        if (
            not isinstance(indices, list) or not isinstance(slices, list)
            or len(indices) != len(slices) or not indices
            or any(type(value) is not int for value in indices)
            or contract.get("prompt_rewrite_for_physical_split")
                is not (contract_version == 2)
            or (
                contract_version == 2
                and contract.get("physical_prompt_compiler_version") != 2
            )
        ):
            raise QueueRecoveryRuntimeError(
                "H3 semantic execution contract is invalid."
            )
        cursor = 0
        semantic_prompt = str(contract.get("semantic_prompt"))
        prompt_digests = contract.get("executable_prompt_sha256")
        if contract_version == 2 and (
            not isinstance(prompt_digests, list)
            or len(prompt_digests) != len(indices)
        ):
            raise QueueRecoveryRuntimeError(
                "H3 semantic execution prompt evidence is invalid."
            )
        for physical_index, (segment_index, execution_slice) in enumerate(
            zip(indices, slices)
        ):
            try:
                segment_index = int(segment_index)
                valid = (
                    isinstance(execution_slice, dict)
                    and all(
                        type(execution_slice.get(field)) is int
                        for field in (
                            "segment_index", "physical_segment_index",
                            "start_frame", "end_frame_exclusive",
                        )
                    )
                    and int(execution_slice.get("segment_index")) == segment_index
                    and int(execution_slice.get("physical_segment_index"))
                        == physical_index
                    and int(execution_slice.get("start_frame")) == cursor
                    and int(execution_slice.get("end_frame_exclusive")) > cursor
                    and (
                        prompt_lines[segment_index] == semantic_prompt
                        if contract_version == 1 else
                        prompt_digests[physical_index] == hashlib.sha256(
                            prompt_lines[segment_index].encode("utf-8")
                        ).hexdigest()
                    )
                )
                cursor = int(execution_slice.get("end_frame_exclusive"))
            except (IndexError, TypeError, ValueError):
                valid = False
            if not valid:
                raise QueueRecoveryRuntimeError(
                    "H3 semantic execution slice is invalid."
                )
            partition.append(segment_index)
            mappings[segment_index] = (
                contract, physical_index, execution_slice,
            )
        if contract_version == 2:
            events = contract.get("event_ownership")
            if not isinstance(events, list):
                raise QueueRecoveryRuntimeError(
                    "H3 semantic event ownership is invalid."
                )
            for event in events:
                try:
                    owner = event.get("owner_segment_index")
                    local_owner = event.get(
                        "owner_physical_segment_index"
                    )
                    payload = event.get("executable_payload")
                    valid = (
                        isinstance(event, dict)
                        and type(owner) is int
                        and type(local_owner) is int
                        and 0 <= local_owner < len(indices)
                        and int(indices[local_owner]) == owner
                        and isinstance(payload, str)
                        and payload.strip()
                        and payload in prompt_lines[owner]
                        and event.get("owner_physical_segment_id") == (
                            f"{contract.get('authored_shot_id')}:segment-"
                            f"{local_owner + 1}"
                        )
                        and (
                            event.get("kind") != "final_blocking"
                            or local_owner == len(indices) - 1
                        )
                    )
                    owner_slice = slices[local_owner]
                    source_start = event.get("source_start_frame")
                    source_end = event.get("source_end_frame_exclusive")
                    if source_start is None and source_end is None:
                        valid = valid and all(
                            event.get(field) is None
                            for field in (
                                "local_start_frame",
                                "local_end_frame_exclusive",
                            )
                        )
                    elif (
                        type(source_start) is int
                        and type(source_end) is int
                    ):
                        valid = valid and (
                            int(owner_slice.get("start_frame"))
                            <= source_start
                            < int(owner_slice.get("end_frame_exclusive"))
                            and source_end > source_start
                            and event.get("local_start_frame") == (
                                source_start
                                - int(owner_slice.get("start_frame"))
                            )
                            and event.get("local_end_frame_exclusive") == (
                                min(
                                    source_end,
                                    int(owner_slice.get(
                                        "end_frame_exclusive"
                                    )),
                                )
                                - int(owner_slice.get("start_frame"))
                            )
                        )
                    else:
                        valid = False
                except (AttributeError, IndexError, TypeError, ValueError):
                    valid = False
                if not valid:
                    raise QueueRecoveryRuntimeError(
                        "H3 semantic event ownership is invalid."
                    )
            nested_events.extend(events)
    if sorted(partition) != list(range(clip_count)) or len(set(partition)) != clip_count:
        raise QueueRecoveryRuntimeError(
            "H3 semantic execution slices do not partition the children."
        )
    if (
        contract_version == 2
        and shot_plan.get("event_ownership") != nested_events
    ):
        raise QueueRecoveryRuntimeError(
            "H3 semantic event ownership copies disagree."
        )
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise QueueRecoveryRuntimeError(
                "H3 physical execution child is invalid."
            )
        predecessor = shots[index - 1] if index else None
        try:
            contract, physical_index, execution_slice = mappings[index]
            valid = (
                int(shot.get("index")) == index
                and str(shot.get("prompt")) == prompt_lines[index]
                and int(shot.get("source_index"))
                    == int(contract.get("source_index"))
                and int(shot.get("physical_segment_index"))
                    == physical_index
                and shot.get("execution_slice") == execution_slice
                and int(shot.get("published_frames"))
                    == int(execution_slice.get("end_frame_exclusive"))
                    - int(execution_slice.get("start_frame"))
                and int(shot.get("execution_cursor_frame"))
                    == int((shot.get("execution_slice") or {}).get("start_frame"))
                and (
                    shot.get("predecessor_segment_index") is None
                    if index == 0 else int(shot.get("predecessor_segment_index"))
                        == index - 1
                )
                and (
                    shot.get("predecessor_physical_segment_id") is None
                    if predecessor is None else str(
                        shot.get("predecessor_physical_segment_id")
                    ) == str(predecessor.get("physical_segment_id"))
                )
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise QueueRecoveryRuntimeError(
                "H3 physical execution predecessor contract is invalid."
            )
    return [copy.deepcopy(shot) for shot in shots]


def rewrite_h3_execution_prompts(shot_plan: dict, prompts: list[str], *, validate_prompt) -> dict:
    """Return a validated v2 plan copy; never repair a stale seal or mutate input.

    The caller validates each target schema by raising on failure and returning
    None on success. Applying the returned plan remains the caller's operation.
    """
    from services.director.h3_dialogue import _dialogue_spans
    from services.h3_shot_planner import (
        seal_h3_shot_plan, _semantic_dialogue_identity, _tag_dialogue_occurrences,
        _compile_segment_local_prompts, _strip_dialogue_occurrence_tokens,
        _canonical_context_ir_parts, h3_effective_source, h3_source_compiler_inputs,
    )

    candidate = copy.deepcopy(shot_plan)
    if not isinstance(candidate, dict) or type(candidate.get("semantic_physical_contract_version")) is not int or candidate["semantic_physical_contract_version"] != 2:
        raise QueueRecoveryRuntimeError("H3 prompt mapping requires a sealed v2 execution plan.")
    original_prompts = candidate.get("clip_prompts")
    if not isinstance(original_prompts, list) or not original_prompts or any(not isinstance(value, str) for value in original_prompts):
        raise QueueRecoveryRuntimeError("H3 executable prompt sequence is invalid.")
    count = len(original_prompts)
    validate_h3_execution_shots({"shot_plan": candidate}, original_prompts, count)
    if not isinstance(prompts, list):
        raise QueueRecoveryRuntimeError("H3 mapped prompt sequence must match every execution segment.")
    prompts = list(prompts)
    if len(prompts) != count or any(not isinstance(value, str) or not value.strip() for value in prompts):
        raise QueueRecoveryRuntimeError("H3 mapped prompt sequence must match every execution segment.")
    if not callable(validate_prompt):
        raise TypeError("H3 target prompt validator must be callable.")

    def dialogue_blocks(text):
        spans, malformed = _dialogue_spans(text)
        if malformed:
            raise QueueRecoveryRuntimeError("H3 executable dialogue tags are malformed.")
        return [text[start:end] for start, end in spans]

    published = candidate.get("clip_published_frames")
    if not isinstance(published, list) or len(published) != count or any(
        type(frames) is not int or frames <= 0 or frames != candidate["shots"][index]["published_frames"]
        for index, frames in enumerate(published)
    ):
        raise QueueRecoveryRuntimeError("H3 published frame geometry disagrees.")

    for contract in candidate["source_contracts"]:
        source_index = contract["source_index"]
        authored_id = contract["authored_shot_id"]
        semantic_manifest = [
            _semantic_dialogue_identity(block, source_index=source_index, semantic_occurrence_index=ordinal)
            for ordinal, block in enumerate(dialogue_blocks(contract["semantic_prompt"]))
        ]
        tagged, tokens = _tag_dialogue_occurrences(contract["semantic_prompt"], semantic_manifest)
        compiler_inputs = h3_source_compiler_inputs(contract)
        effective_source = h3_effective_source(
            contract["authored_prompt"], compiler_inputs.get("source_canonicalization"),
        )
        canonical_source = _canonical_context_ir_parts(effective_source) is not None
        _, expected_events = _compile_segment_local_prompts(
            tagged, segment_positions=contract["segment_indices"], published_frames=published,
            source_index=source_index, fps=candidate["fps"],
            final_blocking=str(contract.get("final_blocking") or "") if canonical_source else "",
            opening_blocking=str(contract.get("opening_blocking") or "") if canonical_source else "",
            dialogue_occurrence_tokens=tokens,
        )
        offset = sum(published[:contract["segment_indices"][0]])
        for ordinal, expected in enumerate(expected_events):
            expected["executable_payload"] = _strip_dialogue_occurrence_tokens(
                str(expected.get("executable_payload") or ""), tokens,
            )
            expected.update({
                "authored_shot_id": authored_id, "semantic_shot_index": source_index,
                "event_id": f"{authored_id}:event-{ordinal + 1}",
                "owner_physical_segment_id": f"{authored_id}:segment-{expected['owner_physical_segment_index'] + 1}",
                "published_start_frame": None if expected["source_start_frame"] is None else offset + expected["source_start_frame"],
                "published_end_frame_exclusive": None if expected["source_end_frame_exclusive"] is None else offset + expected["source_end_frame_exclusive"],
            })
            for part in expected["continuation_slices"]:
                part.update({
                    "physical_segment_id": f"{authored_id}:segment-{part['physical_segment_index'] + 1}",
                    "published_start_frame": offset + part["source_start_frame"],
                    "published_end_frame_exclusive": offset + part["source_end_frame_exclusive"],
                })
        if json.dumps(contract["event_ownership"], sort_keys=True, ensure_ascii=False) != json.dumps(expected_events, sort_keys=True, ensure_ascii=False):
            raise QueueRecoveryRuntimeError("H3 event ownership disagrees with its semantic source.")
        for event in expected_events:
            payload = event["executable_payload"]
            if [text.count(payload) for text in original_prompts] != [text.count(payload) for text in prompts]:
                raise QueueRecoveryRuntimeError("H3 mapped prompt changed event multiplicity or ownership.")

    manifest = candidate.get("dialogue_manifest")
    nested = []
    for contract in candidate["source_contracts"]:
        entries = contract.get("dialogue_manifest")
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict)
            or type(entry.get("source_index")) is not int
            or entry["source_index"] != contract.get("source_index")
            or entry.get("authored_shot_id") != contract.get("authored_shot_id")
            or type(entry.get("segment_index")) is not int
            or entry["segment_index"] not in contract["segment_indices"]
            for entry in entries
        ):
            raise QueueRecoveryRuntimeError("H3 source dialogue ownership is invalid.")
        semantic_blocks = dialogue_blocks(contract["semantic_prompt"])
        ordinals = [entry.get("semantic_occurrence_index") for entry in entries]
        if any(type(value) is not int for value in ordinals) or sorted(ordinals) != list(range(len(semantic_blocks))):
            raise QueueRecoveryRuntimeError("H3 semantic dialogue occurrences are invalid.")
        for entry, ordinal in zip(entries, ordinals):
            identity = _semantic_dialogue_identity(
                semantic_blocks[ordinal], source_index=contract["source_index"],
                semantic_occurrence_index=ordinal,
            )
            if any(entry.get(key) != value for key, value in identity.items()) or entry.get("semantic_shot_index") != contract["source_index"]:
                raise QueueRecoveryRuntimeError("H3 dialogue identity disagrees with its semantic source.")
        nested.extend(entries)
    if not isinstance(manifest, list) or manifest != nested or any(
        not isinstance(entry, dict) or type(entry.get("segment_index")) is not int
        or not 0 <= entry["segment_index"] < count or not isinstance(entry.get("exact_block"), str)
        for entry in manifest
    ):
        raise QueueRecoveryRuntimeError("H3 dialogue ownership copies disagree.")
    for index, (original, mapped) in enumerate(zip(original_prompts, prompts)):
        owned_indices = [position for position, entry in enumerate(manifest) if entry["segment_index"] == index]
        expected_blocks = [manifest[position]["exact_block"] for position in owned_indices]
        indices = candidate["shots"][index].get("dialogue_manifest_indices")
        if not isinstance(indices, list) or any(type(position) is not int for position in indices) or indices != owned_indices or dialogue_blocks(original) != expected_blocks:
            raise QueueRecoveryRuntimeError("H3 source dialogue ownership disagrees with executable prompts.")
        if dialogue_blocks(mapped) != expected_blocks:
            raise QueueRecoveryRuntimeError("H3 mapped prompt changed dialogue bytes, order, or ownership.")
        if validate_prompt(index, mapped) is not None:
            raise QueueRecoveryRuntimeError("H3 target prompt validator did not confirm success.")

    candidate["clip_prompts"] = list(prompts)
    for index, shot in enumerate(candidate["shots"]):
        shot["prompt"] = prompts[index]
    for name in ("source_contracts", "semantic_shots"):
        for contract in candidate[name]:
            contract["executable_prompt_sha256"] = [
                hashlib.sha256(prompts[index].encode("utf-8")).hexdigest()
                for index in contract["segment_indices"]
            ]
    seal_h3_shot_plan(candidate)
    validate_h3_execution_shots({"shot_plan": candidate}, candidate["clip_prompts"], count)
    return candidate
