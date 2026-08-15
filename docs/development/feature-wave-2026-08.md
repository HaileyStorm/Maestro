# Maestro Feature-Wave Intake — August 2026

Status: evidence captured; ready for implementation waves

Evidence date: 2026-08-15

This is a decision and merge-order record for the public repositories, papers,
model cards, workflows, and community discussions reviewed for Maestro. A saved
link is not an endorsement. Each candidate is compared with Maestro's current
implementation, including recent comments and issue reports where available.

## Decision rules

- Prefer an existing Maestro capability when it already has stronger recovery,
  privacy, provenance, or cross-platform behavior.
- Extract a useful invariant or technique without importing an upstream agent
  framework wholesale.
- Keep experimental runtimes and model artifacts opt-in until exact model,
  quality, cancellation, and resource gates pass.
- Preserve per-model and per-task engine choice. A faster engine for one model
  must not silently become the engine for every local LLM or VLM.
- Treat community performance claims as benchmark leads, not defaults.
- Keep locally processed creative content local. No candidate authorizes a new
  moderation, scanning, or third-party classification layer.

## Baseline already present in Maestro

The intake must begin from the shipped baseline rather than reimplementing it:

- Local LLMs use a hardware-aware, pinned `llama-server` runtime with GGUF
  discovery, CPU and CUDA execution, model leases, cancellation, streaming,
  bounded request-scoped status, prompt caching, JSON-schema grammars, and
  optional native or projector-based vision.
- Configured OpenAI-compatible, OpenAI, and Anthropic providers are already
  separate selections with locality and authorization gates.
- MiniMax H3 already has FL2VA and Ref2VA planning, Full/Pruned and W4A8 paths,
  semantic references, native-length segmentation, continuation recovery,
  Director/Studio integration, LoRA support, audio/video finalization, and
  verified final-output true-peak protection.
- H3 attention already supports dense SDPA, Sol-Attn, and SageAttention2 with
  explicit fallback. Draft, Fast, Quality, High, Delivery, Spectrum, and
  Lightx2v profiles already separate quality and acceleration choices.
- Durable scoped LLM operations and Director preview operations already provide
  exact request/project ownership, cancellation, reload recovery, bounded
  telemetry, and stale-result fencing.

This makes several upstream projects useful as evidence or test vectors, but
not as dependencies.

## Wave 1 — Configurable local LLM engines

### SGLang: adopt as an isolated, capability-gated engine trial

**Decision:** experiment, then adopt per model/task if it wins. Keep
`llama.cpp` first-class and the default until the trial passes.

SGLang is a credible high-performance local serving engine with an
OpenAI-compatible API, structured output, multimodal support, prefix caching,
continuous batching, quantization, multi-LoRA, and several speculative-decoding
strategies. That makes it a real candidate, not a generic replacement pitch.
Its current installation documentation primarily targets NVIDIA GPU platforms;
it does not establish native Windows parity. Recent issues also show that new
model and speculative-decoding paths can have correctness or configuration
gaps. In particular, speculative decoding must not be enabled merely because a
server accepts the flag.

Implement the engine boundary before installing SGLang into Maestro:

1. Introduce a local-engine capability contract behind the existing LLM
   service. The contract covers load/unload, health/readiness, chat, streaming,
   cancellation, JSON-schema output, prompt caching, vision inputs, timings,
   context limits, and resource release.
2. Keep provider and engine distinct. `local` remains a provider; its engine can
   be `llama_cpp` or `sglang`. External providers do not pass through this
   selector.
3. Allow server-authored defaults plus per-model and per-purpose overrides.
   Chat, planning, enhancement, review, and image-understanding may choose
   different engines when their selected model and capability matrix justify
   it. A saved request records the resolved engine and model revision.
4. Fail closed when a requested capability is absent. Do not silently drop
   images, schemas, cancellation, or a selected draft model. An explicit
   `auto` policy may fall back to `llama_cpp` only before work begins and must
   publish the resolved engine truthfully.
5. Install the trial in an isolated runtime first. Do not replace Maestro's
   pinned PyTorch/CUDA stack or llama.cpp runtime during the benchmark.
6. Keep SGLang Diffusion and MiniMax H3 outside this initial engine change.
   SGLang's text/multimodal server and its newer diffusion stack are separate
   products with different risk and dependency surfaces.

Benchmark at least one text model and one vision model that Maestro already
supports. For each selected model, compare:

- cold load time, first-token latency, warm tokens/second, peak VRAM and RAM;
- ordinary chat, long-context reuse, structured JSON, and response-assist retry;
- streaming cancellation during blocked transport and during decode;
- image input, multi-image ordering, and vision failure behavior;
- unload/model switch, stale-handle fencing, and restart recovery;
- exact output correctness with speculative decoding off;
- draft/speculative support only as a later, separately verified matrix.

