# H3 workflow and template refresh — 2026-08-23

Status: public-source intake completed on 2026-08-23. This is a source and
product-placement decision, not a live-runtime, GPU, model-quality, catalog,
or release claim.

Source thread: Maestro Continuum. Owner emphasis: H3 workflows and templates
are the priority for productive no-GPU work; search beyond the supplied links,
but put each useful idea into the existing Maestro surface.

This note is a dated successor to, not a replacement of:

- `docs/research/H3-ecosystem-watch-2026-08-18.md`
- `docs/research/candidate-intake-2026-08-19.md`
- `docs/development/minimax-h3-fast-runtime-research.md`

Repeated candidates below are refreshes. Their old evidence and rejected
alternatives remain part of the record.

## Current Maestro checkpoint

Do not reimplement or silently overwrite these native surfaces:

- H3 FL2VA and Ref2VA already own local generation, joint 24 fps video plus
  32 kHz stereo audio, model-specific prompt compilation, exact reference
  limits, and explicit Quality/High/Turbo contracts.
- Reference Studio and Character Sheet already own durable identity, wardrobe,
  location, prop, and world artifacts. A sheet workflow is a template or
  layout probe inside that surface, not a new asset store.
- Director and Studio already own projects, shot plans, editable intermediate
  briefs, resumable execution, and final assembly. No second agent canvas or
  Comfy operation manager is needed.
- Existing H3 continuity modes and the decoded-media boundary adapter remain
  valid compatibility paths. A direct-latent continuation experiment must be
  separately named and must not rewrite their contract.
- Maestro does not inspect creative subject matter. Workflow templates must
  not introduce prompt scanning, output moderation, or third-party
  classification.

## Decision rules

- Adopt source-level craft, role, geometry, and verification invariants through
  Maestro's existing briefs, manifests, sheets, planners, and delivery checks.
- Keep runtime masks, latent-tail continuation, H3-generated character sheets,
  and AV inpaint opt-in experiments until they have local acceptance evidence.
- Treat community examples as design/benchmark leads, never defaults, and
  preserve exact revision/license provenance for extracted ideas.
- Do not import upstream graphs, node packs, skill runners, hosted canvases,
  or agent frameworks.
- Never represent hosted or unreleased modules as local open-source features.

## Primary-source refresh

| Source | Confirmed source contract | Decision |
| --- | --- | --- |
| MiniMax-AI/MiniMax-H3 README `6da473b48daf91e5aebfb56451f8a0b116348df5` and skills tree `597042140567efefd8c4adcfe8124c20f63a3399` | One prompt-writing skill plus eight style workflows: minimalist ad, 3D short, papercraft, brand promo, music-video typography, co-op intro, paper collage, and hand-drawn/live action | **Adopt/adapt** exact workflow identities and source craft as Maestro-authored optional briefs; never execute upstream skills or require Hub canvas tools |
| Comfy-Org/workflow_templates `0e0f4577453136eaa1c0e9d4b700e3e5ce5bb416` | Multiples-of-32 canvas, official `1344 x 768`, `17k+5` frames at 24 fps, separate FL2VA/Ref2VA families, ordered reference labels, packed AV decode | **Adopt** geometry and role facts through native controls; do not import graph JSON; `match` versus `max` remains a probe |
| Comfy-Org/ComfyUI `e01fb4c56b7a88149d469b99cbbfe3223d715054` | `MiniMaxH3AddGuide` anchors image, short clip, audio, or paired AV at a resolved frame, including negative indices and legal H3 clip/audio bounds | **Implemented/GO** as a sealed source-only planner; runtime/continuation composition remains unavailable |
| MiniMax-AI/awesome-minimax-h3-integration `f6ea3b6514ae6c8e4d280638cfa3884124292b56` | Official discovery index corroborates prompt/template, continuation, Director, and LanPaint clusters | **Watch** for revisions and newly official capability; never bulk-import its packages or weights |

Official prompt structure stays task-specific. Base modes use
`integrated_multimodal_description`, `overall_soundscape`, then
`non_diegetic_music`. Ref2VA uses `subject_definitions`, `summary`,
`retention_analysis`, `detailed_description`, `overall_soundscape`, then
`non_diegetic_music`. Dialogue, lyrics, visible text, labels, and timing retain
their authored values.

The strongest workflow ideas are one approved brief, one row per real shot,
one explicit job per reference, separate spatial/camera/action/audio fields,
one master audio timeline for music video, latest-approved-asset discipline,
and a final identity/continuity/dialogue/SFX/audio/artifact review.

## Cluster decisions

