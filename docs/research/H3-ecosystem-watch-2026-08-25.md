# H3 ecosystem intake — 2026-08-25

Status: intake, CPU-only scaffolding, and initial artifact acquisition completed
on 2026-08-25. No Maestro model was loaded and no GPU inference or quality
acceptance was performed. Experimental profiles remain explicit and non-default.

Source thread: Maestro Continuum. Owner emphasis: H3 remains the primary focus;
move quickly, adapt ComfyUI ideas into native Maestro rather than importing
graphs, treat mature/NSFW models content-neutrally, and do not let fine-grained
packaging or provenance work block a promising experiment. The owner explicitly
prioritized enabling the Dasiwa Ref2VA hybrid despite its sparse card and wants
the supplied H3 Ref2VA NSFW-motion LoRA tried and included as an opt-in path.

This note is a dated successor to, not a replacement for:

- `docs/research/H3-ecosystem-watch-2026-08-18.md`
- `docs/research/candidate-intake-2026-08-19.md`
- `docs/research/H3-workflow-refresh-2026-08-23.md`
- `docs/development/minimax-h3-fast-runtime-research.md`
- `docs/development/feature-wave-2026-08-media-models.md`

Repeated ideas below are refreshes. The raw paste contained **36 URLs**; all 36
are accounted for in the ledger.

## Current Maestro checkpoint

Intake started at repository checkpoint
`3bcb4f5b860754a489cd038893e1481ce277df2f`.

Do not reimplement or silently displace these native surfaces:

- H3 FL2VA and Ref2VA already own local joint video/audio generation, legal
  frame geometry, references, prompt compilation, cancellation, recovery, and
  final publication.
- The server-authored catalog has Turbo-backed Draft/Fast, native
  Quality/High, Spectrum and LightX2V experiments, and explicit delivery
  profiles. A new four-step tune, sparse attention path, quantized checkpoint,
  or hybrid refiner gets a distinct experiment/profile identity; it does not
  mutate global steps or create a sibling catalog.
- Prompt Coach, Director, Shot Deck, Reference Studio, Character Sheet, Scene
  Kit, Cast Board, Blender bridge, projects, queues, and outputs are the product
  surfaces to extend. There will be no second composer service, scene store,
  operation manager, model catalog, or output path.
- Ordinary H3 user LoRAs already have FL2VA/Ref2VA loading and strengths.
  Motion/style candidates should reuse that path unless their architecture
  actually requires a special sampler or insertion point.
- Music3 remains its own Music lane. LTX refiners remain visibly LTX, even when
  an H3 draft feeds them.
- Local creative content remains content-neutral. NSFW candidates use the same
  compatibility, quality, and runtime evaluation as any other tune; no prompt
  scanning or creative-content gate is added.

No SSD is present yet. Owner-authorized artifacts were downloaded to the
current ignored TVBox-backed Maestro model/LoRA roots and may move to the future
Crucial M500 warm tier later. The storage-tier plan remains dormant: it did not
guess a mount or migrate existing files.

## Initial implementation checkpoint

The owner explicitly extended the intake into a CPU-only implementation and
download wave. The current checkout now contains:

- exact H3 marker-token registration and fail-closed ID/round-trip checks;
- a sealed native Bridge plan with legal frame/audio geometry, seam ownership,
  trim, reroll, and recovery identities;
- a sealed inert H3 ControlNet Union plan for Canny, depth, HED, MLSD, pose,
  and inpaint inputs;
- explicit, no-fallback Dasiwa exact-base, Dasiwa suspected-base, and Better
  Motion Ref2VA profiles using the existing ordinary-LoRA request shape;
- disabled benchmark descriptors for exact and suspected Dasiwa plus Better
  Motion strengths 0.5, 0.7, 0.9, and 1.0;
- an owner-review prompt/rubric pack for motion-LoRA coherence, created with a
  sanitized public Grok consultation and kept separate from Maestro moderation.

The exact Dasiwa LoRA (794,888,664 bytes, SHA-256
`d2a9a723d97520232f17b6fec33335f9e94b03b2c67b56f91f16780355479274`)
and Better Motion V1 (298,261,888 bytes, SHA-256
`15615bf5aef77b974dba6cd109c547fb8a9a5d36a68fd38b3bd3578e59d3545a`)
pass bounded CPU header/hash validation. Dasiwa's required base SHA-256
`71c61492faf65b410d0726840ac3b27b017fcfeb76b16ae11589223d81b7121c`
was not found publicly. The installed Ref2VA checkpoint instead matches the
explicit suspected-compatible SHA-256
`f86f2f79ebd2d76eb8eeb46091e83982e6ff51d255747e7b16e92834b392b8e9`.
Accordingly, the exact profile stays unavailable while the suspected profile is
visibly unverified and reserved for a later coherent-output probe.

