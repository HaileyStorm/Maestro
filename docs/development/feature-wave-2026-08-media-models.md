# Maestro Media and Model Feature Wave — August 2026

Status: evidence captured; merge train ready to begin

Evidence date: 2026-08-15

This is the implementation companion to
`feature-wave-2026-08.md`. It covers the later media/model intake, including
community replies and issue reports. A saved link is not an endorsement, and
an upstream workflow is not automatically a Maestro dependency.

## Product decisions

| Candidate | Decision | Initial Maestro outcome |
| --- | --- | --- |
| MiniMax Music 3 | Required; highest-priority new model integration | Native full-song generation in Studio Music and Director, with an isolated SGLang-Omni runtime adapter |
| CharacterSheet LoRAs | Adopt selectively after license/artifact review | Reference Studio character-sheet action with a model dropdown, VLM planning/QA, and Qwen Image Edit repair |
| ComfyUI-H3-Multishot | Extract invariants; do not import the graph | Native H3 dialogue-turn planner, per-speaker conditioning, seam-safe recovery, and later multi-speaker research |
| H3 FaceRefine | Adapt the repair pattern; do not depend on the node | Confidence-gated native face-region repair that preserves audio and original frames on failure |
| ReDetail LTX-2.5 | Optional experiment only | Clearly labeled creative/generative refinement tier, never a faithful-upscale default |
| MAGI-2-preview | Reject current local integration; watch for distilled releases | No consumer install path for its current 8-Hopper/roughly-307GB model package |
| H3 community speed/step claims | Benchmark leads only | Add controlled A/B matrices; no default changes from anecdotes |

## Wave M1 — MiniMax Music 3

**Priority:** required and first in this companion train.

MiniMax Music 3 already fits Maestro's Audio > Music product surface better
than a new standalone UI. Maestro presently has editable style and lyric fields,
instrumental mode, a configured writing assistant, model-family filtering,
sidecar restoration, cancellation-capable generation jobs, and final-output
handling. The integration should extend those contracts rather than create a
parallel music application.

The official card describes complete songs up to five minutes from a music
description and tagged lyrics. It publishes 32 kHz, 16-bit stereo WAV output,
uses an 8B global plus 0.6B local model, and documents both a Diffusers modular
pipeline and an OpenAI-compatible SGLang-Omni server. Full precision is stated
to fit under 24GB; CPU offload is roughly 22GB, while layer streaming can reach
8GB at a substantial speed cost. The current path is non-streaming.

### Runtime boundary

Implement Music 3 as the first concrete SGLang trial, but keep the engine
resolved per model and purpose:

1. Add a `music_generation` engine capability beside the planned text/VLM
   engine contract. The first candidate is `sglang_omni`; an in-process
   Diffusers adapter remains a fallback experiment, not an automatic fallback.
2. Keep `provider`, `engine`, and `model` separate in configuration and saved
   provenance. An engine choice for Music 3 must not replace llama.cpp for chat
   or planning.
3. Probe exact server/model revision, endpoint capability, cancellation,
   health, resource release, maximum acoustic frames, and output format before
   accepting work.
4. Fail before generation when the selected engine cannot honor lyrics,
   duration, or required output. Do not silently discard unsupported controls.
5. Keep SGLang-Omni isolated from Maestro's pinned WanGP/PyTorch environment.
   Linux is the first runtime target; Windows needs its own exact-revision
   acceptance rather than inheriting a Linux claim.

### Initial user experience

- Reuse Audio > Music's description, style/caption, lyrics, and instrumental
  controls.
- Add Music 3-specific duration and seed controls only when the model advertises
  them. Show the five-minute limit truthfully.
- Provide section-tag assistance for `[Intro]`, `[Verse]`, `[Pre-Chorus]`,
  `[Chorus]`, `[Bridge]`, and `[Outro]`, while explaining that the model is
  generative rather than a strict symbolic sequencer.
- Let the existing writing assistant draft a sectioned song, then keep every
  field editable before generation.
- Save style, lyrics, section plan, instrumental/vocal intent, resolved engine,
  model revision, duration, seed, and output format in project-scoped metadata.
- Run final decoded-audio validation and apply Maestro's existing delivery
  loudness/true-peak policy without altering the musical dynamics beyond the
  declared constant safety gain.
- Support cancel, exact job ownership, restart recovery, and partial download
  cleanup even though model inference itself is non-streaming.

### Director uses, in order

