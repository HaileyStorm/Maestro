"""
Explicit Content Guidance — manages request-authorized prompt injection.

When mature mode is OFF or the request did not explicitly ask for this
guidance, Maestro leaves the model prompt unchanged.
When an explicit request is authorized: injects strong, server-owned explicit
                        prompt-authoring guidance. Each block is SELF-GATING —
                        it applies only to adult sexual or graphic-violence
                        material already requested by the user and forbids
                        inventing or escalating content.

The caller must pass ``nsfw=True`` only after the server has verified Mature
Mode consent, a non-public provider, and literal ``explicit_output: true``.
Mature Mode alone is permission, not a request to make every prompt explicit.
Guide text is cached for the life of the Maestro Python process. Installed or
updated guidance and the routing code that authorizes it take effect after a
Maestro restart; there is intentionally no production live-reload path.

Architecture (after the Director migration):
  - The mature-mode guidance —
    director/nsfw_{screenplay,video,image}_rules.md for the planners,
    plus enhance/nsfw_shared.md for the refine path — are VERSION-CONTROLLED and
    explicit prompt-authoring rules. They ship with every install, so Director
    works with no supplement pack and no download.
  - The old optional "content supplement pack" (a separately-downloaded zip)
    has been fully retired — nothing reads it anymore. Every guide is
    version-controlled and self-gating (it applies only to explicit material
    already requested by the user).
"""

from .guide_loader import load_guide


# Durable Director snapshots use this private, server-owned decision bit. A
# fresh request may never nominate it; recovery may only reuse the persisted
# literal boolean created after the mature-policy/request gate passed.
EXPLICIT_GUIDANCE_SNAPSHOT_KEY = "_director_explicit_llm_guidance"


# ── Guidance text loaders ────────────────────────────────────────────
# All explicit-content guides are version-controlled and server-owned
# (director/nsfw_{screenplay,video,image}_rules.md + enhance/nsfw_shared.md);
# load_guide reads them from the repo, so they ship with every install. The
# block is self-gating — it applies only when the scene is actually sexual.


def get_nsfw_video_guidance() -> str:
    """Explicit-content guidance block for video prompt generation.

    Loaded from the VERSION-CONTROLLED guide under
    llm_guides/director/ (migrated off the optional supplement pack in the
    Director migration), so it ships with every install — Director mature mode
    needs no pack and no download.
    """
    return load_guide("nsfw_video_rules.md")


def get_nsfw_image_guidance() -> str:
    """Explicit-content guidance for image prompt generation (version-controlled)."""
    return load_guide("nsfw_image_rules.md")


def get_nsfw_screenplay_guidance() -> str:
    """Explicit-content guidance for screenplay writing (version-controlled)."""
    return load_guide("nsfw_screenplay_rules.md")


def get_nsfw_enhance_guidance() -> str:
    """Explicit-content guidance for enhance/refine and Director Pass-3 polish.

    Reuses the SAME register-faithful, version-controlled guidance as Studio mode's enhancer
    (llm_guides/enhance/nsfw_shared.md), so the two refine paths stay in sync and
    there is a single mature-enhance guide to maintain.
    """
    from services.guide_loader import load_guide as _load_shared
    return _load_shared("enhance", "nsfw_shared")


def inject_content_guidance(system_prompt: str, nsfw: bool, mode: str = "video") -> str:
    """Inject content guidance into a system prompt.

    When nsfw=True:  Injects explicit prompt-authoring guidance (version-controlled
                     guides under llm_guides/). If a guide file is
                     missing, this is a no-op — the system prompt is
                     returned unchanged.
    When nsfw=False: Return the system prompt unchanged.

    For video/image modes: injects NEAR THE TOP (after the first paragraph)
    so the LLM sees the directive before absorbing other rules.
    For enhance mode:      appends at the end (shorter prompt, less risk
                           of burying).
    """
    if not nsfw:
        return system_prompt

    if nsfw:
        guidance = {
            "video": get_nsfw_video_guidance,
            "image": get_nsfw_image_guidance,
            "enhance": get_nsfw_enhance_guidance,
            "screenplay": get_nsfw_screenplay_guidance,
        }

        if mode == "director":
            content_block = (
                get_nsfw_screenplay_guidance()
                + "\n\n"
                + get_nsfw_video_guidance()
                + "\n\n"
                + get_nsfw_image_guidance()
            )
        elif mode == "both":
            content_block = (
                get_nsfw_video_guidance() + "\n\n" + get_nsfw_image_guidance()
            )
        else:
            getter = guidance.get(mode, guidance["video"])
            content_block = getter()

        # If a guide file is missing or empty the loader returns "".
        # In that case there's nothing to inject — return the system
        # prompt unchanged rather than wedging blank lines into it.
        if not content_block.strip():
            print(
                f"[NSFW] inject_content_guidance(mode={mode!r}): "
                "no content to inject (guide file missing or empty) "
                "— system_prompt returned unchanged"
            )
            return system_prompt
        print(
            f"[NSFW] inject_content_guidance(mode={mode!r}): "
            f"injected {len(content_block)} chars of explicit-content guidance"
        )
    if mode == "enhance":
        return f"{system_prompt}\n\n{content_block}"

    # For video/image: inject after the first paragraph (role line) so the
    # LLM sees the directive early, before all the formatting/style rules.
    lines = system_prompt.split("\n\n", 1)
    if len(lines) == 2:
        return f"{lines[0]}\n\n{content_block}\n\n{lines[1]}"
    else:
        return f"{content_block}\n\n{system_prompt}"


# Backwards compatibility alias
def inject_nsfw_if_enabled(system_prompt: str, nsfw: bool, mode: str = "video") -> str:
    """Deprecated alias — use inject_content_guidance() instead."""
    return inject_content_guidance(system_prompt, nsfw, mode)
