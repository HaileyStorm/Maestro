"""Deterministic MiniMax H3 dialogue compilation and validation.

H3 treats ``<d>[Language] ...</d>`` blocks as an audio-generation contract.
These helpers keep that contract out of best-effort prompt rewriting: structured
Director dialogue is canonical, existing blocks are replaced as whole units,
and malformed or contradictory prompts are rejected before GPU generation.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from services.text_integrity import repair_text


class H3DialogueContractError(ValueError):
    """Raised when an H3 prompt cannot be made safe for native speech."""


_H3_DIALOGUE_TOKEN_RE = re.compile(r"<\s*(/?)\s*d\s*>", re.IGNORECASE)
_H3_STRICT_DIALOGUE_RE = re.compile(
    r"<d>\s*\[([^\]\r\n]+)\]\s+(.+?)\s*</d>",
    re.IGNORECASE | re.DOTALL,
)
_H3_VOCAL_SECTION_RE = re.compile(
    r"\s*(?:DIALOGUE AND VOCAL PERFORMANCE|"
    r"SILENCE AND VOCAL PERFORMANCE)\s*:.*?"
    r"(?=\b(?:FINAL BLOCKING|overall_soundscape|"
    r"non_diegetic_music)\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_SOUND_BOUNDARY_RE = re.compile(
    r"\b(?:overall_soundscape|non_diegetic_music)\s*:",
    re.IGNORECASE,
)
_H3_SPEECHLIKE_AMBIENCE_REPLACEMENTS = (
    (
        re.compile(
            r"\b(?:(?:coffee\s*shop|cafe|restaurant|office|party|crowd|"
            r"background)\s+)?chatter\b",
            re.IGNORECASE,
        ),
        "nonverbal room tone",
    ),
    (
        re.compile(
            r"\b(?:indistinct|distant|background|murmuring|soft|low)\s+"
            r"(?:voices?|conversations?|talking|speech)\b",
            re.IGNORECASE,
        ),
        "nonverbal room tone",
    ),
)

_H3_REF2VA_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
# Base H3 now carries the same stable subject namespace used by Ref2VA, but
# keeps its three native audiovisual fields. The definitions are global
# metadata; shot records reference the labels and add only local state.
_H3_BASE_FIELDS = (
    "subject_definitions",
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_H3_ALL_FIELDS = tuple(dict.fromkeys((*_H3_REF2VA_FIELDS, *_H3_BASE_FIELDS)))
_H3_SUBJECT_LABEL_RE = re.compile(
    r"<Subject\s+(?P<number>[1-9]\d*)>", re.IGNORECASE,
)
_H3_REFERENCE_LABEL_RE = re.compile(
    r"<(?:Subject|Picture|Video|Audio)\s+[1-9]\d*>", re.IGNORECASE,
)
_H3_NO_SUBJECT_DEFINITIONS = (
    "No separately named subjects were authored; shot records carry only "
    "the request's explicitly described visible action and setting."
)
_H3_CANONICAL_TIME = r"(?:(?:\d{1,2}:){1,2})?\d+(?:\.\d+)?"
_H3_CANONICAL_RECORD_RE = re.compile(
    rf"^\[Shot (?P<number>[1-9]\d*)\] "
    rf"\[(?P<start>{_H3_CANONICAL_TIME})s-(?P<end>{_H3_CANONICAL_TIME})s\] "
    r"shot_name: (?P<name>[^|\r\n]+) \| "
    r"audiovisual_description: (?P<description>[^|\r\n]+) \| "
    r"dialogue_and_vocalizations: (?P<vocals>[^\r\n]+)$",
)
_H3_TOP_LEVEL_LABEL_RE = re.compile(
    r"(?mi)^\s*([A-Za-z][A-Za-z0-9_ ]{1,64})\s*:"
)
_H3_CUSTOM_SECTION_RE = re.compile(
    r"\s+(?:OPENING CONTINUITY|FINAL BLOCKING|"
    r"DIALOGUE AND VOCAL PERFORMANCE|SILENCE AND VOCAL PERFORMANCE)\s*:.*?"
    r"(?=\s+(?:OPENING CONTINUITY|FINAL BLOCKING|"
    r"DIALOGUE AND VOCAL PERFORMANCE|SILENCE AND VOCAL PERFORMANCE)\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_SOUND_PROSE_RE = re.compile(
    r"\b(?:the\s+)?soundscape\s+(?:is\s+dominated\s+by|includes|contains|is)\s+"
    r"(.+?)(?=\s+(?:non-diegetic\s+music|the\s+final\s+beat|"
    r"closing\s+blocking|opening\s+continuity|final\s+blocking)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_MUSIC_PROSE_RE = re.compile(
    r"\bnon-diegetic\s+music\s+is\s+(.+?)"
    r"(?=\s+(?:the\s+final\s+beat|closing\s+blocking|"
    r"opening\s+continuity|final\s+blocking)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_H3_AUXILIARY_SECTION_BOUNDARY_RE = re.compile(
    r"\b(?:integrated_multimodal_description|detailed_description|"
    r"overall_soundscape|non_diegetic_music|dialogue\s+beats?)\s*:",
    re.IGNORECASE,
)
_H3_META_SENTENCE_RE = re.compile(
    r"\bMiniMax H3 generates synchronized picture and stereo sound\.\s*",
    re.IGNORECASE,
)
_H3_MOJIBAKE_REPLACEMENTS = {
    "\u00e2\u0080\u0098": "\u2018",
    "\u00e2\u0080\u0099": "\u2019",
    "\u00e2\u0080\u009c": "\u201c",
    "\u00e2\u0080\u009d": "\u201d",
    "\u00e2\u0080\u0093": "\u2013",
    "\u00e2\u0080\u0094": "\u2014",
    "\u00e2\u0080\u00a6": "\u2026",
    "\u00e2\u20ac\u02dc": "\u2018",
    "\u00e2\u20ac\u2122": "\u2019",
    "\u00e2\u20ac\u0153": "\u201c",
    "\u00e2\u20ac\u009d": "\u201d",
    "\u00e2\u20ac\u201c": "\u2013",
    "\u00e2\u20ac\u201d": "\u2014",
    "\u00e2\u20ac\u00a6": "\u2026",
    "\u00c2\u00a0": " ",
    # Some older Windows JSON round-trips decoded the leading UTF-8 byte for
    # punctuation as U+0101 instead of U+00E2 before preserving the two C1
    # continuation bytes. Repair that observed Maestro variant as well.
    "\u0101\u0080\u0098": "\u2018",
    "\u0101\u0080\u0099": "\u2019",
    "\u0101\u0080\u009c": "\u201c",
    "\u0101\u0080\u009d": "\u201d",
    "\u0101\u0080\u0093": "\u2013",
    "\u0101\u0080\u0094": "\u2014",
    "\u0101\u0080\u00a6": "\u2026",
}


def _field(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _normalized_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _dialogue_spans(prompt: str) -> tuple[list[tuple[int, int]], bool]:
    """Return balanced top-level dialogue spans and whether markup was bad."""

    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    malformed = False
    for token in _H3_DIALOGUE_TOKEN_RE.finditer(prompt or ""):
        closing = bool(token.group(1))
        if not closing:
            if depth == 0:
                start = token.start()
            else:
                malformed = True
            depth += 1
            continue
        if depth == 0:
            malformed = True
            continue
        depth -= 1
        if depth == 0 and start >= 0:
            spans.append((start, token.end()))
            start = -1
    if depth:
        malformed = True
    return spans, malformed


def _replace_spans(
    prompt: str,
    spans: Sequence[tuple[int, int]],
    replacements: Sequence[str],
) -> str:
    result = prompt
    for (start, end), replacement in reversed(list(zip(spans, replacements))):
        result = f"{result[:start]}{replacement}{result[end:]}"
    return result


def _strip_dialogue_for_driving_audio(prompt: str) -> str:
    """Remove generated dialogue when mapped audio owns every audible vocal.

    Music-video planning sometimes receives a noisy transcription containing a
    repeated refrain. If an LLM copies that transcript into a ``<d>`` block and
    reaches its output limit before ``</d>``, the visual plan is still safe to
    use: the mapped soundtrack, rather than prompt-authored speech, is the vocal
    source. Remove balanced blocks, malformed nested blocks, unmatched closing
    tags, and an unterminated final block without altering surrounding visual
    instructions.
    """

    text = str(prompt or "")
    pieces: list[str] = []
    cursor = 0
    depth = 0
    open_start = -1
    for token in _H3_DIALOGUE_TOKEN_RE.finditer(text):
        closing = bool(token.group(1))
        if not closing:
            if depth == 0:
                pieces.append(text[cursor:token.start()])
                open_start = token.start()
            depth += 1
            continue
        if depth:
            depth -= 1
            if depth == 0:
                cursor = token.end()
                open_start = -1
            continue

        # A closing tag without an opening tag is markup noise. Preserve the
        # prose around it, but do not leave the invalid token in the prompt.
        pieces.append(text[cursor:token.start()])
        cursor = token.end()

    if depth:
        # Preserve official sound/music fields if malformed raw text includes
        # them after an unterminated dialogue block. Context-IR parsing usually
        # separates these fields before this helper runs, but this also makes
        # the recovery safe for legacy free-form prompts.
        boundary = _H3_SOUND_BOUNDARY_RE.search(text, max(0, open_start))
        cursor = boundary.start() if boundary else len(text)
    pieces.append(text[cursor:])
    return _normalized_space(" ".join(pieces))


def _dialogue_payload(value: Any) -> tuple[str, str]:
    """Return ``(language, words)`` from plain or nested H3 dialogue."""

    text = _H3_DIALOGUE_TOKEN_RE.sub("", str(value or "")).strip()
    language = "English"
    language_prefix = re.match(r"^\[([^\]]+)\]\s*(.*)$", text, re.DOTALL)
    if language_prefix:
        language = language_prefix.group(1).strip() or language
        text = language_prefix.group(2).strip()
    if not text:
        raise H3DialogueContractError("MiniMax H3 dialogue contains an empty line.")
    return language, text


def h3_dialogue_tag(spoken_text: Any) -> str:
    """Build exactly one canonical H3 dialogue block."""

    language, words = _dialogue_payload(spoken_text)
    return f"<d>[{language}] {words}</d>"


def _replace_first_outside_dialogue(
    prompt: str,
    needle: str,
    replacement: str,
) -> tuple[str, bool]:
    """Replace an exact plain-text line without touching existing H3 tags."""

    if not needle:
        return prompt, False
    spans, malformed = _dialogue_spans(prompt)
    if malformed:
        return prompt, False
    cursor = 0
    for start, end in [*spans, (len(prompt), len(prompt))]:
        found = prompt.find(needle, cursor, start)
        if found >= 0:
            return (
                f"{prompt[:found]}{replacement}{prompt[found + len(needle):]}",
                True,
            )
        cursor = end
    return prompt, False


def _rewrite_outside_dialogue(prompt: str, rewrite) -> str:
    spans, malformed = _dialogue_spans(prompt)
    if malformed:
        raise H3DialogueContractError(
            "MiniMax H3 dialogue tags are unbalanced after compilation."
        )
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(rewrite(prompt[cursor:start]))
        parts.append(prompt[start:end])
        cursor = end
    parts.append(rewrite(prompt[cursor:]))
    return "".join(parts)


def _sanitize_scripted_ambience(prompt: str) -> str:
    """Remove requests for synthetic background speech around exact lines."""

    def sanitize(segment: str) -> str:
        for pattern, replacement in _H3_SPEECHLIKE_AMBIENCE_REPLACEMENTS:
            segment = pattern.sub(replacement, segment)
        return re.sub(
            r"\bnonverbal room tone(?:\s*(?:,|and)\s*nonverbal room tone)+\b",
            "nonverbal room tone",
            segment,
            flags=re.IGNORECASE,
        )

    return _rewrite_outside_dialogue(prompt, sanitize)


def _insert_visual_detail(prompt: str, label: str, detail: str) -> str:
    prompt = str(prompt or "").strip()
    detail = _normalized_space(detail)
    statement = f"{label}: {detail}"
    suffix = "" if statement.endswith((".", "!", "?")) else "."
    boundary = _H3_SOUND_BOUNDARY_RE.search(prompt)
    if boundary:
        return (
            f"{prompt[:boundary.start()].rstrip()} {statement}{suffix} "
            f"{prompt[boundary.start():].lstrip()}"
        ).strip()
    return f"{prompt} {statement}{suffix}".strip()


def _speaker_map(subjects: Iterable[Any]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for index, subject in enumerate(subjects or []):
        character_id = _normalized_space(_field(subject, "character_id", ""))
        speaker_name = _normalized_space(
            _field(subject, "speaker_name", "")
            or _field(subject, "visual_description", "")
            or character_id
            or f"visible subject {index + 1}"
        )
        stable_id = f"(S{index + 1})"
        if character_id:
            result[character_id] = (stable_id, speaker_name)
            result[character_id.casefold()] = (stable_id, speaker_name)
        result.setdefault(speaker_name.casefold(), (stable_id, speaker_name))
    return result


def validate_h3_vocal_contract(
    prompt: str,
    dialogue_beats: Sequence[Any] | None = None,
) -> list[str]:
    """Return structural/semantic H3 dialogue errors for a final prompt."""

    prompt = normalize_h3_text(prompt)
    spans, malformed = _dialogue_spans(prompt)
    errors: list[str] = []
    if malformed:
        errors.append("dialogue tags are nested or unbalanced")
    blocks = [prompt[start:end] for start, end in spans]
    actual_words: list[str] = []
    for index, block in enumerate(blocks):
        strict = _H3_STRICT_DIALOGUE_RE.fullmatch(block.strip())
        if not strict or _H3_DIALOGUE_TOKEN_RE.search(strict.group(2) if strict else ""):
            errors.append(f"dialogue block {index + 1} is not canonical")
            continue
        actual_words.append(_normalized_space(strict.group(2)))

    expected_words = []
    for beat in dialogue_beats or []:
        spoken = normalize_h3_text(_field(beat, "spoken_text", ""))
        if _normalized_space(spoken):
            try:
                expected_words.append(_normalized_space(_dialogue_payload(spoken)[1]))
            except H3DialogueContractError as exc:
                errors.append(str(exc))

    if expected_words:
        if len(blocks) != len(expected_words):
            errors.append(
                f"expected {len(expected_words)} dialogue block(s), found {len(blocks)}"
            )
        if actual_words != expected_words:
            errors.append("dialogue words or speaker order differ from dialogue_beats")
    elif not blocks:
        has_silence_contract = bool(re.search(
            r"\bno (?:one|character) speaks\b",
            prompt,
            flags=re.IGNORECASE,
        ))
        has_mapped_audio_contract = (
            "mapped driving audio" in prompt.casefold()
            and "do not generate additional dialogue" in prompt.casefold()
        )
        if not (has_silence_contract or has_mapped_audio_contract):
            errors.append("silent prompt has no explicit H3 silence contract")
    return errors


def compile_h3_vocal_contract(
    prompt: str,
    subjects: Sequence[Any] | None,
    dialogue_beats: Sequence[Any] | None,
) -> tuple[str, str]:
    """Compile a final, idempotent H3 speech/silence contract.

    Existing dialogue is replaced by *whole top-level blocks*. Balanced nested
    blocks from older Maestro projects are therefore repaired without ever
    performing the unsafe ``prompt.replace(spoken_text, tag)`` operation that
    originally split sentences and produced gibberish.
    """

    prompt = str(prompt or "").strip()
    valid_beats: list[tuple[Any, str, str]] = []
    for beat in dialogue_beats or []:
        spoken = _field(beat, "spoken_text", "")
        if not _normalized_space(spoken):
            continue
        _, words = _dialogue_payload(spoken)
        valid_beats.append((beat, words, h3_dialogue_tag(spoken)))

    # Older saved projects do not carry structured dialogue metadata. If they
    # already contain a vocal section, preserve its speaker assignments and
    # repair the full prompt in place instead of deleting the only copy of a
    # line that had been appended inside that section.
    existing_vocal_section = _H3_VOCAL_SECTION_RE.search(prompt)
    if not valid_beats and existing_vocal_section:
        spans, malformed = _dialogue_spans(prompt)
        if malformed and not spans:
            raise H3DialogueContractError(
                "MiniMax H3 dialogue tags are unbalanced and cannot be repaired safely."
            )
        if spans:
            prompt = _replace_spans(
                prompt,
                spans,
                [h3_dialogue_tag(prompt[start:end]) for start, end in spans],
            )
            prompt = _sanitize_scripted_ambience(prompt)
        _, remains_malformed = _dialogue_spans(prompt)
        if remains_malformed:
            raise H3DialogueContractError(
                "MiniMax H3 dialogue tags remain unbalanced after repair."
            )
        errors = validate_h3_vocal_contract(prompt, [])
        if errors:
            raise H3DialogueContractError(
                "Invalid MiniMax H3 vocal contract: " + "; ".join(errors)
            )
        contract_match = _H3_VOCAL_SECTION_RE.search(prompt)
        contract = _normalized_space(
            contract_match.group(0) if contract_match else ""
        )
        return prompt, contract

    prompt = _H3_VOCAL_SECTION_RE.sub(" ", prompt).strip()
    spans, malformed = _dialogue_spans(prompt)
    if malformed and not spans:
        raise H3DialogueContractError(
            "MiniMax H3 dialogue tags are unbalanced and cannot be repaired safely."
        )

    instructions: list[str] = []
    if valid_beats:
        replacements = [
            valid_beats[index][2] if index < len(valid_beats) else ""
            for index in range(len(spans))
        ]
        prompt = _replace_spans(prompt, spans, replacements)
        _, remains_malformed = _dialogue_spans(prompt)
        if remains_malformed:
            raise H3DialogueContractError(
                "MiniMax H3 dialogue tags remain unbalanced after repair."
            )

        mapped = _speaker_map(subjects or [])
        for index in range(len(spans), len(valid_beats)):
            beat, words, tag = valid_beats[index]
            prompt, replaced = _replace_first_outside_dialogue(
                prompt, words, tag,
            )
            if replaced:
                continue
            speaker_id = _normalized_space(_field(beat, "speaker_id", ""))
            stable_id, speaker_name = mapped.get(
                speaker_id,
                mapped.get(
                    speaker_id.casefold(),
                    (speaker_id or "(S1)", speaker_id or "the visible speaker"),
                ),
            )
            delivery = _normalized_space(_field(beat, "delivery", ""))
            physical_cue = _normalized_space(_field(beat, "physical_cue", ""))
            delivery_text = f" with a {delivery} delivery" if delivery else ""
            instruction = (
                f"{stable_id} {speaker_name} speaks{delivery_text}: {tag}."
            )
            if physical_cue:
                instruction += f" While speaking, {physical_cue}"
            instructions.append(instruction)

        prompt = _sanitize_scripted_ambience(prompt)
        guard = (
            "Only these explicitly tagged lines are spoken, once each and in "
            "the listed order. Do not add, paraphrase, repeat, or improvise "
            "dialogue, muttering, murmuring, gibberish, or speech-like "
            "vocalizations. No background or crowd voices are audible. Anyone "
            "not currently delivering a tagged line remains silent with their "
            "mouth closed except for explicitly described nonverbal actions."
        )
        detail = " ".join([*instructions, guard])
        label = "DIALOGUE AND VOCAL PERFORMANCE"
    elif spans:
        replacements = [h3_dialogue_tag(prompt[start:end]) for start, end in spans]
        prompt = _replace_spans(prompt, spans, replacements)
        _, remains_malformed = _dialogue_spans(prompt)
        if remains_malformed:
            raise H3DialogueContractError(
                "MiniMax H3 dialogue tags remain unbalanced after repair."
            )
        prompt = _sanitize_scripted_ambience(prompt)
        detail = (
            "Only the explicitly tagged dialogue already present in this "
            "prompt is spoken. Do not add, paraphrase, repeat, or improvise "
            "any other words, muttering, murmuring, gibberish, or speech-like "
            "vocalizations. No background or crowd voices are audible. "
            "Non-speaking characters keep their mouths closed."
        )
        label = "DIALOGUE AND VOCAL PERFORMANCE"
    else:
        detail = (
            "No one speaks in this shot. All visible people remain silent and "
            "keep their mouths closed except for explicitly described nonverbal "
            "actions. Generate no muttering, murmuring, gibberish, invented "
            "words, background voices, or speech-like vocalizations."
        )
        label = "SILENCE AND VOCAL PERFORMANCE"

    prompt = _insert_visual_detail(prompt, label, detail)
    errors = validate_h3_vocal_contract(prompt, dialogue_beats)
    if errors:
        raise H3DialogueContractError(
            "Invalid MiniMax H3 vocal contract: " + "; ".join(errors)
        )
    return prompt, f"{label}: {detail}"


def h3_dialogue_budget_violations(
    shot_dicts: Sequence[Mapping[str, Any]],
    durations: Sequence[float] | None = None,
    *,
    words_per_second: float = 2.1,
) -> list[dict[str, Any]]:
    """Describe shots whose complete dialogue cannot fit their duration."""

    violations: list[dict[str, Any]] = []
    for index, shot in enumerate(shot_dicts):
        try:
            duration = float(
                durations[index]
                if durations is not None and index < len(durations)
                else shot.get("duration_sec") or 0
            )
        except (TypeError, ValueError):
            duration = 0.0
        word_count = 0
        for beat in shot.get("dialogue_beats") or []:
            spoken = _field(beat, "spoken_text", "")
            if not _normalized_space(spoken):
                continue
            try:
                words = _dialogue_payload(spoken)[1]
            except H3DialogueContractError:
                words = str(spoken or "")
            word_count += len(words.split())
        budget = max(1, int(math.floor(max(0.0, duration) * words_per_second)))
        if word_count > budget:
            violations.append({
                "index": index,
                "title": _normalized_space(shot.get("title") or f"Shot {index + 1}"),
                "duration_sec": duration,
                "word_count": word_count,
                "word_budget": budget,
            })
    return violations


def normalize_h3_text(value: Any) -> str:
    """Repair common UTF-8 mojibake before H3 tokenization.

    Saved Director projects created through a Windows code-page boundary can
    contain the three code points for a UTF-8 punctuation byte sequence (for
    example ``\u00e2\u0080\u0099``) instead of the intended apostrophe.  The
    C1 control bytes are especially harmful in literal dialogue, so repair the
    known sequences and remove any orphaned controls deterministically.
    """

    return repair_text(value)


def _extract_h3_fields(text: str) -> dict[str, str]:
    """Extract known Context-IR fields even from legacy one-line prompts."""

    matches = list(re.finditer(
        r"(?i)(?<![A-Za-z0-9_])(" + "|".join(
            re.escape(field) for field in _H3_ALL_FIELDS
        ) + r")\s*:",
        text,
    ))
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1).lower()
        if name in fields:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        fields[name] = text[match.end():end].strip()
    return fields


def _parse_h3_subject_definitions(value: str) -> list[dict[str, str]]:
    """Parse the Base global entity namespace without interpreting shot prose."""

    text = normalize_h3_text(value).strip()
    if not text or text.casefold() == _H3_NO_SUBJECT_DEFINITIONS.casefold():
        return []
    chunks = re.split(r"(?=<Subject\s+[1-9]\d*>\s*)", text, flags=re.IGNORECASE)
    entries: list[dict[str, str]] = []
    for chunk in chunks:
        chunk = chunk.strip().strip(";,")
        if not chunk:
            continue
        label = _H3_SUBJECT_LABEL_RE.match(chunk)
        if label is None:
            continue
        number = int(label.group("number"))
        rest = chunk[label.end():].strip().strip(";,")
        if rest.casefold().startswith("is "):
            rest = rest[3:].strip()
        identity, separator, description = rest.partition(":")
        # Base examples in the wild often use ``Name, description`` instead
        # of the preferred ``Name: description`` form. Treat only a
        # substantial comma suffix as a description so a plain multi-word
        # identity remains an identity rather than becoming repetition bait.
        if not separator and "," in rest:
            comma_identity, comma_description = rest.split(",", 1)
            if len(comma_description.split()) >= 2:
                identity = comma_identity
                description = comma_description
                separator = ","
        identity = identity.strip().strip(" .;,")
        description = (
            description.strip().strip(" .;,")
            if separator else ""
        )
        entries.append({
            "label": f"<Subject {number}>",
            "number": str(number),
            "identity": identity,
            "description": description,
            "has_description": bool(separator and description),
        })
    return entries


def _h3_subject_identity_aliases(entry: Mapping[str, str]) -> list[str]:
    """Return only authored identity aliases, never inferred descriptions."""

    identity = " ".join(str(entry.get("identity") or "").split()).strip()
    if not identity:
        return []
    aliases: list[str] = []
    # Stable speaker IDs and reference provenance are metadata, not part of
    # the visible name a shot needs to repeat.
    without_speaker = re.sub(
        r"\s*\((?:S\d+|speaker\s+\d+)\)\s*$",
        "",
        identity,
        flags=re.IGNORECASE,
    ).strip()
    without_reference = re.sub(
        r"\s+from\s+<Picture\s+\d+>\s*$",
        "",
        without_speaker or identity,
        flags=re.IGNORECASE,
    ).strip()
    if without_reference:
        aliases.append(without_reference)
    if without_speaker and without_speaker not in aliases:
        aliases.append(without_speaker)
    if identity not in aliases:
        aliases.append(identity)
    return aliases


def _h3_entity_contract_errors(prompt: str, *, mode: str) -> list[str]:
    """Enforce Base's global entity namespace and non-repetition rule."""

    if str(mode or "").strip().casefold() == "ref2va":
        return []
    fields = _extract_h3_fields(prompt)
    definitions = fields.get("subject_definitions", "").strip()
    if not definitions:
        return ["Base H3 prompt is missing subject_definitions"]
    visual = fields.get("integrated_multimodal_description", "")
    visual_labels = {
        f"<Subject {number}>".casefold()
        for number in re.findall(r"<Subject\s+([1-9]\d*)>", visual, re.IGNORECASE)
    }
    if definitions.casefold() == _H3_NO_SUBJECT_DEFINITIONS.casefold():
        return [
            "subject_definitions sentinel is orphaned by shot-record Subject labels"
        ] if visual_labels else []
    entries = _parse_h3_subject_definitions(definitions)
    if not entries:
        return ["subject_definitions contains no parseable stable Subject labels"]
    errors: list[str] = []
    numbers = [int(entry["number"]) for entry in entries]
    if len(numbers) != len(set(numbers)):
        errors.append("subject_definitions labels are not unique")
    visual_compact = " ".join(visual.split()).casefold()
    record_compacts = [
        " ".join(line.split()).casefold()
        for line in str(visual or "").splitlines()
        if _H3_CANONICAL_RECORD_RE.fullmatch(line.strip())
    ]
    defined_labels = {entry["label"].casefold() for entry in entries}
    for label in sorted(visual_labels - defined_labels):
        errors.append(f"shot record references undefined {label}")
    for entry in entries:
        label = entry["label"]
        aliases = _h3_subject_identity_aliases(entry)
        referenced = label.casefold() in visual_labels or any(
            re.search(
                rf"(?<![\w>]){re.escape(alias.casefold())}(?![\w<])",
                visual_compact,
            )
            for alias in aliases
            if alias
        )
        if not referenced:
            errors.append(f"{label} is never referenced by a shot record")
        description = " ".join(entry["description"].split()).strip()
        # Only an explicitly delimited description can be repeated, so a
        # multi-word identity (for example, "The Very Red Fox") remains
        # exempt. Long exact descriptions are rejected anywhere in a record
        # that also names the entity; short phrases require syntactic adjacency
        # so "Mara walks past a tall tree" does not describe Mara as "tall".
        if description and entry.get("has_description"):
            normalized_description = " ".join(description.split()).casefold()
            anchors = [label, *aliases]
            for record in record_compacts:
                matching_anchors = [
                    anchor for anchor in anchors
                    if re.search(
                        rf"(?<![\w>]){re.escape(anchor.casefold())}(?![\w<])",
                        record,
                    )
                ]
                if not matching_anchors:
                    continue
                repeated = (
                    len(description.split()) >= 4
                    and re.search(
                        rf"(?<![\w]){re.escape(normalized_description)}(?![\w])",
                        record,
                    )
                )
                if not repeated:
                    for anchor in matching_anchors:
                        for match in re.finditer(
                            re.escape(anchor.casefold()), record,
                        ):
                            window = record[match.end():match.end() + 220]
                            if re.match(
                                r"\s*(?:\([^)]{1,96}\)\s*)?"
                                r"(?:[,;:–—-]\s*|(?:is|appears as)\s+)?"
                                + re.escape(normalized_description)
                                + r"(?![\w])",
                                window,
                            ):
                                repeated = True
                                break
                        if repeated:
                            break
                if repeated:
                    errors.append(
                        f"{label} full entity description is repeated in a shot record"
                    )
                    break
    return list(dict.fromkeys(errors))