Promotion requires a material end-to-end win for a named model/task, not an
engine microbenchmark. Any speculative mode requires deterministic identifier,
JSON, tool/prompt, and long-context regressions because community reports have
included silent token corruption in new speculative paths.

**Platform rollout:** Linux first. Windows receives the same exact-revision
matrix only after upstream installation and kernel support are proven on the
Windows host. WSL is not equivalent to native Windows and must be labeled as
such if used. Until then, Windows remains on llama.cpp.

## Wave 2 — H3 preview, quality, and workflow experiments

### TAEH3: adopt as an optional preview decoder

**Decision:** implement a native Maestro experiment; never replace full-quality
decode or delivery output.

The new TAEH3 checkpoint is useful for rapid denoising previews and low-VRAM
inspection. Community reports emphasize that tiny video autoencoders can save
substantial time and sometimes avoid preview OOMs. They also show integration
friction: incorrect preview resolution, silent fallback, and a dependency on
nightly UI nodes were all encountered during the first week.

Maestro should integrate the checkpoint directly rather than import a ComfyUI
node. The preview path must:

- be explicitly labeled approximate;
- never write or substitute the final video;
- fall back visibly to the existing preview path when shape, dtype, model
  identity, or artifact verification fails;
- record preview decoder identity in diagnostics but not delivery metadata;
- close/unload resources and remain cancellation-safe;
- pass side-by-side latency, VRAM, temporal-coherence, and failure tests.

### H3 attention and step guidance: benchmark, do not overwrite defaults

**Decision:** adapt only after Maestro-controlled A/B evidence.

A community H3 roundup reported nearly identical runtime for 4V8A, 6V8A, and
8V8A with one prompt/seed and preferred 8V8A for formal output. It also reported
a roughly 12% end-to-end gain from CK Attention on one 4090 run, while warning
not to combine CK, Sage, and Sol indiscriminately. These are good benchmark
leads, not enough evidence for a new default.

Maestro already exposes SDPA, Sol-Attn, and SageAttention2 and already has
server-authored performance profiles. Extend the existing benchmark matrix
instead of adding another generic “fast” switch:

- test 4V4A, 4V8A, 6V8A, and 8V8A across dialogue, music, effects, fast motion,
  and reference-conditioned clips;
- record video and audio evaluations separately and enforce audio steps greater
  than or equal to video steps;
- evaluate CK only if its license, kernel/runtime compatibility, Windows story,
  masks, packed H3 layout, and fallback behavior are acceptable;
- compare CK alone against the existing SDPA, Sage, and Sol paths;
- preserve current defaults until blind audio/visual review supports a change.

### FL2VA versus Ref2VA: improve selection evidence, not model mythology

**Decision:** keep both; add controlled model-selection evidence.

Community comparisons found FL2VA less fuzzy in some fast-motion examples even
when used in a Ref2VA-style workflow, but other replies found Ref2VA smoother in
some regions and recommended trying both. Maestro already treats FL2VA and
Ref2VA as different conditioning tools and can plan different models per
segment. The useful change is a controlled comparison surface:

- same prompt, seed, references, frames, and output index;
- clearly label when an FL2VA checkpoint is accepting reference-like inputs;
- compare identity, fast motion, reference adherence, audio, and temporal
  continuity rather than one still frame;
- feed accepted results back into server-authored planning heuristics only after
  enough diverse pairs exist.

### Long-form music continuity: extract the reference-composition idea

**Decision:** adapt as an optional experiment.

One workflow used a single reference image containing multiple separated
locations to sustain a one-minute music video. Replies still observed a small
boundary jump, asked for memory requirements, and described the workflow as
difficult to operate. Maestro should not copy the graph. Instead, evaluate a
Reference Studio action that composes selected location anchors into one
clearly separated reference sheet, then compare it with Maestro's existing
per-shot references and continuation handoff. Boundary motion and lighting must
be reviewed at every native segment edge.

### LongMedia nodes: extract audio-role semantics and compatibility tests

**Decision:** do not import; compare selected invariants.

The LongMedia project fixed two consecutive video-reference compatibility bugs
after community reports. Its audio path was then redesigned to distinguish
reference audio, preserved source audio, and lip-sync guidance. That separation
is valuable. Audit Maestro's current H3 controls and metadata for the same
three-way distinction. If any role is ambiguous, add an explicit server-owned
audio intent and regression tests; do not copy the node package or its evolving
ComfyUI compatibility layer.

### Low-VRAM variants and LoRAs

**Decision:** expand experiments/catalog selectively.

