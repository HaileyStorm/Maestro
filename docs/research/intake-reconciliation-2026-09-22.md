# Historical intake reconciliation and upstream assessment — 2026-09-22

## Status and scope

This audit corrects the September 21 blanket completion claim. Evaluated,
implemented, executable, tested on a particular device, and accepted by the
owner are different states. Several adopted historical capabilities remain
unimplemented. GPU availability is only one of the remaining dependencies.
The disposition inventory remains the September 22 baseline; delivery notes
and the promotion order below include verified follow-on work through
September 24.

Baseline: Continuum `d4dff8442979b6c0559d27b4272462f3c0ade4a7`.
Upstream reviewed: `Blizaine/Maestro` at
`5efd686ab446d451d927cbc00665e136d8de585e` (2.3.0 plus processor hotfix).
Common ancestor: `811f0f3b26abe615ea2df9ac34833bb820cc2a86`.
Upstream adds 74 commits and changes 659 files from that ancestor. This is a
selective adaptation assessment, not a whole-tree merge or a 2.3.0 parity claim.

The source appendix, [intake-source-index-2026-09-22.json](intake-source-index-2026-09-22.json),
preserves 228 distinct URL spellings with current note/line references and note
hashes. The September 24 refresh added the exact Quad LoRA artifact, creator
record, and FLUX base-license URLs after the media-model note gained 42 lines;
its shifted references were rechecked. These include mirrors, pinned revisions,
comments, and licensing evidence; they are not 228 separate products. It covers
ten tracked notes plus one ignored historical owner-observation note. A claim
about all captured sources is limited to this enumerated corpus. It does not
prove no other conversation or lost file ever contained a candidate.

The September 6 and September 14 task histories were checked by streaming only
user messages: no additional URL dump was found there. The September 21 YuE2
request is accounted for below. The August 23 raw 11-link dump maps to the
August 28 wave-2 record. No private browser-tab inventory was used as intake.
External pages in old notes have not all been fetched again; old license,
availability and benchmark observations retain their historical dates.

Decision procedure: [INTAKE.md](../operations/INTAKE.md). Historical negative
decisions and aliases remain preserved. Existing project/account access,
resource coordination, cancellation, recovery and local content neutrality
remain integration requirements. No upstream parity work may remove an
existing feature or silently change a selected model, engine or provider.

## What is already available

The recent implementation history includes:

| User-visible change | Evidence / limits |
| --- | --- |
| YuE2 composition, lyrics, ABC and installed LoRA selection | `086ef7d`, `38f4d3e`; `yue2_bridge.py`, `Yue2Controls.tsx`. Bridge/UI and prompt assembly use the existing local Sound/Vision service. September 23 follow-on live checks completed a native YuE2 take and a DreamPop v2 LoRA take, both visible in the project My Music library; exact technical receipts are in [GPU_ACCEPTANCE.md](../operations/GPU_ACCEPTANCE.md). The September 24 training-control update adds project-bound staging, queue/UI control, one live 200-step training run, and a private-checkpoint audition path. The signed-in stable-share UI showed project isolation, then generated one 51-second take with the trained checkpoint under a fresh validated grant. Owner listening and duration fidelity remain open. |
| Audio/video generation-path repairs | `086ef7d`: LTX audio runtime, H3 planning failures, YuE2. September 23 follow-on checks completed a fresh H3 two-segment final, a long-form Gallery reroll, FlashVSR2x, and single-voice SeedVC through the signed-in stable share. Their first-restart files and sidecars survived. The later inline-timeline fix `b2adc7b` passed a fresh 18-second H3 reroll: its executed second prompt retained the authored pinwheel beat, and sampled frames kept the tabletop subject across the join. Whole-video and owner quality remain separate acceptance; see [GPU_ACCEPTANCE.md](../operations/GPU_ACCEPTANCE.md). |
| Complete reusable technical profiles, update/save-as-new and restoration | Schema `610156e`, update/save-as-new `68744f1`, output restore `96dcf16`, and stale-profile clear `0533d7e`, plus audio-only fixes. Prompts, media and authorization are intentionally separate from technical profiles. |
| Blend Insert/Overlap, safer source restore and same-job source reattachment | `e673e51`, `75bf89a`, `6a3da85`, `8acfbb9`, `d4dff84`; exact source hashes, project fences, immutable request revisions, duplicate submission guard. |
| Better Generate/Director mobile controls | `5ae983a`, `40863a3`, `ba2784f`; footer layout, LoRA controls and review actions. |
| H3 durable recovery / final adoption | `e1cec99`, `5c4cd83`; completed outputs can retire corresponding recovery jobs. |
| Scene Kit kept Character/Location references in Generate | `70c2113`, `8969cee`; the signed-in stable-share UI staged two exact kept output IDs, then produced one FLUX.2 Klein 9B image that visibly followed both fixtures. The second commit repairs private snapshot storage on this installation's existing uploads-volume symlink. See [GPU_ACCEPTANCE.md](../operations/GPU_ACCEPTANCE.md#2026-09-24-scene-kit-to-generate-two-reference-image). This is one live reference path, not CharacterSheet execution or broad model parity. |

