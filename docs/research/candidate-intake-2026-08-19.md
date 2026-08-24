# Candidate intake — 2026-08-19

Status: evidence captured from two mixed public dumps on 2026-08-19
(morning Batch 1 + evening Batch 2). This is not an implementation
claim, catalog listing, or default change.

Source thread: Cursor Maestro continuum (owner dump, “more to come”).
Owner emphasis: Batch 2 is not necessarily for this wave. Intake only.

This note is a dated successor to, not a replacement of:

- `docs/research/H3-ecosystem-watch-2026-08-18.md`
- `docs/development/feature-wave-2026-08-media-models.md`
- `docs/development/feature-wave-2026-08.md`
- `docs/development/minimax-h3-fast-runtime-research.md`

Overlapping clusters refresh those decisions. They are not re-litigated from
zero.

## Current checkpoint

Do not reimplement or silently overwrite:

- Native H3 Ref2VA / FL2VA with official reference counts, joint AV output,
  and source-bound step/profile contracts (Quality/High vs task-specific
  Turbo). No universal few-step default.
- FlashVSR as the faithful spatial-upscale path already in Tools / H3
  delivery. LTX re-render remains a labeled creative tier only
  (`08-18` watch item 3; media-models ReDetail decision).
- Character Sheet as a Reference Studio capability projection
  (`character_sheet` profiles). Flux Klein / Krea paths are planned; Krea
  profiles are legal-blocked until terms clear. Sheets are planning
  artifacts for later H3/LTX use, not a second catalog.
- Local TTS already includes VoxCPM2 (48 kHz clone path), Chatterbox, and
  other named TTS handlers. A faster cloner must stay opt-in and
  per-model.
- Director / Studio already own multi-step creative orchestration. Do not
  import a second agent canvas, skill plaza, or operation manager.
- Local content neutrality: no candidate authorizes Maestro-side prompt or
  output moderation.

## Decision rules

A dump is not a build list. Prefer an existing Maestro surface. Extract
invariants; do not import Comfy graphs, desktop agent runtimes, or
upstream skill packs. Community speed and identity claims are benchmark
leads. Experimental runtimes stay labeled until exact model, quality,
cancellation, and resource gates pass. Windows is a separate acceptance
target.

## Raw dump (as received)

| # | URL | Class |
| --- | --- | --- |
| 1 | https://design.minimax.io | product demo (official MiniMax Design desktop) |
| 2 | https://github.com/Comfy-Org/ComfyUI/pull/15375 | issue/PR (merged Comfy core) |
| 3 | https://huggingface.co/CQdesign/LTX-2.5-CQ-Video-and-Image-Enhancer-LoRAs | LoRA / tune (LTX-2.5) |
| 4 | https://huggingface.co/mvp-lab/MiniMax-H3-RAVEN-Streaming-LoRA | LoRA / tune (H3 streaming preview) |
| 5 | https://civitai.com/models/2719904/fn-mix-anima-turbo?modelVersionId=3228288 | model card (Anima / Cosmos image merge) |
| 6 | https://note.com/aisamanogeboku/n/n05fb3f74ea2e | social / craft article (Vidu Q3 + 12 principles) |
| 7 | https://www.reddit.com/r/StableDiffusion/comments/1vr5nvc/using_h3_as_a_character_reference_sheet_generator/ | social post (fetch 403; title + aliases only) |
| 8 | https://github.com/ysharma3501/LuxTTS | repo (ZipVoice-distill TTS) |
| 9 | https://note.com/sepiablue/n/n76c317fa560f | social / local H3 experiment write-up |
| 10 | https://lazy-frames.cosmicstack.ai | product demo (deterministic spec→MP4) |

No item is dropped. Reddit comments were not readable (HTTP 403); the
cluster is decided from the title plus the independently fetched note.com
write-up that cites the same Reddit meme.

## Per-cluster decisions

### A. Official Design desktop vs Maestro Director

**Sources:** (1) design.minimax.io

**Claims:** macOS/Windows desktop “director’s chair”: brief → decompose →
parallel copy/image/video/audio agents → merge/export. Local-first asset
center. Human checkpoints. Skills plaza (storyboard, MV, e-commerce,
audiobook). One advertised skill is “Character Scene Storyboard” from
references and a script.

**Already covered:** Director, Studio, project assets, H3/LTX generation,
Music/TTS lanes.

**Decision: Reject** as a Maestro module or dependency. **Adapt / extract**
only the product language that Maestro already wants: director-led
checkpoints, skill-like reusable briefs, and “sheet from refs + script”
as an input to the existing Character Sheet / H3 Ref2VA path.

**Why:** Importing their agent runtime, Plaza, or desktop would add a
second operation manager. Official hosted UX is watch material for
prompt/skill patterns, not a merge target.

