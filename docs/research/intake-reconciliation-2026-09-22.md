# Historical intake reconciliation and upstream assessment — 2026-09-22

## Status and scope

This audit corrects the September 21 blanket completion claim. Evaluated,
implemented, executable, tested on a particular device, and accepted by the
owner are different states. Several adopted historical capabilities remain
unimplemented. GPU availability is only one of the remaining dependencies.
The disposition inventory remains the September 22 baseline; delivery notes
and the promotion order below include verified follow-on work through
October 5.

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

The September 25 Editor/hidden Quad/API-key release gate passed 5,336 backend
tests (17 skipped), the JSON-grammar regression, 718 UI tests, type-check and
production build, targeted lint, and four Editor desktop/mobile browser cases.
The Editor signed-in stable-share draft and preview check is recorded below;
the Quad executor and API-key failure retry have CPU/synthetic evidence only.

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
| YuE2 and installed Sound/Vision LoRAs | Adopt | Bridge-based generation/composition and a project My Music playback/download library are delivered. Native baseline, DreamPop v2, combined artist+style, manually scored instrumental, optional score-first adapter, and guide-backed instrumental takes have live technical receipts. A refreshed LoRA catalog blocks generation if a previously selected adapter has disappeared, instead of silently dropping it. The dedicated instrumental adapter was pinned and converted without tensor-value changes. It generated a clean automatic score, but with a supplied guide-backed ABC it ran to the 360-second semantic cap; the identical-score and same-seed no-LoRA control ended cleanly in 38 seconds. Its UI now warns about that combination. The adapter remains an explicit selection; the Instrumental toggle deliberately does not select it automatically. Instrumental composition selects arrangement/score guides instead of lyric-writing or vocal-direction guides, asks for `[Instrumental]` without sung words, and retains YuE2's required two-voice ABC score. An initial guide-backed draft returned twelve bars for an eight-bar request; a later eight-bar draft had invalid native ABC pitch syntax. The revised composer produced an eight-bar-per-voice score accepted by the installed native parser, and its no-LoRA native take completed with no warnings or truncation. The 45-second audio exceeds the score's 17-second nominal duration, so exact audio-length fidelity and owner listening remain open. The combined artist+style take also reached the 360-second semantic limit and needs ending/listening review. The September 24 update adds a project-scoped training UI, durable jobs, source-take staging, coordinator grants, cancellation, and checkpoint quarantine; one live 200-step run produced a valid private checkpoint. A project-bound picker resolves that checkpoint by job, ID and hash at submission and worker start. Signed-in project-switch and actual 51-second audition generation passed under a fresh grant; owner listening and duration fidelity remain open. |
| CharacterSheet / Krea identity LoRAs | Adapt | The contract requires a verified FLUX anchor, gives Quad a face close-up plus front/side/back full-body panels, starts optional visual review Off, and permits user-selected failed-role Qwen Image Edit repair. An absent local repair editor is sealed for initial generation; selected repair still requires one. The first Quad FLUX LoRA is pinned to its exact source revision, size and SHA-256. Managed download and native use require creator/base host terms; the CivitAI browser requires creator acceptance before fetching the exact version, hashes it before publication, and native use still requires the base notice. Continuum and Classic rehash installed copies before use, including case-only aliases. The September 25 hidden Quad route now resolves a server-verified FLUX anchor, admits a durable private parent/child job, rechecks model/LoRA/terms at worker start, and atomically publishes four hashed panel candidates with committed-publication recovery. CPU executor, route and adjacent Reference Pack checks pass. Public capabilities remain unavailable, no Quad LoRA is installed, no GPU Quad generation or owner visual review has occurred, and a restart before publication needs resubmission. Do not advertise static readiness as a working product. Krea, Dynamic Krea, Triple FLUX, and the separate H3 Orbit Sheet experiment remain outside that first slice. |
| LTX Best Face ID (`source-102`) | Experiment; no managed installation | The August media-model note listed an identity-reference workflow but gave it no decision. The [current model card](https://huggingface.co/Alissonerdx/LTX-Best-Face-ID) describes an LTX-2.3 identity LoRA using overlap/source-phase reference conditioning through BFS Nodes, and the [creator says](https://huggingface.co/Alissonerdx/LTX-Best-Face-ID/discussions/7) WanGP would need that behavior implemented for equivalent results. Ordinary LoRA loading is not this conditioning path. The card labels its license `other`; pin the exact checkpoint and terms, then evaluate an opt-in native conditioner and visual evidence before any managed install. No Continuum executor or quality result is claimed. |
| H3 Character Sheet Generator / H3 Orbit Sheet / OrbitSheets | Experiment | Separate orbit-sheet candidate, including the historical 73-versus-124-frame fixture; no enabled executor. Do not conflate it with CharacterSheet M2. |
| Realism People LoRA | Watch | Generic LoRA loading exists; candidate-specific artifact/license/trigger/strength/audio evidence does not. |
| Looping Sketch Anime LoRA | Defer | Historical license uncertainty and no accepted managed artifact. |
| PinkFluffyBunny | Reject managed default | Preserve user-import possibility; do not confuse it with the existing PinkCherry checkpoint. |
| 10Eros Beta3 / INT8 ConvRot | Experiment | Descriptor, evaluation and runtime scaffolding; handler remains deliberately unwired. Existing checkpoint rows are not proof of execution. |
| Dasiwa / Better NSFW Motion v3257589 | Experiment | Native admission and opt-in paths exist (`h3_dasiwa.py`, profiles, `wgp.py`); bounded prior coherence probes do not establish every base/quality combination. |
| FastH3 Preview v0.2 → v1 | Benchmark lead | v1 supersedes v0.2 for evaluation; preserve both sources. T2VA-only recipe, exact four-forward schedule and runtime/adapter/device checks remain. No production FastH3 profile. |
| Alibaba PAI Acc-LoRAs / PDD | Experiment; CPU executor implemented | The paired eight-evaluation interval-head executor now preserves FP32 output projections, separate video/audio schedules, normal backbone LoRA conversion, runtime source identity and transactional cleanup. FL2VA and Ref2VA remain distinct, with ordinary Ref2VA audio references retained. Native admission stays closed pending exact adapter/base integrity and device qualification; CPU tests do not establish native generation or quality. |
| Turbo-SLA | Benchmark lead | Generic LoRA-family compatibility exists; no dedicated pinned SLA execution profile/quality acceptance. |
| Official H3 Turbo (`source-031`) | Adopt task- and resolution-specific 4/8-step profiles | The August 18 decision is profile-scoped, never a global step or LoRA change. Managed Turbo 4/8 has signed-in live technical samples in [GPU_ACCEPTANCE.md](../operations/GPU_ACCEPTANCE.md), but this historical GitHub link alone does not establish byte lineage for every installed adapter or broad quality acceptance. |
| LightX2V FL2V Turbo eight-step BF16 (`source-139`) | Benchmark lead | The August 28 note pins artifact revision, size, digest, reported settings and quality concerns. Keep this eight-step FL2V candidate distinct from existing LightX2V four-step and managed Turbo; its proposed comparison has no accepted native execution or owner quality result. Do not infer Ref2VA support. |
| STUDIO1939 old-animation / RAVEN streaming / MATLOW de-rope | Experiment | Distinct candidate artifacts/techniques, not aliases for Better Motion. No accepted dedicated execution path. |
| Single-Frame H3 VAE | Experiment | Lower-priority still-image/structured decode candidate. No replacement of the video VAE. |
| TAEH3 (`source-073`, `source-074`) | Defer implementation of historical Adopt target | The August feature wave chose an optional, approximate native preview decoder; this is a priority deferral of that target, not a reversal or a claim it shipped. No decoder is integrated. Promotion still requires exact checkpoint/runtime identity, visible fallback, cancellation/resource handling and side-by-side preview evidence; full-quality delivery remains authoritative. |
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
| Multishot, Extender, Context Loop and continuation suites | Adapt | Native segment planners/continuity/recovery exist. Bridge and FL2VA still Guide have separate native executors. Two-still interior Guide source/API/UI support and one 124-frame native pair at frames 31/90 are verified. The original HEVC browser failure and three black opening frames are retained; a separate CPU H.264 copy played to completion. Owner visual acceptance remains pending. Broader Guide media and native continuation remain separate. No graph import. |
| Javawock H3 Bridge workflow | Adopt native executable slice | Native project-bound Gallery selection now binds two source revisions and sealed frame ranges, generates a Ref2VA AddGuide interval, and assembles an A + bridge + B final without importing the source graph. Three signed-in stable-share submissions completed 345-, 328-, and 328-frame H.264/AAC results under separate exact GPU grants; see [GPU acceptance](../operations/GPU_ACCEPTANCE.md). The second run exposed a client store-reconnect fault despite backend Queue visibility; after the fix, the third run showed its accepted job immediately in Queue, running progress, terminal clearance, and a private Gallery final that survived restart. Sampled first-run seam frames retained the subject. Full playback, soundtrack/continuity quality, and owner acceptance remain open. |
| H3 Bridge, Guide, Control and Regenerate 2K | Adapt / experiment | Bridge has an executor and Gallery action. A separate Gallery action now binds one authorized still to an interior frame of an H3 Base FL2VA clip. A signed-in stable-share submission completed one 124-frame A/V output under a validated GPU grant; Queue, Gallery, sidecar hash, full decode, and sampled source-frame similarity passed. The current in-app browser displayed a black HEVC picture while its timeline advanced; an H.264 control played visibly, so browser playback for HEVC remains open. A two-still extension now binds both exact source revisions and frame positions, preserves aspect through cropping, inherits both access flags, and disables endpoint trimming for interior guides. CPU/source/UI checks and a UI build pass. One two-still native run retained both anchors, all 124 frames and zero tail trim; full CPU decode passed. The original HEVC preview failed, while the separate CPU H.264 browser copy played to completion without altering the original. Three black opening frames remain a visual defect, and owner acceptance is pending. The executable source/API/UI path now accepts three ordered Gallery stills at distinct interior frames, binding every revision, source byte hash and access flag. The third still uses a private worker-to-native tensor transport, not a semantic reference. Focused CPU tests exercise all three source replay gates, worker transport, native crop/Picture order and packer times; the UI build passes. One Gallery-submitted three-still native run now completed 124 frames at 1344×768, 24 fps and 28 steps, with source positions 62/90/31 retained in selection order. Full video/audio decode, sanitized Guide metadata, post-restart Gallery visibility and separate H.264 browser-copy playback passed; no near-black frames were measured in this sample. Original HEVC browser playback remains unsupported, and the earlier two-still opening defect remains a negative. Owner whole-clip and listening acceptance remain pending. Guide video/audio, more than three stills, Control, native continuation, and Regenerate 2K remain separate; `h3_control_plan.py`, `h3_native_continuation.py`, and `h3_regenerate_2k.py` are still plan/admission only. See [GPU acceptance](../operations/GPU_ACCEPTANCE.md). |
| Comfy #15375 masks / Fun ControlNet Union / LanPaint / H3 inpainting Space | Adapt / experiment | Generic SAM/LTX Inpaint is not H3 audiovisual latent-mask execution. Need native mask/interval/untouched-region semantics. Do not import hosted subject-matter guards. |
| Comfy #15808 marker parity | Adopt | `_ensure_h3_marker_tokens` in `models/minimax_h3/conditioner.py` with marker tests in `test_minimax_h3.py`. |
| H3 FaceRefine / temporal face repair | Adapt | Generic repair primitives exist; dedicated H3 temporal crop/track/regenerate/composite path is unfinished. |
| H3 Sigma Refiner / 3D→real slider | Experiment | Schedule-tail / candidate adapter ideas only; no accepted automatic enhancement. |
| Ref2VA reference-size match/max, IA/face-mask tricks | Experiment | No dedicated user control or controlled fixture establishing these candidate workflows. |
| CQ Design LTX2.5 enhancer / CrossView Warp / LTX MSR | Experiment | LTX2.5 and ordinary adapters exist, but these specific artifacts/workflows are not delivered recipes. |
| ReDetail | Adapt / creative experiment | No dedicated source-preserving refinement action; LTX re-render must not be called faithful H3 enhancement. |
| FlashVSR / latent upscale / RIFE comparisons | Benchmark lead | FlashVSR technical delivery paths and some prior output checks exist. `ComfyUI-MiniMaxH3_LatentUpscaler` and RIFE comparisons and owner detail acceptance remain. |
| H3 output verification (`source-092`) | Adapt, CPU publication slice and ordinary live generation verified | The September 26 CPU gate checks nonempty regular media, decoded frame count, frame grid, duration, positive dimensions and rate, promised audio presence, and exact saved-plan frames/rate where available. H3 delivery also checks an explicitly requested output canvas. Ordinary H3 Finals remain out of the queue, Gallery and search behind a separate pending marker until verification; failed media is moved to private quarantine when possible, and locked media retains the marker. Delivery failures roll back before final publication. Dark/static/silent signal samples remain advisory and never judge creative subject matter. CPU fixtures and regression checks cover ordinary recovery, delivery rollback and Gallery classification. Under a validated local GPU grant, signed-in stable-share generations produced a private 5.167-second H3 final (124 frames) and a private 17.167-second three-segment final (412 frames), both 608×352 at 24 fps with AAC audio. The deployed probe passed all structural checks, pending markers cleared, and both finals appeared in the project Gallery; sampled frames in the longer take retained the robot and umbrella across the book and plant beats. Exact native audio-format fidelity, Director assembly and owner playback/quality acceptance remain open. |
| Sage2 / Sol-Attn / step/cache anecdotes | Benchmark lead | The tracked Sage record binds an older Torch/CUDA release. September 23 signed-in Base native, four/eight-step Turbo and same-seed 864×480 dense/Sage candidate outputs prove kernel execution without recorded fallback on this host; they do not replace the repeat-sample, source/runtime and owner review gates. Manual Sage settings now display as Custom while Draft/Fast remain disabled. Exact receipts are in [GPU_ACCEPTANCE.md](../operations/GPU_ACCEPTANCE.md). Sol-Attn remains a distinct opt-in kernel experiment; no universal fastest-engine or few-step default follows. |
| Sol-Super hybrid (`source-159`) | Adopt architecture as an experimental profile family | The August 25 decision specifies Hybrid Draft, Hybrid Refine, then Hybrid Sol, with dense RTX 5090 parity before sparse kernels or tiny autoencoders. No three-stage H3-to-LTX executor or accepted 5090 result is delivered. NVIDIA's GB200 timing is a benchmark lead, not a Maestro default or proof that ordinary Sol-Attn alone implements the hybrid. |
| EasyCache / FirstBlockCache | Defer | Historical fast-runtime advice is superseded; no automatic revival from the old note. |
| Manual per-clip horizontal flip | Adopt, CPU and live flow verified | Source: ignored `docs/development/segment-horizontal-flip-continuity-2026-08-19.md`. Mounted Gallery action plus CPU queue worker preserve the source, copy every audio stream and write provenance on a separate final output. Actual MP4/AAC, WebM/Opus and MOV/PCM tests pass. Signed-in stable Cloudflare action produced a new playable-format MP4 with unchanged 608×352 / 24 fps / 5.166667 s geometry and duration, equal encoded audio hash, unchanged source hash and correct mirrored pixels. Legacy sidecarless inputs can run but remain blocked after restart without existing project ownership evidence. September 25 CPU crash-window simulation verifies sealed output adoption without another encode. October 3 real POSIX SIGKILL checks also verify adoption, sidecar-only replay and changed-source rejection with the actual worker/journal and synthetic app wiring; whole-service crash/restart, Windows and human acceptance remain separate gates. |
| Suggested geometric flip | Experiment | Needs fixtures and an opt-in proposal; default automatic flip remains rejected. Later automated execution stays deferred. |
| Facing/screen-direction carry | Adapt, explicit fields and Multi-Shot editor delivered | Same-source H3 seam locks carry an authored `screen direction:` or `facing:` line into the next clip's camera-world instruction. Ordinary prose does not create a direction lock; independent non-temporal boundaries and default flip behavior are unchanged. The October 3 H3 Multi-Shot editor adds optional Screen direction and Subject facing controls backed by these prompt lines. Shared prompts disable later clip controls; model switches reject stale H3 options. Submission, native child dispatch and output restoration preserve multiline clip prompts and empty clip positions. Nine focused UI checks, three backend/parser/planner checks, scoped lint and the production build pass; signed-in browser editing, keyboard disclosure, clearing and shared-prompt restoration were verified without generation. This editor does not add automatic carry across independent clips. GPU direction fidelity and human quality acceptance remain open. |

### Templates, styles, composition and LLMs

| Candidate family | Retained decision | Actual state / missing work |
| --- | --- | --- |
| Official H3 skills, Context-IR, prompt composer, OpenH3-IR | Adapt | `h3_upstream_skills.py` loads bounded data and offers native craft families; `director/workflow_templates.py` supplies advisory structure. Upstream skills/graphs are not executed as tools. |
| Animation principles, storyboard/parkour boards, character reveals, kinetic typography, limited palettes, logos, tapestry, origami/page turns, sprites, ink/smoke, noir/foley, fake speedpaint | Adapt / benchmark leads as recorded per source | Craft/data guidance exists for some families. A named recipe does not prove local reproduction of a social example. Keep exact source/model/seed/style and visual/audio checks for each promoted recipe. |
| Mayz, Kōda, Shams, Dave, Renataro, Naoneko, TechHalla, Andrew, lepadphone, PhotogenicWeekE, airina, ailker and Kashiko examples | Preserve source-specific decisions | All URLs remain in the appendix/original tables; missing post bodies/comments remain evidence gaps. Hosted Seedance/Magnific/Hailuo/Vidu/PixVerse examples are not local executors. |
| MiniMax Design desktop / Lazy Frames / OpenMontage / Director Cut Studio | Adapt ideas; reject wholesale runtime | Grow existing Director/Reference rather than import another project store, queue or agent canvas. |
| Remotion + Blender-MCP composition | Adopt; bounded shared package delivered, broader acceptance open | The typed Blender facade and CPU Editor now share a [queued composition package](../composition-api.md), delivered in `8779322a`: one or two animation segments and up to eight ordered clip instances, with sealed inputs and create-only publication. A retained local technical check reused one rendered segment in two clip instances, producing a 96-frame, four-second video; Editor then produced a fully decoded 72-frame, three-second cut at 24 fps and 1280×720, preserving the source. Director rejected that scene's motion, so its Keep/Editor approval remained unavailable. Earlier Editor duplicate-append, trim, reorder/removal and draft-reopening checks remain separate evidence. Broader package recovery, motion quality, Windows and human acceptance remain open; camera/light/physics extensions need their own contract. |
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
| Coding-benchmark LLM-as-verifier (`source-208`) | Reject as a Maestro module | The August 19 intake rejected this Terminal-Bench sample-and-rank idea as a coding-agent workflow with no Maestro media job. It adds no model, verifier loop or default to Director. |

## Upstream assessment and selected ports

The 2.3 assessment below is pinned to
`5efd686ab446d451d927cbc00665e136d8de585e`. A September 25 fetch found
the 2.4.0 commit `4afe1693e34119ef2d7e710ec8632c22da225055`.
The September 26 fetch reached `d98af351` after the 2.4.1 release commit
`960cf9d` and a Linux DLSS test fix. Their separate dispositions follow.
Neither release is a whole-tree merge target for Continuum.

| Upstream release/cluster | Integration disposition |
| --- | --- |
| 1.9.1 llama binary-release pointer (`1d1610d`) | Adapt semantic release → nightly resolution and asset checks; retain Continuum staged install, SONAME repair, cached reuse and CUDA-build policy. |
| 2.1.3 UTF-8 transport and failed-load recovery (`5b47516`) | Delivered byte-first SSE decoding at both local streaming paths and explicit UTF-8 JSON response decoding in `9d62da5`, retaining Continuum's cancellation, retry, and progress handling. FlashVSR partial-load cleanup is in `5fff121`; failed WGP generation now releases retained model resources after frame unwind in `a32023a`, while the H3 OOM relief retry keeps its resident model. The latter passed 5,306 backend and 715 UI checks on September 24 but awaits the next coordinated service restart and live GPU acceptance. On September 27, one signed-in stable-share Chat round trip through the local Gemma 4 31B Heretic ARA model preserved Japanese/Chinese characters, French accents, Arabic and an emoji exactly. The fresh grant was withdrawn after local model unload, with the service still ready. This supplies one local multilingual transport receipt; other providers, chunk-boundary exhaustiveness and general language quality remain unaccepted. See [the dated receipt](../operations/GPU_ACCEPTANCE.md#2026-09-27-local-multilingual-chat-transport). |
| 2.2.3 H3 RMSNorm (`f614f61`) | Adapt inference chunking with Continuum's existing 8192-token limit; preserve native normalization, hooks, gradients and settings. GPU memory/throughput acceptance remains pending. |
| 2.0.1 Gallery Extend handoff (`5734846`) | Adapted and shipped as `81f43e7`: existing Continuum actions open Studio / Video / Extend and attach the selected clip. Workspace-qualified fetches, stale-request cancellation, visible errors and active/stashed preview ownership are covered by 688 UI tests plus lint/build. Signed-in stable Cloudflare flow from Audio to Extend was exercised with an existing 5.2-second clip; no generation submitted. |
| 2.0 Editor / layered timeline / export | The first Continuum Editor slice opens a project Gallery video into a non-destructive, single-source trim timeline and autosaves its draft with revision/CAS and current-source checks. Its preview is bound to a content hash and current server-owned privacy flag; the Gallery action also covers videos without generation metadata. In the signed-in stable-share UI, a 5.2-second private video opened in Editor, its preview played, and a trim draft survived Gallery exit/reopen. The CPU export follow-on renders one saved source cut as a separate H.264/AAC MP4, retains project privacy and provenance, and queues through normal cancellation and restart recovery. The full backend gate passed 5,352 tests (17 skipped), syntax and JSON grammar checks. The later Queue reconnect fix passed 721 UI tests, type-check/build, targeted lint, and four synthetic desktop/mobile browser cases. A signed-in stable-share request queued a nonzero 3.0-second-start trim; its durable job survived a coordinated restart and required owner reauthentication. On September 25 the original signed-in session resumed job `2477c81b85764c85afb8d7887eb050c3` in `codex-pinkcherry-beta06`; after a fresh validated 20-minute coordinator grant, the queue rendered and published `editor_cut_2477c81b85764c85afb8d7887eb050c3.mp4`. FFprobe found a 2.166667-second 608×352 H.264/AAC MP4. Its sidecar preserves the project, private flag, source name/revision, Editor revision 2, 3.0-second source start, and trim duration. The signed-in stable-share Gallery showed the private output; the queue returned to paused with no active jobs and the grant was withdrawn. A September 25 CPU stress check exposed eight cross-process saves all accepting the same revision; a shared file lock now permits one winner and seven reload conflicts in the regression. The fix passed the complete local CI gate (5,358 backend tests, 17 skipped; JSON grammar; 721 UI tests; production build) and was deployed as `9bf9267`. Two signed-in stable-share tabs then raced changes to the same draft: the 0.5-second trim saved, the competing 0.7-second trim received HTTP 409 with a reload instruction, and a later Gallery reopen showed the saved 0.5-second trim at revision 3. This establishes one live browser concurrency case; playback/listening quality, AI round trips and owner acceptance remain open. Subsequent native Editor milestones support 1–8 ordered clips, titles, timed images and one audio layer with source trim, absolute placement, gain, mute and fades. The selected clip now has a [mixed audio preview](../operations/MAESTRO_EDITOR_MIXED_PREVIEW_2026-10-03.md), verified with focused browser cases and the retained live layered draft; this preview follows browser media clocks and does not establish sample-exact export parity or human listening acceptance. |
| 2.0–2.2 H3 character/story/targeted-window repair | Compare with native Reference and H3 authored/durable contracts. Port improvements by behavior; no replacement of current identity or recovery authority. |
| 2.1 VAE/residency/INT8/NVFP4/VDN/Viggle/audio refinement | Separate runtime/compatibility changes; benchmark each accepted path under a fresh lease. The bounded NVFP4 adaptation in `65866ce` keeps LightX2V as the default, honors an explicit backend selection, and falls back only for recognized unsupported cuBLAS shapes, caching those shapes. OOM and other kernel errors still surface. CPU checks passed, but a live unsupported-shape fallback, image quality, and the other runtime refinements remain unaccepted. Preserve existing manual profiles and working kernels. |
| 2.2–2.3 YuE2 / My Music / training / instrumental / multi-LoRA | The current bridge exposes a project-filtered My Music library, guide-backed instrumental composition, and native multi-LoRA selection through the installed Sound/Vision service. No-LoRA, single-LoRA, combined-LoRA, manually scored instrumental, adapter-led automatic-score, guide-backed instrumental, and trained private-checkpoint takes have live technical receipts. The combined take reached a generation limit. The guide writer now has a signed-in eight-bar-per-voice draft accepted by the installed native parser and a clean no-LoRA score-to-audio take; an earlier draft missed the requested bar count, and the rendered 45-second audio exceeded the later score's 17-second nominal duration. Exact audio-length fidelity remains open: the September 27 retained-artifact check binds the 17.143-second score to 1,125 EOS-ended codec frames and a correctly decoded 44.998667-second WAV, locating this take's mismatch in semantic generation rather than decoder timing. The stock 45-second and separate 70-second takes now complete signed-in stable-share MP3-preview playback without media errors; this is not audible-output or owner-quality acceptance. See [the diagnosis and playback receipt](../operations/GPU_ACCEPTANCE.md#2026-09-27-yue2-playback-and-duration-diagnosis). The dedicated score-first LoRA reached the semantic cap with that supplied score while an identical-score, same-seed no-LoRA control ended cleanly; the UI warns and offers adapter-led score planning or base-model supplied-score rendering. One pair and metadata cannot establish listening quality. The September 24 update below implements project-bound training control with the existing fast-storage AI Toolkit and joint-v9 pair; one 200-step run produced a private checkpoint, and a subsequent signed-in audition generated a 51-second take. Owner listening remains open. |
| 2.3 Qwen Image 2.1 + processor hotfix (`f83a714`, `5efd686`) | Useful model addition with a distinct architecture, 10-reference and RGBA semantics; this baseline has no qwen21 handler. The October 5 metadata review pins [official model revision `d26bb612`](https://huggingface.co/Qwen/Qwen-Image-2.1/tree/d26bb61231c349cf6b7896fa83353113880e1ba3) and the [Qwen Research license at `6627d87c`](https://github.com/QwenLM/Qwen-Image-2.1/blob/6627d87c6433151463ec4b48b8945a24fcf16a35/LICENSE), SHA-256 `8dc973f024ff95966bea25866efa443fd16776dcb1001e681e3d467ea572b28d`; the model and code license bytes match. Its seven official weight files total 33,115,613,408 bytes. The hotfix processor export is pinned at `DeepBeepMeep/Qwen_image_2@55ab7995df218d8f759c6c283ef4c3be66111345`; all ten files match the official processor/config publisher identities. Optimized model/VAE metadata is pinned at `DeepBeepMeep/Qwen_image_2@8a136b04af66b7b20f689032f8d5a80f28add0cc`, with encoder variants separately at `DeepBeepMeep/Ideogram4@a531af16b9cb570479356dae9e3f4a721adc3b7f`. Conversion lineage remains a publisher claim; no weights were downloaded or independently compared. Keep the model dormant until the owner reviews its non-commercial research/evaluation host-use terms; subsequent managed installation must bind exact selected artifact digests before catalog/runtime/output and device acceptance. |
| PWA / Web Push / Tailscale / notification history | Optional architecture work; preserve stable Cloudflare and current access/privacy. No silent remote-service activation. |
| 30-second H3 single pass | Experimental capability requiring separate geometry/memory/recovery acceptance; existing long-form segmented generation stays. |
| Upstream Classic UI removal / generic fallback / content classification | Do not port behavior that removes existing features, obscures a chosen runtime or violates local content neutrality. |

### 2026-09-25 upstream 2.4.0 delta

The single new upstream commit changes 173 paths and adds a Gallery viewer,
H3 planning/repair controls, a separate Singularity model, Qwen Image 2.1
improvements, and reliability fixes. Its [release notes](https://github.com/Blizaine/Maestro/blob/4afe1693e34119ef2d7e710ec8632c22da225055/docs/RELEASE_NOTES_V2.4.0.md)
are the source for the following scoped decisions. This is source assessment,
not installed or live behavior in Continuum.

| 2.4.0 cluster | Continuum disposition and next evidence |
| --- | --- |
| Gallery viewer, image comparison, mobile controls and media-to-input menus | **Viewer, same-project image comparison and Image/Video-start destinations adapted.** The full-screen viewer stays within the current project/output list, keeps private media URLs gated until reveal, supports keyboard and touch navigation, restores focus, and retries failed or zero-dimension image loads. Comparison selects two Gallery images, swaps them, and offers a pointer/keyboard split while gating both private previews independently. Synthetic desktop/mobile Firefox and Chromium checks cover navigation, privacy, disappearing selections, load errors, comparison, accessibility and zoom. In the signed-in stable-share UI, four real project images appeared in Gallery; two opened in comparison, the split moved to 100% by keyboard, and Swap exchanged their roles. The earlier signed-in viewer also opened a real video and image, and private video remained URL-gated. Gallery images now offer explicit Studio Image and Video-start destinations alongside the existing one-click action, with visible fetch errors and stale-request cancellation. Desktop/mobile synthetic handoff checks pass. After the coordinated release, the signed-in stable-share UI took one real project image into Image mode as Ref 1 and another into Video Frames as Frame Start; no generation was submitted. The in-app browser crashed on Play in both the new viewer and the pre-existing inline Gallery player, so playback/sound and long-form browser acceptance remain open. Other media-to-input destinations and owner acceptance remain open. Do not import upstream cross-folder assumptions. |
| H3 source fidelity, window repair controls and music/performance timing | **Adapt in bounded slices** against Continuum's authored plans and restart/cancellation rules. The long-video review now shows content-free source/event ordinals per planned segment, with continuation markers, from the sealed shot plan. Its copy distinguishes planned coverage from rendered fidelity; the map persists through queue recovery without exposing authored prompt text. The full CPU backend gate passed 5,352 tests with 17 skips and the new duration-replay regression passed separately; 721 UI tests and 116 synthetic desktop/mobile browser checks passed. This does not provide a source-fidelity score or a repair button: those still need a current sealed-plan checker, targeted revision and same-job recovery, followed by controlled multi-window and GPU quality comparisons. Keep fidelity warnings and source evidence; treat the optional continue-on-warning setting as a separate default-off decision, not a bypass for invalid or failed generations. |
| Director performance/dialogue and supplied-audio handling | **LTX-2.5 source-audio prompt slice adapted; broader role and timing work remains.** The music-video and rerun prompt now binds lip movement only to a shot's explicitly assigned, audible vocalist, keeps instrumental gaps closed, preserves other actions and does not summon a singer into an empty shot. The source track remains the sound source. CPU request-path tests cover rerun and two-shot prompts, including an empty stage; the full backend gate passed 5,352 tests with 17 skipped, syntax, and JSON grammar. Live LTX-2.5 media and owner quality are unverified. Continue adapting exact speaker/role and transcript timing without inventing dialogue, duplicating supplied audio, or adding a subject-matter gate. |
| Qwen Image 2.1 LoRA, memory and Director support | **Defer activation with the 2.3 base model** until its pinned research-license host review and exact artifact revisions are settled. Preserve the distinct Qwen 20B model and LoRA library. Once authorized, take the fused-LoRA, memory and Director fixes with CPU and device checks. |
| H3 Singularity v1.3 Pruned INT8 References | **Benchmark lead**, not a default or replacement for existing H3/Ref2VA choices. Verify exact model/adapter hashes, source terms, memory, cancellation, recovery and quality before catalog promotion. |
| Face Refiner cleanup, Recast mask memory, API-key save, bundled DramaBox guides | **Adopt the applicable corrections, defer Face Refiner cleanup with its absent feature.** The API-key field now waits for an authoritative save and masked-value refresh, keeps the entered value plus an inline error on failure, and permits retry without changing provider selection; the UI gate and a failed-save/retry test pass. Recast mask composition now holds scratch arrays to one frame while preserving global overlap checks and earlier-card priority. In the same 1,000-frame CPU fixture, traced scratch above the 9.2 MB output fell from about 49 MB to 39 KB; four focused tests pass. This does not establish live Recast GPU output. The two upstream DramaBox markdown guides are now bundled, load without missing-guide warnings, and are textually identical to Continuum's existing fallbacks. Upstream Face Refiner's retry/atexit cleanup lives in `app/services/face_refiner.py`, a service absent from Continuum; reassess that cleanup when a native temporal face-refinement executor is justified, rather than adding an unused service. |
| Remote LLM vision payloads | **Defer automatic use**. An explicit remote provider and media-sharing privacy decision must precede transmitting project images; local-model and account/project boundaries stay intact. |

### 2026-09-26 upstream 2.4.1 delta

The fetch reached `d98af351` (release `960cf9d` plus a Linux DLSS test fix).
The [upstream release notes](https://github.com/Blizaine/Maestro/blob/960cf9d/docs/RELEASE_NOTES_V2.4.1.md)
and changed source were assessed against Continuum's existing project, model,
queue and recovery boundaries. None of these clusters is accepted merely by
fetching upstream.

| 2.4.1 cluster | Continuum disposition and next evidence |
| --- | --- |
| Qwen Image 2.1 control, inpaint/outpaint, native 2K, Turbo adapters, cache and text-to-image defaults | **Adapt after the base-model gate.** The local baseline has no accepted Qwen 2.1 runtime; its pinned source and non-commercial host-use review still precede download or activation. Preserve saved settings and current image paths. Port the unset-inpaint text-to-image fix when the handler exists, then verify masks, source geometry, 2K memory and Turbo results under separate CPU/device checks. |
| H3 INT8 video VAE auto-selection and Comfy Kitchen kernels | **Benchmark lead.** Keep explicit VAE and attention choices authoritative. Compare the exact asset and kernel pins against the installed RTX 50 runtime, then test quality, memory, recovery and fallback under a fresh coordinator grant before changing any default. Published upstream speed figures are not local measurements. |
| Experimental Windows 10 DLSS backend | **Defer host deployment.** This is a Linux installation; keep existing finishing and Windows 11 behavior. Assess the separate installer and Windows-only device acceptance on a compatible host, without enabling it through ordinary updates. The following Linux DLSS test-only commit is not a runtime feature to import. |
| Gallery chronology, Media Info, finishing history and upload deletion | **Adapt in bounded slices.** Compare folder-qualified ordering and overlapping refresh handling with Continuum's project Gallery; add useful measured media facts and truthful finishing provenance where absent. Deletion must preserve active-job input and project authorization checks. Do not trade away existing private-preview gating, Editor/Bridge actions or recoverable output flows for upstream parity. |

The September 27 chronology slice makes equal-mtime Gallery outputs sort by
name after newest-first modification time, including the multi-clip filter.
The endpoint regression reverses filesystem enumeration and checks paginated
results. The full local gate passed 5,464 backend tests (17 skipped), the UI
suite, type-check and build. After the coordinated Pinokio restart, direct,
host-local LAN, Quick Tunnel and stable-share `/health`, `/ready`, account
context and workspaces returned 200. Chrome reloaded to owner sign-in. This
accepts deployment and unauthenticated browser hydration; signed-in Gallery
ordering and owner acceptance remain separate work.

The September 27 Media Info slice adds the selected Gallery item's listed file
size, image natural dimensions, and browser-decoded video dimensions and
duration to the viewer after its private preview is revealed. Unsupported
video shows only the listed size; it does not claim a successful media probe.
Measurements are tied to the selected file revision and disappear on selection
change. Focused image/video, private-reveal, and selection checks pass; the UI
gate passed 750 tests, type-check and build, and the publication/syntax guard
passed. This is UI measurement, not sealed-file metadata or a live playback
claim. Commit `7a1250c` was pushed and deployed through Pinokio Restart
Maestro. The direct, host-local LAN, Quick Tunnel and stable-share access
surfaces each returned 200 for `/health`, `/ready`, account context and
workspaces; Pinokio reported the exact restart notice cleared. Chrome loaded
the stable-share owner sign-in page. Signed-in Media Info and owner acceptance
remain separate work.

The October 3 [signed-in local Gallery check](../operations/MAESTRO_GALLERY_MEDIA_INFO_LIVE_2026-10-03.md)
verified H.264 dimensions/duration, unsupported HEVC's size-only display,
private reveal and image/video selection changes against retained file facts.
All four media files and their sidecars were unchanged. Signed-in remote
viewing, live stale-file replacement and human acceptance remain separate.

The viewer's project-output image, video, comparison, thumbnail and download
URLs now carry the file revision from the Gallery listing. If that output is
ordinarily replaced in place, a separate listing-revision check refuses the
stale preview instead of showing new media beside old listing facts. This
mtime/size token is not a cryptographic content identity; the stronger
`content_revision` share contract stays separate. The session-owned Uploads
view keeps its unversioned URL. Focused viewer checks, all 750 UI tests,
type-check/build and the guard gate pass.
Signed-in viewing of a replaced file remains unverified.

Commit `81fdede` passed the combined local gate (5,473 backend tests, 17
skipped; 750 UI tests; TypeScript/Vite build and guard), was pushed, and was
deployed through Pinokio Restart Maestro on 2026-09-28 UTC. Pinokio reported
only `start.js` running at the newly discovered port, with no last error and
the exact restart notice cleared. Direct, LAN, Quick Tunnel and stable-share
access each returned 200 for `/health`, `/ready`, account context and
workspaces. Chrome loaded the stable-share owner sign-in page. This is service
and unauthenticated browser evidence, not a signed-in stale-preview check.

The first finishing-history slice shows one recorded Tools upscale/media-flow
event in a collapsed Gallery card section, using the authorized sidecar's
successful tool field, recognized method, source basename, recorded time and
job time where each exists. Private cards hide the section until reveal.
Embedded metadata and generation requests without a completed tool record
cannot produce this section. Focused private-reveal and missing-record checks,
all 750 UI tests, the TypeScript/Vite build and the repository guard gate pass.
Measured before/after geometry and frame rate, post-generation finishing
success records, a full processing chain, upload deletion, signed-in viewing
and owner acceptance remain separate work.

Commit `a640f99` was pushed and deployed through Pinokio Restart Maestro on
2026-09-27. Pinokio reported only `start.js` running, ready at its newly
discovered local port, with no last error, and the exact public restart notice
cleared. The direct, LAN, Quick Tunnel and stable-share access surfaces each
returned 200 for `/health`, `/ready`, account context and workspaces. Chrome
loaded the stable-share owner sign-in page after restart. This is service and
unauthenticated browser evidence; the finishing section itself has not been
accepted in a signed-in browser or with a real processed output.

The next bounded finishing slice records measured source/output video headers
for a newly completed Tools Upscale job. The publisher probes its own staged
output and the exact authorized source, revalidates that source, and checks the
staged bytes again with cancellation-aware hashing. It then includes numeric
geometry, frame rate, duration and audio facts in the existing atomic,
privacy-stamped sidecar only when both probes succeed. A failed optional probe
leaves the completed video publishable without a measured comparison. The
Gallery card shows these recorded facts under the same private-reveal gate.
This does not reconstruct legacy measurements, prove requested generation
finishing succeeded, or add a local media-flow worker. Post-generation
finishing records, full processing chains, signed-in viewing and owner
acceptance remain open.
The full backend gate passed 5,472 tests with 17 skips. Its first UI pass found
two test harness format-module mocks missing the existing duration formatter;
after those mocks were updated, all 750 UI tests, TypeScript/Vite build and the
repository guard gate passed. An independent review's output-size, staged-byte
identity and cancellation findings were resolved with focused regressions.

Commit `f530359` was pushed and deployed through Pinokio Restart Maestro on
2026-09-27. Pinokio reported only `start.js` running, ready at the newly
discovered local port, with no last error and the exact restart notice cleared.
The direct, LAN, Quick Tunnel and stable-share access surfaces each returned
200 for `/health`, `/ready`, account context and workspaces. Chrome loaded the
stable-share owner sign-in page. This verifies service reachability and
unauthenticated UI hydration; a signed-in Gallery comparison and real Tools
Upscale output remain unverified.

The September 27 generation finishing slice adds versioned, per-output
sidecar records only after a post-generation pass returns or reports a
definite no-op. It records upscale, film grain, voice replacement and audio
level smoothing as applied, not applied, or outcome unconfirmed. H3's separate
transaction records applied upscale and exact delivery fit only after both
passes complete and final publication succeeds; accepting protected native
media does not claim either pass. Gallery shows the recorded steps in its
collapsed finishing section after private reveal, validates the closed step
and outcome vocabulary, and ignores request settings or embedded metadata as
evidence. This is a new-output record, not a historical backfill or a complete
processing-chain ledger. At that checkpoint, optional passes after H3 delivery
publication were not part of its transactional record. The October 5 finishing
repair moves requested grain and voice replacement into private work before
publication and final hashes. Durable recovery binds effective options and
ordered authorized voice-reference identities; replay starts from verified
native bytes. Completed sealed delivery adopts its finished bytes without
requiring already-consumed references. Per-output outcomes survive publication,
and strict transaction-owned voice remux cannot create a Gallery sibling.

The October 6 single-source processing-chain slice preserves sanitized recorded
finishing outcomes from the exact Gallery sidecar sealed in the request
manifest. Successful Tools Upscale and Revoice append their observed step;
Flip and browser-compatible copies carry the existing records forward. Repeated
steps remain ordered. Publication and crash adoption seal the resulting sidecar
with the output, so recovery does not reconstruct history from current inputs.
Gallery shows the most recent 32 records after private reveal and explicitly
counts earlier omitted records. Request settings, embedded metadata, upload
metadata and private reference paths cannot supply historical outcomes. Missing
history remains missing. This preserves recorded evidence within the existing
Gallery trust boundary; hashes do not independently authenticate its historical
provenance. Multi-source Editor branch histories, unrecorded legacy operations,
Windows behavior and owner acceptance remain open. The full affected processing
modules pass 61 checks; the Gallery module passes 14 checks, with TypeScript,
scoped lint and a production build also passing. These establish source and
CPU behavior; runtime activation and owner acceptance remain separate gates.

Legacy ordinary remux retains its existing behavior. Grain and voice jobs keep
the manual-recoverable native-move route. At that finishing checkpoint, manual
copy-on-write recovery still needed a separate publication-intent implementation;
the subsequent bounded recovery update is recorded below. CPU regression evidence
does not establish native GPU finishing, Windows behavior, signed-in Gallery
viewing or owner acceptance.

A real CPU grain check applied the finishing pass, preserved protected source
bytes and decoded all video frames, but exposed a 19 ms shorter AAC tail after
the legacy raw-AAC round trip. Grain now copies every original audio stream
directly from its container. The existing transaction suite's native FFmpeg
regression preserves both AAC tracks' packet payloads, timestamps, skip metadata
and decoded PCM exactly; all seven tests pass, including late-remux cancellation
and failure cleanup. Unsupported stream-copy/container combinations preserve
the original instead of silently transcoding or dropping audio. This proves
the tested MP4/H.264/AAC mux path; it does not extend the native GPU or Windows
acceptance claims above.

The October 5 YuE2 controls repair fences submission and score-continuation
responses across project switches, including A → B → A, and unmount. Obsolete
success, failure and refresh responses cannot overwrite a current review or
clear a newer operation's busy indicator. Accepted backend requests are neither
cancelled nor resent. Deferred component-handler tests reproduce the original
race and verify current-operation completion; production compilation passes.
A private headless browser fixture with real React/ReactDOM in StrictMode also
passes obsolete-response and current-completion checks, with no external
requests. This fixture does not establish live Maestro generation or listening.

The October 5 Gallery upload-removal implementation adds a confirmed Delete
action in Uploads and a session-authorized upload endpoint. It preserves files
used by active readers, recoverable jobs, or Director audio rejoining; Chat
images retain their separate removal flow. Media and access metadata are
staged together with rollback on failure. Temporary staging is not a
user-restorable trash. Automated checks cover session isolation, reader drain,
recovery references, rollback, and stale Gallery responses; all 923 UI tests
pass. Independent review is complete. The Pinokio restart activated the source;
local and stable-share health/readiness checks pass with the same compiled UI
asset, and the live Uploads view shows Delete controls. Live permanent removal
and Windows file-release acceptance remain separate gates.

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
   first restart. A later signed-in 17-second FlashVSR2x was interrupted
   in flight, held for owner reauthentication, resumed as the same job, and
   published once; the Gallery result survived a further restart. The UI now
   rediscovers held jobs immediately after project selection. A signed-in
   single-voice Revoice job was also interrupted in flight, held for owner
   reauthentication, resumed under a revalidated grant as the same job, and
   published one changed-audio MP4 that survived a further restart. A later
   running single-voice Revoice job was cancelled during vocal separation;
   it stopped before SeedVC loading, published no result, and left the queue
   idle through a clean restart. See
   [GPU_ACCEPTANCE.md](../operations/GPU_ACCEPTANCE.md). In-call cancellation
   preemption, the result-adoption crash window, other variants,
   speaker/detail quality, and owner acceptance remain open. Gallery Extend
   and manual flip have CPU/UI/live-flow evidence. Flip now also has three
   real POSIX SIGKILL/CPU-media publication-recovery checks: sealed-result
   adoption without re-encoding, sidecar-only replay, and changed-source
   rejection. These use the actual worker and journal with synthetic app
   wiring; whole-service crash/restart, Windows and human acceptance remain
   distinct. See [flip process recovery](../operations/MAESTRO_HFLIP_PROCESS_RECOVERY_2026-10-03.md).
2. Complete Music3 native live generation and recovery acceptance after the
   pinned host-term review and asset download. The hidden Quad CharacterSheet
   job and atomic publication path now have CPU checks; install the pinned
   LoRA only after its applicable host terms, then exercise live generation,
   cancellation, recovery, quality and public capability admission before
   presenting it as a working product. Scene Kit's
   separate kept-reference handoff now has one live two-image receipt.
3. Native Editor now has ordered clips, titles, timed images and one audio
   layer, with export receipts and selected-clip mixed preview. Continue owner
   playback/listening acceptance, broader concurrency/recovery checks and AI
   round trips with scoped queue/recovery and source-safe media handling. Keep Qwen
   Image 2.1 dormant until exact model/processor/license revisions are pinned
   and the owner reviews its noncommercial research host-use terms; then adapt
   its distinct native runtime without auto-enabling it or changing Qwen 20B.
   Native YuE2 generation,
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

The [dated source-to-decision crosswalk](intake-decision-crosswalk-2026-09-25.json)
links every indexed URL and occurrence to its candidate cluster, evidence role,
historical decision, and current reconciliation anchor. It retains shared and
multi-claim sources without inventing separate products or treating a source
listing as an original decision. The crosswalk covers only the enumerated
eleven-note corpus; implementation and acceptance remain governed by the
states and evidence above.

## 2026-09-27 explicit carry-forward watch decisions

These two clusters previously had only adjacent current anchors in the
crosswalk. Their historical decisions in the August 19 intake remain intact;
this records the current disposition explicitly, without a new source search,
installation, benchmark or implementation claim.

| Candidate | Current disposition | Evidence boundary and reopening criterion |
| --- | --- | --- |
| Ref2VA reference-file compressor rumor (`agg-compressor`) | Watch; no managed integration | The retained intake does not establish a pinned compressor node, artifact or license. Reference-size match/max and pruned or quantized DiT weights are different mechanisms and do not verify this claim. Reopen only when a concrete source and license identify the alleged reference compressor, followed by a reproducible compatibility and quality case. See the August 19 intake, section O. |
| Music3 plus LTX local music-video workflow (`music3-ltx-mv`) | Watch the combined workflow; preserve the separate adopted Music3 lane | Adopting the native Music3 path does not establish the proposed combined music-video workflow. Reopen after native Music3 song acceptance and an exact LTX workflow/source are available for a bounded audio-timing and video comparison under the existing Director/project/queue authorities. The community 30-versus-24-fps claim remains a benchmark lead, not a default. See the August 19 intake, section U. |

All 124 candidate clusters now have explicit current disposition anchors. The
228 source IDs and 301 occurrence pointers are unchanged. Historical source-only
or absent decisions remain recorded as gaps; this does not turn watch/defer
decisions, implemented features or technical samples into completed acceptance.


## 2026-10-05 bounded verification update

The current finishing, runtime-binding and YuE2 response-fencing changes passed
the complete backend CI gate: 5,833 tests with 17 existing skips. The complete
UI gate passed 810 tests and the production build; the applicable Editor browser
checks passed 16 cases. A separate native CPU grain run preserved all original
AAC packets, timestamps and decoded audio samples while changing the video.

A fresh-grant private H3 check then restored a checkpoint across two processes
with different Python hash seeds. Complete runtime bindings matched, the clip
extended from 56 to 73 frames, and the retained video/audio latent prefix bytes
remained identical. Both small-canvas outputs passed full CPU decoding; workers
exited and the exact grant was withdrawn. See the
[loaded-code qualification](../operations/MAESTRO_H3_CUMULATIVE_APPEND_2026-10-02.md#loaded-code-serialization-stability-2026-10-05-utc).
These checks do not close full-resolution quality, the retained opening fade,
manual copy-on-write recovery, owner resource-unload acceptance, Music3 consent
and first-song acceptance, YuE2 listening/duration fidelity, or Windows gates.

## 2026-10-05 manual recovery and repeated Editor sources

The subsequent Studio H3 recovery implementation gives each eligible manual
action a separate queued child with its own sealed publication intent. The
failed source job and producer media remain intact. Retry accounting occurs
once after admission and source verification; accepting the retained original
does not use a retry. Completed publication, cancellation, restart adoption,
and stale source metadata are fenced through the durable source/child pair.
The corrected candidate passed 353 tests across the four affected suites,
including actual parent/child materialization and fresh-journal charge/adoption.
Independent review closed the restart metadata finding. The integrated full
backend gate then passed all 5,859 tests with 17 existing skips on the selected
Python 3.11 RTX 50 runtime, with all frozen inputs unchanged. The first full
run exposed one independent legal-access fixture missing the real source
predicate; loading that helper repaired the fixture without changing its
assertions. The corrected file's 16 tests and the complete rerun passed.
Grain, voice replacement, Director and cumulative variants retain their
existing recovery paths and are outside this bounded implementation.

A separate fresh-grant qualification on that runtime restored private H3
continuation across two processes with different Python hash seeds. Runtime
binding payloads matched and both retained latent prefixes remained byte
identical as the clip grew from 56 to 73 frames. Both 320×192 / 24 fps HEVC/AAC
outputs passed full CPU audio/video decoding with unchanged file hashes. Both
workers exited and the exact lease was withdrawn. This establishes the
selected runtime's bounded continuation execution; it does not establish live
manual-recovery queue acceptance, opening-fade quality or human acceptance.

The Editor now represents repeated uses of one authorized immutable source as
independent clips. Each clip keeps its own trim, sequence position and take
state; removing one use retains the shared source while another use remains.
The eight-clip limit counts clip instances. CPU media and real-journal tests
verify ordered ranges, source authority and privacy without changing originals.
All 811 UI tests, the type-check/build and 16 mounted synthetic Editor browser
checks passed on unchanged sources. All 29 integrated append/export route tests
passed. After the coordinated release, the existing authorized project passed
live duplicate append, independent 0.5–1.5 and 2.5–3.5 second trims, reorder,
removal of a third instance and reopening the saved draft. Two distinct clip
IDs retained one source asset. The CPU export contained 48 frames at 24 fps
with AAC audio and a two-second duration; full audio/video decoding and browser
playback completed without error. Original media and sidecar hashes stayed
unchanged. These checks do not establish a shared Editor/Blender composition
package or owner creative/listening acceptance.

The next UI slice adds **Edit this video** after a successful Blender Keep.
It resolves the exact current video through the authorized project Gallery
listing, retaining revision and privacy. Project, account, result, permission
and unmount changes fence delayed responses. All 811 UI tests, type checking,
the production build and four focused full-UI synthetic browser checks passed
across desktop Firefox and mobile Chromium. A separate mounted StrictMode
probe covered permission revocation and account/project transition races.
Native kept-Blender-to-Editor acceptance remains pending; this handoff does
not complete the neutral composition package or its worker/publication gates.

The live Blender panel exposed a missing client in the selected CUDA 13
environment. The existing pinned Pinokio repair installed Blender MCP 1.0.0
and its missing docutils dependency; all 308 pre-existing package versions
remained unchanged. The running backend then reported Blender 5.1.2 ready
without a restart. The panel now identifies this missing-client condition
and directs users to the same repair action. Four focused desktop/mobile
browser checks and all 811 UI tests passed; no native render or model load
was performed during this runtime repair.

Blender frame review now uses the configured local vision-enhancer selection
instead of the global chat selection. Each review pass loads and checks that
same selection under its exact model lease; capability and model provenance
are checked inside the lease. Rendering runs after the lease releases. LAN
origin detection precedes project authorization. All 133 Blender integration,
MCP service and LLM runtime tests passed, including actual extracted-route
execution with mocked leases/renderers for two-pass review, changing settings,
early capability rejection and mid-loop cleanup. These tests establish offline
behavior; native Blender review and kept-video Editor acceptance remain pending.

A leased native Blender attempt loaded the configured vision model and produced
two visual-review responses, then rejected a revision during structured-plan
validation before publishing a video. The raw revision was not retained, so its
exact invalid field is unknown. The model was unloaded and the grant withdrawn.
Director revisions now use the existing detailed scene/animation contracts,
expanded for the local grammar converter, with a complete data-only semantic
legend and replacement-scene requirement. The model also receives the schema
in its prompt. All 135 affected tests passed; the final integration rerun passed
20 tests, and the pinned runtime's official Python grammar converter accepted
the schema without warnings. Actual scene, animation and legend validators
remain controlling and invalid revisions clean their private review frames
without publication. Native candidate, Keep and Editor acceptance remain pending.

A second leased native attempt applied two structured revisions and completed
three visual responses, then stopped without approval. Its model and exact
grant were cleaned up; no full video or Keep-to-Editor acceptance was observed.
Nonapproval now returns bounded Director feedback through a dedicated error
type, so the panel can show the last analysis instead of discarding it. Other
errors retain generic copy. All 21 Blender integration tests, 811 UI tests,
type/lint/build checks and six synthetic desktop/mobile browser checks passed.
Those checks prove feedback visibility and absence of candidate actions on
nonapproval, plus the existing kept-video handoff; they do not prove a native
approved candidate or human creative acceptance.


## October 5 follow-on: H3 PDD CPU executor

The Alibaba PAI interval-head adaptation uses the cached upstream source at
`194cd36a40be631885d846f675d8d8dca1047cc1` and the published 32-interval,
four-interval-block recipe. Eight paired evaluations use video shift 12 and
audio shift 3. The original repaired FP32 output heads are restored after
success, cancellation, setup or inference failure, and model release. The
backbone adapter follows the existing H3 conversion and MMGP lifecycle.

CPU checks exercise independent interval-overlap arithmetic, paired model
steps, both head projections, stale artifact rejection, execution-device
fences, and cleanup. Existing H3 regression checks remain separate from
artifact/device evidence. Native admission is disabled until the exact
FL2VA or Ref2VA adapter roster and digest, matching base checkpoint, loaded
H3 runtime binding and device receipt are qualified. No native PDD sample,
speedup, or quality acceptance is claimed. Ordinary H3, managed Turbo and
LightX2V retain their existing paths.

## October 5 follow-on: Studio reconciliation and YuE2 timing

One local Chrome held-submission check now exercises an actual reload before
the client adopts the server acknowledgement. JavaScript was paused after
the POST while the backend durably accepted the job. After reload, the UI
showed uncertain admission, disabled Retry and offered Check submission.
That manual scoped GET adopted the original held job without another POST.
Cancellation stopped it at step zero with no output; temporary debugger and
network settings were restored. Independent evidence/source review passed.
This is client acknowledgement-adoption loss, not literal packet loss, and
does not qualify in-flight generation, GPU Stop, Windows or account switching.
The separate terminal-held badge repair (`c278df80`) passed 923 UI tests and
shows an idle queue after cancellation in the live UI.

The retained stock and duration-worded YuE2 takes still last 44.999 and
60.279 seconds against the same eight-bar, 112-BPM score's nominal 17.143
seconds. Exact score/token/prefix round trips pass; decoder sample counts
match their respective semantic sequences. CPU spectral-flux analysis finds
recurring 0.533-second attacks throughout the early, middle and late thirds
of both recordings. This weakens a uniform single-score-pass slowdown as
the primary explanation: that would require about 42.67 or 31.85 BPM.
Synthetic controls discriminate all three tested tempos. Attack periodicity
cannot identify melody, count performed bars or resolve metrical ambiguity;
accompaniment could maintain this rhythm while melody timing differs.
Phrase alignment, ending behavior and owner listening remain open. No model
was loaded, accepted media regenerated, or audio trimmed or time-scaled.

## October 6 follow-on: Editor alternate takes

The Editor's manual return path can retain up to eight authorized Gallery
video takes for each clip, including its original. Import preserves the
selected take. An explicit switch restores that take's saved source start
without changing the clip's duration, timeline position, or layer clocks;
an insufficient saved range is rejected. Shared video assets keep independent
trim states per clip, and removing a clip retains assets referenced by another
clip or take. Dedicated revision-checked routes own take membership and source
identity; ordinary trim saves cannot substitute another asset.

Export admission checks every retained source. The render plan and output
lineage include only selected takes and active layers, so unused takes do not
become hidden export inputs. A real CPU FFmpeg regression renders distinct
original and alternate colors and audio tones, then restores the original
trim. It checks the unchanged sequence duration and frame count, selected
sound, and unchanged Gallery source bytes. A separate mixed-frame-rate check
keeps a single-clip draft at its original 60 fps when selecting a 24 fps take,
including title and image layers on exactly the final frame. The joined video
uses exact frame timestamps; its audio timing stays unchanged. Runtime/browser acceptance remains
separate from this CPU evidence. This slice adopts existing results; automatic
AI dispatch and broader round-trip recovery and human acceptance remain open.

The October 6 live Chrome check imported a retained Gallery video as an inactive
take, switched sources, saved a distinct 0.1-second source start, restored each
take’s start, and reopened the saved selection. Switching reset the private
preview reveal. A live CPU export produced a private 1920×1080 MP4 with 246
frames at 24 fps, a 10.25-second duration, and AAC audio. Its first frame matched
the selected original rather than the mirrored take; both source hashes stayed
unchanged. Local and stable-share health/readiness and matching built assets
passed after the coordinated Pinokio restart. Windows/LAN and human quality
acceptance remain separate.

## October 6 follow-on: ordinary Editor export publication recovery

Ordinary Editor exports now seal the original request, project incarnation,
output policy, render settings and exact media/metadata hashes and file identities
before create-only publication. Recovery adopts a complete matching pair without
another encode. A matching metadata-only orphan is retracted before one fresh
encode. Changed sources and foreign replacements preserve their evidence;
cancellation cleanup does not require consumed sources, remains durable while
blocked, and settles exact absence before queue retirement. The Blender
composition recovery branch keeps its separate contract.

Startup holds publication evidence for review before generic reconciliation can
move files. An owner Retry binds completion to the original producing attempt
and the newly admitted attempt in one durable transition; it can adopt only the
verified complete pair. A changed or partial pair after that admission stays
held without another render.

Real POSIX process-kill checks use CPU FFmpeg media and a reopened durable
journal. They exercise both publication boundaries, exact adoption, cancellation,
interrupted cleanup, native Retry and foreign-file races. All 120 affected
checks passed, including cancellation with the real lifecycle lock. These
checks do not establish a live
service interruption, Windows/LAN behavior or human quality acceptance.

## October 6 follow-on: private native PDD qualification

The pinned private candidate completed eight paired video/audio evaluations
with FP32 fused head banks. Actual MMGP insertion matched 254 numeric values
across 50 AdaLN and 52 fused QKV targets. Native output was finite at
320×192 with 124 frames and stereo 32 kHz audio. Pristine runtime and attention
bindings were restored and the native model was released. The bounded guardian
stopped its owned child, drained the unit and withdrew the exact GPU lease;
fresh negative authority confirmation and controller process-absence checks
passed. Independent source/receipt review found no success-path acceptance
blocker. Earlier failures remain retained evidence.

This qualifies this exact small private FL2VA configuration. Reference-video
and reference-audio conditioning, Ref2VA cancellation/error, external
cancellation and authority-loss paths, full-resolution, synchronization,
visual/listening quality and speed remain open. Production PDD admission
remains closed.

A separate fresh native FL2VA run cooperatively cancelled after its first
paired evaluation. It produced no output, restored the pristine runtime and
shared attention state, and released the model. The guardian stopped its exact
child, drained the unit and withdrew the lease; fresh negative authority and
process-absence checks passed. This proves that specific cooperative
cancellation boundary, not external cancellation or authority-loss recovery.

A separate fresh native FL2VA run raised the pinned diagnostic error after its
first paired forward. The specific error was observed, no output was written,
all 254 MMGP insertion values matched, and pristine heads, runtime bindings and
shared attention were restored. Model release, process exit, an empty unit and
exact lease withdrawal passed, including fresh negative authority confirmation.
This qualifies that injected-error boundary; broader failure recovery remains
separate.

A separate fresh native Ref2VA success run used four pinned still-image
references with ABCK conditioning, without reference video or audio. Eight
paired evaluations retained FP32 fused heads and matched all 254 MMGP insertion
values. Independent CPU readback confirmed finite video tensors with 124
frames at 320×192 and nonempty stereo 32 kHz audio. Pristine runtime and
attention restoration, model release, process/unit cleanup and exact lease
withdrawal passed, including fresh negative authority confirmation. Independent
receipt review found no blocker for this exact small private still-reference
qualification. It does not establish the remaining conditioning, recovery,
quality or public-admission gates above.

## October 6 follow-on: processed-tool Retry and Stop recovery

Upscale, Revoice, Flip and browser-compatible copy now seal the original
producing attempt and exact media/metadata file identities before publication.
An owner Retry can adopt a complete verified result without processing again;
Stop can retract the original attempt's files after that Retry. Partial or
foreign replacement pairs remain held for review. Safe rollback and verified
absence permit fresh processing, with the retry status reporting that work
truthfully. The Editor and Blender composition recovery contracts are preserved.

Historical results without file ownership records retain validated adoption
and explicit review evidence. Startup and Retry do not quarantine or delete
unbound replacements. Cancellation preserves the review record, manifest and
staging through journal compaction until the owner removes both canonical
names; exact absence must be committed before retirement or fresh publication.

The affected complete modules passed 369 CPU checks using fresh final crash and
startup runs plus unchanged-module evidence, including real FFmpeg output,
process-kill recovery, native Retry dispatch,
foreign-file replacements and interrupted cleanup. A combined-module Torch
reimport fixture failure also reproduces on the unchanged predecessor; its
evidence is retained separately. These checks do not prove live GPU processing,
Windows/LAN behavior, a live service crash or human quality acceptance.

After the coordinated Pinokio restart, local and stable-share health/readiness
passed and the matching public restart notice was cleared. One live Gallery
CPU Flip produced a private copy that decoded in Chrome, preserved the source
video and metadata hashes and copied the audio bitstream exactly. The queue
returned to idle. This validates ordinary publication on the activated source;
live crash injection and GPU finishing quality remain separate gates.


## 2026-10-06 — ordinary image residency settings repair

A normal FLUX.2 Klein 9B image failed before model loading because the shared
residency reference capture assumed optional custom settings were a dictionary.
Both requested and finalized captures now accept absent settings while keeping
H3 third and additional still-guide references in their original order.

The regression reproduced the original error at both captures. The 28 relevant
wrapper, residency and failed-generation checks passed on the repaired source,
and independent review found no blocker. The publication guard, source syntax
and diff checks also passed. The full suite was not repeated, following the
owner's proportional-test instruction.

Three separate private, default-four-step FLUX jobs completed with one decoded
1024-square image each. The third survived an actual in-flight browser reload
with the same submission UUID, job, settings, privacy and single execution.
The retained runtime access log records one generation POST and one Resume POST.
The failed first attempt remains explicit. Owned model runtimes were stopped
and drained before exact grant withdrawal, and local/stable health and readiness
passed after Pinokio service restoration. No accepted H3 samples were regenerated.

The reload trial exposed a separate defect: later metadata refreshes changed the
sidecar after its recovery checksum was saved. The media still matches its
receipt, but the trial's metadata seal fails validation. Its original image,
sidecar, completed-job receipt and failed verification remain intact.

Ordinary-repeat refreshes now preserve verified metadata bytes when no change
is needed. Observed finishing and deliberate role/access transitions update
the complete original recovery unit, retaining its identity and continuation
data. The update must match the exact validated or written media and sidecar
bytes; unrelated replacements and persistence failures stop publication.
The regression covers repeated no-ops, finishing, private/native transitions,
both role fields, sibling replacements before and during checkpoint, and failed
recovery persistence. All 207 checks in the affected recovery module passed on
the final source, and independent review cleared the corrected race boundaries.
The earlier 28 residency/wrapper checks remain current. Publication, syntax and
diff checks pass. Fresh native verification of the repaired metadata seal is a
separate remaining gate; the prior failed receipt is not retroactively repaired.
The repair is activated through the coordinated Pinokio restart. The new backend
and both local/stable health and readiness passed; the matching restart notice
was cleared by the existing launcher flow.

## 2026-10-06 — Retake source preview-policy inheritance

Retake admission previously dropped an authorized source video's private and
explicit preview flags. Omitted or null choices now inherit those flags;
explicit boolean choices override them. The existing policy validator rejects
malformed flags before video decoding or job admission. The derived job carries
its session and durable policy through native registry preparation.

The endpoint regression reproduced 26 failures before the repair. CPU checks
exercise all four source-flag combinations, explicit overrides, session-owned
uploads, inaccessible/cross-project sources and content neutrality using the
actual admission, policy and resolver functions. The 45-check privacy and
content-neutrality run passed 44 checks and exposed one obsolete source
assertion for upload serving. After updating that assertion to the current
central authorizer, all six affected checks passed; unchanged passing evidence
was reused. Independent review found no consequential issue. Native Retake
generation, resulting media and the Editor AI roundtrip remain separate gates.

Published as `3558c790` and activated through one coordinated Pinokio restart.
Both local and stable-share `/health` and `/ready` answered HTTP 200; the
restart notice was cleared. The held recovery-check image retained its exact
identity, execution count and held state across this restart. It has not been
resumed while another project's GPU lease is active.

## 2026-10-06 — Editor selected-cut Retake review

Editor now saves pending edits before opening **Retake selected cut**, pauses
its preview, and carries the active take's source interval and content revision
into the existing review dialog. Source playback speed is included in that
interval. Opening does not submit a job or change the timeline, layers or take
selection. Later metadata cannot reset the selected interval; account, project
and opening changes invalidate deferred callbacks. Gallery's filename-only
Retake path remains available.

An optional source revision binds Retake admission to the authorized Gallery
video and its metadata. The endpoint checks it before decoding and again under
the shared lineage lock through registration. Replaced bytes, changed metadata
and an upload with the same filename cannot silently become the chosen source.
The nine affected backend checks and all 40 checks in the two affected UI
modules pass; TypeScript, scoped lint and the production build also pass.
These are CPU checks. Native Retake generation and its resulting media, broader
automatic roundtrip recovery, Windows/LAN behavior and human acceptance remain
separate obligations. The existing explicit Gallery-result take import is the
return path for this review-handoff milestone.

Published as `9ae56241` and activated through the coordinated Pinokio restart.
Actual Chrome review opened the saved alternate take with source-revision-bound
media and the exact 0.1–10.336-second selection. Closing the dialog preserved
the saved draft and both takes' media and metadata hashes. The same-inode
server-log observation recorded no Retake, Generate or Resume submission.
Local and stable-share health/readiness passed and the restart notice cleared.
This verifies the review handoff; no new Retake sample was generated.

## 2026-10-06 — Retake fresh-review isolation and input errors

Every new Retake review starts with blank prompt/negative text and the existing
form defaults. A synchronous opening identity guard prevents an old review's
text or controls from appearing or submitting under another account, project
or source before the new state is prepared. Editor source interval/revision,
model/LoRA selection and deferred-response fences remain intact.

Retake admission rejects malformed or nonfinite timing/strength controls before
video decoding or registration. Strength stays within its documented 0–1 range.
Frame conversion clamps to source bounds before multiplication, avoiding
finite-timestamp overflow; numeric-string controls, negative-start clamping and
whole-source end sentinels remain supported. Invalid source timing/geometry and
decoder failures return a plain error without private decoder paths.

The old form reproduced retained private text. All 13 affected dialog checks
and 14 affected backend admission checks pass, with TypeScript, scoped lint and
production build validation. Earlier unchanged source-policy and Editor evidence
is retained. Live fresh-review checks and native generated Retake media remain
separate gates.
