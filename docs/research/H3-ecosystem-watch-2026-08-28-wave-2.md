# H3 ecosystem intake — 2026-08-28, wave 2

Status: public evidence captured and decisions recorded on 2026-08-28. This is
an intake note, not an implementation or acceptance claim. No candidate was
installed, downloaded, loaded, exposed in the catalog, or made a default. No
GPU work, private prompt/media inspection, or application-environment change
was performed for this intake.

Source thread: Maestro Continuum. The owner marked this tranche as intended for
Maestro, emphasized FastH3 and programmatic video composition, and asked for
broad consideration under `docs/operations/INTAKE.md`, including comments,
issues, and linked sources rather than surface-level summaries.

This is a dated successor to, not a replacement for:

- `docs/research/H3-ecosystem-watch-2026-08-28.md`
- `docs/research/H3-ecosystem-watch-2026-08-25.md`
- `docs/research/H3-workflow-refresh-2026-08-23.md`
- `docs/development/minimax-h3-fast-runtime-research.md`
- `docs/development/feature-wave-2026-08-media-models.md`

The raw tranche contained **11 unique URLs**, including the two links embedded
inside the owner's parenthetical notes. All 11 are accounted for below.

## Current Maestro checkpoint

The intake started from repository checkpoint
`5cca641f62318fde7acbc52eb6098ac90acbb647`.

The supplied candidates must extend rather than duplicate these existing
authorities:

- H3 native Quality/High, managed Turbo, LightX2V, Spectrum, Sage and step-cache
  modes already have distinct profiles and compatibility gates. Ordinary H3
  LoRAs already have a strength/loading path. A new distilled LoRA is a named,
  isolated candidate, not a rewrite of Draft/Fast or managed Turbo.
- Director, Shot Deck, the persisted shot plan, project queue, cancellation,
  recovery and normal output transaction already own planning, editing and
  publication. A code-driven compositor can be a worker or preview surface; it
  cannot become a second project store, queue, output root or operation manager.
- Prompt Coach, Director craft workflows and the deterministic H3 prompt
  document already own native authoring. Social prompts nominate fixtures and
  recipes, not new products.
- H3 inpainting already has an inert, source-bound control-plan descriptor.
  The current generic inpaint route is a separate SAM/LTX path and must not be
  silently reused as an H3 executor.
- Local creative work remains content-neutral. Upstream moderation or
  subject-matter classifiers are excluded even when the surrounding technique
  is useful.

## Decision rules

1. Keep checkpoint family, LoRA, strength, step schedule, sampler, attention
   engine, cache, quantization, pass count and refiner as separate variables.
   First acceptance varies one item at a time and has no silent fallback or
   stacking.
2. Exact source revision, artifact filename, byte size, digest, base family,
   insertion semantics, schedule, license and runtime identity precede any
   model acquisition or execution.
3. Adopt Maestro capabilities and contracts, not upstream UIs, stores, queues,
   provider routers, hosted Spaces, Comfy graphs or runtime environments.
4. Social posts and creator demos nominate fixed fixtures. They do not establish
   quality, speed, compatibility or causality without reproducible settings and
   local evidence.
5. Source/static, CPU scaffold, live local runtime, GPU output review and owner
   human acceptance remain separate evidence classes.
6. Linux and Windows remain independent targets. B200, hosted-service or browser
   demonstrations are not RTX 5090 acceptance.

## Cluster A — FastH3 distilled acceleration

**Decision: Benchmark lead.** FastH3 is the highest-priority runtime candidate
in this wave, but Preview v1 is not a drop-in replacement for any shipped
profile. A later wave may promote it to an isolated Experiment only after the
source-only descriptor and runtime-admission gates pass.

Preview v1 supersedes the 2026-08-25 note's FastH3 Preview v0.2 row as the one
current FastH3 candidate for future Maestro descriptors. Both represent the
same four-forward `[999, 749, 500, 250]` FastH3 lineage, while v1 provides a
newer, smaller adapter bundle and exact manifest. Do not create a parallel v0.2
profile or benchmark case. Retain the older self-contained v0.2 identity and
its training-preview limitations in the 2026-08-25 note as provenance.

### Exact public source contract