Additional owner-authorized, ignored artifacts were acquired and header-checked
without loading them: Turbo-SLA, Prompt Rewriter, ControlNet Union,
Single-Frame VAE, Music3 FP8/Turbo, and the earlier 10Eros Ref2VA Beta2 INT8
ConvRot skip-edge checkpoint. These are inventory for later
one-variable-at-a-time acceptance, not evidence of runtime compatibility or
quality. Beta3 acquisition was a separate owner-authorized transfer. Both the
skip-edge and fully quantized artifacts now have exact final sizes, SHA-256
identities, bounded header contracts, and reusable owner-private final-path
receipts. This is storage/integrity evidence, not runtime or quality
acceptance.

## Decision style for this wave

Capability and forward momentum come first. We record enough identity to avoid
mixing unrelated artifacts, but defer exhaustive packaging, redistribution,
and release hardening until a candidate survives its first useful probe.

The practical rules are:

1. Extract the user job and useful mechanism, not the upstream UI or graph.
2. Give every materially different runtime a visible experiment/profile name.
3. Preserve authored dialogue, lyrics, visible text, timing, reference roles,
   and original prompt when a composer or learned rewriter is used.
4. Compare candidates separately before stacking them. Distillation, sparse
   attention, quantization, style/motion LoRAs, and LTX refinement are different
   variables.
5. Social examples are craft and benchmark leads. A strong example can become
   a useful recipe immediately without becoming a global default.
6. Exact hashes, tensor checks, notices, and platform polish are promotion work,
   not reasons to avoid defining a promising experiment.

## Executive decisions

### 1. Native H3 Bridge

**Decision: Adopt as a native planned capability.**

The Javawock Bridge graph expresses a real Director job: take the tail of clip
A and head of clip B, generate only the connecting interval, then assemble
`A -> generated bridge -> B`. Maestro should implement this through Director,
Shot Deck, AddGuide/continuation geometry, and the existing assembly path—not by
running the Comfy graph.

The native plan should define:

- exact A-tail and B-head guide ranges;
- bridge duration on H3's legal frame/audio grid;
- whether source audio, generated bridge audio, a drive track, or crossfade owns
  each boundary;
- hidden overlap and trim rules;
- optional continuity prompts for subject, camera, light, motion, and sound;
- a review point where only the bridge is rerolled;
- final mux and seam evidence in the normal project/output record.

The workflow repository moved during intake (`28fd8b0...`) and its discussions
report Switchboard and latent-upscale rough edges. That reinforces extracting
the Bridge job rather than depending on the graph.

### 2. H3 Composer and craft recipes

**Decision: Adopt/adapt into Prompt Coach, Director briefs, Shot Deck, and the
existing workflow catalog.**

The standalone H3 Prompt Composer, Reddit role/dialogue experiment, official
prompt guides, and social examples converge on a useful native authoring layer:

- one physical-input map separate from reusable logical asset identities;
- explicit source-subject, motion/performance, wardrobe, camera, timing, and
  audio roles;
- one authoritative camera instruction, whether authored manually or by a
  visual path planner;
- storyboard panels treated as sequential shot guidance, not one static image;
- local project import/export and field-to-compiled-prompt round trips;
- original and composed prompts shown side by side before use;
- Prompt Check as structural/advisory feedback, never a readiness certificate
  or creative-content judge.

Add these optional recipe/probe families to the existing native catalog:

1. **Bridge / transition** — connect two clips or key states.
2. **Character reveal** — absolute identity reference plus timed cuts.
3. **Storyboard performance** — panels own beats; a separate character sheet
   owns identity.
4. **Brand / logo reel** — exact logo geometry, causal construction, premium
   motion, final mark lock.
5. **Tapestry / causal world build** — every final element visibly grows from
   one material lineage.
6. **Kinetic typography** — text as smoke, dust, thread, ink, paper, or graphic
   material with explicit timing and legibility checks.
7. **Limited-palette rhythm** — noir/neon palette locks, cuts, freezes, reverse
   time, and audiovisual transient mapping.
8. **Origami / page-turn worlds** — hard material transitions instead of soft
   morphs.
9. **Sprite animation** — flat matte, one move per short clip, pose-extreme
   extraction, background removal, spritesheet packing.
10. **Action/beat graphics** — parkour or similar continuous action whose
    contacts, graphic changes, and sound transients land together.
