"""Select a bounded set of local music-writing Markdown guides for an LLM.

Local models cannot invoke Codex skills.  This module treats installed skill
Markdown as a curated document library and exposes only the relevant, bounded
context for a song brief.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


CORE_GUIDES = (
    "mc-workflow",
    "lw-workflow",
    "mc-symbolic-score",
    "mc-render-compile",
    "lw-song-intent",
    "lw-structure",
    "lw-ai-tell-audit",
    "mc-ai-tell-audit",
)

STYLE_GUIDES = {
    "rock": "mc-style-rock-band",
    "band": "mc-style-rock-band",
    "j-pop": "mc-style-jpop",
    "jpop": "mc-style-jpop",
    "city pop": "mc-style-citypop-rnb",
    "r&b": "mc-style-citypop-rnb",
    "rnb": "mc-style-citypop-rnb",
    "hip-hop": "mc-style-hiphop",
    "hip hop": "mc-style-hiphop",
    "trap": "mc-style-hiphop",
    "rap": "lw-rap",
    "edm": "mc-style-edm",
    "club": "mc-style-edm",
    "jazz": "mc-style-jazz",
    "cinematic": "mc-style-cinematic",
    "orchestral": "mc-style-cinematic",
    "latin": "mc-style-latin",
    "mandarin pop": "mc-style-chinese-pop",
    "cantopop": "mc-style-chinese-pop",
}

LANGUAGE_GUIDES = {
    "mandarin": "lw-mandarin",
    "chinese": "lw-mandarin",
    "cantonese": "lw-cantonese",
    "japanese": "lw-japanese",
    "korean": "lw-korean",
    "english": "lw-english",
}

DETAIL_GUIDES = {
    "rhyme": "lw-rhyme",
    "imagery": "lw-imagery",
    "story": "lw-narrative",
    "narrative": "lw-narrative",
    "theatre": "lw-musical-theatre",
    "musical": "lw-musical-theatre",
    "harmony": "mc-harmony",
    "melody": "mc-melody",
    "groove": "mc-rhythm-groove",
    "orchestration": "mc-orchestration",
    "vocal": "mc-vocal-direction",
    "mix": "mc-mix-intent",
}


@dataclass(frozen=True)
class MusicDocumentContext:
    selected: tuple[str, ...]
    text: str
    missing: tuple[str, ...]


def skills_root() -> Path:
    configured = os.environ.get("MAESTRO_MUSIC_SKILLS_ROOT", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".agents" / "skills"


def select_music_guides(brief: str, *, language: str = "") -> tuple[str, ...]:
    haystack = f"{brief}\n{language}".casefold()
    selected = list(CORE_GUIDES)
    for keyword, guide in (*STYLE_GUIDES.items(), *LANGUAGE_GUIDES.items(), *DETAIL_GUIDES.items()):
        if keyword in haystack and guide not in selected:
            selected.append(guide)
    return tuple(selected)


def load_music_document_context(
    brief: str,
    *,
    language: str = "",
    root: Path | None = None,
    per_guide_chars: int = 5_000,
    total_chars: int = 28_000,
) -> MusicDocumentContext:
    root = root or skills_root()
    selected = select_music_guides(brief, language=language)
    chunks: list[str] = []
    missing: list[str] = []
    used = 0
    for name in selected:
        path = root / name / "SKILL.md"
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            missing.append(name)
            continue
        allowance = min(per_guide_chars, total_chars - used)
        if allowance <= 0:
            break
        excerpt = body[:allowance].rstrip()
        chunks.append(f"## {name}\n{excerpt}")
        used += len(excerpt)
    return MusicDocumentContext(selected, "\n\n".join(chunks), tuple(missing))


def composition_system_prompt(context: MusicDocumentContext) -> str:
    return (
        "You are the structured songwriting stage for a local YuE2 workflow. "
        "The reference material below is documentation, not executable tools. "
        "Use it as guidance and return exactly one JSON object with string fields "
        "style, lyrics, and abc. Keep lyrics outside ABC; ABC must contain V: Vocal "
        "and V: Ins voices and must not contain w: lyric lines. Make style concise "
        "and operational. Preserve the user's requested language and creative intent.\n\n"
        + context.text
    )