### B. H3 latent masks / masked extend / inpaint

**Sources:** (2) ComfyUI PR #15375 (merged 2026-08-18, `ff6c8a8`)

**Claims:** Per-token video and audio latent noise masks. Video masks snap
to the 2×2 latent patch grid; audio masks to whole latent frames; both
forced binary (~0.5 threshold). Enables masked extension and
reference-conditioned latent inpaint. Example graphs exist; one issue
thread notes skipping forecasting on masked runs.

**Already covered:** H3 continuation is queued as one cluster
(Multishot + Extender) on the 08-18 watch. Maestro does not ship a
masked H3 inpaint UI.

**Decision: Adapt / extract** into that same continuation cluster. Do not
import the example JSON, MaskVidExperiments nodes, or Comfy’s mask
broadcast quirks.

**Maestro outcome:** If native H3 extend/inpaint is later planned, treat
binary, patch-aligned AV masks as a source-bound constraint (blockier
than pixel masks; audio is frame-quantized). Reviews flagged
batch-mask collapse in an intermediate patch; any native port must keep
per-sample masks.

**Recheck:** next Comfy H3 template that documents official mask nodes.

### C. LTX-2.5 generative enhance (refresh)

**Sources:** (3) CQdesign LTX-2.5 CQ Video/Image Enhancer LoRAs

**Claims:** Two LoRAs (image + video) to improve/restore poor or
low-resolution media. Author states this is **generative enhancement,
not a regular upscaler**. Prompt not required. Card warns to use
`ltx-2.5-video-vae-conv-bf16` or the result over-sharpens.

**Already covered:** ReDetail / LTX-2.5 creative refine is **experiment
only**; do not promote LTX re-render as faithful default. Official LTX
IC-LoRA spatial upscaler is the same class.

**Decision: Experiment** (refresh). Alias of the existing LTX creative
tier, not a new product.

**Maestro outcome:** If listed later, label “creative enhance / may
rewrite identity and markings.” Keep FlashVSR as the faithful path.
License, hash, and VAE pin required before any catalog row.

### D. H3 causal streaming (RAVEN) vs few-step Turbo

**Sources:** (4) mvp-lab MiniMax-H3-RAVEN-Streaming-LoRA

**Claims:** Preview LoRA that turns H3 into a **causal chunked** generator
(RAVEN-style), 4 NFE, 192 frames, 768×1376, 24 fps, `sink=2` / `window=2`.
Authors say the adapter is **undertrained**, texture-limited, **not** a
paper-evaluated model, and the bundled trial is a **one-node 8-GPU
FSDP** validation. Base remains MiniMax-H3 Community License.

**Already covered:** Official / community Turbo is a **different**
contract: few-step *bidirectional* denoise, task-specific, never global.
08-18 watch item 5.

**Decision: Watch.** Do not install. Do not treat as Turbo.

**Recheck:** consumer-single-GPU recipe, trained (not preview) weights,
and a quality comparison against Quality/High and official Turbo on the
same prompt/seed class. Until then it is research-only.

### E. Fn-Mix Anima-Turbo (image checkpoint)

**Sources:** (5) Civitai 2719904 / version 3228288

**Claims:** Anima BASE 1.0 merge (NVIDIA Cosmos lineage), CircleStone Labs
license. Version fetched is **Fn-Base-Anima-V1.0 “Base-no turbo”**
(~3.9 GB bf16): multi-illustrator tag emphasis; turbo LoRA recommended
only to match sample look. Comfy-tested only. Halo/hat leakage without
those tokens. No negative prompt at CFG 1.

**Already covered:** Nothing. Maestro has no Anima/Cosmos image family
(Wan “animate” is unrelated).

**Decision: Reject** as an H3 or video candidate. **Defer** as a possible
future image-family listing only if Anima is adopted as a first-class
engine with license review.

**Why:** Wrong product surface for this dump’s H3/video work; merge
checkpoint plus third-party license; not a transferable H3 invariant.

### F. Twelve animation principles as prompt craft

**Sources:** (6) note.com / aisamanogeboku (2026-02-04, Vidu Q3)

**Claims:** Disney’s twelve principles (timing, slow-in/out, arcs,
exaggeration, pose-to-pose, solid drawing, appeal, staging, anticipation,
squash/stretch, follow-through, secondary action) improve AI motion if
written into prompts. Paid tail not used. Examples are Vidu Q3, not H3.

**Already covered:** H3 official six-section Ref2VA format; Director
planning copy.

**Decision: Adapt / extract.** Not a model and not Vidu adoption.

**Maestro outcome:** Optional craft notes for Director / H3 prompt helpers
(anticipation, hold/“ma”, follow-through). User-authored; not a scanner
and not a default rewriter.

