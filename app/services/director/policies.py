"""
Shared Prompt Policies — centralized rules enforced across all skills and render modes.

Instead of duplicating long rule prose in every system prompt, policies are defined
here as structured data + helper functions that renderers and validators consume.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

from .schema import ShotPlan, ProductionPlan, CharacterProfile


# ── Policy Configuration ─────────────────────────────────────────────

@dataclass
class PromptPolicy:
    """Toggleable policy flags — renderers and validators check these."""
    no_character_names_outside_dialogue: bool = True
    dialogue_in_quotes: bool = True
    chronological_action: bool = True
    single_paragraph: bool = True
    present_tense: bool = True
    no_montage_language: bool = True
    physical_emotion_only: bool = True
    explicit_camera_language: bool = True
    re_describe_characters_every_shot: bool = True
    no_meta_language_in_image_prompts: bool = True
    no_action_in_image_prompts: bool = True


# Default policy — all rules enabled
DEFAULT_POLICY = PromptPolicy()


# ── Anti-Pattern Definitions ─────────────────────────────────────────

# Words/phrases that should never appear in single-shot video prompts
MONTAGE_LANGUAGE = [
    "montage", "quick cuts", "cut to", "series of shots",
    "rapid cuts", "jump cut", "intercut", "cross-cut",
    "smash cut", "match cut", "dissolve to", "fade to",
    "transition to", "we see", "next we see",
]

# Meta-language that shouldn't appear in image prompts
IMAGE_META_LANGUAGE = [
    "preserve", "maintain", "keep unchanged", "keep the same",
    "don't change", "same as before", "as in the reference",
    "remain", "stays the same", "unaltered",
]

# Abstract emotion labels — prefer physical cues instead
ABSTRACT_EMOTIONS = [
    "feeling happy", "feeling sad", "feeling angry",
    "shows emotion", "emotional moment", "with emotion",
    "conveys sadness", "expresses joy",
]

# Vague camera language — prefer explicit terms
VAGUE_CAMERA = [
    "cinematic camera", "dramatic camera", "interesting angle",
    "cool shot", "nice framing", "creative camera",
    "camera does something", "dynamic camera work",
]

# Action verbs that don't belong in static image prompts
IMAGE_ACTION_VERBS = [
    "walks", "runs", "dances", "jumps", "turns",
    "raises hand", "waves", "throws", "catches",
    "speaks", "says", "whispers", "shouts",
    "moves toward", "steps", "reaches",
]


# ── Character Description Helpers ────────────────────────────────────

def describe_character(char: CharacterProfile, include_wardrobe: bool = True) -> str:
    """Build a visual description string for a character (no names)."""
    parts = [char.physical_description]
    if include_wardrobe and char.wardrobe:
        parts.append(char.wardrobe)
    return ", ".join(parts)


def resolve_subjects_text(shot: ShotPlan, plan: Optional[ProductionPlan] = None) -> str:
    """Build a text description of all subjects on screen for prompt injection."""
    if not shot.subjects_on_screen:
        return ""
    parts = []
    for subj in shot.subjects_on_screen:
        desc = subj.visual_description
        if subj.character_id and plan:
            char = plan.get_character(subj.character_id)
            if char:
                desc = describe_character(char)
        if subj.position_or_relation:
            desc += f", {subj.position_or_relation}"
        parts.append(desc)
    if len(parts) == 1:
        return parts[0]
    return " and ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 2 else " and ".join(parts)


# ── Dialogue Formatting ──────────────────────────────────────────────

def format_dialogue_for_video(shot: ShotPlan, plan: Optional[ProductionPlan] = None) -> str:
    """Format dialogue beats into prose suitable for video prompts.

    Returns text like: 'The woman in red says "Hello there" with a warm smile,
    then the man in the suit replies "Welcome back" while nodding.'
    """
    if not shot.dialogue_beats:
        return ""

    lines = []
    for beat in shot.dialogue_beats:
        # Build speaker description
        speaker_desc = "a person"
        if beat.speaker_id and plan:
            char = plan.get_character(beat.speaker_id)
            if char:
                speaker_desc = describe_character(char, include_wardrobe=False)

        # Build the line
        parts = [f'{speaker_desc} says "{beat.spoken_text}"']
        if beat.delivery:
            parts.append(f"{beat.delivery}")
        if beat.physical_cue:
            parts.append(f"{beat.physical_cue}")
        lines.append(", ".join(parts))

    return ". ".join(lines)


def format_dialogue_metadata(shot: ShotPlan) -> list[str]:
    """Format dialogue as metadata strings for clip plan output."""
    if not shot.dialogue_beats:
        return []
    return [
        f"{beat.speaker_id or 'unknown'}: \"{beat.spoken_text}\""
        for beat in shot.dialogue_beats
    ]


# ── Camera Description ───────────────────────────────────────────────

def format_camera_text(cam: "CameraPlan") -> str:
    """Build a natural camera description from CameraPlan fields."""
    parts = []

    # Framing first
    parts.append(cam.framing)

    # Angle
    if cam.angle:
        parts.append(cam.angle)

    # Movement
    if cam.movement:
        intensity_prefix = {
            "static": "steady",
            "subtle": "gentle",
            "moderate": "",
            "dynamic": "energetic",
        }.get(cam.movement_intensity, "")
        if intensity_prefix:
            parts.append(f"{intensity_prefix} {cam.movement}")
        else:
            parts.append(cam.movement)

    # Lens feel
    if cam.lens_feel:
        parts.append(cam.lens_feel)

    return ", ".join(parts)


# ── Action Beat Assembly ─────────────────────────────────────────────

def format_action_sequence(shot: ShotPlan) -> str:
    """Assemble action beats into chronological prose."""
    beats = list(shot.action_beats)
    if shot.performance_beats:
        beats.extend(shot.performance_beats)
    if not beats:
        return ""
    return ". ".join(beats)


# ── Environment & Style Block ────────────────────────────────────────

DEFAULT_VISUAL_STYLE = "photorealistic realism"


def build_visual_style_default_block(
    *,
    structured_style_present: bool = False,
) -> str:
    """Return the shared visual-style precedence rule for prompt writers.

    This is model guidance, not a server-side prompt classifier. Structured
    style fields and visual references stay authoritative; only an otherwise
    unspecified visual medium receives the product default.
    """
    fallback = (
        "- A separate structured model workflow already supplies the visual "
        "medium/style. Do not apply the product photorealism default or invent "
        "a competing generic style; leave generic visual_style empty unless "
        "the user's request explicitly authored one.\n"
        if structured_style_present else
        f"- Only when neither source specifies a visual style, use {DEFAULT_VISUAL_STYLE} "
        "with natural materials, believable lighting, and physically plausible detail.\n"
    )
    return (
        "VISUAL STYLE DEFAULT:\n"
        "- Preserve any visual medium or style explicitly authored by the user.\n"
        "- When a supplied visual reference defines the medium or style, preserve "
        "that reference style unless the user explicitly requests a change.\n"
        + fallback
        + "- Never replace an authored stylized medium with photorealism."
    )


def build_visual_style_refinement_block() -> str:
    """Lock an upstream-resolved style during a refinement-only pass."""
    return (
        f"{build_visual_style_default_block()}\n\n"
        "REFINEMENT STYLE LOCK:\n"
        "- The upstream Director has already resolved this prompt's visual style.\n"
        "- Preserve every supplied VISUAL STYLE anchor exactly.\n"
        "- If no style anchor is present, the prompt is reference-defined or "
        "style-neutral; do not introduce, replace, or infer a style during this "
        "refinement pass."
    )


def build_visual_style_authority_block(resolved_style: object) -> str:
    """Render a binding structured style selected before an LLM call."""
    if not isinstance(resolved_style, str) or not resolved_style.strip():
        return ""
    return (
        "AUTHORITATIVE VISUAL STYLE:\n"
        f"- Use exactly this visual medium/style: {resolved_style}\n"
        "- Do not replace it with an aesthetic proposed by the planner output."
    )


def build_visual_style_provenance_block() -> str:
    """Tell structured planners how to label their visual-style decision."""
    return (
        "VISUAL STYLE PROVENANCE:\n"
        "- Set visual_style_source to authored_request only when visual_style "
        "comes from an explicit style or style-change request in the user's "
        "freeform concept/story.\n"
        "- Otherwise set visual_style_source to planner_default. Never label a "
        "planner-invented aesthetic as authored_request."
    )


def resolve_visual_style(
    authored_style: object,
    *,
    has_visual_reference: bool = False,
    structured_style_present: bool = False,
) -> str:
    """Resolve a structured style without inspecting free-form prompt text."""
    if isinstance(authored_style, str) and authored_style.strip():
        return authored_style
    if structured_style_present:
        return ""
    return "" if has_visual_reference else DEFAULT_VISUAL_STYLE


def resolve_planned_visual_style(
    authored_style: object,
    planned_style: object,
    *,
    has_visual_reference: bool = False,
    planned_style_source: object = "",
    structured_style_present: bool = False,
) -> str:
    """Resolve planner output using structured provenance, not prompt scanning.

    A structured choice and a visual reference are locks. Otherwise the
    planner may carry a style authored in freeform scene/story text into its
    structured output; photorealism is only the final missing-value fallback.
    """
    if isinstance(authored_style, str) and authored_style.strip():
        return authored_style
    if has_visual_reference:
        if (
            planned_style_source == "authored_request"
            and isinstance(planned_style, str)
            and planned_style.strip()
        ):
            return planned_style
        return ""
    if structured_style_present:
        if (
            planned_style_source == "authored_request"
            and isinstance(planned_style, str)
            and planned_style.strip()
        ):
            return planned_style
        return ""
    if isinstance(planned_style, str) and planned_style.strip():
        return planned_style
    return DEFAULT_VISUAL_STYLE


def anchor_visual_prompt(
    prompt: str,
    authored_style: object,
    *,
    has_visual_reference: bool = False,
) -> str:
    """Attach one structural style anchor without inspecting prompt content.

    The anchor is a suffix so model dialects with a mandatory first token (for
    example Qwen image edit's ``create new scene``) remain valid.
    """
    style = resolve_visual_style(
        authored_style,
        has_visual_reference=has_visual_reference,
    )
    if not style:
        return prompt
    return f"{prompt} VISUAL STYLE: {style}."


def anchor_reference_edit_visual_style(
    prompt: str,
    authored_style: object,
) -> str:
    """Carry an explicit style through a reference-edit prompt dialect.

    Qwen-style edit prompts keep their required opening token. When the
    standard closer would also preserve the reference art style, replace only
    that conflicting clause while retaining the identity/body fidelity lock.
    """
    if not isinstance(authored_style, str) or not authored_style.strip():
        return prompt
    reference_style_closer = (
        "Preserve character identity, attire, body attributes, and the art "
        "style of the reference image."
    )
    identity_closer = (
        "Preserve character identity, attire, and body attributes from the "
        "reference image."
    )
    base = prompt.rstrip()
    if base.endswith(reference_style_closer):
        base = base[:-len(reference_style_closer)].rstrip()
        return (
            f"{base} Apply the authored visual style: {authored_style}. "
            f"{identity_closer}"
        )
    return f"{base} Apply the authored visual style: {authored_style}."


def format_scene_setting(shot: ShotPlan) -> str:
    """Build environment + lighting + mood + style text."""
    parts = []
    if shot.environment:
        parts.append(shot.environment)
    if shot.lighting:
        parts.append(shot.lighting)
    if shot.mood:
        parts.append(f"{shot.mood} atmosphere")
    if shot.visual_style:
        parts.append(shot.visual_style)
    return ", ".join(parts)


# ── Detection Helpers (used by validators) ───────────────────────────

def detect_anti_patterns(text: str, mode: str) -> list[str]:
    """Scan prompt text for policy violations. Returns list of warnings."""
    warnings = []
    text_lower = text.lower()

    # Check montage language (all video modes)
    if mode in ("t2v", "i2v", "a2v", "retake", "extend"):
        for phrase in MONTAGE_LANGUAGE:
            if phrase in text_lower:
                warnings.append(f"Montage language detected: '{phrase}' — single-shot prompts cannot use this")

    # Check image-specific anti-patterns
    if mode == "image_gen":
        for phrase in IMAGE_META_LANGUAGE:
            if phrase in text_lower:
                warnings.append(f"Meta-language in image prompt: '{phrase}' — use action verbs instead")
        for verb in IMAGE_ACTION_VERBS:
            if verb in text_lower:
                warnings.append(f"Action verb in image prompt: '{verb}' — image prompts describe static frames only")

    # Check vague camera language (all modes)
    for phrase in VAGUE_CAMERA:
        if phrase in text_lower:
            warnings.append(f"Vague camera language: '{phrase}' — use explicit camera terms")

    # Check abstract emotions
    for phrase in ABSTRACT_EMOTIONS:
        if phrase in text_lower:
            warnings.append(f"Abstract emotion: '{phrase}' — use visible physical cues instead")

    return warnings


def detect_character_names_in_prompt(text: str, characters: Optional[list[CharacterProfile]] = None) -> list[str]:
    """Check if character names appear outside of quoted dialogue."""
    if not characters:
        return []

    warnings = []
    # Remove quoted dialogue before checking
    text_without_dialogue = re.sub(r'"[^"]*"', '', text)
    text_without_dialogue = re.sub(r"'[^']*'", '', text_without_dialogue)

    for char in characters:
        if char.display_name and len(char.display_name) > 2:
            if char.display_name.lower() in text_without_dialogue.lower():
                warnings.append(
                    f"Character name '{char.display_name}' used outside dialogue — "
                    f"describe by appearance instead"
                )
    return warnings


# ── Prompt Compression Helpers ───────────────────────────────────────

_REDUNDANT_ADJECTIVE_PATTERNS = [
    (r'\b(very|really|extremely|incredibly|absolutely)\s+', ''),  # intensity modifiers
    (r'\b(beautiful|gorgeous|stunning)\s+(beautiful|gorgeous|stunning)\b', r'\1'),  # doubled
]

_FILLER_PHRASES = [
    "in this scene", "we can see", "the scene shows",
    "the viewer sees", "it appears that", "there is a",
    "we are shown", "the shot reveals", "it is clear that",
]


def compress_prompt_text(text: str) -> tuple[str, int]:
    """Remove redundancy and filler from prompt text.

    Returns (compressed_text, chars_removed).
    """
    original_len = len(text)
    result = text

    # Remove filler phrases
    for filler in _FILLER_PHRASES:
        result = re.sub(re.escape(filler), '', result, flags=re.IGNORECASE)

    # Remove redundant adjective patterns
    for pattern, replacement in _REDUNDANT_ADJECTIVE_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # Collapse multiple spaces
    result = re.sub(r'  +', ' ', result).strip()

    # Collapse multiple commas/periods
    result = re.sub(r',\s*,', ',', result)
    result = re.sub(r'\.\s*\.', '.', result)

    return result, original_len - len(result)


# ── System Prompt Builders (compact rule blocks for LLM) ─────────────

def build_character_rules_block(
    has_reference: bool,
    characters: Optional[list[CharacterProfile]] = None,
    *,
    preserve_names: bool = False,
) -> str:
    """Build the character-related rules block for LLM system prompts."""
    if preserve_names:
        base = (
            "H3 CHARACTER RULES:\n"
            "- Preserve every user-supplied proper character/person name and "
            "series, film, or franchise in video prompts exactly as written.\n"
            "- Keep the proper name together with useful visible traits; never "
            "collapse a named identity to a generic man, woman, or character.\n"
            "- Do not invent names that the user or screenplay did not supply."
        )
    else:
        from .guide_loader import load_guide
        base = load_guide("character_identification_rules.md")
        if not base:
            base = "CHARACTER RULES:\n- Describe characters by appearance, not names."

    lines = [base]
    if has_reference:
        # IMPORTANT: this rule split is the fix for the "user uploads
        # selfie tagged 'man in black', screenplay turns him into a
        # knight, future shot prompts keep saying 'man in black' and
        # the image generator never renders armor" bug. The reference
        # photo supplies IDENTITY (face, body type, gender). The
        # CURRENT shot's costume/role/state comes from the screenplay.
        # Telling the LLM to "base descriptions on the reference photo"
        # without this distinction made it freeze the user's reference
        # outfit into every downstream prompt.
        lines.append(
            "- The reference photo supplies IDENTITY (face, build, gender, "
            "approximate age). It does NOT freeze the character's costume "
            "or role for the rest of the story."
        )
        lines.append(
            "- Describe each character's APPEARANCE in each shot based on "
            "what the SCREENPLAY says they look like in that scene — costume, "
            "armor, props, state (wet hair, torn clothing, etc.). The "
            "screenplay overrides the reference photo's costume."
        )
        lines.append(
            "- Example: reference photo shows 'man in black t-shirt'. "
            "Screenplay says character is a knight. Shot descriptions: "
            "'tall man in gleaming silver plate armor' (NOT 'man in black')."
        )
    if characters:
        lines.append(
            "- Character reference (retain each supplied name/label with its visual description):"
            if preserve_names
            else "- Character reference (use ONLY the visual descriptions below, never names):"
        )
        for c in characters:
            desc = describe_character(c)
            if preserve_names and c.display_name:
                lines.append(f"  * {c.id} / {c.display_name}: {desc}")
            else:
                # Do NOT include display_name in image-driven workflows —
                # image models need visible descriptions, not bare names.
                lines.append(f"  * {c.id}: {desc}")
        lines.append(
            "- The descriptions above are REFERENCE-PHOTO descriptions. "
            "If the screenplay transforms a character (e.g. into a knight, "
            "wizard, vampire, queen), describe them as transformed in shot "
            "prompts. The reference photo is for IDENTITY only."
        )
    return "\n".join(lines)


def build_video_rules_block() -> str:
    """Build the video prompt rules block for LLM system prompts."""
    from .guide_loader import load_guide
    return load_guide("video_prompt_rules.md") or "VIDEO PROMPT RULES:\n- One flowing paragraph, present tense."


def build_camera_style_block(
    *,
    structured_style_present: bool = False,
) -> str:
    """Build the adaptive camera style guidance."""
    from .guide_loader import load_guide
    camera = (
        load_guide("camera_style_guidance.md")
        or "CAMERA STYLE:\n- Match complexity to content."
    )
    return (
        f"{build_visual_style_default_block(structured_style_present=structured_style_present)}"
        f"\n\n{camera}"
    )
