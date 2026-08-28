# H3 ecosystem intake — 2026-08-28

Status: public evidence captured and decisions recorded on 2026-08-28. This is
an intake note, not an implementation or acceptance claim. No candidate was
downloaded, installed, loaded, exposed in the catalog, or made a default, and
no GPU work or private prompt/media inspection was performed for this intake.

Source thread: Maestro Continuum. The owner marked this tranche as intended for
Maestro and asked for broad consideration under `docs/operations/INTAKE.md`,
including comments and linked sources rather than surface-level summaries.

This note is a dated successor to, not a replacement for:

- `docs/research/H3-ecosystem-watch-2026-08-25.md`
- `docs/research/H3-workflow-refresh-2026-08-23.md`
- `docs/development/minimax-h3-fast-runtime-research.md`
- `docs/development/feature-wave-2026-08-media-models.md`

The raw tranche contained **11 URLs**. All 11 are accounted for below.

## Current Maestro checkpoint

The intake started from repository checkpoint `c1f9bc1`.

The supplied candidates must extend rather than duplicate these shipped or
already-scaffolded surfaces:

- Director, Shot Deck, the persisted shot plan, H3 sequence planning, project
  queues, cancellation/recovery, and the normal output path already own
  multi-shot planning and execution.
- Character Sheet and Reference Studio already own anchors, panel roles,
  capability/readiness gates, project scope, repair lineage, and accepted
  reference artifacts.
- H3 native Quality/High, managed Turbo, LightX2V four-step, Spectrum, Sage,
  step-cache, and explicit experimental profiles already have distinct names
  and compatibility gates. A new accelerator is another controlled variable,
  not a replacement for the catalog or a global step change.
- Ordinary H3 style LoRAs already have a loading/strength path. A style tune
  should reuse it unless its tensor insertion or scheduler contract proves that
  it is not an ordinary LoRA.
- Prompt Coach, Director craft workflows, and the deterministic H3 prompt
  document already own native authoring. External agents and editors do not
  become a second composer, provider router, project store, or operation
  manager.
- The Prompt Rewriter lane from the 2026-08-25 train is active: its deterministic
  request/preview scaffold, blocked dependency closure, blocked runtime
  admission, and pinned-uv metadata producer exist, while wheel-byte replay,
  isolated installation, model execution, GPU output, and human acceptance
  remain open. This tranche does not jump ahead of or redefine that owner.
- Local creative work remains content-neutral. Negative prompt language and
  style exclusions are creative controls, never moderation or subject-matter
  classification.

## Decision rules

1. Extract the useful job or invariant; do not import an upstream UI, Comfy
   graph, agent framework, provider catalog, queue, or runtime wholesale.
2. Keep checkpoint family, LoRA, step schedule, sampler, attention engine,
   cache, quantization, and refiner as separate variables. First acceptance is
   one variable at a time and has no silent fallback or stacking.
3. Social demonstrations nominate fixtures and recipes. They do not establish
   quality, performance, compatibility, or a default without reproducible
   settings and local evidence.
4. Managed artifacts need exact revision, filename, size, digest, family and
   license/access binding. Existing H3 legal-access handling remains the owner;
   this intake adds no creative-content gate.
5. Linux and Windows remain independent runtime targets. RTX 5090 evidence on
   this host is required before a runtime candidate can be promoted here.

## Cluster A — native montage and Director editing

**Decision: Adapt / extract.** The cluster contributes missing invariants and
fixtures to Maestro's existing Director path; none of its external products or
hosted runtimes is adopted.

### OpenMontage

