# H3 motion-LoRA acceptance pack

Status: source- and CPU-ready on 2026-08-25. No generation or GPU acceptance
has been performed. The neutral fixture/rubric design was refined first with
Grok 4.5 Expert and then through the live Grok Build 4.6 route using public,
sanitized briefs; no Maestro project, private prompt, media, identity, path, or
operator record was sent. The first one-turn Grok Build attempt failed before
output at its turn cap; the bounded two-turn request completed.

This pack compares native MiniMax H3 Ref2VA against one optional motion LoRA
at strengths `0.5`, `0.7`, `0.9`, and `1.0`. Hold checkpoint, reference,
prompt, seed, resolution, duration, scheduler, and attention engine constant.
Run the native baseline first. Do not stack Dasiwa, Turbo, SLA, Spectrum,
MATLOW, or another motion/style LoRA in this matrix.

## Neutral fixtures first

Use the same adult reference character and clearly visible wardrobe across all
four fixtures. Target 4-6 seconds, 24 fps, and native audio where available.

1. **Motion and anatomy:** the character in a blue zip hoodie and dark jeans
   walks three steps, stops, turns 180 degrees on the left foot, raises both
   arms overhead, lowers them, and resumes a natural gait. Preserve face,
   body proportions, joint direction, clothing folds, and continuous motion.
2. **Identity and wardrobe:** a close tracking shot follows the character
   speaking while adjusting a patterned scarf, then unbuttoning and rebuttoning
   one jacket cuff. Preserve face, eyes, hair, scarf pattern, jacket mark,
   micro-expression, hand anatomy, and fabric response.
3. **Style and visible text:** the reference character stands beside a glass
   door printed with `H3-TEST` and `2026`. A flat 2D sticker of the same face
   briefly appears on the glass while the character remains three-dimensional.
   Preserve identity, legible text, lighting, and the boundary between styles.
4. **Audio and particles:** the character claps twice, then says the exact
   phrase `one two three check` while confetti falls past the face. Align clap
   impacts, mouth shapes, syllables, and particle motion without changing the
   reference identity or wardrobe.

Two additional diagnostic cards separate locomotion from fine-motor/occlusion
failures:

- **Door contact:** one continuous side-tracking shot follows three steady
  steps, a stop, two visible knuckle contacts on a wooden door, then the exact
  line `I am here`. Score planted feet, opposite arm swing, hand/door contact,
  two knock onsets, camera speed, and lip sync.
- **Mug occlusion:** one slow dolly-right shot follows a seated character
  grasping a mug, taking one sip, returning it to the same ring mark, placing
  both palms down, then saying `That is enough`. Score grasp topology, mug/table
  contact, identity through occlusion, parallax, clink timing, and lip sync.

## Mature-content variants

These are parameterized structures, not a second execution path. Replace the
bracketed fields and reuse the same baseline/strength/seeding protocol.

- `[ADULT_REFERENCE_CHARACTER]` in `[WARDROBE]` performs a slow
  `[CONSENSUAL_INTIMATE_MOTION]` while maintaining eye contact and natural
  breathing. Preserve face, body proportions, wardrobe details, joint
  coherence, continuous motion, and the authored visual style.
- A close tracking shot follows `[ADULT_REFERENCE_CHARACTER]` adjusting or
  removing `[SPECIFIC_GARMENT]` during `[CONSENSUAL_ACTION]` while saying
  `[SHORT_PHRASE]`. Preserve exact identity, fabric physics, anatomy, and
  audio-visual lip synchronization.

Maestro does not inspect or classify the subject matter. The mature fixtures
exercise the same local model, request, queue, output, and review contracts as
the neutral fixtures.

## Scoring

Score each axis from `0` through `4`: `0` unusable, `1` major failure, `2`
partial with frequent defects, `3` good with minor defects, and `4` excellent
without a regression from baseline.

- motion continuity and trajectory stability;
- anatomy, hands, joint direction, and body proportions;
- reference identity, hair, wardrobe, marks, and fabric behavior;
- stable 2D/3D/render style without bleed;
- visible text, logo, particle, and edge clarity;
- dialogue/impact/audio synchronization;
- prompt/action completion and absence of refusal/evasion;
- overall preference versus the native baseline.

Record the individual axes rather than only an average. A useful adapter raises
motion/anatomy scores while holding identity, texture, color, and audio at the
baseline. Flag these characteristic regressions explicitly: over-smoothing,
waxy/plastic faces, frozen eyes, identity or wardrobe drift, muted color or
particles, limb/joint collapse, text corruption, audio drift, black/truncated
output, generic substitution, or ignored motion.

Suggested pass rule: every required axis is at least `3`, and no axis is `0`.
Do not average away a catastrophic identity, topology, camera, temporal, or
audio-sync failure. If native audio is unavailable for a tested path, record
the audio axis as unsupported rather than scoring silence as a model failure.

## Review routing

Local deterministic checks own container validity, duration, 24-fps video,
audio presence/rate, finite samples, seed/profile identity, cancellation,
recovery, and output finality. Human review owns usefulness and final keep/drop.

Grok may provide a second visual review when the owner explicitly selects the
media batch for external review. Send only the chosen output/reference/prompt
needed for that comparison; never upload unrelated project data, private paths,
account records, or hidden prompts. Record the reviewer/model and preserve the
local output as the authority. Grok advice is comparative evidence, not a
release decision and not a Maestro moderation layer.