### G. Character sheets as H3 Ref2VA identity (no char-LoRA)

**Sources:** (7) Reddit title (comments unread); (9) note.com / sepiablue
2026-08-18; media-models Wave M2

**Claims (sepiablue, public):** Three ChatGPT-made sheets (full-body
turnaround, head angles, face-part/expression) bound as distinct
`<Picture N>` roles in H3 **ref2va**, no character LoRA. RTX 4070 12 GB,
576×832, 124 frames, ~225 s with Spectrum + Sage + FirstBlockCache.
Three prompt styles: official over-structured, mid-length natural
English, and ultra-short. Author’s visual pick is the **mid-length**
prompt (identity held, motion less stiff). Over-structure stiffened
motion; under-specify spawned extra tennis balls. Notes i2v still wins
when a true first frame exists. Mentions sheets can also be made with
Flux Klein 9B or short H3 runs.

**Already covered:** Character Sheet workflow (M2) plus native H3 Ref2VA
image refs. Dual-ref tests in the current H3 pack already bind stills as
identity/wardrobe, not setting.

**Decision: Adapt / extract** (refresh of M2 + Ref2VA). **Benchmark lead**
for prompt density only. Not a new node pack.

**Maestro outcome:**

1. Keep building sheets in Reference Studio (Klein default when legal).
2. When feeding H3, assign each sheet panel a **role** (body / head /
   face), not “setting” and not first/last frame unless FL2VA is chosen.
3. Probe mid-length role-binding vs the official six-section wall on the
   same refs — user report nominates the probe; do not change the default
   prompt template from one 4070 write-up.
4. Do not treat “skip character LoRA” as a promise of Museum-length
   identity.

Reddit remains an evidence-intake URL; re-read comments when the page is
reachable.

### H. Fast local voice clone (LuxTTS)

**Sources:** (8) ysharma3501/LuxTTS (Apache-2.0); weights
`YatharthS/LuxTTS`

**Claims:** ZipVoice distill, ~4 steps, custom 48 kHz vocoder, author
claims ~150× realtime on one GPU and &lt;1 GB VRAM, CPU faster-than-realtime.
Zero-shot clone from a reference wav.

**Already covered:** VoxCPM2 is the current local 48 kHz clone path in
Maestro; other TTS families stay named and selectable.

**Decision: Experiment / benchmark lead.** Do not replace VoxCPM.

**Maestro outcome:** Isolated comparison on the same reference wav and
script: quality, clone fidelity, cancellation, peak VRAM, and sample-rate
honesty. Keep engine choice per model. Speed claims are not a default
switch.

### I. Byte-stable spec video (Lazy Frames)

**Sources:** (10) lazy-frames.cosmicstack.ai / cosmicstack-labs/lazy-frames

**Claims:** Typed JSON spec → headless Chrome + ffmpeg MP4. Eight
deterministic scene types (type, stats, browser frame, UI callout,
parallax, atmosphere, video-layer, 3D). SHA-256 byte-stable on one
machine. Local core; plugins declare permissions. Ships an agent
`SKILL.md`. Audio: procedural music, macOS TTS, canned SFX.

**Already covered:** Maestro generates learned video; it is not a
Chrome compositor. Director already plans and renders model outputs.

**Decision: Reject** as a generation backend or skill import. **Defer**
the narrow idea of a deterministic “UI/promo capture” tool if Director
later needs pixel-stable product explainers — that would be a new,
explicitly named utility, not H3.

**Why:** Wrong job (compositor vs generative AV). macOS TTS and agent-skill
bundling are not Maestro defaults. Importing the skill pack would add
another agent contract.

## Batch 2 (2026-08-19 evening)

Second owner dump, all `xcancel.com` status URLs. Owner said the material
is not necessarily for this wave. Nearby same-day note
`docs/development/segment-horizontal-flip-continuity-2026-08-19.md`
is a laterality-repair heuristic, not this dump; do not merge it here.

### Capture and fetch

Owner list after dropping the intra-dump duplicate of
`sep_is_heim/2089369376223043837`: **21 unique** URLs (owner count said
22; this note uses 21 unique + that one alias). All 21 are social posts.

**xcancel.com** is an X/Twitter frontend. Direct WebFetch of every
xcancel URL failed on an antibot “Verifying your browser…” interstitial.
Public facts were recovered from `api.fxtwitter.com/<user>/status/<id>`
for the same status IDs (200 JSON). Hugging Face WebFetch for the
CrossView Warp card timed out; the README was recovered from
`huggingface.co/.../raw/main/README.md`. Reply-only prompt pastes
(Mayz, aiaicreate) were not fetched.

### Raw dump (as received)

