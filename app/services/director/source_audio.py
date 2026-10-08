"""Factual source-voice timing, separate from authored dialogue and prompts."""
from __future__ import annotations

import math


def source_voice_intervals(lyrics: list[dict] | None, speaker_mappings) -> list[dict]:
    """Keep explicit source timestamps/identities without reading transcript words."""
    mappings = speaker_mappings or {}
    if isinstance(mappings, list):
        mappings = {
            entry.get("speakerId") or entry.get("speaker_id"): entry
            for entry in mappings if isinstance(entry, dict)
            and (entry.get("speakerId") or entry.get("speaker_id"))
        }
    intervals = []
    for line in lyrics or []:
        if not isinstance(line, dict) or "start" not in line or "end" not in line:
            continue
        try:
            start, end = float(line["start"]), float(line["end"])
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        speaker_id = line.get("speaker")
        mapping = mappings.get(speaker_id, {})
        intervals.append({
            "start": start, "end": end, "speaker_id": speaker_id,
            "name": mapping.get("name") or None,
            "role": mapping.get("role") or None,
        })
    return sorted(intervals, key=lambda voice: voice["start"])


def overlapping_source_voices(intervals: list[dict], start: float, end: float) -> list[dict]:
    """Select a shot's turns, retaining original absolute source bounds."""
    return [dict(voice) for voice in intervals
            if voice["start"] < end and voice["end"] > start]


def source_voice_context(intervals: list[dict], start: float, end: float, *, anchor: str = "shot start") -> str:
    """Render intersecting turns relative to a supplied source-audio slice."""
    turns = []
    for voice in overlapping_source_voices(intervals, start, end):
        name = voice["name"] or "unmapped source voice"
        if voice["role"]:
            name += f" ({voice['role']})"
        local_start, local_end = max(voice["start"], start) - start, min(voice["end"], end) - start
        turns.append(f"{local_start:.3f}–{local_end:.3f}s: {name}")
    if not turns:
        return ""
    return (
        f"Transcribed voice intervals relative to {anchor}: " + "; ".join(turns)
        + ". These identify audible source parts; lip-sync only the assigned "
        "person when visible, and this does not require them on screen. "
        "Unmapped voices have no assigned visual identity. Missing transcript "
        "coverage does not establish silence; follow the supplied audio through gaps"
    )


def source_voice_timing_packet(params: dict, clips: list[dict], *, audio_origin_sec: float = 0.0) -> dict | None:
    """Bind the full saved source timeline; planned overlaps alone miss rounded cuts."""
    if "lyrics" in params:
        intervals = source_voice_intervals(params.get("lyrics"), params.get("speaker_mappings"))
    else:
        intervals = []
        for clip in clips:
            for voice in _saved_source_voices(clip.get("source_voice_intervals")):
                if voice not in intervals:
                    intervals.append(dict(voice))
        intervals.sort(key=lambda voice: voice["start"])
    if not intervals:
        return None
    return {"schema": "director.source-voice-timing.v1", "intervals": intervals,
            "audio_origin_sec": audio_origin_sec}


def _saved_source_voices(records) -> list[dict]:
    """Skip damaged optional facts without inventing absent source timestamps."""
    valid = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        try:
            start, end = float(record["start"]), float(record["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        valid.append({"start": start, "end": end, "speaker_id": record.get("speaker_id"),
                      "name": record.get("name") if isinstance(record.get("name"), str) else None,
                      "role": record.get("role") if isinstance(record.get("role"), str) else None})
    return valid


def apply_source_voice_window(prompt: str, custom_settings: dict, *, model_type: str,
                            model_def: dict, start_frame: int, num_frames: int, fps: float) -> str:
    """Apply source facts after slicing, against exactly the same frame clock."""
    if not any(str(value or "").lower().startswith("ltx2_25") for value in
               (model_type, model_def.get("architecture"))):
        return prompt
    packet = custom_settings.get("director_source_voice_timing")
    if not isinstance(packet, dict) or packet.get("schema") != "director.source-voice-timing.v1":
        return prompt
    try:
        start = float(packet["audio_origin_sec"]) + start_frame / float(fps)
        end = start + num_frames / float(fps)
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            return prompt
        timing = source_voice_context(_saved_source_voices(packet["intervals"]), start, end,
                                      anchor="this generation window's start")
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        # Optional saved facts must never prevent generation or fabricate timing.
        return prompt
    return f"{prompt.rstrip()} SOURCE-AUDIO VOICE TIMING: {timing}" if timing else prompt


def apply_h3_source_voice_window(prompt: str, custom_settings: dict, window: dict) -> str:
    """Describe intended supplied-track output, not timestamp-locked conditioning."""
    from services.h3_prompt_mapping import append_h3_soundscape_context

    packet = custom_settings.get("director_source_voice_timing")
    if not isinstance(packet, dict) or packet.get("schema") != "director.source-voice-timing.v1":
        return prompt
    try:
        start = float(window["start_sec"])
        end = start + int(window["published_frames"]) / float(window["fps"])
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            return prompt
        voices = _saved_source_voices(packet["intervals"])
        # Factual labels must not introduce H3 dialogue/reference syntax.
        for voice in voices:
            for field in ("name", "role"):
                if voice[field]:
                    voice[field] = voice[field].translate(str.maketrans({"<": "(", ">": ")", "|": "/"}))
        timing = source_voice_context(voices, start, end, anchor="this published segment's start")
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return prompt
    return append_h3_soundscape_context(
        prompt, "Supplied-track output timing (performance intent; not timestamp-locked conditioning): " + timing,
    ) if timing else prompt
