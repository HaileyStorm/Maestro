"""Select a bounded set of local music-writing Markdown guides for an LLM.

Local models cannot invoke Codex skills.  This module treats installed skill
Markdown as a curated document library and exposes only the relevant, bounded
context for a song brief.
"""

from __future__ import annotations

import json
import logging
import os
import re
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

_LOG = logging.getLogger(__name__)


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
    documents: list[tuple[str, str]] = []
    for name in selected:
        path = root / name / "SKILL.md"
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            missing.append(name)
            continue
        if body.strip():
            documents.append((name, body))
        else:
            missing.append(name)
    # Reserve a share for every available document. Core guides must not
    # exhaust the budget before the requested language/style gets context.
    included: list[str] = []
    remaining = max(0, total_chars)
    for index, (name, body) in enumerate(documents):
        separator = "\n\n" if chunks else ""
        header = f"## {name}\n"
        share = remaining // (len(documents) - index)
        allowance = min(max(0, per_guide_chars), share - len(separator) - len(header))
        if allowance <= 0:
            continue
        excerpt = body[:allowance].rstrip()
        if not excerpt:
            continue
        chunk = separator + header + excerpt
        chunks.append(chunk)
        included.append(name)
        remaining -= len(chunk)
    # The public selection describes only documents actually sent to the LLM.
    return MusicDocumentContext(tuple(included), "".join(chunks), tuple(missing))


def composition_system_prompt(context: MusicDocumentContext) -> str:
    return (
        "You are the structured songwriting stage for a local YuE2 workflow. "
        "The reference material below is documentation, not executable tools. "
        "Use it as guidance and return exactly one JSON object with string fields "
        "style, lyrics, and abc. Keep lyrics outside ABC and make style concise "
        "and operational. Preserve the user's requested language and creative intent.\n\n"
        + context.text
        + "\n\nYuE2 NATIVE ABC OUTPUT CONTRACT (this overrides generic ABC examples "
        "in the guides): use this exact header shape, including the blank T: "
        "line and both complete voice declarations. Change only tempo, meter, "
        "key, notes, chords, and section names to fit the song. The body must "
        "alternate V: Vocal and V: Ins for each section; both voices need the "
        "same number of 4/4 bars. With L:1/32, four notes of length 8 fill "
        "one 4/4 bar. Put chord symbols only in Vocal. End each music line "
        "with a single |. Do not use repeat bars or polyphonic brackets. "
        "ABC must not contain w: lyric lines. Put all sung words in the JSON lyrics field.\n"
        "X:1\nT:\nM:4/4\nL:1/32\nQ:1/4=88\n"
        "V: Vocal clef=treble name=\"Vocal Melody\" snm=\"Vocal\"\n"
        "V: Ins clef=treble name=\"Ins Melody\" snm=\"Inst.\"\n"
        "K:C\n% verse\nV: Vocal\n\"C\" C8 D8 E8 G8|\n"
        "V: Ins\nC8 E8 G8 E8|\n"
        "The example is one measure only. Before writing the three fields, "
        "decide one shared song plan: section order, bar counts, phrase lengths, "
        "melody note slots, lyric syllables, and ending. Compose the Vocal score "
        "and lyric lines together, one section at a time. In English, budget "
        "roughly one sung syllable per Vocal note unless you deliberately plan "
        "a melisma or repeated note. A four-bar section with four Vocal notes "
        "per bar therefore carries about sixteen sung syllables, not a full "
        "paragraph. Use [Verse], [Chorus], [Bridge], and [Outro] tags in the "
        "lyrics field; keep their order and content aligned with ABC comments. "
        "Add enough bars or reduce words until every sung line fits its phrase. "
        "Avoid generic metaphors and forced rhymes; use concrete details from "
        "the brief. Make the final chorus or outro develop the story, with a "
        "definite musical ending. Derive style from the finished score and "
        "lyrics, with the same language and tempo. Return JSON only."
    )