| # | URL | Author | Date (UTC) | Media | Claimed technique | Demo-only? | Downloadable artifact? | Batch 1 overlap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 11 | https://xcancel.com/rS_alonewolf/status/2090033091188240661 | rS_alonewolf | 2026-08-19 11:08 | photo (settings) | Comfy `ref_image_size` **match** vs **max**; match downscales refs to output size and faces drift once motion starts | no (settings write-up) | no (Comfy param) | G (identity) |
| 12 | https://xcancel.com/Mayz1169/status/2090071861354995996 | Mayz1169 / Kiki | 2026-08-19 13:42 | video (quotes prior neon clip) | H3 fashion MV: fast cuts, neon graphics, saturated color | yes | no (prompt “below”) | F (craft) |
| 13 | https://xcancel.com/beginnersblog1/status/2090051365322252386 | beginnersblog1 | 2026-08-19 12:20 | video ~53s | ChatGPT situation→story→30s scenario→12–15 frame storyboard→video prompt→**Seedance** | yes | no | A (director canvas); wrong engine |
| 14 | https://xcancel.com/fadeaway2024/status/2089931180170949040 | fadeaway2024 / 迪威 | 2026-08-19 04:23 | video ~15s 2688×1536 | High-sat H3 PV/motion-graphics packaging; author is writing a Skill to publish later | yes | promised Skill not posted | A (Skills plaza) |
| 15 | https://xcancel.com/beginnersblog1/status/2089701404059574320 | beginnersblog1 | 2026-08-18 13:10 | video | Hailuo H3 MV: lock singer/outfit/locations; main sheet + 6 refs; **one job per ref**; music owns timing | mostly | no (prompt/storyboard “below”) | G |
| 16 | https://xcancel.com/tanabe_fragm/status/2089934724697555303 | tanabe_fragm | 2026-08-19 04:37 | video ~15s 1920×1080 | GPT Images storyboard → Claude Code → Suno → H3 MV | yes | no (prompts in thread) | A |
| 17 | https://xcancel.com/ai_hakase_/status/2089986485063688566 | ai_hakase_ | 2026-08-19 08:03 | photo (news card) | Aggregator: 3d→real slider LoRA; Ref2VA “.safetensors 60%/40%”; H3 Sigma Refiner; Music Slider LoRA; OrbitSheets | mixed | some real, some unverified | D, G, Music 3 watch |
| 18 | https://xcancel.com/0x0SojalSec/status/2089349548347105623 | 0x0SojalSec | 2026-08-17 13:52 | video (quotes 3090 clip) | Local RTX 3090 R2V “perfect character swap / consistency” | yes | workflow “in comment” unread | G |
| 19 | https://xcancel.com/nakazakifam/status/2089591129293386152 | nakazakifam | 2026-08-18 05:52 | video (quotes ImaStudio lookbook) | JP T2V fashion lookbook @1080p; JP speech collapses; suggests external TTS + later R2V | yes | no | F; TTS H |
| 20 | https://xcancel.com/CharaspowerAI/status/2089359531495059783 | CharaspowerAI | 2026-08-17 14:31 | video ~15s | Restates official H3 ref counts; Magnific hosted 2K sale | yes | hosted product | checkpoint; A |
| 21 | https://xcancel.com/Mayz1169/status/2089329615290716363 | Mayz1169 / Kiki | 2026-08-17 12:32 | video (quotes H3 vs Seedance) | Character sheet + intro description → polished H3 showcase | yes | prompt “below” | G |
| 22 | https://xcancel.com/ChrisGwinnLA/status/2089560817192825136 | ChrisGwinnLA | 2026-08-18 03:51 | video ~15s 1080×1920 | Close-up clip + original stills/sheets → recreate as **wide coverage**, keep source audio/performances; tagged MiniMax Design | yes | no | A; Qwen-Video-Edit watch |
| 23 | https://xcancel.com/SD_Tutorial/status/2089380306851905745 | SD_Tutorial | 2026-08-17 15:54 | none in JSON | LTX-2.3 IC-LoRA CrossView Warp v2 (novel viewpoint) | no | yes (HF + Comfy node) | C (LTX IC-LoRA class, different job) |
| 24 | https://xcancel.com/techhalla/status/2089459241543221537 | techhalla | 2026-08-17 21:07 | video ~34s | Seedance 2.5 “kills plastic look” on Magnific | yes | hosted | reject hosted; not H3 |
| 25 | https://xcancel.com/tkz_aiart/status/2089552425367847191 | tkz_aiart | 2026-08-18 03:18 | video | Local H3 on 16 GB: storyboard-faithful; 1056×608 claimed limit; times vs 608–1344 | mostly | no | G; D |
| 26 | https://xcancel.com/deadsun_0/status/2089393566745330134 | deadsun_0 | 2026-08-17 16:47 | video ~51s | Music 3 restyle + LTX-2.5 clips; local 3060 @480p; 30 fps “noticeably better” than 24 | yes | no | 08-18 Music 3 / LTX |
| 27 | https://xcancel.com/sep_is_heim/status/2089369376223043837 | sep_is_heim (same as note.com/sepiablue) | 2026-08-17 15:10 | video ~10s | Ref2VA, **no Turbo**, 5s; 0.4 / 0.9 / 1.3 MP compare; Turbo if music degrade is OK | no (A/B) | no | D, G, note.com (9) |
| 28 | https://xcancel.com/aiaicreate/status/2089570098017366185 | aiaicreate | 2026-08-18 04:28 | video ~20s | Structured prompts to lock character/asset shape-color-detail; per-second camera/audio | yes | URL “in replies” unread | F, G |
| 29 | https://xcancel.com/MaxForAI/status/2089678026242154678 | MaxForAI | 2026-08-18 11:37 | none | llm-as-a-verifier: DeepSeek V4-Flash sample-5 + self-rank vs Fable 5 on Terminal-Bench | n/a | coding-agent paper/tool | none (wrong surface) |
| 30 | https://xcancel.com/yachimat_manga/status/2089469482397761958 | yachimat_manga | 2026-08-17 21:48 | video | H3 anime-OP character-intro cuts; keyframes optional; TapNow contest | yes | prompt in replies | F, G |
| 31 | https://xcancel.com/EEJ_OOXO/status/2089624347388633438 | EEJ_OOXO | 2026-08-18 08:04 | none | Sheet IA: one frontal face; 2-panel face\|body; separate prop/mood sheets; Seedance/Kling examples | no (craft) | no | G |