- Maestro already has an experimental W4A8 FL2VA path. Kijai's card still calls
  W4A8 work in progress and ties one ConvRot VAE to an upstream PR; retain the
  experimental label and exact capability probe.
- ClipProj and GGUF H3 variants are candidates for a separate low-VRAM preview
  study. Measure quality, load time, RAM/offload cost, and generation speed;
  “it runs” is not enough for default support.
- `MiniMax-H3-Realism-People-LoRA` is the strongest initial catalog candidate:
  it documents one adapter across T2V/I2V/R2V, supplies same-seed comparisons,
  and declares the MiniMax H3 community license. Verify artifact identity,
  license display, trigger/strength guidance, audio preservation, and Maestro
  LoRA-affine compatibility before listing it.
- `MiniMax-H3-Looping-Sketch-Anime` is a narrower optional style candidate.
  Verify its missing/unclear license state before any managed listing.
- PinkFluffyBunny is a novelty LoRA with an Apache-2.0 declaration and alpha
  quality. It may remain user-installable through existing model discovery; it
  does not justify a default catalog slot.
- Do not recommend Heretic/abliterated LLM checkpoints as H3 conditioning text
  encoders. The Heretic author explains that abliteration perturbs hidden
  representations without adding generation knowledge, which can reduce prompt
  adherence without changing H3's learned output distribution. An uncensored
  local LLM may still be useful as a separately selected prompt enhancer; that
  is not the H3 conditioning encoder.

## Wave 3 — Research ideas to extract, not frameworks to merge

### Model Discovery Agent

**Decision:** use the experimental-design pattern, not the agent.

The paper combines LLM-proposed hypotheses with Bayesian inference,
value-of-information experiment selection, and predictive checks that expand an
inadequate hypothesis class. The useful Maestro application is the existing
paired sample campaign: choose the next A/B generation that most reduces
uncertainty about a profile, reference strategy, or runtime—not a new autonomous
scientist inside the product. Begin with deterministic experiment manifests and
human accept/reject labels; Bayesian scheduling is optional only after the
ordinary campaign produces enough data.

### Hermes memory benchmark

**Decision:** adopt the evaluation lessons; do not select a memory provider.

The seven-provider benchmark found different strengths for histories,
conditional preferences, and changing facts, while no provider reliably
rejected planted false memories. It explicitly did not test durability,
security, access control, or multi-user isolation. Maestro should keep files,
sealed ledgers, and project-scoped stores authoritative. If project memory is
expanded later, build conflict fixtures for changed facts, planted
contradictions, conditional preferences, provenance, deletion, and exact
project/account isolation before considering an external memory layer.

### OptMem

**Decision:** reject direct integration; retain two design prompts.

The compact memory tree and bounded wake-up prompt are interesting, but open
issues cover scoped memories, torn records/forget behavior, parsing, extra
inference passes, and licensing. Maestro already has stronger project/account
scope and durable operation ledgers. The only reusable ideas are a compact
pointer to authoritative project context and a bounded retrieval wake-up; both
must use Maestro's existing storage and provenance rules.

### ShadowFrog

**Decision:** reject as a dependency.

A maintained shadow knowledge base can help coding agents, but this is not a
Maestro user feature and overlaps current project documentation and tracking.
Community work still shows unfinished cross-platform/Windows effort. Do not add
a second writable codebase memory.

### Prime Agent

**Decision:** do not embed the framework; extract recovery invariants only.

Prime Agent's crash-safe JSONL, worker-identity fencing, bounded compaction, MCP
hardening, and provider-compatibility work are useful comparison points.
Maestro already implements many of these invariants in scoped operations,
Director recovery, and queue journals. Its v0.8 tracker repeatedly described
candidate branches as not human-ready and later moved community intake from a
large issue queue to discussion-first. Use individual invariants to review
Maestro; do not import the agent runtime.

### codex-router and modded-nanogpt

**Decision:** reject for Maestro.

`codex-router` is a Codex/provider-routing tool, not a Maestro product module.
Its general lesson—explicit engine compatibility and selectable routing—is
already captured in the SGLang engine plan. The modded-nanogpt training PR is a
training benchmark optimization with no credible current Maestro inference or
workflow integration.

## Merge train

Keep one writer per shared file cluster and merge in this order:

1. **Baseline and benchmark fixtures.** Freeze current llama.cpp text/vision
   behavior and H3 output/profile fixtures. No new engine or model code yet.
2. **Local engine contract.** Refactor `app/services/llm_service.py` behind a
   capability interface while preserving current behavior byte-for-byte at the
   HTTP/UI boundary. Update launch/config/catalog and scoped-operation tests in
   the same serialized backend lane.