1. Generate a complete song from the Director music brief and sectioned lyrics.
2. Generate an instrumental score from a story/shot mood arc.
3. Align a Director edit plan to the accepted song's measured sections and
   duration; do not ask the music model to guarantee exact bar boundaries.
4. Offer variations that preserve the user's editable song brief and seed
   lineage.
5. Defer reference-song continuation, humming input, and deterministic
   multi-singer assignment until a real conditioning interface exists.

### Community evidence and deferrals

- The released package has a decoder/audio VAE path but not the semantic RVQ
  encoder requested for arbitrary reference-song conditioning.
- Reference-song and humming conditioning have requests but no supported
  official interface.
- Prompt-only multi-singer instructions have a positive community report but
  are explicitly not reliable enough for a deterministic speaker-control UI.
- Community training work is expensive and inconclusive: one report describes
  35GB of cached embeddings for 299 tracks, hours on an H200, and substantial
  cost while still working toward a compatible encoder. Treat training as a
  research track, not a launch dependency.
- GGUF and Apple-Silicon ports are community experiments. They may become
  runtime candidates only after exact output, long-song, cancellation, and
  resource tests.

### Acceptance matrix

- 30-second, 90-second, and near-five-minute instrumental and vocal songs;
- sectioned lyrics, Unicode lyrics, blank lyrics, and invalid tag handling;
- deterministic seed replay within the same model/runtime revision;
- measured load time, generation time, peak VRAM/RAM, output duration, sample
  rate, channel count, and decoded-frame integrity;
- cancellation before admission, during load, during generation, and before
  final publication;
- model switch/unload, server restart, stale-request fencing, and project reload;
- Studio Music, Director soundtrack, LAN-authorized parity, and private/no-store
  operation status;
- comparison of SGLang-Omni and Diffusers only when both support the exact same
  model revision and request contract.

## Wave M2 — Character sheets as a Reference Studio workflow

**Decision:** implement as a composable workflow, with the Dynamic Krea 2 LoRA
an explicit experimental choice rather than the silent default.

The CharacterSheet repository is a LoRA collection, not a complete model. Its
useful variants target FLUX.2 Klein 9B and Krea 2. The dynamic Krea LoRA expects
a structured bracketed prompt, uses a Qwen3-VL prompt workflow, and can produce
hero, turnaround, action, silhouette, expression/state, detail, and metadata
panels. The card also documents prompt sensitivity, text errors, imperfect
cross-view identity, mainly-human training data, and a `ref_boost` fidelity dial
whose excessive values can damage edits.

### Proposed workflow

1. **Anchor:** generate or choose one accepted high-quality character image.
   The anchor model remains user-selectable; existing Flux, Krea, Qwen, or
   imported project media can qualify.
2. **Sheet model:** expose a dropdown:
   - `Quad — FLUX.2 Klein` as the conservative default where supported;
   - `Quad — Krea 2` as the stronger Krea alternative;
   - `Dynamic — Krea 2 (experimental)` for the structured concept sheet;
   - later, `Triple — FLUX.2 Klein` for lower-cost turnaround-only work.
3. **Plan:** use the selected local VLM (Qwen3-VL when available) to describe
   invariant identity traits and generate the exact structured prompt required
   by the chosen LoRA. This is creative planning, not moderation.
4. **Generate:** keep native resolution and model-specific defaults server-owned.
   Initial evidence suggests 1536x1024, about 8 steps for Klein, about 10 steps
   for accelerated Krea variants, and an optional slower Krea Raw profile.
5. **Review:** run local VLM checks for character identity, view coverage,
   duplicated/missing panels, layout, and obvious label corruption. Surface the
   evidence and keep user acceptance authoritative.
6. **Repair:** pass only failed panels or callouts to an existing Qwen Image Edit
   model. Preserve accepted panels and the original anchor; never regenerate the
   entire sheet merely because one label failed.
7. **Publish:** save the accepted sheet as a Reference Studio artifact with
   anchor/model/LoRA hashes, prompt, seed, VLM/editor revisions, per-panel
   repair lineage, terms/license acceptance, and identity references available
   to Director, LTX, H3, Recast, and Repaint.

### Guardrails and tests

- Review the Civitai model license and each base-model license before any
  managed catalog listing. User-imported artifacts remain subject to existing
  model-import and terms handling.
- Do not assume Krea image-edit LoRAs train like ordinary text-to-image LoRAs;
  community evidence points to dual reference-token and VLM-grounded
  instruction conditioning.