The supplied Hugging Face repository was observed at revision
[`bcf40ca6f457ed66f8badf13514943e390205fca`](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA/tree/bcf40ca6f457ed66f8badf13514943e390205fca).
Its repository totals 17,503,017,152 bytes including metadata; the four adapter
files total 17,502,990,144 bytes:

- dense data-free: 1,485,626,152 bytes, SHA-256
  `4ce198c83132251b7fd0de2503823aa49c53983f068318f66cb19eaefb7fcc12`;
- VSA data-free: 5,339,117,712 bytes, SHA-256
  `42dc502a2078f166c396a1fa75f29728d1844363652d345d5ef3e2b444ed6470`;
- VSA synthetic step 1300: 5,339,128,568 bytes, SHA-256
  `de6af1ea8b2f4b31a7ac3752b4836f88671199fdf277a5dd922f9cca3bea7b65`;
- VSA synthetic step 1900: 5,339,117,712 bytes, SHA-256
  `bbac632dffb828d99123ab06c7ff3efc246351bed63d1860b4252e61dafeaed8`.

The pinned
[`adapter_manifest.json`](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA/blob/bcf40ca6f457ed66f8badf13514943e390205fca/adapter_manifest.json)
binds MiniMaxAI/MiniMax-H3, rank 64, four transformer forwards at base
timesteps `[999, 749, 500, 250]`, five scheduler points and guidance 1.0.
Default LoRA strength is 1.0. The VSA variants declare 90% sparsity with tile
size 64; the dense adapter does not require the VSA backend.

