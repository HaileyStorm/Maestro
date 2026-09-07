"""Render MiniMax H3 reference semantics without granting asset authority.

Callers own reference admission and ordering.  This module renders only the
structural metadata it receives.  Director may opt into its subject namespace
through a resolver; callers without that context receive label-only text and
never acquire synthetic ``<Subject N>`` identifiers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from models.minimax_h3.reference_manifest import reference_role_text


SubjectMatch = tuple[int, str, str]
SubjectResolver = Callable[[str], SubjectMatch | None]
ReferenceRelationships = tuple[
    list[str],
    list[str],
    bool,
    dict[int, list[str]],
    list[str],
    list[str],
]


def _trim_sentence(value: object) -> str:
    return " ".join(str(value or "").split()).strip(" .")


def _role_suffix(role: str) -> str:
    return f" for {reference_role_text(role)}" if role else ""


def reference_relationships(
    references: Sequence[Mapping[str, Any]] | None,
    *,
    subject_resolver: SubjectResolver | None = None,
    bind_unmatched_subjects: bool = False,
    next_subject_no: int = 1,
) -> ReferenceRelationships:
    """Return definitions, retention, drive state, bindings, details, tasks.

    ``bind_unmatched_subjects`` is Director's explicit presentation context.
    It preserves Director's established subject binding for selected image
    intents.  The default label-only context is suitable for schema mapping,
    where allocating a fresh subject number could collide with authored Base
    definitions.

    Missing image or audio intent is deliberately neutral.  A role remains
    literal escaped text and cannot imply identity, voice, or subject binding.
    """

    definitions: list[str] = []
    retention: list[str] = []
    subject_sources: dict[int, list[str]] = {}
    detail_bindings: list[str] = []
    task_types: list[str] = ["reference generation"]
    picture_no = video_no = audio_no = 0
    has_driving_audio = False

    def add_task_type(value: str) -> None:
        if value not in task_types:
            task_types.append(value)

    def resolve_subject(role: str, *, allow: bool) -> SubjectMatch | None:
        if not allow or not role or subject_resolver is None:
            return None
        return subject_resolver(_trim_sentence(role))

    for reference in references or []:
        kind = str(reference.get("type") or "").strip().lower()
        raw_role = str(reference.get("role") or "").strip()
        rendered_role = reference_role_text(
            raw_role or f"the supplied {kind} reference"
        )
        suffix = _role_suffix(raw_role)

        if kind == "image":
            picture_no += 1
            label = f"<Picture {picture_no}>"
            intent = str(reference.get("image_intent") or "").strip().lower()

            if intent not in {"identity", "scene", "style", "composition"}:
                definitions.append(f"{label} is a supplied visual reference{suffix}.")
                retention.append(
                    f"{label}: weak_reference - use only visual traits requested by the "
                    "authored prompt, without assigning identity, subject, scene, or style ownership."
                )
                detail_bindings.append(
                    f"{label} supplies only visual traits explicitly requested by the authored prompt."
                )
                continue

            subject_match = resolve_subject(raw_role, allow=True)
            mapped_role = (
                f"<Subject {subject_match[0]}> ({subject_match[1]})"
                if subject_match else rendered_role
            )
            if intent == "composition":
                if bind_unmatched_subjects or subject_match:
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
                else:
                    definitions.append(
                        f"{label} provides a soft [Shot 1] composition anchor{suffix}."
                    )
                    retention.append(
                        f"{label} ([Shot 1] composition anchor): partially_preserved - retain "
                        "subject placement and spatial relationships while generating natural motion."
                    )
                    detail_bindings.append(
                        f"{label} softly guides the opening composition and cast layout."
                    )
                continue

            if not bind_unmatched_subjects and subject_match is None:
                if intent == "identity":
                    definitions.append(
                        f"{label} provides identity and appearance reference{suffix}."
                    )
                    retention.append(
                        f"{label}: fully_preserved - preserve its identity and appearance only; "
                        "do not copy incidental background, framing, pose, or composition."
                    )
                elif intent == "scene":
                    definitions.append(
                        f"{label} provides environment and location reference{suffix}."
                    )
                    retention.append(
                        f"{label}: fully_preserved - preserve its architecture, materials, "
                        "lighting context, and location identity, but not incidental people."
                    )
                else:
                    definitions.append(
                        f"{label} provides broad visual-style reference{suffix}."
                    )
                    retention.append(
                        f"{label}: weak_reference - retain broad similarity to its medium, "
                        "palette, lighting language, and texture without copying people or composition."
                    )
                continue

            if subject_match:
                subject_no, subject_name, _ = subject_match
            else:
                subject_no = next_subject_no
                next_subject_no += 1
                subject_name = rendered_role

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
                    "source location, framing, composition, pose, or opening-still appearance"
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
            subject_match = resolve_subject(raw_role, allow=True)
            mapped_role = (
                f"<Subject {subject_match[0]}> ({subject_match[1]})"
                if subject_match else rendered_role
            )
            if bind_unmatched_subjects or subject_match:
                definitions.append(
                    f"{label} provides motion and temporal reference for {mapped_role}."
                )
                retention.append(
                    f"{label} (motion, camera, and temporal structure): weak_reference - "
                    "retain only the requested motion, timing, camera, or scene traits "
                    "while generating the described target video."
                )
            else:
                definitions.append(
                    f"{label} is a supplied temporal visual reference{suffix}."
                )
                retention.append(
                    f"{label}: weak_reference - retain only requested motion, timing, camera, "
                    "or scene traits while generating the described target video."
                )
            detail_bindings.append(
                f"The requested motion and temporal behavior follow {label} without copying it as source footage."
            )
            if (reference.get("has_audio") or reference.get("audio_path")) and reference.get("include_audio", True):
                audio_no += 1
                audio_label = f"<Audio {audio_no}>"
                add_task_type("audio reuse")
                definitions.append(
                    f"{audio_label} is the soundtrack paired with {label}."
                )
                retention.append(
                    f"{audio_label}: partially_copy - retain the paired soundtrack's "
                    "audible timeline with its video reference."
                )
                detail_bindings.append(
                    f"{audio_label} supplies the audible timeline paired with {label}."
                )

        elif kind == "audio":
            audio_no += 1
            label = f"<Audio {audio_no}>"
            intent = str(reference.get("audio_intent") or "").strip().lower()

            if intent not in {"voice", "style", "drive"}:
                add_task_type("audio reference")
                definitions.append(f"{label} is a supplied audio reference{suffix}.")
                retention.append(
                    f"{label}: weak_reference - use only audible traits requested by the "
                    "authored prompt, without assigning voice or style ownership."
                )
                detail_bindings.append(
                    f"{label} supplies only audible traits explicitly requested by the authored prompt."
                )
                continue

            subject_match = resolve_subject(raw_role, allow=True)
            mapped_role = (
                f"<Subject {subject_match[0]}> ({subject_match[1]})"
                if subject_match else rendered_role
            )
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
                    if bind_unmatched_subjects or subject_match else
                    f"{label} is an audio-style reference{suffix}."
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
                speaker_suffix = (
                    f" {subject_match[2]}"
                    if subject_match and subject_match[2] else ""
                )
                audio_target = (
                    f"<Subject {subject_match[0]}>"
                    if subject_match else rendered_role
                )
                if bind_unmatched_subjects or subject_match:
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
                else:
                    definitions.append(
                        f"{label} is a voice-timbre reference{suffix}."
                    )
                    retention.append(
                        f"{label}: reference - follow its voice timbre, emotion, and delivery "
                        "without copying source words, timing, or waveform."
                    )
                    detail_bindings.append(
                        f"Newly scripted dialogue follows the voice timbre and delivery of {label}."
                    )

    return (
        definitions,
        retention,
        has_driving_audio,
        subject_sources,
        detail_bindings,
        task_types,
    )