- Limit supported initial subjects to the artifact's demonstrated human range,
  label other categories experimental, and measure rather than promise identity.
- Test missing/duplicate views, cropped limbs, failed labels, repaired panels,
  changed anchor, cancellation, reload, model unavailability, terms rejection,
  and exact project/account scoping.

## Wave M3 — H3 multi-speaker dialogue without pretending native support

**Decision:** work around the single-speaker conditioner now; reserve native
multi-speaker conditioning for a later research/training track.

ComfyUI-H3-Multishot documents the limitation directly: several reference
subjects can be blended visually, but only Subject 1 is described as speaking
because H3 voice conditioning is single-speaker. Maestro should not hide that
limitation or let both visible characters appear to speak from one voice track.

### Product workaround

1. Director creates an audio-first dialogue master with speaker identity,
   utterance text, start/end timing, pauses, ambience, music, and effects.
2. Split H3 generation at speaker-turn boundaries. For each speaking turn,
   make that speaker the sole H3 voice-conditioned subject while keeping every
   visible character's image references and prior-scene continuity.
3. Prompt speech ownership explicitly. Non-speaking visible characters receive
   silence/closed-mouth/observing direction; no line crosses a native shot seam.
4. Use Maestro's existing native segment checkpoint and recovery model. Publish
   each verified segment before attempting the final join so an OOM cannot lose
   hours of completed work.
5. Preserve the dialogue master as the timing/source-of-truth audio. Rejoin
   clips under exact timing, then apply per-speaker lip-sync or face-region
   repair where H3 mouth motion is insufficient.
6. For rapid alternation in one camera setup, use multiple short turn clips with
   continuity handoff. If a cut is unacceptable, generate the shared visual
   plate once and apply temporally masked per-speaker face/lip repair rather
   than claiming simultaneous native conditioning.
7. Validate final speaker-to-face alignment, silent-character mouth motion,
   seam continuity, identity drift, audio duration/start times, and final true
   peak before publication.

The upstream prompting evidence also supports 24 fps for stable voice character,
an opening airlock, settled endings, and dialogue lengths matched to usable
speaking time. Adopt those as planner constraints, not hard-coded prose.

### Research/training track

Investigate a multi-speaker adapter only after the workaround is measurable:

- diarized multi-speaker audiovisual training data with speaker-specific voice
  references, temporal masks, silence examples, and overlapping-speech cases;
- separate speaker tokens or multi-audio cross-attention rather than concatenated
  references with ambiguous ownership;
- metrics for wrong-speaker mouth motion, dual-mouth artifacts, identity drift,
  voice similarity, timing error, and seam discontinuity;
- compatibility with ordinary single-speaker H3 checkpoints and a fail-closed
  fallback to the turn-based workflow.

This is a substantial model-research project and must not block the immediate
turn-based product path.

## Wave M4 — Native repair and creative refine tools

### Face-region repair

H3 FaceRefine's useful pattern is detection, temporal tracking, stabilized crop,
model-assisted regeneration, and compositing. Implement the pattern natively:

- confidence-gated subject/face selection and stable transform smoothing;
- exact frame/time mapping and original-audio preservation;
- user-selectable identity reference and bounded repair region;
- preview before replacement, with original frames retained for rollback;
- cancellation-safe temporary files and atomic final publication;
- fail open to the original frame when detection/tracking/repair confidence is
  insufficient.

Generalize to hands or arbitrary regions only after the face path has temporal
coherence evidence. The upstream issue list itself reports multishot and
whole-video concerns, so do not present this as solved by importing the node.

### ReDetail/LTX-2.5 creative refinement

Treat this as generative re-detailing, not faithful upscaling:

- label it `Creative refine (may invent detail)` next to existing faithful
  spatial-upscale options;
- default to 1.5x experiments rather than 2x on constrained systems;
- preflight both VRAM and host RAM, because community runs reported roughly
  65GB at 1.5x and over 80GB at 2x;
- add silence before a joint audio/video model processes a silent clip, then
  restore/preserve the intended final audio contract;
- offer conditioning-cache reuse only when model/revision and prompt identity
  match exactly;
- compare faces, labels, logos, and reference identity against FlashVSR and the
  original, with rollback always available.

## Explicit rejection/watchlist

### MAGI-2-preview