def _canonical_h3_record_lines(body: str) -> list[str]:
    """Return exact non-empty physical record lines, or an empty list."""

    lines = [line.strip() for line in str(body or "").splitlines() if line.strip()]
    if not lines or not all(_H3_CANONICAL_RECORD_RE.fullmatch(line) for line in lines):
        return []
    return lines


def _normalize_h3_record_body(body: str) -> str:
    """Normalize outer whitespace without flattening canonical shot records."""

    lines = _canonical_h3_record_lines(body)
    return "\n".join(lines) if lines else _normalized_space(body)


def _augment_h3_record_description(
    body: str,
    addition: str,
    *,
    last: bool = False,
) -> str:
    """Add deterministic continuity prose inside one canonical record."""

    lines = _canonical_h3_record_lines(body)
    addition = _trim_sentence(addition)
    if not lines or not addition:
        return body
    index = len(lines) - 1 if last else 0
    match = _H3_CANONICAL_RECORD_RE.fullmatch(lines[index])
    if match is None:
        return body
    description = match.group("description").strip()
    description = (
        f"{description.rstrip(' .')} {addition}."
        if last else f"{addition}. {description}"
    )
    lines[index] = (
        f"[Shot {match.group('number')}] "
        f"[{match.group('start')}s-{match.group('end')}s] "
        f"shot_name: {match.group('name').strip()} | "
        f"audiovisual_description: {description.strip()} | "
        f"dialogue_and_vocalizations: {match.group('vocals').strip()}"
    )
    return "\n".join(lines)