Human acceptance of these current changes remains pending: the owner explicitly
has not tried them. Earlier accepted, revision-bound cases remain valid history. A successful output does not establish quality across every model,
LoRA, input combination, platform or recovery path.

## Historical candidate disposition and actual delivery

The tables summarize the retained decisions at capability level. The original
notes own the detailed per-source decisions, rationale and comments. A native
implementation of an extracted idea does not imply the candidate package or
checkpoint was installed.

### Models, LoRAs and execution

| Candidate | Retained decision | Actual state / requirement before promotion |
| --- | --- | --- |
| MiniMax Music3 | Adopt native WanGP path; retain SGLang as isolated experiment | The upstream-native Music3 handler is registered for Studio and Director's existing audio queue, project output, recovery and same-owner local/LAN/Cloudflare access. The catalog exposes the real model only through the normal visibility whitelist; download and execution require the versioned host review term and exact official/optimized source manifest. The [official LICENSE](https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/fbdf52fbaaca799592917417eb05f1899f1255ec/LICENSE) is pinned at `fbdf52fbaaca799592917417eb05f1899f1255ec` with SHA-256 `b21d12df2adae59dad3fcf80c1d81492654c662342f497d9a9770198c9317e58`; the optimized WanGP assets are pinned separately at `DeepBeepMeep/TTS@d31b4665414200fcab779ced520b01bd9f5e07ba`. Byte-level lineage from that conversion to the newer official commit is not independently proven, so keep both source identities visible. CPU tests cover source drift, terms admission and catalog parity. No checkpoint has been downloaded, no GPU song has been generated, and quality/cancellation/recovery still need live acceptance. The separate SGLang runtime/client remains unconnected to production, with an adapter-only external signature and country gate; neither is an official license requirement. |
| Music3 Turbo FP8 and MiniMax Music Slider LoRA | Watch | No accepted managed recipe. Need exact artifact, base compatibility, adapter control and audio evidence after the Music3 executor. |
| YuE2 and installed Sound/Vision LoRAs | Adopt | Bridge-based generation/composition and a project My Music playback/download library are delivered. Native baseline, DreamPop v2, combined artist+style, manually scored instrumental, optional score-first adapter, and guide-backed instrumental takes have live technical receipts. A refreshed LoRA catalog blocks generation if a previously selected adapter has disappeared, instead of silently dropping it. The dedicated instrumental adapter was pinned and converted without tensor-value changes. It generated a clean automatic score, but with a supplied guide-backed ABC it ran to the 360-second semantic cap; the identical-score and same-seed no-LoRA control ended cleanly in 38 seconds. Its UI now warns about that combination. The adapter remains an explicit selection, with automatic selection on the Instrumental toggle unimplemented. Instrumental composition selects arrangement/score guides instead of lyric-writing or vocal-direction guides, asks for `[Instrumental]` without sung words, and retains YuE2's required two-voice ABC score. An initial guide-backed draft returned twelve bars for an eight-bar request; a later eight-bar draft had invalid native ABC pitch syntax. The revised composer produced an eight-bar-per-voice score accepted by the installed native parser, and its no-LoRA native take completed with no warnings or truncation. The 45-second audio exceeds the score's 17-second nominal duration, so exact audio-length fidelity and owner listening remain open. The combined artist+style take also reached the 360-second semantic limit and needs ending/listening review. The September 24 update adds a project-scoped training UI, durable jobs, source-take staging, coordinator grants, cancellation, and checkpoint quarantine; one live 200-step run produced a valid private checkpoint. A project-bound picker resolves that checkpoint by job, ID and hash at submission and worker start. Signed-in project-switch and actual 51-second audition generation passed under a fresh grant; owner listening and duration fidelity remain open. |
| CharacterSheet / Krea identity LoRAs | Adapt | `character_sheet_workflow.py`, capabilities and profile gates are planning/validation only; profiles explicitly unavailable/non-executable. Ordinary Reference Packs are a different delivered capability. The corrected contract requires a verified FLUX anchor, gives Quad a face close-up plus front/side/back full-body panels, starts optional visual review Off, and permits user-selected failed-role Qwen Image Edit repair. The September 24 contract update permits an absent local repair editor for initial generation, seals that absence, and rejects selected repair unless a local editor is available. The first Quad FLUX LoRA is pinned to its exact source revision, size and SHA-256. Managed download and native use require creator/base host terms; the CivitAI browser requires creator acceptance before fetching the exact version, hashes it before publication, and native use still requires the base notice. Continuum and Classic rehash installed copies before use, including case-only aliases. Studio, Director, and CivitAI expose the creator notice. That artifact support has CPU checks but no installed LoRA or live Quad generation. The first real `quad_flux2_klein` job still needs server-verified anchor provenance, durable queue/recovery, and atomic project-asset publication. Do not enable it from static readiness alone. Krea, Dynamic Krea, Triple FLUX, and the separate H3 Orbit Sheet experiment remain outside that first slice. |
| H3 Character Sheet Generator / H3 Orbit Sheet / OrbitSheets | Experiment | Separate orbit-sheet candidate, including the historical 73-versus-124-frame fixture; no enabled executor. Do not conflate it with CharacterSheet M2. |
| Realism People LoRA | Watch | Generic LoRA loading exists; candidate-specific artifact/license/trigger/strength/audio evidence does not. |
| Looping Sketch Anime LoRA | Defer | Historical license uncertainty and no accepted managed artifact. |
| PinkFluffyBunny | Reject managed default | Preserve user-import possibility; do not confuse it with the existing PinkCherry checkpoint. |
| 10Eros Beta3 / INT8 ConvRot | Experiment | Descriptor, evaluation and runtime scaffolding; handler remains deliberately unwired. Existing checkpoint rows are not proof of execution. |
| Dasiwa / Better NSFW Motion v3257589 | Experiment | Native admission and opt-in paths exist (`h3_dasiwa.py`, profiles, `wgp.py`); bounded prior coherence probes do not establish every base/quality combination. |
| FastH3 Preview v0.2 → v1 | Benchmark lead | v1 supersedes v0.2 for evaluation; preserve both sources. T2VA-only recipe, exact four-forward schedule and runtime/adapter/device checks remain. No production FastH3 profile. |
| Alibaba PAI Acc-LoRAs / PDD | Experiment | No native PDD interval-head executor in this baseline. Upstream now has one; needs adaptation to Continuum's execution/recovery contracts and device tests. |
| Turbo-SLA | Benchmark lead | Generic LoRA-family compatibility exists; no dedicated pinned SLA execution profile/quality acceptance. |
| STUDIO1939 old-animation / RAVEN streaming / MATLOW de-rope | Experiment | Distinct candidate artifacts/techniques, not aliases for Better Motion. No accepted dedicated execution path. |
| Single-Frame H3 VAE | Experiment | Lower-priority still-image/structured decode candidate. No replacement of the video VAE. |
| TAEH3 | Deferred preview experiment | No integrated candidate decoder. Approximate preview must remain distinct from delivery and retain cancellation/fallback. |
| W4A8 | Experiment | Opt-in installer/provenance/validation path exists; synthetic prerequisites do not establish full generation quality. |
| H3 ClipProj / GGUF / Kijai experimental variants | Benchmark lead | Generic GGUF LLM support and existing H3 quantization are not these candidate integrations. Exact runtime and device evidence required. |
| Fn-Mix Anima/Cosmos | Defer | No candidate-specific managed model; verify artifact, license and engine family before listing. |
| SenseNova-U1.5 | Watch | Separate still/reference model family; historical 35–50 GB envelope needs fresh artifact and hardware evaluation. |
| MAGI-2-preview | Reject current local integration | Historical hardware envelope is unsuitable; watch a smaller/distilled release. No installation planned from the old preview claim. |
| LuxTTS | Experiment / watch | Existing VoxCPM2, Chatterbox and other TTS handlers remain; no LuxTTS replacement or accepted adapter. |
| Mirelo Audio→MIDI | Watch | No local executable artifact established by the historical demo. |
| Qwen-Video-Edit | Watch | Research candidate, not a delivered local edit model. |