Do not add a managed install today. The current public stack targets eight
NVIDIA Hopper GPUs, has a 114B MoE model, and publishes roughly 307GB of model
artifacts. Even its preview and refiner cannot coexist on one 80GB GPU. Recheck
only when a distilled or consumer-targeted release has an exact license,
resource envelope, and local runtime path.

The architecture still offers one transferable idea: separate a cheap preview
decision from an expensive refiner, while never confusing the preview with a
delivery artifact.

## H3 benchmark leads, not defaults

- The repeated servasyy roundup post is deduplicated. Its same-seed result
  suggests 8V8A may cost little more than lower video-step counts when audio
  dominates, and reports a CK Attention gain on one 4090. Add both to the
  controlled matrix; do not stack CK, Sage, and Sol or change defaults without
  Maestro evidence.
- Community 25–32-step preferences are subjective leads. Measure audio, acting,
  motion, identity, time, VRAM, and failure rate across existing profiles.
- A 3-step whole-generation scout can change the selected result. Prefer the
  planned approximate TAEH3 preview; retain a coarse seed scout only as an
  explicit experiment.
- LightX2V H3 Turbo and experimental Kijai artifacts may be exact-version
  catalog/profile updates, not duplicate feature surfaces.

## Merge train

Keep one writer per shared cluster and land vertical slices in this order:

1. **Engine contract:** model/purpose-resolved engine configuration, capability
   probe, saved provenance, cancellation, and isolated lifecycle. No Music 3 UI
   is merged against a hard-coded server.
2. **Music 3 runtime slice:** model definition/install manifest, SGLang-Omni
   adapter, resource probe, generation job, output validation, and API tests.
3. **Music 3 product slice:** Studio Music controls, Director soundtrack use,
   recovery/status, sidecars, LAN parity, and UI/runtime tests.
4. **Reference artifact schema:** panel/view lineage and review/repair metadata
   that reuses existing Reference Studio privacy and publication rules.
5. **Character-sheet slice:** dropdown, prompt planner, LoRA execution, VLM QA,
   Qwen repair, and artifact adoption. Land terms/license handling with the
   model listing rather than afterward.
6. **Dialogue master and turn planner:** speaker/time schema, H3 per-turn
   requests, checkpointed segments, seam-safe rejoin, and final AV validation.
7. **Repair tools:** face-region repair first; creative ReDetail experiment only
   after resource preflight and labeling are in place.
8. **Benchmark promotions:** H3 step/attention/turbo/default changes land only
   from frozen A/B evidence, separately from feature implementation.

Every slice must leave `main` releasable, include an obsolescence audit, and
avoid adding a second operation manager, model catalog, project store, or output
publication path.

## Source and community evidence

- MiniMax Music 3 model card:
  <https://huggingface.co/MiniMaxAI/MiniMax-Music3>
- MiniMax Music 3 discussions on RVQ/reference/training/GGUF/Apple Silicon/
  humming/multi-singer control:
  <https://huggingface.co/MiniMaxAI/MiniMax-Music3/discussions>
- CharacterSheet model card:
  <https://huggingface.co/Alissonerdx/CharacterSheet>
- Krea identity-edit training discussion:
  <https://huggingface.co/conradlocke/krea2-identity-edit/discussions/16>
- LTX character identity reference workflow:
  <https://huggingface.co/Alissonerdx/LTX-Best-Face-ID>
- ComfyUI-H3-Multishot and prompting guide:
  <https://github.com/jlucasmcrell/ComfyUI-H3-Multishot>
- H3 Multishot compatibility, final-mix OOM, and drift reports:
  <https://github.com/jlucasmcrell/ComfyUI-H3-Multishot/issues>
- H3 FaceRefine and issue reports:
  <https://github.com/Carasibana/ComfyUI-H3-FaceRefine>
- MAGI-2-preview:
  <https://github.com/SandAI-org/MAGI-2-preview>
- ReDetail community report and resource discussion:
  <https://www.reddit.com/r/StableDiffusion/comments/1vo5vnz/redetail_upscale_minimax_h3_renders_with_the/>
- Deduplicated H3 roundup post:
  <https://x.com/servasyy_ai/status/2087343267277160510>
- H3 3-step preview discussion:
  <https://x.com/kiyoshi_shin/status/2087652240152756252>
- H3 25-step discussion:
  <https://www.reddit.com/r/StableDiffusion/comments/1vmjdiw/minimax_h3_25_steps_should_be_the_lowest_setting/>