11. **High-resolution regeneration** — use a low-resolution motion prototype as
    conditioning for a new H3 render; label it regeneration, not faithful
    pixel-preserving upscale, and review drift.

The Reddit `<S#>/<T#>` definitions syntax becomes an ablation fixture, not a
new truth. Compare official schema, natural language, and shorthand with
declarations/usages deliberately reordered so positional reading is not
mistaken for variable binding.

### 3. Optional local learned prompt composition

**Decision: Experiment, explicit and editable.**

The LightX2V Qwen3-VL-8B Prompt-Rewriter adapter (`a795219...`, about 2.79 GB
plus its base model) is a prompt-engine candidate, not an H3 transformer LoRA.
It supports T2VA/I2VA/L2VA/FL2VA and currently excludes Ref2VA. Slot it into
Maestro's existing local prompt-enhancer/model override path behind an explicit
**Compose for H3** action.

The first version must preserve the original, show the result before use, keep
dialogue/lyrics/text/timing/reference roles literal, and never run implicitly at
submission. A deterministic Maestro composer remains the no-extra-model path.

### 4. H3 marker-token parity

**Decision: Adopt the contract; audit/test before touching runtime code.**

Merged ComfyUI PR #15808 adds seven H3 markers at IDs 151669–151675:
`<d>`, `</d>`, cutoff, lyrics start/end, and caption start/end. Maestro already
emits literal dialogue markers, so the immediate CPU wave is a tokenizer parity
test: verify fixed IDs, preserve prior Qwen special tokens, round-trip markers,
and keep the raw/non-chat H3 presentation. Do not copy Comfy internals or add a
chat-template layer.

### 5. Sol-Super hybrid profile family

**Decision: Adopt the architecture as an experimental family; do not copy the
GB200 result into a 5090 default.**

MiniMax's official post points to NVIDIA SANA's Sol-Engine H3 Super
Acceleration: a four-step H3 draft at 896x512, latent upsample, then three LTX
refinement steps at target resolution with Sol-Attn and tiny H3/video
autoencoders. NVIDIA reports 6.85 s for 5 s and 14.93 s for 10 s 768p on one
GB200, versus its published SGLang baseline.

Define three separable profiles so we can scale the idea down intelligently:

- **Hybrid Draft** — four-step low-resolution H3 draft for interactive review;
  approximate and never silently published as delivery.
- **Hybrid Refine** — H3 draft plus three-step LTX refinement at target
  resolution; begin dense on RTX 5090 to isolate topology from sparse kernels.
- **Hybrid Sol** — the full tiny-autoencoder + Sol-Attn path on compatible
  hardware, after dense hybrid parity.

If Hybrid Refine passes identity, text/logo, motion, and audio tests, it can
eventually become the default for a specifically named interactive/delivery
profile—not for Quality/High or every H3 job.

### 6. Low-step and acceleration candidates

Keep these as distinct cases in one comparison matrix:

| Candidate | Decision | First question |
| --- | --- | --- |
| FastVideo FastH3 Preview v0.2 (`11dd7d6...`) | **Experiment / benchmark lead** | Does its explicit `[999,749,500,250]` DMD2 ladder preserve usable AV quality on 5090? |
| LightX2V Turbo-SLA (`10ade67...`) | **Experiment** | Does four-step FL2V + 85% SLA outperform current Fast/Turbo on this host after compile cost? |
| Dasiwa Ref2VA Hybrid V1 4-step (`da516a7...`, ~0.795 GB) | **Adopt as owner-prioritized experiment** | What family/keys/strength/schedule actually work, and how does it change explicit motion/identity? |
| 10Eros-Max Beta3 TURBO Hybrid INT8 ConvRot (`dbdd879...`) | **Experiment** | Does the six-step skip-edge checkpoint preserve useful AV quality before testing the fully quantized checkpoint? |
| Existing managed Turbo / LightX2V / Sage | **Baseline duties** | Keep as controls; do not remove while evaluating newer paths. |

FastH3 v0.2 is still a training preview (step 2900/4000), text-to-AV only,
about 148 GB self-contained, and below base quality on high-motion/audio detail.
It is not a drop-in replacement for Ref2VA or managed Turbo. Turbo-SLA is a
LoRA/runtime pair; sparse attention and four-step distillation must be measured
separately where possible.