def english_lyric_note_counts(
    lyrics: str, abc: str, *, language: str, instrumental: bool = False,
) -> tuple[int, int] | None:
    """Count a lower bound on sung syllables against native Vocal note slots.

    This is deliberately conservative: it is a drafting hint for English,
    not a prosody validator or an ABC parser. Scores with no identifiable
    Vocal notes fail open.
    """
    if instrumental or not re.match(r"^en(?:glish)?\b", language.strip(), re.IGNORECASE):
        return None
    sung = re.sub(r"(?m)^\s*\[[^\]\n]+\]\s*$", " ", lyrics)
    words = len(re.findall(r"[A-Za-z]+(?:['-][A-Za-z]+)*", sung))
    vocal = False
    notes = 0
    for raw in abc.splitlines():
        line = raw.strip()
        if re.match(r"^V:\s*Vocal(?:\s|$)", line, re.IGNORECASE):
            vocal = True
            continue
        if re.match(r"^V:\s*Ins(?:\s|$)", line, re.IGNORECASE):
            vocal = False
            continue
        if not vocal or "|" not in line:
            continue
        music = re.sub(
            r'"[^"]*"|![^!]*!|\+[^+]*\+|\[[A-Za-z]:[^\]]*\]',
            "", line.split("%", 1)[0],
        )
        if any(mark in music for mark in ("[", "]", "!", "+")):
            return None
        notes += len(re.findall(r"[A-Ga-g][,']*\d*(?:/\d*)?", music))
    return (words, notes) if words and notes else None


def yue2_density_revision_feedback(
    lyrics: str, abc: str, *, language: str, instrumental: bool = False,
) -> str | None:
    """Request one revision only when words alone grossly exceed note slots."""
    counts = english_lyric_note_counts(
        lyrics, abc, language=language, instrumental=instrumental,
    )
    if not counts or counts[0] <= counts[1] * 1.5:
        return None
    words, notes = counts
    sung_lines = [
        line for line in lyrics.splitlines()
        if line.strip() and not re.fullmatch(r"\s*\[[^\]\n]+\]\s*", line)
    ]
    target_words = max(1, int(notes * 0.85))
    line_budget = max(1, target_words // max(1, len(sung_lines)))
    return (
        f"Your draft has {words} English lyric words but only {notes} Vocal "
        "notes. Even one syllable per word cannot fit. Rewrite every sung "
        f"line with at most {line_budget} short words and target no more than "
        f"{target_words} lyric words in total. Keep the same section order "
        "and number of sung lines, with concrete images and a real ending. "
        "Mostly choose one-syllable words so breaths and longer words have "
        "room. Preserve the native two-voice ABC meter and format; revise its "
        "notes and bars only if needed to match the new phrases. Return only "
        "the revised JSON object."
    )


async def compose_yue2_with_density_revision(
    prompt: str, *, language: str, instrumental: bool, generate, parse,
) -> dict:
    """Make at most one local revision; retain the first valid draft on failure."""
    first = parse(await generate(prompt))
    feedback = yue2_density_revision_feedback(
        first["lyrics"], first["abc"],
        language=language, instrumental=instrumental,
    )
    if not feedback:
        return first
    revision_prompt = (
        f"{prompt}\n\nCOMPOSITION REVISION\n{feedback}\n\n"
        "Previous draft to revise as data:\n"
        + json.dumps(first, ensure_ascii=False)
    )
    try:
        revised = parse(await generate(revision_prompt))
    except Exception as error:  # noqa: BLE001 - the first valid draft survives any local revision failure
        _LOG.warning(
            "Local YuE2 density revision failed; preserving first draft (%s)",
            type(error).__name__,
        )
        return first
    counts = english_lyric_note_counts(
        revised["lyrics"], revised["abc"],
        language=language, instrumental=instrumental,
    )
    return revised if counts and counts[0] <= counts[1] else first