def _seconds_from_h3_token(value: str) -> float | None:
    try:
        parts = [float(part) for part in str(value or "").split(":")]
    except (TypeError, ValueError):
        return None
    if not parts or len(parts) > 3 or any(part < 0 for part in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60.0 + part
    return seconds


def validate_h3_context_ir_records(
    prompt: str,
    *,
    mode: str = "t2va",
    duration_seconds: float | None = None,
) -> list[str]:
    """Validate H3 fields and physical records through the production parser.

    This is deliberately structural. It does not judge subject matter or ask a
    model to interpret the prompt. Literal record text remains authoritative.
    """

    text = normalize_h3_text(prompt).strip()
    mode = str(mode or "t2va").strip().lower()
    expected = _H3_REF2VA_FIELDS if mode == "ref2va" else _H3_BASE_FIELDS
    errors: list[str] = []
    positions: list[int] = []
    for field in expected:
        matches = list(re.finditer(rf"(?mi)^\s*{re.escape(field)}\s*:", text))
        if len(matches) != 1:
            errors.append(f"expected one {field} field, found {len(matches)}")
        else:
            positions.append(matches[0].start())
    if len(positions) == len(expected) and positions != sorted(positions):
        errors.append("Context-IR fields are out of order")
    for field in set(_H3_ALL_FIELDS) - set(expected):
        if re.search(rf"(?mi)^\s*{re.escape(field)}\s*:", text):
            errors.append(f"unexpected {field} field for {mode}")

    fields = _extract_h3_fields(text)
    if len(positions) == len(expected):
        first_field = re.search(
            rf"(?mi)^\s*{re.escape(expected[0])}\s*:", text,
        )
        prefix = text[:first_field.start()].strip() if first_field else ""
        prefix_is_one_line = bool(prefix) and "\n" not in prefix and "\r" not in prefix
        if mode in {"t2va", "ref2va"} and prefix:
            errors.append("unexpected wrapper text precedes the first Context-IR field")
        elif mode == "i2va" and not (
            prefix_is_one_line
            and prefix.startswith(
                "For the target video, at 0.00 seconds into the target video, "
                "<Picture 1>"
            )
            and prefix.endswith("is fully referenced.")
        ):
            errors.append("I2VA alignment header is missing or contains wrapper text")
        elif mode in {"fl2va", "l2va"} and not (
            prefix_is_one_line
            and prefix.startswith("How the reference pictures align with the target video")
            and prefix.endswith("mark of the target video.")
        ):
            errors.append(
                f"{mode.upper()} alignment header is missing or contains wrapper text"
            )

    expected_labels = {field.casefold() for field in expected}
    for match in _H3_TOP_LEVEL_LABEL_RE.finditer(text):
        if match.group(1).strip().casefold() not in expected_labels:
            errors.append(f"unexpected top-level field {match.group(1).strip()}")
    for field in expected:
        value = fields.get(field, "").strip()
        if not value:
            errors.append(f"{field} field is empty")
    for field in ("overall_soundscape", "non_diegetic_music"):
        value = fields.get(field, "").strip()
        if value and len(value.splitlines()) != 1:
            errors.append(f"{field} must be exactly one physical line")
    errors.extend(_h3_entity_contract_errors(text, mode=mode))

    visual_name = (
        "detailed_description" if mode == "ref2va"
        else "integrated_multimodal_description"
    )
    visual = fields.get(visual_name, "")
    lines = [line.strip() for line in visual.splitlines() if line.strip()]
    matches = [_H3_CANONICAL_RECORD_RE.fullmatch(line) for line in lines]
    if not lines or not all(matches):
        errors.append(
            f"{visual_name} must contain only canonical physical-line shot records"
        )
        return list(dict.fromkeys(errors))

    typed_matches = [match for match in matches if match is not None]
    numbers = [int(match.group("number")) for match in typed_matches]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append("shot record numbers are not sequential")

    # Use the same parser that drives global-timeline execution, but isolate
    # the visual field so Context-IR metadata cannot be mistaken for events.
    from shared.utils.prompt_parser import parse_global_timeline_prompt

    global_lines, events = parse_global_timeline_prompt(visual)
    if global_lines or len(events) != len(lines) or any(
        event.get("kind") != "range" for event in events
    ):
        errors.append("production timeline parser did not recognize every canonical record")

    ranges: list[tuple[float, float]] = []
    for match in typed_matches:
        start = _seconds_from_h3_token(match.group("start"))
        end = _seconds_from_h3_token(match.group("end"))
        if start is None or end is None or end <= start:
            errors.append(f"Shot {match.group('number')} has an invalid time range")
            continue
        ranges.append((start, end))
    if ranges:
        if not math.isclose(ranges[0][0], 0.0, abs_tol=1e-6):
            errors.append("first shot record does not start at 0.00 seconds")
        for previous, current in zip(ranges, ranges[1:]):
            if not math.isclose(previous[1], current[0], abs_tol=1e-6):
                errors.append("shot record ranges are not contiguous")
                break
        if duration_seconds is not None:
            try:
                duration = float(duration_seconds)
            except (TypeError, ValueError):
                errors.append("duration is not numeric")
            else:
                if duration > 0 and not math.isclose(
                    ranges[-1][1], duration, abs_tol=0.01,
                ):
                    errors.append(
                        "final shot record does not end at the requested duration"
                    )

    spans, malformed = _dialogue_spans(text)
    if malformed:
        errors.append("dialogue tags are nested or unbalanced")
    for index, (start, end) in enumerate(spans, start=1):
        if not _H3_STRICT_DIALOGUE_RE.fullmatch(text[start:end].strip()):
            errors.append(f"dialogue block {index} is not canonical")
    return list(dict.fromkeys(errors))


def _strip_h3_custom_sections(text: str) -> str:
    previous = None
    result = str(text or "")
    while result != previous:
        previous = result
        result = _H3_CUSTOM_SECTION_RE.sub(" ", result)
    return result


def _trim_sentence(value: Any) -> str:
    return _normalized_space(value).strip(" .")


def _meaningful_context_present(context: str, body: str) -> bool:
    stop = {
        "about", "after", "again", "being", "from", "have", "into",
        "make", "named", "show", "starring", "that", "their", "this",
        "with", "when", "where", "while",
    }
    words = {
        word.casefold()
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{3,}", context)
        if word.casefold() not in stop
    }
    if not words:
        return True
    body_words = {
        word.casefold()
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{3,}", body)
    }
    required = min(4, max(2, math.ceil(len(words) * 0.45)))
    return len(words & body_words) >= required


def _source_prompt_parts(
    prompt: str,
    *,
    project_context: str = "",
    opening_blocking: str = "",
    closing_blocking: str = "",
    audio_plan: Mapping[str, Any] | None = None,
) -> tuple[str, str, str, list[str]]:
    """Recover one concise visual timeline and the two official sound fields."""

    text = normalize_h3_text(prompt).strip()
    spans, _ = _dialogue_spans(text)
    existing_blocks = [
        h3_dialogue_tag(normalize_h3_text(text[start:end]))
        for start, end in spans
    ]
    fields = _extract_h3_fields(text)
    body = (
        fields.get("detailed_description")
        or fields.get("integrated_multimodal_description")
        or text
    )
    soundscape = fields.get("overall_soundscape", "")
    music = fields.get("non_diegetic_music", "")
    if not (
        fields.get("detailed_description")
        or fields.get("integrated_multimodal_description")
    ):
        boundary = _H3_SOUND_BOUNDARY_RE.search(body)
        if boundary:
            body = body[:boundary.start()]

    if not soundscape:
        match = _H3_SOUND_PROSE_RE.search(body)
        if match:
            soundscape = match.group(1)
            body = f"{body[:match.start()]} {body[match.end():]}"
    if not music:
        match = _H3_MUSIC_PROSE_RE.search(body)
        if match:
            music = match.group(1)
            body = f"{body[:match.start()]} {body[match.end():]}"

    # Legacy Maestro added a long context anchor before the actual prompt.
    # The planner's native body starts at this sentence, so retain everything
    # after it and discard only the duplicated wrapper.
    marker = _H3_META_SENTENCE_RE.search(body)
    if marker and body.lstrip().lower().startswith("project continuity"):
        body = body[marker.end():]
    body = _H3_META_SENTENCE_RE.sub("", body)
    body = _strip_h3_custom_sections(body)
    body = re.sub(
        r"^\s*(?:integrated_multimodal_description|detailed_description)\s*:\s*",
        "",
        body,
        flags=re.IGNORECASE,
    )
    canonical_body = bool(_canonical_h3_record_lines(body))
    if not canonical_body:
        body = re.sub(r"^\s*\[Shot\s+1\]\s*", "", body, flags=re.IGNORECASE)
    body = _normalize_h3_record_body(body)

    context = _normalized_space(normalize_h3_text(project_context))
    if context and not _meaningful_context_present(context, body):
        if len(context) > 360:
            context = context[:360].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
        if canonical_body:
            body = _augment_h3_record_description(
                body, f"Project context: {context}",
            )
        else:
            body = f"Project context: {context}. {body}".strip()

    opening = _normalized_space(normalize_h3_text(opening_blocking))
    if opening and opening.casefold() not in body.casefold():
        if canonical_body:
            body = _augment_h3_record_description(
                body, f"Opening composition: {opening}",
            )
        else:
            body = f"Opening composition: {opening}. {body}".strip()
    closing = _normalized_space(normalize_h3_text(closing_blocking))
    if closing and closing.casefold() not in body.casefold():
        if canonical_body:
            body = _augment_h3_record_description(
                body, f"By the final beat, {closing}", last=True,
            )
        else:
            body = f"{body} By the final beat, {closing}.".strip()

    plan = audio_plan if isinstance(audio_plan, Mapping) else {}
    if not _trim_sentence(soundscape):
        sound_bits = []
        ambience = _trim_sentence(plan.get("ambience", ""))
        if ambience:
            sound_bits.append(ambience)
        sound_bits.extend(
            _trim_sentence(effect)
            for effect in (plan.get("effects") or [])
            if _trim_sentence(effect)
        )
        soundscape = ", ".join(sound_bits) or "Natural scene-appropriate stereo ambience"
    # Small local planners occasionally emit the requested Context-IR labels
    # inline after first writing a prose prompt. The prose music extractor can
    # then capture the repeated visual/sound fields (and even a non-canonical
    # ``<d>`` block) as part of non_diegetic_music. Auxiliary fields never own
    # dialogue, so trim at the first nested field and remove any dialogue block
    # before final validation. The visual compiler retains/canonicalizes the
    # authoritative copy when scripted dialogue is actually present.
    def clean_auxiliary(value: Any) -> str:
        cleaned = normalize_h3_text(value)
        boundary = _H3_AUXILIARY_SECTION_BOUNDARY_RE.search(cleaned)
        if boundary:
            cleaned = cleaned[:boundary.start()]
        return _strip_dialogue_for_driving_audio(cleaned)

    soundscape = _trim_sentence(
        _sanitize_scripted_ambience(clean_auxiliary(soundscape))
    )
    music = _trim_sentence(clean_auxiliary(music)) or "N/A"
    if music.casefold() in {"n/a", "none", "no music"}:
        music = "N/A"
    return body, soundscape, music, existing_blocks


def _speaker_registry_entry(
    registry: Mapping[str, Any], key: str,
) -> tuple[str, str] | None:
    value = registry.get(key) or registry.get(key.casefold())
    if isinstance(value, Mapping):
        stable_id = _normalized_space(value.get("stable_id", ""))
        name = _normalized_space(value.get("speaker_name", ""))
    elif value:
        stable_id = _normalized_space(value)
        name = ""
    else:
        return None
    if stable_id and not stable_id.startswith("("):
        stable_id = f"({stable_id})"
    return stable_id, name


def _subject_name_for_key(subjects: Sequence[Any], key: str) -> str:
    folded = key.casefold()
    for subject in subjects or []:
        character_id = _normalized_space(_field(subject, "character_id", ""))
        speaker_name = _normalized_space(_field(subject, "speaker_name", ""))
        if folded in {character_id.casefold(), speaker_name.casefold()}:
            return speaker_name or character_id
    return key or "the visible speaker"


def _build_stable_speaker_registry(
    clip_plans: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Assign speaker IDs once per Director project, not once per shot."""

    registry: dict[str, dict[str, str]] = {}
    used_numbers: set[int] = set()
    for plan in clip_plans:
        existing = plan.get("_director_speaker_registry") or {}
        if not isinstance(existing, Mapping):
            continue
        for raw_key, raw_value in existing.items():
            key = _normalized_space(raw_key).casefold()
            entry = _speaker_registry_entry(existing, str(raw_key))
            if not key or not entry or not entry[0]:
                continue
            match = re.fullmatch(r"\(S(\d+)\)", entry[0], re.IGNORECASE)
            if not match:
                continue
            used_numbers.add(int(match.group(1)))
            registry[key] = {
                "stable_id": f"(S{int(match.group(1))})",
                "speaker_name": entry[1],
            }

    next_number = 1
    for plan in clip_plans:
        subjects = plan.get("_director_subjects_on_screen") or []
        for beat in plan.get("_director_dialogue_beats") or []:
            raw_key = _normalized_space(_field(beat, "speaker_id", ""))
            name = _subject_name_for_key(subjects, raw_key)
            key = (raw_key or name).casefold()
            if not key or key in registry:
                continue
            while next_number in used_numbers:
                next_number += 1
            registry[key] = {
                "stable_id": f"(S{next_number})",
                "speaker_name": name,
            }
            used_numbers.add(next_number)
            next_number += 1
    return registry


def _ensure_speaker_before_tag(
    body: str,
    tag: str,
    *,
    speaker_name: str,
    stable_id: str,
    cursor: int,
) -> tuple[str, int]:
    tag_index = body.find(tag, cursor)
    if tag_index < 0:
        return body, cursor
    nearby_start = max(0, tag_index - 260)
    nearby = body[nearby_start:tag_index]
    if stable_id in nearby:
        return body, tag_index + len(tag)

    name_matches = list(re.finditer(
        re.escape(speaker_name), nearby, flags=re.IGNORECASE,
    )) if speaker_name else []
    if name_matches:
        insertion = nearby_start + name_matches[-1].end()
        body = f"{body[:insertion]} {stable_id}{body[insertion:]}"
        tag_index += len(stable_id) + 1
    else:
        prefix = f"{speaker_name or 'The visible speaker'} {stable_id} says: "
        body = f"{body[:tag_index]}{prefix}{body[tag_index:]}"
        tag_index += len(prefix)
    return body, tag_index + len(tag)


def _compile_official_dialogue(
    body: str,
    subjects: Sequence[Any],
    dialogue_beats: Sequence[Any],
    registry: Mapping[str, Any],
    existing_blocks: Sequence[str],
    *,
    has_driving_audio: bool = False,
) -> tuple[str, str]:
    """Place exact tagged lines and stable speaker IDs in the visual field."""

    valid_beats: list[dict[str, str]] = []
    for beat in dialogue_beats or []:
        spoken = normalize_h3_text(_field(beat, "spoken_text", ""))
        if not _normalized_space(spoken):
            continue
        _, words = _dialogue_payload(spoken)
        speaker_key = _normalized_space(_field(beat, "speaker_id", ""))
        entry = _speaker_registry_entry(registry, speaker_key)
        if entry:
            stable_id, speaker_name = entry
        else:
            speaker_name = _subject_name_for_key(subjects, speaker_key)
            stable_id = f"(S{len(valid_beats) + 1})"
        valid_beats.append({
            "words": normalize_h3_text(words),
            "tag": h3_dialogue_tag(spoken),
            "stable_id": stable_id,
            "speaker_name": speaker_name,
            "delivery": _normalized_space(_field(beat, "delivery", "")),
            "physical_cue": _normalized_space(_field(beat, "physical_cue", "")),
        })

    body = _strip_h3_custom_sections(body)
    if has_driving_audio and not valid_beats:
        # The mapped audio is authoritative. LLM-authored vocal tags are both
        # unnecessary and actively harmful here: they can compete with the
        # soundtrack or become unbalanced when a repetitive transcript causes
        # output truncation. Structured dialogue remains authoritative for
        # narrative shots and follows the stricter validation path below.
        body = _strip_dialogue_for_driving_audio(body)
        existing_blocks = []
    spans, malformed = _dialogue_spans(body)
    if malformed and not spans:
        raise H3DialogueContractError(
            "MiniMax H3 dialogue tags are unbalanced and cannot be repaired safely."
        )

    if valid_beats:
        replacements = [
            valid_beats[index]["tag"] if index < len(valid_beats) else ""
            for index in range(len(spans))
        ]
        body = _replace_spans(body, spans, replacements)
        for beat in valid_beats[len(spans):]:
            body, replaced = _replace_first_outside_dialogue(
                body, beat["words"], beat["tag"],
            )
            if not replaced:
                delivery = (
                    f" with a {beat['delivery']} delivery"
                    if beat["delivery"] else ""
                )
                cue = (
                    f" While speaking, {beat['physical_cue']}"
                    if beat["physical_cue"] else ""
                )
                body = (
                    f"{body} {beat['speaker_name']} {beat['stable_id']} speaks"
                    f"{delivery}: {beat['tag']}.{cue}"
                ).strip()

        cursor = 0
        for beat in valid_beats:
            body, cursor = _ensure_speaker_before_tag(
                body,
                beat["tag"],
                speaker_name=beat["speaker_name"],
                stable_id=beat["stable_id"],
                cursor=cursor,
            )
        guard = (
            "Only the tagged lines are spoken, once each in order. After the "
            "final tagged line, every character remains silent with their "
            "mouth closed; no other speech or background voices occur."
        )
        if "only the tagged lines are spoken" not in body.casefold():
            body = f"{body} {guard}".strip()
        contract = " ".join(
            f"{beat['speaker_name']} {beat['stable_id']}: {beat['tag']}"
            for beat in valid_beats
        )
    else:
        canonical_blocks = [h3_dialogue_tag(block) for block in existing_blocks]
        if spans:
            canonical_blocks = [h3_dialogue_tag(body[start:end]) for start, end in spans]
            body = _replace_spans(body, spans, canonical_blocks)
        elif canonical_blocks:
            additions = " ".join(
                f"A visible speaker (S{index}) says: {tag}."
                for index, tag in enumerate(canonical_blocks, start=1)
            )
            body = f"{body} {additions}".strip()
        if canonical_blocks:
            guard = (
                "Only the tagged lines are spoken; everyone remains silent "
                "with their mouth closed at all other times."
            )
            if "only the tagged lines are spoken" not in body.casefold():
                body = f"{body} {guard}".strip()
            contract = " ".join(canonical_blocks)
        elif has_driving_audio:
            driver_contract = (
                "Any audible voice or vocal comes only from the mapped driving "
                "audio and remains synchronized to it; do not generate "
                "additional dialogue or speech-like vocalization."
            )
            if "mapped driving audio" not in body.casefold():
                body = f"{body} {driver_contract}".strip()
            contract = driver_contract
        else:
            silence = (
                "No character speaks; all visible mouths remain closed, and "
                "no speech-like vocalization or background voice occurs."
            )
            if not re.search(r"\bno (?:one|character) speaks\b", body, re.IGNORECASE):
                body = f"{body} {silence}".strip()
            contract = silence

    body = _normalize_h3_record_body(body)
    return body, contract


def _reference_relationships(
    references: Sequence[Mapping[str, Any]] | None,
    subjects: Sequence[Any] | None = None,
    registry: Mapping[str, Any] | None = None,
) -> tuple[
    list[str],
    list[str],
    bool,
    dict[int, list[str]],
    list[str],
    list[str],
]:
    """Build MiniMax's documented Ref2VA reference contract.

    Visual and audio retention markers intentionally use different fixed
    vocabularies. Identity/location pictures are bound through ``<Subject N>``
    instead of being misrepresented as concrete keyframes.
    """

    definitions: list[str] = []
    retention: list[str] = []
    subject_sources: dict[int, list[str]] = {}
    detail_bindings: list[str] = []
    task_types: list[str] = ["reference generation"]
    picture_no = video_no = audio_no = 0
    has_driving_audio = False
    next_subject_no = len(subjects or []) + 1
    registry = registry if isinstance(registry, Mapping) else {}

    def mapped_subject(role: str) -> tuple[int, str, str] | None:
        folded_role = role.casefold()
        for subject_index, subject in enumerate(subjects or [], start=1):
            character_id = _normalized_space(_field(subject, "character_id", ""))
            subject_name = _normalized_space(
                _field(subject, "speaker_name", "") or character_id
            )
            candidates = [
                value for value in (subject_name, character_id)
                if len(value) >= 2
            ]
            if not any(value.casefold() in folded_role for value in candidates):
                continue
            entry = _speaker_registry_entry(
                registry,
                character_id or subject_name,
            )
            stable_id = entry[0] if entry else ""
            return subject_index, subject_name or character_id, stable_id
        return None

    def add_task_type(value: str) -> None:
        if value not in task_types:
            task_types.append(value)

    for reference in references or []:
        kind = str(reference.get("type") or "").strip().lower()
        role = _trim_sentence(reference.get("role", "")) or f"the supplied {kind} reference"
        subject_match = mapped_subject(role)
        mapped_role = (
            f"<Subject {subject_match[0]}> ({subject_match[1]})"
            if subject_match else role
        )
        if kind == "image":
            picture_no += 1
            label = f"<Picture {picture_no}>"
            intent = str(reference.get("image_intent") or "identity").lower()
            if intent == "composition":
                definitions.append(
                    f"{label} is the soft composition and cast-layout anchor "
                    f"for [Shot 1], showing {mapped_role}."
                )
                retention.append(
                    f"{label} ([Shot 1] composition anchor): partially_preserved - "
                    "retain the intended subject placement, wardrobe, setting, "
                    "and spatial relationships while generating natural motion "
                    "rather than a frozen opening frame."
                )
                detail_bindings.append(
                    f"{label} softly guides the opening composition and cast layout."
                )
                continue

            if subject_match:
                subject_no, subject_name, _ = subject_match
            else:
                subject_no = next_subject_no
                next_subject_no += 1
                subject_name = role

            if intent == "scene":
                source_clause = f"environment and location identity come from {label}"
                explanation = (
                    f"preserve the architecture, materials, lighting context, and "
                    f"location identity supplied by {label}, but not incidental people"
                )
                marker = "fully_preserved"
                detail_bindings.append(
                    f"<Subject {subject_no}> is the environment established by {label}."
                )
            elif intent == "style":
                source_clause = f"visual style is guided by {label}"
                explanation = (
                    f"retain broad similarity to the medium, palette, lighting "
                    f"language, and texture of {label}, but not its people, pose, "
                    "framing, or exact composition"
                )
                marker = "weak_reference"
                detail_bindings.append(
                    f"The visual treatment of <Subject {subject_no}> follows {label} broadly."
                )
            else:
                source_clause = f"visual identity and appearance come from {label}"
                explanation = (
                    f"preserve the identity and appearance supplied by {label}; "
                    "use it for identity only and do not copy its background, "
                    "source location, framing, "
                    "composition, pose, or opening-still appearance"
                )
                marker = "fully_preserved"
                detail_bindings.append(
                    f"At first appearance, <Subject {subject_no}> uses identity and appearance from {label}."
                )

            if subject_match:
                subject_sources.setdefault(subject_no, []).append(source_clause)
            else:
                definitions.append(
                    f"<Subject {subject_no}> is {subject_name}, whose {source_clause}."
                )
            retention.append(
                f"<Subject {subject_no}> (appears in [Shot 1]): {marker} - {explanation}."
            )
        elif kind == "video":
            video_no += 1
            label = f"<Video {video_no}>"
            definitions.append(f"{label} provides motion and temporal reference for {mapped_role}.")
            retention.append(
                f"{label} (motion, camera, and temporal structure): weak_reference - "
                "retain only the requested motion, timing, camera, or scene traits "
                "while generating the described target video."
            )
            detail_bindings.append(
                f"The requested motion and temporal behavior follow {label} without copying it as source footage."
            )
        elif kind == "audio":
            audio_no += 1
            label = f"<Audio {audio_no}>"
            intent = str(reference.get("audio_intent") or "voice").lower()
            if intent == "drive":
                has_driving_audio = True
                add_task_type("audio reuse")
                definitions.append(
                    f"{label} is the performance-driving audio timeline for {mapped_role}."
                )
                retention.append(
                    f"{label}: partially_copy - reuse its audible content and "
                    "timing while synchronizing visible action and lip movement; "
                    "additional scene ambience or practical effects may be mixed around it."
                )
                detail_bindings.append(
                    f"Visible performance and lip movement remain synchronized to {label} throughout [Shot 1]."
                )
            elif intent == "style":
                add_task_type("audio reference")
                definitions.append(
                    f"{label} is the audio-style reference for {mapped_role}."
                )
                retention.append(
                    f"{label}: weak_reference - retain broad similarity to its "
                    "rhythm, texture, and style without copying its words, exact "
                    "timing, or waveform."
                )
                detail_bindings.append(
                    f"The requested audio treatment broadly follows {label}."
                )
            else:
                add_task_type("audio reference")
                speaker_suffix = ""
                if subject_match and subject_match[2]:
                    speaker_suffix = f" {subject_match[2]}"
                audio_target = (
                    f"<Subject {subject_match[0]}>"
                    if subject_match else mapped_role
                )
                definitions.append(
                    f"{label} is the voice-timbre reference for "
                    f"{audio_target}{speaker_suffix}."
                )
                retention.append(
                    f"{label}: reference - the target speaker follows its voice "
                    "timbre, emotion, and delivery without copying the source "
                    "words, timing, or waveform."
                )
                detail_bindings.append(
                    f"Newly scripted dialogue for {audio_target}{speaker_suffix} follows the voice timbre and delivery of {label}."
                )
    return (
        definitions,
        retention,
        has_driving_audio,
        subject_sources,
        detail_bindings,
        task_types,
    )


def _ref2va_subject_definitions(
    subjects: Sequence[Any],
    registry: Mapping[str, Any],
    source_bindings: Mapping[int, Sequence[str]] | None = None,
    included_indices: set[int] | None = None,
) -> list[str]:
    definitions: list[str] = []
    source_bindings = source_bindings or {}
    for index, subject in enumerate(subjects or [], start=1):
        if included_indices is not None and index not in included_indices:
            continue
        character_id = _normalized_space(_field(subject, "character_id", ""))
        name = _normalized_space(
            _field(subject, "speaker_name", "") or character_id or f"subject {index}"
        )
        entry = _speaker_registry_entry(registry, character_id or name)
        speaker = f" {entry[0]}" if entry and entry[0] else ""
        description = _trim_sentence(_field(subject, "visual_description", ""))
        wardrobe = _trim_sentence(_field(subject, "wardrobe", ""))
        details = "; ".join(
            item for item in (
                description,
                f"wearing {wardrobe}" if wardrobe else "",
                *source_bindings.get(index, ()),
            ) if item
        )
        definitions.append(
            f"<Subject {index}> is {name}{speaker}" + (f": {details}." if details else ".")
        )
    return definitions


def _base_subject_definitions(
    subjects: Sequence[Any],
    registry: Mapping[str, Any],
    authored: str = "",
    body: str = "",
) -> list[str]:
    """Build Base's global entity declarations from Director metadata."""

    authored = normalize_h3_text(authored).strip()
    if authored:
        # The saved prompt is authoritative. In particular, do not replace an
        # authored namespace with stale Director metadata or manufacture a
        # sentinel after the namespace has been parsed out of the body.
        return [authored]
    body_compact = " ".join(str(body or "").split()).casefold()
    included_indices: set[int] = set()
    for index, subject in enumerate(subjects or [], start=1):
        character_id = _normalized_space(_field(subject, "character_id", ""))
        name = _normalized_space(
            _field(subject, "speaker_name", "") or character_id
        )
        registry_entry = _speaker_registry_entry(
            registry, character_id or name,
        )
        candidates = [
            name,
            character_id,
            registry_entry[0] if registry_entry else "",
            f"<Subject {index}>",
        ]
        if any(
            candidate
            and re.search(
                rf"(?<![\w>]){re.escape(candidate.casefold())}(?![\w<])",
                body_compact,
            )
            for candidate in candidates
        ):
            included_indices.add(index)
    definitions = _ref2va_subject_definitions(
        subjects, registry, included_indices=included_indices,
    )
    return definitions or [_H3_NO_SUBJECT_DEFINITIONS]


def _label_ref2va_subjects_in_body(
    body: str,
    subjects: Sequence[Any],
    definitions: str = "",
) -> str:
    """Label exact authored identities without inventing visible entities."""

    result = body
    definition_entries = _parse_h3_subject_definitions(definitions) if definitions else []
    if definition_entries:
        references = [
            (entry["label"], _h3_subject_identity_aliases(entry))
            for entry in definition_entries
        ]
    elif definitions:
        # Preserve malformed authored definitions for the validator to reject;
        # falling back to metadata here could silently orphan the source.
        references = []
    else:
        references = []
        for index, subject in enumerate(subjects or [], start=1):
            name = _normalized_space(
                _field(subject, "speaker_name", "")
                or _field(subject, "character_id", "")
            )
            if name:
                references.append((f"<Subject {index}>", [name]))

    for label, aliases in references:
        if label in result:
            continue
        for alias in aliases:
            if not alias:
                continue
            pattern = re.compile(
                rf"(?<![\w>]){re.escape(alias)}(?![\w<])",
                re.IGNORECASE,
            )
            result, count = pattern.subn(
                lambda match: f"{label} ({match.group(0)})",
                result,
                count=1,
            )
            if count:
                break
    return result


def _alignment_header(
    mode: str,
    duration_seconds: float,
    final_shot_number: int = 1,
) -> str:
    duration = max(0.0, float(duration_seconds or 0.0))
    final_shot_number = max(1, int(final_shot_number or 1))
    if mode == "i2va":
        return (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced."
        )
    if mode == "fl2va":
        return (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the "
            f"target video; Picture 2 (from Shot {final_shot_number}) aligns with the "
            f"{duration:.2f}-second "
            "mark of the target video."
        )
    if mode == "l2va":
        return (
            "How the reference pictures align with the target video — "
            f"<Picture 1> (from [Shot {final_shot_number}]) aligns with the "
            f"{duration:.2f}-second mark of the target video."
        )
    return ""


def compile_h3_official_prompt(
    prompt: str,
    subjects: Sequence[Any] | None,
    dialogue_beats: Sequence[Any] | None,
    *,
    mode: str = "t2va",
    duration_seconds: float = 0.0,
    references: Sequence[Mapping[str, Any]] | None = None,
    speaker_registry: Mapping[str, Any] | None = None,
    project_context: str = "",
    opening_blocking: str = "",
    closing_blocking: str = "",
    audio_plan: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Compile Director metadata into MiniMax's documented Context-IR shape."""

    mode = str(mode or "t2va").strip().lower()
    if mode not in {"t2va", "i2va", "fl2va", "l2va", "ref2va"}:
        raise H3DialogueContractError(f"Unknown MiniMax H3 prompt mode: {mode}")
    registry = speaker_registry if isinstance(speaker_registry, Mapping) else {}
    (
        reference_definitions,
        retention,
        has_driving_audio,
        subject_sources,
        detail_bindings,
        task_types,
    ) = _reference_relationships(
        references if mode == "ref2va" else None,
        subjects or [],
        registry,
    )
    audio_mode = _normalized_space(_field(audio_plan or {}, "mode", "")).casefold()
    if audio_mode in {"audio_driven", "music_driven"}:
        # Initial Director preflight runs before concrete Ref2VA manifests are
        # assembled. The shot's audio plan still proves that a mapped source
        # track owns the vocals, so do not temporarily treat it as a silent or
        # prompt-scripted shot.
        has_driving_audio = True
    body, soundscape, music, existing_blocks = _source_prompt_parts(
        prompt,
        project_context=project_context,
        opening_blocking=opening_blocking,
        closing_blocking=closing_blocking,
        audio_plan=audio_plan,
    )
    body, vocal_contract = _compile_official_dialogue(
        body,
        subjects or [],
        dialogue_beats or [],
        registry,
        existing_blocks,
        has_driving_audio=has_driving_audio,
    )
    canonical_body = bool(_canonical_h3_record_lines(body))
    if not canonical_body:
        body = re.sub(r"^\s*\[Shot\s+1\]\s*", "", body, flags=re.IGNORECASE)
        body = f"[Shot 1] {body}".strip()

    if mode == "ref2va":
        authored_fields = _extract_h3_fields(prompt)
        authored_definitions = str(
            authored_fields.get("subject_definitions") or ""
        ).strip()
        if authored_definitions:
            definition_parts = [authored_definitions]
            authored_labels = {
                match.group(0).casefold()
                for match in _H3_REFERENCE_LABEL_RE.finditer(
                    authored_definitions
                )
            }
            for definition in reference_definitions:
                leading = _H3_REFERENCE_LABEL_RE.match(definition.strip())
                if leading and leading.group(0).casefold() in authored_labels:
                    continue
                definition_parts.append(definition)
            definitions = "\n".join(definition_parts)
        else:
            subject_definitions = _ref2va_subject_definitions(
                subjects or [],
                registry,
                subject_sources,
            )
            definitions = "\n".join([
                *subject_definitions, *reference_definitions,
            ])
        if not definitions:
            definitions = "Use the explicitly described subjects and target scene."
        authored_retention = str(
            authored_fields.get("retention_analysis") or ""
        ).strip()
        retention_text = "\n".join(
            item for item in [authored_retention, *retention] if item
        ) or (
            "Preserve the explicitly described identities, wardrobe, setting, "
            "action, and audio roles."
        )
        body = _label_ref2va_subjects_in_body(
            body, subjects or [], definitions,
        )
        summary_body = re.sub(r"^\[Shot\s+1\]\s*", "", body, flags=re.IGNORECASE)
        summary_body = _H3_STRICT_DIALOGUE_RE.sub(
            "scripted dialogue",
            summary_body,
        )
        summary = _trim_sentence(re.split(r"(?<=[.!?])\s+", summary_body, maxsplit=1)[0])
        if len(summary) > 320:
            summary = summary[:320].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
        authored_summary = str(authored_fields.get("summary") or "").strip()
        summary = authored_summary or (
            f"[{' + '.join(task_types)}] "
            f"{summary or 'A complete audiovisual shot matching the mapped subjects and requested action.'}"
        )
        if detail_bindings:
            if _canonical_h3_record_lines(body):
                body = _augment_h3_record_description(
                    body, " ".join(detail_bindings),
                )
            else:
                body = re.sub(r"^\[Shot\s+1\]\s*", "", body, flags=re.IGNORECASE)
                body = f"[Shot 1] {' '.join(detail_bindings)} {body}".strip()
        if has_driving_audio and music == "N/A":
            music = "Use the mapped driving audio according to retention_analysis."
        compiled = (
            f"subject_definitions: {definitions}\n\n"
            f"summary: {summary}\n\n"
            f"retention_analysis: {retention_text}\n\n"
            f"detailed_description: {body}\n\n"
            f"overall_soundscape: {soundscape}.\n\n"
            f"non_diegetic_music: {music}"
        )
    else:
        authored_definitions = _extract_h3_fields(prompt).get(
            "subject_definitions", "",
        )
        subject_definitions = _base_subject_definitions(
            subjects or [], registry, authored_definitions, body,
        )
        definitions_text = "\n".join(subject_definitions)
        body = _label_ref2va_subjects_in_body(
            body, subjects or [], definitions_text,
        )
        shot_numbers = [
            int(value)
            for value in re.findall(r"\[Shot\s+(\d+)\]", body, flags=re.IGNORECASE)
        ]
        header = _alignment_header(
            mode,
            duration_seconds,
            max(shot_numbers) if shot_numbers else 1,
        )
        compiled = (
            f"subject_definitions: {definitions_text}\n\n"
            f"integrated_multimodal_description: {body}\n\n"
            f"overall_soundscape: {soundscape}.\n\n"
            f"non_diegetic_music: {music}"
        )
        if header:
            compiled = f"{header}\n\n{compiled}"
    compiled = normalize_h3_text(compiled).strip()
    if canonical_body:
        errors = validate_h3_context_ir_records(
            compiled,
            mode=mode,
            duration_seconds=(duration_seconds if duration_seconds else None),
        )
        if errors:
            raise H3DialogueContractError(
                "Invalid canonical MiniMax H3 Context-IR: " + "; ".join(errors)
            )
    return compiled, vocal_contract


def validate_h3_prompt_contract(
    prompt: str,
    dialogue_beats: Sequence[Any] | None = None,
    *,
    mode: str = "t2va",
    references: Sequence[Mapping[str, Any]] | None = None,
    duration_seconds: float | None = None,
) -> list[str]:
    """Validate the official field order plus Maestro's exact dialogue data."""

    text = str(prompt or "")
    mode = str(mode or "t2va").strip().lower()
    expected = _H3_REF2VA_FIELDS if mode == "ref2va" else _H3_BASE_FIELDS
    errors = validate_h3_vocal_contract(text, dialogue_beats)
    positions: list[int] = []
    for field in expected:
        matches = list(re.finditer(
            rf"(?mi)^\s*{re.escape(field)}\s*:", text,
        ))
        if len(matches) != 1:
            errors.append(f"expected one {field} field, found {len(matches)}")
        elif matches:
            positions.append(matches[0].start())
    if len(positions) == len(expected) and positions != sorted(positions):
        errors.append("Context-IR fields are out of order")
    unexpected = set(_H3_ALL_FIELDS) - set(expected)
    for field in unexpected:
        if re.search(rf"(?mi)^\s*{re.escape(field)}\s*:", text):
            errors.append(f"unexpected {field} field for {mode}")
    extracted_fields = _extract_h3_fields(text)
    errors.extend(_h3_entity_contract_errors(text, mode=mode))
    visual_field = "detailed_description" if mode == "ref2va" else "integrated_multimodal_description"
    visual = extracted_fields.get(visual_field, "")
    if not re.match(r"^\s*\[Shot\s+1\]", visual, flags=re.IGNORECASE):
        errors.append(f"{visual_field} does not begin with [Shot 1]")
    if re.search(r"(?m)^\s*\[Shot\s+\d+\]\s+\[", visual, re.IGNORECASE):
        errors.extend(validate_h3_context_ir_records(
            text,
            mode=mode,
            duration_seconds=duration_seconds,
        ))
    if re.search(
        r"\b(?:PROJECT CONTINUITY|OPENING CONTINUITY|FINAL BLOCKING|"
        r"DIALOGUE AND VOCAL PERFORMANCE|SILENCE AND VOCAL PERFORMANCE)\s*:",
        text,
        flags=re.IGNORECASE,
    ):
        errors.append("legacy Maestro prompt wrapper remains in the H3 payload")
    if any(0x80 <= ord(character) <= 0x9F for character in text):
        errors.append("prompt contains an orphaned C1 control character")
    if mode == "i2va" and not text.startswith(
        "For the target video, at 0.00 seconds into the target video, <Picture 1>"
    ):
        errors.append("I2VA prompt is missing the official 0.00-second alignment line")
    if mode == "fl2va" and not text.startswith(
        "How the reference pictures align with the target video"
    ):
        errors.append("FL2VA prompt is missing the official two-picture alignment line")
    if mode == "l2va" and not text.startswith(
        "How the reference pictures align with the target video — <Picture 1>"
    ):
        errors.append("L2VA prompt is missing the official last-picture alignment line")
    if mode == "t2va" and text.startswith((
        "For the target video,", "How the reference pictures align",
    )):
        errors.append("T2VA prompt must not contain a picture-alignment header")
    if mode == "ref2va":
        summary = extracted_fields.get("summary", "")
        if not re.match(
            r"^\[(?:reference generation|video editing|video continuation|"
            r"keyframe completion|audio reuse|audio reference)(?:\s+\+\s+"
            r"(?:reference generation|video editing|video continuation|"
            r"keyframe completion|audio reuse|audio reference))*\]\s+\S",
            summary,
            flags=re.IGNORECASE,
        ):
            errors.append("Ref2VA summary is missing its official task-type prefix")

        if references:
            expected_labels: list[str] = []
            counts = {"image": 0, "video": 0, "audio": 0}
            for reference in references:
                kind = str(reference.get("type") or "").strip().lower()
                if kind not in counts:
                    continue
                counts[kind] += 1
                noun = {"image": "Picture", "video": "Video", "audio": "Audio"}[kind]
                expected_labels.append(f"<{noun} {counts[kind]}>")
            missing_labels = [label for label in expected_labels if label not in text]
            if missing_labels:
                errors.append(
                    "Ref2VA prompt does not map supplied references: "
                    + ", ".join(missing_labels)
                )

            retention_text = extracted_fields.get("retention_analysis", "")
            if counts["image"] + counts["video"] and not re.search(
                r":\s*(?:fully_preserved|partially_preserved|"
                r"attribute_transfer|weak_reference)\s+-",
                retention_text,
            ):
                errors.append("Ref2VA visual retention uses no official marker")
            if counts["audio"] and not re.search(
                r":\s*(?:fully_copy|partially_copy|reference|weak_reference)\s+-",
                retention_text,
            ):
                errors.append("Ref2VA audio retention uses no official marker")
    return list(dict.fromkeys(errors))


def compile_h3_clip_plans(
    clip_plans: Sequence[MutableMapping[str, Any]],
    *,
    prompt_modes: Sequence[str] | None = None,
    durations: Sequence[float] | None = None,
    reference_manifests: Sequence[Sequence[Mapping[str, Any]]] | None = None,
) -> Sequence[MutableMapping[str, Any]]:
    """Compile every saved/renderable H3 clip immediately before queuing.

    ``_director_h3_source_prompt`` remains the immutable planner result. This
    lets generation recompile a prompt from T2VA to I2VA/FL2VA after the actual
    start/end files are known without nesting an alignment header or six-field
    Ref2VA wrapper around a previously compiled prompt.
    """

    registry = _build_stable_speaker_registry(clip_plans)
    for index, plan in enumerate(clip_plans):
        beats = plan.get("_director_dialogue_beats") or []
        for beat in beats:
            if isinstance(beat, MutableMapping) and "spoken_text" in beat:
                beat["spoken_text"] = normalize_h3_text(beat["spoken_text"])

        current_prompt = str(plan.get("video_prompt", "") or "")
        last_compiled = str(plan.get("_director_h3_compiled_prompt", "") or "")
        source_prompt = plan.get("_director_h3_source_prompt")
        if source_prompt and last_compiled and current_prompt != last_compiled:
            # Prompt review/editing happens after the first preflight. Treat a
            # changed compiled prompt as the user's new authoritative source.
            source_prompt = current_prompt
            plan["_director_h3_source_prompt"] = source_prompt
        if not source_prompt:
            source_prompt = current_prompt
            plan["_director_h3_source_prompt"] = source_prompt
        mode = (
            prompt_modes[index]
            if prompt_modes is not None and index < len(prompt_modes)
            else plan.get("_director_h3_prompt_mode")
            or (
                "ref2va"
                if plan.get("_director_h3_model_family") == "ref2va"
                else "t2va"
            )
        )
        duration = (
            durations[index]
            if durations is not None and index < len(durations)
            else plan.get("_director_duration_sec") or 0.0
        )
        references = (
            reference_manifests[index]
            if reference_manifests is not None and index < len(reference_manifests)
            else plan.get("_director_h3_reference_manifest") or []
        )
        prompt, contract = compile_h3_official_prompt(
            source_prompt,
            plan.get("_director_subjects_on_screen") or [],
            beats,
            mode=mode,
            duration_seconds=duration,
            references=references,
            speaker_registry=registry,
            project_context=plan.get("_director_project_context", ""),
            opening_blocking=plan.get("_director_opening_blocking", ""),
            closing_blocking=plan.get("_director_closing_blocking", ""),
            audio_plan=plan.get("_director_audio_plan") or {},
        )
        plan["video_prompt"] = prompt
        plan["_director_h3_compiled_prompt"] = prompt
        plan["_director_vocal_contract"] = contract
        plan["_director_h3_prompt_mode"] = mode
        plan["_director_speaker_registry"] = registry
        errors = validate_h3_prompt_contract(
            prompt,
            beats,
            mode=mode,
            references=references,
            duration_seconds=duration,
        )
        if errors:
            raise H3DialogueContractError(
                f"Shot {index + 1}: " + "; ".join(errors)
            )
    return clip_plans