### A. Native creative workflow catalog

**Decision: Adopt now, source-only.**

Place official creative recipes in the existing H3 workflow catalog. Preserve
their exact IDs and upstream revision, and mark the prose as Maestro-adapted.
Useful shared fields are:

- intended result and aspect/duration assumptions;
- reference roles and which shot consumes each role;
- visual style and motion language;
- dialogue, SFX, soundscape, and music as separate authored fields;
- approval checkpoints and final delivery checks.

The 3D, music-video, and brand/product briefs should contain richer craft, but
must not mutate dialogue, music, or sound fields as a side effect of visual
guidance.

### B. Ref2VA role recipes

**Decision: Adopt after native integration review.**

The useful vocabulary is server-authored and small:

- identity/body/head/face;
- wardrobe;
- location/world;
- prop/vehicle/creature;
- performance/motion/timing;
- voice/music/audio texture;
- typography;
- storyboard/order;
- coverage reframe.

These are jobs attached to a reference, not replacements for the subject or
object identity already stored in the manifest. A template selection must
therefore remain separate from a user-authored subject binding. Existing
free-form records continue to load.

Coverage reframe stays a recipe: video owns performance/timing, sheets own
identity, and the prompt owns the new camera coverage. It is not a second
edit backend.

### C. H3 Guide Plan v2

**Source:** ethanfel/ComfyUI-MiniMax-H3-Guide at commit
`054ccb822864aac57f98c87e66fb13d2e5aa3b7a` (GPL-3.0-only).

Plan v2 separates project setup, typed reference roles, one Shot per real cut,
prompt merge/validation, and application to native H3. It distinguishes
identity, endpoint, motion, edit-source, continuation, voice, soundtrack,
and Foley intent.

**Decision: Adapt / extract.** Maestro already has the correct planner and
manifest surfaces. Adopt typed role intent, deterministic labels, one timing
source, and shot-local attachment semantics. Do not copy GPL code, install the
node pack, or add a second compiler. WIP example graphs are not product-ready
evidence.

The related andrewdidi/minimax-h3-serverless repository records a
`keyframe_motion` mode derived from Guide Plan v2. **Reject** its serverless
wrapper and request schema as a Maestro dependency; the keyframe + motion-role
idea is already covered by Maestro's native plan.

### D. H3 Character Sheet

**Source:** ethanfel/ComfyUI-MiniMax-H3-Edit at commit
`98f9467625bc34829da735e3ac1391b9b06bbcc6` (MIT).

The repository runs one continuous 73- or 124-frame orbit, extracts calibrated
views, and composes a 4- or 6-panel sheet. It separates semantic Qwen guides
from native VAE references and labels mixed transport experimental.

**Decision: Experiment.** Keep FLUX Quad as Maestro's conservative Character
Sheet default. Add H3-generated short-video sheets only as an explicit local
probe after runtime acceptance. Reuse the existing Reference Studio artifact
path and allow manual frame selection; do not create another sheet registry.

**Adapt now:** a competing `identity focus` layout—one clean frontal face plus
front/back body views, with neutral light and separate identity-detail or
expression pages when requested. It stays opt-in until compared against the
existing multi-angle pack on the same character.

### E. Direct-latent continuation candidates

Treat all four sources as one evaluation cluster, not four products.

| Source | Revision / license | Transferable idea | Decision |
| --- | --- | --- | --- |
| ttulttul/ComfyUI-Minimax-H3-Continuation | `e1768d5fdfc6f9519d2090dcf78458c2d9625f80`, MIT | hidden overlap; guide synchronized native AV tail; discard sampled overlap; append only new suffix | primary source for a tensor-free native continuation plan |
| seitanism/ComfyUI-H3-Motion-Context-MultiRef | `87de57ba619297503fa49c9594c0c021d5b0c261`, GPL-3.0 | MultiRef roles, master-song timeline, AV extension, latent masks, lower cache pressure | extract contracts only; no fork import |
| HerrgottMargott/Herrgotts-H3-Infinite-Continuation-Suite | `4b1edd678de7356beebf3761b2532a35e07d0389`, GPL-3.0 | freeze-safe visual handover, independent protected speech tail, exact AV boundaries, saved-chain assembly | benchmark/experiment source; avoid heuristic defaults |
| NikoDemon80/ComfyUI-H3-Motion-Context | `f80e36bc1d7887a143b12e6645313fd6b9cd2aee`, GPL-3.0 | prior latent tail without decode/re-encode; backward-owned audio context; trim picture and sound together | provenance and comparison baseline |

