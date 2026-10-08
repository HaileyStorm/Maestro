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
        except (TypeError, ValueError):
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


def source_voice_context(intervals: list[dict], start: float, end: float) -> str:
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
        "Transcribed voice intervals relative to shot start: " + "; ".join(turns)
        + ". These identify audible source parts; lip-sync only the assigned "
        "person when visible, and this does not require them on screen. "
        "Unmapped voices have no assigned visual identity. Missing transcript "
        "coverage does not establish silence; follow the supplied audio through gaps"
    )
