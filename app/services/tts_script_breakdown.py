"""Parse speaker and emotion turns from a local TTS script."""

from __future__ import annotations

import re
from typing import Any


_TURN_RE = re.compile(
    r"^\s*(?P<speaker>Speaker\s+[1-4]|[A-Za-z][A-Za-z0-9 _-]{0,31})"
    r"(?:\s*\[(?P<emotion>[^\]]{1,48})\])?\s*:\s*(?P<line>\S.*)\s*$"
)


def parse_tts_script_turns(script: str) -> list[dict[str, Any]]:
    """Return speaker/emotion/line turns. Unknown shape yields one untagged turn."""

    if not isinstance(script, str) or not script.strip():
        return []
    turns: list[dict[str, Any]] = []
    for raw in script.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _TURN_RE.match(line)
        if match is None:
            if turns:
                turns[-1]["line"] = f"{turns[-1]['line']} {line}"
            else:
                turns.append({
                    "speaker": "Speaker 1",
                    "emotion": "",
                    "line": line,
                })
            continue
        turns.append({
            "speaker": " ".join(match.group("speaker").split()),
            "emotion": (match.group("emotion") or "").strip(),
            "line": match.group("line").strip(),
        })
    return turns


def format_voxcpm_turn_text(
    line: str,
    *,
    emotion: str = "",
    alt_prompt: str = "",
    model_mode: str = "",
) -> str:
    """Build VoxCPM2 text: parenthetical voice design, then spoken line."""

    spoken = str(line or "").strip()
    design = ", ".join(
        part.strip(" ()[]")
        for part in (alt_prompt, model_mode, emotion)
        if str(part or "").strip()
    )
    if design and spoken:
        return f"({design}){spoken}"
    return spoken or (f"({design})" if design else "")