**Decision: Experiment one Maestro-native direct-tail family.** Plan exact
frame/audio geometry and replay data first. Preserve the existing decoded
18/17 boundary adapter. Runtime work later must prove:

- legal context and extension lengths;
- absolute 24 fps video / 40 Hz audio boundary math without cumulative drift;
- exact hidden-overlap removal and suffix append;
- no contradictory opening guide;
- bounded cancellation/recovery and truthful final frame/audio counts;
- one selected continuation method, with the others retained as provenance.

Masked continuation and direct guide-tail continuation are separate
mechanisms until evidence shows they compose cleanly.

### F. Director timeline and storyboard templates

**Source:** AIMixer/ComfyUI_MiniMaxH3_Director at commit
`a267324a9f88141ff4e4b0e8c1a6ed90b4e45db7` (Apache-2.0).

The useful product ideas are a visual multi-segment timeline, per-group task
roles, selective retakes, shared versus shot-local references, source-audio
choice, scene detection, continuity context, and a human-readable run report.

**Decision: Adapt / extract.** Put these capabilities into Maestro's existing
Director timeline and persisted shot plan. Do not import the all-in-one node,
custom frontend, model downloader, or refinement graph. Director must retain
one operation owner and one canonical project/output path.

The official skill shot-table/QC references add useful lightweight templates:
one row per shot, explicit handoff, landmarks/positions/light, per-second
action/camera/audio, and a final artifact review. Use these as optional craft
guides; avoid turning every small project into a mandatory studio bureaucracy.

### G. OpenH3-IR

**Source:** ruashots/ComfyUI-OpenH3-IR at commit
`8660988b033d427f346e72fdbcf2d45ede48edbe` (Apache-2.0).

Useful ideas include named `@reference` bindings, explicit dialogue locking,
role-aware media, one duration/frame decision, reusable director guidance,
and a structured local brief before H3 conditioning.

**Decision: Adapt / extract.** Maestro already has manifests, typed plans,
dialogue fields, and local/remote LLM selection. Add human-readable named
reference aliases and a typed intermediate H3 brief to those native records.
Do not import the Comfy nodes, start a second HTTP service, or silently send
media to a hosted vision model.

This is not the official hosted H3-Context-IR and must never be labeled as
bit-identical reproduction of it.

### H. LanPaint H3 AV inpaint

**Source:** scraed/LanPaint at commit
`32cf848e93971da380d868936e007f5611218bee` (GPL-3.0).

LanPaint v2.1 documents per-frame video masks plus audio intervals in one H3
packed latent pass, with original media preserved and a mask-blended result.
The source also documents boundary-quality issues and recommends bounded video
lengths for stability.

**Decision: Experiment.** Adopt the user job—local AV repair with explicit
video and audio regions—and the separation between keep/regenerate masks.
Do not copy GPL code or import its editor/graph. A native Maestro experiment
must first prove per-sample mask geometry, audio interval alignment, untouched
regions, cancellation, and original-audio/fps preservation.

### I. Output verification

**Source:** tonyd2wild/minimax-h3-local
`scripts/verify_output.sh` at commit
`76abed188f3e7ef210a223ee23a2ce1b005d5c9a`.

The script checks that an output is more than a present MP4: stream/container
facts, approximate duration, frame count/dimensions/fps, an audio stream,
sampled luma, distinct sampled frames, and non-silent audio.

**Decision: Adopt the verification contract, not the shell script.** Extend
Maestro's delivery verification with structured CPU checks for:

- expected frame-grid length and duration tolerance;
- expected dimensions and frame rate;
- stereo 32 kHz audio presence when the job promises native H3 audio;
- non-empty, non-all-black, non-frozen sampled video;
- audible signal when sound was requested;
- stable artifact hash plus mux/probe results in benchmark evidence.

The implemented checks currently cover the benchmark runner, not ordinary
delivery. They inspect media integrity only, never subject matter or prompts.

## Hosted or unavailable official modules

### H3-Context-IR

MiniMax's official README describes a hosted multi-stage preprocessing and
orchestration system. It is not included in the open-source release.

**Decision: Watch / optional disclosed adapter only.** Maestro may implement
its own local typed brief and may later offer an explicitly selected hosted
adapter under the existing provider/privacy gate. Do not claim local official
Context-IR, and do not make the hosted call a prerequisite for local H3.

### H3-Regenerate-2K

MiniMax describes a second H3 pass that uses the 768p output together with the
original context. The module is not open-sourced.

