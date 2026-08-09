"""Content-neutral MiniMax H3 source-audio and media-ordinal contracts.

This module is a clean-room Maestro implementation.  It contains no upstream
T8Mars code and operates only on request structure: model identity, media-slot
presence, explicit mode/settings, and canonical ``<Picture N>`` / ``<Video
N>`` / ``<Audio N>`` tags.  It never examines creative subject matter or
waveform/media content.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


H3_SOURCE_AUDIO_ALGORITHM_VERSION = "maestro_h3_source_audio_v1"
H3_MULTIRATE_EVIDENCE_PROFILE = "t8_4v8a_evidence_v1"
H3_SOURCE_AUDIO_MODES = frozenset({
    "native", "lock_source", "remix_source", "reference_only",
})
H3_EXPERIMENTAL_SOURCE_AUDIO_MODES = frozenset(
    H3_SOURCE_AUDIO_MODES - {"native"}
)

_MEDIA_KINDS = ("Picture", "Video", "Audio")
_MEDIA_TAG = re.compile(r"<(Picture|Video|Audio) ([1-9][0-9]*)>")
_MEDIA_LIKE_TAG = re.compile(r"<(?:Picture|Video|Audio)\b[^>]*>", re.IGNORECASE)


class H3AudioCompatibilityError(ValueError):
    """An H3 audio request is outside the explicitly supported matrix."""


class H3MediaMapError(ValueError):
    """An H3 media ordinal cannot resolve to the supplied structural map."""


@dataclass(frozen=True)
class H3AudioRoles:
    """Resolved audio roles without inspecting or persisting media content."""

    mode: str
    algorithm_version: str
    primary_audio_ordinal: int
    drive_audio: Any | None
    reference_audios: tuple[Any, ...]
    final_audio: Any | None
    final_audio_kind: str
    remix_strength: float
    audio_ordinal_remap: tuple[tuple[int, int], ...]

    @property
    def experimental(self) -> bool:
        return self.mode in H3_EXPERIMENTAL_SOURCE_AUDIO_MODES


def _custom_settings(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return len(value) > 0
    return True


def source_audio_mode(custom_settings: Mapping[str, Any] | None) -> str:
    """Return the explicit mode, defaulting to the legacy/native contract."""

    raw = _custom_settings(custom_settings).get("h3_source_audio_mode")
    mode = "native" if raw in (None, "") else str(raw).strip().lower()
    if mode not in H3_SOURCE_AUDIO_MODES:
        raise H3AudioCompatibilityError(
            "MiniMax H3 source audio mode must be native, lock_source, "
            "remix_source, or reference_only"
        )
    return mode


def source_audio_requested(custom_settings: Mapping[str, Any] | None) -> bool:
    return source_audio_mode(custom_settings) in H3_EXPERIMENTAL_SOURCE_AUDIO_MODES


def multirate_profile(custom_settings: Mapping[str, Any] | None) -> str:
    custom = _custom_settings(custom_settings)
    raw = custom.get("h3_multirate_profile")
    profile = "" if raw in (None, "") else str(raw).strip()
    if profile and profile != H3_MULTIRATE_EVIDENCE_PROFILE:
        raise H3AudioCompatibilityError("Unknown MiniMax H3 multirate profile")
    return profile


def validate_multirate_evidence_request(
    custom_settings: Mapping[str, Any] | None,
    *,
    benchmark_dry_run: bool = False,
) -> dict[str, Any] | None:
    """Return the disabled 4-video/8-audio evidence identity.

    The runtime deliberately has no enabling branch.  Only the local synthetic
    benchmark's sanitized dry-run may materialize this descriptor.
    """

    profile = multirate_profile(custom_settings)
    if not profile:
        return None
    if not benchmark_dry_run:
        raise H3AudioCompatibilityError(
            "MiniMax H3 4-video/8-audio multirate is benchmark-dry-run only "
            "until live synchronized visual/audio acceptance passes"
        )
    return {
        "profile": H3_MULTIRATE_EVIDENCE_PROFILE,
        "algorithm_version": "maestro_h3_dual_clock_evidence_v1",
        "video_evaluations": 4,
        "audio_evaluations": 8,
        "enabled_for_generation": False,
    }


def _primary_ordinal(custom: Mapping[str, Any], count: int) -> int:
    raw = custom.get("h3_primary_audio_ordinal", 1)
    if isinstance(raw, bool) or (
        isinstance(raw, float) and not raw.is_integer()
    ):
        raise H3AudioCompatibilityError("Primary H3 audio ordinal must be an integer")
    try:
        ordinal = int(raw)
    except (TypeError, ValueError) as exc:
        raise H3AudioCompatibilityError(
            "Primary H3 audio ordinal must be an integer"
        ) from exc
    if ordinal < 1 or ordinal > count:
        raise H3AudioCompatibilityError(
            f"Primary H3 audio ordinal must resolve to one of {count} supplied audio slots"
        )
    return ordinal


def remap_primary_audio(
    sources: Sequence[Any], primary_audio_ordinal: int,
) -> tuple[tuple[Any, ...], dict[int, int]]:
    """Move the selected audio to ordinal one and return old->new ordinals."""

    ordered = tuple(sources)
    if not ordered:
        raise H3AudioCompatibilityError("The selected H3 source-audio mode needs audio")
    if primary_audio_ordinal < 1 or primary_audio_ordinal > len(ordered):
        raise H3AudioCompatibilityError("Primary H3 audio ordinal is out of range")
    old_order = list(range(1, len(ordered) + 1))
    primary_index = primary_audio_ordinal - 1
    new_old_order = [old_order[primary_index], *old_order[:primary_index], *old_order[primary_index + 1 :]]
    remapped = tuple(ordered[index - 1] for index in new_old_order)
    old_to_new = {old: new for new, old in enumerate(new_old_order, 1)}
    return remapped, old_to_new


def remap_prompt_audio_ordinals(prompt: str, old_to_new: Mapping[int, int]) -> str:
    """Rewrite only exact Audio tags; all creative text remains byte-for-byte."""

    if not isinstance(prompt, str):
        raise H3MediaMapError("MiniMax H3 accepts one text prompt per generation")

    def replace(match: re.Match[str]) -> str:
        if match.group(1) != "Audio":
            return match.group(0)
        old = int(match.group(2))
        return f"<Audio {int(old_to_new.get(old, old))}>"

    return _MEDIA_TAG.sub(replace, prompt)


def canonical_media_map(
    *, picture_count: int = 0, video_count: int = 0, audio_count: int = 0,
) -> tuple[dict[str, int | str], ...]:
    """Build the path-free ordinal table used by prompt and latent assembly."""

    counts = {
        "Picture": picture_count, "Video": video_count, "Audio": audio_count,
    }
    entries: list[dict[str, int | str]] = []
    for kind in _MEDIA_KINDS:
        count = counts[kind]
        if isinstance(count, bool) or (
            isinstance(count, float) and not count.is_integer()
        ):
            raise H3MediaMapError(f"{kind} count must be a non-negative integer")
        try:
            count = int(count)
        except (TypeError, ValueError) as exc:
            raise H3MediaMapError(
                f"{kind} count must be a non-negative integer"
            ) from exc
        if count < 0:
            raise H3MediaMapError(f"{kind} count must be a non-negative integer")
        for ordinal in range(1, count + 1):
            entries.append({
                "kind": kind.lower(),
                "ordinal": ordinal,
                "tag": f"<{kind} {ordinal}>",
            })
    return tuple(entries)


def validate_prompt_media_ordinals(
    prompt: str,
    *, picture_count: int = 0, video_count: int = 0, audio_count: int = 0,
) -> tuple[dict[str, int | str], ...]:
    """Validate media-like tags against exact, contiguous modality namespaces."""

    if not isinstance(prompt, str):
        raise H3MediaMapError("MiniMax H3 accepts one text prompt per generation")
    media_map = canonical_media_map(
        picture_count=picture_count,
        video_count=video_count,
        audio_count=audio_count,
    )
    available = {
        "Picture": int(picture_count),
        "Video": int(video_count),
        "Audio": int(audio_count),
    }
    used: dict[str, set[int]] = {kind: set() for kind in _MEDIA_KINDS}
    for candidate in _MEDIA_LIKE_TAG.finditer(prompt):
        if _MEDIA_TAG.fullmatch(candidate.group(0)) is None:
            raise H3MediaMapError(
                f"{candidate.group(0)} is not a canonical MiniMax H3 media tag; "
                "use <Picture N>, <Video N>, or <Audio N>"
            )
    for match in _MEDIA_TAG.finditer(prompt):
        kind, ordinal = match.group(1), int(match.group(2))
        if ordinal < 1 or ordinal > available[kind]:
            raise H3MediaMapError(
                f"{match.group(0)} does not resolve to a supplied MiniMax H3 {kind.lower()}"
            )
        used[kind].add(ordinal)
    for kind, ordinals in used.items():
        if ordinals and ordinals != set(range(1, max(ordinals) + 1)):
            raise H3MediaMapError(
                f"MiniMax H3 {kind.lower()} tags must use a contiguous ordinal prefix"
            )
    return media_map


def resolve_h3_audio_roles(
    *,
    selected_model_type: str,
    model_def: Mapping[str, Any] | None,
    custom_settings: Mapping[str, Any] | None,
    sampling_steps: int | None,
    attention_engine: str,
    audio_prompt_type: str = "",
    audio_guides: Sequence[Any] = (),
    final_audio: Any | None = None,
    semantic_references: bool = False,
    multisegment: bool = False,
    activated_loras: Sequence[Any] | None = None,
    loras_multipliers: Any = None,
    skip_steps_cache_type: Any = None,
    native_boundary: bool = False,
) -> H3AudioRoles:
    """Resolve and validate Base-FL2VA audio roles identically at every layer."""

    custom = _custom_settings(custom_settings)
    validate_multirate_evidence_request(custom, benchmark_dry_run=False)
    mode = source_audio_mode(custom)
    guides = tuple(item for item in audio_guides if _present(item))

    if mode == "native":
        return H3AudioRoles(
            mode=mode,
            algorithm_version=H3_SOURCE_AUDIO_ALGORITHM_VERSION,
            primary_audio_ordinal=1,
            drive_audio=None,
            reference_audios=(),
            final_audio=final_audio if _present(final_audio) else None,
            final_audio_kind="explicit" if _present(final_audio) else "generated",
            remix_strength=1.0,
            audio_ordinal_remap=(),
        )

    if str(selected_model_type or "") != "minimax_h3" or bool(
        dict(model_def or {}).get("minimax_h3_reference_mode")
    ):
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes currently require Base FL2VA"
        )
    if semantic_references:
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes cannot be combined with semantic references"
        )
    if multisegment:
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes currently require one segment"
        )
    if native_boundary:
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes cannot use native boundary conditioning"
        )
    unsupported_audio_flags = sorted(
        set(str(audio_prompt_type or "")) - set("ABC")
    )
    if unsupported_audio_flags:
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes cannot combine with legacy "
            f"audio preprocessing flags: {''.join(unsupported_audio_flags)}"
        )
    if isinstance(sampling_steps, bool) or (
        isinstance(sampling_steps, float) and not sampling_steps.is_integer()
    ):
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes require exactly 20 native steps"
        )
    try:
        steps = int(sampling_steps or 0)
    except (TypeError, ValueError) as exc:
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes require exactly 20 native steps"
        ) from exc
    if steps != 20:
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes require exactly 20 native steps"
        )
    engine = str(attention_engine or "sol_attn")
    if engine not in {"sdpa", "sol_attn"}:
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes require Dense SDPA or Sol-Attn"
        )
    for key, label in (
        ("h3_turbo_profile", "H3 Turbo"),
        ("h3_spectrum_profile", "Spectrum"),
        ("h3_lightx2v_profile", "LightX2V"),
    ):
        if _present(custom.get(key)):
            raise H3AudioCompatibilityError(
                f"Experimental H3 source-audio modes cannot use {label}"
            )
    if activated_loras:
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes cannot stack user LoRAs"
        )
    if _present(loras_multipliers):
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes cannot use LoRA multipliers"
        )
    if _present(skip_steps_cache_type):
        raise H3AudioCompatibilityError(
            "Experimental H3 source-audio modes cannot use a step cache"
        )
    if not guides:
        raise H3AudioCompatibilityError(
            f"MiniMax H3 {mode} requires at least one drive-audio slot"
        )
    if mode in {"lock_source", "remix_source"} and len(guides) != 1:
        raise H3AudioCompatibilityError(
            f"MiniMax H3 {mode} accepts exactly one drive-audio slot"
        )
    if len(guides) > 3:
        raise H3AudioCompatibilityError(
            "MiniMax H3 reference_only accepts at most three audio slots"
        )

    primary = _primary_ordinal(custom, len(guides))
    reordered, old_to_new = remap_primary_audio(guides, primary)
    raw_strength = custom.get("h3_audio_remix_strength", 0.5)
    if isinstance(raw_strength, bool):
        raise H3AudioCompatibilityError("H3 audio remix strength must be numeric")
    try:
        remix_strength = float(raw_strength)
    except (TypeError, ValueError) as exc:
        raise H3AudioCompatibilityError("H3 audio remix strength must be numeric") from exc
    if mode == "remix_source":
        if not 0.0 < remix_strength <= 1.0:
            raise H3AudioCompatibilityError(
                "H3 audio remix strength must be greater than zero and at most one"
            )
    elif "h3_audio_remix_strength" in custom:
        raise H3AudioCompatibilityError(
            "H3 audio remix strength applies only to remix_source"
        )

    explicit_final = final_audio if _present(final_audio) else None
    if explicit_final is not None:
        resolved_final, final_kind = explicit_final, "explicit"
    elif mode == "lock_source":
        resolved_final, final_kind = reordered[0], "source"
    else:
        resolved_final, final_kind = None, "generated"
    return H3AudioRoles(
        mode=mode,
        algorithm_version=H3_SOURCE_AUDIO_ALGORITHM_VERSION,
        primary_audio_ordinal=primary,
        drive_audio=reordered[0],
        reference_audios=reordered if mode == "reference_only" else (),
        final_audio=resolved_final,
        final_audio_kind=final_kind,
        remix_strength=remix_strength,
        audio_ordinal_remap=tuple(sorted(old_to_new.items())),
    )


__all__ = [
    "H3AudioCompatibilityError", "H3AudioRoles", "H3MediaMapError",
    "H3_EXPERIMENTAL_SOURCE_AUDIO_MODES", "H3_MULTIRATE_EVIDENCE_PROFILE",
    "H3_SOURCE_AUDIO_ALGORITHM_VERSION", "H3_SOURCE_AUDIO_MODES",
    "canonical_media_map", "multirate_profile", "remap_primary_audio",
    "remap_prompt_audio_ordinals", "resolve_h3_audio_roles",
    "source_audio_mode", "source_audio_requested",
    "validate_multirate_evidence_request", "validate_prompt_media_ordinals",
]