### Controls, continuity, quality and editing

| Candidate / extracted idea | Retained decision | Actual state / missing work |
| --- | --- | --- |
| FL2VA versus Ref2VA / LongMedia audio roles | Adopt / extract | Distinct native modes, reference binding and audio-role contracts exist. Controlled comparisons and quality remain separate from source correctness. |
| Multishot, Extender, Context Loop and continuation suites | Adapt | Native segment planners/continuity/recovery exist. Candidate-specific native continuation/Bridge/Guide executors remain separate; no graph import. |
| Javawock H3 Bridge workflow | Adopt native planned slice | Source recipe retained in the August 25 record; native `h3_bridge_plan.py` is a plan, not an executor. |
| H3 Bridge, Guide, Control and Regenerate 2K | Adapt / experiment | `h3_bridge_plan.py`, `h3_guide_plan.py`, `h3_control_plan.py`, `h3_native_continuation.py`, `h3_regenerate_2k.py` define sealed plans/admission but no enabled executor. |
| Comfy #15375 masks / Fun ControlNet Union / LanPaint / H3 inpainting Space | Adapt / experiment | Generic SAM/LTX Inpaint is not H3 audiovisual latent-mask execution. Need native mask/interval/untouched-region semantics. Do not import hosted subject-matter guards. |
| Comfy #15808 marker parity | Adopt | `_ensure_h3_marker_tokens` in `models/minimax_h3/conditioner.py` with marker tests in `test_minimax_h3.py`. |
| H3 FaceRefine / temporal face repair | Adapt | Generic repair primitives exist; dedicated H3 temporal crop/track/regenerate/composite path is unfinished. |
| H3 Sigma Refiner / 3D→real slider | Experiment | Schedule-tail / candidate adapter ideas only; no accepted automatic enhancement. |
| Ref2VA reference-size match/max, IA/face-mask tricks | Experiment | No dedicated user control or controlled fixture establishing these candidate workflows. |
| CQ Design LTX2.5 enhancer / CrossView Warp / LTX MSR | Experiment | LTX2.5 and ordinary adapters exist, but these specific artifacts/workflows are not delivered recipes. |
| ReDetail | Adapt / creative experiment | No dedicated source-preserving refinement action; LTX re-render must not be called faithful H3 enhancement. |
| FlashVSR / latent upscale / RIFE comparisons | Benchmark lead | FlashVSR technical delivery paths and some prior output checks exist. `ComfyUI-MiniMaxH3_LatentUpscaler` and RIFE comparisons and owner detail acceptance remain. |
| Sage2 / Sol / step/cache anecdotes | Benchmark lead | The tracked Sage record binds an older Torch/CUDA release. September 23 signed-in Base native, four/eight-step Turbo and same-seed 864×480 dense/Sage candidate outputs prove kernel execution without recorded fallback on this host; they do not replace the repeat-sample, source/runtime and owner review gates. Manual Sage settings now display as Custom while Draft/Fast remain disabled. Exact receipts are in [GPU_ACCEPTANCE.md](../operations/GPU_ACCEPTANCE.md). The older Sol Adopt decision remains an opt-in experiment; it does not authorize a default. No universal fastest-engine or few-step default. |
| EasyCache / FirstBlockCache | Defer | Historical fast-runtime advice is superseded; no automatic revival from the old note. |
| Manual per-clip horizontal flip | Adopt, CPU and live flow verified | Source: ignored `docs/development/segment-horizontal-flip-continuity-2026-08-19.md`. Mounted Gallery action plus CPU queue worker preserve the source, copy every audio stream and write provenance on a separate final output. Actual MP4/AAC, WebM/Opus and MOV/PCM tests pass. Signed-in stable Cloudflare action produced a new playable-format MP4 with unchanged 608×352 / 24 fps / 5.166667 s geometry and duration, equal encoded audio hash, unchanged source hash and correct mirrored pixels. Legacy sidecarless inputs can run but remain blocked after restart without existing project ownership evidence. Hard-crash publication recovery and human acceptance remain separate gates. |
| Suggested geometric flip | Experiment | Needs fixtures and an opt-in proposal; default automatic flip remains rejected. Later automated execution stays deferred. |
| Facing/screen-direction carry | Adapt, explicit-field slice delivered | Same-source H3 seam locks now carry an authored `screen direction:` or `facing:` line into the next clip's camera-world instruction. Ordinary prose does not create a direction lock; independent non-temporal boundaries and default flip behavior are unchanged. CPU planner checks pass. A first-class structured facing editor and GPU quality acceptance remain open. |