Intra-dump alias (not a second candidate): the same
`sep_is_heim/2089369376223043837` URL appeared twice in the owner paste.

### Per-cluster decisions (Batch 2)

#### J. Reference-image encode size (match vs max)

**Sources:** (11) rS_alonewolf, 2026-08-19.

**Claims:** Face hold failed once motion started because `ref_image_size`
was **match** (shrink refs to the output video size, faster, loses face
detail). Switching to **max** (keep refs higher-res) visibly restored
identity. Photo of the Comfy control is attached.

**Already covered:** Native Ref2VA identity/wardrobe binding. Maestro
source has no `ref_image_size` symbol; encode-resolution policy is not
an exposed user control in this checkout.

**Decision: Adapt / extract.** Highest-signal new invariant in this
batch.

**Maestro outcome:** When identity work is later scheduled, treat
reference encode resolution as a first-class Ref2VA setting (preserve
face pixels vs matching output size). Do not import the Comfy widget.
Community “max is always better” remains a **benchmark lead**, not a
silent default (cost/latency).

#### K. H3 fashion / MV / promo craft (demo)

**Sources:** (12) Mayz fashion MV; (14) fadeaway PV packaging; (19)
nakazakifam JP lookbook; (30) yachimat OP intro cuts.

**Claims:** H3 is strong at saturated graphic MV, beat-sync lookbooks,
and character-intro cuts. fadeaway intends a Skill. nakazakifam: native
JP speech collapsed at the end; 1080p cleaner than generate-low +
upscale (anecdote); external TTS can replace collapsed speech.

**Decision: Adapt / extract** the craft (shot purpose, typography as
its own layer, beat-owned timing). **Watch** the unpublished Skill.
**Reject** importing hosted Magnific/Hailuo Skills.

**Overlap:** Batch 1 F (twelve principles) and A (Design Skills).

#### L. ChatGPT → Seedance storyboard factory

**Sources:** (13) beginnersblog1 2026-08-19.

**Decision: Reject** as a Maestro pipeline. Wrong engine (Seedance +
hosted ChatGPT). Director already plans storyboards.

**Leftover:** “storyboard frames then prompt” is already the Studio
direction; do not add a second canvas.

#### M. One job per reference (H3 MV)

**Sources:** (15) beginnersblog1 Hailuo H3; (21) Mayz sheet→showcase.

**Claims:** Lock singer/outfit/locations; main sheet + six supporting
assets; each ref has one job (character, world, props, performance,
typography, storyboard). Music controls timing. Review weak parts;
rewrite lyrics/music rather than hoping a regen fixes structure.

**Decision: Adapt / extract** — refresh of Batch 1 **G**. Not a new
node pack.

#### N. External agent + Suno + H3

**Sources:** (16) tanabe_fragm.

**Decision: Reject** the Claude Code / Suno / GPT Images chain as a
Maestro dependency (second operation manager; overlaps A). **Adapt /
extract** only “storyboard image as a video-order ref.”

#### O. Aggregator “H3 accessory pack”