10Eros Beta3 supersedes the earlier FL2VA/Ref2VA assumption for this intake.
Both new artifacts are complete **TURBO Hybrid** transformers and must not be
registered or described as FL2VA or Ref2VA. The conservative first candidate is
`10Eros_Max_h3_TURBO-hybrid_beta3_int8_convrot_skip_edges.safetensors`
(revision `09beb98782a6feb2f44c39c46179743ca8607c6c`, 22,513,576,472 bytes,
SHA-256 `a5ae4559cf19b0830adc1de6e8355d10eaf10524f78e9851a189a80990e6963a`):
184 ConvRot layers cover blocks 2–47 while blocks 0, 1, 48, and 49 remain
BF16. The second candidate is
`10Eros_Max_h3_TURBO-hybrid_beta3_int8_convrot.safetensors` (revision
`84ea7a6ec06e0cb5f2f35615e25e3529c5ec6c02`, 20,973,147,816 bytes, SHA-256
`ebd0cb25273253213028bea0289da4c5c94929027ed9191fbb24fc924d4a8f0d`):
200 ConvRot layers cover blocks 0–49. Both use native `int8_tensorwise`,
per-channel absmax scales, ConvRot group size 256, and a BF16 source.

The **provisional Maestro experiment policy** starts at six steps with
`er_sde/simple` and `multires/simple` as separate candidates and does not stack
Maestro's built-in Turbo, Spectrum, LightX2V, SageAttention, or step cache.
Those choices are not represented as immutable Hugging Face artifact facts.
CPU descriptors and disabled benchmark/evaluation scaffolds are useful now;
runtime registration, GPU use, quality acceptance, defaults, and automatic
fallback remain absent.
A Grok review contributed provisional public breadth only. The pinned
Hugging Face repository revisions and artifact identities above remain the
primary technical authority.

### 7. Owner-prioritized mature-motion paths

**Decision: Adopt as opt-in experiments; content is not a gating dimension.**

- **Dasiwa Ref2VA Hybrid V1** is enabled in the plan despite its minimal card.
  The first CPU/source implementation should add a visible experimental profile
  and bounded benchmark case; initial tensor/header/runtime details can be
  learned during that work.
- **H3 Better NSFW Motion, Ref2VA V1** (Civitai version `3257589`) gets a
  separate explicit motion profile. The supplied artifact is about 284.4 MiB,
  SHA-256 `15615BF5AEF77B974DBA6CD109C547FB8A9A5D36A68FD38B3BD3578E59D3545A`.
  Probe strengths 0.5/0.7/0.9/1.0 across realistic, stylized 3D, and 2D cases;
  its author notes a possible 2D-to-3D push.
- The linked MATLOWAI motion adapter is a different mechanism: it belongs only
  in its de-rope/windowed pass, with separate lower-invention and full-motion
  strengths. Do not stack it blindly into the first sampling pass.

All reuse Maestro's current LoRA/profile UI and project execution. No mature-
content classifier, warning engine, or parallel model browser is added.

### 8. H3 ControlNet Union

**Decision: Experiment as a high-value native H3 control profile.**

Alibaba PAI's 6.8 GB control branch (`6419c27...`) provides Canny, Depth, HED,
MLSD, Pose, and video inpaint through five control blocks. This is a genuinely
new H3 conditioning capability, not a Z-Image ControlNet and not automatically
compatible with Maestro's generic video-guide path.

CPU-now work should define the request/plan shape: control kind, source video,
strength, aspect/frame snapping, mask/inpaint mode, base H3 family, and
`control_apply_audio=false`. Later runtime work can map that shape into H3 only
after a source/model admission check. Its documented full model + text encoder
footprint is too large for a straightforward single-5090 load, so offload and
residency behavior are part of the experiment—not a reason to discard it.

### 9. Single-frame H3 VAE

**Decision: Experiment, lower priority, still-image only.**

The 500K decoder (`eada4e7...`, ~9.69 GB) is not a full video VAE. It is
interesting for diagrams, product contours, line art, documents, UI-like
layouts, independent frame extraction, and static H3 edit experiments. The
official decoder still wins the broad perceptual metrics and natural texture;
full-video temporal decode is explicitly unsupported.

Keep this separate from canonical video decode and from the lightweight preview
decoder. A useful Maestro outcome would be an opt-in structured frame-decoder
experiment inside Reference Studio and the existing Outputs flow, not a global
VAE switch or a new top-level product surface.

### 10. Reference, character, 3D, and Blender ideas

**Decision: Adapt the data/authoring ideas; do not add another scene editor.**

- The known-character index suggests a compact local evaluation schema:
  character, source asset, clip IDs, date, good/bad/on-the-fence status, and
  notes. Use owner-selected local assets in Reference Studio/Cast Board; do not
  import the public clip corpus.
- `kimodo.cpp` is a promising CPU/Vulkan text-to-SMPL-X motion source. Watch it
  as an optional motion-to-Blender handoff; it does not become an H3 engine.