**Decision: Watch; descriptor implemented/GO.** The sealed CPU-only plan is
source-only, hosted-only, and unavailable: no transport, profile, fallback,
submission, or download. Verified source geometry is sealed; 11/11 CPU tests
passed. Target dimensions are intentionally unspecified. Execution needs review.

## Implementation status at this checkpoint

### Implemented in the current working tree

| Slice | Zero-GPU evidence and boundary |
| --- | --- |
| Creative briefs | 3D, music-video, and brand/product craft guidance passed focused CPU review |
| `identity focus` | Opt-in frontal-face/body-front/body-back probe passed; default identity pack unchanged |
| `cumulative_append` | **GO** source-only AV lattice/clock/overlap/window/final-trim geometry; no sampler/runtime wiring |
| H3 AddGuide | **GO**, 14/14 CPU: negative indices, legal `17n+5` crop, source-floor audio capacity, ordered overlaps; runtime composition unavailable |
| Benchmark integrity | 36/36 CPU plus real synthetic fixtures for AV duration, stereo coverage, DC-removed audibility, tool preflight; benchmark-only, not delivery/live evidence |
| Director shot table/QC | **GO** opt-in API/persisted advisory, official skills revision `597042140567efefd8c4adcfe8124c20f63a3399`; no UI/runtime authority |
| Creative Guide Jukebox | 17/17 Node plus ESLint/diff passed; disposable native-component browser QA at 1280×900 and 390×844 showed eight cards, deterministic Surprise selection change, Clear removal, 44px mobile buttons, and no horizontal overflow; desktop/mobile screenshots and image-first evidence validation passed; not runtime/GPU acceptance |
| H3 Prompt Coach | **GO** pure-browser advisory structural review—duration/timed-cue coverage, reference ordinals, sections, dialogue/text preservation, keyframe links, and camera/action/audio presence—without prompt rewrite, transmission, classification, or blocking; official `d21241f0a4b3acbb34c97dae47fa417b7065e438` facts plus Niutonian `e49bcbf161f760f2d11ccac4c295c6d64de83fd3` MIT provenance; 29/29, lint, bundle, and review passed; 1280×900/390×844 browser QA passed 44px, no-overflow, blank-hide, screenshot, and image-first checks; not full Context-IR/GPU/runtime |
| Director Shot Deck | **GO** read-only Director review surface—strict owned v1 parser, workspace-scoped preview/recovery/pipeline adoption, stale-plan clearing, compact beat ribbon, expandable shot craft, and static pending QC; 6/6 focused tests, lint/bundle, review, and 1280×900/390×844 no-overflow browser checks; advisory only, never submitted or execution-authoritative |
| Regenerate-2K | **GO** sealed source/hosted-only unavailable plan, 11/11 CPU, no transport, target dimensions intentionally unspecified |
| Sol launcher path | Normal Install/Update/Start is profile-aware, preserves legacy aliases, and passed 28/28 CPU tests; no launcher was run |
| H3 classic model selection | Dropdown and Models Manager reuse exact aliases; linked assets are read-only to deletion; 20/20 CPU tests passed |
| Required-runtime readiness | Manifest/readiness parity covers ten auxiliary assets, linked-complete and split-root layouts; 24/24 CPU tests passed |
| CivitAI quarantine | Linked-root first-match quarantine behavior passed 53/53 CPU tests; no live download or catalog acceptance |

### Reviewed but not retained this turn

- The Ref2VA role-template draft was removed: it overwrote subject binding and had no API/UI/compiler path. Future work separates subject from job; no helper remains.

### Planned after this note

- Wire corrected reference jobs through catalog, manifest, compiler, and save/reload tests.
- Add named references and exact dialogue retention to existing native records.
- Define source-only records for masked AV repair and H3 character-sheet video.
- Model acceptance waits for the owner to lift the zero-GPU boundary.

## Merge train

1. Preserve reviewed craft, identity, continuation/AddGuide geometry, launcher profiles, alias/readiness parity, quarantine, and advisory data through closure.
2. Design Ref2VA role-template storage so subject identity and reference job survive independently; wire one end-to-end save/reload/compiler path.
3. Add named-reference and exact-dialogue semantics to the existing typed H3
   brief.
4. Later decide whether benchmark-integrity results should feed structured
   delivery records; do not overstate current benchmark evidence.
5. Add source-only capability descriptors for arbitrary-frame guides, masked
   AV repair, and H3-generated character sheets.
6. When GPU work is authorized, evaluate one direct-tail continuation family,
   then H3 character sheets and LanPaint-style AV inpaint as separate probes.
One writer remains required per shared source/test cluster; no step authorizes
bulk upstream imports or a default/profile change.