**Sources:** (17) ai_hakase_ news-card post. Treat claims separately;
do not adopt the card as one product.

| Claim | Evidence | Decision |
| --- | --- | --- |
| `3d_to_real_detail_slider_H3` | Real card: `siraxe/3d_to_real_detail_slider_H3`. Author: trained as detail slider, became 3D→real; examples FL2VA-as-T2V; turbo-5 shown | **Experiment** / Watch. Opt-in LoRA only; never a default. Turbo-5 in the demo is not a global step policy (Batch 1 D) |
| Ref2VA “compress to `.safetensors`, ~60% size / ~40% faster, 4K refs” | No matching custom-node source found. Closest public artifacts are **pruned/quant DiT weights**, not a ref-file compressor | **Watch** until a pinned node + license exists. Do not chase mystery modules |
| H3 Sigma Refiner | Real repo: `yichengup/ComfyUI-YCNodes-MiniMax-H3`. Extra low-sigma tail steps to reduce motion-edge flicker. Distinct from official `MiniMaxH3SigmaShift` | **Experiment** / extract schedule-tail idea only. Do not import the node. Do not confuse with FaceRefine (08-18 watch) |
| Minimax Music Slider LoRA | Closest real artifact is **Music 3** concept sliders (`ntc-ai/minimax-music3-concept-sliders`), not an H3 AV LoRA | **Watch** on the Music 3 lane. Aggregator label is misleading |
| ComfyUI-OrbitSheets | Real repo: `lumos675/ComfyUI-OrbitSheets`. Hard-cut multi-view H3 sequences + vision frame pick for turnaround/location sheets | **Watch** / Adapt later. Overlaps Character Sheet M2. Prefer hard-cut coverage over continuous orbit if sheets are generated with H3. Do not import the graph or VLM picker |

#### P. “Perfect” local character swap

**Sources:** (18) 0x0SojalSec. 3090 R2V demo; workflow in unread comments.

**Decision: Benchmark lead / Watch.** Community consistency superlatives
are not defaults. Refresh of G.

#### Q. Hosted Magnific / Hailuo promo

**Sources:** (20) CharaspowerAI; (24) techhalla Seedance 2.5.

**Decision: Reject** as a Maestro module. (20) only restates official
ref counts already in the checkpoint. (24) is Seedance + Magnific, not
H3.

#### R. Coverage reframe (close-up → wide)

**Sources:** (22) ChrisGwinnLA.

**Claims:** Feed the close-up clip plus original stills/sheets; ask H3
to rebuild a wider shot while keeping source audio and performances.
Tagged MiniMax Design.

**Already covered:** Video refs + sheets on Ref2VA; Qwen-Video-Edit is
already a separate edit-backend watch. Horizontal-flip continuity is a
different cluster.

**Decision: Adapt / extract.** Use existing Ref2VA roles: video =
performance/timing, sheets = identity, prompt = new camera coverage.
Do not add a second edit product. Official Design remains Reject (A).

#### S. LTX CrossView Warp (novel view)

**Sources:** (23) SD_Tutorial →
`Cseti/LTX2.3-22B_IC-LoRA-CrossView-Warp_v2` (Apache-2.0) +
`cseti007/ComfyUI-CrossViewWarp`.

**Claims:** Video + camera offset (azimuth/elevation/distance, optional
keyframed move) → same scene from a new viewpoint. IC-LoRA reads a
MoGe-2 depth warp (geometry) plus the original clip (identity). v2:
719 Blender scenes, attention + FFN, full-res warp (`downscale 1`).
Prompt is the word `crossview`.

**Already covered:** LTX CQ / IC-LoRA enhance (Batch 1 C) is
**restoration**, not novel view. LTX MSR is multi-subject refine.

**Decision: Experiment** (new LTX job: camera warp). Do not import the
Comfy graph. Label as creative / geometry-conditioned, not faithful
upscale. Recheck: hash, MoGe dependency isolation, identity drift,
Windows.

#### T. Consumer 16 GB local envelope

**Sources:** (25) tkz_aiart (4070 Ti SUPER 16 GB / 32 GB RAM; Windows).
Times quoted: 608×352 ~1m30; 864×480 ~2m27; 1056×608 ~4m08;
1344×768 ~7m32. Author says 1056×608 was the usable ceiling and
storyboard + upscale “works.”

**Decision: Benchmark lead.** Windows + 16 GB is a separate acceptance
target. Do not copy these times into profiles.

#### U. Music 3 + LTX-2.5 local MV

**Sources:** (26) deadsun_0.

**Decision: Watch** — refresh of the 08-18 Music 3 and LTX refine
lanes. 30-vs-24 fps is a **benchmark lead**, not a default. Not H3.

#### V. Resolution vs Turbo (same author as Batch 1 G)