- NKD's face-rig, timeline, masks, camera/depth, and coverage ideas become
  Director/Blender/Reference Studio authoring prompts, not node-pack imports.
- CozyClay is rejected as a dependency, but its single authoring core,
  transaction/undo semantics, and proposed RGB+depth+normal+prompt+metadata shot
  package are useful for Maestro's existing Blender/Scene Kit bridge.
- SenseNova-U1.5 is a separate 35–50 GB any-to-any image/edit family. Watch it
  for a future still/reference job; do not force it into H3.

### 11. Music and post-production

**Decision: Experiment in Music; watch Audio-to-MIDI as post-production.**

MiniMax Music 3 Turbo FP8 (`da6efd4...`) packages an FP8 text encoder and DiT
plus an eight-step LoRA (about 11.8 GB total). Its author reports a 190-second
song in about 245 s on RTX 4090 with FP8 + LoRA + Sage, versus about 895 s for
their INT8 baseline. Add a separate Music3 FP8/Turbo case later; preserve
lyrics, vocals, duration, and transient quality as first-class comparisons.

Mirelo's Audio-to-MIDI tempo maps, meter/downbeat detection, score view, and
per-instrument MIDI are a useful future Music3/Director export idea, but not an
H3 or Music3 runtime dependency in this wave.

## CPU-now merge train

These are the highest-value implementation slices that require no model load or
GPU. Rows 1, 2, 5 (initial Dasiwa/Better Motion descriptors), and 6 are now
implemented as CPU-only foundations; the remaining rows retain their owners
below.

Use one lightweight **experiment descriptor** across the runtime candidates so
future work adds cases, not bespoke integration machinery. It extends the
existing authorities—`app/services/h3_profiles.py::_PROFILES` for user-visible
profiles, `app/scripts/benchmark_h3_profiles.py::BenchmarkCase` for benchmark-
only cases, and `app/services/h3_evaluation.py` for manifests/reports. It is not
a new catalog. The descriptor should state:

- capability/profile ID and artifact class (checkpoint, video LoRA, prompt
  LoRA, decoder, control branch, attention backend, or refiner);
- H3/Music/LTX family and FL2VA/Ref2VA/task modes;
- steps, timestep ladder/scheduler, precision, strengths/insertion phase, and
  attention mode that define the experiment;
- reference, control, video, and audio semantics;
- anticipated storage, VRAM/RAM/offload posture and incompatible stacks;
- visible fallback policy (normally none for a benchmark) and result metadata.

This is capability negotiation and experiment bookkeeping, not exhaustive
release certification. It lets Dasiwa, motion LoRAs, SLA, FastH3, Sol hybrid,
ControlNet, VAE, and Music3 cases enter the same fixed-fixture matrix without
being flattened into one ambiguous `fast` toggle.

1. **Tokenizer marker parity** — fixed marker IDs, preservation of existing
   special tokens, literal round trips, no chat-template insertion.
2. **Native Bridge plan v1** — sealed A-tail/B-head/bridge geometry, audio owner,
   trim/assembly, reroll boundary, recovery identity.
3. **Composer craft pack** — Bridge, storyboard, character reveal, brand/logo,
   tapestry, kinetic material typography, limited palette/foley, sprite, action
   beat, and high-res regeneration recipes inside the existing catalog.
4. **Prompt Composer gaps** — field-to-compiled-prompt tests for source subject,
   performance transfer, wardrobe, camera ownership, and physical/logical
   reference mapping.
5. **Experiment descriptors/cases** — Dasiwa first, H3 Better NSFW Motion,
   MATLOW motion adapter, SLA, FastH3, 10Eros, Sol hybrid levels, ControlNet
   Union, Single-Frame VAE, Prompt Rewriter, Music3 FP8.
6. **Control plan v1** — typed Canny/depth/HED/MLSD/pose/inpaint inputs without
   runtime binding.
7. **Shot package v2** — optional RGB plate, depth, normals, blocking frame,
   camera facts, prompt, and metadata from the existing Blender/Director path.
8. **Known-character local fixture schema** — deterministic local index and
   review statuses, with no bundled third-party corpus.

### Writer and acceptance ownership

Keep one writer per row and sequence the shared-file rows:

| Slice | Source owner | Focused regression / acceptance owner |
| --- | --- | --- |
| Marker parity | `app/models/minimax_h3/conditioner.py` | `tests/test_minimax_h3.py`: fixed IDs, prior-special preservation, literal marker round trip |
| Bridge plan | new `app/services/h3_bridge_plan.py`, then the existing Director/shot binding in `app/launch.py` | new `tests/test_h3_bridge_plan.py`; runtime seam/audio/recovery case recorded by `benchmark_h3_profiles.py` and `h3_evaluation.py` |
| Craft recipes | `app/services/h3_upstream_skills.py` and `app/services/director/workflow_templates.py` | `tests/test_h3_upstream_skills.py`, `tests/test_director_workflow_templates.py`, and the existing H3 style UI test |
| Composer field flow | `app/services/director/h3_dialogue.py` and existing Prompt Coach/compiler surfaces | `tests/test_h3_director_dialogue.py` plus field-to-compiled-prompt UI/source tests |
| Runtime experiments | extend `_PROFILES`, `BenchmarkCase`, and H3 evaluation manifests; candidate execution stays in current H3/Music modules | `tests/test_h3_profiles.py`, `tests/test_h3_benchmark_runner.py`, `tests/test_h3_evaluation.py`, then fixed-fixture GPU rows |
| Control plan | new `app/services/h3_control_plan.py`; runtime binding later in the current H3 handler | new `tests/test_h3_control_plan.py`, then one GPU row per control kind |
| Shot package | current `app/services/blender_mcp_service.py` and Director output path | existing Blender service tests plus one package round-trip test |
| Character fixture | current `app/services/reference_sheets.py` and Cast Board path | existing reference/character-sheet suites plus deterministic index cases |

Bridge runtime acceptance owns seam quality, audio boundaries, bridge-only
reroll, cancellation, recovery, and final assembly evidence. Prompt Rewriter
runtime acceptance owns a separate deterministic-composer versus base-Qwen
versus adapted-Qwen comparison; it never piggybacks on video-LoRA acceptance.

## First RTX 5090 wave

Run one candidate at a time; do not create a giant stack whose improvement
cannot be attributed.

1. Re-establish current native Ref2VA and FL2VA controls on four fixed fixtures:
   simple T2VA; one-image FL2VA; three-reference Ref2VA with audio; two-shot
   continuation with dialogue/music.
2. **Native Bridge** — two short controlled source clips; verify visual seam,
   audio ownership/crossfade, bridge-only reroll, cancellation, recovery, and
   exact assembled duration before using external adapters.
3. **Dasiwa Ref2VA Hybrid V1** — owner-prioritized first external adapter.
4. **H3 Better NSFW Motion Ref2VA** — strengths and 2D/3D/realistic matrix.
5. **MATLOW motion adapter** — de-rope pass only, separate from the motion LoRA.
6. **Turbo-SLA** — first measure four-step tune dense, then SLA if separable.
7. **FastH3 v0.2** — only after storage/admission; exact trained ladder, dense
   first, then its 64-token/90%-sparse VSA contract.
8. **Sol Hybrid Draft/Refine/Sol** — dense hybrid first; tiny autoencoders and
   Sol-Attn after topology parity.
9. **10Eros Beta3 TURBO Hybrid ConvRot** — six-step skip-edge first, then full;
   do not claim FL2VA/Ref2VA compatibility or stack built-in accelerators.
10. **ControlNet Union** — one control type at a time, then inpaint; verify audio
   remains truthful when control does not apply to audio.
11. **Prompt Rewriter** — compare deterministic composer, base Qwen, and the
    adapter on the same authoring fixtures before any video-quality conclusion.
12. **Single-Frame VAE**, then **Music3 FP8/Turbo**, as separate lanes.

For every H3 case record wall time, cold/warm behavior, peak VRAM/RAM, actual
steps/scheduler/precision, playable 24 fps video and promised audio, identity,
markings/text/logo, motion, reference-role adherence, AV sync, cancellation,
resume/finality, and an owner visual/audio decision. This is a compact
comparison matrix, not a release bureaucracy.

## Raw candidate ledger — all 36 supplied URLs