The upstream [FastH3 preview report](https://haoailab.com/blogs/fasth3-preview/)
is explicit that Preview v1 is **T2VA only**. It reuses H3's text encoder,
video/audio VAEs, tokenizers and schedulers, and its validation outputs include
stereo audio, but FL2VA students were not trained and Ref2VA is a different
transformer. The reported speed measurements and defaults use B200 systems;
they are not evidence for this RTX 5090 host. The bundle's
[pinned license](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA/blob/bcf40ca6f457ed66f8badf13514943e390205fca/LICENSE)
is the MiniMax H3 Community License. It governs the adapters and also points
back to the restricted H3 base; an adapter repository tag does not provide a
separate redistribution or host-access exception. Existing H3 legal-access
review remains a hard gate.

An unresolved [FastVideo issue #1606](https://github.com/hao-ai-lab/FastVideo/issues/1606)
questions a VSA training-versus-kernel tile mismatch. That makes exact adapter,
kernel, tile and backend binding an acceptance requirement rather than an
implementation detail. The open [ComfyUI discussion](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA/discussions/1)
has no maintainer answer and does not supply a native Maestro route.

### Experiment matrix

The first CPU slice should add source-only, disabled descriptors and benchmark
cases under the existing H3 profile/evaluation authorities. It must not expose
a catalog row or live runner until runtime admission exists.

The first live matrix, after a separate GPU grant and legal/runtime gates, is:

1. native T2VA control with the same prompt, seed, geometry and duration;
2. dense data-free at the exact documented four-forward schedule and strength
   1.0;
3. one VSA candidate at the same schedule and strength, only after exact
   RTX 5090 kernel compatibility is proven;
4. lower strengths as separate arms, with generator reload between settings as
   upstream requires;
5. more-step or multi-pass arms only after the four-forward baseline settles.

The owner specifically asked about more steps, lower strength and multi-pass.
Lower strength is a supported runtime control, but its quality tradeoff is not
documented. Upstream lists eight-step work, stronger final-step supervision,
learned timestep placement and related quality work as **future experiments**;
there is no released eight-step Preview v1 contract. Arbitrarily adding denoise
steps to a four-forward distilled schedule therefore is not presumed valid.

@Machinedelusion's supplied reply says that two inference passes are key and
that multiple samplers may help when one-shot output is weak. It is a useful
hypothesis, not a schedule or correctness proof. A later multi-pass case must
have distinct pass IDs, exact per-pass model/LoRA/strength/timesteps, immutable
intermediate lineage, cancellation between passes, and an explicit comparison
against both the one-pass FastH3 result and the native baseline. It must never
be silently folded into the ordinary Turbo or delivery-upscale path.

## Cluster B — programmatic composition and motion boards

**Decision: Adopt a Maestro-owned programmatic composition capability.** The
first implementation plan should define a neutral composition document and a
sandboxed render/player adapter under Director. This decision adopts the native
capability, not any external editor or engine. Remotion is the preferred first
backend benchmark candidate, not yet an adopted dependency.

The useful capability extends far beyond the supplied parkour example:
motion-reference boards, animated storyboards, title and lower-third systems,
diagram and UI animation, captions, branded layouts, before/after explainers,
kinetic typography, guide overlays, H3 reference clips and deterministic final
assembly. Director remains the source of shot, asset, timing, dialogue/audio
and output commitments.

### Remotion

Remotion was observed at release
[`v4.0.518`](https://github.com/remotion-dev/remotion/releases/tag/v4.0.518).
Its React compositions, embeddable
[`@remotion/player`](https://www.remotion.dev/docs/player), server-side render
APIs and newer WebCodecs client renderer make it the strongest first adapter
candidate for Maestro's React UI and deterministic exports.

Its [current license](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md)
is not a normal permissive open-source license: larger for-profit organizations
require a company license, and a proposed v5 license-key change remains under
discussion in [issue #9539](https://github.com/remotion-dev/remotion/issues/9539).
No dependency or distribution decision is made until the owner/legal gate
accepts the applicable terms and the exact package versions.

### Diffusion Studio

[`diffusionstudio/editor`](https://github.com/diffusionstudio/editor) was
observed at release
[`v0.200.0`](https://github.com/diffusionstudio/editor/releases/tag/v0.200.0),
under MPL-2.0. Its code-and-canvas SolidJS document, editable entities, headless
ECS runtime, explicit clip timing, transitions, captions, audio synchronization
and render CLI are valuable design references.

It also owns its own document/editor runtime. Its
[`dapi report` contract](https://github.com/diffusionstudio/editor/blob/main/reference/report.md)
can publish logs containing project names, paths and prompt text directly to a
public issue unless logging is disabled. Its current
[issue tracker](https://github.com/diffusionstudio/editor/issues) reports
Windows spawn failures, export freezes and rendering defects. Maestro should
extract document/patch/render invariants, not import the editor or its
reporting/provider behavior.

### Revideo, Motion Canvas, Twick and Friction

- [`midrender/revideo`](https://github.com/midrender/revideo) is an MIT
  TypeScript scene/player and headless renderer with WebCodecs plus FFmpeg audio.
  Its [README](https://github.com/midrender/revideo/blob/main/README.md)
  documents anonymous PostHog render-count telemetry and the opt-out. It is a
  credible fallback benchmark, but telemetry must be disabled and current
  [Windows/audio/quality issues](https://github.com/midrender/revideo/issues)
  resolved for a managed worker.
- [`motion-canvas/motion-canvas`](https://github.com/motion-canvas/motion-canvas)
  is an MIT generator-based animation system with a browser player and strong
  vector/explainer primitives. It is a design reference and possible specialized
  backend, not the first general editor integration.
- [`ncounterspecialist/twick`](https://github.com/ncounterspecialist/twick)
  supplies useful timeline/canvas/player ideas, but its current Sustainable Use
  License supersedes older MIT metadata, and its IndexedDB/provider/cloud paths
  overlap Maestro's authorities. Treat it as reference only.
- The exact owner-named **Rendave** identity could not be resolved: the apparent
  GitHub repository is absent. Do not silently substitute Rendiv or Rendervid.
- @ihteshamali's post points to [`friction2d/friction`](https://github.com/friction2d/friction),
  a GPL-3.0 Skia/Qt vector motion-graphics editor with multiple scenes/timelines,
  SVG animation and expression tooling. Its reusable value is the motion-graphics
  interaction model; its code and editor are not imported.

Every backend executes project-authored JavaScript/TypeScript, browser graphics
or FFmpeg work. Treat it as an untrusted, resource-bounded worker. Input paths,
network, environment, modules, browser origin, output path, cancellation and
process-group cleanup must be explicit. The backend receives a sealed
composition package and returns an owner-private candidate output plus evidence;
only Maestro's existing output transaction can publish it.

## Cluster C — H3 audio/video inpainting

**Decision: Experiment.** Extend the existing inert H3 control-plan descriptor;
do not adopt the hosted Space, remote planner or its runtime.

The supplied Space was observed at commit
[`1c0ab6b0b8a2e1bed7c20205050e07d1ba868837`](https://huggingface.co/spaces/linoyts/minimax-h3-inpainting/tree/1c0ab6b0b8a2e1bed7c20205050e07d1ba868837).
The underlying adapter repository is
[`diffusers-modular/minimax-h3-inpainting`](https://huggingface.co/diffusers-modular/minimax-h3-inpainting/tree/dca1cd614f8c6bc5496594d82602a82a8ad4edba),
whose Apache-2.0 metadata does not remove the H3 base-model license.

The transferable algorithm packs source video/audio and masks into the Ref2VA
sequence, re-imposes preserved rows during denoising, reduces pixel masks over
H3's 16x spatial VAE compression and 2x2 transformer patching, uses 17-frame
temporal chunks, and treats audio on a separate 40-latent-rows-per-second clock.
Generic image-mask resizing or video-FPS audio indexing is therefore incorrect.
The source card recommends hard masks and reports non-local decoder halo near
mask boundaries; these are source claims requiring local reproduction.

The hosted implementation is not locally self-contained: it calls a public
Qwen conditioner, its planner is currently gated/private, SAM3 is separately
gated, and discussion reports local cloning/access failures. ZeroGPU AoTI and
constant-binding patches are hosting workarounds, not Maestro runtime contracts.
Its `ncii_guard.py` subject-matter classifier conflicts with Maestro Local
Content Neutrality and must not be ported.

The smallest future slice extends `h3_control_plan.py` with typed per-sample
video masks, audio intervals, keep-versus-regenerate semantics and exact mask
geometry while keeping execution and fallback false. A later executor must
prove untouched-region fidelity, boundary halo, original frame rate, preserved
or intentionally regenerated audio, A/V alignment, cancellation, recovery and
normal output finality. The merged
[`ComfyUI PR #15375`](https://github.com/Comfy-Org/ComfyUI/pull/15375) is a
parity reference, not a graph to import.

## Cluster D — social craft, motion and process fixtures

**Decision: Adapt / extract.** These sources become optional craft recipes or
fixed benchmark fixtures under existing Director and H3 craft ownership. None
becomes a model, provider, queue or default.

- **@PhotogenicWeekE:** two 15-second H3 demonstrations include an explicit
  drawing-timelapse recipe and a blueprint-to-built-house causal sequence with
  time-coded construction, alarm and dialogue/audio. Preserve the causal-stage
  and exact-audio structure as fixtures; the posts do not provide complete
  checkpoint/seed/edit evidence.
- **@ailker:** the post announces an H3 Max motion-prompt guide. The linked X
  article body was unavailable through public sources; comments request failure
  cases where camera motion overrides subject motion. Keep only a Watch entry
  until the guide has a canonical accessible copy and reproducible examples.
- **@airina_xyz:** a 15-second storybook loop specifies an exact causal chain,
  readable text, foley-only audio, parallax camera and anti-morphing constraints.
  Use it as a causality/text/loop/audio fixture, not as PixVerse acceptance.
- **@Kashiko_AIart:** a Remotion motion board presents 30 named parkour actions
  as bilingual animated cards, then selects actions as conditioning references
  for H3 or Seedance. Generalize it into typed motion-reference boards rather
  than a parkour-only feature; one reply reports unexpected game-like audio,
  which becomes an audio-artifact check.
- **Reddit speedpaint:** the author reports four 12-second standard Ref2VA clips
  stitched with manual first/last-frame editing. Comments identify lines that
  appear before the hand reaches them, hand morphing, static overlays and
  implausible drawing order. The useful fixture scores causal hand/tool/stroke
  ordering and disclosure. Label outputs as synthetic process visualizations;
  do not present them as evidence of how an artwork was actually created.

Negative prompt wording in these recipes remains creative direction, not
moderation. Exact dialogue, lyrics, visible text, timing, reference roles and
audio ownership remain protected by the existing H3 compiler contracts.

## Next-wave order / merge train

1. **Finish the active Prompt Rewriter checkpoint first.** Preserve the prior
   note's predecessor gate: complete its fresh metadata canary, reviewed
   wheel-byte/offline replay boundary and current isolated-runtime checkpoint
   before starting another model-runtime lane. Read-only intake and planning may
   continue, but FastH3 acquisition/execution and compositor runtime work do not
   preempt it.
2. **FastH3 source-only descriptors.** One owner for `h3_profiles.py`,
   `h3_evaluation.py`, `benchmark_h3_profiles.py` and their exact tests adds
   distinct dense/VSA candidates and
   disabled benchmark cases with exact manifest, T2VA-only and no-stacking
   contracts. Do not download or expose them in the catalog in that wave.
3. **FastH3 runtime plan and four-forward acceptance.** Isolate the FastVideo
   dependency/kernel path, then request a bounded RTX 5090 grant for native,
   dense and one proven-compatible VSA arm. Lower strength follows; more-step
   and multi-pass cases remain separately named hypotheses.
4. **Programmatic Composition contract.** One Director schema/orchestrator/
   pipeline owner defines a neutral, versioned composition package from shot
   plans and a sandboxed worker result; UI and renderer adapters remain later
   disjoint owners. Evaluate Remotion first, Revideo as the permissive fallback,
   and Motion Canvas for specialized vector work. Do not start until license,
   telemetry, sandbox, path and finality gates are explicit.
5. **H3 AV inpaint descriptor.** One `h3_control_plan.py` plus focused-tests
   owner extends the inert control plan with source-only mask/audio geometry and
   parity fixtures; keep execution false.
6. **Craft fixtures.** One `h3_upstream_skills.py` plus focused-tests owner adds
   motion-board, causal-storybook, construction/drawing process and synthetic-
   speedpaint recipes only where existing craft IDs do not already express the
   invariant.
7. **Live work remains separately coordinated.** GPU/runtime rows require fresh
   grants after CPU descriptors, exact artifacts, dependency closure and static
   review settle.

Nothing in this train makes native Quality/High, managed Turbo, LightX2V,
Director, Shot Deck, Character Sheet, existing craft workflows or the current
inpaint route obsolete. No compatibility shim for an external editor or hosted
Space should be created.

## Promotion gates

A later candidate may advance only with:

- exact source, artifact, dependency and runtime revisions plus license/access;
- isolated install and rollback without application-environment mutation;
- one-variable RTX 5090 evidence with no fallback or hidden stacking;
- legal H3 frame geometry, playable output, promised stereo audio, exact A/V
  duration and synchronization;
- identity, text, causality, style, motion, boundary and prompt-adherence review;
- cold/warm wall time, peak VRAM/RAM, compile and load/unload costs;
- cancellation before admission, during load/inference/render and before
  publication, plus recovery/finality settlement;
- owner-private project/output parity, output hashes and owner human acceptance;
- separate Windows acceptance and an obsolescence/rollback audit.

## Watch, not adoption

- **FastH3:** recheck on a pinned eight-step or quality-improved release, a
  resolved VSA tile/kernel contract, exact RTX 5090 support and legal access.
- **Machinedelusion multi-pass:** recheck when exact per-pass settings,
  intermediate handoff, seeds, model/LoRA revisions and comparative outputs are
  public.
- **Remotion:** recheck licensing and the proposed v5 key requirement before a
  dependency decision.
- **Diffusion Studio:** recheck Windows/export/shader issues and its document
  API stability after the v0.200.0 breaking change.
- **Revideo:** recheck exact package/repository provenance, telemetry-off mode,
  Windows paths, audio and quality controls.
- **Motion Canvas:** recheck headless rendering, dependency support and audio/
  FFmpeg consistency.
- **Twick:** recheck only if its Sustainable Use terms and overlapping stores/
  providers become compatible with a narrow adapter.
- **Rendave:** recheck only after the owner supplies the intended canonical
  identity; do not substitute another project.
- **Friction:** retain as GPL design reference only.
- **H3 inpainting:** recheck after a fully local conditioner/planner-free path,
  exact base/legal access and native AV-mask parity fixtures exist.
- **Social prompts:** recheck when canonical prompt, seed, checkpoint, LoRAs,
  strength, steps/scheduler, attention/cache, edit chain and failure evidence
  are recoverable.

## Public evidence index

Owner-supplied tranche, preserved exactly:

1. [@PhotogenicWeekE H3 2D/timelapse post](https://x.com/PhotogenicWeekE/status/2093198730958897335)
2. [@ihteshamali Friction motion-graphics post](https://x.com/ihteshamali/status/2092998752332341410)
3. [@ailker H3 Max motion-prompt guide post](https://x.com/ailker/status/2093165511920046289)
4. [FastVideo FastH3 four-step Preview v1 LoRA](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA)
5. [@Machinedelusion multi-pass reply](https://x.com/Machinedelusion/status/2093429094168002757)
6. [@airina_xyz H3 storybook loop](https://x.com/airina_xyz/status/2093288708359282982)
7. [Diffusion Studio editor](https://github.com/diffusionstudio/editor)
8. [@Kashiko_AIart Remotion motion board](https://x.com/Kashiko_AIart/status/2093304933160526010)
9. [Remotion](https://www.remotion.dev)
10. [Reddit MiniMax fake speedpaint thread](https://www.reddit.com/r/StableDiffusion/comments/1vzykd7/generating_fake_speedpaint_timelapse_with_minimax/?share_id=Sek-eLtl3VGmrwFwIsaAJ&utm_content=2&utm_medium=android_app&utm_name=androidcss&utm_source=share&utm_term=2)
11. [MiniMax H3 inpainting Space](https://huggingface.co/spaces/linoyts/minimax-h3-inpainting)

Additional canonical/comment evidence used during triage:

- [FastH3 pinned source](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA/tree/bcf40ca6f457ed66f8badf13514943e390205fca)
- [FastH3 pinned adapter license](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA/blob/bcf40ca6f457ed66f8badf13514943e390205fca/LICENSE)
- [FastH3 preview report](https://haoailab.com/blogs/fasth3-preview/)
- [FastVideo issue #1606 — VSA tile question](https://github.com/hao-ai-lab/FastVideo/issues/1606)
- [FastH3 discussion #1 — ComfyUI request](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA/discussions/1)
- [Diffusion Studio reporting/privacy contract](https://github.com/diffusionstudio/editor/blob/main/reference/report.md)
- [Diffusion Studio issues](https://github.com/diffusionstudio/editor/issues)
- [Remotion source](https://github.com/remotion-dev/remotion)
- [Revideo source](https://github.com/midrender/revideo)
- [Revideo issues](https://github.com/midrender/revideo/issues)
- [Motion Canvas source](https://github.com/motion-canvas/motion-canvas)
- [Twick source](https://github.com/ncounterspecialist/twick)
- [Friction source](https://github.com/friction2d/friction)
- [H3 inpainting pinned Space source](https://huggingface.co/spaces/linoyts/minimax-h3-inpainting/tree/1c0ab6b0b8a2e1bed7c20205050e07d1ba868837)
- [H3 inpainting adapter source](https://huggingface.co/diffusers-modular/minimax-h3-inpainting/tree/dca1cd614f8c6bc5496594d82602a82a8ad4edba)
- [ComfyUI H3 inpainting PR #15375](https://github.com/Comfy-Org/ComfyUI/pull/15375)
- [H3 inpainting Space discussion #1](https://huggingface.co/spaces/linoyts/minimax-h3-inpainting/discussions/1)
- [Official MiniMax H3 license](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)
- [Public mirror — @PhotogenicWeekE thread](https://api.fxtwitter.com/2/conversation/2093198730958897335)
- [Public mirror — @ihteshamali thread](https://api.fxtwitter.com/2/conversation/2092998752332341410)
- [Public mirror — @ailker thread](https://api.fxtwitter.com/2/conversation/2093165511920046289)
- [Public mirror — @Machinedelusion thread](https://api.fxtwitter.com/2/conversation/2093429094168002757)
- [Public mirror — @airina_xyz thread](https://api.fxtwitter.com/2/conversation/2093288708359282982)
- [Public mirror — @Kashiko_AIart thread](https://api.fxtwitter.com/2/conversation/2093304933160526010)

Direct X pages were unavailable to the research environment. Social post text,
thread replies and media metadata were recovered through public mirrors; those
records are incomplete secondary evidence and do not control artifact identity
or defaults.

## What this note did not do

This intake did not install or clone an upstream runtime, download model or
LoRA bytes, start a model or GPU job, inspect private projects or media, change
an H3 profile/default, expose a catalog row, add a compositor dependency,
import a Comfy graph, modify the application environment, or start any item in
the merge train. Those are later, separately owned implementation and
acceptance waves.