### Templates, styles, composition and LLMs

| Candidate family | Retained decision | Actual state / missing work |
| --- | --- | --- |
| Official H3 skills, Context-IR, prompt composer, OpenH3-IR | Adapt | `h3_upstream_skills.py` loads bounded data and offers native craft families; `director/workflow_templates.py` supplies advisory structure. Upstream skills/graphs are not executed as tools. |
| Animation principles, storyboard/parkour boards, character reveals, kinetic typography, limited palettes, logos, tapestry, origami/page turns, sprites, ink/smoke, noir/foley, fake speedpaint | Adapt / benchmark leads as recorded per source | Craft/data guidance exists for some families. A named recipe does not prove local reproduction of a social example. Keep exact source/model/seed/style and visual/audio checks for each promoted recipe. |
| Mayz, Kōda, Shams, Dave, Renataro, Naoneko, TechHalla, Andrew, lepadphone, PhotogenicWeekE, airina, ailker and Kashiko examples | Preserve source-specific decisions | All URLs remain in the appendix/original tables; missing post bodies/comments remain evidence gaps. Hosted Seedance/Magnific/Hailuo/Vidu/PixVerse examples are not local executors. |
| MiniMax Design desktop / Lazy Frames / OpenMontage / Director Cut Studio | Adapt ideas; reject wholesale runtime | Grow existing Director/Reference rather than import another project store, queue or agent canvas. |
| Remotion + Blender-MCP composition | Adopt neutral composition capability, unfinished | Typed Blender facade exists for primitives/materials/keyframes/rendering. A shared sealed composition package and 2D renderer are not delivered. Camera/light/physics extensions need their own contract. |
| Diffusion Studio / Revideo / Motion Canvas / Twick / Friction / CozyClay | Reference or benchmark; reject duplicate application dependency | Native Editor/composition can reuse ideas after project/output/resource review. Friction licensing and unresolved Rendave identity remain explicit constraints. |
| Prompt Intelligence / known-character index | Adapt / extract | Role-dialogue ablation and local evaluation-schema ideas; no downloaded character library or blanket prompt rule. Sources remain in the August 25 record. |
| kimodo.cpp / NKD VFX and Preview Tools | Watch / adapt respectively | CPU/Vulkan SMPL-X-to-Blender candidate; NKD face-rig/timeline/mask/camera/depth techniques. No imported tool suite or accepted runtime. |
| General SGLang hosting | Isolated experiment, unfinished | Current general LLM runtime remains llama.cpp; Music3-specific SGLang code is not a generic engine adapter. |
| Qwen Flash-Next endpoint | Defer | Await exact independently accepted artifact/runtime/tokenizer/template/modality/cancellation identity. An OpenAI-shaped URL alone does not establish compatibility. |
| Heretic / abliterated checkpoints | Keep roles distinct | Generic prompt-enhancer GGUFs are not interchangeable H3 conditioning encoders. The later bounded PinkCherry beta-0.6/Heretic INT8 conditioner acceptance in the August 25 record remains exact-artifact history, not blanket interchangeability. |
| H3 Prompt Rewriter LoRA 8B | Experiment, unfinished | Source contracts/dependency reports exist; process lifecycle/cancellation execution remains unsupported. |
| Model Discovery / Hermes | Adapt evaluation lessons | Use existing experiment/recovery/project records. No autonomous discovery agent or external memory provider implied. |
| OptMem / ShadowFrog / Prime Agent | Reject framework dependency; extract bounded invariants where recorded | Preserve existing authoritative project stores and queue/recovery. |
| codex-router / modded-nanogpt | Reject as Maestro modules | Different product/research surface. |

