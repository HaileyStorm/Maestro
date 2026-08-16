"""Pure, deterministic turn planning for MiniMax H3 multi-speaker work.

H3's documented conditioning contract is single-speaker.  This module models a
dialogue master as a sequence of non-overlapping turns so a later, separately
authorized runtime adapter could render each turn while keeping every other
visible subject explicitly silent.  Nothing here loads models, grants legal
authorization, touches files, or changes existing single-speaker and legacy
alternating-speaker paths.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


H3_TURN_PLAN_SCHEMA_VERSION = "h3_turn_plan_v1"
H3_TURN_PLAN_MODE = "m3_turn_conditioned"
H3_TURN_PLAN_FPS = 24
H3_LEGAL_BLOCKED = "legal_blocked"
H3_WRITTEN_AUTHORIZATION_VERIFIED = "written_authorization_verified"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_AUDIO_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,31}$")
_MAX_SPEAKERS = 1024
_MAX_TURNS = 1024
_MAX_FRAMES = H3_TURN_PLAN_FPS * 60 * 60 * 24
_MAX_TEXT_BYTES = 262_144
_MAX_TOTAL_TEXT_BYTES = 1_048_576
_MAX_REPLAY_BYTES = 3_145_728


class H3TurnPlanError(ValueError):
    """Raised when a multi-speaker turn plan is incomplete or ambiguous."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise H3TurnPlanError("Turn plan is not canonical JSON data.") from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise H3TurnPlanError(f"{name} must be an object.")
    if not all(type(key) is str for key in value):
        raise H3TurnPlanError(f"{name} keys must be text.")
    return value