`calesthio/OpenMontage` was observed at
[`cd9f3c1f03368be87b140af494914b8ee4e3c7a4`](https://github.com/calesthio/OpenMontage/tree/cd9f3c1f03368be87b140af494914b8ee4e3c7a4);
the repository's pinned
[`LICENSE`](https://github.com/calesthio/OpenMontage/blob/cd9f3c1f03368be87b140af494914b8ee4e3c7a4/LICENSE)
is AGPL-3.0. Its useful ideas are capability-coverage preflight, explicit human
approval and delivery gates, keep/change analysis for reference footage,
auditable provider/cost decisions, and post-render ffprobe/frame/audio/subtitle
checks. Those strengthen Maestro's existing Director and finality contracts.

Its agent-first pipeline, Backlot UI, 100-plus tools, provider router, schemas,
budget ledger, project store, and optional cloud-key integrations are the wrong
product boundary for Maestro. Open issue signals around [missing provider
coverage](https://github.com/calesthio/OpenMontage/issues/542), [fixed-frame-rate
assumptions](https://github.com/calesthio/OpenMontage/issues/528), [invalid
temporal latents](https://github.com/calesthio/OpenMontage/issues/526), and
[skill/schema drift](https://github.com/calesthio/OpenMontage/issues/493)
reinforce fail-closed preflight; they do not justify adopting the stack. A
recent dependency security fix also argues for source pinning before copying
even a small implementation detail.

### MiniMax H3 Director Cut Studio

The current source moved during intake to
[`e3fa37b78548e918c676309d04c697e61226e98b`](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio/commit/e3fa37b78548e918c676309d04c697e61226e98b),
version `0.2.6-alpha.1`. GitHub repository metadata reported MIT during intake,
but an immutable app-level license was not independently established, so no
code-copy permission is inferred. It is a local PySide6/ComfyUI application
with its own timeline, project format, model bundle and runtime. That overlaps
Maestro rather than complementing it.

The useful refresh is its latest timeline contract: stable logical references
mapped per segment, clip-instance versus source separation, SHA-bound stale
analysis, independent time-coded dialogue/voice-over/lyrics layers, migration
repair, and bounded background enrichment/unload behavior. These belong in
Maestro's existing Director shot table, Shot Deck, sequence planner, recovery
seal, and output finality. No compatibility shim or project importer is planned
from this intake.

### Social montage and staged-transformation examples

- **@koldo2k stickman H3 demo — evidence role: watch-only craft fixture.** The post shows
  a 30.4-second, 1260x720 story/educational stickman sequence and claims no
  manually moved keyframes. The public record did not expose canonical prompt,
  seed, checkpoint, LoRAs, edit chain, or useful failure reports. Preserve it
  as a fixture lead for shot segmentation and motion continuity.
- **@bond_ai1 concrete-room transformation — evidence role: hosted-service fixture.**
  The Seedance 2.5 example claims a one-photo, staged concrete-to-finished-room
  sequence in about 20 minutes. The linked source and public mirror are
  anecdotal; a mirror also corrects the money conversion to roughly ¥300,000,
  not ¥3,000,000. Extract only the causal stage plan and before/after
  commitments. Seedance is not a Maestro dependency or H3 default.
- **@HeyAbhishek storyboard-to-anime workflow — evidence role: storyboard
  planning fixture.** The thread's useful contract
  is explicit panel order, stable design/clothing/framing/lighting/emotion,
  visual continuity, and anti-drift constraints. The thread reports a
  visual-only/no-audio request, so Maestro must retain H3's normal audio
  ownership rather than copy that example's limitation.

**Maestro outcome:** extend the existing Director/Shot Deck contracts only when
a later implementation wave can prove that a proposed field is not already
owned. Start with logical-reference/clip-source/stale-analysis invariants; keep
social recipes as fixed-fixture evaluations.

## Cluster B — H3 Character Sheet and identity references

**Decision: Experiment.** Add only an explicit H3 Orbit Sheet descriptor and
fixed-fixture reference-design comparisons inside the existing Character Sheet
capability.

### H3 Character Sheet Generator

The Hugging Face repository was observed at
[`ccc9d411b6b7056b43edf2690503e063560a5acd`](https://huggingface.co/PoopMan333/H3_Character_Sheet_Generator/tree/ccc9d411b6b7056b43edf2690503e063560a5acd).
It supplies Comfy
workflows rather than a model: a 124-frame slow orbit sampled into six panels
and a faster 73-frame path sampled into four panels, with up to nine references
and an optional Turbo path. The card admits that it is slow, that quality is
limited versus still-image sheets, that acceleration trades prompt adherence,
and that frame timing and source identity can drift.

The useful experiment is an explicit **H3 Orbit Sheet** profile comparing 73
versus 124 frames, deterministic frame-to-panel selection, explicit per-source
include/exclude roles, and optional retention of the spin/raw frames. It must
reuse Character Sheet authorization, anchor and seed commitments, panel order,
repair lineage, cancellation/recovery, and Reference Studio publication. The
Comfy graph is not imported.

The workflow card reports license metadata `other` and links the MiniMax H3
community license; an independent license for the workflow files was not
established. Managed availability therefore remains subject to the existing H3
legal-access and license-binding path; the intake does not infer worldwide
catalog eligibility or permission to redistribute the workflow.

### Face-mask identity-reference trick

@meAsifAi proposes masking the small full-body face with an opaque white circle,
then supplying a rear view and one macro face close-up so the video model does
not bind identity to a blurry tiny face. Replies ask whether the mask actually
beats an ordinary sheet, suggest a headless-front variant and a side view, and
include one anecdotal Wan 3 success.

Test ordinary, white-circle, headless-front, and added-side-view variants with
the same anchor, prompt, seed, frame plan and model. Measure identity and outfit
drift; do not treat the proposed mechanism as established or add content
inspection.

## Cluster C — style craft and style LoRAs

**Decision: Experiment.** Evaluate one optional craft recipe first, then keep
artifact and prompt-only arms separate within the existing style/LoRA path.

### STUDIO 1939 old-animation LoRA

The repository was observed at
[`19214d4c3989de6caca673d534d0d4b16b73b0f7`](https://huggingface.co/lovis93/studio-1939-old-animation-lora-minimax-h3/tree/19214d4c3989de6caca673d534d0d4b16b73b0f7),
about 329 MB total, with separate
rank-16 light (65,628,096 bytes) and rank-64 strong (262,433,016 bytes) LoRAs.
The card uses trigger `gulliv3r`, disables prompt expansion, suggests strength
1.0 for the full treatment and 0.4–0.8 for blending, and reports license
metadata `other` while referencing the MiniMax H3 community license. An
independent artifact-license grant was not established.

Keep light and strong as separate candidate identities. If later acquired,
they use the ordinary H3 LoRA path only after exact SHA/header/family checks and
license/access review. The artifact cannot silently become a style default.

The transferable recipe—frozen character design, explicit limited palette,
camera and art-direction fields, optical-track/post-treatment, and consistent
era-specific motion—is useful without the LoRA. Add it later as an optional
craft workflow, with negative style wording treated only as creative direction.

### Four-color H3 motion example

@Mayz1169 reports a 15.1-second 1080p H3 sequence constrained to four colors,
with smooth fast 2D motion and transitions. No canonical generation settings or
independent metrics were recoverable from the supplied post. Use it to nominate
a limited-palette rhythm fixture and identity/palette-lock measurements, not a
profile or default.

## Cluster D — acceleration candidates

**Decision: Benchmark lead.** Both candidates enter the existing one-variable
matrix as separately named cases; neither is adopted, stacked, or made a
profile/default by this intake.

### Alibaba PAI MiniMax-H3 Acc-LoRAs

The repository was observed at
[`335001fb9e5455d68a0caa18ec2e319072150328`](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs/tree/335001fb9e5455d68a0caa18ec2e319072150328)
and contains distinct FL2VA and Ref2VA
BF16 rank-64 PDD LoRAs, each about 1.37 GB. Upstream requires Diffusers
ModularPipeline 0.40 or newer, a special `apply_pdd_lora` path, and
configuration-derived step semantics. [Discussion
#1](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs/discussions/1)
reports that native Comfy support is absent and warns that pruning or conversion
would create a different, unverified artifact.

Any later experiment must prove exact family/base binding, tensor insertion,
timestep and scheduler semantics, audio preservation, cold/warm latency,
VRAM/RAM, cancellation and cleanup, and fixed-fixture quality versus native
23/28-step, managed Turbo, and LightX2V. PDD must not be stacked with Turbo,
SLA, cache, or another LoRA in its first matrix, and it must not force an
upgrade of Maestro's application environment.

### LightX2V FL2V Turbo 8-step v1.0 768p Comfy BF16

The exact requested artifact was observed at repository revision
[`05ef678438e84933c406131b59abbf86919b3aac`](https://huggingface.co/lightx2v/Minimax-h3-Turbo/blob/05ef678438e84933c406131b59abbf86919b3aac/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors),
filename
`minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors`, size
1,956,193,000 bytes, SHA-256
`08cfe946033af7d27719b964b6e0a0e50c32138daabbd6ce4137e23df6bf9980`.
The repository metadata reports Apache-2.0 for this artifact repository. That
metadata does not resolve the separate H3 base-model access and derivative-use
terms, which remain subject to the existing legal/access review.

[Discussion #48](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/48)
recommends eight steps, Euler/Simple, video shift 6, audio shift 3, and up to
768p. [Discussion #44](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/44)
reports Ref2V ghosting in some aspect ratios, and [discussion
#33](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/33) reports
blurry hands and quality concerns. Requests for Ref2VA remain unresolved. The
requested file is FL2V; no community claim authorizes silently treating it as
Ref2VA.

## Next-wave order / merge train

This is a delta to the 2026-08-25 CPU-now and RTX 5090 trains, not a parallel
train:

1. **Finish the active Prompt Rewriter checkpoint first.** Its current owner
   retains dependency metadata, wheel-byte/offline replay, isolated runtime,
   and later deterministic/base/adapted acceptance. No new runtime lane in this
   tranche preempts it.
2. **Fold montage/style ideas into existing rows 3 and 4.** The prior Composer
   craft-pack and field-flow owners receive storyboard-first, staged-build,
   limited-palette, logical-reference, clip/source, stale-analysis and authored-
   text fixtures. Implement only fields proven missing from the current sealed
   shot plan.
3. **Extend existing row 5 experiment descriptors.** After conflicting claims
   clear, add CPU-only descriptors for H3 Orbit Sheet, STUDIO 1939, Alibaba PDD,
   and LightX2V eight-step. This is descriptor work, not artifact acquisition or
   execution.
4. **Append later GPU rows without reordering accepted predecessors.** H3 Orbit
   compares 73 versus 124 frames after current Character Sheet controls;
   STUDIO 1939 follows the recipe-only arm; Alibaba PDD and then LightX2V
   eight-step follow current native/Turbo/LightX2V controls one variable at a
   time.
5. **Keep social A/Bs inside those fixtures.** White-circle/headless/side-view,
   palette, storyboard and staged-transform arms do not become independent
   products or queues.

Shared files and runtime/default changes require a later separately owned wave.
Nothing in this intake makes native Quality/High, managed Turbo, LightX2V
four-step, Director, Shot Deck, or existing Character Sheet profiles obsolete.

## Promotion gates

A later experiment may advance only with:

- exact source revision, artifact filename, size, SHA-256, architecture/family,
  license/access, and runtime identity;
- one-variable-at-a-time RTX 5090 evidence with no silent fallback;
- legal H3 frame grid, playable video, promised audio, and correct A/V duration;
- identity, wardrobe, text, style, motion, prompt-adherence, and A/V-sync review;
- cold/warm wall time, peak VRAM/RAM, load/unload and compile costs;
- cancellation before admission, during load/inference, and before publication,
  plus recovery/finality settlement;
- project/private-output parity and owner human quality acceptance;
- an obsolescence and rollback audit before any predecessor is retired.

## Watch, not adoption

- **OpenMontage:** recheck when issues
  [#542](https://github.com/calesthio/OpenMontage/issues/542),
  [#528](https://github.com/calesthio/OpenMontage/issues/528),
  [#526](https://github.com/calesthio/OpenMontage/issues/526), and
  [#493](https://github.com/calesthio/OpenMontage/issues/493) have a pinned fix
  or test fixture relevant to provider coverage, frame rate, temporal latents,
  or skill/schema drift.
- **Director Cut Studio:** recheck when a stable non-alpha project schema or a
  source-bound test demonstrates one timeline/reference invariant missing from
  Maestro.
- **H3 Character Sheet Generator:** recheck for runtime work only after the
  pinned workflow, Ref2VA stack, optional Turbo identity, 73/124-frame outputs,
  and existing H3 legal-access binding can be recorded together.
- **STUDIO 1939:** recheck artifact acquisition after both weight digests/header
  contracts, family compatibility, and existing H3 license/access binding are
  independently verified; recipe-only fixtures need no model acquisition.
- **Alibaba PDD:** recheck when a pinned isolated Diffusers ModularPipeline
  closure can load the exact FL2VA/Ref2VA insertion contract without changing
  the application environment, or native support is documented upstream.
- **LightX2V eight-step:** recheck after exact FL2V A/V and quality evidence on
  this host; wait for a separately identified Ref2VA artifact rather than
  inferring compatibility.
- **@koldo2k:** recheck when its canonical prompt, seed, checkpoint, manual edit
  chain, and no-keyframe claim can be reproduced.
- **@bond_ai1:** recheck when the staged transformation has canonical source
  settings, timing/cost accounting, and an H3-local comparison rather than a
  hosted-service anecdote.
- **@meAsifAi:** recheck when ordinary, white-circle, headless, and side-view
  identity fixtures have one-variable H3 evidence.
- **@Mayz1169:** recheck when the four-color prompt, seed, checkpoint, edit
  chain, and measurable palette/identity retention are available.
- **@HeyAbhishek:** recheck when storyboard panel order, generation settings,
  edit chain, and H3 audio-preserving adaptation can be reproduced.

## Public evidence index

1. [@koldo2k stickman H3 demo](https://x.com/koldo2k/status/2092621357896933487)
2. [@bond_ai1 staged room transformation](https://x.com/bond_ai1/status/2092455923877060644)
3. [OpenMontage](https://github.com/calesthio/OpenMontage)
4. [STUDIO 1939 H3 LoRA](https://huggingface.co/lovis93/studio-1939-old-animation-lora-minimax-h3)
5. [H3 Character Sheet Generator](https://huggingface.co/PoopMan333/H3_Character_Sheet_Generator)
6. [@meAsifAi face-mask character-sheet trick](https://x.com/meAsifAi/status/2092365498134696162)
7. [Alibaba PAI MiniMax-H3 Acc-LoRAs](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs)
8. [MiniMax H3 Director Cut Studio](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio)
9. [@Mayz1169 four-color H3 demo](https://x.com/Mayz1169/status/2092597548863402041)
10. [@HeyAbhishek storyboard-to-anime workflow](https://x.com/HeyAbhishek/status/2092622327389503903)
11. [LightX2V FL2V Turbo 8-step v1.0 BF16](https://huggingface.co/lightx2v/Minimax-h3-Turbo/blob/main/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors)

Additional canonical/comment evidence used during triage:

- [OpenMontage pinned source](https://github.com/calesthio/OpenMontage/tree/cd9f3c1f03368be87b140af494914b8ee4e3c7a4)
- [OpenMontage issue #542 — missing provider coverage](https://github.com/calesthio/OpenMontage/issues/542)
- [OpenMontage issue #528 — frame-rate assumptions](https://github.com/calesthio/OpenMontage/issues/528)
- [OpenMontage issue #526 — invalid temporal latents](https://github.com/calesthio/OpenMontage/issues/526)
- [OpenMontage issue #493 — skill/schema drift](https://github.com/calesthio/OpenMontage/issues/493)
- [Director Cut Studio current commit](https://github.com/karuvanan/MiniMax-H3-Director-Cut-Studio/commit/e3fa37b78548e918c676309d04c697e61226e98b)
- [STUDIO 1939 pinned tree](https://huggingface.co/lovis93/studio-1939-old-animation-lora-minimax-h3/tree/19214d4c3989de6caca673d534d0d4b16b73b0f7)
- [H3 Character Sheet Generator pinned tree](https://huggingface.co/PoopMan333/H3_Character_Sheet_Generator/tree/ccc9d411b6b7056b43edf2690503e063560a5acd)
- [Alibaba Acc-LoRAs pinned tree](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs/tree/335001fb9e5455d68a0caa18ec2e319072150328)
- [Alibaba Acc-LoRAs discussion #1 — Comfy compatibility](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs/discussions/1)
- [LightX2V eight-step pinned artifact](https://huggingface.co/lightx2v/Minimax-h3-Turbo/blob/05ef678438e84933c406131b59abbf86919b3aac/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors)
- [LightX2V Turbo discussion #48 — settings](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/48)
- [LightX2V Turbo discussion #44 — ghosting report](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/44)
- [LightX2V Turbo discussion #33 — quality report](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/33)
- [MiniMax H3 community license, moving official source](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)
- [Original room-transformation source](https://x.com/DmitryEdit/status/2089851392127049974/video/1)

Social post text and reply summaries were recovered through public mirrors when
direct X pages were unavailable. They are secondary, incomplete evidence and
are not used for artifact identity or default decisions.

## What this note did not do

This intake did not install or clone an upstream runtime, download model/LoRA
bytes, start a model or GPU job, inspect private projects or media, change an H3
profile/default, add a catalog row, modify the application environment, or
begin the merge train. Those require later separately owned implementation and
acceptance waves.