## Upstream assessment and selected ports

Read-only upstream HEAD check on 2026-09-23 still resolves to
`5efd686ab446d451d927cbc00665e136d8de585e`, matching this assessment.

| Upstream release/cluster | Integration disposition |
| --- | --- |
| 1.9.1 llama binary-release pointer (`1d1610d`) | Adapt semantic release → nightly resolution and asset checks; retain Continuum staged install, SONAME repair, cached reuse and CUDA-build policy. |
| 2.1.3 UTF-8 transport (`5b47516`) | Adapt byte-first SSE decoding at both current streaming paths and explicit UTF-8 JSON response decoding; preserve existing cancellation/retry/progress machinery. |
| 2.2.3 H3 RMSNorm (`f614f61`) | Adapt inference chunking with Continuum's existing 8192-token limit; preserve native normalization, hooks, gradients and settings. GPU memory/throughput acceptance remains pending. |
| 2.0.1 Gallery Extend handoff (`5734846`) | Adapted and shipped as `81f43e7`: existing Continuum actions open Studio / Video / Extend and attach the selected clip. Workspace-qualified fetches, stale-request cancellation, visible errors and active/stashed preview ownership are covered by 688 UI tests plus lint/build. Signed-in stable Cloudflare flow from Audio to Extend was exercised with an existing 5.2-second clip; no generation submitted. |
| 2.0 Editor / layered timeline / export | Useful substantial addition; no timeline Editor exists in Continuum yet. Start with a project-scoped **Open in Editor** action from a Gallery video, non-destructive clip import, and autosaved timeline persistence. Server-side project read/write authorization and workspace-switch safety are required even for that first slice. Export, AI round trips, and queue/recovery integration follow separately; upstream cross-folder browsing is not authorization. |
| 2.0–2.2 H3 character/story/targeted-window repair | Compare with native Reference and H3 authored/durable contracts. Port improvements by behavior; no replacement of current identity or recovery authority. |
| 2.1 VAE/residency/INT8/NVFP4/VDN/Viggle/audio refinement | Separate runtime/compatibility changes; benchmark each accepted path under a fresh lease. Preserve existing manual profiles and working kernels. |
| 2.2–2.3 YuE2 / My Music / training / instrumental / multi-LoRA | The current bridge exposes a project-filtered My Music library, guide-backed instrumental composition, and native multi-LoRA selection through the installed Sound/Vision service. No-LoRA, single-LoRA, combined-LoRA, manually scored instrumental, adapter-led automatic-score, guide-backed instrumental, and trained private-checkpoint takes have live technical receipts. The combined take reached a generation limit. The guide writer now has a signed-in eight-bar-per-voice draft accepted by the installed native parser and a clean no-LoRA score-to-audio take; an earlier draft missed the requested bar count, and the rendered 45-second audio exceeded the later score's 17-second nominal duration. Exact audio-length fidelity remains open. The dedicated score-first LoRA reached the semantic cap with that supplied score while an identical-score, same-seed no-LoRA control ended cleanly; the UI warns and offers adapter-led score planning or base-model supplied-score rendering. One pair and metadata cannot establish listening quality. The September 24 update below implements project-bound training control with the existing fast-storage AI Toolkit and joint-v9 pair; one 200-step run produced a private checkpoint, and a subsequent signed-in audition generated a 51-second take. Owner listening remains open. |
| 2.3 Qwen Image 2.1 + processor hotfix (`f83a714`, `5efd686`) | Useful model addition with new architecture, 10-reference and RGBA semantics. Hotfix cannot be applied alone because this baseline has no qwen21 handler. The [official model card](https://huggingface.co/Qwen/Qwen-Image-2.1) links a [Qwen Research license](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/LICENSE) limiting use to non-commercial purposes. Pin the exact source and license revision and require the owner's explicit host-use review before downloading or activating weights; then cover catalog/runtime and output behavior. |
| PWA / Web Push / Tailscale / notification history | Optional architecture work; preserve stable Cloudflare and current access/privacy. No silent remote-service activation. |
| 30-second H3 single pass | Experimental capability requiring separate geometry/memory/recovery acceptance; existing long-form segmented generation stays. |
| Upstream Classic UI removal / generic fallback / content classification | Do not port behavior that removes existing features, obscures a chosen runtime or violates local content neutrality. |

The [YuE2 real-audio model card](https://huggingface.co/Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4)
documents a matched joint-v9 tokenizer head and NAR decoder LoRA. The pinned
pair is now an opt-in Sound/Vision decoder profile exposed in Maestro, separate
from artist/style LoRAs and disabled for those combinations. A September 24
same-score, same-seed live comparison completed under a validated 20-minute
GPU grant: semantic tokens matched stock, while latents and audio differed.
Both 48 kHz stereo WAVs lasted 44.9987 seconds with no clipped samples or
truncation. This proves the native decoder path ran, but neither the card's
measurements nor one technical pair establishes improved listening quality;
owner audition and exact score-to-audio duration fidelity remain open. See
[the technical receipt](../operations/GPU_ACCEPTANCE.md#2026-09-24-yue2-joint-v9-decoder-comparison).

At the September 22 audit, the native YuE2 trainer had only a manual CPU
preflight. It lacked project-owned source staging, service-level GPU leases,
controlled run names, and durable generation/training arbitration. Training
therefore stayed unavailable in Maestro then.

### 2026-09-24 YuE2 training-control update

Maestro now authorizes training against its selected project and a finished
YuE2 take in that project's My Music library. The Sound/Vision service stages
that take on fast storage, writes a controlled AI Toolkit configuration with
captions and lyrics, and runs one durable training job at a time alongside its
generation queue. Jobs request fresh coordinator leases, can be cancelled, and
fail closed if a child process cannot be confirmed stopped. Checkpoints remain
outside the host-wide installed LoRA catalog until explicit audition and
installation; training does not silently publish an adapter to other projects.

Backend verification passed 282 Sound/Vision tests with one skip. The Maestro
bridge/UI has focused route and UI tests; a signed-in stable-share browser
submitted and cancelled a project-scoped training smoke job before GPU use.
It then submitted a separate 200-step style job from a finished project take.
The service obtained a fresh GPU grant, completed the run, validated one
private safetensors checkpoint, and withdrew the grant. The detailed source,
audio, checkpoint and lease evidence is in [GPU_ACCEPTANCE.md](../operations/GPU_ACCEPTANCE.md#2026-09-24-yue2-project-training-smoke).
This proves one local training execution, not a useful adapter or quality. A
follow-on signed-in stable-share audition selected the private checkpoint and
completed a 51-second take under a fresh exact coordinator grant. Project
switching hid the checkpoint outside the training project. A subsequent idle
Sound/Vision restart retained the training job, checkpoint, take and identical
WAV hash while the stable share remained ready. Owner listening, exact
score-to-audio duration, full-machine recovery, and Windows acceptance remain
open. The former CLI starter remains a separate manual path.

## Changes made during this audit

In addition to the selected upstream backend ports, YuE2 document allocation
was corrected: eight core guides could exhaust the 28,000-character budget
before Japanese/city-pop/jazz/harmony guides were included. Budget is now shared
across available guides, including headings/separators. The public guide list
describes documents actually included. Reproducing the same request now includes
all twelve selected guides within 27,999 characters. This is prompt-assembly
evidence, not a generated-song quality comparison.

Published source milestones: `badde36` (H3 normalization), `9d62da5` (LLM
transport/runtime resolution), `7cd2c83` (music guide budget). Final affected
LLM/runtime modules: 192 tests pass. H3/music modules: 80 tests, 11 existing
CUDA-gated skips, no failures. Compilation and tracked-publication checks pass.

CPU verification includes native RMSNorm parity across FP32/FP16/BF16,
noncontiguous batches, module hooks, gradients and a full block/final layer;
real Requests streaming with UTF-8 and embedded Unicode line separators;
mocked release pointer/asset failures; music guide budget and missing-file cases.
Device-masked H3 suites retain their CUDA skips. Current deployment/check
receipts and exact publication commits are reported with the delivery message.

Follow-on Gallery flip verification: 693 UI tests, lint/build, 10 route/lifecycle
checks, 7 actual FFmpeg transform checks, and 165 queue-recovery checks pass.
The full backend discovery ran 5,183 tests with 17 skips and exposed two existing
fixture problems: the H3 profile namespace omitted a real helper, and a lease
exclusion test included unbounded whole-process garbage-collection latency in
its two-second join. Both fixtures were corrected; their complete modules pass
17 and 31 tests respectively. At that checkpoint only the affected modules were rerun; the full
backend result below supersedes that earlier run. The separate five JSON-grammar checks also pass.

Upscale/Revoice follow-on verification (2026-09-23): 16 model-free tool execution
checks cover exact input ownership/content, legacy recovery refusal, route scope,
processor reachability, cancellation, crash-result adoption, changed result
rejection, publication durability failures, and cancellation during recovery adoption.
The independent correction review found no remaining release blocker. Four exclusive-rename checks,
21 delivery checks, 10 existing flip checks and 60 lifecycle checks pass. These
are CPU, synthetic and filesystem evidence, not GPU acceptance. The first full
backend run completed 5,198 tests with 17 skips and four fixture failures: three
UI source anchors predated the project-bound poller signature, and a queue-only
Director fixture depended on free space on the host temporary drive. Corrected
fixture modules pass 51 tests. The second full backend run passes 5,207 tests
with 17 skips, plus all five JSON-grammar checks. The final cancellation-adoption
correction separately passes all 16 tool checks, and the managed Music3 command
change passes 82 runtime/catalog/staging checks. Abrupt process
termination can still leave private temporary directories for later cleanup;
no arbitrary prefix-based deletion is authorized by these changes.

Music3 CPU groundwork (2026-09-23): the upstream-native WanGP handler now enters
the normal Studio and Director generation path, and its source/license graph is
checked before download and execution. The separate managed SGLang command binds
the exact served model name, while its loopback HTTP transport bounds responses,
rejects redirects and unsupported encodings, and closes its request on
cancellation. Ten disposable HTTP-server tests and 36 client tests pass for that
isolated path. SGLang does not provide the production Music3 caller. Neither
native source checks nor transport tests prove a playable song, GPU cancellation,
or crash recovery; those remain live acceptance targets.

H3/inpaint CPU repairs (2026-09-23): the H3 continuation encoders now have their
required subprocess import. Prompt mapping loads its sealed manifest from the
owning project, including when output is staged elsewhere, and uses that same
project for reference validation. Inpainting probes source geometry before SAM
pre-scaling; its temporary video stays in a private project directory, preserves
an existing source-adjacent file, and is removed on success or failure. SAM's
completed mask is moved to a fresh private project mask directory before that cleanup
so the queued retake can still read it; a mask returned outside staging or
through a symlink is rejected, and a permissive destination is not reused.
The affected CPU checks include real FFmpeg media encoding; no model or GPU
generation was attempted. Full release checks and live deployment evidence remain
separate from these CPU results.

The next core WanGP static pass found separate unbound names in native H3
boundary audio decoding, H3 pre-mux recovery, Multitalk overlap preparation,
and temporal upsampling. Those paths now bind their inputs before use;
Classic aligned-pose validation also returns its ordinary UI error instead of
calling a missing helper. The combined affected H3/inpaint suite passes 74 CPU
tests, including an extracted H3 recovery branch using the configured video
container. `F821` has no remaining findings in `launch.py` or `wgp.py`.
The release gate passes 5,222 backend tests with 17 skips, five JSON-grammar
checks, 693 UI tests, and the UI type-check/build under the CPU-only test
environment. This does not establish GPU generation or owner acceptance.

## Outstanding work and promotion order

1. Expand live GPU acceptance for the repaired Upscale/Revoice workers. The
   removed resolver, missing recovery dispatch, unsealed source/reference inputs,
   host-global remote workspace fallback, public working copies, and broad output
   collection have CPU-tested repairs. Exact manifest inputs and create-only
   publication now support verified crash-result adoption without processor
   replay; FlashVSR scratch files stay in private project staging. One signed-in
   FlashVSR2x and one single-voice SeedVC output completed and survived their
   first restart. Other variants, cancellation, in-flight crash recovery,
   speaker/detail quality, and owner acceptance remain open. Gallery Extend and manual flip have CPU/UI/live-flow
   evidence; flip hard-crash adoption and human acceptance remain distinct.
2. Complete Music3 native live generation and recovery acceptance after the
   pinned host-term review and asset download. Implement the missing CharacterSheet executor in coherent slices; the first Quad LoRA artifact/terms boundary is CPU-tested, but its project job and live run are still missing;
   its static scaffold must not be advertised as a working product. Scene Kit's
   separate kept-reference handoff now has one live two-image receipt.
3. Adapt upstream Qwen2.1 and Editor as separate integrations with complete
   settings/restore/access/recovery coverage. Native YuE2 generation,
   project-scoped library, training and private-checkpoint audition now have
   live technical receipts; owner listening, duration fidelity, broader LoRA
   combinations, crash recovery and Windows checks remain.
4. The owner opened a September 23 GPU window. The completed H3/FlashVSR/SeedVC
   checks used one exact six-hour coordinator grant, then withdrew it after
   the jobs reached terminal success. A later six-hour grant covered the
   signed-in Sage2 candidate native and same-seed Turbo comparison recorded in
   [GPU_ACCEPTANCE.md](../operations/GPU_ACCEPTANCE.md). The owner has since
   asked for future grants sized to the work, typically 10–30 minutes; the
   already-issued grant remains valid. Request and validate a fresh exact grant
   for each later GPU slice. A grant alone does not establish runtime or
   quality acceptance.
5. Keep experimental acceleration/LoRA/preview/repair candidates opt-in until
   exact artifact, cancellation, recovery and quality checks pass. Windows and
   owner listening/visual acceptance remain separate targets.

The planned sample-campaign stories (Reference Lock, One Idea/Four Roles,
Recovery Is a Feature, Pocket to Picture Lock, Continuity Rescue, Break It on
Purpose, One Note/Three Consequences, Reference Enters/Direction Emerges) remain
design-ready until matched control and review evidence exists. This report
does not close those stories or erase historical tracker records.

## Evidence boundary

No new model installation, GPU generation, training run, model-default change,
third-party creative-content transfer or human acceptance occurred in this
audit. Source fetches, CPU checks and source integration do not establish those
claims. The inventory is now explicit; the unfinished implementations above
are still unfinished.