def _sequence(value: object, *, name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise H3TurnPlanError(f"{name} must be an array.")
    return value


def _integer(value: object, *, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise H3TurnPlanError(
            f"{name} must be an integer from {minimum} through {maximum}."
        )
    return value


def _identifier(value: object, *, name: str) -> str:
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise H3TurnPlanError(f"{name} must be a stable identifier.")
    return value


def _digest(value: object, *, name: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise H3TurnPlanError(f"{name} must be a lowercase SHA-256 digest.")
    return value


def _seconds(frame: int) -> float:
    """Return the sole canonical JSON representation of a 24 fps position."""

    return round(frame / H3_TURN_PLAN_FPS, 9)


def _text(value: object, *, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise H3TurnPlanError(f"{name} must contain exact non-empty text.")
    encoded = value.encode("utf-8")
    if len(encoded) > _MAX_TEXT_BYTES:
        raise H3TurnPlanError(f"{name} is too large.")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise H3TurnPlanError(f"{name} contains unsupported control characters.")
    return value


def _audio_format(value: object) -> str:
    if type(value) is not str:
        raise H3TurnPlanError("dialogue master audio format must be text.")
    normalized = value.strip().lower()
    if not _AUDIO_FORMAT_RE.fullmatch(normalized):
        raise H3TurnPlanError("dialogue master audio format is invalid.")
    return normalized


def _dialogue_master(value: object) -> dict[str, Any]:
    source = _mapping(value, name="dialogue_master")
    audio_sha256 = _digest(
        source.get("audio_sha256"), name="dialogue master audio_sha256"
    )
    audio_format = _audio_format(source.get("audio_format"))
    sample_rate_hz = _integer(
        source.get("sample_rate_hz"),
        name="dialogue master sample_rate_hz",
        minimum=8_000,
        maximum=768_000,
    )
    channels = _integer(
        source.get("channels"),
        name="dialogue master channels",
        minimum=1,
        maximum=64,
    )
    duration_frames = _integer(
        source.get("duration_frames"),
        name="dialogue master duration_frames",
        minimum=1,
        maximum=_MAX_FRAMES,
    )
    identity_source = {
        "audio_format": audio_format,
        "audio_sha256": audio_sha256,
        "channels": channels,
        "duration_frames": duration_frames,
        "fps": H3_TURN_PLAN_FPS,
        "sample_rate_hz": sample_rate_hz,
    }
    master_id = f"h3dm1_{_sha256_text(_canonical_json(identity_source))}"
    compiled = {
        "master_id": master_id,
        **identity_source,
        "duration_seconds": _seconds(duration_frames),
    }
    if "fps" in source and source["fps"] != H3_TURN_PLAN_FPS:
        raise H3TurnPlanError(
            f"dialogue master fps must be exactly {H3_TURN_PLAN_FPS}."
        )
    for key in ("master_id", "duration_seconds"):
        if key in source and source[key] != compiled[key]:
            raise H3TurnPlanError(f"dialogue master {key} contradicts frame authority.")
    return compiled


def _legal_authorization(value: object) -> dict[str, Any]:
    """Normalize an explicit fail-closed authorization state.

    A digest is an evidence reference, not proof by itself.  Any future runtime
    must verify that evidence through its separately owned authorization gate.
    """

    source = _mapping(value, name="legal_authorization")
    state = source.get("state")
    territory = _identifier(
        source.get("territory"), name="legal authorization territory"
    )
    if state == H3_LEGAL_BLOCKED:
        if source.get("authorization_evidence_sha256") not in (None, ""):
            raise H3TurnPlanError(
                "legal_blocked plans cannot claim authorization evidence."
            )
        if source.get("runtime_allowed") not in (None, False):
            raise H3TurnPlanError("legal_blocked plans cannot allow runtime use.")
        compiled = {
            "state": H3_LEGAL_BLOCKED,
            "territory": territory,
            "runtime_allowed": False,
            "authorization_scope": "h3_local_inference",
        }
        if (
            "authorization_scope" in source
            and source["authorization_scope"] != compiled["authorization_scope"]
        ):
            raise H3TurnPlanError("legal authorization scope is contradictory.")
        return compiled
    if state == H3_WRITTEN_AUTHORIZATION_VERIFIED:
        evidence = _digest(
            source.get("authorization_evidence_sha256"),
            name="written authorization evidence",
        )
        if source.get("runtime_allowed") not in (None, True):
            raise H3TurnPlanError(
                "verified written authorization cannot contradict runtime state."
            )
        compiled = {
            "state": H3_WRITTEN_AUTHORIZATION_VERIFIED,
            "territory": territory,
            "runtime_allowed": True,
            "authorization_scope": "h3_local_inference",
            "authorization_evidence_sha256": evidence,
        }
        if (
            "authorization_scope" in source
            and source["authorization_scope"] != compiled["authorization_scope"]
        ):
            raise H3TurnPlanError("legal authorization scope is contradictory.")
        return compiled
    raise H3TurnPlanError(
        "legal_authorization state must be legal_blocked or "
        "written_authorization_verified."
    )


def _speakers(value: object) -> tuple[list[dict[str, str]], dict[str, str]]:
    raw = _sequence(value, name="speakers")
    if not raw or len(raw) > _MAX_SPEAKERS:
        raise H3TurnPlanError(
            f"speakers must contain 1 through {_MAX_SPEAKERS} speakers."
        )
    speakers: list[dict[str, str]] = []
    subject_by_speaker: dict[str, str] = {}
    seen_subjects: set[str] = set()
    for index, item in enumerate(raw):
        source = _mapping(item, name=f"speaker {index}")
        speaker_id = _identifier(source.get("speaker_id"), name=f"speaker {index} id")
        subject_id = _identifier(source.get("subject_id"), name=f"speaker {index} subject")
        if speaker_id in subject_by_speaker:
            raise H3TurnPlanError(f"speaker id {speaker_id!r} is ambiguous.")
        if subject_id in seen_subjects:
            raise H3TurnPlanError(f"subject id {subject_id!r} has multiple speakers.")
        subject_by_speaker[speaker_id] = subject_id
        seen_subjects.add(subject_id)
        speakers.append({"speaker_id": speaker_id, "subject_id": subject_id})
    return speakers, subject_by_speaker


def _visible_subjects(
    value: object,
    *,
    index: int,
    known_subjects: set[str],
    conditioned_subject: str,
) -> list[str]:
    raw = _sequence(value, name=f"turn {index} visible_subject_ids")
    visible: list[str] = []
    seen_visible: set[str] = set()
    for subject_index, item in enumerate(raw):
        subject_id = _identifier(
            item, name=f"turn {index} visible subject {subject_index}"
        )
        if subject_id in seen_visible:
            raise H3TurnPlanError(
                f"turn {index} repeats visible subject {subject_id!r}."
            )
        if subject_id not in known_subjects:
            raise H3TurnPlanError(
                f"turn {index} references unknown visible subject {subject_id!r}."
            )
        visible.append(subject_id)
        seen_visible.add(subject_id)
    if conditioned_subject not in visible:
        raise H3TurnPlanError(
            f"turn {index} must show its conditioned speaker subject."
        )
    return visible


def _turn_seed(
    *,
    dialogue_master_id: str,
    index: int,
    speaker_id: str,
    speaker_subject_id: str,
    text_sha256: str,
    start_frame: int,
    end_frame: int,
    source_start_frame: int,
    source_end_frame: int,
    visible_subject_ids: Sequence[str],
) -> dict[str, object]:
    return {
        "dialogue_master_id": dialogue_master_id,
        "end_frame": end_frame,
        "index": index,
        "source_end_frame": source_end_frame,
        "source_start_frame": source_start_frame,
        "speaker_id": speaker_id,
        "speaker_subject_id": speaker_subject_id,
        "start_frame": start_frame,
        "text_sha256": text_sha256,
        "visible_subject_ids": list(visible_subject_ids),
    }


def _compile_plan(source_value: object) -> dict[str, Any]:
    source = _mapping(source_value, name="turn plan")
    if source.get("schema_version") != H3_TURN_PLAN_SCHEMA_VERSION:
        raise H3TurnPlanError(
            f"schema_version must be {H3_TURN_PLAN_SCHEMA_VERSION!r}."
        )
    if source.get("mode") != H3_TURN_PLAN_MODE:
        raise H3TurnPlanError(
            f"mode must explicitly be {H3_TURN_PLAN_MODE!r}; legacy modes pass through."
        )

    legal_authorization = _legal_authorization(source.get("legal_authorization"))
    master = _dialogue_master(source.get("dialogue_master"))
    speakers, subject_by_speaker = _speakers(source.get("speakers"))
    known_subjects = set(subject_by_speaker.values())
    raw_turns = _sequence(source.get("turns"), name="turns")
    if not raw_turns or len(raw_turns) > _MAX_TURNS:
        raise H3TurnPlanError(f"turns must contain 1 through {_MAX_TURNS} entries.")
    total_frames = _integer(
        source.get("total_frames"),
        name="total_frames",
        minimum=1,
        maximum=_MAX_FRAMES,
    )
    if "total_seconds" in source and source["total_seconds"] != _seconds(total_frames):
        raise H3TurnPlanError("total_seconds contradicts 24 fps frame authority.")

    prepared: list[dict[str, Any]] = []
    total_text_bytes = 0
    previous_end = 0
    previous_source_end = 0
    for index, item in enumerate(raw_turns):
        turn = _mapping(item, name=f"turn {index}")
        speaker_id = _identifier(
            turn.get("speaker_id"), name=f"turn {index} speaker_id"
        )
        if speaker_id not in subject_by_speaker:
            raise H3TurnPlanError(
                f"turn {index} references unknown speaker {speaker_id!r}."
            )
        text = _text(turn.get("text"), name=f"turn {index} text")
        total_text_bytes += len(text.encode("utf-8"))
        if total_text_bytes > _MAX_TOTAL_TEXT_BYTES:
            raise H3TurnPlanError("turn-plan dialogue exceeds the total text limit.")
        start_frame = _integer(
            turn.get("start_frame"),
            name=f"turn {index} start_frame",
            minimum=0,
            maximum=_MAX_FRAMES - 1,
        )
        end_frame = _integer(
            turn.get("end_frame"),
            name=f"turn {index} end_frame",
            minimum=start_frame + 1,
            maximum=_MAX_FRAMES,
        )
        for key, expected in (
            ("start_seconds", _seconds(start_frame)),
            ("end_seconds", _seconds(end_frame)),
        ):
            if key in turn and turn[key] != expected:
                raise H3TurnPlanError(
                    f"turn {index} {key} contradicts 24 fps frame authority."
                )
        if start_frame < previous_end:
            raise H3TurnPlanError(
                f"turn {index} overlaps the previous speaker; H3 supports one voice at a time."
            )
        nested_source = turn.get("source_audio")
        if nested_source is not None and (
            "source_audio_start_frame" in turn or "source_audio_end_frame" in turn
        ):
            raise H3TurnPlanError(
                f"turn {index} cannot mix flat and nested source-audio authority."
            )
        source_fields = (
            _mapping(nested_source, name=f"turn {index} source_audio")
            if nested_source is not None
            else {}
        )
        source_start = _integer(
            turn.get("source_audio_start_frame", source_fields.get("start_frame")),
            name=f"turn {index} source_audio_start_frame",
            minimum=0,
            maximum=master["duration_frames"] - 1,
        )
        source_end = _integer(
            turn.get("source_audio_end_frame", source_fields.get("end_frame")),
            name=f"turn {index} source_audio_end_frame",
            minimum=source_start + 1,
            maximum=master["duration_frames"],
        )
        if source_start < previous_source_end:
            raise H3TurnPlanError(f"turn {index} overlaps prior dialogue-master audio.")
        if source_end - source_start != end_frame - start_frame:
            raise H3TurnPlanError(
                f"turn {index} source audio and visible timing must have equal frame counts."
            )
        for key, expected in (
            ("dialogue_master_id", master["master_id"]),
            ("start_seconds", _seconds(source_start)),
            ("end_seconds", _seconds(source_end)),
        ):
            if key in source_fields and source_fields[key] != expected:
                raise H3TurnPlanError(
                    f"turn {index} source_audio {key} contradicts source authority."
                )
        nested_conditioning = turn.get("conditioning")
        if nested_conditioning is not None and "visible_subject_ids" in turn:
            raise H3TurnPlanError(
                f"turn {index} cannot mix flat and nested visible-subject authority."
            )
        conditioning_fields = (
            _mapping(nested_conditioning, name=f"turn {index} conditioning")
            if nested_conditioning is not None
            else {}
        )
        visible = _visible_subjects(
            turn.get(
                "visible_subject_ids", conditioning_fields.get("visible_subject_ids")
            ),
            index=index,
            known_subjects=known_subjects,
            conditioned_subject=subject_by_speaker[speaker_id],
        )
        if (
            "speaker_subject_id" in turn
            and turn["speaker_subject_id"] != subject_by_speaker[speaker_id]
        ):
            raise H3TurnPlanError(
                f"turn {index} speaker_subject_id contradicts speaker authority."
            )
        if "text_sha256" in turn and turn["text_sha256"] != _sha256_text(text):
            raise H3TurnPlanError(f"turn {index} text_sha256 contradicts exact text.")
        if (
            "sole_conditioned_speaker_id" in conditioning_fields
            and conditioning_fields["sole_conditioned_speaker_id"] != speaker_id
        ):
            raise H3TurnPlanError(
                f"turn {index} has contradictory conditioned-speaker authority."
            )
        if "kind" in conditioning_fields and conditioning_fields["kind"] != "sole_speaker":
            raise H3TurnPlanError(
                f"turn {index} conditioning kind must be sole_speaker."
            )
        prepared.append(
            {
                "speaker_id": speaker_id,
                "speaker_subject_id": subject_by_speaker[speaker_id],
                "text": text,
                "text_sha256": _sha256_text(text),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "source_start": source_start,
                "source_end": source_end,
                "visible": visible,
            }
        )
        previous_end = end_frame
        previous_source_end = source_end

    if prepared[-1]["end_frame"] > total_frames:
        raise H3TurnPlanError("total_frames ends before the final turn.")

    compiled_turns: list[dict[str, Any]] = []
    previous_turn_id: str | None = None
    for index, item in enumerate(prepared):
        next_start = (
            prepared[index + 1]["start_frame"]
            if index + 1 < len(prepared)
            else total_frames
        )
        next_source_start = (
            prepared[index + 1]["source_start"]
            if index + 1 < len(prepared)
            else master["duration_frames"]
        )
        seed = _turn_seed(
            dialogue_master_id=master["master_id"],
            index=index,
            speaker_id=item["speaker_id"],
            speaker_subject_id=item["speaker_subject_id"],
            text_sha256=item["text_sha256"],
            start_frame=item["start_frame"],
            end_frame=item["end_frame"],
            source_start_frame=item["source_start"],
            source_end_frame=item["source_end"],
            visible_subject_ids=item["visible"],
        )
        turn_id = f"h3turn1_{index:04d}_{_sha256_text(_canonical_json(seed))}"
        seam_source = {
            "dialogue_master_id": master["master_id"],
            "previous_turn_id": previous_turn_id,
            "turn_id": turn_id,
        }
        seam_id = f"h3seam1_{_sha256_text(_canonical_json(seam_source))}"
        pause_before = item["start_frame"] - (
            prepared[index - 1]["end_frame"] if index else 0
        )
        gap_after = next_start - item["end_frame"]
        source_pause_before = item["source_start"] - (
            prepared[index - 1]["source_end"] if index else 0
        )
        source_gap_after = next_source_start - item["source_end"]
        silent_subjects = [
            subject_id
            for subject_id in item["visible"]
            if subject_id != item["speaker_subject_id"]
        ]
        for container, fields in (
            (
                raw_turns[index],
                {
                    "pause_before_frames": pause_before,
                    "pause_before_seconds": _seconds(pause_before),
                    "gap_after_frames": gap_after,
                    "gap_after_seconds": _seconds(gap_after),
                },
            ),
            (
                _mapping(raw_turns[index].get("source_audio"), name="source_audio")
                if isinstance(raw_turns[index], Mapping)
                and raw_turns[index].get("source_audio") is not None
                else {},
                {
                    "pause_before_frames": source_pause_before,
                    "pause_before_seconds": _seconds(source_pause_before),
                    "gap_after_frames": source_gap_after,
                    "gap_after_seconds": _seconds(source_gap_after),
                },
            ),
        ):
            for key, expected in fields.items():
                if key in container and container[key] != expected:
                    raise H3TurnPlanError(
                        f"turn {index} {key} contradicts explicit gap authority."
                    )
        conditioning_input = (
            _mapping(raw_turns[index].get("conditioning"), name="conditioning")
            if isinstance(raw_turns[index], Mapping)
            and raw_turns[index].get("conditioning") is not None
            else {}
        )
        if (
            "visible_silent_subject_ids" in conditioning_input
            and conditioning_input["visible_silent_subject_ids"] != silent_subjects
        ):
            raise H3TurnPlanError(
                f"turn {index} silent-subject list contradicts visibility authority."
            )
        compiled_turns.append(
            {
                "turn_id": turn_id,
                "speaker_id": item["speaker_id"],
                "speaker_subject_id": item["speaker_subject_id"],
                "text": item["text"],
                "text_sha256": item["text_sha256"],
                "start_frame": item["start_frame"],
                "end_frame": item["end_frame"],
                "start_seconds": _seconds(item["start_frame"]),
                "end_seconds": _seconds(item["end_frame"]),
                "pause_before_frames": pause_before,
                "pause_before_seconds": _seconds(pause_before),
                "gap_after_frames": gap_after,
                "gap_after_seconds": _seconds(gap_after),
                "source_audio": {
                    "dialogue_master_id": master["master_id"],
                    "start_frame": item["source_start"],
                    "end_frame": item["source_end"],
                    "start_seconds": _seconds(item["source_start"]),
                    "end_seconds": _seconds(item["source_end"]),
                    "pause_before_frames": source_pause_before,
                    "pause_before_seconds": _seconds(source_pause_before),
                    "gap_after_frames": source_gap_after,
                    "gap_after_seconds": _seconds(source_gap_after),
                },
                "conditioning": {
                    "kind": "sole_speaker",
                    "sole_conditioned_speaker_id": item["speaker_id"],
                    "visible_subject_ids": list(item["visible"]),
                    "visible_silent_subject_ids": silent_subjects,
                },
                "continuity": {
                    "previous_turn_id": previous_turn_id,
                    "seam_id": seam_id,
                },
            }
        )
        previous_turn_id = turn_id

    return {
        "schema_version": H3_TURN_PLAN_SCHEMA_VERSION,
        "mode": H3_TURN_PLAN_MODE,
        "legal_authorization": legal_authorization,
        "dialogue_master": master,
        "speakers": speakers,
        "turns": compiled_turns,
        "total_frames": total_frames,
        "total_seconds": _seconds(total_frames),
    }


def normalize_h3_turn_plan(value: object) -> dict[str, Any]:
    """Normalize raw M3 input, deriving all identities and aligned times."""

    return _compile_plan(value)


def seal_h3_turn_plan(value: object) -> dict[str, Any]:
    """Normalize raw M3 input and return a deterministic sealed plan."""

    source = _mapping(value, name="turn plan")
    if "plan_seal" in source:
        return validate_h3_turn_plan(source)
    compiled = normalize_h3_turn_plan(source)
    compiled["plan_seal"] = _sha256_text(_canonical_json(compiled))
    if len(_canonical_json(compiled).encode("utf-8")) > _MAX_REPLAY_BYTES:
        raise H3TurnPlanError(
            "sealed turn plan exceeds the canonical replay payload limit."
        )
    return compiled


def build_h3_turn_plan(
    *,
    mode: str,
    legal_authorization: Mapping[str, Any],
    dialogue_master: Mapping[str, Any],
    speakers: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    total_frames: int,
) -> dict[str, Any]:
    """Build an explicit M3 plan without activating it in legacy runtimes."""

    return seal_h3_turn_plan(
        {
            "schema_version": H3_TURN_PLAN_SCHEMA_VERSION,
            "mode": mode,
            "legal_authorization": legal_authorization,
            "dialogue_master": dialogue_master,
            "speakers": speakers,
            "turns": turns,
            "total_frames": total_frames,
        }
    )


def validate_h3_turn_plan(value: object) -> dict[str, Any]:
    """Recompute every derived field and seal, rejecting any altered byte."""

    source = _mapping(value, name="sealed turn plan")
    seal = _digest(source.get("plan_seal"), name="plan_seal")
    compiled = normalize_h3_turn_plan(source)
    expected = {**compiled, "plan_seal": _sha256_text(_canonical_json(compiled))}
    if len(_canonical_json(expected).encode("utf-8")) > _MAX_REPLAY_BYTES:
        raise H3TurnPlanError(
            "sealed turn plan exceeds the canonical replay payload limit."
        )
    if seal != expected["plan_seal"]:
        raise H3TurnPlanError("plan_seal does not match the canonical turn plan.")
    if dict(source) != expected:
        raise H3TurnPlanError("sealed turn plan contains noncanonical or altered fields.")
    return expected


def canonical_h3_turn_plan_json(value: object) -> str:
    """Return byte-stable canonical JSON after validating the plan seal."""

    return _canonical_json(validate_h3_turn_plan(value))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise H3TurnPlanError(f"canonical replay repeats key {key!r}.")
        result[key] = value
    return result


def replay_h3_turn_plan(payload: str | bytes) -> dict[str, Any]:
    """Replay canonical JSON and reject duplicates, tampering, or reformatting."""

    if isinstance(payload, bytes):
        if len(payload) > _MAX_REPLAY_BYTES:
            raise H3TurnPlanError("turn-plan replay exceeds the payload limit.")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise H3TurnPlanError("turn-plan replay must be UTF-8.") from exc
    elif type(payload) is str:
        text = payload
        if len(text) > _MAX_REPLAY_BYTES:
            raise H3TurnPlanError("turn-plan replay exceeds the payload limit.")
        try:
            encoded_size = len(text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise H3TurnPlanError("turn-plan replay must be valid Unicode.") from exc
        if encoded_size > _MAX_REPLAY_BYTES:
            raise H3TurnPlanError("turn-plan replay exceeds the payload limit.")
    else:
        raise H3TurnPlanError("turn-plan replay must be text or UTF-8 bytes.")
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                H3TurnPlanError(f"turn-plan replay contains {value}.")
            ),
        )
    except H3TurnPlanError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise H3TurnPlanError("turn-plan replay is not valid JSON.") from exc
    validated = validate_h3_turn_plan(decoded)
    if text != _canonical_json(validated):
        raise H3TurnPlanError("turn-plan replay bytes are not canonical JSON.")
    return validated


def public_h3_turn_plan_projection(value: object) -> dict[str, Any]:
    """Return content-free scheduling data suitable for public status views."""

    plan = validate_h3_turn_plan(value)
    master = plan["dialogue_master"]
    projection = {
        "schema_version": plan["schema_version"],
        "mode": plan["mode"],
        "legal_authorization": {
            "state": plan["legal_authorization"]["state"],
            "territory": plan["legal_authorization"]["territory"],
            "runtime_allowed": plan["legal_authorization"]["runtime_allowed"],
            "authorization_scope": plan["legal_authorization"][
                "authorization_scope"
            ],
        },
        "dialogue_master": {
            "audio_format": master["audio_format"],
            "channels": master["channels"],
            "duration_frames": master["duration_frames"],
            "duration_seconds": master["duration_seconds"],
            "fps": master["fps"],
            "sample_rate_hz": master["sample_rate_hz"],
        },
        "speakers": plan["speakers"],
        "turns": [
            {
                "speaker_id": turn["speaker_id"],
                "speaker_subject_id": turn["speaker_subject_id"],
                "start_frame": turn["start_frame"],
                "end_frame": turn["end_frame"],
                "start_seconds": turn["start_seconds"],
                "end_seconds": turn["end_seconds"],
                "pause_before_frames": turn["pause_before_frames"],
                "gap_after_frames": turn["gap_after_frames"],
                "conditioning": turn["conditioning"],
            }
            for turn in plan["turns"]
        ],
        "total_frames": plan["total_frames"],
        "total_seconds": plan["total_seconds"],
    }
    projection["projection_seal"] = _sha256_text(_canonical_json(projection))
    return projection


def is_h3_turn_plan_mode(value: object) -> bool:
    """Return true only for the explicit M3 path; legacy values remain false."""

    return value == H3_TURN_PLAN_MODE


__all__ = [
    "H3_TURN_PLAN_FPS",
    "H3_LEGAL_BLOCKED",
    "H3_TURN_PLAN_MODE",
    "H3_TURN_PLAN_SCHEMA_VERSION",
    "H3_WRITTEN_AUTHORIZATION_VERIFIED",
    "H3TurnPlanError",
    "build_h3_turn_plan",
    "canonical_h3_turn_plan_json",
    "is_h3_turn_plan_mode",
    "normalize_h3_turn_plan",
    "public_h3_turn_plan_projection",
    "replay_h3_turn_plan",
    "seal_h3_turn_plan",
    "validate_h3_turn_plan",
]