**Sources:** (27) sep_is_heim / Kamimoto = note.com/sepiablue.

**Claims:** Ref2VA, Turbo off, 5 s; compared 0.4 / 0.9 / 1.3 MP. Higher
res looks better and costs time. Use Turbo only if soundtrack
degradation is acceptable.

**Decision: Benchmark lead** — refresh of **D** (Turbo is
task-specific; audio is part of the contract) and **G**. Not a new
product.

#### W. Structured lock-against-reinterpretation prompts

**Sources:** (28) aiaicreate. Reply URL unread.

**Decision: Adapt / extract** — refresh of F/G. User-authored craft;
not a scanner or default rewriter.

#### X. llm-as-a-verifier (coding bench)

**Sources:** (29) MaxForAI quoting jackyk02 / Terminal-Bench 2.1.

**Decision: Reject.** Wrong product surface (coding-agent sample-and-
rank). No Maestro user video job. Do not import a verifier loop into
Director.

#### Y. Character-sheet information architecture

**Sources:** (31) EEJ_OOXO (Seedance/Kling examples; author says the
principle is model-agnostic).

**Claims:** Do **not** stuff many face angles — one clear frontal
close-up; extra faces can contradict. 2-panel sheet: left = face,
right = body front/back (face on the body panel may be erased). Split
**character / prop / background / grade**. Neutral studio lighting on
the character sheet. Accessories/marks that define identity get their
own emphasis.

**Already covered:** Character Sheet M2 + Batch 1 G (three ChatGPT
sheets: turnaround, heads, face parts).

**Decision: Adapt / extract** (refresh of G). Second-highest signal
after J.

**Maestro outcome:** Keep role-split refs. Record a **competing probe**:
G’s multi-angle face pack vs Y’s single frontal face. User reports
nominate the probe; do not change the sheet template from one tweet.
Do not import the author’s Netlify sequence/previz tools.

## Next-wave order (if later asked to implement)

Bird-in-the-hand first. Neither dump jumps the existing trains. Batch 2
is explicitly **not** this wave unless Hailey later picks a slice.

1. Keep the live H3 usable-checkpoint work (identity, relief, continuity).
   Use cluster G / M / Y only as **prompt-role and sheet-layout** probes
   on the existing Ref2VA / Character Sheet surface — no new engine.
2. If identity still drifts after that, schedule cluster **J**
   (reference encode size) on the native Ref2VA path.
3. Character Sheet M2 remains the sheet factory; do not add Anima (E)
   or OrbitSheets (O) to get sheets.
4. Continuation cluster stays Multishot + Extender; fold **mask
   geometry** from B into that design when extend/inpaint is scheduled.
   Coverage reframe (R) stays a Ref2VA role recipe, not Extender.
5. LTX CQ (C) and CrossView Warp (S) stay separately named LTX
   experiments; FlashVSR remains the faithful spatial path.
6. LuxTTS (H) only after a labeled A/B vs VoxCPM; Music 3 (U + slider
   watch) remains the media-models audio priority.
7. RAVEN (D), MiniMax Design (A), hosted Magnific (Q), Seedance
   factories (L), Lazy Frames (I), and llm-as-a-verifier (X) stay
   watch / reject.

One writer per shared file if any of the above is later implemented.
Obsolescence: FlashVSR is not replaced by C or S; VoxCPM is not
replaced by H or collapsed H3 speech; Director is not replaced by A,
I, N, or X; official Turbo is not replaced by RAVEN or the 3d→real
slider demo.

## Watch, not adoption

| Item | Recheck trigger |
| --- | --- |
| RAVEN streaming LoRA | Single-GPU recipe + non-preview weights + quality vs Turbo/Quality |
| MiniMax Design Skills | Public docs for H3 prompt/sheet patterns useful without their runtime |
| Comfy official H3 mask templates | Documented nodes after #15375 |
| LTX CQ / IC-LoRA enhance | License + hash + identity-drift evidence on 5090 |
| LuxTTS | Controlled A/B vs VoxCPM2 |
| Reddit 1vr5nvc comments | Page reachable; user reports nominate probes only |
| Fn-Mix Anima | Only if Anima becomes a Maestro image family |
| Lazy Frames | Only if a deterministic promo-render utility is explicitly wanted |
| H3 `ref_image_size` max vs match (J) | Native encode-size control + identity A/B on the same refs |
| Sheet layout: multi-angle pack vs 2-panel frontal (Y vs G) | Same character, both layouts, Ref2VA role-bind |
| Turbo vs Quality soundtrack (V) | Same prompt/seed class; score picture and music separately |
| siraxe 3d→real slider | License + hash + labeled opt-in; no Turbo-5 default |
| H3 Sigma Refiner tail steps | Isolated schedule experiment; flicker A/B; not FaceRefine |
| OrbitSheets hard-cut coverage | Only if H3-generated sheets are wanted; prefer existing Studio sheets |
| Mystery Ref2VA “60%/40% safetensors” compressor | Pinned node + honest size/speed card; until then ignore |
| Music 3 concept sliders | Music 3 lane only; not an H3 AV LoRA |
| LTX CrossView Warp v2 | Isolated LTX experiment; MoGe + identity-drift + Windows |
| fadeaway PV Skill | Public artifact posted without a second agent runtime |
| Reddit 1vr5nvc / unread X replies | Page reachable; prompts stay user-authored |