3. **SGLang adapter and isolated Linux trial.** Keep installation optional and
   separate. Do not add UI defaults until measured model/task winners exist.
4. **Engine configuration UI.** Expose only supported choices, resolved engine,
   and actionable fallback state. Preserve per-model/per-task authority.
5. **TAEH3 preview lane.** This can proceed in parallel with the LLM adapter
   after its own file reservation because it belongs to H3 decode/preview, not
   LLM serving. Serialize any edits that meet in shared model catalog or UI
   settings files.
6. **H3 benchmark-driven refinements.** Run step/attention and FL2VA/Ref2VA
   comparisons before changing profiles. Land each accepted improvement
   separately with its output evidence and fallback.
7. **Catalog additions.** Add only verified artifacts whose license, hashes,
   model-family compatibility, triggers, and examples are complete.
8. **Windows parity.** Pull the exact accepted commits onto the Windows Maestro
   checkout and rerun platform-specific install, load, cancel, vision, preview,
   and cleanup gates. Linux success is not Windows acceptance.

Shared hotspots that must not have concurrent writers include
`app/services/llm_service.py`, `app/launch.py`, `app/wgp.py`, the MiniMax H3
handler/transformer/decoder modules, `ui/src/stores/useStore.ts`, and the model
selector/settings components. Independent benchmark artifacts and test-fixture
analysis can run in parallel; Git index/ref operations remain serial.

## Acceptance boundary for the feature wave

The base is ready for feature implementation when:

- current main is clean, pushed, and starts through the supported Pinokio flow;
- no candidate depends on an open browser tab or private URL;
- every candidate has an adopt/adapt/experiment/defer/reject decision;
- SGLang has a capability and benchmark plan without displacing llama.cpp;
- H3 work is expressed as measured deltas against Maestro's shipped profiles;
- shared-file ownership and merge order are explicit;
- Windows is treated as a separate acceptance target;
- no research agent or memory framework is imported merely because it is new.

## Public evidence index

### LLM engine

- SGLang repository and overview: <https://github.com/sgl-project/sglang>
- SGLang installation: <https://docs.sglang.io/get_started/install.html>
- New H3 diffusion audit issue: <https://github.com/sgl-project/sglang/issues/34954>
- Speculative-decoding corruption report: <https://github.com/sgl-project/sglang/issues/34959>

### H3 runtime, models, and workflows

- TAEHV/TAEH3: <https://github.com/madebyollin/taehv>
- TAEH3 request and integration discussion: <https://github.com/madebyollin/taehv/issues/24>
- LongMedia: <https://github.com/vizart-vj/ComfyUI-MiniMax-H3-LongMedia>
- LongMedia video-reference compatibility report: <https://github.com/vizart-vj/ComfyUI-MiniMax-H3-LongMedia/issues/1>
- LongMedia audio-reference report: <https://github.com/vizart-vj/ComfyUI-MiniMax-H3-LongMedia/issues/2>
- H3 roundup and community replies: <https://x.com/servasyy_ai/status/2087343267277160510>
- Music-video continuity workflow and replies: <https://x.com/alexfredo87/status/2086824831165587859>
- FL2VA/Ref2VA comparison and replies: <https://x.com/grmchn4ai/status/2087070449289343039>
- Kijai experimental H3 artifacts: <https://huggingface.co/Kijai/MiniMax-H3-experimental>
- Community workflow collection: <https://huggingface.co/javawock7618/comfy-MiniMax-H3-workflows>
- Realism People LoRA: <https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA>
- Looping Sketch Anime LoRA: <https://huggingface.co/Inner-Reflections/MiniMax-H3-Looping-Sketch-Anime>
- PinkFluffyBunny LoRA: <https://huggingface.co/SexGod1979/PinkFluffyBunny-MiniMax-H3>
- Heretic text-encoder warning and discussion: <https://www.reddit.com/r/StableDiffusion/comments/1vmdxzk/psa_im_the_creator_of_heretic_and_i_advise_you_to/>

### Research and agent frameworks

- Model Discovery Agent paper: <https://arxiv.org/abs/2608.09696>
- Hermes memory-provider benchmark: <https://engturtle.github.io/hermes-memconflict/report/>
- OptMem: <https://github.com/VictorTaelin/OptMem>
- ShadowFrog: <https://github.com/microsoft/ShadowFrog>
- Prime Agent: <https://github.com/PrimeIntellect-ai/prime-agent>
- Prime Agent v0.8 tracker/community record: <https://github.com/PrimeIntellect-ai/prime-agent/issues/1182>
- codex-router: <https://github.com/duolahypercho/codex-router>
- modded-nanogpt PR 315: <https://github.com/KellerJordan/modded-nanogpt/pull/315>