| # | Source | Decision and Maestro outcome |
| ---: | --- | --- |
| 1 | [Kashiko: H3 2K regeneration](https://x.com/Kashiko_AIart/status/2090450507269914742) | **Benchmark lead** for a high-resolution regeneration profile; adapt only after drift review. |
| 2 | [Javawock H3 workflows](https://huggingface.co/javawock7618/comfy-MiniMax-H3-workflows) | **Adopt** the native Bridge job; extract workflow ideas without a graph dependency. |
| 3 | [FastH3 Preview v0.2](https://huggingface.co/FastVideo/FastVideo-Minimax-FastH3-Preview-v0.2) | **Experiment** with the distinct four-step DMD2/VSA preview. |
| 4 | [Reddit Prompt Intelligence](https://www.reddit.com/r/StableDiffusion/comments/1vv03ly/look_what_i_discovered_prompt_intelligence/?share_id=p-BSX0wPkIvWcZcEC85tu&utm_content=2&utm_medium=android_app&utm_name=androidcss&utm_source=share&utm_term=1) | **Adapt / extract** as a role/dialogue ablation, not proof of variables or a new compiler. |
| 5 | [Andrew Carr sprite workflow](https://x.com/andrew_n_carr/status/2091560238558429560) | **Adopt** a native sprite recipe/export plan. |
| 6 | [TechHalla Midjourney + H3](https://x.com/techhalla/status/2091457020213838151) | **Adapt / extract** strict timing, identity, camera, and material craft into recipes. |
| 7 | [Stefan: kimodo.cpp](https://x.com/Stefan_3D_AI/status/2091531702183350276) | **Watch** for a useful CPU/Vulkan SMPL-X-to-Blender motion handoff. |
| 8 | [lepadphone logo reel](https://x.com/lepadphone/status/2091441060153278565) | **Adopt** an optional brand/logo reel recipe. |
| 9 | [Known-character index](https://huggingface.co/datasets/malcolmrey/various/blob/main/h3-center/known-characters/INDEX.md) | **Adapt / extract** a local evaluation schema, not the public corpus. |
| 10 | [SenseNova-U1.5 collection](https://huggingface.co/collections/sensenova/sensenova-u15) | **Defer** as a separate still/reference family. |
| 11 | [Nekodificador face control](https://x.com/Nekodificador/status/2090511775783391266) | **Adapt / extract** rig, timeline, mask, and camera ideas into existing authoring paths. |
| 12 | [MiniMax official Sol-Super post](https://x.com/MiniMax_AI/status/2092008802984001919) | **Adopt** the architecture as Hybrid Draft/Refine/Sol experiments. |
| 13 | [Mayz neon/typography](https://x.com/Mayz1169/status/2090071170943185174) | **Benchmark lead** for saturated palette and timed graphic transitions. |
| 14 | [Mirelo Audio-to-MIDI](https://x.com/MireloAI/status/2090135900990701611) | **Watch** for a later Music3/Director post-production wave. |
| 15 | [Music3 Turbo FP8](https://huggingface.co/guillaume127/MiniMax-Music-3-Turbo-FP8) | **Experiment** in separate Music3 lane. |
| 16 | [Dasiwa Ref2VA Hybrid 4-step](https://huggingface.co/t8star/Minimax-H3-Dasiwa-V1-Hybird-4steps) | **Experiment** as the owner-prioritized external profile despite the sparse card. |
| 17 | [Kōda Tapestry](https://x.com/aimikoda/status/2091893350748025010) | **Adopt** a recipe/benchmark for causal world and brand construction. |
| 18 | [Shams kinetic character reveal](https://x.com/ShamsAmin56/status/2089699228427948519) | **Adopt** a recipe/benchmark for identity lock and exact typography. |
| 19 | [Single-Frame VAE 500K](https://huggingface.co/iamkaikai/MiniMax-H3-Single-Frame-VAE-500K) | **Experiment** still/structured decoder only; never default video VAE. |
| 20 | [Dave character introduction](https://x.com/dave392750/status/2090257804984828244) | **Adapt / extract** into the native character-reveal recipe. |
| 21 | [H3 Turbo-SLA](https://huggingface.co/lightx2v/Minimax-h3-Turbo-SLA) | **Experiment** four-step FL2V and sparse runtime separately. |
| 22 | [Kōda storyboard guidance](https://x.com/aimikoda/status/2090556776156742071) | **Adopt** a storyboard-as-sequential-shot recipe. |
| 23 | [Renataro noir/foley film](https://x.com/renataro9/status/2090573248752934994) | **Benchmark lead** for palette, cadence, and AV transient mapping. |
| 24 | [H3 Prompt Composer](https://github.com/BMB12d3/minimax-h3-prompt-composer) | **Adapt / extract** camera/input/project/checker ideas into the native composer. |
| 25 | [10Eros H3 INT8 ConvRot](https://huggingface.co/cicalooo/10Eros-Max-h3-int8-convrot) | **Experiment** Beta3 TURBO Hybrid only: six-step skip-edge first, fully quantized second; no FL2VA/Ref2VA or default/runtime claim. |
| 26 | [Naoneko action/graphics](https://x.com/Naonekozamurai/status/2091072883040600420) | **Benchmark lead** for action/contact/audio/graphic sync. |
| 27 | [TechHalla Origami Cities](https://x.com/techhalla/status/2091149136737546441) | **Adopt** a material-transition recipe. |
| 28 | [TechHalla Dusk to Words](https://x.com/techhalla/status/2091102688645644384) | **Adopt** a kinetic-material typography recipe. |
| 29 | [Mayz creator signature](https://x.com/Mayz1169/status/2091469241543455115) | **Watch** the motif; the post names MiniMax Design, not proven H3. |
| 30 | [CozyClay](https://github.com/NomaDamas/CozyClay) | **Reject** the dependency while extracting shot-package/shared-authoring ideas. |
| 31 | [ComfyUI PR #15808](https://github.com/Comfy-Org/ComfyUI/pull/15808) | **Adopt** the marker-token parity contract and CPU tests. |
| 32 | [TechHalla Ink Smoke](https://x.com/techhalla/status/2091676049251688806) | **Benchmark lead** for one-take reference-role/typography behavior. |
| 33 | [H3/LTX better NSFW motion v3257589](https://civitai.red/models/2344781/h3-ltx-23-ltx2-better-nsfw-motion?modelVersionId=3257589) | **Experiment** with the supplied H3 Ref2VA V1; keep LTX versions separate. |
| 34 | [H3 Fun ControlNet Union](https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union) | **Experiment** as a high-priority native typed H3 control capability. |
| 35 | [Tokyo Valentine page-turn film](https://x.com/tokyo_Valentine/status/2089987272838095022) | **Reject** as H3 evidence (Seedance), retaining the page-turn motif only. |
| 36 | [H3 Prompt Rewriter LoRA 8B](https://huggingface.co/lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B) | **Experiment** as an explicit local prompt-engine option, not H3 video LoRA. |

## Watch, not current implementation

| Item | Recheck / promotion trigger |
| --- | --- |
| SenseNova-U1.5 | Named Maestro still/reference job and local runtime budget |
| kimodo.cpp | Stable Blender retarget/export contract and useful local CPU/Vulkan sample |
| Mirelo Audio-to-MIDI | Music3 post-production/export wave |
| FastH3 later checkpoints | Training run completion or materially improved release |
| Javawock workflow details | Pin the exact graph only when implementing native Bridge tests |
| Prompt Composer upstream | Use as comparison; issue #19 field/role bugs remain useful regressions |
| MiniMax hosted Context-IR/Regenerate-2K | Local release or separately selected hosted adapter |

## Public evidence index

Key non-social canonical sources used to expand the raw ledger:

- NVIDIA H3 Super Acceleration:
  https://nvlabs.github.io/Sana/Sol-Engine/H3-Super-Acceleration/
- MiniMax H3 model and prompt guides:
  https://huggingface.co/MiniMaxAI/MiniMax-H3
- Prompt Composer pinned source:
  https://github.com/BMB12d3/minimax-h3-prompt-composer/tree/58a76b31f5e78445c69d873d39c0bb31158cedb8
- Comfy marker-token merge:
  https://github.com/Comfy-Org/ComfyUI/commit/924743af083c151296cc16f925aeab113b6484e8
- MATLOW motion adapter:
  https://huggingface.co/MATLOWAI/MiniMax-H3-Motion-Adapter
- Context Loop design source:
  https://github.com/ethanfel/ComfyUI-MiniMaxH3-Contex-Loop
- `kimodo.cpp`:
  https://github.com/localai-org/kimodo.cpp
- NKD tools:
  https://github.com/Nekodificador/ComfyUI-NKD-VFX-Tools
  and https://github.com/Nekodificador/ComfyUI-NKD-Preview-Tools
- Mirelo product surfaces:
  https://mirelo.ai/ and https://mirelo.ai/video-to-audio

Direct X pages were intermittently inaccessible to unauthenticated tooling.
Where needed, X's public oEmbed or a read-only mirror established the post text,
then linked official repositories/model pages controlled technical decisions.

## Remaining evidence boundary

This wave downloaded selected model assets and implemented CPU-only plans,
validation, profiles, and benchmark scaffolding. It did **not** load a Maestro
model, create a Maestro CUDA context, run inference, judge an output, activate
an experimental benchmark case, change a default, migrate storage, import a
Comfy graph/node pack, add prompt moderation, mutate Beads, or restart Maestro.
Both Beta3 artifacts completed pinned transfers and CPU-only
integrity/header/receipt checks. Commit `3855a94` also adds a model-free,
fail-closed runtime-admission seam with exact request and receipt binding plus a
held-descriptor consumption contract. It deliberately remains unwired from WGP
and the MiniMax H3 handler, so neither artifact has runtime or GPU acceptance.

GPU/runtime acceptance remains a separately scheduled wave after the current
external GPU owner releases capacity. Exact Dasiwa-base acquisition remains
open; the suspected-base case must remain visibly provisional even if its later
neutral coherence output looks useful.