## Public evidence index

Canonical after dedup (aliases in parentheses):

- MiniMax Design product: https://design.minimax.io
- Comfy H3 AV latent masks: https://github.com/Comfy-Org/ComfyUI/pull/15375
  (merge `ff6c8a8af144fc9e9e7bc436b1b202f9316848d8`)
- LTX-2.5 CQ enhancer LoRAs: https://huggingface.co/CQdesign/LTX-2.5-CQ-Video-and-Image-Enhancer-LoRAs
  (same class as official LTX IC-LoRA spatial upscaler / ReDetail)
- H3 RAVEN streaming preview: https://huggingface.co/mvp-lab/MiniMax-H3-RAVEN-Streaming-LoRA
  (paper 2605.15190 is background; card says this adapter was not paper-eval’d)
- Fn-Mix Anima: https://civitai.com/models/2719904
  (version 3228288 = Base-no turbo / Fn-Base-Anima-V1.0)
- 12 principles craft: https://note.com/aisamanogeboku/n/n05fb3f74ea2e
- H3-as-sheet-generator social: https://www.reddit.com/r/StableDiffusion/comments/1vr5nvc/using_h3_as_a_character_reference_sheet_generator/
- H3 sheet-as-LoRA-substitute write-up: https://note.com/sepiablue/n/n76c317fa560f
- LuxTTS: https://github.com/ysharma3501/LuxTTS
  (weights https://huggingface.co/YatharthS/LuxTTS)
- Lazy Frames: https://lazy-frames.cosmicstack.ai
  (source https://github.com/cosmicstack-labs/lazy-frames)
- Batch 2 social (canonical xcancel; facts via fxtwitter API):
  - https://xcancel.com/rS_alonewolf/status/2090033091188240661
  - https://xcancel.com/Mayz1169/status/2090071861354995996
  - https://xcancel.com/beginnersblog1/status/2090051365322252386
  - https://xcancel.com/fadeaway2024/status/2089931180170949040
  - https://xcancel.com/beginnersblog1/status/2089701404059574320
  - https://xcancel.com/tanabe_fragm/status/2089934724697555303
  - https://xcancel.com/ai_hakase_/status/2089986485063688566
  - https://xcancel.com/0x0SojalSec/status/2089349548347105623
  - https://xcancel.com/nakazakifam/status/2089591129293386152
  - https://xcancel.com/CharaspowerAI/status/2089359531495059783
  - https://xcancel.com/Mayz1169/status/2089329615290716363
  - https://xcancel.com/ChrisGwinnLA/status/2089560817192825136
  - https://xcancel.com/SD_Tutorial/status/2089380306851905745
  - https://xcancel.com/techhalla/status/2089459241543221537
  - https://xcancel.com/tkz_aiart/status/2089552425367847191
  - https://xcancel.com/deadsun_0/status/2089393566745330134
  - https://xcancel.com/sep_is_heim/status/2089369376223043837
    (alias of Batch 1 sepiablue; URL was duplicated in the paste)
  - https://xcancel.com/aiaicreate/status/2089570098017366185
  - https://xcancel.com/MaxForAI/status/2089678026242154678
  - https://xcancel.com/yachimat_manga/status/2089469482397761958
  - https://xcancel.com/EEJ_OOXO/status/2089624347388633438
- Named artifacts cited by Batch 2 (not new dump URLs):
  - https://huggingface.co/siraxe/3d_to_real_detail_slider_H3
  - https://github.com/yichengup/ComfyUI-YCNodes-MiniMax-H3
  - https://github.com/lumos675/ComfyUI-OrbitSheets
  - https://huggingface.co/ntc-ai/minimax-music3-concept-sliders
  - https://huggingface.co/Cseti/LTX2.3-22B_IC-LoRA-CrossView-Warp_v2
  - https://github.com/cseti007/ComfyUI-CrossViewWarp

## What this note did not do

No install, no weight download, no GPU job, no catalog row, no default
or profile change, no Comfy graph import, no private tab or generation
inspection, no Beads mutation, no Maestro/computer restart. Ready for
more dump items.