## Owner inputs and current gates

- H3: `JP` remains recorded, current, and available; this wave did not rewrite the declaration.
- Krea: owner → `noncommercial`, user → `commercial_under_1m` is decided and
  v2 readiness is CPU-verified; live attestation is unrecorded and profile runtime stays gated.

## Watch, not adoption

| Item | Recheck trigger |
| --- | --- |
| Official H3-Context-IR | local source release, or separately approved hosted adapter |
| Official H3-Regenerate-2K | local artifacts/runtime, or separately approved hosted adapter |
| `ref_image_size` match versus max | same-reference identity/cost A/B on native Maestro path |
| H3 AddGuide runtime composition | explicit native runtime binding and exact AV execution tests |
| direct-tail continuation | one chosen design passes seam, audio, recovery, and finality checks |
| native masked continuation | per-sample video/audio mask execution without path duplication |
| H3 orbit character sheets | controlled comparison with FLUX Quad and manual selected-frame review |
| LanPaint-style H3 AV repair | native masked keep/regenerate proof and bounded local acceptance |
| official integration index | official capability/revision change, not popularity alone |

## Rejected as product dependencies

- Upstream Comfy graphs/nodes, MiniMax Hub execution, or skill runners.
- A second Director, project/timeline store, catalog, queue, or output path.
- External agent chains and undisclosed hosted Context-IR/2K fallback.
- Creative-content scanners or aesthetic judges.
- Mixing every continuation implementation into one stack.

## Public evidence index

Official:

- MiniMax H3 repository and README:
  https://github.com/MiniMax-AI/MiniMax-H3/commit/6da473b48daf91e5aebfb56451f8a0b116348df5
- MiniMax H3 skills tree:
  https://github.com/MiniMax-AI/MiniMax-H3/tree/597042140567efefd8c4adcfe8124c20f63a3399/skills
- MiniMax H3 official integration index:
  https://github.com/MiniMax-AI/awesome-minimax-h3-integration/tree/f6ea3b6514ae6c8e4d280638cfa3884124292b56
- Comfy official H3 workflow templates:
  https://github.com/Comfy-Org/workflow_templates/tree/0e0f4577453136eaa1c0e9d4b700e3e5ce5bb416
- Comfy arbitrary-frame H3 guide commit:
  https://github.com/Comfy-Org/ComfyUI/commit/e01fb4c56b7a88149d469b99cbbfe3223d715054

Community sources used for extraction/experiments:

- H3 Guide Plan v2:
  https://github.com/ethanfel/ComfyUI-MiniMax-H3-Guide/tree/054ccb822864aac57f98c87e66fb13d2e5aa3b7a
- H3 edit and character sheets:
  https://github.com/ethanfel/ComfyUI-MiniMax-H3-Edit/tree/98f9467625bc34829da735e3ac1391b9b06bbcc6
- Direct-tail continuation:
  https://github.com/ttulttul/ComfyUI-Minimax-H3-Continuation/tree/e1768d5fdfc6f9519d2090dcf78458c2d9625f80
- MultiRef motion context:
  https://github.com/seitanism/ComfyUI-H3-Motion-Context-MultiRef/tree/87de57ba619297503fa49c9594c0c021d5b0c261
- Herrgott continuation suite:
  https://github.com/HerrgottMargott/Herrgotts-H3-Infinite-Continuation-Suite/tree/4b1edd678de7356beebf3761b2532a35e07d0389
- Niko motion context:
  https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context/tree/f80e36bc1d7887a143b12e6645313fd6b9cd2aee
- H3 Director timeline:
  https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/tree/a267324a9f88141ff4e4b0e8c1a6ed90b4e45db7
- OpenH3-IR Comfy integration:
  https://github.com/ruashots/ComfyUI-OpenH3-IR/tree/8660988b033d427f346e72fdbcf2d45ede48edbe
- LanPaint H3 AV inpaint:
  https://github.com/scraed/LanPaint/tree/32cf848e93971da380d868936e007f5611218bee
- CPU-friendly output verification source:
  https://github.com/tonyd2wild/minimax-h3-local/blob/76abed188f3e7ef210a223ee23a2ce1b005d5c9a/scripts/verify_output.sh
- Guide Plan v2 serverless wrapper (rejected dependency):
  https://github.com/andrewdidi/minimax-h3-serverless/tree/5d8567df26148c8d6f7477a761875efc4fba4c38

## What this note did not do

No install, download, model load, inference, benchmark, sample, graph import,
private-data inspection, default change, Beads/Git mutation, restart, or
publication occurred.
